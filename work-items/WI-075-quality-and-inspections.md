# WI-075 — Scope that can be inspected, and failures that get re-checked

**Status:** in progress — A through J shipped, v1.446.0 → v1.458.0 (A–F renumbered on rebase; `main` had taken 1.444/1.445). Slice 1 and Slice 2 are complete. I delivered the trigger engine; the master checklists followed as **strawmen seeded Draft** at Nik's request (v1.457.0), and J made a period review compute its own numbers (v1.458.0).
**Branch:** `claude/quality-inspections-planning-691339` (off `main` at v1.443.0)
**Tracked:** PRJ-00580, TASK-2026-02013 with a child per deliverable (A–N)
**Decides:** [ADR-0012](../decisions/adr/0012-project-inspections-do-not-use-quality-inspection.md)

## Why

A five-document design package — a team deck, a companion paper, a field-level build spec, a
scope-to-inspection process document, and a screen capture of the live form — specifies a Quality
module for this company. It is a complete design and not a build, and it was written against how
the business works rather than against what is in the ERP.

The problem it exists to close is named plainly in the companion paper. Scope, contract terms,
pricing and inspection standards have historically lived in four separate places: a verbal
understanding from a hand-off meeting, a contract clause, an estimator's spreadsheet, and whatever
an inspector happened to check on site. When those four agree, nothing goes wrong. When they
disagree there is **no single record either side can point to**. The blasting subcontractor dispute
on Union Station is the worked example: labour hours invoiced with no documented cap, no
verification method and no measurable acceptance standard tied back to the original scope. Nobody
was wrong to argue their side, because there was nothing to argue against.

The design principle that follows, and the one every deliverable below is checked against: **if a
criterion cannot be inspected it should not be in the contract, and if an inspection item does not
trace back to something contracted, question why it exists.**

## What prod actually has (measured 2026-09-14 via the assistant MCP)

**The Quality module is empty.** All eleven core Quality doctypes have **zero rows** — Quality
Inspection, Quality Inspection Template, Quality Goal, Quality Review, Quality Action, Non
Conformance, Quality Feedback, Quality Meeting, Quality Procedure, and the two Inspection
Parameter/Reading children. The `Quality` workspace exists, is public, links all of them, and
nobody has ever used it.

That is a **window, not just a blank slate**. Select options on those doctypes can be replaced by
Property Setter today with no data migration. The first saved record closes it permanently: an
off-options Select value makes a row unsaveable and no `ignore_*` flag bypasses `_validate_selects`.

**Much of the front-end chain is already built, and better than the documents assume.**

| The documents ask for | Already on prod |
|---|---|
| CRM Won → Hand-off Meeting trigger | Built **and enforced**. `crm_enhancements/handoff.py` blocks Project creation from a Closed-Won Opportunity until a meeting is booked; `process_steps.py` is a 7-step engine with SLAs and escalation; 707 `Project Process Step` rows against 7 templates |
| Scope authored once | Eight scope child tables on **both** Project and Opportunity, plus `custom_general_scope_description` and `custom_scope_contributors`. The data exists; the **lock** does not |
| Contract fed from the locked scope | `Project Contract` — 16 rows, submittable, 8 agreement types, `scope_of_work` composed from those child tables, 8 seeded templates, a signature pipeline |
| Subcontractor MSA with standing rates | Already on `Project Contract`: `msa_tier`, `msa_contract`, `msa_effective_date`, `rate_journeyman`, `rate_apprentice`, `rate_equipment`, `materials_markup_percent`, `not_to_exceed`. `validate_msa_gate` already refuses an SOW without a **Signed** MSA for that Supplier |
| Master template → frozen instance | The exact pattern is live in `sapphire_maintenance`: Template → Template Section → Section Item authoring, Record → Result (Pass/Fail/Replace, notes, photo) execution, mandatory-row counters, signature pad, touch-first wizard. 48 templates, 185 sections, 393 items |

