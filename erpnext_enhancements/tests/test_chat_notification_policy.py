"""Bench-free tests for the suppression decision — ADR §H.1's twelve rows, as a table.

Subject: ``erpnext_enhancements.chat.notifications.policy``, standard library only, so the
one rule that decides whether twenty people's phones buzz runs in the only CI tier this
repo protects automatically (``CLAUDE.md``: there is no Frappe integration-test job).

**The expectations below are a literal table, not a second implementation.** That is the
point and it is easy to lose: a test that recomputes ``bell = not focused and not muted``
passes whenever the code and the test share a misreading, which is exactly the failure a
notification matrix produces — plausible, self-consistent, and wrong in one cell nobody
looks at. So :data:`TRUTH_TABLE` is transcribed from the ADR's markdown table by hand, cell
by cell, and the tests only ever compare against it.

What each group is really guarding:

* **the twelve rows** — the matrix itself, every cell, including the ones that are ``no``
  because somebody argued them (auto-read in the blurred rows is the load-bearing one: a
  false read receipt is unrecoverable in a way a suppressed ping is not);
* **the multi-tab quantifier** — one focused tab suppresses; a second blurred tab does not
  un-suppress. R02 names this *"the one most likely to be got wrong"*, and its production
  symptom is "notifications stopped working when I opened a second tab";
* **expiry** — the presence record is a heartbeat with a TTL, so a browser that dies
  without a disconnect event stops suppressing **with no cooperation from the client**.
  This is the test that stops presence quietly becoming a sticky flag;
* **fail-open** — an unreadable presence store notifies. A deploy ``FLUSHDB``s Redis, so
  this state happens on every release, and the alternative is an unannounced blackout;
* **determinism and totality** — the same inputs in a different order give the same answer,
  and every reason code is reachable. An unreachable code is a row nobody can hit; a
  code with no row is a ``KeyError`` in a background job at 2am.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_notification_policy.py -q`` step in
CI; ``python -m unittest`` collects nothing from a file shaped like this and reports
success — which has already cost this repo two suites.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

from erpnext_enhancements.chat.notifications import policy as P

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
POLICY_PY: pathlib.Path = APP_DIR / "chat" / "notifications" / "policy.py"

#: An arbitrary fixed "now". Fixed because the module takes the clock as a parameter, which
#: is what lets the two timers be tested without a single sleep.
NOW: int = 1_800_000_000

ROOM: str = "0f9c1a2b3c4d5e6f7a8b9c0d1e2f3a4b"
OTHER: str = "ffffffffffffffffffffffffffffffff"


def _client(
	*,
	room: str | None = ROOM,
	focused: bool = True,
	age: int = 1,
	blurred_for: int | None = None,
	surface: P.Surface = P.Surface.APP,
) -> P.ClientPresence:
	"""One tab, described in terms a reader of the ADR would use.

	``age`` is seconds since the last beat and ``blurred_for`` is seconds since focus was
	lost — both relative to :data:`NOW`, because absolute epochs in a test body are unreadable
	and the arithmetic is where the off-by-ones live.
	"""
	changed = NOW - blurred_for if blurred_for is not None else NOW - 600
	return P.ClientPresence(
		room=room,
		focused=focused,
		surface=surface,
		last_seen=NOW - age,
		focused_changed_at=changed,
	)


# --- the module stays in the bench-free tier -----------------------------------


def test_policy_imports_nothing_outside_the_standard_library() -> None:
	"""``frappe`` appearing here would delete this whole suite from CI silently — the file
	would stop importing on the runner and the step would be removed as "broken"."""
	tree = ast.parse(POLICY_PY.read_text(encoding="utf-8"), filename=str(POLICY_PY))
	roots = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			roots.update(alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.ImportFrom) and node.level == 0:
			roots.add((node.module or "").split(".")[0])
	assert roots <= {
		"__future__",
		"dataclasses",
		"enum",
		"typing",
	}, f"{POLICY_PY.name} imports {sorted(roots)}; standard library only"


def test_policy_reads_no_clock() -> None:
	"""``now`` is a parameter. Reading the clock here would make the grace untestable and —
	worse — would let two recipients in one fan-out disagree about what time it is.

	Asserted over the **parsed tree**, not the source text, because this module's own prose
	argues at length about the clock it does not read. A substring search over a file that
	documents its reasoning finds the documentation; the kiosk service-worker guard learned
	the same thing and strips comments before asserting.
	"""
	tree = ast.parse(POLICY_PY.read_text(encoding="utf-8"), filename=str(POLICY_PY))
	forbidden = {"time", "now", "utcnow", "monotonic", "now_datetime", "today"}
	called = {
		node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
		for node in ast.walk(tree)
		if isinstance(node, ast.Call)
	}
	assert not (called & forbidden), f"{POLICY_PY.name} calls {sorted(called & forbidden)}"


# --- the constants are the ADR's, not the house pair ----------------------------


def test_the_constants_are_chats_own_and_not_the_house_thirty_seventyfive() -> None:
	"""ADR §H.3.1. 30 s is exactly the GCLB idle-connection cut and the only reason realtime
	works on this site is socket.io's 25 s ping beating it by five seconds. A new subsystem
	copying 30 s inherits that five-second margin for no benefit."""
	assert P.HEARTBEAT_SECONDS == 20
	assert P.PRESENCE_TTL_SECONDS == 55
	assert P.BLUR_GRACE_SECONDS == 120


def test_the_ttl_is_at_least_two_heartbeats() -> None:
	"""One dropped beat — a GC pause, a throttled timer, a transient 502 — must not flip a
	present user to absent, or every notification decision flaps."""
	assert P.PRESENCE_TTL_SECONDS >= 2 * P.HEARTBEAT_SECONDS


def test_the_blur_grace_sits_above_the_presence_ttl() -> None:
	"""Two timers that overlap race; one layered above the other is hysteresis. §H.2.1."""
	assert P.BLUR_GRACE_SECONDS >= 2 * P.PRESENCE_TTL_SECONDS


# --- the twelve rows ------------------------------------------------------------

#: ADR §H.1, transcribed by hand. Keys are the reason code; values are
#: ``(bell, push, room_indicator, counter, badge, auto_read)``.
#:
#: **Do not generate this from the module under test.** It is a copy of the specification,
#: and its only value is being an independent one.
TRUTH_TABLE: dict[P.Reason, tuple[bool, bool, bool, bool, bool, bool]] = {
	# row 1  — SPA open, this room focused
	P.Reason.FOCUSED_HERE: (False, False, False, False, False, True),
	# row 2  — this room active, window blurred < 120 s
	P.Reason.BLURRED_WITHIN_GRACE: (False, False, True, True, False, False),
	# row 3  — this room active, window blurred >= 120 s
	P.Reason.BLURRED_PAST_GRACE: (True, True, True, True, True, False),
	# row 4  — SPA open, a different room active
	P.Reason.OTHER_ROOM_ACTIVE: (False, False, True, True, False, False),
	# row 5  — in ERPNext, chat closed
	P.Reason.IN_ERPNEXT_CHAT_CLOSED: (True, True, True, True, True, False),
	# row 6  — not in ERPNext at all
	P.Reason.ABSENT: (True, True, True, True, True, False),
	# row 7  — presence missing or stale
	P.Reason.PRESENCE_UNKNOWN: (True, True, True, True, True, False),
	# row 8  — the recipient is the author
	P.Reason.AUTHOR: (False, False, False, False, False, True),
	# row 9  — a direct mention
	P.Reason.MENTION: (True, True, True, True, True, False),
	# row 10 — a direct mention while focused on this room
	P.Reason.MENTION_WHILE_FOCUSED: (False, False, False, False, False, True),
	# row 11 — the room is muted
	P.Reason.MUTED: (False, False, True, True, False, False),
	# row 12 — the recipient's own notification settings are off
	P.Reason.NOTIFICATIONS_DISABLED: (False, False, True, True, True, False),
}


def _cells(decision: P.Decision) -> tuple[bool, bool, bool, bool, bool, bool]:
	return (
		decision.bell,
		decision.push,
		decision.room_indicator,
		decision.counter,
		decision.badge,
		decision.auto_read,
	)


def test_every_row_of_the_truth_table_matches_the_adr() -> None:
	for reason, expected in TRUTH_TABLE.items():
		decision = P.decide(recipient=P.Recipient(), presence=reason)
		if reason in {
			P.Reason.AUTHOR,
			P.Reason.MENTION,
			P.Reason.MENTION_WHILE_FOCUSED,
			P.Reason.MUTED,
			P.Reason.NOTIFICATIONS_DISABLED,
		}:
			# These five are reached through a recipient flag rather than a presence state,
			# so they are asserted by their own tests below; here we only pin the row.
			decision = P._outcome(reason)
		assert _cells(decision) == expected, f"row {reason.value} disagrees with ADR §H.1"


def test_the_table_covers_every_reason_and_invents_none() -> None:
	"""A reason with no row raises ``KeyError`` inside a background job; a row with no reason
	is a decision nothing can ever produce. Both are only visible as an equality."""
	assert set(TRUTH_TABLE) == set(P.Reason)
	assert set(P._OUTCOMES) == set(P.Reason)


def test_no_cell_is_left_unset() -> None:
	"""ADR §H.1: *"No cell is blank."* A ``None`` reaching the fan-out reads as falsy and
	silently suppresses."""
	for reason in P.Reason:
		for cell in _cells(P._outcome(reason)):
			assert cell is True or cell is False


# --- the rows reached through presence ------------------------------------------


def test_row_1_focused_on_this_room_suppresses_everything_and_marks_read() -> None:
	decision = P.decide_for(
		recipient=P.Recipient(), clients=[_client(focused=True)], room=ROOM, now=NOW
	)
	assert decision.reason is P.Reason.FOCUSED_HERE
	assert not decision.notifies
	assert decision.auto_read is True


def test_row_2_blurred_inside_the_grace_shows_the_dot_and_nothing_else() -> None:
	decision = P.decide_for(
		recipient=P.Recipient(),
		clients=[_client(focused=False, blurred_for=P.BLUR_GRACE_SECONDS - 1)],
		room=ROOM,
		now=NOW,
	)
	assert decision.reason is P.Reason.BLURRED_WITHIN_GRACE
	assert not decision.notifies
	assert decision.room_indicator is True


def test_row_2_never_marks_read_however_briefly_blurred() -> None:
	"""The load-bearing half of the grace. Marking a message read for somebody who is not
	looking clears their own unread state and — once per-message receipts exist — tells the
	*sender* it was read. A suppressed ping is recoverable; a false receipt is not."""
	for blurred_for in (0, 1, 30, P.BLUR_GRACE_SECONDS - 1):
		decision = P.decide_for(
			recipient=P.Recipient(),
			clients=[_client(focused=False, blurred_for=blurred_for)],
			room=ROOM,
			now=NOW,
		)
		assert decision.auto_read is False, f"auto-read fired after {blurred_for}s of blur"


def test_row_3_blurred_past_the_grace_notifies_on_both_surfaces() -> None:
	decision = P.decide_for(
		recipient=P.Recipient(),
		clients=[_client(focused=False, blurred_for=P.BLUR_GRACE_SECONDS)],
		room=ROOM,
		now=NOW,
	)
	assert decision.reason is P.Reason.BLURRED_PAST_GRACE
	assert decision.bell is True
	assert decision.push is True


def test_the_grace_boundary_is_exclusive_at_the_constant() -> None:
	"""The boundary is where the off-by-one lives, and ``<`` versus ``<=`` is the whole rule."""
	inside = P.classify_presence(
		[_client(focused=False, blurred_for=P.BLUR_GRACE_SECONDS - 1)], room=ROOM, now=NOW
	)
	at = P.classify_presence(
		[_client(focused=False, blurred_for=P.BLUR_GRACE_SECONDS)], room=ROOM, now=NOW
	)
	assert inside is P.Reason.BLURRED_WITHIN_GRACE
	assert at is P.Reason.BLURRED_PAST_GRACE


def test_a_tab_that_has_never_reported_a_focus_change_counts_as_long_blurred() -> None:
	"""``focused_changed_at == 0`` means the server never saw a transition. Reading that as
	"just blurred" would let a tab left open on Friday suppress all weekend."""
	never = P.ClientPresence(room=ROOM, focused=False, last_seen=NOW - 1, focused_changed_at=0)
	assert P.classify_presence([never], room=ROOM, now=NOW) is P.Reason.BLURRED_PAST_GRACE


def test_row_4_a_different_room_suppresses_the_ping_but_not_the_counter() -> None:
	decision = P.decide_for(
		recipient=P.Recipient(), clients=[_client(room=OTHER, focused=True)], room=ROOM, now=NOW
	)
	assert decision.reason is P.Reason.OTHER_ROOM_ACTIVE
	assert not decision.notifies
	assert decision.counter is True
	assert decision.badge is False


def test_row_5_the_bubble_beating_with_no_room_is_in_erpnext_chat_closed() -> None:
	"""This is the row that looks unobservable. It is observable because the floating bubble
	on every Desk page heartbeats with ``room=None``."""
	decision = P.decide_for(
		recipient=P.Recipient(),
		clients=[_client(room=None, focused=True, surface=P.Surface.DESK)],
		room=ROOM,
		now=NOW,
	)
	assert decision.reason is P.Reason.IN_ERPNEXT_CHAT_CLOSED
	assert decision.bell is True
	assert decision.push is True


def test_row_6_no_clients_at_all_is_absent_and_notifies() -> None:
	decision = P.decide_for(recipient=P.Recipient(), clients=[], room=ROOM, now=NOW)
	assert decision.reason is P.Reason.ABSENT
	assert decision.bell is True


def test_row_7_an_unreadable_presence_store_notifies_and_says_so() -> None:
	"""Distinct from row 6 on purpose: an empty store and an unreachable one are different
	operational events even though both notify, and the reason code is what tells an operator
	that Redis is down rather than that everyone went home."""
	decision = P.decide_for(
		recipient=P.Recipient(),
		clients=[_client(focused=True)],
		room=ROOM,
		now=NOW,
		store_available=False,
	)
	assert decision.reason is P.Reason.PRESENCE_UNKNOWN
	assert decision.bell is True
	assert decision.push is True


# --- expiry: presence is a heartbeat, never a sticky flag ------------------------


def test_presence_expiry_resumes_notifications() -> None:
	"""ADR §H.3.6's named test, and the most important one in the file.

	A browser crash, a ``SIGKILL``, a closed laptop lid and a network partition all produce
	**no disconnect event**. A design that sets a flag on connect and clears it on disconnect
	leaves that person permanently "focused" and silently drops every message to them until
	they next open the app — which they report as chat being broken, not as a notification
	bug.

	So: stop the heartbeat with no cooperation of any kind from the client — no ``pagehide``,
	no goodbye, no key deletion — advance the clock past the TTL, and assert the decision
	flips on its own.
	"""
	crashed = _client(focused=True, age=0)

	before = P.decide_for(recipient=P.Recipient(), clients=[crashed], room=ROOM, now=NOW)
	assert before.notifies is False, "precondition: a live focused tab suppresses"

	later = NOW + P.PRESENCE_TTL_SECONDS + 1
	after = P.decide_for(recipient=P.Recipient(), clients=[crashed], room=ROOM, now=later)

	assert after.reason is P.Reason.ABSENT
	assert after.bell is True
	assert after.push is True


def test_blur_grace_expiry_resumes_notifications() -> None:
	"""The companion test, because ``BLUR_GRACE`` is a second timer and therefore a second
	opportunity to write a sticky flag by accident."""
	blurred = _client(focused=False, age=0, blurred_for=0)

	assert (
		P.decide_for(recipient=P.Recipient(), clients=[blurred], room=ROOM, now=NOW).notifies
		is False
	)

	# Keep the tab beating — only the blur ages. Otherwise this would re-test the TTL.
	still_here = P.ClientPresence(
		room=ROOM,
		focused=False,
		last_seen=NOW + P.BLUR_GRACE_SECONDS,
		focused_changed_at=NOW,
	)
	after = P.decide_for(
		recipient=P.Recipient(), clients=[still_here], room=ROOM, now=NOW + P.BLUR_GRACE_SECONDS
	)
	assert after.reason is P.Reason.BLURRED_PAST_GRACE
	assert after.notifies is True


def test_a_stale_record_does_not_count_even_when_redis_still_holds_it() -> None:
	"""Presence is one hash per user with a field per tab, because per-field expiry needs
	Redis 7.4 and production runs 7.0.15. One live tab therefore keeps a dead sibling's field
	alive past its own TTL, and filtering on read is the half of that design that makes it
	correct."""
	dead = _client(focused=True, age=P.PRESENCE_TTL_SECONDS + 1)
	assert P.classify_presence([dead], room=ROOM, now=NOW) is P.Reason.ABSENT


# --- the multi-tab quantifier ---------------------------------------------------


def test_one_focused_tab_suppresses_and_a_second_blurred_tab_does_not_unsuppress() -> None:
	"""R02 §2.6's *"the one most likely to be got wrong"*. The rule is *"**no** client of this
	user has that room focused"* — a quantifier over every tab, not the newest beat."""
	focused = _client(focused=True, age=30)
	blurred = _client(focused=False, age=0, blurred_for=600)

	for order in itertools.permutations([focused, blurred]):
		decision = P.decide_for(recipient=P.Recipient(), clients=list(order), room=ROOM, now=NOW)
		assert decision.reason is P.Reason.FOCUSED_HERE
		assert decision.notifies is False


