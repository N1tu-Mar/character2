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


❯ read through the codebase like @session1.md and @ROADMAP.md @TASK.md and the  
  fixtures folder and its files and the relay folder's files too. look at the   
  tests and i want you to just confirm once you understand as we are going to   
  be fixing up some more issues in the codebase                                 
  ⎿  Read ROADMAP.md (567 lines)
  ⎿  Read TASK.md (95 lines)

  Read 5 files, listed 1 directory, ran 2 shell commands (ctrl+o to expand)

⏺ Read all. Confirmed.

  What repo is

  Relay on SQLite + FakeHubSpot (one unlocked JSON file, not ours to change).
  Surface: submit, run_once, recover, retry, cancel, get, deployment_summary,
  audit.

  Evidence chain

  fixtures/operator_report.txt four complaints → deployment_events.jsonl c1–c11
  → ROADMAP groups G1–G7 (+c3 ambiguous, c11 out of scope) → changes CH1–CH16 →
  acceptance tests §6 → mutations M1–M20.

  Core promise: two axes never merged. Workflow status
  (pending/running/done/cancelled/failed, historical) vs certification
  (complete/divergent/unknown, recomputed fresh every time). done + divergent
  legal. Identity = external_key(idempotency_key, asset_id) =
  f"{len(k)}:{k}:{asset_id}", length-prefixed for injectivity. KeyError =
  divergent, parse/open failure = unknown, must not share except.

  Shipped so far (4 source/test commits)

  - eb29a84 red tests, 4a25036 CH2 + CH11-partial, c02bac5
  unrelated-IntegrityError pin.
  - core.py:106 idempotency_key TEXT NOT NULL UNIQUE.
  - submit insert-first, IntegrityError re-reads winner, hash match returns
  winner id, mismatch raises IdempotencyConflict, key-absent re-raises original.
  - retry UPDATEs existing row to pending + clears receipt instead of inserting
  second row.
  - 9 tests green.

  Not done yet — everything else

  Last session ended announcing the shared-key slice; no code landed. Current
  state:

  - core.py:226 still external_key = f"{run_id}:{asset['asset_id']}" — CH1/CH1b,
  and the §5 first-slice helper, both open.
  - core.py:241 "verified": True still literal. deployment_summary/audit still
  read receipt_json, never open provider. CH5/CH5b/CH6/CH7 open.
  - cancel writes status only; run_once terminal UPDATE unconditional;
  RunCancelled still dead. CH8.
  - No owner/lease_expires_at/attempt columns. CH4.
  - recover() still bare loop on status='running', no per-run try/except, no
  failed state exists. CH9.
  - No reconcile wrapper around create_draft. CH10.
  - No zero-asset guard (CH3), no empty-component guard (CH1b).
  - No WAL/timeout on _connect (CH12).

  Open defects flagged mid-session, unfixed

  - retry resets to pending unconditionally — resurrects a cancelled run. CH8
  territory.
  - _connect() never closes connections; ResourceWarning: unclosed database
  under Python 3.13. Turns into database is locked once CH12 lands.
  - database is locked reachable at higher contention than 8 threads / 4
  processes — conflict path correct, "no unexpected exception" only proven at
  that scale.
  - retry does not reset attempt count / clear lease per §4.14 (columns don't
  exist yet).
  - tests/test_visible.py both starter tests still tautological (F13, F14), kept
  as ballast.
  - session1.md untracked.

  Ready. Which slice next — shared external_key helper (§5 says it goes first),
  or certification?

✻ Brewed for 1m 23s

❯ please Add failing tests for CH1, CH1b, and CH3 only, and do not edit         
  production code. Cover that provider keys use idempotency_key and asset_id    
  instead of run_id, that retry, recovery, and repeated execution compute the   
  same provider key, then make sure that the colon-containing components cannot 
  collide, that one deployment namespace cannot absorb another prefix-shaped    
  key. as that results into conflicts later on. then make sure that             
  payload_hash and display_name are not used as provider identity, that empty   
  idempotency_key, empty asset_id, and zero-asset payloads are rejected at      
  submit, and that two assets sharing the same normalized display name are      
  still valid. Derive all values from the loaded payload instead of hardcoding  
  fixture-specific IDs, hashes, counts, or name limits. Run only the new tests, 
  show the failing output and the test diff, confirm relay/core.py was not      
  modified, then stop.                                                          

