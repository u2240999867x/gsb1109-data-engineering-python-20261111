import warnings


class NullCalculationWarning(RuntimeWarning):  # pragma: no cover
    """
    Warning raised if a calculation which can not logically
    account for null values is performed on a :class:`.Column` containing
    nulls.
    """
    pass


def warn_null_calculation(operation, column):
    """
    Warn that a calculation is being performed on a column containing nulls.

    :param operation:
        The aggregation or computation being performed.
    :param column:
        The :class:`.Column` containing null values.
    """
    warnings.warn('Column "{}" contains nulls. These will be excluded from {} calculation.'.format(
        column.name,
        operation.__class__.__name__
    ), NullCalculationWarning, stacklevel=2)


class DuplicateColumnWarning(RuntimeWarning):  # pragma: no cover
    """
    Warning raised if multiple columns with the same name are added to a new
    :class:`.Table`.
    """
    pass


def warn_duplicate_column(column_name, column_rename):
    """
    Warn that a column name already exists and the column will be renamed.

    :param column_name:
        The name of the column that already exists in the table.
    :param column_rename:
        The new name that will be used for the column.
    """
    warnings.warn('Column name "{}" already exists in Table. Column will be renamed to "{}".'.format(
        column_name,
        column_rename
    ), DuplicateColumnWarning, stacklevel=2)


class UnnamedColumnWarning(RuntimeWarning):  # pragma: no cover
    """
    Warning raised when a column has no name and an a programmatically generated
    name is used.
    """
    pass


def warn_unnamed_column(column_id, new_column_name):
    """
    Warn that a column has no name and a generated name will be used.

    :param column_id:
        The index of the unnamed column.
    :param new_column_name:
        The generated name that will be used for the column.
    """
    warnings.warn('Column %i has no name. Using "%s".' % (
        column_id,
        new_column_name
    ), UnnamedColumnWarning, stacklevel=2)


class PSINewCategoryWarning(RuntimeWarning):  # pragma: no cover
    """
    Warning raised when a batch contains non-null values outside the categories
    or numeric boundaries declared by a :class:`.PSIBaseline`. The values are
    counted in the baseline's reserved unknown bin rather than discarded.
    """
    pass


class PSIEmptyBatchWarning(RuntimeWarning):  # pragma: no cover
    """
    Warning raised when a batch has no usable observations for a
    :class:`.PSI` calculation. PSI is not calculated and the result is
    inconclusive, not evidence that no drift exists.
    """
    pass