**And the vocabulary does not match.** Project types on prod are Service 354, Build 78, *blank* 73,
Events 62, Design 58, Internal 13, Other 8, Overhead 5, Group Projects 3. **"Service" is the
documents' "Maintenance"** and is the largest category by a factor of four. **"Controls Fab" exists
nowhere** — controls work is `Control Panel Design` in `water_engineering`, 2 rows, one of which has
no project. "Products" exists only as a Value Stream with 7 projects, and value streams cover just
127 of 654 projects, so that axis cannot carry a rule on its own.

**Nor do the roles.** President, Production Manager, Account Executive, Controller and Inspector do
not exist. Prod has the `* Team` roles plus `Project Manager` and `Quality Manager`.

**Nor are there many people to spread this across.** 20 Employees, 15 active, 14 of those carrying a
Position. The Technician family is 3 Junior, 2 Senior, **0 Master**. All 55 `Position Requirement`
rows are type `Credential`; **zero** are `Training Course` or `Sign-off`, so this work item's four
courses will be the first required training ever attached to a rung.

**One gap has been documented and waiting since it was seeded.**
`Project.custom_quality_sign_off_by` and `custom_quality_sign_off_date` exist as Custom Fields
**referenced by no code at all**, and `patches/seed_process_maps_finance_production.py:305` seeds a
process step "QA / quality sign-off before install" with `coverage: "Gap — To Build"`. This work
item is that gap.

## The two showstoppers in core, found in review

Both verified against `erpnext origin/version-16` — **not** the sibling checkout, which reads
`17.0.0-dev` while prod runs 16.x.

**1. The Property Setter on `Quality Action.status` alone would brick the doctype.** The entire v16
controller is one line:

```python
def validate(self):
    self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"
```

An action with **no `resolutions` rows** — precisely what a punch-list item is — evaluates `any([])`
as `False` and saves as **Completed**. Every punch item would be born closed. And once the Property
Setter replaces the options with the five-state lifecycle, that same line writes the literal
`"Completed"`, which is no longer a valid option, so `_validate_selects` raises on **every** save.

So the Property Setter and `override_doctype_class["Quality Action"]` are **one indivisible change**
and land in the same PR. Precedent for the override is already in the file at `hooks.py:372`.

**2. `Quality Review.goal` must stay required.** Core's validate calls
`frappe.get_doc("Quality Goal", self.goal)` whenever `reviews` is empty, so relaxing `reqd` turns an
ordinary save into a lookup of the empty string. A period report is generated *against* a goal;
that is the model core already has and it is the right one. `Quality Review.status` gets no Property
Setter either — `set_status()` owns it and Open/Passed/Failed is correct.

A related trap sits next to it: erpnext's daily `review()` branches on Daily, Weekly, Monthly and
Quarterly and has **no `Annual` branch**. Adding `Annual` by Property Setter yields a goal that
generates nothing, forever, with no error. Annual reviews are owned by a scheduler job of ours.

## Decisions (Nik, 2026-09-14 — do not re-litigate)

1. **The inspection engine is a new `Quality` module of our own**, modelled on the
   `sapphire_maintenance` shape. `sapphire_maintenance` itself is **not refactored** and keeps
   working exactly as it does today; two checklist engines coexist deliberately.
2. **Core's other seven Quality doctypes are reused** and extended with Custom Fields and Property
   Setters. Core's own `Quality Inspection` is not used — ADR-0012.
3. **`Project Scope of Work` is a new submittable DocType.** Submit is the lock.
4. **`Change Order` is a new first-class DocType**, not a `Project Contract` revision.
5. **A punch item is a Quality Action with a `punch_list` flag** — the documents' model, not the
   Task-category approach `docs/KPI_DASHBOARD_DESIGN.md` recommended. It inherits carry-forward and
   two-step closure for free, and ADR-0012 supersedes that recommendation explicitly.
6. **Inspector qualification is advisory and never blocks.** With 2 Senior Technicians, 0 Masters
   and 1 Project Manager, a hard gate stops the *record* of an inspection rather than stopping
   unqualified work.
