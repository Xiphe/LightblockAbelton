"""Physical sample coverage follows warp segments, never current song tempo."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.recording_coverage import sample_covers


RECORDED_MARKERS = ((0.0, 0.0), (.015625, .03125))


class RecordingCoverageTests(unittest.TestCase):
    def test_native_short_hidden_segment_proves_full_five_and_a_half_beats(self):
        self.assertTrue(sample_covers(5.5, 121275, 44100.0, RECORDED_MARKERS))
        self.assertTrue(sample_covers(5.0, 110250, 44100.0, RECORDED_MARKERS))
        self.assertFalse(sample_covers(5.5, 110250, 44100.0, RECORDED_MARKERS))

    def test_coverage_tolerance_is_at_most_one_physical_sample(self):
        self.assertTrue(sample_covers(5.5, 121274, 44100.0, RECORDED_MARKERS))
        self.assertFalse(sample_covers(5.5, 121273, 44100.0, RECORDED_MARKERS))

    def test_piecewise_tempo_change_and_last_segment_continuation_use_actual_markers(self):
        markers = ((0.0, 0.0), (1.0, 2.0), (3.0, 4.0))
        self.assertTrue(sample_covers(1.0, 24000, 48000.0, markers))
        self.assertTrue(sample_covers(3.0, 96000, 48000.0, markers))
        self.assertTrue(sample_covers(4.0, 144000, 48000.0, markers))
        self.assertTrue(sample_covers(5.0, 192000, 48000.0, markers))
        self.assertFalse(sample_covers(5.0, 144000, 48000.0, markers))
        self.assertTrue(sample_covers(6.0, 240000, 48000.0, markers))

    def test_markers_must_start_at_fresh_recording_zero_and_increase_in_both_units(self):
        invalid = (((.1, 0), (1, 2)), ((0, .1), (1, 2)),
                   ((0, 0), (0, 1)), ((0, 0), (1, 0)),
                   ((0, 0), (1, 2), (.5, 3)), ((0, 0), (1, 2), (2, 1)),
                   ((0, 0), (-1, 2)), ((0, 0), (1, -2)))
        for markers in invalid:
            with self.subTest(markers=markers), self.assertRaises(ValueError):
                sample_covers(1, 44100, 44100.0, markers)

    def test_nonfinite_or_malformed_inputs_cannot_claim_coverage(self):
        for target in (0, -1, True, float("nan"), float("inf"), "5.5", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                sample_covers(target, 121275, 44100.0, RECORDED_MARKERS)
        for frames in (0, -1, True, False, 121275.0, "121275", None):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                sample_covers(5.5, frames, 44100.0, RECORDED_MARKERS)
        for rate in (0, -1, True, float("nan"), float("inf"), "44100", None):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                sample_covers(5.5, 121275, rate, RECORDED_MARKERS)
        for markers in (None, (), ((0, 0),), {0: 0, 1: 2}, ((0, 0), (1,)),
                         ((0, 0), (1, 2, 3)), ((0, 0), (None, 2)),
                         ((0, 0), (True, 2)), ((0, 0), (1, True)),
                         ((0, 0), (float("nan"), 2)), ((0, 0), (1, float("inf")))):
            with self.subTest(markers=markers), self.assertRaises(ValueError):
                sample_covers(1, 44100, 44100.0, markers)


if __name__ == "__main__":
    unittest.main()
