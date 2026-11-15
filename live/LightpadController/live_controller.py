"""Production controller facade: Live owns music, transactions own only intent.

No saved-file inspection, manifest path, prepared hash or proof-directory data
is needed. Read methods never create tracks or issue recording operations.
"""
import math
import copy

from . import protocol as p
from .live_state import LiveState, read_grid, same, number
from .live_recording import LiveRecording
from .metronome import MetronomeControls
from .parameter_bindings import normalized, PARAMETER_OPCODES
from .timing import Clock


class LiveController:
    def __init__(self, song, quantizations=None, assignments=None, emit=None, monotonic=None):
        self.song = song
        self.state = LiveState(song)
        self.recording = LiveRecording(song, self.state, quantizations, emit, monotonic)
        self.assignments = copy.deepcopy(dict(assignments or {}))
        self._explicit_bindings = {}
        self.controls = MetronomeControls(song, lambda: True, now=monotonic)
        self.groups = self  # Existing Surface read-only adapter compatibility.
        self._depth = 0
        self._cached = False
        self._events = []

    def begin_read_batch(self):
        self._depth += 1
        self.invalidate_read_batch()

    def end_read_batch(self):
        self._depth = max(0, self._depth-1)
        self.invalidate_read_batch()

    def invalidate_read_batch(self):
        self._cached = False

    def _read(self):
        if not self._depth or not self._cached:
            self.recording.reconcile()
            self._cached = True
        return self.state

    def readiness(self):
        try:
            state = self._read()
            ready = bool(state.groups)
            return dict(state=5 if ready else 4 if state.issues else 3,
                        reason='ready' if ready else 'remote_groups_ambiguous' if state.issues else 'no_remote_groups',
                        recording_busy=self.active_recording() is not None,
                        issues={str(k): v for k, v in {**state.issues, **self.recording.issues}.items()})
        except Exception as error:
            return dict(state=4, reason='live_state_unavailable', recording_busy=True,
                        issues={'state': type(error).__name__})

    def active_recording(self):
        self._read()
        take = self.recording.take
        return ((take.group, take.take_id) if take is not None
                and take.state in ('queued', 'recording', 'finish_queued', 'canceling') else None)

    def modal_allowed(self):
        try:
            self._read()
            return (bool(self.state.groups) and self.active_recording() is None
                    and not self.recording._advance)
        except Exception:
            return False

    def meter_state(self):
        meter, click = self.controls.meter(), self.controls.metronome()
        return meter + (click,) if meter is not None and click is not None else None

    def _parameter(self, group, opcode):
        mixer = group.mixer_device
        if opcode == p.SET_VOLUME:
            return mixer.volume
        if opcode == p.SET_PAN:
            return mixer.panning
        index = {p.SET_SEND_1: 0, p.SET_SEND_2: 1, p.SET_SEND_3: 2, p.SET_CONTROL_4: 3}[opcode]
        group_index = next(i for i, item in self.state.groups.items() if same(item.group, group))
        specs = self.assignments.get(group_index)
        target = specs[index] if specs is not None else {'kind': 'send', 'index': index}
        identities = [group, mixer]
        if target['kind'] == 'send':
            parameter = mixer.sends[target['index']]
            if specs is not None:
                identities.append(self.song.return_tracks[target['index']])
        elif target['kind'] == 'device':
            owner = group
            path = target['device_path']
            for position in range(0, len(path), 2):
                device = owner.devices[path[position]]
                identities.append(device)
                if position+1 < len(path):
                    owner = device.chains[path[position+1]]
                    identities.append(owner)
            if 'expected_device_name' in target and device.name != target['expected_device_name']:
                raise ValueError('configured device name does not match')
            if 'parameter_name' in target:
                name = target['parameter_name']
                if not isinstance(name, str) or not name:
                    raise ValueError('configured parameter name must be a nonempty string')
                matches = [parameter for parameter in device.parameters if parameter.name == name]
                if len(matches) != 1:
                    raise ValueError('configured parameter name is missing or ambiguous')
                parameter = matches[0]
            else:
                parameter = device.parameters[target['parameter_index']]
            if 'expected_parameter_name' in target and parameter.name != target['expected_parameter_name']:
                raise ValueError('configured parameter name does not match')
        else:
            raise ValueError('unknown mixer assignment')
        if specs is not None:
            identities.append(parameter)
            key = (group_index, index)
            retained = self._explicit_bindings.get(key)
            if retained is None:
                self._explicit_bindings[key] = tuple(identities)
            elif len(retained) != len(identities) or not all(same(a, b) for a, b in zip(retained, identities)):
                raise ValueError('explicit mixer target moved or was replaced; reassign this control')
        return parameter

    def configure_targets(self, index, targets):
        """Explicit reassignment authorizes a new binding for this group only."""
        self.assignments[index] = copy.deepcopy(targets)
        for key in tuple(self._explicit_bindings):
            if key[0] == index:
                del self._explicit_bindings[key]

    def _mix(self, command):
        group = self._read().groups.get(command.group)
        if group is None:
            return p.NOT_READY
        if command.op == p.SET_MUTE:
            group.group.mute = command.arg == 127
            return p.APPLIED
        parameter = self._parameter(group.group, command.op)
        if not getattr(parameter, 'is_enabled', True):
            return p.NOT_READY
        fraction = .5 if command.op == p.SET_PAN and command.arg == 64 else command.arg / 127.
        value = parameter.min + (parameter.max-parameter.min)*fraction
        parameter.value = round(value) if getattr(parameter, 'is_quantized', False) else value
        return p.APPLIED

    def dispatch(self, command):
        mixer = command.op == p.SET_MUTE or command.op in PARAMETER_OPCODES
        if not mixer:
            self.invalidate_read_batch()
        try:
            # Mixer writes change Native parameter values, not track/clip
            # membership. Keep this callback's already observed graph; snapshot
            # still reads the actual changed values. Every callback starts fresh.
            if mixer:
                return self._mix(command)
            self.recording.reconcile()
            if command.op == p.REFRESH:
                return p.APPLIED
            if command.op == p.ACK_COMPLETION:
                return self.recording.acknowledge(command.group, command.arg)
            if command.op in (p.TAP_TEMPO, p.SET_NUMERATOR, p.SET_DENOMINATOR, p.SET_METRONOME):
                return self.controls.dispatch(command)
            if command.op == p.BEGIN:
                outcome = self.recording.begin(command.group)
            elif command.op in (p.ACCEPT, p.ACCEPT_NEXT):
                outcome = self.recording.accept(command.group, command.op == p.ACCEPT_NEXT)
            elif command.op in (p.ABORT, p.RETRY):
                outcome = self.recording.cancel(command, command.op == p.RETRY)
            elif command.op == p.ADVANCE:
                outcome = self.recording.advance(command)
            else:
                return p.BAD_COMMAND
            events = self.recording.tick()
            immediate = next((result for candidate, result in events if candidate == command), None)
            self._events.extend((candidate, result) for candidate, result in events if candidate != command)
            return immediate if immediate is not None else outcome
        except (AttributeError, RuntimeError, ValueError, TypeError, IndexError, KeyError) as error:
            self.recording.issues[command.group] = str(error)
            return p.NOT_READY
        finally:
            if not mixer:
                self.invalidate_read_batch()

    def tick(self):
        self.invalidate_read_batch()
        # An idle tick only reconciles observations. Reuse those observations
        # for the rest of this callback. Active recording retains its stricter
        # revalidation after native effects and structural changes.
        idle = self.recording.take is None and not self.recording.needs_tick()
        events, self._events = self._events, []
        events.extend(self.recording.tick())
        if idle and self.recording.take is None and not self.recording.needs_tick():
            self._cached = 'state' not in self.recording.issues
        else:
            self.invalidate_read_batch()
        return events

    def needs_tick(self):
        return bool(self._events) or self.recording.needs_tick()

    def _fields(self, sequence, outcome):
        fields = {i: 0 for i in range(91, 109)}
        fields.update({94: 127, 104: sequence, 105: outcome})
        return fields

    def snapshot(self, index, sequence=0, outcome=p.NONE):
        fields = self._fields(sequence, outcome)
        try:
            group = self._read().groups.get(index)
            if group is None:
                return fields
            running_clips = [item for item in group.playing if not item.clip.is_recording]
            fields[91] = p.PLAYING if running_clips else p.EMPTY
            fields[92] = 127 if group.group.mute else 0
            color = int(group.group.color)
            for cc, shift in ((95, 16), (96, 8), (97, 0)):
                fields[cc] = round(((color >> shift) & 255)*127/255)
            for opcode, cc in ((p.SET_VOLUME, 98), (p.SET_SEND_1, 99), (p.SET_SEND_2, 100),
                               (p.SET_SEND_3, 101), (p.SET_CONTROL_4, 108), (p.SET_PAN, 102)):
                try:
                    fields[cc] = normalized(self._parameter(group.group, opcode),
                                            preserve_positive=opcode != p.SET_PAN)
                except (AttributeError, RuntimeError, ValueError, IndexError, KeyError, TypeError):
                    pass  # One missing send never disables native volume/pan/mute.
            fields[103] = p.CAP_MIXER | p.CAP_ADVANCE
            if bool(self.song.is_playing) and not getattr(group.group, 'is_frozen', False):
                try:
                    read_grid(self.song)
                    if 'q_bar' in self.recording.quantizations:
                        fields[103] |= p.CAP_RECORDING | p.CAP_REFERENCE_WRAP | p.CAP_CHAINING
                except (AttributeError, RuntimeError, ValueError):
                    pass
            take = self.recording.take
            if take is not None and take.group == index:
                fields[106] = {'queued': 1, 'recording': 2, 'finish_queued': 3,
                               'completed': 4, 'canceling': 1}.get(take.state, 0)
                fields[107] = take.take_id
                if take.state == 'queued':
                    fields[94] = min(127, self.countdown_remaining_bars(index, take.take_id))
            clock = self.group_clock(index)
            if clock is not None:
                fields[93] = min(127, int(128*clock.position/clock.span))
        except (AttributeError, RuntimeError, ValueError, TypeError, IndexError):
            fields[91], fields[103] = p.UNAVAILABLE, 0
        return fields

    def global_clock(self):
        try:
            return read_grid(self.song).clock()
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return None

    def group_clock(self, group):
        try:
            self._read()
            reference = self.state.reference(group)
            if reference is None:
                return None
            take = self.recording.take
            return reference.clock(read_grid(self.song), take.take_id if take and take.group == group else 0)
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return None

    def _take(self, group, take_id, states):
        self._read()
        take = self.recording.take
        return (take if take is not None and take.group == group and take.take_id == take_id
                and take.state in states and group in self.state.groups else None)

    def _context(self, group):
        return p.PLAYING if any(not x.clip.is_recording for x in self.state.groups[group].playing) else p.EMPTY

    def countdown_remaining_bars(self, group, take_id):
        try:
            take = self._take(group, take_id, ('queued',))
            if take is None:
                return 0
            grid = read_grid(self.song)
            return max(0, math.ceil((take.target-grid.position)/grid.length-1e-9))
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return 0

    def countdown_clock(self, group, take_id):
        try:
            take = self._take(group, take_id, ('queued',))
            if take is None:
                return None
            grid = read_grid(self.song)
            remaining = take.target-grid.position
            if remaining <= 0:
                return None
            span = grid.length*(8 if remaining > 8*grid.length else 1)
            return Clock(self._context(group), take_id, span, (-remaining)%span, 0,
                         grid.length, grid.tempo, grid.numerator, grid.denominator, grid.running)
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return None

    def recording_bars(self, group, take_id):
        try:
            take = self._take(group, take_id, ('recording', 'finish_queued'))
            if take is None or take.anchor is None or not take.clip.is_recording:
                return 0
            return max(0, math.floor((self.song.current_song_time-take.anchor)/take.bar+1e-9))
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return 0

    def recording_clock(self, group, take_id):
        try:
            take = self._take(group, take_id, ('recording', 'finish_queued'))
            if take is None or take.anchor is None or not take.clip.is_recording:
                return None
            position = (self.song.current_song_time-take.anchor)%take.bar
            return Clock(self._context(group), take_id, take.bar, position, 0, take.bar,
                         self.song.tempo, take.numerator, take.denominator, bool(self.song.is_playing))
        except (AttributeError, RuntimeError, ValueError, TypeError):
            return None
