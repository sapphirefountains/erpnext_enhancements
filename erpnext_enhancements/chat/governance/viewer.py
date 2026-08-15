# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The oversight viewer's HTTP surface. Phase 6 §4.B.

**This is the surface that collects the reason, which is why refusal lands here.** v1.288.6
shipped :func:`chat.governance.access_report.reason_quality` and deliberately did *not*
enforce it: refusing a read for a bad reason belongs with the thing that can ask for a good
one, and enforcing it a release earlier would have failed ``Administrator``'s existing
privileged reads — live, with no field to type a reason into. A governance control whose
first act is to break the admin is one that gets switched off. Here there is a field, so
here it is enforced: **G6-10, refused rather than accepted-and-blanked.**

**A separate surface, never a refactor of the SPA.** The employee chat app and this share no
code path, no template and no socket. That is not tidiness — `chat/permissions.py` records
that the gate has exactly two doors and the employee app is not one of them, and the fastest
way to make it a third would be to teach its components an "oversight mode".

**No realtime, by construction (G6-6).** Opening a room the auditor is not in must not
establish a subscription to that room's doc room, because a socket join is a read this
module cannot audit — the row is written when the transcript is fetched, and a live event
arriving afterwards was never asked for. So this surface has no socket at all: it renders
server-side, it polls nothing, and `tests/test_chat_oversight_viewer.py` asserts the module
contains no `publish_realtime` and no subscribe call. A viewer that quietly went live would
be indistinguishable from one that did not until somebody read the network tab.

**POST-only, including the reads.** The endpoints here are classified non-mutating — the
audit row they write is a record *of* the read rather than an effect the caller wants, the
same reasoning `chat/endpoints.py` applies to `search_messages`. They are still POST,
because every one of them carries a **reason** in its body, and a reason in a query string
ends up in the web server's access log, the browser's history and the `Referer` header of
whatever the auditor clicks next. The reason is the most sensitive field in the request.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

from erpnext_enhancements.chat import audit, permissions
from erpnext_enhancements.chat.governance import access_report
from erpnext_enhancements.chat.retrieval import gate

#: A cap on how many rooms one oversight read may name. Not a permission — the permission is
#: the role — but a read naming two hundred rooms is a fishing expedition wearing a reason,
#: and the audit row would record it as a single justified act.
MAX_ROOMS_PER_READ = 25


class OversightRefused(frappe.ValidationError):
	"""Refused before anything was read. Carries a message written for the auditor."""


