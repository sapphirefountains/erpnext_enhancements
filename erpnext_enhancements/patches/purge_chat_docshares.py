"""Delete every ``DocShare`` on a chat DocType. The controller hook only stops **new** ones.

--------------------------------------------------------------------------------------
What a share does that a permission does not
--------------------------------------------------------------------------------------

A ``DocShare`` row on a chat record is not one more permission among several. On Frappe v16 it
**overrides** the two mechanisms this package's access control is built from, and neither
override is visible from reading our code:

* ``frappe/database/query.py`` calls ``permission_query_conditions``, ANDs every condition
  together, and then ORs the shared names onto the result — under its own comment, *"shared
  docs trump all other restrictions"*. The membership SQL is ANDed; the share is ORed past it.
* ``frappe/permissions.py`` answers a controller hook's ``False`` with
  ``false_if_not_shared()``. That is the single-document gate, and the single-document gate is
  the **realtime boundary**: ``doc_subscribe`` reaches ``chat_room_has_permission`` before
  joining ``doc:Chat Room/<room>``, and membership is never re-checked after the join. A share
  therefore buys a live feed of a conversation, not merely a row in a list.

Zero DocPerm is not immunity, which is the part most likely to be assumed wrong. With no role
permission the query engine returns the shared-name filter **and never calls the hook at all**,
so a share on a ``Chat Message`` makes exactly that message listable.

--------------------------------------------------------------------------------------
Why a patch as well as the hook
--------------------------------------------------------------------------------------

``validate_share`` refuses new rows and cannot touch rows that already exist. Nothing in this
app ever created one deliberately, but three paths could have, and two of them are ordinary
operations rather than attacks: **Administrator** using the desk's "Assigned To" sidebar (for
whom ``check_share_permission`` short-circuits), an **Assignment Rule** with
``document_type = "Chat Room"`` (which calls ``assign_to._add(ignore_permissions=True)``), and
any server-side insert setting ``flags.ignore_share_permission``. None of them announces what it
granted beyond *"Shared with the following Users with Read access"*.

``everyone = 1`` is why this is not left to a report. ``frappe.share.get_shared`` matches it for
every non-Guest user, so a single such row is not one leak but every account at once.

Deletes rather than reports, because a share on a chat record has no legitimate meaning: access
to a conversation is membership. The rows it removes are recoverable by adding a
``Chat Room Member``, which is the thing that should have been done instead.

**Keyed on the doctype, not on emptiness.** ``share_doctype`` is the fact that makes a row
wrong; nothing about its other columns matters. CLAUDE.md's ``Chat Relay Job.auth_identity``
lesson is that a predicate describing the schema migration rather than the data can match
nothing and log itself a success — so this one names the tables outright and reports the count
it actually deleted.

Safe to run twice: the second run finds nothing and deletes nothing.
"""

import frappe

#: Every chat DocType a share could name. Deliberately broader than the two with a DocPerm —
#: `Chat Room` is the one the query engine's share-OR can reach, and the rest are reachable by
#: the zero-DocPerm path above. A table listed here that never had a share costs one indexed
#: DELETE returning nothing.
CHAT_DOCTYPES = (
	"Chat Room",
	"Chat Message",
	"Chat Room Member",
	"Chat Attachment",
	"Chat Message Revision",
	"Chat Audit Log",
	"Chat Retrieval Audit",
)


def execute() -> None:
	if not frappe.db.table_exists("DocShare"):
		return

	removed: dict[str, int] = {}
	for doctype in CHAT_DOCTYPES:
		# Counted before deleting, so the log says what happened rather than that something did.
		# `(share_doctype, share_name)` is indexed by DocShare's own `on_doctype_update`.
		count = frappe.db.count("DocShare", {"share_doctype": doctype})
		if not count:
			continue
		frappe.db.delete("DocShare", {"share_doctype": doctype})
		removed[doctype] = count

	frappe.db.commit()

	if removed:
		detail = ", ".join(f"{doctype}: {count}" for doctype, count in sorted(removed.items()))
		print(f"purged chat DocShare rows — {detail}")
		# Loud, because every one of these was a read somebody had and no longer has, and the
		# person who granted it will not otherwise learn that it went.
		frappe.log_error(
			title="chat: DocShare rows purged",
			message=(
				f"Deleted {sum(removed.values())} DocShare row(s) on chat DocTypes ({detail}). "
				"A share grants a read the membership rules never see and the audit trail never "
				"records. Access to a conversation is membership — add a Chat Room Member."
			),
		)
	else:
		print("purged chat DocShare rows — none found")
