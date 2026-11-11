class MissingType(object):
    """Sentinel type for fields that are absent from the input data.

    This value is only produced when evaluating an expression with
    missing-field preservation enabled (see ``jmespath.inspect()`` and
    ``Options(preserve_missing=True)``).  A normal ``jmespath.search()``
    call never produces this value; absent fields evaluate to None.

    The sentinel is a singleton so it can be checked with ``is``::

        result is jmespath.MISSING
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MissingType, cls).__new__(cls)
        return cls._instance

    def __repr__(self):
        return 'MISSING'

    def __bool__(self):
        return False


MISSING = MissingType()
