"""Protocol19 Constants, monitor and source-preview behavior on compiled code."""
import tempfile
from pathlib import Path
import unittest
import test_current_device as device


class ConstantsDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not device.PROGRAMS:
            folder=tempfile.TemporaryDirectory(prefix='constants-device-')
            cls.addClassCleanup(folder.cleanup)
            device.PROGRAMS.update(device.compile_programs(Path(folder.name)))

    def ready(self):
        vm=device.ready('main')
        for group in range(8,12):device.snapshot(vm,group,[1,0,0,100,30,20,127,127,0,0,64,16,0,0,0],1)
        return vm

    def test_constants_have_two_rows_and_captured_mixer_bank(self):
        vm=self.ready();vm.call('repaint/v')
        for group in range(8,12):
            x=(group-8)*4
            self.assertTrue(all(vm.leds[y][x+n]!=0 for y in (8,9) for n in range(3)))
            self.assertEqual(vm.leds[10][x],0)
            device.touch(vm,1,0,x+1,8.5);self.assertEqual(device.frames(vm)[-1]['role'],6)
            device.touch(vm,3,0,x+1,8.5)
        device.presentation(vm,mode=1,selected=11,revision=1)
        device.touch(vm,1,1,1,3)
        event=device.frames(vm)[-1]
        self.assertEqual((event['view'],event['group'],event['opened']),(1,3,True))
        self.assertTrue(all(0<=v<128 for _,_,v in vm.midi))
        vm.call('repaint/v')
        self.assertNotEqual(vm.leds[0][4],0)
        self.assertEqual(vm.leds[7],[0]*15)
        self.assertEqual(vm.leds[10],[0]*15)
        self.assertEqual(vm.leds[11],[0]*15)
        self.assertNotEqual(vm.leds[12][7],0)

    def test_monitor_dot_and_white_pending_priority_include_constants(self):
        vm=self.ready();device.presentation(vm,body=[0,0,127,0,0,0,0,6],revision=1)
        vm.call('repaint/v');self.assertEqual(vm.leds[7][9],0x3080ff)
        constant_before=vm.leds[8][5]
        device.presentation(vm,body=[0,0,127,0,0,64,4,6],revision=2)
        vm.call('repaint/v');self.assertEqual(vm.leds[7][9],0xb0b0b0)
        self.assertEqual(vm.leds[10][5],0xb0b0b0)
        self.assertLess(vm.leds[8][5],constant_before, 'pending Constants show their future mute state')
        # Invalid monitor cannot commit any of the new presentation.
        device.presentation(vm,body=[1,11,127,0,0,0,0,8],revision=3)
        vm.call('repaint/v');self.assertEqual(vm.leds[10][5],0xb0b0b0)

    def test_no_source_preview_has_yellow_x_without_trace_and_l_still_clears(self):
        vm=self.ready();device.touch(vm,1,x=1,y=1)
        device.touch(vm,2,x=1,y=5)
        device.presentation(vm,flags=32,revision=1);vm.call('repaint/v')
        self.assertEqual(vm.leds[0][0],0xffff00)
        self.assertEqual(vm.leds[1][1],0xffff00)
        self.assertEqual(vm.leds[3][1],0, 'no painted guide outside the selected group')
        device.touch(vm,2,x=4,y=5)
        device.presentation(vm,flags=8,revision=2);vm.call('repaint/v')
        self.assertEqual(vm.leds[0][0],0)
        self.assertEqual(vm.leds[5][1],0xffffff)
        device.presentation(vm,flags=32,revision=3);vm.call('repaint/v')
        self.assertEqual(vm.leds[0][0],0xffff00)
        self.assertNotEqual(vm.leds[5][1],0xffffff)

    def test_clock_21bit_decode_and_unchanged_clock_and_snapshot_expiry(self):
        vm=self.ready()
        for revision,raw in enumerate((127,128,255,16383,16384,1048575),1):
            body=device.clock_payload(0,span=8191,position=0)
            body[5:8]=[raw>>shift&127 for shift in (0,7,14)]
            body[2:5]=[127,127,127]
            device.clock(vm,0,body,revision=revision)
            self.assertEqual(vm.call('clockNumber/iii',0,5)['return'],raw)
        device.clock(vm,0,revision=126)
        self.assertEqual(vm.call('clockAge/ii',0)['return'],0)
        vm.time_ms+=1000
        self.assertEqual(vm.call('clockAge/ii',0)['return'],1000)
        self.assertTrue(vm.call('available/bi',11)['return'])
        vm.time_ms+=2000
        self.assertFalse(vm.call('available/bi',11)['return'])


if __name__=='__main__':unittest.main()
