# WI-012 — Purchasing flow: Material Request (team lead) → Purchase Order (PM)

A permission split, not custom code: field team leads request materials; PMs commit company money by converting the request to a PO.

> ⚠️ **Superseded in part by [WI-066](wi066-po-creator-and-sod.md).** The split below granted Purchase Order create/write/submit to `Purchase User` — a role four Role Profiles carry, so sixteen enabled users could commit money rather than the handful intended. WI-066 moves those bits to a dedicated **`PO Creator`** role and adds a segregation-of-duties gate on submit. **The table below is the original design; the current one is in the WI-066 doc.**

## Role split (native Role Permissions) — original WI-012 design

| Role (who) | Material Request | Purchase Order |
|---|---|---|
| **`Stock User`** — field team leads (via the Production Team profile) | create / write / submit | **read-only** (cannot create) |
| **`Purchase User`** / **`Purchase Manager`** — PMs / purchasing | create / write / submit | **create / write / submit** → **read-only** as of WI-066 |
| `Stock Manager` | create / write / submit | (no access) |
| `External Contractor` — subcontractors | create / write / submit | read-only |
| **`PO Creator`** — added by WI-066 | (via their other roles) | **create / write / submit** |
| **`Employee Self Service`** — added by WI-066 | **create / write / submit** | (no access) |

So a team lead gets a **PermissionError** creating a PO; a `PO Creator` can `Create → Purchase Order` from the MR. PO **submit** is further governed by WI-013's dollar threshold and WI-066's segregation gate.

**Team-lead role decision:** reuse native **`Stock User`** (already held by field staff via the Production Team profile) rather than seeding a dedicated `Field Team Lead` role — lower friction, and it's how the split was already configured on prod (2025-10-02). Swap to a dedicated role later if desired.

## Standard flow (SOP)

1. **Anyone** raises a **Material Request** (type **Purchase**) with `Material Request Item.project` set to the job — or one of the five **Overhead** buckets for non-job spend (v1.187.0; the 13 `Internal` projects are specific R&D jobs, not overhead).
2. *(Optional — recommended for new suppliers or high-value spend)* Raise a **Request for Quotation** from the MR, or from the Project form's **+ Request Quote** button; suppliers return **Supplier Quotations** and the winner converts to the PO. **Not a gate** — no dollar amount requires it and skipping it blocks nothing. Added by WI-066, which documents this path; before that it existed in the software but in no policy.
3. A **`PO Creator`** reviews the MR, then **`Create → Purchase Order`** — PO items inherit `project` from the MR items (native mapping), and a blank line project is filled from the header (v1.187.0).
4. **Separation of duties (WI-066):** whoever *raised* the MR cannot *submit* the PO that fills it. They may draft it; another `PO Creator` submits. No role overrides this, not even the CEO.
5. PO submit above the threshold additionally needs a **`PO Approver`** (WI-013).

## Buying Settings (reviewed, left as-is)

`po_required = No`, `pr_required = No` — **intentional.** There is no native "MR required before PO" toggle; the MR-before-PO discipline is enforced by the role split + this SOP. Hard-requiring a PO would block the ~77%-Staffing / subcontract-labor supplier invoices that arrive **without** a PO day one.

## Version control (the "C9" correction)

The applied Custom DocPerm rows for `Material Request` + `Purchase Order` are now a fixture — `erpnext_enhancements/fixtures/custom_docperm.json` (**13 rows** after WI-066; 10 as originally shipped) with the `hooks.py` allowlist entry `{"dt": "Custom DocPerm", "filters": [["parent", "in", ["Material Request", "Purchase Order"]]]}`. Because Custom DocPerm fully overrides a doctype's standard perms, these rows are the **complete** effective permission set for both doctypes; `bench migrate` re-asserts them on every deploy and applies them to fresh sites. No more hand-clicked-only permission state.

**Editing, not deleting.** Fixture sync only creates and updates — removing a row from the JSON leaves it in the database untouched. WI-066 therefore flips the `Purchase User` / `Purchase Manager` flags **in place** rather than dropping the rows, which would have read in git as though the control shipped while changing nothing on prod.

## Acceptance & TEST verification

- [ ] `SELECT COUNT(*) FROM \`tabCustom DocPerm\` WHERE parent IN ('Material Request','Purchase Order')` > 0 — **10 on prod** ✅.
- [ ] Rows present in the repo (fixture) ✅.
- [ ] **On TEST, as a `Stock User`-only test user:** can submit a Material Request; gets **PermissionError** creating a Purchase Order.
- [ ] **On TEST, as a PM (`Purchase User`) test user:** `Create → Purchase Order` from the MR succeeds and PO items inherit `project` from the MR items.

## Notes for review

- `External Contractor` currently holds broad MR perms (create/**delete**/**amend**) — pre-existing, not introduced here. Trim in a Phase-2 permission review if subcontractors shouldn't delete/amend MRs.
- Making `project` mandatory on PO/PI lines is **WI-014**; the dollar-threshold escalation is **WI-013**; supplier-master cleanup is out of scope.
