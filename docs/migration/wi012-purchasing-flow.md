# WI-012 — Purchasing flow: Material Request (team lead) → Purchase Order (PM)

A permission split, not custom code: field team leads request materials; PMs commit company money by converting the request to a PO.

## Role split (native Role Permissions)

| Role (who) | Material Request | Purchase Order |
|---|---|---|
| **`Stock User`** — field team leads (via the Production Team profile) | create / write / submit | **read-only** (cannot create) |
| **`Purchase User`** / **`Purchase Manager`** — PMs / purchasing | create / write / submit | **create / write / submit** |
| `Stock Manager` | create / write / submit | (no access) |
| `External Contractor` — subcontractors | create / write / submit | read-only |

So a team lead gets a **PermissionError** creating a PO; a PM can `Create → Purchase Order` from the MR. PO **submit** above a dollar threshold is further governed by WI-013's Authorization Rule (separate item).

**Team-lead role decision:** reuse native **`Stock User`** (already held by field staff via the Production Team profile) rather than seeding a dedicated `Field Team Lead` role — lower friction, and it's how the split was already configured on prod (2025-10-02). Swap to a dedicated role later if desired.

## Standard flow (SOP)

1. Field team lead raises a **Material Request** (type **Purchase**) with `Material Request Item.project` set to the job (or an **Internal** project for overhead).
2. PM reviews the MR, then **`Create → Purchase Order`** — PO items inherit `project` from the MR items (native mapping).
3. PO submit follows WI-013's threshold/authorization rule (when live).

## Buying Settings (reviewed, left as-is)

`po_required = No`, `pr_required = No` — **intentional.** There is no native "MR required before PO" toggle; the MR-before-PO discipline is enforced by the role split + this SOP. Hard-requiring a PO would block the ~77%-Staffing / subcontract-labor supplier invoices that arrive **without** a PO day one.

## Version control (the "C9" correction)

The applied Custom DocPerm rows for `Material Request` + `Purchase Order` are now a fixture — `erpnext_enhancements/fixtures/custom_docperm.json` (10 rows) with the `hooks.py` allowlist entry `{"dt": "Custom DocPerm", "filters": [["parent", "in", ["Material Request", "Purchase Order"]]]}`. Because Custom DocPerm fully overrides a doctype's standard perms, these 10 rows are the **complete** effective permission set for both doctypes; `bench migrate` re-asserts them on every deploy and applies them to fresh sites. No more hand-clicked-only permission state.

## Acceptance & TEST verification

- [ ] `SELECT COUNT(*) FROM \`tabCustom DocPerm\` WHERE parent IN ('Material Request','Purchase Order')` > 0 — **10 on prod** ✅.
- [ ] Rows present in the repo (fixture) ✅.
- [ ] **On TEST, as a `Stock User`-only test user:** can submit a Material Request; gets **PermissionError** creating a Purchase Order.
- [ ] **On TEST, as a PM (`Purchase User`) test user:** `Create → Purchase Order` from the MR succeeds and PO items inherit `project` from the MR items.

## Notes for review

- `External Contractor` currently holds broad MR perms (create/**delete**/**amend**) — pre-existing, not introduced here. Trim in a Phase-2 permission review if subcontractors shouldn't delete/amend MRs.
- Making `project` mandatory on PO/PI lines is **WI-014**; the dollar-threshold escalation is **WI-013**; supplier-master cleanup is out of scope.
