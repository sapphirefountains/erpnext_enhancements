# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google space provisioning — the three modes of ADR §4.H, and the gate that refuses one.

A ``Chat Room`` is an ERPNext row. A Google *space* is a thing that appears in twenty
coworkers' native Chat clients, costs one of **60 project-wide space writes per minute**,
and cannot be un-created without ``spaces.delete`` — a call this codebase never makes
without an explicit human confirmation step because it cascades and takes the conversation
history with it. So the two are deliberately not the same act, and everything here is
shaped by the asymmetry: a room is cheap and reversible, a space is neither.

The three modes
---------------
**Mode 1 — lazy.** A room with ``provisioning_mode`` other than ``Not Mirrored`` gets its
space on first use, not at creation. :func:`provision_room` is the whole of it and it is
**idempotent**: a bound room short-circuits to a metadata refresh, and an unbound one
replays the same deterministic ``requestId`` so a retried job gets the same space back
rather than a second one.

*The write path may not import this module.* ``tests/test_chat_guardrails.py`` asserts —
transitively, including function-local imports — that no ``doc_events`` handler can reach
``gchat/client.py``, and this module imports it at module scope. The write path therefore
enqueues by **dotted string**::

    frappe.enqueue(
        "erpnext_enhancements.chat.sync.provisioning.provision_room_job",
        room=self.room,
        queue="long",
        enqueue_after_commit=True,
        job_id=f"chat-provision-{self.room}",
        deduplicate=True,
    )

That string is part of this module's contract. Renaming :func:`provision_room_job` breaks a
caller no import graph will show you.

**Mode 2 — org mirroring.** A throttled, resumable **batch**, never a loop.
:func:`enroll_org_units` is the explicit per-entity opt-in and does **zero** Google I/O:
it creates ``Chat Room`` rows for named units and nothing else, so it is safe to run at any
scale and there is no path by which installing the app provisions anything.
:func:`run_provisioning_run` then walks *enrolled* rooms in ``linked_document`` order,
charging every space write to the project bucket and **pausing** — not failing — when the
window is spent. ``Chat Provisioning Run.cursor`` is the checkpoint, and ``dry_run``
defaults on.

**Mode 3 — per-document spaces.** :func:`create_document_room` is user-initiated,
permission-checked, gated on ``Chat Settings.document_spaces_enabled``, and registered in no
hook. A per-document space is never created as a side effect of creating a document; at ~60
space writes a minute, a rule that made one per Project would saturate the project's entire
space budget for hours and leave thousands of empty spaces in every coworker's client.

Why the orphan check is a sweep and not a ``doc_events`` hook
-------------------------------------------------------------
Deleting or cancelling a linked document must not silently orphan its space, so
:func:`sweep_orphaned_document_rooms` archives the room, leaves the space and alerts. It is
a **scheduled sweep** rather than an ``on_trash``/``on_cancel`` hook for two reasons, and
the second is the load-bearing one:

1. The guardrail above forbids a ``doc_events`` target that reaches the transport, so a hook
   would have to live in a third module that only enqueues — one more file, one more hop,
   for a latency nobody is waiting on.
2. **A hook does not see every deletion.** ``frappe.db.delete``, a bulk delete and a direct
   SQL cleanup all leave the document gone and fire nothing. The sweep asks the only
   question that is actually true — *does the linked document still exist* — so it cannot be
   routed around.

The external-user gate (CQ-12), and why it refuses loudly
----------------------------------------------------------
``Space.externalUserAllowed`` is readable, immutable, and means this space admits members
from outside the Workspace domain. Mirroring such a space sends company conversation to
somebody the org chart does not cover, so it is refused unless a human has ticked
``Chat Room.mirror_whitelisted``. The refusal happens at provisioning time, raises
:class:`ProvisioningRefused`, writes the reason onto the room and alerts — it does **not**
quietly skip. A data-egress control that fails silently is a data-egress control nobody
knows has failed.

Dry run cannot bind a space name
--------------------------------
In dry-run the client answers from ``gchat/dryrun.py`` with a synthetic, deterministic
``spaces/DRYRUN-…`` name. Writing that into ``gchat_space_name`` would burn the unique index
on a space that does not exist and send every later reconciliation hunting for it, so
:func:`_bind_space` refuses any name :func:`dryrun.is_dryrun_name` recognises and records
the plan instead. This is the one place where "the dry run did something durable" would be
worse than the dry run being useless.

Purity discipline
-----------------
The decisions — space type, the resume slice, the external gate, the threading read-back —
are module-level pure functions taking data and returning data, and they are what
``tests/test_chat_provisioning.py`` covers hardest. ``frappe`` is imported at module scope
because this module is never a ``doc_events`` target; the Google calls all go through the
one transport in ``gchat/client.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import frappe

from erpnext_enhancements.chat.gchat import client as chat_client
from erpnext_enhancements.chat.gchat import dryrun, ids
from erpnext_enhancements.chat.sync.ratelimit import BUCKET_SPACE_WRITES, ProjectQuota

# --------------------------------------------------------------------------------------
# Vocabulary — the strings three modules have to agree on
# --------------------------------------------------------------------------------------

#: ``Chat Room.room_type`` values.
ROOM_TYPE_DM: Final[str] = "Direct Message"
ROOM_TYPE_GROUP: Final[str] = "Group"
ROOM_TYPE_ORGANIZATION: Final[str] = "Organization"
ROOM_TYPE_DOCUMENT: Final[str] = "Document"

#: ``Chat Room.provisioning_mode`` values.
PROVISION_NOT_MIRRORED: Final[str] = "Not Mirrored"
PROVISION_ON_CREATE: Final[str] = "On Create"
PROVISION_ON_FIRST_MESSAGE: Final[str] = "On First Message"

#: ``Chat Room.provisioning_state`` values.
STATE_PENDING: Final[str] = "Pending"
STATE_PROVISIONING: Final[str] = "Provisioning"
STATE_READY: Final[str] = "Ready"
STATE_FAILED: Final[str] = "Failed"
STATE_DISABLED: Final[str] = "Disabled"

#: The ``Chat Relay Job.operation`` string this module's ``requestId`` is derived from.
#: Taken from the relay job's own Select options rather than invented, so that if
#: provisioning is ever driven through a relay job row the derivation lands on the same
#: value and a replay is still a replay. **Never change this string**: it is an input to
#: :func:`ids.request_id`, so changing it re-mints every provisioning request id and turns
#: a retry into a duplicate space.
OPERATION_SPACE_CREATE: Final[str] = "Space Create"

#: ``Space.spaceThreadingState``. Read back, never requested — the field is Output only and
#: the API cannot create a threaded space (PHASE2_VERIFIED.md §1.2). The room's Select
#: carries exactly these three plus blank, and ``SPACE_THREADING_STATE_UNSPECIFIED`` maps to
#: blank rather than being stored, because storing an "unspecified" is storing a guess.
THREADING_STATES: Final[frozenset[str]] = frozenset(
	{"THREADED_MESSAGES", "GROUPED_MESSAGES", "UNTHREADED_MESSAGES"}
)

#: The Space field that says this space admits people from outside the Workspace domain.
#: ``VERIFY:`` the discovery document names it ``externalUserAllowed`` and marks it
#: immutable; it was not observed populated on a live response this session, so the reader
#: below treats *absent* as ``False`` and says so rather than defaulting to "assume unsafe".
#: Defaulting the other way would refuse to mirror every space the API declines to annotate,
#: which is a gate that fails closed into "nothing works" — see :func:`external_users_allowed`.
EXTERNAL_USERS_FIELD: Final[str] = "externalUserAllowed"

#: How many units one batch pass provisions before it re-checks the project budget and
#: re-enqueues itself. Small on purpose: the prod deploy kills a running worker, so a pass
#: that commits every unit and hands control back often loses at most one unit's work.
DEFAULT_BATCH_SIZE: Final[int] = 10

#: Ceiling on rooms one orphan sweep inspects, so a site with thousands of document rooms
#: cannot turn a cron tick into a long-running scan.
DEFAULT_SWEEP_LIMIT: Final[int] = 200

#: Truncation for anything written into a ``Small Text`` error column, matching the field
#: descriptions on ``Chat Room.provisioning_error`` and ``Chat Provisioning Run.last_error``.
_ERROR_CHARS: Final[int] = 1000

#: Matches the transport's convention so one grep finds the whole Google surface's logs.
_LOGGER_NAME: Final[str] = "chat.sync.provisioning"


class ProvisioningError(Exception):
	"""Provisioning failed in a way a retry might fix — a 5xx, a quota, a transport fault."""


class ProvisioningRefused(ProvisioningError):
	"""Provisioning is **not permitted** and retrying will not change that.

	Separate from :class:`ProvisioningError` because the two want opposite handling: a
	failure is re-driven by the next sweep, a refusal must stop and be read by a human. The
	external-user gate raises this, and so does a room whose shape cannot satisfy Google's
	per-``spaceType`` contract.
	"""


# --------------------------------------------------------------------------------------
# Pure: what shape of space does this room want, and is it allowed one
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpacePlan:
	"""Everything ``spaces.setup`` needs for one room, decided without touching Google.

	Frozen and constructed in one place so that the dry-run report, the live call and the
	test all describe the same intent. ``member_emails`` **excludes** ``caller_email``:
	``spaces:setup`` adds the caller implicitly and rejects a body that names them again.
	"""

	room: str
	room_type: str
	space_type: str
	display_name: str
	caller_email: str
	member_emails: tuple[str, ...]
	request_id: str

	def as_log_line(self) -> str:
		"""One human-readable line for ``Chat Provisioning Run.log``. Identifiers only.

		This is the deliverable of a dry run — the counts say how many, only the log says
		which, and "which" is what a person reads before unticking ``dry_run``. Member
		*addresses* are identifiers and are included; nothing here is message content.
		"""
		return (
			f"{self.room_type} {self.room}: {self.space_type} "
			f"display_name={self.display_name or '<none>'} caller={self.caller_email} "
			f"members={len(self.member_emails)}"
		)


def space_type_for(room_type: str, display_name: str) -> str:
	"""Which of Google's three mutually exclusive space contracts this room wants.

	The mapping, and the reasoning that is not obvious from the table:

	==================  =====================================================================
	``Direct Message``  ``DIRECT_MESSAGE`` — exactly one other member, never a display name
	``Organization``    ``SPACE`` — a named space; the unit's name is the point of it
	``Document``        ``SPACE`` — named after the document, for the same reason
	``Group``           ``SPACE`` when it has a title, ``GROUP_CHAT`` when it does not
	==================  =====================================================================

	That last row is the only judgement call. A ``GROUP_CHAT`` **must not** carry a
	``displayName`` and a ``SPACE`` **must**, so a titled group has exactly one legal
	shape and an untitled one has exactly one other. Choosing by the title rather than
	asking the caller means the two can never be combined wrongly, and it makes the failure
	that remains — an untitled group with only one other member — a validation error naming
	the constraint instead of a 400 from Google four steps later.
	"""
	kind = (room_type or "").strip()
	if kind == ROOM_TYPE_DM:
		return chat_client.SpaceType.DIRECT_MESSAGE.value
	if kind == ROOM_TYPE_GROUP and not (display_name or "").strip():
		return chat_client.SpaceType.GROUP_CHAT.value
	return chat_client.SpaceType.SPACE.value


def choose_caller(members: Sequence[Mapping[str, Any]]) -> str:
	"""Which human the relay impersonates to create the space.

	``spaces:setup`` is **user authentication only** — there is always a human creating the
	space — and whoever it is becomes the space's manager. Managers first, then the
	lexicographically lowest active member, because the choice has to be **deterministic**:
	a retry that picked a different caller would create the space under a different manager,
	and ``requestId`` replay only protects us while the request is the same request.

	Returns ``""`` when there is nobody, which the caller turns into a refusal. A space with
	no ERPNext member is a space nothing can ever relay into.
	"""
	active = [row for row in members if row.get("is_active", 1) and (row.get("user") or "").strip()]
	if not active:
		return ""
	managers = sorted(str(row["user"]) for row in active if str(row.get("role") or "") == "Manager")
	if managers:
		return managers[0]
	return sorted(str(row["user"]) for row in active)[0]


def build_plan(
	*,
	room: str,
	room_type: str,
	title: str,
	members: Sequence[Mapping[str, Any]],
	site: str,
	dm_user_1: str = "",
	dm_user_2: str = "",
) -> SpacePlan:
	"""Turn a room's row plus its membership into a validated :class:`SpacePlan`.

	**The validation is Google's own, borrowed rather than restated.** The plan is handed to
	:func:`chat_client.build_setup_space_call`, which encodes the three per-``spaceType``
	contracts, and any ``ValueError` it raises becomes a :class:`ProvisioningRefused` here.
	Restating those rules in a second place is how the two copies drift, and the copy that
	drifts is always the one furthest from the API.

	A DM is special-cased: its membership is the *pair on the room row*, not the
	``Chat Room Member`` table, because ``(dm_user_1, dm_user_2)`` is the room's identity and
	is the thing the unique index enforces. Taking the members from the child table would let
	a stray third row turn a DM into a call Google rejects.
	"""
	display_name = (title or "").strip()
	space_type = space_type_for(room_type, display_name)

	if space_type == chat_client.SpaceType.DIRECT_MESSAGE.value:
		pair = [user for user in (dm_user_1, dm_user_2) if (user or "").strip()]
		if len(pair) != 2:
			raise ProvisioningRefused(
				f"Chat Room {room} is a Direct Message but does not name both users "
				f"(dm_user_1={dm_user_1!r}, dm_user_2={dm_user_2!r}). The pair is the room's "
				"identity and the unique index depends on it."
			)
		caller, peer = pair[0], pair[1]
		member_emails: tuple[str, ...] = (peer,)
		display_name = ""
	else:
		caller = choose_caller(members)
		if not caller:
			raise ProvisioningRefused(
				f"Chat Room {room} has no active member to create the space as. spaces.setup is "
				"user-authentication only, so there is always a human creating the space, and a "
				"room with no ERPNext member is a room nothing could ever relay into."
			)
		member_emails = tuple(
			sorted(
				{
					str(row["user"]).strip()
					for row in members
					if row.get("is_active", 1)
					and (row.get("user") or "").strip()
					and str(row["user"]).strip() != caller
				}
			)
		)
		if space_type == chat_client.SpaceType.SPACE.value:
			display_name = display_name or ""
		else:
			display_name = ""

	plan = SpacePlan(
		room=room,
		room_type=(room_type or "").strip(),
		space_type=space_type,
		display_name=display_name,
		caller_email=caller,
		member_emails=member_emails,
		request_id=ids.request_id(room, OPERATION_SPACE_CREATE, site=site),
	)
	assert_plan_is_legal(plan)
	return plan


def assert_plan_is_legal(plan: SpacePlan) -> None:
	"""Run the plan through the transport's builder purely, to surface a 400 as a refusal.

	Nothing is sent. :func:`chat_client.build_setup_space_call` is a pure function that
	returns a description of a request, and every one of Google's shape constraints —
	``SPACE`` needs a ``displayName``; ``GROUP_CHAT`` must not have one and needs at least
	two memberships; ``DIRECT_MESSAGE`` must not have one and needs exactly one — is enforced
	inside it. Calling it here is how "encode the constraints as validation, not runtime
	discovery" is implemented without a second copy of the rules.
	"""
	try:
		chat_client.build_setup_space_call(
			space_type=plan.space_type,
			request_id=plan.request_id,
			member_emails=plan.member_emails,
			display_name=plan.display_name or None,
			caller_email=plan.caller_email,
		)
	except ValueError as exc:
		raise ProvisioningRefused(
			f"Chat Room {plan.room} cannot be provisioned as a {plan.space_type}: {exc}"
		) from None


