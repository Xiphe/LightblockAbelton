"""Allocation permission across compiled pad, actual bridge and Audio backend.

Native creation is simulated, including independent pad repaints during its
blocking interval. This proves the admission ordering, not physical latency.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
for directory in ('lightpad/tests', 'live', 'live/PerformanceController', 'bridge/tests'):
    sys.path.insert(0, str(ROOT / directory))
from littlefoot_harness import Harness
import test_audio_inputs as native_fixture
import test_audio_performance_path as baseline


class AllocationPathTests(unittest.TestCase):
    exchange = baseline.PathTests.exchange
    close_pipe = baseline.PathTests.close_pipe
    touch = baseline.PathTests.touch
    state = baseline.PathTests.state
    facts = baseline.PathTests.facts
    pump = baseline.PathTests.pump
    until = baseline.PathTests.until
    begin = baseline.PathTests.begin
    button = baseline.PathTests.button

    @classmethod
    def setUpClass(cls):
        cls.replay = os.environ.get('PERFORMANCE_REPLAY')
        if not cls.replay or not Path(cls.replay).is_file():
            raise unittest.SkipTest('Set PERFORMANCE_REPLAY to the built performance_replay executable')
        scratch = tempfile.TemporaryDirectory(prefix='allocation-path-')
        cls.addClassCleanup(scratch.cleanup)
        binary = Path(scratch.name) / 'main.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home() / '.local/bin/blocksctl'),
                        'compile', str(ROOT / 'lightpad/performance-main.littlefoot'),
                        '-o', str(binary)], check=True, capture_output=True)
        cls.program = binary.read_bytes()

    def setUp(self):
        self.vm = Harness(self.program)
        self.vm.call('initialise/v')
        self.midi_cursor = 0
        self.fixture = native_fixture.AudioInputChecks()
        self.backend = self.fixture.make(initial=3, groups=1, lanes=2)
        self.song = self.fixture.song
        self.addCleanup(self.fixture.doCleanups)
        self.pipe = subprocess.Popen([self.replay], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.close_pipe)
        self.obs = 0
        self.commands = []
        self.exchange('reset 1 %s' % self.vm.time_ms)
        self.backend.reseed()
        self.until(lambda: 0 in self.backend.reserves)
        self.advance(200)

    def advance(self, ms):
        self.vm.time_ms += ms
        self.fixture.clock = self.vm.time_ms / 1000.
        self.song.advance(1 + (self.vm.time_ms - 1000) / 500.)
        self.backend.tick()
        self.facts()
        self.pump()
        self.vm.call('repaint/v')
        self.pump()

    def test_overflow_is_announced_before_create_and_busy_touches_never_replay(self):
        original_create = self.song.create_audio_track
        created = []
        violations = []

        def announced_create(index):
            grants = [(value, actual) for key, value, actual in self.commands if key == 58]
            if not grants:
                # Native mutation errors are deliberately contained by the
                # backend. Retain this verdict outside that exception boundary.
                violations.append('Native creation ran before the compiled pad granted Busy')
                created.append(0)
                return original_create(index)
            self.assertEqual(grants[-1][0], grants[-1][1])
            # The approved tiny indicator is a solid yellow bar over the
            # existing recording view, leaving its perimeter available.
            pixel = self.vm.leds[7][7]
            self.assertGreater((pixel >> 16) & 255, 100)
            self.assertGreater((pixel >> 8) & 255, 40)
            self.assertLess(pixel & 255, 100)
            self.assertEqual(self.state('currentView'), 2)
            token = self.state('currentToken')
            midi_before = len(self.vm.midi)
            self.touch('Start', 4, 3, 3)
            self.vm.time_ms += 1900
            self.fixture.clock = self.vm.time_ms / 1000.
            self.song.advance(1 + (self.vm.time_ms - 1000) / 500.)
            self.vm.call('repaint/v')
            self.touch('End', 4, 3, 3)
            self.assertEqual(self.state('currentView'), 2)
            self.assertEqual(self.state('currentToken'), token)
            self.assertFalse(any(message[0] == 176 for message in self.vm.midi[midi_before:]),
                             'Cancel while Busy must not become a deferred recording action')
            created.append(grants[-1][0])
            return original_create(index)

        self.song.create_audio_track = announced_create
        self.begin()
        first = self.backend.recording.take
        self.until(lambda: 0 in self.backend.reserves and len(created) == 2)
        self.assertFalse(violations, '; '.join(violations))
        self.advance(200)
        self.assertEqual(len(set(created)), 1, 'one Busy permission covers the complete next bundle')
        self.assertIs(self.backend.recording.take, first)
        self.assertEqual(first.state, 'recording')
        self.assertFalse(any(key == 68 for key, value, actual in self.commands))
        self.button(10, 10)
        following = first.following
        self.assertIsNotNone(following)
        self.assertEqual(following.target, first.finish_target)
        self.until(lambda: self.backend.recording.take is following and following.state == 'recording')
        self.assertEqual(self.state('currentToken'), following.take_id)
        self.until(lambda: 0 in self.backend.reserves and self.backend.allocation_ticket is None)
        self.advance(200)
        self.button(3, 3)
        self.until(lambda: not self.backend.busy)

    def test_busy_bar_stays_until_both_sources_are_ready(self):
        original_advance = self.advance
        original_create = self.song.create_audio_track
        created = []
        intermediate_frames = []

        def create(index):
            created.append(self.backend.allocation_ticket)
            return original_create(index)

        def advance(ms):
            original_advance(ms)
            if created and 0 not in self.backend.reserves:
                intermediate_frames.append(self.vm.time_ms)
                self.assertEqual(self.backend.allocation_ticket, created[0],
                                 'Busy cleared before the complete source bundle was ready')
                self.assertEqual(self.vm.leds[7][7], 0xffc000,
                                 'the compiled pad hid Busy during routing or the next source')

        self.song.create_audio_track = create
        self.advance = advance
        self.begin()
        self.until(lambda: 0 in self.backend.reserves)
        self.advance(200)
        self.assertEqual(len(created), 2)
        self.assertGreater(len(intermediate_frames), 2,
                           'exercise intermediate routing callbacks, not just creation')
        self.assertEqual(len({ticket for ticket in created}), 1)
        self.assertEqual(sum(key == 58 for key, value, actual in self.commands), 1)
        self.assertIsNone(self.backend.allocation_ticket)
        self.assertNotEqual(self.vm.leds[7][7], 0xffc000)
        self.assertEqual(len(self.backend.reserves[0].members), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
