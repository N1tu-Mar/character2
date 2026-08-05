"""Certification -- what a fresh look at HubSpot says about a deployment now.

Covers ROADMAP.md CH5 (the `verified` literal is replaced by a computed
certification of `complete` / `divergent` / `unknown`, with `certified_at` and a
`discrepancies` list) and CH5b (a `KeyError` out of `read()` means "we looked and
it is not there" and certifies `divergent`; a state file that cannot be parsed or
opened means "we could not look" and certifies `unknown` -- the two must not
share an `except` clause).

Everything here goes through the public surface: `submit`, `run_once`, and
`audit`. Divergence is produced the way the operator's report describes it --
HubSpot's contents change underneath a finished run -- by editing the provider's
own state file, exactly as `demo.py` does. Where an exception has to come out of
the provider, it comes from a wrapper placed *around* `FakeHubSpot`; the provider
itself is never modified, never subclassed, and never monkeypatched (TASK.md).

Every expectation is derived at runtime from the approved payload or from
`FakeHubSpot.DISPLAY_NAME_LIMIT`. No fixture asset id, no fixture hash, no asset
count, and no literal name limit appears below, so these tests keep their meaning
against any request shape and against a change to the provider's normalization.

Provider keys are never constructed here either. Objects are located in provider
state by their `source_asset_id`, so these tests hold both before and after CH1
moves the key off `run_id` -- a certification test that has to know the key
encoding would fail for a reason that has nothing to do with certification.

Nothing here submits twice, retries, cancels, claims, or recovers.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from relay import Relay
from relay.core import FakeHubSpot, _canonical_json, _digest_text

FULL_FIXTURE = Path("fixtures/deployment_request.json")

CERTIFICATIONS = {"complete", "divergent", "unknown"}

# The fields that identify a deployed object and record where it came from
# (ROADMAP §4.4, §4.5). Altering any one of them in HubSpot means the draft in
# front of us is no longer the draft we were approved to write.
IDENTITY_FIELDS = (
    "external_key",
    "source_asset_id",
    "source_sha256",
    "object_type",
    "status",
    "display_name",
)


def approved_request() -> dict[str, Any]:
    return json.loads(FULL_FIXTURE.read_text())


class ProviderWrapper:
    """Simulation of provider failure, placed around the provider.

    `FakeHubSpot` is treated as HubSpot: not ours to edit, and not ours to
    subclass into something that behaves differently. Everything below wraps an
    instance and delegates, so the object under test is still the provider the
    starter shipped.
    """

    def __init__(self, provider: FakeHubSpot) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class UnreadableProvider(ProviderWrapper):
    """A provider whose state cannot be read at all.

    Both readers fail, because that is what a torn or half-written state file
    (F8) actually looks like -- there is no view of the objects to be had, so no
    per-object claim is possible in either direction.
    """

    def __init__(self, provider: FakeHubSpot, error: BaseException) -> None:
        super().__init__(provider)
        self._error = error

    def read(self, external_key: str) -> dict[str, str]:
        raise self._error

    def list_objects(self) -> list[dict[str, str]]:
        raise self._error


class ProviderMissingOneObject(ProviderWrapper):
    """A readable provider that has lost exactly one object.

    `read` raises `KeyError` for that key and the listing omits it, which is the
    coherent shape: a state file that parsed fine and does not contain the
    object. Anything less coherent -- a listing that shows an object `read`
    denies -- is a state the real provider cannot be in, and asserting on it
    would pin an implementation detail rather than a behavior.
    """

    def __init__(self, provider: FakeHubSpot, missing_key: str) -> None:
        super().__init__(provider)
        self._missing_key = missing_key

    def read(self, external_key: str) -> dict[str, str]:
        if external_key == self._missing_key:
            raise KeyError(external_key)
        return self._provider.read(external_key)

    def list_objects(self) -> list[dict[str, str]]:
        return [
            obj
            for obj in self._provider.list_objects()
            if obj["external_key"] != self._missing_key
        ]


class CountingProvider(ProviderWrapper):
    """Counts how often the provider was actually consulted."""

    def __init__(self, provider: FakeHubSpot) -> None:
        super().__init__(provider)
        self.reads = 0

    def read(self, external_key: str) -> dict[str, str]:
        self.reads += 1
        return self._provider.read(external_key)

    def list_objects(self) -> list[dict[str, str]]:
        self.reads += 1
        return self._provider.list_objects()


class Deployment:
    """One approved request, deployed, plus direct access to provider state.

    Editing the state file is how HubSpot changing underneath a finished run is
    simulated. It is the same move `demo.py` makes, and it is deliberately done
    behind the provider's back -- the point of certification is that it notices.
    """

    def __init__(
        self,
        relay: Relay,
        run_id: str,
        payload: dict[str, Any],
        provider_path: Path,
    ) -> None:
        self.relay = relay
        self.run_id = run_id
        self.payload = payload
        self.provider_path = provider_path

    def asset_ids(self) -> list[str]:
        return [asset["asset_id"] for asset in self.payload["assets"]]

    def state(self) -> dict[str, dict[str, str]]:
        return json.loads(self.provider_path.read_text())

    def write_state(self, objects: dict[str, dict[str, str]]) -> None:
        self.provider_path.write_text(_canonical_json(objects))

    def key_of(self, asset_id: str) -> str:
        """Find a deployed object by the asset it came from, not by its key."""
        for key, stored in self.state().items():
            if stored["source_asset_id"] == asset_id:
                return key
        raise AssertionError(
            f"precondition: no provider object was deployed for {asset_id!r}"
        )

    def stored(self, asset_id: str) -> dict[str, str]:
        return self.state()[self.key_of(asset_id)]

    def audit(self) -> dict[str, Any]:
        return self.relay.audit(self.run_id)


class CertificationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.key = "campaign-1"

    def deploy(self, payload: dict[str, Any] | None = None) -> Deployment:
        """Submit and run one approved request in its own database.

        A fresh directory per call, so a test that mutates provider state for
        one case cannot leak into the next.
        """
        payload = approved_request() if payload is None else payload
        root = Path(tempfile.mkdtemp(dir=self.root))
        provider_path = root / "fake-hubspot.json"
        relay = Relay(root / "deployments.db", provider_path)
        run_id = relay.submit(self.key, payload)
        relay.run_once(run_id)
        deployment = Deployment(relay, run_id, payload, provider_path)
        self.assertEqual(
            len(deployment.state()),
            len(payload["assets"]),
            "precondition: every approved asset reached the provider",
        )
        return deployment

    # -- the certification report -------------------------------------------

    def report_for(self, deployment: Deployment) -> dict[str, Any]:
        """The operator's "Check again", with the shape CH5 requires.

        Fetched from the returned report rather than imported, so a missing
        certification reports as a failure naming the change instead of an
        error that says nothing about why.
        """
        report = deployment.audit()
        if "certification" not in report:
            raise AssertionError(
                "audit() reports no 'certification' (CH5): an operator-facing"
                " check must say complete, divergent, or unknown, computed from"
                " a fresh readback -- never a stored 'verified' literal"
            )
        self.assertIn(
            report["certification"],
            CERTIFICATIONS,
            "certification must be one of the three defined verdicts",
        )
        self.assertTrue(
            report.get("certified_at"),
            "a certification is point-in-time and must say when it was taken"
            " (ROADMAP §4.6, §4.9)",
        )
        self.assertIsInstance(
            report.get("discrepancies"),
            list,
            "certification must carry the list of what it found wrong",
        )
        return report

    def assertCertifies(
        self,
        deployment: Deployment,
        expected: str,
        message: str,
    ) -> dict[str, Any]:
        report = self.report_for(deployment)
        self.assertEqual(report["certification"], expected, message)
        if expected == "complete":
            self.assertEqual(
                report["discrepancies"],
                [],
                "a complete deployment has nothing to name",
            )
        else:
            self.assertFalse(
                report.get("verified"),
                "nothing may report success alongside a verdict that is not"
                " complete -- that is the false pass this change removes (F1)",
            )
        if expected == "divergent":
            self.assertNotEqual(
                report["discrepancies"],
                [],
                "divergent must name what differs, not just say so",
            )
        return report

    def assertNames(self, report: dict[str, Any], needle: str) -> None:
        self.assertIn(
            needle,
            _canonical_json(report["discrepancies"]),
            f"the discrepancies must name {needle!r} so an operator knows what"
            " to look at in HubSpot",
        )


class CompleteDeploymentTest(CertificationTestCase):
    """What a correct deployment is entitled to say (ROADMAP §4.1-4.5)."""

    def test_a_correct_full_deployment_certifies_complete(self) -> None:
        deployment = self.deploy()

        report = self.assertCertifies(
            deployment,
            "complete",
            "a deployment that wrote every approved asset and was not touched"
            " afterwards must certify complete",
        )

        self.assertEqual(
            report["run_id"],
            deployment.run_id,
            "the certification belongs to the deployment that was asked about",
        )
        self.assertEqual(
            {stored["source_asset_id"] for stored in deployment.state().values()},
            set(deployment.asset_ids()),
            "precondition: the provider holds exactly the approved assets",
        )

    def test_names_past_the_limit_certify_complete(self) -> None:
        """Normalization is the provider's rule, not a discrepancy (M8).

        The names are built from the provider's published limit rather than
        taken from the fixture, so the case survives a change to
        `DISPLAY_NAME_LIMIT` that would stop the fixture exceeding it. The
        expected stored value is never recomputed here -- doing so would just
        restate the provider's rule in the test. What is asserted is that
        truncation demonstrably happened and certification still says complete.
        """
        payload = approved_request()
        overlong = "N" * (FakeHubSpot.DISPLAY_NAME_LIMIT * 2)
        for asset in payload["assets"]:
            asset["display_name"] = f"{asset['asset_id']} {overlong}   "

        deployment = self.deploy(payload)

        for asset in payload["assets"]:
            stored_name = deployment.stored(asset["asset_id"])["display_name"]
            self.assertNotEqual(
                stored_name,
                asset["display_name"],
                "precondition: the provider did normalize this name",
            )
            self.assertLessEqual(
                len(stored_name),
                FakeHubSpot.DISPLAY_NAME_LIMIT,
                "precondition: the stored name is the truncated form",
            )
        self.assertCertifies(
            deployment,
            "complete",
            "an approved name longer than the provider stores is expected;"
            " comparing against the raw approved string would invent a"
            " discrepancy the operator cannot act on (M8)",
        )

    def test_two_assets_sharing_a_normalized_name_certify_complete(
        self,
    ) -> None:
        """CH16's guard on the certification side (F10, I6, M7).

        `deployment_request.json` is shaped so two approved assets normalize to
        one stored name. Those are two distinct, correctly deployed drafts, and
        a certification that treated a shared name as a collision would report a
        valid approval as divergent. The colliding pair is constructed from the
        provider's limit rather than read out of the fixture, so the case does
        not depend on the fixture continuing to collide.
        """
        payload = approved_request()
        template = payload["assets"][0]
        shared = "S" * FakeHubSpot.DISPLAY_NAME_LIMIT
        colliding = []
        for suffix in ("-one", "-two"):
            asset = dict(template)
            asset["asset_id"] = f"{template['asset_id']}{suffix}"
            asset["display_name"] = f"{shared}{suffix}"
            asset["source_sha256"] = _digest_text(asset["asset_id"])
            colliding.append(asset)
        payload["assets"] = colliding

        deployment = self.deploy(payload)

        self.assertEqual(
            len({stored["display_name"] for stored in deployment.state().values()}),
            1,
            "precondition: the provider normalized both names to one value",
        )
        self.assertCertifies(
            deployment,
            "complete",
            "two approved assets that happen to share a stored label are two"
            " correct drafts; a name is compared, never used to tell objects"
            " apart (ROADMAP §4, Identity)",
        )


class DivergenceTest(CertificationTestCase):
    """HubSpot no longer holds what was approved (ROADMAP §4.7)."""

    def test_a_deleted_object_certifies_divergent(self) -> None:
        """The operator's "once there were fewer" (F2, c8)."""
        deployment = self.deploy()
        removed = deployment.asset_ids()[0]
        objects = deployment.state()
        del objects[deployment.key_of(removed)]
        deployment.write_state(objects)

        report = self.assertCertifies(
            deployment,
            "divergent",
            "an approved asset that is no longer in HubSpot is the whole"
            " complaint; a check that still reports success is the defect",
        )

        self.assertNames(report, removed)
        self.assertEqual(
            self.relay_status(deployment),
            "done",
            "history does not change when HubSpot does (ROADMAP §4, two axes)",
        )

    def relay_status(self, deployment: Deployment) -> str:
        return str(deployment.relay.get(deployment.run_id)["status"])

    def test_a_key_error_is_divergent_and_an_unreadable_state_is_unknown(
        self,
    ) -> None:
        """CH5b, pinned from both sides in one test (M11, M11b).

        Both verdicts come out of the same readback as exceptions and they mean
        opposite things. Asserted together so that no single `except` clause can
        satisfy one by absorbing the other.
        """
        deployment = self.deploy()
        absent = deployment.asset_ids()[0]
        missing_key = deployment.key_of(absent)
        provider = deployment.relay.provider

        deployment.relay.provider = ProviderMissingOneObject(
            provider,
            missing_key,
        )
        report = self.assertCertifies(
            deployment,
            "divergent",
            "a KeyError is a successful read with a negative answer -- we"
            " looked, and the object is gone (M11b)",
        )
        self.assertNames(report, absent)

        deployment.relay.provider = UnreadableProvider(
            provider,
            json.JSONDecodeError("Expecting value", "", 0),
        )
        self.assertCertifies(
            deployment,
            "unknown",
            "the same provider state read a moment later is unparseable; that"
            " is 'we could not look', not data loss (M11)",
        )

    def test_altering_an_identity_field_certifies_divergent(self) -> None:
        """M6 -- each field that identifies a draft, checked on its own.

        A fresh deployment per field, so one alteration cannot be reported by a
        check that was really noticing another.
        """
        for field in IDENTITY_FIELDS:
            with self.subTest(field=field):
                deployment = self.deploy()
                altered = deployment.asset_ids()[0]
                key = deployment.key_of(altered)
                objects = deployment.state()
                original = objects[key][field]
                objects[key][field] = (
                    _digest_text(original)
                    if field == "source_sha256"
                    else f"{original}-altered"
                )
                self.assertNotEqual(
                    objects[key][field],
                    original,
                    "precondition: the field really was changed",
                )
                deployment.write_state(objects)

                report = self.assertCertifies(
                    deployment,
                    "divergent",
                    f"{field} no longer matches what was approved; the draft in"
                    " HubSpot is not the draft we were approved to write",
                )

                self.assertNames(report, field)

    def test_a_stranger_in_the_namespace_certifies_divergent(self) -> None:
        """M17 -- the reverse scan, for objects nobody approved.

        The stray key is derived by extending a key this deployment really
        wrote, so it lands inside this deployment's namespace under any key
        encoding -- including after CH1 changes it.
        """
        deployment = self.deploy()
        objects = deployment.state()
        adopted = objects[deployment.key_of(deployment.asset_ids()[0])]
        stray_key = f"{adopted['external_key']}-stray"
        stray = dict(adopted)
        stray["external_key"] = stray_key
        stray["object_id"] = f"hs-{_digest_text(stray_key)[:12]}"
        objects[stray_key] = stray
        deployment.write_state(objects)

        report = self.assertCertifies(
            deployment,
            "divergent",
            "certification iterating the approved assets alone would never see"
            " an extra draft; a deployment is the whole namespace, not just the"
            " objects we went looking for (ROADMAP §4.3)",
        )

        self.assertNames(report, stray_key)