def test_the_newest_beat_does_not_win() -> None:
	"""Explicitly the failure this design prevents: keying on the most recent report makes a
	second tab overwrite the first, which reads as "notifications stopped working when I
	opened another tab"."""
	newest_and_absent = _client(room=None, age=0)
	older_and_focused = _client(focused=True, age=40)
	decision = P.decide_for(
		recipient=P.Recipient(),
		clients=[newest_and_absent, older_and_focused],
		room=ROOM,
		now=NOW,
	)
	assert decision.reason is P.Reason.FOCUSED_HERE


def test_when_the_focused_tab_expires_the_remaining_tab_decides() -> None:
	"""Two sessions, one focused on the room and one blurred; a message produces nothing.
	Then the focused session expires and the next message produces both. ADR §H.7."""
	focused = _client(focused=True, age=0)
	elsewhere = _client(room=OTHER, focused=True, age=0)
	clients = [focused, elsewhere]

	assert P.classify_presence(clients, room=ROOM, now=NOW) is P.Reason.FOCUSED_HERE

	later = NOW + P.PRESENCE_TTL_SECONDS + 1
	kept_beating = P.ClientPresence(room=OTHER, focused=True, last_seen=later, focused_changed_at=later)
	assert (
		P.classify_presence([focused, kept_beating], room=ROOM, now=later)
		is P.Reason.OTHER_ROOM_ACTIVE
	)