7. **Milestones key on `project_type` as it is.** "Service" stays "Service"; 354 projects are not
   renamed. A new **`Products`** Project Type carries Controls Fab.
8. **The five literal roles are created** — President, Production Manager, Account Executive,
   Quality Inspector, Controller — by patch, and granted through Role Profiles.
9. **Sequencing is a thin end-to-end slice on Build first**, then widen, then the front-end chain.
10. **All four front-end chain items are in scope**: Change Orders, MSA rates on work orders,
    budget categories with a reallocation log, and a subcontractor scorecard.
11. **All four training courses are in scope.**
12. **Progress is tracked as Tasks under PRJ-00580.**
13. **B ships as two changes, and the hand-off step is declined for now.** B1 is the schema;
    B2 stamps the Project and stops. A step in the 7-step tracker was the obvious design and
    was turned down on blast radius, not on the idea: the record links to a Project so it
    cannot precede step 3, anywhere but last means renumbering steps that 707 live
    `Project Process Step` rows already carry, and `hand_off_sla_compliance` hardcodes
    `LAUNCH_STEP_NUMBER = 7` and would silently stop computing the launch deadline. Not worth
    buying before anyone has locked a real scope and learned where the step belongs. If it
    comes, it is its own change.

## Deliverables

One PR per sub-phase. Slice 1 proves the chain end-to-end on Build before anything widens.

| | Deliverable | Size |
|---|---|---|
| **A** | Module scaffold: `Quality` module, `Quality Control` workspace, the five roles, `Products` project type, `Quality Settings` + its backfill | M |
| **B1** | `Project Scope of Work` + `Scope Acceptance Criterion`, schema and `criterion_key` only, no consumers | M |
| **B2** | Locking stamps `Project.custom_scope_of_work` / `custom_scope_locked_on`. **No hand-off step** — see the decision below | S |
| **C** | Inspection authoring: Milestone, Section, Section Item, Template, Template Section; the Build master template seeded incl. commissioning | L |
| **D** | The inspection record, the Master+Addendum merge, the freeze, generation from a milestone | L |
| **E** | Core Quality extension: NCR + Quality Action fields, the Select replacement **and the class override together**; inspection Fail → NCR → Quality Action | L |
| **F** | Carry-forward and re-verification; the punch-list and client-visible flags | M |
| **G** | Critical NCR alert, per-recipient acknowledgment, and the sweep that re-drives it | M |
| **H** | The field inspection wizard; photos, signatures, and the advisory qualification check | L |
| **I** | Milestones and master templates for Design, Events, Service and Products | L |
| **J** | Quality Goal floor rule, Quality Review period report, Quality Meeting agenda, the annual job | L |
| **K** | `Change Order`, and the KPI re-point off the revision proxy | M |
| **L** | MSA rates referenced by work orders, and the expiration alert | M |
| **M** | Budget categories, budget lines, `Budget Reallocation` and the protected-category rule | L |
| **N** | Subcontractor scorecard and recovery tracking | M |

### The freeze, the carry-forward, and the alert

These three are the whole design; everything else is fields around them.

**The freeze.** Generating an instance copies the master template items *and* the addendum criteria
into result rows on the instance, and stores the template revision plus a content hash over the
merged list. The links back to the live Section rows are **provenance only and are never re-read at
render**. No `fetch_from` on any result field — a single one silently un-freezes the snapshot the
moment somebody edits the master. This is `sapphire_maintenance`'s *"a Service Plan is a stamp, not
a live link"*, and that sentence belongs in `quality/README.md`.

**The carry-forward.** Open Quality Actions at `PM Resolved` are claimed into the next generated
inspection, and **the claim is a stamp written in the same transaction** — without it, two
inspections generated the same morning both carry the same item and one of them closes it. On
submit: Pass closes; Fail reopens, escalates priority one step exactly once, and clears the claim so
the next generation re-claims it; a blank result decides nothing and clears the claim. This is what
keeps "we fixed it" from being taken on faith, and it is also the entire punch-list feature.

