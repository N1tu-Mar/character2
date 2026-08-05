# ROADMAP — deployments an operator can trust

Design kept small enough for the 2-hour target. `FakeHubSpot` is treated as an
external provider and is not modified; all coordination is added around it.

---

## 1. Current evidence

### Direct facts — observed by running code, not by reading it

| # | Fact | How |
|---|---|---|
| F1 | `"verified": True` is a literal in the source (`core.py:209`). No comparison produces it. | read + probe |
| F2 | `deployment_summary` (`core.py:222`) and `audit` (`core.py:239`) read the receipt out of SQLite. Neither opens the provider. | deleted 2 objects from the provider state file; both still reported `objects_deployed: 4, all_present: true, verified: true` |
| F3 | The provider key is `f"{run_id}:{asset_id}"` (`core.py:194`) and `run_id` is a fresh UUID per run (`core.py:117`, `:161`). | read + probe |
| F4 | Same idempotency key submitted twice → 2 runs, 8 provider objects. | probe |
| F5 | `retry()` → 8 provider objects. | probe |
| F6 | `cancel()` then `run_once()` → status returns to `done`, 4 drafts created. `RunCancelled` (`core.py:19`) is defined and never raised anywhere. | probe + grep |
| F7 | No lease, claim, owner, or worker id exists anywhere in the repo. | grep |
| F8 | `FakeHubSpot._load`/`_save` is an unlocked read-modify-write of one JSON file (`core.py:61`, `:78`, `:79`). 50 concurrent creates: 34–44 returned success, provider held 2–6, the rest raised `JSONDecodeError` from reading a half-written file. | probe, 3 trials |
| F9 | `make stress`: 48 objects created, provider holds 36–44, varies every run. 1–3 runs left `running`, then the recovery pass flips all 12 to `done`. | 4 runs of `make stress` |
| F10 | `_store_display_name` (`core.py:52-53`) strips and truncates to 40 chars. On `deployment_request.json`, 4 approved names become 3 distinct stored names — `asset-email-002` and `asset-email-003` normalize to the same value. | probe |
| F11 | `deployment_request.json` and `deployment_request_short.json` share **every** `asset_id` and **every** `source_sha256`. Only `display_name` differs. | probe |
| F12 | `deployment_request_empty.json` (0 assets) returns `objects_deployed: 0, verified: true, all_present: true`. `all_present` is `all([])`. | probe |
| F13 | `test_every_deployed_object_matches_its_source_asset` cannot fail. Fed `source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"`; the provider echoed it verbatim (`core.py:67`) and the assert passed. | probe |
| F14 | `test_reported_deployment_recovers_without_duplicate_drafts` checks object **count** only. In its own scenario 3 of 4 display names do not match the approval and it passes. | probe |
| F15 | `recover()` (`core.py:261-266`) is a bare loop with no per-run `try/except`. | read |
| F16 | `payload_hash` is stored at submit (`core.py:119`) and never read by any decision. | read |
| F17 | `stress.py:30` submits the **same payload** under **12 different** idempotency keys; `stress.py:56` declares the expected result as `WORKERS * assets` = 48 objects. | read |
| F18 | Zero network capability. No `requests`/`socket`/`urllib`/`http`/`httpx` import and no URL anywhere in `relay/`, `tests/`, `demo.py`, `stress.py`. | grep |
| F19 | `object_id` is `"hs-" + sha256(external_key)[:12]` (`core.py:64`) — recomputed by hand and matched. It restates the key and is not evidence of existence. | probe |
| F20 | `thumbnail_render` (c11) appears nowhere in the repository. | grep |

### Interpretations — reasoned, not observed

