"""Start deadline cleanup against explicit native-state and clock fakes."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_audio_backend as native
from LightpadController import protocol as p
from LightpadController import audio_backend as module


class QuantizedStartCleanupTests(unittest.TestCase):
    def make(self):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()
        self.now = 100.0
        clock = patch.object(module, "time", SimpleNamespace(monotonic=lambda: self.now), create=True)
        clock.start()
        self.addCleanup(clock.stop)

    def test_public_beat_crossing_target_after_arm_disarms_only_owned_empty_track(self):
        for reported_after_arm in (4.0, 4.01):
            with self.subTest(reported_after_arm=reported_after_arm):
                self.make()
                self.fixture.song.current_song_time = 3.6
                self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
                take = self.session.take
                slot = self.helper.slot(take.slot)
                slot.track.after_arm = lambda: setattr(self.fixture.song, "current_song_time", reported_after_arm)
                self.backend.tick()
                self.assertFalse(slot.track.arm, "a known missed boundary after arming must clean up its empty owned arm")
                self.assertEqual(self.fixture.calls, [("arm", slot.track.name, True), ("arm", slot.track.name, False)])
                self.assertFalse(slot.has_clip)
                self.assertFalse(slot.is_triggered)
                self.assertFalse(self.backend._attempts[take.slot].start_issued)
                self.assertEqual(take.schedule.start_at, 8)
                self.assertEqual(self.backend._attempts[take.slot].start_target, 8)
                self.assertIs(self.session.take, take)
                self.assertEqual(take.state, "queued")
                self.assertNotIn(0, self.backend.faults)

    def test_frozen_public_beat_and_slow_arm_setter_disarm_before_actual_fire(self):
        self.make()
        self.fixture.song.current_song_time = 3.6
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        slot = self.helper.slot(take.slot)

        def slow_arm():
            self.now += .180

        slot.track.after_arm = slow_arm
        self.backend.tick()
        self.assertEqual(self.fixture.song.current_song_time, 3.6)
        self.assertFalse(slot.track.arm)
        self.assertEqual(self.fixture.calls, [("arm", slot.track.name, True), ("arm", slot.track.name, False)])
        self.assertFalse(slot.has_clip)
        self.assertFalse(slot.is_triggered)
        self.assertFalse(self.backend._attempts[take.slot].start_issued)
        self.assertEqual(take.schedule.start_at, 8)
        self.assertEqual(self.backend._attempts[take.slot].start_target, 8)
        self.assertIs(self.session.take, take)
        self.assertEqual(take.state, "queued")
        self.assertNotIn(0, self.backend.faults)

    def test_child_arm_stall_disarms_child_and_preserves_already_issued_parent_finish(self):
        for crossing in ("frozen_clock", "public_beat"):
            with self.subTest(crossing=crossing):
                self.make()
                lease, clip = self.helper.record()
                parent = self.session.take
                parent_slot = self.helper.slot(lease)
                self.fixture.song.current_song_time = 6
                self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
                child = parent.next_take
                child_slot = self.helper.slot(child.slot)

                def slow_child_arm():
                    if crossing == "frozen_clock":
                        self.now += .980
                    else:
                        self.fixture.song.current_song_time = 8.01

                child_slot.track.after_arm = slow_child_arm
                self.fixture.calls.clear()
                self.backend.tick()
                self.assertFalse(child_slot.track.arm, "refused child must not leave an empty track armed")
                self.assertFalse(child_slot.has_clip)
                self.assertFalse(self.backend._attempts[child.slot].start_issued)
                self.assertTrue(self.backend._attempts[lease].finish_issued)
                self.assertTrue(parent_slot.track.arm)
                self.assertIs(parent_slot.clip, clip)
                self.assertTrue(clip.is_recording)
                self.assertEqual(parent.schedule.start_at, 8)
                self.assertEqual(child.schedule.start_at, 8)
                self.assertEqual(self.fixture.calls, [
                    ("fire", parent_slot.track.name, parent_slot.row, {"launch_quantization": self.fixture.q_bar}),
                    ("arm", child_slot.track.name, True), ("arm", child_slot.track.name, False)])
                # A prospective first-take length is not yet an accepted
                # reference. No replacement wrap may be guessed before it is.
                before_parent_acceptance = list(self.fixture.calls)
                self.fixture.song.current_song_time = 8.01
                self.backend.tick()
                self.assertEqual(self.fixture.calls, before_parent_acceptance)
                self.assertEqual(child.schedule.start_at, 8)
                self.assertEqual(self.backend._attempts[child.slot].start_target, 8)
                self.assertEqual(self.session.groups[0].accepted, [])
                self.assertNotIn(0, self.backend.faults)
                self.fixture.song.current_song_time = 8.1
                parent_slot.finish_native(4)
                self.backend.tick()
                self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [4])
                self.assertEqual((clip.start_time, clip.loop_end), (8, 4))
                self.assertIs(parent_slot.clip, clip)
                self.assertFalse(parent_slot.track.arm)
                self.assertFalse(child_slot.track.arm)
                self.assertIs(self.session.take, child)
                self.assertEqual((child.state, child.schedule.queued_at, child.schedule.start_at), ("queued", 8, 12))
                self.assertEqual(self.backend._attempts[child.slot].start_target, 12)
                self.assertFalse(self.backend._attempts[child.slot].start_issued)
                self.assertNotIn(0, self.backend.faults)
                child_slot.track.after_arm = None
                self.fixture.song.current_song_time = 9
                self.backend.tick()
                fires = [call for call in self.fixture.calls if call[0] == "fire"]
                self.assertEqual(fires, [
                    ("fire", parent_slot.track.name, parent_slot.row, {"launch_quantization": self.fixture.q_bar}),
                    ("fire", child_slot.track.name, child_slot.row, {"launch_quantization": self.fixture.q_bar})])
                self.assertEqual((clip.start_time, clip.loop_end), (8, 4))
                self.assertFalse(any(call[0] in ("stop", "delete") for call in self.fixture.calls))

    def test_guarded_cleanup_never_disarms_or_deletes_a_foreign_clip_acquired_after_arm(self):
        self.make()
        self.fixture.song.current_song_time = 3.6
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        slot = self.helper.slot(self.session.take.slot)
        foreign = native.Clip(4, recording=False)
        foreign.is_playing = False

        def lost_empty_ownership():
            self.now += .180
            slot.clip = foreign

        slot.track.after_arm = lost_empty_ownership
        self.backend.tick()
        self.assertIs(slot.clip, foreign)
        self.assertTrue(slot.track.arm)
        self.assertEqual(self.fixture.calls, [("arm", slot.track.name, True)])
        self.assertIn(0, self.backend.faults)


if __name__ == "__main__":
    unittest.main()
