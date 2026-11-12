"""Optional scratch-proof instrumentation. Not attached by the controller.

The coordinator must pass explicit fixture objects and retain the JSON log.
Snapshots/listeners never write Live properties. invoke() runs only the exact
callable provided by the proof driver, bracketed by observations. Listener
timestamps describe callback delivery, not sample-accurate recording onset.
"""

import json
import time

from .bar_clock_probe import observe_bar_time


class ProofTrace:
    def __init__(self, song, group, layers, emit, row=0, clock=time.monotonic):
        self.song, self.group, self.layers = song, group, tuple(layers)
        self.emit, self.row, self.clock = emit, row, clock
        self._listeners = []

    @staticmethod
    def _read(obj, names):
        result = {}
        for name in names:
            try:
                result[name] = getattr(obj, name)
            except (RuntimeError, AttributeError) as error:
                result[name] = {"unavailable": type(error).__name__}
        return result

    def mark(self, event, **details):
        record = {"event": event, "monotonic": self.clock(), "details": details,
                  "bar_time": observe_bar_time(self.song, self.clock),
                  "song": self._read(self.song, ("current_song_time", "tempo", "is_playing",
                      "signature_numerator", "signature_denominator", "record_mode",
                      "session_record", "overdub", "count_in_duration", "is_counting_in",
                      "loop", "is_ableton_link_enabled", "tempo_follower_enabled", "external_sync")),
                  "group": self._read(self.group, ("name", "mute")), "layers": []}
        for track in self.layers:
            entry = self._read(track, ("name", "arm", "playing_slot_index", "fired_slot_index"))
            try:
                slot = track.clip_slots[self.row]
                entry["slot"] = self._read(slot, ("has_clip", "is_triggered", "is_recording"))
                if slot.has_clip:
                    entry["clip"] = self._read(slot.clip, ("is_recording", "is_playing",
                        "playing_position", "start_time", "end_time", "loop_start",
                        "loop_end", "length", "warping", "file_path"))
            except (RuntimeError, AttributeError, IndexError) as error:
                entry["unavailable"] = type(error).__name__
            record["layers"].append(entry)
        self.emit("Lightpad proof " + json.dumps(record, sort_keys=True))
        return record

    def invoke(self, label, call, **details):
        self.mark("before." + label, **details)
        try:
            result = call()
        except Exception as error:
            self.mark("error." + label, error_type=type(error).__name__)
            raise
        self.mark("after." + label, **details)
        return result

    def attach(self):
        self.close()
        for obj, label, properties in [(self.group, "group", ("mute",))] + [
                (track, "layer%d" % index, ("arm", "playing_slot_index", "fired_slot_index"))
                for index, track in enumerate(self.layers)]:
            for prop in properties:
                self._listen(obj, label, prop)
        self.watch_clips()
        self.mark("attached")

    def _listen(self, obj, label, prop):
        add = getattr(obj, "add_%s_listener" % prop, None)
        remove = getattr(obj, "remove_%s_listener" % prop, None)
        if add is None or remove is None:
            self.mark("listener_unavailable", object=label, property=prop)
            return
        callback = lambda: self.mark("observed." + label + "." + prop)
        try:
            add(callback)
            self._listeners.append((obj, prop, remove, callback))
        except (RuntimeError, ValueError):
            self.mark("listener_unavailable", object=label, property=prop)

    def watch_clips(self):
        """Call after has_clip changes/polling, to observe newly created clips."""
        for index, track in enumerate(self.layers):
            try:
                slot = track.clip_slots[self.row]
                if not any(obj == slot and prop == "has_clip" for obj, prop, _, _ in self._listeners):
                    self._listen(slot, "slot%d" % index, "has_clip")
                if slot.has_clip and not any(obj == slot.clip for obj, _, _, _ in self._listeners):
                    self._listen(slot.clip, "clip%d" % index, "playing_status")
            except (RuntimeError, AttributeError, IndexError):
                self.mark("fixture_unavailable", layer=index)

    def close(self):
        # Drain first so repeated/reentrant close never unregisters twice.
        listeners, self._listeners = self._listeners, []
        for _, _, remove, callback in listeners:
            try:
                remove(callback)
            except (RuntimeError, ValueError, TypeError):
                # Deleted native clips can raise Boost.Python.ArgumentError
                # (a TypeError) on this retained unregistration method.
                # Continue removing the remaining live-object listeners.
                pass
