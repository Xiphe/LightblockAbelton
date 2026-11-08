"""Offline queue-boundary regressions with actual protocol encoders, no Live."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p, timing
from LightpadController.output_pacing import OutputPacer


def snapshot(group=0, revision=1, *, take=7, view=1, seq=10,
             outcome=p.APPLIED, state=p.PLAYING):
    fields = dict.fromkeys(range(91, 109), 0)
    fields.update({91: state, 94: 127, 104: seq, 105: outcome, 106: view, 107: take})
    return p.encode_snapshot(group, revision, fields)


def clock(index=0, *, take=7, position=1):
    native = timing.Clock(timing.ONE_BAR if index == 8 else timing.REFERENCE,
                          0 if index == 8 else take, 4 if index == 8 else 8,
                          position, 0, 4, 120, 4, 4, True)
    return timing.encode_global_timing(1, native) if index == 8 else timing.encode_view_timing(index, 1, native)


class OutputPacingTests(unittest.TestCase):
    def test_reconnect_preserves_result_and_terminal_then_restores_current_take_before_clock(self):
        sent, completed = [], []
        output = OutputPacer(lambda messages: sent.append(messages))
        output.offer(snapshot(take=7, view=1), 100, lambda: completed.append('result'))
        output.offer(snapshot(revision=2, take=7, view=4), 100.01)
        # Native state can advance while peer feedback is disconnected. The
        # immutable receipts survive; obsolete ordinary snapshots do not.
        output.clear()
        output.offer(snapshot(revision=3, take=8, view=2), 100.02)
        output.clock(0, lambda: clock(take=8, position=3), 100.02)
        output.flush(100.02)
        output.flush(100.12)
        self.assertEqual([(m[16][2], m[17][2]) for m in sent if m[0][1] == 40],
                         [(1, 7), (4, 7), (2, 8)])
        self.assertEqual(completed, ['result'])
        self.assertEqual(sent[-1][0][1], 70)
        self.assertEqual(sent[-1][2][2], 8)
        self.assertEqual(sent[-2][17][2], 8,
                         'current clock must follow restoration of its matching take snapshot')

    def test_transient_rounding_retries_later_and_does_not_send_invented_clock(self):
        sent, samples = [], []
        output = OutputPacer(lambda messages: sent.append(messages))
        now = [100.0]
        def supplier():
            samples.append(now[0])
            return False if len(samples) == 1 else clock(index=8, position=0)
        output.clock(8, supplier, now[0])
        output.flush(now[0])
        self.assertEqual(sent, [])
        for group in range(3):
            output.offer(snapshot(group, outcome=p.NONE), now[0])
        output.flush(now[0])
        self.assertEqual(len(sent), 3, 'rounding consumes none of the whole-frame budget')
        now[0] += .04
        output.flush(now[0])
        self.assertEqual(samples, [100.0])
        now[0] = 100.09
        output.flush(now[0])
        self.assertEqual(samples, [100.0, 100.09])
        self.assertEqual(len(sent), 4)
        self.assertEqual(sent[-1][0][1], 70)

    def test_missing_clock_consumes_no_wire_budget_and_can_be_reoffered(self):
        sent = []
        output = OutputPacer(lambda messages: sent.append(messages))
        output.clock(8, lambda: None, 100)
        output.flush(100)
        self.assertEqual(sent, [])
        for group in range(3):
            output.offer(snapshot(group, outcome=p.NONE), 100.001)
        output.flush(100.001)
        self.assertEqual(len(sent), 3)
        output.clock(8, lambda: clock(index=8), 100.1)
        output.flush(100.5)
        self.assertEqual(sent[-1][0][1], 70)

    def test_metadata_send_failure_keeps_hello_before_snapshots_and_counts_attempt_budget(self):
        attempts, delivered = [], []
        def send(messages):
            attempts.append(messages)
            if len(attempts) <= 5:
                raise OSError('injected metadata boundary failure')
            delivered.append(messages)
        output = OutputPacer(send)
        output.offer(p.encode_metadata(True), 100)
        output.offer(snapshot(), 100)
        for _ in range(6):
            output.flush(100)
        self.assertEqual(len(attempts), 5, 'ambiguous sends still consume the bounded metadata budget')
        self.assertEqual(delivered, [])
        output.flush(100.09)
        self.assertEqual([messages[0][1] for messages in delivered], [110, 111, 112, 40])
        self.assertEqual(output.send_errors, 5)

    def test_reconnect_cannot_replenish_consumed_frame_or_metadata_budget(self):
        sent = []
        output = OutputPacer(lambda messages: sent.append(messages))
        output.offer(p.encode_metadata(True), 100)
        output.offer([(0xBF, 113, 1), (0xBF, 114, 0)], 100)
        for group in range(3):
            output.offer(snapshot(group), 100)
        output.flush(100)
        self.assertEqual(len(sent), 8)
        output.clear()
        output.offer(p.encode_metadata(True), 100.001)
        output.offer(snapshot(7), 100.001)
        output.flush(100.001)
        self.assertEqual(len(sent), 8)
        output.flush(100.09)
        self.assertEqual([messages[0][1] for messages in sent[8:]], [110, 111, 112, 40])


if __name__ == '__main__':
    unittest.main()
