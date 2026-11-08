"""MIDI CC framing for controller protocol v4, without a Live dependency.

Only Command events may reach the mutation layer. Call complete() once that
layer has chosen an outcome. Replay events contain the cached outcome and must
never execute the command again. Malformed frames are discarded as a unit.
"""

from collections import namedtuple


VERSION = 18
GROUP_COUNT = 8
META_STATUS = 0xBF
RECORD_PREVIEW = 4
ADVANCE_PREVIEW = 8
HELLO_CC = 110

REFRESH, SET_MUTE, BEGIN, ACCEPT, ACCEPT_NEXT, RETRY, ABORT, ADVANCE = range(8)
SET_VOLUME, SET_SEND_1, SET_SEND_2, SET_SEND_3, SET_PAN, TAP_TEMPO = range(8, 14)
ACK_COMPLETION = 14
SET_CONTROL_4 = 15
SET_NUMERATOR, SET_DENOMINATOR, SET_METRONOME = range(16, 19)

UNAVAILABLE, EMPTY, PLAYING, QUEUED, RECORDING, FINISH_QUEUED = range(6)
NONE, APPLIED, OUTCOME_QUEUED, REJECTED, CAPACITY, NOT_READY, BAD_COMMAND, BUSY, STALE = range(9)

CAP_RECORDING = 1
CAP_REFERENCE_WRAP = 2
CAP_CHAINING = 4
CAP_ADVANCE = 8
CAP_MIXER = 16

# Stable internal field keys, not physical controller numbers. Snapshot
# encoding maps keys 91..108 to the safe wire bank CC41..58.
STATE_FIELDS = {
    "state": 91, "mute": 92, "phase": 93, "countdown": 94,
    "red": 95, "green": 96, "blue": 97, "volume": 98,
    "send_1": 99, "send_2": 100, "send_3": 101, "pan": 102,
    "capabilities": 103, "sequence": 104, "outcome": 105, "recording_view": 106,
    "take_id": 107, "control_4": 108,
}

Command = namedtuple("Command", "seq group op arg")
Replay = namedtuple("Replay", "command outcome")
Rejected = namedtuple("Rejected", "group seq outcome")


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _valid_command(command):
    if not _integer(command.op, REFRESH, SET_METRONOME):
        return False
    if command.op == SET_NUMERATOR:
        return command.group == 0 and 1 <= command.arg <= 99
    if command.op == SET_DENOMINATOR:
        return command.group == 0 and command.arg in (1, 2, 4, 8, 16)
    if command.op == SET_METRONOME:
        return command.group == 0 and command.arg in (0, 127)
    if command.op == ACK_COMPLETION:
        return True
    if command.op == SET_MUTE:
        return command.arg in (0, 127)
    if SET_VOLUME <= command.op <= SET_PAN or command.op == SET_CONTROL_4:
        return True
    return command.arg == 0 and (command.op != TAP_TEMPO or command.group == 0)


