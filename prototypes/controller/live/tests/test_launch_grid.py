"""Select only a supplied, proven native quantization without moving targets."""

from enum import IntEnum
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.launch_grid import launch_window


QUANTIZATIONS = {"q_bar": 4, "q_quarter": 7, "q_eight": 9, "q_sixtenth": 11}


class LaunchGridTests(unittest.TestCase):
    def test_odd_bar_reference_wrap_uses_only_provided_grids_without_delaying_target(self):
        self.assertEqual(tuple(launch_window(12.0, 0.0, 3.5, QUANTIZATIONS)), (11.0, 12.0, 7))
        self.assertEqual(tuple(launch_window(12.0, 0.0, 3.5, dict(QUANTIZATIONS, q_half=5))),
                         (10.0, 12.0, 5))
        self.assertEqual(tuple(launch_window(14.0, 0.0, 3.5, QUANTIZATIONS)), (10.5, 14.0, 4))

    def test_coarsest_provided_period_wins_even_when_longer_than_current_bar(self):
        half_proven = dict(QUANTIZATIONS, q_half=5)
        result = launch_window(4.0, 0.0, .25, half_proven)
        self.assertEqual((result.previous, result.target, result.quantization), (2.0, 4.0, 5))
        self.assertEqual(tuple(launch_window(4.0, 0.0, .25, QUANTIZATIONS)), (3.0, 4.0, 7))
        with self.assertRaises(AttributeError):
            result.target = 8.0

    def test_eighth_and_sixteenth_targets_use_their_actual_previous_boundary(self):
        self.assertEqual(tuple(launch_window(12.5, 0.0, 3.5, QUANTIZATIONS)), (12.0, 12.5, 9))
        self.assertEqual(tuple(launch_window(12.25, 0.0, 3.5, QUANTIZATIONS)), (12.0, 12.25, 11))

    def test_nonzero_verified_bar_origin_permits_only_its_bar_grid(self):
        self.assertEqual(tuple(launch_window(8.25, 1.25, 3.5, QUANTIZATIONS)), (4.75, 8.25, 4))
        with self.assertRaises(ValueError):
            launch_window(12.0, 1.25, 3.5, QUANTIZATIONS)
        with self.assertRaises(ValueError):
            launch_window(8.25, 1.25, 3.5, {"q_quarter": 7})

    def test_unsupported_target_or_missing_proof_never_becomes_another_boundary(self):
        with self.assertRaises(ValueError):
            launch_window(.125, 0.0, .25, QUANTIZATIONS)
        with self.assertRaises(ValueError):
            launch_window(12.0, 0.0, 3.5, {"q_bar": 4})
        with self.assertRaises(ValueError):
            launch_window(12.0, 0.0, 3.5, {})

    def test_absolute_tolerance_accepts_roundoff_without_snapping_requested_target(self):
        result = launch_window(12.0 + 5e-8, 0.0, 3.5, QUANTIZATIONS)
        self.assertEqual(result.target, 12.0 + 5e-8)
        self.assertEqual(result.previous, 11.0 + 5e-8)
        self.assertEqual(result.quantization, 7)
        with self.assertRaises(ValueError):
            launch_window(12.0 + 2e-7, 0.0, 3.5, QUANTIZATIONS)

    def test_native_enum_handles_are_validated_and_returned_without_replacement(self):
        class Quantization(IntEnum):
            BAR = 4
            QUARTER = 7
        class NativeHalf:
            def __int__(self):
                return 5
        half = NativeHalf()
        mapping = {"q_bar": Quantization.BAR, "q_quarter": Quantization.QUARTER}
        self.assertIs(launch_window(12.0, 0.0, 3.5, mapping).quantization, Quantization.QUARTER)
        self.assertIs(launch_window(4.0, 0.0, .25, dict(mapping, q_half=half)).quantization, half)

    def test_invalid_input_or_unproven_enum_mapping_is_unavailable(self):
        for target, origin, length in ((float("nan"), 0, 4), (float("inf"), 0, 4),
                                       (12, float("nan"), 4), (12, float("inf"), 4),
                                       (12, 0, 0), (12, 0, -1), (12, 0, float("inf")),
                                       (True, 0, 4), (12, True, 4), (12, 0, True)):
            with self.subTest(target=target, origin=origin, length=length), self.assertRaises(ValueError):
                launch_window(target, origin, length, QUANTIZATIONS)
        for mapping in (None, [], {"q_no_q": 0}, {"q_bar": 7}, {"q_quarter": 4},
                         {"q_bar": True}, {"q_bar": 4.0}, {"q_bar": "4"},
                         {"q_bar": None}, {"q_unknown": 4}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                launch_window(12, 0, 4, mapping)


if __name__ == "__main__":
    unittest.main()
