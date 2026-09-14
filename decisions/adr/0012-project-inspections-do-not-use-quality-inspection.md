# 0012. Build project inspections on our own record, not ERPNext's Quality Inspection

- **Status:** Accepted
- **Date:** 2026-09-14

## Context

A five-document design package (a team deck, a companion paper, a full build spec, a
scope-to-inspection process document, and a screenshot of the live form) specifies a Quality
module for Sapphire Fountains. Its central record is a checklist inspection run at a project
milestone — pre-pour, in-process fabrication, systems startup, final walkthrough — whose
criteria are drawn from that project's own contracted, measurable acceptance standards.

ERPNext ships a Quality module, and [ADR-0002](0002-native-first.md) makes native-first a
standing rule: a custom mechanism where a native one suffices is a defect, not a preference.
So the null hypothesis was that all eight menu items in the spec are core doctypes we simply
configure. Seven of them are. The eighth is not, and the reason is structural rather than
cosmetic.

**Verified against prod on 2026-09-14 via the assistant MCP.** `Quality Inspection` lives in
the **Stock** module, not `Quality Management`, and it is an incoming-materials record:

- `reference_type` is **required**, and its options are Purchase Receipt, Purchase Invoice,
  Subcontracting Receipt, Delivery Note, Sales Invoice, Stock Entry, Job Card. `Project` is
  not among them, and `reference_name` is a Dynamic Link on it, also required.
- `item_code` is **required**, and `QualityInspection` reads it in validate to pull
  specification details.
- `sample_size` is **required**.
- `readings` are `Quality Inspection Reading` rows — numeric parameters with min/max — not
  pass/fail/NA checks with a verification method, a photo, or a note.

There is no project, no milestone, no photo and no signature. Making it fit would mean
property-setting four required fields optional, adding `Project` to a core Select, and adding
a second readings shape — while the core controller continues to assume the item and the stock
reference on every upgrade. That is not configuration; it is maintaining a fork of a core
doctype in fixtures.

Two facts made the decision cheap rather than fraught. First, **every core Quality table is
empty** — Quality Inspection, Quality Inspection Template, Quality Goal, Quality Review,
Quality Action, Non Conformance, Quality Feedback, Quality Meeting and Quality Procedure all
have zero rows, so nothing is being migrated away from. Second, **the right engine already
exists in this app**: `sapphire_maintenance` runs Template → Template Section → Section Item on
the authoring side and Maintenance Record → Maintenance Result (Pass/Fail/Replace, notes,
photo) on the execution side, with mandatory-row counters, a signature pad and a touch-first
wizard, across 48 live templates.

## Decision

Project milestone inspections use a new `Project Quality Inspection` DocType in a new
`Quality` module, with its own template/section/item authoring layer modelled on the
`sapphire_maintenance` shape. Core's `Quality Inspection` and `Quality Inspection Template` are
**not used and not configured**.

The other seven core Quality DocTypes — Quality Goal, Quality Review, Quality Action, Non
Conformance, Quality Feedback, Quality Meeting and Quality Procedure — **are** used, extended
with Custom Fields and Property Setters. Their Select options are replaced now, while the
tables are empty, because an off-options Select value makes a row permanently unsaveable and
no `ignore_*` flag bypasses `_validate_selects`.

`sapphire_maintenance` is not refactored. Two checklist engines coexist deliberately;
converging them is a later decision with its own record.

This record also **supersedes the punch-list recommendation in
[`docs/KPI_DASHBOARD_DESIGN.md`](../../docs/KPI_DASHBOARD_DESIGN.md)** (§Production, KPI #11),
which proposed flagging a stock Task with `custom_task_category='Punch/Rework'` "to avoid a new
child table". A punch item is structurally a Quality Action: it is raised against a standard,
resolved by the PM, and must be re-verified before it may close. Modelling it as a Quality
Action with a `punch_list` flag gets the auto-carry-forward and two-step closure for free;
modelling it as a Task means bolting re-verification onto Task separately. The KPI doc's
analysis predates the observation that core's Quality Action exists and is empty.

## Reusing the seven is not free either

Two of the seven carry controller logic that the extension has to work with rather than
around. Both verified against `erpnext origin/version-16` — not the sibling `develop` checkout,
which is `17.0.0-dev` while prod runs 16.x.

**`Quality Action`'s entire controller is one line**, and it is hostile to both halves of this
design:

```python
def validate(self):
    self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"
```

An action with no `resolutions` rows — which is precisely what a punch-list item is — evaluates
`any([])` as `False` and saves as **Completed**. Every punch item would be born closed. And
once the Property Setter replaces the status options with the five-state lifecycle, that same
line writes the literal `"Completed"`, which is no longer a valid option, so `_validate_selects`
raises on **every** save of the doctype.

So `override_doctype_class["Quality Action"]` is not an optimisation, it is a precondition: the
Property Setter and the override are one indivisible change. The override's `validate` never
writes a value outside the option list, treats an empty resolutions table as *no information*
rather than *done*, and leaves `Verified at Next Inspection` and `Closed` to be written only by
the inspection verification path.

**`Quality Review.goal` stays required.** Core's validate calls
`frappe.get_doc("Quality Goal", self.goal)` whenever `reviews` is empty, so relaxing `reqd`
turns an ordinary save into a lookup of the empty string. A period report is generated *against*
a goal; that is the model core already has, and it is the right one. For the same reason
`Quality Review.status` gets no Property Setter — `set_status()` owns it.

A related trap sits next to it: erpnext's daily `review()` branches on Daily, Weekly, Monthly
and Quarterly and has **no `Annual` branch**. Adding `Annual` to the frequency options by
Property Setter yields a goal that silently generates nothing, forever, with no error. Annual
reviews are therefore owned by a scheduler job of ours, not by a Select option.

## Consequences

**What this buys.** The inspection record can carry what the design actually requires: a
project, a milestone, pass/fail/NA with a verification method, per-item photo requirements, an
inspector signature and a client-rep signature, and a frozen snapshot of the merged Master +
Addendum checklist that later template edits cannot reach. Seven of the eight menu items stay
native, so the Quality workspace, the permission model and the tree of procedures are all
framework behaviour we did not write.

**What it costs.** Two checklist engines now exist in this app — `sapphire_maintenance` for
service visits and `quality` for project inspections — and a reader will reasonably ask why.
The answer is this record. Anyone adding a third should instead converge the two.

**Invariants a future contributor must preserve.** A generated inspection instance is a
snapshot: its rows are copied, and the links back to the live Section rows are provenance only
and must never be re-read at render. The Select options on the seven extended core doctypes are
load-bearing state machines — changing one after records exist is a data migration, not an
edit. And `frappe.get_doc("Quality Inspection", …)` in this app's code is always a mistake;
the project record is `Project Quality Inspection`.

**What would have to change before revisiting.** If upstream ERPNext ever makes
`Quality Inspection.reference_type` extensible and its item/sample fields optional — or splits
a non-stock inspection out of the Stock module — the case for this record weakens and a
superseding ADR should re-run the comparison.
