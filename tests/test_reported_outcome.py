"""What every operator-facing surface is allowed to say, and when a run fails.

Covers ROADMAP.md §4.9 and M13 (no surface may present a workflow status as
evidence about HubSpot -- every surface that reports status also reports a
certification and when it was computed), and ROADMAP.md §4.8, §4.14 with CH6
(a run that finishes its writes but cannot certify `complete` ends `failed`,
`recover()` never reclaims it, and `retry` is the only way out).

`tests/test_certification.py` drives `audit`. Nothing drives
`deployment_summary`, which is the panel `make demo` prints and the one an
operator reads first. An implementation that adds certification to `audit`
alone passes that file completely while the main panel still prints a bare
`verified: true` over a HubSpot that has lost objects. This file closes that
hole, and pins the completion-time half of the rule that the same file leaves
open: every test there changes HubSpot *after* `run_once` returned, so nothing
yet says what a run must do when it cannot certify its own work.

Everything goes through the public surface: `submit`, `run_once`, `get`,
`audit`, `deployment_summary`, `recover`, `retry`. Divergence is produced by
a wrapper placed *around* `FakeHubSpot` and by editing the provider's own
state file -- the provider is never modified, subclassed, or monkeypatched
(TASK.md).

Provider keys are never constructed here. Objects are found by their
`source_asset_id`, so these tests hold under any key encoding. Every
expectation is derived from the approved payload at runtime; no fixture asset
id, hash, or asset count appears below.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from relay import Relay

FIXTURE = Path("fixtures/deployment_request.json")

CERTIFICATIONS = {"complete", "divergent", "unknown"}

# The surfaces an operator reads. Both must answer the same way about HubSpot;
# which one you happen to open cannot change what you are told.
SURFACES = ("deployment_summary", "audit")


def approved_request() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


class ProviderWrapper:
    """Delegates to the provider the starter shipped, and changes nothing."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class CountingProvider(ProviderWrapper):
    """Counts every read of provider state."""

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        self.reads = 0

    def read(self, external_key: str) -> dict[str, str]:
        self.reads += 1
        return self._provider.read(external_key)

    def list_objects(self) -> list[dict[str, str]]:
        self.reads += 1
        return self._provider.list_objects()


class UnreadableProvider(ProviderWrapper):
    """A provider whose state cannot be read at all (F8)."""

    def __init__(self, provider: Any, error: BaseException) -> None:
        super().__init__(provider)
        self._error = error

    def read(self, external_key: str) -> dict[str, str]:
        raise self._error

    def list_objects(self) -> list[dict[str, str]]:
        raise self._error


class LosesAnObjectMidRun(ProviderWrapper):
    """A concurrent writer erases one object while this run is still writing.

    This is the c8 mechanism, made deterministic and placed strictly *before*
    the run decides its own outcome: the erase happens on the last write, so
    the earlier readback already succeeded and the receipt will still list
    every object. What the run reports at completion is therefore a claim it
    can no longer support -- which is the whole point of rule 8.
    """

    def __init__(
        self,
        provider: Any,
        state_path: Path,
        erase_asset_id: str,
        writes_expected: int,
    ) -> None:
        super().__init__(provider)
        self._state_path = state_path
        self._erase_asset_id = erase_asset_id
        self._writes_expected = writes_expected
        self.writes = 0

    def create_draft(self, *, external_key: str, asset: dict) -> dict:
        created = self._provider.create_draft(
            external_key=external_key,
            asset=asset,
        )
        self.writes += 1
        if self.writes == self._writes_expected:
            self._erase()
        return created

    def _erase(self) -> None:
        objects = json.loads(self._state_path.read_text())
        for key, stored in list(objects.items()):
            if stored["source_asset_id"] == self._erase_asset_id:
                del objects[key]
        self._state_path.write_text(json.dumps(objects))


class ReportedOutcomeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.db_path = root / "deployments.db"
        self.provider_path = root / "fake-hubspot.json"
        self.relay = Relay(self.db_path, self.provider_path)
        self.key = "campaign-1"
        self.payload = approved_request()
        self.assets = self.payload["assets"]
        self.run_id = self.relay.submit(self.key, self.payload)

    # -- provider state -------------------------------------------------------

    def provider_state(self) -> dict[str, dict[str, str]]:
        return json.loads(self.provider_path.read_text())

    def write_provider_state(self, objects: dict[str, dict[str, str]]) -> None:
        self.provider_path.write_text(json.dumps(objects))

    def key_of(self, asset_id: str) -> str:
        """Locate a deployed object by the asset it came from, not by its key."""
        for key, stored in self.provider_state().items():
            if stored["source_asset_id"] == asset_id:
                return key
        raise AssertionError(
            f"precondition: nothing was deployed for {asset_id!r}"
        )

    def held(self) -> int:
        return len(self.relay.provider.list_objects())

    def deploy(self) -> None:
        self.relay.run_once(self.run_id)
        self.assertEqual(
            self.held(),
            len(self.assets),
            "precondition: every approved asset reached the provider",
        )

    def delete_one_object(self) -> str:
        """Take an object out of HubSpot behind the service's back."""
        asset_id = str(self.assets[0]["asset_id"])
        objects = self.provider_state()
        del objects[self.key_of(asset_id)]
        self.write_provider_state(objects)
        return asset_id

    # -- surfaces -------------------------------------------------------------

    def report(self, surface: str, relay: Relay | None = None) -> dict[str, Any]:
        relay = self.relay if relay is None else relay
        return getattr(relay, surface)(self.run_id)

    def certification_of(
        self,
        surface: str,
        relay: Relay | None = None,
    ) -> dict[str, Any]:
        """The verdict a surface gives, with the shape §4.9 requires.

        Read off the returned report rather than imported, so a surface that
        never grew a certification reports as a failure naming the change
        instead of an error that says nothing about why.
        """
        report = self.report(surface, relay)
        if "certification" not in report:
            raise AssertionError(
                f"{surface}() reports no 'certification' (CH7, ROADMAP §4.9,"
                " M13): a workflow status is history and is not evidence about"
                " what HubSpot holds now. Every surface that reports a status"
                " must report a verdict computed from a fresh readback"
            )
        self.assertIn(
            report["certification"],
            CERTIFICATIONS,
            f"{surface}() must certify complete, divergent, or unknown",
        )
        self.assertTrue(
            report.get("certified_at"),
            f"{surface}() must say when it looked; a verdict with no time on"
            " it cannot be told apart from a stored one (§4.6)",
        )
        return report

    def assertSurfaceCertifies(
        self,
        surface: str,
        expected: str,
        message: str,
    ) -> dict[str, Any]:
        report = self.certification_of(surface)
        self.assertEqual(report["certification"], expected, message)
        if expected != "complete":
            self.assertFalse(
                report.get("verified"),
                f"{surface}() must not carry a success flag beside a"
                " {expected} verdict -- that is the false pass this removes",
            )
        return report


class DeploymentSummaryTest(ReportedOutcomeTestCase):
    """The panel `make demo` prints, and the first thing an operator reads."""

    def test_the_summary_certifies_a_correct_deployment_complete(self) -> None:
        self.deploy()

        report = self.assertSurfaceCertifies(
            "deployment_summary",
            "complete",
            "a deployment that wrote every approved asset and was not touched"
            " afterwards must certify complete",
        )

        self.assertEqual(report["status"], "done", "history is still reported")
        self.assertEqual(
            report["assets_approved"],
            len(self.assets),
            "the summary still says how much was approved",
        )

    def test_the_summary_certifies_divergent_when_hubspot_lost_an_object(
        self,
    ) -> None:
        """The operator's whole complaint, on the surface they actually read.

        `status` is history and does not change when HubSpot does. What must
        change is the verdict -- otherwise there is no way to tell that what
        HubSpot holds is not what this service says it deployed.
        """
        self.deploy()
        missing = self.delete_one_object()
        self.assertEqual(
            self.held(),
            len(self.assets) - 1,
            "precondition: HubSpot really is short one object",
        )

        report = self.assertSurfaceCertifies(
            "deployment_summary",
            "divergent",
            "a summary that still reports success over a missing draft is the"
            " defect; 'done' describes what the run did, never what HubSpot"
            " holds now (§4.9)",
        )

        self.assertEqual(
            report["status"],
            "done",
            "the run really did finish; history is not rewritten (§4, two axes)",
        )
        self.assertIn(
            missing,
            json.dumps(report.get("discrepancies", [])),
            "the summary must name the asset that is missing, so an operator"
            " knows what to look for in HubSpot",
        )

    def test_the_summary_never_reports_more_objects_than_hubspot_holds(
        self,
    ) -> None:
        """`objects_deployed` is a count of a stored receipt today.

        It survives a deletion untouched, which is how a panel comes to read
        `objects_deployed: 4` over a HubSpot holding two. Whatever the field
        ends up meaning, it may not exceed what a fresh look can find.
        """
        self.deploy()
        self.delete_one_object()

        report = self.report("deployment_summary")

        self.assertLessEqual(
            report["objects_deployed"],
            self.held(),
            "no operator-facing count may claim more drafts than HubSpot has",
        )

    def test_every_surface_answers_the_same_way_about_hubspot(self) -> None:
        """Which panel you open cannot change what you are told (§4.9)."""
        self.deploy()
        self.delete_one_object()

        verdicts = {
            surface: self.certification_of(surface)["certification"]
            for surface in SURFACES
        }

        self.assertEqual(
            set(verdicts.values()),
            {"divergent"},
            f"the surfaces disagree about the same deployment: {verdicts}",
        )

    def test_the_summary_reads_hubspot_every_time_it_is_asked(self) -> None:
        """A verdict is a reading, not a recollection (§4.6, M9, M10).

        Counted rather than inferred: a summary that never opens the provider
        is reciting the receipt, and the second call proves the verdict is not
        cached from the first.
        """
        self.deploy()
        counter = CountingProvider(self.relay.provider)
        self.relay.provider = counter

        self.assertSurfaceCertifies(
            "deployment_summary",
            "complete",
            "precondition: the deployment certifies complete before the change",
        )
        first_pass = counter.reads
        self.assertGreater(
            first_pass,
            0,
            "a summary that never opened HubSpot cannot be reporting on it",
        )

        self.delete_one_object()

        self.assertSurfaceCertifies(
            "deployment_summary",
            "divergent",
            "the second call must reach a different verdict from the same"
            " stored record -- a cached verdict would still say complete",
        )
        self.assertGreater(
            counter.reads,
            first_pass,
            "each call takes its own reading",
        )

    def test_an_unreadable_hubspot_makes_the_summary_say_unknown(self) -> None:
        """We could not look is not the same as it is not there (CH5b)."""
        self.deploy()
        self.relay.provider = UnreadableProvider(
            self.relay.provider,
            json.JSONDecodeError("Expecting value", "", 0),
        )

        self.assertSurfaceCertifies(
            "deployment_summary",
            "unknown",
            "a provider that cannot be read supports no claim in either"
            " direction; reporting success is a fabrication and reporting"
            " divergent would call a transient read failure data loss",
        )


