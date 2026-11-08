"""Bounded timing-only audio proof. No transport/routing/monitor/MIDI writes.

Starts disabled. Explicit enable requires the saved fixture path, file hash,
runtime topology and operator-confirmed inspection fingerprint. Only explicit
start/finish commands fire slots. Separate explicit stop_attempt and
discard_attempt probe stages test one cancellation candidate without fallback.
poll() observes native transitions and disarms the exact owned track only after
requested finish is observed. It never automatically discards a stopped take.
This driver is NOT the Session backend and makes no cancellation/chaining
capability claim. Do not install or enable without coordinator review.
"""

import hashlib
import json
import math
import os

from .proof_trace import ProofTrace
from .bar_clock_probe import observe_bar_time


class ProofRefused(Exception):
    pass


def routing(value):
    return {key: getattr(value, key, None) for key in ("display_name", "identifier")}


class AudioProofDriver:
    def __init__(self, song, manifest, q_bar, monitor_off, emit):
        self.song, self.manifest = song, manifest
        self.q_bar, self.monitor_off, self.emit = q_bar, monitor_off, emit
        self.enabled = False
        self.row = 0
        self.active = None
        self.accepted = {}
        self.fault = None
        self._tracks = None
        self._fingerprint = None
        self._trace = None
        self._token = 0
        self._sync_ui_confirmed = set()
        self._overdub = None

    def _global_state(self):
        return ProofTrace._read(self.song, ("record_mode", "session_record", "overdub",
            "count_in_duration", "is_counting_in", "loop", "is_ableton_link_enabled",
            "tempo_follower_enabled", "external_sync", "clip_trigger_quantization"))

    def _owned_audio_attempt_context(self):
        """Allow native Session flag transitions only around our exact audio take.

        Actual 12.2.7 audio recording flips overdub alongside session_record.
        Arrangement Record and every ownership/arm guard remain independent.
        """
        attempt = self.active
        if (attempt is None or self._tracks is None or attempt["layer"] in self.accepted
                or tuple(self.song.tracks) != self._tracks):
            return False
        track, slot = attempt["track"], attempt["slot"]
        if (track.has_midi_input or track.is_foldable
                or self._layers().get(attempt["layer"]) != track
                or track.clip_slots[self.row] != slot):
            return False
        clip = attempt["clip"]
        if clip is not None and slot.has_clip and slot.clip != clip:
            return False
        if attempt["state"] in ("finish_queued", "stop_requested", "stopped_observed"):
            return True
        return slot.has_clip and slot.clip.is_recording

    def _guard_globals(self, starting=False):
        state = self._global_state()
        for name in ("record_mode", "is_counting_in", "loop"):
            if type(state[name]) not in (bool, int) or state[name] != 0:
                raise ProofRefused("required global %s must be readable and Off" % name)
        if not isinstance(state["count_in_duration"], int) or state["count_in_duration"] != 0:
            raise ProofRefused("count-in must be readable and None")
        if type(state["session_record"]) not in (bool, int) or (starting and state["session_record"] != 0):
            raise ProofRefused("Session Record must be readable and Off before start")
        if type(state["overdub"]) not in (bool, int):
            raise ProofRefused("MIDI Arrangement Overdub must be readable")
        if (self._overdub is not None and state["overdub"] != self._overdub
                and not self._owned_audio_attempt_context()):
            raise ProofRefused("MIDI Arrangement Overdub changed after enable")
        if state["clip_trigger_quantization"] != self.q_bar:
            raise ProofRefused("global quantization must remain the inspected one bar")
        for key, prop in (("link", "is_ableton_link_enabled"),
                          ("tempo_follower", "tempo_follower_enabled"),
                          ("external_sync", "external_sync")):
            value = state[prop]
            if isinstance(value, dict):
                if key not in self._sync_ui_confirmed:
                    raise ProofRefused("native %s unavailable; explicit UI Off evidence required" % key)
            elif type(value) not in (bool, int) or value != 0:
                raise ProofRefused("%s must be Off" % key)
        return state

    def _topology(self):
        tracks = tuple(self.song.tracks)
        rows = []
        for track in tracks:
            parent = getattr(track, "group_track", None)
            rows.append({"name": track.name, "type": "GroupTrack" if track.is_foldable
                else "MidiTrack" if track.has_midi_input else "AudioTrack",
                "parent_index": next((i for i, candidate in enumerate(tracks)
                                      if parent == candidate), None),
                "slots": len(track.clip_slots), "frozen": bool(track.is_frozen),
                "devices": len(track.devices)})
            if track.name in self.manifest["owned_capture_tracks"] + [self.manifest["excluded_monitor"]]:
                rows[-1].update(input_type=routing(track.input_routing_type),
                    input_channel=routing(track.input_routing_channel),
                    output_type=routing(track.output_routing_type),
                    monitor=int(track.current_monitoring_state))
        return {"set_path": os.path.realpath(self.song.file_path),
                "tracks": rows, "scenes": len(self.song.scenes)}

    def inspect(self):
        topology = self._topology()
        fingerprint = hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest()
        arms = [{"name": t.name, "arm": bool(t.arm), "implicit_arm": bool(t.implicit_arm)}
                for t in self.song.tracks if t.can_be_armed]
        return {"enabled": self.enabled, "timing_only": True, "fingerprint": fingerprint,
                "fixture": topology, "arms": arms, "transport": bool(self.song.is_playing),
                "tempo": self.song.tempo, "meter": [self.song.signature_numerator,
                    self.song.signature_denominator], "active": self._active_state(),
                "globals": self._global_state(),
                "bar_time": observe_bar_time(self.song),
                "sync_ui_confirmed_off": sorted(self._sync_ui_confirmed),
                "accepted": sorted(self.accepted), "fault": self.fault}

    def _active_state(self):
        if self.active is None:
            return None
        state = {key: self.active[key] for key in ("layer", "token", "state", "target_beat")}
        state["native_track"] = ProofTrace._read(self.active["track"],
            ("arm", "playing_slot_index", "fired_slot_index"))
        slot = self.active["slot"]
        state["native_slot"] = ProofTrace._read(slot, ("has_clip", "is_triggered", "is_recording"))
        try:
            if slot.has_clip:
                state["native_clip"] = ProofTrace._read(slot.clip, ("is_recording", "is_playing",
                    "playing_position", "loop_start", "loop_end", "length", "file_path"))
        except (RuntimeError, AttributeError) as error:
            state["native_clip"] = {"unavailable": type(error).__name__}
        return state

    def _check_fixture(self):
        expected = self.manifest["tracks"]
        topology = self._topology()
        if not self.song.file_path or topology["set_path"] != os.path.realpath(self.manifest["set_path"]):
            raise ProofRefused("loaded saved Set path does not match fixture")
        with open(self.manifest["set_path"], "rb") as source:
            saved_hash = hashlib.sha256(source.read()).hexdigest()
        if saved_hash != self.manifest["set_sha256"]:
            raise ProofRefused("saved fixture hash changed; coordinator inspection required")
        if len(topology["tracks"]) != len(expected) or topology["scenes"] != self.manifest["scene_count"]:
            raise ProofRefused("fixture track/scene count changed")
        ids = {entry["als_track_id"]: entry["native_track_index"] for entry in expected}
        for actual, prepared in zip(topology["tracks"], expected):
            parent = ids.get(prepared["parent_als_group_id"])
            if (actual["name"] != prepared["name"] or actual["type"] != prepared["type"]
                    or actual["parent_index"] != parent or actual["frozen"]
                    or actual["devices"] != prepared["device_count"]):
                raise ProofRefused("fixture track identity/type/parent/device mismatch")
        owned = set(self.manifest["owned_capture_tracks"] + [self.manifest["excluded_monitor"]])
        for track in self.song.tracks:
            if track.name in owned:
                if track.current_monitoring_state != self.monitor_off:
                    raise ProofRefused("timing proof requires all fixture monitoring Off")
                kind = routing(track.input_routing_type)["display_name"]
                channel = routing(track.input_routing_channel)["display_name"]
                output = routing(track.output_routing_type)["display_name"]
                if (kind != "Ext. In" or not channel or channel.split()[0] != "1"
                        or output != self.manifest["owned_group"]):
                    raise ProofRefused("fixture requires Ext. In mono 1 to the exact owned parent group")
        return topology

    def _guard(self, require_playing=True, empty_discarded_slot=None):
        if not self.enabled or self.fault:
            raise ProofRefused("proof disabled or faulted")
        self._guard_globals()
        self._check_fixture()
        if tuple(self.song.tracks) != self._tracks:
            raise ProofRefused("runtime track objects changed")
        if self.inspect()["fingerprint"] != self._fingerprint:
            raise ProofRefused("routing/topology changed after enable")
        if (self.song.signature_numerator, self.song.signature_denominator, self.song.tempo) != (4, 4, 120):
            raise ProofRefused("initial timing proof requires inspected 120 BPM 4/4 fixture")
        if require_playing and not self.song.is_playing:
            raise ProofRefused("coordinator must start transport before this command")
        active_track = self.active["track"] if self.active else None
        for track in self.song.tracks:
            if track.can_be_armed and (track.implicit_arm or (track.arm and track != active_track)):
                raise ProofRefused("unrelated or implicit arm detected")
        for layer, track in self._layers().items():
            slot = track.clip_slots[self.row]
            known = self.accepted.get(layer)
            if self.active and self.active["layer"] == layer:
                if slot != self.active["slot"]:
                    raise ProofRefused("owned attempt slot replaced or reordered")
                known = self.active["clip"]
                if known is not None and (not slot.has_clip or slot.clip != known):
                    # Only the explicit discard operation can account for its
                    # own just-deleted clip. All other guards remain strict.
                    if slot != empty_discarded_slot or slot.has_clip:
                        raise ProofRefused("owned attempt clip replaced or removed")
            elif slot.has_clip != (known is not None) or (known is not None and slot.clip != known):
                raise ProofRefused("unknown/replaced clip in owned proof row")

    def _layers(self):
        return {letter: self._tracks[entry["native_track_index"]]
                for letter, name in zip("ABC", self.manifest["owned_capture_tracks"])
                for entry in self.manifest["tracks"] if entry["name"] == name}

    def enable(self, command):
        if self.enabled or self.active:
            raise ProofRefused("already enabled")
        self._check_fixture()
        inspection = self.inspect()
        if command.get("confirm_set_path") != self.manifest["set_path"]:
            raise ProofRefused("explicit confirmed scratch Set path required")
        if command.get("confirm_fingerprint") != inspection["fingerprint"]:
            raise ProofRefused("explicit inspected fingerprint required")
        confirmed = command.get("confirm_sync_ui_off", [])
        if (not isinstance(confirmed, list) or any(type(key) is not str or key not in
                ("link", "tempo_follower", "external_sync") for key in confirmed)):
            raise ProofRefused("invalid explicit sync UI evidence")
        self._sync_ui_confirmed = set(confirmed)
        global_state = self._guard_globals(starting=True)
        if self.song.is_playing or any(a["arm"] or a["implicit_arm"] for a in inspection["arms"]):
            raise ProofRefused("enable requires stopped transport and all tracks unarmed")
        self._tracks = tuple(self.song.tracks)
        for track in self._tracks:
            if any(slot.has_clip for slot in track.clip_slots):
                raise ProofRefused("initial proof fixture must contain no clips")
        self._fingerprint = inspection["fingerprint"]
        self._overdub = global_state["overdub"]
        group = next(t for t in self._tracks if t.name == self.manifest["owned_group"])
        self._trace = ProofTrace(self.song, group, self._layers().values(), self.emit, self.row)
        self._trace.attach()
        self.enabled = True
        return self.inspect()

    def start(self, command):
        self._guard()
        if self.active:
            raise ProofRefused("a take is already active")
        self._guard_globals(starting=True)
        layer = command.get("layer")
        if layer not in self._layers():
            raise ProofRefused("layer must be A, B or C")
        track = self._layers()[layer]
        slot = track.clip_slots[self.row]
        if slot.has_clip or track.playing_slot_index >= 0 or track.fired_slot_index >= 0:
            raise ProofRefused("target track/slot is not empty and stopped")
        self._token += 1
        target = (math.floor(self.song.current_song_time / 4.0) + 1) * 4.0
        self.active = {"layer": layer, "track": track, "slot": slot, "clip": None,
            "state": "queued", "token": self._token, "target_beat": target}
        try:
            self._trace.invoke("arm." + layer, lambda: setattr(track, "arm", True), token=self._token)
            self._guard()
            self._trace.invoke("fire." + layer, lambda: slot.fire(launch_quantization=self.q_bar),
                               token=self._token, target_beat=target)
        except Exception as error:
            self.fault = "start failed: " + type(error).__name__ + ": " + str(error)[:500]
            raise
        return self.inspect()

    def finish(self, command):
        self._guard()
        if self.active is None or self.active["state"] != "recording":
            raise ProofRefused("finish requires actual observed recording")
        attempt = self.active
        if (not attempt["slot"].has_clip or attempt["slot"].clip != attempt["clip"]
                or not attempt["clip"].is_recording):
            raise ProofRefused("owned clip is no longer recording")
        target = (math.floor(self.song.current_song_time / 4.0) + 1) * 4.0
        try:
            self._trace.invoke("finish." + attempt["layer"],
                lambda: attempt["slot"].fire(launch_quantization=self.q_bar),
                token=attempt["token"], target_beat=target)
        except Exception as error:
            self.fault = "finish failed: " + type(error).__name__ + ": " + str(error)[:500]
            raise
        attempt["state"], attempt["target_beat"] = "finish_queued", target
        return self.inspect()

    def _unaccepted_attempt(self):
        attempt = self.active
        if attempt is None or attempt["layer"] in self.accepted:
            raise ProofRefused("an unaccepted active attempt is required")
        track, slot, clip = attempt["track"], attempt["slot"], attempt["clip"]
        if self._layers().get(attempt["layer"]) != track or track.clip_slots[self.row] != slot:
            raise ProofRefused("attempt track or slot identity changed")
        if slot.has_clip:
            if clip is None or slot.clip != clip or clip in self.accepted.values():
                raise ProofRefused("attempt clip identity is not observed and unaccepted")
        elif clip is not None:
            raise ProofRefused("observed attempt clip disappeared")
        # Native 12.2.7 leaves playing=-2 after an immediate completed stop.
        # fired=-2 is a pending stop and remains excluded independently.
        if (track.playing_slot_index not in (-2, -1, self.row)
                or track.fired_slot_index not in (-1, self.row)):
            raise ProofRefused("another slot owns this attempt track's playback or launch")
        return attempt

    @staticmethod
    def _quiet_attempt(attempt):
        track, slot, clip = attempt["track"], attempt["slot"], attempt["clip"]
        return (track.fired_slot_index == -1 and track.playing_slot_index in (-2, -1)
                and not slot.is_triggered and
                (not slot.has_clip if clip is None else
                 slot.has_clip and slot.clip == clip
                 and not clip.is_recording and not clip.is_playing))

    def stop_attempt(self, command):
        """Request exactly one native positional-False stop, without cleanup."""
        self._guard()
        attempt = self._unaccepted_attempt()
        if attempt["state"] not in ("queued", "recording"):
            raise ProofRefused("stop requires a queued or observed recording attempt")
        if attempt["state"] == "recording" and not attempt["clip"].is_recording:
            raise ProofRefused("cached recording attempt is no longer natively recording")
        try:
            self._trace.invoke("stop_attempt." + attempt["layer"],
                lambda: attempt["track"].stop_all_clips(False), token=attempt["token"])
        except Exception as error:
            self.fault = "stop_attempt failed: " + type(error).__name__
            raise
        attempt["state"] = "stop_requested"
        return self.inspect()

    def discard_attempt(self, command):
        """Delete only an explicitly stopped, observed owned attempt."""
        self._guard()
        attempt = self._unaccepted_attempt()
        if attempt["state"] != "stopped_observed" or not self._quiet_attempt(attempt):
            raise ProofRefused("discard requires repeated quiet state after observed stop")
        track, slot = attempt["track"], attempt["slot"]
        try:
            if slot.has_clip:
                self._trace.invoke("discard_attempt." + attempt["layer"],
                    lambda: slot.delete_clip(), token=attempt["token"])
            if slot.has_clip:
                raise ProofRefused("attempt deletion did not leave the exact slot empty")
            # Recheck the same global/topology guards before disarming. The
            # single exception accounts only for this operation's own deletion.
            self._guard(empty_discarded_slot=slot)
            if (track.fired_slot_index != -1 or track.playing_slot_index not in (-2, -1)
                    or slot.is_triggered):
                raise ProofRefused("attempt track relaunched during discard")
            self._trace.invoke("disarm_discarded." + attempt["layer"],
                lambda: setattr(track, "arm", False), token=attempt["token"])
        except Exception as error:
            self.fault = "discard_attempt failed: " + type(error).__name__
            raise
        self._trace.mark("discard_observed", token=attempt["token"])
        self._overdub = self.song.overdub  # Observe native state; never write it.
        self.active = None
        return self.inspect()

    def dispatch(self, command):
        op = command["op"]
        if op == "inspect":
            return self.inspect()
        if op == "enable":
            return self.enable(command)
        if op == "start":
            return self.start(command)
        if op == "finish":
            return self.finish(command)
        if op == "stop_attempt":
            return self.stop_attempt(command)
        if op == "discard_attempt":
            return self.discard_attempt(command)
        if op == "disable":
            if self.active:
                raise ProofRefused("finish active take before disabling; no automatic stop/discard")
            self.enabled = False
            self.close()
            return self.inspect()
        raise ProofRefused("unknown proof operation")

    def poll(self):
        if not self.enabled or self.fault:
            return
        try:
            self._guard(require_playing=False)
            self._trace.watch_clips()
            self._trace.mark("poll")
            attempt = self.active
            if not attempt:
                return
            slot = attempt["slot"]
            if attempt["state"] in ("stop_requested", "stopped_observed"):
                self._unaccepted_attempt()
                if attempt["state"] == "stop_requested" and self._quiet_attempt(attempt):
                    attempt["state"] = "stopped_observed"
                    self._trace.mark("stopped_observed", token=attempt["token"])
                elif attempt["state"] == "stopped_observed" and not self._quiet_attempt(attempt):
                    raise ProofRefused("stopped attempt became active again")
                return
            if not slot.has_clip:
                return
            clip = slot.clip
            if attempt["clip"] is None:
                attempt["clip"] = clip
            if clip != attempt["clip"]:
                raise ProofRefused("attempt clip replaced")
            if attempt["state"] == "queued" and clip.is_recording:
                attempt["state"] = "recording"
                self._trace.mark("recording_observed", token=attempt["token"])
            elif attempt["state"] == "finish_queued" and not clip.is_recording and clip.is_playing:
                self._trace.invoke("disarm." + attempt["layer"],
                    lambda: setattr(attempt["track"], "arm", False), token=attempt["token"])
                self.accepted[attempt["layer"]] = clip
                self._trace.mark("finish_observed", token=attempt["token"])
                self._overdub = self.song.overdub  # Native Session transition baseline.
                self.active = None
        except Exception as error:
            self.fault = "observation failed: " + type(error).__name__ + ": " + str(error)[:500]
            if self._trace:
                self._trace.mark("fault", error_type=type(error).__name__, message=str(error)[:500])

    def close(self):
        if self._trace:
            self._trace.close()
