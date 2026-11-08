#!/usr/bin/env python3
"""Read scratch proof logs without interpreting callback arrival as audio time.

No Live imports, endpoint access, Set/audio reads or writes. JSON goes to stdout
unless an explicit --output path is supplied. All assessments are inconclusive:
this summary preserves evidence for review; it does not establish timing or
audio continuity from callbacks.
"""

import argparse
import json
import math
from pathlib import Path
import sys


TRACE_PREFIX = "Lightpad proof "
MAILBOX_PREFIX = "Lightpad proof mailbox "
STARTUP_PREFIX = "Audio proof loaded DISABLED"
REQUIRED_EVENTS = ("before.fire", "recording_observed", "before.finish", "finish_observed")
NATIVE_FIELDS = ("start_time", "end_time", "loop_start", "loop_end", "length", "file_path")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _json_loads(value):
    def reject_constant(value):
        raise ValueError("nonfinite JSON constant")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number")
        return number

    return json.loads(value, parse_constant=reject_constant, parse_float=finite_float)


def _key(event):
    for prefix in ("before.fire.", "before.finish."):
        if event.startswith(prefix):
            return prefix[:-1]
    return event


def _layer(event):
    parts = event.split(".")
    return parts[-1] if len(parts) >= 3 and parts[-1] in ("A", "B", "C") else None


def _new_segment(identifier, session=None, startup=None):
    return {"segment_id": identifier, "session": session, "startup": startup,
            "takes": [], "errors": [], "notices": []}


def _snapshot(line, outer, trace, layer):
    layers = trace.get("layers")
    match = None
    if isinstance(layers, list) and layer:
        candidates = [item for item in layers if isinstance(item, dict)
                      and item.get("name") in (layer, "Proof " + layer)]
        if len(candidates) == 1:
            match = candidates[0]
    song = trace.get("song") if isinstance(trace.get("song"), dict) else {}
    return {"line": line, "event": trace["event"],
            "arrival_monotonic": outer.get("monotonic"),
            "trace_monotonic": trace.get("monotonic"),
            "observed_song_beats": song.get("current_song_time"),
            "song": song, "details": trace.get("details", {}),
            "raw_layers": layers,
            "native_layer": match,
            "native_clip": match.get("clip") if match else None}


