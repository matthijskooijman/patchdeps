#!/usr/bin/env python3

# Copyright (c) 2013 Matthijs Kooijman <matthijs@stdin.nl>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# Simple script to process a list of patch files and identify obvious
# dependencies between them. Inspired by the similar (but more limited)
# perl script published at
# http://blog.mozilla.org/sfink/2012/01/05/patch-queue-dependencies/
#
# This program requires the unidiff library to work, which can be found
# at https://github.com/matiasb/python-unidiff

import os
import sys
import unidiff
import argparse
import itertools
import subprocess
import collections

class Changeset():

    def get_patch_set(self):
        """
        Returns this changeset as a unidiff.PatchSet.
        """
        # parse_unidiff expects an iterable that does not reset itself
        # on every iteration, so we just wrap it in chain.
        diff = itertools.chain(self.get_diff())
        return unidiff.parse_unidiff(diff)

    def get_diff(self):
        """
        Returns the textual unified diff for this changeset as an
        iterable of lines
        """
        raise NotImplementedError


class GitRev(Changeset):
    def __init__(self, rev, msg):
        self.rev = rev
        self.msg = msg

    def get_diff(self):
        diff = subprocess.check_output(['git', 'diff', self.rev + '^', self.rev])
        # Convert to utf8 and just drop any invalid characters (we're
        # not interested in the actual file contents and all diff
        # special characters are valid ascii).
        return str(diff, encoding='utf-8', errors='ignore').split('\n')

    def __str__(self):
        return "%s (%s)" % (self.rev, self.msg)

    @staticmethod
    def get_changesets(args):
        """
        Generate Changeset objects, given arguments for git rev-list.
        """
        output = subprocess.check_output(['git', 'rev-list', '--oneline', '--reverse'] + args)

        if not output:
            sys.stderr.write("No revisions specified?\n")
        else:
            lines = str(output, encoding='ascii').strip().split('\n')

            for line in lines:
                yield GitRev(*line.split(' ', 1))

def print_depends(depends):
    for k, v in depends.items():
        print("%s depends on: " % k)
        for p in v:
            print("  %s" % p)

def analyze_by_file(patches):
    """
    Find dependencies in a list of patches by looking at the files they
    change.

    The algorithm is simple: Just keep a list of files changed, and mark
    two patches as conflicting when they change the same file.
    """
    # Which patches touch a particular file. A dict of filename => list
    # of patches
    touches_file = collections.defaultdict(list)

    # Which patch depends on which other patches? A dict of
    # patch => (list of dependency patches)
    depends = collections.defaultdict(set)

    for patch in patches:
        for f in patch.get_patch_set():
            for other in touches_file[f.path]:
                depends[patch].add(other)

            touches_file[f.path].append(patch)
    return depends

def main():
    parser = argparse.ArgumentParser(description='Analyze patches for dependencies.')
    types = parser.add_argument_group('type').add_mutually_exclusive_group(required=True)
    types.add_argument('--git', dest='changeset_type', action='store_const',
                   const=GitRev, default=None,
                   help='Analyze a list of git revisions (non-option arguments are passed git git rev-list as-is')
    parser.add_argument('arguments', metavar="ARG", nargs='*', help="""
                        Specification of patches to analyze, depending
                        on the type given. When --git is given, this is
                        passed to git rev-list as-is (so use a valid
                        revision range, like HEAD^^..HEAD).""")

    args = parser.parse_args()

    patches = args.changeset_type.get_changesets(args.arguments)

    depends = analyze_by_file(patches)

    print_depends(depends)

if __name__ == "__main__":
    main()

# vim: set sw=4 sts=4 et:
