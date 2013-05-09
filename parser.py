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

# @@ -(source offset, length) +(target offset, length) @@
# For merges (diff with multiple parents):
# @@@ -(source offset, length) -(source offset, length) +(target offset, length) @@
# More than 2 is also possible
RE_HUNK_HEADER = re.compile(r"^@@+ ([^@]*) @@+")
RE_HUNK_HEADER_NUMBERS = re.compile(r"[-\+](\d+)(?:,(\d+))?")

#   kept line (context)
# + added line
# - deleted line
RE_HUNK_BODY_LINE = re.compile(r'^([- \+]+)')

LINE_TYPE_ADD = '+'
LINE_TYPE_DELETE= '-'
LINE_TYPE_CONTEXT = ' '
LINE_TYPES = (LINE_TYPE_ADD, LINE_TYPE_DELETE, LINE_TYPE_CONTEXT)

class UnidiffParseException(Exception):
    pass

class Change(object):
    """
    A single line from a patch hunk.
    """
    def __init__(self, hunk, action, actions, source_linenos_rel, source_line,
                 target_lineno_rel, target_line):
        """
        The line numbers must always be present, either source_line or
        target_line can be None depending on the action.
        """
        self.hunk = hunk
        # The "dominant" action (ADD or DELETE if present, CONTEXT otherwise)
        self.action = action
        # The action for each of the sources
        self.actions = actions
        self.source_linenos_rel = source_linenos_rel
        self.source_line = source_line
        self.target_lineno_rel = target_lineno_rel
        self.target_line = target_line

        self.source_linenos_abs = list(map(sum, zip(self.hunk.source_starts, self.source_linenos_rel)))
        self.target_lineno_abs = self.hunk.target_start + self.target_lineno_rel

    def __str__(self):
        return "(-%s, +%s) %s%s" % (', -'.join(map(str, self.source_linenos_abs)),
                                    self.target_lineno_abs,
                                    self.actions,
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

    def __init__(self, src_starts, src_lens, tgt_start, tgt_len):
        self.source_starts = [int(n) for n in src_starts]
        self.source_lengths = [int(n) for n in src_lens]
        self.target_start = int(tgt_start)
        self.target_length = int(tgt_len)
        self.changes = []

    def append_change(self, change):
        """
        Append a Change
        """
        self.changes.append(change)

    def __str__(self):
        return "<@@ %s %d,%d @@>" % (' '.join("%d,%d" % x for x in zip(self.source_starts, self.source_lengths)),
                                        self.target_start, self.target_length)


def _parse_hunk(diff, source_starts, source_lens, target_start, target_len):
    hunk = Hunk(source_starts, source_lens, target_start, target_len)
    modified = 0
    deleting = 0
    num_sources = len(source_starts)
    source_linenos = [0] * num_sources
    target_lineno = 0

    for line in diff:
        if line and line[0] == '\\':
            # Skip "\ No newline at end of file" lines
            continue

        if len(line) < num_sources:
            raise UnidiffParseException('Hunk diff data expected: ' + line)

        # With multiple sources, there is one action (+/-/space) column
        # for each source.
        actions = line[:num_sources]
        original_line = line[num_sources:]

        # Check if only valid action characters are used
        if any([c not in LINE_TYPES for c in actions]):
            raise UnidiffParseException('Invalid action characters: ' + line)

        # Mixing - and + doesn't make sense (only mixing either with
        # spaces is possible).
        if LINE_TYPE_DELETE in actions and LINE_TYPE_ADD in actions:
            raise UnidiffParseException('Cannot mix + and - actions: ' + line)

        kwargs = dict(actions = actions,
                      hunk = hunk,
                      source_linenos_rel = list(source_linenos),
                      target_lineno_rel = target_lineno,
                      source_line = None,
                      target_line = None)

        if LINE_TYPE_ADD in actions:
            kwargs['target_line'] = original_line
            action = LINE_TYPE_ADD
        elif LINE_TYPE_DELETE in actions:
            kwargs['source_line'] = original_line
            action = LINE_TYPE_DELETE
        else:
            # Action for all sources must be context
            kwargs['source_line'] = original_line
            kwargs['target_line'] = original_line
            action = LINE_TYPE_CONTEXT

        # The "dominant" action
        kwargs['action'] = action

        # Find out the line number change for each source
        for (i, a) in enumerate(actions):
            # Delete action always means the line was in that source,
            # but a context action in a line that also has delete
            # actions means the line was already gone in that source, so
            # don't increment the source lineno in that case.
            if (a == LINE_TYPE_DELETE or
                a == LINE_TYPE_CONTEXT and action != LINE_TYPE_DELETE):
                    source_linenos[i] += 1

        if (action == LINE_TYPE_CONTEXT or
            action == LINE_TYPE_ADD):
                target_lineno += 1

        hunk.append_change(Change(**kwargs))

        if (any(a > b for (a, b) in zip(source_linenos, hunk.source_lengths)) or
            target_lineno > hunk.target_length):
                raise UnidiffParseException( 'To many lines in hunk: ' + line)

        # check if we have seen all the lines advertised by the header
        if (source_linenos == hunk.source_lengths and
            target_lineno == hunk.target_length):
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
            starts = []
            lengths = []
            for pair in re_hunk_header.group(1).split(' '):
                # The hunk header contains a two or more pairs of
                # numbers, like:
                # @@@ -428,15 -425,20 +426,19 @@@
                # More than two is used by git for merge commits, e.g.,
                # a diff with multiple sources.
                re_pair = RE_HUNK_HEADER_NUMBERS.match(pair)
                if not re_pair:
                    raise UnidiffParseException('Invalid hunk header: ' + line)

                starts.append(re_pair.group(1))
                length = re_pair.group(2)
                # If the hunk length is 1, it is sometimes left out
                if length is None:
                    lengths.append(1)
                lengths.append(length)

            if len(starts) < 2:
                raise UnidiffParseException('Invalid hunk header: ' + line)

            hunk = _parse_hunk(diff, starts[:-1], lengths[:-1], starts[-1], lengths[-1])
            current_file.append(hunk)
    return ret

