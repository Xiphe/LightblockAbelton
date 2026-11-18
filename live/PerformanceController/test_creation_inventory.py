"""Creation guards must scale with the Set, without weakening identity checks."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_audio_inputs as fixtures
from PerformanceController import backend as backend_module
from PerformanceController import selection_restore as selection_module


class CreationInventoryChecks(unittest.TestCase):
    def prepared(self, lanes=160):
        fixture = fixtures.AudioInputChecks()
        self.addCleanup(fixture.doCleanups)
        backend = fixture.make(groups=1, lanes=lanes)
        backend.allocations.begin_callback()
        self.assertIsNone(backend._create_audio_lane(0, 0))
        backend.allocations.begin_callback()
        self.assertTrue(backend.allocations.grant(backend.allocation_ticket))
        backend.allocations.begin_callback()
        return fixture, backend

    def test_audio_and_midi_creation_use_linear_identity_checks(self):
        for midi in (False, True):
            with self.subTest(midi=midi):
                fixture, backend = self.prepared()
                before = tuple(fixture.song.tracks)
                original_backend = backend_module.same
                original_selection = selection_module.same
                with patch.object(backend_module, 'same', wraps=original_backend) as first, \
                        patch.object(selection_module, 'same', wraps=original_selection) as second:
                    created = backend._create_audio_lane(0, 0, midi=midi)
                    comparisons = first.call_count + second.call_count
                self.assertIsNotNone(created)
                self.assertEqual(len(fixture.song.tracks), len(before) + 1)
                self.assertLessEqual(comparisons, 20 * len(before),
                    'one insertion must not compare every track against every earlier track')
                self.assertEqual(backend.restoration.created, (created,))

    def test_unexpected_second_insertion_is_not_adopted_or_replayed(self):
        fixture, backend = self.prepared(lanes=4)
        create = fixture.song.create_audio_track
        def two(index):
            create(index)
            create(index)
        fixture.song.create_audio_track = two
        before = len(fixture.song.tracks)
        with self.assertRaises(ValueError):
            backend._create_audio_lane(0, 0)
        self.assertEqual(len(fixture.song.tracks), before + 2)
        self.assertIsNone(backend.restoration)

    def test_reordering_existing_tracks_during_creation_is_rejected(self):
        fixture, backend = self.prepared(lanes=4)
        create = fixture.song.create_audio_track
        def reorder(index):
            create(index)
            fixture.song._tracks[1], fixture.song._tracks[2] = (
                fixture.song._tracks[2], fixture.song._tracks[1])
        fixture.song.create_audio_track = reorder
        with self.assertRaises(ValueError):
            backend._create_audio_lane(0, 0)


if __name__ == '__main__':
    unittest.main()
