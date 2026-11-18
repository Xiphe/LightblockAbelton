"""Mixed source preparation/ownership over actual Backend callbacks, not hardware proof."""
from pathlib import Path
import sys,json,unittest
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).parent))
import test_audio_inputs as fixtures
from PerformanceController.backend import REJECT
from PerformanceController.input_contract import RECEIVER_KEY,MIDI_OWNER_KEY
from PerformanceController.fake_native import Track


class MixedInputChecks(unittest.TestCase):
    def make(self,lanes=4):
        self.fixture=fixtures.AudioInputChecks();self.addCleanup(self.fixture.doCleanups)
        b=self.fixture.make(initial=1,groups=2,lanes=lanes)
        self.b,self.song=b,self.fixture.song
        self.instrument=self.fixture.sources[1]
        self.instrument.has_audio_input=False;self.instrument.has_midi_input=True
        self.instrument.has_audio_output=True;self.instrument.has_midi_output=False
        self.device=NS(type='instrument',name='Synth',class_display_name='Synth',can_have_chains=False)
        self.instrument.devices=[self.device]
        self.external=NS(display_name='My Keys',attached_object=None)
        self.channel=NS(display_name='Ch. 2',attached_object=None)
        self.instrument.input_routing_type=self.external;self.instrument.input_routing_channel=self.channel
        self.instrument.current_monitoring_state=2;self.instrument.output_routing_type='Sends Only'
        self.receivers={}
        for g,item in b.state.groups.items():
            anchor=b.state.anchors[g]
            helper=next(t for t in self.song._tracks if t.group_track is anchor)
            helper.name='MIDI receiver';helper.input_routing_type='No Input';helper.current_monitoring_state=0
            helper.output_routing_type=anchor.name;helper.output_routing_channel=''
            helper.set_data(RECEIVER_KEY,json.dumps(dict(schema=1,group=g)))
            self.receivers[g]=helper
            musical=[t for t in self.song._tracks if t.group_track is item.group and not t.is_foldable]
            for t in musical[2:]:t.has_audio_input=False;t.has_midi_input=True;t.has_audio_output=False;t.has_midi_output=True
        self.instrument.available_output_routing_types=['Sends Only']+[NS(display_name='Receiver%d'%g,attached_object=t) for g,t in self.receivers.items()]
        self.endpoint=NS(display_name='1-Synth',attached_object=None)
        self.destination=NS(display_name=self.instrument.name,attached_object=self.instrument)
        def routes(track):
            track.available_input_routing_types=[self.external,'No Input']
            track.available_input_routing_channels=[self.channel]
            track.available_output_routing_types=[self.destination,'No Output']
            track.available_output_routing_channels=[NS(display_name='Track In'),self.endpoint]
        for t in self.song._tracks:
            if t.has_midi_input and t is not self.instrument:routes(t)
        create=self.song.create_midi_track
        def create_midi(index):create(index);routes(self.song._tracks[index])
        self.song.create_midi_track=create_midi
        b.state.topology_dirty=True;self.ticks(100)
        return b

    def ticks(self,n=40):self.fixture.ticks(n)

    def test_mixed_bundle_reuses_lanes_and_preserves_original_instrument(self):
        b=self.make();self.assertEqual(b.dispatch(48,3),3);self.ticks(80)
        reserve=b.reserves[0];self.assertEqual([m.source.kind for m in reserve.members],['audio','midi'])
        midi=reserve.members[1]
        self.assertIs(midi.track.input_routing_type,self.external)
        self.assertIs(midi.track.input_routing_channel,self.channel)
        self.assertIs(midi.track.output_routing_type,self.destination)
        self.assertIs(midi.track.output_routing_channel,self.endpoint)
        self.assertEqual(midi.track.current_monitoring_state,2)
        self.assertIs(self.instrument.input_routing_type,self.external)
        self.assertIs(self.instrument.input_routing_channel,self.channel)
        self.assertEqual(self.instrument.devices,[self.device])
        self.assertIs(self.instrument.output_routing_type.attached_object,self.receivers[0])
        self.assertEqual(self.instrument.current_monitoring_state,0)
        take=self.fixture.start()
        self.assertEqual([m.clip.is_audio_clip for m in self.fixture.members(take)],[True,False])
        self.assertEqual(midi.track.current_monitoring_state,2)
        self.assertEqual(b.dispatch(65,1),0)
        self.song.advance(take.finish_target-.5);self.ticks(1)
        self.song.advance(take.finish_target);self.ticks(30)
        self.assertEqual(take.state,'completed')
        self.assertTrue(all(m.clip.is_playing for m in self.fixture.members(take)))

    def test_exclusive_owner_unpatch_retains_playback_then_clear_releases_actual_route(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.assertEqual(b.audio_inputs.midi.owner(1),0)
        self.assertEqual(b.dispatch(49,2),REJECT)
        self.assertEqual(b.dispatch(48,0),0);self.ticks(20)
        self.assertEqual(b.audio_inputs.midi.owner(1),0)
        self.assertEqual(self.instrument.current_monitoring_state,2)
        self.assertEqual(b.dispatch(104,0),0);self.ticks(40)
        self.assertEqual(self.instrument.output_routing_type,'Sends Only')
        self.assertEqual(self.instrument.current_monitoring_state,2)
        self.assertIsNone(b.audio_inputs.midi.owner(1))
        self.assertEqual(b.dispatch(49,2),2);self.ticks(60)
        self.assertEqual(b.audio_inputs.midi.owner(1),1)

    def test_clear_keeps_midi_selection_without_idle_reacquire_then_begin_reacquires(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.assertEqual(b.dispatch(104,0),0);self.ticks(40)
        self.assertEqual(b.audio_inputs.desired[0],2)
        self.assertIsNone(b.audio_inputs.monitor_owner)
        self.assertIsNone(b.audio_inputs.midi.owner(1))
        self.assertEqual(self.instrument.output_routing_type,'Sends Only')
        self.assertEqual(b.dispatch(64,1),0);self.ticks(60)
        self.assertEqual(b.audio_inputs.midi.owner(1),0)
        self.assertEqual(b.audio_inputs.monitor_owner,0)
        self.assertEqual(self.instrument.current_monitoring_state,0)
        self.assertIsNotNone(b.recording.take)

    def test_mixed_clear_retains_audio_monitor_without_idle_midi_reacquisition(self):
        b=self.make();self.assertEqual(b.dispatch(48,3),3);self.ticks(60)
        self.assertEqual(b.dispatch(104,0),0);self.ticks(60)
        self.assertEqual(b.audio_inputs.desired[0],3)
        self.assertEqual(b.audio_inputs.monitor_owner,0)
        self.assertIsNone(b.audio_inputs.midi.owner(1))
        self.assertEqual(self.instrument.output_routing_type,'Sends Only')
        self.assertEqual(self.instrument.current_monitoring_state,2)
        reserve=b.reserves[0]
        self.assertEqual(reserve.members[0].track.current_monitoring_state,1)
        self.assertTrue(reserve.members[0].track.arm)
        self.assertEqual(reserve.members[1].track.current_monitoring_state,2)
        self.assertTrue(b.audio_inputs.group_mask&1,
                        'a still-free remembered instrument permits an explicit Begin')
        self.assertFalse(b.audio_inputs.validate(reserve,0,reserve.row),
                         'recording admission still waits for the actual instrument lease')
        self.assertEqual(b.dispatch(64,1),0)
        self.assertIs(b.reserves[0],reserve,'lease waiting must not evict the active Audio monitor')
        self.assertIsNone(b.recording.take)
        for unused in range(30):
            self.ticks(1)
            self.assertEqual(reserve.members[0].track.current_monitoring_state,1)
            if b.recording.take is not None:break
        self.assertEqual(b.audio_inputs.midi.owner(1),0)
        self.assertEqual(self.instrument.current_monitoring_state,0)
        self.assertIsNotNone(b.recording.take)

    def test_clear_then_immediate_other_group_patch_releases_old_route_before_assignment(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.assertEqual(b.dispatch(104,0),0)
        self.assertEqual(b.dispatch(49,2),2)
        self.ticks(80)
        self.assertEqual(b.audio_inputs.desired[0],2)
        self.assertEqual(b.audio_inputs.midi.owner(1),1)
        self.assertIs(self.instrument.output_routing_type.attached_object,self.receivers[1])
        self.assertFalse(b.audio_inputs.group_mask&1)
        self.assertTrue(b.audio_inputs.group_mask&2)
        self.assertEqual(b.dispatch(64,1),REJECT)

    def test_unmarked_receiver_is_never_adopted(self):
        b=self.make();self.receivers[0].set_data(RECEIVER_KEY,'')
        b.state.topology_dirty=True;self.ticks(20)
        self.assertEqual(b.dispatch(48,2),2);self.ticks(20)
        self.assertTrue(all(m.source.kind=='audio' for m in b.reserves[0].members))
        self.assertTrue(any(e['event']=='input_preparation_failed' and 'receiver unavailable' in e.get('reason','')
                            for e in b.events))
        self.assertNotIn(0,b.audio_inputs.midi.receivers)
        self.assertIsNone(b.audio_inputs.midi.owner(1))
        self.assertEqual(self.instrument.output_routing_type,'Sends Only')

    def test_unsupported_midi_effect_chain_is_unavailable_without_bypass(self):
        b=self.make();self.instrument.devices.insert(0,NS(type=2,name='Arp'))
        b.state.topology_dirty=True;self.ticks(20)
        self.assertFalse(b.audio_inputs.available&2)
        self.assertEqual(b.dispatch(48,2),REJECT)
        self.assertEqual(len(self.instrument.devices),2)
        self.instrument.devices.pop(0)
        self.ticks(30)
        self.assertTrue(b.audio_inputs.available&2,'repair heals without a track-topology mutation')

    def test_monitor_transfer_silences_live_source_without_changing_clip_destination(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.assertEqual(b.dispatch(57,1),1);self.ticks(20)
        self.assertEqual(self.instrument.current_monitoring_state,2)
        self.assertIs(self.instrument.output_routing_type.attached_object,self.receivers[0])
        self.assertEqual(b.audio_inputs.midi.owner(1),0)

    def test_saved_ownership_reopens_from_actual_receiver_and_uuid(self):
        from PerformanceController.backend import Backend
        b=self.make();self.assertEqual(b.dispatch(48,3),3);self.ticks(60)
        marker=self.instrument.get_data(MIDI_OWNER_KEY,'')
        b.close()
        reopened=Backend(self.song,fixtures.Q,now=lambda:self.fixture.clock)
        self.addCleanup(reopened.close);self.fixture.backend=self.b=reopened
        self.ticks(60)
        self.assertEqual(reopened.audio_inputs.midi.owner(1),0)
        self.assertEqual(reopened.audio_inputs.desired[0],3)
        self.assertEqual(self.instrument.get_data(MIDI_OWNER_KEY,''),marker)
        self.assertEqual(len(reopened.reserves[0].members),2)
        midi=reopened.reserves[0].members[1]
        self.assertTrue(reopened.monitoring.owned(midi.track))
        self.assertEqual(midi.track.current_monitoring_state,2)

    def test_nonzero_instrument_send_is_diagnosed_and_never_wiped(self):
        b=self.make();send=self.instrument.mixer_device.sends[0];send.value=.35
        self.ticks(20)
        self.assertEqual(b.dispatch(48,2),REJECT)
        self.assertEqual(send.value,.35)
        self.assertIn('sends must be dry',b.audio_inputs.issues[1])

    def test_midi_overflow_uses_one_busy_ticket_until_prepared_bundle(self):
        b=self.make();self.assertEqual(b.dispatch(48,3),3);self.ticks(60)
        first=self.fixture.start();self.ticks(50)
        # First current and first prepared bundle consume both MIDI lanes.
        self.assertEqual(b.dispatch(67,first.take_id|(2<<7)),0)
        self.song.advance(first.finish_target-.5);self.ticks(1)
        self.song.advance(first.finish_target);self.ticks(70)
        self.assertEqual(b.recording.take.state,'recording')
        self.assertIn(0,b.reserves)
        self.assertTrue(any(call[0]=='create_midi_track' for call in self.song.calls))
        self.assertIsNone(b.allocation_ticket)
        self.assertEqual(len(b.reserves[0].members),2)

    def test_pending_unpatch_does_not_acquire_an_unused_instrument(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2)
        self.assertEqual(b.dispatch(48,0),0);self.ticks(40)
        self.assertIsNone(b.audio_inputs.midi.owner(1))
        self.assertEqual(self.instrument.output_routing_type,'Sends Only')

    def test_pending_instrument_request_follows_uuid_after_catalog_reorder(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2)
        self.song._tracks.remove(self.instrument)
        first=self.fixture.sources[0]
        self.song._tracks.insert(self.song._tracks.index(first),self.instrument)
        b.state.topology_dirty=True;self.ticks(60)
        self.assertIs(b.audio_inputs.catalog_tracks[0],self.instrument)
        self.assertEqual(b.audio_inputs.desired[0],1)
        self.assertEqual(b.audio_inputs.midi.owner(0),0)
        self.assertIs(self.instrument.output_routing_type.attached_object,self.receivers[0])

    def test_duplicate_named_physical_ports_do_not_validate_a_wrong_prepared_input(self):
        from dataclasses import replace
        from PerformanceController.midi_routing import midi_route_equal
        class Port:
            def __init__(self,name):self.display_name=name;self.attached_object=None
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        reserve=b.reserves[0];lane=reserve.track
        desired,wrong=Port('My Keys'),Port('My Keys')
        source=replace(b.audio_inputs.sources[1],input_type=desired)
        b.audio_inputs.sources[1]=source
        lane.input_routing_type=wrong;lane.available_input_routing_types=[desired,wrong]
        self.assertFalse(midi_route_equal(desired,wrong))
        self.assertFalse(b.audio_inputs.validate(reserve,0,reserve.row))

    def test_ambiguous_cross_track_external_port_fallback_fails_closed(self):
        class Port:
            def __init__(self):self.display_name='My Keys';self.attached_object=None
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        self.instrument.input_routing_type=Port()
        old_routes={}
        for track in self.song._tracks:
            if track.has_midi_input and track is not self.instrument:
                old_routes[id(track)]=track.input_routing_type
                track.available_input_routing_types=[Port(),Port()]
        self.ticks(60)
        self.assertNotIn(0,b.reserves)
        self.assertIn('ambiguous',b.failures[0])
        for track in self.song._tracks:
            if id(track) in old_routes:self.assertIs(track.input_routing_type,old_routes[id(track)])

    def test_prepared_midi_begin_does_not_enumerate_offered_choices(self):
        from PerformanceController.fake_native import Track
        class NoChoices(Track):
            def __getattribute__(self,name):
                if name.startswith('available_'):raise AssertionError('offered choices read on command path')
                return super().__getattribute__(name)
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        lane=b.reserves[0].track;lane.__class__=NoChoices
        self.assertEqual(b.dispatch(64,1),0)
        self.assertIs(b.recording.take.track,lane)

    def test_manual_source_recording_is_unavailable_and_never_silenced(self):
        from PerformanceController.fake_native import Clip
        b=self.make();source=self.instrument
        source.arm=True;source.current_monitoring_state=0
        source.clip_slots[0].clip=Clip(audio=False,recording=True)
        source.playing_slot_index=0
        self.ticks(30)
        self.assertFalse(b.audio_inputs.available&2)
        self.assertEqual(source.current_monitoring_state,0)
        self.assertTrue(source.arm)
        self.assertTrue(source.clip_slots[0].clip.is_recording)
        self.assertEqual(b.dispatch(48,2),REJECT)

    def test_changed_original_input_reprepares_future_bundle_without_retargeting_capture(self):
        b=self.make();self.assertEqual(b.dispatch(48,2),2);self.ticks(60)
        take=self.fixture.start();captured=take.source;old_input=take.track.input_routing_type
        new=NS(display_name='Different Keyboard',attached_object=None)
        self.instrument.input_routing_type=new
        for track in self.song._tracks:
            if track.has_midi_input and track is not self.instrument:
                track.available_input_routing_types=list(track.available_input_routing_types)+[new]
        self.ticks(100)
        self.assertIs(b.audio_inputs.sources[1].input_type,new)
        self.assertIs(b.reserves[0].track.input_routing_type,new)
        self.assertIs(take.source,captured)
        self.assertIs(take.track.input_routing_type,old_input)
        self.assertTrue(take.clip.is_recording)

    def test_clear_then_immediate_begin_reacquires_after_the_pending_release(self):
        b=self.make();self.assertEqual(b.dispatch(48,3),3);self.ticks(60)
        self.assertEqual(b.dispatch(104,0),0)
        self.assertEqual(b.dispatch(64,1),0)
        self.ticks(80)
        self.assertEqual(b.audio_inputs.midi.owner(1),0)
        self.assertIsNotNone(b.recording.take)
        self.assertIsNone(b.pending_begin)

    def test_twenty_five_source_banks_merge_without_truncation(self):
        fixture=fixtures.AudioInputChecks();self.addCleanup(fixture.doCleanups)
        b=fixture.make(initial=1,groups=1,source_count=25)
        fixture.ticks(80)
        self.assertEqual(len(b.audio_inputs.catalog_tracks),25)
        self.assertEqual(b.dispatch(116,1<<11),1<<11)
        self.assertEqual(b.audio_inputs.desired[0],1|(1<<24))
        self.assertEqual(b.dispatch(48,3),3)
        self.assertEqual(b.audio_inputs.desired[0],3|(1<<24))
        self.assertEqual(b.dispatch(116,0),0)
        self.assertEqual(b.audio_inputs.desired[0],3)
        self.assertEqual(b.dispatch(48,1<<13),REJECT)
        self.assertEqual(b.dispatch(116,1<<12),REJECT)
        b._global_facts();facts=b.drain_facts(500)
        self.assertIn((1,80,3),facts)
        self.assertTrue(any(kind==6 and a==64+24*8 for kind,a,value in facts))
        self.assertFalse(any(kind==1 and 116<=a<=123 for kind,a,value in facts))

if __name__=='__main__':unittest.main()
