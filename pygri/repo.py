"""High-level git repository interaction built on dulwich"""

from __future__ import with_statement
import os
import subprocess # only used for Repo.cmd()
import difflib
import fnmatch

from dulwich.repo import Repo as DulwichRepo
from dulwich.objects import Blob, Commit, Tree
from dulwich.errors import NotTreeError, NotBlobError

class Repo(object):
    """
    An abstraction layer on top of dulwich.repo.Repo for higher-level
    git repository actions like:

    * staging only modified files
    * checking out whole trees (or paths within them) from refs
    * diffs with python's difflib module
    * branching and tagging (both displaying and creating)
    * listing commits down from a ref

    Methods are structured to match git commands when appropriate.

    It also supports executing arbitrary git commands, if git is installed.
    Of course, everything else is implemented in pure python, so having git
    installed is optional.

    Should be considered a work-in-progress.
    """

    def __init__(self, path, gitignore=None):
        """
        Constructs a Repo object.

        :param path: path to the repository.
        :param gitignore: Unix filename patterns to ignore. Can be given
                          as a list of pattern strings, or a path to a file.
                          If neither is supplied, it will look for a 
                          ``.gitignore`` within the repository root directory. 
                          If you'd prefer nothing is ignored, just supply 
                          an empty list.
        """
        self.repo = DulwichRepo(path) # The inner Dulwich Repo object.
        self.root = path
        # Unix filename patterns that will be ignored by certain actions.
        self.ignore_patterns = self._gitignore_setup(gitignore) 
        # List of commit SHAs that hold a stash
        self.stashes = []

    @classmethod
    def init(cls, path, mkdir=False, bare=False, gitignore_path=None):
        """
        Initializes a normal or bare repository. This is mostly a
        handoff to Dulwich.
        
        :param path: the path (which must be a directory) to create
                     the repository within.
        :param mkdir: if True, make a directory at **path**. Equivalent 
                      to ``mkdir [path] && cd [path] && git init``.
        :param bare: if True, create a bare repository at the path.

        :return: a ``Repo`` instance.
        """
        if bare:
            DulwichRepo.init_bare(path)
        else:
            DulwichRepo.init(path, mkdir)
        return cls(path, gitignore_path)

    def add(self, path=None, all=False, add_new_files=True):
        """
        Add files to the repository or staging area if new or modified. 
        Equivalent to the ``git add`` command. 

        :param path: the path to the file to add, relative to the
            repository root. 
        :param all: if True, add all files under the given path. If 
            **path** is omitted, the repository's root path will be used.
        :param add_new_files: if True, this command will also add new
            files. Note this is the default behavior. The option is 
            provided for situations (e.g. ``git commit -a``) where adding
            new files would be undesirable.

        :return: list of filepaths that were added.
                   
        If **path** is a file and **all** is True, only the single 
        file will be added.
        If **path** is a directory and **all** is False, nothing 
        will be added.
        Likewise, if both **path** and **all** are omitted, nothing 
        will be added.        

        Additionally, the ``add`` method checks to see if the path(s)
        have been modified. We don't want to create new blobs if we 
        don't need them.
        """

        # the implementation creates a list of paths and stages them using 
        # dulwich.Repo.stage

        # Paths are a little tricky. To work with repositories independent
        # of the current working directory, we need absolute paths to files.
        # At the same time, git trees are relative to the repository root.
        # So, we have to do a few conversions.

        adds = []

        # get an absolute path for doing isfile/isdir checks.
        if path is not None:
            path = os.path.join(self.root, path)

        # add all files within given path
        if path is not None and all:
            if os.path.isdir(path):
                # walk the directory
                for directory, dirnames, filenames in os.walk(directory):
                    if '.git' in dirnames:
                        # in case path is root, don't traverse the .git subdir 
                        dirnames.remove('.git')
                    for f in filenames:
                        path = os.path.join(directory, f)
                        adds.append(path)
            elif os.path.isfile(path):
                adds.append(path)
        
        # add all files within root path
        elif path is None and all:
            # walk the root directory
            for directory, dirnames, filenames in os.walk(self.root):
                if '.git' in dirnames:
                    # don't traverse the .git subdir 
                    dirnames.remove('.git')
                for f in filenames:
                    path = os.path.join(directory, f)
                    adds.append(path)

        # add file at path
        elif path is not None:
            # add only if file
            if os.path.isfile(path):
                adds.append(path)

        # back to relative paths, so we can add them to the tree.
        rels = []
        for p in adds:
            # get the path relative to repo root.
            rels.append(os.path.relpath(p, self.root))
        adds = rels

        # filter unmodified files (and untracked files if not add_new_files)
        if add_new_files:
            adds = [f for f in adds if self._file_is_modified(f) or \
                    not self._file_in_tree(f)]
        else:
            adds = [f for f in adds if self._file_is_modified(f)]

        # filter gitignore patterns
        adds = self._filter_ignores(self.ignore_patterns)

        # don't waste time with stage if empty list.
        if adds:
            self.repo.stage(adds)

        return adds

    def branch(self, name=None, ref=None):
        """
        Create a new branch or display the current one. Equivalent to 
        `git branch`.
        
        :param name: the name of the branch
        :param ref: a commit reference (branch, tag, or SHA). Same idea 
                    as the git-branch ``--start-point`` option. Will 
                    create the branch off of the commit. Defaults to HEAD.
        :return: None on create, branch name on display.
        
        When the name param is not given, the current branch will be
        returned as a string using the branch's full name
        (i.e. ``refs/heads/[branch_name]``).
        """
        # create a branch
        if name is not None:
            if ref is None:
                ref = self.head().id
            else:
                ref = self._resolve_ref(ref)
            self.repo.refs['refs/heads/%s' % name] = ref
        # display the name of the current branch
        else:
            # couldn't find an easy way to get it out of dulwich, 
            # which resolves HEAD to the commit, so we'll just read 
            # .git/HEAD directly.
            path = os.path.join(self.repo._controldir, 'HEAD')
            if os.path.isfile(path):
                with open(path, 'r') as fp:
                    return fp.read().strip()[5:]

    def checkout(self, ref, path=None):
        """
        Checkout the entire tree (or a subset) of a commit given a branch, 
        tag, or commit SHA.

        This is a fairly naive implementation. It will just write the blob data
        recursively from the tree pointed at by the given reference, 
        overwriting the working tree as necessary. It doesn't do deletions or 
        renames.

        If you wanted to checkout 'HEAD':
          >>> repo.checkout(repo.head())

        If you wanted to checkout the master branch:
          >>> repo.checkout('master')

        If you wanted to checkout v1.2 (i.e. a tag):
          >>> repo.checkout('v1.2')

        :param ref: branch, tag, or commit
        :param path: checkout only file or directory at path, should be
                     relative to the repo's root. 
        :raises KeyError: if bad reference.
        """
        sha = self._resolve_ref(ref)
        obj = self.repo[sha]
        tree = self.repo[obj.tree]

        if tree is None:
            raise KeyError('Bad reference: %s' % ref)
        if path is None:
            path = self.root

        else:
            # check if path and self.root are same
            if not os.path.samefile(path, self.root):
                # if not, we need the path's tree 
                # (a sub-tree of the commit tree)
                tree = self._obj_from_tree(tree, path)
        
        # write the tree
        self._write_tree_to_wt(tree, path)

    def cmd(self, cmd):
        """
        Run a raw git command from the shell and return any output. Unlike 
        other methods (which depend on Dulwich's git reimplementation and 
        not git itself), this is dependent on the git shell command. 

        The given git subcommand and arguments are prefixed with ``git`` and
        run through the subprocess module.

        To maintain the class's indifference to the current working directory,
        we also prepend the ``--git-dir`` and ``--work-tree`` arguments. 

        :param cmd: A list of command-line arguments (anything the subprocess 
                    module will take).
        :return: a string containing the command's output.

        **Usage** (output has been truncated for brevity):
          >>> repo.cmd(['checkout', '-q', 'master'])
          >>> repo.cmd(['commit', '-q', '-a', '-m', 'Initial Commit'])
          >>> repo.cmd(['remote', '-v'])
          "origin  git@ndreynolds.com:hopper.git (fetch)\\n\\n origin ..."
          >>> repo.cmd(['log'])
          "commit 68a116eaee458607a3a9cf852df4f358a02bdb92\\nAuthor: Ni..."

        As you can see, it doesn't do any parsing of the output. It's available
        for times when the other methods don't get the job done.
        """

        if not type(cmd) is list:
            raise TypeError('cmd must be a list')
        git_dir = os.path.join(self.root, '.git')
        prefix = ['git', '--git-dir', git_dir, '--work-tree', self.root]
        # It would be nice to use check_output() here, but it's 2.7+
        return subprocess.Popen(prefix + cmd, 
                                stdout=subprocess.PIPE).communicate()[0]

    def commit(self, all=False, **kwargs):
        """
        Commit the changeset to the repository.  Equivalent to the 
        `git commit` command.

        This method does a commit; use the ``commits`` method to 
        retrieve one or more commits.

        Uses ``dulwich.objects.BaseRepo.do_commit()``, see that for
        params. At minimum, you need to provide **committer** and 
        **message**. Everything else will be defaulted.

        :param all: commit all modified files that are already being tracked.
        :param \*\*kwargs: the commit attributes (e.g. committer, message,
                         etc.). Again, see the underlying dulwich method.
        """
        
        if all:
            # add all changes (to already tracked files)
            self.add(all=True, add_new_files=False)

        # pass the kwargs to dulwich, get the returned commit id.
        commit_id = self.repo.do_commit(**kwargs)

        # return the Commit object (instead of the id, which is less useful).
        return self.repo[commit_id]

    def commits(self, ref=None, n=10):
        """
        Return up to n-commits down from a ref (branch, tag, commit),
        or if no ref given, down from the HEAD.

        If you just want a single commit, it may be cleaner to use the
        ``object`` method.

        :param ref: a branch, tag (not yet), or commit SHA to use 
                          as a start point.
        :param n: the maximum number of commits to return. If fewer 
                  matching commits exist, only they will be returned.

        :return: a list of ``dulwich.objects.Commit`` objects.

        **Usage**:
          >>> repo.commits()
          [<Commit 6f50a9bcd25ddcbf21919040609a9ad3c6354f1c>,
           <Commit 6336f47615da32d520a8d52223b9817ee50ca728>]
          >>> repo.commits()[0] == repo.head()
          True
          >>> repo.commits(n=1)
          [<Commit 6f50a9bcd25ddcbf21919040609a9ad3c6354f1c>]
          >>> repo.commits('6336f47615da32d520a8d52223b9817ee50ca728', n=1)
          [<Commit 6336f47615da32d520a8d52223b9817ee50ca728>]
        """

        if ref is not None:
            start_point = self._resolve_ref(ref)
        else:
            start_point = self.head().id
        return self.repo.revision_history(start_point)[:n]

    def diff(self, a, b=None, path=None):
        """
        Return a diff of commits a and b.

        :param a: a commit identifier.
        :param b: a commit identifier. Defaults to HEAD.
        :param path: a path to a file or directory to diff, relative
                     to the repo root. Defaults to the entire tree.
        """
        if not os.path.isfile(path):
            raise NotImplementedError('Specify a file path for now')
        return self._diff_file(path, a, b)

    def head(self):
        """Return the HEAD commit or raise an error."""
        # It seems best to make this a function so we don't have to
        # set and continually update it.
        try:
            return self.repo['HEAD']
        except KeyError:
            # The HEAD will be missing before the repo is committed to.
            raise NoHeadSet

    def object(self, sha):
        """
        Retrieve an object from the repository.

        :param sha: the 40-byte hex-rep of the object's SHA1 identifier.
        """
        return self.repo[sha]

    def status(self, from_path=None):
        """
        Compare the working directory with HEAD.

        :param from_path: show changes within this path, which must be a
                          file or directory relative to the repo.
        :return: a tuple containing three lists: new, modified, deleted
        """
        # TODO: also compare the index and HEAD, or the index and WT.
        # TODO: Filter out .gitignore

        # use from_path if set, otherwise root.
        if from_path is not None:
            from_path = os.path.join(self.root, from_path)
            if not os.path.exists(from_path):
                raise OSError('from_path does not exist.')
            path = from_path
        else:
            path = self.root

        # store changes in dictionary
        changes = {}
        changes['new'] = []
        changes['modified'] = []
        changes['deleted'] = []
        
        # path is a file
        if os.path.isfile(path):
            status = self._file_status(path)
            if status == FILE_IS_NEW:
                changes['new'].append(path)
            elif status == FILE_IS_MODIFIED:
                changes['modified'].append(path)
            elif status == FILE_IS_DELETED:
                changes['deleted'].append(path)

        # path is a directory
        elif os.path.isdir(path):
            for directory, dirnames, filenames in os.walk(path):
                if '.git' in dirnames:
                    dirnames.remove('.git')
                for f in filenames:
                    fpath = os.path.relpath(os.path.join(directory, f), 
                                            self.root)
                    status = self._file_status(fpath)
                    if status == FILE_IS_NEW:
                        changes['new'].append(fpath)
                    elif status == FILE_IS_MODIFIED:
                        changes['modified'].append(fpath)
                    elif status == FILE_IS_DELETED:
                        changes['deleted'].append(fpath)

        return changes['new'], changes['modified'], changes['deleted']

    def stash(self):
        """
        Stash the changes in a dirty working tree.

        As in Git, this works by creating a commit object that is a child
        of the HEAD commit. 
        """
        # TODO
        raise NotImplementedError

    def stash_apply(self, ref):
        """
        Apply the stash commit to the working tree.
        """
        # TODO
        raise NotImplementedError

    def tag(self, name, ref=None):
        """
        Create a tag.

        :param name: name of the new tag (e.g. 'v1.0' or '1.0.6')
        :param ref: a commit ref to tag, defaults to HEAD.
        """
        # TODO: display tags attached to HEAD when no args.
        if ref is None:
            ref = self.head().id
        ref = self._resolve_ref(ref)
        self.repo.refs['refs/tags/%s' % name] = ref

    def tree(self, sha=None):
        """
        Return the tree with given SHA, or if no SHA given, return the
        HEAD commit's tree. Raise an error if an object matches the SHA, 
        but is not a tree.

        :param sha: tree reference. 
        
        Note that a commit reference would not work. To get a commit's 
        tree, just provide ``c.tree``, which contains the SHA we need.
        """
        if sha is None:
            obj = self.repo[self.head().tree]
        else:
            obj = self.repo[sha]
        if type(obj) is Tree:
            return obj
        else:
            raise NotTreeError('Object is not a Tree')

    def _file_status(self, path, ref=None):
        """
        Checks the status of a file in the working tree relative to a
        commit (usually HEAD). Statuses include: new, modified, and deleted.

        These statuses are conveyed as constants::

        FILE_IS_UNCHANGED = 0
        FILE_IS_NEW       = 1
        FILE_IS_MODIFIED  = 2
        FILE_IS_DELETED   = 3

        :param path: file path relative to the repo
        :param ref: optional ref to compare the WT with, default is HEAD.
        :return: status constant
        :raises KeyError: when the path doesn't exist in either tree.
        """
        full_path = os.path.join(self.root, path)
        in_work_tree = os.path.exists(full_path)
        in_tree = self._file_in_tree(path)

        # new
        if not in_tree and in_work_tree:
            return FILE_IS_NEW
        # deleted
        elif in_tree and not in_work_tree:
            return FILE_IS_DELETED
        # modified
        elif in_tree and in_work_tree and self._file_is_modified(path):
            return FILE_IS_MODIFIED
        # unchanged
        elif in_tree and in_work_tree:
            return FILE_IS_UNCHANGED
        # does not exist (at least in our 2-tree world)
        else:
            raise KeyError('Path not found in either tree.')

    def _file_is_modified(self, path, ref=None):
        """
        Returns True if the current file (in the WT) has been modified from 
        the blob in the commit's tree, False otherwise.

        :param path: path to the file relative to the repository root.
        :param ref: optional ref to compare the WT with, default is HEAD.

        This returns False for new files (not present in the tree). If this
        is unexpected, just call ``_file_in_tree`` first.

        It assumes that the given path does exist. Just expect an OSError
        if it doesn't.
        """
        # handle no head scenario when this gets called before first commit
        try:
            self.head()
        except NoHeadSet:
            return False

        # get the tree
        tree = self.repo[self.head().tree]
        # get the blob from the tree
        blob1 = self._obj_from_tree(tree, path)
        if type(blob1) is not Blob:
            return False

        # make a second blob from the current file
        with open(os.path.join(self.root, path), 'r') as fp:
            blob2 = Blob.from_string(fp.read())
        # are the two blobs equivalent? 
        # if their contents are the same they should be...
        # calls dulwich.objects.ShaFile.__eq__, which just compares SHAs
        return blob1 != blob2

    def _file_in_tree(self, path, ref=None):
        """
        Returns True if the file corresponds to a blob in the HEAD 
        commit's tree, False otherwise.

        :param path: path to the file relative to the repository root.
        :param ref: optional ref to compare the WT with, default is HEAD.
        """
        # handle no head scenario when this gets called before first commit
        try:
            self.head()
        except NoHeadSet:
            return False

        # get the tree
        tree = self.repo[self.head().tree]
        if self._obj_from_tree(tree, path) is not None:
            return True
        return False

    def _obj_from_tree(self, tree, path):
        """
        Walk a tree recursively to retrieve and return a blob or sub-tree 
        from the given path, or return None if one does not exist.

        :param tree: a dulwich.objects.Tree object.
        :param path: path relative to the repository root. 

        :return: Tree object, Blob object, or None if the path could 
                 not be found.
        
        For example, providing ``hopper/git.py`` would return the 
        ``git.py`` blob within the ``hopper`` sub-tree.
        """
        if type(tree) is not Tree:
            raise NotTreeError('Object is not a tree')
        # remove trailing slashes from path (so basename doesn't return '')
        if path[-1] == os.sep:
            path = path[:-1]

        # we need the head of the path, which is either the file itself or a
        # directory.
        head = path.split(os.sep)[0]
        if len(head) > 1:
            # clip head from path for recursion
            new_path = os.sep.join(path.split(os.sep)[1:])

        for entry in tree.iteritems():
            # these are dulwich.objects.TreeEntry objects
            if entry.path == head:
                # get the Tree or Blob.
                obj = self.repo[entry.sha]
                # return if we're at the right path
                if head == path:
                    return obj
                # otherwise recurse if it's a Tree
                elif type(obj) is Tree:
                    return self._obj_from_tree(obj, new_path)

        # if we get here the path wasn't there.
        return None

    def _write_tree_to_wt(self, tree, basepath):
        """
        Walk a tree recursively and write each blob's data to the working 
        tree.

        :param tree: a dulwich.objects.Tree object.
        :param basepath: blob data is written to:
                         ``os.path.join(basepath, blob_path)``.
                         Recursive calls will append the sub-tree
                         name to the original call.
        """
        if type(tree) is not Tree:
            raise NotTreeError('Object is not a tree')
        for entry in tree.iteritems():
            obj = self.repo[entry.sha]
            if type(obj) is Blob:
                path = os.path.join(basepath, entry.path)
                with open(path, 'wb') as fp:
                    fp.write(obj.data)
            elif type(obj) is Tree:
                new_basepath = os.path.join(basepath, entry.path)
                self._write_tree_to_wt(obj, new_basepath)

    def _resolve_ref(self, ref):
        """
        Resolve a reference to a commit SHA.

        :param ref: branch, tag, commit reference.
        :return: a commit SHA.
        :raises KeyError: if ref doesn't point to a commit.
        """
        # order: branch -> tag -> commit
        # (tag and branch can have same name, git assumes branch)

        # dulwich.Repo.refs keys the full name
        # (i.e. 'refs/heads/master') for branches and tags
        branch = _expand_branch_name(ref)
        tag = _expand_tag_name(ref)

        # branch?
        if branch in self.repo.refs:
            # get the commit SHA that the branch points to
            return self.repo[branch].id
        # tag?
        elif tag in self.repo.refs:
            return self.repo[tag].id
        # commit?
        else:
            obj = self.repo[ref]
            if type(obj) is Commit:
                return obj.id
            else:
                raise KeyError('Bad reference: %s' % ref)

    def _diff_file(self, path, a, b=None, html=False):
        """
        Use difflib to compare a file between two commits, or a
        single commit and the working tree.

        :param a: ref to commit a.
        :param b: ref to commit b, defaults to the working tree.
        :param path: path to file, relative to repo root.
        :param html: format using difflib.HtmlDiff.
        :raise NotBlobError: if path wasn't present in both trees.
        """
        # resolve commit
        a = self._resolve_ref(a)
        # get the trees
        tree1 = self.repo[self.repo[a].tree]
        # get the blob
        blob1 = self._obj_from_tree(tree1, path)
        # set data or empty string (meaning no blob at path)
        data1 = blob1.data if type(blob1) is Blob else ''

        if b is None:
            with open(os.path.join(self.root, path), 'r') as fp:
                data2 = fp.read()
        else:
            b = self._resolve_ref(b)
            tree2 = self.repo[self.repo[b].tree]
            blob2 = self._obj_from_tree(tree2, path)
            data2 = blob2.data if type(blob2) is Blob else ''
            # if both blobs were missing => bad path
            if type(blob1) is not Blob and type(blob2) is not Blob:
                raise NotBlobError('Path did not point to a blob in either tree')

        diff = list(difflib.context_diff(data1.splitlines(), data2.splitlines()))
        return diff.join('\n')

    def _filter_ignores(self, paths):
        """
        Match the given paths against our gitignore patterns. Matches
        are filtered out of the return list.

        :param paths: a list of filepaths
        """
        for pat in self.ignore_patterns:
            paths = filter(lambda x: not fnmatch.fnmatch(x, pat), paths)
        return paths

    def _gitignore_setup(self, gi):
        """Interpret the constructor's gitignore parameter."""
        # gitignore given as list of patterns.
        if type(gi) is list and all(lambda x:type(x) is str, gi):
            return gi
        # gitignore given as path to file
        elif type(gi) is str:
            if os.path.isfile(gi):
                return _parse_gitignore(open(gi, 'r'))
            else:
                raise OSError('Given .gitignore path does not exist')
        # not given, let's look for one:
        elif gi is None:
            default = os.path.join(self.root, '.gitignore')
            if os.path.isfile(default):
                return _parse_gitignore(open(default, 'r'))
        # not there (or bad list), return default
        return []


