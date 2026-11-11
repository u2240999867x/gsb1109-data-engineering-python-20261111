import os
import json
from io import BytesIO, StringIO
from itertools import islice

import pytest

from boltons.jsonutils import (JSONLIterator,
                               DEFAULT_BLOCKSIZE,
                               ReverseLine,
                               INCOMPLETE_RAISE,
                               INCOMPLETE_SKIP,
                               reverse_iter_lines)

CUR_PATH = os.path.dirname(os.path.abspath(__file__))
NEWLINES_DATA_PATH = CUR_PATH + '/newlines_test_data.txt'
JSONL_DATA_PATH = CUR_PATH + '/jsonl_test_data.txt'


def _test_reverse_iter_lines(filename, blocksize=DEFAULT_BLOCKSIZE):
    fo = open(filename)
    reference = fo.read()
    fo.seek(0, os.SEEK_SET)
    rev_lines = list(reverse_iter_lines(fo, blocksize))
    assert '\n'.join(rev_lines[::-1]) == reference


def _test_reverse_iter_lines_bytes(filename, blocksize=DEFAULT_BLOCKSIZE):
    fo = open(filename, 'rb')
    reference = fo.read()
    fo.seek(0, os.SEEK_SET)
    rev_lines = list(reverse_iter_lines(fo, blocksize))
    assert os.linesep.encode('ascii').join(rev_lines[::-1]) == reference



def test_reverse_iter_lines():
    for blocksize in (2, 4, 16, 4096):
        _test_reverse_iter_lines(NEWLINES_DATA_PATH, blocksize)
        _test_reverse_iter_lines_bytes(NEWLINES_DATA_PATH, blocksize)


def test_jsonl_iterator():
    ref = [{'4': 4}, {'3': 3}, {'2': 2}, {'1': 1}, {}]
    jsonl_iter = JSONLIterator(open(JSONL_DATA_PATH), reverse=True)
    jsonl_list = list(jsonl_iter)
    assert jsonl_list == ref


class _CappedReadFile:
    """Wraps a file object, raising if read() is called more than
    max_reads times, so a non-terminating read loop fails fast
    instead of hanging the test suite."""
    def __init__(self, fo, max_reads=100):
        self._fo = fo
        self._reads = 0
        self._max_reads = max_reads

    def read(self, size=-1):
        self._reads += 1
        if self._reads > self._max_reads:
            raise RuntimeError('read() called more than %s times,'
                               ' likely an infinite loop' % self._max_reads)
        return self._fo.read(size)

    def __iter__(self):
        return iter(self._fo)

    def __getattr__(self, name):
        return getattr(self._fo, name)


def test_jsonl_iterator_mid_last_line_seek_terminates(tmp_path):
    # a rel_seek landing mid-final-line of a file with no trailing
    # newline used to loop forever in _align_to_newline
    path = tmp_path / 'no_trailing_newline.jsonl'
    path.write_text('{"1": 1}\n{"2": 2}')
    fo = _CappedReadFile(open(str(path)))
    jsonl_iter = JSONLIterator(fo, rel_seek=0.9)
    assert list(jsonl_iter) == []


def test_jsonl_iterator_rel_seek_negative():
    # negative rel_seek used to normalize to >1.0, seeking past EOF
    # and looping forever in _align_to_newline
    ref = list(JSONLIterator(open(JSONL_DATA_PATH)))
    fo = _CappedReadFile(open(JSONL_DATA_PATH))
    tail = list(JSONLIterator(fo, rel_seek=-0.5))
    assert tail
    assert tail == list(JSONLIterator(open(JSONL_DATA_PATH), rel_seek=0.5))
    assert tail == ref[len(ref) - len(tail):]


def test_jsonl_explicit_end_seek(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n{"n": 2}\n')
    with path.open('rb', buffering=0) as stream:
        assert list(JSONLIterator(stream, reverse=True, rel_seek=1.0)) == [{'n': 2}, {'n': 1}]
    with path.open() as stream:
        assert list(JSONLIterator(stream, rel_seek=1.0)) == []


def test_jsonl_relative_seek_binary_matches_text(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'{"n": 1}\n{"n": 2}\n{"n": 3}\n')
    with path.open('rb') as binary, path.open() as text:
        assert list(JSONLIterator(binary, rel_seek=0.4)) == list(JSONLIterator(text, rel_seek=0.4))


def test_reverse_iter_lines_uses_file_encoding(tmp_path):
    path = tmp_path / 'latin1.txt'
    path.write_bytes('café\nfin\n'.encode('latin-1'))
    with path.open(encoding='latin-1') as stream:
        assert (list(reverse_iter_lines(stream))
                == ['', 'fin', 'café'])


def test_reverse_iter_lines_uses_explicit_binary_encoding():
    stream = BytesIO('café\nfin\n'.encode('latin-1'))
    assert (list(reverse_iter_lines(stream, encoding='latin-1'))
            == ['', 'fin', 'café'])


def test_reverse_iter_lines_keeps_text_stream_attached(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('first\nsecond\n')
    with path.open() as stream:
        assert list(reverse_iter_lines(stream)) == ['', 'second', 'first']
        stream.seek(0)
        assert stream.read() == 'first\nsecond\n'


def test_reverse_iter_lines_leading_empty_lines():
    from io import BytesIO
    data = b'\n\nfirst\nsecond\n'
    for blocksize in (2, 4, 4096):
        rev = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize))
        assert rev == [b'', b'second', b'first', b'', b'']
        assert b'\n'.join(reversed(rev)) == data
    assert list(reverse_iter_lines(BytesIO(b'\n'))) == [b'', b'']


