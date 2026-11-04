# WI-082: Receipt, bill and PO capture — one door for every purchasing document

**Status:** scoping, for Nikolas to accept or amend (2026-11-04). No feature code is authorized by this
file; each phase below becomes its own PR once the open questions have answers.
**Phase:** 2   **Type:** APP_CODE + CONFIG   **Size:** L (four phases: 0 = S, 1 = M, 2 = M, 3 = M)
**Blocked by:** nothing for Phase 0; Phase 1 needs the Document AI processors provisioned in Triton
and an inbound Email Account on prod; Phase 2 needs the phase-number field on purchasing lines
(Phase 0 here) and the kiosk in daily use by the build crew (Nikolas, 2026-11-04: the kiosk tracks
build-crew hours once Sapphire is off QuickBooks Time)
**Blocks:** WI-078 actual cost per phase (the SOV and pay-application design assumes it exists); the
job-cost side of WI-077

## Why

The estimate-to-pay-application design (WI-077, WI-078, the 2026-11-04 concept decisions) prices a job
by phase (100 to 800) and bills a percent per Schedule of Values line. Percent-complete accounting, which
Nikolas chose for launch, needs **actual cost per phase**. The Salt Air reality check (PRJ-00604,
2026-11-04) found that prod has almost none:

- **Purchase Invoices:** 10 rows in the whole table, none dated 2026, none submitted (read-only query,
  2026-11-04). Every supplier bill Sapphire pays is keyed into QuickBooks by hand.
- **Purchase Orders, 2026:** 247, of which 243 submitted; **153 carry a grand total of 0** (store-run
  orders written without prices). Every PO since June names a project (the `Purchase Order Item.project`
  Property Setter made it mandatory, WI-014), so the attribution habit exists; the money does not.
- **The real volume lives in the QuickBooks mirror.** `tabQuickBooks Raw Payload`, by transaction date,
  January to October 2026: **519 vendor Bills** (31 to 67 a month) and **1,695 Purchases**, of which
  1,406 were paid by **company card**, 244 by cash or debit and 45 by check. About 220 payable
  documents a month, roughly 10 per working day, and about two thirds of them are card purchases
  with no PO behind them.
- **Receipts for those card purchases live in email, on phones and in Google Drive**, and nowhere in
  ERPNext: 24 Files are attached to Purchase Orders, 3 to Purchase Invoices and 11 to Suppliers.
  QuickBooks itself holds almost none either: one `Attachable` mapping in the whole sync table.
- **The module built for this has never run.** `accounting_intake` (AI extraction, a review queue, draft
  record creation) has **0 Document Intake rows and 0 log rows** on prod. Its settings say
  `intake_enabled = 1` and `auto_extract = 1`, but no email account, no watched Drive folder and no
  supplier Drive parent are configured, its status reads *Not Configured*, and Triton's extraction
  endpoint answers 503 because no Document AI processor id is deployed (Triton `docs/convergence.md`).
- **The store-run receipt path has never run either.** The Stock Scan page's *Bought on a store run*
  (v1.536.0: a Purchase Receipt with no PO, the receipt photo and the receipt total) has **0 rows** on
  prod, and the kiosk has recorded 15 Job Intervals since August.

Sapphire is leaving QuickBooks. Once it has, there is no second system where Lisa can key the bill, so
the capture door has to exist in ERPNext before the cutover, and it has to be usable by a technician
standing at a counter, not only by accounting at a desk.

## Native-first check

Verified against the live instance on 2026-11-04 (frappe 16.53, erpnext 16.52) and the repo:

- **ERPNext has no OCR or document extraction.** Nothing native reads a receipt. The one custom piece
  in this item is therefore extraction plus the review queue, and both already exist in
  `accounting_intake`. Replacing that module would be a second custom system for the same job; this
  item **reuses it** (see *Extraction pipeline* for the reasons and the gaps).
- **Everything the pipeline produces is native:** draft Purchase Invoice (`is_paid` + Mode of Payment for
  a card purchase), draft Purchase Receipt (3-way match), Supplier, File, Communication (inbound email),
  Email Account (inbound mail), Role Permission. No new transaction DocType.
