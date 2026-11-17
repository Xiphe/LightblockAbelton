"""Ownership/transition checks; not an audible latency simulator."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PerformanceController.direct_monitoring import DirectMonitoring, DirectMonitoringError, AUTO, OFF


class Track:
    def __init__(self, name, trace):
        self.name, self.trace = name, trace
        self._arm, self._mode = False, OFF
        self.is_frozen = False
        self.playing_slot_index = -1
        self.clip_slots = [NS(has_clip=False, clip=None)]
        self.deleted = False
        self.disable_error = False
        self.arm_error_after_write = False
        self.arm_refused = False
        self.disarm_refused = False

    def _check(self):
        if self.deleted:
            raise RuntimeError('Native Track deleted')

    @property
    def arm(self):
        self._check()
        return self._arm

    @arm.setter
    def arm(self, value):
        self._check()
        self.trace.append((self.name, 'arm', value))
        if value and self.arm_refused or not value and self.disarm_refused:
            raise RuntimeError('arm setter refused')
        self._arm = value
        if value and self.arm_error_after_write:
            raise RuntimeError('uncertain arm write')

    @property
    def current_monitoring_state(self):
        self._check()
        return self._mode

    @current_monitoring_state.setter
    def current_monitoring_state(self, value):
        self._check()
        self.trace.append((self.name, 'monitor', value))
        if value == OFF and self.disable_error:
            raise RuntimeError('old path disable refused')
        self._mode = value

    def play(self, recording=False):
        self.playing_slot_index = 0
        self.clip_slots[0] = NS(has_clip=True, clip=NS(is_recording=recording, is_playing=True))


class Proxy:
    """Fresh wrappers sharing one Native Track's identity and state."""
    def __init__(self, track):
        object.__setattr__(self, '_track', track)

    def __getattr__(self, name):
        return getattr(self._track, name)

    def __setattr__(self, name, value):
        setattr(self._track, name, value)

    def __eq__(self, other):
        return self._track is getattr(other, '_track', other)


def reserve(group, *tracks):
    return NS(group=group, members=tuple(NS(track=t) for t in tracks))


def take(group, *tracks, state='recording'):
    result = NS(group=group, track=tracks[0], members=tuple(NS(track=t, armed_here=False) for t in tracks[1:]),
                armed_here=False, state=state, following=None)
    return result


