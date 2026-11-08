#!/usr/bin/env python3
"""Fullscreen metronome layout/gesture regressions on actual compiled bytecode.

No Live instance, device, MIDI endpoint, installation or historical fixtures.
The pure maintained host detent function is the parity oracle; digits remain
confirmed-state output. --result optionally records a disposable QA report.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest

import test_current_device as device

sys.path.insert(0, str(device.ROOT / 'prototypes/controller/live'))
host_meter_step = importlib.import_module('LightpadController.device_ui').meter_step
PREVIEW = {'owner': 400, 'origin': 408, 'start': 416, 'step': 424, 'target': 432, 'until': 440}
# Accepted3x5 font, written as independent rows to make centering failures legible.
FONT = {
    1: ['.#.', '##.', '.#.', '.#.', '###'],
    4: ['#.#', '#.#', '###', '..#', '..#'],
    6: ['###', '#..', '###', '#.#', '###'],
    7: ['###', '..#', '..#', '..#', '..#'],
    8: ['###', '#.#', '###', '#.#', '###'],
    9: ['###', '#.#', '###', '..#', '###'],
}


def preview(vm, name, field=0):
    # Littlefoot allocates these six2-int arrays in declaration order after
    # the aligned397-byte raw heap. Readback checks the actual VM state, not
    # a second device model. No heap writes are performed by these tests.
    return struct.unpack('<i', vm.heap_bytes(PREVIEW[name] + field * 4, 4))[0]


def actual_clock(vm, numerator=4, denominator=4, revision=1, click=False):
    bar = 4 * numerator / denominator
    device.clock(vm, span=bar, bar=bar, numerator=numerator,
                 denominator=denominator, position=.5, revision=revision, click=click)


def pixels_for_value(value, field):
    text = str(value)
    result = set()
    for number, char in enumerate(text):
        left = field * 8 + (2 if len(text) == 1 else number * 4)
        for y, row in enumerate(FONT[int(char)]):
            for x, bit in enumerate(row):
                if bit == '#': result.add((left + x, 3 + y))
    return result


def digit_pixels(vm, field):
    return {(x, y) for y in range(3, 8) for x in range(field * 8, field * 8 + 7)
            if vm.leds[y][x] == 0xb0b0b0}


class MetronomeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not device.PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix='lightpad-fullscreen-meter-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def ready(self, numerator=4, denominator=4):
        vm = device.ready('metronome')
        if (numerator, denominator) != (4, 4): actual_clock(vm, numerator, denominator)
        return vm

    def assert_raster_with_actual_digits(self, vm, field, actual, selected_row, legal_rows):
        digits = pixels_for_value(actual, field)
        selected, grid = (0x45b6eb, 0x071724) if field == 0 else (0xbb6de0, 0x1b0c26)
        for y in range(1, 10):
            raster = selected if y == selected_row else grid if y in legal_rows else 0
            for x in range(field * 8, field * 8 + 7):
                self.assertEqual(vm.leds[y][x], 0xb0b0b0 if (x, y) in digits else raster, (field, x, y))
        self.assertEqual(digit_pixels(vm, field), digits)

    def test_real_budget_and_current_main_fixture_preservation(self):
        vm = self.ready()
        self.assertLessEqual(vm.code_size + vm.heap_size, 7200)
        expected = device.CONTRACT['programs']['main']
        self.assertEqual(hashlib.sha256(device.PROGRAMS['main']).hexdigest(), expected['binary_sha256'])
        self.assertEqual(hashlib.sha256((device.ROOT / expected['source']).read_bytes()).hexdigest(), expected['source_sha256'])
        for case in device.CONTRACT['led_cases']:
            if case['program'] != 'main': continue
            actual = device.render_case(case)
            self.assertEqual([' '.join(f'{p:06x}' for p in row) for row in actual.leds], case['pixels'], case['name'])

    def test_fullscreen_hit_regions_and_full_height_center_gap(self):
        for x, y, role in ((0, 1, 2), (6.99, 9.99, 2), (8, 1, 3), (14.99, 9.99, 3),
                            (0, 10, 4), (6.99, 14.99, 4), (8, 10, 1), (14.99, 14.99, 1),
                            (0, .99, 5), (7.5, .5, 5), (14.9, .5, 5),
                            (7.5, 1, 5), (7.5, 9.99, 5), (7.5, 10, 5), (7.5, 14.99, 5), (2, 10.5, 4)):
            vm = self.ready()
            device.touch(vm, 1, x=x, y=y)
            event = device.frames(vm)[-1]
            self.assertEqual((event['role'], event['opened'], event['extra']), (role, True, False), (x, y))
        # The former Close area is now Click and can never close locally.
        vm = self.ready()
        device.touch(vm, 1, x=2, y=13.5); device.touch(vm, 3, x=2, y=13.5)
        self.assertEqual((device.frames(vm)[-1]['role'], device.frames(vm)[-1]['opened']), (4, True))

    def test_single_and_two_digit_values_are_centered_and_confirmed(self):
        for numerator, denominator in ((4, 4), (7, 8), (99, 16)):
            vm = self.ready(numerator, denominator)
            vm.time_ms += 120
            vm.call('repaint/v')
            self.assertEqual(digit_pixels(vm, 0), pixels_for_value(numerator, 0))
            self.assertEqual(digit_pixels(vm, 1), pixels_for_value(denominator, 1))
            self.assertTrue(all(vm.leds[9][x] == 0x071724 for x in range(7)))
            self.assertTrue(all(vm.leds[9][x] == 0x1b0c26 for x in range(8, 15)))
            self.assertTrue(all(vm.leds[y][7] == 0 for y in range(1, 15)))

    def test_grid_is_visible_at_start_and_tracks_captured_relative_origin(self):
        vm = self.ready()
        vm.call('repaint/v')
        idle_brightness = vm.leds[9][0]
        device.touch(vm, 1, x=2, y=7.5)
        vm.call('repaint/v')
        # Two-LED grid exists before any Move, with confirmed white digits above it.
        self.assert_raster_with_actual_digits(vm, 0, 4, 7, (1, 3, 5, 9))
        self.assertTrue(all(vm.leds[y][7] == 0 for y in range(1, 15)))
        device.touch(vm, 2, x=2, y=5.5)
        vm.call('repaint/v')
        self.assertEqual(vm.leds[5][0], 0x45b6eb)
        self.assertEqual(vm.leds[7][0], idle_brightness, 'unselected grid uses the resting field brightness')
        self.assert_raster_with_actual_digits(vm, 0, 4, 5, (1, 3, 7, 9))
        # A different fractional capture origin changes grid rows, not fixed ruler rows.
        vm = self.ready(); device.touch(vm, 1, x=9, y=6.25); vm.call('repaint/v')
        self.assert_raster_with_actual_digits(vm, 1, 4, 6, (2, 4, 8))

    def test_grid_omits_detents_outside_legal_meter_bounds(self):
        for numerator, denominator, field, allowed in (
                (1, 4, 0, (1, 3)), (99, 4, 0, (7, 9)),
                (4, 1, 1, (1, 3)), (4, 16, 1, (7, 9))):
            vm = self.ready(numerator, denominator)
            x = field * 8
            device.touch(vm, 1, x=x + 1, y=5.5); vm.call('repaint/v')
            self.assert_raster_with_actual_digits(vm, field, numerator if field == 0 else denominator, 5, allowed)

    def test_preview_requires_fresh_confirmed_meter_and_cannot_seed_zero(self):
        vm = device.new('metronome')
        for group in range(8): device.snapshot(vm, group)
        device.presentation(vm)
        device.touch(vm, 1, x=2, y=7.5)
        self.assertEqual(device.frames(vm)[-1]['role'], 5)
        self.assertEqual(preview(vm, 'owner'), -1)
        actual_clock(vm)
        device.touch(vm, 2, x=2, y=4.5)
        self.assertEqual(preview(vm, 'owner'), -1)
        device.touch(vm, 3, x=2, y=4.5)
        device.touch(vm, 1, x=2, y=7.5)
        self.assertEqual(preview(vm, 'owner'), 0)
        self.assertEqual(preview(vm, 'start'), 4)
        vm.time_ms += 1000; vm.call('repaint/v')
        self.assertEqual(preview(vm, 'owner'), -1)
        device.touch(vm, 1, finger=1, x=2, y=7.5)
        self.assertEqual(device.frames(vm)[-1]['role'], 5)

    def test_exact_detent_entry_return_and_host_parity(self):
        vm = self.ready()
        device.touch(vm, 1, x=2, y=7.5)
        origin = preview(vm, 'origin'); expected = 0
        for delta in (1.99, 2, 1.75, 1.74, -1.99, -2, -1.75, -1.74, 4, 3.75, 3.74, -4, -3.75, -3.74, 0):
            device.touch(vm, 2, x=2, y=7.5 - delta)
            raw_y = vm.call('touchY/ii', 0)['return']
            expected = host_meter_step((origin - raw_y) / 128., expected)
            self.assertEqual(preview(vm, 'step'), expected, delta)
            self.assertEqual(preview(vm, 'target'), max(1, min(99, 4 + expected)), delta)

    def test_coalesced_random_reversals_match_host_and_stay_bounded(self):
        vm = self.ready(99, 16)
        device.touch(vm, 1, x=9, y=7.5)
        origin = preview(vm, 'origin', 1); expected = 0
        rng = random.Random(1825)
        for _ in range(120):
            device.touch(vm, 2, x=9, y=rng.uniform(.1, 14.8), milliseconds=0)
            raw_y = vm.call('touchY/ii', 0)['return']
            expected = host_meter_step((origin - raw_y) / 128., expected)
            self.assertEqual(preview(vm, 'step', 1), expected)
            self.assertEqual(preview(vm, 'target', 1), 1 << max(0, min(4, 4 + expected)))
        self.assertEqual(len(device.frames(vm)), 1, 'preview movement must not add wire frames before40ms')

    def test_actual_digits_stay_visible_during_drag_and_external_changes(self):
        vm = self.ready()
        device.touch(vm, 1, x=2, y=7.5); device.touch(vm, 2, x=2, y=5.4)
        self.assertEqual(preview(vm, 'target'), 5)
        vm.call('repaint/v')
        self.assertEqual(digit_pixels(vm, 0), pixels_for_value(4, 0))
        held_raster = [row[:7] for row in vm.leds[1:10]]
        actual_clock(vm, 7, 8)
        vm.call('repaint/v')
        self.assertEqual(digit_pixels(vm, 0), pixels_for_value(7, 0))
        changing_digits = pixels_for_value(4, 0) | pixels_for_value(7, 0)
        for y in range(1, 10):
            for x in range(7):
                if (x, y) not in changing_digits:
                    self.assertEqual(vm.leds[y][x], held_raster[y - 1][x], (x, y))
        self.assert_raster_with_actual_digits(vm, 0, 7, 5, (1, 3, 7, 9))
        self.assertEqual(digit_pixels(vm, 1), pixels_for_value(8, 1))
        self.assertEqual(preview(vm, 'target'), 5)
        device.touch(vm, 3, x=2, y=5.4); vm.call('repaint/v')
        self.assertGreater(preview(vm, 'until'), vm.time_ms, 'pending target remains after release')
        self.assertEqual(digit_pixels(vm, 0), pixels_for_value(7, 0), 'release shows actual7, never target5')

    def test_target_stays_while_held_then_expires_or_clears_on_confirmation(self):
        vm = self.ready()
        device.touch(vm, 1, x=2, y=7.5); device.touch(vm, 2, x=2, y=5.4)
        actual_clock(vm, 5, 4)
        vm.call('repaint/v')
        self.assertEqual(preview(vm, 'owner'), 0, 'matching actual must not remove held highlight')
        device.touch(vm, 3, x=2, y=5.4)
        self.assertEqual(preview(vm, 'until'), 0)
        vm = self.ready()
        device.touch(vm, 1, x=2, y=7.5); device.touch(vm, 2, x=2, y=5.4); device.touch(vm, 3, x=2, y=5.4)
        until = preview(vm, 'until')
        self.assertEqual(until - vm.time_ms, 200)
        vm.time_ms += 199; vm.call('repaint/v')
        self.assertEqual(preview(vm, 'until'), until)
        vm.time_ms += 1; vm.call('repaint/v')
        self.assertEqual(preview(vm, 'until'), 0)
        vm = self.ready()
        device.touch(vm, 1, x=2, y=7.5); device.touch(vm, 2, x=2, y=5.4); device.touch(vm, 3, x=2, y=5.4)
        actual_clock(vm, 5, 4)
        vm.call('repaint/v'); self.assertEqual(preview(vm, 'until'), 0)

    def test_simultaneous_fields_and_latest_start_visual_ownership(self):
        vm = self.ready()
        device.touch(vm, 1, finger=0, x=2, y=7.5)
        device.touch(vm, 1, finger=1, x=9, y=7.5)
        device.touch(vm, 2, finger=0, x=2, y=5.4)
        device.touch(vm, 2, finger=1, x=9, y=9.6)
        self.assertEqual((preview(vm, 'target', 0), preview(vm, 'target', 1)), (5, 2))
        device.touch(vm, 1, finger=2, x=2, y=7.5)
        device.touch(vm, 2, finger=2, x=2, y=9.6)
        self.assertEqual((preview(vm, 'owner'), preview(vm, 'target')), (2, 3))
        device.touch(vm, 3, finger=0, x=2, y=5.4)
        self.assertEqual((preview(vm, 'owner'), preview(vm, 'target')), (2, 3))
        self.assertEqual(preview(vm, 'owner', 1), 1)

    def test_nonoverview_stale_reset_and_finger_reuse_clear_targets(self):
        for reason in ('nonoverview', 'stale', 'hello', 'reuse'):
            vm = self.ready()
            device.touch(vm, 1, x=2, y=7.5); device.touch(vm, 2, x=2, y=5.4)
            if reason == 'nonoverview': device.presentation(vm, mode=1, revision=1)
            if reason == 'stale': vm.time_ms += 3000; vm.call('repaint/v')
            if reason == 'hello': vm.call('handleMessage/viii', 274, 0, 0xA8 | (3 << 8) | (8 << 10))
            if reason == 'reuse': device.touch(vm, 1, x=9, y=12.5)
            self.assertEqual(preview(vm, 'owner'), -1, reason)
            self.assertEqual(preview(vm, 'until'), 0, reason)

    def test_confirmed_click_switch_and_plain_tap_flash_leave_row_zero_unchanged(self):
        off = self.ready(); off.call('repaint/v')
        on = self.ready(); actual_clock(on, click=True); on.call('repaint/v')
        for y in range(10, 15):
            for x in range(7):
                self.assertEqual(off.leds[y][x], 0xb0b0b0 if 11 <= y < 14 and x in (1, 2)
                                 else 0x87382c if 11 <= y < 14 and 1 <= x < 6 else 0x30100e)
                self.assertEqual(on.leds[y][x], 0xb0b0b0 if 11 <= y < 14 and x in (4, 5)
                                 else 0x247746 if 11 <= y < 14 and 1 <= x < 6 else 0x0b2b17)
            self.assertEqual(off.leds[y][7], 0)
            self.assertEqual(on.leds[y][7], 0)
        # Touching Click does not speculate a new switch position.
        device.touch(off, 1, x=3, y=12.5); off.call('repaint/v')
        self.assertEqual(off.leds[12][1], 0xb0b0b0)
        self.assertEqual(off.leds[12][5], 0x87382c)
        actual_clock(off, click=True); off.call('repaint/v')
        self.assertEqual(off.leds[12][5], 0xb0b0b0)
        vm = self.ready(); device.touch(vm, 1, x=10, y=12.5)
        vm.call('repaint/v')
        reference = self.ready()
        reference.time_ms = vm.time_ms; reference.call('repaint/v')
        self.assertEqual(vm.leds[0], reference.leds[0], 'Tap must not flash the animated metronome row')
        self.assertTrue(all(vm.leds[y][x] == 0x303030 for y in range(10, 15) for x in range(8, 15)))
        vm.time_ms += 99; vm.call('repaint/v')
        self.assertTrue(all(vm.leds[y][x] == 0x303030 for y in range(10, 15) for x in range(8, 15)))
        vm.time_ms += 1; vm.call('repaint/v')
        self.assertTrue(all(vm.leds[y][x] == 0x161616 for y in range(10, 15) for x in range(8, 15)))
        reference.time_ms = vm.time_ms; reference.call('repaint/v')
        self.assertEqual(vm.leds[0], reference.leds[0])

    def test_no_close_action_and_no_new_wire_lane(self):
        vm = self.ready()
        device.touch(vm, 1, x=2, y=13.5); device.touch(vm, 3, x=2, y=13.5)
        self.assertEqual(device.frames(vm)[-1]['role'], 4)
        self.assertTrue(device.frames(vm)[-1]['opened'])
        vm.call('repaint/v')
        self.assertTrue(all(message[1] in (108, 110) or 20 <= message[1] <= 26 for message in vm.midi))
        self.assertTrue(all(not event['extra'] for event in device.frames(vm)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MetronomeLayoutTests))
    data = dict(scope='Actual canonical compiled bytecode/independent PythonVM/fake natives; no physical or optical evidence',
                tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                programs={name: hashlib.sha256(value).hexdigest() for name, value in device.PROGRAMS.items()},
                instances=len(device.VMS), callbacks=sum(vm.calls for vm in device.VMS),
                instructions=sum(vm.executed_instructions for vm in device.VMS),
                max_callback=max((vm.max_instructions for vm in device.VMS), default=0),
                max_stack=max((vm.max_stack_bytes for vm in device.VMS), default=0))
    if args.result: args.result.write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(data, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
