"""Real fixture/controller read batching must never authorize native writes."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_surface
import test_audio_controller as controller_helpers
import test_surface_touch as touch_helpers
from LightpadController import configuration as config, protocol as p
from LightpadController import surface as module


class SnapshotReadBatchTests(unittest.TestCase):
    def make(self):
        self.native = controller_helpers.AudioControllerTests()
        self.engine = self.native.make(groups=8, enabled=True)
        self.addCleanup(self.native.doCleanups)
        self.fixture, self.song = self.native.fixture, self.native.song
        self.song.metronome = False
        self.now = 100.0
        for item in (patch.multiple(config, AUDIO_CONTROLLER_MANIFEST=None, NATIVE_BAR_CLOCK=False),
                     patch.object(module.time, "monotonic", side_effect=lambda: self.now)):
            item.start()
            self.addCleanup(item.stop)
        self.instance = SimpleNamespace(song=self.song, sent=[], logs=[])
        self.surface = module.LightpadController(self.instance)
        self.surface._engine, self.surface._groups = self.engine, self.engine.groups
        self.addCleanup(self.surface.disconnect)
        self.surface.receive_midi((0xBF, 110, p.VERSION))
        for _ in range(20):
            self.now += .05
            self.surface.update_display()
        return self.surface

    def touch(self, kind, finger=0, seq=1, x=1.5, y=1.5):
        view=self.surface._ui.view
        for message in touch_helpers.frame(finger,seq,kind,x,y,role=6 if view==0 else 0,view=view,group=0):
            self.surface.receive_midi(message)

    def mixer_move_frame(self):
        self.touch(1)
        self.now += .5
        self.surface.update_display()
        self.assertEqual(self.surface._ui.view, 1)
        self.touch(1, finger=1, x=1.5, y=12.5)
        messages = touch_helpers.frame(1,2,2,1.5,11.5,role=0,view=1,group=0)
        for message in messages[:-1]:
            self.surface.receive_midi(message)
        return messages[-1]

    def test_complete_raw_move_shares_full_scope_validation_reads_across_all_eight_groups(self):
        surface = self.make()
        commit = self.mixer_move_frame()
        validations = []
        validate = self.fixture.validate

        def counted(*args, **kwargs):
            validations.append({"native": surface._native_busy,
                                "transport": kwargs.get("require_transport", False)})
            return validate(*args, **kwargs)

        volume = self.fixture.groups[0].group.mixer_device.volume
        before = volume.value
        with patch.object(self.fixture, "validate", side_effect=counted):
            surface.receive_midi(commit)
        self.assertGreater(volume.value, before)
        readonly = [item for item in validations if not item["native"]]
        native = [item for item in validations if item["native"]]
        self.assertLessEqual(len(readonly), 2,
            "one raw Move must share pre/post-dispatch scope reads, observed %d read sweeps and %d native guards" %
            (len(readonly), len(native)))
        self.assertGreaterEqual(len(native), 1, "native parameter writes still need a fresh uncached guard")

    def test_read_batch_shares_scope_but_reads_actual_parameters_and_clip_phase_each_time(self):
        self.make()
        lease, clip, _ = self.native.accept(length=4)
        volume = self.fixture.groups[0].group.mixer_device.volume
        clip.playing_position = .5
        self.engine.begin_read_batch()
        try:
            first = self.engine.snapshot(0)
            volume.value = .9
            clip.playing_position = 2.5
            second = self.engine.snapshot(0)
        finally:
            self.engine.end_read_batch()
        self.assertNotEqual(first[98], second[98])
        self.assertEqual(second[98], round(.9 * 127))
        self.assertEqual((first[93], second[93]), (16, 80))
        self.assertIs(self.native.slot(lease).clip, clip)

    def test_path_route_or_arm_changes_between_read_epochs_invalidate_all_groups(self):
        for changed in ("path", "route", "arm", "implicit"):
            with self.subTest(changed=changed):
                self.make()
                self.engine.begin_read_batch()
                try:
                    self.assertEqual(self.engine.snapshot(0)[91], p.EMPTY)
                    self.assertEqual(self.engine.snapshot(7)[91], p.EMPTY)
                finally:
                    self.engine.end_read_batch()
                self.invalidate_scope(changed)
                self.engine.begin_read_batch()
                try:
                    for group in (0, 7):
                        fields = self.engine.snapshot(group)
                        self.assertEqual((fields[91], fields[103]), (p.UNAVAILABLE, 0))
                finally:
                    self.engine.end_read_batch()

    def test_shared_read_scope_does_not_turn_stopped_transport_into_recording_capability(self):
        self.make()
        self.engine.begin_read_batch()
        try:
            self.assertEqual(self.engine.snapshot(0)[103] & p.CAP_RECORDING, p.CAP_RECORDING)
            self.song.is_playing = False
            for group in (7, 0):
                fields = self.engine.snapshot(group)
                self.assertEqual(fields[103] & p.CAP_RECORDING, 0)
                self.assertEqual(fields[103] & p.CAP_MIXER, p.CAP_MIXER)
        finally:
            self.engine.end_read_batch()

    def invalidate_scope(self, changed):
        if changed == "path":
            self.song.file_path += ".other"
        elif changed == "route":
            self.fixture.groups[0].layers[0].input_routing_type.display_name = "Resampling"
        elif changed == "arm":
            self.native.unrelated.arm = True
        else:
            self.native.unrelated.implicit_arm = True

    def test_scope_changed_after_cached_read_cannot_authorize_a_native_parameter_write(self):
        for changed in ("path", "route", "arm", "implicit"):
            with self.subTest(changed=changed):
                surface = self.make()
                volume = self.fixture.groups[0].group.mixer_device.volume
                before = volume.value
                results = []

                def ui_action():
                    self.assertEqual(surface._ui_snapshot(0)[91], p.EMPTY)
                    self.invalidate_scope(changed)
                    self.assertTrue(surface._ui_send(0, p.SET_VOLUME, 127,
                        lambda outcome: results.append((outcome, surface._ui_snapshot(0)[91]))))

                with patch.object(surface._ui, "tick", side_effect=ui_action):
                    surface.update_display()
                self.assertEqual(results, [(p.NOT_READY, p.UNAVAILABLE)])
                self.assertEqual(volume.value, before)
                self.assertEqual(volume.writes, [])

    def test_native_arm_callback_revalidation_is_uncached_before_record_fire(self):
        for changed in ("path", "route", "arm", "implicit"):
            with self.subTest(changed=changed):
                surface = self.make()
                track = self.fixture.groups[0].layers[0]
                track.after_arm = lambda changed=changed: self.invalidate_scope(changed)
                results = []
                self.native.calls.clear()

                def ui_action():
                    self.assertEqual(surface._ui_snapshot(0)[91], p.EMPTY)
                    self.assertTrue(surface._ui_send(0, p.BEGIN, 0, results.append))

                with patch.object(surface._ui, "tick", side_effect=ui_action):
                    surface.update_display()
                self.assertEqual(results, [p.NOT_READY])
                self.assertIn(("arm", track.name, True), self.native.calls)
                self.assertFalse(any(call[0] in ("fire", "stop", "delete") for call in self.native.calls))
                self.assertIn(0, self.engine.backend.faults)
                self.assertFalse(track.clip_slots[0].has_clip)

    def test_native_parameter_setter_callback_cannot_reuse_scope_for_next_write(self):
        surface = self.make()
        mixer = self.fixture.groups[0].group.mixer_device
        results = []

        def changed_by_native_setter(value):
            self.invalidate_scope("route")
            return value

        mixer.volume.coerce = changed_by_native_setter

        def after_volume(outcome):
            results.append(outcome)
            self.assertTrue(surface._ui_send(0, p.SET_PAN, 127, results.append))

        def ui_action():
            self.assertEqual(surface._ui_snapshot(0)[91], p.EMPTY)
            self.assertTrue(surface._ui_send(0, p.SET_VOLUME, 127, after_volume))

        with patch.object(surface._ui, "tick", side_effect=ui_action):
            surface.update_display()
        self.assertEqual(results, [p.APPLIED, p.NOT_READY])
        self.assertEqual(mixer.volume.value, 1)
        self.assertEqual(mixer.panning.value, 0)
        self.assertEqual(mixer.panning.writes, [])

    def test_callback_exception_closes_read_epoch_and_next_inspection_rechecks_scope(self):
        surface = self.make()

        def failed_ui():
            self.assertEqual(surface._ui_snapshot(0)[91], p.EMPTY)
            raise RuntimeError("injected UI observer failure")

        with patch.object(surface._ui, "tick", side_effect=failed_ui):
            with self.assertRaises(RuntimeError):
                surface.update_display()
        self.invalidate_scope("path")
        self.assertEqual(self.engine.snapshot(0)[91], p.UNAVAILABLE)

    def test_nested_surface_callback_invalidates_ancestor_snapshot_cache(self):
        for changed in ("path", "route"):
            with self.subTest(changed=changed):
                surface = self.make()
                nested = False
                observations = []

                def ui_observer():
                    nonlocal nested
                    if nested:
                        observations.append(("nested", surface._ui_snapshot(0)[91]))
                        return
                    observations.append(("before", surface._ui_snapshot(0)[91]))
                    self.invalidate_scope(changed)
                    nested = True
                    surface.update_display()
                    observations.append(("after", surface._ui_snapshot(0)[91]))

                with patch.object(surface._ui, "tick", side_effect=ui_observer):
                    surface.update_display()
                self.assertEqual(observations, [("before", p.EMPTY), ("nested", p.UNAVAILABLE),
                                                ("after", p.UNAVAILABLE)])
                self.assertEqual(surface._last[0][103], 0)

    def test_scope_epoch_changed_during_capability_projection_cannot_return_mixed_valid_snapshot(self):
        self.make()
        capabilities = self.engine.backend.read_capabilities
        projections = []

        def changed_epoch(group):
            projections.append(group)
            if len(projections) == 1:
                self.engine.begin_read_batch()
                try:
                    self.invalidate_scope("path")
                finally:
                    self.engine.end_read_batch()
            return capabilities(group)

        self.engine.begin_read_batch()
        try:
            with patch.object(self.engine.backend, "read_capabilities", side_effect=changed_epoch):
                fields = self.engine.snapshot(0)
        finally:
            self.engine.end_read_batch()
        self.assertTrue(projections)
        self.assertLessEqual(len(projections), 2)
        self.assertEqual((fields[91], fields[103], fields[106], fields[107]),
                         (p.UNAVAILABLE, 0, 0, 0))

    def projection(self, name):
        if name == "meter_state":
            return self.engine.controls, "meter", lambda: self.engine.meter_state()
        if name == "global_clock":
            return self.fixture, "global_clock", lambda: self.engine.global_clock()
        self.native.accept(length=4)
        return self.engine.backend, "reference_clock", lambda: self.engine.group_clock(0)

    def test_meter_and_clocks_resample_scope_after_mid_projection_epoch_change(self):
        for name in ("meter_state", "global_clock", "group_clock"):
            with self.subTest(projection=name):
                self.make()
                owner, method, read = self.projection(name)
                original = getattr(owner, method)
                projections = []

                def changed_epoch(*args, **kwargs):
                    value = original(*args, **kwargs)
                    self.assertIsNotNone(value)
                    projections.append(value)
                    if len(projections) == 1:
                        self.engine.begin_read_batch()
                        try:
                            self.invalidate_scope("path")
                        finally:
                            self.engine.end_read_batch()
                    return value

                self.engine.begin_read_batch()
                try:
                    with patch.object(owner, method, side_effect=changed_epoch):
                        result = read()
                finally:
                    self.engine.end_read_batch()
                self.assertIsNone(result, "a pre-change projection must not survive an invalidated scope")
                self.assertTrue(projections)
                self.assertLessEqual(len(projections), 2)

    def test_meter_and_clocks_stop_after_two_continually_reentered_projections(self):
        for name in ("meter_state", "global_clock", "group_clock"):
            with self.subTest(projection=name):
                self.make()
                owner, method, read = self.projection(name)
                original = getattr(owner, method)
                projections = []

                def reentered(*args, **kwargs):
                    value = original(*args, **kwargs)
                    projections.append(value)
                    self.engine.begin_read_batch()
                    self.engine.end_read_batch()
                    return value

                self.engine.begin_read_batch()
                try:
                    with patch.object(owner, method, side_effect=reentered):
                        result = read()
                finally:
                    self.engine.end_read_batch()
                self.assertIsNone(result)
                self.assertEqual(len(projections), 2, "unstable epochs must have a bounded retry cost")

    def test_snapshot_stops_after_two_continually_reentered_capability_projections(self):
        self.make()
        capabilities = self.engine.backend.read_capabilities
        projections = []

        def reentered(group):
            value = capabilities(group)
            projections.append(group)
            self.engine.begin_read_batch()
            self.engine.end_read_batch()
            return value

        self.engine.begin_read_batch()
        try:
            with patch.object(self.engine.backend, "read_capabilities", side_effect=reentered):
                fields = self.engine.snapshot(0)
        finally:
            self.engine.end_read_batch()
        self.assertEqual(len(projections), 2)
        self.assertEqual((fields[91], fields[103], fields[106], fields[107]),
                         (p.UNAVAILABLE, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
