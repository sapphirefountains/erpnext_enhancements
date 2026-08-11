# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The suppression decision — one pure function, and the only place it is made.

**The client reports; the server decides.** That is invariant I6, and it is the whole
reason this module exists as a separate, import-free file. A notification suppressed by a
client that simply declines to render it has been suppressed by something nobody can
audit, test or explain to the person who says chat stopped working. So the browser sends
facts — which room it is looking at, whether the window is focused — and every "should
this fire" answer is computed here, on the server, from those facts.

--------------------------------------------------------------------------------------
Pure, and the purity is load-bearing rather than tasteful
--------------------------------------------------------------------------------------

No ``frappe``. No database. **No clock**: ``now`` is a parameter. The whole
twelve-row matrix is therefore exercisable in the bench-free CI tier, which is the only
tier in this repo with automatic regression protection (``CLAUDE.md``: there is no Frappe
integration-test job). A rule that lives inside a function which needs a bench is a rule
nothing checks, and this is the rule that decides whether twenty people's phones buzz.

Passing the clock in also makes the two timers testable without waiting: the presence TTL
and :data:`BLUR_GRACE` are both "how long ago", and a test that has to sleep is a test that
gets deleted.

--------------------------------------------------------------------------------------
The states, and why they are observable
--------------------------------------------------------------------------------------

ADR §H.1's table has rows for "in ERPNext with the chat app closed" and "not in ERPNext at
all", which look unobservable — Frappe v16 has no presence primitive and no Python-visible
view of who holds a socket (ADR §H.3.0). They are observable here for one reason: the
floating bubble on every Desk page heartbeats too, reporting ``room=None``. So a client
record with no active room *is* the "in ERPNext, chat closed" signal, and the absence of
every record is the "not in ERPNext" signal. If the bubble ever stops beating, rows 5 and 6
collapse into one — which changes a reason code and nothing else, because both notify.

--------------------------------------------------------------------------------------
Aggregation across tabs is a quantifier, not a most-recent-wins
--------------------------------------------------------------------------------------

One person has three tabs. The rule (ADR §H.1.1) is *"**no** client of this user has that
room focused"* — so suppression is decided over **every** live client, and one focused
client suppresses while a second blurred client does not un-suppress. Keying on the newest
beat instead is the single most likely thing to get wrong here, and it presents as
"notifications stopped working when I opened a second tab".

:data:`_STATE_PRECEDENCE` encodes it: the most-engaged client decides.

--------------------------------------------------------------------------------------
Missing signal notifies. Loud beats silent.
--------------------------------------------------------------------------------------

Presence lives in Redis, and every production deploy ``FLUSHDB``s Redis. An unreadable or
empty presence store therefore resolves to :attr:`Reason.PRESENCE_UNKNOWN`, which
**notifies**. The two failures are not symmetric: a duplicate ping is visible,
self-correcting and reported by the person who got it, while a suppressed message is
invisible to everyone including the person who needed it and surfaces days later as "I
never got that". A Redis outage under this rule is loud; under the opposite rule it is an
unannounced, total notification blackout. ADR §H.3.4 / CQ-9.

--------------------------------------------------------------------------------------
One contradiction in the source documents, resolved as a setting
--------------------------------------------------------------------------------------

ADR §H.1 row 9 lists the rows a mention overrides — 2, 4, 5, 6, 7 — and §H.2.3 says in
terms that it does **not** override row 11 (mute), deferring the argument to CQ-8. CQ-8's
own ship-default (a) then says *"soft mute … mentions STILL NOTIFY"*. Those cannot both be
built.

The truth table wins here, because §H.7 hands Phase 4 the twelve-row matrix as invariant
I7's test and a table that contradicts its own test is worse than either answer. So the
default is **mute beats mention**, and the other reading is one flag away
(:attr:`Policy.mention_beats_mute`) rather than a rewrite. Recorded in
``chat/notifications/README.md`` as a question for Nikolas, not settled by us.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

