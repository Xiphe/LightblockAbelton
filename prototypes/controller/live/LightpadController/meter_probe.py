"""Bounded, explicit native meter/launch experiment. No Live or MIDI imports.

This is an isolated lab command interface, not production scheduling or timing
validation. It permits meter edits during a queued fire so native retargeting
can be observed. Requested targets never become native timing evidence. All
groups must already be muted. Transport, routes, monitoring and mute are never
written. close() stops this object's polling without native cleanup.
"""

import json
import math
import os
import time

from .audio_fixture import AudioFixture, FixtureError, _bool, _get, _same
from .bar_clock_probe import observe_bar_time


OPERATIONS = frozenset(("inspect", "enable", "disable", "sample", "set_meter",
    "set_metronome", "start", "finish", "stop_attempt", "discard_attempt"))
_CLIP_FIELDS = ("start_time", "end_time", "loop_start", "loop_end", "start_marker",
    "end_marker", "length", "file_path", "playing_position", "is_recording",
    "is_playing", "is_audio_clip", "warping", "looping")
_PRESERVED_FIELDS = ("start_time", "end_time", "loop_start", "loop_end", "start_marker",
    "end_marker", "length", "file_path", "is_audio_clip", "warping", "looping")


class MeterProbeError(ValueError):
    pass


def _json_safe(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError, OverflowError):
        return {"unavailable": "UnsupportedJSONValue", "type": type(value).__name__}


def _observe(call):
    try:
        return _json_safe(call())
    except Exception as error:
        return {"unavailable": type(error).__name__}


def _properties(obj, names):
    return {name: _observe(lambda name=name: getattr(obj, name)) for name in names}