| # | Interpretation | Why it is not a fact |
|---|---|---|
| I1 | F8 is an artifact of the fake being a JSON file; real HubSpot does not lose writes. | We have no real HubSpot to check against. |
| I2 | `deploy-207a` / `deploy-207b` (c3) are two separate deployments rather than one duplicated approval. | The keys differ, and nothing in the log says whether that reflects operator intent or a client that regenerated its key. Genuinely ambiguous — see §2. |
| I3 | c9's `payload_version: "C"` is a request shape the code cannot handle. | Payload C is not in `fixtures/`. Its content is unknown. |
| I4 | c11 is out of scope. | Based on absence of evidence (F20), which is weaker than presence. |
| I5 | The empty-request case (F12) is the same defect as the partial-loss case. | The empty fixture appears nowhere in the event log; the tie to the operator report is via "keeps disagreeing with what the service says it did," which is a broad reading. Weakest tie in this document. |
| I6 | F10 is deliberate fixture design: `deployment_request.json` is shaped so two approved assets normalize to the same stored `display_name`, demonstrating that display-name uniqueness is **not** a valid identity rule. | Intent is inferred. But the reading is load-bearing here — it is why identity is keyed on `idempotency_key` + `asset_id` and never on a name. |

---

## 2. Current theory

Grouped by common cause. Each group states what would falsify it.

### G1 — The receipt is not derived from anything (c7, c8, c1-partially)

`verified` is a constant (F1) and both operator-facing screens re-read that
constant instead of the provider (F2). The service reports intent, never state.

- **c7** — receipt said verified, drafts were not what was approved. Nothing ever compared them.
- **c8** — receipt says 4 objects, operator counts 3. The receipt was built from readbacks taken before another worker's write erased one.
- **Falsified if:** deleting or altering a provider object caused `audit` to report a problem. It does not (F2).

### G2 — Identity is derived from the run, not the approval (c2, c4)

The provider key contains `run_id` (F3), so every new run is a new set of
drafts. Repeated submits (c2) and admin retries (c4) both mint new runs.
`idempotency_key` is stored and never enforced; `payload_hash` is stored and
never read (F16).

c2 and c4 share the *provider key* cause but need different repairs at the door:

- c2 is stopped by enforcing the idempotency key at `submit`.
- c4 is **not**, because `retry()` deliberately creates a new run (`core.py:154-177`). Only an approval-derived provider key stops it.

**c4 also contains a second, separate fault.**
`{"event":"worker_stall","local_status":"running"}` precedes the retry. The run
was stuck because nothing owns or times out a run (F7) — that is G4. The
operator's retry was a *response* to G4, and it duplicated because of G2. Two
faults stacked, which is why c4 looks like both.

- **Falsified if:** submitting the same approved request twice produced one set of drafts. It produces 8 (F4, F5).

### G3 — The same key with different content is accepted silently (c5)

`deploy-303` submitted with payload A then payload B. Both are accepted, both
get rows, both would deploy. The column that could detect this (`payload_hash`)
is written and never compared (F16).

Related to G2, distinct from it: G2 is "one approval deployed twice," G3 is "one
key claiming two different approvals." G2 wants dedupe. G3 wants rejection.

- **Falsified if:** the second submit raised. It does not.

### G4 — Nothing owns a run (c8, and the stall inside c4)

Two `worker_claim` events for `run-14` and no claim mechanism exists (F7). Both
workers run the whole payload, both build receipts, and their concurrent
provider writes erase each other (F8).

- **Falsified if:** a second worker calling `run_once` on a claimed run declined to proceed. Nothing stops it.

### G5 — Cancellation is advisory (c6)

`cancel` writes a status (`core.py:147-152`). `run_once` never reads it, and its
final `UPDATE ... status='done'` (`core.py:211-219`) is unconditional, so a
finishing worker overwrites the cancel. `RunCancelled` is dead code (F6).

- **Falsified if:** a cancelled run stopped writing and stayed cancelled. It reaches `done` and writes all 4 drafts (F6).

### G6 — Recovery has no per-run failure isolation (c9)

`recover()` is a bare loop (F15). One raise ends the pass and every run behind
it stays `running` forever. Matches "two campaigns behind it never went out."

**Honest status: cause is visible in the code, the reproduction was rigged.** It
was made to raise with an invented payload (missing `source_sha256`), because
payload C is not in `fixtures/` (I3). Attempting it with a naturally-occurring
error did not reproduce the stuck state. Treat G6 as code-visible, not observed.

