"""What a recovery pass owes the runs behind it, and what a failed write means.

Covers ROADMAP.md CH9 (per-run isolation in `recover()`, a bounded number of
automatic attempts, and `failed` staying out of the recovery pool) and CH10 (a
write whose outcome we did not learn is settled by reading the provider back).

Two promises from ROADMAP §3:

  * "A deployment that cannot complete fails by itself and does not hold up the
    deployments behind it." -- G6 (c9). `recover()` is a bare loop (F15), so the
    first raise ends the pass. That is the operator's "a stuck run seems to take
    the whole queue down with it -- two campaigns behind it never went out".
  * "A deployment reported as complete has just been read back from HubSpot."
    -- G7 (c10). A write that raised having stored the object is a success; a
    write that raised having stored nothing is not. The exception looks the same
    either way, so it cannot be what decides.

The three CH9 tests are one argument in three parts: isolation without a bound
turns a head-of-line stall into a spin, and a bound that `recover()` then
reclaims bounds nothing (§4.14, M18b).

Failure is injected with wrappers around `FakeHubSpot`; the provider is never
modified, subclassed, or monkeypatched (TASK.md). No schema column is named
except CH4's lease, and only to make it lapse -- if CH4 has not landed the
helper no-ops rather than dictating a design. Boundedness is asserted by
counting writes, not by reading an `attempt` counter. Every expectation derives
from the approved payload at runtime, and the only key handling is
`deployment_namespace`, used to aim a fault at exactly one deployment.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from relay import InjectedCrash, Relay, deployment_namespace

FIXTURE = Path("fixtures/deployment_request.json")

# Recovery passes driven at a run that can never succeed. Any value above the
# bound works; the assertion is that attempts stop, not where they stop.
PASSES = 8

# CH4's lease. Named only to make it lapse -- see `expire_every_lease`.
LEASE_COLUMN = "lease_expires_at"


def approved_request() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


class ProviderRefused(RuntimeError):
    """The write was rejected and nothing was stored."""


class ProviderTimedOut(RuntimeError):
    """The write reached the provider; the answer did not reach us.

    c10's shape: `gateway_timeout, retryable: true`, and a readback afterwards
    finds the object.
    """


class ProviderWrapper:
    """Delegates to the provider the starter shipped, and changes nothing."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class RefusesOneDeployment(ProviderWrapper):
    """Every write for one deployment raises, and nothing is stored.

    Aimed by namespace so the other runs are served normally, which is what
    makes the isolation claim testable. The failure is permanent on purpose: a
    transient one could not show that attempts are bounded.
    """

    def __init__(self, provider: Any, idempotency_key: str) -> None:
        super().__init__(provider)
        self._namespace = deployment_namespace(idempotency_key)
        self.attempted: list[str] = []

    def create_draft(self, *, external_key: str, asset: dict) -> dict:
        if external_key.startswith(self._namespace):
            self.attempted.append(external_key)
            raise ProviderRefused(f"write refused for {external_key!r}")
        return self._provider.create_draft(
            external_key=external_key,
            asset=asset,
        )


class PersistsThenRaises(ProviderWrapper):
    """The write lands, and then the call fails. Every time, so nothing passes
    by getting one lucky answer."""

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self.persisted: list[str] = []

    def create_draft(self, *, external_key: str, asset: dict) -> dict:
        self._provider.create_draft(external_key=external_key, asset=asset)
        self.persisted.append(external_key)
        raise ProviderTimedOut(f"no answer for {external_key!r}")


class RaisesWithoutPersisting(ProviderWrapper):
    """The write never lands and the call fails the same way -- identical to
    the caller, opposite in HubSpot."""

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self.attempted: list[str] = []

    def create_draft(self, *, external_key: str, asset: dict) -> dict:
        self.attempted.append(external_key)
        raise ProviderRefused(f"write refused for {external_key!r}")


class RelayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.db_path = root / "deployments.db"
        self.provider_path = root / "fake-hubspot.json"
        self.relay = Relay(self.db_path, self.provider_path)
        self.payload = approved_request()
        self.assets = self.payload["assets"]
        self.assertGreater(
            len(self.assets),
            1,
            "precondition: these tests need a run that can stop part-way",
        )

    def another_worker(self) -> Relay:
        """A separate worker process against the same database and provider."""
        return Relay(self.db_path, self.provider_path)

    def held_for(self, idempotency_key: str) -> list[dict[str, str]]:
        """What HubSpot holds under one deployment's namespace."""
        namespace = deployment_namespace(idempotency_key)
        return [
            stored
            for key, stored in json.loads(self.provider_path.read_text()).items()
            if key.startswith(namespace)
        ]

    def status_of(self, run_id: str) -> str:
        return str(self.relay.get(run_id)["status"])

    def certification_of(self, run_id: str) -> str:
        report = self.relay.deployment_summary(run_id)
        if "certification" not in report:
            raise AssertionError(
                "deployment_summary() reports no 'certification' (CH7): there"
                " is no way to ask whether HubSpot holds what was approved"
            )
        return str(report["certification"])

    def a_run_left_running(self, idempotency_key: str) -> str:
        """Submit a deployment and leave it mid-flight, as a crash would.

        This is the state `recover()` exists for: the worker stopped after a
        confirmed provider write and before the local record caught up.
        """
        run_id = self.relay.submit(idempotency_key, self.payload)
        with self.assertRaises(InjectedCrash):
            self.relay.run_once(run_id, crash_at="after_first_provider_write")
        self.assertEqual(
            self.status_of(run_id),
            "running",
            "precondition: an interrupted run is left for recovery to find",
        )
        return run_id

    def expire_every_lease(self) -> None:
        """Make any lease look lapsed, without waiting on a clock.

        §4.14 has `recover()` select `running` runs whose lease expired. If CH4
        has not landed there is no lease and recovery selects on status alone --
        either way these runs are in the pool, which is all this needs. So it is
        best-effort, and does not decide whether a lease is an epoch or a
        timestamp.
        """
        connection = sqlite3.connect(self.db_path)
        with contextlib.closing(connection):
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(deployments)")
            }
            if LEASE_COLUMN not in columns:
                return
            row = connection.execute(
                f"SELECT {LEASE_COLUMN} FROM deployments"
                f" WHERE {LEASE_COLUMN} IS NOT NULL LIMIT 1"
            ).fetchone()
            expired: Any = 0
            if row is not None and not isinstance(row[0], (int, float)):
                expired = "1970-01-01T00:00:00+00:00"
            connection.execute(
                f"UPDATE deployments SET {LEASE_COLUMN} = ?", (expired,)
            )
            connection.commit()

    def recovery_pass(self, worker: Relay) -> None:
        """One sweep, which must survive whatever it finds.

        Reported here rather than allowed to escape, because the defect is that
        the runs behind this one were abandoned, not that something raised.
        """
        try:
            worker.recover()
        except BaseException as error:  # noqa: BLE001 - reported, not hidden
            self.fail(
                "a recovery pass must not be ended by one broken run"
                f" ({type(error).__name__}: {error}). Every run it picked up"
                " after this one was abandoned mid-queue (CH9, G6, M18)"
            )

    def an_exhausted_run(
        self,
        idempotency_key: str,
    ) -> tuple[str, Relay, RefusesOneDeployment]:
        """Drive one deployment past whatever the automatic path allows it."""
        run_id = self.a_run_left_running(idempotency_key)
        worker = self.another_worker()
        breaker = RefusesOneDeployment(worker.provider, idempotency_key)
        worker.provider = breaker
        for _ in range(PASSES):
            self.expire_every_lease()
            self.recovery_pass(worker)
        return run_id, worker, breaker


class RecoveryIsolationTest(RelayTestCase):
    """CH9 -- one bad run is one bad run."""

    def test_a_broken_run_does_not_strand_the_deployments_behind_it(
        self,
    ) -> None:
        """The operator's "two campaigns behind it never went out" (c9).

        A good run is queued on each side of the broken one, so the assertion
        does not depend on the order `recover()` visits rows in: whichever way
        it sweeps, a good run sits behind the failure.
        """
        broken_key = "campaign-broken"
        first_good = self.a_run_left_running("campaign-before")
        broken = self.a_run_left_running(broken_key)
        second_good = self.a_run_left_running("campaign-after")
        self.expire_every_lease()

        worker = self.another_worker()
        breaker = RefusesOneDeployment(worker.provider, broken_key)
        worker.provider = breaker

        self.recovery_pass(worker)

        self.assertTrue(
            breaker.attempted,
            "precondition: the broken run really was picked up and did fail",
        )
        for name, run_id in (("before", first_good), ("after", second_good)):
            with self.subTest(queued=name):
                self.assertEqual(
                    self.status_of(run_id),
                    "done",
                    "an unrelated failure must not decide whether an approved"
                    " campaign is deployed",
                )
                self.assertEqual(
                    self.certification_of(run_id),
                    "complete",
                    "the recovered run must be provably in HubSpot, not just"
                    " marked finished",
                )
        self.assertNotEqual(
            self.status_of(broken),
            "done",
            "the run that could not be written must not be reported finished",
        )

    def test_a_run_that_can_never_be_written_stops_being_retried(self) -> None:
        """Isolation alone is not enough (§4.14).

        A pass that survives a failure and re-attempts it every sweep turns c9's
        hard stop into a spin: the queue moves, and one approval hammers HubSpot
        forever. The automatic path gets a bounded number of attempts and then
        hands the run to a person.
        """
        run_id, _worker, breaker = self.an_exhausted_run("campaign-unwritable")

        self.assertEqual(
            self.status_of(run_id),
            "failed",
            "a run the automatic path cannot complete ends failed and is"
            " surfaced; leaving it `running` is how it gets swept again"
            f" forever. It attempted {len(breaker.attempted)} writes",
        )
        self.assertLess(
            len(breaker.attempted),
            PASSES * len(self.assets),
            "every pass attempted the write again, so nothing bounds this run's"
            " attempts -- the bound has to bite before the passes run out, or"
            " it is only the test that stopped",
        )

    def test_recovery_never_reclaims_a_run_that_exhausted_its_attempts(
        self,
    ) -> None:
        """§4.14 and M18b -- `failed` is terminal for the automatic path.

        If `recover()` picked up `failed` too, CH9's bound would bound nothing:
        the run would re-enter the pool next sweep with a fresh allowance.
        `failed` is a request for a human, and only `retry` answers it (CH11).
        """
        key = "campaign-exhausted"
        run_id, worker, breaker = self.an_exhausted_run(key)
        self.assertEqual(
            self.status_of(run_id),
            "failed",
            "precondition: the run has already exhausted the automatic path",
        )
        attempts_when_exhausted = len(breaker.attempted)
        held_when_exhausted = len(self.held_for(key))

        for _ in range(3):
            self.expire_every_lease()
            self.recovery_pass(self.another_worker())
            self.recovery_pass(worker)

        self.assertEqual(
            self.status_of(run_id),
            "failed",
            "an unattended pass must leave a failed deployment where the"
            " operator can see it",
        )
        self.assertEqual(
            len(breaker.attempted),
            attempts_when_exhausted,
            "a failed run must not be attempted again without a person asking",
        )
        self.assertEqual(
            len(self.held_for(key)),
            held_when_exhausted,
            "an unattended pass must not write to HubSpot on behalf of a run"
            " that already failed",
        )