### Constants

FILE_IS_UNCHANGED = 0
FILE_IS_NEW       = 1
FILE_IS_MODIFIED  = 2
FILE_IS_DELETED   = 3


### Utilities

def _expand_branch_name(shorthand):
    """Expand branch name"""
    return _expand_ref('heads', shorthand)


def _expand_tag_name(shorthand):
    """Expand tag name"""
    return _expand_ref('tags', shorthand)


def _expand_ref(ref_type, shorthand):
    """
    Expands and normalizes ref shorthand into a full name.
    For example, inputs:

    ``master``, ``heads/master``, ``refs/heads/master``

    all yield:
        
    ``refs/heads/master``

    :param ref_type: the reference type (e.g. 'tags', 'heads')
    :param shorthand: 
    """
    if shorthand.startswith('refs/'):
        return shorthand
    if shorthand.startswith('%s/' % ref_type):
        return 'refs/%s' % shorthand
    return 'refs/%s/%s' % (ref_type, shorthand)


def _parse_gitignore(f):
    """
    Given a file object, parse the gitignore into a list. 
    """
    return [line.strip() for line in f if not line.startswith('#')]


### Exceptions

class NoHeadSet(Exception):
    """The repository has no HEAD."""


class NothingToCommit(Exception):
    """No changes to the tree."""
