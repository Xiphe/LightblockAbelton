"""A disabled recording press cannot become a later action on release."""

import unittest

from test_device_ui import Host
from LightpadController import protocol as p


class RecordTouchLifecycleTests(unittest.TestCase):
    def test_disabled_accept_press_stays_inert_when_recording_starts(self):
        for y in (2.5, 9.5):
            with self.subTest(y=y):
                host = Host()
                host.begin()
                host.fields[0][106] = 1
                host.touch(1, x=9.5, y=y)
                host.fields[0][106] = 2
                host.touch(3, x=9.5, y=y, after=.05)
                self.assertEqual(host.sent, [(0, p.BEGIN, 0)])

    def test_record_press_does_not_carry_into_a_new_take(self):
        for x, y in ((2.5, 2.5), (9.5, 2.5), (2.5, 9.5), (9.5, 9.5)):
            with self.subTest(x=x, y=y):
                host = Host()
                host.begin()
                host.touch(1, x=x, y=y)
                host.fields[0][107] = 8
                host.ui.active_take = 8
                host.touch(3, x=x, y=y, after=.05)
                self.assertEqual(host.sent, [(0, p.BEGIN, 0)])

    def test_disabled_press_during_an_inflight_action_stays_inert(self):
        host = Host()
        host.begin()
        host.tap(x=2.5, y=9.5)
        self.assertEqual(host.sent[-1], (0, p.RETRY, 0))
        host.touch(1, x=9.5, y=2.5)
        host.finish(outcome=p.OUTCOME_QUEUED,
                    effect=lambda group, op, arg: host.fields[group].update({106: 2, 107: 8}))
        host.touch(3, x=9.5, y=2.5, after=.05)
        self.assertEqual(len(host.sent), 2)

    def test_cancel_and_retry_remain_available_while_finish_is_pending(self):
        for y, opcode in ((2.5, p.ABORT), (9.5, p.RETRY)):
            with self.subTest(opcode=opcode):
                host = Host()
                host.begin()
                host.tap(x=9.5, y=2.5)
                host.touch(1, x=2.5, y=y)
                host.finish(effect=lambda group, op, arg: host.fields[group].update({106: 3}))
                host.touch(3, x=2.5, y=y, after=.05)
                self.assertEqual(host.sent[-1], (0, opcode, 0))


if __name__ == '__main__':
    unittest.main()
