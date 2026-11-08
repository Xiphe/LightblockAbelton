"""Normalize explicit scratch manifests without modifying them or Live.

Missing clock/input preparation can be derived only from the exact saved Set
and selected owned-track metadata. No 4/4, stereo-input or sync-state defaults
are supplied. The runtime fixture still owns initial saved-file hash checking
and native validation. This loader never grants recording capabilities.
"""

import copy
import gzip
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zlib


SYNC_KEYS = ("link", "tempo_follower", "external_sync")


class ConfigurationError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _read_json(path, description):
    try:
        with Path(path).open(encoding="utf-8") as source:
            value = json.load(source, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigurationError("cannot read %s: %s" % (description, type(error).__name__))
    if not isinstance(value, dict):
        raise ConfigurationError("%s must be a JSON object" % description)
    return value


def _names(value, description):
    if (not isinstance(value, list) or not value
            or any(type(item) is not str or not item for item in value)
            or len(set(value)) != len(value)):
        raise ConfigurationError("%s must be a nonempty list of distinct names" % description)
    return list(value)


def _groups(manifest):
    if "groups" in manifest:
        return copy.deepcopy(manifest["groups"]), "explicit groups"
    if "group_names" in manifest:
        names = _names(manifest["group_names"], "group_names")
        if len(names) > 8:
            raise ConfigurationError("controller manifest supports at most eight group indices")
        layers = _names(manifest.get("owned_layer_names"), "owned_layer_names")
        return [{"index": index, "name": name, "layer_names": list(layers)}
                for index, name in enumerate(names)], "flat group_names and owned_layer_names"
    if "owned_group" in manifest:
        name = manifest["owned_group"]
        if type(name) is not str or not name:
            raise ConfigurationError("owned_group must be an explicit nonempty name")
        layers = _names(manifest.get("owned_capture_tracks"), "owned_capture_tracks")
        return [{"index": 0, "name": name, "layer_names": layers}], "one-group proof manifest"
    raise ConfigurationError("manifest has no explicit group configuration")


def _saved_bar_grid(manifest):
    path = manifest.get("set_path")
    if type(path) is not str or not Path(path).is_absolute() or Path(path).suffix.lower() != ".als":
        raise ConfigurationError("an explicit absolute saved .als path is required for clock evidence")
    observed = manifest.get("transport_observed_ui")
    meter = observed.get("meter") if isinstance(observed, dict) else None
    if (not isinstance(meter, list) or len(meter) != 2
            or any(type(value) is not int for value in meter)):
        raise ConfigurationError("recorded transport meter is required to verify the saved clock")
    try:
        with gzip.open(path, "rb") as source:
            root = ET.parse(source).getroot()
    except (OSError, EOFError, ET.ParseError, zlib.error) as error:
        raise ConfigurationError("cannot inspect saved .als meter evidence: %s" % type(error).__name__)
    events = list(root.iter("RemoteableTimeSignature"))
    if len(events) != 1:
        raise ConfigurationError("saved Set must contain exactly one RemoteableTimeSignature event")
    event = events[0]
    try:
        numerator = int(event.find("Numerator").attrib["Value"])
        denominator = int(event.find("Denominator").attrib["Value"])
        at = float(event.find("Time").attrib["Value"])
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise ConfigurationError("saved time-signature event has missing or invalid fields")
    if (not 1 <= numerator <= 99 or denominator not in (1, 2, 4, 8, 16)
            or not math.isfinite(at) or at != 0.0):
        raise ConfigurationError("saved meter must be valid and have its only event at time zero")
    if [numerator, denominator] != meter:
        raise ConfigurationError("saved meter disagrees with the recorded transport meter")
    return {"origin": 0.0, "numerator": numerator, "denominator": denominator, "fixed": True,
            "provenance": {"source": "saved_als_single_time_signature", "set_path": path,
                           "event_count": 1, "event_time": at, "recorded_transport_meter": list(meter)}}


def _capture_channel(manifest, groups):
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list) or not isinstance(groups, list) or not groups:
        raise ConfigurationError("owned track metadata is required to derive capture input")
    targets = []
    for spec in groups:
        if not isinstance(spec, dict) or type(spec.get("name")) is not str:
            raise ConfigurationError("explicit group names are required to select owned audio tracks")
        layers = _names(spec.get("layer_names"), "layer_names")
        parents = [track for track in tracks if isinstance(track, dict)
                   and track.get("type") == "GroupTrack" and track.get("name") == spec["name"]]
        if len(parents) != 1 or parents[0].get("als_track_id") is None:
            raise ConfigurationError("no unique saved parent group for owned audio input")
        parent_id = str(parents[0]["als_track_id"])
        for name in layers:
            matches = [track for track in tracks if isinstance(track, dict)
                       and track.get("name") == name and track.get("type") == "AudioTrack"
                       and str(track.get("parent_als_group_id")) == parent_id]
            if len(matches) != 1:
                raise ConfigurationError("no unique saved direct audio layer %s in %s" % (name, spec["name"]))
            track = matches[0]
            target = track.get("input_target")
            if "input_target" not in track:
                legacy_input = track.get("input")
                target = legacy_input.get("target") if isinstance(legacy_input, dict) else None
            if type(target) is not str or re.fullmatch(r"AudioIn/External/M[0-9]+", target) is None:
                raise ConfigurationError("owned capture input must be a known mono external input target")
            targets.append(target)
    if not targets or len(set(targets)) != 1:
        raise ConfigurationError("all owned capture tracks must use the same mono external input")
    channel = str(int(targets[0].rsplit("/M", 1)[1]) + 1)
    return channel, {"source": "selected_owned_audio_track_inputs", "input_target": targets[0],
                     "owned_track_count": len(targets)}


