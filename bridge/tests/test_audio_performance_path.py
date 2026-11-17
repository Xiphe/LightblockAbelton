"""Actual compiled client + C++ reconciler + Python backend, fake Native IO.

This tests their common seam, not firmware/Live timing or real audio capture.
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
from littlefoot_harness import Harness
from test_performance_main import read_state
from PerformanceController.backend import Backend
from PerformanceController.fake_native import PerformanceSong, Track

QS=dict(q_bar=4,q_half=5,q_quarter=7,q_eight=9,q_sixtenth=11)

class PathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replay=os.environ.get('PERFORMANCE_REPLAY')
        if not cls.replay or not Path(cls.replay).is_file():
            raise unittest.SkipTest('Set PERFORMANCE_REPLAY to the built performance_replay executable')
        tmp=tempfile.TemporaryDirectory(prefix='audio-performance-path-');cls.addClassCleanup(tmp.cleanup)
        binary=Path(tmp.name)/'main.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),'compile',str(ROOT/'lightpad/performance-main.littlefoot'),'-o',str(binary)],check=True,capture_output=True)
        cls.program=binary.read_bytes()
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),'compile',str(ROOT/'lightpad/performance-metronome.littlefoot'),'-o',str(binary)],check=True,capture_output=True)
        cls.modal_program=binary.read_bytes()
    def setUp(self):
        self.vm=Harness(self.program);self.vm.call('initialise/v');self.midi_cursor=0
        self.song=PerformanceSong(groups=8,compatible=True)
        # The performance setup preallocates four compatible Audio lanes per
        # group. Rapid Next must use that pool, not rely on a background track
        # insertion during the preceding Native count-in or handoff window.
        for group in tuple(t for t in self.song.tracks
                           if t.is_foldable and t.name.startswith('[R') and t.name!='[Remote]'):
            anchor=next(t for t in self.song.tracks
                        if t.is_foldable and t.name=='[Remote]' and t.group_track is group)
            for index in range(3):
                lane=Track(self.song,'Prepared Audio %d'%(index+1),group)
                self.song.routes(lane,group,compatible=True)
                self.song._tracks.insert(self.song._tracks.index(anchor),lane)
        self.backend=Backend(self.song,QS,now=lambda:self.vm.time_ms/1000.)
        self.addCleanup(self.backend.close)
        self.pipe=subprocess.Popen([self.replay],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        self.addCleanup(self.close_pipe)
        self.obs=0;self.commands=[]
        self.exchange('reset 1 %s'%self.vm.time_ms)
        self.backend.reseed()
        for unused in range(25):self.advance(100)
        self.assertEqual(len(self.backend.reserves),8)
    def close_pipe(self):
        self.pipe.stdin.close();self.pipe.wait(timeout=3);self.pipe.stdout.close()
    def exchange(self,line):
        self.pipe.stdin.write(line+'\n');self.pipe.stdin.flush();result=[]
        while True:
            row=self.pipe.stdout.readline().strip()
            if row=='E':return result
            if not row:raise AssertionError('replay terminated')
            if row.startswith('ERR'):raise AssertionError(row)
            result.append(row.split())
    def touch(self,kind,f,x,y):
        self.vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)
    def state(self,name):return read_state(self.vm,name)
    def facts(self):
        for kind,a,b in self.backend.drain_facts(6):
            self.exchange('fact 1 %d %d %d %d %d'%(self.obs,kind,a,b,self.vm.time_ms))
            self.obs=(self.obs+1)&127
    def pump(self):
        for unused in range(300):
            messages=self.vm.midi[self.midi_cursor:];self.midi_cursor=len(self.vm.midi)
            for status,cc,value in messages:
                if status&0xf0==0xb0:
                    self.exchange('device %d %d %d %d'%((status&15)+1,cc,value,self.vm.time_ms))
            rows=self.exchange('pump %d'%self.vm.time_ms)
            for row in rows:
                if row[0]=='D':
                    gen,seq,key,value,device_seq=map(int,row[1:])
                    before=len(self.song.tracks)
                    actual=self.backend.dispatch(key,value)
                    self.assertEqual(len(self.song.tracks),before,'structural work on command dispatch')
                    self.commands.append((key,value,actual))
                    self.exchange('ack %d %d %d %d %d'%(gen,seq,key,actual,self.vm.time_ms))
                elif row[0]=='F':
                    kind,a,b,header=map(int,row[1:])
                    self.vm.call('handleMessage/viii',a,b,header)
                else:raise AssertionError(row)
            if not rows and self.midi_cursor==len(self.vm.midi):return
        self.fail('unbounded replay pump')
    def advance(self,ms):
        self.vm.time_ms+=ms
        self.song.advance(1+(self.vm.time_ms-1000)/500.)
        self.backend.tick();self.facts();self.pump();self.vm.call('repaint/v');self.pump()
    def until(self,condition):
        for unused in range(180):
            if condition():return
            self.advance(100)
        self.fail('workflow did not settle: '+str(list(self.backend.events)[-4:]))
    def begin(self,group=0):
        x=group%4*4+1;y=group//4*4+1
        self.touch('Start',0,x,y);self.touch('Move',0,x,y+4);self.touch('End',0,x,y+4)
        self.assertEqual(self.state('currentView'),2)
        self.pump()
        self.until(lambda:self.backend.recording.take is not None and self.backend.recording.take.state=='recording')
        self.advance(200)
    def button(self,x,y):
        self.touch('Start',1,x,y);self.touch('End',1,x,y);self.pump()
    def test_chord_and_fader_reach_backend_without_structural_work(self):
        self.touch('Start',0,1,1);self.touch('Start',1,5,1)
        self.touch('End',0,1,1);self.touch('End',1,5,1)
        self.assertEqual(self.state('currentMute')&3,3)
        self.pump();self.advance(100)
        self.assertTrue(self.backend.state.groups[0].group.mute)
        self.assertTrue(self.backend.state.groups[1].group.mute)
        self.assertEqual(self.state('currentMute')&3,3)
        self.touch('Start',0,1,1);self.advance(500)
        self.assertEqual(self.state('currentView'),1)
        self.touch('Start',1,1,12);self.touch('Move',1,1,9)
        wanted=self.state('currentValue');self.pump()
        self.touch('End',1,1,9);self.touch('End',0,1,1);self.pump()
        actual=self.backend.state.groups[0].group.mixer_device.volume.value
        self.assertAlmostEqual(actual,wanted/127.)
        self.assertEqual(self.state('currentView'),0)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))
    def test_chord_same_contact_retouch_stays_pending_until_last_release(self):
        self.touch('Start',0,1,1);self.touch('Start',1,5,1)
        self.touch('End',0,1,1);self.touch('Start',0,1,1);self.touch('End',0,1,1)
        self.pump();self.advance(600)
        self.assertEqual(self.state('currentView'),0)
        self.assertFalse(any(key==56 for key,value,actual in self.commands))
        self.assertEqual(self.state('currentBatch'),2)
        self.touch('Start',0,1,1);self.touch('End',0,1,1);self.pump()
        self.assertFalse(any(key==56 for key,value,actual in self.commands))
        self.touch('End',1,5,1);self.pump()
        self.assertEqual([(value,actual) for key,value,actual in self.commands if key==56],[(3,3)])

    def test_accept_next_starts_at_old_finish_inside_four_bar_reference(self):
        self.begin();base=self.backend.recording.take
        self.until(lambda:self.song.current_song_time>=base.anchor+12.25)
        self.button(10,3);self.until(lambda:self.state('currentView')==0)
        self.assertAlmostEqual(base.clip.loop_end-base.clip.loop_start,16.)
        self.begin();old=self.backend.recording.take
        self.button(10,10)
        self.assertIsNotNone(old.following,'Next must reserve its replacement before ending the current take')
        following=old.following
        self.assertAlmostEqual(following.target,old.finish_target)
        self.assertAlmostEqual(old.finish_target,old.anchor+4.)
        self.until(lambda:self.backend.recording.take is following and following.state=='recording')
        self.assertAlmostEqual(following.anchor,old.finish_target)
        self.assertEqual(self.state('currentToken'),following.take_id)
        self.assertEqual(self.state('currentView'),2)
        self.button(3,3);self.until(lambda:not self.backend.busy)

    def test_record_accept_overdub_and_accept_next_across_real_protocols(self):
        self.begin();self.button(10,3)
        self.until(lambda:self.state('currentView')==0)
        first=self.backend.state.groups[0]
        self.assertTrue(any(s.has_clip and not s.clip.is_recording for t in first.tracks for s in t.clip_slots))
        self.begin();old=self.state('currentToken');self.button(10,10)
        self.until(lambda:self.backend.recording.take is not None and self.backend.recording.take.state=='recording' and self.backend.recording.take.take_id!=old)
        self.advance(200)
        self.assertEqual(self.state('currentView'),2)
        self.button(3,3);self.until(lambda:self.state('currentView')==0)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))

    def test_cancel_immediately_after_next_preserves_the_accepted_take(self):
        self.begin();old=self.backend.recording.take
        self.button(10,10)
        # No fact tick intervenes: firmware still sends the accepted old token.
        self.button(3,3)
        self.until(lambda:not self.backend.busy)
        self.assertTrue(old.slot.has_clip)
        self.assertIs(old.slot.clip,old.clip)
        self.assertFalse(old.clip.is_recording)
        self.assertTrue(old.clip.is_playing)
        self.assertEqual(self.state('currentView'),0)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))

    def test_cancel_old_device_context_after_native_handoff_with_delayed_facts(self):
        self.begin();old=self.backend.recording.take
        self.button(10,10);following=old.following
        self.assertIsNotNone(following)
        # Live crosses the boundary while its facts have not reached the device.
        self.vm.time_ms+=100
        self.song.advance(old.finish_target-.5);self.backend.tick()
        self.vm.time_ms+=250
        self.song.advance(old.finish_target);self.backend.tick()
        self.assertIs(self.backend.recording.take,following)
        self.assertEqual(following.state,'recording')
        self.assertEqual(self.state('currentToken'),old.take_id)
        self.button(3,3)
        self.until(lambda:not self.backend.busy)
        self.assertTrue(old.slot.has_clip)
        self.assertTrue(old.clip.is_playing)
        self.assertFalse(following.slot.has_clip)
        self.assertEqual(self.state('currentView'),0)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))

    def test_retry_then_cancel_preserves_accepted_layer(self):
        self.begin();self.button(10,3);self.until(lambda:self.state('currentView')==0)
        accepted=[s.clip for t in self.backend.state.groups[0].tracks for s in t.clip_slots if s.has_clip]
        self.assertEqual(len(accepted),1)
        self.begin();old=self.backend.recording.take.clip;token=self.state('currentToken')
        self.button(3,10)
        self.until(lambda:self.backend.recording.take is not None and self.backend.recording.take.state=='recording' and self.backend.recording.take.take_id!=token)
        self.advance(200)
        self.button(3,3);self.until(lambda:self.state('currentView')==0)
        # UI closes optimistically; actual owned-clip cleanup finishes on Native ticks.
        self.until(lambda:not self.backend.busy)
        remaining=[s.clip for t in self.backend.state.groups[0].tracks for s in t.clip_slots if s.has_clip]
        self.assertEqual(remaining,accepted)
        self.assertFalse(any(clip is old for clip in remaining))
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))
    def test_cancel_old_device_context_after_retry_with_delayed_facts(self):
        self.begin();old=self.backend.recording.take
        self.button(3,10)
        for unused in range(50):
            self.vm.time_ms+=100
            self.song.advance(1+(self.vm.time_ms-1000)/500.)
            self.backend.tick()
            current=self.backend.recording.take
            if current is not None and current is not old and current.state in ('queued','recording'):
                break
        else:self.fail('Retry replacement was not admitted')
        self.assertEqual(self.state('currentToken'),old.take_id)
        self.button(3,3);self.until(lambda:not self.backend.busy)
        self.assertFalse(any(s.has_clip for t in self.backend.state.groups[0].tracks for s in t.clip_slots))
        self.assertEqual(self.state('currentView'),0)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))

    def test_modal_reseed_meter_click_then_main_fader_sequence(self):
        now=self.vm.time_ms;self.vm=Harness(self.modal_program);self.vm.time_ms=now
        self.vm.call('initialise/v');self.midi_cursor=0
        self.exchange('reseed %d'%now);self.pump();self.advance(100)
        self.touch('Start',0,3,5);self.touch('Move',0,3,3);self.touch('End',0,3,3);self.pump()
        self.assertEqual(self.song.signature_numerator,5)
        self.button(3,12);self.assertTrue(self.song.metronome)
        now=self.vm.time_ms;self.vm=Harness(self.program);self.vm.time_ms=now
        self.vm.call('initialise/v');self.midi_cursor=0
        self.exchange('reseed %d'%now);self.pump();self.advance(100)
        self.touch('Start',0,1,1);self.advance(500)
        self.touch('Start',1,1,12);self.touch('Move',1,1,10);wanted=self.state('currentValue')
        self.pump();self.touch('End',1,1,10);self.touch('End',0,1,1);self.pump()
        self.assertAlmostEqual(self.backend.state.groups[0].group.mixer_device.volume.value,wanted/127.)
        self.assertTrue(all(actual!=16383 for key,value,actual in self.commands))

if __name__=='__main__':unittest.main(verbosity=2)
