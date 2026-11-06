"""Exercise the actual Remote Script without importing Live or opening MIDI."""

import importlib
from pathlib import Path
import sys
import types
import unittest


class FakeTrack:
    def __init__(self, name="Feedback probe", mute=False, group=True, nested=False):
        self.name = name
        self.is_foldable = group
        self.is_grouped = nested
        self._mute = mute
        self.mute_listeners = []
        self.name_listeners = []

    @property
    def mute(self):
        return self._mute

    @mute.setter
    def mute(self, value):
        if value != self._mute:
            self._mute = value
            for callback in tuple(self.mute_listeners):
                callback()

    def add_mute_listener(self, callback):
        self.mute_listeners.append(callback)

    def remove_mute_listener(self, callback):
        self.mute_listeners.remove(callback)

    def add_name_listener(self, callback):
        self.name_listeners.append(callback)

    def remove_name_listener(self, callback):
        self.name_listeners.remove(callback)

    def rename(self, name):
        self.name = name
        for callback in tuple(self.name_listeners):
            callback()


class FakeSong:
    def __init__(self, tracks):
        self.tracks = tracks
        self.listeners = []

    def add_tracks_listener(self, callback):
        self.listeners.append(callback)

    def remove_tracks_listener(self, callback):
        self.listeners.remove(callback)

    def replace_tracks(self, tracks):
        self.tracks = tracks
        for callback in tuple(self.listeners):
            callback()


class FakeControlSurface:
    def __init__(self, c_instance):
        self._c_instance = c_instance

    def song(self):
        return self._c_instance.song

    def _send_midi(self, message, optimized=True):
        self._c_instance.sent.append((message, optimized))

    def build_midi_map(self, handle):
        pass

    def refresh_state(self):
        pass

    def show_message(self, message):
        self._c_instance.messages.append(message)

    log_message = show_message

    def disconnect(self):
        self._c_instance.disconnected = True


forwarded = []
sys.modules["Live"] = types.SimpleNamespace(MidiMap=types.SimpleNamespace(
    forward_midi_cc=lambda *args: forwarded.append(args)))
sys.modules["_Framework"] = types.ModuleType("_Framework")
sys.modules["_Framework.ControlSurface"] = types.SimpleNamespace(ControlSurface=FakeControlSurface)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))
create_instance = importlib.import_module("LightpadIntegrationSpike").create_instance


class SpikeTests(unittest.TestCase):
    def make_surface(self, tracks):
        instance = types.SimpleNamespace(song=FakeSong(tracks), sent=[], messages=[],
                                         handle=lambda: 123, disconnected=False)
        surface = create_instance(instance)
        self.addCleanup(surface.disconnect)
        return surface, instance

    def test_startup_mapping_and_authoritative_commands(self):
        track = FakeTrack()
        surface, instance = self.make_surface([track])
        self.assertEqual(instance.sent, [((0xBF, 20, 127), False)])
        surface.build_midi_map(456)
        self.assertEqual(forwarded[-1], (123, 456, 15, 20))
        surface.receive_midi((0xBF, 20, 0))
        self.assertTrue(track.mute)
        self.assertEqual(instance.sent[-1], ((0xBF, 20, 0), False))
        self.assertEqual(len(instance.sent), 2)  # synchronous listener, no duplicate reply
        surface.receive_midi((0xBF, 20, 127))
        self.assertFalse(track.mute)
        self.assertEqual(instance.sent[-1], ((0xBF, 20, 127), False))
        surface.receive_midi((0xBF, 20, 127))
        self.assertEqual(len(instance.sent), 4)  # unchanged requests still receive actual state

    def test_external_changes_and_refresh(self):
        track = FakeTrack(mute=True)
        surface, instance = self.make_surface([track])
        track.mute = False
        self.assertEqual(instance.sent[-1], ((0xBF, 20, 127), False))
        surface.refresh_state()
        self.assertEqual(instance.sent[-2:], [((0xBF, 20, 127), False)] * 2)
        self.assertEqual(len(track.mute_listeners), 1)

    def test_ack_and_unrelated_messages_do_not_control_anything(self):
        target, other = FakeTrack(), FakeTrack("Another group")
        surface, instance = self.make_surface([target, other])
        for message in [(0xBF, 21, 0), (0xBE, 20, 0), (0xBF, 19, 0),
                        (0xBF, 20, 64), (0x9F, 20, 0), (0xBF, 20), ()]:
            surface.receive_midi(message)
        self.assertFalse(target.mute)
        self.assertFalse(other.mute)
        self.assertEqual(len(instance.sent), 1)

    def test_absent_ambiguous_nested_and_non_group_targets_are_disabled(self):
        for tracks in [[], [FakeTrack("Other")], [FakeTrack(), FakeTrack()],
                       [FakeTrack(nested=True)], [FakeTrack(group=False)]]:
            with self.subTest(tracks=tracks):
                surface, instance = self.make_surface(tracks)
                surface.receive_midi((0xBF, 20, 0))
                self.assertEqual(instance.sent, [])
                self.assertTrue(all(not track.mute for track in tracks))
                self.assertTrue(any("control disabled" in text for text in instance.messages))

    def test_target_removal_and_replacement(self):
        old = FakeTrack()
        surface, instance = self.make_surface([old])
        instance.song.replace_tracks([])
        self.assertEqual(old.mute_listeners, [])
        self.assertEqual(old.name_listeners, [])
        surface.receive_midi((0xBF, 20, 0))
        self.assertFalse(old.mute)
        self.assertEqual(len(instance.sent), 1)
        new = FakeTrack(mute=True)
        instance.song.replace_tracks([new])
        self.assertEqual(instance.sent[-1], ((0xBF, 20, 0), False))
        new.rename("No longer the probe")
        surface.receive_midi((0xBF, 20, 127))
        self.assertTrue(new.mute)
        self.assertEqual(new.mute_listeners, [])

    def test_disconnect_removes_listeners_and_stops_all_feedback(self):
        track = FakeTrack()
        surface, instance = self.make_surface([track])
        surface.disconnect()
        self.assertEqual(instance.song.listeners, [])
        self.assertEqual(track.mute_listeners, [])
        self.assertEqual(track.name_listeners, [])
        track.mute = True
        surface.receive_midi((0xBF, 20, 127))
        surface.refresh_state()
        self.assertTrue(track.mute)
        self.assertEqual(len(instance.sent), 1)
        self.assertTrue(instance.disconnected)


if __name__ == "__main__":
    unittest.main()
