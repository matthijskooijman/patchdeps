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
import textwrap
import itertools
import subprocess
import collections

from parser import parse_diff
from parser import LINE_TYPE_ADD, LINE_TYPE_DELETE, LINE_TYPE_CONTEXT

class Bunch:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)

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

class PatchFile(Changeset):
    def __init__(self, filename):
        self.filename = filename

    def get_diff(self):
        f = open(self.filename, 'r', encoding='utf-8')
        # Iterating over a file gives separate lines, with newlines
        # included. We want those stripped off
        return map(lambda x: x.rstrip('\n'), f)

    @staticmethod
    def get_changesets(args):
        """
        Generate Changeset objects, given patch filenamesk
        """
        for filename in args:
            yield PatchFile(filename)

    def __str__(self):
        return os.path.basename(self.filename)

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
                desc = getattr(depends[p][dep], 'desc', None)
                if desc:
                    print("  %s (%s)" % (dep, desc))
                else:
                    print("  %s" % dep)

def print_depends_matrix(patches, depends):
    # Which patches have at least one dependency drawn (and thus
    # need lines from then on)?
    has_deps = set()
    for p in patches:
        line = str(p)[:80] + "  "
        if p in has_deps:
            line += "-" * (84 - len(line) + p.number * 2)
            line += "' "
        else:
            line += " " * (84 - len(line) + p.number * 2)
            line += "  "

        for dep in patches[p.number + 1:]:
            # For every later patch, print an "X" if it depends on this
            # one
            if p in depends[dep]:
                line += getattr(depends[dep][p], 'matrixmark', 'X')
                has_deps.add(dep)
            elif dep in has_deps:
                line += "|"
            else:
                line += " "
            line += " "

        print(line)

def dot_escape_string(s):
    return s.replace("\\", "\\\\").replace("\"", "\\\"")

def depends_dot(args, patches, depends):
    """
    Returns dot code for the dependency graph.
    """
    # Seems that fdp gives the best clustering if patches are often
    # independent
    res = """
digraph ConflictMap {
node [shape=box]
layout=neato
overlap=scale
"""

    if args.randomize:
        res += "start=random\n"

    for p in patches:
        label = dot_escape_string(str(p))
        label = "\\n".join(textwrap.wrap(label, 25))
        res += """{} [label="{}"]\n""".format(p.number, label)
        for dep, v in depends[p].items():
            style = getattr(v, 'dotstyle', 'solid')
            res += """{} -> {} [style={}]\n""".format(dep.number, p.number, style)
    res += "}\n"

    return res

def show_xdot(dot):
    """
    Shows a given dot graph in xdot
    """
    p = subprocess.Popen(['xdot', '/dev/stdin'], stdin=subprocess.PIPE)
    p.stdin.write(dot.encode('utf-8'))
    p.stdin.close()

class ByFileAnalyzer(object):
    def analyze(self, args, patches):
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
        depends = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                for other in touches_file[f.path]:
                    depends[patch][other] = True

                touches_file[f.path].append(patch)

        if 'blame' in args.actions:
            for f, ps in touches_file.items():
                patch = ps[-1]
                print("{!s:80} {}".format(str(patch)[:80], f))

        return depends

class ByLineAnalyzer(object):
    def analyze(self, args, patches):
        """
        Find dependencies in a list of patches by looking at the lines they
        change.
        """
        # Per-file info on which patch last touched a particular line.
        # A dict of file => list of LineState objects
        state = dict()

        # Which patch depends on which other patches?
        # A dict of patch => (dict of patch depended on => type) Here,
        # type is either DEPEND_HARD or DEPEND_PROXIMITY.
        depends = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                if not f.path in state:
                    state[f.path] = ByLineFileAnalyzer(f.path, args.proximity)

                state[f.path].analyze(depends, patch, f)

        if 'blame' in args.actions:
            for a in state.values():
                a.print_blame()

        return depends


