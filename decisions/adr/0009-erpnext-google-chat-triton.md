# 0009. Build employee chat inside `erpnext_enhancements`, mirrored to Google Chat, with Triton reachable by `@triton`

- **Status:** Accepted
- **Date:** 2026-08-07

> **CQ-1 is resolved. Nikolas approved this record on 2026-08-08 and chose to keep human
> attribution.** The relay authors messages as the real person via domain-wide delegation; Google
> Chat's own notification therefore fires for anyone running the native Chat client, and that is
> accepted. **Locked decision #3 is restated for the whole project as:** *"Exactly two
> notifications are fired by ERPNext — the bell/Notification Log entry and Web Push — under the
> presence rules in [§H](#h-notification-decision-table). Users running the native Google Chat
> client additionally receive Chat's own notification. That third ping is documented, expected, and
> not a defect."* Every phase inherits this wording; a later session finding "exactly two
> notifications" in a phase prompt should read it as the restatement above.
>
> Consequences that follow immediately, and that later phases must not re-open:
> `NOTIFICATION_TYPE_SILENT` is **not used** for the coworker relay; the relay runs under user auth
> (DWD), so outbound attachments and `messageReplyOption` threading are both available; the Chat app
> identity is used **only** for Triton's replies, which are correctly bot-badged. **CQ-5 falls out of
> this and needs no separate answer.**

## Where this record lives

**This record is at `decisions/adr/0009-…`, not at `docs/adr/0001-…`, and the deviation is
deliberate.** The Phase 0 prompt asks for `docs/adr/0001-erpnext-google-chat-triton.md`. This
repository already has an ADR convention — established by its own
[`0001-record-architecture-decisions.md`](0001-record-architecture-decisions.md) and documented in
[`README.md`](README.md): records live at `decisions/adr/NNNN-slug.md`, are numbered sequentially,
are **immutable once accepted**, and are indexed in that README's table. Number 0001 is taken, and
writing to `docs/adr/` would start a rival ADR namespace in a repo that already has one.

| The prompt's path | This record | Why |
|---|---|---|
| `docs/adr/0001-erpnext-google-chat-triton.md` | [`decisions/adr/0009-erpnext-google-chat-triton.md`](0009-erpnext-google-chat-triton.md) | next free number in the established register |
| `docs/adr/0001-appendix-a-widget-behavior-inventory.md` | [`decisions/adr/0009-appendix-a-widget-behavior-inventory.md`](0009-appendix-a-widget-behavior-inventory.md) | keeps the appendix beside its record |
| `docs/adr/0001-appendix-b-implementation-plan.md` | [`decisions/adr/0009-appendix-b-implementation-plan.md`](0009-appendix-b-implementation-plan.md) | same |
| — | one new row in [`decisions/adr/README.md`](README.md) §Index | the README's own stated ritual |

So a reviewer checking Phase 0's literal acceptance criterion — "does `docs/adr/0001-…` exist?" —
will find it does not, and this note is why. The content is complete; the address obeys the house
convention. (`DECISIONS.md` D0.)

Two further placement decisions follow from the same reasoning:

- **The open questions for the human stay inside this record**, as the `CQ-n` register in
  [§K.2](#k2-the-cq-register--questions-only-nikolas-can-answer). They are deliberately *not*
  written into [`decisions/OPEN-DECISIONS.md`](../OPEN-DECISIONS.md), whose `OD-n` numbering is
  scoped to the ERPNext migration and is load-bearing for work items. Lifting any `CQ` into that
  register is Nikolas's option, not ours.
- **Status is Proposed.** The checkpoint asks Nikolas to answer CQ-1 and move this to Accepted. An
  ADR is immutable once accepted, so revisiting any decision below means a new record that
  supersedes this one.

## Context

Sapphire Fountains wants one employee chat system, and it has two places a conversation can live.
The work lives in ERPNext — 187 custom DocTypes across 28 modules, projects, tasks, service visits
(§A.1, §A.3) — and the people live in Google Chat, because the company runs Google Workspace and
staff already have the app on their phones. Picking one and abandoning the other loses either the
context or the audience. That tension is what forces this record.

Thirteen product decisions were locked before Phase 0 opened, and they are not re-argued here. The
ones that bind the engineering are: **ERPNext is the source of truth** (#1); the mirror is **fully
bidirectional** (#2); **exactly two notifications per notifiable event and never an email** (#3);
Triton answers **`@triton`** in a room (#5); Triton's retrieval is **gated by room membership** (#6);
the **citations panel is preserved exactly** (#7); the frontend **extends the existing floating
widget** rather than adding a second one (#8); attachments and read receipts are in scope (#9);
**per-document rooms** exist (#10); Google auth was left open (#11); and non-participant reads are
**audited** (#12).

Phase 0 was asked to settle, before a line of code, the six choices that are expensive to reverse:
where the app lives, whether to adopt Raven, which Google identity posts a message, where embeddings
are stored, what the fields are called, and how per-room ordering works. Seven audit agents plus a
completeness critic spent the session establishing the evidence — reading both repositories,
measuring the production host, and verifying Google's documentation live on 2026-08-07.

Three things that evidence found are the reason this record is long rather than short:

1. **Two of the six choices were decided by measurement, not preference.** Production MariaDB is
   `10.11.18-MariaDB-0+deb12u1` from a live `SELECT VERSION()` — no `VECTOR` type, no `VEC_*`
   functions (§I.5) — which closes the "on 11.8 the decision may flip" branch against the flip. And
   there is **no Raven line known-good on Frappe v16**: its desk bundle disables itself on v16 in a
   deliberate commit, and its `develop` branch imports a realtime API that v16 does not have (§C.2).
2. **The audit found a contradiction between the locked decisions themselves that no research
   document had assembled**, although both halves sit on the same Google page: the mechanism that
   suppresses Chat's own notification requires app authentication, and app authentication makes the
   Chat app the sender with an `App` badge. *Silent* and *authored by the real human* are mutually
   exclusive. Decision #2 and decision #3 cannot both hold as written. That is
   [§E.3](#e3-the-trilemma--the-finding-that-changes-the-product), it is CQ-1, and it is the reason
   this record leads with Google auth rather than burying it.
3. **Several of this repository's own documented numbers are wrong**, and repeating them would
   propagate the error: fixtures hold **513** Custom Fields and **409** Property Setters, not
   "~425 / ~349"; `permission_query_conditions` and `has_permission` are at exact **10-and-10**
   parity, not asymmetric; and the production roster is **~20 enabled users**, not the ~50 the
   premise assumed (§A.0).

Everything below is evidenced. Repository facts carry `path:line`. Live-system facts say what they
were verified against and when. Anything not established is carried explicitly as
`VERIFY: <claim> — <how to settle> — <what it blocks>` rather than written as confident prose.

## Decision

Stated compactly here; each headline has its full argument in the section named.

1. **Chat ships as a new self-contained `chat/` module inside `erpnext_enhancements`, not as a
   sibling Frappe app.** Decision #8 requires *extending* a desk-global widget that lives in this
   app's esbuild bundle, and a sibling app could only fork it or reach into another app's bundle at
   runtime — the exact thing [ADR 0008](0008-global-assets-ship-as-bundles.md) exists to prevent.
   The retrieval gate that decision #6 needs must also sit next to `assistant_tools/_gate.py`, which
   only this app may import. Full argument, the rejected sibling app, and the registration checklist:
   [§D](#d-app-placement). (`DECISIONS.md` D1.)
2. **We do not adopt Raven and we do not fork it; we reimplement, lifting its proven schema
   decisions with attribution.** There is no Raven line known-good on v16, and even a v16-ready
   Raven owns a route and a page, which fights decision #8. What we lift and why each is proven:
   [§C](#c-prior-art--the-raven-decision). (`DECISIONS.md` D2.)
3. **Google auth is a hybrid: domain-wide delegation impersonating the authoring human for the
   coworker relay, edits, deletes and attachment uploads; a separately registered Chat app on
   `chat.bot` for Triton's replies.** Keyless throughout — two service accounts with
   `roles/iam.serviceAccountTokenCreator` between them and assertions signed by IAM Credentials
   `signJwt`, so no private key ever lands on disk. The trade-off table, the keyless finding and the
   Workspace admin runbook: [§E](#e-google-auth). (`DECISIONS.md` D3.)
   **This is also the one decision that carries an escalation to Nikolas** — see the trilemma at
   [§E.3](#e3-the-trilemma--the-finding-that-changes-the-product) and CQ-1.
4. **Embeddings are stored on the chunk DocType and scored with in-process `numpy` cosine over the
   permission-filtered candidate set, behind a two-method `VectorBackend` adapter** so the backend is
   a one-file swap. Production MariaDB has no vector support and `numpy 2.5.1` is already importable
   in the prod bench, so this adds no dependency on a host that cannot `pip install`
   ([ADR 0004](0004-no-vendor-sdks.md)). Four numeric revisit triggers, and the Vertex AI RAG
   alternative we are not taking: [§I.5](#i5-vector-storage--decisionsmd-d4-its-measured-reason-and-the-alternative-we-are-not-taking).
   The storage *type* is refined from D4's "BLOB" because Frappe has no BLOB fieldtype —
   [§F.11.2](#f112-the-embedding-column--a-contradiction-with-decisionsmd-d4-reported).
   (`DECISIONS.md` D4.)
5. **There is exactly one set of field names in this project: the master prompt's §5 canon.**
   `gchat_message_name`, `gchat_thread_name`, `client_message_id`, `text`, `sender`, `sync_state`,
   `sync_origin` on `Chat Message`; `gchat_space_name` on `Chat Room`. Later phase prompts use other
   words for the same fields; the normative alias map is
   [§F.1](#f1-the-field-name-canon-d5-and-the-alias-map-every-later-phase-must-read-through), and a
   session reading Phase 2 in isolation must read through it rather than invent a second schema.
   (`DECISIONS.md` D5.)
6. **`Chat Message.seq` is an explicit `Int`, assigned inside the insert transaction, unique per
   `(room, seq)` — never a raw timestamp — and every cache, digest and chunk watermark is the
   three-value tuple `(max(seq), count(*), max(modified))`, never `seq` alone.** Edits and deletes do
   not advance `seq`, so a watermark that tracks only `seq` will serve cached context containing a
   message the user just deleted. Stated as named invariants with their tests:
   [§F.16](#f16-the-monotonic-per-room-sequence--a-named-invariant-and-its-test). (`DECISIONS.md` D6.)

Two decisions that are not on that list but shape as much code:

- **Chat content DocTypes carry no DocPerm**, with `Chat Room` the single, argued exception, because
  the only mechanism that can keep chat out of the AI assistant's generic `run_database_query` is a
  denylist in `assistant_tools/_gate.py` — raw SQL consults no Frappe permission layer at all.
  [§F.18](#f18-the-permission-model-followed-through-to-its-consequences),
  [§I.2](#i2-i5--the-denylist-on-the-generic-mcp-tools-by-the-mechanism-that-actually-closes-it),
  [§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved).
- **Delivery is guaranteed by a transactional outbox and its sweeper, never by the job queue**,
  because every production deploy `FLUSHDB`s the queue Redis and destroys pending jobs
  ([§J.7](#j7-the-deploy-flushdbs-both-redis-instances-and-restarts-every-process),
  [§G.1](#g1-outbound--the-transactional-outbox)).

## How to read this record

This record is long because it is the input to six downstream phases and a hedge here becomes six
ambiguities. It has two companion documents:

- **[`0009-appendix-a-widget-behavior-inventory.md`](0009-appendix-a-widget-behavior-inventory.md)** —
  the behaviour of the existing floating Triton widget as of 2026-08-07, file and line, written down
  before anything touches it. **It is a Phase 3 gate**: `PHASE_3_chat_spa` is instructed to STOP if
  that file does not exist, and to re-check its §14 table row by row as the regression suite. A
  divergence Phase 3 finds between that document and the running widget is a regression to report,
  not a spec to update.
- **[`0009-appendix-b-implementation-plan.md`](0009-appendix-b-implementation-plan.md)** — the
  phase-by-phase implementation plan, following the **master phase map** (1 foundations & auth,
  2 sync engine, 3 SPA, 4 notifications, 5 Triton, 6 governance), not Phase 0 §4.L's alternative
  proposal, because the six downstream prompt files already exist and are written to the master map
  (`DECISIONS.md` D7).

The body is eleven sections in three groups, exactly as they were drafted:

| Sections | What |
|---|---|
| [A](#a-repository-audit--erpnext_enhancements), [B](#b-repository-audit--triton), [C](#c-prior-art--the-raven-decision), [D](#d-app-placement), [E](#e-google-auth) | the evidence base — both repositories audited, the Raven decision, app placement, and Google auth including the trilemma |
| [F](#f-data-model), [G](#g-sync-protocol) | the data model and the bidirectional sync protocol |
| [H](#h-notification-decision-table), [I](#i-triton-integration-and-contextcaching), [J](#j-networking-and-infrastructure-risks), [K](#k-open-items-and-open-questions) | notifications, Triton integration and caching, infrastructure risk, and the two open registers |

**Two registers, and conflating them is how a checkpoint becomes unreadable.**
[§K.1](#k1-the-verify-register) is `VERIFY` — facts nobody has established yet, each with a
settlement method, owned by a phase. [§K.2](#k2-the-cq-register--questions-only-nikolas-can-answer)
is `CQ` — judgements only Nikolas can make, owned by the human. **Phase 1 should not start until
CQ-1 has an answer.**

## Resolved seams between the three drafts

Sections A–E, F–G and H–K were drafted in parallel and reconciled here. Where they disagreed, the
resolution is `DECISIONS.md` first, then the better-cited evidence. Each is also flagged inline at
the point of disagreement with a **Seam note (assembly)** blockquote, so a reader arriving mid-record
is not misled.

| # | The disagreement | Resolution |
|---|---|---|
| **S1** | **Where realtime message content is published.** [§F.18.3](#f183-the-realtime-consequence--and-a-deviation-from-a-sibling-note-declared) puts content on per-user rooms (`user:{email}`) and has chat never call `doc_subscribe`; [§H.4.1](#h41-the-channel-split--confirmed-not-provisional) / [§H.4.4](#h44-the-events-and-the-realtime-hygiene-rules-that-bind-them) put content on the room's doc room (`doc:Chat Room/<room>`) with counters on `user:<email>`. | **`DECISIONS.md` D2 wins**: it explicitly lifts *"realtime scoped to the channel doc room rather than per-user fan-out"* from Raven. The doc-room split and the `Chat Room`-only DocPerm that makes it joinable ([§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved)) are **the design**. §F.18.3's per-user fan-out is preserved verbatim as the **recorded fallback** and as the argument Phase 1 must answer, because it identifies two real residuals the doc-room design carries: cooperative-only eviction, and silent join refusal. Carried as **P3-1**, which Phase 1 must sign off before writing a DocType JSON. |
| **S2** | **DocPerm on `Chat Room`.** §F.18.4 lists `Chat Room` in the zero-DocPerm set; §H.4.2 gives it a minimal `read` DocPerm plus the `permission_query_conditions` + `has_permission` pair. | Same root cause as S1, same resolution: **§H.4.2's split governs.** Every other chat DocType, including `Chat Message`, keeps zero DocPerm. `Chat Room` stays on the `_gate.py` denylist regardless, because `run_database_query` consults no permission layer. |
| **S3** | **Presence constants.** [§F.14.2](#f142-key-shapes-and-ttls) uses the house 30 s heartbeat / 75 s TTL from `live_form_sync.js:71-72`; [§H.3.1](#h31-the-constants--and-why-chat-does-not-reuse-30-s--75-s) deliberately chooses 20 s / 55 s and argues against reusing 30/75. | `DECISIONS.md` is silent. **§H.3.1's constants govern** (`CHAT_PRESENCE_HEARTBEAT = 20 s`, `CHAT_PRESENCE_TTL = 55 s`, `BLUR_GRACE = 120 s`) because §H owns the presence→suppression contract that decision #3 depends on and it considered and rejected 30/75 explicitly. **§F.14.2's key *shape* governs** — a Redis **hash** keyed on the user with one field per session, not a key per session — because that argument is measured and §H.3.1 does not address it: per-field TTLs need Redis 7.4 and production is `redis_version 7.0.15`, and a key-per-session layout needs a `SCAN` to enumerate a user's tabs. |
| **S4** | **Embedding storage type.** `DECISIONS.md` D4 and [§I.5](#i5-vector-storage--decisionsmd-d4-its-measured-reason-and-the-alternative-we-are-not-taking) both say "BLOB"; [§F.11.2](#f112-the-embedding-column--a-contradiction-with-decisionsmd-d4-reported) shows **Frappe has no BLOB fieldtype** and uses `Long Text` holding base64 of raw `float32` bytes. | **§F.11.2 governs, as a refinement rather than a reversal** — it satisfies D4's intent (in-process numpy cosine, no new dependency) and costs a measured 33% storage overhead. A raw `longblob` added by patch is recorded as the optimisation, gated on D4's own numeric revisit triggers. Reported per `DECISIONS.md` D8. |
| **S5** | **The realtime hygiene rules are stated twice** — as named `CHAT-RT-1/2/3` in §F.18.3 and as unnamed rules (a)/(b)/(c) in §H.4.4. | Not a conflict: identical rules, different supporting citations. **`CHAT-RT-1`, `CHAT-RT-2` and `CHAT-RT-3` are the canonical names**; §H.4.4 is the same three rules with the additional `task_id` and site-room evidence. |
| **S6** | **Size of the hand-rolled DWD assertion builder.** `DECISIONS.md` D3 says "~40 lines"; [§E.4.2](#e42-sizing-it-as-its-own-module-with-its-own-unit-test) sizes it at ~50 with a different composition (no cryptography — IAM Credentials signs — plus a larger token cache). | **A refinement, not a reversal.** §E.4.2's sizing governs; reported per `DECISIONS.md` D8. |
| **S7** | **Whether the Raven-on-v16 experiment is phase-blocking.** `notes_gap_report.md` §E ranks it blocking; `DECISIONS.md` D2 records it non-blocking. | **D2 is binding** ([§C.6](#c6-the-raven-on-v16-verify-recorded-as-non-blocking)). It is carried as **CQ-22**, a question for the human, not a gate. |
| **S8** | **"~50 users."** The premise carried through `DECISIONS.md` D1 and the research; the production site measures **23 enabled Users, 20 enabled System Users, 18 active in 30 days, 15 active Employees with linked Users**. | No decision changes. Every "~50" in this record is an **upper bound** and is labelled as one; three numbers must be sized on ~20 — the per-coworker Workspace Events subscription count, the per-message notification fan-out, and the retrieval-corpus volume model (§A.0, §F.0). |

Section [§E.7](#e7-contradictions-recorded-in-this-section-rather-than-resolved-silently) records five
further contradictions internal to the evidence base — between audit notes rather than between
drafts — and resolves none of them silently.

---

## A. Repository audit — `erpnext_enhancements`

### A.0 What was audited, and the numbers that are wrong in our own docs

| Fact | Value | Source |
|---|---|---|
| Worktree | `C:/Users/nbbsh/Documents/GitHub/erpnext_enhancements/.claude/worktrees/sf-google-chat-ref-86390f` | branch `claude/sf-google-chat-ref-86390f`, HEAD `6da57b85` |
| App version at audit | `1.260.3` | `erpnext_enhancements/__init__.py:1`, `package.json:3` — re-verified this session |
| Frappe bound | `>=16.0.0,<17.0.0` | `pyproject.toml:35` (`[tool.bench.frappe-dependencies]`) |
| Python bound | `>=3.10` | `pyproject.toml:7` |
| Licence | `mit` | `hooks.py:26` — re-verified this session; load-bearing for §C |
| Frappe modules | 28 | `erpnext_enhancements/modules.txt:1-28` |
| DocTypes shipped | 187 across 26 modules | `notes_ee_audit.md:781-782` |
| `hooks.py` size | 1288 lines | `notes_ee_audit.md:146` |
| **Custom Fields in fixtures** | **513** | `erpnext_enhancements/fixtures/custom_field.json`, counted with `json.load` this session |
| **Property Setters in fixtures** | **409** | `erpnext_enhancements/fixtures/property_setter.json`, same |

> **Correction, and it must not be repeated.** `CLAUDE.md`, `README.md:147`,
> `erpnext_enhancements/fixtures/README.md:4-5` **and the Phase 0 prompt itself** all say
> *"~425 Custom Fields and ~349 Property Setters"*. The files hold **513 and 409**. The repo's
> own documentation is stale by ~88 and ~60 records. `DECISIONS.md` D8 requires the corrected
> numbers; `notes_gap_report.md` §0-8 counted them independently and got the same answer, and I
> counted them a third time while writing this. **Whoever next edits `fixtures/README.md` should
> fix the number there — Phase 0 writes no code, so this ADR only records it.**

> **Second correction, same discipline.** `notes_ee_audit.md` §2.19 asserts *"the asymmetry:
> `permission_query_conditions` has 11 entries, `has_permission` has 10"* and its own inline
> VERIFY admits the count was not done carefully. It is **10 and 10, at exact parity** — the same
> ten DocTypes in both (`hooks.py:1123-1148`, `hooks.py:1150-1164`), recounted by
> `notes_gap_report.md` §0-9 and again by `notes_close_repo.md` §1.3. `DECISIONS.md` D8 makes the
> corrected reading binding, and the doctrine to lift is the positive one: **every
> `permission_query_conditions` entry has a `has_permission` twin, without exception.** A chat ACL
> that adds one must add both, in the same commit.

Third number worth fixing before it is quoted: the project premise of *"~50 users"*.
`notes_infra.md:140-151` measured **23 enabled Users, 20 enabled System Users, 18 active in the
last 30 days, 15 active Employees each with a linked User** on production.
`notes_register_reconciled.md` C4 flags this. It changes no decision, but three numbers in later
phases should be sized on ~20, not 50: the per-coworker Workspace Events subscription count, the
per-message notification fan-out, and the retrieval-corpus volume model. Where this ADR argues
"at ~50 users" (per `DECISIONS.md` D1) it is arguing an upper bound, and the real figure is lower.

---

### A.1 Module map, with chat relevance marked

`★★★` = directly load-bearing for chat · `★` = adjacent, will need a touch · `·` = irrelevant.
The authoritative index of per-module READMEs is `README.md:81-128`; read those, not the tree.

#### A.1a The 28 Frappe modules (`erpnext_enhancements/modules.txt`)

| Module (folder) | One line | Chat relevance |
|---|---|---|
| **Enhancements Core** `enhancements_core/` | The app's Single settings doctype, directory-link exclusions, desk shortcuts, Month-End Close, User Form Draft — `README.md:83` | **★★★** owns `ERPNext Enhancements Settings` (179 fields) where every feature flag lives, and owns `Collab Doctype`, the app's only realtime allowlist |
| **AI Governance** `ai_governance/` | AI write-confirmation records, **Triton Settings**, **Triton Assistant Settings**, model usage — `README.md:104` | **★★★** owns both Triton Singles, the `Triton Allowed User` whitelist child table, and `AI Pending Action` |
| **Google Drive** `google_drive/` | Drive folder provisioning, attachment sync, link manager — `README.md:106` | **★★★** **the house pattern for Google service-account auth** (`google_drive/drive_utils.py:115-146`) and for a delivery log (`Drive Sync Log`) |
| **Training** `training/` | UI-authored mixed-media courses, quizzes, watch telemetry, assignment, recertification — `README.md:92` | **★★★** the closest existing analogue to a chat build: a `www/` shell for non-desk users, a `fetch` transport with no `frappe.*` globals, 6+6 permission hooks, GCS signed URLs, and the "no DocPerm on content" doctrine |
| **Integration Hub** `integration_hub/` | Integrations Health page, GA4 / Search Console dashboard — `README.md:105` | **★★★** `api/integrations_health.py:409-410` registers the Triton/Gemini health checks; a chat integration registers here |
| **Accounting Intake** `accounting_intake/` | Document intake → AI extraction → two-gate review → draft posting — `README.md:102` | **★★★** the *second, independent* Triton client, and the second copy of the outbox/sweeper pattern |
| **Travel Management** `travel_management/` | Travel Trip workflow + child tables → draft Expense Claim — `README.md:89` | ★ the `permission_query_conditions`/`has_permission` precedent (`hooks.py:1124`, `:1155`) |
| **Sapphire Maintenance** `sapphire_maintenance/` | Template→Record→Result subsystem, visit wizard, customer portal — `README.md:87` | ★ `publish_realtime("msgprint", …, user=owner)` precedent, `api/maintenance_workflow.py:69,78` |
| **CRM Enhancements** `crm_enhancements/` | Opportunity customizations, fountain-move public intake, lead attribution, sales pipeline — `README.md:85` | ★ `publish_realtime(..., user=..., after_commit=True)` at `crm_enhancements/project_prompt.py:56-64`; the only atomic Redis counter in the repo (`fountain_move/intake.py:775-791`) |
| **Project Enhancements** `project_enhancements/` | Project Dashboard page, Master Project, procurement rollups, contracts + e-signature — `README.md:84` | ★ biggest realtime consumer (`project_dashboard_updated`); the e-sign "quietly went nowhere" digest is the missing-dead-letter precedent |
| **Device Management** `device_management/` | Device inventory + dashboard, BYOD-scoped permissions — `README.md:108` | ★ the `Notification Log` + `publish_realtime(user=…)` pair, `device_management/tasks.py:87-110` |
| **Workforce** `workforce/` | Time Kiosk pages, Job Interval clock-in sessions, payroll export — `README.md:94` | ★ the kiosk PWA's server half; the service-worker precedent and its scar |
| **KPI Dashboards** `kpi_dashboards/` | Nightly department KPI snapshots and dashboard workspaces — `README.md:103` | ★ the paired `permission_query_conditions` + `has_permission` precedent with a test (`hooks.py:1129-1135`, `tests/test_kpi_snapshot_permissions.py`) |
| **Offsite Backup** `offsite_backup/` | Verified nightly/weekly backups to a Drive Shared Drive — `README.md:110` | ★ the *second*, deliberately separate Google service account; **and the best exponential-backoff implementation in the repo** (`offsite_backup/drive.py:46-50,111-139`) |
| **QuickBooks Online** `quickbooks_online/` | QBO sync: OAuth2, REST client, entity mapping, CDC, webhooks — `README.md:98` | · as a *transport* — it has **no** rate limiting or 429 handling at all (`notes_close_repo.md` §2.1). Its `core/{client,api,utils,constants,tasks}.py` *shape* is the house integration template (`decisions/adr/0004-no-vendor-sdks.md:22-30`) |
| **Stripe Payments** `stripe_payments/` | Card + ACH payments, saved methods, dunning, payout reconciliation — `README.md:100` | · except the hand-rolled-webhook-signature precedent — `decisions/adr/0004-…:31-36` |
| Task Enhancements · Inventory Enhancements · MDM Integration · Morning Briefing · Asset Management · Process Documentation · Water Engineering · Plaid Banking · Fleet Maintenance · Product Configurator · Package Dispatch · QuickBooks Time | see `README.md:81-128` | · |

`google_calendar/` is **a folder but not a Frappe module** — absent from `modules.txt`, containing
only `README.md`, `__init__.py`, `calendar_utils.py`. **★★★ for chat**, because it is the proof
that adding a *new Google API surface* to this app means adding a scope and a `build(...)` call,
not a new credential (`google_calendar/calendar_utils.py:5-15,24-46`).

#### A.1b Shared / cross-cutting packages

| Folder | One line | Chat relevance |
|---|---|---|
| `api/` (49 files) | Every `@frappe.whitelist()` HTTP endpoint plus some doc-event hooks/workers — `erpnext_enhancements/api/README.md:1-8` | **★★★** a chat endpoint module lands here. **Mixed indentation warning at `api/README.md:7`** — see §A.7 |
| `public/` | Browser assets: desk JS/CSS, kiosk + wall + training front-ends — `public/README.md:1-7` | **★★★** |
| `www/` | Standalone web pages: `/kiosk`, `/wall`, `/itinerary`, `/training`, `/pay`, `/fountain-move`, `/contract-sign`, `/stripe-return`, … — `www/README.md:1-11` | **★★★** see §A.10 |
| `utils/` | Cross-cutting helpers **including two site-wide hooks/monkeypatches** — `utils/README.md:1-4` | **★★★** `utils/triton_sync.py` is the wildcard `after_save` hook |
| `tests/` (103 suites) | ~70 bench-free and in CI, the rest need a bench — `tests/README.md` | **★★★** §A.6 |
| `fixtures/` (16 files, 1,049 records) | Version-controlled Custom Fields, Property Setters, roles, workflows, web pages — `fixtures/README.md:1-16` | **★★★** §A.8 |
| `assistant_tools/` (34 files) | MCP tools exposed to Frappe Assistant Core **plus the AI write gate `_gate.py`** | **★★★** `_gate.py` is the seam that imposes the chat denylist — §B.10 |
| `scripts/` | Build/codegen + CI guards: `check_www_controllers.py`, `check_import_dirs.py`, two node test files | **★★★** |
| `decisions/adr/` | ADRs `0000`–`0008` — the engineering decision record. **This ADR is `0009`** | **★★★** |
| `setup/` | Migrate-time idempotent provisioning re-asserted on every `bench migrate` — `hooks.py:775-864` | ★ |
| `patches/` | One-time migrations; `patches.txt` is 492 lines of ordered entries | ★ |
| `custom_html_blocks/` (41 widgets × {js,html,css}) | Dashboard widget source of truth, upserted on migrate — `hooks.py:784` | ★ a chat "unread" widget would live here — but note the shadow-root CSS trap in §A.5 |
| `workspace_sidebar/` (10 JSONs) | **Frappe 16.29.0 made this an import dir** — `scripts/check_import_dirs.py:9-24` | ★ |
| `templates/`, `script_migrations/`, `data/`, `docs/`, `work-items/`, `infra/` | see `README.md` | ★/· |

---

### A.2 `hooks.py`, hook family by hook family

The file states its own annotation policy, and it constrains what a chat PR may add:

> ```
> **This file is annotated, and the annotations are documentation.** Several of the comments
> below record why an apparently odd choice is load-bearing — why global assets ship as
> esbuild bundles rather than raw `/assets` paths (the immutable one-year cache means edits
> never reach a device that already cached them), why two vendored UMD libraries are
> deliberately excluded from that rule, why `setup.document_locks` runs on `before_migrate`
> rather than `after_migrate`. Keep that density when you add an entry; a bare hook line with
> no explanation is the thing that gets "cleaned up" two years later.
> ```
> — `hooks.py:9-15`

> ```
> Every customization added to the app needs a line here **and** a matching entry in the
> owning module's README. See `CLAUDE.md` and `.claude/skills/`.
> ```
> — `hooks.py:17-18`

**Every top-level key actually present**, enumerated from source this session
(`grep -nE "^[a-z_]+ *=" erpnext_enhancements/hooks.py`):

`app_name`/`app_title`/`app_publisher`/`app_description`/`app_email`/`app_license` (21–26) ·
`app_include_css` (36) · `app_include_js` (43) · `web_include_css` (64) · `web_include_js` (67) ·
`doctype_js` (69) · `doctype_list_js` (252) · `doctype_calendar_js` (267) · `doctype_css` (271) ·
`override_doctype_class` (276) · `doc_events` (280) · `scheduler_events` (572) ·
`extend_bootinfo` (751) · `jinja` (757) · `before_migrate` (765) · `after_migrate` (775) ·
`fixtures` (872) · `override_whitelisted_methods` (1108) · `override_doctype_dashboards` (1112) ·
`permission_query_conditions` (1123) · `has_permission` (1150) · `ignore_links_on_delete` (1166) ·
`portal_menu_items` (1168) · `assistant_tools` (1192) · `assistant_skills` (1267) · a module-scope
monkeypatch call (1286–1288).

**Keys that are ABSENT** (each verified by a zero-hit grep): `website_route_rules`,
`website_redirects`, `website_context`, `update_website_context`, `notification_config`,
`on_session_creation`, `on_logout`, `sounds`, `standard_navbar_items`, `add_to_apps_screen`,
`has_website_permission`, `standard_queries`, `auto_cancel_exempted_doctypes`, `scheduled_tasks`,
`before_request`, `after_request`, `app_include_icons`, `page_js`, `webform_include_js`,
`required_apps`, `global_search_doctypes`.

> **The single most important structural fact for a chat build:** there is **no
> `website_route_rules` anywhere in the app**. Every public route this app serves is derived from
> a `www/` template filename. A chat SPA at `/chat/room/<id>` introduces the app's **first**
> such rule. That rule is now known to be expressible — see §A.10.

#### A.2.1 `app_include_css` — `hooks.py:36-42`

Preceded by the cache-invalidation rule, quoted in full because a chat PR is reviewed against it:

> ```
> # include js, css files in header of desk.html
> #
> # Everything global ships as esbuild bundles ("name.bundle.css/js", resolved
> # through assets.json to a content-hashed filename) — NOT raw /assets paths.
> # Raw /assets paths are served with a 1-year *immutable* Cache-Control and
> # carry no content hash, so edits to them never reach a device that already
> # cached them (the "Kanban fix works on desktop, phones still broken" bug,
> # v0.8.1). The only exceptions are the two vendored libraries below.
> ```
> — `hooks.py:28-35`

Two entries: `desk_enhancements.bundle.css` (37) and `desk_addons.bundle.css` (41, built from a
`.scss` entry — inline note at `hooks.py:38-40`).

**Chat collision.** A chat panel's CSS is imported into `public/css/desk_addons.bundle.scss`
(where `global_enhancements/triton_widget.css` already sits — `public/README.md:152`) or into
`desk_enhancements.bundle.css`. **A new `app_include_css` line carrying a raw `/assets` path is a
bug by ADR** — `decisions/adr/0008-global-assets-ship-as-bundles.md:37-40`. Cascade order is
deliberately preserved (`0008-…:54-56`).

#### A.2.2 `app_include_js` — `hooks.py:43-59`

Four entries: two **deliberate raw-path exceptions** (`js/vue.global.js` at 49,
`project_enhancements/lib/frappe-gantt.umd.js` at 50), then `kanban.bundle.js` (54) and
`erpnext_enhancements.bundle.js` (58) — *everything else global, including the Triton widget*.

> ```
> # Vendored global-defining libraries stay raw ON PURPOSE: importing a UMD
> # build from an esbuild bundle captures its exports instead of letting it
> # set window.Vue / window.Gantt — and their content never changes, so the
> # immutable /assets cache cannot serve them stale. Loaded first so the
> # globals exist before any bundled consumer runs.
> ```
> — `hooks.py:44-48`

**Chat collision.** A chat FAB or panel is a *global desk script*, so it is `import`ed into
`public/js/erpnext_enhancements.bundle.js` — **not** added as a new `app_include_js` line. That
bundle already imports `./global_enhancements/triton_widget.js`, so **a second floating button on
every desk page collides with the Triton FAB in screen corner, z-index and keyboard shortcut.**
Locked decision #8 resolves this by *extending* that widget rather than adding a second one; this
section is the evidence for why #8 is right and not merely a preference.

#### A.2.3 `web_include_css` / `web_include_js` — `hooks.py:61-67`

**Both are bare strings, not lists** (`web_include_css = "login_enhancements.bundle.css"`,
`web_include_js = "login_enhancements.bundle.js"`). A chat feature needing a script on *website*
pages — which is what `/training` serves for Website Users with `desk_access = 0` — must either
convert these to lists or add its import into `login_enhancements.bundle.js`. Converting a scalar
to a list changes Frappe's hook-merging behaviour; the repo already carries a related warning at
`hooks.py:1186-1189` (*"Frappe's hook merging list-wraps scalar values and FAC does not unwrap
them"*). Carried as **VERIFY: whether making `web_include_js` a list changes merge behaviour for
other installed apps — read `frappe/__init__.py::get_hooks` — blocks: only whether a chat script
can reach website pages without touching the login bundle** (`notes_ee_audit.md` §13 VERIFY 24).

#### A.2.4 `doctype_js` / `doctype_list_js` / `doctype_calendar_js` / `doctype_css` — `hooks.py:69-273`

31 `doctype_js` keys, 8 `doctype_list_js`, 2 `doctype_calendar_js`, and exactly **one**
`doctype_css` entry, which *is* a raw path and is allowed because `doctype_css`/`doctype_js` load
through `frappe.require`'s version-aware cache (`public/README.md:20-21`).

The load-bearing note for chat is the one explaining why *most* doctypes have no entry:

> ```
> # NOTE: the custom Comments App is now mounted globally by comments_auto.js
> # (see app_include_js + COMMENT_APP_DOCTYPES). Doctypes that only needed the
> # comments tab no longer require a doctype_js entry; the entries below keep
> # only their non-comments form scripts.
> ```
> — `hooks.py:148-151`

**Chat collision.** If chat wants a per-document side panel ("discuss this Project" — locked
decision #10), the precedent is **`comments_auto.js` mounting globally off a
`COMMENT_APP_DOCTYPES` list** (`public/README.md:80`), not 23 `doctype_js` entries. That list
**deliberately excludes** Project, Customer, Employee, Account, Timesheet and Contact because
their own form scripts mount it (`public/README.md:80,162`) — a chat mount must respect the same
double-mount hazard.

Two more entries are direct templates for chat's own settings UI: `Training Settings`
(`hooks.py:82`, with the comment at 77-81 explaining that **a Password field cannot take a
multi-line service-account JSON by paste**, so the key is entered through a dialog) and
`Project Folder Google Drive Settings` (`hooks.py:87`, same rationale at 83-86). **Any chat
credential UI copies this pattern** — see §A.4 and §E.

#### A.2.5 `override_doctype_class` — `hooks.py:276-278`

One entry: `"Task"`. Nothing chat collides with.

#### A.2.6 `doc_events` — `hooks.py:280-570`

23 DocType keys plus the wildcard. Three quoted comments constrain a chat build directly.

The `User` hook — chat will almost certainly want one, and this is the only existing one:

> ```
> # training: a Role Profile change rewrites a user's roles wholesale and can
> # bring a role-targeted Required course into scope for someone the Employee
> # hook never sees. Compares the roles child table against
> # get_doc_before_save() and returns immediately when unchanged; the sweep
> # itself is enqueue_after_commit so it can never delay a login or a save.
> ```
> — `hooks.py:525-529`

The `Employee` hook, on why a naive `on_update` handler is a performance bug:

> ```
> # training: re-evaluate assignment rules when something a rule keys off
> # actually moved. Guarded with get_doc_before_save() on department /
> # designation / grade / employment_type / status (plus user_id first
> # appearing) — without that comparison EVERY Employee save enqueues a
> # full rule sweep, and Employee is saved often.
> ```
> — `hooks.py:507-511`

The `before_insert`-not-`validate` rule, which a chat gate would hit the same way:

> ```
> # ORDER IS LOAD-BEARING, and so is the hook itself being before_insert.
> # ... It cannot live on `validate`:
> # create_project_from_opportunity_background sets
> # flags.ignore_validate before inserting, so a validate hook silently
> # never runs on the path that creates most projects. before_insert
> # survives both ignore_validate and ignore_permissions.
> ```
> — `hooks.py:297-304`

**THE WILDCARD HOOK IS THE SINGLE LARGEST CHAT COLLISION IN THE FILE.**

```python
"*": {
    "after_save": "erpnext_enhancements.utils.triton_sync.global_triton_sync",
},
```
— `hooks.py:567-569`

Every document save on the site already enqueues a background HTTP POST. `PLAN.md:43` calls it a
bulk-operation hazard — *"a wildcard `'*'` after_save hook → `global_triton_sync` (no
flag/settings guard — one queued POST per ORM save)"* — and WI-050 exists because of it.
**A `Chat Message` DocType would enqueue one Triton webhook POST per message.** The exclusion
mechanism already exists in two forms in `utils/triton_sync.py`:

- **module-level**: `excluded_modules`, `utils/triton_sync.py:30-33`, already excluding
  `"Telephony"` and `"AI Governance"` — the comment at `:25-29` explains *"AI Governance holds
  high-volume log doctypes … that would spam the webhook queue for zero indexing value"*;
- **doctype-level**: `excluded_doctypes = ["Fountain Move Request", "Fountain Move Invite"]`,
  `utils/triton_sync.py:41`, for PII reasons documented at `:35-40`;
- child tables and Singles are skipped automatically — `utils/triton_sync.py:48`.

> **Named Phase 1 obligation (CHAT-EXCL-1).** The chat module is added to
> `utils/triton_sync.py:30-33`'s `excluded_modules` **in the same commit that creates the first
> chat DocType**, and a bench-free test asserts it. The PII rationale at `:35-40` applies verbatim
> — chat message bodies are exactly the class of content that block already exists to keep out of
> a third-party webhook.

#### A.2.7 `scheduler_events` — `hooks.py:572-746`

62 handlers: **13 `cron`** (`:573-637`), **34 `daily`** (`:638-708`), **13 `hourly`**
(`:709-739`), **2 `weekly`** (`:740-745`). Two rationales a chat job inherits:

> ```
> # QuickBooks Online sync — STAGGERED across the hour, not all fired together.
> # The three jobs each write the single QuickBooks Online Settings doc (token
> # refresh must save() through the doc for Password-field encryption, so it
> # can't use db.set_value like the cursor writes do); firing them at the same
> # instant made two saves race and the loser fail with TimestampMismatchError
> ```
> — `hooks.py:583-588`

> ```
> # Morning Briefing pre-generation, weekdays 06:30. Frappe evaluates cron
> # in the site's System Settings timezone (must be America/Denver here).
> ```
> — `hooks.py:574-575`

**Retention is a house convention, and chat must not break it.** Five separate `purge_old_*` daily
jobs exist (`hooks.py:677,678,686,687,688,699`), each with a retention setting on a Settings
doctype. **A `Chat Message` DocType without a purge job and a retention setting breaks the
pattern** — and `notes_gap_report.md` §D-3 identifies exactly this as the mitigation that makes
storing third-party content acceptable at all.

#### A.2.8 `extend_bootinfo` — `hooks.py:748-751`

Ships **13 keys** to every desk session (`boot.py:74-86`), all named `ee_<feature>`. Two
constraints, both quoted:

> ```
> Runs once per desk session load; keep it cheap — everything added here is
> serialized into every desk page's boot payload.
> ```
> — `boot.py:3-4`

> ```
> the server-side guards in ``feature_flags`` remain the authority.
> ```
> — `boot.py:47-48`

**Chat collision.** The chat widget's enablement flag belongs here as `ee_chat`. It must **not**
ship the roster or the room list — that violates "keep it cheap". Note that `ee_desk_shortcuts`
(`boot.py:76`) *does* ship a per-user computed list, justified as *"purely cosmetic — target pages
enforce their own permissions — and is computed defensively so it can never break boot"*
(`boot.py:39-40`). A chat unread **count** could ride that justification; a chat **room list**
cannot.

#### A.2.9 `before_migrate` / `after_migrate` — `hooks.py:764-864`

`before_migrate` has one entry, `setup.document_locks.clear_stale_role_profile_locks`, with a
six-line comment (765-771) explaining the deploy-FLUSHDB → orphaned-lock → `DocumentLockedError`
failure. `after_migrate` has 23 entries with ordering load-bearing in three places, and this
burn-in note is exactly the class of bug a chat setup hook could reproduce:

> ```
> # training: starter Training Badges. Insert-only and inert until gamification is on.
> # Lives in `setup` beside the starter categories, not in `gamification`, which is
> # the runtime awarding logic. This pointed at gamification and the function was
> # never written there, so every migrate since Phase 4 died on AttributeError.
> ```
> — `hooks.py:840-844`

CI now guards it — `test_hook_targets_resolve`, `.github/workflows/ci.yml:424-429`:
*"Every dotted path in hooks.py must name something that exists. Frappe resolves after_migrate at
the very END of `bench migrate`, so a typo there fails the deploy after every schema change has
already been applied — the most expensive possible moment to find one. It cost a production
build."*

#### A.2.10 `fixtures` — `hooks.py:866-1106`

14 entries. Full treatment in §A.8. The section header is the statement of intent:

> ```
> # Version-controlled customizations: every manually created Custom Field and
> # Property Setter on the site lives in fixtures/ and is re-applied on migrate —
> # the repo is the source of truth, UI changes do not survive deploys.
> ```
> — `hooks.py:866-871`

#### A.2.11 `override_whitelisted_methods` — `hooks.py:1108-1110`

One entry. **This is the mechanism by which chat *could* intercept a core Frappe endpoint** — for
example `frappe.desk.form.assign_to.add`, to post a chat notification on assignment. Note that six
call sites already import `frappe.desk.form.assign_to` **function-locally**
(`api/maintenance_dispatch.py:100`, `api/maintenance_visit.py:131-132`, `api/telephony.py:1274`,
`crm_enhancements/fountain_move/notify.py:163`), so an override would **not** intercept those.

#### A.2.12 `permission_query_conditions` (`hooks.py:1123-1148`) and `has_permission` (`hooks.py:1150-1164`)

**Ten entries each, the same ten DocTypes** (§A.0). The two doctrines available to chat are both
stated in comments in this file.

**Doctrine A — DocPerm + row filter.** `KPI Snapshot`:

> ```
> # KPI Snapshot: a department manager sees their own departments' numbers only.
> # The DocPerms had to widen from System-Manager-only for the KPI assistant tool
> # to be visible to the people who own the numbers, and a DocPerm is doctype-wide
> # -- without this, `read` on the doctype would have meant read on every
> # department. api/kpi.py::_can_view decides which; this enforces it where Frappe
> # actually checks reads.
> ```
> — `hooks.py:1129-1134`

and its single-document counterpart:

> ```
> # The single-document counterpart of the KPI Snapshot query condition above.
> # A query condition filters lists; this is what refuses a direct read of one
> # snapshot by name.
> ```
> — `hooks.py:1151-1153`

**Doctrine B — no DocPerm at all.** Training content:

> ```
> # Course CONTENT is
> # not scoped here at all -- learner roles hold no DocPerm on Training Course /
> # Version / Lesson / Question / Answer Option, so /api/resource refuses them
> # outright and the answer key cannot leak through a careless future endpoint.
> ```
> — `hooks.py:1136-1141`

**Two corrections to how these doctrines have been described in the audit notes, both from
`notes_close_repo.md` §1.2.2 and both material:**

1. **Doctrine B is an analogy, not a precedent.** `Training Course` itself carries **three**
   DocPerm rows (System Manager, Training Manager, Training Author). **Zero of this app's 187
   DocTypes have an empty `permissions` array.** The real doctrine is *"the roles we are defending
   against hold no DocPerm"*, and its adversary is a Website User with `desk_access = 0`.
2. **`Administrator` bypasses all of it**, before any hook runs. Verified from v16 source:
   `if user == "Administrator": … return True` in `frappe/permissions.py`
   (`notes_close_repo.md` §1.2.2). "No DocPerm" means *"unreachable by everyone except
   Administrator"*, not *"unreachable"*.

The permission model that follows from these two corrections, and the contradiction between the
two closing notes about it, is in §B.10 — because it is the same question as the MCP denylist.

#### A.2.13 `ignore_links_on_delete` — `hooks.py:1166`

`["User Form Draft"]`. **A chat message linking to arbitrary documents (decision #10) will block
their deletion with `LinkExistsError` unless it is added here.** Note also that
`utils/patch_delete.py` monkey-patches the delete endpoints so link conflicts become a recoverable
JSON signal on HTTP POST (`utils/README.md:14-25`) — a chat link rides into that path.

#### A.2.14 `portal_menu_items` — `hooks.py:1168-1176`

3 entries (`/training`, `/maintenance-records`, `/pay`), with the warning at 1169-1172 that *"a
dead menu item teaches people to ignore the menu"*.

#### A.2.15 `assistant_tools` / `assistant_skills` — `hooks.py:1178-1273`

26 tool paths plus one skills manifest, and the **optional-dependency discipline** any chat
integration copies:

> ```
> # These hooks are read ONLY by frappe_assistant_core: its tool loader imports
> # the dotted paths below (each wrapped in try/except on FAC's side), and its
> # migrate hook syncs the skills manifest into FAC Skill rows. On sites without
> # FAC installed they are inert strings — erpnext_enhancements has no import-
> # time or install-time dependency on FAC. Do not import assistant_tools/* from
> # app code (tripwire-tested).
> ```
> — `hooks.py:1181-1186`
> ```
> # NOTE: each module filename must equal its tool's name (FAC's custom_tools
> # plugin derives tool identifiers from the module path).
> ```
> — `hooks.py:1190-1191`

#### A.2.16 The module-scope side effect — `hooks.py:1275-1288`

```python
from erpnext_enhancements.monkeypatches import apply as _apply_monkeypatches
_apply_monkeypatches()
```

> ```
> # Carried in app code so they survive `bench update` (vs. editing apps/frappe).
> # Applied here because Frappe imports every app's hooks.py in every worker the
> # first time it loads hooks, so this runs once per process before any patched
> # path is reached. `_load_app_hooks` skips functions and `_`-prefixed names, so
> # neither the import alias nor the call is mistaken for a hook.
> ```
> — `hooks.py:1278-1284`

**Chat collision.** `hooks.py` executes code at import time **in every worker**. Anything chat adds
here must be import-safe with no DB access. (The same mechanism is what makes the `_gate.py`
denylist in §B.10 reliable: `assistant_tools/__init__.py:23-25` applies the gate at import time.)

---

### A.3 DocType inventory, and what already models conversation, notification, Google, presence or Triton state

187 DocTypes across 26 modules. Placement is asserted by
`erpnext_enhancements/tests/test_doctype_modules.py`, bench-free and in CI at `ci.yml:205-206`:

> ```
> For each DocType JSON shipped by this app, assert that:
>   * it lives under its module's directory — Frappe maps a doctype's ``module``
>     to ``<app>/<scrub(module)>/doctype/<name>/`` — and
>   * that module is registered in ``modules.txt``
> ```
> — `tests/test_doctype_modules.py:3-8`

**The direct answer to "what already exists that chat would overlap":**

| DocType | Module | Why it matters to chat |
|---|---|---|
| **Triton Settings** (single) | AI Governance | The Triton connection: `gateway_url`, `admin_webhook_secret` (Password), `chat_model_id`, `voice_model_id`, `email_model_id`, `maps_api_key` (Password, doubling as the Vertex AI key), 5 Twilio fields, `softphone_users`, `call_recordings_drive_folder` — `ai_governance/doctype/triton_settings/triton_settings.json:6-30` |
| **Triton Assistant Settings** (single) | AI Governance | The widget's behaviour and **the rollout gate to copy**: `enabled`, `default_model`, `request_timeout`, `enable_page_context`, `enable_write_actions`, `debug_logging`, `restrict_to_whitelist`, `allowed_users` (Table) — `triton_assistant_settings.json:6-19` |
| **Triton Allowed User** (child) | AI Governance | The per-user rollout whitelist rows |
| **AI Pending Action** | AI Governance | The human-in-the-loop write-confirmation record; `_gate.py:458-472` writes a `Notification Log` **plus** `publish_realtime("ai_pending_action", …, user=…)` for it — the closest thing in the app to a directed chat notification |
| **AI Action Log** | AI Governance | Append-only audit of AI-initiated actions, purged daily (`hooks.py:688`). **The denylist's evidence trail writes here** (§B.10) |
| **Collab Doctype** (child) | Enhancements Core | The allowlist behind `frappe.boot.collab_doctypes` — the app's only "which surfaces get live multi-user behaviour" registry |
| **Drive Sync Log** | Google Drive | Google API call outcome log with `status`/`attempts`/`payload`, retried daily (`hooks.py:690`) — **the outbox template**, §A.11 |
| **Project Folder Google Drive Settings** (single) | Google Drive | The primary Google service-account credential (`service_account_json` Password + `shared_drive_id`), reused by Calendar |
| **Offsite Backup Settings** (single) | Offsite Backup | A deliberately **separate** Google service account — `README.md:159` |
| **Training Settings** (single) | Training | A **third** Google credential (`gcs_service_account_json`, for GCS signed URLs — `training/gcs_media.py:75`) |
| **GA4 Settings** (single) | Integration Hub | A **fourth** Google credential path — a service-account JSON as a *Private File path*, `api/analytics.py:78,278` |
| **Managed Device** | Device Management | `_notify_assignee` at `device_management/tasks.py:87-110` is the Notification Log + realtime DM pattern |
| **Daily Briefing** | Morning Briefing | The closest existing "per-user generated content, cached" model |
| **ERPNext Enhancements Settings** (single) | Enhancements Core | **179 fields.** Every feature flag. A chat section belongs here |

**There is NO DocType modelling a chat message, a chat room/space, a chat membership, a presence
record, or any Google Chat entity.** Verified by a filename search over
`*/doctype/*/*.json` for `chat|message|presence|thread|conversation|notif`, which returns exactly
one hit.

**And that one hit is not prior art.** `Training Question Thread` — the only DocType in the app
with "Thread" in its name — was read in full by `notes_gap_report.md` §0-10:
`autoname: naming_series` (`TRN-QNA-.######`), not a child table, fields
`naming_series, lesson, course, course_version, status (Open/Answered/Hidden), is_public, upvotes,
user, asked_on, at_seconds, question (Text), answer (Text Editor), answered_by, answered_on`.
**It is a flat one-question/one-answer record with no reply table, no parent link and no thread
root**, and it uses a naming series, which Phase 0 §4.G explicitly forbids for messages because
*"series counters serialize inserts"*. **Do not cite it as precedent.**

**There is NO Google Chat code anywhere in the app.** A repo-wide grep for
`Google Chat|google_chat|chat.googleapis|chat.spaces` returns nothing. **In this codebase the word
"chat" means the Triton assistant** (`triton_chat.py`, `chat_model_id`) and nothing else — a
naming hazard the ADR resolves by using `Chat Room` / `Chat Message` / `gchat_*` per
`DECISIONS.md` D5, and never the bare word "chat" for Triton.

---

### A.4 Every Triton integration point

#### A.4.0 Two things named "Triton", plus a legacy name

> ```
> **Note:** "Triton" appears in two roles — (1) the telephony/AI **gateway** service (service
> user `triton@sapphirefountains.com`, formerly "Poseidon" — see the rename patches), and
> (2) the in-app **AI assistant** widget. They share the `Triton Settings` connection but the
> widget has its own `Triton Assistant Settings`.
> ```
> — `README.md:167`

The "Poseidon" legacy survives in `patches/rename_poseidon_service_user.py`,
`patches/rename_poseidon_settings_doctype.py`, a Role Profile literally named `"Poseidon"`
(`hooks.py:1093`), and the GCP project id `sapphire-fountains-poseidon` used by the Gemini client
(`api/README.md:71`).

#### A.4.1 Where the base URL and auth come from

**There is no `site_config.json` key and no environment variable for Triton.** Four configuration
sources, in this precedence:

| Source | Field | Type | Read at |
|---|---|---|---|
| `Triton Settings` | `gateway_url` | Data | `triton_chat.py:63`, `api/telephony.py:987,1335,1410`, `accounting_intake/triton_client.py:26`, `ai_governance/doctype/triton_settings/triton_settings.py:29` |
| `Triton Settings` | `admin_webhook_secret` | **Password** | `triton_chat.py:64`, `api/telephony.py:990,1338,1401,109`, `triton_settings.py:30` |
| `Accounting Intake Settings` | `triton_gateway_url`, `triton_service_secret` (Password) | Data / Password | `accounting_intake/triton_client.py:24,27` — **falls back** to `Triton Settings.gateway_url` at `:26` |
| **Hard-coded** | `https://triton.sapphirefountains.com/api/v1/webhooks/frappe-webhook` | literal | **`utils/triton_sync.py:53`** |

> **`utils/triton_sync.py:53` is the one hard-coded Triton URL in the app**, and it is a real
> deployment hazard: a test bench posts to production Triton. Chat must not add a second one. Any
> fix to it changes behaviour and needs its own changelog entry, so it is out of scope for a chat
> PR and belongs in its own commit.

#### A.4.2 Outbound: ERPNext → Triton

22 call sites. The chat-relevant subset, each with what it authenticates with:

| # | Site | Path | Auth presented |
|---|---|---|---|
| 1 | `triton_chat.py:137-142` | `POST {base}/api/v1/auth/erpnext-bridge/token` | `Bearer {admin_webhook_secret}`; body `{"email", "full_name"}`; response cached in `frappe.cache()` under `triton_user_token::{user}` with TTL `expires_in − 120s` (`triton_chat.py:151-155`) |
| 2–15 | `triton_chat.py:283-532` | sessions, models, personas, morning-briefing, messages, action confirm/cancel | `Bearer {per-user JWT}` |
| 16 | `triton_chat.py:567,571-581` | `POST {base}/api/v1/assistant/sessions/{id}/query/stream` | per-user JWT + `Accept: text/event-stream`; **relayed byte-for-byte** into a streaming `werkzeug` Response (`triton_chat.py:597-601`) |
| 17 | `utils/triton_sync.py:53,64-70` | the hard-coded frappe-webhook | **none**; `{"doctype","name","user_id":1}`; fire-and-forget via `frappe.enqueue('requests.post', …)` |
| 18 | `accounting_intake/triton_client.py:45-51` | `POST {gateway}/api/v1/document-ai/extract` | `Bearer {triton_service_secret}` — **the docstring at `:13` says `X-Triton-Service-Secret`; the code sends `Authorization: Bearer`. The docstring is stale.** |
| 19 | `ai_governance/…/triton_settings.py:49-57` | `POST {gateway_url}/refresh-settings` | `Bearer {admin_webhook_secret}`, enqueued `enqueue_after_commit=True` from `on_update` |
| 20–22 | `api/telephony.py:1000,1015,1344-1349,1416` | `/api/outbound-call`, `/api/send-sms` | `Bearer {admin_webhook_secret}` |

**There is no shared HTTP client.** Four modules each build their own `requests` call with their
own headers and timeout policy. Only `triton_chat.py` retries (once, on 401, after force-refreshing
the token — `triton_chat.py:169-188`).

#### A.4.3 Inbound: Triton → ERPNext — the model a Google Chat webhook copies

All inbound endpoints live in `api/telephony.py` and `api/call_intelligence.py`, are
`allow_guest=True`, and are guarded by `@validate_webhook_secret`, a Bearer shared-secret decorator
reading `Triton Settings.admin_webhook_secret` (`api/telephony.py:97-109`). Every handler switches
identity: `frappe.set_user("triton@sapphirefountains.com")` — `api/telephony.py:134,149,177,331,378,462,542`,
`api/call_intelligence.py:60,290` — and writes with `ignore_permissions=True` (`api/README.md:67`).

**So the house inbound pattern is: a guest endpoint + a shared-secret decorator + an identity
switch to a dedicated service user + `ignore_permissions=True` writes.** A Google Chat inbound
endpoint follows the same *shape* but **not** the same credential: Chat presents a Google-signed
JWT, and the verification is four-part, not a shared secret — §E and `notes_google_verify.md:1299-1303`.

And the risk precedent, which chat must not repeat: `PLAN.md:44` records that the QuickBooks Time
webhook (`quickbooks_time/api.py::qb_timesheet_webhook`) is *"guest-callable with **no** signature
verification — a live security gap"* (WI-046).

#### A.4.4 The widget, browser half

`public/js/global_enhancements/triton_widget.js` — **1,404 lines**, plus
`public/css/global_enhancements/triton_widget.css` — 759 lines. Its own header:

> ```
> * Targets: every ERPNext desk page (a global floating widget, not tied to any
> * doctype). Loaded via: hooks.py `app_include_js` (global). Self-disables unless
> * the server `get_config` reports it enabled and the user is not Guest.
> *
> * A floating trident button on every ERPNext desk page that opens a chat panel
> * wired to Triton. All traffic goes through same-origin whitelisted methods on
> * `erpnext_enhancements.triton_chat` (no CORS, no client-side secrets). The chat
> * stream is relayed back as SSE and rendered token-by-token.
> ```
> — `triton_widget.js:1-13`

Mechanics that matter for decision #8: `const METHOD = "erpnext_enhancements.triton_chat";` and
`const xcall = (m, args) => frappe.xcall(...)` (`:16,45`); localStorage keys `triton_session_id`,
`triton_model`, `triton_persona_key`, `triton_briefing_date` (`:17-26`); markdown via
`frappe.markdown` with an escape fallback (`:47-54`); `prefers-reduced-motion` honoured (`:62-63`);
**Mermaid lazy-loaded from jsDelivr** (`:76`). It is **100% vanilla JS — it does not use Vue at
all** (`notes_widget_inventory.md:963-966`), which means the two-Vue-copies hazard does not exist
today and would be *created* by a bundled-Vue SPA.

#### A.4.5 The rollout gate — copy this exactly

`triton_chat.get_settings()` (`triton_chat.py:50-86`) merges the two Singles.
`user_has_widget_access()` (`:89-104`) is enforced **server-side on every token mint**:

> ```
> # Whitelist gate (server-side enforcement). Every Triton API call mints a
> # token through here, so a non-whitelisted user cannot reach Triton even by
> # calling the whitelisted methods directly.
> ```
> — `triton_chat.py:120-122`

and it is folded into `get_config()` so a non-whitelisted user never even sees the button
(`triton_chat.py:249-253`). **Master switch + `restrict_to_whitelist` + `allowed_users` child
table, enforced server-side, with the client config as a cosmetic echo — that is the staged-rollout
template for chat**, and it composes with the Google-side pilot gate in §E.5.1: the Chat app's
Visibility setting scopes `@triton`; **this** gate scopes the ERPNext half.

#### A.4.6 Two security observations that bound what chat may do

1. `get_gateway_config` in `ai_governance/doctype/triton_settings/triton_settings.py:63-97`
   **returns decrypted secrets** — Maps API key, Twilio API secret, admin webhook secret — to any
   System Manager over HTTP, gated only by `if "System Manager" not in frappe.get_roles(...)`
   (`:74-75`). Cross-reference the standing memory note *"Prod compromise 2026-08-02"*.
   **A chat credential must not be added to that response.**
2. `triton_chat.py:148,183,587` guard `frappe.log_error` behind `settings["debug"]` and truncate
   bodies to 500 chars — consistent with the memory note *"Frappe log_error leaks secrets"* and
   with the CI-guarded circuit breaker at `ci.yml:326-333`. **Chat relay logging inherits this
   rule**, and it matters more here, because the payload being logged is a colleague's message.

---

### A.5 The esbuild bundle model, and exactly how the widget reaches the page

`decisions/adr/0008-global-assets-ship-as-bundles.md` is the authority.

> ```
> Every global asset ships as a bundle. `app_include_css` and `app_include_js` in `hooks.py`
> reference `name.bundle.css` / `name.bundle.js`, and the content hash makes cache
> invalidation automatic.
> ```
> — `0008-…:26-28`
> ```
> Raw `/assets` paths are served with a **one-year immutable `Cache-Control`** and carry no
> content hash. So an edit to one never reaches a device that already cached it. Ever — not on
> the next deploy, not on a hard refresh.
> ```
> — `0008-…:16-19`
> ```
> - **A raw `/assets` path in `hooks.py` is a bug**, even when it works in testing …
> - Adding a global script means adding it to the relevant bundle entry
>   (`public/js/erpnext_enhancements.bundle.js`, `kanban.bundle.js`, …), not adding a new
>   include line.
> ```
> — `0008-…:44-49`

**There are exactly 7 bundle entrypoints** under `public/`:
`js/erpnext_enhancements.bundle.js` (from `hooks.py:58`) · `js/kanban.bundle.js` (`:54`) ·
`js/login_enhancements.bundle.js` (`:67`) · `css/desk_enhancements.bundle.css` (`:37`) ·
`css/desk_addons.bundle.scss` → `desk_addons.bundle.css` (`:41`) ·
`css/login_enhancements.bundle.css` (`:64`) · `css/gantt_widget.bundle.css` (**linked at runtime by
JS**, not by `hooks.py`).

**The exact chain by which the Triton widget reaches a desk page — this is the chain decision #8
extends:**

1. `hooks.py:58` lists `"erpnext_enhancements.bundle.js"` in `app_include_js`.
2. Frappe's esbuild resolves it through `sites/assets/assets.json` to a **content-hashed** filename
   and emits a `<script>` into `desk.html`.
3. That bundle `import`s `./global_enhancements/triton_widget.js` (immediately after
   `mermaid_theme.js` — the order is load-bearing and commented).
4. `triton_widget.js` is an IIFE (`:15`) that calls
   `frappe.xcall("erpnext_enhancements.triton_chat.get_config")`.
5. If `get_config()` returns `enabled: true` — master switch **and** `user_has_widget_access`
   (`triton_chat.py:253`) — it builds the FAB and panel.
6. Styles come from `public/css/global_enhancements/triton_widget.css`, imported by
   `public/css/desk_addons.bundle.scss` (`public/README.md:152`), reaching the page via
   `hooks.py:41`.
7. Chat traffic returns through same-origin `frappe.xcall`; the SSE stream is a plain streaming
   HTTP response from `triton_chat.stream_query` (`triton_chat.py:597-601`).

**There is no build step of this repo's own.** `package.json:5-7` has exactly one script,
`"lint": "eslint ."`, and there is no esbuild/rollup/vite config file anywhere. **Bundling is
entirely Frappe's `bench build`.** A chat SPA that wants its own build toolchain would be
introducing the first one — which is a real cost and belongs in the Phase 3 decision, not assumed.

**Three exceptions and scars a chat surface must respect:**

- **The two vendored UMD globals stay raw on purpose** (`hooks.py:44-48`), and `vue.global.js` is
  `vue v3.5.26` setting `window.Vue` (`notes_widget_inventory.md:588-591`). If Phase 3 ships a
  bundled Vue, **two Vue copies exist on the same page**; externalising to `window.Vue` is the only
  option that eliminates rather than manages the hazard, and it pins the SPA to 3.5.26.
- **`gantt_widget.bundle.css` must stay bundled AND be linked per root node** — *"as a raw
  `/assets` path it was served immutable *and* left stale on disk by a deploy…  **Must be linked
  per root node** — Custom HTML Blocks render in a shadow root that document-level styles cannot
  cross"* (`public/README.md:150`). **A chat widget rendered inside a Custom HTML Block will not
  receive document-level CSS.**
- **The standalone PWAs sit outside the bundle mechanism** and version their assets off
  `utils/deploy.py::get_deploy_version()` — the mtime of `sites/assets/assets.json`, because
  Frappe's own helper *"falls back to a **random** string and would re-bust on every page view"*
  (`utils/deploy.py:29-31`). **A chat `www/` page must use `get_deploy_version()`** — and must not
  copy `itinerary.py`'s import of it from `www.kiosk`, which `www/training.py:34-36` explicitly
  warns against.

---

### A.6 Test setup, and the exact CI step names for the unittest-vs-pytest split

`.github/workflows/ci.yml` — 624 lines, **4 jobs**. Triggers are `push: branches: [main]` plus
`pull_request:` (`ci.yml:22-25`), with `concurrency … cancel-in-progress: ${{ github.event_name ==
'pull_request' }}` (`:45-47`) so **a `main` run is never cancelled, because `main` deploys
automatically** (`:41`).

| Job | Name (as it appears in the GitHub UI) | Lines | Gate? |
|---|---|---|---|
| `lint` | **`Lint (ruff, advisory)`** | `:70-81` | No — `continue-on-error: true` at `:80` |
| `undefined-names` | **`No undefined names (F821)`** | `:105-115` | **Hard gate** |
| `unit-tests` | **`Standalone unit tests`** | `:134-581` | **Hard gate**, 43 steps, `timeout-minutes: 10` |
| `version-sync` | **`Version sync (__init__.py == package.json)`** | `:593-607` | **Hard gate** |

The F821 rationale is the most quotable paragraph in the file for anyone writing
swallow-everything handlers — and chat will write several:

> ```
> # F821 is not style. An undefined name is a NameError at runtime, and this app
> # swallows exceptions on purpose in the places it is most likely to bite:
> # scheduler jobs, doc_events and notification helpers all wrap their bodies in
> # `except Exception: frappe.log_error(...)` so a notification problem cannot
> # cost the work it was reporting on. That guard is correct — and it converts a
> # NameError into silence.
> ```
> — `ci.yml:83-104`

**Installed dependencies for the whole `unit-tests` job are one line** — `ci.yml:144`:

```yaml
      - run: python -m pip install --upgrade pip httpx pytest jinja2
```

**Only `httpx`, `pytest` and `jinja2`.** `frappe`, `requests` and `googleapiclient` are **not**
installed. `ci.yml:334-336` documents a suite that needed *"a fake googleapiclient (the real one is
NOT installed on this runner, and both `finance_calendar.py` and `drive_sync.py` import HttpError
at module scope)"*. **A bench-free chat suite that imports the relay must stub `requests`**, exactly
as `tests/test_triton_personas.py:68-71` already does.

#### A.6.1 THE LOAD-BEARING SPLIT, verbatim

> ```
> # The QuickBooks Online suite is bench-free too but is plain pytest functions
> # (the `monkeypatch` fixture), which `python -m unittest` silently cannot
> # collect — it ran nowhere in CI and broke unnoticed for weeks. It gets its
> # own pytest step below; any future pytest-style bench-free suite belongs on
> # that step, not the unittest module list.
> ```
> — `ci.yml:128-132`, restated at `README.md:227`, `tests/README.md:19`, `CLAUDE.md`, and
> `decisions/adr/0005-bench-free-tests-in-ci.md:39-43`

**(A) A new bench-free *pytest* suite gets its own new step, naming exactly one file.** There are
**nine** such steps and **every one names one file**. Quoted in full, in file order:

```yaml
      - name: QuickBooks Online sync (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_quickbooks_online.py -q
      - name: Stripe Payments (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_stripe_payments.py -q
      - name: Lead/Opportunity attribution (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_lead_attribution.py -q
      - name: Fountain move intake (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_fountain_move.py -q
      - name: Field photo gate + payroll export (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_field_systems.py -q
      - name: Account hygiene + reconciliation (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_account_hygiene.py -q
      - name: Marketing spend + value stream dashboard (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_marketing_spend.py -q
      - name: Gantt widget read API (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_gantt_api.py -q
      - name: Procurement quantity/status math (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_procurement_quantities.py -q
```
— `ci.yml:498-501, 517-520, 528-529, 537-538, 545-546, 549-550, 556-557`
(enumerated in `notes_close_repo.md` §4.4)

**(B) A new bench-free *unittest* suite that installs its own `frappe` stub gets its own step, and
uses a module dotted path, not a file path** — the opposite convention from the pytest steps:

```yaml
      # Own step: installs its own frappe stub in setUpModule, so it must not
      # share a process with the other bench-free suites.
      - name: Administrator login alerts (bench-free)
        run: python -m unittest erpnext_enhancements.tests.test_security_alerts -v
```
— `ci.yml:244-247`

The cross-talk mechanism is spelled out, and it is why "own step" is the default:

> ```
> # THREE separate steps, not one `unittest a b c`. Each installs its own
> # frappe stub in setUpModule, and running them in one process cross-talks:
> # the roles suite imports the real erpnext_enhancements.training.roles, and
> # `from ... import roles` in training.assignment then resolves the attribute
> # already set on the package rather than the assignment suite's stub. That
> # combination fails; individually all three pass. Verified 2026-08-01.
> ```
> — `ci.yml:348-353`

**(C) The one case where appending to an existing multi-module step is correct** is a suite that
shares the stub environment installed by `test_assistant_tools_schema` — which is precisely where
the chat MCP-denylist test belongs (§B.10):

```yaml
      - name: AI gate + assistant-tool contract + integrations health + device mgmt
        run: >-
          python -m unittest
          erpnext_enhancements.tests.test_assistant_tools_schema
          erpnext_enhancements.tests.test_ai_gate_unit
          erpnext_enhancements.tests.test_integrations_health
          erpnext_enhancements.tests.test_device_management
          erpnext_enhancements.tests.test_mdm_integration
          erpnext_enhancements.tests.test_drive_match -v
```
— `ci.yml:146-154`, justified at `ci.yml:120-126`

**(D) A JavaScript suite is plain `node`, deliberately not a framework** — `ci.yml:568-575`,
rationale at `:558-560`. **(E) A `scripts/` guard is a plain `python scripts/x.py` step** —
`ci.yml:505-513`.

#### A.6.2 Six existing steps encode bug classes chat will meet

- **`ci.yml:459-460` Training bootstrap wire** — *"the bootstrap returns `assigned`/`library` and
  the player read `courses`/`catalog`, so /training told every learner nothing was assigned to
  them, always. A missing key in JS is undefined, and `undefined || []` renders perfectly — there
  is nothing at runtime that could notice."*
- **`ci.yml:461-470` Training boundary contract** — *"enumerates every whitelisted reply's keys …
  and every key the player reads off a reply, then fails on anything present on one side only."*
  **This is the generalisation that closes the whole class, and the chat client/server envelope has
  exactly this shape.**
- **`ci.yml:392-399` Training heartbeat wire format** — *"Drift there is SILENT AND TOTAL"*.
- **`ci.yml:447-453` Training player CSS contract** — *"130 of the 138 classes the player emits had
  no rule"*.
- **`ci.yml:471-477` Kiosk service worker scope** — see §A.10.2.
- **`ci.yml:214-222` hooks.py integrity** — *"Python resolves a repeated key by keeping the LAST
  and silently discarding the earlier value … Two duplicated doc_events keys cost six live
  handlers."*

#### A.6.3 The coverage gap, measured — and one live instance of the documented bug

There are **103 test files** in `erpnext_enhancements/tests/` plus one at the repo root.
**70 run in CI; 33 do not** (`notes_ee_audit.md` §6.9). Two matter here:

- **`tests/test_collab.py` — the realtime relay's only test suite — does not run in CI.** It is
  `FrappeTestCase`-based and needs a bench. **The existing realtime permission model therefore has
  no CI coverage, and anything chat builds on `api/collab.py` inherits that gap.**
- **`tests/test_triton_personas.py` is bench-free pytest and is in NO CI step.** Confirmed three
  times: `grep -c "test_triton_personas" .github/workflows/ci.yml` → **0**
  (`notes_gap_report.md` §0-11, `notes_close_repo.md` §4.4). It is 282 lines, fourteen module-level
  `def test_*(monkeypatch)` functions, and it contains
  `test_stream_query_payload_carries_persona_key` (`:266`),
  `test_stream_query_forwards_empty_persona_key_as_the_default_voice` (`:273`) and
  `test_stream_query_omits_persona_key_when_not_supplied` (`:280`) — **the only automated
  assertions about `stream_query` anywhere in either repo, and they have never executed.**

> **Named recommendation (TEST-0).** The fix is literally two lines appended to the `unit-tests`
> job:
> ```yaml
>       - name: Triton persona proxy + SSE relay (bench-free pytest suite)
>         run: python -m pytest erpnext_enhancements/tests/test_triton_personas.py -q
> ```
> **Phase 0 writes no code, so this ADR does not do it. It should be the first commit of the chat
> work — before Phase 1, not inside Phase 5** — because every "streaming must survive" row in
> Appendix A is otherwise defended by a file that has never run.

#### A.6.4 The removed integration job, and what it means for chat tests

`ci.yml:609-623` is a comment block, not a job:

> ```
> # NOTE: A Frappe integration-test job (spinning up a real bench with ERPNext
> # + payments and running `bench run-tests --app erpnext_enhancements`) was
> # removed. On the version-16 toolchain it never reached our own assertions —
> # it kept aborting in Frappe's test-record auto-generation, which walks the
> # entire ERPNext doctype dependency graph and tripped over environment gaps
> # one after another … We rely on the standalone `unit-tests` job above instead.
> ```

**Consequence for chat: every test that must run in CI has to be bench-free.** Anything requiring a
bench is a manual acceptance step, and the ADR must say which is which. The denylist's *attachment*
canary (`test_ai_gating_integration.test_gate_marker_present`) is bench-only — a residual risk
§B.10 states rather than hides.

#### A.6.5 `version-sync`, quoted, because every chat PR meets it

```yaml
version-sync:
  name: Version sync (__init__.py == package.json)
  …
      - name: Compare versions
        run: |
          PY=$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' erpnext_enhancements/__init__.py)
          PKG=$(sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p' package.json | head -1)
          …
          if [ -z "$PY" ] || [ "$PKG" != "$PY" ]; then
            echo "::error::Version drift -- __init__.py is '$PY' but package.json is '$PKG'. …"
            exit 1
          fi
```
— `ci.yml:593-607`, rationale at `:583-592` (*"release.yml refuses to tag when they disagree … but
it only runs after a merge to main — by then the drift has already silently blocked the release
(this bit v0.9.0)"*)

`release.yml` is the downstream half: triggered on `push: branches:[main] paths:
["erpnext_enhancements/__init__.py"]` (`release.yml:23-27`), re-checks parity (`:60-67`), skips if
the tag exists (`:69-79`), extracts the `## [VERSION]` CHANGELOG section as release notes by awk
(`:81-99`), and `gh release create`s (`:101-113`).

---

### A.7 Indentation conventions per directory, and the ruff configuration

The rule, from `decisions/adr/0007-tolerate-mixed-indentation.md:29-31`:

> ```
> Tolerate the inconsistency. **Match the file you are editing** — never normalise a file you
> are only passing through.
> ```

The measured split — this table is the one a chat PR needs open beside it:

| Area | Indentation | Evidence |
|---|---|---|
| Repo default / Frappe convention / `ruff format` | **tabs** | `.editorconfig:11-14`; `pyproject.toml:87` `indent-style = "tab"` |
| JSON | 2 spaces, no final newline | `.editorconfig:16-19` |
| **`api/` — most files** | **4 spaces** | `api/README.md:7` |
| **`api/analytics.py`, `api/collab.py`, `api/comments.py`, `api/user_drafts.py`, `api/integrations_health.py`** | **tabs** | `api/README.md:7`, verbatim: *"⚠ **Mixed indentation:** most files in this folder use 4-space indentation, but `analytics.py`, `collab.py`, `comments.py`, `user_drafts.py`, and `integrations_health.py` use **tabs**. Match the file you are editing."* |
| `water_engineering/engine/` | 4 spaces throughout | `decisions/adr/0007-…:15-17` |
| `triton_chat.py`, `utils/triton_sync.py`, `ai_governance/doctype/triton_settings/triton_settings.py`, `api/telephony.py`, `api/call_intelligence.py`, `api/training.py`, `scripts/check_import_dirs.py`, `tests/test_doctype_modules.py` | **4 spaces** | observed (`notes_ee_audit.md` §7.2) |
| `hooks.py`, `boot.py`, `api/collab.py`, `google_drive/drive_utils.py`, `utils/deploy.py`, `www/wall.py`, `www/training.py`, `scripts/check_www_controllers.py`, `accounting_intake/triton_client.py`, `tests/test_triton_personas.py` | **tabs** | observed |

> **The practical rule for chat.** A *new* chat file uses **tabs** (the configured `ruff format`
> style). But chat will touch two 4-space files — `triton_chat.py` (Phase 5) and
> `utils/triton_sync.py` (the exclusion, CHAT-EXCL-1) — and those stay 4-space. The file a chat
> realtime relay would most naturally extend, `api/collab.py`, is **tabs** while the rest of `api/`
> is spaces. Getting this wrong produces a diff nobody can review.

**ruff configuration — `pyproject.toml:43-88`** (condensed: the real `select`/`ignore` arrays are one
entry per line, each with its own trailing comment; every value below is verbatim):

```toml
[tool.ruff]
line-length = 110
target-version = "py310"

[tool.ruff.lint]
select = ["F", "E", "W", "I", "UP", "B", "RUF"]
ignore = [
    "B017", "B018", "B023", "B904",
    "E101",  # indentation contains mixed spaces and tabs
    "E402", "E501", "E741",
    "F401", "F403", "F405", "F722",
    "W191",  # indentation contains tabs
    "RUF001", "RUF002", "RUF003",
]
typing-modules = ["frappe.types.DF"]

[tool.ruff.format]
quote-style = "double"
indent-style = "tab"
docstring-code-format = true
```

**`E101` and `W191` are explicitly ignored — that is how the mixed tree passes lint at all.**
`ruff check` is advisory (`continue-on-error: true`, `ci.yml:80`) because of a documented backlog of
73 non-auto-fixable findings (`ci.yml:50-69`), and a repo-wide `--fix` or `format` is **forbidden**
as a drive-by: *"It will bury the actual change"* (`decisions/adr/0007-…:45`, repeated in
`CLAUDE.md`).

pre-commit runs `trailing-whitespace`, `check-merge-conflict`, `check-ast`, `check-json`,
`check-toml`, `check-yaml`, `debug-statements`, ruff's import sorter (`--select=I --fix`), ruff
lint, ruff-format, prettier on `[javascript, vue, scss]`, and eslint `--quiet` on `[javascript]` —
`.pre-commit-config.yaml:8-68`, ruff pinned `v0.8.1` in both pre-commit and CI.

Three more conventions a chat PR is reviewed against:

- **Public asset namespacing is `public/{js,css}/<module>/`** — `README.md:243`,
  `decisions/adr/0008-…:41-42`. Chat assets go in `public/js/chat/` and `public/css/chat/`.
- **`hooks.py` and the owning module's README are kept in sync on every customization** —
  `README.md:266`, `CLAUDE.md`, `hooks.py:17-18`. **A new `chat/README.md` is part of Phase 1's
  definition of done, not a nicety.**
- **App-level import directories must contain only JSON**, enforced at `ci.yml:512-513` by
  `scripts/check_import_dirs.py`. The fallback list at `:38-45` includes `doctype`, `page`,
  `print_format`, `workspace`, `workspace_sidebar` and others. **A chat feature must not drop a
  README into any directory Frappe treats as importable.**

**The version + changelog ritual is mandatory on every change** (`CLAUDE.md`): bump
`erpnext_enhancements/__init__.py`, bump `package.json`, add a dated Keep-a-Changelog section.
`CHANGELOG.md` is 10,384 lines and is the app's real history; the `/release-prep` skill walks it.

---

### A.8 The fixtures model, and what it means for Custom Fields that chat adds to standard DocTypes

**16 files, 1,049 records.** The two that matter: `custom_field.json` — **513** records, 709,338
bytes, declared at `hooks.py:873-907`; `property_setter.json` — **409** records, 231,530 bytes,
declared at `hooks.py:908-920`.

**How they are applied**, `fixtures/README.md:68-72`:

> ```
> Within `bench migrate`: one-shot patches → **fixture sync (these files)** →
> `sync_customizations` (`<module>/custom/*.json` — none exist in this app anymore) →
> `after_migrate` hooks. Things that run *after* fixtures can override them, so the
> app keeps those channels disjoint from fixture-owned records
> ```

**Import order within fixture sync is alphabetical by filename**, and the consequence is spelled
out in `hooks.py`:

> ```
> # NOTE: this list's order governs *export* only. Fixtures IMPORT in alphabetical
> # filename order (frappe/utils/fixtures.py sorts the directory), so
> # custom_docperm.json lands before role.json and role.json before
> # role_profile.json. Any Role a fixture references must therefore be seeded by a
> # post_model_sync patch, which runs before fixture sync — not added here and
> # hoped for.
> ```
> — `hooks.py:1050-1055`

> **Direct consequence for chat: a new Role (a "Chat User" or a "Chat Auditor") must be seeded by a
> patch, not by a fixture entry**, exactly as `PO Approver`/`PO Creator` are (`hooks.py:1047-1049`
> names `patches/seed_po_approver_role.py` and `patches/seed_po_creator_role.py`). And per
> `hooks.py:1076-1080`, a user who holds **any** Role Profile has `roles` regenerated from the union
> of their profiles on every save (`User.populate_role_profile_roles`), so **a direct role grant
> does not survive** — a chat role reaches a profiled user only through a profile. This matches the
> standing memory note *"ERPNext user-role edits are Desk-only"*.

**THE TWO-STEP DELETION RULE**, `fixtures/README.md:25-27`, verbatim:

> ```
> **Deletions need two steps:** removing a record from the JSON stops managing it but
> does NOT delete it from the database — fixture sync only creates/updates. Also write a
> one-shot patch (`frappe.delete_doc("Custom Field", name)`) for the deletion.
> ```

**What this means concretely for chat.** The obvious candidates are a Google Chat user id on
`User`, a chat preference set on `User` or `Employee`, and a `custom_chat_space_id`-shaped field on
`Project` / `Customer` (decision #10). For each:

1. The field must be created on the site and **re-exported** into `custom_field.json`, or
   hand-authored following the spec at `fixtures/README.md:37-46` (sorted by `name`; `indent=1`;
   sorted keys; trailing newline; LF; `modified`/`modified_by`/`creation`/`owner`/`idx`/tags/
   comments/assignments stripped; **all other keys verbatim including nulls**).
2. It is re-applied on **every fixture-touching deploy** to **both** test and prod — there is one
   pipeline (`PLAN.md:296`: *"merge to `main` → Frappe Cloud deploys the same code to test and
   prod"*). **There is no way to ship a Custom Field to test only**; differentiation is runtime
   feature flags (`PLAN.md:297`).
3. **Backing it out later requires a patch**, and until that patch runs the column and its data
   remain in the database. For a field carrying a Google Chat user id, that is a **data-retention
   question**, not hygiene.
4. Deploys that change these files re-import the whole set and make `bench migrate` *"run
   noticeably longer"* (`fixtures/README.md:12-13`).

**Current state of the two target DocTypes:**

- **`User` has ZERO app-owned Custom Fields.** The only `User-*` records referenced anywhere are
  four *excluded* ones at `hooks.py:896-900` (`User-hide_my_private_information_from_others`,
  `User-user_category`, `User-verify_terms` from `lms`; `User-assistant_enabled` from
  `frappe_assistant_core`), filtered **out** because they belong to other apps.
  > **This is a clean field and also a trap.** Adding `User-custom_chat_*` is unprecedented here,
  > and the exclusion list at `hooks.py:894-905` exists precisely because other apps' `User` fields
  > keep appearing in exports. A new `User` field must be added to the fixture **and** the exclusion
  > filter must not accidentally swallow it.
- **`Employee` has 7** app-owned Custom Fields (comments tab + HTML, QuickBooks user id, default
  vehicle warehouse, three payroll fields), plus code-owned `is_system_generated = 1` fields created
  by `device_management.setup.create_device_employee_fields` (`hooks.py:786`).

**The split-file mechanism, and when chat needs it.** `custom_field_hrms.json` (3 records) exists
because of this failure:

> ```
> # hrms-app doctypes are NOT installed on prod/test. One record targeting a
> # missing doctype raises DoesNotExistError, and sync_fixtures then skips the
> # ENTIRE custom_field.json silently — which is exactly what had been happening
> # on every prod deploy (discovered 2026-07-14 via the WI-065 label changes not
> # landing).
> ```
> — `hooks.py:876-882`

**If a chat field targets a DocType that might be absent on some bench, it goes in its own file.**
And any file added to `fixtures/` that is **not** named in the `fixtures` hook is dead weight —
`hooks.py:1025-1026` records the Web Page version of that trap: *"the page simply 404s with no error
anywhere."*

**Fixture-owned versus code-owned — the house rule and the chat decision.**
`fixtures/README.md:74-84`: `setup/custom_fields.py` (`after_migrate`, `hooks.py:776`) creates
fields with `is_system_generated = 1`, insert-only for existing fields; one-shot patches own further
ones. **Fixtures are for manual/UI-authored customizations; `setup/` is for code-provisioned ones.**
Therefore: **a chat panel HTML field on a DocType is code-provisioned** (follow
`accounting_intake/setup.py::create_supplier_drive_field`, `hooks.py:788`); **a chat preference
field a user edits is fixture-shaped.**

One cosmetic hazard worth pre-empting, `fixtures/README.md:103-105`: *"Custom Fields import in file
order (alphabetical by name), so a field whose `insert_after` points at a field created later in the
file may land at the end of the form."*

---

### A.9 The defensive-hook convention, with cited examples

The rule is stated in three places. `CLAUDE.md`:

> ```
> - **Defensive hooks are load-bearing.** `doc_events` fire during ERPNext's own test
>   bootstrap, before this app's custom fields exist. That is why custom-field reads use
>   `getattr(obj, "field", None) or ""` and column-filtered queries guard with
>   `frappe.db.has_column(...)`. Preserve those guards; removing one turns a fresh-DB install
>   into a crash.
> ```

and inline on the `Lead` block:

> ```
> # Lead attribution. Lead had no doc_events block at all before v1.241.0.
> # Both handlers are inert unless the attribution Custom Fields exist on the
> # bench (they check frappe.db.has_column), which is what keeps them safe
> # during erpnext's own test bootstrap.
> ```
> — `hooks.py:415-418`

**The mechanism.** `bench run-tests` makes Frappe create test records for every DocType in the
ERPNext dependency graph (`decisions/adr/0005-bench-free-tests-in-ci.md:10-17`; restated at
`ci.yml:609-618`). Creating a test `Lead` fires this app's handlers. On a bench where fixtures have
not been applied — a fresh CI database, a partially-applied migrate — the Custom Fields those
handlers read **do not exist yet**. A bare attribute read raises `AttributeError`; a filtered query
raises a SQL error; either aborts the whole bootstrap. **The same window exists during a migrate**,
because patches run before fixture sync (`fixtures/README.md:70`).

**The canonical column guard — check the COLUMN, not the meta** —
`crm_enhancements/attribution.py:145-156`:

```python
def _has_attribution_fields(doctype):
	"""True if this bench has the attribution Custom Fields for ``doctype``.

	``doc_events`` fire during erpnext's test bootstrap, before fixtures are
	applied. Checking the column rather than the meta because a Custom Field row
	can exist before the column does during a partially-applied migrate.
	"""
	try:
		return frappe.db.has_column(doctype, "custom_utm_source")
	except Exception:
		return False
```

**The canonical accessor** — `crm_enhancements/attribution.py:158-162`:

```python
def _get(doc, fieldname):
    """Read a custom field that may not exist. Returns "" rather than None so
    callers can treat blank and missing identically."""
    return (getattr(doc, fieldname, None) or "") if doc else ""
```

**Real `getattr(..., None) or ""` sites**, cited: `sync_contact.py:517-520` (four in a row),
`sync_contact.py:481,487,494` (comparison form), `crm_enhancements/attribution.py:314,368`,
`crm_enhancements/data_quality.py:144`, `api/training.py:1510-1511`,
`workforce/photo_gate.py:88-94` (child-table form), `api/telephony.py:857` (chained across three
config sources), `document_merge.py:88`, `google_drive/drive_sync.py:251`,
`water_engineering/issues.py:193,200,204,218,567,636,637`.

**Real `frappe.db.has_column(...)` guards — 93 call sites outside `tests/`**, in three shapes:
*early return* (`accounting_intake/filing.py:135`, `api/comments.py:46`,
`google_drive/drive_sync.py:317,587,642`, `training/assignment.py:261`,
`workforce/photo_gate.py:156`, `sync_contact.py:527`, `document_merge.py:292,327`); *column
selection* (`api/marketing_dashboard.py:41`, `kpi_dashboards/snapshots.py:337,786`,
`crm_enhancements/attribution.py:349`, `api/pickup_routing.py:174` with a perf note at `:167` —
*"Resolved once per request rather than once per address"*); and *patches* — every backfill guards
first (18 patches cited at `notes_ee_audit.md` §9.4).

**THE TRAP: `has_column` on a missing TABLE raises.**
`travel_management/doctype/travel_trip/travel_trip.py:63-75`:

```python
def _has_travel_backlink(doctype):
	"""True only when ``doctype``'s table exists *and* carries the fixture-managed
	``custom_travel_trip`` back-link.

	HRMS is optional, so Expense Claim / Employee Advance / Vehicle Log may be
	absent entirely. ``has_column`` raises ``TableMissingError`` for a missing
	table (it returns False only for a missing column on a table that exists), so
	gate on ``table_exists`` first — otherwise rollups/cleanup crash on a
	no-HRMS site."""
	return frappe.db.table_exists(doctype) and frappe.db.has_column(
		doctype, "custom_travel_trip"
	)
```

**Any chat guard against an optional DocType must be `table_exists(...) and has_column(...)`.**

And the module-level statement of the whole convention, `workforce/photo_gate.py:41-46`:

> ```
> ## Defensive throughout
>
> Every field this module reads may be absent on a bench that has not migrated —
> the Job Interval photo fields ship in the same release as the code. Reads go
> through ``getattr(..., None) or default`` and ``frappe.db.has_column``, the same
> convention the rest of the app uses for custom-field access.
> ```

> **Chat is exactly this situation.** Chat ships Custom Fields *in the same release as the code that
> reads them*. Between the code deploying and the fixture applying — and during ERPNext's test
> bootstrap, and during a partially-applied migrate — **every read must be defended**. And per
> `ci.yml:83-104`, the `except Exception: log_error` wrappers that catch the fallout convert a
> `NameError` into silence, which is why F821 is a hard gate and why chat handlers must be written
> under it.

---

### A.10 `www/`, the hyphen trap, and what serving an SPA from `www/` implies

**11 controllers + 11 templates + 2 service workers + 1 manifest.** The naming pattern is consistent
and load-bearing: `contract-sign.html` ↔ `contract_sign.py`; `fountain-move.html` ↔
`fountain_move.py`; `pay-card.html` ↔ `pay_card.py`; `stripe-return.html` ↔ `stripe_return.py`.
**The route keeps the hyphen; the controller does not.**

**The hyphen trap, in full** — `www/README.md:13-38`:

> ```
> ## Controller filenames: hyphens are silently fatal
>
> Frappe locates a page's controller from the **template's** basename, replacing
> hyphens with underscores (`frappe/website/page_renderers/template_page.py`):
>
> ```
> www/stripe-return.html   ->  frappe imports  www/stripe_return.py
> ```
>
> A controller named `stripe-return.py` is therefore **never imported**, and its
> `get_context()` never runs. Nothing raises. The template still renders — just with
> every context variable undefined, silently taking whichever branch that implies.
>
> **This is not hypothetical: `www/stripe-return.py` never executed until v1.159.10.**
> Every Stripe Checkout return, including cancellations, rendered "Thank you! Your
> payment is being processed", because `outcome` was undefined so `outcome == "cancel"`
> was false. A customer who deliberately cancelled was told their payment was going
> through.
>
> The **route** comes from the template, so a hyphenated URL is perfectly fine — the
> public path stayed `/stripe-return` through the fix. Only the `.py` needs
> underscores. An earlier revision of this README claimed the opposite (that a
> hyphenated route required a hyphenated filename); that was wrong, and it is what
> let the bug survive review.
> ```

CI guards it — `scripts/check_www_controllers.py:32-61` fails on any `*.py` in `www/` whose stem
contains a hyphen, run at `ci.yml:502-506`. **The guard is filename-only**: it does not verify that
a `.html` has a matching `.py`, nor the reverse.

> **Named Phase 3 rule (WWW-1): the chat controller is `www/chat.py`, underscored, and a filesystem
> test asserts the template/controller pair exists.** The existing guard catches the hyphen; nothing
> catches a missing controller.

#### A.10.1 What serving an SPA from `www/` implies, given zero `website_route_rules`

**The routing question is now CLOSED, and in our favour.** `notes_close_frappe.md` §2 read
`frappe/website/path_resolver.py` from v16 source and established:

- **A `<path:…>` catch-all is expressible**, and **both frappe and ERPNext ship one today** —
  ERPNext's `/orders/<path:name>` → `templates/pages/order.py` is a working in-production precedent
  (`notes_close_frappe.md` §2.2).
- The concrete rule for us is **one entry**: `{"from_route": "/chat/<path:chat_path>", "to_route":
  "chat"}`. `/chat` itself needs no rule (the flat `www/chat.html` serves it), and the remaining path
  arrives as **`frappe.form_dict.chat_path`** (`notes_close_frappe.md` §2.3-2.4).
- **Therefore deep links survive a hard refresh**, which is a named Phase 0 §7 acceptance criterion
  and was `notes_gap_report.md` §E item 7 / B1 in `notes_register_reconciled.md`.
  `notes_ee_audit.md` §13 VERIFY 14 is **CLOSED**.

That closure changes the shape of the answer but not the fact that **this app has never had a
`website_route_rules` entry** (`hooks.py` has no such key; a repo-wide grep returns nothing;
confirmed by `notes_gap_report.md` §0-12). **Chat introduces the app's first one**, and that is a
reviewable, deliberate act rather than a routine addition.

**The `/training` shell is the pattern to copy**, `www/training.py:20-25`:

> ```
> * **It is a shell and nothing more.** Every learner-visible fact comes from the
>   one bootstrap call below, and every subsequent interaction goes back through
>   ``api.training`` over ``fetch``. The page must run for *Website Users* with
>   ``desk_access = 0`` (customer contacts holding Training Learner), so nothing
>   here — and nothing in the player scripts — may rely on the desk bundle or a
>   ``frappe.*`` global being present in the browser.
> ```

and the transport seam, `www/training.html:12-25`:

> ```
>     * build the *fetch transport* and construct the player with it.
>
>   That last one is the seam, and it is deliberate. TR.Player takes
>   (rootEl, boot, transport) and knows nothing about window.TRAINING_BOOT or
>   about living in a www/ page …
> ```
> ```
>   No frappe.call and no frappe.* globals anywhere in this page or in the player:
>   learners include customer Website Users with desk_access = 0, who never get the
>   desk bundle. Plain fetch() with an X-Frappe-CSRF-Token header.
> ```

**Calibration for the size of that job:** `public/js/training/{blocks,video,quiz,player}.js` =
476 + 1502 + 858 + 1276 = **4,112 lines of vanilla JS**, plus `public/css/training/player.css` =
**2,469 lines**, loaded in a fixed order with `?v={{ deploy_version }}` on every URL.

**The chrome-removal scar**, which any chat page will meet — `www/training.html:50-60`:

> ```
>   /* Hide the website chrome. `footer` is deliberately qualified: player.js builds
>      the sticky action bar as <footer class="tr-bottom"> — the element holding
>      "Start the quiz", "Finish this lesson", the resume button and the gate
>      reasons — and a bare `footer { display: none !important }` hid it. The
>      button rendered, and no learner could ever see it or press it. Nothing
>      erred; the one control that advances a course was simply invisible. */
> ```

**Boot payload convention:** each shell injects three uppercase globals prefixed by the page name
(`KIOSK_BOOT`/`KIOSK_CSRF`/`KIOSK_BUILD`; `WALL_BOOT`/`WALL_BUILD`;
`TRAINING_BOOT`/`TRAINING_CSRF`/`TRAINING_BUILD`). **A chat page follows with `CHAT_BOOT` /
`CHAT_CSRF` / `CHAT_BUILD`.**

**Role gating precedent** — `www/wall.py:40-48` (guest → `/login?redirect-to=/wall`, then a
`STAFF_ROLES` intersection check, with the dedicated low-privilege-user note at `:11-13`), and the
**two-belt deploy pickup** at `www/README.md:54`: the SW registered as `/wall-sw.js?v=<deploy token>`
re-checked every 60 s, **plus** every data refresh carrying the server's `deploy_version` so a
mismatch reloads even if the SW never installed. **A long-lived chat tab needs both belts.**

#### A.10.2 The service-worker constraint, stated precisely

**Two service workers exist, both registered at ROOT scope**: `www/kiosk-sw.js` registered as
`/kiosk-sw.js?v=<token>` by `public/js/kiosk/app.js:1003-1012`, and `www/wall-sw.js` as
`/wall-sw.js?v=…` by `public/js/wall/app.js:394-409`. Both say so in their own headers
(`kiosk-sw.js:2`, `wall-sw.js:2`).

**Correction to `notes_infra.md:737-741`, from `notes_close_repo.md` §4.2:** its claim that *"neither
covers `/app/*`"* is imprecise — **their scope does cover `/app/*`**, which is exactly why the kiosk
worker carries this and why there is a CI test for it:

```js
// Exactly the paths above, for the fetch handler to test membership against.
// This worker is registered at ROOT SCOPE — it sees every request on the origin,
// including every other page's JavaScript — so what it chooses to answer has to
// be an explicit list rather than a prefix.
const PRECACHE_PATHS = new Set(PRECACHE);
```
— `www/kiosk-sw.js:59-64`, asserted at `ci.yml:471-477`, whose own comment records the incident:
*"It used to serve the whole app's assets cache-first with ignoreSearch, which froze JavaScript
across the site for any browser that had ever opened /kiosk — found with a training player four
releases stale that no `?v=` could reach."*

The accurate statement is: **neither worker is ever registered from a desk page, and neither
implements `push`, `notificationclick` or `pushsubscriptionchange`** — the complete
`addEventListener` set is `install`/`activate`/`fetch`/`message`/`sync` for kiosk and
`install`/`activate`/`fetch` for wall. **Web Push is entirely new surface**, consistent with
`notes_infra.md:990-1005` finding `pywebpush`, `py_vapid`, `ecdsa`, `http_ece` and `firebase_admin`
all absent from the production bench.

> **The registration-collision risk, which no earlier note raised** (`notes_close_repo.md` §4.2 item
> 6): `register()` is keyed by **scope**. Three different scripts all registering at scope `/` may be
> competing for one registration slot per origin, and a worker is only replaced when *its own* script
> URL changes — i.e. when someone opens *its* page.
> `VERIFY: whether two different scriptURLs registered at the same scope replace one another's
> registration or coexist — read the Service Workers spec Register/Update algorithm at
> https://w3c.github.io/ServiceWorker/#navigator-service-worker-register, or open /kiosk then a desk
> page and inspect navigator.serviceWorker.getRegistrations() — blocks: whether a desk push worker
> can be registered at root scope at all, or must be served from a subpath with an explicit narrower
> {scope: "/app/"} and a Service-Worker-Allowed header.`

---

### A.11 The realtime and background-job substrate, and the site-room footgun

#### A.11.1 Every `frappe.publish_realtime` call site — and the targeting mode *is* the security model

**12 call sites across 9 files** (`notes_ee_audit.md` §11.1), in three groups:

**(A) Document-room scoped** — membership permission-checked by Frappe's socket.io:
`api/collab.py:155-169` (`collab_field_update`) and `api/collab.py:209-224` (`collab_focus`).

**(B) User-scoped** — the closest thing to a DM this app has:
`assistant_tools/_gate.py:470-473` (`ai_pending_action`, paired with a `Notification Log` insert at
`:459-468`) · `crm_enhancements/project_prompt.py:56-64` (**`after_commit=True`**) ·
`crm_enhancements/api.py:461-465` · `device_management/tasks.py:104-108` (paired with a
`Notification Log` at `:90-103`) · `document_merge.py:459-463` and `:466-470` ·
`api/maintenance_workflow.py:69-72` and `:78-81` (**reusing Frappe's built-in `msgprint` event to
raise a toast from a background job**).

**(C) Unscoped broadcast** — every connected session receives it: `api/telephony.py:359`
(`triton_incoming_call`, carrying resolved caller identity and CRM context) ·
`crm_enhancements/page/sales_pipeline/sales_pipeline.py:296` ·
`project_enhancements/page/project_dashboard/project_dashboard.py:1397-1401`.

#### A.11.2 The site-room footgun, as a NAMED Phase 1 lint rule

`notes_gap_report.md` §0-3 fetched v16 `frappe/realtime.py` and established the real precedence.
The signature:

```python
def publish_realtime(
	event: str | None = None,
	message: dict | None = None,
	room: str | None = None,
	user: str | None = None,
	doctype: str | None = None,
	docname: str | None = None,
	task_id: str | None = None,
	after_commit: bool = False,
):
```

and the room resolution, in order:

```python
if event == "msgprint" and not user:
	user = frappe.session.user
elif event == "list_update":
	doctype = doctype or message.get("doctype")
	room = get_doctype_room(doctype)
elif event == "docinfo_update":
	room = get_doc_room(doctype, docname)
```
then
```python
if not room:
	if task_id:
		after_commit = False
		…
		room = get_task_progress_room(task_id)
	elif user:
		room = get_user_room(user)
	elif doctype and docname:
		room = get_doc_room(doctype, docname)
	else:
		room = get_site_room()
```

> **LINT-RT-1 (Phase 1, enforced by a bench-free source-level test).** *Every `publish_realtime`
> call in the chat module passes an explicit targeting argument — `room=`, or `user=`, or both
> `doctype=` and `docname=`.* **The final fallback is `get_site_room()`, a site-wide broadcast.** A
> chat event that forgets its targeting broadcasts message content to **every connected session on
> the site**. `notes_close_frappe.md` §5 adds that the rule must also defend against the `task_id`
> branch, which resolves a room *before* `user`/`doctype` are consulted. The test is a regex over
> `erpnext_enhancements/chat/**/*.py`, in the shape of the existing "every guest endpoint has a rate
> limiter" test at `tests/test_contract_esign.py:526-534`.
>
> **LINT-RT-2 (same test).** *No chat event may be named `list_update` or `docinfo_update`.* Those
> two names **overwrite an explicitly passed `room=`**, because the assignment happens before the
> `if not room:` guard.
>
> **LINT-RT-3 (Phase 1, stated not tested).** *Membership revocation requires a cooperative eviction
> push*, because room joins are permission-checked **once, at join time**
> (`notes_close_frappe.md` §1.5.3). The residual — a hostile client keeps the stream until it
> reconnects — is stated, not hidden.

The three existing unscoped broadcasts (§A.11.1 group C) are a **pre-existing finding**, not
something chat introduces; `triton_incoming_call` in particular ships resolved caller identity and
CRM context to every session. The ADR records them so a reviewer does not attribute them to chat.

#### A.11.3 The realtime security authority, which chat must be measured against

`api/collab.py:1-20`:

> ```
> Clients on a collab-enabled form … POST debounced field changes (and field-focus presence
> events) here; after a write-permission check each event is re-published to the document's
> realtime room (``doc:{doctype}/{docname}``), whose membership is itself permission-checked
> by Frappe's socket.io ``can_subscribe_doc``. Clients never emit realtime events to each
> other directly — this endpoint is the security authority for every broadcast.
> ```
> ```
> The relay never writes to the database: broadcast values are ephemeral and
> persistence only happens through normal document saves …
> ```

and its explicit non-throttle, which chat **cannot** reuse:

> ```
> No server-side throttle in v1: the client debounces 300ms per field (~3
> requests/sec/field while typing), focus events fire only on focus moves plus
> a 30s heartbeat, and each call is one permission check plus one Redis
> publish. v2 hardening, should abuse appear: a ``frappe.cache()`` token bucket
> keyed by ``(user, docname)``.
> ```
> — `api/collab.py:35-40`

**That last sentence is the house's own design for the component Phase 2 needs**, and
`notes_close_repo.md` §2.1 confirms it does not exist yet: **there is no token bucket anywhere in the
repo**, and the QuickBooks client — a rate-limited API — has **no** 429 handling, no backoff and no
throttle at all (`quickbooks_online/core/client.py`, read in full, 402 lines). The pieces to build
it from are `offsite_backup/drive.py:46-50,111-139` (the only good backoff, with a named
`RETRYABLE_STATUS = (429, 500, 502, 503, 504)`, but **no jitter and no `Retry-After`**) and
`crm_enhancements/fountain_move/intake.py:775-791` (`_bump_counter`, the only atomic Redis counter,
whose docstring carries three bugs the repo already paid for: raw redis-py needs
`frappe.cache.make_key`; atomicity is the point; **only the creator sets the TTL**).

**Presence** exists only in a limited form: `broadcast_focus` powers per-field "Jane is editing this
field" highlights with *"A 30s heartbeat + 75s receiver-side TTL"* making it self-healing
(`public/README.md:91`; constants at `public/js/collab/live_form_sync.js:71-72`), and document-level
"currently viewing" avatars are Frappe's built-in FormViewers, untouched (`public/README.md:92`).
**Frappe v16 has no live presence primitive at all** — `frappe/realtime.py` contains no presence,
online-user or last-seen function, and the only "active users" helper is a **72-hour aggregate
count** over `User.last_active` (`notes_gap_report.md` §0-4). **Every input to the chat presence
signal must be purpose-built**, and the 30 s heartbeat precedent inherits a five-second margin
against the GCLB's 30 s idle timeout (`notes_infra.md:792-901`).

Two further closures worth stating flat, both from `notes_close_frappe.md` §1: **doc-room joins ARE
permission-checked** (`realtime/handlers.js` `doc_subscribe` →
`/api/method/frappe.realtime.has_permission`), which is the entire basis for putting message content
on doc rooms; and **socket.io authenticates Website Users and even Guests**, so a customer-facing
chat surface is not blocked by realtime — with the corollary that **`user:Guest` is a shared room and
must never be addressed**. `notes_ee_audit.md` §13 VERIFY 13 is **CLOSED**.

#### A.11.4 `frappe.enqueue`, and why the queue is not a delivery guarantee

**66 `frappe.enqueue` call sites** in application code, **46 of them passing
`enqueue_after_commit=True`** (`notes_close_repo.md` §3.1). House conventions, all observable:

- **`queue="long"` for anything touching an external API or a batch.**
- **`enqueue_after_commit=True` whenever the job reads the row just written.** `hooks.py:529` names
  it as the reason a `User.on_update` sweep *"can never delay a login or a save."*
- **Pass a document *name*, never a document.**
- **`job_id=` + `deduplicate=True`** is the answer to double-enqueue —
  `crm_enhancements/fountain_move/photos.py:155-164`.
- **The whole enqueue body sits inside `try/except`** so a failure cannot block the write —
  `google_drive/drive_utils.py:29-46`.
- **The job does not inherit the enqueuing session's user** — `fountain_move/conversion.py:17`
  (*"**Why the job re-authenticates.** `frappe.enqueue` captures…"*) with
  `finally: frappe.set_user(previous_user)` at `photos.py:171`. **A relay job that impersonates the
  authoring human for DWD (`DECISIONS.md` D3) inherits this directly.**

**The counter-example, quoted so it is not copied** — `utils/triton_sync.py:62-70`:

```python
        # Offload the HTTP POST to a background job so the save stays fast and is
        # not coupled to Triton's availability.
        frappe.enqueue(
            'requests.post',
            url=TRITON_URL,
            json=payload,
            now=False,
            queue='default'
        )
```

A raw library call as the job target, with **no `enqueue_after_commit`, no retry, no logging of the
outcome and no record that it was attempted.**

**And the reason none of this is a guarantee** — `infra/cloudbuild-deploy.yaml:30-38`, read from
source:

```
          bench --site all migrate && \
            bench build && \
            redis-cli -p 13000 FLUSHDB && redis-cli -p 11000 FLUSHDB' && \
            sudo systemctl restart frappe-bench
```

Port 11000 is the **queue** Redis; 13000 is the cache. **Every RQ job queued-but-not-started at
deploy time is destroyed, silently, with no error and no dead-letter.** This is the standing memory
note *"Deploy FLUSHDB destroys queued jobs"*, now confirmed from the deploy script in this worktree,
and it is why `before_migrate` carries `clear_stale_role_profile_locks` (`hooks.py:764-772`).

> **Stated as `DECISIONS.md` D8 requires: the outbox sweeper, not the queue, is the delivery
> guarantee.** The house pattern already exists twice and chat copies it — §A.11.5. And a corollary
> for the token bucket: a flushed bucket key must read as *a fresh, full bucket*, never as *zero
> tokens*; a fail-closed bucket silently stops all outbound relay after every deploy, and the
> symptom (messages stop, nothing logs) is the worst kind (`notes_close_repo.md` §2.3.4).

#### A.11.5 The outbox pattern, which already exists twice

**Shape 1 — re-drive from a stored payload.** `google_drive/drive_sync.py:696-723`
(`retry_failed_syncs`, daily at `hooks.py:689-690`), copied and *saying so* at
`accounting_intake/channels.py:147-165` (*"Mirrors `drive_sync.retry_failed_syncs`"*). Six decisions
in thirty lines that Phase 2 keeps: `payload["method"]` must
`startswith("erpnext_enhancements.")` (a stored dotted path is an arbitrary-code sink);
`limit_page_length=200` (a sweeper must be bounded); consume the row (`status = "Skipped"`) so
tomorrow's pass does not re-drive it; per-row `try/except` with `log_error`;
**`update_modified=False`** on the bookkeeping write; `queue="long"`.

**Shape 2 — sweep a boolean column, no payload** —
`crm_enhancements/fountain_move/photos.py:201-214`. **This is the closer analogue for chat**, because
a `Chat Message` row already *is* the outbox: it carries `sync_state` and `sync_origin`
(`DECISIONS.md` D5) and needs no separate payload blob. No second table, no serialized method path,
no arbitrary-code sink.

> **Interaction with `DECISIONS.md` D6, and it is easy to get wrong.** The cache/digest watermark is
> `(max(seq), count(*), max(modified))`. A sweeper that marks rows with `update_modified=False`
> leaves the watermark undisturbed; **a sweeper that forgets invalidates every cached digest on every
> pass.** State it as a named invariant with its test.

**The outbox table shape**, `google_drive/doctype/drive_sync_log/drive_sync_log.json`: `hash`
autoname (matching the Raven lift in `DECISIONS.md` D2, and the opposite of the naming series Phase 0
§4.G forbids for messages), `action`, `status` (Success/Failed/Skipped/Stale),
`reference_doctype`/`reference_name`, remote-identity fields, `attempts` (Int), `error` (Small Text,
truncated to 1000 at write), `payload` (**Code**, JSON). Its writer never raises —
`google_drive/drive_sync.py:83-103`, *"Never raises — logging must not break the action being
logged"* — the same discipline as `_gate.py:341-396`.

**And the one thing the house does NOT have: a dead-letter.** Every existing retry loop simply stops
at `attempts >= MAX` and the row stays `Failed` with nobody notified. The closest thing is
`esign.tasks.digest_awaiting_signature` (weekly, `hooks.py:744`) — *"one summary of every agreement
still out for signature, so a link that quietly went nowhere is visible without anyone remembering to
look."* **Chat needs the equivalent**, because the entire point of decision #2's mirror is that both
sides agree, and a silently-undelivered message is the failure a user cannot see.

#### A.11.6 Existing `Notification Log` usage

**7 production insert sites**, each hand-building its dict: `assistant_tools/_gate.py:459-468`
(`for_user = action.requested_by`, `type="Alert"`) · `device_management/tasks.py:90-103` ·
`api/device_management.py:314-320` · `api/maintenance_renewal.py:157-163` ·
`api/telephony.py:1288-1296` and `:1298-1305` · `fleet_maintenance/status.py:231` ·
`kpi_dashboards/snapshots.py:1602-1618` (with the rationale *"A Notification Log rather than an
email: it lands in the bell menu of people …"*).

**There is no email-digest or Notification-Log abstraction to reuse.** A chat notification bridge
either becomes the first unification of seven live call sites — a refactor with its own risk — or
follows the same hand-built shape. **The ADR recommends the latter**: do not refactor the seven in
the same PR; `ci.yml:180-189` already pins their audience semantics.

Two more facts a chat notification must respect: `document_merge.py:70` treats
`("Notification Log", "document_type", "document_name")` as a reference table to repoint during a
merge, so **a chat message DocType referencing documents needs registering in
`document_merge.py:60-80`** or a merge orphans it; and the memory note *"Frappe Notification fixtures
need `enabled:1`"* — without it they import disabled and re-disable on every migrate, which CI now
asserts (`ci.yml:180-189`).

#### A.11.7 The remaining collision surface, ranked

1. **The wildcard `after_save` → `global_triton_sync`** (`hooks.py:567-569`) — one background HTTP
   POST per message save unless the chat module is excluded. **CHAT-EXCL-1.**
2. **A second floating desk widget** competing with the Triton FAB — resolved by decision #8.
3. **A third root-scope service worker** (§A.10.2).
4. **`ERPNext Enhancements Settings` is already 179 fields**, read via `get_cached_doc` on nearly
   every request path. A chat section is fine; a chat *hot path* reading it per message is not.
5. **CDN dependencies.** The Triton widget already lazy-loads Mermaid from jsDelivr
   (`triton_widget.js:76`) and telephony pulls the Twilio SDK. Whether chat may add a third runtime
   CDN fetch is a policy question; `decisions/adr/0004-no-vendor-sdks.md` establishes strong
   scepticism about vendor SDKs on the **server**, and the client precedent is looser.
6. **`ignore_links_on_delete`** (§A.2.13) and **`document_merge.py:60-80`** (§A.11.6).
7. **Feature-flag discipline.** `feature_flags.py:50-54`: *"Default OFF (the staged-rollout
   contract): the gate code ships dormant and behaves byte-identically to before until the checkbox …
   is flipped — no deploy needed."* **A chat feature must ship dormant** (`PLAN.md:297`).
8. **One deploy pipeline, two sites** (`PLAN.md:296`), and a branch whose **name contains "main"**
   ran a real production deploy until `c70939a4` anchored the regex. **Branch naming for chat work
   matters.**
9. **House naming:** `Customer` is relabelled **"Accounts"** in the prod UI, and cancelled-state
   labels are spelled **"Canceled"**, one `l` (standing memory notes).
10. **The DB clock is UTC while Frappe writes `creation` in site-local time** — measured this session
    by `notes_register_reconciled.md` C7: `NOW() == UTC_TIMESTAMP()` while the newest `creation` is
    exactly six hours earlier. **Any SQL-side freshness check comparing a Frappe-written timestamp to
    `NOW()` is wrong by the site's UTC offset, permanently and silently.** This bears directly on
    D6's `max(modified)` watermark, on outbox-sweeper age checks, and on digest staleness alarms. Use
    `frappe.utils.now_datetime()` on both sides.

---

## B. Repository audit — `triton`

**Audited tree:** `C:/Users/nbbsh/Documents/GitHub/triton`, main working tree only. **Version at
audit: `0.42.3`** (`VERSION`, 6 bytes, no trailing newline — re-read this session). Paths below
beginning `backend/`, `frontend/`, `docs/`, `deploy/` are relative to the **triton** repo root;
paths beginning `erpnext_enhancements/` are relative to the ERPNext worktree.

> **Repo-hygiene warning for every later phase** (`notes_triton_audit.md` §11.7): the triton repo
> contains its own `.claude/worktrees/` with at least two full copies of itself
> (`bold-lehmann-1258e5`, `goofy-mendeleev-5d9bd1`). A naive recursive `grep -rn` from the repo root
> returns **triplicated hits with misleading paths**. Restrict searches to
> `backend/`, `frontend/`, `docs/`, `deploy/`.

### B.1 The exact chat endpoints, and which one the widget actually calls

**Router mounting** — `backend/app/api/v1/api.py`, three routers on the *same* `/assistant` prefix:

```python
api_router.include_router(assistant.router, prefix="/assistant", tags=["assistant"])            # api.py:8
api_router.include_router(streaming.router, prefix="/assistant", tags=["assistant-streaming"])  # api.py:9
api_router.include_router(research.router, prefix="/assistant", tags=["assistant-research"])    # api.py:10
```

plus the identity bridge at `/auth` (`api.py:6`). `api_router` mounts at
`settings.API_V1_STR = "/api/v1"` (`backend/app/core/config.py:32`).

**The complete chat-turn endpoint set:**

| # | Method + full route | Router file | Handler function | Notes |
|---|---|---|---|---|
| 1 | `POST /api/v1/assistant/sessions/{session_id}/query/stream` | `backend/app/api/v1/endpoints/streaming.py:199` | **`stream_query_assistant`** (`streaming.py:200`) | **SSE. This is the one the ERPNext widget uses.** |
| 2 | `POST /api/v1/assistant/sessions/{session_id}/query/stream_multimodal` | `streaming.py:485` | `stream_multimodal_query_assistant` (`:486`) | SSE, `multipart/form-data`; every flag is its own `Form` field, **not** `ChatQuery` |
| 3 | `POST /api/v1/assistant/sessions/{session_id}/query` | `backend/app/api/v1/endpoints/assistant.py:550` | `query_assistant` (`:551`) | Non-streaming, `response_model=ChatMessageSchema`. **No caller found in either repo.** |
| 4 | `POST /api/v1/assistant/sessions/{session_id}/research/plan` | `backend/app/api/v1/endpoints/research.py:45` | `generate_research_plan` (`:46`) | JSON |
| 5 | `POST /api/v1/assistant/sessions/{session_id}/research/execute` | `research.py:124` | `execute_research` (`:125`) | SSE |

Session/message CRUD the chat path also needs, all in `assistant.py`: `GET …/sessions` →
`get_chat_sessions` (`:359-364`) · `POST …/sessions` → `create_chat_session` (`:449-472`) ·
`GET …/sessions/{id}/messages` → `get_session_messages` (`:366-403`) · `PUT …/sessions/{id}` →
`update_chat_session_title` (`:492-517`) · `DELETE …/sessions/{id}` → `delete_chat_session`
(`:519-548`) · `GET …/models` → `list_available_models` (`:157-164`) · `GET …/tools` →
`list_available_tools` (`:166-234`) · `GET …/morning-briefing` → `get_morning_briefing`
(`:123-146`). Pending-action lifecycle in `backend/app/api/v1/endpoints/integrations.py`:
`POST /api/v1/integrations/actions/{action_id}/confirm` → `confirm_action` (`:176-197`) and
`…/cancel` → `cancel_action` (`:200-219`). `docs/api-reference.md:38-58` lists the same set, so the
docs are in sync here.

**Which one the widget calls — traced end to end.** Browser → same-origin Frappe method:

```javascript
const METHOD = "erpnext_enhancements.triton_chat";                       // triton_widget.js:16
const res = await fetch(`/api/method/${METHOD}.stream_query`, {          // triton_widget.js:1270-1271
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": frappe.csrf_token,
        Accept: "text/event-stream",
    },
    body: JSON.stringify({
        session_id: state.sessionId,
        prompt: text,
        context: opts.hidden ? "[]" : JSON.stringify(state.contextRefs),
        hidden: opts.hidden ? 1 : 0,
        model: state.model || "",
        persona_key: state.persona || "",
    }),
});
```
— `erpnext_enhancements/public/js/global_enhancements/triton_widget.js:1269-1286`

Frappe server → Triton, `erpnext_enhancements/triton_chat.py:567`:

```python
url = f"{base_url}/api/v1/assistant/sessions/{cint(session_id)}/query/stream"
```

with the body assembled at `triton_chat.py:558-566`:

```python
payload: dict = {"prompt": _build_prompt(prompt, context), "hidden": cint(hidden) == 1}
if model:
    payload["model_name"] = model
# Sent even when empty: "" explicitly means the plain Triton voice for this
# turn, which Triton distinguishes from omitting the field (inherit the
# session's sticky persona, then the account default).
if persona_key is not None:
    payload["persona_key"] = persona_key
```

**The widget never calls `stream_multimodal` and never calls the non-streaming `/query`.** The
fourteen other Triton routes it proxies, all through `_request` (`triton_chat.py:162-188`), are
enumerated at `notes_triton_audit.md:134-149`.

### B.2 The request model, `ChatQuery`, PASTED

`backend/app/schemas/chat.py:51-70`, re-read from source this session, byte for byte:

```python
class ChatQuery(BaseModel):
    prompt: Optional[str] = None # Optional because prompt could be in 'parts'
    parts: Optional[List[ChatPart]] = None
    model_name: Optional[str] = None
    thinking_level: Optional[str] = None # low, medium, high, minimal
    # Persona for this turn. Omit (None) to inherit the session's sticky
    # persona, then the account default; send "" to force the plain Triton
    # voice for this turn regardless of either.
    persona_key: Optional[str] = None
    use_search: bool = False
    use_maps: bool = False
    use_code_execution: bool = False
    use_deep_research: bool = False
    use_orchestrator: bool = False
    google_drive_file_ids: Optional[List[str]] = None
    forced_tools: Optional[List[str]] = None
    response_schema: Optional[dict] = None # JSON schema for structured output
    # Auto-continuation turn fired after an action is approved: reaches the model
    # via history but is hidden from the rendered transcript (system note).
    hidden: bool = False
```

and its one nested model, `backend/app/schemas/chat.py:47-49`:

```python
class ChatPart(BaseModel):
    text: Optional[str] = None
    inline_data: Optional[dict] = None # {mime_type: str, data: str (base64)}
```

**Field by field, with what the streaming handler does with each:**

| Field | Type | Default | Consumed where |
|---|---|---|---|
| `prompt` | `str` | `None` | `streaming.py:220, 221, 222, 263, 271, 302, 315, 450`. **See the `None` trap below.** |
| `parts` | `List[ChatPart]` | `None` | **DEAD.** Never read anywhere in `backend/app/`; a grep for `query.parts` / `ChatPart` returns only the schema definition. |
| `model_name` | `str` | `None` | `streaming.py:316` → `select_model(explicit_model=…)` |
| `thinking_level` | `str` | `None` | `streaming.py:439`. The comment says `low, medium, high, minimal`; **no enum enforces it** |
| `persona_key` | `str` | `None` | `streaming.py:286-292`. Tri-state: `None` = inherit; `""` = force plain Triton; `"builtin:x"` / `"custom:42"` = that persona |
| `use_search` | `bool` | `False` | `streaming.py:341, 436` — forces the in-process path |
| `use_maps` | `bool` | `False` | `streaming.py:341, 437` — forces the in-process path |
| `use_code_execution` | `bool` | `False` | `streaming.py:435` |
| `use_deep_research` | `bool` | `False` | `streaming.py:318, 341` — forces the in-process path |
| `use_orchestrator` | `bool` | `False` | `streaming.py:319, 336`; **hard-overridden to `False` at `backend/app/core/intelligence.py:1016`** |
| `google_drive_file_ids` | `List[str]` | `None` | Declared but **not read by `stream_query_assistant`** — only the multimodal endpoint and `analyze_business_context_stream` consume it (`intelligence.py:1081`) |
| `forced_tools` | `List[str]` | `None` | `streaming.py:303-310` — injected as a `[USER GUIDANCE: …]` prefix onto the prompt |
| `response_schema` | `dict` | `None` | **DEAD.** No chat handler reads it |
| `hidden` | `bool` | `False` | `streaming.py:265, 270` — persists the user message with `ui_metadata={"system_note": True}` so the model sees it via history but the frontend filters it out |

> **THE TRAP a new bridge will hit first.** `backend/app/api/v1/endpoints/streaming.py:220`:
> ```python
>         # Intercept admin passphrase command (non-streaming)
>         if query.prompt.startswith("/admin enable "):
> ```
> `prompt` is declared `Optional` and defaults to `None`, and it is dereferenced unguarded. A caller
> that posts `{}` or `{"parts": [...]}` — exactly what the schema's own comment invites — gets an
> `AttributeError`, i.e. **a 500, not a 422**. The same unguarded call exists on the non-streaming
> path at `assistant.py:566`. **A Google Chat relay must always send a non-null `prompt` string;
> sending `parts` alone does not merely fail to work, it crashes.**

**The multimodal endpoint's body is not `ChatQuery`** — `streaming.py:485-501` declares each field
as a separate `Form(...)` parameter plus `files: List[UploadFile] = []`, with
`google_drive_file_ids` and `forced_tools` as **JSON-encoded strings** parsed at `:508-511` and
`:515-521`, and **no `thinking_level`, no `hidden`, no `use_orchestrator`**.

### B.3 Response models

#### B.3.1 Non-streaming — PASTED

Declared `response_model=ChatMessageSchema` (`assistant.py:550`), which is `ChatMessage` from
`backend/app/schemas/chat.py:5-20`:

```python
class ChatMessageBase(BaseModel):
    role: str
    content: str

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatMessage(ChatMessageBase):
    id: int
    session_id: int
    tokens: int
    ui_metadata: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True
```

`ui_metadata` is an untyped `dict`; the non-streaming handler fills it at `assistant.py:647-658`
with `{"commands": [...], "sources": [...]}` plus a flattening of the first command's keys.

Session shapes, `backend/app/schemas/chat.py:22-45`:

```python
class ChatSessionBase(BaseModel):
    title: Optional[str] = None
    model_name: str = "gemini-2.5-flash"
    # Sticky persona for this conversation ("builtin:dan_bot" / "custom:42").
    persona_key: Optional[str] = None

class ChatSessionCreate(ChatSessionBase):
    pass

class ChatSessionUpdate(BaseModel):
    # Both optional so a client can rename, re-persona, or do both. Widened
    # from a required `title` when personas landed — handlers must treat None
    # as "leave unchanged", not "clear".
    title: Optional[str] = None
    persona_key: Optional[str] = None

class ChatSession(ChatSessionBase):
    id: int
    user_id: int
    created_at: datetime
    messages: List[ChatMessage] = []

    class Config:
        from_attributes = True
```

The identity-bridge models, `backend/app/api/v1/endpoints/erpnext_bridge.py:59-68`, re-read from
source this session:

```python
class BridgeTokenRequest(BaseModel):
    email: str
    full_name: Optional[str] = None


class BridgeTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: int
```

and the pending-action decision body, `integrations.py:170-173`:

```python
class ActionDecisionRequest(BaseModel):
    # Chat session the action was proposed in, so the backend can append a
    # model-only context note recording the outcome. Optional/backward-compatible.
    session_id: Optional[int] = None
```

#### B.3.2 Streaming — there are NO Pydantic response models, and that is the finding

The streaming endpoints return `fastapi.responses.StreamingResponse` with
`media_type="text/event-stream"` (`streaming.py:474-482`, `:696`). **The event payloads are plain
`dict`s serialised with `json.dumps`. There is no Pydantic model anywhere for them, and therefore no
schema in the OpenAPI document.** Any consumer contract has to be written by hand from the code.

The canonical list, verbatim from the module docstring of
`backend/app/core/tool_loop_stream.py:12-19`:

```
The generator yields the same SSE-shaped events the legacy code emitted:
  - {"type": "thought", ...}
  - {"type": "text", "content": str}
  - {"type": "tool_status", "content": str}
  - {"type": "pending_action", "params": ...}
  - {"type": "ui_command", "command": ..., "params": ..., optional "payload": ...}
  - {"type": "done", "usage": dict, "sources": list, "text": str, "function_calls_executed": list}
  - {"type": "error", "content": str}
```

and verbatim from `streaming.py:10-12`:

```
The events the client understands are `thought`, `text`, `tool_status`, `ui_command`,
`source`, `done`, and `error`. `ui_command` is how a proposed mutation reaches the UI for
confirmation; dropping or reordering it breaks the write gate, not just the rendering.
```

> **A discrepancy Phase 5 must carry forward.** The `streaming.py` docstring says `source`
> (singular). **The wire name is `sources` (plural)** — the relay dispatches on `"sources"` at
> `streaming.py:131`, the tool loop emits `"sources"` inside the terminal `done` event
> (`tool_loop_stream.py:191`), and **both** frontends read `"sources"`
> (`frontend/src/views/ChatView.vue:3049`; `triton_widget.js:1348`). There is additionally an
> **`agent_spawn`** event the ERPNext widget handles (`triton_widget.js:1341-1344`) that appears in
> neither docstring.

### B.4 The streaming protocol, by name, with its exact event vocabulary

**Transport: Server-Sent Events over a plain HTTP POST.** Not chunked-JSON, not WebSocket.
WebSockets exist in Triton but only for logs, presence and Gemini Live
(`backend/app/api/v1/endpoints/websockets.py:52, 87, 121`) — **never for chat.**

Response headers, `streaming.py:474-482`:

```python
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
```

Frame encoding, `streaming.py:62-64`:

```python
def _sse(event: dict) -> str:
    """Encode an event dict as a single SSE frame."""
    return f"data: {json.dumps(event)}\n\n"
```

**There is no `event:` line.** Every frame is a bare `data:` line and the discriminator is the JSON
body's `"type"` key. A standard `EventSource` would see everything as the default `message` event —
moot, because `EventSource` cannot POST, which is exactly why both clients hand-roll the parse
(`frontend/src/lib/api.ts:124-131`; `ChatView.vue:2915-2947`; `triton_widget.js:1294-1306`).

**Frame-by-frame schema** (`_run_agent_stream`, `streaming.py:67-196`):

| `type` | Payload keys | Emitted at | Relay behaviour |
|---|---|---|---|
| *(SSE comment)* | literally `: ping\n\n` | `streaming.py:326` (text), `:645` (multimodal) | **Once, before any work.** Flushes headers through proxies. **Not a heartbeat.** |
| `tool_status` | `content: str` | `streaming.py:328`; `tool_loop_stream.py:234, 312` | Relayed verbatim, not persisted |
| `thought` | `content: str` | `intelligence.py:1066,1082,1094,1097,1170`; passthrough at `tool_loop_stream.py:202-203,278-279` | Accumulated into `ui_meta["thinking"]` |
| `text` | `content: str` | `tool_loop_stream.py:205-206,212,282-284,291,307,344` | Accumulated; the concatenation becomes the persisted `ChatMessage.content` |
| `pending_action` | `params: dict` | `tool_loop_stream.py:96` | Collected into `ui_meta["pending_actions"]` so cards survive a refresh (`streaming.py:111-115,150-153`) |
| `ui_command` | `command: str`, `params: dict` | `tool_loop_stream.py:98,100,102,104` | Relayed; only `render_visualization` is also stashed into `ui_meta["visualization"]`. Known commands: `render_chart`, `render_visualization`, `render_3d_simulation`, `render_design_options`, `render_design_canvas`, `render_design_math`, `voice_dial`, `show_native_plan_approval` |
| `visualization` | `content: dict` | legacy path | Stashed into `ui_meta["visualization"]` |
| `sources` | `content: list` | mid-stream variant | Extended into `collected_sources` (`streaming.py:131-133`) |
| `findings` | `content: list` | `intelligence.py:1031` (Deep Research via Reasoning Engine only) | **Not handled by `_run_agent_stream` — silently dropped** |
| `agent_spawn` | `label`/`agent` | orchestrator paths | Handled by the ERPNext widget (`triton_widget.js:1341`); **not relayed by `_run_agent_stream`** |
| **`done`** | `usage`, `sources`, `text`, `function_calls_executed`, **plus injected `content` and `ui_metadata`** | `tool_loop_stream.py:187-194`, re-shaped at `streaming.py:135-184` | **Terminal** |
| **`error`** | `content: str` | `tool_loop_stream.py:221,297`; `streaming.py:472`, `:694` | **Terminal** |

**How the stream terminates — three ways.** (1) A `done` frame: `_run_agent_stream` persists the
assembled assistant `ChatMessage` and a `ModelUsage` row inside a **fresh** `SessionLocal()`
(`streaming.py:155-171`), enqueues memory generation (`:173-180`), then **mutates the event before
forwarding**:

```python
            event["content"] = full_text
            event["ui_metadata"] = ui_meta
            yield _sse(event)
```
— `streaming.py:182-184`

(2) An `error` frame, after writing a `ChatMessage` carrying the error text with `tokens=0`
(`:186-196`). (3) **The stream just ends** — there is **no sentinel frame, no `[DONE]`, no
`event: close`**; both clients loop until `reader.read()` reports done.

**Errors mid-stream:** a Gemini-level `error` is forwarded and the generator `return`s immediately
(`tool_loop_stream.py:220-222`, `:296-298`) — **no `done` follows an error**. A fatal endpoint
exception emits one frame: `data: {'type': 'error', 'content': 'Communication disruption
detected.'}` (`streaming.py:472`). **Pre-stream failures return ordinary HTTP errors, not SSE** —
`404 "Session not found"` at `streaming.py:217`, which the ERPNext proxy converts into a synthetic
SSE error frame itself (`triton_chat.py:535-537, 582-590`). **Tool-level failures are not stream
errors**: `_execute_tool` swallows the exception and returns the string
`f"Operation failed: {str(e)}"` to the model (`intelligence.py:1552`) plus an `IntegrationAuditLog`
row with `success=False`.

**Heartbeat: there is none.** The only SSE comment is the single `: ping\n\n` at stream open, whose
stated purpose is TTFB, not liveness:

```python
    async def event_generator():
        # SSE comment flushes headers + opens the stream through any proxy before
        # we do any work. Drives TTFB toward zero.
        yield ": ping\n\n"
```
— `streaming.py:323-326`

Liveness during a long tool turn is carried only by `tool_status` frames, emitted at tool-loop
boundaries and not on a timer. The ERPNext proxy sets **connect 15 s, read
`settings["timeout"]` (default 120 s)** — `triton_chat.py:580`, default at `:76`.

> `VERIFY: whether a Gemini tool turn can exceed 120 s of silence between tool_status frames and
> trip the ERPNext-side read timeout — settle by grepping the production Frappe Error Log for
> "Triton Chat" entries containing "Connection error" / "Read timed out" — blocks: whether Phase 5
> must add a server-side keepalive frame.` (`notes_triton_audit.md:505-508`)

And the infra interaction, which is worse than the app-level timeout: **the GCLB backend
`timeout_sec` is unset in Terraform and therefore defaults to 30 s as a total request→response
budget** (`infra/configs/load_balancer.yaml:33-39`; `notes_infra.md:792-901`). The relay is a plain
HTTP response, so **a long answer is truncated by the load balancer with no error**, and because
failures are in-band SSE frames the client cannot distinguish "the LB cut us off" from "the stream
ended". **This is an existing defect Phase 5 inherits, not one it creates**, and the ADR records it
as such.

### B.5 Auth, and the validating dependency

There are **two distinct credentials** on the chat path, checked by different code.

#### B.5.1 The chat endpoints: a Triton JWT (HS256 bearer)

Scheme declaration, `assistant.py:32-34`:

```python
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/google/login"
)
```

**The streaming endpoints use a different dependency from the rest of the API, imported under an
alias that is easy to miss:**

```python
from app.api.v1.endpoints.assistant import get_current_user_streaming as get_current_user
```
— `backend/app/api/v1/endpoints/streaming.py:26`

**The validating dependency is `get_current_user_streaming`, `assistant.py:64-99`:**

```python
async def get_current_user_streaming(
    token: str = Depends(reusable_oauth2),
) -> User:
    """Auth dependency for long-lived streaming/SSE endpoints.
    ...
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=403, detail="Could not validate credentials")
    except (JWTError, ValueError):
        raise HTTPException(status_code=403, detail="Could not validate credentials")

    with SessionLocal() as db:
        user = db.query(User).options(
            joinedload(User.roles).joinedload(Role.permissions)
        ).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        # Detach before the session (and its connection) is released so callers
        # get a fully-loaded but session-free instance.
        db.expunge(user)
    return user
```

The non-streaming twin `get_current_user` (`assistant.py:40-61`) does the same JWT check but holds
a pooled DB connection for the whole response, which is *why* streaming has its own.

Algorithm and secret: `ALGORITHM = "HS256"` (`config.py:88`), `SECRET_KEY` defaulting to
`"dev-only-insecure-change-me"` (`config.py:87`) with a comment at `:83-86` warning that rotating it
**invalidates every issued JWT and makes every Fernet-encrypted secret undecryptable**. Token
minting is `create_access_token` (`backend/app/core/auth_utils.py:9-16`) whose only claims are `exp`
and `sub`, with `sub = str(user.id)`. Browser-login TTL is 8 days
(`ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 8`, `config.py:89`).

**Ownership beyond auth:** every session route filters on `ChatSession.user_id ==
current_user.id` and returns **404, not 403**, on a miss — deliberate, so a user cannot probe for
another user's session ids (`assistant.py:11-12`; `streaming.py:212-217`).

#### B.5.2 The ERPNext bridge: a shared gateway secret (machine-to-machine)

`POST /api/v1/auth/erpnext-bridge/token` — handler `mint_bridge_token` at
`erpnext_bridge.py:89`. The validator, `erpnext_bridge.py:71-85`:

```python
def _require_gateway_secret(authorization: Optional[str]) -> None:
    """Validate the shared ``Bearer`` secret. Constant-time compare; 503 if the
    server has no secret configured, 401 if it doesn't match. Mirrors
    telephony_gateway._require_gateway_secret."""
    secret = (settings.ERPNEXT_GATEWAY_SECRET or "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="ERPNEXT_GATEWAY_SECRET is not configured on the server.",
        )
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

Configured as env `ERPNEXT_GATEWAY_SECRET` on the Triton side (`config.py:310`, with the comment at
`:305-310` saying it must equal ERPNext's Admin Webhook Secret) and as
`Triton Settings.admin_webhook_secret` on the ERPNext side, read server-side only via
`conn.get_password(...)` (`triton_chat.py:64`) and **never returned by `get_config`**
(`triton_chat.py:242-262`).

Additional gates inside the handler: email must contain `@` (`:97-98`); **domain must equal
`ALLOWED_DOMAIN = "sapphirefountains.com"`** (`:56`, enforced `:100-106`, else 403); unknown
in-domain emails are **auto-provisioned** as new Triton `User` rows (`:108-121`); inactive users get
403 (`:122-123`); minted TTL is `BRIDGE_TOKEN_TTL_SECONDS = 30 * 60` (`:52`).

#### B.5.3 A third credential, adjacent but not on the chat path

`backend/app/api/v1/endpoints/agent_callbacks.py` — the deployed Agent Engine agent calls back with
an optional Google ID-token check (`:47-74`) plus an always-on HMAC-SHA256 over
`"{ts}.{path}.{sha256(body)}"` keyed by a per-`ChatSession` secret (`:15-17`; signing side at
`backend/app/core/adk/callback_tool.py:61-66`). Its docstring states the identity rule that a Chat
bridge should copy verbatim (`agent_callbacks.py:19-20`):

> `User identity is derived from the looked-up ChatSession, never claimed by the agent.`

### B.6 Actor / identity — the API cannot be told who is asking

#### B.6.1 The answer, plainly

**There is no `on_behalf_of`, `actor`, `user_email`, `as_user`, `requesting_user` or any equivalent
field anywhere in:**

- **`ChatQuery`** — `backend/app/schemas/chat.py:51-70`, pasted in full in §B.2. Read it: there is
  no such field.
- **the multimodal `Form` signature** — `streaming.py:485-501`.
- **any chat route's path or query parameters** — the routes are enumerated in §B.1.

**The API cannot be told who is asking.** The actor is established exactly once, at the auth
boundary: `get_current_user_streaming` decodes the JWT and reads `sub` (`assistant.py:81-86`); `sub`
is the Triton `User.id` (`auth_utils.py:14`); the `User` row is loaded with roles and permissions
eager-loaded and then detached (`assistant.py:90-98`); everything downstream takes `current_user` /
`self.user`.

#### B.6.2 The impersonation mechanism that DOES exist, and is already in production

`POST /api/v1/auth/erpnext-bridge/token` **exchanges an email for that person's short-lived Triton
JWT**, authenticating machine-to-machine with the shared gateway secret (§B.5.2). Its own docstring
states the intent — `erpnext_bridge.py:5-8`:

> `needs to talk to this API *as the logged-in ERPNext user* so attribution, per-user memory,
> usage accounting and ERPNext permission scoping all line up with the rest of Triton.`

**So the credential is per-user; the shared secret authorises only the *impersonation request*, not
the chat turn.** This is a token-exchange/impersonation grant, not a superuser session, and it is
what makes locked decision #5 ("Triton acts as the mentioning user") achievable **with zero new
lines in Triton** on the identity axis.

#### B.6.3 The thirteen downstream enforcement points

| Hop | Enforcement | Cite |
|---|---|---|
| Session ownership | `ChatSession.user_id == current_user.id`, 404 on miss | `streaming.py:212-215` |
| Persona resolution | `effective_persona_key(current_user, session, query.persona_key)` | `streaming.py:286-288` |
| Prompt personalisation | `self.user.job_title`, `self.user.custom_instructions` | `intelligence.py:299-300` |
| Tool payload scoping | `tool_packs.profile_for_session(persona_key=…, roles=self.user.roles)` | `intelligence.py:1390-1393` |
| ERPNext calls | `FrappeClient(db=db, user_id=user.id)` → per-user OAuth2 bearer | `intelligence.py:247` |
| FAC MCP discovery | `FrappeClient(db=db, user_id=user.id)` | `frappe_mcp.py:223` |
| QuickBooks gate | `qbo.qbo_active_for_user(self.db, self.user)` | `intelligence.py:1366, 1456` |
| Google Workspace | `GoogleWorkspaceClient(db, user)` | `intelligence.py:246` |
| RAG corpus | per-user; the company KB is a separate opt-in corpus | `intelligence.py:2292` |
| Pending action row | `PendingAction.user_id = user.id` | `actions.py:54` |
| Confirm / cancel | `PendingAction.user_id == user.id` in the load filter | `actions.py:410-413` |
| Audit log | `IntegrationAuditLog.user_id` | `actions.py:435-441` |
| Approved write dispatch | `FrappeClient(db=db, user_id=user.id)` | `actions.py:492` |

(plus the deployed-agent callback, where the user is derived from the `ChatSession` and never
claimed by the agent — `agent_callbacks.py:19-20, 89-96`.)

**System mode** — `FrappeClient()` with the shared `FRAPPE_API_KEY`/`FRAPPE_API_SECRET` — **is used
on exactly four background paths and none of them is chat**: `sales_sync.py:497`,
`portfolio_sync.py:449`, `sync_engine.py:34`, `wiki_sync.py:295` and `:374`. The first two carry the
inline comment `# system mode (shared FRAPPE_API_KEY/SECRET)`.

#### B.6.4 The two residuals — and one of them is a Phase 0 ROLLOUT task, not a Phase 5 bug

**Residual 1 — there is no non-ERPNext identity bridge.** `erpnext_bridge.py` is the only
email→JWT exchange, and it is hard-bound to `ERPNEXT_GATEWAY_SECRET` and to
`ALLOWED_DOMAIN = "sapphirefountains.com"`. A Google Chat relay must either present **the same**
`ERPNEXT_GATEWAY_SECRET` — semantically wrong, because that secret's name and docstring both say
"ERPNext" and it is *also* the telephony gateway's secret (`backend/app/api/telephony_gateway.py:42`)
— or get a sibling bridge with its own secret. Sizing, from `notes_triton_audit.md:761-776`:
reusing the existing bridge is **~0 new lines in Triton**; a sibling `chat_bridge.py` is
**≈135 lines** (the existing module is exactly 135), plus one `include_router` line, ~8 lines of
config with its `@validator` sanitiser, ~8 lines of `prompt` guards, ~20 lines of docs and ~80 lines
of tests. **This is a naming/hygiene decision, not a capability decision**, and it belongs in the
`CQ-n` register.

**Residual 2 — every human Triton acts for must have completed the ERPNext OAuth link, or the
client raises.** `FrappeClient.__init__` raises `FrappeAuthRequired` when a user-scoped construction
finds no OAuth row and no legacy key — `backend/app/core/frappe.py:151-155`:

```python
            if self._mode == "system":
                raise FrappeAuthRequired(
                    "ERPNext is not linked for this user. Ask them to click "
                    "'Link ERPNext' on the login screen or in Profile."
                )
```

and because `TritonIntelligence.__init__` constructs the client **eagerly** at `intelligence.py:247`,
**a chat turn for an unlinked user raises before the first token is generated.** On the SSE path
that lands in the broad `except Exception` at `streaming.py:460` and the user sees
`"Communication disruption detected."` — **not** a "link ERPNext" prompt. (The structured
`401 erpnext_link_required` contract described in `docs/decisions/0004-*:25-28` and
`backend/app/main.py:22-23` applies only to *non-streaming* routes.) Triton's own ADR accepts the
friction explicitly — `docs/decisions/0004-per-user-oauth-to-erpnext.md:32`:

> `Users must complete an ERPNext OAuth link before Triton can act for them. That is real
> onboarding friction, deliberately accepted.`

> **Flagged as a ROLLOUT task, in Phase 0, not as a Phase 5 bug.** Chat-room participants have not
> necessarily completed that link, and **auto-provisioning a Triton `User` (which the bridge does)
> does NOT auto-provision an ERPNext grant.** Every person `@triton` will ever act for must complete
> `GET /api/v1/auth/erpnext/login?token=<triton jwt>` → `GET /api/v1/auth/erpnext/callback`
> (`backend/app/api/v1/endpoints/auth.py:323-324, 352-353`), with status readable at
> `…/auth/erpnext/status` (`:448-463`). At the measured roster that is ~20 people, not ~50
> (`notes_register_reconciled.md` C4), but it is still an onboarding campaign with a completion
> metric, and it must be scheduled **before** Phase 5 rather than discovered inside it. The
> companion fix — making the unlinked case legible on the streaming path instead of
> "Communication disruption detected." — is a Phase 5 task worth ~4 lines.

### B.7 Context injection, and whether a volatile value sits above the stable prefix

**The answer: the ordering is already correct, the prompt cache is already being earned, and this is
NOT an available cheap win — it has already been taken.**

Prompt assembly happens in two functions, both in `backend/app/core/intelligence.py`:
`_build_system_instruction(persona)` (`:275-306`), which the module docstring at `:12` calls *"the
**single** source of the system prompt"*; and `analyze_business_context_stream(...)` (`:993-1213`),
whose per-turn message assembly is `:1138-1156`. Prefix caching is resolved by
`_resolve_cached_prefix` (`:359-486`).

`_build_system_instruction`, verbatim (`intelligence.py:275-306`):

```python
    def _build_system_instruction(self, persona: Optional[ResolvedPersona] = None) -> str:
        """Assemble the stable system instruction for a chat turn.

        Layered so a user-authored persona can set the voice without being able
        to loosen anything:

            1. <persona> fence — establishes identity
            2. the user's job title and personal instructions
            3. TOOL_USAGE_RULES — hardcoded, never overridable
            4. FOUNTAIN_DESIGN_GUIDE — hardcoded
            5. PERSONA_GUARDRAIL — last, so recency favors "the rules win"

        Two properties this function must keep, both enforced by tests in
        backend/tests/test_personas.py, because _resolve_cached_prefix hashes
        the return value into the Gemini CachedContent key:

        * With persona=None the output is byte-identical to what Triton emitted
          before personas existed. Any drift there would invalidate every live
          CachedContent for every user on deploy.
        * It is a pure function of (persona, job_title, custom_instructions).
          A clock read or an unordered iteration in here would make the hash
          flap and force a cache rebuild on every single turn.
        """
```

**There is no `datetime`, no session id, no request id and no "now" in that function** — the
invariant at `:294-296` says so explicitly and `backend/tests/test_personas.py` enforces it.

The per-turn split, verbatim (`intelligence.py:1138-1156`):

```python
            # 2. Build messages
            messages = []
            if not cached_name:
                if user_personal_context:
                    messages.append({"role": "user", "parts": [{"text": f"SYSTEM CONTEXT:\n{user_personal_context}"}]})
                    messages.append({"role": "model", "parts": [{"text": "Understood."}]})
                messages.extend(history_contents)

            volatile_chunks: List[str] = []
            if context_text.strip():
                volatile_chunks.append(
                    f"RETRIEVED CONTEXT (Use if relevant to the query):\n{context_text}"
                )
            volatile_chunks.append(f"USER QUERY:\n{user_query}")
            user_turn_parts = [{"text": "\n\n".join(volatile_chunks)}]
            for p in multimodal_parts:
                user_turn_parts.append(p)

            messages.append({"role": "user", "parts": user_turn_parts})
```

with the classifying comment immediately above (`intelligence.py:1115-1118`):

```python
            # Cache the stable parts (system instruction + history). RAG
            # snippets and drive context vary per query — they go on the user
            # turn so the cache survives across tool-loop iterations rather
            # than thrashing on every message.
```

**The mechanism is Gemini *explicit* `CachedContent`, not implicit prefix caching** —
`self.gemini.create_cached_content(...)` at `intelligence.py:455-461` (implemented at
`backend/app/core/gemini.py:182-239` via `self.client.caches.create(...)`, TTL 3600 s). The cache
**key** is a SHA-256 over a `sort_keys=True` JSON payload of `{system, history, tools fingerprint,
fac_catalog digest}` (`intelligence.py:402-415`). `datetime.now(timezone.utc)` *is* called in that
function, at `:422` — but **only** to compare against `cached_content_expires_at`; it never enters
the digest payload and never enters the prompt.

> **Correction carried forward** (`notes_register_reconciled.md` C6): research document R03 §9
> reasons about *implicit* prefix caching and a 4,096-token minimum. **Triton does not use that
> mechanism.** Anything in a later phase that sizes a "stable prefix" against the implicit threshold
> is sizing against the wrong thing.

**Turns that run uncached**, `intelligence.py:1127-1136`: any turn with attachments, or with
Search / Maps / code execution enabled, runs **uncached** — *"Baking them into an hour-long cache
would either strand the wrong toolset or thrash the cache each time the user flips one"*. And
`_resolve_cached_prefix` returns `None` when there is no history yet (`:446-453`), so **the first
turn of a session is never cached**.

**Where the ERPNext widget's page context lands — and it is correct by construction.**
`_build_prompt` (`erpnext_enhancements/triton_chat.py:194-236`) prepends its preamble **on the
ERPNext side**, so it arrives inside `ChatQuery.prompt` (`triton_chat.py:230-236`):

```python
    preamble = (
        "[ERPNEXT PAGE CONTEXT] The user is currently viewing the following in "
        "ERPNext. Use your ERPNext tools to fetch live details as needed when "
        "they are relevant to the question; do not assume values you have not "
        "fetched:\n" + "\n".join(lines) + "\n\n"
    )
    return preamble + prompt
```

Because `prompt` becomes `USER QUERY:` on the **volatile** turn (`intelligence.py:1151`), **it does
not touch the cached prefix.** A Google Chat relay that injects its own context preamble the same
way inherits the same correctness for free — and **must** inject it into `prompt`, never into the
system instruction.

**One latent bug this surfaces**, `streaming.py:269-274`:

```python
        # Auto-rename session if first message
        if not query.hidden and len(session.messages) <= 1 and session.title in ["New Chat", "New Intelligence Stream"]:
            new_title = query.prompt[:40] + ("..." if len(query.prompt) > 40 else "")
```

`query.prompt` here is the **preamble-prefixed** string, so a first message with pinned context
would title the session `"[ERPNEXT PAGE CONTEXT] The user is c..."`. It is latent today only because
`start_session` names the session `"ERPNext Chat"` (`triton_chat.py:278`), which is not in the
rename allowlist. **It becomes live the moment a new bridge creates sessions titled `"New Chat"`** —
so a Chat bridge must either title its sessions distinctly or fix the rename to strip the preamble.

### B.8 The `tool_defs` registry — shape, and one definition pasted

`backend/app/core/adk/tool_defs.py` is **1,452 lines / 68,061 bytes** with **exactly three
module-level `def`s**, all at the bottom: `summarize_tool_call` (`:1290`), `_humanize_tool_name`
(`:1412`), `risk_level` (`:1437`). It holds **75 `types.FunctionDeclaration(...)` literals**. Its
docstring is the specification (`tool_defs.py:1-19`):

```python
"""Hand-maintained tool declarations for the deployed ADK agents.

This module is a **declarative registry**, not ordinary code — it is mostly one long list
of `FunctionDeclaration`s. Giving an agent a new ERPNext capability means adding an entry
here, not writing a new module.

Why it exists at all: the live Gemini path discovers `fac_*` tools dynamically from Frappe
Assistant Core (`core.frappe_mcp`), but a deployed Agent Engine agent has no per-request
discovery — its tool set is frozen at deploy time. `FRAPPE_TOOLS` is that frozen set,
bundled at build time and wrapped as `CallbackTool`s in `adk/agents.py`. Execution still
routes per-user through the `/api/v1/agent/tool` callback, which dispatches any `fac_`
name to the live FAC client.

`FRAPPE_LOCAL_TOOLS` (`fac_bulk_create_documents`, `fac_merge_documents`) are the
exception: Triton-local, executed in-process via `actions._dispatch_frappe`.

Because this list is maintained by hand, it drifts. When a tool behaves differently for a
deployed agent than in chat, suspect a stale declaration here first.
"""
```

**Top-level structures, in file order:** `FRAPPE_TOOLS` (`:24`, deliberately **excluded** from
`TRITON_TOOL_DEFINITIONS` at `:1207-1211`) · `ENGINEERING_TOOLS` (`:435`) · `OPERATIONS_TOOLS`
(`:470`) · `HELPDESK_TOOLS` (`:507`) · `ANALYST_TOOLS` (`:547`) · `GOOGLE_WORKSPACE_TOOLS` (`:614`,
`gws_*`) · `BI_TOOLS` (`:915`, `bi_render_*`) · `VOICE_TOOLS` (`:1103`) · `LOCAL_TOOLS` (`:1129`,
`local_*`) · `_FRAPPE_LOCAL_TOOL_NAMES` (`:1203`) · `FRAPPE_LOCAL_TOOLS` (`:1204`) ·
**`TRITON_TOOL_DEFINITIONS`** (`:1212`, the one wrapper the live path starts from) ·
**`MUTATION_TOOLS`** (`:1223`, the static half of the write gate) · `ALL_TOOLS` (`:1253`) ·
`INTEGRATION_FOR_TOOL` (`:1265`, built by a prefix-matching loop at `:1266-1287`) ·
`HIGH_RISK_TOOLS` (`:1425`).

**One definition, pasted** — `tool_defs.py:25-37`, the first entry of `FRAPPE_TOOLS`:

```python
    types.FunctionDeclaration(
        name="fac_list_documents",
        description="Search and list ERPNext/Frappe documents with filters.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "doctype": {"type": "STRING"},
                "filters": {"type": "OBJECT", "description": "JSON filters e.g. {'status': 'Open'}"},
                "fields": {"type": "ARRAY", "items": {"type": "STRING"}}
            },
            "required": ["doctype"]
        }
    ),
```

So the schema is `google.genai.types.FunctionDeclaration(name=…, description=…, parameters=<raw
Gemini OpenAPI-subset dict with UPPERCASE type names>)`. **There is no custom wrapper class, no
decorator and no plugin base class**, and the types are the Gemini spellings `OBJECT` / `STRING` /
`ARRAY` / `INTEGER`, **not** JSON-Schema lowercase. Note also that the `description` carries
behavioural policy, not just prose — `fac_bulk_create_documents` at `:62-77` uses it to say *"never
loop fac_create_document for that. Requires one user confirmation for the whole batch."*

**Registration on the live path:** `TRITON_TOOL_DEFINITIONS` is the seed (`intelligence.py:1350`);
`sfo_search_knowledge` is stripped when `COMPANY_KB_ENABLED` is false (`:1351-1362`); **`fac_*`
tools are discovered live from the FAC MCP server, not read from this file**
(`frappe_mcp.get_fac_tool_declarations(self.db, self.user)`, `:1363-1365`); QuickBooks tools are
appended only when `qbo_active_for_user` (`:1366-1370`); `_apply_tool_budget` scopes by tool pack
and caps the count (`:1374-1412`). Discovery itself runs MCP `initialize` then `tools/list`
(`frappe_mcp.py:230-231`), converts each server tool via `build_function_declaration(...)` (`:242`),
maps `gemini_name ↔ server_name` with `_NAME_PREFIX = "fac_"` (`:59`, `:113-120`), and caches for
3600 s with a content digest so an unchanged catalogue costs nothing (`:185-201, 285-298`).

**Dispatch is a single switch** — `TritonIntelligence._execute_tool` (`intelligence.py:1442-1552`) —
and its ordering is commented as load-bearing (`:1494-1495`): qbo gate → mutation gate → `qbo_` →
`fac_bulk_create_documents` → `fac_` → `gws_` → `BI_RENDER_HANDLERS` → `sfo_` →
`voice_call_contact` → `local_` → *"Unknown tool source"*.

**What adding a tool requires — and the answer for chat is the important half.** *If the tool is an
ERPNext domain tool, you do not touch Triton at all*: add it in
`erpnext_enhancements/assistant_tools/` per that package's README (one module per tool, filename
equal to the tool name, one `BaseTool` subclass, register in `hooks.py`, classify in `_gate.py`, set
`self.annotations = annotations_for(self.name)`) and the live Gemini path picks it up within the
3600 s discovery TTL. `docs/convergence.md:63-64` states it: *"add new domain tools on the ERPNext
side and the live path picks them up immediately."* The **deployed** ADK path additionally needs
`python -m scripts.snapshot_fac_tools --user <id>` run from `backend/` and the regenerated
`adk/fac_tools_generated.py` (195,610 bytes, committed) checked in.

*If the tool is Triton-native* (`sfo_`, `gws_`, `bi_`, `local_`, `voice_`) it is nine steps —
declaration, dispatch arm, `MUTATION_TOOLS` + an `actions._dispatch*` arm if it mutates, a
`summarize_tool_call` case, a risk classification, tool-pack membership (or B3's drift test fails
CI), presence in `GET /api/v1/assistant/tools` (asserted by `tests/test_tools_endpoint.py`), the
snapshot re-run, and the version bump (`notes_triton_audit.md:1124-1141`).

### B.9 The pending-action flow, and exactly what routing a Chat post through it requires

The module docstring of `backend/app/core/actions.py` (608 lines) is the spec — `actions.py:1-10`:

```python
"""Pending-action lifecycle: propose → confirm/cancel → execute → audit.

The flow:
1. intelligence._execute_tool detects a mutation tool → calls create_pending().
2. create_pending() persists a PendingAction row and returns a ui_command dict.
3. The SSE stream delivers the ui_command to the Vue frontend.
4. Frontend renders a confirm/cancel card.
5. User clicks confirm → POST /integrations/actions/{id}/confirm.
6. confirm_action() loads the row, calls _dispatch(), writes IntegrationAuditLog.
"""
```

**Detection** — `intelligence.py:1463-1471`:

```python
            is_mutation = (
                name in MUTATION_TOOLS
                or quickbooks_mcp.is_qbo_mutation(name)
                or frappe_mcp.is_fac_mutation(name)
            )
            if is_mutation and not getattr(self, "bypass_pending", False):
                result = create_pending(self.db, self.user, name, args)
                logger.info(f"[TOOL] {name} → pending_action (awaiting confirmation)")
                return result
```

**Proposal** — `create_pending` (`actions.py:40-98`) writes a `PendingAction` row (`:52-63`),
optionally generates a plain-English description with the Flash-Lite tier — **only when
`risk == "high"`** (`:135-136`) — and returns the envelope at `:72-98`, whose comment is worth
carrying because it records a real failure:

```python
        # Model-visible guardrail. The deployed ADK agent receives this whole
        # dict as the tool's function_response … Without an explicit non-result
        # signal it treated the proposal as a completed call, fabricated the
        # (non-existent) output, and chained a dependent action on the invented
        # data — e.g. emailing a "report" built from made-up rows before the
        # report-generating code had actually run.
        "status": "awaiting_user_approval",
        "executed": False,
        "output": None,
        "message": _PENDING_ACTION_MODEL_NOTICE,
        "params": {
            "action_id": action_id,
            "tool_name": tool_name,
            "integration": integration,
            "summary": summary,
            "description": description,
            "risk": risk,
            "args": args,
        },
```

**Delivery and turn termination** — `tool_loop_stream._emit_ui_events` turns it into
`{"type": "pending_action", "params": params}` (`:95-96`), the model is fed `_PENDING_ACTION_ACK`
instead of the envelope (`:53-63, 74-85`), and **the turn ends there** (`:258-266`):

```python
    # A proposed confirmation-gated action hasn't run — its output doesn't
    # exist yet. End the turn at the approval card instead of streaming again;
    # continuing here is what let the model invent the missing result and act
    # on it. …
    if any(_is_pending_action(r) for r in results):
        logger.info(f"[{log_prefix}] Pending action proposed — ending turn to await user approval.")
        yield _done_event()
        return
```

**Decision** — `confirm_action` (`actions.py:166-205`) moves `pending` → `approved` → `_dispatch` →
`executed` (or `failed`), writes `_write_audit` on both branches (`:188, 193`), posts the real output
as a visible assistant message (`_record_result_message`, `:290-342`) and appends a hidden
`system_note` so the model learns the outcome (`_record_decision_note`, `:345-402`).
`cancel_action` (`:208-221`) sets `cancelled` and records the note but **writes no audit row — only
executions are audited.** `_load_action` enforces ownership **and expiry** (`:409-419`); the TTL is
1 hour on the model default (`backend/app/models/actions.py:34-37`).

**What marks a tool as mutating — three independent classifiers, OR-ed:** the static
`MUTATION_TOOLS` set (28 names: the two local `fac_` helpers, 20 `gws_*`, 5 `sfo_*`);
`quickbooks_mcp.is_qbo_mutation(name)`; and `frappe_mcp.is_fac_mutation(name)` (`:146-161`), where
**the tool's own MCP annotations from ERPNext are authoritative** and only a tool that asserts
nothing falls back to a verb guess.

#### B.9.1 Routing a Chat post through it — the exact requirement list

A Google Chat post is a mutation of an external system, and `CLAUDE.md:56-58` makes it
non-negotiable: *"Any AI tool that mutates an external system must route through the pending-action
flow… Bypassing it is a security regression, not a shortcut."*
`docs/decisions/0003-confirmation-gate-for-ai-writes.md:33-35` repeats it: *"This is a security
boundary, not a UX pattern."* A hypothetical `gchat_post_message` therefore needs **all** of:

| # | Requirement | Where | Why it fails without it |
|---|---|---|---|
| 1 | A `FunctionDeclaration` in a `tool_defs.py` list | `tool_defs.py` | The model cannot name it |
| 2 | The name added to `MUTATION_TOOLS` | `tool_defs.py:1223` | It executes immediately, ungated. **The prefix classifiers only cover `fac_` and `qbo_`; a new `gchat_` prefix matches neither, so the static set is the only thing that can gate it.** |
| 3 | A `summarize_tool_call` case | `tool_defs.py:1290` | The card shows a humanised slug from `_humanize_tool_name` |
| 4 | A risk classification | `tool_defs.py:1425` | Defaults to `"medium"`, so `_describe_action` is skipped (`actions.py:135`) and the card carries no plain-English explanation. **Posting publicly on someone's behalf arguably wants `"high"`.** |
| 5 | A prefix arm in `integration_for_tool` | `backend/app/core/tools.py:18-37` | Audit rows get `integration="unknown"` |
| 6 | **A dispatch arm in `actions._dispatch`** | `actions.py:462-474` | **`confirm_action` raises `ValueError(f"Unknown integration for tool {name}")` — the approval fails at execution time, *after* the row is already marked `approved`.** `_dispatch` today knows only `fac_`, `gws_`, `qbo_`. This is the sharpest edge in the list. |
| 7 | A dispatch arm in `intelligence._execute_tool` | `intelligence.py:1473-1504` | Falls through to *"Unknown tool source"* |
| 8 | Tool-pack membership | `backend/app/core/tool_packs.py` | The B3 drift test fails CI |
| 9 | Per-user Google credentials carrying a Chat scope | `backend/app/core/google_workspace.py` | `GoogleWorkspaceClient(db, user)` already carries per-user OAuth, so **the scope is the open question, not the plumbing** — see §E |
| 10 | The three-file version bump + CHANGELOG | §B.12 | CI |

**And the UX consequence, which is a design decision rather than wiring.** The confirmation card is
rendered by a *chat client*. **If Triton is answering inside Google Chat there is no Vue card and no
Frappe card** — the `pending_action` frame arrives and nothing on that surface draws it. Either a
Chat card with an action button is built, or approvals are deliberately routed back to the Triton
SPA / ERPNext desk. Note the precedent that makes the first option legitimate: ERPNext's own
confirmation is **desk-only by design** with explicitly **no MCP confirm tool**, because *"a
model-callable confirm would collapse the human-in-the-loop guarantee under prompt injection"*
(`erpnext_enhancements/assistant_tools/README.md:26-29`, `_gate.py:19-22`). **A Chat-card confirm
button is a human click, not a model call, so it does not violate that reasoning — but the ADR must
restate the reasoning explicitly rather than leaving the reader to reconstruct it.**

### B.10 HOW THE ERPNEXT MCP SERVER AUTHENTICATES — and the consequence that is still open

#### B.10.1 The verdict

**It authenticates as the individual calling user, via that user's own ERPNext OAuth2 access token.
It is NOT a shared API key on the chat path. Locked decision #5 is not violated for ERPNext data.**

#### B.10.2 The trace, in five steps

1. **Where the server lives.** The MCP server is **Frappe Assistant Core (FAC)** — a third-party
   Frappe app (`https://github.com/buildswithpaul/Frappe_Assistant_Core`) installed on the ERPNext
   site. **It is in neither repo.** `erpnext_enhancements` contributes *tools* to it and the import
   direction is strictly FAC → us — `assistant_tools/README.md:49-59`: *"**Nothing inside
   erpnext_enhancements may import this package.** The import direction is FAC → us … On sites
   without FAC installed the hook entries are inert strings and this package is never imported."*
   The endpoint, from Triton's config (`backend/app/core/config.py:178`):
   ```python
       FRAPPE_MCP_ENDPOINT: str = "https://erp.sapphirefountains.com/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp"
   ```
   Protocol: stateless JSON-RPC 2.0 over StreamableHTTP, MCP protocol version `"2025-03-26"`
   (`frappe_mcp.py:42`), client described as *"OAuth Bearer auth"* (`frappe_mcp.py:5`).
2. **What is sent.** `FrappeClient._auth_header` (`backend/app/core/frappe.py:161-164`):
   ```python
       def _auth_header(self) -> Dict[str, str]:
           if self._mode == "oauth":
               return {"Authorization": f"Bearer {self._bearer_token}"}
           return {"Authorization": f"token {self._token_auth}"}
   ```
   and the MCP call reuses exactly those headers — `mcp_request` (`frappe.py:546-566`), whose
   docstring says *"The endpoint is hosted on the same Frappe instance, so this reuses our auth
   headers and the one-shot 401 → OAuth-refresh retry."*
3. **Which mode the chat path uses.** `TritonIntelligence.__init__` (`intelligence.py:247`):
   `self.frappe = FrappeClient(db=db, user_id=user.id)`. With `db` and `user_id` set,
   `FrappeClient.__init__` resolves in order (`frappe.py:118-155`): an `erpnext_oauth` row →
   `_mode = "oauth"`; else a legacy `erpnext` key/secret row → `_mode = "legacy_key"`; else **raise
   `FrappeAuthRequired`**. **It never silently falls back to system mode for a user-scoped
   construction.** FAC discovery uses the same (`frappe_mcp.py:223`), and so does the approved-write
   dispatch (`actions.py:492`).
4. **Where it is stored.** Triton's Postgres `APIKey` table, `provider="erpnext_oauth"`,
   `encrypted_key` holding a Fernet-encrypted JSON blob of
   `{access_token, refresh_token, expires_at, erpnext_email}` (`frappe.py:126-131`;
   `auth.py:459-460`). Rows are read newest-first, deliberately (`frappe.py:46-60`). Refresh is
   proactive (`:198-209`) against `{base}/api/method/frappe.integrations.oauth2.get_token` (`:247`)
   using `FRAPPE_OAUTH_CLIENT_ID` / `FRAPPE_OAUTH_CLIENT_SECRET` (`config.py:183-184`). A dead grant
   (403/401/404) sets `_link_dead` and the next call raises `FrappeAuthRequired` (`:211-215`).
5. **Whose identity the ERPNext side runs as.** Frappe resolves an `Authorization: Bearer <token>`
   to the `OAuth Bearer Token` row's user and sets `frappe.session.user` to that person. Confirmed
   from *our* side, which reads exactly that: `assistant_tools/_gate.py:403`
   (`user = frappe.session.user`, the proposer of an AI Pending Action), `_gate.py:528`, and
   `assistant_tools/gating_api.py:23,69,82,100,130` (the confirming human). Stated as a design
   property at `assistant_tools/README.md:80-83`: *"the gate re-runs `execute` as the *confirming*
   user, so those checks bind to the human, not the AI's identity."*

**Is there per-user impersonation? No, and none is needed** — there is no `set_user`, no
`X-Frappe-User` header, no sudo. **The token is the identity.** Triton's own ADR states the choice
and its cost (`docs/decisions/0004-per-user-oauth-to-erpnext.md:21-23`): *"Triton talks to ERPNext
with **per-user OAuth2** credentials, so writes are attributed to the individual and constrained by
that individual's ERPNext permissions. A shared **API key** remains as a fallback for unattended
paths that have no user context."*

> `VERIFY: FAC's own request-authentication code — how frappe.session.user is set from the bearer
> token — settle by reading frappe_assistant_core/api/fac_endpoint.py on the prod bench, or by
> calling the MCP endpoint with two different users' tokens and diffing the visible tool list —
> blocks: nothing in this ADR; the conclusion is inferred from (a) Triton sending a per-user bearer
> and (b) erpnext_enhancements reading frappe.session.user inside the gate.`
> (`notes_triton_audit.md:2140-2145`)

#### B.10.3 THE UNCLOSED CONSEQUENCE — the I5 denylist lives in an app we do not own

**The per-user guarantee holds for ERPNext data reached through FAC. It says nothing about how we
keep chat DocTypes *out* of the generic MCP tools.** Phase 0 §4.J requires the existing
`run_database_query` / `get_document` / `list_documents` tools to **denylist chat DocTypes** — and
**those tools live inside FAC, an app in neither repo, whose import direction is strictly FAC → us.**
`notes_gap_report.md` §B-2 and `notes_register_reconciled.md` B2 both rank this blocking.

`notes_close_repo.md` §1 closed it, and the answer is **not** the one the critic expected. Three
findings, each load-bearing:

1. **The MCP surface is three surfaces with three different permission models.** Read from the live
   FAC `tools/list` catalog this session (`notes_close_repo.md` §1.1), quoting the tools' own
   descriptions: `get_document` and `list_documents` consult **DocPerm + the permission hooks**;
   `run_database_query` describes itself as *"Restricted to SELECT statements only. **Requires
   System Manager role for security.**"* — **a role check and a read-only-SQL check, and nothing
   else. Raw SQL consults no DocPerm, no `permission_query_conditions` and no `has_permission` at
   any point.** Our own `_gate.py:52-55` agrees and says only that it is read-only:
   ```python
   # Privileged-but-read-only: FAC enforces read-only SQL for run_database_query
   # (utils/read_only_db.py), so confirmation would be pure friction.
   EXPLICIT_READONLY = {
       "run_database_query",
   ```
   **So no permission mechanism Frappe offers can close `run_database_query`** — a System Manager
   remains one `SELECT text, sender FROM \`tabChat Message\`` away from every private message on the
   site, delivered into a model's context window. That is precisely what invariant I5 forbids.
2. **`Administrator` bypasses everything**, before any hook runs (§A.2.12).
3. **THE MECHANISM, named concretely: the `_gate.py` `_safe_execute` seam.** This app **already**
   intercepts every FAC tool call — FAC's own built-ins included — from inside
   `erpnext_enhancements`, without forking anything. `assistant_tools/_gate.py:1-16`:

   ```
   """AI write-confirmation gate for Frappe Assistant Core (FAC).

   Wraps ``BaseTool._safe_execute`` — the single choke point both FAC execution
   paths converge on (the legacy JSON-RPC handler via ``ToolRegistry.execute_tool``
   and the StreamableHTTP endpoint via ``mcp/tool_adapter``) …

   Why patch here and not ``execute_tool``: ``api/fac_endpoint`` calls
   ``_import_tools()`` (which imports this package) on every MCP request *before*
   dispatch, so a class-level wrap applied from ``assistant_tools/__init__`` is
   in place before any tool executes in a fresh worker — and ``tool_adapter``
   bypasses ``execute_tool`` entirely.
   """
   ```

   with `apply_gate()` at `_gate.py:557-591` (idempotent, self-guarding, **loud in the Error Log
   when a real FAC build renames the seam**) applied at import time by
   `assistant_tools/__init__.py:23-25`.

   **Five properties that make it the right seam**, all from `notes_close_repo.md` §1.5: it sees the
   **tool name and the raw arguments for every tool** (`_gate.py:500,524,525` — and both
   `get_document` and `list_documents` take a literal `doctype` argument, so the denylist is a
   string comparison, not a parse); it is **unconditionally in the request path**; **it runs even
   when `ai_write_gating_enabled` is off**, because the kill-switch is checked at `_gate.py:506-516`
   *after* the function is entered — so **a denylist branch inserted above `_gate.py:502` binds
   regardless of any settings flag**, which matters because *"a denylist that could be switched off
   from a settings form is not an invariant"*; it already **fails closed and logs loudly**
   (`:542-554`); and it already has an **upgrade canary** (`assistant_tools/README.md:38-40`, written
   against FAC v2.4.3).

**The recommended mechanism, stated as the ADR's answer:**

- **Layer 2 (the denylist itself, and the concrete answer to §4.J bullet 2): a
  `CHAT_DENYLIST_DOCTYPES = frozenset({...})` beside the existing `EXPLICIT_MUTATING` /
  `EXPLICIT_READONLY` / `APP_MUTATING` sets (`_gate.py:41-106`), plus a new FIRST branch in
  `_gated_execute` above `_gate.py:502`** that refuses when `arguments.get("doctype")` is in the
  denylist, and refuses when the tool is `run_database_query` (or `run_python_code`) and the
  normalised query/code text contains any denylisted table name. Refusals reuse `_error_response`
  (`_gate.py:488-494`, already FAC's expected `{"success": False, "error": …}` shape) and write an
  `AI Action Log` row through `insert_action_log` (`_gate.py:341-396`), **so an attempt to read chat
  through a generic tool is evidence, not silence.** The SQL rule is coarse and absolute — *if the
  case-folded, backtick-and-whitespace-stripped query text contains any chat table name, refuse the
  whole call.* **Do not attempt to allow "safe" queries**; over-refusal costs an analyst one
  rephrase, under-refusal costs the invariant.
- **Its test**, bench-free and therefore in CI (`notes_close_repo.md` §1.7): assert every chat
  DocType name appears in the denylist set (set-equality against the filesystem, in the shape of the
  existing `test_every_registered_tool_is_classified`); assert the gate refuses **with gating OFF**
  for `get_document`, `list_documents` and `run_database_query`, including the obvious evasions (no
  backticks, mixed case, a leading `/* comment */`, a `JOIN` in a subquery,
  `information_schema.columns` filtered on the table name), with a recording double proving the
  original was **never called**. It belongs on the existing multi-module step at `ci.yml:146-154`,
  which is the one place appending is correct (§A.6.1(C)).
- **A residual the ADR states rather than hides:** the seam's *attachment* is covered only by
  `test_ai_gating_integration.test_gate_marker_present`, which needs a bench and therefore **does
  not run in CI** (§A.6.4).
- **And a Phase 6 manual acceptance step**, because it cannot be written bench-free: open an MCP
  session as a real System Manager, issue `run_database_query` with
  ``SELECT COUNT(*) FROM `tabChat Message` `` and expect the refusal envelope, not a number.

#### B.10.4 ⚠ CONTRADICTION between two closing notes, which the ADR must NOT resolve silently

**`notes_close_repo.md` §1.6 recommends Layer 1 = "ship the chat content DocTypes with zero rows in
their `permissions` array".** **`notes_close_frappe.md` §1.7 shows that, taken literally, this makes
realtime chat over doc rooms impossible**: the doc-room join runs
`frappe.has_permission("Chat Room", doc=<room>, ptype="read")` under the *joining user's own*
session, and with no DocPerm rows `get_role_permissions` yields nothing, so **the join is silently
refused for every non-Administrator and the channel split dies with it.** Its recommended resolution
is the opposite of Layer 1: **give the chat DocTypes a minimal `read` DocPerm so `has_permission`
can succeed, and make the membership gate a `permission_query_conditions` + `has_permission`
pair** — which then also contains FAC's `get_document` / `list_documents`, because FAC resolves the
bearer to `frappe.session.user` and inherits the same filter a human gets.

Both notes are closing notes; neither trumps the other by the phase rules, and `DECISIONS.md` is
silent on it. **The two recommendations agree on everything except Layer 1, and they do not conflict
at all on the `_gate.py` seam — which is required either way, because neither DocPerm design touches
`run_database_query`.** The disagreement is settled by one cheap check:

> **`CQ` / `VERIFY (highest-value remaining check in Phase 0): does FAC's `run_database_query` (and
> `get_document` / `list_documents`) go through `frappe.get_list` — which applies
> `permission_query_conditions` — or raw `frappe.db.sql`, which does not?` Settle by reading
> `apps/frappe_assistant_core/frappe_assistant_core/**/tools/` **on the prod bench** (the GitHub raw
> URL 404s and DeepWiki has no per-tool detail). **What it decides:** whether chat DocTypes carry a
> minimal DocPerm plus a hook pair (`notes_close_frappe.md` §1.7) or zero DocPerm plus
> whitelisted-endpoint-only reads (`notes_close_repo.md` §1.6). **What it does NOT decide:** the
> `_gate.py` denylist, which is required under both readings.

**The ADR's position, stated so Phase 1 is not blocked:** build the `_gate.py` denylist
unconditionally; **default to the `notes_close_frappe.md` §1.7 shape** — minimal DocPerm + the
`permission_query_conditions`/`has_permission` pair, because it is the only shape in which the
realtime channel split works, and because `DECISIONS.md` D8's ten-and-ten parity doctrine already
requires the pair — and record `notes_close_repo.md` §1.6's zero-DocPerm variant as the fallback if
the FAC check comes back "raw SQL". Either way, **the KPI Snapshot coupling is mandatory**: the
moment any role is granted a read DocPerm on a chat DocType, **both** hooks land in the same commit
with a test modelled on `tests/test_kpi_snapshot_permissions.py`.

### B.11 `docs/convergence.md` — does any flag or ownership claim touch chat?

`docs/convergence.md` is the **single ownership matrix** between Triton and `erpnext_enhancements`
(`:6-8`); `docs/architecture.md` says *which Triton feature needs which erpnext_enhancements
version*, this one says *which system should be doing the work* (`:10-13`).

**Chat, messaging, presence and the embedded widget: NO. There is no row for any of them, and no
flag governs them.** Checked row by row and grepped on both sides
(`notes_triton_audit.md:1516-1524`).

**Notifications: partially, and only for voice.** The single notification claim is the incoming-call
row (`docs/convergence.md:34`), whose flag is **`VOICE_NOTIFY_EMAIL`** versus ERPNext's Call-Center
desk notification; Triton logs a startup warning when it detects both. It is about telephony alerts,
not chat notifications.

**The complete set of named flags in the document:** `BRIEFING_SCHEDULER_ENABLED` (Triton env,
default `True`; `False` hands morning-briefing generation to ERPNext's cron) · `VOICE_NOTIFY_EMAIL`
· *ERPNext Enhancements Settings → Require Confirmation for AI Writes* (**ships OFF**) ·
`COMPANY_KB_ENABLED` (Triton env, default `False`).

**And the maintenance rule, which binds this project** — `docs/convergence.md:66-70`:

> `When you add a cross-repo overlap (a feature that exists, or could plausibly run, on both
> sides), add a row here with an explicit owner. A second implementation without an entry in
> this table should be treated as a bug.`

> **A Google Chat surface is by definition a cross-repo overlap candidate. A new row in
> `docs/convergence.md` is MANDATORY, not optional** — and it is a *triton*-repo change, so it lands
> in whichever phase first ships a Triton-side line of code, under Triton's own version ritual
> (§B.12). The row's content is set by `DECISIONS.md` D3 and by locked decisions #1 and #5:
> **ERPNext owns chat storage, sync and notification; Triton owns only the `@triton` reply, acting
> as the mentioning user.**

**Google Chat's existing footprint in Triton is deploy notifications only.** A grep across the repo
matches **seven files, all deployment tooling** (`CHANGELOG.md`, `docs/deployment.md`,
`.github/workflows/deploy.yml`, `deploy/setup_alerts.ps1`, `deploy/MIGRATION.md`,
`deploy/notify.sh`, `deploy/deploy.sh`). It is a plain **incoming webhook** announcing deploy status
(`deploy/notify.sh:2-3,19,57,84-87`), URL held in a Secret Manager secret named by
`$DEPLOY_CHAT_WEBHOOK_SECRET` (default `triton-deploy-chat-webhook`), created by hand in the target
space's *Apps & integrations* UI. **There is no Google Chat API client, no Chat app/bot endpoint, no
Chat event handler and no Chat scope anywhere in `backend/app/`. Green field.**

> **Do not mistake it for reusable plumbing.** A space-scoped incoming webhook is **one-way and
> identity-less**: it posts into one space as an anonymous integration, and it **cannot** receive a
> mention, read a thread, or act as any user. Everything decision #5 needs — event delivery, thread
> reads, per-user attribution — requires a **Chat app registration** or per-user Google OAuth with a
> Chat scope, neither of which exists today.

### B.12 The version / changelog lockstep, and its two tests

**Three files must agree**, and `VERSION` is canonical:

| # | File | Current content |
|---|---|---|
| 1 | `VERSION` | `0.42.3` — 6 bytes, **no trailing newline** (re-read this session) |
| 2 | `frontend/package.json:3` | `"version": "0.42.3",` |
| 3 | `backend/app/main.py:272` | `version="0.42.3",` inside `FastAPI(...)` |

with the warning at `backend/app/main.py:26-27`: *"The `version=` argument to `FastAPI(...)` below is
one of the three places the version must match; `backend/tests/test_version.py` enforces it."*

**Test 1 — backend, `backend/tests/test_version.py`, the whole file:**

```python
"""Version-consistency guard (backend side).

Mirrors the frontend Vitest check: the canonical VERSION file, the FastAPI app
version, and the frontend package.json must agree. Pure file reads — no app
import, no services — so it always runs.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _canonical() -> str:
    return (ROOT / "VERSION").read_text().strip()


def test_backend_main_matches_version():
    main = (ROOT / "backend" / "app" / "main.py").read_text()
    m = re.search(r'version\s*=\s*"([^"]+)"', main)
    assert m, 'FastAPI(version="…") not found in backend/app/main.py'
    assert m.group(1) == _canonical()


def test_frontend_package_matches_version():
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text())
    assert pkg["version"] == _canonical()


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _canonical())
```

> **Sharp edge:** `re.search(r'version\s*=\s*"([^"]+)"', main)` takes the **first** `version="…"` in
> `main.py`. Introducing any earlier `version=` assignment in that file would silently hijack the
> guard.

**Test 2 — frontend, `frontend/src/__tests__/version.test.ts`**, a Vitest mirror of the same three
assertions, whose comment calls it *"the single highest-leverage test — it automates the mandatory
version bump and fails loudly the moment the three drift."* **Both are blocking in CI**:
`CLAUDE.md:45-50` records that frontend tests + the production build and backend `pytest` block,
while `vue-tsc` and `ruff` are `continue-on-error`.

**The edit set, every time:** `VERSION` (keep the no-trailing-newline convention) ·
`frontend/package.json` · `backend/app/main.py:272` · a new dated `## [X.Y.Z] - YYYY-MM-DD` heading
in `CHANGELOG.md` with `Added`/`Changed`/`Fixed`/`Removed`/`Deprecated`/`Security` subsections.
**A new endpoint — which a Chat bridge is — is a MINOR bump: `0.42.3 → 0.43.0`.** Docs must be
updated in the same change (`docs/README.md:33-36`), and a `docs-sync` skill exists to find which
doc a change touches.

### B.13 `sync_db_init` — the schema-bootstrap gotcha, and what adding a column requires

**Alembic is scaffolded but NOT wired in — confirmed.** `backend/alembic/versions/` exists and
contains **zero** migration files; `backend/alembic/README` states *"Alembic is scaffolded but NOT
yet wired into deploys"*; the ADR is
`docs/decisions/0002-startup-schema-bootstrap-over-alembic.md`.

`backend/app/main.py:52-124`, quoted (the `alter_cmds` body abbreviated only where noted; the
control flow complete):

```python
def sync_db_init():
    """Verify database connectivity and ensure core tables exist."""
    logger.info("Verifying database connection...")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

            # Ensure extensions exist before tables that depend on them
            logger.info("Ensuring database extensions exist...")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()

            # Ensure tables exist (Individual transaction)
            logger.info("Ensuring database tables exist...")
            Base.metadata.create_all(bind=conn)
            conn.commit()

            # Detect if pgvector extension is available to decide on the column type.
            has_vector = False
            try:
                res = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
                has_vector = bool(res)
            except Exception:
                pass
            embedding_type = f"vector({settings.VOICE_EMBED_DIM})" if has_vector else "JSON"

            # Individual ALTER statements with their own commits
            alter_cmds = [
                "ALTER TABLE chatmessage ADD COLUMN IF NOT EXISTS ui_metadata JSON;",
                "ALTER TABLE chatsession ADD COLUMN IF NOT EXISTS cached_content_name VARCHAR;",
                …
                # B4 tool budget: the FAC catalogue digest a session is pinned to,
                # so a mid-conversation ERPNext deploy does not rebuild its cached
                # prefix. There are no Alembic migrations in this repo by design
                # (ADR 0002), so the column goes here AND on the model.
                "ALTER TABLE chatsession ADD COLUMN IF NOT EXISTS fac_catalog_digest VARCHAR;",
                …
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_google_id ON \"user\" (google_id);"
            ]

            for cmd in alter_cmds:
                try:
                    conn.execute(text(cmd))
                    conn.commit()
                except Exception:
                    pass
        logger.info("Database connection and schema verified.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
```

The full list is `backend/app/main.py:80-113` — **30 statements**: 10 on `chatsession`, 1 on
`chatmessage`, 7 on `"user"`, 12 on `voicecallsession`, 1 unique index. It is driven from the
lifespan handler via `initialize_database()` (`:131-137`), which sets `DB_INIT_DONE` in a `finally`
— **so the flag proves the bootstrap *ran*, not that it succeeded** (`:10-12`).

**Adding a column requires TWO edits, and the second is the one people forget:**

1. Add the column to the SQLAlchemy model (e.g. `backend/app/models/chat.py`).
2. **Append a matching `"ALTER TABLE <t> ADD COLUMN IF NOT EXISTS <c> <type>;"` string to
   `alter_cmds` in `backend/app/main.py`** (currently ending at `:113`).

`CLAUDE.md:37-44` and `backend/app/main.py:8-10` both spell out the failure mode: **`create_all`
creates missing *tables* but never alters an existing one, so a model-only change works on a fresh
local database and silently does nothing in production.** The in-flight example to copy is the
`fac_catalog_digest` column, whose own comment says *"the column goes here AND on the model"*.

Two more rules: **never run `alembic revision --autogenerate` against a real database** — with no
baseline it diffs models against the live DB and will propose dropping everything absent from the
models (`CLAUDE.md:41-43`, `docs/decisions/0002-*.md:35-37`); and the mechanism is **additive only**
— it cannot express a data migration or a rename/drop, which ADR 0002 names as its revisit trigger.
Every `ALTER` is wrapped in a bare `except: pass` and `sync_db_init` logs rather than raises, so
**failures here are silent by design.**

### B.14 The scratch-file trap, and `pytest.ini`'s `testpaths`

`backend/pytest.ini`, the whole file:

```ini
[pytest]
testpaths = tests
addopts = -q
```

**`testpaths = tests`** — collection is confined to `backend/tests/`. `backend/conftest.py` exists
solely to anchor the rootdir; its entire content is a comment (*"Presence of this file makes the
backend directory the pytest rootdir, so the `app` package is importable from the tests without an
editable install."*).

**Nine Python files sit in `backend/` root and only `conftest.py` is test infrastructure:**

| File | Status |
|---|---|
| `backend/test_filter.py` | **scratch — despite the `test_` name.** Contains `frappe = FrappeClient()` at line 6, i.e. a live system-mode client. **Never collected.** |
| `backend/test_re.py` | **scratch** — a regex doodle. Never collected. |
| `backend/scratch_add_column.py`, `scratch_test_live.py`, `scratch_test_ws.py` | scratch |
| `backend/check_schema.py`, `check_user_db.py`, `frappe_sync_hook.py` | ops one-shots |
| `backend/conftest.py` | **the only real one** (rootdir anchor) |
| `backend/scratch/` | directory; contains `add_profile_columns.py` |

Two more scratch scripts sit at the **repo root**, outside `backend/`: `check_agents_status.py` and
`list_engines.py`.

> **Correction to `CLAUDE.md`, which a later phase would otherwise propagate.** `CLAUDE.md:51-52`
> lists `test_regex.py` among the scratch files. **`backend/test_regex.py` does not exist.** The
> real pair is `test_filter.py` and `test_re.py` (`notes_triton_audit.md:1882-1886`). Cosmetic, but
> an ADR that quotes `CLAUDE.md` here would ship a wrong filename into six downstream phases.

**The trap, stated for whoever writes a Chat-bridge test:** a file named `test_*.py` placed in
`backend/` root **looks collected and is not** — `testpaths` confines pytest to `backend/tests/`,
and there is no warning. The real suite is **19 modules** under `backend/tests/`
(`test_adk_toolbox`, `test_app_imports`, `test_calendar`, `test_deploy_env_completeness`,
`test_erpnext_token_refresh`, `test_fac_tool_snapshot`, `test_fountain_design`, `test_frappe_mcp`,
`test_gemini_cache_tools`, `test_merge_documents`, `test_personas`, `test_qbo_disconnect_landing`,
`test_qbo_token_rotation`, `test_tool_drift`, `test_tool_packs`, `test_tools_endpoint`,
`test_ui_command_relay`, `test_version`, `test_vertex_sdk_deprecations`). **A Chat-bridge suite goes
there**, mirroring `tests/test_erpnext_token_refresh.py` (~80 lines, per the §B.6.4 sizing). Note
also that the live-FAC check inside `test_tool_drift.py` is **opt-in**, skipped unless
`FRAPPE_MCP_ENDPOINT` and `TRITON_SNAPSHOT_USER` are set (`:186-187`).

---

## C. Prior art — the Raven decision

### C.1 The decision, in one sentence

**We do not adopt Raven as a dependency and we do not fork it; we reimplement the chat core inside
`erpnext_enhancements`, deliberately lifting Raven's proven schema decisions with attribution**
(`DECISIONS.md` D2).

### C.2 The reasoning

**Lead reason — there is no Raven line known-good on Frappe v16, and the evidence is four
independent strands that converge.**

1. **Raven's own desk bundle hard-refuses to render on Frappe 16 and 17.**
   `raven/public/js/raven.bundle.js`, fetched from `develop` on 2026-08-07:
   ```javascript
   $(document).on('app_ready', function () {
       if (frappe.boot.show_raven_chat_on_desk && frappe.user.has_role("Raven User")) {
           try {
               // If on mobile or on frappe v16, do not show the chat
               if (frappe.is_mobile() || frappe.boot.versions["frappe"].startsWith('16') || frappe.boot.versions["frappe"].startsWith('17')) {
                   return;
               }
   ```
   Introduced by commit **`a79c689`, 2026-01-08, "fix: do not show Raven chat on v16"**
   (`notes_raven.md:154-170`). **This is a deliberate disable, not an accident**, and it is the
   *only* embeddable artefact Raven has.
2. **Raven `develop` imports a realtime API that does not exist in v16.**
   `raven/realtime/handlers.py` (added 2026-06-11, commit `083425e`) opens with
   `from frappe.realtime import get_user_room, realtime` and uses `@realtime.on(...)` decorators
   with a `Socket` type (`notes_raven.md:172-190`).
3. **Frappe `version-16` does not have that API.** Fetched both this session by the Raven agent:
   `frappe/realtime.py` on `version-16` **exists as a single module** whose entire public surface is
   `publish_progress`, `publish_realtime`, `flush_realtime_log`, `clear_realtime_log`,
   `emit_via_redis`, `has_permission`, `get_socketio_secret`, `get_user_info`, `get_doctype_room`,
   `get_doc_room`, `get_user_room`, `get_site_room`, `get_task_progress_room`, `get_website_room` —
   **no `realtime` registry, no `.on()` decorator, no `Socket` class** — and
   `frappe/realtime/registry.py` on `version-16` returns **HTTP 404**, while the same path on
   `develop` exists as a package (`notes_raven.md:192-206`). **⇒ importing
   `raven/realtime/handlers.py` on a v16 bench fails.**
4. **Independent corroboration, obtained separately and for a different purpose.**
   `notes_gap_report.md` §0-3 fetched the same v16 `frappe/realtime.py` while closing the
   `publish_realtime` signature question and **enumerated the identical public surface** — arriving
   at the same conclusion without looking for it. Community corroboration too: the Frappe forum
   thread *"V16: Important apps not compatible yet"* (opened 2026-01-16) lists Raven as *"Not
   Compatible"* (`notes_raven.md:209-211`).

**Net: as of 2026-08-07 there is no Raven line known-good on Frappe v16.** The last release
(**v2.8.11, 2026-04-17**) predates the `handlers.py` commit and therefore does not carry *that*
specific blocker — but `develop` has had no commit since **2026-06-18**, there has been **no release
in ~3.7 months**, and active development has moved to an unreleased **`v3-alpha`** branch that was
committed to *the day of this audit* (`notes_raven.md:108-127`). **Adopting now means choosing
between a four-month-old release whose v16 status is unverified, a seven-week-stale `develop` that
cannot import, and an alpha branch — with a major version visibly incoming.** It puts this
project's schedule behind someone else's v16 port.

**Second reason, fully independent of the first: Raven's frontend owns a route and the page, and
locked decision #8 requires extending a desk-global widget that lives in *this* app.**

- Raven's build script literally copies its own SPA `index.html` over the Frappe website template:
  `"build": "vite build --base=/assets/raven/raven/ && yarn copy-html-entry"` and
  `"copy-html-entry": "cp ../raven/public/raven/index.html ../raven/www/raven.html"`
  (`notes_raven.md:731-741`). It mounts on `#root`, registers a service worker at
  `/assets/raven/raven/sw.js`, ships `website_route_rules` for `/raven/<path:app_path>` and
  `/raven_mobile/<path:app_path>`, and **exports nothing reusable or embeddable**
  (`notes_raven.md:745-772`).
- Its one embeddable artefact is a **second, smaller React app** (`raven/public/js/raven_chat/`)
  mounted into the desk's `.main-section` — and that is the artefact disabled on v16 (strand 1).
  **Raven maintains two React chat UIs and the smaller one is switched off on the version we run.**
- The widget we must extend is **1,404 lines of vanilla-JS IIFE** inside an esbuild bundle
  (§A.4.4, §A.5). **There is no shared substrate whatsoever.** Reusing Raven's frontend means either
  shipping React + Radix + TipTap + Firebase into the ERPNext desk bundle to rewrite Raven's
  disabled widget, or abandoning decision #8 and sending users to `/raven`.

**So under every option we write the widget UI ourselves.** Once that is true, adopting buys a
backend we must reach through someone else's ~24 API modules, on a Frappe version it does not
support, with a v3 rewrite pending.

### C.3 Rejected alternatives, treated separately

#### C.3.1 Rejected: adopt Raven as a bench dependency

**What it would buy, stated fairly and at full strength — this is the option's real case:** we would
not write a chat backend at all. Channels, DMs, threads, mentions, reactions, polls, pinned
messages, file handling with blurhash thumbnails, link previews, search, typing indicators and push
all exist and are battle-tested in a ~3.5-year-old product with 746 stars and a commercial sponsor.
The schema is genuinely good (§C.4). `linked_doctype` / `linked_document` on **both** Channel and
Channel Member means **per-document chat spaces are already modelled** — a direct fit for decision
#10. `Raven Webhook`'s trigger taxonomy (Message Sent / Edited / Deleted / Reacted On, Channel
Created / Deleted, Channel Member Added / Deleted, User Added / Deleted) is a ready-made outbound
event surface we could drive sync from **without touching Raven's code**. Upstream keeps fixing bugs
we would otherwise own. **And AGPL is not a problem in this mode** — separate bench apps are
ordinary aggregation and we would write no derived code.

**Why it is rejected anyway, in order of decisiveness:**

1. **It does not install.** §C.2 strands 1–4. This is a hard stop unless the v2.8.11 experiment
   comes back clean.
2. **The widget constraint is unsatisfiable** (§C.2 second reason). We would be writing a new chat
   UI inside the Triton widget that talks to Raven's ~24 API modules — **most of the frontend work
   with none of the frontend savings, plus a permanent API-compatibility tax across Raven
   upgrades.**
3. **Sync state has nowhere clean to live.** `Raven Message` has **no external message id, no
   idempotency key, no `edited_at`, no per-recipient delivery state** — `is_synced` is a bare Check
   with no counterpart id (`notes_raven.md:390-392, 1131-1146`). We would add Custom Fields to a
   third party's DocTypes on the eve of their v3, or maintain a sidecar mapping table and reconcile
   it. **And Raven's own `after_insert` writes the channel summary with raw `frappe.qb`, bypassing
   the ORM**, so some of its writes do not fire hooks we might rely on (`notes_raven.md:914-918`).
4. **Its dependency tree collides with a documented constraint.** Raven requires
   `pandas`, `google-cloud-documentai`, `openai>=2.30.0`, `openai-agents>=0.17.2`, `markitdown`,
   `linkpreview`, `blurhash-python` (`notes_raven.md:225-238`). The production host is a managed
   server where packages **cannot be pip-installed** — the documented reason `stripe_payments`
   hand-rolls the Stripe REST calls (`decisions/adr/0004-no-vendor-sdks.md`), and
   `notes_infra.md:971-977` found **no `pip install` step anywhere in the deploy pipeline**.
5. **It adds a second global `doc_events["*"]`** on five events, on top of ours
   (`notes_raven.md:1023-1044`) — so every document write site-wide pays an extra Redis `hget`, and
   it lands in the same ERPNext-test-bootstrap path §A.9 describes.
6. **Audit has a hole we did not open.** Raven broadcasts `raven:unread_channel_count_updated` to a
   **global** room `"doctype:Raven User"` carrying `channel_id`, `sent_by` and
   `last_message_timestamp` **for channels the recipient is not a member of**
   (`notes_raven.md:876-904`). Message content is not included for non-DM channels, but existence,
   activity timing and author leak to non-members — **in code we do not control**, against a
   governance story of *"every non-participant read is audited"*.
7. **Oversight needs a parallel read path and a parallel UI anyway.** Raven's
   `permission_query_conditions` are a deny-by-default backstop (`owner = user`), Frappe v16 combines
   multiple query conditions with **AND** and `build_match_conditions` has no Administrator
   short-circuit, so a sibling app **can only tighten, never loosen** (`notes_raven.md:609-637`).
   The clean path exists — our own endpoint reading `tabRaven Message` directly — but then we own
   the oversight UI too.

#### C.3.2 Rejected: fork Raven

**What it would buy:** we could remove the v16 blocker on our own schedule (revert
`realtime/handlers.py` to `publish_realtime`-only, delete the v16 guard in `raven.bundle.js`), make
oversight a first-class role by editing `raven/permissions.py` directly, strip the dependencies we
cannot install (all three heavy ones are AI-feature-only, and `raven_ai` could be deleted outright),
and add external-id fields to the schema properly instead of bolting them on.

**Why it is rejected, and the first reason is dispositive on its own:**

1. **The licence forecloses it.** Raven is **AGPLv3**; `erpnext_enhancements` is **`app_license =
   "mit"`** (`hooks.py:26`, re-verified this session). **A fork must stay AGPLv3 and must stay a
   separate app — no part of it may be folded into `erpnext_enhancements`.** So we would take on all
   the maintenance burden of a fork and **still** have two apps and two UIs. And if anyone ever
   copies a Raven function across "just this once", **the MIT licence on the whole app becomes
   false.** Note also AGPL §13's network clause. *(`VERIFY: whether AGPL §13 obligations for an
   internal-only deployment are material — a legal read, not an engineering question — blocks:
   only the fork option, which is rejected on other grounds too.)*
2. **It does not solve the widget problem at all** — forking the backend does nothing about
   `triton_widget.js` being vanilla JS and Raven's UI being React.
3. **Team size and surface area.** A ~94k-line ERPNext customisation is already the maintenance
   load; adding a forked chat platform with its own React SPA, its own mobile app, a service worker
   and a Firebase integration is a step change for a ~20-user deployment.
4. **The changelog discipline breaks.** `CLAUDE.md` mandates a version bump plus a changelog entry
   on every change to `erpnext_enhancements`; a vendored AGPL fork sits outside that ritual or
   forces a second one.

**Patterns and schema *shapes* are not copyrightable; code is.** That distinction is what makes §C.4
legitimate and forking unnecessary.

### C.4 What we lift from Raven's schema, and why each is proven

Every row was verified against Raven's own DocType JSON or controller this session by
`notes_raven.md`; the "verdict" column records what that verification found, including where it
**refuted** the reported claim.

| # | What we lift | Raven's evidence | Verdict, and why it is proven |
|---|---|---|---|
| 1 | **`autoname: "hash"` on the message DocType** | `raven_message.json` — `"autoname": "hash"`, `"naming_rule": "Random"` | **CONFIRMED**, and also used on `Raven Channel Member` and `Raven Message Reaction`. Directly satisfies Phase 0 §4.G's ban on naming series for messages (*"series counters serialize inserts"*), and matches this repo's own `Drive Sync Log` (§A.11.5). **REFUTED for the channel**, which uses `naming_rule: "By script"` and a human-readable slug — so we do **not** lift that half |
| 2 | **Separate `Channel` / `Message` / `Channel Member` DocTypes, not child tables** | all three are top-level DocTypes with their own permissions, indexes and query hooks | **CONFIRMED.** Child tables are reserved for genuinely subordinate data (`Raven Mention`, `Raven Pinned Messages`, `Raven Poll Option`). This is the choice that makes per-row permissions and indexed membership possible at all |
| 3 | **Realtime for message content scoped to the channel DOC ROOM** | `frappe.publish_realtime("message_created", {...}, doctype="Raven Channel", docname=self.channel_id, after_commit=…)` | **CONFIRMED for content**, and **REFUTED as a blanket claim** — Raven is a hybrid, and its unread/list/member signalling is a **global broadcast** (§C.3.1 item 6). **We lift the content half and explicitly reject the global half.** Independently supported on our side: doc-room joins *are* permission-checked in v16 (`notes_close_frappe.md` §1) |
| 4 | **Denormalised `last_message_timestamp` + `last_message_details` (JSON) on the room** | both fields exist read-only on `Raven Channel`, written by `set_last_message_timestamp()` via `frappe.qb` direct SQL | **CONFIRMED.** This is what makes a room list render without N queries. Note the trade-off Raven accepted and we must decide consciously: writing via `frappe.qb` **bypasses the ORM and therefore does not re-fire channel hooks** |
| 5 | **Mentions as a child table plus a per-user realtime event** | `mentions` Table → `Raven Mention` (`istable: 1`, exactly one `user` field), and `extract_mentions()` fires `publish_realtime("raven_mention", …, user=mention_id, after_commit=True)` | **CONFIRMED, exactly as reported.** It is also the shape our own §A.11.1 group (B) already uses for `ai_pending_action` |
| 6 | **`linked_doctype` / `linked_document` on the room** | present on **both** `Raven Channel` *and* `Raven Channel Member`, all `read_only: 1` | **CONFIRMED and stronger than reported** — per-document spaces *and* per-document membership derivation are both already modelled. This is decision #10's schema, proven in production by someone else |
| 7 | *(supporting)* **`last_visit` Datetime per (channel, user) as the entire read-state model** | `Raven Channel Member.last_visit`, `reqd`, `fetch_if_empty: 1`; unread is a `COUNT(*)` per request | **CONFIRMED**, with the honest caveat that Raven has **no materialised counter and no per-message read receipts** (open issue #1199 since 2024-12-28), and its unread query has two code smells we must not copy (a `.left_join` chained after the `WHERE`s that reference it, behaving as an inner join; and a `"2000-11-11"` magic sentinel) |
| 8 | *(supporting)* **Membership as a redis-cached map behind `has_permission`** | `get_channel_members(channel_id)` caching a `{user_id: member}` dict, invalidated by `delete_channel_members_cache` | **CONFIRMED.** This is the piece that keeps a per-message permission check cheap, and §A.11.3 shows this repo has the Redis primitives but no equivalent map |
| 9 | *(supporting)* **The outbound event taxonomy** | `Raven Webhook.webhook_trigger` options: Message Sent, Message Edited, Message Deleted, Message Reacted On, Channel Created, Channel Deleted, Channel Member Added, Channel Member Deleted, User Added, User Deleted | **CONFIRMED.** Not schema — a **checklist**: it is the complete list of events a bidirectional mirror must handle, arrived at by a mature product |

**Two things we deliberately do NOT lift**, both because they are Raven's *weaknesses*: the global
`"doctype:Raven User"` broadcast (§C.3.1 item 6), and `Raven Incoming Webhook`, which is
`@frappe.whitelist(allow_guest=True)` where **the webhook document name parsed from the URL path is
the only credential** and the handler inserts a message with permissions ignored
(`notes_raven.md:1114-1124`) — the same class of gap `PLAN.md:44` already records for the QuickBooks
Time webhook.

**Threads, for completeness:** Raven models a thread as a **`Raven Channel` with `is_thread = 1`
whose primary key equals the parent `Raven Message`'s hash name** (`notes_raven.md:459-471`).
Elegant — threads reuse membership, unread and permissions wholesale — but it mixes three entity
kinds under three naming schemes in one table. We record it as *considered*; whether we adopt it is
a Phase 1 schema decision, and it interacts with §E's finding that **Google Chat threading may not
be available to us at all**.

### C.5 The cost, stated honestly

**We own the chat core outright** — rooms, threads, membership, unread, mentions, reactions,
attachments, search and the widget UI — and that is **more code than adopting**, not less. The ADR
must not pretend otherwise. Specifically:

- **We will rediscover bugs Raven already fixed**, especially in notifications (releases 2.6.4,
  2.6.5, 2.6.6, 2.8.6, 2.8.8, 2.8.9, 2.8.10 and 2.8.11 all carry notification fixes), ordering, and
  attachment handling.
- **Features Raven gives free are scoped out or deferred**: polls, custom emoji, link previews,
  blurhash thumbnails, a mobile app.
- **The single largest risk is scope creep into "we are building Slack."** The mitigation is a hard
  scope line the ADR must draw and Appendix B must hold: **this is a Google-Chat-mirrored,
  ERPNext-source-of-truth messaging system with an oversight and audit layer — not a chat
  platform.** Where locked decision #1 makes ERPNext the source of truth, the composition surface is
  ours and cannot be scoped away; everything beyond it (polls, emoji, previews) is explicitly out.
- **The realtime and unread work is easy to under-estimate.** Raven needed a hybrid room strategy, a
  cached membership map and a denormalised channel summary to feel instant. **Lift their design; do
  not improvise it.**

### C.6 The Raven-on-v16 VERIFY, recorded as non-blocking

> `VERIFY: whether Raven v2.8.11 installs and runs on a Frappe v16 bench — settle by
> `bench get-app --branch v2.8.11 raven` against a throwaway v16 site, then `install-app` +
> `migrate` + a smoke test of `/raven` — blocks: only the adopt option.`

**`DECISIONS.md` D2 records this as *not phase-blocking under this decision*, and D2 is binding.**
`notes_gap_report.md` §E ranks it phase-blocking item #3; `notes_register_reconciled.md` C2 carries
that disagreement forward and resolves it the same way. **The divergence is recorded here rather
than resolved silently**, and the reason D2 wins is structural: the case against adopting rests on
**two independent grounds**, and the experiment only touches one of them. Even a clean "v2.8.11 runs
fine on v16" leaves the widget constraint (§C.2 second reason), the dependency-install constraint
(§C.3.1 item 4) and the imminent v3 untouched.

**What would have to change for adopt to be reconsidered** — all three, together:

1. The v2.8.11-on-v16 experiment comes back clean **and** the managed host proves able to install
   `pandas`, `google-cloud-documentai` and `openai-agents`; **and**
2. locked decision #8 is relaxed from *"extend the existing floating widget"* to *"link out from the
   existing widget to `/raven`"*; **and**
3. the human accepts a second chat UI in the product, an upstream v3 migration on someone else's
   schedule, and the metadata side-channel in §C.3.1 item 6 as a documented governance exception.

If all three hold, adopt becomes the better value — §C.3.1's opening paragraph is the honest case
for it, and `notes_raven.md:1338-1346` reaches the same conclusion independently. **That is a
`CQ-n` item for the human, not an engineering call**, and it is cheap to keep open because nothing
in Phase 1's schema work is wasted either way: the lifted patterns (§C.4) are the same patterns
Raven itself uses.

---

## D. App placement

### D.1 The decision, in one sentence

**Chat ships as a new, self-contained `chat/` module inside `erpnext_enhancements`, not as a sibling
Frappe app** (`DECISIONS.md` D1).

### D.2 The reasoning, drawn from the repository audited in §A

**Strongest single reason — locked decision #8 makes a sibling app structurally unable to do the
job.** Decision #8 requires *extending* the existing floating widget. That widget is a **desk
global** loaded from `erpnext_enhancements/hooks.py:58` inside `erpnext_enhancements.bundle.js`,
which `import`s `./global_enhancements/triton_widget.js` (§A.5, the seven-step chain). **A sibling
app can extend it in exactly two ways, and both are forbidden here:** fork it into the second app
(forbidden by #8, and it immediately doubles the FAB problem in §A.2.2), or reach into another app's
esbuild bundle at runtime — which is precisely what `decisions/adr/0008-global-assets-ship-as-bundles.md`
exists to prevent, and which the raw-`/assets`-path prohibition makes unreviewable.

**Supporting reasons, each an artifact of the audit rather than a preference:**

1. **The retrieval gate that decision #6 needs must sit next to `assistant_tools/_gate.py`.** §B.10
   establishes that the *only* mechanism able to keep chat DocTypes away from FAC's
   `run_database_query` is a branch inserted into `_gated_execute` above `_gate.py:502`, in a package
   that FAC imports on every MCP request. **That package is
   `erpnext_enhancements/assistant_tools/`.** A sibling app cannot patch it without importing it —
   and `assistant_tools/README.md:49-59` forbids importing it from app code, tripwire-tested. **This
   is not a convenience argument; it is the invariant-I5 argument, and it is decisive on its own.**
2. **The presence and realtime precedent is here.** `api/collab.py` + `public/js/collab/live_form_sync.js`
   is the only heartbeat/TTL presence implementation in the estate (§A.11.3), the `Collab Doctype`
   allowlist is the only realtime registry, and `api/collab.py` is the file a chat relay most
   naturally extends.
3. **The Google client libraries already import in production.** `google-api-python-client` and
   `google-auth` are declared dependencies (`pyproject.toml:11-16`) and both
   `googleapiclient.discovery` and `google.oauth2.service_account` were **measured importing on the
   prod bench** (`notes_infra.md:990-1005`). `google_calendar/calendar_utils.py` is the worked
   example of adding a *new Google API surface* by adding a scope and a `build(...)` call (§A.1a).
   **A sibling app would either duplicate that credential plumbing or import across an app
   boundary.**
4. **Everything the release process needs already exists here and would have to be duplicated
   wholesale**: the `version-sync` hard gate and `release.yml` (§A.6.5), the fixtures pipeline
   (§A.8), the `unittest`/`pytest` CI split with its 43 steps and its documented cross-talk hazards
   (§A.6.1), `scripts/check_www_controllers.py`, `scripts/check_import_dirs.py`, and the
   `hook_targets_resolve` guard that already cost a production build once.
5. **The deploy is single-repo.** `infra/cloudbuild-deploy.yaml:35-36` does
   `git -C apps/erpnext_enhancements fetch upstream main && reset --hard FETCH_HEAD` — **one app,
   one path.** A second app needs a second deploy path, and this is the same reason §B.10 rejects
   forking FAC.

### D.3 The rejected option, and why

**Rejected: a sibling `sapphire_chat` Frappe app.**

**What it would genuinely buy** — and this is a real case, not a straw man: a clean blast radius for
high-write tables (chat's insert volume never touches the ERP app's own migrate or fixture surface);
an independent release cadence, so a chat hotfix does not carry 94k lines of unrelated app with it;
a hard module boundary that cannot be eroded by a convenient import; and a natural future path to a
separate database or a separate release train if volume ever demands it.

**Why it is rejected:**

1. **It cannot satisfy decision #8** (§D.2, strongest reason).
2. **It cannot host the I5 denylist** (§D.2 item 1).
3. **Deployment is single-repo `main` → GCP**, so a second app adds an **install/migrate ordering
   dependency** — chat's DocTypes must exist before any `erpnext_enhancements` code references them,
   and `after_migrate` ordering is already load-bearing in three places in one app (§A.2.9) —
   **without removing a single shared failure mode.** Both apps still share the bench, the queue
   Redis that the deploy FLUSHDBs, the same GCLB with its 30 s backend timeout, and the same
   database.
4. **Cross-app `has_permission` costs more than the boundary buys at this size.** §A.2.12 and §B.10
   put the chat permission decision inside the same file as the KPI Snapshot and Training doctrines;
   splitting it across an app boundary means a chat `has_permission` hook in one app and the tools it
   must contain in another. And per `notes_raven.md:609-637`, Frappe combines multiple
   `permission_query_conditions` for one DocType with **AND** — a cross-app arrangement can only ever
   tighten, never coordinate.
5. **The measured population does not justify it.** ~20 enabled users, not ~50
   (`notes_register_reconciled.md` C4).

**Revisit trigger, stated numerically so it is testable rather than aspirational:** revisit if chat
write volume forces a separate database or a separate release train — concretely, if the chat tables
exceed the largest existing table by an order of magnitude, if `bench migrate` duration becomes
chat-dominated, or if a chat hotfix cadence starts colliding with the ERP release cadence often
enough that someone proposes branching. None of those is near today.

### D.4 The factors §4.E names, answered one by one

| Factor | Answer |
|---|---|
| **The widget constraint** | Decisive **for** extending. The widget is a desk global in this app's bundle (`hooks.py:58` → `erpnext_enhancements.bundle.js` → `triton_widget.js`); a sibling app can only fork it or violate ADR 0008 (§D.2) |
| **The ~94k-line app's version/changelog gate and CI** | Extending means **every chat PR pays** the version bump + CHANGELOG ritual and the four CI jobs (§A.6, §A.7). That is a real per-PR tax — and it is a tax we already pay and already have tooling for (`/release-prep`). A sibling app would need its own copy of all of it, including the 43-step `unit-tests` job and its documented stub cross-talk rules |
| **Chat's write volume** | The genuine argument *for* separation, and the only one. Mitigated inside one app by: **CHAT-EXCL-1** (excluding the chat module from the wildcard `after_save` Triton webhook, §A.2.6), a **retention purge from day one** matching the five existing `purge_old_*` jobs (§A.2.7), and the outbox living **on the `Chat Message` row itself** rather than in a second high-write log table (§A.11.5). Note the concrete measured baseline the estate is starting from: `tabNotification Log` reached 33.2 MB / 7,165 rows over 13 months with **no retention rule at all** (`notes_infra.md`, via `notes_gap_report.md` §E-14) — two notifications per chat message across ~20 users changes that curve by orders of magnitude, which is why the purge is Phase 1, not Phase 6 |
| **Chat's DocType count** | Small against the existing 187 (§A.3). The Phase 1 set is `Chat Room`, `Chat Message`, `Chat Room Member` plus a mention child table and a chunk/embedding DocType — roughly 5–6, comparable to `Accounting Intake` (5) or `Google Drive` (4). **It does not move the module map.** Every one must sit under `erpnext_enhancements/chat/doctype/` with `module: "Chat"` registered in `modules.txt`, or `tests/test_doctype_modules.py` fails the build (§A.3) |
| **Does a separate app make deploy/rollback/bench-migrate safer or riskier?** | **Riskier.** Safer in exactly one respect — a chat migration failure would not abort the ERP app's own `bench migrate`. Riskier in three: a second install/migrate ordering dependency; a second deploy path that `infra/cloudbuild-deploy.yaml` does not currently have; and a **split rollback** — `git reset --hard` on one app while the other stays forward leaves cross-app hooks pointing at code that is not there, which is exactly the failure class `test_hook_targets_resolve` exists to catch **within** one app and cannot catch **across** two |
| **How would fixtures and custom fields split?** | **They would split badly, and this is more concrete than it first appears.** Fixtures are per-app: a sibling app gets its own `fixtures/` directory and its own `fixtures` hook, and **fixture sync runs per app in alphabetical filename order within each app** (§A.8). A chat Custom Field on `User` or on `Project` would then be **owned by the chat app while every other Custom Field on those DocTypes is owned by `erpnext_enhancements`** — two apps writing customizations to the same standard DocType, with the `hooks.py:894-905` exclusion list in one app now needing to *not* swallow the other app's records. That is precisely the failure `custom_field_hrms.json` exists to prevent, at a larger scale. **Inside one app, chat's Custom Fields are ordinary rows in `custom_field.json` subject to the same two-step deletion rule as the other 513** |

### D.5 Where chat lives, and how it registers

**Module path:** `erpnext_enhancements/chat/`, a Frappe module named **`Chat`**, with
`erpnext_enhancements/chat/doctype/<scrubbed>/` for each DocType, per the mapping
`tests/test_doctype_modules.py:3-8` asserts.

**How it registers — the complete checklist, each item traceable to §A:**

1. **`modules.txt`** gains a `Chat` line — required by `tests/test_doctype_modules.py`, which asserts
   both the directory mapping *and* the `modules.txt` registration (§A.3).
2. **`hooks.py`** gains, at minimum: `doc_events` for the chat DocTypes; `scheduler_events` entries
   for the outbox sweeper (a **`cron`** slot staggered clear of the :00/:20/:40 QBO and 05:00–07:15
   backup clusters, §A.2.7) and the retention purge (`daily`); a `permission_query_conditions` entry
   **and its `has_permission` twin** (§A.2.12, §B.10.4); `ignore_links_on_delete` if messages link to
   documents (§A.2.13); and `website_route_rules` — **the app's first** — for
   `/chat/<path:chat_path>` (§A.10.1). Every entry carries an annotation, per `hooks.py:9-15`.
3. **`public/js/chat/` and `public/css/chat/`**, per the namespacing rule (`README.md:243`), with the
   widget extension `import`ed into `public/js/erpnext_enhancements.bundle.js` and the stylesheet
   imported into `public/css/desk_addons.bundle.scss` — **never a new `app_include_*` line and never
   a raw `/assets` path** (§A.2.1, §A.5).
4. **`erpnext_enhancements/chat/README.md`**, kept in sync with `hooks.py` — `README.md:266`,
   `CLAUDE.md`, `hooks.py:17-18`. It must **not** be placed in any directory
   `scripts/check_import_dirs.py` treats as importable (§A.7).
5. **`api/chat.py`** (4-space or tabs — match whichever the neighbouring file uses, §A.7) for the
   whitelisted endpoints, plus a `_check` in `api/integrations_health.py` and a row in its registry
   at `:405-412`, reporting secrets only as `configured: true/false` (§A.1b, `api/README.md:27,62`).
6. **`utils/triton_sync.py:30-33`** gains `"Chat"` in `excluded_modules` — **CHAT-EXCL-1**, in the
   same commit as the first chat DocType (§A.2.6).
7. **A settings section on `ERPNext Enhancements Settings`** carrying the master switch, the
   `restrict_to_whitelist` gate and the retention days — shipping **dormant** (§A.4.5, §A.11.7 item
   7), with a **`ee_chat`** flag in `boot.py` and nothing else (§A.2.8).
8. **A `Chat User` / `Chat Auditor` Role seeded by a patch, not a fixture**, and reaching profiled
   users through a Role Profile (§A.8).
9. **CI steps**: a bench-free pytest step per suite naming exactly one file, plus the denylist test
   appended to the existing `ci.yml:146-154` multi-module unittest step (§A.6.1).
10. **`docs/convergence.md`** gains a chat row **in the triton repo** (§B.11) — mandatory, not
    optional.

---

## E. Google auth

**Everything in this section was verified against `developers.google.com`,
`docs.cloud.google.com` or `knowledge.workspace.google.com` on **2026-08-07**, by
`notes_google_verify.md` and `notes_close_google.md`. Google ships to these pages continuously —
there is a release-note entry dated that same day — so treat every fact as having a short shelf life
and re-check before Phase 1 writes code. Quotes are faithful to page content; byte-exact whitespace
is not guaranteed. **No live Google API call was made and no Admin console was opened in Phase 0**,
so every step whose *execution* would settle a question is written as a settlement method, not as a
result.

### E.1 The trade-off table

Four identities are available. Three are real candidates; the fourth is included because it exists
in the estate today and must be explicitly ruled out.

| | **(1) DWD / user auth** (service account impersonating the human) | **(2) Chat app / app identity** (`chat.bot`) | **(3) Per-user 3LO OAuth** (each coworker consents individually) | **(4) Incoming webhook** (what Triton's deploy notifier uses today) |
|---|---|---|---|---|
| **Message attribution** | **The real human.** *"With user authentication, the user sends the message, and Chat displays the Chat app name next to the user's name"* — so it is **not** invisible impersonation; a *"via <app name>"* attribution is visible (`create-messages`; `authenticate-authorize-chat-user`) | **The app, with an `App` badge.** *"With app authentication, the Chat app sends the message. To note that the sender isn't a person, Chat displays `App` next to its name"* (`create-messages`) | **The real human**, same as (1) — DWD *is* user authentication (`authenticate-authorize-chat-user`: *"Although a service account is used for authentication, domain-wide delegation impersonates a user and is therefore considered user authentication"*) | Anonymous integration, one identity per space. **Cannot act as any user** |
| **Rich-card support (`cardsV2`)** | **No, at GA.** User auth supports *"only text (`text`)"* (`spaces.messages/create`). **Correction to the brief:** cards under user auth exist in **Developer Preview** — *"In Developer Preview, you can also send cards"* (`create-messages`). Do not architect on it; note it as the future unlock | **Yes** — *"text (`text`), cards (`cardsV2`), and accessory widgets (`accessoryWidgets`)"* (`spaces.messages/create`) | Same as (1) — text only at GA | Cards yes, but see the identity and inbound columns |
| **Key management** | **Keyless, and it is Google's own recommendation.** Two service accounts (VM-attached + delegation) with `roles/iam.serviceAccountTokenCreator` between them; assertions signed by IAM Credentials `signJwt`. **Zero private-key files on disk, zero rotation, zero secret in `site_config.json`.** Google: *"**avoid service account keys and use the `signJwt` API instead**"* (`best-practices-for-managing-service-account-keys`), repeated on the Workspace side (`domain-wide-delegation-best-practices`). Cost: ~50 hand-rolled lines (§E.4) | **One OAuth client, no key**, if the app uses its own service-account credentials with `chat.bot`. No rotation story surfaced in the docs read this session. `VERIFY: what credential material a Cloud-console-configured Chat app actually holds for `chat.bot` calls, and whether it can also be keyless via the same signJwt path — settle by reading the Chat app auth quickstart with the app's own SA — blocks: only the operational runbook, not the design` | **Refresh tokens, one per user, ~20 of them, stored encrypted by us** — the same storage problem Triton already solved once for ERPNext OAuth (`backend/app/core/frappe.py:126-131`, Fernet-encrypted blobs). Revocable by the user at any time, and **individually** | Webhook URL **is** the credential, held in Secret Manager (`deploy/notify.sh:16-21`). Long-lived bearer-in-a-URL |
| **Admin approval burden** | **One super-admin action, plus propagation.** *"You must be signed in as a **super administrator** for this task"*, at **Security → Access and data control → API controls → Manage Domain Wide Delegation → Add new → Authorize → View details**, and *"**Changes can take up to 24 hours but typically happen more quickly**"* (`control-api-access-with-domain-wide-delegation`). **Plus** App Access Control marking the OAuth client **Trusted** — required, because internal apps are **not** trusted by default (§E.5.2 step 8) — whose console listing lags **24–48 hours** | **Zero admin approval for `chat.bot`** — *"Google Chat API methods that support app authorization with the `chat.bot` authorization scope don't require additional approval"* (`authenticate-authorize-chat-app`). **But any `chat.app.*` scope requires one-time super-admin approval**, and the only documented path for it is a **Google Workspace Marketplace admin-install of a published app** (§E.5.2 step 11) | **Zero admin actions; ~20 individual consent flows**, plus **re-consent on every scope change**. Note the interaction: with DWD or a Trusted app, *"your app will either receive all requested scopes or none"* — a 3LO app instead faces the granular-permissions consent screen per user | Zero admin actions; created by hand in each space's *Apps & integrations* UI |
| **Revocation failure mode** | **Atomic and audited, and it is the best of the four.** Revoking is one `gcloud iam service-accounts remove-iam-policy-binding` on **one** resource — no key to hunt for, no rotation, and the change is in the IAM audit log. Contrast a downloaded key, revoked by deleting the key *and hoping no copy exists*. Per-user failure is **`USER_SCOPE_REVOKED`** on a Workspace Events subscription — *"The authorizing user has revoked the grant of one or more OAuth scopes"* — which **suspends** the subscription (`reactivate-subscription`) | Revoking the app's scopes or blocking it in App Access Control kills **every** space at once. Blunt but complete | **~20 independent revocation surfaces.** Any coworker can revoke in their own Google Account settings, silently, and the first symptom is a suspended subscription or a 401 mid-relay | Rotating the URL is manual, per space, and there is no per-user granularity at all |
| **Operations it CAN perform** | Post as the human; **edit and delete that human's own messages** (app auth *"can only update/delete messages created by the calling Chat app"* — `spaces.messages/patch`, `/delete`); **upload attachments** (`media.upload` — *"**Requires user authentication**"*, scopes `chat.messages.create` / `chat.messages` / `chat.import`; **`chat.bot` is absent**); create per-user `spaces/-` Workspace Events subscriptions (the only **GA** way to see coworker messages in spaces the app is not in); set that user's own `spaceNotificationSetting` | Post as the app **with cards**; **`NOTIFICATION_TYPE_SILENT` and `NOTIFICATION_TYPE_FORCE_NOTIFY`** (both *"Requires app authentication"*); **download** Chat-hosted attachments (`chat.bot` **is** on `media.download`); receive interaction events; per-space subscriptions **with `chat.app.*` + admin approval** | Everything (1) can, per consenting user | **Post into one space, one way.** Cannot receive a mention, cannot read a thread, cannot act as any user |
| **Operations it CANNOT perform** | **No `cardsV2` at GA**; **no `NOTIFICATION_TYPE_SILENT`**; cannot act for a user who has not been granted delegation | **Cannot upload attachments at all** (`media.upload` requires user auth); cannot post as a human; cannot create a `spaces/-` all-spaces subscription (*"Only supports user authentication"*) | Nothing structural — but it cannot act for a user who has not consented, and it cannot be provisioned centrally | Everything except posting |
| **Verdict** | **Chosen** for the human relay, edits, deletes and attachment uploads | **Chosen** for Triton's `@triton` replies only | **Rejected**: ~20 consent flows plus re-consent on every scope change, with no central provisioning and ~20 revocation surfaces. Recorded because Google itself recommends preferring it — see §E.4.3 | **Rejected**: identity-less and one-way; keep it for deploy notifications only |

Two cells above were flagged by `notes_gap_report.md` §A-2 as *"NOT verified by anyone"* — **key
management** and **admin approval burden**. Both are now closed by `notes_close_google.md` §2 and §3
against live Google documentation, and the quotes above are the evidence. **The one remaining
unverified cell is (2)'s key management**, carried as a `VERIFY:` inline rather than written as
confident prose.

### E.2 The recommendation

**The hybrid in `DECISIONS.md` D3, unchanged:**

- **Human coworker relay, edits, deletes and attachment uploads → service-account domain-wide
  delegation impersonating the authoring human (user auth).** It is the only mode in which a relayed
  message renders as the real person, and the only mode that can upload a file.
- **Triton's replies → a separately registered Chat app on `chat.bot` (app auth).** Triton *should*
  be bot-badged; app auth is the only mode that permits `cardsV2`; and **`chat.bot` requires no
  administrator approval**, which removes an entire super-admin step from the runbook.
- **Keyless.** Two service accounts with `roles/iam.serviceAccountTokenCreator` between them and
  DWD assertions signed by IAM Credentials `signJwt` — §E.4.

**Two scoping consequences of the hybrid that nobody had stated before, and that Phase 1 must
build for:**

1. **The Chat app's Visibility setting scopes `@triton`. It does not scope the human relay.** A DWD
   relay posting as a human **does not go through the Chat app at all** — Google's 2026-07-17 release
   note says read-only/DWD integrations need no app registration. **So the pilot needs a second,
   ERPNext-side gate** (the `restrict_to_whitelist` + `allowed_users` pattern of §A.4.5), or the
   relay will happily post on behalf of people who are not in the pilot
   (`notes_close_google.md` §3.0).
2. **Two identities means two failure modes on one logical conversation.** A space where the human
   relay works and the Chat app is not installed shows human messages and no `@triton`; the reverse
   shows a bot talking to itself. The health check in `api/integrations_health.py` must report both
   independently (§D.5 item 5).

### E.3 THE TRILEMMA — the finding that changes the product

**This is checkpoint item 1, and it is the reason this ADR leads with §E rather than burying it.**

The contradiction sits in two sentences on the **same Google page**, and no research document or
phase prompt assembled them:

> **`NOTIFICATION_TYPE_SILENT`** — *"Do not notify recipients, and do not mark the message as
> unread. This behaves similarly to the user muting the conversation or enabling Chat Do Not
> Disturb. **Requires app authentication.**"*
> — <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>,
> and restated on the guide: *"Forced notifications and silent messages are only available for Chat
> apps using **app authentication**."*
> — <https://developers.google.com/workspace/chat/create-messages>

> **Under app authentication** — *"With **app authentication**, the Chat app sends the message. To
> note that the sender isn't a person, Chat displays `App` next to its name."*
> — <https://developers.google.com/workspace/chat/create-messages>

**Therefore a silent message is necessarily authored by the bot.** There is no `sender` override:
`Message.sender` is *"**Output only.** The user who created the message"*
(<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages>), and the only
regime with different rules is one-time **import mode** (§E.3.4). **"Silent" and "authored by the
real human" are mutually exclusive.**

#### E.3.1 The trilemma, as a table — at most two of the three

| Want | Requires | Excludes |
|---|---|---|
| **Messages authored by the real human in Chat** | user auth / DWD | silent → **Chat fires its own notification → locked decision #3 breaks** |
| **No Chat-native notification** | app auth + `NOTIFICATION_TYPE_SILENT` | human attribution → **decision #2's mirror becomes a bot log feed**; also threading; also outbound attachments |
| **Threaded replies inside Chat** | `messageReplyOption` + a threaded space | silent messages; **and `spaceThreadingState` is not settable at creation** |

#### E.3.2 The two constraints that tighten it further

**(a) Silent messages cannot thread.** Quoted verbatim from
<https://developers.google.com/workspace/chat/create-messages>, alongside its three sibling
limitations:

> - **Threading:** *"You can't start or reply to a thread with a silent message."*
> - **Mentions:** *"Silent messages don't support mentioning users. If you include a mention in a
>   silent message, it's treated as plain text."*
> - **External users:** *"Forced notifications and silent messages don't apply to external users
>   (guests) in a space."*
> - **Space type:** *"Forced notifications and silent messages aren't supported in direct messages
>   (DMs) or spaces owned by people who don't have a Google Workspace account."*

**(b) `spaceThreadingState` is Output only, so an API-created space may not be threadable at all.**
> *"`spaceThreadingState`: **Output only.** The threading state in the Chat space."*
> — <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces>

and the convenience method says it outright:
> *"Spaces with threaded replies aren't supported."*
> — <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces/setup>

A literal pass over the `spaces.create` reference found **no sentence containing "thread" or
"threaded" at all** — it neither permits nor forbids threading, which combined with the Output-only
annotation means **you cannot choose a space's threading mode when you create it.**

A related trap for whoever writes the relay: **omitting `messageReplyOption` actively *ignores* a
`thread.name` / `thread.threadKey` you supplied** —
`MESSAGE_REPLY_OPTION_UNSPECIFIED` is *"Default. Starts a new thread. **Using this option ignores
any thread ID or threadKey that's included.**"* A caller who sets `thread` but forgets
`messageReplyOption` gets a silent new thread, **not an error**.

#### E.3.3 The fourth constraint — app auth cannot upload attachments

> *"Uploads an attachment. For an example, see Upload media as a file attachment. **Requires user
> authentication**"* — with scopes `chat.messages.create`, `chat.messages`, `chat.import`.
> **`chat.bot` is absent.**
> — <https://developers.google.com/workspace/chat/api/reference/rest/v1/media/upload>

(App auth **can** download: `chat.bot` *is* among `media.download`'s scopes, and
<https://developers.google.com/workspace/chat/download-media-attachments> says *"App authentication
requires the `chat.bot` scope using service account credentials."*)

**So an app-auth relay cannot carry locked decision #9's file attachments outbound.** A single
logical message with text *and* a file would need **two different senders** — the app posting the
text silently and a DWD-signed upload for the file — which is not one message and cannot be made to
look like one.

**Stated as the impossible triangle:** cards need app auth; silence needs app auth; attachments need
user auth. You can have **{cards + silence}** or **{attachments + human attribution}**, never all
three on one message.

#### E.3.4 The one regime with different rules, and why it does not rescue us

**Import mode** is the only place Google lets an app write messages attributed to arbitrary domain
users with backdated `createTime` — and it is heavily fenced
(<https://developers.google.com/workspace/chat/import-data-overview>): *"Chat apps have 90 days to
complete the import of data for a space. After 90 days, if the space is still in import mode, it's
automatically deleted and will be inaccessible and unrecoverable"*; *"Spaces in import mode are
hidden from end users"*; *"Only `SpaceType.SPACE` and `SpaceType.GROUP_CHAT` are supported"*;
*"Members must be users within the same domain"*; historical memberships must be imported **while**
in import mode. `chat.import` is granted **only** by a Workspace super-admin through domain-wide
delegation, and `completeImport` requires *"user authentication and domain-wide delegation"*.

**It is a one-time backfill mechanism for a hidden space, not a steady-state relay.** It is worth
recording for a possible historical-backfill phase, and the single call that would settle whether
that is even possible is in §E.6.

#### E.3.5 The documented fallback, and why it is a poor one

`users.spaces.spaceNotificationSetting.patch` can set `muteSetting: MUTED` or
`notificationSetting: OFF` for a user in a space, and **DWD can do it for each coworker** because
impersonation makes the caller *be* that user (the `get` sibling states *"Only the caller's user id
or email is allowed in the path"*; there is **no `useAdminAccess`** on this method, and it is absent
from the admin-overview's list of admin-capable methods). **Reject it as the fallback**, for three
reasons `notes_google_verify.md` §1g sets out: it is **per-space, not per-message**, so it silences
human coworkers along with the relay — it implements *"this space is dead to you"*, not *"Chat is
transport"*; it requires enrolling every user and re-applying on every new space; and it is
**user-visible and user-reversible**, with nothing notifying ERPNext when someone flips it back.

#### E.3.6 Locked decision #3 is at risk. Our recommendation, and the human's call

**Locked decision #3 is at risk, and the ADR says so plainly rather than engineering around it.**
`notes_google_verify.md` §4e recommends an *"Option A"* — unthreaded spaces + `NOTIFICATION_TYPE_SILENT`
+ ERPNext owns all notification — and calls it *"Satisfies the locked product decision."* **It does
not.** `notes_gap_report.md` §D-1 caught this, and it is the most consequential correction in the
whole evidence base: **Option A silently sacrifices message attribution**, which is the thing the
hybrid exists to preserve and the thing that makes decision #2's bidirectional mirror meaningful.
Under Option A, a coworker in the native Google Chat client sees **every relayed message from every
colleague arrive from a single bot with an `App` badge** — plus a visual indicator that it was
delivered silently, because *"Whether forced or silent, these messages include a visual indicator
that notifies the recipients of the special notification behavior."* **That is not a mirror of the
conversation; it is a log feed.**

**Do not present the app-auth "Option A" as satisfying the locked decisions. It does not.**

**Our recommendation to Nikolas:** **keep human attribution (DWD), and accept Chat-native
notifications for people who run the native Google Chat client.**

**Proposed restatement of locked decision #3:**

> *"Exactly two ERPNext-fired notifications per message. Users running the native Google Chat client
> additionally receive Chat's own notification for messages relayed into Chat, which is documented
> and accepted. Google Chat remains transport, not an ERPNext notification channel."*

**This is the human's call, not the ADR's.** Phase 0 §4.I explicitly authorises this outcome —
*"If **neither** works, say so plainly … the human needs to decide, not you."* The three options in
front of him are:

| Option | What he gets | What he gives up |
|---|---|---|
| **A — restate #3 (our recommendation)** | Real human attribution; attachments outbound; the mirror is a mirror | Native-client users get a second notification; #3 is amended |
| **B — app-auth silent relay** | #3 holds literally; no Chat-native notification | Every relayed message is bot-badged with a silent-delivery indicator; **no outbound attachments** (#9 breaks); no threading; mentions degrade to plain text; guests and DMs are excluded anyway |
| **C — accept a split** | Text silent-and-bot-badged, attachments human-attributed | Two senders on one logical message; the most confusing outcome of the three, and we recommend against it |

**Also record what Option B does *not* cost, so the choice is fair:** the space-type and guest
carve-outs only matter for DMs and external guests, and a named internal `SPACE` — which is what
decision #10's per-document spaces are — is exactly the case where silent works.

### E.4 The keyless GCP finding

**Stated at the confidence level `notes_close_google.md` §2 actually established — no higher.**

#### E.4.1 What is CONFIRMED, verbatim, against live Google documentation

1. **`roles/iam.serviceAccountTokenCreator` is the role, and it contains the permission.**
   <https://docs.cloud.google.com/iam/docs/service-account-permissions> — the role *"lets principals
   create short-lived credentials for a service account"*, and its permission list includes
   **`iam.serviceAccounts.signJwt`** alongside `getAccessToken`, `getOpenIdToken`,
   `implicitDelegation` and `signBlob`. Credential types include *"**Signed JSON Web Tokens (JWTs)
   and binary blobs**"*. **So granting the VM-attached service account
   `roles/iam.serviceAccountTokenCreator` *on the delegation service account* is exactly the binding
   that permits the call.** `DECISIONS.md` D3's *"two service accounts (VM-attached + delegation)
   with `roles/iam.serviceAccountTokenCreator` between them"* is **confirmed verbatim.**
2. **The method and its path are exactly as claimed.**
   <https://docs.cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/signJwt>:
   `POST https://iamcredentials.googleapis.com/v1/{name=projects/*/serviceAccounts/*}:signJwt`, with
   `name` documented as *"`projects/-/serviceAccounts/{ACCOUNT_EMAIL_OR_UNIQUEID}`"* — **the literal
   hyphen wildcard is in Google's own format string.** Request body `{delegates[], payload}`;
   response `{keyId, signedJwt}`; required permission `iam.serviceAccounts.signJwt`.
3. **`payload` is the claims set only.** *"Must be a serialized JSON object that contains a JWT
   Claims Set."* **IAM Credentials builds the header and chooses `alg`/`kid`; we do not
   base64url-encode anything and we write no cryptography on the signing path.** `delegates` exists
   for A→B→C chains and is omitted for our single hop.
4. **A self-signed JWT carrying `sub` is the supported keyless-DWD path, and Google recommends it
   over keys.** <https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys>:
   *"**Although examples illustrating the use of domain-wide delegation commonly suggest the use of
   service account keys, using service account keys is not necessary to perform domain-wide
   delegation.**"* … *"When using domain-wide delegation, **avoid service account keys and use the
   `signJwt` API instead**"* … *"Construct a JWT and use the **`sub` claim** to specify the email
   address of the user for which you're requesting delegated access."* The **four-step sequence**,
   quoted in order: (1) *"Authenticate a service account by using an **attached service account**…"*;
   (2) *"**Construct a JWT** and use the `sub` claim…"*; (3) *"Use the **`signJwt` API** to sign the
   JWT."*; (4) *"**Pass the signed JWT** to the OAuth2 Token resource to obtain an access token."*
   **Step 1 names "attached service account" first, which is exactly the production topology** (a
   single standalone GCE VM per environment). Corroborated on the Workspace side:
   <https://knowledge.workspace.google.com/admin/apps/domain-wide-delegation-best-practices> —
   *"**Using service account keys is not necessary to perform domain-wide delegation. Use the signJwt
   API instead.**"*
5. **The claim set, field by field**, from
   <https://developers.google.com/identity/protocols/oauth2/service-account>: required `iss` (*"The
   email address of the service account"* — i.e. the **delegation** SA, not the user), `scope`,
   `aud` (*"Always `https://oauth2.googleapis.com/token`"*), `exp`, `iat`; plus **`sub`** — *"The
   email address of the user for which the application is requesting delegated access."* Token
   exchange: `POST https://oauth2.googleapis.com/token` with
   `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and `assertion=<signed JWT>`.
6. **`google.auth.impersonated_credentials` has no `subject` parameter — the settled fact.**
   Verified twice independently (`notes_gap_report.md` §0-6, then re-fetched by
   `notes_close_google.md` §2.5) against
   <https://google-auth.readthedocs.io/en/master/reference/google.auth.impersonated_credentials.html>:
   `Credentials.__init__` takes exactly `source_credentials`, `target_principal`, `target_scopes`,
   `delegates=None`, `lifetime=3600`, `quota_project_id=None`, `iam_endpoint_override=None`. **No
   `subject`. No `with_subject` method.** The library gives impersonation but **not delegation**, so
   the `sub` claim has no supported home and **the assertion is hand-rolled.**
7. **`google.cloud.iam_credentials_v1` is a separate distribution** (`google-cloud-iam` on PyPI), not
   part of `google-auth`. The production import probe confirmed `google.oauth2.service_account` and
   `googleapiclient.discovery` — **neither implies `google-cloud-iam` is installed** — and the host
   cannot pip-install. **Therefore `signJwt` is called as a plain `requests` POST**, which is not a
   workaround but the house doctrine (`decisions/adr/0004-no-vendor-sdks.md`, the same reason
   `stripe_payments` and the QuickBooks client hand-roll their HTTP). **Do not write
   `IAMCredentialsClient.sign_jwt` in any phase.**

#### E.4.2 Sizing it as its own module with its own unit test

`DECISIONS.md` D3 budgets *"~40 lines of the assertion builder"*. **The premise is confirmed; the
composition is different from what the phrase implies**, and this is a refinement, not a reversal:

| Piece | ~Lines | Note |
|---|---|---|
| Get a token for the VM-attached SA | 5 | `google.auth.default()` + `Request()`, or a raw metadata-server GET. Already-installed deps only |
| Build the claims set dict | 10 | `iss`, `sub`, `scope`, `aud`, `iat`, `exp` — all six confirmed in §E.4.1(5) |
| `POST …:signJwt` with `{"payload": json.dumps(claims)}` | 8 | plain `requests` |
| `POST https://oauth2.googleapis.com/token` with `grant_type` + `assertion` | 8 | form-encoded |
| **Cache the access token per `(subject, sorted(scopes))`** | 10 | **the part that is easy to get wrong** |
| Error mapping (`401 unauthorized_client` → DWD scope/client-id mismatch; `403` on signJwt → missing `serviceAccountTokenCreator`) | 8 | the two documented failure signals |

**≈50 lines, one module, one unit test** — recorded as such rather than as "~40", with the note that
**the signing step is smaller than expected (no cryptography, because IAM Credentials signs) while
the caching step is larger.**

**Three interacting constraints on that cache, each of which a naive implementation violates:**

1. **It is per-impersonated-user** — at the measured roster that is ~20 live access tokens, not one.
   The key must be `(subject, sorted(scopes))`.
2. **It must not live only in the queue Redis**, because the deploy FLUSHDBs **both** instances
   (§A.11.4). A flushed token cache is *safe* — it fails open into a re-mint — but it costs two extra
   HTTP round-trips per relayed message for the first minute after every deploy, against a **1
   write/second/space** budget. Prefer the cache Redis (13000), and treat a cold cache as a latency
   event, not a correctness one.
3. **Token minting is itself quota-limited.**
   `VERIFY: the per-project quota on iamcredentials.googleapis.com signJwt — read the Quotas page for
   iamcredentials.googleapis.com in the Cloud console for project erpnext-465317 — blocks: whether a
   cold cache after a deploy can mint ~20 tokens in a burst without 429ing. Low risk, cheap, and it
   only bites on the first Monday morning after a Friday deploy.`

**The rejected micro-alternative, recorded so nobody re-derives it:**
`impersonated_credentials.Credentials` exposes `sign_bytes(message)` and `signer_email`, so the JWT
could be assembled and base64url-encoded locally and signed through a library that is *plausibly*
already installed. **Rejected:** it trades ~8 lines of `requests` for a serialisation step we would
not otherwise write, and it costs a dependency assumption. `requests` always works and matches ADR
0004 exactly. *(`VERIFY: whether google.auth.impersonated_credentials imports on the prod bench — add
it to the frappe.get_module probe at notes_infra.md:979-989 — blocks: nothing; it only decides
whether ~8 lines get shorter.`)*

#### E.4.3 The one thing to put in front of the security reviewer

Google's own DWD best-practices page says, in the same breath as recommending `signJwt`:

> *"Use domain-wide delegation **only when you have a critical business case that requires an app to
> bypass user consent** to access Google Workspace data. Try alternatives such as OAuth with user
> consent or use Marketplace apps."*
> — <https://knowledge.workspace.google.com/admin/apps/domain-wide-delegation-best-practices>

**That is Google telling us DWD is a last resort, and the ADR quotes it rather than hiding it.** The
honest framing: **DWD is chosen because `DECISIONS.md` D3 requires human attribution on relayed
messages, human attribution requires user auth, and per-user 3LO — the documented alternative — was
rejected for ~20 individual consent flows with re-consent on every scope change and ~20 independent
revocation surfaces (§E.1 column 3).** Say that, cite Google's sentence, and let the reviewer weigh
it. **Do not present DWD as uncontroversial.** The same page's other three instructions are
compliance obligations we adopt: *"Use organization policies to restrict key creation and upload for
service accounts with domain-wide delegation"*; *"Ensure that service accounts with domain-wide
delegation have only the essential privileges needed"*; *"Do not give access to non-essential OAuth
scopes."*

**And two remaining unknowns, stated as unknowns:**

> `VERIFY: the OAuth scope the VM-attached service account must present to call signJwt — the
> reference page's Authorization-scopes block was truncated in both fetches and the agent declined to
> assert `cloud-platform` from memory. Settle by re-fetching the signJwt reference and reading that
> block, or by curling the method from the VM with the metadata-server token and recording which
> scope it returns. Blocks: whether the production VM's access scopes need widening — a VM property
> whose change requires a stop/start, so it is much cheaper to know BEFORE Phase 1 than during it.`

> `VERIFY: whether Google rejects an assertion whose exp is more than N seconds out — R04's
> exp = now + 600 is conventional, not documented. Blocks: nothing; 600 s is safe.`

### E.5 The Google Workspace admin runbook

> **Read this framing note before using the runbook, and repeat it in the checkpoint.** Every
> navigation path below is quoted from a live Google support or developer page fetched **2026-08-07**.
> **The Admin console was not opened.** So each step is **documentation-verified, not
> execution-verified** — a real distinction, because documentation lags consoles. Steps whose path
> could **not** be found in documentation are marked **⚠ VERIFY against live console** and are given
> **no invented click path**. The checkpoint should say: *"the runbook's navigation is verified
> against Google's current help pages; it has not been executed."*

#### E.5.0 A runbook-wide finding: the Workspace admin help has MOVED DOMAINS

Every `support.google.com/a/answer/NNNN` URL fetched this session returned **HTTP 301** to
`knowledge.workspace.google.com` with a human-readable slug replacing the numeric answer id —
confirmed four times independently (`notes_close_google.md` §0). This is the concrete instance of the
risk `notes_gap_report.md` §A-1 refused to accept the runbook on: the *pages* did not move, **the
documentation site did**.

**Rule for the runbook and for Appendix B:** cite
`knowledge.workspace.google.com/admin/<area>/<slug>` URLs, **and give the menu path beside every one
of them**, because the menu path is what a human clicks and it survives a URL change. **Never ship a
bare numeric console URL** — the same rule `notes_gap_report.md` §C-4 applied to
`https://admin.google.com/ac/appsettings/216932279217`, which remains **unverified** and appears
below only beside its menu path.

#### E.5.1 THE LOAD-BEARING CLAIM: Chat apps must be enabled at the TOP organizational unit — CONFIRMED. A pilot-OU rollout is impossible.

`notes_gap_report.md` §A-1 flagged this as *"a load-bearing constraint on the rollout plan and it is
currently pure inheritance from R04"*, because it decides whether a pilot-OU rollout is even
possible. **It is true, and Google states it three times on two independent pages.**

<https://knowledge.workspace.google.com/admin/chat/allow-users-to-install-chat-apps> (301 from
`support.google.com/a/answer/7651360`), quoted verbatim:

> *"**Apps must be turned on for the top organizational unit to work with the Chat API, and to
> ensure apps work properly in spaces.**"*
>
> *"Some apps require top-level organizational unit access to install apps in Chat. **If you don't
> allow this, then Chat APIs may be prevented from working properly.**"*
>
> *"Spaces requires that users have top-level organizational unit access, otherwise Chat apps might
> not function properly. We **highly recommend** not restricting this function based on
> organizational units."*

Independently corroborated from the developer side —
<https://developers.google.com/workspace/chat/troubleshoot-chat-apps> lists, among the documented
causes of *"This organization's administrator must allow users to install this Chat app"*:

> - *"The organization has disabled Chat apps."*
> - *"The organization hasn't added this specific Chat app to the organization's allowlist."*
> - *"**The organization granting access to a sub-organizational unit, without enabling it for the
>   parent organizational unit.**"*

**That third bullet is a diagnosed error, not advice.** And a third direction:
<https://knowledge.workspace.google.com/admin/chat/set-up-app-authorization-for-chat> states that
Chat app permissions **"cannot be granted by organizational unit."**

> **CONSEQUENCE — a rollout-plan decision, not a footnote. The pilot CANNOT be scoped by
> organizational unit. It is scoped by the Chat app's own Visibility setting**, which takes *"up to
> five individuals, **or one or more Google Groups**"* (§E.5.2 step 10, Visibility) — so a pilot of more than five
> people **requires a Google Group** (`chat-pilot@sapphirefountains.com`), created **before** the app
> is configured. **And because Visibility gates the app and not the DWD relay (§E.2 consequence 1),
> the pilot needs the ERPNext-side `restrict_to_whitelist` gate as well.** Two gates, two systems,
> and they must be kept in sync — which is itself an argument for making the ERPNext gate the
> authoritative one and the Google Group derived from it.

#### E.5.2 Step-by-step, with what to verify after each

**Step 0 — Cloud project.** Use the project that owns the ERPNext VM. *Verify:*
`gcloud projects describe` returns `ACTIVE` and the project **number** is recorded (needed for the
project-number JWT audience mode, §E.5.2 step 10).

**Step 1 — Enable APIs:** `chat`, `pubsub`, `workspaceevents`, `iamcredentials`, `admin`, `logging`.
*Verify:* `gcloud services list --enabled` shows all six. **Gotcha, quoted from
<https://docs.cloud.google.com/iam/docs/reference/credentials/rest>:** *"disabling this API also
disables the IAM API (`iam.googleapis.com`). However, enabling this API doesn't enable the IAM
API."*

**Step 2 — OAuth consent screen = Internal.** ⚠ **VERIFY against live console:** the label may now
sit under *"Google Auth Platform"* rather than *"APIs & Services → OAuth consent screen"*; this was
inherited unverified from R04 and **is not asserted here**. *Verify:* no verification banner.

**Step 3 — Turn on Google Chat, `ON for everyone`, at the TOP OU.**
Menu path, quoted from
<https://knowledge.workspace.google.com/admin/chat/turn-chat-on-or-off-for-your-organization>:

> 1. *"In the Google Admin console, go to Menu → **Apps** → **Google Workspace** → **Google Chat**"*
> 2. *"Click **Service Status**"*
> 3. *"(Optional) Select an **organizational unit** or configuration **group**"*
> 4. Select **"ON for everyone"**
> 5. *"Click **Save**"*

with, from the same page: *"**Changes can take up to 24 hours but typically happen more quickly.**"*
and *"Group settings override organizational units."*
*Verify:* a coworker can open `chat.google.com` and see Chat. **If it does not appear, wait out the
24-hour window before debugging anything else** — this propagation delay is the single most common
source of "the runbook is wrong" during a first setup.
⚠ The numeric convenience URL `https://admin.google.com/ac/appsettings/216932279217` is carried
**unverified**, beside the menu path, per §E.5.0. **Expect the numeric service id to be unstable.**

**Step 4 — Allow Chat apps, at the TOP OU.** Menu path, quoted from
<https://knowledge.workspace.google.com/admin/chat/allow-users-to-install-chat-apps>: *"Menu →
**Apps** → **Google Workspace** → **Google Chat**"*, then **Chat apps**, where the settings are named
*"**Allow users to install Chat apps**"* and *"**Allow users to add and use incoming webhooks**"*.
Applied at the **top organizational unit**, per §E.5.1. The page notes *"To apply the setting for
certain users, put their accounts in a configuration group"* — **do not use that to scope the
pilot.** *Verify:* no *"This organization's administrator must allow users to install this Chat app"*
error. **Propagation:** this page states no delay; its siblings state 24 hours, so assume the same
envelope. *(`VERIFY: whether the Chat-apps setting shares the 24-hour envelope — settle by
observation during first setup — blocks: nothing; prevents a false "it's broken" at minute five.`)*

**Step 5 — Create the pilot Google Group** (`chat-pilot@<domain>`), Admin → Directory → Groups.
*Verify:* the group exists and pilot users are members. **This must precede step 10**, because
Visibility caps individuals at five (§E.5.2 step 10, Visibility).

**Step 6 — Create the delegation service account and grant the VM-attached SA
`roles/iam.serviceAccountTokenCreator` ON it** (Cloud console → IAM). *Verify:* a `signJwt` curl from
the VM returns a `signedJwt`, **and there is no key file on disk** (§E.4). **This must precede step
7**, because DWD is authorised against the delegation SA's Client ID, which does not exist until the
SA does.

**Step 7 — Domain-wide delegation.** Menu path, quoted verbatim from
<https://knowledge.workspace.google.com/admin/apps/control-api-access-with-domain-wide-delegation>:

> *"Menu → **Security** → **Access and data control** → **API controls** → **Manage Domain Wide
> Delegation**."*
> *"You must be signed in as a **super administrator** for this task."*
> 1. *"Click **Add new**."*
> 2. *"Enter the **Client ID** for either the service account or the OAuth 2.0 client."*
> 3. *"In **OAuth Scopes,** add each scope that the application can access (should be appropriately
>    narrow)."*
> 4. *"Click **Authorize**."*
> 5. *"Point to the new client ID, click **View details**, and make sure that every scope is
>    listed."*

**Propagation, CONFIRMED and quoted:** *"**Changes can take up to 24 hours but typically happen more
quickly.**"* — **this closes `notes_gap_report.md` §A-2's admin-burden gap, verbatim from Google's
own page.**

Two operational notes: **the Client ID is the delegation service account's numeric OAuth client id**,
not its email and not the project number — reading the wrong one produces the classic
`401 unauthorized_client`; and **step 5 is not ceremony** — the console silently accepts a scope list
and the *View details* round-trip is the only confirmation that what was pasted is what was stored.

*Verify:* `GET https://chat.googleapis.com/v1/spaces` using a DWD-impersonated token for a pilot user
returns **that user's** spaces. If it returns the app's spaces you are on app auth, not DWD;
`401 unauthorized_client` means the scope strings or the client id are wrong. **Allow the full 24
hours before concluding the scopes are wrong.**

**Step 8 — App Access Control: mark the OAuth client Trusted.** Menu path, quoted from
<https://knowledge.workspace.google.com/admin/apps/control-which-third-party-and-internal-apps-access-google-workspace-data>:
**Security → Access and data control → API controls**, then *"**Manage Third-Party App Access**"* →
*"**Configured apps**"* → *"**Add app**"* → *"**OAuth App Name or Client ID**"*. Access levels,
quoted: **Trusted** — *"Trusting an app overrides a service restriction"*; **Limited** — *"Can access
only unrestricted Google services"*; **Blocked** — *"Can't access any Google service"*.

**Are internal apps Trusted by default? REFUTED, and this step is therefore mandatory rather than
conditional.** The page says *"If you build internal apps (owned by your organization), you can trust
all apps to access restricted Google Workspace APIs. That way, you don't have to trust them all
individually"* — **an opt-in setting that exists precisely because the default is not trust.**
`notes_research_gaps.md` R04-V11 said *"do not assume it"*; it is now positively resolved in the
direction R04 warned about.

**Propagation here is different and slower**, quoted: *"Details about third-party apps typically
appear **24–48 hours** after authorization"* and *"The accessed apps list is updated **48 hours**
after a token is granted or revoked."* **Carry the 48-hour figure** — it is the longest delay in the
whole setup, and it applies to the *visibility of the app in the console*, which is exactly what an
admin stares at while deciding whether step 7 worked.

*Verify:* the client appears under **Configured apps** as **Trusted**; then re-run step 7's check.

> **⚠ UNRESOLVED, and it is the one that will burn an afternoon.** R04 claims that an app blocked by
> App Access Control surfaces as `403 "Request had insufficient authentication scopes"` — a
> misleading symptom, because it points at scopes rather than at access control.
> **`notes_close_google.md` §3.4 searched Google's support and developer documentation and found no
> page that states it**; the troubleshooting page documents two *different* strings. Consistent
> general guidance exists (apps with DWD or marked Trusted *"bypass the granular permissions consent
> screen… your app will either receive all requested scopes or none"*) but is not a statement of the
> symptom.
> `VERIFY: that an app blocked by App Access Control returns 403 "Request had insufficient
> authentication scopes" / ACCESS_TOKEN_SCOPE_INSUFFICIENT rather than a distinct access-control
> error — settle during setup by deliberately leaving the client un-Trusted, making one
> DWD-impersonated GET /v1/spaces call and recording the exact error body, then Trusting it, waiting,
> and repeating. Two calls, five minutes, and it produces the single most valuable line in the
> troubleshooting section. Blocks: nothing structural.`

**Step 9 — Pub/Sub topics, pull subscriptions, and the publisher binding.** Quoted from
<https://developers.google.com/workspace/chat/quickstart/pub-sub>:

> *"Grant Chat permission to publish to the topic by assigning the **Pub/Sub Publisher** role to the
> following service account: `chat-api-push@system.gserviceaccount.com`"*
> *"Create a **pull** subscription to the topic."*
> *"Assign the **Pub/Sub Subscriber Role** on the subscription for the service account that you
> previously created."*

and the **same principal and role** for Workspace Events, from
<https://developers.google.com/workspace/events/guides/create-subscription>: *"**Chat events:** use
`chat-api-push@system.gserviceaccount.com`"*, *"In the **Assign roles** menu, select `Pub/Sub
Publisher`."* **This closes R04-V14: one principal covers both surfaces.**

**Recommend two topics, not one** — interaction events are latency-sensitive and low-volume while the
message firehose is bursty, and a poison message on one must not stall the other.

*Verify:* `gcloud pubsub topics get-iam-policy <topic>` shows the binding, then post in the test space
and `gcloud pubsub subscriptions pull` returns a payload. **Failure here is silent** — Chat's publish
is rejected on Google's side and nothing surfaces to the app. **The diagnostic is Cloud Logging on the
topic for permission-denied publishes**, and Phase 1 should wire that alert before it is needed.

**Step 10 — Chat API Configuration.** Navigate via
<https://developers.google.com/workspace/chat/configure-chat-api> to the Chat API page →
**Configuration** (`https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat`).
Fields quoted from that page: **App name** (≤25 chars), **Avatar URL** (HTTPS, square PNG/JPEG,
256×256+ recommended), **Description** (≤40 chars), **Enable interactive features**, **Log errors to
Logging**. Also on that page, and nobody had it: a *"Grant other people permission to configure the
Chat API"* section naming the **Chat apps Owner** and **Chat apps Viewer** IAM roles, and noting that
a custom role should use **`chat.bots.get` / `chat.bots.update`** rather than project-level
permissions.

> **⚠ VERIFY against live console: whether the Configuration tab offers a *"Build this Chat app as a
> Workspace add-on"* checkbox, and that it is left UNCHECKED.** A targeted fetch of the current page
> did not surface it, and the add-on model exists as a separate documentation track — so R04's
> warning is not groundless. **It matters because checking it may change the Pub/Sub publisher
> principal (R04-V14), which would break the inbound leg silently.** Keep R04's instruction: **do not
> check it.**

**Connection settings — a fork the ADR must state, not a preference.** Quoted:
*"Under **Connection settings**, select **HTTP endpoint URL**"* (`quickstart/gcf-app`) versus
*"Under **Connection settings**, select **Cloud Pub/Sub** and paste the name of the Pub/Sub topic
that you previously created"* (`quickstart/pub-sub`), the latter adding a detail nobody had recorded:
*"on the Chat API configuration page, under **Connection settings** copy the **Service account
email**, which is a unique email generated for your Google Cloud project."*

| Mode | Delivery | Consequence here |
|---|---|---|
| **HTTP endpoint URL** | Chat POSTs a bearer-JWT-authenticated request to a URL | Reaches a Frappe `@frappe.whitelist(allow_guest=True)` endpoint directly. **Requires the four-part JWT verification of §E.5.3, hand-rolled.** The URL becomes the JWT `aud` and must match byte for byte. Subject to the GCLB 30 s backend timeout — acceptable, since Chat's own interaction deadline is 30 s anyway and the handler must ack-and-enqueue regardless |
| **Cloud Pub/Sub** | Chat publishes to a topic; we pull | No public endpoint, no JWT verification, no load balancer in the path. **But it needs a puller**, and Frappe's scheduler makes that lumpy — latency equals the scheduler interval, which for an `@triton` reply is user-visible |

**Note the asymmetry, which is not a choice:** Workspace Events' `NotificationEndpoint` offers **only
`pubsubTopic`** — there is **no HTTPS-webhook option** — so **Pub/Sub infrastructure is mandatory for
the message firehose regardless.** **Recommendation: HTTP endpoint for interaction events** (latency
is user-visible for `@triton`, and the 30 s synchronous-response affordance exists only there),
**Pub/Sub for Workspace Events** (no alternative exists).

**Visibility — the pilot gate.** Quoted from `quickstart/pub-sub`: *"Under **Visibility**, select
**Make this Google Chat app available to specific people and groups** in your domain and enter your
email address"*, and *"You can specify **up to five individuals, or one or more Google Groups** from
your Google Workspace organization."* → **the pilot Group from step 5.**

*Verify:* app status shows *"**Live - available to users**"*
(<https://developers.google.com/workspace/chat/troubleshoot-chat-apps>); DM the app from Chat as a
pilot user and see a `MESSAGE` interaction event reach the stub endpoint.

**Step 11 — one-time `chat.app.*` approval — ONLY if a `chat.app.*` scope is ever used, and it should
not be.** Path, quoted from
<https://knowledge.workspace.google.com/admin/chat/set-up-app-authorization-for-chat> (reached by
following `support.google.com/a?p=chat-app-auth` through two 301s, which **closes R04-V12**): *"Menu
→ **Apps** → **Google Workspace Marketplace apps** → **Apps list**"*, requiring the **Google
Workspace Marketplace administrator privilege**, with steps *"Click **Install app**"* → browse the
Marketplace → *"**Admin install** → **Continue**"* → review → *"Select **Everyone at your
organization** → **Finish**"*, propagating in *"up to 24 hours"*. The page also states that app
permissions *"cannot be granted by organizational unit"* and that *"**Before apps can perform the
actions their authorization scopes request, a Workspace administrator must grant one-time
app-specific approval.**"*

> **THE WRINKLE, and it is a real finding that turns into a design constraint.** The only documented
> `chat.app.*` approval path goes through a **Marketplace admin-install of a published app**. Asked
> directly about private, unlisted or internal apps, the page gave an **explicit negative**: *"The
> provided documentation does not contain any information about private apps, unlisted apps, internal
> applications, or any non-Marketplace app authorization processes."* **A Chat app configured only via
> the Cloud console Configuration tab — which is what this project builds — is not in that list.**
>
> **Therefore: use `chat.bot` only, and never a `chat.app.*` scope.** `chat.bot` requires **no**
> administrator approval (quoted twice this session: `authenticate-authorize-chat-app` and the
> troubleshooting page's recommended remedy). Under `DECISIONS.md` D3 the registered Chat app exists
> to post **Triton's replies** — a `chat.bot` job in full. **This removes a super-admin approval step
> and a Marketplace-publication question from the runbook entirely**, and it is written here as a
> *decision with a reason*, not as a default.
>
> **What it costs, stated:** it forecloses shape **A** of the inbound firehose (per-space, app auth,
> `chat.app.messages.readonly`) and shape **C** (customer-level, `chat.app.all.messages.readonly`,
> Developer Preview + an Enterprise SKU), leaving **shape B** — one per-user
> `//chat.googleapis.com/spaces/-` subscription per coworker under DWD, the only **GA** way to see
> coworker messages in spaces the app is not a member of. At ~20 coworkers that is ~20 subscriptions,
> ~20 renewals per week under the recommended 7-day TTL, and **~20 independent `USER_SCOPE_REVOKED`
> surfaces**, which is the thing to monitor rather than the count.
> `VERIFY: whether an unlisted/private Chat app can be granted chat.app.* approval, and by what path
> — ask Workspace support, or investigate private Marketplace publication to your own domain —
> blocks: shapes A and C only; not blocking for V1 under the chat.bot-only decision.`

**Step 12 — create the Workspace Events subscription(s).** An API call, not a console step. *Verify:*
`subscriptions.get` shows a future `expireTime`; record the delta.

#### E.5.3 The inbound authenticity check, stated because step 10's HTTP mode depends on it

Google Chat sends a bearer token in the `Authorization` header, issued by
**`chat@system.gserviceaccount.com`** in both audience modes (endpoint-URL mode uses an OIDC **ID
token**; project-number mode a **self-signed JWT**) —
<https://developers.google.com/workspace/chat/verify-requests-from-chat>. **The verification must be
all four of:** signature valid against Google's keys for `chat@system.gserviceaccount.com`; `aud`
equal to the configured endpoint URL (or project number); **`email == chat@system.gserviceaccount.com`**;
and **`email_verified` true.** Failure → HTTP `401`.

**The trap, and it is the whole point:** a stock `verify_oauth2_token(...)` checks the signature and
`aud` but **not** the `email` claim — and **any Google service account in the world can mint a valid
OIDC ID token for your audience.** Without the explicit issuer-email check, an attacker with any GCP
project can forge "requests from Chat".

**And the escape hatch worth raising as a design option:** *"Cloud Run and Cloud Functions
automatically handle verification when you authorize `chat@system.gserviceaccount.com` as an invoker
via IAM."* If the interaction endpoint terminates on Cloud Run rather than in Frappe, granting
`roles/run.invoker` offloads the entire check to the platform — materially safer than hand-rolling
JWT verification on a host that cannot install SDKs.

**An IP allowlist is not available as a control, and the ADR should say so before a reviewer asks.**
`notes_close_google.md` §4.1 established three independent negatives: the Chat verification page
*"contains no information about IP addresses, IP ranges, allowlisting, or firewall configuration"*;
Google publishes only two org-wide range files (`goog.json`, `cloud.json`) and states that *"**The
default domain IP ranges used by Google APIs and services are allocated dynamically and change
often**"*; and a targeted search across four Google domains for Chat-specific egress ranges returned
nothing. **Allowlisting `goog.json` would admit every Google service and every Google customer's
Cloud resources — weaker than no control at all in the threat model that matters. The issuer-email
pin authenticates *who*, which is strictly stronger than authenticating *where from*.**

#### E.5.4 Ordering, and the property that saves three days

**Steps 3, 4, 7 and 8 each carry a propagation delay** (24 h, 24 h assumed, 24 h, 24–48 h). **They
are independent, so do them all in one sitting and then wait once.** A runbook that verifies each
before starting the next takes four days. Two hard orderings: **step 5 before step 10** (Visibility
takes a Group, and the Group must exist first) and **step 6 before step 7** (DWD is authorised
against a Client ID that does not exist until the SA does).

#### E.5.5 What the runbook does NOT cover, stated so nobody assumes it does

- **It has not been executed.** Every path is documentation-verified only (§E.5 framing note).
- **Two navigation items are explicitly unverified**: the OAuth-consent-screen label (step 2) and the
  numeric console URL in step 3.
- **The Workspace-side prerequisites for the org-wide firehose are unchecked**, and both are gating
  for shape C: `VERIFY: whether the tenant holds at least one Enterprise SKU licence — Admin console
  → Billing → Subscriptions`; and `VERIFY: whether the Cloud project is enrolled in the Developer
  Preview Program — https://developers.google.com/workspace/preview, enrolment is per-project and
  requires a form.` Both are **moot under the `chat.bot`-only decision** and are recorded so the
  decision can be revisited rather than re-researched.
- **The subscription-count question is open in the honest sense.** Workspace Events documents rate
  limits (600 subscription writes/minute per project, 100/minute per user) and Google says *"As long
  as you stay within the per-minute quotas … there's no limit to the number of requests you can make
  per day"* — but **no cap on *concurrent* subscriptions is stated anywhere**, and the only count cap
  in the documentation applies to *customer-level* subscriptions, a different type. Silence is not
  permission.
  `VERIFY: whether an undocumented cap on concurrent Workspace Events subscriptions exists near 20–50
  — settle during the pilot by creating all subscriptions and confirming subscriptions.list returns
  them all ACTIVE — blocks: shape B at full org size, whose only fallbacks (shape A, shape C) are
  both foreclosed by §E.5.2 step 11. Worth settling BEFORE the pilot ends, not after: discovering it
  at 20 users is much better than at 50.`

### E.6 The Google-side `VERIFY:` register carried into Phase 1

Ranked by what breaks. Items closed by `notes_close_google.md` are not repeated.

**Before or during Phase 1 setup — each is one call or one console look:**

1. `VERIFY: what spaceThreadingState a space created by spaces.create actually reports — create one
   test space via the API, then spaces.get and read the field. FIVE-MINUTE CALL. Blocks: decision #5
   (Triton replies in-thread) and the threading leg of the trilemma. It should be made before any
   threading design is written.`
2. `VERIFY: whether messageReplyOption functions in a GROUPED_MESSAGES space — post with
   REPLY_MESSAGE_OR_FAIL into an API-created space; a NOT_FOUND or a silently-new-thread result
   answers it. Blocks: same.`
3. `VERIFY: the OAuth scope the VM-attached SA must present to call signJwt (§E.4.3) — blocks a VM
   property change that requires a stop/start, so settle it BEFORE Phase 1.`
4. `VERIFY: whether an app blocked by App Access Control returns 403 "insufficient authentication
   scopes" (§E.5.2 step 8).`
5. `VERIFY: whether the Chat API Configuration tab offers a "Build this Chat app as a Workspace
   add-on" checkbox, and that it is UNCHECKED (§E.5.2 step 10) — if checked, the Pub/Sub publisher
   principal may change and the inbound leg dies silently.`
6. `VERIFY: that NOTIFICATION_TYPE_SILENT in a SPACE with only internal members produces no push and
   no unread badge on the native Android/iOS clients, and what the "visual indicator" on a silent
   message actually looks like — post one and observe two real devices, and screenshot it. This is a
   UX sign-off item for the human, and it is only relevant if he chooses Option B in §E.3.6.`
7. `VERIFY: that a DWD-impersonated user token can create a spaces/- subscription at all — the docs
   say "only supports user authentication" and DWD is user authentication, but no page states the
   combination explicitly. One validateOnly: true create call. Blocks: shape B, i.e. the entire
   inbound firehose under the chat.bot-only decision.`
8. `VERIFY: that a DWD-impersonated token can call media.upload — one live upload. Blocks: decision
   #9 outbound attachments entirely.`

**During Phase 1–2, cheap but consequential:**

9. `VERIFY: whether a DRIVE_FILE attachment posted by a coworker is readable by the ERPNext service
   account without an explicit Drive share — have a coworker attach a Drive file in a test space,
   then attempt files.get. This is an ACL question with real data-exposure consequences and must be
   answered before any attachment ingestion ships.`
10. `VERIFY: the maximum observed length and character set of real Chat message resource names — the
    N≥30 procedure across at least three spaces (one API-created, one human-created). Google
    documents the FORMAT of Message.name and Space.name and declines to bound either; the only
    documented bound is the client-assigned id at 63 chars. Not blocking under a Data(255) column,
    which is chosen precisely so this is a confirmation rather than a gate — but it also settles
    whether the name is URL-path-safe for SPA deep links.`
11. `VERIFY: which scope authorizes google.workspace.chat.message.v1.deleted — the event type exists
    in the event-type table and in the SpaceEvent resource, but a targeted second pass found no row
    for it in the scopes-by-event-type table. notes_gap_report.md §D-2 row 4 marked this "addressed";
    the sibling it cites says in terms "This is genuinely UNRESOLVED, and I will not guess", and
    notes_register_reconciled.md C1 resolves the disagreement in the sibling's favour. Settle with one
    subscriptions.create validateOnly holding only chat.messages.readonly. Blocks: the delete-sync
    path.`
12. `VERIFY: the exact Google Chat 429 response shape — does it carry Retry-After, and is the quota
    error distinguishable from a permission 403? The limits page documents the limits but not the
    error payload. Blocks: the token bucket's retry classification (§A.11.3).`
13. `VERIFY: the markupSyntax field's exact name, location and enum values — a release note dated
    2026-08-07 announces standard-Markdown message formatting via MARKUP_SYNTAX_MARKDOWN, but a
    targeted fetch of the spaces.messages/create reference the same day did NOT show the field. DO
    NOT code against markupSyntax until this is settled. If it is real it removes the need to
    translate ERPNext's Markdown-ish content into Chat's legacy markup, which is a genuine
    simplification for Phase 2.`

**Deferred, but recorded as deferred:**

14. `VERIFY: whether an import-mode message can be attributed to an arbitrary domain user and
    backdated — create a throwaway import-mode space, post one backdated message impersonating a test
    user, read it back and inspect sender and createTime. This is the single call that settles whether
    historical Chat backfill is possible at all. The docs never state the attribution rule outright,
    and neither does the reference page.`
15. `VERIFY: whether a space can re-enter import mode after completeImport (strongly implied not, no
    method exists), and whether an app can import into a space it did not create (UNRESOLVED — the
    overview page explicitly does not address it).`
16. `VERIFY: whether knowledge.workspace.google.com is now canonical or transitional — re-fetch one
    support.google.com/a/answer/... URL and see whether the 301 still fires. Blocks: only which URL
    the runbook cites.`

### E.7 Contradictions recorded in this section rather than resolved silently

1. **`notes_google_verify.md` §4e's "Option A (recommended) … Satisfies the locked product decision"
   is wrong**, and §E.3.6 says why. The finding itself — the enum, the constraints, the GA date — is
   sound; the *synthesis* built on it is not. `notes_gap_report.md` §D-1 is the correction, and the
   critic wins.
2. **`DECISIONS.md` D3 says "~40 lines of the assertion builder"; §E.4.2 sizes it at ~50 with a
   different composition** — no cryptography, because IAM Credentials signs, and a larger token cache
   than "assertion builder" implies. **A refinement, not a reversal**, reported per D8.
3. **`notes_gap_report.md` §E ranks the Raven-on-v16 experiment phase-blocking; `DECISIONS.md` D2
   records it non-blocking.** D2 is binding (§C.6).
4. **`notes_close_repo.md` §1.6 and `notes_close_frappe.md` §1.7 disagree about whether chat DocTypes
   carry any DocPerm** (§B.10.4). Unresolved between two closing notes; settled by one cheap check on
   the prod bench; the `_gate.py` denylist is required either way.
5. **The "~50 users" premise is ~2.5× the measured roster** (§A.0). It changes no decision but three
   numbers in this section — subscription count, renewal load and revocation surfaces — should be
   sized on ~20.

---

## F. Data model

### F.0 Scope, placement and the rules this section obeys

Everything below lives in a new self-contained `chat/` module inside `erpnext_enhancements`
(`DECISIONS.md` D1), i.e. `erpnext_enhancements/chat/doctype/<snake_name>/`. Every custom DocType
must sit in its declared module or `tests/test_doctype_modules.py` fails the build (`CLAUDE.md`), so
the module name is `Chat` and every DocType JSON below declares `"module": "Chat"`.

Four standing rules govern this section, and each is evidenced rather than asserted:

1. **The field-name canon is `DECISIONS.md` D5, verbatim.** No synonym is introduced. §F.1 carries
   the alias table so a session reading a later phase prompt in isolation does not invent a second
   schema.
2. **Every index is named with the query it serves** (§F.19 is the consolidated register). Indexes
   considered and *rejected* are listed with their reason, because at this site's measured volumes
   most candidate indexes are not worth their write cost.
3. **Column lengths and unique constraints are sized against two limits that nobody had put side by
   side until `notes_close_google.md` §1.5 did** — Frappe caps a `Data` field at 1000 characters
   while MariaDB 10.11 InnoDB `DYNAMIC` caps a single-column index key at 3072 bytes = 768 utf8mb4
   characters. §F.2 states the resulting rule.
4. **Volumes are sized on the measured roster, not on "~50 users."** The production site has
   **23 enabled Users, 20 enabled System Users, 18 active in the last 30 days, and 15 active
   Employees each with a linked User** (`notes_infra.md:140-151`). `notes_register_reconciled.md`
   C4 records that the "~50 users" premise carried through `DECISIONS.md` D1 and the research is
   roughly **2.5× the measured roster**, and that three numbers must not be quoted at 50: the
   per-coworker Workspace Events subscription count, the per-message fan-out estimate, and the
   volume model. Where a research estimate is quoted below it is quoted as an *upper* bound and
   labelled as such.

---

### F.1 The field-name canon (D5), and the alias map every later phase must read through

`DECISIONS.md` D5 adopts the master prompt's §5 names unchanged. They are canonical. Later phase
prompts use different words for the same fields; this is the mapping, and it is normative.

| Role in the design | **Canonical field (D5)** | On DocType | Aliases that appear in later phase prompts |
|---|---|---|---|
| Google Chat message resource name, `spaces/{space}/messages/{message}` | **`gchat_message_name`** | `Chat Message` | `google_message_name` |
| Google Chat thread resource name, `spaces/{space}/threads/{thread}` | **`gchat_thread_name`** | `Chat Message` | — |
| Our `client-`-prefixed idempotency id | **`client_message_id`** | `Chat Message` | `client_id`, `idempotency_key` |
| The message body | **`text`** | `Chat Message` | `content`, `body` |
| The authoring ERPNext user | **`sender`** | `Chat Message` | `author`, `user`, `from_user` |
| Relay/mirror state of this row | **`sync_state`** | `Chat Message` | `sync_status`, `relay_status` |
| Which side authored this row | **`sync_origin`** | `Chat Message` | `origin`, `source` |
| Google Chat space resource name, `spaces/{space}` | **`gchat_space_name`** | `Chat Room` | `google_space_name` |

Two consequences the ADR states now rather than letting Phase 2 discover them:

- **`gchat_thread_name` is the thread *resource name*, never a `threadKey`.**
  `Thread.threadKey` is documented at **"Supports up to 4000 characters"** and is the *only*
  documented length on the whole `Message` resource page (`notes_close_google.md` §1.4, quoting
  <https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages>). A Frappe
  `Data` field cannot hold 4000 characters (hard cap 1000, §F.2) and a `Text`/`Long Text` column
  cannot carry a unique index at all without a prefix, which Frappe cannot emit
  (`notes_close_google.md` §1.5, quoting `frappe/database/mariadb/database.py`'s
  `add_unique` → `", ".join(fields)`, bare column names, no `(n)` syntax). **Named rule:
  `threadKey` is never stored in a `Data` field and is never a key. Thread identity is
  `gchat_thread_name`.** Getting this wrong is a silent truncation at insert, not an error.
- **`sync_origin` is a three-valued Select, not a boolean.** `ERPNext` / `Google Chat` / `Triton`.
  Raven's nearest equivalent is a bare `is_synced` Check *with no counterpart id*
  (`notes_raven.md:1136-1140`), which is precisely why Raven gives zero prior art for a sync engine.

---

### F.2 Column length, unique constraints, and the two limits that collide

This closes Phase 0 §7's *"a unique constraint on the Google Chat message resource name is present
and its role in dedupe is stated"* and the `notes_gap_report.md` §A-6 / `notes_register_reconciled.md`
B3 blocker.

**What Google documents, and what it refuses to document** (all from `notes_close_google.md` §1.1–§1.3,
each with an explicit-negative fetch designed to make absence visible):

| Claim | Verdict |
|---|---|
| A maximum length for `Message.name` | **REFUTED — no such annotation exists** on the `spaces.messages` reference |
| A maximum length or charset for the `{space}` segment | **REFUTED — no such annotation** on the `spaces` reference or the create-space guide |
| A maximum for `clientAssignedMessageId` | **CONFIRMED — begins `client-`, up to 63 characters, lowercase letters/numbers/hyphens only, unique within a space** (<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>) |
| `Thread.threadKey` = 4000 characters | **CONFIRMED** |

Google documents the *format* of these identifiers and declines to bound them. Therefore: **treat
the length as unbounded-in-principle and bound it ourselves.**

**What Frappe and MariaDB impose, verified from source this session by `notes_close_google.md` §1.5:**

- `frappe/database/database.py`: `VARCHAR_LEN = 140` — a `Data` field is `varchar(140)` by default.
- `frappe/database/schema.py`, `DBTable.validate()`: `if not (1 <= new_length <= 1000): frappe.throw(...)`
  — a `Data` length is configurable, legal range **1–1000 inclusive**, and any value **below 64 is
  silently raised to 64**.
- `frappe/database/mariadb/database.py`: tables are `ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARACTER SET=utf8mb4`.
- MariaDB `DYNAMIC` row format supports a **3072-byte** index key prefix
  (<https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-row-formats/innodb-dynamic-row-format>),
  i.e. **768 utf8mb4 characters worst case.**
- Frappe's `add_unique` / `add_index` emit bare column names and **cannot express a prefix index.**

**The cliff, stated plainly: Frappe will let you declare `Data` with `length: 1000`, and that column
cannot be uniquely indexed on this database.** 1000 × 4 = 4000 bytes > 3072.

**Rule adopted.** Every Google resource-name column is `Data` with **`length: 255`**
(`varchar(255)` = 1020 bytes worst case, ~3× inside the index cliff, ~5× the largest observed value —
Google's own worked example `spaces/AAAAAAAAAAA/messages/BBBBBBBBBBB.BBBBBBBBBBB` is 51 characters).
`client_message_id` is `Data` with `length: 64` (the floor clamp makes anything smaller moot, and our
derivation in §G.2 produces exactly 39 characters).

**Rejected alternatives, named so a later phase does not re-litigate them:**
- *`Data(140)`, the default* — probably enough, not **provably** enough, and §7's criterion forbids
  "probably". Neither `|space|` nor the system-assigned message id has a documented ceiling.
- *A prefix index* — unnecessary at 1020 bytes and inexpressible through Frappe's helpers.
- *A `sha256`-of-resource-name `Data(64)` hash column* — the correct shape only if the natural key
  could exceed 768 characters. It cannot here, and it costs the ability to
  `SELECT ... WHERE gchat_message_name = %s` on the index and to read the key in a debug session.
  **Recorded as the escape hatch** if a future Google identifier format ever blows past 768.

**THE TRAP, and it is severe.** From `frappe/model/base_document.py`, `get_valid_dict`
(quoted at `notes_close_google.md` §1.6):

```python
elif (fieldtype in datetime_fields and value == "") or (
    getattr(df, "unique", False) and cstr(value).strip() == ""
):
    value = None
```

Frappe coerces `""` → `NULL` **only when the DocField itself carries `unique: 1`**, and MariaDB
permits unlimited `NULL`s in a unique index. So:

- **`unique: 1` declared on the DocField** → every not-yet-relayed message stores `NULL`, they
  coexist, the first successful relay writes the real name. **This is what we do.**
- The index created **only** by `frappe.db.add_unique(...)` in a patch, with no `unique: 1` on the
  field → the coercion never fires, unrelayed rows store `""`, and **the second unrelayed message in
  the entire table fails to insert.** A production-only failure invisible to any test that relays
  synchronously.

`notes_gap_report.md` §0-5 offered these as equivalent options. **They are not**, and
`notes_close_google.md` §5.2 says so. The rule: **single-field unique constraints are declared
`unique: 1` on the DocField; `frappe.db.add_unique(doctype, fields, constraint_name=None)` in a
patch is used only for composite constraints, which have no DocField equivalent — and every column
in a composite unique constraint must be guaranteed non-empty at insert time.**

`VERIFY: the Frappe empty-vs-NULL coercion end to end` — the source says `unique: 1` coerces `""` →
`None`; nobody has run it. **Settle:** on a bench, insert two `Chat Message` rows with
`gchat_message_name` unset and run ``SELECT COUNT(*) FROM `tabChat Message` WHERE gchat_message_name IS NULL``.
**Blocks:** the inbound dedupe index; failure is production-only and looks like a random insert error.
(`notes_close_google.md` §6 item 5.)

`VERIFY: the maximum observed length and character set of real Chat message resource names` — the
full N≥30 / three-spaces / both-auth-paths procedure is specified at `notes_close_google.md` §1.7,
with pass criterion `max(len(name)) + 100 <= 255`. **Blocks:** nothing under the 255 recommendation —
255 is sized precisely so this is a confirmation, not a gate. It additionally settles whether the
name is safe as a URL path segment in the SPA's deep links.

---

### F.3 DocType inventory

Eleven new standard DocTypes plus three child tables — fourteen rows. "Volume" is the expected row growth at the measured
roster; "DocPerm" is whether the DocType JSON's `permissions` array is empty (see §F.18).

| # | DocType | Shape | Autoname | Volume | DocPerm |
|---|---|---|---|---|---|
| 1 | `Chat Room` | standard | `hash` | ~10²–10³ | **none** |
| 2 | `Chat Room Member` | standard | `hash` | rooms × members | **none** |
| 3 | `Chat Message` | standard | **`hash`** | the hot table | **none** |
| 4 | `Chat Mention` | **child** of `Chat Message` | — | ≤ a few per message | n/a |
| 5 | `Chat Attachment` | standard | `hash` | low | **none** |
| 6 | `Chat Relay Job` | standard | `hash` | ≥ 1 per outbound op | **none** |
| 7 | `Chat Inbound Event` | standard | `hash` | 1 per inbound event | **none** |
| 8 | `Chat Context Chunk` | standard | `hash` | messages ÷ ~15 | **none** |
| 9 | `Chat Room Digest` | standard | `hash` | 1 per room | **none** |
| 10 | `Chat Thread Digest` | standard | `hash` | 1 per thread | **none** |
| 11 | `Chat Retrieval Audit` | standard | `hash` | 1 per privileged read | System Manager `read` |
| 12 | `Chat Retrieval Audit Room` | **child** of #11 | — | rooms per read | n/a |
| 13 | `Chat Settings` | **Single** | — | 1 | System Manager `read`/`write` |
| 14 | `Chat Allowed User` | **child** of `Chat Settings` | — | ≤ roster | n/a |

**Name-collision check, measured.** `notes_infra.md:640-681` ran the collision sweep against the
live production `tabDocType`: eleven matches for
`'Chat|Message|Conversation|Notification|Presence|Thread|Channel|Mention|Reaction|Inbox'`, **all
core, zero custom**. Its verdict: *"The names `Chat*`, `Message*`, `Conversation*`, `Presence*` are
entirely free."* Names to stay away from: anything leading with `Notification` (six core DocTypes in
`Desk`, two in `Email`); `Training Question Thread` exists (Training); `Marketing Web Channel` exists
(KPI Dashboards) — which is one reason this design says **Room**, not **Channel**.

**Deliberately NOT created, with the reason:**

| Not created | Why |
|---|---|
| `Chat Read Receipt` | §F.15 — the dominant scaling risk; replaced by a high-water mark |
| `Chat Presence`, `Chat Typing` | §F.14 — Redis with a TTL; as DocTypes they would be ~200× the entire site's current write volume |
| `Chat Reaction` | out of scope for V1: `spaces.messages.reactions.create/delete` are **user-auth only** (`notes_research_gaps.md:575`), so every reaction would cost a DWD impersonation and a per-space write token. Named in §G.12's lossy list. |
| `Chat Space Provisioning Run` | resumability is expressed by `Chat Relay Job` rows plus `Chat Room.provisioning_state`; a second table would duplicate the retry budget |
| A separate `Chat Audit Log` | `Chat Retrieval Audit` covers privileged reads; deletions are captured on the message row (§F.6); MCP refusals already write `AI Action Log` via the existing `insert_action_log` (`assistant_tools/_gate.py:341-396`, per `notes_close_repo.md` §7) |

---

### F.4 `Chat Room`

Covers all four shapes decision #10 requires — 1:1 DM, group space, org-mirrored, per-document —
with `room_type` as the discriminator. Lifted from Raven: `linked_doctype` / `linked_document`, and
the denormalised last-message fields (`DECISIONS.md` D2's explicit lift list; the originals are
`notes_raven.md:299-315`).

**Autoname `hash`, not a readable slug.** Raven names a regular channel
`"{workspace}-{slugified-name}"` and a thread channel after the parent message id
(`notes_raven.md:268-282`), which mixes three entity kinds under three naming schemes in one table
and leaves DM naming undefined enough that the audit had to raise a `VERIFY` about it
(`notes_raven.md:283-288`). We take the hash and put uniqueness where it belongs — in indexes.

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `room_type` | Select | `Direct Message` / `Group` / `Organization` / `Document` | reqd, set_only_once, in_list_view | the discriminator |
| 2 | `title` | Data | 140 | | absent for DMs (rendered from the peer) |
| 3 | `description` | Small Text | | | |
| 4 | `is_archived` | Check | default 0 | | |
| 5 | `linked_doctype` | Link | DocType | read_only | per-document rooms only |
| 6 | `linked_document` | Dynamic Link | `linked_doctype` | read_only | per-document rooms only |
| 7 | `dm_user_1` | Link | User | read_only | lexicographically lower of the pair |
| 8 | `dm_user_2` | Link | User | read_only | lexicographically higher of the pair |
| 9 | `provisioning_mode` | Select | `Not Mirrored` / `On Create` / `On First Message` | reqd, default `On First Message` | §F.4.1 |
| 10 | `provisioning_state` | Select | `Pending` / `Provisioning` / `Ready` / `Failed` / `Disabled` | read_only, default `Pending` | |
| 11 | `provisioning_attempts` | Int | default 0 | read_only | |
| 12 | `provisioning_error` | Small Text | | read_only | truncated to 1000 at write |
| 13 | **`gchat_space_name`** | **Data** | **255** | **`unique: 1`**, read_only | D5 canon; `spaces/{space}` |
| 14 | `gchat_space_type` | Select | `SPACE` / `GROUP_CHAT` / `DIRECT_MESSAGE` | read_only | mirrors Google `SpaceType` |
| 15 | `gchat_threading_state` | Select | `THREADED_MESSAGES` / `GROUPED_MESSAGES` / `UNTHREADED_MESSAGES` | read_only | read back from `spaces.get`; §G.9 |
| 16 | `gchat_space_uri` | Data | 255 | read_only | deep link into the native client |
| 17 | `seq_high_water` | Int | default 0 | read_only, **advisory** | §F.16 — a reporting mirror, never the allocator |
| 18 | `last_message` | Link | Chat Message | read_only | denormalised |
| 19 | `last_message_at` | Datetime | | read_only | denormalised |
| 20 | `last_message_sender` | Link | User | read_only | denormalised |
| 21 | `last_message_preview` | Small Text | | read_only | denormalised, truncated to 200 chars, **plain text** |
| 22 | `retention_days` | Int | default 0 | | 0 = inherit `Chat Settings` |

**On the denormalised last-message fields.** Raven writes its equivalents
(`last_message_timestamp`, `last_message_details` JSON) from the message's `after_insert` via
`frappe.qb` direct SQL, deliberately bypassing the ORM so it does not re-trigger the channel's
`on_update` hooks (`notes_raven.md:912-917`). We copy that, with one change: **four typed columns
instead of one JSON blob**, because the room-list query sorts on `last_message_at` and a JSON blob
cannot be sorted or filtered without extraction. The write is
`frappe.db.set_value("Chat Room", room, {...}, update_modified=False)` — `update_modified=False`
because these fields are a cache of the message table, and churning `Chat Room.modified` on every
message would make the room row's `modified` meaningless for any future staleness check.

**`last_message_preview` is plain text, and is escaped by us.** `notes_infra.md:357-359` measured
that core Notification Log `subject` values carry raw HTML (`<strong>`, `<b class="subject-title">`)
and that the bell renders it as markup. The room list renders this preview; storing the raw `text`
here would carry any HTML in the body straight into a second render surface.

**`message_count` is deliberately absent.** The D6 watermark needs an exact `count(*)`; a
denormalised counter that drifts silently corrupts cache invalidation, which is the one failure mode
D6 exists to prevent. The count is computed by SQL against the `unique(room, seq)` index, which makes
it an index-only range count.

#### F.4.1 `provisioning_mode` — what it is and why it exists

`provisioning_mode` decides **when** the Google space backing a room is created, not **whether** the
room exists. Three values:

- **`Not Mirrored`** — an ERPNext-only room. No `spaces.create`, no relay, no subscription concern.
  This is the correct mode for rooms whose content must not leave ERPNext, and the mode per-document
  rooms on sensitive doctypes should default to.
- **`On Create`** — provision the space at room creation. Correct for the org-mirrored rooms and for
  a DM the moment two people open one.
- **`On First Message`** — **the default.** Provision lazily, on the first outbound message.

The default is `On First Message` for a measured reason. Project-wide **space writes are capped at
60 per 60 seconds** (`spaces.setup`, `spaces.create`, `spaces.patch`, `spaces.delete` —
`notes_google_verify.md:973`, quoting <https://developers.google.com/workspace/chat/limits>). If
decision #10's per-document rooms are auto-created from a `doc_event` on, say, Project and Task, the
site already holds **6,535 Tasks** (`notes_infra.md:215`). Eagerly provisioning a Google space for
each would take a minimum of 6,535 / 60 = **109 minutes of saturated project-wide space-write
quota**, and would put thousands of empty spaces in every coworker's Chat client. Lazy provisioning
makes the Google-side cost proportional to actual conversation.

#### F.4.2 Indexes on `Chat Room`

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`gchat_space_name`** | **UNIQUE**, declared `unique: 1` on the DocField | *"An inbound Workspace Event carries `spaces/{space}`; find the one ERPNext room it maps to."* It is also the structural guarantee that two rooms can never bind to the same Google space, which would double-deliver every inbound message. |
| **`(linked_doctype, linked_document)`** | **UNIQUE**, via `frappe.db.add_unique` in a patch | *"Given a Project or Task, find-or-create its per-document room exactly once"* — and, because two `doc_event` workers can race on the same document, the constraint is what makes `get_or_create_document_room()` idempotent without a lock. **Both columns must be non-empty when either is set**, or the composite hits the empty-string trap of §F.2; the `before_insert` hook sets both or neither. |
| **`(dm_user_1, dm_user_2)`** | UNIQUE, via patch | *"Open the DM between A and B"* — with the pair stored in lexicographic order so `(A,B)` and `(B,A)` resolve to one row. Same non-empty obligation. |

**Rejected, with reasons:** an index on `provisioning_state` (the provisioning sweeper scans a few
hundred rows every five minutes; the scan is cheaper than the index maintenance — revisit above
~50k rooms); an index on `last_message_at` (the room list is driven from `Chat Room Member` and joins
to a few hundred rooms, so the sort is a filesort over a tiny set); an index on `room_type` (low
cardinality, and no query filters on it alone).

---

### F.5 `Chat Room Member`

A **standalone DocType, not a child table of `Chat Room`** — Raven's pivotal modelling choice
(`notes_raven.md:398-399`, `:453`) and a `DECISIONS.md` D2 lift. A child table would be loaded in
full with the room and could not be indexed on `user`, which is the leading column of the single
most-run query in the system.

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `room` | Link | Chat Room | reqd, set_only_once | |
| 2 | `user` | Link | User | reqd, set_only_once | |
| 3 | `role` | Select | `Member` / `Manager` | reqd, default `Member` | |
| 4 | `is_active` | Check | default 1 | | soft removal; §F.5.1 |
| 5 | `joined_at` | Datetime | | read_only | |
| 6 | `left_at` | Datetime | | read_only | |
| 7 | `derived_from_document` | Check | default 0 | read_only | membership materialised from the linked document's permissions |
| 8 | **`last_read_seq`** | **Int** | default 0 | read_only | **the read high-water mark — §F.15** |
| 9 | `last_read_at` | Datetime | | read_only | when the mark last advanced |
| 10 | `notification_mode` | Select | `All` / `Mentions Only` / `None` | default `All` | |
| 11 | `muted_until` | Datetime | | | |
| 12 | `gchat_membership_name` | Data | 255 | read_only | `spaces/{space}/members/{member}` |
| 13 | `gchat_member_state` | Select | `JOINED` / `INVITED` / `NOT_A_MEMBER` | read_only | mirrors Google `MembershipState` |
| 14 | `sync_state` | Select | `Pending` / `Relayed` / `Failed` / `Not Mirrored` | read_only, default `Pending` | same vocabulary as `Chat Message.sync_state` |

#### F.5.1 Membership is soft-deleted, and the reason is auditable history

`is_active = 0` + `left_at`, never a row delete. Three consequences, all wanted: the audit trail for
decision #12 can answer *"was this person in the room when that was said"*; the `unique(room, user)`
constraint stays meaningful across leave-and-rejoin; and the Google-side membership resource name
survives, so a re-add can be reconciled rather than blindly re-created.

`derived_from_document = 1` marks rows a per-document room materialises from the linked document's
own permissions. R03 §4.4's recommendation, carried at `notes_research_gaps.md:1182`, is to
**materialise membership into rows** rather than evaluate `frappe.has_permission(doc)` per candidate
at read time, **plus a nightly reconciliation job whose non-empty diff is an alert.** We adopt both.
The alert matters: a silent drift here is a person reading a room whose underlying document they no
longer have access to.

#### F.5.2 Indexes on `Chat Room Member`

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`(room, user)`** | **UNIQUE**, via `frappe.db.add_unique` | *"Is this user a member of this room?"* — the membership check that gates **every** read in the system: the whitelisted room-open endpoint, every realtime fan-out, and every retrieval call. It is also the structural fix for the duplicate-member row Raven had to add a patch for and which its JSON still does not declare (`notes_raven.md:416-424`). Both columns are `reqd`, so the empty-string trap of §F.2 cannot fire. |
| **`(user, is_active)`** | index, via `frappe.db.add_index` | *"Every room this user is currently in"* — the first query the SPA runs on boot, the query that builds the unread badge, and the query the retrieval gate runs to derive `allowed_rooms`. `unique(room, user)` cannot serve it: its leading column is `room`. |

**Rejected:** an index on `last_read_seq` (no query filters on it; it is only ever read for a row
already located by `(room, user)` or `(user, is_active)`); an index on `gchat_membership_name`
(membership events are reconciled by `(room, user)` after resolving the Google user to an ERPNext
`User`, which is a Directory lookup, not an index probe).

---

### F.6 `Chat Message` — the hot table

#### F.6.1 `autoname: "hash"`, `naming_rule: "Random"` — and why the two alternatives are forbidden

**Never a naming series.** A Frappe naming series allocates from a single `tabSeries` row under a
row lock held for the duration of the inserting transaction. Every message insert on the site would
therefore serialize on one row. The research's own volume model puts steady state at
*"2,000 messages/day ≈ 0.023 writes/sec average, maybe 2–3/sec at a standup spike"*
(`notes_research_gaps.md:436`) — that is survivable, but the series also couples message insertion to
whatever else on the site touches the same series machinery, and it leaks strict global ordering
information into a primary key that is quoted in URLs. Phase 0 §4.G's own prohibition is recorded at
`notes_close_repo.md:1031`: *"series counters serialize inserts."*

**Never a child table of `Chat Room`.** Frappe loads child tables **in full** with the parent, so
`frappe.get_doc("Chat Room", x)` would materialise every message ever sent in that room. The rule is
stated flatly at `notes_research_gaps.md:1121`: *"Never store messages as a child table of a room."*
Raven independently reached the same shape — three top-level DocTypes, child tables reserved for
genuinely subordinate data (`notes_raven.md:453`).

**Hash it is**, matching Raven's `Raven Message` (`"autoname": "hash"`, `"naming_rule": "Random"` —
`notes_raven.md:336`) and this repo's own outbox precedent, `Drive Sync Log`
(`notes_close_repo.md:1031`). `VERIFY: the exact length of a Frappe v16 hash name` — the research
records "10 chars from a hash" (`notes_research_gaps.md:417`, R02-V02, still open). **Settle:**
`frappe.generate_hash()` in a bench console, and record both length and alphabet. **Blocks:** nothing
structurally — §G.2's `client_message_id` derivation is deliberately built so that neither the length
nor the alphabet of the hash matters.

#### F.6.2 Fields

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `room` | Link | Chat Room | reqd, set_only_once | |
| 2 | **`seq`** | **Int** | | reqd, read_only | **D6 — the monotonic per-room sequence, §F.16** |
| 3 | **`sender`** | Link | User | reqd, set_only_once | **D5 canon** (alias: `author`) |
| 4 | `sender_kind` | Select | `Human` / `Triton` / `System` | reqd, default `Human` | drives rendering and unread exclusion |
| 5 | `message_type` | Select | `Text` / `File` / `System` | reqd, default `Text` | |
| 6 | **`text`** | **Long Text** | | | **D5 canon** (alias: `content`) — the body as authored |
| 7 | `text_plain` | Long Text | | read_only | plain-text extraction; §F.6.3 |
| 8 | `parent_message` | Link | Chat Message | set_only_once | the reply edge |
| 9 | `thread_root` | Link | Chat Message | read_only | denormalised root; §F.6.4 |
| 10 | `mentions` | Table | Chat Mention | | child table; §F.7 |
| 11 | `has_attachments` | Check | default 0 | read_only | render flag |
| 12 | `is_edited` | Check | default 0 | read_only | |
| 13 | `edited_at` | Datetime | | read_only | |
| 14 | `is_deleted` | Check | default 0 | read_only | **soft delete; §F.6.5** |
| 15 | `deleted_at` | Datetime | | read_only | |
| 16 | `deleted_by` | Link | User | read_only | |
| 17 | `deletion_source` | Select | `ERPNext` / `Google Chat` / `Retention` / `Admin` | read_only | mirrors Google `DeletionType` semantics |
| 18 | **`gchat_message_name`** | **Data** | **255** | **`unique: 1`**, read_only | **D5 canon** (alias: `google_message_name`) |
| 19 | **`gchat_thread_name`** | **Data** | **255** | read_only | **D5 canon** — the thread *resource name*, never a `threadKey` |
| 20 | `gchat_create_time` | Datetime | | read_only | Google `Message.createTime` |
| 21 | `gchat_last_update_time` | Datetime | | read_only | Google `Message.lastUpdateTime` — §G.8 rule 3 |
| 22 | **`client_message_id`** | **Data** | **64** | reqd, read_only | **D5 canon** — §G.2 |
| 23 | **`sync_state`** | Select | `Pending` / `Relayed` / `Failed` / `Not Mirrored` / `Inbound` | read_only, default `Pending` | **D5 canon** (alias: `sync_status`) |
| 24 | **`sync_origin`** | Select | `ERPNext` / `Google Chat` / `Triton` | reqd, read_only, default `ERPNext` | **D5 canon** (alias: `origin`) |
| 25 | `truncated_for_relay` | Check | default 0 | read_only | §G.10 — this body did not fit in 32,000 bytes |

**`sync_state` and `sync_origin` share a vocabulary with `Chat Room Member.sync_state` and
`Chat Relay Job`,** deliberately, so an operator reading three tables in an incident does not have to
translate.

**Every write to `sync_state` uses `frappe.db.set_value(..., update_modified=False)`.** This is not a
style preference. `DECISIONS.md` D6 makes the digest/cache watermark
`(max(seq), count(*), max(modified))`, so a relay retry that touched `modified` would invalidate every
cached digest for that room on every attempt. `notes_close_repo.md` §3.3 note 5 flags exactly this
interaction with the house sweeper pattern. **Named rule: relay bookkeeping never advances
`Chat Message.modified`.** Its test is in §F.16.

**Relay attempt counters, error text and HTTP status are deliberately NOT on this table.** They live
on `Chat Relay Job` (§F.9). Two reasons: the same `modified`-churn argument above, and the fact that
outbound work includes operations with no `Chat Message` row at all (space creation, membership
changes).

#### F.6.3 `text` and `text_plain`

`text` is the body as authored, in the SPA's markup. `text_plain` is the read-only plain-text
extraction, and it earns its storage three times over:

- it is what §G.10's 32,000-**byte** budget is computed against, after UTF-8 encoding;
- it is what `Chat Context Chunk.body` is assembled from (§F.11), so retrieval never re-parses markup;
- it is what `Chat Room.last_message_preview` is truncated from.

Cost at the research's upper-bound volume: ~500k messages/year (`notes_research_gaps.md:432`) at a
mean of ~20 tokens is on the order of tens of megabytes a year — against a site whose largest table
today is `tabQuickBooks Raw Payload` at 453 MB (`notes_infra.md:195`). It is not a scaling concern.

**Rendering rule inherited from the widget audit:** `notes_gap_report.md` §E item 20 carries an open
`VERIFY` that prod Frappe may predate the commit which makes `frappe.markdown` sanitize. Until that
is settled, chat bodies are sanitized on the way **out** by the SPA using DOM APIs, never
`innerHTML` (`notes_research_gaps.md:1185`). The data model's contribution is only that `text` is
stored verbatim and never trusted.

#### F.6.4 Threading inside ERPNext: `parent_message` + `thread_root`

ERPNext models threading with two fields, not with Raven's scheme. Raven makes a thread **a
`Raven Channel` whose primary key equals the parent message's hash name** (`notes_raven.md:459-471`),
which is elegant but mixes three entity kinds in one table under three naming schemes — and it would
force us to provision a Google space per thread.

`parent_message` is the edge; `thread_root` is the denormalised root so the thread panel is one
indexed range scan instead of a recursive walk. Both are maintained in `before_insert`:
`thread_root = parent.thread_root or parent.name`. Depth is **one level** — a reply to a reply
attaches to the same root. This mirrors the existing Comments App in this repo, which is
*"single-level, Slack-style"* (`notes_infra.md:687-689`), and it means `thread_root` never needs
recomputation.

**This is the field pair that survives §G.9's threading risk.** If it turns out that API-created
Google spaces cannot be threaded at all — `spaceThreadingState` is **Output only**
(`notes_google_verify.md:580`) and `spaces.setup` states *"Spaces with threaded replies aren't
supported"* (`notes_google_verify.md:619`) — then ERPNext still has complete thread structure and
only the Chat-side rendering is lossy. Decision #5's "replies in-thread" degrades in Chat, not in the
source of truth.

#### F.6.5 Delete is soft, and that is a policy choice the human must confirm

`is_deleted = 1` plus `deleted_at` / `deleted_by` / `deletion_source`. **`text` is retained on the
row.** Every read path filters `is_deleted = 0`; the only path that does not is the Phase 6 oversight
endpoint, and it writes a `Chat Retrieval Audit` row for the privilege (§F.12).

This is what satisfies invariant I10 — the audit trail must survive a user-facing delete — and it is
the only design that can, because **Google's own tombstone is content-free**: `showDeleted` returns
`name`, `deleteTime` and `deletionMetadata` but *"message content is unavailable"*
(`notes_google_verify.md:866-871`). If ERPNext does not keep the body, nobody has it.

The alternative considered and rejected: move the body to an append-only audit row and null the
column. Rejected because it creates a second source of truth for message text, doubles the write on
every delete, and buys nothing that a read filter does not already buy — the MCP surface is closed by
§F.18 regardless of which table the bytes are in.

**Human question (CQ).** "Delete" in this design is a visibility change, not an erasure. A hard
erase is available (`Retention` purge, §F.17) but it is time-based and global, not per-message. Whether
an employee's "delete this message" must eventually mean "the bytes are gone" is an
employment-law/records-policy question, not an engineering one. **Recommendation: keep soft delete,
and add a `Chat Settings.hard_delete_after_days` that promotes soft-deleted rows to real deletion on a
schedule** — that gives the audit its window and the employee their erasure, with the window as the
single dial the human sets.

#### F.6.6 Indexes on `Chat Message`

This is the most consequential index set in the design.

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`(room, seq)`** | **UNIQUE**, via `frappe.db.add_unique` | *"The room backlog page: ``WHERE room = %s AND seq < %s ORDER BY seq DESC LIMIT 50``"* — the only pagination the SPA performs. The same index serves the unread count (``COUNT(*) WHERE room = %s AND seq > %s``), the D6 watermark's `count(*)`, and the seq allocator's `MAX(seq)` probe. It is simultaneously the structural enforcement of D6's *"unique per (room, seq)"*. Both columns are `reqd`, so §F.2's empty-string trap cannot fire. |
| **`gchat_message_name`** | **UNIQUE**, declared **`unique: 1` on the DocField** | *"Given an inbound resource name, has this Google message already been stored?"* — one index probe. This is what makes dedupe **structural rather than procedural**: a Workspace Events redelivery, a Pub/Sub at-least-once duplicate, and a `spaces.spaceEvents.list` reconciliation replay of the same 28-day window all converge on the same resource name, and the second insert is a `DuplicateEntryError` the relay treats as success. Declared on the DocField, not in a patch, for the empty-string→`NULL` coercion of §F.2. |
| **`(room, client_message_id)`** | **UNIQUE**, via `frappe.db.add_unique` | *"Given an inbound message carrying `clientAssignedMessageId`, is this our own message coming back?"* — echo suppression in one probe (§G.3). Scoped to `room` because Google guarantees `messageId` uniqueness **within a space**, so mirroring its own rule is correct and inventing a stricter global one is not. `client_message_id` is `reqd` and generated at insert, before any relay is attempted, so it is never empty. |
| **`(thread_root, seq)`** | index | *"The thread panel: every reply under this root, in order."* |
| **`gchat_thread_name`** | index | *"An inbound threaded reply names `spaces/{s}/threads/{t}` and we do not know its ERPNext parent; find the root message already bound to that thread."* |
| `creation` | index | **`VERIFY:`** assumed to be created by Frappe by default, inferred from a `MUL` key observed on the live `tabNotification Log` (`notes_infra.md:273`) — but Frappe's MariaDB table template indexes `modified`, not `creation`, and that observation is equally explained by that DocType's own `search_index`. The patch therefore does **not** create it. Settle with `SHOW INDEX FROM \`tabChat Message\`` after migrate; see §F.19 for the remedy if it is absent. Used only by the Phase 6 retention purge's `creation < cutoff` scan, so nothing before Phase 6 depends on the answer. |

**Rejected, with reasons — each of these is a plausible index that does not earn its write cost:**

- `(sender, creation)` — "all messages by X" is an oversight-view query run by one person
  occasionally. It may scan.
- `sync_state` — the outbound sweeper queries `Chat Relay Job`, not this table (§F.9). No hot query
  filters messages by sync state.
- `is_deleted` — low cardinality and always combined with `room`; the `(room, seq)` range already
  narrows to ≤50 rows before the filter applies.
- A FULLTEXT index on `text_plain` — genuinely tempting, and deliberately deferred. Verified
  constraints: InnoDB FULLTEXT has a 3-character minimum token, sees **committed rows only**, and
  **partitioned tables cannot have FULLTEXT indexes on any engine**
  (`notes_research_gaps.md:473-477`). V1 lexical search is a `LIKE` over the permission-filtered
  candidate set, which at ~500k rows/year and a membership filter is small. Revisit with the same
  numeric triggers as D4 (§F.11).

**Ordering rule, stronger than the research's.** `notes_research_gaps.md:1122` says *"Do not sort chat
history by `modified`. Sort by `creation`."* **We sort by `seq`.** Two reasons `creation` is not good
enough: two inserts into one room can share a `creation` value to the microsecond under concurrency,
whereas `(room, seq)` is unique by construction; and `creation` is written in **site-local time while
the production database clock is UTC** — measured this session at
`notes_register_reconciled.md` C7, where `NOW() == UTC_TIMESTAMP()` but the newest
`tabScheduled Job Log.creation` was exactly six hours earlier. Any SQL-side comparison of a
Frappe-written timestamp to `NOW()` is silently wrong by the site's UTC offset. `seq` has no timezone.

---

### F.7 `Chat Mention` (child table of `Chat Message`)

`"istable": 1`, no `permissions` array (child tables are governed by the parent). This is a
`DECISIONS.md` D2 lift — *"mentions as a child table plus a per-user realtime event"* — confirmed
exactly in Raven (`notes_raven.md:456`), whose own `Raven Mention` is a single `user` field.

| # | fieldname | fieldtype | options | notes |
|---|---|---|---|---|
| 1 | `mention_type` | Select | `User` / `Room` / `Triton` | reqd |
| 2 | `user` | Link → User | | set when `mention_type = User` |
| 3 | `start_index` | Int | | offset into `text_plain` |
| 4 | `length` | Int | | span length |

Two departures from Raven, both deliberate:

- **`user` is a `Link`, not `Data`.** Raven uses `Data` (`notes_raven.md:431`). A `Link` participates
  in rename and link validation, which matters because the mention is the input to a notification and
  a dangling mention is a notification that goes nowhere. Cost is one link-validation query per
  mention on insert; mentions per message are bounded by human patience.
- **`start_index` / `length` exist** so the SPA can render the mention span without re-parsing the
  body, and so an inbound Google mention can be stored faithfully. R01's rule, carried at
  `notes_research_gaps.md:1168`, is *"parse the annotation, do **not** regex the raw `text`"* — Google
  delivers mentions as `annotations` of type `USER_MENTION` plus a separate `argumentText`.

`VERIFY: the exact sub-field names of a Google Chat `USER_MENTION` annotation (offset/length and the
user resource)` — `notes_research_gaps.md:609,649` names the `annotations` field and the
`USER_MENTION` type but not the annotation's own sub-fields, and no agent fetched the `Annotation`
reference. **Settle:** read
<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages#Annotation>, or
capture one real payload in the pilot space. **Blocks:** faithful inbound mention rendering only —
the notification itself can be driven from the resolved user list without offsets.

**Indexes: none.** Child tables carry Frappe's standard `parent` index, which is the only access
path (`WHERE parent = %s`). A separate index on `user` would serve "every message mentioning me",
but that query is answered from `Notification Log` (§F.17), not from the child table.

---

### F.8 `Chat Attachment`, Frappe `File`, and private-file access control

**The bytes live in a core Frappe `File` row. The Google-side identity lives in a sidecar DocType.**
The sidecar rather than Custom Fields on `File`, because `File` is a core table every app on the
bench writes to, and `notes_raven.md:1075-1079` records the general lesson: *"Sidecar is the only
safe option"* when you need to hang your own identity off somebody else's doctype.

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `message` | Link | Chat Message | reqd, set_only_once | |
| 2 | `room` | Link | Chat Room | reqd, read_only | **denormalised on purpose** — the download endpoint's membership check must not join through `Chat Message` |
| 3 | `file` | Link | File | read_only | the private `File` row; empty for `Drive Link` |
| 4 | `file_name` | Data | 255 | read_only | Google `Attachment.contentName` — the original filename, not a path |
| 5 | `content_type` | Data | 140 | read_only | Google `Attachment.contentType` |
| 6 | `file_size` | Int | | read_only | bytes |
| 7 | `source` | Select | `Uploaded` / `Drive Link` / `ERPNext` | reqd | mirrors Google `Attachment.source` (`UPLOADED_CONTENT` / `DRIVE_FILE`) |
| 8 | `gchat_attachment_name` | Data | 255 | read_only | `spaces/{s}/messages/{m}/attachments/{a}` |
| 9 | `gchat_attachment_data_ref` | Data | 255 | read_only | `attachmentDataRef.resourceName` — **the only legitimate download handle** |
| 10 | `drive_file_id` | Data | 140 | read_only | `driveDataRef.driveFileId` |
| 11 | `ingest_state` | Select | `Pending` / `Stored` / `Linked` / `Skipped` / `Failed` | read_only, default `Pending` | |
| 12 | `skip_reason` | Small Text | | read_only | e.g. blocked type, over the size ceiling |

**Rules this table encodes, each from a verified fact:**

1. **Every stored chat attachment is `is_private = 1` and is attached to its `Chat Message`.**
   `attached_to_doctype = "Chat Message"`, `attached_to_name = <message>`. R02 §8's phrasing, carried
   at `notes_research_gaps.md:1174`, is that this is *"the single decision that makes attachment
   security correct by construction"*. It also makes the `File` row garbage-collect with the message.
2. **Never fetch `downloadUri` or `thumbnailUri`.** Both are documented as browser-session URLs for
   humans, with Google stating twice that *"Chat apps shouldn't use this URL to download attachment
   content"* (`notes_google_verify.md:801-812`). The programmatic path is `media.download` with
   `attachmentDataRef.resourceName`, which is why field 9 exists and field-shaped `downloadUri` does
   not. Google does not document how long `downloadUri` stays valid, and this note will not invent a
   TTL.
3. **`DRIVE_FILE` attachments are stored as links, not copies** (`source = Drive Link`,
   `drive_file_id` set, `file` empty). `media.download` explicitly *"Downloads uploaded media, **but
   not Google Drive files**"* (`notes_google_verify.md:788-789`), and the two sources have entirely
   different ACL owners: Chat-hosted blobs are gated by space membership, Drive files by Drive
   permissions **independent of the Chat space** (`notes_google_verify.md:781-786`). Copying a Drive
   file into ERPNext would re-home somebody else's ACL decision inside our permission model.
4. **Outbound attachments require DWD.** `media.upload` is documented *"Requires user
   authentication"* and `chat.bot` is absent from its scope list (`notes_google_verify.md:714-727`).
   App auth **can** download and **cannot** upload. This is one leg of the D3 trilemma and it is why
   `DECISIONS.md` D3 routes attachment uploads through the DWD identity.
5. **An attachment message costs two write tokens**, because `media.upload` shares the per-space
   1-write/second bucket with `messages.create` (`notes_google_verify.md:1001-1003`; `DECISIONS.md`
   D8). Enforced in §G.1.4, recorded here because it is a property of the data, not of the code.

#### F.8.1 The private-file consequence of the zero-DocPerm decision — follow it through

Frappe serves a private file at `/private/files/<name>` behind a permission check that consults the
`File` row's `attached_to_doctype` / `attached_to_name`. **With zero DocPerm on `Chat Message`
(§F.18), that check fails for every user except `Administrator`** — so a chat attachment would be
unreadable through the standard private-file route **by its own room members**.

This is not a defect to work around; it is the same consequence, in a second place, of the
permission decision. The answer is the same as everywhere else in this design: **a whitelisted
endpoint that establishes membership itself.**

```
@frappe.whitelist()
def download_attachment(attachment: str):
    row = frappe.db.get_value("Chat Attachment", attachment,
                              ["room", "file", "source", "file_name", "content_type"], as_dict=True)
    if not row: raise frappe.PermissionError
    _require_member(row.room)                 # the (room, user) index probe of F.5.2
    _audit_read(purpose="attachment", rooms=[row.room], count=1)
    ...stream the private File's bytes with an explicit Content-Disposition...
```

This mirrors exactly what the Training runtime already does: *"Learner roles hold no DocPerm on the
content doctypes at all … Visibility is therefore computed in Python — once, in
`_visible_course_names` — and the reads that follow it are deliberately unchecked"*
(`api/training.py:21-25`, quoted at `notes_close_repo.md` §1.2.1). It also inherits that module's
sharpest rule: *"there is no reason to publish the key to a door and then rely on the lock"*
(`api/training.py:275-282`) — the SPA addresses attachments by `Chat Attachment` name, never by
`File` name or by a `/private/files/` URL.

`VERIFY: the exact mechanism Frappe v16 uses to gate /private/files/<f>` — specifically, whether it
calls `frappe.has_permission(attached_to_doctype, doc=attached_to_name)` and therefore fails closed
under zero DocPerm, or whether an unattached private file falls back to owner-only. **Settle:** read
`frappe/core/doctype/file/utils.py` and `frappe/core/doctype/file/file.py` on the bench (no agent
read them this session). **Blocks:** whether the download endpoint is *mandatory* or merely
*preferred*; the endpoint is the right design either way, because it is also where the audit row is
written.

#### F.8.2 Indexes on `Chat Attachment`

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`message`** | index | *"Render this message's attachments"* — run for every message in a backlog page that has `has_attachments = 1`, batched as `WHERE message IN (...)`. |
| **`gchat_attachment_name`** | **UNIQUE**, `unique: 1` on the DocField | *"Has this Google attachment already been ingested?"* — the same structural-dedupe role as `gchat_message_name`, for the inbound ingest job, which is retried by the sweeper and must be idempotent. Empty until ingest, hence the DocField flag and its `""`→`NULL` coercion. |

**Rejected:** an index on `room` (it exists to avoid a join in the permission check on a row already
located by primary key, not to be filtered on) and on `ingest_state` (the ingest sweeper is bounded
and the table is small).

---

### F.9 `Chat Relay Job` — the outbox

**A contradiction with a sibling note, reported rather than resolved silently.**
`notes_close_repo.md` §3.5 recommends *"The `Chat Message` row **is** the outbox … No second payload
table"*, on the strength of the `fountain_move/photos.py::sweep_unmirrored_photos` precedent. The
phase prompt for this section, and `notes_research_gaps.md:1175` (R02 §3.3), both specify a separate
**`Chat Relay Job`** row created in the same transaction. **We adopt the separate table**, for three
reasons the sibling note did not weigh:

1. **Retry bookkeeping on `Chat Message` would churn `modified`,** and D6's watermark is
   `(max(seq), count(*), max(modified))`. Every relay attempt would invalidate every cached digest
   for that room. `notes_close_repo.md` §3.3 note 5 raises exactly this hazard about the sweeper's
   own bookkeeping write; putting the counter on the hot row makes it unavoidable rather than
   avoidable.
2. **Most outbound operations have no `Chat Message` row.** Space creation, space patching,
   membership add/remove and attachment upload all need a retry budget, an error field and an
   ordering position. A boolean sweep over `Chat Message` cannot represent them, so they would need a
   second mechanism anyway.
3. **Ordering.** §G.8's *Create-Before-Edit* rule requires a total order over *operations* within a
   room, not over messages. `unique(room, job_seq)` gives it directly.

`Chat Message.sync_state` remains as the denormalised, read-only mirror the SPA renders — written
with `update_modified=False` (§F.6.2).

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `room` | Link | Chat Room | reqd, set_only_once | also the rate-limit bucket key (§G.1.4) |
| 2 | `job_seq` | Int | | reqd, read_only | per-room FIFO position |
| 3 | `operation` | Select | `Message Create` / `Message Update` / `Message Delete` / `Space Create` / `Space Update` / `Member Add` / `Member Remove` / `Attachment Upload` | reqd, set_only_once | the enum **is** the handler selector |
| 4 | `reference_doctype` | Link | DocType | read_only | `Chat Message` / `Chat Room` / `Chat Room Member` / `Chat Attachment` |
| 5 | `reference_name` | Data | 140 | read_only | |
| 6 | `status` | Select | `Pending` / `In Progress` / `Done` / `Failed` / `Dead` / `Skipped` | reqd, default `Pending` | |
| 7 | `available_at` | Datetime | | reqd | backoff and rate-limit deferral land here |
| 8 | `attempts` | Int | default 0 | | |
| 9 | `payload` | Code | JSON | | operation arguments only — **never a dotted method path** |
| 10 | `request_id` | Data | 64 | read_only | the deterministic `requestId` (§G.2) |
| 11 | `impersonate_user` | Link | User | read_only | which human this call is made as, under DWD |
| 12 | `last_error` | Small Text | | read_only | truncated to 1000 at write |
| 13 | `http_status` | Int | | read_only | |
| 14 | `google_error_status` | Data | 64 | read_only | e.g. `RESOURCE_EXHAUSTED`, `NOT_FOUND` |
| 15 | `completed_at` | Datetime | | read_only | |

**One improvement on the house pattern, stated because it removes a live footgun.** The existing
outbox `Drive Sync Log` stores `payload = {"method": ..., "kwargs": {...}}` and its sweeper must
guard with `if not payload.get("method", "").startswith("erpnext_enhancements."): continue`
(`google_drive/drive_sync.py:696-723`, quoted at `notes_close_repo.md` §3.3) — because a stored
dotted path is an arbitrary-code sink. **`Chat Relay Job` stores no method path.** The `operation`
enum selects the handler from a module-level dict; `payload` carries arguments only. The sink does
not exist, so the guard is not needed.

**Everything else is copied verbatim from the house pattern** (`notes_close_repo.md` §3.4, §7):
`hash` autoname; a never-raising `log_relay_job()` writer modelled on `drive_sync.log_sync`
(*"Never raises — logging must not break the action being logged"*); `error` truncated to 1000
characters; `attempts` as the retry budget; per-row `try/except` in the sweeper; bounded batch.

#### F.9.1 Indexes on `Chat Relay Job`

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`(room, job_seq)`** | **UNIQUE**, via `frappe.db.add_unique` | *"Take the lowest `job_seq` still `Pending` for this room"* — the per-room FIFO that makes §G.8's *Create-Before-Edit* rule a property of the schema rather than a hope. |
| **`(status, available_at)`** | index | *"``WHERE status = 'Pending' AND available_at <= %s ORDER BY available_at LIMIT 200``"* — the sweeper's **only** query, run every five minutes. |

**Rejected:** `(reference_doctype, reference_name)`. The obvious query it would serve — "show this
message's relay history" — is an occasional admin action, and the ordering guarantee comes from
`job_seq`, not from a per-message lookup.

#### F.9.2 Dead-letter, and the gap in the house patterns

`notes_close_repo.md` §3.5 item 7 records that **there is no dead-letter pattern anywhere in this
repo** — every existing retry loop stops at `attempts >= MAX` and the row stays `Failed` with nobody
notified. The closest thing is `esign.tasks.digest_awaiting_signature` (weekly, `hooks.py:744`):
*"one summary of every agreement still out for signature, so a link that quietly went nowhere is
visible without anyone remembering to look."*

Chat needs the equivalent and it is not optional, because decision #2's whole premise is that both
sides agree. A `Dead` job is a message a coworker believes they sent and nobody in Chat received.
**Design: `attempts >= Chat Settings.relay_max_attempts` moves the row to `Dead`, a daily
`digest_dead_relay_jobs()` scheduler job emails/notifies the operator with a count and the ten
oldest, and the SPA renders a per-message "not delivered to Chat" affordance driven off
`Chat Message.sync_state = Failed`.**

---

### F.10 `Chat Inbound Event`

The landing table for everything arriving from Google, before any interpretation. R02 §3.3's rule,
at `notes_research_gaps.md:1176`: *"the webhook endpoint does the absolute minimum — verify
signature/JWT, write a raw `Chat Inbound Event` row, enqueue, return 200 fast."*

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `transport` | Select | `Interaction` / `Workspace Event` | reqd | which of §G.4's two mechanisms delivered it |
| 2 | `event_type` | Data | 140 | | e.g. `google.workspace.chat.message.v1.created`, or `MESSAGE` |
| 3 | `gchat_space_name` | Data | 255 | | for routing to a room |
| 4 | `gchat_resource_name` | Data | 255 | | the changed resource |
| 5 | `pubsub_message_id` | Data | 140 | `unique: 1` | Pub/Sub's own id; empty for the HTTP transport |
| 6 | `received_at` | Datetime | reqd | | `frappe.utils.now_datetime()`, never SQL `NOW()` |
| 7 | `payload` | Code | JSON | | **the verbatim body** |
| 8 | `status` | Select | `Received` / `Processed` / `Ignored` / `Failed` | reqd, default `Received` | |
| 9 | `attempts` | Int | default 0 | | |
| 10 | `last_error` | Small Text | | read_only | |
| 11 | `resulting_message` | Link | Chat Message | read_only | what it produced, if anything |

**Why the verbatim payload is stored and not parsed-then-discarded.** R02 §9.4, at
`notes_research_gaps.md:1188`: *"Record real Google Chat payloads once and store them as fixtures;
the inbound webhook parser should be tested against verbatim captured payloads, not hand-written
approximations."* This table **is** the fixture source. It is also the only way to reprocess after a
parser bug, and the only evidence available when Google changes a payload shape — which they do:
`notes_google_verify.md:1331-1346` lists eleven Chat API changes in the seven months to 2026-08-07,
one of them dated the day the audit ran.

**Batched payloads are the norm, not the exception.** Workspace Events delivers
`.batchCreated` / `.batchUpdated` / `.batchDeleted` / `.batchChanged` variants automatically, and
R04 §8's instruction (`notes_research_gaps.md:684-685`) is *"your handler must accept batched
payloads, not just singletons."* One `Chat Inbound Event` row per **delivery**, fanned out to N
message operations by the worker — not one row per contained resource, so that acknowledging the
Pub/Sub delivery is a single decision.

#### F.10.1 Indexes on `Chat Inbound Event`

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`pubsub_message_id`** | **UNIQUE**, `unique: 1` on the DocField | *"Pub/Sub is at-least-once; has this exact delivery already been recorded?"* — the second delivery becomes a `DuplicateEntryError` the puller treats as "already acked". The DocField flag is what lets the HTTP transport leave it empty on every row (`""`→`NULL`, §F.2) without colliding. |
| **`(status, received_at)`** | index | *"``WHERE status IN ('Received','Failed') AND received_at < %s``"* — the stuck-event sweeper, and the drain query if the worker falls behind. |

**Rejected:** an index on `gchat_resource_name` (dedupe is done on `Chat Message.gchat_message_name`,
which is the authoritative constraint; a second index here would be a redundant probe) and on
`event_type`.

**Retention.** `Processed` and `Ignored` rows are purged after
`Chat Settings.inbound_event_retention_days` (default 30). **`Failed` rows are never purged
automatically** — a purge that deletes the evidence of the events it failed to process is a purge
that hides an outage.

---

### F.11 Retrieval: `Chat Context Chunk`, `Chat Room Digest`, `Chat Thread Digest`

Field lists follow R03 §2's proposal as carried at `notes_research_gaps.md:1085-1098`, with three
changes forced by `DECISIONS.md` D4 and by the measured database.

#### F.11.1 `Chat Context Chunk`

| # | fieldname | fieldtype | options / length | flags | notes |
|---|---|---|---|---|---|
| 1 | `room` | Link | Chat Room | reqd | |
| 2 | `thread_root` | Link | Chat Message | | empty for un-threaded spans |
| 3 | `first_message` | Link | Chat Message | reqd | |
| 4 | `last_message` | Link | Chat Message | reqd | |
| 5 | `first_seq` | Int | | reqd | |
| 6 | `last_seq` | Int | | reqd | |
| 7 | `body` | Long Text | | reqd | rendered `Author (time): text` lines — **this is what gets embedded** |
| 8 | `token_count` | Int | | | computed at seal time |
| 9 | `content_hash` | Data | 64 | | sha256 of `body` |
| 10 | **`embedding`** | **Long Text** | | | **base64 of a float32 array — §F.11.2** |
| 11 | `embedding_model` | Data | 140 | | |
| 12 | `embedding_dim` | Int | | | |
| 13 | `embedding_version` | Int | | | bump to force a re-embed |
| 14 | `participants` | Small Text | | | JSON list of author user ids; powers pre-filtering |
| 15 | `sealed` | Check | default 0 | | a sealed chunk is immutable |

#### F.11.2 The embedding column — a contradiction with `DECISIONS.md` D4, reported

D4 says embeddings are *"stored as a **BLOB** on the chunk DocType"*. **Frappe has no BLOB
fieldtype.** Its type map offers `Data`→`varchar`, `Text`→`text`, `Long Text`/`Code`→`longtext`, and
nothing that produces a `blob`/`longblob` column. Implementing D4 literally therefore requires raw
DDL in a patch to add a column Frappe's schema sync does not manage — which is the same class of
thing `notes_close_google.md` §1.5 warns about for prefix indexes.

**What we do instead, and it satisfies D4's intent exactly:** `embedding` is a `Long Text` holding
**base64 of the raw `numpy.float32` bytes**. At the recommended pinned dimension of 768
(`notes_research_gaps.md:460`) that is 3,072 raw bytes → 4,096 base64 characters. At the research's
upper-bound corpus of ~100k chunks (`notes_research_gaps.md:438`) it is ~400 MB against ~300 MB for
a raw blob — a 33% overhead on a table that would then sit second on this site behind
`tabQuickBooks Raw Payload` at 453 MB (`notes_infra.md:195`). Decoding is
`np.frombuffer(base64.b64decode(v), dtype=np.float32)`, and `numpy 2.5.1` is already importable in
production (`notes_infra.md:990-1005`), so D4's "no new dependency" property holds.

**Recorded as the optimisation with its trigger:** a raw `longblob` added by a patch, removing the
33% and the base64 round-trip, if and only if one of D4's own numeric revisit triggers fires
(p95 retrieval > 400 ms, > 20k candidate chunks after filtering, > 250k chunks total, or a MariaDB
upgrade to 11.8 LTS). The MariaDB half of that is measured and closed: production is
**`10.11.18-MariaDB-0+deb12u1`** with no `VECTOR` type and no `VEC_*` functions
(`notes_infra.md:35-54`).

**Two normalisation rules that belong in the schema's documentation, not in a code comment**, because
getting them wrong corrupts similarity silently: `gemini-embedding-001` outputs at any MRL dimension
other than 3072 **must be manually re-normalised** — *"Skipping this silently corrupts cosine
similarity. It is the #1 mistake with MRL truncation"* (`notes_research_gaps.md:458`) — and the input
limit is 2,048 tokens, which bounds chunk size.

#### F.11.3 `Chat Room Digest` and `Chat Thread Digest`

Same shape, keyed on `room` and on `thread_root` respectively.

| # | fieldname | fieldtype | notes |
|---|---|---|---|
| 1 | `room` (both) / `thread_root` (thread only) | Link | reqd |
| 2 | `digest_version` | Int | monotonic; **embedded in every cache key** |
| 3 | `generation_count` | Int | |
| 4 | **`watermark_seq`** | Int | D6 component 1 |
| 5 | **`watermark_count`** | Int | D6 component 2 — **added here; the research's schema had only two components** |
| 6 | **`watermark_modified`** | Datetime | D6 component 3 — catches edits |
| 7 | `summary_text` | Long Text | |
| 8 | `token_count` | Int | |
| 9 | `covered_from` / `covered_to` | Datetime | |
| 10 | `is_stale` | Check | **retrieval skips stale digests** |
| 11 | `rebuild_failures` | Int | |
| 12 | `model_used` | Data(140) | |
| 13 | `generated_at` | Datetime | |

**`watermark_count` is an addition to the research's proposed schema, and it is the whole point of
D6.** `notes_research_gaps.md:1094-1098` lists only `watermark_seq` and `watermark_modified`.
`DECISIONS.md` D6 requires all three of `(max(seq), count(*), max(modified))`. A two-component
watermark cannot detect a **delete**: deleting message 40 of 100 lowers `count(*)` while leaving
`max(seq)` at 100, and — because our delete is a soft delete that sets `is_deleted` via a normal
save — it does advance `max(modified)`, so in *our* design the two-component form would in fact
catch it. **That coincidence is exactly why the third component must be stored anyway**: it makes the
invariant independent of how delete happens to be implemented. See §F.16.

#### F.11.4 Indexes on the retrieval DocTypes

| DocType | Index | Kind | The one query it serves |
|---|---|---|---|
| `Chat Context Chunk` | **`(room, first_seq)`** | index | *"Candidate chunks for the rooms this user may read, newest first: ``WHERE room IN (...) ORDER BY first_seq DESC LIMIT 8000``"* — the permission-filtered candidate scan that feeds the numpy cosine pass, capped at N=8,000 per `notes_research_gaps.md:467`. |
| `Chat Context Chunk` | **`(room, last_seq)`** | index | *"Which sealed chunk covers seq X"* — the staleness check that marks a chunk `is_stale` when a message inside its span is edited or deleted, run on every message update. |
| `Chat Context Chunk` | `content_hash` | **rejected** | Deduping identical chunk bodies is not a real workload; the chunker is deterministic over a seq span. |
| `Chat Room Digest` | **`room`** | **UNIQUE**, `unique: 1` | *"Fetch the one digest for this room"* — and the uniqueness stops the rebuild job from ever racing two digests into existence for one room. |
| `Chat Thread Digest` | **`thread_root`** | **UNIQUE**, `unique: 1` | Same, per thread. |
| both digests | `is_stale` | **rejected** | The rebuild job scans a table with one row per room — a few hundred rows. |

---

### F.12 `Chat Retrieval Audit` (+ child `Chat Retrieval Audit Room`) — decision #12's audit log

One DocType covers **every privileged read of chat content**: Triton's retrieval, the Phase 6
oversight view, and the attachment download endpoint. Shape from R03 §11.1 as carried at
`notes_research_gaps.md:1100-1104`.

**Parent — `Chat Retrieval Audit`:**

| # | fieldname | fieldtype | options / length | notes |
|---|---|---|---|---|
| 1 | `request_id` | Data | 64 | uuid; correlates with the Triton turn |
| 2 | `accessed_by` | Link | User | reqd |
| 3 | `actor_type` | Select | `Triton` / `Admin` / `User` | reqd |
| 4 | `purpose` | Select | `mention` / `search` / `briefing` / `oversight` / `attachment` | reqd |
| 5 | `query_hash` | Data | 64 | sha256 of the query text |
| 6 | `query_text` | Long Text | | **written only when `Chat Settings.store_retrieval_query_text` is on; default off** |
| 7 | `tiers_used` | Data | 140 | |
| 8 | `chunk_count` | Int | | |
| 9 | `message_count` | Int | | |
| 10 | `token_count` | Int | | |
| 11 | `context_truncated` | Check | | |
| 12 | `rooms` | Table | Chat Retrieval Audit Room | |

**Child — `Chat Retrieval Audit Room`** (`istable: 1`): `room` (Link → Chat Room),
`was_participant` (Check), `messages_read` (Int), `oldest_message_ts` / `newest_message_ts`
(Datetime).

**`was_participant` is the field that makes this log worth keeping.** It records, per room per read,
whether the person the read was performed *for* was actually a member. A non-empty set of
`was_participant = 0` rows is precisely the oversight event decision #12 exists to make visible.

**Three ordering and retention rules, all from R03 §11 (`notes_research_gaps.md:1183-1184`) and all
non-negotiable:**

1. **The audit row is inserted and committed *before* `retrieve()` returns content. If the audit
   write fails, retrieval fails.** Therefore `retrieve()` must be called at the **start** of the
   Triton turn, before any other writes — otherwise the audit commit would commit somebody else's
   half-finished transaction.
2. **`query_hash` by default; raw query text behind a flag that ships off.** The query a manager
   types is itself sensitive.
3. **The retention purge must never delete audit rows.** This is the one chat DocType with no
   retention rule, and §F.17's table records that as a deliberate omission rather than an oversight.

**Indexes:**

| Index | Kind | The one query it serves |
|---|---|---|
| `name` | PRIMARY | — |
| **`(accessed_by, creation)`** | index | *"Everything this person's sessions read, most recent first"* — the oversight review query, and the one an employee would ask about themselves. |
| **`(actor_type, creation)`** | index | *"Every `Admin`-type read in the period"* — the Phase 6 governance report. |
| child `parent` | Frappe default | *"Which rooms did this read touch"* |

**Permissions: this DocType DOES carry DocPerm** — `System Manager` with `read` and `report` only, no
`write`, no `delete`, no `create`. It is the exception to §F.18 and the exception is deliberate: an
audit log nobody can look at is not an audit log. Because `System Manager` sees all rows by design,
no `permission_query_conditions` pair is required — **but the moment a narrower role (a "Chat
Auditor") is granted `read` here, §F.18's Layer 3 obligation fires and both hooks must ship in the
same commit.**

---

### F.13 `Chat Settings` (Single)

Credential pointers, feature flags, quotas and kill switches. Modelled field-for-field on
`Triton Assistant Settings`, which `notes_ee_audit.md` §14.7 recommends copying exactly: *"`enabled`
master switch + `restrict_to_whitelist` + an `allowed_users` child table, enforced **server-side on
every call** … and folded into the client config so non-whitelisted users never see the entry
point."* It ships dormant.

**Rollout gate**

| fieldname | fieldtype | default | notes |
|---|---|---|---|
| `enabled` | Check | **0** | the master switch; ships dormant |
| `restrict_to_whitelist` | Check | 1 | |
| `allowed_users` | Table → `Chat Allowed User` | — | child: `user` (Link → User) |

**Credential pointers — and the striking thing is what is absent**

| fieldname | fieldtype | notes |
|---|---|---|
| `google_project_id` | Data(140) | |
| `delegation_service_account` | Data(255) | the SA **email**; the DWD identity |
| `chat_app_service_account` | Data(255) | the Chat app's own SA email |
| `workspace_customer_id` | Data(64) | |
| `interaction_endpoint_url` | Data(255) | **the JWT `aud`; must match byte-for-byte** (§G.6) |
| `pubsub_events_subscription` | Data(255) | `projects/{p}/subscriptions/{s}` |
| `pubsub_interaction_subscription` | Data(255) | reserved; empty under the HTTP transport |
| `relay_identity_mode` | Select `Domain-Wide Delegation` / `App Auth` | the D3 trilemma escape lever |

**There is no `Password` field on `Chat Settings`, and that is the point.** Under `DECISIONS.md` D3's
keyless design there is **no private key on disk, no rotation, and no secret in `site_config.json`**;
the entire trust relationship is one IAM binding (`roles/iam.serviceAccountTokenCreator`) on the
delegation service account's allow policy, and revocation is a single
`remove-iam-policy-binding` — atomic and audited (`notes_close_google.md` §2.1, §2.7). Contrast the
existing `Project Folder Google Drive Settings.service_account_json`, a `Password` field measured at
length 2,358 on the live site (`notes_infra.md:494`): that is a long-lived RSA private key living in
the database of a host on which an attacker has previously executed arbitrary Server Scripts
(`notes_close_google.md` §2.7). Google's own documentation says to avoid exactly that shape:
*"Although examples … commonly suggest the use of service account keys, using service account keys is
not necessary to perform domain-wide delegation … avoid service account keys and use the `signJwt`
API instead"* (`notes_close_google.md` §2.3).

**Feature flags**

| fieldname | fieldtype | default | what it gates |
|---|---|---|---|
| `relay_outbound_enabled` | Check | 0 | ERPNext → Google Chat |
| `relay_inbound_enabled` | Check | 0 | Google Chat → ERPNext |
| `mirror_attachments` | Check | 0 | §F.8; costs 2 write tokens per message |
| `triton_enabled` | Check | 0 | the @triton path |
| `import_mode_enabled` | Check | **0** | §G.11; migration only, never steady state |
| `store_retrieval_query_text` | Check | **0** | §F.12 rule 2 |
| `threading_enabled` | Check | 0 | set to 1 only after §G.9's five-minute settlement passes |

**Quotas and tuning — every number here is a measured Google limit or a house constant, not a guess**

| fieldname | fieldtype | default | source |
|---|---|---|---|
| `space_write_tokens_per_second` | **Float** | 1 | per-space write cap (`notes_google_verify.md:997`) |
| `upload_token_cost` | **Float** | **2** | `media.upload` shares the bucket (`notes_google_verify.md:1001-1003`; D8) |
| `project_space_writes_per_minute` | Int | 60 | `notes_google_verify.md:973` |
| `project_membership_writes_per_minute` | Int | 300 | `notes_google_verify.md:971` |
| `project_message_writes_per_minute` | Int | 3000 | `notes_google_verify.md:968` |
| `message_byte_limit` | Int | **32000** | whole message, **bytes** (`notes_google_verify.md:1014-1024`) |
| `attachment_byte_limit` | Int | 209715200 | 200 MB (`notes_google_verify.md:748`) |
| `relay_max_attempts` | Int | 5 | matches `offsite_backup/drive.py:46-50` `MAX_ATTEMPTS = 5` |
| `relay_initial_backoff_seconds` | Int | 2 | matches the same module's `INITIAL_BACKOFF_SECONDS = 2` |
| `sweeper_batch_size` | Int | 200 | matches `drive_sync.retry_failed_syncs`' `limit_page_length=200` |
| `subscription_ttl_seconds` | Int | **604800** | 7 days, the `includeResource: false` ceiling (`notes_google_verify.md:253-271`) |
| `subscription_renew_before_seconds` | Int | 86400 | renew a full day early; the 12-hour lifecycle reminder is the backstop |
| `presence_heartbeat_seconds` | Int | 30 | house constant, `live_form_sync.js:71-72` |
| `presence_ttl_seconds` | Int | 75 | house constant, same |

> **The two token-bucket fields are `Float`, not `Int`, and the choice is deliberate.**
> `space_write_tokens_per_second` and `upload_token_cost` are the only genuinely ambiguous
> fieldtypes on this Single: both default to a whole number today, but they are the *refill rate*
> and the *charge* of one bucket, so they must share a type or the arithmetic silently truncates —
> and a sub-1/s refill (throttling a noisy space to 0.5 writes/second without a schema migration) is
> a plausible enough future that `Float` is the cheaper side of the guess. Phase 1 must not
> re-derive this.

**Retention**

| fieldname | fieldtype | default | notes |
|---|---|---|---|
| `message_retention_days` | Int | 0 | 0 = keep forever |
| `hard_delete_after_days` | Int | 0 | §F.6.5's promotion of soft deletes; 0 = never |
| `inbound_event_retention_days` | Int | 30 | `Failed` rows exempt (§F.10.1) |
| `relay_job_retention_days` | Int | 30 | `Dead` rows exempt |

**Kill switches — separate from `enabled`, deliberately**

| fieldname | fieldtype | default | notes |
|---|---|---|---|
| `pause_outbound` | Check | 0 | stop relaying without tearing the feature down |
| `pause_inbound` | Check | 0 | stop ingesting; events accumulate in Pub/Sub and are recoverable (§G.5) |
| `pause_triton` | Check | 0 | mute @triton while leaving human mirroring alive |
| `pause_retrieval` | Check | 0 | close the retrieval gate without disabling chat |

An operator in an incident needs to stop **one direction**. `enabled = 0` is a rollout control, not
an incident control, and conflating them is how a small problem becomes an outage. Every switch is
read via `frappe.get_cached_doc("Chat Settings")` at the top of every job and every endpoint — the
same shape as `triton_chat.py:112-122` — and the deploy's Redis FLUSHDB re-reads it for free.

**Permissions:** `System Manager` `read`/`write`. This is the second exception to §F.18, and it is
required: a Single with no DocPerm cannot be opened in the desk, and a settings page nobody can edit
is not a settings page.

---

### F.14 Presence and typing live in Redis, not in DocTypes

#### F.14.1 The justification, with the site's own numbers

R02 §2.6's counterfactual, at `notes_research_gaps.md:422`: presence as a DocType is
*"50 users × every 30s = **144k writes/day** for data that is worthless 60 seconds later."* At the
**measured** roster of 20 enabled System Users (`notes_infra.md:141`) it is ~58k writes/day. Either
number should be read against what this ERP actually writes today: **the entire site produces ~730
`tabVersion` rows/day, ~74 `tabComment` rows/day and ~61 `tabNotification Log` rows/day**
(`notes_infra.md:227-236`). Presence-as-a-DocType would therefore be roughly **80×** the total
existing write volume of the business, permanently, for data with a 60-second half-life. Typing
indicators would be worse: at one event per keystroke burst they are an order of magnitude above
presence.

There is also no Frappe primitive to reuse — `notes_gap_report.md` §0-4 and `notes_close_frappe.md`
§5 both enumerated the entire public surface of `frappe/realtime.py` and confirmed **no presence
primitive exists**.

#### F.14.2 Key shapes and TTLs

> **Seam note (assembly).** The **key shapes below govern** — in particular `chat:focus:{user}` as a Redis
> **hash with one field per session**, because per-field TTLs (`HEXPIRE`) need Redis 7.4 and
> production is measured at `7.0.15`, and a key-per-session layout would need a `SCAN`.
> **The constants below do not govern.** [§H.3.1](#h31-the-constants--and-why-chat-does-not-reuse-30-s--75-s)
> considered the house 30 s/75 s pair explicitly and chose `CHAT_PRESENCE_HEARTBEAT = 20 s`,
> `CHAT_PRESENCE_TTL = 55 s`, `BLUR_GRACE = 120 s`; §H owns the presence→suppression contract
> that decision #3 depends on, so read 75 s below as 55 s and 30 s as 20 s. Seam **S3**.

Namespaced by hand following the house convention (`training:attempt:` in `training/progress.py:59`,
`triton_user_token::{user}` in `triton_chat.py:124`), because **the cache Redis and the socket.io
adapter share DB 0 of the same instance** at `redis://127.0.0.1:13000` (`notes_infra.md:583-586`,
`:619-623`).

| Key | Type | Value | TTL | Written by |
|---|---|---|---|---|
| `chat:presence:{user}` | string (JSON) | `{"state": "online\|idle\|away", "at": <epoch>}` | **75 s** | the heartbeat endpoint |
| `chat:focus:{user}` | **hash**, field = session id | `{"room": ..., "focused": bool, "at": <epoch>}` | **75 s on the key** | the focus endpoint |
| `chat:typing:{room}` | hash, field = user | `<epoch>` | **15 s on the key** | the typing endpoint |
| `chat:ratelimit:space:{room}` | integer | token count | 1 s window | the relay (§G.1.4) |
| `chat:ratelimit:project:{bucket}` | integer | token count | 60 s window | the relay |

All of these live on the **cache** Redis (13000), never the queue Redis (11000). Both are flushed by
the deploy, but the queue instance is the one whose loss also destroys jobs, and there is no reason
to put presence in its way.

**The 30 s heartbeat / 75 s TTL pair is the existing house constant**, not a new invention:
`public/js/collab/live_form_sync.js:71-72` — `FOCUS_HEARTBEAT_MS = 30 * 1000`,
`FOCUS_TTL_MS = 75 * 1000` (`notes_infra.md:724-727`). Two heartbeats may be missed before a user
drops to offline.

**But inherit the caveat with the constant.** `notes_infra.md:726-727` flags that 30 s *"is exactly
at the GCLB idle boundary"*: the load balancer's backend timeout is never set in Terraform
(`infra/configs/load_balancer.yaml:33-39`), GCP's default is 30 s, idle WebSockets are closed at it,
and the only reason realtime works in production today is that socket.io's default 25 s
`pingInterval` beats the 30 s idle cut **by five seconds** (`notes_infra.md:792-901`). **Named rule:
the presence heartbeat must never be the thing keeping the socket alive.** It rides the existing
socket; socket.io's ping is the keepalive. If the heartbeat were made the keepalive, raising it to
60 s for efficiency would silently kill every idle connection.

#### F.14.3 Why `chat:focus` is a hash and not one key per session

The suppression rule decision #3 needs is *"**no** client of this user has that room focused"*
(R02 §2.6, `notes_research_gaps.md:1156-1160`, which flags this as *"the multi-tab case … the one
most likely to be got wrong"*). A per-user string key cannot express it; a key per `(user, session)`
can, but then enumerating a user's sessions requires a `SCAN`, which is the wrong tool.

A Redis hash keyed on the user with one field per session is the right shape — **except that
per-field TTLs (`HEXPIRE`) require Redis 7.4, and production is measured at `redis_version 7.0.15`**
(`notes_infra.md:565`). So each field's value carries its own `at` epoch, the **key** carries the
75 s TTL, and readers ignore fields older than the TTL. One key per user, one field per tab, no SCAN,
no version dependency.

#### F.14.4 Two Redis behaviours that must be designed around, both measured

- **Every production deploy `FLUSHDB`s both Redis instances**, then restarts every process:
  `infra/cloudbuild-deploy.yaml` ends `redis-cli -p 13000 FLUSHDB && redis-cli -p 11000 FLUSHDB' && sudo systemctl restart frappe-bench`
  (`notes_infra.md:592-597`; `notes_close_repo.md` §2.3, §3.0). Presence, focus, typing and the rate
  limiter are **gone on every deploy, TTL notwithstanding**. The design copies
  `training/progress.py:44` — *"a deploy FLUSHDBs Redis, so the worst case is losing one flush
  interval of watching — about a minute — not an attempt"* — so the degradation is **"everyone shows
  offline for one heartbeat"**, never corruption. The rate limiter's version of the same rule is
  harsher and is stated in §G.1.4: **it must fail OPEN.**
- **`maxmemory-policy` is `allkeys-lru`** (`notes_infra.md:565`), so keys can be evicted **before**
  their TTL under memory pressure, and `allkeys` means TTL'd keys are candidates alongside untimed
  ones. Current headroom is enormous (4.75 MB used of 1.57 GB, `evicted_keys: 0`), so this is a
  correctness note rather than a live risk — **never treat a Redis key here as guaranteed-until-TTL.**

#### F.14.5 The realtime security doctrine these endpoints inherit

Presence, focus and typing are all written through whitelisted endpoints, never emitted
client-to-client. The house statement is `api/collab.py:1-41`: *"Clients never emit realtime events
to each other directly — this endpoint is the security authority for every broadcast"*
(`notes_infra.md:717-723`). Chat copies it verbatim. §F.18.3 argues for going further and putting message *content* on
per-user rooms too — that argument is preserved below as the **recorded fallback**, but the
shipped design keeps content on the permission-checked doc room. See the seam note at §F.18.3
and [§H.4.1](#h41-the-channel-split--confirmed-not-provisional). Seam **S1**.

---

### F.15 READ RECEIPTS — the reconciliation, stated so no later phase has to guess

Decision #9 asks for per-message read receipts. Per-message rows are the **dominant scaling risk in
this entire design**. This section resolves it and does not leave it ambiguous.

#### F.15.1 The arithmetic, on the site's own numbers

R02 §1.4, at `notes_research_gaps.md:437`: *"2,000 messages/day × average room size 10 = ~20,000
receipt writes/day, versus 2,000 message writes. The receipts table becomes **10×** the message
table."* `notes_infra.md:238-244` reaches the identical conclusion independently from the live
measurements and states the fix as a rule: *"**Sizing rule: keep per-message fan-out out of the DB.**
Store one message row; keep read state as a per-(user, channel) high-water mark … That turns 12,000
rows/day into ~20 updated rows/day."*

Two things make that multiplier worse here rather than better. First, at a **20-person company**
(`notes_infra.md:141`) a ten-person room is *most of the company*, so "average room size 10" is not a
conservative assumption, it is the expected case. Second, 20,000 receipt rows/day would be roughly
**27× the entire site's current daily write volume across all tables** (~730 rows/day,
`notes_infra.md:229`). This is not a table that gets big later; it is a table that immediately
dominates the database of a working ERP.

#### F.15.2 The design

**Storage: a high-water mark per `(user, room)` — `Chat Room Member.last_read_seq` (Int) and
`last_read_at` (Datetime). There is no `Chat Read Receipt` DocType.**

**Derivation.** "User U has read message M" is a pure function of two indexed reads:

```
read(U, M)  ==  exists member(U, M.room)  and  M.seq <= member(U, M.room).last_read_seq
```

Per-message receipts are therefore not *stored*, they are *derived* — and the derivation is exact,
not approximate, because reading is monotone in `seq`: the set of messages a user has read in a room
is always a prefix `{m : m.seq <= k}`, and a prefix is completely described by its endpoint `k`.

**Advancing the mark.** One whitelisted endpoint, `mark_read(room, seq)`, which
(a) verifies membership on the `(room, user)` index, (b) advances monotonically —
`new = max(old, seq)`, never backwards, so an out-of-order client cannot un-read anything —
(c) writes with `frappe.db.set_value(..., update_modified=False)` rather than a full `save()`, so a
scroll does not run `validate` on every frame, and (d) fires the four-part cross-surface sync of
[§H.4.5](#h45-the-four-part-read-sync-concretely).

#### F.15.3 What the SPA renders — DM

The peer's read state is a single marker that **moves down the transcript**: it is drawn beneath the
newest message whose `seq <= peer.last_read_seq`. Concretely, one query per open DM:
`SELECT last_read_seq FROM tabChat Room Member WHERE room = %s AND user != %s`.

This is **exactly the information a per-message receipt table would provide**. In a two-member room
the high-water mark and the receipt set are information-theoretically equivalent (§F.15.2), so the
per-message table would store 10× the rows to represent zero additional information.

#### F.15.4 What the SPA renders — group room

**"Read by N" on the newest message only**, expandable on click to the member list.
`N = COUNT(*) FROM tabChat Room Member WHERE room = %s AND is_active = 1 AND last_read_seq >= %s`, one
query against the `(user, is_active)` index's table, and the expanded list is the same query
returning `user` instead of `COUNT(*)`.

**Deliberately NOT rendered: a per-message tick on every message in a group room.** It would require
either one query per rendered message or the materialised table this section exists to avoid, and it
is information almost nobody uses. If a specific older message's read count is wanted, the same
single query answers it for that message's `seq` on demand.

#### F.15.5 What is lost, stated rather than hidden

- A high-water mark cannot express **"read message 40 but not 39"**. Nothing in this product needs
  it, and no chat client anyone uses exposes it.
- `last_read_at` is the time of the **last advance**, not a per-message read timestamp. A forensic
  *"when exactly did Bob see this specific message"* question cannot be answered better than "some
  time between the advance that passed its seq and the one before".
- There is **no "delivered" state**, only "read". Adding delivery would double the columns and
  require the client to distinguish socket-receipt from render, which it cannot do reliably across a
  backgrounded tab.

#### F.15.6 HUMAN QUESTION (CQ) — are per-message rows actually needed for DMs?

**Our recommendation: no.**

The reasoning is not "it is expensive" — in a DM it is *also* redundant. Receipts are monotone in
`seq`, so in a 2-member room `last_read_seq = k` and the set `{m : m.seq <= k}` carry identical
information. A per-message receipt table for DMs would multiply the row count by the message count to
represent a single integer.

The only thing per-message rows would genuinely add is a **per-message timestamp**. If the human
wants that, the cheap shape is **not** a receipt-per-message table but a `Chat Read Advance` log —
**one row per advance**, i.e. one row per reading session per room, bounded by human attention rather
than by message volume. At a plausible ten reading sessions per person per day across 20 people that
is ~200 rows/day, versus ~20,000. **Recorded as the escape hatch with its trigger: build
`Chat Read Advance` only if a named product requirement asks for per-message read *times*.**

---

### F.16 The monotonic per-room sequence — a named invariant, and its test

#### F.16.1 INVARIANT `CHAT-SEQ-1` — sequence assignment

> For every `Chat Message`, `seq` is a positive `Int` assigned **inside the inserting transaction**,
> and the pair `(room, seq)` is unique. `seq` is never derived from a timestamp, never assigned by
> the client, and never reused — including after a delete, because deletes are soft (§F.6.5).

Assignment, following R03 §2.4's recommended option (a) as carried at
`notes_research_gaps.md:1106-1110`:

```sql
SELECT COALESCE(MAX(seq), 0) + 1 FROM `tabChat Message` WHERE room = %s FOR UPDATE
```

The locking read takes an index range/gap lock on `(room, seq)` for that room, which is what
serializes two concurrent inserts into the same room. The `unique(room, seq)` constraint (§F.6.6) is
the backstop, not the mechanism: if the allocator is ever bypassed, the second insert fails loudly
rather than producing two messages at position 40.

**Two honest caveats, and one recorded alternative.** A gap lock on an index range can deadlock
against a concurrent insert into an adjacent gap; the insert path therefore **retries the whole
transaction once on a deadlock (MariaDB 1213)** before surfacing an error. And the lock is held for
the duration of the insert, which serialises a room's writes — that is not a cost, it is the
requirement. **The recorded alternative** is a `next_seq` counter column on `Chat Room`, taken with
`SELECT ... FOR UPDATE` on a single row: it locks one row instead of an index range and cannot
deadlock on gaps. **Rejected for V1** because it introduces a second source of truth that can drift
below `MAX(seq)` if any code path (a backfill, an import) ever writes `seq` directly, and the
`MAX(...)` form is self-healing. `Chat Room.seq_high_water` exists as an **advisory mirror for
reporting only** and is explicitly not the allocator; a nightly check that
`seq_high_water == MAX(seq)` is a cheap drift alarm.

*(This is a deviation in emphasis, not in substance, from anything binding: `DECISIONS.md` D6
mandates the property, not the mechanism.)*

#### F.16.2 INVARIANT `CHAT-WATERMARK-1` — the three-value watermark

> Every cache key, every digest staleness check and every chunk validity check is derived from
> **`(max(seq), count(*), max(modified))`** over the room's messages. **Never from `seq` alone.**

This is `DECISIONS.md` D6 verbatim, and R03 calls it *"the single most common bug in this design;
write the test first"* (`notes_research_gaps.md:1108-1110`). The mechanism of the bug: **edits and
deletes do not advance `seq`.** A watermark tracking only `seq` will happily serve cached context
containing a message the user just deleted — into a model's context window, which is the worst place
for it to surface.

Three supporting rules the invariant depends on, each of which is a way the watermark can be
silently defeated:

1. **Relay bookkeeping must not advance `modified`** (§F.6.2) — otherwise the third component moves
   on every retry and the watermark is pure noise, invalidating every digest continuously.
2. **Conversely, anything that genuinely changes message content or visibility MUST advance
   `modified`** — soft delete and edit both go through a normal save for exactly this reason. The
   split is: *content and visibility changes use `save()`; sync bookkeeping uses
   `set_value(..., update_modified=False)`.*
3. **`max(modified)` is compared using `frappe.utils.now_datetime()` on both sides, never SQL
   `NOW()`.** Measured this session at `notes_register_reconciled.md` C7: the production database
   clock is UTC (`NOW() == UTC_TIMESTAMP()`) while Frappe writes `creation`/`modified` in site-local
   time (America/Denver), so the newest `tabScheduled Job Log.creation` sat exactly **six hours**
   behind `NOW()`. A naive `TIMESTAMPDIFF(MINUTE, MAX(modified), NOW())` reports 361 minutes for a
   row written one minute ago. The reconciliation agent nearly recorded a stalled scheduler because
   of it. **This applies to every staleness check in the design** — digest staleness, outbox age, the
   subscription renewal window — not only to the watermark.

#### F.16.3 The test, written as the acceptance criterion Phase 1 must ship

R03's own `T-6` (`notes_research_gaps.md:1198-1199`) is the shape; this is it stated fully, and it is
**bench-free** if the watermark function is a pure helper over four integers — which is the reason to
write it as a pure helper.

```
GIVEN a room with messages seq 1..100, a sealed Chat Context Chunk covering seq 30..45,
      and a Chat Room Digest whose watermark is (100, 100, T0)
WHEN  message seq 40 is EDITED
THEN  max(seq) is still 100
 AND  count(*) is still 100
 AND  max(modified) has advanced past T0
 AND  the derived watermark differs from the stored one
 AND  the covering chunk is marked is_stale = 1
 AND  the digest is marked is_stale = 1 and is omitted from retrieval
 AND  the retrieval cache key has changed

WHEN  message seq 40 is then DELETED (soft)
THEN  max(seq) is still 100
 AND  the message appears in NO retrieval tier
 AND  the watermark differs again
 AND  a seq-only watermark would have been unchanged by BOTH operations
      -- this last assertion is the point of the test and must be written explicitly
```

The final assertion is what makes this a regression test rather than a description. It fails the day
somebody "simplifies" the watermark to `max(seq)`.

---

### F.17 What is deliberately NOT stored — the amplification budget

Three mechanisms turn a chat feature into a database problem, and all three are opt-out rather than
opt-in in Frappe. Each is set explicitly per DocType, with the measured number that justifies it.

#### F.17.1 The setting table

| DocType | `track_changes` | `in_global_search` on any field | `index_web_pages_for_search` | `track_seen` / `track_views` | Registered for retention |
|---|---|---|---|---|---|
| `Chat Room` | **0** | **no** | **0** | 0 / 0 | no (low volume) |
| `Chat Room Member` | **0** | **no** | **0** | 0 / 0 | no |
| **`Chat Message`** | **0** | **no** | **0** | **0 / 0** | yes, `message_retention_days` (default 0 = keep) |
| `Chat Mention` | n/a (child) | **no** | n/a | n/a | with parent |
| `Chat Attachment` | **0** | **no** | **0** | 0 / 0 | with parent |
| `Chat Relay Job` | **0** | **no** | **0** | 0 / 0 | **yes, 30 days** |
| `Chat Inbound Event` | **0** | **no** | **0** | 0 / 0 | **yes, 30 days** (`Failed` exempt) |
| `Chat Context Chunk` | **0** | **no** | **0** | 0 / 0 | with the messages it covers |
| `Chat Room Digest` / `Chat Thread Digest` | **0** | **no** | **0** | 0 / 0 | no (one row per room) |
| **`Chat Retrieval Audit`** | **0** | **no** | **0** | 0 / 0 | **NEVER — deliberate** |
| `Chat Settings` | 0 | no | 0 | 0 / 0 | n/a (Single) |

#### F.17.2 `track_changes = 0` — the Version amplification

A `Version` row is written per tracked change, carrying the before/after of every changed field. On
this site **`tabVersion` is already the busiest table by write rate — 21,843 rows in 30 days,
728.1 rows/day — and the third largest at 177.5 MB / 50,659 rows** (`notes_infra.md:175`, `:229`).
Chat edits would add a Version row containing **two full copies of the message body** each time.

At the research's upper-bound 500k messages/year (`notes_research_gaps.md:432`) and even a modest 5%
edit rate, that is 25k Version rows/year of duplicated bodies — and it would put message text into a
table that has **no retention rule** (`tabLogs To Clear` contains no `Version` row —
`notes_infra.md:246-257`) and that the MCP `run_database_query` surface can read (§F.18). Setting
`track_changes = 0` therefore serves both scale and I5.

**It must be set explicitly.** Frappe's default for `track_changes` is recorded as unverified
(R02-V04, deferred to Phase 1 by `notes_register_reconciled.md`), so relying on a default is
relying on something nobody has checked. The DocType JSON says `"track_changes": 0`.

**And the audit trail does not suffer**, because edit history that matters is captured on the row
itself (`is_edited`, `edited_at`) and the pre-edit body — if a later phase wants it — belongs in a
purpose-built, retention-governed place, not in a general-purpose table with none.

#### F.17.3 No field is `in_global_search`, and `index_web_pages_for_search = 0`

`__global_search` on this site is **761,057 rows / 151.5 MB — already the fourth-largest table**
(`notes_infra.md:198`). Adding message bodies would make it the largest table on the site inside a
year, and it would place chat text into a search surface whose permission behaviour under a
zero-DocPerm DocType nobody has verified (`notes_close_repo.md` §6 item 3 carries exactly this
`VERIFY`). Chat search is served by the retrieval package (§F.11), which is membership-filtered by
construction.

Note that **Raven sets `index_web_pages_for_search: 1` on `Raven Channel`**
(`notes_raven.md:330`). Do not copy that.

#### F.17.4 Notification Log — the growth curve, measured, and the design that avoids it

This is the amplification that would actually hurt, and `notes_close_frappe.md` §4.5 computed it from
the live numbers rather than guessing.

**Where the table stands today** (`notes_infra.md:178`, `:203`, `:231`, `:246-257`): **7,165 rows /
33.2 MB accumulated over 13 months** (oldest row 2025-07-09), currently growing at **60.7 rows/day**,
with **no `Logs To Clear` row** — so it grows forever. Indexes are all single-column
(`PRIMARY`, `app`, `creation`, `document_name`, `for_user`); there is **no composite
`(for_user, read, creation)`**, so the bell's own "unread for me, newest first" query is a filtered
sort on `for_user` alone (`notes_infra.md:302-305`).

**The curve at decision #3's two notifications per message** (`notes_close_frappe.md` §4.5, using its
own 600 messages/day scenario):

| Scenario | Notification Log rows/day | vs today's 60.7 | Rows in 13 months |
|---|---:|---:|---:|
| Today | 61 | 1× | 7,165 |
| Chat, mention-only (~5% of messages, 1 recipient) | +30 | 1.5× | ~11k |
| Chat, two per message to **one** recipient | +1,200 | **20×** | ~470k |
| Chat, two per message **fanned out to a 10-person room** | +12,000 | **198×** | ~4.7M |

At the measured ~4.6 KB/row that last row is **~2 GB/year on a table with no retention and no
composite index.**

**The design that avoids it, and it is a schema decision, not a Phase 4 decision:**

1. **Notifications are per-conversation-state, not per-message.** One `Notification Log` row per
   `(user, room)` that is refreshed rather than multiplied. The primitive that makes this expressible
   is **new in v16**: `enqueue_create_notification(users, doc, dedupe_on=[...])`, whose worker skips
   creation when a row already matches the named fields
   (`notes_close_frappe.md` §3.7, quoting `frappe/desk/doctype/notification_log/notification_log.py`).
   **Use `dedupe_on=["document_type", "document_name"]`** with
   `document_type = "Chat Room"`, `document_name = <room>`. That turns 12,000 rows/day into tens.
2. **The unread *counter* is carried on the realtime channel, not in the database** — it costs zero
   rows (§F.18.3).
3. **`link` is set to the SPA deep link** `/chat/room/<room>?message=<msg>`, and `document_type` /
   `document_name` are *also* set so the row stays queryable. This is safe and correct: Frappe v16's
   `get_item_link` **short-circuits on `link`** before consulting `document_type`
   (`notes_infra.md:368-383`, read from `frappe/public/js/frappe/ui/notifications/notifications.js`),
   and `link` is set on **0 of 9,612 existing rows** (`notes_infra.md:340`) so nothing in core
   competes for it. It also resolves a problem `notes_close_repo.md` §1.2.3 raised — that a
   Notification pointing at a zero-DocPerm doctype would render a link landing on a 403 — because the
   `link` we set points at the SPA, not at `/app/chat-message/...`.
4. **Neither notification is email**, unconditionally, via `notification_skip_email_types` in
   `erpnext_enhancements/hooks.py`. This is a first-class v16 extension point and it **beats an
   explicit per-user opt-in**: `is_email_notifications_enabled_for_type` returns `False` on
   `notification_type in get_skip_email_types()` before it ever reads the user's settings
   (`notes_close_frappe.md` §3.2-§3.3). Registering it also makes the "enable for all users" button
   throw, which is the assertion we want (`notes_close_frappe.md` §3.5).
5. **`Notification Log.type` is a `Link` to a real `Notification Type` DocType in v16, not a Select**
   (`notes_close_frappe.md` §3.4). Writing `"type": "Chat Mention"` without a `Notification Type`
   record of that name is a **link-validation failure on insert**. The chat notification type names
   are therefore app configuration and must be *installed* — the recommended mechanism is an
   idempotent `after_migrate` installer mirroring the framework's own
   `install_notification_types()`, not a fixture, because fixture deletion is a two-step process
   (`fixtures/README.md`).
6. **Recipient resolution goes through `User.email`, not `User.name`.** `_get_user_ids` filters
   `{"enabled": 1, "email": ("in", user_emails)}` and returns `name`; on a site where a login id
   differs from the email field, passing `name` yields **zero recipients and the notification
   vanishes with no error** (`notes_close_frappe.md` §3.8 item 1). `Chat Room Member.user` is a
   `User` link, so Phase 4 must resolve member → `User.email` explicitly.

**And one honest limit on decision #3's "exactly two".** A user whose
`Notification Settings.enabled = 0` receives **no `Notification Log` rows at all** — not the mention,
not the counter (`notes_close_frappe.md` §3.8 item 3). "Exactly two" reads as a guarantee and is not
one; it is "at most two, and zero for anyone who has switched notifications off". The ADR states it
rather than letting a user discover it.

#### F.17.5 Retention registration, and the trap that silently disables it

The mechanism is one hook (`notes_close_frappe.md` §4.3):

```python
# erpnext_enhancements/hooks.py
default_log_clearing_doctypes = {
    "Chat Relay Job": 30,
    "Chat Inbound Event": 30,
}
```

The next `daily_maintenance` run appends the `Logs To Clear` rows automatically. No patch, no
fixture, no manual UI step. Three mechanical details that decide whether it works:

- **`remove_unsupported_doctypes()` runs first in `run_log_clean_up` and deletes any row whose
  controller does not implement `clear_old_logs(days)`.** So **every chat DocType registered for
  retention must implement a `clear_old_logs(days)` staticmethod**, or the row is silently removed on
  the next daily run and retention silently stops. **This is a Phase 1 acceptance test**, not a code
  comment.
- **`add_default_logtypes` never updates an existing row.** Changing the hook value later has no
  effect on a site that already has the row. Choose the number once, deliberately.
- **`retentions[-1]`, not `retentions`** — `frappe.get_hooks` merges dict-valued hooks into a list per
  key and takes the last installed app's value. Ours is the only declarer, so it is unambiguous
  today; it is install-order-dependent if a future app ever declares the same doctype.

**A standalone repo improvement, offered rather than smuggled in.** `{"Notification Log": 30}` in the
same hook would fix a **pre-existing, unrelated 13-month leak** (33.2 MB, no retention row) and would
additionally unlock `clear_log_table("Notification Log")`, which today raises `ValidationError`
because the doctype is not in the hook dict — and which is the only feasible way to run the first
catch-up delete without a MariaDB big-delete inside `daily_maintenance`
(`notes_close_frappe.md` §4.4). **This is not part of the chat feature. It is flagged to the human as
its own change** (`notes_close_frappe.md` §6 item 7).

#### F.17.6 Also deliberately absent

- **No `Comment` rows on chat DocTypes** — the desk comment machinery is unreachable anyway under
  zero DocPerm, and this repo already ships a threaded-comments product on documents
  (`notes_infra.md:683-715`) whose lane chat must stay out of (§F.17.7).
- **No `_assign`, no `_liked_by`, no `_seen`.** `track_seen` writes to `_seen` on every view of the
  hot table.
- **No per-recipient delivery rows to Google.** `Chat Relay Job` is per operation, not per recipient.
- **Chat DocTypes must be excluded from any `doc_events["*"]` handler this app registers.** Raven's
  own global wildcard is the cautionary example — five events on every doctype site-wide, paying a
  Redis `hget` on every document write anywhere in ERPNext (`notes_raven.md:1023-1047`), and
  interacting badly with the `CLAUDE.md` gotcha that `doc_events` fire during ERPNext's own test
  bootstrap.
  `VERIFY: which wildcard doc_events handlers erpnext_enhancements registers today, and whether any
  of them would fire on chat DocTypes` — **settle:** read the `doc_events` block in
  `erpnext_enhancements/hooks.py` for a `"*"` key. **Blocks:** whether an explicit ignore list is
  needed in Phase 1.

#### F.17.7 The feature collision nobody should discover in Phase 3

`notes_infra.md:683-715` found a **feature** collision rather than a name collision: this repo
already ships a Slack-style threaded-comments UI — `public/js/comments.js`, backed by the Custom
Field `Comment.custom_parent_comment`, auto-mounted on **23 doctypes** plus six more from their own
form scripts, with mention support and native bell notifications. Measured usage: **2,021
`comment_type='Comment'` rows, of which exactly 2 are threaded replies** in 13 months.

Its verdict stands: *"Any chat design must either (a) explicitly supersede/absorb it, or (b)
explicitly stay out of its lane (channels ≠ document comments). Shipping a second overlapping surface
without saying which one wins is the predictable failure here."* **This ADR takes (b): document
comments remain the record-attached commentary surface; chat rooms are conversations. A per-document
`Chat Room` links *to* a document but does not replace its comments.** Whether to eventually absorb
the Comments App is a `CQ` item, not a Phase 1 decision.

---

### F.18 The permission model, followed through to its consequences

#### F.18.1 The layered decision, from `notes_close_repo.md` §1.6

Invariant I5 requires that generic tooling cannot ingest chat content. `notes_close_repo.md` §1.1
established the fact that decides the design: **the MCP surface is three surfaces with three
different permission models**, and `run_database_query` *"cannot be closed by any permission
mechanism Frappe offers, because it does not use one"* — it is gated on the System Manager role and
executes raw SQL, which consults no DocPerm, no `permission_query_conditions` and no
`has_permission` at any point.

| Layer | What | Closes |
|---|---|---|
| **1** | **Zero DocPerm** — an empty `permissions` array on every chat *content* DocType | `/api/resource`, the desk, `get_document`, `list_documents`, `search_documents`, `generate_report` (subject to a `VERIFY` in `notes_close_repo.md` §6 item 1) |
| **2** | **A denylist in `assistant_tools/_gate.py`, above the settings check** | `run_database_query` and `run_python_code`, which Layer 1 does not touch |
| **3** | **`permission_query_conditions` + `has_permission` as a paired obligation** | deferred; fires only if a role is ever granted a read DocPerm on a chat DocType |

Layer 2 uses a seam this app **already owns and already runs in production**: `_gate.py` wraps
`BaseTool._safe_execute`, sees every tool's name and raw arguments before dispatch, and runs even
when `ai_write_gating_enabled` is off because the flag is checked *inside* the wrapped function
(`notes_close_repo.md` §1.5). A denylist branch inserted above that check therefore binds
unconditionally — which matters, because a denylist that a settings form can switch off is not an
invariant. Refusals reuse the existing `_error_response` envelope and write an `AI Action Log` row
through `insert_action_log`, so an attempt to read chat through a generic tool is **evidence, not
silence**.

For `run_database_query` the rule is coarse and absolute, because string-matching SQL is exactly the
filter that loses to backticks, comments, case and subqueries: *if the query text, case-folded and
with backticks and whitespace stripped, contains any chat table name, refuse the whole call.* Do not
attempt to allow "safe" queries. Over-refusal costs an analyst one rephrase; under-refusal costs the
invariant.

**Zero DocPerm is a new precedent in this app, and the ADR says so.** `notes_close_repo.md` §1.2.2
enumerated all 187 DocTypes this app ships: **0 of 187 have an empty `permissions` array.** The
"Training Course doctrine" that everyone cites is an **analogy, not a precedent** — `Training Course`
itself carries three DocPerm rows, and the doctrine is *"the learner roles hold no DocPerm"*, whose
adversary is a `desk_access = 0` Website User. Our adversary is the MCP session of a person who
legitimately holds System Manager. And `Administrator` bypasses everything unconditionally, before
any hook runs (`frappe/permissions.py`, quoted at `notes_close_repo.md` §1.2.2): **"no DocPerm" means
"unreachable by everyone except Administrator", not "unreachable".**

#### F.18.2 What this costs, and what it therefore forces in Phase 6

Everything in this list stops working for chat content, and every item is simultaneously a security
win and an ergonomic loss (`notes_close_repo.md` §1.2.3):

`/api/resource/Chat Message` (403 for all but Administrator) · desk list view · desk form view ·
report view and Report Builder · export · print · email-this-doc · assignment · share · awesomebar
and global search · link-field autocomplete · list-view filters, sorting, counts and sidebar stats.

**Therefore the Phase 6 admin oversight view cannot be a desk list view, and Phase 6 must not be
allowed to discover that.** It is one of:

- a **custom desk Page** — this app ships **19** of them and it is the established way to put an
  admin surface in the desk without a list view (`google_drive/page/drive_link_manager/`,
  `integration_hub/page/integrations_health/`, `device_management/page/device_console/`,
  `quickbooks_online/page/quickbooks_online_dashboard/`, `stripe_payments/page/stripe_payments_dashboard/`,
  `workforce/page/time_kiosk/`); or
- a **`www/` portal page**, which is what `/training` does for `desk_access = 0` users.

Either way the shape is fixed: open with `frappe.only_for("<Chat Auditor role>")` (the idiom is
`drive_sync.py:684`, or the multi-role `frappe.only_for(TRIAGE_ROLES)` at
`crm_enhancements/fountain_move/api.py:30`), read with `frappe.get_all` / `frappe.db.sql`, and **write
a `Chat Retrieval Audit` row per call** (§F.12).

#### F.18.3 The realtime consequence — and a deviation from a sibling note, declared

> **Seam note (assembly).** **This subsection does not state the shipped design; it states the recorded
> fallback and the argument Phase 1 must answer.** `DECISIONS.md` D2 explicitly lifts
> *"realtime scoped to the channel doc room rather than per-user fan-out"* from Raven, and D2 is
> binding, so message content rides `doc:Chat Room/<room>` — made joinable by the minimal
> `Chat Room`-only DocPerm at
> [§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved),
> which was written with the socket-source evidence this subsection did not have
> ([§H.4.1](#h41-the-channel-split--confirmed-not-provisional)). Everything below is kept
> verbatim because it names two residuals the doc-room design really does carry —
> **cooperative-only eviction** and **silent join refusal** — and because it is the escape hatch if either
> proves intolerable. **`CHAT-RT-1/2/3` at the end of this subsection are canonical and apply to
> the shipped design unchanged.** Seams **S1** and **S2**; carried as **P3-1**, which Phase 1 must
> sign off before writing a DocType JSON.

`notes_close_frappe.md` §1.7 found the collision and was right to call it *"the most dangerous finding
in this file"*. Frappe v16's socket.io server permission-checks doc-room joins by calling back into
Python:

```js
socket.on("doc_subscribe", function (doctype, docname) {
    socket.has_permission(doctype, docname).then(() => { socket.join(doc_room(doctype, docname)); });
});
```

which reaches `frappe.realtime.has_permission` → `frappe.has_permission(doctype, doc=name, throw=True)`
under the **joining user's own session**, with `ptype = "read"`. **With zero DocPerm, that fails for
every non-Administrator, and the join is refused — silently**, because `socket.has_permission` has no
reject path at all: on denial the promise is left pending forever and `.catch` only logs
(`notes_close_frappe.md` §1.5 item 1). So message content on `doc:Chat Room/<room>` does not work
under Layer 1.

`notes_close_frappe.md` §1.7 resolves this by **giving chat DocTypes a minimal `read` DocPerm plus the
`permission_query_conditions` + `has_permission` pair**, and closing the MCP surface with the pair
instead of with Layer 1.

**We deviate, and here is the reasoning.** That resolution predates `notes_close_repo.md`'s finding
that **the pair does not close `run_database_query` at all** — it is raw SQL below the permission
layer — so trading Layer 1 away for the pair buys realtime and loses the invariant. Instead:

> **Chat carries message content on per-user rooms (`user:{email}`), not on doc rooms. Chat does not
> use `doc_subscribe` at all.**

Three properties make this affordable and, on balance, better:

1. **The cost is measured and negligible.** Steady state is *"2,000 messages/day ≈ 0.023 writes/sec
   average, maybe 2–3/sec at a standup spike"* (`notes_research_gaps.md:436`), and the largest
   plausible room at a 20-person company is ~20 people. Worst case ≈ 60 Redis publishes/second at a
   spike, against a Redis instance measured at 4.75 MB used of 1.57 GB with 11 connected clients
   (`notes_infra.md:565`).
2. **It fixes the eviction hole that the doc-room design cannot fix.** Doc-room membership is checked
   **once, at join**: a user removed from a room *keeps receiving message content* until they
   disconnect or unsubscribe, and the best available remedy is a **cooperative** eviction push that a
   hostile client can simply ignore (`notes_close_frappe.md` §1.5 item 3, which explicitly refuses to
   paper over the residual). With per-user fan-out, membership is evaluated **server-side on every
   publish** — which is precisely this repo's own stated realtime doctrine: *"Clients never emit
   realtime events to each other directly — this endpoint is the security authority for every
   broadcast"* (`api/collab.py:1-41`).
3. **It keeps the permission story uniform**, which is what makes the Layer-1 regression test
   expressible as a one-line filesystem assertion: *walk `erpnext_enhancements/chat/doctype/*/*.json`
   and assert `permissions == []` for every non-`istable` DocType* — modelled on the existing
   `tests/test_doctype_modules.py` (`notes_close_repo.md` §1.7 assertion 1). A design where three
   DocTypes are exceptions cannot be tested that cheaply, and *"somebody adds a System Manager row so
   they can look at a message in the desk"* is the most likely regression in the whole feature.

**Recorded fallback:** if fan-out ever becomes a real cost — it will not at this roster — the escape
is `notes_close_frappe.md` §1.7's design (minimal DocPerm on `Chat Room` **only**, plus both hooks,
with `Chat Message` keeping zero DocPerm), or the app-owned socket handler at
`apps/erpnext_enhancements/realtime/handlers.js`, which v16 auto-loads with an authenticated socket
and which `notes_close_frappe.md` §1.8 documents as an available extension point (not recommended for
V1: it lives outside the esbuild pipeline and needs a socketio restart to take effect).

**Three realtime invariants, named here because they are schema-adjacent and Phase 1 must lint them**
(all from `notes_close_frappe.md` §5 and `DECISIONS.md` D8):

- **`CHAT-RT-1`** — every chat `publish_realtime` call passes **`room=` explicitly**, computed by a
  single chat-owned helper. Not `user=`, not `doctype=`+`docname=`. This is the only form immune to
  *both* failure modes: `publish_realtime`'s final fallback is `get_site_room()` = `"all"` — a room
  **every System User is already sitting in** — and, worse, inside a background job with
  `frappe.local.task_id` set, a call passing `user=` and no `room=` is silently retargeted to
  `task_progress:<task_id>`, a room **any client may join with no permission check whatsoever**.
  Chat relay work happens in background jobs, so the second failure mode is the live one.
- **`CHAT-RT-2`** — no chat event may be named `list_update` or `docinfo_update`. Both names
  **unconditionally overwrite an explicitly passed `room=`**, before the `if not room:` guard.
- **`CHAT-RT-3`** — the fan-out helper refuses `Guest`. A Guest socket joins `user:Guest`, which is a
  **shared** room, not a private one (`notes_close_frappe.md` §1.6).

#### F.18.4 Which DocTypes carry DocPerm, and the standing obligation

| DocPerm | DocTypes | Why |
|---|---|---|
| **none** | `Chat Room`, `Chat Room Member`, `Chat Message`, `Chat Attachment`, `Chat Relay Job`, `Chat Inbound Event`, `Chat Context Chunk`, `Chat Room Digest`, `Chat Thread Digest` | Layer 1 |
| `System Manager` read/report | `Chat Retrieval Audit` | an audit log nobody can read is not an audit log (§F.12) |
| `System Manager` read/write | `Chat Settings` | a Single with no DocPerm cannot be opened |
| n/a | `Chat Mention`, `Chat Retrieval Audit Room`, `Chat Allowed User` | child tables never carry their own DocPerms |

> **Seam note (assembly).** One row of that table is superseded. **`Chat Room` carries a minimal `read`
> DocPerm** plus the `permission_query_conditions` + `has_permission` pair, because the socket
> join is keyed on the room and nothing else can make it permission-resolvable
> ([§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved)).
> `Chat Room` stays on the `_gate.py` denylist regardless, because `run_database_query` consults
> no permission layer at all. Every other DocType in the `none` row is unchanged, `Chat Message`
> emphatically included. Seam **S2**. The standing obligation immediately below therefore fires
> **now**, in Phase 1, rather than being deferred.

**Standing obligation (Layer 3), written as a rule so a future commit cannot skip it:** *if any role
is ever granted a `read` DocPerm on a chat DocType — the likely trigger is wanting a desk list view
for a Chat Auditor — that same commit must add **both** `permission_query_conditions` and
`has_permission`, per the ten-and-ten parity doctrine at `hooks.py:1123-1164`, and must add a test
modelled on `tests/test_kpi_snapshot_permissions.py`.* A `has_permission` hook can only **restrict**
what DocPerms already grant, so the DocPerm row is the outer bound and the pair is the only thing
between a member-scoped read and a full-table read.

`VERIFY: that FAC's get_document uses frappe.get_doc/has_permission and list_documents uses
frappe.get_list rather than frappe.get_all` — FAC is in neither repo and its GitHub raw URL 404s;
read `apps/frappe_assistant_core/**/tools/` on the bench. **Blocks:** whether Layer 1 closes two of
the three surfaces or one. It does **not** change the recommendation, which closes all three
regardless. (`notes_close_repo.md` §6 item 1.)

`VERIFY: whether frappe.db.sql is reachable inside run_python_code's sandbox` — the tool pre-loads the
`frappe` module by its own description. **Settle:** run `print(frappe.db.sql("select 1"))` through it.
**Blocks:** whether the Layer-2 denylist needs a `code`-argument branch as well as a `query` one.

---

### F.19 Consolidated index register

Every index in the design, with the single query it serves. **An index with no named query is not in
this table and is not created.**

| DocType | Index | Kind | Query it serves |
|---|---|---|---|
| Chat Room | `gchat_space_name` | UNIQUE (DocField) | inbound event → room resolution; forbids two rooms per space |
| Chat Room | `(linked_doctype, linked_document)` | UNIQUE (patch) | idempotent find-or-create of a per-document room under a `doc_event` race |
| Chat Room | `(dm_user_1, dm_user_2)` | UNIQUE (patch) | open the DM between A and B, order-independent |
| Chat Room Member | `(room, user)` | UNIQUE (patch) | **the membership check that gates every read**; forbids duplicate members |
| Chat Room Member | `(user, is_active)` | index | "every room this user is in" — SPA boot, unread badge, retrieval `allowed_rooms` |
| **Chat Message** | **`(room, seq)`** | **UNIQUE (patch)** | the backlog page, the unread count, the D6 `count(*)`, the seq allocator — and D6's uniqueness |
| **Chat Message** | **`gchat_message_name`** | **UNIQUE (DocField)** | **inbound dedupe in one probe — structural, not procedural** |
| **Chat Message** | **`(room, client_message_id)`** | **UNIQUE (patch)** | **echo suppression in one probe** |
| Chat Message | `(thread_root, seq)` | index | the thread panel |
| Chat Message | `gchat_thread_name` | index | inbound threaded reply → ERPNext thread root |
| Chat Message | `creation` | **`VERIFY:`** assumed Frappe default — see below | the retention purge's cutoff scan |
| Chat Mention | `parent` | Frappe default | render a message's mentions |
| Chat Attachment | `message` | index | render a message's attachments |
| Chat Attachment | `gchat_attachment_name` | UNIQUE (DocField) | idempotent inbound attachment ingest |
| Chat Relay Job | `(room, job_seq)` | UNIQUE (patch) | the per-room FIFO that guarantees Create-Before-Edit |
| Chat Relay Job | `(status, available_at)` | index | the sweeper's only query |
| Chat Inbound Event | `pubsub_message_id` | UNIQUE (DocField) | Pub/Sub at-least-once dedupe |
| Chat Inbound Event | `(status, received_at)` | index | the stuck-event sweeper |
| Chat Context Chunk | `(room, first_seq)` | index | the permission-filtered candidate scan (cap N=8,000) |
| Chat Context Chunk | `(room, last_seq)` | index | "which chunk covers seq X" — the staleness marker |
| Chat Room Digest | `room` | UNIQUE (DocField) | fetch the room's digest; forbids a duplicate rebuild |
| Chat Thread Digest | `thread_root` | UNIQUE (DocField) | fetch the thread's digest |
| Chat Retrieval Audit | `(accessed_by, creation)` | index | "everything this person read" |
| Chat Retrieval Audit | `(actor_type, creation)` | index | "every Admin-type read in the period" |

**`VERIFY:` `Chat Message.creation` is indexed by Frappe without us asking.** This row was written from
an observation of `tabNotification Log` showing a `MUL` key on `creation`, which is equally explained by
that DocType's own `search_index` — and Frappe's MariaDB table template indexes `modified`, not
`creation`. Nobody has run this against a bench, so the register must not be read as asserting it.

- **How to settle:** after `bench --site <site> migrate` on a site carrying this app, run
  ``SHOW INDEX FROM `tabChat Message`;`` and look for a key whose column is `creation`.
- **What it blocks:** Phase 6's retention purge, whose cutoff scan is a range over `creation` across the
  hot table. Nothing in Phases 1–5 depends on it, which is why it is carried rather than fixed blind.
- **Remedy if absent:** add `("Chat Message", ("creation",), "creation_index")` to `INDEXES` in
  `erpnext_enhancements/patches/add_chat_indexes.py` and correct this row's Kind to `index (patch)`.

**Explicitly rejected**, so a later phase does not add them by reflex: `Chat Room.provisioning_state`,
`Chat Room.last_message_at`, `Chat Room.room_type`, `Chat Room Member.last_read_seq`,
`Chat Room Member.gchat_membership_name`, `Chat Message.(sender, creation)`,
`Chat Message.sync_state`, `Chat Message.is_deleted`, a FULLTEXT index on `Chat Message.text_plain`,
`Chat Attachment.room`, `Chat Attachment.ingest_state`,
`Chat Relay Job.(reference_doctype, reference_name)`, `Chat Inbound Event.gchat_resource_name`,
`Chat Inbound Event.event_type`, `Chat Context Chunk.content_hash`, and `is_stale` on either digest.
Each rejection's reason is in the DocType's own subsection.

**Mechanics reminder (§F.2):** single-field unique constraints are `unique: 1` on the DocField;
composites go through `frappe.db.add_unique(doctype, fields, constraint_name=None)` in a patch and
every column in them must be non-empty at insert. `frappe.db.add_index` takes a list of fields.
Neither helper can emit a prefix index, and none is needed at `varchar(255)`.

---

### F.20 Section F's open items

#### F.20.1 Human questions raised by the data model (`CQ` register entries)

| # | Question | Our recommendation |
|---|---|---|
| CQ-F1 | **Are per-message read receipts actually needed for DMs?** | **No.** In a two-member room the high-water mark and the receipt set are information-theoretically identical (§F.15.6). If per-message read *times* are a real requirement, build `Chat Read Advance` (one row per advance, ~200/day) rather than a receipt table (~20,000/day). |
| CQ-F2 | **Does "delete" have to mean the bytes are gone?** | Keep soft delete (I10 needs the body to exist), and set `Chat Settings.hard_delete_after_days` to promote soft deletes to real deletion on a schedule. One dial, and the human sets it. §F.6.5. |
| CQ-F3 | **Does chat absorb or coexist with the existing Comments App?** | Coexist. Chat rooms are conversations; `Comment` + `custom_parent_comment` remains the record-attached commentary surface on 29 doctypes. Revisit only with usage evidence — today it is 2 threaded replies in 13 months. §F.17.7. |
| CQ-F4 | **Should `{"Notification Log": 30}` retention ship with chat?** | It is a **pre-existing, unrelated** 13-month leak and a one-line fix that also unlocks the bulk-delete path. Offered as its own change, not folded into the chat PR. §F.17.5. |
| CQ-F5 | **Which rooms default to `provisioning_mode = Not Mirrored`?** | A policy question about which conversations may leave ERPNext at all. Recommendation: per-document rooms on HR-, payroll- and legal-adjacent doctypes default to `Not Mirrored`; everything else to `On First Message`. §F.4.1. |
| CQ-F6 | **Delegating retrieval to Triton's Vertex AI RAG instead of `Chat Context Chunk`.** | Rejected for V1 and surfaced rather than buried, per `DECISIONS.md` D4: Triton's RAG is **per-user corpora** — *"Every user gets their own RagCorpus… There is no shared corpus"* — which does not map onto membership-defined room visibility, and decision #6's gate must live in ERPNext. |

#### F.20.2 `VERIFY` register carried forward from section F

| ID | Claim | How to settle | What it blocks |
|---|---|---|---|
| F-V1 | Frappe's `""`→`NULL` coercion fires end-to-end for a `unique: 1` DocField | insert two `Chat Message` rows with `gchat_message_name` unset; ``SELECT COUNT(*) … WHERE gchat_message_name IS NULL`` | the inbound dedupe index; failure is production-only and looks like a random insert error |
| F-V2 | Real Chat message resource names fit comfortably in `varchar(255)`, and their charset is URL-path-safe | the N≥30 / three-spaces / both-auth-paths procedure at `notes_close_google.md` §1.7 | nothing under the 255 recommendation — it confirms rather than gates; it does settle SPA deep-link safety |
| F-V3 | The length and alphabet of a Frappe v16 hash name | `frappe.generate_hash()` in a bench console | nothing — §G.2's derivation is built so neither matters |
| F-V4 | Frappe v16's `/private/files/` gate calls `frappe.has_permission(attached_to_doctype, doc=…)` | read `frappe/core/doctype/file/file.py` and `.../utils.py` on the bench | whether the chat download endpoint is mandatory or merely preferred |
| F-V5 | The sub-field names of a Google `USER_MENTION` annotation | fetch the `Annotation` reference, or capture one real payload | faithful inbound mention offsets only |
| F-V6 | Whether `erpnext_enhancements` registers any `doc_events["*"]` handler that would fire on chat DocTypes | read the `doc_events` block in `hooks.py` | whether Phase 1 needs an explicit ignore list |
| F-V7 | FAC's `get_document` / `list_documents` use permission-checked calls | read `apps/frappe_assistant_core/**/tools/` on the bench | whether Layer 1 closes two MCP surfaces or one — not the recommendation |
| F-V8 | `frappe.db.sql` reachability inside `run_python_code` | `print(frappe.db.sql("select 1"))` through the tool | whether the Layer-2 denylist needs a `code`-argument branch |
| F-V9 | v16 `__global_search` read-back is permission-filtered for a zero-DocPerm doctype | read `frappe/utils/global_search.py::search` | whether `search_documents` needs its own denylist branch |
| F-V10 | `Notification Type` rows exist on prod, and fixture-vs-installer for a custom type | `frappe.get_all("Notification Type", fields=["name","enabled"])` | Phase 4's delivery mechanism only |
| F-V11 | No enabled `User` has `name != email` | ``SELECT COUNT(*) FROM `tabUser` WHERE name <> email AND enabled = 1`` | Phase 4's member→recipient resolver silently dropping everyone |
| F-V12 | `tabNotification Log` has an index on `for_user` after the v16 migration | re-run the `information_schema.STATISTICS` query from `notes_infra.md` | whether Phase 4 adds the index itself |

---

## G. Sync protocol

### G.0 The four flows, and the one sentence that shapes all of them

| Flow | Transport | Identity | Guarantee |
|---|---|---|---|
| **Outbound** ERPNext → Chat | REST to `chat.googleapis.com` | DWD as the authoring human (`DECISIONS.md` D3) | **the outbox sweeper**, not the queue (§G.1) |
| **Inbound A** interaction events (@triton, DMs to the app) | **HTTPS to a Frappe endpoint** | Chat app (`chat.bot`) | 30-second synchronous deadline (§G.4.1) |
| **Inbound B** the coworker firehose | **Pub/Sub, pull** | DWD per coworker (shape B) | subscription + a `spaceEvents.list` reconciliation sweep (§G.4.2, §G.5) |
| **Triton replies** | REST, app auth | the registered Chat app | same outbox (§G.1) |

The sentence that shapes everything: **the production deploy `FLUSHDB`s the queue Redis, so a queued
job does not survive a deploy.** `infra/cloudbuild-deploy.yaml` ends
`bench --site all migrate && bench build && redis-cli -p 13000 FLUSHDB && redis-cli -p 11000 FLUSHDB' && sudo systemctl restart frappe-bench`
(`notes_close_repo.md` §3.0, read from source; `notes_infra.md:592-597`). Port 11000 is the queue.
**Every RQ job that is queued-but-not-started at deploy time is destroyed, silently, with no error
and no dead-letter.** Therefore:

> **The queue is a latency optimisation. The sweeper is the delivery guarantee.**

**Assume RQ retries do not exist.** Nothing in this design depends on a broker-level retry,
dead-letter queue or visibility timeout. Every retry is a row in `Chat Relay Job` or
`Chat Inbound Event` with an `attempts` counter and an `available_at`, re-driven by a scheduled
sweeper — which is also the existing house rhythm: this app already runs nine such sweepers
(`notes_close_repo.md` §3.3).

---

### G.1 Outbound — the transactional outbox

#### G.1.1 The write path

```python
# erpnext_enhancements/chat/doctype/chat_message/chat_message.py  (controller)

def before_insert(self):
    self.seq = _allocate_seq(self.room)                 # F.16.1, inside this transaction
    self.client_message_id = derive_client_message_id(self)   # G.2, once, stored forever
    self.sync_state = "Pending" if _room_is_mirrored(self.room) else "Not Mirrored"

def after_insert(self):
    _write_room_denormalised_fields(self)               # set_value(..., update_modified=False)
    _fan_out_realtime(self)                             # per-user rooms, CHAT-RT-1..3
    if self.sync_state != "Pending":
        return
    # THE OUTBOX ROW — same transaction as the message. Not a queue entry. A row.
    enqueue_relay(room=self.room, operation="Message Create",
                  reference_doctype="Chat Message", reference_name=self.name,
                  payload={"text_bytes_budget": settings.message_byte_limit})
```

```python
# erpnext_enhancements/chat/relay.py

def enqueue_relay(*, room, operation, reference_doctype, reference_name, payload=None):
    """Create the outbox row and (optimistically) wake a worker. Never raises."""
    try:
        job = frappe.get_doc({
            "doctype": "Chat Relay Job",
            "room": room,
            "job_seq": _allocate_job_seq(room),          # unique(room, job_seq) — G.8 rule 1
            "operation": operation,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "status": "Pending",
            "available_at": frappe.utils.now_datetime(),  # NEVER SQL NOW() — F.16.2 rule 3
            "request_id": derive_request_id(operation, reference_name),
            "payload": json.dumps(payload) if payload else None,
        }).insert(ignore_permissions=True)

        frappe.enqueue(
            "erpnext_enhancements.chat.relay.run_relay_job",
            queue="long",
            enqueue_after_commit=True,      # MANDATORY — see G.1.2
            job_id=f"chat-relay-{job.name}",
            deduplicate=True,
            job_name=job.name,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Chat relay enqueue")
```

Four properties copied verbatim from the house pattern
(`google_drive/drive_utils.py:29-46` + `fountain_move/photos.py:155-164`, indexed at
`notes_close_repo.md` §7):

1. **The whole body sits in `try/except`**, so a relay problem can never block the message insert.
   Decision #1 says ERPNext is the source of truth; a mirror that can refuse a write is not a mirror.
2. **The job takes a document *name*, never a document.**
3. **`job_id=` + `deduplicate=True`** — the house answer to "the same work got queued twice", which
   matters because the sweeper will re-enqueue rows the queue may still hold.
4. **`queue="long"`** for anything that makes a network call.

**The counter-example not to copy** is in this repo too: `utils/triton_sync.py:62-70` does
`frappe.enqueue('requests.post', ...)` — a raw library call as the job target, with **no
`enqueue_after_commit`, no retry, no logging of the outcome, and no record that it was attempted**
(`notes_close_repo.md` §3.2). If the deploy flushes the queue, that webhook is gone and nothing
anywhere knows. That is precisely the failure the chat relay must not have.

#### G.1.2 `enqueue_after_commit=True` is mandatory, not stylistic

Without it, the worker can pick the job up **before the inserting transaction commits**, read the
`Chat Relay Job` row that is not yet visible to it, find nothing, and exit successfully. The message
is then in the database and in nobody's outbox as far as the worker is concerned — recoverable only
by the sweeper, and only after the stale-job timeout. Of this app's 66 `frappe.enqueue` call sites,
**46 already pass `enqueue_after_commit=True`** (`notes_close_repo.md` §3.1); chat's do too, without
exception, and a source-level test asserts it (§G.1.7).

Frappe's own notification path has the same property and the same exposure:
`enqueue_create_notification` uses `enqueue_after_commit=not frappe.in_test`, so **a deploy landing
between commit and worker pickup destroys the notification while the message row survives**
(`notes_close_frappe.md` §3.8 item 4). Same failure class, same answer: the sweeper must be able to
detect and re-issue a missing notification, which is safe only because `dedupe_on` makes re-issue
idempotent (§F.17.4). **These two facts are only useful together.**

#### G.1.3 The sweeper is the delivery guarantee

```python
# scheduler_events: cron "2,7,12,17,22,27,32,37,42,47,52,57 * * * *"
def sweep_relay_jobs():
    now = frappe.utils.now_datetime()
    rows = frappe.get_all("Chat Relay Job",
        filters={"status": "Pending", "available_at": ["<=", now]},
        fields=["name", "room", "job_seq"],
        order_by="available_at asc",
        limit_page_length=settings.sweeper_batch_size)          # 200, per drive_sync
    for row in rows:
        try:
            frappe.enqueue("erpnext_enhancements.chat.relay.run_relay_job",
                           queue="long", job_id=f"chat-relay-{row.name}",
                           deduplicate=True, job_name=row.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Chat relay sweep")
```

Design points, each with its precedent:

- **Bounded** (`limit_page_length=200`) — *"a sweeper must be bounded or a bad day queues everything
  at once"* (`notes_close_repo.md` §3.3).
- **Per-row `try/except` with `frappe.log_error`** — one poisoned row does not stop the sweep.
- **`update_modified=False` on every bookkeeping write** — and note the D6 interaction explicitly:
  the sweeper touches `Chat Relay Job`, not `Chat Message`, so it cannot disturb the watermark at
  all. That separation is one of the three reasons §F.9 chose a separate table.
- **Cadence: a `cron` entry at 5-minute intervals, offset off :00/:20/:40.** `hourly` is far too
  coarse for chat given the FLUSHDB exposure. The house already uses `cron` for staggering rather
  than the named buckets, with the reason written out — *"firing them at the same instant made two
  saves race"* (`hooks.py:583-594`, per `notes_close_repo.md` §3.5). The QBO jobs own :00/:20/:40, so
  chat takes the odd-minute offsets above.
- **A stale `In Progress` reaper.** A worker killed mid-flight (a deploy restart is exactly this)
  leaves a row `In Progress` forever. Rows in `In Progress` with `modified` older than the `long`
  queue timeout of **1500 s** (`notes_research_gaps.md:415`) are returned to `Pending`. Without this,
  the FLUSHDB that motivates the sweeper also creates rows the sweeper will not pick up.

#### G.1.4 Rate limiting — a per-space token bucket, and what it must charge

**There is no rate limiter in this repo to lift, and the repo says so in its own words.**
`notes_close_repo.md` §2.1 grepped for `token bucket`, `ratelimit`, `rate_limit`, `backoff`,
`Retry-After`, `429`: the single hit in application code is a statement that one does *not* exist —
`api/collab.py:36-40`, *"v2 hardening, should abuse appear: a `frappe.cache()` token bucket keyed by
`(user, docname)`."* That is the house's own design intent for exactly this component, written by the
author of the nearest analogous subsystem. **For chat the key is `(space,)`, not `(user, docname)`.**
Two specific corrections to earlier speculation: `quickbooks_online/core/client.py` has **no** 429
handling, backoff or throttle of any kind (its only retry is refresh-once-on-401), and
`google_drive/drive_utils.py` open-codes a weak retry loop five times. Neither is the thing to copy.

**The buckets, with the limit each enforces:**

| Bucket key | Limit | Covers |
|---|---|---|
| `chat:ratelimit:space:{room}` | **1 write / second** | `media.upload`, `spaces.delete`, `spaces.patch`, `spaces.messages.create`, `.delete`, `.patch`, `spaces.messages.reactions.delete` |
| `chat:ratelimit:project:message_writes` | 3000 / 60 s | `messages.create` / `.patch` / `.delete` |
| `chat:ratelimit:project:space_writes` | **60 / 60 s** | `spaces.setup`, `.create`, `.patch`, `.delete` |
| `chat:ratelimit:project:membership_writes` | **300 / 60 s** | `spaces.members.create` / `.delete` |
| `chat:ratelimit:project:attachment_writes` | 600 / 60 s | `media.upload` |

(All from `notes_google_verify.md:964-1003`, quoting
<https://developers.google.com/workspace/chat/limits>. Reads are 15/sec per space and 3000/60 s per
project — a separate, looser bucket, or none.)

**`media.upload` charges TWO tokens from the per-space bucket.** It shares that bucket with
`messages.create` (`notes_google_verify.md:1001-1003`; `DECISIONS.md` D8), so a message with an
attachment consumes **two seconds** of that space's write budget, not one. A bucket that charges one
will 429 on exactly the "someone drops a screenshot into a busy space" case. `Chat Settings.
upload_token_cost` defaults to 2 and exists so the number is visible rather than buried.

**Implementation constraints, each of which is a bug this repo has already paid for**
(`notes_close_repo.md` §2.2(ii), quoting `fountain_move/intake.py:775-791`):

1. **Atomic, via raw redis-py `incrby` + `expire`.** A `get_value` → arithmetic → `set_value` bucket
   loses the race between two workers and double-sends.
2. **Namespace by hand with `frappe.cache.make_key(key)`.** `frappe.cache.incrby` / `.expire` are raw
   redis-py and do **not** get the wrapper's per-site prefix; on a shared bench two sites would
   increment the same counter and rate-limit each other. (`tests/test_error_log_fixes.py:80-93` pins
   this split, including that *"redis returns bytes, never an int"*.)
3. **Only the creator sets the TTL**, or a sustained burst pushes expiry out forever.
4. **It must FAIL OPEN.** The deploy `FLUSHDB`s the cache Redis, so a missing key must read as *a
   fresh, full bucket*, never as *zero tokens*. `incrby` on a missing key returns 1, which gives this
   for free — but the obvious "read the count, refuse if missing" formulation is fail-**closed**, and
   a fail-closed bucket silently stops all outbound relay after every deploy with the worst possible
   symptom: messages stop, nothing logs. **This must be asserted by a test.**

**A 429 returns the row to the outbox; it never sleeps the worker.** Sleeping a `long`-queue worker
for a second per message across the roster is how the queue backs up. The handler sets
`available_at = now + backoff` and `status = Pending`, and returns.

**Backoff.** Lift `offsite_backup/drive.py:46-50, 111-139` — the only implementation in this repo
with a named retryable-status constant and transport-error handling:
`RETRYABLE_STATUS = (429, 500, 502, 503, 504)`, `MAX_ATTEMPTS = 5`, `INITIAL_BACKOFF_SECONDS = 2`,
plus `ConnectionError`/`TimeoutError` because *"the request never reached a decision, so retrying it
is safe rather than merely hopeful"*. **Close its two gaps**: it has **no jitter** (`delay *= 2`
exactly) and does **not read `Retry-After`**. Google Chat 429s are per-space, so N workers retrying
one space in lockstep at 2/4/8/16 s is a thundering herd on a bucket that refills at one token per
second. Chat's backoff is `min(cap, 2**attempt) * (1 + random())`, and honours `Retry-After` when
present.

`VERIFY: the exact Google Chat 429 response shape — does it carry Retry-After, and is the quota error
distinguishable from a permission 403?` — `notes_google_verify.md` documents the limits but not the
error payload. **Settle:** <https://developers.google.com/workspace/chat/limits> plus one deliberate
over-send in a test space; record the exact `status` string (`RESOURCE_EXHAUSTED`?) into
`Chat Relay Job.google_error_status`. **Blocks:** the retryable classification.
(`notes_close_repo.md` §6 item 6.)

**The considered alternative, recorded rather than silently dropped:** a per-space
`frappe.utils.synchronization.filelock(f"gchat_space_{space_id}")` around each send, plus a sleep to
the next slot, gives strict serialisation with no Redis state at all — the idiom exists twice in this
repo already (`quickbooks_online/core/client.py:22,41,152`; `stripe_payments/core/payouts.py:37,198`).
**Rejected for V1** because it blocks a worker for up to a second per message; **kept as the fallback**
if the Redis bucket proves fiddly.

#### G.1.5 Bulk provisioning is a resumable job, because of two project-wide ceilings

**60 space writes/minute** and **300 membership writes/minute** are project-wide, not per-space
(`notes_google_verify.md:971-973`). `notes_google_verify.md:990` states the consequence plainly:
*"60 spaces/minute means provisioning 1,000 project spaces takes ~17 minutes minimum."*

Bulk provisioning is therefore never a single long-running job. It is:

- **`Chat Relay Job` rows** — one `Space Create` per room, one `Member Add` per member (or one
  `spaces.setup` carrying **up to 49 memberships**, `notes_research_gaps.md:366`, which is the
  quota-efficient shape and the reason `spaces.setup` is preferred over `create` + N × `members.create`);
- **drained by the same 5-minute sweeper**, subject to the same buckets;
- **resumable by construction**, because the state is rows: a deploy, a crash or a `pause_outbound`
  mid-run leaves the remaining rows `Pending` and the run continues from where it stopped.
  `Chat Room.provisioning_state` is the denormalised progress indicator, not the state machine.

This is also why §F.4.1 defaults `provisioning_mode` to `On First Message`: at 60 space writes/minute
the difference between eager and lazy provisioning of per-document rooms is the difference between
109 minutes of saturated quota and none.

`spaces.setup` carries one more constraint worth stating here rather than discovering in Phase 2:
it explicitly **cannot create a threaded space** — *"Spaces with threaded replies aren't supported"*
(`notes_google_verify.md:619`). See §G.9.

#### G.1.6 Dead-letter and the "quietly went nowhere" digest

`attempts >= Chat Settings.relay_max_attempts` (5) moves the row to `Dead`. Because **there is no
dead-letter pattern anywhere in this repo** — every existing retry loop stops at the cap and the row
stays `Failed` with nobody notified (`notes_close_repo.md` §3.5 item 7) — chat builds the one thing
that is missing, modelled on the nearest analogue,
`esign.tasks.digest_awaiting_signature` (weekly, `hooks.py:744`): *"one summary of every agreement
still out for signature, so a link that quietly went nowhere is visible without anyone remembering to
look."*

`digest_dead_relay_jobs()` runs daily, reports the count and the ten oldest, and the SPA renders a
per-message "not delivered to Chat" affordance from `Chat Message.sync_state = "Failed"`. A silently
undelivered message is the failure a user cannot see, and decision #2's entire premise is that both
sides agree.

#### G.1.7 The source-level tests this section owes

Both are **bench-free**, so they run in CI on every PR — and per `CLAUDE.md` a new bench-free
**pytest** suite needs its own `python -m pytest <file> -q` step in `ci.yml`, never an append to a
unittest module list.

- **"Every outbound Chat API call goes through the bucket."** A regex over
  `erpnext_enhancements/chat/**.py` asserting that no module other than the transport calls
  `chat.googleapis.com`, and that the transport's send functions are decorated with the bucket.
  Modelled exactly on `tests/test_contract_esign.py:526-534`, which asserts *"a guest endpoint is
  missing its rate limiter"* by regex over the source.
- **"Every chat `frappe.enqueue` passes `enqueue_after_commit=True`."** Same technique. It is the
  single most consequential one-keyword omission in the design.

---

### G.2 Idempotency — the two deterministic derivations

#### G.2.1 The verified constraints on a client-assigned message id

Quoted verbatim from
<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>, and
independently re-verified this session (`notes_google_verify.md:651-653`;
`notes_close_google.md` §1.3):

> - "Begins with `client-`. For example, `client-custom-name` is a valid custom ID, but
>   `custom-name` is not."
> - "Contains up to **63 characters** and only **lowercase letters, numbers, and hyphens**."
> - "Is unique within a space. A Chat app can't use the same custom ID for different messages."

**A derivation that can exceed 63 characters or emit an illegal character is a bug.** The derivation
below cannot do either, by construction, and the proof is three lines.

#### G.2.2 `client_message_id`

```python
CLIENT_PREFIX = "client-"          # mandated by Google
_HEX_CHARS = 32                    # 128 bits

def derive_client_message_id(msg) -> str:
    """Deterministic, legal-by-construction, computed ONCE at insert and stored forever."""
    seed = f"{frappe.local.site}|Chat Message|{msg.name}"
    return CLIENT_PREFIX + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_HEX_CHARS]
```

**Why this is correct against all three constraints, and not merely "probably fine":**

| Constraint | Why it holds |
|---|---|
| begins with `client-` | literal prefix, unconditional |
| ≤ 63 characters | `len("client-") + 32 = 39`, **fixed**, independent of every input |
| `[a-z0-9-]` only | `hexdigest()` emits `[0-9a-f]` only — a strict subset. No normalisation step exists that could fail. |
| unique within a space | 128 bits of SHA-256 over a globally unique seed; collision probability is negligible, and the `unique(room, client_message_id)` index would catch one anyway |

**Rejected derivation:** `"client-" + msg.name.lower()`. It is reversible, which is superficially
attractive, and it is wrong twice — it is legal only if Frappe's hash alphabet is lowercase
alphanumeric, which **nobody has verified** (F-V3, R02-V02 still open), and it breaks silently the
day a naming rule changes.

**Non-reversibility is not a cost**, because nothing needs to invert it. Echo suppression asks *"do I
have a row with this `client_message_id`?"*, which is one probe on the `(room, client_message_id)`
unique index (§F.6.6).

**The site name is in the seed, and `client_message_id` is stored, never re-derived.** The seed
includes `frappe.local.site` so a non-production site relaying into the same Workspace mints different
ids rather than colliding with production's. But a site *rename* would then change every derivation —
which is exactly why the value is computed once in `before_insert`, stored in a `reqd` column, and
read from the row thereafter. The derivation function is called in exactly one place in the codebase,
and a test asserts that.

#### G.2.3 `requestId` for space and membership provisioning

`requestId` is a query parameter on `spaces.create` and `spaces.setup`
(`notes_research_gaps.md:631`). R01 §1's recommendation, at `notes_research_gaps.md:1166`, is that *"a
deterministic `requestId` derived from the ERPNext conversation ID gives exactly-once provisioning for
free"* — which is what stops a retried `Space Create` job from creating a second space for the same
room.

```python
def derive_request_id(operation: str, reference_name: str) -> str:
    seed = f"{frappe.local.site}|{operation}|{reference_name}"
    return str(uuid.UUID(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]))
```

UUID form, not raw hex, because it is the shape Google's `requestId` parameters conventionally take
and it is unambiguously safe. Stored on `Chat Relay Job.request_id` so a retry of *that row* replays
the same value.

`VERIFY: the documented character set and maximum length of `requestId` on `spaces.create` /
`spaces.setup`, and its deduplication window` — the notes name the parameter
(`notes_research_gaps.md:631`) but no agent fetched a format or a retention period for it, and
"exactly-once provisioning for free" is only true within whatever window Google honours.
**Settle:** read the `spaces.create` and `spaces.setup` references; if no window is documented, do not
rely on `requestId` alone — the `unique(gchat_space_name)` and `unique(linked_doctype,
linked_document)` constraints (§F.4.2) plus a `spaces.list` reconciliation are the real guarantee.
**Blocks:** nothing, because the constraints already carry it; it decides only whether a duplicate
space is *prevented* or *detected*.

#### G.2.4 The upsert primitive

`spaces.messages.patch` accepts **`allowMissing`**: *"If `true` and the message isn't found, a new
message is created and `updateMask` is ignored"* (`notes_google_verify.md:937-942`). Combined with
the `client-` id, this is a genuine documented **upsert keyed on an ERPNext identifier** —
`notes_google_verify.md:1400-1401` names it as the right pattern for a sync engine.

**Use it for `Message Update`, not for `Message Create`.** An update job that runs before its create
job (which the FIFO forbids, but which a manual replay could produce) then creates the message rather
than 404-ing — the `allowMissing` upsert is §G.8's *Create-Before-Edit* rule's safety net, not its
mechanism. `Message Create` uses `messages.create` with `messageId`, because that is the only path
that can also set `messageReplyOption` and `createMessageNotificationOptions`.

---

### G.3 Echo suppression

Every inbound message must be classified as **ours coming back** or **genuinely new** before anything
is written. Getting this wrong duplicates every message the system sends.

#### G.3.1 The check

```python
def classify_inbound(space: str, resource_name: str, msg: dict) -> str:
    room = _room_for_space(space)                     # unique(gchat_space_name) probe
    if room is None:
        return "IGNORE"                               # a space we do not mirror

    # 1. Structural dedupe: have we already stored this exact Google message?
    if frappe.db.exists("Chat Message", {"gchat_message_name": resource_name}):
        return "DUPLICATE"                            # redelivery, or a reconciliation replay

    # 2. Echo: does it carry OUR client-assigned id?
    cid = msg.get("clientAssignedMessageId")
    if cid and cid.startswith(CLIENT_PREFIX):
        existing = frappe.db.get_value(
            "Chat Message", {"room": room, "client_message_id": cid}, "name")
        if existing:
            _bind(existing, resource_name, msg)       # first time we learn the resource name
            return "ECHO"
        # a client- id we minted but cannot find: alarm, do not guess
        _alarm("chat_echo_orphan", room=room, client_message_id=cid)
        return "ECHO_ORPHAN"

    return "NEW"
```

`DUPLICATE` and `ECHO` are both **success**. So is the `DuplicateEntryError` that a racing insert
raises against `unique(gchat_message_name)` — R02's instruction, at
`notes_research_gaps.md:1119`, is explicit: *"Design the relay to treat `DuplicateEntryError` as
success."* That is what makes dedupe **structural rather than procedural**: a new inbound code path
cannot forget to check, because the database checks.

#### G.3.2 The fallback for inbound events with no client id

An inbound message legitimately has no `clientAssignedMessageId` when a human typed it in the native
Chat client, or when another Chat app posted it. Those are `NEW` and need no fallback.

The dangerous case is **our own message arriving back without one**, which can only happen if the
relay posted without `messageId`. **That is a bug, and the design makes it structurally impossible**:
`client_message_id` is `reqd` on `Chat Message`, generated in `before_insert` before any relay is
attempted, and the transport refuses to call `messages.create` without it (asserted by the same
source-level test as §G.1.7).

The fallback therefore exists only for the residue — a message posted by an older build, a manual
API call during setup, or an import-mode backfill (§G.11), which cannot set `messageId` for a
different sender. It is a **bounded, logged, alarming heuristic, never a silent path**:

```
IF   sync_origin would be "Google Chat"
AND  msg.sender resolves to an ERPNext User U
AND  an ERPNext Chat Message exists in this room with sender = U,
     sync_state IN ("Pending", "Failed"),
     created within ECHO_WINDOW (default 120 s) of msg.createTime,
     and sha256(text_plain) == sha256(normalised inbound text)
THEN bind it as an ECHO, write a "chat_echo_heuristic" audit line, and increment a counter
ELSE treat as NEW
```

Three properties that keep it honest: it is **time-bounded** (a 120-second window, so it cannot bind
an old message); it requires an **exact normalised body hash**, not a similarity; and every firing is
**counted and alarmed**, so a nonzero rate is an incident, not a steady state. The alternative — a
substring or similarity heuristic — is exactly the shape that already produced a live defect
elsewhere in this stack, where a source-filter `in` test makes *"Pond A"* match inside *"Pond Alpha"*
(`notes_gap_report.md` §B-3). We do not repeat it.

---

### G.4 Inbound — two entirely different mechanisms

**Conflating these is the classic failure, so state the difference first.** From
`notes_google_verify.md:1189-1207`, quoting
<https://developers.google.com/workspace/chat/events-overview>:

| | Chat app **interaction events** | **Workspace Events** subscriptions |
|---|---|---|
| Fires when | a user interacts with **your app** — DMs it, @mentions it, uses a command | **any** change to a subscribed resource |
| Delivery | HTTPS to your endpoint **or** Pub/Sub, per app config | **Pub/Sub only** — `notificationEndpoint` offers *only* `pubsubTopic` |
| Response | **synchronous, ≤ 30 s**, can post back into the space | fire-and-forget; you ack the Pub/Sub message |
| Coverage | only interactions aimed at the app | everything in the target resource |
| Configured in | the Chat API Configuration page in the Cloud console | `subscriptions.create` |

**Interaction events are the @triton path and nothing else.** The `MESSAGE` trigger is documented as
*"@mentions the Chat app or uses a slash command"* (`notes_google_verify.md:1214`, `:1222-1225`): in a
DM with the app every message reaches it; in a space, only mentions and commands. **They are not the
coworker firehose and cannot be made into one.**

#### G.4.1 Inbound A — interaction events over **HTTPS**, for @triton

**Transport: HTTP endpoint URL.** Configured under *Connection settings → HTTP endpoint URL* on the
Chat API Configuration page (`notes_close_google.md` §3.5b, quoting the GCF quickstart).

**Why HTTPS and not Pub/Sub — and the two research documents disagreed here.** R04 §10 recommends
**Pub/Sub pull for everything**, run *"as a supervisor/systemd process alongside the bench workers,
not as a `scheduler_events` cron — streaming pull wants a persistent connection"*
(`notes_research_gaps.md:1178`). `notes_close_google.md` §3.5b recommends **HTTP for interaction
events and Pub/Sub for Workspace Events**. **We follow `notes_close_google.md`, and it means running
two transports.** The justification, in order of weight:

1. **Latency is user-visible on this path and only this path.** @triton is a person waiting for an
   answer. Under Pub/Sub with a Frappe-scheduled puller, the floor on response time is the scheduler
   interval — *"which Frappe's scheduler makes lumpy"* (`notes_close_google.md` §3.5b). A bot that
   answers on the next tick is a bad bot. Under HTTP the request arrives immediately.
2. **The 30-second synchronous-response affordance exists on the interaction path**
   (`notes_google_verify.md:1227-1231`, CONFIRMED verbatim: *"To respond synchronously, a Chat app
   must respond within 30 seconds, and the response must be posted in the space where the interaction
   occurred"*). It lets the handler return an immediate acknowledgement **in-line**, without spending
   a per-space write token on it.
3. **Pub/Sub is mandatory for Inbound B regardless**, so choosing HTTP here does not introduce
   Pub/Sub as a new dependency — it only declines to route a second thing through it. The cost of the
   second transport is therefore **one JWT verifier** (§G.6), not a second piece of infrastructure.

**The cost of the choice, stated:** a public `allow_guest=True` endpoint exists, and §G.6 is the only
thing between it and an open relay. `notes_close_google.md` §3.5b also records the escape hatch: if
the endpoint ever terminates on Cloud Run, granting `roles/run.invoker` to
`chat@system.gserviceaccount.com` offloads the whole check to the platform.

`VERIFY: whether the 30-second synchronous-response affordance survives Pub/Sub delivery of
interaction events` — `notes_google_verify.md:1191-1195` attributes "synchronous, ≤30s" to
interaction events as a class while noting delivery may be HTTP *or* Pub/Sub, and no page read this
session says what happens under Pub/Sub. **Settle:** configure a throwaway app in Pub/Sub mode and
observe whether any inline response channel exists. **Blocks:** nothing under the HTTP recommendation
— it would only matter if the human later prefers a single transport.

**The handler does the absolute minimum**, per R02 §3.3 (`notes_research_gaps.md:1176-1177`):

```python
@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)                  # frappe.rate_limiter.rate_limit — see G.6
def chat_interaction():
    claims = verify_chat_request()                  # G.6 — raises -> 401, nothing else runs
    body = json.loads(frappe.request.get_data())
    ev = frappe.get_doc({
        "doctype": "Chat Inbound Event", "transport": "Interaction",
        "event_type": body.get("type"), "gchat_space_name": _space_of(body),
        "payload": json.dumps(body), "received_at": frappe.utils.now_datetime(),
        "status": "Received",
    }).insert(ignore_permissions=True)
    frappe.enqueue("erpnext_enhancements.chat.inbound.process_event",
                   queue="long", enqueue_after_commit=True,
                   job_id=f"chat-inbound-{ev.name}", deduplicate=True, event=ev.name)
    return _ack_card()                              # immediate, inline, no write token spent
```

**Two hard reasons the real work is never done inline**, and both are measured:

- The Chat deadline is 30 seconds (`notes_google_verify.md:1227-1231`).
- The **GCLB backend timeout is 30 seconds** — Terraform never sets `timeout_sec`
  (`infra/configs/load_balancer.yaml:33-39`) and GCP's default is 30 s
  (`notes_infra.md:792-901`). A Frappe request that touches Triton, QuickBooks or Drive can exceed
  it, and this repo already has the scar: the existing Triton SSE relay is a plain HTTP response, so
  *"the 30 s backend timeout applies as a total request→response budget and long answers are
  truncated without an error"* (`notes_gap_report.md` §A-4).

So: **ack immediately with a card, do the work in a background job, then `spaces.messages.patch` the
card** (`notes_google_verify.md:1233-1236`). R04's version of the same instruction is blunter:
*"Do not attempt a synchronous LLM call in the webhook"* (`notes_research_gaps.md:1177`).

#### G.4.2 Inbound B — the coworker firehose over **Pub/Sub, pull**

**There is no choice of transport here.** `NotificationEndpoint` offers **only** `pubsubTopic`
(`notes_google_verify.md:404-410`): *"Pub/Sub is the only delivery mechanism listed. There is no
HTTPS-webhook option for Workspace Events subscriptions."* This is a hard architectural constraint —
inbound coworker sync requires Google Cloud Pub/Sub infrastructure, not a Frappe endpoint.

**Subscription shape — B, per user, `spaces/-`.** The three available shapes
(`notes_google_verify.md:512-517`):

| Shape | Target | Auth | Coverage | Status |
|---|---|---|---|---|
| A. per-space | `//chat.googleapis.com/spaces/{space}` | app auth **+ admin approval** (`chat.app.messages.readonly`) | only spaces the app is in; one subscription per space | GA |
| **B. per-user, all spaces** | `//chat.googleapis.com/spaces/-` | **user auth only** (DWD counts) | all spaces *that user* is in; **one subscription per coworker** | **GA — chosen** |
| C. customer-level | `//admin.googleapis.com/customers/{customer}` | app auth + admin approval (`chat.app.all.messages.readonly`) | the whole org, one subscription | **Developer Preview + Enterprise SKU** |

**B is chosen** because it is the only **GA** way to see coworker messages in spaces the app is not a
member of, and because A's `chat.app.*` scopes hit a documented wall: the only documented approval
path for them runs through **Google Workspace Marketplace admin-install of a published app**, and the
page explicitly says nothing about private/unlisted apps (`notes_close_google.md` §3.5e). `chat.bot`
requires no administrator approval and is all the app identity needs. **`chat.app.*` is therefore out
of scope for V1 as a decision with a reason, not as an oversight.**

The cost of B is **one subscription per coworker** — at the measured roster ~20, not 50
(`notes_register_reconciled.md` C4) — each needing renewal and each dying independently if that
person revokes their grant (`USER_SCOPE_REVOKED`).

**Configuration: `payloadOptions.includeResource: false`, `ttl` omitted (= max), 7-day expiry.**

```json
{
  "targetResource": "//chat.googleapis.com/spaces/-",
  "eventTypes": ["google.workspace.chat.message.v1.created",
                 "google.workspace.chat.message.v1.updated",
                 "google.workspace.chat.message.v1.deleted"],
  "notificationEndpoint": { "pubsubTopic": "projects/PROJECT/topics/TOPIC" },
  "payloadOptions": { "includeResource": false }
}
```

Four independent reasons for `includeResource: false`, all verified
(`notes_google_verify.md:247-271`, `:326-328`, `:1387-1390`; `notes_close_google.md` §4.2):

1. It is the **only** configuration with a **7-day** ceiling. With resource data it is 4 hours, or 24
   hours with DWD — and note that 24 h raises the *include-resource* ceiling, **not** the 7-day one.
   (`notes_register_reconciled.md` C5 corrects a research claim here: DWD buys **no** TTL benefit
   under the chosen configuration, so the DWD case rests on attribution alone, exactly as
   `DECISIONS.md` D3 argues it.)
2. **Both lifecycle reminders can fire.** They are sent *"12 hours and one hour before the expiration
   time"* — on a 4-hour TTL the 12-hour reminder **can never fire**.
3. Renewal becomes ~20 `subscriptions.patch` calls **per week** instead of ~20 every two hours,
   leaving enormous headroom under the 600 writes/minute Workspace Events quota even during a retry
   storm.
4. The extra `spaces.messages.get` per event fits the 3000-reads/60 s project quota with room to
   spare.

**Delivery: a pull subscription.** Google's own quickstart says *"Create a **pull** subscription to
the topic"* (`notes_close_google.md` §3.6a). The publisher principal is
**`chat-api-push@system.gserviceaccount.com`** with `roles/pubsub.publisher`, **the same principal
for both the interaction topic and the Workspace Events topic** (`notes_close_google.md` §3.6a-b,
CONFIRMED on two Google pages). **Two topics, not one** — interaction events are latency-sensitive
and low-volume, the firehose is bursty, and a poison message on one must not stall the other.

**The puller.** R04's recommendation is a supervisor/systemd process alongside the bench workers,
because *"streaming pull wants a persistent connection"* (`notes_research_gaps.md:1178`). That is a
new process class on a host that today runs bench under systemd, so the ADR records the trade rather
than assuming it:

- **Recommended: a long-running pull worker** started by the bench Procfile/systemd, doing exactly
  what §G.4.1's handler does — write a `Chat Inbound Event` row, ack, enqueue. It must be idempotent
  (`unique(pubsub_message_id)`, §F.10.1) because Pub/Sub is at-least-once, and it must survive the
  deploy restart, which it does by re-pulling unacked messages.
- **Fallback: a `cron` scheduler job doing a bounded synchronous pull** every minute. Simpler to
  operate, adds up to a minute of latency to coworker messages — acceptable for the mirror, and it is
  the shape to ship first if the process-management work is not ready.

`VERIFY: the Pub/Sub message-retention window for unacked messages on the events subscription` — not
covered by any note this session. **Settle:** `gcloud pubsub subscriptions describe` and read
`messageRetentionDuration`. **Blocks:** the precise statement in §G.8 rule 4 of how long the inbound
side can be down before the Pub/Sub path (as opposed to the 28-day `spaceEvents.list` backfill) loses
data.

`VERIFY: whether a DWD-impersonated user token can create a `spaces/-` subscription at all` — the docs
say the target *"Only supports user authentication"* and DWD **is** user authentication
(`notes_google_verify.md:204-209`), but no page states the combination.
**Settle:** one `subscriptions.create` with `validateOnly: true` using an impersonated token.
**Blocks: shape B entirely** — this is the single most important call to make before Phase 1 commits
to the inbound design.

`VERIFY: whether an undocumented cap on concurrent Workspace Events subscriptions exists` — the docs
publish rate limits (600 writes/min per project, 100/min per user) and are **explicitly silent** on a
count cap; the only documented count cap applies to *customer-level* subscriptions, which are a
different type (`notes_close_google.md` §4.2). **Settle cheaply and early:** create all pilot
subscriptions and confirm `subscriptions.list` returns them all `ACTIVE`. **Blocks:** shape B at full
org size — and both fallbacks (A needs the Marketplace-only `chat.app.*` approval, C needs Developer
Preview plus an Enterprise SKU) are expensive, so discovering this at 5 users is much better than at
20.

`VERIFY: which scope authorizes `google.workspace.chat.message.v1.deleted`` — the event type exists in
the event-type table and in the `SpaceEvent` resource, but **does not appear as a row in the
scopes-by-event-type table** (`notes_google_verify.md:487-509`, which declined to guess; the
reconciliation agent's C1 confirms the sibling declined to close and carried it as deferred).
**Settle:** one `subscriptions.create` with `validateOnly: true`, `eventTypes:
["…message.v1.deleted"]`, holding only `chat.messages.readonly` — success proves the message scopes
cover it, `PERMISSION_DENIED` proves otherwise. **Blocks: the inbound delete path (§G.7).**

---

### G.5 Subscription lifecycle — renewal, alerting, reconciliation

#### G.5.1 The TTL numbers, as VERIFIED — not the confused triple the research carried

`notes_research_gaps.md:384-386` carries 7 days / 4 hours / 24 hours as three mutually confusing
figures flagged `R01-V09 / R01-G03 / R04-V13`. `notes_google_verify.md:243-271` settled them by
quoting Google's authoritative table verbatim, and **the research was right on all three numbers and
wrong about how they relate**:

| Configuration | Maximum expiry |
|---|---|
| `payloadOptions.includeResource: false` — payload carries only the resource **name** | **up to 7 days** |
| `includeResource: true`, no DWD | up to 4 hours |
| `includeResource: true`, **with** DWD | up to 24 hours |

**The 24-hour figure raises the *include-resource* ceiling. It is not a raise of the 7-day figure.**
Under our chosen `includeResource: false` configuration, DWD buys **no** TTL benefit
(`notes_register_reconciled.md` C5).

Two mechanics that make the renewal job simple:

- **`ttl` is input-only**: *"If unspecified or set to `0`, uses the maximum possible duration."* So we
  never hard-code 4 h / 7 d / 24 h — we ask for the max and **read back `expireTime`**, which is
  *"Always displayed on output, regardless of what was used on input."*
- **Renewal is `subscriptions.patch`** (*"Updates or renews a Google Workspace subscription"*),
  updating `expireTime` / `ttl` / `eventTypes`, with a `validateOnly` flag for dry runs.

`VERIFY: the exact `ttl` string format accepted (`"604800s"`? a Duration?)` — the reference says
`ttl` is a duration string but `notes_google_verify.md:452-455` saw no literal example. **Settle:** one
`validateOnly: true` create call. **Blocks:** nothing — omitting `ttl` asks for the max, which is what
we want anyway.

#### G.5.2 The renewal scheduler

```python
# scheduler_events: hourly
def renew_chat_subscriptions():
    if settings.pause_inbound: return
    horizon = frappe.utils.now_datetime() + timedelta(
        seconds=settings.subscription_renew_before_seconds)      # default 86400 = one day early
    for sub in _tracked_subscriptions():                          # one per coworker
        if sub.state == "SUSPENDED":
            _reactivate(sub);  continue                           # G.5.4
        if sub.expire_time <= horizon:
            _patch_ttl_to_max(sub)                                # subscriptions.patch
            _record(sub, read_back_expire_time=True)
```

Three properties:

- **Renew a full day early** on a 7-day TTL. The two lifecycle events — sent *"12 hours and one hour
  before the expiration time"*, with CloudEvent types
  `google.workspace.events.subscription.v1.expirationReminder` and `...v1.expired` — are the
  **backstop**, not the trigger. A design whose renewal depends on receiving a push is a design that
  fails exactly when push is broken.
- **Read back `expireTime` after every patch** and store it. A patch that silently did not extend is
  otherwise invisible until expiry.
- **Hourly, not daily.** A daily job that fails twice has burned two of the seven days.

#### G.5.3 Alerting — three distinct alarms, because they have three distinct causes

| Alarm | Condition | Why it is separate |
|---|---|---|
| `chat_subscription_expiring` | any tracked subscription with `expireTime` inside 24 h that the renewal job has failed to extend | the renewal job is broken |
| `chat_subscription_suspended` | any subscription in `SUSPENDED` | a **per-user** problem — most often that person revoked a grant |
| `chat_subscription_missing` | a coworker in the roster with **no** `ACTIVE` subscription | the create path never ran, or the subscription was expired and permanently deleted |

The third alarm exists because **expiry is permanent and unrenewable**: *"After a subscription
expires, the Google Workspace Events API permanently deletes it, and you can't renew or reactivate
it"* — recovery is `subscriptions.create` of a **new** subscription
(`notes_google_verify.md:292-301`). A renewal job that only ever patches will never notice that there
is nothing left to patch.

#### G.5.4 Suspension is a separate failure mode from expiry, and it does **not** pause the clock

`suspensionReason` values, quoted (`notes_google_verify.md:348-368`): `USER_SCOPE_REVOKED`,
`RESOURCE_DELETED`, `USER_AUTHORIZATION_FAILURE`, `ENDPOINT_PERMISSION_DENIED`, `ENDPOINT_NOT_FOUND`,
`ENDPOINT_RESOURCE_EXHAUSTED`, `APP_SCOPE_REVOKED`, `APP_AUTHORIZATION_FAILURE`. Reactivation is
`subscriptions.reactivate()`, and critically: *"Reactivated subscriptions maintain the original
expiration date."*

So a suspension does not buy time. On a 7-day TTL that is survivable; on a 4-hour TTL a five-hour
suspension is fatal — one more reason for the 7-day configuration.

**`USER_SCOPE_REVOKED` is not a bug and must not be retried into the ground.** It means a coworker
revoked their grant. The correct handling is: stop retrying that subscription, mark the person's
mirror state, raise `chat_subscription_suspended` naming them, and let a human ask. **~20
subscriptions are ~20 independent revocation surfaces** (`notes_close_google.md` §4.2) — which is why
the design needs a monitored `subscriptions.list` sweep and not just a renewal job.

**Note the docs' silence, honestly:** *"The docs do NOT state how long a subscription may remain
suspended before permanent deletion"* (`notes_google_verify.md:369-371`). No number is invented here.

#### G.5.5 The reconciliation sweep — and what it does to the consequence of a missed renewal

This is the part that changes the risk statement, and it must be stated correctly rather than
dramatically.

**`spaces.spaceEvents.list` retains 28 days**: *"You can list events that occurred up to 28 days ago.
If unspecified, lists space events from the past 28 days"*
(<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.spaceEvents/list>, quoted
at `notes_google_verify.md:335-341`), with a filter syntax that supports
`eventTypes:"google.workspace.chat.message.v1.created"`.

**The real consequence, stated precisely:**

> **With the sweep, a missed renewal costs latency and not data — recoverable for up to 28 days.
> Without the sweep, a missed renewal is silent, permanent data loss from the moment of expiry.**

`notes_google_verify.md:330-346` puts it the same way and it is worth being exact about the
conditional: the 28 days is a property of *Chat's retention*, not of our subscription. It is
available whether or not the subscription lived. What the sweep does is *use* it.

```python
# scheduler_events: cron, every 30 minutes, offset off the relay sweeper
def reconcile_chat_spaces():
    if settings.pause_inbound: return
    for room in _mirrored_rooms_bounded(limit=50):        # bounded, like every house sweeper
        since = room.last_reconciled_at or (now - 27 days)   # stay inside the 28-day window
        for ev in spaces_spaceEvents_list(room.gchat_space_name, filter=_types_filter(), since=since):
            _land_as_inbound_event(ev)      # unique(pubsub_message_id) is empty here; dedupe
                                            # falls to unique(gchat_message_name) — G.3.1
        _set(room, "last_reconciled_at", now, update_modified=False)
```

Two design notes:

- **The sweep and the push path converge on the same dedupe.** A replayed event and a pushed event
  produce the same `gchat_message_name`, so the second insert is a `DuplicateEntryError` treated as
  success (§G.3.1). This is exactly what "structural rather than procedural" buys: the reconciliation
  sweep needed no dedupe logic of its own.
- **`last_reconciled_at` must never drift past 27 days**, or the window silently opens a hole. The
  `chat_reconcile_stale` alarm fires if any mirrored room's `last_reconciled_at` is older than 7 days.

R01's own framing of the same safety net, at `notes_research_gaps.md:1170`, is a `messages.list`
sweep filtered on `createTime >` the last successful watermark; `spaceEvents.list` is strictly better
because it also carries **updates and deletes**, which `messages.list` cannot express.

`VERIFY: whether `spaces.spaceEvents.list` has its own quota` — it appears in **no** quota table
(`notes_google_verify.md:1036-1037`). **Settle:** re-read the limits page at implementation time, and
watch for `RESOURCE_EXHAUSTED` under load. **Blocks:** how aggressively the sweep may run; the
30-minute bounded design is conservative precisely because this is unknown.

`VERIFY: whether the Workspace Events `message.v1.deleted` payload carries `deletionMetadata`` — i.e.
whether the push path can attribute a delete without a follow-up read
(`notes_google_verify.md:946-951`). **Settle:** delete a message in a subscribed test space and inspect
the Pub/Sub payload. **Blocks:** the delete-attribution half of §G.7.

---

### G.6 Request authenticity — JWT verification only, never an IP allowlist

The interaction endpoint is `allow_guest=True`. **This check is the only thing between the webhook
and an open relay.**

#### G.6.1 Why an IP allowlist is not an option — and why that is fine

Three independent negatives, all established this session (`notes_close_google.md` §4.1):

1. **The Chat verification page does not mention IP at all.** Asked directly for any sentence about
   IP addresses, ranges, allowlisting or firewalls: *"The page contains no information about IP
   addresses, IP ranges, allowlisting, or firewall configuration."* Its entire authenticity story is
   the bearer token.
2. **Google publishes exactly two IP-range files, both org-wide** (`goog.json`, `cloud.json`), with
   no per-service breakdown, and states that *"The default domain IP ranges used by Google APIs and
   services are allocated dynamically and **change often**."*
3. A targeted search across `developers.google.com`, `support.google.com`, `cloud.google.com` and
   `gstatic.com` for Chat webhook egress ranges returned nothing.

**The sentence for the security review:**

> An IP allowlist is not available as a control for inbound Google Chat requests. Google publishes no
> Chat-specific egress range; the only published ranges are org-wide and are documented as *"allocated
> dynamically and change often"*. Allowlisting `goog.json` would admit every Google service and every
> Google customer's Cloud resources, which is **weaker than no control at all** in the threat model
> that matters — because any GCP project can mint a valid OIDC ID token for our audience. The control
> is the JWT, verified on **all four** of signature, `aud`, issuer identity and `email_verified`. Pinning
> the issuer to `chat@system.gserviceaccount.com` is strictly **stronger** than a network allowlist,
> because it authenticates *who* rather than *where from*, and Google guarantees that identity in both
> audience modes.

#### G.6.2 The trap: a generic ID-token verification is NOT sufficient

Quoted from <https://developers.google.com/workspace/chat/verify-requests-from-chat> via
`notes_google_verify.md:1285-1297`:

> For ID token verification, validate that the token includes `"email_verified"` and the email equals
> `"chat@system.gserviceaccount.com"`. For Project Number JWTs, verify the issuer is
> `chat@system.gserviceaccount.com` and the audience matches your project number.

**A stock `google.oauth2.id_token.verify_oauth2_token(...)` call checks the signature and the `aud`.
It does *not* check that the `email` claim is `chat@system.gserviceaccount.com`.** Any Google service
account in the world can mint a valid OIDC ID token for our audience. Without the explicit identity
check, an attacker with **any** GCP project can forge "requests from Chat" against our endpoint — and
because the endpoint writes to the message store, that is message injection into a company's
conversation record.

Two audience modes exist (`notes_google_verify.md:1266-1272`), and the issuer identity is
`chat@system.gserviceaccount.com` in **both**:

| Mode | Token type | `aud` |
|---|---|---|
| **HTTP endpoint URL** (Google's recommended, and ours) | OpenID Connect **ID token** | the configured endpoint URL, byte-for-byte |
| Project number | **self-signed JWT** | the Cloud project number |

#### G.6.3 The verification, at code level

```python
CHAT_ISSUER = "chat@system.gserviceaccount.com"

def verify_chat_request() -> dict:
    """Four checks. Any failure -> 401 with no detail. This is the entire security boundary."""
    header = frappe.get_request_header("Authorization") or ""
    if not header.startswith("Bearer "):
        _reject("missing bearer")
    token = header[len("Bearer "):].strip()

    settings = frappe.get_cached_doc("Chat Settings")
    audience = settings.interaction_endpoint_url          # MUST match the console byte-for-byte

    try:
        # (1) signature against Google's keys  +  (2) aud  +  exp
        from google.oauth2 import id_token
        from google.auth.transport import requests as ga_requests
        claims = id_token.verify_oauth2_token(token, ga_requests.Request(), audience=audience)
    except Exception:
        _reject("signature or audience")

    # (3) ISSUER IDENTITY -- the check the stock verifier does NOT perform.
    #     Without this line, any GCP project on earth can forge a request to this endpoint.
    if claims.get("email") != CHAT_ISSUER:
        _reject("issuer identity")

    # (4) the claim must be asserted as verified
    if claims.get("email_verified") not in (True, "true"):
        _reject("email_verified")

    return claims


def _reject(reason: str):
    frappe.log_error(f"Chat webhook rejected: {reason}", "Chat Inbound Auth")   # no token, no body
    raise frappe.AuthenticationError                                             # -> HTTP 401
```

Rules attached to this function, each with a reason:

- **`google.oauth2` and `google.auth.transport.requests` are importable in production** — measured:
  `google.oauth2.service_account` and `googleapiclient.discovery` both import on the prod bench
  (`notes_infra.md:990-1005`). This is not a new dependency.
- **Return 401 on any failure, with no detail in the body.** Google's own instruction: *"If token
  verification fails, your service should respond to the request with an HTTPS response code `401
  (Unauthorized)`."*
- **Never log the token, and never log the request body on a rejection.** A rejected body is
  attacker-controlled, and this repo has already been bitten by frame locals leaking to the Error Log.
- **The audience is `Chat Settings.interaction_endpoint_url`, and it must match the Cloud console
  entry byte-for-byte** — trailing slash included. A mismatch presents as universal 401s that look
  like a signing problem.
- **Stack `@rate_limit` from `frappe.rate_limiter`.** It is `frappe.rate_limiter.rate_limit`, **not**
  `frappe.rate_limit` — the latter does not exist and importing it is an import-time failure this repo
  has already made (`tests/test_fountain_move.py:15-16`). There is also a source-level test in this
  repo asserting *"a guest endpoint is missing its rate limiter"* by regex
  (`tests/test_contract_esign.py:526-534`), so the chat endpoint must satisfy it. Note its dimension
  is the **IP**, which is spoofable — it is a cost control, not the security boundary. The JWT is the
  boundary.
- **Verification is a pure function of `(token, audience, clock)`, so it is bench-free testable** —
  and R02's test tiering puts *"webhook signature verification"* explicitly in the tier that blocks
  the PR (`notes_research_gaps.md:1187`).

**The escape hatch, recorded:** *"Cloud Run and Cloud Functions automatically handle verification when
you authorize `chat@system.gserviceaccount.com` as an invoker via IAM"*
(`notes_google_verify.md:1308-1318`). If the endpoint ever terminates on Cloud Run, granting
`roles/run.invoker` to that principal offloads the whole check to the platform — *"materially safer
than hand-rolling JWT verification in Frappe"*. It is not the V1 shape, because the endpoint lives in
the Frappe app, but it is the first thing to reach for if this code ever becomes a maintenance
burden.

`VERIFY: whether the current Google page names the JWK endpoint or the x509 endpoint as canonical` —
the page fetched this session surfaced
`https://www.googleapis.com/service_accounts/v1/metadata/x509/chat@system.gserviceaccount.com`; the
JWK form was seen only in secondary sources (`notes_google_verify.md:1274-1283`). **Settle:** re-read
the page. **Blocks:** nothing under the `verify_oauth2_token` path, which fetches Google's keys
itself; it matters only if the verifier is ever hand-rolled.

---

### G.7 Edit and delete, both ways

#### G.7.1 The ownership rule that decides the identity of every edit and delete

Quoted verbatim (`notes_google_verify.md:912-922`):

> `spaces.messages.delete` — *"When using app authentication, requests can only delete messages
> created by the calling Chat app."*
> `spaces.messages.patch` — *"When using app authentication, requests can only update messages created
> by the calling Chat app."*

**Therefore: editing or deleting a human's message requires DWD impersonation of that author** — or of
a space manager, which the `SPACE_OWNER_VIA_APP` deletion type implies is a supported route
(§G.7.4). This is not a preference; it is the API's ownership model, and it means the identity used
for an edit is **the identity that created the message**, recorded on `Chat Relay Job.impersonate_user`
so a retry cannot drift to a different principal.

One consequence worth stating plainly: **@triton's own messages are the only ones the app identity can
edit**, which is exactly what the "ack card, then patch the card" pattern of §G.4.1 needs. Under
`DECISIONS.md` D3's split, that is the one place app auth is used and it is sufficient there.

#### G.7.2 Outbound edit — `patch` semantics and `allowMissing`

```python
def relay_message_update(job):
    msg = frappe.get_doc("Chat Message", job.reference_name)
    body, truncated = fit_to_byte_budget(msg)              # G.10
    with dwd_as(msg.sender):                               # D3: the authoring human
        patch(
            name = msg.gchat_message_name or _alias_name(msg),   # spaces/{s}/messages/{client-id}
            updateMask = "text",
            allowMissing = True,                                  # G.2.4
            body = {"text": body},
        )
```

Facts this encodes (`notes_google_verify.md:924-944`):

- **`patch`, not `update`.** *"The `patch` method uses a `patch` request while the `update` method
  uses a `put` request."* `patch` is the recommended one.
- **`updateMask` accepted paths** are `text`, `attachment`, `cards` (app auth), `cardsV2` (app auth),
  `accessoryWidgets` (app auth), `quotedMessageMetadata` (removal only). Under DWD/user auth the only
  mask we may use is **`text`** — user auth supports *"only text (`text`)"* at GA
  (`notes_google_verify.md:1065-1066`).
- **`allowMissing: true`** — *"If `true` and the message isn't found, a new message is created and
  `updateMask` is ignored."* It requires a client-assigned id, which §G.2.2 guarantees exists on every
  row. This turns an out-of-order or replayed edit into a create rather than a 404.
- **The message can be addressed by its alias** `spaces/{space}/messages/{clientAssignedMessageId}`
  (the reference states the substitution explicitly, `notes_close_google.md` §1.1), so an edit does not
  strictly require that `gchat_message_name` was ever written back. That is the property that makes
  `allowMissing` genuinely useful rather than decorative.

#### G.7.3 Outbound delete

```python
def relay_message_delete(job):
    msg = frappe.get_doc("Chat Message", job.reference_name)
    with dwd_as(msg.sender):
        delete(name=msg.gchat_message_name or _alias_name(msg), force=True)
```

- Scopes for message delete are `chat.bot`, `chat.import`, `chat.messages` — **`chat.delete` is NOT
  among them.** `https://www.googleapis.com/auth/chat.delete` exists and gates deleting a **space**,
  not a message (`notes_google_verify.md:878-900`). The brief's suspicion was half right and the half
  matters: nothing in the message path needs the restricted scope.
- **`force`**: *"When `true`, deleting a message also deletes its threaded replies. When `false`, if a
  message has threaded replies, deletion fails."* We pass `true`, because ERPNext has already decided
  the delete and a relay that half-fails leaves the two sides disagreeing — which is the one thing
  decision #2 forbids. The ERPNext-side consequence (replies to a deleted root remain, with a
  "deleted" placeholder root) is a rendering decision, not a sync decision.
- **`spaces.delete` always cascades** — *"the space's child resources—like messages posted in the
  space and memberships in the space—are also deleted"* (`notes_google_verify.md:902-905`). **Named
  rule: no code path in `erpnext_enhancements` calls `spaces.delete` without an explicit human
  confirmation step.** It is a foot-gun that deletes a company's conversation history in one call, and
  the `chat.delete`/`chat.app.delete` scopes should simply not be granted in V1.

#### G.7.4 What a delete leaves behind, and what that means for I10

Google's tombstone is rich in *metadata* and empty of *content*
(`notes_google_verify.md:837-871`):

- `deleteTime` — *"Output only. The time at which the message was deleted in Google Chat."*
- `deletionMetadata.deletionType` — `CREATOR`, `SPACE_OWNER`, `ADMIN`, `APP_MESSAGE_EXPIRY`,
  **`CREATOR_VIA_APP`**, **`SPACE_OWNER_VIA_APP`**, `SPACE_MEMBER`.
- `messages.list?showDeleted=true` — *"Deleted messages include deleted time and metadata about their
  deletion, but **message content is unavailable**."*

Two consequences, and the second is the important one:

1. **Attribution is clean.** `CREATOR_VIA_APP` is precisely what a DWD-impersonated delete on behalf
   of the author produces, and `SPACE_OWNER_VIA_APP` what an impersonated manager produces. These map
   directly onto `Chat Message.deletion_source`, so an inbound delete can be attributed without
   guessing.
2. **If ERPNext does not keep the body, nobody has it.** This is the whole argument for §F.6.5's soft
   delete, and it is what makes I10 satisfiable at all: the audit trail that must survive a
   user-facing delete can only live in ERPNext, because Google's copy of the text is gone the moment
   the delete lands. A design that hard-deleted the ERPNext row on a user delete would leave **no
   copy of the message anywhere**, on either side, and no amount of Phase 6 tooling could recover it.

#### G.7.5 Inbound edit and delete

- **`google.workspace.chat.message.v1.updated`** → locate the row by `unique(gchat_message_name)`,
  fetch current state with `spaces.messages.get` (required, because `includeResource: false` means the
  event carries only the name), apply §G.8 rule 3, then `save()` the row so `modified` advances and the
  D6 watermark invalidates the covering chunk and digest (§F.16.2 rule 2).
- **`google.workspace.chat.message.v1.deleted`** → locate the row, set `is_deleted = 1`,
  `deleted_at`, `deletion_source = "Google Chat"`, and map `deletionMetadata.deletionType` onto
  `deleted_by` where it resolves. **Retain `text`.** Then `save()`, for the same watermark reason.
- **If the row does not exist**, the update/delete is for a message we never ingested. Land it as a
  `Chat Inbound Event` with `status = Ignored` and a reason, rather than creating a phantom — with one
  exception, §G.8 rule 1's `allowMissing`-equivalent: an *update* for an unknown message is treated as
  a **create** from the fetched resource, because that is the same message arriving by a different
  door.

`VERIFY: whether DWD impersonation of a *space manager* can delete an arbitrary member's message` —
the `SPACE_OWNER_VIA_APP` enum implies it; **no page states the permission rule**
(`notes_google_verify.md:952-954`). **Settle:** one live delete. **Blocks:** whether a Phase 6
moderation action ("remove this message from Chat") is possible without impersonating its author —
which matters, because impersonating an author to delete their own message is a surprising thing to do
on someone's behalf.

---

### G.8 Ordering and conflict resolution — four **named rules**, not principles

Each rule has a name, a precise statement, the mechanism that enforces it, and its test. A principle
gets argued about in Phase 2; a named rule with a test does not.

#### Rule 1 — **CREATE-BEFORE-EDIT** (an edit arrives before its create)

> **Statement.** For any message, the operations `Message Create`, `Message Update` and
> `Message Delete` are applied in `job_seq` order within their room, in both directions. An edit can
> never be applied to a message whose create has not been applied.

**Outbound mechanism — structural.** `Chat Relay Job` carries `job_seq`, allocated per room, with
`unique(room, job_seq)` (§F.9.1). The worker takes the **lowest `Pending` `job_seq` for that room**;
if an earlier job for the same room is not `Done`, the later job is **deferred** (`available_at`
pushed forward), not failed. A room is therefore a strict FIFO, which is also what the 1-write/second
bucket wants anyway — there is no benefit to parallelism inside a space.

**Inbound mechanism — `allowMissing`-equivalent.** An `updated` event for an unknown
`gchat_message_name` is applied as a **create** from the fetched resource (§G.7.5). A `deleted` event
for an unknown message is recorded as `Ignored` — creating a row purely to mark it deleted would
manufacture a message that never existed on our side, and Google's tombstone has no body to store.

**Test.** Insert a message, immediately edit it, and run the sweeper with the create job artificially
deferred: assert the update job is deferred rather than attempted, that no `NOT_FOUND` is recorded,
and that after the create completes the update applies with the **final** text — not an intermediate.

#### Rule 2 — **FIRST-WRITER-WINS ON THE RESOURCE NAME** (the same message arrives twice)

> **Statement.** A Google message is stored at most once. The first insert to claim a given
> `gchat_message_name` wins; every subsequent attempt is discarded as success.

**Mechanism — the `unique(gchat_message_name)` index (§F.6.6).** The four paths that can deliver the
same message are: the Workspace Events push, a Pub/Sub at-least-once redelivery, the
`spaceEvents.list` reconciliation sweep replaying a 28-day window, and a manual replay of a
`Chat Inbound Event`. All four converge on the same resource name, so the second insert raises
`DuplicateEntryError`, which the relay treats as success
(`notes_research_gaps.md:1119`: *"Design the relay to treat `DuplicateEntryError` as success"*).

**This is what "structural rather than procedural" means.** No inbound code path contains a dedupe
check, so no new inbound code path can forget one.

**Test.** Feed the same captured payload through the push handler and the reconciliation sweep;
assert exactly one `Chat Message` row, exactly one realtime publish, and that the second attempt is
logged as `DUPLICATE` rather than as an error.

#### Rule 3 — **LAST-WRITER-WINS BY `lastUpdateTime`, ERPNEXT BREAKS TIES** (an ERPNext edit races a Chat edit)

> **Statement.** When both sides have edited the same message, the version with the later authoritative
> timestamp wins. Google's `lastUpdateTime` is compared against the ERPNext row's `edited_at`. **If
> they are equal, or if either is missing, ERPNext wins** — because `DECISIONS.md`/decision #1 makes
> ERPNext the source of truth, and a tie-break that favours the mirror inverts that.

**Mechanism.** `Chat Message.gchat_last_update_time` stores Google's value on every inbound touch
(§F.6.2). The inbound update handler compares:

```
if inbound.lastUpdateTime > row.edited_at:   apply inbound, set sync_origin unchanged, save()
else:                                        discard inbound, and RE-QUEUE an outbound Message Update
                                             so Chat converges on the ERPNext text
```

The `else` branch is the part people leave out. Discarding the inbound edit without re-queueing an
outbound one leaves the two sides **permanently divergent** with nothing to notice it — which is
worse than either version winning.

**Both timestamps must be compared as timezone-correct values.** Google returns RFC-3339 UTC;
`edited_at` is Frappe-written site-local (`notes_register_reconciled.md` C7). The comparison converts
explicitly. A naive comparison is wrong by six hours **in the direction that always makes Google
win**, which would silently invert decision #1.

**Test.** Construct a row edited in ERPNext at T, deliver an inbound update stamped T−1s: assert the
inbound is discarded, an outbound `Message Update` job is created, and the stored text is ERPNext's.
Then deliver one stamped T+1s: assert the inbound is applied and no outbound job is created (which
would otherwise loop forever — see §G.3's echo suppression, which is what stops it).

#### Rule 4 — **CATCH-UP-BY-SWEEPER, NOT BY QUEUE** (the relay is down for an hour)

> **Statement.** No message is lost by an outage of the relay, the workers, the queue Redis, or the
> whole VM. Outbound work is recovered from `Chat Relay Job` rows by the sweeper; inbound work is
> recovered from unacked Pub/Sub messages and, beyond that, from the 28-day `spaceEvents.list` window.
> **Recovery is bounded by rate limits, not by retry counts.**

**Outbound, with the numbers.** Rows accumulate as `Pending`. When service returns, the sweeper drains
them in `job_seq` order at **one write per second per space**. So a room that accumulated 600 messages
during the outage takes **600 seconds — ten minutes — to drain**, and drains *in order*. A room with
20 messages drains in 20 seconds. **This is the number to put in front of the human**: the mirror is
eventually consistent with a worst-case lag equal to the backlog depth in seconds, per room, and no
amount of engineering changes it because 1 write/second/space is Google's binding limit
(`notes_google_verify.md:997-1003`).

**Outbound, at the retry cap.** `attempts >= relay_max_attempts` moves a row to `Dead` and it appears
in the next daily digest (§G.1.6). `Dead` is a human-visible state, never a silent one.

**Inbound.** Unacked Pub/Sub messages are redelivered when the puller returns (retention window is
`VERIFY`, §G.4.2). Beyond that window the reconciliation sweep recovers up to **28 days**. The
practical statement: **inbound can be down for days without data loss, and the alarm that matters is
`chat_reconcile_stale`, not the puller's liveness.**

**A deploy is a special case of an outage, and the important one.** A deploy `FLUSHDB`s the queue,
kills queued jobs, and restarts every process. Under this design a deploy costs: queued relay jobs (recovered
by the sweeper within five minutes), the token buckets (fail open, §G.1.4), presence and typing
(one heartbeat, §F.14.4), and in-flight `In Progress` relay rows (returned to `Pending` by the stale
reaper, §G.1.3). **Nothing in that list is data.**

**Test.** With the transport stubbed to fail, insert 50 messages across three rooms; assert 50
`Pending` rows with contiguous per-room `job_seq`. Restore the transport, run the sweeper, and assert
every message is delivered exactly once, in per-room `job_seq` order, with no more than one write per
space per second.

---

### G.9 Threading — the exact enum, the space state it needs, and the risk to decision #5

#### G.9.1 The exact enum, and the silent failure of omitting it

Reproduced verbatim on a deliberately literal second pass (`notes_google_verify.md:626-645`, from
<https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create>):

| `messageReplyOption` | Description |
|---|---|
| `MESSAGE_REPLY_OPTION_UNSPECIFIED` | *"Default. Starts a new thread. **Using this option ignores any thread ID or threadKey that's included.**"* |
| **`REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`** | *"Creates the message as a reply to the thread specified by thread ID or threadKey. If it fails, the message starts a new thread instead."* |
| `REPLY_MESSAGE_OR_FAIL` | *"…If the message creation fails, a `NOT_FOUND` error is returned instead."* |

Parameter description, quoted: *"Specifies whether a message starts a thread or replies to one. **Only
supported in named spaces.** When responding to user interactions, this field is ignored."*

**The exact value to use is `REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD`**, with `thread.name` set from the
root message's `gchat_thread_name`. It is preferred over `REPLY_MESSAGE_OR_FAIL` because a thread that
has been deleted on the Chat side must not block the mirror — decision #1 says a message valid in
ERPNext gets relayed, and a fallback-to-new-thread is a degraded render, whereas a `NOT_FOUND` is a
lost message.

**And the omission is worse than "posts a new top-level message."** Omitting `messageReplyOption`
*actively ignores* a `thread.name` / `thread.threadKey` you supplied. **A caller who sets `thread` but
forgets `messageReplyOption` gets a silent new thread, not an error.** `notes_google_verify.md:640-643`
names this as *"a real bug shape — the API will not tell you."* **Named rule: the transport never
accepts a `thread` argument without a `messageReplyOption`; the two are one parameter object, and a
unit test asserts they cannot be separated.**

#### G.9.2 The space state it requires, and the verified problem

Three independently-verified facts stack into one constraint (`notes_google_verify.md:571-680`):

1. **`spaceThreadingState` is `Output only`** — *"Output only. The threading state in the Chat space."*
   The deprecated `threaded` field is also output-only. **You cannot request a threaded space.**
2. **`spaces.setup` states it outright**: *"Spaces with threaded replies aren't supported."*
3. **`spaces.create` says nothing about threading at all** — on a targeted literal pass, the page
   *"contains no sentence with the word 'thread' or 'threaded'."*

The enum value that gives in-line threading is **`THREADED_MESSAGES`**; `GROUPED_MESSAGES` is the
modern *"organized by topic"* mode and the docs **do not spell out whether `messageReplyOption` works
there**; `UNTHREADED_MESSAGES` obviously does not.

**So decision #5's "replies in-thread" is at genuine risk**, and the ADR says so rather than assuming
it: an API-created space may report `GROUPED_MESSAGES` or `UNTHREADED_MESSAGES`, in which case
`messageReplyOption` may do nothing at all. `notes_google_verify.md:649` also records that *"If the
space doesn't use threading, this field is ignored."*

#### G.9.3 The settlement — a five-minute live call, before any threading design is written

`notes_gap_report.md` §E ranks this **phase-blocking item #1** and
`notes_register_reconciled.md` §(b) carries it. It is two API calls:

```
1. spaces.create  ->  a throwaway named space
2. spaces.get     ->  read spaceThreadingState
   PASS if THREADED_MESSAGES.
   If GROUPED_MESSAGES:  post two messages with messageReplyOption=REPLY_MESSAGE_OR_FAIL and a
                         thread.name from the first; a NOT_FOUND or a silently-new-thread answers it.
   If UNTHREADED_MESSAGES: threading in Chat is not available on API-created spaces. Stop.
```

Record the raw result in the ADR's evidence appendix, and set
`Chat Settings.threading_enabled = 1` **only** if it passes.

#### G.9.4 The fallback if it fails, and why it is survivable

**ERPNext keeps full thread structure regardless** — `parent_message` + `thread_root` (§F.6.4) are
ERPNext-side fields that owe Google nothing. The SPA renders threads correctly whatever Chat does.

The Chat-side fallback is a **reply-context prefix**: the relayed body opens with a single line
naming the parent's author and a deep link back to the ERPNext message
(`/chat/room/<room>?message=<msg>`). It costs bytes against the 32,000-byte budget (§G.10) and it is
visibly a degradation — which is the point: it should look like a workaround, so nobody mistakes it
for the feature.

Two things **not** to reach for:
- **Silent messages do not help and actively conflict.** *"You can't start or reply to a thread with a
  silent message"* (`notes_google_verify.md:101`) — threading and `NOTIFICATION_TYPE_SILENT` are
  mutually exclusive, which is one leg of `DECISIONS.md` D3's trilemma. Under the recommended DWD
  identity, silent is unavailable anyway.
- **`quotedMessageMetadata` is not a substitute.** It appears on `messages.patch`'s `updateMask` as
  **removal only** (`notes_google_verify.md:932-936`), and nothing verified this session shows it can
  be *set* on create.

`VERIFY: whether `messageReplyOption` functions in a `GROUPED_MESSAGES` space` — carried from
`notes_google_verify.md:677-679`. **Settle:** step 2 above. **Blocks:** decision #5's in-thread
replies.

`VERIFY: the full `SpaceThreadingState` enum, including any `UNSPECIFIED` member` —
`notes_google_verify.md:592-596` declined to assert one it did not see printed. **Settle:** the RPC
reference, or one `spaces.get`. **Blocks:** only the `Select` options on
`Chat Room.gchat_threading_state`.

---

### G.10 Oversized messages — 32,000 **bytes**, whole message

#### G.10.1 The limit, exactly

CONFIRMED on two pages (`notes_google_verify.md:1014-1024`):

> *"The maximum message size (including any text or cards) is 32,000 bytes."*
> *"The maximum message size, including the message contents, is 32,000 bytes."*

Two properties that a naive implementation gets wrong:

- **The unit is bytes, not characters.** Multi-byte UTF-8 counts more than once — an emoji is four
  bytes, a curly quote is three. `len(text)` is the wrong measurement.
- **The budget is the whole message including cards**, not just the `text` field. A `cardsV2` payload
  eats the same 32 KB.

Google's own remedy is *"your Chat app must send multiple messages instead"*
(`notes_google_verify.md:1026`).

#### G.10.2 The choice, and the recommendation

**Reject at compose time, or relay a truncated body with a deep link?**

**Recommendation: relay a truncated body with a deep link back to ERPNext. Do not reject at compose
time, and do not split into multiple messages.**

| Option | Assessment |
|---|---|
| **Reject at compose time** | **Rejected.** It makes ERPNext's UX hostage to a Google limit and inverts decision #1: a message that is valid in the source of truth would be blocked by the mirror. It also fails for the inbound direction and for any message that becomes oversized only after a reply-context prefix is added. |
| **Split into N Chat messages** (Google's own suggestion) | **Rejected.** It breaks the 1:1 mapping the entire sync engine rests on. Which fragment carries `gchat_message_name`? What does an edit patch? What does a delete delete? The unique index that makes dedupe structural assumes one Google message per ERPNext message. |
| **Truncate + deep link** | **Recommended.** The Chat side becomes a notification-with-preview for the rare long message; ERPNext keeps the whole thing; edits and deletes still address one resource. |

Mechanics:

```python
SUFFIX_TEMPLATE = "\n\n[…truncated — read the full message in ERPNext: {url}]"

def fit_to_byte_budget(msg, limit=None):
    limit = limit or settings.message_byte_limit          # 32000, exposed in Chat Settings
    url   = f"{base}/chat/room/{msg.room}?message={msg.name}"
    suffix = SUFFIX_TEMPLATE.format(url=url)
    body   = _render_for_chat(msg)                        # includes any reply-context prefix (G.9.4)
    encoded = body.encode("utf-8")
    if len(encoded) + _envelope_overhead() <= limit:
        return body, False
    budget = limit - len(suffix.encode("utf-8")) - _envelope_overhead()
    cut = encoded[:budget].decode("utf-8", errors="ignore")   # never split a codepoint
    return cut + suffix, True
```

Three details that matter: the budget is computed on **encoded bytes**; the suffix's own bytes are
subtracted **before** cutting; and the decode uses `errors="ignore"` so a cut never lands mid-codepoint
and produces an invalid payload. `Chat Message.truncated_for_relay` records that it happened, so the
SPA can show "the Chat copy of this message is truncated" and the oversight view can count it.

**Human question (CQ-G1).** This is a product decision dressed as a limit. **Recommendation:
truncate + deep link, as above.** The alternative a human might legitimately prefer is *reject at
compose with a friendly "this is too long for Chat — post it as a file"*, which is defensible if the
company would rather never see a truncated message than ever see one. It is theirs to pick.

---

### G.11 Back-fill — import mode is a migration tool, never the live relay

#### G.11.1 The verified constraints

All from <https://developers.google.com/workspace/chat/import-data-overview> and siblings, via
`notes_google_verify.md:1101-1167`:

| Constraint | Verbatim / verified |
|---|---|
| Window | *"Chat apps have **90 days** to complete the import of data for a space. After 90 days, if the space is still in import mode, it's **automatically deleted and will be inaccessible and unrecoverable**."* |
| Visibility | *"Spaces in import mode are **hidden from end users**."* |
| Space types | *"Only `SpaceType.SPACE` and `SpaceType.GROUP_CHAT` are supported."* — **no DMs** |
| Members | *"Members must be users within the same domain."* |
| Memberships | *"Historical memberships **must** be imported when a space is in import mode. You **can't** import historical memberships after the space completes import mode."* |
| Backdating floor | message `createTime` *"must be set to a value between the space creation time that you previously set and present time"* — the floor is the space's own `createTime`, which you set |
| Write rate | **10 writes/sec per space** in import mode, versus 1/sec normally |
| Authorisation | `chat.import` is granted *"Only [by] Google Workspace domain administrators … to service accounts through domain-wide delegation"*, and `completeImport` requires *"user authentication and domain-wide delegation"* |
| Attachments | *"we **highly recommend** using Google Drive API to upload files and link the file URIs … instead to avoid hitting Google Chat internal limit for attachment upload"* |
| Completion | `completeImport` makes the space visible and is **irreversible** |

#### G.11.2 Why it is migration-only

Three of those constraints make import mode structurally unusable as a live relay, and any one of them
would be sufficient:

1. **A space in import mode is hidden from end users.** A live relay into it would be invisible to
   the very people it exists to reach.
2. **`completeImport` is irreversible and there is no method to re-enter import mode.** You get one
   shot per space.
3. **Historical memberships cannot be imported after completion**, so the moment a space goes live its
   membership history is frozen — which is exactly the audit surface decision #12 needs.

`notes_research_gaps.md:1172` states the same conclusion from R01 §3 and R04 §13: *"a **one-time
migration tool, not a sync mechanism** … Do not design the steady-state path around it."*
`Chat Settings.import_mode_enabled` therefore defaults to **0** and is a deliberate, temporary flip.

#### G.11.3 The two things that decide whether back-fill is possible at all

Both are unresolved, and both are one live call:

`VERIFY: that an import-mode message can be attributed to an arbitrary domain user` — i.e. that
DWD-impersonating user A produces a message whose `sender` is A, backdated. `notes_google_verify.md`
is explicit that *"the reference page does not state the attribution rule"* (`:1162-1167`) even though
it is the obvious purpose of the feature. **Settle:** create a throwaway import-mode space, post one
backdated message impersonating a test user, read it back with `spaces.messages.get` and inspect
`sender` and `createTime`. **`notes_google_verify.md:1171-1175` calls this "the single call that
settles whether historical Chat backfill is possible at all."**

`VERIFY: whether an app can import into a space it did not create` — **UNRESOLVED**; the
import-data-overview page explicitly did not address it on a targeted pass
(`notes_google_verify.md:1159-1161`). **Blocks:** whether existing human-created spaces can be
back-filled, or only new ones.

Also unresolved and worth knowing before scheduling: whether there is a **maximum backdating age**
(no absolute limit is documented), and whether a space can **re-enter** import mode after
`completeImport` (strongly implied no; no method exists).

#### G.11.4 If it goes ahead

- Run it against a **separate set of rooms** with `provisioning_mode` set explicitly, never against a
  live mirrored space.
- Use the **10 writes/sec** import-mode budget — a distinct bucket key from the steady-state 1/sec
  one, so the two cannot be confused.
- **Import memberships first**, in the same import-mode window.
- Leave **≥30 minutes of slack** before `importModeExpireTime` (R01's recommendation,
  `notes_research_gaps.md:391`).
- **Link attachments from Drive rather than uploading them**, per Google's own recommendation above.
- Treat the whole thing as `Chat Relay Job` rows so it is resumable and bounded like everything else.

---

### G.12 The deliberately lossy list — for the human to confirm, not to discover

Decision #2 promises a mirror. **A mirror is not a copy.** Every item below is a place where the two
sides will not match, each with the verified reason. **This list is a checkpoint item: the human is
asked to accept it, item by item.**

| # | What is lossy | Why, verified | Which side loses |
|---|---|---|---|
| 1 | **Messages over 32,000 bytes are truncated in Chat** | the limit is bytes and covers the whole message (`notes_google_verify.md:1014-1024`) | Chat |
| 2 | **Threaded replies may render flat in Chat** | `spaceThreadingState` is **Output only**; `spaces.setup` states *"Spaces with threaded replies aren't supported"* (`:580`, `:619`) | Chat |
| 3 | **Chat's own notification fires for anyone running the native client** | silent requires **app auth**, and app auth renders the app as sender with an `App` badge — mutually exclusive with human attribution (`DECISIONS.md` D3's trilemma) | the decision-#3 promise |
| 4 | **Relayed messages carry a visible "via \<app name\>" attribution** | *"Chat displays the Chat app name next to the user's name"* under user auth (`:1061-1070`) — DWD is **not** invisible impersonation | Chat |
| 5 | **No cards on human-relayed messages** | user auth supports *"only text (`text`)"* at GA; cards-under-user-auth exists only in **Developer Preview** (`:1065`, `:1078-1084`) | Chat |
| 6 | **Attachments require the DWD identity; app auth cannot upload** | *"Requires user authentication"*; `chat.bot` absent from `media.upload` scopes (`:714-727`) | the app-auth path |
| 7 | **Drive-sourced attachments are stored as links, not copies** | `media.download` *"Downloads uploaded media, but not Google Drive files"*; Drive ACLs are independent of the space (`:781-799`) | ERPNext |
| 8 | **Some file types cannot be uploaded at all** | *"Some file types aren't supported, and can't be uploaded"* — the blocked list was not fetched (`:753-755`) | Chat |
| 9 | **External / guest users** | forced and silent notification modes *"don't apply to external users (guests)"*; and shape-B subscriptions only cover spaces the **subscribing** coworker is in (`:99-103`, `:515`) | both |
| 10 | **Reactions do not round-trip in V1** | `reactions.create` / `.delete` are **user-auth only** (`notes_research_gaps.md:575`), so each reaction costs an impersonation and a write token | both |
| 11 | **Deleted message text exists only in ERPNext** | Google's tombstone is content-free — *"message content is unavailable"* (`:866-871`) | Chat |
| 12 | **Chat's own read state is not mirrored** | read-state subscriptions are GA since 2026-07-13 but out of V1 scope; ERPNext read state is the high-water mark of §F.15 | both |
| 13 | **Message formatting may not survive exactly** | the `markupSyntax` / `MARKUP_SYNTAX_MARKDOWN` field was announced 2026-08-07 but **did not appear on the reference page the same day** (`:1335`, `:1360-1365`) — *"Do not code against `markupSyntax` until this is settled"* | Chat |
| 14 | **Display order can differ** | ERPNext orders by `seq`; Chat orders by its own `createTime`, and a backlog drained at 1/sec after an outage arrives in real time far later than it was authored | Chat |
| 15 | **@triton's replies carry an `App` badge** and cannot be silent and threaded at once | app auth is required for `cardsV2`; silent cannot start or reply to a thread (`:59-61`, `:101`) | Chat |
| 16 | **Mentions inside a silent message degrade to plain text** | *"If you include a mention in a silent message, it's treated as plain text"* (`:99-100`) — relevant only if the app-auth identity mode is ever selected | Chat |
| 17 | **A space deleted in Chat cascades** | `spaces.delete` *"Always performs a cascading delete"* (`:902-905`) — ERPNext retains everything, so the two sides diverge permanently and by design | Chat |

**Items 3, 4 and 5 are the ones a human will actually notice**, and all three trace to the single
`DECISIONS.md` D3 trilemma. Everything else is a footnote by comparison.

---

### G.13 `VERIFY` register carried forward from section G

Ranked by what breaks. Items closed elsewhere in this document are not repeated.

**Settle before Phase 1 commits to the design (each is one call):**

| ID | Claim | Settle | Blocks |
|---|---|---|---|
| G-V1 | A DWD-impersonated token can create a `spaces/-` subscription | one `subscriptions.create` with `validateOnly: true` | **shape B entirely** — the whole inbound coworker design |
| G-V2 | What `spaceThreadingState` an API-created space reports | `spaces.create` then `spaces.get` — **five minutes** | decision #5's in-thread replies (§G.9) |
| G-V3 | Whether `messageReplyOption` functions in a `GROUPED_MESSAGES` space | post with `REPLY_MESSAGE_OR_FAIL` into an API-created space | same |
| G-V4 | Which scope authorises `google.workspace.chat.message.v1.deleted` | `validateOnly` create holding only `chat.messages.readonly` | the inbound delete path |
| G-V5 | Whether an undocumented cap on concurrent Workspace Events subscriptions exists | create all pilot subscriptions; confirm `subscriptions.list` returns them all `ACTIVE` | shape B at full org size; both fallbacks are expensive |
| G-V6 | The OAuth scope the VM-attached SA must present to call `signJwt` | re-fetch the signJwt reference, or curl from the VM with the metadata token | whether the VM's access scopes need widening — **a VM property change requiring stop/start** |

**Settle during Phase 1, cheap:**

| ID | Claim | Settle | Blocks |
|---|---|---|---|
| G-V7 | The Google Chat 429 shape — `Retry-After`? distinguishable from a permission 403? | the limits page + one deliberate over-send | the retryable classification and backoff |
| G-V8 | Pub/Sub message-retention window on the events subscription | `gcloud pubsub subscriptions describe` | the precise inbound-outage statement in §G.8 rule 4 |
| G-V9 | Whether the `message.v1.deleted` payload carries `deletionMetadata` | delete in a subscribed test space; inspect the payload | delete attribution without a follow-up read |
| G-V10 | The accepted `ttl` string format | one `validateOnly: true` create | nothing — omitting `ttl` asks for the max |
| G-V11 | `requestId` charset, length and dedup window | read the `spaces.create` / `spaces.setup` references | whether a duplicate space is *prevented* or merely *detected* |
| G-V12 | Whether `spaces.spaceEvents.list` has its own quota | the limits page; observe under load | how aggressively the reconciliation sweep may run |
| G-V13 | Whether the 30-second synchronous affordance survives Pub/Sub delivery | a throwaway app in Pub/Sub mode | nothing under the HTTP recommendation |
| G-V14 | `markupSyntax` field name, location and enum values | re-fetch the `messages.create` reference in a few days | **do not code against it until settled** |
| G-V15 | Whether DWD can call `media.upload` | one live upload with an impersonated token | any outbound attachment feature |
| G-V16 | Whether a coworker's `DRIVE_FILE` attachment is readable by the service account without an explicit share | coworker attaches a Drive file; attempt `files.get` | **an ACL question with real data-exposure consequences — answer before any attachment ingestion ships** |
| G-V17 | The list of blocked file types | the "File types blocked in Google Chat" support article | the `skip_reason` vocabulary |
| G-V18 | Whether DWD impersonation of a space *manager* can delete another member's message | one live delete | whether Phase 6 moderation must impersonate authors |

**Deferred, but recorded as deferred:**

| ID | Claim | Blocks |
|---|---|---|
| G-V19 | Import-mode sender attribution to an arbitrary domain user | **whether historical Chat back-fill is possible at all** (§G.11.3) |
| G-V20 | Whether an app can import into a space it did not create | whether existing spaces can be back-filled |
| G-V21 | Whether a space can re-enter import mode after `completeImport` | back-fill retry strategy |
| G-V22 | Maximum backdating age in import mode | how far back a migration may reach |
| G-V23 | Whether an unlisted/private Chat app can be granted `chat.app.*` approval | shape A, shape C, every `chat.app.all.*` scope — **not blocking under the `chat.bot`-only decision** |
| G-V24 | Whether the JWK or the x509 key URL is canonical on the current page | nothing under `verify_oauth2_token`; only a hand-rolled verifier |

---

## H. Notification decision table

*(Phase 0 §4.I. Serves locked decision #3 and invariants I6, I7, I8.)*

### H.0 What is locked, what this section decides, and what it escalates

Decision #3 locks three of the six presence rows and locks the two surfaces: at most one Notification
Log row and at most one Web Push per notifiable event, and **no email**. Phase 0 §4.I marks three
cells-worth of behaviour as *decide*: the blurred-window row, the bubble badge in the different-room
row, and whether a mention overrides suppression. This section resolves all three **as the default we
will ship**, with reasoning, and simultaneously registers each as a human question (CQ-2, CQ-3, CQ-4
in §K.2) because they are product judgements dressed as engineering ones.

Two things this section does **not** re-argue and instead cross-references:

- **The trilemma** — `NOTIFICATION_TYPE_SILENT` requires app authentication, and app authentication
  makes the Chat app the sender with an `App` badge, so *silent* and *authored by the real human* are
  mutually exclusive (`notes_google_verify.md:107-108` vs `:1050-1051`; assembled by
  `notes_gap_report.md` §D-1; adopted as `DECISIONS.md` D3).
  [§E.3](#e3-the-trilemma--the-finding-that-changes-the-product) states it in full. **The
  consequence for this section, stated without hedging: under the recommended DWD/human-attribution
  design, decision #3's "exactly two notifications" holds for every user of the ERPNext SPA and does
  not hold for a user running the native Google Chat client, who additionally receives Chat's own
  notification.** That is not a bug this table can fix. It is CQ-1.
- **The three-value watermark** `(max(seq), count(*), max(modified))` from `DECISIONS.md` D6, which
  [§F.16.2](#f162-invariant-chat-watermark-1--the-three-value-watermark) specifies. §I.7 depends on it; this section only touches it where read-state and digest
  staleness interact.

### H.1 The truth table

Rows are recipient presence states as evaluated **server-side** (I6: the client reports, the server
decides). Columns are outputs. **No cell is blank.** "counter" means the per-user unread counter
published on the user's own realtime room; "badge" means the floating bubble's numeric badge, which
is a *render* of that counter (§H.1.1 explains why those are two different things).

| # | Recipient state (server-evaluated) | Bell / Notification Log | Web Push | Room unread indicator | Bubble count badge | Auto-mark-read |
|---|---|---|---|---|---|---|
| 1 | SPA open, **this room focused** (window focused, `visibilityState === "visible"`, active room == this room, presence fresh) | **no** | **no** | **no** | **no** (counter does not increment) | **yes** — `last_read_seq` advances to this message |
| 2 | SPA open, this room active, **window blurred < 120 s** | **no** | **no** | **yes** | **no** (counter increments; badge suppressed in a tab whose SPA is foregrounded — see H.1.1) | **no** |
| 3 | SPA open, this room active, **window blurred ≥ 120 s** | **yes** | **yes** | **yes** | **yes** | **no** |
| 4 | SPA open, **different room** active (window focused or blurred — irrelevant here) | **no** | **no** | **yes** | **no** in the SPA tab; **yes** in any non-SPA tab (counter increments in both) | **no** |
| 5 | In ERPNext, **SPA closed** (desk or portal page, socket connected, no chat presence key) | **yes** | **yes** | **yes** | **yes** | **no** |
| 6 | **Not in ERPNext at all** (no presence key, no socket) | **yes** | **yes** | **yes** (rendered on next load) | **yes** (rendered on next load) | **no** |
| 7 | **Presence signal missing or stale** (Redis key absent/expired, or `last_seen` older than TTL) | **yes** | **yes** | **yes** | **yes** | **no** |
| 8 | **Recipient is the author** (own message, any surface) | **no** | **no** | **no** | **no** | **yes** |
| 9 | **Direct @mention of the recipient**, or an `@triton` reply addressed to them — **overrides rows 2, 4, 5, 6, 7** | **yes** (a *Chat Mention* type row, distinct from the room row) | **yes** | **yes** | **yes** | **no** |
| 10 | **Direct @mention** while row 1 holds (focused on this room) | **no** | **no** | **no** | **no** | **yes** — message renders highlighted and is marked read |
| 11 | Recipient has **muted** this room (mute semantics are CQ-8) | **no** | **no** | **yes** | **no** | **no** |
| 12 | Recipient has `Notification Settings.enabled = 0` (Frappe's per-user kill switch) | **no** — Frappe drops the row before we see it | **no** | **yes** | **yes** | **no** |

Rows 1, 4, 5 and 6 are the rows locked by decision #3 and are reproduced unchanged. Rows 2/3 are the
resolved blurred-window case (§H.2.1). Row 4's badge cell is the resolved different-room case
(§H.2.2). Rows 9/10 are the resolved mention-override case (§H.2.3). Rows 7, 8, 11 and 12 are states
decision #3 did not enumerate and which the table must cover or Phase 4 will invent them.

**Row 12 is the honesty clause and it must survive into the ADR body.** `make_notification_logs`
filters recipients through `is_notifications_enabled(user)`
(`notes_close_frappe.md`, `frappe/desk/doctype/notification_log/notification_log.py::_get_user_ids`),
so a user who has switched off system notifications receives **zero** Notification Log rows — not
one, not two. "Exactly two notifications" is a ceiling and a design intent, never a delivery
guarantee. Say so in the ADR rather than letting a reader read it as an SLA.

#### H.1.1 Why "counter" and "badge" are different columns

The unread counter is **server state** published once, on the recipient's per-user realtime room. The
bubble badge is **client render** of that counter, and the same user can have three tabs open with
different foreground surfaces. Making the server decide per-tab would require the server to model
tabs, which is exactly the multi-tab trap `notes_research_gaps.md:1156-1160` names as *"the one most
likely to be got wrong"* (R02 §2.6: the suppression rule must be *"**no** client of this user has
that room focused"*).

So the rule is split, and it is the only split that survives multi-tab:

- **Server:** publishes `chat:unread_updated` on `user:<email>` for every row except 1, 8, 10. The
  counter is a per-(user, room) high-water-mark delta, never a per-message fan-out
  (`notes_infra.md:238-244`; `notes_close_frappe.md` §4.5 conclusion 2).
- **Client:** each tab renders the badge only when the SPA is not the foreground surface *in that
  tab*. A tab sitting on a Sales Order shows the badge; the tab with the SPA expanded does not.
- **Suppression of the *notification* surfaces (bell, push) is server-side and quantified over every
  connected client of that user** — `focused_on(room) := ∃ session s.t. presence[user][session] has
  {room, focused: true, fresh}`. One focused client suppresses; a second blurred client does not
  un-suppress. This is I6 restated in a form Phase 4 can test.

### H.2 Resolving the three open rows — reasoning, and the human question each becomes

#### H.2.1 Blurred window (rows 2 and 3) — resolved as a **bounded grace period**, `BLUR_GRACE = 120 s`

The single-row formulation Phase 0 offers cannot be answered, because "blurred" spans two genuinely
different situations that need opposite answers:

- **Alt-tabbed for eight seconds** to check an email. Firing a push here pings a person whose eyes are
  one keystroke away from the message. This is the most-complained-about class of chat notification.
- **Left the tab open on Friday and went home.** Firing nothing here is silent message loss, and it is
  worse than the general silent-loss case because the user *believes* they have the app open.

Resolving in favour of either extreme is wrong for the other. So the ship-default is a bounded grace:
the presence record carries `focused` **and** `focused_changed_at`; the notifier treats a blurred
client as focused while `now - focused_changed_at < BLUR_GRACE`, and as absent thereafter. 120 s is
chosen as ~2× the presence TTL (§H.3.5) so the state machine has one clean layer of hysteresis rather
than two timers racing; it is a config value, logged on every suppression decision, and revisited with
data (the same discipline R03 §8.2 applies to the token ceiling — `notes_research_gaps.md:1048-1052`).

**Auto-mark-read is `no` in both blurred rows, and that is the load-bearing half of this resolution.**
Marking a message read for someone who is not looking at it is a lie that propagates: it clears their
own unread state, and — once per-message read receipts exist (decision #9) — it tells the *sender*
their message was read. A suppressed notification is recoverable (the room indicator is still there).
A false read receipt is not.

→ **CQ-2.** The number 120 and the existence of the grace at all are product calls.

#### H.2.2 Bubble badge in the different-room state (row 4) — resolved as **counter yes, badge per-surface**

Decision #3 locks row 4 to "only an unread indicator on that room in the list". The badge question it
leaves open is really the question in §H.1.1: the badge and the room indicator would be two renders of
the same fact inside one viewport if the SPA is expanded in that tab. Showing both double-counts, and
users read a double-count as a bug.

Ship-default: the counter always increments (so any *other* tab, and the next page load, is correct),
and the bubble badge is suppressed in a tab whose SPA is foregrounded. This keeps the server rule
simple enough to test (I6) and puts the only per-tab judgement in the client, where the information
actually exists.

→ **CQ-3.**

#### H.2.3 Mention override (rows 9 and 10) — resolved as **overrides everything except a focused view**

A mention is the one signal users explicitly opt into and the one whose loss they escalate. It gets:

- a **separate Notification Log row** of a distinct type (`Chat Mention`, §H.5.2) with its own
  `dedupe_on`, so it is not swallowed by the room-level row's dedupe;
- a deep link to the **message**, not the room: `/chat/room/<room>?message=<msg>`;
- **override of rows 2, 4, 5, 6, 7** — including the blur grace, and including a room the user is not
  currently looking at.

It does **not** override row 1 (the user is looking at the message; a push for a message on screen is
noise, and row 10 marks it read) and it does **not** override row 11 (mute). Mute-overriding-by-mention
is the single most contested default in chat products and is deliberately deferred to CQ-8 rather than
chosen here.

One Google-side interaction that must be recorded beside this, because it is a real degradation of the
mention semantics on the Chat surface: *"Silent messages don't support mentioning users. If you include
a mention in a silent message, it's treated as plain text"* (`notes_google_verify.md:99-100`). Under the
recommended DWD design we do not send silent messages at all, so this does not bite — but it is one
more reason the app-auth "Option A" is not a free win.

→ **CQ-4.**

### H.3 The exact server-side inputs

This is the part Phase 0 §4.I says is usually hand-waved. Each input below is named with its **signal,
source, freshness/TTL and failure mode**.

#### H.3.0 The settled negative: Frappe v16 has no presence primitive, so all of this is purpose-built

`frappe/realtime.py` was enumerated twice this session — once by the critic
(`notes_gap_report.md` §0-4) and once independently while closing the socket.io question
(`notes_close_frappe.md` §5). Its complete public surface is `publish_progress`, `publish_realtime`,
`flush_realtime_log`, `clear_realtime_log`, `emit_via_redis`, `has_permission`, `get_socketio_secret`,
`get_user_info`, `get_doctype_room`, `get_doc_room`, `get_user_room`, `get_site_room`,
`get_task_progress_room`, `get_website_room`. **No presence, online-user or last-seen function
exists.** The only adjacent helper, `frappe.core.doctype.user.user.get_active_users`, is a 72-hour
aggregate count, and `User.last_active` is 3-day granular (`notes_gap_report.md` §0-4, quoting the
v16 source). Neither is usable as a presence signal.

The node side is equally unhelpful *as a source*: the socket server holds no session state of its own
and proxies every authorisation question back into Python over HTTP
(`realtime/middlewares/authenticate.js`, quoted at `notes_close_frappe.md` §1.3). There is no
"who is connected" endpoint, and no Python-visible view of the connected set.

**Therefore: chat presence is a new subsystem, and the house precedent is the right model to copy.**
`erpnext_enhancements/api/collab.py:155,209` is a whitelisted, permission-checked realtime relay whose
docstring states the doctrine this design inherits verbatim — *"Clients never emit realtime events to
each other directly — this endpoint is the security authority for every broadcast"*
(`api/collab.py:1-41`, via `notes_ee_audit.md` §11.2 and `notes_infra.md:717-727`). Its presence timing
is `FOCUS_HEARTBEAT_MS = 30 * 1000` / `FOCUS_TTL_MS = 75 * 1000`
(`public/js/collab/live_form_sync.js:71-72`).

#### H.3.1 The constants — and why chat does **not** reuse 30 s / 75 s

> **Seam note (assembly).** **These constants govern** (seam **S3**). The Redis **key shapes** they are
> written into do not come from here — they come from
> [§F.14.2](#f142-key-shapes-and-ttls) and [§F.14.3](#f143-why-chatfocus-is-a-hash-and-not-one-key-per-session):
> one `chat:focus:{user}` **hash** with a field per session, not a key per session, because
> per-field TTLs need Redis 7.4 and production is `7.0.15`. Read §H.3.2's
> `chat:presence:<user>:<session_id>` as *that* hash field, with the 55 s TTL on the key.

`notes_infra.md:724-727` flags the trap explicitly: **30 s is exactly the GCLB idle-connection
boundary**, and the only reason realtime works in production today is that socket.io's stock 25 s
`pingInterval` beats the 30 s idle cut **by five seconds** (`notes_infra.md:846-863`, socket.io
defaults from <https://socket.io/docs/v4/server-options/>, GCP default from
<https://docs.cloud.google.com/load-balancing/docs/https/request-distribution>). Copying 30 s into a
new subsystem inherits a five-second margin for no benefit.

Chat presence therefore chooses its constants deliberately:

| Constant | Value | Why this number |
|---|---|---|
| `CHAT_PRESENCE_HEARTBEAT` | **20 s** | Comfortably inside socket.io's 25 s ping and the 30 s GCLB idle cut, so a presence beat never becomes the thing that is racing the load balancer. Two consecutive misses still fit inside the TTL. |
| `CHAT_PRESENCE_TTL` | **55 s** | ≈ 2 × heartbeat + one heartbeat of slack. A single dropped beat (a GC pause, a throttled background timer, a transient 502) does not flip a present user to absent. |
| `BLUR_GRACE` | **120 s** | ≈ 2 × TTL, so blur hysteresis sits one layer above presence expiry instead of racing it (§H.2.1). |

Note the heartbeat is an **HTTP POST to a whitelisted endpoint**, following `api/collab.py`, not a
client-emitted socket event — clients never emit to each other, and the endpoint is the security
authority. The GCLB constraint that applies to a POST is the 30 s *total request* budget, which a
presence write cannot approach; the idle-connection constraint applies to the socket, which the 20 s
choice deliberately stays clear of.

> `VERIFY: the background-tab timer throttling floor in the browsers our staff run (Chrome/Edge/Safari), and whether a 20 s interval survives it in a backgrounded tab` — settle by instrumenting one tab for 10 minutes in the background and logging actual beat intervals. **Blocks:** nothing structural; if throttling stretches beats past 55 s, the blurred-and-backgrounded case simply degrades into row 3/7 (notify), which is the safe direction. Worth knowing so the ADR does not claim a fidelity it has not measured.

#### H.3.2 Input 1 — does the recipient have the SPA open?

| Property | Value |
|---|---|
| **Signal** | `chat:presence:<user>:<session_id>` exists in Redis and its `last_seen` is within `CHAT_PRESENCE_TTL`. |
| **Source** | A whitelisted `POST` heartbeat from the SPA every 20 s, plus one immediate beat on socket `connect`/reconnect. The `connect` hook is the existing pattern: `public/js/collab/live_form_sync.js:167,172,194` binds `frappe.realtime.on("connect", …)` → `_on_reconnect()` precisely so a dropped socket rehydrates (`notes_infra.md:937-939`). |
| **Freshness / TTL** | 55 s. |
| **Failure mode** | Half-open connection: the socket is up but the tab is frozen or the network is black-holing. The key expires and the user falls to row 7 (notify). This is the *correct* degradation and is why presence is a heartbeat, not a connect/disconnect flag (§H.3.6). |

**Explicitly rejected as the signal: socket.io room membership.** The node process exposes no such
view to Python (§H.3.0), and even if it did, `doc_subscribe` failures are silent
(`notes_close_frappe.md` §1.5.1 — the promise is left pending forever on denial *and* on network
error, because the executor has no `reject` path at all), so "I am in the room" is not something the
client can even reliably assert.

#### H.3.3 Input 2 — which room is focused, and is the window focused?

| Property | Value |
|---|---|
| **Signal** | The heartbeat payload: `{active_room, focused, focused_changed_at, visibility}`. `focused` is the conjunction of `document.hasFocus()` and `document.visibilityState === "visible"`. |
| **Source** | The SPA. Re-sent immediately (out of band, not waiting for the next beat) on `focus`, `blur`, `visibilitychange` and room switch — so the *transitions* users notice are sub-second, and the 20 s beat only maintains liveness. |
| **Freshness / TTL** | Same 55 s key. `focused_changed_at` is server-stamped on receipt, not client-supplied, so a client cannot backdate its own blur to buy suppression. |
| **Failure mode** | A client that reports `focused: true` forever is claiming suppression it has not earned. This is bounded, not prevented: the key still expires 55 s after the last beat, so a hostile or wedged client can suppress its **own** notifications indefinitely and nobody else's. That is an acceptable blast radius — it is self-harm — and it is the reason I6 is written as "the server decides", not "the client cannot lie". State this residual rather than implying the signal is trusted. |

**The room identity is the ERPNext `Chat Room` name, never a Google space name.** A client reporting
`gchat_space_name` would let the mapping table become a suppression oracle.

#### H.3.4 The default when the signal is missing or stale — **fail toward "send both"**

Row 7 of the table. This is a genuine fork and Phase 0 §4.I demands it be argued, not asserted.

**Chosen: notify.** Three reasons, in order of weight:

1. **The two failures are not symmetric.** A duplicate ping is annoying, visible, self-correcting and
   *reported* — the user tells you. A suppressed message is invisible to everyone, including to the
   person who needed it, and is discovered days later when someone says "I never got that". A design
   that fails silently in the direction of data loss is the worse of the two, and it is worse by more
   than one order of magnitude in a business chat system.
2. **The duplicate is bounded by construction.** `enqueue_create_notification(..., dedupe_on=[...])` is
   new in v16 and makes notification creation idempotent per recipient
   (`notes_close_frappe.md` §3.7, quoting `notification_log.py::enqueue_create_notification` and
   `_notification_exists`). With the room-level row keyed on `(for_user, document_type,
   document_name)`, a spurious notify does not create a second bell row — it lands on an existing one.
   So the cost of failing open is *at most* one extra push, not an unbounded pile of bell entries.
3. **The stale-signal case is dominated by a known, frequent, benign event** — the deploy
   (§H.3.5 below) — where "notify" is exactly right, because a deploy has in fact just disconnected
   everyone.

**The consequence the human must know, stated plainly: under a Redis outage, everyone gets notified
about everything.** Presence lives entirely in Redis; if `redis_cache` (`127.0.0.1:13000`) is down or
has evicted the keyspace, every recipient evaluates to row 7 and every message to every room produces
a bell row and a push for every member. At the measured roster (23 enabled users, 20 System Users,
`notes_infra.md:140-151`, corrected from the "~50" premise by `notes_register_reconciled.md` C4) that
is loud but survivable; it is not a data-loss event, and the room-level dedupe keeps the Notification
Log from exploding. The reverse choice — fail closed — turns the same outage into a total,
unannounced notification blackout. **Choose loud over silent.** → registered as **CQ-9**.

Two mitigations that make the failure-open case cheap, both of which Phase 4 must build:

- **Deploy-window suppression is achieved by the reconnect beat, not by a special case.** Every
  production deploy runs `redis-cli -p 13000 FLUSHDB && redis-cli -p 11000 FLUSHDB` followed by
  `systemctl restart frappe-bench` (`infra/cloudbuild-deploy.yaml:40-41` per `notes_infra.md:592-600`;
  `notes_close_repo.md` §5.6 records the same flush at `:37` flushing both ports). Presence is
  therefore *guaranteed* wiped on every deploy. Because clients re-beat on socket `connect` rather
  than waiting for their 20 s timer, the notify-everyone window is bounded by reconnect latency
  rather than by the heartbeat interval. Copy `live_form_sync.js`'s `_on_reconnect()` shape exactly.
- **Redis is a working copy, never the truth.** The precedent is `training/progress.py:44-46`:
  *"a deploy FLUSHDBs Redis, so the worst case is losing one flush interval of watching — about a
  minute — not an attempt"*, with `flush_stale_attempts()` to drain what a closed tab left behind
  (`notes_infra.md:606-612`). Presence is designed the same way: losing the keyspace degrades to
  "everyone shows offline for one heartbeat", never to corruption. Read state (`last_read_seq`) lives
  in the database, not in Redis, for exactly this reason.

#### H.3.5 Where presence lives — exact key shapes

Namespacing is not optional here. `redis_cache` and `redis_socketio` are **the same instance and the
same DB 0** (`notes_infra.md:582-587`), `maxmemory-policy` is `allkeys-lru` so keys can be **evicted
before their TTL** (`notes_infra.md:614-617`), and the house convention is an explicit prefix
(`training:attempt:` in `training/progress.py:59`, `triton_user_token::{user}` in
`triton_chat.py:124`).

| Key | Type | Value | TTL | Written by | Read by |
|---|---|---|---|---|---|
| `chat:presence:{user}:{session_id}` | String (JSON) | `{"room": <Chat Room name or null>, "focused": bool, "focused_changed_at": <epoch s, server-stamped>, "last_seen": <epoch s>, "ua": <short>}` | **55 s**, refreshed on every beat | the heartbeat endpoint | the notifier, per recipient |
| `chat:presence:sessions:{user}` | Set of `session_id` | index so the notifier never `SCAN`s | **55 s**, refreshed on every beat | the heartbeat endpoint | the notifier |
| `chat:typing:{room}` | Hash `user → epoch` | typing indicator (decision #9) | **10 s** (read-side filtered on the stamp) | the typing endpoint | room members |

Notes that matter to the implementer:

- **The session set is allowed to contain stale members.** The notifier reads the set, then `MGET`s
  the per-session keys and ignores misses. Redis 7.0.15 (`notes_infra.md:565`) has no per-hash-field
  expiry (`HEXPIRE` is a later addition), which is why presence is one key per session with a real
  TTL rather than one hash per user. Do not "optimise" this into a hash.
- **Never `SCAN` in a request path.** The set exists precisely to avoid it.
- **`session_id` is the browser tab's own id (a `crypto.randomUUID()` held in `sessionStorage`), not
  the Frappe session id.** One Frappe session spans many tabs, and the multi-tab quantifier in
  §H.1.1 needs per-tab granularity. It must never be a value Frappe treats as reserved — in
  particular the request key `sid` must never appear in a POST body, because Frappe's auth layer pops
  it and fakes a "session expired" response.
- **Eviction under `allkeys-lru` is indistinguishable from expiry to the reader**, and both land on
  row 7. That is by design and is one more reason the default is "notify".

#### H.3.6 Presence is a heartbeat with a TTL — never a sticky flag — and there is a test that proves it

The failure this rule exists to prevent: a browser crash, a `SIGKILL`, a laptop lid closing, or a
network partition produces **no disconnect event**. A design that sets `online = 1` on connect and
clears it on disconnect leaves that user permanently "present and focused", and they stop receiving
notifications **forever**, silently, until they next open the app. This is the single most common
way presence-based suppression turns into permanent message loss, and it is not hypothetical: the
same class of bug is already documented in this repo's realtime layer as a listener leak scar
(`public/js/collab/live_form_sync.js:597-603`).

**Rules, all testable:**

1. There is **no** `presence_connect` / `presence_disconnect` pair. There is one verb: `heartbeat`.
2. Absence of a fresh key **is** absence. The notifier never reads a boolean.
3. An explicit `beacon`-style "I am leaving" on `pagehide` is an **optimisation only** — it deletes
   the key early. Its absence must change nothing but latency.

**The required Phase 4 test, named:**

```
test_presence_expiry_resumes_notifications
  Given user B is present with {room: R, focused: true} and a message in R produces zero notifications
  When the heartbeat stops (simulate a crash: no pagehide, no disconnect, no key deletion)
   And the clock advances past CHAT_PRESENCE_TTL
  Then a second message in R produces exactly one Notification Log row and exactly one Web Push
   And the transition required no client cooperation of any kind
```

A companion test asserts the same for the blur grace
(`test_blur_grace_expiry_resumes_notifications`), because `BLUR_GRACE` is a second timer and a second
opportunity to write a sticky flag by accident.

### H.4 Cross-surface notification sync

Decision #3 requires that reading in the SPA clears the bell and the push, dismissing the push clears
the bell, and mark-all-read clears the badge. `notes_research_gaps.md:1134-1138` (R02 §4.4) enumerates
the four parts and names the one people forget — **(d), the push message that closes the OS
notification**.

#### H.4.1 The channel split — CONFIRMED, not provisional

Phase 0 §4.I asserts that `doc:{doctype}/{name}` rooms are permission-checked on join while
`user:{user}` rooms are not, and the critic correctly flagged that **no note had verified it**
(`notes_gap_report.md` §C-5, §E item 6; carried as blocking item B4 in
`notes_register_reconciled.md`). It has since been verified from the v16 node source, and the answer
is stronger than the assumption:

- **Doc rooms are permission-checked.** `realtime/handlers.js`, verbatim:
  `socket.on("doc_subscribe", function (doctype, docname) { socket.has_permission(doctype, docname).then(() => { socket.join(doc_room(doctype, docname)); }); })`,
  where `socket.has_permission` is an HTTP callback to
  `/api/method/frappe.realtime.has_permission`, which is
  `frappe.has_permission(doctype, doc=name, throw=True)` → `frappe.permissions.has_permission` →
  `get_doc_permissions`, i.e. the full document-level stack including `has_permission` hooks and User
  Permissions. **The `ptype` is `read`** (`notes_close_frappe.md` §1.4).
- **User rooms are not checked because there is no verb to check.** `user_room` appears exactly twice
  in the entire four-file node surface: its definition, and one server-side
  `socket.join(user_room(socket.user))` executed at connection time from the already-authenticated
  identity. There is no `user_subscribe` event and no handler anywhere that takes a user id as a
  client argument (`notes_close_frappe.md` §1.4). This is stronger than "self-subscribing is
  self-authorising" — self-subscription is not client-initiated at all.
- **Authorisation happens entirely at join time.** The redis→socket fan-out does no re-check:
  `io.of(namespace).to(message.room).emit(...)`. **The room membership set *is* the ACL**
  (`notes_close_frappe.md` §1.2).

So the split below is stated flat, with no hedge. `notes_gap_report.md` §A-5.2 / §C-5 are closed.

| Channel | Room | Permission-checked on join? | Carries |
|---|---|---|---|
| **Content** | `doc:Chat Room/<room>` | **Yes** — `frappe.has_permission("Chat Room", doc=<room>, ptype="read")`, evaluated in Python under the joining user's own session | `chat:message`, `chat:message_edited`, `chat:message_deleted`, `chat:typing` |
| **Counters / state** | `user:<email>` | **No** — and unreachable by anyone but that user's own authenticated sockets | `chat:unread_updated`, `chat:mention`, `chat:notification_state`, `chat:room_access_revoked` |

#### H.4.2 The collision this creates with the MCP denylist, and how it is resolved

**This is the most consequential interaction in §H–§K and it must not be resolved by accident.**

> **Seam note (assembly).** **This subsection states the shipped design**, and it supersedes the per-user
> content fan-out proposed at
> [§F.18.3](#f183-the-realtime-consequence--and-a-deviation-from-a-sibling-note-declared),
> which was drafted without the v16 socket-source evidence in §H.4.1 and which `DECISIONS.md` D2
> contradicts directly (D2 lifts *"realtime scoped to the channel doc room rather than per-user
> fan-out"*). §F.18.3's residuals are real and are restated at §H.4.3; its design is the recorded
> fallback. Seams **S1** and **S2**.

- `notes_close_frappe.md` §1.7 establishes that a DocType with **zero DocPerm rows** cannot be
  doc-room-joined by anyone but `Administrator`: `get_role_permissions` yields nothing, `perm` is
  falsy, and the join is *silently* refused (§1.5.1 — no error, no ack, no timeout).
- `notes_close_repo.md` §1.6 recommends exactly that — **Layer 1: zero DocPerm on `Chat Message`,
  `Chat Room`, `Chat Room Member`** — as the primary containment against FAC's generic MCP tools.

Taken together and applied literally, realtime chat over doc rooms is impossible. **The resolution
this ADR adopts, and it is a deviation from both notes, split along the axis the evidence supports:**

| DocType | DocPerm | Why |
|---|---|---|
| **`Chat Room`** | a **minimal `read` DocPerm** for the chat role, **plus** a `permission_query_conditions` + `has_permission` pair added in the same commit | The socket join is keyed on the **room**, so this is the only DocType that *must* be permission-resolvable. The pair is the membership gate. `hooks.py:1123-1148` / `:1150-1164` are at exact 10-and-10 parity (`notes_gap_report.md` §0-9, `DECISIONS.md` D8) and the doctrine — *every query condition has a `has_permission` twin* — holds without exception; `tests/test_kpi_snapshot_permissions.py:9-12` is the template test (`notes_close_repo.md` §1.3). |
| **`Chat Message`** | **zero** | This is the DocType that carries message bodies. Zero DocPerm closes `/api/resource/Chat Message`, the desk list/form/report views, global search, export, and FAC's `get_document` / `list_documents` / `search_documents` outright (`notes_close_repo.md` §1.2.3). Nothing needs to join a doc room *named after a message*. |
| **`Chat Room Member`**, `Chat Context Chunk`, `Chat Room Digest`, `Chat Thread Digest`, `Chat Retrieval Audit` | **zero** | Same reasoning; none is a realtime room target. |

A room record carries a title, membership metadata, `gchat_space_name` and `linked_doctype` /
`linked_document` — not message text — so the exposure the minimal DocPerm buys back is bounded, and
the pair reduces it to "your own rooms". **`Chat Room` is nonetheless still on the `_gate.py` denylist
(§I.2), because `run_database_query` consults no permission layer at all** and a room list is itself
sensitive.

Two alternatives were considered and are recorded so they close once:

- **A `DocShare` row per (user, room)**, which grants `read` even with zero DocPerm via the
  `false_if_not_shared()` branch of `frappe/permissions.py` (`notes_close_frappe.md` §1.7). Rejected:
  it makes membership two sources of truth — a `Chat Room Member` row and a `DocShare` row — which is
  precisely the shape that drifts.
- **Our own `apps/erpnext_enhancements/realtime/handlers.js`**, which the socket server auto-loads
  with an already-authenticated socket (`notes_close_frappe.md` §1.8). Rejected for V1: it puts a node
  file outside the esbuild pipeline, needs a socketio restart to take effect, and duplicates a working
  mechanism. **Recorded as the available escape hatch** if the silent-refusal or no-eviction residuals
  below prove intolerable.

> **Contradiction, written down rather than resolved quietly:** this deviates from
> `notes_close_repo.md` §1.6, which lists `Chat Room` among the zero-DocPerm set, and it narrows
> `notes_close_frappe.md` §1.7, which recommends a minimal DocPerm on *all* chat DocTypes. Neither
> note had the other's finding in front of it. Phase 1 must not write a DocType JSON until this split
> is confirmed by Nikolas or by the Phase 1 reviewer.

#### H.4.3 Two residuals of the join-time-only model, stated rather than hidden

1. **Membership revocation is cooperative, not enforced.** Room membership is checked **once, at
   join**; there is no re-validation (`notes_close_frappe.md` §1.5.3). A user removed from a room
   keeps receiving that room's content until they disconnect or unsubscribe. The design's answer is a
   **targeted eviction push** — `chat:room_access_revoked` on `user:<removed>`, and the client calls
   `doc_unsubscribe` — which is a cooperative eviction. **A hostile client with an open socket keeps
   receiving that room's traffic until reconnect.** If that is unacceptable, message content cannot
   live on a long-lived doc room and must move to per-user fan-out for private rooms. Named as a
   Phase 1 invariant with its test; the residual is accepted, not papered over.
2. **A refused `doc_subscribe` is silent.** The promise in `socket.has_permission` is left pending
   forever on denial and on network error alike (`notes_close_frappe.md` §1.5.1). The SPA must
   therefore **never treat "I emitted `doc_subscribe`" as "I am subscribed"**: the whitelisted
   room-open endpoint that returns the backlog also returns `subscribed: true`, and the client shows
   a degraded/reconnecting state until it arrives.

Related, and cheap to get wrong: **subscribe lazily to the active room only.** Every join costs a full
HTTP round trip into Python with a real session load and a `get_lazy_doc`, landing on the same
gunicorn pool that serves the desk (`notes_close_frappe.md` §1.5.2). The room list and its unread
counters are driven entirely off `user:<email>`, which costs zero joins.

#### H.4.4 The events, and the realtime hygiene rules that bind them

| Event | Room | Payload (shape, not schema) | Fired when |
|---|---|---|---|
| `chat:message` | `doc:Chat Room/<room>` | the rendered message + `seq` | on insert, `after_commit` |
| `chat:message_edited` / `chat:message_deleted` | `doc:Chat Room/<room>` | `{message, seq, modified}` | on edit / tombstone |
| `chat:typing` | `doc:Chat Room/<room>` | `{user, expires_at}` | throttled client report |
| `chat:unread_updated` | `user:<email>` | `{room, unread_count, total_unread, last_seq}` | every table row except 1, 8, 10 |
| `chat:mention` | `user:<email>` | `{room, message, snippet}` | rows 9 |
| `chat:notification_state` | `user:<email>` | `{room, last_read_seq, cleared: ["bell","push"], notification_log_names: [...]}` | **read on any surface** |
| `chat:room_access_revoked` | `user:<email>` | `{room}` | member removal (§H.4.3) |

**Three named realtime invariants for Phase 1's lint/test rules**, each evidenced from v16 source.
These are the same three rules as `CHAT-RT-1`, `CHAT-RT-2` and `CHAT-RT-3` at
[§F.18.3](#f183-the-realtime-consequence--and-a-deviation-from-a-sibling-note-declared) — **those
names are canonical**; what follows adds the `task_id` and site-room evidence (seam **S5**):

- **(a) Every chat `publish_realtime` call passes `room=` explicitly**, computed by a single
  chat-owned helper. Not `user=`, not `doctype=`+`docname=`. This is the *only* form immune to both
  hazards below (`notes_close_frappe.md` §5).
- **(b) No chat event may be named `list_update` or `docinfo_update`.** Both names **overwrite an
  explicitly passed `room=`**, because that assignment happens before the `if not room:` guard
  (`notes_gap_report.md` §0-3; re-confirmed verbatim in `notes_close_frappe.md` §5).
- **(c) The final fallback is `get_site_room()`, which returns `"all"`, and every System User is
  already sitting in it** (`realtime/handlers.js`: `if (socket.user_type == "System User")
  socket.join(SITE_ROOM)`). A call that forgets its scoping argument delivers chat content to every
  desk session instantly. This is not theoretical — `notes_ee_audit.md` §11.1 already records **three
  existing unscoped broadcasts carrying business data**, `triton_incoming_call` among them.

And the subtler variant the critic did not have: **`task_id` outranks even an explicit `user=`**, and
`frappe.realtime` seeds it implicitly from `frappe.local.task_id`. Inside a background job — which is
where every relay and notification write happens — a `publish_realtime(event, msg, user=…)` with no
`room=` is silently retargeted to `task_progress:<task_id>`, a room **any client may join with no
permission check whatsoever** (`task_subscribe`/`progress_subscribe` call `socket.join` directly). It
also sets `after_commit = False`, discarding the transactional guarantee
(`notes_close_frappe.md` §5). Rule (a) is what defends against this, and it is invisible in the
calling code without it.

Also: `WEBSITE_ROOM` (`"website"`) is joined by **everyone** including Website Users and Guests, and
`user:Guest` is a **shared** room. Never publish chat on either (`notes_close_frappe.md` §1.5.5,
§1.6).

#### H.4.5 The four-part read sync, concretely

When user B reads room R up to `seq = N` (in the SPA, or by clicking a push, or by mark-all-read):

1. **Persist** `Chat Room Member.last_read_seq = N` — the high-water mark. Read state is a per-(user,
   room) mark, never a row per (user, message): `notes_infra.md:238-244` sizes the alternative and it
   is the same arithmetic that kills per-message notifications (§H.6).
2. **Clear the bell:** set `read = 1` on the matching `Notification Log` rows (the room row, and any
   mention rows for messages `≤ N`).
3. **Publish** `chat:notification_state` on `user:<email>` carrying `last_read_seq` and the cleared
   Notification Log names, so **every other tab** and the floating bubble converge. Frappe's own
   `Notification Log.after_insert` already publishes `frappe.publish_realtime("notification",
   after_commit=True, user=self.for_user)` (`notes_close_frappe.md` §3.1), so the standard bell
   indicator refreshes for free; our event is what moves the *chat* counters.
4. **Close the OS notification.** Send a Web Push data message that the service worker turns into
   `registration.getNotifications({tag}).then(ns => ns.forEach(n => n.close()))`. Each room's
   notifications carry a **stable `tag`** (`chat-room-<room>`) so a later push replaces rather than
   stacks, and so a single close call clears the lot
   (`notes_research_gaps.md:1145-1149`, R02 §5.3). **This is the step that is always forgotten**
   (R02 §4.4(d)) and it is the one that makes "dismissing the push clears the bell" true in the other
   direction too: the `notificationclick` handler posts the read to the server, which then runs
   steps 1–3.

Mark-all-read is the same four steps with `N = max(seq)` per room, in one transaction, publishing one
`chat:notification_state` per room.

### H.5 The three specific traps

#### H.5.1 Notification Log `link` vs `document_type` / `document_name` — **`link` wins; set both**

Two independent lines of evidence, and the confidence is high enough to state without a hedge:

- **Source.** v16 `frappe/public/js/frappe/ui/notifications/notifications.js::get_item_link` returns
  `notification_doc.link` **first and short-circuits** before `document_type` / `document_name` are
  consulted (`notes_infra.md:367-383`, read from the `version-16` branch).
- **Live data.** `link` is set on **0 of 9,612** Notification Log rows across 13 months
  (`notes_infra.md:331-342`), so nothing in core competes for the field and writing it is
  collision-free whichever way the tie breaks. `link` is `SmallText`
  (`notes_close_frappe.md` §3.7), so a deep link fits comfortably.

**Decision: set `link` to the SPA deep link *and* set `document_type` / `document_name` to
`Chat Room` / `<room>`** (room-level rows) or `Chat Message` / `<name>` (mention rows), so the row
stays queryable, auditable, and usable as the `dedupe_on` key. Adopted from `notes_infra.md:441-445`.

**Residual, and it is a grep not a design question:** does the *deployed* `frappe 16.30.0` desk bundle
match the branch tip? Assets are content-hashed, so a source read does not prove a shipped behaviour.

> `VERIFY: the deployed bundle's get_item_link matches the version-16 source` — one read-only command, `notes_infra.md` V-3:
> ```bash
> gcloud compute ssh production-erpnext-standard-vm --zone=us-east4-a --project=erpnext-465317 --tunnel-through-iap \
>   --command="grep -n -A 12 'get_item_link' /home/frappe/frappe-bench/apps/frappe/frappe/public/js/frappe/ui/notifications/notifications.js"
> ```
> **Blocks:** nothing — under "set both", either precedence produces a working link (one deep, one to the desk form). Do it in Phase 4 anyway; it is thirty seconds and it converts a strong inference into a fact.

#### H.5.2 The `after_insert` email path — the exact suppression, and the test that asserts zero emails

`Notification Log.after_insert` calls `send_notification_email(self)` whenever
`is_email_notifications_enabled_for_type(self.for_user, self.type)` is true
(`notes_close_frappe.md` §3.1, quoted verbatim from v16). Decision #3 says neither of the two
notifications is email, so this must be **structurally impossible**, not merely defaulted off.

**The mechanism: `notification_skip_email_types` in `erpnext_enhancements/hooks.py`.** It is a v16
hooks list, merged across all installed apps by `frappe.get_hooks`, consulted unconditionally as the
**first** gate inside `is_email_notifications_enabled_for_type`, and **no user setting can override
it** (`notes_close_frappe.md` §3.2, §3.3). Frappe's own `hooks.py` uses it for `"Alert"`.

```python
# erpnext_enhancements/hooks.py
notification_skip_email_types = ["Chat Message", "Chat Mention"]
```

Three consequences Phase 4 must build around, all from the same source read:

1. **`Notification Log.type` is a `Link` to a real `Notification Type` DocType in v16**, not a Select
   (`notes_close_frappe.md` §3.4). Writing `"type": "Chat Mention"` without a `Notification Type`
   record of that name is a **link-validation failure on insert**. Ship the records. **Recommended
   delivery: an idempotent `after_migrate` installer mirroring
   `frappe.desk.doctype.notification_type.notification_type.install_notification_types`**, rather than
   a fixture — it is robust against the fixture-deletion-is-two-steps rule
   (`fixtures/README.md`, `CLAUDE.md`).
2. **The type names become app configuration.** The hook is keyed by Notification Type *name*, not by
   doctype or app, so `"Chat Message"` and `"Chat Mention"` are part of this app's public surface from
   the moment they ship. Appendix B names them now so Phase 4 does not invent a third.
3. **Registering the hook makes the "enable email for all users" button throw** —
   `frappe.throw(_("{0} never sends email, so it cannot be enabled for users."))`
   (`notes_close_frappe.md` §3.5). That throw is the assertion we want and gets its own cheap unit
   test.

There is a second, structural layer that comes free and must **not** be relied on: a Notification Type
created *after* v16's `backfill_notification_email_type_preferences` patch is in **nobody's**
`email_notification_types` allow-list, so gate 3 fails for everyone by default
(`notes_close_frappe.md` §3.5). It is a default, reversible by any System Manager. Belt (hook) and
braces (opt-in default).

**The test, and its third precondition is the entire point** (bench-required, so it belongs in a
`tests/` suite that needs a real bench, not the bench-free CI list — `CLAUDE.md`):

```
test_chat_notifications_never_email
  Given a Notification Type row named "Chat Message" exists with enabled = 1
    And "Chat Message" in frappe.get_hooks("notification_skip_email_types")
    And a User whose Notification Settings has enable_email_notifications = 1
    And that user's email_notification_types explicitly CONTAINS "Chat Message"   # the adversarial case
  When enqueue_create_notification([user], {"type": "Chat Message", ...}) runs to completion
  Then is_email_notifications_enabled_for_type(user, "Chat Message") is False
   And exactly one Notification Log row exists for that user
   And ZERO Email Queue rows were created by that insert
```

The adversarial precondition is what proves the hook beats an explicit per-user opt-in — which is the
only thing that makes decision #3's "no email" an **invariant** rather than a default. Assert on the
predicate (fast, pure) *and* on the Email Queue row count (proves the wiring). This is I7's test.

Two further traps in the same code path that will silently eat notifications and must be designed
around now:

- **`_get_user_ids` filters on the `email` field and returns `name`.** On a site where a User's login
  id differs from their email, passing `name` yields **zero** recipients and the notification vanishes
  with no error. **Phase 4 must resolve `Chat Room Member` → `User.email`, never → `User.name`**
  (`notes_close_frappe.md` §3.8.1; carried as CF-4).
- **`enqueue_after_commit=not frappe.in_test`** — notification creation rides the commit into the
  **background queue**, and the prod deploy FLUSHDBs the queue Redis. **A deploy landing between
  commit and worker pickup destroys the notification**; the message row survives because it is
  committed (`notes_close_frappe.md` §3.8.4). Same failure class as the relay, same answer: **the
  outbox sweeper is the delivery guarantee, not the queue** (`DECISIONS.md` D8). The sweeper detects
  a message whose notification was never written and re-issues it — which is only safe because
  `dedupe_on` makes re-issue idempotent. **These two facts are useless apart and correct together.**

#### H.5.3 `createMessageNotificationOptions` — confirmed, and it collides with human attribution

The field is **confirmed to exist, is GA since 2026-05-08, and genuinely suppresses Chat's own
notification** — both the push and the unread marker
(`notes_google_verify.md:27-139`, enum quoted verbatim at `:52-61`:
`NOTIFICATION_TYPE_NONE` / `NOTIFICATION_TYPE_FORCE_NOTIFY` / `NOTIFICATION_TYPE_SILENT`). The
register's "highest-priority gap" (R01-G01/V04) is **RESOLVED**.

**And it requires app authentication** (`notes_google_verify.md:107-108`), which makes the Chat app
the sender with an `App` badge (`:1050-1051`).
[§E.3](#e3-the-trilemma--the-finding-that-changes-the-product) states the trilemma in full; this section
states only its notification consequence and states it unambiguously:

> **Under the recommended DWD/human-attribution design (`DECISIONS.md` D3), locked decision #3 is at
> risk for native-client users, and no server-side mechanism in this ADR closes that gap.** A
> coworker running the native Google Chat client receives ERPNext's two notifications *and* Chat's
> own. The restatement we recommend Nikolas adopt is: *"exactly two ERPNext-fired notifications;
> users running the native Google Chat client additionally receive Chat's own notification, which is
> documented and accepted."* Phase 0 §4.I explicitly authorises this outcome.

Four further fences on `_SILENT`, all quoted at `notes_google_verify.md:95-108`, which matter even if
Nikolas chooses app auth: it does not apply to **external users**; **mentions degrade to plain text**
inside a silent message; **you cannot start or reply to a thread** with one; and it is **unsupported
in DMs**. Combined with `spaceThreadingState` being **Output only**
(`notes_google_verify.md:575-623`) and app auth being **unable to upload attachments**
(`notes_google_verify.md:714-735`), the app-auth branch costs threading, attachments, mentions and
attribution to buy silence.

The documented fallback, `users.spaces.spaceNotificationSetting.patch`, is **not** a substitute and is
rejected with reasons: it is **per-space, not per-message**, so it mutes human coworkers along with
the relay; it must be re-applied per user per new space; and any coworker can flip it back in the
Chat UI with nothing notifying ERPNext (`notes_google_verify.md:212-225`). Its enums and its
self-scoping are documented in full there, and `DECISIONS.md` D3 adopts the sibling's verdict verbatim.

→ **CQ-1** (the trilemma itself) and **CQ-5** (which of A/B/C we ship for Chat-native notifications).

### H.6 The volume consequence: notifications are per-conversation-state, not per-message

This is not an optimisation. It is a schema constraint discovered by arithmetic and it changes what
decision #3 *means*.

Measured baseline: `tabNotification Log` is **7,165 rows / 33.2 MB over 13 months**, currently growing
at **61 rows/day**, with **no `Logs To Clear` row** — it is registered for retention in neither
`frappe/hooks.py` nor `erpnext/hooks.py` (`notes_infra.md:178,229-257`; corroborated against both
hook dicts verbatim in `notes_close_frappe.md` §4.1). Now project decision #3 onto it
(`notes_close_frappe.md` §4.5, using `notes_infra.md`'s own 600-messages/day scenario):

| Scenario | Rows/day | vs today | Rows in 13 months |
|---|---:|---:|---:|
| Today | 61 | 1× | 7,165 |
| Chat, notification per **mention only** (≈5% of messages, 1 recipient) | +30 | 1.5× | ~11k |
| Chat, **two per message to one recipient** | +1,200 | **20×** | ~470k |
| Chat, two per message **fanned out to a 10-person room** | +12,000 | **198×** | ~4.7M |

At ~4.6 KB/row that third row is **~2 GB/year on a table with no retention and no index on
`for_user`**. So:

1. **One Notification Log row per (user, room), updated or dedupe-suppressed — never one per
   message.** `dedupe_on=["document_type", "document_name"]` with `document_type = "Chat Room"` is
   the framework primitive that makes this expressible, and it is new in v16
   (`notes_close_frappe.md` §3.7). Mentions get their own type and their own `dedupe_on` including the
   message name, because they are per-message *and* low volume.
2. **The unread count rides `user:<email>`, which costs zero rows.**
3. **Register retention regardless.** `default_log_clearing_doctypes = {"Notification Log": 30}` in
   `erpnext_enhancements/hooks.py` is one line; the next `daily_maintenance` run appends the
   `Logs To Clear` row automatically and starts trimming, with no patch, fixture or UI step
   (`notes_close_frappe.md` §4.3). It also **unlocks `clear_log_table`**, which today raises
   `ValidationError` for this doctype, for the one-time 13-month catch-up delete (§4.4). Three
   mechanical details: the hook merge takes `retentions[-1]` (install-order dependent if two apps ever
   declare the same doctype); `add_default_logtypes` **never updates an existing row**, so the number
   is chosen once; and any chat DocType registered for retention **must implement a
   `clear_old_logs(days)` staticmethod**, or `remove_unsupported_doctypes()` deletes the row on the
   next daily run and retention silently stops.
   **This last item fixes a pre-existing, unrelated 13-month leak and is independent of chat** — it is
   offered to Nikolas as a standalone improvement (**CQ-21**), not smuggled in.

> `VERIFY: whether tabNotification Log has an index on for_user after the v16 migration` — the v16 `notification_log.json` carries `"search_index": 1` on `for_user`, but the measured prod table did not have it (`notes_infra.md:302-305`; `notes_close_frappe.md` §4.5). Re-run the `information_schema.STATISTICS` query. **Blocks:** only whether Phase 4 adds it itself via `frappe.db.add_index("Notification Log", ["for_user", "read"])` — signature confirmed at `notes_gap_report.md` §0-5.

### H.7 Named invariants and tests this section hands to Phase 4

| Invariant | Test |
|---|---|
| **I6** — suppression is decided server-side | With the client's suppression code stubbed out, the server still emits zero notifications for row 1 |
| **I7** — exactly two surfaces, in sync, no email | The 12-row matrix above × (bell, push, room indicator, badge, auto-read) with exact counts; plus `test_chat_notifications_never_email` (§H.5.2) |
| **I8** — realtime security rides the doc room | User B `doc_subscribe`s a room they are not a member of and receives nothing; and the `has_permission` hook returns an **explicit boolean on every path** (returning `None` denies on v16) |
| Presence is a heartbeat, not a flag | `test_presence_expiry_resumes_notifications` (§H.3.6) |
| Blur grace is not a sticky flag | `test_blur_grace_expiry_resumes_notifications` |
| Multi-tab quantifier | Two sessions, one focused on R and one blurred; a message in R produces zero notifications. Then the focused session expires; the next message produces both |
| Realtime hygiene | Source-level: every chat `publish_realtime` passes `room=`; no chat event is named `list_update` or `docinfo_update` |
| Eviction | Removing a member publishes `chat:room_access_revoked` on their user room; the client unsubscribes |

---

## I. Triton integration and context/caching

*(Phase 0 §4.J. Serves locked decisions #5, #6, #7 and invariants I4, I5, I9, I10, I11, I12, I13.)*

This section commits to **architecture**, not implementation. Every constant it names is a config
value with a revisit trigger, and the detail belongs to Phase 5. What must be fixed *now* is the set
of commitments later phases cannot cheaply reverse: where retrieval lives, what closes the MCP
surface, what the gate's signature is, where vectors are stored, how digests are invalidated, and
which identity does what.

### I.1 Retrieval lives in ERPNext, behind exactly one whitelisted method

**Commitment.** All chat retrieval executes inside `erpnext_enhancements`, in one package
(`erpnext_enhancements/chat/retrieval/`) exposing exactly **one** public symbol, reached over the wire
by exactly **one** `@frappe.whitelist()` method, called with **the mentioning user's own
credentials**. Triton never reads a chat table, never receives a room id it did not get back from this
method, and holds no chat-scoped database access of any kind.

**Why this is the only shape that satisfies decision #5.** Triton's identity model is already correct
on the ERPNext side and was verified end to end: the MCP endpoint is third-party **Frappe Assistant
Core**, reached at
`https://erp.sapphirefountains.com/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp`
(`triton:backend/app/core/config.py:178`), authenticated with a **per-user OAuth2 bearer**
(`FrappeClient._auth_header`, `triton:backend/app/core/frappe.py:161-164`), constructed on the chat
path as `FrappeClient(db=db, user_id=user.id)` (`triton:backend/app/core/intelligence.py:247`) which
**never falls back to system mode for a user-scoped construction**; the shared
`FRAPPE_API_KEY`/`FRAPPE_API_SECRET` is confined to four named unattended non-chat jobs
(`notes_triton_audit.md:1336-1459`, `:709-712`). Frappe resolves that bearer to `frappe.session.user`,
which is exactly what `assistant_tools/_gate.py:403,528` reads. So a whitelisted method called over
that bearer runs **as the human**, and every permission decision is made in the process that owns the
ACL.

The alternative — Triton querying chat data itself — fails on three independent counts: it would need
a credential with cross-user reach (the thing decision #5 forbids), it would put the permission filter
in a different process from the membership table, and it would make I4's source-level test
unenforceable because the SQL would live in a repo the test cannot see.

**Consequence to state honestly:** the audit row is written *before* content is returned (§I.7.3), so
`retrieve()` must be called at the **start** of the Triton turn, before any other write in that turn
(`notes_research_gaps.md:1183`, R03 §11.2).

### I.2 I5 — the denylist on the generic MCP tools, by the mechanism that actually closes it

Phase 0 §4.J says the existing MCP `run_database_query` / `get_document` tools must denylist chat
DocTypes. The critic recommended doing it by withholding DocPerm and called that *"almost certainly the
answer"* (`notes_gap_report.md` §B-2). **That is two-thirds true and the missing third is the one that
matters.**

#### I.2.1 The three surfaces, and why one of them is immune to Frappe permissions

FAC's own `tools/list` catalog was read live this session (`notes_close_repo.md` §1.1); the security
clauses below are quoted from it:

| FAC tool | Its own stated security model | What it consults |
|---|---|---|
| `get_document` | no security clause | DocPerm + `has_permission` hook |
| `list_documents` | no security clause | DocPerm + `permission_query_conditions` |
| `run_database_query` | *"Restricted to SELECT statements only. **Requires System Manager role for security.**"* | **a role check and a read-only-SQL check. Nothing else.** |
| `run_python_code` | *"…permission-checked… PRE-LOADED: pd, np, **frappe**, …"* | claims permission-checking, pre-loads `frappe` |
| `search_documents` | *"Global search across all accessible documents"* | Frappe global search |

This repo's own comment agrees and is the second independent source —
`erpnext_enhancements/assistant_tools/_gate.py:52-55`: *"Privileged-but-read-only: FAC enforces
read-only SQL for `run_database_query` (`utils/read_only_db.py`), so confirmation would be pure
friction."* Note what it claims (read-only) and what it does not claim (permission-checked).

> **The single most important sentence in this section: `run_database_query` cannot be closed by any
> permission mechanism Frappe offers, because it does not use one.** Raw SQL sits *underneath* DocPerm,
> `permission_query_conditions` and `has_permission`. Any answer that stops at "no DocPerm" leaves a
> System Manager one ``SELECT text, sender FROM `tabChat Message` `` away from every private message on
> the site, delivered into a model's context window. That is precisely what I5 forbids.

Two further facts that kill the simple answer:

- **"No DocPerm" is not "unreachable"; it is "unreachable by everyone except Administrator."**
  `frappe/permissions.py`, read live: `if user == "Administrator": … return True`, and in
  `get_role_permissions`, `if user == "Administrator": … return allow_everything(...)`
  (`notes_close_repo.md` §1.2.2). A zero-DocPerm DocType is still fully readable by Administrator
  through `/api/resource`, the desk, and therefore FAC's `get_document`.
- **The "Training Course doctrine" is an analogy, not a precedent.** All 187 non-child DocTypes this
  app ships carry at least one DocPerm row; `Training Course` itself has three (System Manager,
  Training Manager, Training Author). The doctrine is *"the **learner roles** hold no DocPerm"*, and
  its adversary is a `desk_access = 0` Website User — not a System Manager driving an MCP session
  (`notes_close_repo.md` §1.2.1-1.2.2). The ADR must present chat as the **first application** of the
  doctrine, with the Training text as its *rationale* and not as its *authority*.

**Also rejected: forking or patching FAC.** It is a third-party app in neither repo; the deploy only
does `git -C apps/erpnext_enhancements fetch && reset --hard` (`infra/cloudbuild-deploy.yaml:35-36`),
so a fork needs a second deploy path; and it inverts this app's own stated invariant — *"**Nothing
inside erpnext_enhancements may import this package.** The import direction is FAC → us… A tripwire
test in `tests/test_assistant_tools_schema.py` enforces this"* (`assistant_tools/README.md:49-59`, via
`notes_close_repo.md` §1.4).

#### I.2.2 The mechanism: `_gate.py`'s `_safe_execute` seam — already built, already in production

This app **already** intercepts every FAC tool call, including FAC's own built-ins, from inside
`erpnext_enhancements`, without forking anything. That is what the AI write gate is
(`assistant_tools/_gate.py`, docstring `:1-16`, wrap `:557-591`, applied at import time by
`assistant_tools/__init__.py:23-25`). Five properties make it the right seam
(`notes_close_repo.md` §1.5):

1. **It sees the tool name and the raw arguments for every tool, built-in included** —
   `_gated_execute(tool, original, arguments)` at `_gate.py:500`, reading `getattr(tool, "name", "")`
   (`:524`) and `(arguments or {}).get("doctype")` (`:525`). `get_document` and `list_documents` both
   take a literal `doctype` argument, so the denylist is a string comparison, not a parse.
2. **It is unconditionally in the request path** — `api/fac_endpoint` calls `_import_tools()` on every
   MCP request *before* dispatch, so the class-level wrap is in place before any tool executes in a
   fresh worker.
3. **It runs even when `ai_write_gating_enabled` is off.** The kill-switch is checked at
   `_gate.py:506-516`, *after* the function is entered. A denylist branch inserted **above line 502**
   therefore binds regardless of the settings flag — which matters, because that flag ships dormant
   (`assistant_tools/README.md:14-15`). **A denylist that can be switched off from a settings form is
   not an invariant.**
4. **It already fails closed and logs loudly** (`_gate.py:542-554`).
5. **It already has an upgrade canary** — `apply_gate()` writes an Error Log when the seam is missing,
   and `test_ai_gating_integration.test_gate_marker_present` fails on bench CI
   (`assistant_tools/README.md:38-40`; written against FAC v2.4.3).

#### I.2.3 The decision, in four layers

**Layer 1 — no DocPerm on the chat *content* DocTypes.** `Chat Message`, `Chat Room Member`,
`Chat Context Chunk`, `Chat Room Digest`, `Chat Thread Digest`, `Chat Retrieval Audit` ship with
**zero rows** in their `permissions` array. `Chat Room` is the documented exception and carries a
minimal `read` DocPerm plus its mandatory hook pair, because the socket join needs it — see §H.4.2,
where the collision is resolved and the deviation recorded.

**Layer 2 — a denylist in `_gate.py`, above the settings check.** One module-level
`CHAT_DENYLIST_DOCTYPES = frozenset({...})` alongside the existing `EXPLICIT_MUTATING` /
`EXPLICIT_READONLY` / `APP_MUTATING` sets (`_gate.py:41-106`), and one new **first** branch in
`_gated_execute`, before the `ai_gate_bypass` check at `_gate.py:502`:

- `arguments.get("doctype")` in the denylist → refuse via the existing `_error_response`
  (`_gate.py:488-494`, which already produces FAC's expected `{"success": False, "error": …}`
  envelope), with a message that **names the correct endpoint** rather than merely refusing;
- tool is `run_database_query` or `run_python_code` and the normalised query/code text contains any
  denylisted table name → same refusal;
- every refusal writes an `AI Action Log` row through the existing `insert_action_log`
  (`_gate.py:341-396`), so an attempt to read chat through a generic tool is **evidence, not
  silence**. That log is already append-only and already purged on a schedule
  (`hooks.py:688` → `ai_governance.tasks.purge_old_action_logs`).

**The one thing this handles awkwardly, said plainly rather than glossed:** `run_database_query` takes
a SQL string, not a `doctype`, so denylisting it means matching table names in query text.
String-matching SQL loses to backticks, whitespace, comments, case, `information_schema`, and joins
buried in subqueries. **Do not attempt to allow "safe" queries.** The rule is coarse and absolute: *if
the query text, case-folded with backticks and whitespace stripped, contains any chat table name,
refuse the whole call.* Over-refusal costs an analyst one rephrase; under-refusal costs the invariant.

**Layer 3 — the paired-hook obligation, deferred but written into the ADR as a rule.** *If any role is
ever granted a read DocPerm on a chat DocType — the likely trigger is wanting a desk list view for a
Chat Auditor — that commit must add **both** `permission_query_conditions` and `has_permission` in the
same change, per the exact 10-and-10 parity at `hooks.py:1123-1148` / `:1150-1164`, with a test
modelled on `tests/test_kpi_snapshot_permissions.py:9-12`.* The KPI Snapshot comment at
`hooks.py:1129-1135` is the exact analogue and should be cited: *"a DocPerm is doctype-wide — without
this, `read` on the doctype would have meant read on every department."*

**Layer 4 — Triton-side classification.** Chat tools added in Phase 5 get their annotations from the
same `annotations_for` function FAC forwards verbatim in `tools/list` (`_gate.py:147-172`).

#### I.2.4 The source-level test that proves it

Four assertions, all **bench-free**, in a new `erpnext_enhancements/tests/test_chat_mcp_denylist.py`
(`notes_close_repo.md` §1.7). Because it tests `_gate.py` under the stub set installed by
`test_assistant_tools_schema`, it is the one case where **appending to an existing multi-module
`unittest` step is right rather than wrong** — the step at `ci.yml:146-154`.

1. **The DocType JSONs carry no permissions.** Pure filesystem, modelled on
   `tests/test_doctype_modules.py`: walk `erpnext_enhancements/chat/doctype/*/*.json` and assert
   `permissions == []` for every non-`istable` DocType **except `Chat Room`**, which asserts the
   inverse (a DocPerm exists **and** both hooks are registered for it). *This is the assertion that
   catches the most likely regression* — somebody adding a System Manager row so they can look at a
   message in the desk.
2. **Every chat DocType name appears in `_gate.CHAT_DENYLIST_DOCTYPES`**, by set-equality against the
   filesystem-derived list. Same shape as the existing `test_every_registered_tool_is_classified`,
   which exists because an unclassified tool silently took the fail-closed branch — the identical
   failure mode.
3. **The gate refuses with gating OFF.** Set the settings flag disabled; call `_gated_execute` with a
   fake tool named `get_document` and `arguments={"doctype": "Chat Message", "name": "x"}`; assert the
   return is `{"success": False, …}` and that `original` was **never called** (a recording double).
   Repeat for `list_documents`, and for `run_database_query` with
   ``{"query": "select text from `tabChat Message`"}`` plus the obvious evasions: no backticks, mixed
   case, a leading `/* comment */`, a `JOIN` in a subquery, and `information_schema.columns` filtered
   on the table name. **The "gating OFF" precondition is the whole point** — it proves the refusal does
   not depend on `ai_write_gating_enabled`.
4. **The seam is still attached.** Cite the existing bench-CI canary rather than duplicating it — and
   **record the residual**: that canary runs only on a real bench, so the denylist's *attachment* is
   covered by a test CI does not run.

Plus one **Phase 6 manual acceptance step** that cannot be written bench-free: open an MCP session as a
real System Manager and issue `run_database_query` with ``SELECT COUNT(*) FROM `tabChat Message` `` —
expect the refusal envelope, not a number. This is R03's `T-15`.

> `VERIFY: that FAC's get_document uses frappe.get_doc/has_permission and list_documents uses frappe.get_list rather than frappe.get_all` — read `apps/frappe_assistant_core/**/tools/` on the prod bench; FAC is in neither repo, the GitHub raw URL 404s and DeepWiki has no per-tool detail (`notes_close_repo.md` §6 item 1). **Blocks:** whether Layer 1 closes two surfaces or one. **Does not change the recommendation**, which closes all three regardless.

> `VERIFY: whether frappe.db.sql is reachable inside run_python_code's sandbox` — the tool pre-loads `frappe`; run `print(frappe.db.sql("select 1"))` through it. **Blocks:** whether the denylist needs a `code`-argument branch as well as a `query` one (`notes_close_repo.md` §6 item 2).

> `VERIFY: that Frappe v16 __global_search read-back is permission-filtered for a zero-DocPerm doctype` — read `frappe/utils/global_search.py::search`. **Blocks:** whether `search_documents` needs its own branch (`notes_close_repo.md` §6 item 3). Independently, message text must **not** be flagged `in_global_search` (`notes_research_gaps.md:1123`).

### I.3 The gated entry point — one derived `allowed_rooms`, never an argument

**Commitment (I4), stated as a contract Phase 5 implements verbatim:**

- **One package, one public symbol.** `erpnext_enhancements/chat/retrieval/` exports exactly
  `retrieve(...)`. Everything else in the package is private.
- **`retrieve()` derives `allowed_rooms` itself** from the calling user's `Chat Room Member` rows (plus
  document-derived membership, §I.3.1) and **never accepts it as an argument**. There is no parameter,
  keyword or otherwise, by which a caller supplies room ids.
- **`restrict_to` intersects, never unions.** A caller may *narrow* the search to a subset it already
  has; the implementation is `allowed_rooms & frozenset(restrict_to)`. A `restrict_to` naming a room
  the user cannot see silently contributes nothing. (`T-9`.)
- **Every private search function takes `allowed_rooms` as a required first positional
  `frozenset[str]`** — not a keyword, not defaulted, not optional — so omitting it is a `TypeError` at
  import time rather than a leak at runtime.
- **`retrieve(user="Administrator")` raises.** Administrator short-circuits Frappe's permission stack
  entirely (§I.2.1), so "all rooms" would be the literal answer. (`T-8`.)
- All of this is R03 §4.2 as reproduced at `notes_research_gaps.md:1180`, adopted unchanged.

**The source-level test (`T-7`, `test_no_ungated_sql`) is the enforcement:** parse every Python file in
`erpnext_enhancements/` and assert that (a) no string literal containing a chat table name
(`tabChat Message`, `tabChat Room`, `tabChat Room Member`, `tabChat Context Chunk`, the digest tables)
appears outside `chat/retrieval/`, and (b) every function inside that package which builds SQL has
`allowed_rooms` as its first positional parameter. Bench-free, so it blocks the PR. Per `CLAUDE.md` and
`notes_close_repo.md` §4.4, if written pytest-style it needs **its own `python -m pytest <file> -q`
step** in `ci.yml` — there are nine such steps and **every one names exactly one file**; it must not be
appended to a unittest module list.

The functional companions are `T-2` (a message in a room the asking user is not in changes nothing and
appears in no tier) and `T-11` (room removal takes effect on the **very next** call — which is why
`triton:rooms:{user}` is on the never-cached list, §I.9.2).

#### I.3.1 Document-derived membership is materialised, and the reconciliation diff is an alert

Decision #10's per-document spaces mean some membership is *derived* from an ERPNext document's
permissions rather than from an explicit member row. Evaluating `frappe.has_permission(doc)` per
candidate row at retrieval time is both slow and unauditable, so membership is **materialised into
`Chat Room Member` rows** and maintained by hooks, with a **nightly reconciliation job whose non-empty
diff is an alert, not a routine correction** (`notes_research_gaps.md:1182`, R03 §4.4).

The open half is real and is deferred with a named owner: **nobody has enumerated the full set of
ERPNext events that change effective document visibility** (R03-V04/G06, DEFERRED to Phase 2 in
`notes_register_reconciled.md`). A missed event is a **silent over-permission** — a leak nothing alerts
on — which makes it the highest-consequence deferred item in the retrieval design. The enumeration must
be done against Frappe source: `frappe/share.py` (DocShare), `frappe/desk/form/assign_to.py`
(`_assign`/ToDo), `frappe/core/doctype/user/user.py` (deactivation), Role changes, and
`User Permission`.

### I.4 Permission-filter **before** ranking — the security requirement and the performance answer

**Commitment.** The candidate set is reduced to `allowed_rooms` in the SQL `WHERE` clause, *before* any
vector is loaded, any score computed, or any ranking applied. Filtering after ranking is forbidden even
where the post-filter would produce identical visible output.

Two independent reasons, pointing the same way — which is why this is an invariant, not an
optimisation:

- **Security.** Post-filtering means the ranker has already read content the user may not see. Any
  quantity derived from that read — a score distribution, a "top-K was mostly room X" heuristic, a
  count, a latency difference — is a side channel. More prosaically, post-filtering is one `return`
  statement away from being no filter at all, and that is the kind of bug that ships.
- **Performance.** This is also what makes the chosen vector backend viable. Phase 0 §4.J states the
  shape: a corpus-wide scan of ~100k chunks becomes a filtered candidate set under ~5k. In-process
  numpy cosine over 5k vectors is microseconds; over 100k it is visible in a web worker's profile.
  **`DECISIONS.md` D4's revisit triggers are written against the *filtered* candidate set (>20k)
  precisely because the filter is assumed to run first.**

### I.5 Vector storage — `DECISIONS.md` D4, its measured reason, and the alternative we are not taking

> **Seam note (assembly).** D4's word is "BLOB" and it is kept verbatim below, but **Frappe has no BLOB
> fieldtype**. The column is a `Long Text` holding base64 of the raw `numpy.float32` bytes — a
> refinement that satisfies D4's intent exactly, at a measured 33% storage overhead, with a raw
> `longblob` by patch recorded as the optimisation. See
> [§F.11.2](#f112-the-embedding-column--a-contradiction-with-decisionsmd-d4-reported).
> Seam **S4**.

**Decision (D4, binding): embeddings are stored as a BLOB on the chunk DocType and scored with
in-process numpy cosine over the permission-filtered candidate set, behind a two-method
`VectorBackend` adapter (`upsert(chunk_id, vec)`, `search(allowed_rooms, query_vec, limit)`) so the
backend is a one-file swap.**

**The reason is measured, not assumed.** Production MariaDB is **`10.11.18-MariaDB-0+deb12u1`** on
Debian 12, from a live `SELECT VERSION()`, corroborated by probing the feature surface — **zero**
`VEC%` routines, **zero** vector plugins (`notes_infra.md:33-83`). There is no `VECTOR` type and no
`VEC_*` functions. Phase 0 §4.J's *"on 11.8 the decision may flip for V1"* branch is therefore **closed
against the flip**. And `numpy 2.5.1` is already installed in the prod bench
(`notes_infra.md:990-1005`), so this adds **no dependency** — decisive on a host whose deploy pipeline
has **no `pip install` step at all** (`notes_infra.md:971-977`; ADR 0004's no-SDK doctrine).

**Revisit triggers, numeric and non-negotiable** (D4): p95 retrieval > 400 ms; > 20,000 candidate
chunks *after* filtering; > 250,000 chunks total; or a MariaDB upgrade to 11.8 LTS. Reaching any one
makes the adapter swap a scheduled task, not a debate.

**Companion commitment: the lexical tier is mandatory, not a fallback.** `FULLTEXT` on InnoDB via a
raw-DDL patch, queried in BOOLEAN MODE, fused with the vector tier by **Reciprocal Rank Fusion** —
never a linear combination of raw scores (`notes_research_gaps.md:1040-1042`). This is what makes an
exact invoice number outrank a topically similar chunk lacking it (`T-4`). Two InnoDB FULLTEXT
properties the implementer must know: it sees **committed rows only**, and **partitioned tables cannot
have FULLTEXT indexes** (`notes_research_gaps.md:473-477`).

> `VERIFY: whether bench migrate drops a hand-added FULLTEXT index` — R03-V10, DEFERRED to Phase 5. Add `FULLTEXT(body)` via raw DDL on a test site, run `bench migrate` **twice**, then `SHOW INDEX FROM \`tabChat Context Chunk\``. **Blocks:** if wrong, the lexical half of retrieval degrades **invisibly** — exact-number matching stops working and nothing errors.

**The genuine alternative, recorded as a human-visible option rather than a silent rejection: delegate
retrieval to Triton's existing Vertex AI RAG Engine.** Triton already runs it. Rejected for V1 on two
grounds:

1. **Triton's RAG is per-user corpora.** *"Every user gets their own RagCorpus… There is no shared
   corpus"* (`triton:backend/app/core/rag_corpus.py:7-14`, via `notes_infra.md:1120-1127`). Chat
   visibility is **membership-defined and shared** — a room's history is visible to N people and
   changes as membership changes. Per-user corpora do not map onto that: you would either duplicate
   every room's content into every member's corpus (N× storage, N× embedding cost, N places to redact
   on a delete) or build a shared corpus Triton does not have.
2. **Decision #6's gate must live in ERPNext** (§I.1). Moving the index out moves the filter out.

**If Nikolas prefers the Vertex route anyway**, the honest cost is a shared-corpus feature in Triton
that does not exist today, plus a re-answer to how §I.7's invalidation reaches a corpus ERPNext does
not own — deletion propagation being the hard part. → **CQ-23**.

Rejected once so it closes once: **Frappe's `FullTextSearch` / Whoosh wrapper**, which writes a
per-node on-disk index that is not shared across gunicorn workers and sits outside `bench backup`. That
reasoning holds regardless of Whoosh's maintenance status, which is why the register carries R03-V11 as
**ACCEPTED** rather than open.

### I.6 Summarization is scheduler-driven batch over dirty-room counters — never per-message enqueue

**Commitment.** Digest generation is a **cron scheduler job** (every 5 minutes) that selects rooms by a
dirty-counter predicate and summarises them in batch. There is **no** `frappe.enqueue` on the message
write path for summarization.

**The reason, and it is a specific trap with a specific source.** The natural design — enqueue a
summarize job per message with `job_id=f"digest-{room}"` and `deduplicate=True` so repeated messages
collapse — is silently broken. `frappe/utils/background_jobs.py`, quoted from source:

```python
    if deduplicate:
        if not job_id:
            frappe.throw(_("`job_id` paramater is required for deduplication."))
        job = get_job(job_id)
        if job and job.get_status(refresh=False) in (JobStatus.QUEUED, JobStatus.STARTED):
            frappe.logger().error(f"Not queueing job {job.id} because it is in queue already")
            return
```

**`deduplicate` drops the new enqueue when an existing job is `QUEUED` *or* `STARTED`.** A digest job
that is *currently running* therefore swallows every message arriving while it runs — and those are
exactly the messages that made the digest stale. In a busy room the job is always running, the drop is
always happening, and the digest never advances. It is a stall, it is silent (an `ERROR` log line and a
`return`), and it looks like "summaries are a bit behind" for weeks
(`notes_research_gaps.md:823-841`, quoting the source read by R03 §5.3; the load-bearing warning is at
`:840-841`, and R02-V10 carries it forward).

**The batch design, with its constants** (`notes_research_gaps.md:495-499`, R03 §5.1-5.3, §5.5):

| Item | Value |
|---|---|
| Scheduler cadence | cron every **5 minutes** |
| Selection predicate | `unsummarized_count >= 25 OR digest_dirty_since < now() - 15 minutes` |
| Resulting bounded staleness | **≤ ~20 minutes** |
| Room digest size | ~200–400 tokens |
| Thread digest threshold | one per thread exceeding ~40 messages |
| Full-rebuild triggers | `generation_count >= 20`, or `covered_to - covered_from > 90 days`, or `is_stale = 1` |
| Poison-pill guard | at `rebuild_failures = 3`, leave `is_stale = 1`, **stop scheduling rebuilds**, raise an Error Log / admin notification |
| Health alert | alert when `max(digest.generated_at)` is older than **1 hour** |

Two live facts that make this safe to build on:

- **The production scheduler is running.** Measured this session: `tabScheduled Job Log` shows a
  dead-steady 75–76 rows/hour for 30 consecutive hours with zero failures, 149 of 152
  `Scheduled Job Type` rows have `stopped = 0`, and the newest log was ~1 minute old at probe time
  (`notes_register_reconciled.md` R03-V05). The health check is insurance now, not discovery.
- **Cron key syntax is `scheduler_events = {..., "cron": {"*/5 * * * *": [...]}}`**, corroborated from
  a verified hooks example (`notes_research_gaps.md:847-857`), and **changing `scheduler_events`
  requires `bench migrate`** to register the `Scheduled Job Type` rows.

**And the clock trap that would otherwise corrupt every one of these predicates.** Measured this
session: the production DB is UTC (`SELECT @@session.time_zone, NOW(), UTC_TIMESTAMP()` → `SYSTEM`,
with `NOW() == UTC_TIMESTAMP()`), while Frappe writes `creation`/`modified` in **site-local** time —
six hours earlier (America/Denver, MDT = UTC−6). A naive
`TIMESTAMPDIFF(MINUTE, MAX(creation), NOW())` reports **361 minutes** for a row written one minute ago
(`notes_register_reconciled.md` C7). **Every SQL-side freshness comparison in this section — the dirty
predicate, the staleness alarm, the outbox-sweeper age, the cache-invalidation window — must use
`frappe.utils.now_datetime()` on both sides or convert explicitly.** This is a named invariant beside
D6 and matches the standing repo memory *"Frappe local time vs UTC"*.

Scheduler jobs run as **Administrator**, so the digest worker must `frappe.set_user(...)` explicitly
rather than inheriting it (`notes_research_gaps.md:855-857`) — and note that a digest job legitimately
reads across rooms with `ignore_permissions`, which is exactly why §I.7.3's audit rows and §I.2's
denylist both exist.

### I.7 Invalidation — an edit or delete inside a covered span forces a **full rebuild**

**Commitment (I10).** A rolling summary can add information but **cannot unsay it**. Therefore any edit
or delete whose message falls inside a digest's `[covered_from, covered_to]` span sets `is_stale = 1`
on that digest, and the rebuild is a **full regeneration from the source messages** — never an
incremental append. Retrieval **skips stale digests** outright rather than serving them with a caveat.

**This is the mechanism that stops Triton leaking redacted content through a summary**, and it is not
hypothetical: decision #12 requires deletes to propagate both ways while the audit trail survives in
ERPNext, and Google's own tombstones are **content-free** — `showDeleted` returns the tombstone but not
the body (`notes_google_verify.md:867-871`) — so ERPNext is the only place the original text exists. If
a digest generated before the delete is still served, the deleted sentence is still in Triton's
context, and the person who deleted it has no way to know.

#### I.7.1 It is tied to the three-value watermark, and only the three-value form works

`DECISIONS.md` D6 fixes the watermark as **`(max(seq), count(*), max(modified))`**, and
[§F.16.2](#f162-invariant-chat-watermark-1--the-three-value-watermark) specifies it. The reason it must be all three is exactly this section:

- **Edits and deletes do not advance `seq`.** A watermark tracking `seq` alone is unchanged by a
  delete, so the cache key is unchanged, so the cached context — containing the message the user just
  deleted — is served again. `max(modified)` catches the edit; `count(*)` catches the hard delete that
  neither `seq` nor `modified` would move.
- **Every digest, chunk and cache key uses the three-value watermark.** `Chat Room Digest` and
  `Chat Thread Digest` carry `watermark_seq` **and** `watermark_modified` as separate fields precisely
  so staleness is decidable without re-reading the message table
  (`notes_research_gaps.md:1094-1098`).
- R03 names this *"the single most common bug in this design; write the test first"*
  (`notes_research_gaps.md:1106-1110`).

**`T-6` is the test to write before any digest code exists:** edit a message and then delete it inside
a digest's covered span; assert `is_stale = 1`, the digest is **omitted** from retrieval, the message
appears in **no** tier, and the context cache key **changed**.

#### I.7.2 Detection

`Chat Message.on_update` guarded by `self.has_value_changed("text")`, plus the delete/tombstone path.
`has_value_changed` is long-standing but **unverified in v16 by any note** (R03-V06, DEFERRED to the
Phase 1 bench-probe batch): if it misbehaves, either digests never go stale — a governance failure
under decision #12 — or every save triggers a full rebuild. One `inspect.getsource` settles it, and
`T-6` should be written first regardless.

#### I.7.3 The audit boundary sits here too

**I9: every non-participant read is audited.** A `Chat Retrieval Audit` row (with a child
`Chat Retrieval Audit Room` per room, carrying `was_participant`) is **inserted and committed before
`retrieve()` returns content. If the audit write fails, retrieval fails**
(`notes_research_gaps.md:1183`, R03 §11.2). Reads by participants of their own rooms are not audited;
Triton reads and admin oversight reads are. Store `query_hash` (sha256) by default, with raw text
behind a `CHAT_AUDIT_STORE_QUERY_TEXT` flag that is **default off**, and the retention purge job **must
never delete audit rows** (R03 §11.3-11.4; `T-16`).

### I.8 The token budget — a hard ceiling with a deterministic degradation ladder

**Commitment (I11).** Assembly never exceeds the configured ceiling. When it degrades it does so in a
**fixed, ordered** way, sets an explicit `context_truncated` flag, and **Triton is instructed to tell
the user its view was cut**.

**The tier split** (R03 §8.2, reproduced at `notes_research_gaps.md:509-519`, adopted unchanged):

```python
CONTEXT_TOKEN_CEILING = 40_000   # retrieved chat context only — excludes system prompt & tool defs

BUDGET = {
    "T0_stable":   6_000,   # doc card + room digests   (prompt-cacheable)
    "T1_thread":  18_000,   # floor: 4_000 — never starved
    "T2_cross":   14_000,
    "T3_authored": 6_000,
    "reserve":     2_000,   # citation manifest + slack
}
```

- **Unused budget flows T1 → T2 → T3.** **T0 neither borrows nor lends**, because its size must stay
  stable for the prompt cache (§I.9).
- **40,000 is a deliberate choice, not a model limit.** Gemini's window is far larger; the ceiling
  exists because cost and latency scale with input tokens on *every* `@triton` mention, and because
  recall past a few tens of thousands of tokens is not free (`notes_research_gaps.md:1048-1052`).
  **Make it a config value, log the realised token count on every turn, and revisit with data after two
  weeks.** → **CQ-17**.

**The degradation ladder, fixed and ordered** (R03 §8.3, `notes_research_gaps.md:1054-1060`):

1. Drop lowest-ranked **T3** chunks.
2. Drop lowest-ranked **T2** chunks, but **keep at least the top 3**.
3. Replace remaining T2 chunk bodies with room-digest lines.
4. Compress **T1** — thread root + last N verbatim, middle replaced by the `Chat Thread Digest`.
5. **Hard floor** — system prefix + the **last 20 messages** of the current thread, then truncate
   oldest-first.

**Whenever rung 4 or 5 fires, set `context_truncated = true` and instruct Triton to say so in its
reply. Surfacing truncation is a correctness feature, not a UX nicety** — a model that silently answers
from a cut view produces a confident wrong answer and the user gets no signal. `T-5` asserts the ladder
fires in fixed order, that T1 never drops below its 4,000-token floor, and that the flag is set at
rungs 4–5.

**The counting risk that is baked in at seal time.** `token_count` is computed once when a chunk is
sealed and every budgeting decision is pure arithmetic over it, so a systematically wrong count
silently over- or under-fills the ceiling. **Add a `token_count_method` field either way**, so a later
correction pass can find the estimates; fall back to `ceil(len(text)/4) × 1.15` if no counter is usable
in a background-job context (R03-V12, DEFERRED to Phase 5).

The 40k figure also rests on a long-context recall assumption nobody has measured against the pinned
model (R03-V01, DEFERRED to Phase 5: a needle-in-haystack eval at 10k/20k/40k/80k with the fact at
varying depths). If the pinned model does not degrade, the ceiling can rise and the ladder can relax.

### I.9 Prompt-cache ordering as a testable invariant

**Commitment (I12).** For a fixed user, the concatenation of the stable segments is **byte-identical**
across two assemblies unless a **named** trigger has fired. `assemble(user, room, watermark, budget)`
is a **pure function of its arguments** (`notes_research_gaps.md:1069-1071`).

**The segment order, S0…S5, non-decreasing in volatility** (R03 §9.2):

| Segment | Content | Invalidation trigger |
|---|---|---|
| **S0** | system prompt / persona / tool definitions / citation instructions | deploy |
| **S1** | org glossary | fixture edit |
| **S2** | the asking user's profile card | user record edit |
| **S3** | **T0** — pinned document card + stable room digests, tagged with `digest_version` | digest republish, or the pinned doc's `modified` |
| **S4** | **T2 + T3** retrieved chunks in rank order | every turn |
| **S5** | **T1** current thread verbatim + the live question | always |

**Two rules, both testable:**

- **Rule 1 — no `now()` above S5.** *"A single volatile character at the front invalidates the entire
  prefix and silently costs the full 90% discount on every request, with no error and no log line"*
  (`notes_research_gaps.md:1073-1075`). `T-1` assembles twice with unchanged inputs and asserts the
  prefix bytes match exactly.
- **Rule 2 — the prefix's *identity* must be stable, and that is not the same as its size.** R03's
  form of this rule is about *implicit* prefix caching: below the threshold implicit caching never
  engages, so at ~3,000 tokens **the correct move is to make the prefix bigger, not to trim it**
  (`notes_research_gaps.md:1075-1077`). **That mechanism is not the one Triton uses** — see
  §I.9.1, which establishes that Triton runs explicit Gemini `CachedContent` keyed by a hash over
  `{system, history, tools fingerprint, fac_catalog}`, where size is irrelevant and *identity* is
  everything. `T-14` therefore asserts the thing that actually breaks us: that the toolset
  fingerprint is byte-stable across two assemblies with unchanged inputs, because a tool appearing,
  vanishing or changing shape is baked into the cache identity by design and silently forces a full
  rebuild. **Do not implement `T-14` as a token-count warning against the 4,096 implicit threshold**
  — that would be a test against a mechanism this deployment does not use.

#### I.9.1 Does Triton's current prompt assembly already violate this? — **No. The win is already taken.**

The register lists this as an *"independent quick win"* (R03-G04) and the critic's table omits it
entirely. It is **RESOLVED, in the direction that means there is nothing to schedule**
(`notes_register_reconciled.md` §(a) row 14, citing `notes_triton_audit.md:793-874`):

- `TritonIntelligence._build_system_instruction` (`triton:backend/app/core/intelligence.py:275-306`) is
  a **pure function of `(persona, job_title, custom_instructions)`**, with the invariant written into
  its own docstring: *"A clock read or an unordered iteration in here would make the hash flap and
  force a cache rebuild on every single turn."* There is no `datetime`, session id or request id in it.
- Volatile content is **deliberately placed on the user turn**, not the prefix: *"Cache the stable
  parts (system instruction + history). RAG snippets and drive context vary per query — they go on the
  user turn so the cache survives across tool-loop iterations rather than thrashing on every message"*
  (`intelligence.py:1115-1118`; the split itself at `:1138-1156`).
- The discipline is itself an ADR in the Triton repo:
  `triton:docs/decisions/0006-cache-layering-persona-stable-rag-volatile.md`, whose consequence line is
  *"Where you put context is a cost decision, not an ergonomic one."*
- **The ERPNext widget's page-context preamble is correct by construction**: `_build_prompt`
  (`erpnext_enhancements/triton_chat.py:194-236`) prepends it on the ERPNext side, so it arrives inside
  `ChatQuery.prompt` → `USER QUERY:` on the **volatile** turn (`notes_triton_audit.md:931-948`). Adding
  chat context the same way does not touch the cached prefix.

**One correction the ADR must carry, because it changes what "the threshold" means.** R03 reasons about
*implicit* prefix caching and a 4,096-token minimum. **Triton does not use implicit caching.** It uses
**explicit Gemini `CachedContent`** (`triton:backend/app/core/gemini.py:182-239`, TTL **3600 s**),
keyed by a SHA-256 over a `sort_keys=True` payload of
`{system, history, tools fingerprint, fac_catalog}` (`intelligence.py:402-415`). `datetime.now()` **is**
called in that function — but only to compare against `cached_content_expires_at`; it never enters the
digest payload and never enters the prompt (`notes_triton_audit.md:876-908`). **Anything in this ADR
that sizes a "stable prefix" against the implicit threshold is sizing against the wrong mechanism**
(`notes_register_reconciled.md` C6). Rule 2 therefore reads, for us: keep S0–S3 stable **and keep the
toolset fingerprint stable**, because a tool appearing, vanishing or changing shape is baked into the
cache identity by design and forces a rebuild — *"the toolset is baked into the cache, so it is part of
the cache's identity"* (`intelligence.py:402-415`).

**And know which turns run uncached before adding a surface** (`intelligence.py:1127-1136`): any turn
with attachments, or with Search / Maps / code execution enabled, runs uncached by deliberate design;
and the **first turn of a session is never cached** (`intelligence.py:446-453`). A chat-context addition
that only ever appears on a first turn buys nothing.

> `VERIFY: the prompt-cache thresholds and discounts for the pinned model` — R03-V13, DEFERRED to Phase 5. Re-read the Vertex context-cache reference at implementation time and **log the per-turn `cachedContentTokenCount`** to confirm the cache is actually engaging. **Blocks:** nothing structural; sizing only.

#### I.9.2 Cache keys and TTLs

Adopted from R03 §10.3 (`notes_research_gaps.md:539-549`), with `{watermark}` being the three-value
form from §I.7.1:

| Key | Value | TTL | Invalidation |
|---|---|---|---|
| `triton:ctx:{user}:{room}:{watermark}:{budget_hash}` | assembled `RetrievalResult` | 900 s | watermark in key |
| `triton:emb:q:{sha256(query)}:{model}:{dim}` | query embedding | 3600 s | model/dim in key |
| `triton:digest:{room}:{digest_version}` | rendered digest text | 86400 s | version in key |
| `triton:chunkvec:{chunk}:{embedding_version}` | float32 vector | 3600 s | version in key |
| `triton:rooms:{user}` | — | — | **never cached** (R03 §4.2 rule 6) — this is what makes `T-11` true |

Stampede guard `LOCK_TTL = 30`, poll delays `(0.05, 0.1, 0.2, 0.4, 0.8, 1.6)`, then **build anyway
rather than block the user**. Three live constraints apply to all of it: `redis_cache` and
`redis_socketio` are the **same instance and DB 0** so keys must be distinctly namespaced;
`maxmemory-policy` is `allkeys-lru` so **a key can be evicted before its TTL**
(`notes_infra.md:582-587`, `:614-617`); and the deploy FLUSHDBs it, so every consumer must treat a miss
as normal (§J.7).

> `VERIFY: whether frappe.cache()'s raw .set(nx=, ex=) bypasses site-key prefixing` — R03-V14, DEFERRED to the Phase 1 bench-probe batch. Read `frappe/utils/redis_wrapper.py`; `frappe.cache().set("k",1)` then inspect the raw keyspace. **Blocks:** an unprefixed lock key is a cross-site collision — low blast radius on a single-site bench, one-line fix, invisible bug.

### I.10 Citations — and the honest connection to what the widget actually renders

**Commitment.** Citations are **assembly-order integer ids** (`[[ref:N]]`) whose **manifest is known
before generation**, so the manifest streams **first** and inline links resolve live as tokens arrive.
Unknown ids are **dropped silently with a metric** (R03 §12, `notes_research_gaps.md:1185`).

| Item | Value |
|---|---|
| Wire order | `citations` → `token` → `citations_append` → `token` → `done` |
| Context-line label format | `⟦ref:K⟧ Author Name (2026-08-05 14:02, America/Chicago): body` |
| Model-emitted format | `[[ref:N]]` |
| Tolerant parse regex | `\[\[\s*ref\s*:\s*(\d+)\s*\]\]` |
| Stream tail buffer | up to `MAX_TOKEN_LEN` (~16) chars, so a citation split across two SSE frames renders as **one** anchor (`T-13`) |
| Health signal | a `citation_miss` rate above ~2% over a rolling window is a **prompt regression signal**, not a UI bug |
| Rendering | anchors built with **DOM APIs, never `innerHTML`** |

The DOM rule is not decorative. The widget renders assistant text through `frappe.markdown(...)`
(`triton_widget.js:47-53`), and whether the deployed Frappe sanitises that output is itself an open item
(`notes_widget_inventory.md` V1: *"if not, it is a **live XSS finding**, not a design input"*). Building
citation anchors with `createElement`/`textContent` — already how `renderSources` escapes labels
(`triton_widget.js:1101-1102`) — keeps inline citations out of that blast radius entirely.

#### I.10.1 What "the sources dropdown is preserved exactly" actually means

Locked decision #7 says the **sources dropdown (citations panel) is preserved exactly**. Phase 0 §4.J
argues inline links cannot regress it *because* the panel is populated from the retrieval manifest
rather than from what the model cited — **but that argument only holds if the current behaviour is
known, and the current behaviour is not what the criterion assumes.** Per `DECISIONS.md` D8 this must be
restated in the same sentence as the answer, or a reviewer scores a correct finding as a miss:

- **The ERPNext widget has no dropdown.** `renderSources` (`triton_widget.js:1085-1106`) builds a
  **flat, always-visible, non-collapsible chip row**. It reads only
  `s.label || s.title || s.url || "source"` and `s.url`. It **never reads `s.kind` or `s.subtitle`**,
  which Triton always populates. There is no sort order (insertion order only), no per-kind icon, no
  subtitle line, no grouping, and no client-side dedupe. A source with no `url` renders as an inert
  `<span>`. **It does not open, because it is always open** (`notes_widget_inventory.md:530-576`).
- **The dropdown the prompt describes is in the Triton web app** —
  `triton:frontend/src/views/ChatView.vue:363-381`, a collapsible accordion labelled "{n} Sources"
  rendering `kind`, `label` and `subtitle || url`. **The ERPNext widget is a strictly poorer renderer of
  the same payload.**
- **The all-retrieved-vs-only-cited answer is hybrid and path-dependent**, decided entirely Triton-side
  (`notes_widget_inventory.md:610-674`; `notes_triton_audit.md:2001-2094`):
  - **tool-call sources: ALL RETRIEVED, unconditionally** (`triton:.../sources.py:286-299`) — a 20-row
    list query yields 20 chips whether or not the model mentioned one;
  - **semantic-search sources: ONLY CITED, by a substring heuristic** — `_referenced_in_text`
    (`sources.py:273-283`) lower-cases the answer and asks whether the label, subtitle or trailing URL
    segment (≥3 chars) appears in it, as a plain `in` test with no token boundaries: **"Pond A" matches
    inside "Pond Alpha"**;
  - on the **deployed Reasoning Engine path** context sources are **hard-coded to `[]`**
    (`reasoning_engine.py:249`), so that path is purely all-retrieved;
  - on the **orchestrator path** there is **no filtering at all** (`orchestrator.py:221-225`);
  - which path runs is decided per turn (`streaming.py:336-363`), and **a user with a persona selected
    in the widget gets different sources semantics than a user without one** — the widget always sends
    `persona_key` (`triton_widget.js:1285`), `""` when none is chosen, which is falsy in Python, so the
    default is the deployed path;
  - `dedupe` additionally **drops any source with an empty `url`** (`sources.py:258-270`).

**What this means for decision #7's "preserved exactly".** Introducing a real `[[ref:N]]` protocol
changes behaviour on the in-process path (currently substring-filtered) and on the orchestrator path
(currently unfiltered), so **all three call sites must change together**:
`triton:backend/app/core/sources.py:286` (`merge_and_filter`),
`triton:backend/app/core/reasoning_engine.py:249`, `triton:backend/app/core/intelligence.py:1195`, plus
`triton:backend/app/core/orchestrator.py:224`. Changing one is how you get a citation protocol that
works for some users and not others, split by whether they picked a persona.

Two ways to satisfy "preserved exactly", and **the choice is Nikolas's, not ours**:

- **(a) Strict preservation.** The chip row keeps rendering exactly what it renders today — the same
  hybrid, path-dependent array — and inline `[[ref:N]]` links are purely additive. Zero regression risk;
  the chips stay inconsistent with the inline citations, because a cited chunk may have no chip when the
  substring heuristic missed it.
- **(b) Manifest-backed chips.** The chip row renders the **full retrieval manifest**, with entries the
  model actually cited **marked** (bold, a check, or ordered first). Strictly more useful, makes chips
  and inline links consistent, and lets us finally read `s.kind`/`s.subtitle`, which the widget already
  receives and discards. It is also, unambiguously, a **change** to the preserved surface.

→ **CQ-14.** Our recommendation is (b) for chat-sourced citations only, leaving today's behaviour
untouched for non-chat sources — but it is a change to a locked decision's surface and must be asked,
not assumed.

One further finding that bounds how much citation value exists today: `sources.py` has tool-specific
builders for exactly nine tool names, so **every domain `fac_*` tool from `erpnext_enhancements`
(`maintenance_day_board`, `project_status_overview`, …) contributes zero citations**
(`notes_triton_audit.md:2075-2080`, R10).

> `VERIFY: that {FRAPPE_BASE_URL}/desk/{slug}/{name} resolves on Frappe v16` — Triton builds ERPNext source URLs as `/desk/{slug}/{name}`, not `/app/...`, and the code itself flags this as unusual (`triton:.../sources.py:26-28`). **If it does not resolve, every ERPNext citation chip in the widget is already a dead link today and inline citations would inherit the bug.** One browser open of `{site}/desk/task/TASK-0001` (`notes_widget_inventory.md:601-608`; `notes_gap_report.md` §E item 19).

### I.11 Triton's identity — I13, and the rollout task hiding inside it

**Commitment (I13). Triton's ERPNext permissions and Triton's Chat posting identity are two different
things and are never merged:**

| Concern | Identity | Mechanism |
|---|---|---|
| **Reading chat context; calling ERPNext tools; acting on data** | **the mentioning human** | the gated `retrieve()` and FAC tools, over that person's own OAuth2 bearer (§I.1) |
| **Posting Triton's answer into Google Chat** | **the bot / Chat app identity** (`chat.bot`) | app auth; Triton *should* be bot-badged, and app auth is the only mode that permits `cardsV2` (`DECISIONS.md` D3) |

**Conflating them is how a superuser service account gets built**, which decision #5 forbids. The
failure mode is concrete and seductive: the relay already needs a machine credential to post as the
bot, and reusing that credential for the *read* half would make the code simpler and would silently give
Triton every user's data. The test that pins it (I13's): **assert the retrieval call carries the
mentioning user's credentials while the Chat write carries the app identity** — two assertions on one
turn.

The impersonation grant that makes the read half work already exists in production:
`POST /api/v1/auth/erpnext-bridge/token` exchanges an email for that person's short-lived Triton JWT,
authenticating machine-to-machine with a shared gateway secret
(`triton:backend/app/api/v1/endpoints/erpnext_bridge.py:5-8`). **The shared secret authorises the
impersonation request, not the chat turn** — it is a token-exchange grant, not a superuser session
(`notes_triton_audit.md:676-689`). Two shapes are available, both small in Triton
(`notes_triton_audit.md:751-776`): reuse the existing bridge (~0 lines in Triton; the endpoint and secret
keep saying "erpnext" while serving Chat, and that secret is *also* the telephony gateway secret at
`triton:backend/app/api/telephony_gateway.py:42`), or mint a sibling bridge (~150 lines). The naming
smell is real; the security posture is identical. → **CQ-24**.

#### I.11.1 The rollout task that looks like a Phase 5 bug and is actually a Phase 0 obligation

**Every human Triton acts for must have completed the ERPNext OAuth link first, or the turn dies before
the first token.** `FrappeClient.__init__` raises `FrappeAuthRequired` when a user-scoped construction
finds no OAuth row and no legacy key (`triton:backend/app/core/frappe.py:151-155`), and
`TritonIntelligence.__init__` constructs the client **eagerly** at `intelligence.py:247`. On the SSE
path that lands in the broad `except Exception` at `streaming.py:460` and the user sees **"Communication
disruption detected."** — not a "link ERPNext" prompt; the structured `401 erpnext_link_required`
contract applies only to non-streaming routes (`notes_triton_audit.md:725-748`, R1/R2).

**Auto-provisioning a Triton `User` — which the bridge does — does not auto-provision an ERPNext
grant.** So *"every coworker who may be in a room where someone types `@triton` must have clicked 'Link
ERPNext' before that happens"* is a **rollout task with a named owner and a completion check**, not a
Phase 5 implementation detail. It belongs in Appendix B's Phase 1 and Phase 6 rollout steps and in the
checkpoint. At the measured roster of ~20 active users (`notes_infra.md:140-151`;
`notes_register_reconciled.md` C4) it is an afternoon — but only if someone schedules the afternoon.

The graceful-degradation question that rides with it — mandatory prompt, degrade to
read-only-without-ERPNext, or refuse to answer (`notes_triton_audit.md:2126-2127`) — is folded into
**CQ-24**.

### I.12 Does Triton's own chat post route through the pending-action flow?

**The requirement.** `triton:CLAUDE.md:56-58` is unambiguous: *"Any AI tool that mutates an external
system must route through the pending-action flow… Bypassing it is a security regression, not a
shortcut"*, and `triton:docs/decisions/0003-confirmation-gate-for-ai-writes.md:33-35` repeats it — *"This
is a security boundary, not a UX pattern."* **A Google Chat post is a mutation of an external system.
Under the rule as written, it needs confirmation.**

**And the rule as written produces an absurd product**, which is why this is escalated rather than
decided: a user types `@triton what's the status of PRJ-00580`, and Triton renders an approval card
asking permission to *answer them*. That is not a security boundary; it is a broken chat bot.

**What the mechanics require if Triton's post is gated** — from `notes_triton_audit.md:1296-1332`, the
five that bite:

| # | Requirement | Why it fails without it |
|---|---|---|
| 2 | The tool name added to `MUTATION_TOOLS` (`tool_defs.py:1223`) | The prefix classifiers cover only `fac_`/`qbo_`; a new `gchat_` prefix matches **neither**, so the static set is the only thing that can gate it |
| 4 | Risk classification (`tool_defs.py:1425`) | Defaults to `"medium"`, so `_describe_action` is skipped (`actions.py:135`) and the card gets no plain-English explanation. Posting publicly on someone's behalf arguably wants `"high"` |
| 5 | A prefix arm in `integration_for_tool` (`triton:backend/app/core/tools.py:18-37`) | Audit rows get `integration="unknown"` |
| 6 | **A dispatch arm in `actions._dispatch` (`actions.py:462-474`)** | **`confirm_action` raises `ValueError(f"Unknown integration for tool {name}")` — approval fails at execution time, *after* the row is already `approved`.** `_dispatch` knows only `fac_`, `gws_`, `qbo_`. This is the sharpest edge (R4) |
| 9 | Per-user Google credentials carrying a Chat scope | `GoogleWorkspaceClient(db, user)` already carries per-user OAuth (`intelligence.py:246`), so the plumbing exists; the **scope** is the open question |

**And the UX consequence that has no answer today (R3):** the confirmation card is rendered by a chat
client. If Triton is answering **inside Google Chat**, there is no Vue card and no Frappe card — *the
`pending_action` frame arrives and nothing on that surface draws it*
(`notes_triton_audit.md:1319-1324`). A Chat card with an approve button would work and would **not**
violate the desk-only doctrine, because a card button is a **human click, not a model call** — the
doctrine exists because *"a model-callable confirm would collapse the human-in-the-loop guarantee under
prompt injection"* (`assistant_tools/README.md:26-29`, `_gate.py:19-22`). That reasoning must be
restated explicitly wherever the exemption is granted.

**Our recommendation, offered as an explicit human decision and not taken silently:**

> **Exempt exactly one thing: Triton's own conversational reply to a mention, posted into the same room
> and thread as the mention, containing no tool-driven external mutation.** Everything else Triton does
> from a chat turn — creating an ERPNext document, sending an email, posting into a *different* space,
> adding or removing space members, deleting a message — stays gated.

The narrow exemption is defensible on the boundary the doctrine actually protects: the reply is
addressed to the person who invoked it, in the place they invoked it, and its content is the answer they
asked for. It is not an *action taken on the world*; it is the turn completing. Bolt three conditions
onto it so it cannot widen by accident:

1. The reply may target only the room and thread of the triggering mention — never a room derived from
   model output.
2. The reply is posted by the **bot identity** (§I.11), so it is visibly attributed and cannot
   impersonate the human.
3. Every exempt post still writes an `IntegrationAuditLog` row, so "not confirmed" never means "not
   recorded". Triton already logs non-mutating tool executions from `_execute_tool`
   (`intelligence.py:1507-1515`), so this is a small extension, not a new mechanism.

→ **CQ-18.** If Nikolas declines the exemption, the answer is not "gate it anyway" — it is that **Phase
5 must build an approval surface inside Google Chat** (R3), which is a design task with a schedule, and
Appendix B must carry it as such.

#### I.12.1 One inherited constraint on any Phase 5 refactor of the relay

`triton_chat.py::stream_query` (`:539-601`) **streams** — `stream=True` upstream,
`r.iter_content(chunk_size=None)` yielding straight through, wrapped in a `werkzeug` Response with
`X-Accel-Buffering: no`. Nothing is accumulated. And its docstring states the property that constrains
every refactor: *"Everything the generator needs is captured before we hand the Response back, so the
lazy body never touches Frappe's request/DB context after teardown"* (`:546-547`).

**The generator runs after Frappe has torn down the request** — no `frappe.session`, no usable
`frappe.db`, no site context. **A Phase 5 change that wants to persist assistant chunks into a
`Chat Message` as they arrive cannot do it inside this generator.** The three options are: buffer and
write once after the stream closes in a follow-up request; write from a `frappe.enqueue`d job seeded
*before* the Response is returned; or re-establish site context inside the generator
(`frappe.init`/`frappe.connect`), which is a substantially larger change and must be called out as such
(`notes_close_repo.md` §4.3).

Failures arrive as **in-band SSE frames**, not HTTP errors (`_sse_error`, `:535-536`), so a transport
failure and a Triton 500 look identical to the client — which matters for §J.1, where the load balancer
truncating the stream is indistinguishable from the stream ending.

**And the only automated coverage of any of this has never run.** `tests/test_triton_personas.py` is a
bench-free **pytest** suite whose three `stream_query` assertions are the sole automated statements
about the relay, and `grep -c "test_triton_personas" .github/workflows/ci.yml` → **0**
(`notes_gap_report.md` §0-11; independently confirmed at `notes_close_repo.md` §4.4). Fixing it is two
lines appended to the `unit-tests` job:

```yaml
      - name: Triton persona proxy + SSE relay (bench-free pytest suite)
        run: python -m pytest erpnext_enhancements/tests/test_triton_personas.py -q
```

**It should be the first commit of the chat work — before Phase 5, not in it** — because every
"streaming must survive" row in Appendix A is otherwise defended by a file that has never executed.
→ **CQ-25**.

---

## J. Networking and infrastructure risks

*(Phase 0 §4.K.)*

Each risk below carries its **fix** and its **evidence**. Two of them (J.1, J.7) are already shaping
production behaviour today without anyone having decided that they should.

### J.1 The GCLB backend-service timeout defaults to 30 seconds

**The finding.** Terraform **never sets `timeout_sec` on any backend service.** The production
declaration is `infra/configs/load_balancer.yaml:33-39` — `backends`, `health_checks`, `protocol`,
`port_name`, and nothing else. The vendored module *supports* it
(`modules/net-lb-app-ext/variables-backend-service.tf:38` → `backend-service.tf:75`), so passing `null`
leaves `google_compute_backend_service.timeout_sec` unset and GCP applies its default. A repo-wide grep
for `timeout_sec` across `infra/` returns **only health-check timeouts**
(`infra/mig.tf:75`, `infra/compute.tf:410`, `infra/variables.tf:703-705`). **Nothing about the backend
request timeout is recorded anywhere in the repo** (`notes_infra.md:792-828`).

**What GCP does, fetched live** (<https://docs.cloud.google.com/load-balancing/docs/https/request-distribution>,
via `notes_infra.md:830-844`):

- Default backend-service timeout for global external Application Load Balancers: **30 seconds** — *"the
  maximum amount of time allowed between the load balancer sending the first byte of a request to the
  backend and the backend returning the last byte of the HTTP response."*
- *"Active websocket connections don't use the configured backend service timeout of the load balancer.
  The connections are automatically closed after 24 hours (86,400 seconds)."*
- **Idle** WebSocket connections **are** closed when the backend-service timeout expires.
- The backend HTTP keepalive is fixed at 600 s and *"doesn't apply to websockets."*

**Why production works today, and why the margin is five seconds.** Frappe v16's socket.io server passes
**no ping options** — `new Server(server, { cors: {...}, cleanupEmptyChildNamespaces: true })`, fetched
from `frappe/version-16/realtime/index.js` — so `pingInterval`, `pingTimeout`, `transports`, `path` and
`adapter` are all socket.io defaults, i.e. **`pingInterval = 25000 ms`**
(<https://socket.io/docs/v4/server-options/>). **The server pings every 25 s, which makes the connection
non-idle, which keeps it under a 30 s idle cut — by five seconds**
(`notes_infra.md:846-863`). *That is the entire reason `collab_focus` presence and
`project_dashboard_updated` work in production today. It is luck with a thin margin, not design.*
Anything that perturbs it — a GFE hiccup, a backgrounded tab whose browser throttles the pong, a
socket.io upgrade that raises `pingInterval` — starts producing reconnect churn. **This is also why chat
presence does not reuse the 30 s constant** (§H.3.1).

**And SSE is worse, because the timeout is a total budget.** The existing Triton relay
(`erpnext_enhancements/triton_chat.py:539-601`) returns a streaming `werkzeug` Response with
`{"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}` and a client-configured read timeout
defaulting to **120 s** (`triton_chat.py:75`). **That is a plain HTTP response, not a WebSocket, so the
30 s backend-service timeout applies as a total first-byte-of-request → last-byte-of-response budget,
regardless of how much data is flowing.** A Triton answer that takes longer is **cut off by the load
balancer and the browser sees a truncated stream, not an error** — and because this relay signals
failure with in-band SSE frames (§I.12.1), the client cannot distinguish "the LB cut us off" from "the
stream ended" (`notes_infra.md:865-875`; `notes_close_repo.md` §4.3). **The ADR records this as an
existing defect that Phase 5 inherits, not one it creates.**

**The corollary nobody should skip:** if long Triton answers *do* work in production today, someone has
already raised the timeout out of band, Terraform does not know, and **a `terraform apply` would
silently revert it**. That is a live risk, not a hypothetical.

**Read the current value (read-only, safe):**

```bash
gcloud compute backend-services describe production-glb-production-vm-backend \
  --global --project=erpnext-465317 \
  --format="yaml(name,timeoutSec,securityPolicy,edgeSecurityPolicy,sessionAffinity,protocol,portName,connectionDraining,healthChecks)"
```

This is `notes_infra.md` V-1 merged with the Cloud Armor question per `notes_gap_report.md` §0-1 — **one
command answers four questions**. Both environments at once:

```bash
gcloud compute backend-services list --project=erpnext-465317 --global \
  --format="table(name,timeoutSec,protocol,port,portName)"
```

**Set it (mutating — a human runs this):**

```bash
gcloud compute backend-services update production-glb-production-vm-backend \
  --global --project=erpnext-465317 --timeout=3600
gcloud compute backend-services update spot-glb-spot-vm-backend \
  --global --project=erpnext-465317 --timeout=3600
```

**And mirror it into Terraform in the same change, or it will be reverted** —
`infra/configs/load_balancer.yaml`, both the production block at `:33-39` and the spot block at `:8-14`:

```yaml
      timeout_sec: 3600          # GCP default is 30s, which closes idle WebSockets
                                 # and truncates SSE responses longer than 30s.
```

It flows through `infra/load_balancer.tf:83-102` → `modules/net-lb-app-ext/backend-service.tf:75` with
no module change. **Run `terraform plan` first; do not apply from an agent.** 3600 s is an order of
magnitude below the 24-hour hard cap, and if the maximum turns out to be lower, the `update` command
fails **visibly** at the moment of the change — a loud, cheap failure (R04-V16, ACCEPTED).

**Defence in depth, both required regardless of the timeout:** socket.io's pings (already present, but
now with a real margin rather than five seconds), and an explicit **`: keepalive` SSE comment
heartbeat** on any long-lived stream — Triton's SSE has **no heartbeat today** and relies on
`tool_status` frames to keep the connection warm (`notes_triton_audit.md`, R7).

> **`VERIFY: the live timeout must be OBSERVED in a browser on production, not inferred.`** R04's own §14 check 11 requires **a WebSocket open >60 s and an SSE stream surviving 60 s idle, observed empirically**, and says Phase 0 is not complete without it. **Phase 0 as scoped is read-only and cannot run it, so Phase 0 closes with C11 UNEXECUTED and the ADR says so rather than implying the gate passed** (`notes_register_reconciled.md` C3). Order of operations in Phase 1: the read-only `describe` first, then the browser observation, then the `update` + Terraform mirror.

### J.2 Cloud Armor — **no WAF is attached today**, so this is a precondition, not a fix

**The verified finding.** Phase 0 §4.K and §7 require the Cloud Armor false-positive risk to appear with
its fix, and **no notes file mentioned Cloud Armor, WAF, OWASP or `security_policy` at all** until the
critic closed it (`notes_gap_report.md` §0-1). Read from the repo this session:

- The vendored module supports an attachment point:
  `modules/net-lb-app-ext/variables-backend-service.tf:36` (`security_policy = optional(string)`) wired
  at `modules/net-lb-app-ext/backend-service.tf:73`; plus an edge policy at
  `modules/net-lb-app-ext/variables.tf:25` wired at `modules/net-lb-app-ext/backends.tf:31`.
- **The actual config sets neither.** `infra/configs/load_balancer.yaml:26-49` declares
  `production-vm-backend` with no `security_policy` and no `edge_security_policy`; same for
  `spot-vm-backend` at `:1-25`.
- **No `google_compute_security_policy` resource exists anywhere in the repo** —
  `grep -rn "google_compute_security_policy" --include=*.tf .` across the worktree returns nothing.

**Therefore: as declared in Terraform there is no Cloud Armor policy in front of ERPNext today.** The
OWASP SQLi/XSS false-positive risk Phase 0 §4.K describes — a coworker pasting a SQL snippet or an HTML
tag into a chat message and getting a 403 with no feedback — is a **future** risk, and the correct ADR
statement is a **precondition on ever enabling a WAF**, not a fix to apply now.

**The precondition, written so a future operator cannot enable a policy without meeting it:**

> If a Cloud Armor policy is ever attached to `production-glb-production-vm-backend`, it **must** carry
> higher-priority `allow` rules for (a) the chat message-write endpoints, (b) the Google inbound
> webhook/interaction endpoint, and (c) the presence/typing heartbeat endpoints, **before** any
> preconfigured OWASP ruleset is set to `deny`. Chat message bodies are user-typed free text and will
> match `sqli-stable` and `xss-stable` signatures as a matter of routine. Stage the ruleset in
> **preview mode** first and read the logs for a week before enforcing.

**Caveat, and it is the same caveat as J.1:** Terraform not declaring it does not prove GCP does not
have it. `timeout_sec` is the precedent for an out-of-band console change Terraform does not know
about. The merged `describe` in J.1 returns `securityPolicy` and `edgeSecurityPolicy` and settles it in
the same call.

**The avatar note.** Phase 0 §4.K asserts that *"the same policy will also break the Chat app's avatar
fetch; host the avatar on public GCS."* **No notes file verified this**, and it is a claim about
Google-side fetch behaviour, so it is carried as a claim of the prompt rather than as a finding:

> `VERIFY: whether Google Chat fetches a Chat app's avatar image server-side from the configured URL, and whether that fetch would be subject to our Cloud Armor policy or our authentication` — settle by reading the Chat app configuration page's avatar field documentation and by observing one fetch in access logs after configuring the app. **Blocks:** nothing today (no WAF exists); if true, the fix is trivial and known — host the avatar on a public GCS object, which the app already has precedent for (`training/gcs_media.py` hand-rolls V4 signing). **Do not let a trivial fix become a launch-day surprise:** put the avatar on GCS from day one regardless.

### J.3 The 30-second synchronous interaction deadline, and the ack-then-enqueue pattern it forces

**Confirmed verbatim** from
<https://developers.google.com/workspace/chat/receive-respond-interactions>: *"To respond
synchronously, a Chat app must respond within 30 seconds, and the response must be posted in the space
where the interaction occurred"* (`notes_google_verify.md:1227-1237`).

**Design consequence, and it is not negotiable: Triton's LLM turn can never answer inline.** A Gemini
turn with a tool loop routinely exceeds 30 s. The pattern is therefore fixed:

1. The interaction endpoint does the **absolute minimum**: verify the JWT, write a raw inbound-event
   row, `enqueue`, return `200` fast (`notes_research_gaps.md:1176-1177`).
2. Respond immediately with an **ack** (a short message or card) in the originating space.
3. Do the real work in a Frappe background job.
4. **`spaces.messages.patch`** the ack, or post the answer as a threaded reply under the original.

*"Do not attempt a synchronous LLM call in the webhook"* (R04 §4.6, `notes_research_gaps.md:1177`).

Two adjacent facts that shape the same endpoint:

- **Interaction events fire only for messages aimed at the app** — the `MESSAGE` trigger is *"@mentions
  the Chat app or uses a slash command"*. In a DM with the app every message reaches it; in a space,
  only mentions and commands (`notes_google_verify.md:1222-1225`). The coworker firehose comes from
  Workspace Events, not from here.
- **The authoritative interaction-event `type` enum has seven values**, two more than the research had:
  `MESSAGE`, `ADDED_TO_SPACE`, `REMOVED_FROM_SPACE`, `CARD_CLICKED`, **`APP_HOME`**, **`SUBMIT_FORM`**,
  `APP_COMMAND` (`notes_google_verify.md:1211-1225`). **Log-and-alert on any unrecognised `type`** —
  belt and braces, because Google adds them.
- **Verification is four checks, not two.** Signature, `aud`, `email == chat@system.gserviceaccount.com`,
  and `email_verified`. A stock `verify_oauth2_token(...)` checks only the first two, and **any GCP
  project in the world can mint a valid OIDC token for your audience**
  (`notes_google_verify.md:1285-1306`). The fourth unit test — a valid Google ID token for a *different*
  audience — is the one that catches the naive implementation (R04-C09).

### J.4 Web Push — the platform gap, and hand-rolled VAPID as the realistic plan

**Frappe's built-in path does not serve us, and it is worse than "not configured".** Frappe v16 ships
`frappe.push_notification.PushNotification`, confirmed present in the deployed build and byte-matching
the published v16 source (`notes_infra.md:1052-1071`). On this site:
`Push Notification Settings.enable_push_notification_relay = 1` with both `api_key` and `api_secret`
populated, while **`push_relay_server_url` is `null` in site config** (`notes_infra.md:543-558`). So
`is_enabled()` returns **True** — it reads only the DocType flag — and `_send_post_request()` then hands
a `None` relay URL to `FrappeClient` and fails at request time. **It is configured-looking and broken**
(`notes_infra.md:1073-1090`). It is also FCM-only and needs a second service to operate. **Do not design
chat push on it.**

**The measured absence.** A live import probe on the prod bench
(`frappe.get_module`, `notes_infra.md:979-1005`):

```json
{"pywebpush": [false, "ModuleNotFoundError"], "py_vapid": [false, "ModuleNotFoundError"],
 "ecdsa": [false, "ModuleNotFoundError"], "http_ece": [false, "ModuleNotFoundError"],
 "firebase_admin": [false, "ModuleNotFoundError"],
 "cryptography": [true, "46.0.7"], "jwt": [true, "2.13.0"], "requests": [true, "2.33.1"],
 "numpy": [true, "2.5.1"], "redis": [true, "7.1.1"]}
```

**And the reason is stronger than the README's.** The deploy pipeline runs exactly
`git fetch/reset → bench --site all migrate → bench build → FLUSHDB → restart`
(`infra/cloudbuild-deploy.yaml:35-41`). There is **no `pip install`, no `bench setup requirements`, no
`pip install -e apps/…`** step at all. So a new PyPI dependency added to `pyproject.toml` **would not be
installed by a deploy** and would be lost on any VM rebuild from the startup script
(`notes_infra.md:971-977`). *The no-new-dependency rule holds — for a better reason than the README
gives, since the "managed server where packages can't be pip-installed" premise no longer matches the
topology.*

**Therefore: hand-rolled VAPID + `aes128gcm`, matching ADR 0004's no-SDK doctrine** — the same posture
as `stripe_payments` (which hand-rolls `Stripe-Signature` HMAC verification) and `quickbooks_online`
(which hand-rolls its OAuth/webhook client), both of which have precedent, tests and conventions in this
repo. Every primitive is already present in `cryptography 46.0.7` (`notes_infra.md:1021-1031`):
`ec` (P-256 keypair and ECDH), `serialization` (raw/DER/PEM), `kdf.hkdf.HKDF`, `ciphers.aead.AESGCM`;
plus `PyJWT 2.13.0` for the ES256 VAPID JWT and `requests 2.33.1` for the POST.

**Sizing: ~150–250 lines plus tests, in two pieces** (`notes_infra.md:1033-1046`):

| Piece | Size | What it is |
|---|---:|---|
| **VAPID** | ~50 lines | Generate/store a P-256 keypair once (private key in a `Password` field — the site already stores a 2,358-char service-account JSON that way, and `frappe.utils.password.encrypt/decrypt/get_decrypted_password/set_encrypted_password` are confirmed present); per request build a JWT `{aud: <origin of endpoint>, exp: now+12h, sub: "mailto:…"}`, sign ES256, send as `Authorization: vapid t=<jwt>, k=<base64url raw public key>`. Publish the same public key to the browser as `applicationServerKey`. |
| **`aes128gcm` payload encryption** | ~120 lines | RFC 8188 content-coding: ephemeral P-256 key, ECDH against the subscription's `p256dh`, HKDF twice (once with the `auth` secret for the IKM, once for CEK + nonce), pad, AES-128-GCM, prepend the 86-byte header (salt‖rs‖idlen‖ephemeral public key). This is the fiddly part; it is fully specified with abundant reference implementations. |
| **Tests** | — | A **bench-free** suite pinning both against the RFC 8291/8292 test vectors. Fits this repo's testing model exactly; needs its own CI step per `CLAUDE.md`. |

**Plus the surface that does not exist yet.** The app ships two service workers, `www/kiosk-sw.js` and
`www/wall-sw.js`, both served from `www/` and therefore **registered at root scope** — which *does*
cover `/app/*`, contrary to `notes_infra.md:737-741`'s imprecise phrasing; that is exactly why the kiosk
worker carries a warning in its own source (*"This worker is registered at ROOT SCOPE — it sees every
request on the origin"*, `www/kiosk-sw.js:60-63`) and why there is a dedicated CI step guarding it
(`ci.yml:471-477`). **The accurate statement is: neither worker is ever registered from a desk page, and
neither implements `push` or `notificationclick`** — the complete `addEventListener` set is
`install`/`activate`/`fetch`/`message`/`sync` for kiosk and `install`/`activate`/`fetch` for wall
(`notes_close_repo.md` §4.2). Web Push is **entirely new surface**. The itemised delta a desk push
worker needs: a `push` handler + `showNotification`; a `notificationclick` handler that focuses an
existing client or opens the deep link (this is where decision #3's "clicking the notification lands on
the message" lives, and it must survive a **hard** load — see the `website_route_rules` catch-all at
[§A.10.1](#a101-what-serving-an-spa-from-www-implies-given-zero-website_route_rules), closed as
**B1** in §K.1.3);
a `pushsubscriptionchange` handler plus a server-side `Push Subscription` registry
(`user, endpoint, p256dh, auth, user_agent, created, last_seen`) with pruning on 404/410; VAPID keys;
a `?v=<deploy token>` cache-busting registration URL (both existing workers use
`kiosk.py::get_deploy_version`, *"the mtime of sites/assets/assets.json"*); and it **must not** precache
or answer for `/assets` — both existing workers learned that the expensive way.

> `VERIFY: whether two different scriptURLs registered at the same scope replace one another's registration, or coexist` — three scripts (`kiosk-sw.js`, `wall-sw.js`, a new `desk-sw.js`) all registering at scope `/` may be competing for one registration slot per origin, so a browser that opens `/kiosk` and then the desk could flip the root registration back and forth. Settle from <https://w3c.github.io/ServiceWorker/#navigator-service-worker-register>, or empirically via `navigator.serviceWorker.getRegistrations()` in devtools. **Blocks:** whether a desk push worker can live at root scope at all, or must be served from a subpath with an explicit narrower `{scope: "/app/"}` — which then requires a `Service-Worker-Allowed` response header at the nginx layer. **This is a genuinely new risk that no notes file raised** (`notes_close_repo.md` §4.2 item 6, §6 item 4) and it is cheap to settle.

**One limitation to acknowledge, not to solve: iOS Safari.** Web Push on iOS requires the site to be
added to the Home Screen and launched as a standalone PWA; a page open in the Safari tab cannot receive
push. Anyone on this project should assume iPhone users get the bell and the in-app experience but not
the push, unless they install the PWA. **This is not in the notes and is carried as unverified rather
than asserted:**

> `VERIFY: the current iOS Safari Web Push requirements (add-to-Home-Screen, permission-on-user-gesture, and whether a Frappe desk page can be installed as a PWA at all)` — settle from the WebKit release notes / MDN Push API browser-compatibility table, and by testing on one company iPhone. **Blocks:** nothing structural; it changes what we promise users and whether push is sufficient for field staff, who are the most mobile-dependent group. Raise it in the rollout comms, not in the architecture.

**And the honest scoping recommendation:** because push is genuinely new surface, `notes_infra.md`'s
OQ-3 recommends **scoping it as its own phase with its own checkpoint**, and shipping Phase 1 on
in-app + bell if that is what it takes. Appendix B follows the master map (Phase 4 = notifications), so
push lands there — but it is the largest single unbuilt component in this ADR and should be sized as
such.

### J.5 Session affinity is `NONE` — moot today, a hard precondition the moment there are two backends

`infra/configs/load_balancer.yaml` sets no `session_affinity`, and the module defaults it —
`modules/net-lb-app-ext/variables-backend-service.tf:161`:
`coalesce(backend_service.session_affinity, "NONE")`. So affinity is **NONE**
(`notes_gap_report.md` §0-2).

**And it does not matter today**, because both load balancers have exactly one backend: a single
standalone VM NEG (`infra/configs/load_balancer.yaml:36`; one VM per environment,
`notes_infra.md:750-764`). With one backend there is nothing to be sticky to.

**It stops being true the moment the VM becomes a MIG with more than one instance.** At that point
socket.io needs either `CLIENT_IP` affinity or a Redis socket.io adapter — and **Frappe v16 passes no
`adapter` option at all** (`notes_infra.md:849-855`, quoting `realtime/index.js`), so the adapter would
have to be introduced. Record it in the ADR as a **scaling precondition, not a current bug**, and put it
in Appendix B's "before you scale horizontally" list alongside the presence design (which is Redis-keyed
and therefore already multi-instance-safe, and the read-state design, which is DB-backed and likewise).

### J.6 Does the socket.io path traverse the load balancer? — Yes, and it is the only proxy hop

```
Cloudflare (DNS-only, gray cloud)
    └── erp.sapphirefountains.com → 136.68.113.208
        └── production-glb (LB, us-east4)
            └── production-erpnext-standard-vm:80
                └── nginx → frappe-bench:8000 (gunicorn) | 127.0.0.1:9000 (socketio)
```

From `infra/docs/GCP_SETUP.md:9-21`, with *"Traffic goes directly to GCP LBs"* at `:77` — **Cloudflare
is not in the data path, so its own 100 s proxy timeout does not apply.** There is exactly one proxy hop
that can kill a WebSocket: the GCLB (`notes_infra.md:750-767`). nginx is the **stock Frappe template**
generated by `bench setup production` (`infra/configs/startup_script.sh:124-127`), with upstreams
`frappe-bench-frappe → 127.0.0.1:8000` and `frappe-bench-socketio-server → 127.0.0.1:9000`
(`infra/assets/test-vm-setup.md:244-247`).

**Port 9000 is deliberately NOT in the LB firewall rule** — `allow-lb-to-production-vm` permits the
health-check ranges `130.211.0.0/22` and `35.191.0.0/16` on 80/443/8000 only
(`infra/docs/GCP_SETUP.md:118`). socket.io is reachable only through nginx on :80, **which is correct and
should stay that way** (`notes_infra.md:927-930`). Incidentally this closes R04-V17: both health-check
ranges are already allowed, so R04's "safe default" is the deployed state.

> `VERIFY: the deployed nginx socket.io block — proxy_read_timeout, proxy_http_version 1.1, Upgrade / Connection "upgrade"` — `notes_infra.md` V-4, one read-only SSH `sed -n '1,200p' /etc/nginx/sites-available/frappe-bench`. **Blocks:** whether nginx is a **second, independent** WebSocket-killing hop. Frappe's stock template sets these; the deployed value has not been read. Do it in the same Phase 1 pass as the `gcloud describe`.

### J.7 The deploy FLUSHDBs both Redis instances and restarts every process

**The measurement.** `infra/cloudbuild-deploy.yaml:40` runs
`redis-cli -p 13000 FLUSHDB && redis-cli -p 11000 FLUSHDB`, followed by
`sudo systemctl restart frappe-bench` at `:41` (`notes_infra.md:592-600`;
`notes_close_repo.md` §5.6 records the same flush hitting **both** ports from `:37`). And the topology
makes it worse than it looks: **`redis_cache` and `redis_socketio` are the same instance and the same
DB 0** at `127.0.0.1:13000`, with `redis_queue` at `:11000` (`notes_infra.md:582-587`). All bench
processes run under one honcho group, so the restart drops the socket.io node process along with
everything else (`infra/assets/test-vm-setup.md:321`).

**So every production deploy is a hard realtime reset.** Presence, typing, unread counters, rate-limit
buckets and any in-flight SSE all die; **every WebSocket drops**; and **queued background jobs do not
survive** (`notes_infra.md:932-939`). This is not hypothetical — it is the documented cause of at least
three past incidents in this repo (`setup/document_locks.py:18-30`, `hooks.py:769`,
`training/progress.py:44`).

**The four consequences this ADR commits to, each with its fix:**

1. **The outbox sweeper, not the queue, is the delivery guarantee.** `DECISIONS.md` D8 states it;
   [§G.1](#g1-outbound--the-transactional-outbox) specifies the outbox. A deploy landing between commit and worker pickup destroys the relay job — the
   ERPNext row survives because it is committed. The same applies to **notifications**, because
   `enqueue_create_notification` uses `enqueue_after_commit` (§H.5.2), which is why the sweeper must be
   able to re-issue a missing notification and why `dedupe_on` is what makes that safe.
2. **Redis is a working copy; the database is the truth.** The precedent to copy is
   `training/progress.py:44-46` — *"a deploy FLUSHDBs Redis, so the worst case is losing one flush
   interval of watching — about a minute — not an attempt"* — with `flush_stale_attempts()` to drain
   what a closed tab left behind. Presence degrades to "everyone offline for one heartbeat" (§H.3.4);
   read state lives in `Chat Room Member.last_read_seq`, in the DB.
3. **Clients must reconnect and rehydrate from the DB.** `live_form_sync.js:167,172,194` already binds
   `frappe.realtime.on("connect", …)` → `_on_reconnect()` for exactly this. **Copy it.**
4. **The per-space token bucket fails open into a fresh full bucket, which is safe** — but the queued
   jobs beside it do not survive, which is not. Size the bucket accordingly and let the sweeper carry
   correctness. Note that `media.upload` **shares the per-space 1-write/second bucket with
   `messages.create`** (`notes_google_verify.md:1001-1004`), so a message with an attachment costs
   **two** tokens; a bucket that charges one will 429 on exactly the "someone drops a screenshot into a
   busy space" case (`DECISIONS.md` D8).

One more consequence worth stating because it is the *good* kind: `maxmemory-policy` is
`allkeys-lru` with `maxmemory 1.57 G` and only 4.75 M in use, `evicted_keys: 0`
(`notes_infra.md:565-567`, `:614-617`). Headroom is enormous today — but **never treat a Redis key as
guaranteed-until-TTL here**, because `allkeys` means even keys with a TTL are eviction candidates.

### J.8 The infrastructure risk table, condensed

| # | Risk | Status today | Fix | Evidence |
|---|---|---|---|---|
| J.1 | GCLB 30 s backend timeout closes idle WebSockets and truncates SSE as a **total** budget | **Unset in Terraform; live value unread.** Works today only because socket.io's 25 s ping beats it by 5 s | `--timeout=3600` on both backend services **plus** `timeout_sec: 3600` in `load_balancer.yaml`; SSE `: keepalive`; observe in a browser on production | `notes_infra.md:792-901`; GCP docs fetched live |
| J.2 | Cloud Armor OWASP rules would 403 chat text | **No WAF attached; no `google_compute_security_policy` in the repo** | A precondition on ever enabling one: higher-priority allow rules on message/webhook/heartbeat paths, preview mode first | `notes_gap_report.md` §0-1 |
| J.3 | 30 s synchronous interaction deadline | Confirmed verbatim | Ack immediately, enqueue, `messages.patch` the answer. Never a synchronous LLM call | `notes_google_verify.md:1227-1237` |
| J.4 | Web Push is a platform gap | Frappe's relay is present-but-dead; no push libs installed; no `pip` step in the deploy; no desk service worker | Hand-rolled VAPID + `aes128gcm` on `cryptography`/`PyJWT`/`requests`, ~150–250 lines + RFC-vector tests; new `desk-sw.js` + `Push Subscription` registry | `notes_infra.md:990-1005`, `:1021-1090`; `notes_close_repo.md` §4.2 |
| J.5 | Session affinity `NONE` | Moot at one backend | A scaling precondition: `CLIENT_IP` affinity or a socket.io Redis adapter (v16 passes no `adapter`) before a second instance | `notes_gap_report.md` §0-2; `notes_infra.md:849-855` |
| J.6 | socket.io path through the LB | Yes — one hop; Cloudflare DNS-only; :9000 not LB-exposed | Keep :9000 behind nginx; read the deployed nginx socket.io block | `notes_infra.md:750-776`, `:927-930` |
| J.7 | Deploy FLUSHDBs both Redis + restarts everything | Confirmed in the pipeline | Outbox sweeper as the guarantee; Redis as a working copy; `on("connect")` rehydrate; bucket charges 2 tokens for uploads | `notes_infra.md:592-600`, `:932-939` |

---

## K. Open items and open questions

These are **two different registers** and conflating them is how a checkpoint becomes unreadable.

- **§K.1, the VERIFY register**, is *facts we have not yet established*. Every item has a settlement
  method that a person or an agent can execute; none of them requires a judgement call. They are owned
  by phases.
- **§K.2, the CQ register**, is *decisions only Nikolas can make*. No amount of further research settles
  them, because they are product, cost or risk-appetite choices. They are owned by the human, and Phase
  1 should not start until CQ-1 has an answer.

### K.1 The VERIFY register

#### K.1.1 The counts

The 133-item research register was reconciled against all six sibling audit notes item by item
(`notes_register_reconciled.md`, which is the authoritative table — this ADR references it rather than
reproducing 133 rows).

| Status | Count | Share |
|---|---:|---:|
| **RESOLVED** — settled this session, with a citation | **58** | 43.6% |
| **DEFERRED** — genuinely open, not needed now, owned by a named phase | **66** | 49.6% |
| **ACCEPTED** — a risk we choose to carry, with its bad-outcome stated | **7** | 5.3% |
| **BLOCKING** — must be settled before the ADR can be called final | **2** | 1.5% |
| **Total** | **133** | 100% |

By source document:

| Document | Registered | RESOLVED | DEFERRED | ACCEPTED | BLOCKING |
|---|---:|---:|---:|---:|---:|
| R01 `01_google_chat_api.md` | 37 | 21 | 14 | 2 | 0 |
| R02 `02_frappe_platform.md` | 38 | 22 | 13 | 1 | 2 |
| R03 `03_triton_context_caching.md` | 29 | 9 | 19 | 1 | 0 |
| R04 `04_gcp_workspace_setup.md` | 29 | 6 | 20 | 3 | 0 |
| **Total** | **133** | **58** | **66** | **7** | **2** |

**Why the reconciliation mattered.** `notes_research_gaps.md` was written last and **cites none of its
six siblings** — a grep for their filenames returns zero matches — so it carries as open, several as
"phase-blocking" or "highest-priority", items that sibling agents definitively closed the same session
(`notes_gap_report.md` §D-2). The reconciliation found the critic's thirteen over-reports, **upheld
twelve**, and found **fourteen more the critic's table missed**. Reporting the register as-is would have
carried ~28 settled items into the checkpoint as open risk — including **both** items the phase prompt
singles out as highest priority (R01-G01 `createMessageNotificationOptions`, and R03-G02 how the MCP
server authenticates) — **burying the two that actually matter**
(`notes_register_reconciled.md` §(a)). A checkpoint that cries wolf on thirteen settled items is its own
harm.

#### K.1.2 DEFERRED, by owning phase

| Phase | Count | Ids |
|---|---:|---|
| **1 — foundations & auth** | **37** | R01-V03, V14, V16, V17, V18, G06, G12, G13, G15, G16 · R02-V02, V04, V07, V22, M02, G08 · R03-V06, V14, V15, M03, M04 · R04-V01, V03, V04, V05, V06, V08, V10, V11, C01, C02, C03, C04, C06, C07, C09, C11 |
| **2 — bidirectional sync engine** | **15** | R01-V08, V10, G04, G17 · R02-V10, V11, V20, M01, G06 · R03-V04, G06 · R04-V14, C08, C10, C12 |
| **3 — chat SPA** | **0** | — its one open question was **BLOCKING**, not deferred |
| **4 — notifications** | **2** | R02-V14 (`after_insert` email), R02-V17 (service-worker scope) |
| **5 — Triton integration** | **11** | R03-V01, V02, V07, V10, V12, V13, V16, V17, M01, M02, M05 |
| **6 — governance, audit, rollout** | **1** | R03-G05 (real message volume, unmeasurable before the feature exists) |
| **Total** | **66** | |

**Reading of the shape, which is more useful than the numbers.** Phase 1 carries more than half the
deferred load (37 of 66), and **16 of those are R04's runbook gates** — i.e. **the residual risk in this
project is concentrated in one Google Workspace admin session and one GCP console session, not spread
across the codebase**. Of the Phase 1 items exactly one has a schedule-shaped blast radius —
**R04-V08 / R04-C03**, whether the Internal-app waiver still holds for the *restricted* scopes
(`chat.messages`, `chat.delete`, `chat.import`). If it does not, the project faces a multi-week Google
verification before it can send a single message. **Run it first, before any other console work**,
because a red result reshapes the whole plan. Phase 3 carrying zero deferred items is **not** a sign the
SPA is well understood; it is a sign its single open question was severe enough to be classed BLOCKING.

**Six items are worth settling in Phase 1 as one bench-console batch**, because each is a single
`inspect`/import call and together they de-risk three later phases: R02-V02 (hash-name length), R02-V04
(`track_changes` default), R02-V22/M02 (`FrappeTestCase` vs `IntegrationTestCase`), R03-V06
(`has_value_changed`), R03-V14/M03 (redis-wrapper site-key prefixing), R03-V15/M04
(`get_url_to_form`).

Two of the Phase 1 items are worth naming because they are one-line commands with disproportionate
value: the **merged `gcloud describe`** (J.1) which answers timeout + `securityPolicy` +
`edgeSecurityPolicy` + `sessionAffinity` in a single call, and adding
`google.auth.impersonated_credentials` and `sys.version` to the existing `frappe.get_module` import
probe (`notes_infra.md:979-1005`), which sizes the keyless-DWD module and corrects the Python-version
line in a human runbook that currently hardcodes `python3.14` — a value provably inherited from a **CI
job that was subsequently deleted** (`notes_close_repo.md` §4.1).

#### K.1.3 BLOCKING — four closed, one de-escalated; here is what each was and what remains

At reconciliation time there were **two BLOCKING register ids plus three blocking items with no register
id** (found by the critic *between* the notes, so they could not appear in the table). **Four (B1–B4) have
since been closed** by the three `notes_close_*.md` passes; the fifth, **B5**, was not closed but
**de-escalated by `DECISIONS.md` D2** — a binding decision that records the Raven-on-v16 experiment as
non-blocking — and is carried as **CQ-22**, a question for the human rather than a gate. This is the
single biggest change in the evidence base and the reason Phase 0 can close.

| Id | The question | What breaks if it is wrong | Status now | Residual |
|---|---|---|---|---|
| **B1** (R02-V18 / R02-G05) | Does Frappe v16 `website_route_rules` support a `<path:…>` catch-all, so `/chat/room/X?message=Y` survives a **hard refresh**? | Every deep-link acceptance criterion in decisions #3 and #8. The notification deep link — the whole point of decision #3 — would land on an error page. It also decides the SPA's mount model, a foundational Phase 3 choice. | **CLOSED — YES.** v16 uses **werkzeug `Rule`** verbatim (`frappe/website/path_resolver.py`, `frappe/website/router.py`), so the full converter set including `<path:name>` is available. Both frappe **and ERPNext ship such rules today**, and `/orders/<path:name>` → `erpnext/templates/pages/order.py` is a **working in-production** end-to-end precedent, reading the capture from `frappe.form_dict`. One rule suffices: `{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}`; **`/chat` itself needs no rule** (`notes_close_frappe.md` §2). | The pinned werkzeug's `PathConverter.regex` (CF-2) — immaterial; worst case one extra explicit `/chat` rule. Plus the `website_404` cache trap: loading `/chat/room/X` **before** the rule ships caches that URL as a 404 until Redis is flushed — the deploy's FLUSHDB saves us, a hotfix without a restart would not. Note it in Phase 3 rollout. |
| **B2** (no register id; critic §B-2) | How can a chat-DocType denylist be imposed on FAC's `run_database_query` / `get_document` / `list_documents`, given FAC is in **neither repo**? | Phase 0 §4.J bullet 2, invariant I5, and — if the only answer were "no DocPerm" — the entire chat data model, since every read would have to go through a whitelisted endpoint. | **CLOSED, with a different answer than assumed.** "No DocPerm" closes two of three surfaces; **`run_database_query` is role-gated and executes raw SQL, so no Frappe permission mechanism touches it**. The mechanism that does is the **`_gate.py` `_safe_execute` seam**, which is already built, already in production, and sees every FAC tool call including built-ins (§I.2). | CF-1: whether FAC's `get_document`/`list_documents` use `frappe.get_doc`/`get_list` rather than `get_all` — read the app on the bench. **Does not change the recommendation.** Plus the `run_python_code` sandbox question and the `__global_search` question (§I.2.4). |
| **B3** (no register id; critic §A-6) | What is the maximum length of a Google Chat message resource name, and can a Frappe `Data` column carry it under a unique index? | §7's named acceptance criterion, and with it the **structural** (rather than procedural) echo-suppression design. Too short and the unique index either truncate-collides — two Google messages dedupe into one, i.e. **silent message loss** — or fails to create. | **CLOSED, by refutation plus a sized answer.** **No maximum is documented** for `Message.name` or for the space id. So: `gchat_message_name` = `Data` **length 255** with **`unique: 1` on the DocField**; `gchat_space_name` likewise; `client_message_id` = `Data(64)` in a composite `unique(room, client_message_id)`. 255 × utf8mb4 = 1,020 bytes, far inside InnoDB's 3,072-byte index limit (`notes_close_google.md` §1.6). | An empirical N≥30 capture across ≥3 spaces (`notes_close_google.md` §1.7), which **confirms rather than gates** — 255 is sized for ~5× the observed value. It also settles the character set, which decides whether the name is URL-path-safe for SPA deep links. |
| **B4** (no register id; critic §C-5) | Does v16 socket.io permission-check `doc:` room joins and leave `user:` rooms unchecked? | The entire realtime channel split. If doc-room joins are **not** checked, broadcasting message content there leaks it to any authenticated session that guesses the room name — compounding with the already-confirmed `get_site_room()` fallback. | **CLOSED — YES, and better than assumed.** `doc_subscribe` calls back into Python (`frappe.realtime.has_permission` → the full document-level stack, `ptype="read"`); **user rooms are not client-joinable at all** — there is no `user_subscribe` verb anywhere in the four-file node surface (§H.4.1, `notes_close_frappe.md` §1.4). | Three residuals, all named in §H.4.2–H.4.3: the **DocPerm collision** (resolved by the `Chat Room`-only DocPerm split, which is a **deviation from both close notes** and needs Phase 1 sign-off); **membership revocation is cooperative** (a hostile client keeps the stream until reconnect); and **a refused `doc_subscribe` is silent**. |
| **B5** (critic §E item 3) | Does Raven v2.8.11 install and run on a Frappe v16 bench? | The adopt option, entirely. | **DE-ESCALATED, not closed.** `DECISIONS.md` D2 (binding) records it as **"not phase-blocking under this decision"** — it matters only if the human wants to revisit adopt, and the case against adopt already rests on two independent grounds: no Raven line known-good on v16 (its desk bundle disables itself on v16, commit `a79c689`; `develop` imports `frappe.realtime.realtime`/`Socket`, which do not exist in v16), and Raven owns a route and the page, which fights decision #8. | Carried as **CQ-22**, not as a gate. `notes_register_reconciled.md` C2 records the contradiction with the critic's ranking. |

**So: as of this ADR, zero items are BLOCKING.** What was blocking is now either closed or has become a
named residual with a phase owner. That is the claim the checkpoint should make, and it is defensible
because each closure cites source read this session.

#### K.1.4 The seven ACCEPTED risks, with what happens if each turns out badly

Recorded here because an accepted risk that is not written down is just an unexamined one
(`notes_register_reconciled.md`):

| Id | Risk accepted | If it turns out badly |
|---|---|---|
| R01-V19 / G14 | Scoping the Chat app's visibility to one Google Group avoids the "does domain-wide visibility require internal Marketplace publishing" question | The console demands whole-domain deployment for something we need (most plausibly 1:1 DM discoverability), and go-live gains a Marketplace publish step measured in **weeks**. Detected at the first DM test, not at rollout |
| R02-V12 | Build the DB-backed transactional outbox unconditionally rather than relying on RQ `Retry` semantics | We built a sweeper we might partly have got free — small redundant machinery, **no correctness risk**. Reinforced by the deploy FLUSHDB (§J.7) |
| R03-V11 | Reject Frappe's `FullTextSearch`/Whoosh wrapper on per-node-index grounds regardless of Whoosh's health | Nothing material; a reviewer re-raises it once |
| R04-V07 | Message actions are Developer Preview; we plan @mention interception instead | A later UX idea wanting a Message action waits for GA — a feature deferral, not a rework |
| R04-V15 | JWT-only webhook verification; explicitly **no** IP allowlisting | Google *has* published stable egress ranges and we skipped an optional defence-in-depth layer — addable later. The reverse error (allowlisting a range Google does not guarantee) breaks **silently**, so the asymmetry favours accepting |
| R04-V16 | Set the GCLB timeout to 3600 s, an order of magnitude under the claimed 86,400 s max | If the max is lower, `gcloud compute backend-services update` fails **visibly** at the moment of the change — loud and cheap |
| R01-V19's sibling in D4 | BLOB + numpy now, adapter later | Covered by D4's four numeric revisit triggers; the swap is a one-file change by construction |

#### K.1.5 The eight residual VERIFY items this Part adds or sharpens

Everything below is *new* in §H–§K or materially sharpened by them. Items already carried in
`notes_register_reconciled.md` are not repeated.

| Id | Claim to settle | How | Blocks |
|---|---|---|---|
| **P3-1** | The `Chat Room`-carries-DocPerm / everything-else-zero split (§H.4.2) is correct and sufficient | Phase 1 review + the bench-free DocType-JSON test in §I.2.4 item 1 | **Phase 1 cannot write a DocType JSON without it.** It is a deviation from both close notes |
| **P3-2** | Background-tab timer throttling does not stretch a 20 s heartbeat past the 55 s TTL (§H.3.1) | Instrument one backgrounded tab for 10 minutes; log actual beat intervals | Nothing structural — failure degrades toward "notify", the safe direction. Prevents claiming a fidelity we have not measured |
| **P3-3** | Two different scriptURLs at the same service-worker scope coexist rather than replace one another (§J.4) | The Service Workers spec's Register algorithm, or `navigator.serviceWorker.getRegistrations()` after opening `/kiosk` then the desk | Whether a desk push worker can live at root scope, or needs a subpath plus a `Service-Worker-Allowed` header. **New risk; no notes file raised it** |
| **P3-4** | iOS Safari Web Push requirements, and whether a Frappe desk page can be installed as a PWA (§J.4) | WebKit release notes / MDN compat table + one company iPhone | Nothing structural; changes what we promise field staff, and belongs in rollout comms |
| **P3-5** | Whether Google Chat fetches a Chat app avatar server-side, and whether a future WAF would block it (§J.2) | Chat app configuration docs + one observed fetch in access logs | Nothing today. Mitigate unconditionally by hosting the avatar on public GCS from day one |
| **P3-6** | The exact Google Chat 429 response shape — does it carry `Retry-After`, and is a quota error distinguishable from a permission 403? | <https://developers.google.com/workspace/chat/limits> plus one deliberate over-send in a test space | The token bucket's retry classification (`notes_close_repo.md` §6 item 6) |
| **P3-7** | Whether the existing Google OAuth scope set in Triton includes any Chat scope (§I.12 requirement 9) | Read `triton:backend/app/core/config.py` / `google_workspace.py` scope lists against the Chat API's documented scopes | Whether a Triton-side Chat post forces re-consent for existing users. **Do not guess a scope string** |
| **P3-8** | The deployed desk bundle's `get_item_link` matches the `version-16` source (§H.5.1) | One read-only `grep` on the VM (`notes_infra.md` V-3) | Nothing under "set both `link` and `document_type`" — converts a strong inference into a fact |

### K.2 The CQ register — questions only Nikolas can answer

Each is phrased as a question, carries **our recommendation**, and states **the cost of each option**.
Per `DECISIONS.md` D0 these stay **inside this ADR**; they are deliberately *not* written into
`decisions/OPEN-DECISIONS.md`, whose `OD-n` numbering is scoped to the ERPNext migration and is
load-bearing for work items. Lifting any of them into that register is Nikolas's option, not our
default.

**CQ-1 is a gate. Phase 1 should not start until it has an answer**, because it determines the Google
auth identity, and the auth identity determines the scope list, which is frozen in a single super-admin
session.

---

**CQ-1 — The auth / notification / threading trilemma. Which two of the three do we keep?**

At most two of these can hold simultaneously
([§E.3](#e3-the-trilemma--the-finding-that-changes-the-product) states it in full;
`DECISIONS.md` D3):

| Want | Requires | Excludes |
|---|---|---|
| Messages authored by the **real human** in Chat | user auth / DWD | silent → Chat fires its own notification → **decision #3 breaks for native-client users** |
| **No Chat-native notification** | app auth + `NOTIFICATION_TYPE_SILENT` | human attribution → decision #2's mirror becomes a **bot log feed**; also threading; also **outbound attachments** (app auth cannot upload) |
| **Threaded replies** inside Chat | `messageReplyOption` + a threaded space | silent messages; and `spaceThreadingState` is **Output only**, so an API-created space may not be threaded at all |

- **Our recommendation: keep human attribution (DWD) + threading; accept Chat-native notifications for
  people running the native client**, and restate decision #3 as *"exactly two ERPNext-fired
  notifications; users running the native Google Chat client additionally receive Chat's own
  notification, which is documented and accepted."* Phase 0 §4.I explicitly authorises this outcome.
- **Cost of our recommendation:** native-client users get a third ping. Nothing else is lost.
- **Cost of the app-auth alternative ("Option A"):** every relayed message from every colleague arrives
  in the native client from a single bot with an `App` badge and a silent-delivery indicator; mentions
  degrade to plain text; threading is unavailable; **attachments cannot be relayed outbound at all**.
  That is not a mirror of the conversation; it is a log feed. **Do not present it as satisfying the
  locked decisions, because it does not.**
- **Cost of the documented fallback (`spaceNotificationSetting.patch`):** per-space, not per-message —
  it would mute human coworkers along with the relay, must be re-applied per user per new space, and any
  coworker can flip it back with nothing notifying ERPNext.

---

**CQ-2 — Blurred window: does a message notify someone whose SPA is open but not focused?**

- **Recommendation: a bounded grace.** Suppress for the first **120 s** of blur; notify after that
  (§H.2.1, table rows 2 and 3). `BLUR_GRACE` is a config value, logged on every suppression decision.
- **Cost of "always suppress":** the person who left the tab open on Friday gets nothing until Monday —
  silent message loss, and worse than the general case because they *believe* the app is open.
- **Cost of "always notify":** a push every time someone alt-tabs for eight seconds. This is the
  single most-complained-about class of chat notification.
- **Cost of our middle option:** one tunable number that will be wrong for somebody, and a second timer
  to test (§H.3.6's companion test exists for exactly that reason).
- Note we resolve **auto-mark-read = no** in both blurred rows regardless, because a false read receipt
  is unrecoverable in a way a suppressed ping is not.

---

**CQ-3 — In the "SPA open, different room" state, should the floating bubble show a count badge?**

- **Recommendation: the unread counter always increments server-side; the badge is suppressed only in a
  tab whose SPA is foregrounded** (§H.1.1, §H.2.2).
- **Cost of "always show the badge":** the same fact is rendered twice in one viewport — a room-list
  indicator and a bubble count — and users read a double-count as a bug.
- **Cost of "never show it":** a user with the SPA in tab 2 and a Sales Order in tab 1 sees no signal at
  all in tab 1, which is the common case for desk work.

---

**CQ-4 — Does an @mention override the presence suppressions?**

- **Recommendation: yes — override rows 2, 4, 5, 6, 7; do not override row 1 (they are looking at it);
  do not override mute** (§H.2.3). Mentions get their own Notification Type, their own dedupe key, and a
  deep link to the **message**, not the room.
- **Cost of "no override":** a direct mention in a room you are not currently viewing produces only a
  faint list indicator. This is the failure people escalate.
- **Cost of "override everything including mute":** mute stops meaning anything, and the first person to
  be woken by a mention in a muted room will say so. Deliberately deferred to CQ-8 rather than folded
  in here.

---

**CQ-5 — Google Chat native notifications: A, B, or C?**

Given CQ-1, what do we actually ship for the Chat surface?

- **(A) Accept them.** DWD relay, human attribution, Chat notifies natively. **Cost:** native-client
  users get a third ping per message. **Zero engineering cost.** *This is our recommendation.*
- **(B) Suppress via app auth + `NOTIFICATION_TYPE_SILENT`.** **Cost:** everything in CQ-1's second row
  — bot attribution, no threading, no outbound attachments, mentions as plain text. **Also fails
  entirely for DMs, for external/guest members, and for spaces owned by non-Workspace accounts.**
- **(C) Per-user `spaceNotificationSetting` muting.** **Cost:** mutes human coworkers too, per-space,
  user-reversible, and requires enrolling every user via DWD and re-applying on every new space. We
  recommend against it explicitly.

---

**CQ-6 — Should Web Push previews show message content on the lock screen?**

- **Recommendation: sender + room name only, no body, by default; a per-user opt-in for full previews.**
- **Cost of showing content:** a customer name, a price, or a complaint about a colleague is readable by
  anyone glancing at a phone on a table. Ours is a business chat carrying customer and financial
  context.
- **Cost of hiding it:** every notification requires opening the app to learn whether it mattered, which
  measurably reduces how much people trust notifications.
- Note this is a *payload* decision, and the payload is encrypted end-to-end under `aes128gcm` (§J.4) —
  so hiding content on the lock screen is a rendering choice in the service worker, not a security
  control. Say that explicitly so nobody assumes it is one.

---

**CQ-7 — Does the bubble badge count unread *messages* or unread *rooms*?**

- **Recommendation: rooms.** "3" means three conversations want you, which is actionable. Message counts
  in a busy room produce a badge reading "247" that conveys nothing and never goes down fast enough to
  feel like progress.
- **Cost of rooms:** you cannot tell a one-message nudge from a forty-message thread without opening it.
- **Cost of messages:** badge inflation, and the number becomes noise people learn to ignore — which
  defeats the whole notification design.
- Either way the server publishes both figures on `chat:unread_updated` (§H.4.4), so this is a render
  decision that can be changed without a migration. Choose once anyway, because changing it later
  retrains everyone's intuition.

---

**CQ-8 — What does muting a room actually do?**

Four semantics, and they are genuinely different products:

| Option | Bell | Push | Room indicator | Mentions |
|---|---|---|---|---|
| **(a) Soft mute** *(our recommendation)* | no | no | **yes** | **still notify** |
| (b) Hard mute | no | no | yes | **no** |
| (c) Hide | no | no | **no** — room drops out of the list until opened | no |
| (d) Leave | — | — | — | — (this is CQ-10, a different question) |

- **Recommendation: (a).** It matches what people expect from Slack-family products and keeps the escape
  hatch — someone can still reach you by name.
- **Cost of (a):** a colleague who learns that `@name` beats mute can defeat it, and occasionally will.
- **Cost of (b):** genuinely urgent mentions are silently dropped; the mute becomes a liability.
- **Cost of (c):** rooms vanish and people forget they exist; good for archived projects, bad as a
  default.
- Table row 11 in §H.1 encodes (a). Changing to (b) is a one-cell change; changing to (c) affects the
  room-list query.

---

**CQ-9 — The freshness window and the fail-open default: is "notify when presence is unknown" the right
call?**

- **Recommendation: yes — fail toward sending both notifications** (§H.3.4). Presence TTL 55 s,
  heartbeat 20 s, blur grace 120 s.
- **Cost:** **under a Redis outage, everyone gets notified about everything.** Also, briefly after every
  deploy — because the deploy FLUSHDBs Redis (§J.7) — until clients re-beat on socket reconnect. Bounded
  by reconnect latency, not by the heartbeat interval.
- **Cost of the alternative (fail closed):** the same outage becomes a **total, unannounced notification
  blackout**. Nobody is told; nothing logs it as an incident; people discover it days later.
- The asymmetry is the argument: a duplicate ping is visible, self-correcting and reported; a suppressed
  message is invisible to everyone. **Choose loud over silent.** If Nikolas prefers quiet, the honest
  version is "we accept that a Redis outage silently stops all chat notifications", and that sentence
  should be in the ADR rather than implied.

---

**CQ-10 — When someone leaves a room, what happens to their history?**

- **Recommendation: history stays visible up to the moment they left; nothing after.** Their
  `Chat Room Member` row is retained with a `left_on` stamp rather than deleted, and `allowed_rooms`
  includes the room with an upper `seq` bound.
- **Cost:** the retrieval gate grows a per-room upper bound, which complicates §I.3's contract slightly
  and must be in `T-11`'s assertions.
- **Cost of "history disappears entirely":** people lose their own record of decisions they participated
  in, and Triton loses the ability to answer "what did we agree in that project room" for someone who
  was there at the time. It also makes leaving a room a destructive act, which discourages tidying up.
- **Cost of "history stays forever, including new messages":** leaving means nothing, and removing
  someone from a room stops being a control — which breaks decision #12's oversight story.
- **This interacts with the eviction residual in §H.4.3:** a member removed while their socket is open
  keeps receiving that room's realtime traffic until they reconnect. The `left_on` model bounds the
  *stored* history; it does not bound the live stream. Both need to be true.

---

**CQ-11 — Oversized messages: what happens when someone exceeds Google's 32,000-byte limit?**

The limit is **32,000 bytes for the whole message including cards**, in bytes not characters
(`notes_google_verify.md:1014-1027`). Attachments are separately limited to 200 MB.

- **Recommendation: accept the message in ERPNext (it is the source of truth), relay a truncated version
  to Chat with a clear "… (truncated — open in ERPNext)" marker and a deep link, and mark the
  `Chat Message` row `sync_state` as partially-synced so the lossiness is queryable.**
- **Cost:** the Chat mirror is not byte-identical, which weakens decision #2's "full bidirectional"
  claim for a rare case.
- **Cost of "reject at compose time":** ERPNext refuses a message that ERPNext could perfectly well
  store, because of a downstream transport limit. That is the tail wagging the dog, and it will happen
  when someone pastes a log file.
- **Cost of "relay nothing and fail the job":** the message exists in ERPNext and silently never appears
  in Chat — the worst option, because it is invisible.
- Note this belongs on CQ-13's deliberately-lossy list.

---

**CQ-12 — Do we support spaces containing external users (guests)?**

- **Recommendation: no for V1 — restrict relayed rooms to in-domain members, and refuse (loudly, at
  provisioning time) to mirror a space containing an external member.**
- **Cost:** a genuine use case (a shared space with a customer or a subcontractor) is unavailable in V1.
- **Cost of supporting them:** three separate mechanisms degrade for guests — `NOTIFICATION_TYPE_SILENT`
  explicitly *"doesn't apply to external users (guests) in a space"*
  (`notes_google_verify.md:97-98`); the ERPNext identity mapping has no user to map an external sender
  to, and R03's rule 8 says a webhook that cannot map a Google sender to an ERPNext user **must fail the
  turn, never fall back to a service identity**; and decision #12's oversight/audit story would then
  cover people who are not employees, which is a legal question, not an engineering one.
- If Nikolas wants external spaces, the honest sequencing is: V1 refuses them, V2 designs the identity
  mapping and the retention/oversight policy for non-employees **first**, then enables them.

---

**CQ-13 — Confirm the deliberately-lossy list.**

Some fidelity is lost by design, and the ADR would rather name it than have it discovered. Confirm that
each of these is acceptable:

| Lost | Why | Consequence |
|---|---|---|
| **Presence, typing indicators, read receipts across the Chat surface** | Google exposes essentially none of these for coworkers — availability and read-state APIs are **self-scoped only**, and typing indicators do not exist in the API at all (decision #9; invariant I14) | These features are **ERPNext-only**. A coworker who lives in the native Chat client is invisible to the SPA's presence UI — see CQ-15 |
| **Chat-native threading, if CQ-1 goes the app-auth way** | Silent messages cannot start or reply to a thread; `spaceThreadingState` is Output only | Chat becomes a flat feed even though ERPNext threads correctly |
| **Outbound attachments, if CQ-1 goes the app-auth way** | App auth **cannot upload** (it can download) (`notes_google_verify.md:714-735`) | Decision #9's files never reach Chat |
| **Message bodies of deleted messages, from Google's side** | `showDeleted` tombstones are **content-free** (`notes_google_verify.md:867-871`) | ERPNext must have captured the body earlier; Google will not give it back. This is why decision #12's audit trail must be ERPNext-side |
| **Reactions and pins, in V1** | Not in decision #9's scope; `reactions.create`/`delete` are user-auth only | A reaction in Chat does not appear in the SPA and vice versa |
| **Text over 32,000 bytes** | CQ-11 | Truncated with a marker and a deep link |
| **Rich formatting fidelity** | Chat's formatting vocabulary and Frappe's markdown do not fully overlap | Round-tripping a message may not be byte-identical |

- **Recommendation: accept the list as written, and put it verbatim in the rollout comms** — a
  documented limitation is a feature; a discovered one is a bug report.

---

**CQ-14 — May the sources chip row change to show the full manifest with cited entries marked?**

Decision #7 says the sources panel is *"preserved exactly"*, and §I.10.1 establishes that what actually
exists in the ERPNext widget is a **flat, always-visible chip row** (not a dropdown), rendering a
**hybrid, path-dependent** array whose semantics differ depending on whether the user has a persona
selected.

- **(a) Strict preservation.** Chips render exactly as today; inline `[[ref:N]]` links are purely
  additive. **Cost:** chips and inline citations disagree — a cited chunk may have no chip, because the
  substring heuristic (`"Pond A"` matching inside `"Pond Alpha"`) missed it.
- **(b) Manifest-backed chips** *(our recommendation, for chat-sourced citations only)*. The row renders
  the full retrieval manifest with cited entries marked, and finally reads `s.kind` / `s.subtitle`,
  which the widget already receives and discards. **Cost:** it is unambiguously a change to a surface a
  locked decision says is preserved, and it requires all three Triton call sites to change together
  (`sources.py:286`, `reasoning_engine.py:249`, `intelligence.py:1195`, plus `orchestrator.py:224`).

---

**CQ-15 — How does presence render for someone who lives in the native Chat client and is never in the
SPA?**

This person exists, and under decision #9 they are **permanently, correctly "offline"** in the SPA's
presence UI — because presence is ERPNext-sourced by construction (I14), and Google's availability API
is self-scoped so we cannot read theirs even if we wanted to.

- **Recommendation: render three states, not two — `online`, `away`, and `on Google Chat`** — the third
  derived not from presence at all but from *"this person has an active `gchat_space_name` mapping and
  has posted from the Chat origin recently"*. It is an honest, useful signal that does not pretend to be
  presence.
- **Cost:** a third state to design, and it is a lagging indicator (last-seen-ish, not live).
- **Cost of doing nothing:** they show as offline forever, colleagues conclude the presence feature is
  broken, and trust in every green dot goes with it. **This is the most likely "the new chat is buggy"
  complaint in the first week**, and it is a rendering decision, not a bug.
- **Cost of the tempting wrong answer** — inferring presence from Chat API responses — is that it
  violates I14 and is unimplementable anyway (self-scoped APIs). Do not let anyone try.

---

**CQ-16 — Do we need per-message read receipts for DMs?**

Decision #9 lists per-message read receipts in V1 scope. The measured cost is the problem: a row per
(user, message) is the single biggest scaling risk in the schema, on a site whose **entire** current
write pressure is ~730 `Version` rows/day (`notes_infra.md:227-244`).

- **Recommendation: a per-(user, room) high-water mark everywhere, and *derive* "user X has read message
  Y" from it** (`notes_research_gaps.md:1123-1124`). This gives correct per-message receipts for DMs at
  O(members) rows instead of O(members × messages) — you can render "seen" on the last message the other
  person has read, which is what DM UIs actually show.
- **Cost:** you cannot answer "who read *this specific* message" for a group room where people read out
  of order — but people do not read chat out of order, and no chat product exposes that.
- **Cost of true per-message receipts:** the read-receipt table becomes the largest in the database, and
  it is pure write amplification with a `Version` row behind it unless `track_changes = 0` is set
  explicitly (R02-V04, still unverified).
- **The question for Nikolas is narrower than it looks:** is "seen up to here" sufficient for DMs, or
  does anyone actually need per-message granularity? We believe the former; it is worth thirty seconds
  of his time to confirm before Phase 1 sizes the schema.

---

**CQ-17 — Is the 40,000-token context ceiling right?**

- **Recommendation: ship 40k with the tier split in §I.8, make it a config value, log the realised token
  count on every turn, and revisit with real data after two weeks.**
- **Cost of a lower ceiling:** Triton answers from a narrower view more often, sets `context_truncated`
  more often, and says "my view was cut" more often — which is honest but erodes confidence.
- **Cost of a higher ceiling:** cost and latency scale with input tokens on **every** `@triton` mention,
  and recall past a few tens of thousands of tokens is not free — a claim that is itself unmeasured for
  the pinned model (R03-V01, deferred to Phase 5).
- This is genuinely a cost/quality dial and Nikolas owns the cost side. The engineering commitment is
  that the ladder is deterministic and the truncation is *surfaced*, whatever the number.

---

**CQ-18 — Is Triton's own answer exempt from the pending-action confirmation flow?**

`triton:CLAUDE.md:56-58` says every AI tool that mutates an external system must route through the
pending-action flow, and *"bypassing it is a security regression, not a shortcut"*. A Google Chat post is
such a mutation.

- **Recommendation: exempt exactly one thing — Triton's conversational reply to a mention, posted into
  the same room and thread as the mention, containing no tool-driven external mutation** — with three
  bolted-on conditions (target derived from the trigger and never from model output; posted under the
  bot identity; still audited). Everything else stays gated (§I.12).
- **Cost of the exemption:** a documented hole in a security boundary, which must be written down in the
  Triton repo's own ADR line as well as here, or the next reader treats it as drift.
- **Cost of no exemption:** Triton renders an approval card asking permission to answer the question it
  was just asked; **and** — because there is no card renderer inside Google Chat (R3) — **Phase 5 must
  build an approval surface in Chat before Triton can reply at all**. That is a design task with a
  schedule, not a wiring task, and Appendix B would have to carry it.
- Note the related sharp edge either way: `actions._dispatch` knows only `fac_`/`gws_`/`qbo_`, so a
  gated-but-undispatched `gchat_*` tool **fails after the row is already marked `approved`** (R4).

---

**CQ-19 — Retention: how long do chat messages live, and who can delete them?**

**Deferred to Phase 6 by design** — decision #12 is a governance decision and the master map puts
governance in Phase 6. It is listed here so it is visibly deferred rather than forgotten, and because
two of its inputs must be decided *earlier* than Phase 6:

- **Now (Phase 1):** every high-volume chat DocType gets a `Logs To Clear` registration in the **same
  PR** that adds it, and implements `clear_old_logs(days)` on its controller — otherwise the row is
  silently removed on the next daily maintenance run and retention stops (§H.6;
  `notes_close_frappe.md` §4.3). Choosing the number can wait; **wiring the mechanism cannot**, because
  `add_default_logtypes` never updates an existing row.
- **Now (Phase 1):** the audit tables are **exempt from every purge**. `T-16` asserts it.
- **Phase 6:** the actual retention period, whether deletes are hard or tombstoned after N days, and who
  holds the Chat Auditor role.

---

**CQ-20 — Does chat supersede the existing "Comments App", or stay out of its lane?**

This repo already ships a Slack-style threaded, mention-aware, file-attaching comment UI on ~29
doctypes (`public/js/comments.js`, `comments_auto.js:18-43`, `api/comments.py`, Custom Field
`Comment.custom_parent_comment`) — and **its threading has been used twice in 13 months**: 2,021
`Comment` rows, of which 2 are threaded replies (`notes_infra.md:683-715`).

- **Recommendation: stay out of its lane in V1** — chat is channels and DMs; comments are document
  annotations — **but say so explicitly in the ADR**, and schedule a follow-up decision on retiring the
  comment threading.
- **Cost of staying out:** two overlapping surfaces, and users will ask which one to use on a Project.
- **Cost of superseding it now:** decision #10's per-document spaces would have to absorb the comment
  history, and retiring `custom_parent_comment` needs a fixture removal **plus** a `frappe.delete_doc`
  patch (the two-step deletion rule).
- **Shipping a second overlapping surface without saying which one wins is the predictable failure
  here.**

---

**CQ-21 — Two standalone repo improvements, offered rather than smuggled in.**

Both are independent of chat, both are one-liners, and both fix pre-existing problems this audit
surfaced. They are offered as separate PRs so they do not hide inside a feature branch:

1. **`default_log_clearing_doctypes = {"Notification Log": 30}`** in `erpnext_enhancements/hooks.py`.
   `Notification Log` is registered for retention in **neither** frappe's nor ERPNext's hooks, and has
   grown to **7,165 rows / 33.2 MB over 13 months** with no trimming. The hook also unlocks
   `clear_log_table` for the one-time catch-up delete, which today raises `ValidationError` for this
   doctype (§H.6; `notes_close_frappe.md` §4.1-4.4). **Cost:** choosing 30 vs 90 once, because the value
   is never updated on an existing row.
2. **Fix the misleading push-relay config.** `Push Notification Settings.enable_push_notification_relay
   = 1` with credentials present but `push_relay_server_url` null means `PushNotification.is_enabled()`
   **lies** (§J.4). Either set the URL deliberately or clear the flag. **Cost:** one write on a bench.
   **Cost of leaving it:** it will confuse the next person, including us in Phase 4.

---

**CQ-22 — Do you want to revisit "adopt Raven" at all?**

`DECISIONS.md` D2 (binding) is **reimplement, lifting Raven's proven schema decisions with
attribution**, and records the adopt question as **not phase-blocking**.

- **Recommendation: no — do not revisit.** As of 2026-08-07 there is no Raven line known-good on Frappe
  v16 (its desk bundle explicitly disables itself on v16, commit `a79c689`; `develop` imports
  `frappe.realtime.realtime` / `Socket`, which do not exist in v16 — corroborated by an independent read
  of v16 `frappe/realtime.py`). And even a v16-ready Raven owns a route and the page, which fights
  decision #8's "extend the existing floating widget".
- **Cost of our recommendation, stated so the ADR does not pretend otherwise: we own the chat core
  outright** — rooms, threads, unread, attachments — which is materially more code than adopting.
- **Cost of revisiting:** one throwaway v16 bench, `bench get-app --branch v2.8.11 raven` + install +
  migrate + smoke `/raven`. Half a day to find out, and the schedule then depends on someone else's v16
  port.

---

**CQ-23 — Vector storage: ERPNext-local BLOB + numpy, or delegate retrieval to Triton's Vertex AI RAG?**

- **Recommendation: ERPNext-local (`DECISIONS.md` D4)**, because production MariaDB is 10.11.18 (no
  `VECTOR` type), `numpy 2.5.1` is already installed, and it keeps the permission filter in the same
  process as the ACL. Four numeric revisit triggers are recorded (§I.5).
- **Cost:** we write and maintain a small retrieval stack, and the adapter swap is a future task.
- **Cost of the Vertex route:** Triton's RAG is **per-user corpora** — *"Every user gets their own
  RagCorpus… There is no shared corpus"* — which does not map onto membership-defined visibility. You
  would either duplicate every room into every member's corpus (N× storage, N× embedding cost, N places
  to redact on a delete) or build a shared-corpus feature Triton does not have. Deletion propagation is
  the hard part, and §I.7's invalidation is what stops Triton leaking redacted content.

---

**CQ-24 — Triton's chat bridge: reuse `ERPNEXT_GATEWAY_SECRET`, or mint a sibling? And what happens to
an unlinked user?**

- **Recommendation: mint a sibling bridge (~150 lines in Triton), and make the ERPNext OAuth link a
  named rollout task with a completion check** (§I.11.1).
- **Cost of reusing the existing secret:** ~0 lines, and the endpoint and secret keep saying "erpnext"
  while serving Chat — a naming smell that is also a blast-radius smell, since that same secret serves
  the telephony gateway.
- **Cost of the sibling:** ~150 lines plus the Triton version/CHANGELOG lockstep.
- **The sub-question that is not optional:** what does Triton do when the mentioning user has **not**
  linked ERPNext? Today the turn raises before the first token and the user sees *"Communication
  disruption detected."* Options: block the feature until everyone is linked; degrade to a
  read-only-without-ERPNext answer; or reply with an explicit "link ERPNext to let me help" message.
  **Recommendation: the third**, plus a pre-launch sweep of all ~20 active users.

---

**CQ-25 — May we land the `test_triton_personas.py` CI fix as the first commit, before Phase 1?**

- **Recommendation: yes.** Two lines in `ci.yml`. The suite is the **only** automated coverage of
  `triton_chat.py` and it has **never run** — `grep -c "test_triton_personas" .github/workflows/ci.yml`
  → 0. Every "streaming must survive" row in Appendix A is otherwise defended by a file that has never
  executed (§I.12.1).
- **Cost:** one tiny PR, plus whatever the suite turns out to be failing on today — which is exactly the
  information we want before Phase 5 touches the relay.
- **Cost of waiting:** Phase 5 refactors the SSE relay with no regression net.

---

#### K.2.1 What blocks what

| CQ | Blocks | Must be answered before |
|---|---|---|
| **CQ-1** | The Google auth identity → the DWD scope list, which is frozen in one super-admin session | **Phase 1, first** |
| CQ-5 | Follows from CQ-1 | Phase 1 |
| CQ-12 | Whether the identity-mapping design must handle non-employees | Phase 1 schema |
| CQ-16 | Read-receipt schema sizing | Phase 1 schema |
| CQ-10 | The retrieval gate's per-room upper bound | Phase 1 schema, Phase 5 gate |
| CQ-2, CQ-3, CQ-4, CQ-7, CQ-8, CQ-9 | The notification table's shipped defaults | Phase 4 (defaults are already resolved in §H, so these are confirmations, not blockers) |
| CQ-6 | The service worker's rendering | Phase 4 |
| CQ-14, CQ-17, CQ-18, CQ-23, CQ-24 | Phase 5's scope | Phase 5 |
| CQ-11, CQ-13 | Rollout comms and the sync spec's lossy list | Phase 2 |
| CQ-15 | The SPA's presence rendering | Phase 3 |
| CQ-19 | Retention | Phase 6 by design; the *mechanism* wires in Phase 1 |
| CQ-20, CQ-21, CQ-22, CQ-25 | Nothing — independent, and CQ-25 is worth doing immediately | — |

---

## Consequences

### What this buys

- **One conversation, two surfaces, and the ERP row is the one that survives.** A message written in
  the SPA is an ERPNext document with a permission model, an audit trail and a retention policy; the
  Google Chat copy is a mirror of it. Nothing about the design depends on Google keeping a record.
- **Chat content is reachable by exactly one code path.** Zero DocPerm on the content DocTypes plus
  the `_gate.py` denylist means `/api/resource`, the desk, global search, export, and every generic
  MCP tool are closed by construction rather than by policy ([§F.18](#f18-the-permission-model-followed-through-to-its-consequences),
  [§I.2](#i2-i5--the-denylist-on-the-generic-mcp-tools-by-the-mechanism-that-actually-closes-it)).
  Retrieval goes through one whitelisted method that derives `allowed_rooms` server-side and audits
  every non-participant read.
- **No new dependency, no new secret, no new deploy path.** Embeddings ride `numpy 2.5.1`, already in
  the bench. Google auth is keyless — no private key file, no rotation, nothing in
  `site_config.json`. Chat deploys with the app it lives in.
- **The delivery guarantee survives a deploy.** Because the outbox row is committed with the message
  and the sweeper is the guarantee, the `FLUSHDB` that destroys queued jobs on every production
  deploy costs latency, not messages.

### What it costs

- **We own the chat core outright.** Rooms, threads, unread, mentions, attachments, read receipts,
  presence, search — all of it, because Raven is not adoptable on v16 ([§C.5](#c5-the-cost-stated-honestly)).
  This record does not pretend that is cheaper than adopting; it argues that adopting is not
  available and that the widget constraint means we write the frontend either way.
- **Zero DocPerm is a new precedent in this app, and it removes the desk.** 0 of 187 existing
  DocTypes ship an empty `permissions` array. For chat there is no list view, no form view, no
  report builder, no export, no link-field autocomplete and no global search. **Phase 6's admin
  oversight surface must therefore be a custom desk Page or a `www/` portal page, and Phase 6 must
  not discover that late** ([§F.18.2](#f182-what-this-costs-and-what-it-therefore-forces-in-phase-6)).
- **Two Google identities means two independent failure modes on one conversation** — a space where
  the human relay works and the Chat app is missing shows people but no `@triton`; the reverse shows
  a bot talking to itself. `api/integrations_health.py` must report both separately (§E.2).
- **~50 hand-rolled lines of assertion building**, because
  `google.auth.impersonated_credentials` has no `subject` parameter. That is the same tax
  [ADR 0004](0004-no-vendor-sdks.md) already documents, and it gets the same treatment: its own
  module, its own unit test, not a candidate for casual refactoring ([§E.4.2](#e42-sizing-it-as-its-own-module-with-its-own-unit-test)).
- **Every chat PR pays this app's release ritual** — version bump in two files, a CHANGELOG entry,
  and the `version-sync` hard gate — plus the `unittest`/`pytest` CI split, where a new bench-free
  *pytest* suite added to a unittest module list silently never runs (§A.6.1).
- **Decision #3 does not hold for people running the native Google Chat client.** Under the
  recommended human-attribution design they receive Chat's own notification in addition to ours.
  This is the trilemma, it is unavoidable, and it is CQ-1
  ([§E.3](#e3-the-trilemma--the-finding-that-changes-the-product)).

### The invariants a future contributor must preserve

The `I-n` numbering is the master prompt's. The `CHAT-*` names are this record's own, and they are
the ones a reviewer can actually grep for.

| # | Invariant | Where it is enforced |
|---|---|---|
| **I4** | Retrieval enters through one whitelisted method that derives `allowed_rooms` server-side; a room list is never an argument | [§I.3](#i3-the-gated-entry-point--one-derived-allowed_rooms-never-an-argument); source-level test in §I.2.4 |
| **I5** | Generic tooling cannot ingest chat content | Layer 1 zero DocPerm (§F.18.1) + the `_gate.py` `_safe_execute` denylist ([§I.2](#i2-i5--the-denylist-on-the-generic-mcp-tools-by-the-mechanism-that-actually-closes-it)); refusals write an `AI Action Log` row |
| **I6** | Notification suppression is decided **server-side**; the client only reports | [§H.3](#h3-the-exact-server-side-inputs); test = stub the client's suppression and assert zero notifications for row 1 |
| **I7** | Exactly two surfaces per notifiable event, kept in sync, and **never an email** | [§H.1](#h1-the-truth-table) truth table + `test_chat_notifications_never_email` ([§H.5.2](#h52-the-after_insert-email-path--the-exact-suppression-and-the-test-that-asserts-zero-emails)) |
| **I8** | Realtime security rides the permission-checked doc room; `has_permission` returns an explicit boolean on every path (`None` denies on v16) | [§H.4.1](#h41-the-channel-split--confirmed-not-provisional), [§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved); see seam **S1** |
| **I9** | Every non-participant read is audited | `Chat Retrieval Audit` + child room table ([§F.12](#f12-chat-retrieval-audit--child-chat-retrieval-audit-room--decision-12s-audit-log)), written per retrieval call ([§I.7.3](#i73-the-audit-boundary-sits-here-too)) |
| **I10** | The audit trail survives a user-facing delete; a rolling summary may add information but cannot unsay it | Soft delete + tombstone ([§F.6.5](#f65-delete-is-soft-and-that-is-a-policy-choice-the-human-must-confirm), [§G.7.4](#g74-what-a-delete-leaves-behind-and-what-that-means-for-i10)); edit/delete inside a covered span forces a **full digest rebuild** ([§I.7](#i7-invalidation--an-edit-or-delete-inside-a-covered-span-forces-a-full-rebuild)) |
| **I11** | Context assembly never exceeds the configured token ceiling, and degrades down a deterministic ladder | [§I.8](#i8-the-token-budget--a-hard-ceiling-with-a-deterministic-degradation-ladder) |
| **I12** | For a fixed user, the concatenation of the stable prompt segments is byte-identical across turns | [§I.9](#i9-prompt-cache-ordering-as-a-testable-invariant); §I.9.1 confirms Triton's current assembly already satisfies it |
| **I13** | Triton's ERPNext permissions and Triton's Chat posting identity are two different things | [§I.11](#i11-tritons-identity--i13-and-the-rollout-task-hiding-inside-it); test asserts the retrieval call carries the **asking user's** identity |
| **I14** | Presence, typing and read receipts are ERPNext-sourced by construction — Google's equivalents are self-scoped only and cannot be mirrored | [§F.14](#f14-presence-and-typing-live-in-redis-not-in-doctypes), [§F.15](#f15-read-receipts--the-reconciliation-stated-so-no-later-phase-has-to-guess), §I.13 lossy list |
| **I1, I2, I3, I15** | `VERIFY: the master prompt names invariants I1–I15; the Phase 0 evidence base quotes only I4–I14 by content, so I1, I2, I3 and I15 are not restated here rather than being invented — settle by reading the master prompt's invariant register — blocks: nothing structural, but Phase 1's test plan should cover them by number` | — |
| **`CHAT-SEQ-1`** | `seq` is an explicit `Int` assigned inside the insert transaction, unique per `(room, seq)`, never a timestamp | [§F.16.1](#f161-invariant-chat-seq-1--sequence-assignment) with its concurrency test |
| **`CHAT-WATERMARK-1`** | Every cache key, digest and chunk uses `(max(seq), count(*), max(modified))` — never `seq` alone | [§F.16.2](#f162-invariant-chat-watermark-1--the-three-value-watermark), [§I.7.1](#i71-it-is-tied-to-the-three-value-watermark-and-only-the-three-value-form-works) |
| **`CHAT-RT-1`** | Every chat `publish_realtime` passes `room=` explicitly, from one chat-owned helper — never bare `user=`, never `doctype=`+`docname=` | [§F.18.3](#f183-the-realtime-consequence--and-a-deviation-from-a-sibling-note-declared), [§H.4.4](#h44-the-events-and-the-realtime-hygiene-rules-that-bind-them); Phase 1 lint rule. The fallback is `get_site_room()` = `"all"`, and inside a background job `task_id` outranks even an explicit `user=` |
| **`CHAT-RT-2`** | No chat event may be named `list_update` or `docinfo_update` — both overwrite an explicitly passed `room=` | same; Phase 1 lint rule |
| **`CHAT-RT-3`** | The fan-out helper refuses `Guest`; `user:Guest` and `website` are shared rooms | same |
| **`CHAT-EXCL-1`** | `"Chat"` is in `utils/triton_sync.py:30-33`'s `excluded_modules`, landed in the same commit as the first chat DocType | §A.2.6, §D.5 item 6 |
| **`CHAT-PERM-1`** | Every `permission_query_conditions` entry has a `has_permission` twin, added in the same commit — the existing 10-and-10 parity is the doctrine | §A.2.12, §H.4.2 |
| **`CHAT-BUNDLE-1`** | Chat's global assets are imported into the existing bundle entries — never a new `app_include_*` line, never a raw `/assets` path | [ADR 0008](0008-global-assets-ship-as-bundles.md); §A.5, §D.5 item 3 |

### What would have to change before any of this is worth revisiting

- **App placement** — revisit if chat write volume forces a separate database or a separate release
  train: concretely, if the chat tables exceed the largest existing table by an order of magnitude,
  if `bench migrate` duration becomes chat-dominated, or if a chat hotfix cadence starts colliding
  with the ERP release cadence (§D.3). None is near today.
- **Raven** — revisit only if the human wants to re-open adopt, in which case the experiment is
  "does v2.8.11 install and run on a v16 bench" (CQ-22, [§C.6](#c6-the-raven-on-v16-verify-recorded-as-non-blocking)).
  Note that a v3 rewrite is visibly incoming.
- **Google auth** — revisit if Google ships a `sender` override outside import mode, or if `cardsV2`
  under user auth leaves Developer Preview (§E.1). Either would collapse the trilemma.
- **Vector storage** — the four numeric triggers, and they are not negotiable: p95 retrieval
  > 400 ms; > 20,000 candidate chunks after filtering; > 250,000 chunks total; or a MariaDB upgrade
  to 11.8 LTS ([§I.5](#i5-vector-storage--decisionsmd-d4-its-measured-reason-and-the-alternative-we-are-not-taking)).
  Reaching one makes the adapter swap a scheduled task, not a debate.
- **The realtime channel** — revisit if cooperative eviction proves intolerable, in which case
  [§F.18.3](#f183-the-realtime-consequence--and-a-deviation-from-a-sibling-note-declared)'s per-user
  fan-out is the recorded fallback and an app-owned `realtime/handlers.js` is the escape hatch
  ([§H.4.2](#h42-the-collision-this-creates-with-the-mcp-denylist-and-how-it-is-resolved)).

## What Phase 1 may assume

A fresh session starting `PHASE_1_foundations_and_auth` may take the following as settled and must
not re-derive them. Everything else is in [§K.1](#k1-the-verify-register) with a settlement method.

**Placement and shape**

1. Chat is a module named `Chat` inside `erpnext_enhancements`, at `erpnext_enhancements/chat/`,
   with every DocType JSON declaring `"module": "Chat"` and a `Chat` line in `modules.txt` — or
   `tests/test_doctype_modules.py` fails the build. The full registration checklist is
   [§D.5](#d5-where-chat-lives-and-how-it-registers), ten items, each traceable.
2. Raven is not a dependency and not a fork. The schema decisions being lifted, with attribution,
   are listed in [§C.4](#c4-what-we-lift-from-ravens-schema-and-why-each-is-proven).
3. Field names are `DECISIONS.md` D5's, verbatim. The alias table in
   [§F.1](#f1-the-field-name-canon-d5-and-the-alias-map-every-later-phase-must-read-through) is
   normative for reading later phase prompts.

**Platform facts, measured or read this session**

4. Production MariaDB is `10.11.18-MariaDB-0+deb12u1`: no `VECTOR` type, no `VEC_*` functions.
   Production Redis is `7.0.15` with `maxmemory-policy allkeys-lru`, so **no per-field TTLs
   (`HEXPIRE`)** and keys may be evicted before their TTL. `numpy 2.5.1` imports in the prod bench.
5. Every production deploy `FLUSHDB`s **both** Redis instances (13000 cache, 11000 queue) and
   restarts every process. Queued jobs do not survive a deploy. The outbox sweeper is the delivery
   guarantee.
6. Frappe v16 `website_route_rules` supports a werkzeug `<path:…>` catch-all, and
   `{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}` is sufficient; `/chat` itself
   needs no rule. Deep links survive a hard refresh (§K.1.3 **B1**).
7. v16 socket.io **permission-checks `doc:` room joins** by calling back into Python with
   `ptype="read"`, and **`user:` rooms are not client-joinable at all** — there is no
   `user_subscribe` verb (§K.1.3 **B4**). A refused `doc_subscribe` is *silent*: never treat
   "I emitted `doc_subscribe`" as "I am subscribed".
8. `publish_realtime`'s final fallback is `get_site_room()` = `"all"`, which every System User is
   already sitting in; and inside a background job `frappe.local.task_id` outranks an explicit
   `user=`. `CHAT-RT-1/2/3` exist because of this.
9. Frappe v16 has **no presence primitive** — no online-user, no last-seen, no live-presence
   function. Every input to the chat presence signal is purpose-built.
10. Frappe has **no BLOB fieldtype**; `Long Text` + base64 of raw `float32` is the embedding column
    ([§F.11.2](#f112-the-embedding-column--a-contradiction-with-decisionsmd-d4-reported)).
11. `gchat_message_name` and `gchat_space_name` are `Data(255)` with `unique: 1`;
    `client_message_id` is `Data(64)` in a composite `unique(room, client_message_id)`. 255 ×
    utf8mb4 = 1,020 bytes, well inside InnoDB's 3,072-byte index limit (§K.1.3 **B3**).
12. The corrected repo numbers: **513** Custom Fields, **409** Property Setters,
    `permission_query_conditions`/`has_permission` at **10 and 10**, ~**20** enabled users. Do not
    quote the stale figures in `CLAUDE.md`, `README.md:147` or `fixtures/README.md`.

**Google facts, verified against live documentation on 2026-08-07**

13. Domain-wide delegation is *user* authentication, and it is the only mode in which a relayed
    message renders as the real person. `chat.bot` app auth is the only mode that permits `cardsV2`,
    `NOTIFICATION_TYPE_SILENT`, and it **requires no administrator approval**.
14. App auth **cannot upload attachments** (`media.upload` requires user auth), and silent messages
    cannot start or reply to a thread. `spaceThreadingState` is **Output only**.
15. Chat apps are enabled at the **top** organizational unit; a pilot-OU rollout is impossible. The
    pilot gate is the Chat app's Visibility setting — which scopes `@triton` and **not** the DWD
    human relay, so an ERPNext-side `restrict_to_whitelist` gate is required too (§E.2, §E.5.1).
16. `google.auth.impersonated_credentials` has **no `subject` parameter**, so the DWD assertion is
    hand-rolled and signed by IAM Credentials `signJwt` — no key file, no rotation
    ([§E.4](#e4-the-keyless-gcp-finding)).
17. `media.upload` shares the per-space **1 write/second** bucket with `messages.create`, so the
    token bucket must charge an attachment **two** tokens ([§G.1.4](#g14-rate-limiting--a-per-space-token-bucket-and-what-it-must-charge)).

**Process**

18. Zero register items are BLOCKING as of this record (§K.1.3). What was blocking is closed or has
    become a named residual with a phase owner.
19. **CQ-1 must be answered before Phase 1 freezes the DWD scope list**, because that list is set in
    one super-admin session and changing it later is another one. **P3-1** — the
    `Chat Room`-carries-DocPerm split — must be signed off before Phase 1 writes any DocType JSON.
20. Appendix A exists and is a Phase 3 gate. Appendix B follows the master phase map, not Phase 0
    §4.L's alternative numbering (`DECISIONS.md` D7).
21. Phase 0 wrote no code. The only repository changes are this record, its two appendices, the
    `decisions/adr/README.md` index row, `CHANGELOG.md` and the two version files.
