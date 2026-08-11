# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turning ``Chat Settings`` into a :class:`~...notifications.policy.Policy`, with clamps.

Three tunables reach the suppression rule from the operator's form: the heartbeat interval,
the presence TTL and the blur grace. This module is the only place they are read, and the
only place they are bounded.

--------------------------------------------------------------------------------------
Why these fields are read at all, rather than left as constants
--------------------------------------------------------------------------------------

``presence_heartbeat_seconds`` and ``presence_ttl_seconds`` shipped on the form in Phase 1
and **nothing read them**. An operator could change either one and watch it have no effect,
forever, with no error — which is worse than not offering the control, because the form
asserts a capability the code does not have. Phase 4 either had to delete them or honour
them, and honouring them is the smaller lie: the numbers really are the ones the system
runs on, and CQ-2 explicitly wants the blur grace to be *"a config value … revisited with
data"*.

--------------------------------------------------------------------------------------
Why they are clamped, which is the interesting half
--------------------------------------------------------------------------------------

**These values suppress notifications, so a bad one is silent.** Set the TTL to an hour and
a browser that crashed at nine o'clock goes on silencing its owner's messages until ten,
with nothing anywhere to say so. Set the heartbeat above the TTL and every user flickers
between present and absent, so notifications arrive for roughly half the messages and
nobody can characterise the pattern. Neither shows up in a log.

So each read is bounded, the bounds are asymmetric in the safe direction — toward
notifying, never toward silence — and a value outside them is corrected rather than
obeyed. Nothing here throws: this runs on the notification path, and refusing to decide
because a settings field is out of range would turn a typo into an outage.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any, Final

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.notifications import policy

#: A heartbeat below this is a self-inflicted denial of service — one whitelisted POST per
#: tab per interval, with a full session load behind it. Above 60 and the TTL needed to
#: cover two beats grows past the point where a crashed tab's silence is noticeable.
_HEARTBEAT_BOUNDS: Final[tuple[int, int]] = (5, 60)

#: A TTL below two heartbeats makes a single dropped beat flip a present user to absent,
#: which flickers. The ceiling is what stops a crashed browser silencing somebody for the
#: rest of the afternoon.
_TTL_CEILING: Final[int] = 300

#: A grace of zero is a legitimate choice — "notify the moment they look away". The ceiling
#: exists because a grace measured in hours is indistinguishable from never notifying, and
#: whoever set it would not find out.
_GRACE_BOUNDS: Final[tuple[int, int]] = (0, 1800)


def load(settings: Any | None = None) -> policy.Policy:
	"""Build the policy for this site. **Never raises, never returns ``None``.**

	Falls back to the module defaults on any failure, including a site that has this code but
	no ``Chat Settings`` row — which is every fresh bench, and the state ERPNext's own test
	bootstrap runs in. Returning the shipped defaults there is right: they are the values the
	whole test suite pins.
	"""
	try:
		doc = settings if settings is not None else frappe.get_cached_doc("Chat Settings")
	except Exception:
		return policy.Policy()

	heartbeat = _clamp(
		_read(doc, "presence_heartbeat_seconds", policy.HEARTBEAT_SECONDS),
		*_HEARTBEAT_BOUNDS,
	)
	ttl = _clamp(
		_read(doc, "presence_ttl_seconds", policy.PRESENCE_TTL_SECONDS),
		# The floor is derived, not fixed: whatever the heartbeat ends up being, one dropped
		# beat must not be able to flip a present user to absent.
		2 * heartbeat,
		_TTL_CEILING,
	)
	grace = _clamp(_read(doc, "blur_grace_seconds", policy.BLUR_GRACE_SECONDS), *_GRACE_BOUNDS)

	return policy.Policy(
		blur_grace_seconds=grace,
		presence_ttl_seconds=ttl,
		mention_beats_mute=bool(cint(_read(doc, "mention_beats_mute", 0))),
	)


def heartbeat_seconds(settings: Any | None = None) -> int:
	"""The interval to tell the client to beat at.

	Separate from :func:`load` because the policy has no use for it — the *server* only ever
	asks "is this record still fresh", which is the TTL. The heartbeat is a client instruction
	and it travels in the heartbeat response, so the two sides cannot drift.
	"""
	try:
		doc = settings if settings is not None else frappe.get_cached_doc("Chat Settings")
	except Exception:
		return policy.HEARTBEAT_SECONDS
	return _clamp(_read(doc, "presence_heartbeat_seconds", policy.HEARTBEAT_SECONDS), *_HEARTBEAT_BOUNDS)


def _read(doc: Any, fieldname: str, fallback: int) -> int:
	"""One settings integer, with a fallback for a field that is absent or blank.

	``getattr(..., None) or fallback`` per the house rule: ``doc_events`` and boot code run
	during ERPNext's own test bootstrap, before this app's fields exist, and a settings read
	that raises there turns a fresh install into a crash.
	"""
	value = cint(getattr(doc, fieldname, None) or 0)
	return value if value > 0 else int(fallback)


def _clamp(value: int, low: int, high: int) -> int:
	"""Bound a value into ``[low, high]``. Corrects rather than refuses — see the docstring."""
	return max(low, min(int(value), high))
