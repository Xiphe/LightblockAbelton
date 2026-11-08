"""Raw clock observations preserve uncertainty without calling Live."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.bar_clock_probe import observe_bar_time


class NativeTime:
    bars = 9
    beats = 7
    sub_division = 2
    ticks = 59

    def __str__(self):
        return "raw native 9.7.2.59"


class ClockProbeTests(unittest.TestCase):
    def song(self, numerator=7, denominator=8):
        return SimpleNamespace(current_song_time=28.125, tempo=117.5,
            signature_numerator=numerator, signature_denominator=denominator,
            get_current_beats_song_time=lambda: NativeTime())

    def clock(self):
        values = iter((100.0, 100.25))
        return lambda: next(values)

    def test_native_call_between_reads_preserves_song_meter_and_time_uncertainty(self):
        song = self.song(4, 4)
        song.current_song_time = 3.99
        calls = []
        def native_read():
            calls.append("native")
            song.current_song_time = 4.01
            song.signature_numerator, song.signature_denominator = 7, 8
            song.tempo = 119.75
            return NativeTime()
        song.get_current_beats_song_time = native_read
        result = observe_bar_time(song, self.clock())
        self.assertEqual(calls, ["native"])
        self.assertEqual(result["monotonic_before"], 100.0)
        self.assertEqual(result["monotonic_after"], 100.25)
        self.assertEqual(result["before"]["current_song_time"], 3.99)
        self.assertEqual(result["after"]["current_song_time"], 4.01)
        self.assertEqual((result["before"]["signature_numerator"],
                          result["before"]["signature_denominator"]), (4, 4))
        self.assertEqual((result["after"]["signature_numerator"],
                          result["after"]["signature_denominator"]), (7, 8))
        self.assertEqual(result["before"]["tempo"], 117.5)
        self.assertEqual(result["after"]["tempo"], 119.75)
        self.assertNotIn("phase", result)
        self.assertNotIn("bar_start", result)

    def test_raw_attrs_text_type_and_unverified_units_are_not_normalized(self):
        result = observe_bar_time(self.song(), self.clock())
        native = result["native_beat_time"]
        self.assertEqual(native["type"], __name__ + ".NativeTime")
        self.assertEqual(native["text"], "raw native 9.7.2.59")
        self.assertEqual([native[key] for key in ("bars", "beats", "sub_division", "ticks")],
                         [9, 7, 2, 59])
        self.assertEqual(result["units"]["current_song_time"], "quarter-notes")
        self.assertIn("unverified", result["units"]["native_beat_time"])
        self.assertNotIn("ticks_per_quarter", native)
        json.dumps(result, allow_nan=False)

    def test_missing_method_still_reads_both_sides_without_default_bar_time(self):
        song = self.song()
        del song.get_current_beats_song_time
        result = observe_bar_time(song, self.clock())
        native = result["native_beat_time"]
        self.assertEqual(native["unavailable"], "AttributeError")
        for name in ("type", "text", "bars", "beats", "sub_division", "ticks"):
            self.assertEqual(native[name], {"unavailable": "AttributeError"})
        self.assertEqual(result["before"], result["after"])
        self.assertEqual(result["after"]["signature_denominator"], 8)

    def test_runtime_error_in_native_call_preserves_other_readings(self):
        song = self.song()
        def lost_native():
            song.current_song_time = 29.0
            raise RuntimeError("native object lost")
        song.get_current_beats_song_time = lost_native
        result = observe_bar_time(song, self.clock())
        self.assertEqual(result["native_beat_time"]["ticks"], {"unavailable": "RuntimeError"})
        self.assertEqual(result["before"]["current_song_time"], 28.125)
        self.assertEqual(result["after"]["current_song_time"], 29.0)

    def test_missing_and_runtime_error_fields_do_not_hide_other_raw_attrs(self):
        class PartialTime:
            bars, ticks = 3, 0
            @property
            def beats(self):
                raise RuntimeError("unavailable field")
            def __str__(self):
                raise RuntimeError("unavailable text")
        song = self.song()
        song.get_current_beats_song_time = lambda: PartialTime()
        native = observe_bar_time(song, self.clock())["native_beat_time"]
        self.assertEqual(native["bars"], 3)
        self.assertEqual(native["ticks"], 0)
        self.assertEqual(native["beats"], {"unavailable": "RuntimeError"})
        self.assertEqual(native["sub_division"], {"unavailable": "AttributeError"})
        self.assertEqual(native["text"], {"unavailable": "RuntimeError"})

    def test_actual_seven_eight_and_two_two_metadata_survives_without_four_four_fallback(self):
        for numerator, denominator in ((7, 8), (2, 2)):
            with self.subTest(meter=(numerator, denominator)):
                song = self.song(numerator, denominator)
                result = observe_bar_time(song, self.clock())
                for side in ("before", "after"):
                    self.assertEqual(result[side]["signature_numerator"], numerator)
                    self.assertEqual(result[side]["signature_denominator"], denominator)
                self.assertNotIn("bar_length", result)

    def test_missing_song_properties_are_reported_individually(self):
        song = self.song()
        del song.tempo
        del song.signature_denominator
        result = observe_bar_time(song, self.clock())
        for side in ("before", "after"):
            self.assertEqual(result[side]["tempo"], {"unavailable": "AttributeError"})
            self.assertEqual(result[side]["signature_denominator"], {"unavailable": "AttributeError"})
            self.assertEqual(result[side]["current_song_time"], 28.125)
        self.assertEqual(result["native_beat_time"]["bars"], 9)

    def test_nonfinite_or_non_json_values_stay_explicit_and_json_safe(self):
        native = NativeTime()
        native.ticks = object()
        song = self.song()
        song.tempo = float("nan")
        song.get_current_beats_song_time = lambda: native
        result = observe_bar_time(song, self.clock())
        self.assertEqual(result["before"]["tempo"],
                         {"unavailable": "UnsupportedJSONValue", "type": "builtins.float"})
        self.assertEqual(result["native_beat_time"]["ticks"],
                         {"unavailable": "UnsupportedJSONValue", "type": "builtins.object"})
        json.dumps(result, allow_nan=False)

    def test_observation_never_sets_song_or_native_properties(self):
        class ReadOnlySong:
            def __init__(self):
                object.__setattr__(self, "current_song_time", 2.5)
                object.__setattr__(self, "tempo", 90)
                object.__setattr__(self, "signature_numerator", 2)
                object.__setattr__(self, "signature_denominator", 2)
            def __setattr__(self, name, value):
                raise AssertionError("unexpected setter")
            def get_current_beats_song_time(self):
                return NativeTime()
        result = observe_bar_time(ReadOnlySong(), self.clock())
        self.assertEqual(result["before"]["tempo"], 90)
        self.assertEqual(result["native_beat_time"]["ticks"], 59)


if __name__ == "__main__":
    unittest.main()
