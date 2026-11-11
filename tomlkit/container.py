from __future__ import annotations

import copy
import math

from collections.abc import Iterator
from collections.abc import Sequence
from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from typing import Self

from tomlkit._compat import decode
from tomlkit._types import _CustomDict
from tomlkit._utils import merge_dicts
from tomlkit.exceptions import InvalidStringError
from tomlkit.exceptions import KeyAlreadyPresent
from tomlkit.exceptions import KeyRenameError
from tomlkit.exceptions import NonExistentKey
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT
from tomlkit.items import Comment
from tomlkit.items import DottedKey
from tomlkit.items import InlineTable
from tomlkit.items import Item
from tomlkit.items import Key
from tomlkit.items import Null
from tomlkit.items import SingleKey
from tomlkit.items import String
from tomlkit.items import StringType
from tomlkit.items import Table
from tomlkit.items import Trivia
from tomlkit.items import Whitespace
from tomlkit.items import item as _item


def _is_empty_aot(item: Item) -> bool:
    """Whether ``item`` is an array of tables with no elements left."""
    return isinstance(item, AoT) and not item.body


def _emits_header(key: Key, table: Table) -> bool:
    """Whether rendering ``table`` under ``key`` emits a ``[header]`` line.

    A super table normally renders as nothing but the path prefix of its
    children, so it opens no scope of its own.
    """
    return (
        not table.is_super_table()
        or (
            any(
                not isinstance(v, (Table, AoT, Whitespace, Null))
                for _, v in table.value.body
            )
            and not key.is_dotted()
        )
        or (
            any(
                k is not None and k.is_dotted()
                for k, v in table.value.body
                if isinstance(v, Table)
            )
            and not key.is_dotted()
        )
    )


def _header_less_aots(
    body: list[tuple[Key | None, Item]], prefix: str = ""
) -> list[tuple[str, AoT]]:
    """Every emptied array of tables reachable without crossing a header.

    An emptied array of tables has no ``[[key]]`` header left to render, so it
    falls back to the inline ``key = []`` form -- and TOML reads a bare
    key/value pair into whichever table the closest preceding header opened.
    So the fallbacks of a scope have to be written at the top of that scope,
    with a key relative to it, and that includes the ones sitting inside super
    tables, which emit no header to separate them.

    Returns ``(dotted key, aot)`` pairs, keyed relative to the scope ``body``
    belongs to.
    """
    found: list[tuple[str, AoT]] = []
    for k, v in body:
        if k is None:
            continue
        path = f"{prefix}.{k.as_string()}" if prefix else k.as_string()
        if _is_empty_aot(v):
            found.append((path, v))
        elif isinstance(v, Table) and not _emits_header(k, v):
            found.extend(_header_less_aots(v.value.body, path))
    return found


def _render_empty_aot(key: str, aot: AoT) -> str:
    """Render an emptied array of tables as an inline empty array."""
    return (
        f"{aot.trivia.indent}"
        f"{decode(key)}"
        f" = []"
        f"{aot.trivia.comment_ws}"
        f"{decode(aot.trivia.comment)}"
        f"{aot.trivia.trail or chr(10)}"
    )


PathPart = SingleKey | int


def _coerce_key_fragments(
    path: str | Key | Sequence[str | int | Key],
) -> list[PathPart]:
    """Turn a dotted path into keys.

    Strings and ``Key`` parts name table entries; an integer part indexes an
    array of tables element (e.g. ``("servers", 0, "host")``).
    """
    if isinstance(path, Key):
        return list(path)
    if isinstance(path, str):
        path = path.split(".")
    fragments: list[PathPart] = []
    for part in path:
        if isinstance(part, int):
            fragments.append(part)
        elif isinstance(part, SingleKey):
            fragments.append(part)
        elif isinstance(part, Key):
            if len(part._keys) != 1:
                raise ValueError("Each path part must name a single key")
            fragments.append(next(iter(part)))
        else:
            fragments.append(SingleKey(part))
    if not fragments:
        raise ValueError("A key path cannot be empty")
    return fragments


def _dotted_path_string(fragments: Sequence[PathPart]) -> str:
    return ".".join(
        str(part) if isinstance(part, int) else part.key for part in fragments
    )


def _header_fragments(fragments: Sequence[PathPart]) -> list[SingleKey]:
    """Drop array-of-tables element indices from a header path."""
    return [part for part in fragments if isinstance(part, SingleKey)]


def _find_body_entry(
    container: Container, key: SingleKey
) -> tuple[int, Key, Item] | None:
    """Locate the (first) body entry stored under ``key`` in ``container``."""
    index = container._map.get(key)
    if index is None:
        return None
    body_index = index[0] if isinstance(index, tuple) else index
    body_key, item = container._body[body_index]
    assert body_key is not None
    return body_index, body_key, item


def _invalidate_table_names(table: Table) -> None:
    """Drop cached header names of ``table`` and every table nested in it."""
    table.display_name = None
    table.name = None
    for _, child in table.value.body:
        if isinstance(child, Table):
            _invalidate_table_names(child)
        elif isinstance(child, AoT):
            for element in child.body:
                _invalidate_table_names(element)


def _rename_table_entry(
    holder: _EntryHolder,
    new_fragment: SingleKey,
    target_path: Sequence[SingleKey],
) -> None:
    """Rename the table/AoT held by ``holder`` in place.

    The header path is rebuilt from the new fragments and cached display
    names inside the table are invalidated, so every nested ``[header]``
    picks up the renamed prefix on the next render.
    """
    container = holder.container
    body_index = holder.index
    old_key = holder.key
    item = holder.item
    if isinstance(item, Table):
        _invalidate_table_names(item)
        rendered_key = (
            DottedKey(list(target_path))
            if len(target_path) > 1
            else target_path[0]
        )
        item.display_name = rendered_key.as_string()
        item.name = new_fragment.key
        container._body[body_index] = (new_fragment, item)
    else:
        assert isinstance(item, AoT)
        item.name = new_fragment.key
        for element in item.body:
            _invalidate_table_names(element)
            element.display_name = ".".join(part.as_string() for part in target_path)
        container._body[body_index] = (new_fragment, item)

    del container._map[old_key]
    container._map[new_fragment] = holder.map_index
    for position, table_key in enumerate(container._table_keys):
        if table_key == old_key:
            container._table_keys[position] = new_fragment
    dict.__delitem__(container, old_key.key)
    dict.__setitem__(container, new_fragment.key, item.value)
    container._validation_cache.clear()
    holder.key = new_fragment
    holder.source_path = list(target_path)


def _rename_leaf_entry(holder: _EntryHolder, new_fragment: SingleKey) -> None:
    """Rename a plain (non-table) entry in place, keeping its trivia."""
    container = holder.container
    body_index = holder.index
    old_key = holder.key
    item = holder.item
    if isinstance(item, (Table, AoT)):
        raise TypeError("Tables and arrays of tables need _rename_table_entry()")
    # The parser keeps the whitespace between the key and the separator inside
    # the key's original text (e.g. "timeout " + "= "); carry it over so the
    # spacing around "=" is preserved byte for byte.
    trailing_ws = old_key._original[len(old_key._original.rstrip(" \t")) :]
    replacement = SingleKey(
        new_fragment.key,
        t=new_fragment.t,
        sep=old_key.sep,
        original=new_fragment._original + trailing_ws,
    )
    replacement._dotted = old_key.is_dotted()
    if old_key.is_multi():
        # A dotted leaf (`a.b.c = 1`) is stored as a chain of super tables;
        # rebuild the key chain so the inline path renders with the new name.
        new_key = DottedKey([*old_key._keys[:-1], replacement], sep=old_key.sep)
    else:
        new_key = replacement
    container._body[body_index] = (new_key, item)
    del container._map[old_key]
    container._map[new_key] = holder.map_index
    dict.__delitem__(container, old_key.key)
    dict.__setitem__(container, new_key.key, item.value)
    container._validation_cache.clear()
    holder.key = new_key


