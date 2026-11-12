"""Finish replanning must preserve both model projections on guard failures."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_deferred_finish as deferred
from LightpadController import protocol as p


class FinishPlanGuardTests(unittest.TestCase):
    def make(self):
        helper = deferred.DeferredFinishTests()
        helper.make(reference=True)
        self.addCleanup(helper.doCleanups)
        return helper

    def test_newer_queue_time_than_admission_position_cannot_commit_invalid_schedule(self):
        helper = self.make()
        helper.fixture.song.current_song_time = 900.0
        # A queue-time read can be newer than the admission samples if the
        # transport jumps backwards between getters. No native effect is due.
        admission = SimpleNamespace(position=lambda lead: 810.0,
                                    require=lambda target, lead: None)
        attempt = helper.backend._attempts[helper.lease]
        with patch.object(helper.backend, "_admission", return_value=admission):
            outcome = helper.session.accept(0, next_take=True)
        self.assertIn(outcome, (p.NOT_READY, p.OUTCOME_QUEUED))
        if outcome == p.NOT_READY:
            self.assertEqual(attempt.state, "recording")
            self.assertIsNone(attempt.finish_target)
            self.assertIsNone(attempt.next_lease)
            self.assertEqual(helper.current.state, "recording")
            self.assertIsNone(helper.current.schedule)
            self.assertIsNone(helper.current.next_take)
            self.assertFalse(any(a.state == "queued" for a in helper.backend._attempts.values()))
        else:
            parent = helper.current.schedule
            child = helper.current.next_take
            self.assertGreater(parent.start_at, parent.queued_at)
            self.assertGreater(parent.start_at, 900.0)
            self.assertEqual(attempt.finish_target, parent.start_at)
            self.assertEqual(child.schedule.queued_at, parent.start_at)
            self.assertEqual(helper.backend._attempts[child.slot].start_target, child.schedule.start_at)
        self.assertEqual(helper.fires, [])
        self.assertEqual((helper.reference_clip.start_time, helper.reference_clip.loop_end), (824, 8))

    def test_changed_reference_anchor_or_length_refuses_deferral_without_retarget(self):
        for field, changed in (("start_time", 825), ("loop_end", 9)):
            with self.subTest(field=field):
                helper = self.make()
                helper.at(831)
                self.assertEqual(helper.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
                child = helper.current.next_take
                attempt = helper.backend._attempts[helper.lease]
                following = helper.backend._attempts[child.slot]
                old_parent, old_child = helper.current.schedule, child.schedule
                setattr(helper.reference_clip, field, changed)
                helper.at(831.992)
                helper.backend.tick()
                self.assertIn(0, helper.backend.faults)
                self.assertEqual((attempt.finish_target, following.start_target), (832, 832))
                self.assertEqual((helper.current.schedule, child.schedule), (old_parent, old_child))
                self.assertFalse(attempt.finish_issued)
                self.assertFalse(following.start_issued)
                self.assertEqual(helper.fires, [])
                self.assertEqual(getattr(helper.reference_clip, field), changed)
                self.assertEqual([layer.length for layer in helper.session.groups[0].accepted], [8])

    def test_stale_session_observation_refuses_deferral_without_backend_retarget(self):
        helper = self.make()
        helper.at(831)
        self.assertEqual(helper.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        child = helper.current.next_take
        attempt = helper.backend._attempts[helper.lease]
        following = helper.backend._attempts[child.slot]
        old_parent, old_child = helper.current.schedule, child.schedule
        # Exercise the real observer's stale-token check, rather than replacing
        # it with a stub that invents a rejection.
        helper.current.token += 100
        helper.at(831.992)
        helper.backend.tick()
        self.assertIn(0, helper.backend.faults)
        self.assertEqual((attempt.finish_target, following.start_target), (832, 832))
        self.assertEqual((helper.current.schedule, child.schedule), (old_parent, old_child))
        self.assertFalse(attempt.finish_issued)
        self.assertFalse(following.start_issued)
        self.assertEqual(helper.fires, [])
        self.assertEqual((helper.reference_clip.start_time, helper.reference_clip.loop_end), (824, 8))


if __name__ == "__main__":
    unittest.main()
