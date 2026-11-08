#!/usr/bin/env python3
"""Actual-bytecode endpoint continuity regressions, no hardware or MIDI.

A fake-native 1ms advance at the full-screen clear models time elapsing between
trackBar() and the later metronome painter. This exercises the real repaint
callback; it does not assert optical/display atomicity or firmware scheduling.
The injected advance goes red on the pre-fix odd-meter sampling race in both programs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_current_device as device

TRACES = []
PROGRAM_NAMES = ('main', 'metronome')


def center(vm):
    weights = [colour & 255 for colour in vm.leds[vm.swing_row]]
    if not sum(weights):
        raise AssertionError('metronome row is unexpectedly dark')
    return sum(x * weight for x, weight in enumerate(weights)) / sum(weights)


def setup(program, numerator, denominator, boundary):
    vm = device.ready(program)
    vm.swing_row = 14 if program == 'main' else 0
    bar = numerator * 4 / denominator
    position = (boundary - 1 / 64) % bar
    device.clock(vm, span=bar, bar=bar, numerator=numerator,
                 denominator=denominator, position=position, revision=1)
    return vm


def frame(vm, milliseconds, advance_at_clear=False):
    vm.time_ms = milliseconds
    original = vm.fill_rect
    if advance_at_clear:
        def clear(colour, x, y, width, height):
            if (x, y, width, height) == (0, 0, 15, 15):
                vm.time_ms += 1
            return original(colour, x, y, width, height)
        vm._register('fillRect/viiiii', clear)
    try:
        vm.call('repaint/v')
    finally:
        vm._register('fillRect/viiiii', original)
    return center(vm)


class SwingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if any(name not in device.PROGRAMS for name in PROGRAM_NAMES):
            folder = tempfile.TemporaryDirectory(prefix='lightpad-swing-')
            cls.addClassCleanup(folder.cleanup)
            for name, data in device.compile_programs(Path(folder.name)).items():
                device.PROGRAMS.setdefault(name, data)

    def test_odd_bar_wrap_does_not_flash_opposite_edge_within_repaint(self):
        for program in PROGRAM_NAMES:
            for numerator, denominator in ((3, 4), (7, 8), (5, 16)):
                with self.subTest(program=program, meter=(numerator, denominator)):
                    vm = setup(program, numerator, denominator, numerator * 4 / denominator)
                    points = [frame(vm, 1006), frame(vm, 1007, True), frame(vm, 1008)]
                    TRACES.append(dict(case='intra-repaint odd bar wrap', program=program,
                                       meter=[numerator, denominator], centers=points))
                    self.assertLess(max(abs(b - a) for a, b in zip(points, points[1:])), 1,
                                    f'opposite-edge one-frame flash: {points}')

    def test_four_four_every_beat_and_bar_wrap_is_continuous(self):
        for program in PROGRAM_NAMES:
            for boundary in (1, 2, 3, 4):
                for advancing in (False, True):
                    with self.subTest(program=program, boundary=boundary, advancing=advancing):
                        vm = setup(program, 4, 4, boundary)
                        points = [frame(vm, 1000 + offset, advancing) for offset in range(17)]
                        TRACES.append(dict(case='4/4 endpoint sweep', program=program, boundary=boundary,
                                           advance_at_clear=advancing, centers=points))
                        self.assertLess(max(abs(b - a) for a, b in zip(points, points[1:])), 1, points)

    def test_four_four_valid_clock_refresh_at_each_endpoint_is_continuous(self):
        for program in PROGRAM_NAMES:
            for boundary in (1, 2, 3, 4):
                with self.subTest(program=program, boundary=boundary):
                    vm = setup(program, 4, 4, boundary)
                    before = frame(vm, 1006)
                    vm.time_ms = 1008
                    device.clock(vm, span=4, bar=4, numerator=4, denominator=4,
                                 position=boundary % 4, revision=2)
                    after = frame(vm, 1008)
                    TRACES.append(dict(case='4/4 endpoint clock refresh', program=program,
                                       boundary=boundary, centers=[before, after]))
                    self.assertLess(abs(after - before), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-binary', type=Path)
    parser.add_argument('--metronome-binary', type=Path)
    parser.add_argument('--program', choices=('main', 'metronome', 'both'), default='both')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    global PROGRAM_NAMES
    PROGRAM_NAMES = ('main', 'metronome') if args.program == 'both' else (args.program,)
    for name in PROGRAM_NAMES:
        path = getattr(args, name + '_binary')
        if path: device.PROGRAMS[name] = path.read_bytes()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SwingTests))
    report = dict(scope='Actual compiled bytecode with fake native time/display; no optical or firmware proof',
                  program_sha256={name: hashlib.sha256(device.PROGRAMS[name]).hexdigest() for name in PROGRAM_NAMES},
                  tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), traces=TRACES)
    if args.result: args.result.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
