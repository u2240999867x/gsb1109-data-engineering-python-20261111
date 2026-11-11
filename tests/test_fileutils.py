import os.path
import pathlib

import pytest

from boltons import fileutils
from boltons.fileutils import FilePerms, FindResult, iter_find_files
from boltons.strutils import removeprefix


BOLTONS_PATH = os.path.dirname(os.path.abspath(fileutils.__file__))


def test_fileperms():
    up = FilePerms()
    up.other = ''
    up.user = 'xrw'
    up.group = 'rrrwx'
    try:
        up.other = 'nope'
    except ValueError:
        # correctly raised ValueError on invalid chars
        pass

    assert repr(up) == "FilePerms(user='rwx', group='rwx', other='')"
    assert up.user == 'rwx'
    assert oct(int(up)) == '0o770'

    assert int(FilePerms()) == 0


def test_fileperms_from_int_leading_zero_groups():
    assert int(FilePerms.from_int(0o007)) == 0o007
    assert FilePerms.from_int(0o070).group == 'rwx'
    assert FilePerms.from_int(0o070).user == ''


def test_fileperms_revoke():
    fp = FilePerms(user='rw', other='r')
    fp.other = ''
    assert int(fp) == 0o600
    fp.user = 'r'
    assert int(fp) == 0o400


class _FlushFailingFile:
    def __init__(self, f):
        self.f = f

    def write(self, data):
        return self.f.write(data)

    def fileno(self):
        return self.f.fileno()

    def flush(self):
        raise OSError('simulated flush failure')

    def close(self):
        self.f.close()


class _FlushFailingSaver(fileutils.AtomicSaver):
    def _open_part_file(self):
        super()._open_part_file()
        self.real_part_file = self.part_file
        self.part_file = _FlushFailingFile(self.real_part_file)


def test_atomicsaver_flush_failure_cleans_up(tmp_path):
    dest = tmp_path / 'output.bin'
    saver = _FlushFailingSaver(str(dest))
    try:
        with saver as f:
            f.write(b'data')
    except OSError as e:
        assert 'simulated flush failure' in str(e)
    else:
        raise AssertionError('expected OSError from flush')
    assert saver.real_part_file.closed
    assert not os.path.exists(saver.part_path)
    assert not dest.exists()



def test_atomicsaver_pathlike(tmp_path):
    dest = tmp_path / 'output.bin'
    with fileutils.AtomicSaver(dest) as f:
        f.write(b'pathlike works')
    assert dest.read_bytes() == b'pathlike works'


def test_iter_find_files_pathlike():
    boltons_path = pathlib.Path(BOLTONS_PATH)
    results = list(iter_find_files(boltons_path, patterns=['*.py']))
    basenames = [os.path.basename(p) for p in results]
    assert 'fileutils.py' in basenames


def test_iter_find_files():
    def _to_baseless_list(paths):
        return [removeprefix(p, BOLTONS_PATH).lstrip(os.path.sep) for p in paths]

    assert 'fileutils.py' in _to_baseless_list(iter_find_files(BOLTONS_PATH, patterns=['*.py']))

    boltons_parent = os.path.dirname(BOLTONS_PATH)
    assert 'fileutils.py' in _to_baseless_list(iter_find_files(boltons_parent, patterns=['*.py']))
    assert 'fileutils.py' not in _to_baseless_list(iter_find_files(boltons_parent, patterns=['*.py'], max_depth=0))


def test_iter_find_files_trailing_sep(tmp_path):
    sub2 = tmp_path / 'sub' / 'sub2'
    sub2.mkdir(parents=True)
    for path in (tmp_path / 'a.txt', tmp_path / 'sub' / 'b.txt', sub2 / 'c.txt'):
        path.write_text('x')

    def _names(directory):
        return sorted(os.path.basename(p) for p in
                      iter_find_files(directory, '*.txt', max_depth=1))

    assert _names(str(tmp_path)) == ['a.txt', 'b.txt']
    assert _names(str(tmp_path) + os.path.sep) == ['a.txt', 'b.txt']


def _make_linked_tree(tmp_path):
    """Builds a tree with internal, external, looping, and broken links."""
    root = tmp_path / 'root'
    real = root / 'real'
    (real / 'sub').mkdir(parents=True)
    (real / 'a.txt').write_text('a')
    (real / 'sub' / 'b.txt').write_text('b')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'c.txt').write_text('c')
    (root / 'top.txt').write_text('top')
    os.symlink(str(real), str(root / 'link_real'))
    os.symlink(str(real), str(root / 'link_real2'))
    os.symlink(str(outside), str(root / 'link_outside'))
    os.symlink(str(root), str(root / 'link_loop'))
    os.symlink(str(root / 'missing'), str(root / 'link_broken'))
    os.symlink(str(real / 'a.txt'), str(root / 'file_link.txt'))
    return root


def test_iter_find_files_follow_links_never(tmp_path):
    root = _make_linked_tree(tmp_path)
    found = list(iter_find_files(str(root), '*.txt'))
    assert all(isinstance(p, str) for p in found)
    assert sorted(os.path.basename(p) for p in found) == ['a.txt', 'b.txt', 'file_link.txt', 'top.txt']


