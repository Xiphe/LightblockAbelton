"""Target-relative queued feedback never schedules or invents Native recording."""
import unittest
from unittest.mock import patch
import test_audio_controller as native
import test_dynamic_recording as dynamic
import test_retained_start as retained
import test_device_ui as ui_fixture
from controller_test_support import RecordingSurfaceCase
from LightpadController import protocol as p, timing, compact_wire
from LightpadController.device_ui import DeviceUI
from LightpadController.output_pacing import OutputPacer
from test_timing import number


class CountdownProviderTests(unittest.TestCase):
    def setUp(self):
        self.helper=native.AudioControllerTests()
        self.controller=self.helper.make()
        self.addCleanup(self.helper.doCleanups)

    def begin(self):
        self.assertEqual(self.controller.dispatch(self.helper.command(p.BEGIN)),p.OUTCOME_QUEUED)
        return self.controller.session.take

    def queued_reference(self):
        lease,clip,token=self.helper.accept(length=288)
        self.controller.dispatch(self.helper.command(p.ACK_COMPLETION,token%128))
        return self.begin(),clip

    def test_first_queue_uses_native_target_and_current_bar_without_mutation(self):
        take=self.begin();calls=list(self.helper.calls)
        clock=self.controller.countdown_clock(0,take.token%128)
        self.assertEqual(take.schedule.start_at,4)
        self.assertEqual((clock.context,clock.span,clock.position,clock.first_bar),(1,4,1,0))
        self.assertEqual(self.controller.countdown_remaining_bars(0,take.token%128),1)
        self.assertEqual(self.helper.calls,calls)
        self.assertEqual(self.controller.backend.faults,{})

    def test_72_bar_wait_counts_9_8_1_then_invalid_due_without_replanning(self):
        take,reference=self.queued_reference();target=take.schedule.start_at
        calls=list(self.helper.calls)
        for beats,remaining in ((36,9),(32,8),(4,1),(0,0),(-2,0)):
            self.helper.song.current_song_time=target-beats
            self.assertEqual(self.controller.countdown_remaining_bars(0,take.token%128),remaining)
            clock=self.controller.countdown_clock(0,take.token%128)
            if beats>0:
                span=32 if beats>32 else 4
                self.assertEqual((clock.context,clock.span,clock.position),(2,span,(-beats)%span))
            else:self.assertIsNone(clock)
        self.assertEqual(take.schedule.start_at,target)
        self.assertEqual(self.controller.backend._attempts[take.slot].start_target,target)
        self.assertEqual(self.controller.group_clock(0).span,288)
        self.assertEqual(reference.loop_end,288)
        self.assertEqual(self.helper.calls,calls)

    def test_full_token_schedule_and_lease_identity_must_match(self):
        take=self.begin();attempt=self.controller.backend._attempts[take.slot]
        self.assertIsNone(self.controller.countdown_clock(1,take.token%128))
        self.assertEqual(self.controller.countdown_remaining_bars(0,(take.token+1)%128),0)
        original=take.schedule
        take.schedule=original._replace(start_at=original.start_at+4)
        self.assertIsNone(self.controller.countdown_clock(0,take.token%128))
        take.schedule=original
        attempt.token+=128
        self.assertEqual(self.controller.countdown_remaining_bars(0,take.token%128),0)
        self.assertEqual(self.controller.backend.faults,{})

    def test_retry_shows_replacement_target_during_owned_ancestor_cleanup(self):
        lease,clip,token=self.helper.record()
        self.helper.song.current_song_time=clip.start_time+2
        self.controller.dispatch(self.helper.command(p.RETRY))
        take=self.controller.session.take
        calls=list(self.helper.calls)
        self.assertNotEqual(take.token,token)
        self.assertGreater(self.controller.countdown_remaining_bars(0,take.token%128),0)
        self.assertIsNotNone(self.controller.countdown_clock(0,take.token%128))
        self.assertIsNone(self.controller.countdown_clock(0,token%128))
        self.assertEqual(self.helper.calls,calls)
        self.assertIs(self.helper.slot(lease).clip,clip)

    def test_accept_next_queued_child_only_after_parent_is_accepted(self):
        lease,ref,token=self.helper.accept(length=8)
        self.controller.dispatch(self.helper.command(p.ACK_COMPLETION,token%128))
        take=self.begin();self.helper.song.current_song_time=take.schedule.start_at-.5
        self.controller.tick();self.helper.song.current_song_time=take.schedule.start_at
        self.helper.slot(take.slot).begin_native(take.schedule.start_at);self.controller.tick()
        self.helper.song.current_song_time+=2
        self.controller.dispatch(self.helper.command(p.ACCEPT_NEXT))
        child=take.next_take
        self.assertIsNone(self.controller.countdown_clock(0,child.token%128))
        self.helper.song.current_song_time=take.schedule.start_at
        self.helper.slot(take.slot).finish_native(4);self.controller.tick()
        self.assertIs(self.controller.session.take,child)
        self.assertEqual(self.controller.countdown_remaining_bars(0,child.token%128),1)
        self.assertEqual(self.controller.countdown_clock(0,child.token%128).position,0)
        self.assertEqual(child.schedule.start_at-self.helper.song.current_song_time,4)
        self.assertEqual(ref.loop_end,8)

    def test_stop_and_resume_use_song_position_not_wall_time(self):
        take=self.begin();self.helper.song.is_playing=False
        first=self.controller.countdown_clock(0,take.token%128)
        second=self.controller.countdown_clock(0,take.token%128)
        self.assertEqual(first,second);self.assertFalse(first.running)
        self.helper.song.is_playing=True;self.helper.song.current_song_time=2
        self.assertEqual(self.controller.countdown_clock(0,take.token%128).position,2)
        self.assertEqual(take.schedule.start_at,4)

    def test_current_queued_meter_changes_feedback_without_changing_target(self):
        helper=dynamic.DynamicRecordingTests();helper.make();self.addCleanup(helper.doCleanups)
        reference,take=helper.begin_in_seven_eight();helper.at(8)
        target=take.schedule.start_at
        self.assertEqual(helper.controller.countdown_remaining_bars(0,take.token%128),2)
        clock=helper.controller.countdown_clock(0,take.token%128)
        self.assertEqual((clock.span,clock.numerator,clock.denominator),(3.5,7,8))
        helper.song.signature_numerator=4;helper.song.signature_denominator=4
        clock=helper.controller.countdown_clock(0,take.token%128)
        self.assertEqual((clock.span,clock.numerator,clock.denominator),(4,4,4))
        self.assertEqual(helper.controller.countdown_remaining_bars(0,take.token%128),1)
        self.assertEqual(take.schedule.start_at,target)
        timing.encode_view_timing(0,1,clock)

    def test_actual_observation_switches_to_recording_feedback_and_counter(self):
        take=self.begin();self.helper.song.current_song_time=take.schedule.start_at
        self.assertIsNone(self.controller.countdown_clock(0,take.token%128))
        self.assertEqual(take.state,'queued')
        self.helper.slot(take.slot).begin_native(take.schedule.start_at);self.controller.tick()
        self.assertEqual(take.state,'recording')
        self.assertIsNone(self.controller.countdown_clock(0,take.token%128))
        self.assertEqual(self.controller.recording_clock(0,take.token%128).position,0)
        self.assertEqual(self.controller.recording_bars(0,take.token%128),0)

    def test_invalid_scope_and_partial_plan_return_no_feedback_or_fault(self):
        take=self.begin();calls=list(self.helper.calls)
        with patch.object(self.controller,'_scope_valid',return_value=False):
            self.assertIsNone(self.controller.countdown_clock(0,take.token%128))
            self.assertEqual(self.controller.countdown_remaining_bars(0,take.token%128),0)
        take.schedule=None
        self.assertIsNone(self.controller.countdown_clock(0,take.token%128))
        self.assertEqual(self.helper.calls,calls)
        self.assertEqual(self.controller.backend.faults,{})


class DeferredCountdownTests(unittest.TestCase):
    def test_retained_late_start_updates_both_feedback_target_and_remaining_count(self):
        helper=retained.RetainedStartTests();helper.make();self.addCleanup(helper.doCleanups)
        helper.fixture.song.signature_numerator=4;helper.fixture.song.signature_denominator=4
        helper.at(3);helper.session.begin(0);take=helper.session.take
        self.assertEqual(take.schedule.start_at,4)
        self.assertEqual(helper.backend.countdown_remaining_bars(take.slot,take.token,take.schedule),1)
        helper.at(3.992);helper.backend.tick()
        self.assertEqual(take.schedule.start_at,8)
        calls=list(helper.fixture.calls)
        self.assertEqual(helper.backend.countdown_remaining_bars(take.slot,take.token,take.schedule),2)
        self.assertEqual(helper.fixture.calls,calls)
        self.assertEqual(helper.backend.faults,{})


class CountdownPresentationTests(unittest.TestCase):
    def host(self,remaining):
        host=ui_fixture.Host()
        host.ui=DeviceUI(host.send,lambda g:host.fields[g],lambda:host.meter,lambda:host.now,
                           recording_bars=lambda *_:3,countdown_bars=lambda *_:remaining)
        host.fields[0].update({106:1,107:7})
        host.ui.view,host.ui.recording_group,host.ui.active_take=2,0,7
        return host

    def test_remaining_9_8_1_use_existing_blocks_and_8_byte_frame(self):
        for remaining,encoded in ((9,8),(8,7),(1,0),(0,0),(112,111),(113,111)):
            host=self.host(remaining);payload=host.ui.presentation()
            self.assertEqual(payload[5:7],(encoded,0))
            self.assertEqual(len(compact_wire.project_wire(p.encode_presentation(1,payload))),10)
        host=self.host(9);host.fields[0][106]=2
        self.assertEqual(host.ui.presentation()[5],3)
        host.fields[0][106]=3;self.assertEqual(host.ui.presentation()[5],3)
        host.ui.view=0;host.ui.chord_mask=129
        self.assertEqual(host.ui.presentation()[5:7],(1,1))

    def test_latest_countdown_frame_replaces_stale_count_under_existing_pacer_binding(self):
        host=self.host(9);sent=[];out=OutputPacer(sent.append)
        fields=dict(host.fields[0]);fields[106]=1
        out.offer(p.encode_snapshot(0,1,fields),100);out.flush(100)
        out.presentation(p.encode_presentation(1,host.ui.presentation()),100.001,(0,p.PLAYING,7))
        host.ui._countdown_bars=lambda *_:8
        out.presentation(p.encode_presentation(2,host.ui.presentation()),100.002,(0,p.PLAYING,7))
        out.flush(100.002)
        views=[m for m in sent if m[0][1]==20]
        self.assertEqual(len(views),1);self.assertEqual(views[0][6][2],7)


class CountdownSurfaceTests(RecordingSurfaceCase):
    def test_selected_queued_surface_clock_and_due_invalidation_do_not_replace_global_or_overview(self):
        take,slot=self.begin()
        current=self.surface._timing_frame(0,True)
        self.assertEqual(number(current,73),256)
        self.assertEqual(number(current,76),64)
        self.assertEqual(self.surface._ui.presentation()[5:7],(0,0))
        global_before=self.surface._timing_frame(8,True)
        self.native.song.current_song_time=take.schedule.start_at
        invalid=self.surface._timing_frame(0,True)
        self.assertTrue(all(v==0 for _,_,v in invalid[1:-1]))
        self.assertEqual(take.state,'queued')
        self.surface._ui.active_take=(take.token+1)%128
        with patch.object(self.surface._engine,'group_clock',side_effect=AssertionError('queued fallback')), \
             patch.object(self.surface,'_ui_snapshot',return_value={91:p.EMPTY,106:1,107:take.token%128}):
            self.assertTrue(all(v==0 for _,_,v in self.surface._timing_frame(0,True)[1:-1]))
        self.native.song.current_song_time=1
        self.surface._ui.view=0
        self.assertEqual(self.surface._timing_frame(8,True)[1:-1],global_before[1:-1])

    def test_actual_surface_pacer_sends_latest_remaining_value_in_same_frame(self):
        take,slot=self.begin()
        selected=[];original=self.surface._output.send
        def send(messages):
            selected.append(list(messages));original(messages)
        self.surface._output.send=send
        self.advance(.2);self.surface._output.clear()
        self.surface._publish(force=True,only=0);self.surface._output.flush(self.now.now)
        remaining=[9]
        calls=list(self.native.calls)
        with patch.object(self.surface._engine,'countdown_remaining_bars',side_effect=lambda *_:remaining[0]):
            self.surface._publish_presentation(force=True)
            remaining[0]=8
            self.surface._publish_presentation(force=True)
            self.pump(.3)
        views=[m for m in selected if m[0][:2]==(191,20)]
        self.assertTrue(views)
        self.assertTrue(all(m[6][2]==7 and m[4][2]==take.token%128 for m in views))
        self.assertTrue(all(len(compact_wire.project_wire(m))==10 for m in views))
        self.assertEqual(self.native.calls,calls)

    def test_actual_surface_native_observation_replaces_invalid_queued_clock(self):
        take,slot=self.begin()
        self.native.song.current_song_time=take.schedule.start_at
        self.assertTrue(all(v==0 for _,_,v in self.surface._timing_frame(0,True)[1:-1]))
        self.assertEqual(take.state,'queued')
        self.start_native(take,slot)
        actual=self.surface._timing_frame(0,True)
        self.assertEqual(self.surface._ui_snapshot(0)[106],2)
        self.assertEqual(number(actual,73),256)
        self.assertEqual(number(actual,76),0)
        self.assertEqual(self.surface._ui.presentation()[5:7],(0,0))


if __name__=='__main__':unittest.main()
