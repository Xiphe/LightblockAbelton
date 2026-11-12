"""Normal MIDI callbacks with the real controller and fake native observations."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import compact_assertions as wire
from unittest.mock import patch

import test_surface
import test_audio_controller as controller_tests
from LightpadController import configuration as config, protocol as p
from LightpadController import surface as module


class AudioSurfaceTests(unittest.TestCase):
    def make(self, bad_manifest=False):
        self.now = 100.0
        monotonic = patch.object(module.time, "monotonic", side_effect=lambda: self.now)
        monotonic.start()
        self.addCleanup(monotonic.stop)
        helper = controller_tests.AudioControllerTests()
        helper.make(enabled=False)
        self.addCleanup(helper.doCleanups)
        self.helper = helper
        manifest_path = Path(helper.song.file_path).with_suffix(".json")
        manifest = dict(helper.fixture.manifest)
        if bad_manifest:
            manifest["set_path"] += ".other"
        manifest_path.write_text(json.dumps(manifest))
        settings = patch.multiple(config, AUDIO_CONTROLLER_MANIFEST=str(manifest_path),
            SYNC_UI_EVIDENCE=None, VALIDATED_AUDIO_FEATURES=("record", "cancel", "wrap", "accept_next", "advance"))
        settings.start()
        self.addCleanup(settings.stop)
        native = patch.object(module, "Live", SimpleNamespace(
            Song=SimpleNamespace(Quantization=SimpleNamespace(q_bar=4)),
            Track=SimpleNamespace(Track=SimpleNamespace(monitoring_states=SimpleNamespace(OFF=2)))))
        native.start()
        self.addCleanup(native.stop)
        instance = SimpleNamespace(song=helper.song, sent=[], logs=[])
        controller = module.LightpadController(instance)
        self.logical_sent = wire.capture_logical_delivery(controller)
        self.addCleanup(controller.disconnect)
        return controller, instance

    def pump(self, surface):
        for _ in range(20):
            self.now += 0.05
            surface.update_display()

    @staticmethod
    def command(surface, op, seq=1, arg=0):
        for cc, value in zip(range(60, 64), (seq, op, arg, seq)):
            surface.receive_midi((0xB0, cc, value))

    def test_opt_in_startup_is_write_free_and_handshake_sends_real_global_clock(self):
        surface, instance = self.make()
        self.assertIsNotNone(surface._engine, instance.logs)
        self.assertEqual(self.helper.calls, [])
        self.assertEqual(instance.sent, [])
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.pump(surface)
        self.assertIn((0xBF, 40, 5), instance.sent)
        self.assertEqual(self.helper.calls, [])
        self.assertEqual(surface._last[0][103] & p.CAP_RECORDING, p.CAP_RECORDING)
        self.command(surface, p.BEGIN)
        self.assertEqual(surface._last[0][106], 1)
        instance.sent.clear()
        self.pump(surface)
        self.assertIn((0xBF, 40, 5), instance.sent)
        self.assertIn((0xB0, 41, 1), instance.sent)
        self.assertEqual(surface._engine.session.take.state, "queued")
        self.assertEqual(self.helper.song.current_song_time, 1.0)

    def test_cancel_ack_waits_for_native_cleanup_even_after_peer_disconnect(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.command(surface, p.BEGIN)
        take = surface._engine.session.take
        slot = self.helper.slot(take.slot)
        self.helper.song.current_song_time = 4
        slot.begin_native(4)
        surface.update_display()
        self.pump(surface)
        self.assertTrue(any(self.logical_sent[cursor + 14:cursor + 16] ==
                            [(0xB0, 54, 1), (0xB0, 55, p.OUTCOME_QUEUED)]
                            for cursor, message in enumerate(self.logical_sent)
                            if message[:2] == (0xB0, 40)),
                        "Begin protected logical delivery must complete on the compact wire before Abort")
        self.command(surface, p.ABORT, seq=2)
        self.assertEqual(surface._outcomes[0], (1, p.OUTCOME_QUEUED))
        self.assertEqual(surface._last[0][106], 1)
        self.assertTrue(slot.has_clip)
        surface.receive_midi((0xBF, 110, 2))
        self.assertFalse(surface._parser.ready)
        slot.track.quiet_native()
        instance.sent.clear()
        surface.update_display()
        self.assertEqual(instance.sent, [])
        self.assertEqual(surface._outcomes[0], (2, p.APPLIED))
        self.assertFalse(slot.has_clip)
        self.assertFalse(slot.track.arm)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.assertEqual(surface._last[0][106], 0)

    def test_invalid_explicit_manifest_never_falls_back_to_named_group_mutations(self):
        surface, instance = self.make(bad_manifest=True)
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.command(surface, p.SET_MUTE, arg=0)
        self.assertTrue(self.helper.fixture.groups[0].group.mute)
        self.assertEqual(surface._last[0][91], p.UNAVAILABLE)
        self.assertEqual(surface._last[0][105], p.NOT_READY)
        self.assertEqual(self.helper.calls, [])

    def test_phase_only_changes_do_not_flood_full_snapshots(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.command(surface, p.BEGIN)
        instance.sent.clear()
        for beat in (1.5, 2.0, 2.5):
            self.helper.song.current_song_time = beat
            surface._publish()
        self.assertFalse(any(cc == 33 for _, cc, _ in instance.sent))

    def test_invalid_actual_clock_emits_invalidation_without_crashing_display(self):
        surface, instance = self.make()
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.pump(surface)
        self.assertIn((0xBF, 40, 5), instance.sent)
        self.helper.song.current_song_time = float("nan")
        instance.sent.clear()
        self.pump(surface)
        self.assertIn((0xBF, 40, 0), instance.sent)
        self.assertFalse(any(status == 0xBF and cc == 113 for status, cc, _ in instance.sent))


if __name__ == "__main__":
    unittest.main()
