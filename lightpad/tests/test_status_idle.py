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

    def status(self, now=1000):
        vm = Harness(self.programs['status']); vm.circles = []
        vm._register('blendCircle/vifffb', lambda *args: vm.circles.append(args))
        vm.time_ms = now; vm.call('initialise/v')
        return vm

    def status_paint(self, vm, now):
        vm.time_ms = now; vm.circles.clear(); vm.call('repaint/v')
        return 'animation' if len(vm.circles) == 16 else 'statement'

    def asset(self, screen):
        vm = self.status(); vm.call('drawIdleStatement/vi', screen)
        return [row[:] for row in vm.leds]

    def test_idle_sequence_keeps_tunnel_45s_statement_15s_then_pride_forever(self):
        vm = self.status(); afd, pride = self.asset(0), self.asset(1)
        self.assertEqual(self.status_paint(vm, 45999), 'animation')
        self.assertTrue(all(pixel == 0x010107 for row in vm.leds for pixel in row))
        self.assertEqual(self.status_paint(vm, 46000), 'animation')
        self.assertTrue(all(pixel == 0x010107 for row in vm.leds for pixel in row))
        self.assertEqual(self.status_paint(vm, 48000), 'statement')
        self.assertEqual(vm.leds, afd)
        self.status_paint(vm, 62999); self.assertEqual(vm.leds, afd)
        self.status_paint(vm, 63000); self.assertEqual(vm.leds, afd)
        self.status_paint(vm, 65000); self.assertEqual(vm.leds, pride)
        self.status_paint(vm, 10000000); self.assertEqual(vm.leds, pride)
        self.assertFalse(any(message[1] == 107 for message in vm.midi))
        self.assertLessEqual(vm.code_size + vm.heap_size, 7200)

    def test_tunnel_to_statement_crossfade_blends_every_pixel_exactly_once(self):
        vm = self.status(); afd = self.asset(0)
        # Curved rendering is a Native fake. Inject an arbitrary nonuniform
        # tunnel image to test actual compiled crossfade composition, without
        # claiming to reproduce firmware circle rasterization or LED rounding.
        tunnel = [[((x*13+21) << 16) | ((y*11+17) << 8) | (x*5+y*7+31)
                   for x in range(15)] for y in range(15)]
        def curved(*args):
            if not vm.circles: vm.leds = [row[:] for row in tunnel]
            vm.circles.append(args)
        vm._register('blendCircle/vifffb', curved)
        self.status_paint(vm, 47000)
        expected = [[Harness.blend(base, (127 << 24) | target) & 0xffffff
                     for base, target in zip(a, b)] for a, b in zip(tunnel, afd)]
        self.assertEqual(vm.leds, expected)
        self.assertNotEqual(vm.leds, afd)
        self.assertNotEqual(vm.leds, tunnel)
        coverage = [[0]*15 for _ in range(15)]
        for overlay in vm.overlays:
            self.assertEqual(overlay['alpha'], 127)
            x,y,w,h = overlay['rect']
            for yy in range(y,y+h):
                for xx in range(x,x+w): coverage[yy][xx] += 1
        self.assertTrue(all(count == 1 for row in coverage for count in row))

    def test_statement_to_pride_crossfade_mixes_both_full_images(self):
        vm = self.status(); afd, pride = self.asset(0), self.asset(1)
        self.status_paint(vm, 64000)
        expected = [[Harness.blend(base, (127 << 24) | target) & 0xffffff
                     for base,target in zip(a,b)] for a,b in zip(afd,pride)]
        self.assertEqual(vm.leds, expected)
        self.assertNotEqual(vm.leds, afd)
        self.assertNotEqual(vm.leds, pride)
        self.assertEqual(len(vm.overlays), 6)
        self.assertTrue(all(overlay['alpha'] == 127 for overlay in vm.overlays))

    def test_pride_tap_restarts_the_complete_cycle_from_last_release(self):
        vm = self.status(); self.status_paint(vm, 65000)
        self.assertEqual(vm.leds, self.asset(1))
        self.touch([vm], 'touchStart', 4, 1., 1.)
        self.assertEqual(self.status_paint(vm, 65000), 'animation')
        vm.time_ms = 65020; self.touch([vm], 'touchEnd', 4, 1., 1., 0.)
        self.assertEqual(self.status_paint(vm, 110019), 'animation')
        self.status_paint(vm, 112020); self.assertEqual(vm.leds, self.asset(0))
        self.status_paint(vm, 129020); self.assertEqual(vm.leds, self.asset(1))

    def test_held_touch_keeps_animation_and_restarts_45s_on_release(self):
        vm = self.status(); self.touch([vm], 'touchStart', 4, 1., 1.)
        self.assertEqual(self.status_paint(vm, 100000), 'animation')
        self.touch([vm], 'touchEnd', 4, 1., 1., 0.)
        self.assertEqual(self.status_paint(vm, 144999), 'animation')
        self.status_paint(vm, 147000); self.assertEqual(vm.leds, self.asset(0))

    def test_manual_statement_mode_keeps_priority_over_sequence(self):
        vm = self.status(); a, b = (.1, .1), (1.8, 1.8)
        self.down([vm], a, b)
        self.assertEqual(self.status_paint(vm, 3000), 'statement')
        self.up([vm], a, b)
        manual = [row[:] for row in vm.leds]
        self.status_paint(vm, 200000); self.assertEqual(vm.leds, manual)
        self.down([vm], a, b)
        self.assertEqual(self.status_paint(vm, 202000), 'animation')
        self.up([vm], a, b)
        self.assertEqual(self.status_paint(vm, 246999), 'animation')
        self.status_paint(vm, 249000); self.assertEqual(vm.leds, self.asset(0))

    def test_fault_icons_override_pride_and_idle_reentry_restarts_sequence(self):
        for value in (2, 3, 4):
            vm = self.status(); self.status_paint(vm, 65000)
            vm.call('handleMessage/viii', MAGIC | value, 0, HEADER)
            self.status_paint(vm, 65000)
            fresh = self.status(); fresh.time_ms = 65000
            fresh.call('handleMessage/viii', MAGIC | value, 0, HEADER)
            self.status_paint(fresh, 65000)
            self.assertEqual(vm.leds, fresh.leds)
            vm.call('handleMessage/viii', MAGIC, 0, HEADER)
            self.assertEqual(self.status_paint(vm, 65000), 'animation')
            self.assertEqual(self.status_paint(vm, 109999), 'animation')
            self.status_paint(vm, 112000); self.assertEqual(vm.leds, self.asset(0))

    def test_sequence_crosses_millisecond_wrap_and_terminal_pride_stays_latched(self):
        now = (1 << 31) - 30000; vm = self.status(now=now)
        self.assertEqual(self.status_paint(vm, now+44999), 'animation')
        self.status_paint(vm, now+47000); self.assertEqual(vm.leds, self.asset(0))
        self.status_paint(vm, now+64000); self.assertEqual(vm.leds, self.asset(1))
        self.status_paint(vm, now+(1 << 31)+100000); self.assertEqual(vm.leds, self.asset(1))

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
