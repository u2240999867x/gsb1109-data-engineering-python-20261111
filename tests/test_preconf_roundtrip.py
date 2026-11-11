"""Small deterministic round-trip checks for preconfigured converters.

The same model is sent through the JSON and MessagePack pipelines, the two
formats whose dependencies are installed locally. Formats requiring optional
dependencies that are not installed (cbor2, bson, msgspec, ...) are
intentionally not covered here instead of being reported as passing.

Each test names the exact step at which types change: ``unstructure`` (model
to unstructured data), the wire step (``dumps``/library ``loads``), and
``structure`` (unstructured data back to the model).
"""

from datetime import date, datetime, timezone
from json import loads as json_loads
from typing import Dict, Tuple

import pytest
from attrs import define
from msgpack import loads as msgpack_loads

from cattrs.errors import IterableValidationError
from cattrs.preconf.json import make_converter as json_make_converter
from cattrs.preconf.msgpack import make_converter as msgpack_make_converter


@define
class Inner:
    name: str
    payload: bytes


@define
class Record:
    ident: int
    data: bytes
    when: datetime
    day: date
    pair: Tuple[str, int]
    scores: Dict[int, float]
    inner: Inner


RECORD = Record(
    ident=7,
    data=b"\x00hello\xff",
    when=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    day=date(2026, 1, 2),
    pair=("seven", 7),
    scores={1: 1.5, 20: 2.5},
    inner=Inner(name="nested", payload=b"n\x00t"),
)


@pytest.mark.parametrize(
    ("make_converter", "loads_kwargs"),
    [
        (json_make_converter, {}),
        # msgpack refuses non-string map keys unless told otherwise.
        (msgpack_make_converter, {"strict_map_key": False}),
    ],
    ids=["json", "msgpack"],
)
def test_record_roundtrip(make_converter, loads_kwargs):
    """A full dumps/loads round-trip restores the record exactly."""
    converter = make_converter()

    assert converter.loads(converter.dumps(RECORD), Record, **loads_kwargs) == RECORD


def test_json_unstructure_step_changes_types():
    """JSON: the unstructure step stringifies bytes/dates and lists tuples."""
    raw = json_make_converter().unstructure(RECORD)

    assert isinstance(raw["data"], str)  # bytes -> base85 string
    assert isinstance(raw["when"], str)  # datetime -> ISO 8601 string
    assert isinstance(raw["day"], str)  # date -> ISO 8601 string
    assert isinstance(raw["pair"], tuple)  # tuples are left to the library
    assert isinstance(raw["inner"], dict)  # nested record -> mapping
    assert isinstance(raw["inner"]["payload"], str)
    # Numeric keys are still integers at this step.
    assert all(isinstance(k, int) for k in raw["scores"])


def test_json_wire_step_changes_types():
    """JSON: the text step stringifies numeric keys and lists tuples."""
    converter = json_make_converter()
    wire = json_loads(converter.dumps(RECORD))

    assert wire["scores"] == {"1": 1.5, "20": 2.5}
    assert isinstance(wire["pair"], list)  # tuple -> list
    # The structure step converts keys and tuples back.
    assert converter.structure(wire, Record) == RECORD


def test_msgpack_unstructure_step_changes_types():
    """MessagePack: the unstructure step turns dates into timestamp floats."""
    raw = msgpack_make_converter().unstructure(RECORD)

    assert isinstance(raw["data"], bytes)  # bytes stay bytes
    assert isinstance(raw["when"], float)  # datetime -> timestamp float
    assert isinstance(raw["day"], float)  # date -> timestamp float
    assert isinstance(raw["pair"], tuple)  # tuples are left to the library
    assert isinstance(raw["inner"], dict)  # nested record -> mapping
    assert isinstance(raw["inner"]["payload"], bytes)
    assert all(isinstance(k, int) for k in raw["scores"])


def test_msgpack_wire_step_preserves_types():
    """MessagePack: the wire step preserves bytes and numeric keys."""
    converter = msgpack_make_converter()
    wire = msgpack_loads(converter.dumps(RECORD), strict_map_key=False)

    assert wire["data"] == RECORD.data
    assert wire["scores"] == RECORD.scores
    assert isinstance(wire["pair"], list)  # tuple -> list at the wire step
    assert converter.structure(wire, Record) == RECORD


@pytest.mark.parametrize(
    "make_converter",
    [json_make_converter, msgpack_make_converter],
    ids=["json", "msgpack"],
)
def test_hetero_tuple_arity_mismatch_raises(make_converter):
    """Heterogeneous tuples reject sequences of the wrong length."""
    converter = make_converter()

    with pytest.raises(IterableValidationError):
        converter.structure(["only-one"], Tuple[str, int])


def test_json_bytes_require_base85():
    """JSON: bytes are declared as base85 strings; anything else is an error."""
    converter = json_make_converter()

    with pytest.raises(ValueError):
        converter.structure("not base85", bytes)


def test_json_datetime_requires_iso_string():
    """JSON: datetimes are declared as ISO strings; timestamps are an error."""
    converter = json_make_converter()

    with pytest.raises(TypeError):
        converter.structure(1.5, datetime)


def test_msgpack_datetime_requires_timestamp():
    """MessagePack: datetimes are declared as timestamps; ISO strings error."""
    converter = msgpack_make_converter()

    with pytest.raises(TypeError):
        converter.structure("2026-01-02T03:04:05", datetime)


def test_msgpack_strict_map_keys_reject_int_keys():
    """MessagePack: int map keys are unsupported under strict_map_key."""
    converter = msgpack_make_converter()

    with pytest.raises(ValueError, match="strict_map_key"):
        converter.loads(converter.dumps(RECORD), Record)
