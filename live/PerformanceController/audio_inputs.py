"""Original Audio/MIDI sources, Set-local choices and prepared recording lanes.

Discovery and Native preparation are maintenance work. Touch commands change
cached intent only; actual recording lanes provide monitoring without helpers.
"""
from dataclasses import dataclass, replace
import json
import time
from .model import same, live_valid, NATIVE_ERRORS, native_error
from .audio_patch_state import AudioPatchState
from .input_contract import MAX_SOURCES
from .midi_routing import MidiRouting, midi_profile, midi_route_equal, endpoint

OFF, IN, AUTO = 2, 0, 1
PREPARED_KEY = 'net.xiphe.lightpad.audio.prepared-lane.v1'


class RoutingBudget:
    """At most four writes; check 4ms between writes, never preempt a call.

    Native discovery may itself exceed 4ms. Permit the first necessary write
    after discovery so a slow getter cannot starve this job indefinitely.
    """
    def __init__(self):
        self.started=time.monotonic();self.writes=0
    def available(self):
        return self.writes<4 and (self.writes==0 or time.monotonic()-self.started<.004)
    def more(self):
        return self.writes<4 and time.monotonic()-self.started<.004


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
    kind: str = 'audio'
    input_type: object = None
    input_channel: object = None
    instrument: object = None
    endpoint_name: object = None


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
        self._midi_proofs=[]
        self.midi = MidiRouting(self)
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
            self.midi.discover()
            return
        catalog = tuple(t for t in tracks if not t.is_foldable
                        and (t.has_audio_input or getattr(t,'has_midi_input',False))
                        and self._within(t, self.root))
        if len(catalog) > MAX_SOURCES:
            raise ValueError('Input catalog exceeds 25 supported sources')
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
            if not track.has_audio_input:
                try:
                    device,input_type,input_channel=midi_profile(track)
                    self.sources[i]=Source(i,track,str(track.name),int(track.color),
                        int(getattr(track,'color_index',0)),None,kind='midi',
                        input_type=input_type,input_channel=input_channel,instrument=device,endpoint_name='1-'+str(device.name))
                except ValueError as error:
                    # Keep an unavailable profile candidate so a later manual
                    # instrument-chain repair can heal on the slow cadence.
                    self.sources[i]=Source(i,track,str(track.name),int(track.color),
                        int(getattr(track,'color_index',0)),None,kind='midi')
                    self.issues[i]=str(error)
                except NATIVE_ERRORS as error:
                    native_error(error);self.issues[i]=str(error)
            elif prior is not None:
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
        self.midi.discover()
        self.observe_sources(force=True)

    def _recover_prepared_ownership(self):
        # Saved arms belong to us only with our own marker and matching actual
        # source/parent/monitoring state. Unmarked manual arms stay untouched.
        for group, item in self.backend.state.groups.items():
            for track in item.tracks:
                try:
                    if not track.arm or track.is_foldable:continue
                    raw=track.get_data(PREPARED_KEY,'')
                    if not raw:continue
                    marker=json.loads(raw)
                    if marker.get('schema')!=1 or marker.get('group')!=group:continue
                    attached=getattr(track.input_routing_type,'attached_object',None)
                    matching=any(identifier==marker.get('source') and same(source,attached)
                                 for identifier,source in zip(self.state_store.source_ids,self.catalog_tracks))
                    source=next((s for s in self.sources.values()
                                 if self.state_store.source_ids[s.index]==marker.get('source')),None)
                    midi_matching=(source is not None and source.kind=='midi'
                        and self._cache_midi_proof(track,source) and self._midi_compatible(track,source))
                    if (same(track.group_track,item.group) and
                            (matching and post_fx(track.input_routing_channel) or midi_matching)):
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
                if source.kind=='midi':
                    device,input_type,input_channel=midi_profile(source.track)
                    endpoint_name='1-'+str(device.name)
                    if (not same(source.instrument,device) or not same(source.input_type,input_type)
                            or not same(source.input_channel,input_channel) or source.endpoint_name!=endpoint_name):
                        source=replace(source,instrument=device,input_type=input_type,input_channel=input_channel,
                                       endpoint_name=endpoint_name)
                        self.sources[i]=source
                        for group,mask in self.desired.items():
                            if not mask&(1<<i):continue
                            self.revisions[group]=self.revisions.get(group,0)+1
                            if group in self._intents:self._intents[group]['revision']=self.revisions[group]
                            self.backend.reserves.pop(group,None);self.last_ready.pop(group,None)
                            self.jobs.pop(group,None);self.prepared_masks.pop(group,None)
                            self.busy.add(group)
                        self._midi_proofs=[p for p in self._midi_proofs if not same(p['source'],source.track)]
                    if self.midi.supported(source):
                        available |= 1 << i;self.issues.pop(i,None)
                elif (not source.track.is_frozen and source.track.current_monitoring_state == IN
                        and label(source.track.output_routing_type).casefold().replace(' ', '') == 'sendsonly'
                        and all(p.value == p.min for p in source.track.mixer_device.sends)):
                    available |= 1 << i
                name, color = str(source.track.name), int(source.track.color)
                index = int(getattr(source.track, 'color_index', 0))
                if (name, color, index) != (source.name, source.color, source.color_index):
                    self.sources[i] = replace(source,name=name,color=color,color_index=index)
            except ValueError as error:
                self.issues[i]=str(error)
            except NATIVE_ERRORS as error:
                native_error(error)
                self.issues[i]=str(error)
                self.backend.state.topology_dirty=True
        self._available = available
        self.midi.observe()
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
                   and not self.desired.get(g, 0) & ~self.available
                   and self.midi.recordable(g,self.desired.get(g,0)))

    def set_patch(self, group, mask):
        old = self.desired.get(group, 0)
        if self.root is None or group not in self.backend.state.groups or mask & ~((1 << MAX_SOURCES) - 1):
            raise ValueError('Audio group or selection unavailable')
        if (mask & ~old) & ~self.available: raise ValueError('input unavailable')
        self.midi.request(group,mask)
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
        self.midi.request(group,self.desired.get(group,0))
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
        return (self.prepared_masks.get(group, 0)
                | (int(group in self.busy) << 25) | (int(group in self.failed) << 26))

    def available_lane(self, track, parent, row, source=None):
        try:
            return (same(getattr(track, 'group_track', None), parent)
                and not track.is_foldable and not track.is_frozen
                and (getattr(track,'has_midi_input',False) and not track.has_audio_input
                     if source is not None and source.kind=='midi' else track.has_audio_input)
                and (not track.arm or self.backend.monitoring.owned(track))
                and self.backend.recording._quiet(track)
                and 0 <= row < len(track.clip_slots)
                and not track.clip_slots[row].has_clip and not track.clip_slots[row].is_triggered)

        except NATIVE_ERRORS as error:
            native_error(error);self.backend.state.topology_dirty=True;return False

    def _group_index(self,parent):
        return next((g for g,item in self.backend.state.groups.items() if same(item.group,parent)),None)

    def _midi_compatible(self,track,source):
        # The slow preparation path proves offered-route uniqueness. Admission
        # checks only those retained Native values, never enumerates ports.
        proof=next((p for p in self._midi_proofs if same(p['track'],track)),None)
        if proof is None:return False
        return (same(proof['source'],source.track) and same(proof['instrument'],source.instrument)
                and same(proof['wanted_type'],source.input_type)
                and same(proof['wanted_channel'],source.input_channel)
                and proof['endpoint']==source.endpoint_name
                and track.current_monitoring_state==OFF
                and all(same(getattr(track,prop),value) for prop,value in proof['routes'].items()))

    @staticmethod
    def _offered(track,property,wanted):
        offered=tuple(getattr(track,'available_'+property+'s'))
        exact=[r for r in offered if same(r,wanted)]
        if len(exact)==1:return exact[0]
        identifier=getattr(wanted,'identifier',None)
        if identifier is not None:
            exact=[r for r in offered if getattr(r,'identifier',None)==identifier]
            if len(exact)==1:return exact[0]
        # Cross-track external wrappers may lack equality/identifier. Only a
        # unique actual offered label+attachment can prove that fallback.
        choices=[r for r in offered if label(r)==label(wanted)
                 and same(getattr(r,'attached_object',None),getattr(wanted,'attached_object',None))]
        if len(choices)!=1:raise ValueError('configured MIDI '+property+' unavailable or ambiguous')
        return choices[0]

    def _cache_midi_proof(self,track,source):
        routes={prop:self._offered(track,prop,wanted) for prop,wanted in
                (('input_routing_type',source.input_type),('input_routing_channel',source.input_channel))}
        choices=[r for r in track.available_output_routing_types
                 if same(getattr(r,'attached_object',None),source.track)]
        if len(choices)!=1:raise ValueError('MIDI instrument destination unavailable or ambiguous')
        routes['output_routing_type']=choices[0];routes['output_routing_channel']=endpoint(track,source)
        if (label(routes['output_routing_channel'])!=source.endpoint_name
                or not all(same(getattr(track,prop),value) for prop,value in routes.items())):return False
        self._midi_proofs=[p for p in self._midi_proofs if not same(p['track'],track)]
        self._midi_proofs.append(dict(track=track,source=source.track,instrument=source.instrument,
            wanted_type=source.input_type,wanted_channel=source.input_channel,
            endpoint=source.endpoint_name,routes=routes))
        return True

    def _prune_midi_proofs(self):
        retained=[m.track for reserve in self.backend.reserves.values() for m in reserve.members]
        for job in self.jobs.values():
            retained.extend(m.track for m in job['members'])
            if job['track'] is not None:retained.append(job['track'])
        self._midi_proofs=[p for p in self._midi_proofs if any(same(p['track'],t) for t in retained)]

    def _routing_write(self,group,job,source,track,prop,wanted,budget,confirmed=None):
        check=confirmed or (lambda current:same(current,wanted))
        pending=job.get('pending_route')
        if pending is not None:
            if pending['property']!=prop:
                if check(getattr(track,prop)):return True
                raise ValueError('pending route stage changed')
            if not check(getattr(track,prop)):return False
            job.pop('pending_route',None)
            self.backend.events.append(dict(event='preparation_route_confirmed',group=group,
                source=source.index,kind=source.kind,property=prop,at_ns=time.monotonic_ns()))
            return True
        if check(getattr(track,prop)):return True
        if not budget.available():return False
        # Record before Native effects. An unconfirmed assignment is observed,
        # never blindly reissued by the following maintenance callback.
        job['pending_route']=dict(property=prop,wanted=wanted)
        budget.writes+=1;started=time.monotonic_ns()
        setattr(track,prop,wanted)
        returned=time.monotonic_ns()
        ready=check(getattr(track,prop))
        if ready:job.pop('pending_route',None)
        self.backend.events.append(dict(event='preparation_route_write',group=group,source=source.index,
            kind=source.kind,property=prop,elapsed_ns=returned-started,
            readback_ns=time.monotonic_ns()-returned,confirmed=ready,at_ns=returned))
        return ready

    def _prepare_midi(self,group,track,source,job,budget):
        if not self._routing_write(group,job,source,track,'current_monitoring_state',OFF,budget):return False
        for prop,wanted in (('input_routing_type',source.input_type),('input_routing_channel',source.input_channel)):
            pending=job.get('pending_route')
            chosen=pending['wanted'] if pending is not None and pending['property']==prop else self._offered(track,prop,wanted)
            if not self._routing_write(group,job,source,track,prop,chosen,budget):return False
        pending=job.get('pending_route')
        if pending is not None and pending['property']=='output_routing_type':chosen=pending['wanted']
        elif same(getattr(track.output_routing_type,'attached_object',None),source.track):chosen=track.output_routing_type
        else:
            choices=[r for r in track.available_output_routing_types
                     if same(getattr(r,'attached_object',None),source.track)]
            if len(choices)!=1:raise ValueError('MIDI instrument destination unavailable or ambiguous')
            chosen=choices[0]
        if not self._routing_write(group,job,source,track,'output_routing_type',chosen,budget):return False
        pending=job.get('pending_route')
        wanted=pending['wanted'] if pending is not None and pending['property']=='output_routing_channel' else endpoint(track,source)
        if label(wanted)!=source.endpoint_name:raise ValueError('instrument endpoint changed')
        if not self._routing_write(group,job,source,track,'output_routing_channel',wanted,budget):return False
        started=time.monotonic_ns()
        valid=self._cache_midi_proof(track,source)
        self.backend.events.append(dict(event='preparation_proof',group=group,source=source.index,
            kind=source.kind,elapsed_ns=time.monotonic_ns()-started,confirmed=valid,at_ns=started))
        return valid

    def _prepare_audio(self,group,track,source,job,budget,parent):
        # New Native tracks must finish their implicit parent setup. Existing
        # Audio lanes retain output routing, effects and mixer settings.
        if job.get('created') and not self.backend._parent_output(track,parent):return False
        if not self._routing_write(group,job,source,track,'current_monitoring_state',OFF,budget):return False
        pending=job.get('pending_route')
        if pending is not None and pending['property']=='input_routing_type':chosen=pending['wanted']
        elif source_route(track.input_routing_type,source):chosen=track.input_routing_type
        else:
            choices=[r for r in track.available_input_routing_types if source_route(r,source)]
            if len(choices)!=1:raise ValueError('source route unavailable or ambiguous')
            chosen=choices[0]
        if not self._routing_write(group,job,source,track,'input_routing_type',chosen,budget,
                                   lambda current:source_route(current,source)):return False
        pending=job.get('pending_route')
        if pending is not None and pending['property']=='input_routing_channel':chosen=pending['wanted']
        else:
            choices=[r for r in track.available_input_routing_channels if post_fx(r)]
            if not choices:return False
            if len(choices)!=1:raise ValueError('Post FX route ambiguous')
            chosen=choices[0]
        return self._routing_write(group,job,source,track,'input_routing_channel',chosen,budget,post_fx)

    def compatible(self, track, parent, row, source):
        try:
            if source.kind=='midi':
                return self.available_lane(track,parent,row,source) and self._midi_compatible(track,source)
            return (self.available_lane(track, parent, row,source)
                and (track.current_monitoring_state == OFF or
                     track.current_monitoring_state == AUTO and self.backend.monitoring.owned(track))
                and source_route(track.input_routing_type, source) and post_fx(track.input_routing_channel))

        except NATIVE_ERRORS as error:
            native_error(error);self.backend.state.topology_dirty=True;return False

    def validate(self,reserve,group,row,require_lease=True):
        item=self.backend.state.groups[group]
        if (reserve.row!=row or reserve.revision!=self.revisions.get(group)
            or not same(reserve.parent,item.group)):return False
        selected=self.selected(group)
        if (len(selected)!=len(reserve.members) or any(
                source.index!=member.source.index or not same(source.track,member.source.track)
                for source,member in zip(selected,reserve.members))):return False
        if require_lease and any(source.kind=='midi' and not self.midi.ready(group,source) for source in selected):
            return False
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
        self.backend.pool_unavailable.pop(group,None)
        self.midi.confirm(group)
        self._prune_midi_proofs()
        self.backend.allocations.release(group)

    def maintenance(self,group,row,active):
        b=self.backend;item=b.state.groups[group]
        attempted_revision=self.revisions.get(group,0)
        try:
            job=self.jobs.get(group)
            if (job is not None and job.get('overflow')
                    and job['revision']==attempted_revision and job['row']==row):
                # Reseeding cancels protocol permission, not already created
                # lanes. Reacquire Busy before resuming that job's routing.
                with b.allocations.operation(b._allocation_context(group)) as allowed:
                    if not allowed:return
            if not self.midi.maintenance(group):return
            if not self.desired.get(group,0):
                self.jobs.pop(group,None);b.reserves.pop(group,None)
                self.prepared_masks[group]=0
                self.confirmed[group]=0;self.last_ready.pop(group,None);self._intents.pop(group,None)
                self.busy.discard(group);self.failed.discard(group)
                b.pool_unavailable.pop(group,None)
                b.allocations.release(group)
                return
            selected=self.selected(group)
            reserve=b.reserves.get(group)
            if reserve is not None and self.validate(reserve,group,row,require_lease=False):
                # A cancelled/deleted earlier slot can become free while a later
                # reserve is ready. Reorder only in maintenance, never dispatch.
                excluded=self.excluded(active)
                first=[]
                for source in selected:
                    track=next((t for t in item.tracks if not any(same(t,e) for e in excluded+first)
                                and self.available_lane(t,item.group,row,source)),None)
                    if track is None:break
                    first.append(track)
                if len(first)==len(reserve.members) and all(
                        same(track,m.track) for track,m in zip(first,reserve.members)):
                    self._confirm_reserve(group,reserve);return
            b.reserves.pop(group,None)
            job=self.jobs.get(group)
            if job is None or job['revision']!=self.revisions.get(group) or job['row']!=row:
                job=dict(revision=self.revisions.get(group),row=row,stage='select',members=[],track=None,at=b.now())
                self.jobs[group]=job
            self.busy.add(group)
            budget=RoutingBudget();batch_started=time.monotonic_ns()
            try:
                for unused in range(MAX_SOURCES):
                    excluded=self.excluded(active)+[m.track for m in job['members']]
                    source=selected[len(job['members'])]
                    if job['track'] is None:
                        track=next((t for t in item.tracks
                                    if not any(same(t,e) for e in excluded)
                                    and self.available_lane(t,item.group,row,source)),None)
                        if track is not None and self.compatible(track,item.group,row,source):
                            job['members'].append(Destination(source,track,track.clip_slots[row]))
                        else:
                            created=track is None
                            if created:
                                if not b.allow_audio_track_creation:
                                    b.pool_unavailable[group]='prepared Audio pool unavailable'
                                    self.busy.discard(group)
                                    return
                                job['overflow']=True
                                track=b._create_audio_lane(group,row,midi=source.kind=='midi')
                                if track is None:return
                            job['track']=track;job['created']=created
                            job['stage']='routing';job['at']=b.now()
                            # Structural Native work always yields, even when
                            # the fake implementation completes immediately.
                            if created:return
                    if job['track'] is not None:
                        track=job['track']
                        if (not self.available_lane(track,item.group,row,source)
                                or not any(same(track,t) for t in item.tracks)):
                            raise ValueError('preparing recording destination changed')
                        ready=(self._prepare_midi(group,track,source,job,budget) if source.kind=='midi'
                               else self._prepare_audio(group,track,source,job,budget,item.group))
                        if not ready:
                            if b.now()-job['at']>8:raise ValueError('recording routing preparation timeout')
                            return
                        if not self.compatible(track,item.group,row,source):
                            if b.now()-job['at']>8:raise ValueError('source routing verification timeout')
                            return
                        job['members'].append(Destination(source,track,track.clip_slots[row]));job['track']=None
                    self.prepared_masks[group]=sum(1<<m.source.index for m in job['members'])
                    if len(job['members'])==len(selected):break
                    if not budget.more():return
            finally:
                b.events.append(dict(event='preparation_work_batch',group=group,writes=budget.writes,
                    members=len(job['members']),stage=job['stage'],elapsed_ns=time.monotonic_ns()-batch_started,
                    at_ns=batch_started))
            if len(job['members'])==len(selected):
                appearance_started=time.monotonic_ns()
                for m in job['members']:
                    try:
                        if str(m.track.name)!=m.source.name:m.track.name=m.source.name
                        if int(m.track.color_index)!=m.source.color_index:m.track.color_index=m.source.color_index
                    except (RuntimeError,ValueError,AttributeError) as error:
                        b.events.append(dict(event='source_appearance_unavailable',group=group,reason=str(error)))
                b.events.append(dict(event='preparation_appearance',group=group,elapsed_ns=time.monotonic_ns()-appearance_started,at_ns=appearance_started))
                if self.revisions.get(group,0)!=job['revision'] or self.jobs.get(group) is not job:return
                reserve=BundleReserve(group,item.group,row,job['revision'],tuple(job['members']))
                ownership_started=time.monotonic_ns()
                self._mark_prepared_ownership(group,reserve)
                b.events.append(dict(event='preparation_ownership_marker',group=group,elapsed_ns=time.monotonic_ns()-ownership_started,at_ns=ownership_started))
                if self.revisions.get(group,0)!=job['revision'] or self.jobs.get(group) is not job:return
                b.reserves[group]=reserve
                confirm_started=time.monotonic_ns()
                self._confirm_reserve(group,reserve)
                b.events.append(dict(event='preparation_reserve_confirm',group=group,elapsed_ns=time.monotonic_ns()-confirm_started,at_ns=confirm_started))
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
        b.allocations.release(group)
        self.midi.cancel(group)
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
                if self.validate(restored,group,prior.row,require_lease=False):
                    b.reserves[group]=restored
                    # The old ready destination remains usable immediately.
                    b.failures.pop(group,None)
                    return
            except NATIVE_ERRORS + (ValueError,) as error:native_error(error)
        # Uncertain mutations stay latched; source-readiness failures alone may
        # clear when a retained source becomes available again.
        b.failures[group]=reason
