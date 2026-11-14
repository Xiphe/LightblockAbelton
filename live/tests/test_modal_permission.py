"""Helper-only modal permission from actual host state, without Live or MIDI."""
import unittest
from unittest.mock import patch
from controller_test_support import RecordingSurfaceCase
from LightpadController import protocol as p

def cached_gate(snapshot_times, ui_time, view, held, now):
    return len(snapshot_times) == 8 and view == 0 and (not held) and (0 <= now - ui_time <= 1) and all((phase == 0 and 0 <= now - received <= 1 for phase, received in snapshot_times))

class PermissionTests(RecordingSurfaceCase):

    def query(self, allowed, nonce=42):
        calls = list(self.native.calls)
        commands = list(self.commands())
        engine = self.surface._engine
        session = engine.session
        take = session.take
        self.instance.sent.clear()
        self.surface.receive_midi((191, 109, nonce))
        self.assertEqual(self.instance.sent, [(191, 109 if allowed else 108, nonce)])
        self.assertEqual(self.native.calls, calls)
        self.assertEqual(self.commands(), commands)
        self.assertIs(self.surface._engine, engine)
        self.assertIs(engine.session, session)
        self.assertIs(session.take, take)

    def test_idle_allows_correlated_nonce_without_graphics_or_native_mutation(self):
        self.query(True, 0)
        self.query(True, 127)

    def test_disconnected_or_invalid_scope_denies(self):
        self.surface._parser.ready = False
        self.query(False)
        self.surface._parser.ready = True
        with patch.object(self.surface._engine, '_scope_valid', return_value=False):
            self.query(False)

    def test_queued_recording_and_finish_pending_deny(self):
        take, slot = self.begin()
        self.query(False)
        self.start_native(take, slot)
        self.query(False)
        self.native.song.current_song_time = slot.clip.start_time + 2
        self.tap(9.5, 2.5)
        self.query(False)

    def test_retry_and_cancellation_cleanup_deny(self):
        take, slot = self.record()
        self.tap(2.5, 9.5)
        self.query(False)
        slot.track.quiet_native()
        self.tick()
        self.query(False)

    def test_cancel_cleanup_denies_until_native_quiet_then_allows(self):
        take, slot = self.record()
        self.tap(2.5, 2.5)
        self.assertIsNone(self.surface._engine.session.take)
        self.query(False)
        slot.track.quiet_native()
        self.tick()
        self.pump(0.15)
        self.query(True)

    def test_accepted_reference_survives_allowed_permission_and_hello(self):
        take, slot = self.record()
        self.native.song.current_song_time = slot.clip.start_time + 2
        self.tap(9.5, 2.5)
        self.native.song.current_song_time = slot.clip.start_time + 4
        slot.finish_native(4)
        self.tick()
        self.pump(0.15)
        group = self.surface._engine.session.groups[0]
        accepted = tuple(group.accepted)
        clip = slot.clip
        self.query(True)
        self.surface.receive_midi((191, 110, p.VERSION))
        self.query(True)
        self.assertEqual(tuple(group.accepted), accepted)
        self.assertIs(slot.clip, clip)

    def test_held_auxiliary_group_and_partial_frames_deny(self):
        self.raw(1, 1, x=12.5, y=13.5, drawer_open=True)
        self.query(False)
        self.raw(3, 1, x=12.5, y=13.5, drawer_open=True)
        self.query(True)
        self.raw(1, 6, finger=1, x=1.5, y=1.5)
        self.query(False)
        self.surface.receive_midi((191, 110, p.VERSION))
        self.query(True)
        self.surface.receive_midi((176, 20, 5))
        self.query(False)

    def test_pending_desire_command_or_completion_denies(self):
        ui = self.surface._ui
        ui._desired[0][16] = 5
        self.query(False)
        ui._desired[0].clear()
        self.surface._ready_completions.append(object())
        self.query(False)
        self.surface._ready_completions.clear()
        self.surface._pending_results[0] = object()
        self.query(False)
        self.surface._pending_results.clear()
        self.query(True)

    def test_fresh_cached_overview_is_not_permission_after_begin(self):
        now = self.now.now
        cached = [(self.surface._engine.snapshot(g)[106], now) for g in range(8)]
        self.begin()
        self.assertTrue(cached_gate(cached, now, 0, 0, self.now.now))
        self.assertIsNotNone(self.surface._engine.session.take)
        self.query(False)

    def test_scope_failure_zeros_phase_even_with_active_take(self):
        take, slot = self.record()
        with patch.object(self.surface._engine, '_scope_valid', return_value=False):
            self.surface.receive_midi((191, 110, p.VERSION))
            now = self.now.now
            cached = [(self.surface._engine.snapshot(g)[106], now) for g in range(8)]
            self.assertEqual(self.surface._ui.view, 0)
            self.assertTrue(cached_gate(cached, now, 0, 0, now))
            self.assertIs(self.surface._engine.session.take, take)
            self.query(False)

    def test_unattached_reserved_backend_attempt_denies(self):
        backend = self.surface._engine.backend
        lease = backend.reserve(0, 0, ())
        self.assertIsNone(self.surface._engine.session.take)
        self.query(False)
        backend.release(0, lease)
        self.query(True)

    def test_malformed_requests_never_reply_or_mutate(self):
        before = list(self.native.calls)
        self.instance.sent.clear()
        for message in ((191, 109), (191, 109, 128), (191, 109, True), (191, 109, -1), (191, 109, 5, 1)):
            self.surface.receive_midi(message)
        self.assertEqual(self.instance.sent, [])
        self.assertEqual(self.native.calls, before)
if __name__ == '__main__':
    unittest.main()
