# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Deterministic Google Chat identifiers, derived from ERPNext identifiers.

**Pure module. No I/O, no ``frappe`` import, no clock, no randomness.** Every
function here is a total function of its arguments, which is what lets the
bench-free pytest tier cover it — the only tier in this repo with automatic
regression protection in CI.

Two derivations live here, and together they are **invariant I3**.

``client_message_id`` produces the ``clientAssignedMessageId`` we hand to
``spaces.messages.create``. Google's constraints on it are quoted in ADR 0009
§G.2.1 and are hard: *begins with* ``client-``, *up to 63 characters*, *only
lowercase letters, numbers and hyphens*, *unique within a space*. A derivation
that can exceed the cap or emit an illegal character is a bug, so the cap and
the charset are not left to inspection of the hash alphabet: they are asserted
on the way out by :func:`assert_legal_client_message_id`, and the assertion is
part of the contract rather than a debugging aid.

``request_id`` produces the ``requestId`` query parameter on ``spaces.create``
and ``spaces.setup``. Specifying an existing ``requestId`` returns the resource
created with that id instead of creating a new one, so a deterministic value
makes provisioning **exactly-once for free** — a retried provisioning job
replays the same value and gets the same space back rather than a second one.

**Invariant I3, stated once so no later phase has to re-derive it.** Every
``clientAssignedMessageId`` ERPNext issues begins with ``client-``. Therefore an
inbound Chat event carrying a ``client-`` prefixed ``clientAssignedMessageId``
that ERPNext issued is *definitionally our own echo* — not a new message — and
must be suppressed rather than stored again. :func:`is_client_message_id` is the
cheap first half of that test (does it look like ours?); the authoritative half
is a probe on the ``unique(room, client_message_id)`` index, because "looks like
ours" and "is ours" are not the same claim and only the index knows.

**Non-reversibility is deliberate and is not a cost.** ADR 0009 §G.2.2 rejects
the reversible ``"client-" + name.lower()`` derivation: it is legal only if
Frappe's hash alphabet is lowercase alphanumeric, which nobody has verified, and
it breaks silently the day a naming rule changes. Nothing needs to invert these
— echo suppression asks "do I have a row with this id?", which is an index
probe, not a decoding step. So this module ships **no inverse**; see the
divergence note against Appendix B §3.4 in the Phase 1 hand-off.

**Why ``site`` is an explicit argument.** ADR 0009 §G.2.2/§G.2.3 seed both
derivations with ``frappe.local.site``, so a non-production site relaying into
the same Workspace mints different ids rather than colliding with production's.
Reading that global here would make the module impure and un-unit-testable, so
the caller passes it. It is keyword-only and has **no default**, because a
default would silently produce a second, site-less id space the day someone
forgets it.

**Derived once, stored forever.** ``client_message_id`` is computed in
``Chat Message.validate`` and stored in a column; it is never re-derived on read.
The site name is in the seed, so a site *rename* would change the derivation —
which is precisely why the stored value, not the function, is the source of truth
after insert.

**Not in ``before_insert``, and it cannot be** — stated here because that is
where an earlier revision of this docstring sent people, and because
``before_insert`` is the obvious place to look. Frappe v16's ``Document.insert``
runs ``before_insert`` *before* ``set_new_name``, and ``set_new_name`` begins by
assigning ``doc.name = None`` for every autoname other than ``prompt``/``UUID``
(``frappe/model/naming.py``). ``Chat Message`` is ``autoname: hash``, so at
``before_insert`` there is no name to seed the derivation with and the id would
be minted from ``""``. ``validate`` is the first hook after the name is final,
and it still runs before Frappe's mandatory-field check — which matters, because
``client_message_id`` is ``reqd``. See
:func:`erpnext_enhancements.chat.sync.outbox.derive_client_message_id`, and
``tests/test_chat_outbox.py::test_before_insert_cannot_see_the_document_name``,
which executes the claim so that a change in Frappe's ordering shows up as a red
test rather than as a comment nobody re-reads.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable

#: Mandated by Google: *"Begins with ``client-``."* (ADR 0009 §G.2.1)
CLIENT_MESSAGE_ID_PREFIX = "client-"

#: Mandated by Google: *"Contains up to 63 characters."*
CLIENT_MESSAGE_ID_MAX_LENGTH = 63

#: Mandated by Google: *"only lowercase letters, numbers, and hyphens."*
_LEGAL_CLIENT_MESSAGE_ID = re.compile(r"\A[a-z0-9-]+\Z")

#: 128 bits of SHA-256, hex-encoded. ``len("client-") + 32 == 39`` — fixed, and
#: independent of every input, which is what makes the 63-char cap unreachable
#: by construction rather than by luck.
_DIGEST_CHARS = 32

#: ``requestId`` is emitted in UUID form rather than as raw hex, because that is
#: the shape Google's ``requestId`` parameters conventionally take.
#: VERIFY: the documented character set, maximum length and **deduplication
#: window** of ``requestId`` on ``spaces.create`` / ``spaces.setup`` — ADR 0009
#: §G.2.3 records that no agent ever fetched them. Settle by reading those two
#: reference pages. Blocks nothing: the ``unique(gchat_space_name)`` and
#: ``unique(linked_doctype, linked_document)`` constraints already carry
#: correctness, so this decides only whether a duplicate space is *prevented* or
#: merely *detected*.
_REQUEST_ID_HEX_CHARS = 32


