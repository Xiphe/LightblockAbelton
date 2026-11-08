"""Native audio executor for Session, gated independently of offline tests.

Session port calls plan effects without native writes. tick() executes them
after Session records its intent. Native failures quarantine the group, while
already requested finishes continue to be observed so accepted audio survives.
Late unissued starts retain their queued generation at the next safe bar/wrap.
Late finishes move together with their queued child and Session projection.
Issued effects retain their exact target.
This module never changes transport, routing, monitoring, mute or global record.

Normal capability evidence names are record, cancel, wrap, accept_next, advance.
probe=True bypasses that evidence gate only for an explicitly isolated lab.
It does not constitute evidence or enable the backend automatically.
"""

from collections import namedtuple
import math
import time

from . import protocol as p
from .session import BackendFailure, Schedule, FinishPlan
from .recording_coverage import sample_covers
from .launch_admission import LaunchAdmission


Lease = namedtuple("Lease", "group row layer generation")
_EPSILON = 1e-7
_TRANSACTION_LEAD = .100
_FIRE_LEAD = .050


class _LaunchDeadline(BackendFailure):
    """A known refusal before a native enqueue, eligible for empty-arm cleanup."""

    def __init__(self, message="insufficient time before native enqueue boundary"):
        super().__init__(p.NOT_READY)
        self.args = (message,)


class _Attempt:
    def __init__(self, lease, track, slot, ancestors=()):
        self.lease, self.track, self.slot = lease, track, slot
        self.ancestors = tuple(ancestors)
        self.token = None
        self.clip = None
        self.state = "reserved"
        self.start_target = None
        self.start_queued_at = None
        self.start_mode = None
        self.start_reference = None
        self.start_cleanup_pending = False
        self.start_disarm_issued = False
        self.start_issued = False
        self.finish_target = None
        self.finish_queued_at = None
        self.finish_reference = None
        self.finish_issued = False
        self.length_write_issued = False
        self.recording_coverage = None
        self.next_lease = None
        self.finish_parent = None
        self.cancel_requested = False
        self.stop_issued = False
        self.delete_issued = False
        self.cancel_finish_intent = False


