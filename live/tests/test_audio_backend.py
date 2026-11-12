"""Native-effect ordering with fake observations, never Live timing evidence."""

import json
import hashlib
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import protocol as p
from LightpadController.audio_backend import LiveAudioBackend
from LightpadController.audio_fixture import AudioFixture
from LightpadController.session import Session, BackendFailure, Schedule, QUEUED_VIEW, RECORDING_VIEW


class Clip:
    def __init__(self, start, length=4, recording=True):
        self.start_time, self.loop_start, self.loop_end = start, 0, length
        self.is_recording, self.is_playing, self.warping = recording, True, True
        self.is_audio_clip, self.looping, self.playing_position = True, True, 0.0


class Slot:
    def __init__(self, track, row):
        self.track, self.row = track, row
        self.clip, self.is_triggered = None, False
        self.fire_error = None
        self.delete_error = None

    @property
    def has_clip(self):
        return self.clip is not None

    def fire(self, **kwargs):
        self.track.calls.append(("fire", self.track.name, self.row, kwargs))
        if self.fire_error:
            raise self.fire_error
        self.track.fired_slot_index = self.row
        self.is_triggered = True

    def delete_clip(self):
        self.track.calls.append(("delete", self.track.name, self.row))
        if self.delete_error:
            raise self.delete_error
        self.clip = None

    def begin_native(self, beat):
        self.clip = Clip(beat)
        self.is_triggered = False
        self.track.fired_slot_index, self.track.playing_slot_index = -1, self.row
        return self.clip

    def finish_native(self, length):
        self.clip.start_time += length
        self.clip.loop_end = length
        self.clip.is_recording = False
        self.is_triggered = False
        self.track.fired_slot_index = -1


class Track:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls
        self._arm, self.implicit_arm, self.mute = False, False, True
        self.fired_slot_index = self.playing_slot_index = -1
        self.clip_slots = [Slot(self, row) for row in range(8)]
        self.stop_error, self.after_arm = None, None

    @property
    def arm(self):
        return self._arm

    @arm.setter
    def arm(self, value):
        self.calls.append(("arm", self.name, value))
        self._arm = value
        if value and self.after_arm:
            self.after_arm()

    def stop_all_clips(self, quantized, /):
        self.calls.append(("stop", self.name, quantized))
        if self.stop_error:
            raise self.stop_error
        # A native stop request can remain at -2 until a later observation.
        self.fired_slot_index = self.playing_slot_index = -2

    def quiet_native(self):
        self.fired_slot_index = self.playing_slot_index = -1
        for slot in self.clip_slots:
            slot.is_triggered = False
            if slot.has_clip:
                slot.clip.is_recording = slot.clip.is_playing = False


class Fixture:
    """The backend's read-only fixture port, with explicit fake native state."""
    def __init__(self, layers=3, groups=2):
        self.calls = []
        self.q_bar = object()
        self.groups = {g: SimpleNamespace(group=SimpleNamespace(mute=True),
            layers=tuple(Track("G%d Layer %d" % (g, layer), self.calls)
                         for layer in range(layers))) for g in range(groups)}
        self.unrelated = Track("unrelated", self.calls)
        self.song = SimpleNamespace(current_song_time=1.0, tempo=120.0, is_playing=True, record_mode=False,
                                    session_record=False, overdub=False)
        self.bar_length, self.bar_origin = 4, 0
        self.valid = True

    def validate(self, allowed_armed=(), require_transport=False):
        if not self.valid or self.song.record_mode or (require_transport and not self.song.is_playing):
            raise RuntimeError("fixture guard refused")
        tracks = [t for binding in self.groups.values() for t in binding.layers] + [self.unrelated]
        if any(t.implicit_arm or (t.arm and t not in allowed_armed) for t in tracks):
            raise RuntimeError("unrelated arm")

    def next_bar(self, at=None, inclusive=False):
        at = self.song.current_song_time if at is None else at
        value = (at - self.bar_origin) / self.bar_length
        index = math.ceil(value) if inclusive else math.floor(value) + 1
        return self.bar_origin + index * self.bar_length

    def next_wrap(self, clip, at=None, inclusive=False):
        if clip is None or not clip.is_playing:
            raise RuntimeError("reference unavailable")
        at = self.song.current_song_time if at is None else at
        length = clip.loop_end - clip.loop_start
        value = (at - clip.start_time) / length
        index = math.ceil(value) if inclusive else math.floor(value) + 1
        return clip.start_time + index * length

    def reference_clock(self, clip, take_id=0):
        return {"reference_start": clip.start_time, "take_id": take_id}


