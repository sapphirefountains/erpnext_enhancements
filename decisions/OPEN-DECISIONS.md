# Open Decisions Register — Sapphire Fountains ERPNext Migration

These are **business decisions, not engineering ones**. No work item resolves them; each OD-gated work item carries the decision as an explicit precondition and is written to execute under any branch. Facts cited were verified against the live systems on 14 Jul 2026.

---

## OD-1 — JDH (the CEO's second company): in ERPNext or not?

**Verified state:** exactly one Company exists on both instances — 'Sapphire Fountains' (abbr SF). No JDH record anywhere.

**Blocks:** the *scope* of the Chart of Accounts rebuild execution (WI-029: one company or two) and WI-061. The CoA **design** (WI-004) is deliberately branch-proof: a company-agnostic numbered template importable per-company via the native Chart of Accounts Importer, with no 'SF'/'Sapphire' embedded in account names and identical account numbers across companies.

**Branches:**
- **(a) JDH stays in QuickBooks** — single-company chart; nothing else changes.
- **(b) Second `Company` in the same instance now** — import the same CoA CSV under the JDH abbreviation (native per-company charts give structurally parallel trees with automatic ' - <abbr>' suffixing); add inter-company accounts; Authorization Rule and Bank Account masters are per-company and get cloned/scoped (noted in WI-013/WI-042). The Stripe module is single-company (one `company` field) — JDH payments need a gap analysis first.
- **(c) Phase-2 migration** — Phase 1 proceeds as (a); WI-061 executes later. Adding a company is possible any time because the chart template is reusable — **this decision is NOT cutover-blocking**.

**Cheapest decision deadline:** before WI-029 executes (late Dec 2026), but only to avoid a second import pass.

---

## OD-2 — Utah sales-tax taxability (Build vs Service vs rentals/products)

**This is a CPA ruling, not a config choice.** Utah taxes improvement to real property (contractor pays tax on materials; customer not charged) differently from repair of tangible personal property (taxable to customer).

**Verified state:** 3 QBO-imported rate templates exist ('US ST 4% - SF', 'US ST 6% - SF' default, 'US ST 6.25% - SF', all single-row On Net Total) + 3 mirror Item Tax Templates; Tax Category and Tax Rule are present and EMPTY (rate selection is fully manual today); the stock 'Tax Detail' report is absent from this build.

**Blocks:** WI-036 (template rebuild), WI-037 (Tax Category/Tax Rule automation), WI-038 (filing procedure columns: taxable vs exempt by stream/jurisdiction).

**Branches:**
- **(a) Uniform taxability** → keep rate-per-jurisdiction templates; Tax Rules keyed on customer category/state only.
- **(b) Stream-differentiated (Build ≠ Service)** → Item Tax Templates per Item Group + Tax Categories on customers/items; the filing procedure splits by stream; purchase-side use-tax handling for Build materials is documented (Phase-2 candidate if Build is non-taxable to customers).

**Mitigation if late:** go live with today's manual template selection; the liability still books correctly to the per-jurisdiction sub-accounts in the new chart. Automation lands as a January fast-follow. **Escalate the ruling request now.**

---

## OD-3 — "Rent" vs "Events": same value stream?

**Verified state:** the app and the data both say **Rent** — `project_type` Rent = 61 projects, and the Closed-Won handoff's value-stream priority list is `[Design, Build, Service, Rent]`. 'Events' appears nowhere in code or data.

**Blocks:** WI-027 (backfilling the 71 untyped projects), Item Group naming, value-stream reporting labels, and the reserved 'Events' income account number in the CoA design (WI-004 reserves a sibling number so a late decision costs nothing structurally).

**Branches:**
- **(a) Same stream, keep 'Rent'** — no changes.
- **(b) Same stream, rename to 'Events'** — rename the Project Type, the value-stream rows, and the handoff priority-list constant (one APP_CODE line).
- **(c) Distinct streams** — add an 8th project_type and split the 61 Rent projects per a business-provided list.

---

## OD-4 — Commercial/Residential segment placement

**Verified state (changes the question):** `Customer.customer_type` ALREADY carries the segment for 1,212 customers — Commercial = 1,040, Residential = 172 (plus Company 365, Individual 13, Partnership 12 — non-standard select options were added at some point). `customer_group` is 71% empty, with 454 customers already 'Government'. **No project-level segment field exists yet.**

**Blocks:** WI-026 (customer-group taxonomy backfill of 1,146 rows), any new Project segment Custom Field (would ship as FIXTURE), WI-054 (revenue-by-segment reporting).

**Branches:**
- **(a) Project attribute + customer fallback for the Products stream** (the brief's stated direction) → add a Project custom field (FIXTURE); WI-054's native **Accounting Dimension 'Segment'** captures one unified number at entry (project attribute first, customer fallback), eliminating the need for a custom union report.
- **(b) Customer-only** → leverage the existing `customer_type` data; no new field; reporting reads the customer attribute.
- **(c) Both mandatory** → highest data-entry tax; conflicts with the accountant's minimal-UI demand — flagged, not recommended.

**Note:** the "report must union both sources" requirement dissolves if the Accounting Dimension approach in branch (a) is accepted — the dimension IS the union, captured at entry.

---

## OD-5 — Cutover date commitment

Treated as **fixed: 2027-01-01**. Scope flexes into Phase 2; the date does not. Anything not on the critical path by the ~1 Dec 2026 UAT start moves to Phase 2. The go/no-go meeting (~22 Dec 2026, WI-051) is the formal checkpoint; its abort path (stay on QBO for January, ERPNext parallel continues) is enumerated in the runbook, not decided.

---

## OD-6 — Draft-mirror disposition (discovered during planning)

**Verified state:** prod holds 15,947 unsubmitted QBO-imported drafts — 12,341 Journal Entries ($13.34M total_debit), 1,563 Sales Invoices (~$9.18M), 1,405 Payment Entries, 638 Quotations. They carry zero GL value but: (1) any accidental submit posts phantom history into the new ledger; (2) they hard-block the CoA rebuild (Frappe link-checks refuse to delete referenced Accounts); (3) list-view noise and naming-series pollution.

**Blocks:** WI-028 (which needs this ratified before deleting anything).

**Branches:**
- **(a) Bulk delete after the final pre-cutover sync — RECOMMENDED.** Reference value survives three ways: QBO itself stays accessible read-only, every imported record's JSON is archived in `QuickBooks Raw Payload`, and a pre-delete CSV export is taken. Live quotations are protected: WI-023 triages and submits keepers first.
- **(b) Keep + flag/filter** — retains in-ERP reference but leaves the accidental-submit risk, still blocks Account deletion (forcing the messier rename/merge CoA path), and pollutes lists.
- **(c) Selective** — delete PEs/JEs, keep SIs as reference; partial versions of both cost profiles.

---

## OD-7 — Stripe surcharging at go-live (discovered during planning)

**Verified state:** surcharging is built and even enabled on TEST, but ships default-OFF behind an 8-item legal/operational checklist (`docs/stripe_surcharging_compliance.md`): counsel sign-off, 30-day card-network notice, caps (≤3% and ≤ cost of acceptance), banned-state suppression, debit-limitation acceptance, disclosure, refund-returns-surcharge verification, Amex equal treatment.

**Blocks:** WI-055 only. Nothing on the critical path.

**Default:** launch **without** surcharge; enable later (earliest ~Feb 2027 given the 30-day notice) only after every checklist item has evidence. An ACH-convenience-fee-only launch is a permissible earlier branch if counsel approves (outside card-network rules).
