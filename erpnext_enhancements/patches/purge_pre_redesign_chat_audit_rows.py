# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Remove ``Chat Retrieval Audit`` rows written by the v1.268.0 design, which was withdrawn.

**Deleting audit rows is exactly what this feature forbids, so the reason has to be good.**
It is, and it is narrow: the rows this removes are not a record of anything a person did.

v1.268.0 wrote the audit row from inside :func:`chat.permissions.note_privileged_read`, i.e.
from the permission hooks themselves. Those hooks fire while a query is being *built*, before
any content is returned and before the permission answer is known — and they fire for
``Administrator``, so background jobs tripped them too. A row from that design says only
"something asked for an unrestricted scope": no rooms, no message count, no query, and an
``accessed_by`` of ``Administrator``, which the field's own description calls meaningless.

Two further facts make them worse than useless:

* their ``chain_hash`` was computed over a ``creation`` value the database never stored
  (``Document.insert`` overwrites it via ``set_user_and_timestamp``), so every one of them
  fails verification; and
* they predate ``recorded_at``, the field the chain now signs, so it is ``NULL`` on all of
  them and they cannot be re-verified under the new scheme either.

Leaving them means ``verify_chain`` reports a break forever, on rows nobody tampered with —
the "alarm that always fires" the module warns about, which trains people to ignore the one
that matters.

**Selected on ``recorded_at is null``, which is precisely "written before the redesign".**
Not on a date, not on ``accessed_by`` — a rule that could ever match a real row is the wrong
rule for a table like this. Any row written from v1.269.0 onward has ``recorded_at`` set
(it is ``reqd``), so this cannot match one however often it runs.

Idempotent: a second run finds nothing. Safe on a fresh site, where the table is empty.
"""

import frappe

from erpnext_enhancements.chat.doctype.chat_retrieval_audit.chat_retrieval_audit import (
	RETENTION_PURGE_FLAG,
)


def execute() -> None:
	if not frappe.db.table_exists("Chat Retrieval Audit"):
		# Fresh site, or a site where the migrate that creates the table has not run yet.
		return

	stale = frappe.db.sql(
		"""select `name` from `tabChat Retrieval Audit`
			where `recorded_at` is null""",
		pluck=True,
	)
	if not stale:
		return

	# The one flag the controller accepts, set by the one caller allowed to purge. The
	# controller refuses every other delete, which is why this cannot be done with a bare
	# `frappe.db.delete` and should not be.
	frappe.flags[RETENTION_PURGE_FLAG] = True
	try:
		for name in stale:
			frappe.delete_doc(
				"Chat Retrieval Audit",
				name,
				ignore_permissions=True,
				force=True,
				delete_permanently=True,
			)
	finally:
		frappe.flags[RETENTION_PURGE_FLAG] = False

	frappe.db.commit()
	print(  # noqa: T201 - patches report to the migrate log, which is where this belongs
		f"chat: removed {len(stale)} pre-redesign Chat Retrieval Audit row(s) "
		f"(written by permission hooks in v1.268.0; no rooms, no counts, unverifiable)"
	)
