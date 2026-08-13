"""Stamp ``APP`` on queued Triton replies — the job ``backfill_relay_auth_identity`` did not do.

--------------------------------------------------------------------------------------
Why the first patch was a no-op, and logged as a success
--------------------------------------------------------------------------------------

``backfill_relay_auth_identity`` matched ``coalesce(auth_identity, '') = ''``. That guard was
chosen deliberately, and the reasoning was sound: rows written since the column arrived carry a
decision the writer made, and a backfill able to overwrite one is a backfill able to
re-attribute a coworker's own message to the app. "Only fill what nobody decided" is the right
shape.

**The column was never empty.** ``Chat Relay Job.auth_identity`` declares ``"default": "USER"``,
and when ``bench migrate`` adds a column with a default, MariaDB writes that default into every
existing row as part of the ``ALTER``. So the schema change filled the column before the patch
could look at it, the patch matched nothing, and it recorded itself in ``tabPatch Log`` as
having run — which is exactly what a successful run looks like from the outside.

Note the asymmetry against the one this repository already documents, because they point in
opposite directions and both have now cost a release:

* a ``default`` on a new field of a **Single** never reaches the existing row (``tabSingles``
  stores one row per field and ``bench migrate`` adds none), so the value silently reads as
  ``None``;
* a ``default`` on a new field of a **normal** doctype reaches *every* existing row, so the
  value silently reads as the default.

"New field, existing rows" has two opposite answers depending on the storage model, and neither
is the one you assume.

--------------------------------------------------------------------------------------
What this one matches instead
--------------------------------------------------------------------------------------

The writer's own rule, applied to the rows the writer never saw: ``sync_origin = 'Triton'``
means the app identity, and that is precisely what :func:`outbox.auth_identity_for_origin`
decides for every job written since.

Overwriting ``USER`` here is therefore safe in the one way that matters. The original guard
existed to protect a **coworker's message** from being re-stamped as the app; a Triton-origin
row is a bot reply by construction — ``handler._post_reply`` is the only thing that writes that
origin — so the case the guard protects cannot occur inside this predicate.

Idempotent by outcome rather than by emptiness: the second run matches nothing because the rows
it would match are already ``APP``. ``Dead`` jobs are left alone, as before — reviving a
dead-lettered job is an operator decision with a button already built for it.
"""

import frappe

RELAY_JOB = "Chat Relay Job"
MESSAGE = "Chat Message"
ORIGIN_TRITON = "Triton"
IDENTITY_APP = "APP"


def execute() -> int:
	return restamp_triton_relay_auth_identity()


def restamp_triton_relay_auth_identity() -> int:
	"""Set ``auth_identity = APP`` on open Triton-origin relay jobs. Returns the row count."""
	if not frappe.db.exists("DocType", RELAY_JOB):
		return 0
	if not frappe.db.has_column(RELAY_JOB, "auth_identity"):
		return 0

	frappe.db.sql(
		"""
		update `tabChat Relay Job` j
		join `tabChat Message` m on m.`name` = j.`reference_name`
		set j.`auth_identity` = %(app)s
		where j.`reference_doctype` = %(message)s
			and m.`sync_origin` = %(origin)s
			and coalesce(j.`auth_identity`, '') != %(app)s
			and j.`status` in ('Pending', 'Failed')
		""",
		{"message": MESSAGE, "origin": ORIGIN_TRITON, "app": IDENTITY_APP},
	)
	updated = int(frappe.db.sql("select row_count()")[0][0] or 0)
	frappe.db.commit()
	return updated
