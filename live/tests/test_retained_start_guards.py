"""Retained starts wait for actual cleanup and validated reference readback."""

import unittest

import test_audio_backend as native
import test_deferred_finish as deferred
import test_retained_start as retained
from LightpadController import protocol as p


class DeferredDisarmTrack(native.Track):
    @native.Track.arm.setter
    def arm(self, value):
        if not value and self.defer_disarm:
            self.calls.append(("arm", self.name, False))
            self.disarm_pending = True
            return
        native.Track.arm.fset(self, value)

    def observe_disarmed(self):
        self._arm = False
        self.disarm_pending = False
        self.defer_disarm = False


class RetainedStartGuardTests(unittest.TestCase):
    def make_pending_disarm(self):
        helper = retained.RetainedStartTests()
        helper.make()
        self.addCleanup(helper.doCleanups)
        helper.at(3.6)
        self.assertEqual(helper.session.begin(0), p.OUTCOME_QUEUED)
        take = helper.session.take
        slot = helper.helper.slot(take.slot)
        track = slot.track
        track.__class__ = DeferredDisarmTrack
        track.defer_disarm, track.disarm_pending = True, False
        track.after_arm = lambda: helper.clock.advance_ms(180)
        original = take.schedule
        helper.backend.tick()
        track.after_arm = None
        attempt = helper.backend._attempts[take.slot]
        self.assertTrue(track.arm)
        self.assertTrue(track.disarm_pending)
        self.assertTrue(attempt.start_cleanup_pending)
        self.assertFalse(attempt.start_issued)
        self.assertEqual((take.schedule, attempt.start_target), (original, original.start_at))
        self.assertEqual(helper.fires, [])
        self.assertNotIn(0, helper.backend.faults)
        return helper, take, track, attempt

    def test_deferred_disarm_readback_keeps_old_schedule_until_actual_unarmed(self):
        helper, take, track, attempt = self.make_pending_disarm()
        schedule = take.schedule
        for beat in (4.1, 4.3, 4.5):
            helper.at(beat)
            helper.backend.tick()
            self.assertEqual(take.schedule, schedule)
            self.assertEqual(attempt.start_target, 4)
            self.assertTrue(attempt.start_cleanup_pending)
            self.assertEqual(helper.fires, [])
        self.assertEqual(sum(call == ("arm", track.name, False) for call in helper.fixture.calls), 1)
        track.observe_disarmed()
        helper.at(5)
        helper.backend.tick()
        helper.assert_queued(take, 8)
        self.assertFalse(attempt.start_cleanup_pending)
        self.assertEqual(helper.fires, [])
        helper.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in helper.fires], [(8, 8)])

    def test_cancel_pending_disarm_does_not_release_owned_armed_track(self):
        helper, take, track, attempt = self.make_pending_disarm()
        self.assertEqual(helper.session.abort(0), p.APPLIED)
        self.assertEqual(attempt.state, "canceling")
        self.assertTrue(helper.backend.pending_cancel(0))
        for beat in (4.1, 4.5):
            helper.at(beat)
            helper.backend.tick()
            self.assertEqual(attempt.state, "canceling")
            self.assertTrue(track.arm)
            self.assertTrue(helper.backend.pending_cancel(0))
            self.assertEqual(helper.fires, [])
        track.observe_disarmed()
        helper.backend.tick()
        self.assertEqual(attempt.state, "released")
        self.assertFalse(helper.backend.pending_cancel(0))
        self.assertIsNone(helper.session.take)
        self.assertEqual(helper.fires, [])
        self.assertNotIn(0, helper.backend.faults)

    def test_retry_pending_disarm_retains_old_cleanup_and_one_replacement(self):
        helper, old, track, attempt = self.make_pending_disarm()
        self.assertEqual(helper.session.retry(0), p.OUTCOME_QUEUED)
        replacement = helper.session.take
        self.assertNotEqual(replacement.token, old.token)
        self.assertEqual(attempt.state, "canceling")
        count = len(helper.backend._attempts)
        for beat in (4.1, 4.5, 5):
            helper.at(beat)
            helper.backend.tick()
            self.assertEqual(attempt.state, "canceling")
            self.assertTrue(track.arm)
            self.assertTrue(helper.backend.pending_cancel(0))
            self.assertIs(helper.session.take, replacement)
            self.assertEqual(len(helper.backend._attempts), count)
            self.assertEqual(helper.fires, [])
        track.observe_disarmed()
        helper.backend.tick()
        self.assertEqual(attempt.state, "released")
        self.assertFalse(helper.backend.pending_cancel(0))
        self.assertEqual(len(helper.fires), 1)
        self.assertEqual(helper.fires[0]["target"], helper.fires[0]["native_boundary"])
        self.assertEqual(len(helper.backend._attempts), count)
        self.assertNotIn(0, helper.backend.faults)

    def test_retry_ancestor_cleanup_can_miss_multiple_wraps_without_duplicate_allocation(self):
        helper = retained.RetainedStartTests()
        helper.make(reference=True, recording=True)
        self.addCleanup(helper.doCleanups)
        old = helper.session.take
        old_slot = helper.helper.slot(old.slot)
        helper.at(831)
        self.assertEqual(helper.session.retry(0), p.OUTCOME_QUEUED)
        replacement = helper.session.take
        count = len(helper.backend._attempts)
        for beat, target in ((833, 840), (841, 848), (842, 848)):
            helper.at(beat)
            helper.backend.tick()
            self.assertEqual(replacement.schedule.start_at, target)
            self.assertEqual(helper.backend._attempts[replacement.slot].start_target, target)
            self.assertEqual(len(helper.backend._attempts), count)
            self.assertTrue(helper.backend.pending_cancel(0))
            self.assertEqual(helper.fires, [])
            self.assertNotIn(0, helper.backend.faults)
        old_slot.track.quiet_native()
        helper.backend.tick()
        self.assertFalse(helper.backend.pending_cancel(0))
        self.assertEqual(helper.fires, [])
        helper.at(845)
        helper.backend.tick()
        self.assertEqual([(event["target"], event["native_boundary"]) for event in helper.fires], [(848, 848)])
        self.assertEqual(len(helper.backend._attempts), count)
        self.assertEqual((helper.reference_clip.start_time, helper.reference_clip.loop_end), (824, 8))

    def test_missed_first_reference_child_waits_for_actual_accepted_base_then_wraps(self):
        helper = deferred.DeferredFinishTests()
        helper.make(reference=False)
        self.addCleanup(helper.doCleanups)
        helper.at(831.992)
        self.assertEqual(helper.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        child = helper.current.next_take
        parent_attempt = helper.backend._attempts[helper.lease]
        child_attempt = helper.backend._attempts[child.slot]
        parent_slot = helper.helper.slot(helper.lease)
        original_fire = parent_slot.fire

        def fire_then_miss_child(**kwargs):
            result = original_fire(**kwargs)
            helper.clock.advance_ms(1600)
            return result

        parent_slot.fire = fire_then_miss_child
        helper.at(833)
        helper.backend.tick()
        self.assertTrue(parent_attempt.finish_issued)
        self.assertFalse(child_attempt.start_issued)
        self.assertEqual((parent_attempt.finish_target, child_attempt.start_target), (836, 836))
        self.assertIsNone(helper.session.groups[0].reference)
        helper.at(837)
        helper.backend.tick()
        self.assertEqual((child.schedule.start_at, child_attempt.start_target), (836, 836))
        self.assertFalse(helper.helper.slot(child.slot).track.arm)
        self.assertEqual(len(helper.fires), 1)
        self.assertNotIn(0, helper.backend.faults)
        parent_slot.finish_native(12)
        helper.backend.tick()
        self.assertIs(helper.session.take, child)
        self.assertEqual(helper.session.groups[0].reference.length, 12)
        self.assertEqual((child.schedule.start_at, child_attempt.start_target), (848, 848))
        self.assertEqual((helper.clip.start_time, helper.clip.loop_end), (836, 12))
        self.assertEqual(len(helper.fires), 1)
        helper.at(845)
        helper.backend.tick()
        self.assertEqual([(event["kind"], event["boundary"]) for event in helper.fires],
                         [("finish", 836), ("start", 848)])
        self.assertNotIn(0, helper.backend.faults)


if __name__ == "__main__":
    unittest.main()
