"""Bound actual callback work around performance writes; no hardware timing claim."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_surface  # Install the existing fake Live/ControlSurface bindings.
from live_test_support import Song, Track
from LightpadController import configuration as config, protocol as p, surface as module
from LightpadController.live_controller import LiveController
from LightpadController.touch_protocol import encode_touch

Q = {'q_bar': 4, 'q_half': 5, 'q_quarter': 7, 'q_eight': 9, 'q_sixtenth': 11}


class MixerCallbackWorkTests(unittest.TestCase):
    def make(self):
        self.now = 100.
        self.song = Song(groups=8, scenes=3)
        # Unrelated prepared sources must not be re-enumerated per mixer write.
        inputs = Track(self.song, '[Inputs]', foldable=True)
        self.song.tracks.append(inputs)
        self.song.tracks.extend(Track(self.song, 'Source %d' % i, inputs) for i in range(19))
        self.engine = LiveController(self.song, quantizations=Q)
        instance = NS(song=self.song, sent=[], logs=[], handle=lambda: 123)
        for context in (patch.object(config, 'LIVE_STATE_MODE', True),
                        patch.object(module.time, 'monotonic', side_effect=lambda: self.now),
                        patch.object(module.LightpadController, '_new_live_engine', return_value=self.engine)):
            context.start()
            self.addCleanup(context.stop)
        self.surface = module.LightpadController(instance)
        self.addCleanup(self.surface.disconnect)
        self.surface.receive_midi((191, 110, p.VERSION))
        for _ in range(10):
            self.now += .1
            self.surface.update_display()
        self.groups = [next(t for t in self.song.tracks if t.name.startswith('[R%d]' % (i + 1)))
                       for i in range(8)]

    def contact(self, kind, finger, seq, x, y, role=6, view=0, group=0):
        for message in encode_touch(kind, finger, x, y, seq, role, view=view, group=group):
            self.surface.receive_midi(message)

    def test_chord_writes_do_not_reconcile_between_group_mutations(self):
        self.make()
        self.contact(1, 0, 1, 1.5, 1.5)
        self.contact(1, 1, 1, 5.5, 1.5)
        self.contact(3, 0, 2, 1.5, 1.5)
        self.assertFalse(any(group.mute for group in self.groups))
        observed = []
        original = Track.__setattr__
        with patch.object(self.engine.state, 'refresh', wraps=self.engine.state.refresh) as refresh:
            def set_value(track, name, value):
                if name == 'mute':
                    observed.append((track, refresh.call_count))
                original(track, name, value)
            with patch.object(Track, '__setattr__', set_value):
                self.contact(3, 1, 2, 5.5, 1.5)
            self.assertEqual([group for group, _ in observed], self.groups[:2])
            self.assertEqual(observed[0][1], observed[1][1],
                             'result handling must not rescan the Set between mute writes')
            self.assertLessEqual(refresh.call_count, 1,
                                 'a completed contact shares one current graph observation')
        self.assertTrue(all(group.mute for group in self.groups[:2]))

    def test_fader_move_shares_one_graph_read_and_publishes_actual_value(self):
        self.make()
        self.contact(1, 0, 1, 1.5, 1.5)
        self.now += .6
        self.surface.update_display()
        self.assertEqual(self.surface._ui.view, 1)
        self.contact(1, 1, 1, 1., 10., role=0, view=1)
        before = self.groups[0].mixer_device.volume.value
        with patch.object(self.engine.state, 'refresh', wraps=self.engine.state.refresh) as refresh:
            self.contact(2, 1, 2, 1., 9., role=0, view=1)
            self.assertLessEqual(refresh.call_count, 1,
                                 'a slider packet must not repeatedly rescan the Set')
        self.assertNotEqual(self.groups[0].mixer_device.volume.value, before)
        self.assertEqual(self.surface._last[0][98],
                         round(self.groups[0].mixer_device.volume.value * 127))

    def test_manual_group_change_is_seen_on_the_next_contact(self):
        self.make()
        self.groups[0].name = 'No longer a remote group'
        self.contact(1, 0, 1, 1.5, 1.5)
        self.contact(3, 0, 2, 1.5, 1.5)
        self.assertFalse(self.groups[0].mute)
        self.assertEqual(self.surface._last[0][91], p.UNAVAILABLE)

    def test_recording_command_revalidates_after_mixer_write_in_same_batch(self):
        self.make()
        self.engine.begin_read_batch()
        try:
            self.assertEqual(self.engine.dispatch(p.Command(1, 0, p.SET_MUTE, 127)), p.APPLIED)
            self.groups[0].name = 'No longer a remote group'
            self.assertEqual(self.engine.dispatch(p.Command(2, 0, p.BEGIN, 0)), p.NOT_READY)
            self.assertFalse(any(call[0] == 'create_track' for call in self.song.calls))
        finally:
            self.engine.end_read_batch()


if __name__ == '__main__':
    unittest.main()
