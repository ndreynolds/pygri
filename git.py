from __future__ import with_statement
import os
import time
import subprocess

from dulwich.repo import Repo as DulwichRepo
from dulwich.objects import Commit, Blob, Tree
from dulwich.errors import CommitError

class Repo(object):
    '''
    High-level Git repository interactions using Dulwich.

    In some places this is purely a wrapper of dulwich.repo, in others
    it uses low-level dulwich methods to achieve high-level ends such as 
    the common git commands (e.g. add, commit, branch, log).

    The underlying dulwich Repo object can always be accessed through
    Repo.repo (as redundant as that is).

    Batteries not included.
    '''

    def __init__(self, path):
        self.repo = DulwichRepo(path) # The inner Dulwich Repo object.
        try:
            self.head = self._get_head()
        except KeyError:
            self.head = None
        try:
            self.tree = self.head.tree
            self.blobs = self.head.tree.blobs
        except AttributeError:
            self.tree = Tree()
            self.blobs = []
        self.root = path
        self.objects = self.repo.object_store

    @classmethod
    def init(cls, path, mkdir=False, bare=False):
        '''Initializes a normal or bare repository.'''
        if bare:
            DulwichRepo.init_bare(path)
        else:
            DulwichRepo.init(path, mkdir)
        return cls(path)

    def add_all(self, directory=None):
        '''
        Mimics the `git add .` command.

        If :directory is supplied, stage all files within that directory 
        (recursively). :directory defaults to the repo root.  
        '''
        if directory is None:
            directory = self.root
        if not os.path.isdir(directory):
            raise OSError('Supplied path is not a directory')
        for directory, dirnames, filenames in os.walk(directory):
            if '.git' in dirnames:
                # don't traverse the .git subdir
                dirnames.remove('.git')
            for f in filenames:
                self._add_to_tree(os.path.join(directory, f))

    def add(self, path):
        '''
        Mimics the `git add <file>` command.

        :param path: the path to the file to add.
        '''
        self._add_to_tree(path)

    def branch(self, name, head=None):
        '''Create a new branch with the given name.'''
        if head is None:
            head = self.head.id
        self.repo.refs['refs/heads/%s' % name] = head

    def checkout(self, identifier):
        '''
        Checkout a branch, commit, or file.
        '''
        pass

    def cmd(self, cmd):
        '''
        Run a raw git command from the shell and return any output. Unlike 
        other methods (which depend on Dulwich's git reimplementation and 
        not git itself), this is dependant on the git shell command.

        The given command and arguments are prefixed with:
            git --git-dir=[/path/to/tracker/.git] --work-tree=[/path/to/tracker]

        :param cmd: A list of command-line arguments (anything the subprocess 
                    module will take).

        Usage:
          >>> Repo.cmd(['checkout', '-q', 'master'])
          >>> Repo.cmd(['commit', '-q', '-a', '-m', 'Initial Commit'])
          >>> Repo.cmd(['remote', '-v'])
          "origin  git@ndreynolds.com:hopper2.git (fetch)\n\n origin ... "
          >>> Repo.cmd(['log'])
          "commit 68a116eaee458607a3a9cf852df4f358a02bdb92\nAuthor: Ni..."

        As you can see, it doesn't do any parsing of the output. It's best 
        used for actions with little or no output (e.g. checkouts, add/rm, 
        remote add/rm, etc.). 
        '''
        if not type(cmd) is list:
            raise TypeError('cmd must be a list')
        git_dir = os.path.join(self.root, '.git')
        prefix = ['git', '--git-dir', git_dir, '--work-tree', self.root]
        # It would be nice to use check_output() here, but it's 2.7+
        return subprocess.Popen(prefix + cmd, stdout=subprocess.PIPE).communicate()[0]

    def commit(self, **kwargs):
        '''
        Mimics the `git commit` command.

        This method does a commit; use commits() to retrieve one 
        or more commits.

        The method will accept any arguments accepted by Dulwich's 
        Repo.commit(). It merges these arguments with some sensible 
        defaults. For example, commit_time will be handled automatically
        unless supplied. 

        Not all arguments are defaultable. At minimum you should 
        provide:

            :param message: the commit message
            :param author: the commit author
        '''
        defaults = {
                'commit_time': int(time.time()),
                'commit_timezone': 0,
                'encoding': 'UTF-8',
                'parents': [],
                'tree': self.tree.id
                }

        # merge defaults with kwargs
        options = dict(defaults.items() + kwargs.items())

        # make sure we have everything we need:
        if not options.has_key('author_time'):
            options['author_time'] = options['commit_time']
        if not options.has_key('author_timezone'):
            options['author_timezone'] = options['commit_timezone']
        if not options.has_key('committer'):
            options['committer'] = options['author']

        commit = Commit()
        # Set the commit attributes from the dictionary
        for key in options.keys():
            setattr(commit, key, options[key])
        
        # Get the ref param if it's there, otherwise HEAD
        ref = 'HEAD'
        if kwargs.has_key('ref'):
            ref = kwargs['ref']

        try:
            old_head = self.repo.refs[ref]
            commit.parents = [old_head]
            self.repo.object_store.add_object(commit)
            ok = self.repo.refs.set_if_equals(ref, old_head, commit.id)
        except KeyError:
            commit.parents = []
            self.repo.object_store.add_object(commit)
            ok = self.repo.refs.add_if_new(ref, commit.id)
            # set the branch.
            self.branch('master', commit.id)
        if not ok:
            raise CommitError("%s changed during commit" % (ref,))

        # set the head attribute
        self.head = commit
        # return the Commit object
        return commit

    def commits(self, identifier=None, n=10):
        '''
        Return one or more commits from an identifier, or if omitted,
        up to n-commits down from the HEAD.

        :param identifer: a branch (not yet) or SHA. Given a SHA, the
                          return value will be a single Commit object.
                          Anything else gets you a list.
        :param n: the maximum number of commits to return. If fewer 
                  matching commits exist, only they will be returned.
        '''

        # eventually this needs to check if the identifier is a branch
        # or tag first, then look for an identifier.
        if identifier is not None:
            return self.repo[identifier]
        if not hasattr(self, 'head'):
            raise MissingHead
        return self.repo.revision_history(self.head.id)[:n]

    def diff(self, a, b):
        '''
        Return a diff of commits a and b.
        '''
        raise NotImplementedError

    def log(self):
        return self.commits()

    def tag(self, sha):
        return self.repo.tag(sha)

    def tree(self, sha):
        return self.repo.tree(sha)

    def _add_to_tree(self, path):
        '''Create a blob from the given file and add the blob to the tree.'''
        if os.path.isfile(path):
            fname = os.path.split(path)[-1]
            with open(path, 'r') as fp:
                blob_string = fp.read()
            blob = Blob.from_string(blob_string)
            self.blobs.append(blob)
            self.tree.add(fname, 0100644, blob.id)

    def _store_objects(self):
        '''Store the objects in the repo's object store.'''
        if self.blobs:
            obj_store = self.repo.object_store
            for blob in self.blobs:
                obj_store.add_object(blob)
            obj_store.add_object(self.tree)
            obj_store.add_object(self.commit)
            return True
        return False

    def _get_head(self):
        '''Get and return the repo's HEAD.'''
        if 'HEAD' in self.repo.refs.keys():
            head = self.repo[self.repo.head()]
            head.tree = self.repo[head.tree]
            return head
        else:
            return None

class MissingHead(Exception):
    '''The repository has no HEAD'''
