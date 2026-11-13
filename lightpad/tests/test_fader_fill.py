#!/usr/bin/env python3
"""Compiled fader pixels distinguish zero from positive seven-bit controls."""
from pathlib import Path
import tempfile
import unittest

import test_current_device as device
from littlefoot_harness import Harness, i32


class FaderFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'main' not in device.PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix='lightpad-fader-fill-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def fader(self, colour, raw):
        vm = Harness(device.PROGRAMS['main'])
        vm.call('drawFader/viiif', 4, 2, i32(colour), raw / 127.)
        return vm, [vm.leds[y][4] for y in range(14, 7, -1)]

    def test_only_exact_zero_has_no_fill_and_maximum_keeps_existing_brightness(self):
        for colour in (0xffffffff, 0xffc83c28, 0xff28c83c, 0xff2850c8):
            vm, empty = self.fader(colour, 0)
            _, full = self.fader(colour, 127)
            self.assertEqual(len(set(empty)), 1)
            self.assertEqual(len(set(full)), 1)
            self.assertEqual(empty[0], vm.call('dim/iif', i32(colour), .08)['return'] & 0xffffff)
            self.assertEqual(full[0], vm.call('dim/iif', i32(colour), .5)['return'] & 0xffffff)
            for raw in range(1, 128):
                with self.subTest(colour=hex(colour), raw=raw):
                    _, pixels = self.fader(colour, raw)
                    self.assertNotEqual(pixels, empty)

    def test_all_128_values_are_monotonic_in_every_rgb_channel(self):
        for colour in (0xffffffff, 0xffc83c28):
            _, previous = self.fader(colour, 0)
            for raw in range(1, 128):
                _, pixels = self.fader(colour, raw)
                for old, new in zip(previous, pixels):
                    for shift in (0, 8, 16):
                        self.assertGreaterEqual((new >> shift) & 255, (old >> shift) & 255)
                self.assertNotEqual(pixels, previous, 'each seven-bit input has visible progress')
                previous = pixels

    def test_fractional_first_and_middle_rows_interpolate_instead_of_rounding_height(self):
        _, empty = self.fader(0xffffffff, 0)
        _, full = self.fader(0xffffffff, 127)
        previous = empty[0]
        for raw in range(1, 19):
            _, pixels = self.fader(0xffffffff, raw)
            self.assertLess(previous, pixels[0])
            self.assertLess(pixels[0], full[0])
            self.assertEqual(pixels[1:], empty[1:])
            previous = pixels[0]
        _, middle = self.fader(0xffffffff, 64)
        self.assertEqual(middle[:3], full[:3])
        self.assertLess(empty[3], middle[3])
        self.assertLess(middle[3], full[3])
        self.assertEqual(middle[4:], empty[4:])

    def test_volume_and_all_four_white_faders_share_minimum_feedback_without_pan_changes(self):
        vm = device.ready('main')
        device.clock(vm, running=False, revision=1)
        body = [2, 0, 0, 100, 30, 20, 0, 0, 0, 0, 64, 31, 0, 7, 0]
        device.snapshot(vm, 0, body, revision=1)
        device.presentation(vm, mode=1, revision=1)
        vm.call('repaint/v'); before = [row[:] for row in vm.leds]
        messages = list(vm.midi)
        for field in (6, 7, 8, 9, 14): body[field] = 1
        device.snapshot(vm, 0, body, revision=2)
        vm.call('repaint/v')
        columns = {0, 1, 2, 4, 5, 7, 8, 10, 11, 13, 14}
        for y in range(15):
            for x in range(15):
                if y == 14 and x in columns:
                    self.assertNotEqual(vm.leds[y][x], before[y][x], (x, y))
                else:
                    self.assertEqual(vm.leds[y][x], before[y][x], (x, y))
        self.assertEqual(vm.midi, messages, 'rendering never adds control messages')


if __name__ == '__main__': unittest.main(verbosity=2)
