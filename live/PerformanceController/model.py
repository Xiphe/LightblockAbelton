"""Read current Live state without file manifests or retained musical ownership.

Group identifiers are name prefixes, not track positions. A view is a bounded
read transaction; discard it after any callback or mutation. Only an in-flight
recording may retain a copy of a loop's timing.
"""
from dataclasses import dataclass
from functools import cached_property
import math
import re

from .timing import Clock, ONE_BAR, REFERENCE

MARKER = re.compile(r'^\[R([1-8])\]')
DENOMINATORS = (1, 2, 4, 8, 16)
TICK = 1.0 / 240.0


def same(a, b):
    try:
        return a is b or a == b
    except Exception:
        return False


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Grid:
    position: float
    origin: float
    length: float
    phase: float
    numerator: int
    denominator: int
    tempo: float
    running: bool

    def next_bar(self, at=None, inclusive=False):
        at = self.position if at is None else at
        if not number(at):
            raise ValueError('finite song position required')
        distance = (at - self.origin) / self.length
        n = math.ceil(distance - 1e-9) if inclusive else math.floor(distance + 1e-9) + 1
        return self.origin + n * self.length

    def clock(self):
        return Clock(ONE_BAR, 0, self.length, self.phase, 0, self.length,
                     self.tempo, self.numerator, self.denominator, self.running)


def read_grid(song):
    """Read this bar from native BeatTime, including current meter changes.

    BeatTime has 240 ticks per quarter note. The fine song-position residual
    refines phase within its reported tick. No saved origin, global modulo-bar
    fallback, or default meter is used. Native mutation observations remain
    authoritative for actual recording start and finish.
    """
    before = (song.current_song_time, song.signature_numerator,
              song.signature_denominator, song.tempo, song.is_playing)
    native = song.get_current_beats_song_time()
    after = (song.current_song_time, song.signature_numerator,
             song.signature_denominator, song.tempo, song.is_playing)
    if before != after:
        raise ValueError('Live clock changed during observation')
    position, numerator, denominator, tempo, running = after
    if (not number(position) or not number(tempo) or not 20 <= tempo <= 999
            or type(numerator) is not int or not 1 <= numerator <= 99
            or denominator not in DENOMINATORS or type(running) not in (bool, int)
            or running not in (0, 1)):
        raise ValueError('Live clock unavailable')
    bars, beats, subdivision, ticks = native.bars, native.beats, native.sub_division, native.ticks
    if (not all(type(v) is int for v in (bars, beats, subdivision, ticks)) or bars < 1
            or not 1 <= beats <= numerator or not 1 <= subdivision <= 16 // denominator
            or not 1 <= ticks <= 60):
        raise ValueError('Live BeatTime unavailable')
    coarse_phase = ((beats - 1) * 4.0 / denominator
                    + (subdivision - 1) / 4.0 + (ticks - 1) / 240.0)
    # Meter events and Live's exposed tick positions lie on its tick grid.
    origin = math.floor((position - coarse_phase) / TICK + 1e-7) * TICK
    phase = position - origin
    length = numerator * 4.0 / denominator
    if not -1e-7 <= phase < length or not coarse_phase - 1e-7 <= phase < coarse_phase + TICK + 1e-7:
        raise ValueError('Live BeatTime and song position disagree')
    return Grid(float(position), origin, length, max(0.0, phase), numerator,
                denominator, float(tempo), bool(running))


@dataclass(frozen=True)
class ClipState:
    track: object
    slot: object
    row: int
    clip: object


@dataclass(frozen=True)
class LoopReference:
    clip: object
    length: float
    phase: float
    anchor: float

    def next_wrap(self, at, inclusive=False):
        delta = (at - self.anchor) / self.length
        n = math.ceil(delta - 1e-9) if inclusive else math.floor(delta + 1e-9) + 1
        return self.anchor + n * self.length

    def clock(self, grid, take_id=0):
        first = grid.next_bar(at=self.anchor, inclusive=True) - self.anchor
        return Clock(REFERENCE, take_id, self.length, self.phase, first, grid.length,
                     grid.tempo, grid.numerator, grid.denominator, grid.running)


@dataclass(frozen=True)
class GroupState:
    index: int
    group: object
    tracks: tuple
    playing: tuple
    recording: tuple
    @cached_property
    def occupied_rows(self):
        # Phrase-grid occupancy is needed only for allocation/advance, not
        # animation heartbeats. Reading it lazily keeps steady-state work
        # proportional to tracks and active clips, not tracks × scenes.
        return frozenset(row for track in self.tracks
                         for row, slot in enumerate(track.clip_slots) if slot.has_clip)

    def next_free_row(self, start):
        while start in self.occupied_rows:
            start += 1
        return start

    @property
    def fresh_row(self):
        start = (max(c.row for c in self.playing) + 1 if self.playing else
                 max(self.occupied_rows, default=-1) + 1)
        return self.next_free_row(start)