- **Project attribution is native and already enforced:** `Purchase Order Item.project` is mandatory and
  cascades from the header (`procurement_project.py`). The five Overhead projects give non-job spend a
  home. **Phase attribution has no native field.** Task carries `custom_phase_number`; Purchase Order
  Item and Purchase Invoice Item carry none (checked `information_schema`, 2026-11-04). One Custom Field
  on each line table is the whole schema change, consistent with the 2026-11-04 decision that purchases
  carry **phase only**, no SOV-line link.
- **Card reconciliation is native:** ERPNext 16's Plaid link writes `Bank Transaction` rows and the Bank
  Reconciliation Tool matches them to paid Purchase Invoices. 0 Bank Transaction rows today; Plaid
  production approval is WI-056's long pole. This item makes the Purchase Invoices reconcilable; it does
  not build reconciliation.
- **Google Drive filing is the existing `google_drive` module** (folder ids on Project, Customer,
  Opportunity, Supplier; 35,177 shadow File rows mirror Drive files onto records). Filing a receipt is
  one more writer to that store, following its conventions, not a new store.
- **What stays rejected:** an Expense Claim path (hrms is not installed and cannot be: it collides with
  this app's HR module label and six Training doctype names); a chat channel (the chat product was
  retired in v1.426.0); any QuickBooks write-back (`qbo_writeback_enabled` stays 0 and the field is
  deleted with WI-052).

## The user journeys

### A. A technician buys parts at a counter on a company card

1. Clocked in on the kiosk against a project (Phase 2 makes this the normal case). At the register they
   tap **Receipt** on the kiosk, photograph the paper receipt, and answer two things: *paid with* (company
   card, default; or own money) and *which job* (defaulted from the open Job Interval, one tap to change
   to another project or to Overhead). Nothing else: no amounts, no items.
2. The kiosk uploads the photo as a private File, calls one endpoint, and the technician is done in under
   30 seconds, offline-tolerant the same way kiosk photos already are (a queued upload survives loss of
   signal; the row is registered first).
3. The queue row carries `project`, `paid_by_employee`, `payment_method` and the Job Interval, so the
   extraction only has to read the merchant, date, total and tax. The merchant is matched to a Supplier
   (Home Depot, Lowe's, the seven *Store-Run Vendor* Suppliers first).
4. **Phase** is proposed from the project's phase group whose dates contain the receipt date (Task
   `custom_phase_number`, after the backfill from the "Phase N00" subjects); when more than one phase is
   open the row is marked *Needs Attribution* for the project manager, who picks the phase from the
   project's own list. A receipt is never posted to phase 0 or to no phase.
5. Lisa sees it in the review queue with the photo beside the fields, approves, and a **draft Purchase
   Invoice** appears: supplier = the store, `is_paid = 1`, Mode of Payment = that card, one expense line
   per receipt (or the extracted lines when they read cleanly) on the project's job-cost expense account,
   project and phase on every line. Lisa submits it in the normal flow.
6. The receipt image is filed in the project's Drive folder under *Accounting & Legal*, with a shortcut
   in the Supplier's folder, and a File link sits on the Purchase Invoice, the Project and the Supplier.

If the technician recorded the parts on the Stock Scan page as a store run (so stock exists), the
receipt matches that Purchase Receipt (same store, same day, same total) and the Purchase Invoice is made
from it, so the goods are never counted twice.

### B. Lisa receives a supplier invoice by email

1. Lisa forwards the email (or the supplier is told to send to the intake address) to the intake mailbox.
   The attachment is queued within the mail poll interval; the body is kept as the Communication, so the
   email thread is still readable from the queue row.
2. Extraction reads supplier, invoice number, dates, PO number, totals and lines.
3. The PO number matches a Purchase Order outright (score 100); failing that, the supplier's open POs are
   ranked by amount proximity, then by project and date window. Project and phase come from the PO's
   lines. A PO with stock items yields a draft Purchase Receipt first (3-way match), as the module does
   today.
