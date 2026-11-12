"""Optional actual-clock encoder. No Live binding, defaults or interpolation.

Clock beat fields are actual supplied quarter-note beat values. The caller owns
the reference/queue anchor and supplies first_bar as the first actual bar
boundary at or after that anchor. Zero means a real boundary at the anchor.
Meter/anchor changes require invalidation or a newly observed rebase upstream.
ONE_BAR describes the actual global bar grid for an available empty group's
overview progress or a first take before a reference exists. It does not make
an unavailable group available.

Wire fields use nearest 1/64-beat ticks, low seven bits first. A value which
cannot be represented is rejected, never clamped or wrapped. A loop position
rounding onto its endpoint is rejected as well. None explicitly invalidates
timing; it does not assume a meter or fabricate a clock.
"""

from collections import namedtuple
import math


INVALID = 0
ONE_BAR = 1
REFERENCE = 2
COUNTDOWN = 3
RUNNING = 4
METRONOME = 8
TICKS_PER_BEAT = 64
TEMPO_TICKS_PER_BPM = 16
MAX_TICKS = (1 << 21) - 1
MAX_TEMPO_TICKS = (1 << 14) - 1

Clock = namedtuple("Clock", "context take_id span position first_bar bar_length tempo numerator denominator running")


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _ticks(value, scale, maximum, positive=False):
    if not _finite(value) or value < 0 or value > maximum / scale:
        raise ValueError("clock value is nonfinite, negative or out of range")
    result = int(round(value * scale))
    if positive and result <= 0:
        raise ValueError("positive clock value rounds to zero")
    return result


def _validated(clock, allow_countdown_page=False):
    if not isinstance(clock, Clock):
        raise ValueError("actual Clock required")
    if not _integer(clock.context, ONE_BAR, COUNTDOWN):
        raise ValueError("invalid timing context")
    if not _integer(clock.take_id, 0, 127) or type(clock.running) is not bool:
        raise ValueError("invalid take ID or running state")
    if (not _integer(clock.numerator, 1, 127)
            or not _integer(clock.denominator, 1, 127)):
        raise ValueError("meter does not fit the timing frame")
    span = _ticks(clock.span, TICKS_PER_BEAT, MAX_TICKS, positive=True)
    position = _ticks(clock.position, TICKS_PER_BEAT, MAX_TICKS)
    first_bar = _ticks(clock.first_bar, TICKS_PER_BEAT, MAX_TICKS)
    bar_length = _ticks(clock.bar_length, TICKS_PER_BEAT, MAX_TICKS, positive=True)
    tempo = _ticks(clock.tempo, TEMPO_TICKS_PER_BPM, MAX_TEMPO_TICKS, positive=True)
    tolerance = 1.0 / TICKS_PER_BEAT
    if abs(clock.bar_length - 4.0 * clock.numerator / clock.denominator) > tolerance:
        raise ValueError("bar length disagrees with actual meter")
    if clock.first_bar > clock.bar_length or first_bar > bar_length:
        raise ValueError("first actual bar boundary is outside one bar")
    if clock.position > clock.span or position > span:
        raise ValueError("position exceeds timing span")
    if clock.context in (ONE_BAR, REFERENCE):
        if clock.position >= clock.span or position >= span:
            raise ValueError("loop position must precede its endpoint")
        page = (allow_countdown_page and clock.first_bar == 0
                and clock.span == 8 * clock.bar_length)
        if clock.context == ONE_BAR and abs(clock.span - clock.bar_length) > tolerance and not page:
            raise ValueError("one-bar context requires an actual one-bar span")
    else:
        bars = int(round((clock.span - clock.first_bar) / clock.bar_length))
        if (bars < 0
                or abs(clock.first_bar + bars * clock.bar_length - clock.span) > tolerance
                or abs(first_bar + bars * bar_length - span) > 1):
            raise ValueError("countdown target is outside the actual bar grid")
    return Clock(clock.context, clock.take_id, span, position, first_bar,
                 bar_length, tempo, clock.numerator, clock.denominator, clock.running)


