"""Actual UI-to-wire feedback survives output congestion and never blocks retry."""
import unittest

from test_device_ui import Host
from test_output_pacing import snapshot, clock
from LightpadController import protocol as p
from LightpadController.device_ui import DeviceUI
from LightpadController.output_pacing import OutputPacer


class FailureFlashDeliveryTests(unittest.TestCase):
    def make(self, send=None):
        host = Host()
        host.now = 100.
        host.ui = DeviceUI(host.send, lambda group: host.fields[group], lambda: host.meter, lambda: host.now)
        sent = []
        output = OutputPacer(send or (lambda frame: sent.append((host.now, list(frame)))))
        return host, output, sent

    def publish(self, host, output):
        payload = host.ui.presentation()
        output.presentation(p.encode_presentation(1, payload), host.now,
                            on_sent=host.ui.presentation_receipt(payload))
        output.flush(host.now)

    def test_first_flash_survives_backpressure_then_has_a_full_post_send_interval(self):
        host, output, sent = self.make()
        for group in range(3): output.offer(snapshot(group), host.now)
        output.clock(8, lambda: clock(8), host.now, urgent=True)
        output.flush(host.now)
        host.now = 100.01
        host.ui._flash(0)
        for group in range(8): output.offer(snapshot(group, seq=20+group), host.now, lambda: None)
        for step in range(4):
            host.now = 100.01+step*.1
            self.publish(host, output)
        frames = [(at, frame) for at, frame in sent if frame[0][:2] == (191, 20)]
        self.assertEqual(len(frames), 1)
        self.assertGreater(frames[0][0]-100.01, .25, 'the old host-only interval would have expired')
        self.assertTrue(frames[0][1][5][2] & p.ACTION_FLASH)
        delivered = frames[0][0]
        host.now = delivered+.249
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH)
        host.now = delivered+.25
        self.publish(host, output)
        self.assertFalse(sent[-1][1][5][2] & p.ACTION_FLASH)
        self.assertEqual(host.ui.view, 0)
        for instant, _ in sent:
            self.assertLessEqual(sum(instant-.09+1e-8 < at <= instant for at, _ in sent), 4)

    def test_ambiguous_send_failure_does_not_start_the_flash_hold(self):
        attempts = []
        def send(frame):
            attempts.append(frame)
            if len(attempts) == 1: raise OSError('injected send boundary failure')
        host, output, _ = self.make(send)
        host.ui._flash(0)
        self.publish(host, output)
        host.now += .5
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH)
        self.publish(host, output)
        host.now += .249
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH)
        host.now += .002
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_same_flash_heartbeat_receipt_does_not_extend_the_hold(self):
        host, output, _ = self.make()
        host.ui._flash(0); self.publish(host, output)
        host.now += .2
        self.publish(host, output)
        host.now += .051
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_adopted_recording_without_selected_group_gets_a_delivery_receipt(self):
        host, output, _ = self.make()
        host.fields[0].update({106: 2, 107: 42})
        host.ui.reconcile_recording((0, 42))
        self.assertIsNone(host.ui.selected_group)
        host.ui._flash(0)
        self.publish(host, output)
        host.now += .251
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_rejected_begin_binds_flash_after_returning_to_overview(self):
        host, output, sent = self.make()
        host.ui._queue_take(0)
        host.finish(outcome=p.NOT_READY)
        self.assertEqual(host.ui.view, 0)
        self.publish(host, output)
        self.assertTrue(sent[-1][1][5][2] & p.ACTION_FLASH)
        host.now += .251
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_late_receipt_after_peer_reset_cannot_reactivate_feedback(self):
        host, _, _ = self.make()
        host.ui._flash(0)
        receipt = host.ui.presentation_receipt(host.ui.presentation())
        host.ui.reset(); receipt()
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)
        host.ui._flash(0); host.now += 1.
        receipt()
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH, 'old receipt cannot acknowledge a new generation')

    def test_old_receipt_cannot_acknowledge_a_new_failure_in_the_same_view(self):
        host, _, _ = self.make()
        host.ui._flash(0)
        receipt = host.ui.presentation_receipt(host.ui.presentation())
        host.ui._flash(0); receipt(); host.now += 1.
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_view_or_take_replacement_discards_pending_feedback(self):
        for change in ('mixer', 'take'):
            host, _, _ = self.make()
            host.ui._flash(0)
            receipt = host.ui.presentation_receipt(host.ui.presentation())
            if change == 'mixer':
                host.ui.view, host.ui.selected_group = 1, 0
            else:
                host.fields[0].update({106: 2, 107: 42})
            receipt()
            self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH, change)

    def test_successful_retry_supersedes_queued_flash_without_delaying_the_gesture(self):
        host, output, sent = self.make()
        host.ui._flash(0)
        payload = host.ui.presentation()
        output.presentation(p.encode_presentation(1, payload), host.now,
                            on_sent=host.ui.presentation_receipt(payload))
        host.ui._queue_take(0)
        self.assertEqual(host.sent, [(0, p.BEGIN, 0)])
        host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 2, 107: 9}))
        self.publish(host, output)
        self.assertEqual((host.ui.view, host.ui.active_take), (2, 9))
        self.assertFalse(any(frame[5][2] & p.ACTION_FLASH for at, frame in sent))

    def test_failure_from_another_group_does_not_flash_the_current_mixer(self):
        host, _, _ = self.make()
        host.ui.view, host.ui.selected_group = 1, 3
        host.ui._result(0, p.SET_VOLUME, p.NOT_READY)
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)
        self.assertEqual((host.ui.view, host.ui.selected_group), (1, 3))

    def test_a_receipt_for_a_different_group_payload_does_not_acknowledge_current_flash(self):
        host, _, _ = self.make()
        host.ui._flash(0); old = host.ui.presentation()
        host.ui._flash(3)
        self.assertIsNone(host.ui.presentation_receipt(old))


if __name__ == '__main__': unittest.main()
