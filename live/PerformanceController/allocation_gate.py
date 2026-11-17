"""Announce overflow preparation before entering Live's structural API.

A matching device/bridge grant is permission to try, never an allocation result.
Expired or changed requests clear without performing or replaying Native work.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from .model import same

LEASE_SECONDS = 10.
BUSY_KIND, BUSY_SLOT = 6, 3


@dataclass(frozen=True)
class AllocationContext:
    group: int
    parent: object
    anchor: object
    row: int
    revision: int

    def matches(self, other):
        return (other is not None and self.group == other.group
                and self.row == other.row and self.revision == other.revision
                and same(self.parent, other.parent) and same(self.anchor, other.anchor))


@dataclass
class Request:
    ticket: int
    context: AllocationContext
    announced_at: float
    callback: int
    published_at: float
    granted_callback: object = None
    started: bool = False


class AllocationGate:
    def __init__(self, clock, emit):
        self.clock, self.emit = clock, emit
        self.pending = None
        self.callback = 0
        self._next_ticket = 0
        self._cleared_callback = -1

    @property
    def ticket(self):
        return self.pending.ticket if self.pending is not None else None

    @property
    def group(self):
        return self.pending.context.group if self.pending is not None else None

    @property
    def granted(self):
        return self.pending is not None and self.pending.granted_callback is not None

    def clear(self, publish=False):
        had_request = self.pending is not None
        self.pending = None
        if had_request or publish:
            self._cleared_callback = self.callback + 1
            self.emit(BUSY_KIND, BUSY_SLOT, 0, True)

    def expire(self):
        request = self.pending
        if request is not None and not request.started and self.clock() - request.announced_at >= LEASE_SECONDS:
            self.clear()
            return True
        return False

    def begin_callback(self):
        self.callback += 1
        self.expire()
        request = self.pending
        if request is not None and self.clock() - request.published_at >= .5:
            request.published_at = self.clock()
            self.emit(BUSY_KIND, BUSY_SLOT, request.ticket, True)

    def release(self, group):
        if self.group == group:
            self.clear()

    def grant(self, ticket):
        # This is called from MIDI dispatch; read no Native object or graph.
        if self.expire() or self.pending is None or ticket != self.pending.ticket:
            return False
        if self.pending.started:
            return False
        if self.pending.granted_callback is None:
            self.pending.granted_callback = self.callback
        return True

    def validate(self, context):
        if self.pending is not None and not self.pending.context.matches(context):
            self.clear()

    @contextmanager
    def operation(self, context):
        self.expire()
        request = self.pending
        if request is not None and not request.context.matches(context):
            if request.context.group != context.group:
                yield False
                return
            self.clear()
            request = None
        if request is None:
            # Clearing gets a full publication callback so the bridge can release its
            # failed/expired lease before another positive ticket is published.
            if self.callback <= self._cleared_callback:
                yield False
                return
            self._next_ticket = self._next_ticket % 16382 + 1
            self.pending = Request(self._next_ticket, context, self.clock(), self.callback, self.clock())
            self.emit(BUSY_KIND, BUSY_SLOT, self.pending.ticket, True)
            yield False
            return
        allowed = (request.granted_callback is not None
                   and self.callback > request.callback
                   and self.callback > request.granted_callback)
        if not allowed:
            yield False
            return
        request.started = True
        try:
            yield True
        except BaseException:
            # Permission spans the complete ready bundle, but any uncertain
            # Native attempt terminates it instead of replaying the operation.
            self.clear()
            raise
