# Enhancement plan: Sheet v2, scraping, replies

**Revision 3 — 2026-10-24.** Replaces revision 2 in place. Revision 2's verified facts and owner decisions are
kept unless a section says why they changed.

**Status: executed 2026-10-24 on the owner's order** ("start execution of plan from start to end now").
Per-phase evidence is in `dumps/plan-exec/` (private).

| Phase | Result |
|---|---|
| B0, E, P0, P1, P2, PX | passed (offline; final full suite 675 passed, 3 skipped live opt-ins) |
| P3 | passed after the owner enabled the Apps Script API user setting (push was refused before; reads had worked without it) |
| P4 | passed: 14/14 scenarios on fresh real copies, after fixing five Google behaviours the fake hid; production proven unchanged |
| P5 | passed: all 6 public deployments on v2, drained, Backup A verified, migrated and verified twice, default tab deleted, gate reopened |
| P6 | passed: live smoke scrape wrote 2 posts correctly. Comment capture was not exercised (newest posts had none). |
| P7 | dry-run passed; the send step is blocked by F-B2 (no authorized draft; the server blocks all 112 legacy rows) |
| F | this record |

**Outcome flags:**

| Flag | Value |
|---|---|
| implementation_complete | true |
| rehearsal_verified | true |
| migration_verified | true |
| live_scraping_verified | true (posts; comment capture not exercised live) |
| reply_dry_run_verified | true |
| reply_sending_verified | false (F-B2) |
| nested_reply_targets | disabled (no nesting evidence measured) |
| post_replies | disabled |
| dashboard_visual / slicer behaviour | unverified (owner should look at it) |

> The repo is public. This file contains no Sheet ID, script ID, deployment ID, group ID, token, key, email
> address, author ID or reply text. Private values live only in git-ignored files (§6.4).

**How to read this file:**
- §1 evidence
- §2 disposition of the 19 review findings
- §3 architecture
- §4 reply lifecycle
- §5 contracts
- §6 executor and checkpoints
- §7 phases and graph
- §8 recovery matrix
- §9 acceptance tests
- §10 live tests
- §11 decisions
- §12 blockers
- §13 readiness

---

## 1. Evidence

**Legend:**
- **[V3]** verified again during this revision (2026-10-24, read-only).
- **[V2]** verified during revision 2 the same day, not repeated.
- **[R]** recorded by others (Codex review, earlier audit), not repeated by me.

### 1.1 Repository, tooling, tests
| Fact | Evidence |
|---|---|
| `dev` = `f81c1155be0e76b6fe73b18792b5433cc3bd31bd`; no tracked change vs that commit; this file untracked | [V3] `git rev-parse`, `git status`, `git diff --stat` |
| `main` = `3c1a6b9` | [V2] |
| clasp 3.4.1 installed; authorized as the Sheet owner with the Google-provided client and default clasp scopes | [V3] `clasp show-authorized-user` |
| clasp auth file exists in the user profile (`.clasprc.json`, contents not read) | [V3] file present |
| A login with extra scopes is blocked by Google for this client | [V2] owner saw «تم حظر هذا التطبيق» |
| No `.clasp.json` in the project; no `apps_script/`; no `dumps/plan-exec/` | [V3] / [R] (expected for plan-only) |
| clasp supports `-P/--project <file or folder>`, `-A/--auth`, `create-deployment`, `update-deployment <deploymentId>`, `delete-deployment`, `list-deployments`, `clone-script [scriptId] [version]`, `push`, `pull` | [V3] `clasp --help` |
| The bound project's scriptId was supplied by the owner and is held only in private agent memory and in a scratch `.clasp.json` (session scratchpad, may be cleared) | [V3] |
| Full suite: 553 passed, 3 skipped (live opt-in), 0 failed, 348 s; this includes the 58 Node `.gs` tests | [V2] (not rerun in rev. 3) |
| Earlier audit: 551 passed, 2 failed (`.audit-venv` vs `.venv` launcher path; known flaky `test_sort_control_vanishes_after_newest_verified_by_time`), 3 skipped; both passed on targeted rerun | [R] |

### 1.2 Deployed Apps Script
| Fact | Evidence |
|---|---|
| Bound project: 7 deployments = 1 HEAD (test) deployment + 6 versioned web-app deployments at versions 1–6. Each versioned deployment has its own deployment ID; version numbers ≠ deployment IDs. | [V2] `clasp list-deployments` |
| Python `WEBAPP` (both scripts) → the deployment at **version 5** ("old link"). The owner published **version 6** ("new link") on 2026-10-24. | [V2] |
| HEAD, version 5 and version 6 source == repo `facebook_sheet_receiver.gs` (SHA-256 `42b9c9b8…`). Versions 1–4 are older code, still public. | [V2] `clasp clone-script` per version + byte diff |
| Manifest: V8, web app `USER_DEPLOYING`/`ANYONE_ANONYMOUS`, script time zone Asia/Riyadh, **no explicit `oauthScopes`**, no `@OnlyCurrentDoc`. The remote code file is named «الرمز» (default "Code"). | [V2] |
| Old and new links respond identically (ping, bad token, `pending_replies` = 91 rows, v1 keys) | [V2] HTTP read-only |
| Installable triggers: **unknown**. Listing them needs a scope clasp doesn't hold. v1 code defines no trigger handler. | [V3] limitation |

### 1.3 Live Sheet (view export; last modified 2026-10-23 10:13 UTC)
| Measure | Value | Evidence |
|---|---|---|
| Posts / Comments rows | 499 / 946 | [V2] (= [R]) |
| Duplicate/blank IDs | 0 | [V2] |
| Groups in Posts / Comments | 30 / 27 | [V2] |
| Non-empty `reply` | 112 = 21 `posted` + 91 blank status; 0 statuses without a reply | [V2] |
| `is_reply = yes` | 66 | [V2] |
| Posts `post content`: caption copy / link / empty | 310 / 181 / 8 | [V2] |
| `url == permalink` | 499/499 | [V2] |
| Orphan comments | 0 | [V2] |
| Comments `post content` = its post's | 946/946 | [V2] |
| Missing `author_id` | comments 41 (2 with drafts, both blank status), posts 10 | [V2] |
| Cells starting with `=` | 0 | [V2] |
| `received_at` | gviz type `date`, one day. **Time of day not visible** in a view export. | [V2] |
| Sheet time zone, grid size (Comments 947 = full), notes, validations, protections, named ranges, charts | **not visible** in a view export; the audit's values are [R] | measured in P3 `inventory` |
| Drafts' profile | 112 drafts, 110 authors, 45 posts, 15 groups, 112 distinct texts = the batch the owner ordered **not** to be sent | [V2] + private memory |

### 1.4 Provenance and time zone of historical rows
- [V3] **Provenance:** every Sheet post (499/499) and comment (946/946) ID is in the RUN6 group dumps, with an
  **identical** `created_time` string.
  - Other local dumps that share IDs are the `--dry` test runs of the same morning; they never wrote to the Sheet.
  - The Sheet was wiped before RUN6 (owner).
- [V3] **Capture-time OS zone = UTC:**
  - the RUN6 summary's file modification time is 13:23:26 UTC;
  - the run's local start stamp is 12:52:27, plus its 1,859 s duration = 13:23:26;
  - so local time equalled UTC during the run.
- [V3] **Browser zone:** `launch_browser.py` sets no TZ, so Chromium uses the OS zone.
- [V3] **Raw formats:**
  - all 973 RUN6 comment times are absolute aria-labels with a year;
  - all 506 post times lack a year (absolute without year, or relative). They were converted with
    `datetime.now()` (local = UTC) and the current-year rule, which is unambiguous for October dates;
  - malformed: 0.
- **Conclusion:** every historical `created_time` is a UTC wall-clock string.
- [V3] **`received_at` is not first ingestion.** The upsert refreshes it, and RUN6 re-sent a post's row with each
  comment chunk, so a historical value is that row's **last** RUN6 write time.

### 1.5 Code facts (unchanged since rev. 2, re-read in rev. 3)
- `reply_present` uses a substring match, accepts an unknown author, and matches any reply in the thread
  (`fb_return_replies.py:83-93`).
- Ingestion writes `reply`/`reply_status` when non-blank (`.gs:222-226`); the scraper always sends them empty.
- The v1 receiver excludes only `posted` from the pending list (`.gs:49`).
- Read paths create tabs and append headers (`.gs:39-40`).
- `received_at` is refreshed on update.
- The fake SpreadsheetApp grows without bounds and has no formula model.
- S1/S5/S7 are implemented (S1 not sent to the Sheet; S5 skips whole groups only; S7 is absent from the reply
  path).
- `group.join_button` probe exists.
- `archive_group.on_batch` is dead code.
- `PageRecorder.dump()` has unguarded `mkdir`/final `write_logs()`.
- The reply script's Sheet calls have no retry and send the token in a **GET query string**.
- The local verification tools (`debug/run5-ab/tools/*`, git-ignored) address tabs **by gid**.

---

## 2. Disposition of the 19 review findings

