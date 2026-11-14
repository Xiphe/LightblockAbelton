"""Native route preparation advances only at a display-callback boundary."""
import unittest
from types import SimpleNamespace as NS

import test_surface
from test_surface_pacing import EightClockEngine
from live_test_support import Song
from LightpadController import protocol as p
from LightpadController.live_controller import LiveController


class RoutingPreparationTests(unittest.TestCase):
    def test_surface_only_advances_preparations_at_display_start(self):
        helper = test_surface.SurfaceTests()
        surface, _ = helper.make()
        self.addCleanup(helper.doCleanups)
        class Engine(EightClockEngine):
            advances = 0
            def advance_preparations(self): self.advances += 1
            def needs_tick(self): return True
        engine = Engine()
        surface._engine = engine
        surface.receive_midi((0xBF, 110, p.VERSION))
        for _ in range(5):
            surface.receive_midi((0xBF, 19, 0))
            surface.receive_midi((0xB0, 20, 1))
        self.assertEqual(engine.advances, 0)
        surface.update_display()
        self.assertEqual(engine.advances, 1)
        surface.refresh_state()
        self.assertEqual(engine.advances, 1)

    def make(self):
        controller = LiveController(Song(groups=1), quantizations={})
        job = NS(done=False, error=None)
        calls = []
        class Patches:
            enabled = False
            issues = {}
            def start_toggle(self, group, source):
                calls.append(('start', group, source))
                return job
            def advance_preparations(self):
                calls.append(('advance',))
                job.done = True
            def needs_preparation(self): return not job.done
        controller.patching = Patches()
        return controller, job, calls

    def test_ticks_cannot_advance_a_native_route_in_the_same_callback(self):
        controller, job, calls = self.make()
        command = p.Command(4, 0, p.TOGGLE_PATCH, 2)
        self.assertIsNone(controller.dispatch(command))
        self.assertFalse(controller.modal_allowed())
        for _ in range(5): self.assertEqual(controller.tick(), [])
        self.assertEqual(calls, [('start', 0, 2)])
        self.assertEqual(controller.dispatch(p.Command(5, 0, p.BEGIN, 0)), p.BUSY)
        controller.advance_preparations()
        self.assertEqual(controller.tick(), [(command, p.APPLIED)])
        self.assertEqual(controller.tick(), [], 'one acknowledgement only')
        self.assertTrue(controller.modal_allowed())
        self.assertEqual(calls.count(('advance',)), 1)

    def test_failed_preparation_finishes_once_without_retrying_the_intention(self):
        controller, job, calls = self.make()
        command = p.Command(4, 0, p.TOGGLE_PATCH, 2)
        self.assertIsNone(controller.dispatch(command))
        job.error = ValueError('offered route changed')
        controller.advance_preparations()
        self.assertEqual(controller.tick(), [(command, p.NOT_READY)])
        self.assertEqual(controller.tick(), [])
        self.assertEqual(calls.count(('start', 0, 2)), 1)
        self.assertIn('offered route changed', controller.patching.issues[0])


if __name__ == '__main__': unittest.main()