4. New Items proposed on lines go to the Stock Manager gate (unchanged); Lisa approves the document.
5. Draft Purchase Invoice against the PO, with `bill_no` and `bill_date` from the invoice, unpaid, for
   the normal payment run. Filed to Drive as in A.

### C. A PO is matched to its invoice later

A Purchase Invoice already created standalone (no PO at the time) can be linked to a PO from the queue
row's **Match** action while it is still a draft: the invoice is rebuilt from the PO (`make_purchase_invoice`)
and the standalone draft is deleted, so the PO's billed percentage is right. Nothing is done to a
submitted invoice; that is an accountant's cancel-and-amend, out of this item.

### D. Lisa scans a stack of paper

Drop the scans in the **Receipts Inbox** Drive folder. The hourly watcher queues each file and moves it
to *Processed*. Same review as B.

## Capture channels

| Channel | Who | Exists? | Decision |
|---|---|---|---|
| **Kiosk Receipt button** (`/kiosk`, Clock tab, beside *Take photo*) | technicians, PMs on site | No. `channels.ingest_mobile_photo` exists but is role-gated to Accounts, and the kiosk has no door to it | **Build (Phase 2).** The endpoint takes the employee from the session like every kiosk call, accepts only the caller's own File, and writes project, payer and payment method onto the queue row |
| **Desk upload** (Document Intake list, *Upload Document*) | Lisa, purchasing | Yes | **Keep.** Add *Attach a bill* on the Purchase Order form (pre-fills supplier and PO) and on the Project form (pre-fills project) |
| **Email inbox** | Lisa, suppliers who email invoices | Code yes; **no inbound Email Account on prod** (1 account, outgoing only) | **Configure (Phase 1).** A dedicated mailbox in the company's Google Workspace, incoming enabled, pointed at by `Accounting Intake Settings.email_account`. Credentials live in the Email Account record on prod, never in the repo |
| **Drive folder watch** | Lisa (scans), anyone with Drive | Code yes; no folder configured | **Configure (Phase 1).** A *Receipts Inbox* and *Processed* pair in the Shared Drive |
| MMS to the Triton number | technicians | `telephony.receive_mms` exists; no intake adapter | **Later, optional (Phase 4).** Convenient, but the sender's phone must map to an Employee and the photo arrives with no job context |
| Chat | — | retired v1.426.0 | **No.** The `Chat` option on `source_channel` stays as dead data |

## Extraction pipeline: reuse `accounting_intake`

**Decision: reuse, and finish the parts it never got.** Reasons:

- It already does the hard, easy-to-get-wrong parts and documents why: SHA-256 dedupe at the single
  `ingest_document` door; two independent approval gates (Stock Manager for new Items, Accounts Manager
  for posting); **every output is a draft**, never submitted; matching is advisory and a no-match never
  blocks; Drive filing is best-effort and never fails a posting; failed steps are retried daily with a cap
  on the document, not the log row. These are the properties a replacement would have to re-earn.
- Its review record already has the fields a reviewer needs (header, lines with Item resolution,
  advisory matches, per-field confidence, a preview pane) and the Triton MCP tool
  `document_intake_queue` already reads it.
- The extractor is Triton's `/api/v1/document-ai/extract` over Google Document AI's **pretrained Invoice
  and Expense parsers**, with a processor per document type (Vendor Bill, Receipt / Expense, Customer
  Remittance, Packing Slip). Purpose-built parsers give per-field confidence and need no prompt
  engineering; the only gap is that no processor id is deployed. Provisioning is Phase 0.

What it lacks, and what this item adds:

1. **Job context on the queue row.** `Document Intake` gets `project` (Link), `phase_number` (Int, 100
   to 800), `payment_method` (Select: Company Card / Own Money / Account / Check), `job_interval` (Link),
   and a `Needs Attribution` status. `paid_by_employee` exists.
2. **A technician channel** (above). Roles: a technician holds `Employee`, not `Accounts User`; the kiosk
   endpoint is the gate, not the role.
