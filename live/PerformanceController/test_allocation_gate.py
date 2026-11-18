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
        self.assertEqual(backend.allocation_ticket,ticket)
        self.assertNotIn((6,3,0),backend.drain_facts(300))
        # Further Native creations belong to this same preparation, not a new
        # permission handshake. An already-started ticket cannot be re-granted.
        self.assertEqual(backend.dispatch(58,ticket),REJECT)

    def test_one_busy_ticket_covers_two_source_allocation_routing_and_ready_bundle(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        facts=backend.drain_facts(300)
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        self.step(fixture)
        self.assertEqual(self.creates(fixture),1)
        self.assertEqual(backend.allocation_ticket,ticket,
                         'first creation must not unlock while routing and the second lane are missing')
        self.assertNotIn(0,backend.reserves)
        for unused in range(80):
            facts+=backend.drain_facts(300)
            if 0 in backend.reserves:break
            self.assertEqual(backend.allocation_ticket,ticket,
                             'one granted ticket owns all preparation callbacks')
            self.step(fixture)
        facts+=backend.drain_facts(300)
        self.assertIn(0,backend.reserves)
        reserve=backend.reserves[0]
        self.assertEqual(len(reserve.members),2)
        self.assertTrue(backend.audio_inputs.validate(reserve,0,reserve.row))
        self.assertEqual(self.creates(fixture),2)
        self.assertIsNone(backend.allocation_ticket)
        self.assertEqual({b for kind,a,b in facts if kind==6 and a==3 and b}, {ticket})
        self.assertEqual(sum(f==(6,3,0) for f in facts),1)

    def test_started_bundle_keeps_busy_and_heartbeats_beyond_initial_grant_timeout(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        facts=backend.drain_facts(300)
        started=fixture.clock
        first_seen={};prepare=backend.audio_inputs._prepare_audio
        def delayed_target(*args,**kwargs):
            track=args[1]
            first_seen.setdefault(id(track),fixture.clock)
            # Model a genuinely unsettled Native target, not a fixed number
            # of implementation steps that faster batching can remove.
            if fixture.clock-first_seen[id(track)]<4.5:return False
            return prepare(*args,**kwargs)
        with patch.object(backend.audio_inputs,'_prepare_audio',side_effect=delayed_target):
            for unused in range(30):
                self.step(fixture,1.5)
                facts+=backend.drain_facts(300)
                if 0 in backend.reserves:break
                self.assertEqual(backend.allocation_ticket,ticket)
        self.assertGreater(fixture.clock-started,10)
        self.assertEqual(self.creates(fixture),2)
        self.assertIn(0,backend.reserves)
        self.assertIsNone(backend.allocation_ticket)
        self.assertGreater(sum(f==(6,3,ticket) for f in facts),5)
        self.assertEqual(sum(f==(6,3,0) for f in facts),1)

    def test_existing_stock_preparation_never_locks_the_device(self):
        fixture=audio_fixtures.AudioInputChecks();self.addCleanup(fixture.doCleanups)
        backend=fixture.make(initial=3,groups=1,lanes=4)
        fixture.ticks(80);backend.drain_facts(300)
        self.assertEqual(backend.dispatch(64,1),0)
        take=backend.recording.take;fixture.song.advance(take.target+.001)
        facts=[]
        for unused in range(30):
            self.step(fixture);facts+=backend.drain_facts(300)
            self.assertIsNone(backend.allocation_ticket)
        self.assertIn(0,backend.reserves)
        self.assertEqual(self.creates(fixture),0)
        self.assertFalse(any(kind==6 and a==3 and b for kind,a,b in facts))

    def test_reseed_mid_overflow_routing_reacquires_busy_before_further_native_work(self):
        fixture,backend=self.audio();old=backend.allocation_ticket
        self.assertEqual(backend.dispatch(58,old),old)
        self.step(fixture);self.assertEqual(self.creates(fixture),1)
        job=backend.audio_inputs.jobs[0]
        self.assertIsNotNone(job['track'])
        backend.reseed();self.assertIsNone(backend.allocation_ticket)
        calls=list(fixture.song.calls);stage=job['stage']
        for unused in range(5):self.step(fixture)
        fresh=backend.allocation_ticket
        self.assertIsNotNone(fresh);self.assertNotEqual(fresh,old)
        self.assertEqual(fixture.song.calls,calls,
                         'reconnected peer must paint Busy before retained routing work resumes')
        self.assertEqual(job['stage'],stage)
        self.assertEqual(backend.dispatch(58,old),REJECT)
        self.assertEqual(backend.dispatch(58,fresh),fresh)
        for unused in range(50):
            self.step(fixture)
            if 0 in backend.reserves:break
            self.assertEqual(backend.allocation_ticket,fresh)
        self.assertIn(0,backend.reserves)
        self.assertEqual(self.creates(fixture),2)
        self.assertIsNone(backend.allocation_ticket)

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

    def test_later_bundle_creation_failure_clears_once_and_never_replays(self):
        fixture,backend=self.audio();ticket=backend.allocation_ticket
        self.assertEqual(backend.dispatch(58,ticket),ticket)
        self.step(fixture);self.assertEqual(self.creates(fixture),1)
        self.assertEqual(backend.allocation_ticket,ticket)
        fixture.song.create_error=True
        for unused in range(40):
            self.step(fixture)
            if 0 in backend.failures:break
            self.assertEqual(backend.allocation_ticket,ticket)
        self.assertIn(0,backend.failures)
        self.assertEqual(self.creates(fixture),2)
        self.assertIsNone(backend.allocation_ticket)
        self.assertNotIn(0,backend.reserves)
        for unused in range(40):self.step(fixture)
        self.assertEqual(self.creates(fixture),2,
                         'the failed second allocation must not replay under the shared grant')

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
        self.assertEqual(backend.allocation_ticket,ticket)
        for unused in range(30):
            if 0 in backend.reserves:break
            self.assertEqual(backend.allocation_ticket,ticket)
            self.step(fixture)
        self.assertIn(0,backend.reserves)
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
