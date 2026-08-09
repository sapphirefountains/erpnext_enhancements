# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Synthetic Google Chat responses for dry-run mode — **pure, deterministic, visibly fake**.

When ``Chat Settings.dry_run_mode`` is on, or the master ``enabled`` switch is off, the
transport performs **no network I/O at all** and returns one of these instead. That is the
mechanism by which this system ships dark: the schema, the relay and the whole outbound
path can be exercised on production data before a single byte reaches Google.

Three properties, each of which a later phase depends on:

**Deterministic.** The same inputs produce the same synthetic resource names. Phase 2's
echo suppression is a probe on ``unique(room, client_message_id)`` and a probe on
``unique(gchat_message_name)`` (ADR 0009 §G.3) — neither can be exercised end to end
against a mode that mints a fresh random name every call. A dry-run replay must collide
exactly where a real replay would.

**Visibly fake.** Every name carries ``DRYRUN-`` and every payload carries a non-Google
``dryRun: true`` key. A ``Chat Message`` row whose ``gchat_message_name`` reads
``spaces/DRYRUN-4F2A…/messages/client-…`` cannot be mistaken for real state by a human
reading the list view, and :func:`is_dryrun_name` gives Phase 2's reconciliation sweep a
one-line test for "skip this row, it was never sent". Without that marker the first live
run would try to reconcile against messages that do not exist in Google and start
repairing state that was never broken.

**Shaped like the real thing.** The keys are the keys the real API returns, so a caller
written against dry-run does not need a second code path when the switch flips. The
values that cannot be faked honestly are constants that announce themselves — the
timestamp is the Unix epoch, the sender is ``users/DRYRUN``.

Nothing here imports ``frappe`` or a network library, so the bench-free CI tier can assert
the determinism directly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Final

# The marker every synthetic identifier carries. Grep-able, and the reason a dry-run row
# can never be silently confused with production state.
DRYRUN_MARKER: Final[str] = "DRYRUN-"
DRYRUN_SPACE_PREFIX: Final[str] = f"spaces/{DRYRUN_MARKER}"

# The Unix epoch, because a plausible-looking timestamp is the one field most likely to
# make a fake row read as real in a report.
DRYRUN_CREATE_TIME: Final[str] = "1970-01-01T00:00:00Z"

DRYRUN_SENDER_NAME: Final[str] = "users/DRYRUN"
DRYRUN_SENDER_DISPLAY: Final[str] = "Dry Run — no message was sent"

# 16 hex characters = 64 bits. Long enough that two distinct rooms cannot collide in any
# realistic org; short enough to read at a glance in a list view.
_HASH_CHARS: Final[int] = 16

# ASCII unit separator: cannot appear in an email address, a room name or a request id,
# so ("a", "bc") and ("ab", "c") cannot hash to the same value.
_SEED_SEPARATOR: Final[str] = "\x1f"


def dryrun_hash(*parts: str) -> str:
	"""Stable uppercase-hex digest of the seed parts. Same inputs, same digest, forever."""
	seed = _SEED_SEPARATOR.join(str(part) for part in parts)
	return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_HASH_CHARS].upper()


def dryrun_space_name(*seed: str) -> str:
	"""``spaces/DRYRUN-<hash>`` — the synthetic space resource name."""
	return f"{DRYRUN_SPACE_PREFIX}{dryrun_hash(*seed)}"


def dryrun_thread_name(space_name: str, *seed: str) -> str:
	"""``<space>/threads/DRYRUN-<hash>`` — synthetic, and rooted in the synthetic space."""
	return f"{space_name}/threads/{DRYRUN_MARKER}{dryrun_hash(space_name, *seed)}"


def is_dryrun_name(name: str | None) -> bool:
	"""True for any Google resource name this module minted.

	Phase 2's reconciliation sweep calls this before comparing a stored
	``gchat_message_name`` against Google. Checking every path segment rather than only
	the prefix means a message name, a thread name and a membership name all answer
	correctly with one function.
	"""
	if not name:
		return False
	return any(segment.startswith(DRYRUN_MARKER) for segment in str(name).split("/"))


def dryrun_space_for(space_name: str) -> str:
	"""Map any space name onto its dry-run twin, idempotently.

	This exists because of a real hole. Once a room has been provisioned for real, its
	``gchat_space_name`` is a genuine ``spaces/AAAA…``; a subsequent dry-run *message* into
	that room would otherwise be handed back as
	``spaces/AAAA…/messages/client-…`` — a resource name that looks completely real and
	would be written straight into ``Chat Message.gchat_message_name``. Phase 2's
	reconciliation would then hunt for a message Google has never heard of.

	Folding the real space through a hash keeps the answer deterministic (the same room
	always maps to the same twin, so ``create`` / ``patch`` / ``get`` agree in dry-run) while
	guaranteeing the ``DRYRUN-`` marker survives into every stored name. Already-synthetic
	names pass through unchanged so the mapping is stable under repetition.
	"""
	name = str(space_name or "")
	if is_dryrun_name(name):
		return name
	return dryrun_space_name(name or "unknown-space")


