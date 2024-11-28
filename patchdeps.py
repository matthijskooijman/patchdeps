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

from __future__ import annotations

import argparse
import collections
import itertools
import os
import subprocess
import sys
import textwrap
from enum import Enum
from parser import LineType, parse_diff
from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from parser import Hunk, PatchedFile


class Depend(Enum):
    # Used if a patch changes a line changed by another patch
    HARD = ("hard", "X", "solid")
    # Used if a patch changes a line changed near a line changed by another patch
    PROXIMITY = ("proximity", "*", "dashed")
    # By filename
    FILENAME = ("", "X", "solid")

    def __init__(self, desc: str, matrixmark: str, dotstyle: str) -> None:
        self.desc = desc
        self.matrixmark = matrixmark
        self.dotstyle = dotstyle


class Changeset:
    def get_patch_set(self) -> list[PatchedFile]:
        """
        Returns this changeset as a list of PatchedFiles.
        """
        parsed = parse_diff(self.get_diff())
        if not parsed:
            sys.stderr.write(f"WARNING: Parsing diff {self} produced no patch hunks, maybe format is invalid?\n")
        return parsed

    def get_diff(self) -> Iterable[str]:
        """
        Returns the textual unified diff for this changeset as an
        iterable of lines
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self!s})"


class PatchFile(Changeset):
    def __init__(self, filename: str) -> None:
        self.filename = filename

    def get_diff(self) -> Iterable[str]:
        f = open(self.filename, encoding='utf-8')
        # Iterating over a file gives separate lines, with newlines
        # included. We want those stripped off
        return (line.rstrip('\n') for line in f)

    @staticmethod
    def get_changesets(args: Iterable[str]) -> Iterator[PatchFile]:
        """
        Generate Changeset objects, given patch filenamesk
        """
        for filename in args:
            yield PatchFile(filename)

    def __str__(self) -> str:
        return os.path.basename(self.filename)


class GitRev(Changeset):
    def __init__(self, rev: str, msg: str) -> None:
        self.rev = rev
        self.msg = msg

    def get_diff(self) -> Iterable[str]:
        diff = subprocess.check_output(['git', 'diff', '--no-color', f"{self.rev}^", self.rev])
        # Convert to utf8 and just drop any invalid characters (we're
        # not interested in the actual file contents and all diff
        # special characters are valid ascii).
        return diff.decode(errors='ignore').split('\n')

    def __str__(self) -> str:
        return f"{self.rev} ({self.msg})"

    @staticmethod
    def get_changesets(args: list[str]) -> Iterator[GitRev]:
        """
        Generate Changeset objects, given arguments for git rev-list.
        """
        output = subprocess.check_output(['git', 'rev-list', '--oneline', '--reverse', *args])

        if not output:
            sys.stderr.write("No revisions specified?\n")
        else:
            lines = output.decode().strip().split('\n')
            for line in lines:
                yield GitRev(*line.split(' ', 1))


def print_depends(patches: list[Changeset], depends: dict[Changeset, dict[Changeset, Depend]]) -> None:
    for p in patches:
        if dependencies := depends[p]:
            print(f"{p} depends on:")
            for dep in patches:
                if dependency := dependencies.get(dep):
                    desc = dependency.desc
                    if desc:
                        print(f"  {dep} ({desc})")
                    else:
                        print(f"  {dep}")


def print_depends_tsort(patches: list[Changeset], depends: dict[Changeset, dict[Changeset, Depend]]) -> None:
    for p in patches:
        if dependencies := depends[p]:
            for dep in patches:
                if dep in dependencies:
                    print(f"{dep}\t{p}")


def print_depends_matrix(patches: list[Changeset], depends: dict[Changeset, dict[Changeset, Depend]]) -> None:
    # Which patches have at least one dependency drawn (and thus
    # need lines from then on)?
    has_deps: set[Changeset] = set()
    prereq: set[Changeset] = {dep for p in patches for dep in depends[p]}
    # Every patch depending on other patches needs a column
    depending: list[Changeset] = [p for p in patches if depends[p]]
    column = 82
    for p in patches:
        if depending and depending[0] == p:
            del depending[0]
            column += 2
            fill, corner = "─", "┘"
        else:
            fill = corner = "·" if p in prereq else " "
        line = f"{f'{p!s:.80}  ':{fill}<{column}}{corner}"

        for i, dep in enumerate(depending):
            # Show ruler if a later patch depends on this one
            ruler = "·" if any(depends[d].get(p) for d in depending[i:]) else " "
            # For every later patch, print an "X" if it depends on this one
            if dependency := depends[dep].get(p):
                line += f"{ruler}{dependency.matrixmark}"
                has_deps.add(dep)
            elif dep in has_deps:
                line += f"{ruler}│"
            else:
                line += ruler * 2

        print(line)


def dot_escape_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def depends_dot(args: argparse.Namespace, patches: list[Changeset], depends: dict[Changeset, dict[Changeset, Depend]]) -> str:
    """
    Returns dot code for the dependency graph.
    """
    # Seems that 'fdp' gives the best clustering if patches are often independent
    res = """
