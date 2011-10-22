from PyQt4 import QtGui
from PyQt4 import QtCore
from PyQt4.QtCore import SIGNAL

import cola
from cola import utils
from cola import qtutils
from cola import signals
from cola.git import git


class BrowseDialog(QtGui.QDialog):

    @staticmethod
    def create(ref, width=420, height=333, parent=None):
        dlg = BrowseDialog(ref, parent=parent)
        dlg.resize(width, height)
        dlg.show()
        dlg.raise_()
        if dlg.exec_() != dlg.Accepted:
            return None
        return dlg

    def __init__(self, ref, parent=None):
        QtGui.QDialog.__init__(self, parent)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setWindowTitle('Browsing %s' % ref)

        # inputs
        self.ref = ref

        # outputs
        self.relpath = None
        self.filename = None

        # widgets
        self.tree = GitTreeWidget(self.ref, parent=self)
        self.cancel = QtGui.QPushButton('Cancel')
        self.save = QtGui.QPushButton('Save')
        self.save.setDefault(True)
        self.save.setEnabled(False)

        # layouts
        self.btnlayt = QtGui.QHBoxLayout()
        self.btnlayt.addStretch()
        self.btnlayt.addWidget(self.cancel)
        self.btnlayt.addWidget(self.save)

        self.layt = QtGui.QVBoxLayout()
        self.layt.setMargin(6)
        self.layt.addWidget(self.tree)
        self.layt.addLayout(self.btnlayt)
        self.setLayout(self.layt)

        # connections
        self.connect(self.cancel, SIGNAL('clicked()'), self.reject)

        self.connect(self.save, SIGNAL('clicked()'), self.save_blob)

        self.connect(self.tree, SIGNAL('path_chosen'), self.path_chosen)

        self.connect(self.tree, SIGNAL('selectionChanged()'),
                     self.selection_changed)

    def path_chosen(self, path):
        """Choose an output filename based on the selected path"""
        self.relpath = path
        self.filename = path
        filename = QtGui.QFileDialog.getSaveFileName(self,
                        self.tr('Save File'), self.filename)
        if not filename:
            return
        self.filename = unicode(filename)
        self.accept()

    def save_blob(self):
        """Save the currently selected file"""
        filenames = self.tree.selected_files()
        if not filenames:
            return
        self.path_chosen(filenames[0])

    def selection_changed(self):
        """Update actions based on the current selection"""
        filenames = self.tree.selected_files()
        self.save.setEnabled(bool(filenames))


class GitTreeWidget(QtGui.QTreeView):
    def __init__(self, ref, parent=None):
        QtGui.QTreeView.__init__(self, parent)
        self.setHeaderHidden(True)
        self.setAlternatingRowColors(True)
        self.setAllColumnsShowFocus(True)
        self.setUniformRowHeights(True)

        model = GitTreeModel(ref, self)
        self.setModel(model)

        self.connect(self, SIGNAL('doubleClicked(const QModelIndex &)'),
                     self.double_clicked)

    def double_clicked(self, index):
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        if item.is_dir:
            return
        self.emit(SIGNAL('path_chosen'), item.path)

    def selected_files(self):
        items = map(self.model().itemFromIndex, self.selectedIndexes())
        return [i.path for i in items if not i.is_dir]

    def selectionChanged(self, old_selection, new_selection):
        QtGui.QTreeView.selectionChanged(self, old_selection, new_selection)
        self.emit(SIGNAL('selectionChanged()'))

    def keyPressEvent(self, event):
        """
        Override keyPressEvent to allow LeftArrow to work on non-directories.

        When LeftArrow is pressed on a file entry or an unexpanded directory,
        then move the current index to the parent directory.

        This simplifies navigation using the keyboard.
        For power-users, we support Vim keybindings ;-P

        """
        # Check whether the item is expanded before calling the base class
        # keyPressEvent otherwise we end up collapsing and changing the
        # current index in one shot, which we don't want to do.
        index = self.currentIndex()
        was_expanded = self.isExpanded(index)
        was_collapsed = not was_expanded

        # Vim keybindings...
        # Rewrite the event before marshalling to QTreeView.event()
        key = event.key()

        # Remap 'H' to 'Left'
        if key == QtCore.Qt.Key_H:
            event = QtGui.QKeyEvent(event.type(),
                                    QtCore.Qt.Key_Left,
                                    event.modifiers())
        # Remap 'J' to 'Down'
        elif key == QtCore.Qt.Key_J:
            event = QtGui.QKeyEvent(event.type(),
                                    QtCore.Qt.Key_Down,
                                    event.modifiers())
        # Remap 'K' to 'Up'
        elif key == QtCore.Qt.Key_K:
            event = QtGui.QKeyEvent(event.type(),
                                    QtCore.Qt.Key_Up,
                                    event.modifiers())
        # Remap 'L' to 'Right'
        elif key == QtCore.Qt.Key_L:
            event = QtGui.QKeyEvent(event.type(),
                                    QtCore.Qt.Key_Right,
                                    event.modifiers())

        # Re-read the event key to take the remappings into account
        key = event.key()

        # Process the keyPressEvent before changing the current index
        # otherwise the event will affect the new index set here
        # instead of the original index.
        result = QtGui.QTreeView.keyPressEvent(self, event)

        # Try to select the first item if the model index is invalid
        if not index.isValid():
            index = self.model().index(0, 0, QtCore.QModelIndex())
            if index.isValid():
                self.setCurrentIndex(index)

        # Automatically select the first entry when expanding a directory
        elif (key == QtCore.Qt.Key_Right and was_collapsed and
                self.isExpanded(index)):
            index = self.moveCursor(self.MoveDown, event.modifiers())
            self.setCurrentIndex(index)

        # Process non-root entries with valid parents only.
        elif key == QtCore.Qt.Key_Left and index.parent().isValid():

            # File entries have rowCount() == 0
            if self.model().itemFromIndex(index).rowCount() == 0:
                self.setCurrentIndex(index.parent())

            # Otherwise, do this for collapsed directories only
            elif was_collapsed:
                self.setCurrentIndex(index.parent())

        return result


