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


❯ Please read through the codebase and confirm: - what the test cases are - the 
  problem statement - what the results are for the JSON files in the fixtures   
  folder - what the ways are that we test this entire project Read through the  
  TASK.md file and the README.md files. Also, for context on what we're         
  building and what we are specifically fixing for our operators' sake:         
  whenever character quilt is to create deliverables or products or campaigns   
  for the client, it would not hallucinate the fact that there is an            
  insufficient number of campaigns or deliverables provided on HubSpot.         
  Currently, we're using a fake version of HubSpot, so there are no network     
  calls between this project and the internet. That does not mean that we are   
  building a framework that is not suitable for real production environments.   
                                                                                
                                                                                
                                                                                
                                                                                
                                                                                

⏺ I'll read the codebase first.

  Searched for 1 pattern, read 15 files, ran 8 shell commands (ctrl+o to expand)

⏺ Read everything. Ran demo, test, stress, plus my own probes. Findings below,
  then one scope question.

  Test cases (tests/test_visible.py) — 2, both pass, both weak

  Test: test_reported_deployment_recovers_without_duplicate_drafts
  Asserts: after crash + recover(): status done, receipt non-null, provider
  object count == 4
  Hole: Single-threaded, single run, one crash point. Never checks receipt
  matches provider
  ────────────────────────────────────────
  Test: test_every_deployed_object_matches_its_source_asset
  Asserts: for each receipt object, source_sha256 matches its asset
  Hole: Compares the sha the relay sent to the sha it echoed back —
  self-consistent by construction. Never compares display_name, which is the
  field provider actually mutates

  Problem statement

  TASK.md: decide what the deployment service promises an operator, write it
  down, make smallest set of changes backing it. fixtures/operator_report.txt
  lists 4 symptoms: more drafts than approved, fewer drafts than approved with
  receipt still verified, cancelled campaign kept going, one stuck run took the
  whole queue down. Event log labels 11 cases c1…c11, unlabelled — grouping them
  is the work. At least one is noise.

  My grouping, all reproduced:

  Cause: No idempotency on submit — key stored, never enforced; external_key =
  run_id:asset_id so every new run_id makes fresh drafts
  Cases: c2, c3, c4
  Evidence: probe: same key twice → 8 objects. retry() → 8 objects
  ────────────────────────────────────────
  Cause: No claim/lease — nothing stops 2 workers on one run; provider
  _load/_save is unlocked read-modify-write on a JSON file, so concurrent
  writes are lost
  Cases: c8
  Evidence: make stress: 48 created, provider holds 36–44, varies per run. grep 
  lease|claim → nothing
  ────────────────────────────────────────
  Cause: Receipt is unfalsifiable — verified: True is a hardcoded literal
  (core.py:209); audit() re-reads the receipt, never the provider
  Cases: c1, c7, c8
  Evidence: make demo deletes 2 objects from provider state, audit() still
  returns all_present: true, verified: true
  ────────────────────────────────────────
  Cause: run_once ignores cancellation — no check, and final UPDATE ... 
  status='done' is unconditional. RunCancelled is defined and never raised
  Cases: c6
  Evidence: probe: cancel → run_once → status back to done, 4 drafts created
  ────────────────────────────────────────
  Cause: recover() head-of-line block — bare loop, core.py:265-266, one raising
  run kills the pass
  Cases: c9
  Evidence: probe: poison payload + 2 good runs → KeyError 'source_sha256', both

  later runs stay running, 0 objects deployed. Matches c9 note verbatim
  ────────────────────────────────────────
  Cause: Provider mutates what it stores — display_name stripped and truncated
  to
  40; relay records the mutated value, never compares
  Cases: c7
  Evidence: see fixture table below
  ────────────────────────────────────────
  Cause: Noise
  Cases: c10, c11
  Evidence: c10 = timeout then readback found: provider is idempotent by
  external_key, so this is the correct shape. c11 thumbnail_render — no such
  tool exists anywhere in repo. This is the unrelated one

  Fixture JSON results

  Ran all three through submit → run_once → deployment_summary → audit:

  Fixture: deployment_request.json
  Assets: 4
  Summary: objects_deployed: 4, verified: true
  Audit: all_present: true, verified: true
  Real outcome: 3 of 4 display names silently truncated. asset-email-002 and
  asset-email-003 both land as the identical string "Summer 2026  ABM campaign 
  email - product" — two distinct approved assets, indistinguishable in
  HubSpot. asset-email-003 also has a trailing space, stripped
  ────────────────────────────────────────
  Fixture: deployment_request_short.json
  Assets: 4
  Summary: objects_deployed: 4, verified: true
  Audit: all_present: true
  Real outcome: Clean. All names under 40 chars. A rule that passes only against
   
  this fixture proves nothing — this is the "different shapes on purpose" trap
  ────────────────────────────────────────
  Fixture: deployment_request_empty.json
  Assets: 0
  Summary: objects_deployed: 0, verified: true
  Audit: checked_objects: 0, all_present: true, verified: true
  Real outcome: This is your operator bug. all_present is all([]) — vacuously
  true. Zero deliverables reported as a verified success

  That last row is exactly the hallucination you described: the service tells
  the operator a campaign deployed and verified when HubSpot holds nothing. Same
  defect at partial scale in make demo — 2 objects deleted, still verified: 
  true.

  How the project is tested today

  Three make targets, all green on the starter, none of which prove anything:

  - make test — 2 unittest cases above. Green.
  - make demo — crash after first provider write, restart, recover(), then
  deletes 2 objects from provider state and re-checks. Green output, wrong
  answer.
  - make stress — 12 threads, 12 runs, 4 assets each, threading.Barrier so they
  fire simultaneously. This is the only honest check in the repo. Reports
  objects the provider holds: 39 against objects the runs created: 48, 3
  KeyErrors from provider.read() on keys another thread's _save clobbered, and 3
  runs left running. Then the recovery loop reports attempt 1..3: returned and
  every run flips to done — the queue reports total success over a provider
  missing a quarter of its objects.

  No CI, no property tests, no invariant checks, not a git repo yet (git log →
  fatal: not a git repository).

  ---
  Two things gate the plan:

⏺ User declined to answer questions
  ⎿  · TASK.md requires ROADMAP.md committed on its own before any source or 
     test edit, and this directory is not a git repo yet. How do you want to 
     handle that? (git init, then roadmap commit (Recommended) / Roadmap only, 
     you handle git / Skip roadmap, go straight to code)
     · How much of the fault list should the fix cover? TASK.md rewards choosing
     what NOT to fix and saying why. (Truth of the receipt only / Receipt truth
     + concurrency + queue (Recommended) / Everything including provider 
     serialization)

⏺ What would you like to clarify?

  Findings above stand — grouping, fixture results, and the three make targets
  are all reproduced from probes, not inference. Tell me what part you want to
  push on: the case grouping (whether c10/c11 are really noise), the fixture
  read, the scope framing, the git/roadmap sequencing, or something else
  entirely.

✻ Cogitated for 3m 23s

❯ do not commit or do anything on github. but can you cerify those test cases   
  again because they don't seem true. just verify if you're understanding this  
  properly. and spit back to me in simple words what you understand and you     
  need me to clarify                                                            

⏺ Fair. Let me re-verify rather than restate.

  Ran 3 shell commands (ctrl+o to expand)

⏺ Re-verified. Two of my earlier statements need correcting.

  Verified true

  Test 2 is tautological. I fed it a fake sha, "THIS-IS-NOT-A-REAL-SHA-AT-ALL".
  Provider copied it back verbatim (core.py:68 — "source_sha256": 
  str(asset["source_sha256"])), assert passed. The test compares a value to
  itself. Nothing can make it fail.

  Test 1 checks the count, not the content. After crash + recover: status done,
  receipt present, 4 objects in provider — all three asserts pass. Meanwhile 3
  of the 4 display names do not match what was approved, and 4 approved assets
  collapsed to 3 distinct names in HubSpot. Test looks at none of that.

  Empty fixture reports a verified success with zero deliverables.
  objects_deployed: 0, verified: true, all_present: true. all_present is
  all([]).

  Corrections

  I said test 1 "never checks receipt matches provider." Wrong — it does check
  the count matches. It doesn't check content. Different claim.

  I said c9 (queue head-of-line) was reproduced. Overstated. The mechanism is
  real and plain in the code — recover() at core.py:261-266 is a bare loop with
  no try/except, so any raise ends the pass. But to make it raise I invented a
  payload missing source_sha256. Trying it with a trigger the system generates
  on its own, both recover passes finished the work and nothing stayed stuck.
  So: cause is visible in the code, my demo of it was rigged. Treat it as
  unproven until I find a real trigger.

  That attempt did surface something new though: concurrent recover threw
  JSONDecodeError: Expecting value: line 1 column 1. One thread read the
  provider's state file while another was mid-write_text and got an empty file.
  Torn read, not just lost write.

  In simple words

  The service writes drafts to HubSpot, then writes a receipt saying it worked.
  Nothing ever compares the receipt to HubSpot. verified: True is a hardcoded
  literal on core.py:209. The "Check again" button re-reads the receipt, not the
  provider. So the receipt can say 4 verified drafts while HubSpot holds 0, 3,
  or 4 wrong ones, and every check still says fine.

  make stress is the honest one: 12 workers create 48 objects, provider ends up
  holding 36–44, count changes every run, and the recovery pass still flips all
  12 runs to done.

  Your operator problem is that last mile: the empty request and the
  partial-loss case both come back as confident successes. That's the same
  defect, not two.

⏺ User declined to answer questions
  ⎿  · You said the test cases didn't seem true. I re-verified and corrected two
     claims above. Does that match what you were pushing on, or is something 
     else still wrong? (That was it / Still wrong somewhere else / Re-verify all
     11 cases the same way)
     · For the hallucination fix you described — not claiming success when 
     HubSpot holds too few objects — what should the service do when the 
     approved request has zero assets? (Reject at submit / Run it, mark it 
     not-verified / You tell me)

⏺ What would you like to clarify?

  To be concrete about where I stand: the verified findings are the receipt
  never being compared to the provider (verified: True hardcoded, core.py:209),
  test 2 comparing a value to itself, test 1 checking count but not content, and
  the empty request returning verified: true on zero objects. The one I walked
  back is c9 — cause visible in code, my demo of it rigged.

  No commits, no GitHub, nothing written. Ask away.

✻ Cogitated for 1m 43s

❯ read through files such as @relay/core.py to understand how the hubspot fake  
  servers are created (in class FakeHubSpot). note that we are only using a     
  FakeHubSpot and not even the internet. so no api calls are allowed here.      
  Additionally, please note how the file discusses deployment. please always    
  verify your work and explain to me in simplistic terms how you have done      
  this. we need to read thrugh the deployment and i'd like for you to           
  understand currently how things are deployed to hubspot. it also looks like   
  most of are issues are in @core.py. however, first read through all the files 
  then spit it back to me what you learned. then we will make the roadmap if    
  all is satisfactory                                                           
  ⎿  Read relay/core.py (267 lines)

⏺ Read all files. Now let me verify the FakeHubSpot mechanics by probing rather
  than by reading.

  Ran 5 shell commands (ctrl+o to expand)

⏺ All verified by running it, not by reading it. Here is what I learned, in
  plain terms.

  1. What FakeHubSpot actually is

  A single JSON file on disk. That's the whole thing.

  Grepped for requests, socket, urllib, http, httpx, and any http:// string
  across relay/, tests/, demo.py, stress.py. Zero hits. No network is possible
  here, not just unused.

  FakeHubSpot.__init__ (core.py:41-44) takes a path and writes {} if the file
  doesn't exist. Every operation is: read the whole file, change a dict in
  memory, write the whole file back.

  It has exactly three things the relay can call:

  ┌───────────────────────────────────┬───────────────────────────┐
  │              Method               │       What it does        │
  ├───────────────────────────────────┼───────────────────────────┤
  │ create_draft(external_key, asset) │ Add one draft under a key │
  ├───────────────────────────────────┼───────────────────────────┤
  │ read(external_key)                │ Get one draft back        │
  ├───────────────────────────────────┼───────────────────────────┤
  │ list_objects()                    │ Get all drafts            │
  └───────────────────────────────────┴───────────────────────────┘

  There is no delete and no update. From the relay's side, HubSpot only grows.
  So when the operator says "once there were fewer drafts," the relay did not
  remove them — they were lost. That narrows the search a lot.

  2. What HubSpot does to your data (it is not a passive box)

  The class docstring says it "owns the objects it stores… applies its own
  rules… returns what it decided to keep." I tested each rule:

  It dedupes by external_key. Same key + same asset twice → one object, returns
  the existing one. Retries are safe.

  It rejects contradictions. Same key + different asset → raises
  IdempotencyConflict. You cannot quietly overwrite a draft.

  It does not know two keys are the same campaign. Different key + same asset →
  two drafts, no complaint. This matters, because the relay builds the key as
  run_id:asset_id (core.py:194), and run_id is a fresh UUID every time. So a new
  run means new keys means duplicate drafts. HubSpot is behaving correctly; the
  relay is asking it the wrong question.

  It silently edits your display names. _store_display_name (core.py:52-53)
  strips whitespace and cuts to 40 characters. I sent a 47-character name, got
  40 back. The relay stores the cut version in the receipt and never compares it
  to what was approved.

  Its object_id is not really an ID. hs- + first 12 chars of the SHA of the
  external key (core.py:64). I recomputed it by hand and got the same value.
  It's a restatement of the key, so object_id existing tells you nothing about
  whether the object is in HubSpot.

  3. How deployment works today

  Three steps, and only one of them touches HubSpot:

  submit()    -> writes a row to SQLite, status 'pending'.  Nothing deployed.
  run_once()  -> for each asset: create_draft(), then read() it back.
                 then writes receipt, status 'done'.
  recover()   -> finds every row still 'running' and calls run_once() again.

  Traced live. After submit, SQLite says pending and the HubSpot file is still
  {}. After run_once, 4 objects exist and status is done.

  The bug is in the last line of run_once. It builds the receipt like this:

  receipt = {..., "objects": readbacks, "verified": True}   # core.py:205-210

  verified: True is typed into the source. It is not the result of a check.
  Nothing compares readbacks against the approved payload — not the count, not
  the names, nothing.

  Then the two operator-facing functions make it worse. deployment_summary
  (core.py:222) and audit (core.py:239) both read the receipt out of SQLite.
  Neither one opens HubSpot. audit is labelled "the dashboard's Check again
  button" and it checks nothing.

  I proved it: deployed 4 objects, then deleted 2 straight out of HubSpot's
  file, changed nothing else.

  HubSpot actually holds: 2
  deployment_summary: {"objects_deployed": 4, "verified": true,
  "assets_approved": 4}
  audit:              {"checked_objects": 4, "all_present": true, "verified":
  true}

  Two objects gone. Every screen says fine.

  4. Why objects go missing in the first place

  create_draft does read → edit → write with no lock (core.py:61, 78, 79). Two
  workers both read the file before either writes, so the second write erases
  the first.

  50 concurrent creates, three trials:

  trial 1: 43 returned success | 7 raised JSONDecodeError | provider holds 4  |
  silently lost 39
  trial 2: 34 returned success | 16 raised JSONDecodeError | provider holds 6  |
  silently lost 28
  trial 3: 44 returned success | 6 raised JSONDecodeError | provider holds 2  |
  silently lost 42

  Two separate failures there. Writes get erased, and readers that catch the
  file mid-write get a JSONDecodeError — a torn read. make stress shows the
  milder version: 48 created, 36–44 held.

  Honest caveat: real HubSpot does not lose writes like this. This race is an
  artifact of the fake being a JSON file. So it may be the wrong thing to fix.
  But it doesn't matter for the promise — whether HubSpot loses an object, drops
  one, or was never sent one, the relay must not claim success without looking.
  That defect is ours either way.

  5. The one-sentence version

  The service reports what it intended to do, never what HubSpot actually holds.
  verified: True is a constant, and the "Check again" button re-reads the same
  stale receipt instead of HubSpot.

  That is exactly your operator problem. The empty request
  (deployment_request_empty.json → objects_deployed: 0, verified: true) and the
  partial-loss case are the same defect at two sizes, not two bugs.

  Before I write the roadmap:
⏺ User answered Claude's questions:
  ⎿  · The provider's lost-write race is an artifact of the fake being a JSON 
     file — real HubSpot wouldn't do this. TASK.md says the provider is not ours
     to change. How should the roadmap treat it? → Leave it, make the relay 
     honest (Recommended)
     · The relay builds HubSpot keys as `run_id:asset_id`, and run_id is a new 
     UUID every run — that's why retries and duplicate submits create duplicate 
     drafts. What's the right key? → Key on the operator's approval, not the run
     · How deep should the roadmap go? TASK.md says choosing what NOT to fix 
     scores as well as a repair. → You decide after seeing a draft

⏺ Answers noted. Let me verify the key design actually works before I write it
  down.

  Ran 2 shell commands (ctrl+o to expand)

⏺ Both mechanisms verified against real SQLite. Writing the plan.

⏺ Updated plan
  ⎿  /plan to preview

⏺ Plan written. Three things in it you should push back on if you disagree:

  The main fixture will start reporting verified: false. Once verification
  actually compares readbacks to the approved request, deployment_request.json
  fails — three display names get truncated by HubSpot, and two of them collapse
  to the identical string. That is the correct answer (c7 says exactly this),
  but it means make demo stops looking clean. That's the point, though it's the
  most arguable call in the plan.

  Zero-asset requests get rejected at submit. Directly kills your hallucination
  case. It changes the API contract — a caller that used to get a run_id now
  gets an error.

  c9 is flagged as unproven. Cause is plain in the code, my repro was rigged.
  The roadmap says so rather than claiming a clean reproduction.

  No git operations anywhere in the plan, per your instruction. Noting once that
  TASK.md wants ROADMAP.md committed alone before source edits — your call to
  handle separately.
  ⎿  User rejected Claude's plan:
     ╭─────────────────────────────────────────────────────────────────────────╮
     │ Make the deployment receipt tell the truth                              │
     │                                                                         │
     │ Context                                                                 │
     │                                                                         │
     │ Operators do not trust this service. fixtures/operator_report.txt says  │
     │ what                                                                    │
     │ CharacterQuilt reports afterwards does not match what they find in      │
     │ HubSpot:                                                                │
     │ sometimes more drafts than approved, once fewer with the receipt still  │
     │ saying                                                                  │
     │ verified, a cancelled campaign that kept going, and a stuck run that    │
     │ took two                                                                │
     │ other campaigns down with it.                                           │
     │                                                                         │
     │ The root cause, verified by probe rather than by reading:               │
     │                                                                         │
     │ ▎ The service reports what it intended to do, never what HubSpot        │
     │ ▎ actually                                                              │
     │ ▎ holds.                                                                │
     │                                                                         │
     │ run_once writes "verified": True as a hardcoded literal                 │
     │ (relay/core.py:209).                                                    │
     │ Nothing compares the readbacks to the approved payload.                 │
     │ deployment_summary                                                      │
     │ (core.py:222) and audit (core.py:239) both re-read that receipt out of  │
     │ SQLite; neither opens the provider. audit is labelled "the dashboard's  │
     │ Check                                                                   │
     │ again button" and checks nothing.                                       │
     │                                                                         │
     │ Proof run during exploration — deployed 4 objects, deleted 2 straight   │
     │ out of the                                                              │
     │ provider's state file, changed nothing else:                            │
     │                                                                         │
     │ HubSpot actually holds: 2                                               │
     │ deployment_summary: {"objects_deployed": 4, "verified": true,           │
     │ "assets_approved": 4}                                                   │
     │ audit:              {"checked_objects": 4, "all_present": true,         │
     │ "verified": true}                                                       │
     │                                                                         │
     │ Same defect at zero scale: fixtures/deployment_request_empty.json has 0 │
     │ assets                                                                  │
     │ and returns objects_deployed: 0, verified: true, all_present: true      │
     │ (all([]) is vacuously true). This is the operator-facing bug we care    │
     │ about                                                                   │
     │ most: the service claims a verified campaign deployment when the        │
     │ destination                                                             │
     │ holds nothing.                                                          │
     │                                                                         │
     │ Intended outcome: a written promise the service can actually back, and  │
     │ the                                                                     │
     │ smallest set of changes that backs it.                                  │
     │                                                                         │
     │ ---                                                                     │
     │ The promise (goes at the top of ROADMAP.md)                             │
     │                                                                         │
     │ ▎ A deployment is verified only when every approved asset has been read │
     │ ▎ back                                                                  │
     │ ▎ from HubSpot after the write and matches the approved request field   │
     │ ▎ for field.                                                            │
     │ ▎ Anything else is reported as not verified, with the specific          │
     │ ▎ discrepancy named.                                                    │
     │ ▎ The service never reports success it has not re-read.                 │
     │                                                                         │
     │ Corollaries worth stating because they are what operators actually      │
     │ asked for:                                                              │
     │                                                                         │
     │ - One approved request deploys once, no matter how many times it is     │
     │ submitted,                                                              │
     │ retried, or recovered.                                                  │
     │ - A cancelled deployment stops and stays cancelled.                     │
     │ - One failing deployment never blocks the deployments behind it.        │
     │                                                                         │
     │ Cause map (evidence, not inference)                                     │
     │                                                                         │
     │ Grouped from fixtures/deployment_events.jsonl. Every row below was      │
     │ reproduced                                                              │
     │ by running code, except where marked.                                   │
     │                                                                         │
     │ Cause: Receipt is unfalsifiable                                         │
     │ Cases: c1, c7, c8                                                       │
     │ Where: core.py:209, :239                                                │
     │ Evidence: delete-2-objects probe above                                  │
     │ ────────────────────────────────────────                                │
     │ Cause: Provider key includes run_id, which is a fresh UUID every run    │
     │ Cases: c2, c3, c4                                                       │
     │ Where: core.py:194                                                      │
     │ Evidence: same key submitted twice → 8 objects; retry() → 8 objects     │
     │ ────────────────────────────────────────                                │
     │ Cause: submit stores idempotency_key and never enforces it              │
     │ Cases: c2, c5                                                           │
     │ Where: core.py:115-129                                                  │
     │ Evidence: two rows, two runs, one approval                              │
     │ ────────────────────────────────────────                                │
     │ Cause: No claim/lease — nothing owns a run                              │
     │ Cases: c8                                                               │
     │ Where: no lease/claim anywhere                                          │
     │ Evidence: make stress: 48 created, provider holds 36–44, varies per run │
     │ ────────────────────────────────────────                                │
     │ Cause: run_once never checks cancellation; final UPDATE is              │
     │ unconditional                                                           │
     │ Cases: c6                                                               │
     │ Where: core.py:186-219                                                  │
     │ Evidence: cancel → run_once → status back to done, 4 drafts created     │
     │ ────────────────────────────────────────                                │
     │ Cause: recover() is a bare loop with no per-run error isolation         │
     │ Cases: c9                                                               │
     │ Where: core.py:261-266                                                  │
     │ Evidence: cause visible in code; my repro used an invented poison       │
     │ payload.                                                                │
     │ Unproven with a natural trigger — say so in the roadmap                 │
     │ ────────────────────────────────────────                                │
     │ Cause: Provider truncates display_name to 40 chars, relay records the   │
     │ truncated                                                               │
     │ value                                                                   │
     │ Cases: c7                                                               │
     │ Where: core.py:52-53                                                    │
     │ Evidence: 4 approved names → 3 distinct names in HubSpot                │
     │ ────────────────────────────────────────                                │
     │ Cause: Noise                                                            │
     │ Cases: c10, c11                                                         │
     │ Where: —                                                                │
     │ Evidence: c10 is timeout-then-readback-found, which is the correct      │
     │ shape for an                                                            │
     │ idempotent provider. c11 thumbnail_render — no such tool exists in the  │
     │ repo.                                                                   │
     │ This is TASK.md's "at least one is unrelated"                           │
     │                                                                         │
     │ Decisions already made                                                  │
     │                                                                         │
     │ 1. Do not touch FakeHubSpot. Its unlocked read-modify-write             │
     │ (core.py:61/78/79) loses writes and produces torn reads — 50 concurrent │
     │ creates left 2–6 objects and threw JSONDecodeError. Real HubSpot does   │
     │ not                                                                     │
     │ behave this way, so this is an artifact of the fake, and TASK.md says   │
     │ the                                                                     │
     │ provider is not ours to change. It does not matter for the promise:     │
     │ whether an                                                              │
     │ object was lost, dropped, or never sent, the relay must not claim       │
     │ success                                                                 │
     │ without looking.                                                        │
     │ 2. Key HubSpot objects on the approval, not the run.                    │
     │                                                                         │
     │ Verified design facts                                                   │
     │                                                                         │
     │ Checked before committing to the design:                                │
     │                                                                         │
     │ - deployment_request.json and deployment_request_short.json share every │
     │ asset_id and every source_sha256. Only display_name differs. So a       │
     │ provider key of asset_id alone would merge two different campaigns.     │
     │ payload_hash is required in the key.                                    │
     │ - All three fixture payload hashes are distinct.                        │
     │ - A conditional UPDATE ... WHERE status='pending' checked via rowcount  │
     │ is an                                                                   │
     │ atomic claim: 20 racing threads, exactly 1 winner.                      │
     │ - A UNIQUE index on idempotency_key raises IntegrityError on the second │
     │ submit — c2 and c5 both stop at the door.                               │
     │                                                                         │
     │ ---                                                                     │
     │ Changes                                                                 │
     │                                                                         │
     │ All in relay/core.py unless noted. No git operations — user's call.     │
     │                                                                         │
     │ 1. ROADMAP.md — write first, before any source edit                     │
     │                                                                         │
     │ The promise, the cause map, what is explicitly not being fixed and why  │
     │ (provider race, display_name truncation policy, c11), and for each      │
     │ promise the                                                             │
     │ check that fails first. TASK.md wants claims another engineer could     │
     │ disagree                                                                │
     │ with — the c11-is-noise call and the leave-the-provider-alone call are  │
     │ those.                                                                  │
     │                                                                         │
     │ 2. Schema — _init_db                                                    │
     │                                                                         │
     │ Add UNIQUE on idempotency_key. Add owner, lease_expires_at, attempt.    │
     │ Add cancel_requested. Keep CREATE TABLE IF NOT EXISTS working on a      │
     │ fresh DB;                                                               │
     │ no migration needed since every entry point builds its own DB.          │
     │                                                                         │
     │ 3. submit — one approval, one run                                       │
     │                                                                         │
     │ - Same key + same payload_hash → return the existing run_id.            │
     │ Idempotent.                                                             │
     │ - Same key + different payload_hash → raise IdempotencyConflict         │
     │ (already                                                                │
     │ defined, core.py:15). This is c5.                                       │
     │ - Reject a payload with zero assets. An approved request with no        │
     │ deliverables is                                                         │
     │ not deployable, and letting it through is what produces the             │
     │ "verified deployment of nothing" the operators are complaining about.   │
     │                                                                         │
     │ 4. Provider key — run_once                                              │
     │                                                                         │
     │ Replace f"{run_id}:{asset['asset_id']}" (core.py:194) with a key        │
     │ derived from                                                            │
     │ idempotency_key + payload_hash + asset_id. Same approved request lands  │
     │ on                                                                      │
     │ the same HubSpot objects however many times it runs, so the provider's  │
     │ own                                                                     │
     │ dedupe absorbs retries, recovery, and double-submits. Removes the cause │
     │ of                                                                      │
     │ c2/c3/c4 rather than suppressing it.                                    │
     │                                                                         │
     │ 5. Claim before running — run_once                                      │
     │                                                                         │
     │ Atomic conditional UPDATE with a lease. Non-winners return without      │
     │ touching the                                                            │
     │ provider. Recovery only reclaims runs whose lease has expired. This is  │
     │ c8.                                                                     │
     │                                                                         │
     │ 6. Honest verification — run_once                                       │
     │                                                                         │
     │ After the writes, re-read every expected key from the provider and      │
     │ compare to the                                                          │
     │ approved asset: source_asset_id, source_sha256, object_type,            │
     │ display_name.                                                           │
     │ verified becomes the result of that comparison, never a literal. The    │
     │ receipt carries a discrepancies list naming each mismatch.              │
     │                                                                         │
     │ Expected and intended consequence: deployment_request.json will come    │
     │ back                                                                    │
     │ verified: false with three display_name truncations named. That is the  │
     │ correct answer — c7 says "receipt said verified; the drafts were not    │
     │ what we                                                                 │
     │ approved" — and two of those assets collapse to the identical name in   │
     │ HubSpot.                                                                │
     │ deployment_request_short.json stays verified: true.                     │
     │                                                                         │
     │ 7. Cancellation is real — cancel / run_once                             │
     │                                                                         │
     │ cancel sets cancel_requested. run_once checks it before each provider   │
     │ write                                                                   │
     │ and raises RunCancelled (defined and unused, core.py:19). The final     │
     │ status='done' UPDATE becomes conditional so a finishing worker cannot   │
     │ overwrite                                                               │
     │ a cancel. This is c6.                                                   │
     │                                                                         │
     │ 8. audit re-reads HubSpot                                               │
     │                                                                         │
     │ Currently it re-reads the receipt. Make it read the provider for every  │
     │ expected                                                                │
     │ key and report what is actually there now versus what the receipt       │
     │ claims. This is                                                         │
     │ the function the operator presses when they already suspect a lie.      │
     │                                                                         │
     │ 9. recover isolates failures                                            │
     │                                                                         │
     │ Per-run try/except so one bad run cannot end the pass, plus a bounded   │
     │ attempt count so a permanently failing run stops re-running forever and │
     │ is                                                                      │
     │ surfaced instead. This is c9 — and the roadmap must say this fixes a    │
     │ code-visible cause whose natural trigger I did not reproduce.           │
     │                                                                         │
     │ 10. retry retries, not clones                                           │
     │                                                                         │
     │ Currently inserts a new row with the same idempotency key               │
     │ (core.py:154-177),                                                      │
     │ which the new UNIQUE constraint forbids. Rework to reset the existing   │
     │ run to                                                                  │
     │ pending and clear its lease. With the approval-derived provider key     │
     │ this is                                                                 │
     │ safe by construction.                                                   │
     │                                                                         │
     │ ---                                                                     │
     │ Tests — tests/test_visible.py                                           │
     │                                                                         │
     │ The two existing tests both pass and neither proves anything. Verified: │
     │                                                                         │
     │ - test_every_deployed_object_matches_its_source_asset is tautological.  │
     │ Fed it                                                                  │
     │ source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"; provider echoed it back  │
     │ verbatim (core.py:67) and the assert passed. It compares a value to     │
     │ itself.                                                                 │
     │ - test_reported_deployment_recovers_without_duplicate_drafts checks the │
     │ object                                                                  │
     │ count, never the content. In its own scenario, 3 of 4 display names do  │
     │ not                                                                     │
     │ match what was approved and it passes anyway.                           │
     │                                                                         │
     │ Keep both (they must not regress), and add one test per promise — each  │
     │ written so                                                              │
     │ it fails against the current code:                                      │
     │                                                                         │
     │ ┌─────────────────────────────────────────┬──────────────────────────── │
     │ ───┐                                                                    │
     │ │                  Test                   │      Fails today because    │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ deleting objects from the provider      │ audit never opens the       │
     │   │                                                                     │
     │ │ makes audit report unverified           │ provider                    │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ a request with 0 assets is rejected at  │ it deploys and reports      │
     │   │                                                                     │
     │ │ submit                                  │ verified: true              │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ truncated display_name is reported as a │ receipt records the         │
     │ truncated │                                                             │
     │ │  discrepancy                            │  value and says verified    │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ same key twice returns the same run and │ two runs, 8 objects         │
     │   │                                                                     │
     │ │  one set of drafts                      │                             │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ same key + different payload raises     │ both deploy                 │
     │   │                                                                     │
     │ │ IdempotencyConflict                     │                             │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ retry produces no additional drafts     │ 8 objects                   │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ a cancelled run stays cancelled and     │ status flips back to done   │
     │   │                                                                     │
     │ │ writes nothing further                  │                             │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ a failing run in recover does not block │ bare loop aborts the pass   │
     │   │                                                                     │
     │ │  the runs behind it                     │                             │
     │   │                                                                     │
     │ ├─────────────────────────────────────────┼──────────────────────────── │
     │ ───┤                                                                    │
     │ │ N threads on one run → exactly one      │ no claim exists             │
     │   │                                                                     │
     │ │ claims it                               │                             │
     │   │                                                                     │
     │ └─────────────────────────────────────────┴──────────────────────────── │
     │ ───┘                                                                    │
     │                                                                         │
     │ Verification                                                            │
     │                                                                         │
     │ Keep make demo, make test, make stress all working — TASK.md requires   │
     │ it.                                                                     │
     │                                                                         │
     │ make test    # all new tests green; the two starter tests still green   │
     │ make demo    # must now show the discrepancy instead of hiding it       │
     │ make stress  # run repeatedly, counts vary per run                      │
     │                                                                         │
     │ make stress is the real check. Today it prints objects the provider     │
     │ holds:                                                                  │
     │ 36–44 against 48 created, then flips all 12 runs to done. After the     │
     │ change the                                                              │
     │ provider count is expected to still be short — we are not fixing the    │
     │ fake's race                                                             │
     │ — but no run may report verified: true while its objects are missing,   │
     │ and                                                                     │
     │ the recovery pass must not report success over a short provider.        │
     │                                                                         │
     │ make demo needs rewriting: its current script deletes 2 objects and     │
     │ shows both                                                              │
     │ screens still saying verified. Keep exactly that scenario and let the   │
     │ new code                                                                │
     │ report it honestly. That contrast is the demonstration.                 │
     │                                                                         │
     │ Also re-run the exploration probes as regression checks: the            │
     │ delete-2-objects                                                        │
     │ probe, the wrong-sha probe, the 20-thread claim race, and each fixture  │
     │ through                                                                 │
     │ submit → run_once → deployment_summary → audit.                         │
     │                                                                         │
     │ What this still cannot promise                                          │
     │                                                                         │
     │ For DECISIONS.md:                                                       │
     │                                                                         │
     │ - The provider can still lose writes; we detect it, we do not prevent   │
     │ it.                                                                     │
     │ - Between the provider write and the local receipt there is still a     │
     │ window; we                                                              │
     │ close it with re-read on recovery, not by making the two atomic.        │
     │ - Verification is point-in-time. Something can change in HubSpot a      │
     │ second after                                                            │
     │ audit returns.                                                          │
     │ - A truncated display_name is reported, not repaired — we do not rename │
     │ assets                                                                  │
     │ on the operator's behalf.                                               │
     ╰─────────────────────────────────────────────────────────────────────────╯

✻ Brewed for 4m 45s

❯ follow this outline here that i wrote for you: Do not edit relay/core.py,     
  tests, fixtures, demo.py, stress.py, Makefile,                                
  README.md, DECISIONS.md, SUBMISSION.md, or any other file.                    
  Do not commit anything yet.                                                   
  <---don't edit any of this YET.reread: TASK.md, fixtures/operator_report.txt, 
  fixtures/deployment_events.jsonl, relay/core.py,  tests/test_visible.py,      
  demo.py, stress.py, every fixtures/deployment_request*.json file, Makefile    

⏺ Re-reading all of them. No edits, no commits.

  Read 4 files (ctrl+o to expand)

❯ Use this entire concept as evidence to support a starting theory, but you can 
  challenge it wherever needed. 1. C2 and C4 will most likely share the same    
  request idempotency failure. Repeated submissions will always create new runs 
  and then new provider keys. 2. C5 is related but different. This idempotency  
  key was reused with a different payload content that should not be accepted   
  silently and should be reviewed and processed by an internal test that can    
  proof check it. 3. C6 is a cancellation state transition failure, which is    
  likely because cancellations can be overwritten by running or done, and it    
  does not stop the running or rights that occur later on. 4. C7 is just        
  inaccurate completely with how it verifies things. For example, verified      
  equals true is not independently derived, especially from the current         
  provider state. 5. C8 combines unsafe worker claims with concurrent provider  
  state updates. 6. C9 shows recovery is unable to do per-run failure           
  isolations. 7. C10 is very ambiguous for what it provides the result for. An  
  error can occur, but readback shows that the object might have been accepted. 
  8. C1 is a successful crash recovery control. That is okay! 9. C3 is most     
  likely a very legit deployment control, since the keys will actually differ.  
  10. C11 is the strongest out-of-scope candidate, so this is something that we 
  don't need to worry about.                                                    

  Read 3 files (ctrl+o to expand)

❯ # Current evidence                                                            
  Separate direct facts from interpretations.                                   
                                                                                
  # Current theory                                                              
  Group c1-c11 by likely common cause.                                          
  For every theory, state what observation or test would falsify it.            
                                                                                
  # Operator promise                                                            
  Write a narrow plain-English promise.                                         
  Do not promise rollback, deletion, exactly-once execution, or anything the    
  local                                                                         
  provider cannot prove.                                                        
                                                                                
  # Definition of complete                                                      
  A proposed definition should address:                                         
  - every approved asset has one corresponding current provider draft;          
  - no approved asset is missing;                                               
  - no duplicate or unexpected provider object exists for that logical          
  deployment;                                                                   
  - source asset ID, source hash, type, expected provider-normalized display    
  name,                                                                         
    and draft status match;                                                     
  - verification comes from current provider readback, not only an old receipt; 
  - the local run is not called done/verified when this cannot be proved;       
  - behavior for an empty approved request is explicitly defined;               
  - repeated same-key/same-payload submission is safe;                          
  - same-key/different-payload submission is rejected as a conflict.            
                                                                                
  # Planned changes                                                             
  For each proposed change, label it as exactly one of:                         
  - ROOT CAUSE FIX                                                              
  - FALSE-PASS PREVENTION                                                       
  - OBSERVABILITY ONLY                                                          
  - OUT OF SCOPE                                                                
   <---here's part 1 of the roadmap outline id' like for you to follow          

  Ran 1 shell command (ctrl+o to expand)

⏺ All reread. Your theory holds on 8 of 10. Two I'd push on, and one gained hard
  supporting evidence I hadn't seen before.

  New evidence for your point 9 (c3 is legit). stress.py:30 submits the same 
  payload under 12 different idempotency keys, and stress.py:56 declares the
  expected result as WORKERS * assets = 48 objects. The starter's own harness
  defines different-key + same-payload as separate deployments. c3 is legit by
  the repo's own definition, not just by intuition.

  Writing the roadmap to your outline now.
  ⎿  Interrupted · What should Claude do instead?

❯ Keep the design small enough for the 2-hour target.                           
  Treat FakeHubSpot as an external provider that must not be modified.          
  Coordination may be added around it, not inside its behavior.                 
                                                                                
  # Acceptance tests                                                            
  Tests must independently derive expectations from the approved payload.       
  Do not hardcode four objects, fixture IDs, or fixture-specific hashes.        
  Use the full, short-name, and empty request shapes where relevant.            
                                                                                
  Include tests for at least:                                                   
  - same key + same payload does not produce another logical deployment;        
  - same key + changed payload raises IdempotencyConflict;                      
  - retry does not create another set of drafts;                                
  - deleting a provider object causes current verification to fail;             
  - tampering with source identity/hash/type/status causes verification to      
  fail;                                                                         
  - cancellation after one write prevents later writes and cannot become done;  
  - two workers cannot both normally claim the same pending run;                
  - concurrent different runs do not lose provider objects;                     
  - one broken recovery candidate does not stop later good runs;                
  - an ambiguous write exception followed by a matching readback is reconciled; 
  - an empty approved request has the explicitly (please read through all this  
  too. and then also continue how your writing the roadmap but just keep this   
  as additional context for how the outline should be) (continue work)          

⏺ All reread. Writing the roadmap to your outline.

⏺ Updated plan
  ⎿  /plan to preview

⏺ Roadmap drafted in the plan file. Where I diverged from your theory:

  Your point 1 (c2 + c4 same failure) — right at the provider-key level, but c4 
  has a second fault stacked. The log shows worker_stall, local_status: running
  before the retry. The run was stuck because nothing owns or times out a run —
  that's the c8 cause. The operator's retry was a response to that, and it
  duplicated because of the run-id key. So c2 is stopped at submit; c4 is not,
  because retry() deliberately makes a new run. Only the approval-derived key
  catches it.

  Your point 7 (c10 ambiguous) — I'd reframe it as evidence, not a fault. Write
  timed out, readback found the object. With an idempotent provider that's the
  correct resolution shape. It argues for readback-based verification. The
  actual gap it exposes is that run_once has no try/except around create_draft
  at all, so a timeout kills the run mid-payload.

  Your point 9 (c3 legit) — confirmed, and stronger than intuition. stress.py:30
  submits the same payload under 12 different keys and stress.py:56 declares 48
  objects as the expected answer. The repo's own harness defines different-key
  + same-payload as separate deployments. Flagged in the roadmap as a choice
  though — keying on payload hash alone would merge them and make c3 a duplicate
  bug.

  Your point 8 (c1 control) — agreed with a caveat. It only passes because
  recovery reuses the same run_id, which keeps the provider key stable by
  accident of the very design that causes c2/c4. And its receipt is still an
  unchecked constant. It's a control for duplicates, not for verification.

  One thing your outline changed in my design. You wrote "expected
  provider-normalized display name." That means truncation is the provider's
  contract, not a discrepancy — so deployment_request.json stays verified: true.
  I had it failing. Yours is more defensible. I kept the collision from F10
  (two approved emails normalize to the identical name) as a receipt warning,
  labelled OBSERVABILITY ONLY, and noted someone could argue it should be a hard
  failure.

  Weakest claim in the document, stated as such: c9. Cause is plain in the code,
  but payload C isn't in fixtures/, so I had to invent a broken payload to make
  it raise. Marked code-visible, not observed.

  Also separated 20 direct facts from 5 interpretations, so the arguable parts
  are quarantined rather than mixed in.

⏺ User answered Claude's questions:
  ⎿  · Your acceptance-test list cut off mid-sentence at "an empty approved 
     request has the explicitly...". I read it as "explicitly defined behavior" 
     and chose reject-at-submit. Is there a part 2 of the outline coming, or 
     should I finalize on what I have? → Part 2 is coming
     · The two long email names in deployment_request.json normalize to the 
     identical 40-char string, so the drafts are indistinguishable in HubSpot. 
     Under your definition that's an expected normalization, so verification 
     passes. How should it be reported? → Reject at submit, before deploying

⏺ Before I write that in — it has a consequence you should see.
  Ran 1 shell command (ctrl+o to expand)

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Roadmap updated for reject-at-submit. One consequence you should decide on
  before part 2, because it's the biggest thing in the document.

  Reject-at-submit for name collisions rejects deployment_request.json — the
  primary fixture. Verified:

  deployment_request.json        assets=4  collisions=1  -> REJECTED
     asset-email-002 and asset-email-003 both normalize to
     'Summer 2026 ABM campaign email - product'
  deployment_request_short.json  assets=4  collisions=0  -> accepted
  deployment_request_empty.json  assets=0  collisions=0  -> accepted

  That fixture is used by demo.py:12, stress.py:20, and both starter tests
  (test_visible.py:14, :43). So all three make targets break unless reworked,
  and the two starter tests can't be kept passing unchanged. TASK.md requires
  all three working but does permit changing tests freely.

  I wrote the rework into §7 rather than backing off the decision: make demo
  opens with the two refusals (collision, then empty) and then deploys the short
  fixture, crashes, recovers, and reports honestly. make stress moves to the
  short fixture. Starter tests move with it.

  The case for holding the line: that fixture is the only one with names past
  the limit, and TASK.md says the shapes differ on purpose — it looks like it
  exists to trigger exactly this. The case against: a reviewer opens the repo
  and the headline fixture no longer deploys, which reads as breakage rather
  than a stance.

  Also logged in §8: collision detection only covers normalization we know about
  (strip + truncate). Anything else HubSpot does to a name stays invisible
  until a readback disagrees.

  Standing by for part 2 of the outline — holding the draft rather than
  finalizing against a partial spec, as you asked.

✻ Baked for 4m 53s

❯ # Mutation tests                                                              
                                                                                
  For every acceptance check, describe one intentional implementation break     
  that                                                                          
                                                                                
  must make that check fail.                                                    
                                                                                
                                                                                
                                                                                
  Examples:                                                                     
                                                                                
  - remove the idempotency lookup;                                              
                                                                                
  - make audit trust the old receipt;                                           
                                                                                
  - remove the cancellation check;                                              
                                                                                
  - remove provider coordination;                                               
                                                                                
  - allow recovery exceptions to escape the entire loop.                        
                                                                                
                                                                                
                                                                                
  Do not merely duplicate the acceptance-test wording. State the exact behavior 
                                                                                
  being intentionally broken and the expected failing check. (what im leaving   
  out of scope: explaining why each exclusion is not necessary to answer the    
  operator report. please ignore c11 unless there is actual importance of it    
  for deployment. I'd have you try to create code that is production level, but 
  do not  OVEREMPHASIZE that as hubspot integartion, rollbacks, istributed      
  leases,                                                                       
                                                                                
  long-term retry scheduling, UI, and schema migration. You may consider all    
  these aspects of hubspot integration but we are having a virtual environment  
  and fake version of hubspot so this will not all be accurate. After you       
  rewrite the ROADMAP.md, or add this extra portion, then stop and spit back to 
  me : 1)  the complete ROADMAP.md; 2)git diff -- ROADMAP.md; 3) git status     
  --short; and 4) confirmation that no other file changed. (DO NOT commit or    
  implement anything yet)                                                       

⏺ Adding mutation tests and the scope section. Note upfront: this isn't a git
  repo, so the diff/status commands won't produce output — I'll show you what
  they actually return.

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

  Read 1 file, ran 1 shell command (ctrl+o to expand)

⏺ Found an inconsistency in my own document — C11 rejects the full fixture, but
  half the acceptance tests still used it. Fixing.

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan  ⎿  /plan to preview

❯ We did hear that there is one task that's completely unrelated to the         
  operators' specific problem, which suggests that C11 is the most likely       
  candidate for the one event that's unrelated. I believe that we should ignore 
  this because all it does is check whether it's the case, whether or not it's  
  the thumbnail generation tool timeout. I believe it's best to ignore this.    
  ignore c11 FOR NOW. unless it leads to issues with deployment (and how we     
  simulat edeployment) later on                                                 

⏺ Important catch in your message — C11 in my change table is a different thing 
  from c11 in the event log, and that collision is my fault. C11 is the
  display-name rejection; c11 is thumbnail_render. Renaming my change IDs to CH*
  so they can't be confused.

  Ran 1 shell command (ctrl+o to expand)

⏺ Updated plan
  ⎿  /plan to preview

  Ran 1 shell command (ctrl+o to expand)

⏺ Answering 2–4 first, then the full text.

  2) git diff -- ROADMAP.md
  warning: Not a git repository. Use --no-index to compare two paths outside a
  working tree

  3) git status --short
  fatal: not a git repository (or any of the parent directories): .git

  Neither command can run —
  /Users/nityanthmaramreddy/Downloads/02-durable-run-relay has no .git. There is
  nothing to diff against, and ROADMAP.md does not exist on disk yet.

  4) Confirmation no other file changed. Every repo file still carries its
  original Aug 2 12:43:41 2026 mtime:

  Aug 2 12:43:41  .gitignore, DECISIONS.md, demo.py, Makefile, README.md,
                  stress.py, SUBMISSION.md, TASK.md,
                  relay/__init__.py, relay/core.py, tests/test_visible.py,
                  fixtures/{deployment_events.jsonl, operator_report.txt,
                            deployment_request{,_short,_empty}.json}
  Aug 5 12:36:44  .claude/settings.local.json   <- permissions, from running
  make; not an edit by me

  Only .pyc caches and that settings file are newer, both side effects of
  running make test/make demo/make stress. No source, test, fixture, or doc file
  touched. Nothing committed.

  1) The complete ROADMAP.md — it lives in the plan file, not on disk, because
  plan mode blocks writing anything else. Two changes you should know about
  before reading:

  - I renamed my change IDs C1…C15 → CH1…CH15. Your message read C11 as the
  thumbnail case; C11 was my display-name fix and c11 is the event. That
  collision was mine to fix.
  - c11 is now three lines in the whole document: listed as out of scope,
  flagged as an interpretation not a fact, and marked "revisited only if it
  turns out to touch how a deployment is carried out or simulated."

  I also caught a contradiction in my own draft while writing the mutation
  section: CH11 rejects deployment_request.json, but eleven of the acceptance
  tests still used that fixture as their workhorse. Fixed — the short fixture
  carries everything, the full fixture is used in exactly one test (the
  collision refusal), and tests needing a second deployable payload derive one
  in-test rather than reaching for another file.

  Read 1 file (ctrl+o to expand)

⏺ Updated plan
  ⎿  /plan to preview

⏺ Updated plan
  ⎿  /plan to preview