def test_iter_find_files_follow_links_within(tmp_path):
    root = _make_linked_tree(tmp_path)
    results = list(iter_find_files(str(root), '*.txt', follow_links='within'))
    assert all(isinstance(r, FindResult) for r in results)

    issues = {os.path.basename(r.path): r.issue for r in results if r.issue}
    assert issues == {'link_loop': 'loop', 'link_broken': 'broken'}

    matched = [r for r in results if not r.issue]
    names = sorted(os.path.basename(r.path) for r in matched)
    # dedupe_links defaults to True: link_real2's contents are not repeated
    assert names == ['a.txt', 'b.txt', 'file_link.txt', 'top.txt']

    by_name = {os.path.basename(r.path): r for r in matched}
    # discovered path and resolved target are both reported
    file_link = by_name['file_link.txt']
    assert file_link.target == os.path.realpath(str(by_name['a.txt'].path))
    assert by_name['top.txt'].target == os.path.realpath(by_name['top.txt'].path)
    # the external link is not followed
    assert 'c.txt' not in names


def test_iter_find_files_follow_links_no_dedupe(tmp_path):
    root = _make_linked_tree(tmp_path)
    results = list(iter_find_files(str(root), '*.txt',
                                   follow_links=True, dedupe_links=False))
    names = sorted(os.path.basename(r.path) for r in results if not r.issue)
    assert names == (['a.txt'] * 3 + ['b.txt'] * 3 + ['file_link.txt', 'top.txt'])


def test_iter_find_files_follow_links_include_dirs(tmp_path):
    root = _make_linked_tree(tmp_path)
    results = list(iter_find_files(str(root), 'sub', include_dirs=True,
                                   follow_links=True))
    matched = [r for r in results if not r.issue]
    assert len(matched) == 1
    assert os.path.basename(matched[0].path) == 'sub'
    assert matched[0].target == os.path.realpath(str(root / 'real' / 'sub'))


def test_iter_find_files_follow_links_max_depth(tmp_path):
    root = _make_linked_tree(tmp_path)
    results = list(iter_find_files(str(root), '*.txt',
                                   follow_links=True, max_depth=1))
    names = sorted(os.path.basename(r.path) for r in results if not r.issue)
    assert names == ['a.txt', 'file_link.txt', 'top.txt']


def test_iter_find_files_follow_links_invalid(tmp_path):
    with pytest.raises(ValueError):
        list(iter_find_files(str(tmp_path), '*.txt', follow_links='always'))


def test_iter_find_files_replaced_dir(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    sub = root / 'sub'
    sub.mkdir(parents=True)
    (sub / 'a.txt').write_text('a')

    real_scandir = os.scandir
    swapped = []

    # replace the directory with a file after it has been listed
    def replacing_scandir(path):
        entries = list(real_scandir(path))
        if os.fspath(path) == str(root) and not swapped:
            swapped.append(True)
            (sub / 'a.txt').unlink()
            sub.rmdir()
            sub.write_text('not a dir')
        return iter(entries)

    monkeypatch.setattr(fileutils.os, 'scandir', replacing_scandir)
    results = list(iter_find_files(str(root), '*.txt', follow_links=True))
    assert [(os.path.basename(r.path), r.issue) for r in results] == [('sub', 'replaced')]


def test_rotate_file_no_rotation(tmp_path):
    file_path = tmp_path / 'test_file.txt'
    fileutils.rotate_file(file_path)
    assert not file_path.exists()


def test_rotate_file_one_rotation(tmp_path):
    file_path = tmp_path / 'test_file.txt'
    file_path.write_text('test content')
    assert file_path.exists()

    fileutils.rotate_file(file_path)
    assert not file_path.exists()
    assert (tmp_path / 'test_file.1.txt').exists()


def test_rotate_file_full_rotation(tmp_path):
    file_path = tmp_path / 'test_file.txt'
    file_path.write_text('test content 0')
    for i in range(1, 6):
        cur_path = tmp_path / f'test_file.{i}.txt'
        cur_path.write_text(f'test content {i}')
        assert cur_path.exists()

    fileutils.rotate_file(file_path, keep=5)
    assert not file_path.exists()

    for i in range(1, 6):
        cur_path = tmp_path / f'test_file.{i}.txt'
        assert cur_path.read_text() == f'test content {i-1}'

    assert not (tmp_path / 'test_file.6.txt').exists()

def test_rotate_file_full_rotation_no_ext(tmp_path):
    file_path = tmp_path / 'test_file'
    file_path.write_text('test content 0')
    for i in range(1, 6):
        cur_path = tmp_path / f'test_file.{i}'
        cur_path.write_text(f'test content {i}')
        assert cur_path.exists()

    fileutils.rotate_file(file_path, keep=5)
    assert not file_path.exists()

    for i in range(1, 6):
        cur_path = tmp_path / f'test_file.{i}'
        assert cur_path.read_text() == f'test content {i-1}'

    assert not (tmp_path / 'test_file.6').exists()


def test_rotate_file_keep_one(tmp_path):
    # keep=1 used to delete the backup it had just made
    file_path = tmp_path / 'log.txt'
    for gen in range(3):
        file_path.write_text(f'gen {gen}')
        fileutils.rotate_file(file_path, keep=1)
        assert sorted(p.name for p in tmp_path.iterdir()) == ['log.1.txt']
        assert (tmp_path / 'log.1.txt').read_text() == f'gen {gen}'