- **Falsified if:** a failing run in the recovery set left the runs behind it deployable. Untested against a natural trigger — this is the weakest claim here.

### G7 — Ambiguous writes have no resolution path (c10)

**c10 is not a fault.** The log shows a write returning `gateway_timeout,
retryable:true` and a readback finding the object. That is the *correct* shape:
an idempotent provider plus a readback resolves the ambiguity deterministically.
c10 is evidence **for** readback-based verification, not against it.

What it does expose: `run_once` has no `try/except` around `create_draft` at
all, so a timeout would propagate and the run would die mid-payload. The gap is
the missing reconciliation path, not the ambiguity itself.

- **Falsified if:** a write that raised, followed by a matching readback, were already treated as success. There is no such path.

### Controls and noise

**c1 — successful crash recovery, with a caveat.** It passes *because*
`recover()` reuses the same `run_id`, which keeps the provider key stable by
accident of G2's design, not by intent. And its receipt is still an unchecked
constant. So c1 is a control for **duplicates only**, not for verification.

**c3 — ambiguous from the evidence.** Two different idempotency keys
(`deploy-207a`, `deploy-207b`), the same payload version, 8 provider objects.
Two readings fit the log equally well:

- Two genuinely separate approvals of the same content. 8 objects is correct.
- One approval submitted twice by a client that regenerated its key. 8 objects is the duplication the operator is complaining about.

**Nothing in the evidence distinguishes them.** The keys differ, and the log
records no operator intent. `stress.py` submits the same payload under 12
different keys and expects 48 objects (F17), but that is the *starter's*
expectation encoded in a test harness — it is not evidence about what an
operator meant, and it should not be cited as though it were.

**Design choice, made in spite of the ambiguity:** the operator's idempotency key
is the unit of idempotency. Different keys mean different deployments. This is
chosen because the key is the only stable identifier the operator controls, and
because merging on payload content would make two intentional deployments of the
same assets impossible to express. It does **not** resolve c3 — it decides how
the system will behave when c3's shape recurs.

- **What would resolve it:** an operator confirming whether `207a`/`207b` were one approval or two. Until then c3 stays ambiguous and is not counted as a fixed fault.

**c11 — out of scope.** `thumbnail_render` exists nowhere in the repository
(F20) and is not on the deployment path. Treated as TASK.md's "at least one is
unrelated" and not investigated further. Revisited only if it turns out to touch
how a deployment is carried out or simulated.

---

## 3. Operator promise

> **A deployment reported as `complete` has just been read back from HubSpot,
> object by object, and every approved asset was found, matching what was
> approved, as a draft. If we could not confirm that, we say `divergent` and name
> what differs. If we could not read HubSpot at all, we say `unknown` and do not
> guess.**
>
> **A run finishing is history, not proof.** `done` means the run completed its
> work at the time it ran. It is never presented on its own as evidence that
> HubSpot matches now — that question is answered only by a fresh check.
>
> An approved request deploys once per idempotency key. Submitting the same key
> again, retrying it from the admin panel, or recovering it after a crash reuses
> the same HubSpot objects rather than creating new ones. Submitting the same key
> with different content is refused before anything is written.
>
> A cancelled deployment stops writing and stays cancelled.
>
> A deployment that cannot complete fails by itself and does not hold up the
> deployments behind it.

**Deliberately not promised:** rollback, deletion, cleanup of anything already
written, exactly-once execution, or that HubSpot still matches a second after we
looked. The provider exposes only `create_draft`, `read`, and `list_objects` —
there is no delete and no update, so none of those could be proved here.

---

## 4. Definition of complete

### Two independent axes

The starter conflates "did the run finish" with "is HubSpot right." These are
separate questions with separate lifetimes, and the operator report is largely a
symptom of merging them. They are split:

| Axis | Values | Lifetime |
|---|---|---|
| **Workflow status** — what the run did | `pending`, `running`, `done`, `cancelled`, `failed` | Historical. Append-only in meaning: once a run is `done` it stays `done`. |
| **Provider certification** — what HubSpot holds *now* | `complete`, `divergent`, `unknown` | Point-in-time. Recomputed on every check; the previous value is never reused. |

