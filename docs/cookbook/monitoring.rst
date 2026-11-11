=============================
Monitoring distribution drift
=============================

The Population Stability Index (PSI) measures how much a sample's distribution
has shifted relative to a fixed reference distribution. It is commonly used in
data validation to compare each daily batch against a baseline.

PSI is defined over a fixed set of bins as::

    PSI = sum((p_batch - p_baseline) * ln(p_batch / p_baseline))

A PSI of zero means the distributions are identical. agate only computes the
metric and its per-bin decomposition; it does not define what value counts as
"too much" drift, because that threshold is a business decision.

Declaring a baseline
====================

A :class:`.PSIBaseline` freezes the bins once, from either an explicit list of
categories or explicit numeric boundaries. The same baseline is then reused for
every later batch so the bins can never silently change.

For a categorical column, declare the complete set of accepted categories.
Categories absent from the baseline still receive a zero-count bin, and a
reserved unknown bin (``__unknown__`` by default) catches any value that was
not declared:

.. code-block:: python

    baseline = agate.PSIBaseline.from_categories(
        'region',
        reference_table.columns['region'].values(),
        ['north', 'south', 'east', 'west'])

For a numeric column, declare boundaries. With boundaries
``(0, 10, 20, 30)`` the bins are ``[0, 10)``, ``[10, 20)`` and ``[20, 30]``;
values outside the range go to the unknown bin:

.. code-block:: python

    baseline = agate.PSIBaseline.from_bins(
        'amount',
        reference_table.columns['amount'].values(),
        [0, 10, 20, 30])

The full declaration, including the smoothing parameter and missing-value
policy, is available for audit from :attr:`.PSIBaseline.config`.

Comparing a batch
=================

Apply :class:`.PSI` to each batch table with the same baseline. The returned
:class:`.PSIResult` exposes both the total and one row per fixed bin:

.. code-block:: python

    result = daily_batch.aggregate(agate.PSI(baseline))

    result.psi            # total PSI, or None for an empty batch
    result.contributions  # bin label -> per-bin contribution
    result.table.print_table()

The result table contains each bin's baseline and batch counts, both smoothed
proportions and the bin's contribution to the total, so the source of any shift
is directly visible.

Policies for edge cases
=======================

New categories and out-of-range values are never dropped. They are counted in
the unknown bin, :attr:`.PSIResult.has_unknown_values` is set to ``True`` and a
:class:`.PSINewCategoryWarning` is emitted:

.. code-block:: python

    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        result = batch.aggregate(agate.PSI(baseline))

    new_categories = any(
        warning.category is agate.PSINewCategoryWarning for warning in caught)

Missing values follow one of three policies chosen when the baseline is built:

* ``agate.MISSING_SEPARATE`` (default): nulls are counted in a dedicated,
  separately labeled missing bin.
* ``agate.MISSING_EXCLUDE``: nulls are excluded from both distributions.
* ``agate.MISSING_UNKNOWN``: nulls are counted in the unknown bin.

Zero-frequency bins are kept rather than discarded. A smoothing count (0.5 by
default, overridable with ``smoothing``) is added to every bin before
proportions are calculated, so a zero-frequency bin still has a defined
proportion and contribution. Bins with a zero raw count are listed in
:attr:`.PSIResult.zero_frequency_bins`. Setting ``smoothing=0`` is rejected if
any bin is empty, because PSI would then be undefined.

An empty batch is inconclusive
==============================

If a batch contains no usable observations (for example, an empty file or only
excluded nulls), no PSI value can be calculated. The result has status
``agate.PSI_STATUS_EMPTY_BATCH``, :attr:`.PSIResult.psi` is ``None`` and a
:class:`.PSIEmptyBatchWarning` is emitted. This must not be reported as "no
drift": there was no evidence to compare.

.. code-block:: python

    result = batch.aggregate(agate.PSI(baseline))

    if result.status == agate.PSI_STATUS_EMPTY_BATCH:
        print('No data received; drift could not be assessed.')
    else:
        print('PSI:', result.psi)
