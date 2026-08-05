Submission
This document contains only evidence actually produced during the exercise.
Known gaps and unproved claims are called out directly rather than filled in.

What to read, in order
ROADMAP.md — the evidence, the theory, the promise, and the plan. Committed
before any source or test edit (see git log).

DECISIONS.md — confirmed causes, what shipped, what removed a cause versus
what only stopped a symptom, and what is still open.

relay/core.py — the implementation.

tests/ — seven files, one guarantee per file.

Transcript
Raw, untidied session transcripts in the working tree: roadplanning.md,
session1.md, agent1.md, agent2.md, agent3.md, agent4.md,
agent5.md, and agentmain.md. I kept them separate from the implementation
commits so the reasoning trail remains visible.

I left the dead ends in place deliberately. The most useful ones are the
rigged first reproduction of G6, the hash-versus-length-prefix identity
argument, the rejected display-name collision rule, and the first mutation
pass that falsely treated import errors as killed mutations.

Commands
make test
Ran 62 tests in 0.668s

OK
Run five times consecutively, green every time. The worker-ownership and
cancellation tests force their ordering with threading.Barrier and
threading.Event; no test sleeps and none depends on how long anything takes,
so the timeouts in them are deadlock guards rather than schedule assumptions.

Known noise. The verbose run also emits 1582 ResourceWarning: unclosed database lines, because \_connect() returns a connection that callers use as
with self.\_connect() as connection: — which scopes the transaction, not the
connection. Nothing is uncommitted and no assertion is affected; the warnings
bury the result and would be file-descriptor pressure in a long-lived worker.
Recorded in DECISIONS.md as open item 4, not fixed.

make demo
Scene 1 — deploy, crash after the first provider write, restart, recover:

INJECTED CRASH: crashed after provider write and before local receipt
DEPLOYMENT SUMMARY
{
"run_id": "209a8809-a4d3-49e8-8936-a30392d3c9e8",
"status": "done",
"objects_deployed": 4,
"assets_approved": 4,
"certification": "complete",
"certified_at": "2026-08-05T22:52:46.909462+00:00",
"discrepancies": []
}
objects the destination is holding: 4
Scene 2 — two objects deleted from the destination behind the service's back,
then the operator presses "Check again". This is the demonstration:

SAME RUN, AFTER THE DESTINATION LOST TWO OBJECTS
{
"run_id": "209a8809-a4d3-49e8-8936-a30392d3c9e8",
"status": "done",
"objects_deployed": 2,
"assets_approved": 4,
"certification": "divergent",
"certified_at": "2026-08-05T22:52:46.911862+00:00",
"discrepancies": [
{
"kind": "missing_object",
"external_key": "19:campaign-deploy-001:asset-email-001",
"source_asset_id": "asset-email-001"
},
{
"kind": "missing_object",
"external_key": "19:campaign-deploy-001:asset-email-002",
"source_asset_id": "asset-email-002"
}
]
}
OPERATOR PRESSED CHECK AGAIN
{
...
"checked_objects": 4,
"objects_found": 2,
"all_present": false,
"certification": "divergent",
...
}
objects the destination is holding: 2
status is still done — the run really did finish and history is not
rewritten. certification is divergent and names both missing keys, with the
time of the check. The starter reported verified: true, all_present: true over
this same state.

Scene 3 was cut from the demo for time. ROADMAP.md §8 also planned to
submit deployment_request_empty.json and show it refused at submit. That
behavior is still covered by
SubmitDoorTest.test_the_zero_asset_fixture_is_refused_by_the_same_rule; it is
tested but not demonstrated in make demo.

make stress
Three consecutive runs, 12 workers against one database and one provider:

run 1 run 2 run 3
objects the runs created : 48 48 48
objects the provider holds: 43 46 47
run status counts : {'failed': 4, 'done': 8} {'done': 10, 'failed': 2} {'done': 11, 'failed': 1}
errors raised : 0 0 0

recovery pass over anything left running:
attempt 1: returned attempt 1: returned attempt 1: returned
attempt 2: returned attempt 2: returned attempt 2: returned
attempt 3: returned attempt 3: returned attempt 3: returned
run status counts after : {'failed': 4, 'done': 8} {'done': 10, 'failed': 2} {'done': 11, 'failed': 1}
Four things to read out of that, all of them predicted by ROADMAP.md §8:

The provider still loses writes — 43/46/47 of 48. That is CH13, an
artifact of the fake's unlocked read-modify-write, and deliberately not fixed.

The short count lands in failed, never in done. No run reports
finished over objects that are not there.

Zero errors escape. The starter leaked a raw JSONDecodeError out of
run_once when a write hit a half-written state file, and left the run
running for a worker that no longer existed.

The counts are identical before and after the three recovery passes, and
no run is left running. That is §4.14 holding: recover() does not reclaim
failed, so the passes converge instead of churning. The starter flipped all
12 runs to done regardless.

Verified separately, because stress.py does not check it. The §8 claim
"no run may certify complete while its objects are missing" is printed nowhere.
Reproducing the same 12-worker shape and then certifying every run found zero
violations against three predicates: no run is done without certifying
complete; no complete certification covers fewer objects than the approval
names; no failed run certifies complete.