def _finite(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise MeterProbeError("%s must be a finite number" % label)
    return value


class MeterProbe:
    OPERATIONS = OPERATIONS

    def __init__(self, song, manifest, q_bar, monitor_off, quantizations, emit=None, now=None):
        self.song = song
        self.fixture = AudioFixture(song, manifest, q_bar, monitor_off)
        if (not isinstance(quantizations, dict) or not quantizations
                or any(type(name) is not str or not name for name in quantizations)):
            raise MeterProbeError("explicit named native quantizations are required")
        self.quantizations = dict(quantizations)
        self._emit_callback = emit if emit is not None else lambda event: None
        self._now = now if now is not None else time.monotonic
        self.enabled = False
        self._closed = False
        self._attempt = None
        self._token = 0
        self._sample = None
        self._last_fault = None
        self._layout, self._baseline = [], {}
        # Group slots aggregate child state rather than own independent clips.
        # Retain physical non-group slots, including unrelated tracks, so a
        # track-level stop can never silently affect a different row.
        for track in self.fixture._tracks:
            if _bool(track, "is_foldable"):
                continue
            slots = tuple(_get(track, "clip_slots"))
            index = len(self._layout)
            self._layout.append((track, slots))
            for row, slot in enumerate(slots):
                if _bool(slot, "has_clip"):
                    self._remember_clip((index, row), _get(slot, "clip"))

    def _emit(self, event):
        try:
            self._emit_callback(_json_safe(event))
        except Exception:
            # A logging failure must not cause a native operation to repeat.
            pass

    def _event(self, name, **fields):
        self._emit({"event": name, "observed_monotonic": _observe(self._now),
            "current_beat": _observe(lambda: self.song.current_song_time),
            "timing_assessment": "unverified", **fields})

    def _signature(self, clip):
        # A baseline without readable core loop/file identity cannot safely be
        # protected. Optional unsupported fields remain explicit in evidence.
        for name in ("loop_start", "loop_end"):
            _finite(_get(clip, name), "baseline native " + name)
        if _bool(clip, "is_audio_clip") and type(_get(clip, "file_path")) is not str:
            raise MeterProbeError("baseline native audio file path must be readable")
        return _properties(clip, _PRESERVED_FIELDS)

    def _remember_clip(self, key, clip):
        self._baseline[key] = {"clip": clip, "signature": self._signature(clip),
            "playing_at_binding": _bool(clip, "is_playing")}

    def _meter(self):
        numerator = _get(self.song, "signature_numerator")
        denominator = _get(self.song, "signature_denominator")
        if (type(numerator) is not int or not 1 <= numerator <= 99
                or type(denominator) is not int or denominator not in (1, 2, 4, 8, 16)):
            raise MeterProbeError("current native meter is outside supported 1..99 and 1/2/4/8/16")
        return numerator, denominator

    @staticmethod
    def _recording_evidence(attempt, clip):
        return (_bool(clip, "is_audio_clip") and _bool(clip, "is_recording")
            and _bool(clip, "is_playing") and not _bool(attempt["slot"], "is_triggered")
            and _get(attempt["track"], "playing_slot_index") == attempt["row"]
            and _get(attempt["track"], "fired_slot_index") == -1)

    def _guard(self, require_transport=False):
        try:
            self.fixture._check_bindings()
            self.fixture._globals()
            self.fixture._tempo()
            self._meter()  # Deliberately not AudioFixture's fixed-grid meter.
            if require_transport and not _bool(self.song, "is_playing"):
                raise MeterProbeError("native transport must already be running")
            for binding in self.fixture.groups.values():
                if not _bool(binding.group, "mute"):
                    raise MeterProbeError("all owned groups must already be muted for the meter lab")
            attempt = self._attempt
            owned = (attempt if attempt is not None and attempt["phase"] not in
                     ("accepted", "discarded") else None)
            for track in self.fixture._tracks:
                if _bool(track, "can_be_armed"):
                    if _bool(track, "implicit_arm"):
                        raise MeterProbeError("implicit arming must remain Off")
                    if _bool(track, "arm") and (owned is None or not _same(track, owned["track"])):
                        raise MeterProbeError("unrelated or unpermitted armed track")
            for index, (track, slots) in enumerate(self._layout):
                current = tuple(_get(track, "clip_slots"))
                if len(current) != len(slots) or any(not _same(a, b) for a, b in zip(current, slots)):
                    raise MeterProbeError("retained native slot identities changed")
                permitted_playing = {-1, -2}
                permitted_fired = {-1}
                for row, slot in enumerate(slots):
                    key = (index, row)
                    active = owned is not None and _same(track, owned["track"]) and row == owned["row"]
                    has_clip, triggered = _bool(slot, "has_clip"), _bool(slot, "is_triggered")
                    if active:
                        if not _same(slot, owned["slot"]):
                            raise MeterProbeError("active native slot was replaced")
                        if owned["clip"] is not None:
                            if not has_clip or not _same(_get(slot, "clip"), owned["clip"]):
                                raise MeterProbeError("active native clip was replaced or removed")
                            if not _bool(owned["clip"], "is_audio_clip"):
                                raise MeterProbeError("active native clip must remain audio")
                        elif has_clip:
                            if not owned["start_issued"] or not self._recording_evidence(owned, _get(slot, "clip")):
                                raise MeterProbeError("unknown non-recording clip cannot be adopted")
                        elif triggered and not owned["start_issued"]:
                            raise MeterProbeError("unrequested active slot trigger")
                        permitted_playing.add(row)
                        permitted_fired.add(row)
                        if owned["stop_issued"]:
                            permitted_fired.add(-2)
                    elif key in self._baseline:
                        baseline = self._baseline[key]
                        if not has_clip or not _same(_get(slot, "clip"), baseline["clip"]):
                            raise MeterProbeError("baseline native clip identity changed")
                        clip = baseline["clip"]
                        if self._signature(clip) != baseline["signature"]:
                            raise MeterProbeError("baseline native clip bounds or file identity changed")
                        if triggered or _bool(clip, "is_recording"):
                            raise MeterProbeError("unrequested baseline clip activity")
                        if _bool(clip, "is_playing"):
                            if not baseline["playing_at_binding"]:
                                raise MeterProbeError("unrequested baseline clip playback")
                            if owned is not None and _same(track, owned["track"]):
                                raise MeterProbeError("another row is playing on the active attempt track")
                            permitted_playing.add(row)
                    elif has_clip or triggered:
                        raise MeterProbeError("foreign clip or trigger appeared in an unowned row")
                if (_get(track, "playing_slot_index") not in permitted_playing
                        or _get(track, "fired_slot_index") not in permitted_fired):
                    raise MeterProbeError("foreign native row activity")
            return True
        except MeterProbeError:
            raise
        except (FixtureError, AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise MeterProbeError(str(error)) from error

    def _native_attempt(self):
        attempt = self._attempt
        if attempt is None:
            return None
        result = {name: _json_safe(attempt[name]) for name in ("token", "group", "layer", "row",
            "phase", "start_quantization", "expected_start_target", "finish_quantization",
            "expected_finish_target", "start_issued", "finish_issued", "stop_issued",
            "delete_issued", "recording_observation", "finish_observation")}
        result["track"] = _properties(attempt["track"], ("name", "arm", "implicit_arm",
            "playing_slot_index", "fired_slot_index"))
        result["slot"] = _properties(attempt["slot"], ("has_clip", "is_triggered", "is_recording"))
        if _observe(lambda: attempt["slot"].has_clip) in (True, 1):
            result["clip"] = _observe(lambda: _properties(attempt["slot"].clip, _CLIP_FIELDS))
        return result

    def _owned_observations(self):
        result = []
        for group, binding in sorted(self.fixture.groups.items()):
            for layer, track in enumerate(binding.layers):
                item = {"group": group, "layer": layer, "track": _properties(track,
                    ("name", "arm", "implicit_arm", "playing_slot_index", "fired_slot_index")), "slots": []}
                try:
                    slots = tuple(track.clip_slots)
                    item["slot_count"] = len(slots)
                    for row, slot in enumerate(slots):
                        state = _properties(slot, ("has_clip", "is_triggered", "is_recording"))
                        if state["has_clip"] in (False, 0) and state["is_triggered"] in (False, 0):
                            continue
                        state["row"] = row
                        if state["has_clip"] in (True, 1):
                            state["clip"] = _observe(lambda: _properties(slot.clip, _CLIP_FIELDS))
                        item["slots"].append(state)
                except Exception as error:
                    item["slots_unavailable"] = type(error).__name__
                result.append(item)
        return result

    def inspect(self):
        reasons = []
        try:
            self._guard()
        except Exception as error:
            reasons.append(str(error))
        return _json_safe({"enabled": self.enabled, "closed": self._closed, "probe_only": True,
            "device_capabilities_advertised": False, "timing_assessment": "unverified",
            "observation_is_atomic": False, "set_path": self.fixture.manifest["set_path"],
            "fingerprint": _observe(self.fixture.fingerprint), "scope_valid": not reasons,
            "guard_reasons": reasons, "initial_saved_hash_verified": True,
            "quantizations": {key: _json_safe(value) for key, value in self.quantizations.items()},
            "timing": observe_bar_time(self.song, clock=self._now),
            "globals": self.fixture._raw_globals(),
            "metronome": _observe(lambda: self.song.metronome),
            "groups_muted": {str(index): _observe(lambda binding=binding: binding.group.mute)
                             for index, binding in self.fixture.groups.items()},
            "attempt": self._native_attempt(), "sample": self._sample,
            "last_fault": self._last_fault, "owned_tracks": self._owned_observations()})

    def _enable(self, envelope):
        if self.enabled:
            raise MeterProbeError("meter probe is already enabled")
        path = envelope.get("confirm_set_path")
        expected = self.fixture.manifest["set_path"]
        if (type(path) is not str or not os.path.isabs(path)
                or os.path.realpath(path) != os.path.realpath(expected)):
            raise MeterProbeError("explicit exact scratch Set path confirmation is required")
        self._guard()
        if envelope.get("confirm_fingerprint") != self.fixture.fingerprint():
            raise MeterProbeError("fresh inspected fixture fingerprint is required")
        if _bool(self.song, "is_playing"):
            raise MeterProbeError("native transport must be stopped when enabling the meter probe")
        self.enabled = True

    def _quantum(self, envelope):
        name = envelope.get("quantization")
        if type(name) is not str or name not in self.quantizations:
            raise MeterProbeError("explicit quantization name from the injected allowlist is required")
        target = envelope.get("expected_target")
        if "expected_target" in envelope:
            _finite(target, "expected target")
        return name, target

    def _start(self, envelope):
        name, target = self._quantum(envelope)
        group, layer, row = (envelope.get(key) for key in ("group", "layer", "row"))
        if (type(group) is not int or group not in self.fixture.groups
                or type(layer) is not int or not 0 <= layer < len(self.fixture.groups[group].layers)
                or type(row) is not int):
            raise MeterProbeError("explicit owned integer group/layer/row is required")
        if self._attempt is not None and self._attempt["phase"] not in ("accepted", "discarded"):
            raise MeterProbeError("only one unaccepted native attempt is allowed")
        self._guard(require_transport=True)
        track = self.fixture.groups[group].layers[layer]
        slots = tuple(_get(track, "clip_slots"))
        if not 0 <= row < len(slots):
            raise MeterProbeError("row is outside the retained native slot capacity")
        slot = slots[row]
        if (_bool(slot, "has_clip") or _bool(slot, "is_triggered")
                or _get(track, "playing_slot_index") not in (-1, -2)
                or _get(track, "fired_slot_index") != -1):
            raise MeterProbeError("start needs an empty slot on an otherwise quiet native track")
        self._token += 1
        self._attempt = {"token": self._token, "group": group, "layer": layer, "row": row,
            "track": track, "slot": slot, "clip": None, "phase": "queued",
            "start_quantization": name, "expected_start_target": target,
            "finish_quantization": None, "expected_finish_target": None,
            "start_issued": False, "finish_issued": False, "stop_issued": False,
            "delete_issued": False, "recording_observation": None, "finish_observation": None}
        self._guard(require_transport=True)
        self._event("before.arm", attempt=self._native_attempt())
        track.arm = True
        self._guard(require_transport=True)
        # Retain issuance before entering native code. An exception must never
        # cause an ambiguous fire to repeat automatically.
        self._attempt["start_issued"] = True
        self._event("before.start", attempt=self._native_attempt())
        slot.fire(launch_quantization=self.quantizations[name])

    def _observe_attempt(self):
        attempt = self._attempt
        if attempt is None or attempt["phase"] in ("accepted", "discarded"):
            return
        self._guard()
        slot, track = attempt["slot"], attempt["track"]
        if attempt["clip"] is None and _bool(slot, "has_clip"):
            clip = _get(slot, "clip")
            if not self._recording_evidence(attempt, clip):
                raise MeterProbeError("recording observation is required before adopting a new clip")
            attempt["clip"] = clip
            attempt["recording_observation"] = {"observed_monotonic": _observe(self._now),
                "current_beat": _observe(lambda: self.song.current_song_time),
                "clip": _properties(clip, _CLIP_FIELDS)}
            if not attempt["stop_issued"]:
                attempt["phase"] = "recording"
            self._event("recording_observed", attempt=self._native_attempt())
        clip = attempt["clip"]
        if (attempt["finish_issued"] and not attempt["stop_issued"] and clip is not None
                and not _bool(slot, "is_triggered") and not _bool(clip, "is_recording")
                and _bool(clip, "is_playing") and _get(track, "fired_slot_index") == -1
                and _get(track, "playing_slot_index") == attempt["row"]):
            self._guard()
            attempt["finish_observation"] = {"observed_monotonic": _observe(self._now),
                "current_beat": _observe(lambda: self.song.current_song_time),
                "clip": _properties(clip, _CLIP_FIELDS)}
            self._event("before.disarm", attempt=self._native_attempt())
            track.arm = False
            # The finished clip is preserved and becomes immutable baseline.
            for index, (bound, _) in enumerate(self._layout):
                if _same(bound, track):
                    self._remember_clip((index, attempt["row"]), clip)
                    break
            attempt["phase"] = "accepted"
            self._event("finish_observed", attempt=self._native_attempt())
        elif attempt["stop_issued"] and self._quiet(attempt):
            if attempt["phase"] != "quiet":
                attempt["phase"] = "quiet"
                self._event("stop_observed", attempt=self._native_attempt())

    def _finish(self, envelope):
        name, target = self._quantum(envelope)
        self._observe_attempt()
        attempt = self._attempt
        if (attempt is None or attempt["phase"] != "recording" or attempt["clip"] is None
                or not self._recording_evidence(attempt, attempt["clip"])):
            raise MeterProbeError("finish requires an observed native recording")
        self._guard(require_transport=True)
        attempt.update(finish_quantization=name, expected_finish_target=target,
                       finish_issued=True, phase="finish_queued")
        self._event("before.finish", attempt=self._native_attempt())
        attempt["slot"].fire(launch_quantization=self.quantizations[name])

    def _unaccepted(self):
        if self._attempt is None or self._attempt["phase"] in ("accepted", "discarded"):
            raise MeterProbeError("an owned unaccepted attempt is required")
        return self._attempt

    @staticmethod
    def _quiet(attempt):
        track, slot, clip = attempt["track"], attempt["slot"], attempt["clip"]
        return (_get(track, "fired_slot_index") == -1
            and _get(track, "playing_slot_index") in (-1, -2) and not _bool(slot, "is_triggered")
            and (not _bool(slot, "has_clip") if clip is None else
                 _bool(slot, "has_clip") and _same(_get(slot, "clip"), clip)
                 and not _bool(clip, "is_recording") and not _bool(clip, "is_playing")))

    def _stop(self):
        self._observe_attempt()
        attempt = self._unaccepted()
        self._guard()
        if not attempt["stop_issued"]:
            attempt.update(stop_issued=True, phase="stop_queued")
            self._event("before.stop", attempt=self._native_attempt())
            attempt["track"].stop_all_clips(False)

    def _discard(self):
        self._observe_attempt()
        attempt = self._unaccepted()
        self._guard()
        if not attempt["stop_issued"] or not self._quiet(attempt):
            raise MeterProbeError("explicit stop and observed native quiet are required before discard")
        if _bool(attempt["slot"], "has_clip"):
            if attempt["delete_issued"]:
                raise MeterProbeError("native deletion already issued; refusing ambiguous retry")
            attempt["delete_issued"] = True
            self._event("before.discard", attempt=self._native_attempt())
            attempt["slot"].delete_clip()
            if _bool(attempt["slot"], "has_clip"):
                raise MeterProbeError("native deletion has not been observed")
            attempt["clip"] = None
        self._guard()
        if not self._quiet(attempt):
            raise MeterProbeError("native quiet changed after deletion")
        self._event("before.disarm", attempt=self._native_attempt())
        attempt["track"].arm = False
        attempt["phase"] = "discarded"
        self._event("discard_observed", attempt=self._native_attempt())

    def dispatch(self, envelope):
        if (not isinstance(envelope, dict) or type(envelope.get("op")) is not str
                or envelope["op"] not in OPERATIONS):
            raise MeterProbeError("supported meter probe operation object is required")
        operation = envelope["op"]
        if operation == "inspect":
            return self.inspect()
        if self._closed:
            raise MeterProbeError("meter probe is closed")
        if operation == "enable":
            self._enable(envelope)
        else:
            if not self.enabled:
                raise MeterProbeError("explicit enable is required before meter proof operations")
            try:
                if operation == "start":
                    self._start(envelope)
                elif operation == "finish":
                    self._finish(envelope)
                elif operation == "stop_attempt":
                    self._stop()
                elif operation == "discard_attempt":
                    self._discard()
                else:
                    self._guard()
                    if operation == "disable":
                        if self._attempt is not None and self._attempt["phase"] not in ("accepted", "discarded"):
                            raise MeterProbeError("resolve the unaccepted attempt before disabling")
                        self.enabled, self._sample = False, None
                    elif operation == "sample":
                        seconds = _finite(envelope.get("seconds"), "sample seconds")
                        if not .1 <= seconds <= 20:
                            raise MeterProbeError("sample seconds must be within 0.1..20")
                        start = _finite(self._now(), "sample monotonic start")
                        self._sample = {"started_at": start, "ends_at": start + seconds,
                                        "seconds": seconds}
                        self._event("sample_started", sample=self._sample)
                    elif operation == "set_meter":
                        numerator, denominator = envelope.get("numerator"), envelope.get("denominator")
                        if (type(numerator) is not int or not 1 <= numerator <= 99
                                or type(denominator) is not int or denominator not in (1, 2, 4, 8, 16)):
                            raise MeterProbeError("meter requires numerator 1..99 and denominator 1/2/4/8/16")
                        self._event("before.set_meter", requested={"numerator": numerator, "denominator": denominator},
                                    timing=observe_bar_time(self.song, clock=self._now))
                        self.song.signature_numerator = numerator
                        self._guard()
                        self.song.signature_denominator = denominator
                        self._event("meter_observed", requested={"numerator": numerator, "denominator": denominator},
                                    timing=observe_bar_time(self.song, clock=self._now))
                    elif operation == "set_metronome":
                        enabled = envelope.get("enabled")
                        if type(enabled) is not bool:
                            raise MeterProbeError("metronome enabled must be an actual boolean")
                        _bool(self.song, "metronome")
                        self.song.metronome = enabled
                        self._event("metronome_observed", requested=enabled,
                                    actual=_observe(lambda: self.song.metronome))
            except Exception as error:
                self._event("command_error", operation=operation, error=type(error).__name__, reason=str(error),
                            attempt=self._native_attempt())
                raise
        result = self.inspect()
        result.update(operation=operation, request_sequence=_json_safe(envelope.get("seq")))
        self._event("command_result", operation=operation, request_sequence=_json_safe(envelope.get("seq")),
                    attempt=self._native_attempt())
        return result

    def poll(self):
        if self.enabled and not self._closed:
            try:
                self._guard()
                self._observe_attempt()
                self._last_fault = None
                if self._sample is not None:
                    now = _finite(self._now(), "sample monotonic observation")
                    if now >= self._sample["ends_at"]:
                        sample, self._sample = self._sample, None
                        self._event("sample_completed", sample=sample)
                    elif now >= self._sample["started_at"]:
                        self._event("sample", timing=observe_bar_time(self.song, clock=self._now),
                                    globals=self.fixture._raw_globals(), owned_tracks=self._owned_observations(),
                                    attempt=self._native_attempt())
            except Exception as error:
                fault = {"error": type(error).__name__, "reason": str(error)}
                if fault != self._last_fault:
                    self._event("fault", **fault, attempt=self._native_attempt())
                self._last_fault = fault
        return self.inspect()

    def close(self):
        self._closed = True
        self._sample = None
