"""Atomic Live-to-helper readiness, independent of device graphics and Hello.

The helper consumes these five CCs locally. They never enter the pad's ordinary
frame budget. Readiness describes current usable groups, not successful upload.
No native recording or setup mutation is performed here.
"""

CONNECTING, BRIDGE_UNAVAILABLE, LIVE_UNAVAILABLE, NO_GROUPS, FAILURE, READY = range(6)
NONE, WAITING_FOR_LIVE, NO_REMOTE_GROUPS, HOST_FAILURE, LOADING = range(5)
DEVICE_DISCONNECTED, DEVICE_BUSY, PROGRAM_LOAD_FAILED, DEVICE_REJECTED, STALE_FEEDBACK, PROTOCOL_MISMATCH = range(5, 11)
RECORDING_BUSY = 1
SAFE_MODAL = 2


def encode_health(sequence, state, reason=NONE, flags=0):
    if (type(sequence) is not int or not 0 <= sequence < 128
            or type(state) is not int or not CONNECTING <= state <= READY
            or type(reason) is not int or not NONE <= reason <= PROTOCOL_MISMATCH
            or type(flags) is not int or flags & ~3):
        raise ValueError("invalid helper readiness")
    return [(0xBF, cc, value) for cc, value in zip(
        range(114, 119), (sequence, state, reason, flags, sequence))]


class HealthPublisher:
    """Publish complete changes promptly and heartbeats every half second.

    A failed send consumes the minimum interval but commits neither the sample
    nor its sequence. The next publication sends a complete frame again.
    """
    HEARTBEAT = .5
    MIN_INTERVAL = .125

    def __init__(self, send, now):
        self._send, self._now = send, now
        self._at = None
        self._sent_at = None
        self._last = None
        self._sequence = 0
        self.send_errors = 0

    def publish(self, state, reason=NONE, flags=0, force=False):
        sample = (state, reason, flags)
        messages = encode_health(self._sequence, *sample)
        now = self._now()
        if self._at is not None and now - self._at < self.MIN_INTERVAL - 1e-9:
            return False
        if (not force and self._last == sample and self._sent_at is not None
                and now - self._sent_at < self.HEARTBEAT - 1e-9):
            return False
        self._at = now
        try:
            self._send(messages)
        except Exception:
            self.send_errors += 1
            return False
        self._last, self._sent_at = sample, now
        self._sequence = (self._sequence + 1) % 128
        return True
