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

import os
import sys
import argparse
import itertools
import subprocess
import collections

from parser import parse_diff
from parser import LINE_TYPE_ADD, LINE_TYPE_DELETE, LINE_TYPE_CONTEXT

class Changeset():

    def get_patch_set(self):
        """
        Returns this changeset as a list of PatchedFiles.
        """
        return parse_diff(self.get_diff())

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

def print_depends(patches, depends):
    for p in patches:
        if not depends[p]:
            continue
        print("%s depends on: " % p)
        for dep in patches:
            if dep in depends[p]:
                print("  %s" % dep)

class ByFileAnalyzer(object):
    def analyze(self, patches):
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

class ByLineAnalyzer(object):

    class LineState(object):
        """ State of a particular line in a file """
        def __init__(self, lineno, line, changed_by):
            self.lineno = lineno
            self.line = line
            self.changed_by = changed_by
        def __str__(self):
            return "%s: changed by %s: %s" % (self.lineno, self.changed_by, self.line)

    def analyze(self, patches):
        """
        Find dependencies in a list of patches by looking at the lines they
        change.
        """
        # Per-file info on which patch last touched a particular line.
        # A dict of file => list of LineState objects
        state = collections.defaultdict(list)

        # Which patch depends on which other patches?
        # A dict of patch => (set of patches depended on)
        self.depends = collections.defaultdict(set)

        for patch in patches:
            for f in patch.get_patch_set():
                self.analyze_file(state, patch, f)

        return self.depends

    def analyze_file(self, state, patch, f):
        # fstate[fstate_pos] describes the first line equal to or
        # later than the next line to be processed. All linestates
        # before fstate_pos are already processed and containg target
        # line numbers, all states at or after fstate_pos still contain
        # source line numbers.
        self.fstate = state[f.path]
        self.fstate_pos = 0

        # Offset between source and target files at state_pos
        self.offset = 0

        for hunk in f:
            self.analyze_hunk(patch, hunk)

        self.line_state(-1)

    def line_state(self, lineno):
        """
        Returns the state of the given (source) line number, if any.
        Also takes care of updating the line states up to the given line
        number using self.offset.

        Passing lineno == -1 means to only update all states not yet
        updated.
        """
        while (self.fstate_pos < len(self.fstate) and
               (lineno == -1 or self.fstate[self.fstate_pos].lineno < lineno)):
            self.fstate[self.fstate_pos].lineno += self.offset
            self.fstate_pos += 1

        if (self.fstate_pos < len(self.fstate) and
            self.fstate[self.fstate_pos].lineno == lineno):
                return self.fstate[self.fstate_pos]

        return None

    def analyze_hunk(self, patch, hunk):
        for change in hunk.changes:
            line_state = self.line_state(change.source_lineno_abs)

            if change.action == LINE_TYPE_DELETE:
                self.offset -= 1

                if line_state:
                    # This file was touched by another patch, add
                    # dependency
                    self.depends[patch].add(line_state.changed_by)

                    # Forget about the state for this source line
                    del self.fstate[self.fstate_pos]

            elif change.action == LINE_TYPE_ADD:
                # Mark this line as changed by this patch
                s = self.LineState(lineno = change.target_lineno_abs,
                                   line = change.target_line,
                                   changed_by = patch)
                self.fstate.insert(self.fstate_pos, s)
                self.fstate_pos += 1
                self.offset += 1

            # Don't do anything for context lines

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
    parser.add_argument('--by-file', dest='analyzer', action='store_const',
                        const=ByFileAnalyzer, default=ByLineAnalyzer, help="""
                        Mark patches as conflicting when they change the
                        same file (by default, they are conflicting when
                        they change the same lines).""")

    args = parser.parse_args()

    patches = list(args.changeset_type.get_changesets(args.arguments))
    depends = args.analyzer().analyze(patches)

    print_depends(patches, depends)

if __name__ == "__main__":
    main()

# vim: set sw=4 sts=4 et:
