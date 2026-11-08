"""Real surface callback path with fake Live objects; no MIDI endpoints."""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Parameter:
    def __init__(self, value=0.5, low=0.0, high=1.0, enabled=True):
        self.value, self.min, self.max, self.is_enabled = value, low, high, enabled


class Track:
    def __init__(self, name="Lightpad 1", parent=None, group=True):
        self.name, self.group_track = name, parent
        self.is_foldable, self.is_grouped = group, parent is not None
        self.mute, self.color = False, 0xFF8000
        self.clip_slots = []
        self.mixer_device = SimpleNamespace(volume=Parameter(),
            sends=[Parameter() for _ in range(4)], panning=Parameter(0.0, -1.0, 1.0))


class FakeSurface:
    def __init__(self, instance):
        self._c_instance = instance

    def song(self):
        return self._c_instance.song

    def _send_midi(self, message, optimized=True):
        self._c_instance.sent.append(message)

    def log_message(self, message):
        self._c_instance.logs.append(message)

    def build_midi_map(self, handle):
        pass

    def update_display(self):
        pass

    def refresh_state(self):
        pass

    def disconnect(self):
        pass


forwarded = []
sys.modules["Live"] = SimpleNamespace(MidiMap=SimpleNamespace(
    forward_midi_cc=lambda *args: forwarded.append(args)))
sys.modules["_Framework"] = SimpleNamespace()
sys.modules["_Framework.ControlSurface"] = SimpleNamespace(ControlSurface=FakeSurface)
from LightpadController import protocol as p
from LightpadController.groups import LiveGroups
from LightpadController.surface import LightpadController


