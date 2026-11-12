"""Retained per-group mixer assignments, without Live imports.

Targets are resolved once. Reordering a retained device, chain, parameter or
return invalidates the entire mixer until an explicit new binding is made.
The only native mutation here is a requested DeviceParameter.value write.
"""

import copy
import math

from . import protocol as p


FOURTH_CONTROL_OPCODE = 15
DEFAULT_TARGETS = tuple({"kind": "send", "index": index} for index in range(4))
PARAMETER_OPCODES = (p.SET_VOLUME, p.SET_SEND_1, p.SET_SEND_2,
                     p.SET_SEND_3, p.SET_PAN, FOURTH_CONTROL_OPCODE)


def _index(value):
    return type(value) is int and value >= 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _same(left, right):
    # Live can return new Python proxies for the same native object.
    return left is right or left == right


def normalized(parameter):
    width = parameter.max - parameter.min
    if width <= 0:
        raise ValueError("parameter has no usable range")
    return max(0, min(127, int(round(127 * (parameter.value - parameter.min) / width))))


class GroupParameterBindings:
    """Volume, four configured controls, then pan, as one usable mixer.

    A send target is {'kind': 'send', 'index': 0}. A group-device target is
    {'kind': 'device', 'device_path': [0], 'parameter_index': 3}. Nested paths
    alternate device/chain/device indices, e.g. [0, 1, 2]. Every traversed
    object and its index are retained. values() returns six normalized values
    or None when any binding is unavailable. Disabled parameters may recover;
    changed identities require explicit reconstruction of this binding.
    """

    def __init__(self, song, group, targets=None):
        self.song, self.group = song, group
        self.targets = []
        self.parameters = ()
        self.error = None
        self._invalid = False
        self._checks = []
        try:
            if targets is not None and not isinstance(targets, (tuple, list)):
                raise ValueError("assignment targets must be a four-item list")
            self.targets = copy.deepcopy(list(DEFAULT_TARGETS if targets is None else targets))
            if len(self.targets) != 4:
                raise ValueError("exactly four assignment targets required")
            mixer = group.mixer_device
            self._retain(lambda: group.mixer_device, mixer)
            volume = self._retain(lambda: group.mixer_device.volume, mixer.volume)
            pan = self._retain(lambda: group.mixer_device.panning, mixer.panning)
            controls = [self._resolve_target(mixer, target) for target in self.targets]
            self.parameters = tuple([volume] + controls + [pan])
        except (RuntimeError, AttributeError, IndexError, TypeError, ValueError) as error:
            self._invalid = True
            self.error = str(error)

    def invalidate(self, reason):
        self._invalid = True
        self.error = reason

    def _retain(self, getter, value):
        self._checks.append((getter, value))
        return value

    def _resolve_target(self, mixer, target):
        if not isinstance(target, dict):
            raise ValueError("assignment must be a target object")
        if target.get("kind") == "send":
            index = target.get("index")
            if not _index(index):
                raise ValueError("send index must be a nonnegative integer")
            native_return = self.song.return_tracks[index]
            self._retain(lambda index=index: self.song.return_tracks[index], native_return)
            return self._retain(lambda index=index: mixer.sends[index], mixer.sends[index])
        if target.get("kind") == "device":
            path = target.get("device_path")
            parameter_index = target.get("parameter_index")
            if (not isinstance(path, (tuple, list)) or len(path) % 2 != 1
                    or not all(_index(index) for index in path)
                    or not _index(parameter_index)):
                raise ValueError("device path must alternate device/chain/device indices")
            owner = self.group
            device = None
            for position in range(0, len(path), 2):
                device_index = path[position]
                device = self._retain(
                    lambda owner=owner, index=device_index: owner.devices[index],
                    owner.devices[device_index])
                if position + 1 < len(path):
                    chain_index = path[position + 1]
                    owner = self._retain(
                        lambda device=device, index=chain_index: device.chains[index],
                        device.chains[chain_index])
            return self._retain(
                lambda device=device, index=parameter_index: device.parameters[index],
                device.parameters[parameter_index])
        raise ValueError("assignment kind must be send or device")

    def valid(self):
        if self._invalid:
            return False
        try:
            if (not any(_same(track, self.group) for track in self.song.tracks)
                    or not self.group.is_foldable or self.group.is_grouped):
                self._invalid = True
                self.error = "bound group is no longer an eligible native group"
                return False
            if not all(_same(getter(), retained) for getter, retained in self._checks):
                self._invalid = True
                self.error = "assignment identity or index changed; explicit rebind required"
                return False
            if len(self.parameters) != 6:
                return False
            for parameter in self.parameters:
                if not parameter.is_enabled:
                    self.error = "assigned parameter is disabled"
                    return False
                if (not all(_number(value) for value in (parameter.min, parameter.max, parameter.value))
                        or parameter.max <= parameter.min
                        or not parameter.min <= parameter.value <= parameter.max):
                    self.error = "assigned parameter has an unusable range/value"
                    return False
            self.error = None
            return True
        except (RuntimeError, AttributeError, IndexError, TypeError, ValueError):
            self._invalid = True
            self.error = "assignment object is no longer available"
            return False

    def values(self):
        if not self.valid():
            return None
        try:
            return tuple(normalized(parameter) for parameter in self.parameters)
        except (RuntimeError, AttributeError, TypeError, ValueError):
            self._invalid = True
            self.error = "assignment value read failed"
            return None

    def write(self, opcode, value):
        indices = {p.SET_VOLUME: 0, p.SET_SEND_1: 1, p.SET_SEND_2: 2,
                   p.SET_SEND_3: 3, FOURTH_CONTROL_OPCODE: 4, p.SET_PAN: 5}
        if opcode not in indices or type(value) is not int or not 0 <= value <= 127:
            return p.BAD_COMMAND
        if not self.valid():
            return p.NOT_READY
        parameter = self.parameters[indices[opcode]]
        try:
            fraction = 0.5 if opcode == p.SET_PAN and value == 64 else value / 127.0
            desired = parameter.min + (parameter.max - parameter.min) * fraction
            if getattr(parameter, "is_quantized", False):
                desired = max(parameter.min, min(parameter.max, round(desired)))
            parameter.value = desired
            # Read back through the retained object. Never publish the request
            # as if Live confirmed it; values() reads actual native state.
            if not self.valid():
                return p.NOT_READY
            return p.APPLIED
        except (RuntimeError, AttributeError, TypeError, ValueError):
            self.error = "parameter write failed"
            return p.NOT_READY
