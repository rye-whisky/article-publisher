# -*- coding: utf-8 -*-
"""Log broadcaster — pushes log records to SSE clients via asyncio Queues."""

import asyncio
import logging


class LogBroadcaster(logging.Handler):
    """Custom logging handler that broadcasts formatted log messages
    to all connected SSE clients."""

    def __init__(self, max_queue_size: int = 1000):
        super().__init__()
        self._queues: list = []
        self._loop = None
        self._max_queue_size = max_queue_size

    def set_loop(self, loop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._queues:
            self._queues.remove(q)

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        if self._loop is None:
            return
        for q in list(self._queues):
            try:
                self._loop.call_soon_threadsafe(self._safe_put, q, msg)
            except RuntimeError:
                pass

    @staticmethod
    def _safe_put(q: asyncio.Queue, msg: str):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass
