# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give back the certifications a nullable-column comparison took away.

``certificates._expire_certificates`` and ``_expire_completions_and_reassign`` both
filtered ``{"expires_on": ["<", nowdate()]}``. ``expires_on`` is **nullable on both
doctypes and NULL means never expires** — ``_default_dates`` only fills it when the
course carries ``certificate_valid_months``.

Frappe wraps a comparison on a nullable column in an ifnull sentinel set to the
*minimum* of the type (``frappe/model/db_query.py``, ``prepare_filter_condition``)::

    ifnull(`expires_on`, '0001-01-01') < '2026-09-13'

which every NULL row satisfies. So **every never-expiring certification was marked
Expired**, and the completion sweep then raised a recertification assignment telling
the holder to retake a course that had not lapsed. The fix is in the same release:
an ``["expires_on", "is", "set"]`` clause on both filters.

Found on 2026-09-13 by the Crew Qualification Roster, which derives state from stored
dates and never reads a ``status`` column — it reported two of these as *Passed — no
expiry* while the stored status said Expired, and that disagreement was the finding.

--------------------------------------------------------------------------------------
The predicate is the writer's own rule, not emptiness
--------------------------------------------------------------------------------------

``status = "Expired"`` **and** ``expires_on IS NULL``. That pair is not circumstantial:
it is precisely the set the broken filter selected and nothing else can produce it.
``TrainingCertificate._derive_status`` guards its own expiry branch with ``if
self.expires_on and ...``, so the controller cannot mint one; and the sweep is the only
writer of ``Expired`` on either doctype. A row carrying no expiry date cannot honestly
be expired.

Written with ``db.set_value(..., update_modified=False)`` — the same way the sweep wrote
them — rather than through the doc API, because both are submitted and ``save()`` on a
submitted document is refused.

--------------------------------------------------------------------------------------
The assignments, and the one risk this patch will not take blind
--------------------------------------------------------------------------------------

``_raise_recertification`` has two branches and **sets ``assignment_source =
"Recertification"`` in both**: it inserts a new assignment, or it re-dates an existing
open one. The source field therefore cannot tell a spurious insert from a legitimate
assignment that merely had its due date moved, and cancelling the latter would take
away work somebody is actually meant to do.

So this only cancels an assignment that is still ``Not Started`` — no attempt, no
progress, nothing to lose — and it **prints every name it touches**. On this site the
single affected row (``TRN-ASG-000008``, raised 2026-09-12 against
``Using the Training Module``) was verified to be a fresh insert rather than a re-date:
every earlier assignment for that pair was already ``Completed``, so there was no open
row for the other branch to have found.

Anything not ``Not Started`` is left alone and reported, because a half-done retake is
somebody's actual work and a patch is the wrong place to decide what happens to it.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

``post_model_sync``. Safe to run twice — the second run finds nothing, because the rows
it repaired no longer match. Every section is wrapped: a patch that raises aborts
``bench migrate``, which on this repo is the deploy, and failing to repair a status is
not worth a failed deploy.
"""

import frappe

CERTIFICATE = "Training Certificate"
COMPLETION = "Training Completion"
ASSIGNMENT = "Training Assignment"

EXPIRED = "Expired"
VALID = "Valid"

# The Select option as it is actually spelled on `Training Assignment.status`. Two Ls
# here deliberately: an off-options value makes the row unsaveable and renaming an
# option is a data migration of its own, so this matches the doctype rather than the
# house style used elsewhere.
CANCELLED = "Cancelled"


def execute():
	restored = {}
	for doctype in (COMPLETION, CERTIFICATE):
		restored[doctype] = _restore(doctype)

	_withdraw_spurious_assignments(restored.get(COMPLETION) or [])


def _restore(doctype):
	"""Put back every row the broken filter expired. Returns the rows it repaired."""
	try:
		if not frappe.db.exists("DocType", doctype):
			return []
		rows = frappe.get_all(
			doctype,
			filters=[["status", "=", EXPIRED], ["expires_on", "is", "not set"]],
			fields=["name", "course", "user"],
		)
		for row in rows:
			frappe.db.set_value(doctype, row["name"], "status", VALID, update_modified=False)
		if rows:
			print(
				"unexpire_never_expiring_training_records: %s -- restored %d (%s)"
				% (doctype, len(rows), ", ".join(sorted(r["name"] for r in rows)))
			)
		return rows
	except Exception:
		frappe.log_error(
			title="unexpire_never_expiring_training_records",
			message="Could not restore %s rows" % doctype,
		)
		return []


def _withdraw_spurious_assignments(completions):
	"""Cancel the retakes raised against completions that had not lapsed.

	Only ``Not Started`` rows, and only for a (course, user) pair whose completion was
	just restored. See the module docstring for why the source field alone is not a
	safe discriminator.
	"""
	try:
		if not completions or not frappe.db.exists("DocType", ASSIGNMENT):
			return
		cancelled, left = [], []
		for row in completions:
			if not row.get("course") or not row.get("user"):
				continue
			for assignment in frappe.get_all(
				ASSIGNMENT,
				filters={
					"course": row["course"],
					"user": row["user"],
					"assignment_source": "Recertification",
					"status": ["in", ("Not Started", "In Progress", "Awaiting Sign-off", "Overdue")],
				},
				fields=["name", "status"],
			):
				if assignment["status"] != "Not Started":
					# Somebody has started it. That is their work, and a patch is the
					# wrong place to decide what happens to it.
					left.append("%s (%s)" % (assignment["name"], assignment["status"]))
					continue
				frappe.db.set_value(
					ASSIGNMENT, assignment["name"], "status", CANCELLED, update_modified=False
				)
				cancelled.append(assignment["name"])

		if cancelled:
			print(
				"unexpire_never_expiring_training_records: cancelled %d spurious retake(s) -- %s"
				% (len(cancelled), ", ".join(sorted(cancelled)))
			)
		if left:
			print(
				"unexpire_never_expiring_training_records: LEFT ALONE, already started -- %s"
				% ", ".join(sorted(left))
			)
	except Exception:
		frappe.log_error(
			title="unexpire_never_expiring_training_records",
			message="Could not withdraw spurious recertification assignments",
		)
