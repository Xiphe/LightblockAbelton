"""Current raw touch through the real Surface, DeviceUI and audio controller.

Native observations are supplied explicitly by fakes. These adapter checks
neither open MIDI endpoints nor establish Ableton timing accuracy.
"""

from types import SimpleNamespace
import unittest
import compact_assertions as wire
from unittest.mock import patch

import test_surface_audio as audio_helpers
from LightpadController import protocol as p
from LightpadController.touch_protocol import encode_touch
from LightpadController import surface as module


def frame(finger=0, seq=1, kind=1, x=1.5, y=1.5, role=6,
          view=0, group=0, can_accept=False, drawer_open=False):
    return list(encode_touch(kind,finger,x,y,seq,role,view=view,group=group,
                            can_accept=can_accept,drawer_open=drawer_open))


class TerminalEngine:
    """A terminal observation disappears as soon as its real ACK is handled."""

    def __init__(self, fields):
        self.fields = [dict(item) for item in fields]
        self.fields[0].update({91: p.PLAYING, 106: 0, 107: 0})
        self.commands = []

    def snapshot(self, group, sequence=0, outcome=p.NONE):
        fields = dict(self.fields[group])
        fields.update({104: sequence, 105: outcome})
        return fields

    def dispatch(self, command):
        self.commands.append(command)
        if command.op == p.ACK_COMPLETION:
            self.fields[command.group].update({106: 0, 107: 0})
            return p.APPLIED
        return p.NOT_READY

    def tick(self):
        return []

    def global_clock(self):
        return None

    def group_clock(self, group):
        return None

    def meter_state(self):
        return 4, 4, False