digraph ConflictMap {
node [shape=box]
layout=neato
overlap=scale
"""

    if args.randomize:
        res += "start=random\n"

    for i, p in enumerate(patches):
        label = dot_escape_string(str(p))
        label = "\\n".join(textwrap.wrap(label, 25))
        res += f'{i} [label="{label}"]\n'
        for dep, v in depends[p].items():
            res += f"{patches.index(dep)} -> {i} [style={v.dotstyle}]\n"
    res += "}\n"

    return res


def show_xdot(dot: str) -> None:
    """
    Shows a given dot graph in xdot
    """
    subprocess.run(['xdot', '/dev/stdin'], input=dot.encode(), check=True)


class ByFileAnalyzer:
    def analyze(self, args: argparse.Namespace, patches: list[Changeset]) -> dict[Changeset, dict[Changeset, Depend]]:
        """
        Find dependencies in a list of patches by looking at the files they
        change.

        The algorithm is simple: Just keep a list of files changed, and mark
        two patches as conflicting when they change the same file.
        """
        # Which patches touch a particular file. A dict of filename => list
        # of patches
        touches_file: dict[str, list[Changeset]] = collections.defaultdict(list)

        # Which patch depends on which other patches?
        # A dict of patch => (dict of dependent patches => Depend.FILENAME)
        depends: dict[Changeset, dict[Changeset, Depend]] = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                for other in touches_file[f.path]:
                    depends[patch][other] = Depend.FILENAME

                touches_file[f.path].append(patch)

        if 'blame' in args.actions:
            for path, ps in touches_file.items():
                patch = ps[-1]
                print(f"{patch!s:80.80} {path}")

        return depends


class ByLineAnalyzer:
    def analyze(self, args: argparse.Namespace, patches: list[Changeset]) -> dict[Changeset, dict[Changeset, Depend]]:
        """
        Find dependencies in a list of patches by looking at the lines they
        change.
        """
        # Per-file info on which patch last touched a particular line.
        # A dict of file => list of LineState objects
        state: dict[str, ByLineFileAnalyzer] = {}

        # Which patch depends on which other patches?
        # A dict of patch => (dict of dependent patches => Depend)
        depends: dict[Changeset, dict[Changeset, Depend]] = collections.defaultdict(dict)

        for patch in patches:
            for f in patch.get_patch_set():
                if f.path not in state:
                    state[f.path] = ByLineFileAnalyzer(f.path, args.proximity)

                state[f.path].analyze(depends, patch, f)

        if 'blame' in args.actions:
            for a in state.values():
                a.print_blame()

        return depends


class ByLineFileAnalyzer:
    """
    Helper class for the ByLineAnalyzer, that performs the analysis for
    a specific file. Created once per file and called for multiple patches.
    """

    def __init__(self, fname: str, proximity: int) -> None:
        self.fname = fname
        self.proximity = proximity
        self.line_list: list[ByLineFileAnalyzer.LineState] = []

    def analyze(self, depends: dict[Changeset, dict[Changeset, Depend]], patch: Changeset, hunks: PatchedFile) -> None:
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

    def line_state(self, lineno: int, create: bool) -> LineState | None:
        """
        Returns the state of the given (source) line number, creating a
        new empty state if it is not yet present and create is True.
        """

        self.processed_idx += 1
        for state in self.line_list[self.processed_idx:]:
            # Found it, return
            if state.lineno == lineno:
                return state
            # It's not in there, stop looking
            if state.lineno > lineno:
                break
            # We're already passed this one, continue looking
            self.processed_idx += 1

        if not create:
            return None

        # We don't have state for this particular line, insert a
        # new empty state
        state = self.LineState(lineno=lineno)
        self.line_list.insert(self.processed_idx, state)
        return state

    def update_offset(self, amount: int) -> None:
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

    def analyze_hunk(self, depends: dict[Changeset, dict[Changeset, Depend]], patch: Changeset, hunk: Hunk) -> None:
        #print('\n'.join(map(str, self.line_list)))
        #print('--')
        for change in hunk.changes:
            # When adding a line, don't bother creating a new line
            # state, since we'll be adding one anyway (this prevents
            # extra unused linestates)
            create = change.action != LineType.ADD
            line_state = self.line_state(change.source_lineno_abs, create)

            # When changing a line, claim proximity lines before it as
            # well.
            if change.action != LineType.CONTEXT and self.proximity != 0:
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
                    if patch in s.proximity or s.changed_by == patch:
                        break

                    s.proximity.add(patch)
                    i -= 1
                    lineno -= 1

            # For changes that know about the contents of the old line,
            # check if it matches our observations
            if change.action != LineType.ADD:
                assert line_state is not None
                if line_state.line is not None and change.source_line != line_state.line:
                    sys.exit(
                        f"While processing {patch}\n"
                        "Warning: patch does not apply cleanly! Results are probably wrong!\n"
                        f"According to previous patches, line {change.source_lineno_abs} is:\n"
                        f"{line_state.line}\n"
                        f"But according to {patch}, it should be:\n"
                        f"{change.source_line}\n\n",
                    )

            if change.action == LineType.CONTEXT:
                assert line_state is not None
                if line_state.line is None:
                    line_state.line = change.target_line

                # For context lines, only remember the line contents
                #claim_after(in_change, change.
                #in_change = False

            elif change.action == LineType.ADD:
                self.update_offset(1)

                # Mark this line as changed by this patch
                s = self.LineState(lineno=change.target_lineno_abs,
                                   line=change.target_line,
                                   changed_by=patch)
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
                    deps = itertools.chain(line_state.proximity,
                                           [line_state.changed_by])
                    for p in deps:
                        if p and p not in depends[patch] and p != patch:
                            depends[patch][p] = Depend.PROXIMITY

            elif change.action == LineType.DELETE:
                assert line_state is not None
                self.update_offset(-1)

                # This file was touched by another patch, add dependency
                if line_state.changed_by:
                    depends[patch][line_state.changed_by] = Depend.HARD
                    # TODO(PHH): Assigning to singleton Depend.*.dottooltip; unused by `depends_dot`
                    # https://graphviz.org/docs/attrs/tooltip/
                    # depends[patch][line_state.changed_by].dottooltip = f"-{change.source_line}"

                # Also add proximity deps for patches that touched code
                # around this line
                for p in line_state.proximity:
                    if (p not in depends[patch]) and p != patch:
                        depends[patch][p] = Depend.PROXIMITY

                # Forget about the state for this source line
                del self.line_list[self.processed_idx]
                self.processed_idx -= 1

            # After changing a line, claim proximity lines after it as well.
            if change.action != LineType.CONTEXT and self.proximity != 0:
                # i points to the only linestate that could contain the
                # state for lineno
                i = self.to_update_idx
                # When a file is created, the source line for the adds is 0...
                lineno = change.source_lineno_abs or 1
                while (lineno - change.source_lineno_abs < self.proximity):
                    if i >= len(self.line_list) or self.line_list[i].lineno > lineno:
                        # This line does not exist yet, i points to an
                        # later line. Insert it _before_ i.
                        self.line_list.insert(i, self.LineState(lineno))
                        assert i > self.processed_idx, "Inserting before already processed line"

                    # Claim this line
                    self.line_list[i].proximity.add(patch)

                    i += 1
                    lineno += 1

    def print_blame(self) -> None:
        print(f"{self.fname}:")
        next_line: int | None = None
        for line_state in self.line_list:
            if line_state.line is None:
                continue

            if next_line and line_state.lineno != next_line:
                print(f"{'':50}    …")

            print(f"{line_state.changed_by or ''!s:50.50} {line_state.lineno:4} {line_state.line}")
            next_line = line_state.lineno + 1

        print()


    class LineState:
        """ State of a particular line in a file """
        def __init__(self, lineno: int, line: str | None = None, changed_by: Changeset | None = None) -> None:
            self.lineno = lineno
            self.line = line
            self.changed_by = changed_by
            # Set of patches that changed lines near this one
            self.proximity: set[Changeset] = set()

        def __str__(self) -> str:
            return f"{self.lineno}: changed by {self.changed_by}: {self.line}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Analyze patches for dependencies.')

    types = parser.add_argument_group('type').add_mutually_exclusive_group(required=True)
    types.add_argument('--git', dest='changeset_type', action='store_const',
                   const=GitRev,
                   help='Analyze a list of git revisions (non-option arguments are passed git git rev-list as-is')
    types.add_argument('--patches', dest='changeset_type', action='store_const',
                   const=PatchFile,
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
    actions.add_argument('--list', dest='actions', action='append_const',
                        const='list', help="""
                        Output a list of each patch and the patches it
                        depends on.""")
    actions.add_argument('--matrix', dest='actions', action='append_const',
                        const='matrix', help="""
                        Output a matrix with patches on both axis and
                        markings for dependencies. This is used if not
                        action is given.""")
    actions.add_argument('--tsort', dest='actions', action='append_const',
                        const='tsort', help="""
                        Show dependency graph as tsort input.""")
    actions.add_argument('--dot', dest='actions', action='append_const',
                        const='dot', help="""
                        Output dot format for a dependency graph.""")
    actions.add_argument('--xdot', dest='actions', action='append_const',
                        const='xdot', help="""
                        Show a dependency graph using xdot (if available).""")

    args = parser.parse_args()

    if not args.actions:
        args.actions = ['matrix']

    return args


def main() -> None:
    args = parse_args()

    patches: list[Changeset] = list(args.changeset_type.get_changesets(args.arguments))

    depends = args.analyzer().analyze(args, patches)

    if 'list' in args.actions:
        print_depends(patches, depends)

    if 'matrix' in args.actions:
        print_depends_matrix(patches, depends)

    if 'tsort' in args.actions:
        print_depends_tsort(patches, depends)

    if 'dot' in args.actions:
        print(depends_dot(args, patches, depends))

    if 'xdot' in args.actions:
        show_xdot(depends_dot(args, patches, depends))


if __name__ == "__main__":
    main()

# vim: set sw=4 sts=4 et:
