"""Ephemeral native recording transactions over rediscovered Live state.

Only pending takes retain identities and timing. Completed musical state is
always read back from Live. A native fire is marked issued BEFORE calling Live;
ambiguous native exceptions never cause an automatic second fire.
"""
from dataclasses import dataclass
import math
import time

from . import protocol as p
from .launch_admission import LaunchAdmission
from .live_state import LoopReference, read_grid, same, number
from .recording_coverage import sample_covers
from .patching import PendingRouting

EPSILON = 1e-7
LEAD = .100
FIRE_LEAD = .050
PERIODS = (('q_half', 2.), ('q_quarter', 1.), ('q_eight', .5), ('q_sixtenth', .25))


@dataclass
class Take:
    group: int
    parent: object
    track: object
    slot: object
    row: int
    token: int
    reference: object
    state: str = 'queued'
    queued_at: float = 0.
    target: float = 0.
    start_issued: bool = False
    clip: object = None
    anchor: object = None
    bar: object = None
    numerator: object = None
    denominator: object = None
    finish_target: object = None
    finish_issued: bool = False
    normalized: bool = False
    armed_here: bool = False
    adopted: bool = False
    stop_issued: bool = False
    delete_issued: bool = False
    discard: object = None
    continuation: object = None
    following: object = None
    pending_command: object = None
    failure: object = None
    retry_reference: object = None
    source: object = None
    companions: tuple = ()
    monitor_job: object = None
    routing_prepared: bool = False

    @property
    def take_id(self):
        return self.token % 128


