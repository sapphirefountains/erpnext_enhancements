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
