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
import itertools
import subprocess
import collections

class Changeset():
    def process(self, state):
        diff = itertools.chain(self.get_diff())
        patch = unidiff.parser.parse_unidiff(diff)

        for f in patch:
            for other in state.touches_file[f.path]:
                state.depends[self].add(other)

            state.touches_file[f.path].append(self)

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

class State:
    def __init__(self):
        # Which patches touch a particular file. A dict of filename => list
        # of patches
        self.touches_file = collections.defaultdict(list)

        # Which patch depends on which other patches? A dict of
        # patch => (list of dependency patches)
        self.depends = collections.defaultdict(set)

def print_depends(state):
    for k, v in state.depends.items():
        print("%s depends on: " % k)
        for p in v:
            print("  %s" % p)

def main():
    state = State()

    patches = GitRev.get_changesets(sys.argv[1:])

    for patch in patches:
        patch.process(state)

    print_depends(state)

if __name__ == "__main__":
    main()

# vim: set sw=4 sts=4 et:
