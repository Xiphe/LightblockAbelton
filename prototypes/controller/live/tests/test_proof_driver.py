"""Guarded native-proof callback paths, with explicit fake Live transitions.

No Live import, MIDI endpoint, project or user fixture is opened. These tests
verify mutation scope and observation rules, not Live recording or timing.
"""

import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.proof_driver import AudioProofDriver, ProofRefused


OFF = 2
Q_BAR = object()


def route(name):
    return SimpleNamespace(display_name=name, identifier=name)


class Clip:
    def __init__(self, recording=False):
        self.is_recording = recording
        self.is_playing = True
        self.playing_position = 0.0
        self.start_time = 4.0
        self.end_time = 16.0
        self.loop_start = 0.0
        self.loop_end = 12.0
        self.length = 12.0
        self.warping = True
        self.file_path = "/private/tmp/fake-proof-capture.wav"


class Slot:
    def __init__(self, track, index):
        self.track, self.index = track, index
        self.clip = None
        self.is_triggered = False
        self.calls = []

    @property
    def has_clip(self):
        return self.clip is not None

    @property
    def is_recording(self):
        return self.has_clip and self.clip.is_recording

    def fire(self, **kwargs):
        self.calls.append(kwargs)
        self.track.mutations.append(("fire", self.track.name, self.index, kwargs))
        self.is_triggered = True
        self.track.fired_slot_index = self.index

    def observe_recording(self):
        self.clip = Clip(recording=True)
        self.is_triggered = False
        self.track.playing_slot_index = self.index
        self.track.fired_slot_index = -1
        return self.clip

    def observe_finished(self):
        self.clip.is_recording = False
        self.is_triggered = False
        self.track.fired_slot_index = -1


class Track:
    def __init__(self, name, kind, mutations, parent=None):
        self.name, self.group_track, self.kind = name, parent, kind
        self.is_foldable = kind == "GroupTrack"
        self.has_midi_input = kind == "MidiTrack"
        self.is_frozen = False
        self.devices = []
        self.can_be_armed = not self.is_foldable
        self._arm = False
        self.implicit_arm = False
        self.mute = False
        self.playing_slot_index = -1
        self.fired_slot_index = -1
        self.current_monitoring_state = OFF
        self.input_routing_type = route("Ext. In")
        self.input_routing_channel = route("1 T-Bone (1)")
        self.output_routing_type = route(parent.name if parent else "Main")
        self.mutations = mutations
        self.clip_slots = [Slot(self, index) for index in range(8)]

    @property
    def arm(self):
        return self._arm

    @arm.setter
    def arm(self, value):
        self.mutations.append(("arm", self.name, value))
        self._arm = value


class ProofDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lightpad-native-proof-")
        self.addCleanup(self.temp.cleanup)
        self.saved = Path(self.temp.name) / "Lightpad Audio Proof.als"
        self.saved.write_bytes(b"offline fixture bytes only")
        self.mutations, self.logs = [], []
        self.tracks = [Track("1-MIDI", "MidiTrack", self.mutations),
                       Track("2-MIDI", "MidiTrack", self.mutations),
                       Track("3-Audio", "AudioTrack", self.mutations),
                       Track("4-Audio", "AudioTrack", self.mutations),
                       Track("Lightpad 1", "GroupTrack", self.mutations)]
        group = self.tracks[4]
        self.tracks.extend(Track(name, "AudioTrack", self.mutations, parent=group)
                           for name in ("Proof A", "Proof B", "Proof C", "Proof Monitor"))
        self.song = SimpleNamespace(tracks=self.tracks, scenes=[object() for _ in range(8)],
            file_path=str(self.saved), is_playing=False, tempo=120,
            signature_numerator=4, signature_denominator=4, current_song_time=3.5,
            record_mode=False, loop=False, is_counting_in=False, count_in_duration=0,
            overdub=False, session_record=False, is_ableton_link_enabled=False,
            tempo_follower_enabled=False, external_sync=False, clip_trigger_quantization=Q_BAR)
        self.manifest = {"set_path": str(self.saved),
            "set_sha256": hashlib.sha256(self.saved.read_bytes()).hexdigest(),
            "scene_count": 8, "owned_group": "Lightpad 1",
            "owned_capture_tracks": ["Proof A", "Proof B", "Proof C"],
            "excluded_monitor": "Proof Monitor", "tracks": []}
        for index, track in enumerate(self.tracks):
            self.manifest["tracks"].append({"native_track_index": index,
                "als_track_id": str(index), "name": track.name, "type": track.kind,
                "parent_als_group_id": "4" if track.group_track else "-1", "device_count": 0})
        self.driver = AudioProofDriver(self.song, self.manifest, Q_BAR, OFF, self.logs.append)
        self.addCleanup(self.driver.close)

    @property
    def a(self):
        return self.tracks[5]

    def enable(self):
        inspection = self.driver.inspect()
        return self.driver.dispatch({"op": "enable", "confirm_set_path": str(self.saved),
                                    "confirm_fingerprint": inspection["fingerprint"]})

    def start(self):
        self.enable()
        self.song.is_playing = True
        return self.driver.dispatch({"op": "start", "layer": "A"})

    def record(self):
        self.start()
        clip = self.a.clip_slots[0].observe_recording()
        self.driver.poll()
        self.assertEqual(self.driver.active["state"], "recording")
        return clip

    def refuse_without_writes(self, command):
        before = list(self.mutations)
        with self.assertRaises(ProofRefused):
            self.driver.dispatch(command)
        self.assertEqual(self.mutations, before)

    def test_construction_inspection_poll_and_enable_never_fire_or_arm(self):
        self.assertFalse(self.driver.enabled)
        self.driver.inspect()
        self.driver.poll()
        self.refuse_without_writes({"op": "start", "layer": "A"})
        result = self.enable()
        self.driver.poll()
        self.assertTrue(result["enabled"])
        self.assertTrue(result["timing_only"])
        self.assertEqual(self.mutations, [])
        self.assertFalse(self.song.is_playing)

    def test_enable_requires_both_operator_confirmations(self):
        correct = {"op": "enable", "confirm_set_path": str(self.saved),
                   "confirm_fingerprint": self.driver.inspect()["fingerprint"]}
        for change in ({"confirm_set_path": "/private/tmp/another.als"},
                       {"confirm_fingerprint": "unreviewed"}):
            self.refuse_without_writes(dict(correct, **change))
            self.assertFalse(self.driver.enabled)

    def test_native_parent_group_route_label_is_required(self):
        inspection = self.driver.inspect()
        self.assertEqual(inspection["fixture"]["tracks"][5]["output_type"]["display_name"], "Lightpad 1")
        self.a.output_routing_type = route("Group")
        self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
            "confirm_fingerprint": self.driver.inspect()["fingerprint"]})

    def test_saved_original_unsaved_set_and_hash_change_are_refused(self):
        correct_path = self.song.file_path
        for changed_path in ("", str(Path(self.temp.name) / "original.als")):
            self.song.file_path = changed_path
            self.refuse_without_writes({"op": "enable", "confirm_set_path": correct_path,
                                       "confirm_fingerprint": self.driver.inspect()["fingerprint"]})
        self.song.file_path = correct_path
        self.enable()
        self.song.is_playing = True
        self.saved.write_bytes(b"changed saved fixture")
        self.refuse_without_writes({"op": "start", "layer": "A"})

    def test_enable_refuses_preexisting_clips_in_original_or_owned_tracks(self):
        for track in (self.tracks[0], self.a):
            track.clip_slots[3].clip = Clip()
            self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
                "confirm_fingerprint": self.driver.inspect()["fingerprint"]})
            self.assertFalse(self.driver.enabled)
            track.clip_slots[3].clip = None

    def test_start_waits_for_observation_and_finish_waits_for_native_playback(self):
        result = self.start()
        slot = self.a.clip_slots[0]
        self.assertEqual(result["active"]["state"], "queued")
        self.assertEqual(result["active"]["target_beat"], 4.0)
        self.assertEqual(slot.calls, [{"launch_quantization": Q_BAR}])
        self.assertTrue(self.a.arm)
        self.assertTrue(all(not track.arm for track in self.tracks if track is not self.a))
        self.refuse_without_writes({"op": "finish"})
        clip = slot.observe_recording()
        self.driver.poll()
        self.assertIs(self.driver.active["clip"], clip)
        self.song.current_song_time = 14.5
        result = self.driver.dispatch({"op": "finish"})
        self.assertEqual(result["active"]["state"], "finish_queued")
        self.assertEqual(result["active"]["target_beat"], 16.0)
        self.assertEqual(slot.calls, [{"launch_quantization": Q_BAR}] * 2)
        self.driver.poll()
        self.assertEqual(self.driver.accepted, {})
        self.assertTrue(self.a.arm)
        slot.observe_finished()
        self.driver.poll()
        self.assertIsNone(self.driver.active)
        self.assertIs(self.driver.accepted["A"], clip)
        self.assertFalse(self.a.arm)
        self.assertTrue(self.song.is_playing)
        self.assertTrue(clip.is_playing)
        self.assertEqual([item[:2] for item in self.mutations],
                         [("arm", "Proof A"), ("fire", "Proof A"),
                          ("fire", "Proof A"), ("arm", "Proof A")])

    def test_native_session_audio_recording_overdub_transition_is_observed(self):
        self.start()
        clip = self.a.clip_slots[0].observe_recording()
        # Actual first native run: both flags changed at audio recording start,
        # while Arrangement Record remained Off and only Proof A was armed.
        self.song.session_record = True
        self.song.overdub = True
        self.driver.poll()
        self.assertIsNone(self.driver.fault)
        self.assertEqual(self.driver.active["state"], "recording")
        self.assertIs(self.driver.active["clip"], clip)
        self.assertFalse(self.song.record_mode)

    def test_finish_and_disable_before_actual_recording_do_not_touch_clips(self):
        self.start()
        self.refuse_without_writes({"op": "finish"})
        self.refuse_without_writes({"op": "disable"})
        self.assertEqual(self.driver.active["state"], "queued")
        self.assertEqual(len(self.a.clip_slots[0].calls), 1)
        self.assertTrue(self.a.arm)

    def test_unrelated_and_implicit_arm_prevent_start(self):
        self.enable()
        self.song.is_playing = True
        for track, property_name in ((self.tracks[0], "arm"), (self.a, "implicit_arm")):
            setattr(track, property_name, True)
            self.refuse_without_writes({"op": "start", "layer": "A"})
            setattr(track, property_name, False)
        self.assertEqual(self.a.clip_slots[0].calls, [])

    def test_transport_meter_and_route_changes_prevent_mutation(self):
        self.enable()
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.song.is_playing = True
        self.song.signature_numerator = 3
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.song.signature_numerator = 4
        self.a.input_routing_channel = route("2 SM-58 (2)")
        self.refuse_without_writes({"op": "start", "layer": "A"})

    def test_same_named_replacement_track_and_reparenting_are_refused(self):
        self.enable()
        self.song.is_playing = True
        original = self.tracks[5]
        self.tracks[5] = Track("Proof A", "AudioTrack", self.mutations, self.tracks[4])
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.tracks[5] = original
        original.group_track = None
        self.refuse_without_writes({"op": "start", "layer": "A"})

    def test_same_slot_replacement_clip_is_never_finished_or_accepted(self):
        old_clip = self.record()
        self.a.clip_slots[0].clip = Clip(recording=True)
        self.refuse_without_writes({"op": "finish"})
        self.driver.poll()
        self.assertIsNotNone(self.driver.fault)
        self.assertEqual(self.driver.accepted, {})
        self.assertIs(self.driver.active["clip"], old_clip)

    def test_replaced_slot_with_same_clip_cannot_fire_retained_stale_slot(self):
        clip = self.record()
        old_slot = self.a.clip_slots[0]
        replacement = Slot(self.a, 0)
        replacement.clip = clip
        self.a.clip_slots[0] = replacement
        self.refuse_without_writes({"op": "finish"})
        self.assertEqual(len(old_slot.calls), 1)
        self.assertEqual(replacement.calls, [])

    def test_unrequested_playback_does_not_accept_a_take(self):
        self.record()
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.assertEqual(self.driver.accepted, {})
        self.assertIsNotNone(self.driver.active)
        self.assertTrue(self.a.arm)

    def test_finish_rechecks_native_recording_before_refiring_cached_take(self):
        self.record()
        self.a.clip_slots[0].observe_finished()
        # The native state changed after the last display poll. A stale cached
        # recording state must not relaunch this now-playing clip.
        self.refuse_without_writes({"op": "finish"})
        self.assertEqual(len(self.a.clip_slots[0].calls), 1)

    def test_unknown_clip_and_accepted_slot_cannot_be_overwritten(self):
        clip = self.record()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.assertIs(self.driver.accepted["A"], clip)
        self.tracks[6].clip_slots[0].clip = Clip()
        self.refuse_without_writes({"op": "start", "layer": "C"})

    def test_mute_and_monitoring_survive_recording_and_disable(self):
        self.tracks[4].mute = True
        self.record()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.driver.dispatch({"op": "disable"})
        self.assertFalse(self.driver.enabled)
        self.assertTrue(self.tracks[4].mute)
        self.assertTrue(all(t.current_monitoring_state == OFF for t in self.tracks))
        self.assertEqual(self.a.clip_slots[0].calls, [{"launch_quantization": Q_BAR}] * 2)

    def test_native_fire_exception_faults_without_retrying_or_auto_cleanup(self):
        self.enable()
        self.song.is_playing = True
        calls = []
        def fail(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("native signature rejected")
        self.a.clip_slots[0].fire = fail
        with self.assertRaises(RuntimeError):
            self.driver.dispatch({"op": "start", "layer": "A"})
        self.assertEqual(calls, [{"launch_quantization": Q_BAR}])
        self.assertIsNotNone(self.driver.fault)
        self.assertTrue(self.a.arm)
        self.refuse_without_writes({"op": "start", "layer": "B"})
        self.driver.close()
        self.assertTrue(self.a.arm)

    def test_arrangement_record_refuses_enable_before_any_arm_or_fire(self):
        self.song.record_mode = True
        self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
            "confirm_fingerprint": self.driver.inspect()["fingerprint"]})
        self.assertFalse(self.driver.enabled)
        self.assertEqual(self.mutations, [])

    def test_arrangement_record_becoming_true_refuses_start_and_finish(self):
        self.enable()
        self.song.is_playing = True
        self.song.record_mode = True
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.song.record_mode = False
        self.driver.dispatch({"op": "start", "layer": "A"})
        self.a.clip_slots[0].observe_recording()
        self.driver.poll()
        self.song.record_mode = True
        self.refuse_without_writes({"op": "finish"})
        self.assertEqual(len(self.a.clip_slots[0].calls), 1)

    def test_arrangement_record_becoming_true_prevents_poll_disarm(self):
        self.record()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.song.record_mode = True
        before = list(self.mutations)
        self.driver.poll()
        self.assertEqual(self.mutations, before)
        self.assertTrue(self.a.arm)
        self.assertIsNotNone(self.driver.active)
        self.assertIsNotNone(self.driver.fault)
        self.assertEqual(self.driver.accepted, {})
        inspection = self.driver.inspect()
        self.assertTrue(inspection["globals"]["record_mode"])
        self.assertEqual(inspection["active"]["native_clip"]["is_recording"], False)
        self.assertEqual(inspection["active"]["native_clip"]["is_playing"], True)

    def test_unknown_arrangement_record_blocks_even_with_ui_confirmation(self):
        del self.song.record_mode
        self.assertEqual(self.driver.inspect()["globals"]["record_mode"],
                         {"unavailable": "AttributeError"})
        self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
            "confirm_fingerprint": self.driver.inspect()["fingerprint"],
            "confirm_sync_ui_off": ["link", "tempo_follower", "external_sync"]})
        self.assertFalse(self.driver.enabled)

    def test_loop_countin_and_session_record_refuse_enable_without_changing_flags(self):
        for name, value in (("loop", True), ("is_counting_in", True),
                            ("count_in_duration", 1), ("session_record", True)):
            with self.subTest(name=name):
                old = getattr(self.song, name)
                setattr(self.song, name, value)
                self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
                    "confirm_fingerprint": self.driver.inspect()["fingerprint"]})
                self.assertEqual(getattr(self.song, name), value)
                setattr(self.song, name, old)
        self.assertFalse(self.driver.enabled)

    def test_sync_flags_must_be_off_and_cannot_be_overridden_when_readable(self):
        names = ("is_ableton_link_enabled", "tempo_follower_enabled", "external_sync")
        for name in names:
            with self.subTest(name=name):
                setattr(self.song, name, True)
                self.refuse_without_writes({"op": "enable", "confirm_set_path": str(self.saved),
                    "confirm_fingerprint": self.driver.inspect()["fingerprint"],
                    "confirm_sync_ui_off": ["link", "tempo_follower", "external_sync"]})
                self.assertTrue(getattr(self.song, name))
                setattr(self.song, name, False)

    def test_missing_sync_flag_requires_its_explicit_ui_confirmation(self):
        del self.song.tempo_follower_enabled
        command = {"op": "enable", "confirm_set_path": str(self.saved),
                   "confirm_fingerprint": self.driver.inspect()["fingerprint"]}
        self.refuse_without_writes(command)
        self.refuse_without_writes(dict(command,
            confirm_sync_ui_off=["link", "external_sync"]))
        inspection = self.driver.inspect()
        self.assertEqual(inspection["globals"]["tempo_follower_enabled"],
                         {"unavailable": "AttributeError"})
        result = self.driver.dispatch(dict(command, confirm_sync_ui_off=["tempo_follower"]))
        self.assertTrue(result["enabled"])
        self.assertEqual(result["sync_ui_confirmed_off"], ["tempo_follower"])
        self.assertEqual(self.mutations, [])

    def test_audio_overdub_true_is_preserved_but_baseline_changes_block_start(self):
        self.song.overdub = True
        self.enable()
        self.song.is_playing = True
        self.song.overdub = False
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.song.overdub = True
        self.driver.dispatch({"op": "start", "layer": "A"})
        self.a.clip_slots[0].observe_recording()
        self.driver.poll()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.assertTrue(self.song.overdub)
        self.assertIsNone(self.driver.fault)
        self.assertEqual(sorted(self.driver.accepted), ["A"])

    def test_session_record_must_be_off_before_start_but_native_changes_allowed_during_take(self):
        self.enable()
        self.song.is_playing = True
        self.song.session_record = True
        self.refuse_without_writes({"op": "start", "layer": "A"})
        self.song.session_record = False
        self.driver.dispatch({"op": "start", "layer": "A"})
        self.song.session_record = True
        self.a.clip_slots[0].observe_recording()
        self.driver.poll()
        self.assertIsNone(self.driver.fault)
        self.assertEqual(self.driver.active["state"], "recording")
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.assertIsNone(self.driver.fault)
        self.assertEqual(sorted(self.driver.accepted), ["A"])
        self.assertTrue(self.song.session_record)


if __name__ == "__main__":
    unittest.main()
