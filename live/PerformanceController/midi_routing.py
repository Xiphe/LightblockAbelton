"""Exclusive original-instrument routing through explicitly marked group receivers.

Commands reserve cached intent. Native routes and metadata change only in
maintenance; neither instruments nor their effects are copied.
"""
import json
from .model import same, live_valid, NATIVE_ERRORS, native_error
from .input_contract import RECEIVER_KEY, MIDI_OWNER_KEY

OFF, IN = 2, 0
try:
    import Live
    INSTRUMENT_TYPE = Live.Device.DeviceType.instrument
except (ImportError,AttributeError):
    INSTRUMENT_TYPE = 'instrument'  # explicit simulator/public enum spelling


def label(value):return str(getattr(value,'display_name',value))


def midi_route_equal(left,right):
    if same(left,right):return True
    attached=getattr(left,'attached_object',None)
    return (attached is not None and same(attached,getattr(right,'attached_object',None))
            and label(left)==label(right))


def source_busy(track):
    return (bool(track.arm) or getattr(track,'playing_slot_index',-1) not in (-1,-2)
            or getattr(track,'fired_slot_index',-1)!=-1)


def midi_profile(track):
    if (not getattr(track,'has_midi_input',False) or not getattr(track,'has_audio_output',False)
            or track.is_frozen):raise ValueError('MIDI instrument unavailable')
    devices=tuple(track.devices)
    if not devices:raise ValueError('MIDI source has no instrument')
    device=devices[0]
    kind=getattr(device,'type',None)
    if not (kind==INSTRUMENT_TYPE or str(kind).lower().rsplit('.',1)[-1]=='instrument'):
        raise ValueError('unsupported MIDI effects before instrument or ambiguous instrument endpoint')
    if getattr(device,'can_have_chains',False):
        raise ValueError('instrument rack endpoint requires explicit support')
    input_type,input_channel=track.input_routing_type,track.input_routing_channel
    if getattr(input_type,'attached_object',None) is not None or label(input_type).lower()=='no input':
        raise ValueError('MIDI source requires a configured external input')
    return device,input_type,input_channel


def endpoint(track,source):
    # Live offers MIDI channels as "1-<instrument>" etc. Track In is not
    # equivalent: it would stop clip playback when source monitoring is Off.
    names={str(source.instrument.name),str(getattr(source.instrument,'class_display_name',''))}
    choices=[r for r in track.available_output_routing_channels
             if label(r) in {'1-'+name for name in names if name}]
    if len(choices)!=1:raise ValueError('direct instrument endpoint unavailable or ambiguous')
    return choices[0]


