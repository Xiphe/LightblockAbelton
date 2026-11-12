"""Independent meter double taps through the actual DeviceUI intent queue.

Movement checks cover received positions and moved/dragged flags. The current
numeric device sender cannot report motion hidden entirely by Move coalescing.
No Live process, MIDI port or device is opened by these checks.
"""
import unittest
from unittest.mock import patch
import test_device_ui as fixture
from LightpadController.device_ui import DeviceUI
from LightpadController import protocol as p


class MeterDoubleTapTests(unittest.TestCase):
    def setUp(self):
        with patch.object(fixture, 'DeviceUI', DeviceUI):
            self.host = fixture.Host()
        self.host.meter = (7, 8, False)
        self.ui = self.host.ui

    def event(self, kind, role, finger=1, after=0, x=None, y=5.5, **metadata):
        self.host.now += after
        if x is None:
            x = 1.5 if role == 2 else 11.5
        flags = dict(role=role, view=0, group=0, drawer_open=True)
        flags.update(metadata)
        self.ui.touch(kind, finger, x, y, **flags)

    def tap(self, role, finger=1, gap=0, duration=.05, **end):
        self.event(1, role, finger, after=gap)
        self.event(3, role, finger, after=duration, **end)

    def confirm(self):
        def effect(group, op, arg):
            n, d, click = self.host.meter
            self.host.meter = (arg if op == p.SET_NUMERATOR else n,
                               arg if op == p.SET_DENOMINATOR else d,
                               bool(arg) if op == p.SET_METRONOME else click)
        self.host.finish(effect=effect)

    def test_second_release_resets_only_its_component_and_different_fingers_work(self):
        for role, op in ((2, p.SET_NUMERATOR), (3, p.SET_DENOMINATOR)):
            self.setUp()
            self.tap(role)
            self.assertEqual(self.host.sent, [])
            self.event(1, role, 2, after=.1)
            self.assertEqual(self.host.sent, [])
            self.event(3, role, 2, after=.05)
            self.assertEqual(self.host.sent, [(0, op, 4)])
            self.confirm()
            self.assertEqual(self.host.meter, (4, 8, False) if role == 2 else (7, 4, False))

    def test_histories_never_combine_different_fields(self):
        self.tap(2)
        self.tap(3, finger=2, gap=.05)
        self.assertEqual(self.host.sent, [])
        self.tap(2, gap=.05)
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 4)])

    def test_two_finger_pairs_preserve_both_resets_behind_one_inflight_ack(self):
        for kind in (1, 3):
            self.event(kind, 2, 1, after=.025)
            self.event(kind, 3, 2)
        self.host.now += .1
        for kind in (1, 3):
            self.event(kind, 2, 1, after=.025)
            self.event(kind, 3, 2)
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 4)])
        self.assertEqual(self.ui._desired[0][p.SET_DENOMINATOR], 4)
        self.confirm()
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 4), (0, p.SET_DENOMINATOR, 4)])
        self.confirm()
        self.assertEqual(self.host.meter, (4, 4, False))
        self.assertEqual(self.ui._meter_taps, {})

    def test_pair_is_consumed_so_a_triple_tap_has_one_reset(self):
        self.tap(2)
        self.tap(2, gap=.1)
        self.confirm()
        self.tap(2, gap=.1)
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 4)])

    def test_window_is_release_to_next_start_and_second_hold_does_not_expire_it(self):
        self.tap(2, duration=.25)
        self.event(1, 2, after=.35)
        self.event(3, 2, after=.25)
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 4)])

    def test_expired_or_long_taps_cannot_reset(self):
        for first_duration, gap, second_duration in ((.251, .1, .05), (.05, .351, .05), (.05, .1, .251)):
            self.setUp()
            self.tap(2, duration=first_duration)
            self.tap(2, gap=gap, duration=second_duration)
            self.assertEqual(self.host.sent, [], (first_duration, gap, second_duration))

    def test_observed_out_and_back_invalidates_pair_without_a_detent_write(self):
        for dx, dy in ((1.125, 0), (0, -1.125)):
            self.setUp()
            self.tap(2)
            self.event(1, 2, after=.1)
            self.event(2, 2, x=1.5 + dx, y=5.5 + dy, after=.02)
            self.event(2, 2, after=.02)
            self.event(3, 2, after=.02)
            self.tap(2, gap=.1)
            self.assertEqual(self.host.sent, [])

    def test_reported_end_movement_invalidates_an_otherwise_stationary_pair(self):
        for flag in ('moved', 'dragged'):
            self.setUp()
            self.tap(2)
            self.tap(2, gap=.1, **{flag: True})
            self.tap(2, gap=.1)
            self.assertEqual(self.host.sent, [])

    def test_same_field_overlap_cancels_both_contacts_but_other_field_survives(self):
        self.tap(3, finger=3)
        self.tap(2)
        self.event(1, 2, 1, after=.05)
        self.event(1, 2, 2)
        self.event(3, 2, 1, after=.05)
        self.event(3, 2, 2)
        self.tap(3, finger=3, gap=.05)
        self.assertEqual(self.host.sent, [(0, p.SET_DENOMINATOR, 4)])

    def test_blocking_one_field_does_not_cancel_other_held_second_tap(self):
        self.tap(2)
        self.tap(3, finger=2)
        self.event(1, 2, 1, after=.05)
        self.event(1, 3, 2)
        self.event(3, 5, 1, x=1.5, after=.05)
        self.event(3, 3, 2)
        self.assertEqual(self.host.sent, [(0, p.SET_DENOMINATOR, 4)])

    def test_invalid_release_field_or_modal_context_discards_the_candidate(self):
        for end in ({'x': 8.5}, {'y': 10.5}, {'view': 1}, {'drawer_open': False}):
            self.setUp()
            self.tap(2)
            self.tap(2, gap=.1, **end)
            self.tap(2, gap=.1)
            # A release far below the field remains an ordinary relative drag,
            # but never the double-tap reset, and cannot seed a third-tap reset.
            expected = [(0, p.SET_NUMERATOR, 5)] if 'y' in end else []
            self.assertEqual(self.host.sent, expected, end)

    def test_stale_contact_reset_and_view_change_clear_history(self):
        for change in ('stale', 'reset', 'view'):
            self.setUp()
            self.tap(2)
            if change == 'stale':
                self.event(1, 2, after=.05)
                self.host.now += 1.01
                self.ui.tick()
                self.event(3, 2)
            elif change == 'reset':
                self.ui.reset()
            else:
                self.ui.view = 1
                self.ui.tick()
                self.ui.view = 0
            self.tap(2, gap=.05)
            self.assertEqual(self.host.sent, [], change)

    def test_unavailable_actual_meter_at_release_cannot_reset(self):
        self.tap(2)
        self.event(1, 2, after=.1)
        self.host.meter = None
        self.event(3, 2, after=.05)
        self.host.meter = (7, 8, False)
        self.tap(2, gap=.05)
        self.assertEqual(self.host.sent, [])

    def test_normal_drag_stays_immediate_and_cancels_only_its_tap_history(self):
        self.tap(3, finger=2)
        self.tap(2)
        self.event(1, 2, after=.05)
        self.event(2, 2, y=3.5, after=.01)
        self.assertEqual(self.host.sent, [(0, p.SET_NUMERATOR, 8)])
        self.event(3, 2, y=3.5, after=.01)
        self.confirm()
        self.tap(3, finger=2, gap=.05)
        self.assertEqual(self.host.sent[-1], (0, p.SET_DENOMINATOR, 4))


if __name__ == '__main__':
    unittest.main()
