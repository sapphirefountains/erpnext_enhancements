# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The **only** module in this codebase that speaks HTTP to Google. Everything else calls it.

``tests/test_chat_guardrails.py`` asserts that literally — no other module may mention
``chat.googleapis.com``. One module means one place to reason about identity, one place a
credential can appear in a stack frame, one place to change when Google changes, and one
method (:meth:`GoogleChatClient._request`) a test can patch to prove that dry-run mode
touches the network zero times.

Shape of the module, and why
----------------------------
**Every decision is a pure function; the transport is dumb.** Payload construction,
argument validation, filter building, idempotency-key checking, response parsing and log
record assembly are module-level functions that take data and return data. They build a
:class:`GoogleCall` — a frozen description of one HTTP request — which
:meth:`GoogleChatClient._execute` either short-circuits into a synthetic dry-run response
or hands to :meth:`GoogleChatClient._request`, the single choke point that performs I/O.
This is not style. This repo has no Frappe integration-test job (``CLAUDE.md``), so the
pure tier is the *only* tier CI can regression-test; anything that matters and is not pure
is, in practice, unprotected.

``frappe`` and ``requests`` are imported **inside functions**, never at module scope, for
the same reason: the bench-free CI tier installs neither, and the builders must stay
importable there.

One socket, two hosts
---------------------
``chat/gchat/events_client.py`` speaks to a *different* host —
``workspaceevents.googleapis.com`` — and owns that literal the same way this module owns
the Chat one. It does **not** open its own socket: it builds a :class:`GoogleCall` with
``host=`` set and hands it to :meth:`GoogleChatClient.execute`, so there is still exactly
one retry loop, one dry-run branch, one place a bearer token exists and one method to
patch to prove "dry run performs zero network I/O". A second ``_request`` would double
every one of those and halve the value of each.

Raw bytes, and why they are a separate field
--------------------------------------------
``media.upload`` sends a ``multipart/related`` body and ``media.download`` returns file
bytes, so :class:`GoogleCall` carries an explicit :attr:`GoogleCall.raw_body` and
:attr:`GoogleCall.content_type` rather than overloading ``body``. Two reasons, both
operational: ``body`` is what the logger enumerates keys from and what
``Chat Settings.log_message_bodies`` can be made to dump, and neither should ever be able
to reach a file attachment; and a single ``body`` field carrying "sometimes a mapping,
sometimes bytes" is how a JSON serialiser ends up base64-ing an image into a log line.

The rate limit that shapes Phase 2 — read this before writing a relay
---------------------------------------------------------------------
* **1 write per second, per space.** This is the binding limit. It is not implemented
  here: the per-space token bucket belongs to the Phase 2 relay worker, which must
  serialise outbound writes per space. A client that retries in a tight loop against one
  space simply converts a 429 into five 429s.
* **``media.upload`` shares that bucket with ``messages.create``**, so relaying one
  message with an attachment costs **two seconds** of a space's budget, not one.
* Project-wide: **space writes 60/minute**, **membership writes 300/minute**. Bulk org
  provisioning is therefore a throttled, resumable job — not a loop.

Identity, and the decision the human made on 2026-08-08
-------------------------------------------------------
Two identities exist and they are not interchangeable (ADR 0009 §G.7.1):

* **User (domain-wide delegation)** — the coworker relay. Messages are authored by the
  *real person*, with no ``App`` badge. Text only: no ``cardsV2``. Editing or deleting a
  human's message requires impersonating **that** human, because the API's ownership model
  says app auth may only touch messages the app itself created.
* **App** — Triton's replies, and only those.

**CQ-1 was resolved in favour of human attribution.** ``NOTIFICATION_TYPE_SILENT`` and
``createMessageNotificationOptions`` both require **app** authentication, which is
mutually exclusive with authoring as a human. The relay therefore does **not** use them,
and Google Chat's own notification fires for anyone using the native client. That is
expected and documented, not a defect: locked decision #3 now reads *"exactly two
notifications are fired by ERPNext; native Google Chat client users additionally receive
Chat's own."* :func:`build_create_message_call` refuses notification options under user
identity so the trade-off cannot be re-made by accident in Phase 4.

Logging, and what must never reach a log
-----------------------------------------
One structured line per attempt (see :func:`build_log_record`). **Message bodies are never
logged at INFO.** Chat content is employee-private and decision #12 audits non-participant
*reads*; a log file quietly containing every message defeats that audit more completely
than any UI could. What is logged instead is ``text_length``, ``text_bytes`` and a
truncated hash. Body logging exists behind ``Chat Settings.log_message_bodies``, defaults
off, and turning it on emits a warning line on every call saying so.

Every exception raised out of this module uses ``raise … from None``. That is this repo's
scar tissue, not caution: a bare re-raise out of a background job publishes the failing
frames' locals into the Error Log, and the locals here include an ``Authorization: Bearer``
header.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, NoReturn

from erpnext_enhancements.chat.gchat import dryrun, ids
from erpnext_enhancements.chat.gchat.backoff import (
	DEFAULT_BACKOFF_BASE_SECONDS,
	DEFAULT_BACKOFF_CAP_SECONDS,
	DEFAULT_MAX_RETRIES,
	classify_error,
	compute_backoff,
	parse_retry_after,
	should_retry,
)

# The one occurrence of this host in the app. tests/test_chat_guardrails.py asserts it.
CHAT_API_HOST: Final[str] = "https://chat.googleapis.com"
CHAT_API_VERSION: Final[str] = "v1"

# `media.upload` is served from a `/upload` prefix on the same host — Google's standard
# media-upload endpoint shape. Same host, different path root, so the guardrail's
# one-module rule is untouched.
MEDIA_UPLOAD_PATH_PREFIX: Final[str] = "/upload"

# Derived, never re-typed: a Workspace Events subscription names its target as
# `//chat.googleapis.com/spaces/-`, and `chat/gchat/events_client.py` must not contain that
# literal or the guardrail fires. Splitting the constant keeps the host in this module and
# still gives the subscription builder's caller a correct string (see chat_target_resource).
_CHAT_API_AUTHORITY: Final[str] = CHAT_API_HOST.split("://", 1)[-1]

# Google's constraints on a client-assigned id live in `ids.py` and are re-exported, not
# re-stated: two copies of "≤ 63 characters, lowercase, begins with client-" is one copy
# too many, and the one that drifts is always the copy furthest from the derivation.
CLIENT_MESSAGE_ID_PREFIX: Final[str] = ids.CLIENT_MESSAGE_ID_PREFIX
CLIENT_MESSAGE_ID_MAX_LENGTH: Final[int] = ids.CLIENT_MESSAGE_ID_MAX_LENGTH

# spaces:setup accepts up to 49 memberships and the caller is not one of them.
MAX_SETUP_MEMBERSHIPS: Final[int] = 49

# messages.list: default 25, maximum 1000.
DEFAULT_PAGE_SIZE: Final[int] = 25
MAX_PAGE_SIZE: Final[int] = 1000

# spaces.members.list: default 100, maximum 1000. Different defaults from messages.list,
# so they get their own constants rather than a shared "page size" that is wrong for one.
DEFAULT_MEMBER_PAGE_SIZE: Final[int] = 100
MAX_MEMBER_PAGE_SIZE: Final[int] = 1000

# messages.list `filter` supports exactly two fields and nothing else.
LIST_FILTER_FIELDS: Final[frozenset[str]] = frozenset({"createTime", "thread.name"})

# spaces.members.list `filter` supports these two. Used as a coarse "did you filter on
# something real" guard rather than as a parser — see build_list_members_call.
MEMBER_LIST_FILTER_FIELDS: Final[frozenset[str]] = frozenset({"member.type", "role"})

# Membership.role. MEMBERSHIP_ROLE_UNSPECIFIED exists in the enum and is not offered here:
# it is the "not stated" value, and a membership created without a stated role is a
# membership nobody can reason about later.
MEMBERSHIP_ROLES: Final[frozenset[str]] = frozenset({"ROLE_MEMBER", "ROLE_MANAGER"})

JSON_CONTENT_TYPE: Final[str] = "application/json; charset=UTF-8"

# What an attachment is assumed to be when the caller cannot say. Deliberately the inert
# one: guessing `text/plain` or `image/png` on behalf of a caller is how a mislabelled file
# renders as garbage in Chat with nothing in any log to explain it.
DEFAULT_ATTACHMENT_CONTENT_TYPE: Final[str] = "application/octet-stream"

# `media.download` returns bytes, not JSON, and every other method in this module returns a
# dict. Rather than fork the return type — which would fork the retry loop, the logging and
# the dry-run branch with it — the bytes are handed back under these two non-Google keys,
# the same convention `parse_response_body` already uses for `_unparsed` / `_value`.
# Read them with attachment_bytes() / attachment_content_type(), never by hand.
MEDIA_BYTES_KEY: Final[str] = "_media_bytes"
MEDIA_CONTENT_TYPE_KEY: Final[str] = "_media_content_type"

# A multipart boundary and a part's Content-Type are written into the request as raw header
# lines, so a CR, an LF or a NUL arriving from a filename or a mime type is header
# injection. Rejected at the builder rather than sanitised: a caller passing a control
# character in a mime type has a bug worth surfacing.
_HEADER_UNSAFE_RE: Final[re.Pattern[str]] = re.compile(r"[\r\n\x00]")

# 24 hex characters of a content-derived digest. Deterministic, so the upload builder stays
# a pure function a test can assert byte-for-byte; long enough that colliding with the
# payload's own contents needs deliberate effort (and is handled anyway — see
# _multipart_boundary).
_MULTIPART_BOUNDARY_CHARS: Final[int] = 24

# The only updateMask path user (DWD) authentication may set. `cards`, `cardsV2`,
# `accessoryWidgets` are app-auth only; `quotedMessageMetadata` is removal-only.
USER_AUTH_UPDATE_MASK_FIELDS: Final[frozenset[str]] = frozenset({"text"})

# Truncated body fingerprint. Long enough to correlate two log lines about the same
# message, far too short to be a content oracle.
_TEXT_HASH_CHARS: Final[int] = 12

# Matches the sibling module's convention (`chat.gchat.webhook`), so one grep finds the
# whole Google surface's log lines.
_LOGGER_NAME: Final[str] = "chat.gchat.client"

# Anything shaped like a bearer token is stripped from an error string before it can be
# raised, logged, or written into a Chat Relay Job's `last_error`.
_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


class AuthIdentity(str, Enum):
	"""Which of the two Google identities a call is made as. Never a default guess."""

	#: Domain-wide delegation, impersonating a named human. The coworker relay.
	USER = "USER"
	#: The registered Chat app. Triton's replies, and nothing else (CQ-1).
	APP = "APP"


class SpaceType(str, Enum):
	"""``Space.spaceType``. Each value carries different mandatory body constraints."""

	SPACE = "SPACE"
	GROUP_CHAT = "GROUP_CHAT"
	DIRECT_MESSAGE = "DIRECT_MESSAGE"


class MessageReplyOption(str, Enum):
	"""``messages.create?messageReplyOption``.

	Read :class:`ThreadReply` before using any of these. The default value is a trap.
	"""

	#: **Do not pass this.** It starts a new thread AND ignores any thread id supplied.
	UNSPECIFIED = "MESSAGE_REPLY_OPTION_UNSPECIFIED"
	#: The one to use: reply in-thread, degrade to a new thread if the thread is gone.
	REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
	#: Reply or 404. Rejected for the relay: a deleted thread must not lose a message.
	REPLY_MESSAGE_OR_FAIL = "REPLY_MESSAGE_OR_FAIL"


class GoogleChatError(Exception):
	"""Base for every failure this module raises. Carries no credential and no body."""

	def __init__(
		self,
		message: str,
		*,
		status: int | None = None,
		google_status: str = "",
		correlation_id: str = "",
		client_method: str = "",
	) -> None:
		super().__init__(scrub_secrets(message))
		self.status = status
		self.google_status = google_status
		self.correlation_id = correlation_id
		self.client_method = client_method


class GoogleChatAPIError(GoogleChatError):
	"""Google answered, and the answer was not 2xx."""


class GoogleChatTransportError(GoogleChatError):
	"""The request never produced an HTTP status — DNS, TLS, connect or read failure."""


class GoogleChatAuthContractError(GoogleChatError):
	"""``chat/gchat/auth.py`` does not expose the token function this client expects.

	Raised rather than swallowed because the alternative — falling back to an unauthenticated
	request — would produce a 401 that reads like a Google problem.
	"""


@dataclass(frozen=True)
class ThreadReply:
	"""A thread id and its reply option, bound together so they cannot be separated.

	**This is the shape of ADR 0009 §G.9.1's named rule**, and the rule exists because of a
	failure the API will not report. Quoting the reference:
	``MESSAGE_REPLY_OPTION_UNSPECIFIED`` is *"Default. Starts a new thread. **Using this
	option ignores any thread ID or threadKey that's included.**"* So a caller who sets a
	thread and forgets ``messageReplyOption`` does not get an error — they get a **silent
	new top-level thread**, and the mirror quietly loses its thread structure while every
	call returns 200.

	Making the two one parameter object removes the failure by construction: there is no
	way to express "thread without option" in the type. ``tests/test_chat_gchat_client.py``
	asserts that ``create_message`` has no separate thread argument.

	**SETTLED 2026-08-09 — the outbound relay does not thread, and cannot.** What was a
	``VERIFY:`` here is now closed against the live reference, three ways independently: the
	discovery document marks ``Space.spaceThreadingState`` ``"readOnly": true``;
	``spaces.setup`` states verbatim *"Spaces with threaded replies aren't supported."*; and
	``spaces.patch``'s ``updateMask`` enumerates every updatable path and does not include
	it. There is therefore **no create-time decision to get wrong**, because there is no way
	to ask the API for a threaded space at all.

	What that settles, concretely:

	* ``Chat Settings.threading_enabled`` stays **0**, and threading remains an
	  ERPNext-side-only concept — which ``Chat Message.thread_root``'s own field description
	  already anticipated.
	* **No new ``thread`` argument goes anywhere in this module.** This class stays because
	  it is the correct shape for the day the API grows the capability, and because deleting
	  it would delete the explanation; nothing new may take a thread.
	* Decision #5's in-thread Triton replies need re-planning in Phase 5. That is a product
	  decision, not a transport one, and it belongs at the checkpoint rather than here.

	Two traps recorded for whoever revisits this: ``thread.threadKey`` is scoped **per Chat
	app**, so a space also written to by a human or by another app contains threads our
	``threadKey`` cannot address; and ``messageReplyOption`` is *"Only supported in named
	spaces"* and is ignored entirely when responding to a user interaction.

	``VERIFY:`` which ``spaceThreadingState`` an API-created named space actually lands in.
	``GROUPED_MESSAGES`` is the only plausible reading and no document states it, so the
	provisioning path **reads the value back** into ``Chat Room.gchat_threading_state``
	rather than assuming one (PHASE2_VERIFIED.md §1.2, §8.6).
	"""

	thread_name: str
	reply_option: MessageReplyOption = MessageReplyOption.REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD

	def __post_init__(self) -> None:
		if not self.thread_name or not str(self.thread_name).strip():
			raise ValueError("ThreadReply requires a thread name; omit the argument for a new thread.")
		if self.reply_option is MessageReplyOption.UNSPECIFIED:
			raise ValueError(
				"MESSAGE_REPLY_OPTION_UNSPECIFIED ignores the thread id and silently starts a new "
				"thread. Use REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD, or pass no thread at all."
			)


@dataclass(frozen=True)
class GoogleCall:
	"""One fully-described HTTP request to the Chat API. Built purely, executed dumbly.

	Holding the request as data rather than as arguments-in-flight is what lets a test
	assert on a payload without a network stub, and what lets dry-run answer from the same
	description the real path would have sent.
	"""

	client_method: str
	google_method: str
	http_method: str
	path: str
	query: Mapping[str, str] = field(default_factory=dict)
	body: Mapping[str, Any] | None = None
	space_name: str = ""
	#: ``None`` means either identity is acceptable; otherwise the call is refused unless
	#: the client was constructed with this identity.
	requires_identity: AuthIdentity | None = None
	#: The API origin. Defaults to the Chat API; ``gchat/events_client.py`` sets the
	#: Workspace Events one. A field rather than a hard-coded prefix is what lets a second
	#: Google surface reuse this module's one retry loop and one socket instead of growing
	#: a parallel transport nobody would keep in step.
	host: str = CHAT_API_HOST
	#: A pre-encoded request body — ``media.upload``'s ``multipart/related`` payload. Kept
	#: apart from ``body`` so a file's bytes can never be reached by the logger or by
	#: ``Chat Settings.log_message_bodies``; see the module docstring.
	raw_body: bytes | None = None
	content_type: str = JSON_CONTENT_TYPE
	#: ``media.download`` answers with file bytes, not JSON. See :data:`MEDIA_BYTES_KEY`.
	expects_binary: bool = False
	#: Does this call spend one of the space's **1 write per second**? Set explicitly rather
	#: than inferred from the HTTP verb, because two of Google's own corrections turn on it:
	#: membership writes and ``spaces.setup`` are POSTs that consume **no** per-space budget
	#: (PHASE2_VERIFIED.md §2, corrections 1 and 2), and charging them to the space bucket
	#: throttles provisioning roughly 300× harder than the API requires. ``media.upload``
	#: *does* consume one, shared with ``messages.create`` — so an attachment message costs
	#: two.
	consumes_space_write_budget: bool = False
	text_length: int = 0
	text_bytes: int = 0
	text_hash: str = ""
	dry_run_kind: str = "EMPTY"
	dry_run_seed: tuple[str, ...] = ()
	dry_run_display_name: str = ""
	dry_run_space_type: str = ""
	dry_run_message_id: str = ""
	dry_run_thread_name: str = ""
	#: A synthetic response computed by the builder itself, used verbatim in dry-run.
	#: The ``dry_run_kind`` dispatch below covers the Chat resources Phase 1 shipped; a
	#: builder for a resource this module has no template for (a membership, a subscription
	#: on another host) supplies its own rather than teaching :func:`dry_run_response` about
	#: a shape that does not belong to it.
	dry_run_payload: Mapping[str, Any] | None = None

	@property
	def url(self) -> str:
		return f"{self.host}{self.path}"


# --------------------------------------------------------------------------------------
# Pure helpers: validation
# --------------------------------------------------------------------------------------


def scrub_secrets(text: str) -> str:
	"""Remove anything shaped like a bearer token from a string bound for a log or a raise.

	Belt and braces on top of ``raise … from None``: an error body echoed back by an
	intermediate proxy has been known to include the request's own ``Authorization``
	header, and this module's output lands in ``Chat Relay Job.last_error``, which is
	readable by anyone with the DocType.
	"""
	return _BEARER_RE.sub("Bearer [redacted]", str(text or ""))


def validate_client_message_id(message_id: str) -> str:
	"""Enforce Google's three constraints on a client-assigned message id at the boundary.

	Begins with ``client-``; lowercase letters, digits and hyphens only; at most 63
	characters. The derivation lives in :mod:`erpnext_enhancements.chat.gchat.ids` and is
	legal by construction — this check exists for everything that is *not* that function:
	a manual replay, an import-mode backfill, a future caller that formats its own id.

	It is also invariant I3's boundary. An inbound event carrying a ``client-`` id that
	ERPNext issued is *definitionally* our own echo (ADR §G.3.1), and that inference is only
	sound if nothing else can put a ``client-`` id into a space.

	The three checks themselves are :func:`ids.assert_legal_client_message_id`, which is
	where the derivation lives and therefore where the rules belong. This wrapper only adds
	the transport's context to the error, because a ``ValueError`` surfacing from a relay
	job should say which call rejected it.
	"""
	candidate = str(message_id or "")
	try:
		return ids.assert_legal_client_message_id(candidate)
	except ValueError as exc:
		raise ValueError(f"messageId rejected: {exc} (got {candidate!r})") from None


def validate_request_id(request_id: str) -> str:
	"""Require a non-empty, deterministic ``requestId``.

	``VERIFY:`` Google documents neither a character set, a maximum length, nor a
	deduplication *window* for ``requestId`` (ADR §G.2.3). Until that is read from the
	reference, "same requestId returns the same resource" is a useful optimisation and
	**not** the uniqueness guarantee — ``unique(gchat_space_name)`` and
	``unique(linked_doctype, linked_document)`` are. This validator therefore checks only
	what can be checked honestly.
	"""
	candidate = str(request_id or "").strip()
	if not candidate:
		raise ValueError(
			"requestId is required. A retried provisioning or relay job without one creates a "
			"duplicate space or a duplicate message — see ADR 0009 §G.2.3."
		)
	return candidate


def validate_space_name(space: str) -> str:
	"""``spaces/<id>``, and nothing that could smuggle a second path segment into a URL."""
	candidate = str(space or "").strip()
	if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+", candidate):
		raise ValueError(f"Expected a space resource name of the form 'spaces/<id>'; got {candidate!r}.")
	return candidate


def validate_message_name(message_name: str) -> str:
	"""``spaces/<id>/messages/<id>``.

	The message id segment may be either Google's own or the ``client-`` alias — the
	reference documents the substitution explicitly, and it is what makes ``allowMissing``
	usable before ``gchat_message_name`` has ever been written back (ADR §G.7.2).
	"""
	candidate = str(message_name or "").strip()
	if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+/messages/[A-Za-z0-9_.-]+", candidate):
		raise ValueError(
			f"Expected a message resource name of the form 'spaces/<id>/messages/<id>'; got {candidate!r}."
		)
	return candidate


def validate_user_email(email: str) -> str:
	"""A membership id is ``users/{email}``, so the email is interpolated into a resource name.

	Rejecting whitespace and ``/`` is not politeness — it stops a malformed directory entry
	from turning into a different resource path.
	"""
	candidate = str(email or "").strip()
	if not candidate or "@" not in candidate or "/" in candidate or any(c.isspace() for c in candidate):
		raise ValueError(f"Expected a Workspace email address; got {candidate!r}.")
	return candidate


def validate_membership_name(membership_name: str) -> str:
	"""``spaces/<id>/members/<id>``.

	The member segment is Google's own opaque id, not an email — ``users/{email}`` is the
	*request* form for creating a membership, and the resource that comes back is named
	differently. Deleting the wrong one is not recoverable by re-adding: a re-added member
	rejoins with no history and, in a space that was ever set up by us, a new membership id.
	"""
	candidate = str(membership_name or "").strip()
	if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+/members/[A-Za-z0-9_.-]+", candidate):
		raise ValueError(
			f"Expected a membership resource name of the form 'spaces/<id>/members/<id>'; got {candidate!r}."
		)
	return candidate


