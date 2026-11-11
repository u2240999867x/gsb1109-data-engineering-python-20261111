Key Remapping & Normalization
=============================

.. automodule:: glom.remap

.. contents:: Contents
   :local:

Remapping
---------

Normalize mapping keys (and values, if needed) before the data lands
in your data store. Collisions between original keys that normalize to
the same key are handled explicitly, never by silently letting the
last key win.

.. autofunction:: glom.remap

.. autoclass:: glom.Remap

Collision modes
~~~~~~~~~~~~~~~

.. autodata:: glom.REJECT

.. autodata:: glom.COLLECT

.. autodata:: glom.MERGE

Exceptions
----------

Key-transform failures and value-transform failures are reported with
distinct exception types, and every error retains the original key it
stemmed from. When a single pass hits more than one failure, they are
aggregated into one :class:`~glom.RemapError` rather than failing on
the first one.

.. autoclass:: glom.RemapError

.. autoclass:: glom.KeyTransformError

.. autoclass:: glom.ValueTransformError

.. autoclass:: glom.KeyCollisionError

.. autoclass:: glom.RemapEntry
