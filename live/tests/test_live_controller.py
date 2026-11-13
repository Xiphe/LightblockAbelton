import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.live_controller import LiveController
from live_test_support import Song, Track, Clip
from live_test_support import Parameter
from types import SimpleNamespace as NS

Q={'q_bar':4,'q_half':5,'q_quarter':7,'q_eight':9,'q_sixtenth':11}


class AssignmentParameter(Parameter):
    def __init__(self, name, value=.25):
        self.name, self.writes, self.reads = name, [], 0
        super().__init__(value)
        self.writes.clear()

    @property
    def value(self):
        self.reads += 1
        return self._value

    @value.setter
    def value(self, value):
        self.writes.append(value)
        self._value = value


class LiveControllerTests(unittest.TestCase):
    def make(self, **kwargs):
        self.song=Song(**kwargs)
        self.controller=LiveController(self.song, quantizations=Q, monotonic=lambda:0.)
        self.seq=0
        return self.controller

    def command(self, op, group=0, arg=0):
        self.seq+=1
        return p.Command(self.seq,group,op,arg)

    def record(self, group=0):
        self.assertEqual(self.controller.dispatch(self.command(p.BEGIN,group)),p.OUTCOME_QUEUED)
        take=self.controller.recording.take
        self.start(take)
        self.assertEqual(take.state,'recording')
        return take

    def start(self, take):
        self.song.advance(take.target-.5)
        self.controller.tick()
        self.song.advance(take.target)
        self.controller.tick()

    def finish(self, take, duration=4):
        self.song.advance(take.anchor+duration-1)
        self.assertEqual(self.controller.dispatch(self.command(p.ACCEPT,take.group)),p.OUTCOME_QUEUED)
        self.song.advance(take.finish_target)
        self.controller.tick()
        return take.clip

    def test_constructor_and_readiness_never_create_arm_or_launch_native_objects(self):
        c=self.make()
        for _ in range(3):
            self.assertEqual(c.readiness()['state'],5)
            self.assertTrue(c.modal_allowed())
            self.assertIsNone(c.active_recording())
            c.snapshot(0);c.global_clock();c.group_clock(0)
        self.assertEqual(self.song.calls,[])

    def test_transport_stopped_begin_is_noop_but_mute_and_mixer_work(self):
        c=self.make();self.song.is_playing=False
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.REJECTED)
        self.assertIsNone(c.active_recording());self.assertEqual(self.song.calls,[])
        self.assertEqual(c.readiness()['state'],5)
        self.assertEqual(c.dispatch(self.command(p.SET_MUTE,arg=127)),p.APPLIED)
        self.assertEqual(c.dispatch(self.command(p.SET_VOLUME,arg=100)),p.APPLIED)
        self.assertFalse(c.snapshot(0)[103]&p.CAP_RECORDING)

    def test_populated_reopen_save_as_and_ordinary_rename_reconcile_from_live(self):
        c=self.make();_,_,clip=self.song.add_clip(row=1,length=12,phase=3)
        self.song.file_path='/copied/save-as.als'
        self.song.tracks[0].name='[R1] Renamed'
        c=LiveController(self.song,quantizations=Q,monotonic=lambda:0.)
        self.assertEqual(c.snapshot(0)[91],p.PLAYING)
        self.assertEqual((c.group_clock(0).span,c.group_clock(0).position),(12,3))
        self.assertEqual(self.song.calls,[])

    def test_empty_group_allocates_track_and_preserves_live_defaults(self):
        c=self.make();take=self.record()
        self.assertTrue(take.slot.clip.is_recording)
        self.assertEqual((take.track.input_routing_type,take.track.input_routing_channel,
                          take.track.output_routing_type,take.track.current_monitoring_state),
                         ('Factory Input','Factory Channel','Factory Output','Factory Monitor'))
        self.assertEqual(c.snapshot(0)[106],2)
        self.assertIsNotNone(c.recording_clock(0,take.take_id))

    def test_new_takes_create_default_tracks_and_never_reuse_quiet_manual_tracks(self):
        c=self.make(layers=1);manual=self.song.tracks[1];take=self.record()
        self.assertTrue(any(call[0]=='create_track' for call in self.song.calls))
        self.assertIsNot(take.track,manual)
        self.finish(take)
        c.dispatch(self.command(p.ACK_COMPLETION,arg=take.take_id))
        self.song.advance(take.clip.start_time+1)
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.OUTCOME_QUEUED)
        self.assertTrue(any(call[0]=='create_track' for call in self.song.calls))

    def test_wrongly_placed_created_track_is_preserved_and_not_recorded(self):
        c=self.make();self.song.misplace_new_track=True
        before=len(self.song.tracks)
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.NOT_READY)
        self.assertEqual(len(self.song.tracks),before+1)
        self.assertFalse(any(call[0]=='fire' for call in self.song.calls))
        self.assertIsNone(c.active_recording())

    def test_failed_group_insertion_retry_does_not_create_endless_tracks(self):
        c=self.make();self.song.misplace_new_track=True
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.NOT_READY)
        count=len(self.song.tracks)
        for _ in range(4):
            self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.NOT_READY)
        self.assertEqual(len(self.song.tracks),count)
        misplaced=self.song.tracks[1]
        misplaced.group_track=self.song.tracks[0]
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.OUTCOME_QUEUED)
        self.assertEqual(len(self.song.tracks),count)

    def test_retry_selects_a_new_reference_from_live_for_the_replacement_take(self):
        c=self.make();base,_,clip=self.song.add_clip(length=12,phase=1)
        c.dispatch(self.command(p.BEGIN));old=c.recording.take;self.start(old)
        _,_,new_base=self.song.add_clip(length=8,phase=2)
        self.assertEqual(c.dispatch(self.command(p.RETRY)),p.APPLIED)
        self.assertIs(c.recording.take.reference.clip,new_base)
        self.assertEqual(c.recording.take.reference.length,8)

    def test_manual_stop_after_issued_accept_preserves_actual_loop(self):
        c=self.make();take=self.record();self.song.advance(6)
        c.dispatch(self.command(p.ACCEPT))
        take.slot.pending=None;take.slot.is_triggered=False;take.track.fired_slot_index=-1
        take.clip.is_recording=False;take.clip.loop_end=2.5;take.clip.start_time=6.5
        c.tick()
        self.assertEqual(take.clip.loop_end,2.5)
        self.assertEqual(take.state,'completed')
        self.assertFalse(any(call[0]=='delete' for call in self.song.calls))

    def test_external_loop_edit_after_native_finish_is_not_normalized_away(self):
        c=self.make();take=self.record();self.song.advance(6)
        c.dispatch(self.command(p.ACCEPT));self.song.advance(take.finish_target)
        take.clip.end_marker=take.clip.loop_end
        take.clip.loop_end=3.5
        c.tick()
        self.assertEqual(take.clip.loop_end,3.5)
        self.assertEqual(take.state,'completed')

    def test_dynamic_scene_growth_preserves_all_existing_recordings(self):
        c=self.make();_,_,old=self.song.add_clip(row=4,playing=False)
        take=self.record()
        self.assertEqual(take.row,5)
        self.assertGreaterEqual(len(self.song.scenes),6)
        self.assertFalse(any(call[0]=='delete' for call in self.song.calls))
        self.assertFalse(old.is_recording)

    def test_reference_snapshot_is_per_recording_and_does_not_follow_reorder_or_relaunch(self):
        c=self.make();a,_,first=self.song.add_clip(length=12,phase=2)
        b,_,second=self.song.add_clip(length=8,phase=1)
        self.assertEqual(c.group_clock(0).span,8)
        c.dispatch(self.command(p.BEGIN));take=c.recording.take
        target=take.target
        second.is_playing=False
        self.song.tracks.remove(a);self.song.tracks.append(a)
        self.assertEqual(c.group_clock(0).span,12)
        self.assertEqual(take.reference.length,8)
        self.assertEqual(take.target,target)
        self.assertEqual(c.countdown_remaining_bars(0,take.take_id),2)

    def test_pending_state_does_not_infer_start_from_elapsed_clock(self):
        c=self.make();c.dispatch(self.command(p.BEGIN));take=c.recording.take
        self.song.advance(take.target,apply=False)
        self.assertEqual(c.snapshot(0)[106],1)
        self.assertIsNone(c.countdown_clock(0,take.take_id))
        self.assertEqual(c.recording_bars(0,take.take_id),0)

    def test_adopts_manual_take_and_read_callbacks_issue_no_recording_effects(self):
        c=self.make();track,slot,clip=self.song.add_clip(recording=True,phase=1)
        pair=c.active_recording()
        self.assertEqual(pair[0],0)
        self.assertEqual(c.snapshot(0)[106],2)
        self.assertFalse(c.modal_allowed())
        for _ in range(3):
            self.assertEqual(c.active_recording(),pair)
            c.snapshot(0);c.recording_clock(*pair)
        self.assertEqual(self.song.calls,[])
        self.assertEqual(c.dispatch(self.command(p.ACCEPT)),p.OUTCOME_QUEUED)
        self.assertEqual(self.song.calls[-1][0],'fire')

    def test_selected_manual_recording_group_can_change_without_stopping_other_takes(self):
        c=self.make();left,_,a=self.song.add_clip(group=0,recording=True)
        right,_,b=self.song.add_clip(group=1,recording=True)
        self.assertEqual(c.active_recording()[0],0)
        self.song.view.selected_track=right
        self.assertEqual(c.active_recording()[0],1)
        self.assertTrue(a.is_recording and b.is_recording)
        self.assertEqual(self.song.calls,[])

    def test_accept_waits_for_actual_native_finish_and_preserves_clip(self):
        c=self.make();take=self.record()
        self.song.advance(6)
        c.dispatch(self.command(p.ACCEPT))
        self.song.advance(take.finish_target,apply=False)
        self.assertEqual(c.snapshot(0)[106],3)
        self.song.advance(take.finish_target)
        c.tick()
        self.assertEqual(c.snapshot(0)[106],4)
        self.assertTrue(take.clip.is_playing)
        self.assertIsNone(c.active_recording())
        self.assertFalse(take.track.arm)

    def test_accept_next_queues_finish_before_coincident_next_start(self):
        c=self.make();self.song.add_clip(length=8,phase=1)
        c.dispatch(self.command(p.BEGIN));take=c.recording.take
        self.start(take)
        # Choose a finish which is also the captured base's next wrap.
        target=take.reference.next_wrap(take.anchor)
        self.song.advance(target-1)
        c.dispatch(self.command(p.ACCEPT_NEXT));following=take.following
        self.assertEqual(following.target,take.finish_target)
        fires=[call for call in self.song.calls if call[0]=='fire']
        self.assertEqual(fires[-2][1],take.track)
        self.assertEqual(fires[-1][1],following.track)
        self.song.advance(following.target);c.tick()
        self.assertIs(c.recording.take,following)
        self.assertTrue(take.clip.is_playing)
        self.assertTrue(following.clip.is_recording)
        self.assertFalse(take.track.arm,'completed parent is disarmed even when child is promoted')

    def test_advance_stops_mixed_group_only_and_waits_for_actual_quiet(self):
        c=self.make();a,_,clip_a=self.song.add_clip(row=0)
        b,_,clip_b=self.song.add_clip(row=1)
        other,_,clip_other=self.song.add_clip(group=1)
        self.song.defer_stop=True;command=self.command(p.ADVANCE)
        self.assertIsNone(c.dispatch(command))
        self.assertTrue(clip_a.is_playing and clip_b.is_playing)
        self.assertEqual(c.tick(),[])
        a.quiet();b.quiet()
        self.assertEqual(c.tick(),[(command,p.APPLIED)])
        self.assertTrue(clip_other.is_playing)
        self.assertEqual(c.dispatch(self.command(p.BEGIN)),p.OUTCOME_QUEUED)
        self.assertEqual(c.recording.take.row,2)

    def test_abort_waits_for_native_quiet_and_deletes_only_the_exact_unaccepted_take(self):
        c=self.make();take=self.record();_,_,other=self.song.add_clip(group=1)
        self.song.defer_stop=True;command=self.command(p.ABORT)
        self.assertIsNone(c.dispatch(command))
        self.assertTrue(take.slot.has_clip)
        take.track.quiet()
        self.assertEqual(c.tick(),[(command,p.APPLIED)])
        self.assertFalse(take.slot.has_clip)
        self.assertTrue(other.is_playing)
        self.assertIsNone(c.active_recording())

    def test_retry_discards_take_then_waits_for_pinned_reference_wrap(self):
        c=self.make();self.song.add_clip(length=12,phase=1)
        c.dispatch(self.command(p.BEGIN));old=c.recording.take
        self.start(old)
        command=self.command(p.RETRY)
        result=c.dispatch(command)
        self.assertEqual(result,p.APPLIED)
        new=c.recording.take
        self.assertIsNot(old,new)
        self.assertFalse(old.slot.has_clip)
        self.assertEqual(new.reference.length,12)
        self.assertGreater(new.target,self.song.current_song_time)

    def test_ambiguous_native_fire_exception_is_never_replayed(self):
        c=self.make(layers=1)
        self.song.current_song_time=0.0
        c.dispatch(self.command(p.BEGIN));take=c.recording.take
        take.slot.fire_error='after'
        self.song.advance(1);c.tick()
        for _ in range(5):c.tick()
        self.assertEqual(len([call for call in self.song.calls if call[0]=='fire']),1)
        self.start(take)
        self.assertEqual(c.snapshot(0)[106],2)

    def test_deleted_or_replaced_take_never_authorizes_delete_of_new_clip(self):
        c=self.make();take=self.record()
        original=take.clip
        _,_,replacement=self.song.add_clip(group=1)
        take.slot.clip=replacement
        c.tick()
        self.assertFalse(any(call[0]=='delete' for call in self.song.calls))
        self.assertFalse(original is replacement)

    def test_long_countdown_uses_pages_then_final_one_bar_and_actual_record_bars(self):
        c=self.make();self.song.add_clip(length=72*4,phase=1)
        c.dispatch(self.command(p.BEGIN));take=c.recording.take
        self.assertEqual(c.countdown_clock(0,take.take_id).span,32)
        self.song.advance(take.target-32)
        self.assertEqual(c.countdown_clock(0,take.take_id).span,4)
        self.song.advance(take.target-1);c.tick()
        self.song.advance(take.target);c.tick()
        self.assertEqual(c.recording_bars(0,take.take_id),0)
        self.song.advance(take.anchor+4)
        self.assertEqual(c.recording_bars(0,take.take_id),1)
        self.assertEqual(c.recording_clock(0,take.take_id).span,4)
        self.song.signature_numerator=3
        self.assertEqual(c.recording_clock(0,take.take_id).span,4,'current take retains captured bar metadata')

    def test_late_accept_is_respected_at_the_following_safe_bar(self):
        c=self.make();take=self.record();self.song.advance(7.999)
        c.dispatch(self.command(p.ACCEPT))
        self.assertEqual(take.finish_target,12)
        self.assertFalse(take.finish_issued)
        self.song.advance(10);c.tick()
        self.assertTrue(take.finish_issued)
        self.song.advance(12);c.tick()
        self.assertEqual(take.clip.loop_end,8)

    def test_missed_unissued_countdown_boundary_defers_without_a_second_take(self):
        c=self.make();self.song.add_clip(length=8,phase=1)
        c.dispatch(self.command(p.BEGIN));take=c.recording.take
        first=take.target
        self.song.advance(first+.01);c.tick()
        self.assertIs(c.recording.take,take)
        self.assertGreater(take.target,first)
        self.assertFalse(take.start_issued)
        self.start(take)
        self.assertEqual(take.state,'recording')
        self.assertEqual(len([x for x in self.song.calls if x[0]=='fire']),1)

    def test_live_recording_started_during_unissued_countdown_takes_precedence(self):
        c=self.make();self.song.add_clip(length=32,phase=1)
        c.dispatch(self.command(p.BEGIN));pending=c.recording.take
        track,_,clip=self.song.add_clip(group=1,recording=True)
        self.song.view.selected_track=track
        self.assertEqual(c.active_recording()[0],1)
        self.song.advance(pending.target-1);c.tick()
        self.assertFalse(pending.start_issued)
        self.assertTrue(clip.is_recording)

    def test_abandoned_native_finish_returns_to_recording_without_automatic_retry(self):
        c=self.make();take=self.record();self.song.advance(6)
        c.dispatch(self.command(p.ACCEPT));self.assertTrue(take.finish_issued)
        take.slot.pending=None;take.slot.is_triggered=False;take.track.fired_slot_index=-1
        self.song.advance(take.finish_target+5);c.tick()
        self.assertEqual(take.state,'recording')
        self.assertFalse(take.finish_issued)
        self.assertEqual(len([x for x in self.song.calls if x[0]=='fire']),2)
        c.tick()
        self.assertEqual(len([x for x in self.song.calls if x[0]=='fire']),2)
        self.assertEqual(c.dispatch(self.command(p.ACCEPT)),p.OUTCOME_QUEUED)

    def test_queued_native_start_cancelled_in_live_is_not_automatically_reissued(self):
        c=self.make();c.dispatch(self.command(p.BEGIN));take=c.recording.take
        take.slot.pending=None;take.slot.is_triggered=False;take.track.fired_slot_index=-1
        self.song.advance(take.target+5);c.tick()
        self.assertIsNone(c.active_recording())
        self.assertFalse(take.track.arm)
        self.assertEqual(len([x for x in self.song.calls if x[0]=='fire']),1)

    def test_fresh_recording_native_rounding_is_only_normalized_with_unchanged_markers(self):
        c=self.make();take=self.record();self.song.advance(6)
        c.dispatch(self.command(p.ACCEPT));self.song.advance(take.finish_target)
        take.clip.loop_end=8;take.clip.end_marker=8
        before=len(self.song.calls)
        self.assertEqual(c.active_recording(),(0,take.take_id))
        self.assertEqual(take.clip.loop_end,8,'read-only UI reconciliation never writes loop bounds')
        self.assertEqual(len(self.song.calls),before)
        c.tick()
        self.assertEqual(take.clip.loop_end,4)
        self.assertEqual(take.state,'completed')

    def test_cancel_api_failure_releases_cancel_view_and_preserves_native_recording(self):
        c=self.make();take=self.record()
        def fail(quantized):raise RuntimeError('Native stop unavailable')
        take.track.stop_all_clips=fail
        self.assertEqual(c.dispatch(self.command(p.ABORT)),p.NOT_READY)
        self.assertEqual(c.active_recording(),(0,take.take_id))
        self.assertEqual(c.snapshot(0)[106],2)
        self.assertTrue(take.clip.is_recording)
        self.assertFalse(any(x[0]=='delete' for x in self.song.calls))

    def test_cancel_loses_race_to_manual_finish_and_preserves_completed_audio(self):
        c=self.make();take=self.record()
        take.clip.is_recording=False;take.clip.start_time=self.song.current_song_time
        self.assertEqual(c.dispatch(self.command(p.ABORT)),p.REJECTED)
        self.assertTrue(take.slot.has_clip)
        self.assertIsNone(c.active_recording())

    def test_duplicate_remote_marker_recovers_after_rename_without_script_reload(self):
        c=self.make();duplicate=Track(self.song,'[R1] Copy',foldable=True)
        self.song.tracks.append(duplicate)
        self.assertEqual(c.snapshot(0)[91],p.UNAVAILABLE)
        self.assertEqual(c.snapshot(1)[91],p.EMPTY)
        duplicate.name='Unassigned group'
        self.assertEqual(c.snapshot(0)[91],p.EMPTY)
        self.assertEqual(c.readiness()['state'],5)

    def test_abort_never_deletes_manual_recording_in_unissued_following_reservation(self):
        c=self.make();self.song.add_clip(length=12,phase=1)
        c.dispatch(self.command(p.BEGIN));parent=c.recording.take;self.start(parent)
        self.song.advance(parent.anchor+2)
        c.dispatch(self.command(p.ACCEPT_NEXT));following=parent.following
        self.assertFalse(following.start_issued)
        foreign=Clip(anchor=self.song.current_song_time,recording=True)
        following.slot.clip=foreign;following.track.playing_slot_index=following.row
        following.track.arm=True;self.song.view.selected_track=parent.track
        self.assertEqual(c.dispatch(self.command(p.ABORT)),p.APPLIED)
        self.assertIs(following.slot.clip,foreign)
        self.assertTrue(foreign.is_recording)
        self.assertTrue(following.track.arm)
        self.assertFalse(any(x[0] in ('stop','delete') and x[1] is following.track for x in self.song.calls))
        self.assertFalse(parent.slot.has_clip)

    def test_observe_never_claims_external_clip_in_an_unissued_following_reservation(self):
        c=self.make();self.song.add_clip(length=12,phase=1)
        c.dispatch(self.command(p.BEGIN));parent=c.recording.take;self.start(parent)
        self.song.advance(parent.anchor+2)
        c.dispatch(self.command(p.ACCEPT_NEXT));following=parent.following
        foreign=Clip(anchor=self.song.current_song_time,recording=True)
        following.slot.clip=foreign;following.track.playing_slot_index=following.row
        self.song.view.selected_track=parent.track
        c.tick()
        self.assertIsNone(following.clip)
        self.assertIsNone(following.anchor)
        self.assertFalse(following.start_issued)
        self.assertTrue(foreign.is_recording)

    def test_manually_configured_source_track_is_preserved_and_not_used_for_capture(self):
        c=self.make(layers=1);source=self.song.tracks[1]
        source.devices=[object()];source.input_routing_type='Special source'
        source.current_monitoring_state='In'
        take=self.record()
        self.assertIsNot(take.track,source)
        self.assertFalse(source.arm)
        self.assertEqual(source.input_routing_type,'Special source')
        self.assertEqual(source.current_monitoring_state,'In')
        self.assertEqual(len(source.devices),1)

    def test_retry_reuses_only_its_own_just_emptied_track(self):
        c=self.make();take=self.record();count=len(self.song.tracks)
        self.assertEqual(c.dispatch(self.command(p.RETRY)),p.APPLIED)
        self.assertIs(c.recording.take.track,take.track)
        self.assertEqual(len(self.song.tracks),count)

    def test_explicit_device_assignment_never_retargets_after_device_reorder(self):
        from types import SimpleNamespace as NS
        from live_test_support import Parameter
        c=self.make();group=self.song.tracks[0]
        first,second=NS(parameters=[Parameter(.1)]),NS(parameters=[Parameter(.2)])
        group.devices=[first,second]
        targets=[{'kind':'device','device_path':[0],'parameter_index':0}]+[{'kind':'send','index':i} for i in range(1,4)]
        c.configure_targets(0,targets)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.APPLIED)
        group.devices.reverse()
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=50)),p.NOT_READY)
        self.assertEqual(second.parameters[0].value,.2)
        self.assertEqual(c.dispatch(self.command(p.SET_VOLUME,arg=40)),p.APPLIED)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_2,arg=30)),p.APPLIED)
        self.assertEqual(c.dispatch(self.command(p.SET_MUTE,arg=127)),p.APPLIED)
        c.configure_targets(0,targets)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=50)),p.APPLIED)

    def test_guarded_index_macro_checks_names_before_first_parameter_read_or_write(self):
        for device_name, parameter_name in [('Unrelated rack', 'Reverb'),
                                             ('Random Rev + Delay V2', 'Wrong macro')]:
            with self.subTest(device=device_name, parameter=parameter_name):
                c=self.make();group=self.song.tracks[0]
                macro=AssignmentParameter(parameter_name)
                group.devices=[NS(name=device_name,parameters=[macro])]
                target={'kind':'device','device_path':[0],'parameter_index':0,
                        'expected_device_name':'Random Rev + Delay V2',
                        'expected_parameter_name':'Reverb'}
                c.configure_targets(0,[target]+[{'kind':'send','index':i} for i in range(1,4)])
                self.assertEqual(c.snapshot(0)[99],0)
                self.assertEqual((macro.reads,macro.writes),(0,[]))
                self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.NOT_READY)
                self.assertEqual((macro.reads,macro.writes),(0,[]))
                self.assertNotIn((0,0),c._explicit_bindings)
                for op in (p.SET_VOLUME,p.SET_PAN,p.SET_MUTE,p.SET_CONTROL_4):
                    self.assertEqual(c.dispatch(self.command(op,arg=40 if op!=p.SET_MUTE else 127)),p.APPLIED)
                self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,group=1,arg=70)),p.APPLIED)
                self.assertEqual(self.song.calls,[])

    def test_guarded_index_macro_remains_writable_when_names_match(self):
        c=self.make();macro=AssignmentParameter('Reverb')
        self.song.tracks[0].devices=[NS(name='Random Rev + Delay V2',parameters=[macro])]
        target={'kind':'device','device_path':[0],'parameter_index':0,
                'expected_device_name':'Random Rev + Delay V2','expected_parameter_name':'Reverb'}
        c.configure_targets(0,[target]+[{'kind':'send','index':i} for i in range(1,4)])
        self.assertEqual(c.snapshot(0)[99],32)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.APPLIED)
        self.assertEqual(macro.writes,[100/127.])

    def test_named_macro_finds_unique_parameter_and_retains_its_identity(self):
        c=self.make();macro=AssignmentParameter('Reverb');other=AssignmentParameter('Device On')
        rack=NS(name='Random Rev + Delay V2',parameters=[other,macro])
        self.song.tracks[0].devices=[rack]
        target={'kind':'device','device_path':[0],'expected_device_name':rack.name,'parameter_name':'Reverb'}
        c.configure_targets(0,[target]+[{'kind':'send','index':i} for i in range(1,4)])
        self.assertEqual(c.snapshot(0)[99],32)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.APPLIED)
        rack.parameters.reverse()
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=50)),p.APPLIED)
        self.assertEqual(macro.writes,[100/127.,50/127.])
        self.assertEqual(other.writes,[])
        replacement=AssignmentParameter('Reverb')
        rack.parameters=[other,replacement]
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=80)),p.NOT_READY)
        self.assertEqual(replacement.writes,[])

    def test_missing_or_ambiguous_named_macro_never_falls_back_to_an_index(self):
        for names in [('Other',),('Reverb','Reverb')]:
            with self.subTest(names=names):
                c=self.make();macros=[AssignmentParameter(name) for name in names]
                self.song.tracks[0].devices=[NS(name='Random Rev + Delay V2',parameters=macros)]
                target={'kind':'device','device_path':[0],'expected_device_name':'Random Rev + Delay V2',
                        'parameter_name':'Reverb','parameter_index':0}
                c.configure_targets(0,[target]+[{'kind':'send','index':i} for i in range(1,4)])
                self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.NOT_READY)
                self.assertTrue(all((macro.reads,macro.writes)==(0,[]) for macro in macros))
                self.assertNotIn((0,0),c._explicit_bindings)

    def test_named_macro_rename_stops_writes_without_disabling_other_bars(self):
        c=self.make();macro=AssignmentParameter('Reverb')
        rack=NS(name='Random Rev + Delay V2',parameters=[macro]);self.song.tracks[0].devices=[rack]
        target={'kind':'device','device_path':[0],'expected_device_name':rack.name,'parameter_name':'Reverb'}
        c.configure_targets(0,[target]+[{'kind':'send','index':i} for i in range(1,4)])
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=100)),p.APPLIED)
        macro.name='Renamed'
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=80)),p.NOT_READY)
        self.assertEqual(macro.writes,[100/127.])
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_2,arg=60)),p.APPLIED)

    def test_missing_sends_do_not_disable_volume_pan_or_group_mute(self):
        c=self.make();self.song.tracks[0].mixer_device.sends=[]
        self.assertTrue(c.snapshot(0)[103]&p.CAP_MIXER)
        self.assertEqual(c.dispatch(self.command(p.SET_VOLUME,arg=40)),p.APPLIED)
        self.assertEqual(c.dispatch(self.command(p.SET_PAN,arg=64)),p.APPLIED)
        self.assertEqual(c.dispatch(self.command(p.SET_SEND_1,arg=40)),p.NOT_READY)

    def test_invalid_clock_keeps_overview_and_mute_usable_without_invented_timing(self):
        c=self.make();self.song.get_current_beats_song_time=lambda:None
        self.assertEqual(c.readiness()['state'],5)
        self.assertIsNone(c.global_clock())
        self.assertEqual(c.dispatch(self.command(p.SET_MUTE,arg=127)),p.APPLIED)
        self.assertFalse(c.snapshot(0)[103]&p.CAP_RECORDING)


if __name__=='__main__':unittest.main()
