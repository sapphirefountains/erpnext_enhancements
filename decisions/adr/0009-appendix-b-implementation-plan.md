# 0009 Appendix B. File-by-file implementation plan for Phases 1–6

- **Status:** Proposed — appendix to `decisions/adr/0009-erpnext-google-chat-triton.md`
- **Date:** 2026-08-07
- **Type:** Planning artifact. **Phase 0 writes no code; nothing in this file has been built.**

## What this document is

The record (`0009-erpnext-google-chat-triton.md`) decides *what* is built and *why*. Appendix A
(`0009-appendix-a-widget-behavior-inventory.md`) records what already exists and must not break.
**This appendix decides where each piece goes, in which phase, and how risky it is.** It is the
input to six downstream sessions, each of which runs from its own prompt file in
`C:/Users/nbbsh/Documents/SF Google Chat/` with no memory of this one.

Three standing rules for anyone reading this as a work list:

1. **Every path below is plausible against the real repository as audited on 2026-08-07.** No
   invented directories. Where a path is new, its parent exists and its shape matches a
   named precedent in the same repo. Where a path is `modified`, the file exists today.
2. **A row is not a licence to skip the phase prompt.** The prompts carry the acceptance criteria
   and the checkpoint scripts. This appendix carries the file plan and the risk. Where they
   disagree, the ADR wins, the disagreement is recorded in §13, and the phase reports it at its
   checkpoint rather than resolving it silently.
3. **Risk here is honest, not decorative.** Forty-three rows across the phase tables are rated High.
   They are consolidated into the twenty-two justified entries of §9 (§9-A through §9-V), each naming
   the specific way it fails and the specific thing that would catch it.

### Where this record lives

The Phase 0 prompt asks for `docs/adr/0001-appendix-b-implementation-plan.md`. Per canonical
decision **D0**, and for the same reason Appendix A gives, it lives at
`decisions/adr/0009-appendix-b-implementation-plan.md` instead: this repo's ADR convention
(`decisions/adr/README.md`) numbers records sequentially, `0001` is taken by
`0001-record-architecture-decisions.md`, and the prompt's path would both collide and start a rival
ADR namespace under `docs/`. A human checking the prompt's literal path is not looking at a miss.

---

## 1. Phase-numbering reconciliation — stated explicitly, because a silent choice here would corrupt six later sessions

**The master prompt's phase map and Phase 0 §4.L's proposed decomposition disagree, and this
appendix follows the master map.**

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|---|---|---|---|---|---|---|
| **Master map** (`00_MASTER_PROMPT.md`, and the six prompt files) | foundations & auth | bidirectional sync engine | **chat SPA** | **notifications** | Triton integration | governance, audit, rollout |
| **Phase 0 §4.L proposal** | foundations & auth | **notifications** | … | … | … | **frontend** |

**Appendix B follows the master map.** The reasoning, stated once so no later session re-litigates it:

- Six prompt files already exist on disk — `PHASE_1_foundations_and_auth.md`,
  `PHASE_2_bidirectional_sync_engine.md`, `PHASE_3_chat_spa.md`, `PHASE_4_notifications.md`,
  `PHASE_5_triton_integration.md`, `PHASE_6_governance_audit_rollout.md` — each self-contained,
  each written to the master numbering, each carrying its own acceptance criteria and STOP gate.
  Renumbering would orphan all six.
- Phase 0 §4.L explicitly invites adjustment *"with justification if the audit says otherwise"*. It
  is framed as a **proposal**; the prompt files are **artefacts**. The proposal is what yields.
- The audit does say otherwise, on the merits and not only on convenience. Notifications at Phase 2
  would need presence, a focused-room signal, a bell surface and a badge — all of which are SPA-side
  producers that do not exist until the SPA does. Building the suppression truth table before there
  is a client that can report focus means shipping a decision function whose inputs are all
  `UNKNOWN`, i.e. the fail-open branch, i.e. untested. The master map's ordering (SPA at 3, then
  notifications at 4 consuming the SPA's heartbeat) is the one that lets Phase 4's exhaustive truth
  table be exercised against real presence records.
- Symmetrically, the frontend at Phase 6 would put the largest UI surface after governance and
  rollout, which is the wrong order for a phase whose deliverable is *"rollout"*.

**This is binding on Appendix B and on the six sessions.** `DECISIONS.md` **D7** already fixes it;
this paragraph exists because D7 asks for it to be visible rather than assumed. Appendix A §14
carries the same note for its own remapped rows.

One consequence worth naming: **`notes_infra.md`'s OQ-3 recommends scoping Web Push as its own
phase with its own checkpoint** (ADR §J.4). We are not doing that — push lands inside Phase 4 —
but Phase 4 is therefore the largest single unbuilt component in this plan, and §9 rates it
accordingly.

---

## 2. Conventions used by every table below

**Paths.** Repo-relative from the `erpnext_enhancements` repo root unless prefixed `triton:`.
The chat module is `erpnext_enhancements/chat/` — a Frappe module named **`Chat`**, with
`erpnext_enhancements/chat/doctype/<scrubbed>/` per DocType, which is the mapping
`erpnext_enhancements/tests/test_doctype_modules.py` asserts against the filesystem (ADR §D.5).

**"new/modified".** `new` = the file does not exist today. `modified` = it does, and the audit read
it. A `new` DocType row means three files — `<scrub>.json`, `<scrub>.py`, `__init__.py` — in
`erpnext_enhancements/chat/doctype/<scrub>/`; the table names the directory once.

**Risk.** Low / Med / High, on one axis only: *how bad is it if this row is wrong, weighted by how
likely it is that nothing catches it before production*. A row that fails loudly in CI is Low even
if it is fiddly. A row that fails silently in production six weeks later is High even if it is
twenty lines. Every High is justified in §9.

**CI step shapes — get this right or the suite runs nowhere.** The repo's split is load-bearing and
documented at `ci.yml:128-132`, `README.md:227`, `erpnext_enhancements/tests/README.md:19`,
`CLAUDE.md`, and `decisions/adr/0005-bench-free-tests-in-ci.md:39-43`. Four shapes, and each table
row that adds a test names which one it uses:

| Shape | Form | When |
|---|---|---|
| **P** — own pytest step | `- name: <desc> (bench-free pytest suite)` / `run: python -m pytest erpnext_enhancements/tests/test_x.py -q` | any new bench-free **pytest** suite. Nine such steps exist and **every one names exactly one file** (`ci.yml:498-501, 517-520, 528-529, 537-538, 545-546, 549-550, 556-557`) |
| **U** — own unittest step | `run: python -m unittest erpnext_enhancements.tests.test_x -v` (**dotted module path**, not a file path) | any new bench-free **unittest** suite that installs its own `frappe` stub in `setUpModule` — precedent `ci.yml:244-247`, cross-talk rationale `ci.yml:348-353` |
| **A** — append to the shared stub step | added to the module list at `ci.yml:146-154` | the one case where appending is correct: a suite that needs the stub environment `test_assistant_tools_schema` installs. **Exactly one chat suite qualifies** — `test_chat_mcp_denylist` (ADR §I.2.4) |
| **S** — script guard | `run: python scripts/check_x.py` (`ci.yml:505-513`) or `run: node scripts/test_x.js` (`ci.yml:568-575`) | filesystem/source guards with no test framework |

All new steps go in the `unit-tests` job (`ci.yml:134`, `name: Standalone unit tests`). The CI
runner installs only `httpx pytest jinja2` (`ci.yml:144`), so a bench-free suite must stub
`requests` the way `erpnext_enhancements/tests/test_triton_personas.py` already does. **Every phase
must prove its new steps execute by making one fail on purpose and watching CI go red**; "I added
tests" is not evidence, and this repo has lost a suite to exactly this (`ci.yml:128-132`).

**Bench-required tests** live in the same `erpnext_enhancements/tests/` directory, are named in the
module README with their exact `bench --site <site> run-tests --app erpnext_enhancements` command,
and are **not** wired into CI — there is no Frappe integration-test job and reintroducing one is not
in scope for any of these six phases (`ci.yml`, the block after `version-sync`).

**The version + CHANGELOG ritual.** Three files must agree on **every** change:
`erpnext_enhancements/__init__.py` `__version__`, `package.json` `"version"`, and a new dated
section in `CHANGELOG.md`. The `version-sync` job (`ci.yml:593`) fails the PR on drift, and
`release.yml` refuses to tag. **This appendix does not fix version numbers** — `main` is at
`1.260.3` today and will have moved — it fixes only the bump *class* per phase, which appears as the
last row of every table. The changelog entry is not a formality: this repo's changelog is the best
available history of *why* a workaround exists, and each of these phases contains at least one.

**Indentation.** `erpnext_enhancements/chat/**` is a new package: pick one style, **declare it in
`erpnext_enhancements/chat/README.md`, and be consistent**. Recommendation: **tabs**, matching
Frappe convention and the repo's `ruff format` configuration (tabs, double quotes,
`line-length = 110`), because most new code here is DocType controllers and hook handlers that sit
next to tab-indented neighbours. The exception is `erpnext_enhancements/api/chat.py`, which must
match whatever its neighbours in `api/` use (`api/README.md` lists which files there are tabs).
**Never normalise a file you are only touching**, and never run a repo-wide `ruff --fix` or
`ruff format` as a drive-by (ADR 0007).

---

## 3. Phase 1 — Foundations & Auth

**Objective, in one sentence.** Install the chat schema, the `Chat Settings` Single, the keyless
Google auth module, the single Google Chat transport client with a zero-I/O dry-run mode, and a
JWT-verified inbound webhook — with no relay, no UI, no notifications and no Triton — so Phase 2 has
a schema to write to and a client to call.

**Verifiable checkpoint.** `bench --site <site> execute erpnext_enhancements.chat.gchat.smoke_test.run`
round-trips all eleven steps against **real** Google Chat with `dry_run_mode` off, printing PASS and
elapsed ms per step; a **second** run creates no duplicate space (deterministic `requestId` proven,
not assumed); and a **human** opens the native Google Chat client and confirms the relayed message
renders as the real person **with no `App` badge**. Plus: `SHOW INDEX FROM \`tabChat Message\`` shows
the unique `(room, seq)`, the unique `gchat_message_name` and the unique `(room, client_message_id)`;
an unauthenticated `curl` at the webhook returns **401**; and a deliberate test failure was observed
turning CI red.

### 3.1 Module registration and package bootstrap

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/modules.txt` | modified | append a `Chat` line (the 29th module) | Low → **corrected to High, see below** | `tests/test_doctype_modules.py` asserts the directory mapping **and** the `modules.txt` registration; omission fails CI on the first DocType, loudly and immediately |
| `erpnext_enhancements/setup/module_map.py` + `hooks.py` `before_migrate` | new / modified (post-incident, 2026-08-09) | delete the cached `app_modules` snapshot and call `frappe.setup_module_map(include_all_apps=True)` before model sync, on every migrate | **High** | **the row above was wrong, and it cost the whole Phase 1 schema.** `modules.txt` is *not* what model sync reads: `sync_for()` iterates `frappe.local.app_modules`, snapshotted once in `frappe.init()` from Redis and never rebuilt by `bench migrate` (`SiteMigration.setUp`'s `clear_cache()` deletes the key without calling `setup_module_map`). With the cache warm from the previous release — which this pipeline makes likely, since it FLUSHDBs *after* `bench migrate` — the whole `chat/` folder is skipped, no DocType imports, no `Module Def` is created (it is a *consequence* of `DocType.on_update` → `make_module_and_roles`, never a precondition), and **the migrate exits 0**. Guarded by `tests/test_module_installability.py` |
| `erpnext_enhancements/chat/__init__.py` | new | empty package marker — **no import-time side effects** | Low | the repo has a module-scope side effect at `hooks.py:1275-1288` and it is documented as a hazard; a new package should not add a second |
| `erpnext_enhancements/chat/README.md` | new | the module README: hook index, the declared indentation style, the **raw-SQL review checklist** (`permission_query_conditions` does not protect `frappe.db.sql`), the **`DuplicateEntryError`-is-success** rule for inbound writers, the **1 write/second per space** constraint stated for Phase 2, the **no Chat API call from any `doc_events` handler** rule, and the bench-required test commands | Low | a README is cheap; its absence is what makes the next session re-derive four constraints. `CLAUDE.md` and `README.md:266` require it |
| `erpnext_enhancements/chat/doctype/__init__.py` | new | package marker | Low | mechanical |
| `erpnext_enhancements/utils/triton_sync.py` | modified | add `"Chat"` to `excluded_modules` (`:30-33`) — **invariant CHAT-EXCL-1** | **Med** | without it every chat DocType write announces itself to Triton's index webhook. At chat volumes that is a self-inflicted DoS on the webhook queue *and* an unreviewed egress of employee-private message content to an external service. It fails **silently** — the writes succeed. **Must land in the same commit as the first chat DocType**, and §12 nominates it earlier still (ADR §D.5 item 6, §A.2.6) |

### 3.2 DocTypes — `erpnext_enhancements/chat/doctype/<scrub>/`

Naming, autoname, DocPerm and index decisions are ADR §F.3–§F.19 and are not restated here. The
`DocPerm` column below is load-bearing: **every chat DocType ships with an empty `permissions`
array except `Chat Room`**, which is the mechanism that closes two of the three MCP surfaces
(ADR §F.18, §I.2).

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `…/chat/doctype/chat_room/` | new | `hash` autoname; `gchat_space_name` (Data 255, `unique: 1`); `room_type`; `title`; `linked_doctype` + `linked_document`; `provisioning_mode`; `provisioning_state`; `gchat_space_type`; `gchat_threading_state`; `membership_authority`; `is_archived`; the Raven last-message denormalisation as the ADR's **four typed columns** — `last_message`, `last_message_at`, `last_message_sender`, `last_message_preview` — **not** a `last_message_timestamp` + `last_message_details` JSON blob, which ADR §F.4 rejects because the room-list query sorts on `last_message_at` and a JSON blob cannot be sorted or filtered without extraction. **The one DocType carrying a DocPerm row** | **Med** | it is the realtime security boundary — `doc_subscribe` calls back into Python and runs the full document-level permission stack (ADR §H.4.1), so `has_permission` on this DocType is what makes socket security free. It is also the one place the zero-DocPerm doctrine is deliberately broken, which is **a deviation from both close notes** and needs explicit Phase 1 sign-off (residual **P3-1**) |
| `…/chat/doctype/chat_room_member/` | new | `hash`; `room`; `user`; `role`; `is_active`; `joined_at`; `left_at`; `left_seq`; `last_read_seq` (Int, the read high-water mark) + `last_read_at` per ADR §F.5/§F.15.2 — **never** a `last_read_message` / `last_read_timestamp` pair; mute is `notification_mode` (`All` / `Mentions Only` / `None`) + `muted_until`, **not** an `is_muted` check. **Soft leave**, per ADR §F.5.1 | Med | this is the table the permission query correlates against on **every** read. Getting `is_active` / `left_seq` wrong either leaks a room to a departed member or destroys a person's access to conversations they participated in — which users experience as data loss, not as a permission change |
| `…/chat/doctype/chat_message/` | new | **`autoname: hash`, `naming_rule: Random`**; `sort_field: creation` (**never `modified`**); **`track_changes = 0`**; comments disabled; **no field `in_global_search`**; `index_web_pages_for_search = 0`. Fields per D5's canon: `room`, `seq` (Int), `text`, `text_plain`, `message_type`, `parent_message`, `thread_root`, `replied_message_details` (JSON), `is_edited`, `is_deleted`, `sender`, `sender_email`, `client_message_id` (Data 64), `gchat_message_name` (Data 255, `unique: 1`), `gchat_thread_name`, `sync_origin`, `sync_state`, `mentions` (Table) | **High** | §9-A. This is the hot table and four of its properties are effectively unfixable after data lands: the naming rule, the sort field, `track_changes`, and the unique index on `gchat_message_name` on which **structural** echo-suppression depends. A `Data` default of `varchar(140)` here truncate-collides two distinct Google messages into one — silent message loss (B3, closed at 255) |
| `…/chat/doctype/chat_mention/` | new | child table of `Chat Message`; `user`, `mention_type`, `offset`, `length` | Low | bounded, small, and the correct use of a child table |
| `…/chat/doctype/chat_attachment/` | new | `hash`; `message`; `file` (Link → File); `source` (`UPLOADED_CONTENT` / `DRIVE_FILE`); `gchat_attachment_name` (`unique: 1`); `content_name`; `content_type`; `data_ref`; `drive_file_id`; `content_hash` | Med | the `source` split is a permission decision wearing a data-model costume: copying `DRIVE_FILE` bytes detaches the file from Drive's ACL, which is the governing permission model. Getting the field there in Phase 1 is what stops Phase 2 from "just downloading it" |
| `…/chat/doctype/chat_relay_job/` | new | **schema only, no worker.** `hash`; `room`; `job_seq`; `reference_doctype`/`reference_name`; `operation`; `status`; `attempts`; `next_attempt_at`/`available_at`; `last_error`; `dead_reason` | Low | it is inert in Phase 1. The risk is entirely in Phase 2's transition function |
| `…/chat/doctype/chat_inbound_event/` | new | **schema only.** `hash`; `pubsub_message_id` (`unique: 1`); `event_type`; `payload` (Long Text, untouched); `status`; `received_at`; `processed_at`; `error` | Low | same — inert until Phase 2. The unique on `pubsub_message_id` is what makes Pub/Sub's at-least-once delivery a no-op rather than a duplicate |
| `…/chat/doctype/chat_event_subscription/` | new | **schema only.** `hash`; `subscription_uid`; `target_resource`; `target_user`; `event_types`; `state`; `suspension_reason`; `expire_time`; `last_renewed`; `last_error`; `consecutive_failures` | Med | **see §13 contradiction X-2** — the ADR's DocType inventory (§F.3) does not list this table, but ADR §G.5.2–§G.5.3 require per-subscription state (`state`, `expire_time`, three distinct alarms) and Phase 2 §4.J requires "a subscription health DocType". Creating it in Phase 1 as schema-only is the resolution; a subscription whose expiry is not tracked is **permanent, silent, total loss of inbound sync** (§9-C) |
| `…/chat/doctype/chat_settings/` | new | **Single**, System Manager only, **no secret-bearing field**. Sections: Google connection identifiers; feature flags (`chat_enabled`, `dry_run_mode`, `google_sync_enabled`, `inbound_events_enabled`, `outbound_relay_enabled`, `relay_enabled`, `triton_enabled`, `org_structure_mirroring_enabled`, `document_spaces_enabled`, `restrict_to_whitelist`); retention (`message_retention_days`, `audit_log_retention_days`, `keep_tombstones_forever`, `hard_delete_after_days`, `admin_oversight_role`); Triton tiers + ceilings; operational (`max_retries`, `backoff_base_seconds`, `backoff_cap_seconds`, `http_timeout_seconds`, `log_message_bodies` default **off**, `subscription_renew_before_seconds` default `86400`) | Med | it is the kill switch for the whole system and the place a secret will be added by reflex. The `validate()` (tier budgets + reserve ≤ ceiling; `budget_t1_floor <= budget_t1_thread`) is pure arithmetic and belongs in the bench-free tier |
| `…/chat/doctype/chat_allowed_user/` | new | child of `Chat Settings`; `user` — the pilot whitelist that `restrict_to_whitelist` reads | Low | copies the `restrict_to_whitelist` precedent the Triton widget already uses (`ADR §A.4.5`), which is what makes Phase 6's pilot gating server-side rather than cosmetic |

**Deliberately not created in Phase 1**, each with its owner: `Chat Push Subscription` (Phase 4),
`Chat Context Chunk` / `Chat Room Digest` / `Chat Thread Digest` / `Chat Retrieval Audit` +
`Chat Retrieval Audit Room` / `Triton Invocation Log` (Phase 5), `Chat Export Request` /
`Chat Ops Alert` / `Chat Drift Report` (Phase 6). And **never**: `Chat Read Receipt`,
`Chat Presence`, `Chat Typing`, `Chat Reaction`, `Chat Space Provisioning Run`, a separate
`Chat Audit Log` — each rejection with its measured reason at ADR §F.3.

### 3.3 Patches

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/patches.txt` | modified | append the four chat patch lines, each with the annotation the file's existing entries carry | Low | ordering matters only relative to itself here |
| `erpnext_enhancements/patches/add_chat_indexes.py` | new | idempotent: `frappe.db.add_unique("Chat Message", ["room", "seq"])`, `add_unique("Chat Message", ["room", "client_message_id"])`, `add_unique("Chat Room Member", ["room", "user"])`, `add_unique("Chat Room", ["linked_doctype", "linked_document"])`, `add_unique("Chat Room", ["dm_user_1", "dm_user_2"])`, `add_unique("Chat Relay Job", ["room", "job_seq"])`, plus `add_index` for `(thread_root, seq)`, `(user, is_active)`, `(status, available_at)`, `(status, received_at)` | **Med** | Frappe's DocType JSON has no first-class composite-index field, so these exist **only** if the patch runs — and a patch that silently no-ops leaves the design's central performance and uniqueness claims unbacked. Must be safe to run twice. `VERIFY:` the exact v16 signatures of `frappe.db.add_index` / `frappe.db.add_unique` before writing it (ADR §F.19 records the shapes as read, not as executed) |
| `erpnext_enhancements/patches/seed_chat_roles.py` | new | create `Chat User` and `Chat Auditor` Roles **by patch, not fixture**, and attach them to the relevant Role Profiles | Med | a profiled user's direct roles are rebuilt from their profiles on **every** save, so a role granted directly is wiped; grants must go through a Role Profile. And a Role Profile shipped as a fixture has crashed `bench migrate` here before (`DocumentLockedError`, fixed in v1.171.1) — the patch route avoids re-entering that failure mode (ADR §A.8) |
| `erpnext_enhancements/patches/default_chat_settings.py` | new | seed `Chat Settings` **dormant**: `chat_enabled = 0`, `dry_run_mode = 1`, `restrict_to_whitelist = 1`, empty allow-list, `retention_mode` disabled | Low | shipping dark is the whole point; the risk is in forgetting to, which this patch removes |
| `erpnext_enhancements/patches/refresh_module_map.py` | new (post-incident) | `pre_model_sync`; the one-shot twin of the `before_migrate` hook, inside the same `@atomic` phase as `sync_all()` | Med | `post_model_sync` cannot help — it runs *after* the sync it is meant to enable, i.e. a whole migrate late, which is exactly how Training installed 12 minutes and one redeploy after its own release |
| `erpnext_enhancements/patches/finish_chat_bootstrap.py` | new (post-incident) | `post_model_sync`; new Patch Log identity calling the **original** `add_chat_indexes.execute()` and `default_chat_settings.execute()` | **High** | both originals ran on 2026-08-09 against a schema that did not exist, guarded correctly, did nothing, and were recorded with `skipped = 0`. `patch_handler.run_all` filters the pending list against `Patch Log` and has **no re-run path**, so without a new name the 11 composites stay uncreated and the `Chat Settings` Single stays unmaterialised forever. Delegation rather than duplication keeps one definition of the index set and one of the dormancy contract |

**Post-incident correction (2026-08-09), and the general rule it produces.** Two of the three
patches above were spent on the deploy that shipped them, because their own module's tables did
not exist yet. **A `post_model_sync` patch that touches tables its own module introduces in the
same release is structurally unsafe in this pipeline** and needs an idempotent, non-raising
`after_migrate` twin. `add_chat_indexes` had one (`ensure_chat_indexes`) and it is the only
reason invariant I2's composite set exists in production; `default_chat_settings` did not, and
now does (`ensure_chat_settings`).

### 3.4 Google auth, transport client, webhook

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/chat/gchat/__init__.py` | new | package marker | Low | — |
| `erpnext_enhancements/chat/gchat/auth.py` | new | keyless DWD: build the assertion claim set (`iss`/`sub`/`scope`/`aud`/`exp ≤ 10 min`) as a **pure function**; sign it via IAM Credentials `signJwt` from the VM-attached service account; exchange for an access token; per-subject token cache. Plus the app-identity token path for the Chat app | **High** | §9-B. `google.auth.impersonated_credentials` has **no `subject` parameter**, so ~40–50 lines of the assertion builder are hand-rolled — a hand-rolled security primitive with no library to fall back on, on a host where the deploy pipeline has **no `pip install` step at all** (`infra/cloudbuild-deploy.yaml:35-41` is `fetch/reset → migrate → build → FLUSHDB → restart`). A wrong `aud` or an over-long `exp` fails as a 400 from Google that reads like a config problem |
| `erpnext_enhancements/chat/gchat/client.py` | new | the **only** module in the codebase that speaks HTTP to Google. One `_request()` choke point. Methods: `setup_space`, `find_direct_message`, `create_message`, `patch_message`, `delete_message`, `list_messages`, `get_message`. `create_message` makes `messageId` (with the `client-` prefix enforced) and a deterministic `requestId` **required positional arguments**. `messageReplyOption` exposed explicitly. `createMessageNotificationOptions` plumbed as an optional pass-through. Fully type-annotated | **High** | §9-D. Two independent reasons. (a) Omitting `messageReplyOption` silently starts a **new top-level thread and ignores the thread id you passed** — a wrong answer that looks like a right one. (b) `createMessageNotificationOptions` is the parameter locked decision #3 rests on, and it is confirmed to **require app authentication**, which is mutually exclusive with human attribution (CQ-1). Plumbing it without deciding it is correct for Phase 1; *shipping* on it is not |
| `erpnext_enhancements/chat/gchat/backoff.py` | new | `compute_backoff(attempt, base, cap, retry_after)` and `classify_error(status, exc)` — **pure**, no I/O. Full jitter: `random.uniform(0, min(cap, base * 2**attempt))`. Retry 429/500/502/503/504 + connect/read timeouts; **never** any other 4xx | Low | pure functions with seeded-RNG tests are the one tier with automatic regression protection here. The design risk (retrying a 403 and hiding a scope error) is closed by the classifier's table test |
| `erpnext_enhancements/chat/gchat/ids.py` | new | `client_message_id(erpnext_name) -> "client-…"` and its inverse; `request_id(name, op)`. ≤63 chars, lowercase letters/digits/hyphens, unique within the space. Both directions pure and tested | Med | this derivation *is* invariant I3 — an inbound event carrying a `client-` id ERPNext issued is definitionally our own echo. An encoding bug produces duplicates in Chat at 1 write/second, i.e. a visible room-flooding failure, which is at least loud |
| `erpnext_enhancements/chat/gchat/dryrun.py` | new | deterministic synthetic responses: `spaces/DRYRUN-<hash>`, `spaces/DRYRUN-<hash>/messages/<client-id>`. **Visibly fake** so Phase 2's reconciliation can detect and skip them | Low | a test patching `_request()` and asserting **zero** calls is the whole guarantee, and it is cheap |
| `erpnext_enhancements/chat/gchat/webhook.py` | new | `@frappe.whitelist(allow_guest=True)` endpoint. **Verifier written first, handler after.** Verify signature, **issuer `chat@system.gserviceaccount.com`**, audience against the byte-exact configured URL, expiry — **before** body parsing and before any DB access. Phase 1's handler logs the event type and returns `200` empty | **High** | §9-E. `allow_guest=True` makes it world-reachable; the JWT is the only thing between it and an open relay. `verify_oauth2_token` does **not** check the issuer claim for you, so a generic ID-token verification accepts **any** Google ID token minted for your URL. Never an IP allowlist — Google publishes no stable Chat egress ranges (accepted risk R04-V15) |
| `erpnext_enhancements/chat/gchat/smoke_test.py` | new | the eleven-step `run()` script (§6 of the Phase 1 prompt): config print, token mint, `spaces:setup` ×2 with the same `requestId`, post, **human authorship confirmation**, patch+get, threaded reply, deletes, inbound event observation, summary table, cleanup | Med | it is the checkpoint gate. Its own risk is leaving orphan spaces visible to real employees, which the cleanup step exists to prevent — and which must be verified, because it runs against the live domain |
| `erpnext_enhancements/chat/links.py` | new | `build_message_deep_link(room, message=None, thread=None) -> str` — absolute URL, every component URL-encoded, origin from Frappe's own site-URL helper | Med | **one function, three consumers**: the SPA router (Phase 3), the notification deep-link builder (Phase 4), and the citation resolver for `chat_message` sources (Phase 5). Writing it in Phase 1 is what stops three divergent implementations. `VERIFY:` that the v16 site-URL helper returns the external HTTPS origin behind the load balancer, not an internal address — the classic failure is `http://localhost` links in production pushes |
| `erpnext_enhancements/chat/permissions.py` | new | `chat_message_query(user)`, `chat_room_query(user)`, `chat_room_has_permission(doc, ptype, user)` and siblings. `frappe.db.escape(user)` **always**. **Explicit `True` on every allowing path.** `""` returned only for the configured oversight role, gated on the role and never on a caller-supplied flag. A single clearly-marked hook point for Phase 6's non-participant audit write | **High** | §9-F. Three compounding reasons: on v16 a `has_permission` hook that falls off the end returning `None` now **denies**, so a missing `return True` is a silent lockout; the query string is concatenated into SQL, which is the classic Frappe injection footgun; and these hooks do **not** protect `frappe.db.sql`, so every raw query in the package must re-apply the filter itself |
| `erpnext_enhancements/api/chat.py` | new | the whitelisted surface for Phase 1: `get_settings_public()` (feature flags only) and nothing else. Indentation matches its `api/` neighbours | Low | deliberately thin. Phase 3 fills it |
| `erpnext_enhancements/api/integrations_health.py` | modified | add a `_check_chat` and a row to `_INTEGRATIONS` (`:405-412`), reporting secrets only as `configured: true/false` | Low | the registry pattern already exists and is enumerated by `tests/test_integrations_health.py`; the risk is only in leaking a value instead of a boolean, which the existing suite covers |

### 3.5 Hooks, boot, assets

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/hooks.py` | modified | **annotated** entries for: `permission_query_conditions` for the chat DocTypes **and their `has_permission` twins** (parity is the house doctrine — the register is at exact 10/10 today); `doc_events` for `Chat Message` / `Chat Room Member` (**no Chat API call from any of them**); `ignore_links_on_delete` if messages link documents; and `website_route_rules` — **the app's first ever** — `{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}` (Phase 3 uses it; declaring it in Phase 1 is optional and §9-G argues for *not* declaring it early) | **Med** | `hooks.py` is annotated and the annotation is documentation (`hooks.py:9-15`); `tests/test_hooks_integrity.py` rejects duplicate keys and handlers, and `tests/test_hook_targets_resolve.py` rejects dangling paths, so structural mistakes fail in CI. The residual risk is behavioural: a `doc_events` handler that reaches Google turns the 1-write/second space quota into dropped messages during ordinary typing |
| `erpnext_enhancements/boot.py` | modified | add exactly one flag, `ee_chat`, and nothing else | Low | `extend_bootinfo` runs on every desk load for every user; the existing entries are single booleans for that reason |
| `erpnext_enhancements/public/js/chat/` | new (dir) | **created empty in Phase 1.** No `app_include_js` line, no raw `/assets` path, ever | Low | the namespace exists so Phase 3 has somewhere to land; global assets ship as bundles (ADR 0008) and Phase 1 ships no browser code at all |

### 3.6 Tests and CI — Phase 1

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/tests/test_chat_guardrails.py` | new | **bench-free, unittest, shape U.** The fence, shipped before the field: (1) every chat DocType JSON has `permissions == []` except `Chat Room`, which asserts the inverse plus both hooks registered; (2) every `frappe.enqueue` under `erpnext_enhancements/chat/**` passes `enqueue_after_commit=True`; (3) every chat `publish_realtime` passes `room=` / `user=` / `doctype=`+`docname=`; (4) no chat event is named `list_update` or `docinfo_update`; (5) no module outside `chat/gchat/client.py` mentions `chat.googleapis.com`; (6) no `doc_events`-registered chat function reaches the transport | **Med** | this suite is the cheapest defence in the whole plan and each assertion has a named failure it prevents. `publish_realtime`'s final fallback is `get_site_room()` — a **site-wide broadcast** — so a chat event that forgets its targeting argument broadcasts message bodies to every connected session. And `list_update` / `docinfo_update` **overwrite an explicitly passed `room=`**. Modelled on `tests/test_contract_esign.py:526-534`, which already asserts "a guest endpoint is missing its rate limiter" by regex over source |
| `erpnext_enhancements/tests/test_chat_gchat_client.py` | new | **bench-free, pytest, shape P.** Backoff with seeded RNG (never negative, never over cap, honours `Retry-After`); error classification table; `client-` id derivation + inverse incl. the 63-char cap and charset filter; `requestId` determinism; dry-run determinism and **`_request()` never entered**; payload construction for each of the three `spaces.setup` shape constraints | Low | pure functions; failures are loud and local |
| `erpnext_enhancements/tests/test_chat_auth_claims.py` | new | **bench-free, pytest, shape P.** Claim-set construction: `iss`/`sub`/`scope`/`aud`, `exp ≤ 10 min`, deterministic serialization, and that no token or key material is ever returned in a log record | Med | the only automated defence on a hand-rolled security primitive. A test that asserts the *shape* cannot prove Google accepts it — that is what smoke-test step 2 is for |
| `erpnext_enhancements/tests/test_chat_webhook_verify.py` | new | **bench-free, pytest, shape P.** The six negative cases, by name: no token; malformed token; expired token; **valid Google token with the wrong `email`/issuer claim**; **valid token with the wrong audience**; valid token + malformed body → **401 with no DB write**. Crypto boundary mocked | **High** | §9-E. This is the only pre-production defence on a world-reachable endpoint, and two of the six cases (wrong issuer, wrong audience) are precisely the ones a generic ID-token verification passes |
| `erpnext_enhancements/tests/test_chat_settings_budget.py` | new | **bench-free, pytest, shape P.** `validate()` arithmetic: tier budgets + reserve ≤ ceiling; `budget_t1_floor <= budget_t1_thread`; boundary and off-by-one | Low | pure arithmetic |
| `erpnext_enhancements/tests/test_module_installability.py` | new (post-incident) | **bench-free, unittest, shape U.** Every module folder shipping DocType JSONs is registered in `modules.txt`; every registered module is an importable package; the app rebuilds the module map in `before_migrate` **and** deletes the cache key before rebuilding it; the two spent chat patches have a successor in `[post_model_sync]` and an `after_migrate` backstop each | **High** | the incident's whole signature was silence — a green deploy that installed nothing. This is the only artefact that turns it into a red PR, and it is global: it fails for *any* module, not just Chat. Proven by deleting the hook line and the successor patch line and watching two assertions go red |
| `erpnext_enhancements/tests/test_chat_permissions_bench.py` | new | **bench-required**, named in the module README with its exact command. Non-member denied on the list path **and** the single-doc path **and** the report view; `has_permission` returns an explicit boolean on every path incl. exception paths; the oversight role's `""` escape hatch is role-gated | High | rated High because it is the security test **CI does not run**. Its value depends entirely on a human executing it and recording the result at the checkpoint |
| `scripts/check_no_committed_secrets.py` | new | scan the working tree **and git history, all refs** for service-account key files, PEM private-key blocks, Google client secrets and (from Phase 4) VAPID private keys | Med | Phase 1 §4.4's hard rule. It is a blocking gate whose failure mode is "passes because the pattern is wrong", so it must be proven by planting a fake secret and watching it fire |
| `.github/workflows/ci.yml` | modified | **six new steps** in the `unit-tests` job: four shape-P pytest steps (client, auth claims, webhook verify, settings budget), one shape-U unittest step (`test_chat_guardrails`), one shape-S script step (`python scripts/check_no_committed_secrets.py`). Plus the shape-P step for `test_triton_personas.py` if §12's first commit has not already landed it | **Med** | this repo has silently run a suite nowhere before. A pytest suite appended to a unittest module list executes **zero tests and reports success**. Each new suite needs its **own** step naming exactly one file, and each must be proven by a deliberate red run |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump (new DocTypes, new endpoint), all three in lockstep, changelog entry explaining *why* the keyless DWD assertion builder is hand-rolled and *why* `chat.googleapis.com` appears in exactly one module | Low | the `version-sync` job (`ci.yml:593`) fails the PR on drift, so this cannot be forgotten quietly — only forgotten expensively |

