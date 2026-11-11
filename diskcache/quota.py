"""Persistent multi-dimension quota with atomic admission.

A :class:`Quota` tracks occupancy along multiple dimensions (file count and
total bytes) in a single SQLite database. Admission checks every dimension in
one transaction so a lease is never partially granted. Each successful
admission returns a :class:`Lease` credential that may later be released.
"""

import contextlib as cl
import errno
import os
import os.path as op
import sqlite3
import tempfile
import threading
import time

from .core import Timeout

DBNAME = 'quota.db'

DEFAULT_LIMITS = {
    'count_limit': 2**20,
    'size_limit': 2**30,  # 1gb
}


class Lease:
    """Occupancy credential returned by :meth:`Quota.admit`.

    The lease records the dimensions granted at admission time so it stays
    interpretable even after quota limits are changed below current
    occupancy.
    """

    def __init__(self, quota, lease_id, count, size):
        self._quota = quota
        self._lease_id = lease_id
        self._count = count
        self._size = size

    @property
    def lease_id(self):
        """Lease identifier in the quota database."""
        return self._lease_id

    @property
    def count(self):
        """File count reserved by this lease."""
        return self._count

    @property
    def size(self):
        """Byte size reserved by this lease."""
        return self._size

    def release(self, retry=False):
        """Release occupancy back to the quota.

        Releasing an already-released lease does not restore additional
        quota.

        :param bool retry: retry if database timeout occurs (default False)
        :return: True if occupancy was released, False if already released
        :raises Timeout: if database timeout occurs

        """
        return self._quota.release(self, retry=retry)

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.release()

    def __repr__(self):
        return '%s(lease_id=%r, count=%r, size=%r)' % (
            type(self).__name__,
            self._lease_id,
            self._count,
            self._size,
        )