def _digits(value, count):
    return [(value >> (7 * digit)) & 127 for digit in range(count)]


def _encode_timing(group, revision, clock, allow_countdown_page=False):
    """Encode one frame; display-page permission is never a global default."""
    if not _integer(group, 0, 7) or not _integer(revision, 0, 127):
        raise ValueError("invalid timing group or revision")
    fields = [0] * 18
    if clock is not None:
        encoded = _validated(clock, allow_countdown_page)
        fields = [encoded.context | (RUNNING if encoded.running else 0), encoded.take_id]
        for value in (encoded.span, encoded.position, encoded.first_bar, encoded.bar_length):
            fields.extend(_digits(value, 3))
        fields.extend(_digits(encoded.tempo, 2))
        fields.extend([encoded.numerator, encoded.denominator])
    values = [revision] + fields + [revision]
    return [(0xB0 + group, 70 + index, value) for index, value in enumerate(values)]


def encode_timing(group, revision, clock):
    """Return twenty CC messages with strict ordinary clock validation."""
    return _encode_timing(group, revision, clock)


def encode_view_timing(group, revision, clock, countdown_page=False):
    """Ring clock for queued AND recording views, preserving actual phase.

    View state selects red versus group color. The selected queued view may
    explicitly authorize an exact eight-bar page; ordinary/global clocks stay
    strict, and actual bar length, meter and tempo retain their real units.
    """
    if type(countdown_page) is not bool:
        raise ValueError("countdown page permission must be explicit boolean")
    if clock is not None and (not isinstance(clock, Clock) or clock.context not in (ONE_BAR, REFERENCE)):
        raise ValueError("recording view requires the actual one-bar or reference clock")
    return _encode_timing(group, revision, clock, allow_countdown_page=countdown_page)


def encode_global_timing(revision, clock, metronome=False):
    """Encode the actual one-bar clock on channel 16, or explicit invalidation.

    Global timing has no take owner, so an actual ONE_BAR clock with take_id 0
    is required. There is no default meter or conversion from a group reference.
    Actual metronome state occupies mask 8 only in a valid global frame,
    independently of transport running. Invalidation clears the entire payload.
    """
    if type(metronome) is not bool:
        raise ValueError("metronome must be a boolean observed from Live")
    if clock is not None and (not isinstance(clock, Clock)
                              or clock.context != ONE_BAR or clock.take_id != 0):
        raise ValueError("global timing requires ONE_BAR context and take ID zero")
    messages = [(0xBF, controller, value)
                for _, controller, value in encode_timing(0, revision, clock)]
    if clock is not None and metronome:
        messages[1] = (0xBF, 71, messages[1][2] | METRONOME)
    return messages


def marker_pixels(clock, pixels=56):
    """Locate real bar boundaries in O(pixels), even for very long spans.

    Returned coordinates are sorted and unique. A countdown endpoint occupies
    the final pixel. Loop endpoints are excluded, avoiding a duplicate of zero.
    Each pixel bucket is tested directly, without iterating all elapsed bars.
    """
    if type(pixels) is not int or pixels <= 0:
        raise ValueError("positive integer pixel count required")
    if clock is None:
        return []
    encoded = _validated(clock)
    span, first, bar = encoded.span, encoded.first_bar, encoded.bar_length
    result = []
    for pixel in range(pixels):
        # Integer bucket arithmetic keeps exact boundary ownership independent
        # of floating-point division. ceil(a/b) = -floor(-a/b).
        lower, upper = pixel * span, (pixel + 1) * span
        step = bar * pixels
        first_scaled = first * pixels
        bar_index = max(0, -((first_scaled - lower) // step))
        boundary = first + bar_index * bar
        if boundary < span and lower <= boundary * pixels < upper:
            result.append(pixel)
    if encoded.context == COUNTDOWN and (not result or result[-1] != pixels - 1):
        result.append(pixels - 1)
    return result
