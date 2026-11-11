"""
Observation-non-interference tests.

Enabling glom's diagnostics (``Inspect`` echo and the error target-spec
trace) must never change the *result* of a transformation or trigger extra
side-effecting access on the target.

The probes below are deliberately side-effecting, "honest" business objects
rather than no-op stand-ins:

  * a property that increments a counter on every single access,
  * a single-consumption generator (any extra consumer drains or breaks it),
  * an object whose ``__repr__`` itself raises.

Every check runs the diagnostic-enabled call next to the same call with
diagnostics switched off, so a divergence is attributable to observation.
When extra access shows up, the responsible entry points are the display
calls in ``Inspect._trace`` (which print the target/output) and the trace
renderer ``glom.core.bbrepr`` used by ``format_target_spec_trace``.
"""

import pytest

from glom import glom, Inspect, T, GlomError, PathAccessError
from glom.core import bbrepr


# ---------------------------------------------------------------------------
# Side-effecting probe objects.
# Intentionally keep these side-effectful: they stand in for real business
# objects and must not be swapped for no-op objects to make tests pass.
# ---------------------------------------------------------------------------

class CountingAttr:
    """Each read of ``.value`` increments a class-level counter exactly once."""
    access_count = 0

    @property
    def value(self):
        type(self).access_count += 1
        return "v"


def _one_shot_generator():
    yield 1
    yield 2
    yield 3


class GeneratorHolder:
    """Holds a generator which can only be consumed a single time."""
    def __init__(self):
        self.gen = _one_shot_generator()


class BadRepr:
    """An object whose repr/str conversion itself fails."""
    def __repr__(self):
        raise RuntimeError("repr is broken")

    @property
    def value(self):
        return "ok"


def _boom(_value):
    raise ValueError("boom")


# ---------------------------------------------------------------------------
# 1. Property resolution: exactly one access per resolution, on or off
# ---------------------------------------------------------------------------

def test_counting_attr_success_no_extra_access(capsys):
    CountingAttr.access_count = 0
    off_result = glom(CountingAttr(), T.value)
    off_count = CountingAttr.access_count

    CountingAttr.access_count = 0
    on_result = glom(CountingAttr(), Inspect(T.value))
    on_count = CountingAttr.access_count
    capsys.readouterr()  # drain the echo

    assert off_result == on_result == "v"
    assert off_count == 1
    assert on_count == off_count  # observation added no extra attribute access


def test_counting_attr_failure_no_extra_access(capsys):
    # the property resolves, then a downstream step raises
    CountingAttr.access_count = 0
    with pytest.raises(GlomError):
        glom(CountingAttr(), (T.value, _boom))
    off_count = CountingAttr.access_count

    CountingAttr.access_count = 0
    with pytest.raises(GlomError):
        glom(CountingAttr(), Inspect((T.value, _boom)))
    on_count = CountingAttr.access_count
    capsys.readouterr()

    assert off_count == 1
    assert on_count == off_count


def test_counting_attr_trace_render_no_extra_access(capsys):
    # rendering the target-spec trace (str of the raised error) must not
    # re-access the property either
    CountingAttr.access_count = 0
    with pytest.raises(GlomError) as off_exc:
        glom(CountingAttr(), (T.value, _boom))
    str(off_exc.value)
    off_count = CountingAttr.access_count

    CountingAttr.access_count = 0
    with pytest.raises(GlomError) as on_exc:
        glom(CountingAttr(), Inspect((T.value, _boom)))
    str(on_exc.value)
    on_count = CountingAttr.access_count
    capsys.readouterr()

    assert off_count == on_count == 1


# ---------------------------------------------------------------------------
# 2. Single-consumption generators: diagnostics must not consume them
# ---------------------------------------------------------------------------

def test_generator_passed_through_unconsumed_on_success(capsys):
    g_off = _one_shot_generator()
    off_result = glom(g_off, T)
    assert list(g_off) == [1, 2, 3]

    g_on = _one_shot_generator()
    on_result = glom(g_on, Inspect(T))
    capsys.readouterr()
    assert list(g_on) == [1, 2, 3]  # echoing did not iterate the generator
    assert on_result is g_on
    assert type(on_result) is type(off_result)


def test_generator_intact_on_failure(capsys):
    missing = "definitely_not_an_attr"

    g_off = _one_shot_generator()
    with pytest.raises(PathAccessError) as off_exc:
        glom(g_off, missing)
    str(off_exc.value)  # render the target-spec trace too
    assert list(g_off) == [1, 2, 3]

    g_on = _one_shot_generator()
    with pytest.raises(PathAccessError) as on_exc:
        glom(g_on, Inspect(missing))
    str(on_exc.value)  # echo + trace rendering must both leave it full
    capsys.readouterr()
    assert list(g_on) == [1, 2, 3]


def test_nested_generator_intact_on_failure(capsys):
    # the diagnostic renders the holder (target); that must not touch .gen
    h_off = GeneratorHolder()
    with pytest.raises(PathAccessError) as off_exc:
        glom(h_off, "missing")
    str(off_exc.value)
    assert list(h_off.gen) == [1, 2, 3]

    h_on = GeneratorHolder()
    with pytest.raises(PathAccessError) as on_exc:
        glom(h_on, Inspect("missing"))
    str(on_exc.value)
    capsys.readouterr()
    assert list(h_on.gen) == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. Objects whose repr fails: display must degrade, not change the result
# ---------------------------------------------------------------------------

def test_bbrepr_tolerates_broken_repr():
    # direct guard on the trace renderer entry point
    rendered = bbrepr(BadRepr())
    assert "BadRepr instance" in rendered
    assert bbrepr([1, BadRepr(), 2]).startswith("[1,")


def test_bad_repr_target_does_not_break_success(capsys):
    assert glom(BadRepr(), T.value) == "ok"  # diagnostics off

    result = glom(BadRepr(), Inspect(T.value))  # diagnostics on
    captured = capsys.readouterr()
    assert result == "ok"
    assert "target:" in captured.out
    assert "BadRepr instance" in captured.out  # reprlib fallback, no raise


def test_bad_repr_output_does_not_break_echo(capsys):
    spec = Inspect((T["k"], lambda target: BadRepr()))
    result = glom({"k": 1}, spec)
    captured = capsys.readouterr()
    assert isinstance(result, BadRepr)
    assert "output:" in captured.out
    assert "BadRepr instance" in captured.out


def test_bad_repr_failure_preserves_underlying_error(capsys):
    with pytest.raises(GlomError) as exc_info:
        glom(BadRepr(), Inspect((T.value, _boom)))
    capsys.readouterr()

    message = str(exc_info.value)  # triggers the target-spec trace render
    assert "ValueError" in message  # the real business error
    assert "boom" in message
    assert "BadRepr instance" in message  # the broken repr is contained


def test_inspect_echo_normal_format_unchanged(capsys):
    # the safe rendering must keep the ordinary, printable echo byte-identical
    glom({"a": {"b": {}}}, Inspect("a.b"))
    out = capsys.readouterr().out
    assert out == (
        "---\n"
        "path:   ['a.b']\n"
        "target: {'a': {'b': {}}}\n"
        "output: {}\n"
        "---\n"
    )