**The alert.** A Critical NCR notifies the PM, Production Manager and President with a per-recipient
acknowledgment timestamp. It is enqueued `after_commit` — a `doc_events` handler cannot commit,
because `Document.hook`'s `compose` disables transaction control around handlers — **and** re-driven
by a sweep, because a prod deploy `FLUSHDB`s the queue redis and destroys every pending job. An
acknowledgment is a Desk action, never a tokenised link, so it is authenticated and attributable.

Shipped in v1.453.0, with three things worth recording because they are not obvious from the design:

- **The sweep has two passes, not one.** A row that was never notified and a Critical NCR that was
  never dispatched at all are different failures — the first leaves evidence, the second leaves
  none — so neither pass depends on the other having worked. The dispatch worker writes its rows
  and stamps the document *before* sending, so a half-run worker leaves rows that openly say nobody
  was reached.
- **Nagging is capped at once per calendar day**, matching the hand-off escalation's by-date dedupe.
  An hourly re-send trains people to filter the one message that must not be filtered.
- **The no-recipient case is live today, and is deliberately not a retry loop.** The five roles were
  created in v1.446.0 and nobody holds Production Manager or President yet, so on a project with no
  `custom_project_owner` a Critical NCR resolves to zero recipients. It stamps anyway and writes a
  comment on the record saying it reached nobody. An hourly retry that can never succeed would bury
  a real problem under its own noise; who holds the roles is a thing for a person to fix, and the
  comment is where they will see it.

### The field wizard and the qualification check (sub-phase H, v1.454.0)

**The wizard enforces nothing; the endpoint does.** `api/quality_wizard.py` accepts exactly four
writable row fields -- `outcome`, `measured_value`, `notes`, `photo` -- and has no append path.
That short list is where the freeze actually lives at runtime: a client that could write
`min_value` could turn a failing measurement into a passing one from a phone, on site, with
nothing in the diff to see, and a client that could append a row could add a check nobody
contracted for. Asserted by name in `tests/test_inspection_wizard.py`, negative-tested by leaking
`min_value` in and confirming the build fails.

**A tier means nothing across job families.** `Position` carries `job_family` and an integer
`tier`, so the obvious implementation of "is this inspector senior enough" compares tiers -- and a
bare `tier >= tier` **passes** a tier-3 Designer as a qualified tier-2 Technician. The family gate
runs first. Note the failure direction: it passes, so nobody investigates it. Where seniority is
not modelled on either side it falls back to an exact name match, because an unmodelled hierarchy
is unknown rather than flat.

**Two silences that are deliberate.** A template with no requirements produces no finding, ever --
warning on every unconfigured template is the fastest way to teach people to dismiss the warning.
And an inspector with no Employee record is reported as *unknown*, not unqualified; those are
different claims and only one of them is true.

**Slice 1 is now complete**: scope locks, an inspection generates frozen, a failure raises an NCR
and an action, the fix is re-verified at the next inspection, a Critical failure pages three
people, and there is a tool a person can actually hold. Everything from I onward widens this to
other project types or builds the front-end chain; none of it changes the chain above.

### Sub-phase I did not deliver what it was scoped as, and this is why (v1.456.0)

**Scoped as:** milestones and master templates for Design, Events, Service and Products.

**Delivered:** the trigger engine. The milestones already existed — sub-phase C seeded all
seventeen, covering all five project stages. What C did not do was make any of them fire: every
milestone carries a `trigger_basis` and **nothing in the codebase read it**, which the catalog
said about itself (*"needs its own scheduling — which sub-phase I owns"*). A Build project could
reach QA and sit there, and the pre-final commissioning check would happen only if somebody
remembered.

