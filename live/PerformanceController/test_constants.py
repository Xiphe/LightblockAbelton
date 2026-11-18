"""Constants use retained mixer targets without entering the recording model."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PerformanceController.backend import Backend,REJECT
from PerformanceController.fake_native import PerformanceSong,Track,Clip

Q=dict(q_bar=4,q_half=5,q_quarter=7,q_eight=9,q_sixtenth=11)


def add_constants(song,name='Constants',count=4):
    root=Track(song,name,foldable=True)
    song._tracks.append(root)
    children=[]
    for index,label in enumerate(('Drums','Voiceover','Guest 1','Guest 2','Extra')[:count]):
        child=Track(song,label,root,foldable=index==0)
        song._tracks.append(child);children.append(child)
        if index==0:
            song._tracks.append(Track(song,'Nested drum instrument',child))
    return root,children


class ConstantsChecks(unittest.TestCase):
    def make(self,groups=8,name='Constants',count=4,assignments=None):
        self.clock=0.;self.song=PerformanceSong(groups)
        self.root,self.children=add_constants(self.song,name,count)
        self.backend=Backend(self.song,Q,assignments,now=lambda:self.clock)
        self.addCleanup(self.backend.close)
        return self.backend

    def ticks(self,count=1):
        for unused in range(count):
            self.clock+=.101;self.backend.tick()

    def test_first_four_direct_children_follow_live_order_without_recording_groups(self):
        b=self.make(count=5)
        self.assertEqual(b.state.constants,dict(zip(range(8,12),self.children)))
        self.assertEqual(set(b.state.groups),set(range(8)))
        self.assertEqual(set(b.state.anchors),set(range(8)))
        first,last=self.children[0],self.children[3]
        i,j=self.song._tracks.index(first),self.song._tracks.index(last)
        self.song._tracks[i],self.song._tracks[j]=self.song._tracks[j],self.song._tracks[i]
        b.state.discover()
        self.assertIs(b.state.constants[8],last)
        self.assertIs(b.state.constants[11],first)
        self.assertNotIn(self.children[4],b.state.constants.values())

    def test_bracketed_root_supported_but_ambiguous_roots_only_disable_constants(self):
        for name in ('Constants','[Constants]','[Constants] Live inputs'):
            with self.subTest(name=name):
                b=self.make(name=name)
                self.assertEqual(len(b.state.constants),4)
                add_constants(self.song,'Constants')
                b.state.discover()
                self.assertFalse(b.state.constants)
                self.assertEqual(len(b.state.groups),8)
                self.assertEqual(b.state.constants_issues,{'root':'ambiguous Constants group'})
                self.assertEqual(b.dispatch(128,80),REJECT)
                self.assertEqual(b.dispatch(0,80),80)

    def test_mixer_only_constants_never_read_clip_slots_or_prepare_tracks(self):
        b=self.make()
        class MixerOnly(Track):
            def __getattribute__(self,name):
                if name=='clip_slots':raise AssertionError('Constants clip scan')
                return super().__getattribute__(name)
        for target in self.children:target.__class__=MixerOnly
        with patch.object(b,'_maintenance',wraps=b._maintenance) as maintenance, \
                patch.object(b.state,'scan_occupancy',wraps=b.state.scan_occupancy) as occupancy, \
                patch.object(b.state,'observe_mixer',wraps=b.state.observe_mixer) as observe:
            self.ticks(8)
        self.assertEqual([call.args[0] for call in maintenance.call_args_list],list(range(8)))
        self.assertEqual([call.args[0] for call in occupancy.call_args_list],list(range(8)))
        self.assertEqual([call.args[0] for call in observe.call_args_list if call.args[0]>=8],[8,9,10,11]*2)
        self.assertFalse(any(call[0].startswith('create') for call in self.song.calls))
        self.assertEqual(set(b.reserves),set(range(8)))

    def test_all_24_parameters_use_bank_two_and_retained_targets(self):
        b=self.make();self.song.track_reads=0;self.song.calls.clear()
        for offset in range(24):
            key=128+offset;group,control=divmod(offset,6)
            wanted=64 if control==1 else offset*5
            self.assertEqual(b.dispatch(key,wanted),wanted)
            target=b.state.parameters[8+group][control]
            expected=0. if control==1 else wanted/127.
            self.assertAlmostEqual(target.value,expected)
        self.assertEqual(self.song.track_reads,0)
        self.assertFalse(self.song.calls)
        self.assertEqual(b.dispatch(152,80),REJECT)

    def test_parameter_seed_precedes_constant_availability_and_reports_missing_control(self):
        b=self.make(assignments={8:[{'kind':'send','index':i} for i in (0,1,2,99)]})
        b.reseed();facts=b.drain_facts(300)
        available=next(i for i,(kind,a,unused) in enumerate(facts) if kind==3 and a==8)
        fields=[fact for fact in facts[:available] if fact[0]==1 and 48<=fact[1]<54]
        self.assertEqual([a for unused,a,value in fields],list(range(48,54)))
        self.assertEqual(fields[-1],(1,53,128))
        self.assertEqual(b.dispatch(133,80),REJECT)
        self.assertEqual(b.dispatch(128,80),80)

    def test_multi_mute_spans_loops_and_constants_but_not_root_or_descendants(self):
        b=self.make();self.song.track_reads=0
        self.assertEqual(b.dispatch(56,(1<<0)|(1<<8)|(1<<11)),(1<<0)|(1<<8)|(1<<11))
        self.assertTrue(b.state.groups[0].group.mute)
        self.assertTrue(self.children[0].mute);self.assertTrue(self.children[3].mute)
        self.assertFalse(self.root.mute)
        nested=next(t for t in self.song._tracks if t.group_track is self.children[0])
        self.assertFalse(nested.mute)
        self.assertEqual(self.song.track_reads,0)
        self.assertEqual(b.dispatch(56,4095),4095^((1<<0)|(1<<8)|(1<<11)))
        self.assertEqual(b.dispatch(56,4096),REJECT)

    def test_missing_target_rejects_mute_before_any_other_target_is_changed(self):
        b=self.make(count=3)
        self.assertEqual(b.dispatch(56,1|(1<<11)),REJECT)
        self.assertFalse(b.state.groups[0].group.mute)

    def test_r_group_child_keeps_one_role_and_leaves_constant_slot_blank(self):
        b=self.make(groups=1)
        target=self.children[0];target.name='[R2] Shared target'
        b.state.discover();b.reseed()
        self.assertIs(b.state.groups[1].group,target)
        self.assertNotIn(8,b.state.constants)
        self.assertIs(b.state.constants[9],self.children[1])
        self.assertEqual(b.state.constants_issues,{8:'Constant target also marked as remote group'})
        self.assertEqual(b.dispatch(56,2),2)
        self.assertTrue(target.mute)
        self.assertEqual(b.dispatch(56,1<<8),REJECT)
        self.assertTrue(target.mute)
        self.assertEqual(b.dispatch(128,80),REJECT)
        self.assertEqual(b.dispatch(134,80),80)
        facts=b.drain_facts(300)
        self.assertIn((3,8,0),facts)

    def test_constant_macro_binding_stays_retained_and_invalidates_only_changed_control(self):
        b=self.make()
        parameter=NS(name='Chance',value=.3,min=0.,max=1.,is_enabled=True,is_quantized=False)
        class Rack:
            name='Constant rack'
            def __init__(self):self.reads=0;self.values=[parameter]
            @property
            def parameters(self):self.reads+=1;return self.values
        rack=Rack();self.children[0].devices=[rack]
        b.state.assignments={8:[{'kind':'device','device_path':[0],'parameter_name':'Chance'},
                               {'kind':'send','index':0},None,None]}
        b.state.discover();rack.reads=0
        self.assertEqual(b.dispatch(130,99),99)
        self.assertEqual(rack.reads,0)
        rack.values=[NS(name='Chance',value=.1,min=0.,max=1.,is_enabled=True,is_quantized=False)]
        b.state.observe_mixer(8)
        self.assertEqual(b.dispatch(130,80),REJECT)
        self.assertEqual(b.dispatch(131,80),80)
        self.assertEqual(b.dispatch(128,80),80)

    def test_removed_or_renamed_root_clears_constant_facts_without_changing_loop_model(self):
        b=self.make();b.reseed();b.drain_facts(300)
        callbacks=[]
        self.root.add_name_listener=callbacks.append
        self.root.remove_name_listener=callbacks.remove
        b.state.discover()
        self.root.name='Not a controller root'
        for callback in tuple(callbacks):callback()
        self.assertTrue(b.state.topology_dirty)
        self.ticks(4)
        self.assertFalse(b.state.constants)
        facts=b.drain_facts(300)
        self.assertEqual({a:v for kind,a,v in facts if kind==3 and 8<=a<12},dict.fromkeys(range(8,12),0))
        self.assertEqual(len(b.state.groups),8)
        self.assertEqual(b.dispatch(128,80),REJECT)

    def test_flat_parameter_facts_survive_patch_rollback_without_context_collision(self):
        import test_audio_inputs as fixtures
        fixture=fixtures.AudioInputChecks();self.addCleanup(fixture.doCleanups)
        b=fixture.make(initial=1,groups=1)
        add_constants(fixture.song);b.state.discover();fixture.ticks(40)
        b.reseed()
        before={(kind,a):value for kind,a,value in b.drain_facts(512)}
        self.assertEqual(b.dispatch(48,3),3)
        revision=b.audio_inputs.revisions[0]
        b._group_facts(8);b._group_facts(9);b._global_facts()
        before.update({(kind,a):value for kind,a,value in b.drain_facts(512)})
        self.assertEqual(before[1,48],95)
        self.assertEqual(before[1,57],13)
        self.assertEqual(before[1,80],3)
        self.assertEqual(before[1,88],0)
        b.audio_inputs.fail_preparation(0,'test routing failure',revision)
        b._global_facts()
        after={(kind,a):value for kind,a,value in b.drain_facts(512)}
        self.assertEqual(after[1,80],1)
        self.assertNotIn((1,48),after)
        self.assertNotIn((1,57),after)
        self.assertFalse(any(kind==1 and 128<=a<152 for kind,a,value in b._published.values()))


if __name__=='__main__':unittest.main()
