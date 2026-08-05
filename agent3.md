╭─── Claude Code v2.1.222 ─────────────────────────────────────────────────────╮
│                                                    │ Tips for getting        │
│                 Welcome back Nitu!                 │ started                 │
│                                                    │ Run /init to create a … │
│                       ▐▛███▜▌                      │ ─────────────────────── │
│                      ▝▜█████▛▘                     │ What's new              │
│                        ▘▘ ▝▝                       │ Fixed worktree-isolate… │
│    Opus 5 · Claude Max ·                           │ Fixed PreToolUse auto-… │
│    nityanth.maramreddy@gmail.com's Organization    │ Fixed `/usage-credits`… │
│          ~/Downloads/02-durable-run-relay          │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯


❯ read through the codebase and all the markdown files and the @TASK.md and     
  @README.md and the other folders and their contents. Do a read-only           
  mutation-planning review. You are prohibited from editing files.  but         
  afterwards Map each major guarantee to the smallest test that should fail if  
  that such behavior and output is broken for the following: certification,     
  live audits, worker claiming, cancellation, recovery isolation, and timeout   
  reconciliation. then id like for you to Identify weak or missing tests and    
  give exact test names and proposed mutations for us to develop upon. ill      
  confirm if they are actually important.                                       
  ⎿  Read TASK.md (95 lines)
  ⎿  Read README.md (29 lines)

  Searched for 2 patterns, read 9 files, listed 1 directory, ran 3 shell commands (ctrl+o to expand)

