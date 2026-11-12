"""Quarantine logs its first cause while issued finishes remain observable."""

import unittest

import test_audio_backend as native
from LightpadController import protocol as p


class AudioFaultLoggingTests(unittest.TestCase):
    def make(self):
        self.helper = native.AudioBackendTests()
        self.fixture, self.backend, self.session = self.helper.make()
        self.events = self.helper.events

    def issued_finish(self):
        lease, clip = self.helper.record()
        self.fixture.song.current_song_time = 5
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        self.assertTrue(self.backend._attempts[lease].finish_issued)
        self.fixture.song.current_song_time = 12
        self.helper.slot(lease).finish_native(8)  # Requested8, actual native anchor12.
        return lease, clip

    def test_repeated_finish_observation_fault_emits_only_first_group_fault(self):
        self.make()
        lease, clip = self.issued_finish()
        for _ in range(1091):
            self.backend.tick()
        faults = [event for event in self.events if event["event"] == "fault"]
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["group"], 0)
        self.assertIn("playback anchor", faults[0]["message"])
        self.assertEqual(self.backend.faults[0]["message"], faults[0]["message"])
        self.assertIs(self.helper.slot(lease).clip, clip)
        self.assertTrue(self.helper.slot(lease).track.arm)
        self.assertEqual(self.session.groups[0].accepted, [])

    def test_later_valid_finish_still_finalizes_after_initial_fault_without_replacing_its_cause(self):
        self.make()
        lease, clip = self.issued_finish()
        self.backend.tick()
        original = dict(self.backend.faults[0])
        clip.loop_start = 1  # A different later error must not replace the first.
        self.backend.tick()
        self.assertEqual(self.backend.faults[0], original)
        self.assertEqual(len([event for event in self.events if event["event"] == "fault"]), 1)
        clip.start_time, clip.loop_start, clip.loop_end = 8, 0, 4
        self.backend.tick()
        self.assertEqual(self.backend._attempts[lease].state, "accepted")
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [4])
        self.assertFalse(self.helper.slot(lease).track.arm)
        self.assertIs(self.helper.slot(lease).clip, clip)
        self.assertEqual(self.backend.faults[0], original)
        self.assertEqual(len([event for event in self.events if event["event"] == "finish_observed"]), 1)
        self.assertFalse(self.backend.ready(0), "quarantine remains authoritative after preserving the finish")

    def test_each_group_retains_and_logs_its_own_first_error_including_cause(self):
        self.make()
        try:
            try:
                raise ValueError("initial native failure")
            except ValueError as cause:
                raise RuntimeError("first observation") from cause
        except RuntimeError as error:
            self.backend._fault(0, "observe", error)
        self.backend._fault(0, "start", RuntimeError("later error"))
        self.backend._fault(1, "finish", ValueError("other group"))
        self.backend._fault(1, "observe", RuntimeError("other later error"))
        faults = [event for event in self.events if event["event"] == "fault"]
        self.assertEqual([event["group"] for event in faults], [0, 1])
        self.assertEqual(self.backend.faults[0]["operation"], "observe")
        self.assertEqual(self.backend.faults[0]["cause"], "ValueError")
        self.assertEqual(self.backend.faults[0]["cause_message"], "initial native failure")
        self.assertEqual(self.backend.faults[1]["message"], "other group")


if __name__ == "__main__":
    unittest.main()
