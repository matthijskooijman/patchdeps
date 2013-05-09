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
        diff = subprocess.check_output(['git', 'diff-tree', '-p', self.rev])
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
        self.state = []
        self.proximity_to_claim = set()

    def analyze(self, depends, patch, hunks):

        # Offset between source and target files at state_pos
        self.offset = 0
        prev_state = self.state
        self.state = []

        for hunk in hunks:
            self.analyze_hunk(prev_state, depends, patch, hunk)

        # Move any lines remaining in the old state to the new state
        self.move_upto(prev_state, None)
        self.process_claims(depends, patch)

    def move_upto(self, state, lineno):
        """
        Move lines from the given state into self.state (using
        self.offset to update their line numbers), up to but excluding
        lineno. If lineno is None, all lines are moved.
        """
        while state and (lineno is None or state[0].lineno < lineno):
            s = state.pop(0)
            s.lineno += self.offset
            self.state.append(s)

    def get_state(self, state, lineno):
        """
        Returns the state of the given (source) line number. If there is
        no state, a new, empty state is returned.

        Also make sure that any lines before the request lines are
        copied over to the new state, with their line number updated.
        """
        # First, copy any previous lines
        self.move_upto(state, lineno)

        # Then, see if the next line state is the one we want
        if state and state[0].lineno == lineno:
            return state.pop(0)

        # If not, then we don't have it
        return self.LineState(lineno)

    def claim_before(self, lineno):
        """
        Claim self.proximity lines of context before (and excluding) the
        given lineno.
        """
        for l in range(max(1, lineno - self.proximity), lineno):
            self.proximity_to_claim.add(l)

    def claim_after(self, lineno):
        """
        Claim self.proximity lines of context after (and excluding) the
        given lineno.
        """
        for l in range(lineno + 1, lineno + self.proximity + 1):
            self.proximity_to_claim.add(l)

    def process_claims(self, depends, patch):
        """
        Process any proximity claims in self.proximity_to_claim and note
        them down in self.new_state. Should be called after the entire
        diff has been processed.
        """
        i = 0
        for lineno in sorted(self.proximity_to_claim):
            while i < len(self.state) and self.state[i].lineno < lineno:
                i += 1
            # Since new_state is sorted, i now points to the only
            # linestate that could contain the state for lineno
            if i == len(self.state) or self.state[i].lineno != lineno:
                self.state.insert(i, self.LineState(lineno))

            # Add proximity deps for patches that touched code
            # around this line
            for p in self.state[i].proximity.values():
                if (not p in depends[patch]):
                    depends[patch][p] = self.DEPEND_PROXIMITY

            # Claim the state
            self.state[i].proximity[patch.number] = patch

            i += 1

        self.proximity_to_claim.clear()

    def analyze_hunk(self, prev_state, depends, patch, hunk):
        #print('\n'.join(map(str, self.line_list)))
        #print('--')
        last_change = None
        for change in hunk.changes:
            if change.action != LINE_TYPE_CONTEXT and last_change is None:
                self.claim_before(change.target_lineno_abs)
            elif change.action == LINE_TYPE_CONTEXT and last_change is not None:
                self.claim_after(last_change)

            # Note the line number of the last change within the current
            # set of changes, or set it to None when we're not inside a
            # changes now (but in context)
            if change.action == LINE_TYPE_ADD:
                last_change = change.target_lineno_abs
            elif change.action == LINE_TYPE_DELETE:
                last_change = change.target_lineno_abs - 1
            else:
                last_change = None

            if change.action != LINE_TYPE_ADD:
                line_state = self.get_state(prev_state, change.source_linenos_abs[0])

                # Doublecheck to see if the current linestate has the
                # contents we expect
                if line_state.line is None:
                    # We didn't know about the contents of this line
                    # before (line claimed as proximity), but we do now.
                    line_state.line = change.source_line
                elif change.source_line != line_state.line:
                    sys.stderr.write("While processing %s\n" % patch)
                    sys.stderr.write("Warning: patch does not apply cleanly! Results are probably wrong!\n")
                    sys.stderr.write("According to previous patches, line %s of %s is:\n" % (change.source_linenos_abs[0], self.fname))
                    sys.stderr.write("%s\n" % line_state.line)
                    sys.stderr.write("But according to %s, it should be:\n" % patch)
                    sys.stderr.write("%s\n\n" % change.source_line)
                    print(line_state)
                    print(change)
                    sys.exit(1)

                if change.action == LINE_TYPE_CONTEXT:
                    # Update the line number and optoinally use the line
                    # contents from the patch (if we didn't have any
                    # yet).
                    line_state.lineno = change.target_lineno_abs
                    self.state.append(line_state)
                elif change.action == LINE_TYPE_DELETE:
                    # This file was touched by another patch, add
                    # dependency
                    if line_state.changed_by:
                        depends[patch][line_state.changed_by] = self.DEPEND_HARD

                    self.offset -= 1

                    # Note we do not insert the line into the new state

            else: # LINE_TYPE_ADD
                # Mark this line as changed by this patch
                line_state = self.LineState(lineno = change.target_lineno_abs,
                                            line = change.target_line,
                                            changed_by = patch)
                self.state.append(line_state)
                self.offset += 1

        # Claim any proximity if we the chunk ended in a change line
        # (which should only happen for context-less diffs).
        if last_change is not None:
            self.claim_after(last_change)

    def print_blame(self):
        print("{}:".format(self.fname))
        next_line = None
        for line_state in self.state:
            if line_state.line is None:
                continue

            if next_line and line_state.lineno != next_line:
                for _ in range(3):
                    print("{:50}    .".format(""))

            patch = line_state.changed_by
            # For lines that only appeared as context
            if not patch and line_state.proximity:
                patch = "*: " + str(next(iter(line_state.proximity.values())))
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

            # Changing this line also counts as changing a line nearby,
            # to make process_claims a bit easer
            if changed_by:
                self.proximity[changed_by.number] = changed_by

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
                        The amount of lines around a change that should
                        also be considered part of the change. Two
                        changes need to be twice the given number of
                        lines apart to prevent being marked as a
                        dependency. Pass 0 to only consider exactly the
                        same line. This option is not used when --by-file
                        is passed. The default value is %(default)s.""")
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
