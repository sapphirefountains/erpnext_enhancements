"""Re-prefix the ``spa-`` client message ids that Google will never accept.

--------------------------------------------------------------------------------------
Why these rows exist and why they cannot relay
--------------------------------------------------------------------------------------

``client_message_id`` becomes Google's ``clientAssignedMessageId``, and Google requires the
``client-`` prefix — it is *their* namespace, not ours. The SPA minted ``spa-`` until v1.277.x.
The id is derived once in ``before_insert`` and stored forever, so every message composed
before that release carries a value the relay's own pre-flight check rejects:

    ValueError: messageId rejected: clientAssignedMessageId must begin with 'client-'
                (got 'spa-659cb1cc-28ab-4cfd-b3b9-f2d4289f7cbf')

Rejected *before the request is sent*, so it is classified non-retryable and dead-lettered.
Fixing the minting fixed every message composed since and reached none of these — the third
time in this feature that a repair did not reach the rows it was for.

On production this is 11 messages: 3 already dead, and **8 still queued**, which would have
died the moment their room's membership unblocked. The queue looked like it was waiting on
something fixable; it was waiting to fail.

--------------------------------------------------------------------------------------
Only where nothing at Google can be pointing at the old id
--------------------------------------------------------------------------------------

The rewrite is confined to messages with **no ``gchat_message_name``** — never successfully
relayed, so Google has never seen the id and no Chat resource references it. That is what
makes rewriting an otherwise-immutable identity safe here rather than reckless.

If a message *had* relayed, the old id is the one Google holds, and changing it would break
both directions at once: an inbound echo would no longer match (invariant I3 infers "our own
echo" from a ``client-`` id we issued), and a later edit or delete addressed by client alias
would miss. There is no safe repair for that case, which is why this patch does not attempt
one — a row like that has nothing wrong with it anyway.

The unique index on ``client_message_id`` is respected rather than trusted to be unreachable:
a row whose target id already exists is skipped and counted, not collided. The uuid half is
unchanged, so this can only happen if the same message was somehow re-minted, and "impossible"
is not a reason to write a statement that would abort the patch mid-run if it happened.

Idempotent: the second run matches nothing, because ``like 'spa-%'`` no longer holds.

--------------------------------------------------------------------------------------
What it does NOT do
--------------------------------------------------------------------------------------

It does not requeue the three **dead** jobs. Reviving a dead-lettered job is an operator
decision with a button already built for it (``retry_relay_job``), and a patch that silently
re-drove traffic to Google during ``bench migrate`` would be doing something nobody asked it
to do at a moment nobody is watching. The eight *pending* ones need nothing: the worker reads
``client_message_id`` from the message row when it builds the request, so they pick up the
corrected value on their next attempt.
"""

import frappe

MESSAGE = "Chat Message"
OLD_PREFIX = "spa-"
NEW_PREFIX = "client-"


def execute() -> dict[str, int]:
	return backfill_spa_client_message_id()


def backfill_spa_client_message_id() -> dict[str, int]:
	"""Rewrite never-relayed ``spa-`` ids. Returns ``{"rewritten", "skipped_relayed", "collided"}``."""
	if not frappe.db.exists("DocType", MESSAGE):
		return {"rewritten": 0, "skipped_relayed": 0, "collided": 0}
	for column in ("client_message_id", "gchat_message_name"):
		if not frappe.db.has_column(MESSAGE, column):
			return {"rewritten": 0, "skipped_relayed": 0, "collided": 0}

	candidates = frappe.db.sql(
		"""
		select `name`, `client_message_id`
		from `tabChat Message`
		where `client_message_id` like %(pattern)s
			and coalesce(`gchat_message_name`, '') = ''
		""",
		{"pattern": OLD_PREFIX + "%"},
		as_dict=True,
	)

	# Counted separately so the return value distinguishes "left alone on purpose" from
	# "nothing to do". A patch that reports 0 for both reads as a no-op when it was a refusal.
	skipped = frappe.db.count(MESSAGE, {"client_message_id": ("like", OLD_PREFIX + "%")}) - len(
		candidates
	)

	rewritten = 0
	collided = 0
	for row in candidates:
		target = NEW_PREFIX + str(row["client_message_id"])[len(OLD_PREFIX) :]
		if frappe.db.exists(MESSAGE, {"client_message_id": target}):
			collided += 1
			continue
		# `db.set_value` rather than a document save: this must not fire `Chat Message`'s
		# doc_events. A save would re-enter the outbox and enqueue a *second* relay job for a
		# message that already has one queued, which is how a repair becomes a duplicate.
		frappe.db.set_value(MESSAGE, row["name"], "client_message_id", target, update_modified=False)
		rewritten += 1

	frappe.db.commit()
	return {"rewritten": rewritten, "skipped_relayed": max(0, skipped), "collided": collided}
