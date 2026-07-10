# Sapphire Fountains — Department KPI Dashboard Catalog

_Auto-generated design reference. 131 KPIs across 8 departments. Tiers: **Auto** = computable now from existing data; **Semi-Auto** = needs one light new field/input; **Manual** = needs human entry or an un-integrated external system._

## Automation summary

| Department | KPIs | 🟢 Auto | 🟡 Semi | 🔴 Manual |
|---|---|---|---|---|
| Finance | 16 | 9 | 7 | 0 |
| Design (Water Engineering) | 16 | 11 | 4 | 1 |
| Sales | 18 | 13 | 3 | 2 |
| Marketing | 17 | 7 | 9 | 1 |
| Executive | 15 | 7 | 8 | 0 |
| Production (Build) | 15 | 8 | 5 | 2 |
| Operations (Field-Service / Maintenance / Workforce) | 17 | 11 | 5 | 1 |
| HR (People) | 17 | 12 | 3 | 2 |
| **TOTAL** | **131** | **78** | **44** | **9** |

---
## Finance

> A 16-KPI Finance catalog spanning the full controller/CFO scope for a fountain design-build-maintain business: cash flow & liquidity, revenue & gross margin, AR collections & DSO, AP & payment-terms discipline, billing accuracy (the biggest leak for a project + recurring-maintenance shop), budget/forecast variance, payroll & field-labor cost, profitability by project and by revenue segment (design / build / maintenance / rental), audit & compliance, and QBO/Stripe reconciliation health. Most KPIs are Auto or Semi-Auto by reusing data already wired: ERPNext GL/Sales Invoice/Purchase Invoice/Payment Entry (system of record after QBO sync), Stripe Payment, Document Intake, QuickBooks Sync Log/Mapping, Timesheet Detail, Project custom fields, and the Sapphire Maintenance Record/Contract. The hard truths: this is a single-company shop with no budget doctype, no cash-flow-forecast model, no quoted-cost baseline on projects, and no 13-week cash view — so budgeting/forecast, project margin-vs-bid, and forward cash all need one light new field or a small Budget/Forecast doctype. The Morning Briefing cron is the proven pattern for snapshotting these daily/weekly into a queryable Daily Briefing-style Finance KPI Snapshot. Recommended sequencing: stand up the Auto reconciliation/AR/AP/cash KPIs first (zero new data), then add the three Semi-Auto margin/billing fields, then the Budget doctype for true variance reporting.

### 1. Operating Cash Balance (Cash on Hand) — 🟢 Auto
- **Definition:** Sum of GL Entry balances for all Accounts where account_type IN ('Bank','Cash'): SUM(debit-credit) over tabGL Entry filtered to those accounts, as of posting_date <= today. Report total plus per-account breakdown (operating checking, savings, Stripe in-transit).
- **Why it matters:** A design-build shop carries large WIP (materials + field labor) for months before a milestone bills; knowing true bank cash daily prevents over-committing to material POs and missing payroll during the slow winter season when only maintenance startups/winterizations bill.
- **Target:** Maintain >= 8 weeks of operating expense coverage (set the dollar floor with the owner; ~2x average monthly payroll+overhead is a sensible starting benchmark).
- **Data source:** ERPNext GL Entry (account, debit, credit, posting_date) joined to Account (account_type IN Bank/Cash); populated by QBO CDC sync (hourly). Stripe in-transit visible via Stripe Payment.status='Processing' (ACH not yet settled).
- **Implementation:** frappe.db.sql aggregating SUM(debit-credit) on tabGL Entry GROUP BY account, filtered to Account.account_type IN ('Bank','Cash'). Snapshot daily via a scheduler_events['daily'] task writing to a new 'Finance KPI Snapshot' doctype (clone the Morning Briefing/Daily Briefing pattern in api/briefing.py). Dashboard Number Card for the live value.
- **Refresh:** Daily snapshot 06:30 (cron, America/Denver) + live on-demand; underlying QBO bank balances refresh hourly via cdc_poll.

### 2. 13-Week Rolling Cash Flow Forecast (Net Cash Position) — 🟡 Semi
- **Definition:** Projected weekly ending cash = opening cash + expected inflows (open Sales Invoice by due_date + contracted Contract Milestone amounts by trigger date + recurring maintenance invoices by invoicing_frequency) - expected outflows (open Purchase Invoice by due_date + scheduled payroll + recurring overhead). Rolled forward 13 weeks.
- **Why it matters:** Fountain projects bill on milestones (mobilization, substantial completion, final) that are weeks/months apart while material POs and field payroll go out weekly. A forward cash view is the single most important CFO tool to survive the lumpy build cash cycle and the seasonal maintenance trough.
- **Target:** Projected weekly ending cash never below the 8-week-coverage floor; flag any week projected < floor 4+ weeks out so financing/billing can be accelerated.
- **Data source:** Inflows: Sales Invoice (outstanding, due_date), Project Contract -> Contract Milestone (amount, due_upon/trigger date), Sapphire Maintenance Contract (invoicing_frequency) for recurring. Outflows: Purchase Invoice (outstanding, due_date), Timesheet Detail (labor run-rate), plus a small recurring-overhead assumption table. No forecast doctype exists today.
- **Implementation:** Build a small 'Cash Flow Forecast' generation function: pull dated AR/AP from existing doctypes (Auto part), but Contract Milestone needs a concrete expected_billing_date field (currently due_upon is a text trigger, not a date) and a recurring-overhead assumption needs one small input table. Add 'expected_billing_date' (Date) to Contract Milestone; store weekly overhead assumptions in a 1-row settings child table. Run weekly via scheduler_events['weekly'] into Finance KPI Snapshot; render as a desk chart/HTML block.
- **Refresh:** Weekly regeneration (Monday) + recompute on demand; AR/AP inputs are live.

### 3. Total Revenue (Recognized) & MoM Growth — 🟢 Auto
- **Definition:** SUM(Sales Invoice.base_grand_total) WHERE docstatus=1 AND posting_date in period, plus Stripe-only revenue not yet on an SI if any. MoM growth = (this month - prior month)/prior month. Trailing-12-month total for trend.
- **Why it matters:** Top-line health and seasonality baseline. Lets Finance see whether build revenue (lumpy) and maintenance revenue (recurring) are growing and detect the winter dip vs same period last year.
- **Target:** Set with owner; track YoY same-month growth >= target (e.g. +10% YoY) rather than raw MoM given seasonality.
- **Data source:** ERPNext Sales Invoice (base_grand_total, posting_date, docstatus); system of record after QBO sync. Stripe Payment.amount (status='Paid') as a cross-check on cash-basis receipts.
- **Implementation:** frappe.db.sql SUM(base_grand_total) GROUP BY MONTH on tabSales Invoice WHERE docstatus=1. Pre-aggregate into Finance KPI Snapshot daily; Dashboard Chart (Sum, Monthly interval) already supported by fixtures/dashboard_chart.json.
- **Refresh:** Daily snapshot; live via report builder. QBO invoices land within ~1 hour of edit.

### 4. Gross Margin % by Revenue Segment (Design / Build / Maintenance / Rental) — 🟡 Semi
- **Definition:** Gross margin = (segment revenue - segment direct cost) / segment revenue. Direct cost = COGS from Purchase Invoice + materials (Stock Entry valuation) + field/design labor (Timesheet Detail.amount) attributed to that segment. Segment derived from Project.project_type or Item group (design fee vs build vs maintenance vs rental).
- **Why it matters:** These four lines have wildly different economics: design fees are near-pure-margin labor, builds carry heavy material+subcontract cost, maintenance is route-density driven, rentals are asset-utilization driven. Blended margin hides which line is actually funding the business.
- **Target:** Set per segment with owner; typical benchmarks: design fees > 60%, build 25-40%, maintenance 40-55%, rental > 50%. Flag any segment trending down >5 pts QoQ.
- **Data source:** Revenue: Sales Invoice line items by item_group + Project.project_type. Cost: Purchase Invoice (project), Timesheet Detail.amount (project/activity_type), Stock Entry valuation, Sapphire Maintenance Record.total_labor_cost + consumables. Segment tag not yet standardized on every Sales Invoice.
- **Implementation:** Mostly computable, but needs a consistent segment tag. Add a 'custom_revenue_segment' Select (Design/Build/Maintenance/Rental) to Project (default by project_type) and ensure Sales Invoices carry the Project link. Then a SQL rollup joins SI revenue to PI+Timesheet+Stock Entry costs by project->segment. Snapshot monthly into Finance KPI Snapshot.
- **Refresh:** Monthly close + weekly running estimate; live inputs.

### 5. Project Gross Margin vs Bid (Build-Phase Cost Variance) — 🟡 Semi
- **Definition:** Per build Project: actual margin = (billed/contract value - actual cost-to-date) / contract value, where actual cost = Purchase Invoice + Material PO received + Timesheet Detail.amount + subcontract for that project. Variance = actual margin - bid/estimated margin. Also report cost-to-date vs estimated_costing.
- **Why it matters:** Build profit is made or lost in the field. Material price spikes (stainless, pumps, copper), rework, and labor overruns silently erode the bid. Catching a project running over its estimated cost mid-build is the difference between a profitable and a break-even fountain.
- **Target:** Actual margin within -3 pts of bid margin; zero projects exceeding 100% of estimated_costing before substantial completion without a change order.
- **Data source:** Project (custom_project_dollar_amount = contract value, estimated_costing). Costs: Purchase Invoice (project), Material Request/PO via get_procurement_status(), Timesheet Detail.amount. The bid/quoted COST baseline is NOT reliably stored per project.
- **Implementation:** Actual-cost rollup is Auto (join PI + Timesheet + procurement by project). Gap: no quoted-cost baseline. Add 'custom_bid_cost' (Currency) to Project, set at hand-off from the Project Contract / estimate. Then variance = (custom_project_dollar_amount - actual_cost) margin minus (custom_project_dollar_amount - custom_bid_cost) margin. Build a Project Profitability query report; snapshot active builds weekly.
- **Refresh:** Weekly for active builds; live cost inputs.

### 6. Days Sales Outstanding (DSO) — 🟢 Auto
- **Definition:** DSO = (Accounts Receivable balance / total credit revenue in period) * days in period. AR balance from GL Entry on receivable accounts; revenue from submitted Sales Invoices. Report rolling 90-day DSO.
- **Why it matters:** Cash is trapped in unbilled and uncollected milestones. High DSO on a build-heavy shop directly causes the cash crunch that forces borrowing to cover material POs and payroll.
- **Target:** DSO <= 45 days (commercial fountain clients often net-30 to net-45); investigate if > 55.
- **Data source:** ERPNext GL Entry (Account.account_type='Receivable') for AR balance; Sales Invoice (base_grand_total, docstatus=1) for revenue. All present after QBO sync. ERPNext also has a stock Accounts Receivable report.
- **Implementation:** frappe.db.sql: AR = SUM(debit-credit) on receivable accounts; revenue = SUM(base_grand_total) last 90d; DSO = AR/revenue*90. Snapshot daily into Finance KPI Snapshot; Number Card with trend.
- **Refresh:** Daily; QBO refresh hourly.

### 7. AR Aging & Overdue Collections Rate — 🟢 Auto
- **Definition:** Outstanding AR bucketed 0-30 / 31-60 / 61-90 / 90+ days past due_date (per Sales Invoice.outstanding_amount). Overdue % = (AR past due) / total AR. Collections rate = cash collected this period / (opening AR + new invoices).
- **Why it matters:** Aged fountain receivables (esp. disputed final-completion milestones) are the highest collection risk. A rising 90+ bucket signals a stuck project sign-off or an unhappy client that Finance must escalate before it becomes a write-off.
- **Target:** 90+ bucket < 5% of AR; overdue % < 15%; collections rate >= 95%.
- **Data source:** Sales Invoice (outstanding_amount, due_date, customer, docstatus=1), Payment Entry (allocated amounts). Both present after QBO sync. Stock ERPNext AR aging report exists but is not pre-snapshotted.
- **Implementation:** frappe.db.sql bucketing outstanding_amount by (today - due_date) on tabSales Invoice WHERE outstanding_amount>0. Daily snapshot; dashboard chart by bucket. Optionally auto-email a collections worklist (reuse the daily reminder cron pattern, e.g. customer_inactivity_reminder).
- **Refresh:** Daily snapshot; live on demand.

### 8. Days Payable Outstanding (DPO) & On-Time Payment Rate — 🟢 Auto
- **Definition:** DPO = (AP balance / total purchases in period) * days. On-time payment rate = count(Purchase Invoice paid on/before due_date) / count(Purchase Invoice paid). Also % paid early enough to capture vendor early-pay discounts.
- **Why it matters:** Pump, stainless, and stone vendors gate future material availability on payment behavior; missing terms can stall a build. Conversely paying too early wastes cash. Finance needs to balance vendor goodwill against cash preservation.
- **Target:** On-time payment rate >= 95%; DPO managed to terms (~30 days) without exceeding due dates; capture all >1% early-pay discounts.
- **Data source:** Purchase Invoice (outstanding_amount, due_date, posting_date), Payment Entry (reference to PI, posting_date), GL Entry (Account.account_type='Payable') for AP balance. Present after QBO sync; some bills also flow via Document Intake -> Purchase Invoice.
- **Implementation:** frappe.db.sql comparing Payment Entry posting_date vs PI due_date per paid bill for on-time %; AP balance for DPO. Snapshot daily. Early-pay-discount capture needs vendor terms in Supplier/Payment Terms (mostly present); flag where missed.
- **Refresh:** Daily snapshot; QBO/Intake refresh hourly.

### 9. Billing Accuracy & Unbilled Revenue (Milestone & Maintenance Leakage) — 🟡 Semi
- **Definition:** Two parts: (1) Unbilled completed work = value of completed Contract Milestones / substantially-complete projects with no matching submitted Sales Invoice; (2) Unbilled maintenance = submitted Sapphire Maintenance Records whose visit should have generated an invoice (per contract invoicing_frequency) but did not. Billing accuracy = invoiced amount / (invoiced + leakage).
- **Why it matters:** This is the biggest silent profit leak in a design-build-maintain shop: a finished install milestone that nobody invoiced, or a maintenance route visit that never converted to a Sales Invoice, is margin given away. Catching it weekly recovers real cash.
- **Target:** Unbilled completed work = $0 older than 7 days; maintenance billing leakage = 0 visits unbilled past one billing cycle.
- **Data source:** Project Contract -> Contract Milestone (completed but no SI), Project (substantial_completion_date) vs Sales Invoice (project), Sapphire Maintenance Record (docstatus=1) vs generated Sales Invoice per Sapphire Maintenance Contract.invoicing_frequency.
- **Implementation:** Maintenance leakage is Auto (Record.on_submit already creates the SI via process_maintenance_submission; query Records with no linked SI). Milestone leakage needs a 'billed' flag/date on Contract Milestone to mark which milestones have been invoiced (add 'invoiced_on' Date + 'sales_invoice' Link). Then a weekly exception report lists completed-but-unbilled. Auto-alert via cron (reuse escalate_overdue_steps pattern).
- **Refresh:** Weekly exception report + on-demand; alerts daily.

### 10. Budget vs Actual (Opex Variance by Cost Center / GL Category) — 🟡 Semi
- **Definition:** For each budgeted GL account/cost center: variance = actual (SUM GL Entry debit-credit for expense accounts in period) - budget. Variance % = variance / budget. Report MTD and YTD.
- **Why it matters:** Without a budget baseline, overspend on overhead (shop rent, vehicles, insurance, software) goes unnoticed until year-end. Variance reporting is core controller hygiene and the foundation for the cash forecast assumptions.
- **Target:** Each category within +/-5% of budget MTD; no category > +10% YTD without explanation.
- **Data source:** Actuals: GL Entry (expense accounts, posting_date, cost_center). Budget: NO budget doctype exists in the app today (confirmed gap). ERPNext has a stock Budget doctype that is not currently used/populated.
- **Implementation:** Actuals side is Auto from GL Entry. Enable and populate ERPNext's stock 'Budget' doctype (against Cost Center / GL account, per fiscal year) — this is the lightest path (no custom doctype, ERPNext computes variance natively in the Budget Variance Report). One-time annual budget entry by the controller; thereafter Auto variance. Snapshot monthly.
- **Refresh:** Monthly close; actuals live, budget entered annually/quarterly.

### 11. Payroll & Field-Labor Cost as % of Revenue (Labor Efficiency) — 🟢 Auto
- **Definition:** (Total labor cost in period / revenue in period). Labor cost = SUM(Timesheet Detail.amount) across design+build + Sapphire Maintenance Record.total_labor_cost for maintenance visits + payroll GL postings for non-billable staff. Split into billable vs non-billable and by segment.
- **Why it matters:** Labor is the largest controllable cost in design-build-maintain. Field-labor creep on builds and inefficient maintenance routing are the top margin killers. Tracking labor-to-revenue weekly catches overruns while a project is still in progress.
- **Target:** Total labor < 35% of revenue (set with owner); billable utilization of field crew > 75%.
- **Data source:** Timesheet Detail (hours, amount, project, activity_type), Sapphire Maintenance Record.total_labor_cost, Job Interval (clocked hours for utilization denominator), GL Entry payroll accounts for fully-loaded cost. Revenue from Sales Invoice.
- **Implementation:** frappe.db.sql summing Timesheet Detail.amount + Maintenance Record.total_labor_cost by period/project, divided by Sales Invoice revenue. Utilization = billable hours / Job Interval clocked hours. Snapshot weekly into Finance KPI Snapshot; chart by segment.
- **Refresh:** Weekly (aligns with payroll); live inputs.

### 12. Profitability by Customer / Maintenance Route (Contribution Margin) — 🟡 Semi
- **Definition:** Per customer (and per maintenance route/contract): contribution margin = lifetime/period revenue (Sales Invoice) - direct cost (Purchase Invoice + Timesheet + Maintenance Record labor+consumables + travel). Rank customers and maintenance contracts by margin and by margin %.
- **Why it matters:** Some maintenance contracts and rental clients are unprofitable once drive time, consumables, and travel are loaded in. Identifying the bottom decile lets Finance reprice or exit money-losing routes/contracts at renewal.
- **Target:** No active maintenance contract below 25% contribution margin; flag bottom-decile customers for repricing.
- **Data source:** Sales Invoice (customer), Purchase Invoice (project->customer), Timesheet Detail, Sapphire Maintenance Record (total_labor_cost, consumables child), Travel Trip (total_actual_cost by customer/project). Travel reports already exist (travel_spend_by_category, travel_trip_cost_summary).
- **Implementation:** Revenue+labor+consumable+travel join is Auto. Light gap: consistent customer/project linkage on every cost doc (some Purchase Invoices may lack a project). Enforce project on cost docs at entry, then a query report rolls cost-by-customer. Snapshot monthly; surface bottom-decile in a renewal worklist.
- **Refresh:** Monthly; live inputs.

### 13. QBO <-> ERPNext Sync Health & Reconciliation Status — 🟢 Auto
- **Definition:** Composite: sync success rate (QuickBooks Sync Log status='Completed'/total), open conflicts (Sync Mapping conflict_status='Conflict'), pending manual review (match_status='Pending Review'), last successful CDC sync age (now - settings.last_cdc_sync), and GL reconciliation deltas (ERPNext account balance vs QBO Reports API balance).
- **Why it matters:** Every other Finance KPI assumes ERPNext mirrors QBO accurately. A stalled CDC poll or a pile of unresolved conflicts means revenue, AR, and cash numbers are silently stale or wrong — and the shop has already been burned by sync retry-storms and the job/colon and PRJ-prefix bugs.
- **Target:** Sync success rate >= 99%; open conflicts = 0; last CDC sync age < 2 hours; zero unreconciled GL accounts at month-end.
- **Data source:** QuickBooks Sync Log (status, sync_type, counts), QuickBooks Sync Mapping (conflict_status, match_status, last_synced_at), QuickBooks Online Settings (last_cdc_sync, last_full_import), reconcile_transactions() GL-vs-QBO compare. quickbooks_sync_status MCP tool already exposes this.
- **Implementation:** frappe.db.sql/get_all on Sync Log + Sync Mapping for rates/queues; compute last_cdc_sync age from settings. Reuse existing dashboard fixtures (QuickBooks Sync Runs/Syncs by Status). Add a daily snapshot row + alert if CDC age > threshold or conflicts > 0 (cron, like retry_failed_syncs).
- **Refresh:** Hourly (matches CDC poll) + daily snapshot.

### 14. Stripe Payment -> Payment Entry Reconciliation & Double-Post Guard — 🟢 Auto
- **Definition:** Reconciliation rate = count(Stripe Payment status='Paid' WITH linked payment_entry) / count(Stripe Payment status='Paid'). Exceptions: Paid Stripe Payments with no Payment Entry (under-posted) and any Sales Invoice with both a Stripe-sourced PE and a separate QBO-imported PE (double-post risk). Plus failed/expired payment rate.
- **Why it matters:** Stripe finalize_payment() creates a Payment Entry that QBO may also import, risking a double-counted receipt that overstates cash and revenue. Unlinked paid sessions mean a customer paid but the invoice still shows open. Both corrupt AR and cash KPIs.
- **Target:** 100% of Paid Stripe Payments linked to exactly one Payment Entry; zero invoices with duplicate PEs; failed+expired payment rate < 5%.
- **Data source:** Stripe Payment (status, payment_entry, sales_invoice, stripe_checkout_session, amount, amount_refunded), Payment Entry (reference_no, qbo_payment_id writeback field), Stripe Event (process_status). stripe_payment_status MCP tool exposes summary.
- **Implementation:** frappe.db.sql: (a) Stripe Payment status='Paid' AND payment_entry IS NULL = under-posted queue; (b) GROUP BY sales_invoice HAVING COUNT(distinct Payment Entry)>1 = double-post queue. Daily snapshot + alert. Idempotency already enforced in reconcile.finalize_payment(); this KPI is the audit backstop.
- **Refresh:** Daily snapshot; Stripe webhooks real-time, hourly poll_pending backfill.

### 15. AP Document Intake Throughput & Posting Accuracy — 🟢 Auto
- **Definition:** (1) Review queue depth = count Document Intake status='Needs Review'; (2) extraction-to-posting accuracy = count status='Posted'/total, by document_type; (3) amount variance = Document Intake.grand_total vs created Purchase Invoice.grand_total; (4) avg time-to-posting = reviewed_on - received_on.
- **Why it matters:** Document-AI AP intake is how vendor bills become Purchase Invoices. A growing review backlog delays AP recognition (understating liabilities/DPO) and extraction errors that post wrong amounts directly misstate COGS and cash. This is the data-quality gate on every AP KPI.
- **Target:** Review queue < 10 items / cleared within 2 business days; extraction-to-posting accuracy > 90%; amount variance = $0 on posted docs.
- **Data source:** Document Intake (status, document_type, grand_total, net_total, received_on, reviewed_on, created_doctype, created_docname, error), Accounting Intake Log (per-step audit). document_intake_queue MCP tool exposes the queue.
- **Implementation:** frappe.get_all/SQL on Document Intake grouping by status/document_type; variance = join created Purchase Invoice.grand_total. Snapshot daily; alert when queue > threshold (reuse retry_failed_intakes/cron pattern).
- **Refresh:** Daily snapshot; intake hourly (watched folder) + on-upload.

### 16. Financial Audit Trail Integrity (GL Change & Approval Compliance) — 🟡 Semi
- **Definition:** Count of post-submission edits to financial documents (Version doctype entries on Sales Invoice / Purchase Invoice / Payment Entry / Journal Entry after docstatus=1), cancellations/amendments per period, and % of large journal entries lacking documented approval. Also count manual Journal Entries vs system-generated.
- **Why it matters:** For audit readiness and fraud prevention in a cash-heavy field business (technicians, travel advances, consumables), Finance must show GL changes are controlled and traceable. Unexplained JE volume or frequent invoice amendments are classic audit red flags.
- **Target:** Zero un-approved JEs over a $ threshold; cancellations/amendments < 2% of submitted financial docs; 100% of manual JEs carry a remarks/reason.
- **Data source:** ERPNext Version doctype (change history on financial doctypes), Journal Entry (is_system_generated, remarks, user), Sales/Purchase Invoice amended_from / docstatus=2. All native; no app-specific audit report built yet (confirmed gap).
- **Implementation:** Version-based change counts are Auto via SQL on tabVersion filtered to financial doctypes. Gap: 'approval' isn't structured for JEs. Add a lightweight approval (Workflow or a 'reviewed_by'+'review_reason' field) on Journal Entry above a threshold; then approval-compliance % is computable. Build an audit query report; snapshot monthly.
- **Refresh:** Monthly close + on-demand for audits.

