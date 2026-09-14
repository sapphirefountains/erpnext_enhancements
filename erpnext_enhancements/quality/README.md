# Quality

Project inspections, non-conformance and corrective action — the module that makes a quality
failure traceable to a contracted, measurable standard.

Programme: [WI-075](../../work-items/WI-075-quality-and-inspections.md).
Decision record: [ADR-0012](../../decisions/adr/0012-project-inspections-do-not-use-quality-inspection.md).

**Status: sub-phase A only.** This module currently ships its scaffold — the module itself, the
`Quality Control` workspace, `Quality Settings`, and the roles and project type the rest of the
programme needs. The inspection engine lands in C and D. `quality_enabled` is **off**, and
nothing here acts while it is.

## What this module is for

Scope, contract terms, pricing and inspection standards have historically lived in four separate
places, and when they disagree there is no single record either side can point to. The design
principle, from the programme's source documents: **if a criterion cannot be inspected it should
not be in the contract, and if an inspection item does not trace back to something contracted,
question why it exists.**

## File map

| Path | What it is |
|---|---|
| `module_def/quality.json` | The `Module Def`. Needed as a file so the module has an identity of its own rather than appearing only as a side effect of a DocType import |
| `workspace/quality_control/` | The `Quality Control` workspace — **not** `Quality`; see below |
| `doctype/quality_settings/` | The Single holding every master switch. Dormant by default |

Registered in [`../modules.txt`](../modules.txt), tiled from
[`../setup/desktop_icon_map.py`](../setup/desktop_icon_map.py), and given a sidebar by
[`../workspace_sidebar/quality_control.json`](../workspace_sidebar/quality_control.json).

## Two names that must not change, and why

### The workspace is `Quality Control`, never `Quality`

ERPNext's own `Quality Management` module ships a public workspace whose **name and label are
both `Quality`**, along with the `Desktop Icon` that goes with it.

A Workspace's `name` *is* its `label`, so a file at `workspace/quality/quality.json` would sit at
ERPNext's exact docname. Workspaces are **timestamp-gated** by `import_file_by_path` — unlike
DocTypes, which are hash-gated — so whichever file carries the newer `modified` rewrites that row
on every migrate, **including its `module`**. The result is not a merge conflict; it is ERPNext's
Quality workspace silently re-homed into this module, changing which DocPerms gate it. That is
the Plaid Settings shape, and this repo has already written the verdict down: *a `modified` bump
can never fix a name clash; only distinct names do.*

The same trap sits one level along. `setup/desktop_icons.py` keys `Desktop Icon` rows by
**workspace label** and derives their roles from the same-named `Workspace`, so a `TILES` key of
`"Quality"` would stamp this module's artwork onto ERPNext's tile and inherit ERPNext's roles.
The key is `"Quality Control"` for that reason.

### No Desk Page here may be named `quality` or `quality-control`

`frappe.router` resolves the first path segment against workspaces *before* pages, so a Page
sharing a workspace's slug never renders — and it fails per-user, because the workspace list is
permission-filtered, which makes it look like a permissions bug. `tests/test_workspaces.py`
fails the build on it. The inspection wizard, when it lands in H, is `inspection-wizard`.

## The module gate — why `Quality Settings` grants read so widely

`Workspace.__init__` raises `PermissionError` when its module is not in the caller's
`allowed_modules`, and `get_workspace_sidebar_items` **swallows that exception**. So a workspace
does not fail loudly for someone who cannot see its module; it simply is not there.

`allowed_modules` is built only from DocPerms on **non-child** DocTypes. In sub-phase A this
module has exactly one non-child DocType, so `Quality Settings` is the only thing standing
between the `Quality Control` workspace and vanishing for everybody but System Manager.

That is why its `permissions` block grants `read` to the `* Team` roles and `Project Manager` as
well as write to System Manager and Quality Manager. **Do not narrow it without moving the grant
to another non-child DocType in this module first.** `Position` does the same job for
`hr_enhancements`, for the same reason.

## Core's Quality DocTypes: seven reused, one rejected

The `Quality Control` workspace links seven DocTypes this module does not own —
Non Conformance, Quality Action, Quality Goal, Quality Review, Quality Meeting, Quality Feedback
and Quality Procedure. They are erpnext's, they are kept, and later sub-phases extend them with
Custom Fields and Property Setters.

Core's own **`Quality Inspection` is not used and must not be**. It lives in the Stock module and
is an incoming-materials record: `reference_type` is required and limited to stock documents,
`item_code` and `sample_size` are required, and its readings are numeric parameters. It has no
project, no milestone, no pass/fail and no photo. `frappe.get_doc("Quality Inspection", …)`
anywhere in this app is a mistake. ADR-0012 has the full reasoning.

Two of the seven carry controller logic that later sub-phases have to work *with* rather than
around, and both are recorded here so nobody rediscovers them the hard way:

- **`Quality Action.validate` is one line** that writes `status = "Completed"` whenever the
  `resolutions` table is empty — which is exactly what a punch-list item is. It also writes the
  literal `"Completed"`, so the moment a Property Setter replaces that field's options, *every*
  save of the doctype raises. The Property Setter and an `override_doctype_class` are therefore
  **one indivisible change**.
- **`Quality Review.goal` must stay required.** Core's validate calls
  `frappe.get_doc("Quality Goal", self.goal)` whenever `reviews` is empty, so relaxing `reqd`
  turns an ordinary save into a lookup of the empty string.

## Settings

Every dial in `Quality Settings` ships off or permissive, and `quality_enabled` gates the lot.

A `default` on a new field of a Single **never reaches the row that already exists**, so the
settings ship with [`backfill_quality_settings_defaults`](../patches/backfill_quality_settings_defaults.py)
in the same release, and `QualitySettings.validate` repairs a missing dial in place as a second
defence. Both fill only where `tabSingles` has **no row** — never over a stored falsy value,
because an unticked box and a deliberate `0` are not the same fact.

Read `tabSingles` with `get_single_value`, or with `order_by=None`. It has three columns and no
`creation`, so `frappe.db.get_value("Singles", …)` compiles to a query ending `ORDER BY creation`
and raises on every site, every time.

## Roles

[`seed_quality_roles`](../patches/seed_quality_roles.py) creates President, Production Manager,
Account Executive, Quality Inspector and Controller — by patch rather than `fixtures/role.json`,
because fixture files import in alphabetical filename order and `custom_docperm.json` lands
before `role.json`.

**Grant them through a Role Profile, never directly.** `populate_role_profile_roles` rebuilds a
profiled user's roles from the union of their profiles on every User save, so a direct grant is
wiped the next time anybody edits that user.

One live consequence is recorded in that patch's docstring and repeated here because it is easy
to lose: creating `Account Executive` **arms a code path that currently skips**.
`crm_enhancements/handoff.py:224` documents that the role is a Select value rather than a real
Role on this site, and `_role_holder_emails` skips a Role that does not exist. Verified on
production 2026-09-14 that this is **latent, not live** — all three configured
`Hand-Off Attendee Role` rows use explicit group addresses with `role` null — but it becomes live
the first time somebody sets `role` on one of those rows.
