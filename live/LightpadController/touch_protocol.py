"""Private31 role-tagged raw touch frames, independent of Live and MIDI hardware.

Finger channels B0..BF carry CC20..26: sequence, kind, x-low, x-high,
y-low, y-high, matching sequence. Coordinates are unsigned LED units /128.
Readiness belongs to the surrounding surface: Hello clears all state and
closes this parser until reset(ready=True). No event implies a synthetic End.
"""

from collections import namedtuple


FINGER_COUNT = 16
START, MOVE, END = 1, 2, 3
BEGIN_CC, COMMIT_CC = 20, 26
META_STATUS, HELLO_CC = 0xBF, 110
COORDINATE_SCALE, MAX_COORDINATE = 128, 1919

Touch = namedtuple("Touch", "kind index x y seq role moved drawer_open view group can_accept dragged")


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


class TouchParser:
    """Atomically decode independent finger frames and retain replay guards.

    Only a Start can establish a finger lifecycle. A fresh-sequence Start
    replaces an existing lifecycle authoritatively, emitting no End; the UI
    must cancel its prior gesture without committing it. Move/End require a
    Start. Missing messages neither advance coordinates nor invent releases.

    Sequence values are opaque modulo128 identifiers, not time or ordering
    comparisons. Repeating the latest accepted sequence is ignored, including
    conflicting payloads. Gaps and 127->0 work without requiring lost Move
    frames. A watchdog belongs to the UI and may call expire(index).
    """

    def __init__(self, ready=False):
        self.pre_touch = None
        self.reset(ready=ready)

    def reset(self, ready=False):
        """Clear frames, finger lifecycles and replay guards on connection reset."""
        if type(ready) is not bool:
            raise ValueError("ready must be a boolean")
        self.latest = None
        self._roles = {}
        self.ready = ready
        self._frames = {}
        self._active = set()
        self._last = {}

    def expire(self, index):
        """Abort one lifecycle/frame without emitting End; retain its replay guard.

        Retaining the last accepted sequence prevents a late duplicate Start
        from reactivating an expired gesture. A new-sequence Start recovers
        immediately. Other fingers and connection readiness are unchanged.
        """
        if not _integer(index, 0, FINGER_COUNT - 1):
            raise ValueError("invalid finger index")
        self._frames.pop(index, None)
        self._active.discard(index)
        self._roles.pop(index, None)

    def _interrupt_malformed(self, values):
        if values and _integer(values[0], 0xB0, 0xBF):
            self._frames.pop(values[0] - 0xB0, None)

    def receive(self, message):
        """Return a complete Touch or None. Malformed frames never update touch state."""
        self.latest = None
        try:
            values = tuple(message)
        except TypeError:
            return None
        # A recognizable Hello also closes on malformed data, failing safely.
        if len(values) >= 2 and values[0] == META_STATUS and values[1] == HELLO_CC:
            self.reset(ready=False)
            return None
        if len(values) != 3 or not all(_integer(v, 0, 255) for v in values):
            self._interrupt_malformed(values)
            return None
        status, cc, value = values
        if cc > 127 or value > 127:
            self._interrupt_malformed(values)
            return None
        if not 0xB0 <= status <= 0xBF or not self.ready:
            return None
        index = status - 0xB0
        if cc == BEGIN_CC:
            self._frames[index] = [value]
            return None
        frame = self._frames.get(index)
        if frame is None:
            return None
        if cc != BEGIN_CC + len(frame):
            del self._frames[index]
            return None
        if cc != COMMIT_CC:
            frame.append(value)
            return None
        del self._frames[index]
        kind = frame[1] & 3
        role = (frame[1] >> 2) & 7
        moved = bool(frame[1] & 32)
        drawer_open = bool(frame[1] & 64)
        view = (frame[3] >> 4) & 3
        flag = bool(frame[3] & 64)
        can_accept = flag if role==0 else False
        dragged = flag if role==1 and kind!=START else False
        group = (frame[5] >> 4) | (8 if view == 1 and drawer_open else 0)
        if value != frame[0] or kind not in (START, MOVE, END) or role==7 or moved and kind!=END or role==1 and kind==START and flag:
            return None
        x = frame[2] | ((frame[3] & 15) << 7)
        y = frame[4] | ((frame[5] & 15) << 7)
        if x > MAX_COORDINATE or y > MAX_COORDINATE:
            return None
        event = Touch(kind,index,x/float(COORDINATE_SCALE),y/float(COORDINATE_SCALE),frame[0],role,moved,drawer_open,view,group,can_accept,dragged)
        previous = self._last.get(index)
        if previous is not None and previous.seq == event.seq:
            return None
        if event.kind != START and index not in self._active:
            return None
        if event.kind!=START and role!=self._roles.get(index):
            if not (self._roles.get(index) in (1,2,3,4) and role==5):return None
        self._roles[index]=role
        self._last[index] = event
        if event.kind == END:
            self._active.remove(index)
            self._roles.pop(index,None)
        else:
            self._active.add(index)
        self.latest=event
        if self.pre_touch is not None:self.pre_touch(event)
        return event


def encode_touch(kind,index,x,y,seq,role,drawer_open=False,view=0,group=0,can_accept=False,moved=False,dragged=False):
    """Pure fixture encoder; exactly seven ordinary CC messages."""
    for value,lo,hi in ((kind,1,3),(index,0,15),(seq,0,127),(role,0,6),(view,0,3),(group,0,11 if view == 1 else 7)):
        if not _integer(value,lo,hi):raise ValueError('role touch range')
    if any(type(v) is not bool for v in (drawer_open,can_accept,moved,dragged)) or moved and kind!=END:raise ValueError('role touch flags')
    if dragged and (role!=1 or kind==START):raise ValueError('dragged onlyhandle nonStart')
    px,py=int(x*128),int(y*128)
    if not 0<=px<=1919 or not 0<=py<=1919:raise ValueError('coordinates')
    opened = drawer_open or view == 1 and group >= 8
    values=(seq,kind|(role<<2)|(int(moved)<<5)|(int(opened)<<6),px&127,(px>>7)|(view<<4)|(int(dragged if role==1 and kind!=START else can_accept)<<6),py&127,(py>>7)|((group & 7)<<4),seq)
    return tuple((0xB0+index,20+i,value) for i,value in enumerate(values))
