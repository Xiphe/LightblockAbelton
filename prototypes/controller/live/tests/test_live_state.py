import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.live_state import LiveState, read_grid
from live_test_support import Song, Track


class LiveStateTests(unittest.TestCase):
    def test_discovery_is_independent_of_filename_names_after_prefix_and_track_order(self):
        song = Song(); state = LiveState(song)
        first = state.groups[0].group
        first.name = '[R1] New name'
        song.file_path = '/copied/newname.als'
        song.tracks.insert(0, Track(song, 'Unrelated'))
        song.tracks.reverse()
        self.assertIs(state.refresh().groups[0].group, first)
        self.assertEqual(set(state.groups), {0, 1})

    def test_duplicate_and_nongroup_marker_disable_only_the_ambiguous_slot(self):
        song = Song(); song.tracks.append(Track(song, '[R1] Duplicate', foldable=True))
        song.tracks.append(Track(song, '[R3] Audio'))
        state = LiveState(song)
        self.assertEqual(set(state.groups), {1})
        self.assertEqual(state.issues, {0:'duplicate_remote_group', 2:'remote_marker_requires_group'})

    def test_nested_content_belongs_to_nearest_marked_ancestor(self):
        song = Song(groups=1); outer = song.tracks[0]
        nested = Track(song, '[R2] Nested', outer, foldable=True)
        child = Track(song, 'Child', nested)
        song.tracks.extend((nested, child))
        state = LiveState(song)
        self.assertEqual(state.groups[0].tracks, ())
        self.assertEqual(state.groups[1].tracks, (child,))

    def test_reference_is_actual_leftmost_running_completed_loop_even_when_muted(self):
        song = Song(); a, _, clip_a = song.add_clip(length=8, phase=2)
        b, _, clip_b = song.add_clip(length=12, phase=3)
        b.mute = True
        state = LiveState(song)
        self.assertIs(state.reference(0).clip, clip_b)
        clip_b.is_recording = True
        self.assertIs(state.refresh().reference(0).clip, clip_a)
        clip_a.loop_start, clip_a.loop_end, clip_a.playing_position = 4, 12, 7
        reference = state.refresh().reference(0)
        self.assertEqual((reference.length, reference.phase), (8, 3))

    def test_midi_loop_can_supply_timing_but_unwarped_audio_cannot(self):
        song = Song(); _, _, audio = song.add_clip()
        _, _, midi = song.add_clip(audio=False)
        midi.is_playing = False; audio.warping = False
        self.assertIsNone(LiveState(song).reference(0))
        midi.is_playing = True
        self.assertIs(LiveState(song).reference(0).clip, midi)

    def test_fresh_row_after_highest_playing_or_last_occupied_and_skips_occupied(self):
        song = Song(); song.add_clip(row=2); song.add_clip(row=5)
        track, _, clip = song.add_clip(row=6, playing=False)
        state = LiveState(song)
        self.assertEqual(state.groups[0].fresh_row, 7)
        for item in state.groups[0].playing:item.clip.is_playing=False
        self.assertEqual(state.refresh().groups[0].fresh_row, 7)

    def test_selected_recording_group_wins_then_native_track_order(self):
        song = Song(); left, _, a = song.add_clip(group=0, recording=True)
        right, _, b = song.add_clip(group=1, recording=True)
        state = LiveState(song)
        self.assertEqual(state.recording()[0], 0)
        song.view.selected_track = right
        self.assertEqual(state.recording()[0], 1)

    def test_selected_nonrecording_child_selects_its_remote_groups_recording(self):
        song=Song();song.add_clip(group=0,recording=True)
        right,_,clip=song.add_clip(group=1,recording=True)
        instrument=Track(song,'Instrument',right.group_track,audio=False)
        song.tracks.append(instrument);song.view.selected_track=instrument
        self.assertEqual(LiveState(song).recording()[0],1)

    def test_steady_state_refresh_does_not_scan_idle_clip_slots(self):
        song=Song(groups=1,layers=2,scenes=1000)
        class UntouchedSlot:
            @property
            def has_clip(self):
                raise AssertionError('steady-state callback scanned an idle slot')
        for track in song.tracks[1:]:
            track.clip_slots=[UntouchedSlot() for unused in range(1000)]
        state=LiveState(song)
        self.assertEqual(state.groups[0].playing,())
        self.assertIsNone(state.reference(0))

    def test_native_grid_follows_meter_event_origin_instead_of_global_modulo(self):
        song = Song(); song.signature_numerator=3; song.signature_denominator=4
        song.bar_origin=8; song.current_song_time=9.5
        grid = read_grid(song)
        self.assertEqual((grid.origin, grid.phase, grid.length, grid.next_bar()), (8,1.5,3,11))
        song.current_song_time=10.9999
        self.assertAlmostEqual(read_grid(song).phase, 2.9999)

    def test_unavailable_native_clock_never_invents_a_four_four_clock(self):
        song = Song(); song.get_current_beats_song_time=lambda:None
        with self.assertRaises(AttributeError):read_grid(song)


if __name__=='__main__':unittest.main()
