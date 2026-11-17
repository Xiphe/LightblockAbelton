"""Set-local metadata policy, using fresh wrappers and saved object data."""
from pathlib import Path
import copy
import itertools
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PerformanceController.audio_patch_state import AudioPatchState, SOURCE_KEY, PATCH_KEY


class Track:
    def __init__(self, identity, saved=None, calls=None):
        self.identity = identity
        self.saved = {} if saved is None else saved
        self.calls = [] if calls is None else calls
        self.fail = None

    def __eq__(self, other):
        return isinstance(other, Track) and self.identity == other.identity

    def get_data(self, key, default):
        self.calls.append(('get', self.identity, key))
        return self.saved.get(key, default)

    def set_data(self, key, value):
        self.calls.append(('set', self.identity, key, value))
        if self.fail == 'before':
            raise RuntimeError('disconnected')
        self.saved[key] = value
        if self.fail == 'after':
            raise RuntimeError('uncertain completion')


def id_factory():
    counter = itertools.count(1)
    return lambda: '00000000-0000-0000-0000-%012d' % next(counter)


class PatchStateChecks(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.root = Track('inputs', calls=self.calls)
        self.sources = [Track('source-a', calls=self.calls), Track('source-b', calls=self.calls)]
        self.groups = {0: Track('group-a', calls=self.calls), 1: Track('group-b', calls=self.calls)}
        self.state = AudioPatchState(id_factory())
        self.state.bind(self.root, self.sources, self.groups)

    def flush_all(self):
        for unused in range(10):
            if not self.state.pending:
                return
            self.state.flush()
        self.fail('metadata did not settle')

    def reopen(self, sources=None):
        root = Track('new-inputs', copy.deepcopy(self.root.saved), self.calls)
        originals = self.sources if sources is None else sources
        new_sources = [Track('new-' + t.identity, copy.deepcopy(t.saved), self.calls) for t in originals]
        groups = {g: Track('new-' + t.identity, copy.deepcopy(t.saved), self.calls) for g, t in self.groups.items()}
        state = AudioPatchState(id_factory())
        state.bind(root, new_sources, groups)
        return state

    def test_fresh_state_has_no_implicit_source_or_monitor(self):
        self.assertEqual(self.state.mask(0), 0)
        self.assertIsNone(self.state.monitor_owner)
        self.assertEqual(self.state.pending, 2)
        self.assertFalse(any(c[0] == 'set' for c in self.calls))

    def test_dispatch_is_cache_only_and_last_intent_wins(self):
        self.flush_all()
        self.calls.clear()
        for mask in (3, 1, 0, 2):
            self.state.set_patch(0, mask)
        self.state.set_monitor(0)
        self.state.set_monitor(1)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state.mask(0), 2)
        self.assertEqual(self.state.monitor_owner, 1)
        result = self.state.flush()
        self.assertEqual((result.writes, result.cleared, result.errors), (2, 2, ()))
        self.assertEqual(len([c for c in self.calls if c[0] == 'set']), 2)

    def test_saved_selection_and_owner_restore_with_new_native_objects(self):
        self.state.set_patch(0, 3)
        self.state.set_patch(1, 2)
        self.state.set_monitor(1)
        self.flush_all()
        reopened = self.reopen()
        self.assertEqual((reopened.mask(0), reopened.mask(1), reopened.monitor_owner), (3, 2, 1))
        self.assertEqual(reopened.pending, 0)

    def test_catalog_reorder_follows_ids_not_slot_or_name(self):
        self.state.set_patch(0, 1)
        self.flush_all()
        reopened = self.reopen(list(reversed(self.sources)))
        self.assertEqual(reopened.mask(0), 2)
        self.assertEqual(reopened.unresolved(0), ())

    def test_missing_source_id_is_retained_until_explicit_replacement(self):
        self.state.set_patch(0, 3)
        self.flush_all()
        reopened = self.reopen(self.sources[:1])
        self.assertEqual(reopened.mask(0), 1)
        self.assertEqual(reopened.unresolved(0), (self.state.source_ids[1],))
        reopened.flush()
        self.assertEqual(len(reopened.selected_ids(0)), 2)
        reopened.set_patch(0, 0)
        self.assertEqual(reopened.unresolved(0), ())
        self.assertEqual(reopened.selected_ids(0), ())

    def test_duplicate_existing_ids_disable_both_without_reassignment(self):
        self.state.set_patch(0, 1)
        self.flush_all()
        self.sources[1].saved[SOURCE_KEY] = self.sources[0].saved[SOURCE_KEY]
        reopened = self.reopen()
        self.assertEqual(reopened.duplicate_indices, {0, 1})
        self.assertEqual(reopened.mask(0), 0)
        self.assertEqual(len(reopened.unresolved(0)), 1)
        self.calls.clear()
        self.assertEqual(reopened.flush().writes, 0)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            reopened.set_patch(0, 1)

    def test_pending_intent_survives_rebind_to_equal_fresh_wrappers(self):
        ids = self.state.source_ids
        self.state.set_patch(0, 2)
        self.state.set_monitor(1)
        root = Track('inputs', self.root.saved, self.calls)
        sources = [Track(t.identity, t.saved, self.calls) for t in self.sources]
        groups = {g: Track(t.identity, t.saved, self.calls) for g, t in self.groups.items()}
        self.state.bind(root, sources, groups)
        self.assertEqual(self.state.source_ids, ids)
        self.assertEqual((self.state.mask(0), self.state.monitor_owner), (2, 1))
        self.flush_all()
        self.assertEqual(self.reopen().mask(0), 2)

    def test_replaced_group_does_not_inherit_pending_old_group_selection(self):
        self.state.set_patch(0, 3)
        self.groups[0] = Track('replacement', calls=self.calls)
        self.state.bind(self.root, self.sources, self.groups)
        self.assertEqual(self.state.mask(0), 0)

    def test_unchanged_cached_values_do_not_read_or_write_native_storage(self):
        self.state.set_patch(0, 1)
        self.state.set_monitor(0)
        self.flush_all()
        self.calls.clear()
        self.state.set_patch(0, 1)
        self.state.set_monitor(0)
        self.assertEqual(self.state.flush().writes, 0)
        self.assertEqual(self.calls, [])

    def test_budget_does_not_save_selection_before_new_source_ids(self):
        self.state.set_patch(0, 3)
        self.state.set_monitor(0)
        for unused in range(4):
            self.assertLessEqual(self.state.flush(1).writes, 1)
        self.assertFalse(self.state.pending)
        writes = [c for c in self.calls if c[0] == 'set']
        patch_index = next(i for i, c in enumerate(writes) if c[2] == PATCH_KEY)
        self.assertTrue(all(c[2] == SOURCE_KEY for c in writes[:2]))
        self.assertGreaterEqual(patch_index, 2)

    def test_uncertain_completed_write_is_read_back_without_repeating(self):
        self.sources[0].fail = 'after'
        result = self.state.flush(1)
        self.assertEqual(result.writes, 1)
        self.assertEqual(len(result.errors), 1)
        self.sources[0].fail = None
        self.flush_all()
        self.assertEqual(len([c for c in self.calls if c[:3] == ('set', 'source-a', SOURCE_KEY)]), 1)

    def test_failed_write_blocks_retries_until_explicit_retry(self):
        self.sources[0].fail = 'before'
        self.state.flush()
        self.state.flush()
        self.calls.clear()
        for unused in range(3):
            self.state.flush()
        self.assertEqual(self.calls, [])
        self.sources[0].fail = None
        self.state.retry()
        self.flush_all()
        self.assertEqual(self.sources[0].saved[SOURCE_KEY], self.state.source_ids[0])

    def test_invalid_metadata_does_not_select_an_unrelated_source(self):
        self.flush_all()
        self.groups[0].saved[PATCH_KEY] = '{"schema":99,"sources":[]}'
        self.sources[0].saved[SOURCE_KEY] = 'not-a-uuid'
        reopened = self.reopen()
        self.assertIsNone(reopened.selected_ids(0))
        self.assertEqual(reopened.invalid_indices, {0})
        self.assertEqual(reopened.mask(0), 0)
        self.assertIn('group:0', reopened.issues)


if __name__ == '__main__':
    unittest.main()