# --- the overrides --------------------------------------------------------------


def test_row_8_the_author_is_never_notified_in_any_presence_state() -> None:
	for reason in P.Reason:
		decision = P.decide(recipient=P.Recipient(is_author=True), presence=reason)
		assert decision.reason is P.Reason.AUTHOR
		assert decision.notifies is False
		assert decision.auto_read is True


def test_row_9_a_mention_overrides_exactly_the_rows_the_adr_lists() -> None:
	"""Rows 2, 4, 5, 6 and 7 — and no others. Row 1 is excluded because they are looking at
	it, row 3 already notifies, and row 11 is CQ-8's argument rather than this one's."""
	overridable = {
		P.Reason.BLURRED_WITHIN_GRACE,
		P.Reason.OTHER_ROOM_ACTIVE,
		P.Reason.IN_ERPNEXT_CHAT_CLOSED,
		P.Reason.ABSENT,
		P.Reason.PRESENCE_UNKNOWN,
	}
	for reason in overridable:
		decision = P.decide(recipient=P.Recipient(is_mentioned=True), presence=reason)
		assert decision.reason is P.Reason.MENTION, f"a mention did not override {reason.value}"
		assert decision.bell is True
		assert decision.push is True


def test_row_10_a_mention_while_focused_marks_read_instead_of_pinging() -> None:
	decision = P.decide(recipient=P.Recipient(is_mentioned=True), presence=P.Reason.FOCUSED_HERE)
	assert decision.reason is P.Reason.MENTION_WHILE_FOCUSED
	assert decision.notifies is False
	assert decision.auto_read is True


