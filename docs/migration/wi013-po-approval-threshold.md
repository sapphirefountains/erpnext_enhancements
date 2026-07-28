# WI-013 — Purchase Order approval threshold (CEO escalation)

Large purchases need CEO sign-off. A Purchase Order whose **grand total exceeds the threshold** can only be **submitted** by a user holding the **`PO Approver`** role (the CEO) — everyone else saves the draft and hands it to the approver. No custom approval doctype: a `before_submit` gate implements the native "PM saves, CEO submits" flow.

## Where it's configured

**ERPNext Enhancements Settings → Purchasing Controls → PO Approval Threshold (Amount)**
- **Currency**, **default $500**. Change it any time — no deploy needed; the hook reads the live value.
- **Set to 0 to disable** the check entirely.
- Existing installs are defaulted to $500 by the `default_po_approval_threshold` patch (a field default only applies at record creation, and the Settings Single already exists).

## Who can submit over the threshold

Holders of **`PO Approver`** — **James Harris (CEO), Nikolas Bradshaw (sys-admin) and Lisa Symanski** (seeded in WI-010, assigned in WI-011; Lisa added in WI-066). `Administrator` always bypasses. Everyone else gets an "Approval Required" error on submit and must hand the draft to an approver.

> **Why three.** With two approvers and WI-066's segregation gate — which `PO Approver` does **not** bypass — a PO built from one approver's own Material Request could only be submitted by the other, and a PO consolidating requests from both by nobody except `Administrator`. 37 POs a year worth $221,685 sat behind two people's availability. See [WI-066](wi066-po-creator-and-sod.md).

> **This gate is not the only one on submit.** [WI-066](wi066-po-creator-and-sod.md) registers a segregation-of-duties check **ahead** of this one: whoever raised the linked Material Request cannot submit the PO that fills it, and **no role clears that** — so "only a `PO Approver` can submit it" is true of the *threshold* rule only. A `PO Approver` who is himself the requester is still blocked.

## Design & extension point (per-project / %)

Enforcement lives in `erpnext_enhancements/po_approval.py`; the hook is `doc_events["Purchase Order"]["before_submit"]`. The threshold is resolved in one place — **`get_effective_threshold(doc)`** — with this order:
1. **Per-project override (WI-058, not built yet):** a fixed amount, or a percentage resolved against the project budget. `_project_override(doc)` returns `None` today, so it's a no-op.
2. **Global amount** from Enhancements Settings.

So when per-project thresholds land, only `_project_override` changes — the enforcement, the role gate, and the Settings default stay put. The **percentage** rule is deferred because every project's `estimated_costing` is 0/NULL today (needs budget discipline, WI-057/WI-058).

## Verification

- Bench-free unit tests (`erpnext_enhancements/tests/test_po_approval.py`, wired into CI) cover: global-threshold resolution, 0-disables, boundary (at-threshold passes), over-threshold blocks a non-approver, over-threshold allows a PO Approver, and disabled-allows-large. ✅
- **On TEST (manual):** as a PM (`Purchase User`, no `PO Approver`), submitting a PO with grand total **> threshold** raises the "Approval Required" error; the CEO (`PO Approver`) submits the same PO successfully; a PO **≤ threshold** submits for the PM.

## Notes

- Deliberately a `before_submit` gate (not `validate`): PMs can still **save** an over-threshold draft — they just can't submit it.
- Authorization Rule doctype is left untouched (this replaces the fixed-value native-rule approach with a configurable, per-project-ready one, per the operator's requirement).
- Related: WI-012 (MR→PO role split), WI-057/WI-058 (project budgets → percentage escalation), WI-044 (PI/PE approval).