class SurfaceTests(unittest.TestCase):
    def make(self, tracks=None):
        self.now = [100.0]
        timer = patch('LightpadController.surface.time.monotonic', side_effect=lambda: self.now[0])
        timer.start()
        self.addCleanup(timer.stop)
        self.taps = 0
        def tap():
            self.taps += 1
        song = SimpleNamespace(tracks=tracks if tracks is not None else [Track()],
                               is_playing=True, current_song_time=12.25, tap_tempo=tap,
                               return_tracks=[SimpleNamespace(name="Return %d" % i) for i in range(4)])
        instance = SimpleNamespace(song=song, sent=[], logs=[], handle=lambda: 123)
        surface = LightpadController(instance)
        self.addCleanup(surface.disconnect)
        return surface, instance

    def command(self, surface, op, arg=0, seq=1, group=0):
        for cc, value in zip(range(60, 64), (seq, op, arg, seq)):
            surface.receive_midi((0xB0 + group, cc, value))
        if surface._parser.ready:
            # Model one outstanding command per group: allow its paced wire
            # result before the test sends another distinct command.
            for _ in range(5):
                self.now[0] += .1
                surface.update_display()

    def test_handshake_mapping_and_actual_mute(self):
        surface, instance = self.make()
        self.command(surface, p.SET_MUTE, 127)
        self.assertFalse(instance.song.tracks[0].mute)
        self.assertEqual(instance.sent, [])
        surface.build_midi_map(456)
        self.assertIn((123, 456, 15, 110), forwarded)
        self.assertIn((123, 456, 7, 103), forwarded)
        self.assertIn((123, 456, 0, 99), forwarded)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.assertEqual(instance.sent[:3], p.encode_metadata(True))
        self.command(surface, p.SET_MUTE, 127)
        self.assertTrue(instance.song.tracks[0].mute)
        self.assertEqual(surface._last[0][92], 127)
        self.assertEqual(surface._last[0][105], p.APPLIED)

    def test_duplicate_tap_is_not_reexecuted(self):
        surface, _ = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.command(surface, p.TAP_TEMPO)
        self.command(surface, p.TAP_TEMPO)
        self.assertEqual(self.taps, 1)

    def test_midi_map_preserves_unrelated_cc_that_interrupts_command(self):
        surface, instance = self.make()
        forwarded.clear()
        surface.build_midi_map(456)
        surface.receive_midi((0xBF, 110, p.VERSION))
        mapped = set((channel, cc) for _, _, channel, cc in forwarded)
        wire = [(0xB0, 60, 1), (0xB0, 99, 0), (0xB0, 61, p.SET_MUTE),
                (0xB0, 62, 127), (0xB0, 63, 1)]
        for message in wire:
            if (message[0] - 0xB0, message[1]) in mapped:
                surface.receive_midi(message)
        self.assertFalse(instance.song.tracks[0].mute)
        self.assertEqual(surface._last[0][105], p.NONE)

    def test_v2_peer_cannot_handshake_or_mutate(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, 2))
        self.assertFalse(surface._parser.ready)
        self.command(surface, p.SET_MUTE, 127)
        self.assertFalse(instance.song.tracks[0].mute)
        self.assertEqual(instance.sent, [])

    def test_tap_requires_unique_top_level_group_zero(self):
        for tracks in [[], [Track(), Track()], [Track("Other")], [Track(parent=Track())]]:
            surface, _ = self.make(tracks)
            surface.receive_midi((0xBF, 110, p.VERSION))
            self.command(surface, p.TAP_TEMPO)
            self.assertEqual(self.taps, 0)
            self.assertEqual(surface._last[0][105], p.NOT_READY)

    def test_actual_external_values_publish_and_rename_disables(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        track = instance.song.tracks[0]
        track.mute = True
        track.mixer_device.panning.value = -1.0
        surface.update_display()
        self.assertEqual(surface._last[0][92], 127)
        self.assertEqual(surface._last[0][102], 0)
        track.name = "Other"
        self.command(surface, p.SET_MUTE, 0)
        self.assertTrue(track.mute)
        self.assertEqual(surface._last[0][91], p.UNAVAILABLE)
        self.assertEqual(surface._last[0][105], p.NOT_READY)

    def test_ambiguous_nested_non_group_absent_targets_do_not_mutate(self):
        for tracks in [[], [Track(), Track()], [Track(parent=Track())], [Track(group=False)]]:
            surface, instance = self.make(tracks)
            surface.receive_midi((0xBF, 110, p.VERSION))
            self.command(surface, p.SET_MUTE, 127)
            self.assertTrue(all(not track.mute for track in tracks))
            self.assertEqual(surface._last[0][105], p.NOT_READY)

    def test_all_group_mixer_parameters_and_native_color_readback(self):
        tracks = [Track("Lightpad %d" % i) for i in range(1, 9)]
        surface, _ = self.make(tracks)
        surface.receive_midi((0xBF, 110, p.VERSION))
        for group, track in enumerate(tracks):
            for seq, op in enumerate([p.SET_VOLUME, p.SET_SEND_1, p.SET_SEND_2, p.SET_SEND_3, p.SET_CONTROL_4, p.SET_PAN]):
                self.command(surface, op, 127, seq, group)
            parameters = LiveGroups(surface.song())._parameters(track)
            self.assertTrue(all(parameter.value == parameter.max for parameter in parameters))
            self.assertEqual([surface._last[group][cc] for cc in (95, 96, 97)], [127, 64, 0])
        self.command(surface, p.SET_PAN, 64, 6, 0)
        self.assertEqual(tracks[0].mixer_device.panning.value, 0.0)
        self.assertEqual(surface._last[0][102], 64)

    def test_recording_is_rejected_without_session_backend(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        for seq, op in enumerate(range(p.BEGIN, p.ADVANCE + 1)):
            self.command(surface, op, seq=seq)
            self.assertEqual(surface._last[0][105], p.NOT_READY)
            self.assertEqual(surface._last[0][103] & 15, 0)
        self.assertFalse(instance.song.tracks[0].mute)

    def test_existing_clips_do_not_invent_reference_or_empty_state(self):
        group = Track()
        child = Track("existing layer", parent=group, group=False)
        child.clip_slots = [SimpleNamespace(has_clip=True)]
        surface, _ = self.make([group, child])
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.assertEqual(surface._last[0][91], p.UNAVAILABLE)
        self.assertEqual(surface._last[0][106], 0)

    def test_nested_descendants_never_produce_false_empty_state(self):
        group = Track()
        subgroup = Track("subgroup", parent=group)
        child = Track("existing layer", parent=subgroup, group=False)
        child.clip_slots = [SimpleNamespace(has_clip=True)]
        surface, _ = self.make([group, subgroup, child])
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.assertEqual(surface._last[0][91], p.UNAVAILABLE)

    def test_missing_or_disabled_sends_reject_without_parameter_writes(self):
        track = Track()
        track.mixer_device.sends = []
        surface, _ = self.make([track])
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.command(surface, p.SET_PAN, 127)
        self.assertEqual(track.mixer_device.panning.value, 0)
        self.assertEqual(surface._last[0][105], p.NOT_READY)

    def test_disconnect_emits_nothing_and_never_mutates(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        surface.disconnect()
        sent = list(instance.sent)
        self.command(surface, p.SET_MUTE, 127)
        surface.update_display()
        self.assertEqual(instance.sent, sent)
        self.assertFalse(instance.song.tracks[0].mute)


if __name__ == "__main__":
    unittest.main()
