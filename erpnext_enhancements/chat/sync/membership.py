# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Membership sync, both directions — a converging diff, and a revert cap so it terminates.

--------------------------------------------------------------------------------------
THE CONSTRAINT THAT SHAPES THIS WHOLE MODULE: a listing carries no email addresses
--------------------------------------------------------------------------------------
Read this before changing anything here. From the Chat v1 discovery document, on the
``User`` resource, verbatim:

    "the user's email address can be used as an alias for ``{user}`` **in API requests**. …
    **Only the canonical resource name (for example ``users/123456789``) will be returned
    from the API.**"

and:

    "When returned as an output from a request, if your Chat app authenticates **as a
    user**, the output for a ``User`` resource only populates the user's ``name`` and
    ``type``."

The relay authenticates as a user. So ``spaces.members.list`` hands back
``spaces/{space}/members/{id}`` with ``member.name = users/{opaque id}`` and **nothing that
can be matched to an ERPNext ``User``** — no email, and under user auth not even a display
name. A diff keyed on addresses is therefore impossible on the Google side, and any code
that appears to do it is reading a field that is empty in production and populated only in a
test harness.

Three consequences, all deliberate:

1. **The diff is keyed on membership resource names ERPNext recorded**, learned from the
   ``members.create`` response. An address is only ever an *input*.
2. **A live membership ERPNext cannot name is reported, never removed.** Whatever
   ``membership_authority`` says. Removing a person you cannot identify is not a
   reconciliation, it is a guess, and re-adding does not give them the space's history back
   in their native client. Those show up as :attr:`MembershipDiff.unknown` and are alerted.
3. **A revert can still remove somebody**, because the inbound Workspace Event names the
   membership resource — see :func:`apply_inbound_membership`. The event supplies exactly the
   identifier the listing withholds, which is why the revert path takes a name and not an
   address.

``VERIFY:`` ``spaces.members.delete`` documents the email alias —
*"You can use the email as an alias for ``{member}``. For example,
``spaces/{space}/members/example@gmail.com``"* — which would let this module address a
member it did not create. **Phase 1's ``client.validate_membership_name`` rejects it**: its
pattern is ``spaces/<id>/members/[A-Za-z0-9_.-]+`` and an address contains ``@``. Widening
that class in ``gchat/client.py`` (not a change this module may make) would collapse rule 2
above from "cannot" to "chooses not to", and is the single highest-value follow-up here.

--------------------------------------------------------------------------------------
ERPNext → Google is a DIFF, never an event replay
--------------------------------------------------------------------------------------
:func:`sync_membership_to_google` computes the desired set from ``Chat Room Member``, reads
what Chat believes, and applies the difference. **A diff is safe to run repeatedly and an
event replay is not**, which matters because reconciliation runs on a schedule, after a
crash, after a deploy that ``FLUSHDB``-ed the queue, and in the middle of a provisioning
job. A diff run twice is a no-op the second time; that is the property every recovery path
in this design needs.

Membership writes consume **no per-space budget** (``PHASE2_VERIFIED.md`` §2, correction 1):
``members.create``/``members.delete`` appear in no per-space row of Google's table. They
charge only the project-wide **300 per minute** membership bucket. Charging them against the
space's single write per second — the intuitive-looking mistake — would reconcile membership
roughly 300× slower than the API allows, and would present as "the sync is inexplicably
slow", never as an error.

--------------------------------------------------------------------------------------
Google → ERPNext is gated on ``Chat Room.membership_authority``
--------------------------------------------------------------------------------------
``Bidirectional`` accepts the change. ``ERPNext`` **reverts** it and records what was
attempted.

A revert is capped, because two systems fighting is a real failure mode. Somebody re-adding
themselves in the native client against a reconciler that keeps removing them is an infinite
loop, and every round trip spends from the 300-per-minute bucket every other room shares. At
``Chat Settings.max_reverts_per_hour`` the room stops reverting and raises an alarm — at that
point the disagreement is a question about the org chart, and no number of retries is an
answer to it.

The window lives in ``Chat Room.reverts_this_hour``/``revert_window_start``, **columns and
not a Redis counter**, deliberately: the prod deploy ``FLUSHDB``s Redis, and a loop-breaker
that resets on every release is not a loop-breaker — the flap would resume the moment a
deploy landed, which is exactly when nobody is watching.

--------------------------------------------------------------------------------------
Leaving: ``left_at`` and ``left_seq`` are STAMPED here, and stamping is not granting
--------------------------------------------------------------------------------------
:func:`stamp_left` writes both. **Nothing reads ``left_seq`` for permission purposes.** CQ-10
— whether a departed member may still read what was said while they were present — is
unanswered, and ``chat/permissions.py`` fails closed on purpose: an inactive member sees
nothing. This module records *when* somebody left so the answer is available on the day the
question is settled. It does not soften the permission rule and must not grow one.

``left_seq`` is a sequence rather than a timestamp for the reason ``Chat Room Member``'s own
field description gives: Frappe writes ``creation``/``modified`` in **site-local** time while
the database clock is UTC, so any SQL-side comparison of ``left_at`` is silently wrong by the
site's offset. The value comes from ``Chat Room.seq_high_water``, the denormalised mirror,
and the direction of that mirror's error is the safe one — a mirror that lags stamps a
*lower* boundary, narrowing the range a departed member could ever be granted.

--------------------------------------------------------------------------------------
Purity
--------------------------------------------------------------------------------------
:func:`diff_membership`, :func:`revert_decision` and :func:`classify_inbound_membership` are
total functions of their arguments and are where the tests bite. The Frappe half is thin glue
over them. ``frappe`` is imported at module scope because nothing here is a ``doc_events``
target — ``tests/test_chat_guardrails.py`` forbids one that can reach the transport, and this
module reaches it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

import frappe

from erpnext_enhancements.chat.gchat import client as chat_client
from erpnext_enhancements.chat.gchat import dryrun
from erpnext_enhancements.chat.seams import COUNTER_REVERTS_APPLIED, bump
from erpnext_enhancements.chat.sync.ratelimit import BUCKET_MEMBERSHIP_WRITES, ProjectQuota

#: ``Chat Room.membership_authority`` values. Stated explicitly on the room, never inferred
#: from whichever side changed last — inferring it is how a membership flaps.
AUTHORITY_BIDIRECTIONAL: Final[str] = "Bidirectional"
AUTHORITY_ERPNEXT: Final[str] = "ERPNext"

#: ``Chat Room Member.gchat_member_state``. ``JOINED`` doubles as the "present in Chat but we
#: never learned the resource name" marker — see :func:`diff_membership`.
MEMBER_JOINED: Final[str] = "JOINED"
MEMBER_INVITED: Final[str] = "INVITED"
MEMBER_NOT_A_MEMBER: Final[str] = "NOT_A_MEMBER"

#: States that mean "this person is in the space as far as Chat is concerned". ``INVITED`` is
#: included: an invited-but-not-joined coworker **is** a membership resource, and treating
#: them as absent makes the diff re-add them on every pass — a 409 forever.
PRESENT_STATES: Final[frozenset[str]] = frozenset({MEMBER_JOINED, MEMBER_INVITED})

