"""Relative meter detents, with exact outward and return thresholds."""
import unittest
import test_surface
from unittest.mock import patch
import test_device_ui as fixture
from LightpadController.device_ui import DeviceUI, meter_step
from LightpadController import protocol as p

class MeterDetentTests(unittest.TestCase):

    def test_first_step_needs_two_leds_and_has_no_initial_jump(self):
        for delta in (-2 + 1 / 128, -0.25, 0, 0.25, 2 - 1 / 128):
            self.assertEqual(meter_step(delta, 0), 0)
        self.assertEqual(meter_step(2, 0), 1)
        self.assertEqual(meter_step(-2, 0), -1)

    def test_outward_and_return_boundaries_are_symmetric(self):
        for sign in (-1, 1):
            for step in range(1, 7):
                with self.subTest(sign=sign, step=step):
                    self.assertEqual(meter_step(sign * 2 * step, sign * (step - 1)), sign * step)
                    return_at = sign * (2 * step - 0.25)
                    self.assertEqual(meter_step(return_at, sign * step), sign * step)
                    self.assertEqual(meter_step(return_at - sign / 128, sign * step), sign * (step - 1))

    def test_coalesced_movement_and_reversal_cross_multiple_steps(self):
        self.assertEqual(meter_step(10.125, 0), 5)
        self.assertEqual(meter_step(-6.125, 5), -3)
        self.assertEqual(meter_step(6.125, -3), 3)
        self.assertEqual(meter_step(0, 3), 0)
        self.assertEqual(meter_step(0, -3), 0)

    def host(self, meter=(4, 4, False)):
        with patch.object(fixture, 'DeviceUI', DeviceUI):
            host = fixture.Host()
        host.meter = meter
        return host

    def touch(self, host, kind, role, y, finger=1, x=1.5):
        host.now += 0.05
        host.ui.touch(kind, finger, x, y, role=role, view=0, group=0, drawer_open=True)

    def test_host_uses_confirmed_start_value_and_holds_return_detent(self):
        host = self.host()
        self.touch(host, 1, 2, 6.5)
        self.touch(host, 2, 2, 4.5 + 1 / 128)
        self.assertEqual(host.sent, [])
        self.touch(host, 2, 2, 4.5)
        self.assertEqual(host.sent, [(0, p.SET_NUMERATOR, 5)])
        host.finish(effect=lambda *_: setattr(host, 'meter', (5, 4, False)))
        self.touch(host, 2, 2, 4.75)
        self.assertEqual(len(host.sent), 1)
        self.touch(host, 2, 2, 4.75 + 1 / 128)
        self.assertEqual(host.sent[-1], (0, p.SET_NUMERATOR, 4))

    def test_denominator_moves_through_indices_and_keeps_fractional_drag(self):
        host = self.host()
        self.touch(host, 1, 3, 6.5, x=11.5)
        self.touch(host, 2, 3, 4.5 + 1 / 128, x=11.5)
        self.assertEqual(host.sent, [])
        self.touch(host, 2, 3, 4.5, x=11.5)
        self.assertEqual(host.sent, [(0, p.SET_DENOMINATOR, 8)])
        host.finish(effect=lambda *_: setattr(host, 'meter', (4, 8, False)))
        self.touch(host, 3, 3, 8.5, x=11.5)
        self.assertEqual(host.sent[-1], (0, p.SET_DENOMINATOR, 2))

    def test_clamping_does_not_move_the_start_origin_or_send_fake_steps(self):
        for role, meter, x in ((2, (99, 4, False), 1.5), (3, (4, 16, False), 11.5)):
            host = self.host(meter)
            self.touch(host, 1, role, 6.5, x=x)
            self.touch(host, 2, role, 2.5, x=x)
            self.assertEqual(host.sent, [])
            self.assertEqual(host.ui._aux[1].step, 2)
            self.touch(host, 2, role, 6.5, x=x)
            self.assertEqual(host.sent, [])
            self.touch(host, 3, role, 8.5, x=x)
            self.assertEqual(host.sent[-1], (0, p.SET_NUMERATOR, 98) if role == 2 else (0, p.SET_DENOMINATOR, 8))
if __name__ == '__main__':
    unittest.main()
