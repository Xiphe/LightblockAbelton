"""Set-local Audio patch choices, separate from routing and touch dispatch.

Live restores Track user data with the saved Set. These setters do not mark a
clean Set modified or add Undo entries; callers must not treat this storage as
an automatic save. Flush after actual routing/monitoring work in maintenance.
"""
from dataclasses import dataclass
import json
import uuid

from .model import same

SOURCE_KEY = 'net.xiphe.lightpad.audio.source-id.v1'
PATCH_KEY = 'net.xiphe.lightpad.audio.patch.v1'
OWNER_KEY = 'net.xiphe.lightpad.audio.monitor-owner.v1'
_ABSENT = '__net.xiphe.lightpad.absent__'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _source_id(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


@dataclass
class _Value:
    track: object
    key: str
    desired: object
    dirty: bool = False
    blocked: bool = False
    error: str = ''
    verify: bool = False


@dataclass(frozen=True)
class FlushResult:
    writes: int = 0
    cleared: int = 0
    errors: tuple = ()


class AudioPatchState:
    """Cache choices by actual Native Track identity, save only on flush.

    bind() reads Native metadata during topology maintenance. set_patch() and
    set_monitor() read only retained Python values. Rebinding equal Native
    wrappers preserves pending intent; new group objects restore their own data.
    """
    def __init__(self, new_id=None):
        self._new_id = new_id or (lambda: str(uuid.uuid4()))
        self._root = None
        self._values = []
        self._sources = ()
        self._groups = {}
        self._owner = None
        self._cursor = 0
        self.source_ids = ()
        self.duplicate_indices = frozenset()
        self.invalid_indices = frozenset()
        self.issues = {}

    def _load(self, track, key, default):
        previous = next((v for v in self._values if v.key == key and same(v.track, track)), None)
        if previous is not None:
            previous.track = track
            return previous
        raw = track.get_data(key, _ABSENT)
        return _Value(track, key, default if raw == _ABSENT else raw)

    @staticmethod
    def _selection(value):
        try:
            data = json.loads(value)
            if (not isinstance(data, dict) or type(data.get('schema')) is not int or data.get('schema') != 1
                    or not isinstance(data.get('sources'), list)
                    or not all(_source_id(i) for i in data['sources'])
                    or len(data['sources']) != len(set(data['sources']))):
                raise ValueError('invalid Audio patch payload')
            return tuple(data['sources'])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _owner_index(value):
        try:
            data = json.loads(value)
            owner = data.get('group')
            if type(data.get('schema')) is int and data.get('schema') == 1 and (owner is None or type(owner) is int and 0 <= owner < 8):
                return owner
        except (ValueError, TypeError, AttributeError):
            pass
        return _ABSENT

    def bind(self, input_root, source_tracks, groups):
        """Restore metadata or retain pending values for the same Native objects."""
        if any(type(g) is not int or not 0 <= g < 8 for g in groups):
            raise ValueError('Audio groups must use indices 0 through 7')
        if input_root is None:
            raise ValueError('Audio Inputs root required')
        if not same(input_root, self._root):
            self._values = []
            self._cursor = 0
        self._root = input_root
        values, sources, ids, issues = [], [], [], {}
        for index, track in enumerate(tuple(source_tracks)):
            value = self._load(track, SOURCE_KEY, _ABSENT)
            if value.desired == _ABSENT:
                identifier = self._new_id()
                if not _source_id(identifier):
                    raise ValueError('source ID factory must return a canonical UUID string')
                value.desired = identifier
                value.dirty = True
            if not _source_id(value.desired):
                ids.append(None)
                issues[index] = 'invalid source identity metadata'
            else:
                ids.append(value.desired)
            values.append(value)
            sources.append(value)
        bound_groups = {}
        for group, track in sorted(groups.items()):
            value = self._load(track, PATCH_KEY, _json(dict(schema=1, sources=[])))
            if self._selection(value.desired) is None:
                issues['group:%d' % group] = 'invalid Audio patch metadata'
            bound_groups[group] = value
            values.append(value)
        owner = self._load(input_root, OWNER_KEY, _json(dict(schema=1, group=None)))
        values.append(owner)
        if self._owner_index(owner.desired) == _ABSENT:
            issues['monitor'] = 'invalid monitoring-owner metadata'
        self._values, self._sources, self._groups, self._owner = values, tuple(sources), bound_groups, owner
        self.source_ids = tuple(ids)
        self.invalid_indices = frozenset(i for i, identifier in enumerate(ids) if identifier is None)
        self.duplicate_indices = frozenset(i for i, identifier in enumerate(ids)
                                           if identifier is not None and ids.count(identifier) > 1)
        for index in self.duplicate_indices:
            issues[index] = 'duplicate source identity metadata'
        self.issues = issues
        if self._values:
            self._cursor %= len(self._values)

    def selected_ids(self, group):
        value = self._groups.get(group)
        return self._selection(value.desired) if value is not None else None

    def unresolved(self, group):
        """Saved IDs not uniquely present now; keep them until explicit new intent."""
        selected = self.selected_ids(group)
        if selected is None:
            return ()
        return tuple(identifier for identifier in selected if self.source_ids.count(identifier) != 1)

    def mask(self, group):
        selected = self.selected_ids(group) or ()
        return sum(1 << i for i, identifier in enumerate(self.source_ids)
                   if identifier in selected and i not in self.duplicate_indices and i not in self.invalid_indices)

    @property
    def monitor_owner(self):
        index = self._owner_index(self._owner.desired) if self._owner is not None else None
        return index if index in self._groups else None

    @staticmethod
    def _replace(value, desired):
        if value.desired != desired:
            value.desired = desired
            value.dirty = True
            value.blocked = False
            value.error = ''
            value.verify = False

    def set_patch(self, group, mask):
        """An explicit absolute selection replaces old IDs, including missing ones."""
        if type(group) is not int or group not in self._groups or type(mask) is not int or mask < 0 or mask >> len(self.source_ids):
            raise ValueError('Audio patch selection outside current catalog')
        chosen = [i for i in range(len(self.source_ids)) if mask & (1 << i)]
        if any(i in self.invalid_indices or i in self.duplicate_indices for i in chosen):
            raise ValueError('Audio source identity is unavailable')
        self._replace(self._groups[group], _json(dict(schema=1, sources=sorted(self.source_ids[i] for i in chosen))))
        self.issues.pop('group:%d' % group, None)
        return mask

    def set_monitor(self, group):
        if group is not None and (type(group) is not int or group not in self._groups):
            raise ValueError('monitor group unavailable')
        if self._owner is None:
            raise ValueError('Audio Inputs root unavailable')
        self._replace(self._owner, _json(dict(schema=1, group=group)))
        self.issues.pop('monitor', None)
        return group

    @property
    def pending(self):
        return sum(v.dirty for v in self._values)

    @property
    def errors(self):
        return tuple((v.key, v.error) for v in self._values if v.error)

    def retry(self):
        """Explicit retry after the caller has handled a Native storage failure."""
        for value in self._values:
            value.blocked = False
            value.error = ''
            value.verify = False

    def _dependencies_ready(self, value):
        if value.key != PATCH_KEY:
            return True
        selected = self._selection(value.desired) or ()
        return not any(source.dirty and source.desired in selected for source in self._sources)

    def flush(self, max_writes=2):
        """Bound Native storage work; uncertain writes require readback, not replay."""
        if type(max_writes) is not int or max_writes < 0:
            raise ValueError('nonnegative write budget required')
        if max_writes == 0 or not self._values:
            return FlushResult()
        writes, cleared, errors, checked = 0, 0, [], 0
        # A full cycle is bounded by catalog size plus eight groups and the root.
        while checked < len(self._values) and writes < max_writes:
            value = self._values[self._cursor]
            self._cursor = (self._cursor + 1) % len(self._values)
            checked += 1
            if (not value.dirty or value.blocked and not value.verify
                    or not self._dependencies_ready(value)):
                continue
            try:
                was_verifying = value.verify
                value.verify = False
                actual = value.track.get_data(value.key, _ABSENT)
                if actual == value.desired:
                    value.dirty = value.blocked = False
                    value.error = ''
                    cleared += 1
                elif not value.blocked:
                    # Block before calling: a setter may apply and then raise.
                    value.blocked = True
                    writes += 1
                    value.track.set_data(value.key, value.desired)
                    value.dirty = value.blocked = False
                    value.error = ''
                    cleared += 1
            except (RuntimeError, ValueError, AttributeError, TypeError) as error:
                value.blocked = True
                value.verify = not was_verifying
                if value.error != repr(error):
                    value.error = repr(error)
                    errors.append((value.key, value.error))
        return FlushResult(writes, cleared, tuple(errors))