#: ``Chat Room Member.sync_state``. The same vocabulary as ``Chat Message.sync_state`` and
#: ``Chat Relay Job.status`` on purpose, so an operator reading three tables during an
#: incident does not have to translate. Note the absence of ``Inbound``: the member row's
#: Select does not carry it, because a membership has no inbound-authored variant.
SYNC_PENDING: Final[str] = "Pending"
SYNC_RELAYED: Final[str] = "Relayed"
SYNC_FAILED: Final[str] = "Failed"
SYNC_NOT_MIRRORED: Final[str] = "Not Mirrored"

#: ``Membership.affiliation`` values that mean this member is outside the Workspace
#: organisation that owns the space. **A per-member data-egress signal, observable on every
#: listing** — strictly better than ``Space.externalUserAllowed``, which only says the space
#: *permits* outsiders. This says one is actually in the room.
EXTERNAL_AFFILIATIONS: Final[frozenset[str]] = frozenset({"EXTERNAL", "MANAGED_EXTERNAL"})

#: One hour, in seconds. The revert window's length. A constant rather than a setting because
#: ``Chat Settings.max_reverts_per_hour`` already names the period in its own fieldname, and
#: two places to change one number is how they end up disagreeing.
REVERT_WINDOW_SECONDS: Final[float] = 3600.0

#: ``spaces.members.list`` pages at 100 by default, 1000 at most. A chat room will never have
#: a thousand members, and pagination is still not optional: a truncated listing is not a
#: smaller truth. The diff would read the missing memberships as ones ERPNext cannot name and
#: alert on all of them, or — worse, before rule 2 above existed — remove them.
_MEMBER_PAGE_SIZE: Final[int] = 500

#: Hard stop on pagination, so a server that always returns a ``nextPageToken`` cannot spin a
#: worker forever.
_MAX_MEMBER_PAGES: Final[int] = 20

_ERROR_CHARS: Final[int] = 1000
_LOGGER_NAME: Final[str] = "chat.sync.membership"


class MembershipError(Exception):
	"""Membership reconciliation failed in a way a later convergent pass may fix."""


# --------------------------------------------------------------------------------------
# Pure: the diff
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MembershipDiff:
	"""What to change to make Chat agree with ERPNext. Four lists, and the fourth is the point.

	``to_add`` carries **emails**; ``to_remove`` and ``unknown`` carry **membership resource
	names**. The asymmetry is Google's: ``members.create`` takes ``users/{email}`` as input
	while a listing only ever returns ``spaces/{space}/members/{opaque id}``.

	``unknown`` is every live membership ERPNext cannot put a name to — somebody a manager
	added in the native client, or a member from before this column existed. They are
	**reported and never removed**; see the module docstring. A diff that silently deleted
	them would be deleting people it could not identify, on the strength of a listing it
	could not read.
	"""

	to_add: tuple[str, ...]
	to_remove: tuple[str, ...]
	unchanged: tuple[str, ...]
	unknown: tuple[str, ...]

	@property
	def converged(self) -> bool:
		"""``unknown`` deliberately does not count against convergence.

		It never shrinks by itself — nothing this module can do resolves an unnameable
		membership — so counting it would make every affected room permanently "not
		converged" and every reconciliation pass permanently noisy, which is how a real
		signal gets muted.
		"""
		return not self.to_add and not self.to_remove


def normalise_email(value: str | None) -> str:
	"""Lower-cased and stripped. The comparison key on the ERPNext side of the diff.

	ERPNext's ``User.name`` is whatever somebody typed. Comparing raw values produces a diff
	that adds a member who is already present and then removes them again — a flap
	indistinguishable from the one the revert cap exists to stop, except that this one would
	be entirely our own fault.
	"""
	return str(value or "").strip().lower()


def diff_membership(
	*,
	desired: Iterable[str],
	known: Mapping[str, str],
	present: Iterable[str] = (),
	live: Iterable[str] = (),
	protect: Iterable[str] = (),
) -> MembershipDiff:
	"""Compute the converging diff. Pure, total, order-stable.

	Args:
		desired: emails ERPNext wants in the space.
		known: ``{email: membership resource name}`` for members ERPNext has bound.
		present: emails ERPNext believes are members but holds **no** resource name for —
			``gchat_member_state`` in :data:`PRESENT_STATES`. Without this the diff would
			re-add every member added by ``spaces.setup`` on every pass forever: setup does
			not return the membership resources it created, so their names are never learned,
			and "no name" would otherwise be indistinguishable from "not a member".
		live: membership resource names currently in the space.
		protect: membership names that must never be removed — the caller's own, above all.
			Deleting the membership we are authenticated as ends the session doing the
			deleting and leaves the space unmanageable from ERPNext.

	Sorted output on every list, because the caller applies them under a rate limit and a
	partially-applied diff must be **deterministic**: the same interruption at the same point
	must leave the same state, or a resumed reconciliation is not reproducible and neither is
	the bug report about it.
	"""
	wanted = {normalise_email(item) for item in desired if normalise_email(item)}
	bound = {normalise_email(key): str(value) for key, value in known.items() if value}
	believed = {normalise_email(item) for item in present if normalise_email(item)}
	live_names = {str(name) for name in live if str(name or "").strip()}
	guarded = {str(name) for name in protect if str(name or "").strip()}

	to_add: list[str] = []
	unchanged: list[str] = []
	for email in sorted(wanted):
		name = bound.get(email)
		if name and name in live_names:
			unchanged.append(email)
		elif name:
			# ERPNext recorded a membership that is no longer in the space: somebody removed
			# them in Chat. Re-adding is the convergent answer under either authority — under
			# ERPNext because that is the revert, and under Bidirectional because the inbound
			# path will have already written the row if Chat's removal was meant to stick.
			to_add.append(email)
		elif email in believed:
			unchanged.append(email)
		else:
			to_add.append(email)

	to_remove = sorted(
		name
		for email, name in bound.items()
		if email not in wanted and name in live_names and name not in guarded
	)
	unknown = sorted(live_names - set(bound.values()))

	return MembershipDiff(
		to_add=tuple(to_add),
		to_remove=tuple(to_remove),
		unchanged=tuple(unchanged),
		unknown=tuple(unknown),
	)


def affiliation_of(membership: Mapping[str, Any] | None) -> str:
	"""``Membership.affiliation``, or ``""``.

	``INTERNAL`` / ``EXTERNAL`` / ``MANAGED_EXTERNAL`` — a Workspace-relationship signal that
	is Output only and, unlike the member's address, **is** populated under user
	authentication. It is the only per-member view of the CQ-12 data-egress question the API
	gives us.
	"""
	return str((membership or {}).get("affiliation") or "").strip()


