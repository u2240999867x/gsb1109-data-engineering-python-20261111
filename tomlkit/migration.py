from __future__ import annotations

import copy

from tomlkit.container import Container
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import MigrationError
from tomlkit.exceptions import NonExistentKey
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT
from tomlkit.items import Key
from tomlkit.items import SingleKey
from tomlkit.items import Table


def _as_key(key: Key | str) -> SingleKey:
    if isinstance(key, Key):
        # A dotted key cannot name one table at this level; only a single key
        # fragment can resolve to an item in the container.
        fragments = tuple(key)
        if len(fragments) != 1:
            msg = (
                "a table to migrate must be named by a single key, "
                f"not the dotted key {key.as_string()!r}"
            )
            raise MigrationError(msg)
        return fragments[0]
    return SingleKey(key)


def _fragments(container: Container, key: SingleKey) -> list[Table]:
    """Resolve the table named by ``key`` to the fragments that define it.

    A plain table is a single fragment. A table split out of order into several
    header-less continuations is stored as an :class:`OutOfOrderTableProxy`,
    whose ordered fragment tables are returned in document order.
    """
    try:
        target = container.item(key)
    except NonExistentKey as e:
        raise MigrationError(f"no table named {key.as_string()!r} to migrate") from e

    if isinstance(target, OutOfOrderTableProxy):
        return list(target._tables)
    if isinstance(target, Table):
        return [target]

    raise MigrationError(
        f"{key.as_string()!r} is a {type(target).__name__}, not a table"
    )


def table_to_aot(container: Container, key: Key | str) -> AoT:
    """Migrate a single table named by ``key`` into a one-element array of tables.

    The old configuration allowed exactly one data source written as a plain
    table (``[source]``). The new layout expects an array of tables
    (``[[source]]``). This performs that structural migration while preserving
    every value, comment and unrelated table:

    * nested configuration written under the table (``[source.pool]``) and
      sub-tables declared separately later in the document stay attached to the
      same source and become children of the single array element;
    * the original item is only replaced once every fragment has been
      attributed, so a failure leaves the document unchanged.

    A declaration whose ownership cannot be determined is rejected rather than
    guessed: a later same-name fragment that opens its own ``[header]`` cannot
    be folded into the element, and fragments that would redefine an existing
    key raise :class:`~tomlkit.exceptions.MigrationError`. In particular, later
    sub-tables are never all swept into the first element without proof that
    they belong to this table.

    :param container: the container holding the table, e.g. a parsed document
        or a parent table.
    :param key: the key of the table to migrate.
    :returns: the new :class:`~tomlkit.items.AoT` that replaced the table.
    """
    single_key = _as_key(key)
    fragments = _fragments(container, single_key)

    # Every continuation after the first fragment is a header-less super table
    # materialized from a deeper [source.child] header. A fragment that opens
    # its own header would define a second, independent table of the same name
    # (invalid TOML anyway); folding it in would silently mis-attribute it, so
    # refuse instead of guessing where it belongs.
    for fragment in fragments[1:]:
        if not fragment.is_super_table():
            raise MigrationError(
                "cannot migrate "
                f"{single_key.as_string()!r}: a later table with the same name "
                "opens its own header, so its ownership cannot be determined"
            )

    # Build the element on copies: the source document is not touched until the
    # assignment at the end, which keeps the migration atomic on failure.
    element = copy.deepcopy(fragments[0])
    element._is_aot_element = True
    element._is_super_table = False
    # A parsed table carries a fully-qualified display name (e.g. "source.pool");
    # as an AoT element its headers are re-derived relative to the element, so
    # the stale name must be dropped.
    element.display_name = None

    for fragment in fragments[1:]:
        for child_key, child_item in fragment.value.body:
            try:
                element.value.append(child_key, copy.deepcopy(child_item))
            except TOMLKitError as e:
                if child_key is None:
                    detail = "a comment or whitespace entry cannot be merged"
                else:
                    detail = (
                        "a later fragment redefines an existing key "
                        f"({child_key.as_string()!r})"
                    )
                raise MigrationError(
                    f"cannot migrate {single_key.as_string()!r}: {detail}, so its "
                    "ownership cannot be determined"
                ) from e

    new_aot = AoT([element], name=single_key.key, parsed=True)
    container[single_key] = new_aot

    return new_aot
