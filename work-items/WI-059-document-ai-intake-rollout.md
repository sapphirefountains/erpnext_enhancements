# WI-059: Document-AI intake rollout
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-044 (PI/PE approval workflows active — intake output routes through them)   **Blocks:** nothing

## Why
Vendor bills, receipts, packing slips, and customer remittances are re-keyed by hand today. The `accounting_intake` module is already BUILT and merged: four intake channels (manual upload, inbound email attachments, a Google Drive watched folder polled hourly, mobile photo capture), SHA-256 dedup into a 'Document Intake' queue, extraction delegated to Triton (`/api/v1/document-ai/extract`, Google Document AI behind it), and four registered actions each creating a DRAFT document — Purchase Invoice, Expense Claim, Purchase Receipt, Payment Entry — behind a two-gate review (Stock Manager item approval, then Accounts Manager document approval). This work item is rollout, not build.

## Native-first check
No native ERPNext document-AI capability exists. The custom module's OUTPUTS are all native documents (draft PI / Expense Claim / PR / PE), and review posting is human-gated — nothing native is reimplemented. Verdict: configure and adopt the existing custom module; zero new code.

## Preconditions
- WI-044 live: intake-created draft PIs/PEs flow into the activated approval workflows (preparer ≠ approver).
- Triton gateway reachable from prod with valid credentials (Accounting Intake Settings holds the gateway/secret fields).
- An intake email account and/or a Drive watched-folder ID designated by finance.

## Scope
All on the 'Accounting Intake Settings' single (module Accounting Intake) + operator setup:
- `intake_enabled` = 1; `auto_extract` per finance preference; `confidence_threshold` (default 70) reviewed; `default_company` = 'Sapphire Fountains'.
- Channel config: intake Email Account (the Communication after_insert hook filters to it), Drive watched folder + processed folder, mobile capture rollout to the field app users.
- `qbo_writeback_enabled` stays **0** permanently — QuickBooks is dead post-cutover (rule 4); the write-back path was only ever a bridge feature.
- Operator training: the two-gate review flow (approve_items → approve_document), rejection/reprocess paths, and the rule that everything posts as DRAFT (docstatus 0) for human submission.

## Acceptance criteria
- `SELECT value FROM tabSingles WHERE doctype='Accounting Intake Settings' AND field='intake_enabled'` = 1 on prod; `qbo_writeback_enabled` = 0.
- Ten real vendor bills flow inbox → extraction → item/document approval → draft Purchase Invoice with zero re-keying of header or line data (measured over the pilot fortnight).
- `SELECT COUNT(*) FROM \`tabDocument Intake\` WHERE status='Failed'` = 0 or every failure triaged.
- Finance sign-off that the review queue replaces manual entry for the covered document types.

## Rollback
`intake_enabled` = 0 (runtime flag; documents already queued remain inert); channels deactivate with it.

## Explicitly NOT in this work item
New extraction document types or Triton processor changes; auto-submission of created documents (drafts by design); QBO write-back in any form; SLA/volume commitments with the scanning process itself.