def _locate_entry(
    root: Container, fragments: Sequence[PathPart]
) -> _EntryHolder:
    """Resolve ``fragments`` to the body entry of its last key.

    Out-of-order tables are resolved through their proxy so that renaming a
    key spread across several ``[table]`` parts rewrites every part, and
    integer fragments select an array-of-tables element. Raises
    ``NonExistentKey`` as soon as a prefix is missing.
    """
    current: Container | OutOfOrderTableProxy | AoT = root
    walked: list[PathPart] = []
    for depth, fragment in enumerate(fragments):
        is_last = depth == len(fragments) - 1
        if isinstance(fragment, int):
            if not isinstance(current, AoT):
                raise NonExistentKey(_dotted_path_string(fragments))
            try:
                element = current.body[fragment]
            except IndexError:
                raise NonExistentKey(_dotted_path_string(fragments)) from None
            current = element.value
            walked.append(fragment)
            continue
        if fragment not in current:
            raise NonExistentKey(_dotted_path_string(fragments))
        if is_last:
            if isinstance(current, OutOfOrderTableProxy):
                return _locate_proxy_entry(current, fragment, [*walked, fragment])
            index = current._map[fragment]
            if isinstance(index, tuple):
                proxy = current.item(fragment)
                assert isinstance(proxy, OutOfOrderTableProxy)
                return _locate_proxy_entry(proxy, fragment, [*walked, fragment])
            body_key, item = current._body[index]
            assert body_key is not None
            return _EntryHolder(
                current, index, body_key, item, [*walked, fragment]
            )
        child = current[fragment]
        if isinstance(child, OutOfOrderTableProxy):
            current = child
            walked.append(fragment)
            continue
        if isinstance(child, (Table, InlineTable)):
            current = child.value
            walked.append(fragment)
            continue
        if isinstance(child, AoT):
            current = child
            walked.append(fragment)
            continue
        raise NonExistentKey(_dotted_path_string(fragments))
    raise NonExistentKey(_dotted_path_string(fragments))


def _locate_proxy_entry(
    proxy: OutOfOrderTableProxy,
    fragment: SingleKey,
    source_path: Sequence[PathPart],
) -> _EntryHolder:
    """Find the body entry backing ``fragment`` inside an out-of-order table."""
    part_indices = proxy._tables_map.get(fragment)
    if part_indices is None:
        raise NonExistentKey(_dotted_path_string(source_path))
    table = proxy._tables[part_indices[0]]
    map_index = table.value._map[fragment]
    index = map_index[0] if isinstance(map_index, tuple) else map_index
    body_key, item = table.value._body[index]
    assert body_key is not None
    return _EntryHolder(
        table.value,
        index,
        body_key,
        item,
        list(source_path),
        proxy=proxy,
        map_index=map_index,
    )


def _ensure_target_available(
    root: Container,
    source_fragments: Sequence[PathPart],
    target_fragments: Sequence[PathPart],
) -> None:
    """Validate the destination of a rename before anything is mutated.

    Fails if the target already exists, or if reaching it would have to pass
    through an existing key that is not a table (e.g. a scalar that the new
    path would silently turn into an implicit table). Existing super tables
    are safe to extend, matching the rule the parser applies to dotted keys.
    """
    current: Container | OutOfOrderTableProxy | AoT = root
    for depth, fragment in enumerate(target_fragments):
        is_last = depth == len(target_fragments) - 1
        if isinstance(fragment, int):
            if not isinstance(current, AoT):
                raise KeyRenameError(
                    _dotted_path_string(source_fragments),
                    _dotted_path_string(target_fragments),
                    "the target path indexes a non array-of-tables",
                )
            try:
                current = current.body[fragment].value
            except IndexError:
                raise KeyRenameError(
                    _dotted_path_string(source_fragments),
                    _dotted_path_string(target_fragments),
                    "the target array-of-tables index does not exist",
                ) from None
            continue
        if fragment not in current:
            return
        if is_last:
            raise KeyRenameError(
                _dotted_path_string(source_fragments),
                _dotted_path_string(target_fragments),
                "the target key already exists",
            )
        child = current[fragment]
        if isinstance(child, OutOfOrderTableProxy):
            current = child
            continue
        if isinstance(child, (Table, InlineTable)):
            current = child.value
            continue
        if isinstance(child, AoT):
            current = child
            continue
        raise KeyRenameError(
            _dotted_path_string(source_fragments),
            _dotted_path_string(target_fragments),
            f'key "{fragment.key}" would redefine an existing table',
        )


def _materialize_target_container(
    root: Container, target_parent: Sequence[PathPart]
) -> Container:
    """Resolve the target's parent path to a container, creating super tables.

    Existing tables (including header-less super tables created by dotted
    keys) are reused, so entries that render inline keep their form. Missing
    fragments are inserted as ordinary super tables, which stay invisible in
    the output until the relocated entry needs a header of its own.
    """
    current: Container | OutOfOrderTableProxy | AoT = root
    for fragment in target_parent:
        if isinstance(fragment, int):
            assert isinstance(current, AoT)
            current = current.body[fragment].value
            continue
        entry = _find_body_entry(current, fragment)
        if entry is None:
            table = Table(Container(True), Trivia(), False, is_super_table=True)
            assert isinstance(current, Container)
            current.append(fragment, table)
            current = table.value
            continue

        _, _, item = entry
        if isinstance(item, OutOfOrderTableProxy):
            item = item._internal_container
        elif isinstance(item, (Table, InlineTable)):
            item = item.value
        current = item
    assert isinstance(current, Container)
    return current


def _prune_empty_super_tables(
    root: Container, source_parent: Sequence[PathPart]
) -> None:
    """Remove implicit super tables left empty by moving their last entry out.

    Only header-less (dotted/implicit) super tables are pruned; an explicit
    ``[table]`` that becomes empty is kept, matching the behaviour of ``del``
    on the table key. Out-of-order tables are left for the proxy machinery.
    """
    chain: list[tuple[Container, SingleKey]] = []
    current: Container | OutOfOrderTableProxy = root
    for fragment in source_parent:
        if isinstance(fragment, int):
            assert isinstance(current, AoT)
            current = current.body[fragment].value
            continue
        if fragment not in current or isinstance(current, OutOfOrderTableProxy):
            return
        item = current.item(fragment)
        if not isinstance(item, Table):
            return
        chain.append((current, fragment))
        current = item.value

    for container, fragment in reversed(chain):
        item = container.item(fragment)
        if (
            isinstance(item, Table)
            and item._is_super_table is True
            and not len(item.value)
        ):
            container.remove(fragment)


class _EntryHolder:
    """A live reference to one body entry involved in a rename."""

    def __init__(
        self,
        container: Container,
        index: int,
        key: Key,
        item: Item,
        source_path: Sequence[PathPart],
        proxy: OutOfOrderTableProxy | None = None,
        map_index: int | tuple[int, ...] | None = None,
    ) -> None:
        self.container = container
        self.index = index
        self.key = key
        self.item = item
        self.source_path = list(source_path)
        # Set when the entry is reached through an out-of-order table proxy,
        # whose merged view has to be re-keyed in addition to the live part.
        self.proxy = proxy
        # The (possibly tuple) index backing this key in ``container._map``;
        # a tuple means the entry is an out-of-order table split into parts.
        self.map_index = map_index if map_index is not None else index

    @property
    def is_table(self) -> bool:
        return isinstance(self.item, (Table, AoT))


