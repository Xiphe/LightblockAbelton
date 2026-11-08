"""Dormant launch-admission arithmetic with explicit fake reads, never Live."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.launch_admission import LaunchAdmission


class Clock:
    def __init__(self):
        self.value = 0.0
        self.calls = []

    def __call__(self):
        self.calls.append('clock')
        return self.value


class Song:
    def __init__(self, clock, beat=8.0, tempo=120.0):
        self.clock, self.beat, self.bpm = clock, beat, tempo
        self.beat_cost = self.tempo_cost = 0.0

    @property
    def current_song_time(self):
        self.clock.calls.append('beat')
        if self.beat_cost:
            self.clock.value += self.beat_cost
        return self.beat

    @property
    def tempo(self):
        self.clock.calls.append('tempo')
        if self.tempo_cost:
            self.clock.value += self.tempo_cost
        return self.bpm


class LaunchAdmissionTests(unittest.TestCase):
    def make(self, beat=8.0, tempo=120.0):
        self.clock = Clock()
        self.song = Song(self.clock, beat, tempo)
        return LaunchAdmission(self.song, clock=self.clock)

    def test_position_combines_conservative_elapsed_progress_and_tempo_scaled_headroom(self):
        admission = self.make(tempo=60)
        self.assertEqual(admission.position(), 8)
        self.clock.value, self.song.bpm = 1.0, 180
        self.assertEqual(admission.position(.5), 12.5)
        self.clock.value, self.song.bpm = 2.0, 60
        self.assertEqual(admission.position(.5), 15.5)
        self.song.beat = 20
        self.assertEqual(admission.position(.5), 21.5)

    def test_position_rejects_invalid_or_negative_headroom(self):
        for lead in (-.1, float('nan'), float('inf'), None, 'bad', True):
            with self.subTest(lead=lead), self.assertRaises(ValueError):
                self.make().position(lead)

    def test_original_timestamp_precedes_getters_and_check_clock_follows_all_getters(self):
        admission = self.make()
        self.assertEqual(self.clock.calls, ['clock', 'beat', 'tempo'])
        self.clock.calls.clear()
        admission.require(20, .1)
        self.assertEqual(self.clock.calls, ['beat', 'tempo', 'clock'])

    def test_frozen_native_beat_cannot_hide_elapsed_crossing_of_target(self):
        admission = self.make()
        self.clock.value = 2.0
        self.assertEqual(self.song.beat, 8)
        with self.assertRaises(ValueError):
            admission.require(12, 0)

    def test_repeated_checks_do_not_rebase_immutable_original_sample(self):
        admission = self.make()
        self.clock.value = 1.0
        admission.require(10.5, 0)
        self.clock.value = 2.0
        with self.assertRaises(ValueError):
            admission.require(11.5, 0)

    def test_initial_getter_cost_is_included_in_elapsed_time(self):
        self.clock = Clock()
        self.song = Song(self.clock)
        self.song.beat_cost, self.song.tempo_cost = .4, .2
        admission = LaunchAdmission(self.song, clock=self.clock)
        self.song.beat_cost = self.song.tempo_cost = 0
        with self.assertRaises(ValueError):
            admission.require(9, 0)

    def test_getter_cost_during_check_is_included_before_lead_validation(self):
        admission = self.make()
        self.song.beat_cost, self.song.tempo_cost = .15, .1
        with self.assertRaises(ValueError):
            admission.require(8.6, .1)

    def test_tempo_increase_scales_elapsed_progress_and_required_lead(self):
        admission = self.make(tempo=60)
        self.clock.value, self.song.bpm = 1.0, 180
        with self.assertRaises(ValueError):
            admission.require(11.5, .2)
        admission.require(11.7, .2)

    def test_tempo_decrease_cannot_relax_original_maximum_or_lead(self):
        admission = self.make(tempo=180)
        self.clock.value, self.song.bpm = 1.0, 60
        with self.assertRaises(ValueError):
            admission.require(12.4, .5)
        admission.require(12.6, .5)

    def test_previously_seen_faster_tempo_remains_conservative_after_slowdown(self):
        admission = self.make(tempo=60)
        self.clock.value, self.song.bpm = 1.0, 180
        admission.require(20, .5)
        self.clock.value, self.song.bpm = 2.0, 60
        with self.assertRaises(ValueError):
            admission.require(15.5, .5)
        admission.require(15.6, .5)

    def test_exact_minimum_lead_is_denied_but_strictly_larger_lead_is_accepted(self):
        admission = self.make()
        with self.assertRaises(ValueError):
            admission.require(9, .5)
        admission.require(9.001, .5)

    def test_fresh_larger_native_position_takes_precedence_over_elapsed_estimate(self):
        admission = self.make()
        self.clock.value, self.song.beat = .25, 11
        with self.assertRaises(ValueError):
            admission.require(10, 0)
        with self.assertRaises(ValueError):
            admission.require(11.5, .25)
        admission.require(11.6, .25)

    def test_invalid_clock_or_backwards_elapsed_time_rejects_admission(self):
        for value in (float('nan'), float('inf'), float('-inf'), None, 'bad', True, -1.0):
            with self.subTest(value=value):
                admission = self.make()
                self.clock.value = value
                with self.assertRaises(ValueError):
                    admission.require(20, .1)
        for value in (float('nan'), float('inf'), None, True):
            with self.subTest(initial=value), self.assertRaises(ValueError):
                LaunchAdmission(Song(Clock()), clock=lambda value=value: value)

    def test_negative_clock_epoch_is_valid_when_elapsed_time_moves_forward(self):
        clock = Clock()
        clock.value = -10.0
        song = Song(clock)
        admission = LaunchAdmission(song, clock=clock)
        clock.value = -9.5
        admission.require(10, .1)
        clock.value = -10.1
        with self.assertRaises(ValueError):
            admission.require(10, .1)

    def test_invalid_target_lead_or_native_read_rejects_without_admitting(self):
        for target in (float('nan'), float('inf'), None, 'bad', True):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.make().require(target, .1)
        for lead in (-.1, float('nan'), float('inf'), None, 'bad', True):
            with self.subTest(lead=lead), self.assertRaises(ValueError):
                self.make().require(20, lead)
        for attribute, value in (('beat', float('nan')), ('beat', float('inf')),
                                 ('bpm', 0), ('bpm', -1), ('bpm', float('nan'))):
            with self.subTest(attribute=attribute, value=value):
                admission = self.make()
                setattr(self.song, attribute, value)
                with self.assertRaises(ValueError):
                    admission.require(20, .1)


if __name__ == '__main__':
    unittest.main()
