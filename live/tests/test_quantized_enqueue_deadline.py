"""A frozen beat requires safe deferral, then exact native target alignment.

The independent native clock below is a deterministic fault model, not a Live
clock implementation. An unissued finish may move to a following safe bar, with
the retained Session/backend plan updated before fire. Wrong native anchors
must still be quarantined and native fire remains quantized.
"""

import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_audio_backend as native
from LightpadController import protocol as p
from LightpadController import audio_backend as module


class EnqueueClock:
    def __init__(self, beat):
        self.beat, self.seconds = beat, 100.0
        self.guard_costs, self.quantization_costs = [], []

    def monotonic(self):
        return self.seconds

    def advance_ms(self, milliseconds):
        self.seconds += milliseconds / 1000.0
        self.beat += milliseconds * 120.0 / 60000.0

    def spend(self, costs):
        if costs:
            self.advance_ms(costs.pop(0))


class LaunchWindow:
    def __init__(self, target, quantum, clock):
        self.previous = target - 4
        self._quantum, self._clock = quantum, clock

    @property
    def quantization(self):
        self._clock.spend(self._clock.quantization_costs)
        return self._quantum


class QuantizedEnqueueDeadlineTests(unittest.TestCase):
    TARGET = 832.0

    def make(self):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()
        self.fixture.song.tempo = 120.0
        self.reference, self.reference_clip = self.helper.accept_reference(length=4)
        self.lease, self.clip = self.helper.record(queued_at=823)
        self.assertEqual(self.session.take.token, 2)
        self.assertEqual(self.clip.start_time, 824)
        self.clock = EnqueueClock(831)
        self.native_fires = []
        self.fixture.native_bar_clock = True
        self.fixture.launch_window = lambda target: LaunchWindow(target, self.fixture.q_bar, self.clock)
        validate = self.fixture.validate

        def guarded(*args, **kwargs):
            result = validate(*args, **kwargs)
            self.clock.spend(self.clock.guard_costs)
            return result

        self.fixture.validate = guarded
        for binding in self.fixture.groups.values():
            for track in binding.layers:
                for slot in track.clip_slots:
                    fire = slot.fire

                    def quantized_fire(*, launch_quantization, slot=slot, fire=fire):
                        self.assertIs(launch_quantization, self.fixture.q_bar)
                        boundary = (math.floor(self.clock.beat / 4) + 1) * 4
                        attempt = next(item for item in self.backend._attempts.values() if item.slot is slot)
                        kind = "finish" if slot.has_clip and slot.clip.is_recording else "start"
                        planned = attempt.finish_target if kind == "finish" else attempt.start_target
                        self.native_fires.append({"track": slot.track.name, "row": slot.row,
                            "reported_beat": self.fixture.song.current_song_time,
                            "native_beat_at_fire": self.clock.beat,
                            "native_queued_boundary": boundary, "monotonic": self.clock.seconds,
                            "kind": kind, "planned_target_at_fire": planned})
                        result = fire(launch_quantization=launch_quantization)
                        # The physical trace has a later chained call. It is
                        # still a separate native enqueue, with its own guard.
                        if slot is self.helper.slot(self.lease):
                            self.clock.advance_ms(14)
                        return result

                    slot.fire = quantized_fire
        clock_patch = patch.object(module, "time", SimpleNamespace(monotonic=self.clock.monotonic), create=True)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def reported(self, beat):
        self.fixture.song.current_song_time = beat
        self.clock.seconds += (beat - self.clock.beat) * .5
        self.clock.beat = beat

    def assert_native_fires_match_retained_plan(self, expected_finish=TARGET):
        self.assertTrue(all(item["native_queued_boundary"] == item["planned_target_at_fire"]
                            for item in self.native_fires),
            "native enqueue must match the actual retained plan at fire; observed %r" % self.native_fires)
        attempt = self.backend._attempts[self.lease]
        if attempt.finish_target is not None:
            self.assertEqual(attempt.finish_target, expected_finish)
            self.assertEqual(self.session.take.schedule.start_at, expected_finish)
        self.assertEqual(self.reference_clip.loop_end, 4)
        self.assertEqual(self.session.groups[0].accepted[0].slot, self.reference)

    def test_late_accept_at_frozen_831_992_retains_safe_836_plan_before_native_fire(self):
        self.make()
        self.reported(831.992)
        self.clock.guard_costs = [14]
        outcome = self.session.accept(0, next_take=True)
        self.assertEqual(outcome, p.OUTCOME_QUEUED)
        self.assertEqual(self.session.take.next_take.schedule.start_at, 836)
        self.backend.tick()
        self.assertEqual(self.native_fires, [])
        self.assert_native_fires_match_retained_plan(expected_finish=836)
        self.reported(833)
        self.backend.tick()
        self.assertEqual([item["native_queued_boundary"] for item in self.native_fires], [836, 836])
        self.assert_native_fires_match_retained_plan(expected_finish=836)

    def test_safe_plan_is_rechecked_when_later_tick_arrives_near_frozen_boundary(self):
        self.make()
        self.reported(831)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.assertEqual(self.backend._attempts[self.lease].finish_target, self.TARGET)
        self.reported(831.992)
        self.clock.guard_costs = [14]
        self.backend.tick()
        self.assertEqual(self.native_fires, [])
        self.assertEqual(self.session.take.next_take.schedule.start_at, 836)
        self.assert_native_fires_match_retained_plan(expected_finish=836)
        self.reported(833)
        self.backend.tick()
        self.assertEqual([item["native_queued_boundary"] for item in self.native_fires], [836, 836])
        self.assert_native_fires_match_retained_plan(expected_finish=836)

    def test_elapsed_guard_or_quantization_work_cannot_hide_behind_frozen_song_time(self):
        for slow_part in ("validation", "quantization"):
            with self.subTest(slow_part=slow_part):
                self.make()
                self.reported(831)
                self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
                self.reported(831.75)  # 125ms reported lead at120BPM.
                costs = self.clock.guard_costs if slow_part == "validation" else self.clock.quantization_costs
                costs.append(140)  # Native time crosses832 while the reported beat stays831.75.
                self.backend.tick()
                self.assertEqual(self.native_fires, [])
                self.assert_native_fires_match_retained_plan(expected_finish=836)
                self.reported(833)
                self.backend.tick()
                self.assertEqual([item["native_queued_boundary"] for item in self.native_fires], [836])
                self.assert_native_fires_match_retained_plan(expected_finish=836)

    def test_safe_native_quantized_finish_and_chained_start_keep_exact_target(self):
        self.make()
        self.reported(831)
        self.assertEqual(self.session.accept(0, next_take=True), p.OUTCOME_QUEUED)
        self.clock.guard_costs = [14]
        self.backend.tick()
        self.assertEqual(len(self.native_fires), 2)
        self.assert_native_fires_match_retained_plan()
        self.assertEqual([item["native_queued_boundary"] for item in self.native_fires], [832, 832])
        self.assertAlmostEqual(self.native_fires[1]["monotonic"] - self.native_fires[0]["monotonic"], .014)

    def test_observed_late_playback_anchor_is_not_accepted_or_widened(self):
        self.make()
        self.reported(831)
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        self.assert_native_fires_match_retained_plan()
        self.reported(836)
        self.helper.slot(self.lease).finish_native(12)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(len(self.session.groups[0].accepted), 1)
        self.assertTrue(self.helper.slot(self.lease).track.arm)
        self.assertEqual(self.backend._attempts[self.lease].finish_target, self.TARGET)
        self.assertIs(self.helper.slot(self.lease).clip, self.clip)
        self.assertFalse(any(call[0] in ("stop", "delete") for call in self.fixture.calls))


if __name__ == "__main__":
    unittest.main()
