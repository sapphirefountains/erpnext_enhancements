# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bookkeeping for the Google Workspace Events subscriptions that feed inbound sync.

**The fact that makes this table exist: an expired subscription is permanently deleted and
cannot be renewed.** Google's wording is unambiguous — after a subscription expires the Workspace
Events API *permanently deletes* it, and you can neither renew nor reactivate it. Recovery is not
``subscriptions.patch``; it is ``subscriptions.create`` of a brand-new subscription. So expiry is
not a status a renewal loop can recover from after the fact — it is a deadline, and a deadline has
to be tracked as first-class state that somebody watches. A renewal job that only ever patches
will never notice that there is nothing left to patch, and the failure is silent: inbound sync
simply stops, in full, with no error raised anywhere and no row to show for it.

Everything else in this table falls out of that:

* ``expire_time`` is **read back from Google** after every create and every patch, never computed
  locally. ``ttl`` is input-only and asking for ``0`` means "the maximum", so the authoritative
  expiry only ever comes back on the response. A patch that silently did not extend is otherwise
  invisible until the subscription is gone.
* Renewal runs **hourly and a full day early**, and the expiry-reminder CloudEvents Google sends
  12 hours and 1 hour out are the backstop, not the trigger. A design whose renewal depends on
  receiving a push is a design that fails exactly when push is broken.
* ``state`` and ``suspension_reason`` exist because **suspension is a different failure from
  expiry and does not pause the clock** — a reactivated subscription keeps its original expiry.
  ``USER_SCOPE_REVOKED`` in particular is a person, not a bug: they revoked their grant, and the
  answer is to stop retrying, name them in the alarm, and let a human ask.
* ``consecutive_failures`` is what separates "renewal is failing" from "this one person's grant is
  gone" — the two alarms have two different causes and two different owners.

Five alarms are computed over these rows by
:func:`erpnext_enhancements.chat.sync.subscriptions.check_subscription_health`:
not-ACTIVE, expiring-and-not-renewed, consecutive renewal failures, an ACTIVE subscription that
has never delivered an event — and the one that needs no row at all to fire, and is the reason
the roster is checked rather than the table: a coworker with **no** ``ACTIVE`` subscription.

**The controller holds no behaviour that talks to Google.** Creating, renewing, reactivating and
recreating live in :mod:`erpnext_enhancements.chat.sync.subscriptions`; the renewal scheduler is
``renew_due_subscriptions``. Everything here is validation and derivation over the row itself —
a document event that reached the Workspace Events API would put an HTTP call inside a database
transaction, which ``tests/test_chat_guardrails.py`` exists to forbid.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``state`` values. Google's ``Subscription.state`` verbatim; there is deliberately no local
#: ``Expired`` value, because Google does not keep expired subscriptions in any state.
STATE_UNSPECIFIED: Final[str] = "STATE_UNSPECIFIED"
STATE_ACTIVE: Final[str] = "ACTIVE"
STATE_SUSPENDED: Final[str] = "SUSPENDED"
STATE_DELETED: Final[str] = "DELETED"

#: ``suspensionReason`` values, quoted from Google's reference.
SUSPENSION_REASONS: Final[tuple[str, ...]] = (
	"USER_SCOPE_REVOKED",
	"RESOURCE_DELETED",
	"USER_AUTHORIZATION_FAILURE",
	"ENDPOINT_PERMISSION_DENIED",
	"ENDPOINT_NOT_FOUND",
	"ENDPOINT_RESOURCE_EXHAUSTED",
	"APP_SCOPE_REVOKED",
	"APP_AUTHORIZATION_FAILURE",
)

#: A revoked grant is a human decision, not a transient error. Retrying it burns quota and hides
#: the real signal, which is that one named person has to be asked.
NON_RETRYABLE_SUSPENSION_REASONS: Final[tuple[str, ...]] = (
	"USER_SCOPE_REVOKED",
	"APP_SCOPE_REVOKED",
)


#: Every state a live row may hold. ``DELETED`` is ours, not Google's: a subscription that
#: expired no longer exists anywhere at Google, and the row is kept as the record of the gap
#: the reconciliation sweep then has to cover.
KNOWN_STATES: Final[tuple[str, ...]] = (
	STATE_UNSPECIFIED,
	STATE_ACTIVE,
	STATE_SUSPENDED,
	STATE_DELETED,
)