def summarize(lines):
    """Summarize an iterable of JSONL text lines; never open referenced files."""
    result = {"schema_version": 1, "assessment": "inconclusive",
        "limits": ["Callback timestamps are delivery observations, not native audio start/end times.",
                   "Requested target beats and observed song beats remain separate raw evidence.",
                   "Native clip properties retain their raw Live units; warped/unwarped units need verification.",
                   "Native timing accuracy, sample continuity and audible gaps are not assessed.",
                   "Unavailable or missing properties remain unsupported/inconclusive."],
        "counts": {"lines": 0, "trace_events": 0, "malformed_lines": 0, "ignored_messages": 0},
        "segments": [], "diagnostics": []}
    segments = {}
    takes = {}
    current = {}
    startup = 0
    default_segment = None

    def segment_for(outer, is_startup):
        nonlocal startup, default_segment
        session = outer.get("session")
        if isinstance(session, str) and session:
            identifier = "session:" + session
            default_segment = identifier
        elif is_startup:
            startup += 1
            identifier = default_segment = "startup:%d" % startup
        else:
            identifier = default_segment or "startup:0"
        if identifier not in segments:
            segments[identifier] = _new_segment(identifier, session=session,
                startup={"line": result["counts"]["lines"],
                         "arrival_monotonic": outer.get("monotonic")} if is_startup else None)
            result["segments"].append(segments[identifier])
            takes[identifier], current[identifier] = {}, None
        return identifier, segments[identifier]

    def malformed(line, reason):
        result["counts"]["malformed_lines"] += 1
        result["diagnostics"].append({"line": line, "reason": reason})

    for line_number, raw in enumerate(lines, 1):
        result["counts"]["lines"] = line_number
        try:
            outer = _json_loads(raw)
        except (ValueError, TypeError):
            malformed(line_number, "invalid outer JSON")
            continue
        if not isinstance(outer, dict) or not isinstance(outer.get("message"), str):
            malformed(line_number, "outer record requires a string message")
            continue
        message = outer["message"]
        identifier, segment = segment_for(outer, message.startswith(STARTUP_PREFIX))
        if not _number(outer.get("monotonic")):
            result["diagnostics"].append({"line": line_number,
                                         "reason": "missing/invalid outer monotonic timestamp"})
        if message.startswith(STARTUP_PREFIX):
            segment["notices"].append({"line": line_number, "message": message})
            continue
        if message.startswith(MAILBOX_PREFIX):
            try:
                mailbox = _json_loads(message[len(MAILBOX_PREFIX):])
            except ValueError:
                malformed(line_number, "invalid mailbox JSON")
                continue
            segment["errors"].append({"line": line_number, "source": "mailbox",
                "arrival_monotonic": outer.get("monotonic"), "record": mailbox})
            continue
        if not message.startswith(TRACE_PREFIX):
            if "error" in message.lower() or "unavailable" in message.lower():
                segment["errors"].append({"line": line_number, "source": "surface",
                    "arrival_monotonic": outer.get("monotonic"), "message": message})
            else:
                result["counts"]["ignored_messages"] += 1
            continue
        try:
            trace = _json_loads(message[len(TRACE_PREFIX):])
        except ValueError:
            malformed(line_number, "invalid trace JSON")
            continue
        if not isinstance(trace, dict) or not isinstance(trace.get("event"), str):
            malformed(line_number, "trace requires a string event")
            continue
        result["counts"]["trace_events"] += 1
        details = trace.get("details", {})
        if not isinstance(details, dict):
            malformed(line_number, "trace details must be an object")
            continue
        event = trace["event"]
        error = event.startswith("error.") or event == "fault"
        token = details.get("token")
        association = "explicit token"
        if type(token) is not int or token <= 0:
            token = None
            if error:
                candidate = current[identifier]
                if candidate is not None and (_layer(event) in (None, candidate["layer"])):
                    token = candidate["token"]
                    association = "current incomplete take; error had no token"
        if token is None:
            if error or event.endswith("unavailable"):
                segment["errors"].append({"line": line_number, "source": "trace",
                                          "association": "unassigned", "record": trace})
            elif _key(event) in REQUIRED_EVENTS:
                result["diagnostics"].append({"line": line_number,
                    "reason": "key event has no valid token", "event": event})
            continue
        take = takes[identifier].get(token)
        if take is None:
            take = {"token": token, "layer": _layer(event), "assessment": "inconclusive",
                    "requested": {"start_targets": [], "finish_targets": []},
                    "recording_observations": [], "finish_observations": [],
                    "events": [], "errors": []}
            takes[identifier][token] = take
            segment["takes"].append(take)
        if take["layer"] is None:
            take["layer"] = _layer(event)
        snapshot = _snapshot(line_number, outer, trace, take["layer"])
        take["events"].append(snapshot)
        if _key(event) == "before.fire":
            take["requested"]["start_targets"].append({"line": line_number,
                "target_beat": details.get("target_beat")})
            current[identifier] = take
        elif _key(event) == "before.finish":
            take["requested"]["finish_targets"].append({"line": line_number,
                "target_beat": details.get("target_beat")})
        elif event == "recording_observed":
            take["recording_observations"].append(snapshot)
        elif event == "finish_observed":
            take["finish_observations"].append(snapshot)
            if current[identifier] is take:
                current[identifier] = None
        elif event.startswith("before.arm."):
            current[identifier] = take
        if error:
            take["errors"].append({"association": association, "record": trace,
                                   "line": line_number})

    for segment in result["segments"]:
        for take in segment["takes"]:
            present = {_key(item["event"]) for item in take["events"]}
            take["missing_events"] = [name for name in REQUIRED_EVENTS if name not in present]
            take["required_events_present"] = not take["missing_events"]
            missing_native = []
            for kind in ("recording_observations", "finish_observations"):
                for index, observation in enumerate(take[kind]):
                    clip = observation["native_clip"]
                    for field in NATIVE_FIELDS:
                        value = clip.get(field) if isinstance(clip, dict) else None
                        if value is None or value == "" or isinstance(value, dict):
                            missing_native.append("%s[%d].%s" % (kind, index, field))
            take["unsupported_or_missing_native"] = missing_native
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.resolve() == args.events.resolve():
            raise ValueError("output must not replace the input evidence file")
        with args.events.open(encoding="utf-8") as source:
            summary = summarize(source)
        summary["events_path"] = str(args.events.resolve())
        encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            args.output.write_text(encoded, encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, "Unable to summarize proof events: %s\n" % error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
