# WI-063: Document hub (Drive-backed; define before building)
**Phase:** 2   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-006 (Drive provisioning verified working)   **Blocks:** nothing

## Why
The business wants "one place to find the documents for a job." Google Drive stays the store (brief). The cross-links already exist in the app — `Project.custom_drive_folder_id`, `Customer.custom_drive_folder_id`, `Opportunity.custom_drive_folder_id`, plus a File attachment carrying the folder's webViewLink on each Project — so the "hub" is almost certainly a surfacing problem, not a build problem. This item defines it before anyone writes code.

## Native-first check
The provisioning and cross-linking layer is the existing custom `google_drive` module (built, wired via the Closed-Won handoff and Customer/Opportunity after_insert hooks). Surfacing is native: Workspace shortcuts, list-view columns via Property Setter (`in_list_view` on the folder-link field), and the existing attachment link on the form. Verdict: CONFIG-only definition + surfacing pass. **Any APP_CODE beyond link surfacing (search across Drive, embedded browsing, permission mirroring) requires its own justified work item with a native-first check — none is authorized by this item.**

## Preconditions
- WI-006 acceptance met: service account is Content Manager on the Shared Drive; provisioning verified end-to-end; folder toggles ON post-imports.
- A 30-minute session with the PM + accountant: what "finding the documents" actually means day-to-day (per-project? per-customer? per-invoice scan?).

## Scope
- Surface the existing links consistently: the Drive-folder link visible on Project/Customer/Opportunity forms (verify the File webViewLink attachment renders and opens for a normal PM user), workspace shortcut to the Shared Drive root, and — if the session asks for it — a list-view column Property Setter (rides the WI-019 fixture batch conventions).
- SOP: where each document type lives in the provisioned folder template (Accounting & Legal / Build / Design / Project Management/Pictures), and the rule that job documents go in the job folder, not email threads.
- A written definition memo: either "hub = the surfaced links + SOP, done" or a scoped proposal for anything more (separate work item).
- Decide the backfill question explicitly: folders for the ~1,600 pre-existing Customers are NOT auto-created (the hooks only fire on insert) — enumerate backfill as an optional DATA item for the business to accept or decline; do not run it by default.

## Acceptance criteria
- From Project and Customer forms, one click reaches the correct Drive folder for 100% of a 20-record UAT sample (records created after WI-006 went live).
- SOP page published and linked from the PM workspace.
- Definition memo signed by the PM lead; no unauthorized code written.

## Rollback
Remove shortcuts/columns; links and folders are unaffected.

## Explicitly NOT in this work item
Drive search/browse UI inside ERPNext; permission synchronization between ERPNext roles and Drive; the customer-folder backfill DATA run (enumerated, business-gated); call-recordings folder handling (Telephony concern).
