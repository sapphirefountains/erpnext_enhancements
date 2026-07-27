# WI-014 — Project on purchasing lines (PO + PI items)

Material **and** subcontract cost must land on the job for profitability. `Project` is native but optional and buried on purchase line items; this surfaces it (both PO and PI) and — **Branch A** — requires it on Purchase Order lines.

## What ships (Property Setter fixtures)

| Setter | Effect | Status |
|---|---|---|
| `Purchase Order Item.project` → `in_list_view=1` | Project column visible in the PO line grid | already live + fixtured |
| `Purchase Invoice Item.project` → `in_list_view=1` | Project column visible in the PI line grid | already live + fixtured |
| **`Purchase Order Item.project` → `reqd=1`** | **Project mandatory on every PO line** | **new (this item)** |

**Branch A** (chosen): mandatory on **PO lines only**. Purchase Invoice lines stay **optional** so the accountant's rapid bill entry isn't blocked — the "Invoices without Project" saved filter (WI-008) + December UAT drive PI adoption instead.

## ✅ Precondition met (v1.187.0) — overhead buckets + a header→line cascade

`reqd=1` needs every PO line to have a legitimate `Project` target. Two gaps blocked purchasers; both are closed.

### 1. Non-job spend had nowhere to land

The 13 `Internal`-type projects on prod are all **specific R&D development projects** (`IDP000`–`IDP016`, plus `PRJ-00739`) — there was **no generic overhead bucket**, so a PM buying office or shop supplies had nothing to pick and was blocked.

`patches.seed_overhead_projects` now creates a **`Overhead` Project Type** and five standing buckets:

| Bucket | For |
|---|---|
| `Overhead - Office & Admin` | Office supplies, paper, breakroom, postage, printing, professional services |
| `Overhead - Shop & Warehouse` | Shop consumables, hand tools, safety gear, cleaning, packaging |
| `Overhead - Fleet & Vehicles` | Company vehicle fuel, maintenance, parts, registration, tires |
| `Overhead - IT & Software` | Hardware, licences and SaaS, phones, network gear, IT services |
| `Overhead - Marketing & Trade Shows` | Booths and materials, samples, advertising, branded merchandise |

Their own Project Type — **not** `Internal` — keeps overhead separable from R&D in reporting, and the Projects Dashboard excludes them automatically (it lists only `Build/Design/Events/Service/Delivery`). The patch is insert-only and matched on `project_name`, so later renames survive re-migration.

### 2. The header Project never reached the lines

ERPNext does not push `Purchase Order.project` down to `Purchase Order Item.project`. Measured on prod before the fix: of **204** lines sitting under a PO that *did* name a header project, **44 were still blank** — those POs refused to save even though the job was stated on the document.

`public/js/purchase_order_project.js` (Desk) and `procurement_project.cascade_project_to_items` (`before_validate`, for REST/import/MR-mapped paths) fill **blank** line projects from the header — on header change, on row add, and once more on validate for rows pulled in by "Get Items From". Existing line values are never overwritten, so a PO legitimately spanning two jobs keeps its per-line attribution. A draft PO with no project at all shows a dashboard hint naming the five buckets.

Net effect: the purchaser states the project **once**, on the header.

## Acceptance & verification

- [ ] Fixture contains the entries: `grep 'Purchase Order Item-project' fixtures/property_setter.json` → `in_list_view` **and** `reqd`. ✅
- [ ] **On TEST:** the PO line grid shows a **Project** column; saving a PO line **without** a project raises the mandatory error; an `Overhead - *` project is selectable. PI lines remain optional.
- [ ] **On TEST (v1.187.0):** five `Overhead - *` projects exist under Project Type `Overhead`; setting only the PO **header** Project saves cleanly with every line filled; a line already pointing at another job is left untouched; a draft PO with no project shows the overhead hint.
- [ ] UAT (from 2027-01-01): submitted PO lines with a blank project trend to 0.

## Rollback

Remove the `Purchase Order Item-project-reqd` entry from `fixtures/property_setter.json` and redeploy (the field reverts to optional). Existing documents are unaffected. The visibility setters can stay.

The v1.187.0 additions roll back independently: drop the `before_validate` entry and the `purchase_order_project.js` line from `hooks.py` to disable the cascade. The seeded overhead projects are ordinary Project records — set them inactive rather than deleting, since POs will already reference them.

## Not in scope

Making PI lines mandatory (branch-gated, deliberately not done); per-project budgets (WI-057/058); Cost Center restructuring.
