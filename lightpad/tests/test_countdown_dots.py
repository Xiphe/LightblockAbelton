#!/usr/bin/env python3
"""Reverse countdown on canonical compiled Littlefoot, no hardware or Live.

The host supplies ceil(remaining bars)-1 plus a target-relative one-bar group
clock. These tests encode the unchanged NativeA8 records directly and never
infer actual recording from time. Shared fixtures are maintained pure helpers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_current_device as device
import test_recording_dots as dots


def group_clock(vm, *, group=0, state=2, take=7, position=1, span=4,
                running=False, revision=2):
    body = device.clock_payload(8 if state == 1 and span == 4 else group, span=span, position=position,
                                bar=4, running=running)
    if state == 1: body[0] = (body[0] & ~3) | 1
    body[1] = take
    device.clock(vm, group, body=body, revision=revision)


def ready(*, count=0, position=1, state=2, take=7, group=0, running=False):
    vm = dots.ready(count=count, state=state, recording=1, take=take, group=group)
    group_clock(vm, group=group, state=state, take=take, position=position,
                span=32 if count >= 8 else 4, running=running)
    vm.call('repaint/v')
    return vm


def repaint(vm, milliseconds=0):
    vm.time_ms += milliseconds
    vm.call('repaint/v')


class CountdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'main' not in device.PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix='lightpad-countdown-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def assert_empty_countdown(self, vm):
        self.assertFalse(dots.white_dots(vm))
        self.assertFalse(dots.center_tally(vm))
        self.assertEqual(set(dots.border(vm)), {0x140000}, 'no filled progress or fallback phase, only dim red backdrop')

    def test_nine_eight_and_one_remaining_bars_count_backwards(self):
        vm = ready(count=8)
        self.assertEqual(dots.white_dots(vm), {0, 7, 14, 21, 28, 35, 42, 49})
        self.assertEqual(dots.center_tally(vm), {13})
        dots.ui(vm, 7, revision=2); group_clock(vm, revision=3); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0, 7, 14, 21, 28, 35, 42, 49})
        self.assertFalse(dots.center_tally(vm))
        dots.ui(vm, 0, revision=3); repaint(vm); repaint(vm, 200)
        self.assertEqual(dots.white_dots(vm), {0})
        self.assertFalse(dots.center_tally(vm))

    def test_all_bounded_queued_counts_reuse_same_eight_bar_geometry(self):
        for count in range(112):
            vm = ready(count=count)
            self.assertLessEqual(vm.code_size + vm.heap_size, 7200)
            self.assertEqual(dots.white_dots(vm), dots.expected(7 if count >= 8 else count), count)
            self.assertEqual(dots.center_tally(vm), dots.expected_tally(count), count)

    def test_red_fractional_fill_drains_without_changing_geometry_or_group_clock(self):
        vm = ready(count=0, position=1)
        before = dots.border(vm)
        self.assertGreater(before[30] >> 16, before[50] >> 16)
        group_clock(vm, position=3, revision=3); repaint(vm)
        after = dots.border(vm)
        self.assertGreater(after[5] >> 16, after[30] >> 16)
        self.assertLess(after[30] >> 16, before[30] >> 16)
        self.assertEqual(dots.white_dots(vm), {0})
        self.assertTrue(all((colour & 0xffff) == 0 for p, colour in enumerate(after) if p != 0))
        # Host phase is preserved:128ticks at midbar, not replaced by a local scheduler.
        group_clock(vm, position=2, revision=4)
        self.assertEqual(vm.call('positionNow/fi', 0)['return'], 128.)

    def test_final_bar_clamps_at_target_until_native_observation(self):
        vm = ready(count=0, position=3.5, running=True)
        repaint(vm, 249)
        self.assertEqual(dots.white_dots(vm), {0})
        repaint(vm, 1)
        self.assert_empty_countdown(vm)
        self.assertEqual(vm.call('positionNow/fi', 0)['return'], 256.)
        repaint(vm, 650)
        self.assert_empty_countdown(vm)
        # The queued state has not changed by merely passing the target.
        self.assertEqual(vm.call('field/iii', 0, 12)['return'], 1)
        dots.snapshot(vm, recording=2, revision=2)
        repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0})
        self.assertLess(vm.call('positionNow/fi', 0)['return'], 256., 'actual Native observation releases the clamp')
        # The observed anchor equals the prior target, so the old forward phase
        # is already start-relative; an explicit fresh actual clock may confirm it.
        group_clock(vm, position=1.3, running=True, revision=3); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0})

    def test_invalid_clock_at_due_holds_empty_then_actual_clock_restores_record_view(self):
        vm = ready(count=0, position=3.5, running=True)
        repaint(vm, 250)
        device.clock(vm, 0, body=[0] * 18, revision=3); repaint(vm)
        self.assert_empty_countdown(vm)
        repaint(vm, 100)
        self.assert_empty_countdown(vm)
        dots.snapshot(vm, recording=2, revision=2)
        group_clock(vm, position=.2, running=True, revision=4); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0})
        self.assertFalse(dots.center_tally(vm))
        self.assertTrue(any((colour & 0xffff) != 0 for p, colour in enumerate(dots.border(vm)) if p != 0))

    def test_missing_stale_wrong_take_and_long_reference_clock_never_fall_back(self):
        for state in (1, 2):
            for reason in ('invalid', 'stale', 'wrong_take', 'wrong_context', 'long_reference'):
                with self.subTest(state=state, reason=reason):
                    vm = ready(count=8, state=state)
                    if reason == 'invalid': device.clock(vm, 0, body=[0] * 18, revision=3)
                    if reason == 'stale':
                        vm.time_ms += 1000
                        device.clock(vm, running=False, revision=2)  # fresh global fallback must not be used
                    if reason == 'wrong_take': group_clock(vm, state=state, take=8, revision=3)
                    if reason == 'wrong_context': group_clock(vm, state=3-state, revision=3)
                    if reason == 'long_reference': group_clock(vm, state=state, span=288, revision=3)
                    repaint(vm)
                    self.assert_empty_countdown(vm)

    def test_new_take_ui_snapshot_and_clock_must_all_match(self):
        vm = ready(count=8)
        dots.snapshot(vm, recording=1, take=8, revision=2)
        dots.ui(vm, 7, take=8, revision=2); repaint(vm)
        self.assert_empty_countdown(vm)
        group_clock(vm, take=8, revision=3); repaint(vm)
        self.assertEqual(dots.white_dots(vm), dots.expected(7))
        # New UI before snapshot/clock must clear the old take's entire display.
        dots.ui(vm, 8, take=9, revision=3); repaint(vm)
        self.assert_empty_countdown(vm)
        dots.snapshot(vm, recording=1, take=9, revision=3); repaint(vm)
        self.assert_empty_countdown(vm)
        group_clock(vm, take=9, span=32, revision=4); repaint(vm)
        self.assertEqual(dots.white_dots(vm), dots.expected(7))
        self.assertEqual(dots.center_tally(vm), {13})

    def test_valid_same_take_deferral_can_restore_a_future_wait_without_starting(self):
        vm = ready(count=0, position=3.5, running=True)
        repaint(vm, 250); self.assert_empty_countdown(vm)
        dots.ui(vm, 8, revision=2)
        group_clock(vm, position=0, span=32, running=True, revision=3); repaint(vm)
        self.assertEqual(dots.white_dots(vm), dots.expected(7))
        self.assertEqual(dots.center_tally(vm), {13})
        self.assertGreater(dots.border(vm)[50] >> 16, 20)
        self.assertEqual(vm.call('field/iii', 0, 12)['return'], 1)

    def test_global_clock_and_real_bar_flash_are_independent_of_last_bar_clamp(self):
        vm = ready(count=0, position=3.5, running=True)
        device.clock(vm, position=3.5, running=True, revision=2)
        repaint(vm, 250)
        self.assertEqual(vm.call('positionNow/fi', 0)['return'], 256.)
        self.assertEqual(vm.call('positionNow/fi', 8)['return'], 0.)
        self.assert_empty_countdown(vm)
        # Fresh global samples alone continue to drive the actual bar flash.
        # Rebase explicitly rather than pretending a near-whole-bar forward
        # correction is normal progression; the pulse tracker rejects replay.
        device.clock(vm, body=[0] * 18, revision=3)
        device.clock(vm, position=3.99, running=True, revision=4); repaint(vm)
        repaint(vm, 20)
        self.assertTrue(all((colour & 255) > 170 for colour in dots.border(vm)))

    def test_actual_recording_and_deferred_finish_counting_stay_forward(self):
        for recording in (2, 3):
            vm = dots.ready(count=9, recording=recording)
            group_clock(vm, position=1, revision=2); repaint(vm)
            before = dots.border(vm)
            group_clock(vm, position=3, revision=3); repaint(vm)
            self.assertGreater(dots.border(vm)[30] >> 16, before[30] >> 16)
            self.assertEqual(dots.white_dots(vm), {0, 28})
            self.assertEqual(dots.center_tally(vm), {13})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-binary', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.main_binary: device.PROGRAMS['main'] = args.main_binary.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CountdownTests))
    vm = device.VMS[0]
    report = dict(scope='Actual compiled bytecode/fake time and pixels, no hardware proof',
                  sha256=hashlib.sha256(device.PROGRAMS['main']).hexdigest(),
                  code=vm.code_size, heap=vm.heap_size, total=vm.code_size+vm.heap_size,
                  free=7200-vm.code_size-vm.heap_size, tests=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors), instances=len(device.VMS),
                  callbacks=sum(v.calls for v in device.VMS),
                  max_callback=max(v.max_instructions for v in device.VMS),
                  max_stack=max(v.max_stack_bytes for v in device.VMS))
    if args.result: args.result.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
