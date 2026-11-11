from __future__ import annotations

import bisect
import dataclasses

from typing import Any

from tomlkit.exceptions import ParseError
from tomlkit.exceptions import UnexpectedCharError


def byte_offset(source: str, index: int) -> int:
    """UTF-8 byte offset of a character offset in ``source``.

    ``SourceRange`` stores *character* offsets, which index the decoded
    ``str`` directly. Editors and protocol clients that work on UTF-8 bytes
    can convert with this helper: ``source.encode("utf-8")`` sliced at the
    returned offsets covers exactly the same text as ``source[index:]``.

    The conversion is O(index); ranges expose :meth:`SourceRange.byte_slice`
    for convenience. The offsets always address the decoded ``str``, so encode
    that string as UTF-8 before slicing bytes with them.
    """
    if index < 0:
        raise ValueError("byte_offset() requires a non-negative index")
    return len(source[:index].encode("utf-8"))


@dataclasses.dataclass(frozen=True)
class Position:
    """A single cursor position inside the parsed source text.

    Coordinate conventions (the same ones used by parse errors):

    * ``index`` is a zero-based Python character offset. It counts Unicode
      code points, so it is a valid index into the decoded ``str`` given to
      :func:`tomlkit.parse`. It is *not* a byte offset: to address a UTF-8
      buffer, convert it with :func:`byte_offset` (or
      :meth:`SourceRange.byte_slice`).
    * ``line`` is one-based and counts physical lines (the first line is
      ``1``). Both ``\\n`` and ``\\r\\n`` terminate a line.
    * ``column`` is zero-based and counts characters from the start of the
      line. A tab counts as one character, matching the parser's own error
      reporting.

    A position always describes the boundary *before* the character at
    ``index``; the end of an N-character document is therefore
    ``index == N`` rather than an out-of-range offset.
    """

    index: int
    line: int
    column: int

    def __lt__(self, other: Position) -> bool:
        if not isinstance(other, Position):
            return NotImplemented
        return self.index < other.index


@dataclasses.dataclass(frozen=True)
class SourceRange:
    """A half-open ``[start, end)`` span of the original source text.

    All offsets are zero-based Python character offsets into the decoded
    source string; ``start.index`` is the first character of the span and
    ``end.index`` is the first character *after* it, so slicing the source
    with ``source[start.index:end.index]`` reproduces the covered text. These
    are character offsets, not byte offsets: for a UTF-8 view use
    :meth:`byte_slice`, which converts both ends with :func:`byte_offset`.

    ``start`` and ``end`` follow the :class:`Position` conventions: lines are
    one-based, columns zero-based. A span may cover several lines (for
    example a multiline string); in that case ``end.line`` is greater than
    ``start.line`` and ``end.column`` is measured from the start of the end
    line.

    Ranges are immutable snapshots. They describe the text as it was parsed,
    not the current serialization of an edited document: once the document
    changes, a previously recorded range is stale. Callers distinguish a
    live range from a stale one through :meth:`tomlkit.items.Item.range`,
    which returns ``None`` for items that have no (longer any) source span.
    """

    start: Position
    end: Position

    def as_slice(self) -> slice:
        """The character slice equivalent of this range."""
        return slice(self.start.index, self.end.index)

    def byte_slice(self, source: str) -> slice:
        """The UTF-8 byte slice equivalent of this range in ``source``.

        ``source.encode("utf-8")[source_range.byte_slice(source)]`` covers
        the same text as ``source[source_range.as_slice()]``.
        """
        return slice(
            byte_offset(source, self.start.index),
            byte_offset(source, self.end.index),
        )

    def __str__(self) -> str:
        return (
            f"{self.start.line}:{self.start.column}-{self.end.line}:{self.end.column}"
        )


class SourceTracker:
    """Optional bookkeeping that maps parsed items back to source spans.

    A tracker is only constructed when source tracking is explicitly enabled
    (see :func:`tomlkit.api.parse`'s ``track_sources`` argument). A plain
    :func:`tomlkit.parse` keeps ``Source._tracker`` at ``None`` and pays none
    of the cost below: no per-item span storage is allocated, and no line
    index is built.

    The tracker stores two things:

    * a sorted list of line-start character offsets, shared by every span of
      the document and built once when tracking is enabled;
    * the spans themselves, attached to items by the parser as they are
      created. Discontinuous source (a dotted key, a table declared in several
      places, an array of tables) is represented by several ranges on the same
      item rather than one fabricated range covering the gaps.
    """

    def __init__(self) -> None:
        self._line_starts: list[int] | None = None

    def build_line_index(self, source: str) -> None:
        """Record the character offset at which each line starts.

        Runs once per tracked document; afterwards every
        :meth:`position` call is a binary search instead of a rescan.
        """
        starts = [0]
        for idx, char in enumerate(source):
            if char == "\n":
                starts.append(idx + 1)
            elif char == "\r":
                # Treat "\r\n" as one terminator: only the following "\n"
                # opens the next line. A lone "\r" still opens a line of its
                # own, mirroring the parser's validation semantics.
                if idx + 1 >= len(source) or source[idx + 1] != "\n":
                    starts.append(idx + 1)
        self._line_starts = starts

    def position(self, index: int) -> Position:
        """Convert a character offset into a :class:`Position`."""
        if self._line_starts is None:
            raise RuntimeError(
                "SourceTracker.build_line_index() must run before position()"
            )
        # Rightmost line start not greater than the offset.
        line = bisect.bisect_right(self._line_starts, index)
        line_start = self._line_starts[line - 1]
        return Position(index=index, line=line, column=index - line_start)

    def range(self, start_index: int, end_index: int) -> SourceRange:
        """Build a half-open :class:`SourceRange` between two offsets."""
        return SourceRange(self.position(start_index), self.position(end_index))


class _State:
    def __init__(
        self,
        source: Source,
        save_marker: bool | None = False,
        restore: bool | None = False,
    ) -> None:
        self._source = source
        self._save_marker = save_marker
        self.restore = restore

    def __enter__(self) -> _State:
        # Entering this context manager - save the state
        # PERF: snapshot only the integer index + current char + marker.
        # We no longer carry an iterator (`_chars`) so there's no `copy(...)`
        # to do here — saving 3 attribute reads vs the original iter copy.
        self._idx = self._source._idx
        self._current = self._source._current
        self._marker = self._source._marker

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception_val: BaseException | None,
        trace: Any,
    ) -> None:
        # Exiting this context manager - restore the prior state
        if self.restore or exception_type:
            self._source._idx = self._idx
            self._source._current = self._current
            if self._save_marker:
                self._source._marker = self._marker


class _StateHandler:
    """
    State preserver for the Parser.
    """

    def __init__(self, source: Source) -> None:
        self._source = source
        self._states: list[_State] = []

    def __call__(
        self,
        save_marker: bool | None = False,
        restore: bool | None = False,
    ) -> _State:
        return _State(self._source, save_marker, restore)

    def __enter__(self) -> _State:
        state = self()
        self._states.append(state)
        return state.__enter__()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception_val: BaseException | None,
        trace: Any,
    ) -> None:
        state = self._states.pop()
        state.__exit__(exception_type, exception_val, trace)


class Source(str):
    # EOF is a placeholder value for `current` past the end of input. End-of-input
    # is detected positionally (`end()` / `_idx >= len`), never by comparing to this
    # value, so a real NUL byte in the input is not mistaken for EOF.
    EOF = "\0"

    def __new__(cls, _: str, tracker: SourceTracker | None = None) -> Source:
        # ``str.__new__`` rejects the extra ``tracker`` keyword, so swallow it
        # here and only forward the textual content.
        return super().__new__(cls, _)

    def __init__(self, _: str, tracker: SourceTracker | None = None) -> None:
        super().__init__()

        # Track an integer index over the underlying str (Source subclasses str):
        # init is O(1) and `inc()` just bumps the index and reads the next char,
        # instead of materializing a list of (index, char) pairs up front.
        self._idx = -1  # pre-start sentinel; first inc() will land on 0
        self._marker = 0
        self._current: str = ""

        # Optional source-span bookkeeping. ``None`` on every ordinary parse,
        # so the hot scanning path stays free of tracking overhead.
        self._tracker = tracker
        if tracker is not None:
            tracker.build_line_index(str(self))

        self._state = _StateHandler(self)

        self.inc()

    def reset(self) -> None:
        # initialize both idx and current
        self.inc()

        # reset marker
        self.mark()

    @property
    def state(self) -> _StateHandler:
        return self._state

    @property
    def idx(self) -> int:
        return self._idx

    @property
    def current(self) -> str:
        return self._current

    @property
    def marker(self) -> int:
        return self._marker

    @property
    def tracker(self) -> SourceTracker | None:
        """The tracker attached to this source, or ``None`` when untracked."""
        return self._tracker

    def is_tracking(self) -> bool:
        """Whether source spans are being recorded for this parse."""
        return self._tracker is not None

    def make_range(self, start_index: int, end_index: int) -> SourceRange:
        """Build a :class:`SourceRange` over character offsets.

        Only callable on a tracked source; ordinary parses have no line index
        and raise :class:`RuntimeError` rather than silently building one.
        """
        if self._tracker is None:
            raise RuntimeError("Source range requested on an untracked parse")
        return self._tracker.range(start_index, end_index)

    def extract(self) -> str:
        """
        Extracts the value between marker and index
        """
        return self[self._marker : self._idx]

    def inc(self, exception: type[ParseError] | None = None) -> bool:
        """
        Increments the parser if the end of the input has not been reached.
        Returns whether or not it was able to advance.
        """
        # Integer increment + a single str index, no iterator / StopIteration triage.
        next_idx = self._idx + 1
        if next_idx < len(self):
            self._idx = next_idx
            self._current = self[next_idx]
            return True

        # Past end : pin to len, switch current to EOF, raise if asked.
        self._idx = len(self)
        self._current = self.EOF
        if exception:
            raise self.parse_error(exception) from None
        return False

    def advance_while(self, charset: frozenset) -> bool:
        """Advance while the current character is in ``charset``.

        Equivalent to ``while self.current in charset and self.inc(): pass`` but
        it scans the underlying string in a single pass and updates the index
        and current character only once, instead of paying a per-character
        ``inc()`` call. On return ``current`` is the first character NOT in
        ``charset`` (or EOF). Returns ``True`` if it stopped on a real
        character, ``False`` at EOF — the same value contract as the loop.
        """
        i = self._idx
        n = len(self)
        while i < n and self[i] in charset:
            i += 1
        if i < n:
            self._idx = i
            self._current = self[i]
            return True
        self._idx = n
        self._current = self.EOF
        return False

    def advance_until(self, stopset: frozenset) -> bool:
        """Advance while the current character is NOT in ``stopset``.

        The mirror of :meth:`advance_while`: equivalent to
        ``while self.current not in stopset and self.inc(): pass`` in a single
        scan. On return ``current`` is the first character IN ``stopset`` (or
        EOF), with the same return-value contract.
        """
        i = self._idx
        n = len(self)
        while i < n and self[i] not in stopset:
            i += 1
        if i < n:
            self._idx = i
            self._current = self[i]
            return True
        self._idx = n
        self._current = self.EOF
        return False

    def inc_n(self, n: int, exception: type[ParseError] | None = None) -> bool:
        """
        Increments the parser by n characters
        if the end of the input has not been reached.
        """
        return all(self.inc(exception=exception) for _ in range(n))

    def consume(self, chars: str, min: int = 0, max: int = -1) -> None:
        """
        Consume chars until min/max is satisfied is valid.
        """
        while self.current in chars and max != 0:
            min -= 1
            max -= 1
            if not self.inc():
                break

        # failed to consume minimum number of characters
        if min > 0:
            raise self.parse_error(UnexpectedCharError, self.current)

    def end(self) -> bool:
        """
        Returns True if the parser has reached the end of the input.
        """
        return self._idx >= len(self)

    def mark(self) -> None:
        """
        Sets the marker to the index's current position
        """
        self._marker = self._idx

    def parse_error(
        self,
        exception: type[ParseError] = ParseError,
        *args: Any,
        **kwargs: Any,
    ) -> ParseError:
        """
        Creates a generic "parse error" at the current position.
        """
        line, col = self._to_linecol()

        return exception(line, col, *args, **kwargs)

    def _to_linecol(self) -> tuple[int, int]:
        # `keepends` keeps each line's own terminator inside its length: a "\r\n"
        # break occupies two characters, and assuming a single one made the
        # running offset fall one behind per CRLF line, so an error later in the
        # document was reported on the wrong line and column.
        lines = self.splitlines(keepends=True)
        cur = 0
        for i, line in enumerate(lines):
            if cur + len(line) > self.idx:
                return (i + 1, self.idx - cur)

            cur += len(line)

        return len(lines), 0
