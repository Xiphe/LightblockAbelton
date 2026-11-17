"""Retained Native targets, prepared Audio reserves and recording transactions.

Only maintenance rebuilds Session topology or creates/routes tracks. The command
path uses retained targets; recording allocation can only consume a ready slot.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass
import math
import re
import time
from types import SimpleNamespace

from . import configuration as config, protocol as p
from .model import ClipState, GroupState, LoopReference, read_grid, same, live_valid, NATIVE_ERRORS, native_error
from .recording_core import LiveRecording
from .selection_restore import SelectionRestoration
from .audio_inputs import AudioInputs, MAX_SOURCES
from .direct_monitoring import DirectMonitoring, DirectMonitoringError
from .allocation_gate import AllocationGate, AllocationContext
from .bundle_recording import BundleRecording

MARKER = re.compile(r'^\[R([1-8])\]')
DENOMINATORS = (1, 2, 4, 8, 16)
REJECT = 16383


def label(value):
    return str(getattr(value, 'display_name', value))


def normalized(parameter, preserve_positive=True):
    width = parameter.max - parameter.min
    if width <= 0 or not parameter.is_enabled:
        raise ValueError('parameter unavailable')
    offset = parameter.value - parameter.min
    return max(1 if preserve_positive and offset > 0 else 0,
               min(127, int(round(offset * 127. / width))))


class RetainedState:
    def __init__(self, song, assignments):
        self.song, self.assignments = song, assignments
        self.groups, self.parameters, self.anchors = {}, {}, {}
        self.tracks, self.issues = (), {}
        self.topology_dirty = True
        self.bindings = {}
        self.retained = {}
        self.occupancy = {}
        self._names = []
        self.occupancy_known = set()
        self._scans = {}

    def refresh(self):
        # LiveRecording's critical path must not rediscover the graph.
        return self

    def discover(self):
        self.topology_dirty = True
        current = tuple(self.song.tracks)
        tracks = tuple(track for track in current if live_valid(track))
        candidates = {}
        for track in tracks:
            match = MARKER.match(str(track.name))
            if match:
                candidates.setdefault(int(match.group(1)) - 1, []).append(track)
        groups, anchors, parameters, bindings, issues = {}, {}, {}, {}, {}
        for index, found in candidates.items():
            if len(found) != 1 or not found[0].is_foldable:
                issues[index] = 'ambiguous remote group'
                continue
            group = found[0]
            direct = [t for t in tracks if same(getattr(t, 'group_track', None), group)]
            musical = tuple(t for t in direct if not t.is_foldable)
            anchor = [t for t in direct if t.is_foldable and str(t.name) == '[Remote]']
            if len(anchor) == 1 and direct and same(direct[-1], anchor[0]):
                anchors[index] = anchor[0]
            else:
                issues[index] = 'rightmost Remote subgroup unavailable'
            groups[index] = GroupState(index, group, musical, (), ())
            mix = group.mixer_device
            bound = [mix.volume, mix.panning]
            targets = self.assignments.get(index, self.assignments.get(str(index), None))
            targets = [{'kind':'send', 'index':i} for i in range(4)] if targets is None else targets
            for slot in range(4):
                try:target = targets[slot]
                except (IndexError,TypeError):target = None
                bound.append(self._target(index,slot,group,mix,target))
            checks = ()
            parameters[index] = tuple(bound)
            bindings[index] = tuple(checks)
        old_groups=self.groups
        self.tracks, self.groups, self.anchors = tracks, groups, anchors
        for index,item in groups.items():
            old=old_groups.get(index)
            if old is None or len(old.tracks)!=len(item.tracks) or any(not same(a,b) for a,b in zip(old.tracks,item.tracks)):
                self.occupancy_known.discard(index);self._scans.pop(index,None)
        self.parameters, self.bindings, self.issues = parameters, bindings, issues
        self._listen_names(tracks)
        self.topology_dirty = len(current) != len(tracks)
        for index in tuple(groups):
            self.observe(index)
        return self

    def _listen_names(self,tracks):
        for track,callback in self._names:
            try:track.remove_name_listener(callback)
            except NATIVE_ERRORS as error:native_error(error)
        self._names=[]
        for track in tracks:
            previous=str(track.name)
            def callback(track=track,previous=previous):
                if not live_valid(track):
                    self.topology_dirty=True;return
                current=str(track.name)
                if MARKER.match(previous) or MARKER.match(current) or previous=='[Remote]' or current=='[Remote]':
                    self.topology_dirty=True
            try:
                track.add_name_listener(callback);self._names.append((track,callback))
            except NATIVE_ERRORS as error:native_error(error)

    def close(self):
        for track,callback in self._names:
            try:track.remove_name_listener(callback)
            except NATIVE_ERRORS as error:native_error(error)
        self._names=[]

    def _target(self,index,slot,group,mix,target):
        identity=(index,slot);spec=repr(target)
        old=self.retained.get(identity)
        if old and same(old['group'],group) and old['spec']==spec:
            try:
                if old['invalid'] or not all(same(getter(),obj) for getter,obj in old['checks']):
                    old['invalid']=True;return None
                return old['parameter']
            except NATIVE_ERRORS + (IndexError,) as error:
                native_error(error)
                old['invalid']=True;return None
        checks=[];parameter=None
        try:
            if target['kind']=='send':
                i=target['index'];parameter=mix.sends[i]
                checks.append((lambda mix=mix,i=i:mix.sends[i],parameter))
            elif target['kind']=='device':
                path=target['device_path'];owner=group
                if len(path)%2!=1:raise ValueError('invalid device path')
                for offset in range(0,len(path),2):
                    i=path[offset];device=owner.devices[i]
                    checks.append((lambda owner=owner,i=i:owner.devices[i],device))
                    if offset+1<len(path):
                        c=path[offset+1];chain=device.chains[c]
                        checks.append((lambda device=device,c=c:device.chains[c],chain));owner=chain
                if 'expected_device_name' in target:
                    name=target['expected_device_name']
                    if device.name!=name:raise ValueError('configured device name does not match')
                    checks.append((lambda device=device:device.name,name))
                if 'parameter_name' in target:
                    name=target['parameter_name']
                    if not isinstance(name,str) or not name:
                        raise ValueError('configured parameter name must be nonempty')
                    matches=[(i,p) for i,p in enumerate(device.parameters) if p.name==name]
                    if len(matches)!=1:raise ValueError('configured parameter name is missing or ambiguous')
                    i,parameter=matches[0]
                    # Uniqueness and identity checks run in discovery/observation,
                    # never in the retained parameter-write command path.
                    checks.append((lambda device=device,name=name:
                                   tuple(p for p in device.parameters if p.name==name),
                                   (parameter,)))
                else:
                    i=target['parameter_index'];parameter=device.parameters[i]
                if 'expected_parameter_name' in target:
                    name=target['expected_parameter_name']
                    if parameter.name!=name:raise ValueError('configured parameter name does not match')
                    checks.append((lambda parameter=parameter:parameter.name,name))
                checks.append((lambda device=device,i=i:device.parameters[i],parameter))
            else:raise ValueError('unknown target')
        except (KeyError,IndexError,ValueError,AttributeError,TypeError,RuntimeError):parameter=None
        self.retained[identity]=dict(group=group,spec=spec,parameter=parameter,checks=tuple(checks),invalid=parameter is None)
        return parameter

    def observe(self, index):
        group = self.groups.get(index)
        if group is None:
            return
        playing, recording = [], []
        for track in group.tracks:
            try:
                row = track.playing_slot_index
                slots = track.clip_slots
                if type(row) is not int or not 0 <= row < len(slots):
                    continue
                slot = slots[row]
                if not slot.has_clip:
                    continue
                clip = slot.clip; item = ClipState(track, slot, row, clip)
                if clip.is_recording:
                    recording.append(item)
                if clip.is_playing:
                    playing.append(item)
            except NATIVE_ERRORS + (IndexError,) as error:
                native_error(error)
                self.topology_dirty = True
        updated = GroupState(index, group.group, group.tracks, tuple(playing), tuple(recording))
        # Active observation only reads each track's current slot. Reuse the
        # background inventory instead of scanning track × scene on every tick.
        updated.__dict__['occupied_rows']=self.occupancy.get(index,frozenset())
        self.groups[index] = updated
        # Binding verification belongs to maintenance, never a parameter write.
        bound=list(self.parameters.get(index,()))
        if len(bound)==6:
            for control in range(4):
                retained=self.retained.get((index,control))
                if retained and not retained['invalid']:
                    try:
                        if not all(same(getter(),obj) for getter,obj in retained['checks']):
                            retained['invalid']=True;bound[control+2]=None
                    except NATIVE_ERRORS + (IndexError,) as error:
                        native_error(error)
                        retained['invalid']=True;bound[control+2]=None
            self.parameters[index]=tuple(bound)

    def scan_occupancy(self,index,budget=64):
        """Incremental background inventory with a hard clip-slot read budget."""
        item=self.groups.get(index)
        if item is None:return
        scan=self._scans.get(index)
        if scan is None:
            scan=dict(track=0,row=0,slots=(),occupied=set())
            self._scans[index]=scan
        while budget>0 and scan['track']<len(item.tracks):
            if not scan['slots']:
                try:scan['slots']=tuple(item.tracks[scan['track']].clip_slots)
                except NATIVE_ERRORS as error:
                    native_error(error);self.topology_dirty=True
                    self._scans.pop(index,None);return
            slots=scan['slots']
            while budget>0 and scan['row']<len(slots):
                row=scan['row']
                try:
                    if slots[row].has_clip:scan['occupied'].add(row)
                except NATIVE_ERRORS as error:
                    native_error(error);self.topology_dirty=True
                    self._scans.pop(index,None);return
                scan['row']+=1;budget-=1
            if scan['row']>=len(slots):
                scan['track']+=1;scan['row']=0;scan['slots']=()
        if scan['track']>=len(item.tracks):
            self.occupancy[index]=frozenset(scan['occupied'])
            item.__dict__['occupied_rows']=self.occupancy[index]
            self.occupancy_known.add(index)
            self._scans.pop(index,None)

    def changed_row(self,index,row):
        item=self.groups.get(index)
        if item is None:return
        occupied=set(self.occupancy.get(index,()))
        try:present=any(row<len(t.clip_slots) and t.clip_slots[row].has_clip for t in item.tracks)
        except NATIVE_ERRORS as error:
            native_error(error);self.topology_dirty=True
            self._scans.pop(index,None);return
        if present:occupied.add(row)
        else:occupied.discard(row)
        self.occupancy[index]=frozenset(occupied)
        item.__dict__['occupied_rows']=self.occupancy[index]
        self._scans.pop(index,None)

    def reference(self, index, exclude=None):
        group = self.groups.get(index)
        if group:
            for item in group.playing:
                clip = item.clip
                try:
                    if same(clip, exclude) or clip.is_recording or not clip.is_playing or not clip.looping:
                        continue
                    if clip.is_audio_clip and not clip.warping:
                        continue
                    length = float(clip.loop_end - clip.loop_start)
                    if length <= 0:
                        continue
                    phase = (clip.playing_position - clip.loop_start) % length
                    # Native launch origin avoids song_time-position float drift.
                    anchor = float(clip.start_time + clip.loop_start)
                    return LoopReference(clip, length, phase, anchor)
                except NATIVE_ERRORS + (ValueError,) as error:
                    native_error(error)
                    continue
        return None

    def recording(self):
        actual = [(g, item) for g, group in self.groups.items() for item in group.recording
                  if item.clip.is_recording]
        if not actual:
            return None
        selected = getattr(self.song.view, 'selected_track', None)
        for index, item in actual:
            group = self.groups[index]
            if same(selected, group.group) or same(selected, item.track) or any(same(selected,t) for t in group.tracks):
                return index, item
        return actual[0]


@dataclass
class Reserve:
    group: int
    parent: object
    track: object
    slot: object
    row: int


class PreparedRecording(LiveRecording):
    def __init__(self, song, state, quantizations, reserves, **kwargs):
        super().__init__(song, state, quantizations, **kwargs)
        self.reserves = reserves
        self.request_token = None
        self.validate = None

    def _new_token(self):
        if self.request_token is not None:
            token,self.request_token=self.request_token,None
            self._token=token
            return token
        self._token=(self._token+1)&127
        return self._token

    @staticmethod
    def _quiet(track):
        return track.playing_slot_index in (-1,-2) and track.fired_slot_index == -1

    def _allocate(self, group, row, exclude=(), reuse=None):
        item = self._group(group)
        candidate = self.reserves.get(group)
        if candidate is None or candidate.row != row or not same(candidate.parent, item.group):
            raise ValueError('prepared destination unavailable')
        track, slot = candidate.track, candidate.slot
        if (any(same(track,t) for t in exclude) or not any(same(track,t) for t in item.tracks)
                or not self._quiet(track) or track.arm or track.is_frozen
                or slot.has_clip or slot.is_triggered or not same(track.clip_slots[row],slot)
                or self.validate is None or not self.validate(track,item.group,row)):
            self.reserves.pop(group, None)
            raise ValueError('prepared destination changed')
        self.reserves.pop(group, None)
        return item.group, track, slot

    def _ensure_row(self, row):
        if row >= len(self.song.scenes):
            raise ValueError('prepared scene unavailable')

    def _make(self, group, reference, row, exclude=(), reuse=None):
        # No creation, routing, scene work or full graph discovery occurs here.
        return super()._make(group, reference, row, exclude, reuse)

    def _identity(self, take, empty=False):
        # An unrelated or own background insertion must not reject Accept.
        # Check the retained Native target itself rather than refreshing graph.
        if not same(take.track.group_track,take.parent):
            raise ValueError('recording track changed parent')
        return super()._identity(take, empty)

    def advance(self, command):
        # Allocation/scene creation is maintenance work. Clear stops immediately.
        if self.take is not None and self.take.state != 'completed':
            return p.BUSY
        item = self._group(command.group)
        row = item.fresh_row
        for track in item.tracks:
            track.stop_all_clips(False)
        self._preferred_rows[command.group] = row
        self.reserves.pop(command.group, None)
        return p.APPLIED


class AudioPreparedRecording(BundleRecording, PreparedRecording):
    def _make(self, *args, **kwargs):
        take = super()._make(*args, **kwargs)
        self.monitoring.claim_take(take)
        return take

    def accept(self, group, next_take=False):
        result = super().accept(group, next_take)
        take = self.take
        if take is not None and take.group == group and take.state == 'finish_queued':
            if take.following is not None:
                self.monitoring.claim_take(take.following)
            else:
                spare = self.reserves.get(group)
                if spare is not None:self.monitoring.claim_reserve(spare)
        return result


class Backend:
    def __init__(self, song, quantizations, assignments=None, now=None):
        self.song, self.now = song, now or time.monotonic
        self.state = RetainedState(song, assignments or {})
        self.state.discover()
        self.reserves, self.tasks, self.failures = {}, {}, {}
        self.allow_audio_track_creation = getattr(config,'ALLOW_AUDIO_TRACK_CREATION',True) is True
        # A known lack of a lane/row is readiness, not a latched Native failure.
        self.pool_unavailable = {}
        self.events = deque(maxlen=256)
        self.allocations = AllocationGate(self.now, self._fact)
        specs=getattr(config,'AUDIO_SOURCES',())
        recording_class=AudioPreparedRecording if specs else PreparedRecording
        self.recording = recording_class(song, self.state, quantizations, self.reserves, monotonic=self.now,emit=self.events.append)
        self.recording.validate = self._compatible
        self.monitoring=DirectMonitoring(self) if specs else None
        if self.monitoring:self.recording.monitoring=self.monitoring
        self.audio_inputs=AudioInputs(self,specs) if specs else None
        if self.audio_inputs:self.recording.inputs=self.audio_inputs
        self.pending_begin = None
        self.after = None
        self.last_take = None
        self.failed_take = None
        self.closed_following = None
        self.handoff_alias = None
        self.restoration = None
        self._cursor = 0
        self._slow_at = -10.
        self._clock_at = -10.
        self._refclock_at = -10.
        self._shape_at = -10.
        self._take_at = -10.
        self._last_command = -10.
        self._known_created_topology = None
        self._facts = OrderedDict()
        self._published = {}
        self._tap_at = None
        self._tap_intervals = deque(maxlen=4)
        self._closed = False
        self._track_listener = False
        try:
            song.add_tracks_listener(self._tracks_changed)
            self._track_listener = True
        except AttributeError:
            pass

    @property
    def busy(self):
        take = self.recording.take
        return bool(self.pending_begin or self.after or take and take.state != 'completed')

    def _tracks_changed(self):
        self.state.topology_dirty = True

    def close(self):
        self.allocations.clear()
        self._closed = True
        if self.monitoring:self.monitoring.close()
        if self.restoration:
            self.restoration.close()
        self.state.close()
        if self._track_listener:
            self.song.remove_tracks_listener(self._tracks_changed)
            self._track_listener = False

    def _fact(self, kind, a, b, force=False):
        key = (kind, a if kind in (1,3,6) else (a & 15 if kind == 4 else 0))
        value = (kind, int(a), int(b))
        if force or self._published.get(key) != value:
            self._facts[key] = value
            if kind==5 or (kind==4 and (a&15)==8) or (kind==6 and a==3):
                self._facts.move_to_end(key,last=False)
            self._published[key] = value

    def drain_facts(self, budget=6):
        result = []
        while self._facts and len(result) < budget:
            unused, fact = self._facts.popitem(last=False)
            result.append(fact)
        return result

    def reseed(self):
        # A fresh transport session republishes current facts without changing
        # active recording or prepared Native destinations.
        self._facts.clear()
        self._published.clear()
        self.allocations.clear(publish=True)
        self._slow_at = self._clock_at = self._take_at = -10.
        for group in range(8):
            self._group_facts(group)
        self._global_facts()

    def _group_facts(self, index, parameters=True):
        item = self.state.groups.get(index)
        if item is None:
            self._fact(3,index,0)
            return
        group = item.group; color = int(group.color)
        rgb = ((color >> 17) & 127) | (((color >> 9) & 127) << 7) | (((color >> 1) & 127) << 14)
        populated=bool(item.playing or item.recording)
        flags = (1 << 21) | (populated << 22) | (bool(group.mute) << 23)
        if parameters:
            for control, parameter in enumerate(self.state.parameters.get(index, ())):
                actual=128
                if parameter is not None:
                    try:actual=normalized(parameter,control!=1)
                    except (RuntimeError,ValueError,AttributeError,TypeError):pass
                self._fact(1,index*6+control,actual)
        # The bridge gates availability until all six explicit parameter facts,
        # including unavailable128 sentinels, have actually reached the pad.
        self._fact(3,index,rgb | flags)

    def _global_facts(self):
        self._fact(6,0,int(round(float(self.song.tempo)*128)))
        available=self.audio_inputs.group_mask if self.audio_inputs else 255
        owner=self.audio_inputs.monitor_owner if self.audio_inputs else None
        self._fact(6,1,int(self.song.signature_numerator) | (int(self.song.signature_denominator)<<7)
                   | (bool(self.song.is_playing)<<14) | (bool(self.song.metronome)<<15)
                   | (available<<16) | ((owner+1 if owner is not None else 0)<<24))
        if self.audio_inputs:
            for group in range(8):
                self._fact(1,48+group,self.audio_inputs.desired.get(group,0))
                self._fact(6,8+group,self.audio_inputs.routing_fact(group))
            for index in range(MAX_SOURCES):
                source=self.audio_inputs.sources.get(index)
                color=source.color if source else 0
                rgb=((color>>17)&127)|(((color>>9)&127)<<7)|(((color>>1)&127)<<14)
                self._fact(6,24+index,rgb | (bool(self.audio_inputs.available&(1<<index))<<21))
            self._fact(1,57,owner if owner is not None else 8)
            # Catalog labels are lower-priority than control readiness.
            for index in range(MAX_SOURCES):
                source=self.audio_inputs.sources.get(index)
                name=source.name if source else ''
                encoded=name.encode('ascii','replace')[:32].ljust(32,b'\0')
                for chunk in range(8):
                    word=sum(encoded[chunk*4+offset]<<(offset*7) for offset in range(4))
                    self._fact(6,40+index*8+chunk,word)

    def _clock(self, group, grid):
        reference = self.state.reference(group) if group < 8 else None
        span = reference.length if reference else grid.length
        phase = reference.phase if reference else grid.phase
        self._fact(4,group | (max(1,min(0x1fffff,int(round(span*256))))<<4),
                   max(0,min(0x1fffff,int(round(phase*256)))), True)

    def _take_fact(self, force=False):
        take = self.recording.take
        pending = self.pending_begin
        if self.closed_following is not None and take is self.closed_following[0]:
            old,token=self.closed_following
            bar=old.bar or self.song.signature_numerator*4./self.song.signature_denominator
            a=old.group | (token<<3) | (0<<10) | (min(8191,max(1,int(round(bar*16))))<<13)
            self._fact(5,a,0,force)
            return
        if self.failed_take is not None:
            g,token=self.failed_take
            bar=self.song.signature_numerator*4./self.song.signature_denominator
            a=g | (token<<3) | (5<<10) | (min(8191,max(1,int(round(bar*16))))<<13)
            self._fact(5,a,0,False)
            return
        if pending:
            g,token,unused = pending
            phase,bar,measure = 1,self.song.signature_numerator*4./self.song.signature_denominator,0
        elif take is not None:
            g,token = take.group,take.take_id
            phase = {'queued':2,'recording':3,'finish_queued':4,'canceling':1,'completed':0}.get(take.state,5)
            bar = take.bar or self.song.signature_numerator*4./self.song.signature_denominator
            measure = max(0.,take.target-self.song.current_song_time) if phase == 2 else max(0.,self.song.current_song_time-take.anchor) if phase in (3,4) and take.anchor is not None else 0
            self.last_take = (g,token)
        elif self.last_take:
            g,token = self.last_take
            phase,bar,measure = 0,self.song.signature_numerator*4./self.song.signature_denominator,0
        else:
            return
        a = g | (token<<3) | (phase<<10) | (min(8191,max(1,int(round(bar*16))))<<13)
        self._fact(5,a,min(0xfffffff,int(round(measure*256))), force)

    def _failed_take(self, group, token, reason):
        bar = max(1,min(8191,int(round(self.song.signature_numerator*64./self.song.signature_denominator))))
        self._fact(5,group | (token<<3) | (5<<10) | (bar<<13),0,True)
        self.last_take = group,token
        self.failed_take = group,token
        self.events.append(dict(event='preparation_failed',group=group,reason=str(reason)))

    def _parameter(self, key, value):
        if not 0 <= value <= 127:
            raise ValueError('parameter value out of range')
        group,control = divmod(key,6)
        parameter = self.state.parameters[group][control]
        if parameter is None or not parameter.is_enabled:
            raise ValueError('unbound parameter')
        fraction = .5 if control == 1 and value == 64 else value/127.
        result = parameter.min + (parameter.max-parameter.min)*fraction
        if getattr(parameter,'is_quantized',False):
            result = round(result)
        started = time.monotonic_ns()
        parameter.value = result
        finished = time.monotonic_ns()
        self.events.append(dict(event='parameter_native',key=key,elapsed_ns=finished-started))
        return normalized(parameter,control!=1)

    def dispatch(self, key, value):
        """A short semantic command. Never creates tracks, scenes or routes."""
        self._last_command = self.now()
        try:
            if type(key) is not int or type(value) is not int or not 0 <= value <= 16383:
                return REJECT
            if key < 48:
                return self._parameter(key,value)
            if 48<=key<=55 and self.audio_inputs:
                if value&~127:return REJECT
                group=key-48
                result=self.audio_inputs.set_patch(group,value)
                if self.allocations.group==group:self.allocations.clear()
                self.failures.pop(group,None)
                self._global_facts()
                return result
            if key==58:
                return value if self.allocations.grant(value) else REJECT
            if key==57 and self.audio_inputs and 0<=value<8:
                result=self.audio_inputs.set_monitor(value);self._global_facts();return result
            if key == 56:
                if value & ~255:
                    return REJECT
                for group in range(8):
                    if value & (1<<group):
                        target = self.state.groups[group].group
                        target.mute = not target.mute
                mask = sum(bool(item.group.mute)<<g for g,item in self.state.groups.items())
                for g in self.state.groups:
                    self._group_facts(g,parameters=False)
                return mask
            if 64 <= key <= 103:
                group,action = divmod(key-64,5)
                token,new_token = value & 127,value>>7
                return self._record_command(group,action,token,new_token)
            if 104 <= key <= 111:
                if value != 0 or self.busy:
                    return REJECT
                group=key-104
                status=self.recording.advance(SimpleNamespace(group=group))
                if status == p.APPLIED:
                    if self.allocations.group==group:self.allocations.clear()
                    self.pool_unavailable.pop(group,None)
                    self.state.observe(group)
                    self._group_facts(group,parameters=False)
                return 0 if status == p.APPLIED else REJECT
            if key == 112:
                return self._tap(value)
            if key == 113 and value in (0,127):
                self.song.metronome = value == 127
                return 127 if self.song.metronome else 0
            if key == 114 and 1 <= value <= 99:
                self.song.signature_numerator = value
                return int(self.song.signature_numerator)
            if key == 115 and value in DENOMINATORS:
                self.song.signature_denominator = value
                self._tap_at=None;self._tap_intervals.clear()
                return int(self.song.signature_denominator)
        except (RuntimeError,ValueError,AttributeError,KeyError,IndexError,TypeError) as exc:
            self.events.append(dict(event='command_failed',key=key,reason=str(exc)))
        return REJECT

    def _tap(self,value):
        if value != 0:
            return REJECT
        now=self.now();previous=self._tap_at;self._tap_at=now
        if previous is None or now-previous>max(2.,720./(self.song.signature_denominator*self.song.tempo)):
            self._tap_intervals.clear();return 0
        interval=now-previous
        if interval<=0 or not 20 <= 240./(self.song.signature_denominator*interval) <= 999:
            return REJECT
        self._tap_intervals.append(interval)
        self.song.tempo=240./(self.song.signature_denominator*(sum(self._tap_intervals)/len(self._tap_intervals)))
        return 0

    def _record_command(self, group, action, token, new_token):
        if group not in self.state.groups:
            return REJECT
        take=self.recording.take
        if action == 0:
            if not self.song.is_playing or self.busy:
                return REJECT
            if not self.allow_audio_track_creation and group in self.pool_unavailable:
                return REJECT
            if self.audio_inputs:
                mask=self.audio_inputs.desired.get(group,0)
                if not (self.audio_inputs.group_mask & (1 << group)):return REJECT
            self.failed_take=None
            self.closed_following=None
            self.pending_begin=(group,token,self.now())
            self.last_take=(group,token)
            self.failures.pop(group,None)
            self._admit_begin()
            self._take_fact(True)
            return 0
        # Retry has a promised replacement before that replacement owns a
        # Native Take object. Cancel may still carry the old device context.
        if self.after is not None and self.after[0]=='retry' and self.after[1]==group:
            unused,unused_group,replacement_token,old_token=self.after
            if action==4 and token in (old_token,replacement_token):
                self.after=None;self.handoff_alias=None
                self.last_take=(group,replacement_token)
                if take is not None:self.closed_following=(take,replacement_token)
                self._take_fact(True)
                return 0
        alias=self.handoff_alias
        if alias is not None and group==alias[0]:
            context=(self.pending_begin is not None and self.pending_begin[:2]==(group,alias[2])) if alias[3] is None else take is alias[3]
            if context and token==alias[2]:
                self.handoff_alias=None
            elif context and token==alias[1] and action==4:
                # Alias only this known logical/Native replacement. A new-token
                # command retires it, with no arbitrary elapsed-time cutoff.
                token=alias[2]
                self.handoff_alias=None
        if self.pending_begin:
            if self.pending_begin[:2] != (group,token):
                return REJECT
            if action == 4:
                self.pending_begin=None;self._take_fact(True);return 0
            if action == 2:
                self.pending_begin=(group,new_token,self.now());self.last_take=(group,new_token)
                self.handoff_alias=(group,token,new_token,None)
                self._take_fact(True);return 0
            return REJECT
        if take is not None and take.group==group and take.following is not None:
            if token in (take.take_id,take.following.take_id) and action==4:
                following_token=take.following.take_id
                status=self.recording.cancel_following(group,following_token)
                if status not in (p.APPLIED,p.OUTCOME_QUEUED):return REJECT
                self.closed_following=(take,following_token)
                self.last_take=(group,following_token)
                self._take_fact(True)
                return 0
            # The old UI context may not cancel an already accepted handoff.
            if token==take.take_id and action in (2,4):return REJECT
        if take is None or take.group != group or take.take_id != token or take.state=='completed':
            return REJECT
        if action in (1,3):
            if take.state != 'recording':
                return REJECT
            self.recording.request_token=new_token if action==3 else None
            try:
                status=self.recording.accept(group,next_take=action==3)
            finally:
                self.recording.request_token=None
            if status not in (p.APPLIED,p.OUTCOME_QUEUED):
                return REJECT
            if action==3:
                self.handoff_alias=(group,token,new_token,take.following)
            self.recording.enqueue_ready()
        elif action in (2,4):
            command=SimpleNamespace(group=group)
            self.recording.cancel(command,retry=False)
            self.after=('retry',group,new_token,token) if action == 2 else None
            self.handoff_alias=(group,token,new_token,None) if action==2 else None
        else:
            return REJECT
        self._take_fact(True)
        return 0

    def _admit_begin(self):
        if not self.pending_begin:
            return
        group,token,started=self.pending_begin
        if group in self.failures:
            self.pending_begin=None;self._failed_take(group,token,self.failures[group]);return
        if not self.allow_audio_track_creation and group in self.pool_unavailable:
            self.pending_begin=None;self._failed_take(group,token,self.pool_unavailable[group]);return
        if self.now()-started > config.PREPARATION_TIMEOUT:
            self.pending_begin=None;self._failed_take(group,token,'preparation timeout');return
        if self.state.topology_dirty or group not in self.reserves:
            return
        admitted=False
        self.recording.request_token=token
        try:
            status=self.recording.begin(group)
            if status not in (p.APPLIED,p.OUTCOME_QUEUED):
                raise ValueError('recording admission refused')
            self.pending_begin=None
            alias=self.handoff_alias
            if alias is not None and alias[0]==group and alias[2]==token and alias[3] is None:
                self.handoff_alias=(group,alias[1],token,self.recording.take)
            admitted=True
        except DirectMonitoringError as exc:
            self._monitoring_failed(exc)
        except (RuntimeError,ValueError,AttributeError,IndexError) as exc:
            # Invalidate the stale prepared target once; maintenance can repair.
            self.reserves.pop(group,None)
            self.events.append(dict(event='begin_waiting',group=group,reason=str(exc)))
        finally:
            self.recording.request_token=None
        if admitted:self.recording.enqueue_ready()

    def _monitoring_failed(self, error):
        # A refused Native arm is not a reason to repeat that setter forever.
        # A fresh explicit record/patch action may retry this group's preparation.
        for group in error.groups:
            self.reserves.pop(group, None)
            if self.audio_inputs:
                self.audio_inputs.jobs.pop(group, None)
                self.audio_inputs.prepared_masks.pop(group, None)
                self.audio_inputs.busy.discard(group)
                self.audio_inputs.failed.add(group)
            self.failures[group] = str(error)
        if self.pending_begin and self.pending_begin[0] in error.groups:
            group, token, unused = self.pending_begin
            self.pending_begin = None
            self._failed_take(group, token, str(error))
        self.events.append(dict(event='monitor_preparation_failed', groups=error.groups, reason=str(error)))

    def tick(self,before_maintenance=None):
        if self._closed:
            return
        now=self.now()
        self.allocations.begin_callback()
        # Musical transitions run before structural maintenance or publication.
        if self.state.topology_dirty:
            # Discovery runs only here, never in MIDI dispatch.
            current=tuple(self.song.tracks)
            if self._known_created_topology is not None and len(current)==len(self._known_created_topology) and all(same(a,b) for a,b in zip(current,self._known_created_topology)):
                self.state.tracks=current
                self.state.topology_dirty=False
            else:
                self.state.discover()
                if self.audio_inputs:self.audio_inputs.discover()
                # Explicit Set setup can supply a formerly missing anchor.
                # Structural discovery confirms it; uncertain Native mutations
                # and routing failures remain latched until a separate retry.
                for group,reason in tuple(self.failures.items()):
                    if reason=='rightmost Remote subgroup unavailable' and group in self.state.anchors:
                        del self.failures[group]
            self._known_created_topology=None
            self.pool_unavailable.clear()
            self._shape_at=now
        take=self.recording.take
        if take is not None:
            self.state.observe(take.group)
        before=self.recording.take
        before_state=(before.state, before.clip) if before is not None else None
        self.recording.tick()
        bundle_failure=getattr(self.recording,'failed_bundle',None)
        if bundle_failure is not None:
            self.recording.failed_bundle=None
            self.pending_begin=None;self.after=None
            self._failed_take(*bundle_failure)
        take=self.recording.take
        if self.handoff_alias is not None:
            group,old_token,new_token,following=self.handoff_alias
            if following is None:
                pending=self.pending_begin is not None and self.pending_begin[:2]==(group,new_token)
                cleaning=self.after==('retry',group,new_token,old_token)
                if not pending and not cleaning:self.handoff_alias=None
            elif (following.state=='completed' or
                    (take is not following and (take is None or take.following is not following))):
                self.handoff_alias=None
        if (take is not None and take.state in ('recording','finish_queued')
                and take.clip is not None and take.clip.is_recording):
            if self.closed_following is not None and take is not self.closed_following[0]:
                self.closed_following=None
            self.failed_take=None
            if self.pending_begin is not None and self.pending_begin[:2] != (take.group,take.take_id):
                self.pending_begin=None;self.after=None
        if before is not None and (take is not before or before_state!=(before.state,before.clip)):
            self.state.changed_row(before.group,before.row)
            self.pool_unavailable.pop(before.group,None)
        if self.after and (take is None or take.state=='completed'):
            unused,g,token,old_token=self.after;self.after=None
            self.state.observe(g)
            self.pending_begin=(g,token,now);self.last_take=(g,token)
        self._admit_begin()
        if now-self._clock_at>=.2:
            self._clock_at=now
            try:
                grid=read_grid(self.song)
                self._clock(8,grid)
                if now-self._refclock_at>=.5:
                    self._refclock_at=now
                    for group in self.state.groups:self._clock(group,grid)
            except (RuntimeError,ValueError,AttributeError):pass
        if now-self._take_at>=.1:
            self._take_at=now;self._take_fact(True)
        if before_maintenance is not None:
            # Publish known musical transitions before a synchronous Native
            # preparation call can block the display callback. The caller owns
            # one shared output budget across this hook and the final flush.
            self._take_fact()
            before_maintenance()
        if self.restoration:
            self.restoration.tick()
            if self.restoration.done:self.restoration=None
        if now-self._slow_at>=.1:
            self._slow_at=now
            if self.audio_inputs:self.audio_inputs.observe_sources()
            group=self._cursor;self._cursor=(self._cursor+1)%8
            if group in self.state.groups:
                self.state.observe(group)
                self._group_facts(group)
            self._global_facts()
            priority=self.allocations.group
            if priority is None:
                priority=self.pending_begin[0] if self.pending_begin else (take.group if take is not None and take.state!='completed' else None)
            if priority is None and self.audio_inputs:
                priority=next(iter(sorted(self.audio_inputs.busy)),None)
            if priority is not None and priority not in self.reserves and priority not in self.failures:
                group=priority
            self.state.scan_occupancy(group,64)
            self._maintenance(group)
        if self.monitoring:
            try:self.monitoring.tick()
            except DirectMonitoringError as error:self._monitoring_failed(error)
            except (RuntimeError,ValueError,AttributeError) as error:
                self.events.append(dict(event='monitor_preparation_failed',reason=str(error)))
        if self.audio_inputs:self.audio_inputs.flush_state()

    def _row(self, group):
        item=self.state.groups[group]
        active=self.recording.take
        if active is not None and active.group==group and active.state!='completed':
            return active.row
        reference=self.state.reference(group)
        if reference:
            return next(c.row for c in item.playing if same(c.clip,reference.clip))
        row=self.recording._preferred_rows.get(group,item.fresh_row)
        return item.next_free_row(row)

    def _compatible(self,track,parent,row):
        if (track.is_foldable or not track.has_audio_input or track.is_frozen or track.arm
                or not self.recording._quiet(track) or row>=len(track.clip_slots)
                or track.clip_slots[row].has_clip or track.clip_slots[row].is_triggered):
            return False
        if label(track.input_routing_type) != config.INPUT_TYPE:
            return False
        if not self._channel_one(track.input_routing_channel):
            return False
        return self._parent_output(track,parent) and track.current_monitoring_state == 2

    @staticmethod
    def _channel_one(value):
        name=label(value).strip()
        return '/' not in name and bool(re.search(r'\(1\)$|^1(?:\s|$)',name))

    @staticmethod
    def _parent_output(track,parent):
        if label(track.output_routing_type) != str(parent.name):
            return False
        return not label(getattr(track,'output_routing_channel','')).strip()

    def _prepare_appearance(self,track):
        # Only our new/default trial tracks get source labels. Never rename a
        # user's custom musical track, or rewrite existing clip names/colors.
        name=str(track.name)
        if name not in ('Empty','Audio') and not re.fullmatch(r'\d+[- ]Audio',name):
            return
        try:
            track.name=config.INPUT_NAME
            track.color_index=config.INPUT_COLOR_INDEX
        except (RuntimeError,ValueError,AttributeError) as exc:
            # Cosmetic metadata must not disable a correctly routed destination.
            self.events.append(dict(event='source_appearance_unavailable',reason=str(exc)))

    @property
    def allocation_ticket(self):return self.allocations.ticket

    @property
    def allocation_granted(self):return self.allocations.granted

    def _allocation_context(self,group):
        item=self.state.groups.get(group)
        anchor=self.state.anchors.get(group)
        if item is None or anchor is None:return None
        revision=self.audio_inputs.revisions.get(group,0) if self.audio_inputs else 0
        return AllocationContext(group,item.group,anchor,self._row(group),revision)

    def _maintenance(self,group):
        if self.allocations.pending is not None:
            try:context=self._allocation_context(self.allocations.group)
            except NATIVE_ERRORS + (ValueError,KeyError,IndexError) as error:
                native_error(error)
                self.allocations.clear()
                self.state.topology_dirty=True
                return
            self.allocations.validate(context)
            if context is None:return
        if group not in self.state.groups or group in self.failures:
            if self.allocations.group==group:self.allocations.clear()
            return
        if self.allocations.pending is not None and self.allocations.group!=group:return
        active=self.recording.take
        if active is not None and active.state in ('queued','finish_queued'):
            issued=active.finish_issued if active.state=='finish_queued' else active.start_issued
            # Once Native owns the queued transition, observe its actual start
            # or handoff before another synchronous create/routing call. A
            # narrow time margin cannot bound Live's allocator duration.
            if issued:return
            target=active.finish_target if active.state=='finish_queued' else active.target
            if target is not None and 0 <= target-self.song.current_song_time <= self.song.tempo/60.*.3:
                return
        self.allocations.begin_maintenance()
        try:self._prepare_maintenance(group,active)
        finally:self.allocations.end_maintenance(group)

    def _prepare_maintenance(self,group,active):
        item=self.state.groups[group]
        if group not in self.state.anchors:
            self.failures[group]='rightmost Remote subgroup unavailable';return
        active_phrase=active is not None and active.group==group and active.state!='completed'
        fresh_phrase=self.state.reference(group) is None and not active_phrase
        if fresh_phrase and group not in self.state.occupancy_known:
            return
        row=self._row(group)
        # A concurrent manual clip must never be folded into the fresh phrase.
        if fresh_phrase and any(row<len(t.clip_slots) and t.clip_slots[row].has_clip for t in item.tracks):
            self.recording._preferred_rows[group]=row+1
            return
        if row >= len(self.song.scenes):
            if not self.allow_audio_track_creation:
                self.reserves.pop(group,None)
                self.pool_unavailable[group]='prepared phrase row unavailable'
                return
            self.song.create_scene(-1);return
        if self.audio_inputs:
            self.audio_inputs.maintenance(group,row,active)
            return
        reserve=self.reserves.get(group)
        if reserve is not None:
            if reserve.row==row and self._compatible(reserve.track,item.group,row):
                self.pool_unavailable.pop(group,None)
                return
            self.reserves.pop(group,None)
        if not self.allow_audio_track_creation:
            # This performance path may consume only the existing configured
            # graph. In particular it never resumes any routing/create task.
            try:
                exclude=[active.track] if active is not None and active.state!='completed' else []
                if active is not None and active.following is not None:
                    exclude.append(active.following.track)
                candidate=next((t for t in item.tracks if not any(same(t,x) for x in exclude)
                                and self._compatible(t,item.group,row)),None)
                if candidate is None:
                    self.pool_unavailable[group]='prepared Audio pool unavailable'
                else:
                    self._prepare_appearance(candidate)
                    self.reserves[group]=Reserve(group,item.group,candidate,candidate.clip_slots[row],row)
                    self.pool_unavailable.pop(group,None)
            except (RuntimeError,ValueError,AttributeError,IndexError):
                self.pool_unavailable[group]='prepared Audio pool changed'
            return
        task=self.tasks.get(group)
        if task is not None and (task['row']!=row or not same(task['parent'],item.group)):
            # Clear or a replaced group invalidates the old phrase target.
            # Keep any already created Track in the Set, but choose again from
            # existing compatible lanes instead of publishing a stale reserve.
            self.tasks.pop(group,None);task=None
        try:
            if task is None:
                exclude=[active.track] if active is not None and active.state!='completed' else []
                if active is not None and active.following is not None:
                    exclude.append(active.following.track)
                candidate=next((t for t in item.tracks if not any(same(t,x) for x in exclude)
                                and self._compatible(t,item.group,row)),None)
                if candidate:
                    self._prepare_appearance(candidate)
                    self.reserves[group]=Reserve(group,item.group,candidate,candidate.clip_slots[row],row)
                    return
                # Keep one bounded repair lifecycle; a second creation must
                # not replace a still-observing restoration and leak listeners.
                if self.restoration is not None and not self.restoration.done:
                    return
                with self.allocations.operation(self._allocation_context(group)) as allowed:
                    if not allowed:return
                    anchor=self.state.anchors[group]
                    before=tuple(self.song.tracks)
                    index=next(i for i,t in enumerate(before) if same(t,anchor))
                    restore=SelectionRestoration(self.song,now=self.now)
                    # Register the preparation attempt before Native effects, never retry a possible creation.
                    task=dict(parent=item.group,row=row,track=None,stage='created',at=self.now())
                    self.tasks[group]=task
                    started=time.monotonic_ns();creation_error=None
                    try:self.song.create_audio_track(index)
                    except Exception as exc:creation_error=exc
                    finally:self.events.append(dict(event='create_audio_track_native',group=group,elapsed_ns=time.monotonic_ns()-started))
                    after=tuple(self.song.tracks)
                    created=[t for t in after if not any(same(t,old) for old in before)]
                    if len(created)==1:
                        restore.capture_created(created);self.restoration=restore
                    else:
                        restore.close()
                    if len(created)!=1:raise ValueError('created track identity unavailable; not replayed')
                    track=created[0];task['track']=track
                    if not same(track.group_track,item.group):raise ValueError('created track outside remote group')
                    # Update retained known topology after our own insertion. Deferred
                    # tracks notifications do not force a whole-Set rediscovery.
                    self.state.tracks=after
                    self.state.groups[group]=GroupState(group,item.group,item.tracks+(track,),item.playing,item.recording)
                    self._known_created_topology=after
                    self.state.topology_dirty=True
                    if creation_error is not None:raise creation_error
                    return
            track=task['track']
            if track is None:raise ValueError('ambiguous previous preparation')
            if task['stage']=='created':
                # Native implicit parent assignment may settle on its next callback.
                if not self._parent_output(track,task['parent']):
                    if self.now()-task['at'] > config.PREPARATION_TIMEOUT:
                        raise ValueError('parent routing preparation timeout')
                    return
                track.current_monitoring_state=2;task['stage']='monitor';task['at']=self.now();return
            if task['stage']=='monitor':
                choices=[c for c in track.available_input_routing_types if label(c)==config.INPUT_TYPE]
                if len(choices)!=1:raise ValueError('external input unavailable')
                track.input_routing_type=choices[0];task['stage']='input';task['at']=self.now();return
            if task['stage']=='input':
                choices=[c for c in track.available_input_routing_channels if self._channel_one(c)]
                if len(choices)!=1:raise ValueError('mono input1 unavailable')
                track.input_routing_channel=choices[0];task['stage']='verify';task['at']=self.now();return
            if self._compatible(track,task['parent'],task['row']):
                self._prepare_appearance(track)
                self.reserves[group]=Reserve(group,task['parent'],track,track.clip_slots[task['row']],task['row'])
                del self.tasks[group]
            elif self.now()-task['at'] > config.PREPARATION_TIMEOUT:
                raise ValueError('prepared target verification timeout')
        except (RuntimeError,ValueError,AttributeError,IndexError,StopIteration) as exc:
            self.failures[group]=str(exc)
            self.events.append(dict(event='maintenance_failed',group=group,reason=str(exc)))
            if self.restoration and self.restoration.state=='captured':self.restoration.close();self.restoration=None

    def _create_audio_lane(self,group,row):
        if self.restoration is not None and not self.restoration.done:return None
        with self.allocations.operation(self._allocation_context(group)) as allowed:
            if not allowed:return None
            item=self.state.groups[group];anchor=self.state.anchors[group]
            before=tuple(self.song.tracks)
            index=next(i for i,t in enumerate(before) if same(t,anchor))
            restore=SelectionRestoration(self.song,now=self.now)
            started=time.monotonic_ns();error=None
            try:self.song.create_audio_track(index)
            except Exception as exc:error=exc
            finally:self.events.append(dict(event='create_audio_track_native',group=group,elapsed_ns=time.monotonic_ns()-started))
            after=tuple(self.song.tracks)
            created=[t for t in after if not any(same(t,old) for old in before)]
            if len(created)==1:
                restore.capture_created(created);self.restoration=restore
            else:restore.close()
            if len(created)!=1:raise ValueError('created Audio identity unavailable; not replayed')
            track=created[0]
            if not same(track.group_track,item.group):raise ValueError('created Audio outside remote group')
            self.state.tracks=after
            self.state.groups[group]=GroupState(group,item.group,item.tracks+(track,),item.playing,item.recording)
            self._known_created_topology=after;self.state.topology_dirty=True
            if error is not None:raise error
            return track