class FailedRunTest(ReportedOutcomeTestCase):
    """CH6 rule 8 and §4.14 -- `done` is earned, and `failed` is terminal."""

    def erasing_provider(self) -> LosesAnObjectMidRun:
        """Attach a provider that loses one object on the last write."""
        eraser = LosesAnObjectMidRun(
            self.relay.provider,
            self.provider_path,
            str(self.assets[0]["asset_id"]),
            len(self.assets),
        )
        self.relay.provider = eraser
        return eraser

    def a_failed_run(self) -> None:
        """Drive the run to the state rule 8 describes, however it gets there.

        The run finishes every write it was asked to make and then cannot
        prove the result. Whether it signals that by raising or by returning
        is not pinned here; the durable status is.
        """
        eraser = self.erasing_provider()
        try:
            self.relay.run_once(self.run_id)
        except Exception:  # noqa: BLE001 - the verdict is the status, not this
            pass
        self.assertEqual(
            eraser.writes,
            len(self.assets),
            "precondition: the run did attempt every approved asset",
        )
        self.assertEqual(
            self.held(),
            len(self.assets) - 1,
            "precondition: HubSpot is short one object at completion time",
        )

    def test_a_run_that_cannot_certify_complete_ends_failed(self) -> None:
        self.a_failed_run()

        state = self.relay.get(self.run_id)

        self.assertEqual(
            state["status"],
            "failed",
            "a run that finished its writes and cannot certify complete ends"
            " failed, not done (§4.8). Reporting done here is the service"
            " claiming more than it checked",
        )
        self.assertFalse(
            (state["receipt"] or {}).get("verified"),
            "a run that could not prove its work must not store a receipt"
            " asserting it was verified",
        )

    def test_a_failed_run_reports_divergent_on_every_surface(self) -> None:
        self.a_failed_run()

        for surface in SURFACES:
            with self.subTest(surface=surface):
                report = self.certification_of(surface)
                self.assertEqual(
                    report["certification"],
                    "divergent",
                    f"{surface}() must say what is actually wrong, not only"
                    " that the run failed",
                )

    def test_recovery_does_not_reclaim_a_failed_run(self) -> None:
        """§4.14 and M18b -- the automatic path already tried.

        Repeated passes, because the cost of getting this wrong is not one
        wrong retry but an unbounded loop that re-enters the pool every sweep.
        """
        self.a_failed_run()
        held_before = self.held()

        for _ in range(3):
            Relay(self.db_path, self.provider_path).recover()

        self.assertEqual(
            self.relay.get(self.run_id)["status"],
            "failed",
            "failed is a request for a human; recovery must leave it alone",
        )
        self.assertEqual(
            self.held(),
            held_before,
            "an unattended pass must not write to HubSpot again",
        )

    def test_retry_is_the_only_way_out_of_failed(self) -> None:
        """The operator looks, presses retry, and the run completes cleanly.

        The wrapper is removed first, standing in for the transient condition
        having passed. The re-run must land on the objects that already exist
        rather than mint a second set -- retry reopens a deployment, it does
        not create one (CH11).
        """
        self.a_failed_run()
        self.relay.provider = Relay(self.db_path, self.provider_path).provider

        reopened = self.relay.retry(self.run_id)

        self.assertEqual(reopened, self.run_id, "retry reopens this deployment")
        self.assertEqual(
            self.relay.get(self.run_id)["status"],
            "pending",
            "retry is what moves a run out of failed",
        )

        self.relay.run_once(self.run_id)

        self.assertEqual(
            self.relay.get(self.run_id)["status"],
            "done",
            "the operator-authorized attempt is allowed to succeed",
        )
        self.assertEqual(
            self.held(),
            len(self.assets),
            "the retry lands on the same objects and adds no duplicates",
        )
        self.assertSurfaceCertifies(
            "deployment_summary",
            "complete",
            "after the retry the summary certifies what HubSpot now holds",
        )


if __name__ == "__main__":
    unittest.main()
