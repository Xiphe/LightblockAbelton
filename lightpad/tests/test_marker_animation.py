#!/usr/bin/env python3
"""Actual-bytecode marker slides/fades and queued eight-bar page guards.

No hardware, Live or traffic. Two adjacent fake native pixels carry fractional
marker opacity over the unchanged underlying progress; tests compare actual
compiled callbacks, not a second animation implementation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_current_device as device
import test_recording_dots as dots
import test_countdown_dots as countdown

EIGHT = {0, 7, 14, 21, 28, 35, 42, 49}


def repaint(vm, delta=0):
    vm.time_ms += delta
    vm.call('repaint/v')


def no_marker_border():
    vm = dots.ready(count=1)
    dots.ui(vm, 1, take=8, revision=2); repaint(vm)
    return dots.border(vm)


class MarkerAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'main' not in device.PROGRAMS:
            folder = tempfile.TemporaryDirectory(prefix='lightpad-marker-animation-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def test_first_layout_immediate_then_index_matched_slide_and_added_fade(self):
        vm = dots.ready(count=1)
        base = no_marker_border()
        self.assertEqual(dots.white_dots(vm), {0, 28})
        dots.ui(vm, 2, revision=2); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0, 28}, 'transition begins at prior positions')
        self.assertEqual(dots.border(vm)[37], base[37], 'zero-alpha new dot leaves underlying progress intact')
        repaint(vm, 100)
        self.assertEqual(dots.white_dots(vm), {0, 23}, 'old28 slides halfway toward18')
        self.assertNotEqual(dots.border(vm)[37], base[37], 'new37 fades in')
        self.assertNotEqual(dots.border(vm)[37], dots.WHITE)
        repaint(vm, 100)
        self.assertEqual(dots.white_dots(vm), {0, 18, 37})

    def test_fractional_marker_blends_neighbors_without_cutting_black_holes(self):
        vm = dots.ready(count=1)
        base = no_marker_border()
        dots.ui(vm, 2, revision=2); repaint(vm); repaint(vm, 50)
        # A quarter transition from28 to18 places that marker at25.5.
        for pixel in (25, 26):
            self.assertNotEqual(dots.border(vm)[pixel], base[pixel])
            self.assertNotEqual(dots.border(vm)[pixel], dots.WHITE)
            self.assertNotEqual(dots.border(vm)[pixel], 0)
        self.assertEqual(dots.border(vm)[0], dots.WHITE)
        self.assertEqual(dots.border(vm)[28], base[28], 'old position must not retain an opaque ghost')

    def test_removed_markers_fade_and_zero_alpha_restores_progress(self):
        vm = dots.ready(count=7)
        base = no_marker_border()
        dots.ui(vm, 8, revision=2); repaint(vm); repaint(vm, 100)
        self.assertEqual(dots.center_tally(vm), {13}, 'block tally updates immediately')
        for pixel in EIGHT - {0}:
            self.assertNotEqual(dots.border(vm)[pixel], dots.WHITE)
            self.assertNotEqual(dots.border(vm)[pixel], base[pixel])
        repaint(vm, 100)
        self.assertEqual(dots.white_dots(vm), {0})
        for pixel in EIGHT - {0}: self.assertEqual(dots.border(vm)[pixel], base[pixel])

    def test_identical_count_and_heartbeat_do_not_restart_transition(self):
        vm = dots.ready(count=1)
        dots.ui(vm, 2, revision=2); repaint(vm); repaint(vm, 100)
        dots.ui(vm, 2, revision=3); repaint(vm, 100)
        self.assertEqual(dots.white_dots(vm), {0, 18, 37})
        # Snapshot heartbeat is also not an animation restart.
        dots.snapshot(vm, revision=2); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0, 18, 37})

    def test_rapid_retarget_uses_previous_target_and_finishes_within_200ms(self):
        vm = dots.ready(count=1)
        dots.ui(vm, 2, revision=2); repaint(vm); repaint(vm, 100)
        self.assertEqual(dots.white_dots(vm), {0, 23})
        dots.ui(vm, 3, revision=3); repaint(vm)
        # Explicit prototype limitation: interrupted movement restarts from the
        # previous target geometry, rather than keeping a per-dot path history.
        self.assertEqual(dots.white_dots(vm), {0, 18, 37})
        repaint(vm, 200)
        self.assertEqual(dots.white_dots(vm), {0, 14, 28, 42})

    def test_animation_never_changes_captured_raw_touch_frames(self):
        results = []
        for animate in (False, True):
            vm = dots.ready(count=1)
            device.touch(vm, 1, x=10, y=2)
            if animate: dots.ui(vm, 2, revision=2)
            repaint(vm); repaint(vm, 50)
            device.touch(vm, 2, x=11, y=3)
            repaint(vm, 50)
            device.touch(vm, 3, x=11, y=3)
            results.append(device.frames(vm))
        self.assertEqual(results[0], results[1])
        self.assertEqual([event['kind'] for event in results[0]], [1, 2, 3])

    def test_new_take_group_and_queued_to_actual_have_immediate_first_markers(self):
        vm = dots.ready(count=1)
        dots.ui(vm, 2, revision=2); repaint(vm); repaint(vm, 50)
        dots.snapshot(vm, take=8, revision=2)
        dots.ui(vm, 0, take=8, revision=3); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0})
        dots.snapshot(vm, group=7, take=9, revision=1)
        dots.ui(vm, 7, group=7, take=9, revision=4); repaint(vm)
        self.assertEqual(dots.white_dots(vm), EIGHT)
        vm = countdown.ready(count=2)
        dots.snapshot(vm, recording=2, revision=2)
        dots.ui(vm, 0, revision=2); repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0}, 'actual start must not fade from queued3 dots')

    def test_invalid_binding_view_reset_and_due_clear_without_ghost_fade(self):
        for reason in ('binding', 'view', 'hello'):
            vm = dots.ready(count=1)
            dots.ui(vm, 2, revision=2); repaint(vm); repaint(vm, 50)
            if reason == 'binding':
                dots.ui(vm, 2, take=8, revision=3); repaint(vm)
                self.assertFalse(dots.white_dots(vm))
                dots.snapshot(vm, take=8, revision=2)
                dots.ui(vm, 2, take=8, revision=4); repaint(vm)
            elif reason == 'view':
                device.presentation(vm, mode=0, revision=3); repaint(vm)
                dots.ui(vm, 2, revision=4); repaint(vm)
            else:
                vm.call('handleMessage/viii', 274, 0, 0xA8 | (3 << 8) | (8 << 10))
                for group in range(8): dots.snapshot(vm, group=group, revision=1)
                dots.ui(vm, 2, revision=1); repaint(vm)
            self.assertEqual(dots.white_dots(vm), {0, 18, 37}, reason)
        vm = countdown.ready(count=0, position=3.5, running=True)
        repaint(vm, 250)
        countdown.CountdownTests().assert_empty_countdown(vm)
        countdown.group_clock(vm, position=0, running=True, revision=3)
        repaint(vm)
        self.assertEqual(dots.white_dots(vm), {0})
        vm = countdown.ready(count=1, position=3.75, running=True)
        dots.ui(vm, 0, revision=2); repaint(vm)
        repaint(vm, 125)
        countdown.CountdownTests().assert_empty_countdown(vm)

    def test_paged_empty_clock_may_lead_ui_but_render_requires_matching_page(self):
        vm = countdown.ready(count=0, state=1)
        countdown.group_clock(vm, state=1, span=32, position=8, revision=3)
        self.assertEqual(vm.call('clockNumber/iii', 0, 2)['return'], 2048)
        self.assertEqual(vm.call('timed/iii', 0, 0)['return'] & 3, 1)
        repaint(vm)
        countdown.CountdownTests().assert_empty_countdown(vm)
        dots.ui(vm, 8, revision=2); repaint(vm)
        self.assertEqual(dots.white_dots(vm), EIGHT)
        self.assertEqual(dots.center_tally(vm), {13})
        # Page clock covering8 bars at phase8/32 drains three-quarters red.
        self.assertGreater(dots.border(vm)[30] >> 16, dots.border(vm)[50] >> 16)
        dots.ui(vm, 7, revision=3); repaint(vm)
        countdown.CountdownTests().assert_empty_countdown(vm)
        countdown.group_clock(vm, state=1, span=4, position=1, revision=4); repaint(vm)
        self.assertEqual(dots.white_dots(vm), EIGHT)
        self.assertFalse(dots.center_tally(vm))

    def test_large_remaining_pages_always_eight_markers(self):
        for count in (8, 9, 15, 16, 111):
            for state in (1, 2):
                vm = countdown.ready(count=count, state=state)
                self.assertEqual(dots.white_dots(vm), EIGHT, (count, state))
                self.assertEqual(dots.center_tally(vm), dots.expected_tally(count))

    def test_page_exception_never_weakens_global_or_nonqueued_one_bar_validation(self):
        vm = dots.ready(state=1, recording=2)
        countdown.group_clock(vm, state=1, span=32, revision=3)
        self.assertEqual(vm.call('timed/iii', 0, 0)['return'], 0)
        body = device.clock_payload(0, span=32, position=1, bar=4)
        body[0] = (body[0] & ~3) | 1; body[1] = 0
        device.clock(vm, 8, body=body, revision=3)
        self.assertEqual(vm.call('timed/iii', 8, 0)['return'], 0)
        vm = countdown.ready(count=8, state=1)
        body[1] = 7
        body[8] = 64  # valid bounded first-bar offset, but not the scoped page origin0
        device.clock(vm, 0, body=body, revision=3)
        self.assertEqual(vm.call('timed/iii', 0, 0)['return'], 0)
        repaint(vm)
        countdown.CountdownTests().assert_empty_countdown(vm)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-binary', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.main_binary: device.PROGRAMS['main'] = args.main_binary.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MarkerAnimationTests))
    vm = device.VMS[0]
    data = dict(scope='Actual compiled bytecode/fake pixels and time; no hardware or transport performance proof',
                sha256=hashlib.sha256(device.PROGRAMS['main']).hexdigest(),code=vm.code_size,heap=vm.heap_size,
                total=vm.code_size+vm.heap_size,free=7200-vm.code_size-vm.heap_size,
                tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                max_callback=max(v.max_instructions for v in device.VMS),max_stack=max(v.max_stack_bytes for v in device.VMS),
                retarget_policy='A change before200ms starts from the previous target-count geometry, not a sampled in-flight geometry')
    if args.result: args.result.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