# --- the constants, and why these numbers ------------------------------------
#
# ADR §H.3.1. Chat deliberately does NOT reuse the house 30 s / 75 s pair from
# `public/js/collab/live_form_sync.js`, and the reason is infrastructural rather than
# aesthetic: **30 s is exactly the Google Cloud load balancer's idle-connection cut**, and
# the only reason realtime works on this site today is that socket.io's stock 25 s
# pingInterval beats that cut by five seconds. Copying 30 s into a new subsystem inherits a
# five-second margin and buys nothing.

#: How often a browser tab reports. Comfortably inside socket.io's 25 s ping and the 30 s
#: GCLB idle cut, so a presence beat is never the thing racing the load balancer.
HEARTBEAT_SECONDS: Final[int] = 20

#: How long a report stays believable. ~2× the heartbeat plus one beat of slack, so a single
#: dropped beat — a GC pause, a throttled background timer, a transient 502 — does not flip a
#: present user to absent.
PRESENCE_TTL_SECONDS: Final[int] = 55

#: How long a blurred window still counts as "they are right there".
#:
#: The single-row question "does a blurred window notify?" has no answer, because blurred
#: spans two situations that need opposite ones: alt-tabbed for eight seconds to read an
#: email (pinging here is the most-complained-about class of chat notification), and left the
#: tab open on Friday and went home (firing nothing here is silent message loss, and worse
#: than the general case because the person believes the app is open).
#:
#: 120 s ≈ 2 × the TTL, so blur hysteresis sits one layer above presence expiry instead of
#: racing it. ADR §H.2.1 / CQ-2.
BLUR_GRACE_SECONDS: Final[int] = 120


class Surface(str, Enum):
	"""What a client is looking at ERPNext through. Reported, never inferred.

	The distinction that matters is :attr:`APP` versus :attr:`DESK`: a Desk page carries the
	floating bubble, which heartbeats with no active room, and that beat is what makes ADR
	§H.1's row 5 ("in ERPNext, chat closed") an observable state rather than a wish.
	"""

	#: The chat SPA at ``/chat``.
	APP = "app"
	#: Any other ERPNext page — Desk or portal — carrying the floating bubble.
	DESK = "desk"


class Reason(Enum):
	"""Why the decision came out the way it did. **Closed, and every member reachable.**

	This enum is the support tool. Every failure mode in a notification system is invisible
	by construction — nobody reports the ping they did not get — so ``decide`` returns *why*
	alongside *what*, ``debug.explain`` renders it, and the live production walkthrough shows
	it beside each row so a reviewer sees the decision rather than only the outcome.

	The values are stable strings because they are logged and read back by a human weeks
	later; renaming one silently breaks a grep through the logs.
	"""

	#: Row 8. The recipient wrote it. Nobody is informed of their own message.
	AUTHOR = "author"
	#: Row 1. A live client has this very room open with the window focused.
	FOCUSED_HERE = "focused_here"
	#: Row 10. Mentioned, while already looking at the message. It highlights and marks read.
	MENTION_WHILE_FOCUSED = "mention_while_focused"
	#: Row 2. This room is open but the window is blurred, still inside the grace.
	BLURRED_WITHIN_GRACE = "blurred_within_grace"
	#: Row 3. This room is open, blurred for longer than the grace. They have gone.
	BLURRED_PAST_GRACE = "blurred_past_grace"
	#: Row 4. In chat, looking at a different conversation.
	OTHER_ROOM_ACTIVE = "other_room_active"
	#: Row 5. In ERPNext, chat not open on any room — the bubble is beating, the SPA is not.
	IN_ERPNEXT_CHAT_CLOSED = "in_erpnext_chat_closed"
	#: Row 6. No live client at all. The presence store was readable and had nothing to say.
	ABSENT = "absent"
	#: Row 7. The presence store could not be read, or every record has aged past the TTL.
	#: **Notifies.** A deploy FLUSHDBs Redis, so this is a frequent, benign, correct state.
	PRESENCE_UNKNOWN = "presence_unknown"
	#: Row 9. A direct mention, overriding every suppressing state except a focused view.
	MENTION = "mention"
	#: Row 11. The recipient muted this room.
	MUTED = "muted"
	#: Row 12. The recipient switched their own notifications off. Frappe drops the bell row
	#: before this app sees it, so this is a statement of fact rather than a policy choice.
	NOTIFICATIONS_DISABLED = "notifications_disabled"


