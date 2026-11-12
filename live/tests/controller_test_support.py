"""Native recording fixture for current role-tagged Surface tests.

The real controller/Session/backend run against explicit fake Live objects.
Nothing opens Live, MIDI endpoints, device programs or local verification data.
"""
import unittest
import test_surface_touch as fixture
from LightpadController import protocol as p, touch_protocol

class RecordingSurfaceCase(unittest.TestCase):

    def setUp(self):
        self.f = fixture.SurfaceTouchTests()
        self.addCleanup(self.f.doCleanups)
        self.surface, self.instance = self.f.make()
        self.native = self.f.native
        self.now = self.f.helper
        self.seq = [0] * 16
        self.f.dispatch.reset_mock()

    def advance(self, seconds=0.05):
        self.now.now += seconds

    def tick(self, seconds=0.05):
        self.advance(seconds)
        self.surface.receive_midi((191, 19, 0))
        self.surface.update_display()

    def pump(self, seconds=0.2):
        for unused in range(round(seconds / 0.05)):
            self.tick(0.05)

    def raw(self, kind, role=0, finger=0, x=9.5, y=2.5, after=0.05, view=None, can_accept=False, **flags):
        self.advance(after)
        if view is None:
            view = self.surface._ui.view
        for message in touch_protocol.encode_touch(kind, finger, x, y, self.seq[finger], role, view=view, group=0, can_accept=can_accept, **flags):
            self.surface.receive_midi(message)
        self.seq[finger] = self.seq[finger] + 1 & 127

    def tap(self, x, y, can_accept=True, finger=0):
        self.raw(1, finger=finger, x=x, y=y, can_accept=can_accept)
        self.raw(3, finger=finger, x=x, y=y, can_accept=can_accept)

    def commands(self):
        return [(c.args[0].op, c.args[0].arg) for c in self.f.dispatch.call_args_list]

    def begin(self):
        self.raw(1, 6, x=1.5, y=1.5)
        self.raw(2, 6, x=1.5, y=4.5)
        self.raw(3, 6, x=1.5, y=4.5)
        self.assertEqual(self.commands(), [(p.BEGIN, 0)])
        take = self.surface._engine.session.take
        self.assertIsNotNone(take)
        self.assertEqual(take.state, 'queued')
        self.assertTrue(any((c[0] == 'fire' for c in self.native.calls)))
        self.assertEqual(self.surface._ui.view, 2)
        return (take, self.native.slot(take.slot))

    def start_native(self, take, slot):
        self.native.song.current_song_time = take.schedule.start_at
        slot.begin_native(take.schedule.start_at)
        self.tick()
        self.assertEqual(self.surface._engine.snapshot(0)[106], 2)
        self.assertEqual(self.surface._ui.active_take, take.token % 128)

    def record(self):
        take, slot = self.begin()
        self.start_native(take, slot)
        self.f.dispatch.reset_mock()
        return (take, slot)
