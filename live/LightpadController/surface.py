"""Protocol-v4 Live entry point, native recording and host gesture handling."""

import math
import time
from functools import wraps

import Live
from _Framework.ControlSurface import ControlSurface

from . import protocol as p
from . import configuration as config
from . import timing
from .audio_configuration import load_audio_configuration
from .audio_controller import AudioController
from .audio_fixture import AudioFixture
from .groups import LiveGroups
from .output_pacing import OutputPacer
from .compact_wire import project_wire
from .device_ui import DeviceUI
from .touch_protocol import TouchParser
from . import health


def _ui_callback(method):
    """Share actual UI reads within one callback, never across callbacks."""
    @wraps(method)
    def callback(self, *args, **kwargs):
        previous = self._ui_cache
        if previous is not None:
            previous.clear()
        self._ui_cache = {}
        engine = self._engine
        begin = getattr(engine, 'begin_read_batch', None)
        end = getattr(engine, 'end_read_batch', None)
        batched = False
        try:
            if callable(begin) and callable(end):
                begin()
                batched = True
            return method(self, *args, **kwargs)
        finally:
            try:
                if batched:
                    end()
            finally:
                self._ui_cache = previous
                if previous is not None:
                    previous.clear()
    return callback


class LightpadController(ControlSurface):
    def __init__(self, c_instance):
        self._closed = False
        self._ui_cache = None
        self._parser = p.CommandParser()
        self._revision = [0] * 8
        self._outcomes = [(0, p.NONE)] * 8
        self._pending_results = {}
        self._ui_pending = {}
        self._ui_sequence = 0
        self._touch_parser = TouchParser()
        self._touch_seen = {}
        self._presentation_revision = 0
        self._last_presentation = None
        self._last = [None] * 8
        self._heartbeat = 0
        self._progress_at = 0
        self._timing_revision = [0] * 9
        self._timing_valid = [None] * 9
        self._last_global_feedback = None
        self._engine = None
        self._native_busy = False
        self._ready_completions = []
        self._configuration_failed = False
        self._production = bool(getattr(config, 'LIVE_STATE_MODE', True)) and not config.AUDIO_CONTROLLER_MANIFEST
        self._health = health.HealthPublisher(self._send_health_frame, time.monotonic)
        self._engine_retry_at = time.monotonic()
        self._output = OutputPacer(self._send_wire)
        super(LightpadController, self).__init__(c_instance)
        self._groups = LiveGroups(self.song(), assignments=config.PARAMETER_TARGETS)
        if self._production:
            try:
                self._engine = self._new_live_engine()
                self._groups = self._engine.groups
            except Exception as error:
                self._configuration_failed = True
                self.log_message('Lightpad startup unavailable: %s: %s' % (type(error).__name__, error))
        elif config.AUDIO_CONTROLLER_MANIFEST:
            try:
                manifest = load_audio_configuration(config.AUDIO_CONTROLLER_MANIFEST, config.SYNC_UI_EVIDENCE)
                fixture = AudioFixture(self.song(), manifest,
                    Live.Song.Quantization.q_bar, Live.Track.Track.monitoring_states.OFF,
                    native_bar_clock=config.NATIVE_BAR_CLOCK,
                    native_quantizations={name: getattr(Live.Song.Quantization, name)
                        for name in ('q_bar', 'q_half', 'q_quarter', 'q_eight', 'q_sixtenth')}
                        if config.NATIVE_BAR_CLOCK else None)
                self._engine = AudioController(fixture, assignments=config.PARAMETER_TARGETS,
                    validated_features=config.VALIDATED_AUDIO_FEATURES,
                    emit=self._recording_event, auto_enable=True)
                self._groups = self._engine.groups
            except Exception as error:
                self._configuration_failed = True
                self.log_message("Lightpad audio configuration unavailable: %s: %s" % (type(error).__name__, error))
        self._ui = DeviceUI(self._ui_send, self._ui_snapshot, self._ui_meter, time.monotonic,
                            recording_bars=self._ui_recording_bars,
                            countdown_bars=self._ui_countdown_bars,
                            transport_running=(lambda: bool(self.song().is_playing)) if self._production else None)
        self._ui._frame_context = lambda: self._touch_parser.latest
        self._touch_parser.pre_touch = self._ui.pre_touch
        if self._production:
            self.log_message('Lightpad controller: live-state discovery enabled; groups use [R1] through [R8]')
        else:
            self.log_message("Lightpad controller v4: scratch audio %s; evidence %s" % (
                "bound" if self._engine else "unavailable", tuple(config.VALIDATED_AUDIO_FEATURES)))

    def _send_health_frame(self, messages):
        for message in messages:
            self._send_midi(message, optimized=False)

    def _new_live_engine(self):
        from .live_controller import LiveController
        native = getattr(getattr(Live, 'Song', None), 'Quantization', None)
        quantizations = {name: getattr(native, name) for name in
            ('q_bar', 'q_half', 'q_quarter', 'q_eight', 'q_sixtenth')
            if native is not None and hasattr(native, name)}
        return LiveController(self.song(), quantizations=quantizations,
            assignments=config.PARAMETER_TARGETS, emit=self._recording_event,
            monotonic=time.monotonic)

    def _recover_live_engine(self):
        # Only retry a constructor which never produced a working engine. Never
        # replace in-flight recording intent to recover a stale display.
        now = time.monotonic()
        if (not self._production or self._engine is not None
                or now - self._engine_retry_at < .5):
            return
        self._engine_retry_at = now
        try:
            engine = self._new_live_engine()
        except Exception:
            return
        self._engine, self._groups = engine, engine.groups
        self._configuration_failed = False
        self._ui.reset()
        self._invalidate_ui()
        self._last = [None] * 8
        self._last_presentation = None
        self.log_message('Lightpad controller recovered its live group discovery')

    def _publish_health(self, force=False):
        if self._closed or not self._production:
            return
        read = getattr(self._engine, 'readiness', None)
        if not callable(read) and not self._configuration_failed:
            return
        try:
            if self._configuration_failed:
                state, reason, flags = health.FAILURE, health.HOST_FAILURE, 0
            else:
                observed = read()
                state = observed['state']
                reason = (health.NONE if state == health.READY else
                          health.NO_REMOTE_GROUPS if state == health.NO_GROUPS else health.HOST_FAILURE)
                flags = health.RECORDING_BUSY if observed.get('recording_busy') else 0
                permitted = getattr(self._engine, 'modal_allowed', None)
                if state == health.READY and callable(permitted) and permitted():
                    flags |= health.SAFE_MODAL
            self._health.publish(state, reason, flags, force)
        except Exception as error:
            self.log_message('Lightpad readiness unavailable: %s' % type(error).__name__)
            self._health.publish(health.FAILURE, health.HOST_FAILURE)

    def _sync_live_recording(self):
        read = getattr(self._engine, 'active_recording', None)
        if callable(read):
            try:
                active = read()
            except (RuntimeError, AttributeError, ValueError, TypeError):
                # A failed Native observation is not proof a take ended.
                return
            self._ui.reconcile_recording(active)

    def _recording_event(self, event):
        self.log_message("Lightpad native event: %s" % event)

    def _complete(self, command, outcome):
        pending = self._ui_pending.get(command.group)
        if pending is not None and pending[0] == command:
            del self._ui_pending[command.group]
            self._pin_terminals()
            pending[1](outcome)
            return
        self._outcomes[command.group] = (command.seq, outcome)
        self._pending_results[command.group] = (command, outcome)
        # Native cleanup may complete on the first CC of a later request.
        # Pin its result before that request can produce a BUSY observation.
        self._publish(force=True, only=command.group)

    def _result_sent(self, command, outcome):
        if self._pending_results.get(command.group) == (command, outcome):
            self._parser.complete(command, outcome)
            del self._pending_results[command.group]

    def _tick(self, notify=True):
        if self._closed or self._native_busy or self._engine is None:
            return
        self._native_busy = True
        self._invalidate_ui()
        try:
            completed = self._engine.tick()
        finally:
            self._native_busy = False
            self._invalidate_ui()
        self._ready_completions.extend(completed)
        if notify:
            ready, self._ready_completions = self._ready_completions, []
            for command, outcome in ready:
                self._complete(command, outcome)

    def _dispatch(self, command):
        if self._native_busy:
            return p.BUSY
        self._native_busy = True
        self._invalidate_ui()
        try:
            return (p.NOT_READY if self._configuration_failed else
                    self._engine.dispatch(command) if self._engine is not None else
                    self._groups.dispatch(command))
        except (RuntimeError, ValueError, AttributeError) as error:
            self.log_message("Lightpad command unavailable: %s" % error)
            return p.NOT_READY
        finally:
            self._native_busy = False
            self._invalidate_ui()

    def _invalidate_ui(self):
        if self._ui_cache is not None:
            self._ui_cache.clear()
        invalidate = getattr(self._engine, 'invalidate_read_batch', None)
        if callable(invalidate):
            invalidate()

    def _ui_send(self, group, opcode, argument, on_result):
        if (self._closed or not self._parser.ready or group in self._ui_pending
                or self._parser.pending(group)):
            return False
        # Negative IDs cannot collide with the legacy wire's 0..127 sequences.
        self._ui_sequence -= 1
        command = p.Command(self._ui_sequence, group, opcode, argument)
        self._ui_pending[group] = (command, on_result)
        outcome = self._dispatch(command)
        if outcome is not None:
            self._complete(command, outcome)
        return True

    def _ui_snapshot(self, group):
        if self._ui_cache is not None and group in self._ui_cache:
            return self._ui_cache[group]
        try:
            if self._configuration_failed:
                fields = None
            else:
                fields = (self._engine.snapshot(group) if self._engine is not None else
                          self._groups.snapshot(group))
        except (RuntimeError, ValueError, AttributeError):
            fields = None
        if self._ui_cache is not None:
            self._ui_cache[group] = fields
        return fields

    def _ui_countdown_bars(self, group, take_id):
        read = getattr(self._engine, 'countdown_remaining_bars', None)
        return read(group, take_id) if callable(read) else 0

    def _ui_recording_bars(self, group, take_id):
        read = getattr(self._engine, 'recording_bars', None)
        return read(group, take_id) if callable(read) else 0

    def _ui_meter(self):
        if self._ui_cache is not None and 'meter' in self._ui_cache:
            return self._ui_cache['meter']
        meter = self._read_ui_meter()
        if self._ui_cache is not None:
            self._ui_cache['meter'] = meter
        return meter

    def _read_ui_meter(self):
        if self._engine is not None and hasattr(self._engine, 'meter_state'):
            return self._engine.meter_state() or (0, 4, False)
        song = self.song()
        numerator = getattr(song, 'signature_numerator', 0)
        denominator = getattr(song, 'signature_denominator', 4)
        metronome = getattr(song, 'metronome', False)
        return numerator, denominator, metronome is True

    def _tick_ui(self):
        now = time.monotonic()
        for index, seen in tuple(self._touch_seen.items()):
            if now - seen >= 1.0:
                self._touch_parser.expire(index)
                del self._touch_seen[index]
        self._pin_terminals()
        self._ui.tick()
        self._sync_live_recording()

    def _pin_terminals(self):
        # DeviceUI may synchronously acknowledge observed completion. Protect
        # its actual terminal snapshot before that receipt clears the model.
        if not self._parser.ready:
            return
        for group in range(8):
            fields = self._ui_snapshot(group)
            previous = self._last[group]
            if (fields is not None and fields[106] == 4
                    and (previous is None or previous[106] != 4 or previous[107] != fields[107])):
                self._publish(only=group)

    def build_midi_map(self, handle):
        super(LightpadController, self).build_midi_map(handle)
        for channel in range(16):
            # Every CC must reach the parser so an unrelated CC interrupts a
            # partially received frame. These are reserved controller channels.
            for cc in range(128):
                Live.MidiMap.forward_midi_cc(self._c_instance.handle(), handle, channel, cc)

    def _send(self, messages):
        self._output.offer(messages, time.monotonic())

    def _send_wire(self, messages):
        messages = project_wire(messages)
        for message in messages:
            self._send_midi(message, optimized=False)

    def _modal_permission(self):
        """Read-only permission for this callback, never a cached UI inference."""
        try:
            ui, engine = self._ui, self._engine
            if (self._closed or not self._parser.ready or not self._touch_parser.ready
                    or self._configuration_failed or engine is None or self._native_busy):
                return False
            if (self._touch_seen or self._touch_parser._active or self._touch_parser._frames
                    or self._parser._frames or self._parser._pending or self._ui_pending
                    or self._pending_results or self._ready_completions):
                return False
            if (ui.view != 0 or ui.anchor is not None or ui.chord_mask or ui._chord
                    or ui.begin_wait or ui._rebind_take or ui._retry_recovery
                    or ui._inflight or ui._receipts or any(ui._desired)
                    or ui._pre_start or any(not c.ignored for c in ui._touches.values())
                    or any(not c.ignored for c in ui._roles.values()) or ui._aux):
                return False
            allowed = getattr(engine, 'modal_allowed', None)
            if callable(allowed):
                return bool(allowed())
            session, backend = engine.session, engine.backend
            if (session.take is not None or session.view != 0
                    or session.recording_group is not None or session.view_group is not None
                    or engine._pending or not backend.enabled or backend.faults
                    or any(a.state not in ('accepted', 'released') for a in backend._attempts.values())
                    or engine.needs_tick()
                    or any(backend.pending_cancel(g) or backend.pending_advance(g) for g in range(8))):
                return False
            self._invalidate_ui()
            return bool(engine._scope_valid())
        except Exception:
            return False

    @_ui_callback
    def receive_midi(self, message):
        if self._closed:
            return
        try:
            message = tuple(message)
        except TypeError:
            return
        # Helper-only request/reply lane. A query never drains gestures, runs
        # Native effects, waits for graphics, or changes Session ownership.
        if len(message) >= 2 and message[:2] == (0xBF, 109):
            if len(message) == 3 and all(type(v) is int for v in message) and 0 <= message[2] < 128:
                self._send_midi((0xBF, 109 if self._modal_permission() else 108, message[2]), optimized=False)
            return
        touch = self._touch_parser.receive(message)
        if len(message) >= 2 and message[:2] == (0xBF, 19):
            # The device supplies legal MIDI callback opportunities while its
            # recording view is open. Song-position notifications cannot write
            # Live properties. This lane only services pending native effects;
            # UI callbacks and MIDI output wait for the normal display path.
            if (message == (0xBF, 19, 0) and all(type(v) is int for v in message)
                    and self._parser.ready and self._engine is not None
                    and self._engine.needs_tick()):
                self._tick(notify=False)
            return
        hello = len(message) >= 2 and tuple(message[:2]) == (0xBF, 110)
        if hello:
            self._ui.reset()
            self._touch_seen.clear()
            self._last_presentation = None
        raw = len(message) == 3 and 0xB0 <= message[0] <= 0xBF and 20 <= message[1] <= 26
        if raw and touch is None:
            # Keep command-frame interruption semantics, but do not inspect
            # clips or repaint while a seven-CC contact is still incomplete.
            self._parser.receive(message)
            return
        if not raw or touch is not None:
            self._tick()
        event = self._parser.receive(message)
        if event == ("hello", p.VERSION):
            self._touch_parser.reset(ready=True)
            self._output.clear()
            self._send(p.encode_metadata(bool(self.song().is_playing)))
            self._sync_live_recording()
            self._publish(force=True)
            self._publish_timing(force=True)
        elif isinstance(event, p.Command):
            outcome = p.BUSY if event.group in self._ui_pending else self._dispatch(event)
            if outcome is not None:
                self._complete(event, outcome)
            self._publish(force=True, only=event.group)
            self._publish_timing(only=event.group)
        elif isinstance(event, p.Replay):
            if event.command.group not in self._pending_results:
                self._outcomes[event.command.group] = (event.command.seq, event.outcome)
                self._publish(force=True, only=event.command.group)
        elif isinstance(event, p.Rejected):
            self._outcomes[event.group] = (event.seq, event.outcome)
            self._publish(force=True, only=event.group)
        if self._parser.ready:
            if touch is not None:
                self._touch_seen[touch.index] = time.monotonic()
                self._ui.touch(touch.kind, touch.index, touch.x, touch.y)
                if touch.kind == 3:
                    self._touch_seen.pop(touch.index, None)
            if not raw or touch is not None:
                self._tick_ui()
                if touch is not None:
                    self._publish()
                    self._publish_timing()
                self._publish_presentation()
                self._publish_health()
                # Finish decoding a contact before spending this callback's
                # output budget. Otherwise CC20 can send three old clocks and
                # leave its CC26 gesture/result waiting for another window.
                self._output.flush(time.monotonic())
        else:
            self._output.clear()

    def _publish_presentation(self, force=False):
        if self._closed or not self._parser.ready:
            return
        payload = self._ui.presentation()
        now = time.monotonic()
        if force or payload != self._last_presentation or self._output.presentation_due(now):
            binding = None
            if payload[0] == 2 and payload[2] < 8 and not payload[4] & 1:
                fields = self._ui_snapshot(payload[2])
                if fields is not None:
                    binding = (payload[2], fields[91], payload[3])
            self._presentation_revision = (self._presentation_revision + 1) % 128
            self._output.presentation(p.encode_presentation(self._presentation_revision, payload), now, binding,
                                      on_sent=self._ui.presentation_receipt(payload))
            self._last_presentation = payload

    def _publish(self, force=False, only=None):
        if self._closed or not self._parser.ready:
            return
        for group in range(8) if only is None else [only]:
            sequence, outcome = self._outcomes[group]
            try:
                if self._configuration_failed:
                    raise ValueError("scratch audio configuration is invalid")
                actual = self._ui_snapshot(group)
                if actual is None:
                    raise ValueError("actual group state unavailable")
                fields = dict(actual)
                fields[104], fields[105] = sequence, outcome
            except (RuntimeError, ValueError, AttributeError):
                fields = dict((cc, 0) for cc in range(91, 109))
                fields[94], fields[104], fields[105] = 127, sequence, outcome
            # Fractional clocks have their own bounded-rate frames. Including
            # phase/countdown in this comparison floods the full snapshot bank
            # on every Live display callback while a reference plays.
            previous = self._last[group]
            changed = previous is None or any(fields[cc] != previous[cc]
                                              for cc in fields if cc not in (93, 94))
            now = time.monotonic()
            if force or changed or self._output.heartbeat_due(group, now):
                self._revision[group] = (self._revision[group] + 1) % 128
                result = self._pending_results.get(group)
                callback = None
                if result is not None and (sequence, outcome) == (result[0].seq, result[1]):
                    callback = lambda result=result: self._result_sent(*result)
                self._output.offer(p.encode_snapshot(group, self._revision[group], fields), now, callback)
            self._last[group] = fields

    def _publish_timing(self, only=None, force=False):
        if self._closed or not self._parser.ready or self._engine is None:
            return
        # Global timing remains independent and continuous during every take.
        # It drives real-bar flashes even when the group reference spans bars.
        tempo = getattr(self.song(), 'tempo', None)
        if type(tempo) not in (int, float) or not math.isfinite(tempo):
            tempo = None
        feedback = self._ui_meter() + (tempo,)
        changed = self._last_global_feedback is not None and feedback != self._last_global_feedback
        self._last_global_feedback = feedback
        indices = [8] + (list(range(8)) if only is None else [only])
        for index in indices:
            self._output.clock(index, lambda index=index: self._timing_frame(index, force),
                               time.monotonic(), urgent=index == 8 and changed)

    def _render_group_clock(self, index):
        if self._ui.view == 2 and self._ui.recording_group == index:
            fields = self._ui_snapshot(index)
            if fields is None:
                return None
            if fields[106] in (1, 2, 3):
                read = getattr(self._engine, 'countdown_clock' if fields[106] == 1 else 'recording_clock', None)
                if (fields[107] != self._ui.active_take or not callable(read)):
                    return None
                return read(index, self._ui.active_take)
        return self._engine.group_clock(index)

    def _countdown_page_allowed(self, index, clock):
        if (index >= 8 or not isinstance(clock, timing.Clock)
                or self._ui.view != 2 or self._ui.recording_group != index):
            return False
        try:
            if clock.first_bar != 0 or clock.span != 8 * clock.bar_length:
                return False
            fields = self._ui_snapshot(index)
            return (fields is not None and fields[106] == 1
                    and fields[107] == self._ui.active_take == clock.take_id)
        except Exception:
            return False

    def _timing_frame(self, index, force=False):
        clock = self._engine.global_clock() if index == 8 else self._render_group_clock(index)
        if clock is None and self._timing_valid[index] is False and not force:
            return None
        revision = (self._timing_revision[index] + 1) % 128
        try:
            messages = (timing.encode_global_timing(revision, clock, self._ui_meter()[2]) if index == 8 else
                        timing.encode_view_timing(index, revision, clock,
                            countdown_page=self._countdown_page_allowed(index, clock)))
        except ValueError:
            # Skip an unrepresentable endpoint sample, never invent a wrap.
            if (clock is not None and 0 <= clock.position < clock.span
                    and round(clock.position * 64) == round(clock.span * 64)):
                return False
            messages = (timing.encode_global_timing(revision, None) if index == 8 else
                        timing.encode_view_timing(index, revision, None))
            clock = None
        self._timing_revision[index] = revision
        self._timing_valid[index] = clock is not None
        return messages

    @_ui_callback
    def update_display(self):
        super(LightpadController, self).update_display()
        if self._closed:
            return
        self._recover_live_engine()
        # Continue observing owned native effects if the device disconnects.
        self._tick()
        self._publish_health()
        if not self._parser.ready:
            return
        now = time.monotonic()
        self._tick_ui()
        self._publish()
        self._publish_timing()
        self._publish_presentation()
        if now - self._progress_at >= 0.25:
            self._progress_at = now
            song = self.song()
            messages = [(0xBF, 112, 127 if song.is_playing else 0)]
            beat = song.current_song_time
            if type(beat) in (int, float) and math.isfinite(beat):
                messages.append((0xBF, 113, int((beat % 1.0) * 127)))
            self._send(messages)
        self._output.flush(time.monotonic())

    @_ui_callback
    def refresh_state(self):
        super(LightpadController, self).refresh_state()
        self._publish(force=True)
        self._publish_timing(force=True)
        self._publish_presentation(force=True)
        self._publish_health(force=True)
        if self._parser.ready:
            self._output.flush(time.monotonic())

    def disconnect(self):
        self._closed = True
        self._parser.ready = False
        self._touch_parser.reset()
        self._ui.reset()
        self._output.clear()
        super(LightpadController, self).disconnect()
