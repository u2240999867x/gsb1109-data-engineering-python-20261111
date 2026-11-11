"""Static field-requirement analysis for JMESPath expressions.

This module derives the set of data paths that must be made available to
a JMESPath expression without ever evaluating the expression against real
data.  It is intended for use with APIs that only return fields the caller
explicitly requested: the output tells you which paths to request upstream.

Two kinds of requirements are reported:

* Concrete paths (``paths``): field chains that can be pinned down exactly,
  for example ``a.b.c`` or ``users[0].name``.  Requesting these paths is
  always required.
* Unknown regions (``unknowns``): paths below which a wildcard, a filter,
  a slice/flatten projection, a dynamic expression or a function makes it
  impossible to name every accessed field statically, for example ``users``
  for ``users[?age > 18].name``.  An unknown region means "request the
  whole subtree rooted here"; it is never safe to assume the concrete paths
  alone are sufficient.  ``'*'`` denotes the entire document.

The analysis is purely syntactic: it walks the parsed AST only.  It never
touches input data and never resolves (and therefore never calls) any
function, including custom functions.
"""
from jmespath import parser
from jmespath import visitor


# Marker used when no field path can even name the required region; it means
# the entire input document is required.
ROOT_PATH = '*'


class FieldRequirement(object):
    """Result of a static field-requirement analysis.

    :ivar expression: the JMESPath expression that was analyzed.
    :ivar paths: sorted tuple of concrete, requestable data paths such as
        ``('a.b.c',)``.  Every path here must be requested from upstream.
    :ivar unknowns: sorted tuple of paths such as ``('users',)`` below
        which the exact set of fields cannot be determined.  The entire
        subtree rooted at each path must be requested; ``'*'`` means the
        whole document.
    """

    def __init__(self, expression, paths, unknowns):
        self.expression = expression
        self.paths = tuple(sorted(set(paths)))
        self.unknowns = tuple(sorted(set(unknowns)))

    @property
    def is_complete(self):
        """``True`` when every requirement is a concrete path.

        A ``False`` value means at least one wildcard, filter, projection
        or dynamic expression forces an entire subtree to be requested.
        """
        return not self.unknowns

    def as_dict(self):
        return {
            'expression': self.expression,
            'paths': list(self.paths),
            'unknowns': list(self.unknowns),
            'complete': self.is_complete,
        }

    def __eq__(self, other):
        if not isinstance(other, FieldRequirement):
            return NotImplemented
        return (self.expression == other.expression and
                self.paths == other.paths and
                self.unknowns == other.unknowns)

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self):
        return ('FieldRequirement(expression=%r, paths=%r, unknowns=%r)'
                % (self.expression, list(self.paths), list(self.unknowns)))


class _Location(object):
    """The statically known location of a node's result.

    ``path`` is the rendered path from the document root (``''`` for the
    document itself).  ``opaque`` is ``True`` once a projection, slice,
    flatten operation or dynamic branch has made the exact fields below the
    location unnameable.  ``detached`` marks literal-only values and
    ``synthetic`` marks computed values whose inputs are already recorded.
    """

    __slots__ = ('path', 'opaque', 'detached', 'synthetic')

    def __init__(self, path='', opaque=False, detached=False,
                 synthetic=False):
        self.path = path
        self.opaque = opaque
        # A detached location is produced solely from literals and is backed
        # by no real data path, so it must never be turned into a requirement.
        self.detached = detached
        # A synthetic location holds a value computed from data whose inputs
        # have already been recorded (for example a function result); it has
        # no root-addressable path, so further subfield reads add nothing.
        self.synthetic = synthetic

    def child(self, field_name):
        if self.path:
            path = self.path + '.' + field_name
        else:
            path = field_name
        return _Location(path, self.opaque, self.detached, self.synthetic)

    def opaque_value(self):
        # Mark the whole value at this location as consumed in a way whose
        # subfields cannot be named, such as the elements of a wildcard or
        # filter projection, or an argument handed to a function.
        return _Location(self.path, True, self.detached, self.synthetic)

    def as_synthetic(self):
        return _Location(self.path, True, self.detached, True)


