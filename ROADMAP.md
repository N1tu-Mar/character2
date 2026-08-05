ROADMAP — deployments an operator can trust

Note on ids. CH is short for change. CH1, CH2, CH5b … are theplanned changes in §5. They are deliberately not c1–c11, which are thecase ids recorded in fixtures/deployment_events.jsonl. F* are observedfacts (§1), I* interpretations (§1), G* cause groups (§2), and M*mutation tests (§7).

Design kept small enough for the 2-hour target. FakeHubSpot is treated as anexternal provider and is not modified; all coordination is added around it.

Implementation note. This roadmap was committed before source and testedits. Final outcomes and mutation results are recorded in DECISIONS.md.CH12 was deferred, the third demo scene was cut, explicit operator retry isallowed to reopen a cancelled run, and repeated asset_id values remain anunresolved validation gap.

1. Current evidence

Direct facts — observed by running code, not by reading it

#

Fact

How

F1

"verified": True is a literal in the source (core.py:209). No comparison produces it.

read + probe

F2

deployment_summary (core.py:222) and audit (core.py:239) read the receipt out of SQLite. Neither opens the provider.

deleted 2 objects from the provider state file; both still reported objects_deployed: 4, all_present: true, verified: true

F3

The provider key is f"{run_id}:{asset_id}" (core.py:194) and run_id is a fresh UUID per run (core.py:117, :161).

read + probe

F4

Same idempotency key submitted twice → 2 runs, 8 provider objects.

probe

F5

retry() → 8 provider objects.

probe

F6

cancel() then run_once() → status returns to done, 4 drafts created. RunCancelled (core.py:19) is defined and never raised anywhere.

probe + grep

F7

No lease, claim, owner, or worker id exists anywhere in the repo.

grep

F8

FakeHubSpot._load/_save is an unlocked read-modify-write of one JSON file (core.py:61, :78, :79). 50 concurrent creates: 34–44 returned success, provider held 2–6, the rest raised JSONDecodeError from reading a half-written file.

probe, 3 trials

F9

make stress: 48 objects created, provider holds 36–44, varies every run. 1–3 runs left running, then the recovery pass flips all 12 to done.

4 runs of make stress

F10

_store_display_name (core.py:52-53) strips and truncates to 40 chars. On deployment_request.json, 4 approved names become 3 distinct stored names — asset-email-002 and asset-email-003 normalize to the same value.

probe

F11

deployment_request.json and deployment_request_short.json share every asset_id and every source_sha256. Only display_name differs.

probe

F12

deployment_request_empty.json (0 assets) returns objects_deployed: 0, verified: true, all_present: true. all_present is all([]).

probe

F13

test_every_deployed_object_matches_its_source_asset cannot fail. Fed source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"; the provider echoed it verbatim (core.py:67) and the assert passed.

probe

F14

test_reported_deployment_recovers_without_duplicate_drafts checks object count only. In its own scenario 3 of 4 display names do not match the approval and it passes.

probe

F15

recover() (core.py:261-266) is a bare loop with no per-run try/except.

read

F16

payload_hash is stored at submit (core.py:119) and never read by any decision.

read

F17

stress.py:30 submits the same payload under 12 different idempotency keys; stress.py:56 declares the expected result as WORKERS * assets = 48 objects.

read

F18

Zero network capability. No requests/socket/urllib/http/httpx import and no URL anywhere in relay/, tests/, demo.py, stress.py.

grep

F19

object_id is "hs-" + sha256(external_key)[:12] (core.py:64) — recomputed by hand and matched. It restates the key and is not evidence of existence.

probe

F20

thumbnail_render (c11) appears nowhere in the repository.

grep

Interpretations — reasoned, not observed

#

Interpretation

Why it is not a fact

I1

F8 is an artifact of the fake being a JSON file; real HubSpot does not lose writes.

No real HubSpot was available to check against.

I2

deploy-207a / deploy-207b (c3) are two separate deployments rather than one duplicated approval.

The keys differ, and nothing in the log says whether that reflects operator intent or a client that regenerated its key. Genuinely ambiguous — see §2.

I3

c9's payload_version: "C" is a request shape the code cannot handle.

Payload C is not in fixtures/. Its content is unknown.

I4

c11 is out of scope.

Based on absence of evidence (F20), which is weaker than presence.

I5

The empty-request case (F12) is the same defect as the partial-loss case.

The empty fixture appears nowhere in the event log; the tie to the operator report is via "keeps disagreeing with what the service says it did," which is a broad reading. Weakest tie in this document.

I6

F10 is deliberate fixture design: deployment_request.json is shaped so two approved assets normalize to the same stored display_name, demonstrating that display-name uniqueness is not a valid identity rule.

Intent is inferred. But the reading is load-bearing here — it is why identity is keyed on idempotency_key + asset_id and never on a name.

2. Current theory

Grouped by common cause. Each group states what would falsify it.

G1 — The receipt is not derived from anything (c7, c8, c1-partially)

verified is a constant (F1) and both operator-facing screens re-read thatconstant instead of the provider (F2). The service reports intent, never state.

c7 — receipt said verified, drafts were not what was approved. Nothing ever compared them.

c8 — receipt says 4 objects, operator counts 3. The receipt was built from readbacks taken before another worker's write erased one.

Falsified if: deleting or altering a provider object caused audit to report a problem. It does not (F2).

G2 — Identity is derived from the run, not the approval (c2, c4)

