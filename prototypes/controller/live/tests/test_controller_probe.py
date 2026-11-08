"""The isolated proof facade exercises the normal controller, never Live."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.audio_controller import AudioController
from LightpadController.controller_probe import ControllerProbe, ControllerProbeError, OPERATIONS
from LightpadController.session import BackendFailure, QUEUED_VIEW, RECORDING_VIEW
import test_audio_controller as native_helpers
from test_audio_fixture import OFF, Q_BAR


class ProbeTests(unittest.TestCase):
    def make(self):
        factory = native_helpers.AudioControllerTests()
        factory.make(enabled=False)
        self.addCleanup(factory.doCleanups)
        self.events, self.factory = [], factory
        self.song, self.calls = factory.song, factory.calls
        self.probe = ControllerProbe(self.song, factory.fixture.manifest, Q_BAR, OFF, self.events.append)
        self.addCleanup(self.probe.close)
        return self.probe

    def enable(self):
        return self.probe.dispatch({"op": "enable", "confirm_set_path": self.song.file_path,
            "confirm_fingerprint": self.probe.fixture.fingerprint()})

    def slot(self, lease):
        return self.probe.fixture.groups[lease.group].layers[lease.layer].clip_slots[lease.row]

    def record(self, group=0):
        result = self.probe.dispatch({"op": "begin", "group": group})
        self.assertEqual(result["outcome"], p.OUTCOME_QUEUED)
        take = self.probe.controller.session.take
        # Poll in the bar preceding a reference-wrap target to queue the native
        # fire. These fake native transitions are not timing evidence.
        self.song.current_song_time = take.schedule.start_at - 1
        self.probe.poll()
        self.song.current_song_time = take.schedule.start_at
        clip = self.slot(take.slot).begin_native(self.song.current_song_time)
        self.probe.poll()
        self.assertEqual(self.probe.controller.session.view, RECORDING_VIEW)
        return take.slot, clip

    def accept(self, length=8):
        lease, clip = self.record()
        self.song.current_song_time = clip.start_time + length - 1
        self.probe.dispatch({"op": "accept", "group": 0})
        self.song.current_song_time = clip.start_time + length
        self.slot(lease).finish_native(length)
        self.probe.poll()
        return lease, clip

    def test_construction_and_disabled_poll_inspect_never_arm_or_fire(self):
        probe = self.make()
        self.assertIsInstance(probe.controller, AudioController)
        self.assertFalse(probe.enabled)
        self.assertTrue(probe.controller.backend.probe)
        self.assertEqual(probe.controller.backend.validated_features, frozenset())
        state = probe.dispatch({"op": "inspect"})
        self.assertFalse(state["device_capabilities_advertised"])
        self.assertEqual(state["timing_assessment"], "unverified")
        self.assertTrue(state["probe_only"])
        self.assertFalse(probe.poll()["enabled"])
        self.assertEqual(self.calls, [])
        for op in OPERATIONS - {"inspect", "enable"}:
            with self.subTest(op=op), self.assertRaises(ControllerProbeError):
                probe.dispatch({"op": op, "group": 0})
        self.assertEqual(self.calls, [])

    def test_enable_requires_exact_loaded_real_path_and_current_fingerprint(self):
        probe = self.make()
        correct = {"op": "enable", "confirm_set_path": self.song.file_path,
                   "confirm_fingerprint": probe.fixture.fingerprint()}
        for changes in ({"confirm_set_path": self.song.file_path + ".other"},
                        {"confirm_fingerprint": "stale"}, {"confirm_set_path": "relative.als"}):
            with self.subTest(changes=changes), self.assertRaises(ControllerProbeError):
                probe.dispatch(dict(correct, **changes))
            self.assertFalse(probe.enabled)
            self.assertEqual(self.calls, [])
        result = self.enable()
        self.assertTrue(result["enabled"])
        self.assertEqual(self.calls, [])

    def test_group_argument_must_be_an_actual_integer_and_muting_is_not_exposed(self):
        probe = self.make()
        self.enable()
        for group in (None, True, -1, 8, "0", 0.0):
            with self.subTest(group=group), self.assertRaises(ControllerProbeError):
                probe.dispatch({"op": "begin", "group": group})
        for operation in ("set_mute", "tap_tempo", "set_volume", "stop_transport"):
            with self.subTest(op=operation), self.assertRaises(ControllerProbeError):
                probe.dispatch({"op": operation, "group": 0})
        self.assertEqual(self.calls, [])

    def test_native_take_observations_include_clip_metadata_without_a_pass_claim(self):
        probe = self.make()
        self.enable()
        lease, clip = self.accept(length=8)
        state = probe.inspect()
        self.assertEqual(state["current_beat_units"], "quarter-notes")
        target = next(row for row in state["owned_tracks"] if row["group"] == 0 and row["layer"] == lease.layer)
        actual = target["slots"][0]["clip"]
        self.assertEqual(actual["start_time"], clip.start_time)
        self.assertEqual(actual["loop_start"], 0)
        self.assertEqual(actual["loop_end"], 8)
        self.assertFalse(actual["is_recording"])
        self.assertTrue(actual["is_playing"])
        self.assertEqual(actual["file_path"], {"unavailable": "AttributeError"})
        self.assertFalse(state["device_capabilities_advertised"])
        self.assertEqual(state["timing_assessment"], "unverified")
        native_events = [event["backend_event"]["event"] for event in self.events
                         if event["event"] == "native_controller_event"]
        self.assertIn("recording_observed", native_events)
        self.assertIn("finish_observed", native_events)
        for event in self.events:
            json.dumps(event, allow_nan=False)
        json.dumps(state, allow_nan=False)

    def test_legitimate_owned_arms_are_not_reported_as_fixture_guard_failures(self):
        probe = self.make()
        self.enable()
        self.record()
        state = probe.inspect()
        self.assertTrue(state["fixture"]["ready"])
        self.assertEqual(state["fixture"]["guard_reasons"], [])
        self.assertTrue(state["controller"]["scope_valid"])

    def test_idle_poll_caches_full_readback_without_emitting_every_display_tick(self):
        probe = self.make()
        with patch.object(probe, "inspect", wraps=probe.inspect) as inspect:
            with patch("LightpadController.controller_probe.time.monotonic", return_value=100.0):
                first = probe.poll()
                second = probe.poll()
            self.assertIs(first, second)
            self.assertEqual(inspect.call_count, 1)
            self.assertEqual(self.events, [])

    def test_begin_and_accept_next_use_same_reference_wrap_scheduler(self):
        probe = self.make()
        self.enable()
        reference_lease, reference = self.accept(length=8)
        self.song.current_song_time = 13
        lease, current = self.record()
        self.assertEqual(current.start_time, 20)
        self.song.current_song_time = 21
        result = probe.dispatch({"op": "accept_next", "group": 0, "seq": 55})
        self.assertEqual(result["outcome"], p.OUTCOME_QUEUED)
        following = probe.controller.session.take.next_take
        self.assertEqual(probe.controller.session.take.schedule.start_at, 24)
        self.assertEqual(following.schedule.start_at, 28)
        self.song.current_song_time = 24
        self.slot(lease).finish_native(4)
        probe.poll()
        self.assertEqual(probe.controller.session.view, QUEUED_VIEW)
        self.assertIs(probe.controller.session.take, following)
        self.assertEqual(len(probe.controller.session.groups[0].accepted), 2)
        self.assertIs(self.slot(reference_lease).clip, reference)
        self.song.current_song_time = 28
        self.slot(following.slot).begin_native(28)
        probe.poll()
        self.assertEqual(probe.controller.session.view, RECORDING_VIEW)
        self.assertFalse(probe.inspect()["device_capabilities_advertised"])

    def test_cancel_completion_is_emitted_only_after_observed_native_cleanup(self):
        probe = self.make()
        self.enable()
        lease, _ = self.record()
        result = probe.dispatch({"op": "cancel", "group": 0, "seq": 91})
        self.assertIsNone(result["outcome"])
        probe.poll()
        self.assertFalse(any(event["event"] == "command_completed" for event in self.events))
        self.slot(lease).track.quiet_native()
        state = probe.poll()
        completed = [event for event in self.events if event["event"] == "command_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["operation"], "cancel")
        self.assertEqual(completed[0]["request_sequence"], 91)
        self.assertEqual(completed[0]["outcome"], p.APPLIED)
        self.assertEqual(state["controller"]["pending_commands"], [])

    def test_close_stops_own_poll_without_stop_disarm_or_accepting_native_transition(self):
        probe = self.make()
        self.enable()
        probe.dispatch({"op": "begin", "group": 0})
        take = probe.controller.session.take
        before = list(self.calls)
        probe.close()
        self.song.current_song_time = take.schedule.start_at
        self.slot(take.slot).begin_native(self.song.current_song_time)
        state = probe.poll()
        self.assertTrue(state["closed"])
        self.assertEqual(probe.controller.session.view, QUEUED_VIEW)
        self.assertEqual(self.calls, before)
        self.assertTrue(self.slot(take.slot).track.arm)
        with self.assertRaises(ControllerProbeError):
            probe.dispatch({"op": "cancel", "group": 0})
        self.assertTrue(probe.dispatch({"op": "inspect"})["closed"])

    def test_disable_refuses_active_take_and_never_discards_it(self):
        probe = self.make()
        self.enable()
        self.record()
        before = list(self.calls)
        with self.assertRaises(BackendFailure):
            probe.dispatch({"op": "disable"})
        self.assertTrue(probe.enabled)
        self.assertEqual(self.calls, before)


if __name__ == "__main__":
    unittest.main()
