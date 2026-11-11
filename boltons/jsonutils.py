# Copyright (c) 2013, Mahmoud Hashemi
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
#
#    * The names of the contributors may not be used to endorse or
#      promote products derived from this software without specific
#      prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""``jsonutils`` aims to provide various helpers for working with
JSON. Currently it focuses on providing a reliable and intuitive means
of working with `JSON Lines`_-formatted files.

.. _JSON Lines: http://jsonlines.org/

"""


import io
import os
import json
from collections import namedtuple


DEFAULT_BLOCKSIZE = 4096

#: A final line without a trailing newline makes :func:`reverse_iter_lines`
#: raise :exc:`ValueError`, since it cannot be distinguished from a record
#: that was only half-written when the file was read.
INCOMPLETE_RAISE = 'raise'
#: A final line without a trailing newline is silently skipped.
INCOMPLETE_SKIP = 'skip'
_INCOMPLETE_POLICIES = (INCOMPLETE_RAISE, INCOMPLETE_SKIP)


__all__ = ['JSONLIterator', 'ReverseLine', 'reverse_iter_lines',
           'INCOMPLETE_RAISE', 'INCOMPLETE_SKIP']


class ReverseLine(namedtuple('ReverseLine', 'line start end')):
    """A line returned by :func:`reverse_iter_lines` when
    ``with_ranges=True``.

    ``line`` is the line content (decoded text or bytes), and ``start``
    and ``end`` are the half-open byte offsets of that content in the
    underlying stream, excluding the line terminator. Reading
    ``stream[start:end]`` reproduces the exact bytes of ``line`` before
    decoding. On text streams without a byte buffer (e.g.
    :class:`io.StringIO`), the offsets are character positions instead.
    """
    __slots__ = ()


def reverse_iter_lines(file_obj, blocksize=DEFAULT_BLOCKSIZE, preseek=True,
                       encoding=None, with_ranges=False,
                       incomplete=INCOMPLETE_RAISE):
    """Returns an iterator over the lines from a file object, in
    reverse order, i.e., last line first, first line last. Uses the
    :meth:`file.seek` method of file objects, and is tested compatible with
    :class:`file` objects, as well as :class:`StringIO.StringIO`.

    Lines are located by scanning for ``\\n`` bytes, so multibyte
    characters (e.g. UTF-8) are safe even when a character straddles a
    block boundary: line content is reassembled before any decoding
    happens. A ``\\r\\n`` (CRLF) terminator is recognized as a single
    line ending and the trailing ``\\r`` is stripped from the line,
    including when the ``\\r`` and ``\\n`` fall in different blocks. A
    lone ``\\r`` inside a line is not treated as a terminator.

    The file is only read and seeked; it is never truncated or written
    to. Iteration is lazy and walks the file backwards in blocks of
    *blocksize* bytes, so taking the first few results does not scan the
    whole file.

    Args:
        file_obj (file): An open file object. Note that
            ``reverse_iter_lines`` mutably reads from the file and
            other functions should not mutably interact with the file
            object during iteration. Files can be opened in bytes or
            text mode. Seek before reusing a text stream after iteration.
        blocksize (int): The block size to pass to
          :meth:`file.read()`. Warning: keep this a fairly large
          multiple of 2, defaults to 4096. Must be a positive integer.
        preseek (bool): Tells the function whether or not to automatically
            seek to the end of the file. Defaults to ``True``.
            ``preseek=False`` is useful in cases when the
            file cursor is already in position, either at the end of
            the file or in the middle for relative reverse line
            generation.
        encoding (str): Text encoding used to decode lines. Defaults to the
            file's encoding, or returns bytes when neither is specified.
        with_ranges (bool): When ``True``, yield :class:`ReverseLine`
            objects carrying the original byte range of each line
            instead of bare lines. Defaults to ``False``.
        incomplete (str): Policy for a final line with no trailing
            newline, which may be a complete record or a half-written
            one. ``'raise'`` (the default) raises :exc:`ValueError`;
            ``'skip'`` excludes it from the results. The file is never
            modified either way.

    """
    if blocksize < 1:
        raise ValueError('blocksize expected a positive integer,'
                         ' not %r' % (blocksize,))
    if incomplete not in _INCOMPLETE_POLICIES:
        raise ValueError('incomplete expected one of %r, not %r'
                         % (_INCOMPLETE_POLICIES, incomplete))

    # This function is a bit of a pain because it attempts to be byte/text agnostic
    encoding = encoding or getattr(file_obj, 'encoding', None)

    # need orig_obj to keep alive otherwise __del__ on the TextWrapper will close the file
    orig_obj = file_obj
    try:
        file_obj = orig_obj.buffer
    except (AttributeError, io.UnsupportedOperation):
        pass

    # read(0) reveals whether the stream yields text (e.g., io.StringIO,
    # which has no .buffer to unwrap) or bytes
    empty = file_obj.read(0)
    is_text = isinstance(empty, str)
    decode = bool(encoding) and not is_text
    newline = '\n' if is_text else b'\n'
    carriage_return = '\r' if is_text else b'\r'

    if preseek:
        file_obj.seek(0, os.SEEK_END)

    cur_pos = file_obj.tell()
    if cur_pos == 0:
        return

    # The incomplete-line policy only applies to a tail at the physical
    # end of the file. With preseek=False the cursor may delimit an
    # arbitrary reverse-read window, so a chunk cut off at the cursor is
    # a window boundary, not necessarily a half-written record.
    if preseek:
        at_eof = True
    else:
        file_obj.seek(0, os.SEEK_END)
        at_eof = (cur_pos == file_obj.tell())
        file_obj.seek(cur_pos, os.SEEK_SET)

    # When skipping an unterminated tail, the scan runs over the bytes
    # preceding it. suppress_start marks the single empty segment that
    # split() places after the newline right before the removed tail;
    # that segment is a ghost of the tail, not a real blank line.
    suppress_start = None

    if at_eof:
        # A final line with no trailing newline is ambiguous: it can be a
        # complete record or a record still being appended. Locate where it
        # starts (just after the preceding newline) by walking backwards in
        # blocks, then apply the explicit incomplete-line policy. The file
        # itself is never touched beyond reads.
        file_obj.seek(cur_pos - 1, os.SEEK_SET)
        if file_obj.read(1) != newline:
            chunk_end = cur_pos
            tail_start = 0
            while chunk_end > 0:
                read_size = min(blocksize, chunk_end)
                chunk_start = chunk_end - read_size
                file_obj.seek(chunk_start, os.SEEK_SET)
                chunk = file_obj.read(read_size)
                newline_idx = chunk.rfind(newline)
                if newline_idx != -1:
                    tail_start = chunk_start + newline_idx + 1
                    break
                chunk_end = chunk_start

            if incomplete == INCOMPLETE_RAISE:
                raise ValueError(
                    'final line at byte range [%d, %d) has no trailing newline'
                    ' and may be a partially written record; pass'
                    ' incomplete="skip" to exclude it'
                    % (tail_start, cur_pos))
            # Keep everything up to and including the newline at
            # tail_start - 1, then drop the empty segment at tail_start
            # that split() would otherwise emit as the tail's ghost.
            # Genuine blank lines before that newline keep their ranges.
            # The original file is not modified.
            if tail_start == 0:
                return
            cur_pos = tail_start
            suppress_start = tail_start

    # buff holds the reassembled, not-yet-emitted tail of the scan, with
    # buff_start its absolute offset in the stream.
    buff = empty
    buff_start = cur_pos
    while cur_pos > 0:
        read_size = min(blocksize, cur_pos)
        cur_pos -= read_size
        file_obj.seek(cur_pos, os.SEEK_SET)
        buff = file_obj.read(read_size) + buff
        buff_start = cur_pos

        # split on \n only; UTF-8 continuation bytes and lone \r are
        # never line terminators, and CRLF's \r is stripped per line below
        parts = buff.split(newline)
        if cur_pos > 0 and len(parts) < 2:
            # no complete terminator inside the window yet
            continue

        starts = []
        offset = buff_start
        for part in parts:
            starts.append(offset)
            offset += len(part) + 1  # content plus its newline

        for idx in range(len(parts) - 1, -1, -1):
            if cur_pos > 0 and idx == 0:
                # parts[0] may continue into the next block downwards
                break
            part = parts[idx]
            start = starts[idx]
            if start == suppress_start:
                # empty segment left where the skipped tail began; not a
                # real blank line
                continue
            end = start + len(part)
            if part[-1:] == carriage_return:
                # CRLF terminator: keep the \r out of line and its range
                part = part[:-1]
                end -= 1
            line = part.decode(encoding) if decode else part
            if with_ranges:
                yield ReverseLine(line, start, end)
            else:
                yield line

        buff = parts[0]
        # parts[0] starts at buff_start, so buff_start is unchanged


"""
TODO: allow passthroughs for:

