"""Force the Quality Control desk workspace to re-sync from its app JSON.

v1.454.0 adds a **Run an inspection** shortcut pointing at the new `inspection-wizard` Page, so
the field tool is reachable from the desk rather than only by typing the URL. Without this patch
that edit would very likely never arrive.

Frappe's ``import_file`` imports a module record only when the *file* is newer than the stored
row, comparing ``modified`` — workspaces are **timestamp-gated**, unlike DocTypes, which are
hash-gated. The JSON's ``modified`` is bumped alongside this patch, which is enough where the
check is purely timestamp-based.

This is the belt to that suspenders. ``reload_doc(..., force=True)`` re-reads the file and
rebuilds the row regardless of the age check, which also covers the case that actually bit this
repo before: somebody hand-editing the workspace in the Desk moves the row's timestamp forward,
and from then on the file loses silently, forever. That is how the Training workspace sat on its
three-card install default for weeks while the repo described six.

Safe twice: it re-imports the same JSON.
"""

import frappe


def execute():
	frappe.reload_doc("quality", "workspace", "quality_control", force=True)