**Data gaps:**
- No budget data: the app has no budget doctype and ERPNext's stock Budget doctype is not populated, so Budget-vs-Actual variance has no baseline until budgets are entered.
- No cash-flow forecast model: there is no forward-looking cash doctype; the 13-week forecast must be assembled from dated AR/AP plus assumptions, and Contract Milestone triggers are free-text (due_upon), not dates.
- No quoted/bid COST baseline per project: Project stores contract value (custom_project_dollar_amount) and estimated_costing, but no reliable bid-cost field, so true margin-vs-bid variance is partial.
- Milestone billing status is untracked: Contract Milestone has no 'invoiced_on'/sales_invoice link, so completed-but-unbilled milestone leakage cannot be detected automatically.
- Revenue segment is not standardized on Sales Invoices: design vs build vs maintenance vs rental cannot be cleanly split without a segment tag on Project/Invoice.
- Inconsistent project linkage on cost documents: some Purchase Invoices/Stock Entries lack a project link, weakening per-project and per-customer profitability rollups.
- No structured JE/large-transaction approval: audit-compliance % can't be measured without an approval flag or workflow on Journal Entries.
- Single-company assumption: no multi-entity consolidation, intercompany reconciliation, or FX revaluation (not currently needed but blocks those KPIs if the company expands).
- No sales-tax liability tracking: QBO TaxCode->Account mapping exists but no tax-provision/liability KPI is wired.
- No early-pay discount terms consistently on Suppliers, limiting the discount-capture portion of the DPO KPI.
- No Finance KPI Snapshot doctype yet: daily/weekly historical trending requires standing up a snapshot store (the Morning Briefing/Daily Briefing cron is the proven pattern to clone).

**Recommended minimal manual entry:**
- Annual/quarterly operating budget by GL account or Cost Center, entered once into ERPNext's stock Budget doctype (one-time per fiscal year by the controller) — unlocks Budget-vs-Actual variance.
- Bid/estimated COST per build project: add custom_bid_cost (Currency) on Project, populated at Closed-Won hand-off from the estimate — unlocks margin-vs-bid variance.
- expected_billing_date (Date) on each Contract Milestone, plus a small weekly-overhead assumptions table — unlocks the 13-week cash forecast.
- invoiced_on (Date) + sales_invoice (Link) on Contract Milestone, stamped when a milestone is billed — unlocks completed-but-unbilled leakage detection.
- Revenue segment tag: custom_revenue_segment (Select: Design/Build/Maintenance/Rental) on Project, defaulted by project_type and reviewed at hand-off — unlocks segment margin.
- JE review note/approval on Journal Entries above a $ threshold (reviewed_by + reason, or a light Workflow) — unlocks audit-compliance %.
- Vendor payment terms / early-pay discount on Supplier records where missing — completes the DPO discount-capture metric.

---
## Design (Water Engineering)

> Comprehensive KPI catalog for the Water Engineering / Design department of a fountain design-build-maintain business, covering the full job scope: design throughput & on-time delivery, revision/rework cycles, calc & spec accuracy and sourcing confidence, design hours vs estimate, hand-off completeness to production, control-panel and hydraulic design quality, and design backlog/WIP. Grounded in the actual erpnext_enhancements water_engineering doctypes: Water Feature Design (WFD, submittable, track_changes=1, status Draft->Inputs Gathered->Calculated->Reviewed->Issued, completion_percent, has_warnings, next_inputs_needed, child calc_results with status OK/Warning/Error + citations, amended_from for revisions), Control Panel Design (CPD: theory_of_operation, power_source_confirmation, io_points/interlocks/fuses), and Nozzle Profile (is_generic_estimate sourcing flag, cut_sheet). 16 KPIs: 9 fully Auto from existing fields + Version history, 6 Semi-Auto needing one or two light new fields (hours estimate, due date, rework reason, sign-off, hand-off check), 1 Manual (production-feedback design-defect escape). The biggest leverage is that WFD already carries status, completion %, warnings, a citation-bearing calc audit trail, and amended_from, so throughput, accuracy/sourcing, and revision counts are computable today with SQL + a daily snapshot doctype modeled on the existing Daily Briefing pattern. The main gap is the absence of an estimated design-hours field and an explicit design due date on WFD, plus no structured link from WFD to the contract milestone it fulfills.

### 1. Design Throughput (Designs Issued per Week) — 🟢 Auto
- **Definition:** Count of Water Feature Design docs that entered status='Issued' within the period, by week. Issued = the design is released to production/customer. Tracked off the status-change timestamp from Version history (status field on WFD with track_changes=1) or, more cheaply, off docstatus=1 submit date (WFD is_submittable).
- **Why it matters:** The core productivity signal for the design team — how many fountain designs actually cross the finish line per week. Drives capacity planning, hand-off forecasting to the build crew, and reveals whether design is the bottleneck in the closed-won -> design -> build pipeline.
- **Target:** Set with engineering lead from trailing 8-week baseline (e.g. 4-6 issued/week); flag any week below 60% of trailing average.
- **Data source:** Water Feature Design.status (transition to 'Issued') via tabVersion JSON (track_changes=1); fallback Water Feature Design.modified WHERE docstatus=1. Doctype: Water Feature Design.
- **Implementation:** Daily/weekly cron (hooks.py scheduler_events, mirror the Daily Briefing pattern) running SQL: parse tabVersion rows for `tabWater Feature Design` where data JSON shows status changed to 'Issued', GROUP BY YEARWEEK(creation of that version row). Cheaper proxy: SELECT YEARWEEK(modified), COUNT(*) FROM `tabWater Feature Design` WHERE docstatus=1 GROUP BY 1. Snapshot into a new 'Design KPI Snapshot' doctype for trend retention.
- **Refresh:** Daily snapshot, weekly rollup