class _FieldRequirementVisitor(visitor.Visitor):
    """Collects concrete paths and unknown regions from an AST."""

    def __init__(self):
        super(_FieldRequirementVisitor, self).__init__()
        self.paths = set()
        self.unknowns = set()

    def analyze(self, node):
        location = self.visit(node, _Location())
        # If the whole expression yields the input value itself (for example
        # a bare ``@``), the entire document is required.
        self._require(location)
        return self.paths, self.unknowns

    def visit(self, node, location):
        if location.detached:
            # A value built only from literals is backed by no data.
            return location
        if location.synthetic:
            # A computed value whose inputs are already recorded; reading
            # further subfields off it adds no root-addressable requirement.
            return location
        if location.opaque:
            # The enclosing unknown region already covers every field below
            # this node, so no further requirement can be named concretely.
            self.unknowns.add(location.path or ROOT_PATH)
            return location
        method = self._method_cache.get(node['type'])
        if method is None:
            method = getattr(self, 'visit_%s' % node['type'],
                             self.default_visit)
            self._method_cache[node['type']] = method
        return method(node, location)

    def _require(self, location):
        if location.detached or location.synthetic:
            return
        if location.opaque:
            self.unknowns.add(location.path or ROOT_PATH)
        elif location.path:
            self.paths.add(location.path)
        else:
            # The whole document is required but cannot be narrowed down.
            self.unknowns.add(ROOT_PATH)

    # ---- scalar / structural nodes -------------------------------------

    def visit_identity(self, node, location):
        # Pass-through: the value is only required if a consumer evaluates it
        # as a whole (the caller records that).
        return location

    def visit_current(self, node, location):
        return location

    def visit_field(self, node, location):
        target = location.child(node['value'])
        self._require(target)
        return target

    def visit_literal(self, node, location):
        # Literals are embedded in the expression and need no input data.
        return _Location(detached=True)

    def visit_index(self, node, location):
        # A fixed index is statically addressable: foo[0].bar stays concrete.
        # Indexing the document root has no field name to request, so the
        # whole document is the only safe requirement.
        if not location.path:
            target = location.opaque_value()
        else:
            target = _Location(location.path + '[%s]' % node['value'],
                               location.opaque, location.detached,
                               location.synthetic)
        self._require(target)
        return target

    def visit_slice(self, node, location):
        # A slice spans a dynamic range of elements, so only the whole
        # containing array can be named safely.
        target = location.opaque_value()
        self._require(target)
        return target

    def visit_subexpression(self, node, location):
        return self._visit_chain(node['children'], location)

    def visit_index_expression(self, node, location):
        return self._visit_chain(node['children'], location)

    def _visit_chain(self, children, location):
        for child in children:
            location = self.visit(child, location)
        return location

    # ---- boolean / branching nodes --------------------------------------

    def visit_pipe(self, node, location):
        # The RHS is evaluated against the LHS result.  When the LHS resolves
        # to a concrete field path (``a.b | c.d``), fields in the RHS are
        # simply deeper subpaths (``a.b.c.d``).  If the LHS is already
        # opaque/synthetic/detached, that state carries into the RHS and no
        # further root-addressable requirement is produced.
        intermediate = self.visit(node['children'][0], location)
        return self.visit(node['children'][1], intermediate)

    def visit_or_expression(self, node, location):
        # Either branch may supply the value, so both are required.  The
        # result cannot be attributed to a single field downstream.
        for child in node['children']:
            self._require(self.visit(child, location))
        return location.as_synthetic()

    def visit_and_expression(self, node, location):
        for child in node['children']:
            self._require(self.visit(child, location))
        return location.as_synthetic()

    def visit_not_expression(self, node, location):
        self._require(self.visit(node['children'][0], location))
        return location.as_synthetic()

    def visit_comparator(self, node, location):
        # Both operands are evaluated against the same input.
        for child in node['children']:
            self._require(self.visit(child, location))
        return location.as_synthetic()

    # ---- multi-select nodes ---------------------------------------------

    def visit_multi_select_dict(self, node, location):
        for child in node['children']:
            self._require(self.visit(child, location))
        # The assembled object is a new value; any field chained onto it
        # cannot be named from the document root.
        return location.as_synthetic()

    def visit_multi_select_list(self, node, location):
        for child in node['children']:
            self._require(self.visit(child, location))
        return location.as_synthetic()

    def visit_key_val_pair(self, node, location):
        return self.visit(node['children'][0], location)

    # ---- projection nodes -----------------------------------------------

    def visit_projection(self, node, location):
        return self._visit_projection(node, location, condition=None)

    def visit_value_projection(self, node, location):
        return self._visit_projection(node, location, condition=None)

    def visit_filter_projection(self, node, location):
        return self._visit_projection(node, location,
                                      condition=node['children'][2])

    def _visit_projection(self, node, location, condition):
        left = node['children'][0]
        right = node['children'][1]
        container = self.visit(left, location)
        if not container.opaque:
            # Both wildcard and filter projections iterate over an
            # unspecified set of elements, so the whole container has to be
            # requested and no element-level field can be named.
            region = container.opaque_value()
            self._require(region)
        else:
            region = container
        if condition is not None:
            # The condition runs for every element; the unknown region
            # already covers every field it may touch.
            self.visit(condition, region)
        self.visit(right, region)
        return region

    def visit_flatten(self, node, location):
        child = node['children'][0]
        container = self.visit(child, location)
        if not container.opaque:
            region = container.opaque_value()
            self._require(region)
        else:
            region = container
        return region

    # ---- functions and expression references ----------------------------

    def visit_expref(self, node, location):
        # An expression reference is evaluated later by its caller against
        # data this node cannot name, so treat it as an unknown region.
        region = location.opaque_value()
        self._require(region)
        self.visit(node['children'][0], region)
        return region

    def _require_opaque(self, location):
        # Record that an entire value (not just named subfields) is read.
        if location.detached or location.synthetic:
            return
        self.unknowns.add(location.path or ROOT_PATH)

    def visit_function_expression(self, node, location):
        # Functions are never called.  Each data argument is consumed as a
        # whole value, so it becomes an unknown region; multi-selects only
        # read their explicitly named children, and literals read nothing.
        array_locations = []
        all_detached = True
        for child in node['children']:
            if child['type'] == 'expref':
                all_detached = False
                continue
            result = self.visit(child, location)
            if not result.detached:
                all_detached = False
            if child['type'] not in ('literal', 'multi_select_dict',
                                     'multi_select_list') and \
                    not result.detached:
                self._require_opaque(result)
            if not result.detached and not result.synthetic:
                array_locations.append(result)
        if all_detached:
            # A call such as abs(`-5`) depends on real data nowhere.
            return _Location(detached=True)
        # An expression reference is applied by the function to the elements
        # of a sibling array argument, so open that container as the region.
        for child in node['children']:
            if child['type'] != 'expref':
                continue
            if array_locations:
                region = array_locations[0].opaque_value()
            else:
                region = location.opaque_value()
            self._require(region)
            self.visit(child['children'][0], region)
        # The value a function returns is dynamically computed from inputs
        # that are already recorded; any navigation chained onto it has no
        # root-addressable path of its own.
        return location.as_synthetic()

    def default_visit(self, node, location):
        # An unhandled node type must never be silently skipped, otherwise
        # a branch could drop out of the requirements.
        raise NotImplementedError(
            'static analysis does not support node type %s' % node['type'])


