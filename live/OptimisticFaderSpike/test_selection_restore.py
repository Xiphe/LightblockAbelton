import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

spec = importlib.util.spec_from_file_location('selection_restore', Path(__file__).with_name('selection_restore.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Track:
    def __init__(self, name):
        self.name = name
        self.clip_slots = [NS(name='%s %d' % (name, i)) for i in range(3)]


class View:
    def __init__(self, track, scene, slot):
        self._selected_track, self._selected_scene, self._highlighted_clip_slot = track, scene, slot
        self.listeners = {'selected_track': [], 'selected_scene': []}
        self.writes = []

    def _get(attr):
        return property(lambda self: getattr(self, '_' + attr),
                        lambda self, value: self._set(attr, value))
    selected_track = _get('selected_track')
    selected_scene = _get('selected_scene')
    highlighted_clip_slot = _get('highlighted_clip_slot')

    def _set(self, attr, value):
        self.writes.append((attr, value))
        setattr(self, '_' + attr, value)
        for callback in tuple(self.listeners.get(attr, ())):
            callback()

    def add_selected_track_listener(self, cb): self.listeners['selected_track'].append(cb)
    def remove_selected_track_listener(self, cb): self.listeners['selected_track'].remove(cb)
    def add_selected_scene_listener(self, cb): self.listeners['selected_scene'].append(cb)
    def remove_selected_scene_listener(self, cb): self.listeners['selected_scene'].remove(cb)


class RestorationTests(unittest.TestCase):
    def setUp(self):
        self.original, self.other, self.created = Track('Original'), Track('Other'), Track('Created')
        self.scenes = [NS(name=str(i)) for i in range(3)]
        self.view = View(self.original, self.scenes[1], self.original.clip_slots[1])
        self.song = NS(tracks=[self.original, self.other], scenes=self.scenes,
                       view=self.view, file_path='test.als', return_tracks=[], master_track=NS())
        self.now = 0.
        self.restore = module.SelectionRestore(self.song, now=lambda: self.now)
        self.song.tracks.append(self.created)
        self.restore.capture_created([self.created])
        self.addCleanup(self.restore.close)

    def native_select(self):
        self.view.selected_track = self.created
        self.view.highlighted_clip_slot = self.created.clip_slots[1]

    def test_deferred_selection_then_one_restoration(self):
        self.assertEqual(self.restore.tick(), 'waiting')
        self.now = .1
        self.native_select()
        self.assertEqual(self.restore.tick(), 'checking')
        self.assertIs(self.view.selected_track, self.original)
        self.assertIs(self.view.selected_scene, self.scenes[1])
        self.assertIs(self.view.highlighted_clip_slot, self.original.clip_slots[1])
        self.now = .7
        self.assertEqual(self.restore.tick(), 'restored')
        before = list(self.view.writes)
        self.restore.tick()
        self.assertEqual(self.view.writes, before)
        self.assertEqual(self.restore.attempts, 1)
        self.assertEqual(self.restore.result()['listener_count'], 0)

    def test_unchanged_immediate_selection_does_not_finish_early(self):
        self.assertEqual(self.restore.tick(), 'waiting')
        self.now = .2
        self.assertEqual(self.restore.tick(), 'waiting')
        self.now = .6
        self.assertEqual(self.restore.tick(), 'unchanged')
        self.assertEqual(self.restore.attempts, 0)

    def test_unrelated_manual_track_is_not_overwritten(self):
        self.native_select()
        self.view.selected_track = self.other
        self.view.highlighted_clip_slot = self.other.clip_slots[1]
        self.assertEqual(self.restore.tick(), 'superseded')
        self.assertIs(self.view.selected_track, self.other)
        self.assertEqual(self.restore.attempts, 0)

    def test_unrelated_manual_track_then_back_still_supersedes(self):
        self.view.selected_track = self.other
        self.native_select()
        self.assertEqual(self.restore.tick(), 'superseded')
        self.assertIs(self.view.selected_track, self.created)

    def test_slot_only_manual_selection_is_not_overwritten(self):
        self.native_select()
        self.view.highlighted_clip_slot = self.created.clip_slots[2]
        self.assertEqual(self.restore.tick(), 'superseded')
        self.assertIs(self.view.highlighted_clip_slot, self.created.clip_slots[2])
        self.assertEqual(self.restore.attempts, 0)

    def test_scene_selection_is_not_overwritten(self):
        self.native_select()
        self.view.selected_scene = self.scenes[2]
        self.assertEqual(self.restore.tick(), 'superseded')
        self.assertIs(self.view.selected_scene, self.scenes[2])

    def test_manual_change_after_restoration_is_not_corrected(self):
        self.native_select()
        self.restore.tick()
        self.view.selected_track = self.other
        self.assertEqual(self.restore.tick(), 'superseded')
        self.assertIs(self.view.selected_track, self.other)
        self.assertEqual(self.restore.attempts, 1)

    def test_removed_snapshot_target_fails_without_writes(self):
        self.native_select()
        self.song.tracks.remove(self.original)
        self.assertEqual(self.restore.tick(), 'failed')
        self.assertEqual(self.restore.attempts, 0)

    def test_set_change_fails_without_writes(self):
        self.native_select()
        self.song.file_path = 'another.als'
        self.assertEqual(self.restore.tick(), 'failed')
        self.assertEqual(self.restore.attempts, 0)

    def test_second_capture_cannot_restart(self):
        with self.assertRaises(ValueError):
            self.restore.capture_created([self.created])
        self.assertEqual(self.restore.attempts, 0)

    def test_native_exception_during_restore_never_retries(self):
        self.native_select()
        original = self.view._set
        def broken(attr, value):
            original(attr, value)
            if attr == 'selected_track':
                raise RuntimeError('after a possible Native effect')
        self.view._set = broken
        self.assertEqual(self.restore.tick(), 'failed')
        writes = list(self.view.writes)
        self.restore.tick()
        self.assertEqual(self.view.writes, writes)
        self.assertEqual(self.restore.attempts, 1)

    def test_own_track_write_can_emit_scene_and_slot_side_effects(self):
        self.native_select()
        original = self.view._set
        def native_effects(attr, value):
            original(attr, value)
            if attr == 'selected_track' and value is self.original:
                original('selected_scene', self.scenes[0])
                original('highlighted_clip_slot', self.original.clip_slots[0])
        self.view._set = native_effects
        self.restore.tick()
        self.assertIs(self.view.selected_scene, self.scenes[1])
        self.assertIs(self.view.highlighted_clip_slot, self.original.clip_slots[1])
        self.now = .6
        self.assertEqual(self.restore.tick(), 'restored')

    def _none_slot_target(self, master):
        self.restore.close()
        target = NS(name='Main' if master else 'Return', clip_slots=[])
        if master:
            self.song.master_track = target
        else:
            self.song.return_tracks.append(target)
        self.view.selected_track = target
        self.view.highlighted_clip_slot = None
        self.song.tracks.remove(self.created)
        self.restore = module.SelectionRestore(self.song, now=lambda: self.now)
        self.song.tracks.append(self.created)
        self.restore.capture_created([self.created])
        self.native_select()
        self.restore.tick()
        self.assertIs(self.view.selected_track, target)
        self.assertIsNone(self.view.highlighted_clip_slot)
        self.now = .6
        self.assertEqual(self.restore.tick(), 'restored')

    def test_main_track_snapshot_with_none_slot(self):
        self._none_slot_target(master=True)

    def test_return_track_snapshot_with_none_slot(self):
        self._none_slot_target(master=False)


if __name__ == '__main__': unittest.main()
