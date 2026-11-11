Quickstart
==========

Parsing
-------

TOML Kit comes with a fast and style-preserving parser to help you access
the content of TOML files and strings::


    >>> from tomlkit import dumps
    >>> from tomlkit import parse  # you can also use loads

    >>> content = """[table]
    ... foo = "bar"  # String
    ... """
    >>> doc = parse(content)

    # doc is a TOMLDocument instance that holds all the information
    # about the TOML string.
    # It behaves like a standard dictionary.

    >>> assert doc["table"]["foo"] == "bar"

    # The string generated from the document is exactly the same
    # as the original string
    >>> assert dumps(doc) == content


Modifying
---------

TOML Kit provides an intuitive API to modify TOML documents::

    >>> from tomlkit import dumps
    >>> from tomlkit import parse
    >>> from tomlkit import table

    >>> doc = parse("""[table]
    ... foo = "bar"  # String
    ... """)

    >>> doc["table"]["baz"] = 13

    >>> dumps(doc)
    """[table]
    foo = "bar"  # String
    baz = 13
    """

    # Add a new table
    >>> tab = table()
    >>> tab.add("array", [1, 2, 3])

    >>> doc["table2"] = tab

    >>> dumps(doc)
    """[table]
    foo = "bar"  # String
    baz = 13

    [table2]
    array = [1, 2, 3]
    """

    # Remove the newly added table
    >>> doc.pop("table2")
    # del doc["table2] is also possible

Boolean comments
~~~~~~~~~~~~~~~~

Dictionary access returns a Python ``bool`` for boolean values, so comparisons
such as ``doc["enabled"] is True`` work. To access the underlying TOML item and
its formatting information (``trivia``), use ``item(key)`` on the document or
table. Read the comment with ``trivia.comment`` and update it with ``comment()``::

    >>> doc = parse("enabled = true # Root\n[database]\nenabled = false # Database\n")
    >>> doc["enabled"] is True
    True
    >>> doc["database"]["enabled"] is False
    True
    >>> doc.item("enabled").trivia.comment
    '# Root'
    >>> enabled = doc["database"].item("enabled")
    >>> enabled.trivia.comment
    '# Database'
    >>> _ = enabled.comment("Updated database comment")
    >>> print(dumps(doc), end="")
    enabled = true # Root
    [database]
    enabled = false # Updated database comment

Source ranges
~~~~~~~~~~~~~

For editor-style tooling, :func:`tomlkit.parse` can record where each parsed
item and key came from. Tracking is opt-in, so ordinary parsing keeps its
normal speed and memory profile; only ``track_sources=True`` builds the line
index used to resolve positions::

    >>> from tomlkit import parse
    >>> content = 'a = 1\ns = """\nmulti\nline"""\n'
    >>> doc = parse(content, track_sources=True)

    >>> doc["a"].range
    SourceRange(start=Position(index=4, line=1, column=4), end=Position(index=5, line=1, column=5))
    >>> content[doc["a"].range.as_slice()]
    '1'

Each :class:`~tomlkit.source.SourceRange` is a half-open ``[start, end)``
span. The coordinates are:

* ``index``: zero-based **character** offset into the decoded string (Unicode
  code points, not bytes);
* ``line``: one-based physical line number (both ``\n`` and ``\r\n`` end a
  line);
* ``column``: zero-based character offset within the line, with a tab counted
  as one character.

Byte-oriented callers can convert a span to UTF-8 offsets without storing a
second index per item::

    >>> content[doc["s"].range.as_slice()]
    '"""\nmulti\nline"""'
    >>> content.encode("utf-8")[doc["s"].range.byte_slice(content)]
    b'"""\nmulti\nline"""'

A multiline string therefore naturally gets a range spanning several lines.

Discontinuous source is represented by several ranges rather than one
fabricated span. A dotted key keeps a location per fragment; a table declared
in multiple places exposes one span per declaration through
:class:`~tomlkit.container.OutOfOrderTableProxy`; and an array of tables
exposes every ``[[header]]``::

    >>> aot_source = "[[fruit]]\nname = 'a'\n[[fruit]]\nname = 'b'\n"
    >>> doc = parse(aot_source, track_sources=True)
    >>> [aot_source[r.as_slice()] for r in doc["fruit"].ranges]
    ['[[fruit]]', '[[fruit]]']

An item that was created or edited after parsing has no original location.
``range`` is ``None`` in that case, while ``original_ranges()`` still returns
the spans recorded at parse time (if any) so callers can tell "never had a
location" apart from "its location is stale"::

    >>> doc = parse("a = 1\n", track_sources=True)
    >>> old = doc["a"]
    >>> doc["a"] = 9
    >>> old.range is None
    True
    >>> old.original_ranges()
    (SourceRange(...),)
    >>> doc["a"].range is None
    True

Writing
-------

You can also write a new TOML document from scratch.

Let's say we want to create this following document

.. code-block:: toml

    # This is a TOML document.

    title = "TOML Example"

    [owner]
    name = "Tom Preston-Werner"
    organization = "GitHub"
    bio = "GitHub Cofounder & CEO\nLikes tater tots and beer."
    dob = 1979-05-27T07:32:00Z # First class dates? Why not?

    [database]
    server = "192.168.1.1"
    ports = [ 8001, 8001, 8002 ]
    connection_max = 5000
    enabled = true

It can be created with the following code::

    >>> from tomlkit import comment
    >>> from tomlkit import document
    >>> from tomlkit import nl
    >>> from tomlkit import table

    >>> doc = document()
    >>> doc.add(comment("This is a TOML document."))
    >>> doc.add(nl())
    >>> doc.add("title", "TOML Example")
    # Using doc["title"] = "TOML Example" is also possible

    >>> owner = table()
    >>> owner.add("name", "Tom Preston-Werner")
    >>> owner.add("organization", "GitHub")
    >>> owner.add("bio", "GitHub Cofounder & CEO\nLikes tater tots and beer.")
    >>> owner.add("dob", datetime(1979, 5, 27, 7, 32, tzinfo=utc))
    >>> owner["dob"].comment("First class dates? Why not?")

    # Adding the table to the document
    >>> doc.add("owner", owner)

    >>> database = table()
    >>> database["server"] = "192.168.1.1"
    >>> database["ports"] = [8001, 8001, 8002]
    >>> database["connection_max"] = 5000
    >>> database["enabled"] = True

    >>> doc["database"] = database