def validate_media_resource_name(resource_name: str) -> str:
	"""``attachmentDataRef.resourceName`` — the only handle that can download an attachment.

	Google's path template for ``media.download`` is ``v1/media/{resourceName=**}``, and the
	``**`` means the value legitimately contains slashes; it is interpolated into a URL path
	unescaped, so the checks here are about what must **not** be smuggled through it — a
	traversal segment, a leading slash, a query or fragment delimiter, whitespace.

	``VERIFY:`` the exact character set of ``resourceName`` is not documented anywhere — it
	is an opaque token. The set permitted below was widened to cover base64url-ish values
	(``-``, ``_``, ``=``) on the assumption that is what it is; if a real download ever
	fails this validator, widen it against the observed value rather than removing it.

	**Never substitute ``downloadUri`` or ``thumbnailUri`` for this.** Both are human,
	browser-session URLs and are not usable with a bearer token (PHASE2_VERIFIED.md §4).
	"""
	candidate = str(resource_name or "").strip()
	if not candidate:
		raise ValueError("An attachment download needs attachmentDataRef.resourceName; got an empty value.")
	if candidate.startswith("/") or ".." in candidate or not re.fullmatch(r"[A-Za-z0-9_.\-/=]+", candidate):
		raise ValueError(
			f"Expected an opaque attachmentDataRef.resourceName; got {candidate!r}. It is interpolated "
			"into a URL path, so a leading slash, a '..' segment or a '?' would address a different "
			"resource entirely."
		)
	return candidate


def validate_attachment_filename(filename: str) -> str:
	"""The ``filename`` ``media.upload`` records for an attachment.

	A path separator is rejected rather than stripped. ERPNext's ``File`` rows hold a
	``file_name`` that is *usually* a bare name and occasionally a private path, and
	uploading ``/private/files/….pdf`` would publish this server's directory layout into a
	Chat space as the attachment's visible title.
	"""
	candidate = str(filename or "").strip()
	if not candidate:
		raise ValueError("media.upload requires a filename; Chat shows it as the attachment's title.")
	if "/" in candidate or "\\" in candidate or _HEADER_UNSAFE_RE.search(candidate):
		raise ValueError(
			f"filename must be a bare file name with no path separators or control characters; "
			f"got {candidate!r}."
		)
	return candidate


def validate_media_content_type(content_type: str) -> str:
	"""A MIME type safe to write into a raw multipart part header.

	ASCII-only and control-character-free, because :func:`build_multipart_related_body`
	concatenates it into a header line by hand — this is the injection check, not a
	taxonomy check, and no attempt is made to police whether the type is a real one.
	"""
	candidate = str(content_type or "").strip() or DEFAULT_ATTACHMENT_CONTENT_TYPE
	if _HEADER_UNSAFE_RE.search(candidate) or not candidate.isascii():
		raise ValueError(f"Content-Type must be ASCII with no control characters; got {content_type!r}.")
	return candidate


def chat_target_resource(space: str = "-") -> str:
	"""``//chat.googleapis.com/spaces/<id>`` — a Workspace Events subscription's target.

	Lives here, and is a function rather than a constant, for one structural reason: the
	Chat host may appear in exactly one module (``tests/test_chat_guardrails.py``), and the
	subscription builder lives in another. ``chat/gchat/events_client.py`` therefore takes
	the target as an argument and its caller composes it here.

	``spaces/-`` is the wildcard "every space this user is in", which is subscription
	**shape B** from PHASE2_CONTRACT.md §2: one subscription per coworker, **user
	authentication only**. Shape A (``chat.app.*``) needs Marketplace admin-install approval
	and shape C needs Developer Preview plus an Enterprise SKU, so neither is a fallback.
	"""
	target = "-" if str(space or "-").strip() in ("", "-") else validate_space_name(space).split("/", 1)[-1]
	return f"//{_CHAT_API_AUTHORITY}/spaces/{target}"


def message_alias(space: str, client_message_id: str) -> str:
	"""``spaces/{space}/messages/{client-id}`` — address a message by the id we minted.

	Phase 2 patches and deletes through this alias so an edit does not have to wait for
	``gchat_message_name`` to have been written back (ADR §G.7.2).
	"""
	return f"{validate_space_name(space)}/messages/{validate_client_message_id(client_message_id)}"


def text_fingerprint(text: str) -> tuple[int, int, str]:
	"""``(characters, utf-8 bytes, truncated sha256)`` — everything a log line may say
	about a message body.

	Bytes rather than characters is the operational number: Google's limit is **32,000
	bytes for the whole message**, so an emoji-heavy message can be a third of the length
	a character count suggests (ADR §G.10).
	"""
	value = text or ""
	encoded = value.encode("utf-8")
	digest = hashlib.sha256(encoded).hexdigest()[:_TEXT_HASH_CHARS]
	return len(value), len(encoded), digest


def message_ids_for(
	erpnext_message_name: str,
	operation: str = "Message Create",
	*,
	site: str | None = None,
) -> tuple[str, str]:
	"""``(messageId, requestId)`` for one ``Chat Message`` — the pair
	:meth:`GoogleChatClient.create_message` demands.

	The derivations are owned by :mod:`erpnext_enhancements.chat.gchat.ids` and are
	deliberately **not** duplicated here. This exists so a caller reaches for the canonical
	pair rather than formatting a string, and so the coupling sits on two lines instead of
	scattered through the relay.

	``site`` is ``frappe.local.site``, read here rather than in ``ids`` because ``ids`` is
	pure by design — the site is in the seed so a staging site relaying into the same
	Workspace mints different ids instead of colliding with production's.

	**Use this for a first derivation only.** ``client_message_id`` is computed once in
	``before_insert`` and stored forever; after that the row is the source of truth, because
	a site rename would change what this function returns (ADR §G.2.2).
	"""
	if site is None:
		import frappe

		site = frappe.local.site

	client_id = ids.client_message_id(erpnext_message_name, site=site)
	derived_request_id = ids.request_id(erpnext_message_name, operation, site=site)
	return validate_client_message_id(client_id), validate_request_id(derived_request_id)


# --------------------------------------------------------------------------------------
# Pure helpers: payload construction
# --------------------------------------------------------------------------------------


def _qbool(value: bool) -> str:
	"""Google's REST transcoding wants lowercase JSON booleans in the query string."""
	return "true" if value else "false"


def header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
	"""Case-insensitive header lookup.

	``requests`` hands back a ``CaseInsensitiveDict``, but :meth:`GoogleChatClient._request`
	normalises to a plain ``dict`` so the transport's return type is boring and stubbable —
	which throws the case-insensitivity away. HTTP header casing is not guaranteed, and a
	``retry-after`` that is missed because it was not spelled ``Retry-After`` costs a 429
	storm rather than a clean wait.
	"""
	if not headers:
		return None
	wanted = name.lower()
	for key, value in headers.items():
		if str(key).lower() == wanted:
			return value
	return None


def build_memberships(
	member_emails: Sequence[str], *, caller_email: str | None = None
) -> list[dict[str, Any]]:
	"""``memberships[]`` for ``spaces:setup``. Up to 49, **excluding the caller**.

	The caller is already a member by virtue of creating the space; including them is a
	400, and the failure reads as a membership problem rather than an off-by-one. Passing
	``caller_email`` turns that into a local ``ValueError`` naming the address.
	"""
	seen: set[str] = set()
	memberships: list[dict[str, Any]] = []
	normalised_caller = (caller_email or "").strip().lower()

	for raw in member_emails or ():
		email = validate_user_email(raw)
		lowered = email.lower()
		if normalised_caller and lowered == normalised_caller:
			raise ValueError(
				f"{email} is the calling (impersonated) user and must be excluded from "
				"spaces:setup memberships — the caller is added implicitly."
			)
		if lowered in seen:
			continue
		seen.add(lowered)
		memberships.append({"member": {"name": f"users/{email}", "type": "HUMAN"}})

	if len(memberships) > MAX_SETUP_MEMBERSHIPS:
		raise ValueError(
			f"spaces:setup accepts at most {MAX_SETUP_MEMBERSHIPS} memberships; got {len(memberships)}. "
			"Create the space, then add the remainder through a throttled membership job "
			"(300 membership writes per minute, project-wide)."
		)
	return memberships


def build_setup_space_call(
	*,
	space_type: SpaceType | str,
	request_id: str,
	member_emails: Sequence[str],
	display_name: str | None = None,
	caller_email: str | None = None,
) -> GoogleCall:
	"""``POST /v1/spaces:setup`` — create a space **and** add its members in one call.

	Preferred over ``spaces.create`` because it is one request, one ``requestId`` and one
	failure mode instead of a create followed by N membership writes that can half-succeed.

	The three space types are three different, mutually exclusive contracts:

	=================  ====================================================================
	``SPACE``          requires ``displayName``
	``GROUP_CHAT``     must **not** set ``displayName``; needs at least 2 memberships
	``DIRECT_MESSAGE`` must **not** set ``displayName``; exactly 1 membership;
	                   ``singleUserBotDm: false``; **returns the existing DM if one exists**
	=================  ====================================================================

	That last property is why :meth:`GoogleChatClient.find_direct_message` is a
	reconciliation step and not a precondition — but calling it first still saves a write
	against the 60-space-writes-per-minute project budget.

	**User authentication is required.** ``spaces:setup`` is not available to the app
	identity, which is also why the caller must be excluded from ``memberships[]``.

	A stable ``requestId`` derived from the ERPNext room name makes provisioning
	exactly-once *within Google's dedup window* — see :func:`validate_request_id` for what
	that qualification is doing there.
	"""
	resolved_type = SpaceType(space_type) if not isinstance(space_type, SpaceType) else space_type
	memberships = build_memberships(member_emails, caller_email=caller_email)
	name = (display_name or "").strip()

	if resolved_type is SpaceType.SPACE:
		if not name:
			raise ValueError("A SPACE requires displayName.")
	else:
		if name:
			raise ValueError(f"A {resolved_type.value} must not set displayName; Google rejects it.")

	if resolved_type is SpaceType.GROUP_CHAT and len(memberships) < 2:
		raise ValueError(f"A GROUP_CHAT needs at least 2 memberships; got {len(memberships)}.")

	if resolved_type is SpaceType.DIRECT_MESSAGE and len(memberships) != 1:
		raise ValueError(
			f"A human-to-human DIRECT_MESSAGE needs exactly 1 membership; got {len(memberships)}."
		)

	space: dict[str, Any] = {"spaceType": resolved_type.value}
	if resolved_type is SpaceType.SPACE:
		space["displayName"] = name
	if resolved_type is SpaceType.DIRECT_MESSAGE:
		# Explicitly false: true would mean a DM between a human and the Chat app, which is
		# a different resource and the wrong one for the coworker relay.
		space["singleUserBotDm"] = False

	checked_request_id = validate_request_id(request_id)
	return GoogleCall(
		client_method="setup_space",
		google_method="spaces.setup",
		http_method="POST",
		path=f"/{CHAT_API_VERSION}/spaces:setup",
		body={"space": space, "requestId": checked_request_id, "memberships": memberships},
		requires_identity=AuthIdentity.USER,
		dry_run_kind="SPACE",
		dry_run_seed=(checked_request_id,),
		dry_run_display_name=name,
		dry_run_space_type=resolved_type.value,
	)