def test_reverse_iter_lines_stringio():
    assert list(reverse_iter_lines(StringIO('a\nb\n'))) == ['', 'b', 'a']
    # an unterminated final line needs an explicit policy
    with pytest.raises(ValueError):
        list(reverse_iter_lines(StringIO('x\ny'), blocksize=2))
    assert (list(reverse_iter_lines(StringIO('x\ny'), blocksize=2,
                                    incomplete=INCOMPLETE_SKIP))
            == ['x'])


def test_jsonl_iterator_text_cur_byte_pos(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'{"1": 1}\n{"2": 2}\n')  # no CRLF translation on Windows
    with path.open() as stream:
        jsonl_iter = JSONLIterator(stream)
        assert next(jsonl_iter) == {'1': 1}
        assert jsonl_iter.cur_byte_pos == len('{"1": 1}\n')
        assert list(jsonl_iter) == [{'2': 2}]


class _RecordingStream:
    """Delegating stream that records reads/seeks and refuses any
    mutation, proving reverse iteration never writes or truncates."""
    def __init__(self, stream):
        self._stream = stream
        self.read_sizes = []
        self.seek_targets = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self._stream.read(size)

    def seek(self, target, whence=os.SEEK_SET):
        self.seek_targets.append((target, whence))
        return self._stream.seek(target, whence)

    def write(self, *args, **kwargs):
        raise AssertionError('reverse_iter_lines must not write')

    def truncate(self, *args, **kwargs):
        raise AssertionError('reverse_iter_lines must not truncate')

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _all_blocksizes(data):
    return range(1, len(data) + 1)


def test_reverse_iter_lines_ranges_slice_back_to_bytes():
    data = b'{"a": 1}\n{"b": 2}\n'
    for blocksize in (1, 2, 3, 7, 4096):
        results = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize,
                                          with_ranges=True))
        assert all(isinstance(item, ReverseLine) for item in results)
        assert [item.line for item in results] == [
            b'', b'{"b": 2}', b'{"a": 1}']
        for item in results:
            assert isinstance(item.start, int) and isinstance(item.end, int)
            assert item.start <= item.end
            # the reported range reproduces the line from the raw bytes
            assert data[item.start:item.end] == item.line
    beta = next(item for item in results if item.line == b'{"b": 2}')
    assert (beta.start, beta.end) == (9, 17)


def test_reverse_iter_lines_crlf_across_block_boundaries():
    data = b'alpha\r\nbeta\r\ngamma\r\n'
    expected = [b'', b'gamma', b'beta', b'alpha']
    for blocksize in _all_blocksizes(data):
        results = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize,
                                          with_ranges=True))
        assert [item.line for item in results] == expected
        for item in results:
            assert not item.line.endswith(b'\r')
            assert data[item.start:item.end] == item.line
    beta = next(item for item in results if item.line == b'beta')
    assert data[beta.start - 2:beta.start] == b'\r\n'


def test_reverse_iter_lines_crlf_unterminated_tail_skip_keeps_ranges():
    data = b'alpha\r\nbeta\r\ngamma'
    results = list(reverse_iter_lines(BytesIO(data), blocksize=3,
                                      with_ranges=True,
                                      incomplete=INCOMPLETE_SKIP))
    assert [item.line for item in results] == [b'beta', b'alpha']
    for item in results:
        assert data[item.start:item.end] == item.line
    # the kept lines match the terminated-only prefix (ignoring its
    # trailing blank line) and keep identical byte ranges
    prefix = data[:data.rindex(b'gamma')]  # alpha\r\nbeta\r\n
    prefix_results = [item for item in
                      reverse_iter_lines(BytesIO(prefix), blocksize=3,
                                         with_ranges=True)
                      if item.line]
    assert prefix_results == results


