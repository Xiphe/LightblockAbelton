"""Prepared audio-fixture guards and observed clock values, without Live."""

import hashlib
from enum import IntEnum
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.audio_fixture import AudioFixture, FixtureError
from LightpadController.timing import ONE_BAR, REFERENCE


Q_BAR = 4
OFF = 2


def route(name):
    return SimpleNamespace(display_name=name)


class Track:
    def __init__(self, name, parent=None, group=False, midi=False):
        self.name, self.group_track = name, parent
        self.is_foldable, self.is_frozen = group, False
        self.has_audio_input, self.has_midi_input = not midi, midi
        self.current_monitoring_state = OFF
        self.input_routing_type = route("Ext. In")
        self.input_routing_channel = route("1 T-Bone (1)")
        self.output_routing_type = route(parent.name if parent else "Main")
        self.can_be_armed, self.arm, self.implicit_arm = not group, False, False
        self.mute, self.volume = False, 0.75
        self.clip_slots = [SimpleNamespace(has_clip=False) for _ in range(8)]


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lightpad-audio-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.saved = Path(self.temp.name) / "Full Audio Trial.als"
        self.saved.write_bytes(b"temporary fixture bytes")

    def build(self, groups=2, layers=2, numerator=4, denominator=4, origin=0.0, proof_names=False):
        tracks = [Track("1-MIDI", midi=True), Track("2-MIDI", midi=True),
                  Track("3-Audio"), Track("4-Audio")]
        specifications = []
        for index in range(groups):
            group = Track("Lightpad %d" % (index + 1), group=True)
            tracks.append(group)
            names = ["Proof " + letter for letter in "ABC"] if proof_names else [
                "Layer %d" % (layer + 1) for layer in range(layers)]
            tracks.extend(Track(name, group) for name in names)
            if proof_names:
                tracks.append(Track("Proof Monitor", group))
            specifications.append({"index": index, "name": group.name, "layer_names": names})
        song = SimpleNamespace(file_path=str(self.saved), tracks=tracks,
            record_mode=False, overdub=False, session_record=False, loop=False,
            is_counting_in=False, count_in_duration=0, clip_trigger_quantization=Q_BAR,
            is_ableton_link_enabled=False, tempo_follower_enabled=False, external_sync=False,
            signature_numerator=numerator, signature_denominator=denominator,
            tempo=120.0, current_song_time=0.0, is_playing=False)
        manifest = {"set_path": str(self.saved),
            "set_sha256": hashlib.sha256(self.saved.read_bytes()).hexdigest(),
            "groups": specifications, "bar_grid": {"origin": origin, "numerator": numerator,
                "denominator": denominator, "fixed": True},
            "capture_input_channel": "1", "sync_ui_confirmed_off": ["external_sync"]}
        return song, manifest

    def fixture(self, **kwargs):
        song, manifest = self.build(**kwargs)
        return AudioFixture(song, manifest, Q_BAR, OFF)

    def test_all_eight_groups_resolve_same_named_layers_within_each_native_parent(self):
        fixture = self.fixture(groups=8, layers=8)
        self.assertEqual(len(fixture.groups), 8)
        self.assertTrue(fixture.validate())
        for index, binding in fixture.groups.items():
            self.assertEqual(binding.group.name, "Lightpad %d" % (index + 1))
            self.assertEqual(len(binding.layers), 8)
            self.assertEqual(binding.layers[0].name, "Layer 1")
            self.assertIs(binding.layers[0].group_track, binding.group)
        self.assertIsNot(fixture.groups[0].layers[0], fixture.groups[1].layers[0])

    def test_explicit_one_group_fixture_ignores_but_never_allows_arming_monitor(self):
        fixture = self.fixture(groups=1, proof_names=True)
        self.assertEqual([t.name for t in fixture.groups[0].layers], ["Proof A", "Proof B", "Proof C"])
        monitor = next(t for t in fixture.song.tracks if t.name == "Proof Monitor")
        monitor.arm = True
        with self.assertRaises(FixtureError):
            fixture.validate()
        with self.assertRaises(FixtureError):
            fixture.validate(allowed_armed=(monitor,))

    def test_hash_verified_once_same_path_save_allowed_and_reinitialization_needs_updated_manifest(self):
        song, manifest = self.build()
        fixture = AudioFixture(song, manifest, Q_BAR, OFF)
        self.saved.write_bytes(b"newly saved accepted take metadata")
        self.assertTrue(fixture.validate())
        with self.assertRaises(FixtureError):
            AudioFixture(song, manifest, Q_BAR, OFF)
        manifest["set_sha256"] = hashlib.sha256(self.saved.read_bytes()).hexdigest()
        self.assertTrue(AudioFixture(song, manifest, Q_BAR, OFF).validate())

    def test_other_set_path_and_replaced_runtime_tracks_are_rejected(self):
        fixture = self.fixture()
        fixture.song.file_path = str(Path(self.temp.name) / "Original.als")
        with self.assertRaises(FixtureError):
            fixture.validate()
        fixture.song.file_path = str(self.saved)
        fixture.song.tracks[0] = Track("1-MIDI", midi=True)
        with self.assertRaises(FixtureError):
            fixture.validate()

    def test_explicit_non_four_four_grid_controls_global_phase_and_next_bar(self):
        fixture = self.fixture(numerator=7, denominator=8, origin=1.25)
        fixture.song.current_song_time = 3.0
        fixture.song.tempo = 137.25
        clock = fixture.global_clock()
        self.assertEqual((clock.context, clock.take_id), (ONE_BAR, 0))
        self.assertEqual((clock.numerator, clock.denominator), (7, 8))
        self.assertEqual(clock.bar_length, 3.5)
        self.assertEqual(clock.span, 3.5)
        self.assertEqual(clock.position, 1.75)
        self.assertEqual(clock.tempo, 137.25)
        self.assertFalse(clock.running)
        self.assertEqual(fixture.next_bar(at=4.75, inclusive=True), 4.75)
        self.assertEqual(fixture.next_bar(at=4.75), 8.25)

    def test_missing_grid_and_changed_native_meter_never_get_default_four_four(self):
        song, manifest = self.build()
        del manifest["bar_grid"]
        with self.assertRaises(FixtureError):
            AudioFixture(song, manifest, Q_BAR, OFF)
        fixture = self.fixture()
        fixture.song.signature_numerator = 3
        for operation in (fixture.validate, fixture.global_clock, lambda: fixture.bar_length):
            with self.assertRaises(FixtureError):
                operation()

    def test_native_bar_clock_opt_in_corroborates_changed_meter_without_changing_reference_length(self):
        song, manifest = self.build()
        song.current_song_time = 12.25
        song.get_current_beats_song_time = lambda: SimpleNamespace(
            bars=4, beats=4, sub_division=2, ticks=1)
        fixture = AudioFixture(song, manifest, Q_BAR, OFF, native_bar_clock=True)
        song.signature_numerator, song.signature_denominator = 7, 8
        self.assertTrue(fixture.validate())
        self.assertEqual(fixture.next_bar(), 14.0)
        clock = fixture.global_clock()
        self.assertEqual((clock.span, clock.position, clock.numerator, clock.denominator),
                         (3.5, 1.75, 7, 8))
        clip = SimpleNamespace(is_audio_clip=True, warping=True, looping=True,
            is_playing=True, loop_start=0.0, loop_end=12.0, start_time=8.0,
            playing_position=4.25)
        reference = fixture.reference_clock(clip)
        self.assertEqual((reference.span, reference.position), (12.0, 4.25))
        self.assertEqual((reference.first_bar, reference.bar_length), (2.5, 3.5))
        self.assertEqual(fixture.next_wrap(clip), 20.0)
        song.get_current_beats_song_time = lambda: SimpleNamespace(
            bars=4, beats=3, sub_division=2, ticks=1)
        with self.assertRaises(FixtureError):
            fixture.global_clock()

    def test_native_launch_window_keeps_absolute_reference_target_on_changed_meter(self):
        song, manifest = self.build()
        song.signature_numerator, song.signature_denominator = 7, 8
        song.current_song_time = 11.25
        song.get_current_beats_song_time = lambda: SimpleNamespace(
            bars=4, beats=2, sub_division=2, ticks=1)
        fixture = AudioFixture(song, manifest, Q_BAR, OFF, native_bar_clock=True,
            native_quantizations={'q_bar': 4, 'q_half': 5, 'q_quarter': 7,
                                  'q_eight': 9, 'q_sixtenth': 11})
        window = fixture.launch_window(12.0)
        self.assertEqual((window.previous, window.target, window.quantization), (10.0, 12.0, 5))
        self.assertEqual(fixture.launch_window(14.0).quantization, Q_BAR)

    def test_capture_route_parent_type_and_monitor_guards_are_read_only(self):
        changes = [("input_routing_type", route("Resampling")),
                   ("input_routing_channel", route("2 SM-58 (2)")),
                   ("output_routing_type", route("Main")),
                   ("current_monitoring_state", 0), ("is_frozen", True), ("has_midi_input", True)]
        for name, value in changes:
            fixture = self.fixture()
            layer = fixture.groups[0].layers[0]
            setattr(layer, name, value)
            with self.subTest(property=name), self.assertRaises(FixtureError):
                fixture.validate()
            self.assertEqual(getattr(layer, name), value)
            self.assertFalse(layer.arm)

    def test_only_explicit_owned_arms_allowed_and_implicit_arm_always_off(self):
        fixture = self.fixture()
        owned = fixture.groups[0].layers[0]
        owned.arm = True
        with self.assertRaises(FixtureError):
            fixture.validate()
        self.assertTrue(fixture.validate(allowed_armed=(owned,)))
        self.assertEqual(fixture.global_clock().context, ONE_BAR)
        owned.implicit_arm = True
        with self.assertRaises(FixtureError):
            fixture.validate(allowed_armed=(owned,))
        owned.implicit_arm = False
        fixture.song.tracks[0].arm = True
        with self.assertRaises(FixtureError):
            fixture.validate(allowed_armed=(owned,))

    def test_record_mode_countin_loop_and_native_quantization_guards(self):
        for name, value in (("record_mode", True), ("loop", True), ("is_counting_in", True),
                            ("count_in_duration", 1), ("clip_trigger_quantization", 0)):
            fixture = self.fixture()
            setattr(fixture.song, name, value)
            with self.subTest(property=name), self.assertRaises(FixtureError):
                fixture.validate()
            self.assertEqual(getattr(fixture.song, name), value)

    def test_native_count_in_enum_zero_is_supported_without_accepting_boolean_false(self):
        class NativeCountIn(IntEnum):
            NONE = 0
            ONE_BAR = 1
        fixture = self.fixture()
        fixture.song.count_in_duration = NativeCountIn.NONE
        self.assertTrue(fixture.validate())
        self.assertIs(fixture.song.count_in_duration, NativeCountIn.NONE)
        self.assertEqual(fixture.global_clock().context, ONE_BAR)
        json.dumps(fixture.report(), allow_nan=False)
        for invalid in (False, True, NativeCountIn.ONE_BAR, 0.0, "0"):
            fixture.song.count_in_duration = invalid
            with self.subTest(value=invalid), self.assertRaises(FixtureError):
                fixture.validate()

    def test_sync_ui_evidence_covers_missing_external_only_not_native_on_or_record_mode(self):
        fixture = self.fixture()
        del fixture.song.external_sync
        self.assertTrue(fixture.validate())
        fixture.song.external_sync = True
        with self.assertRaises(FixtureError):
            fixture.validate()
        fixture.song.external_sync = False
        del fixture.song.tempo_follower_enabled
        with self.assertRaises(FixtureError):
            fixture.validate()
        fixture.song.tempo_follower_enabled = False
        del fixture.song.record_mode
        with self.assertRaises(FixtureError):
            fixture.validate()

    def test_overdub_session_record_and_tempo_can_change_without_becoming_fixed_baselines(self):
        fixture = self.fixture()
        before = fixture.fingerprint()
        fixture.song.overdub = fixture.song.session_record = True
        fixture.song.tempo = 155.5
        fixture.groups[0].group.mute = True
        fixture.groups[0].layers[0].volume = 0.1
        fixture.groups[0].layers[0].clip_slots[0].has_clip = True
        self.assertTrue(fixture.validate())
        self.assertEqual(fixture.fingerprint(), before)
        report = fixture.report()
        self.assertTrue(report["ready"])
        self.assertTrue(report["globals"]["session_record"])
        self.assertTrue(report["globals"]["overdub"])
        self.assertEqual(report["tempo"], 155.5)
        json.dumps(report, allow_nan=False)

    def test_reference_clock_uses_actual_warped_loop_phase_and_native_anchor(self):
        fixture = self.fixture()
        fixture.song.current_song_time = 7.0
        clip = SimpleNamespace(is_audio_clip=True, warping=True, looping=True, is_playing=True,
                               loop_start=0.0, loop_end=8.0, playing_position=2.0, start_time=5.0)
        clock = fixture.reference_clock(clip, take_id=7)
        self.assertEqual((clock.context, clock.take_id), (REFERENCE, 7))
        self.assertEqual((clock.span, clock.position, clock.first_bar), (8.0, 2.0, 3.0))
        self.assertEqual(fixture.next_wrap(clip, at=13.0, inclusive=True), 13.0)
        self.assertEqual(fixture.next_wrap(clip, at=13.0), 21.0)
        self.assertEqual(fixture.next_wrap(clip, at=11.5), 13.0)

    def test_reference_first_bar_offset_tracks_current_non_whole_bar_loop_cycle(self):
        fixture = self.fixture()
        clip = SimpleNamespace(is_audio_clip=True, warping=True, looping=True, is_playing=True,
                               loop_start=0.0, loop_end=6.0, playing_position=2.0, start_time=5.0)
        fixture.song.current_song_time = 7.0
        self.assertEqual(fixture.reference_clock(clip).first_bar, 3.0)
        fixture.song.current_song_time = 13.0
        self.assertEqual(fixture.reference_clock(clip).first_bar, 1.0)

    def test_unsupported_reference_data_raises_without_fake_units_or_anchor(self):
        for name, value in (("warping", False), ("looping", False), ("is_playing", False),
                            ("loop_start", 1.0), ("loop_end", 0.0), ("playing_position", 8.0)):
            fixture = self.fixture()
            clip = SimpleNamespace(is_audio_clip=True, warping=True, looping=True, is_playing=True,
                                   loop_start=0.0, loop_end=8.0, playing_position=2.0, start_time=0.0)
            setattr(clip, name, value)
            with self.subTest(property=name), self.assertRaises(FixtureError):
                fixture.reference_clock(clip)
        del clip.start_time
        with self.assertRaises(FixtureError):
            fixture.next_wrap(clip)

    def test_report_returns_guard_reason_instead_of_claiming_unready_capability(self):
        fixture = self.fixture()
        fixture.song.record_mode = True
        report = fixture.report()
        self.assertFalse(report["ready"])
        self.assertIn("Arrangement Record", report["guard_reasons"][0])
        self.assertFalse(report["capabilities"]["prepared_audio_fixture"])
        self.assertFalse(report["capabilities"]["gapless_chaining_proven"])
        self.assertTrue(report["globals"]["record_mode"])

    def test_report_preserves_configured_identity_when_runtime_group_is_unavailable(self):
        fixture = self.fixture()
        class LostTrack(Track):
            def __getattribute__(self, name):
                if name == "name":
                    raise RuntimeError("removed native object")
                return super().__getattribute__(name)
        fixture.groups[0].group.__class__ = LostTrack
        report = fixture.report()
        self.assertFalse(report["ready"])
        self.assertIn("unavailable", report["guard_reasons"][0])
        self.assertEqual(report["groups"][0]["name"], "Lightpad 1")
        json.dumps(report, allow_nan=False)

    def test_unusable_native_values_remain_json_safe_and_arithmetic_errors_are_fixture_errors(self):
        fixture = self.fixture()
        fixture.song.overdub = float("nan")
        report = fixture.report()
        self.assertFalse(report["ready"])
        self.assertEqual(report["globals"]["overdub"], {"unavailable": "UnsupportedJSONValue"})
        json.dumps(report, allow_nan=False)
        fixture.song.overdub = False
        fixture.song.current_song_time = 1e308
        clip = SimpleNamespace(is_audio_clip=True, warping=True, looping=True, is_playing=True,
                               loop_start=0.0, loop_end=1e-308, playing_position=0.0, start_time=0.0)
        with self.assertRaises(FixtureError):
            fixture.reference_clock(clip)


if __name__ == "__main__":
    unittest.main()
