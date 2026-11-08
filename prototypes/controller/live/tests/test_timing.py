"""Optional timing encoding and bounded marker placement, without Live."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.timing import (
    Clock, ONE_BAR, REFERENCE, COUNTDOWN, RUNNING, MAX_TICKS,
    encode_timing, encode_view_timing, encode_global_timing, marker_pixels)


def clock(**changes):
    values = dict(context=REFERENCE, take_id=9, span=12, position=5,
                  first_bar=0, bar_length=3, tempo=120,
                  numerator=3, denominator=4, running=True)
    values.update(changes)
    return Clock(**values)


def number(messages, first_cc, digits=3):
    fields = {cc: value for status, cc, value in messages}
    return sum(fields[first_cc + digit] << (7 * digit) for digit in range(digits))


class TimingTests(unittest.TestCase):
    def test_global_metronome_bit_is_actual_bool_independent_of_running_and_never_leaks_to_groups(self):
        for running, metronome, context in ((False, False, 1), (False, True, 9),
                                             (True, False, 5), (True, True, 13)):
            actual = clock(context=ONE_BAR, take_id=0, span=3, position=1, running=running)
            with self.subTest(running=running, metronome=metronome):
                messages = encode_global_timing(7, actual, metronome=metronome)
                self.assertEqual([cc for _, cc, _ in messages], list(range(70, 90)))
                self.assertEqual(messages[1], (0xBF, 71, context))
                self.assertTrue(all(status == 0xBF for status, _, _ in messages))
                self.assertEqual(encode_timing(0, 7, actual)[1][2] & 8, 0)
                self.assertEqual(encode_view_timing(0, 7, actual)[1][2] & 8, 0)
        self.assertEqual([value for _, _, value in encode_global_timing(7, None, metronome=True)],
                         [7] + [0] * 18 + [7])
        for invalid in (0, 1, 127, None, "on", 1.0):
            for actual in (None, clock(context=ONE_BAR, take_id=0, span=3, position=1)):
                with self.subTest(invalid=invalid, actual=actual), self.assertRaises(ValueError):
                    encode_global_timing(0, actual, metronome=invalid)

    def test_queued_and_recording_views_keep_full_reference_phase_and_markers(self):
        reference = clock(span=32, position=11.2, first_bar=0,
                          bar_length=4, numerator=4, denominator=4)
        queued = encode_view_timing(0, 1, reference)
        recording = encode_view_timing(0, 2, reference)
        self.assertEqual(queued[1:-1], recording[1:-1])
        self.assertEqual(number(queued, 73), 32 * 64)
        self.assertEqual(number(queued, 76), round(11.2 * 64))
        self.assertEqual(marker_pixels(reference), [0, 7, 14, 21, 28, 35, 42, 49])

    def test_view_clock_rejects_normalized_wait_but_accepts_actual_first_take_bar(self):
        wait = clock(context=COUNTDOWN, span=6, position=1)
        with self.assertRaises(ValueError):
            encode_view_timing(0, 0, wait)
        actual = clock(context=ONE_BAR, span=3, position=1)
        self.assertEqual(number(encode_view_timing(0, 0, actual), 76), 64)
        self.assertEqual([v for _, _, v in encode_view_timing(0, 0, None)], [0] * 20)

    def test_frame_has_twenty_contiguous_fields_and_matching_revisions(self):
        messages = encode_timing(7, 127, clock())
        self.assertEqual(len(messages), 20)
        self.assertEqual([cc for status, cc, value in messages], list(range(70, 90)))
        self.assertTrue(all(status == 0xB7 for status, cc, value in messages))
        self.assertEqual(messages[0], (0xB7, 70, 127))
        self.assertEqual(messages[-1], (0xB7, 89, 127))
        fields = {cc: value for status, cc, value in messages}
        self.assertEqual(fields[71], REFERENCE | RUNNING)
        self.assertEqual(fields[72], 9)
        self.assertEqual(number(messages, 73), 12 * 64)
        self.assertEqual(number(messages, 76), 5 * 64)
        self.assertEqual(number(messages, 79), 0)
        self.assertEqual(number(messages, 82), 3 * 64)
        self.assertEqual(number(messages, 85, 2), 120 * 16)
        self.assertEqual((fields[87], fields[88]), (3, 4))

    def test_invalid_clock_is_explicit_zero_payload_without_assumed_meter(self):
        messages = encode_timing(0, 4, None)
        self.assertEqual([value for status, cc, value in messages], [4] + [0] * 18 + [4])
        self.assertEqual(marker_pixels(None), [])

    def test_global_timing_uses_actual_seven_eight_grid_without_four_four_defaults(self):
        actual = clock(context=ONE_BAR, take_id=0, span=3.5, position=1,
                       bar_length=3.5, numerator=7, denominator=8)
        messages = encode_global_timing(11, actual)
        self.assertEqual(len(messages), 20)
        self.assertTrue(all(status == 0xBF for status, _, _ in messages))
        self.assertEqual(messages[0], (0xBF, 70, 11))
        self.assertEqual(messages[-1], (0xBF, 89, 11))
        fields = {cc: value for _, cc, value in messages}
        self.assertEqual((fields[71], fields[72], fields[87], fields[88]),
                         (ONE_BAR | RUNNING, 0, 7, 8))
        self.assertEqual(number(messages, 73), 224)
        self.assertEqual(number(messages, 82), 224)

    def test_global_invalid_clock_explicitly_clears_every_field(self):
        messages = encode_global_timing(5, None)
        self.assertTrue(all(status == 0xBF for status, _, _ in messages))
        self.assertEqual([value for _, _, value in messages], [5] + [0] * 18 + [5])

    def test_global_clock_rejects_group_context_or_nonzero_take_id(self):
        for actual in [clock(), clock(context=COUNTDOWN, span=6, position=1),
                       clock(context=ONE_BAR, span=3, position=1, take_id=1)]:
            with self.subTest(actual=actual):
                with self.assertRaises(ValueError):
                    encode_global_timing(0, actual)
        with self.assertRaises(ValueError):
            encode_global_timing(128, None)

    def test_three_four_one_bar_and_seven_eight_reference_markers(self):
        one_bar = clock(context=ONE_BAR, span=3, position=1)
        self.assertEqual(marker_pixels(one_bar), [0])
        seven_eight = clock(span=7, position=1, bar_length=3.5,
                            numerator=7, denominator=8)
        messages = encode_timing(0, 0, seven_eight)
        self.assertEqual(number(messages, 82), 224)
        self.assertEqual(marker_pixels(seven_eight), [0, 28])

    def test_partial_countdown_places_real_bar_markers_not_equal_subdivisions(self):
        countdown = clock(context=COUNTDOWN, span=9, position=2.5, first_bar=1,
                          bar_length=4, numerator=4, denominator=4)
        self.assertEqual(marker_pixels(countdown), [6, 31, 55])
        self.assertEqual(number(encode_timing(0, 0, countdown), 79), 64)

    def test_countdown_endpoint_maps_to_last_pixel_and_loop_endpoint_is_excluded(self):
        countdown = clock(context=COUNTDOWN, span=6, position=6)
        self.assertEqual(marker_pixels(countdown), [0, 28, 55])
        loop = clock(span=6, position=0)
        self.assertEqual(marker_pixels(loop), [0, 28])

    def test_marker_work_is_bounded_by_pixels_for_thousands_of_bars(self):
        long_loop = clock(span=32760, position=100, bar_length=1 / 16,
                          numerator=1, denominator=64)
        self.assertEqual(marker_pixels(long_loop), list(range(56)))
        self.assertEqual(marker_pixels(long_loop, pixels=1), [0])

    def test_low_seven_bits_first_and_tempo_precision(self):
        value = clock(span=257.25, position=130.5, first_bar=0.5,
                      bar_length=4, numerator=4, denominator=4, tempo=123.5, running=False)
        messages = encode_timing(2, 0, value)
        fields = {cc: value for status, cc, value in messages}
        self.assertEqual([fields[cc] for cc in (73, 74, 75)], [80, 0, 1])
        self.assertEqual(number(messages, 76), 8352)
        self.assertEqual(number(messages, 85, 2), 1976)
        self.assertEqual(fields[71], REFERENCE)

    def test_nonfinite_negative_zero_and_overflow_values_reject(self):
        invalid = [dict(span=0), dict(span=1 / 256), dict(bar_length=0), dict(tempo=0),
                   dict(position=-0.001), dict(first_bar=-1), dict(span=(MAX_TICKS + 1) / 64),
                   dict(tempo=1024), dict(position=float("nan")), dict(span=float("inf")),
                   dict(span=10 ** 1000), dict(tempo=True), dict(numerator=0), dict(denominator=128),
                   dict(numerator=3.0), dict(running=1), dict(take_id=128), dict(context=0)]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    encode_timing(0, 0, clock(**changes))

    def test_meter_span_bar_grid_and_loop_endpoint_validation(self):
        invalid = [dict(bar_length=4), dict(first_bar=3.1), dict(position=12),
                   dict(position=12.1), dict(position=11.999),
                   dict(context=ONE_BAR, span=12),
                   dict(context=COUNTDOWN, span=8, first_bar=1),
                   dict(context=COUNTDOWN, span=0.5, first_bar=1, position=0)]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    encode_timing(0, 0, clock(**changes))

    def test_group_revision_and_pixel_ranges_reject_without_clipping(self):
        for group, revision in [(8, 0), (0, 128), (-1, 0), (True, 0)]:
            with self.assertRaises(ValueError):
                encode_timing(group, revision, None)
        for pixels in [0, -1, True, 3.5]:
            with self.assertRaises(ValueError):
                marker_pixels(clock(), pixels)


if __name__ == "__main__":
    unittest.main()
