#!/usr/bin/env python3
"""Completed-recording-bar dots on actual canonical Littlefoot bytecode.

No hardware, Live, MIDI ports or historical experiments. UI mode2 reuses byte5
as a display count0..111 and byte3 as the opaque take binding; other modes retain
their pending-group mask. The host owns the true cumulative recording count.
The perimeter includes the currently running bar; the center counts completed
eight-bar blocks, so actual start already displays one perimeter dot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_current_device as device

PERIMETER = ([(x, 0) for x in range(15)] + [(14, y) for y in range(1, 15)]
             + [(x, 14) for x in range(13, -1, -1)] + [(0, y) for y in range(13, 0, -1)])
WHITE = 0xb0b0b0


def border(vm):
    return [vm.leds[y][x] for x, y in PERIMETER]


def white_dots(vm):
    return {p for p, colour in enumerate(border(vm)) if colour == WHITE}


def expected(count):
    running_bar = count % 8 + 1
    return {n * 56 // running_bar for n in range(running_bar)}


def center_tally(vm):
    return {y for y in range(1, 14) if vm.leds[y][7] == WHITE}


def expected_tally(count):
    return set(range(14 - count // 8, 14))


def snapshot(vm, *, group=0, state=2, recording=2, take=7, revision=1):
    body = [2, 0, 0, 100, 30, 20, 90, 0, 0, 0, 64, 31, recording, take, 0]
    body[0] = state
    device.snapshot(vm, group, body, revision)


def ui(vm, count, *, group=0, take=7, flags=0, high=0, revision=1, mode=2):
    return device.presentation(vm, body=[mode, group, group, take, flags, count, high], revision=revision)


def settle(vm):
    # Inspect settled geometry; animation intermediates have their own suite.
    vm.time_ms += 200
    vm.call('repaint/v')


def ready(*, count=0, state=2, recording=2, take=7, flags=0, group=0):
    vm = device.ready('main')
    snapshot(vm, group=group, state=state, recording=recording, take=take)
    device.clock(vm, running=False, revision=1)
    body = device.clock_payload(group=8 if state == 1 else group, span=4 if state == 1 else 8,
                                bar=4, position=2, running=False)
    body[1] = take
    device.clock(vm, group, body=body, revision=1)
    ui(vm, count, group=group, take=take, flags=flags)
    vm.call('repaint/v')
    return vm


class RecordingDotsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'main' not in device.PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix='lightpad-recording-dots-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def test_every_display_count_zero_through_111_has_bounded_remainder_and_tally(self):
        for count in range(112):
            with self.subTest(count=count):
                vm = ready(count=count)
                self.assertLessEqual(vm.code_size + vm.heap_size, 7200)
                self.assertEqual(white_dots(vm), expected(count))
                self.assertEqual(len(white_dots(vm)), count % 8 + 1)
                self.assertEqual(center_tally(vm), expected_tally(count))
                self.assertTrue(all(vm.leds[y][7] == (WHITE if y in expected_tally(count) else 0)
                                    for y in range(1, 14)))

    def test_eight_bar_blocks_reset_perimeter_and_stack_from_second_bottom_row(self):
        vm = ready(count=7)
        for revision, count, dots, tally in ((2, 8, {0}, {13}), (3, 9, {0, 28}, {13}),
                                           (4, 16, {0}, {12, 13}),
                                           (5, 111, {0, 7, 14, 21, 28, 35, 42, 49}, set(range(1, 14)))):
            ui(vm, count, revision=revision); vm.call('repaint/v'); settle(vm)
            self.assertEqual(white_dots(vm), dots, count)
            self.assertEqual(center_tally(vm), tally, count)
        ui(vm, 0, revision=6); vm.call('repaint/v'); settle(vm)
        self.assertEqual(white_dots(vm), {0})
        self.assertFalse(center_tally(vm), 'reset must clear all previous block tally pixels')

    def test_first_running_bar_is_visible_immediately_and_first_completion_adds_opposite_dot(self):
        vm = ready(count=0)
        self.assertEqual(white_dots(vm), {0}, 'actual start marks the running first bar at top-left')
        self.assertFalse(center_tally(vm))
        ui(vm, 1, revision=2); vm.call('repaint/v'); settle(vm)
        self.assertEqual(white_dots(vm), {0, 28}, 'one completed bar begins the second bar at bottom-right')
        self.assertFalse(center_tally(vm))
        ui(vm, 7, revision=3); vm.call('repaint/v'); settle(vm)
        self.assertEqual(white_dots(vm), {0, 7, 14, 21, 28, 35, 42, 49})

    def test_dots_replace_reference_markers_and_leave_smooth_progress_unchanged(self):
        empty = ready(count=0)
        ui(empty, 0, take=8, revision=2); empty.call('repaint/v')
        self.assertFalse(white_dots(empty), 'mismatched take provides a smooth-fill baseline without dots')
        for count in (1, 3, 5, 8, 9, 16, 111):
            vm = ready(count=count)
            for pixel, (before, after) in enumerate(zip(border(empty), border(vm))):
                self.assertEqual(after, WHITE if pixel in expected(count) else before)
        # The fourth running bar redistributes four dots, independently of
        # this reference's two bar boundaries at0 and28.
        self.assertEqual(white_dots(ready(count=3)), {0, 14, 28, 42})

    def test_queued_countdown_requires_its_own_clock_and_shows_remaining_count(self):
        vm = ready(count=19, recording=1)
        self.assertFalse(white_dots(vm), 'a cached two-bar reference is not a countdown clock')
        device.clock(vm, 0, span=32, bar=4, position=2, running=False, revision=2)
        vm.call('repaint/v')
        self.assertEqual(white_dots(vm), {n * 7 for n in range(8)})
        self.assertEqual(center_tally(vm), expected_tally(19))
        self.assertTrue(all((colour & 0xffff) == 0 for n, colour in enumerate(border(vm)) if n not in {n * 7 for n in range(8)}))

    def test_long_reference_countdown_never_becomes_solid_white(self):
        vm = ready(count=0, recording=1, group=2)
        device.clock(vm, 2, span=288, bar=4, position=64, running=False, revision=2)
        vm.call('repaint/v')
        self.assertFalse(white_dots(vm), '72-bar reference is not accepted as the queued one-bar clock')
        self.assertFalse(center_tally(vm))
        self.assertEqual(len(set(border(vm))), 1, 'invalid reference cannot advance the red fill')
        ui(vm, 111, group=2, revision=2); vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))
        device.clock(vm, 2, span=32, bar=4, position=2, running=False, revision=3)
        vm.call('repaint/v')
        self.assertEqual(white_dots(vm), expected(111))
        self.assertEqual(center_tally(vm), expected_tally(111))

    def test_inactive_terminal_and_mismatched_queue_never_fall_back_to_reference_dots(self):
        for recording in (0, 4):
            vm = ready(count=11, recording=recording)
            self.assertFalse(white_dots(vm), recording)
            self.assertFalse(center_tally(vm), recording)
        vm = ready(count=11, recording=1)
        ui(vm, 11, take=8, revision=2); vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))

    def test_first_take_and_deferred_finish_show_actual_count_without_can_accept(self):
        for state in (1, 2):
            for recording in (2, 3):
                vm = ready(count=12, state=state, recording=recording, flags=0)
                self.assertEqual(white_dots(vm), {0, 11, 22, 33, 44}, (state, recording))
                self.assertEqual(center_tally(vm), {13})

    def test_binding_packs_full_take_range_and_last_group_without_aliasing_count(self):
        for group, take in ((0, 0), (4, 64), (7, 127)):
            vm = ready(count=109, group=group, take=take)
            self.assertEqual(white_dots(vm), expected(109), (group, take))
            self.assertEqual(center_tally(vm), expected_tally(109))
            ui(vm, 109, group=group, take=(take + 1) % 128, revision=2)
            vm.call('repaint/v')
            self.assertFalse(white_dots(vm))
            self.assertFalse(center_tally(vm))

    def test_take_changes_never_reuse_previous_take_dots(self):
        vm = ready(count=11)
        snapshot(vm, recording=1, take=8, revision=2)
        ui(vm, 0, take=8, revision=2)
        vm.call('repaint/v')
        self.assertFalse(white_dots(vm), 'a queued take waits for its matching one-bar clock')
        self.assertFalse(center_tally(vm))
        snapshot(vm, recording=2, take=8, revision=3)
        vm.call('repaint/v')
        self.assertEqual(white_dots(vm), {0}, 'matching new take begins with its running-bar dot')
        self.assertFalse(center_tally(vm))
        # New snapshot before its UI must not show the prior count, nor fall
        # back to reference-boundary dots during actual recording.
        vm = ready(count=11)
        snapshot(vm, take=8, revision=2); vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))
        ui(vm, 1, take=8, revision=2); vm.call('repaint/v')
        self.assertEqual(white_dots(vm), {0, 28})
        # The symmetric ordering: future UI arrives before the snapshot.
        vm = ready(count=11)
        ui(vm, 1, take=8, revision=2); vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))
        snapshot(vm, take=8, revision=2); vm.call('repaint/v')
        self.assertEqual(white_dots(vm), {0, 28})

    def test_count_limits_high_byte_and_partial_frames_reject_atomically(self):
        vm = ready(count=3)
        baseline = [row[:] for row in vm.leds]
        for revision, count, high in ((2, 112, 0), (3, 127, 0), (4, 3, 1)):
            ui(vm, count, high=high, revision=revision); vm.call('repaint/v')
            self.assertEqual(vm.leds, baseline)
        # UI is still a single Native event. Nonzero reserved eighth byte may
        # never commit a new count.
        event = device.word_event(2, 8, 0, 5, [2, 0, 0, 7, 0, 5, 0, 1])
        vm.call('handleMessage/viii', *event); vm.call('repaint/v')
        self.assertEqual(vm.leds, baseline)
        ui(vm, 5, revision=6); vm.call('repaint/v'); settle(vm)
        self.assertEqual(white_dots(vm), expected(5))

    def test_unavailable_and_expired_feedback_hide_completed_dots(self):
        vm = ready(count=11)
        snapshot(vm, state=0, revision=2); vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))
        vm = ready(count=11)
        vm.time_ms += 3000; vm.call('repaint/v')
        self.assertFalse(white_dots(vm))
        self.assertFalse(center_tally(vm))

    def test_real_bar_flash_overlays_dots_then_returns_to_same_count(self):
        vm = ready(count=11)
        device.clock(vm, position=3.99, running=True, revision=2)
        vm.call('repaint/v')
        vm.time_ms += 20; vm.call('repaint/v')
        self.assertTrue(all((colour & 255) > 170 for colour in border(vm)), 'actual global bar flash overlays whole border')
        self.assertEqual(center_tally(vm), {13}, 'border-only flash preserves center tally')
        vm.time_ms += 160; vm.call('repaint/v')
        self.assertEqual(white_dots(vm), expected(3))
        self.assertEqual(center_tally(vm), {13})

    def test_other_screen_mask_semantics_and_recording_controls_are_unchanged(self):
        for case in device.CONTRACT['led_cases']:
            if case['program'] != 'main' or case['presentation'][0] == 2: continue
            vm = device.render_case(case)
            self.assertEqual([' '.join(f'{p:06x}' for p in row) for row in vm.leds], case['pixels'], case['name'])
        zero, many = ready(count=0), ready(count=111)
        for y in range(1, 14):
            for x in tuple(range(1, 7)) + tuple(range(8, 14)):
                self.assertEqual(zero.leds[y][x], many.leds[y][x], (x, y))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-binary', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.main_binary: device.PROGRAMS['main'] = args.main_binary.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RecordingDotsTests))
    repaint_cost = {}
    for count in (0, 1, 7, 8, 16, 111):
        trial = ready(count=count)
        before = trial.executed_instructions
        trial.call('repaint/v')
        repaint_cost[str(count)] = trial.executed_instructions - before
    vm = device.VMS[0]
    report = dict(scope='Actual compiled bytecode with fake natives; no device/optical/timing proof',
                  main_sha256=hashlib.sha256(device.PROGRAMS['main']).hexdigest(),
                  code=vm.code_size, heap=vm.heap_size, total=vm.code_size+vm.heap_size,
                  free=7200-vm.code_size-vm.heap_size, tests=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors),
                  instances=len(device.VMS), callbacks=sum(v.calls for v in device.VMS),
                  max_callback=max(v.max_instructions for v in device.VMS),
                  max_stack=max(v.max_stack_bytes for v in device.VMS),
                  stopped_clock_repaint_instructions_by_count=repaint_cost)
    if args.result: args.result.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
