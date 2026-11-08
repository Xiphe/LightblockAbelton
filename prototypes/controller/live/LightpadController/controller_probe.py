"""Isolated no-MIDI facade over the normal audio controller for native proofs.

Starts disabled and uses the same fixture, Session and executor as the normal
controller. Probe capability bypass stays inside this explicit lab facade; no
device snapshots or MIDI are emitted. Mailbox nonce/replay/expiry validation
belongs to the caller. This module never changes transport, routing, monitoring
or mute. close() stops its own command/poll path without stopping native audio.
"""

import json
import os
import time

from . import protocol as p
from .audio_controller import AudioController
from .audio_fixture import AudioFixture


OPERATIONS = frozenset(("inspect", "enable", "disable", "begin", "accept",
                        "accept_next", "retry", "cancel", "advance"))
_GROUP_OPERATIONS = {"begin": p.BEGIN, "accept": p.ACCEPT, "accept_next": p.ACCEPT_NEXT,
                     "retry": p.RETRY, "cancel": p.ABORT, "advance": p.ADVANCE}
_CLIP_FIELDS = ("start_time", "end_time", "loop_start", "loop_end", "length",
                "playing_position", "is_recording", "is_playing", "is_audio_clip",
                "warping", "looping", "file_path")


class ControllerProbeError(ValueError):
    pass


