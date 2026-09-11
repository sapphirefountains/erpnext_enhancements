"""Force the AI Governance desk workspace to re-sync from its app JSON.

v1.403.0 adds a **Call Routing** shortcut and link card so the new
`Call Routing Settings` / `Call Routing Rule` DocTypes are reachable from the desk
rather than only from the awesome bar. Without this patch that edit would very
likely never arrive.

Frappe's ``import_file`` imports a module record only when the *file* is newer than
the stored row, comparing ``modified``. This workspace's JSON had carried
``2026-06-15 00:00:00`` since it was written, so file and row read as the same age
on every migrate and the import was skipped. That is exactly how the Training
workspace sat on its three-card install default for weeks while the repo described
six cards (``resync_training_workspace``).

The JSON's ``modified`` is bumped alongside this patch, which is enough where the
check is purely timestamp-gated. This is the belt to that suspenders:
``reload_doc(..., force=True)`` re-reads the file and rebuilds the row regardless
of the age check, which also covers the case that actually bit us before — somebody
hand-editing the workspace in the Desk, which moves the row's timestamp forward and
makes the file lose silently from then on.

Safe twice: it re-imports the same JSON.
"""

import frappe


def execute():
	frappe.reload_doc("ai_governance", "workspace", "ai_governance", force=True)
