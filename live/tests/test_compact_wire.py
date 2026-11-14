"""Promoted wire projection preserves selected values without pacing work."""
import unittest

import test_surface
from LightpadController import compact_wire as wire, protocol as p, timing


class CompactWireTests(unittest.TestCase):
    def test_all_snapshot_fields_in_exact_compact_order_and_inputs_unchanged(self):
        fields = dict.fromkeys(range(91, 109), 0)
        fields.update({91: p.PLAYING, 92: 127, 93: 19, 94: 23,
                       95: 11, 96: 22, 97: 33, 98: 44, 99: 55, 100: 66,
                       101: 77, 102: 88, 103: 31, 104: 37, 105: p.APPLIED,
                       106: 4, 107: 127, 108: 99})
        expected = (p.PLAYING, 127, 19, 11, 22, 33, 44, 55, 66, 77, 88, 31, 4, 127, 99)
        for group in range(p.GROUP_COUNT):
            for revision in (0, 127):
                logical = p.encode_snapshot(group, revision, fields)
                original = list(logical)
                actual = wire.project_wire(iter(logical))
                status = 0xB0 + group
                self.assertEqual(actual, [(status, 33, revision)] +
                    [(status, 40 + index, value) for index, value in enumerate(expected)] +
                    [(status, 55, revision)])
                self.assertEqual(len(actual), 17)
                self.assertEqual(logical, original)
                self.assertEqual(len(logical), 20, 'protected logical receipt fields stay available to OutputPacer')

    def test_clock_projection_preserves_every_quantized_bit_and_global_flags(self):
        clock = timing.Clock(timing.REFERENCE, 127, 30000.0, 29999.984375,
                             2.0, 3.0, 123.4375, 3, 4, True)
        for group in range(8):
            logical = timing.encode_view_timing(group, 127, clock)
            actual = wire.project_wire(logical)
            self.assertEqual(actual[0], (0xB0 + group, 34, 127))
            self.assertEqual([cc for _, cc, _ in actual], [34] + list(range(40, 59)))
            self.assertEqual([v for _, _, v in actual], [v for _, _, v in logical])
        global_clock = timing.Clock(timing.ONE_BAR, 0, 3.5, 3.484375, .5,
                                    3.5, 123.4375, 7, 8, False)
        logical = timing.encode_global_timing(0, global_clock, metronome=True)
        actual = wire.project_wire(logical)
        self.assertEqual(actual[0], (0xBF, 34, 0))
        self.assertEqual(actual[1][2], timing.ONE_BAR | timing.METRONOME)
        self.assertEqual([v for _, _, v in actual], [v for _, _, v in logical])

    def test_ui_is_ten_messages_with_exact_take_preview_and_chord_payload(self):
        for payload in ((0, 7, 127, 0, 4, 0, 0), (0, 127, 127, 0, 0, 127, 1),
                        (2, 127, 7, 127, 2, 0, 0)):
            logical = p.encode_presentation(127, payload)
            actual = wire.project_wire(logical)
            self.assertEqual(actual, [(0xBF, 35, 127)] +
                [(0xBF, 40 + index, value) for index, value in enumerate(payload + (127,))] +
                [(0xBF, 48, 127)])

    def test_metadata_versions_and_nonframe_messages_are_not_reinterpreted(self):
        metadata = p.encode_metadata(False)
        self.assertEqual(wire.project_wire(metadata),
                         [(0xBF, 110, p.VERSION), (0xBF, 111, 1), (0xBF, 112, 0)])
        self.assertEqual(wire.project_wire([]), [])
        for messages in ([(0xBF, 113, 127)], [(0xB0, 43, 127), (0xB0, 44, 1)],
                         [(0xBF, 110, p.VERSION)]):
            self.assertEqual(wire.project_wire(messages), messages)


if __name__ == '__main__':
    unittest.main()