class GitTreeModel(QtGui.QStandardItemModel):
    def __init__(self, ref, parent):
        QtGui.QStandardItemModel.__init__(self, parent)
        self.ref = ref
        self._dir_rows = {}
        self._dir_entries = {'': self.invisibleRootItem()}
        self._initialize()

    def _initialize(self):
        """Iterate over git-ls-tree and create GitTreeItems."""
        status, output = git.ls_tree('--full-tree', '-r', '-t', '-z', self.ref,
                                     with_status=True, with_stderr=True)
        if status != 0:
            cola.notifier().broadcast(signals.log_cmd, status, output)
            return

        for line in output.rstrip().split('\0'):
            if not line:
                continue
            # .....6 ...4 ......................................40
            # 040000 tree c127cde9a0c644a3a8fef449a244f47d5272dfa6	relative
            # 100644 blob 139e42bf4acaa4927ec9be1ec55a252b97d3f1e2	relative/path
            objtype = line[7]
            relpath = line[6 + 1 + 4 + 1 + 40 + 1:]
            if objtype == 't':
                parent = self._dir_entries[utils.dirname(relpath)]
                self.add_directory(parent, relpath)
            elif objtype == 'b':
                self.add_file(relpath)

    def _create_row(self, path, is_dir):
        """Return a list of items representing a row."""
        return [GitTreeItem(path, is_dir)]

    def add_file(self, path):
        """Add a file to the model."""
        dirname = utils.dirname(path)
        parent = self._dir_entries[dirname]
        self._add_file(parent, path)

    def _add_file(self, parent, path):
        """Add a file entry to the model."""
        row_items = self._create_row(path, False)
        parent.appendRow(row_items)

    def add_directory(self, parent, path):
        """Add a directory entry to the model."""
        # Create model items
        row_items = self._create_row(path, True)

        # Insert directories before file paths
        try:
            row = self._dir_rows[parent]
        except KeyError:
            row = self._dir_rows[parent] = 0
        parent.insertRow(row, row_items)
        self._dir_rows[parent] += 1
        self._dir_entries[path] = row_items[0]

        return row_items[0]

    def _create_dir_entry(self, dirname, direntries):
        """
        Create a directory entry for the model.

        This ensures that directories are always listed before files.

        """
        entries = dirname.split('/')
        curdir = []
        parent = self.invisibleRootItem()
        curdir_append = curdir.append
        self_add_directory = self.add_directory
        for entry in entries:
            curdir_append(entry)
            path = '/'.join(curdir)
            if path in direntries:
                parent = direntries[path]
            else:
                grandparent = parent
                parent = self_add_directory(grandparent, path)
                direntries[path] = parent
        return parent


class GitTreeItem(QtGui.QStandardItem):
    """
    Represents a cell in a treeview.

    Many GitRepoItems could map to a single repository path,
    but this tree only has a single column.
    Each GitRepoItem manages a different cell in the tree view.

    """
    def __init__(self, path, is_dir):
        QtGui.QStandardItem.__init__(self)
        self.is_dir = is_dir
        self.path = path
        self.setEditable(False)
        self.setDragEnabled(False)
        self.setText(utils.basename(path))
        if is_dir:
            self.setIcon(qtutils.dir_icon())
        else:
            self.setIcon(qtutils.file_icon())