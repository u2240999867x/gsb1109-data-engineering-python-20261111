Streaming & Iteration
=====================

.. versionadded:: 19.10.0

.. automodule:: glom.streaming

.. contents:: Contents
   :local:

.. autoclass:: glom.Iter

   .. automethod:: map
   .. automethod:: filter
   .. automethod:: chunked
   .. automethod:: split
   .. automethod:: flatten
   .. automethod:: unique
   .. automethod:: limit
   .. automethod:: slice
   .. automethod:: takewhile
   .. automethod:: dropwhile
   .. automethod:: all
   .. automethod:: first

Routing one stream to several outlets
-------------------------------------

A single stream of records often has to feed more than one local
processing outlet: some want only the qualified records, others want the
rejected records together with the reason they failed. :class:`~glom.Fanout`
declares this routing up front, applies each rule only once per record,
and delivers every matching record to every matching outlet.

.. autoclass:: glom.Outlet

.. autoclass:: glom.Fanout

.. autoclass:: glom.streaming.FanoutRun

   .. automethod:: run_once
   .. automethod:: run
   .. automethod:: drain
   .. automethod:: messages

The bounded per-outlet buffers give an explicit backlog policy instead of
reading an entire input into memory. Select one with the ``on_full``
argument of :class:`~glom.Outlet`:

.. autodata:: glom.BLOCK
.. autodata:: glom.DROP_NEWEST

When a :data:`~glom.BLOCK` outlet fills and is not drained,
:meth:`~glom.streaming.FanoutRun.run` raises
:class:`glom.streaming.BackpressureFull`; the non-raising
:meth:`~glom.streaming.FanoutRun.run_once` instead reports
:data:`~glom.BACKPRESSURE` without consuming another source record.

.. autoclass:: glom.streaming.BackpressureFull
.. autodata:: glom.ROUTED
.. autodata:: glom.BACKPRESSURE
.. autodata:: glom.EXHAUSTED
.. autodata:: glom.ALL_CLOSED

