#!/usr/bin/env python3
"""Execute the compiled standalone status program without hardware or MIDI.

The helper protocol is injected as complete native events. Time, touch and LED
natives are deterministic fakes; no app, device, ports or saved defaults change.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from littlefoot_harness import Harness

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'lightpad/status.littlefoot'
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/status_device.json').read_text())
PROGRAM = None
VMS = []
HEADER = 0x23a8
MAGIC = 0x53540000
NAMES = {1: 'Bridge connection', 2: 'Live connection', 3: 'Remote groups', 4: 'Unexpected failure'}
COLORS = {1: 0x4191ff, 2: 0xf0f0f0, 3: 0xff9118, 4: 0xff1818}


def new():
    vm = Harness(PROGRAM)
    vm.circles = []
    vm._register('blendCircle/vifffb', lambda *args: vm.circles.append(args))
    VMS.append(vm)
    vm.call('initialise/v')
    return vm


def state(vm, value, reason=0):
    vm.call('handleMessage/viii', MAGIC | value | (reason << 8), 0, HEADER)


def repaint(vm, delta=0):
    vm.time_ms += delta
    vm.call('repaint/v')


def touch(vm, kind, finger=0):
    vm.call(kind + '/viffff', finger, .5, .5, .5 if kind != 'touchEnd' else 0., 0.)


def assert_screen(test, vm, value):
    if value == 1:
        test.assertGreaterEqual(len(vm.circles), 16)
        test.assertTrue(all(colour == 0x010107 for row in vm.leds for colour in row))
        return
    rows = FIXTURE['icons'][NAMES[value]]
    expected = [[COLORS[value] if row & (1 << x) else 0 for x in range(15)] for row in rows]
    test.assertEqual(vm.leds, expected)


class StatusDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PROGRAM
        if PROGRAM is None:
            compiler = shutil.which('blocksctl')
            if compiler is None:
                raise RuntimeError('Offline blocksctl compiler is required')
            with tempfile.TemporaryDirectory(prefix='lightpad-status-compile-') as directory:
                binary = Path(directory) / 'status.bin'
                subprocess.run([compiler, 'compile', str(SOURCE), '-o', str(binary)], check=True, capture_output=True)
                PROGRAM = binary.read_bytes()

    def test_standalone_boot_runs_approved_idle_without_live_or_helper(self):
        vm = new(); repaint(vm)
        assert_screen(self, vm, 1)
        self.assertEqual(vm.midi, [[191, 110, 18]])
        self.assertLessEqual(vm.code_size + vm.heap_size, 7200)
        self.assertEqual((vm.code_size, vm.heap_size), (FIXTURE['program']['code'], FIXTURE['program']['heap']))
        self.assertEqual(hashlib.sha256(PROGRAM).hexdigest(), FIXTURE['program']['binary_sha256'])
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), FIXTURE['program']['source_sha256'])
        for name, digest in FIXTURE['program']['includes'].items():
            self.assertEqual(hashlib.sha256((SOURCE.parent / name).read_bytes()).hexdigest(), digest)

    def test_exact_helpers_event_displays_all_static_masks_and_colors(self):
        for value in range(2, 5):
            vm = new(); state(vm, value, reason=127); repaint(vm)
            assert_screen(self, vm, value)
            self.assertEqual(vm.midi, [[191, 110, 18], [191, 108, 36]])

    def test_status_ready_is_not_a_fabricated_live_group_screen(self):
        connecting = new(); state(connecting, 0); repaint(connecting)
        ready = new(); state(ready, 5); repaint(ready)
        self.assertEqual(ready.leds, connecting.leds)
        self.assertEqual(ready.circles, connecting.circles)
        self.assertEqual(len(ready.circles), 16)

    def test_loading_uses_the_accepted_local_idle_animation_without_control_traffic(self):
        vm = new(); state(vm, 0); repaint(vm)
        before = vm.circles[:]
        self.assertEqual(len(before), 16)
        vm.circles.clear()
        repaint(vm, 400)
        self.assertEqual(len(vm.circles), 16)
        self.assertNotEqual(vm.circles, before)
        self.assertEqual(vm.midi, [[191, 110, 18], [191, 108, 36]], 'animation sends no control traffic')
        self.assertGreater(len({call[0] for call in vm.circles}), 8)

    def test_atomic_replay_heartbeats_extend_freshness_without_reannouncing(self):
        vm = new(); state(vm, 2); repaint(vm, 3499); assert_screen(self, vm, 2)
        state(vm, 2); repaint(vm, 3499); assert_screen(self, vm, 2)
        self.assertEqual(vm.midi.count([191, 108, 36]), 1)
        repaint(vm, 1); assert_screen(self, vm, 1)
        state(vm, 3); repaint(vm); assert_screen(self, vm, 3)
        self.assertEqual(vm.midi.count([191, 108, 36]), 2, 'recovery has a new model-ready observation')

    def test_malformed_or_unrelated_events_neither_change_state_nor_refresh_it(self):
        invalid = [(MAGIC | 6, 0, HEADER), (MAGIC | (128 << 8) | 2, 0, HEADER),
                   (MAGIC | 2, 1, HEADER), (MAGIC | 2, 0, HEADER | (1 << 16)),
                   (MAGIC | 2, 0, HEADER | (1 << 14)), (MAGIC | 2, 0, HEADER ^ (1 << 10)),
                   (MAGIC | 2, 0, HEADER ^ (1 << 8)), (MAGIC | 2, 0, HEADER | (1 << 23)),
                   (0x53550002, 0, HEADER), (274, 0, HEADER), (0, 0, 0)]
        for event in invalid:
            vm = new(); state(vm, 3); repaint(vm, 3490)
            vm.call('handleMessage/viii', *event); repaint(vm)
            assert_screen(self, vm, 3)
            repaint(vm, 10); assert_screen(self, vm, 1)
            self.assertEqual(vm.midi.count([191, 108, 36]), 1, event)

    def test_hello_retries_only_without_a_helper_and_cannot_refresh_the_link(self):
        vm = new(); repaint(vm, 999)
        self.assertEqual(vm.midi.count([191, 110, 18]), 1)
        repaint(vm, 1); self.assertEqual(vm.midi.count([191, 110, 18]), 2)
        state(vm, 4); repaint(vm, 2000)
        self.assertEqual(vm.midi.count([191, 110, 18]), 2)
        repaint(vm, 1500); assert_screen(self, vm, 1)
        self.assertEqual(vm.midi.count([191, 110, 18]), 3)

    def test_icon_tap_is_helper_only_and_does_not_require_live_feedback(self):
        for value in range(6):
            vm = new(); state(vm, value); before = len(vm.midi)
            touch(vm, 'touchStart'); touch(vm, 'touchMove'); touch(vm, 'touchEnd')
            expected = [[191, 106, 1], [191, 106, 0]]
            if value in (2, 3, 4): expected.append([191, 107, value])
            self.assertEqual(vm.midi[before:], expected)
        vm = new(); touch(vm, 'touchStart'); touch(vm, 'touchEnd')
        self.assertFalse(any(message[1] == 107 for message in vm.midi), 'idle touches never request diagnostics')

    def test_multitouch_requests_once_on_last_release_using_current_visible_state(self):
        vm = new(); state(vm, 2)
        for finger in range(16): touch(vm, 'touchStart', finger)
        before = len(vm.midi)
        for finger in range(15): touch(vm, 'touchEnd', finger)
        self.assertEqual(vm.midi[before:], [[191, 106, count] for count in range(15, 0, -1)])
        before = len(vm.midi)
        state(vm, 3); touch(vm, 'touchEnd', 15)
        self.assertEqual(vm.midi[before:], [[191, 106, 0], [191, 107, 3]])
        touch(vm, 'touchEnd', 15)
        self.assertEqual(vm.midi[before:], [[191, 106, 0], [191, 107, 3]])

    def test_stale_touch_release_reports_bridge_loss_and_invalid_contacts_do_nothing(self):
        vm = new(); state(vm, 2); touch(vm, 'touchStart')
        vm.time_ms += 3500; touch(vm, 'touchEnd')
        self.assertFalse(any(message[1] == 107 for message in vm.midi))
        before = len(vm.midi)
        for finger in (-1, 16, 127):
            touch(vm, 'touchStart', finger); touch(vm, 'touchMove', finger); touch(vm, 'touchEnd', finger)
        self.assertEqual(len(vm.midi), before)

    def test_idle_contact_cannot_turn_into_a_fault_notification_mid_gesture(self):
        vm = new(); touch(vm, 'touchStart')
        state(vm, 2); touch(vm, 'touchEnd')
        self.assertFalse(any(message[1] == 107 for message in vm.midi))
        touch(vm, 'touchStart'); touch(vm, 'touchEnd')
        self.assertEqual(vm.midi[-1], [191, 107, 2])

    def test_fault_icon_wins_over_hidden_mode_and_return_to_idle_has_no_ghost_contacts(self):
        vm = new()
        vm.call('touchStart/viffff', 0, .1, .1, .4, 0.)
        vm.call('touchStart/viffff', 1, 1.8, 1.8, .4, 0.)
        repaint(vm, 2000)
        self.assertIn(9871, vm.logs)
        state(vm, 2); repaint(vm)
        assert_screen(self, vm, 2)
        state(vm, 0); repaint(vm)
        self.assertTrue(all(colour == 0x010107 for row in vm.leds for colour in row))
        vm.call('touchEnd/viffff', 0, .1, .1, 0., 0.)
        vm.call('touchEnd/viffff', 1, 1.8, 1.8, 0., 0.)
        repaint(vm)
        self.assertFalse(any(message[1] == 107 for message in vm.midi))


def main():
    global PROGRAM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.binary: PROGRAM = args.binary.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StatusDeviceTests))
    vm = VMS[0]
    report = dict(scope='Compiled Littlefoot in independent Python VM; no hardware/native timing proof',
                  sha256=hashlib.sha256(PROGRAM).hexdigest(), code=vm.code_size, heap=vm.heap_size,
                  total=vm.code_size+vm.heap_size, free=7200-vm.code_size-vm.heap_size,
                  tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  max_callback=max(v.max_instructions for v in VMS), max_stack=max(v.max_stack_bytes for v in VMS))
    if args.result: args.result.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__': raise SystemExit(main())
