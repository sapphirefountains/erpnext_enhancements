# WI-014 — Project on purchasing lines (PO + PI items)

Material **and** subcontract cost must land on the job for profitability. `Project` is native but optional and buried on purchase line items; this surfaces it (both PO and PI) and — **Branch A** — requires it on Purchase Order lines.

## What ships (Property Setter fixtures)

| Setter | Effect | Status |
|---|---|---|
| `Purchase Order Item.project` → `in_list_view=1` | Project column visible in the PO line grid | already live + fixtured |
| `Purchase Invoice Item.project` → `in_list_view=1` | Project column visible in the PI line grid | already live + fixtured |
| **`Purchase Order Item.project` → `reqd=1`** | **Project mandatory on every PO line** | **new (this item)** |

**Branch A** (chosen): mandatory on **PO lines only**. Purchase Invoice lines stay **optional** so the accountant's rapid bill entry isn't blocked — the "Invoices without Project" saved filter (WI-008) + December UAT drive PI adoption instead.

## ⚠️ Precondition not yet met — a generic overhead project

`reqd=1` needs every PO line to have a legitimate `Project` target. The 13 `Internal`-type projects on prod are all **specific R&D development projects** (`IDP000`–`IDP016`, plus `PRJ-00739`) — there is **no generic overhead bucket**. So a PM buying general/shop supplies (not tied to a customer job or a specific IDP) has nothing obvious to pick and would be **blocked**.

**Action before this goes live to purchasers:** create at least one overhead `Internal` project — e.g. **`Internal - Shop Overhead`** (and optionally `Internal - G&A`) — so non-job POs have an obvious home. (Projects are instance data, not shipped in this fixture.) Low urgency today: purchasing is tiny (17 MRs / 96 POs) and real spend still flows through QBO until cutover.

## Acceptance & verification

- [ ] Fixture contains the entries: `grep 'Purchase Order Item-project' fixtures/property_setter.json` → `in_list_view` **and** `reqd`. ✅
- [ ] **On TEST:** the PO line grid shows a **Project** column; saving a PO line **without** a project raises the mandatory error; an `Internal - *` project is selectable. PI lines remain optional.
- [ ] UAT (from 2027-01-01): submitted PO lines with a blank project trend to 0.

## Rollback

Remove the `Purchase Order Item-project-reqd` entry from `fixtures/property_setter.json` and redeploy (the field reverts to optional). Existing documents are unaffected. The visibility setters can stay.

## Not in scope

Making PI lines mandatory (branch-gated, deliberately not done); per-project budgets (WI-057/058); Cost Center restructuring.