def build_find_direct_message_call(user_email: str) -> GoogleCall:
	"""``GET /v1/spaces:findDirectMessage`` — reconcile to an existing DM before creating one.

	Cheap, and it keeps a retried provisioning job from spending a space write on a DM that
	already exists. ``spaces:setup`` would also return the existing DM, but a read does not
	consume the 60-writes-per-minute space budget.
	"""
	email = validate_user_email(user_email)
	return GoogleCall(
		client_method="find_direct_message",
		google_method="spaces.findDirectMessage",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/spaces:findDirectMessage",
		query={"name": f"users/{email}"},
		requires_identity=AuthIdentity.USER,
		dry_run_kind="SPACE",
		dry_run_seed=("findDirectMessage", email),
		dry_run_space_type=SpaceType.DIRECT_MESSAGE.value,
	)


def build_create_message_call(
	space: str,
	message_id: str,
	request_id: str,
	text: str,
	*,
	thread: ThreadReply | None = None,
	identity: AuthIdentity = AuthIdentity.USER,
	notification_options: Mapping[str, Any] | None = None,
) -> GoogleCall:
	"""``POST /v1/{parent=spaces/*}/messages``.

	``message_id`` and ``request_id`` are **positional and required** — see
	:meth:`GoogleChatClient.create_message` for why they are not kwargs with defaults.

	``thread`` is a :class:`ThreadReply` or nothing. There is no way to pass a thread id
	without its reply option; read that class's docstring for the silent failure it exists
	to prevent.

	``notification_options`` maps to ``createMessageNotificationOptions``, and passing it
	under :attr:`AuthIdentity.USER` raises. The parameter is confirmed to exist and
	confirmed to require **app** authentication — which is mutually exclusive with authoring
	a message as a real human. CQ-1 chose human attribution, so the relay does not use it.
	Plumbing it is correct; shipping the relay on it is not.

	``VERIFY:`` the field *shape* of ``createMessageNotificationOptions`` was never read
	from the reference (ADR §H). It is transcoded here as flat
	``createMessageNotificationOptions.<key>`` query parameters, which is Google's general
	REST rule for a message-typed query parameter — confirm against the reference before
	Phase 4 relies on it, and do not guess a nested shape.
	"""
	parent = validate_space_name(space)
	checked_message_id = validate_client_message_id(message_id)
	checked_request_id = validate_request_id(request_id)

	if notification_options and identity is not AuthIdentity.APP:
		raise ValueError(
			"createMessageNotificationOptions requires app authentication, which stamps the "
			"message as sent by the Chat app. CQ-1 resolved in favour of human attribution, so "
			"the coworker relay must not pass it. Use it only on Triton's app-identity replies."
		)

	body: dict[str, Any] = {"text": text or ""}
	query: dict[str, str] = {"messageId": checked_message_id, "requestId": checked_request_id}

	if thread is not None:
		body["thread"] = {"name": thread.thread_name}
		query["messageReplyOption"] = thread.reply_option.value

	for key, value in (notification_options or {}).items():
		query[f"createMessageNotificationOptions.{key}"] = str(value)

	characters, byte_length, digest = text_fingerprint(text)
	return GoogleCall(
		client_method="create_message",
		google_method="spaces.messages.create",
		http_method="POST",
		path=f"/{CHAT_API_VERSION}/{parent}/messages",
		query=query,
		body=body,
		space_name=parent,
		consumes_space_write_budget=True,
		text_length=characters,
		text_bytes=byte_length,
		text_hash=digest,
		dry_run_kind="MESSAGE",
		dry_run_seed=(parent, checked_message_id),
		dry_run_message_id=checked_message_id,
		dry_run_thread_name=thread.thread_name if thread is not None else "",
	)


def build_patch_message_call(
	message_name: str,
	*,
	text: str,
	allow_missing: bool = True,
	update_mask: Sequence[str] = ("text",),
	identity: AuthIdentity = AuthIdentity.USER,
) -> GoogleCall:
	"""``PATCH /v1/{message.name=spaces/*/messages/*}``.

	``patch``, not ``update``: the reference recommends it, and ``update`` is a PUT.

	``allow_missing=True`` turns an out-of-order or replayed edit into a create rather than
	a 404 — but it **requires a client-assigned message id**, so this refuses the
	combination unless the name addresses the ``client-`` alias. That is the safety net
	under §G.8's *Create-Before-Edit* rule, not its mechanism.

	Under user (DWD) authentication the only settable mask path is ``text``; the card fields
	are app-auth only. And under app authentication you may only patch messages **the app
	created**, so editing a human's message means impersonating that human.
	"""
	name = validate_message_name(message_name)
	mask = tuple(str(path).strip() for path in update_mask if str(path).strip())
	if not mask:
		raise ValueError("updateMask must name at least one field.")

	if identity is AuthIdentity.USER:
		illegal = set(mask) - USER_AUTH_UPDATE_MASK_FIELDS
		if illegal:
			raise ValueError(
				f"User authentication may only patch {sorted(USER_AUTH_UPDATE_MASK_FIELDS)}; "
				f"got {sorted(illegal)}. Card fields require app auth and lose human attribution."
			)

	if allow_missing:
		message_id = name.rsplit("/", 1)[-1]
		if not message_id.startswith(CLIENT_MESSAGE_ID_PREFIX):
			raise ValueError(
				"allowMissing=true requires a client-assigned message id; address the message "
				f"through its client- alias (see message_alias()), not {message_id!r}."
			)

	characters, byte_length, digest = text_fingerprint(text)
	return GoogleCall(
		client_method="patch_message",
		google_method="spaces.messages.patch",
		http_method="PATCH",
		path=f"/{CHAT_API_VERSION}/{name}",
		query={"updateMask": ",".join(mask), "allowMissing": _qbool(allow_missing)},
		body={"text": text or ""},
		space_name=name.split("/messages/")[0],
		consumes_space_write_budget=True,
		text_length=characters,
		text_bytes=byte_length,
		text_hash=digest,
		dry_run_kind="MESSAGE",
		dry_run_seed=(name,),
		dry_run_message_id=name.rsplit("/", 1)[-1],
	)


def build_delete_message_call(
	message_name: str,
	*,
	force: bool = True,
	identity: AuthIdentity = AuthIdentity.USER,
) -> GoogleCall:
	"""``DELETE /v1/{name=spaces/*/messages/*}``.

	``force=true`` also deletes threaded replies. With ``force=false`` the delete **fails**
	if replies exist, which for a mirror means a tombstoned ERPNext message whose Google
	twin is still on screen.

	``force`` **only applies to user authentication**, so passing it under the app identity
	raises here rather than being silently ignored by Google.
	"""
	name = validate_message_name(message_name)
	if force and identity is not AuthIdentity.USER:
		raise ValueError(
			"force=true only applies to user authentication. Under app auth it is ignored, and "
			"a delete with replies then fails — pass force=False explicitly to accept that."
		)

	return GoogleCall(
		client_method="delete_message",
		google_method="spaces.messages.delete",
		http_method="DELETE",
		path=f"/{CHAT_API_VERSION}/{name}",
		query={"force": _qbool(force)},
		space_name=name.split("/messages/")[0],
		consumes_space_write_budget=True,
		dry_run_kind="EMPTY",
		dry_run_seed=(name,),
	)


def build_message_filter(
	*,
	create_time_after: str | None = None,
	create_time_before: str | None = None,
	thread_name: str | None = None,
) -> str:
	"""Build a ``messages.list`` filter from the **only two fields** the API supports.

	``createTime`` (RFC-3339, ``>`` and ``<``, combinable with ``AND``) and ``thread.name``.
	Anything else is a 400. This function is the whole vocabulary of Phase 2's
	reconciliation sweep, which is why it is a builder rather than a free-form string
	argument: a sweep that silently filtered on nothing would compare the wrong window and
	"repair" messages that were never missing.

	Timestamps must be RFC-3339 with an explicit offset — ``2026-08-08T00:00:00Z``. Frappe's
	``now_datetime()`` is **site-local**, not UTC (this app has already shipped one bug from
	that confusion), so convert before formatting rather than after.
	"""
	clauses: list[str] = []
	for operator, value in ((">", create_time_after), ("<", create_time_before)):
		if not value:
			continue
		stamp = str(value).strip()
		if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", stamp):
			raise ValueError(
				f"createTime must be RFC-3339 with an explicit offset, e.g. 2026-08-08T00:00:00Z; "
				f"got {stamp!r}."
			)
		clauses.append(f'createTime {operator} "{stamp}"')

	if thread_name:
		name = str(thread_name).strip()
		if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+/threads/[A-Za-z0-9_-]+", name):
			raise ValueError(f"thread.name must be 'spaces/<id>/threads/<id>'; got {name!r}.")
		clauses.append(f'thread.name = "{name}"')

	return " AND ".join(clauses)


def build_list_messages_call(
	space: str,
	*,
	page_size: int = DEFAULT_PAGE_SIZE,
	page_token: str | None = None,
	filter_expression: str | None = None,
	order_by: str | None = None,
	show_deleted: bool = False,
) -> GoogleCall:
	"""``GET /v1/{parent=spaces/*}/messages`` — the reconciliation sweep's only read.

	``page_size`` defaults to 25 and is capped at 1000 by the API; a value outside that is
	clamped here rather than sent, because a rejected sweep page is a sweep that silently
	does not run.

	Build ``filter_expression`` with :func:`build_message_filter`.
	"""
	parent = validate_space_name(space)
	size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))

	query: dict[str, str] = {"pageSize": str(size), "showDeleted": _qbool(show_deleted)}
	if page_token:
		query["pageToken"] = str(page_token)
	if filter_expression:
		query["filter"] = str(filter_expression)
	if order_by:
		query["orderBy"] = str(order_by)

	return GoogleCall(
		client_method="list_messages",
		google_method="spaces.messages.list",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/{parent}/messages",
		query=query,
		space_name=parent,
		dry_run_kind="MESSAGE_LIST",
		dry_run_seed=(parent,),
	)


def build_get_message_call(message_name: str) -> GoogleCall:
	"""``GET /v1/{name=spaces/*/messages/*}`` — read one message back.

	The smoke test's proof that an edit landed, and Phase 2's way of learning a Google
	resource name for a message it only knows by its ``client-`` alias.
	"""
	name = validate_message_name(message_name)
	return GoogleCall(
		client_method="get_message",
		google_method="spaces.messages.get",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/{name}",
		space_name=name.split("/messages/")[0],
		dry_run_kind="MESSAGE",
		dry_run_seed=(name,),
		dry_run_message_id=name.rsplit("/", 1)[-1],
	)


def build_get_space_call(space: str) -> GoogleCall:
	"""``GET /v1/{name=spaces/*}`` — read one space back.

	Phase 2 needs this for two things and both are "read it, do not assume it".
	Provisioning reads ``spaceThreadingState`` back into ``Chat Room.gchat_threading_state``
	rather than guessing which state an API-created space lands in (see :class:`ThreadReply`
	— the enum member is documented but which one you get is not), and the reconciliation
	sweep uses it to notice a space that has been deleted or renamed outside ERPNext.

	App-authentication capable, so ``requires_identity`` is left open: a sweep does not need
	to impersonate anybody to ask whether a space still exists.

	The dry-run answer folds the space through its deterministic twin, so a
	``spaceThreadingState`` read in dry-run is honest about being synthetic. Do not branch
	on that value in dry-run — ``dryrun.synthetic_space`` deliberately reports
	``SPACE_THREADING_STATE_UNSPECIFIED`` rather than invent an enum member.
	"""
	name = validate_space_name(space)
	return GoogleCall(
		client_method="get_space",
		google_method="spaces.get",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/{name}",
		space_name=name,
		dry_run_kind="SPACE",
		dry_run_seed=(name,),
	)


