"""Focused checks for the disposable adapter, including Native CC registration."""
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
import importlib.util
import sys
import unittest
from unittest.mock import patch


class ControlSurface:
    def __init__(self, instance):
        self.instance = instance
        self._c_instance = instance
    def song(self):
        return self.instance.song
    def _send_midi(self, message, optimized=False):
        self.instance.sent.append(tuple(message))
    def build_midi_map(self, handle):
        handle.base_called = True
    def log_message(self, value):
        self.instance.logs.append(value)
    def update_display(self):
        pass
    def disconnect(self):
        pass


class Parameter:
    min, max = 0., 1.
    def __init__(self):
        self._value = .5
        self.listeners = []
        self.writes = []
    @property
    def value(self):
        return self._value
    @value.setter
    def value(self, value):
        self.writes.append(value)
        self._value = value
        for callback in self.listeners[:]:
            callback()
    def add_value_listener(self, callback):
        self.listeners.append(callback)
    def remove_value_listener(self, callback):
        self.listeners.remove(callback)
    def value_has_listener(self, callback):
        return callback in self.listeners


fake = ModuleType('_Framework.ControlSurface')
fake.ControlSurface = ControlSurface
live = ModuleType('Live')
def forward_midi_cc(script_handle, map_handle, channel, cc):
    map_handle.calls.append((script_handle, channel, cc))
    map_handle.forwarded.add((channel, cc))
live.MidiMap = NS(forward_midi_cc=forward_midi_cc)
root = Path(__file__).parent
spec = importlib.util.spec_from_file_location('PrivateFaderAdapter', root/'__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
with patch.dict(sys.modules, {'_Framework': ModuleType('_Framework'),
                              '_Framework.ControlSurface': fake, 'Live': live}):
    spec.loader.exec_module(package)
    module = sys.modules['PrivateFaderAdapter.surface']


class AdapterChecks(unittest.TestCase):
    def make(self, exact=True):
        scope = patch.object(module.config, 'TARGET_SET', 'Optimistic Fader Test.als')
        scope.start(); self.addCleanup(scope.stop)
        self.parameter = Parameter()
        track = NS(name='[R1] Probe', is_foldable=True, mixer_device=NS(volume=self.parameter))
        self.song = NS(file_path=module.config.TARGET_SET if exact else 'Other.als', tracks=[track])
        self.instance = NS(song=self.song, sent=[], logs=[], handle=lambda: 23)
        self.adapter = package.create_instance(self.instance)
        self.addCleanup(self.adapter.disconnect)
        self.trace = patch.object(module.config, 'TRACE_PATH', None)
        self.trace.start(); self.addCleanup(self.trace.stop)
        return self.adapter

    def command(self, generation=9, sequence=4, desired=100):
        for cc, value in ((69,generation),(70,sequence),(71,desired),(72,sequence)):
            self.adapter.receive_midi((0xB0,cc,value))

    def test_empty_configuration_rejects_even_untitled_set(self):
        self.make()
        self.song.file_path = ''
        with patch.object(module.config, 'TARGET_SET', ''):
            self.adapter.receive_midi((0xBF,79,9))
            self.assertIsNone(self.adapter._generation)
            self.command()
            self.assertEqual(self.parameter.writes,[])

    def test_registered_native_map_delivers_handshake_and_command_only(self):
        self.make()
        midi_map = NS(forwarded=set(), calls=[], base_called=False)
        self.adapter.build_midi_map(midi_map)
        expected = {(0,69),(0,70),(0,71),(0,72),(15,79)}
        self.assertTrue(midi_map.base_called)
        self.assertEqual(midi_map.forwarded,expected)
        self.assertEqual(len(midi_map.calls),5)
        self.assertTrue(all(call[0]==23 for call in midi_map.calls))
        def native_deliver(message):
            if (message[0]&15,message[1]) not in midi_map.forwarded:
                return False
            self.adapter.receive_midi(message)
            return True
        self.assertTrue(native_deliver((0xBF,79,9)))
        self.instance.sent.clear()
        for cc,value in ((69,9),(70,4),(71,100),(72,4)):
            self.assertTrue(native_deliver((0xB0,cc,value)))
        self.assertAlmostEqual(self.parameter.value,100/127.)
        self.assertEqual(self.instance.sent,[(0xB0,69,9),(0xB0,73,4),(0xB0,74,100),(0xB0,75,4)])
        self.assertFalse(native_deliver((0xB0,73,4)))

    def test_command_applies_direct_volume_and_replies_with_matching_generation(self):
        self.make(); self.adapter.receive_midi((0xBF,79,9)); self.instance.sent.clear()
        self.command()
        self.assertAlmostEqual(self.parameter.value,100/127.)
        self.assertEqual(self.instance.sent,[(0xB0,69,9),(0xB0,73,4),(0xB0,74,100),(0xB0,75,4)])

    def test_unhandshaken_wrong_generation_and_wrong_set_do_not_mutate(self):
        self.make(); self.command(); self.assertEqual(self.parameter.writes,[])
        self.adapter.receive_midi((0xBF,79,9)); self.command(generation=8); self.assertEqual(self.parameter.writes,[])
        self.song.file_path='Other.als'; self.command(); self.assertEqual(self.parameter.writes,[])

    def test_incomplete_or_mismatched_frame_does_not_mutate(self):
        self.make(); self.adapter.receive_midi((0xBF,79,9))
        for cc,value in ((69,9),(70,4),(71,100),(72,5)):
            self.adapter.receive_midi((0xB0,cc,value))
        self.assertEqual(self.parameter.writes,[])

    def test_external_changes_publish_latest_value_once_per_callback(self):
        self.make(); self.adapter.receive_midi((0xBF,79,9)); self.instance.sent.clear()
        self.parameter.value=.2; self.parameter.value=.8
        self.assertEqual(self.instance.sent,[])
        self.adapter.update_display()
        self.assertEqual(self.instance.sent,[(0xB0,69,9),(0xB0,76,1),(0xB0,77,102),(0xB0,78,1)])
        self.adapter.update_display(); self.assertEqual(len(self.instance.sent),4)

    def test_delayed_reply_does_not_delay_parameter_write_and_generation_reset_drops_old_ack(self):
        self.make()
        with patch.object(module.config,'ACK_DELAY_SECONDS',1.), patch.object(module.time,'monotonic',return_value=10.):
            self.adapter.receive_midi((0xBF,79,9)); self.instance.sent.clear(); self.command()
            self.assertAlmostEqual(self.parameter.value,100/127.)
            self.assertEqual(self.instance.sent,[])
            self.adapter.receive_midi((0xBF,79,10)); self.assertEqual(self.adapter._acks,[])
            self.instance.sent.clear()
        with patch.object(module.time,'monotonic',return_value=12.):
            self.adapter.update_display()
        self.assertEqual(self.instance.sent,[])

    def test_delayed_reply_emits_after_due_without_sleeping(self):
        self.make()
        with patch.object(module.config,'ACK_DELAY_SECONDS',1.), patch.object(module.time,'monotonic',return_value=10.):
            self.adapter.receive_midi((0xBF,79,9)); self.instance.sent.clear(); self.command()
        with patch.object(module.time,'monotonic',return_value=10.5):
            self.adapter.update_display()
        self.assertFalse(any(message[1]==73 for message in self.instance.sent))
        with patch.object(module.time,'monotonic',return_value=11.):
            self.adapter.update_display()
        self.assertTrue((0xB0,73,4) in self.instance.sent)


if __name__ == '__main__':
    unittest.main()
