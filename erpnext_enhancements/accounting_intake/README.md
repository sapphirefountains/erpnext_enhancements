# `accounting_intake/` — accounting document intake

Vendor bills, receipts and customer remittances arrive by email, Drive, mobile and chat.
This module funnels all of them through one door, extracts their contents with AI, proposes
a posting, and puts a human in front of it before anything reaches the ledger.

Pipeline overview: [`docs/DOCUMENT_MERGE.md`](../../docs/DOCUMENT_MERGE.md) covers the
related merge behaviour; this README is the code map.

## Flow

```
channel ──> intake.ingest_document ──> Document Intake (review queue)
                  (dedupe by                    │
                   content hash)         extraction via Triton
                                                │
                                     matching (advisory only)
                                                │
              Stock Manager approves new Items ─┤
                                                │
              Accounts Manager approves ────> Approved
                                                │
                                     actions.post_document (enqueued)
                                                │
                                     DRAFT ERPNext record  ──> filing
```

## Everything posted is a draft

Every posting handler creates a **docstatus 0** record — never submitted. The accountant has
approved the *proposal*; the resulting Purchase Invoice, Expense Claim or Payment Entry still
goes through the normal ERPNext review and submit flow.

This is the module's central safety property. A handler that submits its output collapses two
independent approvals into one and puts AI-extracted figures straight into the ledger.

## File map

| File | Purpose |
|---|---|
| `intake.py` | **The single entry point every channel funnels through.** `ingest_document` dedupes by content hash, creates the `Document Intake` row, and (when enabled) enqueues extraction via Triton. The manual-upload channel lives here |
| `channels.py` | The other adapters, all thin wrappers over that one door: `email_from_communication` (inbound-email attachments, on `Communication.on_update` — the mail pipeline creates the Files *after* insert, so `after_insert` sees none), `poll_watched_folder` (a Google Drive folder, hourly), plus mobile and chat-origin |
| `extraction.py` | Maps a Triton Document AI extraction onto the review record — header fields, line items with Item resolution, advisory matches, resulting review status. Items that can't be resolved are **proposed on the line** for the inventory clerk rather than created |
| `matching.py` | Advisory party (Supplier/Customer) and source-document (PO / Sales Invoice) suggestions, reusing the pure fuzzy scorer in `google_drive/drive_match.py` |
| `review.py` | The whitelisted review actions and the two-gate approval (below) |
| `actions/base.py` | `post_document` — the enqueued dispatcher that routes an Approved document to its per-type handler |
| `actions/vendor_bill.py` | → draft Purchase Invoice. With a matched PO carrying stock items, creates a draft Purchase Receipt first (3-way match); otherwise invoices against the PO, or builds standalone. Also serves company-card receipts |
| `actions/receipt_expense.py` | → draft Expense Claim (employee reimbursement). Company-card receipts go to `vendor_bill` instead |
| `actions/customer_remittance.py` | → draft Payment Entry (Receive), allocated against the reviewer-selected Sales Invoice, or on-account. Field recipe mirrors `quickbooks_online/core/mapping.py::_map_payment_entry` |
| `filing.py` | Attaches the source document to the created record, and — when enabled — pushes a copy to the party's Google Drive folder |
| `setup.py` | `after_migrate` — adds `custom_drive_folder_id` to Supplier, mirroring the existing Customer/Project/Opportunity fields |

## Two reviewers, two gates

Approval is deliberately split by competence, not merged into one button:

1. **Stock Manager** — `approve_items`, approving any proposed new Items. An accountant
   should not be inventing Item codes.
2. **Accounts Manager** — `approve_document`, which moves the record to `Approved` and
   triggers posting.

## Matching is advisory, always

A suggested supplier or purchase order is a suggestion. The reviewer decides, and **a
no-match never blocks posting** — it just means the reviewer fills it in. Making a match
mandatory would stall the queue on exactly the documents that most need a human.

## Filing is best-effort

Drive filing runs *after* a successful posting and **never fails the posting it follows**.
Suppliers get a folder provisioned under a configurable Shared Drive and parent folder, with
an "Accounting & Legal" subfolder found-or-created. A Drive outage must not turn into an
accounting outage.

## DocTypes

| DocType | Role |
|---|---|
| `Document Intake` | The review-queue record: source file, extracted header, status |
| `Document Intake Line` | Extracted line items, including proposed new Items |
| `Document Intake Match` | Advisory party / source-document matches |
| `Accounting Intake Settings` | Single — channel toggles, Drive filing config, feature gates |
| `Accounting Intake Log` | Audit of intake and posting activity |

## Related

Triton exposes a `document_intake_queue` MCP tool over this pipeline; ownership of Document
Intake between the two systems is covered in Triton's `docs/convergence.md`.
