#!/usr/bin/env python3
"""Execute the compiled throwaway fader's ownership/reconciliation proof.

Uses the existing Python Littlefoot callback harness. No hardware, MIDI ports,
Live processes or installed files are accessed; firmware latency is not tested.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from littlefoot_harness import Harness


SOURCE = Path(__file__).resolve().parents[1] / 'optimistic-fader-spike.littlefoot'


class OptimisticFaderSpikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('blocksctl')
        if compiler is None:
            raise RuntimeError('Offline blocksctl compiler required')
        folder = tempfile.TemporaryDirectory(prefix='lightpad-optimistic-fader-')
        cls.addClassCleanup(folder.cleanup)
        binary = Path(folder.name) / 'fader.bin'
        subprocess.run([compiler, 'compile', str(SOURCE), '-o', str(binary)],
                       check=True, capture_output=True, text=True)
        cls.program = binary.read_bytes()

    def setUp(self):
        self.vm = Harness(self.program)
        self.vm.call('initialise/v')

    def state(self, name):
        return self.vm.call(name + '/i')['return']

    def model(self, kind, sequence, value, *, epoch=1, revision=1):
        header = 0xF1 | kind << 8 | epoch << 10 | revision << 17
        self.vm.call('handleMessage/viii', sequence, value, header)

    def touch(self, kind, finger, x=.2, y=.2):
        self.vm.call('touch' + kind + '/viffff', finger, x, y, 1., 0.)

    def open_mixer(self):
        self.touch('Start', 0)
        self.vm.time_ms += 499
        self.vm.call('repaint/v')
        self.assertEqual(self.state('currentView'), 0)
        self.vm.time_ms += 1
        self.vm.call('repaint/v')
        self.assertEqual(self.state('currentView'), 1)
        self.touch('Start', 1, y=1.6)

    def test_hold_preview_and_close_need_no_host_messages(self):
        self.open_mixer()
        before = [row[:] for row in self.vm.leds]
        self.touch('Move', 1, y=1.2)
        self.assertEqual(self.state('currentValue'), 118)
        self.assertEqual(self.state('hasPending'), 1)
        self.vm.call('repaint/v')
        self.assertTrue(any(self.vm.leds[y][0] != before[y][0]
                            for y in range(8, 15)))
        self.touch('End', 0)
        self.assertEqual(self.state('currentView'), 0)
        self.assertEqual(self.state('currentValue'), 118)
        self.assertEqual(self.state('hasPending'), 1)

    def test_release_flushes_the_last_move_inside_the_send_throttle(self):
        self.open_mixer()
        self.touch('Move', 1, y=1.2)
        self.assertEqual(self.vm.midi[-3:],
                         [[176, 70, 1], [176, 71, 118], [176, 72, 1]])
        self.vm.time_ms += 10
        self.touch('Move', 1, y=1.1)
        self.assertEqual(self.state('currentValue'), 127)
        self.assertEqual(self.state('currentSequence'), 1)
        self.assertEqual(len(self.vm.midi), 3)
        self.touch('End', 1, y=1.1)
        self.assertEqual(self.vm.midi[-3:],
                         [[176, 70, 2], [176, 71, 127], [176, 72, 2]])
        self.touch('End', 0)
        self.assertEqual(len(self.vm.midi), 6,
                         'unchanged release must not duplicate an intent')

    def test_old_ack_cannot_settle_newer_intent_and_current_ack_does(self):
        self.model(0, 0, 64)
        self.open_mixer()
        self.touch('Move', 1, y=1.2)
        self.vm.time_ms += 50
        self.touch('Move', 1, y=1.1)
        self.assertEqual(self.state('currentSequence'), 2)
        self.model(2, 1, 118, revision=2)
        self.assertEqual(self.state('currentValue'), 127)
        self.assertEqual(self.state('hasPending'), 1)
        self.touch('End', 1, y=1.1)
        self.touch('End', 0)
        self.model(2, 2, 127, revision=3)
        self.assertEqual(self.state('currentValue'), 127)
        self.assertEqual(self.state('hasPending'), 0)
        self.model(1, 0, 80, revision=4)
        self.assertEqual(self.state('currentValue'), 80)
        self.model(1, 0, 0, revision=2)
        self.assertEqual(self.state('currentValue'), 80,
                         'stale observation must not undo a newer model')

    def test_confirmed_external_change_waits_until_active_drag_ends(self):
        self.model(0, 0, 64)
        self.open_mixer()
        self.touch('Move', 1, y=1.2)
        sequence = self.state('currentSequence')
        self.model(2, sequence, 118, revision=2)
        self.assertEqual(self.state('hasPending'), 0)
        self.model(1, 0, 0, revision=3)
        self.assertEqual(self.state('currentValue'), 118,
                         'even confirmed newer facts do not own the gesture')
        self.touch('End', 1, y=1.2)
        self.assertEqual(self.state('currentValue'), 0)
        self.touch('End', 0)
        self.assertEqual(self.state('currentView'), 0)

    def test_new_epoch_preserves_pending_preview_and_rejects_old_epoch(self):
        self.model(0, 0, 64)
        self.open_mixer()
        self.touch('Move', 1, y=1.2)
        sequence = self.state('currentSequence')
        self.model(2, sequence, 0, epoch=2, revision=2)
        self.assertEqual(self.state('currentValue'), 118)
        self.assertEqual(self.state('hasPending'), 1)
        self.model(0, 0, 4, epoch=2, revision=0)
        resent = self.state('currentSequence')
        self.assertEqual(resent, (sequence + 1) % 128)
        self.assertEqual(self.state('currentValue'), 118)
        self.assertEqual(self.state('currentView'), 1)
        self.assertEqual(self.vm.midi[-3:],
                         [[176, 70, resent], [176, 71, 118], [176, 72, resent]])
        self.model(2, sequence, 4, epoch=1, revision=3)
        self.assertEqual(self.state('hasPending'), 1)
        self.assertEqual(self.state('currentValue'), 118)
        self.model(2, resent, 118, epoch=2, revision=1)
        self.assertEqual(self.state('hasPending'), 0)
        self.assertEqual(self.state('currentValue'), 118)


if __name__ == '__main__':
    unittest.main(verbosity=2)
