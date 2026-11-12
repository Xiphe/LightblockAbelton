"""Production entry point with fake Live: no files, hardware or applications."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_surface as legacy
from LightpadController import configuration as config, health, protocol as p
from LightpadController import surface as module
from LightpadController.touch_protocol import encode_touch


class LiveSurfaceTests(unittest.TestCase):
    def make(self, tracks=()):
        self.now = 100.
        song = SimpleNamespace(tracks=list(tracks), return_tracks=[], scenes=[],
            is_playing=False, current_song_time=0., signature_numerator=4,
            signature_denominator=4, tempo=120., metronome=False,
            view=SimpleNamespace(selected_track=tracks[0] if tracks else None),
            get_current_beats_song_time=lambda: SimpleNamespace(bars=1, beats=1, sub_division=1, ticks=1))
        instance = SimpleNamespace(song=song, sent=[], logs=[])
        for item in (patch.multiple(config, LIVE_STATE_MODE=True, AUDIO_CONTROLLER_MANIFEST=None),
                     patch.object(module.time, 'monotonic', side_effect=lambda: self.now)):
            item.start()
            self.addCleanup(item.stop)
        surface = module.LightpadController(instance)
        self.addCleanup(surface.disconnect)
        return surface, instance

    @staticmethod
    def health_frames(messages):
        return [messages[i:i+5] for i, message in enumerate(messages) if message[:2] == (191, 114)]

    def test_no_groups_explained_before_pad_hello_then_live_rename_self_heals(self):
        group = legacy.Track('Ordinary group')
        surface, instance = self.make([group])
        surface.update_display()
        frame = self.health_frames(instance.sent)[-1]
        self.assertEqual(frame[1:3], [(191, 115, health.NO_GROUPS), (191, 116, health.NO_REMOTE_GROUPS)])
        self.assertFalse(surface._parser.ready)
        group.name = '[R3] Voice'
        self.now += .5
        surface.update_display()
        frame = self.health_frames(instance.sent)[-1]
        self.assertEqual(frame[1], (191, 115, health.READY))
        self.assertEqual(surface._engine.snapshot(2)[91], p.EMPTY)
        self.assertFalse(surface._configuration_failed)

    def test_saved_file_identity_is_not_a_production_startup_dependency(self):
        surface, instance = self.make([legacy.Track('[R1] Voice')])
        self.assertIsNotNone(surface._engine, instance.logs)
        self.assertFalse(hasattr(instance.song, 'file_path'))
        instance.song.file_path = '/a/completely/new/location/My renamed Set.als'
        surface.update_display()
        self.assertEqual(self.health_frames(instance.sent)[-1][1], (191, 115, health.READY))

    def test_stopped_transport_still_has_ready_groups_and_actual_mute(self):
        group = legacy.Track('[R1] Voice')
        surface, instance = self.make([group])
        surface.receive_midi((191, 110, p.VERSION))
        surface._dispatch(p.Command(1, 0, p.SET_MUTE, 127))
        self.assertTrue(group.mute)
        self.assertFalse(instance.song.is_playing)
        self.assertEqual(surface._engine.snapshot(0)[103] & p.CAP_RECORDING, 0)

    def test_partial_contact_does_not_scan_native_groups_before_complete_frame(self):
        surface, instance = self.make([legacy.Track('[R1] Voice')])
        surface.receive_midi((191, 110, p.VERSION))
        surface._ui.view, surface._ui.recording_group, surface._ui.active_take = 2, 0, 7
        messages = encode_touch(1, 0, 1.5, 1.5, 1, 0, view=2, group=0)
        with patch.object(surface._engine, 'snapshot', wraps=surface._engine.snapshot) as read:
            for message in messages[:-1]:
                surface.receive_midi(message)
            self.assertEqual(read.call_count, 0, 'partial contact CCs must not read Live state')
            surface.receive_midi(messages[-1])
            self.assertGreater(read.call_count, 0)

    def test_failed_initial_native_read_is_retried_without_reloading_script(self):
        from LightpadController.live_controller import LiveController
        calls = []
        def create(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError('Live is still opening the Set')
            return LiveController(*args, **kwargs)
        with patch('LightpadController.live_controller.LiveController', side_effect=create):
            surface, instance = self.make([legacy.Track('[R1] Voice')])
            self.assertTrue(surface._configuration_failed)
            self.now += .5
            surface.update_display()
        self.assertFalse(surface._configuration_failed)
        self.assertIsNotNone(surface._engine)
        self.assertEqual(self.health_frames(instance.sent)[-1][1], (191, 115, health.READY))

    def test_failed_recording_observation_keeps_view_and_allows_next_read(self):
        surface, instance = self.make([legacy.Track('[R1] Voice')])
        surface._ui.view, surface._ui.recording_group, surface._ui.active_take = 2, 0, 7
        with patch.object(surface._engine, 'active_recording', side_effect=RuntimeError('Native object changing')):
            surface._sync_live_recording()
        self.assertEqual((surface._ui.view, surface._ui.active_take), (2, 7))
        # A confirmed empty observation on the next read can close the view.
        surface._sync_live_recording()
        self.assertEqual(surface._ui.view, 0)
