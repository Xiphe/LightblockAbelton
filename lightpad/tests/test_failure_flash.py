#!/usr/bin/env python3
"""Compiled-device group failures flash in-place, without notification traffic."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_current_device as device

ORANGE = 0xff9118
PERIMETER = ([(x, 0) for x in range(15)] + [(14, y) for y in range(1, 15)]
             + [(x, 14) for x in range(13, -1, -1)] + [(0, y) for y in range(13, 0, -1)])


def ready():
    vm = device.ready('main')
    device.clock(vm, running=False, revision=1)
    vm.call('repaint/v')
    return vm


def show(vm, mode=0, group=0, flags=0, revision=1):
    device.presentation(vm, body=[mode, group, group if mode == 2 else 127,
                                 7 if mode == 2 else 0, flags, 0, 0], revision=revision)
    vm.call('repaint/v')


class FailureFlashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'main' not in device.PROGRAMS:
            with tempfile.TemporaryDirectory(prefix='lightpad-failure-flash-') as directory:
                binary = Path(directory) / 'main.bin'
                subprocess.run(['blocksctl', 'compile', str(device.ROOT / 'lightpad/main.littlefoot'),
                                '-o', str(binary)], check=True, capture_output=True)
                device.PROGRAMS['main'] = binary.read_bytes()

    def test_overview_flash_only_changes_the_failed_tile_then_clears_from_host(self):
        vm = ready(); before = [row[:] for row in vm.leds]
        show(vm, group=7, flags=16)
        for y in range(15):
            for x in range(15):
                self.assertEqual(vm.leds[y][x], ORANGE if 12 <= x <= 14 and 4 <= y <= 6 else before[y][x])
        self.assertEqual(vm.heap_bytes(0, 1), bytes([2]), 'flash does not alter native snapshot state')
        show(vm, group=7, flags=0, revision=2)
        self.assertEqual(vm.leds, before)
        self.assertFalse(any(message[:2] == [191, 107] for message in vm.midi))

    def test_a_temporarily_unavailable_group_can_flash_without_replacing_overview(self):
        vm = ready()
        body = [0, 0, 0, 100, 30, 20, 90, 0, 0, 0, 64, 0, 0, 7, 0]
        device.snapshot(vm, 2, body, revision=1)
        show(vm, group=2, flags=16)
        self.assertEqual({vm.leds[y][x] for y in range(3) for x in range(8, 11)}, {ORANGE})
        self.assertEqual(vm.call('field/iii', 2, 0)['return'], 0, 'rendering never fabricates usable native state')

    def test_mixer_stays_open_and_other_controls_keep_their_confirmed_pixels(self):
        vm = ready(); show(vm, mode=1)
        before = [row[:] for row in vm.leds]
        show(vm, mode=1, flags=16, revision=2)
        for y in range(15):
            for x in range(15):
                self.assertEqual(vm.leds[y][x], ORANGE if x <= 2 and y <= 2 else before[y][x])
        show(vm, mode=1, flags=0, revision=3)
        self.assertEqual(vm.leds, before)

    def test_recording_flash_tints_only_progress_without_exiting_controls_or_resetting_markers(self):
        vm = ready()
        device.snapshot(vm, 0, [2, 0, 0, 100, 30, 20, 90, 0, 0, 0, 64, 31, 2, 7, 0], revision=1)
        show(vm, mode=2, flags=2)
        before = [row[:] for row in vm.leds]
        show(vm, mode=2, flags=18, revision=2)
        for y in range(1, 14):
            for x in range(1, 14):
                self.assertEqual(vm.leds[y][x], before[y][x], (x, y))
        self.assertTrue(any(vm.leds[y][x] != before[y][x] for x, y in PERIMETER))
        self.assertEqual(vm.leds[0][0], before[0][0], 'current-bar marker stays white')
        show(vm, mode=2, flags=2, revision=3)
        self.assertEqual(vm.leds, before)

    def test_flash_does_not_change_touch_transport_or_emit_notifications(self):
        normal = ready(); flashing = ready(); show(flashing, flags=16)
        normal.midi.clear(); flashing.midi.clear()
        for vm in (normal, flashing):
            device.touch(vm, 1, x=1, y=1)
            device.touch(vm, 2, x=1, y=3)
            device.touch(vm, 3, x=1, y=3)
        self.assertEqual(normal.midi, flashing.midi)
        self.assertFalse(any(message[:2] == [191, 107] for message in flashing.midi))

    def test_reserved_higher_flag_is_rejected_atomically(self):
        vm = ready(); before = [row[:] for row in vm.leds]
        show(vm, flags=32)
        self.assertEqual(vm.leds, before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.binary: device.PROGRAMS['main'] = args.binary.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FailureFlashTests))
    vm = device.VMS[0]
    report = dict(scope='Actual compiled main in independent Python VM; no hardware timing proof',
                  sha256=hashlib.sha256(device.PROGRAMS['main']).hexdigest(), code=vm.code_size, heap=vm.heap_size,
                  total=vm.code_size+vm.heap_size, free=7200-vm.code_size-vm.heap_size,
                  tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  max_stack=max(v.max_stack_bytes for v in device.VMS), max_callback=max(v.max_instructions for v in device.VMS))
    if args.result: args.result.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