class SurfaceTouchTests(unittest.TestCase):
    def make(self, hello=True):
        self.helper = audio_helpers.AudioSurfaceTests()
        surface, instance = self.helper.make()
        self.addCleanup(self.helper.doCleanups)
        self.native = self.helper.helper
        self.surface, self.instance = surface, instance
        self._wire_roles = {}
        self.assertIsNotNone(surface._engine, instance.logs)
        dispatch = patch.object(surface._engine, "dispatch", wraps=surface._engine.dispatch)
        self.dispatch = dispatch.start()
        self.addCleanup(dispatch.stop)
        self.native.song.metronome = False
        if hello:
            surface.receive_midi((0xBF, 110, p.VERSION))
            self.helper.pump(surface)
        return surface, instance

    def feed(self, messages):
        for message in messages:
            self.surface.receive_midi(message)

    def touch(self, kind, finger=0, seq=1, x=1.5, y=1.5):
        ui=self.surface._ui
        if kind==1:
            role=1 if ui.view==0 and x>=8 and y>=10 else 6 if ui.view==0 else 0
            self._wire_roles[finger]=role
        role=self._wire_roles.get(finger,6)
        group=ui.selected_group if ui.view==1 else ui.recording_group if ui.view==2 else 0
        self.feed(frame(finger,seq,kind,x,y,role=role,view=ui.view,group=group or 0,
                        can_accept=bool(ui.presentation()[4]&2),drawer_open=role==1))
        if kind==3:self._wire_roles.pop(finger,None)

    def tap(self, finger=0, seq=1, x=1.5, y=1.5):
        self.touch(1, finger, seq, x, y)
        self.helper.now += .05
        self.touch(3, finger, (seq + 1) % 128, x, y)

    def accepted_phrase(self):
        surface = self.surface
        self.helper.command(surface, p.BEGIN, seq=20)
        self.helper.pump(surface)
        take = surface._engine.session.take
        slot = self.native.slot(take.slot)
        self.native.song.current_song_time = 4
        clip = slot.begin_native(4)
        surface.update_display()
        self.native.song.current_song_time = 7
        self.helper.command(surface, p.ACCEPT, seq=21)
        self.helper.pump(surface)
        self.native.song.current_song_time = 8
        slot.finish_native(4)
        surface.update_display()
        self.helper.pump(surface)
        self.helper.command(surface, p.ACK_COMPLETION, seq=22, arg=take.token % 128)
        self.helper.pump(surface)
        self.assertEqual(surface._ui_snapshot(0)[91], p.PLAYING)
        self.dispatch.reset_mock()
        return clip

    def internal_recording(self):
        results = []
        self.assertTrue(self.surface._ui_send(0, p.BEGIN, 0, results.append))
        self.assertEqual(results, [p.OUTCOME_QUEUED])
        take = self.surface._engine.session.take
        slot = self.native.slot(take.slot)
        self.native.song.current_song_time = 4
        slot.begin_native(4)
        self.surface.update_display()
        self.helper.pump(self.surface)
        return slot

    def test_current_version_handshake_required_before_raw_tap_reaches_real_engine(self):
        surface, _ = self.make(hello=False)
        self.tap(x=11.5, y=12.5)
        surface.receive_midi((0xBF, 110, 3))
        self.tap(seq=3, x=11.5, y=12.5)
        self.assertEqual(self.dispatch.call_count, 0)
        self.assertEqual(self.native.calls, [])
        self.native.song.tempo = 90
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.tap(seq=5, x=11.5, y=12.5)
        self.helper.now += .45
        self.tap(seq=7, x=11.5, y=12.5)
        commands = [call.args[0] for call in self.dispatch.call_args_list]
        self.assertEqual([(command.group, command.op, command.arg) for command in commands],
                         [(0, p.TAP_TEMPO, 0)] * 2)
        self.assertAlmostEqual(self.native.song.tempo, 120)
        self.assertEqual(self.native.calls, [], "selected-beat taps must not invoke transport-starting native Tap")

    def test_all_sixteen_raw_finger_channels_are_mapped_and_mute_via_device_ui(self):
        surface, instance = self.make()
        self.accepted_phrase()
        forwarded = []
        instance.handle = lambda: 123
        with patch.object(module.Live, "MidiMap", SimpleNamespace(
                forward_midi_cc=lambda *args: forwarded.append(args)), create=True):
            surface.build_midi_map(456)
        mapped = {(channel, cc) for _, _, channel, cc in forwarded}
        self.assertTrue({(finger, cc) for finger in range(16) for cc in range(20, 27)} <= mapped)
        track = self.native.fixture.groups[0].group
        initial_mute = track.mute
        for finger in range(16):
            self.tap(finger=finger)
            self.assertEqual(track.mute, not initial_mute if finger % 2 == 0 else initial_mute)
        commands = [call.args[0] for call in self.dispatch.call_args_list]
        self.assertEqual(len(commands), 16)
        self.assertTrue(all((command.group, command.op) == (0, p.SET_MUTE) for command in commands))
        self.assertTrue(all(command.seq < 0 for command in commands))

    def test_malformed_end_lost_release_and_latest_frame_replays_never_invent_intents(self):
        surface, _ = self.make()
        self.accepted_phrase()
        track = self.native.fixture.groups[0].group
        before = track.mute
        self.touch(1, seq=10)
        malformed = frame(seq=11, kind=3)
        self.feed(malformed[:3] + malformed[4:])
        self.assertEqual(self.dispatch.call_count, 0)
        self.helper.now += 1.1
        surface.update_display()
        self.touch(3, seq=12)
        self.assertEqual(self.dispatch.call_count, 0)
        self.assertEqual(track.mute, before)
        self.touch(1, seq=13)
        self.touch(1, seq=13)  # Exact latest Start replay cannot create a chord.
        self.touch(3, seq=14)
        self.touch(3, seq=14)
        self.assertEqual(self.dispatch.call_count, 1)
        self.assertEqual(track.mute, not before)
        self.touch(1, seq=15)
        self.helper.now += 1.1  # A wholly lost End expires without a mute.
        surface.update_display()
        self.assertEqual(self.dispatch.call_count, 1)

    def test_raw_cc_interrupts_legacy_partial_frame_without_completing_it(self):
        surface, _ = self.make()
        self.feed([(0xB0, 60, 31)] + frame(seq=10) +
                  [(0xB0, 61, p.SET_MUTE), (0xB0, 62, 0), (0xB0, 63, 31)])
        self.assertEqual(self.dispatch.call_count, 0)
        self.assertTrue(self.native.fixture.groups[0].group.mute)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.touch(3, seq=11)  # Reset removed the incomplete gesture as well.
        self.assertEqual(self.dispatch.call_count, 0)

    def test_forwarded_upper_finger_stream_preserves_unrelated_cc_frame_interruption(self):
        surface, instance = self.make()
        self.accepted_phrase()
        forwarded = []
        instance.handle = lambda: 123
        with patch.object(module.Live, "MidiMap", SimpleNamespace(
                forward_midi_cc=lambda *args: forwarded.append(args)), create=True):
            surface.build_midi_map(456)
        mapped = {(channel, cc) for _, _, channel, cc in forwarded}
        start = frame(finger=15, seq=60)
        wire = start[:2] + [(0xBF, 99, 0)] + start[2:] + frame(finger=15, seq=61, kind=3)
        before = self.native.fixture.groups[0].group.mute
        for message in wire:
            if (message[0] - 0xB0, message[1]) in mapped:
                surface.receive_midi(message)
        self.assertEqual(self.dispatch.call_count, 0,
                         "MIDI mapping must preserve strict raw-frame interruption on every finger channel")
        self.assertEqual(self.native.fixture.groups[0].group.mute, before)

    def test_internal_sync_callback_observes_actual_snapshot_without_a_wire_result_reservation(self):
        surface, _ = self.make()
        observed = []
        outcomes_before = list(surface._outcomes)
        with patch.object(surface._output, "offer", wraps=surface._output.offer) as offer:
            accepted = surface._ui_send(0, p.BEGIN, 0,
                lambda outcome: observed.append((outcome, dict(surface._ui_snapshot(0)))))
        self.assertTrue(accepted)
        self.assertEqual(len(observed), 1)
        self.assertEqual((observed[0][0], observed[0][1][106]), (p.OUTCOME_QUEUED, 1))
        self.assertTrue(self.native.calls[-1][0] == "fire")
        self.assertEqual(surface._outcomes, outcomes_before)
        self.assertEqual(surface._parser._pending, {})
        for call in offer.call_args_list:
            messages = call.args[0]
            if len(messages) == 20:
                callback = call.args[2] if len(call.args) > 2 else call.kwargs.get("on_sent")
                self.assertIsNone(callback, "internal UI completion must not synthesize a wire result receipt")
                if messages[0][1] == 40:
                    self.assertGreaterEqual(messages[14][2], 0)

    def test_legacy_result_reservation_blocks_internal_send_until_actual_transmission(self):
        surface, _ = self.make()
        results = []
        with patch.object(surface._output, "flush"):
            self.helper.command(surface, p.BEGIN, seq=35)
            before = self.dispatch.call_count
            self.assertFalse(surface._ui_send(0, p.ABORT, 0, results.append))
            self.assertEqual(self.dispatch.call_count, before)
        self.assertEqual(results, [])
        self.helper.pump(surface)
        slot = self.native.slot(surface._engine.session.take.slot)
        self.assertTrue(surface._ui_send(0, p.ABORT, 0, results.append))
        self.assertEqual(results, [])
        slot.track.quiet_native()
        surface.update_display()
        self.assertEqual(results, [p.APPLIED])

    def test_pending_internal_cleanup_blocks_legacy_and_callback_follows_native_deletion(self):
        surface, _ = self.make()
        slot = self.internal_recording()
        results = []
        self.assertTrue(surface._ui_send(0, p.ABORT, 0, lambda outcome: results.append(
            (outcome, slot.has_clip, slot.track.arm, surface._ui_snapshot(0)[106]))))
        self.assertEqual(results, [])
        self.assertFalse(surface._ui_send(0, p.SET_MUTE, 0, lambda outcome: None))
        count = self.dispatch.call_count
        self.helper.command(surface, p.SET_MUTE, seq=44, arg=0)
        self.assertEqual(self.dispatch.call_count, count)
        self.assertEqual(surface._outcomes[0], (44, p.BUSY))
        self.helper.pump(surface)
        self.assertEqual(results, [])
        slot.track.quiet_native()
        surface.update_display()
        self.assertEqual(results, [(p.APPLIED, False, False, 0)])
        self.assertNotIn(0, surface._ui_pending)

    def test_peer_reset_forgets_gestures_but_pending_native_intent_still_drains(self):
        surface, instance = self.make()
        slot = self.internal_recording()
        results = []
        self.assertTrue(surface._ui_send(0, p.ABORT, 0, results.append))
        self.touch(1, finger=15, seq=60, x=11.5, y=12.5)
        self.assertIn(15,surface._ui._aux)
        count = self.dispatch.call_count
        surface.receive_midi((0xBF, 110, 3))
        self.assertFalse(surface._parser.ready)
        self.assertIsNone(surface._ui.anchor)
        self.assertEqual(surface._ui._touches, {})
        self.assertEqual(surface._ui._aux,{})
        instance.sent.clear()
        slot.track.quiet_native()
        surface.update_display()
        self.assertEqual(results, [p.APPLIED])
        self.assertFalse(slot.has_clip)
        self.assertFalse(slot.track.arm)
        self.assertEqual(instance.sent, [])
        self.assertEqual(self.dispatch.call_count, count)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.touch(3, finger=15, seq=61, x=11.5, y=12.5)
        self.assertEqual(self.dispatch.call_count, count)

    def test_recognizable_malformed_hello_closes_both_parsers_until_fresh_current_version(self):
        surface, _ = self.make()
        parameter = self.native.fixture.groups[0].group.mixer_device.volume
        initial = parameter.value
        surface.receive_midi((0xBF, 110))
        self.assertFalse(surface._touch_parser.ready)
        self.assertFalse(surface._parser.ready)
        self.helper.command(surface, p.SET_VOLUME, seq=80, arg=127)
        self.assertEqual(parameter.value, initial)
        self.assertEqual(self.dispatch.call_count, 0)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.helper.command(surface, p.SET_VOLUME, seq=81, arg=127)
        self.assertEqual(parameter.value, parameter.max)
        self.assertEqual(self.dispatch.call_count, 1)

    def test_terminal_snapshot_reaches_wire_before_auto_ack_cleared_state(self):
        surface, instance = self.make()
        engine = TerminalEngine([surface._engine.snapshot(group) for group in range(8)])
        surface._engine = engine
        engine.fields[0].update({106: 4, 107: 19})
        instance.sent.clear()
        with patch.object(surface._output, "flush"):
            surface.update_display()
        self.assertEqual([(command.op, command.arg) for command in engine.commands],
                         [(p.ACK_COMPLETION, 19)])
        self.assertEqual(engine.fields[0][106], 0)
        self.helper.pump(surface)
        views = []
        for cursor, message in enumerate(instance.sent):
            if message[:2] != (0xB0, 33):
                continue
            snapshot = instance.sent[cursor:cursor + 17]
            wire.assert_frame(self, snapshot, 33)
            views.append(wire.snapshot_fields(self, snapshot)[106])
        self.assertIn(4, views, "automatic ACK must not erase a terminal snapshot before it reaches the device")
        self.assertIn(0, views[views.index(4) + 1:], "actual cleared state must follow the protected terminal")

    def test_ui_meter_reads_actual_native_values_instead_of_manifest_defaults(self):
        surface, _ = self.make()
        self.native.song.signature_numerator = 7
        self.native.song.signature_denominator = 8
        self.native.song.metronome = True
        self.assertEqual(surface._ui_meter(), (7, 8, True))


if __name__ == "__main__":
    unittest.main()
