"""Conservative time accounting for one native enqueue transaction.

This helper does not choose targets, quantization, or admission margins. Live's
reported song position can stay unchanged while Python performs validation.
Charge that work against one immutable clock sample, including time spent in
native getters. A caller must resolve quantization and finish its native guards
before require(), then perform the intended write without further native reads.

Elapsed time cannot establish the age of the initial public clock sample or
make separate native calls atomic. Operational margins still need native proof.
"""

import math
import time


def _number(value, label):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("finite %s required" % label)
    return float(value)


def _tempo(song):
    value = _number(song.tempo, "tempo")
    if not 20 <= value <= 999:
        raise ValueError("tempo outside 20..999 BPM")
    return value


class LaunchAdmission:
    def __init__(self, song, clock=None):
        self._song = song
        self._clock = time.monotonic if clock is None else clock
        # Stamp first: the getters themselves may consume the remaining lead.
        self._started = _number(self._clock(), "monotonic time")
        self._beat = _number(song.current_song_time, "song position")
        self._max_tempo = _tempo(song)

    def position(self, lead_seconds=0):
        """Conservative position including requested headroom, for pure planning."""
        lead_seconds = _number(lead_seconds, "enqueue lead")
        if lead_seconds < 0:
            raise ValueError("nonnegative enqueue lead required")
        beat = _number(self._song.current_song_time, "song position")
        tempo = max(self._max_tempo, _tempo(self._song))
        elapsed = _number(self._clock(), "monotonic time") - self._started
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("monotonic clock moved backwards")
        self._max_tempo = tempo
        conservative_beat = max(beat, self._beat + elapsed * tempo / 60.0)
        required_beats = lead_seconds * tempo / 60.0
        position = conservative_beat + required_beats
        if not math.isfinite(position):
            raise ValueError("enqueue position is unrepresentable")
        return position

    def require(self, target, lead_seconds):
        target = _number(target, "target")
        if target <= self.position(lead_seconds):
            raise ValueError("insufficient time before native enqueue boundary")
