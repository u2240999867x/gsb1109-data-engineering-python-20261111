API Reference
=============

Public API
----------

Functions you need to create TOML elements to construct
a TOML document.

.. automodule:: tomlkit
   :members:
   :undoc-members:
   :show-inheritance:

TOML Document
-------------

.. module:: tomlkit.toml_document

.. autoclass:: TOMLDocument
   :show-inheritance:
   :members: add, append, as_string, clear, copy, fromkeys, get, item, items, keys,
             last_item, pop, popitem, remove, setdefault, unwrap, update, value,
             values

Container
---------

.. autoclass:: tomlkit.container.Container


TOML File
---------

.. module:: tomlkit.toml_file

.. autoclass:: TOMLFile
   :show-inheritance:
   :members:


TOML Items
----------

.. automodule:: tomlkit.items
   :show-inheritance:
   :members:
   :exclude-members: item, AbstractTable

TOML Exceptions
---------------

.. automodule:: tomlkit.exceptions
   :members:
   :show-inheritance:

Source Ranges
-------------

Optional source-span records produced by ``parse(..., track_sources=True)``.
Offsets are character based; use ``byte_offset`` / ``SourceRange.byte_slice``
for UTF-8 byte coordinates.

.. automodule:: tomlkit.source
   :members: Position, SourceRange, SourceTracker, byte_offset
   :show-inheritance:
