#!/usr/bin/env python3
"""Compare integrated idle callbacks with the accepted v9 compiled prototype.

Curved native calls are recorded exactly, not rasterized into an invented
renderer. Pixel assets, gestures and float calculations still execute as real
Littlefoot bytecode. These checks cannot measure hardware latency or LED optics.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from littlefoot_harness import Harness

ROOT = Path(__file__).resolve().parents[2]
PAD = ROOT / 'lightpad'
REFERENCE = Path(__file__).parent / 'fixtures/idle-fractal-prototype.littlefoot'
MAGIC, HEADER = 0x53540000, 0x23a8


class StatusIdleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('blocksctl')
        if compiler is None: raise RuntimeError('Offline blocksctl compiler is required')
        cls.programs = {}
        with tempfile.TemporaryDirectory(prefix='status-idle-compare-') as directory:
            for name in ('status', 'idle-fractal-prototype'):
                output = Path(directory) / (name + '.bin')
                source = PAD / (name + '.littlefoot')
                if name == 'idle-fractal-prototype':
                    source = Path(directory) / REFERENCE.name
                    shutil.copy2(REFERENCE, source)
                    shutil.copy2(PAD / 'idle-statements-prototype.littlefoot',
                                 Path(directory) / 'idle-statements-prototype.littlefoot')
                subprocess.run([compiler, 'compile', str(source),
                                '-o', str(output)], check=True, capture_output=True)
                cls.programs[name] = output.read_bytes()

    def pair(self):
        pair = []
        for name in ('status', 'idle-fractal-prototype'):
            vm = Harness(self.programs[name]); vm.circles = []
            vm._register('blendCircle/vifffb', lambda *args, vm=vm: vm.circles.append(args))
            vm.call('initialise/v'); pair.append(vm)
        pair[0].call('handleMessage/viii', MAGIC, 0, HEADER)
        for vm in pair: vm.logs.clear()
        return pair

    def paint(self, pair, delta=40):
        for vm in pair:
            vm.time_ms += delta
            vm.circles.clear()
        # A real helper refreshes status; the animation must not depend on it
        # for its local motion or interpretation of a held contact.
        pair[0].call('handleMessage/viii', MAGIC, 0, HEADER)
        for vm in pair: vm.call('repaint/v')
        self.assertEqual(pair[0].circles, pair[1].circles)
        self.assertEqual(pair[0].leds, pair[1].leds)
        self.assertEqual(pair[0].logs, pair[1].logs)
        return 'animation' if len(pair[0].circles) == 16 else 'statement'

    def touch(self, pair, event, finger, x, y, z=.4):
        for vm in pair: vm.call(event + '/viffff', finger, x, y, z, 0.)

    def down(self, pair, a, b):
        self.touch(pair, 'touchStart', 0, *a)
        self.touch(pair, 'touchStart', 1, *b)

    def up(self, pair, a, b):
        self.touch(pair, 'touchEnd', 0, *a, z=0.)
        self.touch(pair, 'touchEnd', 1, *b, z=0.)

    def test_approved_reference_and_statement_assets_remain_unchanged(self):
        original = REFERENCE.read_text()
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         '2d52cd9405ae94b583b7cd6ec2836756b710636ef85f36395105389c6d6a052b')
        self.assertEqual(hashlib.sha256((PAD / 'idle-statements-prototype.littlefoot').read_bytes()).hexdigest(),
                         '887dcadf7d96d0c57651ed4908a6bb2e0ad258eff79aec92676660ee20f2019a')

    def status(self, random_values=(), now=1000):
        vm = Harness(self.programs['status']); vm.circles = []
        vm._register('blendCircle/vifffb', lambda *args: vm.circles.append(args))
        values = iter(random_values); vm.random_requests = []
        def random(upper):
            vm.random_requests.append(upper)
            value = next(values, 0)
            self.assertTrue(0 <= value < upper)
            return value
        vm._register('getRandomInt/ii', random)
        vm.time_ms = now; vm.call('initialise/v')
        return vm

    def status_paint(self, vm, now):
        vm.time_ms = now; vm.circles.clear(); vm.call('repaint/v')
        return 'animation' if len(vm.circles) == 16 else 'statement'

    def test_automatic_intervals_duration_and_screens_use_the_requested_ranges(self):
        for screen, delay, duration, next_delay in ((0, 7000, 500, 15000),
                                                   (1, 11234, 1057, 8321),
                                                   (2, 15000, 1500, 7000)):
            vm = self.status((delay-7000, screen, duration-500, next_delay-7000))
            start = 1000 + delay
            self.assertEqual(self.status_paint(vm, start-1), 'animation')
            self.assertEqual(self.status_paint(vm, start), 'statement')
            shown = [row[:] for row in vm.leds]
            vm.call('drawIdleStatement/vi', screen)
            self.assertEqual(vm.leds, shown)
            self.assertEqual(self.status_paint(vm, start+duration-1), 'statement')
            self.assertEqual(self.status_paint(vm, start+duration), 'animation')
            self.assertEqual(self.status_paint(vm, start+next_delay-1), 'animation')
            self.assertEqual(self.status_paint(vm, start+next_delay), 'statement')
            self.assertEqual(vm.random_requests[:4], [8001, 3, 1001, 8001])
            self.assertFalse(any(message[1] == 107 for message in vm.midi))
            self.assertLessEqual(vm.code_size + vm.heap_size, 7200)

    def test_touch_cancels_automatic_statement_and_postpones_until_after_release(self):
        vm = self.status((0, 2, 1000, 0))
        self.assertEqual(self.status_paint(vm, 8000), 'statement')
        self.touch([vm], 'touchStart', 4, 1., 1.)
        self.assertEqual(self.status_paint(vm, 8000), 'animation')
        self.assertEqual(self.status_paint(vm, 28000), 'animation')
        self.touch([vm], 'touchEnd', 4, 1., 1., 0.)
        self.assertEqual(self.status_paint(vm, 34999), 'animation')
        self.assertEqual(self.status_paint(vm, 35000), 'statement')

    def test_manual_statement_mode_keeps_priority_over_automatic_timer(self):
        vm = self.status(); a, b = (.1, .1), (1.8, 1.8)
        self.down([vm], a, b)
        self.assertEqual(self.status_paint(vm, 3000), 'statement')
        self.up([vm], a, b)
        manual = [row[:] for row in vm.leds]
        self.assertEqual(self.status_paint(vm, 30000), 'statement')
        self.assertEqual(vm.leds, manual)
        self.down([vm], a, b)
        self.assertEqual(self.status_paint(vm, 32000), 'animation')
        self.up([vm], a, b)
        self.assertEqual(self.status_paint(vm, 38999), 'animation')
        self.assertEqual(self.status_paint(vm, 39000), 'statement')

    def test_fault_icons_override_automatic_flash_and_idle_reentry_restarts_timer(self):
        for value in (2, 3, 4):
            vm = self.status((0, 1, 1000, 0))
            self.assertEqual(self.status_paint(vm, 8000), 'statement')
            vm.call('handleMessage/viii', MAGIC | value, 0, HEADER)
            self.status_paint(vm, 8000)
            fresh = self.status(); fresh.time_ms = 8000
            fresh.call('handleMessage/viii', MAGIC | value, 0, HEADER)
            self.status_paint(fresh, 8000)
            self.assertEqual(vm.leds, fresh.leds)
            vm.call('handleMessage/viii', MAGIC, 0, HEADER)
            self.assertEqual(self.status_paint(vm, 8000), 'animation')
            self.assertEqual(self.status_paint(vm, 14999), 'animation')
            self.assertEqual(self.status_paint(vm, 15000), 'statement')

    def test_automatic_timer_crosses_signed_millisecond_wrap(self):
        now = (1 << 31) - 3000; vm = self.status(now=now)
        self.assertEqual(self.status_paint(vm, now+6999), 'animation')
        self.assertEqual(self.status_paint(vm, now+7000), 'statement')
        self.assertEqual(self.status_paint(vm, now+7500), 'animation')

    def test_touch_pressure_join_release_all_patterns_match_accepted_bytecode(self):
        pair = self.pair()
        self.assertEqual(self.paint(pair), 'animation')
        self.touch(pair, 'touchStart', 6, .2, 1.2, .9)
        for _ in range(26): self.paint(pair, 80)
        self.touch(pair, 'touchStart', 1, 1.5, .4, .7)
        for _ in range(26): self.paint(pair, 80)
        self.touch(pair, 'touchMove', 6, .8, 1.7, 1.)
        self.paint(pair)
        self.touch(pair, 'touchStart', 4, .8, .8)
        for _ in range(26): self.paint(pair, 80)
        self.touch(pair, 'touchEnd', 4, .8, .8, 0.)
        self.touch(pair, 'touchStart', 4, .8, .8)
        for _ in range(26): self.paint(pair, 80)
        self.touch(pair, 'touchStart', 11, 1., 1.)
        for _ in range(26): self.paint(pair, 80)
        for finger, x, y in ((6, .8, 1.7), (1, 1.5, .4), (4, .8, .8), (11, 1., 1.)):
            self.touch(pair, 'touchEnd', finger, x, y, 0.)
        for _ in range(26): self.paint(pair, 80)
        self.assertTrue(all(message[0] == 191 and message[1] in (106, 107, 108, 110)
                            for message in pair[0].midi))
        self.assertFalse(any(message[1] == 107 for message in pair[0].midi), 'idle interaction never requests diagnostics')
        self.assertFalse(pair[1].midi)

    def test_both_diagonals_preview_latch_cycle_and_close_exactly_as_accepted(self):
        for a, b in (((.1, .1), (1.8, 1.8)), ((1.8, .1), (.1, 1.8))):
            pair = self.pair(); self.down(pair, a, b)
            self.assertEqual(self.paint(pair, 0), 'statement')
            self.paint(pair, 800); self.up(pair, a, b)
            self.assertEqual(self.paint(pair), 'animation')
            self.down(pair, a, b); self.paint(pair, 2000); self.up(pair, a, b)
            self.assertEqual(self.paint(pair), 'statement')
            for screen in (1, 2, 3, 0):
                self.touch(pair, 'touchStart', 2, 1., 1.)
                self.paint(pair, 80)
                self.touch(pair, 'touchEnd', 2, 1., 1., 0.)
                self.assertEqual(self.paint(pair), 'statement')
                self.assertEqual(pair[0].logs[-1], 9880 + screen)
            self.down(pair, a, b)
            self.assertEqual(self.paint(pair, 0), 'animation')
            self.paint(pair, 2000); self.up(pair, a, b)
            self.assertEqual(self.paint(pair), 'animation')
            self.assertFalse(any(message[1] == 107 for message in pair[0].midi))

    def test_edge_movement_and_intermediate_interior_cancellation_match_reference(self):
        pair = self.pair(); a, b = (.1, .1), (1.8, 1.8)
        self.down(pair, a, b)
        self.touch(pair, 'touchMove', 0, .1, 1.)
        self.touch(pair, 'touchMove', 1, 1., 1.8)
        self.assertEqual(self.paint(pair, 2000), 'statement')
        self.up(pair, (.1, 1.), (1., 1.8)); self.paint(pair)
        pair = self.pair(); self.down(pair, a, b); self.paint(pair, 700)
        self.touch(pair, 'touchMove', 1, 1., 1.)
        self.touch(pair, 'touchMove', 1, *b)
        self.assertEqual(self.paint(pair, 2000), 'animation')
        self.up(pair, a, b); self.paint(pair)
        self.assertNotIn(9871, pair[0].logs)

    def test_hold_threshold_release_between_frames_matches_reference(self):
        pair = self.pair(); a, b = (.1, .1), (1.8, 1.8)
        self.down(pair, a, b)
        for vm in pair: vm.time_ms += 2000
        self.up(pair, a, b)
        self.assertEqual(self.paint(pair), 'statement')
        self.assertIn(9871, pair[0].logs)


if __name__ == '__main__': unittest.main(verbosity=2)