def build_list_members_call(
	space: str,
	*,
	page_size: int = DEFAULT_MEMBER_PAGE_SIZE,
	page_token: str | None = None,
	filter_expression: str | None = None,
	show_groups: bool = False,
	show_invited: bool = False,
) -> GoogleCall:
	"""``GET /v1/{parent=spaces/*}/members`` — the membership reconciliation sweep's read.

	``filter_expression`` supports exactly ``member.type`` and ``role``
	(:data:`MEMBER_LIST_FILTER_FIELDS`). The check below is a **coarse** one — it asks
	whether the expression mentions a supported field at all, not whether it parses. That
	catches the failure that actually happens, which is a filter copied from
	``messages.list`` naming ``createTime``: Google answers 400, the sweep logs a failure,
	and nobody notices membership has stopped reconciling. It is not a parser and must not
	grow into one; the API is the parser.

	``show_invited`` returns memberships in the ``INVITED`` state, which matters because an
	invited-but-not-joined coworker is *not* a member for relay purposes and would otherwise
	look like a member ERPNext had failed to add.

	``VERIFY:`` the documented interaction between ``show_groups`` and the ``member.type``
	filter. Google Group memberships behave differently from human ones here and the rule
	was not read this session — Phase 2's membership sync only needs humans, so it passes
	``show_groups=False`` and the question stays parked rather than guessed at.
	"""
	parent = validate_space_name(space)
	size = max(1, min(int(page_size or DEFAULT_MEMBER_PAGE_SIZE), MAX_MEMBER_PAGE_SIZE))

	query: dict[str, str] = {
		"pageSize": str(size),
		"showGroups": _qbool(show_groups),
		"showInvited": _qbool(show_invited),
	}
	if page_token:
		query["pageToken"] = str(page_token)
	if filter_expression:
		expression = str(filter_expression).strip()
		if not any(field_name in expression for field_name in MEMBER_LIST_FILTER_FIELDS):
			raise ValueError(
				f"spaces.members.list filters on {sorted(MEMBER_LIST_FILTER_FIELDS)} and nothing else; "
				f"{expression!r} names none of them and would be a 400. A sweep whose filter 400s is a "
				"sweep that silently stops reconciling membership."
			)
		query["filter"] = expression

	return GoogleCall(
		client_method="list_members",
		google_method="spaces.members.list",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/{parent}/members",
		query=query,
		space_name=parent,
		# Empty is the only honest dry-run answer, for the same reason messages.list is
		# empty: a reconciliation that finds fiction acts on it. A caller must treat a
		# dry-run listing as "no data", which dryrun.is_dryrun_name on the room detects.
		dry_run_payload={"memberships": [], "nextPageToken": "", "dryRun": True},
	)


def build_create_membership_call(space: str, member_email: str, *, role: str = "ROLE_MEMBER") -> GoogleCall:
	"""``POST /v1/{parent=spaces/*}/members`` — add one human to a space.

	**User authentication.** The app-authenticated form needs ``chat.app.memberships``,
	which is a Marketplace admin-install scope, and PHASE2_CONTRACT.md §2 rules that whole
	family out: it is not a fallback we can reach for, so a builder that left the identity
	open would only defer a 403 to production.

	**This call does not spend the space's write-per-second budget.** Membership writes
	appear in no per-space bucket at all (PHASE2_VERIFIED.md §2 correction 1); they consume
	the project-wide **300 per minute** membership bucket and nothing else. Charging them to
	the space bucket would throttle a bulk provisioning sweep about 300× harder than the API
	requires, which is why :attr:`GoogleCall.consumes_space_write_budget` is a field and not
	an inference from the HTTP verb.
	"""
	parent = validate_space_name(space)
	email = validate_user_email(member_email)
	membership_role = str(role or "").strip().upper()
	if membership_role not in MEMBERSHIP_ROLES:
		raise ValueError(
			f"role must be one of {sorted(MEMBERSHIP_ROLES)}; got {role!r}. "
			"MEMBERSHIP_ROLE_UNSPECIFIED is deliberately not offered — a membership with no stated "
			"role is one nobody can reason about when reconciliation later disagrees with Chat."
		)

	twin = dryrun.dryrun_space_for(parent)
	return GoogleCall(
		client_method="create_membership",
		google_method="spaces.members.create",
		http_method="POST",
		path=f"/{CHAT_API_VERSION}/{parent}/members",
		body={"member": {"name": f"users/{email}", "type": "HUMAN"}, "role": membership_role},
		space_name=parent,
		requires_identity=AuthIdentity.USER,
		dry_run_payload={
			"name": f"{twin}/members/{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(parent, email)}",
			"state": "JOINED",
			"role": membership_role,
			"member": {"name": f"users/{email}", "type": "HUMAN"},
			"createTime": dryrun.DRYRUN_CREATE_TIME,
			"dryRun": True,
		},
	)


def build_delete_membership_call(membership_name: str) -> GoogleCall:
	"""``DELETE /v1/{name=spaces/*/members/*}`` — remove one member from a space.

	User authentication, same reasoning as :func:`build_create_membership_call`, and the
	same quota note: no per-space budget, only the project-wide 300-per-minute membership
	bucket.

	Removing a member is the one membership operation with a blast radius: they lose the
	space's history in the native client immediately, and re-adding does not give it back.
	``Chat Room.membership_authority`` is what decides whether ERPNext is even allowed to
	drive this direction — it is an explicit field precisely so that removal is never
	inferred from "ERPNext has fewer rows than Chat does".
	"""
	name = validate_membership_name(membership_name)
	return GoogleCall(
		client_method="delete_membership",
		google_method="spaces.members.delete",
		http_method="DELETE",
		path=f"/{CHAT_API_VERSION}/{name}",
		space_name=name.split("/members/")[0],
		requires_identity=AuthIdentity.USER,
		dry_run_payload={"dryRun": True},
	)


def _multipart_boundary(filename: str, content: bytes) -> str:
	"""A deterministic multipart boundary that cannot occur inside the payload.

	Deterministic because the upload builder is a pure function and a random boundary would
	make its output un-assertable — the whole tier that protects this module is the one that
	compares built payloads. Content-derived rather than filename-derived so two uploads of
	the same name do not share a boundary.

	The loop is not decoration. A boundary that appears in the body terminates the part
	early and Google receives a truncated file with a 200; the digest makes that
	astronomically unlikely and a caller uploading a file that literally contains its own
	digest prefix makes it certain, so it is checked rather than reasoned about.
	"""
	digest = hashlib.sha256(b"|".join((filename.encode("utf-8"), content))).hexdigest()
	stem = f"chatupload{digest[:_MULTIPART_BOUNDARY_CHARS]}"
	candidate = stem
	suffix = 0
	while candidate.encode("ascii") in content:
		suffix += 1
		candidate = f"{stem}{suffix}"
	return candidate


def build_multipart_related_body(
	*, metadata: Mapping[str, Any], content: bytes, content_type: str, boundary: str
) -> bytes:
	"""Google's ``uploadType=multipart`` request body: JSON metadata part, then media part.

	Hand-rolled, and deliberately so — this app ships no Google SDK (``CLAUDE.md``: the host
	is a managed server where packages cannot be pip-installed, which is also why
	``stripe_payments`` hand-rolls its webhook signatures). The format is small, fixed and
	fully specified: CRLF line endings, ``--boundary`` before each part, ``--boundary--`` to
	close.

	``uploadType=media`` would be simpler and is wrong: it carries the bytes and nothing
	else, so the attachment would arrive with no ``filename`` and Chat would have nothing to
	title it with.
	"""
	delimiter = f"--{boundary}".encode("ascii")
	return b"".join(
		(
			delimiter,
			b"\r\nContent-Type: ",
			JSON_CONTENT_TYPE.encode("ascii"),
			b"\r\n\r\n",
			json.dumps(dict(metadata), sort_keys=True).encode("utf-8"),
			b"\r\n",
			delimiter,
			b"\r\nContent-Type: ",
			content_type.encode("ascii"),
			b"\r\n\r\n",
			bytes(content),
			b"\r\n",
			delimiter,
			b"--\r\n",
		)
	)


def build_upload_attachment_call(
	space: str,
	filename: str,
	*,
	content: bytes,
	content_type: str = DEFAULT_ATTACHMENT_CONTENT_TYPE,
) -> GoogleCall:
	"""``POST /upload/v1/{parent=spaces/*}/attachments:upload?uploadType=multipart``.

	**User authentication only, and this is a hard property of the API rather than a policy
	of ours.** ``media.upload``'s scope list is exactly ``chat.import``, ``chat.messages``
	and ``chat.messages.create``. ``chat.bot`` — the app-authentication scope — is *absent*,
	so **no app-auth token can create an attachment at all** (PHASE2_VERIFIED.md §4). The
	relay must impersonate the sending author, which it already does for the message itself,
	and the DWD grant holds ``chat.messages``.

	The mirror-image asymmetry is :func:`build_download_media_call`, which *is* app-auth
	capable — and the asymmetry runs in the direction this design needs, since ERPNext
	ingests far more attachments than it sends.

	**Costs one of the space's writes per second, shared with ``messages.create``** — so
	relaying one message with one attachment costs **two** seconds of that space's budget,
	not one. That is why :attr:`GoogleCall.consumes_space_write_budget` is set here.

	``content`` is a **required keyword argument**. A multipart body cannot be built without
	the bytes, and an "optional" content parameter defaulting to empty is how a zero-byte
	attachment gets uploaded by a caller who forgot to read the file — a failure that
	returns 200 and produces an unopenable attachment.

	The response is an ``UploadAttachmentResponse`` carrying
	``attachmentDataRef.attachmentUploadToken``. Turning that token into a message is a
	*separate* call and is **not** implemented here; see the note in this module's Phase 2
	hand-off, because the ``Message.attachment`` field's writability was not verified and
	this module does not guess at Google's shapes.
	"""
	parent = validate_space_name(space)
	name = validate_attachment_filename(filename)
	media_type = validate_media_content_type(content_type)

	if not isinstance(content, bytes | bytearray):
		raise ValueError(f"media.upload needs the file's bytes; got {type(content).__name__}.")
	payload = bytes(content)
	if not payload:
		raise ValueError(
			f"Refusing to upload {name!r} with zero bytes. Google accepts it and Chat then shows an "
			"attachment nobody can open, which is indistinguishable from a delivery failure at the "
			"only moment anyone looks."
		)

	boundary = _multipart_boundary(name, payload)
	token = f"{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(parent, name, str(len(payload)))}"
	return GoogleCall(
		client_method="upload_attachment",
		google_method="media.upload",
		http_method="POST",
		path=f"{MEDIA_UPLOAD_PATH_PREFIX}/{CHAT_API_VERSION}/{parent}/attachments:upload",
		query={"uploadType": "multipart"},
		space_name=parent,
		requires_identity=AuthIdentity.USER,
		raw_body=build_multipart_related_body(
			metadata={"filename": name}, content=payload, content_type=media_type, boundary=boundary
		),
		content_type=f"multipart/related; boundary={boundary}",
		consumes_space_write_budget=True,
		dry_run_payload={
			"attachmentDataRef": {"resourceName": token, "attachmentUploadToken": token},
			"dryRun": True,
		},
	)


