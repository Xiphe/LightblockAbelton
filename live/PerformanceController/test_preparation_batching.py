"""Confirmed routing progresses together; unsettled Native writes never replay."""
from pathlib import Path
import sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import test_mixed_inputs as mixed_fixture
from PerformanceController import audio_inputs
from PerformanceController.audio_inputs import RoutingBudget
from PerformanceController.fake_native import Track


class PreparationBatchingChecks(unittest.TestCase):
    def fixture(self):
        fixture=mixed_fixture.MixedInputChecks();self.addCleanup(fixture.doCleanups)
        fixture.make(lanes=4)
        return fixture

    def test_audio_and_midi_confirmed_routes_finish_in_one_post_create_callback(self):
        for kind,mask in (('audio',1),('midi',2)):
            with self.subTest(kind=kind):
                fixture=self.fixture();b=fixture.b
                parent=b.state.groups[0].group
                lanes=[t for t in fixture.song._tracks if t.group_track is parent and not t.is_foldable]
                keep=[next(t for t in lanes if t.has_audio_input),next(t for t in lanes if t.has_midi_input)]
                fixture.song._tracks[:]=[t for t in fixture.song._tracks if t not in lanes or t in keep]
                b.state.topology_dirty=True;fixture.ticks(50)
                self.assertEqual(b.dispatch(48,mask),mask);fixture.ticks(50)
                fixture.fixture.start();fixture.ticks(1)
                track=b.audio_inputs.jobs[0]['track'];self.assertIsNotNone(track)
                writes=[];original=Track.__setattr__
                def record(target,field,value):
                    if target is track and field in ('input_routing_type','input_routing_channel',
                                                      'output_routing_type','output_routing_channel'):
                        writes.append(field)
                    original(target,field,value)
                with patch.object(Track,'__setattr__',record),patch.object(audio_inputs.time,'monotonic',return_value=0.):fixture.ticks(1)
                self.assertIn(0,b.reserves)
                self.assertEqual(len(writes),2 if kind=='audio' else 4)
                self.assertIsNone(b.allocation_ticket)

    def test_unconfirmed_assignment_is_observed_without_reissuing(self):
        fixture=self.fixture();inputs=fixture.b.audio_inputs;source=inputs.sources[1]
        class DelayedTrack(Track):
            def __setattr__(self,field,value):
                if field=='input_routing_type' and getattr(self,'delay',False):
                    self.issued.append(value);self.pending_value=value;return
                super().__setattr__(field,value)
        track=Track(fixture.song,'spare',audio=False);track.__class__=DelayedTrack
        track.delay=True;track.issued=[]
        job={};wanted=fixture.external
        for unused in range(3):
            self.assertFalse(inputs._routing_write(0,job,source,track,'input_routing_type',wanted,RoutingBudget()))
        self.assertEqual(track.issued,[wanted])
        object.__setattr__(track,'input_routing_type',track.pending_value)
        self.assertTrue(inputs._routing_write(0,job,source,track,'input_routing_type',wanted,RoutingBudget()))
        self.assertEqual(track.issued,[wanted]);self.assertNotIn('pending_route',job)

    def test_routing_stops_after_four_writes_even_when_all_confirm(self):
        fixture=self.fixture();inputs=fixture.b.audio_inputs;source=inputs.sources[1]
        track=Track(fixture.song,'spare',audio=False);job={}
        with patch.object(audio_inputs.time,'monotonic',return_value=0.):
            budget=RoutingBudget()
            for prop,value in (('current_monitoring_state',2),('input_routing_type','A'),
                               ('input_routing_channel','B'),('output_routing_type','C')):
                self.assertTrue(inputs._routing_write(0,job,source,track,prop,value,budget))
            self.assertFalse(inputs._routing_write(0,job,source,track,'output_routing_channel','D',budget))
        self.assertEqual(budget.writes,4);self.assertEqual(track.output_routing_channel,'')

    def test_slow_native_write_finishes_readback_but_does_not_start_another_write(self):
        fixture=self.fixture();inputs=fixture.b.audio_inputs;source=inputs.sources[1]
        clock=[0.]
        class SlowTrack(Track):
            def __setattr__(self,field,value):
                super().__setattr__(field,value)
                if field=='input_routing_type' and getattr(self,'slow',False):clock[0]+=.005
        track=Track(fixture.song,'spare',audio=False);track.__class__=SlowTrack;track.slow=True;job={}
        with patch.object(audio_inputs.time,'monotonic',side_effect=lambda:clock[0]):
            budget=RoutingBudget()
            self.assertTrue(inputs._routing_write(0,job,source,track,'input_routing_type','A',budget))
            self.assertFalse(inputs._routing_write(0,job,source,track,'input_routing_channel','B',budget))
        self.assertEqual(track.input_routing_channel,'Factory Channel');self.assertEqual(budget.writes,1)

    def test_new_profile_revision_does_not_replay_the_previous_pending_assignment(self):
        fixture=self.fixture();b=fixture.b;self.assertEqual(b.dispatch(48,2),2);fixture.ticks(50)
        reserve=b.reserves[0];track=reserve.track
        class DelayedType(Track):
            def __setattr__(self,field,value):
                if field=='input_routing_type' and getattr(self,'delay',False):
                    self.requests.append(value);return
                super().__setattr__(field,value)
        track.__class__=DelayedType;track.requests=[];track.delay=True
        object.__setattr__(track,'input_routing_type','Factory Input')
        b.reserves.pop(0);b.audio_inputs.busy.add(0);fixture.ticks(2)
        self.assertEqual(track.requests,[fixture.external])
        old_job=b.audio_inputs.jobs[0]
        new=type(fixture.external)(display_name='New Keyboard',attached_object=None)
        fixture.instrument.input_routing_type=new
        for t in fixture.song._tracks:
            if t.has_midi_input and t is not fixture.instrument:
                t.available_input_routing_types=list(t.available_input_routing_types)+[new]
        b.audio_inputs.observe_sources(force=True);fixture.ticks(2)
        self.assertIsNot(b.audio_inputs.jobs[0],old_job)
        self.assertEqual(track.requests,[fixture.external,new])
        object.__setattr__(track,'input_routing_type',new);fixture.ticks(10)
        self.assertIn(0,b.reserves);self.assertNotIn(0,b.failures)
        self.assertEqual(track.requests,[fixture.external,new])

    def test_last_slow_routing_write_still_gets_critical_marker_before_ready(self):
        fixture=self.fixture();b=fixture.b;clock=[0.]
        track=b.reserves[0].track
        class SlowChannel(Track):
            def __setattr__(self,field,value):
                super().__setattr__(field,value)
                if field=='input_routing_channel' and getattr(self,'slow',False):clock[0]+=.005
        track.__class__=SlowChannel;track.slow=True
        object.__setattr__(track,'input_routing_type','Factory Input')
        object.__setattr__(track,'input_routing_channel','Factory Channel')
        b.reserves.pop(0);b.audio_inputs.busy.add(0)
        track._data.pop(audio_inputs.PREPARED_KEY,None)
        with patch.object(audio_inputs.time,'monotonic',side_effect=lambda:clock[0]):fixture.ticks(1)
        self.assertIn(0,b.reserves)
        self.assertTrue(track.get_data(audio_inputs.PREPARED_KEY,''))
        self.assertGreaterEqual(clock[0],.005)

    def test_slow_discovery_still_permits_first_write_then_yields(self):
        fixture=self.fixture();inputs=fixture.b.audio_inputs;source=inputs.sources[1]
        track=Track(fixture.song,'spare',audio=False);clock=[0.];job={}
        with patch.object(audio_inputs.time,'monotonic',side_effect=lambda:clock[0]):
            budget=RoutingBudget();clock[0]=.005
            self.assertTrue(inputs._routing_write(0,job,source,track,'input_routing_type','A',budget))
            self.assertFalse(inputs._routing_write(0,job,source,track,'input_routing_channel','B',budget))
        self.assertEqual(budget.writes,1);self.assertEqual(track.input_routing_type,'A')
        self.assertEqual(track.input_routing_channel,'Factory Channel')

    def test_unconfirmed_route_times_out_without_replaying_the_setter(self):
        fixture=self.fixture();b=fixture.b;self.assertEqual(b.dispatch(48,2),2);fixture.ticks(50)
        track=b.reserves[0].track
        class NeverConfirmed(Track):
            def __setattr__(self,field,value):
                if field=='input_routing_type' and getattr(self,'delay',False):
                    self.requests.append(value);return
                super().__setattr__(field,value)
        track.__class__=NeverConfirmed;track.requests=[];track.delay=True
        object.__setattr__(track,'input_routing_type','Factory Input')
        b.reserves.pop(0);b.audio_inputs.busy.add(0)
        fixture.ticks(100)
        self.assertEqual(track.requests,[fixture.external])
        self.assertIn('timeout',b.failures[0]);self.assertNotIn(0,b.reserves)
        fixture.ticks(40)
        self.assertEqual(track.requests,[fixture.external])

    def test_marker_callback_cannot_publish_old_revision_or_confirm_new_intent(self):
        fixture=self.fixture();b=fixture.b;track=b.reserves[0].track
        object.__setattr__(track,'input_routing_type','Factory Input')
        track._data.pop(audio_inputs.PREPARED_KEY,None)
        b.reserves.pop(0);b.audio_inputs.busy.add(0)
        original=Track.set_data;changed=[]
        def reentrant(target,key,value):
            result=original(target,key,value)
            if target is track and key==audio_inputs.PREPARED_KEY and not changed:
                changed.append(True)
                self.assertEqual(b.dispatch(48,2),2)
            return result
        with patch.object(Track,'set_data',reentrant):fixture.ticks(1)
        self.assertEqual(changed,[True]);self.assertEqual(b.audio_inputs.desired[0],2)
        self.assertEqual(b.audio_inputs.confirmed[0],1)
        self.assertNotIn(0,b.reserves)
        self.assertIn(0,b.audio_inputs.busy)
        self.assertIn(0,b.audio_inputs._intents)
        fixture.ticks(60)
        self.assertEqual(b.audio_inputs.confirmed[0],2)
        self.assertEqual(b.reserves[0].members[0].source.kind,'midi')

if __name__=='__main__':unittest.main()