**The design decision inside it.** Due-ness is "reached or passed", never equality.
`custom_build_status` is a Select somebody types into, not a workflow, so a project can jump
`Procurement` → `Ready for Install` in one save and an equality trigger was never true at any
moment a sweep looked. The inspection is lost with nothing to see afterwards. Under the real rule
a project that skips a stage acquires an *overdue* inspection instead. An unplaceable status
— blank, renamed, legacy — reports as `unknown` rather than "not due", because the latter makes
the sweep read clean forever on every project.

**Not delivered, deliberately: the master checklists.** Only the Build commissioning list has ever
been written down. The Design review gates, the Events setup and teardown checks, the Service pre-
and post-service checks and the Controls Fab panel checks are Sapphire's own standard of care, and
an invented checklist carries the authority of a real one right up until it fails to catch
something — the same reasoning that limited C's seeding. Those milestones now report as **due and
blocked**, which is the honest state and is visible on the Project form rather than hidden.

**This is the open question, and it is a conversation rather than a build:** whose checklists are
those four sets, and who writes them down? Until somebody does, the engine will keep correctly
reporting that an inspection is due and that nobody has said what it consists of.

## Native-first check (ADR-0002)

| Candidate | Verdict |
|---|---|
| `Quality Inspection` (Stock) | **Rejected.** `reference_type` required and limited to stock documents, `item_code` and `sample_size` required, readings are numeric parameters. No project, milestone, pass/fail or photo. ADR-0012. |
| The other seven Quality doctypes | **Used**, with Custom Fields and Property Setters — and with the two controller caveats above. |
| `Quality Procedure` | **Used as-is** as the SOP library. No changes. |
| `Contract` (core) | **Rejected** — 0 rows, unused; `Project Contract` is the live instrument. |
| `Work Order` (Manufacturing) | **Rejected.** BOM-and-warehouse shaped, and this site runs no manufacturing. A subcontractor work order is a **Purchase Order**, which already carries `project` on header and line. |
| `Supplier Scorecard` | **Rejected.** 0 rows against 1181 Suppliers, and its variables (`total_accepted_items`, `rejected_items`, `delivered_late`) compute off Purchase Receipt discipline that is measurably partial — 81 of 123 submitted POs matching "not fully received" are `Closed`. Every variable would return a clean number computed off a denominator nobody maintains. |
| `Budget` (core) | **Rejected**, for WI-057's reason: it budgets by GL account, cost centre and fiscal year, not per project. |
| `Authorization Rule` | **Rejected** for reallocation approval: the governed quantity is a delta between two child rows, not the document's `grand_total`. |

## Training

All four courses are **Required** weight and authored in the sub-phase **after** the feature they
teach ships — a course about a screen that does not exist is worthless, and so is one written
against a screen that is about to move.

| Course | Assignment rules | After |
|---|---|---|
| Running an inspection in the field | Position → the `Technician` family | H |
| NCR and Quality Action, end to end | Position → `Project Manager`, `Operations Manager`; Role → `Production Team` | F |
| Writing scope that can be inspected | Position → `Project Manager`, `Sales Representative`; Role → `Sales Team` | B |
| Subcontractor MSAs, work orders and rates | Position → `Project Manager`, `Purchasing Agent/Inventory Clerk`, `AP/AR, Purchasing Manager` | L |

Rules use **`Position`, not `Designation`** — the assignment engine's own comment gives the reason:
*"every Junior Technician" is a rule about competence, where "every Designation" is a rule about job
titles that happen to line up today.* A wrong `applies_to_value` never matches and never errors, so
every value is checked against prod before it is written.

Each course also needs a `Position Requirement` row of type `Training Course`. Assignment and
job-requirement are two different records and only the second reaches the competency roster.

**`Training Settings` ships dormant.** If `training_enabled` or `auto_assign_enabled` is still off,
four Required courses assign nobody and mail nobody. Each course's PR states which state it assumes.

## Guardrails

- **The Select-replacement window closes at first use.** E must not slip past it, and a guard patch
  fails loudly rather than creating unsaveable rows.
