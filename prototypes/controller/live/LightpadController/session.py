"""Recording/session rules against an injected backend, without Live calls.

This module supplies no runtime simulation and no proof of Live capabilities.
The default backend denies all operations. The backend port must implement:

ready(group) -> bool
    Actual prerequisites, including transport, routing and owned tracks.
capabilities(group) -> protocol bit mask
    Only advertise recording/wrap/chaining/advance after backend validation.
reserve(group, row, exclude) -> opaque slot or None
    Reserve an empty owned layer slot, excluding these slot handles. Do not
    erase clips or create tracks. Allocation strategy belongs to the backend.
release(group, slot)
    Release an unused reservation, without deleting any clip.
queue_start(group, slot, token, mode, reference) -> Schedule
    mode is 'next_bar' or 'reference_wrap'; omit automatic record length.
    Retain valid requests at the next legal safe boundary, including Retry.
queue_finish(group, slot, token, next_slot, next_token, reference) -> FinishPlan
    Finish manually at the next safe bar into playback. If next_slot is provided, queue
    it at the first group-reference wrap AT OR AFTER that finish. A coincident
    finish/wrap starts the next take at the same boundary without another cycle.
    With no existing reference, this first finish defines the reference wrap,
    so the next take starts at the finish boundary. The backend owns the actual
    wrap calculation and must validate both operations before mutation. Failure
    changes neither. This is a backend obligation, not evidence of Live atomicity.
cancel_attempts(group, attempts)
    attempts is a tuple of (slot, token) pairs, possibly including a reserved
    chained take. Immediately cancel all pending launches/finishes and discard
    only those unaccepted takes. Failure must leave all attempts unchanged.
row_count(group) -> int
row_empty(group, row) -> bool
    Check every owned layer track, including slots not known to this model.
advance(group, row)
    Revalidate row emptiness, stop only this group's playback and select row.
    Preserve clips. Do not launch a scene or stop other groups.

Every mutating port method must revalidate group/slot ownership immediately.
Raise BackendFailure(outcome) before mutation on an invalid operation. Observer
callbacks must run after queue methods return and report actual host events.
queue_start must return a valid Schedule; queue_finish returns a valid FinishPlan.
The following take's Schedule begins its countdown at finish.start_at, with its
own start_at at the selected wrap. Plain Accept has no next_start schedule.
Cancellation must refuse
if an attempt has completed since the last delivered observation, allowing the
backend to deliver completion before a new command discards that slot.
Call observe_started(token) only after recording is observed, and
observe_finished(token, length) only after recording has ended into playback.
Before a finish is issued, observe_finish_deferred(token, FinishPlan) moves its
schedule and a still-queued child's schedule together to a later safe bar/wrap.
Keep the original request time. A refused callback must leave backend targets
unchanged. Issued native effects may never be retargeted or retried this way.
Before a start is issued, observe_start_deferred(token, Schedule) retains the
queued generation while moving its start later. Original request time stays
unchanged. Native recording must be observed at the revised exact anchor.
Lengths and Schedule times are in beats, never wall-clock estimates.

The model must be initialized with fresh, empty scratch phrase rows. Restoring
accepted layers after script reload needs a separate, explicit backend design.
"""

from collections import namedtuple
import math

from . import protocol as p


OVERVIEW_VIEW, QUEUED_VIEW, RECORDING_VIEW, FINISH_VIEW, COMPLETED_VIEW = range(5)
Schedule = namedtuple("Schedule", "queued_at start_at")
FinishPlan = namedtuple("FinishPlan", "finish next_start")
Layer = namedtuple("Layer", "slot length")


class BackendFailure(Exception):
    def __init__(self, outcome=p.NOT_READY):
        super(BackendFailure, self).__init__("backend refused operation")
        self.outcome = outcome


class UnavailableBackend:
    def ready(self, group):
        return False

    def capabilities(self, group):
        return 0


class Group:
    def __init__(self):
        self.row = 0
        self.phrases = {0: []}

    @property
    def accepted(self):
        return self.phrases[self.row]

    @property
    def reference(self):
        return self.accepted[0] if self.accepted else None


class Take:
    def __init__(self, group, slot, token):
        self.group = group
        self.slot = slot
        self.token = token
        self.state = "queued"
        self.schedule = None
        self.next_take = None


