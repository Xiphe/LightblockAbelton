"""Routing gestures retain physical overlap and catalog identity without Live."""
from copy import deepcopy
import unittest

from test_device_ui import Host
from LightpadController import protocol as p
from LightpadController.device_ui import DeviceUI


class PatchHost(Host):
    def __init__(self):
        super().__init__()
        self.fields.extend(deepcopy(self.fields[:4]))
        self.sources=[{'index':n,'name':'Source '+str(n),'track':object(),
                       'colour':(20,50,100),'midi':False,'owners':0,'available':True} for n in range(6)]
        self.assignments=[0]*8
        self.actions=[];self.patch_callbacks=[];self.delayed=False;self.patch_busy=False
        self.ui=DeviceUI(self.send,lambda group:self.fields[group],lambda:self.meter,lambda:self.now,
                         monitor=lambda:3,can_record=lambda group:bool(self.assignments[group]),
                         patch_snapshot=lambda:{'sources':tuple(self.sources),'assignments':tuple(self.assignments)},
                         patch_toggle=self.toggle)

    def toggle(self,group,source,callback):
        if self.patch_busy:return False
        self.actions.append((group,source))
        if self.delayed:
            self.patch_busy=True;self.patch_callbacks.append(callback)
        else:
            self.assignments[group]^=1<<source;callback(p.APPLIED)
        return True

    def contact(self,kind,finger,x,y):
        self.ui.touch(kind,finger,x,y,role=6,view=3,group=0)

    def complete(self,outcome=p.APPLIED):
        self.patch_busy=False;self.patch_callbacks.pop(0)(outcome)


class PatchGestureTests(unittest.TestCase):
    def test_input_first_and_group_first_both_toggle_once_and_restore_name_on_group_release(self):
        for input_first in (False,True):
            h=PatchHost()
            first=(1,1,5.5) if input_first else (0,1,.5)
            second=(0,1,.5) if input_first else (1,1,5.5)
            h.contact(1,*first)
            if input_first:self.assertEqual(h.ui.patch_presentation()['held_input'],0)
            h.contact(1,*second)
            self.assertEqual(h.actions,[(0,0)])
            self.assertEqual(h.ui.patch_presentation()['context'],0)
            self.assertEqual(h.ui.patch_presentation()['held_input'],127)
            for _ in range(5):h.contact(2,1,1,5.5)
            self.assertEqual(h.actions,[(0,0)])
            h.contact(3,0,1,.5)
            self.assertEqual(h.ui.patch_presentation()['held_input'],0)

    def test_non_patch_role_cannot_create_patch_context(self):
        h=PatchHost()
        h.ui.touch(1,0,1,.5,role=0,view=3,group=0)
        h.contact(1,1,1,5.5)
        self.assertEqual(h.actions,[])
        self.assertEqual(h.ui.patch_presentation()['context'],127)

    def test_multiple_inputs_and_retap_produce_one_action_per_new_overlap(self):
        h=PatchHost();h.contact(1,0,1,.5)
        h.contact(1,1,1,5.5);h.contact(1,2,4,5.5)
        self.assertEqual(h.actions,[(0,0),(0,1)])
        h.contact(3,1,1,5.5);h.contact(1,1,1,5.5)
        self.assertEqual(h.actions,[(0,0),(0,1),(0,0)])

    def test_multiple_group_fingers_exclude_context_until_only_one_remains(self):
        h=PatchHost();h.contact(1,0,1,.5);h.contact(1,1,5,.5);h.contact(1,2,1,5.5)
        self.assertEqual(h.actions,[]);self.assertEqual(h.ui.patch_presentation()['context'],127)
        h.contact(3,1,5,.5)
        self.assertEqual(h.actions,[(0,0)])

    def test_same_count_source_reorder_cancels_without_retargeting_held_fingers(self):
        h=PatchHost();h.contact(1,1,1,5.5)
        h.sources[0],h.sources[1]=h.sources[1],h.sources[0]
        h.ui.tick();h.contact(1,0,1,.5)
        self.assertEqual(h.actions,[]);self.assertEqual(h.ui.patch_presentation()['context'],127)
        h.contact(3,0,1,.5);h.contact(3,1,1,5.5)
        h.contact(1,0,1,.5);h.contact(1,1,1,5.5)
        self.assertEqual(h.actions,[(0,0)])

    def test_unissued_fifo_drains_once_and_is_dropped_when_group_context_ends(self):
        h=PatchHost();h.delayed=True;h.contact(1,0,1,.5);h.contact(1,1,1,5.5);h.contact(1,2,4,5.5)
        self.assertEqual(h.actions,[(0,0)])
        h.complete();self.assertEqual(h.actions,[(0,0),(0,1)])
        h.contact(1,3,7,5.5);h.contact(3,0,1,.5);h.complete()
        self.assertEqual(h.actions,[(0,0),(0,1)])

    def test_async_completion_rechecks_catalog_before_draining_unissued_indices(self):
        h=PatchHost();h.delayed=True
        h.contact(1,0,1,.5);h.contact(1,1,1,5.5);h.contact(1,2,4,5.5)
        self.assertEqual(h.actions,[(0,0)])
        h.sources[0],h.sources[1]=h.sources[1],h.sources[0]
        h.complete()
        self.assertEqual(h.actions,[(0,0)])
        self.assertEqual(h.ui.patch_presentation()['context'],127)
        h.ui.tick();self.assertEqual(h.actions,[(0,0)])

    def test_async_completion_drops_unissued_actions_after_contact_lease(self):
        h=PatchHost();h.delayed=True
        h.contact(1,0,1,.5);h.contact(1,1,1,5.5);h.contact(1,2,4,5.5)
        h.now+=1.01;h.complete()
        self.assertEqual(h.actions,[(0,0)])
        self.assertEqual(h.ui.patch_presentation()['context'],127)

    def test_reset_and_missing_contact_never_synthesize_a_patch(self):
        h=PatchHost();h.contact(1,1,1,5.5);h.now+=1.01;h.ui.tick()
        h.contact(1,0,1,.5);self.assertEqual(h.actions,[])
        h.ui.reset();h.contact(3,1,1,5.5);self.assertEqual(h.actions,[])

    def test_uncertain_native_exception_is_not_retried(self):
        h=PatchHost()
        def uncertain(group,source,callback):h.actions.append((group,source));raise RuntimeError('after effect unknown')
        h.ui._patch_toggle_callback=uncertain
        h.contact(1,0,1,.5);h.contact(1,1,1,5.5)
        for _ in range(4):h.ui.tick()
        self.assertEqual(h.actions,[(0,0)])
        self.assertEqual(h.ui.patch_presentation()['flash'],0)

    def test_no_source_down_preview_is_yellow_until_release_and_l_advance_still_works(self):
        h=PatchHost()
        for kind,y in ((1,1.5),(2,4.5)):
            h.ui.touch(kind,0,1.5,y,role=6,view=0,group=0)
        self.assertEqual(h.ui.presentation()[4]&44,p.NO_SOURCE_PREVIEW)
        h.ui.touch(3,0,1.5,4.5,role=6,view=0,group=0)
        self.assertEqual(h.sent,[])
        h.ui.touch(1,0,1.5,1.5,role=6,view=0,group=0)
        h.ui.touch(2,0,1.5,4.5,role=6,view=0,group=0)
        h.ui.touch(2,0,4.5,4.5,role=6,view=0,group=0)
        self.assertEqual(h.ui.presentation()[4]&44,p.ADVANCE_PREVIEW)
        h.ui.touch(3,0,4.5,4.5,role=6,view=0,group=0)
        self.assertEqual(h.sent,[(0,p.ADVANCE,0)])

    def test_horizontal_swipe_selects_monitor_without_mute(self):
        h=PatchHost();h.ui.touch(1,0,5.5,1.5,role=6,view=0,group=0)
        h.ui.touch(3,0,2.5,1.5,role=6,view=0,group=0)
        self.assertEqual(h.sent,[(1,p.SET_MONITOR,0)])
        self.assertEqual(h.ui.presentation()[-1],3)

    def test_constant_hold_uses_top_faders_and_lower_pan_without_recording(self):
        h=PatchHost();h.ui.touch(1,0,1,8.5,role=6,view=0,group=0)
        h.now+=.6;h.ui.tick();self.assertEqual((h.ui.view,h.ui.selected_group),(1,8))
        h.ui.touch(1,1,1,5,role=0,view=1,group=8)
        h.ui.touch(2,1,1,3,role=0,view=1,group=8)
        self.assertEqual(h.sent[0][0:2],(8,p.SET_VOLUME))
        self.assertGreater(h.sent[0][2],64)


if __name__=='__main__':unittest.main()
