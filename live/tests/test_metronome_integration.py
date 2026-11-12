"""Global controls through the real controller and retained native fixture."""

import unittest

import test_audio_controller as fixture_helpers
from LightpadController import protocol as p
from LightpadController.audio_controller import AudioController


class ObservedSong:
    """Native-song boundary that records setters, including transport writes."""
    def __init__(self, source):
        object.__setattr__(self, "writes", [])
        for name, value in vars(source).items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "metronome", False)

    def __setattr__(self, name, value):
        self.writes.append((name, value))
        object.__setattr__(self, name, value)


class MetronomeIntegrationTests(unittest.TestCase):
    def make(self):
        helper = fixture_helpers.AudioControllerTests()
        helper.make(enabled=False)
        self.addCleanup(helper.doCleanups)
        self.helper = helper
        self.song = ObservedSong(helper.song)
        helper.song = helper.fixture.song = self.song
        self.controller = AudioController(helper.fixture, probe=True, auto_enable=True)
        helper.controller = self.controller
        return self.controller

    def test_meter_edits_preserve_playing_accepted_reference_and_audio_identity(self):
        controller = self.make()
        lease, clip, _ = self.helper.accept(length=8)
        clip.file_path = "/fixture/accepted-reference.aif"
        reference = controller.session.groups[0].reference
        accepted = tuple(controller.session.groups[0].accepted)
        before = (clip.loop_start, clip.loop_end, clip.start_time, clip.file_path,
                  clip.is_playing, clip.is_recording)
        self.song.writes.clear()
        self.helper.calls.clear()
        for sequence, opcode, argument in ((20, p.SET_METRONOME, 127),
                                            (21, p.SET_NUMERATOR, 7),
                                            (22, p.SET_DENOMINATOR, 8)):
            self.assertEqual(controller.dispatch(p.Command(sequence, 0, opcode, argument)), p.APPLIED)
        self.assertEqual(controller.meter_state(), (7, 8, True))
        self.assertIs(controller.session.groups[0].reference, reference)
        self.assertEqual(tuple(controller.session.groups[0].accepted), accepted)
        self.assertIs(self.helper.slot(lease).clip, clip)
        self.assertEqual((clip.loop_start, clip.loop_end, clip.start_time, clip.file_path,
                          clip.is_playing, clip.is_recording), before)
        self.assertTrue(self.song.is_playing)
        self.assertEqual(self.helper.calls, [], "meter edits must not fire, stop, arm, disarm or delete")
        self.assertEqual([name for name, _ in self.song.writes],
                         ["metronome", "signature_numerator", "signature_denominator"])

    def test_meter_state_mirrors_external_changes_even_when_capture_globals_are_unready(self):
        controller = self.make()
        self.song.signature_numerator, self.song.signature_denominator = 7, 8
        self.song.metronome = True
        self.song.record_mode = True
        self.song.is_playing = False
        self.helper.unrelated.arm = True
        self.song.writes.clear()
        self.helper.calls.clear()
        self.assertEqual(controller.meter_state(), (7, 8, True))
        self.assertEqual(controller.dispatch(p.Command(1, 0, p.SET_NUMERATOR, 5)), p.APPLIED)
        self.assertEqual(controller.meter_state(), (5, 8, True))
        self.assertEqual(self.song.writes, [("signature_numerator", 5)])
        self.assertEqual(self.helper.calls, [])
        self.assertTrue(self.song.record_mode)
        self.assertFalse(self.song.is_playing)
        self.song.signature_denominator = 3
        self.song.writes.clear()
        self.assertIsNone(controller.meter_state())
        self.assertEqual(self.song.writes, [])

    def test_controller_taps_use_selected_denominator_and_external_change_resets_history(self):
        controller = self.make()
        timer = [100.0]
        controller.controls.now = lambda: timer[0]
        self.song.is_playing = False
        self.assertEqual(controller.dispatch(p.Command(1, 0, p.SET_DENOMINATOR, 8)), p.APPLIED)
        self.song.writes.clear()
        self.helper.calls.clear()
        self.assertEqual(controller.dispatch(p.Command(2, 0, p.TAP_TEMPO, 0)), p.APPLIED)
        self.assertEqual(self.song.writes, [])
        timer[0] = 100.25
        self.assertEqual(controller.dispatch(p.Command(3, 0, p.TAP_TEMPO, 0)), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)
        self.assertEqual(self.song.writes, [("tempo", 120.0)])
        self.song.signature_denominator = 2
        self.assertEqual(controller.meter_state(), (4, 2, False))
        self.song.writes.clear()
        timer[0] = 100.5
        self.assertEqual(controller.dispatch(p.Command(4, 0, p.TAP_TEMPO, 0)), p.APPLIED)
        self.assertEqual(self.song.writes, [])
        timer[0] = 101.5
        self.assertEqual(controller.dispatch(p.Command(5, 0, p.TAP_TEMPO, 0)), p.APPLIED)
        self.assertAlmostEqual(self.song.tempo, 120.0)
        self.assertEqual(self.song.writes, [("tempo", 120.0)])
        self.assertFalse(self.song.is_playing)
        self.assertEqual(self.helper.calls, [], "native Song.tap_tempo must not be invoked")

    def test_changed_set_or_retained_track_rejects_global_commands_without_mutation(self):
        for change in ("set_path", "track_identity"):
            with self.subTest(change=change):
                controller = self.make()
                if change == "set_path":
                    self.song.file_path += ".other"
                else:
                    self.song.tracks[1] = object()
                self.song.writes.clear()
                self.helper.calls.clear()
                self.assertIsNone(controller.meter_state())
                for opcode, argument in ((p.SET_METRONOME, 127), (p.SET_NUMERATOR, 7),
                                          (p.SET_DENOMINATOR, 8), (p.TAP_TEMPO, 0)):
                    self.assertEqual(controller.dispatch(p.Command(1, 0, opcode, argument)), p.NOT_READY)
                self.assertEqual(self.song.writes, [])
                self.assertEqual(self.helper.calls, [])

    def test_global_commands_require_group_zero_even_when_other_groups_are_bound(self):
        controller = self.make()
        self.song.writes.clear()
        for opcode, argument in ((p.SET_METRONOME, 127), (p.SET_NUMERATOR, 7),
                                  (p.SET_DENOMINATOR, 8), (p.TAP_TEMPO, 0)):
            self.assertEqual(controller.dispatch(p.Command(1, 1, opcode, argument)), p.BAD_COMMAND)
        self.assertEqual(self.song.writes, [])
        self.assertEqual(self.helper.calls, [])

    def test_stopped_global_controls_ignore_unrelated_arming_and_touch_only_requested_fields(self):
        controller = self.make()
        self.song.is_playing = False
        self.helper.unrelated.arm = True
        self.song.writes.clear()
        self.helper.calls.clear()
        self.assertEqual(controller.dispatch(p.Command(1, 0, p.SET_METRONOME, 127)), p.APPLIED)
        self.assertEqual(controller.meter_state(), (4, 4, True))
        self.assertEqual(controller.dispatch(p.Command(2, 0, p.SET_NUMERATOR, 7)), p.APPLIED)
        self.assertEqual(controller.meter_state(), (7, 4, True))
        self.assertEqual(controller.dispatch(p.Command(3, 0, p.SET_DENOMINATOR, 8)), p.APPLIED)
        self.assertEqual(controller.meter_state(), (7, 8, True))
        self.assertEqual(self.song.writes, [("metronome", True),
                                          ("signature_numerator", 7),
                                          ("signature_denominator", 8)])
        self.assertFalse(self.song.is_playing)
        self.assertEqual(self.helper.calls, [], "global controls cannot fire/stop/arm or call native tap")


if __name__ == "__main__":
    unittest.main()