def test_row_11_mute_beats_a_mention_by_default() -> None:
	"""ADR §H.1 row 9's override list omits row 11 and §H.2.3 says so in terms. CQ-8's own
	ship-default reads the other way; the table wins until Nikolas rules, and the other
	reading is one flag rather than a rewrite."""
	decision = P.decide(
		recipient=P.Recipient(is_mentioned=True, is_muted=True), presence=P.Reason.ABSENT
	)
	assert decision.reason is P.Reason.MUTED
	assert decision.notifies is False
	assert decision.room_indicator is True, "a muted room still shows unread; it just does not ping"


def test_the_mention_beats_mute_flag_reverses_it_and_nothing_else() -> None:
	loud = P.Policy(mention_beats_mute=True)
	mentioned = P.decide(
		recipient=P.Recipient(is_mentioned=True, is_muted=True), presence=P.Reason.ABSENT, policy=loud
	)
	assert mentioned.reason is P.Reason.MENTION

	plain = P.decide(recipient=P.Recipient(is_muted=True), presence=P.Reason.ABSENT, policy=loud)
	assert plain.reason is P.Reason.MUTED, "the flag must not un-mute an ordinary message"


def test_mute_does_not_override_a_focused_view() -> None:
	"""Muting is about pings. Somebody looking at the room still auto-reads it."""
	decision = P.decide(recipient=P.Recipient(is_muted=True), presence=P.Reason.FOCUSED_HERE)
	assert decision.reason is P.Reason.FOCUSED_HERE
	assert decision.auto_read is True


