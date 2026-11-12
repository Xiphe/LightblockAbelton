"""Read raw musical-position evidence without deriving a bar clock.

Reads are sequential, not an atomic transport snapshot. Before/after song and
monotonic readings retain that uncertainty. This module imports no Live code,
writes no properties, and assumes no BeatTime field units or tick resolution.
"""

import json
import time


SONG_FIELDS = ("current_song_time", "tempo", "signature_numerator", "signature_denominator")
BEAT_FIELDS = ("bars", "beats", "sub_division", "ticks")


def _type_name(value):
    cls = type(value)
    return cls.__module__ + "." + cls.__qualname__


def _json_value(value):
    try:
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError, OverflowError):
        return {"unavailable": "UnsupportedJSONValue", "type": _type_name(value)}


def _observe(call):
    try:
        return _json_value(call())
    except Exception as error:
        # Native objects can disappear between reads. Preserve every other
        # observation rather than replacing unavailable values with defaults.
        return {"unavailable": type(error).__name__}


def _properties(obj, names):
    return {name: _observe(lambda name=name: getattr(obj, name)) for name in names}


def observe_bar_time(song, clock=time.monotonic):
    """Return JSON-safe raw observations; never infer phase or a bar boundary.

    ``before`` and ``after`` contain the actual tempo and meter on each side of
    the native call. ``native_beat_time`` preserves its type, text and individual
    fields. A missing method, lost object or unreadable field is explicit.
    Numeric song time uses quarter notes. The raw BeatTime units are unverified.
    """
    result = {"schema_version": 1,
              "units": {"current_song_time": "quarter-notes",
                        "native_beat_time": "unverified raw Python BeatTime fields"},
              "monotonic_before": _observe(clock),
              "before": _properties(song, SONG_FIELDS)}
    try:
        native = song.get_current_beats_song_time()
    except Exception as error:
        unavailable = {"unavailable": type(error).__name__}
        result["native_beat_time"] = {name: dict(unavailable)
            for name in ("type", "text") + BEAT_FIELDS}
        result["native_beat_time"].update(unavailable)
    else:
        result["native_beat_time"] = {
            "type": _observe(lambda: _type_name(native)),
            "text": _observe(lambda: str(native)),
            **_properties(native, BEAT_FIELDS)}
    result["after"] = _properties(song, SONG_FIELDS)
    result["monotonic_after"] = _observe(clock)
    return result