⏺ Skill(superpowers:test-driven-development)
  ⎿  Successfully loaded skill

⏺ Writing failing tests only. Three files touched: one new test file.

⏺ Write(tests/test_provider_key_identity.py)
  ⎿  Wrote 368 lines to tests/test_provider_key_identity.py
       1 """Provider identity, and the guards at the door that keep it we
       2 
          length-
       4 prefixed function over `idempotency_key` and `asset_id`), CH1b (
       5 components refused at `submit`), and CH3 (zero-asset payloads re
       6 `submit`). It also pins CH16 from the other side: two approved a
       7 names normalize to one stored value are both valid and must both
          deploy.
       9 Nothing here certifies, cancels, claims, recovers beyond what a 
      10 bounds attempts.
     … +438 lines (ctrl+o to expand)

  Ran 2 shell commands (ctrl+o to expand)

⏺ All 15 red, zero errors. But 11 fail on the missing-helper guard alone — that
  only proves the function is absent, not that the tests check behavior.
  Probing: inject both helpers at runtime (no file edit) and re-run, so the
  remaining failures are the real ones.

  Ran 4 shell commands (ctrl+o to expand)

⏺ Stopped. Nothing committed, no production code touched.

  Added

  tests/test_provider_key_identity.py, 368 lines, 15 tests in 4 classes. Diff
  shown above.

  Failing output

  Ran 15 tests in 0.039s
  FAILED (failures=15)

  15 failures, 0 errors. Two shapes of failure:

  - AssertionError: relay.core.external_key does not exist (CH1) — 11 tests
  - AssertionError: ValueError not raised — 4 door tests

  Helpers are fetched with getattr, not imported, so a missing name is a failure
  naming the missing change rather than a collection error that would hide the
  rest of the file.

  Probe — proving the red is real, not just a missing name

  11 tests failing on "function absent" prove nothing yet. Injected both helpers
  at runtime with the roadmap's encoding (f"{len(k)}:{k}:{a}"), no file edit,
  re-ran:

  Ran 15 tests in 0.056s
  FAILED (failures=9)

  The 6 pure-encoding tests go green — they test the encoding, and the roadmap's
  encoding satisfies them. The other 9 stay red on real behavior:

  Test: test_deployed_objects_are_named_by_the_approval_not_the_run
  Failure with helpers present: set mismatch — run_once builds
  f"{run_id}:{asset_id}" inline
  ────────────────────────────────────────
  Test: test_the_keys_survive_a_crash_a_recovery_and_a_retry
  Failure with helpers present: set mismatch
  ────────────────────────────────────────
  Test: test_a_namespace_scan_returns_only_its_own_objects
  Failure with helpers present: set mismatch
  ────────────────────────────────────────
  Test: test_an_object_is_named_without_consulting_the_payload
  Failure with helpers present: set mismatch
  ────────────────────────────────────────
  Test: test_two_assets_sharing_a_normalized_name_both_deploy
  Failure with helpers present: set mismatch (name collision itself passes —
  CH16
  guard already holds)
  ────────────────────────────────────────
  Test: 4 × SubmitDoorTest
  Failure with helpers present: ValueError not raised

  Derivation

  No fixture asset id, hash, count, or literal 40. Colliding names built from
  FakeHubSpot.DISPLAY_NAME_LIMIT, so the case survives a change to the limit —
  the fixture's own asset-email-002/003 collision would not. Colon and prefix
  cases constructed in-test; c3's 207a/207b used as shape, not as values.

  Two calls I made — override if wrong

  1. ValueError for CH1b/CH3. Roadmap names no exception. Asserted the base
  class so an implementation may raise a subclass. IdempotencyConflict would be
  wrong — these requests conflict with nothing, they are malformed alone.
  2. test_two_assets_sharing_a_normalized_name_both_deploy also asserts 
  approval-derived keys. Without that it would pass today and prove nothing
  (F14's trap). It now fails today and stays as the M7 guard afterwards.

  Not covered in this slice

  external_key must be the only place a key is constructed — the roadmap's
  monkeypatch test (M5c) proving writer and certifier agree. That needs
  certification to exist (CH5), so it belongs in that slice.

✻ Brewed for 5m 0s

❯ can you break down this issue in simple terms? or what you have done in       
  simple terms to digest easily                                                 

⏺ The problem, plainly

  When the service pushes a draft into HubSpot, it has to give that draft a
  name-tag so it can find it again later. Right now the tag is:

  <run_id>:<asset_id>        e.g.  "a3f9-...-c1:asset-lp-001"

  run_id is a fresh random UUID every single execution.

  So the tag changes every time the work runs. Consequences:

  - Operator hits retry → new run_id → new tag → HubSpot sees a brand-new object
  → 4 more drafts. That's case c4.
  - Same approval submitted twice → same thing → 8 drafts. Case c2.
  - Nothing can ever look up "the draft for asset-lp-001 in campaign X", because
  the tag depends on which attempt happened to write it.

  The fix (CH1): tag by what was approved, not by which attempt ran.

  <idempotency_key>:<asset_id>    e.g.  "campaign-1:asset-lp-001"

  Now retry, crash-recovery, and a repeat submit all compute the same tag, land
  on the same draft, and nothing duplicates.

  Two traps inside that fix

  Trap 1 — the colon is ambiguous.

  key "a"    + asset "b:c"   →  "a:b:c"
  key "a:b"  + asset "c"     →  "a:b:c"     same tag, two unrelated deployments

  Fix: put the length in front. f"{len(key)}:{key}:{asset_id}"

  "a"   / "b:c"  →  "1:a:b:c"
  "a:b" / "c"    →  "3:a:b:c"     now different

  Trap 2 — "list everything belonging to this deployment" by prefix.

  To spot stray or missing objects, the code scans HubSpot for tags starting
  with the deployment's prefix. But deploy-20 is a string prefix of deploy-207a,
  so a naive scan steals the other deployment's objects. The length prefix
  fixes this too: 9:deploy-20: cannot match 11:deploy-207a:....

  The door guards (CH1b, CH3)

  Three malformed requests currently sail straight through submit:

  ┌─────────────────────┬───────────────────────────────────────┬──────────┐
  │       Request       │                 Today                 │ Should   │
  │                     │                                       │    be    │
  ├─────────────────────┼───────────────────────────────────────┼──────────┤
  │ empty               │ accepted                              │ rejected │
  │ idempotency_key     │                                       │          │
  ├─────────────────────┼───────────────────────────────────────┼──────────┤
  │ empty asset_id      │ accepted                              │ rejected │
  ├─────────────────────┼───────────────────────────────────────┼──────────┤
  │ zero assets         │ accepted, reports verified: true over │ rejected │
  │                     │  nothing                              │          │
  └─────────────────────┴───────────────────────────────────────┴──────────┘

  Empty components matter because an empty key makes the namespace prefix itself
  a valid object tag — the scan then matches everything. Zero assets matter
  because "verified campaign, nothing in HubSpot" is exactly what the operator
  complained about.

  One thing deliberately not fixed

  Two of the approved emails have long names that HubSpot truncates to the same
  40 characters. They end up with identical display names in HubSpot. That looks
  like a duplicate and isn't — they're two different, correctly deployed
  assets. So there's a test whose whole job is to fail if anyone ever adds a
  "reject duplicate names" rule.

  What I actually did

  Wrote 15 tests. Touched no production code.

  - 6 test the tag-building rule on its own (colons, prefixes, hash and name
  staying out of the tag)
  - 5 test that a real deploy, a crash-recovery, and a retry all land on the
  same tags
  - 1 tests the shared-name case still works
  - 3 (+1 fixture variant) test the door guards

  All 15 fail right now.

  Then I ran an extra check, because 11 of them were failing only with "that
  function doesn't exist yet" — which proves nothing. I injected a working
  version of the tag function at runtime (no file edit) and re-ran. Six went
  green, meaning they genuinely test the encoding. Nine stayed red — those are
  testing real deployment behavior that's still broken. That's the honest red.

  relay/core.py is byte-identical to the last commit. Confirmed by hash.

  Next

  Implement it: add the two helper functions, route run_once through them, add
  the three guards to submit. Those 15 go green.

✻ Brewed for 31s

※ recap: Goal: make the relay stop reporting HubSpot success it never checked. Wrote 15 failing tests for approval-derived provider keys and submit-time guards, no source touched. Next: implement the external_key and deployment_namespace helpers plus the submit guards to turn them green. (disable recaps in /config)

❯ Implement only CH1, CH1b, and CH3 against the new failing identity and        
  door-guard tests. Keep the shared external_key and deployment_namespace       
  helpers already present on this branch, but change them to use the roadmap’s  
  collision-safe length-prefixed encoding over idempotency_key and asset_id     
  instead of run_id. then Route run_once and every existing provider-key        
  construction through those helpers, ensure payload_hash and display_name are  
  never part of provider identity, and reject empty idempotency_key, empty      
  asset_id, and zero-asset payloads inside submit before creating a database    
  row. Two assets whose provider-normalized display names are identical must    
  remain valid and deploy as distinct objects. Do not change retry semantics,   
  certification, workflow-status logic, cancellation, worker claims, or         
  recovery yet. Run the 15 new tests, then run make test and make demo, show    
  the complete production diff and full outputs, and stop without committing.   
⏺ Correction: those helpers aren't on the branch — relay/core.py is
  byte-identical to c02bac5. They existed only as getattr lookups in my test
  file. Creating them now.

⏺ Update(relay/core.py)
  ⎿  Added 64 lines
      20      pass
      21  
      22  
      23 +class InvalidApprovedRequest(ValueError):                      
      24 +    """An approved request that cannot name a deployment or its
         + objects."""                                                   
      25 +                                                               
      26 +                                                               
      27  def _canonical_json(value: Any) -> str:
      28      return json.dumps(value, sort_keys=True, separators=(",", "
          :"))
      29  
     ...
      32      return hashlib.sha256(value.encode()).hexdigest()
      33  
      34  
      35 +def external_key(idempotency_key: str, asset_id: str) -> str:  
      36 +    """The only place a provider key is built.                 
      37 +                                                               
      38 +    Identity is the operator's approval, never the attempt that
         + carried it                                                    
      39 +    out. The key contains no `run_id`, no UUID, and no timestam
         +p, so a retry,                                                 
      40 +    a recovery pass, and a re-submitted approval all compute th
         +e same value                                                   
      41 +    and land on the same HubSpot object.                       
      42 +                                                               
      43 +    The leading length pins where the idempotency key ends, whi
         +ch makes the                                                   
      44 +    encoding injective for any component contents, colons inclu
         +ded. Plain                                                     
      45 +    concatenation is not: `("a", "b:c")` and `("a:b", "c")` bot
         +h flatten to                                                   
      46 +    `a:b:c`, and two unrelated deployments would share provider
         + objects.                                                      
      47 +                                                               
      48 +    Neither `payload_hash` nor `display_name` is an input. A ha
         +sh in the key                                                  
      49 +    would move identity with the serialization of a payload; a 
         +name in the                                                    
      50 +    key would make two legitimately identical names into one ob
         +ject.                                                          
      51 +    """                                                        
      52 +    return f"{len(idempotency_key)}:{idempotency_key}:{asset_id
         +}"                                                             
      53 +                                                               
      54 +                                                               
      55 +def deployment_namespace(idempotency_key: str) -> str:         
      56 +    """The prefix holding exactly one deployment's provider obj
         +ects.                                                          
      57 +                                                               
      58 +    Unambiguous for the same reason `external_key` is: scanning
         + for                                                           
      59 +    `9:deploy-20:` cannot match `11:deploy-207a:asset-lp-001`, 
         +which naive                                                    
      60 +    prefix matching on `deploy-20` would.                      
      61 +    """                                                        
      62 +    return f"{len(idempotency_key)}:{idempotency_key}:"        
      63 +                                                               
      64 +                                                               
      65 +def _validate_approved_request(                                
      66 +    idempotency_key: str,                                      
      67 +    payload: dict[str, Any],                                   
      68 +) -> list[dict[str, Any]]:                                     
      69 +    """Refuse a request that cannot be named, before any row or
         + write.                                                        
      70 +                                                               
      71 +    Returns the approved assets so the caller does not re-deriv
         +e them.                                                        
      72 +    """                                                        
      73 +    if not idempotency_key:                                    
      74 +        raise InvalidApprovedRequest(                          
      75 +            "an approved request needs an idempotency key: it i
         +s what names"                                                  
      76 +            " the deployment and every object in it"           
      77 +        )                                                      
      78 +                                                               
      79 +    assets = payload.get("assets") or []                       
      80 +    if not assets:                                             
      81 +        raise InvalidApprovedRequest(                          
      82 +            "an approved request with no assets is not a deploy
         +ment"                                                          
      83 +        )                                                      
      84 +                                                               
      85 +    for asset in assets:                                       
      86 +        if not str(asset.get("asset_id") or ""):               
      87 +            raise InvalidApprovedRequest(                      
      88 +                "every approved asset needs an asset id: an emp
         +ty one makes"                                                  
      89 +                f" its provider key equal to {deployment_namesp
         +ace(idempotency_key)!r},"                                      
      90 +                " which is the namespace the scan for extras ru
         +ns over"                                                       
      91 +            )                                                  
      92 +    return list(assets)                                        
      93 +                                                               
      94 +                                                               
      95  class FakeHubSpot:
      96      """Small durable provider simulation with idempotent draft 
          creation.
      97  

