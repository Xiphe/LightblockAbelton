"""Real backend preparation waits for a prior, generation-bound Busy grant."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_audio_inputs as audio_fixtures
import test_backend as legacy_fixtures
from PerformanceController.backend import REJECT
from PerformanceController.allocation_gate import AllocationGate, AllocationContext


class AllocationChecks(unittest.TestCase):
    def audio(self):
        fixture=audio_fixtures.AudioInputChecks()
        self.addCleanup(fixture.doCleanups)
        backend=fixture.make(initial=3,groups=1,lanes=2)
        fixture.ticks(80)
        self.assertEqual(backend.dispatch(64,1),0)
        take=backend.recording.take
        fixture.song.advance(take.target+.001)
        self.step(fixture)
        self.assertEqual(take.state,'recording')
        self.assertIsNotNone(backend.allocation_ticket)
        self.assertFalse(self.creates(fixture))
        return fixture,backend

    @staticmethod
    def creates(fixture):return sum(c[0]=='create_track' for c in fixture.song.calls)

    @staticmethod
    def step(fixture,seconds=.101):
        fixture.clock+=seconds
        fixture.backend.tick()

    def test_busy_is_published_before_any_create_and_matching_grant_yields_one_callback(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        self.assertIn((6,3,ticket),backend.drain_facts(300))
        before=list(fixture.song.calls);fixture.song.track_reads=0
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        self.assertEqual(fixture.song.track_reads,0,'grant must be Python bookkeeping only')
        self.assertEqual(fixture.song.calls,before)
        backend._maintenance(0)
        self.assertEqual(self.creates(fixture),0,'even a granted request cannot create in its announcing callback')
        self.step(fixture)
        self.assertEqual(self.creates(fixture),1)
        self.assertIsNone(backend.allocation_ticket)
        self.assertIn((6,3,0),backend.drain_facts(300))
        self.assertEqual(backend.dispatch(58,ticket),REJECT)

    def test_missing_or_wrong_grant_never_becomes_a_native_timeout_fallback(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        self.assertEqual(backend.dispatch(58,ticket+1),REJECT)
        for unused in range(30):self.step(fixture)
        self.assertEqual(backend.allocation_ticket,ticket)
        self.assertFalse(backend.allocation_granted)
        self.assertEqual(self.creates(fixture),0)
        self.assertNotIn(0,backend.reserves)

    def test_patch_context_and_reseed_reject_old_grants(self):
        fixture,backend=self.audio();old=backend.allocation_ticket
        self.assertEqual(backend.dispatch(48,2),2)
        self.assertIsNone(backend.allocation_ticket)
        self.assertEqual(backend.dispatch(58,old),REJECT)
        self.step(fixture);self.assertIsNone(backend.allocation_ticket)
        self.step(fixture);fresh=backend.allocation_ticket
        self.assertIsNotNone(fresh);self.assertNotEqual(fresh,old)
        backend.reseed()
        self.assertIsNone(backend.allocation_ticket)
        self.assertEqual(backend.dispatch(58,fresh),REJECT)
        self.assertIn((6,3,0),backend.drain_facts(300))
        self.assertEqual(self.creates(fixture),0)

    def test_expired_ticket_clears_before_a_new_announcement_and_never_creates(self):
        fixture,backend=self.audio();old=backend.allocation_ticket
        backend.drain_facts(300)
        self.step(fixture,10.01)
        self.assertIsNone(backend.allocation_ticket)
        self.assertIn((6,3,0),backend.drain_facts(300))
        self.assertEqual(self.creates(fixture),0)
        self.assertEqual(backend.dispatch(58,old),REJECT)
        self.step(fixture);self.assertIsNone(backend.allocation_ticket)
        self.step(fixture);fresh=backend.allocation_ticket
        self.assertIsNotNone(fresh);self.assertNotEqual(fresh,old)

    def test_create_error_clears_busy_and_does_not_replay_the_uncertain_allocation(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        fixture.song.create_error=True
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        self.step(fixture)
        self.assertEqual(self.creates(fixture),1)
        self.assertIsNone(backend.allocation_ticket)
        self.assertIn((6,3,0),backend.drain_facts(300))
        self.assertIn(0,backend.failures)
        for unused in range(20):self.step(fixture)
        self.assertEqual(self.creates(fixture),1)

    def test_invalid_native_context_clears_and_rediscovers_without_creating(self):
        class ArgumentError(TypeError):pass
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        with patch.object(backend,'_allocation_context',side_effect=ArgumentError('deleted Native handle')):
            backend._maintenance(0)
        self.assertIsNone(backend.allocation_ticket)
        self.assertTrue(backend.state.topology_dirty)
        self.assertEqual(self.creates(fixture),0)
        self.assertIn((6,3,0),backend.drain_facts(300))

    def test_python_type_error_in_context_is_not_masked_as_a_native_deletion(self):
        fixture,backend=self.audio()
        with patch.object(backend,'_allocation_context',side_effect=TypeError('Python bug')):
            with self.assertRaisesRegex(TypeError,'Python bug'):backend._maintenance(0)
        self.assertEqual(self.creates(fixture),0)

    def test_close_clears_pending_request_without_creating(self):
        fixture,backend=self.audio()
        backend.close()
        self.assertIsNone(backend.allocation_ticket)
        self.assertIn((6,3,0),backend.drain_facts(300))
        self.assertEqual(self.creates(fixture),0)

    def test_legacy_allocation_uses_the_same_preannouncement_gate(self):
        fixture=legacy_fixtures.BackendChecks();self.addCleanup(fixture.doCleanups)
        backend=fixture.make(groups=1);fixture.warm()
        self.assertEqual(backend.dispatch(64,1),0);take=backend.recording.take
        fixture.song.advance(take.target+.001);self.step(fixture)
        ticket=backend.allocation_ticket;self.assertIsNotNone(ticket)
        self.assertEqual(self.creates(fixture),0)
        self.assertEqual(backend.dispatch(58,ticket),ticket);self.step(fixture)
        self.assertEqual(self.creates(fixture),1)
        self.assertIsNone(backend.allocation_ticket)


class TicketLifecycleChecks(unittest.TestCase):
    def make(self):
        self.now=0.;self.facts=[]
        self.gate=AllocationGate(lambda:self.now,lambda *a:self.facts.append(a))
        return AllocationContext(0,object(),object(),0,1)

    def test_other_group_cannot_replace_announced_plan(self):
        context=self.make();self.gate.begin_callback()
        with self.gate.operation(context) as allowed:self.assertFalse(allowed)
        ticket=self.gate.ticket
        with self.gate.operation(AllocationContext(1,object(),object(),0,1)) as allowed:self.assertFalse(allowed)
        self.assertEqual(self.gate.ticket,ticket)

    def test_context_change_or_parent_replacement_invalidates_permission(self):
        context=self.make();self.gate.begin_callback()
        with self.gate.operation(context):pass
        old=self.gate.ticket;self.assertTrue(self.gate.grant(old))
        self.gate.validate(AllocationContext(0,object(),context.anchor,0,1))
        self.assertIsNone(self.gate.ticket);self.assertFalse(self.gate.grant(old))

    def test_grant_expires_before_create_when_a_native_boundary_holds_it(self):
        context=self.make();self.gate.begin_callback()
        with self.gate.operation(context):pass
        self.assertTrue(self.gate.grant(self.gate.ticket))
        self.now=10.01;self.gate.begin_callback()
        with self.gate.operation(context) as allowed:self.assertFalse(allowed)
        self.assertIsNone(self.gate.ticket)
        self.assertEqual(self.facts[-1],(6,3,0,True))

    def test_operation_exception_always_clears_lease(self):
        context=self.make();self.gate.begin_callback()
        with self.gate.operation(context):pass
        self.gate.grant(self.gate.ticket);self.gate.begin_callback()
        with self.assertRaisesRegex(RuntimeError,'Native'):
            with self.gate.operation(context) as allowed:
                self.assertTrue(allowed);raise RuntimeError('Native failed')
        self.assertIsNone(self.gate.ticket)
        self.assertEqual(self.facts[-1],(6,3,0,True))


if __name__=='__main__':unittest.main()
