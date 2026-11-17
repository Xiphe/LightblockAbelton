"""Issued Native transitions get observed before another slow prepare call.

These checks use the complete Audio backend, real prepared bundles and queued
Native simulator calls. They do not simulate the duration of Live's allocator.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_audio_inputs as audio_fixtures
import test_backend as legacy_fixtures
from PerformanceController.fake_native import Clip


class TransitionMaintenanceChecks(unittest.TestCase):
    def fixture(self, lanes):
        fixture = audio_fixtures.AudioInputChecks()
        self.addCleanup(fixture.doCleanups)
        backend = fixture.make(initial=3, groups=1, lanes=lanes)
        return fixture, backend

    @staticmethod
    def creates(fixture):
        return sum(call[0] == 'create_track' for call in fixture.song.calls)

    def test_issued_next_waits_for_native_handoff_before_overflow_preparation(self):
        fixture, backend = self.fixture(lanes=4)
        fixture.ticks(80)
        take = fixture.start()
        fixture.ticks(50)
        self.assertEqual(len(backend.reserves[0].members), 2)
        # Farther than the existing300ms guard: Native fires are queued but
        # their shared next-bar transition has not happened yet.
        fixture.song.advance(take.anchor + 2.)
        self.assertEqual(backend.dispatch(67, take.take_id | (2 << 7)), 0)
        following = take.following
        self.assertTrue(take.finish_issued)
        self.assertTrue(following.start_issued)
        self.assertEqual(take.state, 'finish_queued')
        self.assertNotIn(0, backend.reserves)
        self.assertGreater(take.finish_target - fixture.song.current_song_time,
                           fixture.song.tempo / 60. * .3)
        before = self.creates(fixture)
        with patch.object(backend.audio_inputs, 'maintenance', wraps=backend.audio_inputs.maintenance) as prepare:
            fixture.ticks(1)
            self.assertEqual(self.creates(fixture), before,
                             'creating another lane here can block observation of the issued handoff')
            self.assertFalse(prepare.called, 'routing preparation must wait too')
            fixture.song.advance(take.finish_target + .001)
            fixture.ticks(1)
            self.assertIs(backend.recording.take, following)
            self.assertEqual(following.state, 'recording')
            self.assertTrue(prepare.called)
            self.assertIsNotNone(backend.allocation_ticket)
            fixture.ticks(1)  # a painted Busy grant permits the later callback
            self.assertGreater(self.creates(fixture), before,
                               'lookahead may resume once the new capture is actually observed')

    def test_issued_start_waits_for_actual_recording_before_overflow_preparation(self):
        fixture, backend = self.fixture(lanes=2)
        fixture.ticks(80)
        self.assertEqual(backend.dispatch(64, 1), 0)
        take = backend.recording.take
        self.assertEqual(take.state, 'queued')
        self.assertTrue(take.start_issued)
        self.assertNotIn(0, backend.reserves)
        self.assertGreater(take.target - fixture.song.current_song_time,
                           fixture.song.tempo / 60. * .3)
        before = self.creates(fixture)
        fixture.ticks(1)
        self.assertEqual(self.creates(fixture), before,
                         'Native start is issued but not yet observed; no synchronous lane insertion')
        fixture.song.advance(take.target + .001)
        fixture.ticks(1)
        self.assertEqual(take.state, 'recording')
        self.assertIsNotNone(backend.allocation_ticket)
        fixture.ticks(1)
        self.assertGreater(self.creates(fixture), before)

    def test_long_unissued_reference_countdown_keeps_lookahead_preparation(self):
        fixture, backend = self.fixture(lanes=3)
        reference = backend.state.groups[0].tracks[0]
        reference.clip_slots[0].clip = Clip(anchor=0., length=16., playing=True)
        reference.playing_slot_index = 0
        fixture.song.advance(2.)
        backend.state.observe(0)
        fixture.ticks(80)
        self.assertEqual(backend.dispatch(64, 1), 0)
        take = backend.recording.take
        self.assertEqual(take.state, 'queued')
        self.assertFalse(take.start_issued)
        self.assertEqual(take.target, 16.)
        before = self.creates(fixture)
        fixture.ticks(1)
        self.assertEqual(take.state, 'queued')
        self.assertFalse(take.start_issued)
        self.assertIsNotNone(backend.allocation_ticket)
        fixture.ticks(1)
        self.assertGreater(self.creates(fixture), before,
                           'a long unissued count-in remains useful preparation time')

    def test_clear_discards_old_row_task_before_reusing_current_phrase_lane(self):
        fixture = legacy_fixtures.BackendChecks()
        self.addCleanup(fixture.doCleanups)
        backend = fixture.make(groups=1)
        fixture.warm()
        original = backend.reserves[0].track
        take = fixture.begin(1)
        fixture.accept(take)
        take.clip.name, take.clip.color = 'Keep earlier source', 33
        self.assertEqual(backend.tasks[0]['row'], 0)
        self.assertIsNotNone(backend.tasks[0]['track'])
        created = self.creates(fixture)
        self.assertEqual(backend.dispatch(104, 0), 0)
        self.assertEqual(backend.recording._preferred_rows[0], 1)
        # One complete round of the existing background cursor is enough to
        # reuse an already configured earlier lane in the new phrase.
        fixture.ticks(8)
        self.assertIn(0, backend.reserves, 'Clear must not finish preparing an obsolete phrase row')
        self.assertEqual(backend.reserves[0].row, 1)
        self.assertIs(backend.reserves[0].track, original)
        self.assertNotIn(0, backend.tasks)
        self.assertEqual(self.creates(fixture), created)
        self.assertIs(original.clip_slots[0].clip, take.clip)
        self.assertEqual((take.clip.name, take.clip.color), ('Keep earlier source', 33))
        fresh = fixture.begin(2)
        self.assertIs(fresh.track, original)
        self.assertEqual(fresh.row, 1)


if __name__ == '__main__':
    unittest.main()