def external_members(memberships: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
	"""Membership names whose ``affiliation`` puts them outside the owning organisation.

	Reported by name rather than by address, because an address is not available (see the
	module docstring). A name is enough for a human to look the membership up in the native
	client, which is what the alert asks them to do.
	"""
	return tuple(
		sorted(
			str(row.get("name") or "")
			for row in memberships
			if affiliation_of(row) in EXTERNAL_AFFILIATIONS and str(row.get("name") or "")
		)
	)


# --------------------------------------------------------------------------------------
# Pure: the revert rule and its cap
# --------------------------------------------------------------------------------------


class InboundMembershipVerdict(str, Enum):
	"""What to do about a membership change Chat made. Total over the inputs below."""

	#: Nothing to do — the two sides already agree.
	IGNORE = "IGNORE"
	#: ``Bidirectional``: take Chat's word for it and write the ERPNext row.
	ACCEPT = "ACCEPT"
	#: ``ERPNext``: undo it and record what was attempted.
	REVERT = "REVERT"
	#: A revert is wanted and the hourly cap is spent. **Alarm, change nothing.** Two systems
	#: disagreeing about who belongs in a room is a question for a person, not a retry.
	CAPPED = "CAPPED"


def classify_inbound_membership(
	*,
	authority: str,
	in_google: bool,
	in_erpnext: bool,
	revert_allowed: bool,
) -> InboundMembershipVerdict:
	"""The Google → ERPNext decision, as one table with no I/O in it.

	``authority`` is ``Chat Room.membership_authority`` verbatim. Anything that is not
	``Bidirectional`` is read as ``ERPNext`` — the strict direction — because the field is
	``reqd`` with a default, so the only ways to see something else are corruption or a Select
	option added later, and in both cases the safe answer is "do not let Chat drive
	membership".
	"""
	if in_google == in_erpnext:
		return InboundMembershipVerdict.IGNORE
	if str(authority or "").strip() == AUTHORITY_BIDIRECTIONAL:
		return InboundMembershipVerdict.ACCEPT
	return InboundMembershipVerdict.REVERT if revert_allowed else InboundMembershipVerdict.CAPPED


@dataclass(frozen=True)
class RevertDecision:
	"""May this room revert right now, and what the window counters become if it does."""

	allowed: bool
	count: int
	window_start: float
	window_reset: bool
	alarm: bool
	reason: str


def revert_decision(
	*,
	now_epoch: float,
	window_start_epoch: float | None,
	count: int,
	cap: int,
	window_seconds: float = REVERT_WINDOW_SECONDS,
) -> RevertDecision:
	"""The hourly revert budget for one room. Pure; epoch seconds in, epoch seconds out.

	Epoch seconds rather than datetimes so the arithmetic is testable without a clock and
	without a timezone. The Frappe glue converts, and it converts in **one** place — this repo
	has already shipped one bug from comparing ``now_datetime()`` (site-local) against a UTC
	instant, and a loop-breaker that is wrong by the site's offset either never fires or never
	stops.

	``cap <= 0`` disables reverting and says so, rather than reading as "unlimited". A zero
	limit is a configured stop; reading it as no limit is the failure direction that gets an
	API client banned.

	The window is **fixed**, anchored on the first revert of the hour, not sliding. The cap
	exists to break a loop, not to meter throughput, and a fixed window breaks a loop just as
	well while costing one column instead of a list of timestamps.
	"""
	ceiling = int(cap)
	if ceiling <= 0:
		return RevertDecision(
			allowed=False,
			count=int(count),
			window_start=float(window_start_epoch or now_epoch),
			window_reset=False,
			alarm=False,
			reason="reverting is disabled (max_reverts_per_hour <= 0)",
		)

	expired = window_start_epoch is None or (now_epoch - float(window_start_epoch)) >= float(window_seconds)
	if expired:
		return RevertDecision(
			allowed=True,
			count=1,
			window_start=float(now_epoch),
			window_reset=True,
			alarm=False,
			reason="new window",
		)

	seen = max(int(count), 0)
	if seen >= ceiling:
		return RevertDecision(
			allowed=False,
			count=seen,
			window_start=float(window_start_epoch),
			window_reset=False,
			alarm=True,
			reason=f"revert cap reached ({seen}/{ceiling} this hour)",
		)
	return RevertDecision(
		allowed=True,
		count=seen + 1,
		window_start=float(window_start_epoch),
		window_reset=False,
		alarm=False,
		reason=f"within cap ({seen + 1}/{ceiling})",
	)


# --------------------------------------------------------------------------------------
# Frappe: helpers
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
	"""Identifiers only. Never a message body, never a token."""
	logger = _logger()
	if logger is not None:
		try:
			logger.info(message)
		except Exception:
			return


def _setting_int(fieldname: str, default: int) -> int:
	"""``getattr(obj, field, None) or default`` — the house rule, and load-bearing here.

	These reads happen during ``bench migrate`` and ERPNext's own test bootstrap, before this
	app's fields necessarily exist. A missing tuning value degrades to a default; it never
	crashes a worker.
	"""
	try:
		raw = getattr(frappe.get_cached_doc("Chat Settings"), fieldname, None)
	except Exception:
		return int(default)
	try:
		return int(raw) if raw not in (None, "") else int(default)
	except (TypeError, ValueError):
		return int(default)


def _now() -> Any:
	from frappe.utils import now_datetime

	return now_datetime()


def _epoch(value: Any) -> float | None:
	"""A Frappe datetime column as epoch seconds, on **one** consistent scale.

	Both the stored value and ``now`` go through here, so the subtraction in
	:func:`revert_decision` compares two site-local readings and the site's UTC offset
	cancels. Mixing one site-local reading with one UTC reading is the bug this exists to
	make impossible.
	"""
	if value in (None, ""):
		return None
	try:
		from frappe.utils import get_datetime

		return get_datetime(value).timestamp()
	except Exception:
		return None


def _room_row(room: str) -> dict[str, Any]:
	row = frappe.db.get_value(
		"Chat Room",
		room,
		[
			"name",
			"is_archived",
			"membership_authority",
			"mirror_whitelisted",
			"external_users_allowed",
			"gchat_space_name",
			"seq_high_water",
			"reverts_this_hour",
			"revert_window_start",
		],
		as_dict=True,
	)
	return dict(row) if row else {}


def _member_rows(room: str, *, active_only: bool = True) -> list[dict[str, Any]]:
	"""``Chat Room Member`` rows. Operational metadata, never message content.

	``frappe.get_all`` is ``get_list(ignore_permissions=True)`` and that is deliberate and
	bounded: this runs in a background job with no session, and it reads membership. Any read
	of ``Chat Message`` from this module would have to apply
	``permissions.membership_filter_sql`` by hand instead.
	"""
	filters: dict[str, Any] = {"room": room}
	if active_only:
		filters["is_active"] = 1
	return [
		dict(row)
		for row in frappe.get_all(
			"Chat Room Member",
			filters=filters,
			fields=[
				"name",
				"user",
				"role",
				"is_active",
				"gchat_membership_name",
				"gchat_member_state",
				"sync_state",
			],
			order_by="user asc",
			limit_page_length=0,
		)
	]


def _set_member(name: str, values: Mapping[str, Any]) -> None:
	frappe.db.set_value("Chat Room Member", name, dict(values), update_modified=False)


def _set_room(room: str, values: Mapping[str, Any]) -> None:
	frappe.db.set_value("Chat Room", room, dict(values), update_modified=False)


def _relay_client(subject: str) -> chat_client.GoogleChatClient:
	"""User identity, always. ``members.create``/``delete`` under app auth need
	``chat.app.memberships``, a Marketplace admin-install scope this deployment does not hold,
	so an app-identity fallback would only defer a 403 to production."""
	return chat_client.GoogleChatClient(subject=subject, identity=chat_client.AuthIdentity.USER)


def _charge_membership_write(quota: ProjectQuota) -> bool:
	"""Spend one of the project's 300 membership writes per minute.

	**Not** the per-space bucket: membership writes appear in no per-space row at all. A
	refusal is "come back next window", never an error — the diff is convergent, so the
	remainder is applied by the next pass with no state carried between them.
	"""
	limit = _setting_int("project_membership_writes_per_minute", 300)
	return quota.charge(BUCKET_MEMBERSHIP_WRITES, limit=limit)


def _member_email(row: Mapping[str, Any]) -> str:
	"""The Workspace address for a ``Chat Room Member`` row, or ``""`` if it is not one.

	``Chat Room Member.user`` is a Link to ``User``, whose ``name`` is the login — which on
	this deployment is the Workspace address. Validated rather than assumed, because the value
	is interpolated into ``users/{email}``, a **resource name**: a stray ``/`` or a space
	would address something else entirely, and a system user like ``Administrator`` is not a
	Chat member at all.
	"""
	try:
		return chat_client.validate_user_email(str(row.get("user") or ""))
	except ValueError:
		return ""


def desired_member_emails(room: str) -> tuple[str, ...]:
	"""The set ERPNext believes should be in the space.

	Read from ``Chat Room Member``, the one table with an opinion. Materialising those rows
	from a linked document's own permissions (``derived_from_document``) belongs to the room
	lifecycle, not here: a diff engine that also decided *who ought to be a member* would have
	two reasons to change and no way to test either in isolation.
	"""
	return tuple(sorted({email for row in _member_rows(room) if (email := _member_email(row))}))


def list_live_memberships(space: str, client: Any) -> dict[str, dict[str, Any]]:
	"""``{membership resource name: Membership}`` for a space, fully paginated.

	``show_invited=True``: an invited-but-not-joined coworker **is** a membership resource, and
	omitting them makes the diff try to add them again — a 409 on every pass, forever.

	``show_groups`` stays ``False``. A Google Group membership is one resource covering many
	people and there is no ERPNext row that corresponds to it; including them would put a
	permanent entry in :attr:`MembershipDiff.unknown` and train whoever reads the alert to
	ignore it.
	"""
	found: dict[str, dict[str, Any]] = {}
	page_token: str | None = None
	for _page in range(_MAX_MEMBER_PAGES):
		try:
			payload = client.list_members(
				space, page_size=_MEMBER_PAGE_SIZE, page_token=page_token, show_invited=True
			)
		except chat_client.GoogleChatError as exc:
			raise MembershipError(f"spaces.members.list failed for {space}: {exc}") from None

		for entry in payload.get("memberships") or ():
			name = str(entry.get("name") or "")
			if name:
				found[name] = dict(entry)

		page_token = str(payload.get("nextPageToken") or "")
		if not page_token:
			break
	return found


# --------------------------------------------------------------------------------------
# ERPNext -> Google
# --------------------------------------------------------------------------------------


def sync_membership_to_google(
	room: str,
	*,
	client: Any | None = None,
	quota: ProjectQuota | None = None,
	desired: Sequence[str] | None = None,
	protect: Sequence[str] = (),
	apply: bool = True,
) -> dict[str, Any]:
	"""Make Chat's membership match ERPNext's. Safe to run any number of times.

	``apply=False`` computes the diff and changes nothing — the same planning/execution split
	as ``Chat Provisioning Run.dry_run``, and for the same reason: removing somebody from a
	space costs them the history in their native client and re-adding does not give it back.

	``desired`` overrides the ERPNext-derived set and is how :func:`leave_space` reuses this
	with a narrowed set. Everything convergent about the design comes from there being exactly
	one apply path.
	"""
	row = _room_row(room)
	if not row:
		raise MembershipError(f"Chat Room {room} does not exist.")

	space = str(row.get("gchat_space_name") or "")
	if not space or dryrun.is_dryrun_name(space):
		# Nothing to diff against. Not an error: an unprovisioned room has no Google
		# membership yet, and provisioning calls back here once it does.
		return {"room": room, "space": space, "skipped": "unprovisioned"}

	if row.get("external_users_allowed") and not row.get("mirror_whitelisted"):
		# The data-egress gate, seen from the other side. Adding an ERPNext coworker to an
		# external-allowed space is itself a mirroring act.
		return {"room": room, "space": space, "skipped": "external_users_not_whitelisted"}

	members = _member_rows(room)
	# `known` is built from EVERY row, active or not, and that is not tidiness — it is the
	# only way a soft-removed member is ever removed from the space. `Chat Room Member` rows
	# are never deleted (the audit trail has to answer "was this person in the room when that
	# was said"), so deactivating somebody leaves the one record of their membership resource
	# name on an inactive row. Reading only active rows drops that name, the diff then cannot
	# put an identity to their live Google membership, rule 2 of the module docstring quite
	# correctly refuses to remove a membership it cannot name — and the person stays in the
	# space forever. Deactivated in ERPNext, still reading the conversation in Chat.
	bindings = _member_rows(room, active_only=False)
	wanted = tuple(desired) if desired is not None else desired_member_emails(room)
	caller = _caller_for(room, members, wanted)
	api = client or _relay_client(caller)

	live = list_live_memberships(space, api)
	known = {
		email: str(member_row.get("gchat_membership_name") or "")
		for member_row in bindings
		if (email := _member_email(member_row))
	}
	believed = tuple(
		email
		for member_row in bindings
		if (email := _member_email(member_row))
		and str(member_row.get("gchat_member_state") or "") in PRESENT_STATES
	)

	diff = diff_membership(desired=wanted, known=known, present=believed, live=live.keys(), protect=protect)
	outsiders = external_members(live.values())

	result: dict[str, Any] = {
		"room": room,
		"space": space,
		"desired": len(wanted),
		"live": len(live),
		"to_add": list(diff.to_add),
		"to_remove": list(diff.to_remove),
		"unknown": list(diff.unknown),
		"external": list(outsiders),
		"added": 0,
		"removed": 0,
		"deferred": 0,
		"applied": bool(apply),
	}

	if outsiders and not row.get("mirror_whitelisted"):
		_alert_external(room, space, outsiders)
	if diff.unknown:
		_log(f"chat membership unknown room={room} count={len(diff.unknown)}")

	if not apply or diff.converged:
		return result

	budget = quota or ProjectQuota()
	out_of_budget = False

	for email in diff.to_add:
		if not _charge_membership_write(budget):
			# The minute's 300 membership writes are gone. Stop the whole pass, not just this
			# loop: a pass that stopped adding but kept removing would converge towards an
			# empty space for as long as the budget stayed spent.
			out_of_budget = True
			result["deferred"] += 1
			break
		try:
			created = api.create_membership(space, email)
		except chat_client.GoogleChatAPIError as exc:
			if exc.status == 409:
				# Already a member — the listing has changed since the diff, or the membership
				# predates this column. Success, never a retry. Mark them present so the next
				# pass stops trying; the resource name stays unlearnable (module docstring).
				_mark_present(room, email)
				continue
			_mark_member_failed(room, email, exc)
			continue
		except chat_client.GoogleChatError as exc:
			_mark_member_failed(room, email, exc)
			continue
		result["added"] += 1
		_bind_membership(room, email, created)

	for membership_name in () if out_of_budget else diff.to_remove:
		if not _charge_membership_write(budget):
			result["deferred"] += 1
			break
		try:
			api.delete_membership(membership_name)
		except chat_client.GoogleChatAPIError as exc:
			if exc.status != 404:
				_log(f"chat membership delete failed room={room} status={exc.status}")
				continue
			# 404: already gone, which is the desired end state. Convergent.
		except chat_client.GoogleChatError:
			_log(f"chat membership delete failed room={room}")
			continue
		result["removed"] += 1
		_clear_membership(room, membership_name)

	_log(
		f"chat membership synced room={room} added={result['added']} removed={result['removed']} "
		f"deferred={result['deferred']} unknown={len(diff.unknown)}"
	)
	return result


def _caller_for(room: str, members: Sequence[Mapping[str, Any]], desired: Sequence[str]) -> str:
	"""Whose identity the membership writes are made as.

	Managers first, then any desired member, then any active member — deterministic at every
	step, because a retry that picked a different principal is a retry against a different
	permission set.
	"""
	from erpnext_enhancements.chat.sync.provisioning import choose_caller

	caller = choose_caller(members)
	if caller:
		return caller
	if desired:
		return str(desired[0])
	raise MembershipError(
		f"Chat Room {room} has no member to act as. Membership writes are user-authenticated, "
		"so there is always a human making them."
	)


# --------------------------------------------------------------------------------------
# Frappe: the ONE place a Chat Room Member row is created
# --------------------------------------------------------------------------------------


def _like_literal(value: str) -> str:
	"""An address as a ``like`` pattern that matches only itself, casing aside.

	``like`` with no wildcard is an equality **under the column's collation**, which is the
	cheapest case-insensitive comparison the database offers. But ``_`` is a ``like`` wildcard
	and it is legal in an email local part, so an unescaped ``alice_b@…`` would also match
	``aliceXb@…`` — resolving one coworker's membership onto another coworker's account. Escaped,
	the pattern matches exactly one string.
	"""
	return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_user(email: str) -> str:
	"""The ERPNext ``User.name`` an address names, resolved **once** and case-insensitively.

	``Chat Room Member.user`` is a Link to ``User``, so the value ``unique(room, user)`` compares
	is the ``User`` row's *name* — whatever was typed when the account was made. An address
	reaching this module has usually been lower-cased by :func:`normalise_email` on its way out
	of :func:`diff_membership`, so the two are not reliably the same string. Resolving once and
	using the result for **both** the existence probe and the insert is what makes the guard and
	the index agree; a guard that looks up a different key than the one the index enforces is not
	a guard, it is a coin toss that usually lands the right way up.

	``""`` means there is no ERPNext account for this address. That is a real and expected state
	— §4.H's Chat member from outside the roster — and it is audited by the caller, never
	invented: a member row whose ``user`` names no ``User`` is a broken Link that every later
	join silently drops, and in real Frappe the Link validation raises out of the middle of the
	pass.
	"""
	target = normalise_email(email)
	if not target:
		return ""
	# Direct first. A Frappe `name` column is a case-insensitive collation, so in production
	# this already matches `Alice@Example.com`; it is one indexed primary-key lookup.
	found = frappe.db.get_value("User", target, "name")
	if found:
		return str(found)
	# The explicit half, for a collation — or a stricter store — that compares bytes. Only
	# reached on a miss, so the common path stays a single lookup.
	found = frappe.db.get_value("User", {"name": ("like", _like_literal(target))}, "name")
	return str(found) if found else ""


def duplicate_errors() -> tuple[type[BaseException], ...]:
	"""The two exception classes a unique collision can raise. **Both** are needed.

	``frappe.DuplicateEntryError`` is raised only for a **primary-key** collision — a duplicate
	``name``. Every *other* unique index, which is exactly what ``unique(room, user)`` and
	``unique(linked_doctype, linked_document)`` are, raises ``frappe.UniqueValidationError``.
	They share no base beyond ``Exception`` (``DuplicateEntryError(NameError)`` versus
	``UniqueValidationError(ValidationError)``), so catching the first alone — which the ADR
	instructs — would not catch the collision the design rests on. ``PHASE2_VERIFIED.md`` §1.1.

	Resolved through ``getattr`` rather than named directly so a Frappe version missing one of
	them degrades to catching the other instead of failing at import.
	"""
	found = [
		getattr(frappe, "DuplicateEntryError", None),
		getattr(frappe, "UniqueValidationError", None),
	]
	classes = tuple(cls for cls in found if isinstance(cls, type) and issubclass(cls, BaseException))
	return classes or (Exception,)


def savepoint_catching_duplicates() -> Any:
	"""``frappe.database.database.savepoint`` armed with both duplicate classes.

	The savepoint is **mandatory, not hygiene**: a failed statement can poison the surrounding
	transaction, and everything after the insert in a reconciliation pass — the bindings, the
	window counters, the audit rows — happens in that transaction.

	``sync/inbound.py`` exports an identical pair publicly and this is deliberately a second
	copy rather than an import of it: ``inbound`` is the *consumer* of this module's decisions —
	:func:`apply_inbound_membership` is called from there — so importing it back would close a
	cycle, and dragging the whole ingest pipeline in to reach a two-line context manager is not
	a trade worth making. Shared instead with ``sync/provisioning.py``, which imports these two
	function-locally for the same reason.
	"""
	from frappe.database.database import savepoint

	return savepoint(catch=duplicate_errors())


def clear_expected_duplicate_message() -> None:
	"""Drop the *"must be unique"* ``msgprint`` Frappe emits **before** it raises.

	``show_unique_validation_message`` calls ``frappe.msgprint`` and only then raises, so an
	*expected* duplicate — which on this path is a success — otherwise leaves a user-visible
	validation banner behind for an outcome the design wanted. Never raises: a cosmetic cleanup
	that can fail the work it tidies up after is worse than the banner.
	"""
	try:
		clear = getattr(frappe, "clear_messages", None)
		if callable(clear):
			clear()
			return
		message_log = getattr(frappe.local, "message_log", None)
		if isinstance(message_log, list):
			message_log.clear()
	except Exception:
		return


def insert_room_member(
	room: str,
	email: str,
	*,
	role: str = "Member",
	values: Mapping[str, Any] | None = None,
) -> str:
	"""Add one person to one room. **A ``unique(room, user)`` collision is SUCCESS, not an error.**

	The single place a ``Chat Room Member`` row is created, so the rule has one copy —
	``sync/provisioning.py``'s per-document rooms come through here too.

	Two separate bugs live behind one symptom, and both are closed here:

	* **The check-then-act.** ``if member: return`` in front of an unguarded ``insert()`` is a
	  TOCTOU window, and membership reconciliation is precisely the workload that runs it twice
	  at once: a scheduled pass, a provisioning hand-off and an at-least-once inbound event can
	  all reach one room in the same second. The loser of that race used to raise out of the
	  middle of a pass and take the rest of the pass with it. **A converging diff must be safe
	  to run repeatedly — that is the whole reason it is a diff and not an event replay** — so
	  the collision is reported as what it means: the member is already there, which is exactly
	  what the caller wanted.
	* **The key.** See :func:`resolve_user`. The probe and the insert now use one resolved
	  ``User`` name rather than whichever spelling of the address happened to arrive.

	``insert(ignore_if_duplicate=True)`` is not an alternative: it covers only the primary-key
	branch, and this index is not the primary key.

	Returns the row name — the existing one, the new one, or the race winner's — and ``""`` when
	the address names no ERPNext ``User``, which the caller audits.
	"""
	user = resolve_user(email)
	if not user:
		return ""

	existing = _member_row_name(room, user)
	if existing:
		return existing

	row: dict[str, Any] = {
		"doctype": "Chat Room Member",
		"room": room,
		"user": user,
		"role": role,
		"is_active": 1,
		"joined_at": _now(),
	}
	row.update(dict(values or {}))

	with savepoint_catching_duplicates():
		doc = frappe.get_doc(row)
		doc.insert(ignore_permissions=True)
		return str(doc.name)

	# Only reachable when the savepoint swallowed a collision, i.e. somebody committed the same
	# (room, user) between the probe above and this insert. Read back the winner's row and carry
	# on: the pass has more work to do and none of it is invalidated by having lost this one.
	clear_expected_duplicate_message()
	return _member_row_name(room, user)


def _member_row_name(room: str, user: str) -> str:
	"""The ``Chat Room Member`` row for one person in one room, matched the way the index matches.

	Case-insensitively, and that is the repair rather than a nicety: the address reaching most
	callers here came out of :func:`diff_membership` lower-cased, while the row holds the ``User``
	link's own name. Probing one casing and writing the other is how a binding silently lands on
	no row at all — and :func:`_bind_membership` is the **only** moment a Google membership
	resource name is ever available, so a probe that misses there loses it permanently.
	"""
	found = frappe.db.get_value(
		"Chat Room Member", {"room": room, "user": ("like", _like_literal(user))}, "name"
	)
	return str(found or "")


def _bind_membership(room: str, email: str, resource: Mapping[str, Any]) -> None:
	"""Record the Google membership resource name against the ERPNext row.

	**This is the only moment the name is ever available.** A listing does not carry an
	address, so it cannot be matched back to a person later; if the create response is not
	captured here, that membership is unnameable — and therefore unremovable by ERPNext —
	forever.
	"""
	name = _member_row_name(room, email)
	if not name:
		return
	_set_member(
		name,
		{
			"gchat_membership_name": str(resource.get("name") or ""),
			"gchat_member_state": str(resource.get("state") or MEMBER_JOINED),
			"sync_state": SYNC_RELAYED,
		},
	)


def _mark_present(room: str, email: str) -> None:
	"""Record "Chat says they are already in" without a resource name.

	The convergence fix for a 409 and for every member added by ``spaces.setup``: setup does
	not return the membership resources it created, so their names are never learned, and
	without this marker "no name" is indistinguishable from "not a member" and the diff
	re-adds them on every pass forever.
	"""
	name = _member_row_name(room, email)
	if name:
		_set_member(name, {"gchat_member_state": MEMBER_JOINED, "sync_state": SYNC_RELAYED})


def mark_setup_members_present(room: str, emails: Iterable[str]) -> None:
	"""Called by provisioning after ``spaces.setup`` succeeded with these memberships.

	Google accepted them and created the space with them, so they are members — we simply
	never saw their resource names. Recording that here is what makes the *first*
	reconciliation pass on a freshly provisioned room issue **zero** membership writes instead
	of one 409 per member.
	"""
	for email in emails:
		if email:
			_mark_present(room, email)


def _clear_membership(room: str, membership_name: str) -> None:
	"""Detach a removed Google membership from its ERPNext row."""
	name = frappe.db.get_value(
		"Chat Room Member", {"room": room, "gchat_membership_name": membership_name}, "name"
	)
	if not name:
		return
	_set_member(str(name), {"gchat_membership_name": "", "gchat_member_state": MEMBER_NOT_A_MEMBER})


def _mark_member_failed(room: str, email: str, exc: BaseException) -> None:
	name = _member_row_name(room, email)
	detail = chat_client.scrub_secrets(f"{type(exc).__name__}: {exc}")[:_ERROR_CHARS]
	_log(f"chat membership add failed room={room} error={detail[:200]}")
	if name:
		_set_member(name, {"sync_state": SYNC_FAILED})


def _alert_external(room: str, space: str, outsiders: Sequence[str]) -> None:
	_audit(
		room,
		"External members in a mirrored space",
		f"{len(outsiders)} membership(s) in {space} have an affiliation outside the Workspace "
		f"organisation that owns it: {', '.join(outsiders[:10])}. ERPNext conversation is being "
		"mirrored into a space an outsider can read. Tick Mirror Whitelisted if that is intended, "
		"or remove them in the Chat client.",
	)


def leave_space(room: str, *, client: Any | None = None, reason: str = "") -> dict[str, Any]:
	"""Remove every ERPNext member from the space. **Never deletes the space.**

	What an orphaned room does. ``spaces.delete`` would take the conversation with it and the
	``chat.delete`` scopes are deliberately not granted in V1; a document being deleted is not
	consent to destroy what people said about it. So ERPNext leaves, the space and its history
	remain, and a human deletes it by hand if that is really what is wanted.

	**Two passes, and the order is the point.** The first removes everyone except the
	impersonated caller; the second removes the caller. Doing it in one pass can delete the
	membership we are authenticated as while there are still deletes to make, and the
	remainder then 403s — leaving a half-emptied space and a room that says it left.

	Implemented on top of :func:`sync_membership_to_google` so it inherits convergence, the
	300-per-minute budget and the protected-caller rule for free, and so there is exactly one
	code path that can remove somebody from a space.
	"""
	members = _member_rows(room)
	caller = _caller_for(room, members, ())
	caller_membership = next(
		(
			str(row.get("gchat_membership_name") or "")
			for row in members
			if _member_email(row) == normalise_email(caller) or str(row.get("user") or "") == caller
		),
		"",
	)

	result = sync_membership_to_google(
		room,
		client=client,
		desired=(caller,),
		protect=(caller_membership,) if caller_membership else (),
		apply=True,
	)

	if caller_membership:
		second = sync_membership_to_google(room, client=client, desired=(), apply=True)
		result["removed"] = int(result.get("removed") or 0) + int(second.get("removed") or 0)
		result["deferred"] = int(result.get("deferred") or 0) + int(second.get("deferred") or 0)

	for row in members:
		stamp_left(room, str(row.get("user") or ""), note=reason)
	return result


# --------------------------------------------------------------------------------------
# Leaving: the stamp, and only the stamp
# --------------------------------------------------------------------------------------


def stamp_left(room: str, user: str, *, seq: int | None = None, note: str = "") -> int:
	"""Record that ``user`` left ``room`` at the room's current sequence position.

	**This grants nothing.** Nothing in this codebase reads ``left_seq`` to decide what a
	departed member may see: CQ-10 — whether somebody who left keeps access to what was said
	while they were there — is unanswered, and ``chat/permissions.py`` fails closed, so an
	inactive member sees nothing at all. The stamp exists so the answer is *available* on the
	day the question is settled. Do not add a permission rule that reads it, here or anywhere;
	stamping is not granting.

	``seq`` defaults to ``Chat Room.seq_high_water``, the denormalised mirror. That field's own
	description warns it is advisory and must never be used to **allocate** a sequence — this
	is not an allocation, it is a boundary, and the direction of the mirror's error is the safe
	one: a mirror that lags stamps a *lower* boundary, narrowing the range a departed member
	could ever be granted rather than widening it.
	"""
	name = _member_row_name(room, user)
	if not name:
		return 0
	tail = int(seq if seq is not None else (frappe.db.get_value("Chat Room", room, "seq_high_water") or 0))
	_set_member(
		name,
		{
			"is_active": 0,
			"left_at": _now(),
			"left_seq": tail,
			"gchat_member_state": MEMBER_NOT_A_MEMBER,
			"sync_state": SYNC_NOT_MIRRORED,
		},
	)
	_log(f"chat membership stamped left room={room} user={user} left_seq={tail} note={note[:80]}")
	return tail


# --------------------------------------------------------------------------------------
# Google -> ERPNext
# --------------------------------------------------------------------------------------


def apply_inbound_membership(
	room: str,
	*,
	in_google: bool,
	membership_name: str = "",
	email: str = "",
	client: Any | None = None,
) -> str:
	"""Handle one membership change Chat made. Returns the :class:`InboundMembershipVerdict`.

	**``membership_name`` is what makes this work at all.** The Workspace Event names the
	membership resource, which is precisely the identifier ``spaces.members.list`` withholds —
	so an inbound event can be matched to an ERPNext row (by the stored
	``gchat_membership_name``) even though a listing cannot, and a revert can delete exactly
	the membership that was added.

	One member at a time, because that is the granularity an event arrives at, and because the
	revert budget is spent per *change*: a pass that reverted five people and counted one would
	let a five-way flap run five times as long as the cap intends.
	"""
	row = _room_row(room)
	if not row:
		raise MembershipError(f"Chat Room {room} does not exist.")

	member = _resolve_member(room, membership_name=membership_name, email=email)
	in_erpnext = bool(member and member.get("is_active"))

	authority = str(row.get("membership_authority") or AUTHORITY_ERPNEXT)
	budget: RevertDecision | None = _revert_allowed(row) if authority != AUTHORITY_BIDIRECTIONAL else None

	verdict = classify_inbound_membership(
		authority=authority,
		in_google=in_google,
		in_erpnext=in_erpnext,
		revert_allowed=bool(budget is not None and budget.allowed),
	)
	who = str((member or {}).get("user") or "") or membership_name or email or "an unidentified member"

	if verdict is InboundMembershipVerdict.IGNORE:
		return verdict.value

	if verdict is InboundMembershipVerdict.ACCEPT:
		_accept_inbound(
			room, member=member, in_google=in_google, membership_name=membership_name, email=email
		)
		return verdict.value

	if verdict is InboundMembershipVerdict.CAPPED:
		_audit(
			room,
			"Membership revert suppressed by the hourly cap",
			f"{who} was {'added to' if in_google else 'removed from'} the Google space and ERPNext "
			f"is authoritative, but the room has reached its revert cap "
			f"({budget.reason if budget else 'cap reached'}). No change was made. Two systems "
			"disagreeing about who belongs in a room is a question about the org, not a retry — "
			"resolve it by hand, or set Membership Authority to Bidirectional.",
		)
		return verdict.value

	# REVERT. Spend the budget first: the *attempt* is what burns Google's membership quota,
	# so an exception in the apply below must still cost a revert or the cap cannot bound a
	# failing flap.
	_commit_revert(room, budget)
	_audit(
		room,
		"Membership change reverted",
		f"{who} was {'added to' if in_google else 'removed from'} the Google space outside ERPNext. "
		f"Membership Authority is ERPNext, so the ERPNext-derived set was re-applied. "
		f"{budget.reason if budget else ''}",
	)
	bump(COUNTER_REVERTS_APPLIED)
	_revert(room, member=member, in_google=in_google, membership_name=membership_name, client=client)
	return verdict.value


def _resolve_member(room: str, *, membership_name: str, email: str) -> dict[str, Any] | None:
	"""The ``Chat Room Member`` row an inbound event refers to, by name then by address.

	The resource name is tried first because it is the identifier Google actually supplies and
	is unambiguous. The address is a fallback for callers that resolved it some other way — an
	admin action, a manual repair — and is never derived from the API, which does not return
	one.

	The address branch matches case-insensitively, for the reason :func:`_member_row_name` gives:
	the row is keyed on the ``User`` link's name and the address is whatever the caller held. A
	miss here does not merely skip an update — it sends the caller down the *insert* path for a
	member who is already present, which the unique index then refuses.
	"""
	if membership_name:
		row = frappe.db.get_value(
			"Chat Room Member",
			{"room": room, "gchat_membership_name": membership_name},
			["name", "user", "is_active", "gchat_membership_name"],
			as_dict=True,
		)
		if row:
			return dict(row)
	if email:
		row = frappe.db.get_value(
			"Chat Room Member",
			{"room": room, "user": ("like", _like_literal(email))},
			["name", "user", "is_active", "gchat_membership_name"],
			as_dict=True,
		)
		if row:
			return dict(row)
	return None


def _accept_inbound(
	room: str,
	*,
	member: Mapping[str, Any] | None,
	in_google: bool,
	membership_name: str,
	email: str,
) -> None:
	"""``Bidirectional``: write Chat's answer into ``Chat Room Member``.

	A member who leaves is **soft-removed** — ``is_active = 0`` plus a ``left_at``/``left_seq``
	stamp — never row-deleted. The audit trail has to answer "was this person in the room when
	that was said", and a deleted row cannot.

	A member who *joined* and whom ERPNext cannot identify is **audited, not invented**. The
	API returns no address (module docstring), so there is no ``User`` to link a row to, and a
	row keyed on an opaque ``users/{id}`` would be a row nothing else in the system can join
	against. When a caller did supply an address, :func:`insert_room_member` resolves it onto
	the ``User`` row's own name before writing — the Link's value is the ``User``'s name, not
	the spelling that happened to arrive — and refuses when it names no account, which is the
	second, quieter half of "audited, not invented".
	"""
	if not in_google:
		if member:
			stamp_left(room, str(member.get("user") or ""), note="left in Google Chat (Bidirectional)")
		return

	if member:
		_set_member(
			str(member["name"]),
			{
				"is_active": 1,
				"left_at": None,
				"gchat_membership_name": membership_name or str(member.get("gchat_membership_name") or ""),
				"gchat_member_state": MEMBER_JOINED,
				"sync_state": SYNC_RELAYED,
			},
		)
		return

	if not email:
		_audit(
			room,
			"Unidentified member joined a mirrored space",
			f"{membership_name or 'a membership'} was added to the space by somebody outside "
			"ERPNext. Membership Authority is Bidirectional, but the Chat API returns no email "
			"address for a membership, so no Chat Room Member row could be created. Add them in "
			"ERPNext if they belong here.",
		)
		return

	created = insert_room_member(
		room,
		email,
		values={
			"gchat_membership_name": membership_name,
			"gchat_member_state": MEMBER_JOINED,
			"sync_state": SYNC_RELAYED,
		},
	)
	if not created:
		_audit(
			room,
			"Member joined a mirrored space with no ERPNext account",
			f"{membership_name or 'a membership'} was added to the space and resolves to an "
			"address with no ERPNext account, so no Chat Room Member row was created. Membership "
			"Authority is Bidirectional, but a member row must Link to a real User — one that "
			"does not is dropped by every later join and refused by the database. Create the "
			"user in ERPNext if they belong here.",
		)


def _revert(
	room: str,
	*,
	member: Mapping[str, Any] | None,
	in_google: bool,
	membership_name: str,
	client: Any | None,
) -> None:
	"""Undo one membership change, using the identifier the event supplied.

	Someone **added**: delete that exact membership. This is the one removal path that can act
	on a member ERPNext never created, because the event named the resource.

	Someone **removed**: re-add them from the ERPNext row's address and let the convergent diff
	do it, so the create response is captured and the resource name is re-learned.
	"""
	space = str(frappe.db.get_value("Chat Room", room, "gchat_space_name") or "")
	if not space or dryrun.is_dryrun_name(space):
		return

	if in_google and not member:
		api = client or _relay_client(_caller_for(room, _member_rows(room), ()))
		try:
			api.delete_membership(membership_name)
		except chat_client.GoogleChatAPIError as exc:
			if exc.status != 404:
				_log(f"chat membership revert delete failed room={room} status={exc.status}")
		except chat_client.GoogleChatError:
			_log(f"chat membership revert delete failed room={room}")
		return

	if in_google and member and not member.get("is_active"):
		# An inactive ERPNext row plus a live Google membership: they were re-added in Chat
		# after leaving here. Detach the row from the resource so the diff removes it.
		_set_member(str(member["name"]), {"gchat_membership_name": membership_name})

	sync_membership_to_google(room, client=client)


def _revert_allowed(row: Mapping[str, Any]) -> RevertDecision:
	"""Evaluate the room's revert budget **without spending it**.

	Split from :func:`_commit_revert` because the verdict is computed before it is known
	whether a revert is even wanted; charging the window during classification would let a
	room whose membership already agrees burn its own budget by being looked at.
	"""
	from frappe.utils import get_datetime

	now_epoch = get_datetime(_now()).timestamp()
	return revert_decision(
		now_epoch=now_epoch,
		window_start_epoch=_epoch(row.get("revert_window_start")),
		count=int(row.get("reverts_this_hour") or 0),
		cap=_setting_int("max_reverts_per_hour", 5),
	)


def _commit_revert(room: str, decision: RevertDecision | None) -> None:
	"""Persist the window counters. Columns, not Redis — see the module docstring."""
	if decision is None:
		return
	values: dict[str, Any] = {"reverts_this_hour": int(decision.count)}
	if decision.window_reset:
		values["revert_window_start"] = _now()
	_set_room(room, values)


def _audit(room: str, title: str, message: str) -> None:
	"""The audit row for an attempted or refused membership change.

	A ``Comment`` of type ``Info`` on the room — Frappe's own audit surface. It lands in the
	room's timeline, it is queryable by ``reference_doctype``/``reference_name``, and it needs
	no new DocType: a table nobody has written a permission story for is a worse place to keep
	this than one the framework already governs.

	Plus a throttled Error Log, because a capped revert is a condition somebody has to act on
	and a timeline comment on a room nobody opens is not a page. Never raises: an audit path
	that can fail the reconciliation it audits is worse than a missing line.
	"""
	body = chat_client.scrub_secrets(message)[:_ERROR_CHARS]
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Chat Room",
				"reference_name": room,
				"content": f"{title}: {body}",
			}
		).insert(ignore_permissions=True)
	except Exception:
		pass
	try:
		from erpnext_enhancements.chat.sync.provisioning import alert

		alert(f"membership:{room}:{title}", f"Chat: {title}", f"Chat Room {room}. {body}")
	except Exception:
		return


