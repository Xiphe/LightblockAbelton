"""Dynamic native-grid orchestration with fake observations, not Live evidence."""

import math
from types import SimpleNamespace
import unittest

import test_audio_controller as controller_tests
import test_finish_length as finish_tests
from LightpadController import protocol as p
from LightpadController.audio_controller import AudioController
from LightpadController.audio_fixture import AudioFixture


QUANTIZATIONS = {'q_bar': 4, 'q_half': 5, 'q_quarter': 7, 'q_eight': 9, 'q_sixtenth': 11}
FEATURES = ('record', 'cancel', 'wrap', 'accept_next', 'advance', 'exact_length')


class DynamicRecordingTests(unittest.TestCase):
    def make(self, quantizations=None):
        helper = controller_tests.AudioControllerTests()
        helper.make(enabled=False)
        self.addCleanup(helper.doCleanups)
        self.helper, self.song, self.calls = helper, helper.song, helper.calls

        def native_time():
            # Experiment 10's empirical one-based fields: 60 ticks per
            # sixteenth, denominator-relative beats, independently fixed zero.
            length = self.song.signature_numerator * 4.0 / self.song.signature_denominator
            bar = math.floor(self.song.current_song_time / length)
            phase_ticks = math.floor((self.song.current_song_time - bar * length) * 240 + 1e-7)
            beat_ticks = 960 // self.song.signature_denominator
            beat, within = divmod(phase_ticks, beat_ticks)
            subdivision, ticks = divmod(within, 60)
            return SimpleNamespace(bars=bar + 1, beats=beat + 1,
                                   sub_division=subdivision + 1, ticks=ticks + 1)

        self.song.get_current_beats_song_time = native_time
        self.fixture = AudioFixture(self.song, helper.fixture.manifest, 4, 2,
            native_bar_clock=True,
            native_quantizations=QUANTIZATIONS if quantizations is None else quantizations)
        self.controller = AudioController(self.fixture, probe=False, validated_features=FEATURES)
        self.controller.enable()
        self.sequence = 0

    def dispatch(self, operation, argument=0):
        self.sequence += 1
        return self.controller.dispatch(p.Command(self.sequence, 0, operation, argument))

    def at(self, beat):
        self.song.current_song_time = beat
        for layer in self.controller.session.groups[0].accepted:
            clip = self.fixture.groups[0].layers[layer.slot.layer].clip_slots[layer.slot.row].clip
            clip.playing_position = (beat - clip.start_time) % (clip.loop_end - clip.loop_start)

    def slot(self, take):
        return self.fixture.groups[0].layers[take.slot.layer].clip_slots[take.slot.row]

    def observe_start(self, take, beat):
        self.at(beat)
        slot = self.slot(take)
        clip = finish_tests.ObservedClip(beat, self.calls, slot.track.name)
        slot.clip, slot.is_triggered = clip, False
        slot.track.fired_slot_index, slot.track.playing_slot_index = -1, slot.row
        self.controller.tick()
        self.assertEqual(take.state, 'recording')
        return clip

    def observe_finish(self, take, beat, padded_length):
        self.at(beat)
        slot, clip = self.slot(take), self.slot(take).clip
        recorded_duration = beat - clip.start_time
        clip.sample_length = round(recorded_duration * .5 * clip.sample_rate)
        clip.start_time, clip._native_loop_end = beat, padded_length
        clip.is_recording, clip.is_playing, clip.playing_position = False, True, 0.0
        slot.is_triggered = False
        slot.track.fired_slot_index, slot.track.playing_slot_index = -1, slot.row
        self.controller.tick()
        return clip

    def reference(self):
        self.assertEqual(self.dispatch(p.BEGIN), p.OUTCOME_QUEUED)
        take = self.controller.session.take
        self.assertEqual(take.schedule.start_at, 4)
        clip = self.observe_start(take, 4)
        self.at(7)
        self.assertEqual(self.dispatch(p.ACCEPT), p.OUTCOME_QUEUED)
        self.observe_finish(take, 8, 4)
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4])
        self.assertEqual(clip.start_time, 8)
        return clip

    def begin_in_seven_eight(self):
        reference = self.reference()
        self.song.signature_numerator, self.song.signature_denominator = 7, 8
        self.at(11.2)
        self.assertEqual(self.dispatch(p.BEGIN), p.OUTCOME_QUEUED)
        take = self.controller.session.take
        self.assertEqual((take.schedule.queued_at, take.schedule.start_at), (11.2, 12))
        self.assertEqual(self.calls[-1], ('fire', self.slot(take).track.name, 0,
                                         {'launch_quantization': 5}))
        return reference, take

    def test_changed_meter_keeps_reference_target_and_normalizes_only_new_padded_take(self):
        self.make()
        reference, take = self.begin_in_seven_eight()
        clip = self.observe_start(take, 12)
        self.at(13)
        self.assertEqual(self.dispatch(p.ACCEPT), p.OUTCOME_QUEUED)
        self.assertEqual(take.schedule.start_at, 14)
        self.assertEqual(self.calls[-1], ('fire', self.slot(take).track.name, 0,
                                         {'launch_quantization': 4}))
        before = (reference.loop_start, reference.loop_end, reference.start_time,
                  tuple(reference.loop_end_writes), reference.file_path)
        self.observe_finish(take, 14, 3.5)
        self.assertEqual(clip.loop_end_writes, [2])
        self.assertEqual(clip.loop_end, 2)
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4, 2])
        self.assertEqual((reference.loop_start, reference.loop_end, reference.start_time,
                          tuple(reference.loop_end_writes), reference.file_path), before)
        self.assertEqual(self.fixture.next_wrap(reference), 16)
        self.assertEqual(self.controller.group_clock(0).span, 4)
        self.assertEqual(self.controller.global_clock().span, 3.5)
        self.assertEqual(self.controller.backend.faults, {})

    def test_accept_next_finishes_on_changed_bar_then_waits_for_original_reference_wrap(self):
        self.make()
        reference, current = self.begin_in_seven_eight()
        current_clip = self.observe_start(current, 12)
        self.at(13)
        self.assertEqual(self.dispatch(p.ACCEPT_NEXT), p.OUTCOME_QUEUED)
        following = current.next_take
        self.assertEqual(current.schedule.start_at, 14)
        self.assertEqual((following.schedule.queued_at, following.schedule.start_at), (14, 16))
        self.assertEqual(self.calls[-1][3], {'launch_quantization': 4})
        self.assertFalse(self.slot(following).track.arm)
        self.observe_finish(current, 14, 3.5)
        self.assertEqual(current_clip.loop_end_writes, [2])
        self.assertIs(self.controller.session.take, following)
        self.assertEqual(following.state, 'queued')
        self.assertFalse(self.slot(following).track.arm)
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4, 2])
        self.at(14.5)
        self.controller.tick()
        self.assertEqual(self.calls[-1], ('fire', self.slot(following).track.name, 0,
                                         {'launch_quantization': 5}))
        self.assertEqual(following.schedule.start_at, 16)
        self.observe_start(following, 16)
        self.assertEqual((reference.loop_end, reference.start_time), (4, 8))
        self.assertEqual(self.controller.backend.faults, {})

    def test_retry_discards_immediately_then_uses_unchanged_reference_wrap_in_new_meter(self):
        self.make()
        reference, old = self.begin_in_seven_eight()
        old_clip = self.observe_start(old, 12)
        self.at(13)
        before = len(self.calls)
        self.assertEqual(self.dispatch(p.RETRY), p.OUTCOME_QUEUED)
        following = self.controller.session.take
        self.assertIsNot(following, old)
        self.assertEqual(following.schedule.start_at, 16)
        self.assertEqual(self.calls[before:], [('stop', self.slot(old).track.name, False)])
        self.assertIs(self.slot(old).clip, old_clip)
        self.assertEqual(following.state, 'queued')
        self.slot(old).track.quiet_native()
        self.controller.tick()
        self.assertEqual(self.calls[before:], [('stop', self.slot(old).track.name, False),
                         ('delete', self.slot(old).track.name, 0),
                         ('arm', self.slot(old).track.name, False)])
        self.assertFalse(self.slot(old).has_clip)
        self.assertFalse(self.slot(following).track.arm)
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4])
        self.at(14.5)
        self.controller.tick()
        self.assertEqual(self.calls[-1], ('fire', self.slot(following).track.name, 0,
                                         {'launch_quantization': 5}))
        self.observe_start(following, 16)
        self.assertEqual((reference.loop_end, reference.start_time), (4, 8))
        self.assertTrue(reference.is_playing)
        self.assertEqual(self.controller.backend.faults, {})

    def test_meter_change_after_native_fire_preserves_requested_start_and_actual_anchor(self):
        self.make()
        reference, take = self.begin_in_seven_eight()
        calls = list(self.calls)
        self.song.signature_numerator, self.song.signature_denominator = 5, 4
        self.at(11.5)
        self.controller.tick()
        self.assertEqual(take.schedule.start_at, 12)
        self.assertEqual(self.calls, calls, 'a fired native launch must not be rescheduled after a meter change')
        self.assertEqual(self.controller.global_clock().span, 5)
        clip = self.observe_start(take, 12)
        self.assertEqual(clip.start_time, 12)
        self.assertEqual(self.calls, calls)
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))
        self.assertEqual(self.controller.backend.faults, {})

    def test_missed_native_enqueue_window_retains_same_take_for_next_safe_reference_wrap(self):
        self.make()
        reference = self.reference()
        self.song.signature_numerator, self.song.signature_denominator = 7, 8
        self.at(9)
        calls = list(self.calls)
        self.assertEqual(self.dispatch(p.BEGIN), p.OUTCOME_QUEUED)
        take = self.controller.session.take
        token = take.token
        self.assertEqual(take.schedule.start_at, 12)
        self.assertEqual(self.calls, calls)
        self.at(12)
        self.controller.tick()
        self.assertNotIn(0, self.controller.backend.faults)
        self.assertEqual(self.calls, calls)
        self.assertFalse(self.slot(take).track.arm)
        self.assertFalse(self.slot(take).has_clip)
        self.assertIs(self.controller.session.take, take)
        self.assertEqual(take.token, token)
        self.assertEqual((take.state, take.schedule.start_at), ('queued', 16))
        self.assertEqual(self.controller.backend._attempts[take.slot].start_target, 16)
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4])
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))
        self.assertTrue(reference.is_playing)
        fields = self.controller.snapshot(0)
        self.assertEqual((fields[91], fields[106], fields[107]), (p.PLAYING, 1, token % 128))
        self.at(14.5)
        self.controller.tick()
        self.assertEqual(self.calls[-1], ('fire', self.slot(take).track.name, 0,
                                         {'launch_quantization': 5}))
        self.observe_start(take, 16)
        self.assertEqual(self.slot(take).clip.start_time, 16)
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))
        self.assertEqual(self.controller.backend.faults, {})

    def test_missing_proven_fine_grid_refuses_off_bar_reference_target_without_fallback(self):
        self.make(quantizations={'q_bar': 4})
        reference = self.reference()
        self.song.signature_numerator, self.song.signature_denominator = 7, 8
        self.at(11.2)
        calls = list(self.calls)
        self.assertEqual(self.fixture.next_wrap(reference), 12)
        self.assertEqual(self.fixture.next_bar(), 14)
        self.assertEqual(self.dispatch(p.BEGIN), p.NOT_READY)
        self.assertIsNone(self.controller.session.take)
        self.assertEqual(self.calls, calls, 'an unsupported target must not fall back to native bar 14')
        self.assertEqual([layer.length for layer in self.controller.session.groups[0].accepted], [4])
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))
        self.assertTrue(reference.is_playing)


if __name__ == '__main__':
    unittest.main()