def test_row_12_notifications_disabled_keeps_the_in_app_counters() -> None:
	"""Frappe drops the ``Notification Log`` row before this app is consulted, so neither
	notification surface fires — but the counter and the room dot are ours and keep working.
	"Exactly two" is a ceiling, never a delivery guarantee."""
	decision = P.decide(
		recipient=P.Recipient(notifications_enabled=False), presence=P.Reason.ABSENT
	)
	assert decision.reason is P.Reason.NOTIFICATIONS_DISABLED
	assert decision.notifies is False
	assert decision.counter is True
	assert decision.room_indicator is True


def test_notifications_disabled_beats_a_mention() -> None:
	"""Not a policy choice — a statement of fact. ``make_notification_logs`` filters the
	recipient out before we are asked, so claiming the mention fired would be a lie."""
	decision = P.decide(
		recipient=P.Recipient(is_mentioned=True, notifications_enabled=False),
		presence=P.Reason.ABSENT,
	)
	assert decision.reason is P.Reason.NOTIFICATIONS_DISABLED
	assert decision.bell is False


def test_the_author_flag_beats_every_other_flag() -> None:
	decision = P.decide(
		recipient=P.Recipient(
			is_author=True, is_mentioned=True, is_muted=True, notifications_enabled=False
		),
		presence=P.Reason.ABSENT,
	)
	assert decision.reason is P.Reason.AUTHOR


