#!/usr/bin/env python3
"""Compare integrated idle callbacks with the accepted v9 compiled prototype.

Curved native calls are recorded exactly, not rasterized into an invented
renderer. Pixel assets, gestures and float calculations still execute as real
Littlefoot bytecode. These checks cannot measure hardware latency or LED optics.
"""
import hashlib
from pathlib import Path
import re
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

    def test_idle_implementation_is_exact_approved_logic_with_scoped_callback_names(self):
        original = REFERENCE.read_text()
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         '2d52cd9405ae94b583b7cd6ec2836756b710636ef85f36395105389c6d6a052b')
        original = original.replace('#heapsize: 64\n', '')
        for old, new in (('initialise', 'initialiseIdle'), ('touchStart', 'idleTouchStart'),
                         ('touchMove', 'idleTouchMove'), ('touchEnd', 'idleTouchEnd'), ('repaint', 'repaintIdle')):
            original = re.sub(r'\b' + old + r'\b', new, original)
        extracted = (PAD / 'idle-animation.littlefoot').read_text().split('\n', 2)[2]
        self.assertEqual(extracted, original)
        self.assertEqual(hashlib.sha256((PAD / 'idle-statements-prototype.littlefoot').read_bytes()).hexdigest(),
                         '887dcadf7d96d0c57651ed4908a6bb2e0ad258eff79aec92676660ee20f2019a')

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
