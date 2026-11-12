"""Live-state reconciliation and recoverable local action feedback."""
import unittest
from test_device_ui import Host
from LightpadController.device_ui import DeviceUI
from LightpadController import protocol as p


class DeviceRecoveryTests(unittest.TestCase):
    def make(self, running=True):
        host = Host()
        host.running = running
        host.ui = DeviceUI(host.send, lambda group: host.fields[group],
            lambda: host.meter, lambda: host.now, transport_running=lambda: host.running)
        return host

    def down(self, host):
        for kind, y in ((1, 1.5), (2, 4.5), (3, 4.5)):
            host.ui.touch(kind, 0, 1.5, y, role=6, view=0, group=0)

    def test_stopped_recording_gesture_is_a_complete_noop(self):
        host = self.make(running=False)
        self.down(host)
        self.assertEqual(host.sent, [])
        self.assertEqual(host.ui.view, 0)
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_failed_begin_flashes_locally_and_next_gesture_can_retry(self):
        host = self.make()
        self.down(host)
        self.assertEqual(host.sent, [(0, p.BEGIN, 0)])
        host.finish(outcome=p.NOT_READY)
        self.assertEqual(host.ui.view, 0)
        self.assertTrue(host.ui.presentation()[4] & p.ACTION_FLASH)
        self.assertEqual(host.ui.presentation()[1], 0)
        self.assertFalse(host.ui._inflight)
        self.down(host)
        self.assertEqual(host.sent, [(0, p.BEGIN, 0), (0, p.BEGIN, 0)])
        host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 2, 107: 9}))
        self.assertEqual((host.ui.view, host.ui.active_take), (2, 9))
        host.now += .26
        self.assertFalse(host.ui.presentation()[4] & p.ACTION_FLASH)

    def test_adopting_or_switching_live_recording_never_dispatches_a_command(self):
        host = self.make()
        host.fields[3].update({106: 2, 107: 40})
        host.ui.reconcile_recording((3, 40))
        self.assertEqual((host.ui.view, host.ui.recording_group, host.ui.active_take), (2, 3, 40))
        host.fields[6].update({106: 2, 107: 41})
        host.ui.reconcile_recording((6, 41))
        self.assertEqual((host.ui.recording_group, host.ui.active_take), (6, 41))
        self.assertEqual(host.sent, [])
        host.fields[6].update({106: 0, 107: 0})
        host.ui.reconcile_recording(None)
        self.assertEqual(host.ui.view, 0)
        self.assertEqual(host.sent, [])

    def test_future_or_stale_recording_binding_is_not_adopted(self):
        host = self.make()
        host.fields[3].update({106: 2, 107: 40})
        host.ui.reconcile_recording((3, 39))
        self.assertEqual(host.ui.view, 0)
        self.assertEqual(host.sent, [])