Caveat: stress.py is a diagnostic printout and does not assert those
three predicates itself. I ran the certification sweep beside it and recorded
the zero-violation result above. The command is therefore evidence to inspect,
not a self-verifying stress test.

Mutation pass
11 mutations, each applied to relay/core.py alone and reverted with
git checkout before the next. Full analysis in DECISIONS.md.

M15 must-fail SURVIVED (named check does not isolate it)
M14b must-fail SURVIVED (named check does not isolate it)
M16 must-fail killed
M11b must-fail killed
M18b must-fail SURVIVED (a different test covers it)
M19 must-fail killed
M17 must-fail killed
M9 must-fail killed
M12 must-fail killed
M24 expected-to-survive SURVIVED (gap confirmed)
M21 expected-to-survive killed (prediction was wrong)
Read the three survivors, not the six kills. Certification and
reconciliation are pinned — remove the readback, the reverse scan, the split
except, or the reconcile wrapper and a named test dies. But M15, M14b and
M18b survived their named check and the entire 62-test suite, because in each
case a second layer holds the behavior up and the test cannot tell the two
apart. ROADMAP.md §7 credits three checks with pinning mechanisms they do not
pin. That is the same species of false confidence as the starter's
verified: true, one level up, and it is written up rather than re-labelled.

M21 went the other way: the prediction that a constant objects_deployed: 0
would survive was wrong — test_visible asserts it equals assets_approved.

How the pass was nearly wrong. The first run reported all nine must-fail
mutations killed with errors=1. They were not killed; errors=1 was
ModuleNotFoundError, because a dotted test target does not put tests/ on
sys.path the way discovery does. Every mutation was "caught" by a suite that
never imported. Re-run with PYTHONPATH=.:tests, three of them survive. A
mutation pass that cannot tell a failure from an import error measures
nothing.

Something found by hand, not taken from the agent
I personally deleted two objects from the provider state file and ran audit
again. The starter still reported objects_deployed: 4, all_present: true,
and verified: true. That direct probe changed the working theory from “the
receipt may be stale” to “the receipt is not derived from provider state at
all.” The probe and output are preserved in roadplanning.md; searching for
deleted 2 objects locates the step.

I also independently checked the starter test that accepts
source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL", repeated make stress to observe
the varying retained-object counts, and counted the fixture’s normalized display
names. Those supporting probes are recorded in the same raw transcript.

The claim I am least sure of
G6 (c9, recovery isolation) is the weakest link in the chain. The cause is
plainly visible in the starter's recover() — a bare loop with no per-run
try/except, so the first raise ends the pass. But the reproduction was
rigged: it was made to raise with an invented payload missing
source_sha256, because payload C from c9 is not in fixtures/ and its content
is unknown. Attempting it with a naturally occurring error did not reproduce the
stuck state.

How it was checked: the fix is tested against an injected permanent write
failure aimed at exactly one deployment's namespace, with a good run queued on
either side of it, so the assertion does not depend on the order recover()
sweeps rows. That proves the isolation property holds. It does not prove that
c9's actual trigger was this mechanism.

The mutation pass sharpened this: M18b — widening recover() to reclaim
failed — survived the very test written to forbid it, because \_claim's
status filter refuses the run before the widened selection can matter. The
behavior is protected; the check that was supposed to prove it is not the thing
proving it.

Second candidate, if a different one is preferred: the c3 reading. Choosing
the operator's idempotency key as the unit of idempotency is a decision made
in spite of the ambiguity, not a resolution of it. If deploy-207a and
deploy-207b were one approval submitted twice by a client that regenerated its
key, this design deploys twice and considers both correct.

What a reviewer should know before opening the repository
The promise is narrow on purpose. Certification proves identity and
presence, never content. The provider exposes create_draft, read, and
list_objects and nothing that returns a rendered body, so there is nothing
to hash-check source_sha256 against. ROADMAP.md §10 states this first
because it is the limit most likely to be mistaken for a stronger claim.

Both starter tests were rewritten, not deleted. Both passed against the
unmodified starter, one of them tautologically (F13) and one with its
assertions nested inside a loop that ran zero times against an empty provider
(F14). They kept their subjects and now assert against a fresh reading of the
provider.

FakeHubSpot was never modified, subclassed, or monkeypatched. Every
simulated provider failure in the tests is a wrapper placed around the
instance the starter ships, per TASK.md.

No fixture value is special-cased. Expectations are derived from the
loaded payload at runtime or from FakeHubSpot.DISPLAY_NAME_LIMIT. No test
writes down an asset id, a hash, an asset count, or the literal name limit.

The provider's own lost-write race is not fixed (CH13). It is an artifact
of the fake, the provider is not ours to change, and the design makes the loss
visible — divergent when a write was lost, unknown when the state file
cannot be parsed — rather than silent.

Four implementation limitations are documented rather than hidden. An
explicit operator retry reopens a cancelled run; repeated asset_id values
are not rejected and can collapse two approvals into one expected object;
SQLite connections are not explicitly closed and produce 1582
ResourceWarning lines; and make demo omits §8's zero-asset scene. The first
is now documented as an operator override. The other three remain open and are
explained in DECISIONS.md.

The suite does not cover everything the code returns. audit's
all_present and checked_objects are asserted by no test, and the only
assertion on deployment_summary's objects_deployed is an inequality. Those
are named as expected-to-survive mutations in DECISIONS.md rather than
quietly left out.
