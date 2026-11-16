"""Two retained Audio inputs and bounded preparation, outside MIDI dispatch.

The explicitly prepared [Input n] helpers inside each rightmost [Remote]
subgroup persist assignments through their mute flags. Their configured route
objects, rather than ambiguous source display names, identify input endpoints.
"""
from dataclasses import dataclass
from .model import same


OFF, IN = 2, 0


def label(value):
    return str(getattr(value, 'display_name', value))


def route_equal(left, right):
    a, b = getattr(left, 'identifier', None), getattr(right, 'identifier', None)
    return a == b if a is not None and b is not None else same(left, right)


@dataclass(frozen=True)
class Source:
    index: int
    track: object
    name: str
    color: int
    color_index: int
    route: object
    channel: object


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
        self.specs = tuple(specs)
        if len(self.specs) != 2:
            raise ValueError('Audio patching requires exactly two prepared sources')
        self.sources, self.helpers, self.issues = {}, {}, {}
        self._source_candidates={}
        self.desired, self.revisions = {}, {}
        self.confirmed,self.last_ready,self._intents={},{},{}
        self._owner_revision=0
        self.monitor_owner = None
        self.monitor_dirty = False
        self._helper_actual={}
        self._helper_plan={}
        self.jobs = {}
        self.prepared_masks = {}
        self.busy = set()
        self.failed = set()
        self._initialized = set()
        self.discover()

    def discover(self):
        tracks = self.backend.state.tracks
        roots = [t for t in tracks if t.is_foldable and str(t.name).split(' ',1)[0]=='[Inputs]']
        def within(track, root):
            parent = getattr(track,'group_track',None)
            for unused in range(32):
                if parent is None: return False
                if same(parent,root): return True
                parent=getattr(parent,'group_track',None)
            return False
        candidates, helpers, issues = {}, {}, {}
        for g,anchor in self.backend.state.anchors.items():
            children = [t for t in tracks if same(getattr(t,'group_track',None),anchor)]
            found=[]
            for spec in self.specs:
                matches=[t for t in children if not t.is_foldable and t.has_audio_input
                         and str(t.name)==spec['helper_name']]
                found.append(matches[0] if len(matches)==1 else None)
            if all(found): helpers[g]=tuple(found)
            else: issues[g]='prepared input helpers unavailable'
        for i,spec in enumerate(self.specs):
            matches=[t for t in tracks if len(roots)==1 and not t.is_foldable
                     and t.has_audio_input and str(t.name)==spec['name'] and within(t,roots[0])]
            helper=next((items[i] for items in helpers.values()),None)
            if len(matches)!=1 or helper is None:continue
            candidates[i]=(matches[0],helper)
        self.sources={}
        self._source_candidates=candidates
        self.helpers,self.issues=helpers,issues
        for g,items in helpers.items():
            for i,t in enumerate(items):
                self._helper_actual[g,i,'mute']=bool(t.mute)
                self._helper_actual[g,i,'current_monitoring_state']=t.current_monitoring_state
        self._available=getattr(self,'_available',0)
        for g,items in helpers.items():
            if g not in self._initialized:
                self.desired[g]=sum((not bool(t.mute))<<i for i,t in enumerate(items))
                self.confirmed[g]=self.desired[g]
                self.revisions[g]=0;self._initialized.add(g)
            # A unique actual monitor group can be recovered from Live.
        owners={g for g,items in helpers.items() if any(t.current_monitoring_state==IN and not t.mute for t in items)}
        if self.monitor_owner is None and len(owners)==1:self.monitor_owner=next(iter(owners))
        if self._helper_plan:self._plan_helpers()
        self.observe_sources()

    def _complete_source_profiles(self):
        """Complete only missing profiles from two retained startup candidates.

        Live may expose the saved tracks before their routing properties have
        settled. Readiness is not discovery: retry those retained objects, not
        the whole Set, and never replace a ready canonical route here.
        """
        for i,(source,helper) in self._source_candidates.items():
            if i in self.sources:continue
            try:
                if str(source.name)!=self.specs[i]['name']:continue
                route,channel=helper.input_routing_type,helper.input_routing_channel
                if label(channel).casefold().replace(' ','')!='postfx':continue
                expected=self.specs[i].get('route_identifier')
                if expected is not None and str(getattr(route,'identifier',''))!=str(expected):continue
                self.sources[i]=Source(i,source,str(source.name),int(source.color),
                                       int(getattr(source,'color_index',0)),route,channel)
            except (RuntimeError,AttributeError,TypeError,ValueError):continue

    @property
    def available(self):return self._available

    def observe_sources(self):
        self._complete_source_profiles()
        before=self._available
        available=0
        for i,source in self.sources.items():
            try:
                if (not source.track.is_frozen and str(source.track.name)==source.name
                    and source.track.current_monitoring_state==IN
                    and label(source.track.output_routing_type).casefold().replace(' ','')=='sendsonly'
                    and all(p.value==p.min for p in source.track.mixer_device.sends)):
                    available|=1<<i
                    color=int(source.track.color);index=int(getattr(source.track,'color_index',0))
                    if (color,index)!=(source.color,source.color_index):
                        self.sources[i]=Source(i,source.track,source.name,color,index,source.route,source.channel)
            except (RuntimeError,AttributeError):pass
        self._available=available
        if available&~before:
            for group,reason in tuple(self.backend.failures.items()):
                wanted=self.desired.get(group,0)
                if reason=='no available recording source' and wanted and not wanted&~available:
                    self.backend.failures.pop(group,None);self.failed.discard(group)
                    self.busy.add(group)

    @property
    def group_mask(self):
        return sum(bool(self.desired.get(g,0))<<g for g in self.backend.state.groups
                   if g in self.helpers and not self.desired.get(g,0)&~self.available)

    def set_patch(self,group,mask):
        old=self.desired.get(group,0)
        if group not in self.helpers or (mask&~old)&~self.available:raise ValueError('input unavailable')
        if self.desired.get(group,0)!=mask:
            self.desired[group]=mask;self.revisions[group]=self.revisions.get(group,0)+1
            self.backend.reserves.pop(group,None)
            self.jobs.pop(group,None)
            self.prepared_masks.pop(group,None)
        self.failed.discard(group);self.busy.add(group)
        self._owner_revision+=1
        self._intents[group]=dict(revision=self.revisions.get(group,0),
                                  previous_owner=self.monitor_owner,owner_revision=self._owner_revision)
        self.monitor_owner=group;self._plan_helpers()
        return mask

    def set_monitor(self,group):
        if group not in self.helpers:raise ValueError('monitor group unavailable')
        self._owner_revision+=1;self.monitor_owner=group;self._plan_helpers()
        return group

    def _plan_helpers(self):
        """Pure cached plan; rapid absolute intents replace, never queue."""
        plan={}
        for g,items in self.helpers.items():
            for i,helper in enumerate(items):
                enabled=bool(self.desired.get(g,0)&(1<<i))
                for attr,wanted in (('mute',not enabled),
                                    ('current_monitoring_state',IN if enabled and g==self.monitor_owner else OFF)):
                    key=g,i,attr
                    if self._helper_actual.get(key)!=wanted:plan[key]=wanted
        self._helper_plan=plan;self.monitor_dirty=bool(plan)

    def reconcile_helpers(self):
        """At most six retained setters for a two-input owner/patch change.

        Disable old paths before enabling new paths. A Native exception cancels
        the remaining plan instead of replaying an uncertain write or enabling
        a second path after an uncertain disable.
        """
        if not self.monitor_dirty:return
        def order(pair):
            (g,i,attr),wanted=pair
            return (0 if attr=='current_monitoring_state' and wanted==OFF else
                    1 if attr=='mute' and wanted else 2 if attr=='mute' else 3,g,i)
        for key,wanted in sorted(self._helper_plan.items(),key=order)[:6]:
            if self._helper_plan.get(key)!=wanted:continue
            g,i,attr=key
            self._helper_plan.pop(key,None)
            try:setattr(self.helpers[g][i],attr,wanted)
            except (RuntimeError,ValueError,AttributeError) as error:
                self._helper_plan.clear();self.monitor_dirty=False
                self.failed.add(g);self.issues[g]=str(error);self.monitor_owner=None
                raise
            self._helper_actual[key]=wanted
        self._plan_helpers()

    def observe_helpers(self,group):
        """Slow Live-side reconciliation never overwrites our pending intent."""
        if group not in self.helpers or any(key[0]==group for key in self._helper_plan):return
        items=self.helpers[group]
        actual=0
        for i,helper in enumerate(items):
            mute=bool(helper.mute);mode=helper.current_monitoring_state
            self._helper_actual[group,i,'mute']=mute
            self._helper_actual[group,i,'current_monitoring_state']=mode
            if not mute:actual|=1<<i
        if actual!=self.desired.get(group,0):
            self.desired[group]=actual;self.revisions[group]=self.revisions.get(group,0)+1
            self.confirmed[group]=actual
            self._intents.pop(group,None)
            self.jobs.pop(group,None);self.backend.reserves.pop(group,None)
            self.prepared_masks.pop(group,None);self.failed.discard(group)
            self.backend.failures.pop(group,None)
        if not self._helper_plan:
            owners={g for g in self.helpers if any(
                not self._helper_actual.get((g,i,'mute'),True)
                and self._helper_actual.get((g,i,'current_monitoring_state'))==IN for i in range(2))}
            self.monitor_owner=next(iter(owners)) if len(owners)==1 else None

    def source_ready(self,helper,source):
        return (route_equal(helper.input_routing_type,source.route)
                and route_equal(helper.input_routing_channel,source.channel))

    def selected(self,group):
        mask=self.desired.get(group,0)
        if not mask or mask&~self.available:raise ValueError('no available recording source')
        if group not in self.helpers:raise ValueError('prepared input helpers unavailable')
        for i in range(2):
            if mask&(1<<i):
                helper=self.helpers[group][i]
                if (str(helper.name)!=self.specs[i]['helper_name']
                    or not self.source_ready(helper,self.sources[i])
                    or not self.backend._parent_output(helper,self.backend.state.anchors[group])):
                    raise ValueError('prepared input helper routing changed')
        return tuple(self.sources[i] for i in range(2) if mask&(1<<i))

    def routing_fact(self,group):
        return (self.desired.get(group,0)|(self.prepared_masks.get(group,0)<<2)
                |(int(group in self.busy)<<4)|(int(group in self.failed)<<5))

    def compatible(self,track,parent,row,source):
        b=self.backend
        if (track.is_foldable or not track.has_audio_input or track.is_frozen or track.arm
            or str(track.name).startswith(('[Patch]','[Return]','[Input '))
            or not b.recording._quiet(track) or row>=len(track.clip_slots)
            or track.clip_slots[row].has_clip or track.clip_slots[row].is_triggered):return False
        return (b._parent_output(track,parent) and track.current_monitoring_state==OFF
                and route_equal(track.input_routing_type,source.route)
                and route_equal(track.input_routing_channel,source.channel))

    def validate(self,reserve,group,row):
        item=self.backend.state.groups[group]
        if (reserve.row!=row or reserve.revision!=self.revisions.get(group)
            or not same(reserve.parent,item.group)):return False
        selected=self.selected(group)
        if tuple(s.index for s in selected)!=tuple(m.source.index for m in reserve.members):return False
        return all(any(same(m.track,t) for t in item.tracks)
                   and same(m.slot,m.track.clip_slots[row])
                   and self.compatible(m.track,item.group,row,m.source) for m in reserve.members)

    def excluded(self,active):
        result=[]
        if active is not None and active.state!='completed':
            for take in (active,active.following):
                if take is not None:result.extend(t.track for t in (take,)+tuple(getattr(take,'members',())))
        return result

    def maintenance(self,group,row,active):
        b=self.backend;item=b.state.groups[group]
        attempted_revision=self.revisions.get(group,0)
        try:
            self.reconcile_helpers()
            self.observe_helpers(group)
            if not self.desired.get(group,0):
                self.jobs.pop(group,None);b.reserves.pop(group,None)
                self.prepared_masks[group]=0
                self.confirmed[group]=0;self.last_ready.pop(group,None);self._intents.pop(group,None)
                self.busy.discard(group);self.failed.discard(group)
                return
            selected=self.selected(group)
            reserve=b.reserves.get(group)
            if reserve is not None and self.validate(reserve,group,row):
                self.busy.discard(group);self.failed.discard(group);return
            b.reserves.pop(group,None)
            job=self.jobs.get(group)
            if job is None or job['revision']!=self.revisions.get(group) or job['row']!=row:
                job=dict(revision=self.revisions.get(group),row=row,stage='select',members=[],track=None,at=b.now())
                self.jobs[group]=job
            self.busy.add(group)
            excluded=self.excluded(active)+[m.track for m in job['members']]
            source=selected[len(job['members'])]
            if job['track'] is None:
                candidates=[t for t in item.tracks if not any(same(t,e) for e in excluded)]
                track=next((t for t in candidates if self.compatible(t,item.group,row,source)),None)
                if track is not None:
                    job['members'].append(Destination(source,track,track.clip_slots[row]))
                else:
                    track=next((t for t in candidates if not t.is_foldable and t.has_audio_input
                                and not str(t.name).startswith(('[Patch]','[Return]','[Input '))
                                and not t.is_frozen and not t.arm and b.recording._quiet(t)
                                and row<len(t.clip_slots) and not t.clip_slots[row].has_clip
                                and not t.clip_slots[row].is_triggered),None)
                    if not b.allow_audio_track_creation:raise ValueError('prepared Audio pool unavailable')
                    if track is None:
                        track=b._create_audio_lane(group,row)
                        if track is None:return
                    job['track']=track;job['stage']='monitor';job['at']=b.now();return
            else:
                track=job['track']
                if (track.arm or not b.recording._quiet(track) or track.clip_slots[row].has_clip
                    or not any(same(track,t) for t in item.tracks)):
                    raise ValueError('preparing Audio destination changed')
                stage=job['stage']
                if stage=='monitor':
                    if not b._parent_output(track,item.group):
                        if b.now()-job['at']>8:raise ValueError('parent routing preparation timeout')
                        return
                    job['stage']=stage='type'
                    if track.current_monitoring_state!=OFF:
                        track.current_monitoring_state=OFF;return
                if stage=='type':
                    job['stage']=stage='channel'
                    if not route_equal(track.input_routing_type,source.route):
                        choices=[r for r in track.available_input_routing_types if route_equal(r,source.route)]
                        if len(choices)!=1:raise ValueError('source route unavailable or ambiguous')
                        track.input_routing_type=choices[0];return
                if stage=='channel':
                    if not route_equal(track.input_routing_type,source.route):
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
                    try:m.track.name=m.source.name;m.track.color_index=m.source.color_index
                    except (RuntimeError,ValueError,AttributeError) as error:
                        b.events.append(dict(event='source_appearance_unavailable',group=group,reason=str(error)))
                if self.revisions.get(group,0)!=job['revision'] or self.jobs.get(group) is not job:return
                b.reserves[group]=BundleReserve(group,item.group,row,job['revision'],tuple(job['members']))
                self.last_ready[group]=b.reserves[group]
                self.confirmed[group]=self.desired[group]
                self._intents.pop(group,None)
                self.jobs.pop(group,None);self.busy.discard(group);self.failed.discard(group)
        except (RuntimeError,ValueError,AttributeError,IndexError,TypeError) as exc:
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
            self._plan_helpers()
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
            except (RuntimeError,ValueError,AttributeError):pass
        # Uncertain mutations stay latched; source-readiness failures alone may
        # clear when a retained source becomes available again.
        b.failures[group]=reason
