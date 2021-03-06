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
import itertools

RE_SOURCE_FILENAME = re.compile(r'^--- (?P<filename>[^\t]+)')
RE_TARGET_FILENAME = re.compile(r'^\+\+\+ (?P<filename>[^\t]+)')

# @@ (source offset, length) (target offset, length) @@
RE_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?\ @@")

#   kept line (context)
# + added line
# - deleted line
# \ No newline case (ignore)
RE_HUNK_BODY_LINE = re.compile(r'^([- \+\\])')

LINE_TYPE_ADD = '+'
LINE_TYPE_DELETE= '-'
LINE_TYPE_CONTEXT = ' '

class UnidiffParseException(Exception):
    pass

class Change(object):
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

        self.source_lineno_abs =  self.hunk.source_start + self.source_lineno_rel
        self.target_lineno_abs =  self.hunk.target_start + self.target_lineno_rel

    def __str__(self):
        return "(-%s, +%s) %s%s" % (self.source_lineno_abs,
                                    self.target_lineno_abs,
                                    self.action,
                                    self.source_line or self.target_line)

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

class Hunk(object):
    """Each of the modified blocks of a file."""

    def __init__(self, src_start=0, src_len=0, tgt_start=0, tgt_len=0):
        self.source_start = int(src_start)
        self.source_length = int(src_len)
        self.target_start = int(tgt_start)
        self.target_length = int(tgt_len)
        self.changes = []
        self.to_parse = [self.source_length, self.target_length]

    def is_valid(self):
        """Check hunk header data matches entered lines info."""
        return self.to_parse == [0, 0]

    def append_change(self, change):
        """
        Append a Change
        """
        self.changes.append(change)

        if (change.action == LINE_TYPE_CONTEXT or
            change.action == LINE_TYPE_DELETE):
                self.to_parse[0] -= 1
                if self.to_parse[0] < 0:
                    raise UnidiffParseException(
                        'To many source lines in hunk: %s' % self)

        if (change.action == LINE_TYPE_CONTEXT or
            change.action == LINE_TYPE_ADD):
                self.to_parse[1] -= 1
                if self.to_parse[1] < 0:
                    raise UnidiffParseException(
                        'To many target lines in hunk: %s' % self)

    def __str__(self):
        return "<@@ %d,%d %d,%d @@>" % (self.source_start, self.source_length,
                                        self.target_start, self.target_length)


def _parse_hunk(diff, source_start, source_len, target_start, target_len):
    hunk = Hunk(source_start, source_len, target_start, target_len)
    modified = 0
    deleting = 0
    source_lineno = 0
    target_lineno = 0

    for line in diff:
        valid_line = RE_HUNK_BODY_LINE.match(line)
        if valid_line:
            action = valid_line.group(0)
            original_line = line[1:]

            kwargs = dict(action = action,
                          hunk = hunk,
                          source_lineno_rel = source_lineno,
                          target_lineno_rel = target_lineno,
                          source_line = None,
                          target_line = None)

            if action == LINE_TYPE_ADD:
                kwargs['target_line'] = original_line
                target_lineno += 1
            elif action == LINE_TYPE_DELETE:
                kwargs['source_line'] = original_line
                source_lineno += 1
            elif action == LINE_TYPE_CONTEXT:
                kwargs['source_line'] = original_line
                kwargs['target_line'] = original_line
                source_lineno += 1
                target_lineno += 1
            hunk.append_change(Change(**kwargs))
        else:
            raise UnidiffParseException('Hunk diff data expected: ' + line)

        # check hunk len(old_lines) and len(new_lines) are ok
        if hunk.is_valid():
            break

    return hunk


def parse_diff(diff):
    ret = []
    current_file = None
    # Make sure we only iterate the diff once, instead of restarting
    # from the top inside _parse_hunk
    diff = itertools.chain(diff)

    for line in diff:
        # check for source file header
        check_source = RE_SOURCE_FILENAME.match(line)
        if check_source:
            source_file = check_source.group('filename')
            current_file = None
            continue

        # check for target file header
        check_target = RE_TARGET_FILENAME.match(line)
        if check_target:
            target_file = check_target.group('filename')
            current_file = PatchedFile(source_file, target_file)
            ret.append(current_file)
            continue

        # check for hunk header
        re_hunk_header = RE_HUNK_HEADER.match(line)
        if re_hunk_header:
            hunk_info = list(re_hunk_header.groups())
            # If the hunk length is 1, it is sometimes left out
            for i in (1, 3):
                if hunk_info[i] is None:
                    hunk_info[i] = 1
            hunk = _parse_hunk(diff, *hunk_info)
            current_file.append(hunk)
    return ret

