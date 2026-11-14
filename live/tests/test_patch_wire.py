"""Selector transport stays bounded and uses the shared graphics budget."""
import unittest
import importlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import test_surface
from LightpadController import patch_wire, protocol as p
from LightpadController.output_pacing import OutputPacer
from LightpadController import surface as surface_module


class PatchWireTests(unittest.TestCase):
    def test_all_twelve_pending_mutes_fit_in_presentation(self):
        frame = p.encode_presentation(0, (0, 127, 127, 0, 0, 127, 31, 127))
        self.assertEqual(frame[7][2], 31)

    def test_unchanged_selector_ui_has_delivery_based_heartbeat(self):
        now = [100.]
        snapshot = dict(sources=(), assignments=(0,) * 8)
        presentation = dict(context=127, held_input=127, flash=127, flash_group=127)
        fake = SimpleNamespace(_ui=SimpleNamespace(patch_presentation=lambda: presentation),
            _ui_patch_snapshot=lambda: snapshot, _patch_last=patch_wire.records(snapshot, presentation),
            _patch_ui_at=None, _patch_revision=0, _patch_meta_sent=(0, 0))
        fake._output = OutputPacer(lambda _: None)
        with patch.object(surface_module.time, 'monotonic', side_effect=lambda: now[0]):
            surface_module.LightpadController._publish_patch(fake)
            self.assertIsNone(fake._patch_ui_at, 'queueing is not receipt')
            fake._output.flush(now[0])
            self.assertEqual(fake._patch_ui_at, 100.)
            now[0] = 100.5
            surface_module.LightpadController._publish_patch(fake)
            self.assertNotIn(('data', 'ui'), fake._output.frames)
            now[0] = 101.01
            surface_module.LightpadController._publish_patch(fake)
            self.assertIn(('data', 'ui'), fake._output.frames)

    def test_all_twenty_five_sources_and_full_assignment_mask_are_representable(self):
        sources = tuple(dict(name='Quelle %d' % i, colour=(127, 0, i),
                             midi=bool(i % 2), owners=255, available=True) for i in range(25))
        data = patch_wire.records(dict(sources=sources, assignments=((1 << 25) - 1,) * 8),
                                  dict(context=7, held_input=24, flash=24, flash_group=7))
        self.assertEqual(data['meta',], (0, 25))
        for group in range(8):
            body = data['assignment', group]
            self.assertEqual(sum(v << (7 * i) for i, v in enumerate(body[2:])), (1 << 25) - 1)
        for body in data.values():
            encoded = p.encode_patch(127, body)
            self.assertEqual(len(encoded), 20)
            self.assertEqual(encoded[0], (0xBF, 36, 127))
            self.assertEqual(encoded[-1], (0xBF, 58, 127))
            self.assertTrue(all(0 <= value <= 127 for _, _, value in encoded))

    def test_name_buffer_has_bounded_four_pixel_columns_and_explicit_terminator(self):
        name = 'Ätherische Quelle ' * 30
        columns = patch_wire.name_columns(name)
        self.assertLessEqual(len(columns), 96)
        self.assertTrue(all(0 <= value < 16 for value in columns))
        data = patch_wire.records(dict(sources=(dict(name=name),)), dict(held_input=0))
        chunks = sorted((key[2], value[3:]) for key, value in data.items() if key[0] == 'name')
        received = tuple(value for _, chunk in chunks for value in chunk)
        self.assertEqual(received[:received.index(16)], columns)
        self.assertNotIn(('name', 0, 0), patch_wire.records(dict(sources=(dict(name=name),)), {}))

    def test_modal_frames_coalesce_and_do_not_exceed_shared_window(self):
        sent = []
        pacer = OutputPacer(lambda messages: sent.append(messages))
        for index in range(25):
            pacer.data(('source', index), p.encode_patch(index, (1, index)), 100.)
        replacement = p.encode_patch(26, (1, 0, 99))
        pacer.data(('source', 0), replacement, 100.)
        pacer.data(('ui',), p.encode_patch(27, (4, 0, 127)), 100., urgent=True)
        pacer.flush(100.)
        self.assertLessEqual(len(sent), 4)
        self.assertTrue(any(frame[0][2] == 27 for frame in sent))
        self.assertNotIn(('data', 'source', 0), pacer.frames)
        self.assertIn(replacement, sent)
        before = len(sent)
        pacer.flush(100.01)
        self.assertEqual(len(sent), before)
        pacer.flush(100.1)
        self.assertLessEqual(len(sent) - before, 4)

    def test_name_change_can_discard_only_unissued_old_text(self):
        pacer = OutputPacer(lambda _: None)
        pacer.data(('name', 0, 0), p.encode_patch(1, (3, 0, 0, 1, 16)), 100.)
        pacer.data(('source', 0), p.encode_patch(2, (1, 0)), 100.)
        pacer.discard_data(('name',))
        self.assertEqual(list(pacer.frames), [('data', 'source', 0)])