def load_audio_configuration(manifest_path, sync_evidence_path=None):
    """Return normalized configuration. The input manifest is never written.

    Explicit groups, bar_grid and capture_input_channel remain explicit and
    receive runtime validation from AudioFixture. Derived clock evidence is
    accepted only from a single time-zero meter event matching the UI record.
    Optional sync evidence contributes only logical keys with literal False.
    """
    original = _read_json(manifest_path, "audio fixture manifest")
    result = copy.deepcopy(original)
    groups, group_source = _groups(original)
    result["groups"] = groups
    provenance = {"manifest_path": str(Path(manifest_path).resolve()), "groups": group_source}
    if "bar_grid" not in original:
        result["bar_grid"] = _saved_bar_grid(original)
        provenance["bar_grid"] = "saved .als single time-zero meter, matched against recorded transport"
    else:
        provenance["bar_grid"] = "explicit configuration"
    if "capture_input_channel" not in original:
        result["capture_input_channel"], provenance["capture_input_channel"] = _capture_channel(original, groups)
    else:
        provenance["capture_input_channel"] = "explicit configuration"
    confirmed = original.get("sync_ui_confirmed_off", [])
    if (not isinstance(confirmed, list)
            or any(type(key) is not str or key not in SYNC_KEYS for key in confirmed)):
        raise ConfigurationError("sync_ui_confirmed_off must contain recognized explicit logical keys")
    confirmed = set(confirmed)
    if sync_evidence_path is not None:
        evidence = _read_json(sync_evidence_path, "sync UI evidence")
        actual = evidence.get("global_ui_evidence")
        added = [key for key in SYNC_KEYS if isinstance(actual, dict) and actual.get(key) is False]
        confirmed.update(added)
        provenance["sync_ui_evidence"] = {"path": str(Path(sync_evidence_path).resolve()),
                                           "literal_false_keys": added}
    result["sync_ui_confirmed_off"] = sorted(confirmed)
    if "validated_features" not in original:
        result["validated_features"] = []
    result["normalization_provenance"] = provenance
    return result