#: Which reasons describe a *presence* state, most-engaged first. The multi-tab aggregation
#: takes the minimum index across a user's live clients, which is what makes "one focused
#: client suppresses, a second blurred client does not un-suppress" true by construction
#: rather than by a chain of ``if``s somebody will reorder.
_STATE_PRECEDENCE: Final[tuple[Reason, ...]] = (
	Reason.FOCUSED_HERE,
	Reason.BLURRED_WITHIN_GRACE,
	Reason.OTHER_ROOM_ACTIVE,
	Reason.BLURRED_PAST_GRACE,
	Reason.IN_ERPNEXT_CHAT_CLOSED,
	Reason.ABSENT,
	Reason.PRESENCE_UNKNOWN,
)

#: The presence states a mention is allowed to override. Row 9's list, verbatim: rows 2, 4,
#: 5, 6 and 7. Rows 1 and 10 are absent because the person is looking at the message, and
#: row 3 is absent because it already notifies.
_MENTION_OVERRIDES: Final[frozenset[Reason]] = frozenset(
	{
		Reason.BLURRED_WITHIN_GRACE,
		Reason.OTHER_ROOM_ACTIVE,
		Reason.IN_ERPNEXT_CHAT_CLOSED,
		Reason.ABSENT,
		Reason.PRESENCE_UNKNOWN,
	}
)


@dataclass(frozen=True)
class Policy:
	"""The tunable half, passed in rather than read from a module constant.

	Every value here is a product judgement dressed as an engineering one (ADR §K.2's CQ-2,
	CQ-4, CQ-8, CQ-9), so each is a parameter with a shipped default and a place to change
	it. Frozen because a policy object mutated between two recipients in one fan-out would
	produce a decision nobody can reproduce from the log line.
	"""

	#: Seconds a blurred window still counts as present. CQ-2.
	blur_grace_seconds: int = BLUR_GRACE_SECONDS
	#: Seconds a client record stays believable. CQ-9.
	presence_ttl_seconds: int = PRESENCE_TTL_SECONDS
	#: Whether a direct mention pierces a muted room. **Default false**, per ADR §H.1 row 9's
	#: override list and §H.2.3's explicit exclusion of row 11. CQ-8's ship-default (a) reads
	#: the other way; see the module docstring for why the table wins until Nikolas rules.
	mention_beats_mute: bool = False


@dataclass(frozen=True)
class ClientPresence:
	"""One browser tab's last report. The unit the aggregation quantifies over.

	Keyed per **tab**, not per session: one Frappe session spans many tabs, and the whole
	multi-tab rule needs per-tab granularity to mean anything.

	``focused_changed_at`` is **server-stamped on receipt**, never client-supplied. A client
	that could set it would be able to backdate its own blur and buy silence indefinitely;
	stamping it here bounds a hostile or wedged client to suppressing its own notifications
	and nobody else's, which is self-harm and an acceptable blast radius.
	"""

	#: The ``Chat Room`` name this tab is looking at, or ``None`` for "in ERPNext, not in a
	#: room". Never a Google space name — a client that could report one would turn the space
	#: mapping table into a suppression oracle.
	room: str | None = None
	#: ``document.hasFocus() && visibilityState === "visible"``, as one boolean.
	focused: bool = False
	#: Which surface reported. Only ever used to explain a decision, never to make one.
	surface: Surface = Surface.APP
	#: Epoch seconds of the last beat. Older than the TTL and this record does not count.
	last_seen: int = 0
	#: Epoch seconds when ``focused`` last changed, stamped by the server.
	focused_changed_at: int = 0

	def is_live(self, *, now: int, ttl: int) -> bool:
		"""Whether this record is fresh enough to be believed.

		Checked here as well as in Redis because the store keys presence as one hash per user
		with a field per tab — per-field expiry needs Redis 7.4 and production runs 7.0.15 —
		so one live tab keeps a dead sibling's field alive past its own TTL. Filtering on read
		is the half of that design that makes it correct.
		"""
		return 0 < self.last_seen and (now - self.last_seen) <= ttl


