from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPLETE = "complete"
DIVERGENT = "divergent"
UNKNOWN = "unknown"

# The fields that identify a deployed object and record where it came from
# (ROADMAP §4.4, §4.5). These are the fields we sent and the provider echoed;
# matching them proves presence and provenance, never content.
IDENTITY_FIELDS = (
    "external_key",
    "source_asset_id",
    "source_sha256",
    "object_type",
    "display_name",
    "status",
)


class InjectedCrash(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class InvalidApprovedRequest(ValueError):
    """An approved request that cannot name a deployment or its objects."""


class ProviderUnreadable(RuntimeError):
    """The provider's state could not be opened or parsed at all.

    Raised only for "we could not look". An object that is absent from a state
    file that parsed fine is a successful read with a negative answer, and is
    never routed through here (ROADMAP §4, Missing is not unreadable).
    """


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def external_key(idempotency_key: str, asset_id: str) -> str:
    """The only place a provider key is built.

    Identity is the operator's approval, never the attempt that carried it
    out. The key contains no `run_id`, no UUID, and no timestamp, so a retry,
    a recovery pass, and a re-submitted approval all compute the same value
    and land on the same HubSpot object.

    The leading length pins where the idempotency key ends, which makes the
    encoding injective for any component contents, colons included. Plain
    concatenation is not: `("a", "b:c")` and `("a:b", "c")` both flatten to
    `a:b:c`, and two unrelated deployments would share provider objects.

    Neither `payload_hash` nor `display_name` is an input. A hash in the key
    would move identity with the serialization of a payload; a name in the
    key would make two legitimately identical names into one object.
    """
    return f"{len(idempotency_key)}:{idempotency_key}:{asset_id}"


def deployment_namespace(idempotency_key: str) -> str:
    """The prefix holding exactly one deployment's provider objects.

    Unambiguous for the same reason `external_key` is: scanning for
    `9:deploy-20:` cannot match `11:deploy-207a:asset-lp-001`, which naive
    prefix matching on `deploy-20` would.
    """
    return f"{len(idempotency_key)}:{idempotency_key}:"


def _validate_approved_request(
    idempotency_key: str,
    payload: dict[str, Any],
) -> None:
    """Refuse a request that cannot name a deployment or its objects."""
    if not idempotency_key:
        raise InvalidApprovedRequest(
            "an approved request needs an idempotency key: it is what names"
            " the deployment and every object in it"
        )

    assets = payload.get("assets") or []
    if not assets:
        raise InvalidApprovedRequest(
            "an approved request with no assets is not a deployment"
        )

    for asset in assets:
        if not str(asset.get("asset_id") or ""):
            raise InvalidApprovedRequest(
                "every approved asset needs an asset id: an empty one makes"
                f" its provider key equal to {deployment_namespace(idempotency_key)!r},"
                " which is the namespace the scan for extras runs over"
            )


class FakeHubSpot:
    """Small durable provider simulation with idempotent draft creation.

    Like the real destination, this provider owns the objects it stores. It
    accepts what you send, applies its own rules to it, and returns what it
    decided to keep.
    """

    DISPLAY_NAME_LIMIT = 40

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        if not self.state_path.exists():
            self.state_path.write_text("{}")

    def _load(self) -> dict[str, dict[str, str]]:
        return json.loads(self.state_path.read_text())

    def _save(self, objects: dict[str, dict[str, str]]) -> None:
        self.state_path.write_text(_canonical_json(objects))

    def _store_display_name(self, value: str) -> str:
        return str(value).strip()[: self.DISPLAY_NAME_LIMIT]

    def create_draft(
        self,
        *,
        external_key: str,
        asset: dict[str, Any],
    ) -> dict[str, str]:
        objects = self._load()
        existing = objects.get(external_key)
        candidate = {
            "object_id": f"hs-{_digest_text(external_key)[:12]}",
            "external_key": external_key,
            "source_asset_id": str(asset["asset_id"]),
            "source_sha256": str(asset["source_sha256"]),
            "object_type": str(asset["type"]),
            "display_name": self._store_display_name(asset["display_name"]),
            "status": "draft",
        }
        if existing is not None:
            if existing != candidate:
                raise IdempotencyConflict(
                    f"provider key {external_key!r} was reused"
                )
            return dict(existing)
        objects[external_key] = candidate
        self._save(objects)
        return dict(candidate)

    def read(self, external_key: str) -> dict[str, str]:
        return dict(self._load()[external_key])

    def list_objects(self) -> list[dict[str, str]]:
        return [dict(value) for value in self._load().values()]


class Relay:
    def __init__(self, db_path: Path, provider_state_path: Path) -> None:
        self.db_path = Path(db_path)
        self.provider = FakeHubSpot(provider_state_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_json TEXT
                )
                """
            )

    def submit(self, idempotency_key: str, payload: dict[str, Any]) -> str:
        """Create the deployment for this approval, or return the existing one.

        The operator's idempotency key is the unit of idempotency, so one key
        names one deployment. Re-submitting the same approval returns the run
        that already exists; submitting different content under a key that is
        already spoken for is refused before anything reaches the provider.

        The `UNIQUE` constraint decides, not a prior `SELECT`. Two workers
        submitting the same key at once would both see no row and both insert,
        so the loser's `IntegrityError` is the answer rather than an error --
        it re-reads the row that actually landed and compares against that.

        A request that cannot name a deployment or its objects is refused
        here, before a row exists and long before the provider is opened.
        """
        _validate_approved_request(idempotency_key, payload)
        run_id = str(uuid.uuid4())
        payload_json = _canonical_json(payload)
        payload_hash = _digest_text(payload_json)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO deployments
                        (id, idempotency_key, payload_hash, payload_json,
                         status)
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (run_id, idempotency_key, payload_hash, payload_json),
                )
        except sqlite3.IntegrityError:
            existing = self._deployment_for_key(idempotency_key)
            if existing is None:
                # The key is free, so the constraint that failed was not the
                # one we are resolving. Do not guess at it.
                raise
            if str(existing["payload_hash"]) != payload_hash:
                raise IdempotencyConflict(
                    f"idempotency key {idempotency_key!r} was already used"
                    " for a different approved request"
                ) from None
            return str(existing["id"])
        return run_id

    def _deployment_for_key(self, idempotency_key: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, payload_hash FROM deployments
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM deployments WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        receipt_json = result.pop("receipt_json")
        result["receipt"] = (
            json.loads(receipt_json) if receipt_json is not None else None
        )
        return result

    def cancel(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE deployments SET status = 'cancelled' WHERE id = ?",
                (run_id,),
            )

    def retry(self, run_id: str) -> str:
        """The admin panel's retry button for a deployment operators call stuck.

        Requeues the deployment the operator is looking at. It does not create
        a second one: an approval already has a deployment, and minting another
        under the same key is what put duplicate drafts in HubSpot.

        The stored receipt is dropped, because a requeued run has not completed
        and must not go on presenting a finished run's receipt as its own.
        """
        self.get(run_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE deployments
                SET status = 'pending', receipt_json = NULL
                WHERE id = ?
                """,
                (run_id,),
            )
        return run_id

    # -- certification --------------------------------------------------------
    #
    # One path, used by everything that reports on a deployment: the completion
    # check inside `run_once`, `audit`, and `deployment_summary`. Every caller
    # gets its answer from a reading of the provider taken now. Nothing here
    # consults `receipt_json`, and no verdict is ever stored as truth.

    def _normalized_display_name(self, value: Any) -> str:
        """The provider's own rule for what it keeps, restated in one place.

        Comparing against the raw approved string would invent a discrepancy
        for every name the provider legitimately truncates. The limit is read
        off the provider rather than written down, so this tracks the rule
        instead of a copy of today's value.
        """
        limit = getattr(
            self.provider,
            "DISPLAY_NAME_LIMIT",
            FakeHubSpot.DISPLAY_NAME_LIMIT,
        )
        return str(value).strip()[:limit]

    def _expected_objects(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        """What the provider must be holding for this approval, per §4.4-4.5."""
        expected: dict[str, dict[str, str]] = {}
        for asset in payload.get("assets", []):
            key = external_key(idempotency_key, str(asset["asset_id"]))
            expected[key] = {
                "external_key": key,
                "source_asset_id": str(asset["asset_id"]),
                "source_sha256": str(asset["source_sha256"]),
                "object_type": str(asset["type"]),
                "display_name": self._normalized_display_name(
                    asset["display_name"]
                ),
                "status": "draft",
            }
        return expected

    def _list_provider_objects(self) -> list[dict[str, str]]:
        try:
            return list(self.provider.list_objects())
        except (json.JSONDecodeError, OSError) as error:
            raise ProviderUnreadable(str(error)) from error

    def _read_provider_object(self, key: str) -> dict[str, str] | None:
        """Read one object. `None` means we looked and it is not there.

        The two failure modes are caught in separate clauses on purpose. A
        `KeyError` is a successful read with a negative answer and must reach
        the caller as a missing object; a state that cannot be parsed or
        opened is "we could not look" and must not be reported as data loss
        (ROADMAP §4, CH5b).
        """
        try:
            return dict(self.provider.read(key))
        except KeyError:
            return None
        except (json.JSONDecodeError, OSError) as error:
            raise ProviderUnreadable(str(error)) from error

    def _verdict(
        self,
        run_id: str,
        certification: str,
        discrepancies: list[dict[str, Any]],
        objects_found: int,
        objects_expected: int,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "certification": certification,
            "certified_at": datetime.now(timezone.utc).isoformat(),
            "discrepancies": discrepancies,
            "objects_found": objects_found,
            "objects_expected": objects_expected,
        }

    def _certify(
        self,
        run: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Read the provider now and say what it holds for this deployment.

        Returns the verdict and the objects actually found, so a caller that
        needs to record what it saw does not have to read the provider twice.
        """
        run_id = str(run["id"])
        idempotency_key = str(run["idempotency_key"])
        expected = self._expected_objects(idempotency_key, run["payload"])
        namespace = deployment_namespace(idempotency_key)
        discrepancies: list[dict[str, Any]] = []
        found: list[dict[str, str]] = []

        try:
            listed = self._list_provider_objects()
            for key, want in expected.items():
                stored = self._read_provider_object(key)
                if stored is None:
                    discrepancies.append(
                        {
                            "kind": "missing_object",
                            "external_key": key,
                            "source_asset_id": want["source_asset_id"],
                        }
                    )
                    continue
                found.append(stored)
                for field in IDENTITY_FIELDS:
                    if str(stored.get(field)) != want[field]:
                        discrepancies.append(
                            {
                                "kind": "field_mismatch",
                                "external_key": key,
                                "source_asset_id": want["source_asset_id"],
                                "field": field,
                                "expected": want[field],
                                "found": stored.get(field),
                            }
                        )
        except ProviderUnreadable as error:
            # No per-object claim is possible in either direction.
            return (
                self._verdict(
                    run_id,
                    UNKNOWN,
                    [{"kind": "provider_unreadable", "detail": str(error)}],
                    0,
                    len(expected),
                ),
                [],
            )

        # The reverse scan (§4.3): anything under this deployment's namespace
        # that nobody approved. Iterating the approved assets alone would never
        # see a stray object.
        for stored in listed:
            key = str(stored.get("external_key", ""))
            if key.startswith(namespace) and key not in expected:
                discrepancies.append(
                    {"kind": "unexpected_object", "external_key": key}
                )

        certification = COMPLETE if not discrepancies else DIVERGENT
        return (
            self._verdict(
                run_id,
                certification,
                discrepancies,
                len(found),
                len(expected),
            ),
            found,
        )

    def certify(self, run_id: str) -> dict[str, Any]:
        """The certification on its own, computed from a fresh readback."""
        verdict, _found = self._certify(self.get(run_id))
        return verdict

    def run_once(
        self,
        run_id: str,
        *,
        crash_at: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE deployments SET status = 'running' WHERE id = ?",
                (run_id,),
            )

        idempotency_key = str(run["idempotency_key"])
        for index, asset in enumerate(run["payload"]["assets"]):
            object_key = external_key(idempotency_key, str(asset["asset_id"]))
            self.provider.create_draft(
                external_key=object_key,
                asset=asset,
            )
            if crash_at == "after_first_provider_write" and index == 0:
                raise InjectedCrash(
                    "crashed after provider write and before local receipt"
                )

        # `done` is earned, not assumed (ROADMAP §4.8). The run has finished
        # every write it was asked to make; whether it may say so depends on
        # what the provider holds when it is read now, after the last write.
        verdict, objects = self._certify(run)
        status = "done" if verdict["certification"] == COMPLETE else "failed"
        receipt = {
            "run_id": run_id,
            "payload_sha256": run["payload_hash"],
            "objects": objects,
            "certification_at_completion": verdict,
        }
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE deployments
                SET status = ?, receipt_json = ?
                WHERE id = ?
                """,
                (status, _canonical_json(receipt), run_id),
            )
        return receipt

    def deployment_summary(self, run_id: str) -> dict[str, Any]:
        """Operator-facing summary of what a deployment did.

        This is what the dashboard and `make demo` show, and it is the
        quickest way to see the outcome of a run.

        `status` is history: it says what the run did and does not change when
        HubSpot does. The certification beside it is taken now, from the
        provider, every single time this is called. Neither is reported
        without the other (ROADMAP §4.9).
        """
        run = self.get(run_id)
        verdict, _found = self._certify(run)
        return {
            "run_id": run_id,
            "status": run["status"],
            "objects_deployed": verdict["objects_found"],
            "assets_approved": len(run["payload"].get("assets", [])),
            "certification": verdict["certification"],
            "certified_at": verdict["certified_at"],
            "discrepancies": verdict["discrepancies"],
        }

    def audit(self, run_id: str) -> dict[str, Any]:
        """The dashboard's "Check again" button.

        Operators press this when they want to know whether HubSpot still
        holds what was approved. It answers by reading HubSpot -- never by
        re-reading the receipt this service wrote about itself.
        """
        run = self.get(run_id)
        verdict, _found = self._certify(run)
        missing = [
            item
            for item in verdict["discrepancies"]
            if item.get("kind") == "missing_object"
        ]
        return {
            "run_id": run_id,
            "status": run["status"],
            "checked_objects": verdict["objects_expected"],
            "objects_found": verdict["objects_found"],
            "all_present": (
                verdict["certification"] != UNKNOWN and not missing
            ),
            "certification": verdict["certification"],
            "certified_at": verdict["certified_at"],
            "discrepancies": verdict["discrepancies"],
        }

    def recover(self) -> None:
        """
        Starter behavior: enough recovery for the happy-path demo.

        Operator evidence reports other cases that this does not make safe.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM deployments WHERE status = 'running'"
            ).fetchall()
        for row in rows:
            self.run_once(str(row["id"]))