3. **Matching to Project and phase** (below). Today it matches Supplier and PO only.
4. **Card purchases** post as paid invoices with a Mode of Payment (below). Today `vendor_bill` builds an
   unpaid invoice and `receipt_expense` builds a reimbursement bill; neither knows about a card.
5. **Drive filing into the Project folder**, not only the party folder, with links back to every record
   (below). Today it files to the Supplier or Customer folder and records the link on the queue row only.
6. **A sweep for rows stranded by a deploy.** The prod deploy flushes both Redis instances, which
   destroys queued extraction jobs. A `Received` row older than ten minutes with `auto_extract` on is
   re-enqueued by the daily retry job (today that job only re-drives rows already marked `Failed`).
7. **Supplier aliases that learn.** When a reviewer corrects the proposed Supplier, the extracted merchant
   text is saved as an alias on that Supplier (a small child table, *Also known as*), and the next
   document with that text matches at 100. This is how 36 near-duplicate Supplier groups (below) stop
   costing a click each time without a data-merge project first.

A Gemini-based extractor (the app already talks to Gemini for training content) is **not** the first
choice: a general model is better at odd layouts and worse at giving a trustworthy per-field confidence,
and the confidence is what routes a document to a human. It is the fallback for a low-confidence
Document AI result, in Phase 4 if the pilot shows the need.

## Matching rules

All matches are **advisory**; the reviewer decides; a no-match never blocks. Scores use the existing
`drive_match` fuzzy scorer and tiers (High / Medium / Low / None).

| Target | Rule, in order | Source of truth |
|---|---|---|
| **Supplier** | 1. alias text equals a stored alias (100). 2. normalized name equals `supplier_name` with punctuation and `Inc/LLC/Co/Corp/Company/Ltd` stripped (High). 3. fuzzy over all Suppliers, *Store-Run Vendor* Suppliers first for a Receipt / Expense (existing). 4. For kiosk receipts, the store the technician picked on an open Stock Scan run the same day | `tabSupplier`, the new alias table |
| **Purchase Order** | 1. extracted PO number exists (100, pre-selected, existing). 2. open POs of the matched supplier ranked by amount proximity (existing), then narrowed to POs whose project equals the row's project when the row has one. 3. open POs of the supplier dated within 60 days before the document. Never a Closed or Completed PO | `tabPurchase Order` |
| **Purchase Receipt (store run)** | same supplier, same day (or the day before), `custom_receipt_total` within 1% of the extracted total | `tabPurchase Receipt.custom_store_run` |
| **Project** | 1. channel context: the kiosk's Job Interval project; *Attach a bill* on a PO or Project form. 2. the matched PO's project. 3. a project id or `custom_project_id` in the email subject or body, or on the document. 4. the matched Supplier's only project with a PO in the last 60 days (Medium at best). 5. otherwise `Needs Attribution`. **Never** a guess from a customer name on a receipt | `tabProject`, `tabPurchase Order Item.project` |
| **Phase** | 1. the matched PO line's `custom_phase_number`. 2. the project's phase group (Task with `custom_phase_number`, `is_group`) whose expected dates contain the document date; exactly one open phase → High, several → `Needs Attribution`. 3. the project manager's pick. A phase number outside 100 to 800 is refused on save | `tabTask.custom_phase_number`, after the backfill |
| **Employee (who paid)** | kiosk session; email sender's `Employee.user_id` (existing); the reviewer | `tabEmployee` |

A document whose Supplier is Medium or lower and whose PO is None goes to Lisa with the candidates
listed; a document whose Project is below High goes to `Needs Attribution` **first**, so the PM answers
the one question only they can answer before accounting spends time on it.

## The review queue and who approves

Three roles, three questions, in this order:

| Gate | Role on prod (holders, 2026-11-04) | Question | Status |
|---|---|---|---|
| Attribution | Projects Manager (17) and the project's own PM | Which job and which phase? Only when the match is not High | `Needs Attribution` (new) |
| Items | Stock Manager (17) and Purchasing's weekly store-run review (POL-0602 §4.8) | Are the proposed new Items real, and coded right? | `Needs Item Review` (existing) |
| Posting | Accounts Manager (8); in practice Lisa | Is this a bill we owe, or a card purchase we made, at these figures? | `Needs Review` → `Approved` → `Posted` (existing) |

