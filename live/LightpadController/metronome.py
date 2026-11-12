"""Guarded native meter/metronome controls, without transport or clip writes."""

from collections import deque
import math
import time

from . import protocol as p


NATIVE_ERRORS = (RuntimeError, AttributeError, TypeError, ValueError, OverflowError)
DENOMINATORS = (1, 2, 4, 8, 16)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class MetronomeControls:
    def __init__(self, song, can_edit, now=None):
        self.song, self.can_edit = song, can_edit
        self.now = time.monotonic if now is None else now
        self._denominator = None
        self._last_tap = None
        self._intervals = deque(maxlen=4)

    def _reset_taps(self, first=None):
        self._last_tap = first
        self._intervals.clear()

    def meter(self):
        """Read an actual supported native meter, or None when unavailable."""
        try:
            numerator = self.song.signature_numerator
            denominator = self.song.signature_denominator
            if (type(numerator) is not int or not 1 <= numerator <= 99
                    or type(denominator) is not int or denominator not in DENOMINATORS):
                self._reset_taps()
                return None
            if denominator != self._denominator:
                self._reset_taps()
                self._denominator = denominator
            return numerator, denominator
        except NATIVE_ERRORS:
            self._reset_taps()
            return None

    def metronome(self):
        """Read actual native metronome state without guessing a default."""
        try:
            value = self.song.metronome
            return value if type(value) is bool else None
        except NATIVE_ERRORS:
            return None

    def _tap(self):
        meter = self.meter()
        if meter is None:
            return p.NOT_READY
        try:
            tempo = self.song.tempo
            instant = self.now()
        except NATIVE_ERRORS:
            return p.NOT_READY
        if not _finite(tempo) or not 20 <= tempo <= 999:
            return p.NOT_READY
        if not _finite(instant):
            return p.REJECTED
        if self._last_tap is None:
            self._reset_taps(instant)
            return p.APPLIED
        interval = instant - self._last_tap
        if not _finite(interval) or interval <= 0:
            return p.REJECTED
        denominator = meter[1]
        selected_beat = 240.0 / (denominator * tempo)
        if interval > max(2.0, 3.0 * selected_beat):
            self._reset_taps(instant)
            return p.APPLIED
        candidate = 240.0 / (denominator * interval)
        if candidate > 999:  # Ignore a bounce without moving the last real tap.
            return p.REJECTED
        if candidate < 20:
            self._reset_taps(instant)
            return p.REJECTED
        intervals = (list(self._intervals) + [interval])[-4:]
        averaged = 240.0 / (denominator * (sum(intervals) / len(intervals)))
        if not _finite(averaged) or not 20 <= averaged <= 999:
            self._reset_taps(instant)
            return p.REJECTED
        try:
            # Native Song.tap_tempo can start transport under a user preference.
            # Set only its quarter-note BPM, derived from selected-beat taps.
            self.song.tempo = averaged
            actual = self.song.tempo
            if not _finite(actual) or not 20 <= actual <= 999:
                self._reset_taps()
                return p.NOT_READY
        except NATIVE_ERRORS:
            self._reset_taps()
            return p.NOT_READY
        self._intervals.append(interval)
        self._last_tap = instant
        return p.APPLIED

    def dispatch(self, command):
        group, opcode, argument = command.group, command.op, command.arg
        if (type(group) is not int or group != 0 or type(opcode) is not int
                or type(argument) is not int):
            return p.BAD_COMMAND
        valid = {
            p.TAP_TEMPO: argument == 0,
            p.SET_NUMERATOR: 1 <= argument <= 99,
            p.SET_DENOMINATOR: argument in DENOMINATORS,
            p.SET_METRONOME: argument in (0, 127),
        }
        if not valid.get(opcode, False):
            return p.BAD_COMMAND
        try:
            if not self.can_edit():
                return p.NOT_READY
        except Exception:
            return p.NOT_READY
        if opcode == p.TAP_TEMPO:
            return self._tap()
        if opcode == p.SET_METRONOME:
            name, value, readback = "metronome", argument == 127, self.metronome
        else:
            name = "signature_numerator" if opcode == p.SET_NUMERATOR else "signature_denominator"
            value, readback = argument, self.meter
        if readback() is None:
            return p.NOT_READY
        try:
            setattr(self.song, name, value)
            return p.APPLIED if readback() is not None else p.NOT_READY
        except NATIVE_ERRORS:
            return p.NOT_READY