class Session:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else UnavailableBackend()
        self.groups = [Group() for _ in range(p.GROUP_COUNT)]
        self.take = None
        self.recording_group = None
        self.view = OVERVIEW_VIEW
        self.view_group = None
        self._completed_token = None
        self._generation = 0

    def _gate(self, group, capabilities):
        if type(group) is not int or not 0 <= group < len(self.groups):
            return p.BAD_COMMAND
        if not self.backend.ready(group):
            return p.NOT_READY
        available = self.backend.capabilities(group)
        if available & capabilities != capabilities:
            return p.NOT_READY
        return None

    def _new_take(self, group, exclude=()):
        slot = self.backend.reserve(group, self.groups[group].row, tuple(exclude))
        if slot is None:
            raise BackendFailure(p.CAPACITY)
        self._generation += 1
        return Take(group, slot, self._generation)

    @staticmethod
    def _schedule(value):
        if (not isinstance(value, Schedule)
                or not all(type(v) in (int, float) and math.isfinite(v) for v in value)
                or value.start_at < value.queued_at):
            raise BackendFailure(p.NOT_READY)
        return value

    @classmethod
    def _finish_plan(cls, value, has_next, reference):
        if not isinstance(value, FinishPlan):
            raise BackendFailure(p.NOT_READY)
        cls._schedule(value.finish)
        if not has_next:
            if value.next_start is not None:
                raise BackendFailure(p.NOT_READY)
        else:
            following = cls._schedule(value.next_start)
            if (following.queued_at != value.finish.start_at
                    or following.start_at < value.finish.start_at
                    or (reference is None and following.start_at != value.finish.start_at)):
                raise BackendFailure(p.NOT_READY)
        return value

    def _start(self, group, mode):
        take = None
        try:
            take = self._new_take(group)
            take.schedule = self._schedule(self.backend.queue_start(
                group, take.slot, take.token, mode, self.groups[group].reference))
        except BackendFailure as error:
            if take is not None:
                self._release(group, take.slot)
            return error.outcome
        self.take = take
        self.recording_group = group
        self.view = QUEUED_VIEW
        self.view_group = group
        self._completed_token = None
        return p.OUTCOME_QUEUED

    def _release(self, group, slot):
        try:
            self.backend.release(group, slot)
        except BackendFailure:
            # A removed track can invalidate both the operation and its unused
            # reservation. Never bypass ownership guards to force cleanup.
            pass

    def begin(self, group):
        denied = self._gate(group, p.CAP_RECORDING)
        if denied is not None:
            return denied
        if self.recording_group is not None:
            return p.BUSY
        reference = self.groups[group].reference
        if reference is not None:
            denied = self._gate(group, p.CAP_RECORDING | p.CAP_REFERENCE_WRAP)
            if denied is not None:
                return denied
        return self._start(group, "reference_wrap" if reference else "next_bar")

    def accept(self, group, next_take=False):
        capability = p.CAP_RECORDING | (p.CAP_CHAINING if next_take else 0)
        denied = self._gate(group, capability)
        if denied is not None:
            return denied
        reference = self.groups[group].reference
        if next_take and reference is not None:
            denied = self._gate(group, capability | p.CAP_REFERENCE_WRAP)
            if denied is not None:
                return denied
        current = self.take
        if current is None or current.group != group or current.state != "recording":
            return p.NOT_READY
        following = None
        try:
            if next_take:
                following = self._new_take(group, (current.slot,))
            plan = self._finish_plan(self.backend.queue_finish(
                group, current.slot, current.token,
                following.slot if following else None,
                following.token if following else None, reference), next_take, reference)
        except BackendFailure as error:
            if following is not None:
                self._release(group, following.slot)
            return error.outcome
        current.state = "finish_queued"
        current.schedule = plan.finish
        current.next_take = following
        if following:
            following.schedule = plan.next_start
        self.view = FINISH_VIEW
        return p.OUTCOME_QUEUED

    def _discard(self, group):
        if self.take is not None:
            attempts = [(self.take.slot, self.take.token)]
            if self.take.next_take is not None:
                following = self.take.next_take
                if following.state == "recording":
                    # Next-start was observed before old-finish. Await old
                    # completion so Abort cannot erase its accepted layer.
                    raise BackendFailure(p.BUSY)
                attempts.append((following.slot, following.token))
            self.backend.cancel_attempts(group, tuple(attempts))
        self.take = None

    def retry(self, group):
        denied = self._gate(group, p.CAP_RECORDING)
        if denied is not None:
            return denied
        reference = self.groups[group].reference
        if reference is not None:
            denied = self._gate(group, p.CAP_RECORDING | p.CAP_REFERENCE_WRAP)
            if denied is not None:
                return denied
        if self.recording_group != group:
            return p.NOT_READY
        try:
            self._discard(group)
        except BackendFailure as error:
            return error.outcome
        # A failed new reservation keeps the recording controls open. There is
        # no invented pending start, and another Retry can try again.
        self.view = QUEUED_VIEW
        return self._start(group, "reference_wrap" if reference else "next_bar")

    def abort(self, group):
        # Cancel remains possible when transport or routing has become unready.
        # The backend still validates exact ownership before discarding a take.
        if type(group) is not int or not 0 <= group < len(self.groups):
            return p.BAD_COMMAND
        if self.recording_group != group:
            return p.NOT_READY
        try:
            self._discard(group)
        except BackendFailure as error:
            return error.outcome
        self.recording_group = None
        self.view = OVERVIEW_VIEW
        self.view_group = None
        self._completed_token = None
        return p.APPLIED

    def advance(self, group):
        denied = self._gate(group, p.CAP_ADVANCE)
        if denied is not None:
            return denied
        if self.recording_group is not None:
            return p.BUSY
        state = self.groups[group]
        try:
            row = next((row for row in range(state.row + 1, self.backend.row_count(group))
                        if self.backend.row_empty(group, row)), None)
            if row is None:
                return p.CAPACITY
            self.backend.advance(group, row)
        except BackendFailure as error:
            return error.outcome
        state.row = row
        state.phrases[row] = []
        return p.APPLIED

    def observe_started(self, token):
        current = self.take
        if current is None:
            return False
        candidate = current if current.token == token else current.next_take
        if candidate is None or candidate.token != token or candidate.state != "queued":
            return False
        candidate.state = "recording"
        candidate.schedule = None
        if candidate is current:
            self.view = RECORDING_VIEW
        return True

    def observe_finished(self, token, length):
        current = self.take
        if (current is None or current.token != token or current.state != "finish_queued"
                or type(length) not in (int, float) or not math.isfinite(length) or length <= 0):
            return False
        self.groups[current.group].accepted.append(Layer(current.slot, length))
        self.take = current.next_take
        if self.take is None:
            self.recording_group = None
            self.view = COMPLETED_VIEW
            self._completed_token = current.token
        else:
            self.view = RECORDING_VIEW if self.take.state == "recording" else QUEUED_VIEW
        return True

    def observe_finish_deferred(self, token, plan):
        """Move an unissued finish and its queued child together, without effects."""
        current = self.take
        if (type(token) is not int or current is None or current.token != token
                or current.state != "finish_queued"
                or not isinstance(current.schedule, Schedule)):
            return False
        following = current.next_take
        if following is not None and (following.state != "queued"
                                      or not isinstance(following.schedule, Schedule)):
            return False
        try:
            self._schedule(current.schedule)
            if following is not None:
                self._schedule(following.schedule)
            plan = self._finish_plan(plan, following is not None,
                                     self.groups[current.group].reference)
        except BackendFailure:
            return False
        if (plan.finish.queued_at != current.schedule.queued_at
                or plan.finish.start_at <= current.schedule.start_at):
            return False
        current.schedule = plan.finish
        if following is not None:
            following.schedule = plan.next_start
        return True

    def observe_start_deferred(self, token, schedule):
        """Retain a queued generation while its unissued start moves later."""
        if type(token) is not int or self.take is None:
            return False
        candidate = self.take if self.take.token == token else self.take.next_take
        if (candidate is None or candidate.token != token or candidate.state != "queued"
                or not isinstance(candidate.schedule, Schedule)):
            return False
        try:
            self._schedule(candidate.schedule)
            self._schedule(schedule)
        except BackendFailure:
            return False
        if (schedule.queued_at != candidate.schedule.queued_at
                or schedule.start_at <= candidate.schedule.start_at):
            return False
        candidate.schedule = schedule
        return True

    def countdown(self, now):
        """Return CC94 from an actual backend schedule, or 127 if unavailable."""
        current = self.take
        if current is None or current.state != "queued" or current.schedule is None:
            return 127
        schedule = current.schedule
        duration = schedule.start_at - schedule.queued_at
        if duration <= 0:
            return 126
        return round(126 * min(1.0, max(0.0, (now - schedule.queued_at) / duration)))

    def view_for(self, group):
        return self.view if self.view_group == group else OVERVIEW_VIEW

    def take_id_for(self, group):
        """CC107, meaningful only with a nonzero recording-view value."""
        if self.view_group != group:
            return 0
        if self.take is not None:
            return self.take.token % 128
        return self._completed_token % 128 if self._completed_token is not None else 0

    def acknowledge_completion(self, group, take_id):
        """Automatic UI receipt, never a second Accept or a clip mutation.

        Terminal view 4 closes the matching UI immediately. The receipt clears
        its retained notification; it cannot close another active take.
        """
        if (type(group) is not int or not 0 <= group < len(self.groups)
                or type(take_id) is not int or not 0 <= take_id < 128):
            return p.BAD_COMMAND
        if (self.view != COMPLETED_VIEW or self.view_group != group
                or self._completed_token is None
                or self._completed_token % 128 != take_id):
            return p.STALE
        self.view = OVERVIEW_VIEW
        self.view_group = None
        self._completed_token = None
        return p.APPLIED
