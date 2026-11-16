"""Dispatch/preparation tests over queued Native requests, not hardware proof."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PerformanceController.backend import Backend, REJECT
from PerformanceController.fake_native import PerformanceSong, Track, Parameter

Q={'q_bar':4,'q_half':5,'q_quarter':7,'q_eight':9,'q_sixtenth':11}

class AssignmentParameter(Parameter):
    def __init__(self,name,value=.25):
        self.name,self.reads,self.writes=name,0,[]
        super().__init__(value)
        self.writes.clear()
    @property
    def value(self):
        self.reads+=1
        return self._value
    @value.setter
    def value(self,value):
        self.writes.append(value);self._value=value

class AssignmentRack:
    def __init__(self,name,names):
        self.name,self.parameter_reads=name,0
        self._parameters=[AssignmentParameter(name) for name in names]
    @property
    def parameters(self):
        self.parameter_reads+=1
        return self._parameters

class BackendChecks(unittest.TestCase):
    def make(self,groups=2,compatible=True,assignments=None):
        self.clock=0.;self.song=PerformanceSong(groups,compatible)
        self.backend=Backend(self.song,Q,assignments,now=lambda:self.clock)
        self.addCleanup(self.backend.close)
        return self.backend
    def ticks(self,n=12):
        for unused in range(n):
            self.clock+=.101;self.backend.tick()
    def warm(self):self.ticks(16)
    def named_macros(self):
        b=self.make(groups=1);group=b.state.groups[0].group
        first=AssignmentRack('Random Rev + Delay V2',
            ['Device On','Reverb','Reverb Variance','Reverb Crazyness','Delay',
             'Delay Variance','Delay Crazyness'])
        second=AssignmentRack('Stereo Random Filter',
            ['Device On','Change Rate','Rate Range','Macro 3','Macro 4','FX Vol'])
        group.devices=[first,second]
        targets=[{'kind':'device','device_path':[0],
                  'expected_device_name':first.name,'parameter_name':name}
                 for name in ('Reverb','Delay')]
        targets+=[{'kind':'device','device_path':[1],
                   'expected_device_name':second.name,'parameter_name':'FX Vol'},
                  {'kind':'send','index':3}]
        b.state.assignments={0:targets};b.state.discover()
        return b,group,first,second,targets

    def test_guarded_named_macros_preserve_all_three_fx_controls(self):
        b,group,first,second,unused=self.named_macros()
        macros=(first._parameters[1],first._parameters[4],second._parameters[5])
        first.parameter_reads=second.parameter_reads=0;self.song.track_reads=0
        for key,value,macro in zip((2,3,4),(0,63,127),macros):
            self.assertEqual(b.dispatch(key,value),value)
            self.assertEqual(macro.writes,[value/127.])
        self.assertEqual(b.dispatch(5,33),33)
        self.assertEqual(first.parameter_reads+second.parameter_reads,0)
        self.assertEqual(self.song.track_reads,0)
        self.assertEqual(group.mixer_device.sends[3].value,33/127.)
        self.assertFalse(first._parameters[0].writes)

    def test_wrong_rack_name_disables_its_macro_without_disabling_other_bars(self):
        b,group,first,second,unused=self.named_macros()
        first.name='Unrelated rack';b.state.observe(0)
        self.assertEqual(b.dispatch(2,100),REJECT)
        self.assertEqual(first._parameters[1].writes,[])
        self.assertEqual(b.dispatch(4,75),75)
        self.assertEqual(b.dispatch(0,40),40)
        self.assertEqual(b.dispatch(1,64),64)
        self.assertEqual(b.dispatch(5,60),60)

    def test_wrong_initial_rack_guard_does_not_inspect_or_write_its_parameters(self):
        b,group,first,unused,targets=self.named_macros()
        for target in targets[:2]:target['expected_device_name']='Another rack'
        first.parameter_reads=0;b.state.discover()
        self.assertEqual(first.parameter_reads,0)
        self.assertEqual(b.dispatch(2,100),REJECT)
        self.assertTrue(all(not p.writes for p in first._parameters))
        self.assertEqual(b.dispatch(4,75),75)

    def test_missing_or_ambiguous_named_macro_never_falls_back_to_index(self):
        for names in (['Other'],['Reverb','Reverb']):
            with self.subTest(names=names):
                b,group,first,unused,targets=self.named_macros()
                first._parameters=[AssignmentParameter(name) for name in names]
                targets[0]['parameter_index']=0;b.state.discover()
                self.assertEqual(b.dispatch(2,100),REJECT)
                self.assertTrue(all((p.reads,p.writes)==(0,[]) for p in first._parameters))
                self.assertEqual(b.dispatch(5,50),50)

    def test_named_binding_changes_remain_disabled_after_structure_is_restored(self):
        for change in ('parameter reorder','parameter replacement','rack replacement',
                       'rack reorder','macro rename','macro ambiguity'):
            with self.subTest(change=change):
                b,group,first,second,unused=self.named_macros()
                self.assertEqual(b.dispatch(2,40),40)
                first._parameters[1].writes.clear()
                original=list(first._parameters)
                if change=='parameter reorder':first._parameters.reverse()
                elif change=='parameter replacement':
                    first._parameters[1]=AssignmentParameter('Reverb')
                elif change=='rack replacement':
                    group.devices[0]=AssignmentRack(first.name,[p.name for p in original])
                elif change=='rack reorder':group.devices.reverse()
                elif change=='macro rename':first._parameters[1].name='Former Reverb'
                else:first._parameters[2].name='Reverb'
                b.state.observe(0)
                self.assertEqual(b.dispatch(2,100),REJECT)
                first._parameters=original;original[1].name='Reverb'
                original[2].name='Reverb Variance';group.devices=[first,second]
                b.state.discover()
                self.assertEqual(b.dispatch(2,80),REJECT)
                self.assertTrue(all(not p.writes for p in first._parameters))

    def test_guarded_index_target_checks_expected_parameter_name(self):
        for parameter_name in ('Reverb','Wrong macro'):
            with self.subTest(parameter_name=parameter_name):
                b,group,first,unused,targets=self.named_macros()
                targets[0]={'kind':'device','device_path':[0],'parameter_index':1,
                            'expected_device_name':first.name,
                            'expected_parameter_name':'Reverb'}
                first._parameters[1].name=parameter_name;b.state.discover()
                expected=70 if parameter_name=='Reverb' else REJECT
                self.assertEqual(b.dispatch(2,70),expected)
                self.assertEqual(len(first._parameters[1].writes),parameter_name=='Reverb')

    def begin(self,token=3):
        self.assertEqual(self.backend.dispatch(64,token),0)
        take=self.backend.recording.take
        self.assertIsNotNone(take);self.assertEqual(take.state,'queued')
        self.song.advance(take.target-.5);self.ticks(1)
        self.song.advance(take.target);self.ticks(1)
        self.assertEqual(take.state,'recording')
        return take
    def accept(self,take,next_token=None):
        self.song.advance(take.anchor+2.)
        key=67 if next_token is not None else 65
        value=take.take_id | ((next_token or 0)<<7)
        self.assertEqual(self.backend.dispatch(key,value),0)
        self.ticks(1)
        target=take.finish_target
        self.song.advance(target);self.ticks(1)
        self.assertFalse(take.clip.is_recording)
        self.assertTrue(take.clip.is_playing)

    def test_parameter_seed_precedes_available_and_reports_unbound_sentinel(self):
        targets=[{'kind':'send','index':i} for i in (0,1,2,99)]
        b=self.make(assignments={0:targets});b.reseed()
        facts=b.drain_facts(200)
        group_at=next(i for i,(k,a,v) in enumerate(facts) if k==3 and a==0)
        before=[(k,a,v) for k,a,v in facts[:group_at] if k==1 and a<6]
        self.assertEqual([a for k,a,v in before],list(range(6)))
        self.assertEqual(before[-1],(1,5,128))
        self.assertEqual(b.dispatch(5,100),REJECT)

    def test_ready_dispatch_never_reads_track_graph_or_creates(self):
        b=self.make();self.warm()
        self.song.track_reads=0;self.song.calls.clear()
        for value in range(128):self.assertEqual(b.dispatch(0,value),value)
        self.assertNotEqual(b.dispatch(56,3),REJECT)
        self.assertEqual(b.dispatch(64,3),0)
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(any(call[0] in ('create_track','create_scene') for call in self.song.calls))
        self.assertEqual(b.recording.take.state,'queued')

    def test_existing_compatible_empty_tracks_reused_without_creation(self):
        b=self.make();self.warm()
        self.assertEqual(set(b.reserves),{0,1})
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))
        first=b.reserves[0].track;take=self.begin()
        self.assertIs(take.track,first)

    def test_four_prepared_lanes_expand_in_background_and_reuse_after_clear(self):
        b=self.make(groups=1)
        group=b.state.groups[0].group;anchor=b.state.anchors[0]
        for index in range(3):
            track=Track(self.song,'Prepared source %d'%index,group)
            self.song.routes(track,group,True)
            self.song._tracks.insert(self.song._tracks.index(anchor),track)
        b.state.discover();self.warm()
        initial=tuple(b.state.groups[0].tracks)
        selected=self.song.view.selected_track
        creates=lambda:sum(call[0]=='create_track' for call in self.song.calls)
        self.assertEqual(len(initial),4)
        self.assertEqual(creates(),0)
        accepted=[]
        for token in range(1,4):
            take=self.begin(token);self.ticks(8)
            self.accept(take)
            take.clip.name='Original source %d'%token;take.clip.color=token
            accepted.append(take.clip)
            self.assertEqual(creates(),0)
        fourth=self.begin(4);self.ticks(12)
        # The fourth recording consumes the last prepared lane. Only bounded
        # maintenance creates/configures its next destination, before Next.
        self.assertEqual(creates(),1)
        spare=b.reserves[0].track
        self.assertNotIn(spare,initial)
        self.assertFalse(b.tasks)
        direct=[t for t in self.song.tracks if t.group_track is group]
        self.assertEqual(tuple(direct[:-2]),initial)
        self.assertIs(direct[-2],spare)
        self.assertIs(direct[-1],anchor)
        self.assertIs(self.song.view.selected_track,selected)
        self.song.advance(fourth.anchor+2.)
        self.assertEqual(b.dispatch(67,4|(5<<7)),0)
        self.assertEqual(creates(),1)
        fifth=fourth.following
        self.assertIs(fifth.track,spare)
        self.assertEqual(fifth.target,fourth.finish_target)
        self.ticks(1);self.song.advance(fourth.finish_target);self.ticks(1)
        self.assertIs(b.recording.take,fifth)
        self.assertEqual(fifth.state,'recording')
        self.assertEqual(fifth.anchor,fourth.finish_target)
        fourth.clip.name='Original source 4';fourth.clip.color=4
        accepted.append(fourth.clip)
        self.assertEqual(b.dispatch(68,5),0);self.ticks(20)
        self.assertFalse(fifth.slot.has_clip)
        # A next spare may also be prepared while the fifth take starts. Clear
        # reuses the existing lanes, including those with historical clips.
        expanded=tuple(b.state.groups[0].tracks);created=creates()
        self.assertEqual(b.dispatch(104,0),0);self.ticks(16)
        fresh=self.begin(6)
        self.assertIn(fresh.track,initial)
        self.assertEqual(fresh.row,1)
        self.assertEqual(tuple(b.state.groups[0].tracks),expanded)
        self.assertEqual(creates(),created)
        self.assertEqual([track.clip_slots[0].clip for track in initial],accepted)
        self.assertEqual([(clip.name,clip.color) for clip in accepted],
                         [('Original source %d'%token,token) for token in range(1,5)])

    def test_next_reserve_prepared_while_recording_before_accept_next(self):
        b=self.make();self.warm();take=self.begin()
        self.ticks(8)
        self.assertIn(0,b.reserves)
        self.assertIsNot(b.reserves[0].track,take.track)
        self.accept(take,next_token=4)
        self.ticks(2)
        next_take=b.recording.take
        self.assertEqual(next_take.take_id,4)
        self.assertEqual(next_take.state,'recording')
        self.assertEqual(next_take.target,take.finish_target)
        self.assertEqual(next_take.anchor,take.finish_target)
        group=b.state.groups[0].group;anchor=b.state.anchors[0]
        direct=[t for t in self.song.tracks if t.group_track is group]
        self.assertIs(direct[-1],anchor)
        self.assertLess(direct.index(take.track),direct.index(next_take.track))
        self.assertLess(direct.index(next_take.track),direct.index(anchor))

    def base_and_overdub(self):
        b=self.make(groups=1)
        self.song.add_clip(group=0,length=16.,phase=1.)
        b.state.topology_dirty=True
        self.warm();old=self.begin(10);self.ticks(8)
        return b,old

    def test_next_same_bar_target_and_actual_anchor_with_four_bar_base(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+1.)
        before=len(self.song.calls)
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        following=old.following
        self.assertEqual(old.finish_target,old.anchor+4.)
        self.assertEqual(following.target,old.finish_target)
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls[before:]))
        self.ticks(1)
        self.assertEqual(old.slot.pending[1],following.slot.pending[1])
        self.song.advance(old.finish_target);self.ticks(1)
        self.assertIs(b.recording.take,following)
        self.assertEqual(following.take_id,11)
        self.assertEqual(following.anchor,old.finish_target)
        self.assertTrue(old.clip.is_playing)
        observed=[event for event in b.events if event['event']=='recording_observed' and event['token']==11]
        self.assertEqual(observed[-1]['anchor'],old.finish_target)

    def test_next_near_bar_chooses_one_later_bar_for_both_sides(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+3.99)
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        self.assertEqual(old.finish_target,old.anchor+8.)
        self.assertEqual(old.following.target,old.finish_target)

    def test_late_pair_admission_defers_both_without_base_loop_alignment(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+1.)
        original=old.anchor+4.
        class SlowArmTrack(Track):
            def __setattr__(self,name,value):
                super().__setattr__(name,value)
                if name=='arm' and value and self.__dict__.pop('advance_on_arm',False):
                    self.song.current_song_time=original-.01
        spare=b.reserves[0].track;spare.__class__=SlowArmTrack;spare.advance_on_arm=True
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        self.assertEqual(old.finish_target,original+4.)
        self.assertEqual(old.following.target,old.finish_target)
        self.assertFalse(old.finish_issued)
        self.song.advance(old.finish_target-.5);self.ticks(1)
        self.assertEqual(old.slot.pending[1],old.following.slot.pending[1])

    def test_next_without_reserve_keeps_old_take_and_clears_requested_token(self):
        b=self.make();self.warm();old=self.begin(9)
        b.reserves.pop(0,None)
        before=len(self.song.calls)
        self.assertEqual(b.dispatch(67,9|(10<<7)),REJECT)
        self.assertEqual(old.state,'recording')
        self.assertIsNone(old.finish_target)
        self.assertIsNone(old.following)
        self.assertIsNone(b.recording.request_token)
        self.assertFalse(any(c[0] in ('create_track','fire') for c in self.song.calls[before:]))

    def test_cancel_old_token_before_handoff_keeps_accepted_old_clip(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+1.)
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        following=old.following
        self.assertEqual(b.dispatch(68,10),0)
        self.ticks(1)
        self.assertIsNone(old.following)
        self.song.advance(old.finish_target);self.ticks(1)
        self.assertTrue(old.slot.has_clip)
        self.assertTrue(old.clip.is_playing)
        self.assertFalse(old.clip.is_recording)
        self.assertFalse(following.slot.has_clip)
        self.assertFalse(b.busy)

    def test_cancel_old_token_after_handoff_targets_only_known_following(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+1.)
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        following=old.following
        self.ticks(1)
        self.song.advance(old.finish_target);self.ticks(1)
        self.assertIs(b.recording.take,following)
        self.assertEqual(b.dispatch(68,10),0)
        self.ticks(1)
        self.assertTrue(old.slot.has_clip)
        self.assertTrue(old.clip.is_playing)
        self.assertFalse(following.slot.has_clip)
        self.assertFalse(b.busy)

    def test_new_token_command_retires_old_cancel_alias(self):
        b,old=self.base_and_overdub()
        self.song.advance(old.anchor+1.)
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        following=old.following
        self.ticks(1)
        self.song.advance(old.finish_target);self.ticks(1)
        self.song.advance(following.anchor+1.)
        self.assertEqual(b.dispatch(65,11),0)
        self.assertIsNone(b.handoff_alias)
        self.assertEqual(b.dispatch(68,10),REJECT)
        self.ticks(1)
        self.song.advance(following.finish_target);self.ticks(1)
        self.assertTrue(following.slot.has_clip)
        self.assertTrue(following.clip.is_playing)
        self.assertFalse(following.clip.is_recording)
        self.assertTrue(old.slot.has_clip)

    def test_retry_old_cancel_while_replacement_pending_preserves_accepted_clips(self):
        b,old=self.base_and_overdub()
        accepted=[slot.clip for track in b.state.groups[0].tracks for slot in track.clip_slots
                  if slot.has_clip and slot.clip is not old.clip]
        self.assertEqual(b.dispatch(66,10|(11<<7)),0)
        b.reserves.pop(0,None)
        self.ticks(1)
        self.assertIsNotNone(b.pending_begin)
        self.assertEqual(b.pending_begin[:2],(0,11))
        self.assertEqual(b.dispatch(68,10),0)
        self.ticks(1)
        self.assertIsNone(b.pending_begin)
        self.assertIsNone(b.handoff_alias)
        self.assertFalse(b.busy)
        remaining=[slot.clip for track in b.state.groups[0].tracks for slot in track.clip_slots if slot.has_clip]
        self.assertEqual(remaining,accepted)

    def test_retry_old_cancel_after_admission_targets_exact_replacement(self):
        b,old=self.base_and_overdub()
        accepted=[slot.clip for track in b.state.groups[0].tracks for slot in track.clip_slots
                  if slot.has_clip and slot.clip is not old.clip]
        self.assertEqual(b.dispatch(66,10|(11<<7)),0)
        self.ticks(1)
        replacement=b.recording.take
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.take_id,11)
        self.assertIs(b.handoff_alias[3],replacement)
        self.song.advance(replacement.target-.5);self.ticks(1)
        self.song.advance(replacement.target);self.ticks(1)
        self.assertEqual(replacement.state,'recording')
        self.assertEqual(b.dispatch(68,10),0)
        self.ticks(1)
        self.assertFalse(replacement.slot.has_clip)
        self.assertFalse(b.busy)
        remaining=[slot.clip for track in b.state.groups[0].tracks for slot in track.clip_slots if slot.has_clip]
        self.assertEqual(remaining,accepted)

    def test_retry_new_context_retires_old_cancel_alias(self):
        b,old=self.base_and_overdub()
        self.assertEqual(b.dispatch(66,10|(11<<7)),0)
        self.ticks(1);replacement=b.recording.take
        self.song.advance(replacement.target-.5);self.ticks(1)
        self.song.advance(replacement.target);self.ticks(1)
        self.song.advance(replacement.anchor+1.)
        self.assertEqual(b.dispatch(65,11),0)
        self.assertIsNone(b.handoff_alias)
        self.assertEqual(b.dispatch(68,10),REJECT)
        self.assertTrue(replacement.slot.has_clip)

    def test_cancel_countdown_keeps_manual_content_and_returns_idle(self):
        b=self.make();self.warm()
        self.assertEqual(b.dispatch(64,7),0)
        take=b.recording.take
        self.assertEqual(b.dispatch(68,7),0)
        self.ticks(1)
        self.assertIsNone(b.recording.take)
        self.assertFalse(take.slot.has_clip)
        self.assertFalse(any(c[0]=='delete' for c in self.song.calls))
        self.assertFalse(take.track.arm)

    def test_manual_routing_change_invalidates_reserve_before_any_launch(self):
        b=self.make();self.warm()
        track=b.reserves[0].track;track.input_routing_channel='2 SM58 (2)'
        self.assertEqual(b.dispatch(64,18),0)
        self.assertIsNone(b.recording.take)
        self.assertIsNotNone(b.pending_begin)
        self.assertFalse(any(c[0]=='fire' for c in self.song.calls))

    def test_stale_token_cannot_accept_or_cancel_new_take(self):
        b=self.make();self.warm();take=self.begin(22)
        self.assertEqual(b.dispatch(65,21),REJECT)
        self.assertEqual(b.dispatch(68,21),REJECT)
        self.assertEqual(take.state,'recording')

    def test_retry_uses_new_token_and_discards_only_owned_take(self):
        b=self.make();self.warm();take=self.begin(6);self.ticks(8)
        self.assertEqual(b.dispatch(66,6|(7<<7)),0)
        self.ticks(3)
        self.assertFalse(take.slot.has_clip)
        replacement=b.recording.take
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.take_id,7)
        self.assertEqual(replacement.state,'queued')
        self.assertEqual(len([c for c in self.song.calls if c[0]=='delete']),1)

    def test_missing_infrastructure_fails_recoverably_without_creating(self):
        b=self.make();anchor=b.state.anchors[0];anchor.name='Unmarked';b.state.topology_dirty=True
        self.ticks(1)
        self.assertEqual(b.dispatch(64,31),0)
        self.ticks(4)
        self.assertIsNone(b.pending_begin)
        facts=b.drain_facts(200)
        self.assertTrue(any(k==5 and ((a>>3)&127)==31 and ((a>>10)&7)==5 for k,a,v in facts))
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))

    def test_restored_remote_anchor_prepares_without_begin_or_clearing_other_failures(self):
        b=self.make();anchor=b.state.anchors[0]
        anchor.name='Unmarked';b.state.topology_dirty=True;self.ticks(16)
        self.assertEqual(b.failures[0],'rightmost Remote subgroup unavailable')
        self.assertNotIn(0,b.reserves)
        b.failures[1]='ambiguous previous preparation'
        anchor.name='[Remote]';b.state.topology_dirty=True;self.ticks(16)
        self.assertNotIn(0,b.failures)
        self.assertIn(0,b.reserves)
        self.assertEqual(b.failures[1],'ambiguous previous preparation')
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))

    def test_ambiguous_created_track_never_auto_retries(self):
        b=self.make(compatible=False);self.song.misplace_new_track=True
        self.ticks(20)
        first=len([c for c in self.song.calls if c[0]=='create_track'])
        self.ticks(40)
        self.assertEqual(len([c for c in self.song.calls if c[0]=='create_track']),first)
        self.assertEqual(first,2)
        self.assertTrue(b.failures)

    def test_clear_preserves_clips_and_advances_only_its_group(self):
        b=self.make();self.warm();take=self.begin();self.accept(take)
        clip=take.clip
        calls_before=len(self.song.calls)
        self.assertEqual(b.dispatch(104,0),0)
        self.assertIs(take.slot.clip,clip)
        self.assertFalse(clip.is_playing)
        self.assertTrue(all(call[0]!='delete' for call in self.song.calls[calls_before:]))
        self.assertEqual(b.recording._preferred_rows[0],1)

    def test_reordering_bound_device_disables_only_affected_control(self):
        b=self.make()
        group=b.state.groups[0].group
        first,second=NS(parameters=[Parameter(.2)]),NS(parameters=[Parameter(.4)])
        group.devices=[first,second]
        b.state.assignments={0:[{'kind':'device','device_path':[0],'parameter_index':0}]+[{'kind':'send','index':i} for i in (1,2,3)]}
        b.state.discover()
        group.devices.reverse();b.state.observe(0)
        self.assertEqual(b.dispatch(2,99),REJECT)
        self.assertNotEqual(b.dispatch(3,99),REJECT)
        self.assertEqual(second.parameters[0].value,.4)

    def test_multiple_creations_do_not_leak_selection_repair_listeners(self):
        b=self.make(compatible=False)
        self.ticks(80)
        self.assertEqual(len(self.song.view.listeners),0)
        self.assertEqual(len(self.song.view.scene_listeners),0)
        self.assertEqual(len([c for c in self.song.calls if c[0]=='create_track']),2)

    def test_active_take_observation_does_not_scan_all_scene_slots(self):
        from unittest.mock import patch
        from PerformanceController.fake_native import Slot
        b=self.make(groups=1)
        # A large Session grid must not turn each recording tick into a grid scan.
        self.song.scenes.extend(object() for _ in range(1996))
        for track in self.song.tracks:
            if not track.is_foldable:
                track.clip_slots.extend(Slot(track,i) for i in range(4,2000))
        b.state.discover()
        b.state.scan_occupancy(0,10000)
        self.warm();self.begin();self.ticks(8)
        reads=[0];original=Slot.has_clip.fget
        def read(slot):
            reads[0]+=1
            return original(slot)
        with patch.object(Slot,'has_clip',property(read)):
            for unused in range(4):
                before=reads[0];self.ticks(1)
                self.assertLess(reads[0]-before,140)

    def test_failed_start_has_bounded_terminal_state_without_replay(self):
        b=self.make();self.warm()
        b.reserves[0].slot.fire_error='before'
        self.assertEqual(b.dispatch(64,9),0);take=b.recording.take
        self.song.advance(take.target-.5);self.ticks(1)
        self.assertTrue(take.start_issued)
        self.song.advance(take.target+8.);self.ticks(1)
        self.assertEqual(take.state,'completed')
        self.assertFalse(take.track.arm)
        self.assertEqual(len([c for c in self.song.calls if c[0]=='fire']),1)

    def test_ambiguous_start_is_observed_without_second_fire(self):
        b=self.make();self.warm()
        b.reserves[0].slot.fire_error='after'
        self.assertEqual(b.dispatch(64,9),0);take=b.recording.take
        self.song.advance(take.target-.5);self.ticks(1)
        self.song.advance(take.target);self.ticks(1)
        self.assertEqual(take.state,'recording')
        self.assertEqual(len([c for c in self.song.calls if c[0]=='fire']),1)

    def test_near_boundary_accept_moves_to_following_safe_bar(self):
        b=self.make();self.warm();take=self.begin()
        self.song.advance(7.99)
        self.assertEqual(b.dispatch(65,take.take_id),0)
        self.assertEqual(take.finish_target,12.)

    def test_manual_recording_supersedes_failed_preparation_without_replaying(self):
        b=self.make();b.failed_take=(0,31);b.last_take=(0,31)
        track,slot,clip=self.song.add_clip(group=1,recording=True)
        b.state.topology_dirty=True
        self.ticks(1)
        self.assertIsNone(b.failed_take)
        self.assertEqual(b.recording.take.group,1)
        self.assertIs(b.recording.take.clip,clip)
        self.assertFalse(any(c[0] in ('fire','delete','stop') for c in self.song.calls))

    def test_metronome_controls_do_not_start_transport(self):
        b=self.make();self.song.is_playing=False
        self.assertEqual(b.dispatch(114,7),7)
        self.assertEqual(b.dispatch(115,8),8)
        self.assertEqual(b.dispatch(113,127),127)
        self.assertEqual(b.dispatch(112,0),0)
        self.clock+=.5;self.assertEqual(b.dispatch(112,0),0)
        self.assertFalse(self.song.is_playing)
        self.assertEqual(self.song.tempo,60.)

class FixedPoolChecks(unittest.TestCase):
    make=BackendChecks.make
    ticks=BackendChecks.ticks
    warm=BackendChecks.warm

    def pool(self,lanes=8):
        from unittest.mock import patch
        from PerformanceController import configuration
        self.patch=patch.object(configuration,'ALLOW_AUDIO_TRACK_CREATION',False,create=True)
        self.patch.start();self.addCleanup(self.patch.stop)
        b=self.make(groups=3)
        item=b.state.groups[2];anchor=b.state.anchors[2]
        original=item.tracks[0]
        if lanes==0:self.song._tracks.remove(original)
        else:
            original.name='Previous T-Bone lane'
            for index in range(lanes-1):
                track=Track(self.song,'Previous SM58 lane %d'%index,item.group)
                self.song.routes(track,item.group,True)
                self.song._tracks.insert(self.song._tracks.index(anchor),track)
        b.state.discover()
        # Model the destructive/graph-changing operations explicitly. A pool
        # performance is forbidden to reach any of them, even in maintenance.
        self.structural_attempts=[]
        def forbidden(kind):
            def attempt(*args,**kwargs):
                self.structural_attempts.append(kind)
                raise AssertionError('structural Native operation in fixed pool: '+kind)
            return attempt
        self.song.create_audio_track=forbidden('Audio creation')
        self.song.create_scene=forbidden('scene creation')
        self.song.delete_track=forbidden('track deletion')
        self.addCleanup(lambda:self.assertEqual(self.structural_attempts,[]))
        class ConfiguredLane(Track):
            def __setattr__(self,key,value):
                if key in ('input_routing_type','input_routing_channel','output_routing_type',
                           'output_routing_channel','current_monitoring_state'):
                    raise AssertionError('routing write in fixed pool')
                super().__setattr__(key,value)
        for item in b.state.groups.values():
            for track in item.tracks:track.__class__=ConfiguredLane
        self.song.calls.clear()
        return b

    def begin_r3(self,token):
        self.assertEqual(self.backend.dispatch(74,token),0)
        take=self.backend.recording.take
        self.assertIsNotNone(take)
        self.song.advance(take.target-.5);self.ticks(1)
        self.song.advance(take.target);self.ticks(1)
        self.assertEqual(take.state,'recording')
        self.ticks(8)
        return take

    def accept_r3(self,take):
        self.song.advance(take.anchor+2.)
        self.assertEqual(self.backend.dispatch(75,take.take_id),0)
        self.ticks(1);self.song.advance(take.finish_target);self.ticks(1)
        self.assertEqual(take.state,'completed')

    def test_eight_layers_exhaust_without_creation_and_clear_reuses_archived_lanes(self):
        b=self.pool();self.warm();tracks=tuple(b.state.groups[2].tracks);accepted=[]
        for token in range(1,9):
            take=self.begin_r3(token)
            if token==8:
                self.assertEqual(b.dispatch(77,8|(9<<7)),REJECT)
                self.assertEqual(take.state,'recording')
                self.assertIsNone(take.finish_target)
            self.accept_r3(take)
            take.clip.name='Original source %d'%token;take.clip.color=token
            accepted.append(take.clip)
        self.ticks(8)
        self.assertEqual(b.dispatch(74,10),REJECT)
        self.assertIsNone(b.pending_begin)
        self.assertFalse(b.busy)
        self.assertEqual(b.dispatch(106,0),0);self.ticks(12)
        fresh=self.begin_r3(11)
        self.assertIn(fresh.track,tracks);self.assertEqual(fresh.row,1)
        self.assertEqual(b.dispatch(78,11),0);self.ticks(3)
        self.assertEqual([t.clip_slots[0].clip for t in tracks],accepted)
        self.assertEqual([(c.name,c.color) for c in accepted],
                         [('Original source %d'%i,i) for i in range(1,9)])
        self.assertEqual(tuple(b.state.groups[2].tracks),tracks)
        self.assertFalse(b.busy)

    def test_retry_at_full_capacity_reuses_its_discarded_lane(self):
        b=self.pool();self.warm();accepted=[]
        for token in range(1,8):
            take=self.begin_r3(token);self.accept_r3(take);accepted.append(take.clip)
        old=self.begin_r3(8)
        self.assertEqual(b.dispatch(76,8|(9<<7)),0);self.ticks(4)
        replacement=b.recording.take
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.take_id,9)
        self.assertIs(replacement.track,old.track)
        self.assertEqual(replacement.state,'queued')
        self.assertTrue(all(clip in [s.clip for t in b.state.groups[2].tracks for s in t.clip_slots]
                            for clip in accepted))
        self.assertEqual(b.dispatch(78,9),0);self.ticks(3)
        self.assertFalse(b.busy)

    def test_same_bar_next_uses_final_pool_lane_then_cancel_keeps_accepted_layers(self):
        b=self.pool();self.warm();accepted=[]
        for token in range(1,7):
            take=self.begin_r3(token);self.accept_r3(take);accepted.append(take.clip)
        old=self.begin_r3(7)
        self.song.advance(old.anchor+1.)
        self.assertEqual(b.dispatch(77,7|(8<<7)),0)
        following=old.following
        self.assertEqual(following.target,old.finish_target)
        self.ticks(1);self.song.advance(old.finish_target);self.ticks(1)
        self.assertIs(b.recording.take,following)
        self.assertEqual(following.state,'recording')
        self.assertEqual(following.anchor,old.finish_target)
        self.assertEqual(b.dispatch(78,8),0);self.ticks(3)
        self.assertFalse(b.busy)
        self.assertTrue(old.slot.has_clip)
        self.assertFalse(following.slot.has_clip)
        self.assertTrue(all(clip in [s.clip for t in b.state.groups[2].tracks for s in t.clip_slots]
                            for clip in accepted))

    def test_unknown_empty_pool_fails_promptly_without_eight_second_preparing(self):
        b=self.pool(lanes=0)
        self.assertEqual(b.dispatch(74,42),0)
        self.ticks(3)
        self.assertIsNone(b.pending_begin)
        self.assertFalse(b.busy)
        self.assertEqual(b.failed_take,(2,42))
        self.assertLess(self.clock,.5)
        facts=b.drain_facts(200)
        self.assertTrue(any(k==5 and (a&7)==2 and ((a>>3)&127)==42 and ((a>>10)&7)==5
                            for k,a,v in facts))

    def test_missing_preallocated_scene_rejects_without_scene_creation(self):
        b=self.pool();b.recording._preferred_rows[2]=len(self.song.scenes)
        self.warm()
        self.assertEqual(b.dispatch(74,21),REJECT)
        self.assertIsNone(b.pending_begin)
        self.assertFalse(b.busy)

    def test_incompatible_pool_never_reconfigures_routing_or_creates(self):
        b=self.pool()
        # Bypass the sentinel only to model a user's external routing change.
        for track in b.state.groups[2].tracks:object.__setattr__(track,'input_routing_channel','2 SM58 (2)')
        self.warm()
        self.assertEqual(b.dispatch(74,22),REJECT)
        self.assertFalse(b.busy)

    def test_cancel_and_retry_reuse_pool_without_discarding_accepted_clip(self):
        b=self.pool();self.warm();first=self.begin_r3(1);self.accept_r3(first)
        accepted=first.clip
        second=self.begin_r3(2)
        self.assertEqual(b.dispatch(76,2|(3<<7)),0);self.ticks(3)
        replacement=b.recording.take
        self.assertIsNotNone(replacement);self.assertEqual(replacement.take_id,3)
        self.assertEqual(b.dispatch(78,2),0);self.ticks(3)
        self.assertFalse(b.busy)
        self.assertIs(first.slot.clip,accepted)
        self.assertFalse(second.slot.has_clip)
        self.assertFalse(replacement.slot.has_clip)

if __name__=='__main__':unittest.main()