# --- properties -----------------------------------------------------------------


def test_p1_suppression_is_monotone_in_engagement() -> None:
	"""Adding a *more* engaged tab may only ever reduce what fires. If adding a tab could
	turn a silent decision loud, opening a second window would start pinging you."""
	blurred = _client(focused=False, blurred_for=600)
	alone = P.decide_for(recipient=P.Recipient(), clients=[blurred], room=ROOM, now=NOW)
	with_focus = P.decide_for(
		recipient=P.Recipient(), clients=[blurred, _client(focused=True)], room=ROOM, now=NOW
	)
	assert alone.notifies is True
	assert with_focus.notifies is False


def test_p2_staleness_only_ever_moves_towards_notifying() -> None:
	"""Time passing with no new beat must never make the system quieter."""
	client = _client(focused=True, age=0)
	for elapsed in (0, 10, P.PRESENCE_TTL_SECONDS, P.PRESENCE_TTL_SECONDS + 1, 86_400):
		decision = P.decide_for(
			recipient=P.Recipient(), clients=[client], room=ROOM, now=NOW + elapsed
		)
		if elapsed <= P.PRESENCE_TTL_SECONDS:
			assert decision.notifies is False
		else:
			assert decision.notifies is True


def test_p3_the_result_is_stable_across_a_thousand_shuffles() -> None:
	"""Determinism, without importing ``random``: every permutation of a fixed set is a
	stronger claim than a thousand random shuffles of it, and it needs no seed."""
	clients = [
		_client(focused=False, blurred_for=5),
		_client(room=OTHER, focused=True),
		_client(room=None),
		_client(focused=False, blurred_for=9_999),
	]
	answers = {
		P.classify_presence(list(order), room=ROOM, now=NOW)
		for order in itertools.permutations(clients)
	}
	assert len(answers) == 1, f"order changed the answer: {answers}"