def _is_under(prefix, path):
    """Return ``True`` when ``path`` lies inside the region ``prefix``."""
    if prefix == ROOT_PATH:
        # The root marker covers the entire document.
        return True
    if path == prefix:
        return True
    return path.startswith(prefix + '.') or path.startswith(prefix + '[')


def _prune(paths, unknowns):
    # Drop unknown regions already enclosed by a broader unknown region.
    kept_unknowns = set()
    for candidate in unknowns:
        if not any(other != candidate and _is_under(other, candidate)
                   for other in unknowns):
            kept_unknowns.add(candidate)
    # A concrete path nested inside an unknown region is already requested
    # as part of that subtree, so listing it separately is redundant.
    kept_paths = set()
    for path in paths:
        if not any(_is_under(unknown, path) for unknown in kept_unknowns):
            kept_paths.add(path)
    return kept_paths, kept_unknowns


def analyze(expression):
    """Statically derive the data paths an expression requires.

    :param expression: a JMESPath expression string.
    :returns: a :class:`FieldRequirement` describing concrete paths and
        unknown regions.  Invalid expressions raise the same exceptions as
        :func:`jmespath.compile`.
    """
    parsed = parser.Parser().parse(expression)
    visitor_ = _FieldRequirementVisitor()
    paths, unknowns = visitor_.analyze(parsed.parsed)
    paths, unknowns = _prune(paths, unknowns)
    return FieldRequirement(expression, paths, unknowns)