The provider key contains run_id (F3), so every new run is a new set ofdrafts. Repeated submits (c2) and admin retries (c4) both mint new runs.idempotency_key is stored and never enforced; payload_hash is stored andnever read (F16).

c2 and c4 share the provider key cause but need different repairs at the door:

c2 is stopped by enforcing the idempotency key at submit.

c4 is not, because retry() deliberately creates a new run (core.py:154-177). Only an approval-derived provider key stops it.

c4 also contains a second, separate fault.{"event":"worker_stall","local_status":"running"} precedes the retry. The runwas stuck because nothing owns or times out a run (F7) — that is G4. Theoperator's retry was a response to G4, and it duplicated because of G2. Twofaults stacked, which is why c4 looks like both.

Falsified if: submitting the same approved request twice produced one set of drafts. It produces 8 (F4, F5).

G3 — The same key with different content is accepted silently (c5)

deploy-303 submitted with payload A then payload B. Both are accepted, bothget rows, both would deploy. The column that could detect this (payload_hash)is written and never compared (F16).

Related to G2, distinct from it: G2 is "one approval deployed twice," G3 is "onekey claiming two different approvals." G2 wants dedupe. G3 wants rejection.

Falsified if: the second submit raised. It does not.

G4 — Nothing owns a run (c8, and the stall inside c4)

Two worker_claim events for run-14 and no claim mechanism exists (F7). Bothworkers run the whole payload, both build receipts, and their concurrentprovider writes erase each other (F8).

Falsified if: a second worker calling run_once on a claimed run declined to proceed. Nothing stops it.

G5 — Cancellation is advisory (c6)

cancel writes a status (core.py:147-152). run_once never reads it, and itsfinal UPDATE ... status='done' (core.py:211-219) is unconditional, so afinishing worker overwrites the cancel. RunCancelled is dead code (F6).

Falsified if: a cancelled run stopped writing and stayed cancelled. It reaches done and writes all 4 drafts (F6).

G6 — Recovery has no per-run failure isolation (c9)

recover() is a bare loop (F15). One raise ends the pass and every run behindit stays running forever. Matches "two campaigns behind it never went out."

Honest status: cause is visible in the code, the reproduction was rigged. Itwas made to raise with an invented payload (missing source_sha256), becausepayload C is not in fixtures/ (I3). Attempting it with a naturally-occurringerror did not reproduce the stuck state. Treat G6 as code-visible, not observed.

Falsified if: a failing run in the recovery set left the runs behind it deployable. Untested against a natural trigger — this is the weakest claim here.

G7 — Ambiguous writes have no resolution path (c10)

c10 is not a fault. The log shows a write returning gateway_timeout,
retryable:true and a readback finding the object. That is the correct shape:an idempotent provider plus a readback resolves the ambiguity deterministically.c10 is evidence for readback-based verification, not against it.

What it does expose: run_once has no try/except around create_draft atall, so a timeout would propagate and the run would die mid-payload. The gap isthe missing reconciliation path, not the ambiguity itself.

Falsified if: a write that raised, followed by a matching readback, were already treated as success. There is no such path.

Controls and noise

c1 — successful crash recovery, with a caveat. It passes becauserecover() reuses the same run_id, which keeps the provider key stable byaccident of G2's design, not by intent. And its receipt is still an uncheckedconstant. So c1 is a control for duplicates only, not for verification.

c3 — ambiguous from the evidence. Two different idempotency keys(deploy-207a, deploy-207b), the same payload version, 8 provider objects.Two readings fit the log equally well:

Two genuinely separate approvals of the same content. 8 objects is correct.

One approval submitted twice by a client that regenerated its key. 8 objects is the duplication the operator is complaining about.

Nothing in the evidence distinguishes them. The keys differ, and the logrecords no operator intent. stress.py submits the same payload under 12different keys and expects 48 objects (F17), but that is the starter'sexpectation encoded in a test harness — it is not evidence about what anoperator meant, and it should not be cited as though it were.

Design choice, made in spite of the ambiguity: the operator's idempotency keyis the unit of idempotency. Different keys mean different deployments. This ischosen because the key is the only stable identifier the operator controls, andbecause merging on payload content would make two intentional deployments of thesame assets impossible to express. It does not resolve c3 — it decides howthe system will behave when c3's shape recurs.

What would resolve it: an operator confirming whether 207a/207b were one approval or two. Until then c3 stays ambiguous and is not counted as a fixed fault.

c11 — out of scope. thumbnail_render exists nowhere in the repository(F20) and is not on the deployment path. Treated as TASK.md's "at least one isunrelated" and not investigated further. Revisited only if it turns out to touchhow a deployment is carried out or simulated.

3. Operator promise

A deployment reported as complete has just been read back from HubSpot,object by object, and every approved asset was found as a draft carrying theidentity and provenance fields the service sent. If the service cannot confirmthat, it reports divergent and names what differs. If HubSpot cannot be read,it reports unknown and does not guess.

That is a claim about identity and presence, not about content. Theprovider exposes create_draft, read, and list_objects and nothing thatreturns the rendered body of a landing page or an email. source_sha256 is aprovenance tag the service sent and the provider echoed back verbatim (core.py:67,F13). Comparing it proves the draft found is the one the service wrote for thatapproved asset. It does not prove HubSpot's stored content hashes to thatvalue, and no method on this provider could establish that.

A run finishing is history, not proof. done means the run completed itswork at the time it ran. It is never presented on its own as evidence thatHubSpot matches now — that question is answered only by a fresh check.

