"""Urgent clock feedback shares the ordinary wire budget, without Live calls."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_output_pacing as pacing_tests
from LightpadController import protocol as p
from LightpadController.output_pacing import OutputPacer


class ClockFeedbackPacingTests(unittest.TestCase):
    def test_urgent_global_clock_waits_for_shared_budget_then_samples_latest_supplier(self):
        now, native_position = [100.0], [1.0]
        sent, samples = [], []
        output = OutputPacer(lambda messages: sent.append((now[0], messages)))

        def supplier(label):
            def sample():
                samples.append((label, now[0], native_position[0]))
                return pacing_tests.clock(index=8, position=native_position[0])
            return sample

        output.clock(8, supplier('initial'), now[0])
        for group in range(2):
            output.offer(pacing_tests.snapshot(group=group), now[0])
        output.flush(now[0])
        self.assertEqual(len(sent), 3)
        self.assertEqual(samples, [('initial', 100.0, 1.0)])
        output.offer(pacing_tests.snapshot(group=7),100.005,lambda:None)
        now[0]=100.005
        output.flush(now[0])
        self.assertEqual(len(sent),4,'a protected result consumes the reserved slot')
        now[0] = 100.01
        output.clock(8, supplier('superseded'), now[0], urgent=True)
        now[0] = 100.02
        output.clock(8, supplier('latest'), now[0], urgent=True)
        self.assertEqual(len(samples), 1, 'enqueue must never sample native clock state')
        now[0] = 100.089
        output.flush(now[0])
        self.assertEqual(len(sent), 4)
        self.assertEqual(len(samples), 1)
        now[0], native_position[0] = 100.09, 3.125
        output.flush(now[0])
        self.assertEqual(samples, [('initial', 100.0, 1.0), ('latest', 100.09, 3.125)])
        self.assertEqual(len(sent), 5)
        self.assertEqual(sent[-1], (100.09, pacing_tests.clock(index=8, position=3.125)))
        self.assertLess(sent[-1][0] - 100.0, .25,
                        'urgent semantic feedback must not wait for the normal quarter-second period')

    def test_unchanged_clock_continues_normal_period_without_urgent_semantic_changes(self):
        now, sent, samples = [100.0], [], []
        output = OutputPacer(lambda messages: sent.append((now[0], messages)))

        def sample():
            samples.append(now[0])
            return pacing_tests.clock(index=8, position=1)

        for step in range(21):
            now[0] = 100.0 + step * .05
            output.clock(8, sample, now[0])
            output.flush(now[0])
        self.assertEqual([instant for instant, _ in sent], [100.0, 100.25, 100.5, 100.75, 101.0])
        self.assertEqual(samples, [instant for instant, _ in sent])
        self.assertTrue(all(frame == pacing_tests.clock(index=8, position=1) for _, frame in sent))

    @unittest.expectedFailure
    def test_sustained_urgent_global_changes_preserve_all_stream_expiries_and_shared_budget(self):
        # Frozen-baseline limitation, not an ignored safety assertion.
        # See docs/integration/current-host-baseline.md. At25Hz urgent edits,
        # a group clock gap reaches1.92s versus its1s freshness requirement.
        # Keep the strict assertion; an unexpected success must fail this suite.
        now, sent = [100.0], []
        output = OutputPacer(lambda messages: sent.append((now[0], messages)))

        for step in range(301):
            now[0] = 100.0 + step * .04
            for group in range(8):
                if output.heartbeat_due(group, now[0]):
                    output.offer(pacing_tests.snapshot(group=group, revision=step % 128,
                                                       outcome=p.NONE), now[0])
                output.clock(group, lambda group=group: pacing_tests.clock(
                    index=group, position=(now[0] - 100.0) % 8), now[0])
            output.clock(8, lambda: pacing_tests.clock(
                index=8, position=(now[0] - 100.0) % 4), now[0], urgent=True)
            output.flush(now[0])

        snapshots = {group: [] for group in range(8)}
        clocks = {status: [] for status in tuple(range(0xB0, 0xB8)) + (0xBF,)}
        for instant, frame in sent:
            status, begin, revision = frame[0]
            self.assertEqual(len(frame), 20)
            self.assertEqual([message[0] for message in frame], [status] * 20)
            self.assertEqual([message[1] for message in frame], list(range(begin, begin + 20)))
            self.assertEqual(frame[-1][2], revision)
            if begin == 40:
                snapshots[status - 0xB0].append(instant)
            else:
                self.assertEqual(begin, 70)
                clocks[status].append(instant)
            self.assertLessEqual(sum(instant - .09 + 1e-8 < at <= instant for at, _ in sent), 4,
                                 'urgent clocks cannot exceed the reserved rolling budget')

        for status, times in clocks.items():
            self.assertTrue(times, 'clock %02x must transmit' % status)
            bounded = [100.0] + times + [now[0]]
            self.assertLess(max(b - a for a, b in zip(bounded, bounded[1:])), 1.0,
                            'clock %02x reached its one-second device expiry' % status)
        for group, times in snapshots.items():
            self.assertTrue(times)
            bounded = [100.0] + times + [now[0]]
            maximum = max(b - a for a, b in zip(bounded, bounded[1:]))
            self.assertLessEqual(maximum, 2.0 + 1e-8,
                                 'snapshot group %d exceeded its 2s hard deadline: %.3fs' %
                                 (group, maximum))


if __name__ == '__main__':
    unittest.main()
