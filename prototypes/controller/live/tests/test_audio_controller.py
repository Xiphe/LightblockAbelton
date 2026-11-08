"""Controller integration with real fixture/backend and explicit native events.

These fake observations exercise orchestration, not Ableton timing evidence.
"""

import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.audio_controller import AudioController
from LightpadController.audio_fixture import AudioFixture
from LightpadController.session import COMPLETED_VIEW, FINISH_VIEW, OVERVIEW_VIEW, QUEUED_VIEW, RECORDING_VIEW
from LightpadController.timing import ONE_BAR, REFERENCE
from test_audio_backend import Track as BackendTrack
from test_audio_fixture import OFF, Q_BAR, route
from test_parameter_bindings import Mixer


class NativeTrack(BackendTrack):
    def __init__(self, name, calls, parent=None, group=False):
        super().__init__(name, calls)
        self.group_track, self.is_foldable = parent, group
        self.is_grouped, self.is_frozen = parent is not None, False
        self.has_audio_input, self.has_midi_input = True, False
        self.current_monitoring_state = OFF
        self.input_routing_type = route("Ext. In")
        self.input_routing_channel = route("1 T-Bone (1)")
        self.output_routing_type = route(parent.name if parent else "Main")
        self.can_be_armed = not group
        self.mixer_device, self.color, self.devices = Mixer(), 0x2080FF, []