class MidiRouting:
    def __init__(self,inputs):
        self.inputs,self.backend=inputs,inputs.backend
        self.receivers={};self.owners={};self.requests={};self.releasing=set()
        self.identities={};self.blocked=set();self.acquiring={}

    def owner(self,index):
        if index in self.requests:return self.requests[index]
        return None if index in self.releasing else self.owners.get(index)

    def discover(self):
        receivers={}
        for group,anchor in self.backend.state.anchors.items():
            matches=[]
            for track in self.backend.state.tracks:
                if not same(getattr(track,'group_track',None),anchor) or track.is_foldable:continue
                try:raw=track.get_data(RECEIVER_KEY,'')
                except NATIVE_ERRORS as error:
                    native_error(error);continue
                if not raw:continue
                try:marker=json.loads(raw) if isinstance(raw,str) else None
                except ValueError:continue
                if (isinstance(marker,dict) and type(marker.get('schema')) is int
                        and type(marker.get('group')) is int
                        and marker==dict(schema=1,group=group) and track.has_audio_input):matches.append(track)
            if len(matches)==1:receivers[group]=matches[0]
        self.receivers=receivers
        pending={self.identities.get(i):g for i,g in self.requests.items()}
        releasing={self.identities.get(i) for i in self.releasing}
        acquiring={self.identities.get(i):g for i,g in self.acquiring.items()}
        self.identities={i:self.inputs.state_store.source_ids[i] for i in self.inputs.sources}
        self.requests={i:pending[identifier] for i,identifier in self.identities.items()
                       if identifier in pending and pending[identifier] in self.backend.state.groups}
        self.releasing={i for i,identifier in self.identities.items() if identifier in releasing}
        self.acquiring={i:acquiring[identifier] for i,identifier in self.identities.items()
                        if identifier in acquiring and acquiring[identifier] in self.backend.state.groups}
        self.observe()

    def supported(self,source):
        try:
            midi_profile(source.track)
            if source_busy(source.track):raise ValueError('MIDI source has manual arm or clip activity')
            if source.index in self.blocked:raise ValueError('MIDI receiver ownership metadata is inconsistent')
            if any(p.value!=p.min for p in source.track.mixer_device.sends):
                raise ValueError('MIDI source sends must be dry to preserve group control')
            return True
        except NATIVE_ERRORS + (ValueError,) as error:
            native_error(error);self.inputs.issues[source.index]=str(error);return False

    def observe(self):
        owners={};blocked=set()
        for i,source in self.inputs.sources.items():
            if source.kind!='midi':continue
            attached=None
            try:
                attached=getattr(source.track.output_routing_type,'attached_object',None)
                raw=source.track.get_data(MIDI_OWNER_KEY,'')
                marker=json.loads(raw) if isinstance(raw,str) and raw else {}
                if not isinstance(marker,dict):marker={}
                group=marker.get('group');receiver=self.receivers.get(group) if type(group) is int else None
                if (type(marker.get('schema')) is int and marker.get('schema')==1 and marker.get('source')==self.inputs.state_store.source_ids[i]
                        and receiver is not None and same(attached,receiver)):
                    owners[i]=group
            except NATIVE_ERRORS + (ValueError,TypeError) as error:native_error(error)
            if i not in owners and i not in self.requests and i not in self.releasing:
                try:
                    marked=(live_valid(attached) and hasattr(attached,'get_data')
                            and bool(attached.get_data(RECEIVER_KEY,'')))
                except NATIVE_ERRORS as error:
                    native_error(error);marked=True
                if marked or any(same(attached,r) for r in self.receivers.values()):blocked.add(i)
        self.owners,self.blocked=owners,blocked

    def request(self,group,mask):
        chosen=[i for i,s in self.inputs.sources.items() if s.kind=='midi' and mask&(1<<i)]
        for i in chosen:
            if i in self.blocked:raise ValueError('MIDI ownership metadata is inconsistent')
            if self.owner(i) not in (None,group):raise ValueError('MIDI instrument belongs to another group')
        for i,g in tuple(self.requests.items()):
            if g==group and i not in chosen:
                self.requests.pop(i,None)
                if self.owners.get(i)!=group:self.releasing.add(i)
        for i in chosen:
            if self.owners.get(i)!=group or i in self.releasing:
                self.requests[i]=group;self.acquiring[i]=group

    def recordable(self,group,mask):
        return all(self.owner(i) in (None,group) and i not in self.blocked for i,s in self.inputs.sources.items()
                   if s.kind=='midi' and mask&(1<<i))

    def admission_ready(self,group,mask):
        return all(self.owners.get(i)==group and i not in self.requests and i not in self.releasing
                   for i,s in self.inputs.sources.items() if s.kind=='midi' and mask&(1<<i))

    def needs_work(self,group):
        return group in self.requests.values()

    def confirm(self,group):
        self.acquiring={i:g for i,g in self.acquiring.items() if g!=group}

    def cancel(self,group):
        # Roll back only leases first acquired by this failed preparation.
        # Previously accepted/playing instruments remain owned until Clear.
        for i in tuple(set(self.requests)|set(self.acquiring)):
            if self.requests.get(i)==group or self.acquiring.get(i)==group:
                self.requests.pop(i,None);self.acquiring.pop(i,None)
                self.releasing.add(i)

    def clear(self,group):
        for i in tuple(set(self.owners)|set(self.requests)):
            if self.owner(i)==group:
                self.requests.pop(i,None);self.acquiring.pop(i,None);self.releasing.add(i)
        selected=[s for i,s in self.inputs.sources.items() if self.inputs.desired.get(group,0)&(1<<i)]
        if self.inputs.monitor_owner==group and selected and all(s.kind=='midi' for s in selected):
            self.inputs.monitor_owner=None;self.inputs.state_store.set_monitor(None)

    def _receiver_valid(self,group):
        receiver=self.receivers.get(group);anchor=self.backend.state.anchors.get(group)
        if receiver is None or anchor is None or not live_valid(receiver):return False
        try:
            raw=receiver.get_data(RECEIVER_KEY,'')
            marker=json.loads(raw) if isinstance(raw,str) and raw else None
            return (isinstance(marker,dict) and type(marker.get('schema')) is int
                    and type(marker.get('group')) is int and marker==dict(schema=1,group=group)
                    and same(receiver.group_track,anchor) and not receiver.arm and not receiver.is_frozen
                    and label(receiver.input_routing_type).lower()=='no input'
                    and receiver.current_monitoring_state==IN and self.backend._parent_output(receiver,anchor)
                    and (getattr(receiver.output_routing_type,'attached_object',None) is None
                         or same(receiver.output_routing_type.attached_object,anchor)))
        except NATIVE_ERRORS + (ValueError,) as error:
            native_error(error);return False

    def _audible(self,index):
        group=self.owners.get(index)
        return (group is not None and group==self.inputs.monitor_owner
                and bool(self.inputs.desired.get(group,0)&(1<<index))
                and index not in self.releasing)

    def maintenance(self,group):
        # Retire an old owner before any new route can become audible.
        for i in tuple(self.releasing):
            source=self.inputs.sources.get(i)
            if source is None:self.releasing.discard(i);continue
            track=source.track
            if source_busy(track):continue
            if track.current_monitoring_state!=OFF:track.current_monitoring_state=OFF;return False
            choices=[r for r in track.available_output_routing_types if label(r).lower().replace(' ','')=='sendsonly']
            if len(choices)!=1:raise ValueError('MIDI release output unavailable')
            if not midi_route_equal(track.output_routing_type,choices[0]):track.output_routing_type=choices[0];return False
            if track.get_data(MIDI_OWNER_KEY,''):track.set_data(MIDI_OWNER_KEY,'')
            self.owners.pop(i,None);self.releasing.discard(i)
        # Silence every old input path before enabling any new owner. Source
        # Monitor Off leaves direct-instrument clip playback unaffected.
        for i,source in self.inputs.sources.items():
            if source.kind!='midi':continue
            if i not in self.owners and i not in self.requests and i not in self.releasing:continue
            if source_busy(source.track):continue
            wanted=self._audible(i)
            if not wanted and source.track.current_monitoring_state!=OFF:
                source.track.current_monitoring_state=OFF;return False
        for i,source in self.inputs.sources.items():
            if source.kind!='midi':continue
            track=source.track
            if source_busy(track):
                if self.requests.get(i)==group:raise ValueError('MIDI source has manual arm or clip activity')
                continue
            if self.requests.get(i)!=group:
                if self._audible(i) and track.current_monitoring_state!=IN:
                    track.current_monitoring_state=IN;return False
                continue
            if not self._receiver_valid(group):raise ValueError('marked group MIDI receiver unavailable')
            receiver=self.receivers[group]
            attached=getattr(track.output_routing_type,'attached_object',None)
            if any(g!=group and same(attached,r) for g,r in self.receivers.items()):
                raise ValueError('MIDI instrument output now belongs to another group')
            choices=[r for r in track.available_output_routing_types if same(getattr(r,'attached_object',None),receiver)]
            if len(choices)!=1:raise ValueError('MIDI receiver route unavailable or ambiguous')
            if not midi_route_equal(track.output_routing_type,choices[0]):track.output_routing_type=choices[0];return False
            payload=json.dumps(dict(schema=1,group=group,source=self.inputs.state_store.source_ids[i]),sort_keys=True,separators=(',',':'))
            if track.get_data(MIDI_OWNER_KEY,'')!=payload:track.set_data(MIDI_OWNER_KEY,payload)
            self.owners[i]=group
            wanted=IN if self._audible(i) else OFF
            if track.current_monitoring_state!=wanted:
                track.current_monitoring_state=wanted;return False
            self.requests.pop(i,None)
            return False
        return True

    def ready(self,group,source):
        return (not source_busy(source.track) and self.owners.get(source.index)==group and source.index not in self.releasing
                and self._receiver_valid(group)
                and same(getattr(source.track.output_routing_type,'attached_object',None),self.receivers[group]))