def external_users_allowed(space_resource: Mapping[str, Any] | None) -> bool:
	"""Does this space admit members from outside the Workspace domain? (CQ-12)

	Absent reads as ``False``, deliberately, and the direction is worth defending. The field
	is documented but was never observed populated on a live response this session, and a
	reader that treated *absent* as *unsafe* would refuse to mirror every space Google
	declines to annotate — a gate that fails into "the feature does not work", which is
	the kind of failure that gets a gate deleted rather than fixed. The room field is
	re-read on every reconciliation pass, so a space that acquires the flag later is caught
	by the next sweep rather than never.
	"""
	if not space_resource:
		return False
	return bool(space_resource.get(EXTERNAL_USERS_FIELD))


def threading_state_of(space_resource: Mapping[str, Any] | None) -> str:
	"""``spaceThreadingState`` as the room's Select stores it, or ``""``.

	Read back, never assumed. ``GROUPED_MESSAGES`` is the only plausible reading for an
	API-created named space and **no document states it**, which is precisely why this is a
	read. ``SPACE_THREADING_STATE_UNSPECIFIED`` — which is what dry-run reports, honestly —
	maps to blank: storing "unspecified" in a Select whose options are three real states
	would be storing a guess in a column somebody later reads as fact.
	"""
	value = str((space_resource or {}).get("spaceThreadingState") or "").strip()
	return value if value in THREADING_STATES else ""


def assert_mirror_allowed(*, room: str, external: bool, whitelisted: bool) -> None:
	"""The data-egress gate. Raises :class:`ProvisioningRefused`; never returns ``False``.

	A boolean return would be checked in one place and forgotten in another. A raise cannot
	be forgotten, and the exception type separates "retry later" from "a human must decide",
	which is exactly what this decision is: the whole point of the gate is that sending
	company conversation outside the domain is a person's call, not a config field's.
	"""
	if external and not whitelisted:
		raise ProvisioningRefused(
			f"Chat Room {room} is backed by a space with externalUserAllowed set, and "
			"mirror_whitelisted is not ticked. Mirroring it would relay internal conversation "
			"into a space that admits members from outside the Workspace domain (CQ-12). Tick "
			"Mirror Whitelisted on the room if that is intended; nothing is relayed until "
			"somebody does."
		)


# --------------------------------------------------------------------------------------
# Pure: the resumable batch
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchSlice:
	"""One pass's worth of work, plus where the cursor lands if every unit succeeds."""

	units: tuple[str, ...]
	next_cursor: str
	remaining: int

	@property
	def exhausted(self) -> bool:
		return self.remaining == 0


def resume_from(units: Sequence[str], cursor: str, batch_size: int) -> BatchSlice:
	"""The next ``batch_size`` units **strictly after** ``cursor``, in total order.

	Three properties, each of which the resume path depends on:

	* **Strictly after.** The cursor is written only once a unit has committed, so the unit
	  it names is done. ``>`` rather than ``>=`` is what stops a resume re-provisioning the
	  last unit of the previous pass; the duplicate would be caught by
	  ``unique(gchat_space_name)`` and would still have spent a space write from a 60-a-minute
	  budget.
	* **Total order.** The caller must read units in the same order every pass
	  (``order_by="… asc"``). A cursor is meaningless over an unstable ordering: units would
	  be skipped, not merely repeated, and a skipped unit is invisible — no error, no failed
	  count, just a department that never got a space and a run that reports Completed.
	* **Sorted here anyway.** Cheap, and it makes the function total over any input rather
	  than correct only when the caller remembered.
	"""
	size = max(int(batch_size), 1)
	ordered = sorted({str(unit) for unit in units if str(unit or "").strip()})
	marker = str(cursor or "")
	pending = [unit for unit in ordered if unit > marker]
	window = tuple(pending[:size])
	return BatchSlice(
		units=window,
		next_cursor=window[-1] if window else marker,
		remaining=max(len(pending) - len(window), 0),
	)


# --------------------------------------------------------------------------------------
# Frappe: small helpers
# --------------------------------------------------------------------------------------


def _logger() -> Any:
	try:
		return frappe.logger(_LOGGER_NAME, allow_site=True)
	except Exception:
		try:
			return frappe.logger(_LOGGER_NAME)
		except Exception:
			return None


def _log(message: str) -> None:
	"""Info-level, identifiers only. Never a message body, never a token."""
	logger = _logger()
	if logger is not None:
		try:
			logger.info(message)
		except Exception:
			return


def alert(key: str, title: str, message: str, *, throttle_seconds: int = 3600) -> None:
	"""One Error Log row per ``key`` per window — the house pattern from ``gchat/webhook.py``.

	Throttled because every caller of this is inside a loop over rooms or units: an
	un-throttled alert on a systemic misconfiguration writes one Error Log row per room and
	buries the signal it exists to raise. Never raises: an alerting path that can fail the
	work it is reporting on is worse than no alert.
	"""
	try:
		cache = frappe.cache()
		cache_key = f"chat:provisioning:alert:{key}"
		if cache.get_value(cache_key):
			return
		cache.set_value(cache_key, 1, expires_in_sec=int(throttle_seconds))
		frappe.log_error(title=title, message=message[:_ERROR_CHARS])
	except Exception:
		return


def _settings() -> Any:
	return frappe.get_cached_doc("Chat Settings")


def _setting_int(fieldname: str, default: int) -> int:
	"""``getattr(obj, field, None) or default`` — the house rule, and it is load-bearing here.

	These reads happen during ``bench migrate`` and during ERPNext's own test bootstrap,
	before this app's fields necessarily exist. A missing tuning value must degrade to a
	default, never crash a provisioning worker.
	"""
	try:
		raw = getattr(_settings(), fieldname, None)
	except Exception:
		return int(default)
	try:
		return int(raw) if raw not in (None, "") else int(default)
	except (TypeError, ValueError):
		return int(default)


def _now() -> Any:
	from frappe.utils import now_datetime

	return now_datetime()


def _room_row(room: str) -> dict[str, Any]:
	"""The room's provisioning-relevant columns, or ``{}``.

	``frappe.db.get_value`` rather than ``get_doc``: this runs in a loop over units and a
	full document load pulls the child tables with it.
	"""
	row = frappe.db.get_value(
		"Chat Room",
		room,
		[
			"name",
			"room_type",
			"title",
			"is_archived",
			"linked_doctype",
			"linked_document",
			"dm_user_1",
			"dm_user_2",
			"provisioning_mode",
			"provisioning_state",
			"provisioning_attempts",
			"membership_authority",
			"external_users_allowed",
			"mirror_whitelisted",
			"gchat_space_name",
			"gchat_space_type",
			"gchat_threading_state",
		],
		as_dict=True,
	)
	return dict(row) if row else {}


def _member_rows(room: str) -> list[dict[str, Any]]:
	"""Active ``Chat Room Member`` rows for a room.

	``frappe.get_all`` is ``get_list(ignore_permissions=True)`` and is used deliberately:
	this is an operational read inside a background job that has no session, and membership
	metadata is not message content. The moment this function is tempted to read
	``Chat Message``, it must grow ``permissions.membership_filter_sql`` instead.
	"""
	return [
		dict(row)
		for row in frappe.get_all(
			"Chat Room Member",
			filters={"room": room, "is_active": 1},
			fields=["name", "user", "role", "is_active", "gchat_membership_name", "gchat_member_state"],
			order_by="user asc",
			limit_page_length=0,
		)
	]


def _duplicate_guard() -> Any:
	"""``savepoint`` armed with both duplicate classes, borrowed from ``sync/membership.py``.

	Imported function-locally, like every other reference this module makes to ``membership``,
	to keep the two modules' import graph acyclic — ``membership`` reads this module's
	vocabulary constants. One copy of the rule, in the module that owns the member table.
	"""
	from erpnext_enhancements.chat.sync.membership import savepoint_catching_duplicates

	return savepoint_catching_duplicates()


