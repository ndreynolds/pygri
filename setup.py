#!/usr/bin/env python

from setuptools import setup, find_packages

readme = open('README.md').read()

setup(name='pygri',
      version='0.1',
      description='Python Git Repository Interface',
      long_description=readme,
      author='Nick Reynolds',
      author_email='ndreynolds@gmail.com',
      url='github.com/ndreynolds/pygri',
      packages=find_packages(),
      include_package_data=True,
      zip_safe=False,
      install_requires=['dulwich'],
      license='MIT'
     )