def _require_auditor() -> str:
	"""The role gate. Returns the acting user.

	Reads the configured role from ``Chat Settings`` rather than naming one, so the hatch has
	exactly one definition. The field ships blank, which means nobody — this whole surface is
	unreachable until somebody deliberately fills it in.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		raise OversightRefused(frappe._("The oversight viewer requires a signed-in user."))
	if not permissions._has_oversight(user):
		raise OversightRefused(
			frappe._(
				"The oversight viewer requires the role named in Chat Settings → Admin "
				"Oversight Role. That field ships blank, so this surface fails closed until "
				"somebody configures it."
			)
		)
	return user


def _require_reason(reason: str | None, category: str | None) -> tuple[str, str]:
	"""G6-10. A read with a bad reason is **refused**, not accepted and blanked.

	Returns the cleaned pair. The grades come from
	:func:`~chat.governance.access_report.reason_quality`, so the number that refuses here and
	the number the compliance report grades by are the same number — two thresholds would mean
	rows that passed the door and fail the report, which reads as tampering rather than as
	disagreement.

	The category is required **here** and optional on the audit row, and that asymmetry is
	deliberate: rows written by other paths genuinely have no category, and forcing one would
	invent it. This surface has a person and a dropdown, so it can insist.
	"""
	cleaned = (reason or "").strip()
	grade = access_report.reason_quality(cleaned)
	if grade != access_report.REASON_OK:
		raise OversightRefused(
			frappe._(
				"This read needs a stated reason of at least {0} characters that says "
				"something ({1}). The reason is what makes the read defensible later, and a "
				"blank or placeholder one is refused rather than recorded as empty."
			).format(access_report.REASON_MIN_LENGTH, grade)
		)

	normalised = audit.normalise_reason_category(category)
	if not normalised:
		raise OversightRefused(
			frappe._(
				"Choose a reason category. It is the part the person whose messages these "
				"are will be shown, so it cannot be left for somebody to infer later."
			)
		)
	return cleaned, normalised


def _rooms(raw: Any) -> list[str]:
	"""Accept a JSON array or a comma-separated string; refuse an unbounded read."""
	if isinstance(raw, str):
		raw = raw.strip()
		try:
			parsed = json.loads(raw) if raw.startswith("[") else [r for r in raw.split(",")]
		except ValueError:
			parsed = [r for r in raw.split(",")]
	else:
		parsed = raw or []

	names = [str(r).strip() for r in parsed if str(r or "").strip()]
	if not names:
		raise OversightRefused(
			frappe._("Name the rooms to read. An oversight read of 'everything' is not expressible here.")
		)
	if len(names) > MAX_ROOMS_PER_READ:
		raise OversightRefused(frappe._("One read may name at most {0} rooms.").format(MAX_ROOMS_PER_READ))
	return names


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=3600, methods=["POST"])
def search(
	query: str = "",
	rooms: Any = None,
	reason: str = "",
	reason_category: str = "",
	subject: str | None = None,
) -> dict[str, Any]:
	"""Cross-room oversight search. Refuses without a role, a reason and a category.

	Delegates the read itself to :func:`chat.retrieval.gate.retrieve_for_oversight`, which is
	the one function permitted to open the membership hatch — and which, until v1.288.7, had
	no caller at all. This is that caller.
	"""
	user = _require_auditor()
	cleaned_reason, category = _require_reason(reason, reason_category)
	named = _rooms(rooms)

	result = gate.retrieve_for_oversight(
		query=query or "",
		rooms=named,
		reason=cleaned_reason,
		subject=(subject or "").strip() or None,
		user=user,
	)
	# `reason_category` rides on the audit row the gate already wrote, rather than on a second
	# row of our own: one read, one record. Written after the fact because the gate owns the
	# insert, and `db_set` on the audit table is allowed for exactly this one field — it is
	# outside the chained tuple's mandatory half and re-signing happens on the next verify.
	_stamp_category(result, category)

	# Read the dataclass, do not `getattr(..., default)` it. `RetrievalResult` has no `text`
	# field and never had one — the transcript lives on `assembly`, exactly as every other
	# consumer reads it (`invoke/handler.py`, `retrieval/api.py`). Until v1.299.1 this line was
	# `getattr(result, "text", "") or ""`, which is not a missing feature but a **silent** one:
	# the default fires on every call, so the auditor got an empty transcript while the gate
	# wrote a full audit row saying they had read it. A defaulted `getattr` on a typed dataclass
	# converts a typo into a plausible answer, which is why the fields below are read directly
	# and `tests/test_chat_oversight_viewer.py` now asserts every name here exists on the class.
	return {
		"rooms": named,
		"subject": (subject or "").strip() or None,
		"reason_category": category,
		"text": result.assembly.text() if result.assembly else "",
		"citations": [c.as_dict() for c in result.manifest],
		"audit_row": result.audit_row,
	}


def _stamp_category(result: Any, category: str) -> None:
	"""Put the category on the row the gate just wrote. Best effort, never fatal.

	A failure here must not undo a read that already happened and was already recorded — the
	row exists and names the reason in full, so a missing category degrades the *subject's*
	view rather than the compliance one. Logged rather than raised for that reason.
	"""
	name = getattr(result, "audit_row", None)
	if not name:
		return
	try:
		frappe.db.set_value(audit.AUDIT_DOCTYPE, name, "reason_category", category, update_modified=False)
	except Exception:
		try:
			frappe.log_error(
				title="chat oversight: reason_category not stamped",
				message=f"audit_row={name}",
			)
		except Exception:
			pass


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=240, seconds=3600, methods=["POST"])
def access_log(
	room: str | None = None,
	actor: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	limit: int = 100,
) -> dict[str, Any]:
	"""Who read what — the unified report, for the auditor.

	**Reading the trail is not itself recorded as a privileged read**, and that is the same
	call `chat/permissions.py` makes for the audit tables' query hook: it reads no chat
	content, and recording it would put the auditor's own review into the log they are
	reviewing — a trail that grows every time somebody looks at it.
	"""
	_require_auditor()
	rows = access_report.access_rows(
		room=(room or "").strip() or None,
		actor=(actor or "").strip() or None,
		from_date=(from_date or "").strip() or None,
		to_date=(to_date or "").strip() or None,
		limit=cint(limit) or 100,
	)
	return {
		"rows": rows,
		"compliance": access_report.compliance_summary(rows),
		"categories": access_report.category_summary(rows),
	}


@frappe.whitelist(methods=["POST"])
def my_access_log(limit: int = 100) -> list[dict[str, Any]]:
	"""The employee-facing half: who read **my** messages (decision D-1).

	No role gate — this answers only for the caller, and every row is redacted by
	:func:`~chat.governance.access_report.redact_for_subject`, which the caller cannot switch
	off. A transparency view that can be asked for the un-redacted reason is a compliance view
	with a friendlier name.

	**Ships built and disabled**, per D-1: the setting is a rollout step rather than part of
	this commit, so the endpoint checks it and refuses rather than assuming.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		raise OversightRefused(frappe._("Sign in to see who has read your messages."))
	if not _transparency_enabled():
		raise OversightRefused(
			frappe._(
				"The 'who read my messages' view is not switched on. It is built and ships "
				"disabled; enabling it is a deliberate rollout step."
			)
		)
	return access_report.subject_rows(subject=user, limit=cint(limit) or 100)


def _transparency_enabled() -> bool:
	"""Never raises. A settings read that throws must not take the page with it."""
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "transparency_view_enabled")))
	except Exception:
		return False
