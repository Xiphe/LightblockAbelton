"""Current role/context routing and relative fullscreen meter interactions.

Native actions use the retained gesture implementation. Device geometry and
local target ticks never replace confirmed Live meter values.
"""
import math
from dataclasses import dataclass
from .device_ui_legacy import DeviceUI as LegacyUI
from . import protocol as p

@dataclass
class Binding:
    role:int
    view:int
    group:int
    started:float
    seen:float
    x:float
    y:float
    ignored:bool=False
    moved:bool=False
    value:int=0
    auxiliary:str=""
    step:int=0
    tap_eligible:bool=False
    tap_pair:bool=False


def meter_step(delta, step):
    """Two-LED anchored detents with a quarter-LED return hysteresis.

    Outward boundaries are inclusive; returning across a boundary is strict.
    The step stays unsaturated so clamping never moves the captured origin.
    """
    while (step >= 0 and delta >= 2 * (step + 1)) or (step < 0 and delta > 2 * step + .25):
        step += 1
    while (step <= 0 and delta <= 2 * (step - 1)) or (step > 0 and delta < 2 * step - .25):
        step -= 1
    return step


class DeviceUI(LegacyUI):
    METER_TAP_SECONDS = .25
    METER_PAIR_SECONDS = .35

    def reset(self):
        super().reset()
        self._roles={}
        self._aux={}
        self._pre_start={}
        self._meter_taps={}
    def tick(self):
        now=self._now()
        for role,released in list(self._meter_taps.items()):
            if now<released or now>released+self.METER_PAIR_SECONDS:self._meter_taps.pop(role,None)
        for index,contact in list(self._aux.items()):
            if now-contact.seen>=self.TOUCH_LEASE_SECONDS:self._drop_aux(index)
        if self.view!=0:
            self._meter_taps.clear()
            for index in list(self._aux):self._drop_aux(index)
        super().tick()
    def _flush(self,group):
        # Legacy tick/result can change view immediately before flushing.
        # Cancel hidden meter desires at that seam, before Native dispatch.
        if self.view!=0 and hasattr(self,'_aux'):
            self._meter_taps.clear()
            for index in list(self._aux):self._drop_aux(index)
            for op in self.METER_OPS:self._desired[0].pop(op,None)
        return super()._flush(group)
    def _drop_aux(self,index):
        contact=self._aux.pop(index,None)
        if contact is not None:
            contact.ignored=True
            if contact.role in (2,3):self._cancel_meter_tap(contact)
            if contact.role in (2,3,4):self._desired[0].pop(self.METER_OPS[contact.role-2],None)
    def _cancel_meter_tap(self,contact):
        self._meter_taps.pop(contact.role,None)
        contact.tap_eligible=False
        contact.tap_pair=False
    def _start_meter_tap(self,contact):
        released=self._meter_taps.pop(contact.role,None)
        overlap=[c for c in self._aux.values() if not c.ignored and c.role==contact.role]
        if overlap:
            for other in overlap:self._cancel_meter_tap(other)
            return
        contact.tap_eligible=True
        now=self._now()
        contact.tap_pair=released is not None and released<=now<=released+self.METER_PAIR_SECONDS
    def _end_meter_tap(self,contact,x,y,view,group,drawer_open):
        now=self._now()
        valid=(contact.tap_eligible and not contact.moved
               and contact.started<=now<=contact.started+self.METER_TAP_SECONDS
               and self._context(contact.role,view,group,drawer_open,x,y))
        if not valid:
            self._cancel_meter_tap(contact)
        elif contact.tap_pair:
            self._meter_taps.pop(contact.role,None)
            self._want(0,self.METER_OPS[contact.role-2],4)
        else:
            self._meter_taps[contact.role]=now
    def _orphan_meters(self):
        for index,contact in list(self._aux.items()):
            if contact.role in (2,3,4):self._drop_aux(index)
        for op in self.METER_OPS:self._desired[0].pop(op,None)
    def pre_touch(self,event):
        """Pure local cancellation BEFORE Surface's Native completion tick."""
        if event.kind==1:
            old=self._touches.get(event.index)
            if old is not None and not old.ignored:self._cancel_gesture()
            self._drop_aux(event.index)
            self._pre_start[event.index]=(self.view,self.recording_group,self.active_take)
            return
        contact=self._aux.get(event.index)
        if contact is None:return
        self._before_aux_event(event.index,event.role)
    @staticmethod
    def _auxiliary_at(drawer_open,x,y):
        return 'tap' if drawer_open and 8<=x<15 and 10<=y<15 else ''
    def _before_aux_event(self,index,role):
        contact=self._aux.get(index)
        if contact is None:return
        if role==5 and contact.role in (1,2,3,4):
            if contact.role==4:self._orphan_meters()
            else:self._drop_aux(index)
    def _context(self,role,view,group,drawer_open,x,y):
        if role==6:return self.view==view==0 and self.group_at(x,y) is not None
        if role==0:
            actual=self.selected_group if self.view==1 else self.recording_group
            return self.view==view and view in (1,2) and actual==group
        if role in (1,2,3,4):
            if self.view!=0 or view!=0:return False
            if role==1:return bool(self._auxiliary_at(drawer_open,x,y))
            if not drawer_open:return False
            if role==2:return 0<=x<7 and 1<=y<10
            if role==3:return 8<=x<15 and 1<=y<10
            return 0<=x<7 and 10<=y<15
        return False
    def touch(self,kind,index,x,y,role=None,view=None,group=None,drawer_open=False,can_accept=False,moved=False,dragged=False):
        context=getattr(self,'_frame_context',lambda:None)()
        if role is None and context is not None:
            role,view,group=context.role,context.view,context.group
            drawer_open,can_accept,moved,dragged=context.drawer_open,context.can_accept,context.moved,context.dragged
        if kind not in (1,2,3) or type(index) is not int or not 0<=index<16 or type(role) is not int or not 0<=role<7:return
        if any(type(v) is not bool for v in (drawer_open,can_accept,moved,dragged)) or moved and kind!=3:return
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (x,y)):return
        x,y=max(0.,min(14.99,x)),max(0.,min(14.99,y))
        old=self._roles.get(index)
        # Cancel before tick can release an old meter desire or record gesture.
        if kind==1 and old is not None:
            if index in self._touches and not self._touches[index].ignored:self._cancel_gesture()
            self._drop_aux(index)
        if kind!=1:self._before_aux_event(index,role)
        self.tick()
        if kind==1:
            binding=Binding(role,view,group,self._now(),self._now(),x,y)
            self._roles[index]=binding
            before=self._pre_start.pop(index,None)
            if role==0 and view==2 and before is not None and before!=(self.view,self.recording_group,self.active_take):
                binding.ignored=True;return
            if not self._context(role,view,group,drawer_open,x,y):
                if role in (2,3):self._meter_taps.pop(role,None)
                binding.ignored=True;return
            if role in (1,2,3,4):
                if role==1:
                    binding.auxiliary=self._auxiliary_at(drawer_open,x,y)
                else:
                    state=self._valid_meter()
                    if state is None:
                        if role in (2,3):self._meter_taps.pop(role,None)
                        binding.ignored=True;return
                    binding.value=(state[0],self.DENOMINATORS.index(state[1]),127 if state[2] else 0)[role-2]
                    if role in (2,3):self._start_meter_tap(binding)
                self._aux[index]=binding;return
            self._start(index,x,y)
            if role==0 and self.view==2:
                contact=self._touches.get(index)
                if contact is not None and contact.control in (1,3) and not can_accept:contact.control=None
            return
        binding=self._roles.get(index)
        if binding is None:return
        if binding.ignored or role!=binding.role:
            if kind==3:self._roles.pop(index,None)
            return
        binding.seen=self._now()
        if binding.role in (1,2,3,4):
            contact=self._aux.get(index)
            if contact is None:return
            contact.seen=self._now()
            if self.view!=0:self._drop_aux(index);return
            contact.moved|=abs(x-contact.x)>=1 or abs(y-contact.y)>=1 or moved or dragged
            if contact.role==1:
                if kind==3:
                    self._aux.pop(index,None);self._roles.pop(index,None)
                    if contact.auxiliary=='tap' and self._auxiliary_at(True,x,y)=='tap':
                        self._try_send(0,p.TAP_TEMPO,0)
                return
            state=self._valid_meter()
            if state is None:self._drop_aux(index);return
            number=contact.role-2
            if number!=2:
                if contact.moved:self._cancel_meter_tap(contact)
                contact.step=meter_step(contact.y-y,contact.step)
                raw=contact.value+contact.step
                value=max(1,min(99,raw)) if number==0 else self.DENOMINATORS[max(0,min(4,raw))]
                original=contact.value if number==0 else self.DENOMINATORS[contact.value]
                contact.moved|=value!=original
                op=self.METER_OPS[number]
                wanted=self._desired[0].get(op,self._pending_value(0,op,state[number]))
                if contact.moved and value!=wanted:self._want(0,op,value)
            if kind==3:
                self._aux.pop(index,None);self._roles.pop(index,None)
                if number==2 and 0<=x<7 and 10<=y<15:self._want(0,self.METER_OPS[2],0 if state[2] else 127)
                elif number!=2:self._end_meter_tap(contact,x,y,view,group,drawer_open)
            return
        allowed=(binding.role==6 and (self.view==0 or self.view==1 and index==self.anchor)
                 or binding.role==0 and self.view==binding.view and (self.selected_group if self.view==1 else self.recording_group)==binding.group)
        contact=self._touches.get(index)
        if not allowed or contact is None or contact.ignored:
            if contact is not None:contact.ignored=True
            if kind==3:self._touches.pop(index,None);self._roles.pop(index,None)
            return
        contact.seen=self._now()
        if kind==2:self._move(index,x,y)
        else:self._end(index,x,y);self._roles.pop(index,None)
