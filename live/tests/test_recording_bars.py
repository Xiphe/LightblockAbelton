"""Completed recording bars from actual observed ownership, never loop wraps."""
import unittest
from unittest.mock import patch
import test_audio_backend as native
import test_audio_controller as controls
import test_dynamic_recording as dynamic
import test_deferred_finish as deferred
from controller_test_support import RecordingSurfaceCase
import test_device_ui as ui_fixture
from LightpadController import protocol as p, compact_wire
from LightpadController.device_ui import DeviceUI
from LightpadController.output_pacing import OutputPacer


class BackendBarTests(unittest.TestCase):
    def setUp(self):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()

    def record(self):
        lease, clip = self.helper.record()
        return lease, clip, self.session.take.token

    def test_countdown_has_zero_then_only_whole_actual_recorded_bars_count(self):
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        take = self.session.take
        self.backend.tick()
        self.fixture.song.current_song_time = take.schedule.start_at + 40
        self.assertEqual(self.backend.recording_bars(take.slot, take.token), 0)
        self.fixture.song.current_song_time = take.schedule.start_at
        self.helper.slot(take.slot).begin_native(take.schedule.start_at)
        self.backend.tick()
        calls = list(self.fixture.calls)
        for elapsed, expected in ((0, 0), (3.999, 0), (4, 1), (11.999, 2), (12, 3), (280, 70)):
            self.fixture.song.current_song_time = take.schedule.start_at + elapsed if take.schedule else 4 + elapsed
            self.assertEqual(self.backend.recording_bars(take.slot, take.token), expected)
        self.assertEqual(self.fixture.calls, calls)
        self.assertEqual(self.backend.faults, {})

    def test_frozen_bar_length_does_not_reinterpret_elapsed_bars_after_meter_change(self):
        lease, clip, token = self.record()
        self.fixture.bar_length = 3.5
        self.fixture.song.current_song_time = clip.start_time + 7.5
        self.assertEqual(self.backend.recording_bars(lease, token), 1)
        self.fixture.song.current_song_time = clip.start_time + 8
        self.assertEqual(self.backend.recording_bars(lease, token), 2)

    def test_failed_optional_start_metadata_never_faults_recording_or_infers_later(self):
        self.session.begin(0)
        take = self.session.take
        self.backend.tick()
        self.fixture.song.current_song_time = take.schedule.start_at
        self.helper.slot(take.slot).begin_native(take.schedule.start_at)
        self.fixture.bar_length = None
        calls = list(self.fixture.calls)
        self.backend.tick()
        self.assertEqual(take.state, 'recording')
        self.assertEqual(self.backend.faults, {})
        self.assertEqual(self.fixture.calls, calls)
        self.fixture.bar_length = 4
        self.fixture.song.current_song_time = 20
        self.assertEqual(self.backend.recording_bars(take.slot, take.token), 0)

    def test_native_identity_anchor_recording_and_token_are_required_without_fault_side_effects(self):
        for changed in ('token', 'anchor', 'clip', 'recording', 'negative', 'nan'):
            self.setUp()
            lease, clip, token = self.record()
            self.fixture.song.current_song_time = clip.start_time + 12
            if changed == 'token': token += 1
            elif changed == 'anchor': clip.start_time += 1
            elif changed == 'clip': self.helper.slot(lease).clip = native.Clip(clip.start_time)
            elif changed == 'recording': clip.is_recording = False
            elif changed == 'negative': self.fixture.song.current_song_time = clip.start_time - 1
            elif changed == 'nan': self.fixture.song.current_song_time = float('nan')
            calls = list(self.fixture.calls)
            self.assertEqual(self.backend.recording_bars(lease, token), 0, changed)
            self.assertEqual(self.fixture.calls, calls)
            self.assertEqual(self.backend.faults, {})

    def test_stopped_position_does_not_accumulate_wall_time_and_valid_resume_uses_same_anchor(self):
        lease, clip, token = self.record()
        self.fixture.song.current_song_time = clip.start_time + 8
        self.fixture.song.is_playing = False
        self.assertEqual(self.backend.recording_bars(lease, token), 2)
        self.assertEqual(self.backend.recording_bars(lease, token), 2)
        self.fixture.song.is_playing = True
        self.fixture.song.current_song_time += 4
        self.assertEqual(self.backend.recording_bars(lease, token), 3)
        clip.is_recording = False
        self.assertEqual(self.backend.recording_bars(lease, token), 0)

    def test_deferred_finish_keeps_counting_against_unchanged_reference(self):
        helper = deferred.DeferredFinishTests()
        helper.make(reference=True)
        helper.clock.beat = 831.992
        helper.fixture.song.current_song_time = 831.992
        self.assertEqual(helper.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assertEqual(helper.current.schedule.start_at, 836)
        helper.fixture.song.current_song_time = 835.999
        self.assertEqual(helper.backend.recording_bars(helper.lease, helper.current.token), 2)
        self.assertEqual(helper.session.groups[0].reference.length, 8)


class ControllerBarTests(unittest.TestCase):
    def setUp(self):
        self.helper = controls.AudioControllerTests()
        self.controller = self.helper.make()
        self.addCleanup(self.helper.doCleanups)

    def test_provider_requires_current_group_take_and_does_not_read_old_accepted_reference_as_elapsed(self):
        lease, clip, token = self.helper.accept(length=8)
        self.assertEqual(self.controller.recording_bars(0, token % 128), 0)
        self.controller.dispatch(self.helper.command(p.ACK_COMPLETION, token % 128))
        self.assertEqual(self.controller.dispatch(self.helper.command(p.BEGIN)), p.OUTCOME_QUEUED)
        take = self.controller.session.take
        self.helper.song.current_song_time = take.schedule.start_at - .5
        self.controller.tick()
        self.helper.song.current_song_time = take.schedule.start_at
        slot = self.helper.slot(take.slot)
        slot.begin_native(take.schedule.start_at)
        self.controller.tick()
        self.helper.song.current_song_time += 8
        slot.clip.playing_position = 0  # loop/clip phase cannot determine elapsed bars
        clip.playing_position = 0
        self.assertEqual(self.controller.recording_bars(0, take.token % 128), 2)
        self.assertEqual(self.controller.recording_bars(1, take.token % 128), 0)
        self.assertEqual(self.controller.recording_bars(0, token % 128), 0)

    def test_retry_excludes_old_recording_during_countdown_and_resets_at_replacement_start(self):
        lease, clip, token = self.helper.record()
        self.helper.song.current_song_time = clip.start_time + 12
        self.assertEqual(self.controller.recording_bars(0, token % 128), 3)
        self.controller.dispatch(self.helper.command(p.RETRY))
        replacement = self.controller.session.take
        self.assertEqual(self.controller.recording_bars(0, replacement.token % 128), 0)
        self.helper.slot(lease).track.quiet_native()
        self.controller.tick()
        self.helper.song.current_song_time = replacement.schedule.start_at
        self.helper.slot(replacement.slot).begin_native(replacement.schedule.start_at)
        self.controller.tick()
        self.assertEqual(self.controller.recording_bars(0, replacement.token % 128), 0)
        self.helper.song.current_song_time += 4
        self.assertEqual(self.controller.recording_bars(0, replacement.token % 128), 1)

    def test_accept_next_resets_only_when_the_observed_child_becomes_current(self):
        lease, clip, token = self.helper.record()
        self.helper.song.current_song_time = clip.start_time + 7
        self.controller.dispatch(self.helper.command(p.ACCEPT_NEXT))
        old = self.controller.session.take
        child = old.next_take
        self.helper.song.current_song_time = old.schedule.start_at
        self.helper.slot(lease).finish_native(8)
        self.helper.slot(child.slot).begin_native(child.schedule.start_at)
        self.controller.tick()
        self.assertIs(self.controller.session.take, child)
        self.assertEqual(self.controller.recording_bars(0, token % 128), 0)
        self.assertEqual(self.controller.recording_bars(0, child.token % 128), 0)
        self.helper.song.current_song_time += 4
        self.assertEqual(self.controller.recording_bars(0, child.token % 128), 1)

    def test_dynamic_off_grid_start_counts_start_relative_bars_and_freezes_meter(self):
        helper = dynamic.DynamicRecordingTests()
        helper.make()
        self.addCleanup(helper.doCleanups)
        reference, take = helper.begin_in_seven_eight()
        helper.observe_start(take, 12)
        helper.at(15.499)
        self.assertEqual(helper.controller.recording_bars(0, take.token % 128), 0)
        helper.at(15.5)
        self.assertEqual(helper.controller.recording_bars(0, take.token % 128), 1)
        helper.song.signature_numerator = 4
        helper.song.signature_denominator = 4
        helper.at(19)
        self.assertEqual(helper.controller.recording_bars(0, take.token % 128), 2)
        self.assertEqual(reference.loop_end, 4)


class PresentationBarTests(unittest.TestCase):
    def host(self, count):
        host = ui_fixture.Host()
        host.ui = DeviceUI(host.send, lambda group:host.fields[group], lambda:host.meter,
                           lambda:host.now, recording_bars=lambda group,take:count)
        host.fields[0].update({106:2,107:7})
        host.ui.view,host.ui.recording_group,host.ui.active_take = 2,0,7
        return host

    def test_reuses_only_recording_byte_five_and_caps_wire_count_without_extra_ccs(self):
        host = self.host(144)
        payload = host.ui.presentation()
        self.assertEqual(payload[5:7], (111,0))
        for raw,expected in ((111,111),(112,111),(127,111),(70,70)):
            host.ui._recording_bars=lambda *_,raw=raw:raw
            self.assertEqual(host.ui.presentation()[5],expected)
        frame = p.encode_presentation(1,payload)
        wire = compact_wire.project_wire(frame)
        self.assertEqual(len(frame),10)
        self.assertEqual(len(wire),10)
        self.assertEqual([m[1] for m in wire],[35]+list(range(40,49)))
        host.ui.view,host.ui.chord_mask = 0,129
        self.assertEqual(host.ui.presentation()[5:7],(1,1))

    def test_queued_wrong_take_stale_and_provider_failure_emit_zero_but_finish_pending_does_not(self):
        host = self.host(3)
        host.fields[0][106] = 3
        self.assertFalse(host.ui.presentation()[4]&2)
        self.assertEqual(host.ui.presentation()[5],3)
        for state,token in ((1,7),(2,8),(4,7)):
            host.fields[0].update({106:state,107:token})
            self.assertEqual(host.ui.presentation()[5:7],(0,0))
        host.fields[0].update({106:2,107:7})
        host.ui.begin_wait = True
        self.assertEqual(host.ui.presentation()[5],0)
        host.ui.begin_wait = False
        host.ui._recording_bars=lambda *_:(_ for _ in ()).throw(RuntimeError('stale read'))
        self.assertEqual(host.ui.presentation()[5],0)

    def test_new_take_replaces_queued_old_count_and_waits_for_matching_snapshot(self):
        sent=[]
        out=OutputPacer(sent.append)
        fields=dict.fromkeys(range(91,109),0)
        fields.update({91:p.PLAYING,106:2,107:7})
        host=self.host(5)
        out.offer(p.encode_snapshot(0,1,fields),100)
        out.flush(100)
        out.presentation(p.encode_presentation(1,host.ui.presentation()),100.001,(0,p.PLAYING,7))
        host.fields[0][107]=8
        host.ui.active_take=8
        host.ui._recording_bars=lambda *_:0
        out.presentation(p.encode_presentation(2,host.ui.presentation()),100.002,(0,p.PLAYING,8))
        out.flush(100.003)
        self.assertEqual(len(sent),1)
        fields[107]=8
        out.offer(p.encode_snapshot(0,2,fields),100.004)
        out.flush(100.004)
        views=[m for m in sent if m[0][1]==20]
        self.assertEqual(len(views),1)
        self.assertEqual(views[0][4][2],8)
        self.assertEqual(views[0][6][2],0)


class SurfaceBarTests(RecordingSurfaceCase):
    def test_actual_surface_provider_publishes_count_without_native_actions_or_extra_wire_words(self):
        take,slot=self.record()
        self.native.song.current_song_time=slot.clip.start_time+12
        before=list(self.native.calls)
        self.assertEqual(self.surface._ui.presentation()[5:7],(3,0))
        self.assertEqual(self.native.calls,before)
        selected=[]
        original=self.surface._output.send
        def send(messages):
            selected.append(list(messages))
            original(messages)
        self.surface._output.send=send
        self.surface._publish_presentation(force=True)
        self.pump(.4)
        views=[m for m in selected if m[0][:2]==(191,20)]
        self.assertTrue(views)
        self.assertEqual(views[-1][6][2],3)
        self.assertEqual(len(compact_wire.project_wire(views[-1])),10)
        self.assertEqual(self.native.calls,before)

    def test_retry_replaces_actual_surface_queued_old_count_before_new_take_view_sends(self):
        take,slot=self.record()
        self.native.song.current_song_time=slot.clip.start_time+8
        selected=[]
        original=self.surface._output.send
        def send(messages):
            selected.append(list(messages))
            original(messages)
        self.surface._output.send=send
        self.advance(.2)
        self.surface._output.clear()
        self.surface._publish(force=True,only=0)
        self.surface._output.flush(self.now.now)
        self.surface._publish_presentation(force=True)
        self.assertEqual(self.surface._output.frames['presentation',]['messages'][6][2],2)
        for group in (1,2):self.surface._publish(force=True,only=group)
        self.surface._output.flush(self.now.now)
        self.tap(2.5,9.5)
        replacement=self.surface._engine.session.take
        self.assertNotEqual(replacement.token,take.token)
        self.pump(.4)
        views=[m for m in selected if m[0][:2]==(191,20)]
        self.assertTrue(views)
        self.assertTrue(any(m[4][2]==replacement.token%128 for m in views))
        self.assertTrue(all(m[6][2]==0 for m in views))
        latest=None
        for messages in selected:
            if messages[0][:2]==(176,40):latest=messages[17][2]
            elif messages[0][:2]==(191,20) and messages[1][2]==2:
                self.assertEqual(messages[4][2],latest)


if __name__=='__main__':unittest.main()
