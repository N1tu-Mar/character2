Decisions

This document records decisions and evidence actually produced during theexercise. Unproved claims and known gaps are stated explicitly.

Time actually spent

About 2.5 hours. TASK.md targets 2 and asks to stop at 2.5, so thisran to the stop line.

The split below is read off commit timestamps rather than estimated. Boundariesare the commits named in each row; git log --date=format:'%H:%M:%S'reproduces it.

Phase

Boundary

Elapsed

Evidence, probes, and ROADMAP.md

before 4125a1b (13:30:10)

not visible in git — see note

Roadmap revisions after the first commit

634a54f 13:30:29 → 763b140 13:53:04

~23 min

Implementation and tests

763b140 13:53:04 → 98cf61d 15:42:54

~1 h 50 min

Write-up (DECISIONS.md, SUBMISSION.md)

after 15:42:54

~17 min; not separately committed

Total working time

evidence through final write-up

about 2 h 30 min

Note on the first row. The baseline commit (4125a1b) and the roadmapcommit (634a54f) are 19 seconds apart, so the evidence gathering and theroadmap were both done before the repository was first committed and leave notimestamp behind. The probes themselves are in roadplanning.md.

What the ordering shows, and it is the point of recording it: ROADMAP.mdis committed at 13:30:29 and the first test edit is at 14:27:27, an hour later.No source or test file was touched before the roadmap existed.

What this service promises an operator

Condensed from ROADMAP.md §3. The wording below preserves the sameoperator-facing guarantee while keeping the implementation caveats visible:

A deployment reported as complete has just been read back from HubSpot,object by object, and every approved asset was found as a draft carrying theidentity and provenance fields the service sent. If the service cannot confirmthat, it reports divergent and names what differs. If HubSpot cannot be read,it reports unknown and does not guess.

That is a claim about identity and presence, not about content.

A run finishing is history, not proof. done means the run completed its workat the time it ran; it is never presented on its own as evidence that HubSpotmatches now.

An approved request deploys once per idempotency key. Submitting the same keyagain, retrying it from the admin panel, or recovering it after a crash reusesthe same HubSpot objects rather than creating new ones. Submitting the samekey with different content is refused before anything is written.

A cancelled deployment stops the current execution and cannot be overwrittenby that worker or by automatic recovery. An explicit operator retry is adocumented override that reopens the existing deployment.

A deployment that cannot complete fails by itself and does not hold up thedeployments behind it.

When it is entitled to say a deployment succeeded. Only when a readbacktaken after the last write finds every approved asset present, with matchingexternal_key, source_asset_id, source_sha256, object_type,status == "draft", and provider-normalized display_name, and finds nothingextra under the deployment's key namespace. A run that finishes its writes andcannot certify complete ends failed, not done. This guarantee assumesasset_id values are unique within the approved request; repeated IDs are aknown input-validation gap documented below.

Two axes, never merged. Workflow status (pending/running/done/cancelled/failed) is history and is never rewritten by a later check.Certification (complete/divergent/unknown) is point-in-time and isrecomputed on every call. done + divergent is a legal, expected pair — it isexactly what the operator saw and had no words for.

Confirmed causes

Grouped by common cause, mapped to the case ids infixtures/deployment_events.jsonl. Full evidence table and falsificationconditions are in ROADMAP.md §1–§2.

Group

Cause

Cases

Confirmed by

G1

The receipt was not derived from anything. verified was a literal, and both operator surfaces re-read that literal instead of opening the provider.

c7, c8, c1 (partly)

Deleted two objects from the provider state file; audit still reported objects_deployed: 4, all_present: true, verified: true (F2)

G2

Identity came from the run, not the approval. The provider key contained a fresh per-run UUID, so every execution minted a new set of drafts.

c2, c4

Same key submitted twice → 2 runs, 8 objects; retry() → 8 objects (F4, F5)

G3

One key claiming two different approvals was accepted silently. payload_hash was written and never compared.

c5

deploy-303 accepted under payload A then payload B (F16)

G4

Nothing owned a run. Two workers executed the same run, both wrote, both built receipts.

c8, and the stall inside c4

No lease, claim, owner, or worker id existed anywhere (F7); two worker_claim events for run-14 in the log

G5

Cancellation was advisory. cancel wrote a status, run_once never read it, and the terminal UPDATE was unconditional.

c6

cancel() then run_once() → status back to done, 4 drafts written; RunCancelled was dead code (F6)

G6

Recovery had no per-run failure isolation. A bare loop ends on the first raise and abandons every run behind it.

c9

