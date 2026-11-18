"""Joined compiled patch/main clients, actual reconciler and Audio backend.

Only Native endpoints, time, LEDs and MIDI are fake. This verifies the matched
semantic seam and optimistic state; it does not measure hardware/audio timing.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'lightpad/tests'))
sys.path.insert(0,str(ROOT/'live'))
sys.path.insert(0,str(ROOT/'live/PerformanceController'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from littlefoot_harness import Harness
import test_audio_inputs as native_fixture
import test_mixed_inputs as mixed_fixture
import test_audio_performance_path as baseline


class InputPath:
    # A patch reseed can retain321 facts plus bounded command/ACK results.
    pump_limit=1024
    # Reuse transport-driving utilities without inheriting/re-running baseline
    # acceptance cases or replacing the real compiled/native protocol seam.
    exchange=baseline.PathTests.exchange
    close_pipe=baseline.PathTests.close_pipe
    touch=baseline.PathTests.touch
    state=baseline.PathTests.state
    facts=baseline.PathTests.facts
    pump=baseline.PathTests.pump
    until=baseline.PathTests.until
    begin=baseline.PathTests.begin
    button=baseline.PathTests.button

    @classmethod
    def setUpClass(cls):
        cls.replay=os.environ.get('PERFORMANCE_REPLAY')
        if not cls.replay or not Path(cls.replay).is_file():
            raise unittest.SkipTest('Set PERFORMANCE_REPLAY to the built performance_replay executable')
        temporary=tempfile.TemporaryDirectory(prefix='audio-input-path-')
        cls.addClassCleanup(temporary.cleanup)
        cls.programs={}
        for name in ('main','patch'):
            binary=Path(temporary.name)/(name+'.bin')
            subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),
                            'compile',str(ROOT/('lightpad/performance-'+name+'.littlefoot')),
                            '-o',str(binary)],check=True,capture_output=True)
            cls.programs[name]=binary.read_bytes()

    def setUp(self):
        self.vm=Harness(self.programs['patch']);self.vm.call('initialise/v');self.midi_cursor=0
        self.backend=self.make_backend();self.song=self.fixture.song
        # Some Native fixtures deliberately warm ownership before returning.
        self.vm.time_ms=max(self.vm.time_ms,int(self.fixture.clock*1000))
        self.pipe=subprocess.Popen([self.replay],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        self.addCleanup(self.close_pipe)
        self.obs=0;self.commands=[]
        self.exchange('reset 1 %s'%self.vm.time_ms)
        self.exchange('patch-names 1')
        self.backend.reseed()
        self.until(lambda:0 in self.backend.reserves)
        self.until(lambda:[191,108,37] in self.vm.midi)
        self.assertIn([191,108,37],self.vm.midi)
        self.assertEqual(self.patch_mask(),1)

    def make_backend(self):
        self.fixture=native_fixture.AudioInputChecks()
        self.addCleanup(self.fixture.doCleanups)
        return self.fixture.make(initial=1,groups=8,source_count=getattr(self,'source_count',2),lanes=getattr(self,'lane_count',4))

    def advance(self,ms):
        self.vm.time_ms+=ms;self.fixture.clock=self.vm.time_ms/1000.
        self.song.advance(1+(self.vm.time_ms-1000)/500.)
        self.backend.tick();self.facts();self.pump();self.vm.call('repaint/v');self.pump()

    def switch(self,name):
        now=self.vm.time_ms;self.vm=Harness(self.programs[name]);self.vm.time_ms=now
        self.vm.call('initialise/v');self.midi_cursor=0
        self.exchange('patch-names %d'%(name=='patch'))
        self.exchange('reseed %d'%now);self.pump();self.advance(100)

    def patch_mask(self,group=0):return int.from_bytes(self.vm.heap_bytes(32+group*4,4),'little')
    def patch_pending(self):return bool(self.vm.call('pending/bi',0)['return'])
    def add_sm58(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.assertEqual(self.patch_mask(),3)
        self.assertEqual(self.backend.audio_inputs.desired[0],1)
        self.touch('End',1,4,5);self.touch('End',0,1,1)


class AudioInputPathTests(InputPath,unittest.TestCase):
    def test_local_target_precedes_host_and_release_does_not_cancel_preparation(self):
        before=len(self.song.calls);self.add_sm58()
        self.assertEqual(len(self.song.calls),before)
        self.assertTrue(self.patch_pending())
        self.pump()
        self.assertEqual(self.backend.audio_inputs.desired[0],3)
        self.assertIn((48,3,3),self.commands)
        self.assertTrue(self.patch_pending(),'admission alone is not routing completion')
        self.until(lambda:not self.patch_pending())
        self.assertEqual(len(self.backend.reserves[0].members),2)
        self.assertEqual(self.patch_mask(),3)
        self.assertFalse(self.backend.audio_inputs.busy)

    def test_patch_main_reseed_and_dual_source_same_bar_next_cancel(self):
        self.add_sm58();self.pump();self.until(lambda:not self.patch_pending())
        self.switch('main');self.begin()
        old=self.backend.recording.take
        members=(old,)+old.members
        self.assertEqual([member.clip.name for member in members],['T-Bone','SM-58'])
        self.assertEqual({member.anchor for member in members},{old.anchor})
        self.until(lambda:0 in self.backend.reserves)
        self.button(10,10);following=old.following
        self.assertIsNotNone(following)
        self.assertEqual(following.target,old.finish_target)
        self.until(lambda:self.backend.recording.take is following and following.state=='recording')
        self.advance(100)
        self.assertEqual(self.state('currentToken'),following.take_id)
        self.assertEqual({member.anchor for member in (following,)+following.members},{old.finish_target})
        # This take exhausted the local pool. Wait for announced insertion to
        # finish before a normal Cancel; ignored Busy touches are covered by
        # the joined allocation path rather than silently replayed here.
        self.until(lambda:0 in self.backend.reserves and self.backend.allocation_ticket is None)
        self.advance(100)
        self.button(3,3);self.until(lambda:not self.backend.busy)
        self.assertEqual(self.state('currentView'),0)
        self.assertTrue(all(member.slot.has_clip and member.clip.is_playing
                            and not member.clip.is_recording for member in members))
        self.assertTrue(all(not member.slot.has_clip and not member.track.arm
                            for member in (following,)+following.members))
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))
        self.switch('patch');self.assertEqual(self.patch_mask(),3)
        self.until(lambda:not self.patch_pending())

    def test_failed_preparation_rolls_back_admitted_mask_and_ends_local_pulse(self):
        # A real background routing resolution failure, after the command was
        # admitted, must recover the prior ready patch rather than hang the UI.
        for track in self.backend.state.groups[0].tracks:
            track.available_input_routing_types=[self.fixture.routes[0],'Ext. In','No Input']
        self.add_sm58();self.pump()
        self.assertIn((48,3,3),self.commands)
        self.until(lambda:self.backend.audio_inputs.desired[0]==1 and not self.patch_pending())
        self.assertEqual(self.patch_mask(),1)
        self.assertEqual(self.backend.audio_inputs.prepared_masks[0],1)
        self.assertEqual(len(self.backend.reserves[0].members),1)
        self.assertFalse(any(slot.has_clip for track in self.backend.state.groups[0].tracks for slot in track.clip_slots))
        self.assertFalse(any(call[0]=='fire' for call in self.song.calls))


class MixedCatalogPathTests(InputPath,unittest.TestCase):
    source_count=25
    lane_count=8

    def test_full_catalog_last_tile_and_both_banks_prepare_one_take(self):
        # All25 advertised slots and their actual cached labels travel through
        # the compiled patch, actual reconciler and matched Native publisher.
        self.until(lambda:(self.backend.audio_inputs.available&(1<<24)) and
                   int.from_bytes(self.vm.heap_bytes(160+24*4,4),'little')&(1<<21))
        self.until(lambda:self.vm.heap_bytes(1076+24,1)[0]==255)
        label=self.vm.heap_bytes(276+24*32,32).split(b'\0',1)[0].decode('ascii')
        self.assertEqual(label,self.fixture.sources[24].name)
        self.touch('Start',0,1,1)
        for source in (24,13,13,1):
            x=source%5*3+1;y=5+source//5*2
            self.touch('Start',1,x,y);self.touch('End',1,x,y)
        desired=3|(1<<24)
        self.assertEqual(self.patch_mask(),desired)
        self.assertEqual(self.backend.audio_inputs.desired[0],1)
        self.touch('End',0,1,1);self.pump()
        self.assertEqual(self.backend.audio_inputs.desired[0],desired)
        self.assertEqual([(key,value) for key,value,actual in self.commands if key in (48,116)],
                         [(48,3),(116,1<<11)])
        self.until(lambda:not self.patch_pending())
        self.assertEqual([m.source.index for m in self.backend.reserves[0].members],[0,1,24])
        self.switch('main');self.begin()
        take=self.backend.recording.take
        members=(take,)+take.members
        self.assertEqual([m.clip.name for m in members],
                         [self.fixture.sources[i].name for i in (0,1,24)])
        begin=next(i for i,(key,value,actual) in enumerate(self.commands) if key==64)
        self.assertTrue(all(i<begin for i,(key,value,actual) in enumerate(self.commands) if key in (48,116)))
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))


class MixedMidiPathTests(InputPath,unittest.TestCase):
    def make_backend(self):
        self.mixed=mixed_fixture.MixedInputChecks()
        self.addCleanup(self.mixed.doCleanups)
        backend=self.mixed.make(lanes=8)
        self.fixture=self.mixed.fixture
        return backend

    def test_local_midi_patch_foreign_owner_and_mixed_same_boundary_next(self):
        # The second source is an actual MIDI instrument in the Native fixture.
        self.add_sm58()
        self.assertEqual(self.patch_mask(),3)
        self.pump();self.until(lambda:not self.patch_pending())
        self.until(lambda:int.from_bytes(self.vm.heap_bytes(160+4,4),'little')&(1<<26))
        metadata=int.from_bytes(self.vm.heap_bytes(160+4,4),'little')
        self.assertTrue(metadata&(1<<22));self.assertEqual((metadata>>23)&7,0)
        self.assertEqual(self.backend.audio_inputs.midi.owner(1),0)
        self.assertEqual([m.source.kind for m in self.backend.reserves[0].members],['audio','midi'])

        # Confirmed foreign ownership disables the real local tile before any
        # host command. It must not steal the original instrument from R1.
        self.touch('Start',0,5,1)
        self.assertFalse(self.vm.call('sourceAvailable/bii',1,1)['return'])
        before=len(self.commands)
        self.touch('Start',1,4,5);self.touch('End',1,4,5);self.touch('End',0,5,1)
        self.assertEqual(self.patch_mask(1),0)
        self.pump()
        self.assertFalse(any(key==49 for key,value,actual in self.commands[before:]))
        self.assertEqual(self.backend.audio_inputs.desired[1],0)

        self.switch('main');self.begin()
        first=self.backend.recording.take
        self.assertEqual([m.clip.is_audio_clip for m in (first,)+first.members],[True,False])
        self.assertIs(first.members[0].track.output_routing_type,self.mixed.destination)
        self.assertIs(first.members[0].track.output_routing_channel,self.mixed.endpoint)
        self.assertEqual(first.members[0].track.current_monitoring_state,2)
        self.assertIs(self.mixed.instrument.input_routing_type,self.mixed.external)
        self.assertIs(self.mixed.instrument.input_routing_channel,self.mixed.channel)
        self.assertEqual(self.mixed.instrument.devices,[self.mixed.device])
        self.assertIs(self.mixed.instrument.output_routing_type.attached_object,self.mixed.receivers[0])
        self.until(lambda:0 in self.backend.reserves and self.backend.allocation_ticket is None)
        self.button(10,10)
        following=first.following
        self.assertIsNotNone(following);self.assertEqual(following.target,first.finish_target)
        self.until(lambda:self.backend.recording.take is following and following.state=='recording')
        self.assertEqual(self.state('currentToken'),following.take_id)
        self.assertEqual({m.anchor for m in (following,)+following.members},{first.finish_target})
        self.assertTrue(all(m.clip.is_playing and not m.clip.is_recording for m in (first,)+first.members))
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))


if __name__=='__main__':unittest.main(verbosity=2)