### 2. On-Time Design Delivery % — 🟡 Semi
- **Definition:** (# WFDs Issued on or before their committed due date) / (# WFDs Issued in period) x 100. Requires a per-design committed due date.
- **Why it matters:** Late designs cascade directly into build delays, crew idle time, and contract milestone slippage (Contract Milestone 'Upon Design Completion' fees). On-time % is the single most customer-visible design metric.
- **Target:** 90% on-time; escalate any design >5 business days past due.
- **Data source:** Water Feature Design Issued date (Version/submit) vs a new due-date field. Could derive due date from linked Project Contract design timeline (concept_days + design_development_days + construction_documents_days) or Project Process Step 'Design Complete' due_by.
- **Implementation:** Add custom field `design_due_date` (Date) to Water Feature Design, auto-populated on create from the linked Project Contract design-phase days or the Project Process Step 'Design Complete' SLA due_by when project is set. Then SQL compares issued date to design_due_date. If a WFD->Contract link is added (see Hand-Off KPI), this becomes fully Auto.
- **Refresh:** Daily

### 3. Design Cycle Time (Draft -> Issued) — 🟢 Auto
- **Definition:** Median and 90th-percentile calendar days from WFD creation (or status='Draft') to status='Issued', for designs issued in the period. Segment by fountain_type (a grand cascade is not a bubbler).
- **Why it matters:** Cycle time is the lead-time customers and the sales team quote against. Trending it by fountain_type exposes which feature types (e.g. Tiered/Grand Cascade) systematically over-run and need template or staffing changes.
- **Target:** Set per fountain_type band from baseline; e.g. simple basin <5 days, multi-tier cascade <15 days. Alert on >1.5x the type median.
- **Data source:** Water Feature Design.creation and the Issued status-change timestamp (tabVersion); segment by Water Feature Design.fountain_type. Doctype: Water Feature Design.
- **Implementation:** SQL/cron: for each WFD issued in period, compute DATEDIFF(issued_version_ts, creation); aggregate median/p90 GROUP BY fountain_type. Store in Design KPI Snapshot. No new fields needed.
- **Refresh:** Weekly

### 4. Design Stage Dwell Time (per status) — 🟢 Auto
- **Definition:** Average days a design spends in each status (Draft, Inputs Gathered, Calculated, Reviewed) before advancing, from consecutive status-change timestamps in Version history.
- **Why it matters:** Pinpoints WHERE designs stall — long dwell in 'Inputs Gathered' means missing customer/site data (a sales hand-off problem), while long dwell in 'Reviewed' means a review bottleneck. Tells the lead exactly which gate to fix.
- **Target:** No stage >3 business days median; 'Reviewed' should clear within 2 days.
- **Data source:** Water Feature Design.status transitions in tabVersion (track_changes=1). Doctype: Water Feature Design + tabVersion.
- **Implementation:** Cron parses ordered status-change events per WFD from tabVersion, diffs consecutive timestamps, buckets by from-status, averages across designs. Reuses the same Version-parsing helper as Throughput. Store per-status averages in Design KPI Snapshot.
- **Refresh:** Weekly

### 5. Design Backlog / WIP Count & Age — 🟢 Auto
- **Definition:** Count of WFDs currently in a non-terminal status (docstatus=0 AND status in Draft/Inputs Gathered/Calculated/Reviewed), plus the age distribution (days since creation) and count aged >14 days.
- **Why it matters:** Live measure of work-in-progress load on the design team and early-warning on aging designs that block downstream build. A growing backlog with rising age = design is under-staffed or stuck on inputs.
- **Target:** WIP within team capacity (set ceiling, e.g. <=2 active designs per engineer); zero designs aged >21 days without a flag/reason.
- **Data source:** Water Feature Design.status, docstatus, creation. Doctype: Water Feature Design.
- **Implementation:** Real-time list/Number Card + daily snapshot: SELECT status, COUNT(*), AVG(DATEDIFF(NOW(),creation)) FROM `tabWater Feature Design` WHERE docstatus=0 AND status!='Issued' GROUP BY status. Add a Dashboard Chart (Group By status) and Number Cards (Active Designs, Designs Aged >14d) via fixtures/dashboard_chart.json + number_card.json. No new fields.
- **Refresh:** Real-time card + daily snapshot

### 6. Revision/Rework Rate (Amended Designs %) — 🟢 Auto
- **Definition:** (# WFDs that have at least one amendment, i.e. another WFD with amended_from pointing to them, OR were re-issued after Issued) / (# WFDs issued in period) x 100. Also report avg amendments per design.
- **Why it matters:** Rework is pure waste — every amended design means the first issue was wrong or the inputs changed late. High amendment rate signals weak input-gathering, premature issue, or scope churn, and directly inflates design cost.
- **Target:** <15% of issued designs amended; <0.3 amendments/design avg.
- **Data source:** Water Feature Design.amended_from (self-link, already exists; amend chain on submittable doctype). Doctype: Water Feature Design.
- **Implementation:** SQL: COUNT(DISTINCT amended_from) / COUNT(issued) and COUNT(*) WHERE amended_from IS NOT NULL grouped by period. amended_from is populated automatically by Frappe's amend workflow — no new field. Snapshot to Design KPI Snapshot.
- **Refresh:** Weekly

### 7. Rework Root-Cause Mix — 🟡 Semi
- **Definition:** Distribution of amended/re-issued designs by reason category (e.g. Customer scope change, Site dimensions wrong, Calc error, Pump/nozzle unavailable, Code/permit). Requires capturing a reason on amendment.
- **Why it matters:** The rework RATE tells you there's waste; the root-cause MIX tells you how to kill it. If 60% of rework is 'site dimensions wrong', the fix is a better site-survey hand-off from sales, not more design QA.
- **Target:** Drive internally-caused categories (calc error, spec error) toward 0; externally-caused (customer scope) tracked but not penalized.
- **Data source:** Water Feature Design (amended docs). Needs a new field. Doctype: Water Feature Design.
- **Implementation:** Add custom Select field `revision_reason` (options: Customer Scope Change / Site Data Correction / Calc Error / Component Unavailable / Code or Permit / Other) shown only when amended_from is set (depends_on). Engineer picks one on amend (one click). Cron GROUP BY revision_reason. Lightest possible manual input layered on the auto amend-detection.
- **Refresh:** Weekly

### 8. Calc Accuracy / Clean-Issue Rate — 🟢 Auto
- **Definition:** % of designs issued with zero unresolved calc warnings/errors = (# Issued WFDs where has_warnings=0 AND no calc_results row has status in ('Warning','Error')) / (# Issued WFDs) x 100.
- **Why it matters:** A design issued with live warnings is a latent field failure — wrong pump head, under-circulated basin, drain undersized. Clean-issue rate is the design-quality gate that protects the build crew and warranty exposure.
- **Target:** >=95% of issued designs clean; zero designs issued with a calc_results status='Error'.
- **Data source:** Water Feature Design.has_warnings + child Water Feature Calc Result.status (OK/Warning/Error). Doctypes: Water Feature Design, Water Feature Calc Result.
- **Implementation:** SQL: among WFDs issued in period, COUNT where has_warnings=0 AND NOT EXISTS (SELECT 1 FROM `tabWater Feature Calc Result` WHERE parent=wfd.name AND status IN ('Warning','Error')) / total. Both flags already computed by the engine on recompute(). Also surface a real-time Number Card 'Issued Designs With Open Warnings'.
- **Refresh:** Daily

### 9. Sourcing Confidence (Verified-Spec Coverage) — 🟢 Auto
- **Definition:** % of nozzle/feature selections on issued designs that reference a manufacturer-sourced spec rather than a generic estimate = 1 - (# feature rows whose Nozzle Profile.is_generic_estimate=1) / (total feature rows with a nozzle_profile) on issued WFDs. Complementary: % of used Nozzle Profiles that have a cut_sheet attached.
- **Why it matters:** Fountain hydraulics live or die on real discharge coefficients. A design built on generic-estimate Cd values can mis-size the pump by 20-40%. This KPI quantifies how much of the issued work rests on unverified placeholder data — a direct field-risk and credibility metric.
- **Target:** >=85% verified-spec coverage on issued designs; 100% on grand-cascade / high-value features; drive generic-estimate usage down over time.
- **Data source:** Water Feature Nozzle.nozzle_profile -> Nozzle Profile.is_generic_estimate and Nozzle Profile.cut_sheet (both exist). Doctypes: Water Feature Nozzle (child of WFD), Nozzle Profile.
- **Implementation:** SQL join: for issued WFDs, JOIN `tabWater Feature Nozzle` -> `tabNozzle Profile`; COUNT(is_generic_estimate=0)/COUNT(*) and COUNT(cut_sheet IS NOT NULL)/COUNT(*). No new fields. Surface as a per-design badge (the engine already flags unsourced nozzles) and a department-level snapshot.
- **Refresh:** Weekly

### 10. Design Hours vs Estimate (Effort Variance) — 🟡 Semi
- **Definition:** For each issued design, (actual design hours logged - estimated hours) / estimated hours. Department metric = avg variance and % of designs over-budget by >20%.
- **Why it matters:** Design labor is a real cost on fixed-fee design contracts (Project Contract total_design_fee). Without effort variance the team can't tell which fountain types are priced wrong or which engineers need support. It's the margin-protection KPI for the design phase.
- **Target:** Avg effort variance within +/-15%; <20% of designs over by >20%.
- **Data source:** Actual: Timesheet Detail (hours, amount) filtered to the WFD's project and design activity_type. Estimate: no field exists today. Doctypes: Timesheet Detail (actual), Water Feature Design (needs estimate field).
- **Implementation:** Actuals are Auto: SUM(hours) FROM `tabTimesheet Detail` WHERE project=wfd.project AND activity_type in design types. Add custom field `estimated_design_hours` (Float) on Water Feature Design (or fetch from a per-fountain_type standard-hours table) — one number entered at design kickoff. Also recommend a dedicated 'Design' activity_type so timesheet hours can be isolated from build hours. Then variance = actual/estimate-1.
- **Refresh:** Weekly

### 11. Design Completeness at Issue — 🟢 Auto
- **Definition:** Average Water Feature Design.completion_percent at the moment of Issue, and % of issued designs with completion_percent < 100. completion_percent = (basins + features + piping + pump filled)/4.
- **Why it matters:** Designs issued below 100% completeness mean a missing basin, no piping model, or no selected pump went to production — a guaranteed RFI or build stoppage. This catches premature issue before it reaches the field.
- **Target:** 100% of issued designs at completion_percent=100; zero issues below 90%.
- **Data source:** Water Feature Design.completion_percent (already computed read-only field). Doctype: Water Feature Design.
- **Implementation:** SQL: AVG(completion_percent) and COUNT(completion_percent<100) among issued WFDs. Add a submit-time validation/Number Card to hard-block or flag Issue below threshold. No new fields.
- **Refresh:** Daily

### 12. Hand-Off Completeness to Production — 🟡 Semi
- **Definition:** % of issued designs whose downstream artifacts are all present: a selected_pump set, a linked Control Panel Design exists, and (for the project) a Project Process Step 'Design Complete' marked Completed. Composite 0-100% per design, averaged.
- **Why it matters:** The hand-off is where design value is lost — a perfect hydraulic model with no control panel design or no pump selection forces the build crew to back-fill engineering. This measures whether production gets a complete package, not just a drawing.
- **Target:** >=95% complete hand-off packages; zero issued designs with no selected_pump.
- **Data source:** Water Feature Design.selected_pump; Control Panel Design.water_feature_design (link); Project Process Step.step_title='Design Complete'/status. Doctypes: Water Feature Design, Control Panel Design, Project Process Step.
- **Implementation:** Mostly Auto via joins (selected_pump NOT NULL; EXISTS Control Panel Design WHERE water_feature_design=wfd.name; Process Step Completed). The one light addition: a custom Check `handoff_package_verified` on WFD that the engineer ticks at issue to confirm drawings/BOM attached, OR add a small 'Design Hand-Off Checklist' child table. Compute composite per design and average. Add WFD->Project Contract link to also verify the design fulfills the contracted scope.
- **Refresh:** Daily

### 13. Control-Panel Design Quality / Completeness — 🟢 Auto
- **Definition:** % of Control Panel Designs that are 'complete': theory_of_operation non-empty, power_source_confirmation filled, >=1 io_points row, >=1 interlocks row, and fuses schedule present. Composite completeness score per CPD, averaged.
- **Why it matters:** The control panel is the highest-liability deliverable (DOC-0127 requires theory_of_operation before coding; safety interlocks prevent pump dry-run and flooding). An incomplete CPD is a programming and safety risk. This is the electrical-design analog to hydraulic clean-issue.
- **Target:** 100% of CPDs have theory_of_operation + power_source_confirmation + interlocks before the panel is built; flag any with 0 interlocks.
- **Data source:** Control Panel Design.theory_of_operation, .power_source_confirmation, child tables io_points / interlocks / fuses / lights. Doctypes: Control Panel Design + its child tables.
- **Implementation:** SQL per CPD: score = (theory_of_operation!='' ) + (power_source_confirmation!='') + (EXISTS io_points) + (EXISTS interlocks) + (EXISTS fuses), normalized to %. AVG across CPDs in period and COUNT of CPDs failing any mandatory check. All fields exist. Add a Number Card 'CPDs Missing Theory of Operation' and 'CPDs With No Interlocks'.
- **Refresh:** Daily

### 14. Hydraulic Headroom / Pump Selection Margin — 🟢 Auto
- **Definition:** For issued designs with a selected pump, the margin between the pump's rated duty and the computed requirement: (selected pump rated_gpm - design_flow_gpm)/design_flow_gpm and rated TDH vs computed_tdh_ft. Report % of designs with margin outside a healthy band (e.g. <5% under-sized or >40% over-sized).
- **Why it matters:** Under-margin pumps fail to throw the designed display; grossly over-margin pumps waste capital and energy and can over-pressure nozzles. This is a design-quality KPI specific to fountain hydraulics that catches both failure modes before procurement.
- **Target:** Pump duty 10-30% above design_flow_gpm and at/above computed_tdh_ft for >=90% of issued designs; zero under-sized selections.
- **Data source:** Water Feature Design.design_flow_gpm, .computed_tdh_ft, .selected_pump; pump rating via Water Feature Pump.rated_gpm/rated_tdh_ft (is_selected) or Item pump curve (Pump Curve Point). Doctypes: Water Feature Design, Water Feature Pump.
- **Implementation:** SQL: join issued WFD to its selected Water Feature Pump row (is_selected=1), compute (rated_gpm-design_flow_gpm)/design_flow_gpm and rated_tdh_ft vs computed_tdh_ft; bucket into under/healthy/over. All values already produced by the calc engine. Surface per-design warning + department distribution.
- **Refresh:** Weekly

### 15. Design Review Sign-Off Rate (First-Pass Yield) — 🟢 Auto
- **Definition:** % of designs that advanced Calculated -> Reviewed -> Issued without ever moving backward (e.g. Reviewed -> Calculated/Inputs Gathered), measured from status-transition history. First-pass yield = designs with a monotonic forward path / total issued.
- **Why it matters:** Backward status moves mean the review caught problems requiring rework before issue — good that review caught it, but a low first-pass yield means designs reach review half-baked, wasting reviewer time. Distinguishes 'review is working' from 'design quality upstream is poor'.
- **Target:** >=80% first-pass yield (forward-only path); trend reviewer rejection reasons alongside Rework Root-Cause Mix.
- **Data source:** Water Feature Design.status transition sequence in tabVersion (track_changes=1). Doctype: Water Feature Design + tabVersion.
- **Implementation:** Cron parses the ordered status sequence per WFD from tabVersion; flag any design whose sequence contains a regression (a later status earlier in the enum after a higher one). Yield = clean-forward / total issued. Reuses the Version-parsing helper. For an explicit reviewer identity/sign-off date, optionally add a `reviewed_by`/`reviewed_on` field (then Semi-Auto), but core yield is Auto.
- **Refresh:** Weekly

### 16. Design Defect Escape Rate (Field/Build Feedback) — 🔴 Manual
- **Definition:** # of design-caused issues discovered AFTER issue (during build or commissioning or first maintenance season) per issued design — e.g. wrong pump, basin overflow, nozzle pattern wrong, panel logic error. = design-attributed defects / designs issued in the cohort.
- **Why it matters:** The ultimate quality outcome: did the design actually work in the field? Internal clean-issue and sourcing KPIs are leading indicators; escape rate is the lagging truth. It closes the design-build-maintain loop and justifies investment in better input-gathering and verified specs.
- **Target:** <0.1 design-attributed defects per issued design; zero safety-related panel escapes.
- **Data source:** No structured capture today. Closest signals: Project Process Step notes, Sapphire Maintenance Record warranty_rma_flag / has_out_of_range_readings on the same serial_no/project, build-phase RFIs. Doctypes: none dedicated (gap).
- **Implementation:** Add a lightweight 'Design Feedback' capture: either a new child table on Water Feature Design ('Design Issue' with fields defect_type, phase_found [Build/Commissioning/Maintenance], attributable [Design/Build/Customer], description) or a single Select+link record the PM/build lead files when a design-caused problem is found. Minimal: 1 record per escape. Then cron counts attributable='Design' escapes per issued cohort. Partial automation possible by flagging Maintenance Records with warranty_rma_flag=1 whose project links to a recently-issued WFD for review.
- **Refresh:** Monthly cohort review

**Data gaps:**
- No estimated design-hours field on Water Feature Design — effort variance (actual vs estimate) cannot be computed until a per-design estimate (or per-fountain_type standard-hours table) is captured.
- No explicit design due date on Water Feature Design — on-time delivery must derive a due date from the linked Project Contract design-phase days or Project Process Step SLA, or have one entered.
- No structured link from Water Feature Design to the Project Contract / Contract Milestone it fulfills — so 'does the issued design match the contracted scope?' (design-to-contract traceability) is manual; adding custom_project_contract on WFD would make Hand-Off and On-Time fully Auto.
- No dedicated 'Design' Timesheet activity_type — design hours are commingled with build hours under the same project, so isolating design labor for effort-variance needs an activity_type convention or a flag.
- No design-defect/field-feedback capture — design quality outcomes (defects discovered in build/commissioning/maintenance) are not recorded against the originating Water Feature Design, so escape rate is currently unmeasurable without a new capture.
- No structured revision-reason on amendments — amended_from detects THAT a revision happened (Auto) but not WHY, so root-cause mix needs one new Select field.
- No persistent design-KPI snapshot store — status-transition metrics are computable from tabVersion live, but trend retention needs a 'Design KPI Snapshot' doctype (modeled on Daily Briefing) since Version rows can be pruned and ad-hoc parsing is expensive.
- completion_percent treats all four design components equally (basin/features/piping/pump) and does not weight by fountain_type — a splash pad with no basin may never reach 100%, so completeness thresholds should be type-aware.
- No reviewer identity/sign-off timestamp field — first-pass yield is derivable from status regressions, but attributing reviews to a specific reviewer (reviewer workload, reviewer-specific catch rate) needs reviewed_by/reviewed_on fields.

**Recommended minimal manual entry:**
- estimated_design_hours (Float) on Water Feature Design — one number at design kickoff (or a small per-fountain_type standard-hours master) to unlock effort variance.
- revision_reason (Select: Customer Scope Change / Site Data Correction / Calc Error / Component Unavailable / Code or Permit / Other) shown only when amended_from is set — one click on amend to unlock rework root-cause mix.
- design_due_date (Date) on Water Feature Design — auto-defaulted from contract/process-step where possible, manually adjustable; unlocks on-time delivery %.
- handoff_package_verified (Check) on Water Feature Design — engineer ticks at issue to confirm drawings/BOM/control-panel package complete; unlocks hand-off completeness.
- A 'Design Issue' capture (child table on WFD or one record per escape) with defect_type, phase_found, attributable, description — filed by PM/build lead when a design-caused problem surfaces in build/commissioning/maintenance; unlocks design defect escape rate.
- Adopt a dedicated 'Design' Timesheet activity_type so design labor can be separated from build labor for effort variance.

---
## Sales

> A 17-KPI catalog covering the full Sales job scope for Sapphire Fountains' design-build-maintain model: lead response and conversion, pipeline value/coverage, win rate, sales-cycle and quote-to-close speed, forecast accuracy, average deal size, rep productivity (including AI-logged call/SMS activity), Closed-Won hand-off speed and quality, and maintenance-contract renewal/churn (the recurring-revenue tail unique to a fountain operator). 11 KPIs are Auto (computable today from Opportunity/Lead/Call Log/Communication/Project Contract/Maintenance Contract doctypes via SQL + a 06:30 cron snapshot modeled on the existing Daily Briefing), 4 are Semi-Auto (need one lightweight new field such as a quote-sent timestamp, a forecast category, or a won/lost reason), and 2 are Manual (CAC-by-channel and win/loss reason, which depend on un-integrated ad-spend and human judgment). Stage-change timestamps are already stamped by a before_save hook, Closed-Won hand-off backlog is already a wired metric, and Triton telephony already logs every call/SMS with sentiment — so rep activity and response-time KPIs need no new plumbing. Primary gaps: no quote/Quotation-Sent timestamp distinct from status, no structured forecast commit, no won/lost reason, and no ad-spend integration for true CAC.

### 1. Lead Response Time (Speed-to-Lead) — 🟢 Auto
- **Definition:** Median elapsed time from Lead.creation (or first inbound Call Log / SMS Communication for that party) to the first outbound contact attempt (first outbound Call Log or sent SMS/email Communication, or Lead.status moving to 'Replied'). Reported as median minutes and % of leads contacted within 1 business hour.
- **Why it matters:** Fountain inquiries are high-consideration, high-ticket purchases; the vendor who calls back first usually wins the design conversation. Response speed is the single most controllable conversion lever for inbound web/GA4 leads.
- **Target:** Median < 30 min during business hours; >= 80% of leads contacted within 1 business hour
- **Data source:** tabLead (name, creation, status, lead_owner); tabCall Log (id, type='Outbound', start_time, linked party via timeline_links); tabCommunication (communication_medium SMS/Email, sent_or_received='Sent', communication_date, timeline_links to Lead). Triton telephony logs calls/SMS in real time.
- **Implementation:** Cron-snapshot KPI (06:30 daily, modeled on api/briefing.py scheduled run). Query: for each Lead created in window, find MIN(start_time) of outbound Call Log + MIN(communication_date) of Sent Communication joined via timeline_links/Dynamic Link; first_contact = least of those; response_minutes = first_contact - lead.creation, clamped to business hours. Aggregate median + %<60min. Store in a new 'Sales KPI Snapshot' doctype (clone of Daily Briefing pattern).
- **Refresh:** Daily snapshot 06:30; live drill-down on demand

### 2. Lead-to-Opportunity Conversion Rate — 🟢 Auto
- **Definition:** (# Leads reaching status 'Converted' OR linked to an Opportunity in the period) / (# Leads created in the period), segmented by lead source.
- **Why it matters:** Separates lead quality from sales effort and tells marketing which channels (organic GSC, GA4 paid, referral) produce real fountain projects versus tire-kickers, so spend is steered to the channels that actually book design meetings.
- **Target:** Overall >= 25%; organic/referral >= 35%
- **Data source:** tabLead (name, status, source, custom_lead_sources child); Opportunity created from Lead (party linkage). lead_source doctype for channel attribution.
- **Implementation:** SQL GROUP BY Lead.source: COUNT(status='Converted' OR exists Opportunity where party_name=lead) / COUNT(*) over the date window. Add to the daily Sales KPI Snapshot. No new fields — Lead.status and source already populated.
- **Refresh:** Daily snapshot; monthly trend

### 3. Open Pipeline Value — 🟢 Auto
- **Definition:** SUM(Opportunity.opportunity_amount) where status IN (Inquiry, Quotation Sent, Negotiation) i.e. all open/non-closed stages. Reported total and broken out by stage and by opportunity_owner.
- **Why it matters:** The headline measure of future revenue in motion. For a design-build shop with long cycles, knowing the dollar value sitting in each stage is the basis for capacity, cash-flow, and hiring decisions.
- **Target:** Maintain >= 3x trailing-quarter booked revenue (see Pipeline Coverage)
- **Data source:** tabOpportunity (status, opportunity_amount [aliased to custom_project_dollar_amount in crm_enhancements/api.py], opportunity_owner). Already surfaced in crm_enhancements/page/sales_pipeline.
- **Implementation:** Reuse existing sales_pipeline.py aggregation (it already sums opportunity_amount per status column). Expose as Number Card 'Open Pipeline Value' via fixtures/number_card.json (Sum function, filter status in open stages) — pattern already exists for 'Open Pipeline Value' card. No new data.
- **Refresh:** Realtime (chart computed at view time)

### 4. Pipeline Coverage Ratio — 🟡 Semi
- **Definition:** Open Pipeline Value (weighted or raw) / Sales Target for the period. Computed per quarter against a configured booking target.
- **Why it matters:** Tells you whether there is enough pipeline to hit the number BEFORE the quarter ends. A ratio under ~3x for long-cycle build work signals a future revenue hole while there's still time to generate demand.
- **Target:** 3.0x - 4.0x of remaining-period booking target
- **Data source:** Open Pipeline Value (Opportunity) / new target value. Target stored in Sales Activity Settings (new field) or a small 'Sales Target' doctype keyed by period/owner.
- **Implementation:** Add a 'quarterly_booking_target' Currency field (and optional per-owner child table) to Sales Activity Settings doctype. KPI = open pipeline SUM / target. Everything except the target number is already automated; the target is a once-a-quarter manual entry by the sales lead.
- **Refresh:** Daily snapshot; target set quarterly

### 5. Win Rate (Closed-Won %) — 🟢 Auto
- **Definition:** # Opportunities reaching status 'Closed Won' / (# Closed Won + # Closed Lost) over the period. Segment by opportunity_owner and by deal-size band.
- **Why it matters:** Core efficiency metric — how good the team is at converting qualified pipeline into signed fountain projects. Drives coaching, qualification discipline, and realistic forecasting.
- **Target:** Overall >= 30%; trend upward QoQ
- **Data source:** tabOpportunity (status='Closed Won' vs 'Closed Lost', custom_date_closed_won, opportunity_owner).
- **Implementation:** SQL: COUNT(status='Closed Won' AND custom_date_closed_won in window) / COUNT(status IN ('Closed Won','Closed Lost') closed in window), GROUP BY opportunity_owner. Add Number Card + Group By chart to fixtures. Closed-Won status and win date already stamped.
- **Refresh:** Daily snapshot; monthly review

### 6. Average Sales Cycle Time — 🟢 Auto
- **Definition:** Median days from Opportunity.creation to custom_date_closed_won for deals won in the period. Optionally segmented by deal-size band and by product family (build vs. rental vs. maintenance).
- **Why it matters:** Long, variable cycles are normal for custom fountains; measuring the median (and outliers) exposes where deals stall and feeds cash-flow timing and forecast realism.
- **Target:** Establish baseline first quarter, then reduce median by 10% YoY; flag deals exceeding 2x median
- **Data source:** tabOpportunity (creation, custom_date_closed_won, opportunity_amount, status='Closed Won').
- **Implementation:** SQL: MEDIAN(DATEDIFF(custom_date_closed_won, creation)) for won opps in window. Frappe lacks SQL median, so compute in the cron job (sort list, pick midpoint) and store in Sales KPI Snapshot. No new fields.
- **Refresh:** Daily snapshot; quarterly trend

### 7. Stage Velocity / Stage Aging (Stalled-Deal Detection) — 🟢 Auto
- **Definition:** Per open Opportunity: days_in_current_stage = today - custom_stage_changed_on. KPI reports avg days-in-stage per stage and the count + dollar value of opportunities exceeding the inactivity threshold ('stalled').
- **Why it matters:** Pinpoints exactly where deals rot (e.g. quotes sent but no follow-up). For long fountain cycles, surfacing the stalled-and-dollar-heavy deals is where a sales manager's attention pays off most.
- **Target:** 0 open opps stalled beyond the configured threshold with no logged activity in 14 days
- **Data source:** tabOpportunity (custom_stage_changed_on stamped by before_save hook, status, opportunity_amount); Sales Activity Settings.inactivity_threshold. Already implemented in sales_pipeline.py staleness (amber/red).
- **Implementation:** Already computed by _stale_level() in sales_pipeline.py using custom_stage_changed_on. Promote the stalled count + stalled $ into the daily snapshot and a Number Card. Cross-reference last Call Log/SMS date via timeline_links to confirm 'no activity'.
- **Refresh:** Realtime board; daily snapshot of stalled count

### 8. Quote-to-Close Rate & Time — 🟡 Semi
- **Definition:** Quote-to-Close Rate = # Opportunities won / # Opportunities that reached 'Quotation Sent' (or have a linked Project Contract sent for signature). Quote-to-Close Time = median days from quote-sent timestamp to custom_date_closed_won.
- **Why it matters:** Isolates closing effectiveness after a proposal/contract is on the table from earlier-funnel qualification. For a contract-driven build business, the gap between 'Out for Signature' and 'Signed' is a direct cash-timing and follow-up signal.
- **Target:** Quote-to-close rate >= 50%; median signature time < 21 days
- **Data source:** tabOpportunity status history; tabProject Contract (status 'Out for Signature' -> 'Signed', signed_on). Needs an explicit quote-sent timestamp on Opportunity.
- **Implementation:** Project Contract.signed_on already exists, so contract-signature timing is Auto for the build/design contract path. For Opportunity-level quote timing, add 'custom_quotation_sent_on' Datetime stamped by the same before_save hook when status first becomes 'Quotation Sent' (one-line addition to the existing stage-stamp logic in script_migrations/opportunity.py / process_steps hook). Then rate = won/quoted, time = signed_on - quotation_sent_on.
- **Refresh:** Daily snapshot

### 9. Average Deal Size (Average Contract Value) — 🟢 Auto
- **Definition:** Mean opportunity_amount of Closed-Won opportunities in the period; also reported as median and by product family (build, rental, maintenance). Optionally validated against signed Project Contract milestones_total.
- **Why it matters:** Tracks whether the team is winning bigger custom builds or drifting toward small jobs; underpins forecast math (coverage, quota) and tells you the revenue mix between one-time builds and recurring maintenance.
- **Target:** Hold or grow vs. trailing 12-month average; set with sales lead
- **Data source:** tabOpportunity (opportunity_amount, status='Closed Won', custom_date_closed_won); cross-check tabProject Contract.milestones_total / custom_project_dollar_amount on the created Project.
- **Implementation:** SQL AVG/MEDIAN(opportunity_amount) WHERE status='Closed Won' AND custom_date_closed_won in window. Number Card (Average function) in fixtures/number_card.json. No new fields.
- **Refresh:** Daily snapshot; monthly trend

### 10. Forecast Accuracy — 🟡 Semi
- **Definition:** 1 - |Forecasted booked revenue - Actual booked revenue| / Actual, per period. Forecast = SUM(opportunity_amount x probability) for opps tagged Commit/Best-Case at period start; Actual = SUM(opportunity_amount) of opps Closed-Won in the period.
- **Why it matters:** Lets leadership trust the pipeline number for cash, staffing, and material-procurement planning. In a build business, an over-optimistic forecast means idle crews or scrambled procurement.
- **Target:** Forecast within +/-15% of actual
- **Data source:** tabOpportunity (opportunity_amount, custom_date_closed_won). Needs a forecast category + probability captured per open opp, snapshotted at period start.
- **Implementation:** Add 'custom_forecast_category' Select (Commit/Best Case/Pipeline/Omitted) to Opportunity (Frappe has a native 'probability' field already on Opportunity). Snapshot the weighted forecast at the start of each month into Sales KPI Snapshot (the cron already runs); at month end compare against actual Closed-Won sum. Light manual input = reps setting the category; math is automated.
- **Refresh:** Snapshot at period start + period-end reconciliation

### 11. Sales Activity Volume (Calls / SMS / Meetings per Rep) — 🟢 Auto
- **Definition:** Per opportunity_owner per week: count of outbound Call Log records + sent SMS Communications + logged Events/meetings. Reported as totals and as activity-per-open-opportunity.
- **Why it matters:** Leading indicator that predicts future pipeline and wins; for a team where Triton already auto-logs every call and text, this is free coverage data that flags reps going quiet on hot deals.
- **Target:** >= 20 meaningful touches per rep per week; every open opp touched within 14 days
- **Data source:** tabCall Log (type='Outbound', start_time, owner/agent); tabCommunication (SMS/Email Sent, communication_date); tabEvent (meetings) — all auto-logged by Triton telephony (api/telephony.py) and SMS gateway.
- **Implementation:** SQL COUNT per owner per week across Call Log + Communication (+ Event), joined to opportunity_owner via timeline_links/party. Add to daily snapshot and a 'Calls by Direction' style chart (Call Center dashboard pattern already exists). Zero new entry — telephony is fully wired.
- **Refresh:** Realtime ingest; weekly rollup

### 12. Rep Productivity (Revenue & Win Yield per Owner) — 🟢 Auto
- **Definition:** Per opportunity_owner: Closed-Won revenue, win count, win rate, avg deal size, and activity-to-win ratio (touches per win). Includes shared-credit view via Opportunity Contributors.
- **Why it matters:** Single scorecard for coaching, quota attainment, and territory balance — and the contributor split lets you fairly attribute team-sold deals (AE + designer) rather than crediting one name.
- **Target:** Each rep at/above team-median revenue and win rate; set quota with owner
- **Data source:** tabOpportunity (opportunity_owner, opportunity_amount, status, custom_date_closed_won, custom_scope_contributors child table for shared credit).
- **Implementation:** SQL aggregate by opportunity_owner (and explode custom_scope_contributors for shared attribution). Combine with the Activity Volume KPI for touches-per-win. Render as a per-rep table in the Sales KPI Snapshot / Sales Pipeline page. No new fields.
- **Refresh:** Daily snapshot; monthly scorecard

### 13. Closed-Won Hand-Off Speed — 🟢 Auto
- **Definition:** Median hours/days from custom_date_closed_won to creation of the linked Project (custom_created_project). Also: count + $ of Closed-Won opps with custom_created_project still NULL (hand-off backlog), bucketed by age (1-day / 3-day thresholds).
- **Why it matters:** A won deal earns nothing until Design/Build starts. Slow hand-off delays the whole revenue-producing pipeline and frustrates the customer right after they signed — the worst moment to drop the ball.
- **Target:** Median hand-off < 1 business day; 0 won opps un-converted beyond 3 days
- **Data source:** tabOpportunity (status='Closed Won', custom_date_closed_won, custom_created_project); tabProject (custom_opportunity, creation). Already a defined metric in the Closed-Won Hand-Off Engine + assistant_tools/closed_won_handoff_status.py.
- **Implementation:** Reuse closed_won_handoff_status.py logic: backlog = Opportunity WHERE status='Closed Won' AND custom_created_project IS NULL; speed = AVG(Project.creation - Opportunity.custom_date_closed_won). Surface as Number Card 'Hand-Off Backlog ($)' + snapshot. Fully wired today.
- **Refresh:** Realtime; daily backlog snapshot

### 14. Hand-Off Quality (Process-Step Completeness & SLA Adherence) — 🟢 Auto
- **Definition:** % of newly created Projects whose seeded Closed-Won hand-off process steps (e.g. 'Hold Hand-Off Meeting', 'Identify Opportunity Needs') are Completed within their due_by/sla_business_days, and % of opps that hit Closed-Won with required fields (dollar amount, customer, summary) populated.
- **Why it matters:** Speed without completeness creates downstream design/build rework. Measuring whether the hand-off steps actually got done on time protects the customer experience and the build margin.
- **Target:** >= 90% of hand-off steps completed within SLA; 100% of won opps with complete required fields
- **Data source:** tabProject Process Step (step_title, status, due_by, sla_business_days, completed_on) seeded on Project insert; tabOpportunity required-field completeness.
- **Implementation:** SQL on Project Process Step for hand-off-phase steps: COUNT(status='Completed' AND completed_on <= due_by) / COUNT(*). Reuse escalate_overdue_steps daily task output. Add field-completeness check on Closed-Won opps. No new schema — steps + SLA fields already exist.
- **Refresh:** Daily snapshot (aligns with escalate_overdue_steps task)

### 15. Maintenance Contract Renewal Rate — 🟢 Auto
- **Definition:** # maintenance contracts renewed (a new/extended Sapphire Maintenance Contract for the same customer starting at/after the prior end_date) / # contracts that reached their end_date in the period. Gross and net (revenue-weighted) versions.
- **Why it matters:** Recurring maintenance is the annuity that makes a fountain build profitable over its life. Renewal rate is the leading indicator of recurring-revenue health and of whether Sales is protecting the installed base, not just chasing new builds.
- **Target:** >= 90% gross renewal of expiring contracts
- **Data source:** tabSapphire Maintenance Contract (customer, start_date, end_date, status, service_plan, project_contract). Fields confirmed present in the doctype JSON.
- **Implementation:** SQL: for contracts with end_date in window, check whether the same customer has another contract with start_date >= that end_date (or status renewed). Rate = renewed / expired. Revenue-weight via linked Sales Order / invoicing_frequency. New cron metric in the Sales KPI Snapshot; no new fields (end_date + status already exist).
- **Refresh:** Daily snapshot; monthly review

### 16. Maintenance Contract Churn & Expiry Risk — 🟢 Auto
- **Definition:** Churn = # contracts that lapsed (reached end_date with no renewal and status not Active) / total active at period start. Expiry risk = count + revenue of contracts with end_date within the next 60 days not yet renewed.
- **Why it matters:** Surfaces recurring-revenue at risk BEFORE it lapses, so Sales can proactively re-sign. A lapsed maintenance contract often means a neglected fountain and a lost lifetime customer.
- **Target:** Annual churn < 10%; 0 contracts lapse without a documented renewal attempt
- **Data source:** tabSapphire Maintenance Contract (status, end_date, customer). status transitions to non-Active on lapse.
- **Implementation:** SQL: churn = COUNT(end_date in window AND status!='Active' AND no successor contract) / active-at-start. Expiry risk = COUNT/SUM where end_date BETWEEN today AND today+60 AND no renewal. Add a daily task that flags upcoming expiries to the contract owner (mirror send_device_warranty_reminders pattern). No new fields.
- **Refresh:** Daily snapshot + 60-day expiry alert

### 17. Customer Acquisition Cost (CAC) by Channel — 🔴 Manual
- **Definition:** Total sales + marketing spend attributable to a channel / # new customers (or won deals) acquired from that channel in the period. Channel from Lead source attribution.
- **Why it matters:** Closes the loop on marketing ROI: which channels produce profitable fountain customers vs. expensive ones. Without it, ad budget is allocated blind.
- **Target:** CAC < 20% of first-project gross margin; payback < 12 months
- **Data source:** Numerator (spend) NOT integrated: Google Ads / Meta / LinkedIn ad spend, plus loaded sales labor cost. Denominator IS available: tabLead source + Closed-Won Opportunity count by channel.
- **Implementation:** Denominator (wins per channel) is Auto from Lead.source -> won Opportunity. Numerator needs ad-spend, which is un-integrated. Lightest capture: a monthly 'Marketing Spend' doctype with channel + amount entered by hand (or a single CSV import from each ad platform). Until an Ads API bridge is built, CAC = manual spend entry / automated wins-by-channel.
- **Refresh:** Monthly (manual spend entry)

### 18. Win/Loss Reason Analysis — 🔴 Manual
- **Definition:** Distribution of structured reasons on Closed-Won and Closed-Lost opportunities (e.g. Price, Timeline, Design Fit, Competitor, No Budget, Went Dark), with % and $ by reason and by competitor.
- **Why it matters:** Tells you WHY you win and lose custom fountain bids — the qualitative driver behind win rate. Directly informs pricing, design positioning, and competitive strategy in a niche market.
- **Target:** Reason captured on 100% of closed opps; <= 25% of losses attributable to any single fixable reason
- **Data source:** tabOpportunity at close. No structured reason field exists today (only free-text status).
- **Implementation:** Add 'custom_win_loss_reason' Select and 'custom_competitor' Data/Link fields to Opportunity, made mandatory when status becomes Closed Won/Lost (mandatory_depends_on). Rep picks a reason at close — one click. Then GROUP BY reason is fully automated. This is the lightest possible capture for inherently human judgment.
- **Refresh:** Realtime on close; monthly rollup

**Data gaps:**
- No quote/proposal-sent timestamp distinct from Opportunity.status: 'Quotation Sent' is a status but there is no custom_quotation_sent_on datetime, so quote-to-close TIME can only be measured from Project Contract.signed_on, not from when the quote actually went out.
- No structured forecast commit: Opportunity has a native 'probability' field but no forecast category (Commit/Best-Case/Pipeline), and no period-start snapshot is taken, so forecast accuracy cannot be computed without adding a category + snapshotting it.
- No won/lost reason or competitor capture: Opportunity close has only the status; win/loss analysis and competitive intelligence are impossible without a structured reason field.
- No ad-spend integration: Google Ads / Meta / LinkedIn spend is not wired (noted as a known gap), so true CAC and channel ROI require manual spend entry — only the wins-per-channel denominator is automated.
- No sales target/quota doctype: pipeline coverage and quota attainment need a target value that lives nowhere today; requires a small new field or Sales Target doctype.
- Maintenance contract has no explicit renewal-link or churn-reason field: renewal is inferred by matching customer + successor start_date, which is reliable but heuristic; a 'renewed_from' link would make renewal/churn exact.
- No first-response activity flag on Lead: lead response time is reconstructed by joining Call Log/Communication via timeline_links, which works but depends on Triton correctly linking the party — unlinked calls/texts would understate responsiveness.
- No persisted Sales KPI snapshot doctype yet: the Daily Briefing pattern exists as a model, but a dedicated Sales KPI Snapshot store must be created to retain historical trend (cycle time, win rate, forecast) for period-over-period analysis.

**Recommended minimal manual entry:**
- Win/Loss reason + competitor at close: one Select pick on Opportunity when status becomes Closed Won/Lost (add custom_win_loss_reason + custom_competitor, mandatory_depends_on close). Unlocks the only KPI that needs human judgment and powers competitive strategy.
- Forecast category per open opp: reps tag each open Opportunity Commit / Best Case / Pipeline (new custom_forecast_category Select); the weighting and accuracy math are then automatic.
- Quarterly sales/booking target: sales lead enters one Currency value per quarter (and optionally per rep) in Sales Activity Settings to enable Pipeline Coverage and quota attainment.
- Monthly marketing spend by channel: enter (or CSV-import) ad spend per channel into a small Marketing Spend doctype so CAC can divide it by the already-automated wins-per-channel count, until an Ads API bridge is built.

---
## Marketing

> Marketing's full scope at Sapphire Fountains spans lead generation across channels, web/SEO performance, paid-campaign ROI, brand & social reach, email nurture, and the MQL->SQL handoff to Sales. The ERP captures the bottom of the funnel well (Lead, Opportunity, Customer, GA4/GSC, Triton calls/SMS, Sales Invoice for closed revenue) but is blind to the top: ad spend, social, email-platform engagement, and cost data all live in external tools (Google Ads, Meta, LinkedIn, an ESP, GSC for organic). That split is the central tension of this catalog. I maximized Auto/Semi-Auto by computing everything downstream of a captured lead from existing doctypes (Lead.source / custom_lead_source, Opportunity stage timestamps, custom_date_closed_won, custom_created_project, Sales Invoice grand_total) and by reusing the already-wired GA4 + Search Console API. The remaining gaps (spend, CAC, social, email, attribution) are made Semi-Auto wherever a single new field or a once-a-week paste of platform totals unlocks the metric, and Manual only for genuinely un-integrated spend. Two structural enablers carry most of the value: (1) a lightweight 'Marketing Spend' doctype (channel, month, amount) pasted monthly, which turns CPL, CAC, and ROI from Manual into Auto; and (2) a 'Marketing Qualified' flag + MQL/SQL date stamps on Lead/Opportunity to make the handoff funnel measurable. Targets are set for a regional custom-fountain design-build-maintain business with a long, high-ACV sales cycle and a heavy reliance on referral/organic plus selective paid search — meaning lead quality and cost-per-acquired-customer matter far more than raw lead volume. 18 KPIs follow, ordered top-of-funnel (reach/traffic) to bottom (revenue/ROI).

### 1. Leads Generated by Channel (volume) — 🟢 Auto
- **Definition:** Count of new Lead records per period, grouped by acquisition channel. Channel = Lead.source (stock) falling back to the Lead.custom_lead_source_details child rows when multi-source. Periodized by Lead.creation (week/month).
- **Why it matters:** The single most actionable top-of-funnel number: which channels (organic search, referral, Google Ads, trade shows, repeat customer, social) actually produce inquiries for custom fountains. Drives where the next marketing dollar goes.
- **Target:** Trend up MoM overall; no single channel >60% of volume (concentration risk). Set absolute monthly floor with owner (typical small design-build: 25-60 leads/mo).
- **Data source:** tabLead.source, tabLead.custom_lead_source_details (child), tabLead.creation. Integration: none needed — native doctype.
- **Implementation:** Dashboard Chart (Group By, document_type=Lead, group_by_based_on=source, based_on=creation, interval=Monthly) added to fixtures/dashboard_chart.json, plus a Number Card 'Leads This Month'. SQL: SELECT source, COUNT(*) FROM `tabLead` WHERE creation >= %s GROUP BY source. Enforce a fixed Lead Source option set so channels are clean.
- **Refresh:** Real-time (chart recomputes on view); snapshot daily via Morning-Briefing-style cron if trend history is wanted.

### 2. Cost Per Lead (CPL) by Channel — 🟡 Semi
- **Definition:** CPL = channel marketing spend in period / leads generated by that channel in period. Computed per paid channel (Google Ads, Meta, LinkedIn, trade shows) and blended across all paid.
- **Why it matters:** Tells you whether a fountain lead from paid search costs $40 or $400. Without it, channel budget decisions are guesses. Pairs with conversion rate to find the cheapest channel that actually closes.
- **Target:** Blended paid CPL under owner-set ceiling (high-ACV B2B-ish home/commercial water features often tolerate $75-$250/lead). Each channel CPL <= 1.5x blended or flag for review.
- **Data source:** Numerator: NEW 'Marketing Spend' doctype (fields: channel, month, amount, campaign optional). Denominator: tabLead grouped by source (the Leads-by-Channel KPI). No external spend integration exists today.
- **Implementation:** Create 'Marketing Spend' doctype (channel Link to a Marketing Channel master, period_month Date, amount Currency, optional campaign). Marketing pastes monthly platform totals (4-6 numbers). Then CPL is a join: spend.amount / count(Lead where source=channel and month). Expose via a whitelisted report marketing_cpl.py + Number Card. Upgrade path to Auto: Google Ads API + Meta Marketing API connectors writing Marketing Spend rows nightly.
- **Refresh:** Monthly (spend paste); CPL recomputes immediately after each paste.

### 3. Customer Acquisition Cost (CAC) by Channel — 🟡 Semi
- **Definition:** CAC = total marketing+sales spend attributable to a channel in period / number of NEW customers acquired from that channel in period. New customer = first Sales Invoice (docstatus=1) for a Customer whose originating Lead/Opportunity carried that channel (Customer.custom_lead_source).
- **Why it matters:** The bottom-line efficiency metric. For a design-build-maintain business a won customer can be worth $50k+ in build plus recurring maintenance, so CAC must be read against LTV, not revenue alone. Reveals whether 'cheap leads' channels actually produce buyers.
- **Target:** Blended CAC < 15-20% of first-project contract value (custom_project_dollar_amount); ideally CAC payback inside the first build invoice. Set precise ratio with owner.
- **Data source:** Spend: Marketing Spend doctype. New customers by channel: tabCustomer.custom_lead_source joined to first tabSales Invoice (MIN(posting_date), docstatus=1). Acquired-customer count via Opportunity.custom_date_closed_won + custom_created_project as cross-check.
- **Implementation:** Once Marketing Spend exists, CAC is a query: spend / count(distinct Customer first-invoiced in period grouped by custom_lead_source). Build marketing_cac.py whitelisted report. Ensure Customer.custom_lead_source is populated at lead-conversion time (add a hook on Lead->Customer conversion to copy source). Manual element is only the spend paste.
- **Refresh:** Monthly.

### 4. Web Sessions & Channel Mix (GA4) — 🟢 Auto
- **Definition:** Total website sessions in trailing 30 days, broken down by GA4 sessionDefaultChannelGroup (Organic Search, Direct, Referral, Paid Search, Social, Email). Plus active users and daily session timeline.
- **Why it matters:** Website is the top of the funnel for a visually-driven product like fountains — galleries and project portfolios drive inquiries. Channel mix shows whether traffic is earned (SEO) or bought, and flags sudden drops (algorithm hits, broken tracking).
- **Target:** Sessions trending up MoM; Organic Search >= 40% of sessions (healthy earned demand). Set absolute session target with owner once baseline is known.
- **Data source:** GA4 Data API via erpnext_enhancements/api/analytics.py::get_ga4_data() (metrics activeUsers, sessions; dimension sessionDefaultChannelGroup, date). Config in GA4 Settings Single doctype. Already wired and live.
- **Implementation:** Already computed and rendered on the ga4_dashboard Desk page. To add to KPI dashboard/snapshot: call get_ga4_data() from a daily cron and store totals in a new 'Marketing Snapshot' doctype (date, metric_name, value) for trend history beyond GA4's 30-day window. No new external work.
- **Refresh:** Daily (GA4 API pull).

### 5. Organic Search Performance (clicks, impressions, CTR, position) — 🟢 Auto
- **Definition:** Google Search Console trailing-30-day clicks, impressions, average CTR (clicks/impressions), and average SERP position; with top queries and top landing pages by clicks.
- **Why it matters:** Fountain buyers search high-intent local terms ('custom fountain builder Utah', 'pondless water feature design'). SEO is the cheapest durable lead channel. Rising impressions with flat clicks = a content/title problem; falling position = lost ground to competitors.
- **Target:** Clicks up MoM; average position improving (lower number) for top 10 commercial queries; CTR >= 3-4% blended. Owner sets a target query set.
- **Data source:** Google Search Console Search Analytics API via api/analytics.py::get_gsc_data() (dimensions date, query, page; metrics clicks, impressions, ctr, position). Config in GA4 Settings. Already wired and live.
- **Implementation:** Already implemented and rendered on ga4_dashboard. Add daily cron to persist clicks/impressions/CTR/position into Marketing Snapshot for >30-day trend, and a Number Card for 'Organic Clicks (30d)'. No new integration.
- **Refresh:** Daily (GSC API pull).

### 6. Website Conversion Rate (Session -> Lead) — 🟡 Semi
- **Definition:** Conversion rate = web-originated Leads in period / GA4 sessions in period (overall, and ideally by channel). A web-originated Lead = Lead.source in the set of online channels (Website, Organic, Paid Search, Social).
- **Why it matters:** Separates a traffic problem from a conversion problem. If sessions are high but few become leads, the site/forms/CTAs are the bottleneck — not ad spend. Critical for a portfolio-driven site where the 'Request a Design Consultation' form is the money action.
- **Target:** Overall site session->lead >= 1.5-3% (form-based B2C/B2B service); paid-search landing pages >= 5%. Set with owner.
- **Data source:** Numerator: tabLead filtered to web sources (creation period). Denominator: GA4 sessions from get_ga4_data(). Best-case: a GA4 conversion event 'generate_lead' fired by the website form (already supported by GA4 conversions dimension).
- **Implementation:** Compute lead count (Auto) / GA4 sessions (Auto) in a small whitelisted function; this is automatable today at the blended level. To make it channel-accurate, add a 'generate_lead' GA4 conversion event on the website form submit (one-time web/dev task) so GA4 itself reports conversions by channel — then it becomes fully Auto from get_ga4_data() conversions. Light dependency = the website form event tag.
- **Refresh:** Daily.

### 7. Lead -> Opportunity Conversion Rate — 🟢 Auto
- **Definition:** Of Leads created in a cohort period, the % that convert to an Opportunity (Lead.status='Converted' or a Dynamic Link from Opportunity back to the Lead). Measured overall and by Lead.source.
- **Why it matters:** The first real quality gate. A channel can flood you with leads that never qualify for a fountain project (wrong budget, renters, tire-kickers). This ratio, sliced by source, is how you tell quality from noise.
- **Target:** Blended Lead->Opp >= 20-30%; underperforming channels < 10% get budget cut. Set baseline with owner.
- **Data source:** tabLead.status='Converted', tabLead.source, tabLead.creation; cross-check via tabOpportunity where party_name/lead links to the Lead. Native doctypes, no integration.
- **Implementation:** SQL cohort: for leads in month M, count those with status='Converted' (or with a linked Opportunity). Build marketing_funnel.py report returning the per-source conversion table, and a Group By Dashboard Chart on Lead.status. Already-stamped status fields make this immediate.
- **Refresh:** Daily snapshot for cohort trend; real-time chart otherwise.

### 8. Opportunity -> Closed-Won Conversion (Win Rate) by Lead Source — 🟢 Auto
- **Definition:** Win rate = Opportunities reaching status='Closed Won' / total Opportunities closed (Won + Lost) in period, grouped by the originating Lead source (Opportunity.custom_lead_source) and by opportunity owner.
- **Why it matters:** Closes the loop from marketing channel all the way to revenue. A source with mediocre lead volume but high win rate (e.g. referrals) is gold; a high-volume low-win source is burning sales time. Directly informs spend reallocation.
- **Target:** Blended win rate >= 25-35% for qualified custom-fountain opps; referral/repeat sources typically 2x paid. Owner sets per-source floor.
- **Data source:** tabOpportunity.status, tabOpportunity.custom_lead_source, tabOpportunity.custom_date_closed_won, tabOpportunity.opportunity_amount, tabOpportunity.opportunity_owner. Native; verified custom fields exist in fixtures/custom_field.json.
- **Implementation:** SQL: win_rate = SUM(status='Closed Won')/SUM(status IN ('Closed Won','Closed Lost')) GROUP BY custom_lead_source. Reuse existing 'Opportunities by Status' chart infra; add a per-source win-rate report marketing_winrate.py. All fields already populated by the stage-change before_save hook.
- **Refresh:** Daily snapshot; real-time chart.

### 9. Pipeline Value Generated by Marketing (by channel) — 🟢 Auto
- **Definition:** Sum of open Opportunity.opportunity_amount attributable to each Lead source, plus dollar value of Closed-Won opportunities by source in period. Distinguishes marketing-sourced from sales-sourced/referral pipeline.
- **Why it matters:** Converts marketing activity into the language leadership cares about: dollars in the pipe. For a business where one fountain build is six figures, channel-level pipeline value matters more than lead counts.
- **Target:** Marketing-sourced pipeline >= 3-4x annual marketing budget (pipeline coverage). Set with owner against quota.
- **Data source:** tabOpportunity.opportunity_amount, status, custom_lead_source, custom_date_closed_won. Native fields (opportunity_amount confirmed; custom fields present).
- **Implementation:** SQL: SUM(opportunity_amount) GROUP BY custom_lead_source, status. Add a 'Pipeline by Source' Sum Dashboard Chart and Number Cards ('Open Pipeline', 'Won This Quarter'). Reuses existing Sales Pipeline dashboard pattern.
- **Refresh:** Real-time chart; daily snapshot for trend.

### 10. Campaign / Channel ROI (ROMI) — 🟡 Semi
- **Definition:** Return on Marketing Investment = (Closed-Won revenue attributable to channel in period - channel spend) / channel spend. Revenue = sum of first-project contract value (Opportunity.opportunity_amount or Customer's first Sales Invoice grand_total) for customers acquired via that channel.
- **Why it matters:** The ultimate 'is this channel worth it' metric. Ties spend directly to closed fountain revenue. A single attributed build win can make a channel's annual ROI strongly positive, so this must be measured over a sales-cycle-length window, not monthly.
- **Target:** Blended ROMI >= 5:1 revenue:spend on a trailing-12-month basis; kill channels < 2:1 after a full sales cycle. Set with owner.
- **Data source:** Spend: Marketing Spend doctype. Revenue: tabOpportunity (Closed Won, opportunity_amount, custom_lead_source, custom_date_closed_won) and/or tabSales Invoice.grand_total joined via Customer.custom_lead_source. Sales-cycle window needed because of long fountain timelines.
- **Implementation:** Once Marketing Spend exists and custom_lead_source flows Lead->Opp->Customer, ROMI is a join over a trailing window. Build marketing_romi.py with a configurable lookback (default 12 months to span the design-build cycle). Manual element = monthly spend paste only.
- **Refresh:** Monthly (revenue accrues continuously; spend pasted monthly).

### 11. MQL -> SQL Conversion & Handoff SLA — 🟡 Semi
- **Definition:** MQL->SQL rate = Leads flagged Marketing Qualified that Sales accepts as Sales Qualified (become an active Opportunity) / total MQLs. Handoff SLA = avg time from MQL flag to first sales touch (first Communication/Call Log against the lead).
- **Why it matters:** The seam where marketing-generated demand is won or lost. Slow or rejected handoffs mean wasted spend. For high-ACV fountain deals, a fast first call materially lifts win rate.
- **Target:** MQL->SQL acceptance >= 60%; first-touch within 1 business day of MQL flag. Set with owner.
- **Data source:** NEW fields: Lead.custom_is_mql (Check) + Lead.custom_mql_date (Datetime); SQL acceptance via Opportunity creation/status. First-touch via tabCommunication / tabCall Log timeline_links against the Lead (Triton already logs these).
- **Implementation:** Add custom_is_mql + custom_mql_date to Lead (set manually by marketing or auto by a scoring rule, e.g. visited pricing page + valid phone). Then MQL->SQL = count(MQL leads that became Opportunities)/count(MQL); SLA = MIN(Communication.communication_date) - custom_mql_date. Light: one flag/date; the touch data is already auto-captured by Triton.
- **Refresh:** Daily.

### 12. Lead Response Time (Speed-to-Lead) — 🟢 Auto
- **Definition:** Median elapsed time from Lead.creation to the first outbound contact attempt (first outbound Call Log or sent SMS/Communication linked to the Lead/Customer).
- **Why it matters:** Speed-to-lead is one of the strongest predictors of conversion. Because Triton logs every call and SMS automatically, this is measurable today with zero new data entry — a rare fully-Auto quality metric.
- **Target:** Median first response < 1 hour during business hours; < 4 hours overall. Set with owner.
- **Data source:** tabLead.creation vs MIN(tabCall Log.start_time where type='Outbound') or MIN(tabCommunication where sent_or_received='Sent'), joined via timeline_links / phone match. Triton telephony already populates these in real time.
- **Implementation:** SQL joining Lead to the earliest outbound Communication/Call Log via the Dynamic Link timeline. Build marketing_speed_to_lead.py report + Number Card 'Median Response Time'. No new fields — Triton call/SMS logging already wired.
- **Refresh:** Daily.

### 13. Brand & Social Reach / Engagement — 🔴 Manual
- **Definition:** Aggregate followers, reach/impressions, and engagement rate (engagements/impressions) across Instagram, Facebook, LinkedIn, plus posting cadence. Engagement rate is the headline (reach is vanity).
- **Why it matters:** Fountains sell on visual aspiration; social is the brand-awareness and portfolio-distribution engine that feeds the top of the funnel (and referrals). Without it, marketing has no read on brand momentum.
- **Target:** Engagement rate >= 1.5-3% per post; follower and reach growth positive QoQ. Set with owner per platform.
- **Data source:** NONE integrated. Sources are Meta Graph API (IG/FB) and LinkedIn API, or each platform's native dashboard. No social integration exists in the app today.
- **Implementation:** Lightest capture: a monthly paste of 5-6 numbers per platform (followers, reach, engagements, posts) into a 'Marketing Snapshot' doctype (date, channel, metric_name, value). Compute engagement = engagements/reach from the pasted rows. Upgrade path to Semi/Auto: Meta Graph API + LinkedIn API connector writing snapshot rows nightly (future integration work).
- **Refresh:** Monthly paste (or nightly if API connector built).

### 14. Email Marketing Performance (open / click / unsubscribe) — 🟡 Semi
- **Definition:** Per-campaign and trailing-period open rate, click-through rate (CTR), unsubscribe rate, and resulting leads. Open rate = opens/delivered; CTR = clicks/delivered; unsub rate = unsubscribes/delivered.
- **Why it matters:** Email nurtures the long fountain consideration cycle (design inspiration, seasonal maintenance offers, past-customer reactivation for new features/rentals). Performance shows whether nurture is keeping prospects warm or burning the list.
- **Target:** Open rate >= 35-45% (engaged niche list), CTR >= 2-4%, unsubscribe < 0.5% per send. Set with owner.
- **Data source:** NONE integrated — lives in the ESP (Mailchimp/HubSpot/ConvertKit). ERPNext has no email-marketing engagement data. Resulting leads CAN be tied back via Lead.source='Email'.
- **Implementation:** Per-campaign paste of ESP summary stats (delivered/opens/clicks/unsubs — 4 numbers) into a 'Marketing Campaign' doctype (campaign, send_date, channel='Email', + those metrics). Resulting-lead attribution is Auto via Lead.source. Upgrade to Auto: most ESPs expose a reporting API / webhook to write Marketing Campaign rows. Light human input = the per-send paste.
- **Refresh:** Per send / weekly.

### 15. Marketing-Influenced Maintenance & Rental Recurring Revenue — 🟡 Semi
- **Definition:** Recurring revenue (maintenance contracts + rentals) generated from customers originally acquired through marketing channels, per period. Ties marketing not just to the one-time build but to the lifetime annuity.
- **Why it matters:** Unique to a design-build-MAINTAIN business: a fountain customer is worth the build PLUS years of maintenance contracts and seasonal rentals. Marketing that acquires customers who go on to sign maintenance is far more valuable than build-only. This reframes CAC against true LTV.
- **Target:** % of marketing-acquired customers attaching a maintenance contract >= 50%; recurring revenue from marketing-sourced customers trending up. Set with owner.
- **Data source:** Sapphire Maintenance Contract (status=Active) + recurring Sales Invoices, joined to Customer.custom_lead_source. Maintenance contract->customer->lead-source chain. Native doctypes.
- **Implementation:** Join Sapphire Maintenance Contract -> Customer -> custom_lead_source; SUM recurring invoice value by source. Auto once Customer.custom_lead_source is reliably populated at conversion (add the Lead->Customer source-copy hook noted in CAC). Build marketing_ltv.py. Only dependency is consistent source stamping on Customer.
- **Refresh:** Monthly.

### 16. Customer Lifetime Value (LTV) by Acquisition Channel — 🟡 Semi
- **Definition:** LTV = sum of all paid Sales Invoice revenue (build + maintenance + rentals, docstatus=1) per customer, averaged across customers grouped by acquisition channel (Customer.custom_lead_source). Compared against that channel's CAC.
- **Why it matters:** The decision metric that makes CAC meaningful. A channel with high CAC but high LTV (referrals that become maintenance customers) beats a cheap channel that only ever produces one-off small jobs. This is how spend should actually be allocated.
- **Target:** LTV:CAC >= 3:1 per channel; flag any channel below 1:1 for elimination. Set with owner.
- **Data source:** tabSales Invoice.grand_total (docstatus=1) SUM per Customer, joined to tabCustomer.custom_lead_source. QBO-synced invoices included (QBO is system of record). Native + already-synced data.
- **Implementation:** SQL: AVG(SUM(grand_total) per customer) GROUP BY custom_lead_source. Auto once custom_lead_source is populated on Customer (same source-copy hook). Pair with the CAC report to render LTV:CAC. No external dependency beyond consistent source tagging.
- **Refresh:** Monthly.

### 17. Marketing Funnel Conversion Cascade (Session -> Lead -> Opp -> Won) — 🟡 Semi
- **Definition:** A single funnel view chaining the four stage rates for a fixed cohort: web sessions, leads, opportunities, closed-won; with stage-to-stage conversion % and overall session-to-customer rate. Sliced by channel where data allows.
- **Why it matters:** Gives marketing and leadership one screen showing exactly where demand leaks — traffic, form conversion, qualification, or close. For a long fountain sales cycle, seeing the whole cascade prevents over-optimizing one stage while another bleeds.
- **Target:** No single stage drop worse than benchmark (session->lead ~2%, lead->opp ~25%, opp->won ~30%); overall session->customer >= 0.1-0.2%. Set with owner.
- **Data source:** GA4 sessions (get_ga4_data) + tabLead + tabOpportunity (status, custom_date_closed_won, opportunity_amount) + Sales Invoice. Combines the GA4 integration with native pipeline doctypes.
- **Implementation:** Build a marketing_funnel_cascade.py whitelisted function that pulls GA4 sessions and runs the Lead/Opp/Won cohort queries, returning a funnel-shaped dict for a custom HTML block (modeled on the Daily Briefing/ga4_dashboard pattern). Fully Auto at the blended level; the only soft spot is channel-accurate session->lead, which needs the GA4 generate_lead event (see Website Conversion KPI).
- **Refresh:** Daily snapshot stored in Marketing Snapshot for trend.

**Data gaps:**
- Paid ad spend (Google Ads, Meta, LinkedIn) is not integrated — no API connector exists; spend must be pasted into a new Marketing Spend doctype until connectors are built. This blocks fully-Auto CPL, CAC, and ROMI.
- Social media reach/engagement (Instagram, Facebook, LinkedIn) is entirely un-integrated — no Meta Graph API or LinkedIn API wiring; only manual paste available today.
- Email marketing engagement (opens/clicks/unsubs) lives in the external ESP with no integration; resulting leads are attributable via Lead.source but send-level performance is not.
- Multi-touch attribution is impossible: the model captures a single Lead.source/custom_lead_source per record, so first-touch vs last-touch vs influenced credit cannot be split. A customer found via organic then nurtured by email then closed by referral collapses to one source.
- Customer.custom_lead_source is not guaranteed to be populated at Lead->Customer conversion, which silently breaks CAC, LTV, and channel ROI joins — needs a conversion hook to copy source from Lead/Opportunity to Customer.
- No GA4 'generate_lead' conversion event on the website form, so channel-accurate session->lead conversion can only be approximated (blended), not measured per channel.
- No MQL concept exists in the data model (no qualification flag or date on Lead), so the MQL->SQL handoff funnel and handoff SLA cannot be measured without new fields.
- Trade-show / event lead capture is free-text in Lead.source only — no structured event, cost, or attendance data, so event ROI is coarse.
- GA4 and Search Console only retain ~30 days via the live API; without a daily snapshot doctype there is no long-term web/SEO trend history.
- No competitive / share-of-voice or brand-search-volume data (e.g. branded vs non-branded query split is available in GSC but not currently separated).

**Recommended minimal manual entry:**
- Monthly marketing spend per channel — paste 4-6 numbers (Google Ads, Meta, LinkedIn, trade shows/other) into a new lightweight 'Marketing Spend' doctype (channel, month, amount). This single monthly entry unlocks CPL, CAC, and channel ROI/ROMI.
- Monthly social snapshot — per platform paste followers, reach, engagements, and post count into a 'Marketing Snapshot' doctype (date, channel, metric, value). ~5 numbers x 3 platforms once a month.
- Per-send email stats — paste delivered/opens/clicks/unsubscribes from the ESP into a 'Marketing Campaign' doctype after each send (4 numbers). Resulting leads attach automatically via Lead.source.
- MQL flag — when marketing qualifies a lead, set a custom_is_mql checkbox (and let custom_mql_date auto-stamp). One click per qualified lead; can be automated later with a scoring rule.
- One-time/ongoing data hygiene: ensure every Lead has a clean Lead.source from a fixed option set, and that source is copied onto the Customer at conversion (ideally via a hook, but until then a quick check during closed-won handoff).

---
## Executive

> A single-screen C-suite view for Sapphire Fountains' design-build-maintain water-feature business, rolling up Sales, Finance, Design/Engineering, Production/Build, Field-Service, and Workforce into 16 KPIs. The data architecture is strong: QBO sync (system of record for accounting), Stripe, stock ERPNext AR/AP/GL, the Opportunity pipeline with stage-change timestamps, Project + Project Contract (contract value, milestones, completion dates), Timesheet/Job Interval labor, and maintenance/visit data are all wired. This makes most financial, pipeline, backlog, delivery, and utilization KPIs Auto or Semi-Auto. The honest gaps for a true exec cockpit are: (1) no plan/budget/target doctype, so every "vs plan" comparison needs a lightweight target store; (2) no cost-of-goods rollup per project, so true gross margin needs labor+materials+travel joined or a periodic GL-based proxy; (3) no systematic CSAT/NPS capture beyond AI-scored calls; (4) cash runway needs a monthly burn read from GL. Recommendation: build one "Executive KPI Snapshot" doctype written by a daily/weekly cron (mirroring the Morning Briefing pattern) plus a tiny "KPI Target" doctype for plan figures; that single pair unlocks ~12 of 16 KPIs as Auto and a true one-screen Executive dashboard fixture.

### 1. Bookings (New Signed Contract Value) vs Plan — 🟡 Semi
- **Definition:** Sum of Project Contract value for contracts where status='Signed' and signed_on falls in the period (use not_to_exceed for fixed-price SOW, else milestones_total / total_design_fee), divided by the period booking target. Reported as $ booked and % of plan.
- **Why it matters:** Bookings are the leading indicator of future revenue for a design-build shop with long delivery cycles. Revenue this quarter was sold months ago; bookings tell the CEO what the next 2-3 quarters of build revenue will look like and whether sales is keeping the funnel full.
- **Target:** 100% of quarterly booking plan (set with owner); minimum 90% trailing-3-month average. Default seed: set with sales leadership.
- **Data source:** Project Contract doctype: status, signed_on, not_to_exceed, milestones_total (sum of Contract Milestone.amount), total_design_fee. Target from new KPI Target doctype (period, metric='bookings', target_amount).
- **Implementation:** Auto query: SELECT SUM(COALESCE(not_to_exceed, milestones_total, total_design_fee)) FROM `tabProject Contract` WHERE status='Signed' AND signed_on BETWEEN %(start)s AND %(end)s. The 'vs plan' half needs a new lightweight 'KPI Target' doctype (fields: period_start, period_end, metric_key, target_amount) entered quarterly by leadership. Snapshot written by weekly cron into Executive KPI Snapshot.
- **Refresh:** Daily snapshot, evaluated weekly/monthly vs plan

### 2. Recognized Revenue vs Plan (MTD/QTD) — 🟡 Semi
- **Definition:** Sum of Sales Invoice.grand_total where docstatus=1 and posting_date in period (QBO-synced, ERPNext is system of record), divided by the revenue plan for the same period. Reported $ and % of plan, split design-fee vs build vs maintenance via item group / contract type where available.
- **Why it matters:** The top-line scorecard. Tells the C-suite whether the company is hitting its revenue commitment and, when split by line of business, whether maintenance recurring revenue is growing as a stabilizer against lumpy build revenue.
- **Target:** 100% of period revenue plan (set with owner). Maintenance revenue trending up QoQ.
- **Data source:** Sales Invoice (grand_total, posting_date, docstatus, items.item_group) synced from QBO; Account/GL Entry for income-account split. Target from KPI Target doctype.
- **Implementation:** Auto query on tabSales Invoice (docstatus=1, posting_date range). Line-of-business split via item_group or a contract-type tag. Only the plan figure needs manual entry in KPI Target. Daily cron computes MTD/QTD into Executive KPI Snapshot.
- **Refresh:** Daily snapshot (QBO CDC is hourly); plan comparison monthly

### 3. Gross Margin % (Build + Maintenance) — 🟡 Semi
- **Definition:** (Recognized Revenue - Direct Cost) / Recognized Revenue for the period. Direct cost = project labor (Timesheet Detail.amount) + materials (Purchase Invoice.grand_total + Stock Entry valuation for project-linked POs) + travel (Travel Trip.total_actual_cost by project) + maintenance consumables. Period-level proxy uses GL COGS accounts vs income accounts.
- **Why it matters:** Custom fountain builds vary wildly in profitability; a healthy top line can hide bleeding projects. Gross margin is the single best early-warning that the company is mispricing custom work or losing control of field labor and materials.
- **Target:** Build gross margin >= 35%; maintenance gross margin >= 50% (set with finance). Flag any project below 20%.
- **Data source:** GL Entry (income vs COGS accounts) for period proxy; per-project: Timesheet Detail (amount, project), Purchase Invoice/Purchase Order (project), Travel Trip (total_actual_cost, project), Sapphire Maintenance Consumable. Sales Invoice for revenue.
- **Implementation:** Period-level is Auto from GL: SUM(income accounts) vs SUM(COGS accounts) by posting_date. Per-project margin needs the three cost streams joined (labor/materials/travel) — buildable as a cron-computed rollup but requires consistent project tagging on POs/Purchase Invoices (custom_project / po_item.project already exist). Lightest path: ship the GL-account-based period margin now (Auto), add per-project margin as a follow-on rollup query.
- **Refresh:** Monthly close (period margin); weekly per-project watchlist

### 4. Net Margin / Operating Profit % — 🟢 Auto
- **Definition:** (Total Income - Total COGS - Total Operating Expense) / Total Income for the period, read from GL Entry grouped by account root type (Income, Expense). Reported $ operating profit and %.
- **Why it matters:** The bottom-line health of the whole company in one number, including overhead the gross margin ignores. This is the number that determines whether Sapphire is actually making money, not just building fountains.
- **Target:** Operating margin >= 10-15% (set with owner/finance).
- **Data source:** GL Entry (account, debit, credit, posting_date) joined to Account.root_type / account_type; QBO-synced Chart of Accounts (196-acct CoA already loaded).
- **Implementation:** SQL: aggregate GL Entry by Account.root_type over the period (Income - Expense). The full QBO CoA and opening balances are already loaded, so this is computable today. No new fields. Daily/monthly cron writes to Executive KPI Snapshot. (Note: there is currently no automated P&L snapshot — this KPI closes that documented gap.)
- **Refresh:** Monthly close, with running MTD daily

### 5. Backlog (Booked-but-Unbuilt Contract Value) — 🟡 Semi
- **Definition:** Sum of remaining contract value across active projects = SUM over Projects (status not Completed/Cancelled) of (contract value - revenue recognized to date). Contract value from custom_project_dollar_amount or linked Project Contract; recognized from Sales Invoice billed against the project.
- **Why it matters:** Backlog is the order book — how many months of build work is already sold and waiting. For a project business it drives capacity, hiring, and cash-flow planning. Shrinking backlog with flat bookings is an early recession signal for the shop.
- **Target:** Maintain >= 4-6 months of build capacity in backlog (set with ops). Trend should be flat-to-up.
- **Data source:** Project (custom_project_dollar_amount, status, custom_master_project); Project Contract (not_to_exceed / milestones_total); Sales Invoice billed-to-date per project (project field on invoice items).
- **Implementation:** Query active Projects, subtract billed Sales Invoice amount per project from contract value. Mostly Auto; the one soft spot is ensuring every active project has a contract value populated (custom_project_dollar_amount exists but may be blank on older projects) — backfill or fall back to linked Project Contract. Cron rollup into snapshot.
- **Refresh:** Weekly snapshot

### 6. Cash Position & Runway (months) — 🟢 Auto
- **Definition:** Cash position = sum of GL balances for accounts where account_type='Cash'/'Bank'. Runway = current cash / trailing-3-month average net monthly burn (operating cash outflow - inflow). Reported as $ cash and # months runway.
- **Why it matters:** Cash, not profit, is what keeps a build business alive through lumpy milestone billing and front-loaded material purchases. Runway is the survival metric every CEO checks first.
- **Target:** Maintain >= 3 months operating runway; cash never below a board-set floor (set with owner).
- **Data source:** GL Entry / Account (account_type Cash/Bank balances) from QBO sync; net burn from GL income vs expense cash movement over trailing 3 months.
- **Implementation:** Cash = SUM(GL Entry debit-credit) for Bank/Cash accounts as of today. Burn = trailing-90-day net of cash-affecting GL. Runway = cash / (burn/3). Computable now from synced GL; no new fields. Daily cron. (Closes the documented 'no cash flow forecast' gap with a backward-looking runway figure.)
- **Refresh:** Daily

### 7. Accounts Receivable & DSO — 🟢 Auto
- **Definition:** AR balance = sum of outstanding Sales Invoice (grand_total - paid) where docstatus=1 and status in Submitted/Overdue. DSO = (AR balance / trailing revenue) * days in period. Plus % of AR >60 days past due from aging buckets.
- **Why it matters:** Slow collections on six-figure fountain installs strangle cash even when the company is profitable. DSO and overdue AR tell the CEO whether milestone billing and collections are actually converting backlog into cash.
- **Target:** DSO <= 45 days; AR >60 days overdue < 10% of total AR (set with finance).
- **Data source:** Sales Invoice (outstanding_amount, due_date, status, docstatus); ERPNext Accounts Receivable aging report; Payment Entry / Stripe Payment for receipts.
- **Implementation:** Query outstanding Sales Invoices and bucket by (today - due_date). DSO = AR / (period revenue/period days). All fields synced from QBO and present in stock ERPNext. No new fields; daily cron snapshot. Pairs with Stripe text-to-pay to accelerate collections.
- **Refresh:** Daily

### 8. Win Rate & Pipeline Coverage — 🟢 Auto
- **Definition:** Win rate = count(Opportunity status='Closed Won' closed in period) / count(Closed Won + Closed Lost in period). Pipeline coverage = open weighted pipeline value / remaining period booking target. Plus total open pipeline $ by stage.
- **Why it matters:** Win rate measures sales effectiveness and pricing competitiveness on custom-quote work; coverage tells the CEO whether there is enough live pipeline to hit the booking plan, before it is too late to act.
- **Target:** Win rate >= 30% (set with sales); pipeline coverage >= 3x of remaining booking target.
- **Data source:** Opportunity (status, opportunity_amount, custom_date_closed_won, custom_stage_changed_on, creation); KPI Target doctype for booking plan.
- **Implementation:** Group tabOpportunity by status over the period for win rate; SUM(opportunity_amount) for open stages for coverage. Stage-change timestamps already stamped by before_save hook. Coverage ratio uses the same KPI Target row as Bookings. Daily cron. (Already partly surfaced by Sales Pipeline dashboard.)
- **Refresh:** Daily

### 9. On-Time Delivery Rate — 🟡 Semi
- **Definition:** % of projects (or milestones) completed on or before their committed date. Project-level: count(Projects completed where actual completion <= expected_end_date) / count(Projects completed in period). Milestone-level proxy via Project Process Step.completed_on vs due_by and Contract substantial_completion_date / final_completion_date vs actual.
- **Why it matters:** Late fountain installs damage reputation, trigger liquidated-damages exposure, and tie up crews that should be on the next job. On-time delivery is the operational promise the whole design-build value chain exists to keep.
- **Target:** >= 85% of projects/milestones delivered on time (set with ops).
- **Data source:** Project (expected_end_date, status, is_active timestamp / completion via Version); Project Process Step (due_by, completed_on, sla_business_days); Project Contract (substantial_completion_date, final_completion_date).
- **Implementation:** Process Step due_by vs completed_on is Auto today (timestamps auto-stamped). True project-level on-time needs a reliable 'actual completion date' — derive from the last process step completion or Version history of status->Completed, or add a custom_actual_completion_date stamped on status change (light hook). Recommend the small hook for accuracy. Cron rollup.
- **Refresh:** Weekly

### 10. Workforce Utilization % — 🟡 Semi
- **Definition:** Billable/productive hours / total available clock hours, company-wide and by crew. Productive hours from Job Interval (end_time - start_time - total_paused_seconds) and Timesheet Detail.hours on billable projects; available hours from headcount * standard work hours in period.
- **Why it matters:** Field labor is the largest controllable cost in build and maintenance. Utilization tells the CEO whether crews are being productively deployed or whether the company is carrying idle labor — directly driving gross margin.
- **Target:** Field crew utilization 75-85% (set with ops); flag sustained <65%.
- **Data source:** Job Interval (start_time, end_time, total_paused_seconds, status, project, employee); Timesheet Detail (hours, amount, project, activity_type); Employee count for denominator.
- **Implementation:** Productive hours sum is Auto from Job Interval/Timesheet. The denominator (available hours) needs standard hours per employee per period — derive from active Employee count * a configurable standard-hours constant (one setting), or from a simple capacity assumption. One light config value; otherwise Auto. Cron rollup by crew.
- **Refresh:** Weekly

### 11. Maintenance Recurring Revenue & Contract Health — 🟡 Semi
- **Definition:** Active maintenance contract count and annualized recurring maintenance revenue (MRR/ARR), plus churn = contracts lost in period / active contracts at start. Annualized value from invoicing frequency * per-visit/contract billing.
- **Why it matters:** Maintenance is the recurring, high-margin, counter-cyclical revenue that smooths the lumpy build business and compounds customer lifetime value. Growing the maintenance book is a core strategic lever for a fountain company.
- **Target:** Net contract count growth QoQ positive; churn < 10% annually; maintenance ARR trending up (set with owner).
- **Data source:** Sapphire Maintenance Contract (status=Active, default_frequency, invoicing_frequency, start/end dates); Sales Invoice generated from Maintenance Record on_submit for realized maintenance revenue.
- **Implementation:** Active-contract count and per-visit revenue are Auto. Annualized ARR needs a clear billing amount per contract; churn needs a contract end/renewal date to detect lapses — the maintenance contract has no explicit renewal_date field (documented gap). Add a renewal_date / lifecycle_stage field (light) to compute churn cleanly; count + realized revenue work today.
- **Refresh:** Monthly

### 12. Closed-Won Hand-Off Cycle Time & Backlog — 🟢 Auto
- **Definition:** Average business days from Opportunity Closed Won (custom_date_closed_won) to Project created (Project.creation where custom_opportunity set); plus count of Closed-Won opportunities still awaiting a project (custom_created_project IS NULL).
- **Why it matters:** The hand-off from sale to delivery is where deals stall and customer momentum is lost. A growing hand-off backlog means sold revenue is sitting idle and crews are starting late — a direct hit to delivery and cash timing.
- **Target:** Avg hand-off <= 2 business days; hand-off backlog (won-but-no-project) = 0 outstanding >3 days.
- **Data source:** Opportunity (status='Closed Won', custom_date_closed_won, custom_created_project); Project (custom_opportunity, creation). Closed-Won Hand-Off Engine already tracks this.
- **Implementation:** SELECT AVG(DATEDIFF(p.creation, o.custom_date_closed_won)) and COUNT(*) WHERE status='Closed Won' AND custom_created_project IS NULL. Fully computable today from the hand-off engine's fields. Daily cron; already partly surfaced via mcp closed_won_handoff_status tool.
- **Refresh:** Daily

### 13. Headcount & Revenue per Employee — 🟢 Auto
- **Definition:** Active headcount (Employee status='Active') and revenue per FTE = trailing-12-month recognized revenue / average active headcount. Optionally labor cost ratio = total Timesheet labor cost / revenue.
- **Why it matters:** Productivity per head is the clearest measure of whether the company is scaling efficiently or just adding cost. For a labor-intensive build/maintain business, revenue per employee is a board-level efficiency benchmark.
- **Target:** Revenue per FTE growing YoY; set absolute benchmark with owner.
- **Data source:** Employee (status='Active', department) stock doctype; Sales Invoice for revenue; Timesheet Detail for labor cost.
- **Implementation:** COUNT active Employees and divide trailing revenue. All stock doctypes/fields exist. No new fields. Quarterly/monthly cron snapshot. (Useful to split by department: design, build, field-service.)
- **Refresh:** Monthly

### 14. Customer Satisfaction Index (CSAT/escalation proxy) — 🟡 Semi
- **Definition:** Composite satisfaction signal: average Call Log.custom_csat_score (where score>0) blended with % of calls flagged high custom_escalation_risk and % negative custom_sentiment over the period. Reported as an index plus high-risk customer count.
- **Why it matters:** Satisfaction drives referrals, repeat builds, and maintenance retention — the lifeblood of a relationship-based custom fountain business. Today the only systematic signal is AI-scored calls; surfacing it keeps the customer voice on the exec screen.
- **Target:** Avg CSAT >= 4.0/5; high-escalation-risk calls < 5% (set with owner). Aspirational: stand up true NPS.
- **Data source:** Call Log (custom_csat_score, custom_escalation_risk, custom_sentiment) from Triton; Communication (SMS sentiment). No structured survey/NPS source exists (documented gap).
- **Implementation:** Call-based composite is Auto from Call Log fields. This is a PROXY — there is no real CSAT/NPS survey wired (gap). To get a true NPS, add a lightweight post-project/post-visit survey (one new 'Customer Survey' doctype: customer, project, score 0-10, comment) sent via existing SMS/email automation; until then ship the call-intelligence proxy and label it as such.
- **Refresh:** Weekly (proxy); per-survey for true NPS

### 15. Top Operational & Financial Risks (open count by severity) — 🟢 Auto
- **Definition:** Aggregated count of active risk flags across the business: projects with design has_warnings=1, overdue Project Process Steps (escalated), maintenance visits with out-of-range chemistry / warranty_rma_flag, projects below margin floor, AR >60 days overdue, QBO sync conflicts/failures, and non-compliant managed devices — grouped High/Medium/Low.
- **Why it matters:** A C-suite screen needs an exception lens, not just averages. One consolidated risk count tells the CEO where to look this week — a stalled hand-off, a bleeding project, a collections problem, or a compliance lapse — without opening six dashboards.
- **Target:** Zero High-severity items unaddressed >7 days; total open risks trending down.
- **Data source:** Water Feature Design (has_warnings); Project Process Step (overdue, escalate_overdue_steps task); Sapphire Maintenance Record (has_out_of_range_readings, warranty_rma_flag); Sales Invoice (overdue AR); QuickBooks Sync Log/Mapping (conflict_status, failed); Managed Device (compliance_status).
- **Implementation:** A single cron that runs ~7 COUNT queries (one per risk source, all existing fields) and writes a categorized risk roster into Executive KPI Snapshot, with drill-down links. Every source field already exists; this is pure aggregation. Severity mapping is a small config dict. Daily cron.
- **Refresh:** Daily

**Data gaps:**
- No plan/budget/target store: every 'vs plan' KPI (bookings, revenue, margin, win rate coverage) has no target to compare against. There is no Budget doctype linked to GL and no quota system. Blocks fully-Auto plan variance until a KPI Target doctype exists.
- No automated P&L / balance sheet snapshot: stock ERPNext reports must be run manually; net margin, gross margin, and cash KPIs require a custom GL-aggregation cron (buildable, but not present today).
- No per-project cost-of-goods rollup: labor (Timesheet), materials (PO/Purchase Invoice), travel (Travel Trip), and consumables are separate streams with no unified build-cost-to-date query, so true per-project gross margin needs a new joined rollup and consistent project tagging on all POs/PIs.
- No true CSAT/NPS capture: only AI-scored call sentiment/CSAT exists. No post-project or post-visit survey doctype, so customer satisfaction is a proxy, not a real Net Promoter Score.
- No reliable project actual-completion timestamp: on-time delivery at the project level must be inferred from Version history or process-step completion unless a custom_actual_completion_date is stamped on status change.
- Maintenance contract has no renewal/end-of-term field or lifecycle stage, so contract churn and ARR-at-risk cannot be computed cleanly.
- No cash-flow forecast: runway is backward-looking (trailing burn). Forward liquidity / milestone-billing forecast would need a predictive model not yet wired.
- Workforce available-hours denominator: utilization needs a standard-hours-per-employee assumption; there is no capacity/availability target doctype, only per-contract frequencies.
- Bookings value depends on Project Contract being authored and signed_on stamped consistently; deals closed-won without a Project Contract record would be missed by the bookings query.

**Recommended minimal manual entry:**
- Create a small 'KPI Target' doctype (fields: period_start, period_end, metric_key, target_amount/target_pct, owner) and have leadership enter quarterly plan figures for bookings, revenue, gross margin, win rate, and utilization. This single doctype unlocks every 'vs plan' KPI as Auto.
- Quarterly: confirm each active Project has custom_project_dollar_amount (contract value) populated — needed for accurate backlog; backfill blanks from the linked Project Contract.
- Add (light, one-time schema) a custom_actual_completion_date on Project stamped by an on-status-change hook, and a renewal_date + lifecycle_stage on Sapphire Maintenance Contract — for on-time delivery and maintenance churn respectively.
- Optional but high-value: stand up a one-field 'Customer Survey' doctype (score 0-10 + comment) auto-sent via existing SMS/email after project completion and after maintenance visits, to replace the call-sentiment CSAT proxy with a real NPS.
- Set one config constant for standard weekly working hours per field employee, to give the utilization KPI a clean denominator.

---
## Production (Build)

> Comprehensive KPI catalog for Sapphire Fountains' Production/Build department, covering the full job scope from hand-off through commissioning: build throughput & cycle time, on-time milestone delivery, budget-vs-actual on both dollars and labor hours, change-order volume/value, procurement lead time & material readiness, rework/punch-list, crew utilization & productivity, quality/first-pass yield, WIP, and schedule slippage. The data foundation is strong: the custom Project doctype already carries cost and hour budgets (custom_project_dollar_amount, custom_materials_budget, custom_time_budget_in_hours, custom_total_time_elapsed, custom_project_spend), schedule fields (expected_start_date/expected_end_date, custom_project_start_date/end), the Project Process Step gate table (with due_by/completed_on/SLA), and the SOW Project Contract (mobilization_date, substantial_completion_date, final_completion_date, not_to_exceed, revision = amendment/change-order counter, amended_from lineage, milestones). Procurement is fully traceable via the existing get_procurement_status() MR->RFQ->SQ->PO->PR->PI chain with dates and received_qty. Stock Task (status, percent_complete, exp_end_date) and Timesheet Detail (hours, amount, project) cover schedule and labor-actuals. 11 of 16 KPIs are Auto or Semi-Auto by reusing these sources via SQL/cron snapshots modeled on the existing Morning Briefing pattern. The honest gaps are fountain-specific quality and field-execution data that ERPNext never captures: punch-list/rework items, first-pass commissioning (water/leak/electrical) test results, and crew clock-in on BUILD projects (the Time Kiosk/Job Interval GPS system today is wired to maintenance visits, not build tasks). Those need a lightweight Build Punch Item child table, a Commissioning Test child table, and extending Job Interval clock-in to build Tasks. Recommend a daily 'Build Operations Briefing' snapshot doctype (mirroring Daily Briefing) so throughput, slippage, WIP-aging and budget-burn trends become queryable over time rather than point-in-time only.

### 1. Build Throughput (Fountains Completed per Period) — 🟢 Auto
- **Definition:** Count of Build-type Projects reaching 'Completed' status (or the 'Build Complete' process step status=Completed) within the period. Tracked weekly and monthly, optionally split by fountain class via custom_value_stream.
- **Why it matters:** The single clearest measure of how much the shop is actually shipping. Trends reveal capacity, seasonality (Utah build season), and whether sales hand-offs are converting into finished water features rather than piling up as WIP.
- **Target:** Set baseline from trailing 12 months, then target +10-15% YoY; e.g. ~3-5 completed builds/month in season. Set with owner.
- **Data source:** Project doctype: filter project_type='Build', status='Completed'; OR Project Process Step where step_title LIKE 'Build Complete%' and status='Completed', completed_on in period. Period date = completed_on (step) or the Project status-change timestamp from Version.
- **Implementation:** SQL: SELECT COUNT(*) FROM `tabProject` p WHERE p.project_type='Build' AND p.status='Completed' AND <completion_date> BETWEEN %(start)s AND %(end)s. Most reliable completion date is the 'Build Complete' Project Process Step.completed_on (join `tabProject Process Step` ps ON ps.parent=p.name AND ps.step_title LIKE 'Build Complete%' AND ps.status='Completed'). Snapshot weekly via a new scheduler_event in hooks.py modeled on briefing_run; render as a Dashboard Chart (timeseries, Weekly) using create_dashboard_chart.
- **Refresh:** Daily snapshot; chart shows weekly/monthly rollup

### 2. Average Build Cycle Time (Hand-off to Completion) — 🟢 Auto
- **Definition:** Mean calendar days from build start to substantial completion per completed Build project. Primary clock = SOW Contract.mobilization_date -> substantial_completion_date; fallback = Project custom_project_start_date (or 'Build Start' step completed_on) -> 'Build Complete' step completed_on.
- **Why it matters:** Cycle time is the heartbeat of a design-build shop. Shrinking it frees crews and cash, exposes process drag (permits, material waits, rework), and lets sales quote realistic timelines to customers.
- **Target:** Establish per fountain class (small residential vs. large commercial cascade differ hugely). Target a 10% reduction vs. trailing baseline; flag any build >150% of class median. Set with owner.
- **Data source:** Project Contract (template_key='sow'): mobilization_date, substantial_completion_date. Fallback: Project Process Step completed_on for 'Build Start'/'Build Complete'; Project.custom_project_start_date/custom_project_end_date.
- **Implementation:** SQL across completed builds: AVG(DATEDIFF(substantial_completion_date, mobilization_date)) from `tabProject Contract` where template_key='sow' and status='Signed' and substantial_completion_date IS NOT NULL, joined to Project. Where contract dates are null, COALESCE to the two process-step completed_on timestamps. Compute median per custom_value_stream class in Python (SQL AVG for the headline). Cron-snapshot into the Build Briefing doctype for trend.
- **Refresh:** Weekly

### 3. On-Time Milestone Completion Rate — 🟢 Auto
- **Definition:** % of build-phase Project Process Steps completed on or before their due_by date. = COUNT(steps where status='Completed' AND completed_on <= due_by) / COUNT(steps where status='Completed' AND due_by IS NOT NULL), filtered to build steps (Build Start, Build Complete, install/commissioning gates) on Build projects.
- **Why it matters:** Milestones are the contractual and operational promises (mobilization, substantial completion, final completion). Slippage here is what triggers customer escalations and liquidated-damages exposure on commercial jobs.
- **Target:** >=85% on-time, trending to >=90%. Set with owner.
- **Data source:** Project Process Step child table: status, completed_on, due_by, step_title, sla_business_days. due_by is already auto-stamped from the SLA engine.
- **Implementation:** SQL on `tabProject Process Step`: SUM(CASE WHEN status='Completed' AND completed_on<=due_by THEN 1 ELSE 0 END)/SUM(CASE WHEN status='Completed' AND due_by IS NOT NULL THEN 1 END), filtered to build-related step_titles and parents that are Build projects. The escalate_overdue_steps daily task already computes overdue; extend it to also write the on-time ratio into the snapshot. Number Card via create_dashboard_chart/number_card fixture.
- **Refresh:** Daily

### 4. Schedule Slippage (Days Past Promised Completion) — 🟢 Auto
- **Definition:** For each in-flight or just-completed Build project: days between actual completion and the promised date. In-flight overdue = today - planned completion (if not yet done). Planned = Contract.substantial_completion_date (or anticipated_completion_date / Project.expected_end_date). Reported as avg slippage days and % of builds finishing late.
- **Why it matters:** Distinguishes 'we deliver but always late' from 'we deliver on time.' Aging WIP past its promise date is the earliest financial and reputational warning sign in construction.
- **Target:** Avg slippage <=5 days; <=20% of builds late. Zero builds >30 days overdue without an escalation note. Set with owner.
- **Data source:** Project Contract.substantial_completion_date / anticipated_completion_date; Project.expected_end_date, custom_project_end_date; 'Build Complete' Process Step.completed_on; Project.status (to know if still open).
- **Implementation:** SQL: for completed builds, DATEDIFF(actual_complete, planned_complete); for open builds with status!='Completed' and planned_complete<today, DATEDIFF(today, planned_complete) as overdue_days. COALESCE the planned date through contract -> expected_end_date -> custom_project_end_date. Surface a 'Builds Overdue' Number Card and a per-project overdue list on the existing project_dashboard page (already has the bulk Project+Task query to extend).
- **Refresh:** Daily

### 5. Budget vs Actual Cost Variance (Cost Performance) — 🟡 Semi
- **Definition:** Per Build project: (Actual cost - Budget) / Budget. Budget = custom_project_dollar_amount (contract value) or custom_materials_budget for the materials slice. Actual = custom_project_spend, validated against the sum of submitted Purchase Invoices + Timesheet labor cost linked to the project. Reported as portfolio-weighted variance and count of builds >budget.
- **Why it matters:** Fixed-price fountain builds live or die on cost control. A creeping materials/labor overrun erodes margin invisibly until invoicing; catching it mid-build lets the PM correct course or raise a change order.
- **Target:** Portfolio variance within +/-5% of budget; <15% of builds exceed budget by >10%. Set with owner.
- **Data source:** Project: custom_project_dollar_amount, custom_materials_budget, custom_project_spend. Ground-truth actuals: Purchase Invoice (project link) grand_total + Timesheet Detail.amount where project=X. QBO-synced PIs are the system of record.
- **Implementation:** Auto for the comparison query: actual_materials = SUM(`tabPurchase Invoice`.grand_total) over PIs linked to project + SUM(`tabPurchase Invoice Item`.amount where project=X) for line-level; actual_labor = SUM(`tabTimesheet Detail`.amount where project=X). Compare to budget fields. Semi-Auto because custom_project_spend must either be populated by a nightly cron that writes the computed actual back to the Project (recommended), or PMs must keep custom_materials_budget filled at hand-off. Build the cron: daily job recompute_project_actuals() updating custom_project_spend = materials PI + labor; then variance is pure SQL.
- **Refresh:** Daily

### 6. Labor Hours Budget vs Actual (Hours Performance) — 🟢 Auto
- **Definition:** Per Build project: actual logged hours / budgeted hours. Budget = custom_time_budget_in_hours. Actual = SUM(Timesheet Detail.hours) for the project (and/or custom_total_time_elapsed rolled up from Tasks). Portfolio metric = total actual hours / total budgeted hours across active builds.
- **Why it matters:** Labor is the most variable build cost and the hardest to see until payroll. Hours-burn vs. budget is a leading indicator of margin erosion and of crews stuck on a job longer than estimated (often a rework or design-gap signal).
- **Target:** Actual hours within 100-110% of budget; flag any build >125%. Set with owner.
- **Data source:** Project.custom_time_budget_in_hours (budget) and custom_total_time_elapsed (duration rollup). Actual: Timesheet Detail.hours where project=X (already rolled by update_elapsed_time_daily task).
- **Implementation:** SQL: SELECT p.name, p.custom_time_budget_in_hours AS budget_hrs, SUM(td.hours) AS actual_hrs FROM `tabProject` p JOIN `tabTimesheet Detail` td ON td.project=p.name WHERE p.project_type='Build' GROUP BY p.name. The daily update_elapsed_time_daily task already aggregates task elapsed_time -> custom_total_time_elapsed, so even projects without timesheets get an actual. Ratio + flag list as Number Card. Only gap: budget hours must be entered at hand-off (single existing field).
- **Refresh:** Daily

### 7. Change-Order Volume & Value — 🟡 Semi
- **Definition:** Count and dollar value of contract amendments per Build project and across the portfolio. Volume = number of SOW/Owner Project Contract revisions (revision>0, tracked via amended_from lineage). Value = delta in not_to_exceed / total_contract_value / milestones_total between consecutive revisions. Also reported as change-order $ as % of original contract value.
- **Why it matters:** Change orders are where fountain projects make or lose money and where scope-creep hides. High CO frequency points to weak upfront design/estimating; CO value recovery (or lack of it) directly drives realized margin.
- **Target:** CO value <10% of original contract value on average; 100% of scope changes captured as a contract revision (no uncaptured verbal changes). Set with owner.
- **Data source:** Project Contract: revision (increments per amendment), amended_from (lineage), not_to_exceed / total_contract_value / milestones_total. Linked to Project via the project field.
- **Implementation:** Auto for volume: COUNT amendments by following amended_from chains per project (revision>0). Auto for value IF each scope change is amended through the contract (delta of total_contract_value/not_to_exceed across the revision chain). Semi-Auto because today nothing forces a scope change to become a contract amendment, and there is no explicit change_reason. Lightest fix: add two fields to Project Contract -- 'change_order_reason' (Select: Customer-requested / Site condition / Design error / Other) and a checkbox 'is_change_order' set on amend -- so CO value can be attributed by cause. Then portfolio CO% = SUM(value deltas)/SUM(original values) via SQL.
- **Refresh:** Daily

### 8. Procurement Lead Time (Material Request to Receipt) — 🟢 Auto
- **Definition:** Mean days from Material Request transaction_date to Purchase Receipt posting_date for build materials, per item chain and per project. Also reported per critical item class (pumps, nozzles, control panels, basin liner/fabrication).
- **Why it matters:** Long-lead items (custom pumps, control panels, fabricated stainless basins) are the #1 cause of build schedule slippage. Knowing real lead times lets PMs order earlier and lets the schedule reflect reality instead of optimism.
- **Target:** Establish per item class; flag any chain where receipt lags MR by >planned lead time. Target 90% of critical items received before their need-by date. Set with owner.
- **Data source:** Existing get_procurement_status() chain: Material Request.transaction_date, Purchase Order.transaction_date, Purchase Receipt.posting_date, plus the per-item completion_percentage already computed. All joined in the existing SQL in project_enhancements/__init__.py.
- **Implementation:** Extend the existing procurement chain SQL to also return DATEDIFF(pr.posting_date, mr.transaction_date) AS lead_days per item, and DATEDIFF(po.transaction_date, mr.transaction_date) AS rfq_to_po_days to isolate where time is lost. Aggregate AVG lead_days by item_group (pumps/nozzles/panels). No new data needed -- all dates already in the chain. Add an item_group join for class breakdown. Snapshot weekly.
- **Refresh:** Weekly

### 9. Material Readiness at Build Start — 🟡 Semi
- **Definition:** % of a build's required materials received (Purchase Receipt/Stock Entry done) by the time the build's mobilization/start gate fires. Per project = received line items / total required line items at start; portfolio = avg readiness across builds starting in period. Also surfaces 'builds starting with <X% materials on hand'.
- **Why it matters:** Starting a fountain build without the pump, liner, or panel on site means crews idle or re-mobilize -- the most expensive form of waste. Readiness % is the gate that prevents premature mobilization.
- **Target:** >=90% of required materials received before mobilization; zero builds mobilizing <70% ready. Set with owner.
- **Data source:** get_procurement_status() per project (received_qty vs ordered_qty, stage) evaluated as of Contract.mobilization_date / 'Build Start' Process Step.completed_on. Project Process Step gives the start timestamp.
- **Implementation:** Auto query: at the moment 'Build Start' step completes (or mobilization_date), compute SUM(received_qty)/SUM(ordered_qty) from the procurement chain for that project. Best implemented as a hook on the Build Start step completion that snapshots readiness into the Build Briefing (point-in-time, because procurement keeps changing). Semi-Auto only because 'required materials' = what was actually requisitioned; if a PM forgets to raise an MR for an item, it won't count. Mitigation: tie required materials to the BOM/build deliverables. Lightest version reuses existing MR/PO data with no new fields.
- **Refresh:** Event (on Build Start) + daily for in-flight

### 10. First-Pass Yield (Commissioning / Water Test) — 🔴 Manual
- **Definition:** % of completed builds that pass commissioning (fill, leak test, pump/flow verification, electrical/GFCI check, nozzle pattern) on the first attempt with no failed test requiring re-work and re-test. = builds passing all commissioning checks first time / total builds commissioned.
- **Why it matters:** For a water feature, commissioning is the moment of truth -- leaks, wrong flow, tripping GFCIs, or off-spec nozzle patterns mean tear-back and re-test. First-pass yield is the purest quality signal and directly drives rework cost and customer first-impression.
- **Target:** >=80% first-pass, trending to >=90%. Set with owner.
- **Data source:** NONE TODAY -- commissioning results are not captured in any doctype. Closest existing signal is the design-side Water Feature Design.calc_results warnings, which is pre-build, not field commissioning.
- **Implementation:** Add a 'Build Commissioning Test' child table on Project (or a standalone submittable doc per build) with rows: test_type (Select: Fill/Leak/Flow-GPM/Electrical-GFCI/Nozzle Pattern/Light Function), result (Pass/Fail), measured_value, retest_required (Check), notes, tested_by, tested_on. First-pass yield then = builds where every row passed on first submission. The field crew fills this on a phone/tablet at commissioning (one short form). Once the table exists, the KPI is fully Auto via SQL.
- **Refresh:** Event (on commissioning) + weekly rollup

### 11. Rework / Punch-List Volume & Closure Rate — 🔴 Manual
- **Definition:** Per build: count of punch-list/rework items raised, count closed, and avg days-to-close. Portfolio = punch items per build, % closed before final completion, and rework-hours as a share of total build hours.
- **Why it matters:** The punch list is where 'substantially complete' becomes 'actually done.' Punch volume measures build quality; slow closure delays final payment milestones and customer sign-off. Rework hours quantify the cost of doing it twice.
- **Target:** <=5 punch items per build; 100% closed before final_completion_date; rework hours <5% of build hours. Set with owner.
- **Data source:** NONE structured today -- punch items would live in free-text custom_notes_for_scheduling or task comments. Stock Task could proxy a punch item but isn't categorized as rework.
- **Implementation:** Add a 'Build Punch Item' child table on Project: description, category (Select: Leak/Finish/Electrical/Plumbing/Cosmetic/Other), raised_on, raised_by, status (Open/Closed), closed_on, rework_hours (Float). Then Auto KPIs: COUNT open/closed, AVG DATEDIFF(closed_on, raised_on), SUM(rework_hours). Alternatively, reuse stock Task with a custom_task_category='Rework/Punch' flag (lighter, no new doctype) -- then query Tasks where custom_task_category='Punch' grouped by project/status. Recommend the Task-flag approach to avoid a new child table.
- **Refresh:** Daily once captured

### 12. Crew Utilization (Build Labor as % of Available Hours) — 🟡 Semi
- **Definition:** Productive build hours logged / total available crew clock hours, per crew member and crew, over the period. Productive = Timesheet/Job Interval hours booked to a Build project Task. Available = scheduled work hours (clock-in span).
- **Why it matters:** Tells you whether build crews are actually turning wrenches on billable fountain work vs. idle, travelling, or waiting on materials. Drives staffing and quoting; low utilization with full payroll is silent margin loss.
- **Target:** Field-build utilization 75-85% (allowing travel/setup). Set with owner.
- **Data source:** Timesheet Detail.hours (project, task, employee) for booked build hours. Job Interval (start_time, end_time, total_paused_seconds) + Time Kiosk Log for clock hours -- BUT today these are wired to maintenance visits, not build projects.
- **Implementation:** Numerator is Auto: SUM(Timesheet Detail.hours) where project is a Build project, by employee/week. Denominator (available clock hours) needs the Time Kiosk/Job Interval clock-in to be used on BUILD jobs too -- currently Job Interval links to maintenance projects via the maintenance workflow. Extend the Time Kiosk PWA / Job Interval to allow clock-in against any Project (build Tasks), then utilization = build hours / clocked hours is fully Auto. Interim Semi-Auto: use Timesheet hours / a standard 40-hr (or scheduled) week per employee as the denominator -- needs only employee scheduled-hours, which HR already holds.
- **Refresh:** Weekly

### 13. Build Labor Productivity (Earned Value per Hour) — 🟢 Auto
- **Definition:** Earned contract value or completed scope per build labor hour. Simple form = SUM(custom_project_dollar_amount of builds completed in period) / SUM(build hours logged in period). Project form = (percent_complete x contract value) / hours logged to date (a CPI-style earned-value rate).
- **Why it matters:** Normalizes throughput by effort. A crew that finishes more fountain value per hour is more productive; declining $/hr signals rework, under-quoting, or capability gaps -- independent of how busy people look.
- **Target:** Establish baseline $/build-hour; target flat-to-rising trend, flag >15% drop quarter-over-quarter. Set with owner.
- **Data source:** Project.custom_project_dollar_amount, percent_complete; Timesheet Detail.hours per project. All present.
- **Implementation:** SQL: portfolio = SUM(custom_project_dollar_amount for builds with Build-Complete step in period) / SUM(Timesheet Detail.hours in period for those projects). Per-project earned value = percent_complete/100 * custom_project_dollar_amount, divided by hours-to-date. Snapshot monthly into Build Briefing for trend; render as timeseries chart. No new fields.
- **Refresh:** Monthly

### 14. Work-in-Progress (WIP) Count & Aging — 🟢 Auto
- **Definition:** Number of Build projects currently in-flight (status active, not Completed/Cancelled) and their age distribution = today - build start, bucketed (0-30 / 31-60 / 61-90 / >90 days). Also total WIP contract value = SUM(custom_project_dollar_amount) of active builds.
- **Why it matters:** WIP is tied-up cash and crew capacity. A growing pile of aged builds means hand-offs are outpacing completions -- the bottleneck the whole company feels. Aged WIP often hides a stuck job (permit, material, rework) nobody is escalating.
- **Target:** WIP count <= crew capacity (set with owner); zero builds aged >90 days without an escalation note; flat or shrinking aged-WIP $ trend.
- **Data source:** Project: status, project_type='Build', custom_project_start_date / 'Build Start' Process Step.completed_on (start clock), custom_project_dollar_amount. The project_dashboard page already bulk-queries active vs inactive projects.
- **Implementation:** SQL: SELECT bucket, COUNT(*), SUM(custom_project_dollar_amount) FROM `tabProject` WHERE project_type='Build' AND status NOT IN ('Completed','Cancelled') GROUP BY age bucket (CASE on DATEDIFF(today, start)). Add as a section to the existing project_dashboard page (get_project_data already returns active projects) and as Number Cards (Active Builds, Aged WIP >90d). Daily snapshot for trend.
- **Refresh:** Daily

### 15. Milestone-to-Cash: Build Billing Realization — 🟡 Semi
- **Definition:** % of contract value billed/collected vs % of build physically complete, per project. Billing realization = (SUM invoiced via Sales Invoice or paid Stripe/QBO for the project) / (percent_complete x contract value). Flags builds where work is ahead of billing (cash drag) or billing ahead of work (over-billing risk).
- **Why it matters:** Fountain builds are milestone-billed (deposit, mobilization, substantial, final). If construction races ahead of milestone invoicing, the company funds the customer's project; if billing leads work, it risks disputes. This ties Build progress to Finance.
- **Target:** Billing within +/-10% of physical completion at any checkpoint; no build >20% under-billed past mobilization. Set with owner.
- **Data source:** Project.percent_complete, custom_project_dollar_amount; Project Contract.milestones (percent/amount, due_upon); Sales Invoice linked to project (grand_total); Payment Entry/Stripe Payment for collected. QBO-synced.
- **Implementation:** Auto comparison query once invoices carry the project link: invoiced = SUM(`tabSales Invoice`.grand_total where project=X, docstatus=1); earned = percent_complete/100 * custom_project_dollar_amount; realization = invoiced/earned. Semi-Auto because Sales Invoices must reliably set the project field (or map to a contract milestone) -- today the milestone->invoice link is manual. Lightest fix: ensure milestone-triggered Sales Invoices stamp project, then the KPI is pure SQL. Surface per-project under/over-billed list for the PM + Finance.
- **Refresh:** Daily

**Data gaps:**
- No commissioning/test-result capture: leak test, flow (GPM) verification, electrical/GFCI check, and nozzle-pattern sign-off are not recorded anywhere, so first-pass yield and build quality are invisible. This is the biggest fountain-specific gap.
- No structured punch-list / rework record: rework items live (if at all) in free-text notes or uncategorized Tasks, so rework volume, closure time, and rework-hours cannot be measured.
- Change orders are not first-class: scope changes are only visible if someone amends the Project Contract (revision/amended_from). There is no change_order_reason or is_change_order flag, so CO cause-attribution and 'uncaptured verbal change' leakage cannot be tracked.
- Build crew clock-in is not wired: Job Interval / Time Kiosk GPS time tracking targets maintenance visits, not Build project Tasks, so true crew utilization (productive hours / clocked hours on builds) has no denominator without extending clock-in to build work.
- custom_project_spend is a manual/derived field with no enforced population: cost-variance is only trustworthy if a cron writes computed actuals (PI + labor) back to it, or PMs keep budgets current.
- Material 'required' set isn't anchored to a BOM: readiness % is computed from whatever MRs were raised, so a forgotten requisition silently inflates readiness. Tying required materials to build deliverables / a BOM would close this.
- Sales Invoice -> project / contract-milestone linkage is inconsistent: billing-realization needs invoices to reliably carry the project link (or map to a milestone), which is currently manual.
- No persisted Build KPI history doctype: every metric above is point-in-time from live tables. Without a daily snapshot (like Daily Briefing), throughput, slippage, WIP-aging, and budget-burn trends can't be charted over time.

**Recommended minimal manual entry:**
- Commissioning test results: at fountain start-up, the field crew fills one short form (new Build Commissioning Test child table on Project) -- test_type (Fill/Leak/Flow-GPM/Electrical-GFCI/Nozzle Pattern/Light), Pass/Fail, measured value, retest_required, notes. ~6 rows per build, captured once. Unlocks first-pass yield.
- Punch-list items: capture rework/punch items as they arise -- lightest path is flagging the existing stock Task with a custom_task_category='Punch/Rework' + category and rework_hours, rather than a new doctype. Unlocks punch volume, closure rate, and rework-hours.
- Budget entry at hand-off: PMs set custom_materials_budget, custom_time_budget_in_hours, and confirm custom_project_dollar_amount when the build is created (existing fields, no new schema) so all budget-vs-actual KPIs have a denominator.
- Change-order attribution: when amending a Project Contract for a scope change, set a new change_order_reason (Customer-requested / Site condition / Design error / Other). One dropdown per amendment; everything else (value delta, count) is automatic.
- Crew clock-in on builds: have field crews clock in/out against Build project Tasks via the existing Time Kiosk PWA (requires extending Job Interval to accept build Tasks). No new data entry beyond the clock-in they already do for maintenance.

---
## Operations (Field-Service / Maintenance / Workforce)

> A 17-KPI catalog covering the full Operations scope for a fountain design-build-maintain business: contract fulfillment and visit completion, seasonal/SLA adherence, technician utilization and route/drive efficiency, water-quality (chemistry) compliance, callback/redo rate, fleet/device uptime and compliance, inventory accuracy and stockouts, travel cost, field safety, timesheet/labor capture, and asset booking utilization. Of the 17, 9 are Auto (computable today from Sapphire Maintenance Record, Sapphire Chemistry Reading, Job Interval, Managed Device, Travel Trip, Asset Booking, Inventory Count Session via SQL + a nightly snapshot cron modeled on the existing Daily Briefing pattern), 6 are Semi-Auto (each needs one new field or light tagging — a scheduled-date stamp on Maintenance Record, an idle-radius geofence on Job Interval GPS, an item min-level, a callback flag, a route-sequence stamp, or odometer capture), and only 2 are Manual (field-incident safety reporting and a per-visit photo/QA spot-check), each with the lightest-weight capture specified. The single highest-leverage build is one new field — a planned/scheduled visit date on the Maintenance Record draft — which unlocks both SLA on-time adherence and seasonal-window adherence as Auto KPIs. Recommend deploying these as a fixtures-based "Operations" Dashboard plus a pre-computed nightly KPI snapshot (Daily Briefing operations variant) so the numbers are durable, trendable, and TV-wall/email deliverable, since most metrics today require runtime SQL with no historical series.

### 1. Visit Completion Rate — 🟢 Auto
- **Definition:** Of all maintenance visits drafted/scheduled in the period, the % that were submitted (docstatus=1) by period end. = count(Sapphire Maintenance Record where docstatus=1) / count(all Sapphire Maintenance Record drafted for the period) * 100.
- **Why it matters:** Recurring maintenance is contracted, billable revenue per visit (Per Visit invoicing auto-drafts a Sales Invoice on submit). Every drafted-but-unsubmitted visit is a missed service obligation and lost invoice, and a churn/retention risk on the maintenance contract.
- **Target:** 98% of scheduled visits submitted within the period
- **Data source:** Sapphire Maintenance Record (docstatus, creation, project, maintenance_contract); predictive_maintenance_scheduling daily task creates the drafts (docstatus=0).
- **Implementation:** SQL: SELECT SUM(docstatus=1)/COUNT(*) FROM `tabSapphire Maintenance Record` WHERE creation BETWEEN %s AND %s. Add a Number Card + Dashboard Chart (Group By status, weekly) to a new Operations dashboard fixture. Snapshot nightly into a Daily Briefing operations variant (model on api/briefing.py scheduled_briefing_run).
- **Refresh:** Daily snapshot (nightly cron) + realtime on Day Board

### 2. On-Time Visit SLA Adherence — 🟡 Semi
- **Definition:** % of submitted visits completed on or before their planned/scheduled date. = count(records where submit date <= scheduled_visit_date) / count(submitted records) * 100.
- **Why it matters:** Fountains in commercial/HOA settings degrade fast when overdue (algae, scale, pump strain). On-time service is the core promise of a maintenance contract and the leading churn indicator; no SLA metric exists today.
- **Target:** 95% on-time; zero visits more than 3 days past scheduled date
- **Data source:** Sapphire Maintenance Record (creation/modified as completion proxy) + NEW field scheduled_visit_date stamped by predictive_maintenance_scheduling when the draft is generated.
- **Implementation:** Add Date field `scheduled_visit_date` to Sapphire Maintenance Record (via after_migrate custom-field sync, same pattern as existing custom fields). Have predictive_maintenance_scheduling stamp it from the contract frequency + next_visit_dates math it already computes. Then SQL: AVG(DATE(modified) <= scheduled_visit_date) over submitted records. This one field also feeds Seasonal Window Adherence below.
- **Refresh:** Daily snapshot

### 3. Seasonal Visit Window Adherence — 🟢 Auto
- **Definition:** % of seasonal startup/winterization visits completed within their target month. = count(seasonal records submitted in target month) / count(seasonal visits due that year) * 100.
- **Why it matters:** Winterization done late risks freeze-cracked plumbing/pumps (warranty exposure); spring startup done late means an unusable fountain in peak season. These are the highest-liability visits of the year for a cold-climate (Utah) fountain operator.
- **Target:** 100% of winterizations before first hard freeze; 100% of startups within target month
- **Data source:** Sapphire Maintenance Record (visit_label, modified) + Sapphire Maintenance Contract (seasonal_startup, startup_month, winterization, winterization_month, startup_last_generated_year, winterization_last_generated_year) + Sapphire Seasonal Visit child (target_month, last_generated_year).
- **Implementation:** SQL joining submitted records WHERE visit_label IS NOT NULL against contract seasonal config; compare MONTH(modified) to startup_month/winterization_month/target_month. Surface as a seasonal-window Number Card that only matters Mar-May and Sep-Nov. Already fully sourced — visit_label is stamped on seasonal drafts.
- **Refresh:** Daily snapshot during seasonal windows

### 4. Technician Billable Utilization — 🟢 Auto
- **Definition:** Productive on-site labor hours / total clocked hours per technician. = SUM(Job Interval (end_time - start_time - total_paused_seconds) on maintenance projects) / SUM(all Job Interval clocked hours) * 100.
- **Why it matters:** Labor is the largest cost in field service. Utilization reveals whether techs are on tools vs. idle/driving, and is the basis for capacity planning and headcount decisions.
- **Target:** 65-75% billable utilization (field-service benchmark, accounting for drive/admin)
- **Data source:** Job Interval (employee, project, start_time, end_time, total_paused_seconds, status, time_category); maintenance projects identified via active Sapphire Maintenance Contract.project.
- **Implementation:** SQL: per employee, SUM(TIMESTAMPDIFF(SECOND,start_time,end_time) - total_paused_seconds) for completed intervals, split by whether project is in (SELECT project FROM `tabSapphire Maintenance Contract` WHERE status='Active'). Snapshot weekly per employee. Reuses the same interval math as create_timesheet() in maintenance_workflow.py.
- **Refresh:** Weekly snapshot

### 5. Timesheet / Labor Capture Completeness — 🟢 Auto
- **Definition:** % of completed job intervals that successfully produced a Timesheet. = 1 - count(Job Interval where sync_status IN ('Pending','Failed') and status='Completed') / count(completed intervals). Companion: count of open intervals >1h with no Maintenance Record.
- **Why it matters:** Uncaptured labor = unbilled revenue and broken cost accounting. A failed/pending sync means a visit's hours never hit a Timesheet or the auto-drafted Sales Invoice, silently leaking margin.
- **Target:** >99% synced; zero intervals stuck Pending >24h
- **Data source:** Job Interval (sync_status, sync_attempts, status, maintenance_nudge_sent); Sapphire Maintenance Record auto-creates the Timesheet on submit (create_timesheet in maintenance_workflow.py).
- **Implementation:** SQL count by sync_status. The hourly nudge_unsubmitted_maintenance_forms task already finds open intervals >1h without a Record; expose its count as a Number Card. Alert when Failed > 0. No new fields needed.
- **Refresh:** Hourly (piggyback existing hourly nudge task)

### 6. Chemistry Out-of-Range Incident Rate — 🟢 Auto
- **Definition:** % of submitted visits where at least one water-chemistry reading was out of spec. = count(Sapphire Maintenance Record where has_out_of_range_readings=1) / count(submitted records) * 100. Drill-down: top out-of-range reading types.
- **Why it matters:** Water chemistry (pH, chlorine/biocide, scale) is the core deliverable of fountain maintenance. A rising out-of-range rate signals dosing problems, equipment failure, or visit-quality issues before they become algae blooms, surface staining, or customer complaints.
- **Target:** <10% of visits with any out-of-range reading; trend down month-over-month
- **Data source:** Sapphire Maintenance Record (has_out_of_range_readings) and Sapphire Chemistry Reading child (reading, reading_value, out_of_range, min_value, max_value, serial_no) — out_of_range auto-set on submit.
- **Implementation:** SQL: SELECT reading, COUNT(*) FROM `tabSapphire Chemistry Reading` WHERE out_of_range=1 AND parent IN (submitted records in period) GROUP BY reading. Already computed flag — just aggregate. Add Group-By Dashboard Chart by reading type and a per-feature (serial_no) hotspot list.
- **Refresh:** Daily snapshot

### 7. Chemistry Data Capture Compliance — 🟢 Auto
- **Definition:** % of mandatory chemistry readings actually captured with a value on submitted visits. = count(readings with reading_value not null where is_mandatory=1) / count(mandatory reading rows) * 100. (Submit is blocked on empty mandatory readings, so this trends near 100% and surfaces template/process gaps.)
- **Why it matters:** You cannot manage water quality you do not measure. Capture compliance proves the chemistry KPI above is trustworthy and protects the company in warranty/liability disputes by documenting every visit's readings.
- **Target:** 100% mandatory readings captured (enforced); monitor optional-reading capture trend
- **Data source:** Sapphire Chemistry Reading (reading_value, is_mandatory, photo) + Sapphire Maintenance Template / Section Item (defines expected/mandatory readings).
- **Implementation:** SQL comparing captured rows vs. template-expected rows per submitted Record. Because submit is blocked on empty mandatory readings, focus the metric on optional-reading and photo-attach capture rate (count(photo not null)/count). Pure query; no new field.
- **Refresh:** Weekly snapshot

### 8. Callback / Redo Rate — 🟡 Semi
- **Definition:** % of completed visits that required a return trip to the same feature within 14 days for the same issue. = count(records flagged is_callback) / count(submitted records) * 100. Proxy until flagged: count of repeat submitted visits to the same project+serial_no within 14 days outside the scheduled frequency.
- **Why it matters:** Callbacks are pure margin destroyers — unpaid drive time, labor, and consumables, plus a customer-trust hit. The single best leading indicator of field-quality problems in a service business.
- **Target:** <5% callback rate
- **Data source:** Sapphire Maintenance Record (project, serial_no, modified, warranty_rma_flag) + NEW checkbox is_callback / Link to original_record.
- **Implementation:** Lightest path: add a Check field `is_callback` to Sapphire Maintenance Record (techs tick it on the Visit Wizard when returning for a redo). Until adoption, compute an Auto proxy via self-join: two submitted records, same project+serial_no, within 14 days, where the second is not a scheduled-frequency visit. warranty_rma_flag already isolates warranty-driven returns to exclude from the redo count.
- **Refresh:** Weekly snapshot

### 9. Device / Mobile Fleet Compliance Rate — 🟢 Auto
- **Definition:** % of active (non-retired) managed devices in Compliant posture. = count(Managed Device where compliance_status='Compliant') / count(status NOT IN ('Retired','Lost/Stolen')) * 100. Companion: count Non-Compliant with last_checked_on > 30 days.
- **Why it matters:** Techs run the Time Kiosk PWA, Visit Wizard, GPS clock-in, and client sign-off on these devices. A non-compliant or unmanaged phone is a data-security gap (customer PII, signatures) and risks field workflow failure mid-visit.
- **Target:** >95% Compliant; zero Non-Compliant unremediated >30 days
- **Data source:** Managed Device (compliance_status, status, last_checked_on, mdm_link_state, mdm_last_seen, mdm_provider) — synced hourly by mdm_integration (Miradore/Action1).
- **Implementation:** SQL count by compliance_status filtered to active devices. Reuse existing device_fleet_dashboard cards; add a Non-Compliant-aging card (compliance_status='Non-Compliant' AND last_checked_on < CURDATE()-30). Already fed by hourly sync_devices.
- **Refresh:** Hourly (provider sync) + daily snapshot

### 10. Device Provider Sync Freshness (Fleet Uptime Proxy) — 🟢 Auto
- **Definition:** % of Managed devices seen by their MDM provider within the last 24h. = count(mdm_link_state='Managed' AND mdm_last_seen > now-24h) / count(mdm_link_state='Managed') * 100.
- **Why it matters:** A managed device that has gone dark (lost, off, broken, out of coverage) is effectively down — that tech may be unable to clock in or file visits. Sync freshness is the closest available proxy for in-field device uptime.
- **Target:** >90% of managed devices seen within 24h on a working day
- **Data source:** Managed Device (mdm_link_state, mdm_last_seen, mdm_provider, assigned_to_employee).
- **Implementation:** SQL: SELECT mdm_provider, AVG(mdm_last_seen > NOW()-INTERVAL 24 HOUR) FROM `tabManaged Device` WHERE mdm_link_state='Managed' GROUP BY mdm_provider. Number Card + stale-device list joined to assigned_to_employee so a supervisor knows whose phone went dark. No new field.
- **Refresh:** Daily snapshot

### 11. Inventory Count Accuracy — 🟢 Auto
- **Definition:** % of counted consumable lines that matched system stock at the last count session (no adjustment needed). = 1 - count(Inventory Count Line with variance != 0) / count(counted lines) * 100. Companion: $ value of Stock Reconciliation adjustment generated.
- **Why it matters:** Truck and warehouse consumable accuracy (chlorine, sequestrants, clarifiers, filters, nozzles) drives whether a tech arrives able to complete the visit. Shrinkage and miscounts cause aborted visits, callbacks, and untracked COGS.
- **Target:** >97% line accuracy; shrinkage variance <2% of counted value
- **Data source:** Inventory Count Session (status, start_time, end_time, counted_by) + Inventory Count Line (scanned/counted qty) + the draft Stock Reconciliation auto-created on finalization (inventory_count_session.py on_submit) + Storage Location.
- **Implementation:** SQL comparing Inventory Count Line counted qty to system qty (or directly summing the generated Stock Reconciliation difference rows / amount). Aggregate by Storage Location for per-truck/per-bin shrinkage. Already produced on session finalization; just report on it.
- **Refresh:** Per count session (event-driven) + monthly rollup

### 12. Consumable Stockout Risk — 🟡 Semi
- **Definition:** Count of field-service consumable items at or below reorder level across storage locations. = count(items where current_qty <= reorder_level) by Storage Location.
- **Why it matters:** A stocked-out biocide or filter on a truck means a failed/partial visit and a guaranteed return trip. Proactive replenishment prevents the most avoidable category of callback and protects visit completion.
- **Target:** Zero stockouts on A-class consumables; <5% of SKUs below reorder at any time
- **Data source:** Bin / Item stock per Storage Location warehouse + Sapphire Maintenance Consumable child (item_code, consumable usage history) + NEW reorder_level per item/location. The weekly suggest_truck_restocks task already models this.
- **Implementation:** Use ERPNext Item.reorder_level (or set a per-warehouse Reorder row) — light config, no code. SQL: Bin.actual_qty <= reorder_level grouped by warehouse/Storage Location. Drive a daily stockout-risk card and feed the existing weekly suggest_truck_restocks output. Burn-rate from Sapphire Maintenance Consumable history refines the threshold.
- **Refresh:** Daily card + weekly restock suggestion

### 13. Travel Cost per Project / Visit — 🟢 Auto
- **Definition:** Total actual field-travel cost attributed to a project or customer over a period. = SUM(Travel Trip.total_actual_cost) grouped by project/customer; and per-visit = trip cost / visits served. Mileage component = SUM(Trip Mileage.amount).
- **Why it matters:** For dispersed fountain sites, windshield time and mileage are a major hidden cost of field service. Travel cost per project exposes unprofitable far-flung contracts and bad routing, and feeds maintenance contract repricing.
- **Target:** Travel <8% of maintenance contract revenue per project; flag any project where it exceeds 15%
- **Data source:** Travel Trip (total_actual_cost, total_mileage_amount, project, customer, status) + Trip Mileage child (distance, rate, amount) + Trip Expense child; reports travel_spend_by_category.py and travel_trip_cost_summary.py already exist.
- **Implementation:** SQL: SUM(total_actual_cost) FROM `tabTravel Trip` GROUP BY project/customer for completed trips in period; divide by visit count from Sapphire Maintenance Record for the same project. Cross-link the existing travel reports into the Operations dashboard. No new field.
- **Refresh:** Monthly snapshot

### 14. Route / Drive Efficiency (On-Site Time Ratio) — 🟡 Semi
- **Definition:** Share of the field workday spent on-site vs. driving/idle. Approx = SUM(on-site interval hours) / SUM(span from first clock-in to last clock-out per tech-day). Companion: idle GPS time (Time Kiosk Log points showing no movement outside a job geofence).
- **Why it matters:** Drive time is unbillable. A low on-site ratio means poor routing/clustering of nearby fountain sites — a direct, schedulable lever on utilization and travel cost without adding headcount.
- **Target:** >60% of the field day on-site; reduce avg drive-time per visit quarter-over-quarter
- **Data source:** Job Interval (start_time, end_time, total_paused_seconds, project) + Time Kiosk Log (latitude, longitude, log_status, timestamp per interval) + NEW route_sequence / planned-stop order.
- **Implementation:** On-site ratio is Auto today: SUM(interval worked seconds) / (MAX(end_time)-MIN(start_time)) per employee per day from Job Interval. To attribute the gap to drive vs. idle, add an idle-detection pass over Time Kiosk Log GPS points (cluster points within ~100m of a job site = on-site, dispersed/moving = transit). Optional NEW `route_sequence` Int on the Maintenance Record draft to measure planned vs. actual stop order.
- **Refresh:** Weekly snapshot

### 15. Vehicle Mileage & Fleet Uptime — 🟡 Semi
- **Definition:** Mileage driven per vehicle per period and % of fleet vehicles available (not In Repair). Mileage = SUM(Trip Mileage.distance) by vehicle/Asset; availability = count(vehicle Assets not booked to Maintenance/In-Repair) / total vehicles.
- **Why it matters:** Trucks are the second-largest field asset after labor. Mileage drives fuel/maintenance cost and PM scheduling; a truck in the shop unplanned cancels a route. Today only travel-mileage is captured, not odometer or downtime.
- **Target:** <5% unplanned vehicle downtime; mileage tracked for every field day
- **Data source:** Trip Mileage child (distance, traveler/date) + Asset Booking (asset, booking_type='Maintenance', from_datetime, to_datetime) for vehicle Assets + NEW odometer capture or per-vehicle assignment on Job Interval.
- **Implementation:** Vehicle availability is Auto from Asset Booking (count vehicle Assets with an active Maintenance booking = down). Per-vehicle mileage needs a light capture: either add a `vehicle` Link + `odometer` field to the clock-in flow, or attribute Trip Mileage.distance to the assigned truck Asset. Start with Asset-Booking downtime (Auto) and phase in odometer.
- **Refresh:** Weekly snapshot

### 16. Asset Booking Utilization — 🟢 Auto
- **Definition:** % of available asset-hours that were booked, per asset and booking type. = SUM(Asset Booking (to_datetime - from_datetime)) / available hours in period * 100. Companion: count of double-booked/conflicting reservations.
- **Why it matters:** Shared equipment (pumps, vacuums, pressure washers, lifts, trailers, rental units) is a constrained resource. Low utilization = idle capital you could shed or share; conflicts = visits delayed because gear was unavailable.
- **Target:** Equipment 50-70% utilization; zero unresolved booking conflicts
- **Data source:** Asset Booking (asset, booking_type [Rental/Travel/Maintenance], from_datetime, to_datetime) — overlap detection already in on_validate.
- **Implementation:** SQL: SUM(TIMESTAMPDIFF(HOUR,from_datetime,to_datetime)) per asset / available hours, grouped by booking_type. Conflicts surfaced by the existing overlapping-booking detection. Add to Operations dashboard as a per-asset utilization chart. No new field.
- **Refresh:** Weekly snapshot

### 17. Field Safety Incident Rate & PPE Acknowledgement — 🔴 Manual
- **Definition:** Two parts: (1) PPE/safety acknowledgement compliance = count(Sapphire Maintenance Record where safety_acknowledged=1) / count(submitted records) * 100 (Auto); (2) recordable field incidents per 100 visits or per 200k labor-hours (Manual, needs incident capture).
- **Why it matters:** Field techs work around water, electricity (pumps, control panels, line voltage), chemicals (chlorine/acids), and confined basins. Safety is a legal/insurance imperative; PPE acknowledgement is the documented control, and incident rate is the outcome measure.
- **Target:** 100% PPE acknowledgement on every visit; zero recordable incidents; near-miss reporting >0 (a healthy near-miss count means people are reporting)
- **Data source:** Sapphire Maintenance Record (safety_acknowledged) for the Auto half. For incidents: NEW lightweight Safety Incident doctype OR a tagged ToDo/Issue (date, employee, project, severity, type: electrical/chemical/slip/other, near-miss flag).
- **Implementation:** PPE acknowledgement rate ships Auto today (safety_acknowledged is already on the Record). For incidents, the lightest capture is a small Safety Incident doctype (or reuse stock Issue with an 'Safety' category) submitted from the field; compute rate per 200k hours using Job Interval labor hours as the denominator. Until then, report PPE acknowledgement and treat incident count as manual log entry.
- **Refresh:** PPE: daily snapshot (Auto). Incidents: per-event manual entry + monthly rollup

**Data gaps:**
- No SLA / scheduled-vs-actual model on visits: Sapphire Maintenance Record has no scheduled/planned visit date, so on-time adherence and overdue-visit escalation cannot be computed until a scheduled_visit_date field is added (single highest-leverage gap — unlocks 2 KPIs).
- No callback/redo flag: returns for rework are indistinguishable from scheduled visits except via a fragile self-join heuristic; needs an is_callback checkbox + original_record link for a clean rate.
- No vehicle/odometer telemetry: only Trip Mileage (manually entered per traveler) exists — no per-truck odometer, fuel, or unplanned-downtime capture, so true fleet mileage/uptime is approximate.
- No GPS idle/geofence analysis: Time Kiosk Log stores raw lat/long points but nothing classifies them into on-site vs. transit vs. idle, so route efficiency beyond the on-site-hours ratio is unbuilt.
- No item-level reorder levels on consumables per truck/warehouse: stockout risk needs Item.reorder_level (or per-warehouse reorder rows) populated.
- No structured safety-incident capture: only the per-visit safety_acknowledged checkbox exists; recordable incidents, near-misses, and severity have no doctype.
- No durable Operations KPI history: every metric is runtime SQL with no historical series — there is no Operations dashboard fixture and no pre-computed nightly snapshot (the Daily Briefing pattern exists but has no operations variant), so trends and SLA attainment over time cannot be charted without building the snapshot job.
- No contract renewal/lifecycle field: Sapphire Maintenance Contract has start_date/end_date and status but no renewal-due or churn-stage tracking, so contract-retention KPIs would be manual.
- Per-visit photo/QA spot-check has no structured quality score — chemistry photos and client_sign_off exist, but there is no supervisor QA pass/fail field for visit-quality auditing.

**Recommended minimal manual entry:**
- Tech ticks an is_callback checkbox on the Visit Wizard when a visit is a return-for-rework (one click; everything else for the callback KPI is automatic).
- Capture truck odometer (or vehicle + odometer) at clock-in on field days, so per-vehicle mileage, fuel cost, and PM scheduling become measurable — one numeric field on the existing GPS clock-in flow.
- Log field safety incidents and near-misses in a lightweight Safety Incident doctype (date, employee, project, type, severity, near-miss flag) — only filed when an event occurs, so volume is near-zero but coverage is complete.
- Set reorder levels on A-class field consumables (biocides, clarifiers, filters, common nozzles) per truck/warehouse — a one-time config in ERPNext Item/Reorder, after which stockout risk is fully automatic.
- Optional: supervisor records a pass/fail QA spot-check on a sample of submitted visits (a single select field on the Maintenance Record) to ground a visit-quality score beyond the automatic chemistry/photo signals.

---
## HR (People)

> A 17-KPI catalog built for what this site actually has: the **hrms app is not installed** (no Attendance, Leave, Job Opening/Applicant, Salary Slip, Appraisal, or Training tables) and payroll lives in QuickBooks, so the automatic KPIs read only `tabEmployee` — which is fully populated (date_of_joining, department, designation on every row; relieving_date on every Left row). Of the 17, 12 are Auto (headcount, mix, hiring/separation counts, 12-month turnover, tenure, span of control — snapshot nightly by the `_hr_metrics` aggregator), 3 are Semi (labor-hours KPIs wired to the custom Job Interval / Timesheet doctypes that exist but carry no real data yet; sum-based and self-suppressing until crews clock in), and 2 are Manual (open positions and eNPS via the one-row-per-month **HR Stat Entry** doctype). Small-n design stance for a ~14-person company: headline KPIs are counts, the only rate KPIs use a 365-day window (one exit at n=14 moves turnover ~7 points, so a 90-day rate would whipsaw), and demographic KPIs (gender/age) are deliberately excluded — at this headcount they de-anonymize individuals. Dashboard access is gated to HR Manager + HR Team (not HR User, which every employee holds).

### 1. Active Headcount — 🟢 Auto
- **Definition:** count(Employee where status='Active').
- **Why it matters:** The base denominator for every people metric and the plainest growth signal.
- **Target:** informational (set a KPI Target only when a hiring plan exists)
- **Data source:** Employee (status).
- **Implementation:** `_hr_metrics` in kpi_dashboards/snapshots.py; nightly KPI Snapshot (department=HR, kpi_key=active_headcount).
- **Refresh:** Daily snapshot

### 2. Full-Time Employees — 🟢 Auto
- **Definition:** count(Active Employees with employment_type='Full-time').
- **Why it matters:** Separates the committed core team from part-time/contract flex capacity.
- **Target:** informational
- **Data source:** Employee (employment_type).
- **Refresh:** Daily snapshot (kpi_key=full_time_count)

### 3. Employment Type Completeness — 🟢 Auto
- **Definition:** % of Active employees with employment_type filled.
- **Why it matters:** Data-quality guard for KPI #2 and the employment-type mix chart — 3 of 14 Active rows are blank today.
- **Target:** 100%
- **Data source:** Employee (employment_type).
- **Refresh:** Daily snapshot (kpi_key=employment_type_completeness_pct)

### 4. New Hires (90d) — 🟢 Auto
- **Definition:** count(Employees with date_of_joining in the last 90 days).
- **Why it matters:** Near-term hiring velocity; pairs with separations for net growth.
- **Target:** informational
- **Data source:** Employee (date_of_joining).
- **Refresh:** Daily snapshot (kpi_key=new_hires_90d; 1-year window companion new_hires_365)

### 5. Separations (90d) — 🟢 Auto
- **Definition:** count(Employees with status='Left' and relieving_date in the last 90 days).
- **Why it matters:** The raw exit count — at n=14 the count is more honest than any short-window rate.
- **Target:** 0
- **Data source:** Employee (status, relieving_date).
- **Refresh:** Daily snapshot (kpi_key=separations_90d; 1-year companion separations_365)

### 6. Net Headcount Change (90d) — 🟢 Auto
- **Definition:** new hires (90d) − separations (90d).
- **Why it matters:** One glance answers "are we growing or shrinking?"
- **Target:** ≥ 0
- **Data source:** Employee.
- **Refresh:** Daily snapshot (kpi_key=net_headcount_change_90d)

### 7. Turnover Rate (12m) — 🟢 Auto
- **Definition:** separations in the last 365 days / two-point average headcount (reconstructed on the window's start and end dates from date_of_joining/relieving_date) × 100.
- **Why it matters:** The classic annualized attrition rate; replacing a trained fountain tech costs months of ramp. Also rolled up onto the Executive dashboard.
- **Target:** ≤ 15% (Lower is better)
- **Data source:** Employee (date_of_joining, relieving_date, status).
- **Implementation:** pure helper `metrics.turnover_rate_pct` (unit-tested); historical headcount reconstructed date-based, so no snapshot history needed.
- **Refresh:** Daily snapshot (kpi_key=turnover_rate_12m)

### 8. Avg Tenure (Active) — 🟢 Auto
- **Definition:** avg(today − date_of_joining) over Active employees, in years.
- **Why it matters:** Institutional knowledge proxy; a falling average flags a churn-and-replace pattern.
- **Target:** informational (trend up)
- **Data source:** Employee (date_of_joining).
- **Refresh:** Daily snapshot (kpi_key=avg_tenure_years)

### 9. Avg Tenure at Exit (12m) — 🟢 Auto
- **Definition:** avg(relieving_date − date_of_joining) over employees who left in the last 365 days, in years.
- **Why it matters:** Distinguishes losing new hires (onboarding problem) from losing veterans (retention problem). Skipped automatically when there were no exits.
- **Target:** informational
- **Data source:** Employee (date_of_joining, relieving_date).
- **Refresh:** Daily snapshot (kpi_key=avg_tenure_at_exit_12m)

### 10. Span of Control — 🟢 Auto
- **Definition:** Active employees with an Active manager / distinct Active managers (via reports_to self-join).
- **Why it matters:** Org-shape sanity check — a ballooning span means managers can't coach; near-1 means layers.
- **Target:** informational (typical field-service span 4–8)
- **Data source:** Employee (reports_to, status).
- **Refresh:** Daily snapshot (kpi_key=span_of_control)

### 11–12. New Hires / Separations (1y) — 🟢 Auto
- **Definition:** the 365-day companions to #4/#5, so the turnover rate's numerator is always visible next to it.
- **Data source:** Employee.
- **Refresh:** Daily snapshot (kpi_keys=new_hires_365, separations_365)

### 13. Field Labor Hours (30d) — 🟡 Semi
- **Definition:** sum of completed Job Interval durations (end − start − paused) over the last 30 days, in hours.
- **Why it matters:** The people-side view of field capacity actually deployed; the base for future utilization math.
- **Target:** informational until the kiosk rollout
- **Data source:** Job Interval (start_time, end_time, total_paused_seconds, status) — the table exists but is empty today.
- **Implementation:** guarded + sum-based, so the KPI stays silent (NULL → skipped) until real intervals exist; appears the day crews start clocking in.
- **Refresh:** Daily snapshot (kpi_key=field_labor_hours_30d)

### 14. Field Staff Clocking In (30d) — 🟡 Semi
- **Definition:** distinct employees with any Job Interval in the last 30 days.
- **Why it matters:** Adoption gauge for the time-kiosk rollout — hours without breadth means one tech is carrying the data.
- **Data source:** Job Interval (employee). Only emitted once #13 has data (a standing 0 pre-rollout would read as "nobody works here").
- **Refresh:** Daily snapshot (kpi_key=field_staff_clocking_30d)

### 15. Timesheet Hours (30d) — 🟡 Semi
- **Definition:** sum(Timesheet.total_hours) over submitted Timesheets started in the last 30 days.
- **Why it matters:** The ERPNext-native labor capture channel (maintenance visits auto-draft Timesheets); complements #13.
- **Data source:** Timesheet (docstatus=1, start_date, total_hours) — only 2 draft test rows exist today, so this self-suppresses.
- **Refresh:** Daily snapshot (kpi_key=timesheet_hours_30)

### 16. Open Positions — 🔴 Manual
- **Definition:** roles actively being hired for, from the newest monthly HR Stat Entry row.
- **Why it matters:** There is no Job Opening doctype on this site; without this one number the hiring pipeline is invisible to the dashboard.
- **Target:** 0 = fully staffed (Lower is better)
- **Data source:** HR Stat Entry (month, open_positions) — one row per month, ~30 seconds of entry.
- **Implementation:** newest row wins; an entry older than the previous calendar month flags the source stale (Watch badge).
- **Refresh:** Monthly manual entry, read nightly (kpi_key=open_positions)

### 17. eNPS — 🔴 Manual
- **Definition:** Employee Net Promoter Score (−100…100) from the newest HR Stat Entry row, when surveyed.
- **Why it matters:** The only sentiment signal available without a survey tool integration.
- **Target:** ≥ 20 (informal small-team benchmark)
- **Data source:** HR Stat Entry (enps). 0 is treated as "not surveyed" (documented on the field) so blank months don't masquerade as a neutral score.
- **Refresh:** Whenever surveyed, read nightly (kpi_key=enps)

**Data gaps (why the rest of the classic HR catalog is out of reach):**
- No Attendance / Employee Checkin doctypes: absenteeism and overtime are unmeasurable (hrms not installed).
- No Leave Application/Allocation/Type: leave utilization and balances have no source.
- No Job Opening/Applicant/Offer: time-to-hire, offer-acceptance, and funnel KPIs would need hrms or an ATS; interim coverage is the manual open-positions count.
- Payroll lives in QuickBooks: labor cost as % of revenue is a Finance-side GL KPI (see Finance #11), not an HR doctype read.
- No Appraisal or Training Event: performance and training-completion KPIs are unbuilt.
- Demographic KPIs (gender balance, age distribution) are computable — the fields are 100% filled — but deliberately excluded at n=14 for privacy; revisit if headcount triples.

**Recommended minimal manual entry:**
- Fill the 3 blank employment_type values on Active employees (drives KPI #3 to 100 and makes the employment-type donut truthful).
- Keep the existing discipline: every departure gets status='Left' + relieving_date (all 4 historical exits have it — the turnover math depends on this).
- One HR Stat Entry row per month (open positions; eNPS when surveyed).
- Optional KPI Targets to light the Good/Watch/Bad badges: turnover_rate_12m ≤ 15 (Lower is better), employment_type_completeness_pct = 100, open_positions = 0 (Lower is better).

---
## Cross-cutting plan

### Architecture

Build a three-layer system that reuses what already works, rather than one monolith.

LAYER 1 — Precomputed snapshot store (the core new build). Create ONE new submittable-free doctype family modeled exactly on Daily Briefing (durable doctype, NOT frappe.cache, because bench migrate/clear-cache flushes Redis mid-deploy):
  • "KPI Snapshot" (parent): fields = department (Select: Finance/Sales/Marketing/Design/Production/Operations/Executive), period (Select: Daily/Weekly/Monthly), snapshot_date, generated_at, generated_by (System/User), source_freshness_json (last QBO CDC time, last Stripe poll, last GA4 pull — so stale-source KPIs render with a warning badge). Autoname format:KPI-{department}-{period}-{snapshot_date} to enforce one row per dept/period/day (idempotent re-runs, same trick as BRIEF-{date}-{user}).
  • "KPI Snapshot Value" (child): kpi_key, label, value (float), value_text, unit, target_value, status (Good/Watch/Bad vs threshold), trend_pct (vs prior snapshot), source, is_stale. One row per KPI. This makes every metric queryable and trendable over time without re-running heavy SQL.
  • "KPI Target" (small standalone doctype): department, kpi_key, period, target_value, effective_from. This is the single highest-leverage enabler — it unlocks every "vs plan / vs target" KPI across Finance, Sales, Exec, Production without a full Budget module. PMs/controller edit it directly; no code.

LAYER 2 — Snapshot jobs (scheduler_events). Add to hooks.py a "cron" entry per cadence mirroring the 06:30 briefing handler that immediately enqueues onto the 'long' queue:
  • Nightly (e.g. "0 5 * * *") snapshot_run(department=...) for each of the 7 catalogs — one enqueued job per department so a slow QBO read can't block Operations.
  • Weekly + monthly variants for cycle-time/throughput/forecast KPIs.
  Each job is a pure aggregator: pull from the SAME doctypes the catalogs already cite (ERPNext GL/Sales Invoice/Purchase Invoice/Payment Entry as system-of-record post-QBO-sync; Stripe Payment; Opportunity/Lead/Call Log/Communication; Project/Task/Timesheet Detail/Project Process Step/Project Contract; Sapphire Maintenance Record/Chemistry Reading; Job Interval; Managed Device; Travel Trip; Document Intake; QuickBooks Sync Log/Mapping). Do NOT call external APIs live in the snapshot path except GA4/GSC which already have a daily-pull module (api/analytics.py) — read its cached output, or snapshot it on the same nightly job. QBO/Stripe facts are read from ERPNext doctypes they already sync into (never hit Intuit/Stripe in the dashboard render path), with source_freshness_json carrying last_cdc_sync / last Stripe poll so a stale sync is visible, not silent.

LAYER 3 — Presentation, two surfaces, both reading the snapshot store:
  (a) ERPNext-native Dashboards as the default per-department surface — extend the existing fixtures (fixtures/dashboard.json + dashboard_chart.json + number_card.json, repo is source of truth, synced on after_migrate). Number Cards and Charts point at KPI Snapshot Value (filtered by department + latest snapshot_date) for trend timeseries, and at live doctypes for the cheap realtime counts that don't need precompute. This gives 7 department dashboards (Finance, Sales, Marketing, Design, Production, Operations, Executive) plus the existing 6 with minimal code.
  (b) A custom desk Page only where native dashboards are too rigid — specifically the Executive one-screen cockpit and any KPI needing a computed gauge/target-vs-actual bullet that Number Cards can't render. Clone the project_dashboard page pattern (whitelisted get_kpi_dashboard(department) endpoint returning the latest KPI Snapshot + targets + trend; publish_realtime on snapshot write for live refresh).

ROLE-BASED VISIBILITY: gate each Dashboard fixture and the page endpoint by role (Accounts Manager→Finance, Sales Manager→Sales, Projects Manager→Production/Design, Maintenance Supervisor→Operations, System Manager + a new "Executive" role→Executive). The page endpoint enforces frappe.has_role; Number Cards/Charts use restrict-to-role on the Dashboard fixture. Department managers see their dashboard as their desk home.

/WALL TV VIEW: reuse the existing public app (public/js/wall/app.js + css/wall/wall.css) that already renders structured briefing data read-only. Add a department/rotation query param so the wall cycles through KPI Snapshot department cards (Operations Day Board + Exec KPIs are the natural TV content). No auth-heavy desk render — it reads a whitelisted guest-safe (or token-gated) KPI summary endpoint, same as it reads briefing data today.

NET NEW: 3 doctypes (KPI Snapshot, KPI Snapshot Value, KPI Target), ~3 scheduler entries + handlers, ~7 dashboard fixtures + their charts/cards, 1 Executive desk page, and the wall rotation param. Everything else is reuse.

### Shared KPIs (roll up to Executive)
- Open Pipeline Value & Win Rate (Sales + Marketing-by-source + Executive bookings)
- Bookings / New Signed Contract Value (Sales close + Finance revenue + Executive vs plan) — from Project Contract.signed_on
- Recognized Revenue & MoM Growth (Finance + Executive, same GL/Sales Invoice source)
- Gross Margin % (Finance by segment + Production by project + Executive rollup) — same labor+materials+travel cost join
- Closed-Won Hand-Off Cycle Time & Backlog (Sales + Production + Executive) — already a wired metric
- On-Time Delivery / On-Time Milestone Rate (Production + Design on-time-design + Executive)
- Workforce / Crew Utilization % (Operations technician + Production crew + Executive) — Job Interval / Timesheet hours
- Maintenance Recurring Revenue & Contract Health/Renewal (Operations + Sales renewal + Finance + Executive)
- DSO & AR Aging (Finance + Executive cash health)
- Cash Position & Runway (Finance + Executive)
- Headcount & Revenue per Employee (Finance labor-efficiency + Executive)
- QBO <-> ERPNext Sync Health (Finance reconciliation + every dashboard's source-freshness badge)
- CSAT / Escalation proxy from Call Log (Sales + Operations + Executive)

### Cross-cutting data gaps
- No KPI Target / plan / budget store: every 'vs plan / vs target / vs quota' KPI across Finance, Sales, Production, Marketing, and Executive has no baseline. A single lightweight KPI Target doctype unlocks ~12+ KPIs and is the highest-leverage build.
- No persisted KPI snapshot history anywhere: all seven catalogs independently flag that metrics are point-in-time live SQL with no trend retention. One shared KPI Snapshot doctype (Daily Briefing pattern) solves this for all departments at once.
- No standardized revenue/cost SEGMENT tag (Design/Build/Maintenance/Rental) on Sales Invoice/Project: blocks gross-margin-by-segment (Finance), profitability (Exec), and marketing-influenced recurring revenue. One Select field on Project propagated to invoices fixes it.
- No unified per-project cost-of-goods rollup: labor (Timesheet), materials (PO/Purchase Invoice), travel (Travel Trip), consumables are separate streams with inconsistent project tagging. Needed by Production margin, Finance project margin, and Executive gross margin. Requires consistent custom_project linkage on ALL POs/PIs + one joined rollup query.
- No structured won/lost + change-order + rework REASON capture: Sales win/loss analysis, Production change-order attribution, and Operations callback root-cause all need a small structured reason Select where today only free text or status exists.
- No real CSAT/NPS capture beyond AI-scored call sentiment: Sales, Operations, and Executive all rely on the same proxy. A post-visit/post-project 1-tap survey would serve three departments.
- No scheduled/planned-date stamp on work artifacts (Maintenance Record scheduled_visit_date; Water Feature Design due date; project actual_completion_date): on-time/SLA KPIs in Operations, Design, Production, and Executive all derive from a missing 'promised vs actual' pair.
- No ad-spend / social / email-platform integration: blocks fully-Auto CPL, CAC, ROMI for Marketing and CAC-by-channel for Sales/Exec. Needs a Marketing Spend paste doctype (interim) or connectors.
- Customer.custom_lead_source not reliably populated at Lead->Customer conversion: silently breaks CAC, LTV, and channel-ROI joins for Marketing and Exec — needs a conversion hook to copy source forward.

### Quick wins (Auto today, ship first)
- **[Finance / cross-cutting] QBO <-> ERPNext Sync Health & source-freshness badge** — Pure read of QuickBooks Sync Log/Mapping already wired; doubles as the trust badge on every other dashboard. Auto today, zero new data.
- **[Sales] Open Pipeline Value, Win Rate & Stage Aging** — Opportunity already carries stage-change timestamps (before_save hook) and amount; SQL + snapshot only. Highest exec visibility for least effort.
- **[Sales / Production / Exec] Closed-Won Hand-Off Cycle Time & Backlog** — Already an existing wired metric (Opportunity Closed Won + custom_created_project null + Process Step SLA). Serves three dashboards from one query.
- **[Finance] AR Aging & DSO + Operating Cash Balance** — GL Entry / Sales Invoice / Account are system-of-record post-QBO sync; stock ERPNext aging logic exists. Auto, high CFO value.
- **[Operations] Visit Completion Rate + Chemistry Out-of-Range Incident Rate** — Sapphire Maintenance Record + Chemistry Reading flags computed on submit already; no new field needed. The most-requested missing dashboard.
- **[Design] Design Throughput, Cycle Time & Clean-Issue Rate** — Water Feature Design status + amended_from + calc_results status (OK/Warning/Error) + tabVersion give throughput, revision %, and accuracy with no new fields.
- **[Production] Build Throughput, WIP Aging & Labor-Hours Budget vs Actual** — Project already stores custom_time_budget_in_hours / custom_total_time_elapsed and Timesheet Detail; cycle time from process steps. Auto today.
- **[Marketing] Web Sessions/Channel Mix + Organic Search + Lead->Opp Conversion by Source** — GA4/GSC already pulled daily (api/analytics.py) and Lead.source/Opportunity are live — snapshot the existing API output, no new integration.

### Phased rollout
**Phase 1 — Snapshot spine + Finance/Ops/Sales Auto dashboards — Stand up the shared infrastructure and ship the all-Auto, zero-new-data dashboards that are most asked for.**
- Create KPI Snapshot + KPI Snapshot Value doctypes (Daily Briefing pattern, durable, idempotent autoname, daily purge task)
- Add nightly per-department snapshot scheduler_events entries + enqueue-to-long handler with source_freshness_json
- Finance dashboard fixture (Sync Health, DSO, AR Aging, Cash Balance, Stripe/PE reconciliation, Intake throughput)
- Operations dashboard fixture (Visit Completion, Chemistry Out-of-Range, Technician Utilization, Device Compliance) — the primary missing dashboard
- Sales dashboard upgrade (Pipeline Value, Win Rate, Stage Aging, Hand-Off Cycle Time/Backlog)
- Role-gating on each new dashboard

**Phase 2 — Design + Production dashboards & KPI Target store — Cover the build side and introduce targets so 'vs plan' KPIs light up.**
- Design dashboard (Throughput, Cycle Time, Clean-Issue Rate, Revision %, WIP age) from existing WFD + Version data
- Production dashboard (Build Throughput, WIP aging, Labor-hours budget vs actual, On-Time Milestone, Schedule Slippage)
- KPI Target doctype + wire target_value/status/trend into snapshot values
- Weekly + monthly snapshot variants for throughput/cycle-time/forecast KPIs
- Marketing dashboard from already-pulled GA4/GSC + Lead-source conversion

**Phase 3 — Executive cockpit + /wall TV + light enabler fields — Roll everything up to one exec screen, push to the wall, and add the handful of one-field enablers that convert Semi-Auto KPIs to Auto.**
- Executive one-screen desk Page (clone project_dashboard) reading latest KPI Snapshot + targets, realtime refresh
- Add /wall rotation param to cycle department KPI cards on the existing wall app
- Add revenue/cost segment Select on Project + propagation; scheduled_visit_date on Maintenance Record; project actual_completion_date stamp; custom_project_contract on WFD — each unlocks a Semi-Auto KPI
- Lead->Customer source-copy hook for CAC/LTV joins

**Phase 4 — Manual-capture micro-flows & remaining gaps — Close the genuinely manual KPIs with byproduct-of-workflow capture, not data-entry chores.**
- Marketing Spend paste doctype (monthly channel totals) -> CPL/CAC/ROMI
- Structured reason Selects: won/lost (Opportunity close dialog), change-order (contract amend), callback flag (maintenance visit)
- Commissioning Test + Build Punch Item child tables on build close-out form (First-Pass Yield, rework)
- Post-visit/post-project 1-tap CSAT (SMS reply or kiosk button) feeding Sales/Ops/Exec satisfaction
- Field safety incident doctype tied to existing visit safety_acknowledged checkbox

### Manual-entry strategy

Principle: a human only types a number when (a) nothing in the system can derive it and (b) the typing happens inside a workflow the person is already doing. Make every manual datum a byproduct, attach it to an existing save/submit, default it off, and assign one owner per cadence.

CONCRETE ASSIGNMENTS:
  • Targets (KPI Target doctype) — owner: Controller (Finance), Sales Manager (quota/coverage), each dept Manager. Cadence: quarterly, edited once. This is the only 'pure data entry' allowed, and it's tiny (one row per KPI per period) and high-leverage. Pre-seed with last period's actual + a % so the form is never blank.
  • Won/Lost + competitor reason — owner: the rep, captured IN the Closed-Won/Closed-Lost dialog they already click. Make it a required Select only on close, not a separate form. Same pattern for change-order reason: fire it on Project Contract amend (the amendment already happens), and callback flag: a single is_callback checkbox on the maintenance visit form the tech already submits.
  • Commissioning test results + punch list — owner: build crew/PM, captured as child tables on the project close-out/handover step they must complete anyway. Defaults pre-filled to 'Pass'; tech only edits exceptions. First-pass yield and rework fall out for free.
  • CSAT/NPS — make it NOT staff work at all: trigger an automated SMS one-tap reply (reuse Triton SMS) after Maintenance Record submit or project completion; the customer supplies the number, ERPNext just logs the reply. Zero internal entry.
  • Marketing spend — owner: Marketing lead, once a month, paste platform totals into Marketing Spend doctype (interim until Google Ads/Meta connectors exist). Cadence-enforced by a monthly reminder task (clone the existing nudge pattern) so it never silently lapses.
  • Field safety incident — owner: tech, only when an incident occurs (exception-only), surfaced as a button next to the existing per-visit safety_acknowledged checkbox.

RELIABILITY MECHANICS: (1) Reuse the existing reminder/nudge scheduler tasks (the maintenance-form SMS nudge, customer-inactivity reminder are the template) to chase any overdue manual cadence — targets quarterly, spend monthly. (2) Snapshot source_freshness_json surfaces 'manual figure is N days stale' as a Watch badge on the dashboard so gaps are visible, never silently wrong. (3) Default-off rollout convention (briefing_enabled style master switch) per capture so half-built flows don't nag users. (4) Pre-fill every manual field with the prior value or a sane default so the human confirms rather than authors. Net: only Targets and monthly Marketing Spend are recurring deliberate entries; everything else rides on a save/submit/close the user already performs.