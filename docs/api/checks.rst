======
Checks
======

.. automodule:: agate.checks
    :no-members:

Checks declare reconciliation relationships between metrics and report the
groups where the relationships do not hold. They are useful for validating
that separately sourced tables remain consistent, for example that sales
minus refunds equal settlement amounts within every region and currency.

Checks are evaluated per group rather than on grand totals, so a value
recorded against the wrong group is reported for both the overstated and
understated groups even when the overall totals match. Groups that are
missing from a table fail the check instead of being implicitly treated as
zero. Tolerances are specified explicitly per check.

.. autosummary::
    :nosignatures:

    agate.Check
    agate.Balance
    agate.CheckResult
    agate.CheckFailure

Detailed list
-------------

.. autoclass:: agate.Check
    :members:
.. autoclass:: agate.Balance
    :members:
.. autoclass:: agate.CheckResult
    :members:
.. autoclass:: agate.CheckFailure
    :members:
