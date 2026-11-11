def run(self, check):
    """
    Run a :class:`.Check` against the tables in this set.

    Each table in the :class:`.TableSet` is treated as one group. Nested
    :class:`.TableSet` instances produce composite group keys.

    :param check:
        An instance of :class:`.Check`.
    :returns:
        A :class:`.CheckResult` listing every failing group.
    """
    return check.run(self)
