"""Manifest normalization uses only explicit local preparation evidence."""

import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.audio_configuration import ConfigurationError, load_audio_configuration


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lightpad-audio-configuration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.saved = self.root / "Controller Trial.als"
        self.path = self.root / "fixture-manifest.json"

    def write_set(self, events=((4, 4, 0),)):
        body = "".join('<RemoteableTimeSignature><Numerator Value="%s"/>'
                       '<Denominator Value="%s"/><Time Value="%s"/></RemoteableTimeSignature>' % event
                       for event in events)
        self.saved.write_bytes(gzip.compress(("<Ableton><LiveSet><TimeSignatures>" + body
                                             + "</TimeSignatures></LiveSet></Ableton>").encode()))

    def manifest(self, groups=8, layers=8, meter=(4, 4)):
        self.write_set(((meter[0], meter[1], 0),))
        value = {"set_path": str(self.saved),
            "set_sha256": hashlib.sha256(self.saved.read_bytes()).hexdigest(),
            "group_names": ["Lightpad %d" % (index + 1) for index in range(groups)],
            "owned_layer_names": ["Layer %d" % (index + 1) for index in range(layers)],
            "transport_observed_ui": {"meter": list(meter)}, "tracks": []}
        for index, name in enumerate(value["group_names"]):
            parent_id = str(index * (layers + 1))
            value["tracks"].append({"als_track_id": parent_id, "type": "GroupTrack", "name": name,
                                    "parent_als_group_id": "-1"})
            value["tracks"].extend({"als_track_id": "%s-%s" % (index, number), "type": "AudioTrack",
                "name": layer, "parent_als_group_id": parent_id, "input_target": "AudioIn/External/M0"}
                for number, layer in enumerate(value["owned_layer_names"]))
        return value

    def write_manifest(self, value):
        self.path.write_text(json.dumps(value))

    def test_flat_parent_manifest_with_72_tracks_has_scoped_layer_groups_and_no_rewrite(self):
        value = self.manifest()
        self.assertEqual(len(value["tracks"]), 72)
        self.write_manifest(value)
        before_manifest, before_set = self.path.read_bytes(), self.saved.read_bytes()
        result = load_audio_configuration(self.path)
        self.assertEqual(len(result["groups"]), 8)
        self.assertEqual(result["groups"][7], {"index": 7, "name": "Lightpad 8",
            "layer_names": ["Layer %d" % n for n in range(1, 9)]})
        self.assertEqual(result["capture_input_channel"], "1")
        self.assertEqual(result["bar_grid"]["origin"], 0.0)
        self.assertEqual(result["bar_grid"]["numerator"], 4)
        self.assertTrue(result["bar_grid"]["fixed"])
        self.assertEqual(result["bar_grid"]["provenance"]["event_count"], 1)
        self.assertEqual(result["normalization_provenance"]["capture_input_channel"]["owned_track_count"], 64)
        self.assertEqual(result["validated_features"], [])
        self.assertEqual(self.path.read_bytes(), before_manifest)
        self.assertEqual(self.saved.read_bytes(), before_set)

    def test_seven_eight_single_zero_event_supplies_exact_meter_not_four_four(self):
        self.write_manifest(self.manifest(meter=(7, 8)))
        result = load_audio_configuration(self.path)
        self.assertEqual((result["bar_grid"]["numerator"], result["bar_grid"]["denominator"]), (7, 8))
        self.assertEqual(result["bar_grid"]["provenance"]["recorded_transport_meter"], [7, 8])

    def test_missing_multiple_or_fragmentary_signature_events_are_rejected(self):
        value = self.manifest()
        self.write_manifest(value)
        for events in ((), ((4, 4, 0), (3, 4, 8)), ((7, 8, 1.5),)):
            with self.subTest(events=events):
                self.write_set(events)
                with self.assertRaises(ConfigurationError):
                    load_audio_configuration(self.path)

    def test_invalid_or_ui_mismatched_meter_is_rejected(self):
        self.write_manifest(self.manifest())
        for event in ((0, 4, 0), (7, 3, 0), (7, 8, 0), (4, 4, "nan")):
            self.write_set((event,))
            with self.subTest(event=event), self.assertRaises(ConfigurationError):
                load_audio_configuration(self.path)

    def test_missing_recorded_transport_meter_is_not_defaulted(self):
        value = self.manifest()
        del value["transport_observed_ui"]
        self.write_manifest(value)
        with self.assertRaises(ConfigurationError):
            load_audio_configuration(self.path)

    def test_malformed_saved_gzip_or_xml_is_a_configuration_error(self):
        self.write_manifest(self.manifest())
        for data in (b"not gzip", gzip.compress(b"<broken XML")):
            self.saved.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ConfigurationError):
                load_audio_configuration(self.path)

    def test_stereo_unknown_and_mixed_owned_input_targets_are_rejected(self):
        value = self.manifest(groups=1, layers=2)
        for target in ("AudioIn/External/S0", "AudioIn/Resampling", "AudioIn/External/M1", None):
            value["tracks"][2]["input_target"] = target
            self.write_manifest(value)
            with self.subTest(target=target), self.assertRaises(ConfigurationError):
                load_audio_configuration(self.path)

    def test_mono_input_target_is_converted_only_from_its_known_channel_index(self):
        value = self.manifest(groups=1, layers=2)
        for track in value["tracks"][1:]:
            track["input_target"] = "AudioIn/External/M1"
        self.write_manifest(value)
        self.assertEqual(load_audio_configuration(self.path)["capture_input_channel"], "2")

    def test_one_group_proof_metadata_uses_nested_legacy_input_and_excludes_monitor(self):
        value = self.manifest(groups=1, layers=3)
        value["owned_group"] = value.pop("group_names")[0]
        value["owned_capture_tracks"] = ["Proof A", "Proof B", "Proof C"]
        del value["owned_layer_names"]
        for track, name in zip(value["tracks"][1:], value["owned_capture_tracks"]):
            track["name"] = name
            track["input"] = {"target": track.pop("input_target")}
        value["tracks"].append({"name": "Proof Monitor", "type": "AudioTrack",
            "parent_als_group_id": "0", "input_target": "AudioIn/External/S0"})
        self.write_manifest(value)
        result = load_audio_configuration(self.path)
        self.assertEqual(result["groups"], [{"index": 0, "name": "Lightpad 1",
                                              "layer_names": ["Proof A", "Proof B", "Proof C"]}])
        self.assertEqual(result["capture_input_channel"], "1")

    def test_explicit_fields_and_feature_evidence_are_preserved_without_set_parsing(self):
        value = {"set_path": str(self.root / "Unavailable.als"), "set_sha256": "not-checked-here",
                 "groups": [{"index": 4, "name": "Explicit", "layer_names": ["Layer X"]}],
                 "bar_grid": {"origin": 3.25, "numerator": 7, "denominator": 8, "fixed": True},
                 "capture_input_channel": "2", "validated_features": ["record", "cancel"],
                 "sync_ui_confirmed_off": ["external_sync"]}
        self.write_manifest(value)
        result = load_audio_configuration(self.path)
        for key in ("groups", "bar_grid", "capture_input_channel", "validated_features", "set_sha256"):
            self.assertEqual(result[key], value[key])
        self.assertEqual(result["sync_ui_confirmed_off"], ["external_sync"])

    def test_ui_evidence_adds_only_literal_false_logical_values(self):
        value = self.manifest()
        self.write_manifest(value)
        evidence = self.root / "activation-status.json"
        evidence.write_text(json.dumps({"global_ui_evidence": {"link": False,
            "tempo_follower": 0, "external_sync": None, "other": False}}))
        result = load_audio_configuration(self.path, evidence)
        self.assertEqual(result["sync_ui_confirmed_off"], ["link"])
        self.assertEqual(result["validated_features"], [])
        evidence.write_text(json.dumps({"global_ui_evidence": {"link": "Off",
            "tempo_follower": False, "external_sync": False}}))
        result = load_audio_configuration(self.path, evidence)
        self.assertEqual(result["sync_ui_confirmed_off"], ["external_sync", "tempo_follower"])

    def test_absent_ui_evidence_never_grants_sync_off_or_recording_features(self):
        self.write_manifest(self.manifest())
        evidence = self.root / "activation-status.json"
        evidence.write_text(json.dumps({"status": "proof tests passed"}))
        result = load_audio_configuration(self.path, evidence)
        self.assertEqual(result["sync_ui_confirmed_off"], [])
        self.assertEqual(result["validated_features"], [])


if __name__ == "__main__":
    unittest.main()