| # | Finding | Disposition | Where |
|---|---|---|---|
| 1 | Rehearsal shares Script Properties with production | **Confirmed.** Properties are project-wide; a different file ID doesn't isolate them. **Resolved:** per-spreadsheet namespaces, explicit validated targets, admin allowlist, no public target parameter, a production-unchanged proof. | §3.2, P4 |
| 2 | Deploy-then-maintenance gap; D29 not in P3 steps | **Confirmed.** **Resolved:** production URLs aren't touched before the cut-over. v2 is first exposed only through a new rehearsal deployment; the namespace gate is set before any versioned deployment is re-pointed; full inventory/re-point/verify/drain procedure. | §3.3, P3, P5 |
| 3 | Rollback re-enables unsafe v1 | **Confirmed.** **Resolved:** no automatic return to v1 code anywhere; a "safe-stop" state; restriction stores are monotonic; separate code/data/HEAD/properties/post-cut-over-write recovery. | §3.3, §8 |
| 4 | Swap not resumable per substep | **Confirmed.** **Resolved by design change:** converge-in-place (original sheets keep their IDs), per-tab substeps keyed by sheet ID + content hash, state inspected before acting, deletion only of tagged sheets. | §3.4, P5 |
| 5 | Interruptions rehearsed on a migrated copy | **Confirmed.** **Resolved:** a fresh copy per scenario (14 scenarios), fault injection only on allowlisted rehearsal targets, a real restore rehearsal, deep backup verification. | P4, §9 |
| 6 | Executor undefined; bootstrap conflict | **Confirmed.** **Resolved:** phase E (executor CLI) and phase B0 (narrow bootstrap commit of this file + CLAUDE.md + `.gitignore` only); "no production mutation" separated from local artefacts. | §6, B0, E, P0 |
| 7 | Blocked optional phases vs graph | **Confirmed.** **Resolved:** an explicit graph; finalizer F always runs; outcomes reported separately. | §7.1, §13 |
| 8 | Retries not idempotent; ambiguous attempts | **Confirmed.** **Resolved:** `request_id` replay cache, deterministic event IDs, idempotent copy/version/deployment creation, 1 + 3 attempts with explicit waits, retryable codes, time budgets + cursors. | §5 |
| 9 | Lease doesn't stop a stale runner | **Confirmed.** **Resolved:** fencing token, claim bound to account + draft fingerprint, renewal, server `prepare_submit` immediately before Enter, no submit without it, no exactly-once promise, four journal replay paths. | §4.2–4.3 |
| 10 | Nested-target / mention ambiguity | **Confirmed.** **Resolved:** comment-id/DOM-nesting evidence required; mention-only never counts; nested (reply) targets disabled until P6 measures nesting evidence; test list. | §4.4, §9 |
| 11 | `declined` only from pending/sending; onEdit ignores the status column | **Confirmed.** **Resolved:** `declined` reachable from sending/uncertain/sent/replied; onEdit handles both columns; server rescans; write order and precedence; event folding. | §4.1, §4.5 |
| 12 | Public ingestion can redirect approved replies | **Confirmed.** **Resolved:** **all writes need private keys** (ingest key, admin key); the public token is retired for writes; recipient binding is immutable per claim and after send; strict payload validation. | §3.5 |
| 13 | Script Properties capacity | **Confirmed** against the cited limits (9 KB/value, 500 KB/store, 6 min/execution: [R], consistent with my knowledge, not re-fetched). **Resolved:** the ledger tab is the primary store; properties hold only compact sharded mirrors with budgets, warning and fail-closed thresholds, a repair path, load tests. | §3.6 |
| 14 | clasp config not reproducible | **Confirmed** (no project `.clasp.json`). **Resolved:** `apps_script/.clasp.json`, generated staging `rootDir`, push allowlist, file inventory, readback hashes, key reuse, capability checks. | §3.7 |
| 15 | ID/secret policy contradictions | **Confirmed** (rev. 2 put a URL in code but forbade IDs in diffs). **Resolved:** one policy, with all deployment identity and credentials in private config; the legacy URL/token is acknowledged in history; no secrets in args/logs/GET. | §6.4 |
| 16 | Time zone and first-ingestion claims | Time zone **confirmed with stronger evidence** (§1.4). First-ingestion claim **rejected**: legacy values are last-write times. **Resolved:** semantics labelled per era. | §1.4, §11 D10 |
| 17 | Dashboard/smoke checks not executable | **Confirmed.** **Resolved:** exact `_activity` columns, independent expected counts, flush before read, pivot vs slicer vs chart vs visual split, network evidence defined (or weakened), dry-run limits stated. | §3.8, P6, P7 |
| 18 | Ownership after column insertion; flag semantics | **Confirmed.** **Resolved:** per-request header map, dynamic write runs, insert-only `received_at`; `--allow-post-replies` is a code flag, never authorization; exclusions restated. | §3.5, §11 |
| 19 | Test/readiness honesty | **Confirmed.** **Resolved:** test classes and a verdict based on procedure + safety, not just access. | §9, §13 |

---

## 3. Architecture (v2)

### 3.1 Components
| Component | Location | Notes |
|---|---|---|
| Receiver + setup | `apps_script/src/facebook_sheet_receiver.gs`, `apps_script/src/sheet_setup.gs` | Replaces the root `.gs` (tests updated). |
| Manifest | `apps_script/src/appsscript.json` | Pulled from remote, kept byte-equal unless a recorded change. |
| Generated config | `apps_script/push/zz_config.gs` (git-ignored, generated) | Key hashes, production file ID, config version. |
| Staging | `apps_script/push/` (git-ignored) | Exactly the allowlisted files (§3.7). |
| clasp project | `apps_script/.clasp.json` (git-ignored) | `rootDir: "push"`. |
| Executor | `tools/plan_exec/` (committed; no private data) | §6. |
| Private config/keys | `.secrets/` (git-ignored) | §6.4. |
| Checkpoints | `dumps/plan-exec/` (git-ignored) | §6.2. |

### 3.2 State isolation (finding 1)
- **Namespace** `ns = first 16 hex chars of SHA-256(spreadsheetId)`. Every per-spreadsheet Script Property key is
  `ns.<ns>.<name>`:
  - `SCHEMA_VERSION`, `MAINTENANCE`, `MIGRATION_RUN`, `MIGRATION_STEP.*`;
  - `LEDGER_HEAD` (count + last hash);
  - mirror shards `SENT.*`, `HELD.*`, `DNC.*`, `UNCERT.*`;
  - `CAPACITY`.
- Per-spreadsheet tabs (ledger, DNC, request cache) live **inside that spreadsheet**.
- **Project-wide on purpose:** `global.REPLIES_KILL` (one switch stops all reply actions everywhere) and
  `global.REHEARSAL_ALLOWLIST` (IDs + expiry). Production file ID and key hashes are in `zz_config.gs` (code, not
  properties).
- **Target resolution:**
  - **Ingestion (ingest key):** always the bound production spreadsheet. Any `target` field → `E_VALIDATION`.
  - **Admin actions:** an explicit `target` is required.
    - `target == production` allows production actions;
    - any other `target` must be in the allowlist, unexpired, and ≠ production;
    - its title must start with `CF-rehearsal-` or `CF-backup-`; `CF-backup-` targets are **read-only**
      (snapshot/hash actions only);
    - else `E_TARGET_NOT_ALLOWED`.
- **Allowlist management:** `allow_target` / `revoke_target` (admin key). Expiry 24 h. P4 revokes all at the end.
- **Rehearsal ingestion:** admin action `ingest_as {target}` runs the same ingestion code against an allowlisted
  rehearsal target. That target's own namespace is not in maintenance, so it works while production is in
  maintenance.
- **Fault injection** (`fault: {after_step, mode: throw|drop_response|corrupt}`) is honoured **only** for
  allowlisted rehearsal targets; `E_VALIDATION` otherwise. Tested.
- **Production-unchanged proof (P4):** before and after rehearsal:
  - production `snapshot_hash`: values, types, notes, formulas, number formats, validations, sheet IDs/names,
    developer metadata;
  - SHA-256 of the production namespace's property dump;
  - Drive `modifiedTime`.
  All three must be equal. Rehearsal never writes production, and reads don't change `modifiedTime`.
- **Correction found in P3 (2026-10-24):**
  - Pushing code to the bound script, or creating versions and deployments, bumps the spreadsheet's Drive
    `modifiedTime`: 10:13 → 14:44 UTC, while a full CSV export stayed byte-identical to P0's.
  - So `modifiedTime` is **not** a data-change signal around script operations. The production-unchanged proof and
    the P5 drain use `snapshot_hash` and the view-export comparison. `modifiedTime` is recorded only as context.

### 3.3 Deployment model and cut-over (findings 2, 3)
- **Before the cut-over, no versioned deployment changes.**
  - P3 pushes v2 to **HEAD**, creates version *N* (description `cf-v2-<run_id>`), and creates **one new rehearsal
    deployment** at *N* (description `cf-rehearsal-<run_id>`).
  - The versioned deployments 1–6 keep serving v1 code.
  - Because the project was already authorized, a new deployment of the same project with the same scopes needs no
    new consent; P3 proves it with capability checks.
- **Every v2 entry point is gated** (`doGet`, `doPost`, `onEdit`). Before any tab access it checks:
  - `api_version`;
  - auth;
  - the namespace `SCHEMA_VERSION == 2` for data actions;
  - `MAINTENANCE`;
  - `REPLIES_KILL` for reply actions.
  On production while it is still schema 1, v2 code is inert: ingestion → `E_SCHEMA`, `onEdit` → no-op. HEAD's
  `onEdit` is live as soon as it is pushed, which is why this gate is mandatory.