def _digest(seed: str, chars: int) -> str:
	"""SHA-256 of ``seed``, truncated to ``chars`` hex characters.

	Hex is the whole trick: ``hexdigest()`` emits ``[0-9a-f]`` only, a strict
	subset of Google's ``[a-z0-9-]``, so no normalisation step exists that could
	fail. Everything legal-by-construction in this module rests on that.
	"""
	return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:chars]


def assert_legal_client_message_id(value: str) -> str:
	"""Return ``value``, or raise :class:`ValueError` if Google would reject it.

	Called on every value this module produces. That is not paranoia about our
	own hash: it is the executable form of the three constraints, so that a
	future change to the derivation cannot quietly start emitting something
	Chat will 400 on. It is also the validator the transport client uses on a
	``messageId`` handed to it from anywhere else.
	"""
	if not value.startswith(CLIENT_MESSAGE_ID_PREFIX):
		raise ValueError(f"clientAssignedMessageId must begin with {CLIENT_MESSAGE_ID_PREFIX!r}")
	if len(value) > CLIENT_MESSAGE_ID_MAX_LENGTH:
		raise ValueError(
			f"clientAssignedMessageId is {len(value)} characters; "
			f"Google allows at most {CLIENT_MESSAGE_ID_MAX_LENGTH}"
		)
	if not _LEGAL_CLIENT_MESSAGE_ID.match(value):
		raise ValueError("clientAssignedMessageId may contain only lowercase letters, numbers and hyphens")
	return value


def client_message_id(erpnext_message_name: str, *, site: str) -> str:
	"""Derive the ``clientAssignedMessageId`` for one ``Chat Message``.

	``erpnext_message_name`` is the ``Chat Message.name``; ``site`` is
	``frappe.local.site``, passed in so this stays pure (see the module
	docstring). Both must be non-empty: an empty name would map every message
	to one id, which the ``unique(room, client_message_id)`` index would turn
	into a hard insert failure on the second message — loud, but only after the
	relay has already misbehaved.
	"""
	if not erpnext_message_name:
		raise ValueError("erpnext_message_name is required to derive a client message id")
	if not site:
		raise ValueError("site is required to derive a client message id (ADR 0009 §G.2.2)")

	seed = f"{site}|Chat Message|{erpnext_message_name}"
	return assert_legal_client_message_id(CLIENT_MESSAGE_ID_PREFIX + _digest(seed, _DIGEST_CHARS))


def is_client_message_id(value: str | None) -> bool:
	"""Does ``value`` carry the shape of an id ERPNext issued? (invariant I3)

	The cheap half of echo suppression. ``True`` means *"treat this inbound
	event as a candidate echo and go look it up"* — **not** *"this is certainly
	ours"*. Any Chat client may set a ``client-`` prefixed id, so the
	authoritative test remains the ``unique(room, client_message_id)`` probe.
	Conflating the two would let a third party suppress our ingestion of their
	messages by guessing a prefix, which is a denial of service, not a defect
	in the derivation.
	"""
	if not value:
		return False
	return value.startswith(CLIENT_MESSAGE_ID_PREFIX)


def request_id(erpnext_name: str, operation: str, *, site: str) -> str:
	"""Derive the deterministic ``requestId`` for a provisioning-style call.

	``operation`` is the relay operation name (``"Space Create"``,
	``"Space Setup"``, ``"Membership Create"``…) and ``erpnext_name`` is the
	referenced document's name — usually a ``Chat Room``. Same value in, same
	value out, forever, which is what makes a retried job replay rather than
	duplicate. The value is stored on ``Chat Relay Job.request_id`` so that a
	retry of *that row* replays it even if the derivation later changes.

	Seed order follows ADR 0009 §G.2.3 (``site|operation|reference``); the
	argument order follows Appendix B §3.4's ``request_id(name, op)``. Those two
	disagree only in presentation, and the seed is the one that must not drift.
	"""
	if not erpnext_name:
		raise ValueError("erpnext_name is required to derive a request id")
	if not operation:
		raise ValueError("operation is required to derive a request id")
	if not site:
		raise ValueError("site is required to derive a request id (ADR 0009 §G.2.3)")

	seed = f"{site}|{operation}|{erpnext_name}"
	return str(uuid.UUID(_digest(seed, _REQUEST_ID_HEX_CHARS)))


def scope_fingerprint(scopes: Iterable[str]) -> str:
	"""Order-insensitive short fingerprint of a scope list, for cache keys.

	Lives here rather than in :mod:`.auth` because it is pure and because the
	access-token cache is keyed on ``(subject, sorted(scopes))`` (ADR 0009
	§E.4.2) — sorting is the load-bearing part, and a caller that reorders its
	scope tuple must still hit the same cache entry rather than mint a second
	token for the same grant.
	"""
	return _digest("|".join(sorted(scopes)), 16)