Nothing posts without the last gate; nothing is ever submitted by the pipeline. At about 220 documents a
month the posting gate is roughly ten approvals per working day for one person, which is why the queue
must show the image beside the fields, pre-select the obvious, and let a High-confidence card receipt
under a threshold Lisa sets be approved in one click from the list. **Auto-approval of anything is not
in this item**; it is the first thing to revisit after a month of pilot numbers.

Rejection and reprocessing stay as built: `Rejected` rows do not block a re-upload of the same file;
`Failed` rows are retried up to three times, then skipped and listed.

## Drive filing and the links back

**One file, in the job's folder, linked from everywhere.** The Document Hub SOP (WI-063) already says
receipts and invoices belong in the project's *Accounting & Legal* subfolder. This item follows it.

- **Where:** `<Shared Drive>/<Customer>/<PRJ-xxxxx - Name>/Accounting & Legal/Receipts & Bills/`
  (find-or-create the `Receipts & Bills` subfolder). Overhead and non-job documents go to the Overhead
  project's folder, so the five Overhead projects need Drive folders (they have none today; the Project
  form's *Create folder* path provisions them).
- **Name:** `YYYY-MM-DD <Supplier> <invoice number or total> <PINV-name>.<ext>`, so a folder listing
  reads as a ledger and a name search finds the invoice from either side.
- **Supplier's view:** a Drive **shortcut** to that file in `<Supplier folder>/Accounting & Legal/`, not a
  copy. One set of bytes, two places to find it. Supplier folders are provisioned under the configured
  supplier parent (`Accounting Intake Settings.supplier_parent_folder_id`, unset today; 0 of 1,213
  Suppliers have a folder).
- **Links back in ERPNext:** the private File already attached to the Purchase Invoice gets
  `custom_drive_file_id`, so the shadow sync treats it as mirrored and never uploads a second copy; the
  `drive_link` the queue row already records is copied to the Purchase Invoice (one new Data field,
  `custom_drive_link`, with an *Open in Drive* button); a link-only File (the shadow shape the Drive sync
  already uses) is attached to the Project and to the Supplier so each record's attachments list shows
  the receipt; the queue row links the Purchase Invoice (`created_docname`, existing) and the Purchase
  Invoice links the queue row (`custom_source_document_intake`, existing).
- **Failure:** filing runs after posting and never fails it (existing rule). A document that posted but
  did not file shows on the exceptions list and is re-driven by the retry job.

## Card purchases with no PO

Two thirds of the volume. The rule:

1. **Company card, parts for a job:** a draft Purchase Invoice to the store Supplier, `is_paid = 1`,
   Mode of Payment = the card (one Mode of Payment per company card, each linked to that card's liability
   account), project and phase on each line, no PO. Lines are expense lines on the job-cost account
   unless the receipt lines resolve to stock Items **and** a store-run Purchase Receipt exists for them;
   then the invoice is made from that receipt.
2. **Company card, overhead** (fuel, shop supplies, meals): the same shape against the Overhead project
   the technician or Lisa picks, phase blank is allowed only on an Overhead project.
3. **Own money:** the existing *Create Reimbursement Bill* handler, a draft Purchase Invoice to the
   employee's reimbursement Supplier (`Employee.custom_reimbursement_supplier`; 9 of 15 active Employees
   linked), project and phase on the line, merchant in the remarks. Unlinked employees are refused with
   the message naming the fix, as built.
4. **A card charge with no receipt** is not this item's problem to create; it is the Bank Reconciliation
   Tool's exception once Plaid is live (WI-056). What this item guarantees is that every receipt that was
   captured is a paid invoice the tool can match on amount and date.
5. **Sales tax on receipts** is captured as the extracted tax total and posted as an item-inclusive amount
   on the line by default (job cost includes the tax paid), with the per-line split kept on the queue row.
   Whether use tax or an input tax account is wanted instead is a CPA question (below).

