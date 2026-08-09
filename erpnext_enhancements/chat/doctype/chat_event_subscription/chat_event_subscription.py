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

Three alarms are computed over these rows in a later phase: expiring-and-not-renewed, suspended,
and — the one that needs no row at all to fire, and is the reason the roster is checked rather
than the table — a coworker with **no** ``ACTIVE`` subscription.

Schema only in Phase 1. No subscription manager, no renewal scheduler, no Google call.
"""

from typing import Final

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


class ChatEventSubscription(Document):
	pass
