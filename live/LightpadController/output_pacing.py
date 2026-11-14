"""Coalesced MIDI frames, deadlines and a shared rolling send budget.

All methods run on Live's callback thread. Native recording is independent of
this output queue. Only immutable outcome/terminal frames are retained; ordinary
state is latest-only and clocks are sampled by their supplier at transmission.
"""

from collections import OrderedDict, deque


class OutputPacer:
    WINDOW = 0.09
    FRAME_LIMIT = 4
    BACKGROUND_LIMIT = 3
    METADATA_LIMIT = 5
    HEARTBEAT_TARGET = 1.0
    # Leave room for the next callback and a busy 90ms send window so actual
    # delivery stays within the two-second heartbeat deadline under edits.
    HEARTBEAT_DEADLINE = 1.75

    def __init__(self, send):
        self.send = send
        self.frames = OrderedDict()
        self.metadata = OrderedDict()
        self._frame_times = deque()
        self._metadata_times = deque()
        self._state_at = {}
        self._state_sent = {}
        self._latest = {}
        self._clock_due = {}
        self._clock_at = {}
        self._round_robin = 0
        self._presentation_at = None
        self.send_errors = 0
        self.last_send_error = None

    @staticmethod
    def _semantic(messages):
        return tuple(value for _, cc, value in messages if cc not in (40, 43, 44, 59))

    @staticmethod
    def _binding(messages):
        return messages[1][2], messages[17][2]

    def heartbeat_due(self, group, now):
        return now - self._state_at.get(0xB0 + group, float('-inf')) >= self.HEARTBEAT_TARGET

    def presentation_due(self, now):
        """Request the current presentation one second after its actual send."""
        return self._presentation_at is None or now - self._presentation_at >= 1.0

    def presentation(self, messages, now, binding=None, on_sent=None):
        """Queue one latest-only BF20..28 frame, optionally behind group state."""
        messages = list(messages)
        key = ('presentation',)
        previous = self.frames.get(key)
        self.frames[key] = {'make': lambda: messages, 'messages': messages,
            'deadline': min(previous['deadline'], now + .25) if previous else now + .25,
            'on_sent': on_sent, 'binding': tuple(binding) if binding is not None else None}

    def data(self, key, messages, now, urgent=False, on_sent=None):
        """Coalesced modal graphics within the same device transport budget."""
        key = ('data',) + tuple(key)
        previous = self.frames.get(key)
        messages = list(messages)
        self.frames[key] = {'make': lambda: messages, 'messages': messages,
            'deadline': min(previous['deadline'], now + (.15 if urgent else 1.5)) if previous else now + (.15 if urgent else 1.5),
            'on_sent': on_sent, 'urgent': urgent}

    def discard_data(self, prefix=()):
        prefix = ('data',) + tuple(prefix)
        for key in list(self.frames):
            if key[:len(prefix)] == prefix:
                del self.frames[key]

    def offer(self, messages, now, on_sent=None):
        if len(messages) != 20:
            for message in messages:
                self.metadata[message[1]] = message
            return
        status = messages[0][0]
        self._latest[status] = messages
        terminal = messages[16][2] == 4
        if on_sent is not None:
            key = ('result', status, messages[14][2], messages[15][2])
        elif terminal and not (status in self._state_sent and
                self._state_sent[status][16][2] == 4 and
                self._state_sent[status][17][2] == messages[17][2]):
            key = ('terminal', status, messages[17][2])
        else:
            key = ('state', status)
        previous = self.frames.get(key)
        if on_sent is not None or key[0] == 'terminal':
            deadline = now + .15
        elif status not in self._state_at:
            deadline = now  # Initialize every group before ordinary clocks.
        elif self._semantic(messages) != self._semantic(self._state_sent[status]):
            deadline = now + .25
        else:
            deadline = self._state_at[status] + self.HEARTBEAT_DEADLINE
        if previous is None or key[0] == 'state':
            self.frames[key] = {'make': lambda: messages, 'messages': messages,
                'deadline': min(previous['deadline'], deadline) if previous else deadline,
                'on_sent': on_sent}
        if key[0] != 'state':
            # Keep the newest ordinary state behind its immutable receipt. If
            # identical, sending that receipt removes the redundant state job.
            ordinary = ('state', status)
            old = self.frames.get(ordinary)
            self.frames[ordinary] = {'make': lambda: messages, 'messages': messages,
                'deadline': min(old['deadline'], now + .25) if old else now + .25,
                'on_sent': None}
        if status not in self._state_sent or self._binding(messages) != self._binding(self._state_sent[status]):
            self._clock_due[status - 0xB0] = now

    def clock(self, index, make_frame, now, urgent=False):
        key = ('clock', index)
        old = self.frames.get(key)
        self.frames[key] = {'make': make_frame, 'messages': None,
            'deadline': self._clock_at.get(index, now) + (.65 if index == 8 else .8),
            'on_sent': None, 'urgent': bool(urgent or old and old.get('urgent', False))}
        if old is not None:
            self.frames[key]['deadline'] = old['deadline']
        self._clock_due.setdefault(index, now)
        if urgent:
            # Actual tempo/meter/metronome changes also carry control feedback.
            # Make that latest sample eligible without bypassing the shared
            # send window or evaluating its phase before actual transmission.
            self.frames[key]['deadline'] = min(self.frames[key]['deadline'], now)
            # A slider can produce changes faster than the shared link can
            # sustain. Coalesce global feedback to one sample per send window;
            # the supplier still samples the latest actual value when sent.
            earliest = (self._clock_at.get(index, now - self.WINDOW) + self.WINDOW
                        if index == 8 else now)
            self._clock_due[index] = min(self._clock_due[index], max(now, earliest))

    def clear(self):
        # Keep applied results pinned across peer loss. Their parser reservations
        # end only on actual transmission; ordinary old samples are disposable.
        self.frames = OrderedDict((key, job) for key, job in self.frames.items()
                                  if key[0] in ('result', 'terminal'))
        self.metadata.clear()
        self._state_at.clear()
        self._state_sent.clear()
        self._clock_at.clear()
        self._clock_due.clear()
        self._presentation_at = None

    @staticmethod
    def _urgent(key, job):
        return key[0] in ('result', 'terminal') or (key[0] in ('clock', 'data') and job.get('urgent', False))

    def _pick(self, now, skipped):
        candidates = []
        for order, (key, job) in enumerate(self.frames.items()):
            if key in skipped:
                continue
            if len(self._frame_times) >= self.BACKGROUND_LIMIT and not self._urgent(key, job):
                continue
            if key[0] == 'clock':
                index = key[1]
                if now + 1e-9 < self._clock_due[index]:
                    continue
            elif key[0] == 'presentation':
                index = 8
                if job['binding'] is not None:
                    group, phrase, take = job['binding']
                    sent = self._state_sent.get(0xB0 + group)
                    if sent is None or self._binding(sent) != (phrase, take):
                        continue
            elif key[0] == 'data':
                index = 8
            else:
                status = key[1]
                index = status - 0xB0
                # Protected per-group frames preserve their original ordering.
                prior = next((other for other in self.frames if other[0] in ('result', 'terminal')
                              and other[1] == status), None)
                if prior is not None and key != prior:
                    continue
            rank = (index - self._round_robin) % 9
            # Use the reserved fourth slot for global edit feedback when
            # ordinary work is ready. Sending it first would consume one of
            # the three background slots and strand that reserved capacity.
            # Protected receipts retain their deadline/per-group ordering.
            reserved_feedback = key == ('clock', 8) and job.get('urgent', False)
            candidates.append((reserved_feedback, job['deadline'], rank, order, key))
        return min(candidates)[-1] if candidates else None

    def flush(self, now):
        for history in (self._frame_times, self._metadata_times):
            while history and now - history[0] >= self.WINDOW - 1e-9:
                history.popleft()
        while self.metadata and len(self._metadata_times) < self.METADATA_LIMIT:
            controller = next(iter(self.metadata))
            message = self.metadata[controller]
            self._metadata_times.append(now)
            try:
                self.send([message])
            except Exception as error:
                self._send_failed(error)
                return
            del self.metadata[controller]
        if 110 in self.metadata or 111 in self.metadata:
            return
        skipped = set()
        while len(self._frame_times) < self.FRAME_LIMIT:
            key = self._pick(now, skipped)
            if key is None:
                break
            job = self.frames[key]
            messages = job['make']()
            if key[0] == 'clock':
                index = key[1]
                if messages is False:  # Valid clock, transient wire rounding.
                    self._clock_due[index] = now + self.WINDOW
                    skipped.add(key)
                    continue
                if messages is not None and index < 8 and messages[1][2] & 3:
                    sent = self._state_sent.get(0xB0 + index)
                    binding = messages[1][2] & 3, messages[2][2]
                    if sent is None or self._binding(sent) != binding:
                        skipped.add(key)
                        continue
                period = .25 if index == 8 else .5
                due = self._clock_due[index] + period
                # No catch-up bursts: a delayed sample replaces missed samples.
                self._clock_due[index] = due if due > now + 1e-9 else now + period
            if messages is None:
                del self.frames[key]
                continue
            # A partial/ambiguous transmission consumes a complete frame's
            # budget. Keep the original receipt and callback until a complete
            # retry succeeds; only transport is retried, never native intent.
            self._frame_times.append(now)
            try:
                self.send(messages)
            except Exception as error:
                self._send_failed(error)
                return
            del self.frames[key]
            if key[0] == 'clock':
                self._clock_at[index] = now
            if key[0] == 'presentation':
                self._presentation_at = now
                index = 8
            elif key[0] == 'data':
                index = 8
            else:
                index = key[1] if key[0] == 'clock' else key[1] - 0xB0
            self._round_robin = (index + 1) % 9
            if key[0] not in ('clock', 'presentation', 'data'):
                status = key[1]
                self._state_at[status] = now
                self._state_sent[status] = messages
                ordinary = self.frames.get(('state', status))
                if ordinary and self._semantic(ordinary['messages']) == self._semantic(messages):
                    del self.frames['state', status]
            if job['on_sent'] is not None:
                job['on_sent']()

    def _send_failed(self, error):
        self.send_errors += 1
        self.last_send_error = '%s: %s' % (type(error).__name__, str(error)[:300])