- **Cut-over order (P5):**
  1. Set production `MAINTENANCE=true` and `REPLIES_KILL=true` through the rehearsal deployment.
  2. Re-point every versioned deployment to *N*. Order: the deployment the current Python uses, then the
     production deployment (D29), then the remaining older ones.
     - Record each `{deployment_id (private), from_version, to_version, ping_result}`.
     - Each re-pointed URL must ping `api_version 2, maintenance true`.
  3. **Drain v1 work in flight:** wait 7 min after the last re-point (above the 6-min execution limit), then
     confirm stability twice, 60 s apart: production `snapshot_hash` and Drive `modifiedTime` unchanged. At most 3
     drain cycles, else safe stop.
  4. Back up, then migrate (§3.4).
- **Interrupted re-pointing:** on resume, `list-deployments` tells which deployments are at *N*; continue with the
  rest. Any URL still at version ≤ 6 is listed as `exposed_v1` in the checkpoint, and P5 can't pass while that
  list is non-empty.
- **Safe-stop state (SS)**, the only automatic recovery after the cut-over begins:
  - all versioned deployments on the latest v2 version;
  - production `MAINTENANCE=true`, `REPLIES_KILL=true`;
  - restriction stores untouched;
  - no reply runner process;
  - data either still v1 or restored to v1 (§3.4).
- **No automatic return to v1 code exists in this plan.** v1 code ignores held/DNC/maintenance and treats
  blank-status drafts as sendable.

### 3.4 Migration: converge-in-place (findings 4, 11)
- The original `Posts` and `Comments` sheets **keep their sheet IDs (gids)**. External gid links, gid-based CSV
  exports and the local tools keep pointing at the live data; consumers see the v2 column layout.
- **Per tab (Posts, then Comments), each substep verified before its checkpoint:**

| Substep | Action | Done when (inspected in the workbook, not taken from the checkpoint) |
|---|---|---|
| M1 build | Create `__v2_build_<tab>` (developer metadata `cf.created=<run_id>:build:<tab>`), write the v2 transform of v1 in chunks of 500 rows | scratch sheet exists with the marker, row count = source, `validate` mapping report OK |
| M2 backup | Duplicate the original to `<tab>__v1_backup` (marker `…:backup:<tab>`), hidden | `snapshot_hash(backup) == snapshot_hash(original at M2 start)` (recorded in M1) |
| M3 converge | Make the original sheet's contents equal the scratch sheet: values, notes, formats, validation, columns sized; chunked, cursor | `content_hash(original) == content_hash(scratch)` |
| M4 cleanup | Delete the scratch sheet **only if** its gid matches the recorded gid **and** its marker matches **and** M3 equality holds | scratch absent |

- **Resume:**
  - read the actual workbook (gid → name, markers, hashes);
  - for each tab, classify `original == v1 snapshot` / `== scratch` / `other`;
  - continue from the first substep not proven done;
  - M3 is idempotent ("converge to scratch") and is simply redone if partial.
- **Lost response after success:** the same `request_id` returns the stored result (§5.3).
- **Never** delete or rename by name alone or from a stale checkpoint. Only the default empty tab is deleted (Q5),
  matched by its gid recorded in P3, re-checked empty (values, notes, formulas, validations) and with no
  dependents.
- **Dependencies:**
  - P3 `inventory` lists formulas referencing these tabs, named ranges, charts, filters, protections, conditional
    formats, data validations and developer metadata.
  - Expected today: none besides the tabs themselves.
  - Anything found becomes a P5 entry condition: it must be preserved (re-applied to the new column positions) and
    verified, or P5 stops with `blocked: unknown-dependency`.
  - The local gid-based tools are updated in P2 to read by header name.
- **Legacy mapping (applied in M1):**
  - `posted` → `sent`, ledger `legacy-sent` (event ID derived from row ID);
  - non-empty reply + blank status → `held`, ledger `held-legacy`;
  - blank/blank → blank;
  - `failed` → `failed`;
  - `declined` → `declined` + DNC;
  - any other value → **stop**.
  Mirrors are updated after the ledger.
- **Dates (M1):**
  - `created_time` text → Date, parsed as **UTC** (§1.4);
  - `received_at` copied as the same Date value (instant preserved);
  - unparseable values preserved verbatim and listed (0 today).
