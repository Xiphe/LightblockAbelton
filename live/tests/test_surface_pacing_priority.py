"""Command feedback refreshes group freshness through the actual MIDI writer."""

from types import SimpleNamespace
import unittest
import compact_assertions as wire
from unittest.mock import patch

import test_surface_pacing as pacing_tests
import test_surface_audio as audio_tests
from LightpadController import configuration as config, protocol as p, timing
from LightpadController import surface as module


class SurfacePacingPriorityTests(unittest.TestCase):
    def test_single_dropped_snapshot_keeps_group_inside_device_freshness_expiry(self):
        now = [100.0]
        sent = []
        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: sent.append((now[0], message))))
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface, lambda: now[0])
            self.addCleanup(surface.disconnect)
            surface._engine = pacing_tests.EightClockEngine()
            surface.receive_midi((0xBF, 110, p.VERSION))
            for step in range(101):
                now[0] = 100.0 + step * .1
                surface.update_display()

        snapshots = []
        clock_streams = set()
        for cursor, (instant, message) in enumerate(sent):
            if message[1] not in (33, 34):
                continue
            start = message[1]
            frame = sent[cursor:cursor + wire.size(start)]
            self.assertEqual([entry[0] for entry in frame], [instant] * wire.size(start))
            self.assertEqual([entry[1][0] for entry in frame], [message[0]] * wire.size(start))
            self.assertEqual([entry[1][1] for entry in frame], wire.controllers(start))
            self.assertEqual(frame[-1][1][2], message[2])
            if start == 34 and instant >= 103.0:
                clock_streams.add(message[0])
            if message[:2] == (0xB7, 33):
                snapshots.append(instant)

        self.assertEqual(clock_streams, set(range(0xB0, 0xB8)) | {0xBF},
                         "all group clocks and the global clock must compete for wire capacity")
        dropped = next(index for index, instant in enumerate(snapshots) if instant >= 103.0)
        self.assertGreater(dropped, 0)
        self.assertLess(dropped, len(snapshots) - 1)
        # The device ignores one entire frame, retaining its previous complete
        # snapshot until the next complete frame is received.
        received = snapshots[:dropped] + snapshots[dropped + 1:]
        self.assertLess(max(b - a for a, b in zip(received, received[1:])), 3.0,
                        "one dropped group 7 snapshot at %.2f must not reach the device's "
                        "three-second expiry; remaining receive times: %r" %
                        (snapshots[dropped], received))

    def test_wall_clock_callbacks_cannot_start_additional_take_before_native_reference_wrap(self):
        helper = audio_tests.AudioSurfaceTests()
        surface, _ = helper.make()
        self.addCleanup(helper.doCleanups)
        native = helper.helper
        surface.receive_midi((0xBF, 110, p.VERSION))
        helper.pump(surface)
        helper.command(surface, p.BEGIN, seq=1)
        first = surface._engine.session.take
        first_slot = native.slot(first.slot)
        self.assertEqual(first.schedule.start_at, 4)
        helper.pump(surface)

        native.song.current_song_time = 4
        reference_clip = first_slot.begin_native(4)
        surface.update_display()
        self.assertEqual(first.state, "recording")
        native.song.current_song_time = 15
        helper.command(surface, p.ACCEPT, seq=2)
        self.assertEqual(surface._engine.session.groups[0].accepted, [])
        native.song.current_song_time = 16
        first_slot.finish_native(12)
        surface.update_display()
        accepted = list(surface._engine.session.groups[0].accepted)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].length, 12)
        self.assertIs(first_slot.clip, reference_clip)
        helper.pump(surface)
        helper.command(surface, p.ACK_COMPLETION, seq=3, arg=first.token % 128)
        helper.pump(surface)

        native.song.current_song_time = 17
        reference_clip.playing_position = 1.0
        helper.command(surface, p.BEGIN, seq=4)
        additional = surface._engine.session.take
        additional_slot = native.slot(additional.slot)
        self.assertEqual(additional.schedule.start_at, 28)
        self.assertFalse(additional_slot.track.arm)
        self.assertFalse(additional_slot.has_clip)
        calls_before = list(native.calls)
        wall_started = helper.now
        for _ in range(10):
            helper.pump(surface)

        self.assertGreaterEqual(helper.now - wall_started, 10.0 - 1e-8)
        self.assertEqual(native.song.current_song_time, 17)
        self.assertEqual(native.calls, calls_before,
                         "wall time cannot reach the native bar-24 arm/fire window for target 28")
        self.assertIs(surface._engine.session.take, additional)
        self.assertEqual(additional.state, "queued")
        self.assertEqual(surface._engine.session.view, 1)
        self.assertEqual(surface._engine.session.groups[0].accepted, accepted)
        self.assertFalse(additional_slot.has_clip)
        self.assertFalse(additional_slot.track.arm)
        self.assertIs(first_slot.clip, reference_clip)
        self.assertTrue(reference_clip.is_playing)
        self.assertEqual(reference_clip.playing_position, 1.0)
        self.assertEqual(surface._engine.backend.faults, {})
        native.song.current_song_time = 24
        reference_clip.playing_position = 8.0
        surface.update_display()
        self.assertEqual([call[0] for call in native.calls[len(calls_before):]], ["arm", "fire"])
        self.assertTrue(additional_slot.track.arm)
        self.assertTrue(additional_slot.is_triggered)
        self.assertEqual(additional.state, "queued")
        self.assertEqual(surface._engine.session.groups[0].accepted, accepted)

    def test_pending_clock_waits_for_new_take_snapshot_and_samples_native_position(self):
        now = [100.0]
        sent = []

        class ChangingTakeEngine(pacing_tests.EightClockEngine):
            latest_snapshot_take = 10
            context = timing.ONE_BAR
            native_position = .25

            def __init__(self):
                self.samples = []

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                if group == 0:
                    fields[91], fields[107] = self.context, self.latest_snapshot_take
                return fields

            def group_clock(self, group):
                if group != 0:
                    return super().group_clock(group)
                self.samples.append((now[0], self.context, self.latest_snapshot_take))
                return timing.Clock(self.context, self.latest_snapshot_take,
                                    4.0 if self.context == timing.ONE_BAR else 8.0,
                                    self.native_position, 0.0, 4.0, 120.0, 4, 4, True)

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: sent.append((now[0], message))))
        engine = ChangingTakeEngine()
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface, lambda: now[0])
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            self.assertTrue(any(message[:2] == (0xB0, 33) for _, message in sent))
            self.assertFalse(any(message[:2] == (0xB0, 34) for _, message in sent))

            engine.latest_snapshot_take = 11
            engine.context = timing.REFERENCE
            engine.native_position = 2.75
            for instant in (100.1, 100.2, 100.3):
                now[0] = instant
                # Flush the already queued suppliers without publishing a new
                # snapshot, so matching must be checked at actual dequeue.
                surface.receive_midi((0xB7, 39, 0))
            self.assertTrue(any(take == 11 for _, _, take in engine.samples))
            self.assertFalse(any(message[:2] == (0xB0, 34) for _, message in sent),
                             "a new native clock cannot use the old take's snapshot")

            engine.native_position = 3.125
            for step in range(4, 11):
                now[0] = 100.0 + step * .1
                surface.update_display()

        matching_snapshot = None
        fresh_clocks = []
        for cursor, (_, message) in enumerate(sent):
            if message[0] != 0xB0 or message[1] not in (33, 34):
                continue
            start = message[1]
            frame = [entry[1] for entry in sent[cursor:cursor + wire.size(start)]]
            self.assertEqual([item[1] for item in frame], wire.controllers(start))
            fields = wire.snapshot_fields(self, frame) if start == 33 else wire.clock_fields(self, frame)
            if start == 33 and (fields[91], fields[107]) == (p.PLAYING, 11):
                matching_snapshot = cursor
            if start == 34 and fields[72] == 11:
                self.assertEqual(fields[71] & 3, timing.REFERENCE)
                self.assertIsNotNone(matching_snapshot)
                self.assertLess(matching_snapshot, cursor)
                position = (fields[76] | fields[77] << 7 | fields[78] << 14) / 64.0
                self.assertEqual(position, 3.125)
                fresh_clocks.append(cursor)
        self.assertTrue(fresh_clocks, "the matching new take clock must eventually transmit")

    def test_command_snapshot_counts_as_group_heartbeat_at_periodic_boundary(self):
        now = [100.0]
        sent = []

        class MuteEngine(pacing_tests.EightClockEngine):
            muted = False

            def dispatch(self, command):
                if command != p.Command(9, 0, p.SET_MUTE, 127):
                    raise AssertionError("unexpected mute command: %r" % (command,))
                self.muted = True
                return p.APPLIED

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                fields[92] = 127 if group == 0 and self.muted else 0
                return fields

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: sent.append((now[0], message))))
        engine = MuteEngine()
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface, lambda: now[0])
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            for step in range(18):
                now[0] = 100.0 + step * .1
                surface.update_display()

            sent.clear()
            command_at = now[0] = 101.75
            for cc, value in zip(range(60, 64), (9, p.SET_MUTE, 127, 9)):
                surface.receive_midi((0xB0, cc, value))
            for offset in (.05, .15, .25, .35, .45, .5, .6, .7, .8, .9,
                           1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0):
                now[0] = command_at + offset
                surface.update_display()

        snapshots = []
        for cursor, (instant, message) in enumerate(sent):
            if message[1] != 33:
                continue
            frame = sent[cursor:cursor + 17]
            self.assertEqual([entry[0] for entry in frame], [instant] * 17)
            wire.assert_frame(self, [entry[1] for entry in frame], 33)
            snapshots.append((instant, message[0] - 0xB0,
                              wire.snapshot_fields(self, [entry[1] for entry in frame])))

        delivered_outcomes = []
        for cursor, (instant, message) in enumerate(logical_sent):
            if message[:2] == (0xB0, 40):
                logical = logical_sent[cursor:cursor + 20]
                self.assertEqual([item[1][1] for item in logical], list(range(40, 60)))
                fields = dict((msg[1], msg[2]) for _, msg in logical)
                if (fields[54], fields[55]) == (9, p.APPLIED):
                    delivered_outcomes.append(instant)
        self.assertTrue(delivered_outcomes, 'protected Native receipt must complete its actual send')
        feedback = [(instant, fields) for instant, group, fields in snapshots
                    if group == 0 and fields[92] == 127 and instant in delivered_outcomes]
        self.assertTrue(feedback,
                        "the actual mute outcome must reach group 0 promptly")
        transmitted_at = feedback[0][0]
        self.assertLessEqual(transmitted_at - command_at, .15 + 1e-8)
        recent = [(instant, fields) for instant, group, fields in snapshots
                  if group == 0 and transmitted_at <= instant <= transmitted_at + .30 + 1e-8]
        self.assertEqual({group for _, group, _ in snapshots if group != 0}, set(range(1, 8)),
                         "other group heartbeats must continue after command feedback")
        self.assertEqual(len(recent), 1,
                         "the command snapshot already refreshed group 0; a periodic boundary "
                         "must not emit another full snapshot within 300 ms: %r" %
                         [instant for instant, _ in recent])


if __name__ == "__main__":
    unittest.main()
