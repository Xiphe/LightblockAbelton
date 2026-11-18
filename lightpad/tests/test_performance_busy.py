"""Execute the painted-before-grant maintenance lock in all three clients.

Fake LEDs prove callback order, not firmware display timing or Live latency.
"""
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from littlefoot_harness import Harness

ROOT=Path(__file__).resolve().parents[1]


class PreparationBusyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=tempfile.TemporaryDirectory(prefix='performance-busy-')
        cls.addClassCleanup(folder.cleanup);cls.programs={};cls.names={}
        compiler=shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl')
        for name in ('main','metronome','patch'):
            source=ROOT/('performance-'+name+'.littlefoot');binary=Path(folder.name)/(name+'.bin')
            subprocess.run([compiler,'compile',str(source),'-o',str(binary)],check=True,capture_output=True)
            cls.programs[name]=binary.read_bytes()
            cls.names[name]=re.findall(r'^(?:int|float|bool) (\w+);',source.read_text(),re.M)

    def make(self,name):
        vm=Harness(self.programs[name]);vm.call('initialise/v');vm.name=name;vm.revision=0
        self.model(vm,0,127,0)
        if name=='main':
            for key in range(48):self.model(vm,1,key,64 if key%6<2 else 0)
        for group in range(8):
            self.model(vm,3,group,100|70<<7|50<<14|1<<21|1<<22)
            if name=='patch':
                self.model(vm,1,48+group,1);self.model(vm,6,8+group,1|1<<25)
        if name=='patch':
            for source in range(25):self.model(vm,6,24+source,100|70<<7|50<<14|1<<21)
        self.model(vm,6,0,120*128);self.model(vm,6,1,4|4<<7|1<<14|255<<16)
        self.model(vm,4,8|1024<<4,0)
        vm.call('repaint/v');vm.midi.clear()
        return vm

    def state(self,vm,name):
        return struct.unpack_from('<i',vm.memory,vm.stack_end+self.names[vm.name].index(name)*4)[0]

    @staticmethod
    def model(vm,kind,a,b,epoch=1):
        vm.revision+=1
        vm.call('handleMessage/viii',a,b,0xf2|kind<<8|epoch<<11|vm.revision<<18)

    @staticmethod
    def touch(vm,kind,f,x,y):
        vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)

    @staticmethod
    def frames(vm):
        messages=[m for m in vm.midi if m[0]==176];result=[]
        for i in range(len(messages)-5):
            unit=messages[i:i+6]
            if [m[1] for m in unit]==[69,70,71,72,73,74] and unit[1][2]==unit[-1][2]:
                result.append((unit[0][2],unit[1][2],unit[2][2],unit[3][2]|unit[4][2]<<7))
        return result

    def grants(self,vm):return [frame for frame in self.frames(vm) if frame[2]==58]

    def engage(self,vm,ticket=7199):
        self.model(vm,6,3,ticket)
        vm.call('repaint/v')
        self.assertEqual(vm.leds[7][7],0xffc000)
        self.assertEqual(self.grants(vm),[],'painting must precede the grant by a full callback')
        vm.call('repaint/v')
        self.assertEqual([frame[3] for frame in self.grants(vm)],[ticket])

    def test_existing_contacts_finish_and_parameters_precede_the_single_grant(self):
        for name in self.programs:
            with self.subTest(name=name):
                vm=self.make(name)
                self.touch(vm,'Start',0,1 if name!='metronome' else 3,1 if name!='metronome' else 5)
                if name=='main':
                    vm.time_ms+=500;vm.call('repaint/v');self.touch(vm,'Start',1,1,12)
                    self.touch(vm,'Move',1,1,10)
                elif name=='metronome':self.touch(vm,'Move',0,3,3)
                else:self.touch(vm,'Start',1,4,5)
                self.model(vm,6,3,7199)
                for unused in range(3):vm.call('repaint/v')
                self.assertEqual(self.grants(vm),[])
                self.assertGreater(self.state(vm,'down'),0)
                if name=='main':
                    self.touch(vm,'Move',1,1,9);self.touch(vm,'End',1,1,9);self.touch(vm,'End',0,1,1)
                elif name=='metronome':
                    self.touch(vm,'Move',0,3,1);self.touch(vm,'End',0,3,1)
                else:
                    self.touch(vm,'End',1,4,5);self.touch(vm,'End',0,1,1)
                self.assertEqual(self.state(vm,'down'),0)
                self.assertTrue(self.frames(vm),'the original gesture must complete normally')
                vm.call('repaint/v');self.assertEqual(self.grants(vm),[])
                self.assertEqual(vm.leds[7][7],0xffc000)
                vm.call('repaint/v')
                self.assertEqual(self.frames(vm)[-1][2:],(58,7199))
                for unused in range(3):vm.call('repaint/v')
                self.assertEqual(len(self.grants(vm)),1)

    def test_painted_lock_ignores_touches_and_clear_does_not_replay_orphan_events(self):
        for name in self.programs:
            with self.subTest(name=name):
                vm=self.make(name);self.model(vm,6,3,23);vm.call('repaint/v')
                before=vm.heap_bytes();commands=self.frames(vm)
                for f,x,y in ((0,1,1),(1,4,5),(2,10,12)):
                    self.touch(vm,'Start',f,x,y);self.touch(vm,'Move',f,x,y-1)
                self.assertEqual(self.frames(vm),commands)
                self.assertEqual(vm.heap_bytes(),before)
                self.assertEqual(self.state(vm,'down'),0)
                vm.call('repaint/v');self.assertEqual(self.grants(vm)[0][3],23)
                # Finger1 remains physically held through the clear. Its next
                # move/end has no accepted start and must not become a tap.
                self.model(vm,6,3,0);before=vm.heap_bytes();commands=self.frames(vm)
                self.touch(vm,'Move',1,4,3);self.touch(vm,'End',1,4,3)
                self.assertEqual(vm.heap_bytes(),before);self.assertEqual(self.frames(vm),commands)
                if name=='main':
                    self.touch(vm,'Start',1,1,1);self.touch(vm,'End',1,1,1)
                elif name=='metronome':
                    self.touch(vm,'Start',1,10,12);self.touch(vm,'End',1,10,12)
                else:
                    self.touch(vm,'Start',0,1,1);self.touch(vm,'Start',1,4,5)
                    self.touch(vm,'End',1,4,5);self.touch(vm,'End',0,1,1)
                self.assertGreater(len(self.frames(vm)),len(commands),'a fresh gesture works after clear')

    def test_duplicate_ticket_and_epoch_reset_do_not_resend_or_leave_a_stale_lock(self):
        for name in self.programs:
            with self.subTest(name=name):
                vm=self.make(name);self.engage(vm)
                self.model(vm,6,3,7199);vm.call('repaint/v')
                self.assertEqual(len(self.grants(vm)),1)
                self.model(vm,0,5,0,epoch=2)
                self.assertEqual(self.state(vm,'preparation'),0)
                self.assertEqual(self.state(vm,'preparationPhase'),0)
                vm.midi.clear();self.model(vm,6,3,42,epoch=2)
                vm.call('repaint/v');self.assertEqual(self.grants(vm),[])
                vm.call('repaint/v');self.assertEqual(self.grants(vm)[0][0::3],(2,42))
                vm.call('initialise/v')
                self.assertEqual(self.state(vm,'preparation'),0)
                self.assertEqual(self.state(vm,'preparationPhase'),0)

    def test_recording_clock_and_border_continue_under_the_bar_without_host_feedback(self):
        vm=self.make('main');token=41
        self.model(vm,5,token<<3|3<<10|64<<13,0)
        self.engage(vm);before=tuple(vm.leds[0])
        vm.time_ms+=300;vm.call('repaint/v')
        self.assertNotEqual(tuple(vm.leds[0]),before)
        self.assertEqual(vm.leds[7][7],0xffc000)
        self.assertEqual(self.state(vm,'view'),2)
        self.assertEqual(self.state(vm,'token'),token)
        self.model(vm,6,3,0);vm.call('repaint/v')
        self.assertEqual(self.state(vm,'view'),2)
        self.assertEqual(self.state(vm,'token'),token)

    def test_overlay_survives_the_recording_scene_early_return(self):
        vm=self.make('main');token=17
        self.model(vm,5,token<<3|3<<10|64<<13,0)
        self.model(vm,5,token<<3|1<<10|64<<13,0)
        self.assertEqual(self.state(vm,'recPhase'),1)
        self.engage(vm)
        self.assertEqual(vm.leds[7][7],0xffc000)


if __name__=='__main__':unittest.main()
