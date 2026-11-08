"""Actual Live group mute/mixer adapter, without recording or routing writes."""

from . import protocol as p
from .configuration import GROUP_NAMES
from .parameter_bindings import GroupParameterBindings, PARAMETER_OPCODES, normalized


class LiveGroups:
    def __init__(self, song, names=GROUP_NAMES, assignments=None):
        if len(names) != 8 or len(set(names)) != 8:
            raise ValueError("eight distinct group names required")
        self.song = song
        self.names = names
        self.assignments = dict(assignments or {})
        self._bindings = {}

    def find(self, index):
        matches = [track for track in self.song.tracks
                   if track.name == self.names[index] and track.is_foldable
                   and not track.is_grouped]
        return matches[0] if len(matches) == 1 else None

    def _parameters(self, track):
        """Compatibility inspection order: volume, four controls, then pan."""
        for index in range(len(self.names)):
            if self.find(index) == track:
                binding = self._binding(index, track)
                return list(binding.parameters) if binding.valid() else []
        return []

    def _binding(self, index, track):
        if index not in self._bindings:
            self._bindings[index] = GroupParameterBindings(
                self.song, track, self.assignments.get(index))
        # Retain an old binding if the group itself was replaced. A fresh
        # same-named group is not an authorization to retarget parameters.
        binding = self._bindings[index]
        if binding.group != track:
            binding.invalidate("bound group changed; explicit rebind required")
        return binding

    def configure_targets(self, index, targets):
        """Explicit host configuration, with no native mutation or device UI."""
        if type(index) is not int or not 0 <= index < len(self.names):
            raise ValueError("invalid group index")
        self.assignments[index] = targets
        self._bindings.pop(index, None)
        track = self.find(index)
        if track is not None:
            self._binding(index, track)

    def dispatch(self, command):
        if command.op == p.REFRESH:
            return p.APPLIED
        if command.op == p.TAP_TEMPO:
            if self.find(0) is None:
                return p.NOT_READY
            self.song.tap_tempo()
            return p.APPLIED
        # Resolve by exact unique identity before EACH mutation, including rename.
        track = self.find(command.group)
        if track is None:
            return p.NOT_READY
        if command.op == p.SET_MUTE:
            track.mute = command.arg == 127
            return p.APPLIED
        if command.op in PARAMETER_OPCODES:
            return self._binding(command.group, track).write(command.op, command.arg)
        # Recording, cancellation and advance require the session backend.
        return p.NOT_READY

    def snapshot(self, index, sequence=0, outcome=p.NONE):
        fields = dict((cc, 0) for cc in range(91, 109))
        fields[94] = 127
        fields[104], fields[105] = sequence, outcome
        track = self.find(index)
        if track is None:
            return fields
        fields[92] = 127 if track.mute else 0
        color = int(track.color)
        for cc, shift in ((95, 16), (96, 8), (97, 0)):
            fields[cc] = int(round(((color >> shift) & 255) * 127 / 255.0))
        values = self._binding(index, track).values()
        if values is not None:
            for cc, value in zip((98, 99, 100, 101, 108, 102), values):
                fields[cc] = value
            fields[103] = p.CAP_MIXER
        # No reference can be inferred from arbitrary pre-existing clips. Only a
        # fully empty group has a known phrase state before session binding.
        children = [child for child in self.song.tracks
                    if getattr(child, "group_track", None) == track]
        if not any(child.is_foldable for child in children) and not any(
                slot.has_clip for child in children for slot in child.clip_slots):
            fields[91] = p.EMPTY
        return fields