An approved request deploys once per idempotency key. Submitting the same keyagain, retrying it from the admin panel, or recovering it after a crash reusesthe same HubSpot objects rather than creating new ones. Submitting the same keywith different content is refused before anything is written.

A cancelled deployment stops the current execution and cannot be overwrittenby that worker or by automatic recovery. An explicit operator retry mayreopen the existing deployment.

A deployment that cannot complete fails by itself and does not hold up thedeployments behind it.

Current implementation caveat: the certification guarantee assumesasset_id values are unique within one approved request. Repeated IDs are notcurrently rejected and can collapse two approved assets onto one expectedprovider key. That input-validation gap is documented in DECISIONS.md.

Deliberately not promised: rollback, deletion, cleanup of anything alreadywritten, exactly-once execution, or that HubSpot still matches a second after the check. The provider exposes only create_draft, read, and list_objects —there is no delete and no update, so none of those could be proved here.

4. Definition of complete

Two independent axes

The starter conflates "did the run finish" with "is HubSpot right." These areseparate questions with separate lifetimes, and the operator report is largely asymptom of merging them. They are split:

Axis

Values

Lifetime

Workflow status — what the run did

pending, running, done, cancelled, failed

Historical. Append-only in meaning: once a run is done it stays done.

Provider certification — what HubSpot holds now

complete, divergent, unknown

Point-in-time. Recomputed on every check; the previous value is never reused.

done + divergent is a legal and expected combination: the run did its job,and something changed in HubSpot afterwards. That pair is exactly what theoperator saw and could not express. Certification is never written back overhistory, and history is never presented as certification.

unknown is deliberately distinct from divergent. If the provider cannot beread — a torn read, a half-written state file (F8) — the answer is "unknown," not "it is wrong." Collapsing the two would make a transient read failurelook like data loss. Not every exception out of the provider means this; asuccessful read that finds nothing is a different answer, and the boundary isdrawn below.

Certification is complete only when all of these hold, from a fresh readback

Coverage — every approved asset has exactly one corresponding provider draft.

No omissions — no approved asset is missing.

No extras — scanning the provider for objects under this deployment's key namespace yields exactly the approved set, no duplicates and no strangers.

Identity and provenance match, per object, on the fields that actually identify it: external_key, source_asset_id, source_sha256, object_type, and status == "draft". These are the fields the service sent and the provider echoed; matching them proves presence and provenance, never content (§3).

Name match, not name uniqueness — display_name equals the provider-normalized form of the approved name. Two objects in the same deployment may legitimately carry the same normalized value; that is not a discrepancy and never fails certification. See below.

Freshness — the verdict comes from reading the provider now, never from replaying a stored receipt or a stored certification.

Honesty — if 1–5 cannot be proved, certification is divergent and the specific failures are named. If the provider cannot be read, certification is unknown.

Missing is not unreadable

Both arrive as exceptions out of the same readback, and they mean oppositethings. The distinction is a rule, not an implementation detail:

Provider behavior

Meaning

Certification

read(key) raises KeyError — the object is absent from a state file that parsed fine

The read completed and the object is not there

divergent, naming the missing external_key

list_objects() / read() raises JSONDecodeError, OSError, or any failure to parse or open the state at all

The provider state could not be read

unknown

KeyError is a successful read with a negative answer. Mapping it to unknownwould turn a real deletion — the operator's "once there were fewer" — intoan unknown result, which is precisely the confusion unknown exists to prevent.Mapping JSONDecodeError to divergent would report a torn read (F8) as dataloss. Both directions are pinned by mutations (M11, M11b).

A single readback may produce both: some keys absent, the file readable. Thatcertifies divergent. unknown requires that the provider state could not beread at all, so no per-object claim is possible.

Rules at the door and at completion

done requires complete at completion time. A run that finishes its writes but cannot certify complete ends failed, not done. Later divergence does not retroactively change it.

done is not a claim about now. No operator-facing surface may present workflow status alone as evidence about HubSpot's current contents. Every surface that reports status also reports a certification and when it was computed.

Empty request — an approved request with zero assets is rejected at submit and never becomes a run. A deployment of nothing is not a deployment, and letting it through is exactly what produces "verified campaign, nothing in HubSpot." Stated as a rule, not a fixture special-case: len(assets) == 0 is refused for any payload. The alternative — run it and certify divergent — is defensible; this roadmap chooses to fail earlier and louder.

Repeat safety — same key + same payload returns the existing run and produces no additional provider objects.

Conflict — same key + different payload_hash raises IdempotencyConflict at submit, before any provider write.

Concurrent submit resolves through the constraint, not around it. Rules 11 and 12 cannot be implemented as a SELECT followed by an INSERT — two workers submitting the same key at once both see no row and both insert. The UNIQUE constraint is the arbiter, and the loser's IntegrityError is a normal control-flow outcome, not an error to surface:

Attempt the INSERT. If it succeeds, this submit created the deployment; return the new run_id.

On IntegrityError, re-read the row that won by idempotency_key.

Winner's payload_hash equals ours → return the winner's run_id (rule 11 holds; the caller cannot tell which submit won, and does not need to).

Winner's payload_hash differs → raise IdempotencyConflict (rule 12 holds).

The re-read is mandatory. Deciding from the submitted payload rather than from the row that actually landed reintroduces the race one layer up.

This is the same shape as CH4's claim: a conditional write whose outcome is read back from the database, never inferred. Both the sequential path and the racing path must reach the identical verdict, so the acceptance suite exercises both.

