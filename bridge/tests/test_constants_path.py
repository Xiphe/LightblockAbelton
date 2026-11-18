"""Compiled Constants UI + actual C++ reconciler + Python Native adapter.

Only Native endpoints, time, graphics and MIDI transports are fake. No Live,
hardware or installed files are touched; these are functional seam checks.
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
for directory in ('lightpad/tests','live','live/PerformanceController','bridge/tests'):
    sys.path.insert(0,str(ROOT/directory))
from littlefoot_harness import Harness
import test_audio_inputs as audio_fixture
import test_constants as constants_fixture
import test_audio_performance_path as baseline


class ConstantsPathTests(unittest.TestCase):
    exchange=baseline.PathTests.exchange
    close_pipe=baseline.PathTests.close_pipe
    touch=baseline.PathTests.touch
    state=baseline.PathTests.state
    facts=baseline.PathTests.facts
    until=baseline.PathTests.until
    begin=baseline.PathTests.begin
    button=baseline.PathTests.button

    @classmethod
    def setUpClass(cls):
        cls.replay=os.environ.get('PERFORMANCE_REPLAY')
        if not cls.replay or not Path(cls.replay).is_file():
            raise unittest.SkipTest('Set PERFORMANCE_REPLAY to the matching performance_replay executable')
        temporary=tempfile.TemporaryDirectory(prefix='constants-path-')
        cls.addClassCleanup(temporary.cleanup)
        cls.programs={}
        for name in ('main','patch','metronome'):
            binary=Path(temporary.name)/(name+'.bin')
            subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),
                            'compile',str(ROOT/('lightpad/performance-'+name+'.littlefoot')),
                            '-o',str(binary)],check=True,capture_output=True)
            program=binary.read_bytes()
            unused,code,unused2,globals_,heap=struct.unpack_from('<5H',program)
            if code+heap>7200:
                raise AssertionError('%s canonical program exceeds7200bytes: %d'%(name,code+heap))
            cls.programs[name]=program

    def setUp(self):
        self.fixture=audio_fixture.AudioInputChecks()
        self.addCleanup(self.fixture.doCleanups)
        self.backend=self.fixture.make(initial=1,groups=8,source_count=2,lanes=4)
        self.song=self.fixture.song
        self.constant_root,self.constants=constants_fixture.add_constants(self.song)
        for target in self.constants:
            target.mixer_device.volume.value=.5
            target.mixer_device.panning.value=0.
            for parameter in target.mixer_device.sends:parameter.value=.25
        self.backend.state.topology_dirty=True
        self.fixture.ticks(80)
        self.vm=Harness(self.programs['main']);self.vm.time_ms=max(1000,int(self.fixture.clock*1000))
        self.vm.call('initialise/v');self.midi_cursor=0
        self.pipe=subprocess.Popen([self.replay],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        self.addCleanup(self.close_pipe)
        self.commands=[];self.obs=0;self.delay_ack=False;self.held_acks=[]
        self.exchange('reset 1 %s'%self.vm.time_ms)
        self.backend.reseed()
        self.until(lambda:0 in self.backend.reserves and all(
            self.vm.call('available/bi',group)['return'] for group in range(12)))
        self.until(lambda:self.vm.call('hasSource/bi',0)['return'])

    def pump(self):
        for unused in range(1024):
            messages=self.vm.midi[self.midi_cursor:];self.midi_cursor=len(self.vm.midi)
            for status,cc,value in messages:
                if status&0xf0==0xb0:
                    self.exchange('device %d %d %d %d'%((status&15)+1,cc,value,self.vm.time_ms))
            rows=self.exchange('pump %d'%self.vm.time_ms)
            for row in rows:
                if row[0]=='D':
                    generation,seq,key,value,device_seq=map(int,row[1:])
                    before=len(self.song.tracks)
                    actual=self.backend.dispatch(key,value)
                    self.assertEqual(len(self.song.tracks),before,'Native allocation on command dispatch')
                    self.commands.append((key,value,actual))
                    ack='ack %d %d %d %d %d'%(generation,seq,key,actual,self.vm.time_ms)
                    if self.delay_ack:self.held_acks.append(ack)
                    else:self.exchange(ack)
                elif row[0]=='F':
                    kind,a,b,header=map(int,row[1:])
                    self.vm.call('handleMessage/viii',a,b,header)
                else:raise AssertionError(row)
            if not rows and self.midi_cursor==len(self.vm.midi):return
        self.fail('unbounded Constants replay pump')

    def advance(self,ms):
        self.vm.time_ms+=ms;self.fixture.clock=self.vm.time_ms/1000.
        self.song.advance(1+(self.vm.time_ms-1000)/500.)
        self.backend.tick();self.facts();self.pump();self.vm.call('repaint/v');self.pump()

    def frames(self,start=0):
        messages=self.vm.midi[start:];frames=[]
        for index in range(len(messages)-5):
            unit=messages[index:index+6]
            if (unit[0][0] in (176,177) and all(m[0]==unit[0][0] for m in unit)
                    and [m[1] for m in unit]==[69,70,71,72,73,74] and unit[1][2]==unit[-1][2]):
                bank=unit[0][0]-176
                frames.append((unit[1][2],unit[2][2]+128*bank,unit[3][2]|unit[4][2]<<7))
        return frames

    def open_constant(self,index):
        self.touch('Start',0,index*4+1,8.5);self.advance(500)
        self.assertEqual(self.state('currentView'),1)

    def close_constant(self,index):
        self.touch('End',0,index*4+1,8.5);self.pump()
        self.assertEqual(self.state('currentView'),0)

    def drag_parameter(self,index,control):
        key=128+index*6+control
        start=len(self.vm.midi)
        if control==1:
            self.touch('Start',1,4,12.5);self.touch('Move',1,8,12.5);self.touch('End',1,8,12.5)
        else:
            x=1 if control==0 else (4,7,10,13)[control-2]
            self.touch('Start',1,x,5);self.touch('Move',1,x,3);self.touch('End',1,x,3)
        frames=[frame for frame in self.frames(start) if frame[1]==key]
        self.assertTrue(frames,'gesture must emit its bank2 parameter locally')
        wanted=frames[-1][2];self.pump()
        parameter=self.backend.state.parameters[8+index][control]
        fraction=.5 if control==1 and wanted==64 else wanted/127.
        self.assertAlmostEqual(parameter.value,parameter.min+(parameter.max-parameter.min)*fraction)
        self.assertEqual([actual for k,value,actual in self.commands if k==key][-1],wanted)
        return wanted

    def switch(self,name):
        now=self.vm.time_ms;self.vm=Harness(self.programs[name]);self.vm.time_ms=now
        self.vm.call('initialise/v');self.midi_cursor=0
        self.exchange('patch-names %d'%(name=='patch'))
        self.exchange('reseed %d'%now);self.pump();self.advance(100)

    def test_all_constants_mute_and_six_controls_reach_retained_targets(self):
        calls=len(self.song.calls)
        for index in range(4):
            x=index*4+1;bit=1<<(8+index)
            self.touch('Start',0,x,8.5);self.touch('End',0,x,8.5)
            self.assertTrue(self.state('currentMute')&bit)
            self.assertFalse(self.constants[index].mute,'local mute must precede the Native roundtrip')
            self.pump();self.assertTrue(self.constants[index].mute)
            self.open_constant(index)
            for control in range(6):self.drag_parameter(index,control)
            self.close_constant(index)
        self.assertEqual({key for key,value,actual in self.commands if key>=128},set(range(128,152)))
        self.assertFalse(any(actual==16383 for key,value,actual in self.commands))
        self.assertEqual(len(self.song.calls),calls)
        self.assertFalse(self.constant_root.mute)

    def test_mixed_loop_constant_chord_commits_one_mask_on_last_release(self):
        before=len(self.commands)
        self.touch('Start',0,1,1);self.touch('Start',1,1,8.5);self.touch('Start',2,13,8.5)
        self.touch('End',0,1,1);self.touch('End',1,1,8.5);self.pump();self.advance(600)
        self.assertEqual(self.state('currentView'),0)
        self.assertFalse(self.commands[before:])
        self.assertFalse(self.constants[0].mute);self.assertFalse(self.constants[3].mute)
        self.touch('End',2,13,8.5)
        mask=1|(1<<8)|(1<<11)
        self.assertEqual(self.state('currentMute')&mask,mask)
        self.pump();self.assertEqual(self.commands[before:],[(56,mask,mask)])
        self.assertTrue(self.backend.state.groups[0].group.mute)
        self.assertTrue(self.constants[0].mute);self.assertTrue(self.constants[3].mute)

    def test_delayed_old_constant_reply_does_not_rewind_local_drag_or_reopen(self):
        self.open_constant(0);self.delay_ack=True
        self.touch('Start',1,1,5);self.touch('Move',1,1,3);self.pump()
        self.assertEqual(len(self.held_acks),1)
        old=self.state('currentValue')
        self.advance(100);self.touch('Move',1,1,6);self.touch('End',1,1,6);self.pump()
        wanted=self.state('currentValue');self.assertNotEqual(wanted,old)
        self.close_constant(0);self.open_constant(0)
        self.assertEqual(self.state('currentValue'),wanted)
        self.delay_ack=False
        for ack in self.held_acks:self.exchange(ack)
        self.held_acks.clear();self.pump();self.advance(200)
        self.assertEqual(self.state('currentValue'),wanted)
        self.assertAlmostEqual(self.constants[0].mixer_device.volume.value,wanted/127.)
        self.close_constant(0)

    def test_constant_down_L_and_horizontal_moves_never_record_or_monitor(self):
        before=len(self.commands)
        for points in (((1,8.5),(1,12.5)),((1,8.5),(1,12.5),(5,12.5)),((1,8.5),(5,8.5))):
            self.touch('Start',0,*points[0])
            for point in points[1:]:self.touch('Move',0,*point)
            self.touch('End',0,*points[-1]);self.pump()
            self.assertEqual(self.state('currentView'),0)
        self.assertEqual(self.commands[before:],[])
        self.assertIsNone(self.backend.recording.take)
        self.assertEqual(set(self.backend.state.groups),set(range(8)))

    def test_loop_capture_and_modal_reseeds_preserve_constant_controls(self):
        self.begin();self.button(10,3);self.until(lambda:self.state('currentView')==0)
        accepted=self.backend.recording.take
        self.assertEqual(accepted.state,'completed');self.assertTrue(accepted.slot.has_clip)
        self.open_constant(3);wanted=self.drag_parameter(3,0);self.close_constant(3)
        self.switch('patch');self.until(lambda:[191,108,37] in self.vm.midi)
        self.switch('metronome');self.until(lambda:[191,108,35] in self.vm.midi)
        self.touch('Start',0,3,5);self.touch('Move',0,3,3);self.touch('End',0,3,3);self.pump()
        self.assertEqual(self.song.signature_numerator,5)
        self.switch('main');self.until(lambda:all(self.vm.call('available/bi',g)['return'] for g in range(12)))
        self.open_constant(3);self.assertEqual(self.state('currentValue'),wanted)
        self.drag_parameter(3,5);self.close_constant(3)
        self.assertTrue(accepted.slot.has_clip)
        self.assertFalse(any(actual==16383 for key,value,actual in self.commands))


if __name__=='__main__':unittest.main(verbosity=2)
