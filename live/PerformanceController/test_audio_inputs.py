"""Audio patch/bundle policy over queued Native calls, not hardware evidence."""
from pathlib import Path
import sys
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PerformanceController import configuration
from PerformanceController.backend import Backend, REJECT
from PerformanceController.fake_native import PerformanceSong, Track, Clip
from PerformanceController.audio_patch_state import SOURCE_KEY, PATCH_KEY, OWNER_KEY
from PerformanceController.audio_inputs import PREPARED_KEY

Q={'q_bar':4,'q_half':5,'q_quarter':7,'q_eight':9,'q_sixtenth':11}
SPECS=({'name':'T-Bone'}, {'name':'SM-58'})

class AudioInputChecks(unittest.TestCase):
    def make(self,initial=1,groups=2,routes_ready=True,source_count=2,lanes=4):
        self.clock=0.;self.song=PerformanceSong(groups)
        self.post=NS(identifier='postfx',display_name='Post FX')
        roots=[t for t in self.song._tracks if t.is_foldable and t.name.startswith('[R') and t.name!='[Remote]']
        root=Track(self.song,'[Inputs] Sources',foldable=True);self.song._tracks.append(root)
        self.input_root=root;self.sources=[]
        identifiers=[]
        specs=SPECS+tuple({'name':'Source %d'%(i+1)} for i in range(2,source_count))
        for i,spec in enumerate(specs):
            source=Track(self.song,spec['name'],root);source.color_index=60+i
            source.current_monitoring_state=0;source.output_routing_type='Sends Only'
            for send in source.mixer_device.sends:send.value=send.min
            identifier='00000000-0000-0000-0000-%012d'%(i+1)
            source.set_data(SOURCE_KEY,identifier);identifiers.append(identifier)
            self.song._tracks.append(source);self.sources.append(source)
        self.routes=tuple(NS(identifier='source-'+str(i),display_name=spec['name'],attached_object=source)
                          for i,(spec,source) in enumerate(zip(specs,self.sources)))
        root.set_data(OWNER_KEY,json.dumps(dict(schema=1,group=0)))
        for g,parent in enumerate(roots):
            selected=[identifiers[i] for i in range(len(identifiers)) if g==0 and initial&(1<<i)]
            parent.set_data(PATCH_KEY,json.dumps(dict(schema=1,sources=selected)))
            anchor=next(t for t in self.song._tracks if t.group_track is parent and t.is_foldable)
            for n in range(lanes-1):
                track=Track(self.song,'Prepared lane %d'%n,parent);self.song.routes(track,parent,True)
                self.song._tracks.insert(self.song._tracks.index(anchor),track)
        for track in self.song._tracks:
            if not hasattr(track,'color_index'):track.color_index=0
            if not track.is_foldable:
                track.available_input_routing_types=(list(self.routes) if routes_ready else [])+['Ext. In','No Input']
                track.available_input_routing_channels=[self.post]
        old_create=self.song.create_audio_track
        def create(index):
            old_create(index);track=self.song._tracks[index]
            track.available_input_routing_types=list(self.routes)+['Ext. In','No Input']
            track.available_input_routing_channels=[self.post]
        self.song.create_audio_track=create
        self.config=patch.object(configuration,'AUDIO_SOURCES',specs,create=True);self.config.start();self.addCleanup(self.config.stop)
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

    def assert_cleanup_or_idle_reserve(self,take):
        reserve=self.backend.reserves.get(take.group)
        for member in self.members(take):
            if not member.track.arm:continue
            self.assertIsNotNone(reserve,'an armed cleaned-up lane must be a ready idle reserve')
            destination=next((m for m in reserve.members if m.track is member.track),None)
            self.assertIsNotNone(destination)
            self.assertTrue(self.backend.monitoring.owned(member.track))
            self.assertEqual(member.track.current_monitoring_state,1)
            self.assertFalse(destination.slot.has_clip)
            self.assertLess(member.track.playing_slot_index,0)


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
        self.assertEqual(first.current_monitoring_state,1)
        self.assertTrue(first.arm and b.monitoring.owned(first))
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

    def test_group_metadata_persists_selection_and_only_owner_reserve_monitors(self):
        b=self.make();self.ticks();self.assertEqual(b.dispatch(48,3),3)
        self.assertEqual(b.dispatch(49,1),1);self.ticks(50)
        a=b.audio_inputs
        self.assertEqual(a.monitor_owner,1)
        self.assertTrue(all(not member.track.arm for member in b.reserves[0].members))
        self.assertTrue(all(member.track.arm and member.track.current_monitoring_state==1
                            and b.monitoring.owned(member.track) for member in b.reserves[1].members))
        for group,mask in ((0,3),(1,1)):
            payload=json.loads(b.state.groups[group].group.get_data(PATCH_KEY,''))
            expected=[a.state_store.source_ids[i] for i in range(2) if mask&(1<<i)]
            self.assertEqual(set(payload['sources']),set(expected))
        self.assertEqual(json.loads(self.input_root.get_data(OWNER_KEY,''))['group'],1)
        self.assertEqual(b.dispatch(57,0),0);self.ticks(8)
        self.assertTrue(all(member.track.arm and member.track.current_monitoring_state==1
                            for member in b.reserves[0].members))
        self.assertTrue(all(not member.track.arm and member.track.current_monitoring_state==2
                            for member in b.reserves[1].members))
        self.assertEqual(json.loads(self.input_root.get_data(OWNER_KEY,''))['group'],0)
        self.assertFalse(any(track.name.startswith('[Input ') for track in self.song._tracks))

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
        self.assert_cleanup_or_idle_reserve(take)
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
        self.assert_cleanup_or_idle_reserve(take)

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

    def test_manual_destination_reroute_does_not_reassign_the_source_choice(self):
        b=self.make();self.ticks();lane=b.reserves[0].track
        lane.input_routing_type=self.routes[1]
        self.ticks(40)
        self.assertEqual(b.audio_inputs.desired[0],1)
        self.assertEqual(b.audio_inputs.state_store.mask(0),1)
        self.assertIs(b.reserves[0].track,lane)
        self.assertIs(lane.input_routing_type.attached_object,self.sources[0])
        self.assertEqual(b.audio_inputs.monitor_owner,0)
        self.assertTrue(lane.arm and lane.current_monitoring_state==1)

    def test_retained_monitor_handoff_disables_old_paths_before_enabling_new(self):
        b=self.make(initial=3);self.ticks(65);self.assertEqual(b.dispatch(49,3),3);self.ticks(40)
        calls=[]
        class Watched(Track):
            def __setattr__(self,name,value):
                if name in ('arm','current_monitoring_state'):
                    calls.append((self.group_track.name,name,value))
                super().__setattr__(name,value)
        for reserve in b.reserves.values():
            for member in reserve.members:member.track.__class__=Watched
        self.song.track_reads=0
        self.assertEqual(b.dispatch(57,0),0)
        self.assertFalse(calls,'monitor gesture admission must use cached state only')
        self.ticks(1)
        modes=[value for group,name,value in calls if name=='current_monitoring_state']
        self.assertEqual(modes,[2,2,1,1])
        self.assertLessEqual(len(calls),8,'two old and two new retained monitoring paths only')
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
        self.sources[1].is_frozen=True;b.audio_inputs.observe_sources(force=True)
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
        self.assert_cleanup_or_idle_reserve(take)
        self.assertTrue(all(not m.slot.has_clip for m in self.members(take)))

    def test_failed_member_arm_never_launches_or_leaves_other_member_armed(self):
        b=self.make(initial=3);self.ticks(65)
        attempts=[]
        class RefusedArm(Track):
            def __setattr__(self,name,value):
                if name=='arm' and value:
                    attempts.append(self)
                    raise RuntimeError('Native arm refused')
                super().__setattr__(name,value)
        failed=tuple(member.track for member in b.reserves[0].members)
        failed[1].arm=False;failed[1].__class__=RefusedArm
        self.assertEqual(b.dispatch(64,1),0)
        self.ticks(100)
        self.assertFalse(any(c[0]=='fire' for c in self.song.calls))
        self.assertIsNone(b.recording.take);self.assertIsNone(b.pending_begin)
        self.assertEqual(b.failed_take,(0,1))
        self.assertTrue(all(not track.arm for track in failed))
        failed_attempts=len(attempts)
        self.assertGreater(failed_attempts,0)
        self.ticks(30)
        self.assertEqual(len(attempts),failed_attempts,'latched Native failure must stop automatic arm retries')
        self.assertTrue(all(not track.arm for track in failed))

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
                self.assert_cleanup_or_idle_reserve(take)
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
        self.assertEqual(a.state_store.mask(0),1)
        self.assertTrue(b.reserves[0].track.arm and b.monitoring.owned(b.reserves[0].track))
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
        self.assertTrue(all(not m.clip.is_recording for m in self.members(take)))
        self.assert_cleanup_or_idle_reserve(take)

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
        self.assertEqual(b.audio_inputs.state_store.mask(0),0)
        self.assertNotIn(0,b.reserves)
        self.assertFalse(any(track.arm for track in b.state.groups[0].tracks))

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

    def test_constructor_recovers_when_native_source_choices_settle_after_loading(self):
        b=self.make(initial=3,routes_ready=False)
        self.assertEqual(b.audio_inputs.available,0)
        self.assertFalse(b.audio_inputs.sources)
        self.ticks(12)
        self.assertEqual(b.failures[0],'no available recording source')
        for group in b.state.groups.values():
            for track in group.tracks:track.available_input_routing_types=list(self.routes)+['Ext. In','No Input']
        self.assertFalse(b.state.topology_dirty)
        self.ticks(60)
        self.assertEqual(b.audio_inputs.available,3)
        self.assertEqual(len(b.reserves[0].members),2)
        self.assertNotIn(0,b.failures)

    def test_startup_profile_uses_actual_source_identity_after_source_rename(self):
        b=self.make(initial=3,routes_ready=False)
        self.sources[0].name='Renamed source during load'
        probe=b.state.groups[0].tracks[0]
        # A same-label route to a destination Track must not replace the source.
        decoy=NS(display_name='Renamed source during load',attached_object=probe)
        for group in b.state.groups.values():
            for track in group.tracks:track.available_input_routing_types=[decoy]+list(self.routes)
        b.audio_inputs.observe_sources(force=True)
        self.assertEqual(b.audio_inputs.available,3)
        self.assertIs(b.audio_inputs.sources[0].track,self.sources[0])
        self.assertEqual(b.audio_inputs.sources[0].name,'Renamed source during load')
        self.ticks(60)
        self.assertIs(b.reserves[0].members[0].track.input_routing_type.attached_object,self.sources[0])

    def test_startup_profile_retry_uses_retained_probe_then_stops_reading_choices(self):
        b=self.make(initial=3,groups=8,routes_ready=False)
        reads=[]
        class CountRoutingReads(Track):
            def __getattribute__(self,name):
                if name=='available_input_routing_types':reads.append(self)
                return super().__getattribute__(name)
        probe=b.state.groups[0].tracks[0];probe.__class__=CountRoutingReads
        self.song.track_reads=0;self.song.calls.clear()
        b.audio_inputs.observe_sources(force=True)
        self.assertEqual(b.audio_inputs.available,0)
        self.assertEqual(len(reads),1,'one retained destination probe read supplies every missing source')
        self.assertTrue(all(track is probe for track in reads))
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)
        probe.available_input_routing_types=list(self.routes)
        b.audio_inputs.observe_sources(force=True)
        self.assertEqual(b.audio_inputs.available,3)
        reads.clear()
        for unused in range(100):b.audio_inputs.observe_sources(force=True)
        self.assertFalse(reads,'completed profiles must not scan available choices again')
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)

    def test_reopen_recovers_marked_idle_arm_without_adding_a_second_monitor(self):
        b=self.make(initial=1,groups=1);self.ticks(80)
        lanes=b.state.groups[0].tracks;first=b.reserves[0].track
        self.assertIs(first,lanes[0])
        marker=json.loads(first.get_data(PREPARED_KEY,''))
        self.assertEqual(marker,dict(schema=1,group=0,source=b.audio_inputs.state_store.source_ids[0],row=0))
        manual=lanes[-1]
        self.assertEqual(manual.get_data(PREPARED_KEY,''),'')
        manual.arm=True;manual.current_monitoring_state=2
        saved_modes=[(track,track.arm,track.current_monitoring_state) for track in lanes]
        b.close()
        # A saved Set restores the pre-close arm/mode values, rather than the
        # old process's shutdown cleanup. All ownership caches are new below.
        for track,arm,mode in saved_modes:
            track.arm=arm;track.current_monitoring_state=mode
        self.song.calls.clear()
        self.backend=Backend(self.song,Q,now=lambda:self.clock);self.addCleanup(self.backend.close)
        fresh=self.backend
        self.assertTrue(fresh.monitoring.owned(first))
        self.assertFalse(fresh.monitoring.owned(manual))
        self.assertFalse(self.song.calls,'saved ownership recovery must not mutate Native state')
        self.ticks(80)
        self.assertIs(fresh.reserves[0].track,first)
        audible=[track for track in fresh.state.groups[0].tracks
                 if track.arm and track.current_monitoring_state==1]
        self.assertEqual(audible,[first])
        self.assertTrue(manual.arm)
        self.assertEqual(manual.current_monitoring_state,2)
        self.assertFalse(fresh.monitoring.owned(manual))
        self.assertFalse(any(call[0]=='create_track' for call in self.song.calls))
        self.assertFalse(fresh.failures)

    def test_reopen_recovers_saved_idle_arm_when_native_routing_settles_later(self):
        b=self.make(initial=1,groups=1);self.ticks(80)
        lanes=b.state.groups[0].tracks;first=b.reserves[0].track
        self.assertIs(first,lanes[0])
        self.assertTrue(first.get_data(PREPARED_KEY,''))
        manual=lanes[-1];manual.arm=True;manual.current_monitoring_state=2
        saved_modes=[(track,track.arm,track.current_monitoring_state) for track in lanes]
        b.close()
        for track,arm,mode in saved_modes:
            track.arm=arm;track.current_monitoring_state=mode
        # Native exposes saved Track objects before attached route objects and
        # offered source choices have completed loading. The source UUID is
        # already saved, so no source identity must be guessed from its name.
        self.routes[0].attached_object=None
        for track in lanes:track.available_input_routing_types=[self.routes[1],'Ext. In','No Input']
        self.song.calls.clear()
        self.backend=Backend(self.song,Q,now=lambda:self.clock);self.addCleanup(self.backend.close)
        fresh=self.backend
        self.assertFalse(fresh.monitoring.owned(first))
        self.assertEqual(fresh.audio_inputs.available,2)
        self.ticks(12)
        self.assertTrue(first.arm and first.current_monitoring_state==1)
        self.assertTrue(manual.arm and manual.current_monitoring_state==2)
        self.assertNotIn(0,fresh.reserves)
        self.assertFalse(fresh.state.topology_dirty)
        self.routes[0].attached_object=self.sources[0]
        for track in lanes:track.available_input_routing_types=list(self.routes)+['Ext. In','No Input']
        self.ticks(60)
        self.assertEqual(fresh.audio_inputs.available,3)
        self.assertTrue(fresh.monitoring.owned(first))
        self.assertIs(fresh.reserves[0].track,first)
        audible=[track for track in fresh.state.groups[0].tracks
                 if track.arm and track.current_monitoring_state==1]
        self.assertEqual(audible,[first])
        self.assertTrue(manual.arm and manual.current_monitoring_state==2)
        self.assertFalse(fresh.monitoring.owned(manual))
        self.assertFalse(any(call[0]=='create_track' for call in self.song.calls))
        self.assertFalse(fresh.failures)

    def test_seven_audio_sources_prepare_and_record_one_shared_take_without_helpers(self):
        b=self.make(initial=127,groups=1,source_count=7,lanes=8)
        self.ticks(200)
        self.assertEqual(b.audio_inputs.available,127)
        self.assertEqual(b.audio_inputs.desired[0],127)
        reserve=b.reserves[0]
        self.assertEqual(len(reserve.members),7)
        self.assertEqual([m.source.track for m in reserve.members],self.sources)
        self.assertEqual(b.audio_inputs.routing_fact(0),127|(127<<7))
        self.assertFalse(any(c[0]=='create_track' for c in self.song.calls))
        self.assertFalse(any(track.name.startswith('[Input ') for track in self.song._tracks))
        take=self.start()
        self.assertEqual(len(self.members(take)),7)
        self.assertEqual({m.anchor for m in self.members(take)},{take.anchor})
        self.assertEqual([m.clip.name for m in self.members(take)],
                         ['T-Bone','SM-58']+['Source %d'%i for i in range(3,8)])
        self.assertTrue(all(m.track.input_routing_type.attached_object is source
                            and m.track.input_routing_channel is self.post
                            for m,source in zip(self.members(take),self.sources)))
        self.song.advance(take.anchor+2.)
        self.assertEqual(b.dispatch(65,take.token),0)
        self.song.advance(take.finish_target);self.ticks(2)
        self.assertEqual(take.state,'completed')
        self.assertTrue(all(m.clip.is_playing and not m.clip.is_recording and not m.track.arm
                            for m in self.members(take)))

    def test_reordering_all_selected_sources_rebuilds_ready_bundle_by_track_identity(self):
        b=self.make(initial=3,groups=1);self.ticks(90)
        old=b.reserves[0]
        for source in self.sources:self.song._tracks.remove(source)
        ordered=list(reversed(self.sources));self.song._tracks.extend(ordered)
        b.state.topology_dirty=True;self.ticks(35)
        self.assertEqual(b.audio_inputs.desired[0],3,'the bit mask alone cannot detect this reorder')
        self.assertIsNot(b.reserves[0],old)
        self.assertFalse(b.audio_inputs.validate(old,0,old.row),'old source-index matches must not pass')
        self.assertEqual([m.source.track for m in b.reserves[0].members],ordered)
        take=self.start()
        self.assertEqual([m.source.track for m in self.members(take)],ordered)
        for member in self.members(take):
            self.assertIs(member.track.input_routing_type.attached_object,member.source.track)
            self.assertEqual(member.clip.name,member.source.name)

    def test_catalog_reorder_keeps_admitted_take_sources_and_rebuilds_only_following(self):
        b=self.make(initial=3,groups=1);self.ticks(90);take=self.start();self.ticks(30)
        captured=tuple((m.source.track,m.track,m.clip,m.anchor) for m in self.members(take))
        for source in self.sources:self.song._tracks.remove(source)
        ordered=list(reversed(self.sources));self.song._tracks.extend(ordered)
        b.state.topology_dirty=True;self.ticks(40)
        self.assertEqual(tuple((m.source.track,m.track,m.clip,m.anchor) for m in self.members(take)),captured)
        self.assertTrue(all(m.clip.is_recording for m in self.members(take)))
        self.assertEqual([m.source.track for m in b.reserves[0].members],ordered)
        self.song.advance(take.anchor+2.)
        self.assertEqual(b.dispatch(67,1|(2<<7)),0)
        following=take.following
        self.assertEqual([m.source.track for m in self.members(following)],ordered)
        for member in self.members(following):
            self.assertIs(member.track.input_routing_type.attached_object,member.source.track)

    def test_same_ready_patch_finalizes_intent_before_later_source_failure(self):
        b=self.make(initial=1,groups=2);self.ticks(80)
        self.assertEqual(b.dispatch(49,1),1);self.ticks(50)
        self.assertEqual(b.audio_inputs.monitor_owner,1)
        self.assertEqual(b.dispatch(48,1),1);self.ticks(12)
        a=b.audio_inputs
        self.assertEqual(a.monitor_owner,0)
        self.assertNotIn(0,a.busy)
        self.assertNotIn(0,a._intents,'a no-op ready reserve still completes the explicit patch action')
        self.assertIs(a.last_ready[0],b.reserves[0])
        self.assertEqual(a.confirmed[0],1)
        self.sources[0].is_frozen=True;a.observe_sources(force=True);self.ticks(15)
        self.assertEqual(a.monitor_owner,0,'a completed patch must not roll monitoring back later')

    def test_removed_busy_group_does_not_block_other_group_metadata(self):
        b=self.make(initial=1,groups=2);self.ticks(80)
        self.assertEqual(b.dispatch(49,3),3)
        removed=b.state.groups[1].group
        def belongs(track):
            for unused in range(8):
                if track is removed:return True
                track=getattr(track,'group_track',None)
                if track is None:return False
            return False
        self.song._tracks=[track for track in self.song._tracks if not belongs(track)]
        b.state.topology_dirty=True;self.ticks(10)
        self.assertEqual(set(b.state.groups),{0})
        self.assertIsNone(b.audio_inputs.monitor_owner,'the removed monitor target must not be guessed')
        self.assertEqual(b.dispatch(48,2),2);self.ticks(80)
        a=b.audio_inputs
        for mapping in (a.desired,a.revisions,a.confirmed,a.last_ready,a._intents,
                        a.jobs,a.prepared_masks,b.reserves,b.failures):
            self.assertNotIn(1,mapping)
        self.assertNotIn(1,a.busy);self.assertNotIn(1,a.failed)
        self.assertEqual(a.state_store.pending,0)
        saved=json.loads(b.state.groups[0].group.get_data(PATCH_KEY,''))
        self.assertEqual(saved['sources'],[a.state_store.source_ids[1]])
        self.assertEqual(json.loads(self.input_root.get_data(OWNER_KEY,''))['group'],0)
        self.assertEqual(a.desired[0],2)

    def test_catalog_order_and_renamed_sources_restore_by_saved_ids(self):
        b=self.make(initial=2,groups=1,source_count=7,lanes=8);self.ticks(100)
        chosen=self.sources[1];old_ids=b.audio_inputs.state_store.source_ids
        chosen.name='T-Bone'  # Same name as another actual source, and existing music lanes.
        self.assertEqual(b.dispatch(57,0),0);self.ticks(10)
        b.close()
        for source in self.sources:self.song._tracks.remove(source)
        ordered=list(reversed(self.sources));self.song._tracks.extend(ordered)
        self.backend=Backend(self.song,Q,now=lambda:self.clock);self.addCleanup(self.backend.close)
        self.ticks(150);a=self.backend.audio_inputs
        self.assertEqual(a.catalog_tracks,tuple(ordered))
        self.assertEqual(a.state_store.source_ids,tuple(reversed(old_ids)))
        new_index=ordered.index(chosen)
        self.assertEqual(a.desired[0],1<<new_index)
        self.assertEqual(a.monitor_owner,0)
        self.assertIs(self.backend.reserves[0].members[0].source.track,chosen)
        self.assertIs(self.backend.reserves[0].track.input_routing_type.attached_object,chosen)

    def test_legacy_helper_like_tracks_do_not_infer_a_fresh_group_source(self):
        b=self.make(initial=0,groups=1);parent=b.state.groups[0].group
        anchor=b.state.anchors[0]
        for index,source in enumerate(self.sources):
            decoy=Track(self.song,'[Input %d]'%(index+1),anchor)
            decoy.mute=False;decoy.current_monitoring_state=0
            decoy.input_routing_type=self.routes[index];decoy.input_routing_channel=self.post
            self.song._tracks.append(decoy)
        b.state.topology_dirty=True;self.ticks(80)
        self.assertEqual(b.audio_inputs.available,3)
        self.assertEqual(b.audio_inputs.desired[0],0)
        self.assertNotIn(0,b.reserves)
        self.assertEqual(b.dispatch(64,1),REJECT)
        self.assertFalse(any(t.group_track is anchor for t in b.state.groups[0].tracks))

    def test_source_effects_stay_once_on_originals_and_ready_lanes_tap_post_fx(self):
        b=self.make(initial=3,groups=1)
        effects=[object(),object()]
        for source,effect in zip(self.sources,effects):source.devices=[effect]
        self.ticks(80)
        self.assertEqual([source.devices for source in self.sources],[[effects[0]],[effects[1]]])
        for member,source in zip(b.reserves[0].members,self.sources):
            self.assertIs(member.track.input_routing_type.attached_object,source)
            self.assertEqual(member.track.input_routing_channel.display_name,'Post FX')
            self.assertEqual(member.track.devices,[])
        self.assertEqual(sum(len(t.devices) for t in self.song._tracks),2)

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

    def test_partial_set_seeds_absent_group_patch_contexts_explicitly(self):
        b=self.make(groups=2)
        b.reseed()
        facts={(kind,a):value for kind,a,value in b.drain_facts(512)}
        for group in range(2,8):
            self.assertEqual(facts[1,48+group],0)
            self.assertEqual(facts[6,8+group],0)

    def test_deleted_native_destinations_rebuild_without_replaying_or_touching_other_groups(self):
        class ArgumentError(TypeError):pass
        class DeletedTrack(Track):
            def __getattribute__(self,name):
                if not name.startswith('_') and object.__getattribute__(self,'_deleted'):
                    raise ArgumentError('None.None(Track) did not match TTrackPyHandle')
                return super().__getattribute__(name)
            def __eq__(self,other):
                if self._deleted:raise ArgumentError('None.None(Track) invalid handle')
                return self is other
        for phase in ('ready','preparing'):
            with self.subTest(phase=phase):
                b=self.make(groups=2);self.ticks(80)
                if phase=='preparing':
                    b.audio_inputs.set_patch(0,3)
                    for unused in range(25):
                        self.ticks(1)
                        job=b.audio_inputs.jobs.get(0)
                        if job and (job['track'] is not None or job['members']):break
                    self.assertIsNotNone(job)
                    doomed=job['track'] or job['members'][0].track
                else:doomed=b.reserves[0].track
                # A Native listener remains retained after the Track is deleted.
                b.state._names.append((doomed,lambda:None))
                self.song._tracks.remove(doomed)
                doomed._deleted=False;doomed.__class__=DeletedTrack;doomed._deleted=True
                b.state.topology_dirty=True
                self.song.calls.clear();self.ticks(100)
                reserve=b.reserves[0]
                self.assertTrue(all(member.track is not doomed for member in reserve.members))
                self.assertTrue(all(track is not doomed for track,group in b.monitoring._owned))
                self.assertTrue(all(track is not doomed for track,callback in b.state._names))
                self.assertTrue(all(track is not doomed for track in b.state.groups[0].tracks))
                self.assertFalse(b.state.topology_dirty)
                self.assertNotIn(0,b.failures)
                self.assertFalse(any(call[0] in ('create_track','fire') for call in self.song.calls))
                self.assertEqual(b.dispatch(1,2),2,'unaffected group mute remains usable')
                b.close()

if __name__=='__main__':unittest.main()