failed is terminal for automatic recovery, and only an operator retry reopens it. recover() picks up runs that are running with an expired lease. It does not pick up failed. A failed run stays failed until retry explicitly resets it to pending and clears its lease (CH11). Two reasons:

The loop has to terminate. CH9 bounds attempts precisely so a run that cannot succeed stops consuming the recovery pass. If recover() also reclaimed failed, the bound would do nothing — the run would re-enter the pool on the next sweep and the head-of-line problem from c9 would come back as a slow spin instead of a hard stop.

failed is a request for a human. A run reaches it by finishing its writes and failing to certify complete (rule 8), or by exhausting attempts. Both mean the automatic path has already tried and produced a wrong or unprovable result. Retrying that without someone looking is how a system talks itself into a false success.

The cost is real and worth stating: a run that failed only because of a transient torn read (F8) needs an operator to press retry, even though a second attempt would likely have certified complete. This design accepts a stalled run over an unattended retry loop, because the failure this project exists to remove is the service claiming more than it checked. An operator who reads failed and retries gets the right answer; a service that quietly retries until something says complete is the starter's behavior with more steps.

Because retry resets to pending rather than inserting a row, and because the provider key no longer contains run_id (CH1), that retry lands on the same HubSpot objects and creates no duplicates. This is the failed path and c4's operator retry converging on one mechanism.

Identity: what names a HubSpot object

Plain concatenation of idempotency_key + ":" + asset_id is ambiguous and isnot used. Nothing forbids a colon inside either component, and if one appears themapping stops being injective: key a with asset b:c and key a:b with assetc both produce a:b:c. Two unrelated deployments would then share providerobjects, and the namespace scan in rule 3 would attribute one deployment'sobjects to another. No fixture triggers this — which is exactly why it has to beclosed by a rule rather than left to the available fixture shapes (F11).

One shared function, length-prefixed, defined once:

external_key(idempotency_key, asset_id) = f"{len(idempotency_key)}:{idempotency_key}:{asset_id}"
deployment_namespace(idempotency_key)   = f"{len(idempotency_key)}:{idempotency_key}:"

The length prefix pins where the key ends, so the encoding is injective for anycomponent contents, colons included. a/b:c gives 1:a:b:c; a:b/c gives3:a:b:c. The namespace prefix is unambiguous for the same reason: scanning for9:deploy-20: cannot match 11:deploy-207a:asset-lp-001, which naive prefixmatching on deploy-20 would.

Both components are rejected at submit if empty, so a namespace prefix cannever itself be a valid object key.

Why length-prefixing rather than hashing the pair. A digest is equallycollision-safe and would also be injective in practice, but it destroys thenamespace scan's readability: rule 3 requires enumerating every provider objectbelonging to a deployment, and with opaque keys an operator reading the providerstate cannot see which deployment owns what. Since the entire point of this workis a system an operator can check, a key they can read is worth more than ashorter one. If key length ever becomes a provider constraint, hashing thelength-prefixed string is the drop-in replacement — the injectivity argumentcarries over unchanged.

This function is the only place a provider key is constructed. Writing inrun_once, recovery, retry, the certification readback, and the rule 3 namespacescan all call it. An f"..." built inline anywhere else is a defect, because thewriter and the certifier disagreeing about a key produces a false divergentthat looks exactly like real data loss. See §5 on why this lands in the firstimplementation slice.

Two properties follow from the inputs, and both matter:

Stable across runs. The key contains nothing that varies per run — no run_id, no UUID, no timestamp. A retry, a recovery pass, and a re-submitted approval all compute the same key and land on the same object. This is what removes G2.

payload_hash is deliberately excluded. It guards the door (rule 12) and does not name the object. If it were part of the key, identity would drift with any change in how a payload is serialized — a reordered key, a whitespace change — and the same approval would silently acquire a second set of drafts. Conflicting content is refused before a write, so it never needs to be encoded in the key afterwards.

display_name is not identity. It is compared as a field (rule 5) and neverused to distinguish objects, deduplicate them, or reject a request.deployment_request.json is shaped to prove the point: asset-email-002 andasset-email-003 both normalize to 'Summer 2026 ABM campaign email - product'(F10, I6). Those are two distinct, correctly deployed drafts that happen to sharea label. A design that treated a shared name as a collision would reject a validapproval — so this roadmap does not add any such rule, anddeployment_request.json remains a fully deployable, successfully certifyingcase.

5. Planned changes

Every change labelled exactly one of ROOT CAUSE FIX / FALSE-PASSPREVENTION / OBSERVABILITY ONLY / OUT OF SCOPE.

Change ids are CH* so they cannot be confused with the event-log case idsc1–c11.

#

Change

Label

Addresses

CH1

Provider external_key comes from one shared, collision-safe, length-prefixed function over idempotency_key and asset_id, replacing run_id:asset_id. Every writer and every reader calls it; no key is built inline. Retries, recovery, and re-submits land on the same HubSpot objects. payload_hash is not part of the key (§4, Identity).

ROOT CAUSE FIX

G2 (c2, c4)

CH1b

Reject empty idempotency_key and empty asset_id at submit, so a deployment namespace can never collide with an object key (§4, Identity).

FALSE-PASS PREVENTION

§4, Identity

CH2

UNIQUE on idempotency_key. Same key + same payload_hash returns the existing run_id; different payload_hash raises IdempotencyConflict before any provider write. Insert-first, then resolve IntegrityError by re-reading the winning row (§4.13) — never SELECT-then-INSERT. Finally reads the column from F16.

ROOT CAUSE FIX

