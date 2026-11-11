from strictyaml.ruamel.comments import CommentedSeq, CommentedMap
from strictyaml.exceptions import raise_type_error, YAMLSerializationError
from strictyaml.yamllocation import YAMLChunk
from strictyaml.dumper import StrictYAMLDumper
from strictyaml.ruamel import dump, scalarstring
from copy import copy, deepcopy
import decimal
import sys


if sys.version_info[0] == 3:
    unicode = str

if sys.version_info[:2] < (3, 7):
    from collections import OrderedDict as OrderedDictBase

    class OrderedDict(OrderedDictBase):
        def __repr__(self):
            return (
                "{"
                + ", ".join("{}: {}".format(repr(k), repr(v)) for k, v in self.items())
                + "}"
            )

else:
    OrderedDict = dict


class YAMLIterator(object):
    def __init__(self, yaml_object):
        self._yaml_object = yaml_object
        self._index = 0

    def __iter__(self):
        return self

    def next(self):
        return self.__next__()

    def __next__(self):
        if self._index >= len(self._yaml_object):
            raise StopIteration
        else:
            self._index = self._index + 1
            return self._yaml_object[self._index - 1]


class YAML(object):
    """
    A YAML object represents a block of YAML which can be:

    * Used to extract parsed data from the YAML (.data).
    * Used to render to a string of YAML, with comments (.as_yaml()).
    * Revalidated with a stricter schema (.revalidate(schema)).
    """

    def __init__(self, value, validator=None):
        if isinstance(value, YAMLChunk):
            self._chunk = value
            self._validator = validator
            if value.is_scalar():
                self._value = validator.validate(value)
                if isinstance(self._value, YAML):
                    self._value = self._value._value
                self._text = value.contents
            else:
                self._value = (
                    value.strictparsed()._value
                    if isinstance(value.strictparsed(), YAML)
                    else value.strictparsed()
                )
                self._text = None
        elif isinstance(value, YAML):
            self._chunk = value._chunk
            self._validator = validator if validator is not None else value.validator
            self._value = value._value
            self._text = value._text
        else:
            self._chunk = YAMLChunk(value)
            self._validator = validator
            self._value = value
            self._text = unicode(value)
        self._selected_validator = None
        assert not isinstance(self._value, YAML)

    def __int__(self):
        # TODO: Raise more sensible exception if not int
        return int(self._value)

    def __str__(self):
        if not self.is_scalar():
            raise TypeError(
                "Cannot cast mapping/sequence '{0}' to string".format(repr(self._value))
            )
        elif type(self._value) in (unicode, str, int, float, decimal.Decimal):
            return unicode(self._value)
        else:
            raise_type_error(
                repr(self), "str", "str(yamlobj.data) or str(yamlobj.text)"
            )

    def __unicode__(self):
        return self.__str__()

    def revalidate(self, schema):
        if self.is_scalar():
            self._value = schema(self._chunk)._value
        else:
            result = schema(self._chunk)
            self._selected_validator = result._selected_validator
        self._validator = schema

    @property
    def data(self):
        """
        Returns raw data representation of the document or document segment.

        Mappings are rendered as ordered dicts, sequences as lists and scalar values
        as whatever the validator returns (int, string, etc.).

        If no validators are used, scalar values are always returned as strings.
        """
        if isinstance(self._value, CommentedMap):
            mapping = OrderedDict()
            for key, value in self._value.items():
                """
                #if isinstance(key, YAML):
                    #mapping[key.data] = value.data if isinstance(value, YAML) else value
                """
                mapping[key.data] = value.data
            return mapping
        elif isinstance(self._value, CommentedSeq):
            return [item.data for item in self._value]
        else:
            if isinstance(self._value, scalarstring.ScalarString):
                return str(self._value)
            return self._value

    def as_marked_up(self):
        """
        Returns strictyaml.ruamel CommentedSeq/CommentedMap objects
        with comments. This can be fed directly into a strictyaml.ruamel
        dumper.
        """
        return self._chunk.contents

    @property
    def is_default(self):
        """
        Return True if this value was supplied by the schema (an Optional
        default) rather than written in the source text.

        Defaults supplied by the schema, including defaults nested inside
        other defaults, are reported as defaults. Values which were explicitly
        set after loading are not defaults.
        """
        return self._chunk.is_default()

    @property
    def value_source(self):
        """
        Return ``"original"`` if this value came from the source text or was
        explicitly written after loading, otherwise ``"default"``.
        """
        return "default" if self.is_default else "original"

    @property
    def source(self):
        """Alias for :attr:`value_source`."""
        return self.value_source

    @property
    def from_default(self):
        """Alias for :attr:`is_default`."""
        return self.is_default

    def is_default_value(self):
        """Alias for :attr:`is_default`."""
        return self.is_default

    @property
    def has_line(self):
        """
        Return True if this value has a corresponding line in the source text.

        Values supplied by schema defaults have no source line, even though
        they are present in memory.
        """
        return not self.is_default

    def provenance(self):
        """
        Return a nested description of where every value came from.

        Mapping children are keyed by their data value, sequences by index.
        Each node is described by ``source`` (``"original"`` or ``"default"``)
        and either ``value`` for scalars or ``children`` for mappings and
        sequences, so defaults nested inside defaults are visible.
        """
        if self.is_mapping():
            return {
                "source": self.value_source,
                "children": {
                    key.data: value.provenance()
                    for key, value in self._value.items()
                },
            }
        elif self.is_sequence():
            return {
                "source": self.value_source,
                "children": [item.provenance() for item in self._value],
            }
        return {"source": self.value_source, "value": self.data}

    def default_keys(self):
        """
        Return the keys of this mapping whose values came from schema defaults.
        """
        if not self.is_mapping():
            raise TypeError(
                "{0} is not a mapping, cannot use .default_keys()".format(repr(self))
            )
        return [
            key.data for key, value in self._value.items() if value.is_default
        ]

    def explicit_keys(self):
        """
        Return the keys of this mapping whose values came from the source text
        or were explicitly written.
        """
        if not self.is_mapping():
            raise TypeError(
                "{0} is not a mapping, cannot use .explicit_keys()".format(repr(self))
            )
        return [
            key.data
            for key, value in self._value.items()
            if not value.is_default
        ]

    def user_keys(self):
        """Alias for :meth:`explicit_keys`."""
        return self.explicit_keys()

    @property
    def start_line(self):
        """
        Return line number that the element starts on (including preceding comments).

        Returns None for values supplied by schema defaults, which have no
        corresponding line in the original document.
        """
        if self.is_default:
            return None
        return self._chunk.start_line()

    @property
    def end_line(self):
        """
        Return line number that the element ends on (including trailing comments).

        Returns None for values supplied by schema defaults, which have no
        corresponding line in the original document.
        """
        if self.is_default:
            return None
        return self._chunk.end_line()

    def lines(self):
        """
        Return a string of the lines which make up the selected line
        including preceding and trailing comments.

        Returns None for values supplied by schema defaults.
        """
        if self.is_default:
            return None
        return self._chunk.lines()

    def lines_before(self, how_many):
        if self.is_default:
            return None
        return self._chunk.lines_before(how_many)

    def lines_after(self, how_many):
        if self.is_default:
            return None
        return self._chunk.lines_after(how_many)

    def __float__(self):
        return float(self._value)

    def __repr__(self):
        return "YAML({0})".format(self.data)

    def __bool__(self):
        if isinstance(self._value, bool):
            return self._value
        else:
            raise_type_error(
                repr(self), "bool", "bool(yamlobj.data) or bool(yamlobj.text)"
            )

    def _strictindex(self, index):
        if isinstance(index, YAML):
            index = index.data
        if self.is_mapping():
            key_validator = (
                self._selected_validator.key_validator
                if self._selected_validator is not None
                else self._validator.key_validator
            )
            return key_validator(YAMLChunk(index)).data
        else:
            return index

    def __nonzero__(self):
        return self.__bool__()

    def __getitem__(self, index):
        return self._value[self._strictindex(index)]

    def __setitem__(self, index, value):
        strictindex = self._strictindex(index)

        # Generate nice error messages - first, copy our whole node's data
        # and use ``to_yaml()`` to determine if the resulting data would
        # validate our schema.  Must replace whole current node to support
        # complex types, e.g. ``EmptyList() | Seq(Str())``.
        if isinstance(value, YAML):
            yaml_value = self._chunk.fork(strictindex, value)
            new_value = self._validator(yaml_value)
        else:
            old_data = self.data
            if isinstance(old_data, dict):
                old_data[index] = value
            elif isinstance(old_data, list):
                if len(old_data) <= index:
                    raise YAMLSerializationError(
                        "cannot extend list via __setitem__.  "
                        "Instead, replace whole list on parent "
                        "node."
                    )
                old_data[index] = value
            else:
                raise NotImplementedError(repr(old_data))
            yaml_value = YAMLChunk(self._validator.to_yaml(old_data))
            yaml_value_repr = self._validator(yaml_value)

            # Now that the new content is properly validated, create a valid
            # chunk with the new information.
            forked_chunk = self._chunk.fork(strictindex, yaml_value_repr[strictindex])
            new_value = self._validator(forked_chunk)

        # Now, overwrite our chunk and value with the new information.
        old_chunk = self._chunk  # Needed for reference to pre-fork ruamel
        self._chunk = new_value._chunk
        self._value = new_value._value
        self._text = new_value._text
        self._selected_validator = new_value._selected_validator
        # Update any parent ruamel links to point to our new chunk.
        self._chunk.pointer.set(old_chunk, "_ruamelparsed", new_value._chunk.contents)
        self._chunk.pointer.set(old_chunk, "_strictparsed", self, strictdoc=True)
        # forked chunk made a deep copy of the original document, but we just
        # updated pointers in the original document.  So, restore our chunk to
        # pointing at the original document.
        self._chunk._ruamelparsed = old_chunk._ruamelparsed
        self._chunk._strictparsed = old_chunk._strictparsed
        self._mark_edited(strictindex, value)

    def _mark_edited(self, strictindex, value):
        path = self._chunk.path()
        if path is None:
            return
        if self.is_mapping():
            key = self._chunk.ruamelindex(strictindex)
        else:
            key = strictindex

        data = value.data if isinstance(value, YAML) else value
        self._add_explicit_paths(path + (key,), data)

    def _add_explicit_paths(self, path, data):
        # Mark the value the caller supplied (and everything below it) as
        # explicitly written. Schema defaults re-added around it are not part
        # of the supplied data, so they remain defaults and stay hidden unless
        # the caller supplied them too.
        self._chunk._edited_paths.add(path)
        if isinstance(data, dict):
            for key, value in data.items():
                self._add_explicit_paths(path + (key,), value)
        elif isinstance(data, list):
            for index, value in enumerate(data):
                self._add_explicit_paths(path + (index,), value)

    def __delitem__(self, index):
        strictindex = self._strictindex(index)
        path = self._chunk.path()
        if path is not None:
            if self.is_mapping():
                target = path + (self._chunk.ruamelindex(strictindex),)
            else:
                target = path + (strictindex,)
            for edited_path in list(self._chunk._edited_paths):
                if (
                    len(edited_path) >= len(target)
                    and edited_path[: len(target)] == target
                ):
                    self._chunk._edited_paths.discard(edited_path)
        del self._value[strictindex]
        del self._chunk.contents[self._chunk.ruamelindex(strictindex)]

    def __hash__(self):
        return hash(self._value)

    def __len__(self):
        return len(self._value)

    def _has_explicit_descendant(self):
        if self.is_mapping():
            return any(
                value._has_explicit_descendant() or not value.is_default
                for value in self._value.values()
            )
        elif self.is_sequence():
            return any(
                item._has_explicit_descendant() or not item.is_default
                for item in self._value
            )
        return False

    def _prune_defaults(self, real_node):
        """
        Remove schema defaults from a marked-up copy of the document.

        A default is kept only when it contains an explicitly written
        descendant, so editing a nested value does not cause sibling defaults
        to be written out.
        """
        if self.is_mapping():
            if not isinstance(real_node, CommentedMap):
                return
            for key, value in list(self._value.items()):
                strict_name = key.data
                name = self._chunk.ruamelindex(strict_name)
                if name not in real_node:
                    name = strict_name
                if name not in real_node:
                    continue
                if value.is_default and not value._has_explicit_descendant():
                    del real_node[name]
                else:
                    value._prune_defaults(real_node[name])
        elif self.is_sequence():
            if not isinstance(real_node, CommentedSeq):
                return
            # A sequence is written out as a whole: dropping default items
            # would change the length/positions of an edited list.
            keep_all = self.is_default and self._has_explicit_descendant()
            kept = []
            for index, item in enumerate(self._value):
                if index >= len(real_node):
                    continue
                if (
                    not keep_all
                    and item.is_default
                    and not item._has_explicit_descendant()
                ):
                    continue
                item._prune_defaults(real_node[index])
                kept.append(real_node[index])
            real_node[:] = kept

    def as_yaml(self, include_defaults=False):
        """
        Render the YAML node and subnodes as string.

        By default only values which came from the source text (or were
        explicitly written after loading) are rendered; schema defaults are
        omitted. Pass ``include_defaults=True`` to also render every current
        default value explicitly.
        """
        if include_defaults:
            marked_up = deepcopy(self.as_marked_up())
        else:
            if self.is_default and not self._has_explicit_descendant():
                # Nothing under this node was written by the user and the
                # node is not itself in the source text - there is nothing to
                # save.
                return ""
            marked_up = deepcopy(self.as_marked_up())
            self._prune_defaults(marked_up)
        dumped = dump(marked_up, Dumper=StrictYAMLDumper, allow_unicode=True)
        return dumped if sys.version_info[0] == 3 else dumped.decode("utf8")

    def items(self):
        if not isinstance(self._value, CommentedMap):
            raise TypeError("{0} not a mapping, cannot use .items()".format(repr(self)))
        return [(key, self._value[key]) for key, value in self._value.items()]

    def keys(self):
        if not isinstance(self._value, CommentedMap):
            raise TypeError("{0} not a mapping, cannot use .keys()".format(repr(self)))
        return [key for key, _ in self._value.items()]

    def values(self):
        if not isinstance(self._value, CommentedMap):
            raise TypeError(
                "{0} not a mapping, cannot use .values()".format(repr(self))
            )
        return [self._value[key] for key, value in self._value.items()]

    def get(self, index, default=None):
        if not isinstance(self._value, CommentedMap):
            raise TypeError("{0} not a mapping, cannot use .get()".format(repr(self)))
        return self._value[index] if index in self._value.keys() else default

    def __contains__(self, item):
        if isinstance(self._value, CommentedSeq):
            return item in self._value
        elif isinstance(self._value, CommentedMap):
            return item in self.keys()
        else:
            return item in self._value

    def __iter__(self):
        if self.is_sequence():
            return YAMLIterator(self)
        elif self.is_mapping():
            return YAMLIterator(self.keys())
        else:
            raise TypeError("{0} is a scalar value, cannot iterate.".format(repr(self)))

    @property
    def validator(self):
        return self._validator

    @property
    def text(self):
        """
        Return string value of scalar, whatever value it was parsed as.
        """
        if isinstance(self._value, CommentedMap):
            raise TypeError("{0} is a mapping, has no text value.".format(repr(self)))
        if isinstance(self._value, CommentedSeq):
            raise TypeError("{0} is a sequence, has no text value.".format(repr(self)))
        return self._text

    def copy(self):
        return copy(self)

    def __gt__(self, val):
        if isinstance(self._value, CommentedMap) or isinstance(
            self._value, CommentedSeq
        ):
            raise TypeError("{0} not an orderable type.".format(repr(self._value)))
        return self._value > val

    def __lt__(self, val):
        if isinstance(self._value, CommentedMap) or isinstance(
            self._value, CommentedSeq
        ):
            raise TypeError("{0} not an orderable type.".format(repr(self._value)))
        return self._value < val

    @property
    def value(self):
        return self._value

    def is_mapping(self):
        return isinstance(self._value, CommentedMap)

    def is_sequence(self):
        return isinstance(self._value, CommentedSeq)

    def is_scalar(self):
        return not self.is_mapping() and not self.is_sequence()

    @property
    def scalar(self):
        if isinstance(self._value, (CommentedMap, CommentedSeq)):
            raise TypeError("{0} has no scalar value.".format(repr(self)))
        return self._value

    def whole_document(self):
        return self._chunk.whole_document

    def __eq__(self, value):
        return self.data == value

    def __ne__(self, value):
        return self.data != value