class ByLineFileAnalyzer(object):
    """
    Helper class for the ByLineAnalyzer, that performs the analysis for
    a specific file. Created once and called for multiple patches.
    """

    # Used if a patch changes a line changed by another patch
    DEPEND_HARD = Bunch(desc = 'hard', matrixmark = 'X', dotstyle = 'solid')
    # Used if a patch changes a line changed near a line changed by
    # another patch
    DEPEND_PROXIMITY = Bunch(desc = 'proximity', matrixmark = '*', dotstyle = 'dashed')

    def __init__(self, fname, proximity):
        self.fname = fname
        self.proximity = proximity
        # Keep two view on our line state, so we can both iterate them
        # in order and do quick lookups
        self.line_list = []
        self.line_dict = {}

    def analyze(self, depends, patch, hunks):
        # This is the index in line_list of the first line state that
        # still uses source line numbers
        self.to_update_idx = 0

        # The index in line_list of the last line processed (i.e,
        # matched against a diff line)
        self.processed_idx = -1

        # Offset between source and target files at state_pos
        self.offset = 0

        for hunk in hunks:
            self.analyze_hunk(depends, patch, hunk)

        # Pretend we processed the entire list, so update_offset can
        # update the line numbers of any remaining (unchanged) lines
        # after the last hunk in this patch
        self.processed_idx = len(self.line_list)
        self.update_offset(0)

    def line_state(self, lineno, create):
        """
        Returns the state of the given (source) line number, creating a
        new empty state if it is not yet present and create is True.
        """

        self.processed_idx += 1
        for state in self.line_list[self.processed_idx:]:
            # Found it, return
            if state.lineno == lineno:
                return state
            elif state.lineno < lineno:
                # We're already passed this one, continue looking
                self.processed_idx += 1
                continue
            else:
                # It's not in there, stop looking
                break
                enumerate

        if not create:
            return None

        # We don't have state for this particular line, insert a
        # new empty state
        state = self.LineState(lineno = lineno)
        self.line_list.insert(self.processed_idx, state)
        return state

    def update_offset(self, amount):
        """
        Update the offset between target and source lines by the
        specified amount.

        Takes care of updating the line states of all processed lines
        (up to but excluding self.processed_idx) with the old offset
        before changing it.
        """

        for state in self.line_list[self.to_update_idx:self.processed_idx]:
            state.lineno += self.offset
            self.to_update_idx += 1

        self.offset += amount

    def analyze_hunk(self, depends, patch, hunk):
        #print('\n'.join(map(str, self.line_list)))
        #print('--')
        for change in hunk.changes:
            # When adding a line, don't bother creating a new line
            # state, since we'll be adding one anyway (this prevents
            # extra unused linestates)
            create = (change.action != LINE_TYPE_ADD)
            line_state = self.line_state(change.source_lineno_abs, create)

            # When changing a line, claim proximity lines before it as
            # well.
            if change.action != LINE_TYPE_CONTEXT and self.proximity != 0:
                # i points to the only linestate that could contain the
                # state for lineno
                i = self.processed_idx - 1
                lineno = change.source_lineno_abs - 1
                while (change.source_lineno_abs - lineno <= self.proximity and
                       lineno > 0):
                    if (i < 0 or
                        i >= self.to_update_idx and
                        self.line_list[i].lineno < lineno or
                        i < self.to_update_idx and
                        self.line_list[i].lineno - self.offset < lineno):
                            # This line does not exist yet, i points to an
                            # earlier line. Insert it
                            # _after_ i.
                            self.line_list.insert(i + 1, self.LineState(lineno))
                            # Point i at the inserted line
                            i += 1
                            self.processed_idx += 1
                            assert i >= self.to_update_idx, "Inserting before already updated line"

                    # Claim this line
                    s = self.line_list[i]

                    # Already claimed, stop looking. This should also
                    # prevent us from i becoming < to_update_idx - 1,
                    # since the state at to_update_idx - 1 should always
                    # be claimed
                    if patch.number in s.proximity or s.changed_by == patch:
                        break

                    s.proximity[patch.number] = patch
                    i -= 1
                    lineno -= 1

            # For changes that know about the contents of the old line,
            # check if it matches our observations
            if change.action != LINE_TYPE_ADD:
                if (line_state.line is not None and
                    change.source_line != line_state.line):
                        sys.stderr.write("While processing %s\n" % patch)
                        sys.stderr.write("Warning: patch does not apply cleanly! Results are probably wrong!\n")
                        sys.stderr.write("According to previous patches, line %s is:\n" % change.source_lineno_abs)
                        sys.stderr.write("%s\n" % line_state.line)
                        sys.stderr.write("But according to %s, it should be:\n" % patch)
                        sys.stderr.write("%s\n\n" % change.source_line)
                        sys.exit(1)

            if change.action == LINE_TYPE_CONTEXT:
                if line_state.line is None:
                    line_state.line = change.target_line

                # For context lines, only remember the line contents
                #claim_after(in_change, change.
                #in_change = False

            elif change.action == LINE_TYPE_ADD:
                self.update_offset(1)

                # Mark this line as changed by this patch
                s = self.LineState(lineno = change.target_lineno_abs,
                                   line = change.target_line,
                                   changed_by = patch)
                self.line_list.insert(self.processed_idx, s)
                assert self.processed_idx == self.to_update_idx, "Not everything updated?"

                # Since we insert this using the target line number, it
                # doesn't need to be updated again
                self.to_update_idx += 1

                # Add proximity deps for patches that touched code
                # around this line. We can't get a hard dependency for
                # an 'add' change, since we don't actually touch any
                # existing code
                if line_state:
                    deps = itertools.chain(line_state.proximity.values(),
                                           [line_state.changed_by])
                    for p in deps:
                        if p and p not in depends[patch] and p != patch:
                            depends[patch][p] = self.DEPEND_PROXIMITY

            elif change.action == LINE_TYPE_DELETE:
                self.update_offset(-1)

                # This file was touched by another patch, add
                # dependency
                if line_state.changed_by:
                    depends[patch][line_state.changed_by] = self.DEPEND_HARD
                    depends[patch][line_state.changed_by].dottooltip = "-" + change.source_line

                # Also add proximity deps for patches that touched code
                # around this line
                for p in line_state.proximity.values():
                    if (not p in depends[patch]) and p != patch:
                        depends[patch][p] = self.DEPEND_PROXIMITY

                # Forget about the state for this source line
                del self.line_list[self.processed_idx]
                self.processed_idx -= 1

            # After changing a line, claim proximity lines after it as
            # well.
            if change.action != LINE_TYPE_CONTEXT and self.proximity != 0:
                # i points to the only linestate that could contain the
                # state for lineno
                i = self.to_update_idx
                lineno = change.source_lineno_abs
                if lineno == 0: # When a file is created, the source
                                # line for the adds is 0...
                    lineno += 1
                while (lineno - change.source_lineno_abs < self.proximity):
                    if (i >= len(self.line_list) or
                        self.line_list[i].lineno > lineno):
                            # This line does not exist yet, i points to an
                            # later line. Insert it _before_ i.
                            self.line_list.insert(i, self.LineState(lineno))
                            assert i > self.processed_idx, "Inserting before already processed line"

                    # Claim this line
                    self.line_list[i].proximity[patch.number] = patch

                    i += 1
                    lineno += 1

    def print_blame(self):
        print("{}:".format(self.fname))
        next_line = None
        for line_state in self.line_list:
            if line_state.line is None:
                continue

            if next_line and line_state.lineno != next_line:
                for _ in range(3):
                    print("{:50}    .".format(""))

            patch = line_state.changed_by
            # For lines that only appeared as context
            if not patch:
                patch = ""

            print("{:50} {:4} {}".format(str(patch)[:50],
                                         line_state.lineno,
                                         line_state.line))
            next_line = line_state.lineno + 1

        print()

    class LineState(object):
        """ State of a particular line in a file """
        def __init__(self, lineno, line = None, changed_by = None):
            self.lineno = lineno
            self.line = line
            self.changed_by = changed_by
            # Dict of patch number => patch for patches that changed
            # lines near this one
            self.proximity = {}

        def __str__(self):
            return "%s: changed by %s: %s" % (self.lineno, self.changed_by, self.line)