class MonitoringChecks(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.backend = NS(recording=NS(take=None), reserves={}, audio_inputs=NS(monitor_owner=0), events=[])
        self.monitor = DirectMonitoring(self.backend)
        self.tracks = [Track(str(i), self.trace) for i in range(10)]

    def test_only_idle_owner_reserve_is_armed(self):
        self.backend.reserves = {0: reserve(0, *self.tracks[:2]), 1: reserve(1, *self.tracks[2:4])}
        self.monitor.tick()
        self.assertTrue(all(t.arm and t.current_monitoring_state == AUTO for t in self.tracks[:2]))
        self.assertTrue(all(not t.arm and t.current_monitoring_state == OFF for t in self.tracks[2:4]))
        self.assertEqual(len(self.monitor._owned), 2)
        self.trace.clear()
        self.monitor.tick()
        self.assertEqual(self.trace, [])

    def test_prepared_following_claim_does_not_silence_current_capture(self):
        current = take(0, *self.tracks[:2])
        self.monitor.claim_take(current)
        for t in self.tracks[:2]:
            t.play(recording=True)
        following = take(0, *self.tracks[2:4], state='queued')
        self.monitor.claim_take(following)
        self.assertTrue(all(t.current_monitoring_state == AUTO for t in self.tracks[:4]))
        self.assertTrue(current.armed_here and all(m.armed_here for m in current.members))
        self.assertTrue(following.armed_here and all(m.armed_here for m in following.members))

    def test_capture_hides_lookahead_until_accept_and_cancel_keeps_completed_loops(self):
        current = take(0, *self.tracks[:2])
        self.monitor.claim_take(current)
        self.backend.recording.take = current
        for t in self.tracks[:2]:
            t.play(recording=True)
        self.backend.reserves[0] = reserve(0, *self.tracks[2:4])
        self.monitor.tick()
        self.assertFalse(any(t.arm for t in self.tracks[2:4]))
        current.state = 'finish_queued'
        self.monitor.claim_reserve(self.backend.reserves[0])
        self.assertTrue(all(t.arm and t.current_monitoring_state == AUTO for t in self.tracks[:4]))
        for t in self.tracks[:2]:
            t.clip_slots[0].clip.is_recording = False
        following = take(0, *self.tracks[2:4], state='canceling')
        self.backend.recording.take = following
        self.backend.reserves[0] = reserve(0, *self.tracks[4:6])
        self.monitor.tick()
        self.assertTrue(all(t.clip_slots[0].clip.is_playing for t in self.tracks[:2]))
        self.assertFalse(any(t.arm for t in self.tracks[:4]))
        self.assertTrue(all(t.arm for t in self.tracks[4:6]))

    def test_owner_transfer_disables_old_inputs_before_enabling_new(self):
        self.backend.reserves = {0: reserve(0, *self.tracks[:2]), 1: reserve(1, *self.tracks[2:4])}
        self.monitor.tick()
        self.trace.clear()
        self.backend.audio_inputs.monitor_owner = 1
        self.monitor.tick()
        old_off = [self.trace.index((t.name, 'monitor', OFF)) for t in self.tracks[:2]]
        new_on = [self.trace.index((t.name, 'monitor', AUTO)) for t in self.tracks[2:4]]
        self.assertLess(max(old_off), min(new_on))
        self.assertFalse(any(t.arm for t in self.tracks[:2]))
        self.assertTrue(all(t.arm for t in self.tracks[2:4]))

    def test_active_capture_keeps_recording_but_loses_monitor_when_owner_changes(self):
        current = take(0, *self.tracks[:2])
        self.monitor.claim_take(current)
        self.backend.recording.take = current
        for t in self.tracks[:2]:
            t.play(recording=True)
        self.backend.reserves[1] = reserve(1, *self.tracks[2:4])
        self.backend.audio_inputs.monitor_owner = 1
        self.monitor.tick()
        self.assertTrue(all(t.arm and t.current_monitoring_state == OFF for t in self.tracks[:2]))
        self.assertTrue(all(t.clip_slots[0].clip.is_recording for t in self.tracks[:2]))
        self.assertTrue(all(t.arm and t.current_monitoring_state == AUTO for t in self.tracks[2:4]))

    def test_manual_external_arm_rejects_whole_bundle_before_any_claim(self):
        self.tracks[1].arm = True
        self.trace.clear()
        with self.assertRaisesRegex(ValueError, 'externally'):
            self.monitor.claim_take(take(0, *self.tracks[:2]))
        self.assertEqual(self.trace, [])
        self.assertEqual(self.monitor._owned, [])
        self.monitor.close()
        self.assertTrue(self.tracks[1].arm)

    def test_deleted_owned_destination_is_dropped_without_global_failure(self):
        self.backend.reserves[0] = reserve(0, self.tracks[0])
        self.monitor.tick()
        self.tracks[0].deleted = True
        self.backend.reserves.clear()
        self.monitor.tick()
        self.assertFalse(self.monitor.owned(self.tracks[0]))

    def test_fresh_equal_wrappers_do_not_look_like_external_arms(self):
        self.backend.reserves[0] = reserve(0, Proxy(self.tracks[0]))
        self.monitor.tick()
        self.backend.reserves[0] = reserve(0, Proxy(self.tracks[0]))
        self.trace.clear()
        self.monitor.tick()
        self.assertEqual(self.trace, [])
        self.assertEqual(len(self.monitor._owned), 1)
        self.assertTrue(self.monitor.owned(Proxy(self.tracks[0])))

    def test_patch_change_does_not_replace_captured_members(self):
        current = take(0, self.tracks[0])
        self.monitor.claim_take(current)
        self.tracks[0].play(recording=True)
        self.backend.recording.take = current
        self.backend.reserves[0] = reserve(0, self.tracks[1])
        self.monitor.tick()
        self.assertTrue(self.tracks[0].arm)
        self.assertFalse(self.tracks[1].arm)
        self.assertIs(current.track, self.tracks[0])

    def test_close_disarms_only_owned_nonrecording_tracks(self):
        self.monitor.claim_take(take(0, *self.tracks[:2]))
        self.tracks[0].play(recording=True)
        self.tracks[2].arm = True
        self.monitor.close()
        self.assertTrue(self.tracks[0].arm)
        self.assertFalse(self.tracks[1].arm)
        self.assertTrue(self.tracks[2].arm)
        self.assertEqual(self.monitor._owned, [])

    def test_owned_tracking_does_not_grow_with_completed_takes(self):
        for track in self.tracks:
            self.backend.reserves[0] = reserve(0, track)
            self.monitor.tick()
            self.assertEqual(len(self.monitor._owned), 1)
        self.backend.reserves.clear()
        self.monitor.tick()
        self.assertEqual(self.monitor._owned, [])

    def test_uncertain_disable_blocks_new_owner_enable(self):
        self.backend.reserves = {0: reserve(0, self.tracks[0]), 1: reserve(1, self.tracks[1])}
        self.monitor.tick()
        self.tracks[0].disable_error = True
        self.backend.audio_inputs.monitor_owner = 1
        with self.assertRaisesRegex(ValueError, 'could not be disabled'):
            self.monitor.tick()
        self.assertEqual(self.tracks[1].current_monitoring_state, OFF)
        self.assertFalse(self.tracks[1].arm)
        self.assertTrue(self.monitor.owned(self.tracks[0]))

    def test_uncertain_new_arm_stays_owned_until_cleanup_is_verified(self):
        track = self.tracks[0]
        track.arm_error_after_write = True
        track.disarm_refused = True
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.monitor.claim_take(take(0, track))
        self.assertTrue(track.arm)
        self.assertTrue(self.monitor.owned(track))
        self.assertEqual(track.current_monitoring_state, OFF)
        track.disarm_refused = False
        self.monitor.tick()
        self.assertFalse(track.arm)
        self.assertFalse(self.monitor.owned(track))

    def test_second_member_arm_failure_cleans_all_quiet_owned_members(self):
        first, second = self.tracks[:2]
        self.monitor.claim_reserve(reserve(0, first, second))
        second.arm = False
        second.arm_refused = True
        with self.assertRaises(DirectMonitoringError) as failure:
            self.monitor.claim_take(take(0, first, second))
        self.assertEqual(failure.exception.groups, (0,))
        self.assertEqual(failure.exception.tracks, (first, second))
        self.assertTrue(all(not t.arm and t.current_monitoring_state == OFF for t in (first, second)))
        self.assertFalse(self.monitor.owned(first))
        self.assertFalse(self.monitor.owned(second))

    def test_failed_tick_cleans_quiet_bundle_and_preserves_accepted_capture(self):
        current, first, refused = self.tracks[:3]
        self.monitor.claim_take(take(0, current))
        current.play(recording=True)
        capture = take(0, current, state='finish_queued')
        self.backend.recording.take = capture
        self.backend.reserves[0] = reserve(0, first, refused)
        refused.arm_refused = True
        with self.assertRaises(DirectMonitoringError):
            self.monitor.tick()
        self.assertTrue(current.arm)
        self.assertEqual(current.current_monitoring_state, AUTO)
        self.assertTrue(current.clip_slots[0].clip.is_recording)
        self.assertFalse(first.arm or refused.arm)
        self.assertEqual(first.current_monitoring_state, OFF)
        self.assertEqual(refused.current_monitoring_state, OFF)

    def test_failed_admission_does_not_clean_unrelated_owner_or_external_arm(self):
        owner, first, refused, manual = self.tracks[:4]
        self.monitor.claim_reserve(reserve(0, owner))
        manual.arm = True
        refused.arm_refused = True
        with self.assertRaises(DirectMonitoringError):
            self.monitor.claim_take(take(0, first, refused))
        self.assertTrue(owner.arm)
        self.assertEqual(owner.current_monitoring_state, AUTO)
        self.assertTrue(manual.arm)
        self.assertFalse(first.arm or refused.arm)

    def test_failed_bundle_preserves_triggered_owned_take_for_recording_containment(self):
        triggered, refused = self.tracks[:2]
        self.monitor.claim_take(take(0, triggered))
        triggered.fired_slot_index = 0
        refused.arm_refused = True
        with self.assertRaises(DirectMonitoringError):
            self.monitor.claim_take(take(0, triggered, refused))
        self.assertTrue(triggered.arm)
        self.assertEqual(triggered.current_monitoring_state, AUTO)
        self.assertTrue(self.monitor.owned(triggered))
        self.assertFalse(refused.arm)

    def test_recover_saved_idle_monitor_is_cache_only_and_reused_once(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = AUTO
        self.trace.clear()
        self.assertTrue(self.monitor.recover_saved(Proxy(track), 0))
        self.assertTrue(self.monitor.recover_saved(Proxy(track), 0))
        self.assertEqual(self.trace, [])
        self.assertTrue(self.monitor.owned(track))
        self.assertEqual(len(self.monitor._owned), 1)
        self.backend.reserves[0] = reserve(0, Proxy(track))
        self.monitor.tick()
        self.assertEqual(self.trace, [])
        self.assertEqual(len(self.monitor._owned), 1)

    def test_recovery_rejects_live_or_unsupported_native_states(self):
        track = self.tracks[0]
        self.assertFalse(self.monitor.recover_saved(track, 0))
        track.arm = True
        track.current_monitoring_state = 0
        self.assertFalse(self.monitor.recover_saved(track, 0))
        track.current_monitoring_state = AUTO
        track.is_frozen = True
        self.assertFalse(self.monitor.recover_saved(track, 0))
        track.is_frozen = False
        track.play(recording=True)
        self.assertFalse(self.monitor.recover_saved(track, 0))
        track.playing_slot_index = -1
        track.fired_slot_index = 0
        self.assertFalse(self.monitor.recover_saved(track, 0))
        self.assertEqual(self.monitor._owned, [])

    def test_recovered_stale_marker_on_playing_clip_retires_without_stopping_playback(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = AUTO
        track.play(recording=False)
        self.assertTrue(self.monitor.recover_saved(track, 0))
        self.monitor.tick()
        self.assertFalse(track.arm)
        self.assertEqual(track.current_monitoring_state, OFF)
        self.assertTrue(track.clip_slots[0].clip.is_playing)
        self.assertFalse(self.monitor.owned(track))

    def test_recovery_rejects_invalid_group_or_conflicting_owned_group(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = OFF
        self.trace.clear()
        for invalid in (-1, 8, True, None):
            self.assertFalse(self.monitor.recover_saved(track, invalid))
        self.assertTrue(self.monitor.recover_saved(track, 0))
        self.assertFalse(self.monitor.recover_saved(track, 1))
        self.assertEqual(self.trace, [])
        self.assertEqual(self.monitor._owned, [(track, 0)])

    def test_unowned_manual_adopted_recording_stays_untouched(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = 0
        track.play(recording=True)
        capture = take(0, track)
        capture.adopted = True
        self.backend.recording.take = capture
        self.backend.reserves[0] = reserve(0, self.tracks[1])
        self.trace.clear()
        self.monitor.tick()
        self.assertEqual(self.trace, [])
        self.assertFalse(self.monitor.owned(track))
        self.assertFalse(capture.armed_here)
        self.assertTrue(track.arm)
        self.assertEqual(track.current_monitoring_state, 0)
        self.assertFalse(self.tracks[1].arm)

    def test_adopted_capture_does_not_claim_manual_members_when_called_explicitly(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = AUTO
        track.play(recording=True)
        capture = take(0, track)
        capture.adopted = True
        self.trace.clear()
        self.monitor.claim_take(capture)
        self.assertEqual(self.trace, [])
        self.assertFalse(capture.armed_here)
        self.assertFalse(self.monitor.owned(track))

    def test_recovered_deleted_handle_is_removed_without_setters(self):
        track = self.tracks[0]
        track.arm = True
        track.current_monitoring_state = AUTO
        self.assertTrue(self.monitor.recover_saved(track, 0))
        track.deleted = True
        self.trace.clear()
        self.assertFalse(self.monitor.recover_saved(track, 0))
        self.assertEqual(self.monitor._owned, [])
        self.assertEqual(self.trace, [])

    def test_owner_zero_and_no_owner_are_distinct(self):
        self.backend.reserves[0] = reserve(0, self.tracks[0])
        self.monitor.tick()
        self.assertTrue(self.tracks[0].arm)
        self.backend.audio_inputs.monitor_owner = None
        self.monitor.tick()
        self.assertFalse(self.tracks[0].arm)
        self.assertEqual(self.tracks[0].current_monitoring_state, OFF)


class DeletedHandleChecks(unittest.TestCase):
    def test_boost_argument_error_retires_owned_deleted_handle(self):
        class ArgumentError(TypeError):pass
        class DeletedTrack(Track):
            def _check(self):
                if self.deleted:raise ArgumentError('None.None(Track) invalid Native handle')
        trace=[];old=DeletedTrack('removed',trace);new=Track('replacement',trace)
        backend=NS(recording=NS(take=None),reserves={0:reserve(0,old)},
                   audio_inputs=NS(monitor_owner=0),events=[])
        monitoring=DirectMonitoring(backend);monitoring.tick()
        old.deleted=True;backend.reserves[0]=reserve(0,new)
        monitoring.tick()
        self.assertFalse(monitoring.owned(old))
        self.assertTrue(new.arm);self.assertEqual(new.current_monitoring_state,AUTO)
        monitoring.close()

    def test_plain_type_error_is_not_reported_as_a_deleted_native_handle(self):
        class ProgrammingBug(Track):
            def _check(self):raise TypeError('wrong Python argument type')
        backend=NS(recording=NS(take=None),reserves={0:reserve(0,ProgrammingBug('bug',[]))},
                   audio_inputs=NS(monitor_owner=0),events=[])
        with self.assertRaisesRegex(TypeError,'wrong Python argument'):
            DirectMonitoring(backend).tick()


if __name__ == '__main__':
    unittest.main()