def _clear_duplicate_msgprint() -> None:
	"""Drop the *"must be unique"* banner Frappe emits before raising. See ``membership``."""
	from erpnext_enhancements.chat.sync.membership import clear_expected_duplicate_message

	clear_expected_duplicate_message()


def _insert_room_deduped(values: Mapping[str, Any], *, probe: Mapping[str, Any]) -> tuple[str, bool]:
	"""Create a ``Chat Room``, treating a composite-unique collision as SUCCESS.

	Returns ``(room name, was created by us)``.

	``unique(linked_doctype, linked_document)`` is what makes "one room per document" true —
	§F.4.2 chose it precisely so the idempotency needs no lock. Both callers here reach it
	through a ``get_value`` probe followed by an ``insert``, and the window between the two is
	exactly where two people clicking the same button, or the batch and a button, land. Neither
	caller has any use for the exception: both already return the existing room when their probe
	hits, so the collision has one correct answer and raising out of a whitelisted endpoint was
	never it.

	The ``savepoint`` is mandatory rather than hygiene — a failed statement can poison the
	surrounding transaction, and :func:`create_document_room` goes on to write member rows in it
	— and **both** exception classes are caught because this index is not the primary key
	(``PHASE2_VERIFIED.md`` §1.1).
	"""
	existing = frappe.db.get_value("Chat Room", dict(probe), "name")
	if existing:
		return str(existing), False

	with _duplicate_guard():
		doc = frappe.get_doc({"doctype": "Chat Room", **dict(values)})
		doc.insert(ignore_permissions=True)
		return str(doc.name), True

	# Only reachable when the savepoint swallowed a collision: somebody committed the same
	# document's room between the probe and the insert. Read back the winner.
	_clear_duplicate_msgprint()
	return str(frappe.db.get_value("Chat Room", dict(probe), "name") or ""), False


def _set_room(room: str, values: Mapping[str, Any]) -> None:
	"""Write room columns without touching ``modified``.

	``update_modified=False`` on every write here. A provisioning retry is not an edit, and
	``Chat Room.modified`` moving on each attempt makes a room look freshly changed to
	anything that sorts or schedules on it — which is also why ``last_reconcile_at`` and
	``last_event_at`` exist as their own columns rather than being inferred from ``modified``.
	"""
	frappe.db.set_value("Chat Room", room, dict(values), update_modified=False)


def _record_failure(room: str, exc: BaseException, *, state: str = STATE_FAILED) -> None:
	detail = chat_client.scrub_secrets(f"{type(exc).__name__}: {exc}")[:_ERROR_CHARS]
	_set_room(room, {"provisioning_state": state, "provisioning_error": detail})
	_log(f"chat provisioning failed room={room} state={state} error={detail[:200]}")


def _relay_client(subject: str) -> chat_client.GoogleChatClient:
	"""A user-identity client impersonating ``subject``.

	No app-identity fallback exists on purpose: ``spaces.setup``, ``members.create`` and
	``members.delete`` are all user-auth only under the scopes this deployment holds, and a
	fallback would turn an impossible call into a 403 three steps from where it was decided.
	"""
	return chat_client.GoogleChatClient(subject=subject, identity=chat_client.AuthIdentity.USER)


def _quota() -> ProjectQuota:
	return ProjectQuota()


def _charge_space_write(quota: ProjectQuota) -> bool:
	"""Spend one of the project's 60 space writes per minute, or report that it is spent.

	A refusal is **not an error**. It means "come back in the next window", and every caller
	here turns it into a pause rather than a failure — the batch is resumable precisely so
	that running out of budget costs a minute instead of a run.
	"""
	limit = _setting_int("project_space_writes_per_minute", 60)
	return quota.charge(BUCKET_SPACE_WRITES, limit=limit)


# --------------------------------------------------------------------------------------
# Mode 1 — provision one room
# --------------------------------------------------------------------------------------


def enqueue_room_provisioning(room: str, *, queue: str = "long") -> bool:
	"""Queue :func:`provision_room_job` for one room, deduplicated.

	Provided for callers that may import this module. **The write path may not** — see the
	module docstring — and enqueues the dotted string itself.

	``deduplicate=True`` needs ``job_id`` and v16 throws without it; note that it also skips
	a new enqueue while an existing job is ``STARTED``, not merely ``QUEUED``, so a second
	message arriving mid-provision does not queue a second provision. That is the behaviour
	we want and it is worth knowing it is not the behaviour the flag's name suggests.
	"""
	target = (room or "").strip()
	if not target:
		return False
	frappe.enqueue(
		"erpnext_enhancements.chat.sync.provisioning.provision_room_job",
		queue=queue,
		room=target,
		job_id=f"chat-provision-{target}",
		deduplicate=True,
		enqueue_after_commit=True,
	)
	return True


def provision_room_job(room: str) -> None:
	"""Background entry point. **Records failures instead of raising them.**

	A raise out of a background job publishes the failing frames' locals into the Error Log,
	and the frames below hold an ``Authorization: Bearer`` header — this app has leaked
	private key material exactly that way once. So the job catches, scrubs, writes the reason
	onto the room and returns; the durable record is ``Chat Room.provisioning_state``, which
	the sweep re-drives, not an exception nobody sees.
	"""
	try:
		provision_room(room)
	except ProvisioningRefused as exc:
		_record_failure(room, exc, state=STATE_FAILED)
		alert(
			f"refused:{room}",
			"Chat provisioning refused",
			f"Chat Room {room} will not be provisioned. {chat_client.scrub_secrets(str(exc))}",
		)
	except Exception as exc:
		_record_failure(room, exc)


def sweep_pending_provisioning(limit: int = DEFAULT_SWEEP_LIMIT) -> dict[str, Any]:
	"""Scheduler entry: queue provisioning for every room that is owed a space.

	**This is mode 1's durable trigger, and it is deliberately not the only one.** The
	low-latency path is the write path enqueueing ``provision_room_job`` on the first message
	(see the module docstring for the dotted string it must use, since it may not import this
	module). That path is a queue entry, and the prod deploy ``FLUSHDB``s the queue Redis —
	so a room whose only claim on a space was an enqueue that vanished during a release would
	wait forever, and the symptom is one conversation that silently never mirrors. A sweep over
	*rows* cannot vanish.

	It also picks up two states nothing else recovers:

	* ``Provisioning`` — a worker killed mid-flight leaves the room saying that forever.
	* ``On Create`` rooms that were created while the master switch was off.

	The filter is ``last_message`` being set, not a count of messages: it is a denormalised
	column on the room, so the whole sweep is one indexed query rather than one per room, and
	"has this conversation started" is exactly what it answers. ``On Create`` rooms bypass it
	because their whole point is not waiting for a first message.
	"""
	queued = 0
	inspected = 0
	for state in (STATE_PENDING, STATE_PROVISIONING):
		rows = frappe.get_all(
			"Chat Room",
			filters={
				"is_archived": 0,
				"provisioning_state": state,
				"provisioning_mode": ["!=", PROVISION_NOT_MIRRORED],
				"gchat_space_name": ["is", "not set"],
			},
			fields=["name", "provisioning_mode", "last_message"],
			order_by="modified asc",
			limit_page_length=int(limit),
		)
		inspected += len(rows)
		for row in rows:
			started = bool(row.get("last_message"))
			eager = str(row.get("provisioning_mode") or "") == PROVISION_ON_CREATE
			if not (started or eager):
				continue
			if enqueue_room_provisioning(str(row["name"])):
				queued += 1
	return {"inspected": inspected, "queued": queued}


