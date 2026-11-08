"""Session mutation rules with an explicit fake port, never Live proof."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.session import (
    Session, Schedule, FinishPlan, BackendFailure, OVERVIEW_VIEW, QUEUED_VIEW,
    RECORDING_VIEW, FINISH_VIEW, COMPLETED_VIEW)


class Backend:
    """All enabled bits describe this fake's behavior only."""
    def __init__(self):
        self.enabled = p.CAP_RECORDING | p.CAP_REFERENCE_WRAP | p.CAP_CHAINING | p.CAP_ADVANCE
        self.is_ready = True
        self.capacity = 3
        self.reserved = set()
        self.clips = {}
        self.calls = []
        self.mutes = [False] * 8
        self.playing = set(range(8))
        self.fail_finish = False
        self.fail_cancel = False
        self.finish_at = 8
        self.next_wrap_at = 12

    def ready(self, group):
        return self.is_ready

    def capabilities(self, group):
        return self.enabled

    def reserve(self, group, row, exclude):
        self.calls.append(("reserve", group, row, exclude))
        for layer in range(self.capacity):
            slot = (group, row, layer)
            if slot not in self.reserved and slot not in self.clips and slot not in exclude:
                self.reserved.add(slot)
                return slot
        return None

    def release(self, group, slot):
        self.assert_owned(group, slot)
        self.calls.append(("release", group, slot))
        self.reserved.remove(slot)

    def assert_owned(self, group, slot):
        if slot[0] != group or slot not in self.reserved:
            raise BackendFailure(p.STALE)

    def queue_start(self, group, slot, token, mode, reference):
        self.assert_owned(group, slot)
        self.calls.append(("start", group, slot, token, mode, reference))
        self.clips[slot] = "queued"
        return Schedule(3, self.next_wrap_at if mode == "reference_wrap" else 4)

    def queue_finish(self, group, slot, token, next_slot, next_token, reference):
        self.assert_owned(group, slot)
        if next_slot is not None:
            self.assert_owned(group, next_slot)
        self.calls.append(("finish", group, slot, token, next_slot, next_token, reference))
        if self.fail_finish:
            raise BackendFailure(p.NOT_READY)
        self.clips[slot] = "finish_queued"
        if next_slot is not None:
            self.clips[next_slot] = "queued"
        finish = Schedule(6.5, self.finish_at)
        next_start = None
        if next_slot is not None:
            next_start = Schedule(self.finish_at,
                                  self.next_wrap_at if reference is not None else self.finish_at)
        return FinishPlan(finish, next_start)

    def cancel_attempts(self, group, attempts):
        for slot, token in attempts:
            self.assert_owned(group, slot)
        self.calls.append(("cancel", group, attempts))
        if self.fail_cancel:
            raise BackendFailure(p.STALE)
        for slot, token in attempts:
            self.reserved.remove(slot)
            self.clips.pop(slot, None)

    def row_count(self, group):
        return 6

    def row_empty(self, group, row):
        self.calls.append(("row_empty", group, row))
        return not any(slot[:2] == (group, row) for slot in self.clips)

    def advance(self, group, row):
        if not self.row_empty(group, row):
            raise BackendFailure(p.STALE)
        self.calls.append(("advance", group, row))
        self.playing.discard(group)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.session = Session(self.backend)

    def record(self, group=0):
        self.assertEqual(self.session.begin(group), p.OUTCOME_QUEUED)
        token = self.session.take.token
        self.assertTrue(self.session.observe_started(token))
        return token

    def accept(self, group=0, length=4):
        token = self.record(group)
        self.assertEqual(self.session.accept(group), p.OUTCOME_QUEUED)
        self.assertTrue(self.session.observe_finished(token, length))
        return self.session.groups[group].reference

    def test_default_and_unproven_backend_deny_mutations(self):
        self.assertEqual(Session().begin(0), p.NOT_READY)
        self.backend.enabled = 0
        self.assertEqual(self.session.begin(0), p.NOT_READY)
        self.assertEqual(self.session.advance(0), p.NOT_READY)
        self.assertEqual(self.backend.calls, [])

    def test_queued_accept_guards_and_countdown_use_actual_partial_bar(self):
        self.session.begin(0)
        before = list(self.backend.calls)
        self.assertEqual(self.session.accept(0), p.NOT_READY)
        self.assertEqual(self.session.accept(0, True), p.NOT_READY)
        self.assertEqual(self.backend.calls, before)
        self.assertEqual(self.session.countdown(3), 0)
        self.assertEqual(self.session.countdown(3.5), 63)
        self.assertEqual(self.session.countdown(4), 126)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.session.observe_started(self.session.take.token)
        self.assertEqual(self.session.countdown(4), 127)

    def test_reference_waits_for_actual_manual_finish_and_lengths_are_independent(self):
        reference = self.accept(length=8)
        self.assertEqual(reference.length, 8)
        self.assertEqual(self.session.view, COMPLETED_VIEW)
        token = self.record()
        self.assertEqual(self.backend.calls[-1][-2:], ("reference_wrap", reference))
        self.assertEqual(self.session.groups[0].reference, reference)
        self.session.accept(0)
        self.assertEqual(len(self.session.groups[0].accepted), 1)
        self.assertFalse(self.session.observe_finished(token, 0))
        self.assertTrue(self.session.observe_finished(token, 12))
        token = self.record()
        self.session.accept(0)
        self.session.observe_finished(token, 4)
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [8, 12, 4])
        self.assertEqual(self.session.groups[0].reference, reference)

    def test_wrap_capability_required_without_silent_next_bar_fallback(self):
        self.accept()
        self.backend.enabled &= ~p.CAP_REFERENCE_WRAP
        before = list(self.backend.calls)
        self.assertEqual(self.session.begin(0), p.NOT_READY)
        self.assertEqual(self.backend.calls, before)

    def test_terminal_completion_auto_closes_once_then_receipt_clears_host(self):
        token = self.record()
        active_view = (0, self.session.take_id_for(0))
        self.session.accept(0)
        self.session.observe_finished(token, 4)
        notification = (0, self.session.take_id_for(0))
        # Reference consumer rule: 4 can close the matching open take, never
        # open one. Heartbeats and a reconnect with no active view stay closed.
        exits = 0
        for active in (active_view, None, None):
            if self.session.view_for(0) == COMPLETED_VIEW and active == notification:
                exits += 1
                active = None
            self.assertIsNone(active)
        self.assertEqual(exits, 1)
        before = list(self.backend.calls)
        self.assertEqual(self.session.acknowledge_completion(*notification), p.APPLIED)
        self.assertEqual(self.session.view_for(0), OVERVIEW_VIEW)
        self.assertEqual(self.backend.calls, before)
        self.assertEqual(self.session.acknowledge_completion(*notification), p.STALE)
        self.assertEqual(len(self.session.groups[0].accepted), 1)

    def test_stale_receipt_cannot_close_new_take_or_wrong_group(self):
        self.accept()
        old_id = self.session.take_id_for(0)
        self.assertEqual(self.session.acknowledge_completion(1, old_id), p.STALE)
        self.assertEqual(self.session.acknowledge_completion(0, (old_id + 1) % 128), p.STALE)
        self.record()
        new_take = self.session.take
        self.assertNotEqual(self.session.take_id_for(0), old_id)
        self.assertEqual(self.session.acknowledge_completion(0, old_id), p.STALE)
        self.assertIs(self.session.take, new_take)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_failed_begin_preserves_terminal_notification_and_wrap_id_zero_is_valid(self):
        self.session._generation = 127
        self.accept()
        self.assertEqual(self.session.take_id_for(0), 0)
        self.backend.capacity = 0
        self.assertEqual(self.session.begin(0), p.CAPACITY)
        self.assertEqual(self.session.view_for(0), COMPLETED_VIEW)
        self.assertEqual(self.session.acknowledge_completion(0, 0), p.APPLIED)

    def test_accept_next_never_emits_terminal_completion_for_previous_take(self):
        old = self.record()
        old_id = self.session.take_id_for(0)
        self.session.accept(0, True)
        following = self.session.take.next_take.token
        self.session.observe_finished(old, 4)
        self.assertEqual(self.session.view_for(0), QUEUED_VIEW)
        self.assertEqual(self.session.take_id_for(0), following % 128)
        self.assertEqual(self.session.acknowledge_completion(0, old_id), p.STALE)
        self.session.observe_started(following)
        self.assertEqual(self.session.view_for(0), RECORDING_VIEW)

    def test_chain_reserves_first_and_waits_for_observed_completion(self):
        token = self.record()
        self.assertEqual(self.session.accept(0, True), p.OUTCOME_QUEUED)
        self.assertEqual([call[0] for call in self.backend.calls[-2:]], ["reserve", "finish"])
        current, following = self.session.take, self.session.take.next_take
        self.assertNotEqual(current.slot, following.slot)
        self.assertEqual(current.schedule.start_at, following.schedule.start_at)
        self.assertEqual(following.schedule.queued_at, current.schedule.start_at)
        self.assertEqual(self.session.view, FINISH_VIEW)
        # Live can deliver next-recording callback before old-finished callback.
        self.assertTrue(self.session.observe_started(following.token))
        self.assertEqual(self.session.view, FINISH_VIEW)
        self.assertTrue(self.session.observe_finished(token, 4))
        self.assertEqual(self.session.take, following)
        self.assertEqual(self.session.view, RECORDING_VIEW)
        self.assertEqual(self.session.groups[0].reference.slot, current.slot)

    def test_chain_capacity_or_queue_failure_does_not_finish_current_take(self):
        self.backend.capacity = 1
        self.record()
        self.assertEqual(self.session.accept(0, True), p.CAPACITY)
        self.assertEqual(self.session.take.state, "recording")
        self.assertFalse(any(call[0] == "finish" for call in self.backend.calls))
        self.backend.capacity = 2
        self.backend.fail_finish = True
        self.assertEqual(self.session.accept(0, True), p.NOT_READY)
        self.assertEqual(self.session.take.state, "recording")
        self.assertEqual(len(self.backend.reserved), 1)

    def test_retry_cancels_immediately_then_waits_for_reference_wrap_preserving_layers_and_mute(self):
        reference = self.accept()
        self.backend.mutes[0] = True
        old = self.record()
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)
        self.assertEqual([call[0] for call in self.backend.calls[-3:]], ["cancel", "reserve", "start"])
        self.assertEqual(self.backend.calls[-1][-2:], ("reference_wrap", reference))
        self.assertEqual(self.session.take.schedule.start_at, 12)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertNotEqual(self.session.take.token, old)
        self.assertFalse(self.session.observe_started(old))
        self.assertFalse(self.session.observe_finished(old, 4))
        self.assertEqual(self.session.groups[0].accepted, [reference])
        self.assertTrue(self.backend.mutes[0])

    def test_retry_before_first_start_and_abort_do_not_create_reference(self):
        self.session.begin(0)
        old = self.session.take.token
        self.session.retry(0)
        self.assertEqual(self.backend.calls[-1][-2:], ("next_bar", None))
        self.assertEqual(self.session.take.schedule.start_at, 4)
        self.assertFalse(self.session.observe_started(old))
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assertIsNone(self.session.take)
        self.assertIsNone(self.session.groups[0].reference)
        self.assertEqual(self.session.view, OVERVIEW_VIEW)
        self.assertEqual(self.backend.clips, {})

    def test_accept_next_finishes_at_next_bar_then_waits_for_later_reference_wrap(self):
        reference = self.accept(length=12)
        old_token = self.record()
        self.assertEqual(self.session.accept(0, True), p.OUTCOME_QUEUED)
        current, following = self.session.take, self.session.take.next_take
        self.assertEqual(current.schedule, Schedule(6.5, 8))
        self.assertEqual(following.schedule, Schedule(8, 12))
        self.assertIs(self.backend.calls[-1][-1], reference)
        self.assertEqual(self.session.view, FINISH_VIEW)
        self.assertTrue(self.session.observe_finished(old_token, 4))
        self.assertEqual(len(self.session.groups[0].accepted), 2)
        self.assertIs(self.session.groups[0].reference, reference)
        self.assertIs(self.session.take, following)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertEqual(self.session.countdown(8), 0)
        self.assertEqual(self.session.countdown(10), 63)
        self.assertEqual(self.session.countdown(12), 126)
        self.assertNotEqual(self.session.view, COMPLETED_VIEW)
        self.assertTrue(self.session.observe_started(following.token))
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_accept_next_coincident_finish_and_reference_wrap_has_no_extra_cycle(self):
        self.accept(length=8)
        self.backend.next_wrap_at = 8
        old_token = self.record()
        self.session.accept(0, True)
        following = self.session.take.next_take
        self.assertEqual(following.schedule, Schedule(8, 8))
        self.session.observe_finished(old_token, 4)
        self.assertEqual(self.session.countdown(8), 126)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.session.observe_started(following.token)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_first_accept_next_uses_its_own_finish_as_first_reference_wrap(self):
        self.backend.enabled &= ~p.CAP_REFERENCE_WRAP
        old_token = self.record()
        self.assertEqual(self.session.accept(0, True), p.OUTCOME_QUEUED)
        following = self.session.take.next_take
        self.assertEqual(following.schedule, Schedule(8, 8))
        self.assertIsNone(self.backend.calls[-1][-1])
        self.session.observe_finished(old_token, 4)
        self.assertEqual(self.session.groups[0].reference.length, 4)
        self.assertIs(self.session.take, following)

    def test_missing_wrap_refuses_retry_before_discard_and_accept_next_before_reserve(self):
        self.accept()
        self.record()
        current = self.session.take
        self.backend.enabled &= ~p.CAP_REFERENCE_WRAP
        before = list(self.backend.calls)
        self.assertEqual(self.session.retry(0), p.NOT_READY)
        self.assertEqual(self.session.accept(0, True), p.NOT_READY)
        self.assertEqual(self.backend.calls, before)
        self.assertIs(self.session.take, current)
        self.assertEqual(current.state, "recording")
        # Plain Accept still ends at the next bar and needs no wrap scheduler.
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.assertEqual(current.schedule, Schedule(6.5, 8))
        self.assertIsNone(current.next_take)

    def test_abort_next_take_waiting_for_wrap_preserves_just_accepted_layer(self):
        reference = self.accept(length=12)
        old_token = self.record()
        current = self.session.take
        self.session.accept(0, True)
        following = current.next_take
        self.session.observe_finished(old_token, 4)
        accepted_slots = {reference.slot, current.slot}
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assertEqual(set(self.backend.clips), accepted_slots)
        self.assertFalse(self.session.observe_started(following.token))
        self.assertEqual(len(self.session.groups[0].accepted), 2)
        self.assertEqual(self.session.view, OVERVIEW_VIEW)

    def test_finish_plan_requires_next_start_at_or_after_finish_with_matching_anchor(self):
        finish = Schedule(6.5, 8)
        reference = object()
        invalid = [(FinishPlan(finish, None), True, reference),
                   (FinishPlan(finish, Schedule(7, 12)), True, reference),
                   (FinishPlan(finish, Schedule(8, 7)), True, reference),
                   (FinishPlan(finish, Schedule(8, 12)), True, None),
                   (FinishPlan(finish, Schedule(8, 8)), False, reference)]
        for plan, has_next, prior_reference in invalid:
            with self.subTest(plan=plan, has_next=has_next, reference=prior_reference):
                with self.assertRaises(BackendFailure):
                    self.session._finish_plan(plan, has_next, prior_reference)

    def test_abort_chained_take_cancels_both_attempts_preserving_accepted(self):
        reference = self.accept()
        old = self.record()
        self.session.accept(0, True)
        following = self.session.take.next_take.token
        self.backend.is_ready = False
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assertFalse(self.session.observe_started(following))
        self.assertFalse(self.session.observe_finished(old, 4))
        self.assertEqual(set(self.backend.clips), {reference.slot})

    def test_abort_waits_for_old_finish_when_next_start_observation_arrives_first(self):
        old = self.record()
        self.session.accept(0, True)
        following = self.session.take.next_take.token
        self.session.observe_started(following)
        before = list(self.backend.calls)
        self.assertEqual(self.session.abort(0), p.BUSY)
        self.assertEqual(self.backend.calls, before)
        self.session.observe_finished(old, 4)
        accepted = self.session.groups[0].reference
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assertEqual(set(self.backend.clips), {accepted.slot})

    def test_retry_capacity_failure_keeps_controls_open_without_inventing_a_schedule(self):
        self.record()
        self.backend.capacity = 0
        self.assertEqual(self.session.retry(0), p.CAPACITY)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertIsNone(self.session.take)
        self.assertEqual(self.session.countdown(100), 127)
        self.assertEqual(self.session.accept(0), p.NOT_READY)
        self.backend.capacity = 1
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)

    def test_removed_slot_refuses_start_without_forcing_cleanup_on_an_unowned_track(self):
        def removed(group, slot, token, mode, reference):
            self.backend.reserved.remove(slot)
            raise BackendFailure(p.STALE)
        self.backend.queue_start = removed
        self.assertEqual(self.session.begin(0), p.STALE)
        self.assertIsNone(self.session.take)
        self.assertEqual(self.backend.clips, {})

    def test_backend_cancel_failure_preserves_model_for_recovery(self):
        self.record()
        current = self.session.take
        self.backend.fail_cancel = True
        self.assertEqual(self.session.abort(0), p.STALE)
        self.assertIs(self.session.take, current)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_one_modal_take_and_group_only_advance_skip_all_occupied_rows(self):
        reference = self.accept(2)
        self.backend.clips[(2, 1, 99)] = "external layer on owned track"
        self.backend.clips[(2, 2, 0)] = "earlier clip"
        self.backend.clips[(3, 3, 0)] = "other group's clip"
        before = dict(self.backend.clips)
        self.assertEqual(self.session.advance(2), p.APPLIED)
        self.assertEqual(self.session.groups[2].row, 3)
        self.assertEqual(self.session.groups[2].accepted, [])
        self.assertEqual(self.session.groups[2].phrases[0], [reference])
        self.assertEqual(self.backend.clips, before)
        self.assertEqual(self.backend.playing, set(range(8)) - {2})
        self.session.begin(0)
        self.assertEqual(self.session.begin(1), p.BUSY)
        self.assertEqual(self.session.advance(1), p.BUSY)


if __name__ == "__main__":
    unittest.main()
