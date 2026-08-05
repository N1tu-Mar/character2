from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# How long a worker owns a run before another worker may take it over. A
# worker that dies without letting go blocks the run for this long and no
# longer; a worker that is merely slow keeps its claim by still holding it.
LEASE_SECONDS = 30.0

# How many times the automatic path may execute one deployment before it stops
# and asks for a person. The bound is what makes recovery's per-run isolation
# safe: without it, a run that can never succeed is retried on every sweep
# forever, which is c9's head-of-line stall turned into a spin (ROADMAP §4.14).
MAX_ATTEMPTS = 3

# How many write-then-readback rounds one object gets when the provider fails
# to answer. Bounded for the same reason: a reconcile loop that never gives up
# is a worker that never returns.
RECONCILE_ATTEMPTS = 3

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


class RunNotOwned(RuntimeError):
    """This worker is not the one entitled to execute or finish this run.

    Raised when a claim is refused because another worker holds a live lease,
    and when a worker that has lost its claim tries to write or to record an
    outcome. A run belongs to one worker at a time; everyone else declines.
    """


class InvalidApprovedRequest(ValueError):
    """An approved request that cannot name a deployment or its objects."""


class ProviderUnreadable(RuntimeError):
    """The provider's state could not be opened or parsed at all.

    Raised only for "we could not look". An object that is absent from a state
    file that parsed fine is a successful read with a negative answer, and is
    never routed through here (ROADMAP §4, Missing is not unreadable).
    """


class ProviderWriteFailed(RuntimeError):
    """A write we could not confirm, and a readback that did not find it.

    The write was attempted, the provider did not answer, and reading the key
    back showed nothing there -- repeated up to the bound. This is the honest
    end of an ambiguous write: we know the object is not in HubSpot, so the
    deployment cannot be reported as done (ROADMAP CH10).
    """