- **Validation** compares every preserved field (IDs, author, text, URLs, relationships, reply, statuses per
  mapping, notes, owner columns, row order), dates as UTC instants, row counts, and a cross-tab relation check
  (every comment's `post_id` exists).

### 3.5 Authentication, ownership and target binding (findings 12, 18)
- **Keys:** two private keys, 256-bit random, base64url.
  - **INGEST** (scraper) and **ADMIN** (reply runner + executor).
  - Only their SHA-256 hashes are in `zz_config.gs`.
  - Sent only in POST bodies, never in URLs or command lines.
- **The public token in git history is retired for writes:** v2 accepts it for nothing. `ping` needs no
  credential.
  - The owner's 2026-10-21 decision (public repo, hardcoded token) isn't reversed: the repo stays public and the
    old value stays in history.
  - v2 simply stops trusting it, because v2 adds reply sending and a public writer could otherwise steer replies.
- **Ingestion rules:**
  - only scraper-owned fields are accepted; `reply`, `reply_status` and unknown keys are rejected
    (`E_VALIDATION`);
  - IDs match `^\d{5,25}$`;
  - URLs must be `https://www.facebook.com/groups/<group_id>/posts/<post_id>/…` whose IDs equal the row's IDs;
  - group ID consistency across a payload;
  - ≤ 25 posts and ≤ 500 comments per request, body ≤ 2 MB, each text ≤ 50,000 characters.
- **Writes:**
  - the header map is read on every request;
  - for each updated row, only changed scraper-owned cells are written, grouped into contiguous runs computed from
    the current header positions, so inserted owner columns are skipped;
  - `received_at` is written on insert only;
  - formats, validation and checkboxes are re-applied to the written ranges.
- **Binding:**
  - a claim binds `account_id`, kind, target ID, `target_author_id`, target URL hash, reply SHA-256 and the row's
    draft version (ledger event count for that target);
  - `prepare_submit` re-reads them and refuses on any change;
  - once a target has a `sent`, `legacy-sent` or `uncertain` event, ingestion may **not** change its `author_id`,
    IDs or URL. A conflicting update is dropped and logged as a `conflict` event.

### 3.6 Durable restriction storage and capacity (findings 11, 13)
- **Primary store:** hidden `_replies` ledger tab, append-only.
  - Each event carries `event_id` (deterministic or `request_id`), `prev_hash`, `hash`.
  - `LEDGER_HEAD` (count + last hash) is kept in the namespace properties.
  - A mismatch (edited or deleted rows) sets `REPLIES_KILL`, and reply actions return `E_INTEGRITY` until
    `repair_check` passes.
- **DNC:** hidden `_dnc` tab of events (`add`, `remove` with authorization reference). State per author = fold of
  its events in order. Removal isn't in this plan's scope.
- **Mirrors:** sharded properties for SENT, HELD, DNC and UNCERT.
  - IDs base36-encoded, comma-joined; a shard ≤ 8,000 bytes (≈ 650 IDs).
  - Budget for all mirrors ≤ 350 KB per namespace (≈ 29,000 IDs; today 112).
  - ≥ 70 % → `capacity_warning` in every response.
  - ≥ 90 % → `claim` refused (`E_CAPACITY`), ingestion continues. Scaling beyond that is future work.
  - Rehearsal namespaces are deleted at the end of P4.
- **Write order:** ledger event → mirror shard.
- **Disagreement:** for restrictions, the **union** wins; any store saying restricted means restricted. Reads
  repair the store that is missing the entry (append `repair` event / add to shard).
- **Detection is server-side:** every `pending_replies`, `claim` and `prepare_submit` rescans the row's current
  cells (catches an owner-selected `declined` and missed onEdit events).
- `sweep_restrictions` (admin) rescans all rows at the start of each reply run.

### 3.7 clasp reproducibility (finding 14)
- **Every clasp command** runs as `clasp -P apps_script/.clasp.json …` from the project root. Scratch pulls use
  `clone-script <scriptId> [version]` into a new empty temp folder; nothing relies on the current directory.
- **`apps_script/.clasp.json`:** `{scriptId (from .secrets/config.json), rootDir: "push"}`.
- **Push allowlist / expected remote inventory:** exactly `facebook_sheet_receiver`, `sheet_setup`,
  `zz_config`, `appsscript`.
  - The staging folder is rebuilt from scratch for every push.
  - The remote «الرمز» file disappears on the first push; that's recorded, and its bytes are kept in the P3
    checkpoint for HEAD restoration.
- **Readback:** after every push, `clone-script` HEAD into a temp folder and compare per-file SHA-256 with staging.
  After a version is created, clone that version and compare again.
- **Manifest:** byte-equal to the pulled one. v2 uses only SpreadsheetApp, LockService, PropertiesService,
  CacheService, Utilities and ContentService.
- **Effective permissions** aren't proven by manifest text. The rehearsal deployment's `capabilities` action
  exercises each one:
  - `SpreadsheetApp.openById` on an allowlisted copy (full spreadsheets scope);
  - developer metadata;
  - properties read/write in a scratch namespace;
  - lock; digest; cache.
  Any authorization error → `blocked: B5` before any production change.
- **Triggers:** installable triggers can't be listed with the current scopes (§1.2). v2 needs none; a stale
  installable trigger pointing at a removed function would only log errors. Recorded as an accepted unknown.
- **Keys** are generated once (B0) and reused. Their fingerprints (first 12 hex of the hash) are checked against
  `zz_config.gs` on every resume. Rotation only through an explicit `rotate-keys` step, recorded.

### 3.8 Dashboard (finding 17)
- **`_activity` (hidden data tab):** one array formula stacking non-blank Posts and Comments rows.

| Col | Name | Type | Definition |
|---|---|---|---|
| A | kind | text | `post` / `comment` |
| B | id | text | post_id / comment_id (rows with a blank ID excluded) |
| C | group_id | text | as stored |
| D | group_label | text | `group_name & " · " & group_id` (unique even when names collide) |
| E | created_at | datetime | `created_time` |
| F | created_date | date | `INT(E)` in the Sheet zone |
| G | weekday_no | number | `WEEKDAY(F, 2)` (1 = Monday) |
| H | weekday | text | `TEXT(F, "dddd")` |
| I | received_date | date | `INT(received_at)` |
| J | reply_status | text | status, or `(none)` |

- **Dashboard tab (visible):**
  - KPI tiles (formulas, **unfiltered**, labelled "all data").
  - Three slicers on `_activity!A1:J`: I (received date), H (weekday), D (group).
  - Five pivot tables, all on source `_activity!A1:J` with a filter `B is not empty`:
    1. posts per created_date;
    2. comments per created_date;
    3. rows per weekday sorted by G;
    4. top 10 groups by posts;
    5. status counts.
  - Five charts, each sourced from one pivot's output range.
- **KPIs:** Posts, Comments, Groups (unique `group_id`), Sent (`sent` + `replied`), Replied, Pending (`pending`
  only), Held, Uncertain, Failed, Declined. Blank-ID rows excluded; empty data shows 0.
- **Labels:** "received (first seen; rows before the cut-over show their last RUN6 write)".
- **Verification (P4) after `SpreadsheetApp.flush()`:**
  - (a) Pivot correctness: pivot output equals counts computed independently by the executor from an exported
    copy, for 4 fixed filter scenarios (none / one group / one weekday / one received date).
  - (b) Slicer behaviour: set each slicer's criteria programmatically, flush, re-read the pivots, and compare with
    the independent counts for that filter. A pivot whose expected count equals its unfiltered count passes when
    equal; "every chart must change" isn't required.
  - (c) Chart sources: each chart's ranges equal its pivot output range, with the expected chart type.
  - (d) Visual usability: **not automatable**, reported as unverified.
  - If (b) values can't be read programmatically: report "slicer behaviour unverified", (a) and (c) still
    required.

---

## 4. Reply lifecycle (single source of truth)

### 4.1 States and transitions
**Statuses:** `pending`, `sending`, `sent`, `uncertain`, `replied`, `declined`, `failed`, `held`.

| From → To | Trigger | Writer |
|---|---|---|
| blank → `pending` | owner types a reply (onEdit), or server normalization of post-cut-over blank+reply rows | onEdit / server |
| `pending` → blank | owner clears a reply while `pending` | onEdit / server |
| `pending` → `sending` | `claim` OK (§4.2) | server |
| `sending` → `pending` | claim released or expired **with no `submit_authorized` event** (nothing could have been sent) | server |
| `sending` → `sent` | confirmed (§4.4) | server via runner |
| `sending` → `uncertain` | a `submit_authorized` event exists and no confirmation (timeout, crash, lost lease) | server / runner |
| `sending` → `failed` | technical failure **before** `submit_authorized` | runner |
| `uncertain` → `sent` | reconciliation finds our reply (strict match) | runner |
| `uncertain` → `failed` (`unconfirmed-not-found`) | two reconciliations ≥ 10 min apart, full expansion, nothing found | runner |
| `failed` → `pending` | owner's intentional retry. Allowed only if the target has no unresolved uncertain attempt younger than 24 h; the runner always reconciles first. | owner |
| `sent` → `replied` | §4.4 evidence | server on ingestion |
| `sending`/`uncertain`/`sent`/`replied` → `declined` | explicit rejection on **our** reply article (matched by reply ID): right after submit, during reconciliation, or on a later visit or scrape (scraper v2 field `moderation` on our own articles) | runner / server |
| `pending`/`failed` → `declined` | owner selects `declined` | owner (onEdit + server rescan) |
| `held` → anything | `release_hold` (admin + reason): **outside this plan's scope** | — |
| `declined` → anything | refused; onEdit reverts manual edits with a note; DNC unchanged | — |

All status writes are compare-and-set on `{expected_status, claim_id, fence}` (§4.2). A stale request can't
overwrite a newer state, including `declined`.

### 4.2 Claims, fencing, submission
1. **`claim`:** checks row eligibility, `author_id` present, not own account, not SENT/HELD/DNC/UNCERT, no live
   lease, `REPLIES_KILL` off, capacity OK. Returns `{claim_id, fence (monotonic per target), lease_until (+5 min),
   binding}`.
2. **`renew`:** every 60 s while working.
3. **`prepare_submit {claim_id, fence, binding}`:** called immediately before Enter. The server re-checks the lease,
   the fence, every binding field against the current cells, the restrictions and the kill switch, then records
   `submit_authorized` with a 30 s deadline.
   - The runner presses Enter **only** inside that window.
   - Any change (reply text, author, URL, DNC) → `E_CONFLICT`; the runner aborts with nothing typed sent.
4. **Server unreachable at `prepare_submit`:** no submission. The claim expires and returns to `pending` (no
   `submit_authorized`).
5. **No exactly-once promise.** Every attempt with `submit_authorized` is treated as possibly delivered until a
   strict match decides otherwise.

### 4.3 Local journal and replay
`dumps/replies/journal.jsonl` (fsync per line): `intent` → `authorized` → `submitted` (just before Enter) →
`confirmed {reply_id, fb_time}` → `marked`.

| Last entry for an attempt | Replay action |
|---|---|
| `intent` only | `release_claim(claim_id, fence)`. A fence mismatch or expiry is ignored. Nothing was sent. |
| `authorized` or `submitted`, not confirmed | Read-only reconciliation of that target first. Found → `mark sent` with reply ID. Not found → `mark uncertain` (never `failed`, never `pending`). |
| `confirmed`, not marked | `mark_status sent` with the journal's original `request_id` (replay-safe) |
| `marked` | nothing (compacted) |

Replay runs before any new work, and the runner never re-sends during replay.

### 4.4 Relationship evidence (finding 10)
- **Strict match** (confirmation, already-posted, reconciliation). All must hold:
  - the author's profile ID **equals `c_user`** (unknown author = no match);
  - the normalized full text equals `reply` or `<mention text> + " " + reply`. Normalization: NFC, collapse
    whitespace, trim, strip bidi marks. Equality, not substring;
  - placement:
    - **top-level comment target T:** the article's own link has `comment_id=T` and a `reply_comment_id`;
      unambiguous;
    - **post target (our top-level comment):** the article's own link has the post ID and `comment_id=<our id>`
      without `reply_comment_id`, and it appears under that post's root;
    - **nested target (T is itself a reply):** requires DOM nesting, meaning the article's closest ancestor comment
      article is T. Mention-of-author is never enough. Until P6 measures that nesting is exposed (`nest_parent_id`
      populated), **nested targets are not eligible** (`failed`, note `nested-target-disabled`);
  - for confirmation only: the reply ID is new since the pre-submit set.
- **`replied`:**
  - a scraped comment C with `C.author_id == target_author_id` (non-empty), newer than our `sent_at`, and **either**
    `C.nest_parent_id == sent_reply_id` (DOM nesting) **or**, for post targets, C's thread parent is our sent
    comment ID;
  - mentions alone never count;
  - rows with `legacy-sent`, an unknown reply ID or an empty `author_id` are never inferred.
- **Missing or hidden reply ≠ non-delivery:** Facebook may hold or hide a comment. That's why manual retry stays
  subject to uncertain-attempt history (§4.1).

### 4.5 onEdit and do-not-contact
- **onEdit:** iterates `e.range` ∩ (`reply` ∪ `reply_status`), multi-row pastes included.
  - **`reply` edits:** blank→`pending`; cleared while `pending` → blank; ignored on `sent`/`declined`/`held`, noted
    `edited-after-send` where relevant.
  - **`reply_status` edits:**
    - owner `declined` → ledger `owner-declined` + DNC add;
    - owner `pending` on a `held`/`sent`/`replied`/`uncertain`/`declined` row → reverted with a note;
    - other owner choices are recorded as `owner-status` events.
  - onEdit uses `LockService.getDocumentLock().tryLock(5000)`. If it can't get the lock, it does nothing and the
    server rescan corrects later.
  - Script/API writes never trigger onEdit; nothing depends on it (§3.6 rescans).
- **DNC:**
  - an `author_id` is required;
  - a decline on a target without `author_id` is recorded on that row only;
  - blank IDs never collapse into one identity, and display names are never used as identity;
  - a decline detected mid-run is added to the runner's local block set and the server at once, so the run's
    remaining targets by that author are skipped.

### 4.6 Runner order and limits
1. Journal replay.
2. Account check (login/checkpoint URL or no `c_user` → exit 3, stop).
3. Then per row:
   1. claim;
   2. target exists;
   3. own comment → `failed own-comment`;
   4. membership («انضمام إلى المجموعة» → `failed not-a-member`, never join);
   5. DNC;
   6. already-posted (strict);
   7. type;
   8. pre-Enter exact text check;
   9. `prepare_submit`;
   10. Enter;
   11. confirm (15 s);
   12. mark.

**Limits:** random 20–60 s gap; `--max-replies` (default 5); `--dry-run` (no «رد» click, no typing).

**Content gate:** the executor reads each draft before a live send. Unsolicited romantic, solicitation or bulk
outreach to individuals → `failed content-refused` and the reply phase stops.

---

## 5. Contracts

### 5.1 Request envelope (POST JSON only; no secrets in URLs)
```json
{"api_version": 2, "request_id": "<uuid4>", "action": "<name>",
 "auth": {"kind": "ingest|admin", "key": "<from .secrets, never logged>"},
 "target": "<spreadsheet id, admin actions only>",
 "payload": {}, "cursor": null, "budget_ms": 240000}
```
- `ping` may be a GET without auth.
- Responses: `{ok, api_version, request_id, code, retryable, replayed, result, cursor, warnings}`.

### 5.2 Error codes
| Code | Meaning | Retry |
|---|---|---|
| `E_AUTH`, `E_VERSION`, `E_VALIDATION`, `E_TARGET_NOT_ALLOWED` | caller error | no |
| `E_SCHEMA`, `E_MAINTENANCE`, `E_KILLED`, `E_INTEGRITY`, `E_CAPACITY` | gate closed | no; the phase stops or waits per its rules |
| `E_CONFLICT`, `E_NOT_ELIGIBLE`, `E_DNC`, `E_HELD` | state | no |
| `E_LOCK_BUSY` | script lock not acquired in 30 s | yes |
| `E_BUDGET` | time budget reached; `cursor` returned | yes: continue immediately with the cursor (not counted as a failure; ≤ 50 continuations per step) |
| `E_INTERNAL` | unexpected exception | yes, once |

### 5.3 Idempotency
- **State-changing requests:** stored in a hidden `__requests` tab of the target spreadsheet as
  `{request_id, action, result (≤ 40 KB, larger results hashed and summarized), created_at}`.
  - A repeat returns the stored result with `replayed: true`.
  - Retention 30 days; pruned by `ingest`/`migrate` when > 5,000 rows.
- **Ledger events:** `event_id = request_id`, or deterministic for migration (`legacy:<kind>:<row id>`).
  Duplicates are ignored.
- **Drive copies:** title `CF-backup-<run_id>-<label>` / `CF-rehearsal-<run_id>-<scenario>`. Before creating,
  search the exact title; reuse a single match; more than one → stop.
- **Versions/deployments:** description `cf-v2-<run_id>` / `cf-rehearsal-<run_id>`. List first and reuse an
  existing match.
- **Migration, format, dashboard steps:** converge semantics (§3.4), safe to repeat.

### 5.4 Retry and budgets
- **Transport retry** (same `request_id`): 1 initial attempt + up to 3 retries, waiting 5 s, 15 s and 45 s (±20 %
  jitter) before retries 1, 2, 3.
  - Retryable: HTTP 429, 500, 502, 503, 504; Google's transient 404 «يتعذر فتح الملف»; network errors; client
    timeout (330 s).
  - Application retry: only codes marked retryable in §5.2.
- **Server budget:** 240 s per call (below 6 min). Chunks: 500 rows for writes, 2,000 rows for reads/hashing.
- **Executor time budget per phase:** P1/P2 4 h, P3 30 min, P4 3 h, P5 1 h, P6 20 min, P7 20 min. Exceeding it →
  phase `failed` (resumable).

---

## 6. Executor, checkpoints, bootstrap

### 6.1 Executor
- **Who executes:** the Claude Code agent, plus `tools/plan_exec/` (Python CLI). The CLI owns state, locks,
  retries, HTTP/clasp actions, verification math and reports. Connector actions (Drive copy, search, trash,
  metadata) are done by the agent and recorded with `plan_exec record`.
- **CLI:** `.venv\Scripts\python.exe -m tools.plan_exec` with `status`, `next`, `run <phase> [--step S]`,
  `record <phase> <step> --json FILE`, `report`, `lock --show|--break-stale`.
- **Graph:** §7.1, encoded in code; `next` refuses a phase whose prerequisites aren't met.
- **Checkpoints:** `state.json` written atomically (temp file + `os.fsync` + `os.replace`); a schema version field;
  invalid or unknown version → stop.
- **Lock:** `dumps/plan-exec/run.lock` = `{pid, process_start_time, hostname, phase, acquired_at}`.
  - Stale only if the PID isn't running, or is running with a different start time.
  - `--break-stale` records the event.
  - `fb_groups_to_sheet.py` and `fb_return_replies.py` v2 also honour it (exit 4 if held by someone else).
  - Cross-machine and public callers are covered by the server gates (§3.2–3.3), not by this file.
- **Signals:** SIGINT/SIGBREAK → mark the current step `interrupted`, release the lock, exit 130.
- **Restart:** `status` shows the interrupted step; `run` resumes through that phase's resume rule.
- **Retry budgets:** finite per step (§5.4, §7); exhausted → `failed`.
- **Reports:** `dumps/plan-exec/report-<run_id>.json` + `.md` with outcome flags (§13), per-phase states, counts
  and failures. No private values; fingerprints only.

### 6.2 Checkpoint schema (per phase)
`{phase, state ∈ {not_started, running, passed, failed, rolled_back, blocked}, started_utc, ended_utc,
git_sha, gs_sha256 (staging + readback), apps_script: {version_before, version_new, deployments: [{id_ref,
from, to, ping}]}, schema_version, backups: [{label, file_ref, deep_hash}], counts {...}, validations [{name,
ok, detail_ref}], retries_used, steps [{name, state, request_id, result_hash}], failure {code, message},
blocked_by}`.
- `id_ref` / `file_ref` are keys into `.secrets/ids.json`, so IDs aren't duplicated in `state.json`.

### 6.3 Bootstrap B0 (narrow; resolves the untracked-plan conflict)
1. On `dev`; HEAD = `f81c115` or a recorded descendant; **tracked files clean**; untracked files outside
   `.gitignore` = exactly `{ENHANCEMENT_PLAN.md}`. Anything else → `blocked: dirty-tree`.
2. Secret/ID scan of `ENHANCEMENT_PLAN.md`. The patterns are kept in the scanner's private config, never written
   in tracked files: the legacy token value, the Apps Script deployment-ID prefix, the Sheet/script IDs, all
   `groups.txt` IDs, email addresses, `.secrets/` key values. Expected: 0 hits.
3. Edit `.gitignore` (add `.secrets/`, `apps_script/.clasp.json`, `apps_script/push/`) and CLAUDE.md (changelog
   entry "plan revision 3 adopted").
4. `git add -- ENHANCEMENT_PLAN.md CLAUDE.md .gitignore`; verify `git diff --cached --name-only` is exactly those
   3 files; `git commit -F <msgfile>`; `git push origin dev`.
5. Create `.secrets/` (ACL restricted to the current Windows user via `icacls`). Write `config.json` / `ids.json`:
   - scriptId, production file ID and production deployment ID, from private agent memory and the version-6 URL
     the owner supplied;
   - verify with `clasp list-deployments <scriptId>` that the production deployment exists at version 6 and the
     Python-referenced one at version 5.
6. Generate `admin.key` / `ingest.key` **only if absent**; record their fingerprints.

### 6.4 Configuration and secrecy policy (finding 15)
| Class | Examples | Storage | Never in |
|---|---|---|---|
| Secrets | admin/ingest keys, clasp OAuth (`.clasprc.json`) | `.secrets/`, user profile | repo, plan, logs, command-line args, URLs, checkpoints (fingerprints only) |
| Private identifiers / personal data | Sheet/script/deployment IDs, group IDs, author IDs, reply texts | `.secrets/ids.json`, Sheet, backups, private snapshots | repo, plan, logs (short fingerprints allowed) |
| Legacy values already public | old deployment URL, old token (git history) | — | not repeated in new commits; v2 trusts neither |

- **Python v2:** reads `WEBAPP` from `.secrets/config.json` or `FB_SHEET_WEBAPP`, and keys from `.secrets/` or
  `FB_SHEET_INGEST_KEY` / `FB_SHEET_ADMIN_KEY`. The hardcoded URL and token are removed from source (P2).
- **Private snapshots:** `dumps/plan-exec/private/` may hold exported values including reply texts (needed for
  independent verification). `state.json` holds none. Retention 30 days, then deleted by the owner or a later
  instruction; not by this plan.
- **Drive backup copies:**
  - created by the connector's account;
  - if that account isn't the Sheet owner, each copy is shared with the owner as editor (needed for script-side
    restore);
  - kept until a later instruction;
  - rehearsal copies that passed are trashed at the end of P4, failing ones kept for diagnosis.
