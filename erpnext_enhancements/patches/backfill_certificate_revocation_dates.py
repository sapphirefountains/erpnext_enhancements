# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Recover a revocation date where it can be *proved*, and say so where it cannot.

Until v1.396.0 `certificates._revoke_certificates_for` wrote
``{"status": "Revoked", "expires_on": today()}``. There was no revocation date
field, so the expiry was doing double duty — and the write destroyed the validity
window printed on the document the holder is carrying. An as-of-date question
answered from ``issued_on <= D <= expires_on`` then returned a plausible, shorter,
wrong window that disagreed with the paper, with nothing recording that it moved.

Why this does NOT key on ``status = "Revoked"``
-----------------------------------------------
Because there are **two** writers of that status and only one of them clobbered the
expiry. Keying on the status would stamp a certificate's original — possibly
years-future — expiry date onto a field labelled "Revoked On", inventing a
revocation date that never existed. That is worse than leaving it empty.

The clobber has a signature, and it is the rule the writer actually applied:
`_revoke_certificates_for` runs *only* from a completion being cancelled. So this
keys on the linked ``Training Completion`` being ``docstatus = 2``, and confirms per
row that ``expires_on`` is not the value ``_default_dates`` would have derived from
``issued_on`` + the course's ``certificate_valid_months``. Both must hold.

Everything else gets a ``history_note`` and a NULL ``revoked_on``. An empty date on a
revoked row means *not reconstructible*, and the roster renders it that way rather
than substituting today's — which is the whole difference between an audit artefact
and a misleading one.

Never aborts a migrate. Safe twice: it only ever fills a blank.
"""

import frappe
from frappe.utils import add_months, getdate

CERTIFICATE = "Training Certificate"

UNKNOWN = (
	"Revoked before v1.396.0, when no revocation date was recorded. The date is not "
	"reconstructible from stored data, so it is deliberately left empty rather than "
	"filled with a guess."
)
RECOVERED = (
	"Revocation date recovered from the cancelled completion. `expires_on` had been "
	"overwritten with the revocation date by the pre-v1.396.0 revoke path, so the "
	"validity window originally printed on this certificate is not recoverable."
)


def execute():
	if not frappe.db.exists("DocType", CERTIFICATE):
		return
	meta = frappe.get_meta(CERTIFICATE)
	if not meta.has_field("revoked_on"):
		return

	rows = frappe.get_all(
		CERTIFICATE,
		filters={"status": "Revoked"},
		fields=["name", "completion", "issued_on", "expires_on", "course"],
	)
	recovered = unknown = 0

	for row in rows:
		if frappe.db.get_value(CERTIFICATE, row.name, "revoked_on"):
			continue

		if _was_clobbered(row):
			frappe.db.set_value(
				CERTIFICATE,
				row.name,
				{"revoked_on": row.expires_on, "history_note": RECOVERED},
				update_modified=False,
			)
			recovered += 1
			continue

		frappe.db.set_value(CERTIFICATE, row.name, "history_note", UNKNOWN, update_modified=False)
		unknown += 1

	print(
		f"[erpnext_enhancements] certificate revocation dates: {recovered} recovered, "
		f"{unknown} marked not reconstructible, of {len(rows)} revoked"
	)


def _was_clobbered(row):
	"""True only when the completion-driven revoke path is what moved ``expires_on``.

	Two independent conditions, because either alone is circumstantial:

	1. the linked completion is cancelled — that path is the only caller of
	   ``_revoke_certificates_for``;
	2. ``expires_on`` is not what ``_default_dates`` would have derived, i.e. it has
	   actually been moved off the issue-plus-validity-months value.

	When the course carries no ``certificate_valid_months`` there is nothing to
	compare against, so (2) cannot be established and the row is left unknown.
	"""
	if not row.completion or not row.expires_on or not row.issued_on:
		return False
	if frappe.db.get_value("Training Completion", row.completion, "docstatus") != 2:
		return False

	months = frappe.db.get_value("Training Course", row.course, "certificate_valid_months")
	if not months:
		return False

	expected = add_months(getdate(row.issued_on), int(months))
	return getdate(row.expires_on) != getdate(expected)
