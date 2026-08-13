"""Stamp ``APP`` on the Triton replies that were already queued when the column arrived.

--------------------------------------------------------------------------------------
The claim this patch exists to correct
--------------------------------------------------------------------------------------

v1.279.0 made ``_identity_of`` default a missing ``auth_identity`` to ``USER``, and justified
it like this: *"every job already in flight was written for the coworker mirror, and defaulting
the other way would silently re-attribute a backlog of people's own messages to the app."*

The **direction** is right and stays. The factual half was not checked and is false. There are
Triton replies sitting in this queue right now — deferred, repeatedly, with
``triton@sapphirefountains.com is not a JOINED member of room …; membership sync has not
completed``. Those are exactly the jobs the release was written to unblock, and the default
hands each of them the identity that cannot post: they keep deferring, forever, against a
membership row that will never exist because the app is not a member of anything.

So the release fixed every *future* reply and left the stuck ones stuck. That is the same shape
as the digest ``poisoned`` flag two releases earlier — a repair that does not reach the rows the
repair was for — and it is worth naming twice because it did not announce itself either time:
the queue keeps deferring politely and the feature stays dark.

--------------------------------------------------------------------------------------
What it touches, and what it deliberately does not
--------------------------------------------------------------------------------------

Only rows where ``auth_identity`` is **empty**. That set is exactly "written before the column
existed": every row written since carries a decision the writer made from ``sync_origin``, and
this must not overrule one. It never rewrites a ``USER`` — a patch that could would be a patch
that can re-attribute a coworker's message to the app, which is the one outcome CQ-1 exists to
prevent.

Only ``Pending`` and ``Failed``. ``In Progress`` is claimed by a worker that has already read
the row and is mid-flight; changing the identity underneath it means the client it built and
the row it writes back disagree. It will land in ``Pending`` or ``Failed`` on its own, and the
next run of this patch — or the operator's retry — catches it there. ``Dead`` and ``Skipped``
are settled; reviving them is the manual-retry button's job and a human's decision.

Idempotent by construction: the second run matches nothing, because the first run filled the
column it filters on.
"""

import frappe

RELAY_JOB = "Chat Relay Job"
MESSAGE = "Chat Message"
ORIGIN_TRITON = "Triton"


def execute() -> int:
	return backfill_relay_auth_identity()


def backfill_relay_auth_identity() -> int:
	"""Set ``auth_identity = APP`` on queued Triton replies. Returns the row count."""
	if not frappe.db.exists("DocType", RELAY_JOB):
		return 0
	# The column is what this patch writes, so a site that has not migrated yet has nothing to
	# do rather than a crash — patches and schema changes do not have a guaranteed order across
	# a rollback.
	if not frappe.db.has_column(RELAY_JOB, "auth_identity"):
		return 0

	frappe.db.sql(
		"""
		update `tabChat Relay Job` j
		join `tabChat Message` m on m.`name` = j.`reference_name`
		set j.`auth_identity` = 'APP'
		where j.`reference_doctype` = %(message)s
			and m.`sync_origin` = %(origin)s
			and coalesce(j.`auth_identity`, '') = ''
			and j.`status` in ('Pending', 'Failed')
		""",
		{"message": MESSAGE, "origin": ORIGIN_TRITON},
	)
	updated = int(frappe.db.sql("select row_count()")[0][0] or 0)

	# Everything else written before the column existed is a coworker mirror, and USER is what
	# `_identity_of` already infers for it. Written down anyway rather than left implicit: an
	# empty column that means one thing to the worker and nothing on the row is how the next
	# person reading a stuck job concludes the field was never populated.
	frappe.db.sql(
		"""
		update `tabChat Relay Job`
		set `auth_identity` = 'USER'
		where coalesce(`auth_identity`, '') = ''
		"""
	)

	frappe.db.commit()
	return updated
