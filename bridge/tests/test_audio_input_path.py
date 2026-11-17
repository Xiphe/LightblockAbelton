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
import test_audio_performance_path as baseline


class AudioInputPathTests(unittest.TestCase):
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
        self.fixture=native_fixture.AudioInputChecks()
        self.backend=self.fixture.make(initial=1,groups=8);self.song=self.fixture.song
        self.addCleanup(self.fixture.doCleanups)
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

    def advance(self,ms):
        self.vm.time_ms+=ms;self.fixture.clock=self.vm.time_ms/1000.
        self.song.advance(1+(self.vm.time_ms-1000)/500.)
        self.backend.tick();self.facts();self.pump();self.vm.call('repaint/v');self.pump()

    def switch(self,name):
        now=self.vm.time_ms;self.vm=Harness(self.programs[name]);self.vm.time_ms=now
        self.vm.call('initialise/v');self.midi_cursor=0
        self.exchange('patch-names %d'%(name=='patch'))
        self.exchange('reseed %d'%now);self.pump();self.advance(100)

    def patch_mask(self):return self.vm.heap_bytes(32,1)[0]
    def patch_pending(self):return bool(self.vm.call('pending/bi',0)['return'])
    def add_sm58(self):
        self.touch('Start',0,1,1);self.touch('Start',1,4,5)
        self.assertEqual(self.patch_mask(),3)
        self.assertEqual(self.backend.audio_inputs.desired[0],1)
        self.touch('End',1,4,5);self.touch('End',0,1,1)

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


if __name__=='__main__':unittest.main(verbosity=2)
