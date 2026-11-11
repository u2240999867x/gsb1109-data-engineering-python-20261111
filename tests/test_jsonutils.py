import os

import pytest

from boltons.jsonutils import (JSONLIterator,
                               JSONLFileFollower,
                               JSONLFollowEvent,
                               JSONLFollowEventType,
                               JSONLFollowPolicy,
                               DEFAULT_BLOCKSIZE,
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
    path.write_bytes('café\nfin'.encode('latin-1'))
    with path.open(encoding='latin-1') as stream:
        assert list(reverse_iter_lines(stream)) == ['fin', 'café']


def test_reverse_iter_lines_uses_explicit_binary_encoding():
    from io import BytesIO

    stream = BytesIO('café\nfin'.encode('latin-1'))
    assert list(reverse_iter_lines(stream, encoding='latin-1')) == ['fin', 'café']


def test_reverse_iter_lines_keeps_text_stream_attached(tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('first\nsecond')
    with path.open() as stream:
        assert list(reverse_iter_lines(stream)) == ['second', 'first']
        stream.seek(0)
        assert stream.read() == 'first\nsecond'


def test_reverse_iter_lines_leading_empty_lines():
    from io import BytesIO
    data = b'\n\nfirst\nsecond\n'
    for blocksize in (2, 4, 4096):
        rev = list(reverse_iter_lines(BytesIO(data), blocksize=blocksize))
        assert rev == [b'', b'second', b'first', b'', b'']
        assert b'\n'.join(reversed(rev)) == data
    assert list(reverse_iter_lines(BytesIO(b'\n'))) == [b'', b'']


def test_reverse_iter_lines_stringio():
    from io import StringIO
    assert list(reverse_iter_lines(StringIO('a\nb\n'))) == ['', 'b', 'a']
    assert list(reverse_iter_lines(StringIO('x\ny'), blocksize=2)) == ['y', 'x']


def test_jsonl_iterator_text_cur_byte_pos(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'{"1": 1}\n{"2": 2}\n')  # no CRLF translation on Windows
    with path.open() as stream:
        jsonl_iter = JSONLIterator(stream)
        assert next(jsonl_iter) == {'1': 1}
        assert jsonl_iter.cur_byte_pos == len('{"1": 1}\n')
        assert list(jsonl_iter) == [{'2': 2}]


def _rotate(path, content):
    "Replaces the path atomically, like a real log rotation rename-over."
    new_path = path.with_name(path.name + '.rotated')
    new_path.write_text(content)
    os.replace(str(new_path), str(path))


def _drain_polls(follower, max_polls=50):
    "Returns every event currently available without blocking."
    events = []
    for _ in range(max_polls):
        event = follower.poll()
        if event is None:
            break
        events.append(event)
    return events


def test_follower_waits_for_missing_then_appears(tmp_path):
    path = tmp_path / 'not_there_yet.jsonl'
    follower = JSONLFileFollower(str(path))
    assert follower.generation == -1
    assert follower.cur_byte_pos is None
    assert follower.poll() is None

    path.write_text('{"n": 1}\n')
    event = follower.poll()  # start_at_end skips the pre-existing line
    assert event is None

    with path.open('a') as stream:
        stream.write('{"n": 2}\n')
    event = follower.poll()
    assert event.is_record
    assert event == JSONLFollowEvent(JSONLFollowEventType.RECORD,
                                     0, {'n': 2}, str(path))
    assert follower.generation == 0
    follower.close()
    assert follower.poll() is None


def test_follower_reads_initial_file_from_start(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n{"n": 2}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False)
    events = _drain_polls(follower)
    assert [e.obj for e in events] == [{'n': 1}, {'n': 2}]
    assert all(e.generation == 0 for e in events)
    assert follower.poll() is None
    follower.close()


def test_follower_retains_partial_tail_and_delivers_once(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n{"n": 2')  # last line has no newline
    follower = JSONLFileFollower(str(path), start_at_end=False)

    event = follower.poll()
    assert event.obj == {'n': 1}
    assert follower.cur_byte_pos == len('{"n": 1}\n')

    # the unfinished tail is held back, not parsed or delivered
    assert follower.poll() is None
    assert follower.poll() is None

    with path.open('a') as stream:
        stream.write('}\n')
    event = follower.poll()
    assert event.obj == {'n': 2}
    # exactly once: nothing left, and re-polling does not replay it
    assert follower.poll() is None
    follower.close()


def test_follower_replacement_new_only(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False)
    assert follower.poll().obj == {'n': 1}

    _rotate(path, '{"n": 2}\n')  # new inode, one pre-existing record
    event = follower.poll()
    assert event.type == JSONLFollowEventType.FILE_REPLACED
    assert event.generation == 1
    assert event.obj is None
    assert follower.generation == 1

    # NEW_ONLY seeks to the end of the new file
    assert follower.poll() is None
    with path.open('a') as stream:
        stream.write('{"n": 3}\n')
    event = follower.poll()
    assert event.obj == {'n': 3}
    assert event.generation == 1
    follower.close()


def test_follower_replacement_from_start(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False,
                                 policy=JSONLFollowPolicy.FROM_START)
    follower.poll()
    _rotate(path, '{"n": 2}\n{"n": 3}\n')

    event = follower.poll()
    assert event.type == JSONLFollowEventType.FILE_REPLACED
    assert event.generation == 1
    events = _drain_polls(follower)
    assert [(e.generation, e.obj) for e in events] == [
        (1, {'n': 2}), (1, {'n': 3})]
    follower.close()


def test_follower_replacement_continue_drains_old_generation(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"g": 0, "n": 1}\n{"g": 0, "n": 2}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False,
                                 policy=JSONLFollowPolicy.CONTINUE)
    assert follower.poll().obj == {'g': 0, 'n': 1}

    # new file appears while the old one still has an unread record
    _rotate(path, '{"g": 1, "n": 1}\n')

    # rotation is noticed but no event is emitted until the old fd drains
    assert follower.poll() is None
    assert follower.generation == 0  # still delivering the old file
    event = follower.poll()  # remaining old-generation record first
    assert event.generation == 0
    assert event.obj == {'g': 0, 'n': 2}

    event = follower.poll()  # old inode hit EOF, now switch over
    assert event.type == JSONLFollowEventType.FILE_REPLACED
    assert event.generation == 1
    assert follower.generation == 1
    event = follower.poll()
    assert event.generation == 1
    assert event.obj == {'g': 1, 'n': 1}
    assert follower.poll() is None
    follower.close()


def test_follower_truncation_new_only(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n{"n": 2}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False)
    assert follower.poll().obj == {'n': 1}

    # open('w') truncates the same inode rather than replacing it;
    # write something shorter than the current read offset so the
    # shrink is detectable
    path.write_text('{"t":1}\n')
    event = follower.poll()
    assert event.type == JSONLFollowEventType.FILE_TRUNCATED
    assert event.generation == 1

    # truncated contents are skipped under NEW_ONLY
    assert follower.poll() is None
    with path.open('a') as stream:
        stream.write('{"t": 2}\n')
    event = follower.poll()
    assert (event.generation, event.obj) == (1, {'t': 2})
    follower.close()


def test_follower_truncation_from_start(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n{"n": 2}\n')
    follower = JSONLFileFollower(str(path),
                                 start_at_end=False,
                                 policy=JSONLFollowPolicy.FROM_START)
    follower.poll()
    path.write_text('{"t":1}\n')

    event = follower.poll()
    assert event.type == JSONLFollowEventType.FILE_TRUNCATED
    assert event.generation == 1
    event = follower.poll()
    assert (event.generation, event.obj) == (1, {'t': 1})
    follower.close()


def test_follower_continue_truncation_falls_back_to_new_only():
    follower = JSONLFileFollower('unused',
                                 policy=JSONLFollowPolicy.CONTINUE)
    assert follower.truncate_policy == JSONLFollowPolicy.NEW_ONLY


def test_follower_disappearance_and_reappear_is_new_generation(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n')
    follower = JSONLFileFollower(str(path), start_at_end=False)
    assert follower.poll().generation == 0

    os.remove(str(path))
    assert follower.poll() is None
    assert follower.cur_byte_pos is None

    path.write_text('{"n": 2}\n')
    event = follower.poll()
    assert event.type == JSONLFollowEventType.FILE_REPLACED
    assert event.generation == 1
    follower.close()


def test_follower_ignore_errors(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('not json\n{"ok": true}\n')

    bad = JSONLFileFollower(str(path), start_at_end=False)
    with pytest.raises(ValueError):
        bad.poll()
    bad.close()

    good = JSONLFileFollower(str(path), start_at_end=False,
                             ignore_errors=True)
    assert good.poll() is None  # malformed line skipped, one per poll
    event = good.poll()
    assert event.obj == {'ok': True}
    assert good.poll() is None
    good.close()


def test_follower_invalid_policy_raises():
    with pytest.raises(ValueError):
        JSONLFileFollower('unused', policy='tail_harder')
    with pytest.raises(ValueError):
        JSONLFileFollower('unused', truncate_policy='nope')


def test_follower_context_manager_closes(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text('{"n": 1}\n')
    with JSONLFileFollower(str(path), start_at_end=False) as follower:
        assert follower.poll().obj == {'n': 1}
    assert follower.poll() is None


def test_follower_sleeps_instead_of_busy_loop(tmp_path, monkeypatch):
    import threading
    from boltons import jsonutils

    path = tmp_path / 'records.jsonl'
    path.write_text('')
    sleeps = []
    monkeypatch.setattr(jsonutils.time, 'sleep',
                        lambda seconds: sleeps.append(seconds))

    follower = JSONLFileFollower(str(path), poll_interval=0.05)
    delivered = {}

    def consume():
        delivered['event'] = next(follower)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    thread.join(timeout=1.0)
    # blocked iteration waits via time.sleep rather than spinning
    assert sleeps
    assert all(interval == 0.05 for interval in sleeps)

    with path.open('a') as stream:
        stream.write('{"n": 1}\n')
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert delivered['event'].obj == {'n': 1}
    follower.close()
