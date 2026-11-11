==================
Reconcile tables
==================

Check that arithmetic relationships between metrics hold within every group. For example, a daily report may contain an order detail table, a refund detail table and a settlement summary, with the requirement that sales minus refunds equal settlement amounts separately for every region and currency.

Declare a balance check
=======================

A :class:`.Balance` check names the metrics on each side of the relationship. When several tables are involved, each metric is a ``(table_index, column_name)`` tuple, where the index refers to the position in the list of tables.

.. code-block:: python

    from decimal import Decimal
    import agate
    from agate import Balance

    check = Balance(
        left_metrics=[('sales', (0, 'sales'))],
        right_metrics=[
            ('refund', (1, 'refund')),
            ('settled', (2, 'settled')),
        ],
        tolerance=Decimal('0.01'),
        name='sales minus refunds equals settlement',
    )

    result = check.run(
        [orders, refunds, settlements],
        group_by=['region', 'currency'],
    )

    if not result:
        for failure in result.failures:
            print(failure.group_key, failure.values, failure.difference)

The check evaluates each region/currency group independently. ``failure.values`` contains the summed metric values that participated in the check, and ``failure.difference`` is the signed left-minus-right amount. A group that does not appear in one of the tables is reported with the missing metrics as ``None`` rather than silently treated as zero, so the failure is surfaced instead of hidden.

Because the check runs per group, a value assigned to the wrong region is reported for both the overstated and understated groups even when the grand totals across all regions still match.

Run a check through a table
===========================

A check can also be invoked directly on a :class:`.Table`, optionally grouping it first:

.. code-block:: python

    result = table.run(check, group_by=['region', 'currency'])

Or on an existing :class:`.TableSet`, where each contained table is treated as a group and nested table sets produce composite group keys:

.. code-block:: python

    result = table.group_by('region').group_by('currency').run(check)

If a complete set of groups is required (including groups with no rows), use :meth:`.Table.homogenize` before running the check. Checks never fabricate groups on their own.
