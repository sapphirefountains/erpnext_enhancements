# WI-066 — `PO Creator` role, and separation of duties on Purchase Order submit

Two controls on the same document. **Who may commit company money** is narrowed from sixteen people to five. **Whoever asked for the materials may not also be the one who buys them.**

Shipped in two releases on purpose — see [Rollout](#rollout-two-releases).

## Where it's configured

**ERPNext Enhancements Settings → Purchasing Controls → Enforce PO Separation of Duties**
- Check, **default ON**. Unticking it stops the segregation gate immediately — no deploy needed. It does not affect the role restriction, which is permission state.
- The sibling **PO Approval Threshold (Amount)** (WI-013) is unchanged.

The role restriction is not configurable: it is Custom DocPerm, applied by `bench migrate` from `erpnext_enhancements/fixtures/custom_docperm.json`.

## Role split (supersedes the WI-012 table)

| Role (who) | Material Request | Purchase Order |
|---|---|---|
| **`PO Creator`** — the five buyers | create / write / submit (via their other roles) | **create / write / submit / cancel / amend** |
| `Purchase User` / `Purchase Manager` — PMs, designers, finance | create / write / submit | **read / report / print / email** — cannot create or commit |
| `Employee Self Service` — everyone employed here | **create / write / submit** | (no access) |
| `Stock User` — field team leads | create / write / submit | read-only |
| `Stock Manager` | create / write / submit | (no access) |
| `External Contractor` — subcontractors | create / write / submit | read-only |

**`PO Creator` holders (5):** James Harris, Lisa Symanski, Clegg Mabey, Nikolas Bradshaw, Parker Bailey.
**`PO Approver` holders (3):** James Harris, Nikolas Bradshaw, **Lisa Symanski** — added by WI-066; see [Why a third approver](#why-a-third-approver).

Because Custom DocPerm fully overrides a doctype's standard perms, the 13 rows in the fixture are the **complete** effective permission set for both doctypes.

> **Close / Hold / Re-open move with the role.** ERPNext gates `update_status` on `submit` permission (`purchase_order.py:948`), not `write` — so after WI-066 only a `PO Creator` can close out a PO. Lisa (AP) and Parker (the main purchaser) are both on the list, so the people who actually do it still can. This is a coupling, not a bug.

## Standard flow (SOP)

1. **Anyone** raises a **Material Request** (type **Purchase**) with `Material Request Item.project` set to the job — or one of the five **Overhead** buckets for non-job spend (WI-014 / v1.187.0).
2. *(Optional — worth doing on new suppliers or high-value spend)* A purchaser raises a **Request for Quotation** from the MR, or from the Project form's **+ Request Quote** button. Suppliers return **Supplier Quotations**; the winner converts with `Create → Purchase Order`. **This is not a gate.** Skipping it blocks nothing, and no dollar amount requires it.
3. A **`PO Creator`** converts the Material Request (or the winning Supplier Quotation) with `Create → Purchase Order`. PO items inherit `project` from the source lines; a blank line project is filled from the header (v1.187.0).
4. **Separation of duties:** the person who *raised* the Material Request cannot *submit* the Purchase Order that fills it. They may draft it — another `PO Creator` submits. **No role overrides this**, not `Purchase Manager`, not `PO Approver`, not the CEO. Only `Administrator`.
5. Above the **PO Approval Threshold** the submitter must additionally hold **`PO Approver`** (WI-013). Both gates apply; the segregation one reports first.

## What the segregation gate does and does not catch

Written down because these are design boundaries, not defects:

- **It only fires when a PO line carries a `material_request` link** — which happens through native `Create → Purchase Order` mapping. A Purchase Order typed from scratch is not checked, and today **120 of 127 POs** have no MR link at all. The gate's reach grows only as MR-first discipline is adopted; a 90-day re-measure is an acceptance criterion in the work item for exactly that reason.
- **`owner` is the only requester identity.** `Material Request` has no `requested_by` field on this site, so an MR typed up on someone else's behalf binds the typist, not the person who wanted the materials. Raise requests in your own name.
- **Handing a draft to a colleague is the intended outcome, not a loophole** — but it is a *procedural* control at that point. The software guarantees two humans touched the document; it cannot guarantee the second one read it.
- **It is not a spend control.** `Buying Settings.po_required = "No"`, and an `Accounts Manager` can book a Purchase Invoice with no PO and no MR. The spend controls are WI-013's threshold and the Purchase Invoice approval workflow (WI-044, dormant).
- **`Administrator` break-glass voids both gates at once** — it bypasses the threshold too, and looks identical to a normal submit in the audit trail. Use a named account.

## Why a third approver

Above the threshold only a `PO Approver` may submit. With two holders and no segregation bypass, a PO built from James's Material Request must be submitted by Nik and vice versa — and a PO consolidating Material Requests from **both** would be submittable by nobody but `Administrator`. That is a hard deadlock, and it becomes reachable the moment several MRs are consolidated onto one supplier PO, which is the normal reason to link MRs at all.

37 POs a year, worth $221,685 — 97% of PO spend — sat behind exactly two people's availability. Adding Lisa removes the deadlock and the bus factor without weakening the rule for anyone.

## Version control

| Artifact | File |
|---|---|
| `PO Creator` Role | `patches/seed_po_creator_role.py` |
| `PO Creators` Role Profile | `fixtures/role_profile.json` + the `hooks.py` name-in allowlist |
| Purchase Order / Material Request perms | `fixtures/custom_docperm.json` (13 rows) |
| Segregation gate | `po_segregation.py`, `hooks.py` `doc_events`, `tests/test_po_segregation.py` |
| Kill switch default | `patches/default_po_sod_on.py` |

**The role is seeded by a patch, not a fixture, and that is load-bearing.** Fixture files import in **alphabetical filename order** (`frappe/utils/fixtures.py` sorts the directory listing) — not the order of the `hooks.py` list, which governs export only. So `custom_docperm.json` imports *before* `role.json`, and a `PO Creator` permission row would reference a Role that does not exist yet. `post_model_sync` patches run before fixture sync, which closes it.

## Rollout (two releases)

The role must be **held** before the old grant is removed. Role assignment is a manual Desk step, so shipping both halves at once would leave a window in which nobody but `Administrator` could create or submit a Purchase Order.

| Release | Contents |
|---|---|
| **v1.191.0** — additive, safe | Seed the role and profile; *add* the `PO Creator` permission rows; Material Request access fix; the segregation gate; docs; diagram fixes. Nobody loses anything. |
| *(manual)* | The Desk runbook below, then the verification queries. |
| **v1.192.0** — subtractive | `Purchase User` and `Purchase Manager` lose create/write/submit on Purchase Order. |

## Desk runbook

> **Role changes must be made in the Desk UI, not the API.** MCP/REST `update_document` on `User.roles` is silently discarded while a role profile is set. Reserve the API for reads.

**Step 0 — snapshot.** For each of the five, record `role_profile_name`, the `role_profiles` rows and the full `roles[]` list. This is the rollback artifact.

**Step 1 — the three users with no role profile.** James Harris, Nikolas Bradshaw, Parker Bailey: open **Users & Permissions → User**, tick **`PO Creator`** in the Roles grid, Save.

> ⚠️ **Never assign a Role Profile to these three.** They hold direct roles including `System Manager` and `PO Approver`. Setting any profile makes `User.validate` regenerate `roles` from the profile alone — the CEO would lose `PO Approver` and the sys-admin would lose `System Manager`.

**Step 2 — the two users who have a role profile.** Clegg Mabey (Production Team) and Lisa Symanski (Finance Team): add **`PO Creators`** as a **second** entry in the Role Profiles table, keeping the existing one. Frappe unions the roles of multiple profiles, and the Desk disables the Roles grid outright once a profile is set — so this is the only route, not merely the preferred one.

> ⚠️ Do not confuse **`PO Creators`** with the pre-existing **`Purchase`** profile.
> Save each user a **second** time and re-check that `PO Creator` is still present. That round-trip is the durability proof this whole mechanism exists for.

**Step 3 — Lisa Symanski also gets `PO Approver`.** Via the `PO Creators` profile she holds only `PO Creator`; `PO Approver` is a separate grant. Because she carries role profiles, it must reach her through a profile too — add it to `Finance Team`, of which she is the only member.

**Step 4 — Daniel Blass.** Reassign or delete his one $0 draft Purchase Order (`PO-2026-00029`) before the subtractive release; afterwards he can read it but not write, discard or delete it.

**Step 5 — nobody else needs editing.** The other eleven lose PO create/submit at the DocPerm level. Do **not** strip `Purchase User` from anyone — they still need it for Material Request, Supplier, Item and RFQ access.

**Step 6 — hold the gate.** Merge v1.192.0 only once the verification below passes on prod.

*Timing note:* `RoleProfile.on_update` queues a locking background job, and a deploy's Redis flush can orphan that lock for up to 3h (`setup/document_locks.py` sweeps it on `before_migrate`). Editing the **profile itself** in Desk inside that window can raise `DocumentLockedError`; adding it to a **User** is a User save and is unaffected.

## Acceptance & verification

```sql
-- expect 0 — nobody but PO Creator can create, write or submit a PO
SELECT COUNT(*) FROM `tabCustom DocPerm` WHERE parent='Purchase Order' AND permlevel=0
  AND role<>'PO Creator' AND (`create`=1 OR `write`=1 OR `submit`=1);

-- expect the five / 'PO Creators' / clegg + lisa
SELECT DISTINCT parent FROM `tabHas Role` WHERE parenttype='User' AND role='PO Creator';
SELECT parent FROM `tabHas Role` WHERE parenttype='Role Profile' AND role='PO Creator';
SELECT parent FROM `tabUser Role Profile` WHERE role_profile='PO Creators';

-- expect james, nikolas, lisa
SELECT parent FROM `tabHas Role` WHERE parenttype='User' AND role='PO Approver';

-- expect >= 1 — Kendalyn can raise a Material Request
SELECT COUNT(*) FROM `tabCustom DocPerm` p
JOIN `tabHas Role` r ON r.role=p.role AND r.parenttype='User'
WHERE r.parent='kendalyn.harris@sapphirefountains.com'
  AND p.parent='Material Request' AND p.`create`=1;

-- expect 0 — RFQ and Supplier Quotation stay on standard perms
SELECT COUNT(*) FROM `tabCustom DocPerm`
WHERE parent IN ('Request for Quotation','Supplier Quotation');

-- baseline 7 of 127 — re-run at 90 days; the gate is only as good as this number
SELECT COUNT(DISTINCT parent) FROM `tabPurchase Order Item`
WHERE IFNULL(material_request,'')<>'';
```

**Behavioural checks on TEST:**

- [ ] `Purchase User`-only user: PO list opens and prints; no `+ Add`; no `Create → Purchase Order` on a submitted MR; the Procurement dashboard still renders.
- [ ] `PO Creator`: creates, saves and submits a PO under the threshold.
- [ ] `PO Creator` who owns the linked MR: **Submit raises "Separation of Duties"**, and the message names the MR, `PO Creator` and `PO Approver`.
- [ ] A second `PO Creator` submits that same draft successfully.
- [ ] A user holding **both `PO Creator` and `PO Approver`** who owns the MR: **still blocked**.
- [ ] Same PO over the threshold, submitted by a `PO Creator` without `PO Approver`: gets WI-013's "Approval Required" — proving the second gate still runs.
- [ ] `PO Creator` on a PO with no MR link: submits normally.
- [ ] kendalyn.harris creates and submits a Material Request.
- [ ] `+ Request Quote` on a Project still opens a new RFQ for a `Purchase User`.
- [ ] Unticking **Enforce PO Separation of Duties** lets the blocked submit through immediately.

## Rollback

Revert the v1.192.0 `custom_docperm.json` commit — the next migrate restores the old grants; no data impact. For the gate alone, untick the Settings flag (instant) or remove the `po_segregation` entry from `hooks.py` (needs a deploy). Roles restore from the step-0 snapshot in Desk.

The **additive** release cannot be rolled back by reverting JSON: fixture sync is create/update-only, so the added rows and the profile would survive. Removing them needs a one-shot `frappe.delete_doc` patch. In practice leave it — an unheld role grants nobody anything.

## Not in scope

Requiring an MR before a PO. Mandating supplier quotes, sole-source waivers or any RFQ gate — the path is documented, not policed. Supplier-master cleanup. Per-project / percentage thresholds (WI-057, WI-058). The Purchase Invoice approval workflow (WI-044).
