"""Coordinated take lifecycle; routing itself is tested in test_patching."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.live_state import LiveState
from LightpadController.live_recording import LiveRecording
from live_test_support import Song, Track, Clip

Q = {'q_bar': 4, 'q_half': 5, 'q_quarter': 7, 'q_eight': 9, 'q_sixtenth': 11}

class MultiSong(Song):
    def create_midi_track(self, index):
        self.create_audio_track(index)
        track = self.tracks[index]
        track.has_audio_input, track.has_midi_input = False, True
    def advance(self, position, apply=True):
        super().advance(position, apply)
        for track in self.tracks:
            for slot in track.clip_slots:
                if slot.has_clip:
                    slot.clip.is_audio_clip = track.has_audio_input

class Patch:
    enabled = True
    def __init__(self, song):
        self.sources = (NS(index=0, track=object(), midi=False, name='Mic'), NS(index=1, track=object(), midi=True, name='Piano'))
        self.monitored = None
        self.fail_index = None
    def selected(self, group): return self.sources
    def source(self, index): return self.sources[index]
    def validate(self, source): pass
    def validate_layer(self, track, source): pass
    def owners(self, source): return 0
    def set_monitor(self, group): self.monitored = group
    def start_monitor(self, group):
        self.monitored = group
        return NS(done=True, error=None)
    def start_layer(self, track, source):
        self.configure_layer(track, source)
        return NS(done=True, error=None)
    def layer_ready(self, track, source): self.validate_layer(track, source); return True
    def forget_layer(self, track, source): pass
    def cancel_layer(self, track): pass
    def configure_layer(self, track, source):
        if source.index == self.fail_index: raise ValueError('route unavailable')
        track.current_monitoring_state = 2

class MultiRecordingTests(unittest.TestCase):
    def setUp(self):
        self.song = MultiSong(groups=1)
        self.patch = Patch(self.song)
        self.r = LiveRecording(self.song, LiveState(self.song), Q, monotonic=lambda: 0, patching=self.patch)
    def start(self):
        self.assertEqual(self.r.begin(0), p.OUTCOME_QUEUED)
        self.r.tick()
        self.song.advance(4)
        self.r.tick()
        self.assertEqual(self.r.phase(), 'recording')
        return self.r.take
    def command(self, op): return p.Command(1, 0, op, 0)
    def test_audio_and_midi_share_start_finish_and_only_audio_can_normalize(self):
        take = self.start()
        members = self.r.members(take)
        self.assertEqual(len(members), 2)
        self.assertEqual([m.anchor for m in members], [4, 4])
        self.assertEqual([m.clip.is_audio_clip for m in members], [True, False])
        self.song.advance(5)
        self.assertEqual(self.r.accept(0), p.OUTCOME_QUEUED)
        self.r.tick()
        self.song.advance(8)
        self.r.tick()
        self.assertEqual(self.r.phase(), 'completed')
        self.assertTrue(all(not m.track.arm and m.clip.is_playing for m in members))
        self.assertFalse(self.r._normalizable(members[1]))
    def test_retry_cancels_every_unaccepted_layer_and_reuses_own_tracks(self):
        take = self.start()
        tracks = tuple(m.track for m in self.r.members(take))
        self.song.advance(5)
        cmd = self.command(p.RETRY)
        self.assertIsNone(self.r.cancel(cmd, retry=True))
        self.assertEqual(self.r.tick(), [(cmd, p.APPLIED)])
        self.assertEqual(tuple(m.track for m in self.r.members(self.r.take)), tracks)
        self.assertTrue(all(not m.slot.has_clip for m in self.r.members(self.r.take)))
        self.assertEqual(len({m.target for m in self.r.members(self.r.take)}), 1)
    def test_ambiguous_batch_fire_is_not_replayed(self):
        self.r.begin(0)
        members = self.r.members(self.r.take)
        members[0].slot.fire_error = 'after'
        self.r.tick()
        self.r.tick()
        self.assertEqual(len([c for c in self.song.calls if c[0] == 'fire']), 1)
        self.assertTrue(members[0].start_issued)
        self.assertFalse(members[1].start_issued)
        self.song.advance(4)
        self.r.tick()
        # The successful destination can be finished without touching the empty peer.
        self.song.advance(5)
        self.assertEqual(self.r.accept(0), p.OUTCOME_QUEUED)
        self.r.tick()
        self.assertEqual(len([c for c in self.song.calls if c[0] == 'fire']), 2)
    def test_cancel_preserves_foreign_recording_on_never_called_batch_tail(self):
        self.r.begin(0)
        take = self.r.take
        take.slot.fire_error = 'after'
        self.r.tick()
        self.song.advance(4)
        self.r.tick()
        peer = take.companions[0]
        foreign = Clip(recording=True)
        peer.slot.clip = foreign
        peer.track.playing_slot_index = peer.row
        cmd = self.command(p.ABORT)
        self.r.cancel(cmd)
        self.assertEqual(self.r.tick(), [(cmd, p.APPLIED)])
        self.assertIs(peer.slot.clip, foreign)
        self.assertTrue(foreign.is_recording)
        self.assertFalse(any(c[0] in ('stop', 'delete') and c[1] is peer.track for c in self.song.calls))

    def test_cancel_preserves_foreign_clip_in_unissued_peer(self):
        self.r.begin(0)
        take = self.r.take
        foreign = Clip(recording=True)
        take.companions[0].slot.clip = foreign
        take.companions[0].track.playing_slot_index = take.row
        cmd = self.command(p.ABORT)
        self.assertIsNone(self.r.cancel(cmd))
        self.assertEqual(self.r.tick(), [(cmd, p.APPLIED)])
        self.assertIs(take.companions[0].slot.clip, foreign)
        self.assertTrue(foreign.is_recording)
        self.assertFalse(any(c[0] in ('stop', 'delete') for c in self.song.calls))
    def test_accept_next_starts_all_next_destinations_at_shared_boundary(self):
        old = self.start()
        self.song.advance(5)
        self.assertEqual(self.r.accept(0, next_take=True), p.OUTCOME_QUEUED)
        following = old.following
        self.r.tick()
        self.song.advance(8)
        self.r.tick()
        self.assertIs(self.r.take, following)
        self.assertTrue(all(m.clip.is_recording for m in self.r.members(following)))
        self.assertTrue(all(not m.clip.is_recording for m in self.r.members(old)))
    def test_allocation_failure_reuses_earlier_empty_tracks(self):
        self.patch.fail_index = 1
        with self.assertRaises(ValueError): self.r.begin(0)
        first = tuple(self.song.tracks)
        self.patch.fail_index = None
        self.assertEqual(self.r.begin(0), p.OUTCOME_QUEUED)
        self.assertEqual(tuple(self.song.tracks), first)
    def test_missing_primary_does_not_trap_a_surviving_recording(self):
        take = self.start()
        peer = take.companions[0]
        self.song.tracks.remove(take.track)
        self.r.tick()
        self.assertIs(self.r.representative(), peer)
        self.song.advance(5)
        self.assertEqual(self.r.accept(0), p.OUTCOME_QUEUED)
        self.r.tick()
        self.song.advance(8)
        self.r.tick()
        self.assertEqual(self.r.phase(), 'completed')
        self.assertFalse(peer.clip.is_recording)
        self.assertTrue(take.clip.is_recording)  # Never stop the removed/moved track.

    def test_cancel_after_primary_removal_controls_only_surviving_member(self):
        take = self.start()
        peer = take.companions[0]
        self.song.tracks.remove(take.track)
        self.r.tick()
        cmd = self.command(p.ABORT)
        self.assertIsNone(self.r.cancel(cmd))
        self.assertEqual(self.r.tick(), [(cmd, p.APPLIED)])
        self.assertFalse(peer.slot.has_clip)
        self.assertTrue(take.clip.is_recording)
        self.assertFalse(any(c[0] in ('stop', 'delete') and c[1] is take.track for c in self.song.calls))

    def test_async_cancel_does_not_revisit_a_deleted_peer(self):
        take = self.start()
        peer = take.companions[0]
        # One destination stops immediately, the other confirms on a later tick.
        self.song.defer_stop = True
        original_stop = take.track.stop_all_clips
        def immediate(quantized):
            original_stop(quantized)
            take.track.quiet()
        take.track.stop_all_clips = immediate
        cmd = self.command(p.ABORT)
        self.r.cancel(cmd)
        self.assertEqual(self.r.tick(), [])
        self.assertFalse(take.slot.has_clip)
        self.assertTrue(peer.slot.has_clip)
        peer.track.quiet()
        self.assertEqual(self.r.tick(), [(cmd, p.APPLIED)])
        self.assertFalse(peer.slot.has_clip)
        self.assertIsNone(self.r.take)

    def test_partial_finish_failure_never_starts_reserved_successor(self):
        take = self.start()
        self.song.advance(5)
        self.r.accept(0, next_take=True)
        following = take.following
        take.slot.fire_error = 'after'
        self.r.tick()
        self.assertIsNone(take.following)
        self.assertFalse(any(m.start_issued for m in self.r.members(following)))
        self.song.advance(8)
        self.r.tick()
        self.assertEqual(self.r.phase(), 'recording')
        self.assertTrue(take.companions[0].clip.is_recording)
        self.assertFalse(any(m.start_issued for m in self.r.members(following)))

    def test_midi_ownership_change_while_queued_prevents_entire_batch_start(self):
        self.r.begin(0)
        self.patch.owners = lambda source: 2 if source.midi else 0
        self.r.tick()
        self.assertFalse(any(c[0] == 'fire' for c in self.song.calls))
        self.assertFalse(any(m.track.arm for m in self.r.members(self.r.take)))

    def test_external_rerouting_of_queued_destination_prevents_batch_launch(self):
        from test_patching import RoutedSong, route, execute
        from LightpadController.live_controller import LiveController
        song = RoutedSong(groups=1)
        song.add_source('Mic')
        song.add_source('Piano', midi=True)
        controller = LiveController(song, quantizations=Q, monotonic=lambda: 0)
        execute(controller, p.Command(10, 0, p.TOGGLE_PATCH, 0))
        execute(controller, p.Command(11, 0, p.TOGGLE_PATCH, 1))
        controller.recording.begin(0)
        while controller.patching.needs_preparation():
            controller.advance_preparations()
        controller.recording.take.track.input_routing_type = route(('off', 'input'), 'No Input')
        controller.recording.tick()
        self.assertFalse(any(call[0] == 'fire' for call in song.calls))
        self.assertFalse(any(m.track.arm for m in controller.recording.members(controller.recording.take)))

    def test_actual_patching_audio_midi_recording_and_native_rediscovery(self):
        from test_patching import RoutedSong, execute
        from LightpadController.live_controller import LiveController
        from LightpadController.patching import route_equal
        song = RoutedSong(groups=1)
        audio, midi = song.add_source('Mic'), song.add_source('Piano', midi=True)
        prepared = NS(type=1, class_name='PluginDevice')
        midi.devices = [prepared]
        controller = LiveController(song, quantizations=Q, monotonic=lambda: 0)
        self.assertEqual(execute(controller, p.Command(10, 0, p.TOGGLE_PATCH, 0)), p.APPLIED)
        self.assertEqual(execute(controller, p.Command(11, 0, p.TOGGLE_PATCH, 1)), p.APPLIED)
        self.assertEqual(controller.dispatch(p.Command(1, 0, p.BEGIN, 0)), p.OUTCOME_QUEUED)
        while controller.patching.needs_preparation():
            controller.advance_preparations()
        controller.recording.tick()
        song.advance(4)
        controller.recording.tick()
        take = controller.recording.take
        members = controller.recording.members(take)
        self.assertEqual([m.clip.is_audio_clip for m in members], [False, True])
        audio_take = next(m for m in members if m.clip.is_audio_clip)
        self.assertTrue(route_equal(audio_take.track.output_routing_type,
            controller.patching._track_route(audio_take.track, 'output', take.parent)))
        self.assertTrue(all(not m.track.name.startswith('[Patch]') for m in members))
        song.advance(5)
        self.assertEqual(controller.dispatch(p.Command(2, 0, p.ACCEPT, 0)), p.OUTCOME_QUEUED)
        controller.recording.tick()
        song.advance(8)
        controller.recording.tick()
        reopened = LiveController(song, quantizations=Q, monotonic=lambda: 0)
        self.assertEqual(reopened.patch_state()['assignments'][0], 3)
        self.assertEqual(reopened.patch_state()['sources'][1]['owners'], 1)
        self.assertEqual(midi.devices, [prepared])
        self.assertTrue(all(m.clip.is_playing and not m.clip.is_recording for m in members))

    def test_late_admission_defers_all_destinations_together(self):
        self.r.begin(0)
        self.song.advance(3.99)
        self.r.tick()
        members = self.r.members(self.r.take)
        self.assertEqual([m.target for m in members], [8, 8])
        self.song.advance(4.01)
        self.r.tick()
        self.assertEqual([m.slot.pending[1] for m in members], [8, 8])

if __name__ == '__main__': unittest.main()
