"""Shared gesture engine through an authoritative port; role admission has separate tests."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.device_ui_legacy import DeviceUI


class Host:
    def __init__(self):
        self.now = 100.0
        self.fields = []
        for _ in range(8):
            fields = dict.fromkeys(range(91, 109), 0)
            fields.update({91: p.PLAYING, 98: 64, 99: 64, 100: 64,
                           101: 64, 102: 64, 103: 31, 108: 64})
            self.fields.append(fields)
        self.meter = (4, 4, False)
        self.busy = set()
        self.sent = []
        self.callbacks = []
        self.sync = False
        self.sync_effect = None
        self.ui = DeviceUI(self.send, lambda group: self.fields[group],
                           lambda: self.meter, lambda: self.now)

    def send(self, group, op, arg, callback):
        if group in self.busy:
            return False
        self.busy.add(group)
        self.sent.append((group, op, arg))
        self.callbacks.append((group, op, arg, callback))
        if self.sync:
            self.finish(effect=self.sync_effect)
        return True

    def finish(self, index=0, outcome=p.APPLIED, effect=None):
        group, op, arg, callback = self.callbacks.pop(index)
        self.busy.remove(group)
        if effect is not None:
            effect(group, op, arg)
        callback(outcome)

    def touch(self, kind, finger=0, x=1.5, y=1.5, after=0):
        self.now += after
        self.ui.touch(kind, finger, x, y)

    def tap(self, finger=0, x=1.5, y=1.5):
        self.touch(1, finger, x, y)
        self.touch(3, finger, x, y, .05)

    def down(self, finger=0, x=1.5, y=1.5):
        self.touch(1, finger, x, y)
        self.touch(2, finger, x, y + 2.5, .05)
        self.touch(3, finger, x, y + 2.5, .05)

    def hold(self, x=1.5, y=1.5):
        self.touch(1, 0, x, y)
        self.now += .5
        self.ui.tick()

    def begin(self):
        self.fields[0][91] = p.EMPTY
        self.down()
        self.finish(effect=lambda group, op, arg: self.fields[group].update({106: 2, 107: 7}))
        self.assert_recording()

    def assert_recording(self):
        assert (self.ui.view, self.ui.active_take, self.ui.begin_wait) == (2, 7, False)


class DeviceUITests(unittest.TestCase):
    def test_populated_tap_uses_actual_mute_and_never_updates_it_locally(self):
        host = Host()
        host.tap()
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 127)])
        self.assertEqual(host.fields[0][92], 0)
        host.finish(outcome=p.REJECTED)
        self.assertEqual(host.fields[0][92], 0)
        host.fields[0][92] = 127
        host.tap()
        self.assertEqual(host.sent[-1], (0, p.SET_MUTE, 0))

    def test_empty_tap_mutes_actual_state_without_starting_a_take(self):
        for mute in (0, 127):
            with self.subTest(mute=mute):
                host = Host()
                host.fields[0].update({91: p.EMPTY, 92: mute})
                host.touch(1)
                self.assertEqual(host.sent, [])
                host.touch(3)
                wanted = 0 if mute else 127
                self.assertEqual(host.sent, [(0, p.SET_MUTE, wanted)])
                self.assertEqual((host.ui.view, host.ui.begin_wait), (0, False))
                self.assertEqual((host.fields[0][91], host.fields[0][92]), (p.EMPTY, mute))
                host.finish(effect=lambda group, op, arg: host.fields[group].update({92: arg}))
                self.assertEqual((host.fields[0][91], host.fields[0][92]), (p.EMPTY, wanted))

    def test_straight_down_from_empty_or_populated_queues_only_after_release(self):
        for state in (p.EMPTY, p.PLAYING):
            with self.subTest(state=state):
                host = Host()
                host.fields[0][91] = state
                host.touch(1)
                host.touch(2, y=4)
                self.assertEqual(host.sent, [])
                host.touch(3, y=4)
                self.assertEqual(host.sent, [(0, p.BEGIN, 0)])
                self.assertEqual(host.ui.presentation(), (2, 0, 0, 0, 1, 0, 0))
                host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 23}))
                self.assertEqual((host.ui.active_take, host.ui.begin_wait), (23, False))

    def test_other_overview_paths_neither_record_nor_fall_back_to_tap(self):
        for state in (p.EMPTY, p.PLAYING):
            for x, y in ((1.5, 3), (1.5, 0), (4, 1.5), (4, 4)):
                with self.subTest(state=state, end=(x, y)):
                    host = Host()
                    host.fields[0][91] = state
                    host.touch(1)
                    host.touch(2, x=x, y=y)
                    host.touch(3, x=x, y=y)
                    self.assertEqual(host.sent, [])
                    self.assertEqual(host.ui.view, 0)

    def test_turned_path_advances_populated_and_ignores_empty_without_begin(self):
        for state in (p.EMPTY, p.PLAYING):
            host = Host()
            host.fields[0][91] = state
            host.touch(1)
            host.touch(2, y=4)
            host.touch(2, x=4, y=4)
            host.touch(3, x=4, y=4)
            self.assertEqual(host.sent, [(0, p.ADVANCE, 0)] if state == p.PLAYING else [])

    def test_busy_discrete_gesture_is_never_replayed_later(self):
        host = Host()
        host.busy.add(0)
        host.fields[0][91] = p.EMPTY
        host.tap()
        self.assertEqual(host.ui.view, 0)
        host.busy.clear()
        host.ui.tick()
        self.assertEqual(host.sent, [])

    def test_group_chord_xor_retouch_last_participant_release_and_no_hold(self):
        host = Host()
        host.fields[1][92] = 127
        host.fields[4][91] = p.EMPTY
        host.touch(1, 0)
        host.touch(1, 1, 5.5)
        host.touch(1, 2, 5.5)  # Remove group1 from pending membership.
        self.assertEqual(host.ui.chord_mask, 1)
        host.touch(3, 2, 5.5)
        host.touch(1, 3, 5.5)  # Re-add it.
        host.touch(1, 4, 1.5, 5.5)  # Empty participation joins mute, never recording.
        host.now += .6
        host.ui.tick()
        self.assertEqual(host.ui.view, 0)
        self.assertEqual(host.sent, [])
        for finger, x, y in ((1, 5.5, 1.5), (0, 1.5, 1.5), (3, 5.5, 1.5)):
            host.touch(3, finger, x, y)
            self.assertEqual(host.sent, [])
        host.touch(3, 4, 1.5, 5.5)
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 127), (1, p.SET_MUTE, 0), (4, p.SET_MUTE, 127)])
        self.assertEqual(host.ui.chord_mask, 0)

    def test_empty_chord_xor_membership_commits_actual_mutes_on_last_release(self):
        host = Host()
        host.fields[0].update({91: p.EMPTY, 92: 127})
        host.fields[7].update({91: p.EMPTY, 92: 0})
        host.touch(1, 0)
        host.touch(1, 15, 13.5, 5.5)
        self.assertEqual(host.ui.presentation()[5:], (1, 1))
        host.touch(1, 1, 13.5, 5.5)
        self.assertEqual(host.ui.presentation()[5:], (1, 0))
        host.touch(3, 1, 13.5, 5.5)
        host.touch(1, 2, 13.5, 5.5)
        self.assertEqual(host.ui.presentation()[5:], (1, 1))
        host.touch(3, 0)
        host.touch(3, 15, 13.5, 5.5)
        self.assertEqual(host.sent, [])
        host.touch(3, 2, 13.5, 5.5)
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 0), (7, p.SET_MUTE, 127)])
        self.assertEqual(host.ui.presentation()[5:], (0, 0))
        self.assertEqual(host.ui.view, 0)

    def test_down_and_l_preview_are_mutually_exclusive_and_never_send_before_end(self):
        for state in (p.EMPTY, p.PLAYING):
            with self.subTest(state=state):
                host = Host()
                host.fields[0][91] = state
                host.touch(1)
                host.touch(2, y=4)
                self.assertEqual(host.ui.presentation()[4], p.RECORD_PREVIEW)
                self.assertEqual(host.ui.presentation()[1], 0)
                self.assertEqual(host.sent, [])
                host.touch(2, x=4, y=4)
                self.assertEqual(host.ui.presentation()[4], p.ADVANCE_PREVIEW)
                self.assertEqual(host.sent, [])
                host.touch(3, x=4, y=4)
                self.assertEqual(host.ui.presentation()[4], 0)
                self.assertEqual(host.sent, [] if state == p.EMPTY else [(0, p.ADVANCE, 0)])

    def test_same_contact_reverses_l_to_i_then_origin_cancel_then_down_rearm(self):
        for state in (p.EMPTY, p.PLAYING):
            with self.subTest(state=state):
                host = Host()
                host.fields[0][91] = state
                host.touch(1)
                flags = []
                for x, y in ((1.5, 4), (4, 4), (1.5, 4), (1.5, 1.5), (1.5, 4)):
                    host.touch(2, x=x, y=y)
                    flags.append(host.ui.presentation()[4])
                    self.assertEqual(host.sent, [], 'held path changes must never dispatch Native intents')
                self.assertEqual(flags, [p.RECORD_PREVIEW, p.ADVANCE_PREVIEW, p.RECORD_PREVIEW, 0, p.RECORD_PREVIEW])
                host.touch(3, y=4)
                self.assertEqual(host.sent, [(0, p.BEGIN, 0)])

    def test_origin_tile_return_cancels_release_without_muting_even_from_top_edge(self):
        for state in (p.EMPTY, p.PLAYING):
            for turned in (False, True):
                for start_y in (.1, 1.5):
                    for end_only in (False, True):
                        with self.subTest(state=state, turned=turned, start_y=start_y, end_only=end_only):
                            host = Host()
                            host.fields[0][91] = state
                            host.touch(1, y=start_y)
                            host.touch(2, y=4)
                            if turned:
                                host.touch(2, x=4, y=4)
                            if not end_only:
                                host.touch(2, y=2.5)
                                self.assertEqual(host.ui.presentation()[4], 0)
                            host.touch(3, y=2.5)
                            self.assertEqual(host.sent, [], 'drag-return cannot become a tap, recording, or Advance')
                            self.assertEqual((host.ui.view, host.ui.presentation()[4]), (0, 0))

    def test_returning_to_vertical_corridor_changes_release_action_from_advance_to_record(self):
        host = Host()
        host.touch(1)
        host.touch(2, y=4)
        host.touch(2, x=4, y=4)
        host.touch(2, x=3, y=4)  # Inside the2px corridor from initial elbowx1.5.
        self.assertEqual(host.ui.presentation()[4], p.RECORD_PREVIEW)
        host.touch(3, x=3, y=4)
        self.assertEqual(host.sent, [(0, p.BEGIN, 0)])

    def test_retreat_below_down_threshold_clears_intent_and_can_rearm(self):
        host = Host()
        host.touch(1)
        host.touch(2, y=4)
        host.touch(2, x=4, y=4)
        host.touch(2, x=4, y=3)  # Outside origin tile, but no longer far enough down.
        self.assertEqual(host.ui.presentation()[4], 0)
        self.assertEqual(host.sent, [])
        host.touch(2, y=4)
        self.assertEqual(host.ui.presentation()[4], p.RECORD_PREVIEW)
        host.touch(3, y=4)
        self.assertEqual(host.sent, [(0, p.BEGIN, 0)])

    def test_chord_membership_does_not_turn_into_reversible_record_path(self):
        host = Host()
        host.fields[0][91] = p.EMPTY
        host.fields[7][91] = p.EMPTY
        host.touch(1)
        host.touch(2, y=4)
        host.touch(2, x=4, y=4)
        host.touch(1, 15, 13.5, 5.5)
        for x, y in ((1.5, 4), (1.5, 1.5), (1.5, 4)):
            host.touch(2, x=x, y=y)
            self.assertEqual(host.ui.presentation()[4], 0)
            self.assertEqual(host.ui.chord_mask, 129)
        host.touch(3, y=4)
        self.assertEqual(host.sent, [])
        host.touch(3, 15, 13.5, 5.5)
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 127), (7, p.SET_MUTE, 127)])

    def test_preview_clears_on_chord_lease_reset_unavailability_and_held_views(self):
        for stop in ('chord', 'lease', 'reset', 'unavailable'):
            with self.subTest(stop=stop):
                host = Host()
                host.touch(1)
                host.touch(2, y=4)
                self.assertEqual(host.ui.presentation()[4], p.RECORD_PREVIEW)
                if stop == 'chord':
                    host.touch(1, 15, 5.5)
                elif stop == 'lease':
                    host.now += 1
                    host.ui.tick()
                elif stop == 'reset':
                    host.ui.reset()
                else:
                    host.fields[0][91] = p.UNAVAILABLE
                    host.ui.tick()
                self.assertEqual(host.ui.presentation()[4] & 12, 0)
                self.assertEqual(host.sent, [])
        for x, y, view in ((1.5, 1.5, 1), (7.5, 14.5, 3)):
            host = Host()
            host.hold(x=x, y=y)
            self.assertEqual((host.ui.view, host.ui.presentation()[4] & 12), (view, 0))

    def test_single_hold_threshold_and_orphan_secondary_mixer_touch(self):
        host = Host()
        host.touch(1)
        host.now += .499
        host.ui.tick()
        self.assertEqual(host.ui.view, 0)
        host.now += .001
        host.ui.tick()
        self.assertEqual(host.ui.view, 1)
        host.touch(1, 7, .5, 12.5)
        self.assertEqual(host.sent, [])
        host.touch(3)
        self.assertEqual(host.ui.view, 0)
        host.touch(2, 7, .5, 10.5)
        host.touch(3, 7, .5, 10.5)
        self.assertEqual(host.sent, [])
        host.tap(7)
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 127)])

    def test_all_four_assignments_volume_gutters_and_horizontal_pan(self):
        columns = {0: p.SET_VOLUME, 1: p.SET_VOLUME, 2: p.SET_VOLUME,
                   4: p.SET_SEND_1, 5: p.SET_SEND_1, 7: p.SET_SEND_2, 8: p.SET_SEND_2,
                   10: p.SET_SEND_3, 11: p.SET_SEND_3, 13: p.SET_CONTROL_4, 14: p.SET_CONTROL_4}
        for column in range(15):
            with self.subTest(column=column):
                host = Host()
                host.hold()
                host.touch(1, 1, column + .5, 12.5)
                self.assertEqual(host.sent, [])
                host.touch(2, 1, column + .5, 10.5)
                if column in columns:
                    self.assertEqual(host.sent[0][:2], (0, columns[column]))
                    self.assertGreater(host.sent[0][2], 64)
                else:
                    self.assertEqual(host.sent, [])
        host = Host()
        host.hold()
        host.touch(1, 1, 2.5, 5.5)
        host.touch(2, 1, 9.5, 5.5)
        self.assertEqual(host.sent, [(0, p.SET_PAN, 127)])

    def test_latest_relative_parameter_survives_normal_editor_release_then_receipt(self):
        host = Host()
        host.hold()
        host.touch(1, 1, .5, 12.5)
        host.touch(2, 1, .5, 11.5)
        host.touch(2, 1, .5, 10.5)
        self.assertEqual(len(host.sent), 1)
        host.touch(3)
        host.finish(effect=lambda group, op, arg: host.fields[group].update({98: arg}))
        self.assertEqual(len(host.sent), 2)
        self.assertGreater(host.sent[-1][2], host.sent[0][2])

    def test_relative_mixer_return_to_original_overrides_pending_and_queued_values(self):
        for intermediate in (False, True):
            with self.subTest(intermediate=intermediate):
                host = Host()
                host.hold()
                host.touch(1, 1, .5, 12.5)
                host.touch(2, 1, .5, 11.5)
                if intermediate:
                    host.touch(2, 1, .5, 10.5)
                host.touch(2, 1, .5, 12.5)
                host.finish(effect=lambda group, op, arg: host.fields[group].update({98: arg}))
                self.assertEqual(host.sent[-1], (0, p.SET_VOLUME, 64))

    def test_stationary_second_control_touch_never_reverses_an_inflight_edit(self):
        host = Host()
        host.hold()
        host.touch(1, 1, .5, 12.5)
        host.touch(2, 1, .5, 11.5)
        host.touch(1, 2, .5, 12.5)
        host.touch(2, 2, .5, 12.5)
        host.touch(3, 2, .5, 12.5)
        host.finish(effect=lambda group, op, arg: host.fields[group].update({98: arg}))
        self.assertEqual(len(host.sent), 1, 'touching another finger is still no initial jump')

    def test_meter_return_to_original_overrides_pending_value(self):
        host = Host()
        host.hold(x=7.5, y=14.5)
        host.touch(1, 1, 3.5, 11.5)
        host.touch(2, 1, 3.5, 10.5)
        host.touch(2, 1, 3.5, 11.5)
        host.finish(effect=lambda group, op, arg: setattr(host, 'meter', (arg, 4, False)))
        self.assertEqual(host.sent, [(0, 16, 5), (0, 16, 4)])

    def test_mixer_capability_loss_drops_pending_writes_and_never_replays_on_restore(self):
        host = Host()
        host.hold()
        host.touch(1, 1, 13.5, 12.5)
        host.touch(2, 1, 13.5, 11.5)
        host.touch(2, 1, 13.5, 10.5)
        host.fields[0][103] &= ~p.CAP_MIXER
        host.ui.tick()
        host.finish()
        self.assertEqual(len(host.sent), 1)
        host.fields[0][103] |= p.CAP_MIXER
        host.ui.tick()
        self.assertEqual(len(host.sent), 1)

    def test_recording_controls_bind_take_gate_accept_and_rebind_retry(self):
        host = Host()
        host.begin()
        self.assertEqual(host.ui.presentation()[4], 2)
        host.tap(x=9.5, y=2.5)
        self.assertEqual(host.sent[-1], (0, p.ACCEPT, 0))
        self.assertEqual(host.ui.presentation()[4], 0)
        host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 3}))
        host.tap(x=9.5, y=2.5)
        self.assertEqual(len(host.sent), 2, 'cannot Accept an already queued finish')
        host.tap(x=2.5, y=9.5)
        self.assertEqual(host.sent[-1], (0, p.RETRY, 0))
        host.finish(outcome=p.OUTCOME_QUEUED,
                    effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 8}))
        self.assertEqual((host.ui.view, host.ui.active_take, host.ui.presentation()[4]), (2, 8, 0))
        host.fields[0][106] = 2
        host.ui.tick()
        self.assertEqual(host.ui.presentation()[4], 2)

    def test_failed_retry_without_actual_take_keeps_fresh_cancel_and_retry_usable(self):
        for action, x, y in ((p.ABORT, 2.5, 2.5), (p.RETRY, 2.5, 9.5)):
            with self.subTest(action=action):
                host = Host()
                host.begin()
                host.tap(x=2.5, y=9.5)
                self.assertEqual(host.sent[-1], (0, p.RETRY, 0))
                host.finish(outcome=p.CAPACITY,
                            effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 0}))
                self.assertEqual((host.ui.view, host.ui.active_take, host.ui.presentation()[4]), (2, None, 0))
                self.assertEqual(len(host.sent), 2, 'failed restart must never automatically retry')
                host.tap(x=9.5, y=2.5)
                host.tap(x=9.5, y=9.5)
                self.assertEqual(len(host.sent), 2, 'no take means neither Accept control is usable')
                host.tap(x=x, y=y)
                self.assertEqual(host.sent[-1], (0, action, 0))
                if action == p.ABORT:
                    host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 0, 107: 0}))
                    self.assertEqual(host.ui.view, 0)
                else:
                    host.finish(outcome=p.OUTCOME_QUEUED,
                                effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 8}))
                    self.assertEqual((host.ui.view, host.ui.active_take), (2, 8))

    def test_failed_retry_invalidates_old_held_contact_and_zero_is_still_valid_successful_take_id(self):
        host = Host()
        host.begin()
        host.tap(x=2.5, y=9.5)
        host.touch(1, 15, 2.5, 2.5)
        host.finish(outcome=p.NOT_READY,
                    effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 0}))
        host.touch(3, 15, 2.5, 2.5)
        self.assertEqual(len(host.sent), 2, 'stale held Cancel must not cross the failed-generation boundary')
        host.tap(x=2.5, y=9.5)
        host.finish(outcome=p.OUTCOME_QUEUED,
                    effect=lambda group, op, arg: host.fields[group].update({106: 1, 107: 0}))
        self.assertEqual(host.ui.active_take, 0, 'successful modulo128 take0 is a real take')
        host.fields[0][106] = 2
        host.ui.tick()
        self.assertEqual(host.ui.presentation()[4], 2)

    def test_all_record_quadrants_generation_checks_and_chaining_capability(self):
        for x, y, op in ((2.5, 2.5, p.ABORT), (9.5, 2.5, p.ACCEPT),
                         (2.5, 9.5, p.RETRY), (9.5, 9.5, p.ACCEPT_NEXT)):
            with self.subTest(op=op):
                host = Host()
                host.begin()
                host.tap(x=x, y=y)
                self.assertEqual(host.sent[-1], (0, op, 0))
        host = Host()
        host.begin()
        host.fields[0][103] &= ~p.CAP_CHAINING
        host.tap(x=9.5, y=9.5)
        self.assertEqual(len(host.sent), 1)
        host.fields[0][107] = 99
        host.tap(x=2.5, y=2.5)
        self.assertEqual(len(host.sent), 1, 'stale modal cannot control another take generation')

    def test_queued_take_cannot_accept_and_terminal_receipt_follows_actual_completion(self):
        host = Host()
        host.begin()
        host.fields[0][106] = 1
        host.tap(x=9.5, y=2.5)
        self.assertEqual(len(host.sent), 1)
        host.fields[0].update({106: 4, 107: 7})
        host.ui.tick()
        self.assertEqual(host.ui.view, 0)
        self.assertEqual(host.sent[-1], (0, p.ACK_COMPLETION, 7))
        host.finish(outcome=p.NOT_READY)
        host.ui.tick()
        self.assertEqual(len(host.sent), 2, 'one observed terminal is not recursively retried')

    def test_synchronous_callbacks_bind_begin_without_overwriting_cleared_reservation(self):
        host = Host()
        host.sync = True
        host.sync_effect = lambda group, op, arg: host.fields[group].update({106: 2, 107: 7})
        host.fields[0][91] = p.EMPTY
        host.down()
        self.assertEqual((host.ui.view, host.ui.active_take, host.ui.begin_wait), (2, 7, False))
        self.assertEqual(host.ui.presentation()[4], 2)
        host.tap(x=9.5, y=2.5)
        self.assertEqual(host.sent[-1], (0, p.ACCEPT, 0))

    def test_reset_generation_ignores_late_callback_and_sends_no_native_release(self):
        host = Host()
        host.fields[0][91] = p.EMPTY
        host.down()
        old = host.callbacks.pop()[3]
        host.busy.clear()
        host.ui.reset()
        host.down()
        host.fields[0].update({106: 2, 107: 77})
        old(p.APPLIED)
        self.assertTrue(host.ui.begin_wait, 'old completion must not bind the new Begin')
        host.finish()
        self.assertEqual(host.ui.active_take, 77)
        self.assertEqual([op for _, op, _ in host.sent], [p.BEGIN, p.BEGIN])

    def test_lost_touch_lease_discards_chord_without_commit_and_fresh_start_recovers(self):
        host = Host()
        host.touch(1)
        host.touch(1, 1, 5.5)
        host.now += 1
        host.ui.tick()
        self.assertEqual((host.ui.chord_mask, host.ui.view), (0, 0))
        host.touch(3)
        host.touch(3, 1, 5.5)
        self.assertEqual(host.sent, [])
        host.tap()
        self.assertEqual(host.sent, [(0, p.SET_MUTE, 127)])

    def test_hold_heartbeats_keep_editor_open_and_timeout_drops_pending_desires(self):
        host = Host()
        host.hold()
        host.touch(1, 1, .5, 12.5)
        host.touch(2, 1, .5, 11.5)
        host.touch(2, 1, .5, 10.5)
        for _ in range(3):
            host.touch(2, 0, 1.5, 1.5, .3)
            host.touch(2, 1, .5, 10.5)
        self.assertEqual(host.ui.view, 1)
        host.now += 1
        host.ui.tick()
        self.assertEqual(host.ui.view, 0)
        host.finish()
        self.assertEqual(len(host.sent), 1)

    def test_meter_hold_relative_drag_click_strip_and_three_setting_coalescing(self):
        host = Host()
        host.hold(x=7.5, y=14.5)
        self.assertEqual(host.ui.view, 3)
        host.touch(1, 1, 3.5, 12.5)
        self.assertEqual(host.sent, [])
        for y in (11.5, 10.5, 9.5):
            host.touch(2, 1, 3.5, y)
        host.touch(1, 2, 11.5, 11.5)
        host.touch(2, 2, 11.5, 10.5)
        host.touch(1, 3, 7.5, 7.5)
        self.assertEqual(host.sent, [(0, 16, 5)])
        host.touch(3, 3, 7.5, 7.5)
        for _ in range(4):
            if not host.callbacks:
                break
            host.finish()
        self.assertEqual(host.sent, [(0, 16, 5), (0, 16, 7), (0, 17, 8), (0, 18, 127)])
        host.touch(3, 0, 7.5, 14.5)
        self.assertEqual(host.ui.view, 0)
        self.assertNotIn(p.TAP_TEMPO, [op for _, op, _ in host.sent])

    def test_full_bottom_tap_and_meter_numeric_bounds(self):
        for x in (.5, 7.5, 14.5):
            host = Host()
            host.tap(x=x, y=14.5)
            self.assertEqual(host.sent, [(0, p.TAP_TEMPO, 0)])
        for meter, x, y in (((1, 1, False), 3.5, 14.5),
                            ((99, 16, True), 3.5, 8.5),
                            ((4, 1, False), 11.5, 14.5),
                            ((4, 16, False), 11.5, 8.5)):
            host = Host()
            host.meter = meter
            host.hold(x=7.5, y=14.5)
            host.touch(1, 1, x, 11.5)
            host.touch(2, 1, x, y)
            self.assertEqual(host.sent, [])

    def test_fresh_start_replaces_lost_group_lifecycle_and_commits_only_new_tap(self):
        host = Host()
        host.touch(1, 0, 1.5, 1.5)
        host.touch(1, 0, 9.5, 1.5, .1)
        host.touch(3, 0, 9.5, 1.5, .05)
        self.assertEqual(host.sent, [(2, p.SET_MUTE, 127)])

    def test_fresh_start_in_old_chord_never_commits_old_pending_groups(self):
        host = Host()
        host.touch(1, 0, 1.5, 1.5)
        host.touch(1, 1, 5.5, 1.5)
        self.assertEqual(host.ui.chord_mask, 3)
        host.touch(1, 0, 9.5, 1.5, .1)
        host.touch(3, 1, 5.5, 1.5)
        self.assertEqual(host.sent, [])
        host.touch(3, 0, 9.5, 1.5, .05)
        self.assertEqual(host.sent, [(2, p.SET_MUTE, 127)])
        self.assertEqual(host.ui.chord_mask, 0)

    def test_restarted_mixer_and_meter_anchors_can_open_new_held_editor(self):
        for old_y, new_y, expected in ((1.5, 14.5, 3), (14.5, 1.5, 1)):
            with self.subTest(old_y=old_y):
                host = Host()
                host.hold(x=1.5, y=old_y)
                host.touch(1, 1, .5, 11.5)
                host.touch(1, 0, 1.5, new_y, .05)
                self.assertEqual(host.ui.view, 0)
                host.now += .5
                host.ui.tick()
                self.assertEqual(host.ui.view, expected)
                host.touch(2, 1, .5, 9.5)
                host.touch(3, 1, .5, 9.5)
                host.touch(3, 0, 1.5, new_y)
                self.assertEqual(host.sent, [])

    def test_valid_meter_enables_all_four_globals_when_all_phrases_are_unavailable(self):
        for opcode in (p.TAP_TEMPO, 16, 17, 18):
            with self.subTest(opcode=opcode):
                host = Host()
                for fields in host.fields:
                    fields[91], fields[103] = p.UNAVAILABLE, 0
                if opcode == p.TAP_TEMPO:
                    host.tap(x=7.5, y=14.5)
                    expected = 0
                else:
                    host.hold(x=7.5, y=14.5)
                    x, y = (7.5, 7.5) if opcode == 18 else (3.5 if opcode == 16 else 11.5, 11.5)
                    host.touch(1, 1, x, y)
                    if opcode == 18:
                        host.touch(3, 1, x, y)
                        expected = 127
                    else:
                        host.touch(2, 1, x, y - 1)
                        expected = 5 if opcode == 16 else 8
                self.assertEqual(host.sent, [(0, opcode, expected)])

    def test_invalid_actual_meter_blocks_globals_and_tolerates_missing_state(self):
        invalid = (None, (0, 4, False), (100, 4, False), (4, 3, False),
                   (True, 4, False), (4, True, False), (4, 4, 0), (4, 4), [4, 4, False])
        for meter in invalid:
            with self.subTest(meter=meter):
                host = Host()
                host.meter = meter
                host.tap(x=7.5, y=14.5)
                host.hold(x=7.5, y=14.5)
                host.touch(1, 1, 3.5, 11.5)
                host.touch(2, 1, 3.5, 10.5)
                host.touch(3, 1, 3.5, 10.5)
                host.touch(1, 2, 11.5, 11.5)
                host.touch(2, 2, 11.5, 10.5)
                host.touch(3, 2, 11.5, 10.5)
                host.touch(1, 3, 7.5, 7.5)
                host.touch(3, 3, 7.5, 7.5)
                self.assertEqual(host.sent, [])

    def test_valid_meter_does_not_enable_mute_mixer_or_record_for_unavailable_group(self):
        for swipe, hold in ((False, False), (True, False), (False, True)):
            host = Host()
            for fields in host.fields:
                fields[91], fields[103] = p.UNAVAILABLE, 0
            host.touch(1)
            if swipe:
                host.touch(2, y=4)
            if hold:
                host.now += .5
                host.ui.tick()
            self.assertEqual(host.ui.view, 0)
            host.touch(3, y=4 if swipe else 1.5)
            self.assertEqual(host.sent, [])

    def test_global_desired_value_flushes_after_phrase_availability_loss_and_reset_drops_it(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                host = Host()
                host.hold(x=7.5, y=14.5)
                host.touch(1, 1, 3.5, 11.5)
                host.touch(2, 1, 3.5, 10.5)
                host.touch(2, 1, 3.5, 9.5)
                for fields in host.fields:
                    fields[91], fields[103] = p.UNAVAILABLE, 0
                host.ui.tick()
                if reset:
                    host.ui.reset()
                host.finish(effect=lambda group, op, arg: setattr(host, 'meter', (arg, 4, False)))
                self.assertEqual(host.sent, [(0, 16, 5)] if reset else [(0, 16, 5), (0, 16, 6)])

    def test_unavailable_snapshot_during_group_touch_is_ignored(self):
        host = Host()
        host.touch(1)
        host.fields[0] = None
        host.touch(3)
        self.assertEqual(host.sent, [])


if __name__ == '__main__':
    unittest.main()