def provision_room(
	room: str,
	*,
	client: Any | None = None,
	quota: ProjectQuota | None = None,
	sync_members: bool = True,
) -> str:
	"""Ensure ``room`` has a Google space, and return its resource name.

	**Idempotent, and that is the headline property.** Running this twice creates one space.
	Three mechanisms stack, deliberately, because each covers a case the others do not:

	1. A room that already carries ``gchat_space_name`` short-circuits to a metadata refresh
	   and makes no space write at all.
	2. A DM asks ``spaces.findDirectMessage`` first. ``spaces.setup`` would also return the
	   existing DM, but a **read does not spend the 60-writes-per-minute space budget** and a
	   write does.
	3. ``requestId`` is derived from the room name, so even a genuine duplicate call replays
	   to the same space rather than creating a second one — within whatever Google's
	   deduplication window is, which is undocumented, which is why it is the third line of
	   defence and not the first.

	Returns ``""`` when the room is deliberately not mirrored or when a dry run declined to
	bind a synthetic name.
	"""
	row = _room_row(room)
	if not row:
		raise ProvisioningRefused(f"Chat Room {room} does not exist.")

	mode = str(row.get("provisioning_mode") or "")
	if mode == PROVISION_NOT_MIRRORED:
		_set_room(room, {"provisioning_state": STATE_DISABLED, "provisioning_error": ""})
		return ""

	existing = str(row.get("gchat_space_name") or "")
	subject_hint = str(row.get("dm_user_1") or "")

	if existing:
		# Bound already. Refresh what Google observes rather than assume it is unchanged: a
		# space can acquire an external member long after we provisioned it, and the gate has
		# to see that.
		api = client or _relay_client(subject_hint or _refresh_subject(room))
		refresh_space_metadata(room, client=api)
		if sync_members:
			_sync_members(room, client=api)
		return existing

	members = _member_rows(room)
	plan = build_plan(
		room=room,
		room_type=str(row.get("room_type") or ""),
		title=str(row.get("title") or ""),
		members=members,
		site=frappe.local.site,
		dm_user_1=str(row.get("dm_user_1") or ""),
		dm_user_2=str(row.get("dm_user_2") or ""),
	)

	api = client or _relay_client(plan.caller_email)
	budget = quota or _quota()

	_set_room(
		room,
		{
			"provisioning_state": STATE_PROVISIONING,
			"provisioning_attempts": int(row.get("provisioning_attempts") or 0) + 1,
			"provisioning_error": "",
		},
	)

	resource = _find_existing_dm(plan, api) if plan.space_type == "DIRECT_MESSAGE" else None

	if resource is None:
		# A dry run spends no project budget. It makes no space write, and letting it consume
		# the 60-a-minute window would let a planning pass throttle real provisioning — a
		# rehearsal is not allowed to have consequences.
		if not getattr(api, "dry_run", False) and not _charge_space_write(budget):
			# Not a failure. The project's 60 space writes for this minute are gone; the room
			# stays Pending and the next pass picks it up.
			_set_room(
				room,
				{
					"provisioning_state": STATE_PENDING,
					"provisioning_error": "Deferred: the project space-write budget for this minute is spent.",
				},
			)
			return ""
		resource = _setup_space(plan, api)

	space_name = str((resource or {}).get("name") or "")
	if not space_name:
		raise ProvisioningError(f"spaces.setup returned no resource name for Chat Room {room}.")

	# Read the space back rather than trusting the create response. spaceThreadingState is
	# Output only and which state a new space lands in is documented nowhere, and
	# externalUserAllowed is the gate's only input.
	observed = _get_space(space_name, api) or dict(resource)
	bound = _bind_space(room, observed)
	if bound:
		_mark_setup_members(room, plan)
		if sync_members:
			_sync_members(room, client=api)
	return bound


def _mark_setup_members(room: str, plan: SpacePlan) -> None:
	"""Tell the membership module that Google accepted these people at setup time.

	``spaces.setup`` creates the memberships but returns only the Space, so their resource
	names are never seen — and a Chat listing carries no email addresses, so they can never be
	matched back either (``sync/membership.py``'s module docstring). Recording "present" here
	is what makes the very first membership pass on a fresh room issue **zero** writes instead
	of one 409 per member.
	"""
	from erpnext_enhancements.chat.sync import membership

	try:
		membership.mark_setup_members_present(room, (plan.caller_email, *plan.member_emails))
	except Exception:
		# Cosmetic-only: without it the next pass issues a 409 per member and then records the
		# same thing. Never worth failing a completed provisioning over.
		_log(f"chat provisioning could not mark setup members present room={room}")


def _refresh_subject(room: str) -> str:
	"""A member to impersonate for a read-only refresh of an already-bound room."""
	members = _member_rows(room)
	caller = choose_caller(members)
	if not caller:
		raise ProvisioningRefused(
			f"Chat Room {room} has no active member to act as; even a read is made as somebody."
		)
	return caller


def _find_existing_dm(plan: SpacePlan, api: Any) -> dict[str, Any] | None:
	"""``spaces.findDirectMessage``, tolerating the 404 that means "there isn't one".

	The 404 is the expected answer for a first-time DM and must not read as a failure. Any
	other status is a real problem and is allowed to propagate — swallowing a 403 here would
	turn a missing scope into a silent duplicate-space create on the next line.
	"""
	try:
		return api.find_direct_message(plan.member_emails[0])
	except chat_client.GoogleChatAPIError as exc:
		if exc.status == 404:
			return None
		raise ProvisioningError(f"findDirectMessage failed for Chat Room {plan.room}: {exc}") from None
	except chat_client.GoogleChatError as exc:
		raise ProvisioningError(f"findDirectMessage failed for Chat Room {plan.room}: {exc}") from None


def _setup_space(plan: SpacePlan, api: Any) -> dict[str, Any]:
	"""``spaces.setup``. Every Google exception is re-raised ``from None``."""
	try:
		return api.setup_space(
			space_type=plan.space_type,
			request_id=plan.request_id,
			member_emails=plan.member_emails,
			display_name=plan.display_name or None,
		)
	except ValueError as exc:
		raise ProvisioningRefused(f"Chat Room {plan.room}: {exc}") from None
	except chat_client.GoogleChatError as exc:
		raise ProvisioningError(f"spaces.setup failed for Chat Room {plan.room}: {exc}") from None


def _get_space(space_name: str, api: Any) -> dict[str, Any] | None:
	"""``spaces.get``, tolerant: a read-back failure must not lose a space we just created.

	If the read fails we still have the create response, and binding the space name matters
	more than learning its threading state. The gate is the exception to that tolerance —
	:func:`_bind_space` re-applies it against whatever resource it is given, so a failed
	read-back cannot smuggle an external-allowed space past the check via a create response
	that omitted the field. That is why the fallback resource is the *create* response and
	not an empty dict.
	"""
	try:
		return api.get_space(space_name)
	except chat_client.GoogleChatError as exc:
		_log(f"chat provisioning could not read back {space_name}: {chat_client.scrub_secrets(str(exc))}")
		return None