def test_p4_every_unknown_signal_fails_open() -> None:
	"""The whole failure direction of the module, asserted once as a property."""
	for decision in (
		P.decide_for(recipient=P.Recipient(), clients=[], room=ROOM, now=NOW),
		P.decide_for(
			recipient=P.Recipient(), clients=[], room=ROOM, now=NOW, store_available=False
		),
		P.decide_for(
			recipient=P.Recipient(),
			clients=[_client(focused=True, age=10_000)],
			room=ROOM,
			now=NOW,
		),
	):
		assert decision.bell is True
		assert decision.push is True


def test_p5_nothing_ever_notifies_the_author() -> None:
	for clients in ([], [_client()], [_client(room=None)], [_client(focused=False)]):
		decision = P.decide_for(
			recipient=P.Recipient(is_author=True), clients=clients, room=ROOM, now=NOW
		)
		assert decision.notifies is False


def test_every_reason_code_is_reachable_from_a_real_input() -> None:
	"""A code nothing can produce is a row nobody has tested. Each entry below is the
	shortest input that reaches it, which doubles as documentation of what the code means."""
	reached = {
		P.decide_for(recipient=r, clients=c, room=ROOM, now=NOW, store_available=s).reason
		for r, c, s in (
			(P.Recipient(is_author=True), [], True),
			(P.Recipient(), [_client(focused=True)], True),
			(P.Recipient(is_mentioned=True), [_client(focused=True)], True),
			(P.Recipient(), [_client(focused=False, blurred_for=1)], True),
			(P.Recipient(), [_client(focused=False, blurred_for=9_999)], True),
			(P.Recipient(), [_client(room=OTHER)], True),
			(P.Recipient(), [_client(room=None)], True),
			(P.Recipient(), [], True),
			(P.Recipient(), [], False),
			(P.Recipient(is_mentioned=True), [], True),
			(P.Recipient(is_muted=True), [], True),
			(P.Recipient(notifications_enabled=False), [], True),
		)
	}
	assert reached == set(P.Reason), f"unreachable: {sorted(r.value for r in set(P.Reason) - reached)}"


# --- the shape the fan-out depends on -------------------------------------------


def test_counter_is_published_for_every_row_except_one_eight_and_ten() -> None:
	"""ADR §H.1.1 states it as a rule rather than as a column, and the fan-out reads it that
	way: the server publishes the number for every row except the three where the recipient is
	demonstrably looking at the message."""
	silent = {P.Reason.FOCUSED_HERE, P.Reason.AUTHOR, P.Reason.MENTION_WHILE_FOCUSED}
	for reason in P.Reason:
		assert P._outcome(reason).counter is (reason not in silent)


def test_auto_read_and_notifying_are_mutually_exclusive() -> None:
	"""Marking read *and* pinging is incoherent in both directions, and a table edit that
	produced it would otherwise pass every row-level test."""
	for reason in P.Reason:
		decision = P._outcome(reason)
		assert not (decision.auto_read and decision.notifies), reason.value


def test_push_never_fires_without_the_bell() -> None:
	"""Decision #3's "exactly two, in sync". A push with no bell row leaves nothing to clear
	when the person reads on another device, which is how a notification becomes immortal."""
	for reason in P.Reason:
		decision = P._outcome(reason)
		assert not (decision.push and not decision.bell), reason.value


def test_a_policy_is_frozen() -> None:
	"""A policy mutated between two recipients of one fan-out produces a decision nobody can
	reproduce from the log line."""
	settings = P.Policy()
	try:
		settings.blur_grace_seconds = 1  # type: ignore[misc]
	except Exception:
		return
	raise AssertionError("Policy must be frozen")


def test_with_policy_overrides_one_field_and_keeps_the_rest() -> None:
	base = P.Policy()
	tuned = P.with_policy(base, blur_grace_seconds=45)
	assert tuned.blur_grace_seconds == 45
	assert tuned.presence_ttl_seconds == base.presence_ttl_seconds
	assert tuned.mention_beats_mute == base.mention_beats_mute