@unittest.skipUnless(shutil.which('blocksctl'), 'offline blocksctl compiler required')
class PatchPublisherDeviceTests(unittest.TestCase):
    """Real publisher/pacer delivery into the actual compiled selector program."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / 'lightpad/tests'))
        cls.device = importlib.import_module('test_current_device')
        cls.Harness = importlib.import_module('littlefoot_harness').Harness
        with tempfile.TemporaryDirectory(prefix='patch-publisher-device-') as directory:
            binary = Path(directory) / 'patch.bin'
            subprocess.run([shutil.which('blocksctl'), 'compile',
                            str(root / 'lightpad/patch.littlefoot'), '-o', str(binary)],
                           check=True, capture_output=True)
            cls.program = binary.read_bytes()

    def make(self, count=3):
        self.now = 100.
        self.vm = self.Harness(self.program)
        self.vm.call('initialise/v')
        self.vm.call('handleMessage/viii', 274, 0, 0xA9 | (3 << 8) | (12 << 11))
        for group in range(8):
            self.device.snapshot(self.vm, group)
        self.sources = [dict(name='MIC' if index == 0 else 'Input %d' % index,
                             colour=(90, 30, 20), midi=False, owners=0, available=True)
                        for index in range(count)]
        self.presentation = dict(context=127, held_input=127, flash=127, flash_group=127)
        self.delivered = []

        def deliver(messages):
            self.assertEqual(messages[0][:2], (0xBF, 36))
            body = tuple(message[2] for message in messages[1:-1])
            self.delivered.append(body)
            self.device.record(self.vm, 4, 12, body, messages[0][2])

        self.surface = SimpleNamespace(
            _ui=SimpleNamespace(patch_presentation=lambda: self.presentation),
            _ui_patch_snapshot=lambda: dict(sources=tuple(self.sources), assignments=(0,) * 8),
            _patch_last={}, _patch_ui_at=None, _patch_revision=0, _patch_meta_sent=None)
        self.surface._output = OutputPacer(deliver)
        clock = patch.object(surface_module.time, 'monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def step(self, count=1):
        for _ in range(count):
            self.vm.time_ms = round(self.now * 1000)
            surface_module.LightpadController._publish_patch(self.surface)
            self.surface._output.flush(self.now)
            self.vm.call('repaint/v')
            self.now += .1

    def test_first_open_completes_actual_device_model_under_pacing(self):
        self.make(25)
        self.step(50)
        self.assertEqual(self.delivered[0][:2], (0, 25), 'catalog metadata must arrive first')
        self.assertEqual(self.device.markers(self.vm), [[191, 108, 37]])
        self.assertTrue(any(pixel for row in self.vm.leds for pixel in row))
        for group in range(8):
            self.assertTrue(any(body[:2] == (2, group) for body in self.delivered))

    def test_catalog_change_during_initial_seed_still_completes_model(self):
        self.make(2)
        self.step()  # Metadata is delivered, while the catalog is not complete.
        self.sources.append(dict(name='Added', colour=(20, 90, 30), available=True))
        self.step(50)
        self.assertEqual(self.device.markers(self.vm), [[191, 108, 37]])
        newest = max(index for index, body in enumerate(self.delivered) if body[0] == 0)
        following = self.delivered[newest + 1:]
        self.assertEqual({body[1] for body in following if body[0] == 1}, {0, 1, 2})
        self.assertEqual({body[1] for body in following if body[0] == 2}, set(range(8)))

    def test_catalog_count_changes_reseed_unchanged_rows_and_held_name(self):
        self.make(2)
        self.step(30)
        self.device.touch(self.vm, 1, 0, 1, 5.5)
        self.presentation['held_input'] = 0
        self.step(20)
        self.assertTrue(any(pixel == 0xb0b0b0 for row in self.vm.leds[:4] for pixel in row))
        for count in (3, 1):
            with self.subTest(count=count):
                self.delivered.clear()
                self.sources[:] = [dict(name='MIC' if index == 0 else 'Other',
                    colour=(90, 30, 20), available=True) for index in range(count)]
                self.step(30)
                self.assertEqual(self.delivered[0][:2], (0, count))
                self.assertEqual({body[1] for body in self.delivered if body[0] == 1}, set(range(count)))
                self.assertEqual({body[1] for body in self.delivered if body[0] == 2}, set(range(8)))
                self.assertTrue(any(body[:3] == (3, 0, 0) for body in self.delivered),
                                'device cleared its unchanged held-name buffer')
                self.assertTrue(any(pixel == 0xb0b0b0 for row in self.vm.leds[:4] for pixel in row))


if __name__ == '__main__':
    unittest.main()
