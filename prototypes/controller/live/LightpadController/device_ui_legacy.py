"""Approved Lightpad gestures, independent of Live, MIDI framing and rendering.

send(group, opcode, argument, on_result) reserves an intent and returns bool.
The adapter invokes on_result(outcome) only when snapshot(group) can observe its
actual result; callbacks may be synchronous. The adapter owns command IDs,
reservation/replay rules and native operations. This module never invents an
actual state change or retries a rejected discrete performance gesture.

snapshot(group) supplies current actual internal field keys91..108 (or None).
meter() supplies actual (numerator, denominator, metronome_enabled). now() is
monotonic seconds. Raw touch coordinates are LED units0..14.99. Each held finger
needs Move heartbeats less than one second apart. Missing contacts cancel the
gesture without synthesizing releases; native recording remains independent.
"""

import math
from collections import OrderedDict
from dataclasses import dataclass

from . import protocol as p


@dataclass
class _Touch:
    x: float
    y: float
    started: float
    seen: float
    group: int = None
    control: int = None
    origin: float = 0.0
    value: int = 0
    moved: bool = False
    down_leg: bool = False
    elbow: float = 0.0
    turned: bool = False
    kind: str = ''
    ignored: bool = False
    take: int = None


class DeviceUI:
    HOLD_SECONDS = .5
    TOUCH_LEASE_SECONDS = 1.0
    MIXER_FIELDS = (98, 99, 100, 101, 108, 102)
    MIXER_OPS = (p.SET_VOLUME, p.SET_SEND_1, p.SET_SEND_2, p.SET_SEND_3,
                 p.SET_CONTROL_4, p.SET_PAN)
    DENOMINATORS = (1, 2, 4, 8, 16)
    METER_OPS = (16, 17, 18)
    RECORD_OPS = (p.ABORT, p.ACCEPT, p.RETRY, p.ACCEPT_NEXT)

    def __init__(self, send, snapshot, meter, now):
        self._send, self._snapshot, self._meter, self._now = send, snapshot, meter, now
        self._generation = 0
        self.pulse_reset = 0
        self.reset()

    def reset(self):
        """Peer loss/reset: forget gestures and desires, never native intent."""
        self._generation += 1
        self.view = 0
        self.selected_group = None
        self.recording_group = None
        self.active_take = None
        self.begin_wait = False
        self._rebind_take = False
        self._retry_recovery = False
        self.anchor = None
        self.chord_mask = 0
        self._chord = False
        self._touches = {}
        self._inflight = {}
        self._desired = [OrderedDict() for _ in range(8)]
        self._receipts = {}
        self._receipt_attempted = {}

    def _fields(self, group):
        return self._snapshot(group) if group is not None else None

    def _valid_meter(self):
        state = self._meter()
        if (isinstance(state, tuple) and len(state) == 3
                and type(state[0]) is int and 1 <= state[0] <= 99
                and type(state[1]) is int and state[1] in self.DENOMINATORS
                and type(state[2]) is bool):
            return state
        return None

    def _can_send(self, group, op):
        if group == 0 and op in (p.TAP_TEMPO, *self.METER_OPS):
            return self._valid_meter() is not None
        return self._available(group)

    def _available(self, group):
        fields = self._fields(group)
        return fields is not None and fields[91] in (p.EMPTY, p.PLAYING)

    def _capable(self, group, flag):
        return self._available(group) and bool(self._fields(group)[103] & flag)

    @staticmethod
    def group_at(x, y):
        px, py = int(x), int(y)
        return None if py > 6 or py == 3 or px % 4 == 3 else py // 4 * 4 + px // 4

    @staticmethod
    def record_control_at(x, y):
        px, py = int(x), int(y)
        if px < 1 or px > 13 or py < 1 or py > 13 or px == 7 or py == 7:
            return None
        return (2 if py > 7 else 0) + (1 if px > 7 else 0)

    def _try_send(self, group, op, arg):
        if not self._can_send(group, op) or group in self._inflight:
            return False
        marker = (op, int(arg), object())
        generation = self._generation
        self._inflight[group] = marker

        def result(outcome):
            if generation != self._generation or self._inflight.get(group) is not marker:
                return
            del self._inflight[group]
            self._result(group, op, outcome)
        try:
            accepted = bool(self._send(group, op, int(arg), result))
        except Exception:
            if self._inflight.get(group) is marker:
                del self._inflight[group]
            raise
        if not accepted and self._inflight.get(group) is marker:
            del self._inflight[group]
        return accepted

    def _pending_value(self, group, op, fallback):
        pending = self._inflight.get(group)
        return pending[1] if pending is not None and pending[0] == op else fallback

    def _result(self, group, op, outcome):
        fields = self._fields(group)
        if self.view == 2 and self.recording_group == group and fields is not None:
            observed, take = fields[106], fields[107]
            if op in (p.ACCEPT_NEXT, p.RETRY):
                self._rebind_take = outcome in (p.APPLIED, p.OUTCOME_QUEUED)
            if op == p.RETRY:
                # Retry may discard successfully, then fail to reserve its
                # replacement. View1/take0 is retained by Session with no take.
                self._retry_recovery = not self._rebind_take and observed == 1 and take == 0
                if self._retry_recovery:
                    self.active_take = None
            if self.begin_wait and op == p.BEGIN:
                self.begin_wait = False
                if outcome in (p.APPLIED, p.OUTCOME_QUEUED) and observed in (1, 2, 3):
                    self.active_take = take
                else:
                    self.view = 0
            if observed == 0 and op in (p.ABORT, p.RETRY):
                self.view = 0
                self._retry_recovery = False
        self._observe()
        self._flush(group)

    def _observe(self):
        for group in range(8):
            fields = self._fields(group)
            if not self._capable(group, p.CAP_MIXER):
                for op in self.MIXER_OPS:
                    self._desired[group].pop(op, None)
            if not self._available(group):
                self._receipts.pop(group, None)
                if self.recording_group == group and self.view == 2:
                    self.view, self.active_take, self.begin_wait = 0, None, False
                    self._retry_recovery = False
                if self.selected_group == group and self.view == 1:
                    self._cancel_gesture(clear_desired=False)
                continue
            observed, take = fields[106], fields[107]
            if self.view == 2 and self.recording_group == group and not self.begin_wait:
                if self._rebind_take and observed in (1, 2) and take != self.active_take:
                    self.active_take, self._rebind_take = take, False
                    self._retry_recovery = False
                if observed == 4 and take == self.active_take:
                    self.view = 0
            if observed != 4:
                self._receipts.pop(group, None)
                self._receipt_attempted.pop(group, None)
            elif not (self.view == 2 and self.recording_group == group and self.begin_wait):
                if self._receipt_attempted.get(group) != take:
                    self._receipts[group] = take

    def _flush(self, group):
        if group in self._inflight:
            return
        desired = self._desired[group]
        # Preserve the prototype's priority: meter edits, terminal receipt,
        # then relative mixer controls. Only desired parameters are coalesced.
        for op in self.METER_OPS if group == 0 and self._valid_meter() is not None else ():
            if op in desired:
                value = desired.pop(op)
                if not self._try_send(group, op, value):
                    desired[op] = value
                return
        if not self._available(group):
            return
        if group in self._receipts:
            take = self._receipts.pop(group)
            self._receipt_attempted[group] = take
            if not self._try_send(group, p.ACK_COMPLETION, take):
                self._receipt_attempted.pop(group, None)
                self._receipts[group] = take
            return
        for op, field in zip(self.MIXER_OPS, self.MIXER_FIELDS):
            if op in desired:
                value = desired.pop(op)
                if value != self._fields(group)[field]:
                    if not self._try_send(group, op, value):
                        desired[op] = value
                    return

    def _want(self, group, op, value):
        if self._can_send(group, op):
            self._desired[group][op] = int(value)
            self._flush(group)

    def _queue_take(self, group):
        if not self._capable(group, p.CAP_RECORDING) or group in self._inflight:
            return
        previous = (self.view, self.recording_group, self.active_take, self.begin_wait,
                    self._rebind_take, self._retry_recovery, self.pulse_reset)
        self.view, self.recording_group = 2, group
        self.active_take, self.begin_wait, self._rebind_take = None, True, False
        self._retry_recovery = False
        self.pulse_reset += 1
        if not self._try_send(group, p.BEGIN, 0):
            (self.view, self.recording_group, self.active_take, self.begin_wait,
             self._rebind_take, self._retry_recovery, self.pulse_reset) = previous

    def _record_action_enabled(self, action):
        group = self.recording_group
        if self.view != 2 or self.begin_wait or not self._available(group):
            return False
        fields = self._fields(group)
        if self.active_take != fields[107]:
            recovering = (self._retry_recovery and action in (0, 2)
                          and fields[106] == 1 and fields[107] == 0)
            if not recovering:
                return False
        if action in (1, 3) and (fields[106] != 2 or group in self._inflight):
            return False
        if action == 3 and not self._capable(group, p.CAP_CHAINING):
            return False
        return True

    def _record_action(self, action):
        if self._record_action_enabled(action):
            self._try_send(self.recording_group, self.RECORD_OPS[action], 0)

    def _cancel_gesture(self, clear_desired=True):
        self.anchor = None
        self._chord, self.chord_mask = False, 0
        for contact in self._touches.values():
            contact.ignored = True
        if self.view in (1, 3):
            self.view = 0
        if clear_desired:
            for desired in self._desired:
                desired.clear()

    def tick(self):
        now = self._now()
        if any(not t.ignored and now - t.seen >= self.TOUCH_LEASE_SECONDS
               for t in self._touches.values()):
            self._cancel_gesture()
        self._observe()
        if self.view == 0 and not self._chord and self.anchor is not None:
            contact = self._touches[self.anchor]
            if not contact.moved and now - contact.started >= self.HOLD_SECONDS:
                if contact.kind == 'group' and self._capable(self.selected_group, p.CAP_MIXER):
                    self.view = 1
                elif contact.kind == 'tempo':
                    self.view = 3
        for group in range(8):
            self._flush(group)

    def touch(self, kind, index, x, y):
        if kind not in (1, 2, 3) or type(index) is not int or not 0 <= index < 16:
            return
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float))
                and math.isfinite(x) and math.isfinite(y)):
            return
        x, y = max(0., min(14.99, x)), max(0., min(14.99, y))
        if kind == 1:
            old = self._touches.get(index)
            if old is not None and not old.ignored:
                # Start is an authoritative new lifecycle, including recovery
                # from a lost End. Cancel before tick can drain old desires.
                self._cancel_gesture()
        self.tick()
        if kind == 1:
            self._start(index, x, y)
            return
        contact = self._touches.get(index)
        if contact is None or contact.ignored:
            if kind == 3:
                self._touches.pop(index, None)
            return
        contact.seen = self._now()
        if kind == 2:
            self._move(index, x, y)
        else:
            self._end(index, x, y)

    def _start(self, index, x, y):
        contact = _Touch(x, y, self._now(), self._now())
        self._touches[index] = contact
        if self.view in (1, 3):
            if index == self.anchor:
                contact.ignored = True
                self._cancel_gesture()
                return
            if self.view == 3:
                if not 7 <= y < 14:
                    return
                state = self._valid_meter()
                if state is None:
                    return
                num, den, click = state
                number = 2 if y < 9 else 0 if x < 7 else 1 if x >= 8 else None
                if number is None or num <= 0 or den not in self.DENOMINATORS:
                    return
                contact.control = number
                contact.value = (num, self.DENOMINATORS.index(den), 127 if click else 0)[number]
                contact.origin = y
            elif self._capable(self.selected_group, p.CAP_MIXER):
                other = 4 if self.selected_group < 4 else 0
                number = 5 if other <= y < other + 3 else None
                px = int(x)
                if y >= 8:
                    number = 0 if px < 3 else None if px % 3 == 0 else px // 3
                if number is not None:
                    contact.control = number
                    contact.value = self._fields(self.selected_group)[self.MIXER_FIELDS[number]]
                    contact.origin = x if number == 5 else y
            return
        if self.view == 0:
            group = self.group_at(x, y)
            if self._chord:
                if group is not None and self._available(group):
                    contact.group = group
                    self.chord_mask ^= 1 << group
                return
            if self.anchor is not None:
                first = self._touches.get(self.anchor)
                if first is not None and first.kind == 'group' and group is not None and self._available(group):
                    contact.group = group
                    self._chord = True
                    self.chord_mask = (1 << self.selected_group) ^ (1 << group)
                return
        if self.anchor is not None:
            return
        self.anchor = index
        if self.view == 2:
            contact.kind = 'record'
            contact.control = self.record_control_at(x, y)
            contact.take = self.active_take
            if contact.control in (1, 3) and not self._record_action_enabled(contact.control):
                contact.control = None
            return
        self.selected_group = self.group_at(x, y)
        if self.selected_group is not None and self._available(self.selected_group):
            contact.kind, contact.group = 'group', self.selected_group
        if y >= 14:
            contact.kind = 'tempo'

    def _move(self, index, x, y):
        contact = self._touches[index]
        if self._chord:
            return
        if self.view in (1, 3):
            number = contact.control
            if index == self.anchor or number is None:
                return
            if self.view == 3:
                if number == 2:
                    return
                state = self._valid_meter()
                if state is None:
                    return
                value = contact.value + int(contact.origin - y)
                value = max(1, min(99, value)) if number == 0 else self.DENOMINATORS[max(0, min(4, value))]
                origin_value = self.DENOMINATORS[contact.value] if number == 1 else contact.value
                contact.moved |= value != origin_value
                op = self.METER_OPS[number]
                wanted = self._desired[0].get(op, self._pending_value(0, op, state[number]))
                if contact.moved and value != wanted:
                    self._want(0, op, value)
            elif self._capable(self.selected_group, p.CAP_MIXER):
                delta = (x - contact.origin) / 14 if number == 5 else (contact.origin - y) / 7
                value = int(max(0., min(127., contact.value + delta * 127)) + .5)
                contact.moved |= value != contact.value
                group, op = self.selected_group, self.MIXER_OPS[number]
                actual = self._fields(group)[self.MIXER_FIELDS[number]]
                wanted = self._desired[group].get(op, self._pending_value(group, op, actual))
                if contact.moved and value != wanted:
                    self._want(group, op, value)
            return
        if index != self.anchor:
            return
        dx, dy = x - contact.x, y - contact.y
        contact.moved |= abs(dx) > 1 or abs(dy) > 1
        if contact.kind == 'group':
            # Returning to the original tile cancels the pending action even
            # when a touch near its top edge still has dy>=2. moved stays
            # sticky so releasing a returned drag can never become a mute tap.
            if self.group_at(x, y) == contact.group or dy < 2:
                contact.down_leg, contact.turned = False, False
            elif not contact.down_leg:
                if abs(dx) <= 1.5:
                    contact.down_leg, contact.elbow = True, x
                    contact.turned = False
            else:
                # This is current intent, not the history of visiting an L.
                contact.turned = abs(x - contact.elbow) >= 2

    def _end(self, index, x, y):
        contact = self._touches[index]
        if self._chord:
            del self._touches[index]
            if contact.group is None:
                return
            if index == self.anchor:
                self.anchor = None
            if any(t.group is not None and not t.ignored for t in self._touches.values()):
                return
            groups = self.chord_mask
            self._chord, self.chord_mask = False, 0
            for group in range(8):
                if groups & (1 << group) and self._available(group):
                    self._try_send(group, p.SET_MUTE, 0 if self._fields(group)[92] else 127)
            return
        self._move(index, x, y)
        del self._touches[index]
        if self.view in (1, 3) and index != self.anchor:
            state = self._valid_meter() if self.view == 3 else None
            if state is not None and contact.control == 2:
                self._want(0, 18, 0 if state[2] else 127)
            return
        if index != self.anchor:
            return
        self.anchor = None
        if self.view in (1, 3):
            self._cancel_gesture(clear_desired=False)
        elif contact.kind == 'record':
            if (contact.take == self.active_take and contact.control is not None
                    and contact.control == self.record_control_at(x, y)):
                self._record_action(contact.control)
        elif contact.kind == 'tempo' and not contact.moved:
            self._try_send(0, p.TAP_TEMPO, 0)
        elif contact.kind == 'group':
            group = self.selected_group
            if not self._available(group):
                return
            if contact.down_leg and contact.turned:
                if self._capable(group, p.CAP_ADVANCE) and self._fields(group)[91] == p.PLAYING:
                    self._try_send(group, p.ADVANCE, 0)
            elif contact.down_leg:
                self._queue_take(group)
            elif not contact.moved:
                self._try_send(group, p.SET_MUTE, 0 if self._fields(group)[92] else 127)

    def _gesture_preview(self):
        if self.view != 0 or self._chord or self.anchor is None or not self._available(self.selected_group):
            return 0
        if sum(not touch.ignored for touch in self._touches.values()) != 1:
            return 0
        contact = self._touches.get(self.anchor)
        if contact is None or contact.ignored or contact.kind != 'group' or not contact.down_leg:
            return 0
        return p.ADVANCE_PREVIEW if contact.turned else p.RECORD_PREVIEW

    def presentation(self):
        """Seven7-bit values consumed by the separate device presentation codec."""
        fields = self._fields(self.recording_group)
        can_accept = (self.view == 2 and not self.begin_wait and self._available(self.recording_group)
                      and fields[106] == 2 and fields[107] == self.active_take
                      and self.recording_group not in self._inflight)
        return (self.view, 127 if self.selected_group is None else self.selected_group,
                127 if self.recording_group is None else self.recording_group,
                0 if self.active_take is None else self.active_take,
                int(self.begin_wait) | (2 if can_accept else 0) | self._gesture_preview(),
                self.chord_mask & 127, self.chord_mask >> 7)
