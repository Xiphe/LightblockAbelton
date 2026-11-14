"""Bounded wire traffic through real surface callbacks, without Live or ports."""

from types import SimpleNamespace
import unittest
import compact_assertions as wire
from unittest.mock import patch

import test_surface
from LightpadController import configuration as config, protocol as p, timing
from LightpadController import surface as module


class EightClockEngine:
    """Injected native-state/clock port; pacing remains the real surface's job."""
    def tick(self):
        return []

    def snapshot(self, group, sequence=0, outcome=p.NONE):
        fields = dict.fromkeys(range(91, 109), 0)
        fields.update({91: p.PLAYING, 94: 127, 98: 64, 102: 64,
                       103: 31, 104: sequence, 105: outcome})
        return fields

    def global_clock(self):
        return timing.Clock(timing.ONE_BAR, 0, 4.0, 1.0, 0.0, 4.0,
                            120.0, 4, 4, True)

    def group_clock(self, group):
        return timing.Clock(timing.REFERENCE, 0, 8.0, 1.0, 0.0, 4.0,
                            120.0, 4, 4, True)


class SurfacePacingTests(unittest.TestCase):
    def test_async_completion_observed_on_next_frame_start_is_pinned_before_busy_rejection(self):
        now = [100.0]

        class AsyncEngine(EightClockEngine):
            def __init__(self):
                self.commands = []
                self.pending = None
                self.completion_ready = False
                self.completions = 0

            def dispatch(self, command):
                self.commands.append(command)
                if command.seq == 27:
                    self.pending = command
                    return None
                return p.APPLIED

            def tick(self):
                if self.completion_ready and self.pending is not None:
                    command, self.pending = self.pending, None
                    self.completions += 1
                    return [(command, p.APPLIED)]
                return []

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                if group == 7 and self.completions:
                    fields[91] = p.EMPTY
                return fields

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, sent=[], logs=[])
        engine = AsyncEngine()

        def command(sequence, opcode):
            for cc, value in zip(range(60, 64), (sequence, opcode, 0, sequence)):
                surface.receive_midi((0xB7, cc, value))

        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            # Occupy the one reserved slot too, so this test exercises an
            # actually queued result under the current 3+1 budget.
            surface._output.offer(p.encode_snapshot(0,1,engine.snapshot(0)),100.0,lambda:None)
            surface._output.flush(100.0)
            now[0] = 100.001
            command(27, p.ADVANCE)
            self.assertEqual(engine.completions, 0)
            # The observation becomes available exactly before CC60 of the
            # next request; receive_midi ticks before parsing that first byte.
            engine.completion_ready = True
            now[0] = 100.002
            command(28, p.BEGIN)
            self.assertEqual(engine.completions, 1)
            self.assertEqual([item.seq for item in engine.commands], [27],
                             "the second request must wait for the first result's wire delivery")
            for step in range(1, 11):
                now[0] = 100.0 + step * .1
                surface.update_display()

            outcomes = []
            for cursor, message in enumerate(logical_sent):
                if message[:2] == (0xB7, 40):
                    frame = logical_sent[cursor:cursor + 20]
                    self.assertEqual([item[1] for item in frame], list(range(40, 60)))
                    fields = dict((cc, value) for _, cc, value in frame)
                    outcomes.append((fields[54], fields[55]))
            self.assertIn((27, p.APPLIED), outcomes,
                          "the newly observed completion must be pinned before BUSY changes latest feedback")
            self.assertIn((28, p.BUSY), outcomes)
            self.assertLess(outcomes.index((27, p.APPLIED)), outcomes.index((28, p.BUSY)))
            now[0] = 101.001
            command(29, p.BEGIN)
            self.assertEqual([item.seq for item in engine.commands], [27, 29],
                             "wire completion must release the original reservation without repeating its action")
            self.assertEqual(engine.completions, 1)

    def test_partial_midi_send_failure_retries_protected_frame_without_repeating_native_command(self):
        now = [100.0]

        class CommandEngine(EightClockEngine):
            def __init__(self):
                self.commands = []

            def dispatch(self, command):
                self.commands.append(command)
                return p.APPLIED

        class FailingOutput(list):
            failures = 0
            fail_protected = False

            def append(self, message):
                if self.fail_protected and message == (0xB7, 46, 64) and not self.failures:
                    self.failures += 1
                    raise OSError("injected MIDI boundary failure during protected outcome")
                super().append(message)

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, sent=FailingOutput(), logs=[])
        engine = CommandEngine()
        errors = []

        def command(sequence):
            for cc, value in zip(range(60, 64), (sequence, p.SET_VOLUME, 64, sequence)):
                surface.receive_midi((0xB7, cc, value))

        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            original_send = surface._output.send
            def send_with_failure_context(messages):
                instance.sent.fail_protected = (len(messages) == 20 and messages[0][0] == 0xB7
                    and messages[14][2] == 27 and messages[15][2] == p.APPLIED)
                try:
                    original_send(messages)
                finally:
                    instance.sent.fail_protected = False
            surface._output.send = send_with_failure_context
            surface.receive_midi((0xBF, 110, p.VERSION))
            # Establish the prior heartbeat first. Otherwise startup's still-
            # unsent group can accidentally reconstruct a lost protected job.
            for step in range(1, 9):
                now[0] = 100.0 + step * .1
                surface.update_display()
            self.assertIn((0xB7, 33), [message[:2] for message in instance.sent])
            now[0] = 101.0
            surface.update_display()
            instance.sent.clear()
            logical_sent.clear()
            now[0] = 101.001
            command(27)
            for step in range(1, 5):
                now[0] = 101.0 + step * .1
                try:
                    surface.update_display()
                except OSError as error:
                    errors.append(error)
            self.assertEqual(instance.sent.failures, 1, "the test must fail during the protected outcome")
            self.assertLessEqual(len(errors), 1)
            self.assertEqual(len(engine.commands), 1,
                             "recovery may repeat only the MIDI frame, never the native command")
            complete_applied = []
            for cursor, message in enumerate(logical_sent):
                if message[:2] != (0xB7, 40):
                    continue
                frame = logical_sent[cursor:cursor + 20]
                # The injected partial frame is discarded at the next start;
                # only an entire replacement can confirm the command.
                if ([item[0] for item in frame] != [0xB7] * 20
                        or [item[1] for item in frame] != list(range(40, 60))
                        or frame[-1][2] != frame[0][2]):
                    continue
                fields = dict((cc, value) for _, cc, value in frame)
                if (fields[54], fields[55]) == (27, p.APPLIED):
                    complete_applied.append(frame)
            compact_complete = [instance.sent[cursor:cursor + 17]
                for cursor, message in enumerate(instance.sent) if message[:2] == (0xB7, 33)
                and [item[1] for item in instance.sent[cursor:cursor + 17]] == wire.controllers(33)]
            self.assertTrue(compact_complete, 'partial compact send must be followed by a complete replacement')
            for compact in compact_complete:
                wire.assert_frame(self, compact, 33)
            self.assertTrue(complete_applied,
                            "a complete replacement APPLIED frame must follow the partial-send failure")
            now[0] = 101.401
            command(28)
            self.assertEqual([item.seq for item in engine.commands], [27, 28],
                             "successful replacement transmission must release the original parser reservation")

    def test_queued_applied_result_survives_repeated_hello_and_v2_peer_without_reexecution(self):
        now = [100.0]

        class CommandEngine(EightClockEngine):
            def __init__(self):
                self.commands = []

            def dispatch(self, command):
                self.commands.append(command)
                return p.APPLIED

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, sent=[], logs=[])
        engine = CommandEngine()

        def command(sequence):
            for cc, value in zip(range(60, 64), (sequence, p.SET_VOLUME, 64, sequence)):
                surface.receive_midi((0xB7, cc, value))

        def outcomes(messages):
            result = []
            for cursor, message in enumerate(messages):
                if message[:2] == (0xB7, 40):
                    frame = messages[cursor:cursor + 20]
                    self.assertEqual([item[1] for item in frame], list(range(40, 60)))
                    fields = dict((cc, value) for _, cc, value in frame)
                    result.append((fields[54], fields[55]))
            return result

        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            # Occupy the one reserved slot too, so this test exercises an
            # actually queued result under the current 3+1 budget.
            surface._output.offer(p.encode_snapshot(0,1,engine.snapshot(0)),100.0,lambda:None)
            surface._output.flush(100.0)
            now[0] = 100.001
            command(17)
            self.assertEqual(len(engine.commands), 1)
            self.assertNotIn((17, p.APPLIED), outcomes(logical_sent),
                             "the result must still be queued when the peer reconnects")
            now[0] = 100.002
            surface.receive_midi((0xBF, 110, p.VERSION))
            now[0] = 100.003
            surface.receive_midi((0xBF, 110, 2))
            before = list(instance.sent)
            now[0] = 100.004
            surface.update_display()
            self.assertEqual(instance.sent, before, "an incompatible peer must receive no queued result")
            now[0] = 100.005
            surface.receive_midi((0xBF, 110, p.VERSION))
            for step in range(1, 7):
                now[0] = 100.0 + step * .1
                surface.update_display()
            self.assertEqual(outcomes(logical_sent).count((17, p.APPLIED)), 1,
                             "reconnect must deliver the original protected outcome exactly once")
            self.assertEqual(len(engine.commands), 1)
            cursor = len(logical_sent)
            now[0] = 100.601
            command(17)
            for step in range(7, 11):
                now[0] = 100.0 + step * .1
                surface.update_display()
            self.assertIn((17, p.APPLIED), outcomes(logical_sent[cursor:]),
                          "the completed command must replay its cached outcome after reconnect")
            self.assertEqual(len(engine.commands), 1, "a replay must not execute the native mutation twice")
            now[0] = 101.001
            command(18)
            self.assertEqual(len(engine.commands), 2,
                             "actual outcome transmission must release the parser for a new command")

    def test_eight_group_outcomes_survive_new_busy_command_and_replay_before_wire_delivery(self):
        now = [100.0]

        class CommandEngine(EightClockEngine):
            def __init__(self):
                self.commands = []

            def dispatch(self, command):
                self.commands.append(command)
                return p.APPLIED if command.seq == 10 else p.BUSY

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                if group == 7 and now[0] >= 100.02:
                    fields[106], fields[107] = 4, 77
                return fields

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, sent=[], logs=[])
        engine = CommandEngine()

        def command(group, sequence):
            for cc, value in zip(range(60, 64), (sequence, p.SET_VOLUME, 64, sequence)):
                surface.receive_midi((0xB0 + group, cc, value))

        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = engine
            surface.receive_midi((0xBF, 110, p.VERSION))
            now[0] = 100.001
            for group in range(8):
                command(group, 10)
            now[0] = 100.005
            command(7, 11)
            command(7, 11)  # Original result is unsent, so both requests remain BUSY.
            for step in range(1, 21):
                now[0] = 100.0 + step * .1
                surface.update_display()

        snapshots = {group: [] for group in range(8)}
        for cursor, message in enumerate(logical_sent):
            if message[1] != 40:
                continue
            frame = logical_sent[cursor:cursor + 20]
            self.assertEqual([item[1] for item in frame], list(range(40, 60)))
            snapshots[message[0] - 0xB0].append(dict((cc, value) for _, cc, value in frame))
        self.assertEqual(sum(command.op == p.SET_VOLUME for command in engine.commands), 8,
                         "a newer command cannot execute before the first outcome reaches the wire")
        self.assertEqual([(command.group, command.arg) for command in engine.commands
                          if command.op == p.ACK_COMPLETION], [(7, 77)])
        for group, frames in snapshots.items():
            self.assertTrue(any(fields[54] == 10 and fields[55] == p.APPLIED for fields in frames),
                            "group %d lost its first APPLIED outcome before wire delivery" % group)
        last = snapshots[7]
        applied = next(index for index, fields in enumerate(last)
                       if fields[54] == 10 and fields[55] == p.APPLIED)
        busy = next(index for index, fields in enumerate(last)
                    if fields[54] == 11 and fields[55] == p.BUSY)
        self.assertLess(applied, busy, "a newer BUSY outcome must follow the earlier APPLIED outcome")
        self.assertTrue(any(fields[56] == 4 and fields[57] == 77 for fields in last),
                        "terminal completion must reach the device despite queued outcomes")

    def test_sustained_jittered_callbacks_keep_all_groups_and_clocks_fresh_without_bursts(self):
        self._assert_sustained_callbacks((0.100, 0.107, 0.104, 0.102,
                                         0.108, 0.101, 0.105, 0.103))

    def test_near_100ms_jitter_and_adjacent_receives_preserve_the_same_freshness_and_rates(self):
        self._assert_sustained_callbacks((0.0997, 0.1003), adjacent_receive=True)

    def test_twelve_controls_keep_snapshots_and_eight_loop_clocks_fresh(self):
        self._assert_sustained_callbacks((.1,), controls=12)

    def _assert_sustained_callbacks(self, intervals, adjacent_receive=False, controls=8):
        now = [100.0]
        sent = []

        class RunningEngine(EightClockEngine):
            def control_indices(self):
                return range(controls)

            def snapshot(self, group, sequence=0, outcome=p.NONE):
                fields = super().snapshot(group, sequence, outcome)
                fields[93] = int(((now[0] - 100.0) * 2 % 8) * 16)
                fields[94] = int((now[0] - 100.0) * 37) % 127
                return fields

            def global_clock(self):
                return timing.Clock(timing.ONE_BAR, 0, 4.0,
                                    (now[0] - 100.0) * 2 % 4, 0.0, 4.0,
                                    120.0, 4, 4, True)

            def group_clock(self, group):
                return timing.Clock(timing.REFERENCE, 0, 8.0,
                                    (now[0] - 100.0) * 2 % 8, 0.0, 4.0,
                                    120.0, 4, 4, True)

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=0.0)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: sent.append((now[0], message))))
        # Both workloads use the same deadlines, rates and wire assertions.
        callbacks = [100.0] if adjacent_receive else [100.099, 100.1001]
        for step in range(120):
            callbacks.append(callbacks[-1] + intervals[step % len(intervals)])
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = RunningEngine()
            surface.receive_midi((0xBF, 110, p.VERSION))
            for instant in callbacks:
                now[0] = instant
                song.current_song_time = (instant - 100.0) * 2
                surface.update_display()
                if adjacent_receive:
                    now[0] = instant + .00005
                    song.current_song_time = (now[0] - 100.0) * 2
                    surface.receive_midi((0xB0, 39, 0))

        snapshots = {group: [] for group in range(controls)}
        clocks = {status: [] for status in list(range(0xB0, 0xB8)) + [0xBF]}
        frame_times, metadata_times = [], []
        cursor = 0
        while cursor < len(sent):
            instant, (status, cc, revision) = sent[cursor]
            if cc in (33, 34, 35):
                size = wire.size(cc)
                if cc == 35:
                    self.assertEqual(status, 0xBF)
                frame = sent[cursor:cursor + size]
                self.assertEqual(len(frame), size)
                self.assertEqual([entry[0] for entry in frame], [instant] * size)
                self.assertEqual([entry[1][0] for entry in frame], [status] * size)
                self.assertEqual([entry[1][1] for entry in frame], wire.controllers(cc))
                self.assertEqual(frame[-1][1][2], revision)
                frame_times.append(instant)
                if cc == 33:
                    snapshots[status - 0xB0].append(instant)
                elif cc == 34:
                    clocks[status].append(instant)
                cursor += size
            else:
                self.assertEqual(status, 0xBF)
                self.assertIn(cc, (110, 111, 112, 113))
                metadata_times.append(instant)
                cursor += 1

        for times, limit in ((frame_times, 3), (metadata_times, 5)):
            for instant in times:
                self.assertLessEqual(sum(instant - .09 + 1e-8 < at <= instant for at in times), limit,
                                     "the budget applies across adjacent windows, not just callback batches")
        end, warmup = now[0], 103.0
        for group, times in snapshots.items():
            self.assertTrue(times, "every group needs an authoritative snapshot")
            self.assertLess(times[0] - 100.0, 1.0, "initial snapshot group %d" % group)
            self.assertLessEqual(max(b - a for a, b in zip(times, times[1:] + [end])), 2.0 + 1e-8,
                                 "heartbeat group %d must stay below its three-second expiry" % group)
            self.assertLessEqual(sum(at >= warmup for at in times) / (end - warmup), 1.3,
                                 "phase/countdown changes must not create extra full snapshots")
        for status, times in clocks.items():
            self.assertTrue(times, "every active clock stream must be served")
            bounded = [100.0] + times + [end]
            self.assertLess(max(b - a for a, b in zip(bounded, bounded[1:])), 1.0,
                            "clock %02x must remain below its one-second expiry" % status)
            rate = sum(at >= warmup for at in times) / (end - warmup)
            if status == 0xBF:
                self.assertGreaterEqual(rate, 3.0, "the independent bar clock keeps its roughly 4 Hz cadence")
                self.assertLessEqual(rate, 4.5)
            else:
                self.assertGreaterEqual(rate, 1.5)
                self.assertLessEqual(rate, 2.5, "group clocks should coalesce to roughly 2 Hz")
        first_heartbeats = [next(at for at in times if at >= warmup) for times in snapshots.values()]
        self.assertGreaterEqual(len(set(first_heartbeats)), 3,
                                "group heartbeats must be spread across callback opportunities")

    def test_clock_phase_is_sampled_when_its_frame_is_transmitted_after_backpressure(self):
        now = [100.0]
        sent = []

        class MovingClockEngine(EightClockEngine):
            def global_clock(self):
                return timing.Clock(timing.ONE_BAR, 0, 4.0,
                                    1.0 + 2.0 * (now[0] - 100.0), 0.0, 4.0,
                                    120.0, 4, 4, True)

        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, logs=[], sent=SimpleNamespace(
            append=lambda message: sent.append((now[0], message))))
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = MovingClockEngine()
            surface.receive_midi((0xBF, 110, p.VERSION))
            for instant in (100.01, 100.1, 100.2, 100.26, 100.3):
                now[0] = instant
                song.current_song_time = 1.0 + 2.0 * (instant - 100.0)
                surface.update_display()

        global_frames = []
        for cursor, (transmitted_at, message) in enumerate(sent):
            if message[:2] != (0xBF, 34):
                continue
            frame = [entry[1] for entry in sent[cursor:cursor + 20]]
            wire.assert_frame(self, frame, 34)
            fields = wire.clock_fields(self, frame)
            position = (fields[76] | fields[77] << 7 | fields[78] << 14) / 64.0
            expected_position = 1.0 + 2.0 * (transmitted_at - 100.0)
            self.assertAlmostEqual(position, expected_position, delta=1.0 / 64,
                                   msg="clock phase must describe transmission time, not enqueue time")
            global_frames.append(transmitted_at)
        self.assertTrue(any(instant > 100.0 for instant in global_frames),
                        "the test must observe a clock after the initial constrained send window")

    def test_handshake_and_repeated_callbacks_share_one_bounded_90ms_send_window(self):
        now = [100.0]
        song = SimpleNamespace(tracks=[], return_tracks=[], is_playing=True,
                               current_song_time=1.0)
        instance = SimpleNamespace(song=song, sent=[], logs=[])
        with patch.object(config, "AUDIO_CONTROLLER_MANIFEST", None), \
                patch.object(module.time, "monotonic", side_effect=lambda: now[0]):
            surface = module.LightpadController(instance)
            logical_sent = wire.capture_logical_delivery(surface)
            self.addCleanup(surface.disconnect)
            surface._engine = EightClockEngine()
            surface.receive_midi((0xBF, 110, p.VERSION))
            for instant in (100.0, 100.01, 100.02, 100.05, 100.089):
                now[0] = instant
                surface.update_display()

        frames, metadata = [], []
        cursor = 0
        while cursor < len(instance.sent):
            status, cc, revision = instance.sent[cursor]
            if cc in (33, 34, 35):
                size = wire.size(cc)
                if cc == 35:
                    self.assertEqual(status, 0xBF)
                frame = instance.sent[cursor:cursor + size]
                self.assertEqual(len(frame), size, "a send window must end between full frames")
                self.assertEqual([item[0] for item in frame], [status] * size)
                self.assertEqual([item[1] for item in frame], wire.controllers(cc),
                                 "snapshot, clock and presentation frames must remain contiguous")
                self.assertEqual(frame[-1][2], revision)
                frames.append(frame)
                cursor += size
            else:
                self.assertEqual(status, 0xBF)
                self.assertIn(cc, (110, 111, 112, 113))
                metadata.append(instance.sent[cursor])
                cursor += 1
        self.assertGreater(len(frames), 0)
        self.assertLessEqual(len(frames), 3,
                             "Hello and repeated callbacks must share the same 90ms frame budget")
        # Three handshake messages and at most one periodic running/beat pair.
        self.assertLessEqual(len(metadata), 5)


if __name__ == "__main__":
    unittest.main()
