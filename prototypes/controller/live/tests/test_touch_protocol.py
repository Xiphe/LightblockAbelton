"""Raw touch framing/lifecycle checks without Live, clocks or MIDI hardware."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "controller_touch_protocol", Path(__file__).resolve().parents[1]
    / "LightpadController" / "touch_protocol.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def expected_touch(kind,index,x,y,seq):
    return p.Touch(kind,index,x,y,seq,0,False,False,0,0,False,False)


def frame(index=0, seq=0, kind=p.START, x=128, y=256):
    values = [seq, kind, x & 127, x >> 7, y & 127, y >> 7, seq]
    return [(0xB0 + index, cc, value) for cc, value in zip(range(20, 27), values)]


def feed(parser, messages):
    return [event for message in messages
            for event in [parser.receive(message)] if event is not None]


class TouchProtocolTests(unittest.TestCase):
    def test_readiness_is_explicit_and_start_is_required_after_enabling(self):
        parser = p.TouchParser()
        self.assertEqual(feed(parser, frame()), [])
        parser.reset(ready=True)
        self.assertEqual(feed(parser, frame(seq=1, kind=p.MOVE)), [])
        self.assertEqual(feed(parser, frame(seq=2, kind=p.END)), [])
        self.assertEqual(feed(parser, frame(seq=3)), [expected_touch(p.START, 0, 1.0, 2.0, 3)])
        parser.reset(ready=False)
        self.assertEqual(feed(parser, frame(seq=4)), [])

    def test_every_finger_channel_decodes_exact_coordinate_boundaries(self):
        for index in range(16):
            for x, y in ((0, 0), (127, 128), (128, 127), (1919, 1919)):
                with self.subTest(index=index, x=x, y=y):
                    parser = p.TouchParser(ready=True)
                    self.assertEqual(feed(parser, frame(index, 17, x=x, y=y)),
                                     [expected_touch(p.START, index, x / 128.0, y / 128.0, 17)])

    def test_no_event_until_complete_matching_commit(self):
        parser = p.TouchParser(ready=True)
        messages = frame(seq=5)
        self.assertEqual(feed(parser, messages[:-1]), [])
        self.assertEqual(parser.receive(messages[-1]), expected_touch(p.START, 0, 1.0, 2.0, 5))
        self.assertIsNone(parser.receive(messages[-1]))

    def test_interleaved_fingers_have_independent_atomic_frames(self):
        parser = p.TouchParser(ready=True)
        frames = [frame(index, index, x=index * 100, y=1919-index) for index in range(16)]
        events = feed(parser, [messages[part] for part in range(7) for messages in frames])
        self.assertEqual(events, [expected_touch(p.START, index, index * 100 / 128.0,
                                         (1919-index) / 128.0, index) for index in range(16)])

    def test_unrelated_cc_interrupts_only_its_finger(self):
        parser = p.TouchParser(ready=True)
        first, other = frame(0, 1), frame(15, 2)
        self.assertEqual(feed(parser, first[:3] + other[:3]), [])
        self.assertIsNone(parser.receive((0xB0, 99, 0)))
        self.assertEqual(feed(parser, first[3:]), [])
        self.assertEqual(feed(parser, other[3:]), [expected_touch(p.START, 15, 1.0, 2.0, 2)])

    def test_missing_reordered_or_duplicate_body_cc_cannot_emit_start(self):
        original = frame(seq=9)
        variants = [original[:3] + original[4:],
                    original[:2] + [original[3], original[2]] + original[4:],
                    original[:3] + [original[2]] + original[3:]]
        for messages in variants:
            with self.subTest(messages=messages):
                parser = p.TouchParser(ready=True)
                self.assertEqual(feed(parser, messages), [])
                self.assertEqual(feed(parser, frame(seq=10, kind=p.END)), [])
                self.assertEqual(len(feed(parser, frame(seq=11))), 1)

    def test_new_begin_replaces_partial_frame_without_emitting_old_start(self):
        parser = p.TouchParser(ready=True)
        self.assertEqual(feed(parser, frame(seq=1)[:4]), [])
        self.assertEqual(feed(parser, frame(seq=2, x=300)),
                         [expected_touch(p.START, 0, 300 / 128.0, 2.0, 2)])

    def test_revision_mismatch_does_not_establish_lifecycle(self):
        parser = p.TouchParser(ready=True)
        messages = frame(seq=3)
        messages[-1] = (0xB0, 26, 4)
        self.assertEqual(feed(parser, messages), [])
        self.assertEqual(feed(parser, frame(seq=5, kind=p.MOVE)), [])

    def test_kind_and_out_of_surface_coordinates_are_rejected_atomically(self):
        for kind, x, y in ((0, 0, 0), (4, 128, 128), (127, 0, 0),
                           (p.START, 1920, 0), (p.START, 0, 1920),
                           (p.START, 16383, 16383)):
            with self.subTest(kind=kind, x=x, y=y):
                parser = p.TouchParser(ready=True)
                self.assertEqual(feed(parser, frame(seq=1, kind=kind, x=x, y=y)), [])
                self.assertEqual(feed(parser, frame(seq=2, kind=p.END)), [])

    def test_malformed_midi_interrupts_frame_and_does_not_raise(self):
        malformed = ((0xB0, 22), (0xB0, 22, 1, 2), (0xB0, 128, 0),
                     (0xB0, 22, 128), (0xB0, 22, -1), (0xB0, 22, True),
                     (0xB0, 22, 1.0), (0xB0, '22', 0))
        for message in malformed:
            with self.subTest(message=message):
                parser = p.TouchParser(ready=True)
                messages = frame(seq=3)
                self.assertEqual(feed(parser, messages[:2]), [])
                self.assertIsNone(parser.receive(message))
                self.assertEqual(feed(parser, messages[2:]), [])
        parser = p.TouchParser(ready=True)
        for message in (None, 1, object(), (), 'bad', (256, 22, 0), (-1, 22, 0)):
            self.assertIsNone(parser.receive(message))

    def test_other_midi_message_types_are_not_finger_cc_interruptions(self):
        parser = p.TouchParser(ready=True)
        messages = frame(seq=1)
        self.assertEqual(feed(parser, messages[:2]), [])
        self.assertIsNone(parser.receive((0x90, 22, 40)))
        self.assertEqual(feed(parser, messages[2:]), [expected_touch(p.START, 0, 1.0, 2.0, 1)])

    def test_duplicate_and_conflicting_latest_sequence_cannot_release_or_restart(self):
        parser = p.TouchParser(ready=True)
        original = frame(seq=8)
        self.assertEqual(len(feed(parser, original)), 1)
        self.assertEqual(feed(parser, original), [])
        self.assertEqual(feed(parser, frame(seq=8, x=1000)), [])
        self.assertEqual(feed(parser, frame(seq=8, kind=p.END)), [])
        self.assertEqual(feed(parser, frame(seq=9, kind=p.MOVE, x=300)),
                         [expected_touch(p.MOVE, 0, 300 / 128.0, 2.0, 9)])
        self.assertEqual(len(feed(parser, frame(seq=10, kind=p.END))), 1)
        self.assertEqual(feed(parser, frame(seq=10, kind=p.START)), [])

    def test_sequence_wrap_and_lost_move_gaps_preserve_real_lifecycle(self):
        parser = p.TouchParser(ready=True)
        events = []
        for seq, kind in ((126, p.START), (127, p.MOVE), (0, p.MOVE), (3, p.END), (4, p.START)):
            events.extend(feed(parser, frame(seq=seq, kind=kind)))
        self.assertEqual([(e.seq, e.kind) for e in events],
                         [(126, p.START), (127, p.MOVE), (0, p.MOVE), (3, p.END), (4, p.START)])

    def test_lost_start_never_allows_move_end_and_next_start_recovers(self):
        parser = p.TouchParser(ready=True)
        self.assertEqual(feed(parser, frame(seq=41, kind=p.MOVE)), [])
        self.assertEqual(feed(parser, frame(seq=42, kind=p.END)), [])
        self.assertEqual(len(feed(parser, frame(seq=43))), 1)
        self.assertEqual(len(feed(parser, frame(seq=44, kind=p.END))), 1)

    def test_lost_end_new_start_is_authoritative_without_synthetic_release(self):
        parser = p.TouchParser(ready=True)
        events = feed(parser, frame(seq=10))
        events += feed(parser, frame(seq=11, kind=p.MOVE))
        events += feed(parser, frame(seq=13, x=512))  # End12 was lost.
        self.assertEqual([event.kind for event in events], [p.START, p.MOVE, p.START])
        self.assertEqual(events[-1], expected_touch(p.START, 0, 4.0, 2.0, 13))
        self.assertEqual(len(feed(parser, frame(seq=14, kind=p.MOVE))), 1)
        self.assertEqual(len(feed(parser, frame(seq=15, kind=p.END))), 1)
        self.assertEqual(feed(parser, frame(seq=16, kind=p.END)), [])

    def test_expire_aborts_only_one_finger_retains_replay_guard_and_never_emits_end(self):
        parser = p.TouchParser(ready=True)
        original = frame(0, 1)
        self.assertEqual(len(feed(parser, original)), 1)
        self.assertEqual(len(feed(parser, frame(1, 1))), 1)
        self.assertEqual(feed(parser, frame(0, 2, kind=p.END)[:3]), [])
        self.assertIsNone(parser.expire(0))
        self.assertEqual(feed(parser, frame(0, 2, kind=p.END)[3:]), [])
        self.assertEqual(feed(parser, original), [])
        self.assertEqual(feed(parser, frame(0, 2, kind=p.MOVE)), [])
        self.assertEqual(len(feed(parser, frame(1, 2, kind=p.END))), 1)
        self.assertEqual(len(feed(parser, frame(0, 3))), 1)

    def test_hello_closes_and_clears_every_lifecycle_frame_and_sequence(self):
        parser = p.TouchParser(ready=True)
        self.assertEqual(len(feed(parser, frame(0, 1))), 1)
        self.assertEqual(feed(parser, frame(15, 2)[:4]), [])
        self.assertIsNone(parser.receive((0xBF, 110, 4)))
        self.assertFalse(parser.ready)
        self.assertEqual(feed(parser, frame(0, 2, kind=p.END)), [])
        parser.reset(ready=True)
        self.assertEqual(feed(parser, frame(15, 2)[4:]), [])
        self.assertEqual(feed(parser, frame(0, 2, kind=p.MOVE)), [])
        self.assertEqual(len(feed(parser, frame(0, 1))), 1)  # Old replay guard was reset.

    def test_invalid_version_or_malformed_hello_also_fails_closed(self):
        for hello in ((0xBF, 110, 3), (0xBF, 110, 128), (0xBF, 110)):
            with self.subTest(hello=hello):
                parser = p.TouchParser(ready=True)
                self.assertEqual(len(feed(parser, frame())), 1)
                self.assertIsNone(parser.receive(hello))
                self.assertFalse(parser.ready)
                self.assertEqual(feed(parser, frame(seq=1, kind=p.END)), [])

    def test_expire_and_readiness_arguments_reject_invalid_types_and_indices(self):
        parser = p.TouchParser(ready=True)
        for index in (-1, 16, True, 1.0, None):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    parser.expire(index)
        for ready in (0, 1, None, 'ready'):
            with self.assertRaises(ValueError):
                parser.reset(ready=ready)


if __name__ == '__main__':
    unittest.main()