---

## 4. Phase 2 — The message core and the bidirectional sync engine

**Objective, in one sentence.** Make ERPNext and Google Chat converge in both directions —
transactional-outbox relay out, Workspace Events ingest in, with echo suppression, edit/delete
propagation, space provisioning in all three modes, attachments, and a subscription lifecycle whose
worst failure is lag rather than silent permanent loss.

**Verifiable checkpoint.** The scripted **200-message soak**: two users exchanging from both clients,
with edits, deletes, attachments, a burst exceeding the per-space write quota, a deliberate relay
outage, deliberate duplicate deliveries and one deliberate out-of-order pair — ending with exactly
the expected ERPNext row count, exactly the expected Chat message count, **zero** duplicates, **zero**
orphans and a stable render order; then **re-run after a full replay of every event and job** with the
same result. Plus all seventeen named chaos tests green.

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/chat/fake_gchat.py` | new | the in-memory fake Chat API: `spaces.setup`, `spaces.members.*`, `messages.create/get/patch/delete/list`, `media.upload/download`; enforces per-space **1 write/second** and the project ceilings by returning **real `429` shapes**; honours `requestId` and client-assigned `messageId` semantics; emits Workspace Events into a controllable queue; fault injection for timeout-then-success, 5xx, duplicate delivery, out-of-order delivery, **delayed response** and **event-before-response** | **Med** | it is the deliverable that makes every other test in this phase possible, and a fake that is wrong in the same direction as the code proves nothing. **Treat it as production code with its own tests**, and validate its event payloads against a **captured real payload**, not against a hand-written approximation |
| `erpnext_enhancements/chat/relay.py` | new | the outbound state machine: one transition function, no bare `db_set` anywhere. States `Queued → Sending → {Synced, Retrying, Failed}`, `Retrying → {Sending, Dead}`, `Cancelled`. Per-message `job_id` dedupe; per-room FIFO via `Chat Relay Job.(room, job_seq)`; dependency gate (room has `gchat_space_name` **and** the author is a `JOINED` member); dead-letter with an `Error Log` row minus secrets; whitelisted **System Manager-only** manual retry that re-enters the state machine | **High** | §9-H. `sync_state` is what the SPA renders and what an operator reads at 2 a.m., and any state assigned outside the transition function is a state no test covers. The ordering requirement is subtle and load-bearing: within one message `create → edit → delete` must apply in that order, or an edit overtakes its own create and Google 404s on a message that exists locally |
| `erpnext_enhancements/chat/bucket.py` | new | the per-space token bucket in **Redis** (shared across workers, survives restarts). **Charges `media.upload` two tokens**, because uploads share the per-space 1-write/second budget with `messages.create` | Med | an in-process bucket is wrong the moment there are two workers. The two-token rule is the difference between working and 429-ing on exactly the "someone drops a screenshot into a busy space" case (`DECISIONS.md` D8) |
| `erpnext_enhancements/chat/sweeper.py` | new | the `scheduler_events["cron"]` sweeper: re-enqueue `Queued`/`Retrying` past `next_attempt_at`; alert on anything `Sending` older than the job timeout (a crashed worker) | **High** | §9-I. **The prod deploy FLUSHDBs the queue Redis** (`infra/cloudbuild-deploy.yaml:35-41`), so queued relay jobs do not survive a deploy. The sweeper — **not the queue** — is the delivery guarantee. This has already been confirmed as the cause of missing Drive folders on this site |
| `erpnext_enhancements/chat/ingest.py` | new | the inbound pipeline: consumer writes the raw `Chat Inbound Event` row, **commits, then acks**, then enqueues processing; processing is separately safe to run twice; dedupe by attempting the insert and treating `DuplicateEntryError` as "already ingested" — **never** `SELECT`-then-`INSERT` | **High** | §9-J. Acking before the row commits loses events on a worker crash; doing real work before acking causes redelivery storms. And `SELECT`-then-`INSERT` is a TOCTOU bug at exactly the moment it matters (two workers, one redelivered event) |
| `erpnext_enhancements/chat/echo.py` | new | the echo decision as **one pure function**: `(event, local lookup results, in-flight set) → {echo-reconcile, ingest, defer, resolve-via-get}`. Layer 1 is the `client-` id; layers 2 and 3 are the documented fallbacks | **High** | §9-K. This is the crux of the phase. The failure is a duplicate that is relayed again, and at 1 write/second the room fills with duplicates within seconds. Making it pure is what lets the whole decision matrix be a table test. **Explicit anti-requirement: no similarity heuristic on message text.** If the design needs one, the design is wrong |
| `erpnext_enhancements/chat/mutate.py` | new | edit/delete both directions. Outbound edit `messages.patch` with `updateMask=text` and `allowMissing=true` (self-healing upsert). Outbound delete with `force` — **user auth only**. Inbound `updated` under a `lastUpdateTime` monotonic guard; inbound `deleted` → tombstone, body moved off the live row, **tombstone terminal** | High | attribution: under app auth Chat permits editing/deleting only messages the **app** created, so touching a human's message requires impersonating that human. If the author has left the company the delegation fails, and the fallback must be an explicit `Failed` with an operator path — never a silent divergence between the two systems |
| `erpnext_enhancements/chat/seq.py` | new | the per-room `seq` allocator inside the insert transaction, and `watermark(room) -> (max(seq), count(*), max(modified))` | **High** | §9-L. `DECISIONS.md` **D6**: a watermark tracking `seq` alone will serve cached context containing a message the user just **deleted**, because edits and deletes do not advance `seq`. Every digest, chunk and cache key in Phase 5 keys on this function. It is the single most likely bug in the design and its test must exist before its code |
| `erpnext_enhancements/chat/provision.py` | new | all three modes. Mode 1 lazy `spaces.setup` with a deterministic `requestId`; the three shape constraints (`SPACE` needs `displayName`; `GROUP_CHAT` must not set it and needs ≥2 memberships; human↔human `DIRECT_MESSAGE` must not set it, needs exactly 1, `singleUserBotDm: false`) encoded as **validation**, not discovered at runtime. Mode 2 org mirroring as a **throttled resumable** job with a dry run and per-entity opt-in. Mode 3 per-document rooms, user-initiated | **High** | §9-M. Space writes are capped at **60/minute** and membership writes at **300/minute** project-wide, so a first-run sweep over the org trips both immediately. And `spaceThreadingState` is **Output only** — if it is not settable at create, getting it wrong now means **recreating every space later**, which is why ADR §G.9.3 makes it a five-minute live call before any threading design is written |
| `erpnext_enhancements/chat/membership.py` | new | ERPNext→Google as a **converging diff** against `spaces.members.list`, not an event replay. Google→ERPNext accepted only where the room's `membership_authority` permits; org-mirrored and per-document rooms **revert** and write an audit note, with a per-room-per-hour revert cap and an alert instead of a loop | Med | a revert loop between two systems is a self-inflicted quota burn that looks like an outage. The cap is what makes it an alert instead |
| `erpnext_enhancements/chat/attachments.py` | new | outbound `media.upload` → `attachmentDataRef` → `messages.create`. Inbound: `UPLOADED_CONTENT` → `media.download` with the **`data_ref`** (the `downloadUri`/`thumbnailUri` are for human users and unusable by an app) → private `File` on the message row; `DRIVE_FILE` → store `driveFileId` and render a link, **never copy the bytes**. Every attachment `is_private = 1` and attached to the `Chat Message` | High | copying a `DRIVE_FILE` detaches it from Drive's permission model, which is the governing ACL — a permission bug wearing a convenience costume. And `is_private = 1` is what makes Frappe's file check delegate to `has_permission`, but Frappe has a long tail of reported issues where private files are more accessible than expected, so the **non-member 403 must be tested on a real bench**, not assumed |
| `erpnext_enhancements/chat/subscriptions.py` | new | hourly renewal via `subscriptions.patch`, renewing a **full day early** on a 7-day TTL; **never hard-code the TTL** — omit `ttl` to request the max and **read back `expireTime`**; handle `expirationReminder` / `suspended` / `expired` as a backstop, not as the trigger; `subscriptions.create` fresh on expiry and immediately sweep the gap; three distinct alarms (`expiring`, `suspended`, `missing`) | **High** | §9-C. An expired subscription is **permanently deleted and cannot be renewed** — recovery is a *new* subscription. A renewal job that only ever patches will never notice one is missing entirely, which is why the third alarm exists. `USER_SCOPE_REVOKED` must alert **by name**: one person revoking consent silently kills inbound sync for every space only they cover |
| `erpnext_enhancements/chat/reconcile.py` | new | the sweep: per mirrored space, `messages.list` with `filter: createTime > <watermark>`, ingested through the **same** idempotent inbound path — never a second code path. Store `last_event_at` / `last_reconcile_at` per room; alert when a room has Chat traffic but no events for longer than a stated window | Med | this is what converts a missed renewal from data loss into lag. Its own risk is a second ingest path drifting from the first, which the "same path" rule removes by construction |
| `erpnext_enhancements/chat/seams.py` | new | `notify_new_message(message)` and `mark_room_context_stale(room, from_seq, to_seq)` — **stubs**, a counter and a debug log each | Low | trivial to write, and their **assertions** are the phase's cheapest correctness proof: exactly once per genuinely new message, **zero** for echoes, replays, reconciled duplicates and outbound relays |
| `erpnext_enhancements/chat/health.py` | new | a `bench execute`-able health command: per-room sync state, oldest `Queued` job age, subscription states and expiries, and the counters (relayed, echoes suppressed, ingested, duplicates rejected, retries, dead letters, quota backoffs) | Low | operational, and the counters are what the chaos tests assert against |
| `erpnext_enhancements/hooks.py` | modified | `doc_events` for the outbox write path; `scheduler_events["cron"]` for the sweeper — **staggered clear of the :00/:20/:40 QuickBooks cluster and the 05:00–07:15 backup/digest cluster** — plus hourly subscription renewal and the 30-minute reconciliation sweep offset off the relay sweeper | Med | the cron collision is real: three QuickBooks jobs already sit at `:00`, `:20`, `:40` precisely because firing them together made two saves race (`hooks.py:572-600`). A chat sweeper landing on the same minute inherits that lesson for free or relearns it |
| `erpnext_enhancements/tests/test_chat_relay_state.py` | new | **bench-free, pytest, shape P.** Every legal transition; **every illegal transition raises**; error classifier; backoff sequence; token bucket arithmetic incl. the two-token upload charge; message byte-length counting with a 4-byte emoji and CJK against the **32,000-byte** limit | Low | table tests over pure functions |
| `erpnext_enhancements/tests/test_chat_echo.py` | new | **bench-free, pytest, shape P.** The full echo decision matrix as a table; idempotency-key derivation per event type; inbound parsing from **captured** payloads (created, updated, deleted, membership, batch variants, malformed) | Med | the fixtures must be captured from a real space. A parser tested only against hand-written payloads is a parser tested against your own assumptions |
| `erpnext_enhancements/tests/test_chat_sync_bench.py` | new | **bench-required.** The seventeen named chaos tests, the Phase 2 prompt's §4.F conflict table one test per row, the "kill the worker" test (workers stopped → row readable, job `Queued`; workers started → terminal success), and the permission suite re-run incl. the **report view** and a second authenticated **socket client** | High | rated High for the same reason as Phase 1's bench suite: it is the phase's real correctness proof and **CI does not run it**. Its value is entirely in a human running it and pasting the output |
| `erpnext_enhancements/tests/test_chat_raw_sql_guard.py` | new | **bench-free, unittest, shape U** (or folded into `test_chat_guardrails`). Enumerate raw SQL under `erpnext_enhancements/chat/**` and assert each occurrence lives in an allowlisted module **and** contains the membership constraint | Med | permission hooks do not protect `frappe.db.sql`, and history paging will be raw SQL. This is the single most likely route to a real data leak in the system |
| `erpnext_enhancements/chat/README.md` | modified | document the state machine, the conflict rules by name, the subscription lifecycle, the two seams, and the **"the sweeper is the delivery guarantee, because the deploy FLUSHDBs the queue"** rationale | Low | — |
| `.github/workflows/ci.yml` | modified | three new shape-P steps (`test_chat_relay_state`, `test_chat_echo`, plus the fake-harness's own suite) and one shape-U step if the raw-SQL guard is separate | Med | same silent-nowhere hazard as Phase 1 |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump; the changelog explains the outbox-vs-queue decision and the FLUSHDB that forces it | Low | — |

---

## 5. Phase 3 — The chat SPA

**Objective, in one sentence.** Ship a chat SPA reached by expanding the floating bubble users
already have — room list, conversation, threads, composer, mentions, attachments, search, presence,
typing, read receipts — with **every** behaviour in Appendix A §14 intact and inline citation links
added on top.

**Verifiable checkpoint.** On production, in a real browser: the bubble looks and behaves exactly as
before when collapsed; Triton still streams and still renders its chip row; expanding lands **in the
same conversation, at the same scroll position, with the same thread open and the same unsent
draft**; a **hard refresh** at `/chat/room/<room>?thread=<msg>&message=<msg>` renders the same place;
`Object.keys(window).filter(k => /^Vue/i.test(k))` plus the build-time check show **exactly one Vue
runtime**; and the full Appendix A §14 table is re-walked row by row with each row marked pass or
*intentionally changed*.

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/www/chat.html` | new | the SPA shell. **No hyphen in the filename** | Med | a `www/` controller whose filename contains a hyphen is **never imported by Frappe**, so its `get_context()` silently never runs — `stripe-return.py` was broken this way from the day it was written. `scripts/check_www_controllers.py` guards it and must stay green |
| `erpnext_enhancements/www/chat.py` | new | `get_context()`: auth gate, the pilot whitelist check, boot payload (VAPID public key placeholder for Phase 4, the restored-room hint, the unread snapshot) | Med | same hyphen rule; plus this is the server-side pilot gate, and a gate implemented only in the client is not a gate (Phase 6 G6-22) |
| `erpnext_enhancements/hooks.py` | modified | `website_route_rules`: `{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}` — **the app's first ever** | **High** | §9-G. Two edges. (a) The rule itself is closed: v16 uses werkzeug `Rule` verbatim, the full converter set including `<path:name>` is available, and `/orders/<path:name>` is a working in-production ERPNext precedent. (b) **The `website_404` cache trap is not closed**: loading `/chat/room/X` *before* the rule ships caches that URL as a 404 until Redis is flushed. A full deploy FLUSHDBs and saves us; a hotfix without a restart does not. This must be in the Phase 3 rollout note, not discovered live |
| `erpnext_enhancements/public/js/chat/spa/` | new (dir) | the SPA: router, room list, conversation (virtualised), thread pane, composer with drafts, mention autocomplete incl. `@triton`, attachments, search, member list, presence/typing/read-receipt clients, reconnect resync | **High** | §9-N. The **two-Vue-copies hazard does not exist today** (Appendix A §11) and a bundled-Vue SPA is what would create it: broken reactivity across the widget/SPA boundary, doubled bundle size, and bugs that present as "the widget stopped updating after I opened the SPA". The decision (reuse `window.Vue` as an external, migrate the widget, or hard-isolate) must be made and **guarded automatically** before a single component is written |
| `erpnext_enhancements/public/js/chat/chat.bundle.js` | new | the SPA entry, content-hashed via `assets.json` | Med | raw `/assets` paths are served **immutable for a year with no content hash**, so an edit never reaches a device that already cached it — the "fix works on desktop, phones still broken" bug (ADR 0008). The two vendored UMD libraries stay raw includes and must not be "fixed" |
| `erpnext_enhancements/public/css/chat/chat.bundle.scss` | new | SPA styles | Low | — |
| `erpnext_enhancements/public/js/global_enhancements/triton_widget.js` | **modified** | the dual-surface extension: a coworker conversation surface alongside the Triton surface, the expand control, the session handoff **writer**, the reverse handoff, and the unread badge | **High** | §9-O. Locked decision #8 forbids forking it, and Appendix A §14 is its regression contract — a table currently defended by **a test file that has never run**. This file is a single 1404-line anonymous IIFE with **zero exports**, so nothing in the repo imports a symbol from it and a refactor cannot break a caller — which is a mercy, and also means nothing catches a behavioural regression except that table |
| `erpnext_enhancements/public/js/erpnext_enhancements.bundle.js` | modified | one `import "./chat/…"` line in the documented order | Low | the bundle's header comment records the ordering constraints; adding one import beside `triton_widget.js` is mechanical |
| `erpnext_enhancements/public/css/desk_addons.bundle.scss` | modified | one `@import` for the widget-extension styles | Low | — |
| `erpnext_enhancements/api/chat.py` | modified | the read/write surface: keyset history paging, room list (from the **denormalised** `last_message` / `last_message_at` / `last_message_sender` / `last_message_preview`, zero joins), member list, mention autocomplete, search, `mark_read`, `set_typing`, `heartbeat`, `set_last_open_room`. Every one permission-checked; **every raw SQL query applies the membership filter itself** | **High** | §9-F again, at the point it actually bites. A search box is precisely where someone writes raw SQL for performance, and `permission_query_conditions` does not touch it. Search over a room the caller is not a participant in (the admin case) must also write an audit row |
| `erpnext_enhancements/chat/presence.py` | new | Redis presence and typing with **TTLs, never sticky flags**; `chat:focus` as a hash; multi-client union | Med | presence as a DocType would be ~200× this site's entire current write volume, which is why it is Redis. The design risk is a flag that never expires, so the expiry test (`test_presence_expiry_resumes_notifications`) is the one that matters |
| `erpnext_enhancements/chat/read_state.py` | new | `mark_room_read` as the **one** writer that may advance the high-water mark. The mark is `Chat Room Member.last_read_seq`, an **`Int` advanced monotonically** — `new = max(old, seq)`, never backwards — written with `update_modified=False`, permission-checked on the `(room, user)` index, with no parameter naming another user | Med | derived read state is `M.seq <= member.last_read_seq` (ADR §F.15.2), and that is what avoids ~20,000 receipt writes/day against 2,000 message writes. **The comparison is on `seq`, never on `creation`**, and the ADR (§F.6.6 ordering rule) rejects the timestamp form with a measured reason: two inserts into one room can share a `creation` value to the microsecond, and `creation` is written in **site-local time while the production database clock is UTC**, so any SQL-side comparison of a Frappe-written timestamp is silently wrong by the site's UTC offset. `seq` has no timezone. A second writer would break monotonicity and the count would visibly regress |
| `erpnext_enhancements/chat/citations.py` | new (stub) | the inline citation **renderer contract** only — the token grammar, the manifest shape, the resolver signature. Behind a feature flag, default off | Med | Phase 5 fills it. Shipping the renderer here behind a flag is what lets Phase 5's fixtures be re-run unchanged; the risk is scope creep into Phase 5's manifest construction |
| `erpnext_enhancements/tests/test_chat_spa_pure.py` | new | **bench-free, pytest, shape P.** Deep-link builder + route parser round-trips; the three-tier **restoration resolver** (URL beats session handoff beats bootinfo; expired/nonce-mismatched handoff ignored; unreadable room falls back one tier at a time); optimistic reconciliation (server response and realtime event in **either** order produce exactly one bubble); read-receipt batcher monotonicity and coalescing; typing throttle/expiry; presence union; mention parsing | Low | pure, and it is where most of the SPA's real logic should live |
| `erpnext_enhancements/tests/test_chat_citations_render.py` | new | **bench-free, pytest, shape P.** Well-formed token → anchor; unknown `k` → dropped with `citation_miss` logged; malformed variants render as literal text; **token split across chunk boundaries reassembled**; tail buffer flushed on stream end; a stream with **no** `citations` event renders identically to today; `javascript:` scheme rejected; `<script>` in a label rendered as text | Med | the whole point is that the flag-off path is byte-identical to today's widget output. That is an assertion, not a hope |
| `erpnext_enhancements/tests/js/` + `vitest.config.*` + `package.json` | new / modified | **stand up a JS test runner** (Vitest + `@vue/test-utils`) as its own CI step. The repo has **no JS test runner today** and **no test of any kind for `triton_widget.js`** | **High** | §9-O. Without it the widget's must-survive table is asserted by nobody. A runner that is installed but never invoked is *worse* than none, because it looks like coverage — so this must be proven by a deliberate red run. Note the existing JS guards are plain `node scripts/*.js` (shape S), deliberately not a framework; introducing Vitest is a new precedent and belongs in the changelog with its reason |
| `erpnext_enhancements/tests/test_chat_spa_bench.py` | new | **bench-required.** The I8 socket boundary with two real clients; private-file **403** for a non-member; `EXPLAIN` showing the `(room, creation)`/`(room, seq)` composite in use; search permission incl. the admin audit row; `mark_read` monotonicity against a deliberately out-of-order client | High | again: the real security proof, run by a human, not by CI |
| `.github/workflows/ci.yml` | modified | two shape-P steps, one **new** JS step (`npx vitest run` or equivalent), and confirmation that `python scripts/check_www_controllers.py` still passes with `www/chat.py` present | Med | the www-controller guard is the one that has already caught this class of bug in this repo |
| `erpnext_enhancements/chat/README.md`, `erpnext_enhancements/public/README.md` | modified | document the SPA's asset model, the single-Vue-runtime decision **and its guard**, and the route scheme as a contract shared with Phases 4 and 5 | Low | — |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump; the changelog records the Vue decision and why the SPA is a bundle | Low | — |

---

## 6. Phase 4 — Notifications

**Objective, in one sentence.** Deliver the whole notification system — server-owned Redis presence,
**one pure** suppression decision function, `Notification Log` bell rows with the email path asserted
off, native VAPID Web Push, cross-surface read synchronisation and the bubble's unread badge — such
that a presence-store outage causes **over**-notification rather than silence.

**Verifiable checkpoint.** A live demonstration on production across **two browsers and one phone**:
every row of the truth table observed to behave correctly; reading on the phone clears the bell row,
dismisses the OS notification on the desktop and corrects the badge, in that order; a deliberate
Redis presence outage produces bell **and** push for every case; and `Email Queue` is provably
unchanged across ten notifications.

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/chat/notifications/policy.py` | new | **PURE.** `PresenceState`, `classify_presence`, `decide`, `NotificationPolicy`. **No Frappe imports, no DB access, no clock reads.** Implements the truth table including `BLUR_GRACE = 120 s`, the mention override, mute, `push_permission`, freshness, and the closed reason-code enum | Low | it is pure, exhaustively testable, and the phase's tests are written **before** it. Low risk precisely because the phase is instructed to build it first |
| `erpnext_enhancements/chat/notifications/presence.py` | new | Redis read/write, reaping, aggregation into `ClientPresence` tuples. **TTL'd, never sticky.** Fail toward "send both" when the store is unavailable | Med | the fail-open default is the safety property; the risk is a code path that treats "store unavailable" as "user is present", which silently converts the system into no notifications at all |
| `erpnext_enhancements/chat/notifications/fanout.py` | new | the real `notify_new_message` implementing Phase 2's seam, plus its background job and an idempotency marker | Med | Phase 2's suite asserts exactly-once/zero-for-echoes against the stub; implementing it must leave those assertions passing **unchanged**. If they start failing, the sync engine was duplicating and nobody noticed |
| `erpnext_enhancements/chat/notifications/bell.py` | new | `Notification Log` create/update/read, **with Frappe's per-notification email path suppressed and asserted off**. Set **both** `link` and `document_type`/`document_name` | **Med** | the `after_insert` email path is the trap: a notifiable chat message that quietly emails is a privacy event, not a UX bug. It is closed by a test asserting `Email Queue` is unchanged, which is why that test is named |
| `erpnext_enhancements/chat/notifications/deeplink.py` | new | thin wrapper importing Phase 1's `chat/links.py` builder | Low | the value is in **not** writing a second builder; research is explicit that the notification deep link and the chat-message citation link are the same link |
| `erpnext_enhancements/chat/notifications/read_state.py` | new | extends Phase 3's `mark_room_read`; `mark_all_read`; `get_unread_state` (the authoritative wholesale count) | Med | the badge must reconcile **wholesale** after any period offline, never by merging increments — a merged count drifts and never self-corrects |
| `erpnext_enhancements/chat/notifications/coalesce.py` | new | windows, token buckets, fairness across rooms | Low | 20 messages in 10 s → 1 bell row, ≤2 pushes, badge 20. Bounded and testable |
| `erpnext_enhancements/chat/notifications/webpush/vapid.py` | new | **hand-rolled** ES256 VAPID JWT: `{aud: <endpoint origin>, exp: now+12h, sub: "mailto:…"}`, signed with a P-256 key stored in a Frappe `Password` field; header `Authorization: vapid t=<jwt>, k=<base64url raw public key>` | **High** | §9-P. ~50 lines of hand-rolled auth crypto with **no library available**: a live import probe on the prod bench returned `ModuleNotFoundError` for `pywebpush`, `py_vapid`, `ecdsa` and `http_ece`, and the deploy pipeline has **no `pip install` step at all**, so adding a dependency would not survive a deploy or a VM rebuild. `cryptography 46.0.7` and `PyJWT 2.13.0` are present and are the whole toolkit |
| `erpnext_enhancements/chat/notifications/webpush/encrypt.py` | new | **hand-rolled** RFC 8188 `aes128gcm`: ephemeral P-256, ECDH against `p256dh`, HKDF twice, pad, AES-128-GCM, 86-byte header | **High** | §9-P. This is the fiddly ~120 lines. A subtly wrong salt or info string produces payloads that are **intermittently undecryptable** — the browser simply shows nothing, with no error anywhere on the server. The **RFC 8291 known-answer vector is the only thing standing between this and that failure**, and it must be a test, not a manual check |
| `erpnext_enhancements/chat/notifications/webpush/sender.py` | new | HTTP POST with `TTL` (~600), `Urgency: normal`, **`Topic`** = the room tag; status handling 201/200, 404/410 → prune immediately, 413 → drop preview and retry once, 429 → honour `Retry-After`, 5xx → backoff and `failure_count` | Med | the pruning rule is what stops a dead-subscription table growing forever and quietly failing every send |
| `erpnext_enhancements/chat/notifications/webpush/subscriptions.py` | new | `Chat Push Subscription` CRUD; **re-registering the same endpoint updates the existing row** | Med | browsers re-issue the same endpoint after a reload; a new row per reload is a classic leak |
| `…/chat/doctype/chat_push_subscription/` | new | `hash`, `track_changes = 0`. `user` (indexed); `endpoint` (**Small Text** — can exceed 255, so **no** unique index on it); `endpoint_hash` Data(64) **UNIQUE**; `p256dh`; `auth`; `user_agent`; `device_label`; `last_seen`; `last_success`; `failure_count`; `is_active` | Med | the endpoint/hash split is the non-obvious part; a unique index on `endpoint` either fails to create or truncate-collides two devices into one |
| `erpnext_enhancements/www/chat-sw.js` → **`erpnext_enhancements/www/chat_sw.js`** or `public/js/chat/desk-sw.js` | new | the service worker: `push`, `notificationclick`, `notificationclose`, `pushsubscriptionchange`, and a `message` handler for in-page dismissal. A version constant; registered with `{updateViaCache: 'none'}` and a `?v=<deploy token>` URL. **It caches nothing** | **High** | §9-Q. Three edges at once. (a) The `www/` **hyphen rule** — a hyphenated `www/` filename is never imported; the two existing workers are `www/kiosk-sw.js` and `www/wall-sw.js`, which are *served* files rather than controllers, so the rule's applicability must be confirmed rather than assumed. (b) **Scope collision (P3-3):** both existing workers register at **root scope**, and whether three different scriptURLs at one scope coexist or replace each other is **unsettled and was raised by no notes file** — if they replace each other, opening `/kiosk` and then the desk flips the root registration back and forth. (c) It **must not** precache or answer for `/assets`; both existing workers learned that expensively, and `ci.yml:471-477` guards the kiosk one for exactly that reason |
| `erpnext_enhancements/chat/notifications/api.py` | new | whitelisted endpoints only — thin wrappers with permission checks: subscribe, unsubscribe, heartbeat, `get_unread_state`, `mark_room_read` | Med | `heartbeat` is high-frequency and unauthenticated-adjacent; it needs a rate limit and must ignore rooms the caller is not in |
| `erpnext_enhancements/chat/notifications/debug.py` | new | `explain(user, room)` — why this user would or would not have been notified, with the reason code | Low | the cheapest possible support tool for a system whose failures are invisible by construction |
| `erpnext_enhancements/hooks.py` | modified | scheduler entries for subscription pruning (no success in 60 days) and the presence reaper; **the Notification Log email suppression wiring** | Med | the suppression is a hook-level assertion; if it regresses, nothing user-visible changes and email starts flowing |
| `erpnext_enhancements/tests/test_chat_notification_policy.py` | new | **bench-free, pytest, shape P. The most important suite in the phase.** The full Cartesian product `surface × room × focused × at_tail × age` written as a **literal expectation table**, not a re-implementation; zero-client → `ABSENT`; store-unavailable → `UNKNOWN`; multi-client aggregation incl. phone-focused/laptop-idle; properties P1 monotonicity, P2 staleness, P3 determinism over 1000 shuffles, P4 fail-open, P5 self; every override; and **reason-code totality** (every enum member reachable) | Low | pure and exhaustive. The only way to get this wrong is to write a test that recomputes the function, which the "literal table" rule forbids |
| `erpnext_enhancements/tests/test_chat_webpush_crypto.py` | new | **bench-free, pytest, shape P.** The **RFC 8291 known-answer vector** byte-for-byte; VAPID header shape (`aud` matching the endpoint origin, `exp` horizon, ES256); status handling against a fake push service for 201/404/410/413/429-with-`Retry-After`/500; payload size guard | **High** | §9-P. Rated High because the *absence* of this test is the risk. With it, the hand-rolled crypto is as safe as a library; without it, it is a coin flip that lands months later |
| `erpnext_enhancements/tests/test_chat_notifications_bench.py` | new | **bench-required.** Fan-out correctness over five staged presence states; **`Email Queue` unchanged across 10 notifications**; idempotency; Phase 2 regression (seam still exactly-once/zero-for-echoes); coalescing counts; fairness; read-state monotonicity; permission boundary; deep-link round-trips; and **server-side authority (I6)** with the client's suppression stubbed out entirely | High | CI does not run it, and I6 is the invariant that makes the whole design defensible: with the client lying, the server still emits zero notifications for the focused case |
| `.github/workflows/ci.yml` | modified | two shape-P steps; and confirm `scripts/check_no_committed_secrets.py` catches a pasted **VAPID private key**, adding the pattern if it does not | Med | a new secret shape that the scanner does not know about is a scanner that reports green on a leak |
| `erpnext_enhancements/chat/notifications/README.md` | new | **why there is no FCM**, in the same voice `stripe_payments` explains why there is no Stripe SDK: Frappe's `push_notification.py` is FCM-only and hardcoded to Frappe Cloud's relay; on this site `enable_push_notification_relay = 1` with credentials present but `push_relay_server_url` **null**, so `is_enabled()` returns True and `_send_post_request()` then fails at request time — it is **configured-looking and broken** | Med | without this the next person "fixes" it by adding FCM. The misleading config itself is offered as a separate one-line change (CQ-21 item 2), deliberately not folded in here |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump | Low | — |

---

## 7. Phase 5 — Triton integration

**Objective, in one sentence.** Make `@triton` work identically from Google Chat and from the SPA
through **one handler behind one normalization layer**, answering from a **gated** retrieval stack
(permission-filter before ranking, audit before content) assembled under a hard token ceiling in
prompt-cache order, streamed to the SPA with inline citations and the existing chip row preserved.

**Verifiable checkpoint.** A byte-identity test proving the same logical mention from both origins
produces **identical handler input**; T5-3 (no ungated chat SQL anywhere in the app) and T5-4 (MCP
denylist) green **before any search function exists**; `retrieve(user="Administrator")` raises; a
real `@triton` from a real Chat client and from the SPA both answer in-thread with resolvable
citations; and the ~15-question evaluation set run with its baseline recorded.

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/chat/invoke/envelope.py` | new | the frozen invocation envelope dataclass + canonical serialization | Low | small and pure; it is the contract everything else is measured against |
| `erpnext_enhancements/chat/invoke/normalize_gchat.py` | new | Chat interaction event → envelope. **No DB writes of content** | Med | this is the **interaction** path (DMs to the app and @mentions of the app) — an entirely different mechanism from Phase 2's Workspace Events firehose, and conflating them is the classic failure of this integration. Both must carry a code comment saying which is which |
| `erpnext_enhancements/chat/invoke/normalize_spa.py` | new | SPA-composed message → envelope | Low | — |
| `erpnext_enhancements/chat/invoke/handler.py` | new | **the one handler.** Consumes envelopes only; physically cannot see the origin | Med | decision #4 requires identical behaviour regardless of origin, and the way to *guarantee* it rather than hope for it is that the handler has no origin field to branch on. Test T5-1 asserts byte identity |
| `erpnext_enhancements/chat/invoke/webhook.py` | new | `allow_guest` endpoint: JWT verify (401 on failure), **sub-second ack**, enqueue, dedupe | **High** | §9-E again, plus a second edge: Google's interaction deadline is **30 seconds**, so the endpoint must ack and enqueue rather than answer inline. A handler that does retrieval inline works in dev and times out under load |
| `erpnext_enhancements/chat/retrieval/gate.py` | new | `__all__ = ["retrieve", "retrieve_for_oversight"]`. `_assert_real_user` (rejects `Administrator`/`Guest`); `_visible_room_ids(user)` **derived here, never passed in**; `restrict_to` may only **narrow** by intersection; the audit row written **before** content is returned. **The only module in the app that may run SQL against chat tables** | **High** | §9-R. This is a security boundary, not a correctness boundary. Every other High in this plan produces a wrong answer; this one produces a **correct answer to the wrong person**, with no user-visible symptom and no error anywhere. It is also the module a well-meaning optimisation will route around ("just this one query, for speed") — which is why T5-3 is a source-level scan and why it must be green before any search function exists |
| `erpnext_enhancements/chat/retrieval/rank.py` | new | **pure.** RRF hybrid ranking, recency decay, boosts | Low | pure, testable, and the failure mode is "worse answers", not "wrong reader" |
| `erpnext_enhancements/chat/retrieval/budget.py` | new | **pure.** Token budgeting and the deterministic degradation ladder against `context_token_ceiling` (default 40,000) and the six tier budgets | Low | pure arithmetic against settings the human can retune without a deploy |
| `erpnext_enhancements/chat/retrieval/assemble.py` | new | **pure.** S0–S5 assembly from already-fetched rows, in **prompt-cache order** (stable prefix first, volatile last) | Med | the ordering is a testable invariant, and ADR §I.9.1 records that Triton's current assembly does **not** already violate it — so the win is available and losing it would be a silent cost regression, not an error |
| `erpnext_enhancements/chat/retrieval/vectors.py` | new | the two-method `VectorBackend` adapter + the numpy implementation: BLOB column, in-process cosine over the **permission-filtered** candidate set | Med | production MariaDB is `10.11.18` — **no `VECTOR` type, no `VEC_*` functions** — measured by a live `SELECT VERSION()`, which closes Phase 0 §4.J's "on 11.8 it may flip" branch against the flip. `numpy 2.5.1` is already installed, so this adds no dependency on a host that cannot install one. The adapter is what makes the revisit a one-file change |
| `erpnext_enhancements/chat/retrieval/citations.py` | new | manifest construction + **server-side** URL resolution | Med | resolving URLs server-side is what stops a model-authored `javascript:` or cross-room link becoming a live anchor. Phase 3's renderer fixtures must be re-run **unchanged** |
| `erpnext_enhancements/chat/indexing/chunker.py` | new | **pure** boundary logic: seal at ≥1,200 tokens, ≥20 messages, ≥45-minute gap, thread boundary, or ≥30-minute idle tail. **The tail stays unsealed and is excluded from the semantic index** | Med | the tail rule is the largest avoidable cost in the design (an embedding call per chat message otherwise). Its correctness argument — the current room's tail is covered verbatim by T1, other rooms' tails by the lexical tier and the next digest pass — belongs in the module docstring because a reviewer **will** challenge it |
| `erpnext_enhancements/chat/indexing/embed.py` | new | `gemini-embedding-001` client; `RETRIEVAL_DOCUMENT` when indexing, `RETRIEVAL_QUERY` when querying; **manual re-normalisation for non-3072 dims** (`v / np.linalg.norm(v)`) | Med | skipping the re-normalisation **silently corrupts cosine similarity** — the documented #1 mistake with MRL truncation. It produces plausible-looking but subtly wrong retrieval, which is the worst failure shape |
| `erpnext_enhancements/chat/indexing/digest.py` | new | rolling summarization as a **scheduler-driven batch over dirty-room counters** — never a per-message enqueue. Full rebuild when an edit or delete lands inside a covered span; poison-pill guard; staleness health check | **High** | §9-L. This is where D6's three-value watermark either works or does not. A digest keyed on `seq` alone will serve a summary containing a message the user just deleted — a privacy failure that looks like a caching bug. Phase 2's `mark_room_context_stale` seam exists precisely so this wiring is plumbing rather than surgery |
| `erpnext_enhancements/chat/triton_client.py` | new | HTTP client for the Triton service, **streaming-aware**, carrying the mentioning user's identity | Med | Triton's API **cannot be told who is asking** on the chat path today; the impersonation mechanism that exists is separate and already in production. The identity mechanism must be stated exactly, not assumed. `VERIFY:` **P3-7** — whether Triton's existing Google OAuth scope set includes any Chat scope; **do not guess a scope string** (a wrong one forces re-consent for every existing user) |
| `…/chat/doctype/chat_context_chunk/` | new | `hash`; `room`; `thread`; `first_message`/`last_message`; `first_seq`/`last_seq`; `body`; `token_count`; `content_hash`; `embedding` (BLOB); `embedding_model`/`_dim`/`_version`; `participants` (JSON); `sealed` | Med | volume is messages ÷ ~15; the `(room, first_seq)` and `(room, last_seq)` indexes are what keep the permission-filtered candidate scan bounded (cap N = 8,000) |
| `…/chat/doctype/chat_room_digest/`, `…/chat_thread_digest/` | new | `digest_version` (monotonic, embedded in every cache key); `generation_count`; `watermark_seq`; `watermark_modified`; `summary_text`; `token_count`; `covered_from`/`covered_to`; `is_stale`; `rebuild_failures`; `model_used`; `generated_at` | Med | `digest_version` in the cache key is what makes an invalidation actually invalidate |
| `…/chat/doctype/chat_retrieval_audit/` + `…/chat_retrieval_audit_room/` | new | decision #12's privileged-read log; `(accessed_by, creation)` and `(actor_type, creation)` indexes; System Manager **read** only | **High** | §9-R. The write must be **fail-closed**: if the audit row cannot be written, retrieval does not return content (test T5-10). An audit that fails open is not an audit |
| `…/chat/doctype/triton_invocation_log/` | new | tokens, cache-hit rate, retrieval time, end-to-end latency, `request_id`, mentioning human, room, message | Low | instrumentation; its value shows up in Phase 6's G6-8 correlation assertion |
| `erpnext_enhancements/assistant_tools/_gate.py` | **modified** | add `CHAT_DENYLIST_DOCTYPES` and refuse chat DocTypes in the `_safe_execute` seam — **including with AI gating switched off** | **High** | §9-S. The three generic MCP tools live in **Frappe Assistant Core, an app in neither repo**, and `run_database_query` executes raw SQL, so **no Frappe permission mechanism touches it** — the zero-DocPerm doctrine closes two of three surfaces and not the third. The `_safe_execute` seam is already built and already in production and sees every FAC tool call including built-ins. The refusal must not depend on `ai_write_gating_enabled`, or turning gating off turns the denylist off with it |
| `erpnext_enhancements/patches/add_chat_fulltext_index.py` | new | MariaDB **FULLTEXT** on `Chat Message.text_plain` for the lexical tier | Med | `VERIFY:` **`bench migrate` does not drop a hand-added FULLTEXT index** — add, migrate, migrate again, check. If it does, the lexical tier silently degrades to nothing after the next deploy |
| `erpnext_enhancements/hooks.py` | modified | `scheduler_events` for the digest pass (5-minute, over dirty counters) and the embedding backfill; `doc_events` wiring `mark_room_context_stale` on edit and delete | Med | the 5-minute cron must be staggered clear of the existing clusters, same as Phase 2's sweeper |
| `erpnext_enhancements/tests/test_chat_mcp_denylist.py` | new | **bench-free, unittest, shape A** — appended to the multi-module step at `ci.yml:146-154`, the **one** case where appending is right, because it needs the stub set `test_assistant_tools_schema` installs. Four assertions: DocType JSONs carry no permissions except `Chat Room`; every chat DocType name is in `_gate.CHAT_DENYLIST_DOCTYPES` by **set equality against the filesystem**; the gate refuses **with gating OFF** for `get_document`, `list_documents` and `run_database_query` incl. the evasions (no backticks, mixed case, leading `/* comment */`, a JOIN in a subquery, `information_schema.columns` filtered on the table name); and the seam is still attached | **High** | §9-S. The set-equality assertion is what makes a **newly added** chat DocType fail by default instead of silently escaping the denylist — the identical failure mode the existing `test_every_registered_tool_is_classified` exists to prevent |
| `erpnext_enhancements/tests/test_chat_retrieval_pure.py` | new | **bench-free, pytest, shape P.** Chunk boundary determinism and idempotent replay; ranking; budget + degradation ladder (build **every** row); assembly order; the boolean-mode query builder with input escaping; citation manifest construction | Low | pure |
| `erpnext_enhancements/tests/test_chat_gate_source_scan.py` | new | **bench-free, unittest, shape U.** **T5-3:** every SQL literal under `erpnext_enhancements/chat/**` lives in `gate.py` **and** contains `allowed_rooms`; the failure message names the file and the statement | **High** | §9-R. This is the fence, and per the phase plan it must be green **before the field is built**. A scan that runs after the code exists is a scan that will be argued with |
| `erpnext_enhancements/tests/test_chat_triton_bench.py` | new | **bench-required.** T5-5 (`Administrator`/`Guest` raise); T5-10 (audit failure blocks retrieval); T5-11 (an edit or delete busts the cache — the three-value watermark); T5-12…T5-15 (budget and assembly); the evaluation set runner | High | CI does not run it; T5-11 is D6's named invariant and the design's most likely bug |
| `triton:` — the sibling bridge | **modified (triton repo)** | ~150 lines: a sibling bridge endpoint + its own secret, rather than reusing `ERPNEXT_GATEWAY_SECRET`; plus `docs/convergence.md` gains a chat row; plus Triton's own version/CHANGELOG lockstep | Med | reusing the existing secret costs ~0 lines and leaves an endpoint named "erpnext" serving Chat while sharing a blast radius with the telephony gateway. **CQ-24** is the human's call, and it includes the sub-question of what Triton does for a mentioning user who has **not** linked ERPNext (today the turn raises before the first token) |
| `.github/workflows/ci.yml` | modified | two shape-P steps, one shape-U step, and one **append** to `ci.yml:146-154` | Med | the append is deliberate and is the only one in the whole plan; anywhere else it is the bug |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump in *both* repos; plus an **ADR addendum** recording every decision this phase made that the ADR left open (assembly split, vector backend, DM threading behaviour, sources-dropdown outcome, MCP identity mechanism) | Low | the ADR is immutable once accepted; an addendum is the sanctioned route |

---

## 8. Phase 6 — Governance, audit, retention, hardening & rollout

**Objective, in one sentence.** Make the system defensible and operable — an oversight role and
viewer, append-only tamper-evident access auditing, retention with a dry run, an itemised hardening
pass, alerting, drift reconciliation, a tested degradation to ERPNext-only chat, and a rollout a
non-engineer can execute.

**Verifiable checkpoint.** The **final definition of done**: all fourteen locked decisions, each with
its verification method **executed** and its result recorded; G6-1 through G6-22 green; the
Chat-dark drill run for 30 minutes with two real users and then drained with zero duplicates and zero
dead letters; a full export downloaded, unzipped, hashes verified and `transcript.html` read; and the
pilot acceptance checklist executed by an actual non-engineer.

**The fourteen, enumerated here so the count is self-checking** — this is the final definition of
done and a miscount in it silently drops an acceptance item. Sourced from `00_MASTER_PROMPT.md` —
its §4 numbers #1–#14 inclusive, with §3 additionally spotlighting `LOCKED DECISION #13` because
Phase 0 is the phase that must decide it:

| # | Locked decision | # | Locked decision |
|---|---|---|---|
| **#1** | ERPNext is the source of truth | **#8** | frontend extends the existing floating widget |
| **#2** | sync is fully bidirectional | **#9** | V1 feature scope (DMs, group spaces, threads, attachments, presence, typing, receipts) |
| **#3** | exactly two notifications, both fired by ERPNext, never email | **#10** | space mapping — all three provisioning modes |
| **#4** | Triton invocation: both paths into one shared handler | **#11** | Google auth — left to the implementing agent |
| **#5** | Triton behaviour: in-thread replies, acts as the mentioning user | **#12** | retention and governance / audited non-participant reads |
| **#6** | Triton context scope: all three tiers, caching first-class | **#13** | app placement — extend `erpnext_enhancements` (`DECISIONS.md` D1) |
| **#7** | citations panel preserved exactly, plus inline links | **#14** | workflow: plan first, implement on approval |

**Fourteen is correct and is deliberately not "corrected" to thirteen.** The ADR's Context paragraph
says *"Thirteen **product** decisions were locked"* and then enumerates only #1–#12; that count
excludes **#14**, which is a workflow decision rather than a product one. The prompt this checkpoint
is written against, `PHASE_6_governance_audit_rollout.md`, says **fourteen** three times — §2
item 10, §6 step 19, and §9 checkpoint item 12 — each pointing at its own §10. Phase 6 verifies
fourteen.

| path | new/modified | what changes | risk | why that risk |
|---|---|---|---|---|
| `erpnext_enhancements/chat/governance/inventory.py` | new | the four inventories as **code, derived from the filesystem/AST**: every chat DocType, every `@frappe.whitelist()` in the package, every rendering surface, every secret | Med | three of the four become tests (G6-1, G6-5, G6-14). An inventory typed by hand is an inventory that is wrong within a month |
| `erpnext_enhancements/patches/seed_chat_oversight_role.py` | new | the oversight Role with **`read` only** on every chat DocType — `write`/`create`/`delete`/`submit`/`cancel`/`amend` all 0 — reaching users through a Role Profile | Med | G6-2 asserts it. A `write` permission on an audit table is the difference between an audit trail and a diary |
| `erpnext_enhancements/chat/governance/audit_chain.py` | new | the `chain_hash` field, its canonical serialization, the chain computation and the verifier | **Med** | tamper-evidence, not tamper-prevention, and the ADR must say so plainly: it detects a row mutated directly by SQL (G6-21) and reports the **first break by name**; it does not stop a DBA. Claiming more than that is the risk |
| `erpnext_enhancements/chat/governance/immutability.py` | new | controller guards + the source-level guard: no `db_set` / `set_value` / `db.delete` / raw `UPDATE`/`DELETE` against the audit tables outside the single allowlisted writer and the retention job | **High** | §9-T. G6-11 must make **every** route fail — `doc.save()`, `frappe.delete_doc(ignore_permissions=True)`, desk bulk delete, as the oversight role, as System Manager, **as Administrator**. This is "build the vault before filling it", and the phase plan sequences it before any new writer exists for exactly that reason |
| `erpnext_enhancements/chat/governance/viewer.py` + `erpnext_enhancements/www/chat_admin.html` / `chat_admin.py` | new | the admin conversation viewer — a **new, separate** surface, never a refactor of the SPA. Threaded, cross-room search, tombstones rendered, an audited expand of original content, and a **mandatory `reason`** field | High | G6-10: a read with an empty or too-short reason must be **refused**, not accepted-and-blanked. And G6-6: opening a room the auditor is not in must not establish a realtime subscription to that room's doc room. The `www/` hyphen rule applies again |
| `erpnext_enhancements/chat/governance/export.py` + `…/doctype/chat_export_request/` | new | the export bundle: background job, `messages.jsonl`, `revisions.jsonl`, attachments, `transcript.html`, a manifest of sha256 hashes, and a download endpoint with **its own** audit row | High | G6-9: the deleted body appears in `revisions.jsonl` and **not** in `messages.jsonl`; every hash verifies; a re-export is byte-identical apart from timestamps and the export id. An export is the artefact that leaves the building, so its contents are a legal question wearing a serialization costume |
| `erpnext_enhancements/chat/governance/access_report.py` | new | the unified `Chat Access Report` over both audit DocTypes | Med | G6-7 is the acceptance bar: **exactly one** audit record per non-participant read via **every** path — viewer, oversight search, export, download, `frappe.get_list`, report view, `/api/resource` — and **zero** for a participant doing the same (I9) |
| `erpnext_enhancements/chat/governance/retention.py` | new | the purge/archive job with a **dry run**; digest and chunk invalidation for anything purged; the survives-a-purge table; `retention_mode` shipped **Disabled** | **High** | §9-U. It is the only code in the system that deliberately destroys data, and it must not eat the audit trail, must not purge a message mid-relay, and must invalidate every digest covering a purged span — or Triton keeps answering from summaries of messages that no longer exist. G6-13 also requires Phase 5's T5-16 to still pass |
| `erpnext_enhancements/chat/governance/drift.py` + `…/doctype/chat_drift_report/` | new | detection and classification for D1–D7; a settling window; repairs routed **through the existing relay/ingest paths**; a three-part loop guard (per-object cap → demote on the third attempt, per-run cap → repair nothing and alert). `drift_repair_enabled` ships **off** | High | an auto-repairer with a bad classifier is a bot that fights the other system at 1 write/second. The three caps are what make its worst case an alert rather than an incident |
| `erpnext_enhancements/chat/governance/alerts.py` + `…/doctype/chat_ops_alert/` | new | the alert record, the delivery path **with its own failure mode handled**, an email fallback, thresholds as `Chat Settings` fields, an alert rate limit and a daily digest | Med | G6-18: alerting must not re-enable Frappe's `Notification Log` `after_insert` email path — the operational alert channel and the suppressed user-notification channel must not be the same wire |
| `erpnext_enhancements/chat/governance/README.md` + `docs/runbooks/chat_operations.md` | new | the operations runbook for the five most likely production incidents | Med | a runbook nobody wrote is a 2 a.m. reconstruction from source |
| `erpnext_enhancements/tests/test_chat_governance_pure.py` | new | **bench-free, pytest, shape P.** URL-scheme allowlist and anchor builder; canonical serialization for `chain_hash`; chain computation and verification; retention eligibility (row `seq`, timestamps, audit linkage, settings → keep/purge/archive); drift classification; drift repair-eligibility incl. the settling window and both caps; alert-threshold evaluation; export manifest builder | Low | pure, and it is where most of this phase's judgement lives |
| `erpnext_enhancements/tests/test_chat_permission_surface.py` | new | **bench-free, unittest, shape U.** G6-1 (every chat DocType has both hooks or is in `PERMISSION_EXEMPT` **with a reason**, and a newly added DocType fails by default), G6-5 (every whitelisted chat method is rate-limited or in `RATE_LIMIT_EXEMPT` with a reason), G6-12 (the audit-table write scan) | **Med** | "a newly added DocType fails by default" is the property that makes this test still work in a year, when nobody remembers it exists |
| `erpnext_enhancements/tests/test_chat_governance_bench.py` | new | **bench-required.** G6-3, G6-4, G6-6, G6-7, G6-9, G6-10, G6-11, G6-13, G6-15, G6-16, G6-17, G6-19, G6-20, G6-21, G6-22 | High | fifteen named tests CI does not run, several of which are the project's security claims |
| `.github/workflows/ci.yml` | modified | one shape-P step, one shape-U step | Low | — |
| Manual, recorded with evidence | — | the eight-case JWT curl matrix; the **Cloud Armor payload check** with all three strings arriving intact; the rate-limit burst; an SVG-with-script attachment fetched in a real browser with headers inspected; the hostile-input corpus in the SPA, the viewer and an exported `transcript.html` with the console open; a full export verified; the 30-minute Chat-dark drill; the pilot checklist run by a non-engineer | **High** | §9-V. **As declared in Terraform there is no Cloud Armor policy in front of ERPNext today** (ADR §J.2) — and Terraform not declaring it does not prove GCP does not have it; `timeout_sec` is the precedent for an out-of-band console change Terraform does not know about, and nobody has read the live backend service. On that reading Cloud Armor is a *precondition to be authored*, not a fix to be verified — and OWASP SQLi/XSS preconfigured rules **false-positive on user-typed chat text**, so a coworker pasting a SQL snippet or an HTML tag gets a 403 with no feedback. Automated tests cannot see the load balancer, the WAF, the Chat client, or a browser's DOM sinks |
| `erpnext_enhancements/__init__.py`, `package.json`, `CHANGELOG.md` | modified | **MINOR** bump; plus a final **ADR addendum** and the new chat DocTypes added to the shared MCP denylist constant | Low | — |

---

## 9. The risk register — every High, justified

**Forty-three rows above are rated High** — 32 written in bold and 11 plain, across the six phase
tables. They consolidate into **twenty-two register entries**, labelled §9-A through §9-V; four of
those (§9-H/§9-I/§9-J/§9-K) share a single paragraph because they fail together. Each entry is named
here with the specific failure and the specific thing that catches it. Where the phase prompts and
the ADR already name a mitigation, it is cited rather than re-argued.

**§9-A — `Chat Message` DocType JSON (Phase 1).** Four properties are effectively unfixable once
data lands: `autoname: hash` (a naming series is a shared counter row every insert must
read-modify-write, serialising inserts on a table taking thousands of writes a day); `sort_field:
creation` (Raven's JSON defaults to `modified DESC`, which teleports an edited message to the bottom
of a transcript); `track_changes = 0` (otherwise every edit writes a `Version` row with a JSON diff,
and decision #12's trail is served by a purpose-built table whose shape we control); and the unique
index on `gchat_message_name` at **`varchar(255)`** rather than Frappe's `Data` default of 140 —
too short and the index either truncate-collides two distinct Google messages into one, which is
**silent message loss**, or fails to create. *Caught by:* the Phase 1 checkpoint's literal
`SHOW INDEX FROM \`tabChat Message\`` and the "insert two rows with the same `gchat_message_name`
and expect `DuplicateEntryError`" acceptance criterion. *Residual:* **F-V1**, whether Frappe's
`""`→`NULL` coercion fires end-to-end for a `unique: 1` DocField — settle by inserting two rows with
the field unset; failure is production-only and looks like a random insert error.

**§9-B — the keyless DWD assertion builder (Phase 1).** `google.auth.impersonated_credentials`
has **no `subject` parameter**, so the assertion is hand-rolled: ~40–50 lines constructing
`iss`/`sub`/`scope`/`aud`/`exp ≤ 10 min`, signed by IAM Credentials `signJwt`. This is a
hand-rolled security primitive on a host whose deploy pipeline is
`fetch/reset → migrate → build → FLUSHDB → restart` with **no `pip install` step at all**, so there
is no library to fall back to and never will be without changing the pipeline. *Caught by:* the
pure-function claim-set test, plus smoke-test step 2 and the live `signJwt` curl — a `signedJwt`
in the response proves the token-creator binding; a `403` means the binding or the VM's
`cloud-platform` scope is wrong and must be fixed rather than worked around. *Mitigation that
matters more than the tests:* it is **keyless** — no private key file on disk, no rotation, no
secret in `site_config.json`.

**§9-C — subscription renewal (Phase 2).** An expired Workspace Events subscription is
**permanently deleted and cannot be renewed**; recovery is a *new* subscription. The failure is
silent, total and one-way: inbound sync simply stops, and a renewal job that only ever patches will
never notice a subscription is missing entirely. *Caught by:* three **separate** alarms, because
they have three separate causes — `expiring` (the renewal job is broken), `suspended` (a per-user
problem, most often that person revoked a grant, and `USER_SCOPE_REVOKED` must alert **by name**),
and `missing` (the create path never ran, or it expired and was deleted). *Mitigated by:* the
verified TTL configuration — `includeResource: false` buys **up to 7 days**, `ttl` is input-only and
omitting it requests the maximum, and `expireTime` is **always** returned on output — combined with
**hourly** renewal a **full day** early, and the reconciliation sweep, which converts a missed
renewal from data loss into lag. That mitigation is why this is High and not Critical.

**§9-D — the Chat client's two silent parameters (Phase 1).** Omitting `messageReplyOption` means
`MESSAGE_REPLY_OPTION_UNSPECIFIED`, which **silently starts a new top-level thread and ignores any
thread id you passed** — a wrong result that returns 200. And `createMessageNotificationOptions`,
the parameter locked decision #3 rests on, is confirmed to **require app authentication**, which is
mutually exclusive with human attribution. *Caught by:* smoke-test step 7, which posts a threaded
reply and asserts the reply's `thread.name` matches — proving threading works before Phase 5 depends
on it. *Not caught by anything in Phase 1:* the notification half, which is **CQ-1** and belongs to
the human (see §10).

**§9-E — the two `allow_guest` webhooks (Phases 1 and 5).** `allow_guest=True` makes an endpoint
world-reachable; the JWT is the only thing between it and an open relay. The specific trap:
`verify_oauth2_token` does **not** check the issuer claim, so a generic ID-token verification
accepts **any** Google ID token minted for your URL. IP allowlisting is not an alternative — Google
publishes no stable Chat egress ranges, and the reverse error (allowlisting a range Google does not
guarantee) breaks silently, which is why JWT-only is an accepted risk rather than a gap. *Caught
by:* the six negative tests, including the two that matter (valid Google token with the wrong
issuer; valid token with the wrong audience); the live unauthenticated `curl` returning **401**; and
the ordering test — malformed body with a bad token returns 401 **with no DB write**, proving the
verifier runs before parsing.

**§9-F — the permission hooks and every raw query (Phases 1 and 3).** Three compounding hazards in
one place. On v16 a `has_permission` hook that falls off the end returning `None` now **denies**, so
a missing `return True` is a silent lockout rather than a leak — annoying but safe. The other two are
not safe: the query-condition string is concatenated into SQL, so `frappe.db.escape(user)` is
mandatory; and these hooks **do not protect `frappe.db.sql`**, which is exactly what history paging
and search will be written in. *Caught by:* the source-level raw-SQL guard (every occurrence in an
allowlisted module **and** containing the membership constraint), and the bench-required suite that
tests the **report view** specifically — the path people forget. *Standing acceptance bar:* "even a
raw report view cannot leak another user's rooms."

**§9-G — the app's first `website_route_rules` (Phase 3).** The rule itself is settled: v16 uses
werkzeug `Rule` verbatim, `<path:name>` is available, and ERPNext ships a working in-production
precedent. What is **not** settled is the operational edge: loading `/chat/room/X` **before** the
rule ships caches that URL in `website_404` until Redis is flushed. A full deploy FLUSHDBs both Redis
instances and saves us; a hotfix without a restart does not. *Caught by:* `curl -I
https://<host>/chat/room/ROOM-123?message=MSG-456` returning 200 and serving the shell, executed as a
**gate** before any deep-link feature is built. *Consequence if missed:* every deep-link acceptance
criterion in decisions #3 and #8 fails, and the notification deep link — the whole point of decision
#3 — lands on an error page.

**§9-H / §9-I / §9-J / §9-K — the sync engine's four crux modules (Phase 2).** Grouped because they
fail together. The **relay state machine** is High because `sync_state` is what the SPA renders and
what an operator reads at 2 a.m., and any state assigned outside the single transition function is a
state no test covers. The **sweeper** is High because the prod deploy **FLUSHDBs the queue Redis**,
so queued relay jobs do not survive a deploy — the sweeper, not the queue, is the delivery
guarantee, and this has already been confirmed as the cause of missing Drive folders on this site.
**Ingest** is High because acking before the raw row commits loses events on a worker crash, doing
real work before acking causes redelivery storms, and `SELECT`-then-`INSERT` is a TOCTOU bug at
exactly the moment it matters. **Echo suppression** is High because a mis-suppressed echo is relayed
again, and at 1 write/second per space the room fills with duplicates within seconds. *Caught by:*
the fake Chat API harness with `event-before-response` fault injection (the Phase 2 prompt's §4.D race), the
five-times-from-two-workers replay test, the "kill the worker" test, the seventeen named chaos tests,
and the 200-message soak run twice. *Standing anti-requirement:* **no similarity heuristic on message
text** — if the design needs a test asserting that two identical short messages matched, the design
is wrong.

**§9-L — the three-value watermark (Phases 2 and 5).** `DECISIONS.md` **D6**: edits and deletes do
**not** advance `seq`, so a cache or digest watermark tracking `seq` alone will serve context
containing a message the user just **deleted**. Every digest, chunk and cache key must key on
`(max(seq), count(*), max(modified))`. *Why High:* the symptom is Triton confidently summarising
deleted content to someone who watched it disappear — a privacy failure that presents as a caching
bug and will be triaged as one. *Caught by:* test T5-11, written **before** the code, plus Phase 2's
assertion that `mark_room_context_stale` is called on every edit and delete with the correct span.

**§9-M — space provisioning and `spaceThreadingState` (Phase 2).** Space writes are capped at
**60/minute** and membership writes at **300/minute** project-wide, so a first-run org sweep trips
both immediately — hence a throttled resumable job with a checkpoint row and a dry run, never a loop.
The sharper edge: `spaceThreadingState` is documented **Output only**, and Google has historically
made threading immutable after creation. If it is create-time-only and we get it wrong, **every space
must be recreated** — in a live domain, in front of real employees. *Caught by:* ADR §G.9.3's
five-minute live call, executed **before any threading design is written**, which is why this is
listed in §10 as a Phase-2 gate rather than as a task.

**§9-N — the two-Vue-copies hazard (Phase 3).** This hazard **does not exist today** — Appendix A
§11 establishes that — and a bundled-Vue SPA is precisely what would create it. Two Vue runtimes on
one page give broken reactivity across the widget/SPA boundary, doubled bundle size, and bugs that
present as "the widget stopped updating after I opened the SPA", six weeks after the change that
caused them. *Caught by:* a **runtime** check that fails if more than one Vue runtime is detected, and
a **build-time** check that the widget bundle and the SPA bundle do not both contain a Vue copy.
"We were careful" is not a guard — a dependency added six months from now will undo it. *Decision
required and recorded:* reuse the vendored global `window.Vue` as a bundler external; migrate the
widget onto the SPA's bundled Vue; or hard-isolate so the two never share a page.

**§9-O — extending the widget with no test harness (Phase 3).** Locked decision #8 forbids forking
the widget, so Phase 3 modifies a 1404-line anonymous IIFE in place. Its regression contract is
Appendix A §14 — **and today that table is defended by nothing**, because
`erpnext_enhancements/tests/test_triton_personas.py` is a bench-free pytest suite referenced
**nowhere** in `ci.yml` (`grep -c "test_triton_personas" .github/workflows/ci.yml` → **0**) and has
therefore never run. It is the only automated coverage of `triton_chat.py`. There is additionally
**no JS test runner in the repo and no test of any kind for `triton_widget.js`**. *Caught by:*
§12's first commit (wiring that suite in, two lines), plus standing up Vitest as its own CI step and
proving it with a deliberate red run. *Until both land, every "streaming must survive" row in
Appendix A is an assertion with no assertor.*

**§9-P — hand-rolled Web Push crypto (Phase 4).** A live import probe on the production bench
returned `ModuleNotFoundError` for `pywebpush`, `py_vapid`, `ecdsa` and `http_ece`; the deploy
pipeline has **no `pip install` step**, so a new PyPI dependency would not be installed by a deploy
and would be lost on any VM rebuild. So ~50 lines of ES256 VAPID plus ~120 lines of RFC 8188
`aes128gcm` are written by hand against `cryptography 46.0.7` and `PyJWT 2.13.0`. The failure mode is
the worst available: a subtly wrong salt or info string produces payloads the browser cannot
decrypt, and it shows **nothing** — no error on the server, no error in the console, just silence.
*Caught by:* the **RFC 8291 published test vector** as a byte-for-byte known-answer test. That single
test is the difference between "as safe as a library" and "a coin flip that lands in month three".
*Also note:* Frappe's built-in path is not merely unconfigured but actively misleading —
`enable_push_notification_relay = 1` with credentials present and `push_relay_server_url` **null**
means `is_enabled()` returns True and the send fails at request time. The module README must say why
FCM is not the answer, or someone will "fix" it.

**§9-Q — the service worker (Phase 4).** Three edges at once. The `www/` **hyphen rule** — a
hyphenated `www/` controller is never imported by Frappe, and `stripe-return.py` was broken this way
from the day it was written. **Scope collision (P3-3)** — both existing workers (`www/kiosk-sw.js`,
`www/wall-sw.js`) register at **root scope**, and whether three different scriptURLs at one scope
coexist or replace one another is **unsettled**; this risk was raised by **no notes file** and is
cheap to settle from the Service Workers spec or `navigator.serviceWorker.getRegistrations()`. If
they replace each other, a user who opens `/kiosk` and then the desk flips the root registration back
and forth, and push silently stops. And **caching** — this worker must not precache or answer for
`/assets`; both existing workers learned that expensively, and `ci.yml:471-477` guards the kiosk one
for exactly that reason. *Also carried:* **P3-4**, iOS Safari requires add-to-Home-Screen PWA
installation for Web Push, which changes what we can promise field staff and belongs in rollout
comms, not in the architecture.

**§9-R — the retrieval permission gate (Phase 5).** **This is a security bug, not a correctness
bug, and that is the whole reason it is High.** Every other High in this plan produces a wrong or
missing answer; this one produces a **correct answer to the wrong person**. There is no user-visible
symptom, no error, no failed request — a manager asks Triton a question and gets a synthesis of a
conversation they were never a member of, and nobody finds out. Three structural defences, all of
which must exist before any search function does: `_visible_room_ids(user)` is **derived inside the
gate and never passed in** (a caller-supplied allow-list is the same bug with extra steps);
`restrict_to` may only **narrow** by intersection; and the audit row is written **before** content is
returned, **fail-closed** — if the audit write fails, retrieval returns nothing (T5-10). *Caught
by:* **T5-3**, a source-level scan asserting every SQL literal under `erpnext_enhancements/chat/**`
lives in `gate.py` and contains `allowed_rooms`, green **before the field is built**. A scan added
after the code exists is a scan that gets argued with.

**§9-S — the MCP denylist across an app we do not own (Phases 1 and 5).** The three generic tools
(`run_database_query`, `get_document`, `list_documents`) live in **Frappe Assistant Core**, an app in
**neither repo**, with the import direction strictly FAC → us. The zero-DocPerm doctrine closes two
of the three surfaces; it does **not** close `run_database_query`, which is role-gated and executes
raw SQL, so no Frappe permission mechanism touches it. The mechanism that does is
`assistant_tools/_gate.py`'s `_safe_execute` seam — already built, already in production, and it
sees every FAC tool call including built-ins. *Caught by:* `test_chat_mcp_denylist.py`'s four
assertions, of which two carry the weight: the refusal is tested **with AI gating switched off**
(proving it does not depend on `ai_write_gating_enabled`), and denylist membership is asserted by
**set equality against the filesystem**, so a newly added chat DocType **fails by default** instead
of silently escaping. *Residual:* the seam's *attachment* is covered by a bench-only canary CI does
not run — recorded, not solved, plus a Phase 6 manual acceptance step (issue `run_database_query`
with ``SELECT COUNT(*) FROM `tabChat Message` `` as a real System Manager and expect the refusal
envelope, not a number).

**§9-T — audit immutability (Phase 6).** The audit table is where deleted content lives, so it needs
**tighter** permissions than the message table, not the same ones. G6-11 must make every route fail:
`doc.save()`, `frappe.delete_doc(ignore_permissions=True)`, desk bulk delete, as the oversight role,
as System Manager, **and as Administrator**. The retention flag path is the only one that may
succeed, and only within policy. *Caught by:* building it **first** — the phase plan puts G6-11 and
G6-12 green *before any new writer exists*. *Honest limit, which the ADR must state:* `chain_hash` is
**tamper-evidence, not tamper-prevention**; it detects a row mutated directly by SQL and names the
first break, and it does not stop someone with database access.

**§9-U — the retention purge (Phase 6).** The only code in the system that deliberately destroys
data. Four ways to get it wrong, each with a named test: eating the audit trail (`audit_survives_purge`
must hold), purging a message mid-relay, failing to invalidate digests and chunks covering a purged
span (after which Triton answers from summaries of messages that no longer exist), and regressing
Phase 5's T5-16. *Caught by:* G6-13 walking the full survives-a-purge table, plus a **dry run** as a
first-class mode. *Mitigated by:* shipping `retention_mode = Disabled`, so the destructive path is
opt-in and the human turns it on deliberately.

**§9-V — Cloud Armor and everything else only a human can see (Phase 6).** **As declared in
Terraform there is no Cloud Armor policy in front of ERPNext today** (ADR §J.2) — the vendored module
supports an attachment point but `infra/configs/load_balancer.yaml` sets neither `security_policy`
nor `edge_security_policy`, and no `google_compute_security_policy` resource exists in the repo.
**Terraform not declaring it does not prove GCP does not have it**: `timeout_sec` is the precedent for
an out-of-band console change Terraform does not know about, and the live backend service has not been
read. Settle it with the one read-only `describe` in §10.2 before treating either answer as fact. On
the Terraform reading this is a **precondition to be authored**, not a fix to be verified. When a
policy is attached, OWASP SQLi/XSS preconfigured rules **false-positive on user-typed chat text**: a coworker
pasting a SQL snippet or an HTML tag gets a 403 with no feedback and the message simply vanishes.
A higher-priority allow rule on the ingest and relay paths is required. *Caught by:* the manual
payload check with all three strings arriving intact — automated tests cannot see the load balancer,
the WAF, the Chat client, or a browser's DOM sinks, which is why eight verification items in Phase 6
are explicitly manual and evidence-recorded.

**One risk deliberately rated Med that a reader may expect to be High:** vector storage. It is Med
because production MariaDB is `10.11.18` (measured, no `VECTOR` type), `numpy 2.5.1` is already
installed so nothing is added to a host that cannot install anything, and the two-method
`VectorBackend` adapter makes the swap a **one-file change by construction**. The revisit triggers
are numeric and stated: p95 retrieval > 400 ms, > 20k candidate chunks after filtering, > 250k chunks
total, or a MariaDB upgrade to 11.8 LTS.

---

## 10. Cross-phase dependencies and the gates

### 10.1 What each phase must be able to assume from its predecessors

| Phase | Must be able to assume | Which is false if… |
|---|---|---|
| **1** | Nothing from a predecessor. It assumes only the **human's answer to CQ-1** and a completed Google Cloud / Workspace runbook | the DWD scope list is not frozen, or the runbook's twelve checks were not walked |
| **2** | The schema exists and migrates cleanly on a **fresh** site; `Chat Relay Job` / `Chat Inbound Event` / `Chat Event Subscription` exist as tables; the transport client exists with dry-run and a single `_request()`; `seq` and the three-value watermark exist; the permission hooks return explicit booleans; the webhook verifies | Phase 1 shipped the schema but not the indexes (the patch is what creates them), or shipped `seq` as a derived value rather than an explicit column |
| **3** | Rows arrive from **both** origins and converge; `sync_state` is meaningful and only ever set through one transition function; the four denormalised last-message columns (`last_message`, `last_message_at`, `last_message_sender`, `last_message_preview`) are populated (the room list renders with **zero joins** off them); realtime `message_created`/`edited`/`deleted` fire on the room's **doc room** with `after_commit=True`; attachments are private `File`s on the message row; `chat/links.py` exists | Phase 2 published realtime without `after_commit=True` (the client re-reads the DB immediately and sees nothing), or left the denormalised fields unwritten |
| **4** | The SPA and the extended bubble exist and can emit a **heartbeat** carrying surface, room, focus and at-tail; `mark_room_read` exists as the single writer; `chat/links.py` builds absolute URLs; Phase 2's `notify_new_message` seam is called **exactly once** per genuinely new message and **zero** times for echoes | Phase 3 shipped presence as a client-owned sticky flag rather than a server-owned TTL'd heartbeat — in which case the truth table's inputs are all `UNKNOWN` and every row takes the fail-open branch |
| **5** | `seq` and the three-value watermark; `mark_room_context_stale` called on every edit and delete with the correct span; the permission hooks and the zero-DocPerm split; `Chat Message.text_plain` suitable for a FULLTEXT index; Phase 3's citation renderer contract and its fixtures | Phase 2 wired the staleness seam to fire only on create, in which case every digest is quietly wrong after the first edit |
| **6** | Everything, plus the hook point Phase 1 left for the non-participant audit write, and Phase 5's `Chat Retrieval Audit` | Phase 1 scattered audit calls instead of leaving one function the read path calls — in which case G6-7's "exactly one record per read via every path" cannot be made true without a refactor |

### 10.2 Gates — what must be settled before which phase may start

**Blocking on a human (CQ register). These are not research items; no further investigation settles
them.**

| Gate | Blocks | Why it cannot wait |
|---|---|---|
| **CQ-1 — the auth / notification / threading trilemma** | **Phase 1, first.** Nothing else in Phase 1 may start | It determines the Google auth identity, which determines the DWD scope list, which is **frozen in a single super-admin session**. At most two of {human attribution, no Chat-native notification, threaded replies} can hold. Our recommendation: keep human attribution (DWD), accept Chat-native notifications for people running the native client, and **restate** locked decision #3 accordingly — Phase 0 §4.I explicitly authorises this outcome and says *"the human needs to decide, not you"* |
| CQ-5 (Google native notifications: A, B or C) | Phase 1 scope list; Phase 4 policy | follows directly from CQ-1 |
| CQ-10 (per-room retrieval upper bound) | Phase 1 schema, Phase 5 gate | sizes the candidate cap the gate enforces |
| CQ-12 (external users / guests in mirrored spaces) | Phase 1 schema | decides whether the identity mapping must handle non-employees, and `externalUserAllowed` spaces are a data-egress path |
| CQ-16 / CQ-F1 (per-message read receipts for DMs) | Phase 1 schema | the high-water mark vs a receipt table is a ~100× write-volume difference |
| CQ-19 / CQ-F2 (retention: how long, and does delete mean gone) | mechanism wires in **Phase 1**; policy is **Phase 6** | the settings fields must exist in Phase 1 even though the job is Phase 6 |
| CQ-11, CQ-13 | Phase 2 | oversized-message rule; the deliberately-lossy list |
| CQ-15 | Phase 3 | how presence renders for someone who lives in the native Chat client |
| CQ-2/3/4/6/7/8/9 | Phase 4 | the shipped notification defaults — already resolved in ADR §H, so these are **confirmations**, not blockers |
| CQ-14, CQ-17, CQ-18, CQ-23, CQ-24 | Phase 5 | chip-row change; the 40,000-token ceiling; pending-action exemption; vector storage; the Triton bridge secret |
| CQ-20, CQ-21, CQ-22, CQ-25 | **nothing** — independent | CQ-25 is worth doing **immediately**; see §12 |

**Blocking on a verification (VERIFY register).** Zero items are BLOCKING as of this ADR: the two
registered blockers plus the three the critic found between the notes have all been closed. What
remains are **residuals with phase owners**:

| Item | Must be settled before | Consequence if skipped |
|---|---|---|
| **R04-V08 / R04-C03** — does the Internal-app waiver still hold for the **restricted** scopes (`chat.messages`, `chat.delete`, `chat.import`)? | **Phase 1, before any other console work** | it is the one Phase-1 item with a **schedule-shaped** blast radius: a red result means multi-week Google verification before a single message can be sent, and it reshapes the whole plan |
| **P3-1** — the `Chat Room`-carries-DocPerm / everything-else-zero split | **Phase 1 cannot write a DocType JSON without it** | it is a deviation from both close notes and needs explicit sign-off; the bench-free DocType-JSON test in ADR §I.2.4 item 1 is the enforcement |
| **F-V1** — Frappe's `""`→`NULL` coercion for a `unique: 1` DocField | Phase 1, before relying on the inbound dedupe index | failure is production-only and presents as a random insert error |
| **`spaceThreadingState` settable at create?** (ADR §G.9.3) | **Phase 2, before any threading design is written** | if create-time-only and set wrong, **every space must be recreated** — in a live domain. ADR §G.9.3 calls it a five-minute live call, and it is treated as phase-blocking for Phase 2 |
| **The `website_404` cache trap** (residual of B1) | Phase 3 rollout note | loading `/chat/room/X` before the rule ships caches a 404 until Redis is flushed; a full deploy saves us, a hotfix without a restart does not |
| **P3-3** — do two scriptURLs at the same service-worker scope coexist or replace? | **Phase 4, before writing the worker** | decides whether a desk push worker can live at root scope at all, or needs a subpath **plus** a `Service-Worker-Allowed` header at nginx. Raised by no notes file |
| R02-V14 (`after_insert` email), R02-V17 (service-worker scope) | Phase 4 | the two deferred items the register assigns to Phase 4 |
| **P3-6** — the exact Chat 429 shape: does it carry `Retry-After`, and is a quota error distinguishable from a permission 403? | Phase 2 token bucket | mis-classifying a 403 as retryable burns quota and hides a scope error |
| **P3-7** — does Triton's existing Google OAuth scope set include any Chat scope? | Phase 5 | decides whether a Triton-side Chat post forces re-consent for every existing user. **Do not guess a scope string** |
| **F-V7 / F-V8 / F-V9** — FAC's `get_document`/`list_documents` call shape; `frappe.db.sql` reachability inside `run_python_code`; v16 `__global_search` filtering for a zero-DocPerm doctype | Phase 5 denylist branches | each decides whether the denylist needs an extra branch. **None changes the recommendation**, which closes all three surfaces regardless |
| **P3-2** (background-tab timer throttling vs the 55 s presence TTL), **P3-4** (iOS Safari PWA requirement), **P3-5** (Chat app avatar fetch vs a future WAF), **P3-8** (deployed `get_item_link` matches source) | Phase 4 / rollout comms / Phase 6 | none is structural; each converts a strong inference into a fact, and P3-2 fails in the **safe** direction (toward notifying) |
| **Is a Cloud Armor policy actually attached to the production backend service?** Terraform declares none (ADR §J.2), but Terraform not declaring something does not prove GCP does not have it — the GCLB backend timeout is the precedent. Settle read-only, copied verbatim from ADR §J.1, with: `gcloud compute backend-services describe production-glb-production-vm-backend --global --project=erpnext-465317 --format="yaml(name,timeoutSec,securityPolicy,edgeSecurityPolicy,sessionAffinity,protocol,portName,connectionDraining,healthChecks)"` — non-empty `securityPolicy` / `edgeSecurityPolicy` means a WAF **does** exist and the OWASP false-positive risk is live today, not future | **Phase 6, before the manual Cloud Armor payload check** (§8's manual row) — and worth running earlier, since one command also answers the `timeoutSec` and `sessionAffinity` questions | Phase 6 either authors rules against a load balancer that already has a policy it did not know about, or records "no WAF" as a fact it never checked. Both make the §9-V payload check meaningless |

**The shape of the residual risk, stated once because it is the most actionable thing in this
section:** Phase 1 carries **37 of the 66 deferred items, and 16 of those are Google Cloud /
Workspace runbook gates**. The residual risk in this project is concentrated in **one Google
Workspace admin session and one GCP console session**, not spread across the codebase. Six further
items — hash-name length, the `track_changes` default, `FrappeTestCase` vs `IntegrationTestCase`,
`has_value_changed`, redis-wrapper site-key prefixing, and `get_url_to_form` — are each a single
`inspect`/import call and should be settled as **one bench-console batch** in Phase 1, because
together they de-risk three later phases.

**One scaling precondition, owned by nobody and therefore recorded here.** Session affinity is
`NONE` and both load balancers have exactly one backend, so it does not matter today. It stops being
true the moment the VM becomes a MIG with more than one instance: socket.io then needs either
`CLIENT_IP` affinity or a Redis socket.io adapter, and **Frappe v16 passes no `adapter` option at
all**. Presence (Redis-keyed) and read state (DB-backed) are already multi-instance-safe; realtime is
not. This belongs on a "before you scale horizontally" list, not in any of the six phases.

---

## 11. Carried to Phase 6 deliberately — do not pull these forward

The master prompt is explicit that Phases 1, 2 and 5 legitimately defer work to Phase 6 and that this
work **must not be absorbed into an earlier phase to "finish it"**. Each item below is named with the
phase that is tempted to build it and the phase that actually owns it.

| Deferred item | Tempting phase | **Owner** | Why it must wait |
|---|---|---|---|
| **Import-mode back-fill** of historical Chat content | 2 | **6 §4.L — and the default answer is *no*** | Import mode is **one-time**, limited to a **90-day window**, `SPACE` and `GROUP_CHAT` only (**no DMs**), and only into spaces the app itself created. The `chat.import` scope was **deliberately not granted** in the runbook. It is **not reversible** — you cannot un-import. Phase 6 presents it as a question with the cost and the irreversibility stated, recommends against, and builds it only if explicitly commissioned, as its own mini-project with its own dry run, space-by-space approval, and a rollback statement that will read "there is none" |
| **The admin conversation viewer / e-discovery surface** | 3 | **6 §4.B** | It is a **new, separate** surface, not a mode of the SPA. Building it in Phase 3 would put an oversight read path inside the code that serves ordinary members, which is exactly the coupling the audit requirement exists to prevent |
| **The non-participant read audit WRITE** | 1, 3, 5 | **6 §4.D** | Phase 1 leaves **one clearly-marked hook point** that the read path calls — deliberately *not* scattered audit calls. Phase 5 writes audit rows for Triton retrieval only. Phase 6 makes G6-7 true: **exactly one** record per non-participant read via **every** path (viewer, oversight search, export, download, `frappe.get_list`, report view, `/api/resource`) and **zero** for a participant (I9). That "every path" property cannot be established by a phase that has not yet inventoried the paths |
| **Retention and purge** | 1 | **6 §4.F** | Phase 1 ships the `Chat Settings` fields and their `validate()`; Phase 6 builds the job, the dry run, the survives-a-purge table and the digest invalidation, and leaves `retention_mode = Disabled`. Building the destroyer before the audit vault is immutable (G6-11/G6-12) inverts the only safe ordering |
| **Cloud Armor rule authoring** | 1, 2 | **6 §4.G.8** | **As declared in Terraform there is no Cloud Armor policy in front of ERPNext today** (ADR §J.2), and Terraform not declaring it does not prove GCP does not have it — settle with the `describe` in §10.2. On the Terraform reading this is a precondition to author, not a fix to verify, and its rules must be written against the *finished* ingest and relay paths — a rule written in Phase 1 against a path that later moves is worse than no rule, because it reads as protection |
| **Drift reconciliation** between ERPNext and Chat | 2 | **6 §4.I** | Phase 2 owns *convergence*; Phase 6 owns *detection and bounded repair of divergence*, with a settling window, three caps and `drift_repair_enabled` **off**. An auto-repairer built alongside the engine it repairs shares the engine's bugs |
| **The employee "who read my messages" view** | 3 | **6 §4.D.4** | Behind `employee_access_transparency`, **defaulted to Disabled**, with five policy questions written up. It is a policy decision presented as a feature, and it is the human's |
| **Export bundles and manifest hashing** | 3 | **6 §4.C** | The artefact that leaves the building. Its contents are a legal question, not a serialization convenience |
| **The audit `chain_hash` tamper-evidence chain** | 5 | **6 §4.D.3** | Must be built **before** any new writer exists, which in Phase 6's ordering means before the viewer and before export |
| **Rollout: pilot flag, server-side gating, layer-by-layer rollback, the Chat-dark degradation drill, the pilot checklist, the operations runbook** | 3, 4 | **6 §4.J, §4.K** | Note the constraint that shapes it: **Chat apps must be enabled at the top organizational unit**, so a pilot-OU rollout is **impossible**; the actual rollout control is the Chat app's visibility setting plus `restrict_to_whitelist` — which is why Phase 1 ships the whitelist child table and Phase 6 uses it |
| **The MCP manual acceptance step** (R03 T-15) | 5 | **6** | Cannot be written bench-free: open an MCP session as a real System Manager, issue `run_database_query` with ``SELECT COUNT(*) FROM `tabChat Message` ``, expect the refusal envelope |
| **`default_log_clearing_doctypes = {"Notification Log": 30}`** | 4 | **neither — its own PR (CQ-21)** | A **pre-existing, unrelated** 13-month leak (7,165 rows / 33.2 MB, registered for retention in neither frappe's nor ERPNext's hooks). Offered as a standalone one-line change so it does not hide inside a feature branch. Same for the misleading `push_relay_server_url` config |

---

## 12. The first commit of Phase 1 — four changes that de-risk everything after them

Each is small, independent, reviewable on its own, and removes a risk that otherwise compounds. Each
carries the full version + CHANGELOG ritual, because this repo has no exemption for small changes.
They can land **before** Phase 1's schema work — and CQ-25 asks the human for permission to land the
first of them before Phase 1 starts at all.

**C1 — Wire `test_triton_personas.py` into CI. Two lines.**

```yaml
      - name: Triton persona proxy + SSE relay (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_triton_personas.py -q
```

`grep -c "test_triton_personas" .github/workflows/ci.yml` returns **0**. This is the **only**
automated coverage of `triton_chat.py`, and it has never run. Appendix A §14's entire "must survive"
table — every streaming row, the persona payload contract, the per-user persona cache key, the
path-traversal guard on `_custom_persona_path` — is currently defended by a file that has never
executed. Phase 3 modifies the widget and Phase 5 refactors the SSE relay; both do so with no
regression net until this lands. *Cost:* one tiny PR plus whatever the suite turns out to be failing
on today — **which is exactly the information we want before Phase 5 touches the relay**, not after.
This is **CQ-25**, and the recommendation is yes.

**C2 — Add `"Chat"` to `utils/triton_sync.py`'s `excluded_modules` (`:30-33`), with its annotation.
One line.**

Invariant **CHAT-EXCL-1**. Without it, every chat DocType write announces itself to Triton's index
webhook: a self-inflicted DoS on the webhook queue at chat volumes, *and* an unreviewed egress of
employee-private message content to an external service. It fails **silently** — the writes succeed
and nothing looks wrong. The file's existing entries already document the same reasoning for
`Telephony` (echoing ingested Call Logs back) and `AI Governance` (high-volume log doctypes), so the
annotation writes itself. Landing it **before** the first chat DocType means the window in which it
could fire never opens.

**C3 — `scripts/check_no_committed_secrets.py` plus its blocking CI step, run against full history.**

Phase 1 §4.4's hard rule is zero credentials in git — **in history as well as at HEAD**. Writing the
scanner before the auth module means the auth module is written under a gate rather than audited
after. Its own failure mode is "passes because the pattern is wrong", so the commit must include
planting a fake key, watching the step fire, and removing it. Phase 4 later extends it with a VAPID
private-key pattern, which is much easier against a scanner that already exists and has a test.

**C4 — `erpnext_enhancements/tests/test_chat_guardrails.py`: the fence, before the field.**

A bench-free unittest suite (shape U) over `erpnext_enhancements/chat/**`, which is **empty at this
point**, so every assertion passes vacuously — and fails the first time the fence is crossed:

1. every chat DocType JSON has `permissions == []`, except `Chat Room` which asserts the inverse
   plus both hooks registered (the assertion that catches the most likely regression: somebody adding
   a System Manager row so they can look at a message in the desk);
2. every `frappe.enqueue` in the package passes `enqueue_after_commit=True` — **the single most
   consequential one-keyword omission in the design**, producing intermittent "message not found"
   failures that reproduce only under load;
3. every `publish_realtime` passes `room=` / `user=` / `doctype=`+`docname=` — because
   `publish_realtime`'s final fallback is `get_site_room()`, a **site-wide broadcast** of message
   content to every connected session;
4. no chat event is named `list_update` or `docinfo_update`, because those two names **overwrite an
   explicitly passed `room=`**;
5. `chat.googleapis.com` appears in exactly one module, and no `doc_events`-registered chat function
   reaches the transport.

The technique is already proven in this repo: `tests/test_contract_esign.py:526-534` asserts "a guest
endpoint is missing its rate limiter" by regex over source, and `tests/test_doctype_modules.py` walks
the filesystem the same way. A guard written **after** the code it guards is a guard that gets
argued with; written before, it is just the shape of the package.

*A fifth, if there is appetite:* land CQ-21's two standalone repo fixes — the `Notification Log`
retention registration and the misleading push-relay config — as their own PRs now, so they are
visibly **not** part of the chat feature and do not confuse Phase 4's Web Push work.

---

## 13. Contradictions and divergences recorded rather than resolved

Per `DECISIONS.md` D0/D8, a contradiction between this appendix's evidence and a binding decision, a
phase prompt or a sibling note is written down, not silently reconciled.

**X-1 — Phase 1 §4.2 names a `Chat Audit Log` DocType; the ADR does not create one.** ADR §F.3
records the rejection explicitly: privileged reads go to `Chat Retrieval Audit` (Phase 5), deletions
are captured on the message row itself (Phase 1 fields `is_deleted`, `deleted_at`, `deleted_by`,
`deletion_source` — the ADR §F.6.2 name, not `deleted_origin` — plus the body moved off the live
row), and MCP refusals already write `AI Action
Log` through the existing `insert_action_log` in `assistant_tools/_gate.py:341-396`. Phase 1's own
prompt says *"the approved ADR is authoritative where it differs"*, so **Appendix B follows the
ADR** — but Phase 1 must **report this divergence at its checkpoint**, because a reviewer checking
the prompt's DocType table against the shipped schema will otherwise score a correct answer as a
miss.

**X-2 — the ADR's DocType inventory (§F.3) has no subscription-health DocType, while ADR §G.5.2–
§G.5.3 require per-subscription state.** §G.5.2's renewal scheduler iterates `_tracked_subscriptions()`
reading `sub.state` and `sub.expire_time`, and §G.5.3 defines three alarms over
`expireTime` / `SUSPENDED` / "no `ACTIVE` subscription for a roster member" — none of which is
computable without a stored row per subscription. Phase 1 §4.2 names `Chat Event Subscription`
(schema only) and Phase 2 §4.J requires "a subscription health DocType, one row per subscription
(~50 rows)". **Appendix B resolves this by creating `Chat Event Subscription` in Phase 1 as
schema-only**, and records that §F.3's fourteen-row inventory is therefore incomplete by one. This is
reported rather than treated as an editorial slip because a subscription whose expiry is not tracked
is permanent, silent, total loss of inbound sync (§9-C).

**X-3 — three later-phase DocTypes are named by the phase prompts and absent from ADR §F.3:**
`Chat Push Subscription` (Phase 4 — though ADR §J.4 independently requires "a server-side
`Push Subscription` registry" with the same fields), `Triton Invocation Log` (Phase 5 §3.18), and
Phase 6's `Chat Export Request`, `Chat Ops Alert` and `Chat Drift Report`. §F.3 is the **core data
model**; these are operational tables belonging to their phases. Recorded so nobody reads §F.3 as a
closed list and deletes them.

**X-4 — the phase-numbering disagreement itself.** Phase 0 §4.L's proposed decomposition puts
notifications at 2 and the frontend at 6; the master map and the six written prompts put SPA at 3,
notifications at 4, Triton at 5 and governance at 6. Resolved in §1 above in favour of the master
map, per `DECISIONS.md` D7. Recorded here as well because it is the divergence most likely to be
rediscovered by a session that reads only Phase 0.

**X-5 — `notes_infra.md` OQ-3 recommends scoping Web Push as its own phase with its own
checkpoint.** Appendix B does **not** do this, because the master map puts notifications at Phase 4
and the prompt file exists. The recommendation is honoured in spirit instead: Phase 4 is rated the
largest single unbuilt component in the plan, its crypto accounts for three of the forty-three
High-rated rows (`webpush/vapid.py`, `webpush/encrypt.py`, `test_chat_webpush_crypto.py`) and for one
of the twenty-two register entries (§9-P), and its checkpoint requires a live demonstration on two
browsers and a phone.

**X-6 — `CLAUDE.md`, `README.md:147` and the Phase 0 prompt all state the fixture counts as
"~425 Custom Fields and ~349 Property Setters".** The measured figures are **513** and **409**. This
appendix uses the measured figures. Relatedly, `permission_query_conditions` and `has_permission` are
at exact parity — **10 and 10** — so the "asymmetry" some audit notes describe is an artifact; the
doctrine worth lifting is that **every query condition has a `has_permission` twin**, which every
chat DocType row above honours.

**X-7 — the "sources dropdown" criterion.** Restated rather than silently satisfied: the ERPNext
widget has **no dropdown**; it has an always-visible, non-collapsible chip row
(`renderSources`, `triton_widget.js:1085-1106`). The dropdown described by the prompts is in the
*Triton web app* (`triton:frontend/src/views/ChatView.vue:363-381`). The all-retrieved-vs-only-cited
question has a **hybrid, path-dependent** answer. Phase 5's row above preserves the chip row and adds
inline links; **CQ-14** asks the human whether the chip row may change at all.

**X-8 — nine fields named in this appendix's schema rows that the ADR's field inventory does not
carry.** `DECISIONS.md` **D5** exists so that there is exactly one set of names in the project, and
the ADR's §F.4–§F.9 tables are that inventory. Appendix B's §3.2 rows name nine fields absent from
them: `Chat Room.membership_authority`; `Chat Room Member.left_seq`; `Chat Message.sender_email` and
`.replied_message_details` (JSON); `Chat Attachment.content_name`, `.content_hash` and `.data_ref`;
`Chat Relay Job.next_attempt_at` and `.dead_reason`. **They are recorded here rather than deleted,
because several are legitimate additions the file plan needs and the ADR simply did not size** —
`membership_authority` is what §4's `membership.py` row reads to decide whether a Google-side
membership change is accepted or reverted, and `left_seq` bounds a departed member's readable range at
a sequence position, which ADR §F.5's `left_at` can only approximate in a timestamp the §9-L timezone
finding says not to compare in SQL. Others look like an ADR field under a second name:
`content_name` against `Chat Attachment.file_name` (whose ADR note is *"Google `Attachment.contentName`"*),
`data_ref` against `gchat_attachment_data_ref`, `next_attempt_at` against `Chat Relay Job.available_at`,
and `dead_reason` against `last_error` + `status = Dead`. (`content_hash` is not a divergence on
`Chat Context Chunk`, where ADR §F.11.1 declares it; it is one on `Chat Attachment`, where the ADR
declares no hash column at all.) **Phase 1 must reconcile each of the nine against the ADR table
before writing a DocType JSON** — adopt the ADR's name wherever one already covers the role, and
carry an ADR addendum row for each field that genuinely has no counterpart. Guessing in either
direction is exactly how the second schema D5 was written to prevent gets created.

---

## 14. `VERIFY:` items this appendix adds

Everything below is new in Appendix B or sharpened by writing the file plan. Items already carried in
ADR §K.1 or `notes_register_reconciled.md` are not repeated.

> `VERIFY: the exact v16 signatures of frappe.db.add_index and frappe.db.add_unique, including whether add_unique accepts a constraint_name and whether either can be called twice safely` — settle with `inspect.signature` in a bench console, then by running the patch twice on a scratch site. **Blocks:** `patches/add_chat_indexes.py`, and therefore every uniqueness and performance claim in ADR §F.19. This is exactly the kind of helper whose argument shape drifts between versions.

> `VERIFY: whether Frappe's www/ hyphen rule applies to a served asset (kiosk-sw.js, wall-sw.js) as well as to a controller module` — the guard `scripts/check_www_controllers.py` exists and the two hyphenated workers are shipped and working, which suggests the rule binds only importable controllers. Settle by reading the guard and Frappe's `www` resolution. **Blocks:** whether Phase 4's service worker may be named `chat-sw.js` or must be `chat_sw.js`. Cheap, and getting it wrong the other way is harmless.

> `VERIFY: whether a Vitest step can run in the existing CI runner image without a package-install step the deploy pipeline does not have` — the repo's two JS guards are plain `node scripts/*.js` with **no** npm install, deliberately. **Blocks:** whether Phase 3's JS harness is Vitest (needs `npm ci`) or must be plain `node` assertions in the existing style. This decides a CI-shape precedent, not a test's content.

> ~~`VERIFY: whether erpnext_enhancements registers any doc_events["*"] handler that would fire on chat DocTypes`~~ — **CLOSED. Settled by reading the whole `doc_events` block this session.** There is exactly **one** wildcard handler, and it is the last entry in the block: `"*": {"after_save": "erpnext_enhancements.utils.triton_sync.global_triton_sync"}` at **`hooks.py:567-569`** — `"*"` appears as a `doc_events` key nowhere else in the file. That single handler is precisely why the Chat module must be added to `utils/triton_sync.py`'s `excluded_modules` (`:30-33`), which is invariant **CHAT-EXCL-1** in §3.1 and change **C2** in §12. **No separate ignore list is needed**, and no chat insert runs unrelated business logic once C2 lands. **Residual, narrow and the only part still open:** the guard `global_triton_sync` applies is **module-scoped**, not doctype-scoped — it returns early on `doctype_meta.module in excluded_modules`, with a short hard-coded `excluded_doctypes` list beside it (`triton_sync.py:41`) for the one case where module scope was too coarse. So a chat DocType created **outside** the `Chat` module would not be covered by CHAT-EXCL-1. **Re-run this check whenever a chat DocType lands anywhere but `erpnext_enhancements/chat/`**, and prefer adding it to the module rather than to `excluded_doctypes`. (ADR carries the same question as **F-V6**; this closes it for the file plan.)

> `VERIFY: that no fixture in fixtures/*.json will pick up chat Custom Fields by a broad filter` — the fixtures export uses filters, and a filter written as "all Custom Fields on these doctypes" could capture chat fields and start managing them. **Blocks:** nothing structural; prevents a surprise in the first `bench migrate` after Phase 1, and prevents the two-step deletion trap (removing a fixture record only stops managing it; deletion needs a one-shot patch calling `frappe.delete_doc`).

---

*End of Appendix B. Appendix A is the behaviour inventory that Phase 3 must not break; the record
itself is `decisions/adr/0009-erpnext-google-chat-triton.md`.*