class Quota:
    """Persistent quota with atomic admission across all dimensions.

    Occupancy and limits are stored in a single SQLite database within
    `directory` so current usage is queryable after a restart. Quotas are
    local to one database; no distributed coordination is provided.

    >>> quota = Quota(count_limit=2, size_limit=100)
    >>> lease = quota.admit(count=1, size=40)
    >>> quota.usage()
    (1, 40)
    >>> lease.release()
    True
    >>> lease.release()
    False
    >>> quota.usage()
    (0, 0)
    >>> quota.close()

    """

    def __init__(self, directory=None, timeout=60, **limits):
        """Initialize quota instance.

        :param str directory: quota database directory
        :param float timeout: SQLite connection timeout
        :param limits: any of DEFAULT_LIMITS

        """
        for key in limits:
            if key not in DEFAULT_LIMITS:
                raise ValueError('unknown limit: %r' % key)

        if directory is None:
            directory = tempfile.mkdtemp(prefix='diskcache-quota-')
        directory = str(directory)
        directory = op.expanduser(directory)
        directory = op.expandvars(directory)

        self._directory = directory
        self._timeout = 0  # Manually handle retries during initialization.
        self._local = threading.local()
        self._txn_id = None

        if not op.isdir(directory):
            try:
                os.makedirs(directory, 0o755)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise EnvironmentError(
                        error.errno,
                        'Quota directory "%s" does not exist'
                        ' and could not be created' % self._directory,
                    ) from None

        sql = self._sql_retry

        sql(
            'CREATE TABLE IF NOT EXISTS Settings ('
            ' key TEXT NOT NULL UNIQUE,'
            ' value)'
        )

        sql(
            'CREATE TABLE IF NOT EXISTS Lease ('
            ' lease_id INTEGER PRIMARY KEY,'
            ' count INTEGER NOT NULL,'
            ' size INTEGER NOT NULL)'
        )

        # Persisted limits take precedence over defaults while explicitly
        # given limits take precedence over both.

        current_limits = dict(sql('SELECT key, value FROM Settings'))

        sets = DEFAULT_LIMITS.copy()
        sets.update(current_limits)
        sets.update(limits)

        for key, value in sets.items():
            query = 'INSERT OR REPLACE INTO Settings VALUES (?, ?)'
            sql(query, (key, value))

        # Close and re-open database connection with given timeout.

        self.close()
        self._timeout = timeout
        self._sql  # pylint: disable=pointless-statement

    @property
    def directory(self):
        """Quota database directory."""
        return self._directory

    @property
    def timeout(self):
        """SQLite connection timeout value in seconds."""
        return self._timeout

    @property
    def _con(self):
        # Check process ID to support process forking. If the process
        # ID changes, close the connection and update the process ID.

        local_pid = getattr(self._local, 'pid', None)
        pid = os.getpid()

        if local_pid != pid:
            self.close()
            self._local.pid = pid

        con = getattr(self._local, 'con', None)

        if con is None:
            con = self._local.con = sqlite3.connect(
                op.join(self._directory, DBNAME),
                timeout=self._timeout,
                isolation_level=None,
            )

        return con

    @property
    def _sql(self):
        return self._con.execute

    @property
    def _sql_retry(self):
        sql = self._sql

        def _execute_with_retry(statement, *args, **kwargs):
            start = time.time()
            while True:
                try:
                    return sql(statement, *args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if str(exc) != 'database is locked':
                        raise
                    diff = time.time() - start
                    if diff > 60:
                        raise
                    time.sleep(0.001)

        return _execute_with_retry

    @cl.contextmanager
    def _transact(self, retry=False):
        sql = self._sql
        tid = threading.get_ident()
        txn_id = self._txn_id

        if tid == txn_id:
            begin = False
        else:
            while True:
                try:
                    sql('BEGIN IMMEDIATE')
                    begin = True
                    self._txn_id = tid
                    break
                except sqlite3.OperationalError:
                    if retry:
                        continue
                    raise Timeout from None

        try:
            yield sql
        except BaseException:
            if begin:
                assert self._txn_id == tid
                self._txn_id = None
                sql('ROLLBACK')
            raise
        else:
            if begin:
                assert self._txn_id == tid
                self._txn_id = None
                sql('COMMIT')

    def admit(self, count=1, size=0, retry=False):
        """Admit occupancy of `count` files and `size` bytes.

        All dimensions are checked in a single transaction: admission
        succeeds only when every dimension fits within its limit, and no
        dimension is charged otherwise.

        Raises :exc:`Timeout` error when database timeout occurs and `retry`
        is `False` (default).

        :param int count: number of files to reserve (default 1)
        :param int size: number of bytes to reserve (default 0)
        :param bool retry: retry if database timeout occurs (default False)
        :return: :class:`Lease` credential or None when over quota
        :raises Timeout: if database timeout occurs
        :raises ValueError: if `count` or `size` is negative

        """
        if count < 0 or size < 0:
            raise ValueError('count and size must be non-negative')

        with self._transact(retry=retry) as sql:
            ((count_limit,),) = sql(
                'SELECT value FROM Settings WHERE key = ?', ('count_limit',)
            ).fetchall()
            ((size_limit,),) = sql(
                'SELECT value FROM Settings WHERE key = ?', ('size_limit',)
            ).fetchall()
            ((used_count, used_size),) = sql(
                'SELECT COALESCE(SUM(count), 0), COALESCE(SUM(size), 0)'
                ' FROM Lease'
            ).fetchall()

            if used_count + count > count_limit:
                return None
            if used_size + size > size_limit:
                return None

            cursor = sql(
                'INSERT INTO Lease (count, size) VALUES (?, ?)',
                (count, size),
            )
            lease_id = cursor.lastrowid

        return Lease(self, lease_id, count, size)

    def release(self, lease, retry=False):
        """Release occupancy held by `lease` credential.

        Repeated release of the same lease does not restore additional
        quota. Release succeeds regardless of the current limits so leases
        remain interpretable after limits are lowered below occupancy.

        :param lease: :class:`Lease` returned by :meth:`admit`
        :param bool retry: retry if database timeout occurs (default False)
        :return: True if occupancy was released, False if already released
        :raises Timeout: if database timeout occurs

        """
        with self._transact(retry=retry) as sql:
            cursor = sql(
                'DELETE FROM Lease WHERE lease_id = ?', (lease.lease_id,)
            )
            released = cursor.rowcount == 1

        return released

    def usage(self):
        """Return current occupancy as (count, size) pair.

        Occupancy is persisted in the quota database and may be queried
        after a restart.

        :return: (count, size) of outstanding leases

        """
        ((count, size),) = self._sql(
            'SELECT COALESCE(SUM(count), 0), COALESCE(SUM(size), 0)'
            ' FROM Lease'
        ).fetchall()
        return count, size

    @property
    def limits(self):
        """Mapping of limit names to values."""
        pairs = self._sql('SELECT key, value FROM Settings').fetchall()
        return dict(pairs)

    def set_limits(self, **limits):
        """Update quota limits.

        Lowering a limit below current occupancy does not invalidate
        outstanding leases; their credentials may still be released.

        :param limits: any of DEFAULT_LIMITS
        :raises ValueError: if a limit name is unknown

        """
        for key in limits:
            if key not in DEFAULT_LIMITS:
                raise ValueError('unknown limit: %r' % key)

        with self._transact() as sql:
            for key, value in sorted(limits.items()):
                query = 'INSERT OR REPLACE INTO Settings VALUES (?, ?)'
                sql(query, (key, value))

    def close(self):
        """Close database connection."""
        con = getattr(self._local, 'con', None)

        if con is None:
            return

        con.close()

        try:
            delattr(self._local, 'con')
        except AttributeError:
            pass

    def __enter__(self):
        # Create connection in thread.
        # pylint: disable=unused-variable
        connection = self._con  # noqa
        return self

    def __exit__(self, *exception):
        self.close()
