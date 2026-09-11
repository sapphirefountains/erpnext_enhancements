# WI-072: HR module + Training redesign (program plan)
**Phase:** 5   **Type:** APP_CODE   **Size:** XL (one branch, one release)
**Blocked by:** nothing   **Blocks:** WI-071 sub-phases E/F/G/H (they should land on top of this)

## Why

Nik asked (2026-09-10) for the Training module to be reachable and useful: a single HR area an
employee opens to see their own record, tiered sign-off so a Senior Technician can attest for a
Junior, a company position tree, a WYSIWYG that a non-technical author can actually use, and a
light social layer. Every decision below was taken with him and is **not** to be re-litigated;
see the "Decisions" section.

The exploration that preceded it found that the module is far more built than it is *used*, and
that the reasons are not the ones the ask assumed. These are verified against production on
2026-09-10, not inferred:

- **Distribution has never been switched on.** Zero `Training Assignment Rule` rows;
  `auto_assign = 0` on all six courses; five `Training Assignment` records exist in total, ever;
  three completions; **two of sixteen active employees have any training record at all**. A
  learner profile built today shows fourteen people an empty room.
- **The HR person cannot see Training.** Kendalyn Harris holds HR Manager, HR User, Expense
  Approver, Leave Approver and nothing else. **No Training DocType grants HR Manager any
  DocPerm**, and Frappe hides a workspace whose module is not in `allow_modules`, silently
  (`frappe origin/version-16:frappe/desk/desktop.py:38-44`, swallowed at `:421`). For her,
  Training is not hard to find; it is absent, and clicking its desk tile is a PermissionError.
  She also has no `Employee` record.
- **The org tree already exists.** `Employee` is a full nested set on `reports_to` in ERPNext
  v16 core (`is_tree: 1`, `nsm_parent_field: reports_to`), as is `Department`. What is missing
  is a *position* ladder — `Designation` is flat, two fields, no rank — and a visual chart
  (hrms ships one; hrms is not installed).
- **The ladder contradicts the reporting line.** Jesse Griffin (Senior Technician) has **zero
  direct reports**. All four Junior Technicians report to Clegg Mabey (Project Manager) — and so
  does Jesse. `Reports To` routing can never reach the one person who has actually watched them
  work. This is why authority must be tier-based rather than `reports_to`-based.
- **Sign-off does not close the loop.** `Training Signoff` has no `on_submit`; recording
  *Competent* never moves the assignment to Completed. And `competent_signoff_name`
  (`training/signoff.py:347-366`) filters on course + user + outcome + docstatus with **no date
  clause**, so when `TRN-CRS-00001` recertifies at 24 months the gate re-opens against the
  original two-year-old attestation with nobody watching.
- **The newer authoring surface is unreachable and broken.** `training-canvas` is referenced
  nowhere outside its own directory. `training_canvas.js:115` declares lowercase tones including
  a phantom `"info"` and `:1075` stamps `callout_tone = "info"` on every new Callout;
  `training_content_block.json:120` declares `"\nTip\nWarning\nDanger"`, `_validate_selects`
  throws, and `save_draft_version` runs a full `lesson.save()` — so **the first Callout an author
  creates kills the whole lesson autosave**. `tests/test_training_canvas.py` is not in `ci.yml`.
- **Learners cannot see author-uploaded images or PDFs.** Both surfaces upload `is_private = 1`
  attached to `Training Lesson`; no learner role holds read on that doctype, and
  `get_media_url` signs only Video and External Embed, so `blocks.js` falls back to a raw
  `/private/files/…` path that 403s.
- **A live privacy leak.** `Training Badge`, `Training Badge Award` and `Training Learner Stat`
  each grant `Training Learner {read: 1}` and **none** is registered in `hooks.py`'s
  `permission_query_conditions` / `has_permission` lists. `Training Learner` is held by customer
  Website Users, so every staff member's points, streaks and badges are enumerable through
  `/api/resource` today.
- **Icons are not the problem.** `Training`, `Workforce` and `HR Dashboard` already have Desktop
  Icons with real artwork, `hidden = 0`, rendering now. Prod carries **61 workspaces and 78 desk
  tiles**, including empty hand-made `HR Hub`, `Operations Hub` and `Production Hub` shells.
- **The content gap is the real story.** The only operational/safety course on the site is
  "Draining a Fountain Basin Safely", two lessons. Ninety-seven lessons across four *unpublished*
  courses are all about using ERPNext.

## Decisions (taken with Nik 2026-09-10 — do not re-litigate)

1. **New Frappe module `HR Enhancements`, labelled "HR". Move no existing DocType.** A module
   move is a per-doctype migration against `tests/test_doctype_modules.py` plus ~177
   `erpnext_enhancements.training` references and 31 dotted paths in `hooks.py`; and **hrms ships
   a module called `HR`**, so claiming that name repeats the Plaid Settings collision
   (v1.361.0). Navigation in v16 is done by Workspace Sidebars and Desktop Icons, not module
   membership — re-homing buys nothing navigationally.
