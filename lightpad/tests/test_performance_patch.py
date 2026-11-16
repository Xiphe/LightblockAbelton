"""Run the canonical local Audio patch program without device or Live access."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from littlefoot_harness import Harness

SOURCE=Path(__file__).resolve().parents[1]/'performance-patch.littlefoot'

class PerformancePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory=tempfile.TemporaryDirectory(prefix='performance-patch-');cls.addClassCleanup(directory.cleanup)
        binary=Path(directory.name)/'patch.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),'compile',str(SOURCE),'-o',str(binary)],check=True,capture_output=True)
        cls.program=binary.read_bytes();cls.instances=[]
    def setUp(self):
        self.vm=Harness(self.program);self.instances.append(self.vm);self.vm.call('initialise/v');self.revision=0
        self.model(0,127,0)
        for g in range(8):
            self.model(3,g,100|70<<7|50<<14|1<<21)
            self.model(1,48+g,1)
            self.model(6,8+g,1|1<<2)
        self.model(6,24,90|40<<7|10<<14|1<<21)
        self.model(6,25,10|70<<7|110<<14|1<<21)
    def model(self,kind,a,b,epoch=1,revision=None):
        self.revision=(self.revision+1)&8191 if revision is None else revision
        self.vm.call('handleMessage/viii',a,b,0xf2|kind<<8|epoch<<11|self.revision<<18)
    def touch(self,kind,f,x,y):self.vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)
    def repaint(self,ms=0):self.vm.time_ms+=ms;self.vm.call('repaint/v')
    def desired(self,g=0):return self.vm.heap_bytes(32+g,1)[0]
    def frames(self):
        messages=[m for m in self.vm.midi if m[0]==176]
        result=[]
        for i in range(len(messages)-5):
            unit=messages[i:i+6]
            if [m[1] for m in unit]==[69,70,71,72,73,74] and unit[1][2]==unit[-1][2]:
                result.append((unit[0][2],unit[1][2],unit[2][2],unit[3][2]|unit[4][2]<<7))
        return result
    def test_budget_ready_and_group_first_source_toggle_is_immediate(self):
        _,code,_,globals_,heap=struct.unpack_from('<5H',self.program)
        self.assertLessEqual(code+heap,7200);self.assertLess(globals_*4,800)
        self.assertIn([191,108,37],self.vm.midi)
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.assertEqual(self.desired(),3)
        self.assertEqual(self.frames()[-1][2:],(48,3))
        self.repaint()
        # Upper row previews the desired addition before any host response.
        self.assertGreater(self.vm.leds[5][4],self.vm.leds[6][4])
        self.touch('End',1,4,5);self.touch('Start',1,4,5)
        self.assertEqual(self.desired(),1)
        self.assertEqual(self.frames()[-1][2:],(48,1))
    def test_source_first_name_and_assignment_need_no_host_contact_roundtrip(self):
        self.touch('Start',1,4,5);self.repaint()
        first=[row[:] for row in self.vm.leds[:4]]
        self.assertTrue(any(pixel==0xb0b0b0 for row in first for pixel in row))
        self.repaint(800)
        self.assertNotEqual(self.vm.leds[:4],first)
        self.assertEqual(self.frames(),[])
        self.touch('Start',0,9,3)
        self.assertEqual(self.desired(6),3)
        self.assertEqual(self.frames()[-1][2:],(54,3))
        self.touch('End',0,9,3);self.touch('End',1,4,5)
        self.assertEqual(self.desired(6),3)
        self.assertFalse(any(20<=m[1]<=26 for m in self.vm.midi))
    def test_released_context_and_delayed_facts_preserve_latest_desired(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5);first=self.frames()[-1]
        self.touch('End',1,4,5);self.touch('Start',1,4,5);latest=self.frames()[-1]
        self.touch('End',1,4,5);self.touch('End',0,1,1)
        self.model(1,48,3);self.model(6,8,3|1<<2|16)
        self.model(2,48|first[1]<<7,3)
        self.repaint(1200)
        self.assertEqual(self.desired(),1)
        self.model(2,48|latest[1]<<7,1)
        self.assertEqual(self.desired(),1)
        self.model(6,8,1|1<<2)
        self.touch('Start',0,1,1);self.repaint()
        self.assertEqual(self.vm.leds[5][1],self.vm.leds[6][1])
    def test_both_sources_can_be_selected_before_the_group(self):
        self.touch('Start',1,1,5);self.touch('Start',2,4,5)
        self.touch('Start',0,1,1)
        self.assertEqual(self.desired(),2)
        self.assertEqual([frame[2:] for frame in self.frames()],[(48,2)])
        self.touch('Move',0,1,2);self.repaint(500)
        self.assertEqual(len(self.frames()),1)
    def test_reject_rolls_back_only_matching_intent_and_flashes(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5);first=self.frames()[-1]
        self.touch('End',1,4,5);self.touch('Start',1,4,5);latest=self.frames()[-1]
        self.model(7,48|first[1]<<7,0)
        self.assertEqual(self.desired(),1)
        self.model(2,48|latest[1]<<7,16383)
        self.assertEqual(self.desired(),1)
        self.touch('End',1,4,5);self.touch('Start',1,4,5);new=self.frames()[-1]
        self.model(2,48|new[1]<<7,16383);self.repaint()
        red=self.vm.leds[5][4]
        self.assertGreater(red>>16,(red>>8)&255)
    def test_unavailable_source_and_multiple_group_context_do_not_assign(self):
        self.model(6,25,10|70<<7|110<<14)
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.assertEqual(self.frames(),[]);self.assertEqual(self.desired(),1)
        self.touch('End',1,4,5);self.touch('Start',2,5,1);self.touch('Start',1,1,5)
        self.assertEqual(self.frames(),[])
    def test_reused_contact_id_cannot_keep_its_old_group_context(self):
        self.touch('Start',0,1,1)
        self.touch('Start',0,4,5)
        self.assertEqual(self.frames(),[])
        self.touch('Start',1,5,1)
        self.assertEqual(self.frames()[-1][2:],(49,3))
        self.assertEqual(self.desired(),1)
    def test_unavailable_assigned_source_can_still_be_removed(self):
        self.model(6,24,90|40<<7|10<<14)
        self.touch('Start',0,1,1);self.touch('Start',1,1,5)
        self.assertEqual(self.desired(),0)
        self.assertEqual(self.frames()[-1][2:],(48,0))
    def test_seed_preserves_context_and_reissues_only_safe_latest_assignment(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.model(0,70,0,epoch=2,revision=0)
        self.assertEqual(self.desired(),3)
        self.assertEqual(self.frames()[-1][0:2],(2,71))
        self.model(0,1,0,epoch=2)
        self.assertEqual(self.frames()[-1][0:2],(2,72))
        self.touch('End',1,4,5);self.touch('Start',1,4,5)
        self.assertEqual(self.desired(),1)
    def test_revision_loss_requests_bounded_reseed_without_erasing_local_intent(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.revision+=1;self.model(6,8,1|1<<2)
        before=len([m for m in self.vm.midi if m==[191,110,19]])
        self.repaint();self.repaint(50)
        self.assertEqual(len([m for m in self.vm.midi if m==[191,110,19]]),before+1)
        self.assertEqual(self.desired(),3)
    @classmethod
    def tearDownClass(cls):
        print('performance-patch code/heap/globals:',struct.unpack_from('<5H',cls.program)[1:])
        print('patch VM max stack:',max(vm.max_stack_bytes for vm in cls.instances))

if __name__=='__main__':unittest.main(verbosity=2)
