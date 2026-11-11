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
import time
from collections import namedtuple, deque


DEFAULT_BLOCKSIZE = 4096


__all__ = ['JSONLIterator', 'JSONLFileFollower', 'JSONLFollowEvent',
           'JSONLFollowEventType', 'JSONLFollowPolicy',
           'reverse_iter_lines']


# returned by _decode_json_line for blank lines and, when errors are
# ignored, malformed lines; distinct from a decoded JSON null (None)
_BLANK = object()

# returned by JSONLFileFollower._read_record at true EOF, so callers
# can tell it apart from a retained, not-yet-complete trailing line
_EOF = object()


def _decode_json_line(line, ignore_errors):
    """Strips and deserializes one JSONL line. Returns ``_BLANK`` for
    empty lines and, when *ignore_errors* is set, unparseable lines."""
    line = line.lstrip()
    if not line:
        return _BLANK
    try:
        return json.loads(line)
    except Exception:
        if not ignore_errors:
            raise
        return _BLANK


def reverse_iter_lines(file_obj, blocksize=DEFAULT_BLOCKSIZE, preseek=True, encoding=None):
    """Returns an iterator over the lines from a file object, in
    reverse order, i.e., last line first, first line last. Uses the
    :meth:`file.seek` method of file objects, and is tested compatible with
    :class:`file` objects, as well as :class:`StringIO.StringIO`.

    Args:
        file_obj (file): An open file object. Note that
            ``reverse_iter_lines`` mutably reads from the file and
            other functions should not mutably interact with the file
            object during iteration. Files can be opened in bytes or
            text mode. Seek before reusing a text stream after iteration.
        blocksize (int): The block size to pass to
          :meth:`file.read()`. Warning: keep this a fairly large
          multiple of 2, defaults to 4096.
        preseek (bool): Tells the function whether or not to automatically
            seek to the end of the file. Defaults to ``True``.
            ``preseek=False`` is useful in cases when the
            file cursor is already in position, either at the end of
            the file or in the middle for relative reverse line
            generation.
        encoding (str): Text encoding used to decode lines. Defaults to the
            file's encoding, or returns bytes when neither is specified.

    """
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
    newline = '\n' if is_text else b'\n'
    decode = bool(encoding) and not is_text
    empty_line = '' if (decode or is_text) else b''

    if preseek:
        file_obj.seek(0, os.SEEK_END)
    buff = empty
    cur_pos = file_obj.tell()
    while 0 < cur_pos:
        read_size = min(blocksize, cur_pos)
        cur_pos -= read_size
        file_obj.seek(cur_pos, os.SEEK_SET)
        cur = file_obj.read(read_size)
        buff = cur + buff
        lines = buff.splitlines()

        # until the start of the file, the first line may be partial
        if cur_pos and (len(lines) < 2 or lines[0] == empty):
            continue
        if buff[-1:] == newline:
            yield empty_line
        for line in reversed(lines[1:] if cur_pos else lines):
            yield line.decode(encoding) if decode else line
        buff = lines[0] if cur_pos else empty
    if buff:
        yield buff.decode(encoding) if decode else buff



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
            self._line_iter = reverse_iter_lines(self._file_obj,
                                                 blocksize=self._blocksize,
                                                 preseek=False)
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
            line = next(self._line_iter)
            obj = _decode_json_line(line, self.ignore_errors)
            if obj is _BLANK:
                continue
            return obj

    __next__ = next


class JSONLFollowEventType:
    """Event types emitted by :class:`JSONLFileFollower`.

    Attributes:
        RECORD: One complete JSONL record was read. The decoded object
            is available on :attr:`JSONLFollowEvent.obj`.
        FILE_REPLACED: The path now points at a different file (a new
            device/inode pair), i.e., a rotation or rename-over.
        FILE_TRUNCATED: The same file was shortened below the current
            read position, as with logrotate's copytruncate. A
            same-inode rewrite that keeps the file at least as long is
            not distinguishable from normal appends.
    """
    RECORD = 'record'
    FILE_REPLACED = 'file_replaced'
    FILE_TRUNCATED = 'file_truncated'


class JSONLFollowPolicy:
    """Policies controlling how :class:`JSONLFileFollower` switches
    over when the followed path is replaced or truncated.

    Attributes:
        NEW_ONLY: Switch immediately and seek to the end of the new
            file, so only records written after the switch are
            delivered. For truncation, the existing contents are
            skipped and only future appends are read.
        FROM_START: Switch immediately and read the new file from its
            first byte. For truncation, the file is reread from the
            start.
        CONTINUE: Only meaningful for replacement. The old file is
            kept open and drained to EOF first, with its records still
            tagged with the old generation; only then is the new file
            opened. Falls back to :attr:`NEW_ONLY` for truncation,
            where there is no old file to drain. Note that an old file
            ending in an unfinished, never-completed tail line delays
            the switch until that line completes or the follower is
            closed.
    """
    NEW_ONLY = 'new_only'
    FROM_START = 'from_start'
    CONTINUE = 'continue'


