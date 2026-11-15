"""PRIVATE one-parameter Live adapter; all Live access stays in Live callbacks.

No gestures, screens, graph scans per move, routing or track/scene creation.
Channel 1: generation69, command70/71/72; ack73/74/75; observed76/77/78.
Channel 16 CC79 establishes and echoes a new generation before commands.
"""
import json
import time
from pathlib import Path

import Live
from _Framework.ControlSurface import ControlSurface
from . import configuration as config


class OptimisticFaderSpike(ControlSurface):
    def __init__(self, c_instance):
        self._closed = False
        self._generation = None
        self._frame = None
        self._parameter = None
        self._dirty = False
        self._observation_revision = 0
        self._acks = []
        self._events = []
        self._trace_at = 0.0
        super().__init__(c_instance)
        # Construction performs only bounded discovery/read/listener attachment.
        # Actual parameter mutation requires both exact Set path and handshake.
        bound = self._find_parameter()
        self.log_message('Optimistic fader spike: target_bound=%s path=%s' %
                         (bound, str(self.song().file_path)))

    def build_midi_map(self, handle):
        super().build_midi_map(handle)
        # The framework does not automatically forward arbitrary CCs into
        # receive_midi. Register only the private command/handshake inputs.
        for channel, cc in ((0,69),(0,70),(0,71),(0,72),(15,79)):
            Live.MidiMap.forward_midi_cc(self._c_instance.handle(), handle,
                                         channel, cc)

    def _in_target_set(self):
        return bool(config.TARGET_SET) and str(self.song().file_path) == config.TARGET_SET

    def _find_parameter(self):
        self._detach_parameter()
        if not self._in_target_set():
            return False
        groups = [track for track in self.song().tracks
                  if track.is_foldable and str(track.name).startswith('[R1]')]
        if len(groups) != 1:
            return False
        self._parameter = groups[0].mixer_device.volume
        self._parameter.add_value_listener(self._value_changed)
        self._dirty = True
        return True

    def _detach_parameter(self):
        parameter, self._parameter = self._parameter, None
        if parameter is not None:
            try:
                if parameter.value_has_listener(self._value_changed):
                    parameter.remove_value_listener(self._value_changed)
            except (RuntimeError, AttributeError):
                pass

    def _value_changed(self):
        # This notification never triggers further Live writes or MIDI output.
        self._dirty = True

    def _actual(self):
        parameter = self._parameter
        if parameter is None:
            raise RuntimeError('R1 volume unavailable')
        width = parameter.max - parameter.min
        if width <= 0:
            raise RuntimeError('R1 volume range unavailable')
        return max(0, min(127, int(round(
            (parameter.value - parameter.min) / width * 127))))

    def _send_cc(self, status, cc, value):
        self._send_midi((status, cc, value), optimized=False)

    def _send_frame(self, first, middle, last, identity, actual):
        self._send_cc(0xB0, 69, self._generation)
        for cc, value in ((first, identity), (middle, actual), (last, identity)):
            self._send_cc(0xB0, cc, value)

    def _event(self, **values):
        if config.TRACE_PATH:
            self._events.append(values)
            # Bounded diagnostic memory even if the destination is unavailable.
            if len(self._events) > 512:
                self._events = self._events[-512:]

    def _reply(self, sequence, actual, event):
        self._send_frame(73, 74, 75, sequence, actual)
        event['ack_sent_ns'] = time.monotonic_ns()
        self._event(**event)

    def receive_midi(self, message):
        received = time.monotonic_ns()
        if self._closed or len(message) != 3:
            return
        status, cc, value = message
        if status == 0xBF and cc == 79:
            self._generation = None
            self._frame = None
            self._acks.clear()
            if self._find_parameter():
                self._generation = value
                self._observation_revision = 0
                self.log_message('Optimistic fader spike: handshake generation=%d' % value)
                self._send_cc(0xBF, 79, value)
                self._publish_observation()
            return
        if status != 0xB0 or self._generation is None:
            return
        if cc == 69:
            self._frame = {'generation': value} if value == self._generation else None
            return
        frame = self._frame
        if frame is None:
            return
        if cc == 70 and set(frame) == {'generation'}:
            frame['sequence'] = value
        elif cc == 71 and set(frame) == {'generation', 'sequence'}:
            frame['desired'] = value
        elif (cc == 72 and set(frame) == {'generation', 'sequence', 'desired'}
              and value == frame['sequence']):
            self._frame = None
            # Never let Save As or Set replacement change the mutation scope.
            if not self._in_target_set() or self._parameter is None:
                return
            started = time.monotonic_ns()
            parameter = self._parameter
            parameter.value = parameter.min + (parameter.max - parameter.min) * frame['desired'] / 127.0
            finished = time.monotonic_ns()
            actual = self._actual()
            event = dict(event='volume_command', generation=self._generation,
                         sequence=value, desired=frame['desired'], actual=actual,
                         received_commit_ns=received, native_set_enter_ns=started,
                         native_set_exit_ns=finished)
            delay = max(0., float(config.ACK_DELAY_SECONDS))
            if delay:
                # This spike has one in-flight command at the bridge. Retaining
                # at most one delayed reply also exposes a protocol violation.
                if self._acks:
                    self._event(event='unexpected_second_inflight', at_ns=finished)
                    old_sequence, old_actual, old_event, unused_due = self._acks.pop(0)
                    self._reply(old_sequence, old_actual, old_event)
                self._acks.append((value, actual, event, time.monotonic() + delay))
            else:
                self._reply(value, actual, event)
        else:
            self._frame = None

    def _publish_observation(self):
        if self._generation is None or self._parameter is None:
            return
        actual = self._actual()
        self._send_frame(76, 77, 78, self._observation_revision, actual)
        self._observation_revision = (self._observation_revision + 1) % 128
        self._dirty = False
        self._event(event='volume_observation', generation=self._generation,
                    actual=actual, sent_ns=time.monotonic_ns())

    def _flush_trace(self, force=False):
        now = time.monotonic()
        if not self._events or not config.TRACE_PATH or (not force and now - self._trace_at < .25):
            return
        self._trace_at = now
        events, self._events = self._events, []
        try:
            with Path(config.TRACE_PATH).open('a') as target:
                target.write(''.join(json.dumps(event, sort_keys=True) + '\n' for event in events))
        except OSError:
            # Diagnostic failure must not prevent musical parameter commands.
            self._events = (events + self._events)[-512:]

    def update_display(self):
        super().update_display()
        if self._closed:
            return
        now = time.monotonic()
        if self._acks and now >= self._acks[0][3]:
            sequence, actual, event, unused_due = self._acks.pop(0)
            self._reply(sequence, actual, event)
        if self._dirty:
            self._publish_observation()
        self._flush_trace()

    def disconnect(self):
        self._closed = True
        self._detach_parameter()
        self._flush_trace(force=True)
        super().disconnect()
