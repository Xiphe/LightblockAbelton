"""Evidence summary preserves requested/native data without timing verdicts."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from summarize_proof import main, summarize


def outer(message, monotonic=10.0, session=None):
    value = {"monotonic": monotonic, "message": message}
    if session is not None:
        value["session"] = session
    return json.dumps(value) + "\n"


def event(name, token=1, layer="A", beat=4.125, target=None, clip=None, session=None):
    details = {} if token is None else {"token": token}
    if target is not None:
        details["target_beat"] = target
    trace = {"event": name, "monotonic": 9.99, "details": details,
             "song": {"current_song_time": beat, "tempo": 120},
             "layers": [{"name": "Proof " + layer, "clip": clip or {
                 "start_time": 4.0, "end_time": 16.0, "loop_start": 0.0,
                 "loop_end": 12.0, "length": 12.0, "file_path": "/missing/evidence.wav"}}]}
    return outer("Lightpad proof " + json.dumps(trace), session=session)


def take(token=1, layer="A", session=None):
    return [event("before.fire." + layer, token, layer, beat=3.6, target=4, session=session),
            event("recording_observed", token, layer, beat=4.125, session=session),
            event("before.finish." + layer, token, layer, beat=15.5, target=16, session=session),
            event("finish_observed", token, layer, beat=16.15, session=session)]


class SummaryTests(unittest.TestCase):
    def test_two_takes_and_reset_tokens_remain_separate_startup_segments(self):
        lines = [outer("Audio proof loaded DISABLED; no MIDI ports")]
        lines += take(1, "A") + take(2, "B")
        lines += [outer("Audio proof loaded DISABLED; no MIDI ports")] + take(1, "C")
        result = summarize(lines)
        self.assertEqual([[t["token"] for t in s["takes"]] for s in result["segments"]], [[1, 2], [1]])
        self.assertEqual([s["segment_id"] for s in result["segments"]], ["startup:1", "startup:2"])
        self.assertEqual(result["segments"][1]["takes"][0]["layer"], "C")

    def test_explicit_sessions_group_interleaved_token_numbers(self):
        lines = take(1, "A", session="first") + take(1, "B", session="second")
        lines += take(2, "C", session="first")
        result = summarize(lines)
        self.assertEqual([s["segment_id"] for s in result["segments"]], ["session:first", "session:second"])
        self.assertEqual([len(s["takes"]) for s in result["segments"]], [2, 1])

    def test_requested_targets_and_callback_arrivals_are_not_native_time_claims(self):
        result = summarize(take())
        item = result["segments"][0]["takes"][0]
        self.assertEqual(result["assessment"], "inconclusive")
        self.assertEqual(item["assessment"], "inconclusive")
        self.assertTrue(item["required_events_present"])
        self.assertEqual(item["requested"]["start_targets"][0]["target_beat"], 4)
        observation = item["recording_observations"][0]
        self.assertEqual(observation["observed_song_beats"], 4.125)
        self.assertEqual(observation["arrival_monotonic"], 10.0)
        self.assertEqual(observation["trace_monotonic"], 9.99)
        self.assertEqual(observation["native_clip"]["start_time"], 4.0)
        self.assertEqual(item["finish_observations"][0]["native_clip"]["end_time"], 16.0)
        self.assertNotIn("timing_error", item)
        self.assertNotIn("gap_samples", item)
        self.assertNotIn("pass", item)

    def test_missing_and_unavailable_native_values_remain_explicit(self):
        lines = [event("before.fire.A", target=4), event("recording_observed", clip={
            "start_time": {"unavailable": "AttributeError"}, "file_path": ""})]
        item = summarize(lines)["segments"][0]["takes"][0]
        self.assertEqual(item["missing_events"], ["before.finish", "finish_observed"])
        self.assertFalse(item["required_events_present"])
        self.assertIn("recording_observations[0].start_time", item["unsupported_or_missing_native"])
        self.assertIn("recording_observations[0].file_path", item["unsupported_or_missing_native"])
        self.assertEqual(item["recording_observations"][0]["native_clip"]["start_time"],
                         {"unavailable": "AttributeError"})

    def test_malformed_outer_trace_and_missing_tokens_are_reported_without_guessing(self):
        result = summarize(["{broken\n", outer("Lightpad proof {broken"),
            outer("Lightpad proof []"), json.dumps(["unexpected"]) + "\n",
            event("recording_observed", token=None)])
        self.assertEqual(result["counts"]["malformed_lines"], 4)
        self.assertTrue(any(d["reason"] == "key event has no valid token" for d in result["diagnostics"]))
        self.assertEqual(sum(len(s["takes"]) for s in result["segments"]), 0)

    def test_nonfinite_numbers_are_malformed_evidence_and_missing_timestamps_stay_explicit(self):
        result = summarize(['{"monotonic":NaN,"message":"bad"}\n',
            '{"monotonic":1e999,"message":"bad"}\n',
            outer('Lightpad proof {"event":"fault","details":{},"monotonic":Infinity}'),
            json.dumps({"message": "Audio proof loaded DISABLED"}) + "\n"])
        self.assertEqual(result["counts"]["malformed_lines"], 3)
        self.assertTrue(any(d["reason"] == "missing/invalid outer monotonic timestamp"
                            for d in result["diagnostics"]))
        json.dumps(result, allow_nan=False)

    def test_errors_with_and_without_tokens_are_visible_and_association_is_labeled(self):
        lines = [event("before.fire.A", target=4), event("error.fire.A", token=None),
                 event("fault", token=None),
                 outer('Lightpad proof mailbox {"event":"dispatch_error","error":"RuntimeError"}')]
        result = summarize(lines)
        item = result["segments"][0]["takes"][0]
        self.assertEqual([e["record"]["event"] for e in item["errors"]], ["error.fire.A", "fault"])
        self.assertIn("no token", item["errors"][0]["association"])
        self.assertEqual(result["segments"][0]["errors"][0]["source"], "mailbox")
        self.assertEqual(result["counts"]["malformed_lines"], 0)

    def test_error_for_different_layer_is_not_assigned_to_current_take(self):
        result = summarize([event("before.fire.A", target=4), event("error.fire.B", token=None)])
        segment = result["segments"][0]
        self.assertEqual(segment["takes"][0]["errors"], [])
        self.assertEqual(segment["errors"][0]["association"], "unassigned")

    def test_cli_is_read_only_by_default_and_output_requires_explicit_path(self):
        with tempfile.TemporaryDirectory(prefix="lightpad-proof-summary-") as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("".join(take()))
            before = path.read_bytes()
            stream = StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["--events", str(path)]), 0)
            self.assertEqual(json.loads(stream.getvalue())["assessment"], "inconclusive")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["events.jsonl"])
            output = Path(directory) / "summary.json"
            self.assertEqual(main(["--events", str(path), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["assessment"], "inconclusive")
            self.assertEqual(path.read_bytes(), before)

    def test_cli_refuses_to_replace_input_evidence(self):
        with tempfile.TemporaryDirectory(prefix="lightpad-proof-summary-") as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("".join(take()))
            before = path.read_bytes()
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main(["--events", str(path), "--output", str(path)])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
