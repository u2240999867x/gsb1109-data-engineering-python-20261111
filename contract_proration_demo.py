#!/usr/bin/env python

"""
Demo: allocate contract fees across calendar months by actual covered days.

Run with:

    python contract_proration_demo.py
"""

from datetime import date
from decimal import Decimal

import agate
from contract_proration import (
    assert_reconciled,
    monthly_periods,
    prorate_contracts,
    reconcile_proration,
)

contracts = agate.Table(
    [
        ('HT-2024-001', 'Acme Facilities', date(2024, 1, 15), date(2024, 3, 10), Decimal('1000.00')),
        ('HT-2024-002', 'Beta Security', date(2024, 1, 31), date(2024, 3, 1), Decimal('100.00')),
    ],
    ('contract_id', 'vendor', 'start_date', 'end_date', 'amount'),
    [agate.Text(), agate.Text(), agate.Date(), agate.Date(), agate.Number()],
)

periods = monthly_periods(date(2024, 1, 1), date(2024, 3, 31))

prorated = prorate_contracts(
    contracts,
    key_column_name='contract_id',
    start_column_name='start_date',
    end_column_name='end_date',
    amount_column_name='amount',
    periods=periods,
    precision=2,
)

print('Prorated rows (all amounts are Decimal, no float anywhere):')
prorated.select([
    'contract_id', 'period', 'covered_days',
    'raw_allocated_amount', 'allocated_amount', 'is_remainder_row',
]).print_table()

reconciliation = reconcile_proration(
    prorated, contracts, 'contract_id', 'start_date', 'end_date', 'amount'
)

print('Reconciliation against the original contracts:')
reconciliation.print_table()

assert_reconciled(reconciliation)
print('OK: every contract reconciles exactly, tail differences are flagged.')
