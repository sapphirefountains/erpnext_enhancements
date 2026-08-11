# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Room Digest — one rolling summary per room, and the three-value watermark.

The docname **is** the room, so "two workers generated a digest for the same room at once"
is a failed insert rather than two summaries that disagree. There is no second uniqueness
rule to keep in sync and no ``unique(room)`` index to add by patch.

--------------------------------------------------------------------------------------
Why the watermark is three values, and why one of them is not enough
--------------------------------------------------------------------------------------

``(watermark_seq, watermark_count, watermark_modified)``, and each catches something the
others cannot:

* ``watermark_seq`` — new messages. Necessary, and **not sufficient**: neither an edit nor a
  delete advances ``seq``.
* ``watermark_modified`` — an **edit**. ``max(modified)`` over the covered span moves when
  somebody rewrites an old message.
* ``watermark_count`` — a **hard delete**, which moves neither of the other two.

A digest keyed on ``seq`` alone is unchanged by a delete, so its cache key is unchanged, so
the summary containing the message the user just deleted is served again. That is a privacy
failure that presents as a caching bug, and it is the single most likely bug in this design.

**Every freshness comparison uses ``frappe.utils.now_datetime()`` on both sides, or converts
explicitly.** The production database runs UTC while Frappe writes ``creation``/``modified``
in site-local time; a naive SQL ``TIMESTAMPDIFF(MINUTE, MAX(creation), NOW())`` reports a row
written one minute ago as **361 minutes** old, and every predicate built on it is wrong in the
same direction.

--------------------------------------------------------------------------------------
Staleness, and why retrieval skips rather than caveats
--------------------------------------------------------------------------------------

An edit or delete inside ``[covered_from, covered_to]`` sets ``is_stale``. Retrieval then
**omits** the digest — it is not served with a "this may be out of date" note, because the
thing that may be out of date is a sentence somebody deleted, and ERPNext holds the only copy
of it. The rebuild is a **full regeneration from the source messages**, never an incremental
append: a rolling summary can add information but it cannot unsay it.

``poisoned`` is deliberately a *separate* flag from ``is_stale``. "Nobody has rebuilt this
yet" and "this cannot be rebuilt" need different answers from an operator, and collapsing
them into one field means a permanently failing room looks identical to a busy one.

Indentation is tabs.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class ChatRoomDigest(Document):
	def validate(self) -> None:
		validate_digest_span(self)

	def before_insert(self) -> None:
		if not self.digest_version:
			self.digest_version = 1


def validate_digest_span(doc: Document) -> None:
	"""Shared by both digest DocTypes, because both have the same span invariant.

	A reversed span is a **throw** rather than a normalisation, unlike the staleness seam
	which widens and warns. The difference is what the caller is doing: the seam runs on the
	delete path, where raising would abort the delete and leave the message live *and* the
	digest stale. Here the caller is a generator that can simply be fixed, and a silently
	corrected span is a generator nobody fixes.
	"""
	covered_from = int(doc.covered_from or 0)
	covered_to = int(doc.covered_to or 0)
	if covered_to and covered_to < covered_from:
		frappe.throw(
			frappe._("{0} span is reversed: covered_from {1} > covered_to {2}.").format(
				doc.doctype, covered_from, covered_to
			)
		)
	if int(doc.watermark_seq or 0) < covered_to:
		frappe.throw(
			frappe._(
				"{0} watermark_seq ({1}) is behind covered_to ({2}). The watermark is what "
				"staleness is decided against; a watermark behind the span it describes means "
				"an edit inside the span can never be detected."
			).format(doc.doctype, doc.watermark_seq, covered_to)
		)
