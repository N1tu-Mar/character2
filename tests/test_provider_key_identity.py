"""Provider identity, and the guards at the door that keep it well defined.

Covers ROADMAP.md CH1 (every provider key comes from one shared, length-
prefixed function over `idempotency_key` and `asset_id`), CH1b (empty
components refused at `submit`), and CH3 (zero-asset payloads refused at
`submit`). It also pins CH16 from the other side: two approved assets whose
names normalize to one stored value are both valid and must both deploy.

Nothing here certifies, cancels, claims, recovers beyond what a key needs, or
bounds attempts.

Every expectation is derived at runtime -- from the loaded payload, from
`FakeHubSpot.DISPLAY_NAME_LIMIT`, or constructed in-test. No fixture asset id,
no fixture hash, no asset count, and no literal name limit is written down, so
these tests keep their meaning against any request shape and against a change
to the provider's normalization limit.

The two helpers below are fetched by attribute rather than imported, so their
absence reports as a failure naming the missing change instead of a collection
error that would hide every other test in this file.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import relay.core
from relay import InjectedCrash, Relay
from relay.core import FakeHubSpot, _canonical_json, _digest_text

FULL_FIXTURE = Path("fixtures/deployment_request.json")
EMPTY_FIXTURE = Path("fixtures/deployment_request_empty.json")


def approved_request() -> dict:
    return json.loads(FULL_FIXTURE.read_text())


def external_key(idempotency_key: str, asset_id: str) -> str:
    """The one function that names a HubSpot object (ROADMAP §4, Identity)."""
    encode = getattr(relay.core, "external_key", None)
    if encode is None:
        raise AssertionError(
            "relay.core.external_key does not exist (CH1): a provider key must"
            " come from one shared function over idempotency_key and asset_id,"
            " never from an f-string built inline"
        )
    return encode(idempotency_key, asset_id)


def deployment_namespace(idempotency_key: str) -> str:
    """The prefix that enumerates one deployment's objects (ROADMAP §4.3)."""
    namespace = getattr(relay.core, "deployment_namespace", None)
    if namespace is None:
        raise AssertionError(
            "relay.core.deployment_namespace does not exist (CH1): the reverse"
            " scan for extras needs a prefix that belongs to exactly one"
            " deployment"
        )
    return namespace(idempotency_key)


class KeyEncodingTest(unittest.TestCase):
    """The encoding on its own, with no database and no provider.

    These are properties of the function, so the components are constructed
    here rather than taken from a fixture -- the fixtures do not contain a
    colon, which is exactly why the rule cannot be left to them (F11).
    """

    def test_a_colon_in_either_component_does_not_collide(self) -> None:
        self.assertNotEqual(
            external_key("a", "b:c"),
            external_key("a:b", "c"),
            "plain concatenation maps both of these to 'a:b:c'; two unrelated"
            " deployments would then share provider objects",
        )

    def test_the_encoding_is_injective_over_colon_heavy_components(
        self,
    ) -> None:
        components = ["a", "b", "a:b", ":a", "a:", "a::b", "1:a"]
        pairs = [(key, asset) for key in components for asset in components]

        keys = [external_key(key, asset) for key, asset in pairs]

        self.assertEqual(
            len(set(keys)),
            len(pairs),
            "distinct (idempotency_key, asset_id) pairs must produce distinct"
            " provider keys, whatever the components contain",
        )

    def test_a_deployments_keys_all_sit_under_its_own_namespace(self) -> None:
        payload = approved_request()
        key = "campaign-1"
        namespace = deployment_namespace(key)

        for asset in payload["assets"]:
            self.assertTrue(
                external_key(key, asset["asset_id"]).startswith(namespace),
                "the namespace scan must find every object it deployed",
            )

    def test_a_prefix_shaped_key_stays_out_of_another_namespace(self) -> None:
        """c3's shape, built here rather than borrowed from the log.

        `deploy-207a` and `deploy-207b` differ by a suffix; one operator key
        being a string prefix of another is ordinary. Naive prefix matching
        would attribute the longer key's objects to the shorter one.
        """
        payload = approved_request()
        shorter = "campaign-1"
        longer = shorter + "7"
        self.assertTrue(longer.startswith(shorter), "precondition: prefix shape")
        namespace = deployment_namespace(shorter)

        for asset in payload["assets"]:
            self.assertFalse(
                external_key(longer, asset["asset_id"]).startswith(namespace),
                f"{longer!r}'s objects must not be scanned as {shorter!r}'s",
            )

    def test_the_payload_hash_is_not_part_of_a_provider_key(self) -> None:
        """It guards the door (rule 12). It does not name the object."""
        payload = approved_request()
        digest = _digest_text(_canonical_json(payload))

        for asset in payload["assets"]:
            self.assertNotIn(
                digest,
                external_key("campaign-1", asset["asset_id"]),
                "an identity that moved with the serialization of a payload"
                " would silently acquire a second set of drafts",
            )

    def test_the_display_name_is_not_part_of_a_provider_key(self) -> None:
        payload = approved_request()

        for asset in payload["assets"]:
            self.assertNotIn(
                asset["display_name"].strip(),
                external_key("campaign-1", asset["asset_id"]),
                "a name is a field to compare, never a thing to identify by",
            )


class RelayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.db_path = root / "deployments.db"
        self.provider_path = root / "fake-hubspot.json"
        self.relay = Relay(self.db_path, self.provider_path)
        self.key = "campaign-1"

    def held_keys(self) -> set[str]:
        return {
            obj["external_key"] for obj in self.relay.provider.list_objects()
        }

    def approved_keys(self, idempotency_key: str, payload: dict) -> set[str]:
        """What the provider should be holding, computed from the approval.

        Only the operator's key and each asset id are in scope here. Nothing
        about the run, the payload hash, or any name reaches this line, so an
        object found under one of these keys was named by the approval alone.
        """
        return {
            external_key(idempotency_key, asset["asset_id"])
            for asset in payload["assets"]
        }

    def all_rows(self) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        with contextlib.closing(connection):
            return connection.execute(
                "SELECT id, idempotency_key FROM deployments"
            ).fetchall()


class ProviderKeyIdentityTest(RelayTestCase):
    """CH1 — the run is an attempt, and an attempt does not name an object."""

    def test_deployed_objects_are_named_by_the_approval_not_the_run(
        self,
    ) -> None:
        payload = approved_request()
        run_id = self.relay.submit(self.key, payload)

        self.relay.run_once(run_id)

        self.assertEqual(
            self.held_keys(),
            self.approved_keys(self.key, payload),
            "HubSpot must hold exactly the objects the approval names",
        )
        for key in self.held_keys():
            self.assertNotIn(
                run_id,
                key,
                "a key carrying the run id mints a new set of drafts on every"
                " retry and every recovery pass (G2)",
            )

    def test_the_keys_survive_a_crash_a_recovery_and_a_retry(self) -> None:
        payload = approved_request()
        expected = self.approved_keys(self.key, payload)
        run_id = self.relay.submit(self.key, payload)

        with self.assertRaises(InjectedCrash):
            self.relay.run_once(run_id, crash_at="after_first_provider_write")

        Relay(self.db_path, self.provider_path).recover()
        self.assertEqual(
            self.held_keys(),
            expected,
            "a recovered run must land on the objects it already wrote",
        )

        self.relay.run_once(self.relay.retry(run_id))
        self.assertEqual(
            self.held_keys(),
            expected,
            "an operator's retry must land on the same objects again",
        )
        self.assertEqual(
            len(self.relay.provider.list_objects()),
            len(payload["assets"]),
            "three executions of one approval, one set of drafts",
        )

    def test_a_namespace_scan_returns_only_its_own_objects(self) -> None:
        """Two deployments, one key a string prefix of the other.

        Two different keys are two different deployments by design (ROADMAP
        §2, c3), so both sets of objects are correct. What must not happen is
        one deployment's scan claiming the other's.
        """
        payload = approved_request()
        theirs = self.key + "7"
        self.relay.run_once(self.relay.submit(self.key, payload))
        self.relay.run_once(self.relay.submit(theirs, payload))

        namespace = deployment_namespace(self.key)
        scanned = {
            obj["external_key"]
            for obj in self.relay.provider.list_objects()
            if obj["external_key"].startswith(namespace)
        }

        self.assertEqual(scanned, self.approved_keys(self.key, payload))
        self.assertEqual(
            len(self.relay.provider.list_objects()),
            2 * len(payload["assets"]),
            "precondition: both deployments really did write",
        )

    def test_an_object_is_named_without_consulting_the_payload(self) -> None:
        """Identity is (idempotency_key, asset_id) and nothing else.

        The expected keys are computed from two strings. If the payload hash
        or a display name had entered the key, an object deployed from the
        full approval would not be found at the key computed without them.
        """
        payload = approved_request()
        asset_ids = [asset["asset_id"] for asset in payload["assets"]]
        self.relay.run_once(self.relay.submit(self.key, payload))

        blind = {external_key(self.key, asset_id) for asset_id in asset_ids}

        self.assertEqual(self.held_keys(), blind)


