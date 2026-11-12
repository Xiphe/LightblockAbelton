"""Late finish intents move together to a safe native bar, without anchor drift."""

import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_audio_backend as native
import test_quantized_enqueue_deadline as clocks
from LightpadController import protocol as p
from LightpadController import audio_backend as module


class DeferredFinishTests(unittest.TestCase):
    def make(self, reference=True):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()
        self.reference_lease = self.reference_clip = None
        if reference:
            lease, clip = self.helper.record(queued_at=815)
            self.fixture.song.current_song_time = 823
            self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
            child = self.session.take.next_take
            self.backend.tick()
            self.fixture.song.current_song_time = 824
            self.helper.slot(lease).finish_native(8)
            self.clip = self.helper.slot(child.slot).begin_native(824)
            self.backend.tick()
            self.reference_lease, self.reference_clip = lease, clip
            self.assertEqual((clip.start_time, clip.loop_end), (824, 8))
        else:
            _, self.clip = self.helper.record(queued_at=823)
        self.current = self.session.take
        self.lease = self.current.slot
        self.assertEqual((self.clip.start_time, self.current.state), (824, "recording"))
        self.clock = clocks.EnqueueClock(831)
        self.fixture.native_bar_clock = True
        self.fixture.launch_window = lambda target: clocks.LaunchWindow(target, self.fixture.q_bar, self.clock)
        self.fires = []
        validate = self.fixture.validate

        def guarded(*args, **kwargs):
            result = validate(*args, **kwargs)
            self.clock.spend(self.clock.guard_costs)
            return result

        self.fixture.validate = guarded
        for binding in self.fixture.groups.values():
            for track in binding.layers:
                for slot in track.clip_slots:
                    fire = slot.fire

                    def native_fire(*, launch_quantization, slot=slot, fire=fire):
                        self.assertIs(launch_quantization, self.fixture.q_bar)
                        self.fires.append({"slot": slot, "boundary": (math.floor(self.clock.beat / 4) + 1) * 4,
                                           "beat": self.clock.beat,
                                           "kind": "finish" if slot.has_clip and slot.clip.is_recording else "start"})
                        return fire(launch_quantization=launch_quantization)

                    slot.fire = native_fire
        clock = patch.object(module, "time", SimpleNamespace(monotonic=self.clock.monotonic), create=True)
        clock.start()
        self.addCleanup(clock.stop)

    def at(self, beat):
        self.fixture.song.current_song_time = beat
        self.clock.seconds += (beat - self.clock.beat) * .5
        self.clock.beat = beat

    def assert_plan(self, finish, following=None):
        self.assertIs(self.session.take, self.current)
        self.assertEqual(self.current.schedule.start_at, finish)
        self.assertEqual(self.backend._attempts[self.lease].finish_target, finish)
        if following is not None:
            child = self.current.next_take
            self.assertIsNotNone(child)
            self.assertEqual((child.schedule.queued_at, child.schedule.start_at), (finish, following))
            attempt = self.backend._attempts[child.slot]
            self.assertEqual((attempt.start_target, attempt.finish_parent), (following, self.lease))
        self.assertNotIn(0, self.backend.faults)

    def test_late_accept_next_defers_finish_and_chains_at_existing_reference_wrap(self):
        self.make(reference=True)
        self.at(831.992)
        self.clock.guard_costs = [14]
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assert_plan(836, 840)
        self.assertEqual(self.fires, [])
        self.backend.tick()
        self.assertEqual(self.fires, [], "native fire must wait for the new836 launch window")
        self.at(833)
        self.backend.tick()
        self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("finish", 836)])
        self.assertFalse(self.helper.slot(self.current.next_take.slot).track.arm)
        self.assertEqual((self.reference_clip.start_time, self.reference_clip.loop_end), (824, 8))

    def test_no_reference_deferred_first_finish_defines_actual_new_base_and_coincident_start(self):
        self.make(reference=False)
        self.at(831.992)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assert_plan(836, 836)
        child = self.current.next_take
        self.at(833)
        self.backend.tick()
        self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires],
                         [("finish", 836), ("start", 836)])
        self.at(836)
        self.helper.slot(self.lease).finish_native(12)
        self.helper.slot(child.slot).begin_native(836)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].reference.length, 12)
        self.assertEqual(self.clip.start_time, 836)
        self.assertEqual(self.fixture.next_wrap(self.clip, at=836), 848)
        self.assertIs(self.session.take, child)
        self.assertEqual(child.state, "recording")
        self.assertNotIn(0, self.backend.faults)

    def test_plain_late_accept_preserves_existing_reference_and_accepts_actual_extended_duration(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.assert_plan(836)
        self.assertIsNone(self.current.next_take)
        self.at(833)
        self.backend.tick()
        self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("finish", 836)])
        self.at(836)
        self.helper.slot(self.lease).finish_native(12)
        self.backend.tick()
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8, 12])
        self.assertIsNone(self.session.take)
        self.assertFalse(self.helper.slot(self.lease).track.arm)
        self.assertEqual((self.reference_clip.start_time, self.reference_clip.loop_end), (824, 8))
        self.assertNotIn(0, self.backend.faults)

    def test_safe_plan_then_late_callback_replans_session_and_backend_before_any_fire(self):
        for chained in (False, True):
            with self.subTest(chained=chained):
                self.make(reference=True)
                self.at(831)
                self.assertEqual(self.session.accept(0, next_take=chained), p.OUTCOME_QUEUED)
                self.assert_plan(832, 832 if chained else None)
                token = self.current.token
                child_token = self.current.next_take.token if chained else None
                self.at(831.992)
                self.clock.guard_costs = [14]
                self.backend.tick()
                self.assert_plan(836, 840 if chained else None)
                self.assertEqual(self.current.token, token)
                if chained:
                    self.assertEqual(self.current.next_take.token, child_token)
                self.assertEqual(self.fires, [])
                self.at(833)
                self.backend.tick()
                self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("finish", 836)])

    def test_slow_guard_or_quantization_with_frozen_reported_beat_replans_unissued_finish(self):
        for slow_part in ("validation", "quantization"):
            with self.subTest(slow_part=slow_part):
                self.make(reference=True)
                self.at(831)
                self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
                self.at(831.75)
                costs = self.clock.guard_costs if slow_part == "validation" else self.clock.quantization_costs
                costs.append(140)
                self.backend.tick()
                self.assert_plan(836, 840)
                self.assertEqual(self.fires, [])
                self.assertGreater(self.clock.beat, 832)
                self.at(833)
                self.backend.tick()
                self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("finish", 836)])

    def test_cancel_while_finish_is_deferred_discards_only_unaccepted_material(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assert_plan(836, 840)
        child = self.current.next_take
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.backend.tick()
        self.assertTrue(self.backend.pending_cancel(0))
        self.assertEqual(self.fires, [])
        self.helper.slot(self.lease).track.quiet_native()
        self.at(833)
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertFalse(self.helper.slot(self.lease).has_clip)
        self.assertFalse(self.helper.slot(child.slot).has_clip)
        self.assertFalse(self.helper.slot(child.slot).track.arm)
        self.assertIs(self.helper.slot(self.reference_lease).clip, self.reference_clip)
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8])
        self.assertIsNone(self.session.take)
        self.assertNotIn(0, self.backend.faults)

    def test_retry_while_deferred_cancels_both_old_intents_and_uses_original_reference_wrap(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assert_plan(836, 840)
        abandoned_child = self.current.next_take
        self.at(833)
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)
        replacement = self.session.take
        self.assertNotIn(replacement.token, (self.current.token, abandoned_child.token))
        self.assertEqual(replacement.schedule.start_at, 840)
        self.backend.tick()
        self.assertEqual(self.fires, [])
        self.helper.slot(self.lease).track.quiet_native()
        self.at(834)
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertEqual(self.fires, [])
        self.at(837)
        self.backend.tick()
        self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("start", 840)])
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8])
        self.assertEqual((self.reference_clip.start_time, self.reference_clip.loop_end), (824, 8))
        self.assertNotIn(0, self.backend.faults)

    def test_already_issued_deferred_finish_is_never_replanned_or_fired_twice(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assert_plan(836, 840)
        self.at(833)
        self.backend.tick()
        self.assertTrue(self.backend._attempts[self.lease].finish_issued)
        for _ in range(5):
            self.backend.tick()
        self.at(835.992)
        self.backend.tick()
        self.assert_plan(836, 840)
        self.assertEqual([(event["kind"], event["boundary"]) for event in self.fires], [("finish", 836)])

    def test_deferred_finish_still_rejects_wrong_actual_anchor_without_cleanup_or_acceptance(self):
        self.make(reference=True)
        self.at(831.992)
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.assert_plan(836)
        self.at(833)
        self.backend.tick()
        self.at(840)
        self.helper.slot(self.lease).finish_native(16)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8])
        self.assertEqual(self.backend._attempts[self.lease].finish_target, 836)
        self.assertIs(self.helper.slot(self.lease).clip, self.clip)
        self.assertTrue(self.helper.slot(self.lease).track.arm)
        self.assertFalse(any(call[0] in ("stop", "delete") for call in self.fixture.calls))


if __name__ == "__main__":
    unittest.main()
