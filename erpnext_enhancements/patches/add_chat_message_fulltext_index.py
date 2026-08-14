"""The FULLTEXT index on ``Chat Message.text_plain`` that retrieval has been querying without.

--------------------------------------------------------------------------------------
Two phases disagreed, and neither was wrong on its own
--------------------------------------------------------------------------------------

``chat/api/search.py`` says it plainly: message search uses ``LIKE`` because *"no FULLTEXT
index has been created — adding one is a migration with its own rollout"*. That was a
considered Phase 1 decision and it is still a reasonable one for search.

Phase 5's retrieval gate then wrote ``_authored_by`` against the same column with
``match(`text_plain`) against (… in boolean mode)`` — which needs the index that decision
deferred. ``add_chat_phase5_indexes`` created a FULLTEXT index for the *other* MATCH in that
module (``Chat Context Chunk.body``) and not for this one, so exactly one of the gate's two
full-text searches had an index behind it.

MariaDB answers the other with **1191, Can't find FULLTEXT index matching the column list** —
deterministic, every execution. It killed every ``@triton`` turn in retrieval, and it was the
second such fault in the same afternoon: the first was a column that did not exist, and this is
an index that did not exist. Both are the same class — SQL naming something the schema does not
have, invisible to every test that never connects to a database.

--------------------------------------------------------------------------------------
The cost, stated rather than discovered
--------------------------------------------------------------------------------------

``ALTER TABLE … ADD FULLTEXT`` builds the index over every existing row and holds the table
while it does. On this deployment ``Chat Message`` is small and it is imperceptible; on a site
with years of history it is not, which is precisely why Phase 1 deferred it. If that is ever
the case here, the alternative is to change ``_authored_by`` to ``LIKE`` and accept losing
relevance ordering on that one tier — a smaller loss than it sounds, because the tier is
"messages you wrote" and already bounded by ``seq desc``.

Reuses ``add_chat_phase5_indexes``'s own helper rather than repeating its DDL: that function
already handles the missing table, the missing column, the index that is already there, and the
fact that ``frappe.db.add_index`` cannot emit ``FULLTEXT`` at all. A second copy would be a
second place for the next person to fix.

Idempotent: ``_apply_fulltext`` returns early when the index exists.
"""

import frappe

from erpnext_enhancements.patches.add_chat_phase5_indexes import _apply_fulltext

MESSAGE_DOCTYPE = "Chat Message"
COLUMN = "text_plain"
INDEX_NAME = "message_text_plain_fulltext"


def execute() -> dict[str, list[str]]:
	return add_chat_message_fulltext_index()


def add_chat_message_fulltext_index() -> dict[str, list[str]]:
	"""Create the index. Returns ``{"failures", "skipped"}`` — empty both means it is there."""
	failures: list[str] = []
	skipped: list[str] = []

	_apply_fulltext(MESSAGE_DOCTYPE, COLUMN, INDEX_NAME, failures=failures, skipped=skipped)

	if failures or skipped:
		# Not raised. A missing index degrades one retrieval tier; a patch that aborts `bench
		# migrate` over it takes the whole deploy with it. The same stance the phase 5 index
		# patch takes, and for the same reason.
		frappe.log_error(
			"Chat Message FULLTEXT index not created:\n"
			+ "\n".join(failures + skipped)
			+ "\n\nUntil it exists, gate._authored_by raises OperationalError(1191) and every "
			"@triton turn fails in retrieval.",
			"Chat Phase 5 indexes",
		)

	frappe.db.commit()
	return {"failures": failures, "skipped": skipped}
