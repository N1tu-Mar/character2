"""Who is allowed to execute a deployment, and who is allowed to end one.

Covers ROADMAP.md CH4 (a run is owned by exactly one worker for a bounded
lease, and an expired lease is reclaimable) and CH8 (cancellation stops the
next provider write, and a run that was cancelled cannot be reported `done`).

Two guarantees are under test here, and they share one mechanism:

  * G4 -- nothing owns a run. Two workers both execute it, both write, and
    both report success (c8, and the stall inside c4).
  * G5 -- cancellation is advisory. `cancel` writes a status, `run_once` never
    reads it, and the unconditional terminal `UPDATE` writes over it (c6).

Both end in the same defect: a worker that is stale, non-owning, or cancelled
is still able to save a receipt and a terminal status. So the assertions below
are mostly about what a *second* actor may not do, and the terminal write is
pinned from both sides -- ownership and cancellation.

Ordering is forced with `threading.Barrier`, `threading.Event`, and an explicit
provider hook. No test sleeps, and no test depends on how long anything takes;
the timeouts are deadlock guards, not schedule assumptions.

Scope: nothing here certifies, bounds attempts, or reconciles an ambiguous
write. Every expectation is derived from the approved payload at runtime -- no
fixture asset id, hash, or asset count is written down.

One documented limit, asserted rather than assumed: an object written before a
cancellation stays in the provider. The provider has no delete (CH15), so a
cancelled run is "no further writes", never "as if it never ran".
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

from relay import InjectedCrash, Relay, RunCancelled

FIXTURE = Path("fixtures/deployment_request.json")

# How many workers race one pending deployment. A property of the test; the
# assertion below ("exactly one") holds for any value above one.
WORKERS = 20

# Deadlock guard only. Ordering comes from the events, never from this number.
JOIN_TIMEOUT_SECONDS = 30

# The only pieces of schema this file names, both from ROADMAP CH4.
LEASE_COLUMN = "lease_expires_at"
OWNER_COLUMN = "owner"


def approved_request() -> dict:
    return json.loads(FIXTURE.read_text())


class ProviderHook:
    """A wrapper around the provider, so a test can act between writes.

    The provider is not ours to change (TASK.md), so the hook sits outside it
    and delegates everything else. `after_write` is called once each provider
    write has been confirmed -- the object is already stored when it runs,
    which is exactly the moment the interesting cases start from.
    """

    def __init__(
        self,
        provider: Any,
        after_write: Callable[[int, str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._after_write = after_write
        self.confirmed_writes: list[str] = []

    def create_draft(self, *, external_key: str, asset: dict) -> dict:
        created = self._provider.create_draft(
            external_key=external_key,
            asset=asset,
        )
        self.confirmed_writes.append(external_key)
        if self._after_write is not None:
            self._after_write(len(self.confirmed_writes), external_key)
        return created

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class Outcome:
    """What one attempt at `run_once` did, without deciding how it declines.

    CH4 and CH8 say a worker that does not own a run, or whose run was
    cancelled, must not execute it. They do not name an exception for it, so
    these tests assert on the durable facts -- the receipt, the status, and
    what the provider holds -- and only require that the attempt did not come
    back with a completed receipt.
    """

    def __init__(self) -> None:
        self.receipt: dict | None = None
        self.error: BaseException | None = None

    @property
    def completed(self) -> bool:
        return self.receipt is not None

    def describe(self) -> str:
        if self.completed:
            return "completed the run"
        return f"declined with {type(self.error).__name__}: {self.error}"


def attempt(relay: Relay, run_id: str) -> Outcome:
    outcome = Outcome()
    try:
        outcome.receipt = relay.run_once(run_id)
    except BaseException as error:  # noqa: BLE001 - reported, not hidden
        outcome.error = error
    return outcome


class WorkerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.db_path = root / "deployments.db"
        self.provider_path = root / "fake-hubspot.json"
        self.relay = Relay(self.db_path, self.provider_path)
        self.key = "approved-campaign"
        self.payload = approved_request()
        self.assertGreater(
            len(self.payload["assets"]),
            1,
            "precondition: 'stops before the later writes' needs later writes",
        )
        self.run_id = self.relay.submit(self.key, self.payload)

    def another_worker(self) -> Relay:
        """A separate worker process against the same database and provider."""
        return Relay(self.db_path, self.provider_path)

    def held_objects(self) -> list[dict]:
        return self.relay.provider.list_objects()

    def state(self) -> dict:
        return self.relay.get(self.run_id)

    def stalled_worker(
        self,
        after_write: int = 1,
    ) -> tuple[threading.Thread, Outcome, threading.Event]:
        """Start a worker and park it once `after_write` writes are confirmed.

        Returns once the worker is genuinely parked, so the caller can act
        against a run that is being executed right now. Releasing it is the
        caller's business; the cleanup releases it either way so a failed
        assertion cannot leave a thread parked forever.
        """
        reached = threading.Event()
        release = threading.Event()
        outcome = Outcome()

        def park(count: int, _external_key: str) -> None:
            if count != after_write:
                return
            reached.set()
            if not release.wait(JOIN_TIMEOUT_SECONDS):
                raise AssertionError("the parked worker was never released")

        worker = self.another_worker()
        worker.provider = ProviderHook(worker.provider, after_write=park)

        def run() -> None:
            result = attempt(worker, self.run_id)
            outcome.receipt = result.receipt
            outcome.error = result.error

        thread = threading.Thread(target=run, daemon=True)

        def release_and_join() -> None:
            """Never leave a parked worker running past the test.

            Releasing without joining lets the worker keep reading and writing
            files while the temporary directory is being removed, which shows
            up as an unrelated error in whichever test happens to be running.
            """
            release.set()
            thread.join(JOIN_TIMEOUT_SECONDS)

        self.addCleanup(release_and_join)
        thread.start()
        self.assertTrue(
            reached.wait(JOIN_TIMEOUT_SECONDS),
            "precondition: the worker never confirmed its first write",
        )
        self.assertEqual(
            len(self.held_objects()),
            after_write,
            "precondition: the parked worker's writes are already stored",
        )
        return thread, outcome, release

    def finish(self, thread: threading.Thread, release: threading.Event) -> None:
        release.set()
        thread.join(JOIN_TIMEOUT_SECONDS)
        self.assertFalse(thread.is_alive(), "the released worker never returned")

    def lease_holder(self) -> str | None:
        """Who the database says owns this run, if anyone."""
        connection = sqlite3.connect(self.db_path)
        with contextlib.closing(connection):
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(deployments)")
            }
            if OWNER_COLUMN not in columns:
                raise AssertionError(
                    f"deployments has no {OWNER_COLUMN} column (CH4): with"
                    " nothing recording who is executing a run, every worker"
                    " is entitled to execute all of them"
                )
            row = connection.execute(
                f"SELECT {OWNER_COLUMN} FROM deployments WHERE id = ?",
                (self.run_id,),
            ).fetchone()
        return row[0]

    def expire_the_lease(self) -> None:
        """Make the current owner's lease look expired, without waiting.

        The column name is CH4's. A past value is derived from what is actually
        stored, so this test does not also dictate whether a lease is an epoch
        or a timestamp -- only that one exists and can lapse.
        """
        connection = sqlite3.connect(self.db_path)
        with contextlib.closing(connection):
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(deployments)")
            }
            if LEASE_COLUMN not in columns:
                raise AssertionError(
                    f"deployments has no {LEASE_COLUMN} column (CH4): without a"
                    " lease that lapses, a run whose worker died is either"
                    " stranded forever or free for anyone to take immediately"
                )
            row = connection.execute(
                f"SELECT {LEASE_COLUMN} FROM deployments WHERE id = ?",
                (self.run_id,),
            ).fetchone()
            self.assertIsNotNone(
                row[0],
                "precondition: the run is owned, so it holds a lease",
            )
            expired = (
                0
                if isinstance(row[0], (int, float))
                else "1970-01-01T00:00:00+00:00"
            )
            cursor = connection.execute(
                f"UPDATE deployments SET {LEASE_COLUMN} = ? WHERE id = ?",
                (expired, self.run_id),
            )
            connection.commit()
            self.assertEqual(cursor.rowcount, 1)


class WorkerClaimTest(WorkerTestCase):
    """CH4 -- one run, one owner, for a bounded time."""

    def test_only_one_of_many_workers_executes_a_pending_deployment(
        self,
    ) -> None:
        """The c8 shape: two `worker_claim` events for one run.

        Every worker is a separate `Relay` against the same database and the
        same provider, and the barrier holds them until all of them are ready,
        so the claim is contended rather than sequential.
        """
        barrier = threading.Barrier(WORKERS)
        outcomes: list[Outcome] = []
        guard = threading.Lock()

        def work() -> None:
            worker = self.another_worker()
            barrier.wait()
            outcome = attempt(worker, self.run_id)
            with guard:
                outcomes.append(outcome)

        threads = [threading.Thread(target=work) for _ in range(WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(JOIN_TIMEOUT_SECONDS)

        self.assertEqual(len(outcomes), WORKERS, "every worker must answer")
        completed = [outcome for outcome in outcomes if outcome.completed]
        self.assertEqual(
            len(completed),
            1,
            "exactly one worker may execute a pending deployment; the others"
            " must decline. Got: "
            + ", ".join(sorted(outcome.describe() for outcome in outcomes)),
        )
        self.assertEqual(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "one execution deploys the approved set once",
        )
        self.assertEqual(self.state()["status"], "done")

    def test_a_second_worker_cannot_execute_an_actively_owned_run(self) -> None:
        """The run is being executed right now, by someone else.

        The owner is parked mid-payload with its first write confirmed, so the
        intruder meets a live claim rather than a stale one. The decisive
        assertion is the provider count: an intruder that proceeds writes the
        remaining approved assets, and that shows up whether or not it manages
        to save a receipt.
        """
        thread, owner, release = self.stalled_worker()

        intruder = attempt(self.another_worker(), self.run_id)

        self.assertFalse(
            intruder.completed,
            "a worker that does not own the run must not complete it",
        )
        self.assertEqual(
            len(self.held_objects()),
            1,
            "the intruder must not write the assets the owner has not reached",
        )
        during = self.state()
        self.assertEqual(
            during["status"],
            "running",
            "a non-owning worker must not move the run to a terminal status",
        )
        self.assertIsNone(
            during["receipt"],
            "a non-owning worker must not save a receipt for someone's run",
        )

        self.finish(thread, release)

        self.assertTrue(
            owner.completed,
            f"the owner must still finish its own run; it {owner.describe()}",
        )
        final = self.state()
        self.assertEqual(final["status"], "done")
        self.assertIsNotNone(final["receipt"])
        self.assertEqual(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "the owner deploys the approved set, once",
        )

    def test_a_hung_workers_run_is_not_taken_before_its_lease_lapses(
        self,
    ) -> None:
        """Hung is the case the lease exists for.

        A worker that stops answering is indistinguishable from one that is
        merely slow, so ownership cannot be released by anyone deciding the
        run looks stuck. Only the lease lapsing releases it.

        A worker that exits through an exception it can report is a different
        case and is covered separately below -- it lets go on the way out,
        because it still can.
        """
        _thread, _outcome, _release = self.stalled_worker()

        intruder = attempt(self.another_worker(), self.run_id)

        self.assertFalse(
            intruder.completed,
            "the lease has not lapsed, so the run is still owned",
        )
        stranded = self.state()
        self.assertEqual(stranded["status"], "running")
        self.assertIsNone(stranded["receipt"])
        self.assertEqual(len(self.held_objects()), 1)
        self.assertIsNotNone(
            self.lease_holder(),
            "a run being executed right now is owned, on the record",
        )

    def test_a_reported_crash_lets_go_of_the_claim_immediately(self) -> None:
        """The deliberate other half of the rule above.

        `crash_at` raises through `run_once`; the process is still alive and
        the worker knows it is not continuing. Holding the run for the rest of
        the lease would strand it for no reason, so a failure a worker can
        report is a claim it releases. Only a worker that cannot report --
        killed, hung -- leaves the lease to do the work.
        """
        with self.assertRaises(InjectedCrash):
            self.relay.run_once(
                self.run_id,
                crash_at="after_first_provider_write",
            )

        self.assertIsNone(
            self.lease_holder(),
            "a worker that reported its own failure must not still own the run",
        )
        self.assertEqual(self.state()["status"], "running", "work is unfinished")

        self.another_worker().recover()

        self.assertEqual(
            self.state()["status"],
            "done",
            "the released run is picked up on the next pass, with no waiting",
        )
        self.assertEqual(len(self.held_objects()), len(self.payload["assets"]))

    def test_an_expired_lease_is_reclaimed_and_the_run_finishes(self) -> None:
        """The other half of CH4: ownership must also be releasable.

        A claim that never lapses turns one hung worker into a permanently
        stranded deployment -- the operator's "stuck" run from c4. Recovery is
        the production path that picks these up, so the reclaim is driven
        through `recover()` rather than through anything test-only.

        The hung worker is then released, and must not be able to record an
        outcome over the run it no longer owns.
        """
        thread, stale, release = self.stalled_worker()

        self.expire_the_lease()
        self.another_worker().recover()

        reclaimed = self.state()
        self.assertEqual(
            reclaimed["status"],
            "done",
            "a run whose lease lapsed must be executable again",
        )
        self.assertIsNotNone(reclaimed["receipt"])
        self.assertEqual(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "the reclaiming worker lands on the same objects; a reclaim is not"
            " a second deployment",
        )
        reclaimed_receipt = reclaimed["receipt"]

        self.finish(thread, release)

        self.assertFalse(
            stale.completed,
            "the worker whose lease lapsed must not finish the run it lost;"
            f" it {stale.describe()}",
        )
        self.assertEqual(
            self.state()["receipt"],
            reclaimed_receipt,
            "a stale worker must not write its own receipt over the one the"
            " owner recorded",
        )
        self.assertEqual(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "and it must not go on writing to HubSpot either",
        )


class CancellationTest(WorkerTestCase):
    """CH8 -- cancellation decides the next write and the terminal status."""

    def source_asset_ids(self) -> set[str]:
        return {obj["source_asset_id"] for obj in self.held_objects()}

    def test_cancellation_after_the_first_write_stops_the_later_writes(
        self,
    ) -> None:
        """c6's shape, driven from the provider hook rather than a clock.

        The cancel lands at the one moment that is hard to get right: after a
        write has been confirmed by the provider and before the next one is
        attempted. `run_once` has to read the cancellation before every write,
        not once at the start.
        """
        cancelled_after = 1

        def cancel_once(count: int, _external_key: str) -> None:
            if count == cancelled_after:
                self.another_worker().cancel(self.run_id)

        self.relay.provider = ProviderHook(
            self.relay.provider,
            after_write=cancel_once,
        )

        with self.assertRaises(RunCancelled):
            self.relay.run_once(self.run_id)

        held = self.held_objects()
        self.assertEqual(
            len(held),
            cancelled_after,
            "no approved asset may be written after the cancellation",
        )
        self.assertEqual(
            self.source_asset_ids(),
            {self.payload["assets"][0]["asset_id"]},
            "what stayed is exactly what was confirmed before the cancel --"
            " the provider has no delete, so a cancelled run means 'no further"
            " writes', never 'as if it never ran' (CH15)",
        )
        final = self.state()
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(
            final["receipt"],
            "a partial deployment must not present a receipt",
        )

    def test_a_cancelled_run_cannot_be_marked_done(self) -> None:
        """The terminal `UPDATE` is the last place a cancel can be lost.

        The worker is parked mid-payload, the operator cancels from elsewhere,
        and then the worker is released to finish. Its final write must not
        overwrite the cancellation -- which is what makes the operator's
        "I cancelled it and it still says done" possible today.
        """
        thread, worker, release = self.stalled_worker()

        self.another_worker().cancel(self.run_id)
        self.assertEqual(self.state()["status"], "cancelled")

        self.finish(thread, release)

        final = self.state()
        self.assertEqual(
            final["status"],
            "cancelled",
            "a cancelled deployment must not reach 'done'; the worker that"
            f" was running it {worker.describe()}",
        )
        self.assertIsNone(
            final["receipt"],
            "no receipt may be saved for a run the operator stopped",
        )
        self.assertFalse(
            worker.completed,
            "a cancelled run must not report a completed deployment",
        )
        self.assertLess(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "the cancel must have stopped at least the last write",
        )

    def test_a_cancelled_deployment_is_not_started_by_a_later_worker(
        self,
    ) -> None:
        """Cancellation also has to hold against a worker that has not begun.

        Recovery and the retry button both hand runs to workers after the fact,
        so a cancelled run that is merely 'not currently executing' would come
        back to life on the next pass.
        """
        self.relay.cancel(self.run_id)

        outcome = attempt(self.another_worker(), self.run_id)

        self.assertFalse(
            outcome.completed,
            f"a cancelled run must not be executed; it {outcome.describe()}",
        )
        self.assertEqual(
            [],
            self.held_objects(),
            "a cancelled run that never started writes nothing at all",
        )
        final = self.state()
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final["receipt"])

    def test_recovery_does_not_resume_a_cancelled_deployment(self) -> None:
        """The same rule, reached through the path that actually sweeps runs.

        A run cancelled while it was `running` is exactly what the starter's
        recovery pass selects on today, so this is the route by which a
        cancelled deployment quietly finishes.
        """
        thread, _worker, release = self.stalled_worker()
        self.another_worker().cancel(self.run_id)
        self.finish(thread, release)

        self.another_worker().recover()

        final = self.state()
        self.assertEqual(
            final["status"],
            "cancelled",
            "recovery must not treat a cancelled run as unfinished work",
        )
        self.assertIsNone(final["receipt"])
        self.assertLess(
            len(self.held_objects()),
            len(self.payload["assets"]),
            "recovery must not write the assets the cancel stopped",
        )


if __name__ == "__main__":
    unittest.main()
