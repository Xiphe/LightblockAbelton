"""Budget timestamps follow expensive native reads, measured at actual MIDI sends."""

from types import SimpleNamespace
import unittest
import compact_assertions as compact
from unittest.mock import patch

import test_surface_pacing as pacing_helpers
from LightpadController import configuration as config, protocol as p
from LightpadController import surface as module


class SurfaceFlushTimeTests(unittest.TestCase):
    def test_slow_display_reads_cannot_replenish_budget_for_adjacent_receive(self):
        now, wire = [100.0], []

        class SlowNativeReads(pacing_helpers.EightClockEngine):
            reads_remaining = 0

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                if self.reads_remaining:
                    self.reads_remaining -= 1
                    now[0] += .03
                return super().snapshot(group, sequence, outcome)

            def meter_state(self):
                return 4, 4, False

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0, tempo=120, signature_numerator=4,
                               signature_denominator=4, metronome=False)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: wire.append((now[0], message))))
        engine = SlowNativeReads()
        with patch.object(config, 'AUDIO_CONTROLLER_MANIFEST', None), \
                patch.object(module.time, 'monotonic', side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            now[0] = 100.1
            engine.reads_remaining = 8
            surface.update_display()
            display_finished = now[0]
            self.assertGreater(display_finished - 100.1, .09,
                               'native reads must advance beyond a complete pacing window')
            now[0] += .001
            surface.receive_midi((0xB7, 39, 0))

        frames = []
        cursor = 0
        while cursor < len(wire):
            instant, (status, cc, revision) = wire[cursor]
            if cc not in (33, 34, 35):
                self.assertEqual(status, 0xBF)
                self.assertIn(cc, (110, 111, 112, 113))
                cursor += 1
                continue
            size = compact.size(cc)
            frame = wire[cursor:cursor + size]
            self.assertEqual([at for at, _ in frame], [instant] * size)
            self.assertEqual([message[0] for _, message in frame], [status] * size)
            self.assertEqual([message[1] for _, message in frame], compact.controllers(cc))
            self.assertEqual(frame[-1][1][2], revision)
            frames.append(instant)
            cursor += size

        self.assertEqual(sum(abs(at - display_finished) < 1e-8 for at in frames), 3,
                         'the slow display must exercise a full actual send batch')
        for instant in frames:
            window = [at for at in frames if instant - .09 + 1e-8 < at <= instant]
            self.assertLessEqual(len(window), 3,
                                 'actual wire sends exceed 3/90ms after costly reads: %r' % window)


if __name__ == '__main__':
    unittest.main()
