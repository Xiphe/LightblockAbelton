"""Helper readiness framing and recovery, without Live or MIDI endpoints."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import health


class HealthTests(unittest.TestCase):
    def test_ready_and_busy_are_independent_and_frame_is_closed_atomically(self):
        self.assertEqual(health.encode_health(127, health.READY, flags=health.RECORDING_BUSY),
                         [(191, 114, 127), (191, 115, 5), (191, 116, 0),
                          (191, 117, 1), (191, 118, 127)])
        for values in ((True, 5, 0, 0), (128, 5, 0, 0), (0, 6, 0, 0),
                       (0, 5, 11, 0), (0, 5, 0, 4), (0, 5, 0, -1)):
            with self.assertRaises(ValueError):
                health.encode_health(*values)

    def test_heartbeat_does_not_depend_on_pad_hello_or_transport_playing(self):
        now, sent = [0.], []
        publisher = health.HealthPublisher(sent.append, lambda: now[0])
        self.assertTrue(publisher.publish(health.NO_GROUPS, health.NO_REMOTE_GROUPS))
        now[0] = .49
        self.assertFalse(publisher.publish(health.NO_GROUPS, health.NO_REMOTE_GROUPS))
        now[0] = .5
        self.assertTrue(publisher.publish(health.NO_GROUPS, health.NO_REMOTE_GROUPS))
        self.assertEqual([frame[0][2] for frame in sent], [0, 1])

    def test_rapid_changes_coalesce_without_flooding_or_hiding_latest_state(self):
        now, sent = [10.], []
        publisher = health.HealthPublisher(sent.append, lambda: now[0])
        publisher.publish(health.CONNECTING, health.LOADING)
        now[0] += .04
        self.assertFalse(publisher.publish(health.FAILURE, health.HOST_FAILURE))
        now[0] += .085
        self.assertTrue(publisher.publish(health.READY))
        self.assertEqual([frame[1][2] for frame in sent], [health.CONNECTING, health.READY])

    def test_partial_send_failure_retries_a_whole_frame_and_does_not_commit(self):
        now, attempts = [0.], []
        def send(messages):
            attempts.append(messages)
            if len(attempts) == 1:
                raise OSError("injected failure after an arbitrary CC")
        publisher = health.HealthPublisher(send, lambda: now[0])
        self.assertFalse(publisher.publish(health.READY))
        self.assertFalse(publisher.publish(health.READY, force=True))
        now[0] = .125
        self.assertTrue(publisher.publish(health.READY))
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(publisher.send_errors, 1)
