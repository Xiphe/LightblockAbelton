"""Semantic MIDI adapter. ACKs use the fast path; maintenance uses display ticks."""
import json
import os
from pathlib import Path
import time
import Live
from _Framework.ControlSurface import ControlSurface

from . import configuration as config
from .backend import Backend, REJECT
from .health import HealthPublisher, READY, NO_GROUPS, FAILURE, NO_REMOTE_GROUPS, HOST_FAILURE, RECORDING_BUSY, SAFE_MODAL


class PerformanceController(ControlSurface):
    def __init__(self,c_instance):
        self._closed=False
        self._generation=None
        self._frame=None
        self._constant_frame=None
        self._last=None
        self._observation_seq=0
        self._trace_at=0.
        self._status_at=None
        self._status_error_reported=False
        self._display_error=None
        self.backend=None
        self._bound_path=None
        super().__init__(c_instance)
        self.health=HealthPublisher(self._send_many,time.monotonic)
        self._bind()

    def _target(self):
        path=str(self.song().file_path)
        targets=getattr(config,'TARGET_SETS',())
        if targets:
            return (isinstance(targets,tuple) and bool(path)
                    and all(isinstance(p,str) and bool(p) for p in targets) and path in targets)
        return bool(path) and bool(config.TARGET_SET) and path==config.TARGET_SET

    def _bind(self):
        if not self._target():return False
        path=str(self.song().file_path)
        disposal_error=None
        if self.backend is not None and self._bound_path!=path:
            previous=self.backend
            self.backend=None;self._bound_path=None
            self._generation=None;self._frame=None;self._last=None
            self._constant_frame=None
            try:previous.close()
            except Exception as exc:disposal_error=str(exc)
        if self.backend is None:
            q=Live.Song.Quantization
            quantizations={name:getattr(q,name) for name in ('q_bar','q_half','q_quarter','q_eight','q_sixtenth') if hasattr(q,name)}
            self.backend=Backend(self.song(),quantizations,config.PARAMETER_BINDINGS)
            self._bound_path=path
            if disposal_error:self.backend.events.append(dict(event='previous_backend_close_failed',reason=disposal_error))
        return bool(self.backend.state.groups)

    def build_midi_map(self,handle):
        super().build_midi_map(handle)
        for channel in (0,1):
            for cc in range(69,75):
                Live.MidiMap.forward_midi_cc(self._c_instance.handle(),handle,channel,cc)
        for cc in (78,79,109):
            Live.MidiMap.forward_midi_cc(self._c_instance.handle(),handle,15,cc)

    def _cc(self,channel,cc,value):
        self._send_midi((0xB0+channel,cc,int(value)),optimized=False)

    def _send_many(self,messages):
        for message in messages:self._send_midi(message,optimized=False)

    def _ack(self,seq,key,value):
        for cc,data in ((69,self._generation),(75,seq),(76,key&127),(77,value&127),(78,(value>>7)&127),(79,seq)):
            self._cc(key>>7,cc,data)

    def _fact(self,kind,a,b):
        seq=self._observation_seq;self._observation_seq=(seq+1)&127
        fields=((89,self._generation),(90,seq),(91,kind))
        fields+=tuple((92+i,(a>>(7*i))&127) for i in range(4))
        fields+=tuple((96+i,(b>>(7*i))&127) for i in range(4))
        fields+=((100,seq),)
        for cc,value in fields:self._cc(0,cc,value)

    def receive_midi(self,message):
        if self._closed or len(message)!=3:return
        status,cc,value=message
        if status==0xBF and cc==109:
            allowed=(self.backend is not None and self._target()
                     and self._bound_path==str(self.song().file_path) and not self.backend.busy)
            self._cc(15,109 if allowed else 108,value)
            return
        if status==0xBF and cc in (78,79):
            if not 1<=value<=127 or not self._bind():return
            if cc==78 or self._generation!=value:
                self._generation=value;self._frame=None;self._last=None;self._observation_seq=0
                self._constant_frame=None
                self.backend.reseed()
            self._cc(15,79,value)
            return
        if status not in (0xB0,0xB1) or self._generation is None:return
        frame_name='_constant_frame' if status==0xB1 else '_frame'
        if cc==69:
            setattr(self,frame_name,[value] if value==self._generation else None)
            return
        frame=getattr(self,frame_name)
        if frame is None:return
        expected=69+len(frame)
        if cc!=expected or not 0<=value<=127:
            setattr(self,frame_name,None);return
        frame.append(value)
        if cc!=74:return
        setattr(self,frame_name,None)
        generation,seq,key,lo,hi,end=frame
        if status==0xB1:key|=128
        if seq!=end:return
        command=(generation,seq,key,lo|(hi<<7))
        if not self._target() or self.backend is None or self._bound_path!=str(self.song().file_path):
            self._ack(seq,key,REJECT);return
        if self._last is not None and self._last[0]==command:
            self._ack(seq,key,self._last[1]);return
        # Same wire sequence with a different payload is never a second command.
        if self._last is not None and self._last[0][:2]==command[:2]:
            self._ack(seq,key,REJECT);return
        started=time.monotonic_ns()
        actual=self.backend.dispatch(key,command[3])
        self._last=(command,actual)
        self._ack(seq,key,actual)
        self.backend.events.append(dict(event='semantic_command',key=key,seq=seq,elapsed_ns=time.monotonic_ns()-started,result=actual))

    def _trace(self):
        if self.backend is None or not config.TRACE_PATH or not self.backend.events:return
        now=time.monotonic()
        if now-self._trace_at<.5:return
        self._trace_at=now
        events=list(self.backend.events);self.backend.events.clear()
        try:
            with Path(config.TRACE_PATH).open('a') as target:
                for event in events:target.write(json.dumps(event,sort_keys=True)+'\n')
        except (OSError,TypeError,ValueError):pass

    def _status(self):
        """Private telemetry from retained Python bookkeeping, never Native reads."""
        if self.backend is None or not config.STATUS_PATH:
            return
        now=time.monotonic()
        if self._status_at is not None and now-self._status_at<.5:
            return
        # A failed write consumes the interval too; no retry or log storm.
        self._status_at=now
        temporary=None
        try:
            backend=self.backend
            take=backend.recording.take
            payload=dict(
                sampled_monotonic=now,
                generation=self._generation,
                allow_audio_track_creation=backend.allow_audio_track_creation,
                display_error=self._display_error,
                pool_unavailable=dict(backend.pool_unavailable),
                groups=sorted(backend.state.groups),
                constants=sorted(backend.state.constants),
                constants_issues=dict(backend.state.constants_issues),
                reserves=[dict(group=group,row=reserve.row)
                          for group,reserve in sorted(backend.reserves.items())],
                tasks=[dict(group=group,stage=task.get('stage'),row=task.get('row'))
                       for group,task in sorted(backend.tasks.items())],
                failures=dict(backend.failures),
                pending_begin=backend.pending_begin,
                after=backend.after,
                topology_dirty=backend.state.topology_dirty,
                take=(dict(group=take.group,token=take.token&127,state=take.state,
                           row=take.row,target=take.target,anchor=take.anchor)
                      if take is not None else None))
            inputs=backend.audio_inputs
            if inputs is not None:
                payload['audio_inputs']=dict(
                    desired=dict(inputs.desired),prepared=dict(inputs.prepared_masks),
                    available=inputs.available,monitor_owner=inputs.monitor_owner,
                    busy=sorted(inputs.busy),failed=sorted(inputs.failed),
                    catalog_size=len(inputs.catalog_tracks),
                    storage_pending=inputs.state_store.pending,
                    storage_errors=list(inputs.state_store.errors),
                    catalog_issues=dict(inputs.issues),
                    tasks=[dict(group=g,stage=job.get('stage'),row=job.get('row'),
                                revision=job.get('revision')) for g,job in sorted(inputs.jobs.items())])
                if take is not None:
                    payload['take']['sources']=[m.source.index for m in (take,)+tuple(getattr(take,'members',()))
                                                if getattr(m,'source',None) is not None]
            target=Path(config.STATUS_PATH)
            temporary=target.with_name('.'+target.name+'.tmp')
            temporary.write_text(json.dumps(payload,sort_keys=True)+'\n')
            os.replace(str(temporary),str(target))
        except Exception as exc:
            if temporary is not None:
                try:temporary.unlink()
                except OSError:pass
            if not self._status_error_reported:
                self._status_error_reported=True
                try:self.backend.events.append(dict(event='status_write_failed',reason=str(exc)))
                except Exception:pass

    def update_display(self):
        super().update_display()
        if self._closed:return
        try:
            if self._target():
                self._bind()
                remaining=[6]
                def flush_facts():
                    if self._generation is not None and remaining[0]>0:
                        facts=self.backend.drain_facts(remaining[0])
                        remaining[0]-=len(facts)
                        for fact in facts:self._fact(*fact)
                self.backend.tick(before_maintenance=flush_facts)
                flush_facts()
                state=READY if self.backend.state.groups else NO_GROUPS
                reason=0 if state==READY else NO_REMOTE_GROUPS
                flags=RECORDING_BUSY if self.backend.busy else SAFE_MODAL
                self.health.publish(state,reason,flags)
                self._display_error=None
                self._trace()
                self._status()
            else:self.health.publish(NO_GROUPS,NO_REMOTE_GROUPS,0)
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)
            if self.backend is not None:
                if error!=self._display_error:
                    self.backend.events.append(dict(event='display_failed',reason=str(exc),error_type=type(exc).__name__))
                self._display_error=error
                # Preserve diagnostic evidence even when the successful-display
                # branch is never reached. Existing trace/status cadence applies.
                self._trace();self._status()
            self.health.publish(FAILURE,HOST_FAILURE,RECORDING_BUSY if self.backend and self.backend.busy else 0)

    def disconnect(self):
        self._closed=True
        if self.backend:self.backend.close()
        super().disconnect()
