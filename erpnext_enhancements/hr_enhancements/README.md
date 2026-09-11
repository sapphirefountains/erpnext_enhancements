# HR Enhancements

The people side of the app: the company's **position ladder**, and one desk area an
employee can open to find their own training record without knowing where anything
lives. Built because ERPNext core ships `Employee`, `Designation` and `Department`
and nothing that joins them up, and because **`hrms` is not installed on this site**
(verified: no `Employee Grade`, no onboarding, no appraisal, no leave — only the five
`Setup`-module doctypes).

## Why the module is called this and not `HR`

`hrms` ships a module literally called **`HR`**. Claiming that name would be the
Plaid Settings collision again (v1.361.0), where our Single overwrote ERPNext's
native one through a newer `modified` and broke both. So the module is
`HR Enhancements`, matching `CRM Enhancements` / `Project Enhancements` /
`Inventory Enhancements` in [`../modules.txt`](../modules.txt), and the **label users
see is `HR`** — set on the workspace, not on the module.

## The navigation problem this exists to solve

Two mechanisms, and only one of them is the one people reach for.

**A workspace silently disappears if you hold no DocPerm in its module.**
`Workspace.__init__` raises `PermissionError` when `doc.module not in
allowed_modules` (frappe `origin/version-16:frappe/desk/desktop.py:38-44`), and
`get_workspace_sidebar_items` swallows it at `:421`. `allowed_modules` is built
**only** from DocPerms on non-child doctypes (`frappe/utils/user.py`) — Singles
count, child tables do not, and the `Workspace Manager` role bypasses the whole gate,
which is why this is invisible to whoever is testing.

That is not hypothetical here. Before v1.386.0, `HR Manager` held **no DocPerm on any
of the 32 Training doctypes**, so the one person on this site actually running HR saw
no Training workspace at all and clicking the Training desk tile put her in a
permission error. `roles: []` on a Workspace JSON does not mean "everyone" — it means
"no extra restriction *on top of* the module gate".

**`Position` is what keeps this module reachable.** It grants `read` to `Employee`,
which every staff account holds, so `HR Enhancements` lands in everyone's
`allow_modules` and the workspace resolves. Removing that DocPerm would make the
whole area vanish for all non-managers, with no error anywhere. If `Position` is ever
retired, something else in this module has to carry an equivalent grant first.

## The ladder

`Position` is a nested-set tree. Groups are **job families** (Technician, Designer);
the leaves under them are the **rungs**, and `tier` is what decides authority —
higher outranks lower, and **only inside the same family**, so a Senior Designer has
no standing over a Junior Technician however the integers compare. Never a same-tier
peer, or two Junior Technicians sign each other's basin course and the gate means
nothing.

`outranks()` and `positions_outranked_by()` in
[`doctype/position/position.py`](doctype/position/position.py) are the single
expression of that rule; every caller reads them rather than re-deriving it.

Three things are deliberately *not* the ladder:

- **`Designation`** stays the HR job title. It is core, flat, two fields, and already
  consumed by the training auto-assignment engine and by payroll reporting.
  **Changing somebody's Designation changes no authority** — say that out loud when
  anyone asks.
- **A `tier` field on `Employee`** would put rank on the person rather than the
  position, which is the opposite of the rule, and would give the org-chart half of
  the ask nothing.
- **`Employee.reports_to`** — and this one is worth understanding, because it is the
  reason the whole ladder exists. On this site the reporting tree says the *opposite*
  of the ladder: Jesse Griffin is the one Senior Technician and has **zero direct
  reports**, while all four Junior Technicians report to the Project Manager, as does
  he. Routing sign-off through `reports_to` cannot reach the one person who has
  actually watched them work. Both trees are real and they answer different
  questions — `reports_to` is who runs your week, `Position` is who is qualified to
  say you can do the job. `Employee` is already a full nested set on `reports_to` in
  ERPNext v16 core, so `/app/employee/view/tree` works today and is linked from the
  workspace beside the position tree.

## Credentials

`Employee Credential` holds a qualification somebody **else** issued — OSHA, forklift,
CDL and its DOT medical card, first aid, respirator fit test, electrical. Every one of
those was structurally unrecordable before v1.386.0: `Training Certificate.completion`
is `reqd: 1`, so the app could only ever hold a certificate that originated in one of
its own courses, and a forklift ticket lived in a filing cabinet.

**Not a variant of `Training Certificate`, deliberately.** A completion is evidence
*this system* produced and can re-derive — version, content hash, score, all
recomputable from the attempt behind it. A credential is evidence somebody outside
produced, which this app can only *hold*. Merging them would mean either weakening the
completion's guarantees or inventing an attempt for a forklift ticket. Two records, one
shared idea of expiry, and both feed the same profile, the same horizon and the same
dispatch advisory. `tests/test_hr_credentials.py` asserts that `completion` is still
required, so if it ever stops being, the question gets re-opened rather than forgotten.

**Status is derived and never typed** — `Valid` / `Expiring` / `Expired` / `Revoked`,
recomputed on every save and re-swept nightly at 05:20. It is arithmetic on a date, so
a typed value is wrong the day after somebody types it, and the whole worth of the
record is that it is true on the morning of the job. Revocation beats the calendar.
`Expiring` is `Valid` inside the ninety-day horizon and **still counts as qualified** —
a horizon that reads as a refusal does the opposite of its job.

**A forward view, not an alarm.** A Monday digest names everything lapsing inside the
horizon, one email to the holder and a roll-up to their supervisor (the holder books
the course; the supervisor stops scheduling them past the date). Nothing in this app
warned about anything *before* the fact until now: `certificates.expire_and_recertify`
reacts after a training certificate lapses, and `fixtures/notification.json` holds
nineteen alerts across a dozen doctypes and **zero** HR or training ones.