- **A Workspace's `name` is its `label`, and workspaces are timestamp-gated** while DocTypes are
  hash-gated. Shipping a file at ERPNext's `Quality` docname would let whichever side has the newer
  `modified` rewrite the row — **including its `module`** — silently re-homing a core workspace into
  ours. Hence `Quality Control`, and the core workspace extended in place from `after_migrate`.
  *A `modified` bump can never fix a name clash; only distinct names do.*
- **A workspace vanishes silently** for anyone holding no DocPerm on a non-child DocType in its
  module. The inspection record's JSON grants `read` to every role that should see it, and
  `quality/README.md` says so, so nobody removes the grant later.
- **No Custom DocPerm on any core Quality doctype.** Custom DocPerm *fully overrides* standard
  perms, so one row would delete core's `Desk User` and `Employee` grants unless every row were
  restated.
- **No Desk Page in this module may be named `quality` or `quality-control`** — the router resolves
  workspaces before pages.
- **"New field, existing rows" has two opposite answers and neither is the one you assume.** A
  `default` on a new field of a *normal* doctype is written into every existing row by the `ALTER`,
  so a backfill keyed on emptiness matches nothing, commits, and records itself in `tabPatch Log`
  indistinguishably from success. A `default` on a new field of a **Single** reaches no row at all.
  `Quality Settings` ships with its backfill in the same PR; the Project counters are backfilled on
  **the rule the writer applies**, never on emptiness.
- **`tabSingles` cannot be read through `db.get_value`** — three columns, no `creation`, and the
  default `ORDER BY creation` raises on every site. And a patch that raises aborts `bench migrate`,
  which on this repo *is* the deploy.
- **`hooks.py` is one dict literal.** `Project` already has `doc_events` and the cron keys already
  have members: append into the existing lists, never add a second key.
- **Never `doctype_js` a DocType this app owns** — frappe already loads the form script and the
  second copy makes a top-level `const` a SyntaxError that removes every button.
- **A `doc_events` handler cannot commit**, so anything durable is enqueued `after_commit`.
- **Creating the `Account Executive` role arms a path that is currently skipped** —
  `crm_enhancements/handoff.py:224` says so in its docstring. Verified 2026-09-14 that this is
  **latent, not live**: all three `Hand-Off Attendee Role` rows use explicit group addresses with
  `role` null. A must not add a role row, and the changelog entry must say this.
- **`_resolve_responsible` knows exactly three roles.** A step with a role it does not handle is
  created, due-dated and escalated while **notifying nobody, silently**.
- **`Project.custom_project_owner` links to Employee, not User.** Resolving the PM needs the
  `Employee.user_id` hop; skipping it addresses nobody.
- **Never bulk `doc.save()` on Project** — heavy `on_update` hooks and a wildcard `'*'` `after_save`
  → `global_triton_sync` firing per ORM save.
- **Adoption is prospective.** `seed_process_steps` fires only `before_insert`, so B's new step
  reaches only projects created after it deploys. In-flight projects are never back-filled; they
  acquire a Scope of Work when a Change Order needs one.
- **Adding a step makes "7-step" prose false in eight files**, one of which is a *seeded string* and
  needs a patch rather than an edit. Prose that reads as documentation and is false is the
  `crew_qualification_roster` lesson.
- **An acceptance check written as a SQL emptiness test passes on broken data.** PAD SPACE makes
  `col <> TRIM(col)` always false and `_ci` collation makes `col <> UPPER(col)` never fire. Use
  `LENGTH(TRIM(...))`, `BINARY`, or Python. **Note the failure direction: it passes.**
- **A nullable datetime with a `<` filter silently matches NULL rows** via the coalesce sentinel.
  New nullable dates go on the named list in `tests/test_nullable_date_filters.py`.
- **Read the framework with `git show origin/version-16:<path>`.** The sibling checkouts are v17.
- **This work item does not belong in `PLAN.md` §6.** That index is migration-scoped and stops at
  WI-070; WI-071 through WI-074 — the app-code programs — are deliberately absent and cross-link
  each other instead. What *is* stale is `PLAN.md:9`, which still reads "70 self-contained work
  items (WI-001 … WI-070)" while `work-items/` holds 75 files. A fixes that line to describe the
  split rather than restating a count that drifts on every program.

