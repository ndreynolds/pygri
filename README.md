pygri
=====
pygri is a high-level **py**thon **g**it **r**epository **i**nterface.

It extends [dulwich](https://github.com/jelmer/dulwich), which is a pure python
git implementation.

What it can do:
---------------

- Stage all modified files.
- Stage all new files
- Make a commit.
- List commits.
- Create/display tags.
- Create/display/checkout branches.
- Resolves refs like `HEAD`, `master`, and `6334f`.
- Checkout path from ref. 
- Run Git shell commands
- Diffs with python's difflib
- Ignore files in a `.gitignore`.

Installation
------------
Download the source code and run the setup file. I may add it to PyPI later.

    git clone git://github.com/ndreynolds/pygi.git
    cd pygi/
    python setup.py install

The only dependency is dulwich which should be installed automatically.

On Debian, compiling dulwich will probably fail if you don't have the python-dev
package. If that's the case:

    sudo apt-get install python-dev

Examples
--------
Some basic examples follow. The docstrings are pretty comprehensive if you need
more information.

Init a new repository:

    repo = Repo.init('my_project', mkdir=True)
    
Access an existing repo:

    repo = Repo('my_project')

Add all files and do an initial commit:

    repo.add(all=True)
    commit = repo.commit(committer="John Doe", message="initial commit")

Grab the HEAD:

    head = repo.commits()[0]
    head = repo.head()

Get a list of commits down from a starting commit:

    commits = repo.commits('head')
    commits = repo.commits('2fd4e1c67a2d28fced849ee1bb76e7391b93eb12')
    commits = repo.commits('master') # resolves to HEAD of 'master' branch
    commits = repo.commits('v1.0') # resolves to HEAD of 'v1.0' tag

Create a branch:

    repo.branch('new-feature')

Do a checkout:

    repo.checkout('master')
    repo.checkout('2fd4e1c67a2d28fced849ee1bb76e7391b93eb12')
    repo.checkout(repo.head().id)
