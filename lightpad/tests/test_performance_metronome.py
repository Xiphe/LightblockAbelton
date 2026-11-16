"""Offline bytecode tests of local metronome controls and stale-host isolation."""
from pathlib import Path
import shutil,struct,subprocess,tempfile,unittest
from littlefoot_harness import Harness
SOURCE=Path(__file__).resolve().parents[1]/'performance-metronome.littlefoot'

class PerformanceMetronomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=tempfile.TemporaryDirectory(prefix='performance-metro-');cls.addClassCleanup(folder.cleanup)
        binary=Path(folder.name)/'metro.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),'compile',str(SOURCE),'-o',str(binary)],check=True,capture_output=True)
        cls.program=binary.read_bytes();cls.instances=[]
    def setUp(self):
        self.vm=Harness(self.program);self.instances.append(self.vm);self.vm.call('initialise/v');self.revision=0
        self.model(0,90,0);self.model(6,0,120*128);self.model(6,1,7|8<<7|1<<14|255<<16|2<<24);self.model(4,8|896<<4,0)
    def model(self,kind,a,b,epoch=1):
        self.revision+=1;self.vm.call('handleMessage/viii',a,b,0xF2|kind<<8|epoch<<11|self.revision<<18)
    def touch(self,kind,f,x,y):self.vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)
    def state(self,name):return self.vm.call(name+'/i')['return']
    def repaint(self,ms=0):self.vm.time_ms+=ms;self.vm.call('repaint/v')
    def frames(self):
        m=[x for x in self.vm.midi if x[0]==176];result=[]
        for i in range(len(m)-5):
            a=m[i:i+6]
            if [x[1] for x in a]==[69,70,71,72,73,74] and a[1][2]==a[-1][2]:result.append((a[0][2],a[1][2],a[2][2],a[3][2]|a[4][2]<<7))
        return result
    def test_relative_detents_and_hysteresis_are_local(self):
        self.touch('Start',0,3,5);self.repaint()
        self.touch('Move',0,3,3.01);self.assertEqual(self.state('currentNumerator'),7)
        self.touch('Move',0,3,3);self.assertEqual(self.state('currentNumerator'),8)
        self.touch('Move',0,3,3.1);self.assertEqual(self.state('currentNumerator'),8)
        self.touch('Move',0,3,3.3);self.assertEqual(self.state('currentNumerator'),7)
        self.touch('End',0,3,3.3)
    def test_delayed_actual_and_old_ack_never_override_newer_meter_intent(self):
        self.touch('Start',0,3,5);self.touch('Move',0,3,3);first=self.frames()[-1]
        self.repaint(50);self.touch('Move',0,3,1);latest=self.frames()[-1]
        self.assertEqual(self.state('currentNumerator'),9)
        self.model(6,1,3|4<<7|1<<14);self.model(2,114|first[1]<<7,8)
        self.assertEqual(self.state('currentNumerator'),9)
        self.touch('End',0,3,1);self.model(2,114|latest[1]<<7,9)
        self.assertEqual(self.state('currentNumerator'),9)
    def test_matching_ack_releases_pending_and_stale_ack_does_not(self):
        self.touch('Start',0,3,5);self.touch('Move',0,3,3);first=self.frames()[-1]
        self.repaint(50);self.touch('Move',0,3,1);latest=self.frames()[-1]
        self.model(2,114|first[1]<<7,2)
        self.assertEqual(self.state('currentNumerator'),9)
        self.touch('End',0,3,1)
        self.assertEqual(self.state('currentNumerator'),9)
        self.model(2,114|latest[1]<<7,10)
        self.assertEqual(self.state('currentNumerator'),10)
        self.model(6,1,6|4<<7|1<<14|255<<16|3<<24)
        self.assertEqual(self.state('currentNumerator'),6)
        self.assertEqual(self.state('currentDenominator'),4)
    def test_stale_and_matching_ack_work_for_every_wire_sequence_after_release(self):
        for sequence in range(128):
            self.setUp();self.model(0,(sequence-1)&127,0,epoch=2)
            self.touch('Start',0,3,5);self.touch('Move',0,3,3)
            self.touch('End',0,3,3)
            self.model(2,114|((sequence-1)&127)<<7,2,epoch=2)
            self.assertEqual(self.state('currentNumerator'),8,sequence)
            self.model(2,114|sequence<<7,10,epoch=2)
            self.assertEqual(self.state('currentNumerator'),10,sequence)
    def tap(self,f,x,y):self.touch('Start',f,x,y);self.vm.time_ms+=40;self.touch('End',f,x,y)
    def test_simultaneous_independent_double_tap_returns_four_four(self):
        for repeat in range(2):
            self.touch('Start',0,3,5);self.touch('Start',1,11,5);self.vm.time_ms+=40
            self.touch('End',0,3,5);self.touch('End',1,11,5);self.vm.time_ms+=50
        self.assertEqual((self.state('currentNumerator'),self.state('currentDenominator')),(4,4))
        self.assertEqual([f[2:] for f in self.frames()[-2:]],[(114,4),(115,4)])
    def test_click_and_tap_plain_buttons_emit_absolute_and_discrete_intents(self):
        self.tap(0,3,12);self.assertEqual(self.state('currentClick'),127)
        self.assertEqual(self.frames()[-1][2:],(113,127))
        self.touch('Start',0,11,12);self.repaint();self.assertEqual(self.vm.leds[12][11],0x303030)
        self.touch('End',0,11,12);self.repaint();self.assertEqual(self.vm.leds[12][11],0x161616)
        self.assertEqual(self.frames()[-1][2:],(112,0))
    def test_raster_gap_digits_and_clamped_denominator(self):
        self.touch('Start',0,11,8);self.touch('Move',0,11,1);self.repaint()
        self.assertEqual(self.state('currentDenominator'),16)
        self.assertTrue(all(self.vm.leds[y][7]==0 for y in range(1,15)))
        self.assertGreater(sum(self.vm.leds[y][x]==0xb0b0b0 for y in range(3,8) for x in range(8,15)),0)
    def test_missing_revision_reseeds_without_canceling_meter_contact(self):
        self.touch('Start',0,3,5);self.touch('Move',0,3,3)
        self.revision+=1;self.model(6,0,140*128);self.repaint()
        self.assertIn([191,110,19],self.vm.midi)
        self.assertEqual(self.state('currentNumerator'),8)
        self.model(0,self.frames()[-1][1],0);self.model(6,1,3|4<<7|1<<14)
        self.assertEqual(self.state('currentNumerator'),8)
        self.assertEqual(self.state('currentDenominator'),4)
        self.touch('Move',0,3,1);self.assertEqual(self.state('currentNumerator'),9)
    @classmethod
    def tearDownClass(cls):
        print('performance-metronome code/functions/globals/heap:',struct.unpack_from('<5H',cls.program)[1:])
        print('VM max stack:',max(vm.max_stack_bytes for vm in cls.instances),'max instructions:',max(vm.max_instructions for vm in cls.instances))

if __name__=='__main__':unittest.main(verbosity=2)
