# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Enhancement Request`` — what an employee filed, and what the reviewer decided about it.

**Not submittable.** Immutability here is ``validate()`` plus ``is_new()``, the same shape
``Chat Export Request`` uses and for the same reason: ``docstatus`` gives you a workflow you
did not ask for, and a record that can be *cancelled* is a record with an undo button. The
lifecycle is the ``status`` Select, and the transition table lives in
:mod:`erpnext_enhancements.product_feedback.states`, which is stdlib-only so it can be
tested without a bench. ADR 0010 records why this is not a Frappe Workflow.

**What is frozen and why.** ``requested_by`` and the five captured-context fields cannot
change after insert. They are the answer to "who saw this, on what page, running what
version" — the questions a reviewer asks first and the ones nobody can reconstruct later.
A field that can be edited afterwards is not evidence.

**The proposal is not the work.** ``proposed_tasks`` holds rows a model wrote. Nothing in
this doctype creates a ``Task``; only ``api.feedback.create_tasks`` does, after a human has
edited and confirmed the rows. That split is lifted from ``api/training_ai.py`` — the
drafting call persists nothing, and the accept call is what stamps a named person against
the result.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document

from erpnext_enhancements.product_feedback.states import (
	IllegalTransition,
	RequestState,
	assert_transition,
)

#: Who filed it, and what their browser said at the time. Frozen after insert — see the
#: module docstring.
_FROZEN_FIELDS = (
	"requested_by",
	"requested_at",
	"context_url",
	"context_doctype",
	"context_docname",
	"context_user_agent",
	"context_app_version",
)


class EnhancementRequest(Document):
	def validate(self) -> None:
		if self.is_new():
			self._default_requester()
			return
		before = self.get_doc_before_save()
		if before is None:
			# A reload path with no prior copy. Nothing to compare against, and refusing here
			# would break `bench migrate`'s own resaves.
			return
		self._refuse_provenance_edits(before)
		self._refuse_illegal_transition(before)

	def _default_requester(self) -> None:
		"""Stamp the session user if the caller did not.

		``api.feedback.submit_request`` always sets this explicitly and drops any
		client-supplied value; this covers a desk-created row so the field is never empty.
		"""
		if not self.get("requested_by"):
			self.requested_by = frappe.session.user
		if not self.get("requested_at"):
			self.requested_at = frappe.utils.now_datetime()

	def _refuse_provenance_edits(self, before: Document) -> None:
		changed = [f for f in _FROZEN_FIELDS if (self.get(f) or "") != (before.get(f) or "")]
		if changed:
			frappe.throw(
				frappe._(
					"Who filed a request, and the context captured when they did, are fixed "
					"once it exists ({0}). They are the record of what was actually seen; a "
					"field that can be edited afterwards is not evidence."
				).format(", ".join(changed)),
				frappe.ValidationError,
			)

	def _refuse_illegal_transition(self, before: Document) -> None:
		old = (before.get("status") or RequestState.SUBMITTED).strip()
		new = (self.get("status") or RequestState.SUBMITTED).strip()
		try:
			assert_transition(old, new)
		except IllegalTransition as exc:
			frappe.throw(frappe._(str(exc)), frappe.ValidationError)


def as_dict_for_job(name: str) -> dict[str, Any]:
	"""The request as plain JSON-safe values, for a worker that must not depend on a class.

	``frappe.enqueue`` pickles its kwargs, so a Document handed to a job is a
	class-definition dependency between the process that queued it and the one that runs
	it — which a deploy breaks. The job is given a name and reads the row itself; this is
	the shared reader.
	"""
	row = frappe.db.get_value(
		"Enhancement Request",
		name,
		[
			"name",
			"status",
			"title",
			"request_type",
			"impact",
			"description",
			"steps_to_reproduce",
			"requested_by",
			"context_url",
			"context_doctype",
			"context_docname",
			"context_app_version",
			"target_erpnext",
			"target_triton",
		],
		as_dict=True,
	)
	return dict(row or {})