def main():
    parser = argparse.ArgumentParser(description='Analyze patches for dependencies.')
    types = parser.add_argument_group('type').add_mutually_exclusive_group(required=True)
    types.add_argument('--git', dest='changeset_type', action='store_const',
                   const=GitRev, default=None,
                   help='Analyze a list of git revisions (non-option arguments are passed git git rev-list as-is')
    types.add_argument('--patches', dest='changeset_type', action='store_const',
                   const=PatchFile, default=None,
                   help='Analyze a list of patch files (non-option arguments are patch filenames')
    parser.add_argument('arguments', metavar="ARG", nargs='*', help="""
                        Specification of patches to analyze, depending
                        on the type given. When --git is given, this is
                        passed to git rev-list as-is (so use a valid
                        revision range, like HEAD^^..HEAD). When
                        --patches is given, these are filenames of patch
                        files.""")
    parser.add_argument('--by-file', dest='analyzer', action='store_const',
                        const=ByFileAnalyzer, default=ByLineAnalyzer, help="""
                        Mark patches as conflicting when they change the
                        same file (by default, they are conflicting when
                        they change the same lines).""")
    parser.add_argument('--proximity', default='2', metavar='LINES',
                        type=int, help="""
                        The number of lines changes should be apart to
                        prevent being marked as a dependency. Pass 0 to
                        only consider exactly the same line. This option
                        is no used when --by-file is passed. The default
                        value is %(default)s.""")
    parser.add_argument('--randomize', action='store_true', help="""
                        Randomize the graph layout produced by
                        --depends-dot and --depends-xdot.""")
    actions = parser.add_argument_group('actions')
    actions.add_argument('--blame', dest='actions', action='append_const',
                        const='blame', help="""
                        Instead of outputting patch dependencies,
                        output for each line or file which patch changed
                        it last.""")
    actions.add_argument('--depends-list', dest='actions', action='append_const',
                        const='depends-list', help="""
                        Output a list of each patch and the patches it
                        depends on.""")
    actions.add_argument('--depends-matrix', dest='actions', action='append_const',
                        const='depends-matrix', help="""
                        Output a matrix with patches on both axis and
                        markings for dependencies. This is used if not
                        action is given.""")
    actions.add_argument('--depends-dot', dest='actions', action='append_const',
                        const='depends-dot', help="""
                        Output dot format for a dependency graph.""")
    actions.add_argument('--depends-xdot', dest='actions', action='append_const',
                        const='depends-xdot', help="""
                        Show a dependencygraph using xdot (if available).""")

    args = parser.parse_args()
    if not args.actions:
        args.actions = ['depends-matrix']

    patches = list(args.changeset_type.get_changesets(args.arguments))

    for i, p in enumerate(patches):
        p.number = i

    depends = args.analyzer().analyze(args, patches)

    if 'depends-list' in args.actions:
        print_depends(patches, depends)

    if 'depends-matrix' in args.actions:
        print_depends_matrix(patches, depends)

    if 'depends-dot' in args.actions:
        print(depends_dot(args, patches, depends))

    if 'depends-xdot' in args.actions:
        show_xdot(depends_dot(args, patches, depends))

if __name__ == "__main__":
    main()

# vim: set sw=4 sts=4 et:
