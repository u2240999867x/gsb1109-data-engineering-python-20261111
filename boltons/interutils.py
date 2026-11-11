# Copyright (c) 2026, Mahmoud Hashemi
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
#
#    * The names of the contributors may not be used to endorse or
#      promote products derived from this software without specific
#      prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""\

The :class:`Interval` type represents a single interval over any
ordered domain, and :class:`IntervalMap` represents a union of
disjoint intervals. Together they provide one interface for union,
intersection, difference and membership tests over policy ranges such
as data retention windows.

Endpoint semantics are always explicit:

- ``lower_inclusive`` / ``upper_inclusive`` select closed ``[a, b]``,
  open ``(a, b)`` and half-open variants independently.
- Discrete domains (dates, integers, ...) take a ``step``, so
  adjacency means "consecutive members" (e.g. ``timedelta(days=1)``
  for :class:`datetime.date`), while the default continuous domain
  only treats touching closed endpoints as adjacent.
- ``None`` bounds mean *explicitly* unbounded. An unknown bound must
  never be passed as ``None`` by accident: use
  :meth:`Interval.from_bounded` for data that must have two known
  endpoints, and reserve unbounded intervals for deliberately open
  ranges. An unknown upper bound is therefore never silently turned
  into infinite retention.
- Intervals which contain no members (such as the intersection of two
  disjoint ranges) are kept as empty intervals carrying an
  :attr:`Interval.empty_reason`, instead of raising or disappearing
  without explanation.

Note that this module does not use floating-point infinities: bounds
are ordinary domain values (``int``, ``date``, ...) or ``None``.

A data retention policy with several windows, one of which ends on an
exclusive boundary, can combine and answer boundary-day questions with
the same interface::

    >>> from datetime import date, timedelta
    >>> day = timedelta(days=1)
    >>> policy = IntervalMap([
    ...     Interval.from_bounded(date(2023, 1, 1), date(2023, 12, 31), step=day),
    ...     Interval.from_bounded(date(2024, 6, 1), date(2024, 12, 31),
    ...                           upper_inclusive=False, step=day)])
    >>> date(2024, 12, 30) in policy
    True
    >>> date(2024, 12, 31) in policy
    False
    >>> hold = Interval.from_bounded(date(2023, 6, 1), date(2024, 6, 30), step=day)
    >>> date(2024, 1, 1) in policy
    False
    >>> date(2024, 1, 1) in policy.union(hold)
    True
"""

from bisect import bisect_right


__all__ = ['Interval', 'IntervalMap']

_DISCRETE = 'discrete'
_CONTINUOUS = 'continuous'
_DOMAIN_NAMES = (_DISCRETE, _CONTINUOUS)

# Rank used when sorting bounds: at the same value an inclusive bound
# sorts before an exclusive one.
_INCLUSIVE_RANK = 0
_EXCLUSIVE_RANK = 1


def _bound_key(value, inclusive):
    return (value, _INCLUSIVE_RANK if inclusive else _EXCLUSIVE_RANK)


class Interval:
    """A single interval with explicit endpoint semantics.

    Args:
        lower (object): Lowest bound, or ``None`` for an explicitly
            unbounded lower end.
        upper (object): Highest bound, or ``None`` for an explicitly
            unbounded upper end.
        lower_inclusive (bool): Whether ``lower`` itself is a member.
        upper_inclusive (bool): Whether ``upper`` itself is a member.
        step (object): Member spacing for a discrete domain, e.g.
            ``1`` for integers or ``timedelta(days=1)`` for dates.
            ``None`` selects a continuous domain unless ``domain``
            says otherwise.
        domain (str): ``'discrete'`` or ``'continuous'``. May also be
            an object exposing a ``step`` attribute, which is treated
            as a discrete domain with that step.
    """

    __slots__ = ('lower', 'upper', 'lower_inclusive', 'upper_inclusive',
                 '_step', '_domain_kind', '_empty_reason')

    def __init__(self, lower=None, upper=None, *,
                 lower_inclusive=True, upper_inclusive=True,
                 step=None, domain=None, _reversed_ok=False):
        if not isinstance(lower_inclusive, bool) or not isinstance(upper_inclusive, bool):
            raise TypeError('lower_inclusive and upper_inclusive must be bool')
        if not _reversed_ok and lower is not None and upper is not None \
                and lower > upper:
            raise ValueError('expected lower <= upper, not %r > %r'
                             % (lower, upper))

        domain_kind, resolved_step = _resolve_domain(domain, step)
        self.lower = lower
        self.upper = upper
        self.lower_inclusive = lower_inclusive
        self.upper_inclusive = upper_inclusive
        self._step = resolved_step
        self._domain_kind = domain_kind
        self._empty_reason = self._compute_empty_reason()

    @classmethod
    def from_bounded(cls, lower, upper, *, lower_inclusive=True,
                     upper_inclusive=True, step=None, domain=None):
        """Build an interval that must have two known endpoints.

        Unlike the constructor, passing ``None`` for either bound
        raises :exc:`ValueError`, so that an unknown bound in source
        data (e.g. a retention policy missing its end date) can never
        be read as an intentionally infinite range.
        """
        if lower is None or upper is None:
            raise ValueError('from_bounded() requires two known endpoints;'
                             ' use the Interval constructor with an explicit'
                             ' None only for a deliberately unbounded range')
        return cls(lower, upper,
                   lower_inclusive=lower_inclusive,
                   upper_inclusive=upper_inclusive,
                   step=step, domain=domain)

    @property
    def is_discrete(self):
        """Whether the interval lives on a discrete domain."""
        return self._domain_kind == _DISCRETE

    @property
    def is_continuous(self):
        """Whether the interval lives on a continuous domain."""
        return self._domain_kind == _CONTINUOUS

    @property
    def step(self):
        """The member spacing of a discrete domain, else ``None``."""
        return self._step

    @property
    def is_bounded(self):
        """Whether both endpoints are known (not ``None``)."""
        return self.lower is not None and self.upper is not None

    @property
    def is_empty(self):
        """Whether the interval contains no members."""
        return self._empty_reason is not None

    @property
    def empty_reason(self):
        """A human-readable explanation when :attr:`is_empty`, else ``None``."""
        return self._empty_reason

    def _compute_empty_reason(self):
        if self.lower is None or self.upper is None:
            return None
        if self.lower > self.upper:
            return ('interval %s has no members: lower bound %r is above upper'
                    ' bound %r'
                    % (self._bracket_repr(), self.lower, self.upper))
        if self._step is not None:
            closed_lower = self.closed_lower
            closed_upper = self.closed_upper
            if closed_lower > closed_upper:
                return ('discrete interval has no members: closed lower %r is'
                        ' past closed upper %r after endpoint exclusivity'
                        % (closed_lower, closed_upper))
            return None
        if self.lower == self.upper and not (self.lower_inclusive
                                             and self.upper_inclusive):
            return ('interval %s has no members: equal bounds %r with an'
                    ' exclusive endpoint'
                    % (self._bracket_repr(), self.lower))
        return None

    @property
    def closed_lower(self):
        """First member of a discrete interval, expressed inclusively.

        Returns ``None`` for an unbounded lower end and raises
        :exc:`TypeError` when the interval has no ``step`` to shift an
        exclusive bound with.
        """
        if self._step is None:
            raise TypeError('closed_lower requires a discrete interval with'
                            ' a step')
        if self.lower is None:
            return None
        if self.lower_inclusive:
            return self.lower
        return self.lower + self._step

    @property
    def closed_upper(self):
        """Last member of a discrete interval, expressed inclusively.

        Returns ``None`` for an unbounded upper end and raises
        :exc:`TypeError` when the interval has no ``step`` to shift an
        exclusive bound with.
        """
        if self._step is None:
            raise TypeError('closed_upper requires a discrete interval with'
                            ' a step')
        if self.upper is None:
            return None
        if self.upper_inclusive:
            return self.upper
        return self.upper - self._step

    def __contains__(self, value):
        if value is None or self.is_empty:
            return False
        if self.lower is not None:
            if self.lower_inclusive:
                if value < self.lower:
                    return False
            elif value <= self.lower:
                return False
        if self.upper is not None:
            if self.upper_inclusive:
                if value > self.upper:
                    return False
            elif value >= self.upper:
                return False
        if self._step is not None:
            # Members must land on the domain grid. timedelta and the
            # built-in numeric types all support % against the step.
            if self.lower is not None:
                if (value - self.lower) % self._step:
                    return False
            elif self.upper is not None:
                if (self.upper - value) % self._step:
                    return False
        return True

    def _bracket_repr(self):
        lower_text = '-inf' if self.lower is None else repr(self.lower)
        upper_text = '+inf' if self.upper is None else repr(self.upper)
        return '%s%s, %s%s' % (
            '[' if self.lower_inclusive else '(', lower_text,
            upper_text, ']' if self.upper_inclusive else ')')

    def __repr__(self):
        suffix = ''
        if self._step is not None:
            suffix = ', step=%r' % (self._step,)
        elif self.is_discrete:
            suffix = ", domain='discrete'"
        text = 'Interval(%s%s)' % (self._bracket_repr(), suffix)
        if self.is_empty:
            text += '  # empty: %s' % self._empty_reason
        return text

    def __eq__(self, other):
        if not isinstance(other, Interval):
            return NotImplemented
        return (self.lower == other.lower
                and self.upper == other.upper
                and self.lower_inclusive == other.lower_inclusive
                and self.upper_inclusive == other.upper_inclusive
                and self._step == other._step
                and self._domain_kind == other._domain_kind)

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return hash((self.lower, self.upper, self.lower_inclusive,
                     self.upper_inclusive, self._step, self._domain_kind))

    def _check_compatible(self, other):
        if not isinstance(other, Interval):
            raise TypeError('interval operations require another Interval, not %r'
                            % type(other).__name__)
        if self.is_discrete != other.is_discrete:
            raise TypeError('cannot mix discrete and continuous intervals'
                            ' (%r vs %r)' % (self, other))
        if self._step is not None and other._step is not None \
                and self._step != other._step:
            raise TypeError('incompatible discrete steps %r vs %r'
                            % (self._step, other._step))
        return self._step if self._step is not None else other._step

    def _kwargs(self, step):
        if step is not None:
            return {'step': step}
        if self.is_discrete:
            return {'domain': _DISCRETE}
        return {'domain': _CONTINUOUS}

    def intersect(self, other):
        """Return the intersection with ``other`` (a possibly empty Interval).

        Accepts an :class:`IntervalMap`, in which case an
        :class:`IntervalMap` is returned.
        """
        if isinstance(other, IntervalMap):
            return IntervalMap([self], **self._map_kwargs(self._step)) \
                .intersection(other)
        step = self._check_compatible(other)

        if self.lower is None:
            lower, lower_inclusive = other.lower, other.lower_inclusive
        elif other.lower is None:
            lower, lower_inclusive = self.lower, self.lower_inclusive
        elif _bound_key(self.lower, self.lower_inclusive) >= \
                _bound_key(other.lower, other.lower_inclusive):
            lower, lower_inclusive = self.lower, self.lower_inclusive
        else:
            lower, lower_inclusive = other.lower, other.lower_inclusive

        if self.upper is None:
            upper, upper_inclusive = other.upper, other.upper_inclusive
        elif other.upper is None:
            upper, upper_inclusive = self.upper, self.upper_inclusive
        elif _bound_key(self.upper, self.upper_inclusive) <= \
                _bound_key(other.upper, other.upper_inclusive):
            upper, upper_inclusive = self.upper, self.upper_inclusive
        else:
            upper, upper_inclusive = other.upper, other.upper_inclusive

        return _make_interval(lower, upper,
                              lower_inclusive=lower_inclusive,
                              upper_inclusive=upper_inclusive,
                              kwargs=self._kwargs(step))

    def __and__(self, other):
        return self.intersect(other)

    def union(self, other):
        """Return the union with ``other``.

        Returns an :class:`Interval` when the ranges overlap or are
        adjacent, otherwise an :class:`IntervalMap` with two members.
        Accepts an :class:`IntervalMap` as well as an :class:`Interval`.
        """
        if isinstance(other, IntervalMap):
            return other.union(self)
        self._check_compatible(other)
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        overlap = self.intersect(other)
        if not overlap.is_empty or _is_adjacent(self, other):
            return _merge_pair(self, other)
        return IntervalMap([self, other])

    def __or__(self, other):
        return self.union(other)

    def difference(self, other):
        """Return the portions of this interval not covered by ``other``.

        Always an :class:`IntervalMap`, which may hold zero, one or two
        intervals; any empty remainder is recorded on the map's
        :attr:`IntervalMap.empty_intervals`.
        """
        if isinstance(other, IntervalMap):
            return IntervalMap([self], **self._map_kwargs(self._step)) \
                .difference(other)
        step = self._check_compatible(other)
        pieces = _difference_pieces(self, other, step)
        return IntervalMap(pieces, **self._map_kwargs(step))

    def __sub__(self, other):
        return self.difference(other)

    def _map_kwargs(self, step):
        if step is not None:
            return {'step': step}
        return {'domain': _DISCRETE if self.is_discrete else _CONTINUOUS}


def _resolve_domain(domain, step):
    if step is not None:
        try:
            if step <= type(step)():
                raise ValueError('step must be a positive spacing, not %r'
                                 % (step,))
        except TypeError:
            # Custom step types without a zero value are validated by
            # their arithmetic during use instead.
            pass
    if domain is None:
        return (_DISCRETE if step is not None else _CONTINUOUS), step
    if isinstance(domain, str):
        if domain not in _DOMAIN_NAMES:
            raise ValueError("domain must be one of %r" % (_DOMAIN_NAMES,))
        if domain == _CONTINUOUS and step is not None:
            raise ValueError('a continuous domain cannot have a step')
        return domain, step
    domain_step = getattr(domain, 'step', None)
    if domain_step is None:
        raise TypeError('domain objects must expose a step attribute')
    if step is not None and step != domain_step:
        raise ValueError('step %r disagrees with domain step %r'
                         % (step, domain_step))
    return _DISCRETE, domain_step


def _make_interval(lower, upper, *, lower_inclusive, upper_inclusive,
                   kwargs):
    """Internal constructor allowing reversed bounds to become empty."""
    return Interval(lower, upper,
                    lower_inclusive=lower_inclusive,
                    upper_inclusive=upper_inclusive,
                    _reversed_ok=True, **kwargs)


def _lower_sort_key(interval):
    # Unbounded lower ends always sort first; at most one survives.
    if interval.lower is None:
        return (0, 0)
    return (1, _bound_key(interval.lower, interval.lower_inclusive))


def _is_adjacent(first, second):
    """Whether two disjoint, ordered intervals touch with no gap."""
    if first.upper is None or second.lower is None:
        return False
    if first._step is not None:
        return first.closed_upper + first._step == second.closed_lower
    if first.is_discrete:
        # Discrete domain without a known step cannot prove adjacency.
        return False
    # On a continuous domain equal bounds only merge when both ends
    # include the shared point; merging [a,b) with (b,c] would wrongly
    # pull b into the resulting interval.
    return first.upper == second.lower \
        and first.upper_inclusive and second.lower_inclusive


def _merge_pair(first, second):
    """Merge two overlapping or adjacent intervals into one."""
    step = first._step if first._step is not None else second._step

    if first.lower is None:
        lower, lower_inclusive = first.lower, first.lower_inclusive
    elif second.lower is None:
        lower, lower_inclusive = second.lower, second.lower_inclusive
    elif _bound_key(first.lower, first.lower_inclusive) <= \
            _bound_key(second.lower, second.lower_inclusive):
        lower, lower_inclusive = first.lower, first.lower_inclusive
    else:
        lower, lower_inclusive = second.lower, second.lower_inclusive

    if first.upper is None:
        upper, upper_inclusive = first.upper, first.upper_inclusive
    elif second.upper is None:
        upper, upper_inclusive = second.upper, second.upper_inclusive
    elif _bound_key(first.upper, first.upper_inclusive) >= \
            _bound_key(second.upper, second.upper_inclusive):
        upper, upper_inclusive = first.upper, first.upper_inclusive
    else:
        upper, upper_inclusive = second.upper, second.upper_inclusive

    kwargs = {'step': step} if step is not None else \
        {'domain': _DISCRETE if first.is_discrete else _CONTINUOUS}
    return _make_interval(lower, upper,
                          lower_inclusive=lower_inclusive,
                          upper_inclusive=upper_inclusive, kwargs=kwargs)


def _difference_pieces(target, removed, step):
    overlap = target.intersect(removed)
    if overlap.is_empty:
        return [target]
    pieces = []
    kwargs = target._map_kwargs(step)
    if removed.lower is not None:
        pieces.append(_make_interval(
            target.lower, removed.lower,
            lower_inclusive=target.lower_inclusive,
            upper_inclusive=not removed.lower_inclusive,
            kwargs=kwargs))
    if removed.upper is not None:
        pieces.append(_make_interval(
            removed.upper, target.upper,
            lower_inclusive=not removed.upper_inclusive,
            upper_inclusive=target.upper_inclusive,
            kwargs=kwargs))
    return pieces


class IntervalMap:
    """A normalized union of disjoint, sorted :class:`Interval` objects.

    All input intervals must share the same kind of domain (discrete
    vs continuous and step). Empty inputs are excluded from the
    normalized result but retained on :attr:`empty_intervals` so that
    operations remain explainable.
    """

    __slots__ = ('_intervals', '_bounded_intervals', '_lower_keys', '_step',
                 '_domain_kind', 'empty_intervals')

    def __init__(self, intervals=(), *, step=None, domain=None,
                 empty_intervals=()):
        if domain is None and step is None:
            intervals = tuple(intervals)
            sample = next((iv for iv in intervals
                           if isinstance(iv, Interval)), None)
            if sample is not None:
                domain = _DISCRETE if sample.is_discrete else _CONTINUOUS
                if sample.step is not None:
                    step = sample.step
        domain_kind, resolved_step = _resolve_domain(domain, step)
        self._step = resolved_step
        self._domain_kind = domain_kind
        self.empty_intervals = tuple(empty_intervals)

        normalized = []
        recorded_empties = list(self.empty_intervals)
        for interval in intervals:
            if not isinstance(interval, Interval):
                raise TypeError('IntervalMap expects Interval members, not %r'
                                % type(interval).__name__)
            if interval.is_discrete != (domain_kind == _DISCRETE):
                raise TypeError('interval %r does not match the %s domain'
                                % (interval, domain_kind))
            if resolved_step is not None and interval.step is not None \
                    and interval.step != resolved_step:
                raise TypeError('interval step %r != map step %r'
                                % (interval.step, resolved_step))
            if interval.is_empty:
                recorded_empties.append(interval)
                continue
            if resolved_step is None and interval.step is not None:
                resolved_step = interval.step
                self._step = resolved_step
            normalized.append(interval)

        normalized.sort(key=_lower_sort_key)
        merged = []
        for interval in normalized:
            if not merged:
                merged.append(interval)
                continue
            head = merged[-1]
            if not head.intersect(interval).is_empty or _is_adjacent(head, interval):
                merged[-1] = _merge_pair(head, interval)
            else:
                merged.append(interval)

        self._intervals = tuple(merged)
        bounded = [iv for iv in merged if iv.lower is not None]
        self._bounded_intervals = tuple(bounded)
        self._lower_keys = [
            _bound_key(iv.lower, iv.lower_inclusive) for iv in bounded]
        self.empty_intervals = tuple(recorded_empties)

    @property
    def intervals(self):
        """Tuple of the normalized non-empty member intervals."""
        return self._intervals

    @property
    def is_discrete(self):
        return self._domain_kind == _DISCRETE

    @property
    def step(self):
        return self._step

    @property
    def bounds(self):
        """``(lower, upper)`` of the whole map, possibly containing ``None``."""
        if not self._intervals:
            return (None, None)
        return (self._intervals[0].lower, self._intervals[-1].upper)

    def __len__(self):
        return len(self._intervals)

    def __bool__(self):
        return bool(self._intervals)

    def __iter__(self):
        return iter(self._intervals)

    def __getitem__(self, index):
        return self._intervals[index]

    def __eq__(self, other):
        if not isinstance(other, IntervalMap):
            return NotImplemented
        return self._intervals == other._intervals

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self):
        body = ', '.join(iv._bracket_repr() for iv in self._intervals)
        return 'IntervalMap([%s])' % body

    def get_interval(self, value):
        """Return the member interval containing ``value``, else ``None``."""
        if value is None or not self._intervals:
            return None
        head = self._intervals[0]
        if head.lower is None:
            if value in head:
                return head
        index = bisect_right(self._lower_keys,
                             (value, _EXCLUSIVE_RANK)) - 1
        if index < 0:
            return None
        candidate = self._bounded_intervals[index]
        return candidate if value in candidate else None

    def __contains__(self, value):
        return self.get_interval(value) is not None

    def _map_kwargs(self):
        if self._step is not None:
            return {'step': self._step}
        return {'domain': self._domain_kind}

    def union(self, *others):
        """Return a new map covering every member of all inputs."""
        members = list(self._intervals)
        empties = list(self.empty_intervals)
        for other in others:
            other_map = other if isinstance(other, IntervalMap) \
                else IntervalMap([other], **self._map_kwargs())
            members.extend(other_map._intervals)
            empties.extend(other_map.empty_intervals)
        return IntervalMap(members, empty_intervals=empties,
                           **self._map_kwargs())

    def __or__(self, other):
        return self.union(other)

    def __ror__(self, other):
        return self.union(other)

    def intersection(self, *others):
        """Return a new map covering only members shared by all inputs."""
        current = list(self._intervals)
        empties = list(self.empty_intervals)
        kwargs = self._map_kwargs()
        for other in others:
            other_map = other if isinstance(other, IntervalMap) \
                else IntervalMap([other], **kwargs)
            next_round = []
            for first in current:
                for second in other_map._intervals:
                    overlap = first.intersect(second)
                    if overlap.is_empty:
                        empties.append(overlap)
                    else:
                        next_round.append(overlap)
            current = next_round
            empties.extend(other_map.empty_intervals)
        return IntervalMap(current, empty_intervals=empties, **kwargs)

    def __and__(self, other):
        return self.intersection(other)

    def __rand__(self, other):
        return self.intersection(other)

    def difference(self, *others):
        """Return a new map covering members not present in any input."""
        remaining = list(self._intervals)
        empties = list(self.empty_intervals)
        kwargs = self._map_kwargs()
        for other in others:
            other_map = other if isinstance(other, IntervalMap) \
                else IntervalMap([other], **kwargs)
            next_round = []
            for target in remaining:
                pieces = [target]
                for removed in other_map._intervals:
                    updated = []
                    for piece in pieces:
                        new_pieces = _difference_pieces(
                            piece, removed, self._step)
                        for new_piece in new_pieces:
                            if new_piece.is_empty:
                                empties.append(new_piece)
                            else:
                                updated.append(new_piece)
                    pieces = updated
                next_round.extend(pieces)
            remaining = next_round
            empties.extend(other_map.empty_intervals)
        return IntervalMap(remaining, empty_intervals=empties, **kwargs)

    def __sub__(self, other):
        return self.difference(other)

    def __rsub__(self, other):
        if isinstance(other, Interval):
            return other.difference(self)
        return IntervalMap([other], **self._map_kwargs()).difference(self)