`done` + `divergent` is a legal and expected combination: the run did its job,
and something changed in HubSpot afterwards. That pair is exactly what the
operator saw and could not express. Certification is never written back over
history, and history is never presented as certification.

`unknown` is deliberately distinct from `divergent`. If the provider cannot be
read — a torn read, an exception, a half-written state file (F8) — the answer is
"we do not know," not "it is wrong." Collapsing the two would make a transient
read failure look like data loss.

### Certification is `complete` only when all of these hold, from a fresh readback

1. **Coverage** — every approved asset has exactly one corresponding provider draft.
2. **No omissions** — no approved asset is missing.
3. **No extras** — scanning the provider for objects under this deployment's key namespace yields exactly the approved set, no duplicates and no strangers.
4. **Identity match**, per object, on the fields that actually identify it: `external_key`, `source_asset_id`, `source_sha256`, `object_type`, and `status == "draft"`.
5. **Name match, not name uniqueness** — `display_name` equals the **provider-normalized** form of the approved name. Two objects in the same deployment may legitimately carry the same normalized value; that is not a discrepancy and never fails certification. See below.
6. **Freshness** — the verdict comes from reading the provider now, never from replaying a stored receipt or a stored certification.
7. **Honesty** — if 1–5 cannot be proved, certification is `divergent` and the specific failures are named. If the provider cannot be read, certification is `unknown`.

### Rules at the door and at completion

8. **`done` requires `complete` at completion time.** A run that finishes its writes but cannot certify `complete` ends `failed`, not `done`. Later divergence does not retroactively change it.
9. **`done` is not a claim about now.** No operator-facing surface may present workflow status alone as evidence about HubSpot's current contents. Every surface that reports status also reports a certification and when it was computed.
10. **Empty request** — an approved request with zero assets is **rejected at `submit`** and never becomes a run. A deployment of nothing is not a deployment, and letting it through is exactly what produces "verified campaign, nothing in HubSpot." Stated as a rule, not a fixture special-case: `len(assets) == 0` is refused for any payload. The alternative — run it and certify `divergent` — is defensible; this roadmap chooses to fail earlier and louder.
11. **Repeat safety** — same key + same payload returns the existing run and produces no additional provider objects.
12. **Conflict** — same key + different `payload_hash` raises `IdempotencyConflict` at `submit`, **before any provider write**.

### Identity: what names a HubSpot object

`external_key = idempotency_key + ":" + asset_id`

That is the whole rule. Two properties follow, and both matter:

- **Stable across runs.** The key contains nothing that varies per run — no `run_id`, no UUID, no timestamp. A retry, a recovery pass, and a re-submitted approval all compute the same key and land on the same object. This is what removes G2.
- **`payload_hash` is deliberately excluded.** It guards the door (rule 12) and does not name the object. If it were part of the key, identity would drift with any change in how a payload is serialized — a reordered key, a whitespace change — and the same approval would silently acquire a second set of drafts. Conflicting content is refused before a write, so it never needs to be encoded in the key afterwards.

**`display_name` is not identity.** It is compared as a field (rule 5) and never
used to distinguish objects, deduplicate them, or reject a request.
`deployment_request.json` is shaped to prove the point: `asset-email-002` and
`asset-email-003` both normalize to `'Summer 2026 ABM campaign email - product'`
(F10, I6). Those are two distinct, correctly deployed drafts that happen to share
a label. A design that treated a shared name as a collision would reject a valid
approval — so this roadmap **does not** add any such rule, and
`deployment_request.json` remains a fully deployable, successfully certifying
case.

---

## 5. Planned changes

Every change labelled exactly one of **ROOT CAUSE FIX** / **FALSE-PASS
PREVENTION** / **OBSERVABILITY ONLY** / **OUT OF SCOPE**.

Change ids are `CH*` so they cannot be confused with the event-log case ids
`c1`–`c11`.