**The `Employee` DocPerm is load-bearing and so is its scoping hook.** The grant is what
puts a technician's own card on their own profile and what keeps this module inside
`allow_modules`; without `permissions.credential_query_conditions` it would also put
everybody's licence number and medical card in front of everybody. They ship in the same
release — DocPerms with no scoping hook is the one combination that leaks, and the
Training module has just finished paying for that lesson three times.

## Skills Matrix

People down the side, qualifications across the top, four words in each cell —
`Current`, `Expiring`, `Lapsed`, `Never`. It answers **"who can I send?"**, which
nothing here could before: `Training Completion Matrix` reports what has already
happened one course at a time, and `training/compliance.py` warns about one individual
at the moment of dispatch.

One grid over **both** sources, because a technician is qualified by a mixture of
internal courses and external credentials and a manager scheduling a basin drain does
not care which system a ticket came out of. Column keys are namespaced `course:` /
`cred:` — a Credential Type and a Training Course may share a title, and a collision
would merge two columns into one, which reads as everybody suddenly being qualified.

Readable by Projects Manager and Maintenance Manager as well as HR: whoever schedules
the work needs it more than HR does. Its `ref_doctype` is `Employee`, which its readers
can actually read — `Training Completion Matrix` lists HR Manager while Training
Completion granted HR Manager nothing, so it errored for exactly its intended reader.

## Time off

**Request → approve → calendar.** No balances, no accrual, no carryover — a decision
rather than an omission. Balances are where the real complexity and every payroll
argument live, and they are only worth carrying if PTO is being tracked as a
liability; it is not, here.

`total_days` counts **calendar** days, because there is no holiday calendar on this
site to subtract from them, and a number quietly pretending to be working days would
be wrong by an unpredictable amount and wrong in the direction that shortens somebody's
leave.

**Approval is the reporting line, not the ladder — the one place in WI-072 where those
deliberately come apart.** Time off is "who plans your week", which is exactly what
`Employee.reports_to` means and exactly what a Position tier does not: a Senior
Technician outranks a Junior on whether they can drain a basin and has no standing at
all over their Thursday. The rest of this module routes authority through the ladder
*because* the reporting tree could not express competence; borrowing it back here would
be granting it something nobody gave it. `tests/test_hr_timeoff_onboarding.py` asserts
that through the AST rather than by reading the source, since the prose explaining the
decision necessarily names the thing being excluded.

The overlap check **warns and never refuses**: two people off the same week is a
scheduling conversation, not this record's business to prevent, and blocking would mean
somebody who genuinely needs the day simply not asking. `who_is_out` returns names and
dates and deliberately **not** the type or the reason — a sick day is not something to
publish to the crew. The calendar colours only `Approved` green, because a Requested day
is not a day off yet.

Not submittable: "I put in for Thursday and then didn't" is a change of mind, not an
amendment of a document. `Canceled`, one l, house style.

## Onboarding

A checklist raised automatically when an Employee is added, joining the existing
`after_insert` hook rather than adding a second one whose ordering nobody declared. It
is contractually incapable of raising — an Employee record failing to save because a
checklist could not be built would be the tail wagging the dog.

**Owners are plain words, not Links.** Half of a first week is done by whoever is free
that morning, and a required assignee is exactly how a checklist stops getting filled
in. The record's job is showing what has not happened yet, not knowing whose fault it
is. Progress is derived on every save, because a stored percentage goes stale the moment
somebody ticks a box; a tick is stamped once, and un-ticking clears it, or the row would
claim it was done by somebody on a date while showing as outstanding.

## Files

- `doctype/position/` — the tree, the tier rule, and the two authority helpers.
  `position_tree.js` renders `/app/position/view/tree`; Frappe picks up a
  `<doctype>_tree.js` by convention, with no hooks entry.
- `workspace/hr/hr.json` — the desk landing page. Shortcuts first (open my training,
  company tree, who reports to whom, sign-offs to record, overdue training), record
  cards below.
- `../workspace_sidebar/hr.json` — the sidebar. **Row order is a product decision**:
  the desk tile routes to the sidebar's *first Link item*, so the first row is the
  workspace itself and not a doctype list.
- `../setup/desktop_icon_map.py` — the home-grid tile (`hr`, `id-card`, teal `PEOPLE`
  alongside Workforce and Training). Artwork is generated by
  `scripts/build_desktop_icons.py` and committed; `tests/test_desktop_icons.py` keeps
  the two in step.
- `../patches/seed_positions_from_designations.py` — seeds the ladder and places
  every Employee on it. Only **one** ladder is asserted (Technician:
  Junior/Senior/Master); every other Designation becomes a single-rung family that
  outranks nobody, because "Electrical Designer" is a specialty and giving it a rank
  would silently assert authority over somebody nobody has granted.

Not a fixture, deliberately: `Position` is a nested set, fixture import is
delete-and-reinsert, and re-inserting the rows of a tree in file order rebuilds
`lft`/`rgt` from whatever `parent_position` happens to resolve at the time.

## Employee fields

`custom_position` (Link) and `custom_position_tier` (Int, fetched, read-only) sit
under a `Position & Competency` section after `designation`, and ship in
[`../fixtures/custom_field.json`](../fixtures/README.md). The fetched tier is display
only — every authority check reads the `Position` itself, because a fetched value is a
copy and a copy is a thing that can be stale.

## Plan of record

[`work-items/WI-072`](../../work-items/WI-072-hr-module-and-training-redesign.md).
Tracked on ERPNext prod under **PRJ-00616**, `TASK-2026-01938`.
