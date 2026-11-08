"""Atomic presentation delivery through the shared pacing boundary, without Live."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_output_pacing as pacing_tests
from LightpadController.output_pacing import OutputPacer


def presentation(revision=1, view=0):
    return [(0xBF, 20, revision), (0xBF, 21, view), (0xBF, 22, 0),
            (0xBF, 23, 1), (0xBF, 24, 2), (0xBF, 25, 3),
            (0xBF, 26, 4), (0xBF, 27, 5), (0xBF, 28, revision)]


class PresentationPacingTests(unittest.TestCase):
    def test_latest_presentation_shares_rolling_budget_with_snapshots_and_clocks(self):
        sent = []
        output = OutputPacer(lambda messages: sent.append(messages))
        first = presentation(1, 1)
        output.offer(pacing_tests.snapshot(), 100.0)
        output.clock(8, lambda: pacing_tests.clock(index=8), 100.0)
        output.presentation(first, 100.0)
        output.flush(100.0)
        self.assertEqual(len(sent), 3)
        self.assertEqual(sorted(len(frame) for frame in sent), [9, 20, 20])
        self.assertIn(first, sent)

        old = presentation(2, 2)
        latest = presentation(3, 3)
        output.presentation(old, 100.01)
        output.presentation(latest, 100.02)
        latest.clear()  # Caller storage is not retained by the queue.
        output.offer(pacing_tests.snapshot(group=1), 100.02)
        output.flush(100.089)
        self.assertEqual(len(sent), 3, 'nine CCs still consume one atomic send unit')
        output.flush(100.09)
        ui_frames = [frame for frame in sent if frame[0][:2] == (0xBF, 20)]
        self.assertEqual(ui_frames, [first, presentation(3, 3)])
        self.assertNotIn(old, sent)
        self.assertFalse(output.presentation_due(101.089))
        self.assertTrue(output.presentation_due(101.09))
        self.assertTrue(output.heartbeat_due(0, 101.09),
                        'presentation traffic cannot refresh a group snapshot heartbeat')

    def test_reconnect_drops_presentation_without_losing_protected_results(self):
        sent, completed = [], []
        output = OutputPacer(lambda messages: sent.append(messages))
        for group in range(3):
            output.offer(pacing_tests.snapshot(group=group), 100.0)
        output.flush(100.0)
        result = pacing_tests.snapshot(group=7, seq=23)
        terminal = pacing_tests.snapshot(group=7, revision=2, seq=23, view=4)
        output.offer(result, 100.01, lambda: completed.append('applied'))
        output.offer(terminal, 100.02)
        output.presentation(presentation(8, 2), 100.03)
        output.clear()
        output.flush(100.03)
        self.assertEqual(len(sent),4,'only the reserved result slot may send before the window expires')
        self.assertEqual(sent[-1],result)
        output.flush(100.09)
        self.assertEqual(sent[3:], [result, terminal])
        self.assertEqual(completed, ['applied'])
        self.assertTrue(output.presentation_due(100.09))

    def test_failed_presentation_retains_atomic_frame_and_updates_heartbeat_only_on_success(self):
        attempts, delivered = [], []

        def send(messages):
            attempts.append(messages)
            if len(attempts) <= 3:
                raise OSError('injected partial presentation send')
            delivered.append(messages)

        output = OutputPacer(send)
        frame = presentation(9, 2)
        output.presentation(frame, 100.0)
        for _ in range(4):
            output.flush(100.0)
        self.assertEqual(attempts, [frame] * 3)
        self.assertEqual(delivered, [])
        self.assertTrue(output.presentation_due(100.0))
        self.assertEqual(output.send_errors, 3)
        output.flush(100.09)
        self.assertEqual(delivered, [frame])
        self.assertFalse(output.presentation_due(101.089))
        self.assertTrue(output.presentation_due(101.09))

    def test_bound_presentation_waits_for_matching_phrase_and_take_snapshot(self):
        for initial_state, initial_take in ((2, 7), (1, 8)):
            with self.subTest(initial_state=initial_state, initial_take=initial_take):
                sent = []
                output = OutputPacer(lambda messages: sent.append(messages))
                output.offer(pacing_tests.snapshot(state=initial_state, take=initial_take), 100.0)
                output.flush(100.0)
                ui = presentation(3, 2)
                output.presentation(ui, 100.01, binding=(0, 2, 8))
                output.flush(100.01)
                self.assertEqual(len(sent), 1, 'mismatched state cannot authorize a take presentation')
                self.assertTrue(output.presentation_due(100.01))
                matching = pacing_tests.snapshot(revision=2, state=2, take=8)
                output.offer(matching, 100.02)
                output.flush(100.02)
                self.assertEqual(sent, [sent[0], matching, ui])
                self.assertFalse(output.presentation_due(100.02))

    def test_presentation_is_one_complete_frame_and_heartbeat_tracks_actual_send(self):
        sent = []
        output = OutputPacer(lambda messages: sent.append(messages))
        self.assertTrue(output.presentation_due(100.0))
        first = presentation(7, 2)
        output.presentation(first, 100.0)
        self.assertEqual(sent, [])
        output.flush(100.0)
        self.assertEqual(sent, [first])
        self.assertEqual([cc for _, cc, _ in sent[0]], list(range(20, 29)))
        self.assertEqual((sent[0][0][2], sent[0][-1][2]), (7, 7))
        self.assertFalse(output.presentation_due(100.999))
        self.assertTrue(output.presentation_due(101.0))


if __name__ == '__main__':
    unittest.main()
