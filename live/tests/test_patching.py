"""Routing contract tests. These simulate offered Native routes, not audio delivery."""
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.live_controller import LiveController
from LightpadController.live_state import LiveState
from LightpadController.patching import Patching as NativePatching, MONITOR_IN, MONITOR_OFF, route_equal
from live_test_support import Song, Track, Slot, Clip

Q = {'q_bar': 4, 'q_half': 5, 'q_quarter': 7, 'q_eight': 9, 'q_sixtenth': 11}


def settle(patching, job):
    for _ in range(600):
        if job.done:
            if job.error is not None:
                raise job.error
            return job.result
        patching.advance_preparations()
    raise AssertionError('routing preparation did not finish')


def execute(controller, command):
    outcome = controller.dispatch(command)
    if outcome is not None:
        return outcome
    for _ in range(600):
        controller.advance_preparations()
        for candidate, result in controller.tick():
            if candidate == command:
                return result
    raise AssertionError('controller routing command did not finish')


class Patching(NativePatching):
    """Existing contract cases drive real jobs through fake display callbacks."""
    def toggle(self, group, source): return settle(self, self.start_toggle(group, source))
    def set_monitor(self, group): return settle(self, self.start_monitor(group))
    def configure_layer(self, track, source): return settle(self, self.start_layer(track, source))
    def configure_input(self, track, source): return settle(self, super().configure_input(track, source))
    def configure_midi_output(self, track, source): return settle(self, super().configure_midi_output(track, source))
    def _new_patch_track(self, group, source): return settle(self, super()._new_patch_track(group, source))


def route(identifier, name):
    return {'identifier': identifier, 'display_name': name}


class RoutedSlot(Slot):
    def apply(self, now):
        super().apply(now)
        if self.has_clip:
            self.clip.is_audio_clip = self.track.has_audio_input


