"""Wire/Native registration tests through the actual adapter receive callback."""
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
import importlib.util
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT.parent))
from PerformanceController.fake_native import PerformanceSong

class FakeSurface:
    def __init__(self,instance):self._c_instance=instance
    def song(self):return self._c_instance.song
    def _send_midi(self,message,optimized=False):self._c_instance.sent.append(tuple(message))
    def update_display(self):pass
    def build_midi_map(self,handle):handle.base=True
    def disconnect(self):pass

framework=ModuleType('_Framework.ControlSurface');framework.ControlSurface=FakeSurface
live=ModuleType('Live')
live.Song=NS(Quantization=NS(q_bar=4,q_half=5,q_quarter=7,q_eight=9,q_sixtenth=11))
live.MidiMap=NS(forward_midi_cc=lambda owner,handle,channel,cc:handle.forwarded.append((channel,cc)))
spec=importlib.util.spec_from_file_location('PerformanceWireTest',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
with patch.dict(sys.modules,{'Live':live,'_Framework':ModuleType('_Framework'),'_Framework.ControlSurface':framework}):
    adapter=importlib.import_module('PerformanceWireTest.surface')

class SurfaceChecks(unittest.TestCase):
    def make(self,target=True):
        self.song=PerformanceSong()
        self.instance=NS(song=self.song,sent=[],handle=lambda:17)
        change=patch.object(adapter.config,'TARGET_SET','test.als' if target else '')
        change.start();self.addCleanup(change.stop)
        self.surface=adapter.PerformanceController(self.instance)
        self.addCleanup(self.surface.disconnect)
        return self.surface
    def handshake(self,generation=4):self.surface.receive_midi((0xBF,79,generation))
    def command(self,seq=1,key=0,value=40,generation=4):
        for cc,v in ((69,generation),(70,seq),(71,key),(72,value&127),(73,value>>7),(74,seq)):
            self.surface.receive_midi((0xB0,cc,v))
    def test_explicit_native_forwarding_matches_entire_contract(self):
        surface=self.make();handle=NS(forwarded=[])
        surface.build_midi_map(handle)
        self.assertTrue(handle.base)
        self.assertEqual(set(handle.forwarded),{(0,n) for n in range(69,75)}|{(15,78),(15,79),(15,109)})
    def test_empty_target_fails_closed_even_untitled(self):
        surface=self.make(False);self.song.file_path=''
        self.handshake();self.command()
        self.assertIsNone(surface.backend)
        self.assertEqual(self.instance.sent,[])
    def test_explicit_target_tuple_allows_only_named_nonempty_paths(self):
        with patch.object(adapter.config,'TARGET_SETS',('test.als','other.als'),create=True):
            surface=self.make();self.assertTrue(surface._target())
            for path,wanted in (('other.als',True),('third.als',False),('',False),('test.als.copy',False)):
                self.song.file_path=path;self.assertEqual(surface._target(),wanted)
        with patch.object(adapter.config,'TARGET_SETS','test.als',create=True):
            surface=self.make();self.assertFalse(surface._target())
    def test_switch_between_allowed_paths_rebinds_before_accepting_commands(self):
        with patch.object(adapter.config,'TARGET_SETS',('test.als','other.als'),create=True):
            surface=self.make();self.handshake();previous=surface.backend
            parameter=previous.state.parameters[0][0];old=parameter.value
            self.song.file_path='other.als';self.command(value=99)
            self.assertEqual(parameter.value,old)
            surface.update_display();self.assertIsNot(surface.backend,previous)
            self.assertTrue(previous._closed);self.assertIsNone(surface._generation)
            self.handshake();self.command(value=80)
            self.assertAlmostEqual(surface.backend.state.parameters[0][0].value,80/127.)
    def test_allowed_path_switch_detaches_even_if_previous_native_close_fails(self):
        with patch.object(adapter.config,'TARGET_SETS',('test.als','other.als'),create=True):
            surface=self.make();self.handshake();previous=surface.backend
            self.song.file_path='other.als'
            surface.receive_midi((0xBF,109,7))
            self.assertEqual(self.instance.sent[-1],(0xBF,108,7))
            with patch.object(previous,'close',side_effect=RuntimeError('old Native handles invalid')):
                surface.update_display()
            self.assertIsNot(surface.backend,previous)
            self.assertEqual(surface._bound_path,'other.als')
            self.assertIsNone(surface._generation)
            self.handshake();self.command(value=70)
            self.assertAlmostEqual(surface.backend.state.parameters[0][0].value,70/127.)
    def test_duplicate_complete_command_acks_without_second_toggle(self):
        surface=self.make();self.handshake()
        self.command(key=56,value=3)
        self.assertTrue(surface.backend.state.groups[0].group.mute)
        self.assertTrue(surface.backend.state.groups[1].group.mute)
        self.command(key=56,value=3)
        self.assertTrue(surface.backend.state.groups[0].group.mute)
        self.assertEqual([m[2] for m in self.instance.sent if m[1]==77], [3,3])
    def test_same_sequence_changed_payload_is_rejected(self):
        surface=self.make();self.handshake();self.command(key=0,value=40)
        self.command(key=0,value=99)
        self.assertAlmostEqual(surface.backend.state.parameters[0][0].value,40/127.)
        self.assertEqual(self.instance.sent[-3][2],127)
        self.assertEqual(self.instance.sent[-2][2],127)
    def test_wrong_generation_or_partial_frame_cannot_mutate(self):
        surface=self.make();self.handshake();parameter=surface.backend.state.parameters[0][0]
        original=parameter.value
        self.command(generation=3)
        for cc,v in ((69,4),(70,5),(72,99),(73,0),(74,5)):surface.receive_midi((0xB0,cc,v))
        self.assertEqual(parameter.value,original)
    def test_observation_frames_are_atomic_and_bounded_per_display(self):
        surface=self.make();self.handshake();self.instance.sent.clear()
        surface.update_display()
        frames=[];current=[]
        for message in self.instance.sent:
            if message[0]==0xB0:
                current.append(message)
                if message[1]==100:frames.append(current);current=[]
        self.assertLessEqual(len(frames),6)
        self.assertTrue(frames)
        for frame in frames:
            self.assertEqual([m[1] for m in frame],[89,90,91,92,93,94,95,96,97,98,99,100])
            self.assertEqual(frame[1][2],frame[-1][2])
    def test_path_change_rejects_commands_without_discovering_new_targets(self):
        surface=self.make();self.handshake()
        parameter=surface.backend.state.parameters[0][0];old=parameter.value
        self.song.file_path='other.als';self.command(value=99)
        self.assertEqual(parameter.value,old)
    def test_explicit_same_generation_reset_reseeds_and_clears_wire_guard(self):
        surface=self.make();self.handshake();self.command(key=56,value=1,seq=0)
        self.assertTrue(surface.backend.state.groups[0].group.mute)
        surface.backend.drain_facts(200)
        surface._observation_seq=37
        surface.receive_midi((0xBF,78,4))
        self.assertEqual(surface._generation,4)
        self.assertIsNone(surface._last)
        self.assertIsNone(surface._frame)
        self.assertEqual(surface._observation_seq,0)
        facts=surface.backend.drain_facts(200)
        self.assertTrue(any(kind==1 and a==0 for kind,a,b in facts))
        self.assertTrue(any(kind==3 and a==0 for kind,a,b in facts))
        self.assertEqual(self.instance.sent[-1],(0xBF,79,4))
        self.command(key=56,value=1,seq=0)
        self.assertFalse(surface.backend.state.groups[0].group.mute)

    def test_explicit_transport_reset_preserves_active_recording_transaction(self):
        surface=self.make();self.handshake()
        backend=surface.backend
        clock=[0.];backend.now=lambda:clock[0];backend.recording.monotonic=lambda:clock[0]
        for unused in range(16):
            clock[0]+=.101;backend.tick()
        self.command(key=64,value=11,seq=0)
        take=backend.recording.take
        self.assertIsNotNone(take)
        self.song.advance(take.target-.5);clock[0]+=.101;backend.tick()
        self.song.advance(take.target);clock[0]+=.101;backend.tick()
        self.assertEqual(take.state,'recording')
        calls_before=list(self.song.calls)
        surface.receive_midi((0xBF,78,4))
        self.assertIs(backend.recording.take,take)
        self.assertEqual(take.state,'recording')
        self.assertTrue(take.clip.is_recording)
        self.assertEqual(self.song.calls,calls_before)
        clock[0]+=.101;backend.tick()
        self.assertTrue(any(kind==5 and ((a>>3)&127)==11 and ((a>>10)&7)==3
                            for kind,a,b in backend.drain_facts(200)))

    def test_display_failure_flushes_bounded_diagnostics_and_recovers(self):
        from tempfile import TemporaryDirectory
        import json
        surface=self.make();self.handshake();backend=surface.backend
        with TemporaryDirectory() as temporary:
            trace=Path(temporary)/'events.jsonl';status=Path(temporary)/'status.json'
            with patch.object(adapter.config,'TRACE_PATH',str(trace)), patch.object(adapter.config,'STATUS_PATH',str(status)):
                with patch.object(backend,'tick',side_effect=RuntimeError('Native probe failure')):
                    with patch.object(adapter.time,'monotonic',return_value=100.):
                        for unused in range(3):surface.update_display()
                    rows=[json.loads(line) for line in trace.read_text().splitlines()]
                    errors=[r for r in rows if r['event']=='display_failed']
                    self.assertEqual(len(errors),1)
                    self.assertEqual(errors[0]['reason'],'Native probe failure')
                    self.assertEqual(json.loads(status.read_text())['display_error'],
                                     'RuntimeError: Native probe failure')
                    self.assertFalse(backend.events)
                    with patch.object(adapter.time,'monotonic',return_value=100.6):surface.update_display()
                    self.assertEqual(len(trace.read_text().splitlines()),len(rows))
                with patch.object(adapter.time,'monotonic',return_value=101.2):surface.update_display()
                self.assertIsNone(json.loads(status.read_text())['display_error'])

    def test_private_status_reads_retained_fields_without_native_graph_reads(self):
        from tempfile import TemporaryDirectory
        import json
        surface=self.make();self.handshake();backend=surface.backend
        backend.state.scan_occupancy(0,64)
        backend._maintenance(0)
        self.song.track_reads=0
        with TemporaryDirectory() as temporary:
            path=Path(temporary)/'status.json'
            with patch.object(adapter.config,'STATUS_PATH',str(path)), patch.object(adapter.time,'monotonic',return_value=20.):
                surface._status()
                status=json.loads(path.read_text())
                self.assertEqual(status['groups'],[0,1])
                self.assertEqual(status['reserves'],[dict(group=0,row=0)])
                self.assertEqual(status['tasks'],[])
                self.assertIsNone(status['take'])
                first=path.read_text()
                backend.reserves.clear()
                surface._status()
                self.assertEqual(path.read_text(),first)
            with patch.object(adapter.config,'STATUS_PATH',str(path)), patch.object(adapter.time,'monotonic',return_value=20.5):
                surface._status()
                self.assertEqual(json.loads(path.read_text())['reserves'],[])
        self.assertEqual(self.song.track_reads,0)

    def test_audio_status_uses_cached_catalog_and_storage_without_native_reads(self):
        from contextlib import ExitStack
        from tempfile import TemporaryDirectory
        import json
        from test_audio_inputs import AudioInputChecks
        surface=self.make()
        previous=surface.backend
        fixture=AudioInputChecks();backend=fixture.make(initial=3)
        self.addCleanup(fixture.doCleanups)
        previous.close();surface.backend=backend
        inputs=backend.audio_inputs
        backend.dispatch(49,1)
        self.assertGreater(inputs.state_store.pending,0)
        fixture.song.track_reads=0
        native_targets=[fixture.input_root]+list(fixture.sources)+[g.group for g in backend.state.groups.values()]
        with TemporaryDirectory() as temporary, ExitStack() as guards:
            for track in native_targets:
                guards.enter_context(patch.object(track,'get_data',side_effect=AssertionError('status must not read Native metadata')))
                guards.enter_context(patch.object(track,'set_data',side_effect=AssertionError('status must not write Native metadata')))
            path=Path(temporary)/'status.json'
            with patch.object(adapter.config,'STATUS_PATH',str(path)), patch.object(adapter.time,'monotonic',return_value=20.):
                surface._status()
                status=json.loads(path.read_text())
        audio=status['audio_inputs']
        self.assertEqual(audio['catalog_size'],2)
        self.assertEqual(audio['storage_pending'],inputs.state_store.pending)
        self.assertEqual(audio['storage_errors'],[])
        self.assertEqual(audio['catalog_issues'],inputs.issues)
        self.assertNotIn('helper_updates',audio)
        self.assertEqual(audio['desired']['1'],1)
        self.assertEqual(audio['monitor_owner'],1)
        self.assertEqual(fixture.song.track_reads,0)
        self.assertFalse(any(e.get('event')=='status_write_failed' for e in backend.events))

    def test_private_status_io_failure_does_not_escape_or_stop_commands(self):
        from tempfile import TemporaryDirectory
        surface=self.make();self.handshake()
        with TemporaryDirectory() as temporary:
            # A directory cannot be the target of atomic file replacement.
            with patch.object(adapter.config,'STATUS_PATH',temporary), patch.object(adapter.time,'monotonic',return_value=20.):
                surface._status()
                surface._status()
                self.command(seq=0,key=0,value=88)
                self.assertAlmostEqual(surface.backend.state.parameters[0][0].value,88/127.)
                errors=[event for event in surface.backend.events if event.get('event')=='status_write_failed']
                self.assertEqual(len(errors),1)
                self.assertEqual(self.instance.sent[-3][2],88)
                self.assertFalse(Path(temporary).with_name('.'+Path(temporary).name+'.tmp').exists())

    def test_same_generation_handshake_does_not_clear_duplicate_guard(self):
        surface=self.make();self.handshake();self.command(key=56,value=1)
        self.handshake();self.command(key=56,value=1)
        self.assertTrue(surface.backend.state.groups[0].group.mute)

if __name__=='__main__':unittest.main()
