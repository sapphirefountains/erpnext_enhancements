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