2. **Tier ladder = a new `Position` nested-set tree DocType** with `tier` (Int) and a
   denormalised `job_family`. `Employee` gains `custom_position`. `Designation` is left untouched
   as the HR job title, and the docs must say plainly that changing a Designation changes no
   authority. This DocType is also ask #6's position tree, and (being non-child) it is what makes
   the new module pass the `allow_modules` gate.
3. **Sign-off authority = strictly higher `tier` within the same `job_family`, company-wide.**
   No department constraint. **Never a same-tier peer** — otherwise two Junior Technicians sign
   each other's basin course.
4. **Sign-off must work on a phone as well as the desk.** Jesse is a field technician; authority
   he can only exercise from a desk he does not sit at is not authority.
5. **All sixteen proposed features are in scope** (see Deliverables).
6. **Promotion: the system proposes, a human promotes.** Auto-promotion is privilege escalation
   directly into the sign-off predicate — a learner could complete their way into authority over
   their peers.
7. **PTO = request → approve → team calendar.** No balances, no accrual, no carryover.
8. **A sign-off expires with the course's recertification window.**
9. **Desk navigation = per-role home grids.**
10. **HR Manager gains real DocPerms and blanket sign-off authority. `triton@` is left as-is** —
    Nik declined trimming the service account's HR Manager / Training Manager / Training Author
    roles. Consequence accepted and recorded here: the AI identity can attest to human competency.
11. **Assignment: build the "Assign to…" UI *and* seed sensible default rules** for Nik to review.
12. **Uncertified dispatch stays advisory (warn), but is rewritten so it actually fires** — today
    `_uncertified()` only produces a finding from an OPEN assignment or a lapsed completion, so a
    Required course a person was never assigned yields nothing, and with zero rules configured it
    has never fired for anyone.
13. **Delivery: one branch, one release.** Nik chose this over incremental PRs knowing the
    tradeoff (long time to first landing, large review surface, and the privacy leak in §Why
    stays open for the duration — it is therefore the **first commit** on the branch so it can be
    cherry-picked out at any moment).

## Native-first check (ADR-0002)

- **Org tree** — native. `Employee` and `Department` are already nested-set trees; use
  `/app/employee/view/tree` and build only the *position* ladder and the visual chart.
- **Position ladder** — APP_CODE. `Designation` is flat and is already consumed by the
  auto-assign rule engine and payroll reporting; overloading it means every future reader must
  know the label means two things.
- **PTO** — APP_CODE, reluctantly. hrms would give this free but is not installed and installing
  it collides with the `HR` module name and six `Training *` doctype names.
- **Reactions / comments** — APP_CODE. Frappe v16 **deleted** `frappe/social/` (Energy Point Log,
  Review, the user-profile page) — verified absent on `origin/version-16`. And `Comment` cannot
  be reused: `public/js/comments.js` is Vue and needs the desk bundle, which `www/training.py`
  forbids, and `tests/test_training_endpoint_surface.py:213-217` hard-asserts the player contains
  no `innerHTML`.
- **Achievement feed** — APP_CODE, on a **new** `Training Achievement` record, never on
  `Training Completion`. Both reuse candidates begin with a read check on the referenced document
  (`api/comments.py:38` and `:97`; `frappe origin/version-16:frappe/desk/like.py:36`), so "let
  colleagues react to a completion" reduces to "publish second-attempt scores and revocation
  reasons company-wide". The achievement carries no score, no attempt, no coverage, no failure.
- **Analytics / skills matrix** — native-first: Query Reports and Dashboard cards over the data,
  custom only where a card cannot express it.
- **AI drafting** — already built (`draft_course_spec` → `author_course_from_spec`, validated,
  deterministic, behind the unreviewed-question publish gate). Surfacing it in the editor is
  wiring, not construction.

## Deliverables

**0 — Live defects (first commit, independently cherry-pickable)**
- Register `Training Badge` / `Training Badge Award` / `Training Learner Stat` in
  `permission_query_conditions` + `has_permission`; close the customer-visible read leak.
- `Training Signoff.on_submit` → close the assignment; react to cancellation.
- Date-scope `competent_signoff_name` to the recertification window (decision 8).
- Canvas Callout tone: one vocabulary across `training_canvas.js`, `training_author.py`,
  `blocks.js`, `player.css`, `training_content_block.json`.
- Empty-block validation: warn while `docstatus == 0`, enforce in `publish_version` (which today
  writes with `db.set_value` and never re-runs lesson validation — so autosave is currently the
  only thing stopping an empty block reaching a learner).
- Sign learner media: extend `get_media_url` to Image/PDF blocks.
- `tests/test_training_canvas.py` into `ci.yml`.
- HR Manager DocPerms across the Training doctypes.

**1 — `HR Enhancements` module + navigation**
- Module registration, workspace labelled "HR", `workspace_sidebar/hr.json`, `TILES["HR"]`
  + generated artwork, per-role home grids, retire the empty Hub shells.