def reconcile_membership_job(room: str) -> None:
	"""Background entry point for one room's membership pass.

	Catches everything and **does not re-raise**, for two compounding reasons. A bare re-raise
	out of a background job publishes the failing frames' locals into the Error Log, and the
	frames below hold an ``Authorization: Bearer`` header. And a ``rollback``/``log_error``
	inside the ``except`` re-raises on its own when the database connection is the thing that
	died, so the job aborts with an *empty* Error Log — a worse failure than the failure. The
	diff is convergent, so swallowing costs one pass and the next one finishes the work.
	"""
	try:
		sync_membership_to_google(room)
	except Exception as exc:
		detail = chat_client.scrub_secrets(f"{type(exc).__name__}: {exc}")[:200]
		_log(f"chat membership reconcile failed room={room} error={detail}")


def enqueue_membership_reconcile(room: str, *, queue: str = "long") -> bool:
	"""Queue :func:`reconcile_membership_job`, deduplicated per room.

	``deduplicate=True`` requires ``job_id`` on v16 and throws without it, and it skips a new
	enqueue while an existing job is ``STARTED`` as well as ``QUEUED`` — the behaviour wanted
	here, since two concurrent diffs of one space would each act on a listing the other is
	invalidating.
	"""
	target = (room or "").strip()
	if not target:
		return False
	frappe.enqueue(
		"erpnext_enhancements.chat.sync.membership.reconcile_membership_job",
		queue=queue,
		room=target,
		job_id=f"chat-membership-{target}",
		deduplicate=True,
		enqueue_after_commit=True,
	)
	return True
