"""Late Begin/Retry retain their intent until a safe legal native boundary."""

import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_audio_backend as native
import test_quantized_enqueue_deadline as clocks
from LightpadController import protocol as p
from LightpadController import audio_backend as module
from LightpadController.session import QUEUED_VIEW


class RetainedStartTests(unittest.TestCase):
    def make(self, reference=False, recording=False):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()
        self.reference_lease = self.reference_clip = None
        if reference:
            lease, clip = self.helper.record(queued_at=815)
            self.fixture.song.current_song_time = 823
            self.assertEqual(self.session.accept(0, next_take=recording), p.OUTCOME_QUEUED)
            child = self.session.take.next_take
            self.backend.tick()
            self.fixture.song.current_song_time = 824
            self.helper.slot(lease).finish_native(8)
            if child is not None:
                self.helper.slot(child.slot).begin_native(824)
            self.backend.tick()
            self.reference_lease, self.reference_clip = lease, clip
            self.assertEqual((clip.start_time, clip.loop_end), (824, 8))
        self.clock = clocks.EnqueueClock(self.fixture.song.current_song_time)
        self.fixture.native_bar_clock = True
        self.fixture.launch_window = lambda target: clocks.LaunchWindow(target, self.fixture.q_bar, self.clock)
        validate = self.fixture.validate

        def guarded(*args, **kwargs):
            result = validate(*args, **kwargs)
            self.clock.spend(self.clock.guard_costs)
            return result

        self.fixture.validate = guarded
        self.fires = []
        for binding in self.fixture.groups.values():
            for track in binding.layers:
                for slot in track.clip_slots:
                    fire = slot.fire

                    def native_fire(*, launch_quantization, slot=slot, fire=fire):
                        self.assertIs(launch_quantization, self.fixture.q_bar)
                        attempt = max((item for item in self.backend._attempts.values() if item.slot is slot),
                                      key=lambda item: item.lease.generation)
                        self.fires.append({"slot": slot, "target": attempt.start_target,
                            "native_boundary": (math.floor(self.clock.beat / 4) + 1) * 4})
                        return fire(launch_quantization=launch_quantization)

                    slot.fire = native_fire
        clock = patch.object(module, "time", SimpleNamespace(monotonic=self.clock.monotonic), create=True)
        clock.start()
        self.addCleanup(clock.stop)

    def at(self, beat):
        self.fixture.song.current_song_time = beat
        self.clock.seconds += (beat - self.clock.beat) * .5
        self.clock.beat = beat

    def assert_queued(self, take, target):
        self.assertIs(self.session.take, take)
        self.assertEqual((self.session.view, take.state, take.schedule.start_at), (QUEUED_VIEW, "queued", target))
        attempt = self.backend._attempts[take.slot]
        self.assertEqual(attempt.start_target, target)
        self.assertFalse(attempt.start_issued)
        self.assertFalse(self.helper.slot(take.slot).track.arm)
        self.assertFalse(self.helper.slot(take.slot).has_clip)
        self.assertNotIn(0, self.backend.faults)

    def test_late_initial_begin_retains_queued_take_for_next_safe_bar(self):
        self.make()
        self.at(3.992)
        self.clock.guard_costs = [14]
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        self.assert_queued(take, 8)
        self.backend.tick()
        self.assert_queued(take, 8)
        self.assertEqual(self.fires, [])
        self.at(5)
        self.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(8, 8)])

    def test_late_begin_with_existing_reference_keeps_next_safe_reference_wrap(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        self.assert_queued(take, 840)
        self.assertEqual(self.fires, [])
        self.at(833)
        self.backend.tick()
        self.assert_queued(take, 840)
        self.at(837)
        self.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(840, 840)])
        self.assertEqual((self.reference_clip.start_time, self.reference_clip.loop_end), (824, 8))

    def test_safe_start_plan_then_late_execution_replans_both_session_and_backend(self):
        for reference in (False, True):
            with self.subTest(reference=reference):
                self.make(reference=reference)
                self.at(831 if reference else 3)
                self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
                take = self.session.take
                original_token = take.token
                self.assertEqual(take.schedule.start_at, 832 if reference else 4)
                self.at(831.992 if reference else 3.992)
                self.clock.guard_costs = [14]
                self.backend.tick()
                target = 840 if reference else 8
                self.assert_queued(take, target)
                self.assertEqual(take.token, original_token)
                self.assertEqual(self.fires, [])
                self.at(837 if reference else 5)
                self.backend.tick()
                self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(target, target)])

    def test_deadline_after_owned_arm_disarms_empty_track_then_retains_next_safe_target(self):
        for crossing in ("frozen_clock", "public_exact", "public_crossed"):
            with self.subTest(crossing=crossing):
                self.make()
                self.at(3.6)
                self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
                take = self.session.take
                slot = self.helper.slot(take.slot)

                def arm_consumes_lead():
                    if crossing == "frozen_clock":
                        self.clock.advance_ms(180)
                    else:
                        self.at(4.0 if crossing == "public_exact" else 4.01)

                slot.track.after_arm = arm_consumes_lead
                self.fixture.calls.clear()
                self.backend.tick()
                self.assert_queued(take, 8)
                self.assertEqual(self.fires, [])
                self.assertEqual(self.fixture.calls, [("arm", slot.track.name, True), ("arm", slot.track.name, False)])
                slot.track.after_arm = None
                self.at(5)
                self.backend.tick()
                self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(8, 8)])

    def test_retry_cancels_current_recording_immediately_and_retains_safe_reference_replacement(self):
        self.make(reference=True, recording=True)
        old = self.session.take
        old_slot = self.helper.slot(old.slot)
        old_clip = old_slot.clip
        self.at(831.992)
        self.fixture.calls.clear()
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)
        replacement = self.session.take
        self.assertNotEqual(replacement.token, old.token)
        self.assertEqual(replacement.schedule.start_at, 840)
        self.backend.tick()
        self.assertEqual(self.fixture.calls, [("stop", old_slot.track.name, False)])
        self.assertTrue(self.backend.pending_cancel(0))
        self.assertIs(old_slot.clip, old_clip)
        self.assertEqual(self.fires, [])
        old_slot.track.quiet_native()
        self.at(833)
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assert_queued(replacement, 840)
        self.assertIs(self.helper.slot(self.reference_lease).clip, self.reference_clip)
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8])
        self.at(837)
        self.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(840, 840)])

    def test_cancel_deferred_begin_releases_intent_without_any_native_start(self):
        self.make()
        self.at(3.992)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        self.assert_queued(take, 8)
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.backend.tick()
        self.at(9)
        self.backend.tick()
        self.assertIsNone(self.session.take)
        self.assertEqual(self.backend._attempts[take.slot].state, "released")
        self.assertFalse(self.helper.slot(take.slot).track.arm)
        self.assertEqual(self.fires, [])
        self.assertNotIn(0, self.backend.faults)

    def test_retry_of_deferred_unstarted_take_replaces_generation_without_replaying_old_start(self):
        self.make()
        self.at(3.992)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        old = self.session.take
        self.assert_queued(old, 8)
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)
        replacement = self.session.take
        self.assertNotEqual(replacement.token, old.token)
        self.assertEqual(self.backend._attempts[old.slot].state, "released")
        self.assert_queued(replacement, 8)
        self.at(5)
        self.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in self.fires], [(8, 8)])

    def test_ambiguous_native_fire_failure_is_not_reissued_or_treated_as_unstarted(self):
        self.make()
        self.at(3)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        slot = self.helper.slot(take.slot)
        slot.fire_error = RuntimeError("ambiguous native fire failure")
        self.backend.tick()
        self.assertEqual(len(self.fires), 1)
        self.assertTrue(self.backend._attempts[take.slot].start_issued)
        self.assertTrue(slot.track.arm)
        self.assertIn(0, self.backend.faults)
        self.at(5)
        for _ in range(5):
            self.backend.tick()
        self.assertEqual(len(self.fires), 1)
        self.assertEqual(self.backend._attempts[take.slot].start_target, 4)
        self.assertFalse(any(call[0] in ("stop", "delete") or call[0] == "arm" and call[2] is False
                             for call in self.fixture.calls))


if __name__ == "__main__":
    unittest.main()