class AudioControllerTests(unittest.TestCase):
    def make(self, probe=True, features=(), enabled=True, groups=2):
        temp = tempfile.TemporaryDirectory(prefix="lightpad-controller-")
        self.addCleanup(temp.cleanup)
        saved = Path(temp.name) / "Full Audio Trial.als"
        saved.write_bytes(b"integration fixture")
        self.calls, tracks, specs = [], [], []
        for gid in range(groups):
            group = NativeTrack("Lightpad %d" % (gid + 1), self.calls, group=True)
            layers = [NativeTrack("Layer %d" % (index + 1), self.calls, parent=group)
                      for index in range(3)]
            tracks.extend([group] + layers)
            specs.append({"index": gid, "name": group.name,
                          "layer_names": [layer.name for layer in layers]})
        self.unrelated = NativeTrack("Unrelated", self.calls)
        tracks.append(self.unrelated)
        self.song = SimpleNamespace(file_path=str(saved), tracks=tracks,
            return_tracks=[object() for _ in range(4)], record_mode=False,
            overdub=False, session_record=False, loop=False, is_counting_in=False,
            count_in_duration=0, clip_trigger_quantization=Q_BAR,
            is_ableton_link_enabled=False, tempo_follower_enabled=False, external_sync=False,
            signature_numerator=4, signature_denominator=4, tempo=120.0,
            current_song_time=1.0, is_playing=True, tap_tempo=lambda: self.calls.append(("tap",)))
        manifest = {"set_path": str(saved),
            "set_sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
            "groups": specs, "bar_grid": {"origin": 0.0, "numerator": 4,
                "denominator": 4, "fixed": True}, "capture_input_channel": "1",
            "sync_ui_confirmed_off": ["external_sync"]}
        self.fixture = AudioFixture(self.song, manifest, Q_BAR, OFF)
        self.controller = AudioController(self.fixture, probe=probe, validated_features=features)
        if enabled:
            self.controller.enable()
        self.sequence = 0
        return self.controller

    def command(self, op, arg=0, group=0):
        self.sequence += 1
        return p.Command(self.sequence % 128, group, op, arg)

    def slot(self, lease):
        return self.fixture.groups[lease.group].layers[lease.layer].clip_slots[lease.row]

    def record(self, group=0):
        self.assertEqual(self.controller.dispatch(self.command(p.BEGIN, group=group)), p.OUTCOME_QUEUED)
        take = self.controller.session.take
        self.controller.tick()
        self.assertEqual(self.controller.session.view, QUEUED_VIEW)
        self.song.current_song_time = take.schedule.start_at
        clip = self.slot(take.slot).begin_native(self.song.current_song_time)
        self.controller.tick()
        self.assertEqual(self.controller.session.view, RECORDING_VIEW)
        return take.slot, clip, take.token

    def accept(self, length=8, group=0):
        lease, clip, token = self.record(group)
        self.song.current_song_time = clip.start_time + length - 1
        self.assertEqual(self.controller.dispatch(self.command(p.ACCEPT, group=group)), p.OUTCOME_QUEUED)
        self.controller.tick()
        self.song.current_song_time = clip.start_time + length
        self.slot(lease).finish_native(length)
        self.controller.tick()
        return lease, clip, token

    def test_begin_waits_for_native_start_and_accept_requires_observed_finish_then_receipt(self):
        controller = self.make()
        begin = self.command(p.BEGIN)
        self.assertEqual(controller.dispatch(begin), p.OUTCOME_QUEUED)
        self.assertEqual([call[0] for call in self.calls], ["arm", "fire"])
        fields = controller.snapshot(0, begin.seq, p.OUTCOME_QUEUED)
        self.assertEqual((fields[91], fields[106]), (p.EMPTY, QUEUED_VIEW))
        lease, token = controller.session.take.slot, controller.session.take.token
        controller.tick()
        self.assertEqual(controller.session.view, QUEUED_VIEW)
        self.song.current_song_time = 4
        clip = self.slot(lease).begin_native(4)
        controller.tick()
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[106], RECORDING_VIEW)
        self.song.current_song_time = 6
        self.assertEqual(controller.dispatch(self.command(p.ACCEPT)), p.OUTCOME_QUEUED)
        controller.tick()
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[106], FINISH_VIEW)
        self.assertEqual(controller.session.groups[0].accepted, [])
        self.song.current_song_time = 8
        controller.tick()
        self.assertEqual(controller.session.groups[0].accepted, [])
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[106], FINISH_VIEW)
        self.slot(lease).finish_native(4)
        controller.tick()
        completed = controller.snapshot(0, 23, p.APPLIED)
        self.assertEqual((completed[91], completed[106], completed[107]),
                         (p.PLAYING, COMPLETED_VIEW, token % 128))
        self.assertEqual((completed[104], completed[105]), (23, p.APPLIED))
        before = list(self.calls)
        self.assertEqual(controller.dispatch(self.command(p.ACK_COMPLETION, (token + 1) % 128)), p.STALE)
        self.assertEqual(controller.dispatch(self.command(p.ACK_COMPLETION, token % 128)), p.APPLIED)
        self.assertEqual(controller.session.view, OVERVIEW_VIEW)
        self.assertEqual(self.calls, before)
        self.assertTrue(clip.is_playing)
        self.assertEqual(len(controller.session.groups[0].accepted), 1)

    def test_abort_holds_modal_view_until_native_stop_delete_and_disarm_complete(self):
        controller = self.make()
        lease, clip, token = self.record()
        abort = self.command(p.ABORT)
        self.assertIsNone(controller.dispatch(abort))
        self.assertEqual(self.calls[-1], ("stop", self.slot(lease).track.name, False))
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[106], QUEUED_VIEW)
        self.assertEqual(controller.tick(), [])
        self.assertIs(self.slot(lease).clip, clip)
        self.assertEqual(self.slot(lease).track.playing_slot_index, -2)
        self.assertEqual(controller.tick(), [])
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[107], token % 128)
        self.slot(lease).track.quiet_native()
        self.assertEqual(controller.tick(), [(abort, p.APPLIED)])
        self.assertFalse(self.slot(lease).has_clip)
        self.assertFalse(self.slot(lease).track.arm)
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[106], OVERVIEW_VIEW)
        self.assertEqual(controller.tick(), [])

    def test_advance_completes_only_after_selected_group_is_quiet_and_preserves_accepted_clip(self):
        controller = self.make()
        lease, clip, _ = self.accept(length=4)
        other = self.fixture.groups[1].layers[0]
        other_clip = other.clip_slots[0].begin_native(0)
        advance = self.command(p.ADVANCE)
        self.assertIsNone(controller.dispatch(advance))
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[91], p.UNAVAILABLE)
        self.assertEqual(controller.tick(), [])
        self.assertTrue(clip.is_playing)
        for layer in self.fixture.groups[0].layers:
            layer.quiet_native()
        self.assertEqual(controller.tick(), [(advance, p.APPLIED)])
        self.assertEqual(controller.session.groups[0].row, 1)
        self.assertIs(self.slot(lease).clip, clip)
        self.assertIs(other.clip_slots[0].clip, other_clip)
        self.assertTrue(other_clip.is_playing)
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[91], p.EMPTY)

    def test_reference_phase_is_native_and_global_clock_remains_one_bar(self):
        controller = self.make()
        _, clip, _ = self.accept(length=8)
        self.song.current_song_time = 13.5
        clip.playing_position = 2.25
        reference, global_clock = controller.group_clock(0), controller.global_clock()
        self.assertEqual((reference.context, reference.span, reference.position), (REFERENCE, 8, 2.25))
        self.assertEqual((global_clock.context, global_clock.span, global_clock.position), (ONE_BAR, 4, 1.5))
        self.assertEqual(global_clock.take_id, 0)
        self.assertIsNone(controller.group_clock(1))
        clip.is_playing = False
        self.assertIsNone(controller.group_clock(0))
        self.assertEqual(controller.global_clock().context, ONE_BAR)

    def test_four_controls_volume_and_pan_read_actual_native_values(self):
        controller = self.make()
        mixer = self.fixture.groups[0].group.mixer_device
        operations = (p.SET_VOLUME, p.SET_SEND_1, p.SET_SEND_2, p.SET_SEND_3, p.SET_CONTROL_4)
        parameters = [mixer.volume] + mixer.sends
        for opcode, parameter in zip(operations, parameters):
            parameter.coerce = lambda requested: .25
            self.assertEqual(controller.dispatch(self.command(opcode, 127)), p.APPLIED)
        self.assertEqual(controller.dispatch(self.command(p.SET_PAN, 64)), p.APPLIED)
        fields = controller.snapshot(0, 0, p.NONE)
        self.assertEqual([fields[cc] for cc in (98, 99, 100, 101, 108)], [32] * 5)
        self.assertEqual(fields[102], 64)
        self.assertEqual(mixer.panning.value, 0)
        self.assertEqual(fields[103] & p.CAP_MIXER, p.CAP_MIXER)
        mixer.sends[3].is_enabled = False
        self.assertEqual(controller.dispatch(self.command(p.SET_VOLUME, 0)), p.NOT_READY)
        self.assertEqual(mixer.volume.value, .25)
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[103] & p.CAP_MIXER, 0)

    def test_changed_native_reference_metadata_invalidates_clock_without_callback_error(self):
        controller = self.make()
        _, clip, _ = self.accept(length=8)
        for attribute, value in (("looping", False), ("loop_start", 1),
                                 ("playing_position", float("nan"))):
            with self.subTest(attribute=attribute):
                previous = getattr(clip, attribute)
                setattr(clip, attribute, value)
                self.assertIsNone(controller.group_clock(0))
                self.assertEqual(controller.snapshot(0, 0, p.NONE)[91], p.PLAYING)
                self.assertIsNotNone(controller.global_clock())
                setattr(clip, attribute, previous)

    def test_no_recording_capability_is_invented_without_native_feature_evidence(self):
        controller = self.make(probe=False)
        self.assertEqual(controller.dispatch(self.command(p.BEGIN)), p.NOT_READY)
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[103], p.CAP_MIXER)
        self.assertEqual(self.calls, [])

    def test_unrelated_arming_blocks_capture_and_mixer_but_keeps_global_tap_available(self):
        controller = self.make()
        self.unrelated.arm = True
        before = list(self.calls)
        group = self.fixture.groups[0].group
        for op, arg in ((p.BEGIN, 0), (p.SET_VOLUME, 127), (p.SET_MUTE, 0)):
            self.assertEqual(controller.dispatch(self.command(op, arg)), p.NOT_READY)
        self.assertEqual(controller.dispatch(self.command(p.TAP_TEMPO)), p.APPLIED)
        self.assertEqual(self.calls, before)
        self.assertTrue(group.mute)
        self.assertEqual(group.mixer_device.volume.writes, [])
        self.assertEqual(controller.snapshot(0, 0, p.NONE)[103], 0)
        self.assertIsNotNone(controller.global_clock(), "meter feedback is independent of capture readiness")

    def test_set_path_or_route_changes_cannot_enable_mutations_or_timing(self):
        for change in ("path", "route"):
            with self.subTest(change=change):
                controller = self.make()
                if change == "path":
                    self.song.file_path += ".other"
                else:
                    self.fixture.groups[0].layers[0].input_routing_type = route("Resampling")
                self.assertEqual(controller.dispatch(self.command(p.BEGIN)), p.NOT_READY)
                self.assertEqual(controller.dispatch(self.command(p.SET_VOLUME, 127)), p.NOT_READY)
                self.assertIsNone(controller.global_clock())
                self.assertIsNone(controller.group_clock(0))
                self.assertEqual(self.calls, [])

    def test_unsupported_group_is_unavailable_and_never_targets_same_named_layers_elsewhere(self):
        controller = self.make(groups=1)
        for opcode in (p.BEGIN, p.SET_MUTE, p.SET_CONTROL_4):
            self.assertEqual(controller.dispatch(self.command(opcode, group=1)), p.NOT_READY)
        fields = controller.snapshot(1, 0, p.NONE)
        self.assertEqual((fields[91], fields[103]), (p.UNAVAILABLE, 0))
        self.assertIsNone(controller.group_clock(1))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