class LiveRecording:
    def __init__(self, song, state, quantizations, emit=None, monotonic=None, patching=None):
        self.song, self.state = song, state
        self.quantizations = dict(quantizations or {})
        self.emit = emit or (lambda event: None)
        self.monotonic = monotonic or time.monotonic
        self.patching = patching
        self.take = None
        self._token = 0
        self._advance = {}
        self._preferred_rows = {}
        self._completions = []
        self._disarms = []
        self.issues = {}
        self._failed_allocations = {}

    @staticmethod
    def members(take):
        return (take,) + take.companions if take is not None else ()

    def phase(self, take=None):
        take = self.take if take is None else take
        states = {item.state for item in self.members(take)}
        return next((state for state in ('canceling', 'finish_queued', 'recording', 'queued')
                     if state in states), 'completed')

    def representative(self, take=None):
        take = self.take if take is None else take
        return next((item for item in self.members(take) if item.clip is not None
                     and item.state in ('recording', 'finish_queued') and item.clip.is_recording), take)

    def _new_token(self):
        self._token += 1
        return self._token

    def _event(self, event, take=None, **details):
        try:
            self.emit(dict(event=event, group=take.group if take else None, **details))
        except Exception:
            pass

    def _refresh(self):
        return self.state.refresh()

    def _group(self, group, parent=None):
        item = self._refresh().groups.get(group)
        if item is None or parent is not None and not same(item.group, parent):
            raise ValueError('recording group changed')
        return item

    def _identity(self, take, empty=False):
        group = self._group(take.group, take.parent)
        if not any(same(t, take.track) for t in group.tracks):
            raise ValueError('recording track moved or disappeared')
        slots = tuple(take.track.clip_slots)
        if take.row >= len(slots) or not same(slots[take.row], take.slot):
            raise ValueError('recording destination moved')
        if take.clip is not None and (not take.slot.has_clip or not same(take.slot.clip, take.clip)):
            raise ValueError('recording clip was replaced')
        if empty and take.slot.has_clip:
            raise ValueError('recording destination is occupied')
        return group

    def _source_ready(self, take):
        if take.source is not None:
            if take.monitor_job is not None:
                if not take.monitor_job.done:
                    raise PendingRouting('recording monitor routing is preparing')
                if take.monitor_job.error is not None:
                    raise take.monitor_job.error
            if take.routing_prepared:
                self.patching.validate_layer(take.track, take.source)
            else:
                self.patching.layer_ready(take.track, take.source)
                take.routing_prepared = True
                self.patching.forget_layer(take.track, take.source)
            if self.patching.owners(take.source) & ~(1 << take.group):
                raise ValueError('instrument belongs to another playing group')

    @staticmethod
    def _quiet(track):
        return (track.playing_slot_index in (-1, -2) and track.fired_slot_index == -1
                and not any(slot.is_triggered or slot.has_clip and
                    (slot.clip.is_recording or slot.clip.is_playing) for slot in track.clip_slots))

    def _ensure_row(self, row):
        while len(self.song.scenes) <= row:
            count = len(self.song.scenes)
            self.song.create_scene(-1)
            if len(self.song.scenes) <= count:
                raise RuntimeError('Live did not create the requested scene')

    def _allocate(self, group, row, exclude=(), reuse=None, source=None):
        item = self._group(group)
        self._ensure_row(row)
        item = self._group(group, item.group)
        # Never appropriate a manual track as a recording destination. Only
        # this transaction's own emptied Retry track or its own failed creation
        # may be reused; every other take starts from Live's Audio Track defaults.
        candidates = [reuse] if reuse is not None else []
        key = group if source is None else (group, source.index)
        failed = self._failed_allocations.get(key)
        if failed is not None:
            parent, created = failed
            if same(parent, item.group) and any(same(created, t) for t in self.song.tracks):
                candidates.append(created)
            else:
                self._failed_allocations.pop(key, None)
                failed = None
        for track in candidates:
            if (any(same(track, x) for x in exclude)
                    or not any(same(track, t) for t in item.tracks)
                    or not (getattr(track, 'has_midi_input', False) if source is not None and source.midi
                            else getattr(track, 'has_audio_input', False))
                    or getattr(track, 'is_frozen', False) or getattr(track, 'arm', False)
                    or not self._quiet(track)):
                continue
            slots = tuple(track.clip_slots)
            if row < len(slots) and not slots[row].has_clip and not slots[row].is_triggered:
                self._failed_allocations.pop(key, None)
                if source is not None:
                    self.patching.start_layer(track, source)
                return item.group, track, slots[row]
        if failed is not None:
            raise RuntimeError('previous layer track still needs placement in the remote group')
        tracks = tuple(self.song.tracks)
        index = next(i for i, t in enumerate(tracks) if same(t, item.group)) + 1
        # Inserting immediately after the Group Track is the public creation
        # route. Never write read-only group_track or assume insertion worked.
        if source is not None and source.midi:
            self.song.create_midi_track(index)
        else:
            self.song.create_audio_track(index)
        after = tuple(self.song.tracks)
        created = [t for t in after if not any(same(t, old) for old in tracks)]
        if len(created) != 1:
            raise RuntimeError('Live did not create one identifiable layer track')
        track = created[0]
        fresh = self._group(group, item.group)
        if (not any(same(track, t) for t in fresh.tracks)
                or not (getattr(track, 'has_midi_input', False) if source is not None and source.midi
                        else getattr(track, 'has_audio_input', False))
                or row >= len(track.clip_slots) or track.clip_slots[row].has_clip):
            # Preserve this new track: user defaults may contain devices or
            # other content. Failed insertion is never permission to delete.
            self._failed_allocations[key] = (item.group, track)
            raise RuntimeError('new audio track was not inserted into the remote group')
        if source is not None:
            # Factory names such as Audio or MIDI may match a source name.
            # Name only this new destination before resolving unique routes.
            base = 'Layer ' + source.name
            name, suffix = base, 2
            while any(str(other.name) == name for other in self.song.tracks if not same(other, track)):
                name, suffix = base + ' ' + str(suffix), suffix + 1
            track.name = name
            self._failed_allocations[key] = (item.group, track)
            self.patching.start_layer(track, source)
            self._failed_allocations.pop(key, None)
        # Legacy sets retain their factory routing; patched sets explicitly use
        # the selected source and keep destination monitoring off.
        return item.group, track, track.clip_slots[row]

    def _target(self, reference, grid, at, inclusive=False):
        return (reference.next_wrap(at, inclusive) if reference is not None
                else grid.next_bar(at, inclusive))

    def _window(self, target, grid):
        options = []
        if 'q_bar' in self.quantizations and abs(math.remainder(target-grid.origin, grid.length)) < EPSILON:
            options.append((grid.length, self.quantizations['q_bar']))
        for name, period in PERIODS:
            if name in self.quantizations and abs(math.remainder(target, period)) < EPSILON:
                options.append((period, self.quantizations[name]))
        if not options:
            raise ValueError('loop boundary is outside the available native launch grids')
        period, quantum = max(options, key=lambda pair: pair[0])
        return target-period, quantum

    def _plan(self, reference, at=None, inclusive=False):
        admission = LaunchAdmission(self.song, clock=self.monotonic)
        grid = read_grid(self.song)
        position = admission.position(LEAD)
        if at is not None:
            position = max(position, at)
        target = self._target(reference, grid, position, inclusive)
        self._window(target, grid)
        return target

    def _make(self, group, reference, row, exclude=(), reuse=None, monitor_job=None):
        sources = (None,)
        if self.patching is not None and self.patching.enabled:
            sources = self.patching.selected(group)
            if not sources:
                raise ValueError('remote group has no patched inputs')
            for source in sources:
                self.patching.source(source.index)
                if self.patching.owners(source) & ~(1 << group):
                    raise ValueError('instrument belongs to another playing group')
            if monitor_job is None:
                monitor_job = self.patching.start_monitor(group)
        allocated = []
        try:
            for source in sources:
                candidate = reuse
                if isinstance(reuse, tuple):
                    candidate = next((item.track for item in reuse if item.source is not None
                                      and source is not None and same(item.source.track, source.track)), None)
                parent, track, slot = self._allocate(group, row, exclude, candidate, source)
                allocated.append((source, parent, track, slot))
            target = self._plan(reference)
        except Exception:
            # Allocation is not musical ownership. Preserve new default devices,
            # and reuse these still-empty tracks on the next explicit attempt.
            for source, parent, track, slot in allocated:
                key = group if source is None else (group, source.index)
                self._failed_allocations[key] = (parent, track)
            raise
        token = self._new_token()
        takes = [Take(group, parent, track, slot, row, token, reference,
                      queued_at=float(self.song.current_song_time), target=target, source=source,
                      monitor_job=monitor_job)
                 for source, parent, track, slot in allocated]
        takes[0].companions = tuple(takes[1:])
        return takes[0]

    def begin(self, group):
        self.reconcile()
        if not bool(self.song.is_playing):
            return p.REJECTED
        if self.take is not None and self.phase() != 'completed' or group in self._advance:
            return p.BUSY
        if self.patching is not None and self.patching.enabled:
            if not self.patching.selected(group):
                return p.NOT_READY
        item = self._group(group)
        reference = self.state.reference(group)
        if reference is None:
            row = self._preferred_rows.get(group, item.fresh_row)
            row = item.next_free_row(row)
        else:
            row = next(c.row for c in item.playing if same(c.clip, reference.clip))
        self.take = self._make(group, reference, row)
        self._preferred_rows.pop(group, None)
        return p.OUTCOME_QUEUED

    def accept(self, group, next_take=False):
        self.reconcile()
        take = self.take
        if take is None or take.group != group or self.phase(take) != 'recording':
            return p.REJECTED
        members = [item for item in self.members(take) if item.state == 'recording']
        for item in members:
            self._identity(item)
        if not all(item.clip is not None and item.clip.is_recording for item in members) or not bool(self.song.is_playing):
            return p.NOT_READY
        finish = self._plan(None)
        representative = self.representative(take)
        following = None
        if next_take:
            # Each new recording obtains a fresh Live reference. An unfinished
            # recording cannot itself win leftmost-reference discovery.
            reference = self.state.reference(group, exclude=representative.clip)
            if reference is None:
                length = finish-representative.anchor
                reference = LoopReference(representative.clip, length, 0., finish)
            following = self._make(group, reference, take.row,
                                   tuple(item.track for item in self.members(take)), monitor_job=take.monitor_job)
            target = reference.next_wrap(finish, inclusive=True)
            self._window(target, read_grid(self.song))
            for item in self.members(following):
                item.queued_at, item.target = finish, target
        for item in members:
            item.finish_target, item.state = finish, 'finish_queued'
        take.finish_target = finish
        take.following = following
        return p.OUTCOME_QUEUED

    def cancel(self, command, retry=False):
        self.reconcile()
        take = self.take
        if take is None or take.group != command.group or self.phase(take) == 'completed':
            return p.REJECTED
        if not take.companions:
            self._identity(take)
        take.retry_reference = self.state.reference(take.group, exclude=take.clip) if retry else None
        for item in self.members(take):
            if item.state == 'completed':
                continue
            item.state = 'canceling'
            item.stop_issued, item.delete_issued, item.discard = False, False, None
        take.continuation = 'retry' if retry else None
        take.pending_command = command
        return None

    def advance(self, command):
        self.reconcile()
        if self.take is not None and self.phase() != 'completed' or self._advance:
            return p.BUSY
        item = self._group(command.group)
        row = item.fresh_row
        self._ensure_row(row)
        # Select the row before stopping. Retain only this pending operation,
        # and preserve every existing clip regardless of where it is playing.
        self._advance[command.group] = dict(parent=item.group, row=row,
                                           command=command, stopped=[])
        return None

    def acknowledge(self, group, take_id):
        take = self.take
        if take is None or self.phase(take) != 'completed':
            return p.APPLIED
        if take.group != group or take.take_id != take_id:
            return p.STALE
        self._disarms.extend(item for item in self.members(take) if item.armed_here)
        self.take = None
        return p.APPLIED

    def _adopt(self, group, item):
        grid = read_grid(self.song)
        anchor = item.clip.start_time
        if not number(anchor):
            raise ValueError('recording start is unavailable')
        take = Take(group, self.state.groups[group].group, item.track, item.slot,
                    item.row, self._new_token(), self.state.reference(group, exclude=item.clip),
                    state='recording', clip=item.clip, anchor=float(anchor),
                    bar=grid.length, numerator=grid.numerator, denominator=grid.denominator,
                    adopted=True, start_issued=True, target=float(anchor))
        self.take = take
        self._event('recording_adopted', take)

    def reconcile(self):
        self._refresh()
        take = self.take
        if take is not None and take.companions:
            self._reconcile_bundle(take)
            return
        actual = self.state.recording()
        if (actual is not None and take is not None
                and take.state in ('queued', 'recording')
                and not same(actual[1].clip, take.clip)
                and not (take.start_issued and take.clip is None and same(actual[1].slot, take.slot))
                and not (take.following is not None and same(actual[1].clip, take.following.clip))):
            # A real take started/selected in Live outranks an unissued UI
            # intention. Dropping that intention does not stop native audio.
            self._adopt(*actual)
            take = self.take
        if take is not None and take.state not in ('completed', 'canceling'):
            try:
                self._identity(take)
                if take.slot.has_clip:
                    if take.clip is None:
                        take.clip = take.slot.clip
                    if take.clip.is_recording:
                        self._observe(take)
                        if not take.adopted or take.state != 'recording':
                            return
                    if take.state == 'queued' and take.start_issued:
                        # External replacement/playback wins; do not adopt it
                        # as an owned recording or repeat a prior launch.
                        self.take = None
                    elif take.clip is not None:
                        self._observe(take)
                elif take.clip is not None:
                    self.take = None
                elif (take.state == 'queued' and take.start_issued
                      and self.song.current_song_time > take.target + read_grid(self.song).length
                      and not take.slot.is_triggered and take.track.fired_slot_index == -1):
                    # Explicit Native cancellation of a queued empty launch.
                    # Accessors remain read-only; tick handles our arm cleanup.
                    take.state = 'completed'
            except (AttributeError, RuntimeError, ValueError, IndexError):
                self.take = None
        take = self.take
        if take is not None and take.state == 'completed' and take.following is not None:
            if take.armed_here:
                self._disarms.append(take)
            self.take = take = take.following
            self._observe(take)
        if take is None or take.state == 'completed':
            recording = self._refresh().recording()
            if recording is not None:
                self._adopt(*recording)

        elif take.adopted and take.state == 'recording':
            # Selecting another currently recording group in Live selects the
            # same group on the controller, without stopping either recording.
            recording = self.state.recording()
            if recording is not None and not same(recording[1].clip, take.clip):
                self._adopt(*recording)

    def _reconcile_bundle(self, take):
        """Observe every destination without treating a peer as a foreign take."""
        if self.phase(take) == 'canceling':
            return
        for item in self.members(take):
            if item.state != 'completed':
                try:
                    self._identity(item)
                except (AttributeError, RuntimeError, ValueError, IndexError) as error:
                    # A disappeared/moved/replaced destination belongs to Live
                    # now. Retire only this member without touching its music.
                    item.failure = str(error)
                    item.state, item.armed_here = 'completed', False
                    continue
                try:
                    self._observe(item)
                except (AttributeError, RuntimeError, ValueError, IndexError) as error:
                    # A temporarily unavailable clock or occupied unissued
                    # reservation does not transfer ownership of another clip.
                    item.failure = str(error)
                    continue
                if (item.state == 'queued' and item.start_issued and not item.slot.has_clip
                        and self.song.current_song_time > item.target + read_grid(self.song).length
                        and not item.slot.is_triggered and item.track.fired_slot_index == -1):
                    item.state = 'completed'
        following = take.following
        if self.phase(take) == 'completed' and following is not None:
            self._disarms.extend(item for item in self.members(take) if item.armed_here)
            self.take = following
            for item in self.members(following):
                self._observe(item)
        elif self.phase(take) == 'completed':
            recording = self.state.recording()
            if recording is not None:
                self._adopt(*recording)
    def _observe(self, take, effects=False):
        self._identity(take)
        if not take.start_issued and not take.adopted:
            if take.slot.has_clip or take.slot.is_triggered:
                raise ValueError('unissued recording reservation was occupied externally')
            return
        if take.slot.has_clip and take.clip is None:
            take.clip = take.slot.clip
        clip = take.clip
        if clip is None:
            return
        if clip.is_recording:
            if (take.finish_issued and take.finish_target is not None
                    and self.song.current_song_time > take.finish_target + (take.bar or 0)
                    and not take.slot.is_triggered and take.track.fired_slot_index == -1):
                # The Native request has disappeared while the take still
                # records. Require a NEW user Accept instead of replaying it.
                take.finish_target, take.finish_issued = None, False
                take.following, take.state = None, 'recording'
                self._event('finish_not_observed', take)
            if take.anchor is None:
                grid = read_grid(self.song)
                if not number(clip.start_time):
                    raise ValueError('actual recording anchor unavailable')
                take.anchor, take.bar = float(clip.start_time), grid.length
                take.numerator, take.denominator = grid.numerator, grid.denominator
                take.target = take.anchor
                take.state = 'recording'
                self._event('recording_observed', take)
            return
        if take.state in ('recording', 'finish_queued'):
            # A stop/accept made directly in Live also completes this take.
            # Only an explicitly issued finish may normalize its fresh file.
            if take.finish_issued and take.finish_target is not None and self._normalizable(take):
                if not effects and abs((clip.loop_end-clip.loop_start)-(take.finish_target-take.anchor)) > EPSILON:
                    return
                if effects:
                    self._normalize(take)
            if effects and take.armed_here:
                take.track.arm = False
                take.armed_here = False
            take.state = 'completed'
            self._event('recording_finished', take)

    @staticmethod
    def _normalizable(take):
        clip = take.clip
        # Native playback anchor must confirm THIS finish, and unchanged
        # fresh markers must still agree. A user stop/relaunch/loop edit wins.
        return (bool(getattr(clip, 'is_audio_clip', False))
                and number(getattr(clip, 'start_time', None))
                and abs(clip.start_time-take.finish_target) <= EPSILON
                and clip.loop_start == 0
                and number(getattr(clip, 'end_marker', None))
                and abs(clip.loop_end-clip.end_marker) <= EPSILON)

    def _normalize(self, take):
        clip = take.clip
        expected = take.finish_target - take.anchor
        length = clip.loop_end - clip.loop_start
        if not all(number(v) for v in (expected, length)) or expected <= 0:
            raise ValueError('recording length unavailable')
        if abs(length-expected) <= EPSILON:
            return
        if take.normalized:
            raise ValueError('Live did not confirm normalized loop length')
        if not clip.is_audio_clip or not clip.warping or not clip.looping or clip.loop_start != 0:
            raise ValueError('fresh recording loop cannot be normalized')
        markers = tuple((point['sample_time'], point['beat_time']) if isinstance(point, dict)
                        else (point.sample_time, point.beat_time) for point in clip.warp_markers)
        if not sample_covers(expected, clip.sample_length, clip.sample_rate, markers):
            raise ValueError('recorded file does not cover requested duration')
        self._identity(take)
        if clip.is_recording:
            raise ValueError('recording resumed before normalization')
        take.normalized = True
        clip.loop_end = expected
        if abs(clip.loop_end-expected) > EPSILON:
            raise ValueError('Live did not confirm normalized length')

    def _defer(self, take, finish=False, admission=None):
        if finish:
            target = self._plan(None, at=take.finish_target)
            for member in self.members(take):
                member.finish_target = target
            following = take.following
            if following is not None and not following.start_issued:
                if following.reference is not None and same(following.reference.clip, take.clip):
                    following.reference = LoopReference(take.clip, target-take.anchor, 0., target)
                for member in self.members(following):
                    member.reference = following.reference
                    member.target = self._target(following.reference, read_grid(self.song), target, True)
                    member.queued_at = target
        else:
            target = self._plan(take.reference, at=take.target)
            for member in self.members(take):
                member.target = target
        self._event('recording_boundary_deferred', take, finish=finish)

    def _execute(self, take, finish=False):
        if take.companions:
            return self._execute_bundle(take, finish)
        if not bool(self.song.is_playing):
            return

        if (take.finish_issued if finish else take.start_issued):
            return
        for unused in range(3):
            grid = read_grid(self.song)
            admission = LaunchAdmission(self.song, clock=self.monotonic)
            target = take.finish_target if finish else take.target
            previous, quantum = self._window(target, grid)
            if grid.position <= previous + EPSILON:
                return
            try:
                admission.require(target, LEAD)
            except ValueError:
                self._defer(take, finish, admission)
                continue
            self._identity(take, empty=not finish)
            if not finish:
                self._source_ready(take)
            if finish:
                if take.clip is None or not take.clip.is_recording:
                    return
            else:
                if getattr(take.track, 'is_frozen', False) or not self._quiet(take.track):
                    raise ValueError('recording track is no longer quiet')
                if not take.armed_here:
                    if take.track.arm:
                        raise ValueError('recording track was armed externally')
                    take.armed_here = True
                    take.track.arm = True
                # Arming can synchronously run other Remote Scripts.
                self._identity(take, empty=True)
                self._source_ready(take)
            try:
                admission.require(target, FIRE_LEAD)
            except ValueError:
                if not finish and take.armed_here:
                    take.track.arm = False
                    take.armed_here = False
                self._defer(take, finish, admission)
                continue
            if finish:
                take.finish_issued = True
            else:
                take.start_issued = True
            # No native reads between the final lead check and enqueue.
            take.slot.fire(launch_quantization=quantum)
            self._event('finish_issued' if finish else 'start_issued', take)
            return


    def _execute_bundle(self, take, finish=False):
        members = tuple(item for item in self.members(take)
                        if not finish or item.state == 'finish_queued')
        issued = 'finish_issued' if finish else 'start_issued'
        if not members or not self.song.is_playing or any(getattr(item, issued) for item in members):
            return
        for unused in range(3):
            grid = read_grid(self.song)
            target = take.finish_target if finish else take.target
            previous, quantum = self._window(target, grid)
            if grid.position <= previous + EPSILON:
                return
            admission = LaunchAdmission(self.song, clock=self.monotonic)
            try:
                admission.require(target, LEAD)
            except ValueError:
                self._defer(take, finish, admission)
                continue
            for item in members:
                self._identity(item, empty=not finish)
                if not finish:
                    self._source_ready(item)
                if finish:
                    if item.clip is None or not item.clip.is_recording:
                        raise ValueError('a recording destination finished externally')
                elif getattr(item.track, 'is_frozen', False) or not self._quiet(item.track):
                    raise ValueError('recording track is no longer quiet')
                elif not item.armed_here and item.track.arm:
                    raise ValueError('recording track was armed externally')
            if not finish:
                for item in members:
                    item.armed_here = True
                    item.track.arm = True
                for item in members:
                    self._identity(item, empty=True)
                    self._source_ready(item)
            try:
                admission.require(target, FIRE_LEAD)
            except ValueError:
                if not finish:
                    for item in members:
                        item.track.arm, item.armed_here = False, False
                self._defer(take, finish, admission)
                continue
            # Reserve the entire batch before the first Native mutation. If
            # one fire raises, none of this potentially issued batch is replayed.
            for item in members:
                setattr(item, issued, True)
            for index, item in enumerate(members):
                try:
                    item.slot.fire(launch_quantization=quantum)
                except Exception:
                    # Later calls were never made. They need no launch replay
                    # and must not leave empty, armed phantom recordings.
                    if not finish:
                        for uncalled in members[index + 1:]:
                            uncalled.start_issued = False
                            uncalled.state = 'completed'
                            if uncalled.armed_here:
                                uncalled.track.arm = False
                                uncalled.armed_here = False
                    else:
                        for uncalled in members[index + 1:]:
                            uncalled.finish_issued = False
                            uncalled.finish_target = None
                            uncalled.state = 'recording'
                        # No successor may launch while an earlier peer still
                        # records. Retain the empty tracks for explicit Retry.
                        following = take.following
                        for reserved in self.members(following):
                            key = (reserved.group, reserved.source.index) if reserved.source is not None else reserved.group
                            self._failed_allocations[key] = (reserved.parent, reserved.track)
                        take.following = None
                    raise
                self._event('finish_issued' if finish else 'start_issued', item)
            return

    def _cancel_one(self, take):
        self._identity(take)
        if not take.start_issued and not take.adopted:
            # A reserved empty slot is not musical ownership. A manual clip or
            # launch that arrives here belongs to Live; release this intention
            # without stopping, adopting, deleting or disarming its track.
            if (take.slot.has_clip or take.slot.is_triggered
                    or not self._quiet(take.track)):
                return True
            if take.armed_here:
                take.track.arm = False
                take.armed_here = False
            return True
        allowed = (-1, -2, take.row)
        if take.track.playing_slot_index not in allowed or take.track.fired_slot_index not in allowed:
            raise ValueError('another clip replaced the pending recording')
        if not take.stop_issued:
            clip = take.slot.clip if take.slot.has_clip else None
            if clip is not None:
                if take.clip is not None and not same(take.clip, clip):
                    raise ValueError('cancel destination changed')
                if not clip.is_recording:
                    # Actual completion wins a late Cancel. Preserve the clip.
                    if take.armed_here:
                        take.track.arm = False
                        take.armed_here = False
                    return True
                take.clip = take.discard = clip
            if not take.start_issued and clip is None:
                if take.armed_here:
                    take.track.arm = False
                    take.armed_here = False
                return True
            take.stop_issued = True
            take.track.stop_all_clips(False)
        if not self._quiet(take.track):
            return False
        if take.discard is not None:
            if not take.slot.has_clip or not same(take.slot.clip, take.discard):
                raise ValueError('cancel clip changed before deletion')
            if take.discard.is_recording or take.discard.is_playing:
                return False
            if take.delete_issued:
                raise ValueError('Live has not confirmed the requested take deletion')
            take.delete_issued = True
            take.slot.delete_clip()
            if take.slot.has_clip:
                raise ValueError('Live did not confirm take deletion')
            take.discard = None
        if take.armed_here or take.adopted:
            take.track.arm = False
            take.armed_here = False
        return True

    def _cancel(self, take):
        all_done = True
        for item in self.members(take.following) + self.members(take):
            if item.state != 'completed':
                done = self._cancel_one(item)
                if done:
                    item.state = 'completed'
                all_done = done and all_done
        if not all_done:
            return
        if self.patching is not None:
            for member in self.members(take.following) + self.members(take):
                if member.source is not None:
                    self.patching.cancel_layer(member.track)
        command, retry = take.pending_command, take.continuation == 'retry'
        reference, group, row = take.retry_reference, take.group, take.row
        if retry and reference is not None:
            current = self._refresh().groups[group]
            row = next((item.row for item in current.playing if same(item.clip, reference.clip)), row)
        self.take = None
        if retry:
            self.take = self._make(group, reference, row, reuse=(self.members(take)
                                  if take.companions else take.track) if not take.adopted else None,
                                  monitor_job=take.monitor_job)
        if command is not None:
            self._completions.append((command, p.APPLIED))

    def _advance_tick(self, group, plan):
        item = self._group(group, plan['parent'])
        for track in item.tracks:
            if not any(same(track, t) for t in plan['stopped']):
                plan['stopped'].append(track)
                track.stop_all_clips(False)
        item = self._group(group, plan['parent'])
        if all(self._quiet(track) for track in item.tracks):
            row = item.next_free_row(plan['row'])
            self._ensure_row(row)
            self._preferred_rows[group] = row
            del self._advance[group]
            self._completions.append((plan['command'], p.APPLIED))

    def tick(self):
        try:
            self.reconcile()
        except Exception as error:
            self.issues['state'] = str(error)
            return self._drain()
        for completed in tuple(self._disarms):
            try:
                self._identity(completed)
                if completed.clip is not None and not completed.clip.is_recording:
                    completed.track.arm = False
                    completed.armed_here = False
            except (AttributeError, RuntimeError, ValueError):
                pass
            self._disarms.remove(completed)
        for group, plan in tuple(self._advance.items()):
            try:
                self._advance_tick(group, plan)
            except Exception as error:
                self.issues[group] = str(error)
                del self._advance[group]
                self._completions.append((plan['command'], p.NOT_READY))
        take = self.take
        if take is None:
            return self._drain()
        if self.phase(take) == 'completed':
            for take in self.members(take):
                if not take.armed_here:
                    continue
                try:
                    self._identity(take)
                    take.track.arm = False
                    take.armed_here = False
                except (AttributeError, RuntimeError, ValueError):
                    pass
            return self._drain()
        try:
            if self.phase(take) == 'canceling':
                self._cancel(take)
            else:
                for member in self.members(take):
                    if member.state != 'completed':
                        self._observe(member, effects=True)
                following = take.following
                if self.phase(take) == 'finish_queued':
                    self._execute(take, finish=True)
                if following is not None and all(item.finish_issued for item in self.members(take)):
                    for member in self.members(following):
                        self._observe(member)
                    self._execute(following)
                if self.phase(take) == 'queued':
                    self._execute(take)
                if self.phase(take) == 'completed' and following is not None:
                    self.take = following
                take.failure = None
                self.issues.pop(take.group, None)
        except PendingRouting:
            # Preparation is neither a failed musical action nor permission
            # to arm/fire. Retain its reference and defer only unissued starts.
            if self.phase(take) == 'queued' and not take.start_issued and self.song.is_playing:
                if take.target <= self.song.current_song_time + LEAD:
                    self._defer(take)
            return self._drain()
        except Exception as error:
            take.failure = str(error)
            self.issues[take.group] = str(error)
            # A failed issued call may have taken effect. Observe Native state
            # on later ticks; never reset its issued flag or blindly replay it.
            self._event('recording_operation_unavailable', take, reason=str(error))
            if take.pending_command is not None:
                self._completions.append((take.pending_command, p.NOT_READY))
                take.pending_command = None
                take.continuation = None
                # Release a failed Cancel intent back to actual Native state.
                # Do not leave the UI trapped in canceling or replay deletes.
                for member in self.members(take):
                    if member.slot.has_clip and same(member.slot.clip, member.clip):
                        member.state = 'recording' if member.clip.is_recording else 'completed'
                    else:
                        member.state = ('queued' if member.start_issued and not member.slot.has_clip
                                        and member.state != 'completed' else 'completed')
        return self._drain()

    def _drain(self):
        events, self._completions = self._completions, []
        return events

    def needs_tick(self):
        return bool(self._advance) or bool(self._disarms) or self.take is not None and self.phase() not in ('recording', 'completed')