def _bind_space(room: str, resource: Mapping[str, Any]) -> str:
	"""Write an observed Space resource onto the room, or refuse to.

	Two refusals live here, and both write state before they raise or return so the reason is
	on the row rather than only in a log:

	* **External users.** ``externalUserAllowed`` is persisted first — so the room records
	  what Google says even when the gate then refuses — and the gate raises
	  :class:`ProvisioningRefused`.
	* **A dry run.** ``dryrun.is_dryrun_name`` recognises the synthetic ``spaces/DRYRUN-…``
	  the client returns when Chat is disabled or in dry-run mode. Binding it would burn the
	  unique index on a space that does not exist and send every later reconciliation hunting
	  for it, so the name is logged and discarded.

	And a third, which is a collision rather than a refusal: ``gchat_space_name`` is unique, and
	this is the write that occupies it. Unlike every other duplicate on this surface **a
	collision here is not success** — it means a *different* room already mirrors this space,
	which is two rooms relaying into one conversation and a question for a person. The savepoint
	is still mandatory, because the caller's own error handling writes to the same transaction;
	what changes is that the swallowed collision is turned back into a
	:class:`ProvisioningRefused` instead of being reported as a bind.
	"""
	space_name = str(resource.get("name") or "")
	external = external_users_allowed(resource)

	_set_room(room, {"external_users_allowed": 1 if external else 0})

	if external:
		whitelisted = bool(frappe.db.get_value("Chat Room", room, "mirror_whitelisted"))
		try:
			assert_mirror_allowed(room=room, external=True, whitelisted=whitelisted)
		except ProvisioningRefused as exc:
			_set_room(
				room,
				{
					"provisioning_state": STATE_FAILED,
					"provisioning_error": str(exc)[:_ERROR_CHARS],
				},
			)
			raise

	if dryrun.is_dryrun_name(space_name):
		_set_room(
			room,
			{
				"provisioning_state": STATE_PENDING,
				"provisioning_error": (
					"Dry run: a space would have been created. The synthetic name is not stored — "
					"binding it would occupy unique(gchat_space_name) with a space that does not exist."
				),
			},
		)
		_log(f"chat provisioning dry-run room={room} would_create={space_name}")
		return ""

	bound = False
	with _duplicate_guard():
		_set_room(
			room,
			{
				"gchat_space_name": space_name,
				"gchat_space_type": str(resource.get("spaceType") or ""),
				"gchat_threading_state": threading_state_of(resource),
				"gchat_space_uri": str(resource.get("spaceUri") or ""),
				"provisioning_state": STATE_READY,
				"provisioning_error": "",
			},
		)
		bound = True

	if not bound:
		_clear_duplicate_msgprint()
		owner = frappe.db.get_value("Chat Room", {"gchat_space_name": space_name}, "name")
		detail = (
			f"{space_name} is already bound to Chat Room {owner or 'another room'}. Two rooms "
			"mirroring one space would relay each other's messages back and forth; nothing is "
			"bound until a human decides which room owns it."
		)
		_set_room(room, {"provisioning_state": STATE_FAILED, "provisioning_error": detail[:_ERROR_CHARS]})
		alert(f"space-taken:{room}", "Chat space already bound to another room", detail)
		raise ProvisioningRefused(f"Chat Room {room}: {detail}")

	_log(f"chat provisioning bound room={room} space={space_name}")
	return space_name


def refresh_space_metadata(room: str, *, client: Any | None = None) -> str:
	"""Re-read an already-bound space and re-apply the external-user gate.

	Called on every provisioning pass over a bound room and available to the §4.J
	reconciliation sweep. ``externalUserAllowed`` is immutable on a space, but *which space*
	a room points at is not — and a room can be whitelisted, or un-whitelisted, at any time.
	Re-reading is cheap (a space read, not a write) and is the only thing that keeps the gate
	true after provisioning day.
	"""
	space_name = str(frappe.db.get_value("Chat Room", room, "gchat_space_name") or "")
	if not space_name or dryrun.is_dryrun_name(space_name):
		return space_name
	api = client or _relay_client(_refresh_subject(room))
	resource = _get_space(space_name, api)
	if resource is None:
		return space_name

	external = external_users_allowed(resource)
	_set_room(
		room,
		{
			"external_users_allowed": 1 if external else 0,
			"gchat_threading_state": threading_state_of(resource),
			"gchat_space_type": str(resource.get("spaceType") or ""),
		},
	)
	whitelisted = bool(frappe.db.get_value("Chat Room", room, "mirror_whitelisted"))
	if external and not whitelisted:
		_set_room(
			room,
			{
				"provisioning_state": STATE_FAILED,
				"provisioning_error": (
					"externalUserAllowed is set on this space and Mirror Whitelisted is not ticked; "
					"mirroring is refused (CQ-12)."
				),
			},
		)
		alert(
			f"external:{room}",
			"Chat room backed by an external-allowed space",
			f"Chat Room {room} points at {space_name}, which admits members from outside the "
			"Workspace domain. Mirroring is refused until a human ticks Mirror Whitelisted.",
		)
	return space_name


def _sync_members(room: str, *, client: Any | None) -> None:
	"""Hand off to the membership diff, and never let it un-provision a space.

	Imported inside the function purely to keep the two modules' import graph acyclic —
	``membership`` reads provisioning's vocabulary constants. A membership failure is logged
	and swallowed here because the space is already created and bound: raising would leave a
	real space with a room that says ``Failed``, and the next convergent pass fixes membership
	anyway.
	"""
	from erpnext_enhancements.chat.sync import membership

	try:
		membership.sync_membership_to_google(room, client=client)
	except Exception as exc:
		_log(f"chat provisioning membership sync deferred room={room} error={type(exc).__name__}")


# --------------------------------------------------------------------------------------
# Mode 2 — org mirroring, as a checkpointed batch
# --------------------------------------------------------------------------------------

#: The org DocTypes a run may walk. Closed, because ``mode`` is interpolated into a query
#: as a DocType name and because "every DocType is mirrorable" is not a feature anybody
#: asked for. Matches ``Chat Provisioning Run.mode``'s Select options exactly.
ORG_MODES: Final[frozenset[str]] = frozenset({"Department", "Team", "Project"})


@frappe.whitelist()
def enroll_org_units(mode: str, units: str | Sequence[str], title_field: str = "") -> dict[str, Any]:
	"""Create ``Chat Room`` rows for named org units. **Zero Google I/O — this is the opt-in.**

	This is what "explicit opt-in per org entity, never provision everything on install"
	looks like as code. Enrolment is a *room* row, which is free and reversible; provisioning
	is a *space*, which is neither. Splitting them means the expensive, irreversible half can
	only ever act on a set a human named, and it makes the set queryable, orderable and
	therefore resumable — which is what :func:`run_provisioning_run` needs a cursor over.

	Idempotent: a unit that already has a room is counted as existing and left alone. The
	room is created with ``provisioning_mode = On First Message`` so that enrolling a
	hundred departments still creates zero spaces until somebody speaks.
	"""
	slice_mode = _checked_mode(mode)
	names = _as_list(units)
	created, existing = [], []
	for unit in names:
		probe = {"linked_doctype": slice_mode, "linked_document": unit}
		title = ""
		if title_field and not frappe.db.get_value("Chat Room", dict(probe), "name"):
			title = str(frappe.db.get_value(slice_mode, unit, title_field) or "")
		# Through the dedupe helper rather than probe-then-insert: enrolment is idempotent by
		# design and the race must not be the one path where it is not.
		_room, was_created = _insert_room_deduped(
			{
				"room_type": ROOM_TYPE_ORGANIZATION,
				"title": title or unit,
				"linked_doctype": slice_mode,
				"linked_document": unit,
				"provisioning_mode": PROVISION_ON_FIRST_MESSAGE,
				"provisioning_state": STATE_PENDING,
				"membership_authority": "ERPNext",
			},
			probe=probe,
		)
		(created if was_created else existing).append(unit)
	return {"mode": slice_mode, "created": created, "existing": existing}


@frappe.whitelist()
def start_org_mirror(mode: str, dry_run: int | bool = 1) -> str:
	"""Open a :class:`Chat Provisioning Run` over the enrolled rooms of one org slice.

	``dry_run`` defaults to **1** and the default is the point: the action being planned
	creates Google spaces, which appear in every coworker's client and cannot be undone
	without a call this codebase refuses to make. A human unticks it after reading the run's
	``log``, which is the only place that says *which* units would be touched.

	One run is one mode. A row meaning "departments and then teams" could not be resumed
	without re-deriving where the boundary fell, which is the exact question the cursor
	exists to answer.
	"""
	slice_mode = _checked_mode(mode)
	if not _setting_int("org_structure_mirroring_enabled", 0):
		frappe.throw(
			frappe._("Enable Org Structure Mirroring in Chat Settings before starting a provisioning run.")
		)
	run = frappe.get_doc(
		{
			"doctype": "Chat Provisioning Run",
			"mode": slice_mode,
			"dry_run": 1 if int(dry_run or 0) else 0,
			"status": "Pending",
			"planned_count": len(_enrolled_units(slice_mode)),
		}
	)
	run.insert(ignore_permissions=True)
	return run.name


def _checked_mode(mode: str) -> str:
	value = str(mode or "").strip()
	if value not in ORG_MODES:
		frappe.throw(frappe._("Provisioning mode must be one of {0}.").format(", ".join(sorted(ORG_MODES))))
	return value


