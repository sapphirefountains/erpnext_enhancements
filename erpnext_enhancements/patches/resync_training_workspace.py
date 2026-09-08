"""Force the Training desk workspace to re-sync from its app JSON.

The prod `Workspace` record still carried the original three-card layout it was
installed with on 2026-08-01 -- Authoring / Learners / Setup, eight links -- while
the app JSON in this repo had grown extra cards (Verification, Recognition,
Reports, and now Cohorts & Sessions) and many more links. None of that ever
reached the desk, and the reason is the importer's staleness check, not the data:

Frappe imports a module record only when the file is *newer* than the stored row
(``import_file`` compares ``modified``), and the workspace JSON's ``modified`` had
been left at the install timestamp through every later edit. So on each migrate the
file and the row read as the same age and the import was skipped -- the added cards
sat in the repo and never synced. (Confirmed on prod: the row and the file both
read ``2026-08-01 12:00:00`` while the file already described six cards.)

The JSON's ``modified`` is now bumped past the install date, which is enough on its
own where the check is timestamp-gated; this patch is the belt to that suspenders,
forcing the import so the fix also lands where the sync would otherwise leave an
existing public workspace untouched. ``reload_doc(..., force=True)`` re-reads the
file and rebuilds the row regardless of the age check.

Safe twice: it re-imports the same JSON. It intentionally overwrites the desk copy
-- the row on prod is the untouched install default, which is the incomplete one
this ships to replace.
"""

import frappe


def execute():
	frappe.reload_doc("training", "workspace", "training", force=True)