**2 — `Position` tree + tiers + org chart**
- `Position` nested-set DocType, `Employee.custom_position`, seeding patch from today's twelve
  Designations, tree view, visual org chart, promotion-eligibility proposal.

**3 — Sign-off authority**
- `training/authority.py::authority_basis()` read from **five** call sites, `before_submit`
  included — `record_signoff` sets `ignore_permissions = True` and the Desk form path submits
  without ever touching the endpoint, so a rule that lives only in `_assert_may_sign` is not a
  rule. Visibility arms on `signoff_query_conditions` / `has_permission` / `get_signoff_queue`.
  Positions snapshotted onto the submitted document.
- Phone/field sign-off surface on `/training`.

**4 — Learner + HR profile**
- The profile page: badges, completions, assignments, optional library, plus the HR facts
  (title, department, manager, hire date, tenure, anniversary, assigned devices/vehicle).
- Browsable colleague profiles (badges and completed courses only — never scores).

**5 — Credentials + compliance**
- External credential register, expiry horizon + renewal alerts, skills matrix,
  record-a-training-that-happened, audit-ready roster export, and the rewritten dispatch advisory.

**6 — Social**
- `Training Achievement`, `Training Kudos` (reaction + optional 280-char plain-text note),
  the feed, leaderboard reshape (top 5 + your rank, streaks self-only, opt-out in a new
  `Training Profile Preference` — **not** on `Training Learner Stat`, which is explicitly
  disposable), and an achievement backfill so the feed is not three items long on day one.

**7 — Authoring**
- One editor (canvas absorbs chapters, quiz, checkpoints, video registration, preview; the
  classic builder stays as the power tool until each capability lands), AI drafting in the
  editor, template gallery, import from Doc/PDF/Process Document.
- Standardise stored rich-text shape: Frappe's Quill control everywhere; drop
  `document.execCommand` (`training_canvas.js:735-746`).

**8 — PTO**
- Request → approve → team calendar.

**9 — Assignment**
- "Assign to…" action (department / designation / position / named people), a real rules editor,
  and seeded default rules for review.

## Status (v1.386.0, 2026-09-10)

Everything below is built, on branch `claude/hr-training-module-redesign-6d052f`, with the
full CI suite (156 steps) green after every commit.

| | |
|---|---|
| **D0** Live defects | done |
| **D1** Module, workspace, tile, sidebar | done *except per-role home grids — see below* |
| **D2** `Position` ladder, org tree, seeding | done |
| **D3** Tiered sign-off + phone surface | done |
| **D4** Profile + colleague profiles | done |
| **D5** Credentials, expiry, skills matrix, sessions, dispatch advisory | done *except the audit roster export* |
| **D6** Achievements, kudos, feed, leaderboard reshape | done |
| **D7** Editor reachable, starters, AI button | **partial** — see below |
| **D8** Onboarding checklist + PTO | done |
| **D9** Assignment UI, group targeting, sweep, seeded rules | done |

### Deliberately not done, and why

**Per-role desk home grids (D1).** The mechanism exists — a `Workspace` carries a `roles`
child table and `get_links` filters by it — but on this site it would achieve close to
nothing: every active employee holds roughly thirty roles, including `Stock Manager` and
`Purchase Master Manager`. Role-gating the grid is only meaningful *after* a role audit,
which is separate work with its own risk (see [[frappe-role-audit-traps]]: never hand-query
`tabDocPerm` to decide a role is safe to remove; `Custom DocPerm` replaces wholesale).

The other half — retiring the empty hand-made `HR Hub`, `Operations Hub` and `Production
Hub` workspaces, which carry `content = "[]"` and no links — means deleting records
somebody created by hand in the Desk. That is Nik's data and his call, not a thing to do
unasked. **Needs a decision from him**, with a proposed before/after list.

**The canvas port (D7).** `training_canvas.js` is now reachable and its defects are fixed,
but roughly 2,600 of the classic builder's 3,842 lines still have no canvas equivalent:
chapters, the quiz pool, checkpoint placement, Drive video registration, transcripts and
the learner preview. Porting them is a large, bench-gated piece of product work and the
right moment for it is when somebody is actually blocked by having to use two pages. Both
surfaces are linked from the course form in the meantime, with the visual one primary.

**The audit roster export (D5).** One printable per person or per crew — every credential,
completion and sign-off with dates, expiries and the attesting supervisor. The data all
exists now and the Skills Matrix answers the live question; this is the version you hand an
insurer. Small work, not yet done.

## Guardrails

- Customers (Website Users holding `Training Learner`) see **none** of HR, the feed, profiles or
  the leaderboard. `gamification.py:11-17` already states the doctrine — "an optional privacy
  filter is a privacy filter somebody eventually leaves out" — and takes `learner_type` as a
  mandatory positional. Every achievement read does the same.
- `Training Learner` keeps `desk_access = 0`; the player stays a website page. Desk access would
  move the licensed-user count.
- Answer keys stay server-side; the player stays `innerHTML`-free; the CSS class contract test
  stays green.
- Every change bumps `__init__.py` + `package.json` + `CHANGELOG.md`.
