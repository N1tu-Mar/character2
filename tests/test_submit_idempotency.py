"""Submit-time idempotency, and the admin retry that must not defeat it.

Scope is deliberately narrow: what `submit` decides before anything reaches the
provider, plus `retry`, which is the other door into the same deployment.
Nothing here recovers or certifies.

Every expectation is derived from the approved payload at runtime. No fixture
asset id, hash, or asset count is written down, so these tests keep their
meaning against any request shape.

Covers ROADMAP.md sections 4.11, 4.12, 4.13, and CH11.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

import relay.core
from relay import IdempotencyConflict, Relay
from relay.core import _canonical_json, _digest_text

FIXTURE = Path("fixtures/deployment_request.json")

# How many threads race each concurrent case. A property of the test, not of
# any payload — the assertions below hold for any value above one.
CONCURRENCY = 8


def approved_request() -> dict:
    return json.loads(FIXTURE.read_text())


def a_different_request(payload: dict) -> dict:
    """Derive a second, genuinely different payload from the approved one.

    Derived rather than loaded from another fixture so the difference is a
    property of this test, not of which files happen to sit in `fixtures/`.
    """
    altered = json.loads(json.dumps(payload))
    altered["assets"][0]["display_name"] += " (revised)"
    return altered


def payload_hash(payload: dict) -> str:
    """The identity the service assigns a payload, computed the way it does."""
    return _digest_text(_canonical_json(payload))


class RelayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.db_path = root / "deployments.db"
        self.provider_path = root / "fake-hubspot.json"
        self.relay = Relay(self.db_path, self.provider_path)
        self.key = "approved-campaign"

    def rows_for(self, idempotency_key: str) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        with contextlib.closing(connection):
            return connection.execute(
                "SELECT id, payload_hash FROM deployments"
                " WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchall()

    def submitted_concurrently(
        self,
        payloads: list[dict],
    ) -> tuple[list[str], list[IdempotencyConflict], list[BaseException]]:
        """Submit every payload under one key, all at once.

        Each thread gets its own `Relay`, because separate worker processes
        are what the deployment queue actually looks like.

        Returns the run ids that came back, the conflicts that were raised,
        and anything else that escaped — the third list is asserted empty so
        an unrelated failure can never be mistaken for a conflict.
        """
        barrier = threading.Barrier(len(payloads))
        run_ids: list[str] = []
        conflicts: list[IdempotencyConflict] = []
        unexpected: list[BaseException] = []
        guard = threading.Lock()

        def submit(payload: dict) -> None:
            worker = Relay(self.db_path, self.provider_path)
            barrier.wait()
            try:
                run_id = worker.submit(self.key, payload)
            except IdempotencyConflict as conflict:
                with guard:
                    conflicts.append(conflict)
            except BaseException as error:  # noqa: BLE001 - reported, not hidden
                with guard:
                    unexpected.append(error)
            else:
                with guard:
                    run_ids.append(run_id)

        threads = [
            threading.Thread(target=submit, args=(payload,))
            for payload in payloads
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return run_ids, conflicts, unexpected


class SubmitIdempotencyTest(RelayTestCase):
    # ROADMAP 4.11 — repeat safety.
    def test_same_key_same_payload_returns_the_same_run(self) -> None:
        payload = approved_request()

        first = self.relay.submit(self.key, payload)
        second = self.relay.submit(self.key, payload)

        self.assertEqual(
            first,
            second,
            "re-submitting an approved request must return the deployment it"
            " already created",
        )
        rows = self.rows_for(self.key)
        self.assertEqual(
            len(rows),
            1,
            "one approved request under one key is one deployment",
        )
        self.assertEqual(rows[0]["id"], first)
        self.assertEqual(rows[0]["payload_hash"], payload_hash(payload))

    # ROADMAP 4.13 — the racing path must reach 4.11's verdict.
    def test_concurrent_same_payload_submissions_converge_on_one_run(
        self,
    ) -> None:
        payload = approved_request()

        run_ids, conflicts, unexpected = self.submitted_concurrently(
            [approved_request() for _ in range(CONCURRENCY)]
        )

        self.assertEqual([], unexpected, "no submit may fail unexpectedly")
        self.assertEqual(
            [],
            conflicts,
            "identical payloads do not conflict, whoever wins the race",
        )
        self.assertEqual(
            len(run_ids),
            CONCURRENCY,
            "every caller gets an answer",
        )
        self.assertEqual(
            len(set(run_ids)),
            1,
            "every caller must be handed the same deployment",
        )
        rows = self.rows_for(self.key)
        self.assertEqual(len(rows), 1, "the race may create only one row")
        self.assertEqual(rows[0]["id"], run_ids[0])
        self.assertEqual(rows[0]["payload_hash"], payload_hash(payload))

    # ROADMAP 4.12 — conflict, refused before any provider write.
    def test_same_key_different_payload_conflicts_before_any_write(
        self,
    ) -> None:
        payload = approved_request()
        altered = a_different_request(payload)
        self.assertNotEqual(
            payload_hash(payload),
            payload_hash(altered),
            "precondition: the derived payload must be a different approval",
        )

        accepted = self.relay.submit(self.key, payload)

        with self.assertRaises(IdempotencyConflict):
            self.relay.submit(self.key, altered)

        self.assertEqual(
            [],
            self.relay.provider.list_objects(),
            "a refused submit must not touch the provider",
        )
        rows = self.rows_for(self.key)
        self.assertEqual(len(rows), 1, "the refused payload gets no row")
        self.assertEqual(rows[0]["id"], accepted)
        self.assertEqual(rows[0]["payload_hash"], payload_hash(payload))

    def test_an_unrelated_integrity_error_is_not_swallowed(self) -> None:
        """A constraint failure we are not resolving must reach the caller.

        `submit` treats `IntegrityError` as the answer to "is this key already
        taken?". It is only entitled to do that when the key really is taken.
        Here the key is free and the failing constraint is the `id` primary
        key, so there is no deployment to hand back and nothing to compare —
        reporting either success or a conflict would be a fabricated answer.
        """
        payload = approved_request()
        collision = uuid.uuid4()

        with mock.patch.object(relay.core.uuid, "uuid4", return_value=collision):
            self.relay.submit(self.key, payload)

            with self.assertRaises(sqlite3.IntegrityError) as caught:
                self.relay.submit("a-different-key", payload)

        self.assertIn("deployments.id", str(caught.exception))
        self.assertEqual(
            len(self.rows_for("a-different-key")),
            0,
            "the refused submit leaves no row behind",
        )
        self.assertEqual([], self.relay.provider.list_objects())

    # ROADMAP 4.13 — the racing path must reach 4.12's verdict.
    def test_concurrent_conflicting_submissions_leave_one_winner(self) -> None:
        payload = approved_request()
        altered = a_different_request(payload)
        contenders = [payload, altered] * (CONCURRENCY // 2)

        run_ids, conflicts, unexpected = self.submitted_concurrently(
            [json.loads(json.dumps(contender)) for contender in contenders]
        )

        self.assertEqual([], unexpected, "no submit may fail unexpectedly")
        rows = self.rows_for(self.key)
        self.assertEqual(
            len(rows),
            1,
            "two approvals under one key must not both get a deployment",
        )

        winner = rows[0]
        losers = [
            contender
            for contender in contenders
            if payload_hash(contender) != winner["payload_hash"]
        ]
        self.assertEqual(
            len(conflicts),
            len(losers),
            "every caller holding the losing payload gets a conflict",
        )
        self.assertEqual(
            len(run_ids),
            len(contenders) - len(losers),
            "every caller holding the winning payload gets a run id",
        )
        self.assertEqual(
            set(run_ids),
            {winner["id"]},
            "the winners share the one deployment that was created",
        )
        self.assertEqual(
            [],
            self.relay.provider.list_objects(),
            "no provider write happens at submit time, winner or loser",
        )


class RetryTest(RelayTestCase):
    """CH11 — the admin panel's retry button.

    `retry` is the second door into a deployment, and the operator reaches for
    it exactly when a run looks stuck (c4). It must reopen the deployment that
    already exists rather than mint a second one.
    """

    def a_completed_deployment(self) -> tuple[str, dict]:
        payload = approved_request()
        run_id = self.relay.submit(self.key, payload)
        self.relay.run_once(run_id)
        return run_id, payload

    def test_retry_reopens_the_existing_deployment(self) -> None:
        run_id, payload = self.a_completed_deployment()
        deployed = len(self.relay.provider.list_objects())
        self.assertEqual(
            deployed,
            len(payload["assets"]),
            "precondition: the approved assets are deployed",
        )

        reopened = self.relay.retry(run_id)

        self.assertEqual(
            reopened,
            run_id,
            "retry reopens the deployment the operator was looking at",
        )
        self.assertEqual(
            len(self.rows_for(self.key)),
            1,
            "retry must not create a second deployment under the same key",
        )
        state = self.relay.get(run_id)
        self.assertEqual(state["status"], "pending", "the run is requeued")
        self.assertIsNone(
            state["receipt"],
            "a requeued run must not still be carrying a completed receipt",
        )
        self.assertEqual(
            len(self.relay.provider.list_objects()),
            deployed,
            "retry itself deploys nothing",
        )

    def test_retry_then_rerun_creates_no_additional_drafts(self) -> None:
        run_id, payload = self.a_completed_deployment()
        deployed = len(self.relay.provider.list_objects())

        self.relay.run_once(self.relay.retry(run_id))

        self.assertEqual(
            len(self.relay.provider.list_objects()),
            deployed,
            "re-running a retried deployment lands on the same drafts",
        )
        self.assertEqual(
            len(self.relay.provider.list_objects()),
            len(payload["assets"]),
            "HubSpot holds exactly the approved set, once",
        )


if __name__ == "__main__":
    unittest.main()