@dataclass(frozen=True)
class Recipient:
	"""Everything about one person that bears on one message. Facts, already resolved.

	Deliberately not a ``User`` document and deliberately not a room name plus a lookup: this
	function may not touch the database, so the caller resolves membership, mute state and
	the framework's per-user kill switch once and hands them over. That also makes every row
	of the matrix constructible in a test without a bench.
	"""

	#: True when the recipient wrote the message. Checked first, and it wins outright.
	is_author: bool = False
	#: True when the message names them directly, or an ``@triton`` reply is addressed to them.
	is_mentioned: bool = False
	#: ``Chat Room Member.notification_mode``/``muted_until`` collapsed to one boolean.
	is_muted: bool = False
	#: ``Notification Settings.enabled``. False means Frappe drops every bell row for this
	#: person before this app is consulted, so "exactly two notifications" is a ceiling and
	#: never a delivery guarantee.
	notifications_enabled: bool = True


@dataclass(frozen=True)
class Decision:
	"""What fires, and why. Five outputs, and **no cell is ever left unset.**

	The columns are ADR §H.1's, with one rename that matters. The table calls the last two
	"room unread indicator" and "bubble count badge"; here they are :attr:`room_indicator`
	and :attr:`counter`, because the server publishes a *counter* on the recipient's own
	realtime room and the badge is a **client render** of it. The same person can have three
	tabs with different foreground surfaces, and making the server decide per tab would mean
	the server modelling tabs. So the split is: the server publishes the number, and each tab
	renders the badge only when its own foreground surface is not the chat app.

	:attr:`badge` is retained as the table's own column so the twelve-row test can assert
	against the ADR verbatim, and it describes what a tab whose SPA is *not* foreground
	should draw.
	"""

	#: Write (or dedupe onto) a ``Notification Log`` row.
	bell: bool
	#: Send a Web Push to every registered subscription of this person.
	push: bool
	#: Show the unread dot against this room in the room list.
	room_indicator: bool
	#: Publish the per-(user, room) unread counter on ``user:<email>``.
	counter: bool
	#: What a tab that is not foregrounding the chat app should render on the bubble.
	badge: bool
	#: Advance ``last_read_seq`` to this message. True only when they are demonstrably
	#: looking at it — rows 1, 8 and 10. Never in a blurred row: marking a message read for
	#: somebody who is not looking clears their unread state *and*, once per-message receipts
	#: exist, tells the sender it was read. A suppressed ping is recoverable. A false read
	#: receipt is not.
	auto_read: bool
	#: Which rule decided. The support tool; see :class:`Reason`.
	reason: Reason

	@property
	def notifies(self) -> bool:
		"""Whether either notification surface fires. The one-line form for a log line."""
		return self.bell or self.push