class DisplayNameIsNotIdentityTest(RelayTestCase):
    """CH16's standing guard — a shared stored name is legitimate (F10, I6)."""

    def colliding_assets(self, payload: dict) -> list[dict]:
        """Two assets the provider will normalize to one stored name.

        Built from the provider's published limit rather than from the
        fixture, so the case survives a change to `DISPLAY_NAME_LIMIT` that
        would stop `deployment_request.json` colliding.
        """
        template = payload["assets"][0]
        shared = "N" * FakeHubSpot.DISPLAY_NAME_LIMIT
        assets = []
        for suffix in ("-one", "-two"):
            asset = dict(template)
            asset["asset_id"] = f"{template['asset_id']}{suffix}"
            asset["display_name"] = f"{shared}{suffix}"
            asset["source_sha256"] = _digest_text(asset["asset_id"])
            assets.append(asset)
        return assets

    def test_two_assets_sharing_a_normalized_name_both_deploy(self) -> None:
        payload = approved_request()
        payload["assets"] = self.colliding_assets(payload)
        shared = "N" * FakeHubSpot.DISPLAY_NAME_LIMIT

        self.relay.run_once(self.relay.submit(self.key, payload))

        held = self.relay.provider.list_objects()
        self.assertEqual(
            {obj["display_name"] for obj in held},
            {shared},
            "precondition: the provider normalizes both names to one value",
        )
        self.assertEqual(
            len(held),
            len(payload["assets"]),
            "two distinct approved assets are two drafts, shared name or not",
        )
        self.assertEqual(
            self.held_keys(),
            self.approved_keys(self.key, payload),
            "the two are told apart by their keys, never by their names",
        )


class SubmitDoorTest(RelayTestCase):
    """CH1b and CH3 — what `submit` refuses before a run exists.

    `ValueError` is asserted rather than a new named exception so that an
    implementation is free to raise a subclass of it. `IdempotencyConflict`
    would be the wrong answer here: nothing about these requests conflicts
    with an earlier one, they are malformed on their own.
    """

    def assertRefused(self, idempotency_key: str, payload: dict) -> None:
        with self.assertRaises(ValueError):
            self.relay.submit(idempotency_key, payload)
        self.assertEqual([], self.all_rows(), "a refused request gets no run")
        self.assertEqual(
            [],
            self.relay.provider.list_objects(),
            "a refused request writes nothing to the provider",
        )

    def test_an_empty_idempotency_key_is_refused(self) -> None:
        """An empty key makes the namespace prefix a valid object key."""
        self.assertRefused("", approved_request())

    def test_an_empty_asset_id_is_refused(self) -> None:
        payload = approved_request()
        payload["assets"][0]["asset_id"] = ""

        self.assertRefused(self.key, payload)

    def test_a_zero_asset_payload_is_refused(self) -> None:
        """A deployment of nothing is not a deployment (ROADMAP §4.10)."""
        payload = approved_request()
        payload["assets"] = []

        self.assertRefused(self.key, payload)

    def test_the_zero_asset_fixture_is_refused_by_the_same_rule(self) -> None:
        self.assertRefused(self.key, json.loads(EMPTY_FIXTURE.read_text()))


if __name__ == "__main__":
    unittest.main()