json.load(fp[, encoding[, cls[, object_hook[, parse_float[, parse_int[, parse_constant[, object_pairs_hook[, **kw]]]]]]]])
"""


class JSONLIterator:
    """The ``JSONLIterator`` is used to iterate over JSON-encoded objects
    stored in the `JSON Lines format`_ (one object per line).

    Most notably it has the ability to efficiently read from the
    bottom of files, making it very effective for reading in simple
    append-only JSONL use cases. It also has the ability to start from
    anywhere in the file and ignore corrupted lines.

    A final line without a trailing newline cannot be told apart from a
    record that is still being appended, so reverse iteration skips it
    (see :func:`reverse_iter_lines`'s ``incomplete`` argument). Forward
    iteration still surfaces it, where ``ignore_errors`` decides what
    happens if it does not parse.

    Args:
        file_obj (file): An open file object.
        ignore_errors (bool): Whether to skip over lines that raise an error on
            deserialization (:func:`json.loads`).
        reverse (bool): Controls the direction of the iteration.
            Defaults to ``False``. If set to ``True`` and *rel_seek*
            is unset, seeks to the end of the file before iteration
            begins.
        rel_seek (float): Used to preseek the start position of
            iteration. Set to 0.0 for the start of the file, 1.0 for the
            end, and anything in between.

    .. _JSON Lines format: http://jsonlines.org/
    """
    def __init__(self, file_obj,
                 ignore_errors=False, reverse=False, rel_seek=None):
        self._reverse = bool(reverse)
        self._file_obj = file_obj
        self.ignore_errors = ignore_errors

        if rel_seek is None:
            if reverse:
                rel_seek = 1.0
        elif not -1.0 < rel_seek <= 1.0:
            raise ValueError("'rel_seek' expected a float between"
                             " -1.0 and 1.0, not %r" % rel_seek)
        elif rel_seek < 0:
            rel_seek = 1.0 + rel_seek
        self._rel_seek = rel_seek
        self._blocksize = 4096
        if rel_seek is not None:
            self._init_rel_seek()
        if self._reverse:
            # An unterminated final line might be a half-written record,
            # so skip it rather than guess; the caller's file is untouched.
            self._line_iter = reverse_iter_lines(self._file_obj,
                                                 blocksize=self._blocksize,
                                                 preseek=False,
                                                 incomplete=INCOMPLETE_SKIP)
        else:
            # readline() (unlike next()) keeps tell() usable on text files
            self._line_iter = iter(self._file_obj.readline,
                                   self._file_obj.read(0))

    @property
    def cur_byte_pos(self):
        "A property representing where in the file the iterator is reading."
        return self._file_obj.tell()

    def _align_to_newline(self):
        "Aligns the file object's position to the next newline."
        fo, bsize = self._file_obj, self._blocksize
        newline = b'\n' if isinstance(fo.read(0), bytes) else '\n'
        cur, total_read = newline[:0], 0
        cur_pos = fo.tell()
        while newline not in cur:
            cur = fo.read(bsize)
            if not cur:
                # no newline until EOF; a partial trailing line was
                # never yieldable from a mid-line seek anyway
                fo.seek(0, os.SEEK_END)
                return
            total_read += bsize
        newline_offset = cur.index(newline) + total_read - bsize
        fo.seek(cur_pos + newline_offset)

    def _init_rel_seek(self):
        "Sets the file object's position to the relative location set above."
        rs, fo = self._rel_seek, self._file_obj
        if rs == 0.0:
            fo.seek(0, os.SEEK_SET)
        else:
            fo.seek(0, os.SEEK_END)
            size = fo.tell()
            if rs == 1.0:
                self._cur_pos = size
            else:
                target = int(size * rs)
                fo.seek(target, os.SEEK_SET)
                self._align_to_newline()
                self._cur_pos = fo.tell()

    def __iter__(self):
        return self

    def next(self):
        """Yields one :class:`dict` loaded with :func:`json.loads`, advancing
        the file object by one line. Raises :exc:`StopIteration` upon reaching
        the end of the file (or beginning, if ``reverse`` was set to ``True``.
        """
        while 1:
            line = next(self._line_iter).lstrip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                if not self.ignore_errors:
                    raise
                continue
            return obj

    __next__ = next


if __name__ == '__main__':
    def _main():
        import sys
        if '-h' in sys.argv or '--help' in sys.argv:
            print('loads one or more JSON Line files for basic validation.')
            return
        verbose = False
        if '-v' in sys.argv or '--verbose' in sys.argv:
            verbose = True
        file_count, obj_count = 0, 0
        filenames = sys.argv[1:]
        for filename in filenames:
            if filename in ('-h', '--help', '-v', '--verbose'):
                continue
            file_count += 1
            with open(filename, 'rb') as file_obj:
                iterator = JSONLIterator(file_obj)
                cur_obj_count = 0
                while 1:
                    try:
                        next(iterator)
                    except ValueError:
                        print('error reading object #%s around byte %s in %s'
                              % (cur_obj_count + 1, iterator.cur_byte_pos, filename))
                        return
                    except StopIteration:
                        break
                    obj_count += 1
                    cur_obj_count += 1
                    if verbose and obj_count and obj_count % 100 == 0:
                        sys.stdout.write('.')
                        if obj_count % 10000:
                            sys.stdout.write('%s\n' % obj_count)
        if verbose:
            print('files checked: %s' % file_count)
            print('objects loaded: %s' % obj_count)
        return

    _main()
