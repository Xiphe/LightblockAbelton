"""Native clock notification scheduling, without Live or MIDI endpoints."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_surface as surface_helpers
import test_audio_backend as backend_helpers
from LightpadController import configuration as config, protocol as p
from LightpadController import surface as module


class ListenerSong(SimpleNamespace):
    def __init__(self):
        super().__init__(tracks=[surface_helpers.Track()], return_tracks=[],
            is_playing=True, current_song_time=1.0, signature_numerator=4,
            signature_denominator=4, metronome=False, tempo=120.0)
        self.listeners, self.added, self.removed = [], [], []
        self.notification_context = False

    def add_current_song_time_listener(self, callback):
        self.added.append(callback)
        self.listeners.append(callback)

    def remove_current_song_time_listener(self, callback):
        assert any(item is callback for item in self.listeners), "remove the exact registered callback"
        self.removed.append(callback)
        self.listeners = [item for item in self.listeners if item is not callback]

    def current_song_time_has_listener(self, callback):
        return any(item is callback for item in self.listeners)

    def notify(self):
        previous = self.notification_context
        self.notification_context = True
        try:
            for callback in tuple(self.listeners):
                callback()
        finally:
            self.notification_context = previous



class ListenerEngine:
    def __init__(self):
        self.groups = object()
        self.active = False
        self.ticks = 0
        self.dispatches = []
        self.in_tick = self.in_dispatch = False
        self.on_tick = self.on_dispatch = None
        self.completions = []
        self.view = 0
        self.notification_song = None

    def _allow_native_mutation(self):
        if self.notification_song is not None and self.notification_song.notification_context:
            raise RuntimeError("Changes cannot be triggered by notifications. You will need to defer your response.")

    def needs_tick(self):
        return self.active

    def tick(self):
        self._allow_native_mutation()
        assert not self.in_tick and not self.in_dispatch, "native executor reentered"
        self.in_tick = True
        self.ticks += 1
        try:
            if self.on_tick is not None:
                self.on_tick()
            completions, self.completions = self.completions, []
            if completions:
                self.view, self.active = 0, False
            return completions
        finally:
            self.in_tick = False

    def dispatch(self, command):
        self._allow_native_mutation()
        assert not self.in_tick and not self.in_dispatch, "native dispatcher reentered"
        self.in_dispatch = True
        self.dispatches.append(command)
        try:
            if self.on_dispatch is not None:
                self.on_dispatch()
            self.active, self.view = True, 1
            return None
        finally:
            self.in_dispatch = False

    def snapshot(self, group, sequence=0, outcome=p.NONE):
        fields = dict.fromkeys(range(91, 109), 0)
        fields.update({91: p.PLAYING if group == 0 else p.UNAVAILABLE, 94: 127,
                       103: 31, 104: sequence, 105: outcome, 106: self.view if group == 0 else 0})
        return fields

    def global_clock(self):
        return None

    def group_clock(self, group):
        return None

    def meter_state(self):
        return 4, 4, False


class NativeClockListenerTests(unittest.TestCase):
    def make(self, native_clock=True, engine=True):
        self.song, self.engine = ListenerSong(), ListenerEngine()
        self.engine.notification_song = self.song
        self.now = 100.0
        instance = SimpleNamespace(song=self.song, sent=[], logs=[])
        patches = [
            patch.multiple(config, LIVE_STATE_MODE=False, AUDIO_CONTROLLER_MANIFEST="offline-test" if engine else None,
                           NATIVE_BAR_CLOCK=native_clock),
            patch.object(module, "load_audio_configuration", return_value={}),
            patch.object(module, "AudioFixture", return_value=object()),
            patch.object(module, "AudioController", return_value=self.engine),
            patch.object(module.time, "monotonic", side_effect=lambda: self.now),
            patch.object(module, "Live", SimpleNamespace(
                Song=SimpleNamespace(Quantization=SimpleNamespace(
                    q_bar=4, q_half=5, q_quarter=7, q_eight=9, q_sixtenth=11)),
                Track=SimpleNamespace(Track=SimpleNamespace(monitoring_states=SimpleNamespace(OFF=2)))))
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.surface = module.LightpadController(instance)
        self.addCleanup(self.surface.disconnect)
        self.instance = instance
        return self.surface

    def ready(self):
        self.surface.receive_midi((0xBF, 110, p.VERSION))
        self.engine.ticks = 0
        self.instance.sent.clear()

    def test_native_clock_mode_never_registers_a_mutating_song_listener(self):
        for enabled, engine in ((False, True), (True, False), (True, True)):
            with self.subTest(native_clock=enabled, engine=engine):
                surface = self.make(native_clock=enabled, engine=engine)
                self.assertEqual(self.song.added, [])
                self.assertEqual(self.song.listeners, [])
                self.engine.active = True
                self.song.notify()
                self.assertEqual(self.engine.ticks, 0)
                surface.disconnect()

    def test_ready_fast_tick_only_executes_native_work_without_ui_or_wire_work(self):
        surface = self.make()
        self.ready()
        self.engine.active = True
        with patch.object(surface, "_tick_ui") as ui, \
                patch.object(self.engine, "snapshot", wraps=self.engine.snapshot) as snapshots, \
                patch.object(surface, "_publish") as publish, \
                patch.object(surface, "_publish_timing") as clocks, \
                patch.object(surface, "_publish_presentation") as presentation, \
                patch.object(surface._output, "flush") as flush:
            surface.receive_midi((0xBF, 19, 0))
            self.assertEqual(self.engine.ticks, 1)
            ui.assert_not_called()
            snapshots.assert_not_called()
            publish.assert_not_called()
            clocks.assert_not_called()
            presentation.assert_not_called()
            flush.assert_not_called()
        self.assertEqual(self.instance.sent, [])
        self.assertFalse(self.song.notification_context)

    def test_fast_tick_completion_waits_for_normal_callback_and_sees_actual_state(self):
        surface = self.make()
        self.ready()
        results = []
        self.assertTrue(surface._ui_send(0, p.ABORT, 0, lambda outcome: results.append(
            (outcome, self.engine.in_tick, self.engine.in_dispatch,
             self.song.notification_context, surface._ui_snapshot(0)[106]))))
        command = self.engine.dispatches[-1]
        self.engine.completions = [(command, p.APPLIED)]
        with patch.object(surface._output, "flush") as flush:
            surface.receive_midi((0xBF, 19, 0))
            flush.assert_not_called()
        self.assertEqual(results, [])
        self.assertIn(0, surface._ui_pending)
        self.assertIn((command, p.APPLIED), surface._ready_completions)
        self.assertEqual(self.instance.sent, [])
        surface.update_display()
        self.assertEqual(results, [(p.APPLIED, False, False, False, 0)])
        self.assertNotIn(0, surface._ui_pending)
        self.assertEqual(list(surface._ready_completions), [])

    def test_invalid_fast_values_peer_down_idle_and_closed_are_inert(self):
        surface = self.make()
        self.engine.active = True
        surface.receive_midi((0xBF, 19, 0))
        self.assertEqual(self.engine.ticks, 0, "a peer must complete the v4 handshake first")
        self.ready()
        for message in ((0xBF, 19, 1), (0xBF, 19, 127), (0xBF, 19, 128),
                        (0xBF, 19, False), (0xBF, 19), (0xBF, 19, 0, 0)):
            with self.subTest(message=message):
                surface.receive_midi(message)
                self.assertEqual(self.engine.ticks, 0)
        self.engine.active = False
        surface.receive_midi((0xBF, 19, 0))
        self.assertEqual(self.engine.ticks, 0)
        surface.disconnect()
        self.engine.active = True
        surface.receive_midi((0xBF, 19, 0))
        self.assertEqual(self.engine.ticks, 0)
        self.assertEqual(self.instance.sent, [])

    def test_nested_fast_tick_tick_and_dispatch_cannot_reenter_executor(self):
        surface = self.make()
        self.ready()
        self.engine.active = True
        results = []

        def nested():
            surface.receive_midi((0xBF, 19, 0))
            surface._tick()
            results.append(surface._dispatch(p.Command(-99, 0, p.SET_MUTE, 0)))

        self.engine.on_tick = nested
        surface.receive_midi((0xBF, 19, 0))
        self.assertEqual(self.engine.ticks, 1)
        self.assertEqual(self.engine.dispatches, [])
        self.assertEqual(results, [p.BUSY])
        self.assertEqual(self.instance.sent, [])

    def test_fast_tick_during_native_dispatch_is_skipped_then_later_tick_executes(self):
        surface = self.make()
        self.ready()
        self.engine.active = True
        self.engine.on_dispatch = lambda: surface.receive_midi((0xBF, 19, 0))
        self.assertIsNone(surface._dispatch(p.Command(-1, 0, p.ABORT, 0)))
        self.assertEqual(self.engine.ticks, 0)
        surface.receive_midi((0xBF, 19, 0))
        self.assertEqual(self.engine.ticks, 1)
        self.assertEqual(len(self.engine.dispatches), 1)
        self.assertEqual(self.instance.sent, [])

    def test_fast_tick_interrupts_partial_finger_fifteen_frame(self):
        surface = self.make()
        self.ready()
        self.engine.active = True
        values = (60, 1, 64, 1, 64, 1, 60)
        start = [(0xBF, cc, value) for cc, value in zip(range(20, 27), values)]
        touches = []
        with patch.object(surface._ui, "touch", side_effect=lambda *args: touches.append(args)):
            for message in start[:3]:
                surface.receive_midi(message)
            surface.receive_midi((0xBF, 19, 0))
            for message in start[3:]:
                surface.receive_midi(message)
        self.assertEqual(touches, [], "fast CC19 must still interrupt the strict BF raw-touch frame")
        self.assertEqual(self.engine.ticks, 1)
        self.assertEqual(self.engine.dispatches, [])

    def test_real_backend_only_requests_fast_ticks_for_queued_and_finishing_work(self):
        helper = backend_helpers.AudioBackendTests()
        fixture, backend, session = helper.make()
        self.assertFalse(backend.needs_tick())
        self.assertEqual(session.begin(0), p.OUTCOME_QUEUED)
        self.assertTrue(backend.needs_tick())
        backend.tick()
        self.assertTrue(backend.needs_tick())
        take = session.take
        slot = helper.slot(take.slot)
        fixture.song.current_song_time = 4
        slot.begin_native(4)
        backend.tick()
        self.assertFalse(backend.needs_tick(), "steady recording without a pending finish needs no fast polling")
        fixture.song.current_song_time = 5
        self.assertEqual(session.accept(0), p.OUTCOME_QUEUED)
        self.assertTrue(backend.needs_tick())
        backend.tick()
        self.assertTrue(backend.needs_tick())
        fixture.song.current_song_time = 8
        slot.finish_native(4)
        backend.tick()
        self.assertFalse(backend.needs_tick(), "accepted playback needs no fast executor polling")

    def test_real_backend_keeps_fast_ticks_until_cancel_or_advance_is_observed_quiet(self):
        helper = backend_helpers.AudioBackendTests()
        fixture, backend, session = helper.make()
        self.assertEqual(session.begin(0), p.OUTCOME_QUEUED)
        backend.tick()
        slot = helper.slot(session.take.slot)
        self.assertEqual(session.abort(0), p.APPLIED)
        self.assertTrue(backend.needs_tick())
        backend.tick()
        self.assertTrue(backend.needs_tick())
        slot.track.quiet_native()
        backend.tick()
        self.assertFalse(backend.needs_tick())
        self.assertEqual(session.advance(0), p.APPLIED)
        self.assertTrue(backend.needs_tick())
        backend.tick()
        self.assertTrue(backend.needs_tick())
        for track in fixture.groups[0].layers:
            track.quiet_native()
        backend.tick()
        self.assertFalse(backend.needs_tick())


if __name__ == "__main__":
    unittest.main()
