"""Prepared commands enqueue before display callbacks can consume their lead.

This exercises the actual dispatch/admission/Native-queue seam with display
callback phase varied independently. It is scheduling policy, not hardware
latency evidence.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_audio_inputs as audio_fixtures
import test_backend as legacy_fixtures


class ReadyEnqueueChecks(unittest.TestCase):
    def audio(self):
        fixture = audio_fixtures.AudioInputChecks()
        self.addCleanup(fixture.doCleanups)
        backend = fixture.make(initial=3, groups=1)
        fixture.ticks(65)
        take = fixture.start()
        fixture.ticks(40)
        return fixture, backend, take

    def test_accept_in_fourth_bar_survives_display_callback_phase(self):
        for callback_delay in (0., .05, .1, .2, .28):
            with self.subTest(callback_delay=callback_delay):
                fixture, backend, take = self.audio()
                finish = take.anchor + 16.
                press = finish - .6  # 300 ms before the boundary at120 BPM.
                fixture.song.advance(press)
                before = sum(call[0] == 'fire' for call in fixture.song.calls)
                fixture.song.track_reads = 0
                self.assertEqual(backend.dispatch(65, take.take_id), 0)
                self.assertEqual(take.finish_target, finish)
                self.assertEqual(fixture.song.track_reads, 0)
                self.assertEqual(sum(call[0] == 'fire' for call in fixture.song.calls) - before, 2)
                fixture.clock += callback_delay
                fixture.song.advance(press + callback_delay * 2.)
                backend.tick()
                self.assertEqual(take.finish_target, finish)
                fixture.song.advance(finish + .001)
                backend.tick()
                self.assertTrue(all(not member.clip.is_recording
                                    for member in fixture.members(take)))
                self.assertEqual([member.clip.loop_end - member.clip.loop_start
                                  for member in fixture.members(take)], [16., 16.])
                planned = [event for event in backend.events if event['event'] == 'finish_planned']
                self.assertEqual(len(planned), 1)
                self.assertEqual(planned[0]['request_beat'], press)
                self.assertEqual(planned[0]['finish_target'], finish)

    def test_next_enqueues_both_bundles_before_late_display_callback(self):
        fixture, backend, take = self.audio()
        finish = take.anchor + 16.
        fixture.song.advance(finish - .6)
        before = len(fixture.song.calls)
        self.assertEqual(backend.dispatch(67, take.take_id | (2 << 7)), 0)
        following = take.following
        self.assertEqual(take.finish_target, finish)
        self.assertEqual(following.target, finish)
        calls = fixture.song.calls[before:]
        self.assertEqual(sum(call[0] == 'fire' for call in calls), 4)
        self.assertFalse(any(call[0] == 'create_track' for call in calls))
        fixture.clock += .28
        fixture.song.advance(finish - .04)
        backend.tick()
        self.assertEqual(take.finish_target, finish)
        fixture.song.advance(finish + .001)
        backend.tick()
        self.assertIs(backend.recording.take, following)
        self.assertEqual({member.anchor for member in fixture.members(following)}, {finish})

    def test_accept_arriving_inside_existing_safety_margin_still_uses_later_bar(self):
        fixture, backend, take = self.audio()
        fourth_bar = take.anchor + 16.
        fixture.song.advance(fourth_bar - .05)
        self.assertEqual(backend.dispatch(65, take.take_id), 0)
        self.assertEqual(take.finish_target, fourth_bar + 4.)
        fixture.song.advance(fourth_bar + .05)
        backend.tick()
        fixture.song.advance(fourth_bar + 4.)
        backend.tick()
        self.assertTrue(all(not member.clip.is_recording for member in fixture.members(take)))
        self.assertEqual(take.clip.loop_end - take.clip.loop_start, 20.)

    def test_legacy_prepared_accept_uses_same_immediate_seam(self):
        fixture = legacy_fixtures.BackendChecks()
        self.addCleanup(fixture.doCleanups)
        backend = fixture.make(groups=1)
        fixture.warm()
        take = fixture.begin()
        finish = take.anchor + 16.
        fixture.song.advance(finish - .6)
        before = sum(call[0] == 'fire' for call in fixture.song.calls)
        self.assertEqual(backend.dispatch(65, take.take_id), 0)
        self.assertEqual(sum(call[0] == 'fire' for call in fixture.song.calls) - before, 1)
        fixture.clock += .28
        fixture.song.advance(finish - .04)
        backend.tick()
        self.assertEqual(take.finish_target, finish)
        fixture.song.advance(finish)
        backend.tick()
        self.assertFalse(take.clip.is_recording)

    def test_ambiguous_second_finish_is_not_rejected_or_replayed(self):
        fixture, backend, take = self.audio()
        fixture.song.advance(take.anchor + 2.)
        take.members[0].slot.fire_error = 'after'
        before = sum(call[0] == 'fire' for call in fixture.song.calls)
        self.assertEqual(backend.dispatch(65, take.take_id), 0)
        issued = sum(call[0] == 'fire' for call in fixture.song.calls)
        self.assertEqual(issued - before, 2)
        fixture.ticks(10)
        self.assertEqual(sum(call[0] == 'fire' for call in fixture.song.calls), issued)
        self.assertTrue(all(member.slot.has_clip for member in fixture.members(take)))


if __name__ == '__main__':
    unittest.main()