class UnknownTest(CertificationTestCase):
    """We could not look (ROADMAP §4, Missing is not unreadable; F8)."""

    def assertUnknown(self, deployment: Deployment) -> None:
        self.assertCertifies(
            deployment,
            "unknown",
            "a provider we cannot read supports no claim about HubSpot in"
            " either direction; reporting divergent would call a transient"
            " read failure data loss, and complete would be a fabrication",
        )

    def test_a_half_written_state_file_certifies_unknown(self) -> None:
        """A torn read, produced for real rather than simulated (F8)."""
        deployment = self.deploy()
        intact = deployment.provider_path.read_text()
        deployment.provider_path.write_text(intact[: len(intact) // 2])

        with self.assertRaises(json.JSONDecodeError):
            json.loads(deployment.provider_path.read_text())
        self.assertUnknown(deployment)

    def test_a_json_decode_error_out_of_the_provider_certifies_unknown(
        self,
    ) -> None:
        deployment = self.deploy()
        deployment.relay.provider = UnreadableProvider(
            deployment.relay.provider,
            json.JSONDecodeError("Expecting value", "", 0),
        )

        self.assertUnknown(deployment)

    def test_provider_state_that_cannot_be_opened_certifies_unknown(
        self,
    ) -> None:
        """An `OSError`, from a state file that is genuinely unopenable.

        Not a wrapper: the path is replaced with a directory, so the provider's
        own read raises `IsADirectoryError` on its own terms.
        """
        deployment = self.deploy()
        deployment.provider_path.unlink()
        deployment.provider_path.mkdir()

        with self.assertRaises(OSError):
            deployment.provider_path.read_text()
        self.assertUnknown(deployment)

    def test_an_oserror_out_of_the_provider_certifies_unknown(self) -> None:
        deployment = self.deploy()
        deployment.relay.provider = UnreadableProvider(
            deployment.relay.provider,
            OSError("provider state could not be opened"),
        )

        self.assertUnknown(deployment)


class FreshnessTest(CertificationTestCase):
    """Certification is a reading, not a recollection (ROADMAP §4.6, M9, M10)."""

    def test_certification_consults_the_provider(self) -> None:
        deployment = self.deploy()
        counter = CountingProvider(deployment.relay.provider)
        deployment.relay.provider = counter

        self.assertCertifies(
            deployment,
            "complete",
            "a correct deployment still certifies complete when the provider"
            " is watched",
        )

        self.assertGreater(
            counter.reads,
            0,
            "a certification that never opened the provider is a recital of"
            " the receipt, which is exactly the starter's false pass (F2, M9)",
        )

    def test_a_stored_receipt_does_not_survive_a_change_in_hubspot(
        self,
    ) -> None:
        """M9 and M10 -- the stored record is history and cannot be the verdict.

        The receipt is deliberately left untouched. It still lists every object
        the run wrote, and it must not be able to carry that claim forward past
        a deletion.
        """
        deployment = self.deploy()
        before = deployment.relay.get(deployment.run_id)
        self.assertCertifies(
            deployment,
            "complete",
            "precondition: the deployment certifies complete before HubSpot"
            " changes",
        )

        removed = deployment.asset_ids()[0]
        objects = deployment.state()
        del objects[deployment.key_of(removed)]
        deployment.write_state(objects)

        report = self.assertCertifies(
            deployment,
            "divergent",
            "the second check reads HubSpot again and reaches a different"
            " verdict from the same stored record",
        )

        self.assertNames(report, removed)
        after = deployment.relay.get(deployment.run_id)
        self.assertEqual(
            _canonical_json(before),
            _canonical_json(after),
            "certification recomputes; it never writes its verdict back over"
            " the run's history (ROADMAP §4, two axes)",
        )


if __name__ == "__main__":
    unittest.main()