_ALL_FOLLOW_POLICIES = frozenset((JSONLFollowPolicy.NEW_ONLY,
                                  JSONLFollowPolicy.FROM_START,
                                  JSONLFollowPolicy.CONTINUE))


class JSONLFollowEvent(namedtuple('JSONLFollowEvent',
                                  'type generation obj path')):
    """An event yielded by :class:`JSONLFileFollower`.

    Attributes:
        type (str): One of the :class:`JSONLFollowEventType` constants.
        generation (int): Monotonic generation number of the file the
            event belongs to. Starts at 0 and increments on every
            replacement or truncation, so every record can be traced
            back to the file generation it was read from.
        obj: The decoded JSON object for ``RECORD`` events, otherwise
            ``None``.
        path (str): The followed path.
    """
    __slots__ = ()

    @property
    def is_record(self):
        "``True`` when this event carries a decoded JSONL record."
        return self.type == JSONLFollowEventType.RECORD


class JSONLFileFollower:
    """Follows a JSONL file at *path* that is appended to and rotated,
    like ``tail -f`` for JSON Lines.

    The follower yields :class:`JSONLFollowEvent` objects. Complete
    lines are decoded and delivered exactly once as ``RECORD`` events;
    an unfinished trailing line is retained at its byte offset and
    only delivered after the line is completed. Replacement (the path
    starts pointing at a different inode) and truncation are reported
    as dedicated events, after which the configured policy decides how
    to switch. Every event carries a ``generation`` identifying which
    incarnation of the path it came from.

    Blocking iteration sleeps for *poll_interval* seconds between
    checks instead of busy-looping, so an idle follower does not pin a
    CPU. :meth:`poll` does one non-blocking check and returns ``None``
    when nothing new is available.

    Args:
        path (str): Path to the JSONL file. It does not have to exist
            yet; the follower waits for it to appear.
        poll_interval (float): Seconds to sleep between change checks
            during blocking iteration.
        ignore_errors (bool): Skip lines that fail JSON
            deserialization instead of raising.
        policy (str): Default switch policy, one of
            :class:`JSONLFollowPolicy`.
        truncate_policy (str): Overrides *policy* for truncation
            events. Defaults to *policy*. :attr:`JSONLFollowPolicy.CONTINUE`
            is not applicable to truncation and falls back to
            :attr:`JSONLFollowPolicy.NEW_ONLY`.
        start_at_end (bool): When ``True`` (the default), existing
            contents of the first file are skipped, like ``tail -f``;
            set to ``False`` to read the initial file from the start.
        encoding (str): Encoding used to decode the bytes read from
            the file. Defaults to UTF-8.
    """
    def __init__(self, path, poll_interval=1.0, *,
                 ignore_errors=False,
                 policy=JSONLFollowPolicy.NEW_ONLY,
                 truncate_policy=None,
                 start_at_end=True,
                 encoding='utf-8'):
        if policy not in _ALL_FOLLOW_POLICIES:
            raise ValueError("'policy' expected one of %r, not %r"
                             % (sorted(_ALL_FOLLOW_POLICIES), policy))
        if truncate_policy is None:
            truncate_policy = policy
        elif truncate_policy not in _ALL_FOLLOW_POLICIES:
            raise ValueError("'truncate_policy' expected one of %r, not %r"
                             % (sorted(_ALL_FOLLOW_POLICIES),
                                truncate_policy))
        if truncate_policy == JSONLFollowPolicy.CONTINUE:
            # truncation leaves no old file to drain
            truncate_policy = JSONLFollowPolicy.NEW_ONLY
        self.path = path
        self.poll_interval = poll_interval
        self.ignore_errors = ignore_errors
        self.policy = policy
        self.truncate_policy = truncate_policy
        self.start_at_end = start_at_end
        self.encoding = encoding

        self._stream = None
        self._identity = None
        self._generation = -1
        # (stream, identity, generation) tuples kept open under the
        # CONTINUE policy until drained, oldest first
        self._retained = deque()
        self._closed = False

    @property
    def generation(self):
        "Generation of the records currently being delivered."
        if self._retained:
            return self._retained[0][2]
        return self._generation

    @property
    def cur_byte_pos(self):
        "Byte position in the currently read file, or ``None`` if idle."
        if self._retained:
            return self._retained[0][0].tell()
        if self._stream is None:
            return None
        return self._stream.tell()

    def _stat_identity(self):
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return None, None
        return (stat.st_dev, stat.st_ino), stat.st_size

    def _open_stream(self, seek_end):
        try:
            stream = open(self.path, 'rb')
        except FileNotFoundError:
            # the path vanished between the stat and the open
            return None, None
        if seek_end:
            stream.seek(0, os.SEEK_END)
        identity, _ = self._stat_identity()
        if identity is None:
            stream.close()
            return None, None
        return stream, identity

    def _read_record(self, stream, generation):
        """Reads one complete JSONL line from *stream*. An unfinished
        trailing line is rewound and left for a later poll. Returns
        ``_EOF`` at a true end of file, ``None`` while holding a
        partial line or skipping a blank/error line, or an event."""
        pos = stream.tell()
        line = stream.readline()
        if not line:
            return _EOF
        if not line.endswith(b'\n'):
            # retain the unfinished tail, byte-for-byte, until it completes
            stream.seek(pos, os.SEEK_SET)
            return None
        obj = _decode_json_line(line.decode(self.encoding),
                                self.ignore_errors)
        if obj is _BLANK:
            return None
        return JSONLFollowEvent(JSONLFollowEventType.RECORD,
                                generation, obj, self.path)

    def _drain_retained(self):
        # Returns a record from a retained, not-yet-EOF old file, if
        # any. A retained file with an unfinished tail stays open and
        # is retried on the next poll instead of being closed early.
        while self._retained:
            stream, _identity, generation = self._retained[0]
            result = self._read_record(stream, generation)
            if result is _EOF:
                stream.close()
                self._retained.popleft()
                continue
            return result
        return None

    def _activate_new_file(self, seek_end, generation):
        stream, identity = self._open_stream(seek_end)
        if stream is None:
            return False
        self._stream = stream
        self._identity = identity
        self._generation = generation
        return True

    def _handle_replacement(self):
        # The path points at a different inode (or reappeared after
        # being removed). Returns the switch event, possibly after
        # retaining the old stream under CONTINUE. Returns None when
        # the new file cannot be opened yet, so the next poll retries.
        generation = self._generation + 1
        if self.policy == JSONLFollowPolicy.CONTINUE and self._stream is not None:
            self._retained.append((self._stream, self._identity,
                                   self._generation))
            self._stream = None
            self._identity = None
            return None
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        seek_end = self.policy == JSONLFollowPolicy.NEW_ONLY
        if not self._activate_new_file(seek_end, generation):
            return None
        return JSONLFollowEvent(JSONLFollowEventType.FILE_REPLACED,
                                generation, None, self.path)

    def _handle_missing(self):
        # The path is gone. Under CONTINUE the old fd stays readable;
        # otherwise drop it and wait for the path to come back.
        if self._stream is None:
            return None
        if self.policy == JSONLFollowPolicy.CONTINUE:
            self._retained.append((self._stream, self._identity,
                                   self._generation))
        else:
            self._stream.close()
        self._stream = None
        self._identity = None
        return None

    def _handle_truncation(self):
        # The same inode shrank. A buffered reader keeps serving its
        # pre-truncation buffer after seek(0) (a seek inside the buffer
        # never touches disk), so reopen the path for a clean view of
        # the new contents. Generation increments either way.
        generation = self._generation + 1
        self._stream.close()
        self._stream = None
        seek_end = self.truncate_policy != JSONLFollowPolicy.FROM_START
        stream, identity = self._open_stream(seek_end)
        if stream is None:
            self._identity = None
            return None
        self._stream = stream
        self._identity = identity
        self._generation = generation
        return JSONLFollowEvent(JSONLFollowEventType.FILE_TRUNCATED,
                                generation, None, self.path)

    def poll(self):
        """Performs one non-blocking check and returns the next
        :class:`JSONLFollowEvent`, or ``None`` if nothing is available
        right now. Never sleeps."""
        if self._closed:
            return None

        event = self._drain_retained()
        if event is not None:
            return event

        if self._stream is None:
            identity, _size = self._stat_identity()
            if identity is None:
                if self._generation == -1:
                    return None  # initial file has not appeared yet
                return self._handle_missing()
            if self._generation == -1:
                if not self._activate_new_file(self.start_at_end, 0):
                    return None
            else:
                event = self._handle_replacement()
                if event is not None:
                    return event
                if self._stream is None:
                    return None

        identity, size = self._stat_identity()
        if identity is None:
            return self._handle_missing()
        if identity != self._identity:
            event = self._handle_replacement()
            if event is not None:
                return event
            if self._stream is None:
                return None
        elif size < self._stream.tell():
            return self._handle_truncation()

        result = self._read_record(self._stream, self._generation)
        return None if result is _EOF else result

    def close(self):
        "Closes every open file handle and stops iteration."
        self._closed = True
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        for stream, _identity, _generation in self._retained:
            stream.close()
        self._retained.clear()

    def __iter__(self):
        return self

    def __next__(self):
        while not self._closed:
            event = self.poll()
            if event is not None:
                return event
            time.sleep(self.poll_interval)
        raise StopIteration

    next = __next__

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


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
