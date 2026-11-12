"""Meter/metronome controls and selected-denominator taps without Live."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.metronome import MetronomeControls


class Song:
    def __init__(self):
        object.__setattr__(self, "writes", [])
        object.__setattr__(self, "unavailable", set())
        object.__setattr__(self, "refuse", set())
        for name, value in (("signature_numerator", 4), ("signature_denominator", 4),
                            ("metronome", False), ("tempo", 120.0),
                            ("is_playing", False), ("record_mode", False)):
            object.__setattr__(self, name, value)

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, "unavailable"):
            raise RuntimeError("native property unavailable")
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name in self.refuse:
            raise RuntimeError("native setter refused")
        self.writes.append((name, value))
        object.__setattr__(self, name, value)

    def tap_tempo(self):
        raise AssertionError("native tap can start transport and must never be called")

    @property
    def tracks(self):
        raise AssertionError("metronome controls must not inspect or mutate clips/tracks")


def command(opcode, argument=0, group=0):
    return p.Command(1, group, opcode, argument)


class MetronomeTests(unittest.TestCase):
    def make(self, can_edit=lambda: True):
        self.song, self.now = Song(), [100.0]
        self.controls = MetronomeControls(self.song, can_edit, now=lambda: self.now[0])
        return self.controls

    def tap(self, at):
        self.now[0] = at
        return self.controls.dispatch(command(p.TAP_TEMPO))

    def test_two_taps_use_selected_denominator_without_native_tap_or_transport_writes(self):
        for denominator, interval in ((8, .25), (4, .5), (2, 1.0)):
            for running in (False, True):
                controls = self.make()
                object.__setattr__(self.song, "signature_denominator", denominator)
                object.__setattr__(self.song, "tempo", 90.0)
                object.__setattr__(self.song, "is_playing", running)
                self.assertEqual(self.tap(100.0), p.APPLIED)
                self.assertEqual(self.song.writes, [])
                self.assertEqual(self.tap(100.0 + interval), p.APPLIED)
                self.assertAlmostEqual(self.song.tempo, 120.0)
                self.assertEqual(self.song.writes, [("tempo", 120.0)])
                self.assertEqual(self.song.is_playing, running)
                self.assertFalse(self.song.record_mode)

    def test_tempo_averages_only_the_latest_four_valid_intervals(self):
        self.make()
        self.assertEqual(self.tap(100.0), p.APPLIED)
        for instant in (100.5, 101.0, 101.5, 102.0, 103.0):
            self.assertEqual(self.tap(instant), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 96.0)
        self.assertTrue(all(name == "tempo" for name, _ in self.song.writes))

    def test_external_or_explicit_denominator_change_starts_a_new_tap_sequence(self):
        controls = self.make()
        self.tap(100.0)
        self.tap(100.5)
        self.song.signature_denominator = 8
        self.assertEqual(controls.meter(), (4, 8))
        self.song.writes.clear()
        self.assertEqual(self.tap(100.75), p.APPLIED)
        self.assertEqual(self.song.writes, [])
        self.assertEqual(self.tap(101.0), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)
        self.assertEqual(controls.dispatch(command(p.SET_DENOMINATOR, 4)), p.APPLIED)
        self.song.writes.clear()
        self.assertEqual(self.tap(101.5), p.APPLIED)
        self.assertEqual(self.song.writes, [])
        self.assertEqual(self.tap(102.0), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)

    def test_long_gap_threshold_uses_current_selected_beat_duration(self):
        self.make()
        object.__setattr__(self.song, "signature_denominator", 2)
        object.__setattr__(self.song, "tempo", 30.0)
        self.assertEqual(self.tap(100.0), p.APPLIED)
        self.assertEqual(self.tap(104.0), p.APPLIED)  # A half-note beat is four seconds.
        self.assertAlmostEqual(self.song.tempo, 30.0)
        self.song.writes.clear()
        self.assertEqual(self.tap(117.0), p.APPLIED)  # More than three selected beats.
        self.assertEqual(self.song.writes, [])
        self.assertEqual(self.tap(121.0), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 30.0)

    def test_bounce_and_nonfinite_taps_cannot_write_or_move_the_last_valid_tap(self):
        self.make()
        self.assertEqual(self.tap(100.0), p.APPLIED)
        for instant in (100.001, 100.0, 99.0, float("nan"), float("inf")):
            self.assertEqual(self.tap(instant), p.REJECTED)
            self.assertEqual(self.song.writes, [])
        self.assertEqual(self.tap(100.5), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)

    def test_out_of_range_slow_tap_resets_without_tempo_write(self):
        self.make()
        object.__setattr__(self.song, "signature_denominator", 16)
        self.assertEqual(self.tap(100.0), p.APPLIED)
        self.assertEqual(self.tap(101.0), p.REJECTED)  # Quarter-note BPM would be 15.
        self.assertEqual(self.song.writes, [])
        self.assertEqual(self.tap(101.125), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)

    def test_tap_guard_and_native_tempo_failures_do_not_mutate_native_state(self):
        allowed = [True]
        self.make(lambda: allowed[0])
        self.assertEqual(self.tap(100.0), p.APPLIED)
        allowed[0] = False
        self.assertEqual(self.tap(100.25), p.NOT_READY)
        self.assertEqual(self.song.writes, [])
        allowed[0] = True
        self.assertEqual(self.tap(100.5), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)
        for value in (19, 1000, True, float("nan")):
            self.make()
            object.__setattr__(self.song, "tempo", value)
            self.assertEqual(self.tap(100.0), p.NOT_READY)
            self.assertEqual(self.song.writes, [])
        self.make()
        self.song.unavailable.add("tempo")
        self.assertEqual(self.tap(100.0), p.NOT_READY)
        self.assertEqual(self.song.writes, [])
        self.make()
        self.assertEqual(self.tap(100.0), p.APPLIED)
        self.song.refuse.add("tempo")
        self.assertEqual(self.tap(100.5), p.NOT_READY)
        self.assertEqual(self.song.writes, [])

    def test_meter_and_metronome_read_actual_external_values_without_writes(self):
        controls = self.make()
        self.assertEqual(controls.meter(), (4, 4))
        self.assertIs(controls.metronome(), False)
        self.song.signature_numerator, self.song.signature_denominator = 7, 8
        self.song.metronome = True
        self.song.writes.clear()
        self.assertEqual(controls.meter(), (7, 8))
        self.assertIs(controls.metronome(), True)
        self.assertEqual(self.song.writes, [])

    def test_explicit_controls_write_only_the_requested_native_property(self):
        controls = self.make()
        for value in range(1, 100):
            self.assertEqual(controls.dispatch(command(p.SET_NUMERATOR, value)), p.APPLIED)
            self.assertEqual(controls.meter(), (value, self.song.signature_denominator))
        for value in (1, 2, 4, 8, 16):
            self.assertEqual(controls.dispatch(command(p.SET_DENOMINATOR, value)), p.APPLIED)
            self.assertEqual(controls.meter(), (99, value))
        for value in (127, 0):
            self.assertEqual(controls.dispatch(command(p.SET_METRONOME, value)), p.APPLIED)
            self.assertIs(controls.metronome(), value == 127)
        self.assertEqual([name for name, _ in self.song.writes],
                         ["signature_numerator"] * 99 + ["signature_denominator"] * 5 + ["metronome"] * 2)
        self.assertFalse(self.song.is_playing)
        self.assertFalse(self.song.record_mode)

    def test_scope_and_arguments_are_revalidated_before_any_native_mutation(self):
        controls = self.make()
        for group in range(1, 8):
            for opcode, value in ((p.TAP_TEMPO, 0), (p.SET_NUMERATOR, 4),
                                  (p.SET_DENOMINATOR, 8), (p.SET_METRONOME, 127)):
                self.assertEqual(controls.dispatch(command(opcode, value, group)), p.BAD_COMMAND)
        for opcode, values in ((p.TAP_TEMPO, (1, True)), (p.SET_NUMERATOR, (0, 100, True, 4.0)),
                               (p.SET_DENOMINATOR, (0, 3, 32, True)),
                               (p.SET_METRONOME, (1, 126, True))):
            for value in values:
                self.assertEqual(controls.dispatch(command(opcode, value)), p.BAD_COMMAND)
        self.assertEqual(controls.dispatch(command(p.SET_MUTE, 127)), p.BAD_COMMAND)
        self.assertEqual(self.song.writes, [])

    def test_guard_false_or_unavailable_prevents_all_control_writes(self):
        def unavailable():
            raise RuntimeError("scope unavailable")
        for guard in (lambda: False, unavailable):
            controls = self.make(guard)
            for opcode, value in ((p.TAP_TEMPO, 0), (p.SET_NUMERATOR, 7),
                                  (p.SET_DENOMINATOR, 8), (p.SET_METRONOME, 127)):
                self.assertEqual(controls.dispatch(command(opcode, value)), p.NOT_READY)
            self.assertEqual(self.song.writes, [])

    def test_missing_invalid_or_refused_native_properties_never_claim_success(self):
        for missing, opcode, value in (("signature_numerator", p.SET_NUMERATOR, 7),
                                       ("signature_denominator", p.SET_DENOMINATOR, 8),
                                       ("metronome", p.SET_METRONOME, 127)):
            controls = self.make()
            self.song.unavailable.add(missing)
            self.assertEqual(controls.dispatch(command(opcode, value)), p.NOT_READY)
            self.assertEqual(self.song.writes, [])
        for field, value in (("signature_numerator", 0), ("signature_numerator", 100),
                              ("signature_denominator", 3), ("signature_denominator", True)):
            controls = self.make()
            object.__setattr__(self.song, field, value)
            self.assertIsNone(controls.meter())
        for value in (None, 1, "on"):
            controls = self.make()
            object.__setattr__(self.song, "metronome", value)
            self.assertIsNone(controls.metronome())
        controls = self.make()
        self.song.refuse.add("signature_numerator")
        self.assertEqual(controls.dispatch(command(p.SET_NUMERATOR, 7)), p.NOT_READY)
        self.assertEqual(self.song.writes, [])


if __name__ == "__main__":
    unittest.main()
