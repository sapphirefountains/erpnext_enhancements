"""The chat package's HTTP surface, enumerated — Phase 6 §3.2 item 2 and §4.G.5.

**This module is data, not behaviour.** It names every ``@frappe.whitelist()`` in the chat
package and says, for each, whether calling it changes state. Nothing imports it at runtime;
``tests/test_chat_endpoint_surface.py`` reads it, and the CI job that runs that test is the
only consumer.

WHY IT IS A FILE RATHER THAN A LIST IN A TEST
=============================================

Three things need the same answer and would otherwise each invent their own:

1. **§4.G.5 — every state-changing endpoint is POST, never GET.** A GET that mutates is
   CSRF-able whatever the token handling does, and is cacheable by intermediaries besides.
2. **§4.G.4 — every endpoint is rate limited or explicitly exempt with a reason.** The limit
   an endpoint deserves follows from what it does, so the classification comes first.
3. **The Phase 6 checkpoint** has to show the surface as a table. A table assembled by hand at
   the end is a table that was wrong for months.

**The set is checked against the filesystem by equality, so a new endpoint fails the build by
default.** That is the property the whole file exists for: a hardcoded list that nothing
compares against the source is the same bug as no list, arriving later and with more
confidence. The precedent is ``assistant_tools/_gate.py``'s chat denylist, which is checked the
same way for the same reason.

WHAT "MUTATING" MEANS HERE, AND THE ONE JUDGEMENT CALL
======================================================

Mutating means *a caller can change something they would want changed*. It is deliberately not
"writes a row": :func:`~erpnext_enhancements.chat.api.search.search_messages` writes a
``Chat Retrieval Audit`` row on a privileged read, and that write is a **record of the read**,
not an effect the caller asked for — forging it against somebody's session accomplishes nothing
except making their own snooping more visible. So search is classified non-mutating and the
audit write is noted rather than counted.

The same reasoning does **not** extend to
:func:`~erpnext_enhancements.chat.api.presence.heartbeat`. Presence looks incidental, but
Phase 4's suppression rules read it: forging a heartbeat for somebody makes the server believe
they are sitting focused on a conversation, which suppresses every notification they would
otherwise get. Silently, and for as long as it is repeated.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final, NamedTuple

#: The package this module describes. Resolved from ``__file__`` rather than named, so moving
#: the package cannot leave the scanner pointed at a directory that no longer exists.
CHAT_PACKAGE: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent

#: The dotted prefix every discovered name is reported under.
DOTTED_ROOT: Final[str] = "erpnext_enhancements.chat"


class Endpoint(NamedTuple):
	"""One ``@frappe.whitelist()`` function, as the AST sees it."""

	dotted: str
	allow_guest: bool
	methods: tuple[str, ...]
	relpath: str
	lineno: int


#: Endpoints that change state. **Each must declare ``methods=["POST"]``**, asserted by
#: ``test_chat_endpoint_surface.py``. The value is why it mutates, in one line, because
#: "everything here is obviously a write" stops being obvious the first time somebody adds one
#: that is not.
MUTATING: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.api.compose.send_message": "inserts a Chat Message and enqueues a relay job",
	f"{DOTTED_ROOT}.api.compose.edit_message": "rewrites a message body and writes a revision row",
	f"{DOTTED_ROOT}.api.compose.delete_message": "tombstones a message and propagates the delete",
	f"{DOTTED_ROOT}.api.conversations.create_direct_message": "creates a Chat Room and its members",
	f"{DOTTED_ROOT}.api.conversations.create_group": "creates a Chat Room and its members",
	f"{DOTTED_ROOT}.api.presence.set_typing": "publishes a typing signal other clients render",
	f"{DOTTED_ROOT}.api.presence.heartbeat": (
		"writes the presence record Phase 4's suppression truth table reads — a forged one "
		"silences somebody's notifications for as long as it is repeated"
	),
	f"{DOTTED_ROOT}.api.presence.goodbye": "clears a presence record, which un-suppresses notifications",
	f"{DOTTED_ROOT}.api.readstate.mark_read": "advances a member's read cursor and clears unread state",
	f"{DOTTED_ROOT}.api.readstate.mark_all_read": "advances every room's read cursor at once",
	f"{DOTTED_ROOT}.api.rooms.set_last_open_room": "writes the room the bubble reopens at",
	f"{DOTTED_ROOT}.gchat.webhook.handle": "ingests inbound Google Chat events; already POST-only",
	f"{DOTTED_ROOT}.invoke.triton_link.link_bot_credentials": "writes the bot's stored credentials",
	f"{DOTTED_ROOT}.notifications.api.subscribe": "creates a Chat Push Subscription for a device",
	f"{DOTTED_ROOT}.notifications.api.unsubscribe": "deletes a Chat Push Subscription",
	f"{DOTTED_ROOT}.notifications.api.read_from_notification": "marks a room read from a push click",
	f"{DOTTED_ROOT}.sync.outbound.retry_relay_job": "moves a relay job's state and wakes a worker",
	f"{DOTTED_ROOT}.sync.provisioning.enroll_org_units": "creates Chat Room rows for org units",
	f"{DOTTED_ROOT}.sync.provisioning.start_org_mirror": (
		"opens a Chat Provisioning Run which, with dry_run off, creates real Google spaces"
	),
	f"{DOTTED_ROOT}.sync.provisioning.create_document_room": "creates a Chat Room bound to a document",
	f"{DOTTED_ROOT}.governance.export_runner.request_export": (
		"inserts a Chat Export Request and queues the build; the bundle it produces is the "
		"artefact that leaves the building"
	),
}

#: Endpoints that only read. The value is the reason, and a reason that is merely "it is a
#: getter" is not one — the entries that matter are the four that write something and are
#: nonetheless classified here.
NON_MUTATING: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.governance.export_runner.download_export": (
		"streams a bundle that was already built. Writes an export_downloaded audit row, "
		"which is a record OF the read. NOT POST, uniquely on this surface: the browser has "
		"to NAVIGATE for response.type='download' to reach a file, and an XHR would receive "
		"the bytes and drop them"
	),
	f"{DOTTED_ROOT}.governance.tombstone.expand": (
		"returns a deleted message's retained body and its revision trail. Writes a "
		"tombstone_expanded row, which is a record OF the read rather than an effect the "
		"caller wants. POST because the reason travels in the body"
	),
	f"{DOTTED_ROOT}.governance.viewer.search": (
		"the oversight read. Writes a Chat Retrieval Audit row, which is a record OF the read "
		"rather than an effect the caller wants — the same reasoning search_messages carries. "
		"POST despite being a read: it carries the REASON in its body, and a reason in a query "
		"string lands in the access log, the browser history and the next Referer header"
	),
	f"{DOTTED_ROOT}.governance.viewer.access_log": (
		"reads the unified access report. Reading the record of reads is not itself a read of "
		"chat content, and recording it would put the auditor's own review into the log they "
		"are reviewing. POST so its filters are not logged alongside somebody's name"
	),
	f"{DOTTED_ROOT}.governance.viewer.my_access_log": (
		"the employee's own 'who read my messages' view (D-1). Answers only for the caller, "
		"every row redacted, and refuses unless the transparency setting is on"
	),
	f"{DOTTED_ROOT}.api.compose.prepare_upload": (
		"returns the size and count limits the composer enforces; the upload itself goes to "
		"Frappe's own upload_file"
	),
	f"{DOTTED_ROOT}.api.conversations.search_people": "searches Users the caller may start a DM with",
	f"{DOTTED_ROOT}.api.history.get_messages": "reads a page of a room's transcript",
	f"{DOTTED_ROOT}.api.history.get_thread": "reads one thread's replies in seq order",
	f"{DOTTED_ROOT}.api.history.get_message_context": "reads the messages around one message",
	f"{DOTTED_ROOT}.api.mentions.search_mention_targets": "searches members mentionable in a room",
	f"{DOTTED_ROOT}.api.presence.get_presence": "reads a room's presence map",
	f"{DOTTED_ROOT}.api.readstate.get_read_marks": "reads who has read up to where",
	f"{DOTTED_ROOT}.api.readstate.get_unread_state": "reads the caller's unread counts",
	f"{DOTTED_ROOT}.api.rooms.get_rooms": "reads the caller's room list",
	f"{DOTTED_ROOT}.api.rooms.get_room": "reads one room's metadata",
	f"{DOTTED_ROOT}.api.rooms.get_members": "reads one room's membership",
	f"{DOTTED_ROOT}.api.rooms.get_bootstrap": "reads the SPA's opening payload",
	f"{DOTTED_ROOT}.api.search.search_messages": (
		"searches the caller's own rooms. Writes a Chat Retrieval Audit row on a privileged "
		"read, but that write is a record OF the read rather than an effect a caller wants — "
		"forging it only makes the forger's own snooping more visible"
	),
	f"{DOTTED_ROOT}.invoke.triton_link.bot_credential_status": "reports whether the bot is linked",
	f"{DOTTED_ROOT}.notifications.api.push_config": "returns the VAPID public key and push settings",
	f"{DOTTED_ROOT}.notifications.api.explain": (
		"prints why one notification decision came out the way it did; Phase 4's debug command "
		"and the runbook's first move on a notification incident"
	),
	f"{DOTTED_ROOT}.retrieval.api.get_chat_context": (
		"the one retrieval door. Writes a Chat Retrieval Audit row before returning content, "
		"for the same reason search does. NOT constrained to POST: the caller is Triton over "
		"HTTP, not this app's SPA, and pinning a method on a cross-service contract from one "
		"side is how the contract breaks on a day nobody is deploying chat"
	),
	f"{DOTTED_ROOT}.sync.attachments.download": (
		"serves an attachment's bytes. **Must stay GET-reachable** — it is a URL a browser "
		"navigates to, and a POST-only byte path cannot be the target of a link, an image tag "
		"or a right-click Save As"
	),
}


#: Endpoints that read, and must nonetheless assert **membership** rather than the weaker
#: "may see the room". Two axes come apart here and the file is clearer for saying so:
#: ``MUTATING`` is about CSRF exposure and drives the POST rule; ``intent="write"`` is about
#: authorisation and drives ``require_room``. Almost everything that needs one needs the other,
#: and this dict is the exception list for the cases that do not.
WRITE_GATED_READS: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.api.compose.prepare_upload": (
		"changes nothing, but its answer is 'you may upload into this room'. Letting the "
		"oversight role through would return yes and then have send_message refuse — one "
		"question answered two ways, which is worse than either answer"
	),
}

#: Endpoints whose caller must hold a role, not merely a session. Each must call
#: ``frappe.only_for`` in its own body — asserted from the AST, because a gate that lives in a
#: caller is a gate the next caller forgets.
#:
#: The value is the role. It is a literal rather than a settings read on purpose: these are
#: operator actions on the *plumbing*, and a settings-driven role would mean the setting that
#: decides who may rebuild a digest is itself editable by whoever already got in.
ADMIN_ONLY: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.invoke.triton_link.bot_credential_status": "System Manager",
	f"{DOTTED_ROOT}.invoke.triton_link.link_bot_credentials": "System Manager",
	f"{DOTTED_ROOT}.sync.outbound.retry_relay_job": "System Manager",
	f"{DOTTED_ROOT}.sync.provisioning.enroll_org_units": "System Manager",
	f"{DOTTED_ROOT}.sync.provisioning.start_org_mirror": "System Manager",
}


# --- §4.G.4 / G6-5: rate limiting -------------------------------------------------
#
# **The mechanism this app has does not do what a rate limit is for, and that is a measured
# fact about this host rather than a theory.** Read this before adding a decorator.
#
# `frappe.rate_limiter.rate_limit` keys on `frappe.local.request_ip` (`rate_limiter.py:147`,
# `:156`, `:161`). `frappe.session.user` appears nowhere in that file: there is **no per-user
# mode**. And `request_ip` is taken from the **first comma element of `X-Forwarded-For`,
# unconditionally, with no trusted-hop count** (`frappe/auth.py:64-66`), falling back to
# `REMOTE_ADDR` only when that header is absent. The identity is a string the caller writes.
#
# On this host it is worse than forgeable — it is already collapsed. `CHANGELOG.md` records
# the measurement: **0/79** of May's logins came from GCP load-balancer ranges, **0/252** of
# June's, then **79/94 in July** and **41/84 in August**, each a different `35.191.x`. Since
# roughly 2026-07-18 the recorded client address has been the load balancer's own, from a
# rotating fleet. So a per-IP bucket is neither stable for one caller nor separate between
# callers, and TASK-2026-01478 is the open investigation into why.
#
# The repo already reached this conclusion once and wrote the rule down, in
# `docs/website-capture/README.md:180-185`:
#
#     do not read the 120/hour as a per-client control, and **do not add one that depends on
#     the IP**.
#
# That is why `RATE_LIMIT_EXEMPT` is long and `RATE_LIMIT` is short. Adding forty decorators
# would satisfy a checklist by installing forty controls that fire on the wrong person at an
# unpredictable moment — and this surface makes that specifically dangerous, because **no chat
# client handles a 429 anywhere**: grepping `429|Retry-After|backoff|RateLimit` across
# `public/js/chat`, `www/chat-sw.js` and `chat_surface.js` returns nothing, while the pattern
# exists one module over at `public/js/fountain_move/fountain_move.js:1102-1108`.
#
# Four of the refusal paths are silent, and three of those are lossy:
#
#   * `presence.heartbeat` swallows the error, so the tab ages out of presence at the 55s TTL
#     and the visible symptom is **notification spam**, not an error;
#   * `readstate.mark_read` advances its `emitted` cursor **before** the POST, so a refused
#     read-mark is lost until strictly newer traffic arrives;
#   * `notifications.api.push_config` memoises the failed *promise*, so one refusal disables
#     push for that tab for the rest of its session;
#   * `rooms.get_room` throws before the `focus()` call that suppresses repeat navigation, so
#     the refusal **removes the thing that was preventing the burst that tripped it**, and
#     leaves the tab joined to no socket room without knowing.
#
# So G6-5 is satisfied here exactly as it is written — *"rate limited **or** explicitly exempt
# with a reason"* — and the reasons are load-bearing rather than a shrug. What would change
# the answer is named in :data:`RATE_LIMIT_PREREQUISITES`, in the order it has to happen.

#: Endpoints carrying a real limit today. Checked against the source: an entry here whose
#: function has no ``@rate_limit`` decorator, or a decorated function missing from here,
#: fails ``test_chat_endpoint_surface.py``.
#:
#: **Every one is an oversight or machine endpoint**, which is the whole reason they are the
#: exceptions. A bucket shared between a handful of auditors is *more* conservative than a
#: per-person one, not less: the failure mode of a collapsed identity there is "the second
#: auditor waits", where on the SPA surface it is "everybody's chat breaks".
RATE_LIMIT: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.gchat.webhook.handle": (
		"600/60s. `allow_guest=True`, so this is the one endpoint with no session behind it "
		"and the JWT is the only gate. Google is the caller and does not share this office's "
		"egress, so the per-IP bucket is closer to meaningful here than anywhere else."
	),
	f"{DOTTED_ROOT}.governance.export_runner.request_export": (
		"20/3600s. An export is a human decision that takes minutes to satisfy and produces a "
		"bundle that leaves the building. Deliberately NOT converted to a short window: the "
		"long lockout is the intended behaviour for the one endpoint that manufactures "
		"downloadable transcripts."
	),
	f"{DOTTED_ROOT}.governance.tombstone.expand": (
		"60/3600s. Reads a deleted body with **no membership filter**, gated on the oversight "
		"role and a graded reason. The count is auditor ergonomics; the chained audit row is "
		"the control. Left at the shipped value: widening it to 600/60s was considered and "
		"refused, because the office-shares-a-bucket argument does not apply to somebody "
		"outside the office, and for them it is a 600x loosening of the endpoint that returns "
		"deleted message bodies."
	),
	f"{DOTTED_ROOT}.governance.viewer.search": (
		"120/3600s. The only caller of the one function permitted to open the membership "
		"hatch, and it returns bodies. Same reasoning as `tombstone.expand`, and left at the "
		"shipped value for the same reason."
	),
	f"{DOTTED_ROOT}.governance.viewer.access_log": (
		"240/3600s. Reads the audit record rather than content, so a higher count than its "
		"siblings; still an oversight surface with a handful of legitimate callers."
	),
}

#: The four arguments for an exemption. Named rather than repeated per endpoint, because
#: there are only four and repeating them would hide how few.
_CLIENT_PACED: Final[str] = (
	"Client-paced. The SPA calls this on a timer, on a keystroke, or once per inbound realtime "
	"message, so any honest ceiling is a whole-office aggregate — and a collapsed per-IP "
	"identity makes that ceiling fire on whoever happens to share the load balancer address, "
	"not on the runaway tab. No client handles the refusal."
)
_INTERACTIVE_READ: Final[str] = (
	"An interactive read the SPA issues in direct response to a person acting — opening a "
	"room, scrolling back, resyncing after a reconnect. Its rate is bounded by what a human "
	"does, and a refusal is invisible to the client, so a limit costs availability and buys a "
	"bound the identity cannot enforce anyway."
)
_HUMAN_WRITE: Final[str] = (
	"A deliberate human write. A person cannot reach a useful ceiling by hand, and the limit "
	"that would stop a script is exactly the case a caller-controlled identity cannot see — "
	"one rotated header and the bucket is fresh."
)
_ADMIN_GATED: Final[str] = (
	"Gated on a role rather than a rate: the `only_for` check refuses the call before any work "
	"happens. A rate limit on an endpoint that already refuses everybody without the role "
	"bounds how fast a System Manager can operate their own plumbing."
)

#: Endpoints with no limit, each with the reason. **Set equality against the discovered
#: surface is asserted**, so a new endpoint fails the build until somebody classifies it —
#: the same mechanism, and the same reason, as ``MUTATING``/``NON_MUTATING`` above.
RATE_LIMIT_EXEMPT: Final[dict[str, str]] = {
	# --- client-paced: timers, keystrokes, and one call per inbound message ---------
	f"{DOTTED_ROOT}.api.presence.heartbeat": (
		_CLIENT_PACED + " Worst on the surface: the Desk bubble starts its interval from the "
		"constructor, so this runs for people who never open chat, and blur/focus/"
		"visibilitychange each fire an unthrottled extra beat."
	),
	f"{DOTTED_ROOT}.api.presence.set_typing": _CLIENT_PACED,
	f"{DOTTED_ROOT}.api.presence.goodbye": _CLIENT_PACED,
	f"{DOTTED_ROOT}.api.readstate.mark_read": (
		_CLIENT_PACED + " And lossy on refusal: the batcher advances its `emitted` cursor "
		"before the POST, so a refused mark is lost until strictly newer traffic arrives."
	),
	f"{DOTTED_ROOT}.api.readstate.get_read_marks": _CLIENT_PACED,
	f"{DOTTED_ROOT}.api.readstate.get_unread_state": _CLIENT_PACED,
	f"{DOTTED_ROOT}.api.history.get_messages": (
		_CLIENT_PACED + " Its rate is driven by **other people**: one call per inbound "
		"realtime message per open tab, so it scales with senders times viewers rather than "
		"with headcount."
	),
	f"{DOTTED_ROOT}.api.rooms.get_rooms": _CLIENT_PACED,
	f"{DOTTED_ROOT}.api.rooms.get_bootstrap": _CLIENT_PACED,
	f"{DOTTED_ROOT}.notifications.api.push_config": (
		_CLIENT_PACED + " And permanently lossy: the client memoises the failed promise, so "
		"one refusal disables push for that tab for the rest of its session, silently."
	),
	# --- interactive reads ----------------------------------------------------------
	f"{DOTTED_ROOT}.api.rooms.get_room": (
		_INTERACTIVE_READ + " **The worst refusal on the surface**: it throws before the "
		"`focus()` call that suppresses repeat keyboard navigation, so the 429 removes the "
		"thing preventing the burst that caused it, and leaves the tab joined to no socket "
		"room with no indication."
	),
	f"{DOTTED_ROOT}.api.rooms.get_members": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.presence.get_presence": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.history.get_thread": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.history.get_message_context": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.conversations.search_people": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.mentions.search_mention_targets": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.api.search.search_messages": (
		_INTERACTIVE_READ + " It is the most expensive read on the surface and would be the "
		"best candidate for a limit if the identity were sound — recorded here as the first "
		"endpoint to revisit once it is."
	),
	f"{DOTTED_ROOT}.notifications.api.explain": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.sync.attachments.download": (
		_INTERACTIVE_READ + " Note it has **no caller today**: every surface renders "
		"attachments from `file_url` straight at `/private/files/`, so a limit here would "
		"bound traffic that does not exist."
	),
	f"{DOTTED_ROOT}.governance.export_runner.download_export": (
		'A navigation GET, so `methods=["POST"]` — the shape copied from its four governance '
		"siblings — would make the decorator inert while passing a presence check. Its own "
		"audit row is written before the bytes are attached, and that record is the control."
	),
	f"{DOTTED_ROOT}.governance.viewer.my_access_log": (
		"A person reading who looked at their own messages. Rate-limiting the transparency "
		"view is the one place on this surface where a refusal has a governance cost as well "
		"as an availability one."
	),
	f"{DOTTED_ROOT}.retrieval.api.get_chat_context": (
		"Called by Triton for one turn of one conversation, and already bounded upstream by "
		"the assistant's own turn rate and by the retrieval gate's token ceiling."
	),
	f"{DOTTED_ROOT}.api.compose.prepare_upload": _INTERACTIVE_READ,
	f"{DOTTED_ROOT}.invoke.triton_link.bot_credential_status": _ADMIN_GATED,
	# --- human writes ---------------------------------------------------------------
	f"{DOTTED_ROOT}.api.compose.send_message": (
		_HUMAN_WRITE + " And a refusal is the one this surface can least afford: the offline "
		"queue drain sits inside the same `try` as the resync reads, so a refusal upstream of "
		"it skips the drain entirely and shows a reconnecting banner over a healthy socket."
	),
	f"{DOTTED_ROOT}.api.compose.edit_message": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.api.compose.delete_message": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.api.conversations.create_group": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.api.conversations.create_direct_message": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.api.readstate.mark_all_read": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.api.rooms.set_last_open_room": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.notifications.api.subscribe": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.notifications.api.unsubscribe": _HUMAN_WRITE,
	f"{DOTTED_ROOT}.notifications.api.read_from_notification": _HUMAN_WRITE,
	# --- role-gated plumbing ---------------------------------------------------------
	f"{DOTTED_ROOT}.invoke.triton_link.link_bot_credentials": _ADMIN_GATED,
	f"{DOTTED_ROOT}.sync.outbound.retry_relay_job": _ADMIN_GATED,
	f"{DOTTED_ROOT}.sync.provisioning.create_document_room": _ADMIN_GATED,
	f"{DOTTED_ROOT}.sync.provisioning.enroll_org_units": _ADMIN_GATED,
	f"{DOTTED_ROOT}.sync.provisioning.start_org_mirror": _ADMIN_GATED,
}

#: What has to be true before the exemptions above stop being the right answer, in the order
#: it has to happen. Each is smaller than the forty decorators it unblocks.
RATE_LIMIT_PREREQUISITES: Final[tuple[str, ...]] = (
	"1. The clients handle a 429. Today none of them does, and four refusal paths are silent "
	"— three of those lossy. The branch already exists at "
	"`public/js/fountain_move/fountain_move.js:1102-1108` and is smaller than any limit it "
	"would make safe. Until this lands, every limit added here degrades invisibly.",
	"2. The identity stops being caller-controlled. Either the edge is configured to overwrite "
	"rather than append `X-Forwarded-For` and the trusted-hop count is established "
	"(TASK-2026-01478), or the limiter stops keying on the IP at all. Frappe offers no "
	"per-user mode, so the second option means a counter of our own over `frappe.cache` keyed "
	"on `frappe.session.user` — server-derived and unforgeable, and roughly thirty lines "
	"reusing the fixed-window arithmetic already in `chat/sync/ratelimit.py`.",
	"3. The numbers are sized from server cost, not client cadence. `presence.heartbeat` does "
	"O(room members) Redis reads per beat, so a ceiling picked from how often browsers call it "
	"can sit far above the point at which the server is already down — which is not a ceiling.",
)


# --- §4.J: the pilot gate ---------------------------------------------------------
#
# *"Pilot gating is enforced server-side on every chat endpoint. Hiding a button is not a
# rollout control — a non-pilot user holding a deep link must be refused by the server."*
#
# The gate is `api/_common.require_session`, which refuses Guest, refuses when
# `Chat Settings.enabled` is off, and refuses anyone `is_user_allowed` says is outside the
# pilot. `require_room` and `require_message` call it, so most endpoints inherit it.
#
# **This dict exists because a transitive-reach scan found fourteen endpoints that did not.**
# Thirteen were correct — Google's webhook, the role-gated plumbing, the oversight surface —
# and one was not: `sync.attachments.download` enforced membership but neither the pilot
# whitelist nor the master switch. Fixed in v1.294.0. The scan is now a test, so the next
# endpoint that misses the gate fails the build instead of being found by a later audit.

#: The two arguments an exemption can rest on, named once each.
_OVERSIGHT_GATE: Final[str] = (
	"Gated on the oversight role and a graded reason rather than the pilot. An auditor is not "
	"necessarily a pilot member, and the governance obligation deliberately outlives the "
	"rollout flag: the moment somebody most needs to review what was said is often after the "
	"feature has been switched off."
)
_PLUMBING_GATE: Final[str] = (
	"Gated on `only_for('System Manager')` rather than the pilot. These are operator actions "
	"on the plumbing, not chat use — an administrator repairing the relay is not making a "
	"claim to be in the pilot, and requiring it would mean a rollback switch could lock the "
	"operator out of the controls they need to finish the rollback."
)

#: Endpoints that deliberately do NOT go through `require_session`, and why. **Set equality
#: against the discovered surface is asserted**, so an endpoint that stops reaching the gate
#: has to be classified here rather than silently joining the exempt set.
PILOT_GATE_EXEMPT: Final[dict[str, str]] = {
	f"{DOTTED_ROOT}.gchat.webhook.handle": (
		"Google is the caller, not a person. `allow_guest=True` and the JWT is the gate. Asking "
		"whether Google is in the pilot whitelist is not a question with an answer."
	),
	f"{DOTTED_ROOT}.governance.viewer.search": _OVERSIGHT_GATE,
	f"{DOTTED_ROOT}.governance.viewer.access_log": _OVERSIGHT_GATE,
	f"{DOTTED_ROOT}.governance.tombstone.expand": _OVERSIGHT_GATE,
	f"{DOTTED_ROOT}.governance.export_runner.request_export": _OVERSIGHT_GATE,
	f"{DOTTED_ROOT}.governance.export_runner.download_export": _OVERSIGHT_GATE,
	f"{DOTTED_ROOT}.governance.viewer.my_access_log": (
		"The transparency view: a person reading who looked at *their own* messages. Gating it "
		"on the pilot would mean somebody removed from the pilot — or reading after the feature "
		"was switched off — could no longer see who had read them, which is the moment they are "
		"most likely to want to. The governance obligation outlives the rollout flag."
	),
	f"{DOTTED_ROOT}.invoke.triton_link.bot_credential_status": _PLUMBING_GATE,
	f"{DOTTED_ROOT}.invoke.triton_link.link_bot_credentials": _PLUMBING_GATE,
	f"{DOTTED_ROOT}.sync.outbound.retry_relay_job": _PLUMBING_GATE,
	f"{DOTTED_ROOT}.sync.provisioning.create_document_room": _PLUMBING_GATE,
	f"{DOTTED_ROOT}.sync.provisioning.enroll_org_units": _PLUMBING_GATE,
	f"{DOTTED_ROOT}.sync.provisioning.start_org_mirror": _PLUMBING_GATE,
}


def pilot_gated(package: pathlib.Path | None = None) -> set[str]:
	"""Dotted names that reach ``require_session`` — directly or through a helper.

	A transitive walk over same-module calls, bounded in depth, because the gate is usually
	reached through ``require_room``/``require_message`` rather than called outright. It is a
	source-level proxy for "this endpoint refuses a non-pilot caller", and a good one: the
	three gate functions are the only things that consult ``is_user_allowed``.

	What it cannot see, stated so a green run is not over-read: a call through a variable, a
	gate reached only on one branch, or a helper in a different module. The behavioural half is
	the bench suite.
	"""
	root = (package or CHAT_PACKAGE).resolve()
	gates = {"require_session", "require_room", "require_message"}
	bodies: dict[tuple[str, str], set[str]] = {}

	for path in sorted(root.rglob("*.py")):
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (OSError, SyntaxError):  # pragma: no cover - unreadable file
			continue
		rel = path.relative_to(root).as_posix()
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef):
				bodies[(rel, node.name)] = _called_names(node)

	def reaches(rel: str, fn: str, seen: set[tuple[str, str]], depth: int) -> bool:
		if depth > 6 or (rel, fn) in seen:
			return False
		seen.add((rel, fn))
		called = bodies.get((rel, fn), set())
		if called & gates:
			return True
		return any((rel, name) in bodies and reaches(rel, name, seen, depth + 1) for name in called)

	out: set[str] = set()
	for dotted, endpoint in discover(root).items():
		rel = endpoint.relpath.replace("\\", "/")
		if reaches(rel, dotted.rsplit(".", 1)[1], set(), 0):
			out.add(dotted)
	return out


def _called_names(node: ast.AST) -> set[str]:
	names: set[str] = set()
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			target = inner.func
			names.add(target.id if isinstance(target, ast.Name) else getattr(target, "attr", ""))
	return names


def rate_limit_classified() -> dict[str, str]:
	"""Every endpoint's rate-limit decision, limited and exempt together.

	The union is what ``test_chat_endpoint_surface.py`` compares against the filesystem, so an
	endpoint that appears in neither dict — or in both — fails the build.
	"""
	return {**RATE_LIMIT, **RATE_LIMIT_EXEMPT}


def gated_by_only_for(package: pathlib.Path | None = None) -> set[str]:
	"""Dotted names of whitelisted functions that call ``frappe.only_for`` in their own body.

	Body-local, deliberately: a role check performed by a helper two frames up is invisible
	here and would report a gated endpoint as ungated. That is the safe direction — a false
	alarm costs one exemption with a reason, while the opposite silently blesses the shape
	this scan exists to catch.
	"""
	root = package or CHAT_PACKAGE
	gated: set[str] = set()

	for path in sorted(root.rglob("*.py")):
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue

		module = _dotted_module(path, root)
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			if not any(_is_whitelist(dec) for dec in node.decorator_list):
				continue
			for inner in ast.walk(node):
				if (
					isinstance(inner, ast.Call)
					and isinstance(inner.func, ast.Attribute)
					and inner.func.attr == "only_for"
				):
					gated.add(f"{module}.{node.name}")
					break
	return gated


def require_room_intents(package: pathlib.Path | None = None) -> dict[str, set[str]]:
	"""For each whitelisted function, the ``intent=`` values it passes to ``require_room``.

	A function that never calls ``require_room`` is absent from the mapping; one that calls it
	without an ``intent`` keyword records ``"read"``, which is the parameter's default and
	therefore what the code actually does.

	This exists because ``require_room``'s two intents ask genuinely different questions —
	``read`` is "may this identity *see* the room", which the oversight role may; ``write`` is
	"is this identity *in* the room", which no amount of oversight makes true. The distinction
	is only load-bearing if every mutating endpoint actually asks the second one, and the only
	way to know that without a bench is to read the call.
	"""
	root = package or CHAT_PACKAGE
	intents: dict[str, set[str]] = {}

	for path in sorted(root.rglob("*.py")):
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue

		module = _dotted_module(path, root)
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			if not any(_is_whitelist(dec) for dec in node.decorator_list):
				continue

			for inner in ast.walk(node):
				if not isinstance(inner, ast.Call):
					continue
				func = inner.func
				name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
				if name != "require_room":
					continue

				intent = "read"
				for keyword in inner.keywords:
					if keyword.arg == "intent":
						intent = str(getattr(keyword.value, "value", "")) or "read"
				intents.setdefault(f"{module}.{node.name}", set()).add(intent)

	return intents


def discover(package: pathlib.Path | None = None) -> dict[str, Endpoint]:
	"""Every ``@frappe.whitelist()`` in the chat package, from the AST.

	AST rather than import, for the reason every source scan in this repo is an AST scan:
	importing the chat package outside a bench pulls in ``frappe`` and dies, and the tests that
	consume this are bench-free by design because bench-free is the only tier CI runs.

	Matches the decorator by attribute name (``whitelist``), so both ``@frappe.whitelist()``
	and a ``from frappe import whitelist`` form are seen. A bare ``@frappe.whitelist`` with no
	call parentheses is matched too — it is not the house style, but it is legal Python and
	silently missing one would take an endpoint out of the surface this file claims to cover.
	"""
	root = package or CHAT_PACKAGE
	found: dict[str, Endpoint] = {}

	for path in sorted(root.rglob("*.py")):
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			# A file this scanner cannot read is a fact the caller needs, not one to swallow —
			# but raising here would make an unrelated syntax error present as "your endpoint
			# inventory is wrong". The test asserts the count separately.
			continue

		module = _dotted_module(path, root)
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			for decorator in node.decorator_list:
				if not _is_whitelist(decorator):
					continue
				allow_guest, methods = _decorator_args(decorator)
				found[f"{module}.{node.name}"] = Endpoint(
					dotted=f"{module}.{node.name}",
					allow_guest=allow_guest,
					methods=methods,
					relpath=path.relative_to(root).as_posix(),
					lineno=node.lineno,
				)
	return found


def classified() -> dict[str, str]:
	"""Both classification dicts as one mapping, for the set-equality assertion."""
	return {**MUTATING, **NON_MUTATING}


# --------------------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------------------


def _dotted_module(path: pathlib.Path, root: pathlib.Path) -> str:
	parts = path.relative_to(root).with_suffix("").parts
	if parts and parts[-1] == "__init__":
		parts = parts[:-1]
	return ".".join((DOTTED_ROOT, *parts)) if parts else DOTTED_ROOT


def _is_whitelist(decorator: ast.expr) -> bool:
	target = decorator.func if isinstance(decorator, ast.Call) else decorator
	if isinstance(target, ast.Attribute):
		return target.attr == "whitelist"
	if isinstance(target, ast.Name):
		return target.id == "whitelist"
	return False


def _decorator_args(decorator: ast.expr) -> tuple[bool, tuple[str, ...]]:
	if not isinstance(decorator, ast.Call):
		return False, ()

	allow_guest = False
	methods: tuple[str, ...] = ()
	for keyword in decorator.keywords:
		if keyword.arg == "allow_guest":
			allow_guest = bool(getattr(keyword.value, "value", False))
		elif keyword.arg == "methods":
			try:
				value = ast.literal_eval(keyword.value)
			except (ValueError, SyntaxError):
				continue
			if isinstance(value, str):
				methods = (value,)
			elif isinstance(value, list | tuple):
				methods = tuple(str(item) for item in value)
	return allow_guest, methods