| # | Change | Label | Addresses |
|---|---|---|---|
| CH1 | Provider `external_key` becomes `idempotency_key:asset_id` instead of `run_id:asset_id`. Retries, recovery, and re-submits land on the same HubSpot objects. `payload_hash` is not part of the key (§4, Identity). | **ROOT CAUSE FIX** | G2 (c2, c4) |
| CH2 | `UNIQUE` on `idempotency_key`. Same key + same `payload_hash` returns the existing `run_id`; different `payload_hash` raises `IdempotencyConflict` before any provider write. Finally reads the column from F16. | **ROOT CAUSE FIX** | G2, G3 (c2, c5) |
| CH3 | Reject zero-asset payloads at `submit`. | **FALSE-PASS PREVENTION** | F12, §4.10 |
| CH4 | Atomic claim: `owner`, `lease_expires_at`, `attempt` columns; claim via conditional `UPDATE` checked with `rowcount`. Verified — 20 racing threads, exactly 1 winner. Expired leases are reclaimable so a stalled worker does not strand a run. | **ROOT CAUSE FIX** | G4 (c8, c4's stall) |
| CH5 | Replace the `verified` boolean with a computed **certification** (`complete` / `divergent` / `unknown`) derived from a fresh readback against §4.1–4.5, plus `certified_at` and a `discrepancies` list. Never a literal. | **FALSE-PASS PREVENTION** | G1 (c7, c8) |
| CH6 | Split workflow status from certification (§4). `status` keeps `pending`/`running`/`done`/`cancelled`/`failed` and is historical; certification is recomputed on demand and never stored as truth. A run ends `done` only if it certified `complete` at completion; otherwise `failed`. | **ROOT CAUSE FIX** | G1, and the operator's "I have no way to know what this service promises" |
| CH7 | `audit` re-reads the provider and recomputes certification, returning it alongside the historical status and the time of the check. `deployment_summary` likewise never presents status alone. | **FALSE-PASS PREVENTION** | G1, §4.9 |
| CH8 | `cancel` sets `cancel_requested`; `run_once` checks it before every provider write and raises `RunCancelled`; the terminal status update becomes conditional on ownership and non-cancellation. | **ROOT CAUSE FIX** | G5 (c6) |
| CH9 | Per-run `try/except` in `recover`, plus a bounded `attempt` count so a permanently failing run ends `failed` and is surfaced rather than retried forever. | **ROOT CAUSE FIX** | G6 (c9) |
| CH10 | Wrap `create_draft` in a reconcile helper **outside** the provider: on exception, read back the expected key; matching → success, absent → bounded retry, present-but-different → fail loudly. | **ROOT CAUSE FIX** | G7 (c10) |
| CH11 | `retry` resets the existing run to `pending` and clears its lease instead of inserting a second row (which CH2's constraint would forbid anyway). | **ROOT CAUSE FIX** | G2 (c4) |
| CH12 | SQLite `timeout` and WAL on `_connect`, so contention blocks briefly instead of raising `database is locked`. Coordination around storage, no behavior change. | **OBSERVABILITY ONLY** | F9 |
| CH13 | The provider's lost-write and torn-read race (F8). **Not fixed.** It is an artifact of the fake (I1), TASK.md says the provider is not ours to change, and CH5 makes it visible instead of silent — as `divergent` when a write was lost, `unknown` when the state file cannot be parsed. | **OUT OF SCOPE** | F8 |
| CH14 | c11 `thumbnail_render`. Not in the deployment path, not in the repository. | **OUT OF SCOPE** | c11 |
| CH15 | Rollback, deletion, cleanup of duplicate drafts already in HubSpot. The provider has no delete. | **OUT OF SCOPE** | — |
| CH16 | Any rule keyed on `display_name` — uniqueness checks, collision rejection, name-based dedupe. **Explicitly not built.** `deployment_request.json` proves a shared normalized name is legitimate (§4, Identity; F10, I6). | **OUT OF SCOPE** | F10 |

**Minimum cut if time runs short**, in order: CH5, CH6, CH7, CH3 (the promise and
the false passes), then CH2, CH1 (duplicates), then CH8, CH4 (cancel and claim),
then CH9, CH10. CH11–CH12 last. If CH9/CH10 do not land, they move to
`DECISIONS.md` as known-open, which for G6 is honest anyway given it is the
least-proven group.

---

## 6. Acceptance tests

Rules for every test below:

- Expectations are **derived from the approved payload at runtime** — iterate `payload["assets"]`, compare against `len(payload["assets"])`.
- **No hardcoded `4`**, no fixture asset ids, no fixture hashes, no literal `40`. Read the limit from `FakeHubSpot.DISPLAY_NAME_LIMIT`.
- All three shapes are exercised. `deployment_request.json` is the workhorse — it deploys and certifies `complete`, and it is the only fixture that exercises both name truncation and a legitimately shared normalized name.
- Where a test needs a second, different payload under the same key, it **derives** one in-test from the loaded fixture rather than reaching for another file. Deriving is not fixture special-casing; hardcoding the derived value would be.
- Provider failure modes are simulated with a **wrapper around** `FakeHubSpot`, never by editing it.
- Each new test must be checked to **fail against the current code** before the fix lands. A test that passes on the starter proves nothing (F13, F14).

| Test | Shape used | Fails today because |
|---|---|---|
| Same key + same payload → one logical deployment; the second submit returns the same run id and adds no provider objects | full | 2 runs, 8 objects (F4) |
| Same key + changed payload → `IdempotencyConflict`, **and the provider is untouched** — assert object count is unchanged after the raise | full, then full with one field altered in-test | both accepted, both would write (F16) |
| `retry` creates no additional drafts | full | 8 objects (F5) |
| `external_key` is stable across runs — the key computed for an asset is identical before and after a retry and a recovery, and contains no `run_id` | full | key embeds a fresh UUID (F3) |
| Two approved assets whose names normalize to the same value both deploy and certify `complete` | **full** — `asset-email-002` and `asset-email-003` collide by name (F10) and must both succeed | (passes today by accident; kept as a guard against ever adding a name-uniqueness rule) |
| A `display_name` past the limit certifies `complete` — normalization is expected, not a discrepancy | full (three names exceed the limit) | nothing compared |
| Deleting a provider object makes a fresh check certify `divergent` | full | `audit` never opens the provider (F2) |
| Tampering with `source_asset_id` / `source_sha256` / `object_type` / `status` in provider state certifies `divergent` | full | nothing is compared (F1) |
| An unexpected object under this deployment's key namespace certifies `divergent` | full + one injected stray object | nothing scans for extras |
| An unreadable provider certifies `unknown`, not `divergent` and not `complete` | full, via a wrapper that raises on read | no such distinction exists |
| **Status and certification are independent** — after a successful run, delete an object; workflow status stays `done`, a fresh check certifies `divergent`, and no surface reports `done` without a certification | full | `done` and `verified` are welded together (F2) |
| A run that finishes writing but cannot certify `complete` ends `failed`, not `done` | full, via a wrapper that drops one write | every run ends `done` (F9) |
| Cancellation after one write prevents later writes and cannot reach `done` | full | status returns to `done`, all assets written (F6) |
| Two workers cannot both claim the same pending run | full | no claim exists (F7) |
| Concurrent different runs do not lose provider objects **or**, where the provider does lose them, no run certifies `complete` | full, N threads | 48 created / 36–44 held, all report done (F9) |
| One broken recovery candidate does not stop later good runs | full + a deliberately broken run | bare loop (F15) |
| Ambiguous write exception followed by a matching readback is reconciled as success | full, via a wrapper that writes then raises | no `try/except` around `create_draft` (G7) |
| Empty approved request is refused at submit and never yields a certification | empty | returns `verified: true` on zero objects (F12) |

Both starter tests stay on `deployment_request.json` and keep asserting what they
asserted. They are near-worthless as written (F13, F14) and are kept only as
regression ballast.

---

## 7. Mutation tests

Each acceptance check is paired with one deliberate implementation break that
must make **that** check fail. If a mutation lands and the suite stays green, the
check is decoration and gets rewritten. Run as a manual pass — revert each
mutation before applying the next.

| # | Behavior intentionally broken | Check that must fail |
|---|---|---|
| M1 | In `submit`, delete the lookup by `idempotency_key` and unconditionally `INSERT` a fresh `uuid4` row. | Same key + same payload → second submit returns a *different* run id and provider object count doubles. |
| M2 | In `submit`, keep the lookup but compare `idempotency_key` only, ignoring `payload_hash` — return the existing run instead of raising. | Same key + changed payload → no `IdempotencyConflict`; the new payload silently inherits the old payload's drafts. |
| M3 | Move the conflict check to *after* the first `create_draft` call instead of before it. | Same key + changed payload still raises, but the provider object count has changed — the "provider is untouched" half of the check fails. |
| M4 | Restore `external_key = f"{run_id}:{asset_id}"` in `run_once`. | `retry` produces a second full set of drafts, and the key-stability check sees a different key after recovery. |
| M5 | Include `payload_hash` in `external_key`, then re-submit the identical approval with its JSON serialized in a different key order. | Key stability fails — semantically identical approvals compute different keys and acquire a second set of drafts. |
| M6 | In the field comparison, assert only that `object_id` is truthy; drop the equality checks on `external_key`, `source_asset_id`, `source_sha256`, `object_type`, `status`. | Tampering with any of those in provider state still certifies `complete`. |
| M7 | Add `display_name` to the fields that must be **unique** across a deployment (or reject colliding names at `submit`). | The two-assets-sharing-a-normalized-name check fails — a valid approval is rejected or certified `divergent`. **This is the mutation that guards against re-introducing the rule this roadmap deliberately excludes (CH16).** |
| M8 | Set the expected `display_name` to the raw approved string instead of the normalized form. | A request whose names exceed the limit certifies `divergent` — a false discrepancy. Together with M7, this pins name handling from both sides: too strict on value, too strict on uniqueness. |
| M9 | In the certification routine, replace the per-key `provider.read()` with `json.loads(row["receipt_json"])["objects"]`. | Deleting a provider object still certifies `complete`. |
| M10 | Have `audit` return the certification stored on the receipt instead of recomputing it. | The status/certification independence check fails — a stale `complete` survives a real deletion. |
| M11 | Map an unreadable provider to `divergent` instead of `unknown`. | The unreadable-provider check fails; a transient read failure is reported as data loss. |
| M12 | Let `run_once` set `status='done'` regardless of the certification it computed. | The finished-but-uncertifiable run is reported `done` instead of `failed`. |
| M13 | Have `deployment_summary` return workflow status with no certification field. | The "no surface reports `done` without a certification" assertion fails. |
| M14a | Remove the `cancel_requested` read from the per-asset loop in `run_once`. | A run cancelled after the first write keeps writing the remaining assets. |
| M14b | Make the terminal update unconditional again — drop `AND status='running' AND cancel_requested=0 AND owner=?`. | A cancelled run reaches `done`. |
| M15 | In the claim, execute the conditional `UPDATE` but ignore `cursor.rowcount` and proceed regardless. | N workers racing one pending run → more than one proceeds to write. |
| M16 | Build the certification from the dicts `create_draft` returned, rather than from a fresh readback taken after every write completes. | A run whose object was erased by a concurrent writer still certifies `complete` — this is the exact c8 mechanism. |
| M17 | In certification, iterate approved assets only; drop the reverse scan for provider objects under this deployment's key namespace. | A stray object goes unnoticed and the run still certifies `complete`. |
| M18 | Remove the per-run `try/except` in `recover` so the first exception escapes the loop. | Runs queued behind a broken recovery candidate stay undeployed. |
| M19 | Delete the reconcile wrapper; call `provider.create_draft` directly and let exceptions propagate. | A write that raises after persisting, followed by a matching readback, kills the run instead of being reconciled as success. |
| M20 | Remove the zero-asset guard from `submit`. | An empty approved request gets a run id and a `complete` certification over zero objects. |

**Test-suite integrity mutation.** Separately, change
`FakeHubSpot.DISPLAY_NAME_LIMIT` and swap which fixture a test loads. Any test
that hardcoded `40`, `4`, a fixture asset id, or a fixture hash breaks in a way
that has nothing to do with the behavior under test. That break is the signal —
those tests get rewritten to derive from the payload.

---

## 8. Verification, demo, and stress

`make demo`, `make test`, `make stress` all keep working, and all three keep
using `deployment_request.json`. No fixture is rejected by this design.

**`make demo`** — the scenario is unchanged; what changes is what it reports.
The point is now the *split* between history and certification:

1. Deploy `deployment_request.json`, crash partway, restart, recover. Status `done`, certification `complete`, computed from a live readback. Note in the output that two of the four drafts share a normalized display name and that this is correct, not a defect.
2. Delete two objects from the provider's state file, exactly as `demo.py:42-45` does today. Press "Check again". **Workflow status is still `done` — history did not change. Certification is now `divergent`, naming the two missing objects, with the time of the check.** Against the starter's output — which reports `verified: true, all_present: true` over the same state — this is the whole demonstration.
3. Submit `deployment_request_empty.json`. Refused at `submit`; no run, no certification, nothing written.

**`make stress`** — unchanged fixture and unchanged shape. It should still show
the provider holding fewer objects than the runs created (CH13 is out of scope),
but **no run may certify `complete` while its objects are missing**, runs that
cannot certify must end `failed` rather than `done`, and reads that fail against
a half-written state file must certify `unknown` rather than `divergent`. Same
short count, honest verdict.

**`make test`** — the two starter tests stay on the full fixture (see §6), plus
the new acceptance suite.

---

## 9. Out of scope

Listed, not argued.

- Real HubSpot integration, credentials, network calls of any kind.
- Rollback, deletion, or cleanup of anything already written. The provider exposes `create_draft`, `read`, `list_objects` and nothing else.
- Distributed leases across hosts. The lease here coordinates workers against one SQLite file and claims no more than that.
- Long-term retry scheduling, backoff daemons, dead-letter queues.
- Any UI.
- Schema migration. Every entry point builds its own database.
- The provider's internal lost-write and torn-read race (CH13, F8).
- Any rule keyed on `display_name` uniqueness (CH16).
- c11 `thumbnail_render` (F20).
- Queue, workflow framework, container, cloud service, real model, OAuth.

### Production posture

The code should read as production code — typed, small functions, no bare
`except`, structured failure values instead of booleans, every decision derived
rather than asserted. It should **not** grow the machinery a real deployment
would need on top of that. The lease is a `WHERE` clause, not a lock service.
Reconciliation is a bounded readback, not a scheduler. The provider is a JSON
file behind an interface we do not touch. Where a real system would need more,
that belongs in §10 as a stated limit, not as a half-built version of the real
thing.

---

## 10. What this still cannot promise

For `DECISIONS.md`:

- **Certification is point-in-time and expires the instant it is returned.** HubSpot can change a second later. `complete` means "complete when we looked," and the timestamp is reported for exactly that reason. There is no watch, no subscription, and no continuous reconciliation.
- The provider can still lose writes. We detect it and report `divergent`; we do not prevent it (CH13).
- `unknown` is honest but not actionable on its own. A run stuck at `unknown` needs a human or a later successful read; nothing here resolves it automatically.
- There is still a window between a provider write and the local record. Recovery closes it by re-reading, not by making the two atomic.
- Identity is only as stable as the operator's idempotency key. If a client regenerates its key for the same approval — c3's ambiguous shape — this design will deploy twice and consider both correct. That is the accepted cost of choosing the key as the unit of idempotency, and it is the case least covered by this roadmap.
- Two drafts may share a display name in HubSpot and be hard for a person to tell apart. That is accepted deliberately (§4, Identity): the objects are distinct and correct, and a name-uniqueness rule would reject valid approvals. The mitigation is that `external_key`, `object_id`, and `source_asset_id` all remain distinct and are what the system reasons about.
- Nothing is ever cleaned up. Duplicate drafts already in HubSpot from before this change stay there — the provider has no delete.
- G6 (c9) is addressed at a cause we reasoned about but never reproduced with a natural trigger.
