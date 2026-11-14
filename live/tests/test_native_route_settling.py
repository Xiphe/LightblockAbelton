"""Native-style routing offers become visible only after a display callback."""
import unittest

from test_patching import RoutedTrack, RoutedSong, route, Q
from LightpadController.patching import Patching, MONITOR_OFF
from LightpadController.live_state import LiveState
from LightpadController.live_recording import LiveRecording


class DelayedTrack(RoutedTrack):
    def __init__(self, *args, **kwargs):
        self._input_type = self._output_type = None
        self._effective_input = self._effective_output = None
        self.type_writes = []
        super().__init__(*args, **kwargs)
        self.input_routing_channel = route(('factory', 'input'), 'Post Mixer')

    @property
    def input_routing_type(self): return self._input_type

    @input_routing_type.setter
    def input_routing_type(self, value):
        self._input_type = value
        self.type_writes.append(('input', value['display_name'] if isinstance(value, dict) else value))

    @property
    def output_routing_type(self): return self._output_type

    @output_routing_type.setter
    def output_routing_type(self, value):
        self._output_type = value
        self.type_writes.append(('output', value['display_name'] if isinstance(value, dict) else value))

    def native_callback(self):
        self._effective_input = self._input_type
        self._effective_output = self._output_type

    @property
    def available_input_routing_channels(self):
        if self._effective_input is None or self._effective_input['display_name'] == 'No Input':
            return (route(('factory', 'input'), 'Post Mixer'),)
        if self._effective_input['display_name'] == 'All Ins':
            return (route(('channel', 'input', 'all'), 'All Channels'),)
        return super().available_input_routing_channels

    @property
    def available_output_routing_channels(self):
        destination = next((track for track in self.song.tracks
                            if self._effective_output is not None
                            and self._effective_output['identifier'] == ('track', id(track))), None)
        basic = (route(('channel', 'output', 'trackin'), 'Track In'),)
        return basic + (tuple(route(('instrument', id(destination), index), name)
                              for index, name in enumerate(destination.instrument_endpoints))
                        if destination is not None and destination.has_midi_input else ())


class DelayedSong(RoutedSong):
    def _create(self, index, midi):
        self.calls.append(('create_midi_track' if midi else 'create_track', index))
        parent = self.tracks[index - 1] if index else None
        if parent is not None and not parent.is_foldable:
            parent = parent.group_track
        self.tracks.insert(index, DelayedTrack(self, 'MIDI' if midi else 'Audio', parent, audio=not midi))

    def callback(self, patch):
        for track in self.tracks:
            if isinstance(track, DelayedTrack): track.native_callback()
        patch.advance_preparations()


class NativeRouteSettlingTests(unittest.TestCase):
    def make(self):
        song = DelayedSong(groups=1)
        song.add_source('Mic')
        patch = Patching(song, LiveState(song))
        return song, patch

    def finish(self, song, patch, job):
        for _ in range(100):
            if job.done:
                if job.error is not None: raise job.error
                return
            song.callback(patch)
        self.fail('preparation did not settle')

    def test_audio_assignment_waits_for_new_channel_offers_without_replaying_toggle(self):
        song, patch = self.make()
        job = patch.start_toggle(0, 0)
        # Calls without a new Native view cannot conjure the Post FX offer.
        for _ in range(8): patch.advance_preparations()
        self.assertFalse(job.done)
        self.assertEqual(len(patch.patch_tracks(0)), 1)
        destination = patch.patch_tracks(0)[0]
        self.assertEqual(destination.current_monitoring_state, MONITOR_OFF)
        self.assertEqual(destination.type_writes.count(('input', 'Mic')), 1)
        self.finish(song, patch, job)
        self.assertEqual(patch.snapshot()['assignments'][0], 1)
        self.assertEqual(patch.monitor(), 0)
        self.assertEqual(destination.input_routing_channel['display_name'], 'Post FX')
        self.assertEqual(destination.type_writes.count(('input', 'Mic')), 1)

    def test_unsettled_layer_never_arms_and_keeps_the_next_safe_boundary(self):
        song, patch = self.make()
        self.finish(song, patch, patch.start_toggle(0, 0))
        recording = LiveRecording(song, patch.state, Q, monotonic=lambda: 0, patching=patch)
        recording.begin(0)
        take = recording.take
        for _ in range(5): recording.tick()
        self.assertFalse(take.track.arm)
        self.assertFalse(take.start_issued)
        self.assertEqual(recording.issues, {})
        song.advance(3.99)
        recording.tick()
        self.assertEqual(take.target, 8)
        for _ in range(60):
            song.callback(patch)
            recording.tick()
            if not patch.needs_preparation(): break
        self.assertFalse(take.start_issued)
        song.advance(4.1)
        recording.tick()
        self.assertTrue(take.start_issued)
        self.assertEqual(take.slot.pending[1], 8)
        self.assertEqual(patch._layer_jobs, [], 'ready takes retain no completed routing jobs')

    def test_completed_preparation_does_not_overwrite_later_manual_routing(self):
        song, patch = self.make()
        self.finish(song, patch, patch.start_toggle(0, 0))
        recording = LiveRecording(song, patch.state, Q, monotonic=lambda: 0, patching=patch)
        recording.begin(0)
        take = recording.take
        for _ in range(60):
            song.callback(patch)
            if not patch.needs_preparation(): break
        # First readiness observation consumes the preparation, before enqueue.
        recording._source_ready(take)
        take.track.input_routing_type = route(('off', 'input'), 'No Input')
        recording.tick()
        self.assertFalse(take.start_issued)
        self.assertEqual(take.track.input_routing_type['display_name'], 'No Input')


if __name__ == '__main__': unittest.main()
