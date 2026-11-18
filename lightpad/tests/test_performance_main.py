"""Execute compiled Audio performance client with fake clock/LED/MIDI natives.

No hardware or Live is touched. VM proof is not a firmware latency measurement.
"""
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from littlefoot_harness import Harness

SOURCE=Path(__file__).resolve().parents[1]/'performance-main.littlefoot'

def read_state(vm,name,source=SOURCE):
    """Read canonical VM memory without compiling a different diagnostic program."""
    names=re.findall(r'^(?:int|float|bool) (\w+);',Path(source).read_text().split('int finger(')[0],re.M)
    if len(names)!=vm.num_globals:raise AssertionError('Compiler/source global layout changed')
    aliases={'currentView':'view','currentMute':'muted','currentBatch':'batch','currentGuide':'guide','currentToken':'token'}
    selected=struct.unpack_from('<i',vm.memory,vm.stack_end+names.index('selected')*4)[0]
    if name=='currentValue':return vm.memory[vm.heap_start+selected*6]&127
    return struct.unpack_from('<i',vm.memory,vm.stack_end+names.index(aliases.get(name,name))*4)[0]

class PerformanceMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory=tempfile.TemporaryDirectory(prefix='performance-main-');cls.addClassCleanup(directory.cleanup)
        binary=Path(directory.name)/'main.bin'
        subprocess.run([shutil.which('blocksctl') or str(Path.home()/'.local/bin/blocksctl'),'compile',str(SOURCE),'-o',str(binary)],check=True,capture_output=True)
        cls.program=binary.read_bytes();cls.instances=[]
    def setUp(self):
        self.vm=Harness(self.program);self.instances.append(self.vm);self.vm.call('initialise/v');self.revision=0
        self.model(0,127,0)
        for key in range(48):self.model(1,key,64 if key%6<2 else 0)
        for group in range(8):self.model(3,group,100|70<<7|50<<14|1<<21|1<<22)
        self.model(6,0,120*128);self.model(6,1,4|4<<7|1<<14|255<<16)
        self.model(4,8|1024<<4,0)
    def model(self,kind,a,b,epoch=1,revision=None):
        self.revision=(self.revision+1)&8191 if revision is None else revision
        self.vm.call('handleMessage/viii',a,b,0xF2|kind<<8|epoch<<11|self.revision<<18)
    def state(self,name):return read_state(self.vm,name)
    def touch(self,kind,f,x,y):self.vm.call('touch'+kind+'/viffff',f,x/7.5,y/7.5,1.,0.)
    def repaint(self,ms=0):self.vm.time_ms+=ms;return self.vm.call('repaint/v')
    def frames(self):
        messages=[m for m in self.vm.midi if m[0]==176]
        frames=[]
        for i in range(len(messages)-5):
            unit=messages[i:i+6]
            if [m[1] for m in unit]==[69,70,71,72,73,74] and unit[1][2]==unit[-1][2]:
                frames.append((unit[0][2],unit[1][2],unit[2][2],unit[3][2]|unit[4][2]<<7))
        return frames
    def open(self,group=0):
        x=group%4*4+1;y=group//4*4+1
        self.touch('Start',0,x,y);self.repaint(500);self.assertEqual(self.state('currentView'),1)
    def test_compiled_budget_and_local_mixer_all_groups(self):
        _,code,_,globals_,heap=struct.unpack_from('<5H',self.program)
        self.assertLessEqual(code+heap,7200)
        self.assertLess(globals_*4,800)
        for group in range(8):
            self.open(group);self.touch('Start',1,1,12);self.touch('Move',1,1,9);self.repaint()
            self.assertGreater(self.state('currentValue'),64)
            self.assertEqual(self.frames()[-1][2],group*6)
            self.touch('End',1,1,9);self.touch('End',0,group%4*4+1,group//4*4+1)
            self.assertEqual(self.state('currentView'),0)
    def test_stale_ack_and_observation_never_rewind_held_or_newer_drag(self):
        self.open();self.touch('Start',1,1,12);self.touch('Move',1,1,10)
        first=self.frames()[-1];self.repaint(45);self.touch('Move',1,1,9);latest=self.frames()[-1]
        value=self.state('currentValue');self.model(2,first[2]|first[1]<<7,2);self.model(1,0,4)
        self.assertEqual(self.state('currentValue'),value)
        self.touch('End',1,1,9);self.touch('End',0,1,1)
        self.assertEqual(self.state('currentValue'),value)
        self.model(2,latest[2]|latest[1]<<7,value)
        self.assertEqual(self.state('currentValue'),value)
    def test_chord_pending_preview_toggle_and_single_commit_on_last_release(self):
        self.touch('Start',0,1,1);self.touch('Start',1,5,1);self.repaint(700)
        self.assertEqual(self.state('currentView'),0);self.assertEqual(self.state('currentBatch'),3)
        self.touch('End',0,1,1);self.assertEqual(self.frames(),[])
        self.touch('Start',2,1,1);self.touch('End',2,1,1)
        self.assertEqual(self.state('currentBatch'),2)
        self.touch('Start',2,1,1);self.touch('End',2,1,1)
        self.assertEqual(self.state('currentBatch'),3)
        self.touch('End',1,5,1);self.assertEqual(self.frames()[-1][2:],(56,3));self.assertEqual(self.state('currentMute'),3)
    def test_chord_original_anchor_contact_id_can_be_reused_before_last_release(self):
        self.touch('Start',0,1,1);self.touch('Start',1,5,1)
        self.assertEqual(self.state('currentBatch'),3)
        # Reusing the original anchor's ID is a new contact, not a reset of the chord.
        self.touch('End',0,1,1);self.touch('Start',0,1,1);self.touch('End',0,1,1)
        self.assertEqual(self.frames(),[])
        self.assertEqual(self.state('currentBatch'),2)
        self.repaint(600)
        self.assertEqual(self.state('currentView'),0)
        self.touch('Start',0,1,1);self.touch('End',0,1,1)
        self.assertEqual(self.frames(),[])
        self.assertEqual(self.state('currentBatch'),3)
        self.touch('End',1,5,1)
        self.assertEqual([frame[2:] for frame in self.frames()],[(56,3)])
        self.assertEqual(self.state('currentMute'),3)
    def test_cancel_pending_next_suppresses_racing_following_fact(self):
        token=self.begin();self.take(token,3,1)
        self.touch('Start',1,10,10);self.touch('End',1,10,10)
        following=self.frames()[-1][3]>>7
        self.touch('Start',1,3,3);self.touch('End',1,3,3)
        self.assertEqual(self.frames()[-1][2:],(68,token))
        self.assertEqual(self.state('currentView'),0)
        self.take(token,3,1)
        self.assertEqual(self.state('currentView'),0)
        self.take(following,3,0)
        self.assertEqual(self.state('currentView'),0)
    def test_cancel_pending_retry_suppresses_racing_replacement_fact(self):
        token=self.begin();self.take(token,3,1)
        self.touch('Start',1,3,10);self.touch('End',1,3,10)
        replacement=self.frames()[-1][3]>>7
        self.touch('Start',1,3,3);self.touch('End',1,3,3)
        self.assertEqual(self.frames()[-1][2:],(68,token))
        self.assertEqual(self.state('currentView'),0)
        self.take(token,3,1)
        self.assertEqual(self.state('currentView'),0)
        self.take(replacement,3,0)
        self.assertEqual(self.state('currentView'),0)
    def test_rejected_retry_after_next_recovers_actual_following_without_old_fact_rewind(self):
        old=self.begin();self.take(old,3,1)
        self.touch('Start',1,10,10);self.touch('End',1,10,10)
        following=self.frames()[-1][3]>>7
        self.touch('Start',1,3,10);self.touch('End',1,3,10)
        rejected=self.frames()[-1]
        self.model(2,rejected[2]|rejected[1]<<7,16383)
        self.assertEqual(read_state(self.vm,'recPhase'),1)
        self.assertEqual(read_state(self.vm,'nextToken'),-1)
        self.take(following,3,0)
        self.assertEqual(self.state('currentToken'),following)
        self.assertEqual(read_state(self.vm,'recPhase'),3)
        self.take(old,3,4)
        self.assertEqual(self.state('currentToken'),following)
        self.assertEqual(read_state(self.vm,'recPhase'),3)
        self.touch('Start',1,3,3);self.touch('End',1,3,3)
        self.take(following,3,1)
        self.assertEqual(self.state('currentView'),0)
    def test_fresh_begin_does_not_recover_a_stale_other_token(self):
        old=self.begin();self.take(old,3,1);self.take(old,0,0)
        current=self.begin()
        self.take(old,3,4)
        self.assertEqual(self.state('currentToken'),current)
        self.assertEqual(read_state(self.vm,'recPhase'),1)
        self.take(current,3,0)
        self.assertEqual(self.state('currentToken'),current)
        self.assertEqual(read_state(self.vm,'recPhase'),3)
    def test_reversible_down_L_I_back_has_no_command(self):
        self.touch('Start',0,1,1);self.touch('Move',0,1,5);self.assertEqual(self.state('currentGuide'),1)
        self.touch('Move',0,5,5);self.assertEqual(self.state('currentGuide'),2)
        self.touch('Move',0,1,5);self.assertEqual(self.state('currentGuide'),1)
        self.touch('Move',0,1,1);self.assertEqual(self.state('currentGuide'),0)
        self.touch('End',0,1,1);self.assertEqual(self.frames(),[])
    def begin(self):
        self.touch('Start',0,1,1);self.touch('Move',0,1,5);self.touch('End',0,1,5)
        self.assertEqual(self.state('currentView'),2);return self.state('currentToken')
    def take(self,token,phase,measurement,bar=4):self.model(5,token<<3|phase<<10|int(bar*16)<<13,int(measurement*256))
    def test_local_record_view_and_countdown_uses_actual_target(self):
        token=self.begin();self.repaint();self.assertEqual(self.frames()[-1][2:],(64,token))
        self.take(token,2,36);self.repaint();self.assertTrue(any(self.vm.leds[13-block][7] for block in range(1)))
        self.take(token,3,0);self.repaint();self.assertGreater(self.vm.leds[0][0],0)
        self.take(token,3,4);self.repaint();self.repaint(201);self.assertEqual(self.vm.leds[14][14],0xb0b0b0)
        self.take(token,0,0);self.assertEqual(self.state('currentView'),0)
    def test_accept_disabled_during_countdown_and_cancel_is_local(self):
        token=self.begin();self.take(token,2,4)
        before=len(self.frames());self.touch('Start',1,10,3);self.touch('End',1,10,3)
        self.assertEqual(len(self.frames()),before)
        self.touch('Start',1,3,3);self.touch('End',1,3,3)
        self.assertEqual(self.state('currentView'),0);self.assertEqual(self.frames()[-1][2:],(68,token))
        self.take(token,3,1);self.assertEqual(self.state('currentView'),0)
    def test_seed_resumes_sequence_without_rewinding_live_gesture(self):
        self.model(0,100,0,epoch=2,revision=0)
        self.open();self.touch('Start',1,1,12);self.touch('Move',1,1,10)
        first=self.frames()[-1];self.assertEqual(first[0:2],(2,101))
        value=self.state('currentValue');self.model(0,1,0,epoch=2)
        self.assertEqual(self.state('currentView'),1);self.assertEqual(self.state('currentValue'),value)
        self.assertEqual(self.frames()[-1][1],102)
        self.model(2,first[2]|first[1]<<7,0,epoch=1)
        self.assertEqual(self.state('currentValue'),value)
    def test_terminal_begin_rejection_recovers_and_late_error_does_not_hide_actual_capture(self):
        token=self.begin();frame=self.frames()[-1]
        self.model(7,frame[2]|frame[1]<<7,1)
        self.assertEqual(self.state('currentView'),0)
        token=self.begin();frame=self.frames()[-1];self.take(token,3,0)
        self.model(7,frame[2]|frame[1]<<7,1)
        self.assertEqual(self.state('currentView'),2)
    def test_accept_next_adopts_replacement_without_old_idle_fact(self):
        token=self.begin();self.take(token,3,1)
        self.touch('Start',1,10,10);self.touch('End',1,10,10)
        command=self.frames()[-1];new=command[3]>>7
        self.assertEqual(command[2],67);self.assertEqual(command[3]&127,token)
        self.take(new,2,8)
        self.assertEqual(self.state('currentToken'),new);self.assertEqual(self.state('currentView'),2)
        self.take(token,0,0)
        self.assertEqual(self.state('currentView'),2)
    def test_completed_parameter_ack_cannot_override_later_external_observation(self):
        self.open();self.touch('Start',1,1,12);self.touch('Move',1,1,10);frame=self.frames()[-1]
        self.touch('End',1,1,10);self.touch('End',0,1,1)
        self.model(2,frame[2]|frame[1]<<7,100);self.model(1,0,20)
        self.assertEqual(self.state('currentValue'),20)
        self.model(2,frame[2]|frame[1]<<7,100)
        self.assertEqual(self.state('currentValue'),20)
    def clear(self):
        self.touch('Start',0,1,1);self.touch('Move',0,1,5);self.touch('Move',0,5,5);self.touch('End',0,5,5)
        frame=self.frames()[-1];self.assertEqual(frame[2:],(104,0));return frame
    def test_clear_empty_hint_survives_ack_old_facts_then_bounded_reconciliation(self):
        self.repaint();before=self.vm.leds[1][1]
        frame=self.clear();self.repaint();empty=self.vm.leds[1][1];self.assertLess(empty,before)
        self.model(2,104|frame[1]<<7,0)
        self.model(3,0,100|70<<7|50<<14|1<<21|1<<22);self.repaint()
        self.assertEqual(self.vm.leds[1][1],empty)
        self.repaint(2001);self.assertEqual(self.vm.leds[1][1],before)
    def test_clear_actual_empty_fact_ends_hint_and_later_manual_playback_wins(self):
        self.clear();self.model(3,0,100|70<<7|50<<14|1<<21);self.repaint();empty=self.vm.leds[1][1]
        self.model(3,0,100|70<<7|50<<14|1<<21|1<<22);self.repaint()
        self.assertGreater(self.vm.leds[1][1],empty)
    def test_clear_rejection_restores_confirmed_populated_display(self):
        self.repaint();before=self.vm.leds[1][1];frame=self.clear()
        self.model(7,104|frame[1]<<7,1);self.repaint(251)
        self.assertEqual(self.vm.leds[1][1],before)
    def test_all_mixer_controls_are_relative_and_flush_on_anchor_release(self):
        self.open()
        for x,key in ((1,0),(4,2),(7,3),(10,4),(13,5)):
            before=len(self.frames());self.touch('Start',1,x,12)
            self.assertEqual(len(self.frames()),before)
            self.touch('Move',1,x,10);self.touch('End',1,x,10)
            self.assertEqual(self.frames()[-1][2],key)
        self.touch('Start',1,7,5);self.touch('Move',1,11,5);self.touch('End',1,11,5)
        self.assertEqual(self.frames()[-1][2],1)
        self.touch('Start',1,1,12);self.touch('Move',1,1,11.9)
        self.touch('End',0,1,1);last=len(self.frames())
        self.assertEqual(self.state('currentView'),0)
        self.touch('End',1,1,11.9);self.assertEqual(len(self.frames()),last)
    def test_external_parameter_waits_for_contact_release_without_local_change(self):
        self.open();self.touch('Start',1,1,12);self.model(1,0,20)
        self.assertEqual(self.state('currentValue'),64)
        self.touch('End',1,1,12)
        self.assertEqual(self.state('currentValue'),20)
    def test_recording_pages_and_actual_bar_pulse_do_not_use_group_loop_length(self):
        token=self.begin();self.model(4,0|71680<<4,0)
        self.take(token,3,32);self.repaint()
        self.assertEqual(self.vm.leds[13][7],0xb0b0b0)
        self.take(token,3,64);self.repaint();self.repaint(201)
        self.assertEqual(self.vm.leds[12][7],0xb0b0b0)
        self.take(token,2,40);self.repaint()
        self.assertEqual(read_state(self.vm,'markerCount'),8)
        self.repaint(500)
        self.assertEqual(read_state(self.vm,'markerCount'),8)
    def test_global_bar_flash_is_independent_of_take_local_phase(self):
        token=self.begin();self.take(token,3,1)
        # Global clock reaches a real bar independently of take-local phases.
        self.model(4,8|1024<<4,1000);self.repaint()
        self.repaint(60)
        self.assertTrue(any(o['rect']==[0,0,15,1] and o['alpha']>0 for o in self.vm.overlays))
    def test_black_clear_preview_and_trace_are_above_groups(self):
        self.touch('Start',0,1,1);self.touch('Move',0,1,5);self.touch('Move',0,5,5);self.repaint()
        self.assertEqual(self.vm.leds[1][1],0xffffff)
        self.assertEqual(self.vm.leds[1][0],0)
        self.assertEqual(self.vm.leds[5][5],0xffffff)
    def test_unavailable_parameter_is_distinct_from_zero_and_cannot_be_dragged(self):
        self.model(1,2,128);self.open();before=len(self.frames())
        self.touch('Start',1,4,12);self.touch('Move',1,4,8);self.touch('End',1,4,8)
        self.assertEqual(len(self.frames()),before)
        self.assertEqual(self.vm.memory[self.vm.heap_start+2],0)
        self.assertEqual(self.vm.memory[self.vm.heap_start+72+2],128)
    def test_parameter_removed_during_owned_drag_recovers_to_zero_after_rejection(self):
        self.open();self.touch('Start',1,1,12);self.touch('Move',1,1,10);frame=self.frames()[-1]
        self.model(1,0,128);self.assertGreater(self.state('currentValue'),0)
        self.model(2,frame[2]|frame[1]<<7,16383);self.touch('End',1,1,10)
        self.assertEqual(self.state('currentValue'),0)
    def test_missing_model_revision_requests_bounded_reseed_without_closing_mixer(self):
        self.open();self.touch('Start',1,1,12);self.touch('Move',1,1,10);wanted=self.state('currentValue')
        self.revision+=1  # A parameter fact was lost before this following packet.
        self.model(3,1,100|70<<7|50<<14|1<<21|1<<22)
        self.repaint()
        hello=lambda:len([m for m in self.vm.midi if m==[191,110,19]])
        self.assertEqual(hello(),1);self.assertEqual(self.state('currentView'),1)
        self.assertEqual(self.state('currentValue'),wanted)
        self.revision+=1;self.model(1,7,40);self.repaint(100)
        self.assertEqual(hello(),1)
        self.repaint(901);self.assertEqual(hello(),2)
        # Same-generation cache reseed repairs a previously missing pan field.
        self.model(0,self.frames()[-1][1],0);self.model(1,1,100);self.model(1,0,20)
        self.assertEqual(self.vm.memory[self.vm.heap_start+1],100)
        self.assertEqual(self.state('currentView'),1);self.assertEqual(self.state('currentValue'),wanted)
    def replacement_failure(self, x, y):
        token=self.begin();self.take(token,3,1)
        self.touch('Start',1,x,y);self.touch('End',1,x,y)
        command=self.frames()[-1];following=command[3]>>7
        self.model(2,command[2]|command[1]<<7,0)
        # A different token's failure must not complete this promise.
        self.take((following+1)&127,5,0)
        self.assertEqual(self.state('currentView'),2)
        # No intermediate preparing/countdown/recording fact was received.
        self.take(following,5,0)
        self.assertEqual(self.state('currentView'),0)
        self.assertEqual(self.state('currentToken'),following)
        self.assertIn([191,107,0],self.vm.midi)
    def test_failed_promised_next_returns_without_intermediate_following_fact(self):
        self.replacement_failure(10,10)
    def test_failed_promised_retry_returns_without_intermediate_replacement_fact(self):
        self.replacement_failure(3,10)
    def test_no_source_down_is_local_yellow_X_but_L_still_clears(self):
        self.model(6,1,4|4<<7|1<<14|254<<16)
        self.touch('Start',0,1,1);self.touch('Move',0,1,5);self.repaint()
        self.assertEqual(self.vm.leds[0][0],0xff9118)
        self.assertEqual(self.vm.leds[1][1],0xff9118)
        self.assertNotIn(0xffffff,[pixel for row in self.vm.leds for pixel in row])
        self.touch('End',0,1,5)
        self.assertEqual(self.frames(),[]);self.assertEqual(self.state('currentView'),0)
        self.clear()
        self.assertEqual(self.frames()[-1][2:],(104,0))
    def test_horizontal_monitor_preview_and_pending_survive_stale_ack(self):
        def swipe(group):
            x=group%4*4+1;y=group//4*4+1
            self.touch('Start',0,x,y);self.touch('Move',0,x+4,y);self.repaint()
            self.assertEqual(self.vm.leds[int(y+2)][int(x)],0x2080ff)
            self.touch('End',0,x+4,y)
            return self.frames()[-1]
        first=swipe(0);latest=swipe(1)
        self.assertEqual(first[2:],(57,0));self.assertEqual(latest[2:],(57,1))
        self.model(6,1,4|4<<7|1<<14|255<<16|1<<24)
        self.model(2,57|first[1]<<7,0);self.repaint()
        self.assertEqual(self.vm.leds[3][5],0x2080ff)
        self.model(2,57|latest[1]<<7,1);self.repaint()
        self.assertEqual(self.vm.leds[3][5],0x2080ff)
        self.model(6,1,4|4<<7|1<<14|255<<16|3<<24);self.repaint()
        self.assertEqual(self.vm.leds[3][9],0x2080ff)
    def test_mute_pending_dot_has_priority_over_monitor_dot(self):
        self.model(6,1,4|4<<7|1<<14|255<<16|1<<24)
        self.touch('Start',0,1,1);self.touch('Start',1,5,1);self.repaint()
        self.assertEqual(self.vm.leds[3][1],0xb0b0b0)
        self.assertEqual(self.vm.leds[3][5],0xb0b0b0)
    @classmethod
    def tearDownClass(cls):
        print('performance-main code/heap/globals:',struct.unpack_from('<5H',cls.program)[1:])
        print('VM max stack:',max(vm.max_stack_bytes for vm in cls.instances),'max instructions:',max(vm.max_instructions for vm in cls.instances))

if __name__=='__main__':unittest.main(verbosity=2)
