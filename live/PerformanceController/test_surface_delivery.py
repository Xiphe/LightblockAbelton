"""Actual Surface/Backend publication ordering around slow fake Native work.

The injected duration advances deterministic clocks rather than sleeping. This
locks known-fact delivery order and the six-frame budget, not Live timing.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
import test_surface as surface_fixture


class SurfaceDeliveryChecks(unittest.TestCase):
    def setUp(self):
        change=patch.object(surface_fixture.adapter.config,'ALLOW_AUDIO_TRACK_CREATION',True)
        change.start();self.addCleanup(change.stop)
        self.fixture=surface_fixture.SurfaceChecks()
        self.surface=self.fixture.make();self.fixture.handshake()
        self.addCleanup(self.fixture.doCleanups)
        self.backend=self.surface.backend;self.song=self.fixture.song
        self.clock=0.
        self.backend.now=lambda:self.clock
        self.backend.recording.monotonic=lambda:self.clock

    def known_start_before_creation(self,seed_backlog=False):
        backend=self.backend
        for unused in range(16):
            self.clock+=.101;backend.tick()
        self.assertEqual(backend.dispatch(64,1),0)
        take=backend.recording.take;self.assertIsNotNone(take)
        self.song.advance(take.target-.5);self.clock+=.101;backend.tick()
        self.song.advance(take.target);self.clock+=.101
        self.assertTrue(take.slot.clip.is_recording)
        backend.drain_facts(200)
        if seed_backlog:backend.reseed()
        backend._slow_at=backend._take_at=backend._clock_at=backend._refclock_at=-10.
        frames=[];current=[];native=[]
        send=self.surface._send_midi
        def capture(message,optimized=False):
            if message[0]==0xb0:
                current.append(message)
                if message[1]==100:
                    self.assertEqual([m[1] for m in current],list(range(89,101)))
                    a=sum(current[3+i][2]<<(7*i) for i in range(4))
                    b=sum(current[7+i][2]<<(7*i) for i in range(4))
                    frames.append((self.clock,current[2][2],a,b));current.clear()
            send(message,optimized=optimized)
        self.surface._send_midi=capture
        create=self.song.create_audio_track
        def slow_create(index):
            native.append(('enter',self.clock))
            self.clock+=1.963;self.song.advance(self.song.current_song_time+3.926)
            create(index);native.append(('return',self.clock))
        self.song.create_audio_track=slow_create
        self.surface.update_display()
        self.assertEqual([event for event,at in native],['enter','return'])
        actual=[frame for frame in frames if frame[1]==5 and ((frame[2]>>10)&7)==3]
        self.assertTrue(actual,'actual recording phase must reach the MIDI callback')
        self.assertEqual((actual[0][2]>>3)&127,1)
        self.assertLessEqual(actual[0][0],native[0][1],
                             'known recording start waited behind Native maintenance')
        self.assertEqual(actual[0][3],0,'publish the known start before the fake block advances time')
        global_clock=[frame for frame in frames if frame[1]==4 and frame[2]&15==8]
        self.assertTrue(global_clock,'the real global bar clock must share critical delivery')
        self.assertLessEqual(global_clock[0][0],native[0][1],
                             'known global bar clock waited behind Native maintenance')
        self.assertEqual(global_clock[0][3],0,'publish the current real bar phase before the block')
        self.assertLessEqual(len(frames),6,'pre/post publication must share one display budget')
        self.assertFalse(current)

    def test_known_start_is_published_before_slow_audio_creation(self):
        self.known_start_before_creation()

    def test_seed_backlog_does_not_hide_start_or_double_display_budget(self):
        self.known_start_before_creation(seed_backlog=True)


if __name__=='__main__':unittest.main(verbosity=2)
