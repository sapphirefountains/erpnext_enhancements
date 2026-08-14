# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Chat Export Request`` — the record of an export, and the thing the job works from.

**Not submittable, and no chat DocType is.** Immutability here is `before_save` plus
`is_new()`, the same shape `Chat Audit Log` uses and for the same reason: `docstatus` gives
you a workflow you did not ask for, and a governance record that can be *cancelled* is a
governance record with an undo button.

**What is frozen and why.** Once a request exists, its scope, its reason and its
`include_deleted_content` flag cannot change. That is not tidiness — `manifest.json` names
the rooms and states whether deleted bodies were included, so a request whose scope could be
edited afterwards would produce a manifest that describes a bundle nobody built. The audit
row written when it was requested says the same things, and the two must not be able to
disagree.

`status`, and only `status` and the result fields, move afterwards. The job writes them with
`frappe.db.set_value`, which skips `validate()` entirely — so the transition check below is
the backstop for a desk or console edit, not the enforcement point. That is stated because
the relay job's controller carries the identical caveat and somebody reading one will read
the other.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document

STATUS_PENDING = "Pending"
STATUS_IN_PROGRESS = "In Progress"
STATUS_COMPLETE = "Complete"
STATUS_FAILED = "Failed"

#: Terminal states. A bundle that exists is not rebuilt, and a failure is re-requested
#: rather than retried in place — a retry that overwrote the row would lose the record that
#: the first attempt happened, which is the only thing that makes "why is there no export?"
#: answerable.
TERMINAL_STATUSES = frozenset({STATUS_COMPLETE, STATUS_FAILED})

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
	STATUS_PENDING: frozenset({STATUS_IN_PROGRESS, STATUS_FAILED}),
	STATUS_IN_PROGRESS: frozenset({STATUS_COMPLETE, STATUS_FAILED}),
	STATUS_COMPLETE: frozenset(),
	STATUS_FAILED: frozenset(),
}

#: Fields that describe *what was asked for*. Frozen after insert, because the manifest and
#: the audit row both restate them and three copies that can drift apart is worse than one
#: copy that cannot be corrected.
_FROZEN_FIELDS = (
	"rooms",
	"subject_user",
	"reason",
	"reason_category",
	"include_deleted_content",
	"requested_by",
	"requested_at",
)


class ChatExportRequest(Document):
	def validate(self) -> None:
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before is None:
			# A reload path with no prior copy. Nothing to compare against, and refusing here
			# would break `bench migrate`'s own resaves.
			return
		self._refuse_scope_edits(before)
		self._refuse_illegal_transition(before)

	def _refuse_scope_edits(self, before: Document) -> None:
		changed = [f for f in _FROZEN_FIELDS if (self.get(f) or "") != (before.get(f) or "")]
		if changed:
			frappe.throw(
				frappe._(
					"An export request's scope and reason are fixed once it is made ({0}). "
					"The manifest and the audit row both restate them, so editing one here "
					"would leave three records of the same request disagreeing. Make a new "
					"request instead."
				).format(", ".join(changed)),
				frappe.ValidationError,
			)

	def _refuse_illegal_transition(self, before: Document) -> None:
		old = (before.get("status") or STATUS_PENDING).strip()
		new = (self.get("status") or STATUS_PENDING).strip()
		if old == new:
			return
		if new not in LEGAL_TRANSITIONS.get(old, frozenset()):
			frappe.throw(
				frappe._("An export request cannot move from {0} to {1}.").format(old, new),
				frappe.ValidationError,
			)

	def on_trash(self) -> None:
		"""A completed export is a record that something left the building.

		Deleting it does not recall the ZIP somebody already downloaded; it only removes the
		evidence that they did. The `export_requested` and `export_downloaded` rows in
		`Chat Audit Log` survive independently and are chained, so this refusal is belt and
		braces rather than the whole protection — but it is the one an operator meets first.
		"""
		if (self.get("status") or "") == STATUS_COMPLETE:
			frappe.throw(
				frappe._(
					"A completed export request cannot be deleted. It records that a bundle "
					"left the building, and deleting it removes the evidence rather than the "
					"bundle."
				),
				frappe.PermissionError,
			)


def as_dict_for_job(name: str) -> dict[str, Any]:
	"""The request as plain JSON-safe values, for a worker that must not depend on a class.

	`frappe.enqueue` pickles its kwargs, so a dataclass or a Document handed to a job is a
	class-definition dependency between the process that queued it and the one that runs it —
	which a deploy breaks. The job is given a name and reads the row itself; this is the
	shared reader.
	"""
	row = frappe.db.get_value(
		"Chat Export Request",
		name,
		[
			"name",
			"status",
			"requested_by",
			"rooms",
			"subject_user",
			"reason",
			"reason_category",
			"include_deleted_content",
		],
		as_dict=True,
	)
	return dict(row or {})