class CommandParser:
    """Per-group strict parser plus last-outcome replay protection.

    An incomplete/malformed frame never yields Command. A new CC60 replaces
    any incomplete frame for its group. An unrelated CC on that group channel
    interrupts it. Other group channels can interleave independently.
    """

    def __init__(self):
        self.ready = False
        self._frames = {}
        self._pending = {}
        self._completed = {}

    def receive(self, message):
        try:
            values = tuple(message)
        except TypeError:
            return None
        if len(values) >= 2 and values[:2] == (META_STATUS, HELLO_CC):
            self.ready = False
            self._frames.clear()
        if len(values) != 3 or not all(_integer(v, 0, 255) for v in values):
            self._interrupt_malformed(values)
            return None
        status, cc, value = values
        if cc > 127 or value > 127:
            self._interrupt_malformed(values)
            return None
        if status == META_STATUS and cc == HELLO_CC:
            self._frames.clear()
            self.ready = value == VERSION
            return ("hello", VERSION) if self.ready else None
        if not 0xB0 <= status < 0xB0 + GROUP_COUNT:
            return None
        group = status - 0xB0
        if not self.ready:
            return None
        if cc == 60:
            self._frames[group] = [value]
            return None
        frame = self._frames.get(group)
        if frame is None:
            return None
        expected = 60 + len(frame)
        if cc != expected:
            del self._frames[group]
            return None
        if cc != 63:
            frame.append(value)
            return None
        del self._frames[group]
        if value != frame[0]:
            return None
        command = Command(frame[0], group, frame[1], frame[2])
        previous = self._completed.get(group)
        if previous and previous.command.seq == command.seq:
            if previous.command == command:
                return previous
            return Rejected(group, command.seq, STALE)
        pending = self._pending.get(group)
        if pending is not None:
            outcome = STALE if pending.seq == command.seq and pending != command else BUSY
            return Rejected(group, command.seq, outcome)
        if not _valid_command(command):
            result = Replay(command, BAD_COMMAND)
            self._completed[group] = result
            return Rejected(group, command.seq, BAD_COMMAND)
        self._pending[group] = command
        return command

    def _interrupt_malformed(self, values):
        if values and _integer(values[0], 0xB0, 0xB0 + GROUP_COUNT - 1):
            self._frames.pop(values[0] - 0xB0, None)

    def complete(self, command, outcome):
        """Cache an outcome after dispatch, including rejected host operations."""
        if not _integer(outcome, APPLIED, STALE):
            raise ValueError("a completed command requires an outcome from 1 through 8")
        if self._pending.get(command.group) != command:
            raise ValueError("command is not pending")
        result = Replay(command, outcome)
        self._completed[command.group] = result
        del self._pending[command.group]
        return result

    def pending(self, group):
        """Whether a legacy command still owns this group's reservation."""
        return group in self._pending


def _cc(group, controller, value):
    if not _integer(group, 0, GROUP_COUNT - 1) or not _integer(value, 0, 127):
        raise ValueError("invalid group or CC value")
    return (0xB0 + group, controller, value)


def encode_snapshot(group, revision, fields):
    """Encode CC40..59, retaining internal `fields` keys 91..108."""
    if set(fields) != set(range(91, 109)):
        raise ValueError("snapshot requires every internal field from 91 through 108 exactly once")
    if not _integer(fields[91], UNAVAILABLE, FINISH_QUEUED):
        raise ValueError("invalid state")
    if fields[92] not in (0, 127) or type(fields[92]) is not int:
        raise ValueError("invalid mute")
    if not _integer(fields[103], 0, 31) or not _integer(fields[105], NONE, STALE):
        raise ValueError("invalid capabilities or outcome")
    if not _integer(fields[106], 0, 4):
        raise ValueError("invalid recording view")
    messages = [_cc(group, 40, revision)]
    messages.extend(_cc(group, field - 50, fields[field]) for field in range(91, 109))
    messages.append(_cc(group, 59, revision))
    return messages


def encode_progress(group, phase, countdown):
    return [_cc(group, 43, phase), _cc(group, 44, countdown)]


def encode_presentation(revision, payload):
    """Atomic global screen selection, independent of native group state."""
    payload = tuple(payload)
    if (not _integer(revision, 0, 127) or len(payload) != 7
            or not all(_integer(value, 0, 127) for value in payload)
            or payload[0] > 3 or payload[1] not in tuple(range(8)) + (127,)
            or payload[2] not in tuple(range(8)) + (127,)
            or payload[4] > 15 or payload[6] > 1):
        raise ValueError("invalid presentation")
    preview = payload[4] & (RECORD_PREVIEW | ADVANCE_PREVIEW)
    if (preview == (RECORD_PREVIEW | ADVANCE_PREVIEW)
            or preview and (payload[0] != 0 or payload[1] > 7 or payload[5] or payload[6])):
        raise ValueError("invalid gesture preview")
    return [(META_STATUS, cc, value) for cc, value in
            zip(range(20, 29), (revision,) + payload + (revision,))]


def encode_metadata(running):
    """Handshake response for Live-backed, timing-unvalidated prototype code."""
    if type(running) is not bool:
        raise ValueError("running must be a boolean observed from Live")
    return [(META_STATUS, 110, VERSION), (META_STATUS, 111, 1),
            (META_STATUS, 112, 127 if running else 0)]
