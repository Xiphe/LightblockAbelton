"""Role/context routing and actual Surface cancellation ordering, without Live."""
import unittest
from unittest.mock import patch
import test_surface as fixtures
import test_device_ui as legacy
from LightpadController import protocol as p, configuration as config, surface as module
from LightpadController.device_ui import DeviceUI
from LightpadController.touch_protocol import TouchParser, encode_touch, START, MOVE, END

class ParserTests(unittest.TestCase):

    def receive(self, parser, *args, **kwargs):
        result = None
        for message in encode_touch(*args, **kwargs):
            result = parser.receive(message)
        return result

    def test_roles_coordinates_context_and_same_seven_bytes(self):
        parser = TouchParser(ready=True)
        wire = encode_touch(START, 15, 14.5, 13.5, 127, 0, view=2, group=7, can_accept=True)
        self.assertEqual(len(wire), 7)
        events = []
        parser.pre_touch = events.append
        for message in wire:
            event = parser.receive(message)
        self.assertEqual((event.role, event.view, event.group, event.can_accept, event.seq), (0, 2, 7, True, 127))
        self.assertEqual((event.x, event.y), (14.5, 13.5))
        self.assertEqual(events, [event])
        self.assertEqual(self.receive(parser, END, 15, 14.5, 13.5, 0, 0, view=0).seq, 0)

    def test_auxiliary_role_can_be_blocked_but_cannot_reactivate(self):
        parser = TouchParser(ready=True)
        self.receive(parser, START, 13, 12.5, 13.5, 1, 1, drawer_open=True)
        self.assertEqual(self.receive(parser, MOVE, 13, 12.5, 13.5, 2, 5).role, 5)
        self.assertIsNone(self.receive(parser, END, 13, 12.5, 13.5, 3, 1, drawer_open=True))
        self.assertEqual(self.receive(parser, END, 13, 12.5, 13.5, 4, 5).role, 5)

    def test_handle_drag_history_is_separate_from_any_movement(self):
        parser = TouchParser(ready=True)
        self.receive(parser, START, 1, 7.5, 14.5, 1, 1)
        event = self.receive(parser, END, 1, 7.5, 14.5, 2, 1, moved=True, dragged=True)
        self.assertTrue(event.dragged)
        self.assertTrue(event.moved)
        self.assertFalse(event.can_accept)

    def test_blocked_meter_transition_only_and_bad_frames_never_commit(self):
        parser = TouchParser(ready=True)
        self.receive(parser, START, 2, 1.5, 6.5, 0, 2, drawer_open=True)
        self.assertEqual(self.receive(parser, MOVE, 2, 1.5, 6.5, 1, 5).role, 5)
        self.assertIsNone(self.receive(parser, END, 2, 1.5, 6.5, 2, 2))
        parser.reset(ready=True)
        self.receive(parser, START, 0, 1.5, 1.5, 1, 6)
        self.assertIsNone(self.receive(parser, MOVE, 0, 1.5, 2.5, 2, 0))
        wire = list(encode_touch(END, 0, 1.5, 1.5, 3, 6))
        wire[1] = (wire[1][0], 21, 7 << 2 | 3)
        for message in wire:
            event = parser.receive(message)
        self.assertIsNone(event)
        wire = list(encode_touch(END, 0, 1.5, 1.5, 4, 6))
        wire[-1] = (wire[-1][0], 26, 5)
        for message in wire:
            event = parser.receive(message)
        self.assertIsNone(event)