class Container(_CustomDict):  # type: ignore[type-arg]
    """
    A container for items within a TOMLDocument.

    This class implements the `dict` interface with copy/deepcopy protocol.
    """

    def __init__(self, parsed: bool = False) -> None:
        self._map: dict[Key, int | tuple[int, ...]] = {}
        self._body: list[tuple[Key | None, Item]] = []
        self._parsed = parsed
        self._table_keys: list[Key] = []
        # number of already-validated fragments and the temp container they
        # were merged into, per out-of-order key; lets parse-time validation
        # resume where the previous pass stopped instead of re-merging every
        # fragment (quadratic) on each append
        self._validation_cache: dict[Key, tuple[int, Container]] = {}
        # superset of the keys mapped to an index tuple, so validating all
        # out-of-order tables doesn't have to scan every key in the map;
        # stale entries are filtered by the per-key isinstance check
        self._out_of_order_keys: set[Key] = set()

    @property
    def body(self) -> list[tuple[Key | None, Item]]:
        return self._body

    def unwrap(self) -> dict[str, Any]:
        """Returns as pure python object (ppo)"""
        unwrapped: dict[str, Any] = {}
        # Resolve each key straight from _map, which already holds the parsed
        # Key objects and their body index, instead of via self.items(): the
        # inherited MutableMapping iteration goes through __getitem__, which
        # rebuilds a SingleKey from the bare string on every key only to throw
        # it away. Out-of-order keys (a tuple index) still go through
        # OutOfOrderTableProxy so their validation (and fragment merge) runs
        # exactly as before. _map is keyed for lookup, not ordered: replacing a
        # value re-inserts its key and moves it last, so take the order from the
        # body index, which is what dumps() and items() follow.
        for key, idx in sorted(
            self._map.items(),
            key=lambda item: item[1][0] if isinstance(item[1], tuple) else item[1],
        ):
            if isinstance(idx, tuple):
                value: Any = OutOfOrderTableProxy(self, idx)
            else:
                value = self._body[idx][1]
            unwrapped[key.key] = value.unwrap() if hasattr(value, "unwrap") else value

        return unwrapped

    @property
    def value(self) -> dict[str, Any]:
        """The wrapped dict value"""
        d: dict[str, Any] = {}
        for k, v in self._body:
            if k is None:
                continue

            key_str = k.key
            val: Any = v.value

            if isinstance(val, Container):
                val = val.value

            if key_str in d:
                merge_dicts(d[key_str], val)
            else:
                d[key_str] = val

        return d

    def parsing(self, parsing: bool) -> None:
        self._parsed = parsing
        self._validation_cache.clear()

        for _, v in self._body:
            if isinstance(v, Table):
                v.value.parsing(parsing)
            elif isinstance(v, AoT):
                for t in v.body:
                    t.value.parsing(parsing)

    def add(self, key: Key | Item | str, item: Any = None) -> Container:
        """
        Adds an item to the current Container.

        :Example:

        >>> # add a key-value pair
        >>> doc.add('key', 'value')
        >>> # add a comment or whitespace or newline
        >>> doc.add(comment('# comment'))
        """
        if item is None:
            if not isinstance(key, (Comment, Whitespace)):
                raise ValueError(
                    "Non comment/whitespace items must have an associated key"
                )

            return self.append(None, key)

        assert not isinstance(key, Item)
        return self.append(key, item)

    def _handle_dotted_key(self, key: Key, value: Item) -> None:
        if isinstance(value, (Table, AoT)):
            raise TOMLKitError("Can't add a table to a dotted key")
        name, *mid, last = key
        name._dotted = True
        table = current = Table(Container(True), Trivia(), False, is_super_table=True)
        for _name in mid:
            _name._dotted = True
            new_table = Table(Container(True), Trivia(), False, is_super_table=True)
            current.append(_name, new_table)
            current = new_table

        last.sep = key.sep
        current.append(last, value)

        self.append(name, table)
        return

    def _get_last_index_before_table(self) -> int:
        last_index = -1
        for i, (k, v) in enumerate(self._body):
            if isinstance(v, Null):
                continue  # Null elements are inserted after deletion

            if isinstance(v, Whitespace) and not v.is_fixed():
                continue

            if isinstance(v, (Table, AoT)) and k is not None and not k.is_dotted():
                break

            if (
                isinstance(v, Table)
                and k is not None
                and k.is_dotted()
                and self._renders_table_header(v)
            ):
                # A dotted-key super table renders inline (`a.b = 1`) only as
                # long as none of its children render a `[table]` header; once
                # one does, anything appended after it would land inside that
                # table's scope.
                break
            last_index = i
        return last_index + 1

    def _renders_table_header(self, table: Table) -> bool:
        for k, v in table.value.body:
            if isinstance(v, AoT):
                return True
            if isinstance(v, Table):
                if k is not None and k.is_dotted() and v.is_super_table():
                    if self._renders_table_header(v):
                        return True
                else:
                    return True
        return False

    def _validate_out_of_order_table(self, key: Key | None = None) -> None:
        if key is None:
            for k in list(self._out_of_order_keys):
                assert k is not None
                self._validate_out_of_order_table(k)
            return
        if key not in self._map:
            return
        current_idx = self._map[key]
        if not isinstance(current_idx, tuple):
            return
        if self._parsed:
            # while parsing, every fragment appended to an out-of-order key
            # triggers a validation pass; resume from the cached temp
            # container so each fragment is merged (and deep-copied) once
            # instead of on every later pass. Fragments are only ever
            # appended during parsing, so a count prefix stays valid; any
            # other mutation clears the cache.
            validated, temp = self._validation_cache.get(key, (0, None))
            if validated > len(current_idx):
                validated, temp = 0, None
            try:
                temp = OutOfOrderTableProxy.validate(
                    self, current_idx[validated:], temp
                )
            except Exception:
                # the temp container may be partially mutated; don't let a
                # caught-and-retried failure resume from a poisoned cache
                self._validation_cache.pop(key, None)
                raise
            self._validation_cache[key] = (len(current_idx), temp)
            return
        OutOfOrderTableProxy.validate(self, current_idx)

    def append(
        self, key: Key | str | None, item: Any, validate: bool = True
    ) -> Container:
        """Similar to :meth:`add` but both key and value must be given."""
        if not isinstance(key, Key) and key is not None:
            key = SingleKey(key)

        if not isinstance(item, Item):
            item = _item(item)

        if key is not None and key.is_multi():
            self._handle_dotted_key(key, item)
            return self

        if isinstance(item, (AoT, Table)) and item.name is None:
            assert isinstance(key, Key)
            item.name = key.key

        prev = self._previous_item()
        prev_ws = isinstance(prev, Whitespace) or ends_with_whitespace(prev)
        if isinstance(item, Table):
            if not self._parsed:
                item.invalidate_display_name()
            if (
                self._body
                and not (self._parsed or item.trivia.indent or prev_ws)
                and key is not None
                and not key.is_dotted()
            ):
                item.trivia.indent = "\n"

        if isinstance(item, AoT) and self._body and not self._parsed:
            item.invalidate_display_name()
            if item and not ("\n" in item[0].trivia.indent or prev_ws):
                item[0].trivia.indent = "\n" + item[0].trivia.indent

        if key is not None and key in self:
            current_idx = self._map[key]
            if isinstance(current_idx, tuple):
                current_body_element = self._body[current_idx[-1]]
            else:
                current_body_element = self._body[current_idx]

            current = current_body_element[1]

            if isinstance(item, Table):
                if not isinstance(current, (Table, AoT)):
                    raise KeyAlreadyPresent(key)

                if item.is_aot_element():
                    # New AoT element found later on
                    # Adding it to the current AoT
                    if not isinstance(current, AoT):
                        current = AoT([current, item], parsed=self._parsed)

                        self._replace(key, key, current)
                    else:
                        current.append(item)

                    return self
                elif isinstance(current, AoT):
                    if not item.is_aot_element():
                        if item.is_super_table() and len(current.body):
                            # A sub-table header such as `[fruit.apple.texture]`
                            # appearing after the array `[[fruit]]` (possibly with
                            # unrelated tables in between) extends the last element
                            # of the array, per the TOML spec.
                            last = current[-1]
                            for k, v in item.value.body:
                                last.value.append(k, v)

                            return self
                        # Tried to define a table after an AoT with the same name.
                        raise KeyAlreadyPresent(key)

                    current.append(item)

                    return self
                elif current.is_super_table():
                    if item.is_super_table():
                        # We need to merge both super tables
                        if (
                            key.is_dotted()
                            or (
                                current_body_element[0] is not None
                                and current_body_element[0].is_dotted()
                            )
                            or self._table_keys[-1] != current_body_element[0]
                        ):
                            if key.is_dotted() and not self._parsed:
                                idx = self._get_last_index_before_table()
                            else:
                                idx = len(self._body)

                            if idx < len(self._body):
                                self._insert_at(idx, key, item)
                            else:
                                self._raw_append(key, item)

                            if validate:
                                self._validate_out_of_order_table(key)

                            return self

                        # Merge the new super table's body into the existing one
                        # in place. Previously this deep-copied `current` before
                        # appending, which is O(size of current) on every merge
                        # and therefore O(n^2) when many subtables share a super
                        # table (e.g. consecutive `[a.b.c]` / `[a.b.d]` headers).
                        # Mutating in place is O(1) per merge. The defensive copy
                        # that protected the out-of-order validation pass has been
                        # moved into OutOfOrderTableProxy (its only consumer).
                        for k, v in item.value.body:
                            current.append(k, v)

                        return self
                    elif (
                        current_body_element[0] is not None
                        and current_body_element[0].is_dotted()
                    ):
                        raise TOMLKitError("Redefinition of an existing table")
                    else:
                        # Merging a concrete table into an existing implicit/super
                        # table is only valid if it does not redefine existing
                        # subtrees via dotted keys and does not change prior types.
                        assert isinstance(current, Table)
                        self._validate_table_candidate(current, item)
                elif not item.is_super_table():
                    raise KeyAlreadyPresent(key)
                else:
                    # An existing concrete table (current) is being extended by
                    # a super-table (item) — e.g. [a] b=1 then [a.b] c=2 out of
                    # order, or [a] b.c=1 then [a.b] d=2.  Validate that the
                    # super-table does not redefine any existing key, raising
                    # early at parse time.  When validation passes, fall through
                    # — _raw_append below will create an out-of-order entry and
                    # preserve table ordering in the document.
                    assert isinstance(current, Table)
                    self._validate_table_candidate(current, item)
            elif isinstance(item, AoT):
                if not isinstance(current, AoT):
                    # Tried to define an AoT after a table with the same name.
                    raise KeyAlreadyPresent(key)

                for table in item.body:
                    current.append(table)

                return self
            else:
                raise KeyAlreadyPresent(key)

        is_table = isinstance(item, (Table, AoT))
        if (
            key is not None
            and self._body
            and not self._parsed
            and (not is_table or key.is_dotted())
        ):
            # If there is already at least one table in the current container
            # and the given item is not a table, we need to find the last
            # item that is not a table and insert after it
            # If no such item exists, insert at the top of the table
            last_index = self._get_last_index_before_table()

            if last_index < len(self._body):
                after_item = self._body[last_index][1]
                if not (
                    isinstance(after_item, Whitespace)
                    or "\n" in after_item.trivia.indent
                ):
                    after_item.trivia.indent = "\n" + after_item.trivia.indent
                return self._insert_at(last_index, key, item)
            else:
                previous_item = self._body[-1][1]
                if isinstance(previous_item, Table) and previous_item.is_super_table():
                    previous_child = previous_item.value._previous_item()
                    if (
                        previous_child is not None
                        and not isinstance(previous_child, Whitespace)
                        and "\n" in previous_item.trivia.trail
                        and "\n" not in previous_child.trivia.trail
                    ):
                        previous_child.trivia.trail += previous_item.trivia.trail
                if not (
                    isinstance(previous_item, Whitespace)
                    or ends_with_whitespace(previous_item)
                    or "\n" in previous_item.trivia.trail
                ):
                    previous_item.trivia.trail += "\n"

        self._raw_append(key, item)
        if validate and key is not None:
            self._validate_out_of_order_table(key)
        return self

    def _validate_table_candidate(self, current: Table, candidate: Table) -> None:
        for k, v in candidate.value.body:
            if k is None:
                continue

            if k in current.value._map:
                existing = current.value.item(k)
                if isinstance(existing, (Table, AoT)) != isinstance(v, (Table, AoT)):
                    raise KeyAlreadyPresent(k)
                if k.is_dotted() and not (
                    isinstance(existing, Table) and existing.is_super_table()
                ):
                    raise TOMLKitError("Redefinition of an existing table")
                if isinstance(existing, Table) and isinstance(v, Table):
                    if not existing.is_super_table() and not v.is_super_table():
                        # Both sides are concrete `[table]` definitions of the
                        # same name; the table is declared twice.
                        raise KeyAlreadyPresent(k)
                    # One side is still an implicit/super table, so a duplicate
                    # (if any) is nested deeper - keep checking the subtree.
                    self._validate_table_candidate(existing, v)
                continue

            if not k.is_dotted():
                # Even when the candidate key itself is not dotted, an
                # existing dotted key may already use it as a prefix —
                # e.g.  [a] b.c=1 then [a.b] d=2  (b prefixes b.c).
                for existing_key in current.value._map:
                    if existing_key.is_dotted() and next(iter(existing_key)) == k:
                        raise TOMLKitError("Redefinition of an existing table")
                continue

            head = next(iter(k))
            if head in current.value._map:
                raise TOMLKitError("Redefinition of an existing table")

    def _raw_append(self, key: Key | None, item: Item) -> None:
        if key is not None and key in self._map:
            current_idx = self._map[key]
            if not isinstance(current_idx, tuple):
                current_idx = (current_idx,)

            current = self._body[current_idx[-1]][1]
            if not isinstance(current, Table):
                raise KeyAlreadyPresent(key)

            self._map[key] = (*current_idx, len(self._body))
            self._out_of_order_keys.add(key)
        elif key is not None:
            self._map[key] = len(self._body)

        self._body.append((key, item))
        if isinstance(item, (Table, AoT)) and key is not None:
            self._table_keys.append(key)

        if key is not None:
            dict.__setitem__(self, key.key, item.value)

    def _remove_at(self, idx: int) -> None:
        key = self._body[idx][0]
        assert key is not None
        index = self._map.get(key)
        if index is None:
            raise NonExistentKey(key)
        self._validation_cache.clear()
        self._body[idx] = (None, Null())

        if isinstance(index, tuple):
            index_list = list(index)
            index_list.remove(idx)
            if len(index_list) == 1:
                self._map[key] = index_list.pop()
            else:
                self._map[key] = tuple(index_list)
        else:
            dict.__delitem__(self, key.key)
            self._map.pop(key)

    def remove(self, key: Key | str) -> Container:
        """Remove a key from the container."""
        if not isinstance(key, Key):
            key = SingleKey(key)

        idx = self._map.pop(key, None)
        if idx is None:
            raise NonExistentKey(key)

        self._validation_cache.clear()
        if isinstance(idx, tuple):
            for i in idx:
                self._body[i] = (None, Null())
        else:
            self._body[idx] = (None, Null())

        dict.__delitem__(self, key.key)

        return self

    def rename(
        self,
        source: str | Key | Sequence[str | int | Key],
        target: str | Key | Sequence[str | int | Key],
    ) -> Container:
        """Rename the key at logical path ``source`` to ``target`` in place.

        Both dotted keys written on one line (``storage.timeout = 1``) and
        keys nested under ``[table]`` headers are addressed by their logical
        path. Integer parts select an array-of-tables element (e.g.
        ``("servers", 0, "host")``). The value keeps its original formatting,
        trivia and comments; the document is never re-encoded through plain
        dicts.

        The destination is checked first: the document is left completely
        untouched (and :class:`KeyRenameError` is raised) when ``target``
        already exists or when reaching it would redefine an existing
        implicit (super) table.
        """
        source_fragments = _coerce_key_fragments(source)
        target_fragments = _coerce_key_fragments(target)

        # Resolve and validate everything before touching a single body entry.
        holder = _locate_entry(self, source_fragments)
        _ensure_target_available(self, source_fragments, target_fragments)

        target_parent = target_fragments[:-1]
        source_parent = holder.source_path[:-1]
        new_fragment = target_fragments[-1]
        assert isinstance(new_fragment, SingleKey)
        source_last = source_fragments[-1]
        assert isinstance(source_last, SingleKey)
        same_container = list(source_parent) == list(target_parent)

        if same_container:
            if holder.is_table:
                target_header = _header_fragments(target_fragments)
                if holder.proxy is not None:
                    holder.proxy._rename_table_parts(
                        source_last, new_fragment, target_header
                    )
                else:
                    _rename_table_entry(holder, new_fragment, target_header)
            else:
                _rename_leaf_entry(holder, new_fragment)
                if holder.proxy is not None:
                    holder.proxy._rekey_merged_view(
                        source_last, new_fragment, holder.item
                    )
        else:
            if holder.proxy is not None:
                holder.proxy._detach_renamed_entry(holder, source_last)
            else:
                # Container.remove() also drops the dict backing and the map
                # entry, so the body slot is released before re-appending.
                holder.container.remove(holder.key)
            if holder.is_table:
                target_header = _header_fragments(target_fragments)
                _invalidate_table_names(holder.item)
                if isinstance(holder.item, Table):
                    holder.item.display_name = (
                        DottedKey(target_header).as_string()
                        if len(target_header) > 1
                        else new_fragment.as_string()
                    )
                    holder.item.name = new_fragment.key
                else:
                    assert isinstance(holder.item, AoT)
                    holder.item.name = new_fragment.key
                    for element in holder.item.body:
                        _invalidate_table_names(element)
                        element.display_name = ".".join(
                            part.as_string() for part in target_header
                        )
            destination = _materialize_target_container(self, target_parent)
            destination.append(new_fragment, holder.item)
            holder.source_path = list(target_fragments)
            if holder.proxy is None:
                _prune_empty_super_tables(self, source_parent)

        return self

    def _insert_at(self, idx: int, key: Key | str, item: Any) -> Container:
        if idx > len(self._body) - 1:
            raise ValueError(f"Unable to insert at position {idx}")

        if not isinstance(key, Key):
            key = SingleKey(key)

        item = _item(item)

        if idx > 0:
            previous_item = self._body[idx - 1][1]
            if not (
                isinstance(previous_item, Whitespace)
                or ends_with_whitespace(previous_item)
                or isinstance(item, (AoT, Table))
                or "\n" in previous_item.trivia.trail
            ):
                previous_item.trivia.trail += "\n"

        # Increment indices after the current index
        for k, v in self._map.items():
            if isinstance(v, tuple):
                new_indices = []
                for v_ in v:
                    if v_ >= idx:
                        v_ = v_ + 1

                    new_indices.append(v_)

                self._map[k] = tuple(new_indices)
            elif v >= idx:
                self._map[k] = v + 1

        if key in self._map:
            current_idx = self._map[key]
            if not isinstance(current_idx, tuple):
                current_idx = (current_idx,)
            self._map[key] = (*current_idx, idx)
            self._out_of_order_keys.add(key)
        else:
            self._map[key] = idx
        self._body.insert(idx, (key, item))

        dict.__setitem__(self, key.key, item.value)

        return self

    def item(self, key: Key | str) -> Item | OutOfOrderTableProxy:
        """Get an item for the given key."""
        if not isinstance(key, Key):
            key = SingleKey(key)

        idx = self._map.get(key)
        if idx is None:
            raise NonExistentKey(key)

        if isinstance(idx, tuple):
            # The item we are getting is an out of order table
            # so we need a proxy to retrieve the proper objects
            # from the parent container
            return OutOfOrderTableProxy(self, idx)

        return self._body[idx][1]

    def last_item(self) -> Item | None:
        """Get the last item."""
        if self._body:
            return self._body[-1][1]
        return None

    def as_string(self) -> str:
        """Render as TOML string."""
        s = ""
        for path, aot in _header_less_aots(self._body):
            s += _render_empty_aot(path, aot)
        for k, v in self._body:
            if k is not None:
                if _is_empty_aot(v):
                    continue
                if isinstance(v, Table):
                    if (
                        s.strip(" ")
                        and not s.strip(" ").endswith("\n")
                        and "\n" not in v.trivia.indent
                    ):
                        s += "\n"
                    s += self._render_table(k, v)
                elif isinstance(v, AoT):
                    if (
                        s.strip(" ")
                        and not s.strip(" ").endswith("\n")
                        and "\n" not in v.trivia.indent
                    ):
                        s += "\n"
                    s += self._render_aot(k, v)
                else:
                    s += self._render_simple_item(k, v)
            else:
                s += self._render_simple_item(k, v)

        return s

    def _render_table(self, key: Key, table: Table, prefix: str | None = None) -> str:
        cur = ""

        if table.display_name is not None:
            _key = table.display_name
        else:
            _key = key.as_string()

            if prefix is not None:
                _key = prefix + "." + _key

        header_emitted = _emits_header(key, table)
        if header_emitted:
            open_, close = "[", "]"
            if table.is_aot_element():
                open_, close = "[[", "]]"

            newline_in_table_trivia = (
                "\n" if "\n" not in table.trivia.trail and len(table.value) > 0 else ""
            )
            cur += (
                f"{table.trivia.indent}"
                f"{open_}"
                f"{decode(_key)}"
                f"{close}"
                f"{table.trivia.comment_ws}"
                f"{decode(table.trivia.comment)}"
                f"{table.trivia.trail}"
                f"{newline_in_table_trivia}"
            )
        elif table.trivia.indent == "\n":
            cur += table.trivia.indent

        if header_emitted:
            # A super table opens no scope, so its fallbacks belong to -- and
            # were already written by -- the nearest enclosing header instead.
            for path, aot in _header_less_aots(table.value.body):
                cur += _render_empty_aot(path, aot)
        for k, v in table.value.body:
            if k is not None and _is_empty_aot(v):
                continue
            elif isinstance(v, Table):
                if (
                    cur.strip(" ")
                    and not cur.strip(" ").endswith("\n")
                    and "\n" not in v.trivia.indent
                ):
                    cur += "\n"
                assert k is not None
                if v.is_super_table():
                    if k.is_dotted() and not key.is_dotted():
                        # Dotted key inside table
                        cur += self._render_table(k, v)
                    else:
                        cur += self._render_table(k, v, prefix=_key)
                else:
                    cur += self._render_table(k, v, prefix=_key)
            elif isinstance(v, AoT):
                if (
                    cur.strip(" ")
                    and not cur.strip(" ").endswith("\n")
                    and "\n" not in v.trivia.indent
                ):
                    cur += "\n"
                assert k is not None
                cur += self._render_aot(k, v, prefix=_key)
            else:
                cur += self._render_simple_item(
                    k, v, prefix=_key if key.is_dotted() else None
                )

        return cur

    def _render_aot(self, key: Key, aot: AoT, prefix: str | None = None) -> str:
        _key = key.as_string()
        if prefix is not None:
            _key = prefix + "." + _key

        cur = ""
        _key = decode(_key)
        for table in aot.body:
            cur += self._render_aot_table(table, prefix=_key)

        return cur

    def _render_aot_table(self, table: Table, prefix: str | None = None) -> str:
        cur = ""
        _key = prefix or ""
        open_, close = "[[", "]]"

        cur += (
            f"{table.trivia.indent}"
            f"{open_}"
            f"{decode(_key)}"
            f"{close}"
            f"{table.trivia.comment_ws}"
            f"{decode(table.trivia.comment)}"
            f"{table.trivia.trail}"
        )

        for path, aot in _header_less_aots(table.value.body):
            cur += _render_empty_aot(path, aot)
        for k, v in table.value.body:
            if k is not None and _is_empty_aot(v):
                continue
            elif isinstance(v, Table):
                assert k is not None
                if v.is_super_table():
                    if k.is_dotted():
                        # Dotted key inside table
                        cur += self._render_table(k, v)
                    else:
                        cur += self._render_table(k, v, prefix=_key)
                else:
                    cur += self._render_table(k, v, prefix=_key)
            elif isinstance(v, AoT):
                assert k is not None
                cur += self._render_aot(k, v, prefix=_key)
            else:
                cur += self._render_simple_item(k, v)

        return cur

    def _render_simple_item(
        self, key: Key | None, item: Item, prefix: str | None = None
    ) -> str:
        if key is None:
            return item.as_string()

        _key = key.as_string()
        if prefix is not None:
            _key = prefix + "." + _key

        return (
            f"{item.trivia.indent}"
            f"{decode(_key)}"
            f"{key.sep}"
            f"{decode(item.as_string())}"
            f"{item.trivia.comment_ws}"
            f"{decode(item.trivia.comment)}"
            f"{item.trivia.trail}"
        )

    def __len__(self) -> int:
        return dict.__len__(self)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.keys(self))

    # Dictionary methods
    def __getitem__(self, key: Key | str) -> Any:
        item = self.item(key)
        if isinstance(item, Item) and item.is_boolean():
            return item.value

        return item

    def __contains__(self, key: object) -> bool:
        # Native membership test. The inherited ``MutableMapping.__contains__``
        # resolves the value via ``__getitem__``/``item()`` (and builds a
        # ``NonExistentKey`` on every absent key) only to discard it. Resolve the
        # key the same way ``item()`` does -- ``str`` becomes a ``SingleKey``
        # (a non-str/non-``Key`` argument still raises ``TypeError``) -- then
        # probe ``_map`` directly. For an out-of-order table the proxy is still
        # built so its validation runs exactly as before.
        if not isinstance(key, Key):
            key = SingleKey(key)  # type: ignore[arg-type]
        idx = self._map.get(key)
        if idx is None:
            return False
        if isinstance(idx, tuple):
            # during parsing every fragment append re-probes membership;
            # if the validation cache covers exactly these fragments they
            # are already known to merge cleanly, so skip rebuilding the
            # proxy (which is linear in the number of fragments)
            validated, _ = self._validation_cache.get(key, (0, None))
            if not (self._parsed and validated == len(idx)):
                OutOfOrderTableProxy(self, idx)
        return True

    def _item_for_key(
        self, key: Key | str, value: object, parent: Item | None = None
    ) -> Item:
        # Inspect plain strings before conversion, so an explicit String keeps
        # the caller's chosen quoting style.
        if isinstance(value, str) and not isinstance(value, String):
            previous = self.get(key)
            if isinstance(previous, String) and previous._t is StringType.SLL:
                try:
                    return String.from_raw(value, StringType.SLL)
                except InvalidStringError:
                    # Some values need the escaping provided by a basic string.
                    pass
        return _item(value, _parent=parent)

    def __setitem__(self, key: Key | str, value: Any) -> None:
        if key in self:
            old_key = next(filter(lambda k: k == key, self._map))
            value = self._item_for_key(key, value)
            self._replace(old_key, key, value)
        else:
            self.append(key, value)

    def __delitem__(self, key: Key | str) -> None:
        self.remove(key)

    def setdefault(self, key: Key | str, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    def _replace(self, key: Key | str, new_key: Key | str, value: Item) -> None:
        if not isinstance(key, Key):
            key = SingleKey(key)

        idx = self._map.get(key)
        if idx is None:
            raise NonExistentKey(key)

        self._replace_at(idx, new_key, value)

    def _replace_at(
        self, idx: int | tuple[int, ...], new_key: Key | str, value: Item
    ) -> None:
        value = _item(value)
        self._validation_cache.clear()

        if isinstance(idx, tuple):
            for i in idx[1:]:
                self._body[i] = (None, Null())

            idx = idx[0]

        k, v = self._body[idx]
        assert k is not None
        # A dotted key renders its value inline (e.g. ``a.b = 1``), which is only
        # consistent with a super table. When the replacement value renders with
        # its own ``[header]`` instead (a non-super table, or an array of tables),
        # keeping the dotted key duplicates the prefix onto the header (#524) and
        # lets the header swallow following dotted siblings (#542). Drop the dotted
        # key so the replacement renders as a plain table/array of tables.
        dotted_to_header = k.is_dotted() and (
            isinstance(value, AoT)
            or (isinstance(value, Table) and not value.is_super_table())
        )
        # That new header also captures every sibling that renders inline -- plain
        # values and dotted keys -- if any still follow it (#513), so it must be
        # moved past them, exactly as a value-to-table change already is.
        reposition_dotted = dotted_to_header and any(
            not isinstance(cur_val, (Null, Whitespace))
            and not (
                isinstance(cur_val, (Table, AoT))
                and (cur_key is None or not cur_key.is_dotted())
            )
            for cur_key, cur_val in self._body[idx + 1 :]
        )
        if not isinstance(new_key, Key):
            if (
                isinstance(value, (AoT, Table)) != isinstance(v, (AoT, Table))
                or new_key != k.key
                or dotted_to_header
            ):
                new_key = SingleKey(new_key)
            else:  # Inherit the sep of the old key
                new_key = k

        del self._map[k]
        self._map[new_key] = idx
        if new_key != k:
            dict.__delitem__(self, k.key)

        if (
            isinstance(value, (AoT, Table)) != isinstance(v, (AoT, Table))
            or reposition_dotted
        ):
            self.remove(k)
            if isinstance(value, (AoT, Table)):
                # New tables must appear after all entries that render inline:
                # plain values and dotted keys (which are super tables). Skip
                # those and insert before the first real ``[header]`` so the new
                # table cannot swallow a following sibling on round-trip.
                for i in range(idx, len(self._body)):
                    cur_key, cur_val = self._body[i]
                    if isinstance(cur_val, (AoT, Table)) and not (
                        cur_key is not None and cur_key.is_dotted()
                    ):
                        self._insert_at(i, new_key, value)
                        idx = i
                        break
                else:
                    idx = -1
                    self.append(new_key, value)
            else:
                # the replaced table's slot lies in the table region, where a
                # plain value would be captured by the preceding table on
                # round-trip; append() puts it with the other root-level values
                idx = -1
                self.append(new_key, value)
        else:
            # Copying trivia
            if not isinstance(value, (Whitespace, AoT)):
                value.trivia.indent = v.trivia.indent
                value.trivia.comment_ws = value.trivia.comment_ws or v.trivia.comment_ws
                value.trivia.comment = value.trivia.comment or v.trivia.comment
                value.trivia.trail = v.trivia.trail
            self._body[idx] = (new_key, value)

        if value is not v and hasattr(value, "invalidate_display_name"):
            value.invalidate_display_name()

        if isinstance(value, Table):
            # Insert a cosmetic new line for tables if:
            # - it does not have it yet OR is not followed by one
            # - it is not the last item, or
            # - The table being replaced has a newline
            result = self._previous_item_with_index()
            assert result is not None
            last, _ = result
            idx = last if idx < 0 else idx
            has_ws = ends_with_whitespace(value)
            replace_has_ws = (
                isinstance(v, Table)
                and v.value.body
                and isinstance(v.value.body[-1][1], Whitespace)
            )
            next_ws = idx < last and isinstance(self._body[idx + 1][1], Whitespace)
            if (idx < last or replace_has_ws) and not (next_ws or has_ws):
                value.append(None, Whitespace("\n"))

            assert isinstance(new_key, Key)
            dict.__setitem__(self, new_key.key, value.value)

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return repr(self.value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return NotImplemented

        return bool(_equal_with_nan(self.value, other))

    def _getstate(self, protocol: int) -> tuple[bool]:
        return (self._parsed,)

    def __reduce__(self) -> tuple[type, tuple[bool], tuple[Any, ...]]:
        return self.__reduce_ex__(2)

    def __reduce_ex__(self, protocol: int) -> tuple[type, tuple[bool], tuple[Any, ...]]:  # type: ignore[override]
        return (
            self.__class__,
            self._getstate(protocol),
            (self._map, self._body, self._parsed, self._table_keys),
        )

    def __setstate__(self, state: tuple[Any, ...]) -> None:
        self._map = state[0]
        self._body = state[1]
        self._parsed = state[2]
        self._table_keys = state[3]
        self._out_of_order_keys = {
            k for k, v in self._map.items() if isinstance(v, tuple)
        }

        for key, item in self._body:
            if key is not None:
                dict.__setitem__(self, key.key, item.value)

    def copy(self) -> Self:
        return copy.copy(self)

    def __copy__(self) -> Self:
        c = self.__class__(self._parsed)
        for k, v in dict.items(self):
            dict.__setitem__(c, k, v)

        c._body += self.body
        c._map.update(self._map)
        c._out_of_order_keys |= self._out_of_order_keys

        return c

    def _previous_item_with_index(
        self, idx: int | None = None, ignore: tuple[type, ...] = (Null,)
    ) -> tuple[int, Item] | None:
        """Find the immediate previous item before index ``idx``"""
        if idx is None or idx > len(self._body):
            idx = len(self._body)
        for i in range(idx - 1, -1, -1):
            v = self._body[i][-1]
            if not isinstance(v, ignore):
                return i, v
        return None

    def _previous_item(
        self, idx: int | None = None, ignore: tuple[type, ...] = (Null,)
    ) -> Item | None:
        """Find the immediate previous item before index ``idx``.
        If ``idx`` is not given, the last item is returned.
        """
        prev = self._previous_item_with_index(idx, ignore)
        return prev[-1] if prev else None


class OutOfOrderTableProxy(_CustomDict):  # type: ignore[type-arg]
    @staticmethod
    def validate(
        container: Container,
        indices: tuple[int, ...],
        temp_container: Container | None = None,
    ) -> Container:
        """Validate out of order tables in the given container"""
        # Append all items to a temp container to see if there is any error.
        # We deep-copy each value before appending: appending a super table
        # merges it in place into a matching one already in temp_container, and
        # those values are the live subtables of `container`. Without the copy
        # the merge would mutate the caller's tables (and corrupt a later
        # validation pass). The container merge itself is now copy-free for
        # speed, so this is where the isolation lives.
        # Passing a previous pass's temp container back in (with the already
        # validated indices stripped from `indices`) resumes the validation
        # incrementally.
        if temp_container is None:
            temp_container = Container(True)
        for i in indices:
            _, item = container._body[i]

            if isinstance(item, Table):
                for k, v in item.value.body:
                    temp_container.append(k, copy.deepcopy(v), validate=True)

        temp_container._validate_out_of_order_table()
        return temp_container

    def __init__(self, container: Container, indices: tuple[int, ...]) -> None:
        self._container = container
        self._internal_container = Container(True)
        self._tables: list[Table] = []
        self._tables_map: dict[Key, list[int]] = {}

        for i in indices:
            _, _item = self._container._body[i]

            if isinstance(_item, Table):
                self._tables.append(_item)
                table_idx = len(self._tables) - 1
                for k, v in _item.value.body:
                    merged = self._merge_aot_fragment(k, v)
                    if merged is None:
                        self._internal_container._raw_append(k, v)
                    else:
                        v = merged
                    key_indices = self._tables_map.setdefault(k, [])  # type: ignore[arg-type]
                    if table_idx not in key_indices:
                        key_indices.append(table_idx)
                    if k is not None:
                        dict.__setitem__(self, k.key, v)

        self._internal_container._validate_out_of_order_table()

    def _merge_aot_fragment(self, key: Key | None, item: Item) -> AoT | None:
        """
        Merge an array-of-tables fragment from a later out-of-order table part.

        An AoT whose elements are split across out-of-order parts of the same
        table arrives here once per part; ``_raw_append`` only knows how to
        chain duplicate ``Table`` parts and would raise ``KeyAlreadyPresent``.
        The fragments are presented as a new merged ``AoT`` referencing the
        live element tables, without mutating either fragment (the parts keep
        rendering their own elements).

        Returns the merged ``AoT``, or ``None`` if this is not such a fragment.
        """
        internal = self._internal_container
        if key is None or not isinstance(item, AoT) or key not in internal._map:
            return None
        idx = internal._map[key]
        if isinstance(idx, tuple):
            # A tuple index means the key already resolved to several body
            # positions, which here only happens for a degenerate collision
            # (e.g. a non-AoT part sharing the key). Three or more genuine AoT
            # fragments do not reach this branch: each later fragment merges
            # into the single growing AoT below, so ``idx`` stays an int.
            return None
        existing = internal._body[idx][1]
        if not isinstance(existing, AoT):
            return None

        merged = AoT([*existing.body, *item.body], parsed=True)
        internal._body[idx] = (internal._body[idx][0], merged)
        dict.__setitem__(internal, key.key, merged.value)
        return merged

    def unwrap(self) -> dict[str, Any]:
        return self._internal_container.unwrap()

    @property
    def value(self) -> dict[str, Any]:
        return self._internal_container.value

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return repr(self.value)

    def __contains__(self, key: object) -> bool:
        # Native membership test. The inherited ``MutableMapping.__contains__``
        # resolves the value via ``__getitem__`` (and builds a ``NonExistentKey``
        # on every absent key) only to discard it. Probe the internal container
        # directly -- the same predicate ``__getitem__`` already uses -- which is
        # itself a native ``_map`` lookup that still rebuilds the proxy for an
        # out-of-order key so its validation runs exactly as before.
        return key in self._internal_container

    def __getitem__(self, key: Key | str) -> Any:
        if key not in self._internal_container:
            raise NonExistentKey(key)

        return self._internal_container[key]

    def __setitem__(self, key: Key | str, value: Any) -> None:
        from .items import item as _item_fn

        def _is_table_or_aot(it: Any) -> bool:
            return isinstance(_item_fn(it), (Table, AoT))

        _key: Key = key if isinstance(key, Key) else SingleKey(key)

        if _key in self._tables_map:
            # Overwrite the first occurrence and remove this key from later parts.
            map_indices = self._tables_map[_key]
            while len(map_indices) > 1:
                table = self._tables[map_indices.pop()]
                del table[key]
                if not table:
                    self._remove_table(table)
            old_value = self._tables[map_indices[0]][key]
            if _is_table_or_aot(old_value) and not _is_table_or_aot(value):
                # Remove the entry from the map and set value again.
                del self._tables[map_indices[0]][key]
                del self._tables_map[_key]
                self[key] = value
                return
            self._tables[map_indices[0]][key] = value
        elif self._tables:
            table_index = 0
            if not _is_table_or_aot(value):  # if the value is a plain value
                for i, table in enumerate(self._tables):
                    # find the first table that allows plain values
                    if any(not _is_table_or_aot(v) for _, v in table.items()):
                        table_index = i
                        break
                else:
                    # No part holds a plain value yet, so the chosen part must
                    # start rendering its own ``[key]`` header. Prefer a part
                    # that is not a super table (it already renders that header):
                    # turning a header-less super part concrete here would emit a
                    # second, duplicate header next to an existing concrete part
                    # and produce TOML that no longer parses.
                    for i, table in enumerate(self._tables):
                        if not table.is_super_table():
                            table_index = i
                            break
            self._tables[table_index][key] = value
            self._tables_map[_key] = [table_index]
        else:
            self._container[key] = value

        self._internal_container[key] = value
        if key is not None:
            dict.__setitem__(self, key, value)

    def _remove_table(self, table: Table) -> None:
        """Remove table from the parent container"""
        self._tables.remove(table)
        for idx, body_item in enumerate(self._container._body):
            if body_item[1] is table:
                self._container._remove_at(idx)
                break

    def __delitem__(self, key: Key | str) -> None:
        _key: Key = key if isinstance(key, Key) else SingleKey(key)
        if _key not in self._tables_map:
            raise NonExistentKey(key)

        for i in reversed(self._tables_map[_key]):
            table = self._tables[i]
            del table[key]
            if not table and len(self._tables) > 1:
                self._remove_table(table)

        del self._tables_map[_key]
        del self._internal_container[key]
        if key is not None:
            dict.__delitem__(self, key)

    def _rekey_merged_view(
        self, old_key: SingleKey, new_key: SingleKey, value: Item
    ) -> None:
        """Reflect an in-place part rename in the proxy's merged view."""
        self._internal_container.remove(old_key)
        self._internal_container._raw_append(new_key, value)
        if old_key in self._tables_map:
            self._tables_map[new_key] = self._tables_map.pop(old_key)
        dict.__delitem__(self, old_key.key)
        dict.__setitem__(self, new_key.key, value)

    def _rename_table_parts(
        self,
        old_key: SingleKey,
        new_key: SingleKey,
        target_header: Sequence[SingleKey],
    ) -> None:
        """Rename a table/AoT spread across every out-of-order part.

        A key that names a table can be backed by several ``[table]`` parts;
        each part's body entry is rewritten in place (preserving whether it
        renders inline as a dotted super table or with a header), then the
        proxy's merged view is re-keyed to match.
        """
        part_indices = self._tables_map[old_key]
        part_tables = [self._tables[index] for index in part_indices]

        value: Item | None = None
        for part in part_tables:
            map_index = part.value._map[old_key]
            indices = map_index if isinstance(map_index, tuple) else (map_index,)
            for first in indices:
                body_key, item = part.value._body[first]
                assert body_key is not None
                value = item
                replacement_key = SingleKey(
                    new_key.key,
                    t=new_key.t,
                    sep=body_key.sep,
                    original=new_key._original,
                )
                replacement_key._dotted = body_key.is_dotted()
                if isinstance(item, Table):
                    _invalidate_table_names(item)
                    item.name = new_key.key
                    if not body_key.is_dotted():
                        item.display_name = (
                            DottedKey(list(target_header)).as_string()
                            if len(target_header) > 1
                            else new_key.as_string()
                        )
                else:
                    assert isinstance(item, AoT)
                    item.name = new_key.key
                    for element in item.body:
                        _invalidate_table_names(element)
                        if not body_key.is_dotted():
                            element.display_name = ".".join(
                                fragment.as_string()
                                for fragment in target_header
                            )
                del part.value._map[body_key]
                part.value._map[replacement_key] = map_index
                part.value._body[first] = (replacement_key, item)
                for position, table_key in enumerate(part.value._table_keys):
                    if table_key == body_key:
                        part.value._table_keys[position] = replacement_key
                dict.__delitem__(part.value, body_key.key)
                dict.__setitem__(part.value, new_key.key, item.value)
                part.value._validation_cache.clear()

        assert value is not None
        self._tables_map[new_key] = self._tables_map.pop(old_key)
        internal = self._internal_container
        old_index = internal._map.pop(old_key)
        internal._map[new_key] = old_index
        indices = old_index if isinstance(old_index, tuple) else (old_index,)
        for body_index in indices:
            _, table_item = internal._body[body_index]
            internal._body[body_index] = (new_key, table_item)
        dict.__delitem__(self, old_key.key)
        dict.__setitem__(self, new_key.key, value.value)

    def _detach_renamed_entry(
        self, holder: _EntryHolder, old_key: SingleKey
    ) -> None:
        """Drop an entry that is being moved out of an out-of-order table.

        Mirrors :meth:`__delitem__` but keeps the (already detached) item
        object so the caller can re-append it at the destination with its
        trivia intact.
        """
        part_index = self._tables_map[old_key][0]
        table = self._tables[part_index]
        table.value.remove(holder.key)
        dict.__delitem__(table, old_key.key)
        del self._tables_map[old_key]
        del self._internal_container[old_key]
        dict.__delitem__(self, old_key.key)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.keys(self))

    def __len__(self) -> int:
        return dict.__len__(self)

    def setdefault(self, key: Key | str, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]


def ends_with_whitespace(it: Any) -> bool:
    """Returns ``True`` if the given item ``it`` is a ``Table`` or ``AoT`` object
    ending with a ``Whitespace``.
    """
    if isinstance(it, Whitespace):
        return True
    if isinstance(it, Table):
        previous = it.value._previous_item()
        return previous is not None and ends_with_whitespace(previous)
    return isinstance(it, AoT) and len(it) > 0 and ends_with_whitespace(it[-1])


def _equal_with_nan(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return False
        return all(_equal_with_nan(left[k], right[k]) for k in left)

    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_equal_with_nan(l, r) for l, r in zip(left, right))  # noqa: E741

    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return True

    return bool(left == right)
