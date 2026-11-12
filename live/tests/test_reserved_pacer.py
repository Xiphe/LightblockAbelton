"""Reserved fourth frame remains bounded and cannot admit background traffic."""
import unittest
from LightpadController import protocol as p, timing, compact_wire as compact
from LightpadController.output_pacing import OutputPacer

def packet_cost(messages):
    """A8 uses8 body bytes/event,15 wire bytes/event and8 packet bytes."""
    wire = compact.project_wire(messages)
    events = (len(wire) - 2 + 7) // 8
    return (events, 8 + 15 * events)

def snapshot(group=0, revision=1, take=7, view=1, sequence=10, outcome=0, volume=64):
    fields = dict.fromkeys(range(91, 109), 0)
    fields.update({91: 2, 94: 127, 98: volume, 104: sequence, 105: outcome, 106: view, 107: take})
    return p.encode_snapshot(group, revision, fields)

def clock(index=8, take=7, position=1):
    c = timing.Clock(1 if index == 8 else 2, 0 if index == 8 else take, 4 if index == 8 else 8, position, 0, 4, 120, 4, 4, True)
    return timing.encode_global_timing(1, c) if index == 8 else timing.encode_view_timing(index, 1, c)

class ReservedPacerTests(unittest.TestCase):

    def test_ordinary_cannot_use_fourth_but_urgent_clock_uses_it_and_samples_latest(self):
        sent = []
        calls = []
        out = OutputPacer(sent.append)
        for group in range(4):
            out.offer(snapshot(group), 100)
        out.flush(100)
        self.assertEqual(len(sent), 3)
        out.clock(8, lambda: clock(position=1), 100.001, urgent=True)

        def latest():
            calls.append(True)
            return clock(position=2)
        out.clock(8, latest, 100.002, urgent=False)
        self.assertEqual(calls, [])
        self.assertTrue(out.frames['clock', 8]['urgent'])
        out.flush(100.002)
        self.assertEqual(sent[-1], clock(position=2))
        self.assertEqual(len(sent), 4)
        self.assertEqual(calls, [True])
        out.clear()
        out.offer(snapshot(7), 100.003)
        out.flush(100.003)
        self.assertEqual(len(sent), 4)
        out.flush(100.09)
        self.assertEqual(len(sent), 5)

    def test_protected_order_callbacks_and_matching_take_lease_survive_reconnect(self):
        sent = []
        completed = []
        out = OutputPacer(sent.append)
        out.offer(snapshot(take=7, view=1, outcome=p.APPLIED), 100, lambda: completed.append('result'))
        out.offer(snapshot(revision=2, take=7, view=4), 100.01)
        out.clear()
        out.offer(snapshot(revision=3, take=8, view=2), 100.02)
        out.clock(0, lambda: clock(index=0, take=8, position=3), 100.02)
        out.flush(100.02)
        out.flush(100.12)
        self.assertEqual([(m[16][2], m[17][2]) for m in sent if m[0][1] == 40], [(1, 7), (4, 7), (2, 8)])
        self.assertEqual(completed, ['result'])
        self.assertEqual(sent[-1][0][1], 70)
        self.assertEqual(sent[-2][17][2], sent[-1][2][2])

    def test_send_failure_consumes_reserved_slot_without_releasing_receipt(self):
        sent = []
        done = []

        def send(messages):
            sent.append(messages)
            if len(sent) == 4:
                raise OSError('injected ambiguous reserved send')
        out = OutputPacer(send)
        for group in range(3):
            out.offer(snapshot(group), 100)
        out.flush(100)
        out.offer(snapshot(7, outcome=p.APPLIED), 100.001, lambda: done.append(1))
        out.flush(100.001)
        self.assertEqual(done, [])
        self.assertEqual(out.send_errors, 1)
        out.flush(100.002)
        self.assertEqual(len(sent), 4)
        out.flush(100.1)
        self.assertEqual(done, [1])

    def test_rolling_bound_fair_groups_metadata_and_actual_A8_byte_event_limits(self):
        now = [100.0]
        sent = []
        out = OutputPacer(lambda messages: sent.append((now[0], messages)))
        for step in range(300):
            now[0] = 100 + step * 0.01
            for group in range(8):
                out.offer(snapshot(group, revision=step % 128, volume=step % 128), now[0])
            if step % 7 == 0:
                out.clock(8, lambda: clock(), now[0], urgent=True)
            if step % 11 == 0:
                out.offer([(191, 112, 127), (191, 113, 1)], now[0])
            out.flush(now[0])
        frames = [(t, m) for t, m in sent if len(m) in (9, 20)]
        self.assertTrue(frames)
        for t, _ in frames:
            window = [m for at, m in frames if t <= at < t + 0.09 - 1e-09]
            self.assertLessEqual(len(window), 4)
            costs = [packet_cost(m) for m in window]
            self.assertLessEqual(sum((count for count, size in costs)), 12)
            self.assertLessEqual(sum((size for count, size in costs)), 212)
        self.assertEqual({m[0][0] for _, m in frames if m[0][1] == 40}, set(range(176, 184)))
        metadata = [t for t, m in sent if len(m) == 1]
        for t in metadata:
            self.assertLessEqual(sum((t <= at < t + 0.09 - 1e-09 for at in metadata)), 5)
if __name__ == '__main__':
    unittest.main()