def resource_name(subscription_uid: str) -> str:
	"""``abc123`` ➜ ``subscriptions/abc123``, the address every Workspace Events call needs.

	``subscription_uid`` stores the **id segment of the resource name**, because ``patch``,
	``reactivate``, ``get`` and ``delete`` all address the subscription by name and a renewal
	that cannot name its subscription is not a renewal. See
	:func:`erpnext_enhancements.chat.sync.subscriptions.subscription_uid_of` for the ``VERIFY:``
	on whether Google's ``Subscription.uid`` is the same string.
	"""
	candidate = str(subscription_uid or "").strip()
	if not candidate:
		return ""
	if candidate.startswith("subscriptions/"):
		return candidate
	return f"subscriptions/{candidate}"


class ChatEventSubscription(Document):
	"""One Workspace Events subscription's health record.

	Validation only, and each rule exists because its absence is silent:

	* an unknown ``state`` is normalised to ``STATE_UNSPECIFIED`` rather than stored, so the
	  health report's "not ACTIVE" branch cannot be bypassed by a typo;
	* ``suspension_reason`` is cleared whenever the state is not ``SUSPENDED``, so a stale
	  ``USER_SCOPE_REVOKED`` cannot keep a reactivated subscription in the do-not-retry branch
	  forever;
	* ``renew_after`` after ``expire_time`` is refused — that is a renewal scheduled for after
	  the subscription has already been permanently deleted, which is the exact failure this
	  whole DocType exists to prevent, and it is invisible until it is too late;
	* counters cannot go negative.
	"""

	def validate(self) -> None:
		# getattr(..., None) or "" throughout: doc_events and validate both fire during
		# ERPNext's own test bootstrap, before this app's fields necessarily exist.
		state = (getattr(self, "state", None) or "").strip() or STATE_UNSPECIFIED
		if state not in KNOWN_STATES:
			state = STATE_UNSPECIFIED
		self.state = state

		if state != STATE_SUSPENDED:
			self.suspension_reason = ""

		self.consecutive_failures = max(0, _int_or_zero(getattr(self, "consecutive_failures", 0)))
		self.event_count = max(0, _int_or_zero(getattr(self, "event_count", 0)))

		expire_time = _as_datetime(getattr(self, "expire_time", None))
		renew_after = _as_datetime(getattr(self, "renew_after", None))
		if expire_time and renew_after and renew_after > expire_time:
			frappe.throw(
				frappe._(
					"Renew After ({0}) is later than Expire Time ({1}). Google permanently deletes an "
					"expired subscription — it cannot be renewed, only recreated — so a renewal "
					"scheduled after the expiry never runs and inbound sync stops with no error "
					"anywhere. Derive Renew After from the expireTime Google returned, not from a "
					"configured TTL."
				).format(renew_after, expire_time)
			)

	@property
	def resource_name(self) -> str:
		"""``subscriptions/{id}`` — what a Workspace Events call has to be given."""
		return resource_name(getattr(self, "subscription_uid", None) or "")

	@property
	def is_retryable_suspension(self) -> bool:
		"""Is this suspension worth another call, or is it a person who has to be asked?

		``USER_SCOPE_REVOKED`` and ``APP_SCOPE_REVOKED`` are decisions somebody made in their
		own Google account. Retrying them burns the per-user quota and buries the one signal
		that matters, which is a named human's name.
		"""
		if (getattr(self, "state", None) or "") != STATE_SUSPENDED:
			return False
		return (getattr(self, "suspension_reason", None) or "") not in NON_RETRYABLE_SUSPENSION_REASONS


def _int_or_zero(value: object) -> int:
	try:
		return int(value)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return 0


def _as_datetime(value: object):
	"""A Frappe Datetime field is a ``datetime`` or a string depending on how it was set.

	Parsed rather than string-compared, because the two forms sort differently the moment one
	of them carries microseconds and the other does not — and this comparison guards the one
	invariant that, when it breaks, breaks silently.
	"""
	if not value:
		return None
	try:
		return frappe.utils.get_datetime(value)
	except Exception:
		return None
