"""Late start intent remains queued at a safe boundary, without native timing proof."""

import unittest
from unittest.mock import patch

import test_audio_controller as controller_helpers
from LightpadController import protocol as p
from LightpadController import audio_backend


FEATURES = ('record', 'cancel', 'wrap', 'accept_next', 'advance')


class StartAdmissionTests(unittest.TestCase):
    def setUp(self):
        timer = patch.object(audio_backend.time, 'monotonic', return_value=100.0)
        timer.start()
        self.addCleanup(timer.stop)
        self.helper = controller_helpers.AudioControllerTests()
        self.controller = self.helper.make(probe=False, features=FEATURES)
        self.addCleanup(self.helper.doCleanups)
        self.song, self.calls = self.helper.song, self.helper.calls

    def leases(self):
        return self.controller.backend.diagnostic_state()['leases']

    def assert_queued_new_lease(self, previous_generations, target):
        new = [lease for lease in self.leases() if lease['generation'] not in previous_generations]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]['state'], 'queued')
        self.assertEqual(new[0]['start_target'], target)
        self.assertEqual(new[0]['token'], self.controller.session.take.token)

    def test_begin_inside_transaction_lead_retains_intent_for_following_bar(self):
        self.song.current_song_time = 3.992
        generations = {lease['generation'] for lease in self.leases()}
        self.assertEqual(self.controller.dispatch(self.helper.command(p.BEGIN)), p.OUTCOME_QUEUED)
        self.assertEqual(self.calls, [])
        current = self.controller.session.take
        self.assertEqual((current.schedule.queued_at, current.schedule.start_at), (3.992, 8))
        self.assertEqual(current.state, 'queued')
        self.assertEqual(self.controller.session.recording_group, 0)
        self.assertEqual(self.controller.backend.faults, {})
        self.assert_queued_new_lease(generations, 8)
        self.song.current_song_time = 3.999
        self.controller.tick()
        self.assertEqual(self.calls, [], 'the queued bar-8 take must not enqueue before its bar-4 window')
        self.assertIs(self.controller.session.take, current)
        self.assertEqual(current.state, 'queued')

    def test_additional_begin_late_for_reference_wrap_retains_intent_for_following_wrap(self):
        reference_lease, reference, _ = self.helper.accept(length=4)
        self.song.current_song_time = 11.992
        reference.playing_position = 3.992
        calls = list(self.calls)
        generations = {lease['generation'] for lease in self.leases()}
        accepted = list(self.controller.session.groups[0].accepted)
        self.assertEqual(self.controller.dispatch(self.helper.command(p.BEGIN)), p.OUTCOME_QUEUED)
        self.assertEqual(self.calls, calls)
        current = self.controller.session.take
        self.assertEqual((current.schedule.queued_at, current.schedule.start_at), (11.992, 16))
        self.assertEqual(current.state, 'queued')
        self.assertEqual(self.controller.session.recording_group, 0)
        self.assertEqual(self.controller.backend.faults, {})
        self.assert_queued_new_lease(generations, 16)
        self.song.current_song_time = 11.999
        self.controller.tick()
        self.assertEqual(self.calls, calls, 'the queued wrap-16 take must not enqueue before its bar-12 window')
        self.assertEqual(self.controller.session.groups[0].accepted, accepted)
        self.assertIs(self.helper.slot(reference_lease).clip, reference)
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))
        self.assertTrue(reference.is_playing)

    def test_retry_cleans_up_old_attempt_and_retains_late_replacement_for_following_wrap(self):
        reference_lease, reference, _ = self.helper.accept(length=4)
        self.song.current_song_time = 9
        reference.playing_position = 1
        self.assertEqual(self.controller.dispatch(self.helper.command(p.BEGIN)), p.OUTCOME_QUEUED)
        old = self.controller.session.take
        slot = self.helper.slot(old.slot)
        self.song.current_song_time = 12
        old_clip = slot.begin_native(12)
        self.controller.tick()
        self.assertEqual(old.state, 'recording')
        self.song.current_song_time = 15.992
        reference.playing_position = 3.992
        calls = len(self.calls)
        generations = {lease['generation'] for lease in self.leases()}
        accepted = list(self.controller.session.groups[0].accepted)
        self.assertEqual(self.controller.dispatch(self.helper.command(p.RETRY)), p.OUTCOME_QUEUED)
        replacement = self.controller.session.take
        self.assertNotEqual(replacement.token, old.token)
        self.assertEqual((replacement.schedule.queued_at, replacement.schedule.start_at), (15.992, 20))
        self.assertEqual(replacement.state, 'queued')
        self.assertEqual(self.controller.backend.faults, {})
        self.assert_queued_new_lease(generations, 20)
        self.assertEqual(self.calls[calls:], [('stop', slot.track.name, False)])
        self.assertTrue(self.controller.backend.pending_cancel(0))
        self.assertIs(slot.clip, old_clip)
        slot.track.quiet_native()
        self.controller.tick()
        self.assertFalse(self.controller.backend.pending_cancel(0))
        self.assertEqual(self.calls[calls:], [('stop', slot.track.name, False),
                                             ('delete', slot.track.name, slot.row),
                                             ('arm', slot.track.name, False)])
        self.assertFalse(slot.has_clip)
        self.assertFalse(slot.track.arm)
        self.song.current_song_time = 15.999
        self.controller.tick()
        self.assertIs(self.controller.session.take, replacement)
        self.assertEqual(replacement.state, 'queued')
        self.assertEqual(self.calls[calls:], [('stop', slot.track.name, False),
                                             ('delete', slot.track.name, slot.row),
                                             ('arm', slot.track.name, False)])
        self.assertEqual(self.controller.backend.faults, {})
        self.assertEqual(self.controller.session.groups[0].accepted, accepted)
        self.assertIs(self.helper.slot(reference_lease).clip, reference)
        self.assertTrue(reference.is_playing)
        self.assertEqual((reference.start_time, reference.loop_end), (8, 4))


if __name__ == '__main__':
    unittest.main()
