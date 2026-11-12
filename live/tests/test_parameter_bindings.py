"""Mixer assignments retain native identities and never retarget on reordering."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.groups import LiveGroups
from LightpadController.parameter_bindings import GroupParameterBindings, FOURTH_CONTROL_OPCODE


class Parameter:
    def __init__(self, value=0.5, low=0, high=1, quantized=False):
        self._value, self.min, self.max = value, low, high
        self.is_enabled = True
        self.is_quantized = quantized
        self.writes = []
        self.coerce = None

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.writes.append(value)
        self._value = self.coerce(value) if self.coerce else value


class Mixer:
    def __init__(self, send_count=4):
        self.volume = Parameter()
        self.panning = Parameter(0, -1, 1)
        self.sends = [Parameter() for _ in range(send_count)]


class Device:
    def __init__(self, name="FX", parameters=None, chains=None):
        self.name = name
        self.parameters = parameters if parameters is not None else [Parameter(), Parameter()]
        self.chains = chains if chains is not None else []


class Chain:
    def __init__(self, devices):
        self.devices = devices


class Group:
    def __init__(self, name="Lightpad 1", send_count=4):
        self.name = name
        self.is_foldable = True
        self.is_grouped = False
        self.group_track = None
        self.mixer_device = Mixer(send_count)
        self.devices = []
        self.clip_slots = []
        self.mute = False
        self.color = 0x2080FF


class Song:
    def __init__(self, groups=None, return_count=4):
        self.tracks = groups if groups is not None else [Group()]
        self.return_tracks = [object() for _ in range(return_count)]
        self.taps = 0

    def tap_tempo(self):
        self.taps += 1


def send(index):
    return {"kind": "send", "index": index}


def device(path, parameter=0):
    return {"kind": "device", "device_path": path, "parameter_index": parameter}


def command(op, arg=127, group=0):
    return p.Command(1, group, op, arg)


class ParameterBindingTests(unittest.TestCase):
    def test_four_default_sends_and_pan_have_actual_readback(self):
        song = Song()
        group = song.tracks[0]
        bindings = GroupParameterBindings(song, group)
        self.assertTrue(bindings.valid())
        self.assertEqual(bindings.values(), (64, 64, 64, 64, 64, 64))
        self.assertEqual(bindings.write(FOURTH_CONTROL_OPCODE, 127), p.APPLIED)
        self.assertEqual(group.mixer_device.sends[3].value, 1)
        self.assertEqual(bindings.values()[4], 127)
        for arg, native in ((0, -1), (64, 0), (127, 1)):
            self.assertEqual(bindings.write(p.SET_PAN, arg), p.APPLIED)
            self.assertEqual(group.mixer_device.panning.value, native)
            self.assertEqual(bindings.values()[5], arg)

    def test_group_snapshot_maps_fourth_assignment_to_cc108_without_shifts(self):
        song = Song()
        adapter = LiveGroups(song)
        self.assertEqual(adapter.dispatch(command(FOURTH_CONTROL_OPCODE)), p.APPLIED)
        fields = adapter.snapshot(0)
        self.assertEqual(set(fields), set(range(91, 109)))
        self.assertEqual(fields[108], 127)
        self.assertEqual(fields[102], 64)
        self.assertEqual(fields[103], p.CAP_MIXER)
        self.assertEqual(len(adapter._parameters(song.tracks[0])), 6)

    def test_assignments_are_independent_for_each_group(self):
        first, second = Group(), Group("Lightpad 2")
        first.devices = [Device()]
        song = Song([first, second])
        adapter = LiveGroups(song, assignments={0: [device([0], 1), send(1), send(2), send(3)]})
        self.assertEqual(adapter.dispatch(command(p.SET_SEND_1, group=0)), p.APPLIED)
        self.assertEqual(first.devices[0].parameters[1].value, 1)
        self.assertEqual(first.mixer_device.sends[0].value, .5)
        self.assertEqual(adapter.dispatch(command(p.SET_SEND_1, group=1)), p.APPLIED)
        self.assertEqual(second.mixer_device.sends[0].value, 1)
        self.assertEqual(second.devices, [])

    def test_nested_chain_target_resolves_only_inside_group(self):
        song = Song()
        group = song.tracks[0]
        target = Device("target")
        group.devices = [Device("rack", chains=[Chain([Device("unused"), target])])]
        bindings = GroupParameterBindings(song, group, [send(0), send(1), send(2), device([0, 0, 1], 1)])
        self.assertTrue(bindings.valid())
        self.assertEqual(bindings.write(FOURTH_CONTROL_OPCODE, 0), p.APPLIED)
        self.assertEqual(target.parameters[1].value, 0)
        group.devices[0].chains[0] = Chain([Device("unused"), target])
        self.assertFalse(bindings.valid())
        self.assertEqual(bindings.write(p.SET_VOLUME, 127), p.NOT_READY)
        self.assertEqual(group.mixer_device.volume.writes, [])

    def test_device_reorder_invalidates_until_explicit_rebind(self):
        song = Song()
        group = song.tracks[0]
        original, other = Device("original"), Device("other")
        group.devices = [original, other]
        targets = [device([0]), send(1), send(2), send(3)]
        adapter = LiveGroups(song, assignments={0: targets})
        self.assertEqual(adapter.snapshot(0)[103], p.CAP_MIXER)
        group.devices.reverse()
        self.assertEqual(adapter.dispatch(command(p.SET_SEND_1)), p.NOT_READY)
        self.assertEqual(original.parameters[0].writes, [])
        self.assertEqual(other.parameters[0].writes, [])
        self.assertEqual(adapter.snapshot(0)[103], 0)
        group.devices.reverse()
        self.assertEqual(adapter.dispatch(command(p.SET_SEND_1)), p.NOT_READY)
        adapter.configure_targets(0, targets)
        self.assertEqual(adapter.dispatch(command(p.SET_SEND_1)), p.APPLIED)
        self.assertEqual(original.parameters[0].value, 1)

    def test_parameter_replacement_invalidates_retained_parameter(self):
        song = Song()
        group = song.tracks[0]
        group.devices = [Device()]
        bindings = GroupParameterBindings(song, group, [device([0], 1), send(1), send(2), send(3)])
        retained = group.devices[0].parameters[1]
        replacement = Parameter()
        group.devices[0].parameters[1] = replacement
        self.assertEqual(bindings.write(p.SET_SEND_1, 127), p.NOT_READY)
        self.assertEqual(retained.writes, [])
        self.assertEqual(replacement.writes, [])

    def test_return_reorder_cannot_redirect_send_or_volume_write(self):
        song = Song()
        group = song.tracks[0]
        bindings = GroupParameterBindings(song, group)
        song.return_tracks.reverse()
        self.assertFalse(bindings.valid())
        self.assertEqual(bindings.write(FOURTH_CONTROL_OPCODE, 127), p.NOT_READY)
        self.assertEqual(bindings.write(p.SET_VOLUME, 127), p.NOT_READY)
        self.assertTrue(all(not parameter.writes for parameter in bindings.parameters))

    def test_send_parameter_replacement_invalidates_even_with_same_return(self):
        song = Song()
        group = song.tracks[0]
        bindings = GroupParameterBindings(song, group)
        group.mixer_device.sends[3] = Parameter()
        self.assertEqual(bindings.write(FOURTH_CONTROL_OPCODE, 127), p.NOT_READY)
        self.assertEqual(group.mixer_device.sends[3].writes, [])

    def test_disabled_assignment_blocks_all_writes_but_can_recover_same_identity(self):
        song = Song()
        group = song.tracks[0]
        bindings = GroupParameterBindings(song, group)
        group.mixer_device.sends[2].is_enabled = False
        self.assertIsNone(bindings.values())
        self.assertEqual(bindings.write(p.SET_VOLUME, 127), p.NOT_READY)
        self.assertEqual(bindings.write(p.SET_PAN, 0), p.NOT_READY)
        self.assertTrue(all(not parameter.writes for parameter in bindings.parameters))
        group.mixer_device.sends[2].is_enabled = True
        self.assertTrue(bindings.valid())

    def test_missing_fourth_return_disables_full_mixer_but_not_group_mute(self):
        song = Song(return_count=3)
        group = song.tracks[0]
        adapter = LiveGroups(song)
        self.assertEqual(adapter.snapshot(0)[103], 0)
        self.assertEqual(adapter.dispatch(command(p.SET_VOLUME)), p.NOT_READY)
        self.assertEqual(adapter.dispatch(command(p.SET_MUTE)), p.APPLIED)
        self.assertTrue(group.mute)
        self.assertEqual(group.mixer_device.volume.writes, [])

    def test_quantized_device_parameter_rounds_and_snapshot_reads_native_value(self):
        song = Song()
        group = song.tracks[0]
        quantized = Parameter(0, 0, 5, quantized=True)
        group.devices = [Device(parameters=[quantized])]
        bindings = GroupParameterBindings(song, group, [device([0]), send(1), send(2), send(3)])
        self.assertEqual(bindings.write(p.SET_SEND_1, 64), p.APPLIED)
        self.assertEqual(quantized.value, 3)
        self.assertEqual(bindings.values()[1], round(127 * 3 / 5))
        group.mixer_device.sends[3].coerce = lambda requested: .25
        self.assertEqual(bindings.write(FOURTH_CONTROL_OPCODE, 127), p.APPLIED)
        self.assertEqual(bindings.values()[4], 32)

    def test_replaced_group_does_not_reuse_old_mixer_even_if_old_group_remains(self):
        song = Song()
        original = song.tracks[0]
        adapter = LiveGroups(song)
        self.assertEqual(adapter.snapshot(0)[103], p.CAP_MIXER)
        original.name = "Former group"
        replacement = Group()
        song.tracks.append(replacement)
        self.assertEqual(adapter.dispatch(command(p.SET_VOLUME)), p.NOT_READY)
        self.assertEqual(original.mixer_device.volume.writes, [])
        self.assertEqual(replacement.mixer_device.volume.writes, [])
        adapter.configure_targets(0, [send(index) for index in range(4)])
        self.assertEqual(adapter.dispatch(command(p.SET_VOLUME)), p.APPLIED)
        self.assertEqual(replacement.mixer_device.volume.value, 1)

    def test_invalid_paths_values_and_ranges_never_write(self):
        song = Song()
        group = song.tracks[0]
        group.devices = [Device()]
        for target in [device([]), device([0, 0]), device([True]), device([1]),
                       device([0], -1), {"kind": "track", "index": 0}]:
            bindings = GroupParameterBindings(song, group, [target, send(1), send(2), send(3)])
            self.assertFalse(bindings.valid())
            self.assertEqual(bindings.write(p.SET_VOLUME, 127), p.NOT_READY)
        bindings = GroupParameterBindings(song, group)
        for value in (-1, 128, True, .5):
            self.assertEqual(bindings.write(p.SET_VOLUME, value), p.BAD_COMMAND)
        self.assertEqual(bindings.write(p.SET_MUTE, 0), p.BAD_COMMAND)
        group.mixer_device.volume.max = group.mixer_device.volume.min
        self.assertFalse(bindings.valid())
        self.assertEqual(group.mixer_device.volume.writes, [])


if __name__ == "__main__":
    unittest.main()
