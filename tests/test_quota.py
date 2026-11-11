"""Test diskcache.quota.Quota."""

import shutil

import pytest

import diskcache as dc


@pytest.fixture
def quota():
    with dc.Quota(count_limit=4, size_limit=100) as quota:
        yield quota
    shutil.rmtree(quota.directory, ignore_errors=True)


def test_init(quota):
    assert quota.limits == {'count_limit': 4, 'size_limit': 100}
    assert quota.usage() == (0, 0)
    quota.close()
    quota.close()


def test_init_unknown_limit():
    with pytest.raises(ValueError):
        dc.Quota(unknown_limit=0)


def test_admit_usage(quota):
    lease = quota.admit(count=2, size=40)
    assert lease.count == 2
    assert lease.size == 40
    assert quota.usage() == (2, 40)


def test_admit_atomic_dimensions(quota):
    quota.admit(count=3, size=90)

    # Fits count but not size: rejected without charging count.
    assert quota.admit(count=1, size=20) is None
    assert quota.usage() == (3, 90)

    # Fits size but not count: rejected without charging size.
    assert quota.admit(count=2, size=10) is None
    assert quota.usage() == (3, 90)

    # Fits both dimensions: admitted.
    lease = quota.admit(count=1, size=10)
    assert lease is not None
    assert quota.usage() == (4, 100)


def test_admit_negative(quota):
    with pytest.raises(ValueError):
        quota.admit(count=-1)
    with pytest.raises(ValueError):
        quota.admit(size=-1)
    assert quota.usage() == (0, 0)


def test_release_idempotent(quota):
    lease = quota.admit(count=2, size=40)
    assert quota.release(lease) is True
    assert quota.usage() == (0, 0)

    # Repeated release does not restore additional quota.
    assert quota.release(lease) is False
    assert lease.release() is False
    assert quota.usage() == (0, 0)

    # Full capacity is still available exactly once.
    assert quota.admit(count=4, size=100) is not None
    assert quota.admit(count=1, size=1) is None


def test_shrink_limits_keeps_leases(quota):
    lease = quota.admit(count=3, size=90)
    quota.set_limits(count_limit=1, size_limit=10)

    # New admissions fail while over capacity.
    assert quota.admit(count=1, size=1) is None

    # Existing credential is still interpretable and releases occupancy.
    assert lease.release() is True
    assert quota.usage() == (0, 0)
    assert quota.admit(count=1, size=10) is not None


def test_set_limits_unknown(quota):
    with pytest.raises(ValueError):
        quota.set_limits(unknown_limit=0)


def test_restart_persists_usage(tmp_path):
    directory = str(tmp_path / 'quota')

    quota = dc.Quota(directory, count_limit=4, size_limit=100)
    quota.admit(count=2, size=40)
    quota.close()

    other = dc.Quota(directory)
    assert other.usage() == (2, 40)
    assert other.limits == {'count_limit': 4, 'size_limit': 100}
    other.close()


def test_lease_context_manager(quota):
    with quota.admit(count=1, size=10) as lease:
        assert quota.usage() == (1, 10)
        assert lease.lease_id is not None
    assert quota.usage() == (0, 0)


def test_lease_repr(quota):
    lease = quota.admit(count=1, size=10)
    assert repr(lease) == 'Lease(lease_id=%r, count=1, size=10)' % (
        lease.lease_id,
    )
