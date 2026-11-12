"""Deferred finish observations at the Session boundary, without native effects."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_session as session_helpers
from LightpadController import protocol as p
from LightpadController.session import Session, Schedule, FinishPlan, FINISH_VIEW, COMPLETED_VIEW


class FinishDeferralModelTests(unittest.TestCase):
    def setUp(self):
        self.backend = session_helpers.Backend()
        self.session = Session(self.backend)

    def record(self, group=0):
        self.assertEqual(self.session.begin(group), p.OUTCOME_QUEUED)
        take = self.session.take
        self.assertTrue(self.session.observe_started(take.token))
        return take

    def reference(self, group=0):
        take = self.record(group)
        self.assertEqual(self.session.accept(group), p.OUTCOME_QUEUED)
        self.assertTrue(self.session.observe_finished(take.token, 4))
        return self.session.groups[group].reference

    def finish(self, next_take=False, reference=False):
        if reference:
            self.reference()
            self.backend.finish_at, self.backend.next_wrap_at = 16, 20
        take = self.record()
        self.assertEqual(self.session.accept(0, next_take=next_take), p.OUTCOME_QUEUED)
        return take

    def state(self):
        def take_state(take):
            return None if take is None else (take.group, take.slot, take.token,
                take.state, take.schedule, take_state(take.next_take))
        return (take_state(self.session.take), self.session.recording_group, self.session.view,
                self.session.view_group,
                tuple((group.row, tuple((row, tuple(layers)) for row, layers in group.phrases.items()))
                      for group in self.session.groups),
                tuple((self.session.view_for(group), self.session.take_id_for(group))
                      for group in range(8)))

    def assert_rejected_unchanged(self, token, plan):
        before, calls = self.state(), list(self.backend.calls)
        self.assertIs(self.session.observe_finish_deferred(token, plan), False)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.backend.calls, calls)

    def test_plain_accept_deferral_preserves_request_time_until_actual_finish(self):
        current = self.finish()
        original = current.schedule
        plan = FinishPlan(Schedule(original.queued_at, 12), None)
        calls = list(self.backend.calls)
        self.assertIs(self.session.observe_finish_deferred(current.token, plan), True)
        self.assertIs(self.session.take, current)
        self.assertEqual(current.schedule, plan.finish)
        self.assertEqual(current.state, 'finish_queued')
        self.assertEqual(self.session.view, FINISH_VIEW)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertEqual(self.backend.calls, calls)
        self.assertTrue(self.session.observe_finished(current.token, 8))
        self.assertEqual(self.session.groups[0].accepted[0].length, 8)
        self.assertEqual(self.session.view, COMPLETED_VIEW)

    def test_first_accept_next_moves_queued_child_to_same_new_finish_boundary(self):
        current = self.finish(next_take=True)
        following = current.next_take
        original = current.schedule
        calls = list(self.backend.calls)
        plan = FinishPlan(Schedule(original.queued_at, 12), Schedule(12, 12))
        self.assertIs(self.session.observe_finish_deferred(current.token, plan), True)
        self.assertIs(self.session.take, current)
        self.assertIs(current.next_take, following)
        self.assertEqual(current.schedule, plan.finish)
        self.assertEqual(following.schedule, plan.next_start)
        self.assertEqual((current.state, following.state), ('finish_queued', 'queued'))
        self.assertEqual(self.session.view, FINISH_VIEW)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertEqual(self.backend.calls, calls)

    def test_reference_accept_next_uses_supplied_later_wrap_without_mutating_accepted_layers(self):
        self.reference(group=1)
        current = self.finish(next_take=True, reference=True)
        following = current.next_take
        accepted = [tuple(group.accepted) for group in self.session.groups]
        calls = list(self.backend.calls)
        plan = FinishPlan(Schedule(current.schedule.queued_at, 20), Schedule(20, 24))
        self.assertIs(self.session.observe_finish_deferred(current.token, plan), True)
        self.assertEqual((current.schedule, following.schedule), (plan.finish, plan.next_start))
        self.assertEqual([tuple(group.accepted) for group in self.session.groups], accepted)
        self.assertEqual(self.backend.calls, calls)
        coincident = FinishPlan(Schedule(current.schedule.queued_at, 24), Schedule(24, 24))
        self.assertIs(self.session.observe_finish_deferred(current.token, coincident), True)
        self.assertEqual(following.schedule, Schedule(24, 24))
        self.assertEqual([tuple(group.accepted) for group in self.session.groups], accepted)
        self.assert_rejected_unchanged(current.token, coincident)

    def test_stale_or_child_token_cannot_replan_current_finish(self):
        current = self.finish(next_take=True)
        plan = FinishPlan(Schedule(current.schedule.queued_at, 12), Schedule(12, 12))
        self.assert_rejected_unchanged(current.token + 100, plan)
        self.assert_rejected_unchanged(current.next_take.token, plan)
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assert_rejected_unchanged(current.token, plan)

    def test_queued_recording_or_completed_take_cannot_receive_finish_deferral(self):
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        current = self.session.take
        plan = FinishPlan(Schedule(current.schedule.queued_at, 12), None)
        self.assert_rejected_unchanged(current.token, plan)
        self.assertTrue(self.session.observe_started(current.token))
        self.assert_rejected_unchanged(current.token, plan)
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.assertTrue(self.session.observe_finished(current.token, 4))
        self.assert_rejected_unchanged(current.token, plan)

    def test_started_following_take_blocks_replan_and_preserves_reverse_callback_order(self):
        current = self.finish(next_take=True)
        following = current.next_take
        original_queue = current.schedule.queued_at
        self.assertTrue(self.session.observe_started(following.token))
        self.assert_rejected_unchanged(current.token,
                                      FinishPlan(Schedule(original_queue, 12), Schedule(12, 12)))
        self.assertTrue(self.session.observe_finished(current.token, 4))
        self.assertIs(self.session.take, following)
        self.assertEqual(following.state, 'recording')
        self.assertEqual(len(self.session.groups[0].accepted), 1)

    def test_malformed_or_nonadvancing_plain_plans_are_rejected_without_mutation(self):
        self.reference(group=1)
        current = self.finish()
        queued = current.schedule.queued_at
        plans = [None, object(), (Schedule(queued, 12), None),
                 FinishPlan((queued, 12), None),
                 FinishPlan(Schedule(float('nan'), 12), None),
                 FinishPlan(Schedule(True, 12), None),
                 FinishPlan(Schedule(queued, float('nan')), None),
                 FinishPlan(Schedule(queued, float('inf')), None),
                 FinishPlan(Schedule(13, 12), None),
                 FinishPlan(Schedule(queued + .1, 12), None),
                 FinishPlan(Schedule(queued, 8), None),
                 FinishPlan(Schedule(queued, 7), None),
                 FinishPlan(Schedule(queued, 12), Schedule(12, 12))]
        for plan in plans:
            with self.subTest(plan=plan):
                self.assert_rejected_unchanged(current.token, plan)

    def test_malformed_child_plan_cannot_partially_move_parent_finish(self):
        current = self.finish(next_take=True)
        finish = Schedule(current.schedule.queued_at, 12)
        for following in (None, (12, 12), Schedule(8, 12), Schedule(12, 11),
                          Schedule(12, 16), Schedule(12, float('nan'))):
            with self.subTest(following=following):
                self.assert_rejected_unchanged(current.token, FinishPlan(finish, following))


if __name__ == '__main__':
    unittest.main()
