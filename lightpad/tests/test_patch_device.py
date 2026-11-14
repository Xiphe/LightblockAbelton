"""Execute patch-program catalog, touch and name handling as compiled bytecode."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_current_device as device
from littlefoot_harness import Harness

SOURCE=device.ROOT/'lightpad/patch.littlefoot'
FIXTURE=Path(__file__).parent/'fixtures/patch_device.json'
PROGRAM=None


def patch(vm,body,revision=0):
    device.record(vm,4,12,list(body)+[0]*(18-len(body)),revision)


def seed(vm,count=3):
    for g in range(8):device.snapshot(vm,g)
    patch(vm,[0,count])
    for index in range(count):patch(vm,[1,index,20,50,100,0,0,0,1])
    for g in range(8):patch(vm,[2,g,0,0,0,0])
    patch(vm,[4,127,127,127,127]);vm.call('repaint/v')


def names(vm,index,columns):
    columns=list(columns)+[16]
    for offset in range(0,len(columns),15):
        chunk=columns[offset:offset+15]
        patch(vm,[3,index,offset]+chunk+[16]*(15-len(chunk)))


class PatchDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PROGRAM
        if PROGRAM is None:
            with tempfile.TemporaryDirectory(prefix='patch-device-') as tmp:
                out=Path(tmp)/'patch.bin'
                subprocess.run([shutil.which('blocksctl'),'compile',str(SOURCE),'-o',str(out)],check=True,capture_output=True)
                PROGRAM=out.read_bytes()

    def new(self,count=3):
        vm=Harness(PROGRAM);vm.call('initialise/v')
        vm.call('handleMessage/viii',274,0,0xA9|(3<<8)|(12<<11))
        if count is not None:seed(vm,count)
        return vm

    def test_real_resource_limit_and_checked_protocol19_source(self):
        vm=self.new();expected=json.loads(FIXTURE.read_text())
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),expected['source_sha256'])
        self.assertEqual(hashlib.sha256(PROGRAM).hexdigest(),expected['binary_sha256'])
        self.assertEqual((vm.code_size,vm.heap_size),(expected['code'],expected['heap']))
        self.assertLessEqual(vm.code_size+vm.heap_size,7200)
        self.assertEqual(vm.midi[:1],[[191,110,19]])
        self.assertEqual(device.markers(vm),[[191,108,37]])

    def test_ready_waits_for_complete_catalog_assignments_and_ui(self):
        vm=self.new(None);patch(vm,[0,1]);patch(vm,[4,127,127,127,127])
        for g in range(8):device.snapshot(vm,g);patch(vm,[2,g,0,0,0,0])
        vm.call('repaint/v');self.assertEqual(device.markers(vm),[])
        patch(vm,[1,0,20,50,100,0,0,0,1]);vm.call('repaint/v')
        self.assertEqual(device.markers(vm),[[191,108,37]])
        for _ in range(3):vm.call('repaint/v')
        self.assertEqual(device.markers(vm),[[191,108,37]])

    def test_group_and_source_geometry_local_feedback_and_captured_touch_roles(self):
        vm=self.new(25);before=[row[:] for row in vm.leds]
        self.assertEqual(before[4],[0]*15)
        for index in range(25):
            x=(index%5)*3+1;y=5+(index//5)*2+.5
            device.touch(vm,1,0,x,y);vm.call('repaint/v')
            self.assertGreater(vm.leds[int(y)][int(x)],before[int(y)][int(x)])
            event=device.frames(vm)[-1]
            self.assertEqual((event['role'],event['view'],event['extra']),(6,3,False))
            device.touch(vm,3,0,x,y)
        device.touch(vm,1,0,1,.5);vm.call('repaint/v')
        selected=vm.leds[0][1]
        device.touch(vm,2,0,13,3.5,50);vm.call('repaint/v')
        self.assertEqual(vm.leds[0][1],selected,'moving does not retarget captured group')
        self.assertGreater(selected,before[0][1])
        # No routing action is emitted directly; all output consists of touch frames/handshake.
        self.assertTrue(all(cc in tuple(range(20,27))+(108,110) for _,cc,_ in vm.midi))

    def test_assigned_busy_and_available_levels_and_failure_red(self):
        vm=self.new();patch(vm,[2,0,1,0,0,0]);patch(vm,[1,1,20,50,100,0,2,0,1])
        device.touch(vm,1,0,1,.5);vm.call('repaint/v')
        self.assertGreater(vm.leds[5][1],vm.leds[5][7])
        self.assertGreater(vm.leds[5][7],vm.leds[5][4])
        patch(vm,[4,0,127,1,0]);vm.call('repaint/v')
        for colour in (vm.leds[0][1],vm.leds[5][4]):
            self.assertGreater((colour>>16)&255,(colour>>8)&255)
            self.assertGreater((colour>>16)&255,colour&255)

    def test_name_scroll_suppressed_by_any_group_and_restored_after_release(self):
        vm=self.new();device.touch(vm,1,1,1,5.5)
        patch(vm,[4,127,0,127,127]);names(vm,0,[15,1,2,4,8]*6)
        vm.call('repaint/v');first=[row[:] for row in vm.leds[:4]]
        self.assertTrue(any(c==0xb0b0b0 for row in first for c in row))
        vm.time_ms+=320;vm.call('repaint/v')
        self.assertNotEqual(vm.leds[:4],first)
        device.touch(vm,1,0,1,.5);vm.call('repaint/v')
        self.assertFalse(any(c==0xb0b0b0 for row in vm.leds[:4] for c in row))
        device.touch(vm,1,2,5,.5);vm.call('repaint/v')
        self.assertFalse(any(c==0xb0b0b0 for row in vm.leds[:4] for c in row))
        device.touch(vm,3,0,1,.5);device.touch(vm,3,2,5,.5);vm.call('repaint/v')
        self.assertTrue(any(c==0xb0b0b0 for row in vm.leds[:4] for c in row))

    def test_maximum_name_terminator_does_not_overwrite_atomic_receiver(self):
        vm=self.new();patch(vm,[4,127,0,127,127]);columns=[n%16 for n in range(96)]
        names(vm,0,columns)
        self.assertEqual(vm.heap_bytes(240,96),bytes(columns))
        device.touch(vm,1,0,1,5.5);vm.call('repaint/v')
        self.assertEqual(vm.call('assignment/ii',0)['return'],0)
        # Replay for a previous source cannot replace the currently named source.
        names(vm,1,[15]*10)
        self.assertEqual(vm.heap_bytes(240,96),bytes(columns))

    def test_bad_record_padding_and_native_interruption_leave_assignments_unchanged(self):
        vm=self.new();patch(vm,[2,0,3,0,0,0]);before=vm.heap_bytes(208,32)
        for body in ([2,0,127,0,0,0],[2,8,0,0,0,0],[2,0,0,0,0,16],[2,0,0,0,0,0,1]):
            patch(vm,body);self.assertEqual(vm.heap_bytes(208,32),before)
        body=[2,0,4]+[0]*15
        events=[device.word_event(4,12,n,0,body[n*8:(n+1)*8]) for n in range(3)]
        vm.call('handleMessage/viii',*events[0]);vm.call('handleMessage/viii',0,0,0)
        for event in events[1:]:vm.call('handleMessage/viii',*event)
        self.assertEqual(vm.heap_bytes(208,32),before)

    def test_real_surface_pacer_initialization_reaches_ready_and_renews_idle_ui(self):
        # Replay actual compact wire delivery, not patch_wire's insertion order.
        # This catches records rejected before metadata that are then never resent.
        sys.path.insert(0,str(device.ROOT/'live/tests'))
        sys.path.insert(0,str(device.ROOT/'live'))
        import test_native_clock_listener as listener
        import test_surface_pacing as pacing
        from LightpadController import protocol as p
        helper=listener.NativeClockListenerTests()
        surface=helper.make();self.addCleanup(helper.doCleanups)
        class Engine(pacing.EightClockEngine):
            def patch_state(self):
                return {'sources':tuple({'name':'INPUT '+str(n),'colour':(20,50,100),
                    'midi':False,'owners':0,'available':True} for n in range(25)),
                    'assignments':(0,)*8,'monitor':None}
        surface._engine=Engine()
        vm=self.new(None);partials={};offset=0;ready_at=None
        def replay():
            nonlocal offset
            for status,cc,value in helper.instance.sent[offset:]:
                if cc in (33,36):
                    partials[status]=[cc,value,[]];continue
                pending=partials.get(status)
                if pending is None:continue
                start,revision,body=pending;length=15 if start==33 else 18
                if cc==40+len(body) and len(body)<length:
                    body.append(value)
                elif cc==40+length and len(body)==length and value==revision:
                    if start==36:device.record(vm,4,12,body,revision)
                    elif status<0xb8:device.record(vm,0,status-0xb0,body,revision)
                    partials.pop(status,None)
                else:partials.pop(status,None)
            offset=len(helper.instance.sent)
        surface.receive_midi((0xbf,105,2));surface.receive_midi((0xbf,110,p.VERSION))
        for step in range(301):
            helper.now=100+step*.02;vm.time_ms=1000+step*20
            surface.update_display();replay();vm.call('repaint/v')
            if device.markers(vm) and ready_at is None:ready_at=step*.02
        self.assertIsNotNone(ready_at,'actual pacer delivery must initialize the compiled selector')
        self.assertLess(ready_at,3.)
        self.assertEqual(device.markers(vm),[[191,108,37]],'unchanged idle UI heartbeats must retain readiness')
        self.assertTrue(any(c for row in vm.leds for c in row))

    def test_expired_model_blocks_held_contact_until_release_and_reseeds(self):
        vm=self.new();device.touch(vm,1,0,1,5.5)
        vm.time_ms+=3000;vm.call('repaint/v')
        self.assertTrue(all(c==0 for row in vm.leds for c in row))
        self.assertEqual(vm.midi[-1],[191,110,19])
        vm.call('handleMessage/viii',274,0,0xA9|(3<<8)|(12<<11));seed(vm)
        before=len(device.frames(vm));device.touch(vm,3,0,1,5.5)
        self.assertEqual(len(device.frames(vm)),before)
        device.touch(vm,1,0,1,5.5)
        self.assertEqual(len(device.frames(vm)),before+1)


if __name__=='__main__':unittest.main()