def _json_safe(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        return {"unavailable": "UnsupportedJSONValue", "type": type(value).__name__}


def _observe(call):
    try:
        return _json_safe(call())
    except Exception as error:
        return {"unavailable": type(error).__name__}


def _properties(obj, names):
    return {name: _observe(lambda name=name: getattr(obj, name)) for name in names}


class ControllerProbe:
    def __init__(self, song, manifest, q_bar, monitor_off, emit=None):
        self.song = song
        self._emit_callback = emit if emit is not None else lambda event: None
        self._closed = False
        self._sequence = 0
        self._pending = {}
        self._native_changed = False
        self._poll_snapshot = None
        self._poll_snapshot_at = 0.0
        self.fixture = AudioFixture(song, manifest, q_bar, monitor_off)
        self.controller = AudioController(self.fixture, probe=True, auto_enable=False,
            validated_features=manifest.get("validated_features", ()), emit=self._backend_event)

    @property
    def enabled(self):
        return self.controller.backend.enabled

    def _emit(self, event):
        try:
            self._emit_callback(_json_safe(event))
        except Exception:
            # Evidence-output failure must not repeat a native operation.
            pass

    def _owned_observations(self, only_group=None):
        observations = []
        for group, binding in sorted(self.fixture.groups.items()):
            if only_group is not None and group != only_group:
                continue
            for index, track in enumerate(binding.layers):
                item = {"group": group, "layer": index,
                        "group_name": _observe(lambda: binding.group.name),
                        "track": _properties(track, ("name", "arm", "implicit_arm",
                            "playing_slot_index", "fired_slot_index")), "slots": []}
                try:
                    slots = tuple(track.clip_slots)
                except Exception as error:
                    item["slots"] = {"unavailable": type(error).__name__}
                else:
                    item["slot_count"] = len(slots)
                    for row, slot in enumerate(slots):
                        state = _properties(slot, ("has_clip", "is_triggered", "is_recording"))
                        # Empty quiet slots contribute their count, while all
                        # occupied, queued or unreadable rows retain evidence.
                        if state["has_clip"] in (False, 0) and state["is_triggered"] in (False, 0):
                            continue
                        state["row"] = row
                        if state["has_clip"] in (True, 1):
                            try:
                                state["clip"] = _properties(slot.clip, _CLIP_FIELDS)
                            except Exception as error:
                                state["clip"] = {"unavailable": type(error).__name__}
                        item["slots"].append(state)
                observations.append(item)
        return observations

    def _backend_event(self, event):
        self._native_changed = True
        self._emit({"event": "native_controller_event", "observed_monotonic": time.monotonic(),
                    "current_beat": _observe(lambda: self.song.current_song_time),
                    "backend_event": event,
                    "owned_tracks": self._owned_observations(event.get("group"))})

    def inspect(self):
        return {"enabled": bool(self.enabled), "closed": self._closed, "probe_only": True,
                "device_capabilities_advertised": False, "timing_assessment": "unverified",
                "observed_monotonic": time.monotonic(), "observation_is_atomic": False,
                "current_beat": _observe(lambda: self.song.current_song_time),
                "current_beat_units": "quarter-notes", "native_clip_units": "raw Live properties",
                "fixture": _observe(lambda: self.fixture.report(
                    allowed_armed=self.controller.backend.owned_armed_tracks())),
                "controller": _observe(self.controller.report),
                "owned_tracks": self._owned_observations()}

    def _enable(self, envelope):
        if self.enabled:
            raise ControllerProbeError("controller probe is already enabled")
        path = envelope.get("confirm_set_path")
        expected = self.fixture.manifest["set_path"]
        if (type(path) is not str or not path or not os.path.isabs(path)
                or os.path.realpath(path) != os.path.realpath(expected)
                or os.path.realpath(self.song.file_path) != os.path.realpath(expected)):
            raise ControllerProbeError("explicit confirmation of the exact loaded scratch Set path is required")
        fingerprint = self.fixture.fingerprint()
        if envelope.get("confirm_fingerprint") != fingerprint:
            raise ControllerProbeError("fresh inspected fixture fingerprint is required")
        self.controller.enable()

    def dispatch(self, envelope):
        if not isinstance(envelope, dict) or type(envelope.get("op")) is not str:
            raise ControllerProbeError("a proof operation object is required")
        operation = envelope["op"]
        if operation not in OPERATIONS:
            raise ControllerProbeError("unsupported controller proof operation")
        if operation == "inspect":
            state = self.inspect()
            self._emit({"event": "inspection", "state": state})
            return state
        if self._closed:
            raise ControllerProbeError("controller probe is closed")
        if operation == "enable":
            self._enable(envelope)
            state = self.inspect()
            self._emit({"event": "enabled", "state": state})
            return state
        if not self.enabled:
            raise ControllerProbeError("explicit enable is required before native proof operations")
        if operation == "disable":
            self.controller.backend.disable()
            state = self.inspect()
            self._emit({"event": "disabled", "state": state})
            return state
        group = envelope.get("group")
        if type(group) is not int or not 0 <= group < 8:
            raise ControllerProbeError("recording proof operations require integer group 0..7")
        self._sequence += 1
        command = p.Command(self._sequence % 128, group, _GROUP_OPERATIONS[operation], 0)
        request = {"operation": operation, "group": group, "sequence": command.seq,
                   "request_sequence": _json_safe(envelope.get("seq"))}
        self._emit({"event": "command_requested", **request})
        try:
            outcome = self.controller.dispatch(command)
        except Exception as error:
            self._emit({"event": "command_error", **request, "error": type(error).__name__,
                        "state": self.inspect()})
            raise
        if outcome is None:
            self._pending[command] = request
        state = self.inspect()
        state.update(operation=operation, group=group, outcome=outcome)
        self._emit({"event": "command_result", **request, "outcome": outcome, "state": state})
        return state

    def poll(self):
        completed = []
        if self.enabled and not self._closed:
            completed = self.controller.tick()
            for command, outcome in completed:
                request = self._pending.pop(command, {"group": command.group,
                    "sequence": command.seq, "operation": next((name for name, opcode in
                        _GROUP_OPERATIONS.items() if opcode == command.op), "unknown")})
                self._emit({"event": "command_completed", **request, "outcome": outcome,
                            "state": self.inspect()})
        # Native execution runs each display tick. The expensive all-track
        # readback is sampled at most 5 Hz while idle, with fresh observations
        # immediately after native events or command completion. Timestamped
        # cached observations are not relabeled as current samples.
        now = time.monotonic()
        if (self._poll_snapshot is None or self._native_changed or completed or self._closed
                or now - self._poll_snapshot_at >= 0.2):
            self._poll_snapshot = self.inspect()
            self._poll_snapshot_at = now
            self._native_changed = False
        return self._poll_snapshot

    def close(self):
        self._closed = True
