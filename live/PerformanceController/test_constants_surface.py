"""Second-bank commands reach Native targets and keep ACK identity separate."""
from pathlib import Path
import sys,unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
import test_surface as wire
from test_constants import add_constants


class ConstantsSurfaceChecks(unittest.TestCase):
    def make(self):
        fixture=wire.SurfaceChecks();self.addCleanup(fixture.doCleanups)
        surface=fixture.make();root,children=add_constants(fixture.song)
        surface.backend.state.discover();fixture.handshake()
        self.fixture,self.surface,self.children=fixture,surface,children
        return surface

    def test_second_bank_command_and_ack_keep_channel_and_low_key(self):
        surface=self.make();self.fixture.song.track_reads=0
        old=surface.backend.state.parameters[0][0].value
        self.fixture.command(key=128,value=77)
        self.assertEqual(self.children[0].mixer_device.volume.value,77/127.)
        self.assertEqual(surface.backend.state.parameters[0][0].value,old)
        self.assertEqual(self.fixture.instance.sent[-6:],[
            (0xB1,69,4),(0xB1,75,1),(0xB1,76,0),
            (0xB1,77,77),(0xB1,78,0),(0xB1,79,1)])
        self.assertEqual(self.fixture.song.track_reads,0)

    def test_interleaved_banks_keep_independent_partial_frames(self):
        surface=self.make()
        messages=[]
        first=((69,4),(70,1),(71,0),(72,50),(73,0),(74,1))
        second=((69,4),(70,2),(71,0),(72,90),(73,0),(74,2))
        for a,b in zip(first,second):
            messages.extend(((0xB0,*a),(0xB1,*b)))
        for message in messages:surface.receive_midi(message)
        self.assertEqual(surface.backend.state.parameters[0][0].value,50/127.)
        self.assertEqual(self.children[0].mixer_device.volume.value,90/127.)
        self.assertIsNone(surface._frame);self.assertIsNone(surface._constant_frame)

    def test_a_cross_bank_fragment_cannot_complete_another_banks_command(self):
        surface=self.make();old=surface.backend.state.parameters[0][0].value
        surface.receive_midi((0xB0,69,4))
        for cc,value in ((70,1),(71,0),(72,20),(73,0),(74,1)):
            surface.receive_midi((0xB1,cc,value))
        self.assertEqual(surface.backend.state.parameters[0][0].value,old)
        self.assertEqual(self.children[0].mixer_device.volume.value,.75)
        self.assertEqual(self.fixture.instance.sent,[(0xBF,79,4)])

    def test_second_bank_duplicate_is_acked_without_replay_or_cross_bank_alias(self):
        surface=self.make()
        with patch.object(surface.backend,'dispatch',wraps=surface.backend.dispatch) as dispatch:
            self.fixture.command(key=128,value=77)
            self.fixture.command(key=128,value=77)
            self.assertEqual(dispatch.call_count,1)
            self.fixture.command(key=0,value=50)
            self.assertEqual(dispatch.call_count,1)
        self.assertEqual(self.fixture.instance.sent[-6:],[
            (0xB0,69,4),(0xB0,75,1),(0xB0,76,0),
            (0xB0,77,127),(0xB0,78,127),(0xB0,79,1)])

    def test_unused_second_bank_key_rejects_on_its_own_channel(self):
        surface=self.make();self.fixture.command(key=152,value=50)
        self.assertEqual(self.fixture.instance.sent[-6:],[
            (0xB1,69,4),(0xB1,75,1),(0xB1,76,24),
            (0xB1,77,127),(0xB1,78,127),(0xB1,79,1)])

    def test_transport_reset_clears_both_partial_banks(self):
        surface=self.make()
        for status in (0xB0,0xB1):
            surface.receive_midi((status,69,4));surface.receive_midi((status,70,1))
        self.assertIsNotNone(surface._frame);self.assertIsNotNone(surface._constant_frame)
        surface.receive_midi((0xBF,78,4))
        self.assertIsNone(surface._frame);self.assertIsNone(surface._constant_frame)
        for status in (0xB0,0xB1):
            for cc,value in ((71,0),(72,20),(73,0),(74,1)):
                surface.receive_midi((status,cc,value))
        self.assertEqual(self.children[0].mixer_device.volume.value,.75)


if __name__=='__main__':unittest.main()