## Open questions

For **Lisa**:

1. How do supplier invoices arrive today, by count: emailed PDFs, paper, supplier portals (Amazon,
   Grainger)? This sets whether the email inbox or the Drive inbox is the first channel to turn on.
2. Which company cards exist and who carries them? One Mode of Payment and one liability account per
   card is the proposal. Should the card's last four digits be stamped on the invoice for reconciliation?
3. Who should answer *which job?* when the system cannot tell: the PM, the technician who bought it, or
   you? The plan says the PM.
4. Is one-click approval from the list acceptable for a High-confidence card receipt under a threshold,
   and what is the threshold? (The plan ships without auto-approval either way.)
5. The 36 near-duplicate Supplier groups (Crystal Fountains ×3, IFM Efector ×3, Digi-Key ×2, Lowe's ×2,
   and so on): merge them before the pilot (a DATA task, `frappe.rename_doc` with merge), or let the
   alias table absorb them? The plan proposes aliases first and a merge list for you to approve later.
6. Amazon is the busiest supplier (74 POs in 2026, 45 of them at $0) and ships one order as several
   invoices. Should an Amazon invoice always be matched to a PO, or posted standalone against the
   project when no PO exists?

For the **CPA**:

7. Sales tax paid on receipts: include in job cost (default), or separate it to an input-tax or use-tax
   account? Does the answer differ for materials bought in Utah versus out of state?
8. Card purchases as paid Purchase Invoices against the card liability account, settled by the statement
   payment: acceptable, or do you want a clearing account between purchase and statement?
9. Employee reimbursements as bills to a per-person Supplier (the QuickBooks practice, kept): any payroll
   or 1099 implication you want handled differently in ERPNext?
10. Retention of source images: is the Drive copy plus the ERPNext attachment sufficient as the record,
    and for how many years?

For **Nikolas and James**:

11. Is the kiosk the right place for the technician's Receipt button, given it has recorded 15 Job
    Intervals since August? The plan assumes the kiosk becomes the crew's daily tool at the QuickBooks
    Time cutover; if that slips, the button needs a home that does not depend on being clocked in.
12. Document AI processors cost money per page (Google's published pricing; a few cents per page at this
    volume, so on the order of tens of dollars a month). Approve provisioning the four processors in the
    Triton deployment?

## Phased build order

| Phase | Size | What ships | Done when |
|---|---|---|---|
| **0. Foundations** | S | `custom_phase_number` on Purchase Order Item and Purchase Invoice Item (fixture) with header-to-line cascade like project, validated 100 to 800; a patch backfilling `Task.custom_phase_number` from "Phase N00" subjects; `Document Intake` gains `project`, `phase_number`, `payment_method`, `job_interval`, `Needs Attribution`; the stranded-row sweep; Modes of Payment for the cards (CONFIG, Lisa); Document AI processors provisioned in Triton's deployment (CONFIG, Triton repo); an inbound Email Account and the two Drive inbox folders (CONFIG, prod) | `SELECT COUNT(*) FROM \`tabTask\` WHERE is_group=1 AND subject LIKE 'Phase %' AND custom_phase_number=0` = 0; a test upload reaches `Needs Review` with fields filled instead of `Failed` |
| **1. Lisa's inbox** | M | Email and Drive channels live; Supplier normalization and aliases; PO matching narrowed by project and date; project and phase proposed from the PO; card-paid posting shape; Drive filing into the project folder with the Supplier shortcut and the links back; *Attach a bill* on PO and Project forms; the queue list shows image, supplier, total, project, phase and the one-click path | Ten real supplier invoices and ten card receipts flow inbox → draft Purchase Invoice with project and phase and no re-keyed header; each has a Drive link that opens |
| **2. Technician receipts** | M | Kiosk Receipt button and endpoint (session employee, own File only, offline-tolerant); the `Needs Attribution` gate for PMs with a phone-friendly page; store-run Purchase Receipt matching; the Overhead rule | A technician photographs a receipt at a counter and it is in the queue with project and payer within two minutes; the PM answers an attribution from a phone |
| **3. Matching and reporting** | M | Match-later from the queue (journey C); a *Job cost by phase* query report over submitted Purchase Invoice lines plus kiosk labor (`Job Interval.labor_cost`), the figure WI-078 reads; an exceptions report: queue rows older than 7 days by gate, posted but unfiled, projects with spend and no open phase; the capture count as a KPI on the Operations dashboard | WI-078's SOV page shows actual cost per phase for Salt Air from these records; the exceptions report is empty or every row has an owner |
| **4. Later, optional** | S each | MMS channel through Triton; Gemini fallback for low-confidence extractions; one-click approval under a threshold | only if the pilot numbers ask for them |