- **Local** (Google Drive for desktop): `.secrets/` and `dumps/` sync to the owner's Drive like `profile/`
  already does (existing owner-accepted risk).

---

## 7. Phases

### 7.1 Dependency graph
```
B0 → E → P0 → P1 → P2 → PX → P3 → P4 → P5 ─┬→ P6 → P7
                                            └──────────→ F (finalizer)
any phase failed/blocked ────────────────────────────→ F
```
- **F always runs last**, whatever the outcome; it records and reports, and never changes production.
- **Gates:**
  - P6 requires P5 passed and B3.
  - P7 requires P6 passed.
  - P6 `blocked` → P7 `blocked` (not run), F runs.
  - P5 `failed` → SS (§3.3) is applied by P5 itself, then F.
- **Automatic advance** happens only along edges whose source is `passed`. `failed` never advances except to F.

### 7.2 Phase table

#### B0 Bootstrap
- **Entry:** none.
- **Actions:** §6.3.
- **Success:** commit pushed; `.secrets/` populated; deployments verified.
- **Failure:** dirty tree; scan hit; deployment mismatch.
- **Retry:** push 1 + 3.
- **Checkpoint:** commit SHA, key fingerprints.
- **Resume:** re-run (each step checks whether it's done).
- **Rollback:** `git revert` of the bootstrap commit (no production effect).
- **Next:** E.

#### E Executor (offline)
- **Entry:** B0 passed.
- **Actions:** implement `tools/plan_exec/` (§6.1) with tests: atomic writes under a simulated crash, schema
  validation, stale-lock rules, signal handling, graph enforcement, retry/budget math, report rendering.
- **Success:** tests green.
- **Failure:** red after 3 fix cycles.
- **Retry:** 3 fix cycles.
- **Checkpoint:** SHA.
- **Resume:** from the last commit.
- **Rollback:** revert.
- **Next:** P0.

#### P0 Preflight
- **Entry:** E passed.
- **No production mutation.** Local test artefacts and checkpoints are allowed.
- **Actions:**
  - git clean (tracked) on `dev`;
  - no project process running (scraper, replies, `reply_run`, `live_run`);
  - full suite;
  - view-export re-measure of §1.3;
  - ping both links;
  - `clasp show-authorized-user` = owner;
  - `list-deployments` = recorded inventory;
  - Drive connector metadata (B2) and its account identity;
  - CDP + `c_user` (B3, live phases only);
  - Node ≥ 23.
- **Success:** recorded; suite green (the known flaky test may be rerun once).
- **Failure:** dirty tree, conflicting process, suite red, deployment inventory changed unexpectedly (a new
  version or re-pointed deployment not made by us → `blocked: remote-changed`).
- **Retry:** suite rerun once for the flaky test.
- **Checkpoint:** counts, inventory, blocker table.
- **Resume:** re-run.
- **Rollback:** none needed.
- **Next:** P1.

#### P1 Apps Script v2 (offline)
- **Entry:** P0 passed.
- **Actions:**
  - implement §3–§5 in `apps_script/src/`;
  - extend `gas_fake.mjs`:
    - max rows/cols with errors outside the grid, `insertRowsAfter`;
    - formula interpretation (two modes);
    - Dates, notes, validation, checkboxes;
    - developer metadata, `getSheetById`;
    - Script Properties with **9 KB/value and 500 KB/store enforcement**;
    - LockService contention, CacheService;
    - slicer/pivot/chart objects (configuration only);
  - a synthetic full-size snapshot (shape of §1.3, synthetic text and IDs).
- **Success:** every §9 class-N test for the `.gs` green; v1 contract tests updated in the same commit; defect tests
  shown failing against the v1 `.gs`.
- **Failure:** red after 3 fix cycles.
- **Retry, checkpoint, resume, rollback:** as E.
- **Next:** P2.

#### P2 Python v2 (offline)
- **Entry:** P1 passed.
- **Actions:**
  - **scraper:** v2 payload (ingest key, `group_name`, `media_url`, `post_text`, `url`, boolean `is_reply`, ISO
    8601 times with offset, `nest_parent_id`, `mentions`, `moderation`); no reply fields; lock; drop dead
    `on_batch`; config from `.secrets/`;
  - **reply runner:** §4 in full, S7 in the reply path, `--dry-run`, `--max-replies`, pacing, journal, network
    evidence counter (§10.2), `--allow-post-replies` (code flag, default off);
  - **`browser_debug.py`:** `post.composer` probe, guarded `dump()`;
  - **local tools:** read by header name instead of gid;
  - **fake Facebook:** the cases listed in §9.
- **Success / failure / retry / checkpoint / resume / rollback:** as P1.
- **Next:** PX.

#### PX Executor production actions (offline)
- **Entry:** P2 passed.
- **Actions:** implement and test, against fakes of clasp, HTTP and Drive:
  - P3–P7 step functions;
  - deployment inventory and re-point with interruption at every deployment;
  - idempotent copy, version and deployment creation;
  - replay after a lost response;
  - drain logic;
  - SS application;
  - report outcome flags.
- **Success:** tests green.
- **Rest:** as P1.
- **Next:** P3.

#### P3 Push to HEAD + rehearsal deployment
- **Entry:** PX passed; B1, B2 OK; lock held.
- **Actions:**
  1. Clone HEAD and versions 5, 6 into temp folders. They must still equal the recorded hash; otherwise
     `blocked: remote-changed`.
  2. Keep the HEAD file bytes for restoration.
  3. Build staging, `clasp -P … push`, readback (§3.7).
  4. `create-version` (idempotent).
  5. `create-deployment` rehearsal (idempotent).
  6. Rehearsal URL `ping` → v2.
  7. `inventory` of production through the rehearsal deployment (read-only): grid, zone, notes, validations,
     protections, named ranges, charts, dependents, `received_at` fractions, gids, the default tab's emptiness.
  8. `capabilities` against a fresh allowlisted Drive copy.
  9. Verify the versioned deployments are unchanged.
  10. Verify production's v1 ping is still v1 and production `snapshot_hash` is unchanged.
- **Success:** all checks pass.
- **Failure:** readback mismatch, capability error (→ `blocked: B5`), production changed, deployments changed.
- **Retry:** push/version/deploy 1 + 2.
- **Checkpoint:** hashes, version *N*, rehearsal deployment ref, inventory.
- **Resume:** each step checks whether it's done (by hash, description, ping).
- **Rollback:** push the kept HEAD bytes back (readback must equal), delete the rehearsal deployment (ours), clear
  the scratch namespaces. Production URLs were never touched → `rolled_back`.
- **Next:** P4.

#### P4 Real-Sheets rehearsal
- **Entry:** P3 passed.
- **Actions:** for each scenario in §9 class G, in order:
  1. Drive copy of production `CF-rehearsal-<run_id>-<Rxx>`.
  2. `allow_target`.
  3. Run the scenario.
  4. Verify.
  5. Record.
  6. `revoke_target`.
  Then the production-unchanged proof (§3.2), deletion of rehearsal namespaces, and trash of passed copies.
- **Success:** every scenario passes; production proof equal.
- **Failure:** any scenario fails, or the production proof differs (→ P3 rollback + `failed`).
- **Retry:** a failing scenario may rerun once on a fresh copy after a code fix (back through P1/P2/PX/P3 tests and
  push); at most 2 rehearsal rounds.
- **Checkpoint:** per scenario result, copy refs.
- **Resume:** discard a half-run scenario's copy (keep it, marked failed) and restart that scenario on a fresh copy.
- **Rollback:** P3 rollback.
- **Next:** P5.

#### P5 Production cut-over and migration
- **Entry:**
  - P4 passed;
  - production `snapshot_hash` and `modifiedTime` equal P3's values (only our read-only actions since);
  - no conflicting process;
  - the inventory has no unknown dependency.
- **Actions:**
  1. Gate: `MAINTENANCE=true`, `REPLIES_KILL=true` (production namespace).
  2. Re-point every versioned deployment (§3.3), verifying each URL.
  3. Drain.
  4. Backup A: Drive copy `CF-backup-<run_id>-pre`, allowlisted read-only. Its deep hash must equal production's.
  5. Re-measure counts. Record the held set and the sent set (private).
  6. M1–M4 for Posts, then Comments.
  7. `format`, `build_dashboard`, `verify` twice.
  8. Delete the default tab if empty (by gid).
  9. `MAINTENANCE=false`, `REPLIES_KILL=false`.
  10. Python v2 is already on `dev`; the scripts read the production URL from private config.
- **Success:**
  - `verify` passes twice; KPIs equal independent counts;
  - all versioned URLs ping v2; `exposed_v1` empty;
  - file ID, bound script and URLs unchanged;
  - restriction mirrors = ledger fold.
- **Failure:** any check after its retries.
- **Retry:** each step 1 + 2 (idempotent); drain ≤ 3 cycles.
- **Checkpoint:** per-deployment results, backup refs and hashes, per-substep state with gids and hashes.
- **Resume:** §3.3 (deployments) and §3.4 (substeps), always by inspecting live state.
- **Rollback:** §8 rows P5-*: data converge back from `__v1_backup`, then SS; never v1 code.
- **Next:** P6 and F.

#### P6 Live smoke scrape
- **Entry:** P5 passed; B3 OK.
- **Actions:** automated live preflight (§10.1), then the scrape (§10.1); read back through the export plus
  `snapshot_hash`.
- **Success:** §10.1 evidence.
- **Failure:** exit ≠ 0, any data check fails.
- **Retry:** none for data checks; exit 3 → `blocked: B3`.
- **Checkpoint:** chosen group (private ref), dump ref, results, nesting/mention population rates.
- **Resume:** rerun is safe (upsert; read-only on Facebook).
- **Rollback:** none; rows are valid upserts and listed.
- **Next:** P7, F.

#### P7 Reply smoke
- **Entry:** P6 passed; B3 OK.
- **Actions:** `sweep_restrictions`; `pending_replies`; dry-run (§10.2); send step only if §10.2 eligibility finds
  a row.
- **Success:** dry-run passes; the send step is `passed` or `blocked: F-B2`.
- **Failure:** dry-run fails; a send ends uncertain after reconciliation, or fails for a code reason.
- **Retry:** dry-run once; **no send retries**.
- **Checkpoint:** results, reply ID if any.
- **Resume:** journal replay; never re-send.
- **Rollback:** none (an authorized reply stays).
- **Next:** F.

#### F Finalizer (always)
- **Entry:** any terminal state.
- **Actions:**
  - report (§6.1);
  - CLAUDE.md changelog: what ran and outcome flags, schema v2 if migrated, correction of the 2026-10-23 reply
    count (21 posted, not 1), test results;
  - README if the configuration changed;
  - memory;
  - this file's status block;
  - secret/ID scan of the diff (§6.3 patterns) → 0;
  - commit (`-F`), push `dev`.
- **Success:** pushed.
- **Failure:** scan hit → fix, rescan.
- **Retry:** push 1 + 3.
- **Checkpoint:** final SHA.
- **Resume:** re-run.
- **Rollback:** revert.
- **Next:** done.

---

## 8. Recovery and rollback matrix

| Situation | Code | Data | HEAD | Script Properties | Writes after cut-over | Result state |
|---|---|---|---|---|---|---|
| Failure in B0–PX | — | — | — | — | — | phase `failed` (local only) |
| P3 failure | rehearsal deployment deleted (ours); versioned deployments never touched | untouched | kept v1 bytes pushed back + readback equal | scratch/rehearsal namespaces cleared; production namespace untouched | — | `rolled_back` |
| P4 failure | as P3 | production untouched (proof) | as P3 | as P3 | — | `rolled_back` |
| P5 fails **before** any deployment re-pointed | as P3 | untouched | as P3 | production gate keys removed | — | `rolled_back` |
| P5 fails **after** some/all re-points, data still v1 | **SS:** finish re-pointing **every** versioned deployment to *N* (never back to v1) | v1 intact | stays v2 (gated, inert on schema 1) | `MAINTENANCE=true`, `REPLIES_KILL=true`; restriction stores kept | none possible (maintenance) | `failed` + SS |
| P5 fails during/after M1–M4 | SS | converge each original gid back to its `__v1_backup` content (idempotent, verified against the pre-migration hash); `__v2_build_*` kept hidden as `__v2_failed_*` for diagnosis | v2 | `SCHEMA_VERSION=1`, migration keys reset; ledger/DNC/HELD/SENT/UNCERT **kept** (monotonic: the 91 stay held even though v1 data shows them blank) | — | `failed` + SS |
| Rollback interrupted | — | resume converge-back by inspecting hashes per gid | — | — | — | continue rollback |
| Failure after P5 passed (P6/P7) | none automatic | none automatic | — | — | rows ingested after the cut-over stay; if a later data rollback is ever ordered, they are first exported to `dumps/plan-exec/private/post-cutover-<ts>.json` and kept in `__v2_failed_*` | phase result only |
| Restore from Backup A (only if both the original and `__v1_backup` are unusable) | SS | `Sheet.copyTo` from the backup file into the **original file**, then converge each original gid to it; deployment never repointed | v2 | as above | exported first | `failed` + SS |

**Rollback success is never "an old ping answers".** It requires all of these:
- every versioned URL pings `api_version 2` with maintenance on (SS) or as specified;
- production data hash equals the target snapshot;
- restriction stores ⊇ their pre-rollback content;
- no runner process;
- report written.

**Leaving SS** needs a code fix and the plan to be re-run from P1. A return to v1 code is outside this plan and
would need a separate explicit instruction.

---

## 9. Acceptance-test matrix

**Classes:**
- **B** existing baseline (553/3 skipped, [V2]);
- **N** new offline requirements (P1/P2/PX/E);
- **G** real Google Sheets rehearsal (P4);
- **F** real Facebook (P6/P7);
- **U** unavailable / optional.

| Area | Tests | Class |
|---|---|---|
| Gates | v1 request refused before any tab access (fake counts calls); every entry point incl. onEdit inert on schema 1; maintenance; kill switch; target allowlist (production, rehearsal, backup read-only, unknown, expired); ingestion with a `target` field rejected; fault injection refused on production | N |
| Isolation | rehearsal on file R leaves the production namespace, cells, ledger and DNC unchanged; namespace keys distinct | N + G (R01) |
| Ownership | reply/status/unknown keys rejected; owner columns inserted between ours untouched; notes preserved; `received_at` insert-only; changed-cells-only writes; identity fields frozen after send | N |
| Validation | ID/URL/group consistency; size limits; `=` strings in both formula modes | N + G (R01 decides the mode) |
| Grid | append at the exact grid edge; insert before write; new rows get formats/checkbox/dropdown/banding | N + G |
| Capacity | 25,000 IDs across mirrors; shard boundary at 8,000 bytes; store at 70 %/90 %; failure between ledger and shard → repair on read; ledger tamper → `E_INTEGRITY` | N |
| Idempotency | lost response on claim, mark_status, ledger event, migrate step, format, dashboard → replay, no duplicate; copy/version/deployment reuse by name/description | N + G (R10) |
| Retry | 1 + 3 attempts with 5/15/45 s; retryable vs non-retryable codes; `E_BUDGET` continuation | N |
| Leases | stale runner after expiry can't submit; fence mismatch; binding change (text/author/URL/DNC) at `prepare_submit`; server unreachable → no submit; two runners → one claim | N |
| Journal | the four replay paths (§4.3) | N |
| Relationships | same author with several targets in one thread; same reply text on two targets; several of our replies in one thread; mention-only response; unknown author; normalization (mention, whitespace, NFC, bidi) | N |
| Declined | from sending (right after submit), uncertain (reconcile), sent, replied; marker elsewhere on the page ignored; owner-selected `declined`; missed onEdit caught by rescan; manual revert of `declined` reverted | N |
| Legacy | `posted` → sent; blank drafts → held; held never eligible even if the cell is cleared or set to `pending` | N + G |
| Migration | substep resume from every state; deletion only of tagged sheets by gid; corruption detected (values, types, notes, relations) | N + G |
| Rehearsal scenarios (fresh copy each) | **R01** clean run + second run no-op + `=` self-test + grid edge + 3 ingested rows + dashboard (a)(b)(c); **R02–R05** Posts interrupted after M1 / M2 / M3-partial / M4; **R06–R09** same for Comments; **R10** lost response after Comments M3; **R11** full rollback; **R12** rollback interrupted mid-converge then resumed; **R13** restore from the external backup file into a deliberately damaged copy; **R14** corruption (cell + ledger row) detected | G |
| Deployment | partial re-point interrupted at each deployment, resume; `exposed_v1` blocks the pass; SS application | N (fakes only; re-pointing real versioned deployments is only done in P5, so a real partial-deployment rehearsal is **U**) |
| Executor | atomic write crash, stale lock, signals, graph, budgets, report flags | N |
| Live scrape | §10.1 evidence; nesting/mention population measured | F |
| Live reply | dry-run limits; send only if eligible | F; send **U** today (F-B2) |
| Post replies | flag default off; code tests only | N; live **U** (F-B3) |
| Visual dashboard usability | — | **U** |

Each new defect test must be shown failing on the old code first. Offline tests don't prove the real Facebook UI or
Google behaviour; classes G and F do, within their limits.

---

## 10. Live tests

### 10.1 Smoke scrape (P6)
- **Automated live preflight:**
  - CDP up;
  - `c_user` present (names only, no values);
  - console/pageerror/failed-request listeners attached (`--debug` without trace/HAR, single group);
  - receiver ping v2.
  This replaces the manual preflight in project memory within this plan's scope, without approval pauses.
- **Target rule:** walk `groups.txt` in order and pick the first group that meets all of these:
  - RUN6 exit 0;
  - its RUN6 dump has ≥ 1 post with «الأحدث» sorting;
  - not the recorded non-member or unavailable group;
  - the live feed shows posts and no «انضمام إلى المجموعة».
  None → `blocked`.
- **Command:** `fb_groups_to_sheet.py --group <selected> --min-posts 1 --max-posts 2 --min-comments 0 --max-comments 5 --workers 1 --debug`
- **Evidence:**
  - exit 0;
  - each scraped post and comment exists once;
  - `group_name` has no `(N+)` counter;
  - `created_time` is a Date equal to the dump's instant;
  - checkbox and dropdown on the new rows;
  - no header changes; owner columns untouched;
  - hashes of every `reply`/`reply_status` cell and of the ledger/DNC unchanged;
  - KPIs moved by exactly the new rows;
  - nesting/mention population rates recorded.
- **Stop:** exit ≠ 0, account block, any check failure.

### 10.2 Reply smoke (P7)
- **Dry-run:** `fb_return_replies.py --dry-run --max-replies 1` on the smoke group's first scraped **top-level
  comment** (never a held or legacy target).
  - It verifies the account, membership, own-comment rule, DNC and the target and «رد» button being visible.
  - **Limit:** it doesn't click «رد», so it doesn't prove the reply composer opens or accepts text.
- **Eligible send (all must hold):**
  - status `pending`, first pending event after P5;
  - no `held-legacy`/`legacy-sent`/`sent`/`uncertain` event on the target;
  - `author_id` present, not DNC, ≠ `c_user`;
  - top-level comment target (nested and post targets disabled);
  - member group;
  - the content gate passes.
- **Limits:** the first such row; **max 1**.
- **Evidence:**
  - strict-match confirmation; reply ID in the ledger; journal complete;
  - **network evidence:** the runner's request listener counts POSTs whose `fb_api_req_friendly_name` is
    `useCometUFICreateCommentMutation` (the name seen in earlier HARs) between `prepare_submit` and confirmation.
    Expected exactly 1. If the name isn't seen at all, the evidence is reported as "DOM-only" and the claim is
    weakened, not failed.
- **Stop:** uncertain, account block, DNC hit, unexpected dialog, page leaving the post.
- **Today:** no eligible row exists (all drafts held) → `blocked: F-B2`.

---

## 11. Decision register

| # | Decision | Evidence / rationale | Default | Resolved | Impact |
|---|---|---|---|---|---|
| D1 | Statuses | Q2's 5 + safety states; none skips an eligible target | 8 statuses (§4.1) | ✅ | P1, dashboard |
| D2 | `posted` | same meaning | → `sent` + `legacy-sent` | ✅ | P5 |
| D3 | Historical sent | unknown time/ID | `sent`, no time invented, no replied inference, no reconciliation | ✅ | P5 |
| D4 | Existing blank-status drafts | the owner ordered that batch not be sent (private memory); 21 were nevertheless posted on 2026-10-23 (logs + Sheet) | `held` permanently within this plan; no release, send or reconcile | ✅ | P5, P7 |
| D5 | Unknown legacy status | safety | migration stops | ✅ | P5 |
| D6 | Missing `author_id` | names aren't identity | never sendable; DNC needs an ID; the 2 existing are held | ✅ | P2, P5 |
| D7 | Lifecycle | §4 | §4 | ✅ | P1, P2 |
| D8 | Durable storage | §3.6 | ledger primary + sharded mirrors + journal | ✅ | P1, P2 |
| D9 | `created_time` | §1.4 | Date; legacy parsed as UTC; v2 ISO with offset; displayed in the Sheet zone (+3 h vs the old text) | ✅ | P1, P2, P5 |
| D10 | `received_at` | §1.4 | legacy instant preserved and labelled "last RUN6 write"; from the cut-over, insert-only first ingestion; time kept | ✅ | P1, dashboard |
| D11 | Sheet zone | owner's file setting | unchanged; P3 measures it | ✅ | P3 |
| D12 | Scraper ownership | §3.5 | dynamic header map, changed cells only | ✅ | P1 |
| D13 | Auth | §3.5, finding 12 | private ingest + admin keys for all writes; legacy token retired for writes; repo stays public | ✅ | P1, P2, B0 |
| D14 | Versioning | §3.3 | `api_version: 2`; namespace schema gate; no v1 aliases | ✅ | P1 |
| D15 | Migration method | §3.4 | converge-in-place, gids preserved | ✅ | P4, P5 |
| D16 | `=` text | real behaviour unknown | decided by R01; apostrophe fallback | ✅ by test | P4 |
| D17 | Dashboard | §3.8 | visible slicers + pivots + charts on one tab; `_activity` hidden source | ✅ | P4, P5 |
| D18 | KPIs | §3.8 | as listed; unfiltered tiles | ✅ | P1 |
| D19 | Q1/Q3/Q5 | owner | kept | ✅ | P5 |
| D20 | onEdit | §4.5 | convenience; server rescans authoritative | ✅ | P1 |
| D21 | Incremental scraping | equal counts ≠ unchanged | not built | ✅ | — |
| D22 | Post replies | no designated target | `--allow-post-replies` is a **code flag only**, never authorization; this plan never passes it | ✅ | P2 |
| D23 | Branch | CLAUDE.md | `dev`; push after B0 and F (plus offline phases); `main` out of scope | ✅ | all |
| D24 | Concurrency | §6.1 | local lock + server gates | ✅ | all |
| D25 | Smoke selection | §10 | deterministic rules | ✅ | P6, P7 |
| D26 | Apps Script layout | §3.7 | `apps_script/` + staging + `-P` | ✅ | P1, P3 |
| D27 | Backups | §3.4, §6.4 | Backup A Drive copy + in-file `__v1_backup` (gid-tracked); no `Spreadsheet.copy()` (scope uncertainty) | ✅ | P5 |
| D28 | v1 backup tabs | insurance | kept hidden; deletion out of scope | ✅ | — |
| D29 | Production deployment | owner published version 6 | the version-6 deployment; Python reads it from private config; **all** versioned deployments re-pointed to v2 at the cut-over | ✅ | P5 |
| D30 | Rehearsal isolation | finding 1 | namespaces, allowlist, explicit target, proof | ✅ | P3, P4 |
| D31 | Rollback | finding 3 | SS; never v1 code; monotonic restrictions | ✅ | §8 |
| D32 | Nested targets | finding 10 | disabled until P6 shows DOM nesting | ✅ | P2, P7 |
| D33 | Executor | finding 6 | agent + `tools/plan_exec` CLI | ✅ | E, PX |
| D34 | Config policy | finding 15 | §6.4 | ✅ | B0, P2 |
| D35 | Diagnostics in live phases | live-run protocol vs no pauses | automated preflight + `--debug` (no trace/HAR) for the single smoke group | ✅ | P6, P7 |
| D36 | Real partial-deployment rehearsal | it would require re-pointing real public deployments | offline only (fakes); real resume procedure in P5 | ✅ (limitation) | P5 |

---

## 12. Dependency / blocker register

| ID | Requirement | Availability (2026-10-24) | Verified how | Resolution | Blocks |
|---|---|---|---|---|---|
| B1 | clasp authorized as the owner; Apps Script API on; scriptId; deployment inventory | ⚠️ **partly open (found in P3, 2026-10-24):** auth, scriptId and inventory OK, but `clasp push` → HTTP 403 "User has not enabled the Apps Script API". Reads (list, clone) work without the user setting; writes don't. Rev. 2/3's "API enabled" was inferred from reads, which was wrong. | P3 push; a failed push changes nothing (verified: HEAD/v5/v6 still equal the repo) | owner turns on https://script.google.com/home/usersettings with the owner account, then P3 resumes at the push | **P3–P8** |
| B2 | Drive connector can copy, search, trash and read metadata | ✅ [V2] metadata; copy not yet exercised | P0 metadata; P3 first copy | if copy fails → `blocked: B2` before any production change | P3+ |
| B3 | Logged-in Facebook session | unknown (browser off) | P0/P6 CDP + `c_user` | `start-cdp-browser.cmd`, login | P6, P7 only |
| B4 | Offline toolchain | ✅ [V2] | P0 suite | — | E–PX |
| B5 | Effective permissions sufficient for v2 without re-consent | expected ✅ (manifest has no `@OnlyCurrentDoc`), unproven | P3 `capabilities` | if denied: P3 rolls back → `blocked: B5`; the owner authorizes once in the editor | P3+ |
| B6 | Private keys and config | created in B0 | B0 | — | — |
| B7 | Script ID available to B0 | ✅ in private memory + scratch config | B0 cross-check with `list-deployments` | if lost: the owner re-sends it | B0 |
| F-B2 | An authorized post-cut-over reply draft | ❌ none | P7 scan | the owner types a new reply after P5 | P7 send only |
| F-B3 | A designated post for post-reply testing | ❌ none | — | future instruction | post replies only |
| F-B5 | Visual dashboard check | not automatable | — | owner looks at it | report only |

**No mandatory blocker is open today.** B2 copy and B5 permissions are runtime checks that stop safely before any
production mutation.

---

## 13. Readiness verdict and outcome reporting

**Outcome flags** in the final report, each `true` / `false` / `not_run`, with reasons:
- `implementation_complete` (E–PX)
- `rehearsal_verified` (P4)
- `migration_verified` (P5)
- `live_scraping_verified` (P6)
- `reply_dry_run_verified` (P7 dry-run)
- `reply_sending_verified` (P7 send; `false` today, F-B2)
- `nested_reply_targets` (disabled unless P6 evidence)
- `post_replies` (disabled)
- `dashboard_visual` (unverified)

Nothing untested is labelled verified.

**Verdict:**
- **The procedure is now coherent and complete enough for a single start instruction.**
  - Every phase has entry, bounded actions, success, failure, retry, checkpoint, resume and rollback.
  - The graph covers blocked optional phases.
  - Production URLs and data are touched only after rehearsal passes, and every failure path ends in a defined
    safe state that never re-enables v1 behaviour.
- **Mandatory blockers open:** none.
- **Runtime conditional stops:** B2 copy, B5 permissions, B3 login. Each is detected before it matters and ends in
  a written `blocked` record.
- **Optional features that will not be verified:** reply sending (F-B2), post replies (F-B3), nested reply
  targets (unless P6 evidence), dashboard visual usability (F-B5), real partial-deployment rehearsal (D36).
- **The start instruction doesn't extend to:**
  - sending, releasing or reconciling the held 2026-10-23 drafts;
  - joining groups; `dnc_remove`; `release_hold`;
  - returning to v1 code;
  - merging to `main`;
  - deleting v1 backup tabs or Backup A.