class AudioBackendTests(unittest.TestCase):
    def make(self, layers=3, groups=2, probe=True, features=()):
        fixture = Fixture(layers, groups)
        events = []
        backend = LiveAudioBackend(fixture, events.append, probe=probe, validated_features=features)
        session = Session(backend)
        backend.bind_session(session)
        backend.enable()
        self.fixture, self.backend, self.session, self.events = fixture, backend, session, events
        return fixture, backend, session

    def slot(self, lease):
        return self.fixture.groups[lease.group].layers[lease.layer].clip_slots[lease.row]

    def record(self, group=0, queued_at=None):
        if queued_at is not None:
            self.fixture.song.current_song_time = queued_at
        self.assertEqual(self.session.begin(group), p.OUTCOME_QUEUED)
        lease, target = self.session.take.slot, self.session.take.schedule.start_at
        self.fixture.song.current_song_time = target - 1
        self.backend.tick()
        self.fixture.song.current_song_time = target
        clip = self.slot(lease).begin_native(target)
        self.backend.tick()
        self.assertEqual(self.session.view, RECORDING_VIEW)
        return lease, clip

    def accept_reference(self, length=12):
        lease, clip = self.record()
        end = clip.start_time + length
        self.fixture.song.current_song_time = end - 1
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        self.fixture.song.current_song_time = end
        self.slot(lease).finish_native(length)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].reference.slot, lease)
        return lease, clip

    def test_normal_capabilities_require_named_native_evidence_and_planning_never_writes(self):
        self.make(probe=False)
        self.assertEqual(self.backend.capabilities(0), 0)
        self.assertEqual(self.session.begin(0), p.NOT_READY)
        self.assertEqual(self.fixture.calls, [])
        self.make(probe=False, features=("record",))
        self.assertEqual(self.backend.capabilities(0), 0)
        self.make(probe=False, features=("record", "cancel", "wrap", "accept_next", "advance"))
        self.assertEqual(self.backend.capabilities(0), 15)
        self.assertEqual(self.session.begin(0), p.OUTCOME_QUEUED)
        self.assertEqual(self.fixture.calls, [])
        self.backend.tick()
        self.assertEqual([call[0] for call in self.fixture.calls], ["arm", "fire"])
        self.assertEqual(self.fixture.calls[-1][3], {"launch_quantization": self.fixture.q_bar})

    def test_created_clip_is_observed_only_after_issued_start_and_generation_is_current(self):
        self.make()
        self.session.begin(0)
        lease, token = self.session.take.slot, self.session.take.token
        self.assertIsNone(self.backend.clip_for(lease))
        self.backend.tick()
        self.assertEqual(self.session.view, QUEUED_VIEW)
        actual = self.slot(lease).begin_native(4)
        self.fixture.song.current_song_time = 4
        self.backend.tick()
        self.assertIs(self.backend.clip_for(lease), actual)
        self.assertEqual(self.session.take.token, token)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_async_minus_two_stop_then_delete_without_retrying_signature(self):
        self.make()
        lease, clip = self.record()
        track = self.fixture.groups[0].layers[lease.layer]
        before = list(self.fixture.calls)
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.assertEqual(self.fixture.calls, before)
        self.assertTrue(self.backend.pending_cancel(0))
        self.backend.tick()
        self.assertEqual(track.playing_slot_index, -2)
        self.assertIs(self.slot(lease).clip, clip)
        self.backend.tick()
        self.assertEqual(sum(call[0] == "stop" for call in self.fixture.calls), 1)
        self.assertFalse(any(call[0] == "delete" for call in self.fixture.calls))
        track.quiet_native()
        self.backend.tick()
        self.assertFalse(self.slot(lease).has_clip)
        self.assertFalse(track.arm)
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertEqual([call[0] for call in self.fixture.calls[-3:]], ["stop", "delete", "arm"])
        self.assertEqual(self.fixture.calls[-3][2], False)

    def test_full_pool_retry_reserves_future_generation_on_pending_cancel_slot(self):
        self.make(layers=1)
        old, old_clip = self.record()
        self.fixture.song.current_song_time = 5
        before = list(self.fixture.calls)
        self.assertEqual(self.session.retry(0), p.OUTCOME_QUEUED)
        future, token = self.session.take.slot, self.session.take.token
        self.assertNotEqual(old, future)
        self.assertEqual((old.row, old.layer), (future.row, future.layer))
        self.assertEqual(self.fixture.calls, before)
        self.backend.tick()
        self.assertIs(self.slot(old).clip, old_clip)
        self.assertIsNone(self.backend.clip_for(future))
        self.fixture.groups[0].layers[0].quiet_native()
        self.fixture.song.current_song_time = 6
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertFalse(self.slot(future).has_clip)
        self.assertIsNone(self.backend.clip_for(future))
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.fixture.song.current_song_time = 8
        new_clip = self.slot(future).begin_native(8)
        self.backend.tick()
        self.assertIsNot(new_clip, old_clip)
        self.assertIs(self.backend.clip_for(future), new_clip)
        self.assertEqual(self.session.take.token, token)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_aborting_reserved_future_keeps_ancestral_native_cleanup_pending(self):
        self.make(layers=1)
        old, _ = self.record()
        self.fixture.song.current_song_time = 5
        self.session.retry(0)
        self.session.abort(0)
        self.assertTrue(self.backend.pending_cancel(0))
        self.backend.tick()
        self.fixture.groups[0].layers[0].quiet_native()
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertEqual(sum(call[0] == "fire" for call in self.fixture.calls), 1)
        self.assertFalse(self.slot(old).has_clip)

    def test_playing_minus_two_is_stopped_but_fired_minus_two_remains_pending(self):
        self.make(layers=1)
        lease, clip = self.record()
        self.fixture.song.current_song_time = 5
        self.session.retry(0)
        future = self.session.take.slot
        self.backend.tick()
        track = self.slot(lease).track
        track.playing_slot_index = -2
        track.fired_slot_index = -2
        self.slot(lease).is_triggered = False
        clip.is_recording = clip.is_playing = False
        self.backend.tick()
        self.assertTrue(self.backend.pending_cancel(0))
        self.assertTrue(self.slot(lease).has_clip)
        track.fired_slot_index = -1
        self.fixture.song.current_song_time = 6
        self.backend.tick()
        self.assertFalse(self.backend.pending_cancel(0))
        self.assertFalse(self.slot(future).has_clip)
        self.assertFalse(self.backend.faults)
        self.assertTrue(track.arm)
        self.assertTrue(self.slot(future).is_triggered)

    def test_public_scope_guard_allows_only_retained_backend_arms(self):
        self.make()
        lease, _ = self.record()
        track = self.slot(lease).track
        self.assertEqual(self.backend.owned_armed_tracks(), (track,))
        self.assertTrue(self.backend.validate_scope())
        self.fixture.unrelated._arm = True
        with self.assertRaises(BackendFailure):
            self.backend.validate_scope()

    def test_next_bar_finish_then_later_wrap_keeps_old_layer_and_waits(self):
        self.make()
        reference, ref_clip = self.accept_reference(length=12)
        old, _ = self.record(queued_at=18)
        self.fixture.song.current_song_time = 30
        before = list(self.fixture.calls)
        self.assertEqual(self.session.accept(0, True), p.OUTCOME_QUEUED)
        following = self.session.take.next_take.slot
        self.assertEqual(self.session.take.schedule, Schedule(30, 32))
        self.assertEqual(self.session.take.next_take.schedule, Schedule(32, 40))
        self.assertNotEqual(old.layer, following.layer)
        self.assertEqual(self.fixture.calls, before)
        self.backend.tick()
        self.assertFalse(self.fixture.groups[0].layers[following.layer].arm)
        self.fixture.song.current_song_time = 32
        self.slot(old).finish_native(4)
        self.backend.tick()
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertEqual(len(self.session.groups[0].accepted), 2)
        self.assertTrue(ref_clip.is_playing)
        self.fixture.song.current_song_time = 35
        self.backend.tick()
        self.assertFalse(self.fixture.groups[0].layers[following.layer].arm)
        self.fixture.song.current_song_time = 37
        self.backend.tick()
        self.assertTrue(self.fixture.groups[0].layers[following.layer].arm)
        self.fixture.song.current_song_time = 40
        self.slot(following).begin_native(40)
        self.backend.tick()
        self.assertEqual(self.session.view, RECORDING_VIEW)
        self.assertEqual(self.session.groups[0].reference.slot, reference)

    def test_coincident_wrap_queues_finish_before_next_start_without_extra_cycle(self):
        self.make()
        self.accept_reference(length=8)
        old, _ = self.record(queued_at=13)
        self.fixture.song.current_song_time = 26
        self.session.accept(0, True)
        following = self.session.take.next_take.slot
        self.assertEqual(self.session.take.next_take.schedule, Schedule(28, 28))
        before = len(self.fixture.calls)
        self.backend.tick()
        fires = [call for call in self.fixture.calls[before:] if call[0] == "fire"]
        self.assertEqual([call[1] for call in fires], [self.slot(old).track.name, self.slot(following).track.name])
        self.fixture.song.current_song_time = 28
        self.slot(old).finish_native(8)
        self.slot(following).begin_native(28)
        self.backend.tick()
        self.assertEqual(self.session.view, RECORDING_VIEW)
        self.assertEqual(len(self.session.groups[0].accepted), 2)

    def test_first_reference_handoff_starts_at_its_finish_boundary(self):
        self.make()
        old, _ = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0, True)
        following = self.session.take.next_take.slot
        self.assertEqual(self.session.take.next_take.schedule, Schedule(8, 8))
        self.backend.tick()
        self.fixture.song.current_song_time = 8
        self.slot(old).finish_native(4)
        self.slot(following).begin_native(8)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].reference.slot, old)
        self.assertEqual(self.session.take.slot, following)
        self.assertEqual(self.session.view, RECORDING_VIEW)

    def test_unissued_missed_start_retains_generation_at_next_safe_bar(self):
        self.make()
        self.session.begin(0)
        take = self.session.take
        self.fixture.native_bar_clock = True
        self.fixture.launch_window = lambda target: SimpleNamespace(
            previous=target - 4, quantization=self.fixture.q_bar)
        self.fixture.song.current_song_time = 4
        self.backend.tick()
        self.assertFalse(self.backend.faults)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertIs(self.session.take, take)
        self.assertEqual(take.schedule, Schedule(1, 8))
        self.assertEqual(self.backend._attempts[take.slot].start_target, 8)
        self.backend.tick()
        self.assertEqual(self.fixture.calls, [])
        self.fixture.song.current_song_time = 4.1
        self.backend.tick()
        self.assertEqual([call[0] for call in self.fixture.calls], ["arm", "fire"])

    def test_next_start_failure_does_not_lose_already_requested_current_finish(self):
        self.make()
        old, old_clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0, True)
        following = self.session.take.next_take.slot
        self.slot(following).fire_error = TypeError("native start failed")
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.fixture.song.current_song_time = 8
        self.slot(old).finish_native(4)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].reference.slot, old)
        self.assertIs(self.backend.clip_for(old), old_clip)
        self.assertTrue(old_clip.is_playing)
        self.assertFalse(self.slot(old).track.arm)
        self.assertEqual(self.session.take.slot, following)
        self.assertEqual(self.session.view, QUEUED_VIEW)

    def test_next_slot_is_prevalidated_before_current_finish_native_write(self):
        self.make()
        old, clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0, True)
        following = self.session.take.next_take.slot
        self.slot(following).clip = Clip(0, recording=False)
        before = list(self.fixture.calls)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.fixture.calls, before)
        self.assertTrue(clip.is_recording)

    def test_native_stop_and_delete_errors_quarantine_without_fallback_or_retry(self):
        for operation in ("stop", "delete"):
            with self.subTest(operation=operation):
                self.make()
                lease, _ = self.record()
                self.session.abort(0)
                track, slot = self.slot(lease).track, self.slot(lease)
                if operation == "stop":
                    track.stop_error = TypeError("stop rejected")
                else:
                    slot.delete_error = RuntimeError("delete rejected")
                self.backend.tick()
                if operation == "delete":
                    track.quiet_native()
                    self.backend.tick()
                before = list(self.fixture.calls)
                self.assertIn(0, self.backend.faults)
                self.backend.tick()
                self.assertEqual(self.fixture.calls, before)
                self.assertTrue(slot.has_clip)
                self.assertTrue(track.arm)
                self.assertTrue(self.backend.pending_cancel(0))

    def test_just_finished_accept_intent_cannot_be_discarded(self):
        self.make()
        lease, clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0)
        self.backend.tick()
        self.slot(lease).finish_native(4)
        before = list(self.fixture.calls)
        self.assertEqual(self.session.abort(0), p.BUSY)
        self.assertEqual(self.fixture.calls, before)
        self.backend.tick()
        self.assertEqual(self.session.groups[0].reference.slot, lease)
        self.assertTrue(clip.is_playing)

    def test_finish_between_cancel_plan_and_execution_is_preserved_and_quarantined(self):
        self.make()
        lease, clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0)
        self.backend.tick()
        self.session.abort(0)
        self.slot(lease).finish_native(4)
        before = list(self.fixture.calls)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.fixture.calls, before)
        self.assertIs(self.slot(lease).clip, clip)

    def test_advance_stops_only_selected_group_waits_for_quiet_and_preserves_clips(self):
        self.make(layers=2)
        old, clip = self.accept_reference(length=4)
        occupied = self.fixture.groups[0].layers[1].clip_slots[1]
        occupied.clip = Clip(0, recording=False)
        occupied.clip.is_playing = False
        other = self.fixture.groups[1].layers[0]
        other.clip_slots[0].begin_native(0)
        other_clip = other.clip_slots[0].clip
        before = list(self.fixture.calls)
        self.assertEqual(self.session.advance(0), p.APPLIED)
        self.assertEqual(self.session.groups[0].row, 2)
        self.assertTrue(self.backend.pending_advance(0))
        self.assertEqual(self.fixture.calls, before)
        self.backend.tick()
        self.assertTrue(self.backend.pending_advance(0))
        stops = [call for call in self.fixture.calls[len(before):] if call[0] == "stop"]
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(call[1].startswith("G0") for call in stops))
        for track in self.fixture.groups[0].layers:
            track.quiet_native()
        self.backend.tick()
        self.assertFalse(self.backend.pending_advance(0))
        self.assertIs(self.slot(old).clip, clip)
        self.assertIsNotNone(occupied.clip)
        self.assertIs(other.clip_slots[0].clip, other_clip)
        self.assertTrue(other_clip.is_playing)
        self.assertTrue(self.fixture.groups[0].group.mute)

    def test_fixture_guard_after_arm_prevents_fire_and_never_disarms_unrelated_track(self):
        self.make()
        self.fixture.groups[0].layers[0].after_arm = lambda: setattr(self.fixture.unrelated, "_arm", True)
        self.session.begin(0)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertTrue(self.fixture.unrelated.arm)
        self.assertFalse(any(call[0] == "fire" for call in self.fixture.calls))

    def test_diagnostics_and_events_are_json_safe_and_reference_clock_is_actual(self):
        self.make()
        _, clip = self.accept_reference(length=4)
        self.assertEqual(self.backend.reference_clock(0, take_id=77),
                         {"reference_start": clip.start_time, "take_id": 77})
        finished = next(event for event in self.events if event["event"] == "finish_observed")
        self.assertEqual(finished["target"], 8)
        self.assertEqual(finished["native"]["start_time"], 8)
        self.assertEqual(finished["start_target"], 4)
        self.assertFalse(finished["native"]["recording"])
        json.dumps(self.backend.diagnostic_state(), allow_nan=False)
        json.dumps(self.events, allow_nan=False)

    def test_real_read_only_fixture_port_binds_without_default_tracks_or_index_assumptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scratch.als"
            path.write_bytes(b"offline fixture only")
            calls = []
            group = SimpleNamespace(name="Lightpad 1", group_track=None, is_foldable=True,
                                    is_frozen=False, can_be_armed=False, mute=True)
            tracks = [Track("Layer 1", calls), Track("Layer 2", calls)]
            for track in tracks:
                track.group_track = group
                track.is_foldable, track.is_frozen = False, False
                track.has_audio_input, track.has_midi_input, track.can_be_armed = True, False, True
                track.current_monitoring_state = 2
                track.input_routing_type = SimpleNamespace(display_name="Ext. In")
                track.input_routing_channel = SimpleNamespace(display_name="1 T-Bone (1)")
                track.output_routing_type = SimpleNamespace(display_name="Lightpad 1")
            song = SimpleNamespace(file_path=str(path), tracks=[group] + tracks,
                record_mode=False, session_record=False, overdub=False, loop=False,
                is_counting_in=False, count_in_duration=0, clip_trigger_quantization=4,
                is_ableton_link_enabled=False, tempo_follower_enabled=False, external_sync=False,
                signature_numerator=4, signature_denominator=4, tempo=120.0,
                current_song_time=1.0, is_playing=True)
            manifest = {"set_path": str(path), "set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "groups": [{"index": 0, "name": "Lightpad 1", "layer_names": ["Layer 1", "Layer 2"]}],
                "bar_grid": {"origin": 0.0, "numerator": 4, "denominator": 4, "fixed": True},
                "capture_input_channel": "1", "sync_ui_confirmed_off": []}
            fixture = AudioFixture(song, manifest, 4, 2)
            backend = LiveAudioBackend(fixture, probe=True)
            session = Session(backend)
            backend.bind_session(session)
            backend.enable()
            self.assertEqual(session.begin(0), p.OUTCOME_QUEUED)
            self.assertEqual(calls, [])
            backend.tick()
            song.current_song_time = 4
            tracks[0].clip_slots[0].begin_native(4)
            backend.tick()
            song.current_song_time = 7
            self.assertEqual(session.accept(0), p.OUTCOME_QUEUED)
            backend.tick()
            song.current_song_time = 8
            tracks[0].clip_slots[0].finish_native(4)
            backend.tick()
            self.assertFalse(backend.faults)
            self.assertEqual(backend.reference_clock(0).span, 4)
            self.assertEqual(session.groups[0].reference.length, 4)
            self.assertTrue(group.mute)

    def test_replaced_owned_clip_is_never_stopped_or_deleted(self):
        self.make()
        lease, old = self.record()
        self.slot(lease).clip = Clip(4)
        before = list(self.fixture.calls)
        self.assertEqual(self.session.abort(0), p.STALE)
        self.assertEqual(self.fixture.calls, before)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.fixture.calls, before)

    def test_cancel_refuses_another_row_before_planning_or_native_stop(self):
        self.make()
        lease, _ = self.record()
        track = self.slot(lease).track
        track.playing_slot_index = 3
        before = list(self.fixture.calls)
        self.assertEqual(self.session.abort(0), p.STALE)
        self.assertEqual(self.fixture.calls, before)
        track.playing_slot_index = 0
        self.assertEqual(self.session.abort(0), p.APPLIED)
        track.fired_slot_index = 3
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.fixture.calls, before)

    def test_invalid_finished_length_is_quarantined_without_fabricated_acceptance(self):
        self.make()
        lease, clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0)
        self.backend.tick()
        self.slot(lease).finish_native(float("nan"))
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertTrue(clip.is_playing)

    def test_nonlooping_nonaudio_and_nonzero_loop_start_are_preserved_without_acceptance(self):
        for field, value in (("looping", False), ("is_audio_clip", False), ("loop_start", 1)):
            with self.subTest(field=field):
                self.make()
                lease, clip = self.record()
                self.fixture.song.current_song_time = 6
                self.session.accept(0)
                self.backend.tick()
                self.slot(lease).finish_native(4)
                setattr(clip, field, value)
                before = list(self.fixture.calls)
                self.backend.tick()
                self.assertIn(0, self.backend.faults)
                self.assertEqual(self.session.groups[0].accepted, [])
                self.assertEqual(self.fixture.calls, before)
                self.assertIs(self.slot(lease).clip, clip)
                self.assertTrue(clip.is_playing)

    def test_queued_slot_foreign_nonrecording_clip_is_never_adopted_stopped_or_deleted(self):
        self.make()
        self.session.begin(0)
        lease = self.session.take.slot
        self.backend.tick()
        foreign = Clip(0, recording=False)
        foreign.is_playing = False
        self.slot(lease).clip = foreign
        before = list(self.fixture.calls)
        self.assertEqual(self.session.abort(0), p.STALE)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.fixture.calls, before)
        self.assertIs(self.slot(lease).clip, foreign)
        self.assertIsNone(self.backend.clip_for(lease))

    def test_start_crossing_boundary_inside_native_call_is_rejected_by_actual_anchor(self):
        self.make()
        self.session.begin(0)
        lease = self.session.take.slot
        slot = self.slot(lease)
        native = slot.fire
        def delayed_fire(**kwargs):
            self.fixture.song.current_song_time = 4
            return native(**kwargs)
        slot.fire = delayed_fire
        self.backend.tick()
        self.fixture.song.current_song_time = 8
        actual = slot.begin_native(8)  # Missed requested4; Live queued its next bar.
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.session.view, QUEUED_VIEW)
        self.assertIsNone(self.backend.clip_for(lease))
        self.assertIs(slot.clip, actual)
        self.assertFalse(any(call[0] in ("stop", "delete") for call in self.fixture.calls))

    def test_native_playback_anchor_must_match_requested_finish_before_accepting(self):
        self.make()
        lease, clip = self.record()
        self.fixture.song.current_song_time = 6
        self.session.accept(0)
        self.backend.tick()
        self.fixture.song.current_song_time = 12
        self.slot(lease).finish_native(8)  # Playback begins12, requested finish8.
        before = list(self.fixture.calls)
        self.backend.tick()
        self.assertIn(0, self.backend.faults)
        self.assertEqual(self.session.groups[0].accepted, [])
        self.assertEqual(self.fixture.calls, before)
        self.assertTrue(clip.is_playing)
        self.assertTrue(self.slot(lease).track.arm)

    def test_boundary_crossed_during_logging_defers_unissued_start_or_finish(self):
        for operation in ("start", "finish"):
            with self.subTest(operation=operation):
                self.make()
                if operation == "finish":
                    lease, _ = self.record()
                    self.fixture.song.current_song_time = 6
                    self.session.accept(0)
                    target = 8
                    self.fixture.native_bar_clock = True
                    self.fixture.launch_window = lambda target: SimpleNamespace(
                        previous=target - 4, quantization=self.fixture.q_bar)
                else:
                    self.session.begin(0)
                    lease, target = self.session.take.slot, 4
                before_count = sum(call[0] == "fire" for call in self.fixture.calls)
                def logging_delay(event):
                    if event["event"] == "before_" + operation:
                        self.fixture.song.current_song_time = target
                self.backend.emit = logging_delay
                self.backend.tick()
                self.assertEqual(sum(call[0] == "fire" for call in self.fixture.calls), before_count)
                if operation == "finish":
                    self.assertFalse(self.backend.faults)
                    self.assertTrue(self.slot(lease).clip.is_recording)
                    self.assertEqual(self.session.take.schedule.start_at, 12)
                    self.assertEqual(self.backend._attempts[lease].finish_target, 12)
                    self.assertFalse(self.backend._attempts[lease].finish_issued)
                else:
                    self.assertFalse(self.backend.faults)
                    self.assertFalse(self.slot(lease).track.arm)
                    self.assertEqual(self.session.take.schedule.start_at, 8)
                    self.assertEqual(self.backend._attempts[lease].start_target, 8)
                    self.assertFalse(self.backend._attempts[lease].start_issued)

    def test_enable_and_idle_scope_reject_unowned_session_record_or_overdub_change(self):
        self.make()
        self.backend.disable()
        self.fixture.song.session_record = True
        with self.assertRaises(BackendFailure):
            self.backend.enable()
        self.assertFalse(self.backend.enabled)
        self.fixture.song.session_record = False
        self.backend.enable()
        for field in ("session_record", "overdub"):
            setattr(self.fixture.song, field, True)
            with self.assertRaises(BackendFailure):
                self.backend.validate_scope()
            self.assertEqual(self.session.begin(0), p.NOT_READY)
            setattr(self.fixture.song, field, False)
        self.assertTrue(self.backend.validate_scope())
        self.assertEqual(self.fixture.calls, [])

    def test_exact_owned_native_flags_allow_chaining_and_refresh_after_completion(self):
        self.make()
        old, _ = self.record()
        self.fixture.song.session_record = self.fixture.song.overdub = True
        self.assertTrue(self.backend.validate_scope())
        self.fixture.song.current_song_time = 6
        self.assertEqual(self.session.accept(0, True), p.OUTCOME_QUEUED)
        following = self.session.take.next_take.slot
        self.backend.tick()
        self.fixture.song.current_song_time = 8
        self.slot(old).finish_native(4)
        self.slot(following).begin_native(8)
        self.backend.tick()
        self.assertTrue(self.backend.validate_scope())
        self.assertTrue(self.fixture.song.session_record)
        self.assertTrue(self.fixture.song.overdub)
        self.fixture.song.current_song_time = 10
        self.assertEqual(self.session.accept(0), p.OUTCOME_QUEUED)
        self.backend.tick()
        self.fixture.song.current_song_time = 12
        self.slot(following).finish_native(4)
        self.fixture.song.session_record = self.fixture.song.overdub = False
        self.backend.tick()
        self.assertTrue(self.backend.validate_scope())
        self.assertEqual(len(self.session.groups[0].accepted), 2)
        self.assertFalse(self.backend.faults)

    def test_owned_cancel_transition_can_keep_native_flags_until_cleanup(self):
        self.make()
        lease, _ = self.record()
        self.fixture.song.session_record = self.fixture.song.overdub = True
        self.assertEqual(self.session.abort(0), p.APPLIED)
        self.backend.tick()
        self.assertTrue(self.backend.validate_scope())
        self.assertTrue(self.fixture.song.session_record)
        self.assertTrue(self.fixture.song.overdub)
        self.slot(lease).track.quiet_native()
        self.fixture.song.session_record = self.fixture.song.overdub = False
        self.backend.tick()
        self.assertTrue(self.backend.validate_scope())
        self.assertFalse(self.backend.pending_cancel(0))

    def test_initial_overdub_on_is_not_rewritten_and_owned_completion_refreshes_baseline(self):
        self.make()
        self.backend.disable()
        self.fixture.song.overdub = True
        self.backend.enable()
        lease, _ = self.record()
        self.assertTrue(self.fixture.song.overdub)
        self.fixture.song.current_song_time = 6
        self.session.accept(0)
        self.backend.tick()
        self.slot(lease).finish_native(4)
        self.fixture.song.current_song_time = 8
        self.fixture.song.overdub = False  # Native completion restores its flag.
        self.backend.tick()
        self.assertTrue(self.backend.validate_scope())
        self.assertEqual(len(self.session.groups[0].accepted), 1)


if __name__ == "__main__":
    unittest.main()