def _as_list(units: str | Sequence[str]) -> list[str]:
	"""Accept a JSON array, a comma-separated string, or a real sequence.

	Whitelisted endpoints receive strings from the Desk, and a helper that only accepted a
	list would work in a test and fail from a button.
	"""
	if isinstance(units, str):
		text = units.strip()
		if text.startswith("["):
			try:
				parsed = json.loads(text)
			except ValueError:
				parsed = []
			return [str(item).strip() for item in parsed if str(item).strip()]
		return [part.strip() for part in text.split(",") if part.strip()]
	return [str(item).strip() for item in (units or ()) if str(item).strip()]


def _enrolled_units(mode: str) -> list[str]:
	"""The units this slice may provision, in the total order the cursor depends on.

	Enrolled **and not yet bound**: a room that already has ``gchat_space_name`` is finished
	and re-walking it would spend a space read confirming what the row already says. The
	filter is on the room, never on the org DocType, so a unit nobody enrolled is invisible
	to the sweep by construction rather than by a check somebody has to remember.
	"""
	return [
		str(row["linked_document"])
		for row in frappe.get_all(
			"Chat Room",
			filters={
				"linked_doctype": mode,
				"room_type": ROOM_TYPE_ORGANIZATION,
				"provisioning_mode": ["!=", PROVISION_NOT_MIRRORED],
				"gchat_space_name": ["is", "not set"],
			},
			fields=["linked_document"],
			order_by="linked_document asc",
			limit_page_length=0,
		)
		if (row.get("linked_document") or "").strip()
	]


def run_provisioning_run(
	run_name: str,
	*,
	client: Any | None = None,
	quota: ProjectQuota | None = None,
	batch_size: int | None = None,
) -> dict[str, Any]:
	"""Advance one :class:`Chat Provisioning Run` by at most one batch. **Never a loop.**

	The shape of one pass, and why each step is where it is:

	1. Read the run. A terminal run is left alone — resuming a Completed run would re-walk
	   from a cursor that already reached the end and report a second Completed.
	2. Slice the enrolled units after ``cursor`` (:func:`resume_from`).
	3. For each unit: provision, then write the cursor, then **commit**. In that order. A
	   cursor written before the work skips a unit on resume, silently — no error, no failed
	   count, just a department that never got a space. A unit provisioned twice is caught by
	   ``unique(gchat_space_name)`` and costs a space write; a unit never provisioned is
	   invisible. The asymmetry decides the order.
	4. Stop and re-enqueue if there is more, so a deploy landing mid-run loses at most one
	   unit's work rather than the whole sweep. The prod deploy ``FLUSHDB``s the queue Redis,
	   so the *row* is what survives — which is the entire reason this DocType exists.

	A per-unit failure is **counted**, not raised. One department whose manager has no
	Workspace account must not abort a sweep of forty, because the resume path would then
	re-attempt the same thirty-nine on every retry.
	"""
	run = _run_row(run_name)
	if not run:
		raise ProvisioningError(f"Chat Provisioning Run {run_name} does not exist.")
	if str(run.get("status") or "") in {"Completed", "Failed"}:
		return {"status": run.get("status"), "processed": 0, "note": "terminal"}

	mode = str(run.get("mode") or "")
	is_dry_run = bool(run.get("dry_run"))
	size = int(batch_size or DEFAULT_BATCH_SIZE)
	budget = quota or _quota()

	units = _enrolled_units(mode)
	batch = resume_from(units, str(run.get("cursor") or ""), size)

	_set_run(run_name, {"status": "Running", "started_at": run.get("started_at") or _now()})

	created = int(run.get("created_count") or 0)
	skipped = int(run.get("skipped_count") or 0)
	failed = int(run.get("failed_count") or 0)
	lines: list[str] = []
	processed = 0
	paused = False

	for unit in batch.units:
		room = frappe.db.get_value("Chat Room", {"linked_doctype": mode, "linked_document": unit}, "name")
		if not room:
			# Enrolment was withdrawn between the slice and now. Not a failure, and the
			# cursor still advances or the run would stall on a unit that no longer exists.
			skipped += 1
			lines.append(f"{unit}: skipped — no enrolled Chat Room")
		elif is_dry_run:
			outcome = _plan_only(str(room), unit)
			skipped += 1
			lines.append(outcome)
		elif not _charge_space_write(budget):
			# The minute's 60 space writes are gone. Pause — do not fail, and do not advance
			# the cursor past a unit we did not do.
			paused = True
			lines.append(f"{unit}: deferred — project space-write budget spent")
			break
		else:
			try:
				# quota=None: the budget for this unit was charged above, and passing the
				# same ProjectQuota in would charge a second write for one space.
				space = provision_room(str(room), client=client, quota=_PrechargedQuota())
				if space:
					created += 1
					lines.append(f"{unit}: created {space}")
				else:
					skipped += 1
					lines.append(f"{unit}: skipped — not mirrored or deferred")
			except ProvisioningRefused as exc:
				failed += 1
				lines.append(f"{unit}: refused — {chat_client.scrub_secrets(str(exc))[:200]}")
			except Exception as exc:
				failed += 1
				lines.append(f"{unit}: failed — {type(exc).__name__}")
				_record_failure(str(room), exc)

		processed += 1
		_set_run(
			run_name,
			{
				"cursor": unit,
				"created_count": created,
				"skipped_count": skipped,
				"failed_count": failed,
			},
		)
		frappe.db.commit()

	_append_run_log(run_name, lines)

	more = paused or (batch.remaining > 0) or (processed < len(batch.units))
	if more:
		_set_run(run_name, {"status": "Paused" if paused else "Running"})
		_enqueue_run(run_name)
	else:
		_set_run(
			run_name,
			{
				"status": "Completed",
				"finished_at": _now(),
				"planned_count": int(run.get("planned_count") or 0) or len(units),
			},
		)
	return {
		"status": "Paused" if paused else ("Running" if more else "Completed"),
		"processed": processed,
		"created": created,
		"skipped": skipped,
		"failed": failed,
		"cursor": batch.next_cursor if processed else str(run.get("cursor") or ""),
	}


class _PrechargedQuota(ProjectQuota):
	"""A quota whose space-write charge has already been paid by the batch driver.

	Not a mock: :func:`run_provisioning_run` charges the project bucket *before* it decides
	to provision a unit, because a refusal has to pause the run rather than half-provision
	it. Handing the real quota down to :func:`provision_room` would then charge a second
	write for the same space and halve the effective budget — a bug that presents as "the
	sweep is inexplicably slow", which is the hardest kind to notice.
	"""

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		return True


def _plan_only(room: str, unit: str) -> str:
	"""The dry-run branch: resolve the plan, create nothing, report what would happen."""
	row = _room_row(room)
	try:
		plan = build_plan(
			room=room,
			room_type=str(row.get("room_type") or ""),
			title=str(row.get("title") or ""),
			members=_member_rows(room),
			site=frappe.local.site,
			dm_user_1=str(row.get("dm_user_1") or ""),
			dm_user_2=str(row.get("dm_user_2") or ""),
		)
	except ProvisioningRefused as exc:
		return f"{unit}: would REFUSE — {chat_client.scrub_secrets(str(exc))[:200]}"
	return f"{unit}: would create — {plan.as_log_line()}"


def _run_row(run_name: str) -> dict[str, Any]:
	row = frappe.db.get_value(
		"Chat Provisioning Run",
		run_name,
		[
			"name",
			"mode",
			"dry_run",
			"status",
			"cursor",
			"planned_count",
			"created_count",
			"skipped_count",
			"failed_count",
			"started_at",
		],
		as_dict=True,
	)
	return dict(row) if row else {}


def _set_run(run_name: str, values: Mapping[str, Any]) -> None:
	"""Run rows **do** move ``modified``: it is an operator's progress indicator."""
	frappe.db.set_value("Chat Provisioning Run", run_name, dict(values))