⏺ Here is the complete ROADMAP.md, ready to write on approval.

  ---
  ROADMAP — deployments an operator can trust

  Design kept small enough for the 2-hour target. FakeHubSpot is treated as an
  external provider and is not modified; all coordination is added around it.

  ---
  1. Current evidence

  Direct facts — observed by running code, not by reading it

  #: F1
  Fact: "verified": True is a literal in the source (core.py:209). No comparison

  produces it.
  How: read + probe
  ────────────────────────────────────────
  #: F2
  Fact: deployment_summary (core.py:222) and audit (core.py:239) read the
  receipt
  out of SQLite. Neither opens the provider.
  How: deleted 2 objects from the provider state file; both still reported
  objects_deployed: 4, all_present: true, verified: true
  ────────────────────────────────────────
  #: F3
  Fact: The provider key is f"{run_id}:{asset_id}" (core.py:194) and run_id is a

  fresh UUID per run (core.py:117, :161).
  How: read + probe
  ────────────────────────────────────────
  #: F4
  Fact: Same idempotency key submitted twice → 2 runs, 8 provider objects.
  How: probe
  ────────────────────────────────────────
  #: F5
  Fact: retry() → 8 provider objects.
  How: probe
  ────────────────────────────────────────
  #: F6
  Fact: cancel() then run_once() → status returns to done, 4 drafts created.
  RunCancelled (core.py:19) is defined and never raised anywhere.
  How: probe + grep
  ────────────────────────────────────────
  #: F7
  Fact: No lease, claim, owner, or worker id exists anywhere in the repo.
  How: grep
  ────────────────────────────────────────
  #: F8
  Fact: FakeHubSpot._load/_save is an unlocked read-modify-write of one JSON
  file
  (core.py:61, :78, :79). 50 concurrent creates: 34–44 returned success,
  provider held 2–6, the rest raised JSONDecodeError from reading a
  half-written file.
  How: probe, 3 trials
  ────────────────────────────────────────
  #: F9
  Fact: make stress: 48 objects created, provider holds 36–44, varies every run.

  1–3 runs left running, then the recovery pass flips all 12 to done.
  How: 4 runs of make stress
  ────────────────────────────────────────
  #: F10
  Fact: _store_display_name (core.py:52-53) strips and truncates to 40 chars. On

  deployment_request.json, 4 approved names become 3 distinct stored names —
  asset-email-002 and asset-email-003 are indistinguishable in HubSpot.
  How: probe
  ────────────────────────────────────────
  #: F11
  Fact: deployment_request.json and deployment_request_short.json share every
  asset_id and every source_sha256. Only display_name differs.
  How: probe
  ────────────────────────────────────────
  #: F12
  Fact: deployment_request_empty.json (0 assets) returns objects_deployed: 0, 
  verified: true, all_present: true. all_present is all([]).
  How: probe
  ────────────────────────────────────────
  #: F13
  Fact: test_every_deployed_object_matches_its_source_asset cannot fail. Fed
  source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"; the provider echoed it
  verbatim (core.py:67) and the assert passed.
  How: probe
  ────────────────────────────────────────
  #: F14
  Fact: test_reported_deployment_recovers_without_duplicate_drafts checks object

  count only. In its own scenario 3 of 4 display names do not match the
  approval and it passes.
  How: probe
  ────────────────────────────────────────
  #: F15
  Fact: recover() (core.py:261-266) is a bare loop with no per-run try/except.
  How: read
  ────────────────────────────────────────
  #: F16
  Fact: payload_hash is stored at submit (core.py:119) and never read by any
  decision.
  How: read
  ────────────────────────────────────────
  #: F17
  Fact: stress.py:30 submits the same payload under 12 different idempotency
  keys; stress.py:56 declares the expected result as WORKERS * assets = 48
  objects.
  How: read
  ────────────────────────────────────────
  #: F18
  Fact: Zero network capability. No requests/socket/urllib/http/httpx import and

  no URL anywhere in relay/, tests/, demo.py, stress.py.
  How: grep
  ────────────────────────────────────────
  #: F19
  Fact: object_id is "hs-" + sha256(external_key)[:12] (core.py:64) — recomputed

  by hand and matched. It restates the key and is not evidence of existence.
  How: probe
  ────────────────────────────────────────
  #: F20
  Fact: thumbnail_render (c11) appears nowhere in the repository.
  How: grep

  Interpretations — reasoned, not observed

  #: I1
  Interpretation: F8 is an artifact of the fake being a JSON file; real HubSpot
  does not lose writes.
  Why it is not a fact: We have no real HubSpot to check against.
  ────────────────────────────────────────
  #: I2
  Interpretation: deploy-207a / deploy-207b (c3) are two legitimate approvals,
  not one duplicated.
  Why it is not a fact: The keys differ; whether that reflects operator intent
  is
  unknowable from the log. F17 supports it strongly.
  ────────────────────────────────────────
  #: I3
  Interpretation: c9's payload_version: "C" is a request shape the code cannot
  handle.
  Why it is not a fact: Payload C is not in fixtures/. Its content is unknown.
  ────────────────────────────────────────
  #: I4
  Interpretation: c11 is out of scope.
  Why it is not a fact: Based on absence of evidence (F20), which is weaker than

  presence.
  ────────────────────────────────────────
  #: I5
  Interpretation: The empty-request case (F12) is the same defect as the
  partial-loss case.
  Why it is not a fact: The empty fixture appears nowhere in the event log; the
  tie to the operator report is via "keeps disagreeing with what the service
  says it did," which is a broad reading. Weakest tie in this document.

  ---
  2. Current theory

  Grouped by common cause. Each group states what would falsify it.

  G1 — The receipt is not derived from anything (c7, c8, c1-partially)

  verified is a constant (F1) and both operator-facing screens re-read that
  constant instead of the provider (F2). The service reports intent, never
  state.

  - c7 — receipt said verified, drafts were not what was approved. Nothing ever
  compared them.
  - c8 — receipt says 4 objects, operator counts 3. The receipt was built from
  readbacks taken before another worker's write erased one.
  - Falsified if: deleting or altering a provider object caused audit to report
  a problem. It does not (F2).

  G2 — Identity is derived from the run, not the approval (c2, c4)

  The provider key contains run_id (F3), so every new run is a new set of
  drafts. Repeated submits (c2) and admin retries (c4) both mint new runs.
  idempotency_key is stored and never enforced; payload_hash is stored and never
  read (F16).

  c2 and c4 share the provider key cause but need different repairs at the door:

  - c2 is stopped by enforcing the idempotency key at submit.
  - c4 is not, because retry() deliberately creates a new run (core.py:154-177).
  Only an approval-derived provider key stops it.

  c4 also contains a second, separate fault.
  {"event":"worker_stall","local_status":"running"} precedes the retry. The run
  was stuck because nothing owns or times out a run (F7) — that is G4. The
  operator's retry was a response to G4, and it duplicated because of G2. Two
  faults stacked, which is why c4 looks like both.

  - Falsified if: submitting the same approved request twice produced one set of
  drafts. It produces 8 (F4, F5).

  G3 — The same key with different content is accepted silently (c5)

  deploy-303 submitted with payload A then payload B. Both are accepted, both
  get rows, both would deploy. The column that could detect this (payload_hash)
  is written and never compared (F16).

  Related to G2, distinct from it: G2 is "one approval deployed twice," G3 is
  "one key claiming two different approvals." G2 wants dedupe. G3 wants
  rejection.

  - Falsified if: the second submit raised. It does not.

  G4 — Nothing owns a run (c8, and the stall inside c4)

  Two worker_claim events for run-14 and no claim mechanism exists (F7). Both
  workers run the whole payload, both build receipts, and their concurrent
  provider writes erase each other (F8).

  - Falsified if: a second worker calling run_once on a claimed run declined to
  proceed. Nothing stops it.

  G5 — Cancellation is advisory (c6)

  cancel writes a status (core.py:147-152). run_once never reads it, and its
  final UPDATE ... status='done' (core.py:211-219) is unconditional, so a
  finishing worker overwrites the cancel. RunCancelled is dead code (F6).

  - Falsified if: a cancelled run stopped writing and stayed cancelled. It
  reaches done and writes all 4 drafts (F6).

  G6 — Recovery has no per-run failure isolation (c9)

  recover() is a bare loop (F15). One raise ends the pass and every run behind
  it stays running forever. Matches "two campaigns behind it never went out."

  Honest status: cause is visible in the code, my reproduction was rigged. I
  made it raise with a payload I invented (missing source_sha256), because
  payload C is not in fixtures/ (I3). Attempting it with a naturally-occurring
  error did not reproduce the stuck state. Treat G6 as code-visible, not
  observed.

  - Falsified if: a failing run in the recovery set left the runs behind it
  deployable. Untested against a natural trigger — this is the weakest claim
  here.

  G7 — Ambiguous writes have no resolution path (c10)

  I disagree with calling c10 a fault. The log shows a write returning
  gateway_timeout, retryable:true and a readback finding the object. That is the
  correct shape: an idempotent provider plus a readback resolves the ambiguity
  deterministically. c10 is evidence for readback-based verification, not
  against it.

  What it does expose: run_once has no try/except around create_draft at all, so
  a timeout would propagate and the run would die mid-payload. The gap is the
  missing reconciliation path, not the ambiguity itself.

  - Falsified if: a write that raised, followed by a matching readback, were
  already treated as success. There is no such path.

  Controls and noise

  c1 — successful crash recovery. Agreed, with a caveat. It passes because
  recover() reuses the same run_id, which keeps the provider key stable by
  accident of G2's design, not by intent. And its receipt is still an unchecked
  constant. So c1 is a control for duplicates only, not for verification.

  c3 — legitimate, and F17 settles it. Two different idempotency keys, same
  payload, 8 objects. stress.py does exactly this — same payload, 12 different
  keys — and declares the expected answer to be 48 objects. The repo's own
  harness defines different-key + same-payload as separate deployments.

  This is a design choice, not a fact. Keying on payload_hash alone instead
  would merge them into 4 and make c3 a duplicate bug. I am choosing the reading
  stress.py encodes. Another engineer could reasonably take the other side, and
  if they do, c3 moves into G2.

  - Falsified if: an operator confirmed 207a/207b were one approval retried by a
  client that regenerated its key.

  c11 — out of scope. thumbnail_render exists nowhere in the repository (F20)
  and is not on the deployment path. Treated as TASK.md's "at least one is
  unrelated" and not investigated further. Revisited only if it turns out to
  touch how a deployment is carried out or simulated.

  ---
  3. Operator promise

  ▎ When this service reports a deployment as verified, it has just read every 
  ▎ approved asset back from HubSpot and confirmed each one is present, is the 
  ▎ asset that was approved, and is a draft. If it cannot confirm that, it does 
  ▎ not say verified — it says what it could not confirm.
  ▎
  ▎ An approved request deploys once. Submitting it again, retrying it from the 
  ▎ admin panel, or recovering it after a crash reuses the same HubSpot objects 
  ▎ rather than creating new ones. Submitting the same key with different 
  ▎ content is refused, not merged.
  ▎
  ▎ A cancelled deployment stops writing and stays cancelled.
  ▎
  ▎ A deployment that cannot complete fails by itself and does not hold up the 
  ▎ deployments behind it.

  Deliberately not promised: rollback, deletion, cleanup of anything already
  written, exactly-once execution, or that HubSpot still matches a second after
  we looked. The provider exposes only create_draft, read, and list_objects —
  there is no delete and no update, so none of those could be proved here.

  ---
  4. Definition of complete

  A deployment is complete and verified only when all of the following hold,
  each checked against a fresh provider readback at the moment of the check:

  1. Coverage — every approved asset has exactly one corresponding provider
  draft.
  2. No omissions — no approved asset is missing from the provider.
  3. No extras — scanning the provider for objects belonging to this logical
  deployment yields exactly the approved set, no duplicates and no strangers.
  4. Identity match, per object: source_asset_id, source_sha256, object_type,
  status == "draft", and display_name equal to the expected provider-normalized
  form of the approved name.
  5. Freshness — the verdict comes from reading the provider now, never from
  replaying a stored receipt.
  6. Honesty — if any of 1–5 cannot be proved, the run is not done and not
  verified; the receipt names each specific failure.
  7. Empty request — an approved request with zero assets is rejected at submit
  and never becomes a run. Rationale: a deployment of nothing is not a
  deployment, and letting it through is exactly what produces "verified
  campaign, nothing in HubSpot." Stated as a rule, not a fixture special-case:
  len(assets) == 0 is refused for any payload. The alternative — run it and
  force verified: false — is defensible; I am choosing to fail earlier and
  louder.
  8. Repeat safety — same key + same payload returns the existing run and
  produces no additional provider objects.
  9. Conflict — same key + different payload raises IdempotencyConflict before
  anything is written.
  10. Distinguishable names — an approved request in which two assets normalize
  to the same provider display name is rejected at submit.

  On point 4: provider-normalized means the relay models the provider's
  documented rule (strip, truncate to FakeHubSpot.DISPLAY_NAME_LIMIT — read from
  the class, never a literal 40) and expects the truncated value. Truncation is
  the provider's contract, not a discrepancy.

  On point 10: truncation is fine, collision is not. The operator approved four
  assets they can tell apart; HubSpot would hold two they cannot. Delivering
  that is delivering something other than what was approved, which is what c7
  says. The provider exposes no delete and no rename, so there is no way to
  repair it after the write — the only place to stop it is before. General rule,
  derived from DISPLAY_NAME_LIMIT, not a fixture special-case.

  Consequence of point 10 — this rejects the primary fixture

  Verified: deployment_request.json has exactly one collision — asset-email-002
  and asset-email-003 both normalize to 'Summer 2026 ABM campaign email - 
  product'. deployment_request_short.json and deployment_request_empty.json have
  none.

  That fixture is used by demo.py:12, stress.py:20, and both starter tests
  (test_visible.py:14, :43). So under this rule all three make targets stop 
  working unless they are reworked, and the two starter tests cannot be kept
  passing unchanged. TASK.md requires all three to work, and permits changing
  tests freely. Rework plan is in §8.

  ---
  5. Planned changes

  Every change labelled exactly one of ROOT CAUSE FIX / FALSE-PASS PREVENTION /
  OBSERVABILITY ONLY / OUT OF SCOPE.

  #: CH1
  Change: Derive a deployment_key from idempotency_key + payload_hash; provider
  external_key becomes deployment_key:asset_id instead of run_id:asset_id.
  Retries, recovery, and re-submits land on the same HubSpot objects.
  Label: ROOT CAUSE FIX
  Addresses: G2 (c2, c4)
  ────────────────────────────────────────
  #: CH2
  Change: UNIQUE on idempotency_key. Same key + same payload_hash returns the
  existing run_id; different payload_hash raises IdempotencyConflict. Finally
  reads the column from F16.
  Label: ROOT CAUSE FIX
  Addresses: G2, G3 (c2, c5)
  ────────────────────────────────────────
  #: CH3
  Change: Reject zero-asset payloads at submit.
  Label: FALSE-PASS PREVENTION
  Addresses: F12, §4.7
  ────────────────────────────────────────
  #: CH4
  Change: Atomic claim: owner, lease_expires_at, attempt columns; claim via
  conditional UPDATE checked with rowcount. Verified — 20 racing threads,
  exactly 1 winner. Expired leases are reclaimable so a stalled worker does not
   strand a run.
  Label: ROOT CAUSE FIX
  Addresses: G4 (c8, c4's stall)
  ────────────────────────────────────────
  #: CH5
  Change: verified becomes the result of a readback comparison against §4.1–4.4,

  never a literal. Receipt carries a discrepancies list.
  Label: FALSE-PASS PREVENTION
  Addresses: G1 (c7, c8)
  ────────────────────────────────────────
  #: CH6
  Change: audit re-reads the provider instead of the receipt, returning the same

  structure as CH5.
  Label: FALSE-PASS PREVENTION
  Addresses: G1
  ────────────────────────────────────────
  #: CH7
  Change: cancel sets cancel_requested; run_once checks it before every provider

  write and raises RunCancelled; the terminal status='done' update becomes
  conditional on ownership and non-cancellation.
  Label: ROOT CAUSE FIX
  Addresses: G5 (c6)
  ────────────────────────────────────────
  #: CH8
  Change: Per-run try/except in recover, plus a bounded attempt count so a
  permanently failing run stops retrying and is surfaced.
  Label: ROOT CAUSE FIX
  Addresses: G6 (c9)
  ────────────────────────────────────────
  #: CH9
  Change: Wrap create_draft in a reconcile helper outside the provider: on
  exception, read back the expected key; matching → success, absent → bounded
  retry, present-but-different → fail loudly.
  Label: ROOT CAUSE FIX
  Addresses: G7 (c10)
  ────────────────────────────────────────
  #: CH10
  Change: retry resets the existing run to pending and clears its lease instead
  of inserting a second row (which CH2's constraint would forbid anyway).
  Label: ROOT CAUSE FIX
  Addresses: G2 (c4)
  ────────────────────────────────────────
  #: CH11
  Change: Reject at submit any approved request in which two assets normalize to

  the same provider display name. Removes the cause of "the drafts were not
  what we approved" that survives an otherwise-correct deployment. Rejects
  deployment_request.json — see §4.10 and §8.
  Label: ROOT CAUSE FIX
  Addresses: F10 (c7's second half)
  ────────────────────────────────────────
  #: CH12
  Change: SQLite timeout and WAL on _connect, so contention blocks briefly
  instead of raising database is locked. Coordination around storage, no
  behavior change.
  Label: OBSERVABILITY ONLY
  Addresses: F9
  ────────────────────────────────────────
  #: CH13
  Change: The provider's lost-write and torn-read race (F8). Not fixed. It is an

  artifact of the fake (I1), TASK.md says the provider is not ours to change,
  and CH5 makes it visible instead of silent. We detect loss; we do not prevent
   it.
  Label: OUT OF SCOPE
  Addresses: F8
  ────────────────────────────────────────
  #: CH14
  Change: c11 thumbnail_render. Not in the deployment path, not in the
  repository.
  Label: OUT OF SCOPE
  Addresses: c11
  ────────────────────────────────────────
  #: CH15
  Change: Rollback, deletion, cleanup of duplicate drafts already in HubSpot.
  The
  provider has no delete.
  Label: OUT OF SCOPE
  Addresses: —

  Minimum cut if time runs short, in order: CH5, CH6, CH3 (the promise and the
  false passes), then CH2, CH1 (duplicates), then CH7, CH4 (cancel and claim),
  then CH8, CH9. CH10–CH12 last. If CH8/CH9 do not land, they move to
  DECISIONS.md as known-open, which for G6 is honest anyway given it is the
  least-proven group.

  ---
  6. Acceptance tests

  Rules for every test below:

  - Expectations are derived from the approved payload at runtime — iterate
  payload["assets"], compare against len(payload["assets"]).
  - No hardcoded 4, no fixture asset ids, no fixture hashes, no literal 40. Read
  the limit from FakeHubSpot.DISPLAY_NAME_LIMIT.
  - All three shapes are exercised, but not equally — see the fixture note
  below.
  - Provider failure modes are simulated with a wrapper around FakeHubSpot,
  never by editing it.
  - Each new test must be checked to fail against the current code before the
  fix lands. A test that passes on the starter proves nothing (F13, F14).

  Consequence of CH11 on fixture choice. The full fixture is refused at submit,
  so it can no longer be the workhorse. Under this design it is used in exactly
  one test — the collision refusal — and the short fixture carries the rest.
  Where a test needs a second deployable payload, it derives one from the short
  fixture in-test (lengthen or alter a display_name) rather than reaching for
  another file. Deriving is not fixture special-casing; hardcoding the derived
  value would be.

  Test: Same key + same payload → one logical deployment, provider object count
  unchanged on the second submit
  Shape used: short
  Fails today because: 2 runs, 8 objects (F4)
  ────────────────────────────────────────
  Test: Same key + changed payload → IdempotencyConflict
  Shape used: short, then short with one display_name altered in-test. Content
  differs while every asset_id and source_sha256 stays identical — proves the
  check is on content, not ids (F11 shows the fixtures were built to make that
  mistake easy)
  Fails today because: both accepted (F16)
  ────────────────────────────────────────
  Test: retry creates no additional drafts
  Shape used: short
  Fails today because: 8 objects (F5)
  ────────────────────────────────────────
  Test: Deleting a provider object makes current verification fail
  Shape used: short
  Fails today because: audit never opens the provider (F2)
  ────────────────────────────────────────
  Test: Tampering with source_asset_id / source_sha256 / object_type / status in

  provider state makes verification fail
  Shape used: short
  Fails today because: nothing is compared (F1)
  ────────────────────────────────────────
  Test: Cancellation after one write prevents later writes and cannot reach done
  Shape used: short
  Fails today because: status returns to done, all assets written (F6)
  ────────────────────────────────────────
  Test: Two workers cannot both claim the same pending run
  Shape used: short
  Fails today because: no claim exists (F7)
  ────────────────────────────────────────
  Test: Concurrent different runs do not lose provider objects or, where the
  provider does lose them, no run reports verified
  Shape used: short, N threads
  Fails today because: 48 created / 36–44 held, all report done (F9)
  ────────────────────────────────────────
  Test: One broken recovery candidate does not stop later good runs
  Shape used: short + a deliberately broken run
  Fails today because: bare loop (F15)
  ────────────────────────────────────────
  Test: Ambiguous write exception followed by a matching readback is reconciled
  as success
  Shape used: short, via a wrapper that writes then raises
  Fails today because: no try/except around create_draft (G7)
  ────────────────────────────────────────
  Test: Empty approved request is refused at submit and never yields a verified
  receipt
  Shape used: empty
  Fails today because: returns verified: true on zero objects (F12)
  ────────────────────────────────────────
  Test: A name past the limit still verifies — normalization is expected, not a
  discrepancy
  Shape used: short with one name lengthened past DISPLAY_NAME_LIMIT without
  colliding, so it survives CH11 and reaches verification
  Fails today because: nothing compared
  ────────────────────────────────────────
  Test: An unexpected object under this deployment's key prefix makes
  verification fail
  Shape used: short + one injected stray object
  Fails today because: nothing scans for extras
  ────────────────────────────────────────
  Test: Two approved names that normalize identically are refused at submit,
  before any provider write
  Shape used: full — the one test it is used for (F10). Assert the provider is
  still empty afterwards
  Fails today because: request deploys and reports verified

  Both starter tests move to the short fixture and keep asserting what they
  asserted. They are near-worthless as written (F13, F14) and are kept only as
  regression ballast.

  ---
  7. Mutation tests

  Each acceptance check is paired with one deliberate implementation break that
  must make that check fail. If a mutation lands and the suite stays green, the
  check is decoration and gets rewritten. Run as a manual pass — revert each
  mutation before applying the next.

  #: M1
  Behavior intentionally broken: In submit, delete the lookup by idempotency_key

  and unconditionally INSERT a fresh uuid4 row.
  Check that must fail: Same key + same payload → second submit returns a
  different run id and provider object count doubles.
  ────────────────────────────────────────
  #: M2
  Behavior intentionally broken: In submit, keep the lookup but compare
  idempotency_key only, ignoring payload_hash — return the existing run instead
   of raising.
  Check that must fail: Same key + changed payload → no IdempotencyConflict; the

  new payload silently inherits the old payload's drafts.
  ────────────────────────────────────────
  #: M3
  Behavior intentionally broken: Restore external_key = f"{run_id}:{asset_id}"
  in
  run_once, discarding the approval-derived key.
  Check that must fail: retry produces a second full set of drafts. (Also kills
  M1's check — acceptable; each check needs at least one killer, not exactly
  one.)
  ────────────────────────────────────────
  #: M4
  Behavior intentionally broken: In the verification routine, replace the
  per-key
  provider.read() with json.loads(row["receipt_json"])["objects"].
  Check that must fail: Deleting a provider object leaves verification reporting

  present and verified.
  ────────────────────────────────────────
  #: M5
  Behavior intentionally broken: In the field comparison, assert only that
  object_id is truthy; drop the equality checks on source_asset_id,
  source_sha256, object_type, status.
  Check that must fail: Tampering with any of those four in provider state still

  verifies.
  ────────────────────────────────────────
  #: M6a
  Behavior intentionally broken: Remove the cancel_requested read from the
  per-asset loop in run_once.
  Check that must fail: A run cancelled after the first write keeps writing the
  remaining assets.
  ────────────────────────────────────────
  #: M6b
  Behavior intentionally broken: Make the terminal update unconditional again —
  drop AND status='running' AND cancel_requested=0  AND owner=?.
  Check that must fail: A cancelled run reaches done.
  ────────────────────────────────────────
  #: M7
  Behavior intentionally broken: In the claim, execute the conditional UPDATE
  but
  ignore cursor.rowcount and proceed regardless.
  Check that must fail: N workers racing one pending run → more than one
  proceeds
  to write.
  ────────────────────────────────────────
  #: M8
  Behavior intentionally broken: Build the receipt from the dicts create_draft
  returned, rather than from a fresh readback taken after every write
  completes.
  Check that must fail: A run whose object was erased by a concurrent writer
  still reports verified: true — this is the exact c8 mechanism.
  ────────────────────────────────────────
  #: M9
  Behavior intentionally broken: Remove the per-run try/except in recover so the

  first exception escapes the loop.
  Check that must fail: Runs queued behind a broken recovery candidate stay
  undeployed.
  ────────────────────────────────────────
  #: M10
  Behavior intentionally broken: Delete the reconcile wrapper; call
  provider.create_draft directly and let exceptions propagate.
  Check that must fail: A write that raises after persisting, followed by a
  matching readback, kills the run instead of being reconciled as success.
  ────────────────────────────────────────
  #: M11
  Behavior intentionally broken: Remove the zero-asset guard from submit.
  Check that must fail: An empty approved request gets a run id and a verified: 
  true receipt over zero objects.
  ────────────────────────────────────────
  #: M12
  Behavior intentionally broken: Set the expected display name to the raw
  approved string instead of the normalized form.
  Check that must fail: A request whose names exceed the limit reports a false
  discrepancy and fails to verify. This is the one mutation  that catches 
  over-strictness rather than  under-strictness — without it, a verifier that
  rejects everything would pass every other check here.
  ────────────────────────────────────────
  #: M13
  Behavior intentionally broken: Remove the normalization-collision precheck
  from
  submit.
  Check that must fail: A request with two colliding names deploys, and the
  provider ends up holding two drafts with identical display names.
  ────────────────────────────────────────
  #: M14
  Behavior intentionally broken: In verification, iterate approved assets only;
  drop the reverse scan for provider objects under this deployment's key
  prefix.
  Check that must fail: A stray object under the deployment's prefix goes
  unnoticed and the run still verifies.

  Test-suite integrity mutation. Separately, change
  FakeHubSpot.DISPLAY_NAME_LIMIT and swap which fixture a test loads. Any test
  that hardcoded 40, 4, a fixture asset id, or a fixture hash breaks in a way
  that has nothing to do with the behavior under test. That break is the signal
  — those tests get rewritten to derive from the payload.

  ---
  8. Verification, and the make-target rework CH11 forces

  make demo, make test, make stress must all keep working (TASK.md). CH11
  rejects deployment_request.json, which all three use, so all three need
  rework. This is the largest single consequence in this roadmap and the 
  decision most worth arguing about.

  make demo — the rejection becomes the opening act, not a broken step:

  1. Submit deployment_request.json → refused at the door, naming the two assets
  that would have been indistinguishable in HubSpot. Nothing written. Provider
  still empty.
  2. Submit deployment_request_empty.json → refused, nothing to deploy.
  3. Submit deployment_request_short.json → deploys, crashes partway, recovers,
  reports verified from a live readback.
  4. Delete two objects from the provider's state file, exactly as demo.py:42-45
  does today, then press "Check again" → now reports the loss instead of hiding
  it.

  Step 4 against the old output is the demonstration. Steps 1–2 show the two
  refusals that stop a false success before it can exist.

  make stress — switch to deployment_request_short.json, the only fixture that
  both deploys and has no collision. Counts stay derived (WORKERS * assets),
  nothing hardcoded. It should still show the provider short of the expected
  total (CH13 is out of scope), but no run may report verified: true while its 
  objects are missing, and the recovery pass must not report success over a
  short provider. That inversion — same short count, honest verdict — is the
  point.

  make test — the two starter tests move to the short fixture (see §6), plus the
  new acceptance suite.

  Risk to name in DECISIONS.md: a reviewer will notice the headline fixture no
  longer deploys. The defense is that the fixture appears to exist precisely to
  trigger this — it is the only one with names past the limit, and TASK.md says
  the shapes differ on purpose. If that reading is wrong, CH11 should drop back
  to a receipt warning and everything above reverts.

  9. Out of scope

  Listed, not argued.

  - Real HubSpot integration, credentials, network calls of any kind.
  - Rollback, deletion, or cleanup of anything already written. The provider
  exposes create_draft, read, list_objects and nothing else.
  - Distributed leases across hosts. The lease here coordinates workers against
  one SQLite file and claims no more than that.
  - Long-term retry scheduling, backoff daemons, dead-letter queues.
  - Any UI.
  - Schema migration. Every entry point builds its own database.
  - The provider's internal lost-write and torn-read race (CH13, F8).
  - c11 thumbnail_render (F20).
  - Queue, workflow framework, container, cloud service, real model, OAuth.

  Production posture

  The code should read as production code — typed, small functions, no bare
  except, structured failure values instead of booleans, every decision derived
  rather than asserted. It should not grow the machinery a real deployment would
  need on top of that. The lease is a WHERE clause, not a lock service.
  Reconciliation is a bounded readback, not a scheduler. The provider is a JSON
  file behind an interface we do not touch. Where a real system would need more,
  that belongs in §10 as a stated limit, not as a half-built version of the
  real thing.

  10. What this still cannot promise

  For DECISIONS.md:

  - The provider can still lose writes. We detect it; we do not prevent it
  (CH13).
  - Verification is point-in-time. HubSpot can change a second after audit
  returns.
  - There is still a window between a provider write and the local receipt.
  Recovery closes it by re-reading, not by making the two atomic.
  - A normalization collision is refused, not repaired. We do not rename an
  operator's assets — we hand the request back and say which two names collide.
  - Collision detection only covers what the relay knows the provider does to a
  name (strip + truncate). Any other normalization HubSpot applies is invisible
  to us until a readback disagrees.
  - Nothing is ever cleaned up. Duplicate drafts already in HubSpot from before
  this change stay there — the provider has no delete.
  - G6 (c9) is fixed at a cause we reasoned about but never reproduced with a
  natural trigger.

  ---
  Nothing written to disk, nothing committed. Approving below lets me create
  ROADMAP.md with exactly this content — no source, test, fixture, or Makefile
  edits, and no git operations.
  ⎿  User rejected Claude's plan:
     ╭─────────────────────────────────────────────────────────────────────────╮
     │ ROADMAP — deployments an operator can trust                             │
     │                                                                         │
     │ Design kept small enough for the 2-hour target. FakeHubSpot is treated  │
     │ as an                                                                   │
     │ external provider and is not modified; all coordination is added around │
     │ it.                                                                     │
     │                                                                         │
     │ ---                                                                     │
     │ 1. Current evidence                                                     │
     │                                                                         │
     │ Direct facts — observed by running code, not by reading it              │
     │                                                                         │
     │ #: F1                                                                   │
     │ Fact: "verified": True is a literal in the source (core.py:209). No     │
     │ comparison                                                              │
     │ produces it.                                                            │
     │ How: read + probe                                                       │
     │ ────────────────────────────────────────                                │
     │ #: F2                                                                   │
     │ Fact: deployment_summary (core.py:222) and audit (core.py:239) read the │
     │ receipt                                                                 │
     │ out of SQLite. Neither opens the provider.                              │
     │ How: deleted 2 objects from the provider state file; both still         │
     │ reported                                                                │
     │ objects_deployed: 4, all_present: true, verified: true                  │
     │ ────────────────────────────────────────                                │
     │ #: F3                                                                   │
     │ Fact: The provider key is f"{run_id}:{asset_id}" (core.py:194) and      │
     │ run_id is a                                                             │
     │ fresh UUID per run (core.py:117, :161).                                 │
     │ How: read + probe                                                       │
     │ ────────────────────────────────────────                                │
     │ #: F4                                                                   │
     │ Fact: Same idempotency key submitted twice → 2 runs, 8 provider         │
     │ objects.                                                                │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F5                                                                   │
     │ Fact: retry() → 8 provider objects.                                     │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F6                                                                   │
     │ Fact: cancel() then run_once() → status returns to done, 4 drafts       │
     │ created.                                                                │
     │ RunCancelled (core.py:19) is defined and never raised anywhere.         │
     │ How: probe + grep                                                       │
     │ ────────────────────────────────────────                                │
     │ #: F7                                                                   │
     │ Fact: No lease, claim, owner, or worker id exists anywhere in the repo. │
     │ How: grep                                                               │
     │ ────────────────────────────────────────                                │
     │ #: F8                                                                   │
     │ Fact: FakeHubSpot._load/_save is an unlocked read-modify-write of one   │
     │ JSON file                                                               │
     │ (core.py:61, :78, :79). 50 concurrent creates: 34–44 returned success,  │
     │ provider held 2–6, the rest raised JSONDecodeError from reading a       │
     │ half-written file.                                                      │
     │ How: probe, 3 trials                                                    │
     │ ────────────────────────────────────────                                │
     │ #: F9                                                                   │
     │ Fact: make stress: 48 objects created, provider holds 36–44, varies     │
     │ every run.                                                              │
     │ 1–3 runs left running, then the recovery pass flips all 12 to done.     │
     │ How: 4 runs of make stress                                              │
     │ ────────────────────────────────────────                                │
     │ #: F10                                                                  │
     │ Fact: _store_display_name (core.py:52-53) strips and truncates to 40    │
     │ chars. On                                                               │
     │ deployment_request.json, 4 approved names become 3 distinct stored      │
     │ names —                                                                 │
     │ asset-email-002 and asset-email-003 are indistinguishable in HubSpot.   │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F11                                                                  │
     │ Fact: deployment_request.json and deployment_request_short.json share   │
     │ every                                                                   │
     │ asset_id and every source_sha256. Only display_name differs.            │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F12                                                                  │
     │ Fact: deployment_request_empty.json (0 assets) returns                  │
     │ objects_deployed: 0,                                                    │
     │ verified: true, all_present: true. all_present is all([]).              │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F13                                                                  │
     │ Fact: test_every_deployed_object_matches_its_source_asset cannot fail.  │
     │ Fed                                                                     │
     │ source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"; the provider echoed it   │
     │ verbatim (core.py:67) and the assert passed.                            │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F14                                                                  │
     │ Fact: test_reported_deployment_recovers_without_duplicate_drafts checks │
     │ object                                                                  │
     │ count only. In its own scenario 3 of 4 display names do not match the   │
     │ approval and it passes.                                                 │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F15                                                                  │
     │ Fact: recover() (core.py:261-266) is a bare loop with no per-run        │
     │ try/except.                                                             │
     │ How: read                                                               │
     │ ────────────────────────────────────────                                │
     │ #: F16                                                                  │
     │ Fact: payload_hash is stored at submit (core.py:119) and never read by  │
     │ any                                                                     │
     │ decision.                                                               │
     │ How: read                                                               │
     │ ────────────────────────────────────────                                │
     │ #: F17                                                                  │
     │ Fact: stress.py:30 submits the same payload under 12 different          │
     │ idempotency                                                             │
     │ keys; stress.py:56 declares the expected result as WORKERS * assets =   │
     │ 48                                                                      │
     │ objects.                                                                │
     │ How: read                                                               │
     │ ────────────────────────────────────────                                │
     │ #: F18                                                                  │
     │ Fact: Zero network capability. No requests/socket/urllib/http/httpx     │
     │ import and                                                              │
     │ no URL anywhere in relay/, tests/, demo.py, stress.py.                  │
     │ How: grep                                                               │
     │ ────────────────────────────────────────                                │
     │ #: F19                                                                  │
     │ Fact: object_id is "hs-" + sha256(external_key)[:12] (core.py:64) —     │
     │ recomputed                                                              │
     │ by hand and matched. It restates the key and is not evidence of         │
     │ existence.                                                              │
     │ How: probe                                                              │
     │ ────────────────────────────────────────                                │
     │ #: F20                                                                  │
     │ Fact: thumbnail_render (c11) appears nowhere in the repository.         │
     │ How: grep                                                               │
     │                                                                         │
     │ Interpretations — reasoned, not observed                                │
     │                                                                         │
     │ #: I1                                                                   │
     │ Interpretation: F8 is an artifact of the fake being a JSON file; real   │
     │ HubSpot                                                                 │
     │ does not lose writes.                                                   │
     │ Why it is not a fact: We have no real HubSpot to check against.         │
     │ ────────────────────────────────────────                                │
     │ #: I2                                                                   │
     │ Interpretation: deploy-207a / deploy-207b (c3) are two legitimate       │
     │ approvals,                                                              │
     │ not one duplicated.                                                     │
     │ Why it is not a fact: The keys differ; whether that reflects operator   │
     │ intent is                                                               │
     │ unknowable from the log. F17 supports it strongly.                      │
     │ ────────────────────────────────────────                                │
     │ #: I3                                                                   │
     │ Interpretation: c9's payload_version: "C" is a request shape the code   │
     │ cannot                                                                  │
     │ handle.                                                                 │
     │ Why it is not a fact: Payload C is not in fixtures/. Its content is     │
     │ unknown.                                                                │
     │ ────────────────────────────────────────                                │
     │ #: I4                                                                   │
     │ Interpretation: c11 is out of scope.                                    │
     │ Why it is not a fact: Based on absence of evidence (F20), which is      │
     │ weaker than                                                             │
     │ presence.                                                               │
     │ ────────────────────────────────────────                                │
     │ #: I5                                                                   │
     │ Interpretation: The empty-request case (F12) is the same defect as the  │
     │ partial-loss case.                                                      │
     │ Why it is not a fact: The empty fixture appears nowhere in the event    │
     │ log; the                                                                │
     │ tie to the operator report is via "keeps disagreeing with what the      │
     │ service                                                                 │
     │ says it did," which is a broad reading. Weakest tie in this document.   │
     │                                                                         │
     │ ---                                                                     │
     │ 2. Current theory                                                       │
     │                                                                         │
     │ Grouped by common cause. Each group states what would falsify it.       │
     │                                                                         │
     │ G1 — The receipt is not derived from anything (c7, c8, c1-partially)    │
     │                                                                         │
     │ verified is a constant (F1) and both operator-facing screens re-read    │
     │ that                                                                    │
     │ constant instead of the provider (F2). The service reports intent,      │
     │ never state.                                                            │
     │                                                                         │
     │ - c7 — receipt said verified, drafts were not what was approved.        │
     │ Nothing ever compared them.                                             │
     │ - c8 — receipt says 4 objects, operator counts 3. The receipt was built │
     │ from readbacks taken before another worker's write erased one.          │
     │ - Falsified if: deleting or altering a provider object caused audit to  │
     │ report a problem. It does not (F2).                                     │
     │                                                                         │
     │ G2 — Identity is derived from the run, not the approval (c2, c4)        │
     │                                                                         │
     │ The provider key contains run_id (F3), so every new run is a new set of │
     │ drafts. Repeated submits (c2) and admin retries (c4) both mint new      │
     │ runs.                                                                   │
     │ idempotency_key is stored and never enforced; payload_hash is stored    │
     │ and                                                                     │
     │ never read (F16).                                                       │
     │                                                                         │
     │ c2 and c4 share the provider key cause but need different repairs at    │
     │ the door:                                                               │
     │                                                                         │
     │ - c2 is stopped by enforcing the idempotency key at submit.             │
     │ - c4 is not, because retry() deliberately creates a new run             │
     │ (core.py:154-177). Only an approval-derived provider key stops it.      │
     │                                                                         │
     │ c4 also contains a second, separate fault.                              │
     │ {"event":"worker_stall","local_status":"running"} precedes the retry.   │
     │ The run was stuck because nothing owns or times out a run (F7) — that   │
     │ is G4. The operator's retry was a response to G4, and it duplicated     │
     │ because of G2. Two faults stacked, which is why c4 looks like both.     │
     │                                                                         │
     │ - Falsified if: submitting the same approved request twice produced one │
     │ set of drafts. It produces 8 (F4, F5).                                  │
     │                                                                         │
     │ G3 — The same key with different content is accepted silently (c5)      │
     │                                                                         │
     │ deploy-303 submitted with payload A then payload B. Both are accepted,  │
     │ both                                                                    │
     │ get rows, both would deploy. The column that could detect this          │
     │ (payload_hash)                                                          │
     │ is written and never compared (F16).                                    │
     │                                                                         │
     │ Related to G2, distinct from it: G2 is "one approval deployed twice,"   │
     │ G3 is "one                                                              │
     │ key claiming two different approvals." G2 wants dedupe. G3 wants        │
     │ rejection.                                                              │
     │                                                                         │
     │ - Falsified if: the second submit raised. It does not.                  │
     │                                                                         │
     │ G4 — Nothing owns a run (c8, and the stall inside c4)                   │
     │                                                                         │
     │ Two worker_claim events for run-14 and no claim mechanism exists (F7).  │
     │ Both                                                                    │
     │ workers run the whole payload, both build receipts, and their           │
     │ concurrent                                                              │
     │ provider writes erase each other (F8).                                  │
     │                                                                         │
     │ - Falsified if: a second worker calling run_once on a claimed run       │
     │ declined to proceed. Nothing stops it.                                  │
     │                                                                         │
     │ G5 — Cancellation is advisory (c6)                                      │
     │                                                                         │
     │ cancel writes a status (core.py:147-152). run_once never reads it, and  │
     │ its                                                                     │
     │ final UPDATE ... status='done' (core.py:211-219) is unconditional, so a │
     │ finishing worker overwrites the cancel. RunCancelled is dead code (F6). │
     │                                                                         │
     │ - Falsified if: a cancelled run stopped writing and stayed cancelled.   │
     │ It reaches done and writes all 4 drafts (F6).                           │
     │                                                                         │
     │ G6 — Recovery has no per-run failure isolation (c9)                     │
     │                                                                         │
     │ recover() is a bare loop (F15). One raise ends the pass and every run   │
     │ behind                                                                  │
     │ it stays running forever. Matches "two campaigns behind it never went   │
     │ out."                                                                   │
     │                                                                         │
     │ Honest status: cause is visible in the code, my reproduction was        │
     │ rigged. I                                                               │
     │ made it raise with a payload I invented (missing source_sha256),        │
     │ because                                                                 │
     │ payload C is not in fixtures/ (I3). Attempting it with a                │
     │ naturally-occurring                                                     │
     │ error did not reproduce the stuck state. Treat G6 as code-visible, not  │
     │ observed.                                                               │
     │                                                                         │
     │ - Falsified if: a failing run in the recovery set left the runs behind  │
     │ it deployable. Untested against a natural trigger — this is the weakest │
     │ claim here.                                                             │
     │                                                                         │
     │ G7 — Ambiguous writes have no resolution path (c10)                     │
     │                                                                         │
     │ I disagree with calling c10 a fault. The log shows a write returning    │
     │ gateway_timeout, retryable:true and a readback finding the object. That │
     │ is the                                                                  │
     │ correct shape: an idempotent provider plus a readback resolves the      │
     │ ambiguity                                                               │
     │ deterministically. c10 is evidence for readback-based verification, not │
     │ against it.                                                             │
     │                                                                         │
     │ What it does expose: run_once has no try/except around create_draft at  │
     │ all, so a timeout would propagate and the run would die mid-payload.    │
     │ The gap is                                                              │
     │ the missing reconciliation path, not the ambiguity itself.              │
     │                                                                         │
     │ - Falsified if: a write that raised, followed by a matching readback,   │
     │ were already treated as success. There is no such path.                 │
     │                                                                         │
     │ Controls and noise                                                      │
     │                                                                         │
     │ c1 — successful crash recovery. Agreed, with a caveat. It passes        │
     │ because                                                                 │
     │ recover() reuses the same run_id, which keeps the provider key stable   │
     │ by                                                                      │
     │ accident of G2's design, not by intent. And its receipt is still an     │
     │ unchecked                                                               │
     │ constant. So c1 is a control for duplicates only, not for verification. │
     │                                                                         │
     │ c3 — legitimate, and F17 settles it. Two different idempotency keys,    │
     │ same                                                                    │
     │ payload, 8 objects. stress.py does exactly this — same payload, 12      │
     │ different                                                               │
     │ keys — and declares the expected answer to be 48 objects. The repo's    │
     │ own harness                                                             │
     │ defines different-key + same-payload as separate deployments.           │
     │                                                                         │
     │ This is a design choice, not a fact. Keying on payload_hash alone       │
     │ instead                                                                 │
     │ would merge them into 4 and make c3 a duplicate bug. I am choosing the  │
     │ reading                                                                 │
     │ stress.py encodes. Another engineer could reasonably take the other     │
     │ side, and                                                               │
     │ if they do, c3 moves into G2.                                           │
     │                                                                         │
     │ - Falsified if: an operator confirmed 207a/207b were one approval       │
     │ retried by a client that regenerated its key.                           │
     │                                                                         │
     │ c11 — out of scope. thumbnail_render exists nowhere in the repository   │
     │ (F20) and is not on the deployment path. Treated as TASK.md's "at least │
     │ one is                                                                  │
     │ unrelated" and not investigated further. Revisited only if it turns out │
     │ to touch                                                                │
     │ how a deployment is carried out or simulated.                           │
     │                                                                         │
     │ ---                                                                     │
     │ 3. Operator promise                                                     │
     │                                                                         │
     │ ▎ When this service reports a deployment as verified, it has just read  │
     │ ▎ every                                                                 │
     │ ▎ approved asset back from HubSpot and confirmed each one is present,   │
     │ ▎ is the                                                                │
     │ ▎ asset that was approved, and is a draft. If it cannot confirm that,   │
     │ ▎ it does not                                                           │
     │ ▎ say verified — it says what it could not confirm.                     │
     │ ▎                                                                       │
     │ ▎ An approved request deploys once. Submitting it again, retrying it    │
     │ ▎ from the                                                              │
     │ ▎ admin panel, or recovering it after a crash reuses the same HubSpot   │
     │ ▎ objects                                                               │
     │ ▎ rather than creating new ones. Submitting the same key with different │
     │ ▎ content                                                               │
     │ ▎ is refused, not merged.                                               │
     │ ▎                                                                       │
     │ ▎ A cancelled deployment stops writing and stays cancelled.             │
     │ ▎                                                                       │
     │ ▎ A deployment that cannot complete fails by itself and does not hold   │
     │ ▎ up the                                                                │
     │ ▎ deployments behind it.                                                │
     │                                                                         │
     │ Deliberately not promised: rollback, deletion, cleanup of anything      │
     │ already                                                                 │
     │ written, exactly-once execution, or that HubSpot still matches a second │
     │ after we                                                                │
     │ looked. The provider exposes only create_draft, read, and list_objects  │
     │ —                                                                       │
     │ there is no delete and no update, so none of those could be proved      │
     │ here.                                                                   │
     │                                                                         │
     │ ---                                                                     │
     │ 4. Definition of complete                                               │
     │                                                                         │
     │ A deployment is complete and verified only when all of the following    │
     │ hold,                                                                   │
     │ each checked against a fresh provider readback at the moment of the     │
     │ check:                                                                  │
     │                                                                         │
     │ 1. Coverage — every approved asset has exactly one corresponding        │
     │ provider draft.                                                         │
     │ 2. No omissions — no approved asset is missing from the provider.       │
     │ 3. No extras — scanning the provider for objects belonging to this      │
     │ logical deployment yields exactly the approved set, no duplicates and   │
     │ no strangers.                                                           │
     │ 4. Identity match, per object: source_asset_id, source_sha256,          │
     │ object_type, status == "draft", and display_name equal to the expected  │
     │ provider-normalized form of the approved name.                          │
     │ 5. Freshness — the verdict comes from reading the provider now, never   │
     │ from replaying a stored receipt.                                        │
     │ 6. Honesty — if any of 1–5 cannot be proved, the run is not done and    │
     │ not verified; the receipt names each specific failure.                  │
     │ 7. Empty request — an approved request with zero assets is rejected at  │
     │ submit and never becomes a run. Rationale: a deployment of nothing is   │
     │ not a deployment, and letting it through is exactly what produces       │
     │ "verified campaign, nothing in HubSpot." Stated as a rule, not a        │
     │ fixture special-case: len(assets) == 0 is refused for any payload. The  │
     │ alternative — run it and force verified: false — is defensible; I am    │
     │ choosing to fail earlier and louder.                                    │
     │ 8. Repeat safety — same key + same payload returns the existing run and │
     │ produces no additional provider objects.                                │
     │ 9. Conflict — same key + different payload raises IdempotencyConflict   │
     │ before anything is written.                                             │
     │ 10. Distinguishable names — an approved request in which two assets     │
     │ normalize to the same provider display name is rejected at submit.      │
     │                                                                         │
     │ On point 4: provider-normalized means the relay models the provider's   │
     │ documented                                                              │
     │ rule (strip, truncate to FakeHubSpot.DISPLAY_NAME_LIMIT — read from the │
     │ class,                                                                  │
     │ never a literal 40) and expects the truncated value. Truncation is the  │
     │ provider's contract, not a discrepancy.                                 │
     │                                                                         │
     │ On point 10: truncation is fine, collision is not. The operator         │
     │ approved four                                                           │
     │ assets they can tell apart; HubSpot would hold two they cannot.         │
     │ Delivering that                                                         │
     │ is delivering something other than what was approved, which is what c7  │
     │ says. The                                                               │
     │ provider exposes no delete and no rename, so there is no way to repair  │
     │ it after                                                                │
     │ the write — the only place to stop it is before. General rule, derived  │
     │ from                                                                    │
     │ DISPLAY_NAME_LIMIT, not a fixture special-case.                         │
     │                                                                         │
     │ Consequence of point 10 — this rejects the primary fixture              │
     │                                                                         │
     │ Verified: deployment_request.json has exactly one collision —           │
     │ asset-email-002                                                         │
     │ and asset-email-003 both normalize to 'Summer 2026 ABM campaign email - │
     │ product'. deployment_request_short.json and                             │
     │ deployment_request_empty.json                                           │
     │ have none.                                                              │
     │                                                                         │
     │ That fixture is used by demo.py:12, stress.py:20, and both starter      │
     │ tests                                                                   │
     │ (test_visible.py:14, :43). So under this rule all three make targets    │
     │ stop                                                                    │
     │ working unless they are reworked, and the two starter tests cannot be   │
     │ kept                                                                    │
     │ passing unchanged. TASK.md requires all three to work, and permits      │
     │ changing tests                                                          │
     │ freely. Rework plan is in §8.                                           │
     │                                                                         │
     │ ---                                                                     │
     │ 5. Planned changes                                                      │
     │                                                                         │
     │ Every change labelled exactly one of ROOT CAUSE FIX / FALSE-PASS        │
     │ PREVENTION / OBSERVABILITY ONLY / OUT OF SCOPE.                         │
     │                                                                         │
     │ #: CH1                                                                  │
     │ Change: Derive a deployment_key from idempotency_key + payload_hash;    │
     │ provider                                                                │
     │ external_key becomes deployment_key:asset_id instead of                 │
     │ run_id:asset_id.                                                        │
     │ Retries, recovery, and re-submits land on the same HubSpot objects.     │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G2 (c2, c4)                                                  │
     │ ────────────────────────────────────────                                │
     │ #: CH2                                                                  │
     │ Change: UNIQUE on idempotency_key. Same key + same payload_hash returns │
     │ the                                                                     │
     │ existing run_id; different payload_hash raises IdempotencyConflict.     │
     │ Finally                                                                 │
     │ reads the column from F16.                                              │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G2, G3 (c2, c5)                                              │
     │ ────────────────────────────────────────                                │
     │ #: CH3                                                                  │
     │ Change: Reject zero-asset payloads at submit.                           │
     │ Label: FALSE-PASS PREVENTION                                            │
     │ Addresses: F12, §4.7                                                    │
     │ ────────────────────────────────────────                                │
     │ #: CH4                                                                  │
     │ Change: Atomic claim: owner, lease_expires_at, attempt columns; claim   │
     │ via                                                                     │
     │ conditional UPDATE checked with rowcount. Verified — 20 racing threads, │
     │                                                                         │
     │ exactly 1 winner. Expired leases are reclaimable so a stalled worker    │
     │ does not                                                                │
     │  strand a run.                                                          │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G4 (c8, c4's stall)                                          │
     │ ────────────────────────────────────────                                │
     │ #: CH5                                                                  │
     │ Change: verified becomes the result of a readback comparison against    │
     │ §4.1–4.4,                                                               │
     │ never a literal. Receipt carries a discrepancies list.                  │
     │ Label: FALSE-PASS PREVENTION                                            │
     │ Addresses: G1 (c7, c8)                                                  │
     │ ────────────────────────────────────────                                │
     │ #: CH6                                                                  │
     │ Change: audit re-reads the provider instead of the receipt, returning   │
     │ the same                                                                │
     │ structure as CH5.                                                       │
     │ Label: FALSE-PASS PREVENTION                                            │
     │ Addresses: G1                                                           │
     │ ────────────────────────────────────────                                │
     │ #: CH7                                                                  │
     │ Change: cancel sets cancel_requested; run_once checks it before every   │
     │ provider                                                                │
     │ write and raises RunCancelled; the terminal status='done' update        │
     │ becomes                                                                 │
     │ conditional on ownership and non-cancellation.                          │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G5 (c6)                                                      │
     │ ────────────────────────────────────────                                │
     │ #: CH8                                                                  │
     │ Change: Per-run try/except in recover, plus a bounded attempt count so  │
     │ a                                                                       │
     │ permanently failing run stops retrying and is surfaced.                 │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G6 (c9)                                                      │
     │ ────────────────────────────────────────                                │
     │ #: CH9                                                                  │
     │ Change: Wrap create_draft in a reconcile helper outside the provider:   │
     │ on                                                                      │
     │ exception, read back the expected key; matching → success, absent →     │
     │ bounded                                                                 │
     │ retry, present-but-different → fail loudly.                             │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G7 (c10)                                                     │
     │ ────────────────────────────────────────                                │
     │ #: CH10                                                                 │
     │ Change: retry resets the existing run to pending and clears its lease   │
     │ instead                                                                 │
     │ of inserting a second row (which CH2's constraint would forbid anyway). │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: G2 (c4)                                                      │
     │ ────────────────────────────────────────                                │
     │ #: CH11                                                                 │
     │ Change: Reject at submit any approved request in which two assets       │
     │ normalize to                                                            │
     │ the same provider display name. Removes the cause of "the drafts were   │
     │ not                                                                     │
     │ what we approved" that survives an otherwise-correct deployment.        │
     │ Rejects                                                                 │
     │ deployment_request.json — see §4.10 and §8.                             │
     │ Label: ROOT CAUSE FIX                                                   │
     │ Addresses: F10 (c7's second half)                                       │
     │ ────────────────────────────────────────                                │
     │ #: CH12                                                                 │
     │ Change: SQLite timeout and WAL on _connect, so contention blocks        │
     │ briefly                                                                 │
     │ instead of raising database is locked. Coordination around storage, no  │
     │ behavior change.                                                        │
     │ Label: OBSERVABILITY ONLY                                               │
     │ Addresses: F9                                                           │
     │ ────────────────────────────────────────                                │
     │ #: CH13                                                                 │
     │ Change: The provider's lost-write and torn-read race (F8). Not fixed.   │
     │ It is an                                                                │
     │ artifact of the fake (I1), TASK.md says the provider is not ours to     │
     │ change,                                                                 │
     │ and CH5 makes it visible instead of silent. We detect loss; we do not   │
     │ prevent                                                                 │
     │  it.                                                                    │
     │ Label: OUT OF SCOPE                                                     │
     │ Addresses: F8                                                           │
     │ ────────────────────────────────────────                                │
     │ #: CH14                                                                 │
     │ Change: c11 thumbnail_render. Not in the deployment path, not in the    │
     │ repository.                                                             │
     │ Label: OUT OF SCOPE                                                     │
     │ Addresses: c11                                                          │
     │ ────────────────────────────────────────                                │
     │ #: CH15                                                                 │
     │ Change: Rollback, deletion, cleanup of duplicate drafts already in      │
     │ HubSpot. The                                                            │
     │ provider has no delete.                                                 │
     │ Label: OUT OF SCOPE                                                     │
     │ Addresses: —                                                            │
     │                                                                         │
     │ Minimum cut if time runs short, in order: CH5, CH6, CH3 (the promise    │
     │ and the                                                                 │
     │ false passes), then CH2, CH1 (duplicates), then CH7, CH4 (cancel and    │
     │ claim), then CH8,                                                       │
     │ CH9. CH10–CH12 last. If CH8/CH9 do not land, they move to DECISIONS.md  │
     │ as known-open,                                                          │
     │ which for G6 is honest anyway given it is the least-proven group.       │
     │                                                                         │
     │ ---                                                                     │
     │ 6. Acceptance tests                                                     │
     │                                                                         │
     │ Rules for every test below:                                             │
     │                                                                         │
     │ - Expectations are derived from the approved payload at runtime —       │
     │ iterate payload["assets"], compare against len(payload["assets"]).      │
     │ - No hardcoded 4, no fixture asset ids, no fixture hashes, no literal   │
     │ 40. Read the limit from FakeHubSpot.DISPLAY_NAME_LIMIT.                 │
     │ - All three shapes are exercised, but not equally — see the fixture     │
     │ note below.                                                             │
     │ - Provider failure modes are simulated with a wrapper around            │
     │ FakeHubSpot, never by editing it.                                       │
     │ - Each new test must be checked to fail against the current code before │
     │ the fix lands. A test that passes on the starter proves nothing (F13,   │
     │ F14).                                                                   │
     │                                                                         │
     │ Consequence of CH11 on fixture choice. The full fixture is refused at   │
     │ submit,                                                                 │
     │ so it can no longer be the workhorse. Under this design it is used in   │
     │ exactly one                                                             │
     │ test — the collision refusal — and the short fixture carries the rest.  │
     │ Where a                                                                 │
     │ test needs a second deployable payload, it derives one from the short   │
     │ fixture                                                                 │
     │ in-test (lengthen or alter a display_name) rather than reaching for     │
     │ another                                                                 │
     │ file. Deriving is not fixture special-casing; hardcoding the derived    │
     │ value would                                                             │
     │ be.                                                                     │
     │                                                                         │
     │ Test: Same key + same payload → one logical deployment, provider object │
     │ count                                                                   │
     │ unchanged on the second submit                                          │
     │ Shape used: short                                                       │
     │ Fails today because: 2 runs, 8 objects (F4)                             │
     │ ────────────────────────────────────────                                │
     │ Test: Same key + changed payload → IdempotencyConflict                  │
     │ Shape used: short, then short with one display_name altered in-test.    │
     │ Content                                                                 │
     │ differs while every asset_id and source_sha256 stays identical — proves │
     │ the                                                                     │
     │ check is on content, not ids (F11 shows the fixtures were built to make │
     │ that                                                                    │
     │ mistake easy)                                                           │
     │ Fails today because: both accepted (F16)                                │
     │ ────────────────────────────────────────                                │
     │ Test: retry creates no additional drafts                                │
     │ Shape used: short                                                       │
     │ Fails today because: 8 objects (F5)                                     │
     │ ────────────────────────────────────────                                │
     │ Test: Deleting a provider object makes current verification fail        │
     │ Shape used: short                                                       │
     │ Fails today because: audit never opens the provider (F2)                │
     │ ────────────────────────────────────────                                │
     │ Test: Tampering with source_asset_id / source_sha256 / object_type /    │
     │ status in                                                               │
     │ provider state makes verification fail                                  │
     │ Shape used: short                                                       │
     │ Fails today because: nothing is compared (F1)                           │
     │ ────────────────────────────────────────                                │
     │ Test: Cancellation after one write prevents later writes and cannot     │
     │ reach done                                                              │
     │ Shape used: short                                                       │
     │ Fails today because: status returns to done, all assets written (F6)    │
     │ ────────────────────────────────────────                                │
     │ Test: Two workers cannot both claim the same pending run                │
     │ Shape used: short                                                       │
     │ Fails today because: no claim exists (F7)                               │
     │ ────────────────────────────────────────                                │
     │ Test: Concurrent different runs do not lose provider objects or, where  │
     │ the                                                                     │
     │ provider does lose them, no run reports verified                        │
     │ Shape used: short, N threads                                            │
     │ Fails today because: 48 created / 36–44 held, all report done (F9)      │
     │ ────────────────────────────────────────                                │
     │ Test: One broken recovery candidate does not stop later good runs       │
     │ Shape used: short + a deliberately broken run                           │
     │ Fails today because: bare loop (F15)                                    │
     │ ────────────────────────────────────────                                │
     │ Test: Ambiguous write exception followed by a matching readback is      │
     │ reconciled                                                              │
     │ as success                                                              │
     │ Shape used: short, via a wrapper that writes then raises                │
     │ Fails today because: no try/except around create_draft (G7)             │
     │ ────────────────────────────────────────                                │
     │ Test: Empty approved request is refused at submit and never yields a    │
     │ verified                                                                │
     │ receipt                                                                 │
     │ Shape used: empty                                                       │
     │ Fails today because: returns verified: true on zero objects (F12)       │
     │ ────────────────────────────────────────                                │
     │ Test: A name past the limit still verifies — normalization is expected, │
     │ not a                                                                   │
     │ discrepancy                                                             │
     │ Shape used: short with one name lengthened past DISPLAY_NAME_LIMIT      │
     │ without                                                                 │
     │ colliding, so it survives CH11 and reaches verification                 │
     │ Fails today because: nothing compared                                   │
     │ ────────────────────────────────────────                                │
     │ Test: An unexpected object under this deployment's key prefix makes     │
     │ verification fail                                                       │
     │ Shape used: short + one injected stray object                           │
     │ Fails today because: nothing scans for extras                           │
     │ ────────────────────────────────────────                                │
     │ Test: Two approved names that normalize identically are refused at      │
     │ submit,                                                                 │
     │ before any provider write                                               │
     │ Shape used: full — the one test it is used for (F10). Assert the        │
     │ provider is                                                             │
     │ still empty afterwards                                                  │
     │ Fails today because: request deploys and reports verified               │
     │                                                                         │
     │ Both starter tests move to the short fixture and keep asserting what    │
     │ they                                                                    │
     │ asserted. They are near-worthless as written (F13, F14) and are kept    │
     │ only as                                                                 │
     │ regression ballast.                                                     │
     │                                                                         │
     │ ---                                                                     │
     │ 7. Mutation tests                                                       │
     │                                                                         │
     │ Each acceptance check is paired with one deliberate implementation      │
     │ break that                                                              │
     │ must make that check fail. If a mutation lands and the suite stays      │
     │ green, the                                                              │
     │ check is decoration and gets rewritten. Run as a manual pass — revert   │
     │ each                                                                    │
     │ mutation before applying the next.                                      │
     │                                                                         │
     │ #: M1                                                                   │
     │ Behavior intentionally broken: In submit, delete the lookup by          │
     │ idempotency_key                                                         │
     │ and unconditionally INSERT a fresh uuid4 row.                           │
     │ Check that must fail: Same key + same payload → second submit returns a │
     │                                                                         │
     │ different run id and provider object count doubles.                     │
     │ ────────────────────────────────────────                                │
     │ #: M2                                                                   │
     │ Behavior intentionally broken: In submit, keep the lookup but compare   │
     │ idempotency_key only, ignoring payload_hash — return the existing run   │
     │ instead                                                                 │
     │  of raising.                                                            │
     │ Check that must fail: Same key + changed payload → no                   │
     │ IdempotencyConflict; the                                                │
     │ new payload silently inherits the old payload's drafts.                 │
     │ ────────────────────────────────────────                                │
     │ #: M3                                                                   │
     │ Behavior intentionally broken: Restore external_key =                   │
     │ f"{run_id}:{asset_id}" in                                               │
     │ run_once, discarding the approval-derived key.                          │
     │ Check that must fail: retry produces a second full set of drafts. (Also │
     │ kills                                                                   │
     │ M1's check — acceptable; each check needs at least one killer, not      │
     │ exactly                                                                 │
     │ one.)                                                                   │
     │ ────────────────────────────────────────                                │
     │ #: M4                                                                   │
     │ Behavior intentionally broken: In the verification routine, replace the │
     │ per-key                                                                 │
     │ provider.read() with json.loads(row["receipt_json"])["objects"].        │
     │ Check that must fail: Deleting a provider object leaves verification    │
     │ reporting                                                               │
     │ present and verified.                                                   │
     │ ────────────────────────────────────────                                │
     │ #: M5                                                                   │
     │ Behavior intentionally broken: In the field comparison, assert only     │
     │ that                                                                    │
     │ object_id is truthy; drop the equality checks on source_asset_id,       │
     │ source_sha256, object_type, status.                                     │
     │ Check that must fail: Tampering with any of those four in provider      │
     │ state still                                                             │
     │ verifies.                                                               │
     │ ────────────────────────────────────────                                │
     │ #: M6a                                                                  │
     │ Behavior intentionally broken: Remove the cancel_requested read from    │
     │ the                                                                     │
     │ per-asset loop in run_once.                                             │
     │ Check that must fail: A run cancelled after the first write keeps       │
     │ writing the                                                             │
     │ remaining assets.                                                       │
     │ ────────────────────────────────────────                                │
     │ #: M6b                                                                  │
     │ Behavior intentionally broken: Make the terminal update unconditional   │
     │ again —                                                                 │
     │ drop AND status='running' AND cancel_requested=0  AND owner=?.          │
     │ Check that must fail: A cancelled run reaches done.                     │
     │ ────────────────────────────────────────                                │
     │ #: M7                                                                   │
     │ Behavior intentionally broken: In the claim, execute the conditional    │
     │ UPDATE but                                                              │
     │ ignore cursor.rowcount and proceed regardless.                          │
     │ Check that must fail: N workers racing one pending run → more than one  │
     │ proceeds                                                                │
     │ to write.                                                               │
     │ ────────────────────────────────────────                                │
     │ #: M8                                                                   │
     │ Behavior intentionally broken: Build the receipt from the dicts         │
     │ create_draft                                                            │
     │ returned, rather than from a fresh readback taken after every write     │
     │ completes.                                                              │
     │ Check that must fail: A run whose object was erased by a concurrent     │
     │ writer                                                                  │
     │ still reports verified: true — this is the exact c8 mechanism.          │
     │ ────────────────────────────────────────                                │
     │ #: M9                                                                   │
     │ Behavior intentionally broken: Remove the per-run try/except in recover │
     │ so the                                                                  │
     │ first exception escapes the loop.                                       │
     │ Check that must fail: Runs queued behind a broken recovery candidate    │
     │ stay                                                                    │
     │ undeployed.                                                             │
     │ ────────────────────────────────────────                                │
     │ #: M10                                                                  │
     │ Behavior intentionally broken: Delete the reconcile wrapper; call       │
     │ provider.create_draft directly and let exceptions propagate.            │
     │ Check that must fail: A write that raises after persisting, followed by │
     │ a                                                                       │
     │ matching readback, kills the run instead of being reconciled as         │
     │ success.                                                                │
     │ ────────────────────────────────────────                                │
     │ #: M11                                                                  │
     │ Behavior intentionally broken: Remove the zero-asset guard from submit. │
     │ Check that must fail: An empty approved request gets a run id and a     │
     │ verified:                                                               │
     │ true receipt over zero objects.                                         │
     │ ────────────────────────────────────────                                │
     │ #: M12                                                                  │
     │ Behavior intentionally broken: Set the expected display name to the raw │
     │                                                                         │
     │ approved string instead of the normalized form.                         │
     │ Check that must fail: A request whose names exceed the limit reports a  │
     │ false                                                                   │
     │ discrepancy and fails to verify. This is the one mutation  that catches │
     │                                                                         │
     │ over-strictness rather than  under-strictness — without it, a verifier  │
     │ that                                                                    │
     │ rejects everything would pass every other check here.                   │
     │ ────────────────────────────────────────                                │
     │ #: M13                                                                  │
     │ Behavior intentionally broken: Remove the normalization-collision       │
     │ precheck from                                                           │
     │ submit.                                                                 │
     │ Check that must fail: A request with two colliding names deploys, and   │
     │ the                                                                     │
     │ provider ends up holding two drafts with identical display names.       │
     │ ────────────────────────────────────────                                │
     │ #: M14                                                                  │
     │ Behavior intentionally broken: In verification, iterate approved assets │
     │ only;                                                                   │
     │ drop the reverse scan for provider objects under this deployment's key  │
     │ prefix.                                                                 │
     │ Check that must fail: A stray object under the deployment's prefix goes │
     │                                                                         │
     │ unnoticed and the run still verifies.                                   │
     │                                                                         │
     │ Test-suite integrity mutation. Separately, change                       │
     │ FakeHubSpot.DISPLAY_NAME_LIMIT and swap which fixture a test loads. Any │
     │ test                                                                    │
     │ that hardcoded 40, 4, a fixture asset id, or a fixture hash breaks in a │
     │ way                                                                     │
     │ that has nothing to do with the behavior under test. That break is the  │
     │ signal —                                                                │
     │ those tests get rewritten to derive from the payload.                   │
     │                                                                         │
     │ ---                                                                     │
     │ 8. Verification, and the make-target rework CH11 forces                 │
     │                                                                         │
     │ make demo, make test, make stress must all keep working (TASK.md). CH11 │
     │ rejects deployment_request.json, which all three use, so all three need │
     │ rework.                                                                 │
     │ This is the largest single consequence in this roadmap and the decision │
     │ most                                                                    │
     │ worth arguing about.                                                    │
     │                                                                         │
     │ make demo — the rejection becomes the opening act, not a broken step:   │
     │                                                                         │
     │ 1. Submit deployment_request.json → refused at the door, naming the two │
     │ assets that would have been indistinguishable in HubSpot. Nothing       │
     │ written. Provider still empty.                                          │
     │ 2. Submit deployment_request_empty.json → refused, nothing to deploy.   │
     │ 3. Submit deployment_request_short.json → deploys, crashes partway,     │
     │ recovers, reports verified from a live readback.                        │
     │ 4. Delete two objects from the provider's state file, exactly as        │
     │ demo.py:42-45 does today, then press "Check again" → now reports the    │
     │ loss instead of hiding it.                                              │
     │                                                                         │
     │ Step 4 against the old output is the demonstration. Steps 1–2 show the  │
     │ two                                                                     │
     │ refusals that stop a false success before it can exist.                 │
     │                                                                         │
     │ make stress — switch to deployment_request_short.json, the only fixture │
     │ that both deploys and has no collision. Counts stay derived (WORKERS *  │
     │ assets),                                                                │
     │ nothing hardcoded. It should still show the provider short of the       │
     │ expected total                                                          │
     │ (CH13 is out of scope), but no run may report verified: true while its  │
     │ objects                                                                 │
     │ are missing, and the recovery pass must not report success over a short │
     │ provider. That inversion — same short count, honest verdict — is the    │
     │ point.                                                                  │
     │                                                                         │
     │ make test — the two starter tests move to the short fixture (see §6),   │
     │ plus                                                                    │
     │ the new acceptance suite.                                               │
     │                                                                         │
     │ Risk to name in DECISIONS.md: a reviewer will notice the headline       │
     │ fixture no                                                              │
     │ longer deploys. The defense is that the fixture appears to exist        │
     │ precisely to                                                            │
     │ trigger this — it is the only one with names past the limit, and        │
     │ TASK.md says                                                            │
     │ the shapes differ on purpose. If that reading is wrong, CH11 should     │
     │ drop back to a                                                          │
     │ receipt warning and everything above reverts.                           │
     │                                                                         │
     │ 9. Out of scope                                                         │
     │                                                                         │
     │ Listed, not argued.                                                     │
     │                                                                         │
     │ - Real HubSpot integration, credentials, network calls of any kind.     │
     │ - Rollback, deletion, or cleanup of anything already written. The       │
     │ provider exposes create_draft, read, list_objects and nothing else.     │
     │ - Distributed leases across hosts. The lease here coordinates workers   │
     │ against one SQLite file and claims no more than that.                   │
     │ - Long-term retry scheduling, backoff daemons, dead-letter queues.      │
     │ - Any UI.                                                               │
     │ - Schema migration. Every entry point builds its own database.          │
     │ - The provider's internal lost-write and torn-read race (CH13, F8).     │
     │ - c11 thumbnail_render (F20).                                           │
     │ - Queue, workflow framework, container, cloud service, real model,      │
     │ OAuth.                                                                  │
     │                                                                         │
     │ Production posture                                                      │
     │                                                                         │
     │ The code should read as production code — typed, small functions, no    │
     │ bare                                                                    │
     │ except, structured failure values instead of booleans, every decision   │
     │ derived rather than asserted. It should not grow the machinery a real   │
     │ deployment would need on top of that. The lease is a WHERE clause, not  │
     │ a                                                                       │
     │ lock service. Reconciliation is a bounded readback, not a scheduler.    │
     │ The                                                                     │
     │ provider is a JSON file behind an interface we do not touch. Where a    │
     │ real                                                                    │
     │ system would need more, that belongs in §10 as a stated limit, not as a │
     │ half-built version of the real thing.                                   │
     │                                                                         │
     │ 10. What this still cannot promise                                      │
     │                                                                         │
     │ For DECISIONS.md:                                                       │
     │                                                                         │
     │ - The provider can still lose writes. We detect it; we do not prevent   │
     │ it (CH13).                                                              │
     │ - Verification is point-in-time. HubSpot can change a second after      │
     │ audit returns.                                                          │
     │ - There is still a window between a provider write and the local        │
     │ receipt. Recovery closes it by re-reading, not by making the two        │
     │ atomic.                                                                 │
     │ - A normalization collision is refused, not repaired. We do not rename  │
     │ an operator's assets — we hand the request back and say which two names │
     │ collide.                                                                │
     │ - Collision detection only covers what the relay knows the provider     │
     │ does to a name (strip + truncate). Any other normalization HubSpot      │
     │ applies is invisible to us until a readback disagrees.                  │
     │ - Nothing is ever cleaned up. Duplicate drafts already in HubSpot from  │
     │ before this change stay there — the provider has no delete.             │
     │ - G6 (c9) is fixed at a cause we reasoned about but never reproduced    │
     │ with a natural trigger.                                                 │
     ╰─────────────────────────────────────────────────────────────────────────╯

✻ Crunched for 7m 6s

❯ don't commit this. but create a markdown file for ROADMAP.md that shows the   
  planned out roadmap for now                                                   

⏺ Write(ROADMAP.md)
  ⎿  Wrote 404 lines to ROADMAP.md
       1 # ROADMAP — deployments an operator can trust
       2 
       3 Design kept small enough for the 2-hour target. `FakeHubSpot` is
       4 external provider and is not modified; all coordination is added
          around it.
       5 
       6 ---
       8 ## 1. Current evidence
       9 
      10 ### Direct facts — observed by running code, not by reading it
     … +717 lines (ctrl+o to expand)

  Ran 1 shell command (ctrl+o to expand)

⏺ ROADMAP.md created — 404 lines at
  /Users/nityanthmaramreddy/Downloads/02-durable-run-relay/ROADMAP.md.

  Nothing else changed. Every other file still carries its original Aug 2 
  12:43:41 mtime — relay/core.py, tests/test_visible.py, demo.py, stress.py,
  Makefile, all four fixtures, README.md, DECISIONS.md, SUBMISSION.md,
  .gitignore. Only ROADMAP.md is new (Aug 5 13:15:15).

  Not committed. Still not a git repo — git status returns fatal: not a git 
  repository. When you do init and commit, TASK.md wants this roadmap as its own
  commit before any source or test edit.

  Contents, ten sections:

  1. Current evidence — 20 direct facts (each with how it was observed)
  separated from 5 interpretations
  2. Current theory — G1–G7 grouped by cause, each with a falsifier; c1 and c3
  as controls, c11 out
  3. Operator promise — narrow, with rollback/deletion/exactly-once explicitly
  excluded
  4. Definition of complete — 10 conditions, including the empty-request and
  name-collision rules
  5. Planned changes — CH1–CH15, each labelled ROOT CAUSE FIX / FALSE-PASS
  PREVENTION / OBSERVABILITY ONLY / OUT OF SCOPE, with a minimum cut if time
  runs short
  6. Acceptance tests — 14, all deriving expectations from the payload, no
  hardcoded counts or fixture values
  7. Mutation tests — M1–M14, one deliberate break per check, plus a test-suite
  integrity mutation
  8. Verification — including the make-target rework CH11 forces
  9. Out of scope — listed, not argued, with a production-posture note
  10. What this still cannot promise

  Two things in there a reviewer could reasonably argue with, flagged as such in
  the document: rejecting the primary fixture at submit (§4.10, §8), and
  reading c3 as two legitimate approvals rather than a duplicate (§2).

✻ Churned for 2m 25s

※ recap: Goal: make the deploy service stop reporting success it hasn't verified against HubSpot. ROADMAP.md is written and nothing else is touched. Next: your approval to start implementing CH1–CH12, beginning with the verification changes. (disable recaps in /config)

❯ Revise ROADMAP.md only; do not implement or commit. Remove the rule that      
  rejects two assets whose display names normalize to the same value, because   
  the main fixture is intentionally shaped to show that display-name uniqueness 
  is not a valid identity rule. Keep that fixture deployable and verify         
  objects using the stable external key, source_asset_id, source_sha256, type,  
  and draft status; display_name should match the provider-normalized value,    
  but two objects may legitimately share that value. Make the operator          
  idempotency key the unit of idempotency, derive each provider external_key    
  from idempotency_key + asset_id, and use payload_hash only to reject          
  same-key/different-payload conflicts before any provider write rather than    
  including it in provider identity. Also separate historical workflow          
  status—pending, running, done, cancelled, or failed—from current provider     
  certification—complete, divergent, or unknown; a run may remain historically  
  done while a later audit reports divergent, and done alone must never be      
  presented as proof that HubSpot currently matches. Change c3 from “legitimate 
  and settled” to “ambiguous from the evidence,” while keeping the design       
  choice that different idempotency keys represent separate deployments without 
  claiming stress.py proves operator intent. Update the planned changes,        
  acceptance tests, mutation tests, demo/test rework, and remaining-limit       
  sections consistently, remove all CH11 collision-rejection work, keep         
  deployment_request.json as a successful verification case, then show only the 
  revised sections and the roadmap diff and stop.                               
  ⎿  ⧉ Selected 404 lines from ROADMAP.md in Visual Studio Code

⏺ Backing up the current file so I can produce a real diff, then revising.

  Ran 1 shell command (ctrl+o to expand)

⏺ Write(ROADMAP.md)
  ⎿  Added 165 lines, removed 134 lines
      20  | F7 | No lease, claim, owner, or worker id exists anywhere in 
          the repo. | grep |
      21  | F8 | `FakeHubSpot._load`/`_save` is an unlocked read-modify-w
          rite of one JSON file (`core.py:61`, `:78`, `:79`). 50 concurre
          nt creates: 34–44 returned success, provider held 2–6, the rest
           raised `JSONDecodeError` from reading a half-written file. | p
          robe, 3 trials |
      22  | F9 | `make stress`: 48 objects created, provider holds 36–44,
           varies every run. 1–3 runs left `running`, then the recovery p
          ass flips all 12 to `done`. | 4 runs of `make stress` |
      23 -| F10 | `_store_display_name` (`core.py:52-53`) strips and trun
         -cates to 40 chars. On `deployment_request.json`, 4 approved nam
         -es become 3 distinct stored names — `asset-email-002` and `asse
         -t-email-003` are indistinguishable in HubSpot. | probe |       
      23 +| F10 | `_store_display_name` (`core.py:52-53`) strips and trun
         +cates to 40 chars. On `deployment_request.json`, 4 approved nam
         +es become 3 distinct stored names — `asset-email-002` and `asse
         +t-email-003` normalize to the same value. | probe |            
      24  | F11 | `deployment_request.json` and `deployment_request_short
          .json` share **every** `asset_id` and **every** `source_sha256`
          . Only `display_name` differs. | probe |
      25  | F12 | `deployment_request_empty.json` (0 assets) returns `obj
          ects_deployed: 0, verified: true, all_present: true`. `all_pres
          ent` is `all([])`. | probe |
      26  | F13 | `test_every_deployed_object_matches_its_source_asset` c
          annot fail. Fed `source_sha256="THIS-IS-NOT-A-REAL-SHA-AT-ALL"`
          ; the provider echoed it verbatim (`core.py:67`) and the assert
           passed. | probe |
     ...
      37  | # | Interpretation | Why it is not a fact |
      38  |---|---|---|
      39  | I1 | F8 is an artifact of the fake being a JSON file; real Hu
          bSpot does not lose writes. | We have no real HubSpot to check 
          against. |
      40 -| I2 | `deploy-207a` / `deploy-207b` (c3) are two legitimate ap
         -provals, not one duplicated. | The keys differ; whether that re
         -flects operator intent is unknowable from the log. F17 supports
         - it strongly. |                                                
      40 +| I2 | `deploy-207a` / `deploy-207b` (c3) are two separate depl
         +oyments rather than one duplicated approval. | The keys differ,
         + and nothing in the log says whether that reflects operator int
         +ent or a client that regenerated its key. Genuinely ambiguous —
         + see §2. |                                                     
      41  | I3 | c9's `payload_version: "C"` is a request shape the code 
          cannot handle. | Payload C is not in `fixtures/`. Its content i
          s unknown. |
      42  | I4 | c11 is out of scope. | Based on absence of evidence (F20
          ), which is weaker than presence. |
      43  | I5 | The empty-request case (F12) is the same defect as the p
          artial-loss case. | The empty fixture appears nowhere in the ev
          ent log; the tie to the operator report is via "keeps disagreei
          ng with what the service says it did," which is a broad reading
          . Weakest tie in this document. |
      44 +| I6 | F10 is deliberate fixture design: `deployment_request.js
         +on` is shaped so two approved assets normalize to the same stor
         +ed `display_name`, demonstrating that display-name uniqueness i
         +s **not** a valid identity rule. | Intent is inferred. But the 
         +reading is load-bearing here — it is why identity is keyed on `
         +idempotency_key` + `asset_id` and never on a name. |           
      45  
      46  ---
      47  
     ...
      137  accident of G2's design, not by intent. And its receipt is sti
           ll an unchecked
      138  constant. So c1 is a control for **duplicates only**, not for 
           verification.
      139  
      139 -**c3 — legitimate, and F17 settles it.** Two different idempot
          -ency keys, same                                               
      140 -payload, 8 objects. `stress.py` does exactly this — same paylo
          -ad, 12 different                                              
      141 -keys — and declares the expected answer to be 48 objects. The 
          -repo's own harness                                            
      142 -defines different-key + same-payload as separate deployments. 
      140 +**c3 — ambiguous from the evidence.** Two different idempotenc
          +y keys                                                        
      141 +(`deploy-207a`, `deploy-207b`), the same payload version, 8 pr
          +ovider objects.                                               
      142 +Two readings fit the log equally well:                        
      143  
      144 -This is a **design choice, not a fact.** Keying on `payload_ha
          -sh` alone instead                                             
      145 -would merge them into 4 and make c3 a duplicate bug. This road
          -map chooses the                                               
      146 -reading `stress.py` encodes. Another engineer could reasonably
          - take the other                                               
      147 -side, and if they do, c3 moves into G2.                       
      144 +- Two genuinely separate approvals of the same content. 8 obje
          +cts is correct.                                               
      145 +- One approval submitted twice by a client that regenerated it
          +s key. 8 objects is the duplication the operator is complainin
          +g about.                                                      
      146  
      149 -- **Falsified if:** an operator confirmed `207a`/`207b` were o
          -ne approval retried by a client that regenerated its key.     
      147 +**Nothing in the evidence distinguishes them.** The keys diffe
          +r, and the log                                                
      148 +records no operator intent. `stress.py` submits the same paylo
          +ad under 12                                                   
      149 +different keys and expects 48 objects (F17), but that is the *
          +starter's*                                                    
      150 +expectation encoded in a test harness — it is not evidence abo
          +ut what an                                                    
      151 +operator meant, and it should not be cited as though it were. 
      152  
      153 +**Design choice, made in spite of the ambiguity:** the operato
          +r's idempotency key                                           
      154 +is the unit of idempotency. Different keys mean different depl
          +oyments. This is                                              
      155 +chosen because the key is the only stable identifier the opera
          +tor controls, and                                             
      156 +because merging on payload content would make two intentional 
          +deployments of the                                            
      157 +same assets impossible to express. It does **not** resolve c3 
          +— it decides how                                              
      158 +the system will behave when c3's shape recurs.                
      159 +                                                              
      160 +- **What would resolve it:** an operator confirming whether `2
          +07a`/`207b` were one approval or two. Until then c3 stays ambi
          +guous and is not counted as a fixed fault.                    
      161 +                                                              
      162  **c11 — out of scope.** `thumbnail_render` exists nowhere in t
           he repository
      163  (F20) and is not on the deployment path. Treated as TASK.md's 
           "at least one is
      164  unrelated" and not investigated further. Revisited only if it 
           turns out to touch
     ...
      168  
      169  ## 3. Operator promise
      170  
      160 -> **When this service reports a deployment as `verified`, it h
          -as just read every                                            
      161 -> approved asset back from HubSpot and confirmed each one is p
          -resent, is the                                                
      162 -> asset that was approved, and is a draft. If it cannot confir
          -m that, it does not                                           
      163 -> say verified — it says what it could not confirm.**         
      171 +> **A deployment reported as `complete` has just been read bac
          +k from HubSpot,                                               
      172 +> object by object, and every approved asset was found, matchi
          +ng what was                                                   
      173 +> approved, as a draft. If we could not confirm that, we say `
          +divergent` and name                                           
      174 +> what differs. If we could not read HubSpot at all, we say `u
          +nknown` and do not                                            
      175 +> guess.**                                                    
      176  >
      165 -> An approved request deploys once. Submitting it again, retry
          -ing it from the                                               
      166 -> admin panel, or recovering it after a crash reuses the same 
          -HubSpot objects                                               
      167 -> rather than creating new ones. Submitting the same key with 
          -different content                                             
      168 -> is refused, not merged.                                     
      177 +> **A run finishing is history, not proof.** `done` means the 
          +run completed its                                             
      178 +> work at the time it ran. It is never presented on its own as
          + evidence that                                                
      179 +> HubSpot matches now — that question is answered only by a fr
          +esh check.                                                    
      180  >
      181 +> An approved request deploys once per idempotency key. Submit
          +ting the same key                                             
      182 +> again, retrying it from the admin panel, or recovering it af
          +ter a crash reuses                                            
      183 +> the same HubSpot objects rather than creating new ones. Subm
          +itting the same key                                           
      184 +> with different content is refused before anything is written
          +.                                                             
      185 +>                                                             
      186  > A cancelled deployment stops writing and stays cancelled.
      187  >
      188  > A deployment that cannot complete fails by itself and does n
           ot hold up the
     ...
      197  
      198  ## 4. Definition of complete
      199  
      184 -A deployment is **complete and verified** only when all of the
          - following hold,                                              
      185 -each checked against a fresh provider readback at the moment o
          -f the check:                                                  
      200 +### Two independent axes                                      
      201  
      202 +The starter conflates "did the run finish" with "is HubSpot ri
          +ght." These are                                               
      203 +separate questions with separate lifetimes, and the operator r
          +eport is largely a                                            
      204 +symptom of merging them. They are split:                      
      205 +                                                              
      206 +| Axis | Values | Lifetime |                                  
      207 +|---|---|---|                                                 
      208 +| **Workflow status** — what the run did | `pending`, `running
          +`, `done`, `cancelled`, `failed` | Historical. Append-only in 
          +meaning: once a run is `done` it stays `done`. |              
      209 +| **Provider certification** — what HubSpot holds *now* | `com
          +plete`, `divergent`, `unknown` | Point-in-time. Recomputed on 
          +every check; the previous value is never reused. |            
      210 +                                                              
      211 +`done` + `divergent` is a legal and expected combination: the 
          +run did its job,                                              
      212 +and something changed in HubSpot afterwards. That pair is exac
          +tly what the                                                  
      213 +operator saw and could not express. Certification is never wri
          +tten back over                                                
      214 +history, and history is never presented as certification.     
      215 +                                                              
      216 +`unknown` is deliberately distinct from `divergent`. If the pr
          +ovider cannot be                                              
      217 +read — a torn read, an exception, a half-written state file (F
          +8) — the answer is                                            
      218 +"we do not know," not "it is wrong." Collapsing the two would 
          +make a transient                                              
      219 +read failure look like data loss.                             
      220 +                                                              
      221 +### Certification is `complete` only when all of these hold, f
          +rom a fresh readback                                          
      222 +                                                              
      223  1. **Coverage** — every approved asset has exactly one corresp
           onding provider draft.
      188 -2. **No omissions** — no approved asset is missing from the pr
          -ovider.                                                       
      189 -3. **No extras** — scanning the provider for objects belonging
          - to this logical deployment yields exactly the approved set, n
          -o duplicates and no strangers.                                
      190 -4. **Identity match**, per object: `source_asset_id`, `source_
          -sha256`, `object_type`, `status == "draft"`, and `display_name
          -` equal to the **expected provider-normalized** form of the ap
          -proved name.                                                  
      191 -5. **Freshness** — the verdict comes from reading the provider
          - now, never from replaying a stored receipt.                  
      192 -6. **Honesty** — if any of 1–5 cannot be proved, the run is no
          -t `done` and not `verified`; the receipt names each specific f
          -ailure.                                                       
      193 -7. **Empty request** — an approved request with zero assets is
          - **rejected at `submit`** and never becomes a run. A deploymen
          -t of nothing is not a deployment, and letting it through is ex
          -actly what produces "verified campaign, nothing in HubSpot." S
          -tated as a rule, not a fixture special-case: `len(assets) == 0
          -` is refused for any payload. The alternative — run it and for
          -ce `verified: false` — is defensible; this roadmap chooses to 
          -fail earlier and louder.                                      
      194 -8. **Repeat safety** — same key + same payload returns the exi
          -sting run and produces no additional provider objects.        
      195 -9. **Conflict** — same key + different payload raises `Idempot
          -encyConflict` before anything is written.                     
      196 -10. **Distinguishable names** — an approved request in which t
          -wo assets normalize to the same provider display name is **rej
          -ected at `submit`**.                                          
      224 +2. **No omissions** — no approved asset is missing.           
      225 +3. **No extras** — scanning the provider for objects under thi
          +s deployment's key namespace yields exactly the approved set, 
          +no duplicates and no strangers.                               
      226 +4. **Identity match**, per object, on the fields that actually
          + identify it: `external_key`, `source_asset_id`, `source_sha25
          +6`, `object_type`, and `status == "draft"`.                   
      227 +5. **Name match, not name uniqueness** — `display_name` equals
          + the **provider-normalized** form of the approved name. Two ob
          +jects in the same deployment may legitimately carry the same n
          +ormalized value; that is not a discrepancy and never fails cer
          +tification. See below.                                        
      228 +6. **Freshness** — the verdict comes from reading the provider
          + now, never from replaying a stored receipt or a stored certif
          +ication.                                                      
      229 +7. **Honesty** — if 1–5 cannot be proved, certification is `di
          +vergent` and the specific failures are named. If the provider 
          +cannot be read, certification is `unknown`.                   
      230  
      198 -On point 4: provider-normalized means the relay models the pro
          -vider's documented                                            
      199 -rule (strip, truncate to `FakeHubSpot.DISPLAY_NAME_LIMIT` — re
          -ad from the class,                                            
      200 -never a literal `40`) and expects the truncated value. Truncat
          -ion is the                                                    
      201 -provider's contract, not a discrepancy.                       
      231 +### Rules at the door and at completion                       
      232  
      203 -On point 10: truncation is fine, **collision is not**. The ope
          -rator approved four                                           
      204 -assets they can tell apart; HubSpot would hold two they cannot
          -. Delivering that                                             
      205 -is delivering something other than what was approved, which is
          - what c7 says. The                                            
      206 -provider exposes no delete and no rename, so there is no way t
          -o repair it after                                             
      207 -the write — the only place to stop it is before. General rule,
          - derived from                                                 
      208 -`DISPLAY_NAME_LIMIT`, not a fixture special-case.             
      233 +8. **`done` requires `complete` at completion time.** A run th
          +at finishes its writes but cannot certify `complete` ends `fai
          +led`, not `done`. Later divergence does not retroactively chan
          +ge it.                                                        
      234 +9. **`done` is not a claim about now.** No operator-facing sur
          +face may present workflow status alone as evidence about HubSp
          +ot's current contents. Every surface that reports status also 
          +reports a certification and when it was computed.             
      235 +10. **Empty request** — an approved request with zero assets i
          +s **rejected at `submit`** and never becomes a run. A deployme
          +nt of nothing is not a deployment, and letting it through is e
          +xactly what produces "verified campaign, nothing in HubSpot." 
          +Stated as a rule, not a fixture special-case: `len(assets) == 
          +0` is refused for any payload. The alternative — run it and ce
          +rtify `divergent` — is defensible; this roadmap chooses to fai
          +l earlier and louder.                                         
      236 +11. **Repeat safety** — same key + same payload returns the ex
          +isting run and produces no additional provider objects.       
      237 +12. **Conflict** — same key + different `payload_hash` raises 
          +`IdempotencyConflict` at `submit`, **before any provider write
          +**.                                                           
      238  
      210 -### Consequence of point 10 — this rejects the primary fixture
      239 +### Identity: what names a HubSpot object                     
      240  
      212 -Verified: `deployment_request.json` has exactly one collision 
          -— `asset-email-002`                                           
      213 -and `asset-email-003` both normalize to `'Summer 2026 ABM camp
          -aign email -                                                  
      214 -product'`. `deployment_request_short.json` and `deployment_req
          -uest_empty.json`                                              
      215 -have none.                                                    
      241 +`external_key = idempotency_key + ":" + asset_id`             
      242  
      217 -That fixture is used by `demo.py:12`, `stress.py:20`, and both
          - starter tests                                                
      218 -(`test_visible.py:14`, `:43`). So under this rule **all three 
          -make targets stop                                             
      219 -working unless they are reworked**, and the two starter tests 
          -cannot be kept                                                
      220 -passing unchanged. TASK.md requires all three to work, and per
          -mits changing tests                                           
      221 -freely. Rework plan is in §8.                                 
      243 +That is the whole rule. Two properties follow, and both matter
          +:                                                             
      244  
      245 +- **Stable across runs.** The key contains nothing that varies
          + per run — no `run_id`, no UUID, no timestamp. A retry, a reco
          +very pass, and a re-submitted approval all compute the same ke
          +y and land on the same object. This is what removes G2.       
      246 +- **`payload_hash` is deliberately excluded.** It guards the d
          +oor (rule 12) and does not name the object. If it were part of
          + the key, identity would drift with any change in how a payloa
          +d is serialized — a reordered key, a whitespace change — and t
          +he same approval would silently acquire a second set of drafts
          +. Conflicting content is refused before a write, so it never n
          +eeds to be encoded in the key afterwards.                     
      247 +                                                              
      248 +**`display_name` is not identity.** It is compared as a field 
          +(rule 5) and never                                            
      249 +used to distinguish objects, deduplicate them, or reject a req
          +uest.                                                         
      250 +`deployment_request.json` is shaped to prove the point: `asset
          +-email-002` and                                               
      251 +`asset-email-003` both normalize to `'Summer 2026 ABM campaign
          + email - product'`                                            
      252 +(F10, I6). Those are two distinct, correctly deployed drafts t
          +hat happen to share                                           
      253 +a label. A design that treated a shared name as a collision wo
          +uld reject a valid                                            
      254 +approval — so this roadmap **does not** add any such rule, and
      255 +`deployment_request.json` remains a fully deployable, successf
          +ully certifying                                               
      256 +case.                                                         
      257 +                                                              
      258  ---
      259  
      260  ## 5. Planned changes
     ...
      267  
      268  | # | Change | Label | Addresses |
      269  |---|---|---|---|
      235 -| CH1 | Derive a `deployment_key` from `idempotency_key` + `pa
          -yload_hash`; provider `external_key` becomes `deployment_key:a
          -sset_id` instead of `run_id:asset_id`. Retries, recovery, and 
          -re-submits land on the same HubSpot objects. | **ROOT CAUSE FI
          -X** | G2 (c2, c4) |                                           
      236 -| CH2 | `UNIQUE` on `idempotency_key`. Same key + same `payloa
          -d_hash` returns the existing `run_id`; different `payload_hash
          -` raises `IdempotencyConflict`. Finally reads the column from 
          -F16. | **ROOT CAUSE FIX** | G2, G3 (c2, c5) |                 
      237 -| CH3 | Reject zero-asset payloads at `submit`. | **FALSE-PASS
          - PREVENTION** | F12, §4.7 |                                   
      270 +| CH1 | Provider `external_key` becomes `idempotency_key:asset
          +_id` instead of `run_id:asset_id`. Retries, recovery, and re-s
          +ubmits land on the same HubSpot objects. `payload_hash` is not
          + part of the key (§4, Identity). | **ROOT CAUSE FIX** | G2 (c2
          +, c4) |                                                       
      271 +| CH2 | `UNIQUE` on `idempotency_key`. Same key + same `payloa
          +d_hash` returns the existing `run_id`; different `payload_hash
          +` raises `IdempotencyConflict` before any provider write. Fina
          +lly reads the column from F16. | **ROOT CAUSE FIX** | G2, G3 (
          +c2, c5) |                                                     
      272 +| CH3 | Reject zero-asset payloads at `submit`. | **FALSE-PASS
          + PREVENTION** | F12, §4.10 |                                  
      273  | CH4 | Atomic claim: `owner`, `lease_expires_at`, `attempt` c
           olumns; claim via conditional `UPDATE` checked with `rowcount`
           . Verified — 20 racing threads, exactly 1 winner. Expired leas
           es are reclaimable so a stalled worker does not strand a run. 
           | **ROOT CAUSE FIX** | G4 (c8, c4's stall) |
      239 -| CH5 | `verified` becomes the result of a readback comparison
          - against §4.1–4.4, never a literal. Receipt carries a `discrep
          -ancies` list. | **FALSE-PASS PREVENTION** | G1 (c7, c8) |     
      240 -| CH6 | `audit` re-reads the provider instead of the receipt, 
          -returning the same structure as CH5. | **FALSE-PASS PREVENTION
          -** | G1 |                                                     
      241 -| CH7 | `cancel` sets `cancel_requested`; `run_once` checks it
          - before every provider write and raises `RunCancelled`; the te
          -rminal `status='done'` update becomes conditional on ownership
          - and non-cancellation. | **ROOT CAUSE FIX** | G5 (c6) |       
      242 -| CH8 | Per-run `try/except` in `recover`, plus a bounded `att
          -empt` count so a permanently failing run stops retrying and is
          - surfaced. | **ROOT CAUSE FIX** | G6 (c9) |                   
      243 -| CH9 | Wrap `create_draft` in a reconcile helper **outside** 
          -the provider: on exception, read back the expected key; matchi
          -ng → success, absent → bounded retry, present-but-different → 
          -fail loudly. | **ROOT CAUSE FIX** | G7 (c10) |                
      244 -| CH10 | `retry` resets the existing run to `pending` and clea
          -rs its lease instead of inserting a second row (which CH2's co
          -nstraint would forbid anyway). | **ROOT CAUSE FIX** | G2 (c4) 
          -|                                                             
      245 -| CH11 | Reject at `submit` any approved request in which two 
          -assets normalize to the same provider display name. Removes th
          -e cause of "the drafts were not what we approved" that survive
          -s an otherwise-correct deployment. Rejects `deployment_request
          -.json` — see §4.10 and §8. | **ROOT CAUSE FIX** | F10 (c7's se
          -cond half) |                                                  
      274 +| CH5 | Replace the `verified` boolean with a computed **certi
          +fication** (`complete` / `divergent` / `unknown`) derived from
          + a fresh readback against §4.1–4.5, plus `certified_at` and a 
          +`discrepancies` list. Never a literal. | **FALSE-PASS PREVENTI
          +ON** | G1 (c7, c8) |                                          
      275 +| CH6 | Split workflow status from certification (§4). `status
          +` keeps `pending`/`running`/`done`/`cancelled`/`failed` and is
          + historical; certification is recomputed on demand and never s
          +tored as truth. A run ends `done` only if it certified `comple
          +te` at completion; otherwise `failed`. | **ROOT CAUSE FIX** | 
          +G1, and the operator's "I have no way to know what this servic
          +e promises" |                                                 
      276 +| CH7 | `audit` re-reads the provider and recomputes certifica
          +tion, returning it alongside the historical status and the tim
          +e of the check. `deployment_summary` likewise never presents s
          +tatus alone. | **FALSE-PASS PREVENTION** | G1, §4.9 |         
      277 +| CH8 | `cancel` sets `cancel_requested`; `run_once` checks it
          + before every provider write and raises `RunCancelled`; the te
          +rminal status update becomes conditional on ownership and non-
          +cancellation. | **ROOT CAUSE FIX** | G5 (c6) |                
      278 +| CH9 | Per-run `try/except` in `recover`, plus a bounded `att
          +empt` count so a permanently failing run ends `failed` and is 
          +surfaced rather than retried forever. | **ROOT CAUSE FIX** | G
          +6 (c9) |                                                      
      279 +| CH10 | Wrap `create_draft` in a reconcile helper **outside**
          + the provider: on exception, read back the expected key; match
          +ing → success, absent → bounded retry, present-but-different →
          + fail loudly. | **ROOT CAUSE FIX** | G7 (c10) |               
      280 +| CH11 | `retry` resets the existing run to `pending` and clea
          +rs its lease instead of inserting a second row (which CH2's co
          +nstraint would forbid anyway). | **ROOT CAUSE FIX** | G2 (c4) 
          +|                                                             
      281  | CH12 | SQLite `timeout` and WAL on `_connect`, so contention
            blocks briefly instead of raising `database is locked`. Coord
           ination around storage, no behavior change. | **OBSERVABILITY 
           ONLY** | F9 |
      247 -| CH13 | The provider's lost-write and torn-read race (F8). **
          -Not fixed.** It is an artifact of the fake (I1), TASK.md says 
          -the provider is not ours to change, and CH5 makes it visible i
          -nstead of silent. We detect loss; we do not prevent it. | **OU
          -T OF SCOPE** | F8 |                                           
      282 +| CH13 | The provider's lost-write and torn-read race (F8). **
          +Not fixed.** It is an artifact of the fake (I1), TASK.md says 
          +the provider is not ours to change, and CH5 makes it visible i
          +nstead of silent — as `divergent` when a write was lost, `unkn
          +own` when the state file cannot be parsed. | **OUT OF SCOPE** 
          +| F8 |                                                        
      283  | CH14 | c11 `thumbnail_render`. Not in the deployment path, n
           ot in the repository. | **OUT OF SCOPE** | c11 |
      284  | CH15 | Rollback, deletion, cleanup of duplicate drafts alrea
           dy in HubSpot. The provider has no delete. | **OUT OF SCOPE** 
           | — |
      285 +| CH16 | Any rule keyed on `display_name` — uniqueness checks,
          + collision rejection, name-based dedupe. **Explicitly not buil
          +t.** `deployment_request.json` proves a shared normalized name
          + is legitimate (§4, Identity; F10, I6). | **OUT OF SCOPE** | F
          +10 |                                                          
      286  
      251 -**Minimum cut if time runs short**, in order: CH5, CH6, CH3 (t
          -he promise and the                                            
      252 -false passes), then CH2, CH1 (duplicates), then CH7, CH4 (canc
          -el and claim), then                                           
      253 -CH8, CH9. CH10–CH12 last. If CH8/CH9 do not land, they move to
          - `DECISIONS.md` as                                            
      254 -known-open, which for G6 is honest anyway given it is the leas
          -t-proven group.                                               
      287 +**Minimum cut if time runs short**, in order: CH5, CH6, CH7, C
          +H3 (the promise and                                           
      288 +the false passes), then CH2, CH1 (duplicates), then CH8, CH4 (
          +cancel and claim),                                            
      289 +then CH9, CH10. CH11–CH12 last. If CH9/CH10 do not land, they 
          +move to                                                       
      290 +`DECISIONS.md` as known-open, which for G6 is honest anyway gi
          +ven it is the                                                 
      291 +least-proven group.                                           
      292  
      293  ---
      294  
     ...
      298  
      299  - Expectations are **derived from the approved payload at runt
           ime** — iterate `payload["assets"]`, compare against `len(payl
           oad["assets"])`.
      300  - **No hardcoded `4`**, no fixture asset ids, no fixture hashe
           s, no literal `40`. Read the limit from `FakeHubSpot.DISPLAY_N
           AME_LIMIT`.
      264 -- All three shapes are exercised, but not equally — see the fi
          -xture note below.                                             
      301 +- All three shapes are exercised. `deployment_request.json` is
          + the workhorse — it deploys and certifies `complete`, and it i
          +s the only fixture that exercises both name truncation and a l
          +egitimately shared normalized name.                           
      302 +- Where a test needs a second, different payload under the sam
          +e key, it **derives** one in-test from the loaded fixture rath
          +er than reaching for another file. Deriving is not fixture spe
          +cial-casing; hardcoding the derived value would be.           
      303  - Provider failure modes are simulated with a **wrapper around
           ** `FakeHubSpot`, never by editing it.
      304  - Each new test must be checked to **fail against the current 
           code** before the fix lands. A test that passes on the starter
            proves nothing (F13, F14).
      305  
      268 -**Consequence of CH11 on fixture choice.** The full fixture is
          - refused at submit,                                           
      269 -so it can no longer be the workhorse. Under this design it is 
          -used in exactly one                                           
      270 -test — the collision refusal — and the short fixture carries t
          -he rest. Where a                                              
      271 -test needs a second deployable payload, it **derives** one fro
          -m the short fixture                                           
      272 -in-test (lengthen or alter a `display_name`) rather than reach
          -ing for another                                               
      273 -file. Deriving is not fixture special-casing; hardcoding the d
          -erived value would                                            
      274 -be.                                                           
      275 -                                                              
      306  | Test | Shape used | Fails today because |
      307  |---|---|---|
      278 -| Same key + same payload → one logical deployment, provider o
          -bject count unchanged on the second submit | short | 2 runs, 8
          - objects (F4) |                                               
      279 -| Same key + changed payload → `IdempotencyConflict` | short, 
          -then short with one `display_name` altered in-test. Content di
          -ffers while every `asset_id` and `source_sha256` stays identic
          -al — proves the check is on content, not ids (F11 shows the fi
          -xtures were built to make that mistake easy) | both accepted (
          -F16) |                                                        
      280 -| `retry` creates no additional drafts | short | 8 objects (F5
          -) |                                                           
      281 -| Deleting a provider object makes current verification fail |
          - short | `audit` never opens the provider (F2) |              
      282 -| Tampering with `source_asset_id` / `source_sha256` / `object
          -_type` / `status` in provider state makes verification fail | 
          -short | nothing is compared (F1) |                            
      283 -| Cancellation after one write prevents later writes and canno
          -t reach `done` | short | status returns to `done`, all assets 
          -written (F6) |                                                
      284 -| Two workers cannot both claim the same pending run | short |
          - no claim exists (F7) |                                       
      285 -| Concurrent different runs do not lose provider objects **or*
          -*, where the provider does lose them, no run reports verified 
          -| short, N threads | 48 created / 36–44 held, all report done 
          -(F9) |                                                        
      286 -| One broken recovery candidate does not stop later good runs 
          -| short + a deliberately broken run | bare loop (F15) |       
      287 -| Ambiguous write exception followed by a matching readback is
          - reconciled as success | short, via a wrapper that writes then
          - raises | no `try/except` around `create_draft` (G7) |        
      288 -| Empty approved request is refused at submit and never yields
          - a verified receipt | empty | returns `verified: true` on zero
          - objects (F12) |                                              
      289 -| A name past the limit still verifies — normalization is expe
          -cted, not a discrepancy | short with one name lengthened past 
          -`DISPLAY_NAME_LIMIT` **without** colliding, so it survives CH1
          -1 and reaches verification | nothing compared |               
      290 -| An unexpected object under this deployment's key prefix make
          -s verification fail | short + one injected stray object | noth
          -ing scans for extras |                                        
      291 -| Two approved names that normalize identically are refused at
          - submit, before any provider write | **full** — the one test i
          -t is used for (F10). Assert the provider is still empty afterw
          -ards | request deploys and reports verified |                 
      308 +| Same key + same payload → one logical deployment; the second
          + submit returns the same run id and adds no provider objects |
          + full | 2 runs, 8 objects (F4) |                              
      309 +| Same key + changed payload → `IdempotencyConflict`, **and th
          +e provider is untouched** — assert object count is unchanged a
          +fter the raise | full, then full with one field altered in-tes
          +t | both accepted, both would write (F16) |                   
      310 +| `retry` creates no additional drafts | full | 8 objects (F5)
          + |                                                            
      311 +| `external_key` is stable across runs — the key computed for 
          +an asset is identical before and after a retry and a recovery,
          + and contains no `run_id` | full | key embeds a fresh UUID (F3
          +) |                                                           
      312 +| Two approved assets whose names normalize to the same value 
          +both deploy and certify `complete` | **full** — `asset-email-0
          +02` and `asset-email-003` collide by name (F10) and must both 
          +succeed | (passes today by accident; kept as a guard against e
          +ver adding a name-uniqueness rule) |                          
      313 +| A `display_name` past the limit certifies `complete` — norma
          +lization is expected, not a discrepancy | full (three names ex
          +ceed the limit) | nothing compared |                          
      314 +| Deleting a provider object makes a fresh check certify `dive
          +rgent` | full | `audit` never opens the provider (F2) |       
      315 +| Tampering with `source_asset_id` / `source_sha256` / `object
          +_type` / `status` in provider state certifies `divergent` | fu
          +ll | nothing is compared (F1) |                               
      316 +| An unexpected object under this deployment's key namespace c
          +ertifies `divergent` | full + one injected stray object | noth
          +ing scans for extras |                                        
      317 +| An unreadable provider certifies `unknown`, not `divergent` 
          +and not `complete` | full, via a wrapper that raises on read |
          + no such distinction exists |                                 
      318 +| **Status and certification are independent** — after a succe
          +ssful run, delete an object; workflow status stays `done`, a f
          +resh check certifies `divergent`, and no surface reports `done
          +` without a certification | full | `done` and `verified` are w
          +elded together (F2) |                                         
      319 +| A run that finishes writing but cannot certify `complete` en
          +ds `failed`, not `done` | full, via a wrapper that drops one w
          +rite | every run ends `done` (F9) |                           
      320 +| Cancellation after one write prevents later writes and canno
          +t reach `done` | full | status returns to `done`, all assets w
          +ritten (F6) |                                                 
      321 +| Two workers cannot both claim the same pending run | full | 
          +no claim exists (F7) |                                        
      322 +| Concurrent different runs do not lose provider objects **or*
          +*, where the provider does lose them, no run certifies `comple
          +te` | full, N threads | 48 created / 36–44 held, all report do
          +ne (F9) |                                                     
      323 +| One broken recovery candidate does not stop later good runs 
          +| full + a deliberately broken run | bare loop (F15) |        
      324 +| Ambiguous write exception followed by a matching readback is
          + reconciled as success | full, via a wrapper that writes then 
          +raises | no `try/except` around `create_draft` (G7) |         
      325 +| Empty approved request is refused at submit and never yields
          + a certification | empty | returns `verified: true` on zero ob
          +jects (F12) |                                                 
      326  
      293 -Both starter tests move to the short fixture and keep assertin
          -g what they                                                   
      327 +Both starter tests stay on `deployment_request.json` and keep 
          +asserting what they                                           
      328  asserted. They are near-worthless as written (F13, F14) and ar
           e kept only as
      329  regression ballast.
      330  
     ...
      341  |---|---|---|
      342  | M1 | In `submit`, delete the lookup by `idempotency_key` and
            unconditionally `INSERT` a fresh `uuid4` row. | Same key + sa
           me payload → second submit returns a *different* run id and pr
           ovider object count doubles. |
      343  | M2 | In `submit`, keep the lookup but compare `idempotency_k
           ey` only, ignoring `payload_hash` — return the existing run in
           stead of raising. | Same key + changed payload → no `Idempoten
           cyConflict`; the new payload silently inherits the old payload
           's drafts. |
      310 -| M3 | Restore `external_key = f"{run_id}:{asset_id}"` in `run
          -_once`, discarding the approval-derived key. | `retry` produce
          -s a second full set of drafts. (Also kills M1's check — accept
          -able; each check needs at least one killer, not exactly one.) 
          -|                                                             
      311 -| M4 | In the verification routine, replace the per-key `provi
          -der.read()` with `json.loads(row["receipt_json"])["objects"]`.
          - | Deleting a provider object leaves verification reporting pr
          -esent and verified. |                                         
      312 -| M5 | In the field comparison, assert only that `object_id` i
          -s truthy; drop the equality checks on `source_asset_id`, `sour
          -ce_sha256`, `object_type`, `status`. | Tampering with any of t
          -hose four in provider state still verifies. |                 
      313 -| M6a | Remove the `cancel_requested` read from the per-asset 
          -loop in `run_once`. | A run cancelled after the first write ke
          -eps writing the remaining assets. |                           
      314 -| M6b | Make the terminal update unconditional again — drop `A
          -ND status='running' AND cancel_requested=0 AND owner=?`. | A c
          -ancelled run reaches `done`. |                                
      315 -| M7 | In the claim, execute the conditional `UPDATE` but igno
          -re `cursor.rowcount` and proceed regardless. | N workers racin
          -g one pending run → more than one proceeds to write. |        
      316 -| M8 | Build the receipt from the dicts `create_draft` returne
          -d, rather than from a fresh readback taken after every write c
          -ompletes. | A run whose object was erased by a concurrent writ
          -er still reports `verified: true` — this is the exact c8 mecha
          -nism. |                                                       
      317 -| M9 | Remove the per-run `try/except` in `recover` so the fir
          -st exception escapes the loop. | Runs queued behind a broken r
          -ecovery candidate stay undeployed. |                          
      318 -| M10 | Delete the reconcile wrapper; call `provider.create_dr
          -aft` directly and let exceptions propagate. | A write that rai
          -ses after persisting, followed by a matching readback, kills t
          -he run instead of being reconciled as success. |              
      319 -| M11 | Remove the zero-asset guard from `submit`. | An empty 
          -approved request gets a run id and a `verified: true` receipt 
          -over zero objects. |                                          
      320 -| M12 | Set the expected display name to the raw approved stri
          -ng instead of the normalized form. | A request whose names exc
          -eed the limit reports a false discrepancy and fails to verify.
          - **This is the one mutation that catches over-strictness rathe
          -r than under-strictness** — without it, a verifier that reject
          -s everything would pass every other check here. |             
      321 -| M13 | Remove the normalization-collision precheck from `subm
          -it`. | A request with two colliding names deploys, and the pro
          -vider ends up holding two drafts with identical display names.
          - |                                                            
      322 -| M14 | In verification, iterate approved assets only; drop th
          -e reverse scan for provider objects under this deployment's ke
          -y prefix. | A stray object under the deployment's prefix goes 
          -unnoticed and the run still verifies. |                       
      344 +| M3 | Move the conflict check to *after* the first `create_dr
          +aft` call instead of before it. | Same key + changed payload s
          +till raises, but the provider object count has changed — the "
          +provider is untouched" half of the check fails. |             
      345 +| M4 | Restore `external_key = f"{run_id}:{asset_id}"` in `run
          +_once`. | `retry` produces a second full set of drafts, and th
          +e key-stability check sees a different key after recovery. |  
      346 +| M5 | Include `payload_hash` in `external_key`, then re-submi
          +t the identical approval with its JSON serialized in a differe
          +nt key order. | Key stability fails — semantically identical a
          +pprovals compute different keys and acquire a second set of dr
          +afts. |                                                       
      347 +| M6 | In the field comparison, assert only that `object_id` i
          +s truthy; drop the equality checks on `external_key`, `source_
          +asset_id`, `source_sha256`, `object_type`, `status`. | Tamperi
          +ng with any of those in provider state still certifies `comple
          +te`. |                                                        
      348 +| M7 | Add `display_name` to the fields that must be **unique*
          +* across a deployment (or reject colliding names at `submit`).
          + | The two-assets-sharing-a-normalized-name check fails — a va
          +lid approval is rejected or certified `divergent`. **This is t
          +he mutation that guards against re-introducing the rule this r
          +oadmap deliberately excludes (CH16).** |                      
      349 +| M8 | Set the expected `display_name` to the raw approved str
          +ing instead of the normalized form. | A request whose names ex
          +ceed the limit certifies `divergent` — a false discrepancy. To
          +gether with M7, this pins name handling from both sides: too s
          +trict on value, too strict on uniqueness. |                   
      350 +| M9 | In the certification routine, replace the per-key `prov
          +ider.read()` with `json.loads(row["receipt_json"])["objects"]`
          +. | Deleting a provider object still certifies `complete`. |  
      351 +| M10 | Have `audit` return the certification stored on the re
          +ceipt instead of recomputing it. | The status/certification in
          +dependence check fails — a stale `complete` survives a real de
          +letion. |                                                     
      352 +| M11 | Map an unreadable provider to `divergent` instead of `
          +unknown`. | The unreadable-provider check fails; a transient r
          +ead failure is reported as data loss. |                       
      353 +| M12 | Let `run_once` set `status='done'` regardless of the c
          +ertification it computed. | The finished-but-uncertifiable run
          + is reported `done` instead of `failed`. |                    
      354 +| M13 | Have `deployment_summary` return workflow status with 
          +no certification field. | The "no surface reports `done` witho
          +ut a certification" assertion fails. |                        
      355 +| M14a | Remove the `cancel_requested` read from the per-asset
          + loop in `run_once`. | A run cancelled after the first write k
          +eeps writing the remaining assets. |                          
      356 +| M14b | Make the terminal update unconditional again — drop `
          +AND status='running' AND cancel_requested=0 AND owner=?`. | A 
          +cancelled run reaches `done`. |                               
      357 +| M15 | In the claim, execute the conditional `UPDATE` but ign
          +ore `cursor.rowcount` and proceed regardless. | N workers raci
          +ng one pending run → more than one proceeds to write. |       
      358 +| M16 | Build the certification from the dicts `create_draft` 
          +returned, rather than from a fresh readback taken after every 
          +write completes. | A run whose object was erased by a concurre
          +nt writer still certifies `complete` — this is the exact c8 me
          +chanism. |                                                    
      359 +| M17 | In certification, iterate approved assets only; drop t
          +he reverse scan for provider objects under this deployment's k
          +ey namespace. | A stray object goes unnoticed and the run stil
          +l certifies `complete`. |                                     
      360 +| M18 | Remove the per-run `try/except` in `recover` so the fi
          +rst exception escapes the loop. | Runs queued behind a broken 
          +recovery candidate stay undeployed. |                         
      361 +| M19 | Delete the reconcile wrapper; call `provider.create_dr
          +aft` directly and let exceptions propagate. | A write that rai
          +ses after persisting, followed by a matching readback, kills t
          +he run instead of being reconciled as success. |              
      362 +| M20 | Remove the zero-asset guard from `submit`. | An empty 
          +approved request gets a run id and a `complete` certification 
          +over zero objects. |                                          
      363  
      364  **Test-suite integrity mutation.** Separately, change
      365  `FakeHubSpot.DISPLAY_NAME_LIMIT` and swap which fixture a test
            loads. Any test
     ...
      369  
      370  ---
      371  
      332 -## 8. Verification, and the make-target rework CH11 forces    
      372 +## 8. Verification, demo, and stress                          
      373  
      334 -`make demo`, `make test`, `make stress` must all keep working 
          -(TASK.md). CH11                                               
      335 -rejects `deployment_request.json`, which all three use, so all
          - three need rework.                                           
      336 -**This is the largest single consequence in this roadmap and t
          -he decision most                                              
      337 -worth arguing about.**                                        
      374 +`make demo`, `make test`, `make stress` all keep working, and 
          +all three keep                                                
      375 +using `deployment_request.json`. No fixture is rejected by thi
          +s design.                                                     
      376  
      339 -**`make demo`** — the rejection becomes the opening act, not a
          - broken step:                                                 
      377 +**`make demo`** — the scenario is unchanged; what changes is w
          +hat it reports.                                               
      378 +The point is now the *split* between history and certification
          +:                                                             
      379  
      341 -1. Submit `deployment_request.json` → refused at the door, nam
          -ing the two assets that would have been indistinguishable in H
          -ubSpot. Nothing written. Provider still empty.                
      342 -2. Submit `deployment_request_empty.json` → refused, nothing t
          -o deploy.                                                     
      343 -3. Submit `deployment_request_short.json` → deploys, crashes p
          -artway, recovers, reports `verified` from a live readback.    
      344 -4. Delete two objects from the provider's state file, exactly 
          -as `demo.py:42-45` does today, then press "Check again" → now 
          -reports the loss instead of hiding it.                        
      380 +1. Deploy `deployment_request.json`, crash partway, restart, r
          +ecover. Status `done`, certification `complete`, computed from
          + a live readback. Note in the output that two of the four draf
          +ts share a normalized display name and that this is correct, n
          +ot a defect.                                                  
      381 +2. Delete two objects from the provider's state file, exactly 
          +as `demo.py:42-45` does today. Press "Check again". **Workflow
          + status is still `done` — history did not change. Certificatio
          +n is now `divergent`, naming the two missing objects, with the
          + time of the check.** Against the starter's output — which rep
          +orts `verified: true, all_present: true` over the same state —
          + this is the whole demonstration.                             
      382 +3. Submit `deployment_request_empty.json`. Refused at `submit`
          +; no run, no certification, nothing written.                  
      383  
      346 -Step 4 against the old output is the demonstration. Steps 1–2 
          -show the two                                                  
      347 -refusals that stop a false success before it can exist.       
      384 +**`make stress`** — unchanged fixture and unchanged shape. It 
          +should still show                                             
      385 +the provider holding fewer objects than the runs created (CH13
          + is out of scope),                                            
      386 +but **no run may certify `complete` while its objects are miss
          +ing**, runs that                                              
      387 +cannot certify must end `failed` rather than `done`, and reads
          + that fail against                                            
      388 +a half-written state file must certify `unknown` rather than `
          +divergent`. Same                                              
      389 +short count, honest verdict.                                  
      390  
      349 -**`make stress`** — switch to `deployment_request_short.json`,
          - the only fixture                                             
      350 -that both deploys and has no collision. Counts stay derived (`
          -WORKERS * assets`),                                           
      351 -nothing hardcoded. It should still show the provider short of 
          -the expected total                                            
      352 -(CH13 is out of scope), but **no run may report `verified: tru
          -e` while its                                                  
      353 -objects are missing**, and the recovery pass must not report s
          -uccess over a short                                           
      354 -provider. That inversion — same short count, honest verdict — 
          -is the point.                                                 
      355 -                                                              
      356 -**`make test`** — the two starter tests move to the short fixt
          -ure (see §6), plus                                            
      391 +**`make test`** — the two starter tests stay on the full fixtu
          +re (see §6), plus                                             
      392  the new acceptance suite.
      393  
      359 -**Risk to name in `DECISIONS.md`:** a reviewer will notice the
          - headline fixture                                             
      360 -no longer deploys. The defense is that the fixture appears to 
          -exist precisely to                                            
      361 -trigger this — it is the only one with names past the limit, a
          -nd TASK.md says                                               
      362 -the shapes differ on purpose. If that reading is wrong, CH11 s
          -hould drop back to                                            
      363 -a receipt warning and everything above reverts.               
      364 -                                                              
      394  ---
      395  
      396  ## 9. Out of scope
     ...
      404  - Any UI.
      405  - Schema migration. Every entry point builds its own database.
      406  - The provider's internal lost-write and torn-read race (CH13,
            F8).
      407 +- Any rule keyed on `display_name` uniqueness (CH16).         
      408  - c11 `thumbnail_render` (F20).
      409  - Queue, workflow framework, container, cloud service, real mo
           del, OAuth.
      410  
     ...
      425  
      426  For `DECISIONS.md`:
      427  
      398 -- The provider can still lose writes. We detect it; we do not 
          -prevent it (CH13).                                            
      399 -- Verification is point-in-time. HubSpot can change a second a
          -fter `audit` returns.                                         
      400 -- There is still a window between a provider write and the loc
          -al receipt. Recovery closes it by re-reading, not by making th
          -e two atomic.                                                 
      401 -- A normalization collision is refused, not repaired. We do no
          -t rename an operator's assets — we hand the request back and s
          -ay which two names collide.                                   
      402 -- Collision detection only covers what the relay knows the pro
          -vider does to a name (strip + truncate). Any other normalizati
          -on HubSpot applies is invisible to us until a readback disagre
          -es.                                                           
      428 +- **Certification is point-in-time and expires the instant it 
          +is returned.** HubSpot can change a second later. `complete` m
          +eans "complete when we looked," and the timestamp is reported 
          +for exactly that reason. There is no watch, no subscription, a
          +nd no continuous reconciliation.                              
      429 +- The provider can still lose writes. We detect it and report 
          +`divergent`; we do not prevent it (CH13).                     
      430 +- `unknown` is honest but not actionable on its own. A run stu
          +ck at `unknown` needs a human or a later successful read; noth
          +ing here resolves it automatically.                           
      431 +- There is still a window between a provider write and the loc
          +al record. Recovery closes it by re-reading, not by making the
          + two atomic.                                                  
      432 +- Identity is only as stable as the operator's idempotency key
          +. If a client regenerates its key for the same approval — c3's
          + ambiguous shape — this design will deploy twice and consider 
          +both correct. That is the accepted cost of choosing the key as
          + the unit of idempotency, and it is the case least covered by 
          +this roadmap.                                                 
      433 +- Two drafts may share a display name in HubSpot and be hard f
          +or a person to tell apart. That is accepted deliberately (§4, 
          +Identity): the objects are distinct and correct, and a name-un
          +iqueness rule would reject valid approvals. The mitigation is 
          +that `external_key`, `object_id`, and `source_asset_id` all re
          +main distinct and are what the system reasons about.          
      434  - Nothing is ever cleaned up. Duplicate drafts already in HubS
           pot from before this change stay there — the provider has no d
           elete.
      404 -- G6 (c9) is fixed at a cause we reasoned about but never repr
          -oduced with a natural trigger.                                
      435 +- G6 (c9) is addressed at a cause we reasoned about but never 
          +reproduced with a natural trigger.                            

  Ran 2 shell commands (ctrl+o to expand)

⏺ Verified against the fixtures: all four external_keys unique per deployment,
  deployment_request.json deployable with two assets legitimately sharing a
  normalized name, empty refused, and the conflict gate separating the two
  payloads by hash.

  Revised sections

  §1 — F10 reworded (dropped "indistinguishable in HubSpot"; now just states
  they normalize to the same value). I2 rewritten to call c3 ambiguous. New I6
  added:

  ▎ | I6 | F10 is deliberate fixture design: deployment_request.json is shaped 
  ▎ so two approved assets normalize to the same stored display_name, 
  ▎ demonstrating that display-name uniqueness is not a valid identity rule. | 
  ▎ Intent is inferred. But the reading is load-bearing here — it is why 
  ▎ identity is keyed on idempotency_key + asset_id and never on a name. |

  §2 — c3 rewritten. Now "ambiguous from the evidence," with both readings
  stated and neither preferred. The stress.py claim is explicitly demoted:

  ▎ stress.py submits the same payload under 12 different keys and expects 48 
  ▎ objects (F17), but that is the starter's expectation encoded in a test 
  ▎ harness — it is not evidence about what an operator meant, and it should not
  ▎ be cited as though it were.
  ▎
  ▎ Design choice, made in spite of the ambiguity: the operator's idempotency 
  ▎ key is the unit of idempotency. […] It does not resolve c3 — it decides how 
  ▎ the system will behave when c3's shape recurs.

  §3 — promise rewritten around complete / divergent / unknown, with history
  separated from proof:

  ▎ A run finishing is history, not proof. done means the run completed its work
  ▎ at the time it ran. It is never presented on its own as evidence that 
  ▎ HubSpot matches now.

  §4 — restructured around two axes. New table splitting workflow status
  (pending/running/done/cancelled/failed, historical) from provider
  certification (complete/divergent/unknown, point-in-time). done + divergent is
  stated as legal and expected. unknown is held distinct from divergent so a
  torn read never reads as data loss.

  Rule 4 now lists the identity fields — external_key, source_asset_id,
  source_sha256, object_type, status == "draft". Rule 5 is new:

  ▎ Name match, not name uniqueness — display_name equals the 
  ▎ provider-normalized form of the approved name. Two objects in the same 
  ▎ deployment may legitimately carry the same normalized value; that is not a 
  ▎ discrepancy and never fails certification.

  New rules 8 and 9: done requires complete at completion time (otherwise
  failed), and no surface may present status without a certification and its
  timestamp.

  New Identity subsection: external_key = idempotency_key + ":" + asset_id, with
  payload_hash excluded and the reason given —

  ▎ If it were part of the key, identity would drift with any change in how a 
  ▎ payload is serialized — a reordered key, a whitespace change — and the same 
  ▎ approval would silently acquire a second set of drafts.

  The old "Consequence of point 10 — this rejects the primary fixture" section
  is deleted.

  §5 — CH11 collision rejection removed. Renumbered: CH5 now produces a
  certification instead of a boolean; CH6 is new (the status/certification
  split); CH7 covers both audit and deployment_summary. CH16 is new — a standing
  exclusion so the removed rule can't drift back in:

  ▎ | CH16 | Any rule keyed on display_name — uniqueness checks, collision 
  ▎ rejection, name-based dedupe. Explicitly not built. | OUT OF SCOPE | F10 |

  §6 — full fixture restored as the workhorse. Collision-refusal test deleted.
  Five tests added: external_key stability across runs, two assets sharing a
  normalized name both certifying complete, unreadable provider certifying
  unknown, status/certification independence, and a run that writes but can't
  certify ending failed. The conflict test now also asserts the provider is
  untouched after the raise.

  §7 — 14 mutations → 20. M7 is the guard against the rule you had me remove:

  ▎ | M7 | Add display_name to the fields that must be unique across a 
  ▎ deployment (or reject colliding names at submit). | The 
  ▎ two-assets-sharing-a-normalized-name check fails — a valid approval is 
  ▎ rejected or certified divergent. This is the mutation that guards against 
  ▎ re-introducing the rule this roadmap deliberately excludes (CH16). |

  Paired with M8 (expecting the raw name instead of the normalized one), name
  handling is pinned from both sides. Also new: M3 (conflict check moved after
  the first write), M5 (payload_hash in the key + reordered JSON), M10 (audit
  returns a stored certification), M11 (unreadable mapped to divergent), M12
  (done regardless of certification), M13 (summary with no certification field).

  §8 — no fixture rework needed. All three targets keep deployment_request.json.
  The demo's payoff is now the split:

  ▎ Workflow status is still done — history did not change. Certification is now
  ▎ divergent, naming the two missing objects, with the time of the check.

  §10 — limits updated. Collision bullets replaced. Three added: certification
  expires the instant it's returned; unknown is honest but not self-resolving;
  and the accepted cost of the idempotency-key choice —

  ▎ If a client regenerates its key for the same approval — c3's ambiguous shape
  ▎ — this design will deploy twice and consider both correct. That is the 
  ▎ accepted cost of choosing the key as the unit of idempotency, and it is the 
  ▎ case least covered by this roadmap.

  Diff

  404 → 435 lines; 167 added, 136 removed.
  Ran 2 shell commands (ctrl+o to expand)

⏺ Diff truncated at 190 lines — full unified diff saved to /Users/nityanthmaramr
  eddy/.claude/projects/-Users-nityanthmaramreddy-Downloads-02-durable-run-relay
  /ef755e86-ce6c-45ea-875b-5c355a9d30ff/tool-results/bnou27sux.txt (38.6KB). The
  remaining hunks are §6 acceptance tests, §7 mutations, §8 demo/stress, §9
  out-of-scope, §10 limits — all summarized above.

  Only ROADMAP.md changed. No source, test, fixture, or Makefile edits. Nothing
  committed — still not a git repo.

  Two things worth flagging in what I wrote:

  done now requires complete at completion time, otherwise failed. You specified
  the two axes and that history is preserved, but not what a run should be
  called when it finishes writing and can't certify. I chose failed so done
  keeps a real meaning; the alternative is done + divergent from the start,
  which makes done mean "the writes happened" and nothing more. Easy to flip if
  you prefer that reading.

  Removing the collision rule leaves nothing checking F10's operator-visible 
  effect. Two drafts in HubSpot share a label and a person browsing the UI can't
  tell them apart. Your reasoning holds — the objects are distinct, and name
  uniqueness is not identity — so I recorded it as an accepted cost in §10
  rather than mitigating it. If you want it surfaced without being enforced, a
  non-blocking note on the receipt would do it, but I didn't add one since you
  asked for the rule gone.

✻ Worked for 5m 26s

❯ Before coding, inspect git status and git log. If the untouched starter has a 
  baseline commit and ROADMAP.md is the only change, commit ROADMAP.md alone.   
  If that is not true, stop and show me the state instead of committing. Do not 
  edit source or tests. conduct git status, git show --stat HEAD, and the last  
  three commits. Just so im aware.                                              

  Ran 1 shell command (ctrl+o to expand)

⏺ Not committing. Your precondition fails — there is no repository at all.

  Ran 1 shell command (ctrl+o to expand)

⏺ Contradiction between those two checks — re-verifying now.

  Ran 2 shell commands (ctrl+o to expand)

⏺ Stopping. Your precondition is not met, so nothing was committed.

  State

  A repo appeared mid-session. My first check found no .git at all; seconds
  later .git existed with a 13:27 timestamp. Someone ran git init between my two
  commands — presumably you in the IDE.

  git status
  On branch main

  No commits yet

  Untracked files:
        .gitignore    DECISIONS.md   Makefile     README.md
        ROADMAP.md    SUBMISSION.md  TASK.md      demo.py
        fixtures/     relay/         stress.py    tests/

  nothing added to commit but untracked files present

  git show --stat HEAD
  fatal: ambiguous argument 'HEAD': unknown revision or path not in the working
  tree.

  git log -3
  fatal: your current branch 'main' does not have any commits yet

  Why I did not commit

  Your rule was: baseline commit exists and ROADMAP.md is the only change.

  Neither holds.

  - No baseline commit. HEAD points at refs/heads/main, which does not exist
  yet. Zero commits.
  - ROADMAP.md is not the only change — from git's view it's one of twelve
  untracked entries. The entire starter is untracked. Committing ROADMAP.md
  alone right now would produce a first commit containing only the roadmap, with
  the starter arriving afterwards. That inverts the history TASK.md asks for.

  There are 17 loose objects under .git/objects, which is just git init template
  content — nothing staged, nothing written by me.

  Source is untouched: relay/core.py still carries its original Aug 2 12:43:41
  mtime. Only ROADMAP.md is mine (Aug 5 13:24:57).

  What the history should look like

  Two commits, in this order:

  1. Baseline — the starter exactly as shipped, ROADMAP.md excluded. Gives a
  real "untouched starter" commit to diff every later change against.
  2. ROADMAP.md alone — satisfying TASK.md's "your ROADMAP.md, committed before
  any source or test edit."

  Doing it in one commit, or roadmap-first, loses the property that makes the
  roadmap commit meaningful.

⏺ User answered Claude's questions:
  ⎿  · The repo has no commits yet and the whole starter is untracked. How do 
     you want the history built? → Baseline, then roadmap (Recommended)

⏺ Approved plan noted. Checking exactly what would land in the baseline before
  staging anything.

  Ran 4 shell commands (ctrl+o to expand)

⏺ Both commits made, in the order you approved.

  634a54f  Add ROADMAP.md before any source or test edit
  4125a1b  Baseline: unmodified starter as shipped

  4125a1b — 16 files, the starter exactly as shipped. No source, test, fixture,
  or doc change. .claude/settings.local.json and the __pycache__ files stayed
  out (already ignored).

  634a54f — ROADMAP.md alone, 435 insertions, 1 file changed. Nothing else in
  it.

  Working tree is clean.

  That gives you the property TASK.md asks for: git diff 4125a1b against any
  later commit shows exactly what changed from the untouched starter, and the
  roadmap landed before a single line of source or test was touched.

  One thing to be aware of — the repo was initialized at 13:27 today,
  mid-session, not when the starter was unpacked. So the baseline commit is
  dated today rather than reflecting when you received the files. Timestamps
  won't show a gap between "received starter" and "started work." Nothing to
  fix, just worth knowing if a reviewer reads the history chronologically.

  No source or tests touched. Ready for implementation when you are.

✻ Crunched for 2m 58s

※ recap: Goal: make the deploy relay stop reporting success it hasn't verified against HubSpot. Roadmap is written and committed alone on top of an untouched starter baseline. Next: implement the changes, starting with certification replacing the hardcoded verified flag. (disable recaps in /config)