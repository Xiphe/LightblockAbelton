"""Original Audio sources, Set-local choices and prepared recording lanes.

Discovery and Native preparation are maintenance work. Touch commands change
cached intent only; actual recording lanes provide monitoring without helpers.
"""
from dataclasses import dataclass
import json
from .model import same, live_valid, NATIVE_ERRORS, native_error
from .audio_patch_state import AudioPatchState

OFF, IN, AUTO = 2, 0, 1
MAX_SOURCES = 7
PREPARED_KEY = 'net.xiphe.lightpad.audio.prepared-lane.v1'


def label(value):
    return str(getattr(value, 'display_name', value))


def route_equal(left, right):
    return same(left, right)


def source_route(route, source):
    return same(getattr(route, 'attached_object', None), source.track)


def post_fx(channel):
    return label(channel).casefold().replace(' ', '') == 'postfx'


@dataclass(frozen=True)
class Source:
    index: int
    track: object
    name: str
    color: int
    color_index: int
    route: object
    channel: object = None


@dataclass(frozen=True)
class Destination:
    source: Source
    track: object
    slot: object


@dataclass
class BundleReserve:
    group: int
    parent: object
    row: int
    revision: int
    members: tuple
    @property
    def track(self): return self.members[0].track
    @property
    def slot(self): return self.members[0].slot


