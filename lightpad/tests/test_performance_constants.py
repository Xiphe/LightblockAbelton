"""Compiled local Constants controls, bank isolation and packed contact bounds."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from littlefoot_harness import Harness
from test_performance_main import read_state

SOURCE=Path(__file__).resolve().parents[1]/'performance-main.littlefoot'


class PerformanceConstantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory=tempfile.TemporaryDirectory(prefix='performance-constants-')
        cls.addClassCleanup(directory.cleanup)
        binary=Path(directory.name)/'main.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),
                        'compile',str(SOURCE),'-o',str(binary)],check=True,capture_output=True)
        cls.program=binary.read_bytes()

    def setUp(self):
        self.vm=Harness(self.program);self.vm.call('initialise/v');self.revision=0
        self.model(0,127,0)
        for key in range(72):self.model(1,key,64 if key%6<2 else 0)
        for group in range(12):self.model(3,group,100|70<<7|50<<14|1<<21|int(group<8)<<22)
        self.model(6,0,120*128);self.model(6,1,4|4<<7|1<<14|255<<16)
        self.model(4,8|1024<<4,0)

    def model(self,kind,a,b,epoch=1):
        self.revision=(self.revision+1)&8191
        self.vm.call('handleMessage/viii',a,b,0xf2|kind<<8|epoch<<11|self.revision<<18)

    def repaint(self,ms=0):
        self.vm.time_ms+=ms;self.vm.call('repaint/v')

    def touch(self,kind,f,x,y):
        self.vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)

    def state(self,key):return read_state(self.vm,key,SOURCE)

    def frames(self):
        messages=[m for m in self.vm.midi if m[0] in (176,177)]
        result=[]
        for i in range(len(messages)-5):
            unit=messages[i:i+6]
            if (len({m[0] for m in unit})==1 and [m[1] for m in unit]==[69,70,71,72,73,74]
                    and unit[1][2]==unit[-1][2]):
                result.append((unit[0][0]-175,unit[0][2],unit[1][2],unit[2][2],unit[3][2]|unit[4][2]<<7))
        return result

    def hold(self,group=8):
        self.touch('Start',0,(group%4)*4+1,8.5)
        self.repaint(500)
        self.assertEqual(self.state('currentView'),1)
        self.assertEqual(self.state('selected'),group)

    def test_compiled_budget_and_flat_strip_geometry(self):
        _,code,_,globals_,heap=struct.unpack_from('<5H',self.program)
        self.assertLessEqual(code+heap,7200)
        self.assertLess(globals_*4+self.vm.max_stack_bytes,800)
        self.repaint()
        for group in range(8,12):
            x=group%4*4
            self.assertTrue(all(self.vm.leds[y][x+n]!=0 for y in (8,9) for n in range(3)))
            self.assertEqual(self.vm.leds[10][x],0)
        self.assertTrue(all(self.vm.leds[11][x]==0 for x in range(15)))

    def test_tap_and_mixed_chord_keep_local_preview_until_last_release(self):
        self.repaint();before=self.vm.leds[8][1]
        self.touch('Start',0,1,1);self.touch('Start',1,1,8.5)
        self.touch('End',1,1,8.5);self.repaint()
        self.assertLess(self.vm.leds[8][1],before)
        self.assertEqual(self.vm.leds[10][1],0xb0b0b0)
        self.assertEqual(self.frames(),[])
        self.touch('Start',1,1,8.5);self.touch('End',1,1,8.5)
        self.assertEqual(self.state('currentBatch'),1)
        self.touch('Start',1,1,8.5);self.touch('End',1,1,8.5)
        self.touch('End',0,1,1)
        self.assertEqual(self.frames()[-1][0::3],(1,56))
        self.assertEqual(self.frames()[-1][-1],257)
        self.assertEqual(self.state('currentMute'),257)
        self.model(3,8,100|70<<7|50<<14|1<<21)
        self.assertEqual(self.state('currentMute'),257,'older facts cannot replace pending mute')

    def test_top_mixer_bottom_pan_and_visual_metronome_stay_local(self):
        self.hold(8)
        self.assertNotEqual(self.vm.leds[0][0],0)
        self.assertNotEqual(self.vm.leds[8][1],0)
        for y in (7,10,11):self.assertEqual(self.vm.leds[y],[0]*15)
        self.assertNotEqual(self.vm.leds[12][7],0)
        self.assertTrue(any(self.vm.leds[14]))
        self.touch('Start',1,1,3);before=self.vm.memory[self.vm.heap_start+48]
        self.touch('Move',1,1,0)
        self.assertGreater(self.vm.memory[self.vm.heap_start+48]&127,before&127)
        self.assertEqual(self.frames()[-1][0],2)
        self.assertEqual(self.frames()[-1][3],0)
        self.touch('End',1,1,0)
        self.touch('Start',1,7,12.5);self.touch('Move',1,12,12.5);self.touch('End',1,12,12.5)
        self.assertEqual(self.frames()[-1][0],2)
        self.assertEqual(self.frames()[-1][3],1)
        self.touch('End',0,1,8.5)
        self.assertEqual(self.state('currentView'),0)

    def test_last_parameter_and_last_contact_preserve_full_key_and_value(self):
        self.model(1,71,64);self.hold(11)
        self.touch('Start',15,13,6);self.touch('Move',15,13,0);self.touch('End',15,13,0)
        frame=self.frames()[-1]
        self.assertEqual((frame[0],frame[3],frame[4]),(2,23,127))
        self.assertEqual(self.vm.memory[self.vm.heap_start+71]&127,127)
        self.touch('End',0,13,8.5)
        self.assertEqual(self.state('currentView'),0)

    def test_stale_constant_ack_and_wrong_bank_do_not_rewind_parameter(self):
        self.hold(10)
        self.touch('Start',1,10,6);self.touch('Move',1,10,4)
        first=self.frames()[-1];self.repaint(45);self.touch('Move',1,10,1)
        latest=self.frames()[-1];key=64
        expected=self.vm.memory[self.vm.heap_start+key]&127
        self.model(2,first[3]|first[2]<<7|1<<14,first[4])
        self.model(1,key,0)
        self.assertEqual(self.vm.memory[self.vm.heap_start+key]&127,expected)
        self.touch('End',1,10,1);self.touch('End',0,9,8.5)
        self.model(2,64|latest[2]<<7,16383)
        self.assertTrue(self.vm.call('pending/bi',key)['return'],'unbanked record ACK is not a Constant parameter ACK')
        self.model(2,latest[3]|latest[2]<<7|1<<14,expected)
        self.assertFalse(self.vm.call('pending/bi',key)['return'])
        self.assertEqual(self.vm.memory[self.vm.heap_start+key]&127,expected)

    def test_patch_ack_cannot_acknowledge_constant_parameter(self):
        self.hold(8);self.touch('Start',1,1,6);self.touch('Move',1,1,0)
        frame=self.frames()[-1]
        self.touch('End',1,1,0);self.touch('End',0,1,8.5)
        self.model(2,48|frame[2]<<7,1)
        self.assertTrue(self.vm.call('pending/bi',48)['return'])
        self.model(2,frame[3]|frame[2]<<7|1<<14,frame[4])
        self.assertFalse(self.vm.call('pending/bi',48)['return'])

    def test_constants_have_no_record_clear_or_monitor_gestures(self):
        for end in ((1,13),(5,13),(6,8.5)):
            self.touch('Start',0,1,8.5)
            self.touch('Move',0,1,13)
            self.touch('Move',0,*end)
            self.touch('End',0,*end)
            self.assertEqual(self.state('currentView'),0)
        self.assertEqual(self.frames(),[])

    def test_unavailable_constant_has_no_actions(self):
        self.model(3,11,0)
        self.touch('Start',0,13,8.5);self.repaint(600);self.touch('End',0,13,8.5)
        self.assertEqual(self.state('currentView'),0)
        self.assertEqual(self.frames(),[])


if __name__=='__main__':unittest.main()