⏺ Read all of it: TASK.md, README.md, ROADMAP.md (572 lines), relay/core.py, all
  7 test files, fixtures, event log, demo.py, stress.py. Ran suite: 61 tests, 
  13 fail — CH4/CH8/CH9/CH10 are red-TDD, everything else green. No files
  edited.

  State of implementation: CH1, CH1b, CH2, CH3, CH5, CH5b, CH6, CH7, CH11
  landed. CH4 (claim/lease), CH8 (cancel), CH9 (isolation/bound), CH10
  (reconcile), CH12 (WAL) not landed.

  ---
  1. Guarantee → smallest test that fails first

  Certification (CH5/CH5b, §4.1–4.7)

  ┌───────────────┬────────────────────────────────────────────────┬───────┐
  │               │                                                │ Mutat │
  │   Guarantee   │             Smallest failing check             │ ion   │
  │               │                                                │  it   │
  │               │                                                │ kills │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Approved      │ CompleteDeploymentTest → DivergenceTest.test_a │       │
  │ asset missing │ _deleted_object_certifies_divergent            │ M9    │
  │  from HubSpot │                                                │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Identity/prov │ test_altering_an_identity_field_certifies_dive │       │
  │ enance fields │ rgent (subTest per field)                      │ M6    │
  │  match        │                                                │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ No extras in  │ test_a_stranger_in_the_namespace_certifies_div │ M17   │
  │ namespace     │ ergent                                         │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Name compared │                                                │       │
  │  normalized,  │ test_names_past_the_limit_certify_complete     │ M8    │
  │ not raw       │                                                │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Name is never │ test_two_assets_sharing_a_normalized_name_cert │ M7    │
  │  identity     │ ify_complete                                   │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Verdict is a  │ test_certification_consults_the_provider       │ M9    │
  │ reading       │                                                │       │
  ├───────────────┼────────────────────────────────────────────────┼───────┤
  │ Missing ≠     │ test_a_key_error_is_divergent_and_an_unreadabl │ M11,  │
  │ unreadable    │ e_state_is_unknown                             │ M11b  │
  └───────────────┴────────────────────────────────────────────────┴───────┘

  Live audits (§4.9, CH7)

  Guarantee: Every surface carries a certification
  Smallest check: certification_of() helper raise in test_reported_outcome.py
  Mutation: M13
  ────────────────────────────────────────
  Guarantee: Surfaces agree
  Smallest check: test_every_surface_answers_the_same_way_about_hubspot
  Mutation: M10
  ────────────────────────────────────────
  Guarantee: Not cached between calls
  Smallest check: test_the_summary_reads_hubspot_every_time_it_is_asked
  Mutation: M10
  ────────────────────────────────────────
  Guarantee: Status is history, verdict is now
  Smallest check: test_a_stored_receipt_does_not_survive_a_change_in_hubspot
  Mutation: M10

  Worker claiming (CH4) — all red

  ┌───────────┬─────────────────────────────────────────────────┬──────────┐
  │ Guarantee │                 Smallest check                  │ Mutation │
  ├───────────┼─────────────────────────────────────────────────┼──────────┤
  │ One owner │ test_only_one_of_many_workers_executes_a_pendin │ M15      │
  │  per run  │ g_deployment                                    │          │
  ├───────────┼─────────────────────────────────────────────────┼──────────┤
  │ Live      │                                                 │          │
  │ claim     │ test_a_second_worker_cannot_execute_an_actively │ M15      │
  │ blocks    │ _owned_run                                      │          │
  │ intruder  │                                                 │          │
  ├───────────┼─────────────────────────────────────────────────┼──────────┤
  │ Crash ≠   │ test_a_dead_workers_run_is_not_taken_before_its │ lease-ch │
  │ free-for- │ _lease_lapses                                   │ eck      │
  │ all       │                                                 │ removal  │
  ├───────────┼─────────────────────────────────────────────────┼──────────┤
  │ Lease     │ test_an_expired_lease_is_reclaimed_and_the_run_ │ infinite │
  │ lapses    │ finishes                                        │  lease   │
  └───────────┴─────────────────────────────────────────────────┴──────────┘

  Cancellation (CH8) — all red

  Guarantee: Checked before every write
  Smallest check: test_cancellation_after_the_first_write_stops_the_later_writes
  Mutation: M14a
  ────────────────────────────────────────
  Guarantee: Terminal update conditional
  Smallest check: test_a_cancelled_run_cannot_be_marked_done
  Mutation: M14b — see finding C1, it does not actually kill M14b
  ────────────────────────────────────────
  Guarantee: Not restarted later
  Smallest check: test_a_cancelled_deployment_is_not_started_by_a_later_worker
  Mutation: claim ignores cancel
  ────────────────────────────────────────
  Guarantee: Recovery skips it
  Smallest check: test_recovery_does_not_resume_a_cancelled_deployment
  Mutation: recover selects status only

  Recovery isolation (CH9) — all red

  Guarantee: One bad run ≠ dead queue
  Smallest check: test_a_broken_run_does_not_strand_the_deployments_behind_it
  Mutation: M18
  ────────────────────────────────────────
  Guarantee: Attempts bounded
  Smallest check: test_a_run_that_can_never_be_written_stops_being_retried
  Mutation: drop bound
  ────────────────────────────────────────
  Guarantee: failed terminal for automatic path
  Smallest check: test_recovery_never_reclaims_a_run_that_exhausted_its_attempts

  + test_recovery_does_not_reclaim_a_failed_run
  Mutation: M18b

  Timeout reconciliation (CH10) — 2 red, 1 green-but-hollow

  Guarantee: Persisted-then-raised = success
  Smallest check:
  test_a_write_that_persisted_before_raising_is_reconciled_as_success
  Mutation: M19
  ────────────────────────────────────────
  Guarantee: Never-landed ≠ success
  Smallest check:
  test_a_write_that_raised_without_persisting_is_never_reported_complete
  Mutation: passes today, kills nothing — finding T1
  ────────────────────────────────────────
  Guarantee: Retry bounded
  Smallest check: test_a_write_that_never_lands_is_not_retried_forever
  Mutation: unbounded loop

  ---
  2. Weak or missing — exact names + mutations

  Ordered by what I think matters. You confirm.

  C1 — test_a_cancelled_run_cannot_be_marked_done does not kill M14b

  Park is at after_write=1 with 4 assets, so the cancelled worker hits the
  per-write check at write 2 and raises RunCancelled — the terminal UPDATE is
  never reached. Drop the AND status='running' AND cancel_requested=0 from the
  terminal update and the test still passes: M14a's guard is doing all the work.
  The unconditional terminal update is only observable when no write remains.

  - Add: CancellationTest.test_a_cancel_landing_after_the_last_write_still_holds
  - Shape: stalled_worker(after_write=len(self.payload["assets"])), cancel,
  release.
  - Mutation: M14b alone (terminal update unconditional, per-write check kept).
  Must fail.

  W1 — recover() bypassing the claim is invisible

  No test drives two recover() passes concurrently. A recover() that selects
  rows and calls run_once without claiming passes all 13 red tests once CH4
  lands — and that is c8's exact shape (two worker_claim, one run).

  - Add: WorkerClaimTest.test_two_concurrent_recovery_passes_execute_a_run_once
  - Mutation: in recover, skip the claim / call run_once directly on selected
  rows.

  W2 — zombie owner overwrites the reclaimer (fencing)

  Lease expiry mid-run is untested in the dangerous order. A parks, lease
  lapses, B reclaims and finishes, A wakes and writes its terminal update over
  B's receipt.

  - Add: WorkerClaimTest.test_a_worker_whose_lease_lapsed_cannot_write_the_termi
  nal_status
  - Mutation: terminal update conditional on status='running' only, drop AND 
  owner=?.

  C2 — cancel() can rewrite history and cancels runs that do not exist

  core.py:272 is an unconditional UPDATE. Cancelling a done run flips it to
  cancelled, contradicting §4's "once a run is done it stays done".
  cancel("no-such-id") succeeds silently while retry raises KeyError. Zero
  coverage — grep confirms.

  - Add: CancellationTest.test_cancelling_a_finished_deployment_does_not_rewrite
  _its_history
  - Add: CancellationTest.test_cancelling_an_unknown_run_is_refused
  - Mutation: none needed for the first — it fails on today's code. That is the
  point.

  C3 — retry resurrects a cancelled run

  retry sets status='pending' unconditionally with no cancellation check. §4.14
  makes retry the way out of failed; nothing says it reopens a cancel, and
  nothing tests it.

  - Add: CancellationTest.test_retry_does_not_reopen_a_cancelled_deployment (or
  decide it should, and pin that instead — a design call for you)
  - Mutation: live behavior today.

  A1 — all_present and checked_objects have zero assertions

  Grep: no test names either field. all_present is the lineal descendant of the
  starter's F2 field. Today all_present can be true beside certification: 
  divergent (field mismatch, stray object) — an operator reads success next to a
  failure verdict.

  - Add: DeploymentSummaryTest.test_no_audit_field_reports_success_beside_a_dive
  rgent_verdict
  - Mutation: all_present = certification != UNKNOWN (drop the missing check),
  and separately hardcode all_present=True. Both survive the current suite.

  A2 — Relay.certify() is public and completely untested

  Grep: never called by any test, demo.py, or stress.py.

  - Decide: cover it or delete it. A public method with no check is a surface an
  operator can be handed.
  - Mutation: make certify return
  json.loads(row["receipt_json"])["certification_at_completion"]. Suite stays
  green.

  R1 — the unknown-at-completion path is not pinned

  test_a_run_that_cannot_certify_complete_ends_failed only drives the divergent
  route. A run that writes everything and then meets a torn read (F8 — the
  likeliest real case, and what §8 says make stress must show) has no test.

  - Add:
  FailedRunTest.test_a_run_that_cannot_read_hubspot_at_completion_ends_failed
  - Mutation: status = "failed" if verdict["certification"] == DIVERGENT else 
  "done" — treats unknown as done. Nothing currently fails.

  K1 — M5c survives the whole suite

  ROADMAP §6 lists the writer/certifier agreement test; it was never written.
  test_deployed_objects_are_named_by_the_approval_not_the_run compares held keys
  against relay.core.external_key — so an inline f-string in run_once
  duplicating today's formula passes. The roadmap calls this out as the one
  false-divergent indistinguishable from real data loss, and the guard is
  absent.

  - Add: ProviderKeyIdentityTest.test_writer_and_certifier_derive_keys_from_the_
  same_function
  - Shape: patch relay.core.external_key to a different valid injective
  encoding, deploy, assert complete.
  - Mutation: M5c — build the key inline in run_once; also inline in
  _expected_objects.

  V1 — list_objects and read failing independently is untested

  Every UnreadableProvider in the suite fails both methods. A wrapper that fails
  only list_objects (reads fine) or only read is untested, and _certify wraps
  both in one try.

  - Add: UnknownTest.test_a_listing_failure_alone_certifies_unknown
  - Add: DivergenceTest.test_an_empty_listing_with_readable_objects_still_finds_
  strangers
  - Mutation: move listed = self._list_provider_objects() outside the try —
  ProviderUnreadable escapes uncaught to the operator surface.

  R2 — recovery isolation is tested only against provider failures

  RefusesOneDeployment raises from the provider. c9's actual failure was
  payload_version: "C" — a payload the service could not handle
  (asset["source_sha256"] → KeyError, before any provider call).

  - Add: RecoveryIsolationTest.test_a_malformed_run_does_not_strand_the_deployme
  nts_behind_it
  - Shape: write a malformed payload row directly, or submit one whose asset
  lacks source_sha256.
  - Mutation: narrow recover's except to the provider error type. Provider test
  passes, this one fails.

  T1 — test_a_write_that_raised_without_persisting_is_never_reported_complete 
  proves nothing

  It is green on unfixed code. And it stays green under the dangerous mutation:
  a reconciler that treats any exception as success still ends failed with a
  divergent certification, because certification-at-completion catches it. The
  test is asserting CH5's work, not CH10's.

  - Rewrite as: AmbiguousWriteTest.test_a_write_that_never_landed_is_resolved_by
  _reading_not_by_the_exception
  - Assert the discrepancies name the absent keys, and that the reconciler
  issued a readback for each failed write (counting wrapper).
  - Mutation: reconciler returns success on exception without reading back.

  T2 — "present-but-different → fail loudly" is entirely missing

  CH10 names three outcomes; only two have tests. Also: FakeHubSpot.create_draft
  raises IdempotencyConflict on key reuse with different content — a legitimate
  provider outcome with no test anywhere for what run_once does with it.

  - Add:
  AmbiguousWriteTest.test_a_key_already_holding_a_different_object_fails_loudly
  - Add: AmbiguousWriteTest.test_a_provider_idempotency_conflict_is_never_reconc
  iled_as_success
  - Mutation: reconciler treats "key present" as success without comparing
  identity fields.

  R3 — M18c is only caught if the bound lives in run_once

  test_retry_is_the_only_way_out_of_failed drives run_once directly after retry.
  If CH9's bound is checked in recover() instead, M18c (retry leaves the
  attempt count exhausted) survives.

  - Strengthen: same test, drive the post-retry attempt through recover() as
  well as run_once.

  S1 — make stress asserts nothing

  TASK.md: "Read what it reports; it is not there for decoration." §8 states two
  checkable claims — no run certifies complete with objects missing, and status
  counts converge across the three recovery passes. stress.py only prints;
  nothing fails if either breaks.

  - Add: test_stress_invariants.py →
  test_no_run_certifies_complete_while_its_objects_are_missing,
  test_status_counts_converge_across_recovery_passes
  - Mutation: M18b — counts keep moving across passes.

  W3 — the 20-worker claim test can pass for the wrong reason

  test_only_one_of_many_workers_executes_a_pending_deployment counts completed 
  == 1. Nineteen sqlite3.OperationalError: database is locked satisfy that
  assertion just as well as nineteen clean claim refusals. Without CH12 (WAL +
  timeout) that is the likely outcome.

  - Strengthen: assert the 19 declines are the claim refusal, not
  OperationalError.
  - Mutation: remove CH12's timeout/WAL from _connect. Test should fail; today
  it would not.

  W4 — run_once on a done run re-executes it

  core.py:471 sets running unconditionally, no status guard. Untested.

  - Add: WorkerClaimTest.test_a_finished_run_is_not_executed_again
  - Mutation: claim accepts any status.

  W5 — retry during active execution creates two executors

  CH11 says retry clears the lease. Pressing retry on a run a worker is actively
  executing hands it to a second worker while the first still writes.
  Self-inflicted by the design, untested.

  - Add: WorkerClaimTest.test_retry_during_active_execution_does_not_produce_two
  _executors

  A3 — certified_at is only checked for truthiness

  A timestamp computed once at Relay.__init__ passes every test.

  - Add: FreshnessTest.test_each_check_carries_its_own_time
  - Mutation: cache certified_at on the instance. Low severity — the
  CountingProvider tests already prove reads happen — include only if cheap.

  ---
  Two I would not build

  - N1 — DISPLAY_NAME_LIMIT variation test. Requires a wrapper that re-truncates
  around the provider to be meaningful. ROADMAP already covers it as a manual
  suite-integrity pass. Cost > value at 2h.
  - N2 — malformed assets type (string, dict). No tie to the operator report.
  TASK.md: "Don't repair behavior you can't tie to the operator's report."

  ---
  Highest-value four, if you cut: C1 (a planned mutation the planned test does
  not kill), W1 (recover bypassing the claim reintroduces c8 through the fix for
  c8), K1 (the roadmap named this test and it was never written), A1
  (all_present is the starter's false-pass field, still uncovered).

✻ Worked for 5m 53s

※ recap: Goal: harden test coverage for this deployment relay. I finished the read-only mutation review and listed weak or missing tests with proposed mutations across all six areas. Next: you confirm which findings matter so I can write those tests. (disable recaps in /config)