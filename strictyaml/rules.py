from strictyaml.exceptions import YAMLValidationError
import sys


if sys.version_info[0] == 3:
    unicode = str


class MapRule(object):
    """
    Base class for declarative rules about relationships between
    keys in a mapping - e.g. keys which may not appear together or
    keys which are only required when a flag is switched on.

    Rules are checked only after every key and value in the mapping
    has passed its own validation, so a rule is never evaluated on -
    or reported for - a field which failed to parse.
    """

    @property
    def keys(self):
        """
        All keys in the mapping that this rule refers to.
        """
        raise NotImplementedError

    def check(self, chunk, values, key_chunks):
        """
        Raise YAMLValidationError if the rule is violated.

        'values' maps each validated key to its validated data and
        'key_chunks' maps each validated key found in the document
        to the YAMLChunk of the corresponding key.
        """
        raise NotImplementedError

    def _original_key(self, key, key_chunks):
        """
        The key as it was originally written in the document.
        """
        if key in key_chunks:
            return unicode(key_chunks[key].contents)
        return key

    def _quoted_keys(self, keys, key_chunks):
        return ", ".join(
            "'{0}'".format(self._original_key(key, key_chunks)) for key in keys
        )

    def _anchor(self, chunk, key_chunks, keys):
        """
        The chunk of the first participating key found in the document,
        so the error points at an actual field where possible.
        """
        for key in keys:
            if key in key_chunks:
                return key_chunks[key]
        return chunk


class MutuallyExclusive(MapRule):
    """
    Keys which may not appear together in a mapping.

    Any one of the keys is allowed on its own, but two or more
    together is a validation error - e.g. a config which may give
    'password' or 'token' but not both.
    """

    def __init__(self, *keys):
        assert len(keys) >= 2, "MutuallyExclusive requires at least two keys"
        self._keys = keys

    @property
    def keys(self):
        return self._keys

    def check(self, chunk, values, key_chunks):
        present = [key for key in self._keys if key in values]
        if len(present) > 1:
            raise YAMLValidationError(
                "while parsing a mapping",
                "mutually exclusive keys {0} found together".format(
                    self._quoted_keys(present, key_chunks)
                ),
                self._anchor(chunk, key_chunks, present),
            )

    def __repr__(self):
        return "MutuallyExclusive({0})".format(
            ", ".join(repr(key) for key in self._keys)
        )


class Requires(MapRule):
    """
    When the trigger key is present and its validated value is
    truthy (e.g. a feature flag switched on), the required keys
    must also be present - e.g. 'partition_key' is required when
    'partitioning' is enabled.
    """

    def __init__(self, key, *required_keys):
        assert len(required_keys) >= 1, "Requires needs at least one required key"
        self._key = key
        self._required_keys = required_keys

    @property
    def keys(self):
        return (self._key,) + self._required_keys

    def check(self, chunk, values, key_chunks):
        if values.get(self._key):
            missing = [key for key in self._required_keys if key not in values]
            if missing:
                raise YAMLValidationError(
                    "while parsing a mapping",
                    "key(s) {0} required when '{1}' is enabled".format(
                        self._quoted_keys(missing, key_chunks),
                        self._original_key(self._key, key_chunks),
                    ),
                    self._anchor(chunk, key_chunks, [self._key]),
                )

    def __repr__(self):
        return "Requires({0})".format(
            ", ".join(repr(key) for key in (self._key,) + self._required_keys)
        )
