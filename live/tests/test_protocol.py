"""Wire framing checks, with mutation counted only after a Command event."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "controller_protocol", Path(__file__).resolve().parents[1] / "LightpadController" / "protocol.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
timing_spec = importlib.util.spec_from_file_location(
    "controller_timing", Path(__file__).resolve().parents[1] / "LightpadController" / "timing.py")
t = importlib.util.module_from_spec(timing_spec)
timing_spec.loader.exec_module(t)


def frame(seq=0, group=0, op=p.SET_MUTE, arg=127):
    return [(0xB0 + group, cc, value) for cc, value in
            zip(range(60, 64), [seq, op, arg, seq])]


class ProtocolTests(unittest.TestCase):
    def test_current_meter_and_metronome_commands_accept_only_the_locked_arguments_on_group_zero(self):
        valid = ((16, (1, 7, 99)), (17, (1, 2, 4, 8, 16)), (18, (0, 127)))
        for opcode, arguments in valid:
            for argument in arguments:
                with self.subTest(opcode=opcode, argument=argument):
                    parser = self.make_parser()
                    command = p.Command(2, 0, opcode, argument)
                    self.assertEqual(self.feed(parser, frame(2, 0, opcode, argument)), [command])
                    parser.complete(command, p.APPLIED)
                    self.assertEqual(self.feed(parser, frame(2, 0, opcode, argument)),
                                     [p.Replay(command, p.APPLIED)])
        invalid = ((16, (0, 100, 127)), (17, (0, 3, 7, 32, 64, 127)),
                   (18, (1, 64, 126)))
        for opcode, arguments in invalid:
            for argument in arguments:
                with self.subTest(opcode=opcode, argument=argument):
                    self.assertEqual(self.feed(self.make_parser(), frame(2, 0, opcode, argument)),
                                     [p.Rejected(0, 2, p.BAD_COMMAND)])
        for group in range(1, 8):
            for opcode, argument in ((16, 4), (17, 4), (18, 127)):
                self.assertEqual(self.feed(self.make_parser(), frame(2, group, opcode, argument)),
                                 [p.Rejected(group, 2, p.BAD_COMMAND)])
        self.assertEqual((p.SET_NUMERATOR, p.SET_DENOMINATOR, p.SET_METRONOME),
                         (16, 17, 18))

    def make_parser(self):
        parser = p.CommandParser()
        self.assertEqual(parser.receive((0xBF, 110, p.VERSION)), ("hello", p.VERSION))
        return parser

    def feed(self, parser, messages):
        return [event for message in messages
                for event in [parser.receive(message)] if event is not None]

    def test_no_dispatch_without_valid_version_handshake(self):
        parser = p.CommandParser()
        self.assertEqual(self.feed(parser, frame()), [])
        parser.receive((0xBF, 110, p.VERSION + 1))
        self.assertEqual(self.feed(parser, frame()), [])
        parser.receive((0xBF, 110, p.VERSION))
        self.assertEqual(self.feed(parser, frame()), [p.Command(0, 0, p.SET_MUTE, 127)])

    def test_legacy_versions_are_rejected_and_fourth_control_uses_opcode_fifteen(self):
        parser = p.CommandParser()
        for version in range(1, p.VERSION):
            self.assertIsNone(parser.receive((0xBF, 110, version)))
            self.assertFalse(parser.ready)
            self.assertEqual(self.feed(parser, frame(0, 0, p.SET_CONTROL_4, 97)), [])
        self.assertEqual(parser.receive((0xBF, 110, p.VERSION)), ("hello", p.VERSION))
        self.assertEqual(self.feed(parser, frame(0, 0, 15, 97)), [p.Command(0, 0, 15, 97)])

    def test_legacy_hello_revokes_current_readiness_and_clear_partial_command(self):
        for version in range(1, p.VERSION):
            with self.subTest(version=version):
                parser = self.make_parser()
                self.assertEqual(self.feed(parser, frame(4)[:2]), [])
                self.assertIsNone(parser.receive((0xBF, 110, version)))
                self.assertFalse(parser.ready)
                self.assertEqual(self.feed(parser, frame(4)[2:]), [])
                self.assertEqual(self.feed(parser, frame(5)), [])
                parser.receive((0xBF, 110, p.VERSION))
                self.assertEqual(self.feed(parser, frame(4)[2:]), [])
                self.assertEqual(self.feed(parser, frame(5)), [p.Command(5, 0, p.SET_MUTE, 127)])

    def test_v2_command_bank_cannot_dispatch_after_current_handshake(self):
        parser = self.make_parser()
        legacy = [(0xB0, cc, value) for cc, value in zip(range(100, 104), (0, p.BEGIN, 0, 0))]
        self.assertEqual(self.feed(parser, legacy), [])
        self.assertEqual(self.feed(parser, frame(1)), [p.Command(1, 0, p.SET_MUTE, 127)])

    def test_malformed_frames_never_dispatch_and_next_frame_recovers(self):
        malformed = [frame()[:3], frame()[1:], frame()[:2] + frame()[3:],
                     [frame()[0], frame()[2], frame()[1], frame()[3]],
                     frame()[:3] + [(0xB0, 63, 1)],
                     frame()[:2] + [(0xB0, 9, 0)] + frame()[2:],
                     frame()[:2] + [(0xB0, 62, 128)] + frame()[3:],
                     frame()[:2] + [(0xB0, 62)] + frame()[2:]]
        for messages in malformed:
            with self.subTest(messages=messages):
                parser = self.make_parser()
                self.assertEqual(self.feed(parser, messages), [])
                self.assertEqual(self.feed(parser, frame(1)), [p.Command(1, 0, p.SET_MUTE, 127)])

    def test_group_frames_interleave_without_cross_group_data(self):
        parser = self.make_parser()
        first, second = frame(5, 0), frame(9, 7, p.SET_VOLUME, 42)
        events = self.feed(parser, [message for pair in zip(first, second) for message in pair])
        self.assertEqual(events, [p.Command(5, 0, p.SET_MUTE, 127), p.Command(9, 7, p.SET_VOLUME, 42)])

    def test_duplicate_replays_cached_rejection_or_success_without_mutation(self):
        for outcome in [p.APPLIED, p.OUTCOME_QUEUED, p.CAPACITY, p.NOT_READY]:
            parser = self.make_parser()
            mutations = []
            for attempt in range(2):
                event = self.feed(parser, frame(127, op=p.ADVANCE, arg=0))[0]
                if isinstance(event, p.Command):
                    mutations.append(event)
                    parser.complete(event, outcome)
                else:
                    self.assertEqual(event, p.Replay(mutations[0], outcome))
            self.assertEqual(len(mutations), 1)
            self.assertEqual(self.feed(parser, frame(0))[0], p.Command(0, 0, p.SET_MUTE, 127))

    def test_changed_content_with_reused_sequence_is_stale(self):
        parser = self.make_parser()
        command = self.feed(parser, frame(4))[0]
        parser.complete(command, p.APPLIED)
        self.assertEqual(self.feed(parser, frame(4, arg=0)), [p.Rejected(0, 4, p.STALE)])

    def test_outstanding_command_cannot_dispatch_twice(self):
        parser = self.make_parser()
        command = self.feed(parser, frame(3))[0]
        self.assertEqual(self.feed(parser, frame(3)), [p.Rejected(0, 3, p.BUSY)])
        self.assertEqual(self.feed(parser, frame(4)), [p.Rejected(0, 4, p.BUSY)])
        self.assertEqual(self.feed(parser, frame(3, arg=0)), [p.Rejected(0, 3, p.STALE)])
        parser.complete(command, p.APPLIED)

    def test_handshake_clears_partial_frame_but_preserves_completed_cache(self):
        parser = self.make_parser()
        command = self.feed(parser, frame(5))[0]
        parser.complete(command, p.APPLIED)
        self.feed(parser, frame(6)[:2])
        parser.receive((0xBF, 110, p.VERSION))
        self.assertEqual(self.feed(parser, frame(6)[2:]), [])
        self.assertEqual(self.feed(parser, frame(5)), [p.Replay(command, p.APPLIED)])

    def test_unknown_op_and_invalid_args_reject_full_frames(self):
        for op, arg, group in [(21, 0, 0), (127, 0, 0), (p.SET_MUTE, 1, 0),
                               (p.BEGIN, 1, 0), (p.TAP_TEMPO, 0, 1)]:
            parser = self.make_parser()
            messages = frame(2, group, op, arg)
            self.assertEqual(self.feed(parser, messages), [p.Rejected(group, 2, p.BAD_COMMAND)])
            self.assertEqual(self.feed(parser, messages), [p.Replay(p.Command(2, group, op, arg), p.BAD_COMMAND)])

    def test_completion_requires_pending_command_and_real_outcome(self):
        parser = self.make_parser()
        command = self.feed(parser, frame())[0]
        with self.assertRaises(ValueError):
            parser.complete(command, p.NONE)
        parser.complete(command, p.APPLIED)
        with self.assertRaises(ValueError):
            parser.complete(command, p.APPLIED)

    def test_logical_snapshot_keeps_each_field_before_wire_projection(self):
        fields = dict.fromkeys(range(91, 109), 0)
        fields.update({91: p.RECORDING, 92: 127, 94: 127, 103: 31, 105: p.APPLIED})
        messages = p.encode_snapshot(7, 127, fields)
        self.assertEqual([cc for _, cc, _ in messages], list(range(40, 60)))
        self.assertEqual(messages[0], (0xB7, 40, 127))
        self.assertEqual(messages[-1], (0xB7, 59, 127))
        self.assertEqual([value for _, _, value in messages[1:-1]],
                         [fields[field] for field in range(91, 109)])
        self.assertEqual(len(messages), 20)
        invalid_state = dict(fields)
        invalid_state[91] = 127
        for invalid in [dict(fields, unexpected=0),
                        {cc: v for cc, v in fields.items() if cc != 93}, invalid_state]:
            with self.assertRaises(ValueError):
                p.encode_snapshot(0, 0, invalid)

    def test_actual_encoded_host_traffic_and_commands_avoid_parameter_controller_bank(self):
        fields = dict.fromkeys(range(91, 109), 0)
        fields.update({91: p.PLAYING, 94: 127, 98: 16, 99: 32, 100: 48,
                       101: 64, 102: 80, 103: 31, 108: 96})
        clock = t.Clock(t.ONE_BAR, 0, 4.0, 1.0, 0.0, 4.0, 120.0, 4, 4, True)
        traffic = p.encode_metadata(True) + t.encode_global_timing(1, clock, metronome=True)
        for opcode, argument in ((16, 99), (17, 16), (18, 127)):
            traffic.extend(frame(1, 0, opcode, argument))
        for group in range(p.GROUP_COUNT):
            snapshot = p.encode_snapshot(group, 17, fields)
            self.assertEqual(dict((cc, value) for _, cc, value in snapshot)[58], fields[108])
            progress = p.encode_progress(group, 13, 127) if group < p.LOOP_GROUP_COUNT else []
            if group < p.LOOP_GROUP_COUNT:
                self.assertEqual(progress, [(0xB0 + group, 43, 13), (0xB0 + group, 44, 127)])
            commands = frame(group, group, p.SET_CONTROL_4, 127)
            self.assertEqual([cc for _, cc, _ in commands], [60, 61, 62, 63])
            traffic.extend(snapshot + progress + commands + (t.encode_view_timing(group, 1, clock) if group < p.LOOP_GROUP_COUNT else []))
        excluded = {6, 38} | set(range(64, 70)) | set(range(96, 102)) | set(range(120, 128))
        self.assertFalse({cc for _, cc, _ in traffic} & excluded)
        self.assertEqual(p.encode_metadata(False), [(0xBF, 110, p.VERSION), (0xBF, 111, 1), (0xBF, 112, 0)])

    def test_completion_receipt_preserves_take_id_and_replays_without_dispatch(self):
        parser = self.make_parser()
        command = self.feed(parser, frame(9, 3, p.ACK_COMPLETION, 127))[0]
        self.assertEqual(command, p.Command(9, 3, p.ACK_COMPLETION, 127))
        parser.complete(command, p.APPLIED)
        self.assertEqual(self.feed(parser, frame(9, 3, p.ACK_COMPLETION, 127)),
                         [p.Replay(command, p.APPLIED)])

    def test_encoding_rejects_out_of_range_and_boolean_values(self):
        for group, phase, countdown in [(8, 0, 127), (0, -1, 127), (0, 0, 128), (0, True, 127)]:
            with self.assertRaises(ValueError):
                p.encode_progress(group, phase, countdown)
        self.assertEqual(p.encode_metadata(True), [(0xBF, 110, p.VERSION), (0xBF, 111, 1), (0xBF, 112, 127)])

    def test_presentation_is_atomic_and_rejects_invalid_views_and_bindings(self):
        payload = (2, 7, 7, 126, 2, 127, 1)
        self.assertEqual(p.encode_presentation(127, payload),
                         [(0xBF, cc, value) for cc, value in
                          zip(range(20, 30), (127,) + payload + (127, 127))])
        for bad in [(4, 0, 0, 0, 0, 0, 0), (0, 12, 127, 0, 0, 0, 0),
                    (0, 127, 8, 0, 0, 0, 0), (0, 127, 127, 0, 32, 0, 0),
                    (0, 127, 127, 0, 0, 0, 32), (True, 0, 0, 0, 0, 0, 0),
                    payload[:-1]]:
            with self.assertRaises(ValueError):
                p.encode_presentation(0, bad)

    def test_preview_flags_keep_the_same_atomic_ten_message_frame(self):
        self.assertEqual((p.RECORD_PREVIEW, p.ADVANCE_PREVIEW), (4, 8))
        for flag in (p.RECORD_PREVIEW, p.ADVANCE_PREVIEW):
            payload = (0, 7, 127, 0, flag, 0, 0)
            frame = p.encode_presentation(5, payload)
            self.assertEqual(frame, [(0xBF, cc, value) for cc, value in
                             zip(range(20, 30), (5,) + payload + (127, 5))])
        for payload in ((0, 0, 127, 0, 12, 0, 0),
                        (1, 0, 127, 0, p.RECORD_PREVIEW, 0, 0),
                        (0, 127, 127, 0, p.RECORD_PREVIEW, 0, 0),
                        (0, 0, 127, 0, p.ADVANCE_PREVIEW, 1, 0),
                        (0, 0, 127, 0, p.ADVANCE_PREVIEW, 0, 1)):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                p.encode_presentation(5, payload)

    def test_recognizable_malformed_hello_closes_readiness(self):
        for message in ((0xBF, 110), (0xBF, 110, True), (0xBF, 110, 128)):
            parser = self.make_parser()
            parser.receive(message)
            self.assertFalse(parser.ready)


if __name__ == "__main__":
    unittest.main()
