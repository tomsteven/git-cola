from cola import git
from cola import utils
from cola import core
from cola.observable import Observable

# put subject at the end b/c it can contain
# any number of funky characters
logfmt = 'format:%H%x01%P%x01%d%x01%an%x01%aD%x01%s'
git = git.instance()


class CommitFactory(object):
    _commits = {}

    @classmethod
    def reset(cls):
        cls._commits.clear()

    @classmethod
    def new(cls, sha1=None, log_entry=None):
        if not sha1 and log_entry:
            sha1 = log_entry[:40]
        try:
            commit = cls._commits[sha1]
            if log_entry and not commit.parsed:
                commit.parse(log_entry)
        except KeyError:
            commit = Commit(sha1=sha1,
                            log_entry=log_entry)
            cls._commits[sha1] = commit
        return commit


class DAG(Observable):
    ref_updated = 'ref_updated'
    count_updated = 'count_updated'
    def __init__(self, ref, count):
        Observable.__init__(self)
        self.ref = ref
        self.count = count

    def set_ref(self, ref):
        self.ref = ref
        self.notify_message_observers(self.ref_updated)

    def set_count(self, count):
        self.count = count
        self.notify_message_observers(self.count_updated)


class Commit(object):
    __slots__ = ('sha1',
                 'subject',
                 'parents',
                 'children',
                 'tags',
                 'author',
                 'authdate',
                 'generation',
                 'parsed')
    def __init__(self, sha1=None, log_entry=None):
        self.sha1 = sha1
        self.subject = None
        self.parents = []
        self.children = []
        self.tags = set()
        self.author = None
        self.authdate = None
        self.parsed = False
        self.generation = 0
        if log_entry:
            self.parse(log_entry)

    def parse(self, log_entry):
        self.sha1 = log_entry[:40]
        (parents, tags, author, authdate, subject) = \
                log_entry[41:].split(chr(0x01), 5)

        if subject:
            self.subject = core.decode(subject)

        if parents:
            generation = None
            for parent_sha1 in parents.split(' '):
                parent = CommitFactory.new(sha1=parent_sha1)
                parent.children.append(self)
                if generation is None:
                    generation = parent.generation+1
                self.parents.append(parent)
                generation = max(parent.generation+1, generation)
            self.generation = generation

        if tags:
            for tag in tags[2:-1].split(', '):
                if tag.startswith('tag: '):
                    tag = tag[5:] # tag: refs/
                elif tag.startswith('refs/remotes/'):
                    tag = tag[13:] # refs/remotes/
                elif tag.startswith('refs/heads/'):
                    tag = tag[11:] # refs/heads/
                if tag.endswith('/HEAD'):
                    continue
                self.tags.add(core.decode(tag))
        if author:
            self.author = core.decode(author)
        if authdate:
            self.authdate = authdate

        self.parsed = True
        return self

    def __str__(self):
        return self.sha1

    def __repr__(self):
        return ("{\n"
                "  sha1: " + self.sha1 + "\n"
                "  subject: " + self.subject + "\n"
                "  author: " + self.author + "\n"
                "  authdate: " + self.authdate + "\n"
                "  parents: [" + ', '.join(self.parents) + "]\n"
                "  tags: [" + ', '.join(self.tags) + "]\n"
                "}")


class RepoReader(object):
    def __init__(self, dag, git=git):
        self.dag = dag
        self.git = git
        self._proc = None
        self._objects = {}
        self._cmd = ('git', 'log',
                     '--topo-order',
                     '--reverse',
                     '--pretty='+logfmt)
        self._cached = False
        """Indicates that all data has been read"""
        self._idx = -1
        """Index into the cached commits"""
        self._topo_list = []
        """List of commits objects in topological order"""

    cached = property(lambda self: self._cached)
    """Return True when no commits remain to be read"""

    def reset(self):
        CommitFactory.reset()
        if self._proc:
            self._topo_list = []
            self._proc.kill()
        self._proc = None
        self._cached = False

    def __iter__(self):
        if self._cached:
            return self
        self.reset()
        return self

    def next(self):
        if self._cached:
            try:
                self._idx += 1
                return self._topo_list[self._idx]
            except IndexError:
                self._idx = -1
                raise StopIteration
        if self._proc is None:
            cmd = self._cmd + ('-%d' % self.dag.count, self.dag.ref)
            self._proc = utils.start_command(cmd)
            self._topo_list = []
        log_entry = self._proc.stdout.readline()
        if not log_entry:
            del self._proc
            self._cached = True
            self._proc = None
            raise StopIteration
        sha1 = log_entry[:40]
        try:
            return self._objects[sha1]
        except KeyError:
            c = CommitFactory.new(log_entry=log_entry)
            self._objects[c.sha1] = c
            self._topo_list.append(c)
            return c

    def __getitem__(self, sha1):
        return self._objects[sha1]

    def items(self):
        return self._objects.items()