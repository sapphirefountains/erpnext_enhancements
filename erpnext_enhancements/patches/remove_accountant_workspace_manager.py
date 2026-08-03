"""Take "Workspace Manager" off the accountant (v1.238.0, WI-018 part two).

This is the one change that makes WI-018 possible at all. `get_workspaces()`
opens with `has_access = "Workspace Manager" in frappe.get_roles()`, and when
that is true it drops the query filters entirely — role restrictions on a
Workspace, `is_hidden`, and blocked modules are **all** bypassed
(`frappe/desk/desktop.py`). So while the accountant holds the role, every lever
WI-018 proposes is inert: we can restrict the Finance Hub by role, hide the empty
hubs and block twenty modules, and she still sees all 61 workspaces.

It also means WI-018's original acceptance criterion — `COUNT(*) FROM
tabBlock Module WHERE parent=<accountant> > 0` — could pass while her sidebar was
completely unchanged. That criterion has been replaced; see the work item.

**This is not a permission.** `block_modules` and `Workspace Manager` are consulted
only by the desk's workspace and desktop-icon code; `frappe/permissions.py` never
looks at either. She keeps read/write on every document she can reach today. What
she loses is the ability to edit workspace *layouts* in the Desk — and note she
can still create a private workspace of her own, because `Desk User` (auto-granted
to every System User) holds create on Workspace.

Deliberately narrow: the accountant only. Six other holders remain (Administrator,
the CEO, the sys-admin, the purchasing agent, the external CPA, and the Triton
service account). Trimming those is a WI-011 amendment, not this work item.

Written with `frappe.db.delete` rather than `doc.save()` so an 89-role User is not
re-validated to remove one row — but that means the document cache must be cleared
by hand. `frappe.clear_cache(user=...)` does **not** do it: that clears the
`user:<email>*` keys, while the User doc is cached separately under
`document_cache::User::<email>` for an hour, and `get_workspaces()` reads the role
list from `frappe.get_cached_doc("User", ...)`. Without the explicit
`clear_document_cache` the change appears not to have worked for up to an hour.

Idempotent. Reversing it is a re-tick in the Desk, which takes seconds — but note
that nothing alerts if someone does, and her sidebar silently returns to 61.
"""

import frappe

ROLE = "Workspace Manager"
ACCOUNTANT = "lisa.symanski@sapphirefountains.com"


def execute():
	if not frappe.db.exists("User", ACCOUNTANT):
		return
	if not frappe.db.exists("Has Role", {"parent": ACCOUNTANT, "parenttype": "User", "role": ROLE}):
		return

	frappe.db.delete("Has Role", {"parent": ACCOUNTANT, "parenttype": "User", "role": ROLE})
	frappe.clear_document_cache("User", ACCOUNTANT)
	frappe.clear_cache(user=ACCOUNTANT)
