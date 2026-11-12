"""Corroborate the observed BeatTime units without assuming a foreign grid."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.native_bar_grid import read_bar_grid


def song(position=0.0, numerator=4, denominator=4, fields=(1, 1, 1, 1)):
    native = SimpleNamespace(**dict(zip(("bars", "beats", "sub_division", "ticks"), fields)))
    return SimpleNamespace(current_song_time=position, signature_numerator=numerator,
                           signature_denominator=denominator,
                           get_current_beats_song_time=lambda: native), native


class NativeBarGridTests(unittest.TestCase):
    def test_raw_native_observations_corroborate_all_five_denominators(self):
        # Raw examples from experiment10/native/events.jsonl. Expected spans
        # and phases are worked quarter-note values, not display beat counts.
        examples = ((15.386666666666667, 7, 1, (1, 4, 14, 33), 28.0, 15.386666666666667),
                    (12.766666666666667, 7, 2, (1, 7, 4, 5), 14.0, 12.766666666666667),
                    (4.246666666666667, 5, 4, (1, 5, 1, 60), 5.0, 4.246666666666667),
                    (4.246666666666667, 5, 8, (2, 4, 1, 60), 2.5, 1.746666666666667),
                    (10.326666666666666, 7, 16, (6, 7, 1, 19), 1.75, 1.576666666666666),
                    (12.25, 7, 8, (4, 4, 2, 1), 3.5, 1.75))
        for position, numerator, denominator, fields, length, phase in examples:
            with self.subTest(denominator=denominator, position=position):
                source, native = song(position, numerator, denominator, fields)
                before, native_before = dict(vars(source)), dict(vars(native))
                actual = read_bar_grid(source)
                self.assertEqual((actual.position, actual.origin, actual.length,
                                  actual.numerator, actual.denominator),
                                 (position, 0.0, length, numerator, denominator))
                self.assertAlmostEqual(actual.phase, phase)
                self.assertEqual(vars(source), before)
                self.assertEqual(vars(native), native_before)

    def test_time_zero_and_tick_subdivision_units_are_one_based(self):
        source, native = song()
        self.assertEqual(tuple(read_bar_grid(source)), (0.0, 0.0, 4.0, 0.0, 4, 4))
        source.current_song_time = .7866666666666667
        native.sub_division, native.ticks = 4, 9
        self.assertAlmostEqual(read_bar_grid(source).phase, .7866666666666667)
        source.current_song_time = .9866666666666667
        native.ticks = 57
        self.assertAlmostEqual(read_bar_grid(source).phase, .9866666666666667)

    def test_explicit_fixed_origin_is_preserved_and_meter_change_does_not_invent_an_anchor(self):
        source, native = song(5.496666666666667, 5, 4, (1, 5, 1, 60))
        first = read_bar_grid(source, origin=1.25)
        self.assertEqual((first.origin, first.length), (1.25, 5.0))
        self.assertAlmostEqual(first.phase, 4.246666666666667)
        source.signature_denominator = 8
        native.bars, native.beats = 2, 4
        changed = read_bar_grid(source, origin=1.25)
        self.assertEqual((changed.origin, changed.length), (1.25, 2.5))
        self.assertAlmostEqual(changed.phase, 1.746666666666667)
        with self.assertRaises(ValueError):
            read_bar_grid(source, origin=0.0)

    def test_fragmentary_foreign_and_quantization_mismatch_are_unavailable(self):
        for position, meter, fields in ((12.6, (7, 8), (4, 1, 1, 25)),
                                        (13.5, (7, 8), (4, 2, 1, 1)),
                                        (.01, (4, 4), (1, 1, 1, 1)),
                                        (-.001, (4, 4), (1, 1, 1, 1)),
                                        (4.0, (4, 4), (1, 4, 4, 60))):
            with self.subTest(position=position, meter=meter, fields=fields):
                source, _ = song(position, *meter, fields)
                with self.assertRaises(ValueError):
                    read_bar_grid(source)

    def test_unstable_native_position_or_meter_readback_is_unavailable(self):
        for field, value in (("current_song_time", .01), ("signature_numerator", 5),
                              ("signature_denominator", 8)):
            source, native = song()
            def changing(source=source, native=native, field=field, value=value):
                setattr(source, field, value)
                return native
            source.get_current_beats_song_time = changing
            with self.subTest(field=field), self.assertRaises(ValueError):
                read_bar_grid(source)

    def test_missing_corrupted_and_out_of_bounds_fields_are_unavailable(self):
        for field, values in (("bars", (0, -1, True, 1.0)), ("beats", (0, 5, True)),
                               ("sub_division", (0, 5, 1.0)), ("ticks", (0, 61, None, True))):
            for value in values:
                source, native = song()
                setattr(native, field, value)
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    read_bar_grid(source)
        for field in ("bars", "beats", "sub_division", "ticks"):
            source, native = song()
            delattr(native, field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                read_bar_grid(source)
        for field, value in (("current_song_time", float("nan")),
                              ("current_song_time", float("inf")), ("current_song_time", True),
                              ("signature_numerator", 0), ("signature_numerator", 100),
                              ("signature_denominator", 3), ("signature_denominator", True)):
            source, _ = song()
            setattr(source, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                read_bar_grid(source)
        for origin in (None, True, float("nan"), float("inf")):
            source, _ = song()
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                read_bar_grid(source, origin)
        source, _ = song()
        del source.get_current_beats_song_time
        with self.assertRaises(ValueError):
            read_bar_grid(source)


if __name__ == "__main__":
    unittest.main()
