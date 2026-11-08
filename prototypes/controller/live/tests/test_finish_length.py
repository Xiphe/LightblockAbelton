"""Owned finish-length guards using the real Session/backend and native fakes.

The supplied playback observations model a two-quarter-note recording whose
native loop was padded to a 7/8 bar. They do not establish native API accuracy.
"""

import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_audio_backend as native
from LightpadController import protocol as p


class ObservedClip(native.Clip):
    def __init__(self, start, calls, track_name):
        self._capture = False
        self._calls, self._track_name = calls, track_name
        self.loop_end_writes = []
        self.write_behavior = "accept"
        self.after_write = None
        super().__init__(start)
        self._capture = True
        self.file_path = "/scratch/native-take.wav"
        self.start_marker, self.end_marker = 0.0, 4.0
        self.sample_rate, self.sample_length = 44100.0, 88200
        self.warp_markers = [{"sample_time": 0.0, "beat_time": 0.0},
                             {"sample_time": .015625, "beat_time": .03125}]

    @property
    def loop_end(self):
        return self._native_loop_end

    @loop_end.setter
    def loop_end(self, value):
        if self._capture:
            self.loop_end_writes.append(value)
            self._calls.append(("loop_end", self._track_name, value))
            if self.write_behavior == "raise":
                raise RuntimeError("native loop bound setter refused")
            if self.write_behavior == "ignore":
                return
        self._native_loop_end = value
        if self._capture and self.after_write is not None:
            self.after_write()


