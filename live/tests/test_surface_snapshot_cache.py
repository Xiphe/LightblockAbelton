"""Callback-local read reuse must preserve actual mutation/result freshness."""

from collections import Counter
import unittest
from unittest.mock import patch

import test_native_clock_listener as listener_helpers
import test_surface_pacing as pacing_helpers
from LightpadController import protocol as p, timing


class CountingEngine(pacing_helpers.EightClockEngine):
    def __init__(self):
        self.reads = Counter()
        self.meter_reads = 0
        self.volume = [64] * 8
        self.click = False
        self.commands = []
        self.dispatch_effect = self.tick_effect = None
        self.deferred = False
        self.completions = []
        self.phase = 1.0

    def snapshot(self, group, sequence=0, outcome=p.NONE):
        self.reads[group] += 1
        fields = super().snapshot(group, sequence, outcome)
        fields[98] = self.volume[group]
        return fields

    def meter_state(self):
        self.meter_reads += 1
        return 7, 8, self.click

    def dispatch(self, command):
        self.commands.append(command)
        if self.dispatch_effect is not None:
            self.dispatch_effect(command)
        return None if self.deferred else p.APPLIED

    def tick(self):
        if self.tick_effect is not None:
            self.tick_effect()
        result, self.completions = self.completions, []
        return result

    def needs_tick(self):
        return bool(self.completions) or self.tick_effect is not None

    def global_clock(self):
        return timing.Clock(timing.ONE_BAR, 0, 4.0, self.phase, 0.0, 4.0, 120.0, 4, 4, True)


class SurfaceSnapshotCacheTests(unittest.TestCase):
    def make(self):
        helper = listener_helpers.NativeClockListenerTests()
        surface = helper.make()
        self.addCleanup(helper.doCleanups)
        self.helper, self.surface, self.instance = helper, surface, helper.instance
        self.engine = CountingEngine()
        surface._engine = self.engine
        surface.receive_midi((0xBF, 110, p.VERSION))
        self.engine.reads.clear()
        self.engine.meter_reads = 0
        self.instance.sent.clear()
        return surface

    def test_display_reuses_one_actual_snapshot_per_group_across_ui_and_publish_and_one_meter_read(self):
        surface = self.make()
        tick = surface._ui.tick

        def repeated_ui_reads():
            tick()
            surface._ui_snapshot(0)
            surface._ui_snapshot(0)
            surface._ui_meter()
            surface._ui_meter()

        with patch.object(surface._ui, "tick", side_effect=repeated_ui_reads), \
                patch.object(surface._output, "flush"):
            surface.update_display()
        self.assertEqual(dict(self.engine.reads), dict.fromkeys(range(12), 1))
        self.assertEqual(self.engine.meter_reads, 1)

    def test_actual_values_changed_between_public_callbacks_never_reuse_old_snapshot(self):
        surface = self.make()
        with patch.object(surface._output, "flush"):
            surface.update_display()
            self.assertEqual(surface._last[0][98], 64)
            self.engine.volume[0] = 101
            surface.refresh_state()
            self.assertEqual(surface._last[0][98], 101)
            self.engine.volume[0] = 17
            surface.update_display()
            self.assertEqual(surface._last[0][98], 17)
        self.assertIsNone(getattr(surface, "_ui_cache", None))
        self.engine.volume[0] = 88
        self.assertEqual(surface._ui_snapshot(0)[98], 88)
        self.engine.volume[0] = 89
        self.assertEqual(surface._ui_snapshot(0)[98], 89,
                         "reads outside a public callback must remain uncached")

    def test_sync_internal_result_callback_sees_post_dispatch_snapshot_and_meter(self):
        surface = self.make()
        results = []

        def native_change(command):
            # A native listener/log observer may read inside dispatch before
            # the setter applies. This value must not survive its completion.
            surface._ui_snapshot(0)
            surface._ui_meter()
            self.engine.volume[0], self.engine.click = 100, True

        self.engine.dispatch_effect = native_change

        def gesture():
            surface._ui_snapshot(0)
            surface._ui_meter()
            self.assertTrue(surface._ui_send(0, p.SET_VOLUME, 100,
                lambda result: results.append((result, surface._ui_snapshot(0)[98], surface._ui_meter()))))

        with patch.object(surface._ui, "tick", side_effect=gesture), \
                patch.object(surface._output, "flush"):
            surface.update_display()
        self.assertEqual(results, [(p.APPLIED, 100, (7, 8, True))])
        self.assertEqual(surface._last[0][98], 100)
        self.assertEqual(surface._outcomes[0], (0, p.NONE))
        self.assertTrue(self.engine.commands[0].seq < 0)
        self.assertEqual(surface._ui_snapshot(0)[104], 0)

    def test_native_completion_invalidates_reads_taken_inside_tick_before_result_callback(self):
        surface = self.make()
        self.engine.deferred = True
        results = []
        self.assertTrue(surface._ui_send(0, p.ABORT, 0,
            lambda result: results.append((result, surface._ui_snapshot(0)[98], surface._ui_meter()))))
        command = self.engine.commands[-1]
        self.engine.completions = [(command, p.APPLIED)]

        def native_change():
            surface._ui_snapshot(0)
            surface._ui_meter()
            self.engine.volume[0], self.engine.click = 23, True

        self.engine.tick_effect = native_change
        with patch.object(surface._output, "flush") as flush:
            surface.receive_midi((0xBF, 19, 0))
            flush.assert_not_called()
        self.assertEqual(results, [], "the fast tick must defer result delivery to an ordinary callback")
        surface.update_display()
        self.assertEqual(results, [(p.APPLIED, 23, (7, 8, True))])
        self.assertIsNone(getattr(surface, "_ui_cache", None))

    def test_wire_sequence_overrides_do_not_modify_cached_actual_fields_or_send_internal_ids(self):
        surface = self.make()
        observed = []

        def compare_after_publish():
            first = surface._ui_snapshot(0)
            surface._outcomes[0] = (37, p.BUSY)
            surface._publish(force=True, only=0)
            observed.append((first[104], first[105], surface._ui_snapshot(0)[104], surface._ui_snapshot(0)[105]))

        with patch.object(surface._ui, "tick", side_effect=compare_after_publish):
            surface.update_display()
        self.assertEqual(observed, [(0, p.NONE, 0, p.NONE)])
        self.assertEqual((surface._last[0][104], surface._last[0][105]), (37, p.BUSY))
        self.assertTrue(all(0 <= value <= 127 for _, _, value in self.instance.sent))


if __name__ == "__main__":
    unittest.main()
