"""Bounded selection fallback after one caller-owned track insertion.

All methods, including tick(), run in Live's callbacks. No graph mutation or
threading is performed here. Selection listeners only retain references.
"""
import time


def same(left, right):
    try:
        return left is right or left == right
    except Exception:
        return False


def contains(values, obj):
    return any(same(value, obj) for value in values)


def single_inserted_track(before, after, index):
    """Validate one insertion and retained order with linear Native comparisons."""
    if len(after) != len(before) + 1 or not 0 <= index <= len(before):
        raise ValueError('created track identity unavailable; not replayed')
    created = after[index]
    if (contains(before, created)
            or any(not same(old, new) for old, new in zip(before[:index], after[:index]))
            or any(not same(old, new) for old, new in zip(before[index:], after[index+1:]))):
        raise ValueError('creation changed existing track identity/order; not replayed')
    return created


class SelectionRestoration:
    """Capture before insertion; capture_created afterward; tick until done.

    The caller must give the exact newly created track objects. An unchanged
    immediate view is not completion: Native selection may arrive later.
    """
    def __init__(self, song, now=time.monotonic, timeout=.5):
        if not 0 < timeout <= 2:
            raise ValueError('timeout must be positive and at most two seconds')
        self.song, self.view, self.now, self.timeout = song, song.view, now, timeout
        self.path = str(song.file_path)
        self.before_tracks = tuple(song.tracks)
        self.track, self.scene, self.slot = self._selection()
        self.row = next((i for i, scene in enumerate(song.scenes)
                         if same(scene, self.scene)), None)
        self.created = ()
        self.state, self.reason = 'captured', None
        self.attempts, self.writes = 0, []
        self.deadline = None
        self._events = []
        self._listeners = []
        self._writing = False
        self._listen(self.view, 'selected_track', self._track_changed)
        try:
            self._listen(self.view, 'selected_scene', self._scene_changed)
        except Exception:
            self.close()
            raise

    @property
    def done(self):
        return self.state in ('restored', 'unchanged', 'superseded', 'failed')

    def result(self):
        return dict(state=self.state, reason=self.reason, done=self.done,
                    restoration_attempts=self.attempts, selection_writes=list(self.writes),
                    listener_count=len(self._listeners),
                    viewport_restored=False, highlighted_slot_polling_gap=True)

    def _listen(self, owner, attr, callback):
        method = getattr(owner, 'add_' + attr + '_listener', None)
        if method is not None:
            method(callback)
            self._listeners.append((owner, attr, callback))

    def _track_changed(self):
        if not self._writing:
            self._events.append(('track', self.view.selected_track))

    def _scene_changed(self):
        if not self._writing:
            self._events.append(('scene', self.view.selected_scene))

    def _selection(self):
        return (self.view.selected_track, self.view.selected_scene,
                self.view.highlighted_clip_slot)

    def _matches_before(self, selection):
        return all(same(a, b) for a, b in zip(selection, (self.track, self.scene, self.slot)))

    def _expected_created_slot(self, track):
        slots = tuple(track.clip_slots)
        return slots[self.row] if self.row is not None and self.row < len(slots) else None

    def _matches_created(self, selection):
        track, scene, slot = selection
        return (contains(self.created, track) and same(scene, self.scene)
                and same(slot, self._expected_created_slot(track)))

    def capture_created(self, created):
        if self.state != 'captured':
            raise ValueError('capture_created may only be called once')
        self.created = tuple(created)
        current = tuple(self.song.tracks)
        if len(self.created) == 1:
            index = next((i for i, track in enumerate(current) if same(track, self.created[0])), None)
            try:
                if index is None:
                    raise ValueError('created track missing')
                single_inserted_track(self.before_tracks, current, index)
            except ValueError:
                self._finish('failed', 'created track identity/order mismatch')
                return
            self.deadline = self.now() + self.timeout
            self.state = 'waiting'
            return
        actual = tuple(track for track in current if not contains(self.before_tracks, track))
        if (not self.created or len(actual) != len(self.created)
                or any(not contains(actual, track) for track in self.created)):
            self._finish('failed', 'created track identity mismatch')
            return
        self.deadline = self.now() + self.timeout
        self.state = 'waiting'

    def _targets_valid(self):
        tracks = (tuple(self.song.tracks) + tuple(getattr(self.song, 'return_tracks', ()))
                  + (getattr(self.song, 'master_track', None),))
        if not contains(tracks, self.track) or not contains(self.song.scenes, self.scene):
            return False
        if any(not contains(tracks, track) for track in self.created):
            return False
        if self.slot is not None and not contains(getattr(self.track, 'clip_slots', ()), self.slot):
            return False
        return True

    def tick(self):
        if self.done or self.state == 'captured':
            return self.state
        try:
            if str(self.song.file_path) != self.path:
                self._finish('failed', 'Set changed')
                return self.state
            if not self._targets_valid():
                self._finish('failed', 'selection target removed')
                return self.state
            events, self._events = self._events, []
            if any((kind == 'track' and not same(value, self.track)
                    and not contains(self.created, value))
                   or (kind == 'scene' and not same(value, self.scene))
                   for kind, value in events):
                self._finish('superseded', 'unrelated selection event')
                return self.state
            current = self._selection()
            if self.state == 'checking':
                if not self._matches_before(current):
                    self._finish('superseded', 'selection changed after restoration')
                elif self.now() >= self.deadline:
                    self._finish('restored')
                return self.state
            if self._matches_before(current):
                if self.now() >= self.deadline:
                    self._finish('unchanged', 'no created-track selection observed')
                return self.state
            if not self._matches_created(current):
                self._finish('superseded', 'view differs from insertion context')
                return self.state
            if self.now() >= self.deadline:
                self._finish('failed', 'restoration window expired')
                return self.state
            # Once only; exceptions after a partial Native effect never replay.
            self.attempts += 1
            self.state = 'checking'
            self._writing = True
            try:
                for attr, desired in (('selected_track', self.track),
                                      ('selected_scene', self.scene),
                                      ('highlighted_clip_slot', self.slot)):
                    if not same(getattr(self.view, attr), desired):
                        self.writes.append(attr)
                        setattr(self.view, attr, desired)
            finally:
                self._writing = False
            self.deadline = self.now() + self.timeout
        except Exception as exc:
            self._finish('failed', '%s: %s' % (type(exc).__name__, exc))
        return self.state

    def _finish(self, state, reason=None):
        self.state, self.reason = state, reason
        self.close()

    def close(self):
        listeners, self._listeners = self._listeners, []
        for owner, attr, callback in listeners:
            getattr(owner, 'remove_' + attr + '_listener')(callback)


SelectionRestore = SelectionRestoration