def build_download_media_call(resource_name: str) -> GoogleCall:
	"""``GET /v1/media/{resourceName}?alt=media`` — fetch an inbound attachment's bytes.

	**App-authentication capable**, and that is the finding the whole attachment design
	rests on: ``media.download``'s scope list *includes* ``chat.bot``, unlike
	``media.upload``'s. ERPNext can therefore ingest an attachment without impersonating
	whoever sent it — which matters, because the sender of an inbound attachment is often
	someone we hold no DWD mandate for.

	``requires_identity`` is left open rather than pinned to ``APP``: the user identity can
	download too, and a relay already holding a user client should not have to build a
	second one.

	**Never use ``downloadUri`` or ``thumbnailUri``.** They are documented as human,
	browser-session URLs and do not accept a bearer token — a download built on them works
	when a developer pastes it into a logged-in browser and fails in every job.

	The response is bytes, not JSON. It comes back under :data:`MEDIA_BYTES_KEY`; read it
	with :func:`attachment_bytes`.
	"""
	resource = validate_media_resource_name(resource_name)
	return GoogleCall(
		client_method="download_media",
		google_method="media.download",
		http_method="GET",
		path=f"/{CHAT_API_VERSION}/media/{resource}",
		query={"alt": "media"},
		expects_binary=True,
		# Zero bytes, marked. Dry-run has nothing to hand back and inventing a file would be
		# worse than an empty one: it would be written to disk as a real attachment.
		dry_run_payload={
			MEDIA_BYTES_KEY: b"",
			MEDIA_CONTENT_TYPE_KEY: DEFAULT_ATTACHMENT_CONTENT_TYPE,
			"dryRun": True,
		},
	)


# --------------------------------------------------------------------------------------
# Pure helpers: dry-run dispatch, response parsing, log records
# --------------------------------------------------------------------------------------


def dry_run_response(call: GoogleCall, *, subject: str | None = None) -> dict[str, Any]:
	"""The synthetic answer for a call that will never be sent. Pure and deterministic.

	Every resource name is folded through :func:`~erpnext_enhancements.chat.gchat.dryrun.dryrun_space_for`,
	including when the caller addressed an **already-provisioned, genuinely real** space.
	That case is the one that matters: a room whose ``gchat_space_name`` is real, written to
	while ``dry_run_mode`` is on, must not hand back a message name that a later
	reconciliation sweep would try to find in Google.

	A builder may also carry its own :attr:`GoogleCall.dry_run_payload`, and that wins. It
	is how a resource this module holds no template for — a membership, an attachment ref, a
	subscription on another host — stays synthetic without teaching this dispatcher a shape
	that belongs to a different resource family.
	"""
	if call.dry_run_payload is not None:
		return dict(call.dry_run_payload)
	if call.dry_run_kind == "SPACE":
		return dryrun.synthetic_space(
			seed=call.dry_run_seed,
			space_type=call.dry_run_space_type or SpaceType.SPACE.value,
			display_name=call.dry_run_display_name or None,
		)
	if call.dry_run_kind == "MESSAGE":
		space_name = dryrun.dryrun_space_for(call.space_name or dryrun.dryrun_space_name(*call.dry_run_seed))
		thread_name = call.dry_run_thread_name
		return dryrun.synthetic_message(
			space_name=space_name,
			message_id=call.dry_run_message_id or dryrun.dryrun_hash(*call.dry_run_seed),
			text=(call.body or {}).get("text", "") if call.body else "",
			thread_name=dryrun.dryrun_thread_name(space_name, thread_name) if thread_name else None,
			sender_email=subject,
		)
	if call.dry_run_kind == "MESSAGE_LIST":
		return dryrun.synthetic_message_list(space_name=dryrun.dryrun_space_for(call.space_name))
	return dryrun.synthetic_empty()


def parse_response_body(raw: str | bytes | None) -> dict[str, Any]:
	"""Parse a Chat API response body, tolerating the empty ones.

	``messages.delete`` returns ``Empty`` — a literal ``{}`` at best and a zero-length body
	at worst — and a 502 from an intermediate proxy returns HTML. Neither is an exception:
	the status code already said what happened, and raising a ``JSONDecodeError`` here would
	mask a perfectly legible 502 behind a parser error.
	"""
	if raw is None:
		return {}
	text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
	if not text.strip():
		return {}
	try:
		parsed = json.loads(text)
	except ValueError:
		return {"_unparsed": scrub_secrets(text[:500])}
	return parsed if isinstance(parsed, dict) else {"_value": parsed}


def _response_bytes(response: Any) -> bytes:
	"""The raw body of a transport response, without requiring it to expose ``.content``.

	``requests`` always has ``.content``; the fake Chat harness's response double is only
	contracted to expose ``.status_code``, ``.text`` and ``.headers``
	(``PHASE2_INTERFACES.md §8``), and a transport helper that hard-depends on a fourth
	attribute quietly makes the fake untestable against the download path. ``surrogateescape``
	on the fallback so a text-shaped double carrying non-UTF-8 bytes round-trips rather than
	raising inside a background job.
	"""
	raw = getattr(response, "content", None)
	if isinstance(raw, bytes | bytearray):
		return bytes(raw)
	text = getattr(response, "text", "") or ""
	return text.encode("utf-8", "surrogateescape") if isinstance(text, str) else bytes(text)


def attachment_bytes(payload: Mapping[str, Any] | None) -> bytes:
	"""The file bytes from a :meth:`GoogleChatClient.download_media` response.

	An accessor rather than a dictionary lookup at the call site, because the key is a
	non-Google private one and a caller who reaches for ``payload["data"]`` — a plausible
	guess — gets a ``KeyError`` in a background job instead of a file. Returns ``b""`` for a
	dry-run response, which is the same thing dry-run downloads.
	"""
	raw = (payload or {}).get(MEDIA_BYTES_KEY)
	if isinstance(raw, bytes | bytearray):
		return bytes(raw)
	return b""


def attachment_content_type(payload: Mapping[str, Any] | None) -> str:
	"""The ``Content-Type`` Google served an attachment with, or the inert default.

	Worth keeping: it is the only trustworthy statement of what the bytes are. The
	``filename`` recorded on the Chat attachment is whatever the sender's client called it,
	and ERPNext's ``File`` doctype infers a type from the extension — both are guesses about
	content this function does not have to make.
	"""
	value = (payload or {}).get(MEDIA_CONTENT_TYPE_KEY)
	return str(value or "").strip() or DEFAULT_ATTACHMENT_CONTENT_TYPE


def extract_error_status(payload: Mapping[str, Any] | None) -> str:
	"""Google's canonical error code (``PERMISSION_DENIED``, ``RESOURCE_EXHAUSTED``, …).

	Worth far more in a log line than the HTTP status: ``403 PERMISSION_DENIED`` on a
	``spaces:setup`` is a missing DWD scope, while ``403`` alone could be almost anything.
	"""
	error = (payload or {}).get("error")
	if isinstance(error, Mapping):
		return str(error.get("status") or error.get("code") or "")
	return ""


def extract_error_message(payload: Mapping[str, Any] | None) -> str:
	"""Google's human-readable error message, scrubbed and bounded."""
	error = (payload or {}).get("error")
	if isinstance(error, Mapping):
		return scrub_secrets(str(error.get("message") or ""))[:500]
	unparsed = (payload or {}).get("_unparsed")
	return scrub_secrets(str(unparsed or ""))[:500]


def build_log_record(
	*,
	call: GoogleCall,
	correlation_id: str,
	attempt: int,
	status: int | None,
	latency_ms: float,
	dry_run: bool,
	identity: AuthIdentity,
	error_status: str = "",
	exception_type: str = "",
) -> dict[str, Any]:
	"""One structured log line per **attempt**. Pure, so its shape is regression-tested.

	Deliberately absent: the message body, the ``Authorization`` header, the impersonated
	subject's mailbox contents, and the query string (which can carry a ``filter`` naming a
	thread). Present instead: ``text_length``, ``text_bytes`` and a truncated hash — enough
	to correlate two lines about the same message, not enough to reconstruct one.

	``correlation_id`` is per **call**, shared by every attempt of that call, which is what
	makes "this message took four attempts and 9 seconds" a single grep.

	``raw_bytes`` is a **length**, never the bytes. An attachment's content is exactly as
	private as a message body and there is no setting that turns it on: ``raw_body`` is
	unreachable from every logging path in this module by construction, which is the reason
	it is a separate field from ``body`` at all.
	"""
	return {
		"event": "chat_gchat_call",
		"correlation_id": correlation_id,
		"client_method": call.client_method,
		"google_method": call.google_method,
		"http_method": call.http_method,
		"host": call.host,
		"path": call.path,
		"space": call.space_name,
		"raw_bytes": len(call.raw_body or b""),
		"identity": identity.value,
		"status": status,
		"latency_ms": round(float(latency_ms), 1),
		"attempt": attempt,
		"is_retry": attempt > 0,
		"dry_run": dry_run,
		"error_status": error_status,
		"exception": exception_type,
		"text_length": call.text_length,
		"text_bytes": call.text_bytes,
		"text_hash": call.text_hash,
	}


# --------------------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------------------

#: Injection point for tests and for the smoke test. Stated as a named type so the contract
#: between the transport and the auth module is one line rather than an implicit habit.
TokenProvider = Callable[[AuthIdentity, str | None], str]

# The functions `chat/gchat/auth.py` exposes. Resolved by name at call time rather than
# imported at module scope, because `auth` imports `frappe` and this module must stay
# importable in the bench-free CI tier. If neither resolves, the client raises
# GoogleChatAuthContractError naming what it looked for — rather than issuing an
# unauthenticated request and reporting Google's misleading 401.
_USER_TOKEN_FUNCTIONS: Final[tuple[str, ...]] = ("get_delegated_token",)
_APP_TOKEN_FUNCTIONS: Final[tuple[str, ...]] = ("get_app_token",)


