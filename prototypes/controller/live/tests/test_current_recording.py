"""Current role-tagged recording actions through the real native executor.

Native observations are explicit fakes. Device rendering is tested separately.
"""
import unittest
from controller_test_support import RecordingSurfaceCase
from LightpadController import protocol as p

class CurrentRecordingTests(RecordingSurfaceCase):

    def test_down_drag_reaches_real_audio_backend_and_disabled_accept_start_is_sticky(self):
        take, slot = self.begin()
        self.raw(1, finger=1, can_accept=False)
        self.start_native(take, slot)
        self.raw(3, finger=1, can_accept=True)
        self.assertEqual(self.commands(), [(p.BEGIN, 0)])
        self.assertEqual(self.surface._engine.session.take.state, 'recording')

    def test_recording_accept_uses_visible_start_permission_and_real_finish(self):
        take, slot = self.record()
        self.native.song.current_song_time = slot.clip.start_time + 2
        self.tap(9.5, 2.5)
        self.assertEqual(self.commands(), [(p.ACCEPT, 0)])
        self.assertEqual(self.surface._engine.session.take.state, 'finish_queued')
        self.native.song.current_song_time = slot.clip.start_time + 4
        slot.finish_native(4)
        self.tick()
        self.pump(0.15)
        self.assertTrue(self.surface._engine.session.groups[0].accepted)
        self.assertFalse(slot.clip.is_recording)

    def test_retry_rebind_does_not_reinterpret_held_accept_from_previous_take(self):
        take, slot = self.record()
        self.tap(2.5, 9.5)
        self.assertEqual(self.commands(), [(p.RETRY, 0)])
        self.raw(1, finger=1, can_accept=False)
        slot.track.quiet_native()
        self.tick()
        new = self.surface._engine.session.take
        self.assertIsNotNone(new)
        self.assertNotEqual(new.token, take.token)
        newslot = self.native.slot(new.slot)
        self.start_native(new, newslot)
        self.raw(3, finger=1, can_accept=True)
        self.assertEqual(self.commands(), [(p.RETRY, 0)])
        self.assertEqual(self.surface._engine.session.take.token, new.token)

    def test_accept_next_dispatches_and_preserves_accepted_audio_before_following_take(self):
        take, slot = self.record()
        self.native.song.current_song_time = slot.clip.start_time + 2
        self.tap(9.5, 9.5)
        self.assertEqual(self.commands(), [(p.ACCEPT_NEXT, 0)])
        self.assertIsNotNone(self.surface._engine.session.take.next_take)
        self.native.song.current_song_time = slot.clip.start_time + 4
        slot.finish_native(4)
        self.tick()
        self.assertTrue(self.surface._engine.session.groups[0].accepted)
        self.assertTrue(slot.has_clip)
        self.assertFalse(slot.clip.is_recording)
        self.assertNotEqual(self.surface._engine.session.take.token, take.token)

    def test_cancel_top_left_waits_for_native_cleanup(self):
        take, slot = self.record()
        self.tap(2.5, 2.5)
        self.assertEqual(self.commands(), [(p.ABORT, 0)])
        self.assertTrue(slot.has_clip)
        slot.track.quiet_native()
        self.tick()
        self.pump(0.15)
        self.assertFalse(slot.has_clip)
        self.assertIsNone(self.surface._engine.session.take)
        self.assertEqual(self.surface._ui.view, 0)
if __name__ == '__main__':
    unittest.main()