class UITests(unittest.TestCase):

    def setUp(self):
        with patch.object(legacy, 'DeviceUI', DeviceUI):
            self.host = legacy.Host()
        self.ui = self.host.ui

    def touch(self, kind, role, index=0, x=1.5, y=1.5, after=0, **metadata):
        self.host.now += after
        metadata.setdefault('view', self.ui.view)
        metadata.setdefault('group', self.ui.selected_group or self.ui.recording_group or 0)
        self.ui.touch(kind, index, x, y, role=role, **metadata)

    def test_group_mute_down_and_original_presentation(self):
        self.touch(START, 6)
        self.touch(END, 6, after=0.05)
        self.assertEqual(self.host.sent, [(0, p.SET_MUTE, 127)])
        self.assertEqual(len(self.ui.presentation()), 7)
        self.assertFalse(hasattr(self.ui, 'drawer_endpoint'))
        self.host.finish()
        self.touch(START, 6)
        self.touch(MOVE, 6, y=4, after=0.05)
        self.touch(END, 6, y=4, after=0.05)
        self.assertEqual(self.host.sent[-1], (0, p.BEGIN, 0))

    def test_mixer_anchor_can_cross_overview_to_mixer_and_control_context_latches(self):
        self.touch(START, 6)
        self.host.now += 0.5
        self.ui.tick()
        self.assertEqual(self.ui.view, 1)
        self.touch(START, 0, index=1, x=1.5, y=12.5, view=1, group=0)
        self.touch(MOVE, 0, index=1, x=1.5, y=10.5, after=0.05, view=0, group=7)
        self.assertEqual(self.host.sent[-1], (0, p.SET_VOLUME, 100))
        self.touch(END, 6, view=1, group=0)
        self.assertEqual(self.ui.view, 0)
        count = len(self.host.sent)
        self.touch(END, 0, index=1, x=1.5, y=9, view=0, group=0)
        self.assertEqual(len(self.host.sent), count)

    def test_wrong_role_or_visible_context_does_not_reinterpret_coordinates(self):
        self.touch(START, 0, view=1, group=0)
        self.touch(END, 0)
        self.touch(START, 2, y=6.5, drawer_open=False)
        self.touch(END, 2, y=4.5, drawer_open=False)
        self.touch(START, 5)
        self.touch(END, 5)
        self.assertEqual(self.host.sent, [])

    def test_old_collapsed_bar_and_close_are_not_admitted(self):
        for opened, x, y in ((False, 7.5, 14.5), (True, 2.5, 13.5)):
            self.touch(START, 1, x=x, y=y, drawer_open=opened)
            self.touch(END, 1, x=x, y=y, drawer_open=opened, after=0.05)
        self.assertEqual(self.host.sent, [])

    def test_meters_and_groups_are_independent_and_blocked_role_cancels(self):
        self.touch(START, 2, index=1, y=6.5, drawer_open=True)
        self.touch(START, 6, index=0)
        self.touch(END, 6, index=0, after=0.05)
        self.assertEqual(self.host.sent, [(0, p.SET_MUTE, 127)])
        self.host.finish()
        self.touch(MOVE, 2, index=1, y=4.5, after=0.05, drawer_open=True)
        self.assertEqual(self.host.sent[-1], (0, p.SET_NUMERATOR, 5))
        count = len(self.host.sent)
        self.touch(END, 5, index=1, y=2.5, drawer_open=True)
        self.assertEqual(len(self.host.sent), count)

    def test_open_tap_captures_start_and_allows_out_and_return(self):
        self.touch(START, 1, index=14, x=11.5, y=10.5, drawer_open=True)
        self.assertEqual(self.ui._aux[14].auxiliary, 'tap')
        self.touch(MOVE, 1, index=14, x=2.5, y=12.5, drawer_open=True, after=0.05)
        self.touch(END, 1, index=14, x=11.5, y=10.5, drawer_open=True, moved=True, after=0.6)
        self.assertEqual(self.host.sent, [(0, p.TAP_TEMPO, 0)])

    def test_tap_leaving_its_button_does_not_toggle_click_or_tap(self):
        self.touch(START, 1, index=14, x=11.5, y=12.5, drawer_open=True)
        self.touch(END, 1, index=14, x=2.5, y=12.5, drawer_open=True, after=0.05)
        self.assertEqual(self.host.sent, [])

    def test_blocked_role_orphans_held_tap(self):
        self.touch(START, 1, index=13, x=11.5, y=12.5, drawer_open=True)
        self.touch(END, 5, index=13, x=11.5, y=12.5)
        self.touch(END, 1, index=13, x=11.5, y=12.5, drawer_open=True)
        self.assertEqual(self.host.sent, [])

    def test_visual_row_and_center_gap_are_blocked_and_every_other_pixel_has_one_role(self):
        for y in range(15):
            for x in range(15):
                roles=[role for role in (1,2,3,4)
                       if self.ui._context(role,0,0,True,x+.5,y+.5)]
                expected=[] if y==0 or x==7 else [2 if x<7 else 3] if y<10 else [4 if x<7 else 1]
                self.assertEqual(roles,expected,(x,y))

    def test_exact_fullscreen_hit_region_boundaries(self):
        cases=((2,0,1),(2,6.99,9.99),(3,8,1),(3,14.99,9.99),
               (4,0,10),(4,6.99,14.99),(1,8,10),(1,14.99,14.99))
        for role,x,y in cases:
            self.assertTrue(self.ui._context(role,0,0,True,x,y),(role,x,y))
            self.assertFalse(self.ui._context(role,0,0,False,x,y),(role,x,y))
        for role,x,y in ((2,7,3),(3,7.99,3),(2,1,10),(3,10,.99),(4,7,12),(1,10,9.99)):
            self.assertFalse(self.ui._context(role,0,0,True,x,y),(role,x,y))

    def test_click_is_left_only_and_never_reinterprets_as_tap(self):
        self.touch(START, 4, x=6.5, y=10.5, drawer_open=True)
        self.touch(END, 4, x=6.5, y=10.5, drawer_open=True)
        self.assertEqual(self.host.sent, [(0, p.SET_METRONOME, 127)])
        self.host.finish(effect=lambda *_: setattr(self.host, 'meter', (4, 4, True)))
        self.touch(START, 4, x=2.5, y=12.5, drawer_open=True)
        self.touch(END, 4, x=11.5, y=12.5, drawer_open=True)
        self.assertEqual(len(self.host.sent), 1)

