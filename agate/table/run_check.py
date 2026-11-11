def run(self, check, group_by=None):
    """
    Run a :class:`.Check` against this table.

    :param check:
        An instance of :class:`.Check`.
    :param group_by:
        An optional column name, key function or sequence of them used to
        group the table before running the check. If omitted, the entire
        table is treated as a single group.
    :returns:
        A :class:`.CheckResult` listing every failing group.
    """
    return check.run(self, group_by=group_by)