def _append_run_log(run_name: str, lines: Iterable[str]) -> None:
	"""Append per-unit decisions. Never raises — logging must not break the sweep it logs."""
	rows = [line for line in lines if line]
	if not rows:
		return
	try:
		current = str(frappe.db.get_value("Chat Provisioning Run", run_name, "log") or "")
		frappe.db.set_value("Chat Provisioning Run", run_name, "log", ("\n".join([current, *rows])).strip())
	except Exception:
		return


def _enqueue_run(run_name: str) -> None:
	frappe.enqueue(
		"erpnext_enhancements.chat.sync.provisioning.run_provisioning_run",
		queue="long",
		run_name=run_name,
		job_id=f"chat-provisioning-run-{run_name}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def resume_provisioning_runs() -> dict[str, Any]:
	"""Scheduler entry: re-drive every run that is not finished.

	The backstop for the failure this whole DocType exists to survive — the prod deploy
	``FLUSHDB``s the queue Redis and restarts the worker group, so a run that only existed as
	an enqueued job is gone with nothing in any log. ``Running`` is included in the resumable
	set on purpose: a worker killed mid-unit leaves the row saying ``Running`` forever, and
	this is what recovers it.
	"""
	names = [
		str(row["name"])
		for row in frappe.get_all(
			"Chat Provisioning Run",
			filters={"status": ["in", ["Pending", "Running", "Paused"]]},
			fields=["name"],
			order_by="creation asc",
			limit_page_length=25,
		)
	]
	for name in names:
		_enqueue_run(name)
	return {"resumed": len(names)}


# --------------------------------------------------------------------------------------
# Mode 3 — per-document spaces, and the orphan sweep
# --------------------------------------------------------------------------------------


@frappe.whitelist()
def create_document_room(
	doctype: str,
	docname: str,
	title: str = "",
	members: str | Sequence[str] = (),
	provision_now: int | bool = 0,
) -> str:
	"""Create a per-document room. **User-initiated, and registered in no hook.**

	Never automatic, and the reason is arithmetic rather than taste: project-wide space
	writes are capped at 60 a minute, so a rule that made a space per Project would saturate
	the entire project's space budget for hours on the first migration and leave thousands of
	empty spaces in every coworker's Chat client. Somebody asks for a room; nothing creates
	one on their behalf.

	Permission is the *document's* permission, checked here rather than inferred. A chat room
	hung off a document is a second, weaker door into knowing that document exists, and the
	one thing it must not do is be easier to open than the document itself.
	"""
	if not _setting_int("document_spaces_enabled", 0):
		frappe.throw(frappe._("Per-document chat rooms are disabled in Chat Settings."))
	if not frappe.has_permission(doctype, "read", docname):
		frappe.throw(frappe._("You do not have permission to read {0} {1}.").format(doctype, docname))

	from erpnext_enhancements.chat.sync import membership

	room, _created = _insert_room_deduped(
		{
			"room_type": ROOM_TYPE_DOCUMENT,
			"title": (title or f"{doctype} {docname}").strip(),
			"linked_doctype": doctype,
			"linked_document": docname,
			"provisioning_mode": PROVISION_ON_FIRST_MESSAGE,
			"provisioning_state": STATE_PENDING,
			"membership_authority": "ERPNext",
		},
		probe={"linked_doctype": doctype, "linked_document": docname},
	)
	if not room:
		raise ProvisioningError(f"Could not create or find a Chat Room for {doctype} {docname}.")

	# Through the membership module's one insert, so this shares the ``unique(room, user)``
	# rule with the reconciler rather than keeping a second, unguarded copy of it. Two people
	# opening the same document at once is the ordinary case here, not the exotic one, and the
	# member rows are seeded in a loop — an insert that raised would leave the room built and
	# its membership half-written.
	for user in {frappe.session.user, *_as_list(members)}:
		if not user or user == "Administrator":
			continue
		membership.insert_room_member(room, user, role="Manager" if user == frappe.session.user else "Member")

	if int(provision_now or 0):
		enqueue_room_provisioning(room)
	return room


def sweep_orphaned_document_rooms(limit: int = DEFAULT_SWEEP_LIMIT) -> dict[str, Any]:
	"""Find per-document rooms whose document is gone or cancelled, and stop mirroring them.

	Scheduled rather than hooked. A ``doc_events`` handler sees ``delete_doc`` and nothing
	else: ``frappe.db.delete``, a bulk delete and a direct SQL cleanup all leave the document
	gone and fire no event. This asks the only question that is reliably true — *does the
	linked document still exist, and is it cancelled* — so it cannot be routed around. The
	cost is up to one cron interval of latency on a room nobody is using any more, which is
	the cheapest thing in this module to spend.

	The action is deliberately **archive, leave, alert** and never ``spaces.delete``. Deleting
	the space would take the conversation with it, and a document being deleted is not
	consent to destroy what people said about it.
	"""
	rooms = frappe.get_all(
		"Chat Room",
		filters={
			"room_type": ROOM_TYPE_DOCUMENT,
			"is_archived": 0,
			"linked_doctype": ["is", "set"],
		},
		fields=["name", "linked_doctype", "linked_document"],
		order_by="modified asc",
		limit_page_length=int(limit),
	)

	orphaned = 0
	for row in rooms:
		doctype = str(row.get("linked_doctype") or "")
		docname = str(row.get("linked_document") or "")
		if not doctype or not docname:
			continue
		reason = _orphan_reason(doctype, docname)
		if not reason:
			continue
		handle_orphaned_room(str(row["name"]), reason=reason)
		orphaned += 1
	return {"inspected": len(rooms), "orphaned": orphaned}


def _orphan_reason(doctype: str, docname: str) -> str:
	"""``""`` when the document is alive and current; otherwise why it is not.

	A missing DocType is treated as *not orphaned*, not as orphaned. An app being uninstalled
	or a DocType being renamed would otherwise archive every room hung off it and make twenty
	people leave twenty spaces over what is a deployment accident.
	"""
	try:
		if not frappe.db.exists("DocType", doctype):
			return ""
		if not frappe.db.exists(doctype, docname):
			return f"{doctype} {docname} no longer exists"
		docstatus = frappe.db.get_value(doctype, docname, "docstatus")
		if docstatus is not None and int(docstatus) == 2:
			return f"{doctype} {docname} is cancelled"
	except Exception:
		# A query failure is not evidence of an orphan. Failing closed here means "do
		# nothing", which is the only safe direction for an irreversible action.
		return ""
	return ""


def handle_orphaned_room(room: str, *, reason: str) -> dict[str, Any]:
	"""Archive the room, leave the space, alert. Idempotent, and it never deletes anything.

	Order matters. The room is archived **first** so that even if leaving the space fails —
	a revoked DWD grant, a Google outage — the room stops accepting relay work immediately;
	the leave is convergent and the next sweep finishes it. Doing it the other way round
	leaves a window where the document is gone, the space is empty, and ERPNext is still
	relaying into it.
	"""
	from erpnext_enhancements.chat.sync import membership

	_set_room(
		room,
		{
			"is_archived": 1,
			"provisioning_mode": PROVISION_NOT_MIRRORED,
			"provisioning_state": STATE_DISABLED,
			"provisioning_error": f"Orphaned: {reason}."[:_ERROR_CHARS],
		},
	)

	left: dict[str, Any] = {"removed": 0, "deferred": 0}
	try:
		left = membership.leave_space(room, reason=reason)
	except Exception as exc:
		_log(f"chat orphan sweep could not leave space for room={room}: {type(exc).__name__}")

	alert(
		f"orphan:{room}",
		"Chat room orphaned by its linked document",
		f"Chat Room {room} was archived because {reason}. Its Google space was NOT deleted — "
		"ERPNext members were removed from it and the conversation history is intact. Delete "
		"the space by hand if that is what is wanted.",
	)
	_log(f"chat orphan sweep archived room={room} reason={reason} removed={left.get('removed')}")
	return {"room": room, "reason": reason, **left}
