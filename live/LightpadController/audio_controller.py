"""Join native recording, actual group controls and protocol snapshots.

The MIDI adapter owns framing. This module owns command completion: Cancel
and Advance are acknowledged only after the native executor observes cleanup.
It never derives recording transitions from a clock or a scheduled target.
"""

from functools import wraps

from . import protocol as p
from .audio_backend import LiveAudioBackend
from .audio_fixture import FixtureError
from .configuration import GROUP_NAMES
from .groups import LiveGroups
from .metronome import MetronomeControls
from .session import Session, BackendFailure, QUEUED_VIEW


def _uncached_native(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        depth = self._read_depth
        self._read_depth = 0
        self.invalidate_read_batch()
        try:
            return method(self, *args, **kwargs)
        finally:
            self._read_depth = depth
            self.invalidate_read_batch()
    return call


def _consistent_read(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        for unused in range(2):
            generation = self._read_generation
            value = method(self, *args, **kwargs)
            if generation == self._read_generation:
                return value
        return None
    return call


class AudioController:
    def __init__(self, fixture, assignments=None, validated_features=(), probe=False,
                 emit=None, auto_enable=False):
        self._read_depth = 0
        self._read_generation = 0
        self._read_validation = {}
        self.fixture = fixture
        self.backend = LiveAudioBackend(fixture, emit=emit, probe=probe,
                                        validated_features=validated_features)
        self.session = Session(self.backend)
        self.backend.bind_session(self.session)
        names = list(GROUP_NAMES)
        for index, binding in fixture.groups.items():
            names[index] = binding.group.name
        self.groups = LiveGroups(fixture.song, names=tuple(names), assignments=assignments)
        self.controls = MetronomeControls(fixture.song, self._control_scope_valid)
        self._pending = {}
        if auto_enable:
            self.enable()

    def enable(self):
        return self.backend.enable()

    def begin_read_batch(self):
        self._read_depth += 1
        self.invalidate_read_batch()

    def end_read_batch(self):
        self._read_depth = max(0, self._read_depth - 1)
        self.invalidate_read_batch()

    def invalidate_read_batch(self):
        self._read_generation += 1
        self._read_validation.clear()

    def _validated_read(self, key, validate):
        if self._read_depth and key in self._read_validation:
            return self._read_validation[key]
        generation = self._read_generation
        valid = bool(validate())
        if self._read_depth and generation == self._read_generation:
            self._read_validation[key] = valid
        return valid

    def _control_scope_valid(self):
        if not self.backend.enabled or 0 not in self.fixture.groups:
            return False
        if self._read_depth and self._read_validation.get('capture') is True:
            return True  # A successful full scope also checked the bindings.
        def validate():
            try:
                return self.fixture.validate_control_scope()
            except (FixtureError, RuntimeError, ValueError, TypeError, AttributeError):
                return False
        return self._validated_read('control', validate)

    @_consistent_read
    def meter_state(self):
        if not self._control_scope_valid():
            return None
        meter, metronome = self.controls.meter(), self.controls.metronome()
        return meter + (metronome,) if meter is not None and metronome is not None else None

    def _scope_valid(self):
        if not self.backend.enabled:
            return False
        def validate():
            try:
                self.backend.validate_scope()
                return True
            except (BackendFailure, RuntimeError, ValueError, AttributeError):
                return False
        return self._validated_read('capture', validate)

    @_uncached_native
    def dispatch(self, command):
        """Return an outcome, or None while native Cancel/Advance is pending."""
        group, op = command.group, command.op
        if op == p.REFRESH:
            return p.APPLIED
        if op == p.ACK_COMPLETION:
            return self.session.acknowledge_completion(group, command.arg)
        if op in (p.TAP_TEMPO, p.SET_NUMERATOR, p.SET_DENOMINATOR, p.SET_METRONOME):
            return self.controls.dispatch(command)
        if group not in self.fixture.groups:
            return p.NOT_READY
        if op not in range(p.BEGIN, p.ADVANCE + 1):
            if group in self.backend.faults or not self._scope_valid():
                return p.NOT_READY
            return self.groups.dispatch(command)
        if self._pending:
            return p.BUSY
        token = self.session.take_id_for(group)
        if op == p.BEGIN:
            outcome = self.session.begin(group)
        elif op == p.ACCEPT:
            outcome = self.session.accept(group)
        elif op == p.ACCEPT_NEXT:
            outcome = self.session.accept(group, next_take=True)
        elif op == p.RETRY:
            outcome = self.session.retry(group)
        elif op == p.ABORT:
            outcome = self.session.abort(group)
        else:
            outcome = self.session.advance(group)
        # Model intent is committed before any native effect executes.
        self.backend.tick()
        if group in self.backend.faults:
            return p.NOT_READY
        waiting = (op == p.ABORT and self.backend.pending_cancel(group)
                   or op == p.ADVANCE and self.backend.pending_advance(group))
        if outcome == p.APPLIED and waiting:
            self._pending[group] = (command, token)
            return None
        return outcome

    @_uncached_native
    def tick(self):
        self.backend.tick()
        completed = []
        for group, (command, token) in tuple(self._pending.items()):
            failed = group in self.backend.faults
            waiting = (self.backend.pending_cancel(group) if command.op == p.ABORT
                       else self.backend.pending_advance(group))
            if failed or not waiting:
                completed.append((command, p.NOT_READY if failed else p.APPLIED))
                del self._pending[group]
        return completed

    def needs_tick(self):
        return bool(self._pending) or self.backend.needs_tick()

    def snapshot(self, group, sequence=0, outcome=p.NONE):
        # A reentrant callback invalidates the outer projection. Resample once
        # rather than caching a mixture of values from two callback epochs.
        for unused in range(2):
            generation = self._read_generation
            fields = self._snapshot(group, sequence, outcome)
            if generation == self._read_generation:
                return fields
        fields[91], fields[103], fields[106], fields[107] = p.UNAVAILABLE, 0, 0, 0
        return fields

    def _snapshot(self, group, sequence=0, outcome=p.NONE):
        fields = self.groups.snapshot(group, sequence, outcome)
        if group not in self.fixture.groups or not self._scope_valid():
            fields[91], fields[103], fields[106], fields[107] = p.UNAVAILABLE, 0, 0, 0
            return fields
        fields[91] = p.PLAYING if self.session.groups[group].accepted else p.EMPTY
        fields[103] |= (self.backend.read_capabilities(group) if self._read_depth
                        else self.backend.capabilities(group))
        fields[106] = self.session.view_for(group)
        fields[107] = self.session.take_id_for(group)
        if group == self.session.recording_group:
            fields[94] = self.session.countdown(self.fixture.song.current_song_time)
        pending = self._pending.get(group)
        if pending is not None:
            command, token = pending
            if command.op == p.ABORT:
                fields[106], fields[107] = QUEUED_VIEW, token
            else:
                fields[91], fields[103] = p.UNAVAILABLE, fields[103] & p.CAP_MIXER
        if group in self.backend.faults:
            fields[91], fields[103] = p.UNAVAILABLE, 0
        clock = self.group_clock(group)
        if clock is not None:
            fields[93] = min(127, int(128 * clock.position / clock.span))
        return fields

    @_consistent_read
    def recording_bars(self, group, take_id):
        """Pure feedback for the exact current observed recording generation."""
        if (type(group) is not int or type(take_id) is not int or not 0 <= take_id < 128
                or group not in self.fixture.groups or group in self.backend.faults):
            return 0
        take = self.session.take
        if (take is None or take.group != group or take.token % 128 != take_id
                or take.state not in ('recording', 'finish_queued')
                or self.session.view_for(group) not in (2, 3) or not self._scope_valid()):
            return 0
        return self.backend.recording_bars(take.slot, take.token)

    @_consistent_read
    def recording_clock(self, group, take_id):
        """Render the exact current recording with its own start-relative bar."""
        if (type(group) is not int or type(take_id) is not int or not 0 <= take_id < 128
                or group not in self.fixture.groups or group in self.backend.faults):
            return None
        take = self.session.take
        if (take is None or take.group != group or take.token % 128 != take_id
                or take.state not in ('recording', 'finish_queued')
                or self.session.view_for(group) not in (2, 3) or not self._scope_valid()):
            return None
        context = p.PLAYING if self.session.groups[group].accepted else p.EMPTY
        return self.backend.recording_clock(take.slot, take.token, context, take_id)

    def _countdown_take(self, group, take_id):
        if (type(group) is not int or type(take_id) is not int or not 0 <= take_id < 128
                or group not in self.fixture.groups or group in self.backend.faults):
            return None
        take = self.session.take
        if (take is None or take.group != group or take.token % 128 != take_id
                or take.state != 'queued' or self.session.view_for(group) != QUEUED_VIEW
                or not self._scope_valid()):
            return None
        return take

    @_consistent_read
    def countdown_remaining_bars(self, group, take_id):
        take = self._countdown_take(group, take_id)
        return self.backend.countdown_remaining_bars(take.slot, take.token, take.schedule) if take else 0

    @_consistent_read
    def countdown_clock(self, group, take_id):
        take = self._countdown_take(group, take_id)
        if take is None:
            return None
        context = p.PLAYING if self.session.groups[group].accepted else p.EMPTY
        return self.backend.countdown_clock(take.slot, take.token, take.schedule, context, take_id)

    @_consistent_read
    def global_clock(self):
        """Actual bar clock, also sent while recording for whole-ring flashes."""
        if not self._control_scope_valid():
            return None
        try:
            return self.fixture.global_clock()
        except (FixtureError, RuntimeError, ValueError, TypeError, AttributeError):
            return None

    @_consistent_read
    def group_clock(self, group):
        if group not in self.fixture.groups or group in self.backend.faults or not self._scope_valid():
            return None
        token = self.session.take_id_for(group)
        pending = self._pending.get(group)
        if pending is not None and pending[0].op == p.ABORT:
            token = pending[1]
        try:
            if self.session.groups[group].reference is not None:
                return self.backend.reference_clock(group, take_id=token)
            # Empty overview groups use the shared global bank. An active first
            # take needs the same actual clock with its own generation token.
            if token or self.session.view_for(group) or pending is not None:
                clock = self.fixture.global_clock()
                return clock._replace(take_id=token)
        except (FixtureError, BackendFailure, RuntimeError, ValueError, TypeError, AttributeError):
            pass
        return None

    def report(self):
        return {"backend": self.backend.diagnostic_state(), "scope_valid": self._scope_valid(),
                "view": self.session.view, "view_group": self.session.view_group,
                "take_id": self.session.take_id_for(self.session.view_group),
                "groups": [{"group": index, "row": state.row,
                            "accepted_layers": len(state.accepted)}
                           for index, state in enumerate(self.session.groups)],
                "pending_commands": [{"group": group, "sequence": pair[0].seq,
                                      "opcode": pair[0].op} for group, pair in self._pending.items()]}
