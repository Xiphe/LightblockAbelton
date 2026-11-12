"""Staged cancellation checks. Fake transitions supply no native Live proof."""

import unittest

import test_proof_driver as fixture
from LightpadController.proof_driver import ProofRefused


class ProofCancelTests(unittest.TestCase):
    # Reuse the prepared fake fixture without inheriting/rerunning its tests.
    setUp = fixture.ProofDriverTests.setUp
    a = fixture.ProofDriverTests.a
    enable = fixture.ProofDriverTests.enable
    start = fixture.ProofDriverTests.start
    record = fixture.ProofDriverTests.record
    refuse_without_writes = fixture.ProofDriverTests.refuse_without_writes

    def install_calls(self, track):
        def stop(quantized, /):
            self.mutations.append(("stop", track.name, quantized))
        track.stop_all_clips = stop
        for slot in track.clip_slots:
            def delete(slot=slot):
                self.mutations.append(("delete", track.name, slot.index))
                slot.clip = None
            slot.delete_clip = delete

    def quiet(self, track):
        track.playing_slot_index = track.fired_slot_index = -1
        slot = track.clip_slots[0]
        slot.is_triggered = False
        if slot.has_clip:
            slot.clip.is_recording = slot.clip.is_playing = False

    def stop(self):
        return self.driver.dispatch({"op": "stop_attempt"})

    def discard(self):
        return self.driver.dispatch({"op": "discard_attempt"})

    def test_queued_empty_stop_waits_for_observation_then_explicit_discard_without_delete(self):
        self.install_calls(self.a)
        self.start()
        self.assertEqual(self.stop()["active"]["state"], "stop_requested")
        self.assertTrue(self.a.arm)
        self.assertFalse(self.a.clip_slots[0].has_clip)
        self.driver.poll()
        self.assertEqual(self.driver.active["state"], "stop_requested")
        self.refuse_without_writes({"op": "discard_attempt"})
        self.quiet(self.a)
        self.refuse_without_writes({"op": "discard_attempt"})
        self.driver.poll()
        self.assertEqual(self.driver.active["state"], "stopped_observed")
        self.assertTrue(self.a.arm)
        self.assertFalse(any(call[0] == "delete" for call in self.mutations))
        self.assertIsNone(self.discard()["active"])
        self.assertFalse(self.a.arm)
        self.assertEqual([call[0] for call in self.mutations], ["arm", "fire", "stop", "arm"])
        self.assertEqual(self.mutations[2], ("stop", "Proof A", False))

    def test_recording_stop_does_not_delete_or_disarm_until_explicit_discard(self):
        self.install_calls(self.a)
        clip = self.record()
        self.stop()
        self.assertIs(self.a.clip_slots[0].clip, clip)
        self.assertTrue(self.a.arm)
        self.driver.poll()
        self.assertEqual(self.driver.active["state"], "stop_requested")
        self.quiet(self.a)
        self.driver.poll()
        before = list(self.mutations)
        self.driver.poll()
        self.assertEqual(self.mutations, before)
        self.assertIs(self.a.clip_slots[0].clip, clip)
        self.discard()
        self.assertFalse(self.a.clip_slots[0].has_clip)
        self.assertFalse(self.a.arm)
        self.assertEqual([call[0] for call in self.mutations[-3:]], ["stop", "delete", "arm"])

    def test_accepted_a_and_group_mute_survive_stopping_and_discarding_b(self):
        accepted = self.record()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.tracks[4].mute = True
        b = self.tracks[6]
        self.install_calls(b)
        self.driver.dispatch({"op": "start", "layer": "B"})
        b.clip_slots[0].observe_recording()
        self.driver.poll()
        before = len(self.mutations)
        self.stop()
        self.quiet(b)
        self.driver.poll()
        self.discard()
        self.assertIs(self.driver.accepted["A"], accepted)
        self.assertIs(self.a.clip_slots[0].clip, accepted)
        self.assertTrue(accepted.is_playing)
        self.assertTrue(self.tracks[4].mute)
        self.assertTrue(all(call[1] == "Proof B" for call in self.mutations[before:]))

    def test_completed_accepted_and_finish_queued_takes_cannot_be_stopped(self):
        self.install_calls(self.a)
        clip = self.record()
        self.driver.dispatch({"op": "finish"})
        self.refuse_without_writes({"op": "stop_attempt"})
        self.refuse_without_writes({"op": "discard_attempt"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.refuse_without_writes({"op": "stop_attempt"})
        self.assertIs(self.driver.accepted["A"], clip)
        self.assertFalse(any(call[0] == "stop" for call in self.mutations))

    def test_an_accepted_layer_is_refused_even_if_an_active_record_is_present(self):
        self.install_calls(self.a)
        clip = self.record()
        self.driver.accepted["A"] = clip
        self.refuse_without_writes({"op": "stop_attempt"})

    def test_clip_replacement_is_never_stopped_or_adopted(self):
        self.install_calls(self.a)
        original = self.record()
        self.a.clip_slots[0].clip = fixture.Clip(recording=True)
        self.refuse_without_writes({"op": "stop_attempt"})
        self.assertIs(self.driver.active["clip"], original)

    def test_track_and_slot_replacements_prevent_stop(self):
        self.install_calls(self.a)
        self.record()
        original_track = self.a
        self.tracks[5] = fixture.Track("Proof A", "AudioTrack", self.mutations, self.tracks[4])
        self.refuse_without_writes({"op": "stop_attempt"})
        self.tracks[5] = original_track
        old_slot = self.a.clip_slots[0]
        replacement = fixture.Slot(self.a, 0)
        replacement.clip = old_slot.clip
        self.a.clip_slots[0] = replacement
        self.refuse_without_writes({"op": "stop_attempt"})

    def test_unobserved_clip_in_queued_attempt_and_other_row_activity_are_refused(self):
        self.install_calls(self.a)
        self.start()
        self.a.clip_slots[0].observe_recording()
        self.refuse_without_writes({"op": "stop_attempt"})
        self.driver.poll()
        self.a.fired_slot_index = 3
        self.refuse_without_writes({"op": "stop_attempt"})

    def test_cached_recording_rechecks_native_recording(self):
        self.install_calls(self.a)
        clip = self.record()
        clip.is_recording = False
        self.refuse_without_writes({"op": "stop_attempt"})

    def test_native_stop_signature_failure_faults_after_one_positional_false_without_fallback(self):
        self.record()
        calls = []
        def bad_stop(*args, **kwargs):
            calls.append((args, kwargs))
            raise TypeError("native stop signature rejected")
        self.a.stop_all_clips = bad_stop
        with self.assertRaises(TypeError):
            self.stop()
        self.assertEqual(calls, [((False,), {})])
        self.assertIsNotNone(self.driver.fault)
        self.assertTrue(self.a.arm)
        self.assertTrue(self.driver.inspect()["active"]["native_clip"]["is_recording"])
        self.refuse_without_writes({"op": "stop_attempt"})
        self.refuse_without_writes({"op": "discard_attempt"})
        self.assertEqual(len(calls), 1)

    def test_ghost_trigger_blocks_discard_and_poll_never_cleans_it_up(self):
        self.install_calls(self.a)
        self.record()
        self.stop()
        self.quiet(self.a)
        self.driver.poll()
        self.a.clip_slots[0].is_triggered = True
        self.refuse_without_writes({"op": "discard_attempt"})
        before = list(self.mutations)
        self.driver.poll()
        self.assertIsNotNone(self.driver.fault)
        self.assertEqual(self.mutations, before)
        self.assertTrue(self.a.arm)

    def test_deleted_or_replaced_attempt_after_stop_blocks_discard(self):
        self.install_calls(self.a)
        self.record()
        self.stop()
        self.quiet(self.a)
        self.driver.poll()
        original = self.a.clip_slots[0].clip
        self.a.clip_slots[0].clip = fixture.Clip()
        self.refuse_without_writes({"op": "discard_attempt"})
        self.a.clip_slots[0].clip = None
        self.refuse_without_writes({"op": "discard_attempt"})
        self.assertIs(self.driver.active["clip"], original)

    def test_native_delete_failure_never_disarms_or_retries(self):
        self.install_calls(self.a)
        self.record()
        self.stop()
        self.quiet(self.a)
        self.driver.poll()
        calls = []
        def bad_delete():
            calls.append("delete")
            raise RuntimeError("native delete failed")
        self.a.clip_slots[0].delete_clip = bad_delete
        with self.assertRaises(RuntimeError):
            self.discard()
        self.assertEqual(calls, ["delete"])
        self.assertTrue(self.a.arm)
        self.assertIsNotNone(self.driver.fault)
        self.refuse_without_writes({"op": "discard_attempt"})
        self.assertEqual(calls, ["delete"])

    def test_native_delete_returning_without_an_empty_slot_faults_before_disarm(self):
        self.install_calls(self.a)
        self.record()
        self.stop()
        self.quiet(self.a)
        self.driver.poll()
        calls = []
        self.a.clip_slots[0].delete_clip = lambda: calls.append("delete")
        with self.assertRaises(ProofRefused):
            self.discard()
        self.assertEqual(calls, ["delete"])
        self.assertTrue(self.a.arm)
        self.assertIsNotNone(self.driver.fault)
        self.assertTrue(self.driver.inspect()["active"]["native_slot"]["has_clip"])

    def test_global_guards_are_required_for_stop_and_discard(self):
        self.install_calls(self.a)
        self.record()
        self.song.record_mode = True
        self.refuse_without_writes({"op": "stop_attempt"})
        self.song.record_mode = False
        self.stop()
        self.quiet(self.a)
        self.driver.poll()
        self.song.record_mode = True
        self.refuse_without_writes({"op": "discard_attempt"})

    def test_native_queued_stop_replay_accepts_final_minus_two_without_deleting(self):
        # Native 12.2.7: canceled empty B has playing=-2, fired=-1, no
        # trigger/clip, and stays armed until the explicit discard stage.
        self.install_calls(self.a)
        self.start()
        self.stop()
        self.quiet(self.a)
        self.a.playing_slot_index = -2
        self.driver.poll()
        self.assertIsNone(self.driver.fault)
        self.assertEqual(self.driver.active["state"], "stopped_observed")
        self.assertTrue(self.a.arm)
        self.assertIsNone(self.discard()["active"])
        self.assertFalse(self.a.arm)
        self.assertEqual(self.a.playing_slot_index, -2)
        self.assertFalse(any(call[0] == "delete" for call in self.mutations))

    def test_native_recording_stop_minus_two_preserves_accepted_a_and_mute(self):
        accepted = self.record()
        self.driver.dispatch({"op": "finish"})
        self.a.clip_slots[0].observe_finished()
        self.driver.poll()
        self.tracks[4].mute = True
        b = self.tracks[6]
        self.install_calls(b)
        self.driver.dispatch({"op": "start", "layer": "B"})
        b.clip_slots[0].observe_recording()
        self.driver.poll()
        before = len(self.mutations)
        self.stop()
        self.quiet(b)
        b.playing_slot_index = -2
        self.driver.poll()
        self.assertIsNone(self.driver.fault)
        self.assertEqual(self.driver.active["state"], "stopped_observed")
        self.discard()
        self.assertIs(self.driver.accepted["A"], accepted)
        self.assertIs(self.a.clip_slots[0].clip, accepted)
        self.assertTrue(accepted.is_playing)
        self.assertTrue(self.tracks[4].mute)
        self.assertFalse(b.clip_slots[0].has_clip)
        self.assertFalse(b.arm)
        self.assertTrue(all(call[1] == "Proof B" for call in self.mutations[before:]))

    def test_pending_fired_minus_two_is_not_quiet_or_discardable(self):
        self.install_calls(self.a)
        self.record()
        self.stop()
        self.quiet(self.a)
        self.a.playing_slot_index = -2
        self.a.fired_slot_index = -2
        self.assertFalse(self.driver._quiet_attempt(self.driver.active))
        self.refuse_without_writes({"op": "discard_attempt"})
        before = list(self.mutations)
        self.driver.poll()
        self.assertIsNotNone(self.driver.fault)
        self.assertNotEqual(self.driver.active["state"], "stopped_observed")
        self.assertEqual(self.mutations, before)
        self.assertTrue(self.a.arm)

    def test_unknown_negative_or_foreign_playing_index_remains_refused(self):
        self.install_calls(self.a)
        self.record()
        for value in (-3, 3):
            with self.subTest(playing_slot_index=value):
                self.a.playing_slot_index = value
                self.refuse_without_writes({"op": "stop_attempt"})


if __name__ == "__main__":
    unittest.main()
