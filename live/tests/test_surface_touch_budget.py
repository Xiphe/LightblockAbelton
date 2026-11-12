"""Partial raw touches cannot spend output capacity before native intent commits."""

from types import SimpleNamespace
import unittest
import compact_assertions as wire
from unittest.mock import patch

import test_surface_pacing as pacing_helpers
import test_surface_touch as touch_helpers
from LightpadController import configuration as config, protocol as p, timing
from LightpadController import surface as module


class SurfaceTouchBudgetTests(unittest.TestCase):
    def arrange_end_with_three_due_clocks(self):
        self.now, self.events, self.wire, self.native_calls = [100.0], [], [], []
        owner = self
        native = SimpleNamespace(mute=False)
        self.native = native

        class Engine(pacing_helpers.EightClockEngine):
            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                fields[92] = 127 if group == 0 and native.mute else 0
                return fields

            def dispatch(self, command):
                if (command.group, command.op, command.arg) != (0, p.SET_MUTE, 127):
                    raise AssertionError('unexpected native intent: %r' % (command,))
                native.mute = True
                owner.native_calls.append(command)
                owner.events.append(('native_mute', native.mute))
                return p.APPLIED

            def group_clock(self, group):
                owner.events.append(('supplier', group, native.mute))
                return super().group_clock(group)

            def global_clock(self):
                owner.events.append(('supplier', 8, native.mute))
                return super().global_clock()

            def meter_state(self):
                return 4, 4, False

        def emitted(message):
            self.wire.append((self.now[0], message))
            self.events.append(('wire', message))

        settings = patch.object(config, 'AUDIO_CONTROLLER_MANIFEST', None)
        settings.start()
        self.addCleanup(settings.stop)
        clock = patch.object(module.time, 'monotonic', side_effect=lambda: self.now[0])
        clock.start()
        self.addCleanup(clock.stop)
        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0, tempo=120, signature_numerator=4,
                               signature_denominator=4, metronome=False)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(append=emitted))
        self.surface = module.LightpadController(instance)
        self.addCleanup(self.surface.disconnect)
        self.engine = self.surface._engine = Engine()
        self.surface.receive_midi((0xBF, 110, p.VERSION))
        self.now[0] = 100.001
        self.feed(touch_helpers.frame(seq=1, kind=1))
        self.assertEqual(self.native_calls, [])

        # Retain the same real pacer, but seed a controlled queue through its
        # public API after Start. Existing consumed-window accounting survives.
        self.now[0] = 100.1
        self.surface._output.clear()
        for group in range(3):
            self.surface._output.offer(p.encode_snapshot(group, 1, self.engine.snapshot(group)), self.now[0])
        self.surface._output.flush(self.now[0])
        self.now[0] = 100.2
        for group in range(3):
            self.surface._output.clock(group, lambda group=group: timing.encode_view_timing(
                group, 1, self.engine.group_clock(group)), self.now[0])
        self.events.clear()
        return touch_helpers.frame(seq=2, kind=3)

    def feed(self, messages):
        for message in messages:
            self.surface.receive_midi(message)

    def assert_wire_budget(self):
        units = []
        cursor = 0
        while cursor < len(self.wire):
            instant, (status, cc, revision) = self.wire[cursor]
            if cc not in (33, 34, 35):
                self.assertEqual(status, 0xBF)
                self.assertIn(cc, (110, 111, 112, 113))
                cursor += 1
                continue
            size = wire.size(cc)
            frame = self.wire[cursor:cursor + size]
            self.assertEqual([at for at, _ in frame], [instant] * size)
            self.assertEqual([message[0] for _, message in frame], [status] * size)
            self.assertEqual([message[1] for _, message in frame], wire.controllers(cc))
            self.assertEqual(frame[-1][1][2], revision)
            units.append(instant)
            cursor += size
        for instant in units:
            self.assertLessEqual(sum(instant - .09 + 1e-8 < at <= instant for at in units), 3)

    def test_end_payload_does_not_flush_old_clocks_before_native_mute_commit(self):
        end = self.arrange_end_with_three_due_clocks()
        sent_before = len(self.wire)
        for message in end[:6]:
            self.surface.receive_midi(message)
            self.assertEqual(len(self.wire), sent_before, 'partial raw End must not spend the fresh output budget')
            self.assertEqual(self.events, [], 'partial End cannot evaluate clocks or mutate native state')
            self.assertFalse(self.native.mute)
        self.surface.receive_midi(end[6])
        self.assertTrue(self.native.mute)
        self.assertEqual(len(self.native_calls), 1)
        self.assertEqual(self.events[0], ('native_mute', True),
                         'CC26 must commit native mute before any outgoing frame or old clock supplier')
        for step in range(1, 7):
            self.now[0] = 100.2 + step * .1
            self.surface.update_display()
        samples = [event for event in self.events if event[0] == 'supplier']
        self.assertTrue(samples)
        self.assertTrue(all(event[2] is True for event in samples),
                        'lazy suppliers must observe post-commit native mute state')
        self.assertTrue(any(self.wire[cursor + 2][1] == (0xB0, 41, 127)
                            for cursor in range(sent_before, len(self.wire))
                            if self.wire[cursor][1][:2] == (0xB0, 33)),
                        'a complete post-commit snapshot must report actual native mute')
        self.assert_wire_budget()

    def test_lost_end_commit_never_mutates_and_display_still_flushes_heartbeats(self):
        end = self.arrange_end_with_three_due_clocks()
        sent_before = len(self.wire)
        self.feed(end[:6])
        self.assertEqual(len(self.wire), sent_before)
        self.now[0] = 100.21
        self.surface.update_display()
        self.assertGreater(len(self.wire), sent_before, 'display callbacks must service output during an incomplete touch')
        self.assertEqual(self.native_calls, [])
        self.assertFalse(self.native.mute)
        self.now[0] = 101.3
        self.surface.update_display()
        self.surface.receive_midi(end[6])  # The held contact has expired, not released.
        self.assertEqual(self.native_calls, [])
        self.assertFalse(self.native.mute)
        self.assert_wire_budget()

    def test_malformed_end_never_commits_native_mute(self):
        for kind in ('wrong_revision', 'missing_payload', 'invalid_kind'):
            with self.subTest(kind=kind):
                end = self.arrange_end_with_three_due_clocks()
                if kind == 'wrong_revision':
                    end[-1] = (0xB0, 26, 3)
                elif kind == 'missing_payload':
                    end = end[:3] + end[4:]
                else:
                    end[1] = (0xB0, 21, 4)
                self.feed(end)
                self.now[0] = 100.21
                self.surface.update_display()
                self.assertEqual(self.native_calls, [])
                self.assertFalse(self.native.mute)


if __name__ == '__main__':
    unittest.main()
