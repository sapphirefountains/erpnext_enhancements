# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who can actually use ``@triton``, and who cannot yet. A ``bench execute`` report.

    bench --site <site> execute erpnext_enhancements.chat.rollout.erpnext_link_report

**This is a rollout task wearing a technical costume, and it will present as a Phase 5 bug.**
Every person Triton acts for must have completed the ERPNext OAuth link first, or the turn
dies before the first token: the client raises on a user-scoped construction with no grant,
and on the streaming path that surfaces as a generic failure rather than a "link ERPNext"
prompt. The handler now catches it and answers with an actionable sentence in-thread — but
the *fix* is somebody sitting with people while they click a button, and at ~50 people that
is an afternoon which only happens if it is scheduled.

So this exists to make the afternoon schedulable: a list of names, a count, and a plain
statement of what each state means.

--------------------------------------------------------------------------------------
The predicate is imported, never re-implemented
--------------------------------------------------------------------------------------

:func:`chat.invoke.handler.has_erpnext_link` is the function the handler refuses on, and it
is the function this report calls. A readiness report that answered the question differently
from the code enforcing it would be worse than no report — it would be confidently wrong at
the exact moment somebody trusted it and told twenty people they were ready.

--------------------------------------------------------------------------------------
What this can and cannot see
--------------------------------------------------------------------------------------

ERPNext is the OAuth **provider**: Triton holds a bearer that ERPNext issued, so the grant is
a row in this database and "has this person linked" is answerable here without asking Triton.

What it cannot see is whether Triton's own copy of that grant is intact — a Triton-side
database restore, or a user deleted and recreated there, would leave this report saying yes
while the turn still fails. That residual is printed rather than implied, because a report
that overstates its confidence is how a rollout gets signed off twice.

It also does not send anything. Listing who needs an email is useful; sending it from a
``bench execute`` is a decision somebody should make with their own hands.

Never raises, and deliberately **not** whitelisted — same posture as ``chat/health.py``. It
reads a roster and an OAuth table; both are ordinary, and neither is worth a new HTTP surface.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

#: How the roster is decided, in priority order, and what each means for the reader.
SOURCE_WHITELIST: str = "pilot whitelist"
SOURCE_MEMBERS: str = "active chat room members"


def erpnext_link_report(as_dict: bool = False) -> Any:
	"""Print (or return) who is ready for ``@triton`` and who is not.

	Args:
		as_dict: return the structure instead of printing it. The default prints, because the
			caller is a person at a terminal deciding who to chase.
	"""
	roster, source = _roster()
	linked: list[str] = []
	unlinked: list[str] = []

	from erpnext_enhancements.chat.invoke.handler import has_erpnext_link

	for user in roster:
		(linked if has_erpnext_link(user) else unlinked).append(user)

	report = {
		"roster_source": source,
		"roster_size": len(roster),
		"linked": sorted(linked),
		"unlinked": sorted(unlinked),
		"ready": not unlinked and bool(roster),
		"triton_enabled": _flag("triton_enabled"),
		"chat_enabled": _flag("enabled"),
		"retrieval_paused": _flag("pause_retrieval"),
		"triton_paused": _flag("pause_triton"),
	}

	if as_dict:
		return report
	_print(report)
	return None


def unlinked_users() -> list[str]:
	"""Just the names, for a caller that wants to do something with them."""
	report = erpnext_link_report(as_dict=True)
	return list(report["unlinked"])


def _roster() -> tuple[list[str], str]:
	"""Who Triton may be asked to answer for.

	**The pilot whitelist when it is on**, because that is the set that can reach chat at all
	and chasing anybody else is wasted effort. Otherwise everybody who is an active member of
	any room — not "every enabled user", which would list the whole company including people
	who have never opened chat and never will.

	The distinction matters for the number this report puts in front of somebody: "12 people
	need to click a button" is a task, and "180 people need to click a button" is a reason to
	give up on the task.
	"""
	if cint(_setting("restrict_to_whitelist")):
		users = _whitelisted_users()
		if users:
			return users, SOURCE_WHITELIST
	return _room_members(), SOURCE_MEMBERS


def _whitelisted_users() -> list[str]:
	try:
		settings = frappe.get_cached_doc("Chat Settings")
	except Exception:
		return []
	return sorted(
		{
			(row.user or "").strip()
			for row in (settings.get("allowed_users") or [])
			if (row.user or "").strip()
		}
	)


def _room_members() -> list[str]:
	"""Distinct active members across every room.

	``frappe.get_all`` deliberately, and it is scoped by nothing — which is correct here and
	worth saying: this runs under ``bench execute`` with no session user, it answers an
	operational question about the whole roster, and scoping it to the invoking identity would
	make the answer depend on who happened to type the command.

	It reads one column, ``user``. No room name, no message, nothing about who talks to whom.
	"""
	try:
		rows = frappe.get_all(
			"Chat Room Member",
			filters={"is_active": 1},
			pluck="user",
			limit_page_length=0,
		)
	except Exception:
		return []
	return sorted({(user or "").strip() for user in rows if (user or "").strip()})


def _flag(fieldname: str) -> bool:
	return bool(cint(_setting(fieldname)))


def _setting(fieldname: str) -> Any:
	try:
		return frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return None


def _print(report: dict[str, Any]) -> None:
	"""Judged output, in the shape ``chat/health.py`` established: every number named, and a
	sentence saying what to do about it. A report that prints a list and leaves the reader to
	work out the consequence is a report that gets skimmed."""
	line = "=" * 78
	print(f"\n{line}\n@triton readiness — the ERPNext OAuth link\n{line}")
	print(f"Roster ({report['roster_source']}): {report['roster_size']} people")
	print(f"  linked and ready : {len(report['linked'])}")
	print(f"  NOT linked yet   : {len(report['unlinked'])}")

	if report["unlinked"]:
		print("\nThese people cannot use @triton yet. A mention from any of them is refused")
		print("in-thread with an instruction rather than answered:\n")
		for user in report["unlinked"]:
			print(f"  - {user}")
		print(
			"\nWhat they do: open the Triton assistant in ERPNext once and click 'Link ERPNext'."
			"\nIt is one click each, and it cannot be done for them — an OAuth grant is theirs"
			"\nto give. That is the whole reason this is an afternoon of somebody's time rather"
			"\nthan a migration."
		)
	elif report["roster_size"]:
		print("\nEveryone on the roster has linked. Nothing to chase.")
	else:
		print("\nThe roster is empty — nobody is whitelisted and no room has an active member.")

	switches = [
		("Enable Chat", report["chat_enabled"], True),
		("Triton Enabled", report["triton_enabled"], True),
		("Pause Triton", report["triton_paused"], False),
		("Pause Retrieval", report["retrieval_paused"], False),
	]
	blocking = [name for name, value, wanted in switches if value != wanted]
	if blocking:
		print(
			f"\nNote: {', '.join(blocking)} would stop @triton answering regardless of who has"
			"\nlinked. That is expected while the feature ships dormant — it is listed so a"
			"\nquiet rollout is not mistaken for a broken one."
		)

	print(
		"\nWhat this cannot see: whether Triton's own copy of the grant is intact. ERPNext is"
		"\nthe OAuth provider, so a grant existing here is authoritative about this side only."
		"\nA Triton-side restore, or a user recreated there, would leave this report saying yes"
		"\nwhile the turn still fails."
	)
	print(f"{line}\n")
