# Where the documents for a job live (Document Hub SOP)

**Status:** SOP draft for WI-063. This is the "where each document type lives" page the work
item calls for. The remaining WI-063 surfacing tasks (list-view Property Setters, the PM
workspace shortcut, and the 20-record UAT) are **business-session-gated** and operator/config
work — see [WI-063](../work-items/WI-063-document-hub.md); no code was written for them here.

## The one rule

**A job's documents live in the job's Google Drive folder — not in email threads, not on
someone's desktop, not in a chat.** Google Drive is the store; ERPNext links to it. If a
document is about a project, a customer, or an opportunity, it goes in that record's Drive
folder, in the right sub-folder below.

## How to reach a job's folder from ERPNext

Every **Project**, **Customer**, and **Opportunity** carries a Drive folder that the app
provisions automatically (on Opportunity→Project conversion and on Customer/Opportunity
insert). From the record:

- Open the record's **custom_drive_folder_id** link, or
- Open the **Drive folder** file attachment on the Project form (it carries the folder's
  `webViewLink`), and

you land in that job's folder in the Shared Drive. One click, the right folder.

> If a record has **no** folder link, its provisioning job may have been lost (e.g. a deploy
> flushed the queue). The daily `resweep_missing_drive_folders` sweep re-creates missing
> folders for Customers/Opportunities; a Project can be re-saved to re-provision. Records that
> pre-date Drive provisioning (~1,600 legacy Customers) have **no** folder unless the
> business accepts the optional backfill (see WI-063 — not run by default).

## The folder template (where each document type goes)

Each provisioned job folder contains this standard sub-folder tree (from
`Project Folder Google Drive Settings → Project Folder Template`, defaulting to):

| Sub-folder | What goes here |
|---|---|
| **Accounting & Legal** | Contracts, signed proposals, change orders, invoices, receipts, permits, insurance certificates, lien waivers — anything that is a financial or legal record of the job. |
| **Build** | Build sheets, fabrication drawings, cut lists, equipment spec sheets, installation photos-in-progress, punch lists, as-builts. |
| **Design** | Water-feature designs, renderings, hydraulic calcs, plans and elevations, client-facing design presentations, revisions. |
| **Project Management** | Schedules, meeting notes, site-visit reports, correspondence worth keeping, general project paperwork that is not accounting/legal, build, or design. |
| **Project Management / Pictures** | Site and job photos: before/during/after, deliveries, site conditions. (Kiosk-captured job photos and maintenance-visit photos also belong here when they are exported for the record.) |

If a document could go in two places, file it by **what it is** (a signed contract is
*Accounting & Legal* even though it came out of *Design*), and file it once — do not scatter
copies.

## Quick reference

- **New contract / invoice / permit** → `Accounting & Legal`
- **Design rendering / plan / hydraulic calc** → `Design`
- **Build sheet / fab drawing / as-built** → `Build`
- **Schedule / meeting notes / site report** → `Project Management`
- **Any job photo** → `Project Management / Pictures`
- **Can't find the folder?** → open the Project/Customer/Opportunity record and click its
  Drive-folder link; if there isn't one, see the note above.
