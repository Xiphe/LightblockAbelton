"""Pure physical-sample coverage for normalized fresh-recording warp markers.

This helper is inactive until a native adapter verifies its marker format and
ownership. It reads no files or native objects and never changes loop bounds.
Marker pairs are (sample_seconds, beat_time). The last segment continues beyond
the final hidden marker, retaining its recorded mapping rather than Song tempo.
"""

import math


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def sample_covers(target_beats, sample_length, sample_rate, markers):
    """Return whether actual sample data covers this positive clip-beat target.

    Malformed or numerically unusable observations raise ValueError. Coverage
    has at most one physical sample of endpoint tolerance, including recordings
    whose existing loop end was rounded down independently of the file length.
    No current-tempo conversion or marker reordering is permitted.
    """
    if (not _finite(target_beats) or target_beats <= 0
            or not isinstance(sample_length, int) or isinstance(sample_length, bool)
            or sample_length <= 0 or not _finite(sample_rate) or sample_rate <= 0):
        raise ValueError("positive finite target/rate and an integer sample length are required")
    if not isinstance(markers, (tuple, list)) or len(markers) < 2:
        raise ValueError("at least two normalized warp markers are required")
    points = []
    for marker in markers:
        if (not isinstance(marker, (tuple, list)) or len(marker) != 2
                or not all(_finite(value) for value in marker)):
            raise ValueError("warp markers must be finite (sample_seconds, beat_time) pairs")
        seconds, beats = marker
        if points and (seconds <= points[-1][0] or beats <= points[-1][1]):
            raise ValueError("warp markers must strictly increase in both units")
        points.append((seconds, beats))
    if points[0] != (0, 0):
        raise ValueError("a fresh recording must have an actual zero sample/beat origin")
    left, right = points[-2:]
    for before, after in zip(points, points[1:]):
        if target_beats <= after[1]:
            left, right = before, after
            break
    try:
        slope = (right[0] - left[0]) / (right[1] - left[1])
        mapped_seconds = left[0] + (target_beats - left[1]) * slope
        covered_endpoint = (sample_length + 1) / sample_rate
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError("warp mapping or sample endpoint is unrepresentable") from error
    if (not _finite(slope) or slope <= 0 or not _finite(mapped_seconds)
            or not _finite(covered_endpoint)):
        raise ValueError("warp mapping or sample endpoint is unrepresentable")
    return mapped_seconds <= covered_endpoint