Phase 0 and Phase 1 are two PRs each (fixtures and patches first, behavior second). The pilot is Salt
Air (PRJ-00604) and the Overhead projects, for one month after Phase 1, measured against the QuickBooks
mirror's bill count for the same month.

## Acceptance criteria

- **Volume parity:** in the pilot month, `SELECT COUNT(*) FROM \`tabDocument Intake\` WHERE status='Posted'`
  is at least 80% of the same month's distinct `qbo_id` count for Bill + Purchase in
  `tabQuickBooks Raw Payload` (while QuickBooks still runs; after the cutover, parity with Lisa's own count).
- **Attribution:** of the Purchase Invoices created from the queue in the pilot month, at least 90% have a
  project on every line and at least 90% of the job (non-Overhead) ones have a phase on every line
  (`SELECT COUNT(*) FROM \`tabPurchase Invoice Item\` WHERE parent IN (...) AND (COALESCE(project,'')=''
  OR COALESCE(custom_phase_number,0)=0)`).
- **Filing:** every `Posted` row has `drive_link` set, and a 20-row sample opens the right file in the
  right project folder; each of those Purchase Invoices has an attached File and the Project and
  Supplier each show the link-only File.
- **Reconcilable:** every card receipt posts as `is_paid = 1` with a Mode of Payment linked to a card
  liability account (`SELECT COUNT(*) FROM \`tabPurchase Invoice\` WHERE custom_source_document_intake IS
  NOT NULL AND is_paid=1 AND COALESCE(mode_of_payment,'')=''` = 0).
- **Nothing auto-submitted:** `SELECT COUNT(*) FROM \`tabPurchase Invoice\` WHERE custom_source_document_intake
  IS NOT NULL AND docstatus=1 AND modified_by IN (<worker users>)` = 0.
- **Failures triaged:** `SELECT COUNT(*) FROM \`tabDocument Intake\` WHERE status='Failed'` = 0 or every
  row is named on the exceptions report with an owner.
- **The journeys, performed:** A (technician, counter, two minutes), B (forwarded email, draft invoice
  against the PO), C (match later), D (Drive inbox), each signed off by the person who performed it.

## Rollback

`Accounting Intake Settings.intake_enabled = 0` stops every channel at once (runtime flag, no deploy);
queued rows stay inert. The kiosk button hides when intake is off. Fixtures (the phase field, the alias
table, the new Document Intake fields) stay in place; removing one is the two-step fixture deletion in
`fixtures/README.md`. Filed Drive documents and created draft invoices are ordinary records and are
left alone.

## Risks

- **Extraction quality on thermal receipts** (faded, crumpled, photographed at an angle). Mitigation: the
  queue shows the image, the technician's two taps supply what OCR cannot (job, payer), and the amounts a
  human approves are the only amounts that reach a draft.
- **The posting gate bottlenecks on one person.** Ten approvals a day is fine; a backlog after a vacation
  is not. Mitigation: the exceptions report, a second Accounts Manager trained, and the one-click path.
- **A deploy destroys queued jobs** (both Redis instances are flushed on every merge to `main`).
  Mitigation: the stranded-row sweep in Phase 0; the design must never assume an enqueued job ran.
- **Phase on tasks is 0 on prod** for every phase group checked (Salt Air, 2026-11-04). Until the
  backfill patch runs, phase proposals fall to `Needs Attribution`; the item ships the patch first.
- **Supplier duplicates** make PO matching miss (a bill from "Crystal Fountains Inc." against a PO on
  "Crystal Fountains"). Aliases cover the matching; the data stays duplicated until Lisa approves a merge.
- **Overhead projects have no Drive folders**; filing to them silently becomes record-only until they are
  provisioned. Phase 0 checklist item.
- **Inbound mail into Frappe** means a mailbox credential on prod. It lives in the Email Account record
  (Password field), never in this repository, which is public. The intake handler only ingests
  attachments on the configured account, so a misrouted email creates nothing.
- **Privacy:** a personal-card receipt can show a card number's last digits and a name. Files are
  private; the kiosk endpoint accepts only the caller's own File; thumbnails never go under `public/`.
- **The kiosk is barely used today.** Phase 2 is sequenced after the crew's move to the kiosk; if that
  move does not happen, the technician channel needs a different home (question 11).
- **Frappe `File` traps already met in this app:** a `/` in a file name is stripped on save; the
  duplicate-entry check reads other rows' names during an insert; `content_hash` is empty for link-only
  rows. Filing code must follow the shadow-sync conventions (`SHADOW_PATH_SEPARATOR`,
  `ignore_duplicate_entry_error`) rather than relearn them.
- **A Single's new fields never backfill on an existing site**; the new Settings fields ship with a
  backfill patch keyed on *no row*, per the repo's CLAUDE.md.

## Explicitly NOT in this work item

- Any QuickBooks integration, write-back, or import of QuickBooks attachments (one `Attachable` mapping
  exists; there is nothing to import).
- Auto-submission of any created document, or auto-approval of any queue row.
- Bank and card statement reconciliation (native Bank Reconciliation Tool; Plaid link is WI-056).
- Supplier payment runs, AP aging, 1099 reporting.
- Customer remittances (the `customer_remittance` handler exists and is left as is) and packing slips
  beyond the 3-way match the module already performs.
- Executing the Supplier merge (a DATA task for Lisa to approve, listed here as a question).
- The estimate, SOV and pay-application records themselves (WI-077, WI-078); this item supplies their
  actual-cost side only.
- Expense Claims, hrms, per diem and the Travel Trip money flow.
- A native kiosk app (WI-076) or any change to clock-in rules.
- Repairing the QuickBooks Time webhook (Nikolas, 2026-11-04: the kiosk replaces it).

## Verified against production

All read-only, through the ERPNext MCP `run_database_query`, 2026-11-04, frappe 16.53 / erpnext 16.52,
app v1.794.0:

| Fact | Figure |
|---|---|
| Purchase Invoices, all time / dated 2026 / submitted | 10 / 0 / 0 |
| Purchase Orders 2026 / submitted / grand total 0 / header project | 247 / 243 / 153 / 208 (100% from June) |
| QuickBooks Bills by TxnDate, Jan–Oct 2026 | 519 (31–67 a month) |
| QuickBooks Purchases Jan–Oct 2026: card / cash or debit / check | 1,406 / 244 / 45 |
| Suppliers / near-duplicate groups / used on a 2026 PO / with a Drive folder | 1,213 / 36 / 57 / 0 |
| Store-Run Vendor Suppliers / store-run Purchase Receipts | 7 / 0 |
| Document Intake rows / Accounting Intake Log rows | 0 / 0 |
| Email Accounts / with incoming enabled | 1 / 0 |
| Drive shadow Files (Project / Customer / Opportunity) | 35,177 (17,163 / 17,627 / 387) |
| Projects / Customers with a Drive folder id | 260 / 1,715 |
| Files attached to Purchase Order / Purchase Invoice / Supplier | 24 / 3 / 11 |
| Role holders: Accounts Manager / Accounts User / Stock Manager / Projects Manager | 8 / 9 / 17 / 17 |
| Active Employees / with a reimbursement Supplier | 15 / 9 |
| Job Intervals since 2026-08-01 / Job Interval Photos | 15 / 6 |
| Bank Transaction rows | 0 |
| Overhead projects | 5 |