class RoutedTrack(Track):
    """Offered routes depend on current routing type, as in Live's API."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clip_slots = [RoutedSlot(self, i) for i in range(len(self.song.scenes))] if not self.is_foldable else []
        self.input_routing_type = route(('off', 'input'), 'No Input')
        self.input_routing_channel = route(('channel', 'input', 'postfx'), 'Post FX')
        self.output_routing_type = route(('off', 'output'), 'No Output')
        self.output_routing_channel = route(('channel', 'output', 'trackin'), 'Track In')
        self.current_monitoring_state = MONITOR_OFF
        self.instrument_endpoints = ('Prepared Instrument',)

    def _types(self, direction):
        return (route(('off', direction), 'No Input' if direction == 'input' else 'No Output'),) + ((route(('physical', 'all'), 'All Ins'),) if direction == 'input' else ()) + tuple(
            route(('track', id(track)), track.name) for track in self.song.tracks if track is not self)

    @property
    def available_input_routing_types(self):
        return self._types('input')

    @property
    def available_output_routing_types(self):
        return self._types('output')

    @property
    def available_input_routing_channels(self):
        return (route(('channel', 'input', 'all'), 'All Channels'), route(('channel', 'input', 'prefx'), 'Pre FX'),
                route(('channel', 'input', 'postfx'), 'Post FX'))

    @property
    def available_output_routing_channels(self):
        destination = next((track for track in self.song.tracks
                            if self.output_routing_type['identifier'] == ('track', id(track))), None)
        basic = (route(('channel', 'output', 'trackin'), 'Track In'),)
        return basic + (tuple(route(('instrument', id(destination), index), name)
                              for index, name in enumerate(destination.instrument_endpoints))
                        if destination is not None and destination.has_midi_input else ())


class RoutedSong(Song):
    def __init__(self, groups=2, scenes=2):
        super().__init__(groups=0, scenes=scenes)
        for index in range(groups):
            self.tracks.append(RoutedTrack(self, '[R%d] Music' % (index + 1), foldable=True))
        self.inputs = self.add_track('[Inputs]', foldable=True)

    def add_track(self, name, group=None, foldable=False, midi=False):
        track = RoutedTrack(self, name, group, foldable=foldable, audio=not midi)
        self.tracks.append(track)
        return track

    def add_source(self, name, midi=False, group=None):
        track = self.add_track(name, self.inputs if group is None else group, midi=midi)
        if midi:
            track.devices = [NS(type=1, class_name='PluginDevice')]
            track.input_routing_type = route(('physical', 'all'), 'All Ins')
            track.input_routing_channel = route(('channel', 'input', 'all'), 'All Channels')
        return track

    def _create(self, index, midi):
        self.calls.append(('create_midi_track' if midi else 'create_track', index))
        if self.create_error:
            raise RuntimeError('Live refused new track')
        parent = self.tracks[index - 1] if index else None
        if parent is not None and not parent.is_foldable:
            parent = parent.group_track
        if self.misplace_new_track:
            parent = None
        self.tracks.insert(index, RoutedTrack(self, 'MIDI' if midi else 'Audio', parent, audio=not midi))

    def create_audio_track(self, index):
        self._create(index, False)

    def create_midi_track(self, index):
        self._create(index, True)

    def create_scene(self, index):
        self.calls.append(('create_scene', index))
        self.scenes.append(object())
        for track in self.tracks:
            if not track.is_foldable:
                track.clip_slots.append(RoutedSlot(track, len(track.clip_slots)))


class PatchingTests(unittest.TestCase):
    def make(self):
        self.song = RoutedSong()
        self.audio = self.song.add_source('Microphone')
        self.midi = self.song.add_source('Piano', midi=True)
        self.state = LiveState(self.song)
        self.patch = Patching(self.song, self.state)
        return self.patch

    def test_native_routing_without_lom_identifier_uses_native_equality(self):
        class NativeRoute:
            def __init__(self, name, target): self.display_name, self.target = name, target
            def __eq__(self, other):
                return isinstance(other, NativeRoute) and self.target is other.target
        target = object()
        self.assertTrue(route_equal(NativeRoute('Piano', target), NativeRoute('Piano', target)))
        self.assertFalse(route_equal(NativeRoute('Piano', object()), NativeRoute('Piano', object())))
        self.assertFalse(route_equal(None, None))

    def test_catalog_flattens_leaf_descendants_in_live_order_with_bounded_identity_freeze(self):
        song = RoutedSong()
        folder = song.add_track('Organizational', song.inputs, foldable=True)
        first = song.add_source('Nested', midi=True, group=folder)
        second = song.add_source('Direct')
        song.add_track('Outside')
        patch = Patching(song, LiveState(song))
        self.assertEqual([source.track for source in patch.catalog()], [first, second])
        patch.freeze()
        song.tracks.remove(first)
        song.tracks.append(first)
        self.assertIs(patch.source(0).track, first)
        self.assertEqual([source.track for source in patch.catalog()], [first, second])
        song.tracks.remove(first)
        with self.assertRaises(ValueError):
            patch.source(0)
        patch.thaw()
        self.assertIs(patch.source(0).track, second)
        for index in range(30):
            song.add_source('Source %d' % index)
        self.assertEqual(len(patch.catalog()), 25)
        self.assertEqual([source.index for source in patch.catalog()], list(range(25)))

    def test_duplicate_inputs_marker_disables_recording_instead_of_legacy_fallback(self):
        self.make()
        self.song.add_track('[Inputs] Duplicate', foldable=True)
        controller = LiveController(self.song, quantizations=Q)
        self.assertTrue(controller.patching.enabled)
        self.assertEqual(controller.patch_state()['sources'], ())
        self.assertFalse(controller.has_sources(0))
        self.assertFalse(controller.snapshot(0)[103] & p.CAP_RECORDING)
        self.assertEqual(controller.dispatch(p.Command(1, 0, p.BEGIN, 0)), p.NOT_READY)
        self.assertFalse(any(call[0].startswith('create') for call in self.song.calls))

    def test_audio_patch_survives_new_engine_and_monitors_only_one_audio_path(self):
        patch = self.make()
        original_input = (self.audio.input_routing_type, self.audio.input_routing_channel)
        prepared_device = NS(type=1, class_name='PluginDevice')
        self.midi.devices = [prepared_device]
        patch.toggle(0, 0)
        selected = patch.selected(0)
        config = patch.patch_tracks(0)[0]
        self.assertEqual([source.track for source in selected], [self.audio])
        self.assertEqual(config.current_monitoring_state, MONITOR_OFF)
        self.assertEqual(self.audio.current_monitoring_state, MONITOR_IN)
        self.assertEqual(self.midi.current_monitoring_state, MONITOR_OFF)
        receiver = patch._return_for(selected[0], 0)
        self.assertIsNotNone(receiver)
        self.assertEqual(receiver.input_routing_type['display_name'], 'No Input')
        self.assertEqual(receiver.current_monitoring_state, MONITOR_IN)
        self.assertTrue(route_equal(receiver.output_routing_type,
                                    patch._track_route(receiver, 'output', self.state.groups[0].group)))
        reopened = Patching(self.song, LiveState(self.song))
        self.assertEqual(reopened.snapshot()['assignments'], (1, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(reopened.monitor(), 0)
        patch.toggle(1, 1)
        self.assertEqual(patch.monitor(), 1)
        self.assertEqual(self.audio.current_monitoring_state, MONITOR_OFF)
        self.assertEqual(config.current_monitoring_state, MONITOR_OFF)
        self.assertEqual(self.midi.current_monitoring_state, MONITOR_OFF)
        self.assertEqual(patch.patch_tracks(1)[0].current_monitoring_state, MONITOR_IN)
        self.assertEqual(patch.patch_tracks(1)[0].output_routing_channel['display_name'], 'Prepared Instrument')
        self.assertEqual((self.audio.input_routing_type, self.audio.input_routing_channel), original_input)
        self.assertEqual(self.midi.devices, [prepared_device])
        self.assertEqual(patch.patch_tracks(1)[0].devices, [])  # Same prepared instrument, no clone.

    def test_patch_removal_disconnects_without_deleting_tracks_or_user_clips(self):
        patch = self.make()
        patch.toggle(0, 0)
        config = patch.patch_tracks(0)[0]
        before = tuple(self.song.tracks)
        config.clip_slots[1].clip = Clip(playing=False)
        with self.assertRaises(ValueError):
            patch.toggle(0, 0)
        self.assertIsNotNone(config.clip_slots[1].clip)
        self.assertEqual(tuple(self.song.tracks), before)
        config.clip_slots[1].clip = None
        patch.toggle(0, 0)
        self.assertEqual(patch.selected(0), ())
        self.assertIn(config, self.song.tracks)
        self.assertEqual(config.input_routing_type['display_name'], 'No Input')

    def test_midi_owner_includes_muted_playback_and_clear_releases_instrument(self):
        patch = self.make()
        source = patch.source(1)
        layer = self.song.add_track('Accepted MIDI', self.state.groups[0].group, midi=True)
        patch.configure_layer(layer, source)
        layer.clip_slots[0].clip = Clip(audio=False)
        layer.playing_slot_index = 0
        layer.mute = True
        self.state.groups[0].group.mute = True
        self.state.refresh()
        self.assertEqual(patch.owners(source), 1)
        with self.assertRaises(ValueError):
            patch.toggle(1, 1)
        self.assertEqual(patch.selected(1), ())
        layer.quiet()
        self.state.refresh()
        self.assertEqual(patch.owners(source), 0)
        patch.toggle(1, 1)
        self.assertEqual([item.track for item in patch.selected(1)], [self.midi])

    def test_ambiguous_source_name_or_instrument_endpoint_fails_closed(self):
        patch = self.make()
        self.song.add_track('Piano')
        with self.assertRaises(ValueError):
            patch.toggle(0, 1)
        self.assertFalse(any(call[0] == 'fire' for call in self.song.calls))
        self.song.tracks.pop()
        self.midi.instrument_endpoints = ('Instrument A', 'Instrument B')
        with self.assertRaises(ValueError):
            patch.toggle(0, 1)
        self.assertEqual(self.midi.current_monitoring_state, MONITOR_OFF)
        self.assertFalse(any(call[0] == 'fire' for call in self.song.calls))

    def test_failed_instrument_endpoint_can_be_retried_without_allocating_more_tracks(self):
        patch = self.make()
        self.midi.instrument_endpoints = ('Instrument A', 'Instrument B')
        with self.assertRaises(ValueError): patch.toggle(0, 1)
        before = tuple(self.song.tracks)
        self.assertEqual(patch.selected(0), ())
        with self.assertRaises(ValueError): patch.toggle(0, 1)
        self.assertEqual(tuple(self.song.tracks), before)
        self.midi.instrument_endpoints = ('Prepared Instrument',)
        patch.toggle(0, 1)
        self.assertEqual(len([t for t in self.song.tracks if t.name.startswith('[Patch]')]), 1)
        self.assertEqual([s.track for s in patch.selected(0)], [self.midi])

    def test_midi_assignments_distinguish_shared_physical_input_and_keep_return_on(self):
        patch = self.make()
        second = self.song.add_source('Second Piano', midi=True)
        original_inputs = [(t.input_routing_type, t.input_routing_channel) for t in (self.midi, second)]
        patch.toggle(0, 1)
        patch.toggle(0, 2)
        reopened = Patching(self.song, LiveState(self.song))
        self.assertEqual(reopened.snapshot()['assignments'][0], 6)
        for source in reopened.selected(0):
            receiver = reopened._return_for(source, 0)
            self.assertIsNotNone(receiver)
            self.assertEqual(receiver.current_monitoring_state, MONITOR_IN)
            self.assertEqual(receiver.input_routing_type['display_name'], 'No Input')
        reopened.toggle(1, 0)
        self.assertEqual(reopened.monitor(), 1)
        for source in reopened.selected(0):
            self.assertEqual(reopened._return_for(source, 0).current_monitoring_state, MONITOR_IN)
        self.assertEqual([(t.input_routing_type, t.input_routing_channel) for t in (self.midi, second)], original_inputs)

    def test_idle_instrument_can_move_between_groups_with_distinct_persistent_returns(self):
        patch = self.make()
        patch.toggle(0, 1)
        first = patch._return_for(patch.source(1), 0)
        patch.toggle(1, 1)
        second = patch._return_for(patch.source(1), 1)
        self.assertIsNot(first, second)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(patch.snapshot()['assignments'][:2], (0, 2))
        patch.toggle(0, 1)
        self.assertIs(patch._return_for(patch.source(1), 0), first)
        self.assertEqual(len([t for t in self.song.tracks if t.name.startswith('[Return]')]), 2)

    def test_idle_midi_transfer_keeps_old_groups_other_sources_and_audio_assignments(self):
        patch = self.make()
        patch.toggle(0, 0)
        patch.toggle(0, 1)
        patch.toggle(1, 0)  # Audio remains assignable to multiple groups.
        patch.toggle(1, 1)
        self.assertEqual(patch.snapshot()['assignments'][:2], (1, 3))
        self.assertEqual(patch.monitor(), 1)
        self.assertTrue(all(t.current_monitoring_state == MONITOR_OFF for t in patch.patch_tracks(0)))

    def test_clear_retains_patch_then_idle_transfer_removes_only_the_old_midi_assignment(self):
        patch = self.make()
        patch.toggle(0, 0)
        patch.toggle(0, 1)
        layer = self.song.add_track('Accepted', self.state.groups[0].group, midi=True)
        patch.configure_layer(layer, patch.source(1))
        layer.clip_slots[0].clip = Clip(audio=False)
        layer.playing_slot_index = 0
        controller = LiveController(self.song, quantizations=Q)
        self.assertEqual(execute(controller, p.Command(10, 1, p.TOGGLE_PATCH, 1)), p.NOT_READY)
        cmd = p.Command(1, 0, p.ADVANCE, 0)
        self.assertEqual(controller.dispatch(cmd), p.APPLIED)
        self.assertEqual(controller.tick(), [])
        self.assertEqual(controller.patch_state()['assignments'][:2], (3, 0))
        self.assertEqual(controller.patch_state()['sources'][1]['owners'], 0)
        self.assertEqual(execute(controller, p.Command(11, 1, p.TOGGLE_PATCH, 1)), p.APPLIED)
        self.assertEqual(controller.patch_state()['assignments'][:2], (1, 2))
        self.assertIsNotNone(layer.clip_slots[0].clip)

    def test_failed_midi_transfer_restores_old_patch_monitor_and_source_output(self):
        patch = self.make()
        patch.toggle(0, 1)
        source_output = self.midi.output_routing_type
        old = patch.patch_tracks(0)[0]
        original = patch._monitor_steps
        def fail(group, selected=None):
            yield from original(group, selected)
            raise ValueError('Native route result unavailable')
        patch._monitor_steps = fail
        with self.assertRaises(ValueError): patch.toggle(1, 1)
        patch._monitor_steps = original
        self.assertEqual(patch.snapshot()['assignments'][:2], (2, 0))
        self.assertEqual(patch.monitor(), 0)
        self.assertEqual(old.current_monitoring_state, MONITOR_IN)
        self.assertTrue(route_equal(self.midi.output_routing_type, source_output))
        patch.toggle(1, 1)
        self.assertEqual(patch.snapshot()['assignments'][:2], (0, 2))

    def test_midi_transfer_preserves_foreign_content_in_old_patch(self):
        patch = self.make()
        patch.toggle(0, 1)
        old = patch.patch_tracks(0)[0]
        foreign = Clip(audio=False, playing=False)
        old.clip_slots[1].clip = foreign
        before = tuple(self.song.tracks)
        with self.assertRaisesRegex(ValueError, 'user music'):
            patch.toggle(1, 1)
        self.assertEqual(tuple(self.song.tracks), before)
        self.assertIs(old.clip_slots[1].clip, foreign)
        self.assertEqual(patch.snapshot()['assignments'][:2], (2, 0))

    def test_preinstrument_midi_fx_fail_closed_but_instrument_audio_fx_are_preserved(self):
        patch = self.make()
        instrument, audio_fx, midi_fx = NS(type=1), NS(type=2), NS(type=4)
        self.midi.devices = [midi_fx, instrument, audio_fx]
        self.assertFalse(patch.snapshot()['sources'][1]['available'])
        with self.assertRaisesRegex(ValueError, 'MIDI effects before'):
            patch.toggle(0, 1)
        self.assertFalse(any(c[0].startswith('create') for c in self.song.calls))
        self.midi.devices = [instrument, audio_fx]
        self.assertTrue(patch.snapshot()['sources'][1]['available'])
        patch.toggle(0, 1)
        self.assertEqual(self.midi.devices, [instrument, audio_fx])
        self.assertEqual(patch.patch_tracks(0)[0].devices, [])

    def test_midi_sixteen_native_endpoints_choose_channel_one(self):
        patch = self.make()
        self.midi.instrument_endpoints = tuple('%d-Drift' % n for n in range(1, 17))
        patch.toggle(0, 1)
        config = patch.patch_tracks(0)[0]
        self.assertEqual(config.output_routing_channel['display_name'], '1-Drift')
        self.assertEqual(config.input_routing_type['display_name'], 'All Ins')
        self.assertEqual(config.input_routing_channel['display_name'], 'All Channels')
        self.assertEqual(patch.snapshot()['assignments'][0], 2)

    def test_occupied_audio_return_is_preserved_and_never_repurposed(self):
        patch = self.make()
        patch.toggle(0, 1)
        receiver = patch._return_for(patch.source(1), 0)
        foreign = Clip(playing=False)
        receiver.clip_slots[0].clip = foreign
        with self.assertRaises(ValueError): patch.set_monitor(0)
        self.assertIs(receiver.clip_slots[0].clip, foreign)
        self.assertFalse(any(c[0] in ('delete', 'stop') for c in self.song.calls))

    def test_failed_monitor_selection_preserves_previous_working_group(self):
        patch = self.make()
        patch.toggle(0, 0)
        patch.toggle(1, 1)
        receiver = patch._return_for(patch.source(1), 1)
        foreign = Clip(playing=False)
        receiver.clip_slots[0].clip = foreign
        patch.set_monitor(0)
        originals = [(source.track, source.track.output_routing_type,
                      source.track.current_monitoring_state) for source in patch.catalog()]
        with self.assertRaisesRegex(ValueError, 'user music'):
            patch.set_monitor(1)
        self.assertEqual(patch.monitor(), 0)
        self.assertEqual(patch.patch_tracks(1)[0].current_monitoring_state, MONITOR_OFF)
        for source, output, monitor in originals:
            self.assertTrue(route_equal(source.output_routing_type, output))
            self.assertEqual(source.current_monitoring_state, monitor)
        self.assertIs(receiver.clip_slots[0].clip, foreign)

    def test_failed_monitor_route_restores_sources_and_disables_new_return(self):
        patch = self.make()
        old_mic = self.song.add_source('Working Mic')
        patch.toggle(0, 2)
        # An otherwise valid persisted assignment need not have a return yet.
        # The failure occurs after its first Native source-output write.
        source = patch.source(1)
        assignment = patch._new_patch_track(1, source)
        patch.configure_midi_output(assignment, source)
        patch.configure_input(assignment, source)
        self.state.refresh()
        original_output = self.midi.output_routing_type
        original_monitor = self.midi.current_monitoring_state
        before = tuple(self.song.tracks)
        original = patch._audio_to_group_steps
        def fail_after_native_route(source, group, created_returns=None):
            yield from original(source, group, created_returns)
            raise RuntimeError('Native route changed before reporting a failure')
        patch._audio_to_group_steps = fail_after_native_route
        with self.assertRaisesRegex(RuntimeError, 'reporting a failure'):
            patch.set_monitor(1)
        self.assertEqual(patch.monitor(), 0)
        self.assertEqual(old_mic.current_monitoring_state, MONITOR_IN)
        self.assertTrue(route_equal(self.midi.output_routing_type, original_output))
        self.assertEqual(self.midi.current_monitoring_state, original_monitor)
        created = [track for track in self.song.tracks if track not in before]
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].name.startswith('[Return]'))
        self.assertEqual(created[0].current_monitoring_state, MONITOR_OFF)
        self.assertFalse(any(c[0] in ('delete', 'stop') for c in self.song.calls))

    def test_failed_monitor_reconfiguration_restores_patch_input_channel(self):
        patch = self.make()
        patch.toggle(0, 0)
        patch.toggle(1, 1)
        patch.set_monitor(0)
        assignment = patch.patch_tracks(1)[0]
        original_input, original_channel = assignment.input_routing_type, assignment.input_routing_channel
        # A changed prepared controller is inherited only if the whole switch
        # succeeds. It must not leave a half-updated persistent assignment.
        self.midi.input_routing_type = route(('new', 'physical'), 'Computer Keyboard')
        original_configure = patch._input_steps
        def fail_after_input_change(track, source):
            if source.midi:
                track.input_routing_type = route(('new', 'physical'), 'Computer Keyboard')
                track.input_routing_channel = route(('new', 'channel'), 'Ch. 2')
                raise RuntimeError('Native input switch failed after applying')
            yield from original_configure(track, source)
        patch._input_steps = fail_after_input_change
        with self.assertRaises(RuntimeError):
            patch.set_monitor(1)
        self.assertEqual(patch.monitor(), 0)
        self.assertTrue(route_equal(assignment.input_routing_type, original_input))
        self.assertTrue(route_equal(assignment.input_routing_channel, original_channel))

    def test_patch_snapshot_does_not_walk_empty_scene_slots(self):
        patch = self.make()
        patch.toggle(0, 0)
        config = patch.patch_tracks(0)[0]

        class NoSceneScan:
            def __iter__(self):
                raise AssertionError('idle snapshot scanned scene slots')

        config.clip_slots = NoSceneScan()
        self.assertEqual(patch.snapshot()['assignments'][0], 1)

    def test_monitor_command_is_idempotent_activation_not_toggle(self):
        self.make()
        self.patch.toggle(0, 0)
        controller = LiveController(self.song, quantizations=Q)
        self.assertEqual(controller.control_indices(), tuple(range(8)))
        for sequence in (1, 2):
            self.assertEqual(execute(controller, p.Command(sequence, 0, p.SET_MONITOR, 0)), p.APPLIED)
            self.assertEqual(controller.patch_state()['monitor'], 0)
            self.assertEqual(self.audio.current_monitoring_state, MONITOR_IN)

    def test_constants_use_first_four_direct_children_preserving_nested_drum_group(self):
        song = RoutedSong()
        constants = song.add_track('Constants', foldable=True)
        drums = song.add_track('Drums', constants, foldable=True)
        nested = song.add_track('Kick', drums, midi=True)
        voice = song.add_track('Voiceover', constants)
        guest1 = song.add_track('Guest1', constants)
        guest2 = song.add_track('Guest2', constants)
        song.add_track('Ignored fifth', constants)
        controller = LiveController(song, quantizations=Q)
        self.assertEqual(controller.state.constants, {8: drums, 9: voice, 10: guest1, 11: guest2})
        self.assertEqual(controller.control_indices(), tuple(range(12)))
        self.assertNotIn(nested, controller.state.constants.values())
        self.assertEqual(controller.snapshot(8)[103], p.CAP_MIXER)
        self.assertEqual(controller.dispatch(p.Command(1, 8, p.SET_MUTE, 127)), p.APPLIED)
        self.assertTrue(drums.mute)
        self.assertFalse(nested.mute)
        self.assertEqual(controller.dispatch(p.Command(2, 9, p.SET_VOLUME, 100)), p.APPLIED)
        self.assertAlmostEqual(voice.mixer_device.volume.value, 100 / 127)
        self.assertEqual(controller.dispatch(p.Command(3, 8, p.BEGIN, 0)), p.NOT_READY)
        self.assertFalse(any(call[0] == 'fire' for call in song.calls))


if __name__ == '__main__':
    unittest.main()