#: The twelve rows, as data. Written as a table rather than as a chain of returns so that it
#: reads next to ADR §H.1 and so that a change is a diff on one line instead of a re-argued
#: branch. Order is (bell, push, room_indicator, counter, badge, auto_read).
_OUTCOMES: Final[dict[Reason, tuple[bool, bool, bool, bool, bool, bool]]] = {
	# 8  — own message: nothing at all, and it is already read.
	Reason.AUTHOR: (False, False, False, False, False, True),
	# 1  — focused on this room: nothing, and it marks read.
	Reason.FOCUSED_HERE: (False, False, False, False, False, True),
	# 10 — mentioned while focused here: same, and the client highlights it.
	Reason.MENTION_WHILE_FOCUSED: (False, False, False, False, False, True),
	# 2  — blurred inside the grace: the room dot only. Explicitly NOT auto-read.
	Reason.BLURRED_WITHIN_GRACE: (False, False, True, True, False, False),
	# 3  — blurred past the grace: they have gone home. Everything.
	Reason.BLURRED_PAST_GRACE: (True, True, True, True, True, False),
	# 4  — a different room is active: the room dot, and the counter for other surfaces.
	Reason.OTHER_ROOM_ACTIVE: (False, False, True, True, False, False),
	# 5  — in ERPNext with chat closed: everything.
	Reason.IN_ERPNEXT_CHAT_CLOSED: (True, True, True, True, True, False),
	# 6  — not in ERPNext at all: everything, rendered on their next load.
	Reason.ABSENT: (True, True, True, True, True, False),
	# 7  — no believable signal: everything. Loud beats silent.
	Reason.PRESENCE_UNKNOWN: (True, True, True, True, True, False),
	# 9  — a direct mention: everything, as its own notification type.
	Reason.MENTION: (True, True, True, True, True, False),
	# 11 — muted: the room dot only. The room still shows unread; it just does not ping.
	Reason.MUTED: (False, False, True, True, False, False),
	# 12 — their own notifications are off: Frappe drops the bell, so neither surface fires,
	#      but the in-app counters are ours and keep working.
	Reason.NOTIFICATIONS_DISABLED: (False, False, True, True, True, False),
}


def classify_presence(
	clients: list[ClientPresence] | tuple[ClientPresence, ...],
	*,
	room: str,
	now: int,
	policy: Policy | None = None,
	store_available: bool = True,
) -> Reason:
	"""Collapse every one of a person's tabs into one presence state. **Pure.**

	Args:
		clients: every reported tab, live or stale. Stale ones are filtered here rather than
			by the caller, so a caller that forgets cannot accidentally believe a dead tab.
		room: the ``Chat Room`` the message landed in.
		now: epoch seconds. A parameter, never ``time.time()`` — see the module docstring.
		policy: the tunables; the shipped defaults when omitted.
		store_available: False when the presence store could not be read at all. Distinct
			from "no clients": an unreadable store and an empty one both notify, but they are
			different operational events and the reason code has to say which.

	Returns:
		The single :class:`Reason` describing this person, chosen by
		:data:`_STATE_PRECEDENCE` — the most engaged live client decides.
	"""
	settings = policy or Policy()

	if not store_available:
		return Reason.PRESENCE_UNKNOWN

	live = [c for c in clients if c.is_live(now=now, ttl=settings.presence_ttl_seconds)]
	if not live:
		return Reason.ABSENT

	states = {_classify_one(client, room=room, now=now, policy=settings) for client in live}
	for candidate in _STATE_PRECEDENCE:
		if candidate in states:
			return candidate

	# Unreachable: _classify_one only ever returns members of _STATE_PRECEDENCE. Kept because
	# the alternative to an explicit fallback is an implicit `None` escaping into `_OUTCOMES`,
	# and this module's whole failure direction is "notify rather than stay silent".
	return Reason.PRESENCE_UNKNOWN