G2, G3 (c2, c5)

CH3

Reject zero-asset payloads at submit.

FALSE-PASS PREVENTION

F12, §4.10

CH4

Atomic claim: owner, lease_expires_at, attempt columns; claim via conditional UPDATE checked with rowcount. Verified — 20 racing threads, exactly 1 winner. Expired leases are reclaimable so a stalled worker does not strand a run.

ROOT CAUSE FIX

G4 (c8, c4's stall)

CH5

Replace the verified boolean with a computed certification (complete / divergent / unknown) derived from a fresh readback against §4.1–4.5, plus certified_at and a discrepancies list. Never a literal.

FALSE-PASS PREVENTION

G1 (c7, c8)

CH5b

Certification maps a KeyError from read() to divergent and a parse/open failure of the provider state to unknown (§4, Missing is not unreadable). The two must not share an except clause.

FALSE-PASS PREVENTION

G1, F8

CH6

Split workflow status from certification (§4). status keeps pending/running/done/cancelled/failed and is historical; certification is recomputed on demand and never stored as truth. A run ends done only if it certified complete at completion; otherwise failed.

ROOT CAUSE FIX

G1, and the operator's "I have no way to know what this service promises"

CH7

audit re-reads the provider and recomputes certification, returning it alongside the historical status and the time of the check. deployment_summary likewise never presents status alone.

FALSE-PASS PREVENTION

G1, §4.9

CH8

cancel sets cancel_requested; run_once checks it before every provider write and raises RunCancelled; the terminal status update becomes conditional on ownership and non-cancellation.

ROOT CAUSE FIX

G5 (c6)

CH9

Per-run try/except in recover, plus a bounded attempt count so a permanently failing run ends failed and is surfaced rather than retried forever. recover selects running runs with expired leases and never failed ones (§4.14).

ROOT CAUSE FIX

G6 (c9)

CH10

Wrap create_draft in a reconcile helper outside the provider: on exception, read back the expected key; matching → success, absent → bounded retry, present-but-different → fail loudly.

ROOT CAUSE FIX

G7 (c10)

CH11

retry resets the existing run to pending and clears its lease instead of inserting a second row (which CH2's constraint would forbid anyway). It is the only path out of failed (§4.14), and it resets the attempt count so CH9's bound applies to the new operator-authorized attempt rather than the exhausted one.

ROOT CAUSE FIX

G2 (c4)

CH12

SQLite timeout and WAL on _connect, so contention blocks briefly instead of raising database is locked. Deferred in the final implementation.

OBSERVABILITY ONLY

F9

CH13

The provider's lost-write and torn-read race (F8). Not fixed. It is an artifact of the fake (I1), TASK.md says the provider is not ours to change, and CH5 makes it visible instead of silent — as divergent when a write was lost, unknown when the state file cannot be parsed.

OUT OF SCOPE

F8

CH14

c11 thumbnail_render. Not in the deployment path, not in the repository.

OUT OF SCOPE

c11

CH15

Rollback, deletion, cleanup of duplicate drafts already in HubSpot. The provider has no delete.

OUT OF SCOPE

—

CH16

Any rule keyed on display_name — uniqueness checks, collision rejection, name-based dedupe. Explicitly not built. deployment_request.json proves a shared normalized name is legitimate (§4, Identity; F10, I6).

OUT OF SCOPE

F10

Minimum cut if time runs short, in order: CH5, CH6, CH7, CH3 (the promise andthe false passes), then CH2, CH1 (duplicates), then CH8, CH4 (cancel and claim),then CH9, CH10. CH11–CH12 last. If CH9/CH10 do not land, they move toDECISIONS.md as known-open, which for G6 is honest anyway given it is theleast-proven group.

One exception to that order. The shared external_key function from §4 landsin the first slice, before CH5, even though CH1's switch toidempotency_key-derived keys comes later. CH5 makes certification compute anexpected key and compare it to what the provider holds — so from the momentcertification exists, the writer and the certifier are two callers that mustagree. If CH5 ships while run_once still builds f"{run_id}:{asset_id}"inline, the two derive keys independently and any divergence between themcertifies divergent while HubSpot is in fact correct. That is a false failurewearing the exact costume of the real one this project exists to detect, and itwould be indistinguishable in the demo.

So: extract the function first with run_id still as its input, then CH1 becomesa one-line change to what is passed in, and every caller moves at once. The costis one refactor commit before any behavior changes; the benefit is that writerand certifier can never disagree about a key at any point in the sequence.

6. Acceptance tests

Rules for every test below:

Expectations are derived from the approved payload at runtime — iterate payload["assets"], compare against len(payload["assets"]).

No hardcoded 4, no fixture asset ids, no fixture hashes, no literal 40. Read the limit from FakeHubSpot.DISPLAY_NAME_LIMIT.

All three shapes are exercised. deployment_request.json is the workhorse — it deploys and certifies complete, and it is the only fixture that exercises both name truncation and a legitimately shared normalized name.

Where a test needs a second, different payload under the same key, it derives one in-test from the loaded fixture rather than reaching for another file. Deriving is not fixture special-casing; hardcoding the derived value would be.

Provider failure modes are simulated with a wrapper around FakeHubSpot, never by editing it.

Each new test must be checked to fail against the current code before the fix lands. A test that passes on the starter proves nothing (F13, F14).

Test

Shape used

Fails today because

Same key + same payload → one logical deployment; the second submit returns the same run id and adds no provider objects

full

2 runs, 8 objects (F4)

Same key + changed payload → IdempotencyConflict, and the provider is untouched — assert object count is unchanged after the raise

full, then full with one field altered in-test

both accepted, both would write (F16)

retry creates no additional drafts

full

8 objects (F5)

external_key is stable across runs — the key computed for an asset is identical before and after a retry and a recovery, and contains no run_id

full

key embeds a fresh UUID (F3)

Key encoding is injective under colons — (key="a", asset="b:c") and (key="a:b", asset="c") produce different external_key values, and neither deployment's namespace scan picks up the other's objects. Derived in-test, not from any fixture

derived in-test

plain concatenation collides (§4, Identity)

A namespace scan for a key that is a string prefix of another key returns only its own objects — e.g. deploy-20 must not match deploy-207a's objects

derived in-test, using c3's key shapes as inspiration rather than its literal values

no namespace scan exists

Empty idempotency_key or empty asset_id is rejected at submit

derived in-test

no such guard (CH1b)

Both writer and certifier use the same key function — monkeypatch the shared function to a different valid encoding and assert a full deploy still certifies complete. If either side built its key inline, the run certifies divergent

full

keys are built inline in run_once (F3)

Two approved assets whose names normalize to the same value both deploy and certify complete

full — asset-email-002 and asset-email-003 collide by name (F10) and must both succeed

(passes today by accident; kept as a guard against ever adding a name-uniqueness rule)

A display_name past the limit certifies complete — normalization is expected, not a discrepancy

full (three names exceed the limit)

nothing compared

Deleting a provider object makes a fresh check certify divergent

full

audit never opens the provider (F2)

Tampering with source_asset_id / source_sha256 / object_type / status in provider state certifies divergent

full

nothing is compared (F1)

An unexpected object under this deployment's key namespace certifies divergent

full + one injected stray object

nothing scans for extras

An unreadable provider certifies unknown, not divergent and not complete

full, via a wrapper that raises JSONDecodeError on read

no such distinction exists

A missing object certifies divergent, not unknown — the state file parses cleanly and one key is absent, so read() raises KeyError. The two exception paths are asserted in the same test so neither can absorb the other

full, one object removed from provider state

both would be untyped failures; nothing reads the provider at all (F2)

Concurrent submits of the same key + same payload: N threads, exactly one row created, every thread receives the same run_id, and no thread sees an IntegrityError escape

full, N threads on one key

submit inserts unconditionally (F4)

Concurrent submits of the same key + different payloads: exactly one wins, every loser raises IdempotencyConflict, and the provider is untouched

full + one field altered in-test

both accepted, both would write (F16)

Status and certification are independent — after a successful run, delete an object; workflow status stays done, a fresh check certifies divergent, and no surface reports done without a certification

full

done and verified are welded together (F2)

A run that finishes writing but cannot certify complete ends failed, not done

full, via a wrapper that drops one write

every run ends done (F9)

Cancellation after one write prevents later writes and cannot reach done

full

status returns to done, all assets written (F6)

Two workers cannot both claim the same pending run

full

no claim exists (F7)

Concurrent different runs do not lose provider objects or, where the provider does lose them, no run certifies complete

full, N threads

48 created / 36–44 held, all report done (F9)

One broken recovery candidate does not stop later good runs

full + a deliberately broken run

bare loop (F15)

recover() does not reclaim a failed run — drive a run to failed, then run repeated recovery passes and assert its status, attempt count, and the provider object count are all unchanged

full, via a wrapper that drops one write

recover selects on status = 'running' only, so failed does not exist yet (F15)

retry is the only path out of failed — after the retry the run is pending with a cleared lease and a reset attempt count, the next pass deploys it, and it certifies complete without adding provider objects

full, wrapper removed before the retry

retry inserts a second row and doubles the drafts (F5)

Ambiguous write exception followed by a matching readback is reconciled as success

full, via a wrapper that writes then raises

no try/except around create_draft (G7)

Empty approved request is refused at submit and never yields a certification

empty

returns verified: true on zero objects (F12)

Both starter tests stay on deployment_request.json and keep asserting what theyasserted. They are near-worthless as written (F13, F14) and are kept only asregression ballast.

7. Mutation tests

Each acceptance check is paired with one deliberate implementation break thatmust make that check fail. If a mutation lands and the suite stays green, thecheck is decoration and gets rewritten. Run as a manual pass — revert eachmutation before applying the next.

#

Behavior intentionally broken

Check that must fail

M1

In submit, delete the lookup by idempotency_key and unconditionally INSERT a fresh uuid4 row.

Same key + same payload → second submit returns a different run id and provider object count doubles.

M2

In submit, keep the lookup but compare idempotency_key only, ignoring payload_hash — return the existing run instead of raising.

Same key + changed payload → no IdempotencyConflict; the new payload silently inherits the old payload's drafts.

M2b

In submit, replace insert-first with SELECT then INSERT, keeping the same verdict logic.

The concurrent-same-payload check fails — N threads race the gap between the read and the write and create more than one row for one key. Sequential submits still pass, which is the point: only the racing test catches it.

M2c

On IntegrityError, raise IdempotencyConflict without re-reading the winning row.

The concurrent-same-payload check fails — an identical re-submit is reported as a conflict purely for losing a race.

M2d

On IntegrityError, return the winner's run_id without comparing payload_hash.

The concurrent-different-payload check fails — a genuinely conflicting payload silently inherits the winner's deployment. Together with M2c this pins §4.13 from both sides.

M3

Move the conflict check to after the first create_draft call instead of before it.

Same key + changed payload still raises, but the provider object count has changed — the "provider is untouched" half of the check fails.

M4

Restore external_key = f"{run_id}:{asset_id}" in run_once.

retry produces a second full set of drafts, and the key-stability check sees a different key after recovery.

M5

Include payload_hash in external_key, then re-submit the identical approval with its JSON serialized in a different key order.

Key stability fails — semantically identical approvals compute different keys and acquire a second set of drafts.

M5b

Replace the length-prefixed key with plain f"{idempotency_key}:{asset_id}".

The injectivity check fails — ("a", "b:c") and ("a:b", "c") collide, and the prefix-scan check attributes one deployment's objects to another.

M5c

Have run_once build its external_key inline instead of calling the shared function.

The writer/certifier agreement check fails — with the shared function patched, the two sides disagree and a correct deployment certifies divergent.

M6

In the field comparison, assert only that object_id is truthy; drop the equality checks on external_key, source_asset_id, source_sha256, object_type, status.

Tampering with any of those in provider state still certifies complete.

M7

Add display_name to the fields that must be unique across a deployment (or reject colliding names at submit).

The two-assets-sharing-a-normalized-name check fails — a valid approval is rejected or certified divergent. This is the mutation that guards against re-introducing the rule this roadmap deliberately excludes (CH16).

M8

Set the expected display_name to the raw approved string instead of the normalized form.

A request whose names exceed the limit certifies divergent — a false discrepancy. Together with M7, this pins name handling from both sides: too strict on value, too strict on uniqueness.

M9

In the certification routine, replace the per-key provider.read() with json.loads(row["receipt_json"])["objects"].

Deleting a provider object still certifies complete.

M10

Have audit return the certification stored on the receipt instead of recomputing it.

The status/certification independence check fails — a stale complete survives a real deletion.

M11

Map an unreadable provider to divergent instead of unknown.

The unreadable-provider check fails; a transient read failure is reported as data loss.

M11b

Map a KeyError from read() to unknown instead of divergent — or catch both exception types in one except and return a single verdict.

The missing-object check fails; a real deletion is reported as unknown. Pinned in the opposite direction from M11 so no single except clause can satisfy both.

M12

Let run_once set status='done' regardless of the certification it computed.

The finished-but-uncertifiable run is reported done instead of failed.

M13

Have deployment_summary return workflow status with no certification field.

The "no surface reports done without a certification" assertion fails.

M14a

Remove the cancel_requested read from the per-asset loop in run_once.

A run cancelled after the first write keeps writing the remaining assets.

M14b

Make the terminal update unconditional again — drop AND status='running' AND cancel_requested=0 AND owner=?.

A cancelled run reaches done.

M15

In the claim, execute the conditional UPDATE but ignore cursor.rowcount and proceed regardless.

N workers racing one pending run → more than one proceeds to write.

M16

Build the certification from the dicts create_draft returned, rather than from a fresh readback taken after every write completes.

A run whose object was erased by a concurrent writer still certifies complete — this is the exact c8 mechanism.

M17

In certification, iterate approved assets only; drop the reverse scan for provider objects under this deployment's key namespace.

A stray object goes unnoticed and the run still certifies complete.

M18

Remove the per-run try/except in recover so the first exception escapes the loop.

Runs queued behind a broken recovery candidate stay undeployed.

M18b

Widen recover's selection to status IN ('running', 'failed').

The no-reclaim check fails — a failed run is picked up unattended, its attempt count climbs across passes, and CH9's bound stops bounding anything.

M18c

Have retry leave the attempt count at its exhausted value instead of resetting it.

The retry check fails — the operator-authorized attempt is refused immediately by CH9's bound, so failed becomes permanent and nothing reopens it.

M19

Delete the reconcile wrapper; call provider.create_draft directly and let exceptions propagate.

A write that raises after persisting, followed by a matching readback, kills the run instead of being reconciled as success.

M20

Remove the zero-asset guard from submit.

An empty approved request gets a run id and a complete certification over zero objects.

Post-implementation mutation result. The table above is the plannedprediction, not a claim that every test isolated its named mechanism. The actualpass found that M15, M14b, and M18b survived their named tests because a seconddefense layer still preserved the behavior. M18b is covered by a differentfailed-run test; M15 and M14b remain mechanism-isolation gaps. M24 confirmed thataudit.all_present is untested, M27 confirmed the explicit retry-after-cancelbehavior, and M28 exposed the repeated-asset_id validation gap. Full commandsand outcomes are in DECISIONS.md.

Test-suite integrity mutation. Separately, changeFakeHubSpot.DISPLAY_NAME_LIMIT and swap which fixture a test loads. Any testthat hardcoded 40, 4, a fixture asset id, or a fixture hash breaks in a waythat has nothing to do with the behavior under test. That break is the signal —those tests get rewritten to derive from the payload.

8. Verification, demo, and stress

make demo, make test, make stress all keep working, and all three keepusing deployment_request.json. No fixture is rejected by this design.

make demo — the scenario is unchanged; what changes is what it reports.The point is now the split between history and certification:

Deploy deployment_request.json, crash partway, restart, recover. Status done, certification complete, computed from a live readback. Note in the output that two of the four drafts share a normalized display name and that this is correct, not a defect.

Delete two objects from the provider's state file, exactly as demo.py:42-45 does today. Press "Check again". Workflow status is still done — history did not change. Certification is now divergent, naming the two missing objects, with the time of the check. Against the starter's output — which reports verified: true, all_present: true over the same state — this is the whole demonstration.

Submit deployment_request_empty.json. Refused at submit; no run, no certification, nothing written. This planned scene was cut from the shipped demo for time; the behavior is covered by SubmitDoorTest.test_the_zero_asset_fixture_is_refused_by_the_same_rule.

make stress — unchanged fixture and unchanged shape. It should still showthe provider holding fewer objects than the runs created (CH13 is out of scope),but no run may certify complete while its objects are missing, runs thatcannot certify must end failed rather than done, and reads that fail againsta half-written state file must certify unknown rather than divergent. Sameshort count, honest verdict.

Shipped-harness caveat: stress.py prints the counts but does not assertthe certification predicates. I ran a separate certification sweep over the same12-worker shape and found zero violations; SUBMISSION.md records that result.

Its recovery pass is now also a test of §4.14. Under CH13 the provider willlegitimately lose writes, so some runs end failed — and because recover()does not reclaim them, the three passes at stress.py:74-79 must converge: thestatus counts stop changing after the first pass and stay stopped. The starter'sversion flips all 12 runs to done regardless (F9). A run count that keepsmoving across passes, or a failed count that drains without anyone pressingretry, means the no-reclaim rule did not hold.

make test — the two starter tests stay on the full fixture (see §6), plusthe new acceptance suite.

9. Out of scope

Listed, not argued.

Real HubSpot integration, credentials, network calls of any kind.

Rollback, deletion, or cleanup of anything already written. The provider exposes create_draft, read, list_objects and nothing else.

Distributed leases across hosts. The lease here coordinates workers against one SQLite file and claims no more than that.

Long-term retry scheduling, backoff daemons, dead-letter queues.

Any UI.

Schema migration. Every entry point builds its own database.

The provider's internal lost-write and torn-read race (CH13, F8).

Any rule keyed on display_name uniqueness (CH16).

c11 thumbnail_render (F20).

Queue, workflow framework, container, cloud service, real model, OAuth.

Production posture

The code should read as production code — typed, small functions, no bareexcept, structured failure values instead of booleans, every decision derivedrather than asserted. It should not grow the machinery a real deploymentwould need on top of that. The lease is a WHERE clause, not a lock service.Reconciliation is a bounded readback, not a scheduler. The provider is a JSONfile behind an interface left unchanged. Where a real system would need more,that belongs in §10 as a stated limit, not as a half-built version of the realthing.

10. What this still cannot promise

For DECISIONS.md:

Certification proves identity and presence, never content integrity. The provider returns no rendered body for a landing page or an email, so there is nothing to hash-check against source_sha256. That field is a provenance tag the service sent and the provider echoed back verbatim (core.py:67); comparing it establishes that the draft found is the one the service wrote for that approved asset, and nothing more. If HubSpot stored the right identifiers against the wrong body, this design certifies complete and is wrong. Closing that would need a provider method that returns content — the same limitation that makes the starter's test_every_deployed_object_matches_its_source_asset tautological (F13). This version is not tautological, because it compares a fresh readback against the approved payload rather than a value against itself, but it is bounded by the same missing capability.

Certification is point-in-time and expires the instant it is returned. HubSpot can change a second later. complete means "complete when the check ran," and the timestamp is reported for exactly that reason. There is no watch, no subscription, and no continuous reconciliation.

The provider can still lose writes. The relay detects it and reports divergent; it does not prevent it (CH13).

unknown is honest but not actionable on its own. A run stuck at unknown needs a human or a later successful read; nothing here resolves it automatically.

failed needs a human even when a retry would have worked. §4.14 makes failed terminal for automatic recovery, so a run that failed to a transient torn read (F8) sits there until an operator presses retry. There is no backoff, no scheduled re-attempt, and no distinction between "failed transiently" and "failed permanently" — the implementation has no reliable way to tell them apart from one readback, and guessing wrong in the permissive direction is how a service retries its way into a false complete. A real system would classify the failure and re-attempt the transient class automatically; that is deliberately not built here.

There is still a window between a provider write and the local record. Recovery closes it by re-reading, not by making the two atomic.

Identity is only as stable as the operator's idempotency key. If a client regenerates its key for the same approval — c3's ambiguous shape — this design will deploy twice and consider both correct. That is the accepted cost of choosing the key as the unit of idempotency, and it is the case least covered by this roadmap.

Two drafts may share a display name in HubSpot and be hard for a person to tell apart. That is accepted deliberately (§4, Identity): the objects are distinct and correct, and a name-uniqueness rule would reject valid approvals. The mitigation is that external_key, object_id, and source_asset_id all remain distinct and are what the system reasons about.

Nothing is ever cleaned up. Duplicate drafts already in HubSpot from before this change stay there — the provider has no delete.

G6 (c9) is addressed at a cause I reasoned about but never reproduced with a natural trigger.

Repeated asset_id values are not rejected. Two approved assets with thesame ID collapse onto one provider key and can currently certify completewith fewer objects than approval entries. The current guarantee thereforeassumes IDs are unique within a request.

Cancellation is terminal only to the current execution and automaticrecovery. An explicit operator retry reopens the existing row by design;no dedicated test pins that transition yet.

CH12 was deferred. SQLite WAL and busy-timeout configuration did not land,and connections are not explicitly closed, so the suite emitsResourceWarning noise.

The shipped stress harness is diagnostic, not self-verifying. It printscounts; the zero-violation certification sweep was run separately.

The shipped demo omits the zero-asset scene. The behavior is covered by afocused test but is not shown in make demo.