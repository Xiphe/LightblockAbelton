"""Trace uses real observation paths, while calls remain explicit test inputs."""

from pathlib import Path
import json
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController.proof_trace import ProofTrace


class Observed:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.listeners = {}

    def __getattr__(self, name):
        if name.startswith("add_") and name.endswith("_listener"):
            return lambda callback: self.listeners.setdefault(name[4:-9], []).append(callback)
        if name.startswith("remove_") and name.endswith("_listener"):
            return lambda callback: self.listeners[name[7:-9]].remove(callback)
        raise AttributeError(name)


class ArgumentError(TypeError):
    """Boost.Python's stale-native-object signature error shape."""


class DeletedClip(Observed):
    def __init__(self):
        super().__init__(is_recording=False, is_playing=False)
        self.deleted = False
        self.remove_calls = 0

    def remove_playing_status_listener(self, callback):
        self.remove_calls += 1
        if self.deleted:
            raise ArgumentError("Clip.remove_playing_status_listener(Clip, function) "
                                "did not match C++ signature")
        self.listeners["playing_status"].remove(callback)


class TraceTests(unittest.TestCase):
    def make(self):
        self.lines = []
        self.slot = Observed(has_clip=False, is_triggered=False, is_recording=False)
        self.track = Observed(name="A1", arm=False, playing_slot_index=-1,
                              fired_slot_index=-1, clip_slots=[self.slot])
        song = SimpleNamespace(current_song_time=3.5, tempo=120, is_playing=True,
                               signature_numerator=4, signature_denominator=4)
        return ProofTrace(song, Observed(name="proof", mute=True), [self.track],
                          self.lines.append, clock=lambda: 42.0)

    def test_attach_poll_and_cleanup_never_write_track_or_slot_state(self):
        trace = self.make()
        trace.attach()
        self.track.listeners["arm"][0]()
        snapshot = trace.mark("poll")
        self.assertEqual(snapshot["monotonic"], 42.0)
        self.assertEqual(snapshot["song"]["current_song_time"], 3.5)
        self.assertFalse(self.track.arm)
        self.assertFalse(self.slot.has_clip)
        trace.close()
        self.assertTrue(all(not listeners for listeners in self.track.listeners.values()))
        self.assertTrue(all(not listeners for listeners in self.slot.listeners.values()))

    def test_deleted_native_clip_argument_error_does_not_interrupt_remaining_cleanup(self):
        trace = self.make()
        trace.attach()
        clip = DeletedClip()
        self.slot.clip, self.slot.has_clip = clip, True
        trace.watch_clips()
        later = Observed(mute=False)
        trace._listen(later, "later", "mute")
        clip.deleted = True
        before = (self.track.arm, self.slot.has_clip, self.slot.clip,
                  clip.is_recording, clip.is_playing)
        trace.close()
        self.assertEqual(clip.remove_calls, 1)
        self.assertEqual(trace._listeners, [])
        self.assertTrue(all(not callbacks for callbacks in self.track.listeners.values()))
        self.assertTrue(all(not callbacks for callbacks in self.slot.listeners.values()))
        self.assertEqual(later.listeners["mute"], [])
        self.assertEqual((self.track.arm, self.slot.has_clip, self.slot.clip,
                          clip.is_recording, clip.is_playing), before)
        trace.close()
        self.assertEqual(clip.remove_calls, 1)

    def test_reentrant_close_does_not_remove_listeners_twice(self):
        trace = self.make()
        trace.attach()
        removals = []
        def reentrant(callback):
            removals.append(callback)
            trace.close()
        trace._listeners.insert(0, (object(), "reentrant", reentrant, object()))
        trace.close()
        self.assertEqual(len(removals), 1)
        self.assertEqual(trace._listeners, [])
        self.assertTrue(all(not callbacks for callbacks in self.track.listeners.values()))

    def test_explicit_calls_bracket_actual_state_and_keep_error_visible(self):
        trace = self.make()
        trace.invoke("arm", lambda: setattr(self.track, "arm", True), token=2)
        events = [json.loads(line.removeprefix("Lightpad proof ")) for line in self.lines]
        self.assertFalse(events[0]["layers"][0]["arm"])
        self.assertTrue(events[1]["layers"][0]["arm"])
        def fail():
            raise RuntimeError("fixture disappeared")
        with self.assertRaises(RuntimeError):
            trace.invoke("start", fail)
        self.assertIn('"event": "error.start"', self.lines[-1])
