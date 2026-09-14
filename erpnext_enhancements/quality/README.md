# Quality

Project inspections, non-conformance and corrective action — the module that makes a quality
failure traceable to a contracted, measurable standard.

Programme: [WI-075](../../work-items/WI-075-quality-and-inspections.md).
Decision record: [ADR-0012](../../decisions/adr/0012-project-inspections-do-not-use-quality-inspection.md).

**Status: sub-phases A through F — the loop closes.** Scope locks, an inspection generates
frozen from the template plus that project's contracted criteria, a failed check raises a
Non-Conformance and a corrective action, and an unverified fix is carried into the next
inspection and re-checked before it may close. That last step is the one the whole design rests
on: a self-reported fix and a re-inspected fix are different levels of confidence.

Still to come: the Critical-NCR alert with per-recipient acknowledgement (G) and the field
wizard (H). Generation is still a deliberate act — `trigger_basis` is read by nothing — and
`quality_enabled` is **off**.

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
| `catalog.py` | The milestone catalog and the Commissioning checks, as data. Frappe-free so CI can read it and sub-phase D can reuse it |
| `lifecycle.py` | The NCR and Quality Action state machines, defined once so the Property Setter fixture and the code that writes a status cannot drift |
| `overrides/quality_action.py` | Replaces core's one-line `validate`. **Inseparable from the status Property Setter** |
| `routing.py` | A failed check becomes an NCR and a Quality Action; and on submit, carried fixes get their verdict |
| `carry_forward.py` | Claiming an unverified fix into the next inspection, and what a re-verification does to it. **The punch list is this, plus a flag** |
| `merge.py` | **The centre of the module.** Merging a master template with a project's contracted criteria, the content hash that freezes the result, and what counts as a failure. Frappe-free, so the freeze is asserted on every push |
| `doctype/project_quality_inspection/` + `inspection_result/` | The generated inspection. Its rows are copies, never links |
| `stable_keys.py` | Row identity — the `*_key` that criteria, checks and (in D) results all join on. Minted once, never regenerated |
| `doctype/inspection_milestone/` | Where in a project's life an inspection is due |
| `doctype/inspection_section/` + `inspection_section_item/` | A reusable block of checks. Top-level rather than a table on the template, because **Frappe has no grandchild tables** |
| `doctype/project_inspection_template/` + `inspection_template_section/` | The Master: the company's standard of care for one milestone |
| `scope_criteria.py` | Acceptance-criterion identity — the `criterion_key` every downstream record joins on. Imports no `frappe`, so it is testable without a bench; consumed by `Project Scope of Work` over in `project_enhancements` |

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

## The freeze, and why it is a test rather than a comment

An inspection's rows are **copied** from the template at generation and never re-read from it.
A later edit to a master template — or to a reusable section inside one — cannot reach an
inspection that already happened.

There is **no `fetch_from` on a single result field**, and that absence is load-bearing. One
would silently un-freeze the snapshot the moment somebody edited the source, and it would look
entirely ordinary in a diff.

The reason this is enforced by `tests/test_inspection_merge.py` rather than by a note is that
the failure is invisible. An implementation that re-reads the master at render populates the
form correctly and shows the right checks; nothing is wrong until somebody tightens a tolerance
and last year's passed inspections quietly become failures — or, worse, its failures quietly
become passes. Nothing logs it, and nobody notices until an insurer or a subcontractor asks what
was actually checked. So the suite mutates the source after generation and demands the rows and
their hash are untouched.

`snapshot_hash` is the authoritative provenance, and `Project Inspection Template.revision`
deliberately is not: the revision cannot see a change made inside a referenced section, while the
hash is taken over the rows that actually landed.

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

## Extending core's Quality Action: why two changes are one change

`Quality Action`'s status Property Setter and `override_doctype_class["Quality Action"]` ship
together and must never be separated. ERPNext's entire controller for that doctype is one line:

    def validate(self):
        self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"

`any([])` is `False`, so an action with **no resolution rows** saves as `Completed` — and an
action with no resolution rows is exactly what a punch-list item is. Every punch item would be
born closed, which is the opposite of the two-step closure this module exists to provide.

Worse, once the Property Setter replaces `Open / Completed` with the five-state lifecycle, that
same line writes a literal the field no longer offers. `_validate_selects` raises, and **every**
save of the doctype fails — including saves that have nothing to do with this app.

So the decision lives in [`lifecycle.py`](lifecycle.py), which imports no `frappe` and is
asserted on every push, and the override is a thin class that calls it. `derive_action_status`
differs from core in three deliberate ways: a terminal status is never moved by a child-table
edit; an empty resolutions table means *no information*, not *done*; and nothing outside the
option list is ever written, including core's own `"Completed"`, which is mapped to `Closed` so
a row arriving with it becomes saveable rather than raising forever.

`Non Conformance` needed no override — core ships it with no controller logic at all.

## The two-step closure, and why the claim is a stamp

An action reaching `PM Resolved` is **not** closed. The companion paper gives the reason and the
constraint together: *PM sign-off should not be blocked from moving a project forward — if a fix
is made, the team should keep working. But a self-reported fix and a re-inspected fix are
different levels of confidence, and only the second one should be allowed to permanently close
the record.*

So the action is claimed by the next inspection generated for its project, appears there as a
row, and closes only on a Pass. A Fail reopens it to `In Progress` (not `Open` — somebody has
already worked on it), escalates one step, counts the reopen, and releases the claim so it is
carried again.

**The claim is written in the same transaction as the generation that carried it.** Without
that, two inspections generated the same morning both carry the same item, both answer it, and
the second one submitted silently overwrites the first one's verdict. It is also why claiming is
synchronous rather than enqueued: a prod deploy `FLUSHDB`s the queue redis and destroys every
pending job.

The escalation happens **once** per verification. A Fail releases the claim, and the verifier
only acts on an action this inspection still holds the claim for, so cancelling and re-submitting
an inspection is a no-op rather than a second ratchet.

An open punch-list item is structurally the same thing — raised against a standard, fixed by
somebody, not actually done until it has been looked at again — so it is a Quality Action with
`custom_punch_list` ticked and gets all of this for free.

## One thing this module cost us on its first two deploys

`Project Scope of Work` was created by model sync and then **force-deleted in the same
migrate** — twice, on v1.452.1 and again on v1.452.2 — because its controller class was named
`ProjectScopeOfWork` and Frappe was looking for `ProjectScopeofWork`.

Frappe resolves a controller with `doctype.replace(" ", "").replace("-", "")`. It strips
spaces; it does not title-case. "of" was lower case in the DocType name, so it stays lower case
in the class name. `get_controller` raises `ImportError` when the class is missing, and
`remove_orphan_doctypes()` passes anything that raises to `frappe.delete_doc(..., force=True)`.

Neither deploy failed. Nothing reached the Error Log. The table survived both times — MariaDB
DDL auto-commits — so production held a 26-column `tabProject Scope of Work` with no DocType
row, and `Project.custom_scope_of_work` pointing at a Link target that did not exist.

The first fix attempt was wrong: the cross-module import in that controller was blamed, made
lazy, and the DocType was deleted again on the very next deploy. The check that "proved" the
import was fine had asked for the class name *we* chose rather than the one Frappe derives —
which is the whole lesson, and is what `tests/test_doctype_controller_names.py` now asserts for
every DocType in the app.

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