def test_reverse_iter_lines_lone_carriage_return_is_content():
    # JSONL only recognizes \n and \r\n; a lone \r must stay in the line
    data = b'{"a": 1}\rX\n{"b": 2}\n'
    for blocksize in (1, 2, 3, 5, 4096):
        results = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize,
                                          with_ranges=True))
        assert [item.line for item in results] == [
            b'', b'{"b": 2}', b'{"a": 1}\rX']
        for item in results:
            assert data[item.start:item.end] == item.line


def test_reverse_iter_lines_multibyte_straddles_blocks():
    line_one = '{"v": "café ☃"}\n'   # 3-byte snowman
    line_two = '{"v": "\U0001f600 中"}\n'  # 4-byte emoji + CJK
    data = (line_one + line_two).encode('utf-8')
    for blocksize in range(1, 8):
        results = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize,
                                          encoding='utf-8',
                                          with_ranges=True))
        assert [item.line for item in results] == [
            '', line_two.rstrip('\n'), line_one.rstrip('\n')]
        for item in results:
            # ranges stay byte-accurate even when a character is split
            # across two blocks
            assert data[item.start:item.end].decode('utf-8') == item.line


def test_reverse_iter_lines_stringio_ranges_are_char_positions():
    text = 'a☃\nb\n'
    results = list(reverse_iter_lines(StringIO(text), blocksize=2,
                                      with_ranges=True))
    assert [item.line for item in results] == ['', 'b', 'a☃']
    for item in results:
        assert text[item.start:item.end] == item.line


def test_reverse_iter_lines_incomplete_policy_raises():
    data = b'{"a": 1}\n{"half":'
    # raising is the explicit policy and also the default
    for policy in (INCOMPLETE_RAISE, None):
        kwargs = {} if policy is None else {'incomplete': policy}
        with pytest.raises(ValueError) as exc_info:
            list(reverse_iter_lines(BytesIO(data), **kwargs))
        message = str(exc_info.value)
        assert '9' in message and '17' in message  # [9, 17) tail range
    with pytest.raises(ValueError):
        list(reverse_iter_lines(BytesIO(b'lonely')))


def test_reverse_iter_lines_incomplete_policy_skips():
    data = b'{"a": 1}\n{"half":'
    assert (list(reverse_iter_lines(BytesIO(data),
                                    incomplete=INCOMPLETE_SKIP))
            == [b'{"a": 1}'])
    # a file that is only an unterminated line yields nothing
    assert list(reverse_iter_lines(BytesIO(b'lonely'),
                                   incomplete=INCOMPLETE_SKIP)) == []
    # terminated files behave identically under both policies
    terminated = b'{"a": 1}\n{"b": 2}\n'
    assert (list(reverse_iter_lines(BytesIO(terminated)))
            == list(reverse_iter_lines(BytesIO(terminated),
                                       incomplete=INCOMPLETE_SKIP)))


def test_reverse_iter_lines_skip_keeps_blank_lines_before_tail():
    # the newline immediately preceding the dropped tail must not erase a
    # genuine blank line that precedes it
    cases = [
        (b'\npartial', [ReverseLine(b'', 0, 0)]),
        (b'a\n\npartial',
         [ReverseLine(b'', 2, 2), ReverseLine(b'a', 0, 1)]),
        (b'\n\npartial',
         [ReverseLine(b'', 1, 1), ReverseLine(b'', 0, 0)]),
    ]
    for data, expected in cases:
        for blocksize in (1, 2, 3, 4096):
            got = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize,
                                          incomplete=INCOMPLETE_SKIP,
                                          with_ranges=True))
            assert got == expected, (data, blocksize, got)
            for item in got:
                assert data[item.start:item.end] == item.line
    # same guarantee with CRLF terminators straddling block boundaries
    crlf = b'a\r\n\r\npartial'
    expected = [ReverseLine(b'', 3, 3), ReverseLine(b'a', 0, 1)]
    for blocksize in (1, 2, 3, 4096):
        got = list(reverse_iter_lines(BytesIO(crlf), blocksize=blocksize,
                                      incomplete=INCOMPLETE_SKIP,
                                      with_ranges=True))
        assert got == expected, (blocksize, got)


def test_reverse_iter_lines_invalid_arguments():
    with pytest.raises(ValueError):
        list(reverse_iter_lines(BytesIO(b'a\n'), blocksize=0))
    with pytest.raises(ValueError):
        list(reverse_iter_lines(BytesIO(b'a\n'), blocksize=-2))
    with pytest.raises(ValueError):
        list(reverse_iter_lines(BytesIO(b'a\n'), incomplete='guess'))