def _classify_one(client: ClientPresence, *, room: str, now: int, policy: Policy) -> Reason:
	"""One live tab's state. Split out so the multi-tab quantifier reads as a quantifier."""
	if client.room is None:
		# Beating, but not in any conversation — the bubble on a Desk page. ADR §H.1 row 5.
		return Reason.IN_ERPNEXT_CHAT_CLOSED

	if client.room != room:
		return Reason.OTHER_ROOM_ACTIVE

	if client.focused:
		return Reason.FOCUSED_HERE

	# Blurred, on this room. Which side of the grace?
	#
	# A zero `focused_changed_at` means the server never saw a focus transition for this tab —
	# it has been blurred since it first reported. Treating that as "just blurred" would let a
	# tab left open on Friday suppress everything all weekend, so it reads as past the grace.
	if client.focused_changed_at <= 0:
		return Reason.BLURRED_PAST_GRACE

	blurred_for = now - client.focused_changed_at
	if blurred_for < policy.blur_grace_seconds:
		return Reason.BLURRED_WITHIN_GRACE
	return Reason.BLURRED_PAST_GRACE


def decide(
	*,
	recipient: Recipient,
	presence: Reason,
	policy: Policy | None = None,
) -> Decision:
	"""Should this message notify this person, and why. **The function I6 names.**

	Takes an already-classified presence state rather than the raw clients, so that the two
	halves — "what is this person doing" and "what follows from it" — are separately
	testable and separately explainable. :func:`decide_for` is the convenience that does both.

	The override order, and it is the whole specification:

	1. **They wrote it.** Nothing else is consulted.
	2. **They are looking at this room, focused.** A push for a message already on screen is
	   noise; a mention there is row 10 and marks read rather than pinging.
	3. **Muted.** Ahead of the mention override by default — see the module docstring for the
	   contradiction this resolves and the flag that reverses it.
	4. **Their own notifications are off.** A statement of fact about the framework, placed
	   ahead of the mention override because Frappe drops the row either way.
	5. **Mentioned**, in any state a mention is allowed to override.
	6. Otherwise the presence state decides.

	Returns a :class:`Decision` in which every field is set, always.
	"""
	settings = policy or Policy()

	if recipient.is_author:
		return _outcome(Reason.AUTHOR)

	if presence == Reason.FOCUSED_HERE:
		return _outcome(Reason.MENTION_WHILE_FOCUSED if recipient.is_mentioned else Reason.FOCUSED_HERE)

	if recipient.is_muted:
		if recipient.is_mentioned and settings.mention_beats_mute:
			return _outcome(Reason.MENTION)
		return _outcome(Reason.MUTED)

	if not recipient.notifications_enabled:
		return _outcome(Reason.NOTIFICATIONS_DISABLED)

	if recipient.is_mentioned and presence in _MENTION_OVERRIDES:
		return _outcome(Reason.MENTION)

	return _outcome(presence)


def decide_for(
	*,
	recipient: Recipient,
	clients: list[ClientPresence] | tuple[ClientPresence, ...],
	room: str,
	now: int,
	policy: Policy | None = None,
	store_available: bool = True,
) -> Decision:
	"""Classify and decide in one call. What the fan-out actually uses."""
	settings = policy or Policy()
	presence = classify_presence(
		clients, room=room, now=now, policy=settings, store_available=store_available
	)
	return decide(recipient=recipient, presence=presence, policy=settings)


def _outcome(reason: Reason) -> Decision:
	"""Look the row up. Raises on a reason with no row rather than inventing one.

	A ``KeyError`` here is a development-time failure at the one line that is wrong. The
	alternative — a default — would silently pick an answer for a state nobody thought about,
	and the two candidate defaults are "notify everybody about everything" and "notify nobody
	about anything". Neither is a good accident.
	"""
	bell, push, indicator, counter, badge, auto_read = _OUTCOMES[reason]
	return Decision(
		bell=bell,
		push=push,
		room_indicator=indicator,
		counter=counter,
		badge=badge,
		auto_read=auto_read,
		reason=reason,
	)


def with_policy(base: Policy, **overrides: object) -> Policy:
	"""A :class:`Policy` with some fields replaced. The settings loader's constructor.

	Exists so that the module that *does* read ``Chat Settings`` can build a policy without
	importing ``dataclasses.replace`` and without this module growing a Frappe import.
	"""
	return replace(base, **overrides)  # type: ignore[arg-type]
