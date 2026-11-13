"""
Explicit day-based proration of contract fees into calendar months, built on
agate.

The proration is *explicit* instead of rounding each month independently:

1. Every contract is split into the target month periods it overlaps, and the
   inclusive number of covered days in each period is counted.
2. Money is handled entirely in :class:`decimal.Decimal`. Internally the
   allocation is done in integer minor units (e.g. cents) so no binary float
   error can appear.
3. Each period first receives its proportional share rounded to the
   business-specified precision. The net tail difference is then moved in whole
   minor units onto deterministic rows (by default the last covered period).
   Rows that carry a tail adjustment are flagged, and the unrounded share is
   kept in a separate column, so the destination of every cent is explainable.
4. The allocated amounts of a contract always sum to the original amount
   (rounded to the business precision when the input amount carries more
   decimal places than the requested precision).

Target periods are validated up front: overlaps and incomplete coverage of a
contract raise :class:`PeriodCoverageError` before any allocation happens.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import agate

REMAINDER_STRATEGIES = ('last', 'first', 'largest_remainder')


class PeriodCoverageError(ValueError):
    """
    Raised when the target periods overlap or do not cover a contract span.
    """


def monthly_periods(start, end):
    """
    Build inclusive calendar-month periods covering :code:`start` through
    :code:`end`.

    Returns a list of :code:`(label, period_start, period_end)` tuples, where
    the label is :code:`YYYY-MM` and the boundaries are always the first and
    last day of the calendar month.

    :param start:
        A :class:`datetime.date`, the first covered day (inclusive).
    :param end:
        A :class:`datetime.date`, the last covered day (inclusive).
    """
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError('start and end must be datetime.date instances.')
    if start > end:
        raise ValueError('start must be on or before end.')

    periods = []
    cursor = date(start.year, start.month, 1)

    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)

        periods.append((
            cursor.strftime('%Y-%m'),
            cursor,
            next_month - timedelta(days=1),
        ))
        cursor = next_month

    return periods


def _normalize_periods(periods):
    """
    Normalize period mappings or ``(label, start, end)`` tuples into tuples and
    validate their basic shape.
    """
    normalized = []

    for index, period in enumerate(periods):
        if isinstance(period, dict):
            label = period['period']
            period_start = period['start']
            period_end = period['end']
        else:
            label, period_start, period_end = period

        if not isinstance(period_start, date) or not isinstance(period_end, date):
            raise ValueError('Period %r must use datetime.date boundaries.' % label)
        if period_start > period_end:
            raise ValueError('Period %r starts after it ends.' % label)

        normalized.append((str(label), period_start, period_end, index))

    if not normalized:
        raise ValueError('At least one target period is required.')

    return normalized


def _check_period_structure(periods):
    """
    Validate period boundaries once, independently of any contract.
    """
    ordered = sorted(periods, key=lambda item: item[1])

    for previous, current in zip(ordered, ordered[1:]):
        if current[1] <= previous[2]:
            raise PeriodCoverageError(
                'Target periods %r (%s to %s) and %r (%s to %s) overlap.'
                % (previous[0], previous[1], previous[2],
                   current[0], current[1], current[2])
            )

    return ordered


def _check_contract_coverage(segments, contract_start, contract_end, contract_key):
    """
    Validate that the overlapping periods tile the full contract span with no
    uncovered head, tail or internal gap.
    """
    if not segments:
        raise PeriodCoverageError(
            'Contract %r (%s to %s) is not covered by any target period.'
            % (contract_key, contract_start, contract_end)
        )

    first_label, first_start, first_end, _ = segments[0]
    last_label, _, last_end, _ = segments[-1]

    if first_start > contract_start:
        raise PeriodCoverageError(
            'Contract %r is not fully covered: the first overlapping period %r starts on '
            '%s but the contract starts on %s.'
            % (contract_key, first_label, first_start, contract_start)
        )

    if first_end < contract_start:
        raise PeriodCoverageError(
            'Contract %r head starting %s is not covered by period %r.'
            % (contract_key, contract_start, first_label)
        )

    if last_end < contract_end:
        raise PeriodCoverageError(
            'Contract %r is not fully covered: the last overlapping period %r ends on %s '
            'but the contract ends on %s.'
            % (contract_key, last_label, last_end, contract_end)
        )

    for previous, current in zip(segments, segments[1:]):
        expected_start = previous[2] + timedelta(days=1)

        if current[1] != expected_start:
            raise PeriodCoverageError(
                'Contract %r has a coverage gap between period %r (ends %s) and period %r '
                '(starts %s); expected %r to start on %s.'
                % (contract_key, previous[0], previous[2],
                   current[0], current[1], current[0], expected_start)
            )


def _covered_days(contract_start, contract_end, period_start, period_end):
    """
    Count inclusive days of overlap between a contract and a period.
    """
    low = max(contract_start, period_start)
    high = min(contract_end, period_end)

    if low > high:
        return 0

    return (high - low).days + 1


def _quantize_minor_units(amount, precision, rounding):
    """
    Convert an amount to signed, rounded integer minor units at the given
    precision.
    """
    quantum = Decimal(1).scaleb(-precision)
    quantized = amount.quantize(quantum, rounding=rounding)

    return int(quantized.scaleb(precision).to_integral_value(rounding=rounding)), quantized


def _allocate_units(total_units, raw_units, rounding, remainder_to):
    """
    Split ``total_units`` integer minor units across periods.

    Returns ``(base_units, adjusted_units, remainder_indexes)`` where the
    adjusted units sum exactly to ``total_units``.
    """
    period_count = len(raw_units)

    if remainder_to == 'largest_remainder':
        # Hamilton / largest-remainder method: floor every share, then hand the
        # leftover units to the largest fractional remainders. Ties are resolved
        # by earlier period first, keeping the result deterministic.
        base_units = [int(share // 1) for share in raw_units]
        ranked = sorted(
            range(period_count),
            key=lambda index: (raw_units[index] - int(raw_units[index]), -index),
            reverse=True,
        )
    else:
        base_units = [
            int(share.quantize(Decimal(1), rounding=rounding)) for share in raw_units
        ]
        order = range(period_count) if remainder_to == 'first' else range(period_count - 1, -1, -1)
        ranked = list(order)

    adjusted_units = list(base_units)
    difference = total_units - sum(base_units)
    remainder_indexes = set()
    step = 1 if difference > 0 else -1

    cursor = 0
    while difference != 0:
        index = ranked[cursor % period_count]
        cursor += 1

        if step < 0 and adjusted_units[index] <= 0:
            continue

        adjusted_units[index] += step
        remainder_indexes.add(index)
        difference -= step

    return base_units, adjusted_units, remainder_indexes


def prorate_contracts(
    contracts,
    key_column_name,
    start_column_name,
    end_column_name,
    amount_column_name,
    periods,
    precision=2,
    rounding=ROUND_HALF_UP,
    remainder_to='last',
    period_column_name='period',
    period_start_column_name='period_start',
    period_end_column_name='period_end',
    days_column_name='covered_days',
    raw_amount_column_name='raw_allocated_amount',
    allocated_column_name='allocated_amount',
    remainder_flag_column_name='is_remainder_row',
):
    """
    Prorate contract fees into target month periods by covered days.

    Each output row keeps every original contract column (the contract identity)
    and adds the period label, period boundaries, inclusive covered days, the
    unrounded proportional amount, the final allocated amount and a flag marking
    rows that received a tail-difference adjustment.

    :param contracts:
        An agate :class:`.Table` containing one row per contract.
    :param key_column_name:
        Name of the column holding the unique contract identifier.
    :param start_column_name:
        Name of the :class:`.Date` column holding the inclusive contract start.
    :param end_column_name:
        Name of the :class:`.Date` column holding the inclusive contract end.
    :param amount_column_name:
        Name of the :class:`.Number` column holding the contract fee.
    :param periods:
        A sequence of ``(label, start, end)`` tuples or mappings with
        :code:`period`/:code:`start`/:code:`end` keys. Boundaries are inclusive
        and must tile each contract span with no overlaps or gaps. Use
        :func:`monthly_periods` to generate calendar-month periods.
    :param precision:
        Business-specified number of decimal places for the allocated amounts.
    :param rounding:
        A :mod:`decimal` rounding constant used when rounding each proportional
        share. Defaults to :data:`decimal.ROUND_HALF_UP`.
    :param remainder_to:
        Where the net tail difference goes: :code:`'last'` (default) adjusts the
        final covered period first, :code:`'first'` the first, and
        :code:`'largest_remainder'` uses the Hamilton largest-remainder method.
    :returns:
        A new agate :class:`.Table` with one row per contract-period pair.
    """
    if remainder_to not in REMAINDER_STRATEGIES:
        raise ValueError(
            'remainder_to must be one of %s, got %r.' % (REMAINDER_STRATEGIES, remainder_to)
        )
    if not isinstance(precision, int) or precision < 0:
        raise ValueError('precision must be a non-negative integer.')
    for required in (key_column_name, start_column_name, end_column_name, amount_column_name):
        if required not in contracts.column_names:
            raise ValueError('Contracts table is missing required column %r.' % required)

    new_column_names = (
        period_column_name, period_start_column_name, period_end_column_name,
        days_column_name, raw_amount_column_name, allocated_column_name,
        remainder_flag_column_name,
    )
    collisions = [name for name in new_column_names if name in contracts.column_names]
    if collisions:
        raise ValueError('Output column names collide with contract columns: %s.' % collisions)

    normalized_periods = _normalize_periods(periods)
    ordered_periods = _check_period_structure(normalized_periods)

    output_column_names = list(contracts.column_names) + list(new_column_names)
    output_column_types = list(contracts.column_types) + [
        agate.Text(), agate.Date(), agate.Date(), agate.Number(),
        agate.Number(), agate.Number(), agate.Boolean(),
    ]

    contract_tables = []

    for contract in contracts.rows:
        contract_key = contract[key_column_name]
        contract_start = contract[start_column_name]
        contract_end = contract[end_column_name]
        amount = contract[amount_column_name]

        if contract_start is None or contract_end is None:
            raise ValueError('Contract %r has a missing start or end date.' % contract_key)
        if contract_start > contract_end:
            raise ValueError('Contract %r starts after it ends.' % contract_key)
        if amount is None:
            raise ValueError('Contract %r has a missing amount.' % contract_key)
        if not isinstance(amount, Decimal):
            raise ValueError('Contract %r amount must be Decimal (use an agate Number column).'
                             % contract_key)

        segments = []
        for label, period_start, period_end, _ in ordered_periods:
            days = _covered_days(contract_start, contract_end, period_start, period_end)

            if days:
                segments.append((label, period_start, period_end, days))

        _check_contract_coverage(segments, contract_start, contract_end, contract_key)

        total_days = sum(segment[3] for segment in segments)
        contract_days = (contract_end - contract_start).days + 1

        if total_days != contract_days:
            raise RuntimeError(
                'Contract %r covered %s days but spans %s days; period tiling is wrong.'
                % (contract_key, total_days, contract_days)
            )

        sign = -1 if amount < 0 else 1
        total_units, quantized_amount = _quantize_minor_units(amount, precision, rounding)
        absolute_total_units = abs(total_units)
        absolute_scaled = abs(amount).scaleb(precision)

        raw_units = [
            (absolute_scaled * segment[3]) / Decimal(total_days) for segment in segments
        ]
        _, adjusted_units, remainder_indexes = _allocate_units(
            absolute_total_units, raw_units, rounding, remainder_to
        )

        if sum(adjusted_units) != absolute_total_units:
            raise RuntimeError(
                'Contract %r allocation does not reconcile: %s minor units vs %s.'
                % (contract_key, sum(adjusted_units), absolute_total_units)
            )

        rows = []
        for index, (label, period_start, period_end, days) in enumerate(segments):
            raw_amount = sign * (raw_units[index].scaleb(-precision))
            allocated_amount = sign * Decimal(adjusted_units[index]).scaleb(-precision)

            rows.append(
                list(contract) + [
                    label,
                    period_start,
                    period_end,
                    Decimal(days),
                    raw_amount,
                    allocated_amount,
                    index in remainder_indexes,
                ]
            )

        if sum(row[output_column_names.index(allocated_column_name)] for row in rows) != quantized_amount:
            raise RuntimeError('Contract %r allocated total differs from the original amount.'
                               % contract_key)

        contract_tables.append(agate.Table(rows, output_column_names, output_column_types))

    if not contract_tables:
        return agate.Table([], output_column_names, output_column_types)

    return agate.Table.merge(contract_tables)


def reconcile_proration(
    prorated,
    contracts,
    key_column_name,
    start_column_name,
    end_column_name,
    amount_column_name,
    precision=2,
    rounding=ROUND_HALF_UP,
    allocated_column_name='allocated_amount',
    days_column_name='covered_days',
):
    """
    Reconcile prorated rows back to the original contracts.

    Returns an agate :class:`.Table` with one row per contract showing the
    original and quantized original amount, the allocated total, the difference,
    the contract and covered day counts, and boolean match flags. A non-zero
    difference or a failed day match means the proration is wrong.
    """
    grouped = prorated.group_by(key_column_name).aggregate([
        ('allocated_total', agate.Sum(allocated_column_name)),
        ('covered_days_total', agate.Sum(days_column_name)),
    ])

    totals_by_key = {
        row[key_column_name]: (row['allocated_total'], int(row['covered_days_total']))
        for row in grouped.rows
    }

    key_type = contracts.columns[key_column_name].data_type
    reconciliation_rows = []

    for contract in contracts.rows:
        contract_key = contract[key_column_name]
        amount = contract[amount_column_name]
        _, quantized_amount = _quantize_minor_units(amount, precision, rounding)

        allocated_total, covered_days = totals_by_key[contract_key]
        contract_days = (contract[end_column_name] - contract[start_column_name]).days + 1
        difference = quantized_amount - allocated_total

        reconciliation_rows.append([
            contract_key,
            amount,
            quantized_amount,
            allocated_total,
            difference,
            Decimal(contract_days),
            Decimal(covered_days),
            covered_days == contract_days,
            difference == 0,
        ])

    return agate.Table(
        reconciliation_rows,
        [
            key_column_name, 'original_amount', 'quantized_original_amount',
            'allocated_total', 'difference', 'contract_days', 'covered_days',
            'days_match', 'amount_match',
        ],
        [
            key_type, agate.Number(), agate.Number(), agate.Number(),
            agate.Number(), agate.Number(), agate.Number(),
            agate.Boolean(), agate.Boolean(),
        ],
    )


def assert_reconciled(reconciliation):
    """
    Raise :class:`AssertionError` if any contract fails the day or money checks.
    """
    failures = [
        row for row in reconciliation.rows
        if not row['days_match'] or not row['amount_match']
    ]

    if failures:
        labels = [str(row[reconciliation.column_names[0]]) for row in failures]
        raise AssertionError('Proration does not reconcile for contracts: %s' % ', '.join(labels))
