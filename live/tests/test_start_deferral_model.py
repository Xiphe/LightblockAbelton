"""Queued start deferral observations at the pure Session boundary."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_session as session_helpers
from LightpadController import protocol as p
from LightpadController.session import Session, Schedule, QUEUED_VIEW, RECORDING_VIEW


class StartDeferralModelTests(unittest.TestCase):
    def setUp(self):
        self.backend = session_helpers.Backend()
        self.session = Session(self.backend)

    def record(self, group=0):
        self.assertEqual(self.session.begin(group), p.OUTCOME_QUEUED)
        take = self.session.take
        self.assertTrue(self.session.observe_started(take.token))
        return take

    def reference(self, group=0):
        current = self.record(group)
        self.assertEqual(self.session.accept(group), p.OUTCOME_QUEUED)
        self.assertTrue(self.session.observe_finished(current.token, 4))
        return self.session.groups[group].reference

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

    def assert_rejected_unchanged(self, token, schedule):
        before, calls = self.state(), list(self.backend.calls)
        self.assertIs(self.session.observe_start_deferred(token, schedule), False)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.backend.calls, calls)

    def test_current_queued_start_moves_later_without_changing_generation_or_request_time(self):
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        current = self.session.take
        original, token = current.schedule, current.token
        calls = list(self.backend.calls)
        later = Schedule(original.queued_at, 8)
        self.assertIs(self.session.observe_start_deferred(token, later), True)
        self.assertIs(self.session.take, current)
        self.assertEqual((current.token, current.schedule, current.state), (token, later, 'queued'))
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertEqual(self.session.recording_group, 0)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertEqual(self.backend.calls, calls)
        self.assertTrue(self.session.observe_started(token))
        self.assertEqual((current.state, self.session.view), ('recording', RECORDING_VIEW))

    def test_queued_child_defers_independently_of_parent_finish_and_preserves_handoff(self):
        parent = self.record()
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        child, finish = parent.next_take, parent.schedule
        original = child.schedule
        calls = list(self.backend.calls)
        later = Schedule(original.queued_at, 12)
        self.assertIs(self.session.observe_start_deferred(child.token, later), True)
        self.assertIs(self.session.take, parent)
        self.assertEqual((parent.schedule, parent.state), (finish, 'finish_queued'))
        self.assertIs(parent.next_take, child)
        self.assertEqual((child.schedule, child.state), (later, 'queued'))
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertEqual(self.backend.calls, calls)
        self.assertTrue(self.session.observe_finished(parent.token, 4))
        accepted = tuple(self.session.groups[0].accepted)
        self.assertIs(self.session.take, child)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertEqual(child.schedule, later)
        self.assertIs(self.session.observe_start_deferred(child.token, Schedule(original.queued_at, 16)), True)
        self.assertEqual(tuple(self.session.groups[0].accepted), accepted)
        self.assert_rejected_unchanged(parent.token, Schedule(finish.queued_at, 20))

    def test_reference_and_other_groups_accepted_layers_survive_start_deferral(self):
        reference = self.reference(0)
        self.reference(1)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        current = self.session.take
        accepted = tuple(tuple(group.accepted) for group in self.session.groups)
        calls = list(self.backend.calls)
        later = Schedule(current.schedule.queued_at, 16)
        self.assertIs(self.session.observe_start_deferred(current.token, later), True)
        self.assertEqual(current.schedule, later)
        self.assertEqual(tuple(tuple(group.accepted) for group in self.session.groups), accepted)
        self.assertIs(self.session.groups[0].reference, reference)
        self.assertEqual(self.backend.calls, calls)

    def test_missing_stale_or_aborted_generation_cannot_change_start_schedule(self):
        self.assert_rejected_unchanged(100, Schedule(3, 8))
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        current = self.session.take
        later = Schedule(current.schedule.queued_at, 8)
        self.assert_rejected_unchanged(current.token + 100, later)
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assert_rejected_unchanged(current.token, later)

    def test_malformed_nonadvancing_or_changed_request_time_is_rejected_without_mutation(self):
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        current = self.session.take
        queued, start = current.schedule
        schedules = [None, object(), (queued, 8), Schedule(queued, start),
                     Schedule(queued, start - .5), Schedule(queued + .1, 8),
                     Schedule(queued, queued - 1), Schedule(True, 8),
                     Schedule(queued, True), Schedule(float('nan'), 8),
                     Schedule(queued, float('nan')), Schedule(queued, float('inf'))]
        for schedule in schedules:
            with self.subTest(schedule=schedule):
                self.assert_rejected_unchanged(current.token, schedule)

    def test_recording_or_finish_queued_parent_cannot_be_mutated_by_start_callback(self):
        current = self.record()
        self.assert_rejected_unchanged(current.token, Schedule(3, 8))
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        finish = current.schedule
        self.assert_rejected_unchanged(current.token, Schedule(finish.queued_at, 12))
        self.assertEqual(current.schedule, finish)

    def test_recording_child_rejects_deferral_before_and_after_parent_completion(self):
        parent = self.record()
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        child, original = parent.next_take, parent.next_take.schedule
        self.assertTrue(self.session.observe_started(child.token))
        later = Schedule(original.queued_at, 12)
        self.assert_rejected_unchanged(child.token, later)
        self.assertTrue(self.session.observe_finished(parent.token, 4))
        self.assertIs(self.session.take, child)
        self.assertEqual(self.session.view, RECORDING_VIEW)
        self.assert_rejected_unchanged(child.token, later)


if __name__ == '__main__':
    unittest.main()