class ProviderWriteDiverged(RuntimeError):
    """The key we wrote is held by something that is not what we sent.

    Reconciliation resolves "did my write land?"; it does not get to decide
    that whatever is sitting on the key will do. A mismatch here means two
    writers disagree about one object, and that is not ours to paper over.
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
        # One `Relay` is one worker. Operators run several processes against
        # the same database, so the identity has to survive being written down
        # and compared by a different process than the one that wrote it.
        self.worker_id = f"{os.getpid()}:{uuid.uuid4()}"
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
                    receipt_json TEXT,
                    owner TEXT,
                    lease_expires_at REAL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # A database written before these columns existed is still a
            # database an operator is holding runs in. Adding the columns is
            # cheaper than an error message they cannot act on.
            present = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(deployments)")
            }
            for column, definition in (
                ("owner", "TEXT"),
                ("lease_expires_at", "REAL"),
                ("attempt", "INTEGER NOT NULL DEFAULT 0"),
                ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in present:
                    connection.execute(
                        f"ALTER TABLE deployments ADD COLUMN {column}"
                        f" {definition}"
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
        """Stop a deployment, whether or not a worker is running it.

        `cancel_requested` is the durable half and the one that decides: the
        worker reads it before every provider write and before it is allowed
        to record an outcome. Writing a status alone is what made cancellation
        advisory -- a finishing worker simply wrote over it.

        Objects already in HubSpot stay there. The provider has no delete, so
        a cancellation means "no further writes", never "as if it never ran".
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE deployments
                SET cancel_requested = 1, status = 'cancelled'
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (run_id,),
            )

    def retry(self, run_id: str) -> str:
        """The admin panel's retry button for a deployment operators call stuck.

        Requeues the deployment the operator is looking at. It does not create
        a second one: an approval already has a deployment, and minting another
        under the same key is what put duplicate drafts in HubSpot.

        The stored receipt is dropped, because a requeued run has not completed
        and must not go on presenting a finished run's receipt as its own. The
        lease and the attempt count are cleared with it: this is a new,
        operator-authorized attempt, not a continuation of the exhausted one.
        """
        self.get(run_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE deployments
                SET status = 'pending',
                    receipt_json = NULL,
                    owner = NULL,
                    lease_expires_at = NULL,
                    attempt = 0,
                    cancel_requested = 0
                WHERE id = ?
                """,
                (run_id,),
            )
        return run_id

    # -- ownership ------------------------------------------------------------
    #
    # A run is executed by one worker at a time. The claim is a conditional
    # `UPDATE` and the database decides who won: `rowcount` is the answer, and
    # nothing is inferred from a prior `SELECT`. Two workers racing one pending
    # run both issue the same statement; SQLite serializes them, the second
    # one's `WHERE` no longer matches, and it updates no rows.

    def _ownership_row(self, run_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, owner, lease_expires_at, cancel_requested
                FROM deployments WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def _claim(self, run_id: str) -> None:
        """Take ownership of a run, or explain why this worker may not.

        Claimable when the run is waiting or already ours, has not been
        cancelled, and is either unowned or held under a lease that has
        lapsed. A lapsed lease is the only way a run leaves a worker that
        never came back -- which is indistinguishable, from here, from a
        worker that is merely slow.
        """
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deployments
                SET status = 'running',
                    owner = ?,
                    lease_expires_at = ?,
                    attempt = attempt + 1
                WHERE id = ?
                  AND cancel_requested = 0
                  AND status IN ('pending', 'running')
                  AND (
                        owner IS NULL
                     OR owner = ?
                     OR lease_expires_at IS NULL
                     OR lease_expires_at <= ?
                  )
                """,
                (
                    self.worker_id,
                    now + LEASE_SECONDS,
                    run_id,
                    self.worker_id,
                    now,
                ),
            )
            claimed = cursor.rowcount == 1
        if claimed:
            return

        # The claim failed. Read the row to say why -- the verdict is still the
        # `rowcount` above, this only turns it into a message.
        row = self._ownership_row(run_id)
        if int(row["cancel_requested"]):
            raise RunCancelled(
                f"deployment {run_id} was cancelled and will not be executed"
            )
        if str(row["status"]) not in ("pending", "running"):
            raise RunNotOwned(
                f"deployment {run_id} is {row['status']!r} and is not waiting"
                " to be executed"
            )
        raise RunNotOwned(
            f"deployment {run_id} is owned by {row['owner']!r} under a lease"
            " that has not lapsed"
        )

    def _require_still_ours(self, run_id: str) -> None:
        """Checked before every provider write and before any outcome.

        Ownership and cancellation are both read again here rather than
        remembered from the claim, because both can change underneath a run
        that is in the middle of writing.

        The same statement renews the lease. A lease is meant to release a run
        from a worker that stopped, not from one that is slow: a deployment
        with many assets, or a provider having a bad day, can outlast one
        lease while visibly making progress, and taking that run away would
        put two workers on it -- the fault this mechanism exists to remove.

        Renewal is driven by progress rather than by a timer, so there is no
        background thread to keep a dead worker's claim alive. A worker that
        stops making progress stops renewing, and its lease lapses on its own.
        """
        deadline = time.time() + LEASE_SECONDS
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deployments
                SET lease_expires_at = ?
                WHERE id = ?
                  AND owner = ?
                  AND cancel_requested = 0
                  AND status = 'running'
                """,
                (deadline, run_id, self.worker_id),
            )
            if cursor.rowcount == 1:
                return

        # The conditional write said no. Read the row to say why.
        row = self._ownership_row(run_id)
        if int(row["cancel_requested"]) or str(row["status"]) == "cancelled":
            raise RunCancelled(
                f"deployment {run_id} was cancelled while it was running"
            )
        if str(row["owner"] or "") != self.worker_id:
            raise RunNotOwned(
                f"deployment {run_id} is no longer owned by this worker"
                f" (now {row['owner']!r})"
            )
        raise RunNotOwned(
            f"deployment {run_id} is {row['status']!r} and is no longer this"
            " worker's to carry on with"
        )

    def _release(self, run_id: str) -> None:
        """Let go of a claim without recording an outcome.

        Called when a run exits through an exception it could report. A worker
        that is killed outright cannot run this, which is exactly what the
        lease is for.
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE deployments
                SET owner = NULL, lease_expires_at = NULL
                WHERE id = ? AND owner = ?
                """,
                (run_id, self.worker_id),
            )

    def _finish(self, run_id: str, status: str, receipt: dict[str, Any]) -> bool:
        """Record a terminal outcome, if this worker is still entitled to.

        Conditional on ownership and on the absence of a cancellation, so a
        stale worker and a cancelled run cannot be written over with `done`.
        `rowcount` decides; the caller is told whether the write landed.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deployments
                SET status = ?,
                    receipt_json = ?,
                    owner = NULL,
                    lease_expires_at = NULL
                WHERE id = ?
                  AND owner = ?
                  AND cancel_requested = 0
                  AND status = 'running'
                """,
                (status, _canonical_json(receipt), run_id, self.worker_id),
            )
            return cursor.rowcount == 1

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

    def _expected_object(
        self,
        idempotency_key: str,
        asset: dict[str, Any],
    ) -> dict[str, str]:
        """What one approved asset must look like in the provider.

        One definition, used by certification and by the reconciliation of an
        unanswered write. If those two disagreed about what a correct object
        looks like, a reconciled write would certify `divergent` immediately
        afterwards.
        """
        key = external_key(idempotency_key, str(asset["asset_id"]))
        return {
            "external_key": key,
            "source_asset_id": str(asset["asset_id"]),
            "source_sha256": str(asset["source_sha256"]),
            "object_type": str(asset["type"]),
            "display_name": self._normalized_display_name(
                asset["display_name"]
            ),
            "status": "draft",
        }

    def _expected_objects(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        """What the provider must be holding for this approval, per §4.4-4.5."""
        expected: dict[str, dict[str, str]] = {}
        for asset in payload.get("assets", []):
            want = self._expected_object(idempotency_key, asset)
            expected[want["external_key"]] = want
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

    # -- writing --------------------------------------------------------------

    def _write_draft(
        self,
        idempotency_key: str,
        asset: dict[str, Any],
    ) -> dict[str, str]:
        """Create one draft, and resolve an unanswered write by looking.

        A provider that raises has told us nothing about HubSpot. The write may
        have landed and the acknowledgement been lost -- c10's
        `gateway_timeout, retryable: true`, with the object right there on a
        readback -- or it may never have landed at all. The two are identical
        from here, so the exception is a question and the provider holds the
        answer (ROADMAP CH10, G7).

        Found and matching is a success: the provider is idempotent and the key
        is derived from the approval, not from the attempt, so the object we
        find is the object we meant to write. Absent means try again, up to
        `RECONCILE_ATTEMPTS` -- bounded, because a reconcile loop that never
        gives up is the stall this project exists to remove. Present but
        different is not ours to resolve and is raised.
        """
        want = self._expected_object(idempotency_key, asset)
        object_key = want["external_key"]
        unanswered: BaseException | None = None

        for _round in range(RECONCILE_ATTEMPTS):
            try:
                return dict(
                    self.provider.create_draft(
                        external_key=object_key,
                        asset=asset,
                    )
                )
            except IdempotencyConflict:
                # The provider answered, and its answer is that this key is
                # held by different content. Nothing ambiguous to resolve.
                raise
            except Exception as error:  # noqa: BLE001 - resolved by readback
                unanswered = error

            stored = self._read_provider_object(object_key)
            if stored is None:
                # We looked, and the write is not there. Another round.
                continue
            if all(
                str(stored.get(field)) == want[field]
                for field in IDENTITY_FIELDS
            ):
                return stored
            raise ProviderWriteDiverged(
                f"provider key {object_key!r} holds something other than the"
                f" approved asset {want['source_asset_id']!r}"
            ) from unanswered

        raise ProviderWriteFailed(
            f"{RECONCILE_ATTEMPTS} attempts to write {object_key!r} were not"
            " confirmed, and reading it back did not find it"
        ) from unanswered

    def _fail(self, run_id: str, reason: str) -> bool:
        """End a run at `failed`, naming what stopped it.

        Conditional on the run still being `running` and not cancelled, for the
        same reason `_finish` is: a worker that has lost the run does not get
        to write its outcome. No receipt of deployed objects is stored -- the
        run has none to show -- only what an operator needs to decide whether
        to press retry (§4.14).
        """
        note = {"run_id": run_id, "outcome": "failed", "reason": reason}
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deployments
                SET status = 'failed',
                    receipt_json = ?,
                    owner = NULL,
                    lease_expires_at = NULL
                WHERE id = ?
                  AND status = 'running'
                  AND cancel_requested = 0
                """,
                (_canonical_json(note), run_id),
            )
            return cursor.rowcount == 1

    def run_once(
        self,
        run_id: str,
        *,
        crash_at: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        self._claim(run_id)

        try:
            idempotency_key = str(run["idempotency_key"])
            for index, asset in enumerate(run["payload"]["assets"]):
                # Before every write, not once at the start: a cancellation
                # that lands between two writes has to stop the second one,
                # and a run taken over by another worker must stop writing on
                # its behalf.
                self._require_still_ours(run_id)
                try:
                    self._write_draft(idempotency_key, asset)
                except (ProviderWriteFailed, ProviderWriteDiverged) as error:
                    # The provider was asked, and answered. This deployment is
                    # over, and it says so where an operator can see it rather
                    # than leaving the row `running` for a worker that has
                    # already stopped (§4.8).
                    self._fail(run_id, f"{type(error).__name__}: {error}")
                    raise
                if crash_at == "after_first_provider_write" and index == 0:
                    raise InjectedCrash(
                        "crashed after provider write and before local receipt"
                    )

            # `done` is earned, not assumed (ROADMAP §4.8). The run has
            # finished every write it was asked to make; whether it may say so
            # depends on what the provider holds when it is read now, after
            # the last write.
            self._require_still_ours(run_id)
            verdict, objects = self._certify(run)
            status = "done" if verdict["certification"] == COMPLETE else "failed"
            receipt = {
                "run_id": run_id,
                "payload_sha256": run["payload_hash"],
                "objects": objects,
                "certification_at_completion": verdict,
            }
            if not self._finish(run_id, status, receipt):
                # The conditional write is the authority. Something changed
                # between the last check and this one, and whatever it was,
                # this outcome is not ours to record.
                self._require_still_ours(run_id)
                raise RunNotOwned(
                    f"deployment {run_id} would not accept an outcome from"
                    " this worker"
                )
            return receipt
        except BaseException:
            # A failure this worker can still report is a failure it can let
            # go of. A worker that is killed cannot, and the lease covers it.
            self._release(run_id)
            raise

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
        """Pick up runs that no live worker is holding.

        Selects only runs that are `running` and either unowned or held under
        a lapsed lease, so a pass never takes a run away from a worker that is
        still on it. Cancelled runs are not unfinished work and are left
        alone; `failed` is not selected either, because it is a request for a
        human and the automatic path has already had its turn (§4.14).

        A run another worker claims first is skipped rather than fought over.

        Two things make a pass safe to run unattended (CH9):

        **Isolation.** Every run is executed inside its own `try`, so one that
        cannot be deployed fails by itself. A bare loop ends on the first raise
        and abandons everything behind it -- the operator's "a stuck run seems
        to take the whole queue down with it".

        **A bound.** Isolation alone would retry a hopeless run on every sweep
        forever, which is the same head-of-line stall arriving as a spin. A run
        gets `MAX_ATTEMPTS` executions; the claim counts them, and the one that
        reaches the bound ends `failed` rather than staying in the pool. From
        there only an operator's `retry` reopens it, which is why `failed` is
        not selected below (§4.14).
        """
        now = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, attempt FROM deployments
                WHERE status = 'running'
                  AND cancel_requested = 0
                  AND (
                        owner IS NULL
                     OR lease_expires_at IS NULL
                     OR lease_expires_at <= ?
                  )
                """,
                (now,),
            ).fetchall()

        for row in rows:
            run_id = str(row["id"])
            attempts_so_far = int(row["attempt"] or 0)
            if attempts_so_far >= MAX_ATTEMPTS:
                # Its attempts were spent by workers that never came back, so
                # nothing here ever saw the failure. The bound still holds.
                self._fail(
                    run_id,
                    f"{attempts_so_far} automatic attempts were made and none"
                    " completed",
                )
                continue
            try:
                self.run_once(run_id)
            except (RunNotOwned, RunCancelled):
                # Someone else's run, or one an operator stopped. Neither is a
                # failure of this pass.
                continue
            except Exception as error:  # noqa: BLE001 - isolation is the point
                if attempts_so_far + 1 >= MAX_ATTEMPTS:
                    self._fail(run_id, f"{type(error).__name__}: {error}")
