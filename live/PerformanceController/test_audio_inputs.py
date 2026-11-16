"""Audio patch/bundle policy over queued Native calls, not hardware evidence."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PerformanceController import configuration
from PerformanceController.backend import Backend, REJECT
from PerformanceController.fake_native import PerformanceSong, Track, Clip

Q={'q_bar':4,'q_half':5,'q_quarter':7,'q_eight':9,'q_sixtenth':11}
SPECS=({'name':'T-Bone','helper_name':'[Input 1]'},
       {'name':'SM-58','helper_name':'[Input 2]'})

class AudioInputChecks(unittest.TestCase):
    def make(self,initial=1,groups=2,helper_channels_ready=True):
        self.clock=0.;self.song=PerformanceSong(groups)
        self.routes=(NS(identifier='source-a',display_name='T-Bone'),
                     NS(identifier='source-b',display_name='SM-58'))
        self.post=NS(identifier='postfx',display_name='Post FX')
        roots=[t for t in self.song._tracks if t.is_foldable and t.name.startswith('[R') and t.name!='[Remote]']
        root=Track(self.song,'[Inputs] Sources',foldable=True);self.song._tracks.append(root)
        self.sources=[]
        for i,spec in enumerate(SPECS):
            s=Track(self.song,spec['name'],root);s.color_index=60+i
            s.current_monitoring_state=0;s.output_routing_type='Sends Only'
            for send in s.mixer_device.sends:send.value=send.min
            self.song._tracks.append(s);self.sources.append(s)
        for g,parent in enumerate(roots):
            anchor=next(t for t in self.song._tracks if t.group_track is parent and t.is_foldable)
            for n in range(3):
                t=Track(self.song,'Prepared lane %d'%n,parent);self.song.routes(t,parent,True)
                self.song._tracks.insert(self.song._tracks.index(anchor),t)
            for i,spec in enumerate(SPECS):
                t=Track(self.song,spec['helper_name'],anchor);self.song.routes(t,anchor)
                t.input_routing_type=self.routes[i]
                t.input_routing_channel=self.post if helper_channels_ready else 'Loading routing channel'
                t.mute=not(bool(initial&(1<<i)) and g==0)
                self.song._tracks.insert(self.song._tracks.index(anchor)+1,t)
        for t in self.song._tracks:
            if not t.is_foldable:
                t.available_input_routing_types=list(self.routes)+['Ext. In','No Input']
                t.available_input_routing_channels=[self.post]
        old_create=self.song.create_audio_track
        def create(index):
            old_create(index);t=self.song._tracks[index]
            t.available_input_routing_types=list(self.routes)+['Ext. In','No Input']
            t.available_input_routing_channels=[self.post]
        self.song.create_audio_track=create
        self.config=patch.object(configuration,'AUDIO_SOURCES',SPECS,create=True);self.config.start();self.addCleanup(self.config.stop)
        self.backend=Backend(self.song,Q,now=lambda:self.clock);self.addCleanup(self.backend.close)
        return self.backend
    def ticks(self,n=40):
        for unused in range(n):self.clock+=.101;self.backend.tick()
    def start(self,token=1):
        b=self.backend;self.assertEqual(b.dispatch(64,token),0)
        self.ticks(1);take=b.recording.take;self.assertIsNotNone(take)
        self.song.advance(take.target-.5);self.ticks(1)
        self.song.advance(take.target);self.ticks(1)
        self.assertEqual(take.state,'recording');return take
    @staticmethod
    def members(take):return (take,)+take.members

    def test_first_direct_audio_lane_wins_over_later_prepared_source_and_keeps_its_sound(self):
        b=self.make(groups=1)
        parent=b.state.groups[0].group
        first,second=b.state.groups[0].tracks[:2]
        first.name='[Return] R1 T-Bone'
        first.input_routing_type=self.routes[1];first.input_routing_channel=self.post
        first.output_routing_type='Sends Only';first.current_monitoring_state=0
        first.devices=[object()];first.mute=True
        first.mixer_device.volume.value=.31;first.mixer_device.panning.value=.67
        first.mixer_device.sends[0].value=.43
        historical=Clip(playing=False);historical.name='Previous microphone';historical.color=123
        first.clip_slots[1].clip=historical
        second.input_routing_type=self.routes[0];second.input_routing_channel=self.post
        devices=tuple(first.devices)
        nested=Track(self.song,'Custom subgroup',parent,foldable=True)
        nested_lane=Track(self.song,'Tempting nested lane',nested)
        self.song.routes(nested_lane,nested);nested_lane.input_routing_type=self.routes[0]
        nested_lane.input_routing_channel=self.post
        anchor=b.state.anchors[0];offset=self.song._tracks.index(anchor)
        self.song._tracks[offset:offset]=[nested,nested_lane]
        b.state.topology_dirty=True
        self.ticks(80)
        reserve=b.reserves[0]
        self.assertIs(reserve.track,first)
        self.assertNotIn(nested_lane,b.state.groups[0].tracks)
        self.assertIs(first.input_routing_type,self.routes[0])
        self.assertEqual(first.current_monitoring_state,2)
        self.assertEqual(first.name,'T-Bone')
        self.assertEqual(tuple(first.devices),devices)
        self.assertTrue(first.mute)
        self.assertEqual(first.mixer_device.volume.value,.31)
        self.assertEqual(first.mixer_device.panning.value,.67)
        self.assertEqual(first.mixer_device.sends[0].value,.43)
        self.assertEqual(first.output_routing_type,'Sends Only')
        self.assertIs(first.clip_slots[1].clip,historical)
        self.assertEqual((historical.name,historical.color),('Previous microphone',123))
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))

    def test_two_inputs_to_one_reuses_first_lane_even_when_second_already_matches(self):
        b=self.make(initial=3,groups=1);self.ticks(80)
        first,second=(m.track for m in b.reserves[0].members)
        first.name='[Patch] Custom';second.name='SM-58'
        self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.assertIs(b.reserves[0].track,first)
        self.assertIs(first.input_routing_type,self.routes[1])
        self.assertEqual(first.name,'SM-58')
        self.assertEqual(b.dispatch(48,1),1);self.ticks(60)
        self.assertIs(b.reserves[0].track,first)
        self.assertIs(first.input_routing_type,self.routes[0])
        self.assertEqual(first.name,'T-Bone')
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))

    def test_newly_available_earlier_lane_replaces_a_later_prepared_reserve(self):
        b=self.make(groups=1);first=b.state.groups[0].tracks[0]
        first.arm=True;self.ticks(70)
        later=b.reserves[0].track;self.assertIsNot(later,first)
        first.arm=False;self.ticks(60)
        self.assertIs(b.reserves[0].track,first)
        self.assertFalse(any(c[0] in ('fire','create_track') for c in self.song.calls))

    def test_direct_lane_reuse_skips_frozen_armed_playing_and_triggered_lanes(self):
        b=self.make(groups=1);parent=b.state.groups[0].group
        anchor=b.state.anchors[0]
        original=tuple(b.state.groups[0].tracks)
        original[0].is_frozen=True;original[1].arm=True
        original[2].playing_slot_index=0
        original[3].has_audio_input=False;original[3].has_midi_input=True
        blocked=Track(self.song,'Triggered',parent);self.song.routes(blocked,parent)
        blocked.fired_slot_index=0
        chosen=Track(self.song,'[Input Custom]',parent);self.song.routes(chosen,parent)
        for t in (blocked,chosen):
            t.available_input_routing_types=list(self.routes)
            t.available_input_routing_channels=[self.post]
        self.song._tracks[self.song._tracks.index(anchor):self.song._tracks.index(anchor)]=[blocked,chosen]
        b.state.topology_dirty=True;self.ticks(80)
        self.assertIs(b.reserves[0].track,chosen)
        self.assertTrue(original[1].arm)
        self.assertEqual(original[2].playing_slot_index,0)
        self.assertEqual(blocked.fired_slot_index,0)
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))

    def test_patch_admits_absolute_latest_intent_without_graph_or_routing_work(self):
        b=self.make();self.ticks();self.song.track_reads=0;self.song.calls.clear()
        for mask in (3,1,2,3):self.assertEqual(b.dispatch(48,mask),mask)
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)
        self.assertEqual(b.audio_inputs.desired[0],3)
        self.ticks(40);self.assertEqual(len(b.reserves[0].members),2)
        self.assertFalse(b.failures)

    def test_helpers_persist_selection_and_only_monitor_owner_is_live(self):
        b=self.make();self.ticks();self.assertEqual(b.dispatch(48,3),3)
        self.assertEqual(b.dispatch(49,1),1);self.ticks(50)
        a=b.audio_inputs
        self.assertEqual(a.monitor_owner,1)
        self.assertEqual([t.mute for t in a.helpers[0]],[False,False])
        self.assertEqual([t.current_monitoring_state for t in a.helpers[0]],[2,2])
        self.assertEqual([t.current_monitoring_state for t in a.helpers[1]],[0,2])
        self.assertEqual(b.dispatch(57,0),0);self.ticks(5)
        self.assertEqual([t.current_monitoring_state for t in a.helpers[0]],[0,0])
        self.assertEqual([t.current_monitoring_state for t in a.helpers[1]],[2,2])

    def test_two_sources_record_finish_same_bar_and_stamp_independent_clips(self):
        b=self.make(initial=3);self.ticks(65);take=self.start()
        self.assertEqual(len(self.members(take)),2)
        self.assertEqual({m.anchor for m in self.members(take)},{take.anchor})
        self.assertEqual([m.clip.name for m in self.members(take)],['T-Bone','SM-58'])
        self.ticks(35);self.song.advance(take.anchor+2.)
        self.assertEqual(b.dispatch(67,1|(2<<7)),0);self.ticks(1)
        following=take.following;self.assertEqual(len(self.members(following)),2)
        self.assertEqual(following.target,take.finish_target)
        self.song.advance(take.finish_target);self.ticks(1)
        self.assertIs(b.recording.take,following)
        self.assertTrue(all(m.clip.is_playing and not m.clip.is_recording for m in self.members(take)))
        self.assertTrue(all(not m.track.arm for m in self.members(take)))
        self.assertTrue(all(m.clip.is_recording for m in self.members(following)))
        self.assertEqual({m.anchor for m in self.members(following)},{take.finish_target})
        self.assertEqual(b.dispatch(68,2),0);self.ticks(5)
        self.assertTrue(all(not m.slot.has_clip for m in self.members(following)))
        self.assertTrue(all(m.slot.has_clip for m in self.members(take)))

    def test_uncertain_second_start_is_not_replayed_and_cleans_owned_members(self):
        b=self.make(initial=3);self.ticks(65)
        reserve=b.reserves[0];reserve.members[1].slot.fire_error='after'
        self.assertEqual(b.dispatch(64,1),0);take=b.recording.take
        self.song.advance(take.target-.5);self.ticks(8)
        fires=[c for c in self.song.calls if c[0]=='fire']
        self.assertEqual(len(fires),2)
        self.assertIsNone(b.recording.take)
        self.assertEqual(b.failed_take,(0,1))
        self.assertTrue(all(not m.slot.has_clip for m in self.members(take)))
        self.assertTrue(all(not m.track.arm for m in self.members(take)))

    def test_pending_patch_retarget_keeps_active_bundle_sources(self):
        b=self.make(initial=3);self.ticks(65);take=self.start()
        old=tuple((m.source.index,m.track,m.clip) for m in self.members(take))
        self.assertEqual(b.dispatch(48,1),1);self.ticks(20)
        self.assertEqual(tuple((m.source.index,m.track,m.clip) for m in self.members(take)),old)
        self.assertTrue(all(m.clip.is_recording for m in self.members(take)))
        self.assertEqual(len(b.reserves[0].members),1)
        self.assertEqual(b.reserves[0].members[0].source.index,0)

    def test_same_label_routes_use_canonical_identity_not_display_text(self):
        b=self.make();self.routes[1].display_name='T-Bone'
        self.assertEqual(b.dispatch(48,2),2);self.ticks(50)
        self.assertIs(b.reserves[0].track.input_routing_type,self.routes[1])
        self.assertIsNot(b.reserves[0].track.input_routing_type,self.routes[0])

    def test_manual_helper_changes_reconcile_without_undoing_them(self):
        b=self.make();self.ticks();helper=b.audio_inputs.helpers[0][1]
        helper.mute=False;helper.current_monitoring_state=0
        b.audio_inputs.helpers[0][0].current_monitoring_state=0
        self.ticks(40)
        self.assertEqual(b.audio_inputs.desired[0],3)
        self.assertFalse(helper.mute)
        self.assertEqual(b.audio_inputs.monitor_owner,0)
        self.assertEqual(len(b.reserves[0].members),2)

    def test_short_monitor_plan_is_bounded_and_disables_before_enabling(self):
        b=self.make(initial=3);self.ticks(65);self.assertEqual(b.dispatch(49,3),3);self.ticks(40)
        calls=[]
        class Watched(Track):
            def __setattr__(self,name,value):
                if name in ('mute','current_monitoring_state'):calls.append((self.group_track.group_track.name,name,value))
                super().__setattr__(name,value)
        for group,items in b.audio_inputs.helpers.items():
            for item in items:item.__class__=Watched
        self.song.track_reads=0
        self.assertEqual(b.dispatch(57,0),0)
        self.ticks(1)
        self.assertLessEqual(len(calls),6)
        modes=[value for group,name,value in calls if name=='current_monitoring_state']
        self.assertEqual(modes,[2,2,0,0])
        self.assertEqual(self.song.track_reads,0)

    def test_uncertain_handoff_preserves_accepted_bundle_and_never_replays(self):
        b=self.make(initial=3);self.ticks(65);take=self.start();self.ticks(40)
        old=tuple(m.clip for m in self.members(take))
        self.song.advance(take.anchor+2.)
        b.reserves[0].members[1].slot.fire_error='after'
        self.assertEqual(b.dispatch(67,1|(2<<7)),0)
        following=take.following
        self.ticks(8)
        self.assertIsNone(b.recording.take)
        self.assertEqual(b.failed_take,(0,2))
        self.assertEqual(tuple(m.slot.clip for m in self.members(take)),old)
        self.assertTrue(all(not m.slot.has_clip for m in self.members(following)))
        fires=sum(c[0]=='fire' for c in self.song.calls);self.ticks(10)
        self.assertEqual(sum(c[0]=='fire' for c in self.song.calls),fires)

    def test_unavailable_selected_source_can_be_removed_but_not_added(self):
        b=self.make(initial=3);self.ticks(65)
        self.sources[1].is_frozen=True;b.audio_inputs.observe_sources()
        self.assertEqual(b.audio_inputs.available,1)
        self.assertFalse(b.audio_inputs.group_mask&1)
        self.assertEqual(b.dispatch(64,1),REJECT)
        self.assertEqual(b.dispatch(48,1),1)
        self.assertEqual(b.dispatch(48,3),REJECT)
        self.ticks(20);self.assertEqual(len(b.reserves[0].members),1)

    def test_missing_second_native_start_finishes_as_failure_not_stuck_countdown(self):
        b=self.make(initial=3);self.ticks(65)
        reserve=b.reserves[0]
        reserve.members[1].slot.fire=lambda **unused:None
        self.assertEqual(b.dispatch(64,1),0);take=b.recording.take
        self.song.advance(take.target-.5);self.ticks(1)
        self.song.advance(take.target);self.ticks(1)
        self.song.advance(take.target+5);self.ticks(4)
        self.assertIsNone(b.recording.take);self.assertEqual(b.failed_take,(0,1))
        self.assertTrue(all(not m.track.arm for m in self.members(take)))
        self.assertTrue(all(not m.slot.has_clip for m in self.members(take)))

    def test_failed_member_arm_never_launches_or_leaves_other_member_armed(self):
        b=self.make(initial=3);self.ticks(65)
        class RefusedArm(Track):
            def __setattr__(self,name,value):
                if name=='arm' and value:raise RuntimeError('Native arm refused')
                super().__setattr__(name,value)
        b.reserves[0].members[1].track.__class__=RefusedArm
        self.assertEqual(b.dispatch(64,1),0);take=b.recording.take
        self.song.advance(take.target-.5);self.ticks(5)
        self.assertFalse(any(c[0]=='fire' for c in self.song.calls))
        self.assertIsNone(b.recording.take);self.assertEqual(b.failed_take,(0,1))
        self.assertTrue(all(not m.track.arm for m in self.members(take)))

    def test_manual_adopted_take_accepts_without_bundle_metadata(self):
        b=self.make();self.ticks(65)
        track=b.state.groups[0].tracks[0];slot=track.clip_slots[0]
        slot.clip=Clip(anchor=4,recording=True);track.playing_slot_index=0;track.arm=True
        self.song.advance(5);b.state.observe(0);self.ticks(1)
        take=b.recording.take;self.assertTrue(take.adopted)
        self.assertEqual(b.dispatch(65,take.take_id),0)
        self.assertEqual(take.state,'finish_queued')
        self.ticks(1);self.song.advance(take.finish_target);self.ticks(2)
        self.assertFalse(slot.clip.is_recording);self.assertIs(slot.clip,take.clip)

    def test_missing_second_finish_is_bounded_for_accept_and_next(self):
        for next_take in (False,True):
            with self.subTest(next_take=next_take):
                b=self.make(initial=3);self.ticks(65);take=self.start();self.ticks(30)
                old=tuple(m.clip for m in self.members(take))
                take.members[0].slot.fire=lambda **unused:None
                self.song.advance(take.anchor+2.)
                key,value=(67,1|(2<<7)) if next_take else (65,1)
                self.assertEqual(b.dispatch(key,value),0)
                following=take.following;target=take.finish_target
                self.ticks(1);self.song.advance(target);self.ticks(2)
                self.song.advance(target+9);self.ticks(100)
                self.assertIsNone(b.recording.take)
                self.assertEqual(b.failed_take,(0,2 if next_take else 1))
                self.assertEqual(tuple(m.slot.clip for m in self.members(take)),old)
                self.assertTrue(all(not m.track.arm for m in self.members(take)))
                if following is not None:
                    self.assertTrue(all(not m.slot.has_clip and not m.track.arm for m in self.members(following)))
                fires=sum(c[0]=='fire' for c in self.song.calls);self.ticks(20)
                self.assertEqual(sum(c[0]=='fire' for c in self.song.calls),fires)

    def test_retry_replaces_whole_bundle_and_old_context_cancel_cleans_replacement(self):
        b=self.make(initial=3);self.ticks(65);old=self.start()
        self.ticks(25)
        self.assertEqual(b.dispatch(66,1|(2<<7)),0);self.ticks(8)
        replacement=b.recording.take
        self.assertIsNotNone(replacement);self.assertIsNot(replacement,old)
        self.assertEqual(replacement.take_id,2)
        self.assertEqual([m.source.index for m in self.members(replacement)],[0,1])
        self.assertTrue(all(not m.slot.has_clip for m in self.members(old)))
        self.song.advance(replacement.target-.5);self.ticks(1)
        self.song.advance(replacement.target);self.ticks(1)
        self.assertTrue(all(m.clip.is_recording for m in self.members(replacement)))
        self.assertEqual(b.dispatch(68,1),0);self.ticks(6)
        self.assertTrue(all(not m.slot.has_clip and not m.track.arm for m in self.members(replacement)))

    def test_routing_failure_rolls_back_to_confirmed_patch_and_ends_pending(self):
        b=self.make();self.ticks(65)
        for track in b.state.groups[0].tracks:
            track.available_input_routing_types=[self.routes[0],'Ext. In','No Input']
        self.assertEqual(b.dispatch(48,3),3)
        for unused in range(40):
            self.ticks(1)
            if 0 in b.audio_inputs.failed:break
        a=b.audio_inputs
        self.assertEqual(a.desired[0],1)
        self.assertEqual(a.prepared_masks[0],1)
        self.assertNotIn(0,a.busy)
        self.ticks(1)
        self.assertEqual([t.mute for t in a.helpers[0]],[False,True])
        self.assertEqual(len(b.reserves[0].members),1)
        self.assertNotIn(0,b.failures)

    def test_failure_from_superseded_patch_cannot_undo_newer_mask_or_owner(self):
        b=self.make();self.ticks(65)
        self.assertEqual(b.dispatch(48,3),3);old=b.audio_inputs.revisions[0]
        self.assertEqual(b.dispatch(48,2),2)
        self.assertEqual(b.dispatch(57,1),1)
        b.audio_inputs.fail_preparation(0,'old route failed',old)
        self.assertEqual(b.audio_inputs.desired[0],2)
        self.assertEqual(b.audio_inputs.monitor_owner,1)
        self.ticks(40);self.assertEqual(b.reserves[0].members[0].source.index,1)

    def test_frozen_source_at_discovery_self_heals_without_topology_change(self):
        b=self.make();self.ticks(65)
        self.sources[0].is_frozen=True;b.audio_inputs.discover();self.ticks(10)
        self.assertEqual(b.audio_inputs.available,2)
        self.assertIn(0,b.audio_inputs.sources)
        self.sources[0].is_frozen=False;self.ticks(40)
        self.assertEqual(b.audio_inputs.available,3)
        self.assertNotIn(0,b.failures)
        self.assertEqual(b.audio_inputs.desired[0],1)
        self.assertIn(0,b.reserves)

    def test_external_member_stop_preserves_clips_and_returns_terminal_failure(self):
        b=self.make(initial=3);self.ticks(65);take=self.start()
        old=tuple(m.clip for m in self.members(take))
        take.track.quiet();b.state.observe(0);self.ticks(5)
        self.assertIsNone(b.recording.take);self.assertEqual(b.failed_take,(0,1))
        self.assertEqual(tuple(m.slot.clip for m in self.members(take)),old)
        self.assertTrue(all(not m.clip.is_recording and not m.track.arm for m in self.members(take)))

    def test_all_unconfirmed_finishes_are_bounded_and_never_replayed(self):
        b=self.make(initial=3);self.ticks(65);take=self.start()
        self.song.advance(take.anchor+2.)
        for member in self.members(take):member.slot.fire=lambda **unused:None
        self.assertEqual(b.dispatch(65,1),0);target=take.finish_target
        self.ticks(1);self.song.advance(target+9);self.ticks(100)
        self.assertIsNone(b.recording.take);self.assertEqual(b.failed_take,(0,1))
        self.assertTrue(all(m.slot.has_clip and not m.clip.is_recording for m in self.members(take)))

    def test_late_route_failure_does_not_resurrect_a_deselected_source(self):
        b=self.make();self.ticks(65);self.assertEqual(b.dispatch(48,0),0);self.ticks(10)
        self.assertEqual(b.audio_inputs.confirmed[0],0)
        for track in b.state.groups[0].tracks:track.available_input_routing_types=[self.routes[0]]
        self.assertEqual(b.dispatch(48,2),2);self.ticks(25)
        self.assertEqual(b.audio_inputs.desired[0],0)
        self.assertEqual(b.audio_inputs.prepared_masks[0],0)
        self.assertEqual([t.mute for t in b.audio_inputs.helpers[0]],[True,True])

    def test_surface_stays_ready_after_manual_deletion_invalidates_completed_clips(self):
        from test_surface import SurfaceChecks, adapter
        b=self.make(initial=3);self.ticks(65);completed=[]
        for token in range(1,2):
            take=self.start(token);self.ticks(35)
            self.song.advance(take.anchor+2.)
            self.assertEqual(b.dispatch(65,token),0);self.ticks(1)
            self.song.advance(take.finish_target);self.ticks(2)
            self.assertEqual(take.state,'completed')
            completed.extend(self.members(take))
        self.assertTrue(all(not member.armed_here and not member.track.arm for member in completed))
        class InvalidNativeClip(Clip):
            def __getattribute__(self,name):
                if not name.startswith('_') and object.__getattribute__(self,'_native_deleted'):
                    raise RuntimeError('Native Clip has been deleted')
                return super().__getattribute__(name)
        surface_case=SurfaceChecks('runTest');surface=surface_case.make()
        surface.backend.close();surface.backend=b;surface._c_instance.song=self.song
        def finish_surface():
            surface.backend=None;surface_case.doCleanups()
        self.addCleanup(finish_surface)
        published=[];surface.health=NS(publish=lambda *fields:published.append(fields))
        for member in completed:
            clip=member.clip;clip._native_deleted=False;clip.__class__=InvalidNativeClip
            member.slot.delete_clip();clip._native_deleted=True
        b.state.observe(0)
        self.assertFalse(b.state.groups[0].playing)
        surface.update_display()
        self.assertEqual(published[-1][0],adapter.READY,
                         list(b.events)[-3:])
        self.assertFalse(any(e['event']=='display_failed' for e in b.events))

    def test_deleting_previous_clips_does_not_interrupt_a_current_recording(self):
        from test_surface import SurfaceChecks, adapter
        b=self.make(initial=3);self.ticks(65);old=self.start();self.ticks(35)
        self.song.advance(old.anchor+2.)
        self.assertEqual(b.dispatch(65,1),0);self.ticks(1)
        self.song.advance(old.finish_target);self.ticks(2)
        current=self.start(2)
        class InvalidNativeClip(Clip):
            def __getattribute__(self,name):
                if not name.startswith('_') and object.__getattribute__(self,'_native_deleted'):
                    raise RuntimeError('Native Clip has been deleted')
                return super().__getattribute__(name)
        surface_case=SurfaceChecks('runTest');surface=surface_case.make()
        surface.backend.close();surface.backend=b;surface._c_instance.song=self.song
        def finish_surface():
            surface.backend=None;surface_case.doCleanups()
        self.addCleanup(finish_surface)
        published=[];surface.health=NS(publish=lambda *fields:published.append(fields))
        for member in self.members(old):
            clip=member.clip;clip._native_deleted=False;clip.__class__=InvalidNativeClip
            member.slot.delete_clip();member.track.quiet();clip._native_deleted=True
        surface.update_display()
        self.assertEqual(published[-1][0],adapter.READY)
        self.assertIs(b.recording.take,current)
        self.assertTrue(all(m.clip.is_recording for m in self.members(current)))
        self.song.advance(current.anchor+2.)
        self.assertEqual(b.dispatch(65,2),0);self.ticks(1)
        self.song.advance(current.finish_target);surface.update_display()
        self.assertEqual(published[-1][0],adapter.READY)
        self.assertTrue(all(m.slot.has_clip and not m.clip.is_recording for m in self.members(current)))
        self.assertFalse(any(e['event']=='display_failed' for e in b.events))

    def test_constructor_recovers_when_saved_helper_channels_settle_after_loading(self):
        b=self.make(initial=3,helper_channels_ready=False)
        self.assertEqual(b.audio_inputs.available,0)
        self.assertFalse(b.audio_inputs.sources)
        self.ticks(12)
        self.assertEqual(b.failures[0],'no available recording source')
        for helpers in b.audio_inputs.helpers.values():
            for helper in helpers:helper.input_routing_channel=self.post
        self.assertFalse(b.state.topology_dirty)
        self.ticks(60)
        self.assertEqual(b.audio_inputs.available,3)
        self.assertEqual(len(b.reserves[0].members),2)
        self.assertNotIn(0,b.failures)

    def test_startup_profile_completion_keeps_configured_source_identity(self):
        b=self.make(initial=3,helper_channels_ready=False)
        self.sources[0].name='Renamed source during load'
        for helpers in b.audio_inputs.helpers.values():
            for helper in helpers:helper.input_routing_channel=self.post
        b.audio_inputs.observe_sources()
        self.assertEqual(b.audio_inputs.available,2)
        self.assertNotIn(0,b.audio_inputs.sources)
        self.sources[0].name='T-Bone'
        b.audio_inputs.observe_sources()
        self.assertEqual(b.audio_inputs.available,3)

    def test_startup_profile_retry_is_bounded_and_stops_reading_ready_helpers(self):
        b=self.make(initial=3,groups=8,helper_channels_ready=False)
        reads=[]
        class CountHelperRoutingReads(Track):
            def __getattribute__(self,name):
                if name in ('input_routing_type','input_routing_channel'):reads.append(name)
                return super().__getattribute__(name)
        for helpers in b.audio_inputs.helpers.values():
            for helper in helpers:helper.__class__=CountHelperRoutingReads
        self.song.track_reads=0;self.song.calls.clear()
        b.audio_inputs.observe_sources()
        self.assertEqual(b.audio_inputs.available,0)
        self.assertEqual(len(reads),4,'only two retained source/helper candidates may be retried')
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)
        for helpers in b.audio_inputs.helpers.values():
            for helper in helpers:helper.input_routing_channel=self.post
        b.audio_inputs.observe_sources()
        self.assertEqual(b.audio_inputs.available,3)
        reads.clear()
        for unused in range(100):b.audio_inputs.observe_sources()
        self.assertFalse(reads,'ready canonical routes must not be read again')
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)

    def test_no_source_rejects_begin_without_native_work(self):
        b=self.make(initial=0);self.ticks();self.song.calls.clear();self.song.track_reads=0
        self.assertEqual(b.dispatch(64,1),REJECT)
        self.assertEqual(self.song.track_reads,0);self.assertFalse(self.song.calls)

    def test_patch_change_reuses_old_lane_without_relabeling_old_clip(self):
        b=self.make();self.ticks();take=self.start();self.song.advance(take.anchor+2.)
        self.assertEqual(b.dispatch(65,1),0);self.ticks(1)
        self.song.advance(take.finish_target);self.ticks(1)
        old=take.clip;old_name,old_color=old.name,old.color
        self.assertEqual(b.dispatch(104,0),0);self.assertEqual(b.dispatch(48,2),2)
        self.ticks(50);fresh=self.start(2)
        self.assertEqual(fresh.clip.name,'SM-58')
        self.assertEqual((old.name,old.color),(old_name,old_color))
        self.assertIs(take.slot.clip,old)

if __name__=='__main__':unittest.main()
