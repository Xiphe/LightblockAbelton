"""Offline native-observation guards, never evidence of Ableton timing accuracy."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.meter_probe import MeterProbe, MeterProbeError, OPERATIONS
from test_audio_controller import NativeTrack
from test_audio_fixture import Q_BAR, OFF


class NativeBeatTime:
    bars, beats, sub_division, ticks = 3, 2, 1, 37

    def __str__(self):
        return "3.2.1.37"


class MeterProbeTests(unittest.TestCase):
    def make(self, baseline=False):
        temp = tempfile.TemporaryDirectory(prefix="lightpad-meter-probe-")
        self.addCleanup(temp.cleanup)
        saved = Path(temp.name) / "Meter Trial.als"
        saved.write_bytes(b"isolated offline meter trial")
        self.calls, self.events, self.time = [], [], 10.0
        group = NativeTrack("Lightpad 1", self.calls, group=True)
        self.layers = [NativeTrack("Layer %d" % (i + 1), self.calls, parent=group) for i in range(2)]
        self.other = NativeTrack("Unrelated", self.calls)
        self.song = SimpleNamespace(file_path=str(saved), tracks=[group] + self.layers + [self.other],
            record_mode=False, overdub=False, session_record=False, loop=False,
            is_counting_in=False, count_in_duration=0, clip_trigger_quantization=Q_BAR,
            is_ableton_link_enabled=False, tempo_follower_enabled=False, external_sync=False,
            signature_numerator=4, signature_denominator=4, tempo=120.0,
            current_song_time=0.0, is_playing=False, metronome=False,
            get_current_beats_song_time=NativeBeatTime)
        self.manifest = {"set_path": str(saved),
            "set_sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
            "groups": [{"index": 0, "name": group.name, "layer_names": [t.name for t in self.layers]}],
            "bar_grid": {"origin": 0.0, "numerator": 4, "denominator": 4, "fixed": True},
            "capture_input_channel": "1", "sync_ui_confirmed_off": ["external_sync"]}
        self.baseline = None
        if baseline:
            self.baseline = self.native_begin(self.layers[1].clip_slots[1], -8)
            self.layers[1].clip_slots[1].finish_native(8)
            self.baseline.file_path = "/scratch/retained.wav"
        self.probe = MeterProbe(self.song, self.manifest, Q_BAR, OFF,
            {"q_bar": Q_BAR, "q_quarter": 7, "q_sixtenth": 11}, emit=self.events.append,
            now=lambda: self.time)
        return self.probe

    def native_begin(self, slot=None, start=4):
        slot = slot or self.layers[0].clip_slots[0]
        clip = slot.begin_native(start)
        clip.file_path = "/scratch/new.wav"
        clip.start_marker, clip.end_marker, clip.end_time, clip.length = 0, 4, start + 4, 4
        return clip

    def enable(self):
        return self.probe.dispatch({"op": "enable", "confirm_set_path": self.song.file_path,
            "confirm_fingerprint": self.probe.inspect()["fingerprint"]})

    def start(self, **kwargs):
        return self.probe.dispatch({"op": "start", "group": 0, "layer": 0, "row": 0,
            "quantization": "q_quarter", "expected_target": 12.0, **kwargs})

    def test_disabled_and_exact_enable_confirmation_require_stopped_transport_and_muted_groups(self):
        probe = self.make()
        self.assertFalse(probe.enabled)
        self.assertIn("set_meter", OPERATIONS)
        for op in ("sample", "set_meter", "set_metronome", "start", "stop_attempt", "discard_attempt"):
            with self.subTest(op=op), self.assertRaises(MeterProbeError):
                probe.dispatch({"op": op})
        with self.assertRaises(MeterProbeError):
            probe.dispatch({"op": "enable", "confirm_set_path": self.song.file_path,
                            "confirm_fingerprint": "stale"})
        self.song.is_playing = True
        with self.assertRaises(MeterProbeError):
            self.enable()
        self.song.is_playing = False
        self.layers[0].group_track.mute = False
        with self.assertRaises(MeterProbeError):
            self.enable()
        self.layers[0].group_track.mute = True
        self.assertTrue(self.enable()["enabled"])
        self.assertEqual(self.calls, [])

    def test_explicit_quantum_queued_meter_edit_and_actual_native_anchor_remain_distinct(self):
        probe = self.make()
        self.enable()
        self.song.is_playing = True
        self.start()
        self.assertEqual(self.calls, [("arm", "Layer 1", True),
            ("fire", "Layer 1", 0, {"launch_quantization": 7})])
        probe.dispatch({"op": "set_meter", "numerator": 7, "denominator": 8})
        self.assertEqual((self.song.signature_numerator, self.song.signature_denominator), (7, 8))
        self.song.current_song_time = 12.5
        self.native_begin(start=12.25)
        state = probe.poll()
        self.assertEqual(state["attempt"]["phase"], "recording")
        self.assertEqual(state["attempt"]["expected_start_target"], 12)
        self.assertEqual(state["attempt"]["recording_observation"]["clip"]["start_time"], 12.25)
        self.assertEqual(state["timing_assessment"], "unverified")
        self.assertFalse(state["device_capabilities_advertised"])
        json.dumps(state, allow_nan=False)

    def test_finish_requires_observed_recording_and_only_native_playback_accepts_and_disarms(self):
        probe = self.make()
        self.enable()
        self.song.is_playing = True
        self.start()
        with self.assertRaises(MeterProbeError):
            probe.dispatch({"op": "finish", "quantization": "q_bar"})
        clip = self.native_begin(start=12)
        probe.poll()
        probe.dispatch({"op": "finish", "quantization": "q_sixtenth", "expected_target": 16.25})
        self.assertEqual(self.calls[-1], ("fire", "Layer 1", 0, {"launch_quantization": 11}))
        self.assertTrue(self.layers[0].arm)
        self.assertEqual(probe.poll()["attempt"]["phase"], "finish_queued")
        self.layers[0].clip_slots[0].finish_native(4.25)
        result = probe.poll()
        self.assertEqual(result["attempt"]["phase"], "accepted")
        self.assertFalse(self.layers[0].arm)
        self.assertIs(self.layers[0].clip_slots[0].clip, clip)
        for op in ("stop_attempt", "discard_attempt"):
            with self.assertRaises(MeterProbeError):
                probe.dispatch({"op": op})

    def test_meter_changes_preserve_baseline_clip_bounds_file_and_quarter_beat_length(self):
        probe = self.make(baseline=True)
        self.enable()
        before = (self.baseline.loop_start, self.baseline.loop_end, self.baseline.file_path)
        for numerator, denominator in ((7, 8), (2, 2), (99, 16)):
            probe.dispatch({"op": "set_meter", "numerator": numerator, "denominator": denominator})
            self.assertEqual((self.baseline.loop_start, self.baseline.loop_end, self.baseline.file_path), before)
            self.assertTrue(probe.inspect()["scope_valid"])
        self.assertEqual(self.calls, [])

    def test_scope_path_route_arms_baseline_replacement_or_foreign_row_blocks_all_native_writes(self):
        for change in ("path", "route", "arm", "implicit", "baseline", "bounds", "file", "foreign"):
            probe = self.make(baseline=True)
            self.enable()
            self.song.is_playing = True
            if change == "path": self.song.file_path += ".other"
            elif change == "route": self.layers[0].input_routing_type.display_name = "Resampling"
            elif change == "arm": self.other.arm = True
            elif change == "implicit": self.other.implicit_arm = True
            elif change == "baseline": self.native_begin(self.layers[1].clip_slots[1], 0)
            elif change == "bounds": self.baseline.loop_end += 1
            elif change == "file": self.baseline.file_path = "/other.wav"
            elif change == "foreign": self.native_begin(self.layers[0].clip_slots[2], 3)
            before = list(self.calls)
            with self.subTest(change=change), self.assertRaises(MeterProbeError):
                self.start()
            self.assertEqual(self.calls, before)

    def test_stop_once_then_explicit_discard_only_after_quiet_never_deletes_accepted_material(self):
        probe = self.make(baseline=True)
        self.enable()
        self.song.is_playing = True
        self.start()
        clip = self.native_begin(start=12)
        probe.poll()
        probe.dispatch({"op": "stop_attempt"})
        probe.dispatch({"op": "stop_attempt"})
        self.assertEqual(sum(c[0] == "stop" for c in self.calls), 1)
        with self.assertRaises(MeterProbeError):
            probe.dispatch({"op": "discard_attempt"})
        probe.poll()
        self.assertIs(self.layers[0].clip_slots[0].clip, clip)
        self.layers[0].quiet_native()
        probe.poll()
        self.assertTrue(self.layers[0].arm)
        state = probe.dispatch({"op": "discard_attempt"})
        self.assertEqual(state["attempt"]["phase"], "discarded")
        self.assertEqual(self.calls[-2:], [("delete", "Layer 1", 0), ("arm", "Layer 1", False)])
        self.assertIs(self.layers[1].clip_slots[1].clip, self.baseline)
        self.assertFalse(any(c[0] == "delete" and c[2] == 1 for c in self.calls))

    def test_unknown_nonrecording_active_clip_or_replaced_native_slot_cannot_be_adopted_or_stopped(self):
        for change in ("clip", "slot"):
            probe = self.make()
            self.enable()
            self.song.is_playing = True
            self.start()
            if change == "clip":
                clip = self.native_begin(start=12)
                clip.is_recording = False
            else:
                self.layers[0].clip_slots[0] = self.layers[0].clip_slots[1]
            before = list(self.calls)
            probe.poll()
            with self.subTest(change=change), self.assertRaises(MeterProbeError):
                probe.dispatch({"op": "stop_attempt"})
            self.assertEqual(self.calls, before)
            self.assertFalse(probe.inspect()["scope_valid"])

    def test_bounded_samples_keep_raw_native_units_and_close_only_stops_own_polling(self):
        probe = self.make()
        self.enable()
        probe.dispatch({"op": "sample", "seconds": .5})
        probe.poll()
        samples = [e for e in self.events if e["event"] == "sample"]
        self.assertEqual(len(samples), 1)
        raw = samples[0]["timing"]["native_beat_time"]
        self.assertEqual((raw["bars"], raw["beats"], raw["sub_division"], raw["ticks"]), (3, 2, 1, 37))
        self.time += .6
        probe.poll()
        self.assertEqual(len([e for e in self.events if e["event"] == "sample"]), 1)
        self.assertTrue(any(e["event"] == "sample_completed" for e in self.events))
        self.song.is_playing = True
        self.start()
        before = list(self.calls)
        probe.close()
        probe.poll()
        self.assertEqual(self.calls, before)
        self.assertTrue(self.layers[0].arm)
        with self.assertRaises(MeterProbeError):
            probe.dispatch({"op": "stop_attempt"})

    def test_queued_empty_attempt_can_be_stopped_and_discarded_without_deleting_any_clip(self):
        probe = self.make()
        self.enable()
        self.song.is_playing = True
        self.start()
        probe.dispatch({"op": "stop_attempt"})
        self.layers[0].quiet_native()
        probe.dispatch({"op": "discard_attempt"})
        self.assertFalse(self.layers[0].arm)
        self.assertFalse(any(c[0] == "delete" for c in self.calls))
        self.assertFalse(self.layers[0].clip_slots[0].has_clip)

    def test_replaced_adopted_clip_or_baseline_playback_on_active_track_blocks_stop(self):
        for change in ("adopted_clip", "baseline_playback"):
            probe = self.make(baseline=True)
            self.enable()
            self.song.is_playing = True
            if change == "adopted_clip":
                self.start()
                self.native_begin(start=12)
                probe.poll()
                self.native_begin(start=13)
            else:
                # A baseline that was playing when bound may later stop. It
                # still must not be caught by this track's stop_all_clips.
                self.layers[1].quiet_native()
                self.start(layer=1)
                self.baseline.is_playing = True
                self.layers[1].playing_slot_index = 1
            before = list(self.calls)
            with self.subTest(change=change), self.assertRaises(MeterProbeError):
                probe.dispatch({"op": "stop_attempt"})
            self.assertEqual(self.calls, before)

    def test_command_types_ranges_and_quantum_allowlist_reject_without_mutation(self):
        probe = self.make()
        self.enable()
        bad = [{"op": "set_meter", "numerator": n, "denominator": d}
               for n, d in ((0, 4), (100, 4), (True, 4), (7, 3), (7, False))]
        bad += [{"op": "set_metronome", "enabled": 1},
                {"op": "sample", "seconds": 0}, {"op": "sample", "seconds": 21},
                {"op": "sample", "seconds": float("nan")},
                {"op": "start", "group": True, "layer": 0, "row": 0, "quantization": "q_bar"}]
        self.song.is_playing = True
        for envelope in bad:
            with self.subTest(envelope=envelope), self.assertRaises(MeterProbeError):
                probe.dispatch(envelope)
        with self.assertRaises(MeterProbeError): self.start(quantization="q_no_q")
        with self.assertRaises(MeterProbeError): self.start(expected_target=float("inf"))
        self.assertEqual(self.calls, [])
        probe.dispatch({"op": "set_metronome", "enabled": True})
        self.assertIs(self.song.metronome, True)


if __name__ == "__main__":
    unittest.main()
