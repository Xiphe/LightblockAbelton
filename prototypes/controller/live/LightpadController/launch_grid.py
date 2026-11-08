"""Choose a supplied proven launch grid without moving an absolute target.

Bar quantization follows its explicitly verified origin and length. The note
grids were observed against absolute quarter-note zero only, so they cannot be
used with a foreign bar origin. A supplied enum is evidence from the caller;
this module never enables additional quantizations or falls back to a later bar.
"""

from collections import namedtuple
from collections.abc import Mapping
import math


LaunchWindow = namedtuple("LaunchWindow", "previous target quantization")
TOLERANCE = 1e-7
# Native enum values observed in experiment 10. q_half remains unavailable
# unless the caller explicitly includes its separately validated handle.
ENUM_VALUES = {"q_bar": 4, "q_half": 5, "q_quarter": 7, "q_eight": 9, "q_sixtenth": 11}
NOTE_PERIODS = (("q_half", 2.0), ("q_quarter", 1.0), ("q_eight", .5), ("q_sixtenth", .25))


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _validated_quantizations(values):
    if not isinstance(values, Mapping) or not values:
        raise ValueError("an explicit nonempty proven quantization mapping is required")
    for name, handle in values.items():
        if name not in ENUM_VALUES or isinstance(handle, (bool, float, str, bytes, bytearray)):
            raise ValueError("unsupported quantization name or native enum")
        try:
            native_value = int(handle)
        except Exception as error:
            raise ValueError("native quantization enum is unavailable") from error
        if native_value != ENUM_VALUES[name]:
            raise ValueError("native quantization enum does not match its proven name")
    return values


def launch_window(target, bar_origin, bar_length, quantizations):
    """Return the coarsest supplied LaunchWindow containing this exact target.

    Native enum handles are validated by their observed integer value and
    returned unchanged. Eligibility/missed-window checks belong to the backend;
    this pure function neither reads a clock nor requests native operations.
    """
    if (not all(_finite(value) for value in (target, bar_origin, bar_length))
            or bar_length <= 0):
        raise ValueError("finite target/origin and positive bar length are required")
    quantizations = _validated_quantizations(quantizations)
    candidates = [("q_bar", bar_length, bar_origin)]
    if bar_origin == 0:
        candidates.extend((name, period, 0.0) for name, period in NOTE_PERIODS)
    matches = []
    for name, period, origin in candidates:
        if name not in quantizations:
            continue
        delta = target - origin
        if not _finite(delta):
            raise ValueError("target distance from the proven origin is unrepresentable")
        if abs(math.remainder(delta, period)) <= TOLERANCE:
            matches.append((period, quantizations[name]))
    if not matches:
        raise ValueError("target is outside every supplied proven launch grid")
    period, handle = max(matches, key=lambda match: match[0])
    previous = target - period
    if not _finite(previous) or previous >= target:
        raise ValueError("the previous launch boundary is unrepresentable")
    return LaunchWindow(previous, target, handle)
