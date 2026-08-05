"""The two checks the starter shipped, rewritten to mean something.

Both kept their subject -- crash recovery without duplicate drafts, and every
deployed object matching the asset it came from -- and both now assert against
a fresh reading of the provider rather than against the service's own record
of itself.

As shipped they passed on the unmodified starter, which is precisely the
problem: the starter reports `verified: true` over a HubSpot it never opened,
so a test that reads the receipt agrees with it. The second one was worse than
weak -- its assertions sat inside a nested loop, so a provider holding nothing
at all ran zero assertions and passed.

Expectations are derived from the approved payload at runtime, and objects are
located by `source_asset_id`, so nothing here knows the key encoding.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from relay import InjectedCrash, Relay


def approved_request() -> dict[str, Any]:
    return json.loads(Path("fixtures/deployment_request.json").read_text())


class ReportedDeploymentFailureTest(unittest.TestCase):
    def deployed_object_for(
        self,
        relay: Relay,
        asset_id: str,
    ) -> dict[str, str]:
        matches = [
            obj
            for obj in relay.provider.list_objects()
            if obj["source_asset_id"] == asset_id
        ]
        self.assertEqual(
            len(matches),
            1,
            f"{asset_id!r} must have exactly one draft in HubSpot; "
            f"found {len(matches)}",
        )
        return matches[0]

    def assertCertifies(self, report: dict[str, Any], expected: str) -> None:
        self.assertIn(
            "certification",
            report,
            "an operator-facing surface must report what a fresh look at"
            " HubSpot says, not a stored flag (ROADMAP §4.9)",
        )
        self.assertEqual(report["certification"], expected)
        self.assertTrue(
            report.get("certified_at"),
            "a certification must say when it was taken",
        )

    def test_reported_deployment_recovers_without_duplicate_drafts(
        self,
    ) -> None:
        payload = approved_request()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "deployments.db"
            provider_path = root / "fake-hubspot.json"
            relay = Relay(db_path, provider_path)
            run_id = relay.submit("reported-deployment", payload)

            with self.assertRaises(InjectedCrash):
                relay.run_once(
                    run_id,
                    crash_at="after_first_provider_write",
                )

            restarted = Relay(db_path, provider_path)
            restarted.recover()

            state = restarted.get(run_id)
            self.assertEqual(state["status"], "done")
            self.assertIsNotNone(state["receipt"])
            self.assertEqual(
                len(restarted.provider.list_objects()),
                len(payload["assets"]),
                "the recovered run lands on the objects the crashed run wrote",
            )
            # The starter passed everything above while never opening the
            # provider. These three are what make the check worth running.
            self.assertCertifies(
                restarted.deployment_summary(run_id),
                "complete",
            )
            self.assertCertifies(restarted.audit(run_id), "complete")
            self.assertEqual(
                {
                    obj["source_asset_id"]
                    for obj in restarted.provider.list_objects()
                },
                {asset["asset_id"] for asset in payload["assets"]},
                "one draft per approved asset, and nothing else",
            )

    def test_every_deployed_object_matches_its_source_asset(self) -> None:
        payload = approved_request()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relay = Relay(root / "d.db", root / "p.json")
            run_id = relay.submit("integrity-check", payload)
            relay.run_once(run_id)

            limit = relay.provider.DISPLAY_NAME_LIMIT
            for asset in payload["assets"]:
                # Located in the provider, not in the receipt. An empty
                # provider now fails here instead of passing vacuously.
                stored = self.deployed_object_for(relay, asset["asset_id"])
                self.assertEqual(stored["source_sha256"], asset["source_sha256"])
                self.assertEqual(stored["object_type"], asset["type"])
                self.assertEqual(stored["status"], "draft")
                self.assertEqual(
                    stored["display_name"],
                    str(asset["display_name"]).strip()[:limit],
                    "the stored name is the provider's normalization of the"
                    " approved one",
                )

            self.assertEqual(
                len(relay.provider.list_objects()),
                len(payload["assets"]),
                "no extra drafts beyond the approved set",
            )
            summary = relay.deployment_summary(run_id)
            self.assertCertifies(summary, "complete")
            self.assertEqual(
                summary["objects_deployed"],
                summary["assets_approved"],
                "the panel's count comes from HubSpot, not from the receipt",
            )
            self.assertEqual(summary["discrepancies"], [])


if __name__ == "__main__":
    unittest.main()
