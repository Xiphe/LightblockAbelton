"""Eight-bar countdown pages are a scoped render clock, never a global grid."""
import unittest
from unittest.mock import patch
import test_audio_controller as native
import test_audio_backend as backend_fixture
from controller_test_support import RecordingSurfaceCase
from LightpadController import protocol as p,timing,compact_wire
from test_timing import number


def page_clock(context=1,span=32,first=0,take=7):
    return timing.Clock(context,take,span,1,first,4,120,4,4,True)


class PageCodecTests(unittest.TestCase):
    def test_default_encoders_and_global_clock_remain_strict(self):
        clock=page_clock()
        for encode in (lambda:timing.encode_timing(0,1,clock),
                       lambda:timing.encode_view_timing(0,1,clock),
                       lambda:timing.encode_global_timing(1,clock._replace(take_id=0))):
            with self.assertRaises(ValueError):encode()
        self.assertEqual(number(timing.encode_view_timing(0,1,page_clock(span=4)),73),256)

    def test_explicit_view_privilege_allows_only_exact_eight_bars_with_zero_first(self):
        for context in (1,2):
            frame=timing.encode_view_timing(0,1,page_clock(context),countdown_page=True)
            self.assertEqual(number(frame,73),32*64)
            self.assertEqual(number(frame,82),4*64)
            self.assertEqual(frame[-3][2:],(4,))
            self.assertEqual(len(compact_wire.project_wire(frame)),20)
        for span,first in ((8,0),(28,0),(36,0),(31.999,0),(32,1)):
            with self.assertRaises(ValueError):
                timing.encode_view_timing(0,1,page_clock(span=span,first=first),countdown_page=True)
        for flag in (1,None,'yes'):
            with self.assertRaises(ValueError):
                timing.encode_view_timing(0,1,page_clock(),countdown_page=flag)

    def test_reference_clocks_and_global_one_bar_are_unaffected(self):
        reference=page_clock(context=2,span=288)
        self.assertEqual(number(timing.encode_view_timing(0,1,reference),73),288*64)
        clock=page_clock(span=4,take=0)
        self.assertEqual(number(timing.encode_global_timing(1,clock),73),256)


class PageProviderTests(unittest.TestCase):
    def test_empty_and_populated_projection_uses_actual_rate_meter_and_page_span(self):
        helper=backend_fixture.AudioBackendTests();fixture,backend,session=helper.make()
        for context in (1,2):
            for remaining in (9,16,72):
                feedback=(remaining*4,4,120,4,4,True)
                with patch.object(backend,'_countdown_feedback',return_value=feedback):
                    clock=backend.countdown_clock(object(),7,object(),context,7)
                self.assertEqual((clock.context,clock.span,clock.bar_length,clock.tempo),(context,32,4,120))
                self.assertEqual(clock.position,(-remaining*4)%32)
                timing.encode_view_timing(0,1,clock,countdown_page=True)

    def test_real_layer_targets_remain_unchanged_for_9_16_and_72_bar_waits(self):
        for bars in (9,16,72):
            helper=native.AudioControllerTests();controller=helper.make();self.addCleanup(helper.doCleanups)
            lease,clip,token=helper.accept(length=bars*4)
            controller.dispatch(helper.command(p.ACK_COMPLETION,token%128))
            controller.dispatch(helper.command(p.BEGIN));take=controller.session.take
            target=take.schedule.start_at;calls=list(helper.calls)
            clock=controller.countdown_clock(0,take.token%128)
            self.assertEqual(controller.countdown_remaining_bars(0,take.token%128),bars)
            self.assertEqual((clock.span,clock.bar_length,clock.position),(32,4,(-bars*4)%32))
            self.assertEqual(controller.group_clock(0).span,bars*4)
            self.assertEqual(take.schedule.start_at,target)
            self.assertEqual(helper.calls,calls)
            self.assertEqual(clip.loop_end,bars*4)

    def test_nine_to_eight_switches_page_to_one_bar_at_target_relative_zero(self):
        helper=native.AudioControllerTests();controller=helper.make();self.addCleanup(helper.doCleanups)
        _,clip,token=helper.accept(length=288)
        controller.dispatch(helper.command(p.ACK_COMPLETION,token%128))
        controller.dispatch(helper.command(p.BEGIN));take=controller.session.take;target=take.schedule.start_at
        for remaining,span,phase in ((36,32,28),(32.001,32,31.999),(32,4,0),(31,4,1),(4,4,0)):
            helper.song.current_song_time=target-remaining
            clock=controller.countdown_clock(0,take.token%128)
            self.assertEqual(clock.span,span)
            self.assertAlmostEqual(clock.position,phase)
        helper.song.current_song_time=target
        self.assertIsNone(controller.countdown_clock(0,take.token%128))
        self.assertEqual(take.state,'queued')


class SurfacePagePrivilegeTests(RecordingSurfaceCase):
    def test_selected_empty_queued_page_is_encoded_without_changing_frame_size(self):
        take,slot=self.begin()
        clock=page_clock(take=take.token%128)
        with patch.object(self.surface._engine,'countdown_clock',return_value=clock):
            frame=self.surface._timing_frame(0,True)
        self.assertEqual(number(frame,73),2048)
        self.assertEqual(number(frame,82),256)
        self.assertEqual(len(compact_wire.project_wire(frame)),20)
        selected=[];original=self.surface._output.send
        def send(messages):
            selected.append(list(messages));original(messages)
        self.surface._output.send=send
        self.advance(.2);self.surface._output.clear()
        with patch.object(self.surface._engine,'countdown_clock',return_value=clock):
            self.surface._publish(force=True,only=0)
            self.surface._output.flush(self.now.now)
            self.surface._publish_timing(only=0,force=True)
            self.surface._output.flush(self.now.now)
        clocks=[m for m in selected if m[0][:2]==(176,70)]
        self.assertEqual(len(clocks),1)
        self.assertEqual(number(clocks[0],73),2048)
        states=[i for i,m in enumerate(selected) if m[0][:2]==(176,40)]
        self.assertLess(states[-1],selected.index(clocks[0]))

    def test_page_privilege_cannot_leak_into_overview_global_or_actual_recording(self):
        take,slot=self.begin();clock=page_clock(take=take.token%128)
        self.surface._ui.view=0
        with patch.object(self.surface._engine,'group_clock',return_value=clock):
            frame=self.surface._timing_frame(0,True)
            self.assertTrue(all(v==0 for _,_,v in frame[1:-1]))
        with patch.object(self.surface._engine,'global_clock',return_value=clock._replace(take_id=0)):
            frame=self.surface._timing_frame(8,True)
            self.assertTrue(all(v==0 for _,_,v in frame[1:-1]))
        self.surface._ui.view=2
        self.start_native(take,slot)
        with patch.object(self.surface._engine,'recording_clock',return_value=clock):
            frame=self.surface._timing_frame(0,True)
            self.assertTrue(all(v==0 for _,_,v in frame[1:-1]))


if __name__=='__main__':unittest.main()
