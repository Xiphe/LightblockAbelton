"""Corroborate native BeatTime against an explicitly known fixed bar origin.

Experiment 10 verified one-based bars/beats/sixteenths/ticks, with 60 ticks
per sixteenth. This reader accepts that observed scratch-Set grid only when
the native fields agree with actual quarter-note song position. It does not
reconstruct Arrangement meter history or infer a replacement origin.
"""

from collections import namedtuple
import math


BarGrid = namedtuple("BarGrid", "position origin length phase numerator denominator")
TICK = 1.0 / 240.0
EPSILON = 1e-9
DENOMINATORS = (1, 2, 4, 8, 16)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _position_and_meter(song):
    position = song.current_song_time
    numerator, denominator = song.signature_numerator, song.signature_denominator
    if (not _finite(position) or type(numerator) is not int or not 1 <= numerator <= 99
            or type(denominator) is not int or denominator not in DENOMINATORS):
        raise ValueError("native song position or meter is unavailable")
    return position, numerator, denominator


def read_bar_grid(song, origin=0.0):
    """Return a corroborated BarGrid or raise ValueError without native writes.

    `origin` must come from independently established fixed-grid provenance.
    BeatTime's quantized phase validates the grid; the returned phase retains
    actual song-position precision. Before/after reads must be unchanged.
    """
    if not _finite(origin):
        raise ValueError("an explicit finite fixed origin is required")
    try:
        before = _position_and_meter(song)
        native = song.get_current_beats_song_time()
        bars, beats, subdivision, ticks = (
            native.bars, native.beats, native.sub_division, native.ticks)
        after = _position_and_meter(song)
        if before != after:
            raise ValueError("native position or meter changed during observation")
        position, numerator, denominator = before
        if (not all(type(value) is int for value in (bars, beats, subdivision, ticks))
                or bars < 1 or not 1 <= beats <= numerator
                or not 1 <= subdivision <= 16 // denominator or not 1 <= ticks <= 60):
            raise ValueError("native BeatTime fields are outside the observed bounds")
        length = numerator * 4.0 / denominator
        bar_origin = origin + (bars - 1) * length
        native_phase = ((beats - 1) * 4.0 / denominator
                        + (subdivision - 1) / 4.0 + (ticks - 1) / 240.0)
        lower = bar_origin + native_phase
        residual = position - lower
        phase = position - bar_origin
        if (not all(_finite(value) for value in (bar_origin, lower, residual, phase))
                or residual < -EPSILON or residual >= TICK + EPSILON
                or not 0 <= phase < length):
            raise ValueError("native BeatTime does not corroborate the declared fixed grid")
        return BarGrid(position, origin, length, phase, numerator, denominator)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("native bar grid is unavailable") from error
