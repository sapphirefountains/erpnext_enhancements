# WI-066: Restrict Purchase Order creation to a dedicated role, and stop a requester approving their own request
**Phase:** 0   **Type:** APP_CODE   **Size:** M
**Blocked by:** WI-012, WI-013   **Blocks:** nothing

## Why
WI-012 shipped the Material Request → Purchase Order split as a Custom DocPerm fixture, but granted Purchase Order create/write/submit to **`Purchase User`** — a role carried by the **Production Team, Design Team, Finance Team and Sales Team** profiles. The effective result is the opposite of the intent: **16 enabled users can create and submit Purchase Orders** (prod_roles_audit), against 5 who should. Five people do the actual buying — parker.bailey 74 POs, james.harris 24, nikolas.bradshaw 23, daniel.blass 3, clegg.mabey 2 (prod_po_by_owner) — so the control is a paper one, not a practical one, and closing it costs almost nothing operationally.

Separately there is no segregation on the requisition chain. Of 127 POs only **7 carry a `Purchase Order Item.material_request` link**, and in **all 7 the PO owner is the Material Request owner** (prod_po_mr_join) — every requisition ever converted on this site was self-approved. WI-013's threshold forces a second person above $500, but says nothing about whose request it was.

Third, `kendalyn.harris@` holds none of the five roles with Material Request access and **cannot raise a request at all**, which contradicts the stated rule that anyone may ask for materials.

## Native-first check
Role + Custom DocPerm is native and SUFFICIENT for the access half. The segregation half has **no native equivalent**: Authorization Rules key on amount, role and doctype — never on document lineage — so there is no native way to express "not if you own the linked Material Request". A `before_submit` hook is therefore justified APP_CODE, and follows the precedent set by WI-013's `po_approval.enforce_threshold` rather than introducing a second mechanism.

## Preconditions
- CEO sign-off on the five PO Creators and on **Lisa Symanski as a third `PO Approver`** (decision below).
- Confirmation that daniel.blass loses PO create/submit (he keeps Material Request, Supplier, RFQ/Supplier Quotation and PO read/print) — **confirmed**.
- Acceptance that **Close / Hold / Re-open** move with the role: ERPNext gates `update_status` on `submit` permission (`erpnext/buying/doctype/purchase_order/purchase_order.py:948`), so only PO Creators can close a PO out — **confirmed**, Lisa (AP) and Parker are both on the list.

## Scope
- Seed a **`PO Creator`** Role via `patches/seed_po_creator_role.py` (not a fixture — fixtures import in alphabetical filename order, so `custom_docperm.json` lands before `role.json`).
- A single-role **`PO Creators`** Role Profile, so the two grantees who carry a role profile can hold the role durably. Deliberately not named `Purchasing`: a legacy `Purchase` profile already exists and the two would be confusable.
- Custom DocPerm: `PO Creator` gains the full Purchase Order flag set at permlevel 0 and 1; **in a later release** `Purchase User` and `Purchase Manager` drop to read/report/print/email.
- `Employee Self Service` gains Material Request create/write/submit and joins the `HR` profile, so anyone employed here can raise a request.
- `po_segregation.enforce_requester_separation` on `before_submit`, ordered ahead of `po_approval.enforce_threshold`, with a Settings kill switch (`po_sod_enforcement_enabled`, default on).
- Client-side guards so the "Create → Purchase Order" affordances disappear for users who can't act on them, instead of failing at save.
- The optional Request for Quotation path documented in the SOP, and the two seeded process diagrams that contradicted it corrected.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabCustom DocPerm\` WHERE parent='Purchase Order' AND permlevel=0 AND role<>'PO Creator' AND (\`create\`=1 OR \`write\`=1 OR \`submit\`=1)` = **0**.
- `SELECT DISTINCT parent FROM \`tabHas Role\` WHERE parenttype='User' AND role='PO Creator'` = exactly the five.
- `SELECT parent FROM \`tabUser Role Profile\` WHERE role_profile='PO Creators'` = clegg.mabey + lisa.symanski, and each survives a second `User` save (the durability proof — Frappe regenerates `roles` from profiles on every save).
- `SELECT parent FROM \`tabHas Role\` WHERE parenttype='User' AND role='PO Approver'` = james.harris, nikolas.bradshaw, lisa.symanski.
- kendalyn.harris holds a role with `Material Request.create` = 1.
- `SELECT COUNT(*) FROM \`tabCustom DocPerm\` WHERE parent IN ('Request for Quotation','Supplier Quotation')` = **0** — those two stay on standard perms deliberately.
- On TEST: a `PO Creator` who owns the linked MR is blocked at Submit; a second `PO Creator` submits the same draft; a user holding **both** `PO Creator` and `PO Approver` who owns the MR is **still blocked**.
- **90-day check:** `SELECT COUNT(DISTINCT parent) FROM \`tabPurchase Order Item\` WHERE IFNULL(material_request,'')<>''` — baseline 7 of 127. If this has not moved, the gate is decorative and the real gap is MR adoption, not PO permissions.

## Rollback
Revert the subtractive release's `custom_docperm.json` commit — the next `bench migrate` restores `Purchase User`/`Purchase Manager` create/write/submit. For the guard alone, untick **Enforce PO Separation of Duties** in Settings (no deploy) or remove the `po_segregation` entry from `hooks.py`. User roles restore from the snapshot taken in step 0 of the runbook. Note the additive release cannot be undone by reverting JSON — fixture sync is create/update-only — but it is harmless left in place.

## Explicitly NOT in this work item
Requiring a Material Request before a Purchase Order (no native toggle; would be its own item with its own flag). Mandating supplier quotes above a threshold, sole-source waivers, or any RFQ gate — WI-066 documents the path, it does not police it. Supplier-master cleanup (still the 1,156-supplier / 892-'Staffing' problem WI-012 punted). Per-project and percentage-of-budget thresholds (WI-057/WI-058). Purchase Invoice approval workflow (WI-044) — note that with `po_required = No`, an `Accounts Manager` can still book a supplier liability with no PO and no MR, entirely outside this control.
