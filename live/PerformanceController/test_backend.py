"""Dispatch/preparation tests over queued Native requests, not hardware proof."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PerformanceController.backend import Backend, REJECT
from PerformanceController.fake_native import PerformanceSong, Track, Parameter

Q={'q_bar':4,'q_half':5,'q_quarter':7,'q_eight':9,'q_sixtenth':11}

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
        self.assertEqual(b.dispatch(67,10|(11<<7)),0)
        original=old.finish_target
        self.song.advance(original+.01);self.ticks(1)
        self.assertEqual(old.finish_target,original+4.)
        self.assertEqual(old.following.target,old.finish_target)
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
        self.assertEqual(b.dispatch(64,9),0);take=b.recording.take
        take.slot.fire_error='before'
        self.song.advance(take.target-.5);self.ticks(1)
        self.assertTrue(take.start_issued)
        self.song.advance(take.target+8.);self.ticks(1)
        self.assertEqual(take.state,'completed')
        self.assertFalse(take.track.arm)
        self.assertEqual(len([c for c in self.song.calls if c[0]=='fire']),1)

    def test_ambiguous_start_is_observed_without_second_fire(self):
        b=self.make();self.warm()
        self.assertEqual(b.dispatch(64,9),0);take=b.recording.take
        take.slot.fire_error='after'
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

if __name__=='__main__':unittest.main()