class FinishLengthTests(unittest.TestCase):
    def make(self, exact=False, probe=True):
        helper = native.AudioBackendTests()
        features = ("record", "cancel", "wrap", "accept_next", "advance")
        if exact:
            features += ("exact_length",)
        helper.make(probe=probe, features=features)
        self.helper, self.fixture, self.backend, self.session = helper, helper.fixture, helper.backend, helper.session
        for binding in self.fixture.groups.values():
            for track in binding.layers:
                for slot in track.clip_slots:
                    def begin(beat, slot=slot, track=track):
                        clip = ObservedClip(beat, self.fixture.calls, track.name)
                        slot.clip, slot.is_triggered = clip, False
                        track.fired_slot_index, track.playing_slot_index = -1, slot.row
                        return clip
                    slot.begin_native = begin
        return helper

    def finishing_two_quarters(self, existing_reference=False):
        reference = None
        if existing_reference:
            reference = self.helper.accept_reference(length=4)
        lease, clip = self.helper.record(queued_at=9)
        self.assertEqual(clip.start_time, 12)
        # The fixture port supplies a changed native bar grid. The production
        # fixed-grid fixture is not being changed or treated as meter evidence.
        self.fixture.bar_length = 3.5
        self.fixture.song.current_song_time = 13
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        attempt = self.backend._attempts[lease]
        self.assertEqual((attempt.start_target, attempt.finish_target), (12, 14))
        self.assertTrue(attempt.finish_issued)
        return lease, clip, reference

    def native_playback(self, lease, clip, length=3.5, anchor=14, sample_beats=2):
        slot = self.helper.slot(lease)
        # These are native observations, not writes performed by the backend.
        clip._native_loop_end = length
        # Physical sample coverage follows the supplied recorded warp mapping,
        # independently of the native loop's rounded display/playback bound.
        clip.sample_length = round(sample_beats * .5 * clip.sample_rate)
        clip.start_time = anchor
        clip.is_recording, clip.is_playing = False, True
        slot.is_triggered = False
        slot.track.fired_slot_index, slot.track.playing_slot_index = -1, lease.row
        self.fixture.song.current_song_time = anchor
        self.fixture.calls.clear()
        clip.loop_end_writes.clear()

    def assert_preserved_unaccepted(self, lease, clip, accepted_count=0, require_fault=True):
        if require_fault:
            self.assertIn(0, self.backend.faults)
        self.assertNotEqual(self.backend._attempts[lease].state, "accepted")
        self.assertEqual(len(self.session.groups[0].accepted), accepted_count)
        self.assertIs(self.helper.slot(lease).clip, clip)
        self.assertTrue(self.helper.slot(lease).track.arm)
        self.assertTrue(clip.is_playing)
        self.assertFalse(clip.is_recording)
        self.assertFalse(any(call[0] in ("stop", "delete") for call in self.fixture.calls))
        self.assertFalse(any(call[0] == "arm" and call[2] is False for call in self.fixture.calls))

    def test_padded_native_finish_is_quarantined_by_default_even_in_probe_mode(self):
        self.make(exact=False, probe=True)
        lease, clip, _ = self.finishing_two_quarters()
        self.native_playback(lease, clip)
        self.backend.tick()
        self.assert_preserved_unaccepted(lease, clip)
        self.assertEqual(clip.loop_end, 3.5)
        self.assertEqual(clip.loop_end_writes, [])

    def test_explicit_exact_length_feature_trims_only_owned_loop_end_before_accept_and_disarm(self):
        self.make(exact=True, probe=False)
        lease, clip, _ = self.finishing_two_quarters()
        self.native_playback(lease, clip)
        untouched = (clip.loop_start, clip.start_marker, clip.end_marker, clip.start_time,
                     clip.file_path, self.fixture.song.current_song_time, self.fixture.song.is_playing)
        self.backend.tick()
        self.assertEqual(clip.loop_end_writes, [2])
        self.assertEqual(clip.loop_end, 2)
        self.assertEqual(self.fixture.calls, [("loop_end", self.helper.slot(lease).track.name, 2),
            ("arm", self.helper.slot(lease).track.name, False)])
        self.assertEqual(self.session.groups[0].accepted[0].length, 2)
        self.assertEqual(self.backend._attempts[lease].state, "accepted")
        self.assertNotIn(0, self.backend.faults)
        self.assertEqual((clip.loop_start, clip.start_marker, clip.end_marker, clip.start_time,
            clip.file_path, self.fixture.song.current_song_time, self.fixture.song.is_playing), untouched)
        self.backend.tick()
        self.assertEqual(clip.loop_end_writes, [2], "accepted clip bounds must never be written again")

    def test_matching_native_duration_accepts_without_feature_or_any_bound_write(self):
        self.make(exact=False)
        lease, clip, _ = self.finishing_two_quarters()
        self.native_playback(lease, clip, length=2)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].accepted[0].length, 2)
        self.assertEqual(clip.loop_end_writes, [])
        self.assertFalse(self.helper.slot(lease).track.arm)
        self.assertNotIn(0, self.backend.faults)

    def test_existing_accepted_reference_keeps_its_identity_bounds_and_length(self):
        self.make(exact=True)
        lease, clip, reference = self.finishing_two_quarters(existing_reference=True)
        ref_lease, ref_clip = reference
        preserved = (ref_clip.loop_start, ref_clip.loop_end, tuple(ref_clip.loop_end_writes),
                     ref_clip.file_path, ref_clip.start_time)
        self.native_playback(lease, clip)
        self.backend.tick()
        self.assertIs(self.helper.slot(ref_lease).clip, ref_clip)
        self.assertEqual((ref_clip.loop_start, ref_clip.loop_end, tuple(ref_clip.loop_end_writes),
                         ref_clip.file_path, ref_clip.start_time), preserved)
        self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [4, 2])
        self.assertEqual(self.session.groups[0].reference.slot, ref_lease)

    def test_short_native_sample_is_never_extended_or_trimmed_to_unsupported_duration(self):
        for native_length in (1.5, 3.5):
            with self.subTest(native_length=native_length):
                self.make(exact=True)
                lease, clip, _ = self.finishing_two_quarters()
                self.native_playback(lease, clip, length=native_length, sample_beats=1.5)
                self.backend.tick()
                self.assert_preserved_unaccepted(lease, clip)
                self.assertEqual(clip.loop_end, native_length)
                self.assertEqual(clip.loop_end_writes, [])

    def test_rounded_down_loop_extends_only_when_physical_sample_covers_full_target(self):
        for marker_style in ("dict", "attrs"):
            with self.subTest(marker_style=marker_style):
                self.make(exact=True)
                lease, clip, reference = self.finishing_two_quarters(existing_reference=True)
                ref_lease, ref_clip = reference
                preserved = (ref_clip.loop_end, ref_clip.start_time, tuple(ref_clip.loop_end_writes))
                self.native_playback(lease, clip, length=1.5, sample_beats=2)
                if marker_style == "attrs":
                    clip.warp_markers = [SimpleNamespace(**marker) for marker in clip.warp_markers]
                self.backend.tick()
                self.assertEqual(clip.loop_end_writes, [2])
                self.assertEqual(clip.loop_end, 2)
                self.assertEqual([layer.length for layer in self.session.groups[0].accepted], [4, 2])
                self.assertFalse(self.helper.slot(lease).track.arm)
                self.assertIs(self.helper.slot(ref_lease).clip, ref_clip)
                self.assertEqual((ref_clip.loop_end, ref_clip.start_time, tuple(ref_clip.loop_end_writes)), preserved)
                self.assertNotIn(0, self.backend.faults)

    def test_native_five_and_half_quarter_recording_with_five_beat_loop_uses_actual_sample_coverage(self):
        self.make(exact=True, probe=False)
        lease, clip = self.helper.record(queued_at=149)
        self.assertEqual(clip.start_time, 152)
        self.fixture.bar_length = 3.5
        self.fixture.song.current_song_time = 157
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        self.assertEqual(self.backend._attempts[lease].finish_target, 157.5)
        self.native_playback(lease, clip, length=5, anchor=157.5, sample_beats=5.5)
        self.assertEqual((clip.sample_length, clip.sample_rate), (121275, 44100.0))
        self.backend.tick()
        self.assertEqual(clip.loop_end_writes, [5.5])
        self.assertEqual((clip.loop_start, clip.loop_end, clip.start_time), (0, 5.5, 157.5))
        self.assertEqual(self.session.groups[0].accepted[0].length, 5.5)
        self.assertFalse(self.helper.slot(lease).track.arm)
        self.assertNotIn(0, self.backend.faults)

    def test_unavailable_or_invalid_native_sample_metadata_blocks_both_normalization_directions(self):
        invalid = (("sample_length", None), ("sample_length", True), ("sample_rate", 0),
                   ("warp_markers", None), ("warp_markers", [{"sample_time": 0, "beat_time": 0}]),
                   ("warp_markers", [{"sample_time": 0, "beat_time": 0},
                                     {"sample_time": float("nan"), "beat_time": .03125}]),
                   ("warp_markers", [SimpleNamespace(sample_time=0, beat_time=0),
                                     SimpleNamespace(beat_time=.03125)]))
        for native_length in (1.5, 3.5):
            for field, value in invalid:
                with self.subTest(native_length=native_length, field=field, value=value):
                    self.make(exact=True)
                    lease, clip, _ = self.finishing_two_quarters()
                    self.native_playback(lease, clip, length=native_length)
                    if field == "sample_length" and value is None:
                        del clip.sample_length
                    else:
                        setattr(clip, field, value)
                    self.backend.tick()
                    self.assert_preserved_unaccepted(lease, clip)
                    self.assertEqual(clip.loop_end_writes, [])
                    self.assertEqual(clip.loop_end, native_length)

    def test_physical_sample_coverage_is_rechecked_inside_normalization_write_after_logger_callback(self):
        for native_length in (1.5, 3.5):
            for changed in ("frames", "rate", "markers"):
                with self.subTest(native_length=native_length, changed=changed):
                    self.make(exact=True)
                    lease, clip, _ = self.finishing_two_quarters()
                    self.native_playback(lease, clip, length=native_length)
                    emit = self.backend.emit
                    before_write_observed = []

                    def invalidate_coverage(event):
                        emit(event)
                        if event["event"] == "before_normalize_finish_length":
                            before_write_observed.append(True)
                            if changed == "frames":
                                clip.sample_length = 33075
                            elif changed == "rate":
                                clip.sample_rate = 96000.0
                            else:
                                clip.warp_markers[1]["sample_time"] = .03125

                    self.backend.emit = invalidate_coverage
                    self.backend.tick()
                    self.assertEqual(before_write_observed, [True])
                    self.assert_preserved_unaccepted(lease, clip)
                    self.assertEqual(clip.loop_end_writes, [])
                    self.assertEqual(clip.loop_end, native_length)

    def test_setter_refusal_or_ignored_readback_never_accepts_disarms_or_retries_the_trim(self):
        for behavior in ("raise", "ignore"):
            with self.subTest(behavior=behavior):
                self.make(exact=True)
                lease, clip, _ = self.finishing_two_quarters()
                self.native_playback(lease, clip)
                clip.write_behavior = behavior
                self.backend.tick()
                self.assert_preserved_unaccepted(lease, clip, require_fault=behavior == "raise")
                self.assertEqual(clip.loop_end, 3.5)
                self.assertEqual(clip.loop_end_writes, [2])
                self.backend.tick()
                self.assert_preserved_unaccepted(lease, clip)
                self.assertEqual(clip.loop_end_writes, [2])

    def test_playback_anchor_is_validated_before_any_normalization_write(self):
        self.make(exact=True)
        lease, clip, _ = self.finishing_two_quarters()
        self.native_playback(lease, clip, anchor=14.25)
        self.backend.tick()
        self.assert_preserved_unaccepted(lease, clip)
        self.assertEqual(clip.loop_end_writes, [])
        self.assertEqual(clip.loop_end, 3.5)

    def test_replaced_native_clip_is_not_trimmed_or_disarmed(self):
        self.make(exact=True)
        lease, original, _ = self.finishing_two_quarters()
        self.native_playback(lease, original)
        replacement = ObservedClip(14, self.fixture.calls, "foreign")
        replacement.is_recording = False
        self.helper.slot(lease).clip = replacement
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertTrue(self.helper.slot(lease).track.arm)
        self.assertEqual(original.loop_end_writes, [])
        self.assertEqual(replacement.loop_end_writes, [])
        self.assertIs(self.helper.slot(lease).clip, replacement)

    def test_normalization_rechecks_native_identity_and_anchor_after_before_write_callback(self):
        for changed in ("identity", "anchor"):
            with self.subTest(changed=changed):
                self.make(exact=True)
                lease, original, _ = self.finishing_two_quarters()
                self.native_playback(lease, original)
                replacement = ObservedClip(14, self.fixture.calls, "foreign")
                replacement.is_recording = False
                emit = self.backend.emit

                def changed_during_callback(event):
                    emit(event)
                    if event["event"] == "before_normalize_finish_length":
                        if changed == "identity":
                            self.helper.slot(lease).clip = replacement
                        else:
                            original.start_time = 14.25

                self.backend.emit = changed_during_callback
                self.backend.tick()
                self.assertIn(0, self.backend.faults)
                self.assertEqual(self.session.groups[0].accepted, [])
                self.assertTrue(self.helper.slot(lease).track.arm)
                self.assertEqual(original.loop_end_writes, [],
                    "ownership and native playback anchor must still hold immediately before the bound write")
                self.assertEqual(replacement.loop_end_writes, [])

    def test_finish_revalidates_identity_anchor_and_exact_bounds_before_disarm_and_after_its_readback(self):
        for phase in ("before_disarm", "after_disarm"):
            for changed in ("identity", "anchor", "bounds", "audio", "warping", "looping"):
                with self.subTest(phase=phase, changed=changed):
                    self.make(exact=True)
                    lease, original, _ = self.finishing_two_quarters()
                    self.native_playback(lease, original)
                    slot = self.helper.slot(lease)
                    replacement = ObservedClip(14, self.fixture.calls, "foreign")
                    replacement.is_recording = False

                    def invalidate():
                        if changed == "identity":
                            slot.clip = replacement
                        elif changed == "anchor":
                            original.start_time = 14.25
                        elif changed == "bounds":
                            original._native_loop_end = 1.5
                        else:
                            setattr(original, 'is_audio_clip' if changed == 'audio' else changed, False)

                    if phase == "before_disarm":
                        emit = self.backend.emit

                        def before_disarm(event):
                            emit(event)
                            if event["event"] == "before_disarm_finish":
                                invalidate()

                        self.backend.emit = before_disarm
                        self.backend.tick()
                        self.assertTrue(slot.track.arm,
                            "changed ownership, anchor or bounds must block the disarm write")
                    else:
                        native_arm = type(slot.track).arm

                        def after_disarm(track, value):
                            native_arm.fset(track, value)
                            if track is slot.track and value is False:
                                invalidate()

                        with patch.object(type(slot.track), "arm", property(native_arm.fget, after_disarm)):
                            self.backend.tick()
                        self.assertFalse(slot.track.arm)
                    self.assertIn(0, self.backend.faults)
                    self.assertEqual(self.session.groups[0].accepted, [])
                    self.assertNotEqual(self.backend._attempts[lease].state, "accepted")
                    self.assertEqual(replacement.loop_end_writes, [])


if __name__ == "__main__":
    unittest.main()