class GoogleChatClient:
	"""Authenticated transport for the Google Chat REST API.

	Construct one per operation and per identity; it is cheap and holds no connection.

	    client = GoogleChatClient(subject="alice@example.com")
	    client.create_message(space, message_id, request_id, "hello")

	**The client does not rate-limit.** Google's binding limit is **1 write per second per
	space**, ``media.upload`` shares that bucket with ``messages.create``, and project-wide
	space writes are 60/minute with membership writes 300/minute. Serialising per space is
	the Phase 2 relay worker's job — a token bucket here would be per-process and therefore
	wrong the moment a second worker starts.

	**No ``doc_events`` handler may construct this.** A hook that reaches Google turns the
	one-write-per-second budget into dropped messages during ordinary typing, and
	``tests/test_chat_guardrails.py`` asserts no ``doc_events``-registered chat function
	reaches the transport.
	"""

	def __init__(
		self,
		*,
		subject: str | None = None,
		identity: AuthIdentity = AuthIdentity.USER,
		dry_run: bool | None = None,
		settings: Any | None = None,
		token_provider: TokenProvider | None = None,
		transport: Any | None = None,
		correlation_id: str | None = None,
	) -> None:
		"""``subject`` is the impersonated human and is required for the user identity.

		``dry_run`` overrides the settings-derived value and exists for tests and for the
		smoke test's deliberate live run; leaving it ``None`` means "ask ``Chat Settings``",
		which is the only safe default.

		``token_provider`` and ``transport`` are injection points, so the bench-free suite
		can exercise the retry loop without a Frappe runtime or a socket.
		"""
		if identity is AuthIdentity.USER and not (subject or "").strip():
			raise ValueError(
				"The user identity impersonates a named human — pass subject=<email>. "
				"Falling back to the app identity would stamp the message with an App badge and "
				"silently break human attribution (CQ-1)."
			)

		self.identity: AuthIdentity = identity
		self.subject: str | None = (subject or "").strip() or None
		self.correlation_id: str | None = correlation_id
		self._dry_run: bool | None = dry_run
		self._settings: Any | None = settings
		self._token_provider: TokenProvider | None = token_provider
		self._transport: Any | None = transport
		self._logger: Any | None = None

	# -- configuration ------------------------------------------------------------------

	@property
	def dry_run(self) -> bool:
		"""``True`` when no network I/O may happen — resolved once, then cached.

		Dry-run is on when ``Chat Settings.dry_run_mode`` is set **or** the master
		``enabled`` switch is off. Both conditions live in ``is_chat_enabled()`` so no
		caller has to remember two field names, and this is its exact inverse.
		"""
		if self._dry_run is None:
			from erpnext_enhancements.chat.doctype.chat_settings.chat_settings import is_chat_enabled

			self._dry_run = not is_chat_enabled()
		return bool(self._dry_run)

	def _get_settings(self) -> Any:
		if self._settings is None:
			import frappe

			self._settings = frappe.get_cached_doc("Chat Settings")
		return self._settings

	def _setting(self, fieldname: str, default: float) -> float:
		"""Read a numeric setting defensively.

		``getattr(obj, field, None) or default`` rather than ``doc.field``: this app's own
		convention, because these reads happen during ``bench migrate`` and during ERPNext's
		test bootstrap, when the field may not exist yet. A missing tuning value must degrade
		to a default, never crash a relay worker.
		"""
		try:
			raw = getattr(self._get_settings(), fieldname, None)
		except Exception:
			return float(default)
		if raw in (None, "", 0):
			return float(default)
		try:
			return float(raw)
		except (TypeError, ValueError):
			return float(default)

	@property
	def max_attempts(self) -> int:
		"""Total attempts including the first, from ``Chat Settings.relay_max_attempts``.

		The field counts **attempts**, not retries — Phase 1's prompt and ADR §G speak of
		``max_retries = 5`` while the shipped DocType field is ``relay_max_attempts`` with a
		default of 5. Treating the stored number as attempts is the reading that matches the
		field name; the fallback keeps the documented retry count.
		"""
		return max(1, int(self._setting("relay_max_attempts", DEFAULT_MAX_RETRIES + 1)))

	@property
	def backoff_base_seconds(self) -> float:
		return self._setting("relay_initial_backoff_seconds", DEFAULT_BACKOFF_BASE_SECONDS)

	@property
	def backoff_cap_seconds(self) -> float:
		return self._setting("backoff_cap_seconds", DEFAULT_BACKOFF_CAP_SECONDS)

	@property
	def timeout(self) -> tuple[float, float]:
		"""``(connect, read)``.

		``Chat Settings`` carries a single ``http_timeout_seconds``; Phase 1 §4.5 asks for
		~5s connect and ~30s read. The read timeout is the configured value and the connect
		timeout is a short fixed floor, because a connect that has not completed in five
		seconds is a routing or DNS fault, and waiting the full read budget for it only holds
		a worker slot open.
		"""
		return (5.0, max(self._setting("http_timeout_seconds", 30.0), 1.0))

	@property
	def log_message_bodies(self) -> bool:
		return bool(self._setting("log_message_bodies", 0.0))

	# -- authentication -----------------------------------------------------------------

	def _access_token(self) -> str:
		"""Mint or fetch a cached access token for this client's identity.

		Delegated entirely to :mod:`erpnext_enhancements.chat.gchat.auth` —
		``get_delegated_token(subject)`` for the human relay, ``get_app_token()`` for
		Triton — which owns the keyless domain-wide-delegation flow (assertion claim set →
		IAM Credentials ``signJwt`` → token exchange) and the per-subject cache. This module
		never sees key material and never caches a token, which is what keeps "where can a
		credential exist" a one-module answer.

		Scope selection belongs to ``auth`` too: it defaults to ``RELAY_SCOPES`` /
		``APP_SCOPES``. A wrong scope surfaces as a 403 from Google, which
		:func:`classify_error` deliberately does **not** retry.
		"""
		if self._token_provider is not None:
			return self._token_provider(self.identity, self.subject)

		from erpnext_enhancements.chat.gchat import auth

		candidates = _USER_TOKEN_FUNCTIONS if self.identity is AuthIdentity.USER else _APP_TOKEN_FUNCTIONS
		for name in candidates:
			function = getattr(auth, name, None)
			if callable(function):
				return str(function(self.subject) if self.identity is AuthIdentity.USER else function())

		raise GoogleChatAuthContractError(
			f"chat.gchat.auth exposes none of {list(candidates)} for {self.identity.value}.",
			client_method="_access_token",
		)

	# -- logging ------------------------------------------------------------------------

	def _get_logger(self) -> Any:
		"""Frappe's logger, or ``None`` — a logging failure must never break a relay.

		``VERIFY:`` the exact ``frappe.logger(...)`` signature on v16. Everywhere else this
		repo calls it bare (``frappe.logger()`` / ``frappe.logger("erpnext_enhancements")``)
		and never passes ``allow_site`` or ``file_count``. ``allow_site=True`` is what puts
		the lines in the *site's* log rather than the bench's, which is what an operator
		looking at one site actually wants — so it is attempted first and the bare form is
		the fallback, rather than asserting a signature nobody here has run.
		"""
		if self._logger is None:
			try:
				import frappe

				try:
					self._logger = frappe.logger(_LOGGER_NAME, allow_site=True)
				except TypeError:
					self._logger = frappe.logger(_LOGGER_NAME)
			except Exception:
				self._logger = None
		return self._logger

	def _log_attempt(
		self,
		call: GoogleCall,
		*,
		correlation_id: str,
		attempt: int,
		status: int | None,
		latency_ms: float,
		error_status: str = "",
		exception_type: str = "",
	) -> None:
		"""Emit the attempt line, and never let logging break the call.

		The whole body is defensive: a logger misconfiguration must not turn a delivered
		message into a failed relay job. The cost is that a broken logger is silent, which is
		the correct trade for a transport.
		"""
		record = build_log_record(
			call=call,
			correlation_id=correlation_id,
			attempt=attempt,
			status=status,
			latency_ms=latency_ms,
			dry_run=self.dry_run,
			identity=self.identity,
			error_status=error_status,
			exception_type=exception_type,
		)
		logger = self._get_logger()
		if logger is None:
			return
		try:
			logger.info(json.dumps(record, sort_keys=True, default=str))
			if self.dry_run:
				rendered = dryrun.describe_request(
					http_method=call.http_method,
					path=call.path,
					query=call.query,
					body_keys=tuple((call.body or {}).keys()),
				)
				logger.info(f"chat_gchat dry_run correlation_id={correlation_id} {rendered}")
			if self.log_message_bodies and call.body:
				# Gated, off by default, and loud when on. Decision #12 audits who reads chat
				# content; a log file that quietly holds every message routes around that audit
				# entirely, so enabling it announces itself on every single call.
				logger.warning(
					"chat_gchat log_message_bodies is ON — employee chat content is being "
					f"written to logs (correlation_id={correlation_id})"
				)
				logger.warning(
					f"chat_gchat body correlation_id={correlation_id} "
					f"{json.dumps(call.body, sort_keys=True, default=str)}"
				)
		except Exception:
			return

	# -- transport ----------------------------------------------------------------------

	def _request(
		self,
		*,
		http_method: str,
		url: str,
		params: Mapping[str, str],
		json_body: Mapping[str, Any] | None,
		headers: Mapping[str, str],
		timeout: tuple[float, float],
		raw_body: bytes | None = None,
		expects_binary: bool = False,
	) -> tuple[int, dict[str, Any], Mapping[str, str]]:
		"""**The single choke point that performs I/O.** Nothing else in the app opens a socket.

		Patch this one method and dry-run's "zero network calls" claim becomes a test with
		one assertion — which is exactly how ``tests/test_chat_gchat_client.py`` proves it.
		That claim is only worth making while there is exactly one of these, which is why
		``gchat/events_client.py`` routes its own host through here rather than growing a
		second one.

		``requests`` is imported here rather than at module scope so the pure tier stays
		importable in CI, which installs neither ``frappe`` nor ``requests``. The app already
		depends on ``requests`` at runtime (``stripe_payments``, ``quickbooks_online``); there
		is no new dependency and, per this repo's standing constraint, no SDK.

		``raw_body`` and ``json`` are mutually exclusive and only one is ever passed to the
		session. That is not tidiness: the fake Chat harness
		(``PHASE2_INTERFACES.md §8``) implements ``request(method, url, params, json,
		headers, timeout)``, so a ``data=`` that was always present would break every
		JSON call against it, and only the upload path — which the fake must support anyway
		— has to know about the extra argument.

		**The import happens only when no transport was injected.** An unconditional
		``import requests`` would raise on the bench-free CI tier — which installs neither
		``frappe`` nor ``requests`` — and take the injected-transport path down with it. That
		path is the *only* way the fake harness reaches this method, so an unconditional
		import would quietly mean the retry loop, the byte paths and the ``_request``
		contract are exercised by nothing CI runs.
		"""
		if self._transport is not None:
			session: Any = self._transport
		else:
			import requests

			session = requests

		kwargs: dict[str, Any] = {
			"params": dict(params),
			"headers": dict(headers),
			"timeout": timeout,
		}
		if raw_body is not None:
			kwargs["data"] = raw_body
		else:
			kwargs["json"] = dict(json_body) if json_body is not None else None

		response = session.request(http_method, url, **kwargs)
		status = int(response.status_code)
		response_headers = dict(response.headers or {})

		if expects_binary and 200 <= status < 300:
			# Only on success. A failed download answers with the ordinary AIP-193 JSON
			# error envelope, and routing that through the byte path would turn a legible
			# 403 into an unparsed blob.
			return (
				status,
				{
					MEDIA_BYTES_KEY: _response_bytes(response),
					MEDIA_CONTENT_TYPE_KEY: header_value(response_headers, "Content-Type")
					or DEFAULT_ATTACHMENT_CONTENT_TYPE,
				},
				response_headers,
			)
		return status, parse_response_body(response.text), response_headers

	def execute(self, call: GoogleCall) -> dict[str, Any]:
		"""Run a :class:`GoogleCall` built elsewhere. The seam ``events_client.py`` uses.

		Public only so that a sibling builder module targeting a **different Google host**
		can reuse this module's retry loop, dry-run branch, logging and token handling
		instead of duplicating them. Duplicating them is the actual risk: four behaviours
		that must not drift, in two files nobody diffs against each other.

		Not an invitation for application code to hand-build calls. Everything the Chat API
		can do has a named method below, and a call that has no method is a call nobody has
		written a docstring's worth of constraints for yet.
		"""
		return self._execute(call)

	def _execute(self, call: GoogleCall) -> dict[str, Any]:
		"""Dry-run short-circuit, then the retry loop around :meth:`_request`.

		The dry-run branch sits **above** ``_request`` deliberately: the guarantee being made
		is "no network I/O at all", and a guarantee implemented inside the transport is one
		refactor away from being untrue. It sits above the byte paths too — an upload in
		dry-run neither opens a socket nor reads a credential, and the identical assertion
		covers it because there is only one place to assert about.
		"""
		if call.requires_identity is not None and call.requires_identity is not self.identity:
			raise GoogleChatError(
				f"{call.google_method} requires the {call.requires_identity.value} identity; "
				f"this client is {self.identity.value}.",
				client_method=call.client_method,
			)

		correlation_id = self.correlation_id or uuid.uuid4().hex[:12]

		if self.dry_run:
			self._log_attempt(call, correlation_id=correlation_id, attempt=0, status=None, latency_ms=0.0)
			return dry_run_response(call, subject=self.subject)

		attempt = 0
		while True:
			status: int | None = None
			payload: dict[str, Any] = {}
			headers: Mapping[str, str] = {}
			failure: BaseException | None = None
			started = time.monotonic()
			try:
				status, payload, headers = self._request(
					http_method=call.http_method,
					url=call.url,
					params=call.query,
					json_body=call.body,
					headers={
						"Authorization": f"Bearer {self._access_token()}",
						"Content-Type": call.content_type,
						"X-Correlation-Id": correlation_id,
					},
					timeout=self.timeout,
					raw_body=call.raw_body,
					expects_binary=call.expects_binary,
				)
			except Exception as exc:
				# Broad on purpose: `requests` raises a wide family and the classifier —
				# not this frame — decides retryable from fatal. Nothing is swallowed;
				# every path ends in _raise().
				failure = exc
			latency_ms = (time.monotonic() - started) * 1000.0

			error_status = extract_error_status(payload) if status and status >= 400 else ""
			self._log_attempt(
				call,
				correlation_id=correlation_id,
				attempt=attempt,
				status=status,
				latency_ms=latency_ms,
				error_status=error_status,
				exception_type=type(failure).__name__ if failure is not None else "",
			)

			if failure is None and status is not None and 200 <= status < 300:
				return payload

			decision = classify_error(status, failure)
			if not should_retry(decision, attempt, self.max_attempts):
				self._raise(
					call,
					correlation_id=correlation_id,
					status=status,
					payload=payload,
					failure=failure,
					attempts=attempt + 1,
				)

			time.sleep(
				compute_backoff(
					attempt,
					self.backoff_base_seconds,
					self.backoff_cap_seconds,
					parse_retry_after(header_value(headers, "Retry-After")),
				)
			)
			attempt += 1

	def _raise(
		self,
		call: GoogleCall,
		*,
		correlation_id: str,
		status: int | None,
		payload: Mapping[str, Any],
		failure: BaseException | None,
		attempts: int,
	) -> NoReturn:
		"""Turn a dead attempt into an exception carrying no credential and no message body.

		``from None`` on every path. A bare re-raise out of a background job publishes the
		failing frames' locals into the Error Log, and ``_execute``'s frame holds the
		``Authorization: Bearer`` header — this app has already leaked private key material
		exactly that way once.
		"""
		if failure is not None:
			raise GoogleChatTransportError(
				f"{call.google_method} failed after {attempts} attempt(s): "
				f"{type(failure).__name__}: {failure}",
				correlation_id=correlation_id,
				client_method=call.client_method,
			) from None

		raise GoogleChatAPIError(
			f"{call.google_method} returned HTTP {status} "
			f"{extract_error_status(payload)} after {attempts} attempt(s): "
			f"{extract_error_message(payload)}",
			status=status,
			google_status=extract_error_status(payload),
			correlation_id=correlation_id,
			client_method=call.client_method,
		) from None

	# -- Phase 1: spaces, messages ---------------------------------------------------------

	def setup_space(
		self,
		*,
		space_type: SpaceType | str,
		request_id: str,
		member_emails: Sequence[str],
		display_name: str | None = None,
	) -> dict[str, Any]:
		"""``spaces.setup`` — create a space and add its members in one call.

		See :func:`build_setup_space_call` for the per-space-type body constraints, which are
		enforced before anything is sent. The caller (this client's ``subject``) is excluded
		from ``memberships[]`` automatically-checked, not silently dropped.

		``VERIFY:`` the OAuth scope this needs under DWD. The ADR records ``chat.delete`` as
		a **space** scope and not a message one, but never enumerated the create/setup scopes;
		scope selection belongs to ``auth.py`` and a wrong one surfaces as a 403 that reads
		like a permissions bug.
		"""
		return self._execute(
			build_setup_space_call(
				space_type=space_type,
				request_id=request_id,
				member_emails=member_emails,
				display_name=display_name,
				caller_email=self.subject,
			)
		)

	def find_direct_message(self, user_email: str) -> dict[str, Any]:
		"""``spaces.findDirectMessage`` — reconcile to an existing DM before creating one."""
		return self._execute(build_find_direct_message_call(user_email))

	def create_message(
		self,
		space: str,
		message_id: str,
		request_id: str,
		text: str,
		*,
		thread: ThreadReply | None = None,
		notification_options: Mapping[str, Any] | None = None,
	) -> dict[str, Any]:
		"""``spaces.messages.create`` — post a message.

		**``message_id`` and ``request_id`` are required positional arguments and that is the
		point.** They are the two idempotency keys, and as optional kwargs with defaults they
		would be omitted the first time someone writes a quick retry path — at which point a
		replayed relay job posts a second copy of the message, and inbound echo suppression
		(which keys on the ``client-`` id, ADR §G.3) has nothing to match. Making them
		positional means Phase 2 physically cannot skip them; it is a type error, not a code
		review comment. Derive both with
		:func:`~erpnext_enhancements.chat.gchat.client.message_ids_for`.

		``thread`` is a :class:`ThreadReply`, which carries the thread id and its reply option
		as one value — read that class before using it. Passing no thread posts at top level.

		``notification_options`` requires app authentication and is rejected under the user
		identity; CQ-1 chose human attribution and the relay does not use it.

		Costs **one second** of the space's write budget, or **two** if an attachment upload
		accompanies it. The relay worker, not this method, is what enforces that.
		"""
		return self._execute(
			build_create_message_call(
				space,
				message_id,
				request_id,
				text,
				thread=thread,
				identity=self.identity,
				notification_options=notification_options,
			)
		)

	def patch_message(
		self,
		message_name: str,
		*,
		text: str,
		allow_missing: bool = True,
		update_mask: Sequence[str] = ("text",),
	) -> dict[str, Any]:
		"""``spaces.messages.patch`` — edit a message, upserting if it is missing.

		Under app authentication you may only patch messages the app created, so editing a
		human's message means constructing this client with ``subject=<that human>``. The
		identity used for an edit must be the identity that created the message; a retry that
		drifts to a different principal fails with a 403 that looks like a scope problem.
		"""
		return self._execute(
			build_patch_message_call(
				message_name,
				text=text,
				allow_missing=allow_missing,
				update_mask=update_mask,
				identity=self.identity,
			)
		)

	def delete_message(self, message_name: str, *, force: bool = True) -> dict[str, Any]:
		"""``spaces.messages.delete`` — delete a message and, with ``force``, its replies."""
		return self._execute(build_delete_message_call(message_name, force=force, identity=self.identity))

	def list_messages(
		self,
		space: str,
		*,
		page_size: int = DEFAULT_PAGE_SIZE,
		page_token: str | None = None,
		filter_expression: str | None = None,
		order_by: str | None = None,
		show_deleted: bool = False,
	) -> dict[str, Any]:
		"""``spaces.messages.list`` — the read Phase 2's reconciliation sweep runs on.

		Build ``filter_expression`` with :func:`build_message_filter`: the API supports
		exactly two filterable fields and a hand-written filter that names a third is a 400.
		"""
		return self._execute(
			build_list_messages_call(
				space,
				page_size=page_size,
				page_token=page_token,
				filter_expression=filter_expression,
				order_by=order_by,
				show_deleted=show_deleted,
			)
		)

	def get_message(self, message_name: str) -> dict[str, Any]:
		"""``spaces.messages.get`` — read one message, by resource name or ``client-`` alias.

		Phase 2's inbound pipeline runs one of these **per event**. That is not an
		inefficiency to optimise away later: the subscription is configured
		``payloadOptions.includeResource: false`` because it is the only configuration with a
		7-day TTL ceiling, and the price of that ceiling is that an event carries a resource
		name and nothing else — no body, no sender, no ``clientAssignedMessageId``. The read
		was budgeted against the 3,000-reads-per-minute project bucket
		(PHASE2_CONTRACT.md §2).

		``VERIFY:`` whether ``clientAssignedMessageId`` is actually **populated** in this
		response. The proto declares it ``OPTIONAL`` — not ``OUTPUT_ONLY``, not
		``INPUT_ONLY`` — and the discovery document does not mark it ``readOnly``, so it is a
		plain read/write field and the design is not inverted; but no Google document or
		sample shows it populated in a response, and the one published event example is a
		human-sent message that would carry no custom id either way. Echo suppression's
		primary path depends on the answer. One live round trip settles it: create a message
		with a ``client-`` id, ``get`` it, print the field
		(PHASE2_VERIFIED.md §8.1).
		"""
		return self._execute(build_get_message_call(message_name))

	# -- Phase 2: spaces, memberships, attachments ----------------------------------------

	def get_space(self, space: str) -> dict[str, Any]:
		"""``spaces.get`` — read a space back, including ``spaceThreadingState``."""
		return self._execute(build_get_space_call(space))

	def list_members(
		self,
		space: str,
		*,
		page_size: int = DEFAULT_MEMBER_PAGE_SIZE,
		page_token: str | None = None,
		filter_expression: str | None = None,
		show_groups: bool = False,
		show_invited: bool = False,
	) -> dict[str, Any]:
		"""``spaces.members.list`` — what Chat believes the membership of a space is.

		The other half of §4.H's membership reconciliation. Which side wins a disagreement is
		``Chat Room.membership_authority``'s answer, never this method's.
		"""
		return self._execute(
			build_list_members_call(
				space,
				page_size=page_size,
				page_token=page_token,
				filter_expression=filter_expression,
				show_groups=show_groups,
				show_invited=show_invited,
			)
		)

	def create_membership(
		self, space: str, member_email: str, *, role: str = "ROLE_MEMBER"
	) -> dict[str, Any]:
		"""``spaces.members.create`` — add one human to a space, as the impersonated caller.

		Charged to the project-wide **300 membership writes per minute** bucket and to *no*
		per-space bucket. A bulk org sweep is therefore throttled at 300/minute, not at one
		per second per space — see :func:`build_create_membership_call`.
		"""
		return self._execute(build_create_membership_call(space, member_email, role=role))

	def delete_membership(self, membership_name: str) -> dict[str, Any]:
		"""``spaces.members.delete`` — remove one member. Same quota bucket as the create."""
		return self._execute(build_delete_membership_call(membership_name))

	def upload_attachment(
		self,
		space: str,
		filename: str,
		*,
		content: bytes,
		content_type: str = DEFAULT_ATTACHMENT_CONTENT_TYPE,
	) -> dict[str, Any]:
		"""``media.upload`` — upload one file and get back an ``attachmentUploadToken``.

		**Only ever reachable under the user identity**, and this client raises rather than
		sends if it was constructed as the app: ``chat.bot`` is absent from ``media.upload``'s
		scope list, so an app-auth upload cannot succeed and a 403 four steps later would read
		like a DWD misconfiguration instead of an impossibility.

		Costs **two** seconds of the space's write budget when it accompanies a message, since
		the upload and the ``messages.create`` share one bucket.
		"""
		return self._execute(
			build_upload_attachment_call(space, filename, content=content, content_type=content_type)
		)

	def download_media(self, resource_name: str) -> dict[str, Any]:
		"""``media.download`` — fetch an inbound attachment's bytes.

		Returns the usual dict, with the bytes under :data:`MEDIA_BYTES_KEY`; read them with
		:func:`attachment_bytes` and the served type with :func:`attachment_content_type`.
		Keeping the return type identical to every other method is deliberate — a method that
		returned raw bytes would be the one call in this class a caller could not check for
		``dryRun``, and would write an empty file to disk as though it were real.
		"""
		return self._execute(build_download_media_call(resource_name))