class LiveAudioBackend:
    def __init__(self, fixture, emit=None, probe=False, validated_features=()):
        self.fixture = fixture
        self.song = fixture.song
        self.emit = emit if emit is not None else lambda event: None
        self.probe = bool(probe)
        self.validated_features = frozenset(validated_features)
        self.enabled = False
        self.faults = {}
        self.session = None
        self._generation = 0
        self._attempts = {}
        self._layers = {}
        self._slots = {}
        self._advances = {}
        self._idle_overdub = None

    def bind_session(self, session):
        if self.session is not None and self.session is not session:
            raise BackendFailure(p.BUSY)
        self.session = session

    @staticmethod
    def _layer_list(binding):
        return tuple(binding.layers)

    def enable(self):
        if self.enabled:
            raise BackendFailure(p.BUSY)
        self.fixture.validate(allowed_armed=(), require_transport=False)
        if self.song.session_record:
            raise BackendFailure(p.NOT_READY)
        layers, slots = {}, {}
        for group, binding in self.fixture.groups.items():
            layers[group] = self._layer_list(binding)
            for index, track in enumerate(layers[group]):
                if track.arm or getattr(track, "implicit_arm", False):
                    raise BackendFailure(p.NOT_READY)
                if track.playing_slot_index not in (-1, -2) or track.fired_slot_index != -1:
                    raise BackendFailure(p.NOT_READY)
                for row, slot in enumerate(track.clip_slots):
                    if slot.has_clip or slot.is_triggered:
                        raise BackendFailure(p.NOT_READY)
                    slots[group, row, index] = slot
        if not layers or any(not tracks for tracks in layers.values()):
            raise BackendFailure(p.NOT_READY)
        self._layers, self._slots = layers, slots
        self._attempts.clear()
        self._advances.clear()
        self.faults.clear()
        self._idle_overdub = bool(self.song.overdub)
        self.enabled = True
        return self.diagnostic_state()

    def disable(self):
        if any(a.state not in ("reserved", "released", "accepted") for a in self._attempts.values()) or self._advances:
            raise BackendFailure(p.BUSY)
        if any(track.arm for tracks in self._layers.values() for track in tracks):
            raise BackendFailure(p.BUSY)
        self.enabled = False

    def _armed(self):
        return tuple(dict.fromkeys(a.track for a in self._attempts.values()
                                   if a.state not in ("released", "reserved", "accepted") and a.track.arm))

    def owned_armed_tracks(self):
        """Actual armed tracks belonging to retained active backend leases."""
        return self._armed()

    def needs_tick(self):
        """Use fast native notifications only while an owned effect is pending."""
        return self.enabled and (bool(self._advances) or any(
            attempt.lease.group not in self.faults and
            (attempt.state in ('queued', 'finish_planned', 'canceling')
             or attempt.state == 'recording' and attempt.finish_target is not None)
            for attempt in self._attempts.values()))

    def validate_scope(self):
        """Read-only shared guard for mixer/mute/tap while owned takes run."""
        self._validate()
        return True

    def _validate(self, transport=False):
        try:
            self.fixture.validate(allowed_armed=self._armed(), require_transport=transport)
            for group, tracks in self._layers.items():
                if group not in self.fixture.groups or self._layer_list(self.fixture.groups[group]) != tracks:
                    raise BackendFailure(p.STALE)
            if self.enabled and not self._owns_native_transition():
                if self.song.session_record or bool(self.song.overdub) != self._idle_overdub:
                    raise BackendFailure(p.NOT_READY)
        except BackendFailure:
            raise
        except Exception as error:
            raise BackendFailure(p.NOT_READY) from error

    @staticmethod
    def _clock_call(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except BackendFailure:
            raise
        except Exception as error:
            raise BackendFailure(p.NOT_READY) from error

    def ready(self, group):
        if not self.enabled or group not in self._layers or group in self.faults or self.pending_advance(group):
            return False
        try:
            self._validate(transport=True)
        except Exception:
            return False
        return True

    def capabilities(self, group):
        if not self.ready(group):
            return 0
        return self._ready_capabilities()

    def read_capabilities(self, group):
        """Read-only projection after the controller validated this epoch.

        Native mutation admission always uses capabilities()/ready(), whose
        full fixture guard is deliberately uncached.
        """
        if (not self.enabled or group not in self._layers or group in self.faults
                or self.pending_advance(group)):
            return 0
        running = self.song.is_playing
        if type(running) not in (bool, int) or running not in (0, 1) or not running:
            return 0
        return self._ready_capabilities()

    def _ready_capabilities(self):
        evidence = self.validated_features
        recording = self.probe or {"record", "cancel"} <= evidence
        result = p.CAP_RECORDING if recording else 0
        if recording and (self.probe or "wrap" in evidence):
            result |= p.CAP_REFERENCE_WRAP
        if recording and (self.probe or "accept_next" in evidence):
            result |= p.CAP_CHAINING
        if self.probe or "advance" in evidence:
            result |= p.CAP_ADVANCE
        return result

    def _require(self, group, capability=0):
        if not self.ready(group) or self.capabilities(group) & capability != capability:
            raise BackendFailure(p.NOT_READY)

    def _attempt(self, group, lease):
        attempt = self._attempts.get(lease)
        if attempt is None or attempt.lease.group != group or attempt.state == "released":
            raise BackendFailure(p.STALE)
        self._identity(attempt, allow_ancestor=attempt.state == "reserved")
        return attempt

    def _identity(self, attempt, empty=False, allow_ancestor=False):
        lease = attempt.lease
        if (self._layers.get(lease.group, ())[lease.layer] != attempt.track
                or attempt.track.clip_slots[lease.row] != attempt.slot
                or self._slots.get((lease.group, lease.row, lease.layer)) != attempt.slot):
            raise BackendFailure(p.STALE)
        if attempt.clip is not None:
            if not attempt.slot.has_clip or attempt.slot.clip != attempt.clip:
                raise BackendFailure(p.STALE)
        elif attempt.slot.has_clip:
            ancestors = [self._attempts[item] for item in attempt.ancestors if item in self._attempts]
            if not (allow_ancestor and any(old.clip == attempt.slot.clip and old.cancel_requested for old in ancestors)):
                if not attempt.start_issued:
                    raise BackendFailure(p.STALE)
                self._recording_evidence(attempt, attempt.slot.clip)
        if empty and attempt.slot.has_clip:
            raise BackendFailure(p.CAPACITY)

    @staticmethod
    def _anchor_matches(clip, target):
        value = clip.start_time
        return (type(value) in (int, float) and math.isfinite(value)
                and target is not None and abs(value - target) <= _EPSILON)

    def _recording_evidence(self, attempt, clip):
        if (not clip.is_audio_clip or not clip.is_recording or not clip.is_playing
                or attempt.track.playing_slot_index != attempt.lease.row
                or not self._anchor_matches(clip, attempt.start_target)):
            raise BackendFailure(p.STALE)

    def _owns_native_transition(self):
        for attempt in self._attempts.values():
            if attempt.state in ("reserved", "released", "accepted") or not attempt.start_issued:
                continue
            try:
                if (attempt.track.clip_slots[attempt.lease.row] != attempt.slot
                        or self._layers[attempt.lease.group][attempt.lease.layer] != attempt.track):
                    continue
                if attempt.slot.has_clip:
                    clip = attempt.slot.clip
                    if attempt.clip is None:
                        self._recording_evidence(attempt, clip)
                    elif clip != attempt.clip or not clip.is_audio_clip:
                        continue
                    if clip.is_recording or attempt.finish_issued or attempt.cancel_requested:
                        return True
                elif attempt.cancel_requested and attempt.stop_issued:
                    return True
            except Exception:
                continue
        return False

    def _refresh_idle_baseline(self):
        if not self._owns_native_transition():
            self._idle_overdub = bool(self.song.overdub)

    def reserve(self, group, row, exclude=()):
        self._require(group, p.CAP_RECORDING)
        excluded_tracks = {self._attempts[lease].track for lease in exclude if lease in self._attempts}
        for index, track in enumerate(self._layers[group]):
            if track in excluded_tracks or (group, row, index) not in self._slots:
                continue
            slot = self._slots[group, row, index]
            if track.clip_slots[row] != slot:
                raise BackendFailure(p.STALE)
            prior = [a for a in self._attempts.values() if a.track == track and a.state != "released"]
            cancellation = [a for a in prior if a.cancel_requested]
            if any(a.lease.row == row and not a.cancel_requested for a in prior):
                continue
            if any(a.state != "accepted" and not a.cancel_requested for a in prior):
                continue
            if slot.has_clip and not any(a.slot == slot and a.clip == slot.clip for a in cancellation):
                continue
            if not cancellation and (track.playing_slot_index not in (-1, -2)
                                     or track.fired_slot_index != -1 or slot.is_triggered):
                continue
            self._generation += 1
            lease = Lease(group, row, index, self._generation)
            ancestors = tuple(a.lease for a in cancellation)
            self._attempts[lease] = _Attempt(lease, track, slot, ancestors)
            return lease
        return None

    def release(self, group, lease):
        attempt = self._attempt(group, lease)
        if attempt.state != "reserved":
            raise BackendFailure(p.BUSY)
        attempt.state = "released"

    def _reference(self, reference, group=None):
        if reference is None:
            return None
        attempt = self._attempts.get(reference.slot)
        if (attempt is None or attempt.state != "accepted"
                or group is not None and attempt.lease.group != group):
            raise BackendFailure(p.NOT_READY)
        self._identity(attempt)
        clip = attempt.clip
        length = clip.loop_end - clip.loop_start
        if (not clip.is_playing or not clip.is_audio_clip or not clip.looping or not clip.warping
                or clip.is_recording or clip.loop_start != 0
                or type(length) not in (int, float) or not math.isfinite(length)
                or abs(length - reference.length) > _EPSILON
                or not self._anchor_matches(clip, attempt.finish_target)):
            raise BackendFailure(p.NOT_READY)
        return clip

    def queue_start(self, group, lease, token, mode, reference):
        admission = self._admission()
        capability = p.CAP_RECORDING | (p.CAP_REFERENCE_WRAP if mode == "reference_wrap" else 0)
        self._require(group, capability)
        attempt = self._attempts.get(lease)
        if attempt is None or attempt.lease.group != group or attempt.state != "reserved":
            raise BackendFailure(p.STALE)
        self._identity(attempt, allow_ancestor=True)
        if mode not in ("next_bar", "reference_wrap"):
            raise BackendFailure(p.BAD_COMMAND)
        now = self._beat()
        schedule = self._safe_start_schedule(attempt, mode, reference, now, admission, initial=True)
        attempt.start_queued_at, attempt.start_mode, attempt.start_reference = now, mode, reference
        attempt.token, attempt.start_target, attempt.state = token, schedule.start_at, "queued"
        return schedule

    def _start_guard(self, attempt, reference, initial=False):
        self._validate(transport=True)
        waiting = any(self._attempts[lease].state != "released" for lease in attempt.ancestors)
        self._identity(attempt, empty=not waiting, allow_ancestor=waiting)
        if (attempt.start_issued or attempt.cancel_requested or attempt.start_cleanup_pending
                or attempt.state != ("reserved" if initial else "queued")):
            raise BackendFailure(p.STALE)
        if not waiting and (attempt.track.arm or not self._quiet(attempt)):
            raise BackendFailure(p.STALE)
        if reference is not None:
            self._reference(reference, attempt.lease.group)
        if self.session is not None:
            if self.session.groups[attempt.lease.group].reference != reference:
                raise BackendFailure(p.STALE)
            if not initial:
                current = self.session.take
                candidate = (current if current is not None and current.token == attempt.token
                             else current.next_take if current is not None else None)
                if (candidate is None or candidate.token != attempt.token or candidate.slot != attempt.lease
                        or candidate.state != "queued"
                        or candidate.schedule != Schedule(attempt.start_queued_at, attempt.start_target)):
                    raise BackendFailure(p.STALE)

    def _safe_start_schedule(self, attempt, mode, reference, queued_at, admission, after=None, initial=False):
        for _ in range(3):
            try:
                position = admission.position(_TRANSACTION_LEAD)
            except Exception as error:
                raise BackendFailure(p.NOT_READY) from error
            at = max(position, queued_at, after) if after is not None else max(position, queued_at)
            target = (self._clock_call(self.fixture.next_wrap,
                      self._reference(reference, attempt.lease.group), at=at)
                      if mode == "reference_wrap" else self._clock_call(self.fixture.next_bar, at=at))
            self._target(target, at)
            if getattr(self.fixture, "native_bar_clock", False):
                self._clock_call(self.fixture.launch_window, target)
            self._start_guard(attempt, reference, initial=initial)
            try:
                self._require_lead(admission, target, _TRANSACTION_LEAD)
            except _LaunchDeadline:
                continue
            return Schedule(queued_at, target)
        raise BackendFailure(p.NOT_READY)

    def _defer_start(self, attempt, admission):
        if attempt.start_issued:
            raise BackendFailure(p.STALE)
        reference = attempt.start_reference
        if attempt.start_mode == "reference_wrap" and reference is None:
            parent = self._attempts.get(attempt.finish_parent)
            if parent is None or not parent.finish_issued:
                raise BackendFailure(p.STALE)
            # A first take's prospective length is not a reference. Wait for
            # actual validated acceptance before choosing its child's wrap.
            if parent.state != "accepted":
                self._validate(transport=True)
                self._identity(attempt, empty=True)
                return False
            if self.session is None:
                raise BackendFailure(p.NOT_READY)
            reference = self.session.groups[attempt.lease.group].reference
            if reference is None or reference.slot != parent.lease:
                raise BackendFailure(p.STALE)
        schedule = self._safe_start_schedule(attempt, attempt.start_mode, reference,
            attempt.start_queued_at, admission, after=attempt.start_target)
        if self.session is not None and not self.session.observe_start_deferred(attempt.token, schedule):
            raise BackendFailure(p.STALE)
        previous = attempt.start_target
        attempt.start_target, attempt.start_reference = schedule.start_at, reference
        self._event("start_deferred", attempt, previous_start=previous)
        return True

    def queue_finish(self, group, lease, token, next_slot, next_token, reference):
        admission = self._admission()
        required = p.CAP_RECORDING
        if next_slot is not None:
            required |= p.CAP_CHAINING | (p.CAP_REFERENCE_WRAP if reference is not None else 0)
        self._require(group, required)
        attempt = self._attempt(group, lease)
        if (attempt.token != token or attempt.state != "recording" or attempt.clip is None
                or not attempt.clip.is_recording):
            raise BackendFailure(p.NOT_READY)
        following = None
        if next_slot is not None:
            following = self._attempt(group, next_slot)
            if following.state != "reserved" or following.track == attempt.track:
                raise BackendFailure(p.CAPACITY)
            self._identity(following, empty=True)
        now = self._beat()
        plan = self._safe_finish_plan(group, now, reference, following is not None, admission,
            guard=lambda: self._finish_guard(attempt, following, reference, initial=True))
        finish, next_start = plan.finish.start_at, plan.next_start
        # All checks precede intent updates, and there are no native writes.
        attempt.finish_queued_at, attempt.finish_reference = now, reference
        attempt.finish_target, attempt.state = finish, "finish_planned"
        if following is not None:
            attempt.next_lease = following.lease
            following.token, following.start_target = next_token, next_start.start_at
            following.start_queued_at, following.start_mode = finish, "reference_wrap"
            following.start_reference = reference
            following.finish_parent, following.state = attempt.lease, "queued"
        return plan

    def _safe_finish_plan(self, group, queued_at, reference, has_next, admission, after=None, guard=None):
        """Choose a current safe bar and aligned child, with no native effects."""
        for _ in range(3):
            try:
                position = admission.position(_TRANSACTION_LEAD)
            except Exception as error:
                raise BackendFailure(p.NOT_READY) from error
            at = max(position, queued_at, after) if after is not None else max(position, queued_at)
            finish = self._clock_call(self.fixture.next_bar, at=at)
            self._target(finish, at)
            self._target(finish, queued_at)
            if getattr(self.fixture, "native_bar_clock", False):
                self._clock_call(self.fixture.launch_window, finish)
            next_start = None
            if has_next:
                target = (self._clock_call(self.fixture.next_wrap, self._reference(reference, group),
                                          at=finish, inclusive=True)
                          if reference is not None else finish)
                self._target(target, finish, inclusive=True)
                if getattr(self.fixture, "native_bar_clock", False):
                    self._clock_call(self.fixture.launch_window, target)
                next_start = Schedule(finish, target)
            if guard is not None:
                guard()
            try:
                self._require_lead(admission, finish, _TRANSACTION_LEAD)
            except _LaunchDeadline:
                continue
            return FinishPlan(Schedule(queued_at, finish), next_start)
        raise BackendFailure(p.NOT_READY)

    def _finish_guard(self, attempt, following, reference, initial=False):
        self._validate(transport=True)
        self._identity(attempt)
        if (attempt.finish_issued or attempt.cancel_requested or attempt.clip is None
                or not attempt.clip.is_recording
                or attempt.state != ("recording" if initial else "finish_planned")):
            raise BackendFailure(p.STALE)
        if following is not None:
            self._identity(following, empty=True)
            if (following.start_issued or following.cancel_requested or following.track.arm
                    or not self._quiet(following)
                    or following.state != ("reserved" if initial else "queued")
                    or not initial and following.finish_parent != attempt.lease):
                raise BackendFailure(p.STALE)
        if reference is not None:
            self._reference(reference, attempt.lease.group)
        if self.session is not None:
            current = self.session.take
            if (self.session.groups[attempt.lease.group].reference != reference
                    or current is None or current.token != attempt.token or current.slot != attempt.lease
                    or current.state != ("recording" if initial else "finish_queued")):
                raise BackendFailure(p.STALE)
            if not initial:
                child = current.next_take
                if (current.schedule != Schedule(attempt.finish_queued_at, attempt.finish_target)
                        or (child is None) != (following is None)
                        or child is not None and (child.token != following.token or child.slot != following.lease
                            or child.state != "queued"
                            or child.schedule != Schedule(attempt.finish_target, following.start_target))):
                    raise BackendFailure(p.STALE)

    def _defer_finish(self, attempt, admission):
        """Move both unissued targets and the Session projection before firing."""
        following = self._attempts.get(attempt.next_lease)
        if attempt.next_lease is not None and following is None:
            raise BackendFailure(p.STALE)
        guard = lambda: self._finish_guard(attempt, following, attempt.finish_reference)
        guard()
        old_finish = attempt.finish_target
        plan = self._safe_finish_plan(attempt.lease.group, attempt.finish_queued_at,
            attempt.finish_reference, following is not None, admission, after=old_finish, guard=guard)
        # Guarded reads and the final deadline check precede both pure model
        # updates; no native calls occur between Session and lease writes.
        if self.session is not None and not self.session.observe_finish_deferred(attempt.token, plan):
            raise BackendFailure(p.STALE)
        attempt.finish_target = plan.finish.start_at
        if following is not None:
            following.start_target = plan.next_start.start_at
            following.start_queued_at = plan.next_start.queued_at
        self._event("finish_deferred", attempt, previous_finish=old_finish,
                    finish_target=attempt.finish_target,
                    next_start=following.start_target if following else None)

    def cancel_attempts(self, group, attempts):
        if not self.enabled or group not in self._layers or group in self.faults:
            raise BackendFailure(p.NOT_READY)
        self._validate()
        selected = []
        for lease, token in attempts:
            attempt = self._attempts.get(lease)
            if (attempt is None or attempt.lease.group != group or attempt.state in ("accepted", "released")
                    or attempt.token != token):
                raise BackendFailure(p.STALE)
            self._identity(attempt, allow_ancestor=True)
            self._cancel_scope(attempt)
            clip = attempt.clip
            if clip is None and attempt.start_issued and attempt.slot.has_clip:
                clip = attempt.slot.clip
            if (attempt.finish_target is not None and clip is not None
                    and not clip.is_recording and clip.is_playing):
                raise BackendFailure(p.BUSY)
            selected.append((attempt, clip))
        for attempt, clip in selected:
            attempt.clip = clip
            attempt.cancel_requested = True
            attempt.cancel_finish_intent = attempt.finish_target is not None
            attempt.finish_target = None
            attempt.next_lease = None
            attempt.state = "canceling" if attempt.start_issued or attempt.start_cleanup_pending else "released"

    def pending_cancel(self, group):
        return any(a.lease.group == group and a.cancel_requested and a.state != "released"
                   for a in self._attempts.values())

    def row_count(self, group):
        return min(len(track.clip_slots) for track in self._layers[group])

    def row_empty(self, group, row):
        self._validate()
        return all(not track.clip_slots[row].has_clip and not track.clip_slots[row].is_triggered
                   for track in self._layers[group])

    def advance(self, group, row):
        self._require(group, p.CAP_ADVANCE)
        if self.pending_cancel(group) or any(a.lease.group == group and a.state not in
                ("reserved", "released", "accepted") for a in self._attempts.values()):
            raise BackendFailure(p.BUSY)
        if not self.row_empty(group, row):
            raise BackendFailure(p.CAPACITY)
        self._advances[group] = {"row": row, "issued": set()}

    def pending_advance(self, group):
        return group in self._advances

    def clip_for(self, lease):
        attempt = self._attempts.get(lease)
        if attempt is None or attempt.state == "released":
            return None
        try:
            self._identity(attempt)
        except Exception:
            return None
        return attempt.clip

    def reference_clock(self, group, take_id=0):
        if self.session is None or group not in self._layers:
            return None
        reference = self.session.groups[group].reference
        if reference is None:
            return None
        return self.fixture.reference_clock(self._reference(reference, group), take_id=take_id)

    def _beat(self):
        value = self.song.current_song_time
        if type(value) not in (int, float) or not math.isfinite(value):
            raise BackendFailure(p.NOT_READY)
        return float(value)

    def _admission(self):
        try:
            return LaunchAdmission(self.song, clock=time.monotonic)
        except Exception as error:
            raise BackendFailure(p.NOT_READY) from error

    @staticmethod
    def _require_lead(admission, target, seconds):
        try:
            admission.require(target, seconds)
        except Exception as error:
            raise _LaunchDeadline() from error

    @staticmethod
    def _target(value, after, inclusive=False):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or value < after or (value == after and not inclusive)):
            raise BackendFailure(p.NOT_READY)

    def _due(self, target):
        now = self._beat()
        if now >= target:
            raise _LaunchDeadline("scheduled boundary was missed")
        if getattr(self.fixture, "native_bar_clock", False):
            return now > self.fixture.launch_window(target).previous
        if now < target - self.fixture.bar_length:
            return False
        if abs(self.fixture.next_bar(at=now) - target) > _EPSILON:
            raise _LaunchDeadline("scheduled target is no longer the next native bar")
        return True

    def _quantization(self, target):
        if getattr(self.fixture, "native_bar_clock", False):
            return self.fixture.launch_window(target).quantization
        return self.fixture.q_bar

    def _event(self, kind, attempt=None, group=None, **details):
        record = {"event": kind, "group": attempt.lease.group if attempt else group}
        try:
            record["beat"] = self._beat()
        except Exception:
            record["beat"] = None
        if attempt is not None:
            finish_event = kind in ("before_finish", "finish", "finish_deferred", "before_disarm_finish", "disarm_finish", "finish_observed")
            record.update(token=attempt.token,
                          target=attempt.finish_target if finish_event else attempt.start_target,
                          start_target=attempt.start_target, finish_target=attempt.finish_target)
            try:
                record["native"] = {"fired": attempt.track.fired_slot_index,
                    "playing": attempt.track.playing_slot_index, "armed": bool(attempt.track.arm),
                    "has_clip": bool(attempt.slot.has_clip), "triggered": bool(attempt.slot.is_triggered)}
                if attempt.slot.has_clip:
                    record["native"].update(recording=bool(attempt.slot.clip.is_recording),
                                             clip_playing=bool(attempt.slot.clip.is_playing))
                    for name in ("start_time", "loop_start", "loop_end"):
                        value = getattr(attempt.slot.clip, name)
                        record["native"][name] = value if type(value) in (int, float) and math.isfinite(value) else None
            except Exception as error:
                record["native"] = {"unavailable": type(error).__name__}
        record.update(details)
        try:
            self.emit(record)
        except Exception:
            pass

    def _fault(self, group, operation, error, attempt=None):
        if group in self.faults:
            return
        details = {"operation": operation, "error": type(error).__name__, "message": str(error)[:500]}
        if error.__cause__ is not None:
            details.update(cause=type(error.__cause__).__name__, cause_message=str(error.__cause__)[:500])
        self.faults[group] = details
        self._event("fault", attempt, group, **details)

    def _write(self, kind, attempt, call, empty=False, deleted=False):
        self._validate(transport=kind in ("start", "finish"))
        if not deleted:
            self._identity(attempt, empty=empty)
        elif (attempt.track.clip_slots[attempt.lease.row] != attempt.slot or attempt.slot.has_clip):
            raise BackendFailure(p.STALE)
        self._event("before_" + kind, attempt)
        result = call()
        self._event(kind, attempt)
        return result

    def _observe(self, attempt):
        if not attempt.start_issued or attempt.state in ("accepted", "released"):
            return
        self._identity(attempt)
        if attempt.slot.has_clip and attempt.clip is None:
            attempt.clip = attempt.slot.clip
        clip = attempt.clip
        if clip is None or attempt.cancel_requested:
            return
        if clip.is_recording and attempt.state == "queued":
            self._recording_evidence(attempt, clip)
            attempt.state = "recording"
            if self.session is not None:
                self.session.observe_started(attempt.token)
            self._event("recording_observed", attempt)
        if attempt.finish_issued and not clip.is_recording and clip.is_playing:
            length = clip.loop_end - clip.loop_start
            if (not clip.is_audio_clip or not clip.looping or not clip.warping or clip.loop_start != 0
                    or type(length) not in (int, float) or not math.isfinite(length) or length <= 0):
                raise RuntimeError("finished take must be looping warped audio with zero loop start and positive finite length")
            if not self._anchor_matches(clip, attempt.finish_target):
                raise RuntimeError("native playback anchor does not match requested finish boundary")
            expected_length = attempt.finish_target - attempt.start_target
            coverage = attempt.recording_coverage
            if coverage is not None and self._sample_coverage(clip, expected_length) != coverage:
                raise RuntimeError("recorded sample changed after loop normalization")
            if abs(length - expected_length) > _EPSILON:
                if ("exact_length" not in self.validated_features or expected_length <= 0
                        or attempt.length_write_issued):
                    raise RuntimeError("native loop length does not match the observed recording duration")
                # Live rounds off-bar loop bounds independently of the file.
                # Correct either direction only when the actual sample covers
                # the recording duration under its own native warp mapping.
                coverage = self._sample_coverage(clip, expected_length)
                def normalize_length():
                    self._validate()
                    self._identity(attempt)
                    if (not clip.is_audio_clip or not clip.warping or not clip.looping
                            or clip.is_recording or not clip.is_playing or clip.loop_start != 0
                            or clip.loop_end != length
                            or not self._anchor_matches(clip, attempt.finish_target)):
                        raise RuntimeError("finished clip changed before loop normalization")
                    if self._sample_coverage(clip, expected_length) != coverage:
                        raise RuntimeError("recorded sample changed before loop normalization")
                    attempt.length_write_issued = True
                    attempt.recording_coverage = coverage
                    clip.loop_end = expected_length
                self._write("normalize_finish_length", attempt, normalize_length)
                length = clip.loop_end - clip.loop_start
                if (type(length) not in (int, float) or not math.isfinite(length)
                        or clip.loop_start != 0 or length <= 0):
                    raise RuntimeError("normalized loop bounds are invalid")
                if abs(length - expected_length) > _EPSILON:
                    return  # A deferred setter must be observed on the next callback.
                if not self._anchor_matches(clip, attempt.finish_target):
                    raise RuntimeError("loop normalization moved the native playback anchor")
            def guard_finished_clip():
                self._identity(attempt)
                current_length = clip.loop_end - clip.loop_start
                if (not clip.is_audio_clip or not clip.warping or not clip.looping
                        or clip.is_recording or not clip.is_playing or clip.loop_start != 0
                        or type(current_length) not in (int, float) or not math.isfinite(current_length)
                        or abs(current_length - expected_length) > _EPSILON
                        or not self._anchor_matches(clip, attempt.finish_target)):
                    raise RuntimeError("finished clip changed before acceptance")
                if coverage is not None and self._sample_coverage(clip, expected_length) != coverage:
                    raise RuntimeError("recorded sample changed before acceptance")
            def disarm():
                self._validate()
                guard_finished_clip()
                attempt.track.arm = False
            self._write("disarm_finish", attempt, disarm)
            guard_finished_clip()
            attempt.state = "accepted"
            self._refresh_idle_baseline()
            if self.session is not None:
                self.session.observe_finished(attempt.token, length)
            self._event("finish_observed", attempt, length=length)

    @staticmethod
    def _sample_coverage(clip, target):
        """Read native sample metadata, never unfinished on-disk headers."""
        try:
            frames, rate, path = clip.sample_length, clip.sample_rate, clip.file_path
            markers = tuple((marker['sample_time'], marker['beat_time'])
                if isinstance(marker, dict) else (marker.sample_time, marker.beat_time)
                for marker in clip.warp_markers)
            if type(path) is not str or not path or not sample_covers(target, frames, rate, markers):
                raise ValueError("actual sample does not cover the recorded duration")
            return frames, rate, path, markers
        except (RuntimeError, AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
            raise RuntimeError("recorded sample coverage is unavailable or insufficient") from error

    @staticmethod
    def _quiet(attempt):
        # Native proof distinguishes playing=-2 stopped from fired=-2 pending.
        return (attempt.track.fired_slot_index == -1 and attempt.track.playing_slot_index in (-1, -2)
                and not attempt.slot.is_triggered and
                (not attempt.slot.has_clip if attempt.clip is None else
                 attempt.slot.has_clip and attempt.slot.clip == attempt.clip
                 and not attempt.clip.is_recording and not attempt.clip.is_playing))

    @staticmethod
    def _cancel_scope(attempt):
        # Track.stop_all_clips affects the whole physical track. Refuse if an
        # external launch moved it to another row since this take was queued.
        allowed = (-1, -2, attempt.lease.row)
        if attempt.track.fired_slot_index not in allowed or attempt.track.playing_slot_index not in allowed:
            raise BackendFailure(p.STALE)

    def _cancel(self, attempt):
        if attempt.start_cleanup_pending and not attempt.start_issued:
            if not self._disarm_unstarted(attempt):
                return
            attempt.clip, attempt.state = None, "released"
            self._refresh_idle_baseline()
            self._event("cancel_observed", attempt)
            return
        self._identity(attempt)
        self._cancel_scope(attempt)
        if attempt.slot.has_clip and attempt.clip is None:
            attempt.clip = attempt.slot.clip
        if (attempt.cancel_finish_intent and attempt.clip is not None
                and not attempt.clip.is_recording and attempt.clip.is_playing):
            raise RuntimeError("requested finish completed before cancellation; preserving clip")
        if not attempt.stop_issued:
            def stop():
                attempt.stop_issued = True
                return attempt.track.stop_all_clips(False)
            self._write("stop", attempt, stop)
        if not self._quiet(attempt):
            return
        if attempt.slot.has_clip:
            if attempt.delete_issued:
                raise RuntimeError("attempt deletion did not complete")
            def delete():
                attempt.delete_issued = True
                return attempt.slot.delete_clip()
            self._write("delete", attempt, delete)
            if attempt.slot.has_clip:
                raise RuntimeError("attempt deletion left a clip in its slot")
        self._write("disarm_cancel", attempt, lambda: setattr(attempt.track, "arm", False), deleted=True)
        attempt.clip, attempt.state = None, "released"
        self._refresh_idle_baseline()
        self._event("cancel_observed", attempt)

    def _execute_finish(self, attempt, admission=None):
        admission = self._admission() if admission is None else admission
        for _ in range(2):
            try:
                if not self._due(attempt.finish_target):
                    return
                self._require_lead(admission, attempt.finish_target, _TRANSACTION_LEAD)
                if attempt.next_lease is not None:
                    following = self._attempts[attempt.next_lease]
                    self._identity(following, empty=True)
                    if following.cancel_requested or following.state != "queued" or following.start_issued:
                        raise RuntimeError("following reservation is no longer valid")
                if attempt.clip is None or not attempt.clip.is_recording:
                    raise RuntimeError("requested finish lost its recording clip")
                def finish():
                    if not self._due(attempt.finish_target):
                        raise _LaunchDeadline()
                    quantum = self._quantization(attempt.finish_target)
                    self._require_lead(admission, attempt.finish_target, _FIRE_LEAD)
                    attempt.finish_issued = True
                    return attempt.slot.fire(launch_quantization=quantum)
                self._write("finish", attempt, finish)
                return
            except _LaunchDeadline:
                # The flag is set before the native call. An exception from
                # that call is ambiguous and must never cause another fire.
                if attempt.finish_issued:
                    raise
                self._defer_finish(attempt, admission)

    def _execute_start(self, attempt, admission=None):
        admission = self._admission() if admission is None else admission
        if attempt.start_cleanup_pending:
            if self._disarm_unstarted(attempt):
                self._defer_start(attempt, admission)
            return
        if any(self._attempts[lease].state != "released" for lease in attempt.ancestors):
            try:
                self._require_lead(admission, attempt.start_target, _TRANSACTION_LEAD)
            except _LaunchDeadline:
                self._defer_start(attempt, admission)
            return
        if attempt.finish_parent is not None and not self._attempts[attempt.finish_parent].finish_issued:
            return
        armed_here = False
        def arm():
            nonlocal armed_here
            self._require_lead(admission, attempt.start_target, _FIRE_LEAD)
            attempt.track.arm = True
            armed_here = True
        try:
            if not self._due(attempt.start_target):
                return
            parent = self._attempts.get(attempt.finish_parent)
            coincident = (parent is not None and parent.finish_issued
                          and parent.finish_target == attempt.start_target)
            self._require_lead(admission, attempt.start_target,
                               _FIRE_LEAD if coincident else _TRANSACTION_LEAD)
            self._identity(attempt, empty=True)
            if attempt.track.arm or not self._quiet(attempt):
                raise RuntimeError("start target track is not quiet and unarmed")
            self._write("arm_start", attempt, arm, empty=True)
            # Arm may notify other scripts. Revalidate before the actual fire.
            self._validate(transport=True)
            self._identity(attempt, empty=True)
            if not self._due(attempt.start_target):
                raise _LaunchDeadline()
            def start():
                if not self._due(attempt.start_target):
                    raise _LaunchDeadline()
                quantum = self._quantization(attempt.start_target)
                self._require_lead(admission, attempt.start_target, _FIRE_LEAD)
                attempt.start_issued = True
                return attempt.slot.fire(launch_quantization=quantum)
            self._write("start", attempt, start, empty=True)
        except _LaunchDeadline:
            if attempt.start_issued:
                raise
            if armed_here:
                attempt.start_cleanup_pending = True
                if not self._disarm_unstarted(attempt):
                    return
            self._defer_start(attempt, admission)

    def _disarm_unstarted(self, attempt):
        if attempt.start_issued or not attempt.start_cleanup_pending:
            raise BackendFailure(p.STALE)
        self._validate()
        self._identity(attempt, empty=True)
        if not self._quiet(attempt):
            raise BackendFailure(p.STALE)
        def disarm():
            self._identity(attempt, empty=True)
            if attempt.start_issued or not self._quiet(attempt):
                raise RuntimeError("refused start acquired a native effect before cleanup")
            attempt.start_disarm_issued = True
            attempt.track.arm = False
        if attempt.track.arm and not attempt.start_disarm_issued:
            self._write("disarm_refused_start", attempt, disarm, empty=True)
        if attempt.track.arm:
            return False
        attempt.start_cleanup_pending, attempt.start_disarm_issued = False, False
        return True

    def _advance(self, group, plan):
        for index, track in enumerate(self._layers[group]):
            if index in plan["issued"]:
                continue
            self._validate()
            if not self.row_empty(group, plan["row"]):
                raise RuntimeError("advance target row became occupied")
            plan["issued"].add(index)
            track.stop_all_clips(False)
            self._event("advance_stop", group=group, layer=index,
                        fired=track.fired_slot_index, playing=track.playing_slot_index)
        if all(track.fired_slot_index == -1 and track.playing_slot_index in (-1, -2)
               and all(not slot.is_triggered and (not slot.has_clip or
                   not slot.clip.is_recording and not slot.clip.is_playing) for slot in track.clip_slots)
               for track in self._layers[group]):
            del self._advances[group]
            self._event("advance_observed", group=group, row=plan["row"])

    def tick(self):
        if not self.enabled:
            return
        # Observation remains independent of quarantine for issued finishes.
        for attempt in tuple(self._attempts.values()):
            if attempt.lease.group in self.faults and not attempt.finish_issued:
                continue
            try:
                self._observe(attempt)
            except Exception as error:
                self._fault(attempt.lease.group, "observe", error, attempt)
        for attempt in tuple(self._attempts.values()):
            group = attempt.lease.group
            if group in self.faults or attempt.state != "canceling":
                continue
            try:
                self._cancel(attempt)
            except Exception as error:
                self._fault(group, "cancel", error, attempt)
        for group, plan in tuple(self._advances.items()):
            if group in self.faults:
                continue
            try:
                self._advance(group, plan)
            except Exception as error:
                self._fault(group, "advance", error)
        # Every finish executes before any coincident following start.
        admission = None
        for kind in ("finish", "start"):
            for attempt in tuple(self._attempts.values()):
                group = attempt.lease.group
                if group in self.faults or attempt.cancel_requested or attempt.state in ("accepted", "released", "reserved"):
                    continue
                try:
                    if kind == "finish" and attempt.finish_target is not None and not attempt.finish_issued:
                        admission = self._admission() if admission is None else admission
                        self._execute_finish(attempt, admission)
                    elif kind == "start" and not attempt.start_issued:
                        admission = self._admission() if admission is None else admission
                        self._execute_start(attempt, admission)
                except Exception as error:
                    self._fault(group, kind, error, attempt)

    def diagnostic_state(self):
        return {"enabled": self.enabled, "probe": self.probe,
            "validated_features": sorted(self.validated_features), "faults": dict(self.faults),
            "pending_cancel": [g for g in self._layers if self.pending_cancel(g)],
            "pending_advance": list(self._advances),
            "leases": [{"group": a.lease.group, "row": a.lease.row, "layer": a.lease.layer,
                        "generation": a.lease.generation, "token": a.token, "state": a.state,
                        "start_target": a.start_target, "finish_target": a.finish_target,
                        "stop_issued": a.stop_issued, "delete_issued": a.delete_issued}
                       for a in self._attempts.values()]}