## Acceptance criteria

Each is a query or an action a person performs, not a judgement.

1. `Quality` appears in `tabModule Def` with `app_name = 'erpnext_enhancements'`, and `bench migrate`
   run twice in succession leaves the second run a no-op.
2. Editing a master template after an inspection has been generated leaves that inspection's rows
   and content hash **unchanged** — asserted bench-free over the pure merge function.
3. A Quality Action saved with an empty `resolutions` table does **not** come back `Completed`, and
   every status the controller writes is a member of the Property Setter's option list.
4. A Quality Action at `PM Resolved` appears in the next generated inspection for its project
   exactly once; a Fail verification returns it to `In Progress` with priority raised **one** step,
   and a cancel-and-resubmit does not ratchet it twice.
5. A Critical NCR produces one acknowledgment row per resolved recipient, a second save mails
   nobody again, and the acknowledgment endpoint refuses a caller who was never notified.
6. The advisory qualification path contains no `frappe.throw` — asserted with comments and
   docstrings **stripped first**, since the comment explaining the absence names the token.
7. End-to-end on a test site, performed by a person: create a Build project, lock a Scope of Work
   with three acceptance criteria, generate the pre-final inspection, confirm the criteria appear as
   addendum rows, fail a commissioning row, confirm an NCR and a Quality Action appear, mark it
   PM Resolved, generate the final walkthrough, confirm the item is carried forward, fail the
   verification, confirm it reopens with escalated priority. Record the document names on the task.
8. After deploy, confirmed against prod rather than against the version string: the Module Def
   exists, the workspace is public, the five roles exist, `Products` is a Project Type, and
   `assets.json` carries the new bundle.

## Rollback

Per sub-phase, since each is its own PR. DocTypes introduced by a sub-phase are dropped with a
`frappe.delete_doc` patch — removing a fixture from JSON only stops managing it. The Property Setter
in E is the one with a tail: reverting it after rows exist is a data migration, not a revert, which
is why the guard patch refuses to apply it once the tables are non-empty. The class override is
removed by deleting its `hooks.py` entry, and core's behaviour returns exactly as it was.

## Explicitly NOT in this work item

- **Rentals.** No asset catalog, no shipping manifest, no condition-and-wear tracking. The build
  spec itself places Rentals outside the Quality metric system. TASK-2026-01642 to TASK-2026-01645
  under TASK-2026-00872 already carry rental Asset Inspection and pre-shipping/return condition
  work; that backlog remains its home. Events execution **is** in scope, as a normal Quality
  category.
- **No refactor of `sapphire_maintenance`.** Converging the two checklist engines is a later
  decision with its own record.
- **No rename of the `Service` project type to `Maintenance`.** 354 projects, and the rename buys
  vocabulary rather than capability.
- **No change to the hand-off gate's own rules.** B adds a step; it replaces nothing.
- **No dollar-threshold escalation on Change Orders.** The President approved the base scope; the
  documents are explicit that adding a threshold here would slow every routine client request for a
  control that is already satisfied upstream.
- **[WI-057](WI-057-project-budget-discipline.md) is not turned into a code item.** It stays Type DATA. `estimated_costing` remains the
  single budget denominator WI-058 divides by; M adds an *allocation* of that number, and a project
  that only ever receives WI-057's treatment has one `Unallocated` category holding the whole of it.
  **M is blocked by WI-057's backfill** — the sum invariant has nothing to check against until the
  denominator exists.
- **Acceptance criteria are authored, not extracted.** Each scope child table is one `Long Text`
  field per row, so there is no structured source to pull from. The *carrying* into the inspection
  template is mechanical; the authoring is a human step at the hand-off meeting, seeded from a
  per-project-type starter set so the PM edits rather than starts from blank. This is what course 3
  exists to teach, and it is the largest gap between what the documents imply and what any build
  can deliver.