⏺ Update(relay/core.py)
  ⎿  Added 4 lines
      188          submitting the same key at once would both see no row 
           and both insert,
      189          so the loser's `IntegrityError` is the answer rather t
           han an error --
      190          it re-reads the row that actually landed and compares 
           against that.
      191 +                                                              
      192 +        A request that cannot name a deployment or its objects
          + is refused                                                   
      193 +        here, before a row exists and long before the provider
          + is opened.                                                   
      194          """
      195 +        _validate_approved_request(idempotency_key, payload)  
      196          run_id = str(uuid.uuid4())
      197          payload_json = _canonical_json(payload)
      198          payload_hash = _digest_text(payload_json)

⏺ Update(relay/core.py)
  ⎿  Added 4 lines, removed 3 lines
      290              )
      291  
      292          readbacks: list[dict[str, str]] = []
      293 +        idempotency_key = str(run["idempotency_key"])         
      294          for index, asset in enumerate(run["payload"]["assets"]
           ):
      294 -            external_key = f"{run_id}:{asset['asset_id']}"    
      295 +            object_key = external_key(idempotency_key, str(ass
          +et["asset_id"]))                                              
      296              self.provider.create_draft(
      296 -                external_key=external_key,                    
      297 +                external_key=object_key,                      
      298                  asset=asset,
      299              )
      300              if crash_at == "after_first_provider_write" and in
           dex == 0:
      301                  raise InjectedCrash(
      302                      "crashed after provider write and before l
           ocal receipt"
      303                  )
      303 -            readbacks.append(self.provider.read(external_key))
      304 +            readbacks.append(self.provider.read(object_key))  
      305  
      306          receipt = {
      307              "run_id": run_id,

⏺ Update(relay/__init__.py)
  ⎿  Added 6 lines
       2      FakeHubSpot,
       3      IdempotencyConflict,
       4      InjectedCrash,
       5 +    InvalidApprovedRequest,                                    
       6      Relay,
       7      RunCancelled,
       8 +    deployment_namespace,                                      
       9 +    external_key,                                              
      10  )
      11  
      12  __all__ = [
      13      "FakeHubSpot",
      14      "IdempotencyConflict",
      15      "InjectedCrash",
      16 +    "InvalidApprovedRequest",                                  
      17      "Relay",
      18      "RunCancelled",
      19 +    "deployment_namespace",                                    
      20 +    "external_key",                                            
      21  ]

  Ran 2 shell commands (ctrl+o to expand)

⏺ Unexpected: 45 tests. New test files appeared that weren't here at session
  start.

  Ran 3 shell commands (ctrl+o to expand)

⏺ câ€” another session committed two red suites mid-work (abd263c, 945835f) for
  CH5/CH5b and CH4/CH8. Those are slices I was told not to touch. Isolating what
  belongs to my scope.

  Ran 1 shell command (ctrl+o to expand)

⏺ Checking whether my change altered those 26 — comparing against HEAD source
  without touching my work.

  Ran 1 shell command (ctrl+o to expand)

⏺ Identical counts before and after — 18 and 8. My change moved none of them.

  Ran 2 shell commands (ctrl+o to expand)

⏺ One cleanup: _validate_approved_request returned assets nobody used.

⏺ Update(relay/core.py)
  ⎿  Added 2 lines, removed 5 lines
      65  def _validate_approved_request(
      66      idempotency_key: str,
      67      payload: dict[str, Any],
      68 -) -> list[dict[str, Any]]:                                     
      69 -    """Refuse a request that cannot be named, before any row or
         - write.                                                        
      70 -                                                               
      71 -    Returns the approved assets so the caller does not re-deriv
         -e them.                                                        
      72 -    """                                                        
      68 +) -> None:                                                     
      69 +    """Refuse a request that cannot name a deployment or its ob
         +jects."""                                                      
      70      if not idempotency_key:
      71          raise InvalidApprovedRequest(
      72              "an approved request needs an idempotency key: it i
          s what names"

⏺ Update(relay/core.py)
  ⎿  Removed 1 line
      86                  f" its provider key equal to {deployment_namesp
          ace(idempotency_key)!r},"
      87                  " which is the namespace the scan for extras ru
          ns over"
      88              )
      89 -    return list(assets)                                        
      89  
      90  
      91  class FakeHubSpot:

  Ran 1 shell command (ctrl+o to expand)

⏺ Stopped, nothing committed.

  First: something changed under us

  Your other session committed two red suites into this repo while I was
  working:

  945835f test: add failing tests for worker ownership and cancellation   (538
  lines)
  abd263c test: add failing certification tests for CH5 and CH5b          (631
  lines)

  Those cover CH4/CH5/CH5b/CH8 — slices you told me not to touch. They are why
  make test is red. Detail below.

  Also correcting your premise: the helpers were not already on this branch.
  relay/core.py was byte-identical to c02bac5; they existed only as getattr
  probes in my test file. I created them.

  The production diff

  Two files, +78/−3. Full diff shown above. Four changes:

  1. external_key(idempotency_key, asset_id) — f"{len(k)}:{k}:{asset_id}".
  Length prefix makes it injective for any component contents. Takes two
  strings; no run_id, no payload_hash, no display_name.

  2. deployment_namespace(idempotency_key) — f"{len(k)}:{k}:".

  3. submit calls _validate_approved_request as its first statement — before
  uuid4(), before the INSERT, before the provider is ever opened. Raises
  InvalidApprovedRequest(ValueError) on empty key, empty asset_id, zero assets.

  4. run_once routes through the helper. The one inline f-string is gone:

  -            external_key = f"{run_id}:{asset['asset_id']}"
  +            object_key = external_key(idempotency_key,
  str(asset["asset_id"]))

  Local renamed to object_key so it stops shadowing the module function. grep
  confirms this was the only provider-key construction in the repo.

  Results

  test_visible                 2 tests   OK
  test_submit_idempotency      7 tests   OK
  test_provider_key_identity  15 tests   OK

  make test: 45 tests, 26 failures — RED. All 26 are in the other session's two
  files:

  ┌─────────────────────┬─────────────┬───────────────────────────┐
  │        Suite        │   Result    │           Scope           │
  ├─────────────────────┼─────────────┼───────────────────────────┤
  │ test_certification  │ 18 failures │ CH5/CH5b — not this slice │
  ├─────────────────────┼─────────────┼───────────────────────────┤
  │ test_worker_control │ 8 failures  │ CH4/CH8 — not this slice  │
  └─────────────────────┴─────────────┴───────────────────────────┘

  I checked whether I caused any of them: copied my source aside, restored
  HEAD's relay/, re-ran both suites. 18 and 8, identical. My change moved none
  of them.

  make demo — runs clean, and the keys visibly changed

  "external_key": "19:campaign-deploy-001:asset-lp-001"
  "external_key": "19:campaign-deploy-001:asset-email-002"

  Was <uuid>:asset-lp-001. 19 is len("campaign-deploy-001"). Crash → recover
  still lands on 4 objects, and now lands on them because the key is stable
  rather than by accident of recover() reusing the run id.

  The shared-name case is visible in the same output — asset-email-002 and
  asset-email-003 both stored as "Summer 2026 ABM campaign email - product", two
  distinct objects under two distinct keys. Exactly what must stay legal.

  Demo still prints verified: true over a provider holding 2 of 4. Untouched on
  purpose — that's CH5/CH6/CH7.

  What I did not do

  Per your instruction: no change to retry, certification, workflow status,
  cancellation, claims, or recovery.

  make stress not run — you scoped this to make test and make demo. It exercises
  key construction heavily across 12 concurrent runs, so it's worth a run
  before this slice is committed.

✻ Cogitated for 4m 10s

※ recap: Goal: make the deploy relay's HubSpot promise provable. I implemented CH1, CH1b, and CH3 — provider keys now derive from the approval, not the run — with 24 tests green and nothing committed. Next: run make stress, then commit this slice. (disable recaps in /config)