Code-visible (F15). Reproduction was rigged — see "least sure of" in SUBMISSION.md

G7

Ambiguous writes had no resolution path. No try/except around create_draft at all, so a timed-out write killed the run mid-payload.

c10

c10 is not a fault — an idempotent provider plus a readback resolves it correctly. What it exposed is the missing reconciliation path

Not faults, and why.

c1 — successful crash recovery. A control for duplicates only: it passedbecause recover() reused the same run_id, which kept the provider keystable by accident of G2's design. Its receipt was still an uncheckedconstant.

c3 — genuinely ambiguous. Two different idempotency keys (deploy-207a,deploy-207b), same payload version, 8 objects. Two separate approvals andone approval submitted twice by a client that regenerated its key both fit thelog exactly. Nothing in the evidence distinguishes them. I chose a rule(the operator's key is the unit of idempotency) that decides how the shapebehaves in future; it does not resolve c3, and c3 is not counted as fixed.

c10 — evidence for readback-based verification, not against it.

c11 — thumbnail_render appears nowhere in the repository (F20) and isnot on the deployment path. Treated as TASK.md's "at least one is unrelated".

Implemented, and proved by a check

Every row is green in the current suite. Change ids are ROADMAP.md §5.

Change

What it does

Check that fails first if it stops holding

CH1

One shared, length-prefixed external_key(idempotency_key, asset_id); no key built inline anywhere

test_provider_key_identity.ProviderKeyIdentityTest.test_deployed_objects_are_named_by_the_approval_not_the_run

CH1

Retry, recovery, and re-submit all land on the same objects

test_provider_key_identity.ProviderKeyIdentityTest.test_the_keys_survive_a_crash_a_recovery_and_a_retry

CH1

Encoding is injective — a colon in either component cannot collide

test_provider_key_identity.KeyEncodingTest.test_the_encoding_is_injective_over_colon_heavy_components

CH1

A namespace scan returns only its own deployment's objects

test_provider_key_identity.ProviderKeyIdentityTest.test_a_namespace_scan_returns_only_its_own_objects

CH1b

Empty idempotency_key / empty asset_id refused at submit

test_provider_key_identity.SubmitDoorTest.test_an_empty_idempotency_key_is_refused, …test_an_empty_asset_id_is_refused

CH2

Same key + same payload returns the existing run

test_submit_idempotency.SubmitIdempotencyTest.test_same_key_same_payload_returns_the_same_run

CH2

Same key + different payload raises before any provider write

test_submit_idempotency.SubmitIdempotencyTest.test_same_key_different_payload_conflicts_before_any_write

CH2

The race resolves through the UNIQUE constraint, not a prior SELECT

test_submit_idempotency.SubmitIdempotencyTest.test_concurrent_same_payload_submissions_converge_on_one_run, …test_concurrent_conflicting_submissions_leave_one_winner

CH2

An unrelated IntegrityError still reaches the caller

test_submit_idempotency.SubmitIdempotencyTest.test_an_unrelated_integrity_error_is_not_swallowed

CH3

Zero-asset request refused at submit, never becomes a run

test_provider_key_identity.SubmitDoorTest.test_a_zero_asset_payload_is_refused

CH4

Exactly one worker executes a pending run

test_worker_control.WorkerClaimTest.test_only_one_of_many_workers_executes_a_pending_deployment

CH4

A live claim is not stealable; a lapsed one is reclaimable

…test_a_dead_workers_run_is_not_taken_before_its_lease_lapses, …test_an_expired_lease_is_reclaimed_and_the_run_finishes

CH5

Certification is computed from a fresh readback, never a literal

test_certification.CompleteDeploymentTest.test_a_correct_full_deployment_certifies_complete

CH5

Every identity/provenance field is compared, one at a time

test_certification.DivergenceTest.test_altering_an_identity_field_certifies_divergent

CH5

The reverse scan catches an object nobody approved

test_certification.DivergenceTest.test_a_stranger_in_the_namespace_certifies_divergent

CH5

Name is compared normalized, and a shared normalized name is legitimate

test_certification.CompleteDeploymentTest.test_names_past_the_limit_certify_complete, …test_two_assets_sharing_a_normalized_name_certify_complete

CH5b

Missing (KeyError) is divergent; unreadable is unknown; pinned in one test so neither except can absorb the other

test_certification.DivergenceTest.test_a_key_error_is_divergent_and_an_unreadable_state_is_unknown

CH6

A run that finishes writing but cannot certify complete ends failed

test_reported_outcome.FailedRunTest.test_a_run_that_cannot_certify_complete_ends_failed

CH7

Every operator surface recomputes from HubSpot and they cannot disagree

test_reported_outcome.DeploymentSummaryTest.test_every_surface_answers_the_same_way_about_hubspot, …test_the_summary_reads_hubspot_every_time_it_is_asked

CH8

A cancellation between two writes stops the second one

test_worker_control.CancellationTest.test_cancellation_after_the_first_write_stops_the_later_writes

CH8

A finishing worker cannot write done over a cancellation

test_worker_control.CancellationTest.test_a_cancelled_run_cannot_be_marked_done

CH8

Neither a later worker nor a recovery pass restarts a cancelled run

…test_a_cancelled_deployment_is_not_started_by_a_later_worker, …test_recovery_does_not_resume_a_cancelled_deployment

CH9

One broken run does not strand the runs behind it

test_recovery_and_reconcile.RecoveryIsolationTest.test_a_broken_run_does_not_strand_the_deployments_behind_it

CH9

A hopeless run stops being retried, and failed is not reclaimed

…test_a_run_that_can_never_be_written_stops_being_retried, …test_recovery_never_reclaims_a_run_that_exhausted_its_attempts

CH10

A write that persisted before raising is reconciled by reading it back

test_recovery_and_reconcile.AmbiguousWriteTest.test_a_write_that_persisted_before_raising_is_reconciled_as_success

CH10

A write that never landed is never reported complete, and is not retried forever

…test_a_write_that_raised_without_persisting_is_never_reported_complete, …test_a_write_that_never_lands_is_not_retried_forever

CH11

retry reopens the existing deployment and is the only way out of failed

test_submit_idempotency.RetryTest.test_retry_reopens_the_existing_deployment, test_reported_outcome.FailedRunTest.test_retry_is_the_only_way_out_of_failed

Suite: 62 tests, OK. Run five times consecutively, green every time — theworker and cancellation tests are thread-ordered with Barrier/Event and notest sleeps, so they are not timing-dependent.

Cause removed vs. symptom stopped

Stated separately because TASK.md scores the distinction, and because two ofthese are easy to describe as more than they are.

Removed a cause.

CH1 — the provider key no longer contains anything that varies perattempt, so a retry, a recovery pass, and a re-submit compute the same key.Duplicates cannot be produced by re-execution any more; nothing filters themout afterwards.

CH2 — the UNIQUE constraint decides, and the loser re-reads the row thatactually landed. Two approvals under one key can no longer both exist.

CH4 — a conditional UPDATE whose rowcount is the verdict. Two workerscan no longer both execute one run.

CH8 — cancel_requested is read before every provider write and theterminal write is conditional on ownership and non-cancellation. A finishingworker can no longer overwrite a cancel.

CH9 — per-run try/except in recover(). The head-of-line stall is gone.

CH10 — the exception is treated as a question and the provider answers it.A timed-out-but-landed write no longer kills a correct deployment.

Removed the cause of the false report, not the cause of the divergence.

CH5 / CH5b / CH6 / CH7 — these do not stop HubSpot from losing an object.They stop the service claiming it did not happen. The provider's lost-writeand torn-read race (CH13, F8) is untouched and deliberately out of scope: itis an artifact of the fake, and TASK.md says the provider is not ours tochange. What changed is that the loss now surfaces as divergent naming themissing key, and an unreadable state surfaces as unknown rather than aseither success or data loss.

Stopped a symptom.

CH3 — refusing a zero-asset request at the door removes the "verifiedcampaign, nothing in HubSpot" report. It repairs nothing upstream; whateverproduced an empty approved request is not diagnosed here. Running it andcertifying divergent was the defensible alternative; I chose to failearlier and louder.

CH9's bound and CH10's bounded retry are mitigations, not fixes. Theystop an unbounded spin. They do not make a hopeless write succeed.

What the coding agents did, and where I overrode them

I split the work into narrow agent sessions: one change set at a time, testsfirst where possible, one focused commit, then stop. The raw sessions arepreserved in roadplanning.md, session1.md, agent1.md, agent2.md,agent3.md, agent4.md, agent5.md, and agentmain.md.

The agents produced implementation and test drafts, but I made the load-bearingdesign decisions:

I chose a readable length-prefixed provider key instead of hashing the pair,because the reverse namespace scan must remain inspectable.

I required idempotency to resolve through the SQLite UNIQUE constraint andan insert-first race path, not SELECT-then-INSERT.

I made failed terminal for automatic recovery; only an explicit operatorretry reopens it.

I kept c3 ambiguous rather than claiming different keys proved differentoperator intent.

I rejected display-name uniqueness as identity because two approved assetslegitimately normalize to the same provider name.

I narrowed certification to identity and presence, not content integrity,because the provider exposes no content to hash-check.

The roadmap evidence table and mutation plan were reviewed and revised beforesource changes. I also reran the mutation pass after discovering that the firstattempt was measuring import errors rather than failing assertions.

Known limitations and open edges

Found during the session and left open — deliberately.

An explicit operator retry reopens a cancelled deployment.cancel stops the current execution, prevents its terminal write, and blocksautomatic recovery. Calling retry later clears cancel_requested and setsthe same row back to pending. I am documenting that as an operator overriderather than claiming cancellation is irreversible. The behavior is reproducedby probe, but no dedicated test currently pins the cancelled-to-retry path.

A repeated asset_id inside one approval certifies complete over amissing draft. Two approved assets sharing an asset_id collapse to oneexpectation, because the expected-object map is keyed by external_key. Thesummary reports objects_deployed: 1, assets_approved: 2, certification:
complete. That is a complete verdict covering fewer drafts than theapproval names — the same class of false pass this work exists to remove,reached from the payload side rather than the provider side. No fixturecontains a repeated id, which is why it survived. Not guarded at submit,and not tested. Reproduced by probe.

CH12 (SQLite WAL + busy timeout) did not land. Contention raisesdatabase is locked rather than blocking briefly. LabelledOBSERVABILITY ONLY in the roadmap and cut for time.

Connections are never closed. \_connect() returns a freshsqlite3.Connection and every caller uses it as with self.\_connect() as
connection:. That context manager scopes the transaction, not theconnection, so each one stays open until garbage collection. Correctness isunaffected — every write still commits — but make test emits 1582ResourceWarning: unclosed database lines, which buries the actual result,and under a long-lived worker pool it is file-descriptor pressure. The fix isone contextlib.closing at the call sites. Not a behavioral defect, so itwas not treated as one; it should still be cleaned up before this is read asproduction code.

make demo covers two of the three scenes in ROADMAP.md §8. Scene 1(crash, restart, recover, certify complete) and scene 2 (destination losestwo objects; status stays done, certification flips to divergent namingboth missing keys) both run. Scene 3 — submittingdeployment_request_empty.json and showing it refused at submit — wasnever added to demo.py. The behavior is tested(SubmitDoorTest.test_the_zero_asset_fixture_is_refused_by_the_same_rule);it just is not demonstrated.

Known and accepted, from ROADMAP.md §10.

Certification proves identity and presence, never content. The providerexposes no method returning a rendered body, so source_sha256 can only becompared as a provenance tag it echoed back. If HubSpot stored the rightidentifiers against the wrong body, this design certifies complete and iswrong.

Certification is point-in-time and expires the instant it is returned.

The provider can still lose writes (CH13). The relay detects and reports; it does notprevent.

unknown is honest but not actionable on its own.

failed needs a human even when a retry would have worked. No backoff, noclassification of transient vs. permanent.

Identity is only as stable as the operator's key. A client that regeneratesits key for one approval — c3's shape — deploys twice and this designconsiders both correct.

Nothing is ever cleaned up. Duplicate drafts written before this change stayin HubSpot; the provider has no delete.

G6 (c9) is addressed at a cause I reasoned about but never reproduced with anatural trigger.

The mutation pass

The mutation pass is the check on the checks. Each mutation is applied torelay/core.py alone, the test ROADMAP.md §7 says must catch it is run, andthe change is reverted with git checkout before the next. Nothing was leftapplied; the working tree was verified clean after every step.

Result: 11 mutations applied, 6 killed by their named test, 3 survived theentire suite, 2 behaved differently from the prediction.

Killed by the named test — the check does what §7 claims

#

Mutation

Test that failed

M16

Build certification from what create_draft returned, not a fresh readback

test_reported_outcome.FailedRunTest.test_a_run_that_cannot_certify_complete_ends_failed

M12

run_once sets done regardless of the certification it computed

same

M11b

Catch KeyError and JSONDecodeError in one except

test_certification.DivergenceTest.test_a_key_error_is_divergent_and_an_unreadable_state_is_unknown

M19

Delete the reconcile wrapper; call create_draft directly

test_recovery_and_reconcile.AmbiguousWriteTest.test_a_write_that_persisted_before_raising_is_reconciled_as_success

M17

Drop the reverse namespace scan

test_certification.DivergenceTest.test_a_stranger_in_the_namespace_certifies_divergent

M9

Certification reads receipt_json["objects"] instead of the provider

test_certification.FreshnessTest.test_a_stored_receipt_does_not_survive_a_change_in_hubspot

Certification and reconciliation are pinned. Those six are the guarantees theoperator report is actually about, and each one has a check that dies when themechanism dies.

Survived — the named check does not isolate the mechanism

All three survived their named test and the whole 62-test suite. This is thefinding the pass exists to produce, and it would not have shown up any other way.

#

Mutation

§7 said this would fail

What actually holds the behavior up

M15

Claim ignores cursor.rowcount and proceeds regardless

test_only_one_of_many_workers_executes_a_pending_deployment

Ownership is enforced at more than one layer. With the rowcount check, one owner check, and \_finish's conditions all removed, 20 racing workers still produce exactly 1 completion and 19 RunNotOwned. The suite cannot isolate the claim from the checks downstream of it

M14b

Terminal update made unconditional again

test_a_cancelled_run_cannot_be_marked_done

The \_require_still_ours call inside the write loop. The parked worker meets the cancellation on its next iteration and never reaches the terminal update at all. Remove both and 5 tests fail, including this one — so the loop check is doing the work and the terminal guard is redundant for every scenario the suite exercises

M18b

Widen recover's selection to status IN ('running', 'failed')

test_recovery_never_reclaims_a_run_that_exhausted_its_attempts

\_claim's status IN ('pending', 'running') filter, which refuses the failed run that recover now hands it. Widen that too and a different test catches it — test_reported_outcome.FailedRunTest.test_recovery_does_not_reclaim_a_failed_run

What this does and does not mean. It is not that the code is wrong — thelayering is deliberate and each of these is a second line of defense that holdswhen the first is removed. It is that three checks are credited in ROADMAP.md§7 with pinning mechanisms they do not pin. If someone deleted the rowcountcheck tomorrow, the suite would stay green and the roadmap would still claim itwas covered. That is the same species of false confidence as the starter'sverified: true, one level up, and it is worth writing down rather thanquietly re-labelling the rows.

The honest repair is one of two things, and there was not time for either:narrow each test so it exercises its mechanism with the other layers heldconstant, or amend §7 to name the check that really covers each mutation. TheM18b row is the easy case — the covering test already exists and is named above.

One caveat on M15. The layer that survives was not isolated. The edit thatdisabled "the owner check" may have hit the message-building branch in \_claimrather than the one in \_require_still_ours, so the correct reading is "morethan one layer enforces ownership and the suite cannot tell them apart", not aclaim about which specific line. Identifying it exactly is follow-up work.

Expected to survive — confirmed gaps

#

Mutation

Outcome

M24

audit returns "all_present": True unconditionally

survived the whole suite. all_present and checked_objects are asserted by no test. This is the starter's false-pass field, still uncovered

M27

retry resets any status to pending

survived. Probe: cancel → retry → run_once ends done with 4 drafts written. Open item 1

M28

Repeated asset_id accepted at submit (no guard exists to remove)

survived. Probe: objects_deployed: 1, assets_approved: 2, certification: complete. Open item 2

M21

deployment_summary returns "objects_deployed": 0 always

killed — prediction was wrong. test_visible.ReportedDeploymentFailureTest.test_every_deployed_object_matches_its_source_asset asserts objects_deployed == assets_approved. The weak assertLessEqual in test_reported_outcome is still weak, but a constant zero does not get through

M25

Mark every run done regardless of certification, in a stress run

not run separately — M12 is the same break and was killed by the suite. The point stands that stress.py itself asserts nothing

A note on how the pass was run

The first attempt reported all nine must-fail mutations as killed witherrors=1. They were not killed — errors=1 was ModuleNotFoundError, becausea dotted test target does not put tests/ on sys.path the way discovery does.Every mutation was "caught" by a suite that never imported. The pass was re-runwith PYTHONPATH=.:tests and the results above are from that run, whichdistinguishes FAIL: from ERROR: and records the failing assertion. Amutation pass that cannot tell a real failure from an import error measuresnothing, which is the same lesson as the rest of this exercise.

The next failure I would inject

Given more time, in this order:

A provider that succeeds and then loses the object between the write andthe certification readback — the c8 mechanism at its exact window. M16approximates it; a wrapper that erases on the read rather than on the writewould pin the ordering directly.

Two workers whose leases overlap by a fraction — expire the owner's leasemid-payload, let a second worker reclaim and finish, then release the firstand confirm its terminal write is refused. test_a_working_run_renews_its_lease_and_cannot_be_stolencovers the renewal; nothing covers the losing side of the handover.

submit racing cancel — nothing exercises a cancellation that landsbetween the row insert and the first claim.