class AmbiguousWriteTest(RelayTestCase):
    """CH10 -- an exception is a question, and the provider has the answer."""

    def test_a_write_that_persisted_before_raising_is_reconciled_as_success(
        self,
    ) -> None:
        """c10, which the roadmap reads as evidence *for* readback (G7).

        Every write reaches HubSpot and every call then raises, so a service
        that believes the exception abandons a deployment that is already
        correct. Reading the expected key back settles it deterministically:
        the provider is idempotent and the key comes from the approval, not
        from the attempt.
        """
        key = "campaign-timeout"
        run_id = self.relay.submit(key, self.payload)
        timeouts = PersistsThenRaises(self.relay.provider)
        self.relay.provider = timeouts

        try:
            self.relay.run_once(run_id)
        except BaseException as error:  # noqa: BLE001 - reported, not hidden
            self.fail(
                "a write that persisted before raising must be reconciled by"
                f" reading it back, not propagated ({type(error).__name__}:"
                f" {error}). There is no try/except around create_draft today,"
                " so the run dies mid-payload (CH10, G7, M19)"
            )

        self.assertEqual(
            len(timeouts.persisted),
            len(self.assets),
            "precondition: every approved asset was written and every write"
            " then raised",
        )
        self.assertEqual(
            self.status_of(run_id),
            "done",
            "the deployment did happen; an unanswered write is not a failed one"
            " once the provider has been read back",
        )
        self.assertEqual(
            self.certification_of(run_id),
            "complete",
            "the reconciled run certifies from a fresh readback like any other",
        )
        self.assertEqual(
            len(self.held_for(key)),
            len(self.assets),
            "reconciliation reads the object it expected; it must not write a"
            " second copy of anything",
        )

    def test_a_write_that_raised_without_persisting_is_not_reported_complete(
        self,
    ) -> None:
        """The same exception, the opposite truth.

        Nothing was stored, so no readback can find it and no amount of retrying
        will. Success must be unreachable here -- this is the "receipt said
        verified, the drafts were not there" shape (c7), reached from the write
        path. And the retrying has to stop: an unbounded reconcile is the stall
        the operator already reported, reintroduced by the fix for it.
        """
        key = "campaign-refused"
        run_id = self.relay.submit(key, self.payload)
        refusals = RaisesWithoutPersisting(self.relay.provider)
        self.relay.provider = refusals

        with contextlib.suppress(Exception):
            self.relay.run_once(run_id)

        self.assertTrue(refusals.attempted, "precondition: the run did write")
        self.assertEqual(
            self.held_for(key),
            [],
            "precondition: HubSpot holds nothing for this deployment",
        )
        self.assertEqual(
            self.status_of(run_id),
            "failed",
            "a run whose writes never landed must say so where an operator can"
            " see it. Letting the exception escape leaves the row `running` for"
            " a worker that no longer exists -- the stuck run from c4, reached"
            " from the write path (§4.8, CH10)",
        )
        self.assertNotEqual(
            self.certification_of(run_id),
            "complete",
            "nothing was written, so nothing can be certified complete -- a"
            " readback that finds the object absent is the answer, not the"
            " reason to try again",
        )
        self.assertLessEqual(
            len(refusals.attempted),
            PASSES * len(self.assets),
            "one call to run_once must not retry a hopeless write without"
            f" limit; it attempted {len(refusals.attempted)} writes for"
            f" {len(self.assets)} approved assets",
        )


if __name__ == "__main__":
    unittest.main()