class SurfaceTests(unittest.TestCase):

    def run_pending(self, action='blocked', prehook=True):
        helper = fixtures.SurfaceTests()
        self.addCleanup(helper.doCleanups)
        with patch.multiple(config, AUDIO_CONTROLLER_MANIFEST=None, NATIVE_BAR_CLOCK=False):
            surface, instance = helper.make()
            song = instance.song
            song.signature_numerator = 4
            song.signature_denominator = 4
            song.metronome = False
            surface.receive_midi((191, 110, p.VERSION))

            class Engine:
                held = None
                release = False

                def __init__(self):
                    self.calls = []

                def dispatch(self, command):
                    self.calls.append(command)
                    self.held = command
                    return None

                def tick(self):
                    if self.release and self.held is not None:
                        command = self.held
                        self.held = None
                        song.signature_numerator = command.arg
                        return [(command, p.APPLIED)]
                    return []

                def snapshot(self, group):
                    return surface._groups.snapshot(group)

                def group_clock(self, index):
                    return None

                def global_clock(self):
                    return None
            engine = Engine()
            surface._engine = engine
            seq = [0] * 16

            def raw(kind, role, index, x, y, **flags):
                for message in encode_touch(kind, index, x, y, seq[index], role, **flags):
                    surface.receive_midi(message)
                seq[index] = seq[index] + 1 & 127
            raw(START, 2, 1, 1.5, 6.5, drawer_open=True)
            raw(MOVE, 2, 1, 1.5, 4.5, drawer_open=True)
            self.assertEqual(len(engine.calls), 1)
            raw(MOVE, 2, 1, 1.5, 2.5, drawer_open=True)
            self.assertIn(p.SET_NUMERATOR, surface._ui._desired[0])
            if action in ('tap', 'blocked_tap'):
                raw(START, 1, 14, 11.5, 12.5, drawer_open=True)
            elif action == 'old_close':
                raw(START, 1, 14, 2.5, 13.5, drawer_open=True)
            elif action == 'old_bar':
                raw(START, 1, 14, 7.5, 14.5)
            engine.release = True
            if not prehook:
                surface._touch_parser.pre_touch = None
            if action == 'blocked':
                raw(END, 5, 1, 1.5, 2.5)
            elif action == 'blocked_tap':
                raw(END, 5, 14, 11.5, 12.5)
            elif action == 'tap':
                raw(END, 1, 14, 11.5, 12.5, drawer_open=True)
            elif action == 'old_close':
                raw(END, 1, 14, 2.5, 13.5)
            elif action == 'old_bar':
                raw(END, 1, 14, 7.5, 14.5)
            return (surface, engine, helper)

    def test_blocked_meter_cancels_before_native_completion(self):
        surface, engine, _ = self.run_pending()
        self.assertEqual(len(engine.calls), 1)
        self.assertNotIn(p.SET_NUMERATOR, surface._ui._desired[0])
        self.assertNotIn(1, surface._ui._roles)
        self.assertNotIn(1, surface._ui._aux)
        self.assertNotIn(1, surface._touch_parser._active)

    def test_missing_pre_touch_hook_reproduces_hidden_meter_dispatch(self):
        surface, engine, _ = self.run_pending(prehook=False)
        self.assertEqual(len(engine.calls), 2)

    def test_tap_does_not_cancel_independent_meter_contacts(self):
        surface, engine, _ = self.run_pending('tap')
        self.assertEqual(len(engine.calls), 2)
        self.assertFalse(surface._ui._roles[1].ignored)

    def test_removed_close_and_bar_do_not_cancel_meter_contacts(self):
        for action in ('old_close', 'old_bar'):
            with self.subTest(action=action):
                surface, engine, _ = self.run_pending(action)
                self.assertEqual(len(engine.calls), 2)
                self.assertFalse(surface._ui._roles[1].ignored)

    def test_blocked_tap_preserves_independent_meter_desire(self):
        surface, engine, _ = self.run_pending('blocked_tap')
        self.assertEqual(len(engine.calls), 2)
        self.assertFalse(surface._ui._roles[1].ignored)
if __name__ == '__main__':
    unittest.main()
