"""Read-only bindings and clock inputs for a prepared scratch audio fixture.

Only construction reads the manifest's Set file to verify its initial hash.
Saving that same loaded Set later is allowed. Runtime track identities, paths,
routing, monitoring and required native guards are checked again by validate.
The bar origin is accepted only from an explicitly verified fixed manifest
grid; this module never reconstructs meter-marker history or guesses 4/4.
"""

from collections import namedtuple
import copy
import hashlib
import json
import math
import os

from .timing import Clock, ONE_BAR, REFERENCE
from .native_bar_grid import read_bar_grid
from .launch_grid import launch_window


GroupBinding = namedtuple("GroupBinding", "group layers")


class FixtureError(Exception):
    pass


def _get(obj, name):
    try:
        return getattr(obj, name)
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise FixtureError("native %s unavailable: %s" % (name, type(error).__name__))


def _number(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise FixtureError("%s must be a finite number" % label)
    return value


def _bool(obj, name):
    value = _get(obj, name)
    if type(value) not in (bool, int) or value not in (0, 1):
        raise FixtureError("native %s must be readable as a boolean" % name)
    return bool(value)


def _display(obj, name):
    value = _get(_get(obj, name), "display_name")
    if type(value) is not str:
        raise FixtureError("native %s display name unavailable" % name)
    return value


def _same(left, right):
    try:
        return left == right
    except (RuntimeError, ValueError) as error:
        raise FixtureError("runtime identity unavailable: %s" % type(error).__name__)


class AudioFixture:
    def __init__(self, song, manifest, q_bar, monitor_off, native_bar_clock=False,
                 native_quantizations=None):
        if type(native_bar_clock) is not bool:
            raise FixtureError("native bar clock opt-in must be an explicit boolean")
        self.native_bar_clock = native_bar_clock
        self.native_quantizations = dict(native_quantizations or {})
        self.song = song
        self.manifest = copy.deepcopy(manifest)
        self.q_bar, self.monitor_off = q_bar, monitor_off
        self.groups = {}
        self._layer_names = {}
        self._grid = self._grid_config()
        self._check_path()
        expected_hash = self.manifest.get("set_sha256")
        if (type(expected_hash) is not str or len(expected_hash) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in expected_hash)):
            raise FixtureError("initial saved Set SHA-256 is required")
        try:
            digest = hashlib.sha256()
            with open(self.manifest["set_path"], "rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as error:
            raise FixtureError("cannot verify initial saved Set: %s" % type(error).__name__)
        if digest.hexdigest() != expected_hash.lower():
            raise FixtureError("initial saved Set hash does not match manifest")
        try:
            self._tracks = tuple(_get(song, "tracks"))
        except (TypeError, RuntimeError):
            raise FixtureError("native track list unavailable")
        specs = self.manifest.get("groups")
        if not isinstance(specs, list) or not specs:
            raise FixtureError("explicit group bindings are required")
        used_group_names = set()
        for spec in specs:
            if not isinstance(spec, dict):
                raise FixtureError("group binding must be an object")
            index, name, names = spec.get("index"), spec.get("name"), spec.get("layer_names")
            if type(index) is not int or not 0 <= index < 8 or index in self.groups:
                raise FixtureError("group index must be unique and between zero and seven")
            if type(name) is not str or not name or name in used_group_names:
                raise FixtureError("group name must be unique and nonempty")
            if (not isinstance(names, list) or not names or any(type(n) is not str or not n for n in names)
                    or len(set(names)) != len(names)):
                raise FixtureError("group requires distinct explicit layer names")
            matches = [t for t in self._tracks if _get(t, "name") == name]
            if len(matches) != 1:
                raise FixtureError("no unique native group named %s" % name)
            group = matches[0]
            if not _bool(group, "is_foldable") or _get(group, "group_track") is not None:
                raise FixtureError("%s must be a top-level native Group Track" % name)
            layers = []
            for layer_name in names:
                candidates = [t for t in self._tracks if _get(t, "name") == layer_name
                              and _same(_get(t, "group_track"), group)]
                if len(candidates) != 1:
                    raise FixtureError("no unique direct audio layer %s in %s" % (layer_name, name))
                layers.append(candidates[0])
            self.groups[index] = GroupBinding(group, tuple(layers))
            self._layer_names[index] = tuple(names)
            used_group_names.add(name)
        self._check_bindings()

    def _check_path(self):
        expected = self.manifest.get("set_path")
        actual = _get(self.song, "file_path")
        if (type(expected) is not str or not os.path.isabs(expected) or not expected
                or type(actual) is not str or not actual
                or os.path.realpath(actual) != os.path.realpath(expected)):
            raise FixtureError("loaded saved Set path does not match manifest")

    def _grid_config(self):
        grid = self.manifest.get("bar_grid")
        if not isinstance(grid, dict) or grid.get("fixed") is not True:
            raise FixtureError("an explicitly verified fixed bar_grid is required")
        origin = _number(grid.get("origin"), "verified bar origin")
        numerator, denominator = grid.get("numerator"), grid.get("denominator")
        if (type(numerator) is not int or not 1 <= numerator <= 99
                or type(denominator) is not int or denominator not in (1, 2, 4, 8, 16)):
            raise FixtureError("verified fixed grid has an unsupported meter")
        return {"origin": origin, "numerator": numerator, "denominator": denominator}

    def _check_bindings(self):
        self._check_path()
        try:
            current = tuple(_get(self.song, "tracks"))
            if len(current) != len(self._tracks) or any(
                    not _same(a, b) for a, b in zip(current, self._tracks)):
                raise FixtureError("retained runtime track identities changed")
            for index, binding in self.groups.items():
                group = binding.group
                spec = next(s for s in self.manifest["groups"] if s["index"] == index)
                if (_get(group, "name") != spec["name"] or not _bool(group, "is_foldable")
                        or _get(group, "group_track") is not None or _bool(group, "is_frozen")):
                    raise FixtureError("bound group identity/type changed")
                for name, layer in zip(self._layer_names[index], binding.layers):
                    if (_get(layer, "name") != name or _bool(layer, "is_foldable")
                            or not _bool(layer, "has_audio_input") or _bool(layer, "has_midi_input")
                            or not _same(_get(layer, "group_track"), group) or _bool(layer, "is_frozen")):
                        raise FixtureError("bound layer identity/type/parent changed")
                    if _get(layer, "current_monitoring_state") != self.monitor_off:
                        raise FixtureError("capture layer monitoring must remain Off")
                    channel = self.manifest.get("capture_input_channel")
                    if type(channel) is not str or not channel or len(channel.split()) != 1:
                        raise FixtureError("explicit capture input channel token is required")
                    actual_channel = _display(layer, "input_routing_channel").split()
                    if (_display(layer, "input_routing_type") != "Ext. In"
                            or not actual_channel or actual_channel[0] != channel
                            or _display(layer, "output_routing_type") != _get(group, "name")):
                        raise FixtureError("capture routing must be exact external input to native parent group")
        except (TypeError, RuntimeError) as error:
            raise FixtureError("runtime binding unavailable: %s" % type(error).__name__)

    def _globals(self):
        state = {}
        for name in ("record_mode", "session_record", "overdub", "is_counting_in", "loop"):
            state[name] = _bool(self.song, name)
        if state["record_mode"]:
            raise FixtureError("Arrangement Record must remain Off")
        if state["is_counting_in"] or state["loop"]:
            raise FixtureError("count-in and Arrangement loop must remain Off")
        count_in = _get(self.song, "count_in_duration")
        if not isinstance(count_in, int) or isinstance(count_in, bool) or count_in != 0:
            raise FixtureError("count-in duration must be readable and None")
        state["count_in_duration"] = count_in
        if _get(self.song, "clip_trigger_quantization") != self.q_bar:
            raise FixtureError("native launch quantization must remain one bar")
        confirmations = self.manifest.get("sync_ui_confirmed_off", [])
        if (not isinstance(confirmations, list)
                or any(name not in ("link", "tempo_follower", "external_sync") for name in confirmations)):
            raise FixtureError("sync UI Off confirmations are invalid")
        for key, name in (("link", "is_ableton_link_enabled"),
                          ("tempo_follower", "tempo_follower_enabled"), ("external_sync", "external_sync")):
            try:
                state[name] = _bool(self.song, name)
            except FixtureError as error:
                if key not in confirmations:
                    raise FixtureError("native %s unavailable; explicit UI Off evidence required" % key)
                # UI evidence can cover an unavailable native property, never
                # a readable On value or an invalid readable native type.
                try:
                    getattr(self.song, name)
                except (AttributeError, RuntimeError, ValueError) as native_error:
                    state[name] = {"unavailable": type(native_error).__name__, "ui_confirmed_off": True}
                else:
                    raise error
            if state[name] is True:
                raise FixtureError("%s must remain Off" % key)
        return state

    def _raw_globals(self):
        result = {}
        for name in ("record_mode", "session_record", "overdub", "is_counting_in", "loop",
                     "count_in_duration", "clip_trigger_quantization", "is_ableton_link_enabled",
                     "tempo_follower_enabled", "external_sync"):
            try:
                value = _get(self.song, name)
                json.dumps(value, allow_nan=False)
                result[name] = value
            except FixtureError:
                result[name] = {"unavailable": True}
            except (TypeError, ValueError, OverflowError):
                result[name] = {"unavailable": "UnsupportedJSONValue"}
        return result

    def _meter(self):
        if self.native_bar_clock:
            try:
                grid = read_bar_grid(self.song, self._grid["origin"])
                return grid.numerator, grid.denominator
            except ValueError as error:
                raise FixtureError("native bar grid is unavailable: %s" % error) from error
        numerator = _get(self.song, "signature_numerator")
        denominator = _get(self.song, "signature_denominator")
        if (type(numerator) is not int or type(denominator) is not int
                or numerator != self._grid["numerator"] or denominator != self._grid["denominator"]):
            raise FixtureError("actual meter changed from the verified fixed bar grid")
        return numerator, denominator

    def _tempo(self):
        tempo = _number(_get(self.song, "tempo"), "actual tempo")
        if not 20 <= tempo <= 999:
            raise FixtureError("actual tempo is outside 20..999 BPM")
        return tempo

    @property
    def bar_length(self):
        numerator, denominator = self._meter()
        return 4.0 * numerator / denominator

    @property
    def bar_origin(self):
        self._meter()
        return self._grid["origin"]

    def validate(self, allowed_armed=(), require_transport=False):
        self._check_bindings()
        self._globals()
        self._meter()
        self._tempo()
        running = _bool(self.song, "is_playing")
        if require_transport and not running:
            raise FixtureError("Live transport must be running")
        try:
            allowed = tuple(allowed_armed)
        except TypeError:
            raise FixtureError("allowed_armed must contain owned native track objects")
        layers = tuple(t for binding in self.groups.values() for t in binding.layers)
        if any(not any(_same(track, owned) for owned in layers) for track in allowed):
            raise FixtureError("only owned audio layer tracks can be allowed armed")
        for track in self._tracks:
            if _bool(track, "can_be_armed"):
                if _bool(track, "implicit_arm"):
                    raise FixtureError("implicit arming must remain Off")
                if _bool(track, "arm") and not any(_same(track, permitted) for permitted in allowed):
                    raise FixtureError("unrelated or unpermitted armed track")
        return True

    def validate_control_scope(self):
        """Global editor scope without capture/transport/meter readiness gates."""
        self._check_bindings()
        return True

    def fingerprint(self):
        self._check_bindings()
        rows = []
        for index, binding in sorted(self.groups.items()):
            rows.append({"index": index, "group": _get(binding.group, "name"), "layers": [
                {"name": _get(layer, "name"), "input": _display(layer, "input_routing_type"),
                 "channel": _display(layer, "input_routing_channel"),
                 "output": _display(layer, "output_routing_type"),
                 "monitor": int(_get(layer, "current_monitoring_state"))}
                for layer in binding.layers]})
        static = {"set_path": os.path.realpath(self.manifest["set_path"]), "groups": rows,
                  "all_track_names": [_get(track, "name") for track in self._tracks], "bar_grid": self._grid}
        return hashlib.sha256(json.dumps(static, sort_keys=True).encode()).hexdigest()

    def report(self, allowed_armed=()):
        reasons = []
        try:
            self.validate(allowed_armed=allowed_armed)
        except FixtureError as error:
            reasons.append(str(error))
        result = {"ready": not reasons, "guard_reasons": reasons,
                  "initial_saved_hash_verified": True,
                  "set_path": self.manifest["set_path"],
                  "groups": [{"index": index, "name": next(spec["name"] for spec in self.manifest["groups"]
                                                            if spec["index"] == index),
                              "layer_names": list(self._layer_names[index])}
                             for index, binding in sorted(self.groups.items())],
                  "globals": self._raw_globals(),
                  "capabilities": {"prepared_audio_fixture": not reasons,
                                   "midi_capture": False, "gapless_chaining_proven": False}}
        try:
            result.update(fingerprint=self.fingerprint(), bar_length=self.bar_length,
                          bar_origin=self.bar_origin, tempo=self._tempo())
        except FixtureError as error:
            result["inspection_unavailable"] = str(error)
        return result

    def _clock_inputs(self):
        self._check_bindings()
        numerator, denominator = self._meter()
        return self._tempo(), numerator, denominator, _bool(self.song, "is_playing")

    @staticmethod
    def _next_boundary(origin, length, at, inclusive):
        origin = _number(origin, "clock anchor")
        length = _number(length, "clock length")
        at = _number(at, "target time")
        if length <= 0 or type(inclusive) is not bool:
            raise FixtureError("positive clock length and explicit inclusive boolean required")
        try:
            cycle = math.floor((at - origin) / length)
            boundary = origin + cycle * length
            if not inclusive or boundary != at:
                boundary += length
        except (OverflowError, ValueError):
            raise FixtureError("clock boundary cannot be represented")
        if not math.isfinite(boundary) or boundary < at or (not inclusive and boundary <= at):
            raise FixtureError("future clock boundary cannot be represented")
        return boundary

    def next_bar(self, at=None, inclusive=False):
        self._clock_inputs()
        if at is None:
            at = _get(self.song, "current_song_time")
        return self._next_boundary(self.bar_origin, self.bar_length, at, inclusive)

    def launch_window(self, target):
        """Native enqueue window for an immutable absolute quarter-note target."""
        self._clock_inputs()
        if not self.native_bar_clock:
            raise FixtureError("native launch-grid evidence is not enabled")
        try:
            return launch_window(target, self.bar_origin, self.bar_length,
                                 self.native_quantizations)
        except ValueError as error:
            raise FixtureError("native launch target is unsupported: %s" % error) from error

    def global_clock(self):
        tempo, numerator, denominator, running = self._clock_inputs()
        position = _number(_get(self.song, "current_song_time"), "actual song position")
        length = self.bar_length
        return Clock(ONE_BAR, 0, length, (position - self.bar_origin) % length,
                     0.0, length, tempo, numerator, denominator, running)

    @staticmethod
    def _reference(clip):
        if (not _bool(clip, "is_audio_clip") or not _bool(clip, "warping")
                or not _bool(clip, "looping") or not _bool(clip, "is_playing")):
            raise FixtureError("reference must be playing, looping, warped audio")
        start = _number(_get(clip, "loop_start"), "native loop start")
        end = _number(_get(clip, "loop_end"), "native loop end")
        anchor = _number(_get(clip, "start_time"), "native playback anchor")
        phase = _number(_get(clip, "playing_position"), "native reference position")
        if start != 0 or end <= start or not start <= phase < end:
            raise FixtureError("prototype reference needs zero loop start and a phase inside positive loop bounds")
        return anchor, end - start, phase - start

    def reference_clock(self, clip, take_id=0):
        tempo, numerator, denominator, running = self._clock_inputs()
        if type(take_id) is not int or not 0 <= take_id < 128:
            raise FixtureError("take ID must be between zero and 127")
        anchor, length, phase = self._reference(clip)
        now = _number(_get(self.song, "current_song_time"), "actual song position")
        try:
            cycle_anchor = anchor + math.floor((now - anchor) / length) * length
        except (OverflowError, ValueError):
            raise FixtureError("native reference cycle cannot be represented")
        first_bar = self._next_boundary(self.bar_origin, self.bar_length, cycle_anchor, True) - cycle_anchor
        return Clock(REFERENCE, take_id, length, phase, first_bar, self.bar_length,
                     tempo, numerator, denominator, running)

    def next_wrap(self, clip, at=None, inclusive=False):
        self._clock_inputs()
        anchor, length, _ = self._reference(clip)
        if at is None:
            at = _get(self.song, "current_song_time")
        return self._next_boundary(anchor, length, at, inclusive)