def test_reverse_iter_lines_empty_and_blank_lines():
    assert list(reverse_iter_lines(BytesIO(b''))) == []
    assert list(reverse_iter_lines(BytesIO(b'\n'))) == [b'', b'']
    assert list(reverse_iter_lines(BytesIO(b'\n\n'))) == [b'', b'', b'']
    data = b'\n\nfirst\nsecond\n'
    for blocksize in (1, 2, 4, 4096):
        rev = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize))
        assert rev == [b'', b'second', b'first', b'', b'']
        assert b'\n'.join(reversed(rev)) == data


def test_reverse_iter_lines_never_modifies_the_file(tmp_path):
    path = tmp_path / 'records.jsonl'
    data = b'{"a": 1}\n{"b": 2}\n{"half":'
    path.write_bytes(data)

    recorder = _RecordingStream(BytesIO(data))
    with pytest.raises(ValueError):
        list(reverse_iter_lines(recorder))
    assert recorder.read_sizes  # it did read
    assert not any(
        whence == os.SEEK_END and target < 0
        for target, whence in recorder.seek_targets
    )

    recorder = _RecordingStream(BytesIO(data))
    list(reverse_iter_lines(recorder, incomplete=INCOMPLETE_SKIP))
    assert path.read_bytes() == data  # on-disk file untouched


def test_reverse_iter_lines_taking_three_does_not_scan_whole_file():
    content = b'{"n": %4d}'  # space-padded so every record is 11 bytes
    data = b''.join(content % index + b'\n'
                    for index in range(100))  # 1200 bytes
    blocksize = 64
    recorder = _RecordingStream(BytesIO(data))

    first_three = list(islice(
        (item for item in reverse_iter_lines(recorder, blocksize=blocksize)
         if item), 3))

    assert first_three == [content % 99, content % 98, content % 97]
    # a couple of blocks at most: nowhere near a full traversal
    assert len(recorder.read_sizes) <= 4
    # every absolute seek stayed in the tail of the 1200-byte file
    set_targets = [target for target, whence in recorder.seek_targets
                   if whence == os.SEEK_SET]
    assert min(set_targets) > 900

    full_recorder = _RecordingStream(BytesIO(data))
    list(reverse_iter_lines(full_recorder, blocksize=blocksize))
    assert len(full_recorder.read_sizes) > len(recorder.read_sizes)


def test_reverse_iter_lines_preseek_mid_window_ignores_tail_policy():
    # the unterminated EOF tail must not affect a window ending before it
    data = b'aaa\nbbb\nccc\nddd'
    stream = BytesIO(data)
    stream.seek(11)  # newline boundary between ccc and ddd
    assert list(reverse_iter_lines(stream, blocksize=4,
                                   preseek=False)) == [b'ccc', b'bbb', b'aaa']
    # a window that reaches physical EOF still applies the policy
    stream = BytesIO(data)
    stream.seek(0, os.SEEK_END)
    with pytest.raises(ValueError):
        list(reverse_iter_lines(stream, blocksize=4, preseek=False))


def test_jsonl_iterator_reverse_skips_unterminated_tail(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'{"1": 1}\n{"2": 2}\n{"partial":')
    with path.open('rb') as stream:
        assert list(JSONLIterator(stream, reverse=True)) == [
            {'2': 2}, {'1': 1}]
    # forward iteration still surfaces the tail for json.loads to judge
    with path.open('rb') as stream:
        with pytest.raises(ValueError):
            list(JSONLIterator(stream))
    with path.open('rb') as stream:
        assert list(JSONLIterator(stream, ignore_errors=True)) == [
            {'1': 1}, {'2': 2}]


def test_jsonl_iterator_reverse_tail_is_lazy(tmp_path):
    path = tmp_path / 'big.jsonl'
    # 120000 bytes, comfortably larger than the iterator's 4096 block size
    data = b''.join(b'{"n": %4d}\n' % index for index in range(10000))
    path.write_bytes(data)
    recorder = _RecordingStream(open(str(path), 'rb'))
    try:
        records = list(islice(JSONLIterator(recorder, reverse=True), 3))
        assert records == [{'n': 9999}, {'n': 9998}, {'n': 9997}]
        # only the tail blocks were touched, never a full traversal
        assert len(recorder.read_sizes) <= 4
        set_targets = [target for target, whence in recorder.seek_targets
                       if whence == os.SEEK_SET]
        assert min(set_targets) > 100000
    finally:
        recorder._stream.close()


def test_jsonl_reverse_roundtrips_through_json(tmp_path):
    path = tmp_path / 'unicode.jsonl'
    records = [{'v': 'café'}, {'v': '中文'}, {'v': '\U0001f600'}]
    path.write_text(''.join(json.dumps(record) + '\n' for record in records),
                    encoding='utf-8')
    with path.open(encoding='utf-8') as stream:
        assert list(JSONLIterator(stream, reverse=True)) == list(
            reversed(records))
