"""Recording-ring clock is one frozen bar from the actual owned take start."""
import unittest
from unittest.mock import patch
import test_audio_controller as native
import test_dynamic_recording as dynamic
from controller_test_support import RecordingSurfaceCase
from LightpadController import protocol as p, timing
from test_timing import number


class RecordingClockTests(unittest.TestCase):
    def setUp(self):
        self.helper = native.AudioControllerTests()
        self.controller = self.helper.make()
        self.addCleanup(self.helper.doCleanups)

    def test_first_take_clock_starts_at_actual_anchor_and_wraps_once_per_bar(self):
        lease, clip, token = self.helper.record()
        calls = list(self.helper.calls)
        for elapsed, expected in ((0,0),(1,1),(3.99,3.99),(4,0),(7,3),(12,0)):
            self.helper.song.current_song_time = clip.start_time + elapsed
            clock = self.controller.recording_clock(0,token%128)
            self.assertEqual((clock.context,clock.span,clock.bar_length,clock.first_bar),(1,4,4,0))
            self.assertAlmostEqual(clock.position,expected)
            timing.encode_view_timing(0,1,clock)
        self.assertEqual(self.helper.calls,calls)

    def test_overdub_ring_uses_one_bar_while_72_bar_reference_remains_unchanged(self):
        reference_lease, reference, old_token = self.helper.accept(length=288)
        self.controller.dispatch(self.helper.command(p.ACK_COMPLETION,old_token%128))
        self.controller.dispatch(self.helper.command(p.BEGIN))
        take = self.controller.session.take
        self.helper.song.current_song_time = take.schedule.start_at-.5
        self.controller.tick()
        self.helper.song.current_song_time = take.schedule.start_at
        slot = self.helper.slot(take.slot)
        clip = slot.begin_native(take.schedule.start_at)
        self.controller.tick()
        self.helper.song.current_song_time += 6
        reference.playing_position = 6
        before = (reference.loop_start,reference.loop_end,reference.start_time)
        clock = self.controller.recording_clock(0,take.token%128)
        self.assertEqual((clock.context,clock.span,clock.position,clock.first_bar),(2,4,2,0))
        self.assertEqual(self.controller.group_clock(0).span,288)
        self.assertEqual((reference.loop_start,reference.loop_end,reference.start_time),before)
        self.assertEqual(self.controller.recording_bars(0,take.token%128),1)
        self.assertEqual(self.controller.recording_clock(0,old_token%128),None)

    def test_off_grid_start_and_frozen_meter_are_coherent_with_counter_and_encoder(self):
        helper=dynamic.DynamicRecordingTests();helper.make();self.addCleanup(helper.doCleanups)
        reference,take=helper.begin_in_seven_eight()
        helper.observe_start(take,12)
        helper.at(13)
        clock=helper.controller.recording_clock(0,take.token%128)
        self.assertEqual((clock.span,clock.position,clock.first_bar,clock.numerator,clock.denominator),(3.5,1,0,7,8))
        self.assertEqual(helper.controller.global_clock().position,2.5)
        helper.song.signature_numerator=4;helper.song.signature_denominator=4
        helper.at(19.25)
        clock=helper.controller.recording_clock(0,take.token%128)
        self.assertEqual((clock.span,clock.position,clock.numerator,clock.denominator),(3.5,.25,7,8))
        self.assertEqual(helper.controller.recording_bars(0,take.token%128),2)
        timing.encode_view_timing(0,3,clock)
        self.assertEqual(helper.controller.global_clock().span,4)

    def test_countdown_missing_identity_or_frozen_meter_invalidates_without_native_side_effects(self):
        self.controller.dispatch(self.helper.command(p.BEGIN))
        take=self.controller.session.take
        self.assertIsNone(self.controller.recording_clock(0,take.token%128))
        self.helper.song.current_song_time=take.schedule.start_at
        clip=self.helper.slot(take.slot).begin_native(take.schedule.start_at)
        self.controller.tick()
        attempt=self.controller.backend._attempts[take.slot]
        calls=list(self.helper.calls)
        self.assertIsNone(self.controller.recording_clock(1,take.token%128))
        self.assertIsNone(self.controller.recording_clock(0,(take.token+1)%128))
        attempt.recording_numerator=None
        self.assertIsNone(self.controller.recording_clock(0,take.token%128))
        self.assertEqual(self.helper.calls,calls)
        self.assertEqual(self.controller.backend.faults,{})

    def test_finish_pending_keeps_clock_and_observed_stopped_clip_invalidates(self):
        lease,clip,token=self.helper.record()
        self.helper.song.current_song_time=clip.start_time+6
        self.controller.dispatch(self.helper.command(p.ACCEPT))
        self.assertEqual(self.controller.recording_clock(0,token%128).position,2)
        self.helper.song.is_playing=False
        self.assertFalse(self.controller.recording_clock(0,token%128).running)
        clip.is_recording=False
        self.assertIsNone(self.controller.recording_clock(0,token%128))


class SurfaceRecordingClockTests(RecordingSurfaceCase):
    def test_actual_recording_frame_uses_take_clock_but_overview_uses_reference_and_global_is_unchanged(self):
        take,slot=self.record()
        self.native.song.current_song_time=slot.clip.start_time+2
        actual=self.surface._timing_frame(0,True)
        self.assertEqual(number(actual,73),256)
        self.assertEqual(number(actual,76),128)
        global_before=self.surface._timing_frame(8,True)
        clock=timing.Clock(2,take.token%128,288,17,0,4,120,4,4,True)
        with patch.object(self.surface._engine,'group_clock',return_value=clock):
            self.surface._ui.view=0
            overview=self.surface._timing_frame(0,True)
            self.assertEqual(number(overview,73),288*64)
        global_after=self.surface._timing_frame(8,True)
        self.assertEqual(global_before[1:-1],global_after[1:-1])

    def test_queued_view_keeps_existing_clock_and_actual_view_never_falls_back_on_failure(self):
        take,slot=self.begin()
        old=self.surface._engine.group_clock(0)
        queued=self.surface._timing_frame(0,True)
        self.assertEqual(number(queued,73),round(old.span*64))
        self.assertEqual(take.schedule.start_at,4)
        self.start_native(take,slot)
        fields=dict(self.surface._ui_snapshot(0))
        with patch.object(self.surface,'_ui_snapshot',return_value=fields), \
             patch.object(self.surface._engine,'recording_clock',return_value=None), \
             patch.object(self.surface._engine,'group_clock',side_effect=AssertionError('long-base fallback')):
            invalid=self.surface._timing_frame(0,True)
            self.assertTrue(all(value==0 for _,_,value in invalid[1:-1]))
        self.surface._ui.active_take=(take.token+1)%128
        with patch.object(self.surface,'_ui_snapshot',return_value=fields), \
             patch.object(self.surface._engine,'group_clock',side_effect=AssertionError('stale binding fallback')):
            invalid=self.surface._timing_frame(0,True)
            self.assertTrue(all(value==0 for _,_,value in invalid[1:-1]))


if __name__=='__main__':unittest.main()
