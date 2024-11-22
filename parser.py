# The MIT License (MIT)
# Copyright (c) 2012 Matias Bordese
# Copyright (c) 2013 Matthijs Kooijman <matthijs@stdin.nl>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
# OR OTHER DEALINGS IN THE SOFTWARE.

"""Unified diff parser module."""

# This file is based on the unidiff library by Matías Bordese (at
# https://github.com/matiasb/python-unidiff)

import re
from enum import Enum

RE_SOURCE_FILENAME = re.compile(r'^--- (?P<filename>[^\t]+)')
RE_TARGET_FILENAME = re.compile(r'^\+\+\+ (?P<filename>[^\t]+)')

# @@ (source offset, length) (target offset, length) @@
RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?\ @@")
RE_HUNK_BODY_LINE = re.compile(r'^([- \+\\])')


class LineType(Enum):
    ADD = '+'  # added line
    DELETE= '-'  # deleted line
    CONTEXT = ' '  # kept line (context)
    IGNORE = '\\'  # No newline case (ignore)


class UnidiffParseError(Exception):
    pass


class Line:
    """
    A single line from a patch hunk.
    """
    def __init__(self, hunk, action, source_lineno_rel, source_line,
                 target_lineno_rel, target_line):
        """
        The line numbers must always be present, either source_line or
        target_line can be None depending on the action.
        """
        self.hunk = hunk
        self.action = action
        self.source_lineno_rel = source_lineno_rel
        self.source_line = source_line
        self.target_lineno_rel = target_lineno_rel
        self.target_line = target_line

        self.source_lineno_abs = self.hunk.source_start + self.source_lineno_rel
        self.target_lineno_abs = self.hunk.target_start + self.target_lineno_rel

    def __str__(self):
        return f"(-{self.source_lineno_abs}, +{self.target_lineno_abs}) {self.action}{self.source_line or self.target_line}"


class PatchedFile(list):
    """Data from a patched file."""

    def __init__(self, source='', target=''):
        self.source_file = source
        self.target_file = target

        if self.source_file.startswith('a/') and self.target_file.startswith('b/'):
            self.path = self.source_file[2:]
        elif self.source_file.startswith('a/') and self.target_file == '/dev/null':
            self.path = self.source_file[2:]
        elif self.target_file.startswith('b/') and self.source_file == '/dev/null':
            self.path = self.target_file[2:]
        else:
            self.path = self.source_file


class Hunk:
    """Each of the modified blocks of a file."""

    def __init__(self, src_start=0, src_len=0, tgt_start=0, tgt_len=0):
        self.source_start = src_start
        self.source_length = self.source_todo = src_len
        self.target_start = tgt_start
        self.target_length = self.target_todo = tgt_len
        self.changes = []

    def is_valid(self):
        """Check hunk header data matches entered lines info."""
        return self.source_todo == self.target_todo == 0

    def append_line(self, line):
        """
        Append a line
        """
        self.changes.append(line)

        if line.action in {LineType.CONTEXT, LineType.DELETE}:
            self.source_todo -= 1
            if self.source_todo < 0:
                raise UnidiffParseError(
                    f'Too many source lines in hunk: {self}')

        if line.action in {LineType.CONTEXT, LineType.ADD}:
            self.target_todo -= 1
            if self.target_todo < 0:
                raise UnidiffParseError(
                    f'Too many target lines in hunk: {self}')

    def __str__(self):
        return f"<@@ {self.source_start},{self.source_length} {self.target_start},{self.target_length} @@>"


def _parse_hunk(diff, source_start, source_len, target_start, target_len):
    hunk = Hunk(source_start, source_len, target_start, target_len)
    source_lineno = 0
    target_lineno = 0

    for line in diff:
        valid_line = RE_HUNK_BODY_LINE.match(line)
        if valid_line:
            action = LineType(valid_line.group(0))
            original_line = line[1:]

            kwargs: dict[str, Any] = {
                "action": action,
                "hunk": hunk,
                "source_lineno_rel": source_lineno,
                "target_lineno_rel": target_lineno,
                "source_line": None,
                "target_line": None,
            }

            if action == LineType.ADD:
                kwargs['target_line'] = original_line
                target_lineno += 1
            elif action == LineType.DELETE:
                kwargs['source_line'] = original_line
                source_lineno += 1
            elif action == LineType.CONTEXT:
                kwargs['source_line'] = original_line
                kwargs['target_line'] = original_line
                source_lineno += 1
                target_lineno += 1
            hunk.append_line(Line(**kwargs))
        else:
            raise UnidiffParseError(f'Hunk diff data expected: {line}')

        # check hunk len(old_lines) and len(new_lines) are ok
        if hunk.is_valid():
            break

    return hunk


def parse_diff(diff):
    ret = []
    # Make sure we only iterate the diff once, instead of restarting
    # from the top inside _parse_hunk
    lines = iter(diff)
    for line in lines:
        if m := RE_SOURCE_FILENAME.match(line):
            source_file = m['filename']
        elif m := RE_TARGET_FILENAME.match(line):
            target_file = m['filename']
            current_file = PatchedFile(source_file, target_file)
            ret.append(current_file)
        elif m := RE_HUNK_HEADER.match(line):
            hunk = _parse_hunk(
                lines,
                int(m[1]),
                _int1(m[2]),
                int(m[3]),
                _int1(m[4]),
            )
            current_file.append(hunk)

    return ret


def _int1(s: str) -> int:
    return 1 if s is None else int(s)
