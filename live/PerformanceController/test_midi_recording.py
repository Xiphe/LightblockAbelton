"""Mixed recording seams over queued Native calls, not MPE or audio proof."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PerformanceController import protocol as p
from PerformanceController.audio_inputs import BundleReserve, Destination
from PerformanceController.backend import AudioPreparedRecording, RetainedState
from PerformanceController.direct_monitoring import DirectMonitoring, AUTO, OFF
from PerformanceController.fake_native import Song, Track, Slot
from PerformanceController.recording_core import LiveRecording, Take

Q = {'q_bar': 4, 'q_half': 5, 'q_quarter': 7, 'q_eight': 9, 'q_sixtenth': 11}


class MidiClip:
    def __init__(self, anchor, length=8, recording=True):
        self.start_time, self.loop_start, self.loop_end, self.end_marker = anchor, 0., length, length
        self.is_recording, self.is_playing, self.looping = recording, True, True
        self.is_audio_clip, self.is_midi_clip, self.playing_position = False, True, 0.
        self.notes, self.controllers = ((60, 0., .5, 96),), ((64, 0., 127), (64, 1., 0))

    def __getattr__(self, name):
        if name in ('warping', 'warp_markers', 'sample_length', 'sample_rate', 'file_path'):
            raise AssertionError('MIDI clip must not read Audio property ' + name)
        raise AttributeError(name)


class MidiSlot(Slot):
    def apply(self, now):
        pending = self.pending
        super().apply(now)
        if pending is not None and now >= pending[1]:
            if pending[0] == 'start':
                self.clip = MidiClip(pending[1])
            else:
                self.clip.end_marker = self.clip.loop_end


class MixedRecordingChecks(unittest.TestCase):
    def setUp(self):
        self.clock = 0.
        self.song = Song(groups=1, scenes=4)
        self.parent = self.song.tracks[0]
        self.tracks = []
        for index in range(6):
            midi = index % 2 == 0
            track = Track(self.song, 'Capture %d' % index, self.parent, audio=not midi)
            track.current_monitoring_state = OFF
            if midi:
                track.clip_slots = [MidiSlot(track, row) for row in range(4)]
            self.song.tracks.append(track)
            self.tracks.append(track)
        self.state = RetainedState(self.song, {}).discover()
        self.reserves = {}
        self.sources = {0: NS(index=0, kind='midi', name='Synth', color=0x22bb66),
                        1: NS(index=1, kind='audio', name='Mic', color=0x5599bb)}
        self.recording = AudioPreparedRecording(self.song, self.state, Q, self.reserves,
                                                monotonic=lambda: self.clock)
        self.recording.inputs = NS(sources=self.sources, validate=lambda r, g, row: r.row == row)
        self.backend = NS(recording=self.recording, reserves=self.reserves,
                          audio_inputs=NS(monitor_owner=0), events=[])
        self.monitoring = DirectMonitoring(self.backend)
        self.recording.monitoring = self.monitoring
        self.addCleanup(self.monitoring.close)

    def reserve(self, offset=0, row=0, midi_only=False):
        members = tuple(Destination(self.sources[index], self.tracks[offset+index],
                                    self.tracks[offset+index].clip_slots[row])
                        for index in range(1 if midi_only else 2))
        self.reserves[0] = BundleReserve(0, self.parent, row, 0, members)

    def tick(self, position=None):
        self.clock += .101
        if position is not None:
            self.song.advance(position)
        self.state.observe(0)
        self.recording.tick()
        self.monitoring.tick()

    def begin(self, midi_only=False):
        self.reserve(midi_only=midi_only)
        self.assertEqual(self.recording.begin(0), p.OUTCOME_QUEUED)
        take = self.recording.take
        self.recording.enqueue_ready()
        self.tick(take.target)
        self.assertEqual(take.state, 'recording')
        return take

    def test_mixed_accept_next_has_same_boundary_and_cancel_preserves_accepted_events(self):
        first = self.begin()
        self.reserve(2)
        self.tick(first.anchor+6.)
        self.assertEqual(self.recording.accept(0, next_take=True), p.OUTCOME_QUEUED)
        following = first.following
        self.assertEqual(following.target, first.finish_target)
        self.assertEqual([t.current_monitoring_state for t in self.tracks[:4]], [OFF, AUTO, OFF, AUTO])
        self.recording.enqueue_ready()
        self.tick(first.finish_target)
        self.assertIs(self.recording.take, following)
        self.assertEqual(following.anchor, first.finish_target)
        self.assertEqual(first.clip.loop_end, 8.)
        self.assertEqual(first.members[0].clip.loop_end, 8.)
        notes, controllers = first.clip.notes, first.clip.controllers
        self.tick(following.anchor+1.)
        self.assertIsNone(self.recording.cancel(NS(group=0)))
        self.tick()
        self.assertIsNone(self.recording.take)
        self.assertFalse(any(track.clip_slots[0].has_clip for track in self.tracks[2:4]))
        self.assertEqual(first.clip.notes, notes)
        self.assertEqual(first.clip.controllers, controllers)
        self.assertTrue(first.clip.is_playing and first.members[0].clip.is_playing)
        self.assertFalse(any(t.arm for t in self.tracks[:4]))

    def test_midi_only_accept_never_reads_audio_metadata(self):
        take = self.begin(midi_only=True)
        self.tick(take.anchor+2.)
        self.assertEqual(self.recording.accept(0), p.OUTCOME_QUEUED)
        self.recording.enqueue_ready()
        self.tick(take.finish_target)
        self.assertEqual(take.state, 'completed')
        self.assertEqual(take.clip.loop_end, 4.)
        self.assertEqual(take.track.current_monitoring_state, OFF)
        self.assertTrue(take.clip.is_midi_clip)
        self.assertFalse(take.track.arm)

    def test_audio_primary_accept_finishes_midi_companion_without_audio_getters(self):
        self.reserve()
        self.reserves[0].members = tuple(reversed(self.reserves[0].members))
        self.assertEqual(self.recording.begin(0), p.OUTCOME_QUEUED)
        take = self.recording.take
        self.assertEqual(take.source.kind, 'audio')
        self.assertEqual(take.members[0].source.kind, 'midi')
        self.recording.enqueue_ready()
        self.tick(take.target)
        self.tick(take.anchor+6.)
        self.assertEqual(self.recording.accept(0), p.OUTCOME_QUEUED)
        self.recording.enqueue_ready()
        self.tick(take.finish_target)
        self.assertEqual(take.state, 'completed')
        self.assertEqual(take.clip.loop_end, take.members[0].clip.loop_end)
        self.assertTrue(take.members[0].clip.is_midi_clip)
        self.assertEqual(take.members[0].track.current_monitoring_state, OFF)

    def test_retry_discards_only_active_mixed_bundle_and_uses_prepared_replacement(self):
        first = self.begin()
        self.reserve(2)
        self.tick(first.anchor+2.)
        self.assertIsNone(self.recording.cancel(NS(group=0), retry=True))
        self.tick()
        retry = self.recording.take
        self.assertIsNotNone(retry)
        self.assertIs(retry.track, self.tracks[2])
        self.assertFalse(any(track.clip_slots[0].has_clip for track in self.tracks[:2]))
        self.assertEqual(retry.track.current_monitoring_state, OFF)
        self.tick()
        self.tick(retry.target)
        self.assertEqual(retry.state, 'recording')
        self.assertEqual(retry.members[0].anchor, retry.anchor)

    def test_midi_actual_clip_type_mismatch_is_not_adopted_as_audio(self):
        take = self.begin(midi_only=True)
        take.clip.is_midi_clip = False
        take.clip.is_audio_clip = True
        with self.assertRaisesRegex(ValueError, 'captured source'):
            self.recording._observe(take)


class MidiNormalizationChecks(unittest.TestCase):
    def setUp(self):
        self.clip = MidiClip(anchor=12., length=12., recording=False)
        self.take = Take(0, object(), object(), object(), 0, 1, None,
                         clip=self.clip, anchor=4., finish_target=12.,
                         start_issued=True, finish_issued=True)
        self.recording = object.__new__(LiveRecording)
        self.recording._identity = lambda take: None

    def test_fresh_owned_midi_trims_loop_only_preserving_note_and_cc_events(self):
        notes, controllers = self.clip.notes, self.clip.controllers
        self.recording._normalize(self.take)
        self.assertEqual(self.clip.loop_end, 8.)
        self.assertEqual(self.clip.end_marker, 12.)
        self.assertEqual(self.clip.notes, notes)
        self.assertEqual(self.clip.controllers, controllers)
        self.assertTrue(self.take.normalized)

    def test_midi_does_not_extend_beyond_native_recorded_region(self):
        self.clip.loop_end = self.clip.end_marker = 4.
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            self.recording._normalize(self.take)
        self.assertEqual(self.clip.loop_end, 4.)
        self.assertFalse(self.take.normalized)

    def test_manual_adoption_or_changed_native_finish_never_rewrites_midi(self):
        self.take.adopted = True
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            self.recording._normalize(self.take)
        self.take.adopted = False
        self.clip.start_time = 16.
        with self.assertRaisesRegex(ValueError, 'does not cover'):
            self.recording._normalize(self.take)
        self.assertEqual(self.clip.loop_end, 12.)


if __name__ == '__main__':
    unittest.main()
