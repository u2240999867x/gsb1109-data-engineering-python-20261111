"""
Checks declare reconciliation relationships between metrics and report the
groups where they do not hold.

A check describes an arithmetic relationship, such as sales minus refunds
equaling settlement amounts, and is run against one or more tables grouped
on the same keys. Unlike comparing grand totals, checks evaluate every group
independently, so a value assigned to the wrong group is reported for both
the overstated and understated groups.
"""

from agate.checks.balance import Balance
from agate.checks.check import Check
from agate.checks.results import CheckFailure, CheckResult

__all__ = [
    'Balance',
    'Check',
    'CheckFailure',
    'CheckResult',
]