class AudioInputs:
    def __init__(self, backend, specs):
        self.backend, self.song = backend, backend.song
        self.state_store = AudioPatchState()
        self.sources, self.issues = {}, {}
        self.catalog_tracks = ()
        self._source_candidates = {}
        self.desired, self.revisions = {}, {}
        self.confirmed, self.last_ready, self._intents = {}, {}, {}
        self._owner_revision = 0
        self.monitor_owner = None
        self.jobs, self.prepared_masks = {}, {}
        self.busy, self.failed = set(), set()
        self._available = 0
        self._observe_at = -10.
        self.root = None
        self._storage_errors = ()
        self.discover()

    @staticmethod
    def _within(track, root):
        parent = getattr(track, 'group_track', None)
        for unused in range(32):
            if parent is None: return False
            if same(parent, root): return True
            parent = getattr(parent, 'group_track', None)
        return False

    def _prune_groups(self):
        live_groups = set(self.backend.state.groups)
        for mapping in (self.desired, self.revisions, self.confirmed, self.last_ready,
                        self._intents, self.jobs, self.prepared_masks,
                        self.backend.reserves, self.backend.failures):
            for group in tuple(mapping):
                if group not in live_groups:mapping.pop(group, None)
        self.busy.intersection_update(live_groups)
        self.failed.intersection_update(live_groups)
        for intent in self._intents.values():
            if intent['previous_owner'] not in live_groups:intent['previous_owner'] = None

    def _prune_destinations(self):
        # A topology callback may remove an owned spare or a partially routed
        # lane. Retire those cached plans, never replay their Native writes.
        for group, item in self.backend.state.groups.items():
            def present(track):
                return live_valid(track) and any(same(track, current) for current in item.tracks)
            plans = [reserve for reserve in (self.backend.reserves.get(group),
                                             self.last_ready.get(group)) if reserve is not None]
            stale = any(not present(member.track) for plan in plans for member in plan.members)
            job = self.jobs.get(group)
            if job is not None:
                stale = stale or any(not present(member.track) for member in job['members'])
                stale = stale or job['track'] is not None and not present(job['track'])
            if not stale:continue
            self.backend.reserves.pop(group,None);self.last_ready.pop(group,None)
            self.jobs.pop(group,None);self.prepared_masks.pop(group,None)
            self.backend.failures.pop(group,None);self.failed.discard(group)
            self.busy.add(group)

    def discover(self):
        self._prune_groups()
        self._prune_destinations()
        tracks = self.backend.state.tracks
        roots = [t for t in tracks if t.is_foldable and str(t.name).split(' ', 1)[0] == '[Inputs]']
        self.root = roots[0] if len(roots) == 1 else None
        if self.root is None:
            self.sources = {}; self._source_candidates = {}; self._available = 0
            self.catalog_tracks = (); self.monitor_owner = None
            self.issues = {'catalog': 'unique [Inputs] group unavailable'}
            return
        catalog = tuple(t for t in tracks if not t.is_foldable and t.has_audio_input
                        and self._within(t, self.root))
        if len(catalog) > MAX_SOURCES:
            raise ValueError('Audio catalog exceeds seven supported sources')
        catalog_changed = (len(catalog) != len(self.catalog_tracks) or
                           any(not same(a, b) for a, b in zip(catalog, self.catalog_tracks)))
        self.catalog_tracks = catalog
        old_ids = self.state_store.source_ids
        confirmed_ids = {g: tuple(identifier for i, identifier in enumerate(old_ids) if mask & (1 << i))
                         for g, mask in self.confirmed.items()}
        self.state_store.bind(self.root, catalog, {g: item.group for g, item in self.backend.state.groups.items()})
        previous = tuple(self.sources.values())
        self.sources = {}; self._source_candidates = {}; self.issues = dict(self.state_store.issues)
        probes = [t for item in self.backend.state.groups.values() for t in item.tracks
                  if not t.is_foldable and t.has_audio_input]
        for i, track in enumerate(catalog):
            if i in self.state_store.duplicate_indices or i in self.state_store.invalid_indices: continue
            prior = next((s for s in previous if same(s.track, track)), None)
            if prior is not None:
                self.sources[i] = Source(i, track, str(track.name), int(track.color),
                                         int(getattr(track, 'color_index', 0)), prior.route)
            elif probes:
                self._source_candidates[i] = (track, probes[0])
        for group in self.backend.state.groups:
            wanted = self.state_store.mask(group)
            before = self.desired.get(group)
            self.desired[group] = wanted
            self.confirmed[group] = (sum(1 << i for i, identifier in enumerate(self.state_store.source_ids)
                                         if identifier in confirmed_ids[group])
                                     if group in confirmed_ids else wanted)
            self.revisions.setdefault(group, 0)
            if before is not None and (before != wanted or catalog_changed):
                self.revisions[group] += 1
                if group in self._intents:self._intents[group]['revision'] = self.revisions[group]
                self.backend.reserves.pop(group, None); self.jobs.pop(group, None)
                self.prepared_masks.pop(group, None)
                self.busy.add(group)
        self.monitor_owner = self.state_store.monitor_owner
        self.observe_sources(force=True)

    def _recover_prepared_ownership(self):
        # Saved arms belong to us only with our own marker and matching actual
        # source/parent/monitoring state. Unmarked manual arms stay untouched.
        for group, item in self.backend.state.groups.items():
            for track in item.tracks:
                try:
                    if not track.arm or track.is_foldable or not track.has_audio_input:continue
                    raw=track.get_data(PREPARED_KEY,'')
                    if not raw:continue
                    marker=json.loads(raw)
                    if marker.get('schema')!=1 or marker.get('group')!=group:continue
                    attached=getattr(track.input_routing_type,'attached_object',None)
                    matching=any(identifier==marker.get('source') and same(source,attached)
                                 for identifier,source in zip(self.state_store.source_ids,self.catalog_tracks))
                    if (matching and same(track.group_track,item.group) and post_fx(track.input_routing_channel)):
                        self.backend.monitoring.recover_saved(track,group)
                except (RuntimeError,AttributeError,ValueError,TypeError):continue

    def _mark_prepared_ownership(self, group, reserve):
        for member in reserve.members:
            identifier=self.state_store.source_ids[member.source.index]
            payload=json.dumps(dict(schema=1,group=group,source=identifier,row=reserve.row),
                               sort_keys=True,separators=(',',':'))
            if member.track.get_data(PREPARED_KEY,'')!=payload:
                member.track.set_data(PREPARED_KEY,payload)

    def _complete_source_profiles(self):
        offered = []
        for i, (source, probe) in self._source_candidates.items():
            if i in self.sources: continue
            try:
                cached = next((choices for target, choices in offered if same(target, probe)), None)
                if cached is None:
                    cached = tuple(probe.available_input_routing_types); offered.append((probe, cached))
                choices = [r for r in cached if same(getattr(r, 'attached_object', None), source)]
                if len(choices) != 1: continue
                self.sources[i] = Source(i, source, str(source.name), int(source.color),
                                         int(getattr(source, 'color_index', 0)), choices[0])
            except (RuntimeError, AttributeError, TypeError, ValueError): continue

    @property
    def available(self): return self._available

    def observe_sources(self, force=False):
        if not force and self.backend.now() - self._observe_at < .75: return
        self._observe_at = self.backend.now()
        self._complete_source_profiles()
        before = self._available
        available = 0
        for i, source in tuple(self.sources.items()):
            try:
                if (not source.track.is_frozen and source.track.current_monitoring_state == IN
                        and label(source.track.output_routing_type).casefold().replace(' ', '') == 'sendsonly'
                        and all(p.value == p.min for p in source.track.mixer_device.sends)):
                    available |= 1 << i
                name, color = str(source.track.name), int(source.track.color)
                index = int(getattr(source.track, 'color_index', 0))
                if (name, color, index) != (source.name, source.color, source.color_index):
                    self.sources[i] = Source(i, source.track, name, color, index, source.route)
            except NATIVE_ERRORS as error:
                native_error(error)
                self.backend.state.topology_dirty=True
        self._available = available
        # Saved arm/routing properties may settle after constructor discovery.
        # Reconcile only our marked retained lanes on the slow source cadence.
        self._recover_prepared_ownership()
        if available & ~before:
            for group, reason in tuple(self.backend.failures.items()):
                wanted = self.desired.get(group, 0)
                if reason == 'no available recording source' and wanted and not wanted & ~available:
                    self.backend.failures.pop(group, None); self.failed.discard(group); self.busy.add(group)

    @property
    def group_mask(self):
        return sum(bool(self.desired.get(g, 0)) << g for g in self.backend.state.groups
                   if self.root is not None and not self.state_store.unresolved(g)
                   and self.state_store.selected_ids(g) is not None
                   and not self.desired.get(g, 0) & ~self.available)

    def set_patch(self, group, mask):
        old = self.desired.get(group, 0)
        if self.root is None or group not in self.backend.state.groups or mask & ~((1 << MAX_SOURCES) - 1):
            raise ValueError('Audio group or selection unavailable')
        if (mask & ~old) & ~self.available: raise ValueError('input unavailable')
        self.state_store.set_patch(group, mask)
        if old != mask:
            self.desired[group] = mask; self.revisions[group] = self.revisions.get(group, 0) + 1
            self.backend.reserves.pop(group, None); self.jobs.pop(group, None)
            self.prepared_masks.pop(group, None)
        self.failed.discard(group); self.busy.add(group)
        self._owner_revision += 1
        self._intents[group] = dict(revision=self.revisions.get(group, 0),
                                  previous_owner=self.monitor_owner, owner_revision=self._owner_revision)
        self.monitor_owner = group
        self.state_store.set_monitor(group)
        return mask

    def set_monitor(self, group):
        if self.root is None or group not in self.backend.state.groups: raise ValueError('monitor group unavailable')
        self._owner_revision += 1; self.monitor_owner = group
        self.state_store.set_monitor(group)
        return group

    def flush_state(self):
        if self.root is None or self.busy: return
        result = self.state_store.flush(max_writes=2)
        if result.errors != self._storage_errors:
            self._storage_errors = result.errors
            if result.errors:
                self.backend.events.append(dict(event='patch_storage_failed', errors=result.errors))

    def selected(self, group):
        mask = self.desired.get(group, 0)
        if self.root is None or group not in self.backend.state.groups:
            raise ValueError('Audio group unavailable')
        if (not mask or mask & ~self.available or self.state_store.unresolved(group)
                or self.state_store.selected_ids(group) is None):
            raise ValueError('no available recording source')
        return tuple(self.sources[i] for i in range(len(self.catalog_tracks)) if mask & (1 << i))

    def routing_fact(self, group):
        return (self.desired.get(group, 0) | (self.prepared_masks.get(group, 0) << 7)
                | (int(group in self.busy) << 14) | (int(group in self.failed) << 15))

    def available_lane(self, track, parent, row):
        try:
            return (same(getattr(track, 'group_track', None), parent)
                and not track.is_foldable and track.has_audio_input and not track.is_frozen
                and (not track.arm or self.backend.monitoring.owned(track))
                and self.backend.recording._quiet(track)
                and 0 <= row < len(track.clip_slots)
                and not track.clip_slots[row].has_clip and not track.clip_slots[row].is_triggered)

        except NATIVE_ERRORS as error:
            native_error(error);self.backend.state.topology_dirty=True;return False

    def compatible(self, track, parent, row, source):
        try:
            return (self.available_lane(track, parent, row)
                and (track.current_monitoring_state == OFF or
                     track.current_monitoring_state == AUTO and self.backend.monitoring.owned(track))
                and source_route(track.input_routing_type, source) and post_fx(track.input_routing_channel))

        except NATIVE_ERRORS as error:
            native_error(error);self.backend.state.topology_dirty=True;return False

    def validate(self,reserve,group,row):
        item=self.backend.state.groups[group]
        if (reserve.row!=row or reserve.revision!=self.revisions.get(group)
            or not same(reserve.parent,item.group)):return False
        selected=self.selected(group)
        if (len(selected)!=len(reserve.members) or any(
                source.index!=member.source.index or not same(source.track,member.source.track)
                for source,member in zip(selected,reserve.members))):return False
        try:
            return all(any(same(m.track,t) for t in item.tracks)
                   and same(m.slot,m.track.clip_slots[row])
                   and self.compatible(m.track,item.group,row,m.source) for m in reserve.members)
        except NATIVE_ERRORS as error:
            native_error(error);self.backend.state.topology_dirty=True;return False

    def excluded(self,active):
        result=[]
        if active is not None and active.state!='completed':
            for take in (active,active.following):
                if take is not None:result.extend(t.track for t in (take,)+tuple(getattr(take,'members',())))
        return result

    def _confirm_reserve(self, group, reserve):
        self.last_ready[group] = reserve
        self.confirmed[group] = self.desired[group]
        self._intents.pop(group, None)
        self.jobs.pop(group, None)
        self.busy.discard(group)
        self.failed.discard(group)

    def maintenance(self,group,row,active):
        b=self.backend;item=b.state.groups[group]
        attempted_revision=self.revisions.get(group,0)
        try:
            if not self.desired.get(group,0):
                self.jobs.pop(group,None);b.reserves.pop(group,None)
                self.prepared_masks[group]=0
                self.confirmed[group]=0;self.last_ready.pop(group,None);self._intents.pop(group,None)
                self.busy.discard(group);self.failed.discard(group)
                return
            selected=self.selected(group)
            reserve=b.reserves.get(group)
            if reserve is not None and self.validate(reserve,group,row):
                # A cancelled/deleted earlier slot can become free while a later
                # reserve is ready. Reorder only in maintenance, never dispatch.
                excluded=self.excluded(active)
                first=[]
                for track in item.tracks:
                    if (not any(same(track,t) for t in excluded)
                            and self.available_lane(track,item.group,row)):
                        first.append(track)
                        if len(first)==len(selected):break
                if len(first)==len(reserve.members) and all(
                        same(track,m.track) for track,m in zip(first,reserve.members)):
                    self._confirm_reserve(group,reserve);return
            b.reserves.pop(group,None)
            job=self.jobs.get(group)
            if job is None or job['revision']!=self.revisions.get(group) or job['row']!=row:
                job=dict(revision=self.revisions.get(group),row=row,stage='select',members=[],track=None,at=b.now())
                self.jobs[group]=job
            self.busy.add(group)
            excluded=self.excluded(active)+[m.track for m in job['members']]
            source=selected[len(job['members'])]
            if job['track'] is None:
                track=next((t for t in item.tracks
                            if not any(same(t,e) for e in excluded)
                            and self.available_lane(t,item.group,row)),None)
                if track is not None and self.compatible(track,item.group,row,source):
                    job['members'].append(Destination(source,track,track.clip_slots[row]))
                else:
                    created=track is None
                    if created:
                        if not b.allow_audio_track_creation:raise ValueError('prepared Audio pool unavailable')
                        track=b._create_audio_lane(group,row)
                        if track is None:return
                    job['track']=track;job['created']=created
                    job['stage']='monitor';job['at']=b.now();return
            else:
                track=job['track']
                if (not self.available_lane(track,item.group,row)
                    or not any(same(track,t) for t in item.tracks)):
                    raise ValueError('preparing Audio destination changed')
                stage=job['stage']
                if stage=='monitor':
                    # New Native tracks must finish their implicit parent setup.
                    # Existing lanes retain their output, effects and mixer state.
                    if job.get('created') and not b._parent_output(track,item.group):
                        if b.now()-job['at']>8:raise ValueError('parent routing preparation timeout')
                        return
                    job['stage']=stage='type'
                    if track.current_monitoring_state!=OFF:
                        track.current_monitoring_state=OFF;return
                if stage=='type':
                    job['stage']=stage='channel'
                    if not source_route(track.input_routing_type,source):
                        choices=[r for r in track.available_input_routing_types if source_route(r,source)]
                        if len(choices)!=1:raise ValueError('source route unavailable or ambiguous')
                        track.input_routing_type=choices[0];return
                if stage=='channel':
                    if not source_route(track.input_routing_type,source):
                        if b.now()-job['at']>8:raise ValueError('source routing confirmation timeout')
                        return
                    choices=[r for r in track.available_input_routing_channels if label(r).casefold().replace(' ','')=='postfx']
                    if not choices:
                        if b.now()-job['at']>8:raise ValueError('Post FX route unavailable')
                        return
                    if len(choices)!=1:raise ValueError('Post FX route ambiguous')
                    job['stage']='verify'
                    if not route_equal(track.input_routing_channel,choices[0]):
                        track.input_routing_channel=choices[0];return
                if not self.compatible(track,item.group,row,source):
                    if b.now()-job['at']>8:raise ValueError('source routing verification timeout')
                    return
                job['members'].append(Destination(source,track,track.clip_slots[row]));job['track']=None
            self.prepared_masks[group]=sum(1<<m.source.index for m in job['members'])
            if len(job['members'])==len(selected):
                for m in job['members']:
                    try:
                        if str(m.track.name)!=m.source.name:m.track.name=m.source.name
                        if int(m.track.color_index)!=m.source.color_index:m.track.color_index=m.source.color_index
                    except (RuntimeError,ValueError,AttributeError) as error:
                        b.events.append(dict(event='source_appearance_unavailable',group=group,reason=str(error)))
                if self.revisions.get(group,0)!=job['revision'] or self.jobs.get(group) is not job:return
                reserve=BundleReserve(group,item.group,row,job['revision'],tuple(job['members']))
                self._mark_prepared_ownership(group,reserve)
                b.reserves[group]=reserve
                self._confirm_reserve(group,reserve)
        except NATIVE_ERRORS + (ValueError,IndexError) as exc:
            native_error(exc)
            self.fail_preparation(group,str(exc),attempted_revision)
            b.events.append(dict(event='input_preparation_failed',group=group,reason=str(exc)))

    def fail_preparation(self,group,reason,revision=None):
        """Roll back only the still-current intent to its confirmed Live patch."""
        b=self.backend;job=self.jobs.get(group);intent=self._intents.get(group)
        current=self.revisions.get(group,0)
        if revision is not None and revision!=current:return
        if job is not None and job['revision']!=current:return
        self.jobs.pop(group,None);b.reserves.pop(group,None)
        wanted=self.confirmed.get(group,self.desired.get(group,0))
        if intent is not None and intent['revision']==current:
            self.desired[group]=wanted;self.revisions[group]=current+1
            if intent['owner_revision']==self._owner_revision:
                self.monitor_owner=intent['previous_owner'];self._owner_revision+=1
            self._intents.pop(group,None)
            self.state_store.set_patch(group,wanted)
            self.state_store.set_monitor(self.monitor_owner)
        self.failed.add(group);self.busy.discard(group);self.issues[group]=reason
        self.prepared_masks[group]=wanted
        prior=self.last_ready.get(group)
        if prior is not None:
            restored=BundleReserve(group,prior.parent,prior.row,self.revisions.get(group,0),prior.members)
            try:
                if self.validate(restored,group,prior.row):
                    b.reserves[group]=restored
                    # The old ready destination remains usable immediately.
                    b.failures.pop(group,None)
                    return
            except NATIVE_ERRORS + (ValueError,) as error:native_error(error)
        # Uncertain mutations stay latched; source-readiness failures alone may
        # clear when a retained source becomes available again.
        b.failures[group]=reason