def synthetic_space(
	*,
	seed: Sequence[str],
	space_type: str,
	display_name: str | None = None,
	member_emails: Sequence[str] = (),
) -> dict[str, Any]:
	"""A ``Space`` resource, as ``spaces.setup`` / ``spaces.findDirectMessage`` return one.

	``seed`` is normally the deterministic ``requestId``, which is derived from the ERPNext
	room name — so two dry-run provisioning attempts for one room return the *same* space,
	which is precisely the exactly-once property the real ``requestId`` buys (ADR §G.2.3).
	Proving it in dry-run is what lets the smoke test's "second run creates no duplicate
	space" assertion be written before anything touches Google.

	``spaceThreadingState`` is reported as ``SPACE_THREADING_STATE_UNSPECIFIED``.
	``VERIFY:`` the real enum's member names — ADR §G.9.4 records that the full
	``SpaceThreadingState`` enum was never seen printed, and this module refuses to invent
	one. Do not branch on this value in dry-run.
	"""
	name = dryrun_space_name(*seed)
	space: dict[str, Any] = {
		"name": name,
		"spaceType": space_type,
		"singleUserBotDm": False,
		"spaceThreadingState": "SPACE_THREADING_STATE_UNSPECIFIED",
		"createTime": DRYRUN_CREATE_TIME,
		"importMode": False,
		"adminInstalled": False,
		# Not a Google field. A second, explicit detector so a consumer that never looks at
		# the resource name still cannot mistake this for real state.
		"dryRun": True,
	}
	if display_name:
		space["displayName"] = display_name
	if member_emails:
		space["dryRunMemberships"] = [f"users/{email}" for email in member_emails]
	return space


def synthetic_message(
	*,
	space_name: str,
	message_id: str,
	text: str = "",
	thread_name: str | None = None,
	sender_email: str | None = None,
) -> dict[str, Any]:
	"""A ``Message`` resource, as ``messages.create`` / ``patch`` / ``get`` return one.

	The resource name is ``<space>/messages/<client-message-id>`` — the caller's own
	deterministic id, not a hash of it. That is deliberate and it is the whole value of
	dry-run for Phase 2: the alias form ``spaces/{space}/messages/{clientAssignedMessageId}``
	is a documented addressing mode (ADR §G.7.2), so a dry-run row can be patched and
	fetched by exactly the name a real row would use.
	"""
	name = f"{space_name}/messages/{message_id}"
	sender: dict[str, Any] = {
		"name": DRYRUN_SENDER_NAME,
		"displayName": DRYRUN_SENDER_DISPLAY,
		"type": "HUMAN",
	}
	if sender_email:
		# Recorded, but under a non-Google key: `sender.name` must stay obviously fake.
		sender["dryRunImpersonatedUser"] = f"users/{sender_email}"

	return {
		"name": name,
		"clientAssignedMessageId": message_id,
		"text": text,
		"argumentText": text,
		"sender": sender,
		"space": {"name": space_name},
		"thread": {"name": thread_name or dryrun_thread_name(space_name, message_id)},
		"createTime": DRYRUN_CREATE_TIME,
		"dryRun": True,
	}


def synthetic_empty() -> dict[str, Any]:
	"""``messages.delete`` returns ``Empty``. So does dry-run — with the marker attached."""
	return {"dryRun": True}


def synthetic_message_list(*, space_name: str) -> dict[str, Any]:
	"""``messages.list``, always empty, and empty is the only honest answer.

	Dry-run has no store to list from, and inventing history would corrupt the one thing
	``list_messages`` is for — Phase 2's reconciliation sweep, which compares Google's view
	against ERPNext's. A sweep run against dry-run must find nothing and conclude nothing,
	not find fiction and act on it. Callers therefore must treat a dry-run sweep as
	"no data", which :func:`is_dryrun_name` on the space lets them detect.
	"""
	return {"messages": [], "nextPageToken": "", "space": space_name, "dryRun": True}


def describe_request(
	*,
	http_method: str,
	path: str,
	query: Mapping[str, str] | None = None,
	body_keys: Sequence[str] = (),
) -> str:
	"""One-line, body-free rendering of the request that would have been sent.

	Body **keys** only — never values. Dry-run is where a developer is most tempted to log
	the whole payload, and the payload is employee chat content; decision #12 audits
	non-participant reads, and a log file holding every message defeats that audit
	whichever mode wrote it.
	"""
	rendered_query = "&".join(f"{key}={value}" for key, value in sorted((query or {}).items()))
	suffix = f"?{rendered_query}" if rendered_query else ""
	keys = ",".join(sorted(body_keys))
	return f"{http_method} {path}{suffix} body_keys=[{keys}]"
