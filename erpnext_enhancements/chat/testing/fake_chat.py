# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An in-memory Google Chat API. **The deliverable that makes every later Phase 2 test
possible, and therefore production code with its own test suite.**

It is a ``transport=`` double, not a ``GoogleChatClient`` subclass
-------------------------------------------------------------------
The difference decides what the tests are worth. Injected as
``GoogleChatClient(transport=fake)``, every test exercises the **real** builders, the
**real** retry loop, the **real** ``_request`` contract and the **real** error classifier —
the fake only ever sees an HTTP method, a URL, a query and a JSON body, exactly as
``requests`` would. Subclassing the client instead would replace all of that with the
fake's own behaviour and quietly test the fake.

The interface is therefore the smallest one ``client._request`` uses::

    response = fake.request(method, url, params=…, json=…, headers=…, timeout=…)
    response.status_code, response.text, response.headers

Routing is by **URL and HTTP verb**, parsed the way the real service parses them. There is
no side channel and no "tell the fake which method you meant" argument, because a side
channel is how a fake ends up accepting a request the real API would have 404'd.

What it refuses to get wrong
-----------------------------
A fake's job is not to say yes. These are the behaviours it exists to hold still, each one
a place where a plausible-looking harness would train the relay to be wrong:

* **Membership writes and ``spaces.setup`` consume no per-space budget.**
  ``PHASE2_VERIFIED.md`` §2 corrections 1 and 2. Charging them against the
  1-write-per-second space bucket would throttle membership reconciliation roughly 300×
  harder than the API does, and the relay would be built around a limit that is not real.
* **The 429 is the AIP-193 envelope and carries no ``Retry-After``.** Chat is served by ESF
  and returns the modern shape, two-space-indented; ``Retry-After`` appears zero times on
  the Chat limits page. A fake that helpfully supplied one would let the relay depend on a
  header production never sends.
* **``requestId`` and ``messageId`` are orthogonal.** A ``requestId`` replay returns the
  *original* message; a ``messageId`` collision is an error. They are not two spellings of
  idempotency: ``messageId`` is permanent and server-enforced, ``requestId``'s window is
  undocumented and therefore cannot be relied on past an immediate network retry.
* **``spaceThreadingState`` is output-only.** Asking for a threaded space is rejected, and
  a created named space reports ``GROUPED_MESSAGES``. The API genuinely cannot do it.
* **Deletes are tombstones.** Metadata survives, content does not, and the row is invisible
  to ``list`` without ``showDeleted=true`` — which is the whole reason ERPNext keeps the
  body on its own row: after a delete, ERPNext is the only party that still has the text.
* **``spaces.delete`` is refused by default.** The scope is not granted in V1 and the call
  cascades an entire company's conversation history. A harness that let it succeed is a
  harness in which somebody writes that code path and the test goes green.
* **A message that has attachments renders them.** ``Message.attachment[]`` comes back from
  ``messages.create``, ``messages.get`` and ``messages.list`` in both source shapes, carrying
  the ``downloadUri`` and ``thumbnailUri`` the parser must *ignore*. A fake that could not
  produce the shape could not test the parser, and the gap does not present as missing
  coverage — it presents as coverage that has to be faked at the client seam, which reads
  like a limitation of the code under test instead of one of the harness.

Determinism
------------
**No wall clock and no unseeded randomness anywhere.** Time comes from an injected
:class:`FakeClock` advanced explicitly by the test; identifiers come from an injected
``random.Random``. Two ``FakeChatAPI`` instances built with the same seed produce
byte-identical resource names and byte-identical events, and no test can pass or fail
because of how fast the machine ran. A flaky harness is worse than no harness — it teaches
people to re-run.

That is also why "a delayed response" advances the clock instead of sleeping: the fault is
observable, the wall-clock cost is zero, and a 30-second timeout can be exercised in a test
that finishes instantly.

Identity
---------
The transport sends exactly one thing that identifies the caller: an
``Authorization: Bearer`` header. So the harness defines the token shape —
:func:`token_for` mints ``fake-token:<email>`` and :meth:`FakeChatAPI.token_provider`
plugs straight into ``GoogleChatClient(token_provider=…)``. That makes the impersonated
human real inside the fake, which is what lets it enforce the rules that depend on it: DM
reuse per participant pair, ``spaces.setup`` refusing the caller in ``memberships[]``, and
``deletionMetadata.deletionType`` distinguishing the author from a manager.

Nothing here logs. Not the token, not a body, not a query string. The call journal
(:attr:`FakeChatAPI.calls`) records method, path, query and a truncated body hash — the
same discipline as ``client.build_log_record`` — so an assertion never has to reach for
content that the house rules say must not be recorded.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

from erpnext_enhancements.chat.testing.fixtures import (
	CHAT_HOST_PLACEHOLDER,
	EVENT_MEMBERSHIP_CREATED,
	EVENT_MEMBERSHIP_DELETED,
	EVENT_MESSAGE_CREATED,
	EVENT_MESSAGE_DELETED,
	EVENT_MESSAGE_UPDATED,
	EVENT_SPACE_UPDATED,
	pubsub_envelope,
	rfc3339_from_epoch_ms,
	space_event_id,
)

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

#: 2026-08-09T00:00:00Z in epoch milliseconds — a constant, never a clock read.
#: A fixed, plausible starting point makes a fixture readable without making it real.
DEFAULT_CLOCK_START_MS: Final[int] = 1_786_233_600_000

#: The seed every harness uses unless a test says otherwise. Two instances with the same
#: seed are byte-identical; a test that needs two *different* fakes says so explicitly.
DEFAULT_SEED: Final[int] = 20260809

#: The token shape this harness defines. Google's tokens are opaque, so the fake is free to
#: pick one — and picking one that names the subject is what makes impersonation testable.
TOKEN_PREFIX: Final[str] = "fake-token:"

#: The caller value that means "the registered Chat app" rather than an impersonated human.
APP_CALLER: Final[str] = "app"

#: Google's own id alphabet for space and message ids: base64url. Matters because
#: ``client.validate_space_name`` accepts ``[A-Za-z0-9_-]`` and nothing else — a fake that
#: minted an id with a ``+`` in it would fail validation on the *next* call, three tests
#: later, and read as a client bug.
_ID_ALPHABET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"

#: ``client.validate_client_message_id`` enforces these too; the fake enforces them
#: independently because it is standing in for the *server*, and a check that exists only on
#: the client is a check the server never made.
_CLIENT_ID_PREFIX: Final[str] = "client-"
_CLIENT_ID_MAX_LENGTH: Final[int] = 63
_CLIENT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^client-[a-z0-9-]*$")

_TEXT_HASH_CHARS: Final[int] = 12

#: Google's ``Attachment.source`` enum, spelled once. Both members are reachable through this
#: harness because the two have **opposite ACL consequences** downstream — an
#: ``UPLOADED_CONTENT`` blob is copied into ERPNext and an ingest that copied a ``DRIVE_FILE``
#: would re-home Drive's permission model inside ours. A fake that could only produce one of
#: them would leave the branch that matters untested.
ATTACHMENT_SOURCE_UPLOADED_CONTENT: Final[str] = "UPLOADED_CONTENT"
ATTACHMENT_SOURCE_DRIVE_FILE: Final[str] = "DRIVE_FILE"

#: What an attachment is called when the caller supplies no ``contentName``.
_FALLBACK_CONTENT_NAME: Final[str] = "attachment"

#: What ``contentType`` an attachment reports when nothing better is known. Matches
#: ``client.DEFAULT_ATTACHMENT_CONTENT_TYPE``, spelled independently because this module must
#: stay importable without ``gchat.client`` (see the import test in ``test_chat_fake_api.py``).
_FALLBACK_CONTENT_TYPE: Final[str] = "application/octet-stream"

# -- quota model, straight out of PHASE2_VERIFIED.md §2 ---------------------------------

#: Per space, per second: **1 write**. The binding limit of the entire outbound design, and
#: the reason a room is a strict FIFO rather than a parallel queue.
SPACE_WRITE_COST_MS: Final[int] = 1000

#: Per space, per second: 15 reads → one read every ~66 ms.
SPACE_READ_COST_MS: Final[int] = 1000 // 15

#: The methods that consume the per-space **write** bucket. Note what is absent:
#: ``spaces.members.create``/``delete`` (correction 1) and ``spaces.setup`` (correction 2 —
#: the space does not exist yet, so there is no bucket to charge). ``media.upload`` is
#: present, which is why relaying one message with an attachment costs the space two
#: seconds and not one.
SPACE_WRITE_METHODS: Final[frozenset[str]] = frozenset(
	{
		"media.upload",
		"spaces.messages.create",
		"spaces.messages.patch",
		"spaces.messages.delete",
		"spaces.patch",
		"spaces.delete",
	}
)

#: The methods that consume the per-space **read** bucket (15/second).
SPACE_READ_METHODS: Final[frozenset[str]] = frozenset(
	{
		"spaces.get",
		"spaces.members.get",
		"spaces.members.list",
		"spaces.messages.get",
		"spaces.messages.list",
	}
)

#: Per-project, 60-second fixed windows. **Several independent buckets by category, not one
#: shared pool** (correction 3) — budgeting them as one under-uses the API roughly fivefold.
#:
#: ``VERIFY:`` only ``message_writes`` (3,000), ``message_reads`` (3,000),
#: ``membership_writes`` (300), ``space_writes`` (60) and ``attachment_writes`` (600) are
#: published figures. Correction 3 establishes that reads are bucketed *per category*; it
#: does not establish that every category's ceiling is 3,000. The three read buckets below
#: that are not ``message_reads`` are set to 3,000 as the least-surprising assumption and
#: are marked here so nobody quotes them back as fact.
PROJECT_LIMITS: Final[Mapping[str, int]] = {
	"message_writes": 3000,
	"message_reads": 3000,
	"membership_writes": 300,
	"membership_reads": 3000,  # VERIFY: assumed, not published
	"space_writes": 60,
	"space_reads": 3000,  # VERIFY: assumed, not published
	"attachment_writes": 600,
	"attachment_reads": 3000,  # VERIFY: assumed, not published
}

PROJECT_WINDOW_MS: Final[int] = 60_000

#: Which project bucket each method charges.
PROJECT_BUCKETS: Final[Mapping[str, str]] = {
	"spaces.setup": "space_writes",
	"spaces.patch": "space_writes",
	"spaces.delete": "space_writes",
	"spaces.get": "space_reads",
	"spaces.findDirectMessage": "space_reads",
	"spaces.members.create": "membership_writes",
	"spaces.members.delete": "membership_writes",
	"spaces.members.get": "membership_reads",
	"spaces.members.list": "membership_reads",
	"spaces.messages.create": "message_writes",
	"spaces.messages.patch": "message_writes",
	"spaces.messages.delete": "message_writes",
	"spaces.messages.get": "message_reads",
	"spaces.messages.list": "message_reads",
	"media.upload": "attachment_writes",
	"media.download": "attachment_reads",
}

#: The fully-qualified RPC name that appears in the 429's ``details[].metadata.method``.
#:
#: ``VERIFY:`` only ``google.chat.v1.ChatService.CreateMessage`` was observed, on the live
#: 401 probe recorded in ``PHASE2_VERIFIED.md`` §5. The rest are constructed by applying the
#: same naming rule and are not quoted from anywhere.
RPC_NAMES: Final[Mapping[str, str]] = {
	"spaces.setup": "google.chat.v1.ChatService.SetUpSpace",
	"spaces.get": "google.chat.v1.ChatService.GetSpace",
	"spaces.findDirectMessage": "google.chat.v1.ChatService.FindDirectMessage",
	"spaces.patch": "google.chat.v1.ChatService.UpdateSpace",
	"spaces.delete": "google.chat.v1.ChatService.DeleteSpace",
	"spaces.members.create": "google.chat.v1.ChatService.CreateMembership",
	"spaces.members.delete": "google.chat.v1.ChatService.DeleteMembership",
	"spaces.members.get": "google.chat.v1.ChatService.GetMembership",
	"spaces.members.list": "google.chat.v1.ChatService.ListMemberships",
	"spaces.messages.create": "google.chat.v1.ChatService.CreateMessage",
	"spaces.messages.get": "google.chat.v1.ChatService.GetMessage",
	"spaces.messages.patch": "google.chat.v1.ChatService.UpdateMessage",
	"spaces.messages.delete": "google.chat.v1.ChatService.DeleteMessage",
	"spaces.messages.list": "google.chat.v1.ChatService.ListMessages",
	"media.upload": "google.chat.v1.ChatService.UploadAttachment",
	"media.download": "google.chat.v1.ChatService.DownloadMedia",
}

_SPACE_SEGMENT: Final[str] = r"spaces/[A-Za-z0-9_-]+"
_CHILD_SEGMENT: Final[str] = r"[A-Za-z0-9_.-]+"

#: ``media.download``'s path template is ``v1/media/{resourceName=**}`` — the ``**`` is a
#: **multi-segment** wildcard, so an ``attachmentDataRef.resourceName`` legitimately contains
#: slashes and ``client.validate_media_resource_name`` allows them. A single-segment pattern
#: here would 404 (or, worse, ``UnknownRoute``) on a perfectly valid ref, and the failure would
#: read as a broken ingest rather than a broken route.
_MEDIA_RESOURCE: Final[str] = r"[A-Za-z0-9_.\-/=]+"

#: ``(http_method, path_pattern, google_method)``. Ordered, first match wins. The collection
#: verbs (``spaces:setup``) cannot collide with the resource patterns because ``:`` is not in
#: the id character class — which is precisely why Google chose that syntax.
_ROUTES: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
	("POST", re.compile(r"^/v1/spaces:setup$"), "spaces.setup"),
	("GET", re.compile(r"^/v1/spaces:findDirectMessage$"), "spaces.findDirectMessage"),
	("POST", re.compile(rf"^/upload/v1/(?P<space>{_SPACE_SEGMENT})/attachments:upload$"), "media.upload"),
	("GET", re.compile(rf"^/v1/media/(?P<resource>{_MEDIA_RESOURCE})$"), "media.download"),
	("GET", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})$"), "spaces.get"),
	("PATCH", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})$"), "spaces.patch"),
	("DELETE", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})$"), "spaces.delete"),
	("GET", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})/members$"), "spaces.members.list"),
	("POST", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})/members$"), "spaces.members.create"),
	(
		"GET",
		re.compile(rf"^/v1/(?P<name>{_SPACE_SEGMENT}/members/{_CHILD_SEGMENT})$"),
		"spaces.members.get",
	),
	(
		"DELETE",
		re.compile(rf"^/v1/(?P<name>{_SPACE_SEGMENT}/members/{_CHILD_SEGMENT})$"),
		"spaces.members.delete",
	),
	("GET", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})/messages$"), "spaces.messages.list"),
	("POST", re.compile(rf"^/v1/(?P<space>{_SPACE_SEGMENT})/messages$"), "spaces.messages.create"),
	(
		"GET",
		re.compile(rf"^/v1/(?P<name>{_SPACE_SEGMENT}/messages/{_CHILD_SEGMENT})$"),
		"spaces.messages.get",
	),
	(
		"PATCH",
		re.compile(rf"^/v1/(?P<name>{_SPACE_SEGMENT}/messages/{_CHILD_SEGMENT})$"),
		"spaces.messages.patch",
	),
	(
		"DELETE",
		re.compile(rf"^/v1/(?P<name>{_SPACE_SEGMENT}/messages/{_CHILD_SEGMENT})$"),
		"spaces.messages.delete",
	),
)


# --------------------------------------------------------------------------------------
# Transport-shaped primitives
# --------------------------------------------------------------------------------------


class FakeReadTimeout(TimeoutError):
	"""Injected transport failure — what a read timeout looks like to the client.

	Subclasses :class:`TimeoutError` on purpose: ``backoff.classify_error`` matches on class
	names **across the MRO** (so it can recognise ``requests``' exception tree without
	importing ``requests``, which the bench-free tier does not install), and ``TimeoutError``
	is in its retryable set. So this is retried by exactly the code path a real read timeout
	would take — no special case, no allow-list entry to keep in sync.
	"""


class UnknownRoute(AssertionError):
	"""No route matched the URL. **A harness bug, raised loudly rather than 404'd.**

	The distinction is the point. A route that matches but finds no resource returns a real
	``404 NOT_FOUND`` — that is an API behaviour a test may legitimately want. A URL no route
	matches means the fake has not implemented something the code under test calls, and
	answering that with a 404 would let the test conclude "Google says it isn't there" from
	what is actually "the harness has never heard of this endpoint".
	"""


@dataclass(frozen=True)
class FakeResponse:
	"""The three attributes ``GoogleChatClient._request`` reads, plus bytes for later.

	``content`` is not used by today's client — it exists because ``media.download`` returns
	binary and ``PHASE2_INTERFACES.md`` §6 adds a bytes path to ``_request``. Shipping it now
	means the attachment task does not have to change the fake to test the path it adds.
	"""

	status_code: int
	text: str
	headers: Mapping[str, str] = field(default_factory=dict)
	content: bytes = b""


@dataclass(frozen=True)
class FakeCall:
	"""One entry in the call journal — what a test asserts against instead of a log.

	Body-free by construction, for the same reason ``client.build_log_record`` is: message
	content is employee-private, and a harness that made it convenient to assert on bodies
	would make it convenient to log them. The message store is where content lives; ask
	:meth:`FakeChatAPI.message` for it.
	"""

	google_method: str
	http_method: str
	path: str
	query: Mapping[str, str]
	caller: str
	status: int | None
	text_bytes: int
	text_hash: str


class FakeClock:
	"""Monotonic, integer-millisecond, and advanced only when a test says so.

	The whole harness reads time through this and nothing else. That is what makes "1 write
	per second per space" a *deterministic* assertion rather than a race against the test
	runner: ``advance(999)`` still 429s, ``advance(1000)`` succeeds, on every machine and
	under every load.
	"""

	def __init__(self, start_ms: int = DEFAULT_CLOCK_START_MS) -> None:
		self._ms = int(start_ms)

	def now_ms(self) -> int:
		return self._ms

	def advance(self, ms: int) -> int:
		"""Move forward. Refuses to go backwards — a rewound clock silently un-spends quota."""
		delta = int(ms)
		if delta < 0:
			raise ValueError("FakeClock only moves forward; a rewound clock refunds spent quota.")
		self._ms += delta
		return self._ms

	def advance_seconds(self, seconds: float) -> int:
		return self.advance(int(round(float(seconds) * 1000.0)))

	def rfc3339(self) -> str:
		return rfc3339_from_epoch_ms(self._ms)


@dataclass
class FakeChatSettings:
	"""A stand-in for ``Chat Settings``, so every Phase 2 test wires the client the same way.

	Only the fields ``GoogleChatClient._setting`` reads. The backoff values are deliberately
	tiny: the client sleeps **synchronously** inside its retry loop, so a suite that left the
	real 0.5-second base in place would spend most of its wall-clock time asleep and the
	retry tests would be the slowest in the repo. They are not zero, because ``_setting``
	treats ``0`` as "unset" and falls back to the real default — a trap worth knowing about
	rather than working around silently.
	"""

	relay_max_attempts: int = 3
	relay_initial_backoff_seconds: float = 0.001
	backoff_cap_seconds: float = 0.002
	http_timeout_seconds: float = 30.0
	log_message_bodies: int = 0


def token_for(subject: str | None) -> str:
	"""Mint the harness's bearer token for an impersonated human, or for the app identity."""
	return f"{TOKEN_PREFIX}{(subject or APP_CALLER).strip() or APP_CALLER}"


def _text_fingerprint(text: str) -> tuple[int, str]:
	encoded = (text or "").encode("utf-8")
	return len(encoded), hashlib.sha256(encoded).hexdigest()[:_TEXT_HASH_CHARS]


def _user_id_for(email: str) -> str:
	"""A stable numeric Google user id for an email address.

	Deterministic so ``sender.name`` is comparable across runs, and numeric because a real
	``users/{id}`` is an opaque decimal id — **not** an email. Anything resolving a member to
	an ERPNext ``User`` has to pay for a lookup, and a fake that put the email in the
	resource name would hide that cost.
	"""
	return str(int(hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:15], 16))


# --------------------------------------------------------------------------------------
# Stored state
# --------------------------------------------------------------------------------------


@dataclass
class _Space:
	name: str
	space_type: str
	display_name: str
	creator: str
	create_time_ms: int
	single_user_bot_dm: bool = False
	deleted: bool = False


@dataclass
class _Membership:
	name: str
	space: str
	email: str
	role: str
	state: str
	create_time_ms: int


@dataclass
class _Attachment:
	"""One ``Message.attachment[]`` entry, stored the way the two sources actually differ.

	``data_ref`` and ``drive_file_id`` are mutually exclusive in practice and both are kept
	rather than collapsed into one "handle" field: they are not two spellings of the same
	thing. ``data_ref`` is a ``media.download`` handle that yields bytes; ``drive_file_id``
	names a file governed by Drive's own ACL that ``media.download`` cannot fetch at all.
	"""

	name: str
	content_name: str
	content_type: str
	source: str
	data_ref: str = ""
	drive_file_id: str = ""


@dataclass
class _Message:
	name: str
	space: str
	client_id: str
	sender: str
	text: str
	thread: str
	create_time_ms: int
	last_update_ms: int | None = None
	delete_time_ms: int | None = None
	deletion_type: str = ""
	attachments: tuple[_Attachment, ...] = ()


@dataclass
class _MethodFaults:
	"""Armed faults for one ``google_method``. Each counter is independent and consumed once.

	Scoped per method rather than globally because that is how a real fault presents: Google
	is not "down", one RPC is failing. A test that arms a 5xx on ``messages.create`` and then
	asserts ``messages.get`` still works is testing the relay's actual recovery path.
	"""

	timeouts_before: int = 0
	timeouts_after: int = 0
	server_errors: int = 0
	server_error_status: int = 503
	rate_limits: int = 0
	delays: int = 0
	delay_ms: int = 0
	duplicate_events: bool = False
	out_of_order: bool = False


@dataclass
class _Race:
	space: str
	during: Callable[[FakeChatAPI], None] | None
	delay_ms: int
	remaining: int


@dataclass
class _RequestContext:
	google_method: str
	http_method: str
	path: str
	groups: Mapping[str, str]
	query: Mapping[str, str]
	body: Mapping[str, Any] | None
	raw: bytes
	caller: str


# --------------------------------------------------------------------------------------
# The fake
# --------------------------------------------------------------------------------------


class FakeChatAPI:
	"""An in-memory Google Chat API with quotas, events and fault injection.

	    fake = FakeChatAPI()
	    client = GoogleChatClient(
	        subject="alice@example.com",
	        dry_run=False,
	        settings=FakeChatSettings(),
	        token_provider=fake.token_provider,
	        transport=fake,
	    )
	    client.create_message(space, message_id, request_id, "hello")
	    events = fake.drain_events()

	Everything is explicit: the clock does not move unless a test moves it, the RNG is
	seeded, and the only way anything reaches the store is through :meth:`request` or one of
	the ``seed_*`` helpers.
	"""

	def __init__(
		self,
		*,
		clock: FakeClock | None = None,
		rng: random.Random | None = None,
		seed: int = DEFAULT_SEED,
		return_client_ids: bool = True,
		default_caller: str = "",
		subscription_id: str = "fake-events-subscription",
		enforce_space_write_quota: bool = True,
		enforce_space_read_quota: bool = False,
		enforce_project_quota: bool = True,
		allow_space_delete: bool = False,
		emit_setup_membership_events: bool = True,
		request_latency_ms: int = 0,
		service_host: str = CHAT_HOST_PLACEHOLDER,
	) -> None:
		"""``return_client_ids=False`` is the switch that matters; read its note below.

		``request_latency_ms`` advances the clock by that much **after** each request, which
		is how a test says "these calls are not all happening in the same instant" without
		reaching for a real sleep. It defaults to ``0`` — a frozen clock — because the default
		has to be the one that makes the 1-write-per-second bucket bite: with a frozen clock,
		two ``messages.create`` calls into one space produce a 429 on the second, exactly as
		production would, and the test has to say ``clock.advance(1000)`` to mean "a second
		later". A non-zero default would hide the binding limit of the entire design behind a
		convenience.

		``service_host`` is the string that appears in the 429's
		``details[].metadata.service``. It defaults to a placeholder rather than to the real
		Chat API host because ``tests/test_chat_guardrails.py`` scans every live string under
		``chat/`` for a Google host — it enforces "only two modules speak HTTP to Google" and
		cannot tell an error fixture from a call site. Pass the real value from
		``client.CHAT_API_HOST`` in a test that needs the byte-accurate body.

		``enforce_space_read_quota`` defaults **off** while the write bucket defaults **on**,
		and the asymmetry is deliberate rather than lazy. The 1-write-per-second bucket is the
		limit the whole outbound design is shaped around, so every test should feel it. The
		15-reads-per-second bucket is equally real, but under a frozen clock an ordinary
		arrange-act-assert that reads a space back three times would trip it for reasons that
		have nothing to do with what the test is about. Turn it on in the tests that are about
		read throughput — the inbound pipeline's one ``messages.get`` per event is exactly
		that — and leave it off elsewhere.

		``allow_space_delete`` defaults **off**. ``chat.delete`` / ``chat.app.delete`` are not
		granted in V1 and must not be: the call cascades and removes a company's conversation
		history in one request. The fake answers ``403 PERMISSION_DENIED`` so that a code path
		which calls it fails in CI rather than in production.
		"""
		self.clock: FakeClock = clock if clock is not None else FakeClock()
		self.rng: random.Random = rng if rng is not None else random.Random(seed)
		self.default_caller: str = default_caller
		self.subscription_id: str = subscription_id

		#: ``VERIFY:`` (``PHASE2_VERIFIED.md`` §8.1) whether the real API populates
		#: ``clientAssignedMessageId`` in a response is **unproven** — the proto marks it
		#: OPTIONAL rather than OUTPUT_ONLY, so the design is not inverted, but no Google
		#: document or sample anywhere shows it populated on a read. Echo suppression's
		#: primary path depends on it. Setting this ``False`` models the bad answer, which is
		#: the only way the bounded fallback ladder can be tested before one live round trip
		#: settles it.
		self.return_client_ids: bool = bool(return_client_ids)

		self.enforce_space_write_quota: bool = bool(enforce_space_write_quota)
		self.enforce_space_read_quota: bool = bool(enforce_space_read_quota)
		self.enforce_project_quota: bool = bool(enforce_project_quota)
		self.allow_space_delete: bool = bool(allow_space_delete)
		self.emit_setup_membership_events: bool = bool(emit_setup_membership_events)
		self.request_latency_ms: int = int(request_latency_ms)
		self.service_host: str = service_host

		self._spaces: dict[str, _Space] = {}
		self._memberships: dict[str, _Membership] = {}
		self._messages: dict[str, _Message] = {}
		#: ``(space, client_message_id)`` → message name. Permanent: ``messageId`` is unique
		#: within a space **forever**, with no expiry, and a tombstone does not free it.
		self._client_ids: dict[tuple[str, str], str] = {}
		#: ``(google_method, scope, requestId)`` → the original response payload.
		self._request_ids: dict[tuple[str, str, str], dict[str, Any]] = {}
		self._attachments: dict[str, bytes] = {}
		#: ``attachmentUploadToken`` → what ``media.upload`` was told about the file. Google
		#: documents no lifetime for a token, so the fake keeps them forever: modelling an
		#: expiry we cannot cite would make a test fail for a reason no document supports.
		self._upload_tokens: dict[str, _Attachment] = {}
		#: ``frozenset({a, b})`` → space name, so ``findDirectMessage`` and ``setup`` agree.
		self._dms: dict[frozenset[str], str] = {}

		self._space_write_free_ms: dict[str, int] = {}
		self._space_read_free_ms: dict[str, int] = {}
		self._project_counters: dict[tuple[str, int], int] = {}

		self._faults: dict[str, _MethodFaults] = {}
		self._duplicate_all: bool = False
		self._out_of_order_all: bool = False
		self._races: list[_Race] = []

		self._queue: list[dict[str, Any]] = []
		self._held: list[dict[str, Any]] = []
		self._event_seq: int = 0

		self.calls: list[FakeCall] = []

	# -- wiring -------------------------------------------------------------------------

	def token_provider(self, identity: Any = None, subject: str | None = None) -> str:
		"""Drop-in for ``GoogleChatClient(token_provider=…)``.

		The client's ``TokenProvider`` contract is ``(AuthIdentity, str | None) -> str``.
		``identity`` is accepted and ignored: the fake identifies the caller from the subject,
		and an app-identity client passes ``subject=None``, which :func:`token_for` maps to
		:data:`APP_CALLER`. Typing the parameter as ``Any`` keeps this module importable
		without ``client``, which imports nothing heavy but does belong to another package.
		"""
		return token_for(subject)

	# -- seeding ------------------------------------------------------------------------

	def seed_space(
		self,
		*,
		display_name: str = "Seeded Space",
		space_type: str = "SPACE",
		members: Sequence[str] = (),
		creator: str = "seed@example.com",
	) -> str:
		"""Create a space directly — **no quota, no faults, no events**.

		Arrangement is not behaviour. A test about inbound ingest should not have to spend
		its space-write budget or drain six membership events before it can begin.
		"""
		space = self._new_space(space_type=space_type, display_name=display_name, creator=creator)
		for email in members:
			self._add_membership(space.name, email)
		return space.name

	def seed_message(
		self,
		space: str,
		*,
		text: str = "seeded",
		sender: str = "seed@example.com",
		client_id: str = "",
		thread: str = "",
		attachments: Sequence[Mapping[str, Any]] = (),
	) -> str:
		"""Insert a message directly — no quota, no faults, **no event**.

		Silence is the point: this is how a test builds the history that a reconciliation
		sweep or a ``messages.list`` page is supposed to find, without the inbound pipeline
		seeing it arrive.

		``attachments`` takes the same specs :meth:`seed_attachment` documents, so a seeded
		message can carry files without a second call.
		"""
		message = self._store_message(
			space=space, text=text, sender=sender, client_id=client_id, thread=thread
		)
		for spec in attachments or ():
			self.seed_attachment(message.name, **dict(spec))
		return message.name

	def seed_attachment(
		self,
		message: str,
		*,
		source: str = ATTACHMENT_SOURCE_UPLOADED_CONTENT,
		content_name: str = _FALLBACK_CONTENT_NAME,
		content_type: str = _FALLBACK_CONTENT_TYPE,
		content: bytes = b"",
		data_ref: str = "",
		drive_file_id: str = "",
	) -> str:
		"""Hang one attachment on an existing message — **arrangement, not behaviour**.

		This is the seeding counterpart to ``messages.create``'s upload-token path, and it
		exists because the two ``Attachment.source`` shapes do not arrive by the same route in
		reality. ``UPLOADED_CONTENT`` is what an API caller can create, through
		``media.upload`` then a token on the create — and the create handler really does that.
		``DRIVE_FILE`` is **not creatable through the API at all**: ``Message.attachment`` is
		documented as *user-uploaded*, and a Drive attachment is something a human's native
		client produces. So a fake that let ``messages.create`` mint a ``DRIVE_FILE`` would be
		teaching a code path the real API refuses.

		Seeding is the honest way to express "a coworker attached a Drive file", the same way
		:meth:`seed_space` expresses "this space already existed". Returns the attachment's
		resource name.

		``content`` registers the bytes behind the data ref so a later ``media.download``
		finds them; a data ref is minted when one is not supplied. Nothing here charges quota
		or emits an event — the message the attachment hangs on already did that, or was
		itself seeded.
		"""
		stored = self._resolve_message(message)
		if stored is None:
			raise AssertionError(
				f"cannot seed an attachment on {message!r}: no such message in this fake. "
				"Create or seed the message first; a fake that invented one would hide the bug."
			)
		attachment = self._build_attachment(
			stored,
			source=source,
			content_name=content_name,
			content_type=content_type,
			data_ref=data_ref,
			drive_file_id=drive_file_id,
		)
		if content:
			if not attachment.data_ref:
				raise AssertionError(
					"bytes were supplied for an attachment with no data ref. A DRIVE_FILE has no "
					"media.download handle by design — that is the whole point of the source split."
				)
			self._attachments[attachment.data_ref] = bytes(content)
		stored.attachments = (*stored.attachments, attachment)
		return attachment.name

	# -- fault injection ----------------------------------------------------------------

	def _fault(self, google_method: str) -> _MethodFaults:
		return self._faults.setdefault(google_method, _MethodFaults())

	def fail_with_timeout(
		self, google_method: str, *, times: int = 1, after_processing: bool = False
	) -> None:
		"""Raise a read timeout instead of answering, for the next ``times`` calls.

		``after_processing=True`` is the version worth reaching for. It runs the handler
		first — the message is created, the Workspace Event is published — and *then* times
		out, which is what a real read timeout usually is: the server did the work and the
		answer got lost. That is the failure ``requestId`` and ``messageId`` exist for, and
		the only way to prove the relay's retry does not post a second copy.
		"""
		fault = self._fault(google_method)
		if after_processing:
			fault.timeouts_after += max(int(times), 0)
		else:
			fault.timeouts_before += max(int(times), 0)

	def fail_with_server_error(self, google_method: str, *, times: int = 1, status: int = 503) -> None:
		"""Return ``status`` for the next ``times`` calls. Retryable, so the client will loop."""
		fault = self._fault(google_method)
		fault.server_errors += max(int(times), 0)
		fault.server_error_status = int(status)

	def fail_with_rate_limit(self, google_method: str, *, times: int = 1) -> None:
		"""Force the AIP-193 429 regardless of the bucket state.

		Not redundant with the quota model. Google publishes two caveats no design can
		remove — *"additional rate limit checks on the Chat backend might also generate the
		same error response"* and *"high API traffic targeting the same space can trigger
		additional internal limits that aren't visible in the Quotas page"* — so staying
		under 1 write/second does **not** guarantee no 429. The bucket is an optimisation;
		backoff is the correctness mechanism, and this is how that gets tested.
		"""
		self._fault(google_method).rate_limits += max(int(times), 0)

	def delay_response(self, google_method: str, *, ms: int, times: int = 1) -> None:
		"""Make the next ``times`` calls slow — by advancing the clock, not by sleeping.

		Which means a 30-second stall costs the test suite nothing, and the delay is visible
		to the quota buckets exactly as real elapsed time would be.
		"""
		fault = self._fault(google_method)
		fault.delays += max(int(times), 0)
		fault.delay_ms = int(ms)

	def duplicate_events(self, google_method: str | None = None, *, enabled: bool = True) -> None:
		"""Deliver every event twice — the same envelope, the same ``ce-id``.

		Identical rather than merely similar, because that is what Pub/Sub at-least-once
		redelivery produces, and because a redelivery with a fresh id would be caught by
		accident. Structural dedupe on ``unique(gchat_message_name)`` has to be what catches
		it. ``google_method=None`` applies to every method.
		"""
		if google_method is None:
			self._duplicate_all = bool(enabled)
		else:
			self._fault(google_method).duplicate_events = bool(enabled)

	def deliver_out_of_order(self, google_method: str | None = None, *, enabled: bool = True) -> None:
		"""Hold this method's events back and release them **after** the next event.

		Pub/Sub does not guarantee ordering without an ordering key, so an edit can be
		delivered before the create it edits. That is ADR §G.8 Rule 1 (CREATE-BEFORE-EDIT) on
		the inbound side: an ``updated`` event for an unknown resource name must be applied
		as a create from the fetched resource, not dropped and not errored.

		One event is held at a time and released when a later event is published, or at drain
		time if none is — so nothing is ever lost, it only arrives late, and a stream of events
		comes out pairwise-swapped rather than fully reversed.
		"""
		if google_method is None:
			self._out_of_order_all = bool(enabled)
		else:
			self._fault(google_method).out_of_order = bool(enabled)

	def race_on_create(
		self,
		space: str,
		*,
		during: Callable[[FakeChatAPI], None] | None = None,
		delay_ms: int = 0,
		times: int = 1,
	) -> None:
		"""Arm the §4.D race: **the event is published before the response comes back.**

		This is the one fault that gets a first-class API, because it is the one that decides
		whether the design is sound and the one nobody would hand-wire. The sequence it
		produces, inside a single ``messages.create``:

		1. the message is committed in the fake;
		2. the Workspace Event is published — an inbound worker could pick it up **now**;
		3. ``during(fake)`` runs, which is where a test puts that inbound worker;
		4. the clock advances by ``delay_ms``;
		5. only then does the HTTP response carrying the resource name return to the relay.

		So at step 3 the relay has **not** written ``gchat_message_name`` yet, and inbound
		sees a message it cannot recognise structurally. Echo suppression must survive that
		on the ``client-`` id alone, or the mirror duplicates every message it sends whenever
		Google is faster than the response. ``times`` arms it for consecutive creates in
		``space``.
		"""
		self._races.append(
			_Race(space=space, during=during, delay_ms=int(delay_ms), remaining=max(int(times), 1))
		)

	def clear_faults(self) -> None:
		"""Disarm everything. Quota state, the store and the event queue are untouched."""
		self._faults.clear()
		self._races.clear()
		self._duplicate_all = False
		self._out_of_order_all = False

	# -- the event queue ----------------------------------------------------------------

	def pending_events(self) -> tuple[dict[str, Any], ...]:
		"""Peek at undelivered events without consuming them.

		A peek **does** release a held out-of-order event, because "pending" has to mean
		"everything not yet delivered" — otherwise a held event is invisible here and reads as
		lost. The cost is that peeking in the middle of an out-of-order sequence releases the
		held event early and destroys the swap; use :meth:`event_count` to check for activity
		without disturbing the ordering.
		"""
		self._release_held()
		return tuple(self._queue)

	def drain_events(self) -> list[dict[str, Any]]:
		"""Take every pending event and clear the queue.

		The test *is* the Pub/Sub consumer. Nothing is delivered on a timer, nothing arrives
		between assertions, and an event that was never drained is a visible leftover rather
		than a race.
		"""
		self._release_held()
		drained = list(self._queue)
		self._queue.clear()
		return drained

	def event_count(self) -> int:
		return len(self._queue) + len(self._held)

	def _release_held(self) -> None:
		if self._held:
			self._queue.extend(self._held)
			self._held.clear()

	def _emit(self, *, event_type: str, subject: str, resource_key: str, resource_name: str) -> None:
		"""Publish one Workspace Event, ``includeResource: false`` — a **name only**.

		Built through ``fixtures.pubsub_envelope`` so the fake's emissions and the fixtures
		the inbound parser is tested against are byte-identical by construction. Two
		constructions of the envelope is how a parser starts passing its unit tests and
		failing on the harness.
		"""
		self._event_seq += 1
		fault = self._faults.get(_method_of_event(event_type))
		envelope = pubsub_envelope(
			event_type=event_type,
			data={resource_key: {"name": resource_name}},
			subject=subject,
			event_id=space_event_id(subject, f"evt{self._event_seq:06d}"),
			publish_time=self.clock.rfc3339(),
			pubsub_message_id=f"{9_000_000_000_000_000 + self._event_seq}",
			subscription_id=self.subscription_id,
		)

		duplicate = self._duplicate_all or bool(fault and fault.duplicate_events)
		batch = [envelope, json.loads(json.dumps(envelope))] if duplicate else [envelope]

		if (self._out_of_order_all or bool(fault and fault.out_of_order)) and not self._held:
			# **One** event is held at a time, deliberately. Holding every event would preserve
			# the original order at drain time and reorder nothing — the mode would look armed
			# and do nothing, which is the worst thing a fault injector can do. Holding one
			# produces a pairwise swap: the stream arrives late-then-early, which is the shape
			# an inbound worker actually has to survive.
			self._held.extend(batch)
			return
		self._queue.extend(batch)
		self._release_held()

	# -- store accessors ----------------------------------------------------------------

	def space(self, name: str) -> dict[str, Any] | None:
		stored = self._spaces.get(name)
		return self._space_resource(stored) if stored else None

	def message(self, name: str) -> dict[str, Any] | None:
		stored = self._resolve_message(name)
		return self._message_resource(stored) if stored else None

	def messages_in(self, space: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
		return [
			self._message_resource(message)
			for message in self._ordered_messages(space)
			if include_deleted or message.delete_time_ms is None
		]

	def members_of(self, space: str) -> list[str]:
		return sorted(m.email for m in self._memberships.values() if m.space == space)

	def attachment(self, resource_name: str) -> bytes | None:
		return self._attachments.get(resource_name)

	# -- the transport entry point -------------------------------------------------------

	def request(
		self,
		method: str,
		url: str,
		params: Mapping[str, Any] | None = None,
		json: Mapping[str, Any] | None = None,
		headers: Mapping[str, str] | None = None,
		timeout: Any = None,
		data: Any = None,
		**_ignored: Any,
	) -> FakeResponse:
		"""``requests.Session.request``, as much of it as the Chat client uses.

		The parameter names are ``requests``' names because ``GoogleChatClient._request``
		calls them by keyword; ``json`` shadows the stdlib module inside this frame, which is
		why every serialisation in this class happens in a helper rather than here.

		``data`` is accepted for the ``media.upload`` bytes path that
		``PHASE2_INTERFACES.md`` §6 adds to ``GoogleCall``; ``timeout`` is accepted and
		ignored, because a fake that honoured it would be reading a clock.
		"""
		http_method = str(method or "GET").upper()
		path = urlsplit(str(url)).path
		query = {str(key): str(value) for key, value in (params or {}).items()}
		body = dict(json) if isinstance(json, Mapping) else None
		raw = data if isinstance(data, bytes | bytearray) else b""

		google_method, groups = self._route(http_method, path)
		caller = self._caller(headers)
		context = _RequestContext(
			google_method=google_method,
			http_method=http_method,
			path=path,
			groups=groups,
			query=query,
			body=body,
			raw=bytes(raw),
			caller=caller,
		)

		response: FakeResponse | None = None
		try:
			response = self._dispatch(context)
			return response
		finally:
			# Elapsed time is applied on the way **out**, which is the only ordering that
			# models a slow response correctly: the request arrived — and charged its quota —
			# at T, and the answer comes back at T + delay. Applying it on the way in would
			# charge the space's write slot at the later timestamp and make a delayed call
			# throttle the call that followed it.
			#
			# It runs on the injected-timeout path too: a request that timed out still consumed
			# wall-clock time, and a harness where it did not would let a retry storm run
			# forever inside one frozen millisecond.
			self._advance_elapsed(context.google_method)
			# Journalled in `finally` so an injected timeout still leaves a row, with a `None`
			# status. An attempt that produced no response is exactly the attempt a test most
			# wants to count: "the relay tried twice" is invisible if only answers are recorded.
			self._journal(context, response)

	def _advance_elapsed(self, google_method: str) -> None:
		"""Consume any armed delay for this method, then the flat per-request latency."""
		fault = self._faults.get(google_method)
		if fault and fault.delays > 0 and fault.delay_ms:
			fault.delays -= 1
			self.clock.advance(fault.delay_ms)
		if self.request_latency_ms:
			self.clock.advance(self.request_latency_ms)

	# -- request pipeline ----------------------------------------------------------------

	def _dispatch(self, context: _RequestContext) -> FakeResponse:
		"""Faults, then quotas, then the handler — the order a real edge applies them.

		Injected faults sit **above** the quota check on purpose: a forced 429 must be
		reachable while the bucket is empty, because Google's own documentation says the
		backend can 429 a request that is inside every published limit.
		"""
		fault = self._faults.get(context.google_method)

		if fault and fault.timeouts_before > 0:
			fault.timeouts_before -= 1
			raise FakeReadTimeout(f"injected read timeout before {context.google_method}")

		if fault and fault.rate_limits > 0:
			fault.rate_limits -= 1
			return self._rate_limited(context.google_method)

		if fault and fault.server_errors > 0:
			fault.server_errors -= 1
			return self._error(
				fault.server_error_status,
				"UNAVAILABLE" if fault.server_error_status == 503 else "INTERNAL",
				"injected server error",
				google_method=context.google_method,
			)

		if not context.caller:
			# Auth is checked BEFORE request validation — observed on the live probe, where an
			# invalid `spaceType` enum still came back 401. Order matters to anyone reading a
			# failure: a 400 that is really a 401 sends them to the wrong file.
			return self._error(
				401,
				"UNAUTHENTICATED",
				"Request had invalid authentication credentials.",
				google_method=context.google_method,
			)

		throttled = self._charge_quota(context)
		if throttled is not None:
			return throttled

		handler = getattr(self, _HANDLERS[context.google_method])
		response = handler(context)

		if fault and fault.timeouts_after > 0:
			# The work is done, the event is published, and the answer is lost on the way
			# back. This is what a read timeout usually is, and it is the exact failure both
			# idempotency keys exist for: the retry must find the original message rather than
			# create a second one.
			fault.timeouts_after -= 1
			raise FakeReadTimeout(f"injected read timeout after {context.google_method} was processed")

		return response

	def _route(self, http_method: str, path: str) -> tuple[str, dict[str, str]]:
		for verb, pattern, google_method in _ROUTES:
			if verb != http_method:
				continue
			match = pattern.match(path)
			if match:
				return google_method, {k: v for k, v in match.groupdict().items() if v}
		raise UnknownRoute(
			f"no fake Chat route for {http_method} {path}. Either the caller built a URL the real "
			"API does not serve, or this method is not implemented here yet — do not paper over it "
			"with a 404, which a test would read as 'Google says it does not exist'."
		)

	def _caller(self, headers: Mapping[str, str] | None) -> str:
		"""Resolve the impersonated human from the ``Authorization`` header.

		Never stored, never journalled, never returned in an error message — the token is the
		one value in this module that a leak would matter for, even a fake one, because the
		habit is what leaks the real one.
		"""
		for key, value in (headers or {}).items():
			if str(key).lower() != "authorization":
				continue
			token = str(value).strip()
			if token.lower().startswith("bearer "):
				token = token[7:].strip()
			if token.startswith(TOKEN_PREFIX):
				return token[len(TOKEN_PREFIX) :].strip()
			return self.default_caller or APP_CALLER
		return self.default_caller

	def _journal(self, context: _RequestContext, response: FakeResponse | None) -> None:
		text = ""
		if context.body is not None:
			text = str(context.body.get("text") or "")
		text_bytes, text_hash = _text_fingerprint(text)
		self.calls.append(
			FakeCall(
				google_method=context.google_method,
				http_method=context.http_method,
				path=context.path,
				query=dict(context.query),
				caller=context.caller,
				status=response.status_code if response is not None else None,
				text_bytes=text_bytes,
				text_hash=text_hash,
			)
		)

	# -- quota ---------------------------------------------------------------------------

	def _charge_quota(self, context: _RequestContext) -> FakeResponse | None:
		"""Per-space bucket, then the per-project 60-second window. ``None`` means allowed.

		The per-space bucket is GCRA, re-implemented here rather than imported from
		``sync/ratelimit.py``. That duplication is deliberate: the fake is the **server**, and
		a server that shares the client's arithmetic cannot catch a bug in the client's
		arithmetic. If the two ever disagree, one of them is wrong and the test says so.
		"""
		space = self._space_of(context)
		now = self.clock.now_ms()

		if space and self.enforce_space_write_quota and context.google_method in SPACE_WRITE_METHODS:
			free_at = self._space_write_free_ms.get(space, 0)
			if now < free_at:
				return self._rate_limited(context.google_method)
			self._space_write_free_ms[space] = now + SPACE_WRITE_COST_MS

		if space and self.enforce_space_read_quota and context.google_method in SPACE_READ_METHODS:
			free_at = self._space_read_free_ms.get(space, 0)
			if now < free_at:
				return self._rate_limited(context.google_method)
			self._space_read_free_ms[space] = now + SPACE_READ_COST_MS

		if self.enforce_project_quota:
			bucket = PROJECT_BUCKETS.get(context.google_method, "")
			limit = PROJECT_LIMITS.get(bucket, 0)
			if bucket and limit:
				window = now // PROJECT_WINDOW_MS
				used = self._project_counters.get((bucket, window), 0)
				if used >= limit:
					return self._rate_limited(context.google_method)
				self._project_counters[(bucket, window)] = used + 1

		return None

	def _space_of(self, context: _RequestContext) -> str:
		"""The space a call is charged to, or ``""`` for the calls that have none.

		``spaces.setup`` and the membership writes return ``""`` — corrections 1 and 2. So
		does ``media.download``, but for a different reason worth stating: its URL is
		``/v1/media/{resourceName}`` and the resource name does not carry the space, so the
		fake **cannot** charge it even though the real service presumably can. ``VERIFY:``
		if attachment downloads ever look throttled in production, this is the gap.
		"""
		if context.google_method in {"spaces.setup", "spaces.findDirectMessage", "media.download"}:
			return ""
		space = context.groups.get("space", "")
		if space:
			return space
		name = context.groups.get("name", "")
		if name:
			return name.split("/members/")[0].split("/messages/")[0]
		return ""

	# -- responses -----------------------------------------------------------------------

	def _ok(self, payload: Mapping[str, Any], *, status: int = 200) -> FakeResponse:
		text = json.dumps(dict(payload), indent=2, ensure_ascii=False)
		return FakeResponse(
			status_code=status,
			text=text,
			headers={"Content-Type": "application/json; charset=UTF-8"},
			content=text.encode("utf-8"),
		)

	def _error(
		self,
		status_code: int,
		google_status: str,
		message: str,
		*,
		google_method: str = "",
		details: Sequence[Mapping[str, Any]] = (),
	) -> FakeResponse:
		"""The AIP-193 envelope — the modern shape, which is what ESF serves for Chat.

		Confirmed against a live unauthenticated probe of the Chat API this session: two-space
		indentation, ``{"error": {"code", "message", "status", "details"}}``, and
		``?prettyPrint=false`` ignored on the error path. Calendar and Gmail return the
		*legacy* ``errors[]`` envelope; Chat does not, and a fake copied from a Calendar
		integration would be wrong in a way that only shows up in ``extract_error_status``.

		``details`` is populated only for the 429. Whether Chat fills it on a 400 or a 403 was
		not observed, and inventing it would be inventing the one field the error classifier
		is most likely to grow a dependency on.
		"""
		error: dict[str, Any] = {"code": int(status_code), "message": message, "status": google_status}
		if details:
			error["details"] = [dict(entry) for entry in details]
		text = json.dumps({"error": error}, indent=2, ensure_ascii=False)
		return FakeResponse(
			status_code=int(status_code),
			text=text,
			headers={"Content-Type": "application/json; charset=UTF-8"},
			content=text.encode("utf-8"),
		)

	def _rate_limited(self, google_method: str) -> FakeResponse:
		"""HTTP 429, ``RESOURCE_EXHAUSTED``, ``RATE_LIMIT_EXCEEDED`` — and **no Retry-After**.

		The missing header is the load-bearing part. ``Retry-After`` appears zero times on
		the Chat quota documentation and zero times in ``google-api-python-client``, which
		retries every 429 on pure exponential backoff. Phase 1's ``parse_retry_after`` stays
		— harmless, and correct if one ever arrives — but nothing may *depend* on one, and a
		fake that supplied one would let something quietly start to.

		``VERIFY:`` the ``message`` string is a guess. The envelope *family* is confirmed
		(observed on a 401 from the live host); this particular sentence is not, and no
		document quotes Chat's 429 text. The falsification plan is one line: the first real
		production 429 logs its raw body verbatim and gets diffed against this.
		"""
		return self._error(
			429,
			"RESOURCE_EXHAUSTED",
			"Quota exceeded for quota metric 'Write requests' and limit "
			f"'Write requests per minute per space' of service '{self.service_host}'.",
			google_method=google_method,
			details=[
				{
					"@type": "type.googleapis.com/google.rpc.ErrorInfo",
					"reason": "RATE_LIMIT_EXCEEDED",
					"domain": "googleapis.com",
					"metadata": {
						"method": RPC_NAMES.get(google_method, google_method),
						"service": self.service_host,
					},
				}
			],
		)

	def _invalid(self, message: str, *, google_method: str = "") -> FakeResponse:
		return self._error(400, "INVALID_ARGUMENT", message, google_method=google_method)

	def _not_found(self, message: str, *, google_method: str = "") -> FakeResponse:
		return self._error(404, "NOT_FOUND", message, google_method=google_method)

	# -- handlers: spaces -----------------------------------------------------------------

	def _handle_setup_space(self, context: _RequestContext) -> FakeResponse:
		"""``spaces.setup`` — create a space and its memberships in one call.

		The three space types are three mutually exclusive contracts and the fake enforces
		all of them server-side, even though ``build_setup_space_call`` enforces them client
		-side too. Duplicating the validation is the point: the builder's checks are what the
		relay is *supposed* to do, and a test that only ever exercised them could not tell a
		correct builder from a permissive server.

		Consumes **no per-space budget** (correction 2 — the space does not exist yet), only
		the 60-per-minute project space-write bucket.
		"""
		if context.caller == APP_CALLER:
			# spaces.setup is not available to the app identity — which is also why the caller
			# must be excluded from memberships[]: there is always a human creating the space.
			return self._error(
				403,
				"PERMISSION_DENIED",
				"spaces.setup requires user authentication.",
				google_method=context.google_method,
			)

		body = context.body or {}
		space_body = dict(body.get("space") or {})
		memberships = list(body.get("memberships") or [])
		request_id = str(body.get("requestId") or "")
		space_type = str(space_body.get("spaceType") or "")
		display_name = str(space_body.get("displayName") or "").strip()

		if "spaceThreadingState" in space_body:
			# PHASE2_VERIFIED.md §1.2: the field is Output only, spaces.setup states verbatim
			# that spaces with threaded replies aren't supported, and spaces.patch's updateMask
			# does not enumerate it. There is no create-time decision to get wrong because
			# there is no way to ask.
			return self._invalid(
				"space.spaceThreadingState is an output-only field and cannot be set. Spaces with "
				"threaded replies aren't supported.",
				google_method=context.google_method,
			)

		if space_type not in {"SPACE", "GROUP_CHAT", "DIRECT_MESSAGE"}:
			return self._invalid(
				f"space.spaceType must be SPACE, GROUP_CHAT or DIRECT_MESSAGE; got {space_type!r}.",
				google_method=context.google_method,
			)

		if space_type == "SPACE" and not display_name:
			return self._invalid(
				"space.displayName is required for a SPACE.", google_method=context.google_method
			)
		if space_type != "SPACE" and display_name:
			return self._invalid(
				f"space.displayName must not be set for a {space_type}.",
				google_method=context.google_method,
			)

		emails: list[str] = []
		for entry in memberships:
			member = dict(dict(entry).get("member") or {})
			name = str(member.get("name") or "")
			if not name.startswith("users/"):
				return self._invalid(
					f"memberships[].member.name must be 'users/{{email}}'; got {name!r}.",
					google_method=context.google_method,
				)
			email = name.split("users/", 1)[1]
			if email.strip().lower() == context.caller.strip().lower():
				return self._invalid(
					"The calling user is added to the space implicitly and must not appear in "
					"memberships[].",
					google_method=context.google_method,
				)
			emails.append(email)

		if space_type == "GROUP_CHAT" and len(emails) < 2:
			return self._invalid(
				f"A GROUP_CHAT requires at least 2 memberships; got {len(emails)}.",
				google_method=context.google_method,
			)
		if space_type == "DIRECT_MESSAGE" and len(emails) != 1:
			return self._invalid(
				f"A DIRECT_MESSAGE requires exactly 1 membership; got {len(emails)}.",
				google_method=context.google_method,
			)

		replay = self._replay(context.google_method, "project", request_id)
		if replay is not None:
			return self._ok(replay)

		if space_type == "DIRECT_MESSAGE":
			key = frozenset({context.caller.strip().lower(), emails[0].strip().lower()})
			existing = self._dms.get(key)
			if existing:
				# Documented: setup RETURNS THE EXISTING DM rather than creating a second one.
				# That is why find_direct_message is a saving and not a precondition — but it is
				# still worth calling, because a read does not spend the 60-writes-per-minute
				# space budget.
				payload = self._space_resource(self._spaces[existing])
				self._remember(context.google_method, "project", request_id, payload)
				return self._ok(payload)

		space = self._new_space(
			space_type=space_type,
			display_name=display_name,
			creator=context.caller,
			single_user_bot_dm=bool(space_body.get("singleUserBotDm")),
		)
		self._add_membership(space.name, context.caller, role="ROLE_MANAGER")
		for email in emails:
			membership = self._add_membership(space.name, email)
			if self.emit_setup_membership_events:
				# Inferred, not documented: a subscription on `spaces/-` should see the
				# membership that added the subscriber. Switchable, because "should" is doing
				# real work in that sentence.
				self._emit(
					event_type=EVENT_MEMBERSHIP_CREATED,
					subject=space.name,
					resource_key="membership",
					resource_name=membership.name,
				)

		if space_type == "DIRECT_MESSAGE":
			self._dms[frozenset({context.caller.strip().lower(), emails[0].strip().lower()})] = space.name

		payload = self._space_resource(space)
		self._remember(context.google_method, "project", request_id, payload)
		return self._ok(payload)

	def _handle_get_space(self, context: _RequestContext) -> FakeResponse:
		space = self._spaces.get(context.groups.get("space", ""))
		if space is None or space.deleted:
			return self._not_found(f"Space {context.groups.get('space', '')} not found.")
		return self._ok(self._space_resource(space))

	def _handle_find_direct_message(self, context: _RequestContext) -> FakeResponse:
		name = str(context.query.get("name") or "")
		if not name.startswith("users/"):
			return self._invalid("name must be 'users/{email}'.", google_method=context.google_method)
		key = frozenset({context.caller.strip().lower(), name.split("users/", 1)[1].strip().lower()})
		existing = self._dms.get(key)
		if not existing:
			return self._not_found("No direct message space found for that user.")
		return self._ok(self._space_resource(self._spaces[existing]))

	def _handle_patch_space(self, context: _RequestContext) -> FakeResponse:
		space = self._spaces.get(context.groups.get("space", ""))
		if space is None or space.deleted:
			return self._not_found(f"Space {context.groups.get('space', '')} not found.")

		mask = [
			part.strip() for part in str(context.query.get("updateMask") or "").split(",") if part.strip()
		]
		if "spaceThreadingState" in mask or "spaceThreadingState" in (context.body or {}):
			return self._invalid(
				"spaceThreadingState is not an updatable field; spaces.patch's updateMask does not "
				"enumerate it.",
				google_method=context.google_method,
			)
		if "displayName" in mask:
			space.display_name = str((context.body or {}).get("displayName") or "")

		self._emit(
			event_type=EVENT_SPACE_UPDATED,
			subject=space.name,
			resource_key="space",
			resource_name=space.name,
		)
		return self._ok(self._space_resource(space))

	def _handle_delete_space(self, context: _RequestContext) -> FakeResponse:
		"""Refused by default, and the refusal is the feature.

		``spaces.delete`` cascades: one call removes a space and every message in it. The
		``chat.delete`` / ``chat.app.delete`` scopes are not granted in V1 and must not be, so
		the honest fake answers the way an ungranted scope answers — 403 — and a code path
		that reaches here fails in CI instead of in a customer's history.
		"""
		if not self.allow_space_delete:
			return self._error(
				403,
				"PERMISSION_DENIED",
				"Request had insufficient authentication scopes for spaces.delete.",
				google_method=context.google_method,
			)
		space = self._spaces.get(context.groups.get("space", ""))
		if space is None or space.deleted:
			return self._not_found(f"Space {context.groups.get('space', '')} not found.")
		space.deleted = True
		return self._ok({})

	# -- handlers: memberships --------------------------------------------------------------

	def _handle_list_members(self, context: _RequestContext) -> FakeResponse:
		space = context.groups.get("space", "")
		if space not in self._spaces:
			return self._not_found(f"Space {space} not found.")
		rows = sorted(
			(m for m in self._memberships.values() if m.space == space),
			key=lambda m: (m.create_time_ms, m.name),
		)
		page_size = _int_or(context.query.get("pageSize"), 100)
		start = _page_offset(context.query.get("pageToken"))
		window = rows[start : start + page_size]
		payload: dict[str, Any] = {"memberships": [self._membership_resource(m) for m in window]}
		if start + page_size < len(rows):
			payload["nextPageToken"] = f"page-{start + page_size}"
		return self._ok(payload)

	def _handle_create_membership(self, context: _RequestContext) -> FakeResponse:
		"""Consumes **no per-space budget** — correction 1, and the reason it is worth a test.

		``members.create`` and ``members.delete`` appear in no per-space row in the published
		quota table; they charge only the 300-per-minute project membership bucket. A relay
		that charged them against the space's single write per second would reconcile
		membership roughly 300× slower than the API allows, and nothing would ever surface it
		as a bug — the sync would just be inexplicably slow.
		"""
		space = context.groups.get("space", "")
		if space not in self._spaces:
			return self._not_found(f"Space {space} not found.")
		member = dict((context.body or {}).get("member") or {})
		name = str(member.get("name") or "")
		if not name.startswith("users/"):
			return self._invalid(
				f"membership.member.name must be 'users/{{email}}'; got {name!r}.",
				google_method=context.google_method,
			)
		email = name.split("users/", 1)[1]
		existing = self._membership_of(space, email)
		if existing is not None:
			return self._error(
				409,
				"ALREADY_EXISTS",
				f"{email} is already a member of {space}.",
				google_method=context.google_method,
			)
		membership = self._add_membership(space, email, role=str(member.get("role") or "ROLE_MEMBER"))
		self._emit(
			event_type=EVENT_MEMBERSHIP_CREATED,
			subject=space,
			resource_key="membership",
			resource_name=membership.name,
		)
		return self._ok(self._membership_resource(membership))

	def _handle_get_membership(self, context: _RequestContext) -> FakeResponse:
		membership = self._memberships.get(context.groups.get("name", ""))
		if membership is None:
			return self._not_found(f"Membership {context.groups.get('name', '')} not found.")
		return self._ok(self._membership_resource(membership))

	def _handle_delete_membership(self, context: _RequestContext) -> FakeResponse:
		name = context.groups.get("name", "")
		membership = self._memberships.pop(name, None)
		if membership is None:
			return self._not_found(f"Membership {name} not found.")
		self._emit(
			event_type=EVENT_MEMBERSHIP_DELETED,
			subject=membership.space,
			resource_key="membership",
			resource_name=membership.name,
		)
		# The real API returns the deleted Membership, not Empty.
		return self._ok(self._membership_resource(membership))

	# -- handlers: messages ------------------------------------------------------------------

	def _handle_create_message(self, context: _RequestContext) -> FakeResponse:
		"""``messages.create``, with ``requestId`` and ``messageId`` treated as orthogonal.

		Order of the two checks, and why:

		1. **``requestId``** — if this exact request id was already answered for this space,
		   the original response comes back and no second message exists. That is AIP-155
		   replay semantics, and it is what makes an immediate network retry safe.
		2. **``messageId``** — unique within the space *forever*, server-enforced, no expiry.
		   A collision with a **different** ``requestId`` is therefore an error, not a replay:
		   somebody is trying to reuse an id, and silently returning the old message would
		   hide a real bug in id derivation.

		``VERIFY:`` the error code for a ``messageId`` collision was never observed. 409 /
		``ALREADY_EXISTS`` is the AIP-compliant answer and is what this asserts; treat it as a
		stated assumption, not a fact, and pin it the first time a real one is seen.

		Quota is charged **before** either check, which is also an assumption: a deduplicated
		request still crossed the edge. It is the conservative reading — it makes the harness
		stricter than the API might be, never looser.

		``attachment[]`` on the body is accepted in **exactly one shape**:
		``{"attachmentDataRef": {"attachmentUploadToken": …}}``, the token a prior
		``media.upload`` returned. That is the only binding Google documents between an upload
		and a message, and ``Message.attachment`` is *"user-uploaded"* — a caller cannot
		conjure a ``DRIVE_FILE`` here. Anything else is a 400, because a fake that accepted a
		hand-written attachment resource would let somebody build a relay around a call the
		real API refuses. Drive-sourced attachments are arranged with
		:meth:`seed_attachment`.
		"""
		space_name = context.groups.get("space", "")
		space = self._spaces.get(space_name)
		if space is None or space.deleted:
			return self._not_found(f"Space {space_name} not found.")

		request_id = str(context.query.get("requestId") or "")
		message_id = str(context.query.get("messageId") or "")
		text = str((context.body or {}).get("text") or "")
		thread_name = str(dict((context.body or {}).get("thread") or {}).get("name") or "")

		uploaded, refusal = self._claim_upload_tokens(context)
		if refusal is not None:
			return refusal

		if message_id:
			problem = _client_id_problem(message_id)
			if problem:
				return self._invalid(
					f"messageId {message_id!r} is invalid: {problem}", google_method=context.google_method
				)

		replay = self._replay(context.google_method, space_name, request_id)
		if replay is not None:
			return self._ok(replay)

		if message_id and (space_name, message_id) in self._client_ids:
			return self._error(
				409,
				"ALREADY_EXISTS",
				f"A message with client-assigned ID {message_id} already exists in {space_name}.",
				google_method=context.google_method,
			)

		message = self._store_message(
			space=space_name, text=text, sender=context.caller, client_id=message_id, thread=thread_name
		)
		message.attachments = tuple(
			self._build_attachment(
				message,
				source=ATTACHMENT_SOURCE_UPLOADED_CONTENT,
				content_name=entry.content_name,
				content_type=entry.content_type,
				data_ref=entry.data_ref,
			)
			for entry in uploaded
		)
		payload = self._message_resource(message)
		self._remember(context.google_method, space_name, request_id, payload)

		race = self._claim_race(space_name)
		if race is not None:
			# The §4.D race, in order: the event exists before the response does.
			self._emit(
				event_type=EVENT_MESSAGE_CREATED,
				subject=space_name,
				resource_key="message",
				resource_name=message.name,
			)
			if race.during is not None:
				race.during(self)
			if race.delay_ms:
				self.clock.advance(race.delay_ms)
			return self._ok(payload)

		self._emit(
			event_type=EVENT_MESSAGE_CREATED,
			subject=space_name,
			resource_key="message",
			resource_name=message.name,
		)
		return self._ok(payload)

	def _claim_upload_tokens(self, context: _RequestContext) -> tuple[list[_Attachment], FakeResponse | None]:
		"""Resolve ``body.attachment[]`` to the uploads it references. ``(uploads, refusal)``.

		A token is **not** consumed. ``media.upload`` has no ``requestId``, so a retried relay
		job uploads the bytes again and gets a *fresh* token; what stops a second message is
		``messageId``, not token exhaustion. Burning the token here would make the retry fail
		for a reason the real API does not have — and the orphaned upload from the first
		attempt is simply never referenced, which is exactly what production does with it.
		"""
		raw = (context.body or {}).get("attachment")
		if raw in (None, ""):
			return [], None
		if isinstance(raw, Mapping):  # defensive: a single resource where a list is documented
			raw = [raw]
		if not isinstance(raw, list | tuple):
			return [], self._invalid(
				"Message.attachment must be a list.", google_method=context.google_method
			)

		uploads: list[_Attachment] = []
		for entry in raw:
			ref = dict(dict(entry or {}).get("attachmentDataRef") or {})
			token = str(ref.get("attachmentUploadToken") or "").strip()
			if not token:
				return [], self._invalid(
					"Message.attachment entries must carry attachmentDataRef.attachmentUploadToken. "
					"Message.attachment is user-uploaded content; a Drive attachment cannot be "
					"created through this API.",
					google_method=context.google_method,
				)
			stored = self._upload_tokens.get(token)
			if stored is None:
				return [], self._invalid(
					f"attachmentUploadToken {token!r} was not issued by media.upload.",
					google_method=context.google_method,
				)
			uploads.append(stored)
		return uploads, None

	def _handle_get_message(self, context: _RequestContext) -> FakeResponse:
		"""Resolves the ``client-`` alias as well as the Google resource name.

		``spaces/{space}/messages/{clientAssignedMessageId}`` is a documented addressing mode
		— the prefix is part of the id, not an extra path segment — and it is what lets an
		edit be addressed before ``gchat_message_name`` has ever been written back.

		A tombstone is returned rather than 404'd: the resource still exists, it has just lost
		its content. ``VERIFY:`` that is inferred from the ``showDeleted`` behaviour of
		``list``; no page states what ``get`` does with a deleted message.
		"""
		message = self._resolve_message(context.groups.get("name", ""))
		if message is None:
			return self._not_found(f"Message {context.groups.get('name', '')} not found.")
		return self._ok(self._message_resource(message))

	def _handle_patch_message(self, context: _RequestContext) -> FakeResponse:
		"""``patch`` with ``allowMissing`` — an upsert, and it emits ``created`` when it creates.

		``allowMissing=true`` turns an out-of-order or replayed edit into a create rather than
		a 404, which is the safety net under Rule 1. It requires a client-assigned id, because
		there is no other way for the server to know what name to create the message under —
		the fake enforces that rather than trusting the builder to.
		"""
		name = context.groups.get("name", "")
		message = self._resolve_message(name)
		allow_missing = str(context.query.get("allowMissing") or "").lower() == "true"
		mask = [
			part.strip() for part in str(context.query.get("updateMask") or "").split(",") if part.strip()
		]
		text = str((context.body or {}).get("text") or "")

		if not mask:
			return self._invalid(
				"updateMask must name at least one field.", google_method=context.google_method
			)
		if set(mask) - {"text"}:
			# Under user (DWD) auth `text` is the only settable path; the card fields are
			# app-auth only and `quotedMessageMetadata` is removal-only.
			return self._invalid(
				f"updateMask paths {sorted(set(mask) - {'text'})} are not settable under user "
				"authentication.",
				google_method=context.google_method,
			)

		if message is None:
			if not allow_missing:
				return self._not_found(f"Message {name} not found.")
			space_name, _, message_id = name.partition("/messages/")
			if not message_id.startswith(_CLIENT_ID_PREFIX):
				return self._invalid(
					"allowMissing=true requires a client-assigned message id.",
					google_method=context.google_method,
				)
			if space_name not in self._spaces:
				return self._not_found(f"Space {space_name} not found.")
			created = self._store_message(
				space=space_name, text=text, sender=context.caller, client_id=message_id, thread=""
			)
			self._emit(
				event_type=EVENT_MESSAGE_CREATED,
				subject=space_name,
				resource_key="message",
				resource_name=created.name,
			)
			return self._ok(self._message_resource(created))

		if message.delete_time_ms is not None:
			return self._error(
				404,
				"NOT_FOUND",
				"A deleted message cannot be edited.",
				google_method=context.google_method,
			)

		message.text = text
		message.last_update_ms = self.clock.now_ms()
		self._emit(
			event_type=EVENT_MESSAGE_UPDATED,
			subject=message.space,
			resource_key="message",
			resource_name=message.name,
		)
		return self._ok(self._message_resource(message))

	def _handle_delete_message(self, context: _RequestContext) -> FakeResponse:
		"""Tombstone, not erasure — and the content really does go.

		Google's tombstone is rich in metadata and empty of content: ``showDeleted=true``
		returns the delete time and the deletion type, never the text. That single fact is why
		ERPNext keeps the body on its own row after a soft delete — if it did not, nobody
		would have it, and the Phase 6 oversight path would be reading an empty string.

		``deletionType`` follows the author: ``CREATOR_VIA_APP`` when the impersonated caller
		is the message's sender, ``SPACE_OWNER_VIA_APP`` otherwise. ``VERIFY:`` the enum names
		are documented, but whether an impersonated *manager* may delete another member's
		message at all is unproven (``PHASE2_VERIFIED.md`` §8.7) — that blocks a Phase 6
		moderation action, not Phase 2.
		"""
		name = context.groups.get("name", "")
		message = self._resolve_message(name)
		if message is None:
			return self._not_found(f"Message {name} not found.")
		if message.delete_time_ms is not None:
			return self._ok({})

		message.delete_time_ms = self.clock.now_ms()
		message.deletion_type = (
			"CREATOR_VIA_APP"
			if message.sender.strip().lower() == context.caller.strip().lower()
			else "SPACE_OWNER_VIA_APP"
		)
		message.text = ""
		self._emit(
			event_type=EVENT_MESSAGE_DELETED,
			subject=message.space,
			resource_key="message",
			resource_name=message.name,
		)
		# messages.delete returns Empty — a literal `{}`.
		return self._ok({})

	def _handle_list_messages(self, context: _RequestContext) -> FakeResponse:
		"""The reconciliation sweep's only read. Two filterable fields and no more.

		``createTime`` (with ``>`` / ``<``, combinable with ``AND``) and ``thread.name``.
		Anything else is a 400 in the real API, so it is a 400 here — a sweep that silently
		filtered on nothing would compare the wrong window and "repair" messages that were
		never missing, which is worse than not sweeping.
		"""
		space = context.groups.get("space", "")
		if space not in self._spaces:
			return self._not_found(f"Space {space} not found.")

		show_deleted = str(context.query.get("showDeleted") or "").lower() == "true"
		rows = [
			message
			for message in self._ordered_messages(space)
			if show_deleted or message.delete_time_ms is None
		]

		predicate = context.query.get("filter") or ""
		if predicate:
			try:
				rows = [row for row in rows if _matches_filter(row, predicate)]
			except ValueError as exc:
				return self._invalid(str(exc), google_method=context.google_method)

		order_by = str(context.query.get("orderBy") or "").strip()
		if order_by:
			if order_by not in {"createTime", "createTime asc", "createTime desc"}:
				return self._invalid(
					f"orderBy {order_by!r} is not supported; only createTime is orderable.",
					google_method=context.google_method,
				)
			rows.sort(key=lambda m: (m.create_time_ms, m.name), reverse=order_by.endswith("desc"))

		page_size = _int_or(context.query.get("pageSize"), 25)
		start = _page_offset(context.query.get("pageToken"))
		window = rows[start : start + page_size]
		payload: dict[str, Any] = {"messages": [self._message_resource(m) for m in window]}
		if start + page_size < len(rows):
			payload["nextPageToken"] = f"page-{start + page_size}"
		return self._ok(payload)

	# -- handlers: media -----------------------------------------------------------------

	def _handle_upload_attachment(self, context: _RequestContext) -> FakeResponse:
		"""``POST /upload/v1/{parent=spaces/*}/attachments:upload``. **User auth only.**

		``media.upload``'s scope list is exactly ``chat.import``, ``chat.messages``,
		``chat.messages.create`` — ``chat.bot`` is absent, so **a Chat app cannot upload at
		all** and the relay has to impersonate the sending author. The fake enforces that: an
		app-identity caller gets the 403 the real API would give, rather than an attachment
		that only works in tests.

		It shares the per-space write bucket with ``messages.create``, which is why an
		attachment message costs the space two seconds. The fake charges **1000 ms here and
		1000 ms on the create** — deliberately not the 2000 ms that
		``ratelimit.UPLOAD_WRITE_COST_MS`` reserves client-side. The client reserves for both
		writes up front; the server sees two separate ones.

		**The stored blob is the media part, not the whole request.** The body is
		``uploadType=multipart``: a JSON metadata part carrying ``filename``, then the bytes.
		Storing the envelope — which this used to do — makes a round trip return the file
		wrapped in its own MIME frame, so an end-to-end byte comparison can only ever be a
		containment check, and ``contentName`` has nowhere to come from. Parsing it costs one
		``split`` and buys both.
		"""
		space = context.groups.get("space", "")
		if space not in self._spaces:
			return self._not_found(f"Space {space} not found.")
		if context.caller == APP_CALLER:
			return self._error(
				403,
				"PERMISSION_DENIED",
				"media.upload does not accept app authentication; chat.bot is not in its scope list.",
				google_method=context.google_method,
			)

		metadata, media, media_type = _parse_multipart_related(context.raw)
		payload_bytes = media if media else (context.raw or json.dumps(context.body or {}).encode("utf-8"))
		resource_name = f"CHAT-ATT-{self._token(22)}"
		self._attachments[resource_name] = payload_bytes
		token = f"upload-{self._token(12)}"
		# The token is what binds this upload to a later `messages.create`, so everything the
		# created message's `attachment[]` will need is remembered against it now. Google
		# documents no other way to carry a filename from an upload onto a message.
		self._upload_tokens[token] = _Attachment(
			name="",
			content_name=str(metadata.get("filename") or "").strip() or _FALLBACK_CONTENT_NAME,
			content_type=media_type or _FALLBACK_CONTENT_TYPE,
			source=ATTACHMENT_SOURCE_UPLOADED_CONTENT,
			data_ref=resource_name,
		)
		return self._ok(
			{"attachmentDataRef": {"resourceName": resource_name, "attachmentUploadToken": token}}
		)

	def _handle_download_media(self, context: _RequestContext) -> FakeResponse:
		"""``GET /v1/media/{resourceName}?alt=media`` — and this one **is** app-auth capable.

		The asymmetry with upload is the whole attachment design: an app cannot create an
		attachment but can read one, which runs in exactly the direction inbound needs.
		``downloadUri`` and ``thumbnailUri`` are human-only and are not usable with a bearer
		token; a fake that served them would invite a code path that 401s in production.
		"""
		resource = context.groups.get("resource", "")
		if str(context.query.get("alt") or "") != "media":
			return self._invalid("alt=media is required to download attachment bytes.")
		payload = self._attachments.get(resource)
		if payload is None:
			return self._not_found(f"Media {resource} not found.")
		return FakeResponse(
			status_code=200,
			text=payload.decode("utf-8", "replace"),
			headers={"Content-Type": "application/octet-stream"},
			content=payload,
		)

	# -- store helpers ---------------------------------------------------------------------

	def _token(self, length: int) -> str:
		return "".join(self.rng.choice(_ID_ALPHABET) for _ in range(length))

	def _new_space(
		self,
		*,
		space_type: str,
		display_name: str,
		creator: str,
		single_user_bot_dm: bool = False,
	) -> _Space:
		space = _Space(
			name=f"spaces/AAAA{self._token(7)}",
			space_type=space_type,
			display_name=display_name,
			creator=creator,
			create_time_ms=self.clock.now_ms(),
			single_user_bot_dm=single_user_bot_dm,
		)
		self._spaces[space.name] = space
		return space

	def _add_membership(self, space: str, email: str, *, role: str = "ROLE_MEMBER") -> _Membership:
		membership = _Membership(
			name=f"{space}/members/{_user_id_for(email)}",
			space=space,
			email=email,
			role=role,
			state="JOINED",
			create_time_ms=self.clock.now_ms(),
		)
		self._memberships[membership.name] = membership
		return membership

	def _membership_of(self, space: str, email: str) -> _Membership | None:
		return self._memberships.get(f"{space}/members/{_user_id_for(email)}")

	def _store_message(self, *, space: str, text: str, sender: str, client_id: str, thread: str) -> _Message:
		token = self._token(11)
		message = _Message(
			name=f"{space}/messages/{token}.{token}",
			space=space,
			client_id=client_id,
			sender=sender,
			text=text,
			thread=thread or f"{space}/threads/{self._token(11)}",
			create_time_ms=self.clock.now_ms(),
		)
		self._messages[message.name] = message
		if client_id:
			self._client_ids[(space, client_id)] = message.name
		return message

	def _build_attachment(
		self,
		message: _Message,
		*,
		source: str,
		content_name: str,
		content_type: str,
		data_ref: str = "",
		drive_file_id: str = "",
	) -> _Attachment:
		"""Mint one ``_Attachment`` under a message. The one place a data ref is invented.

		``Attachment.name`` is ``{message}/attachments/{id}`` — a child of the *message*, not
		of the space. Getting that wrong would matter: ``Chat Attachment.gchat_attachment_name``
		is a unique index, so a name that did not vary per message would make two coworkers'
		attachments collide and one of them would be silently absorbed as a duplicate.
		"""
		resolved_source = str(source or "").strip() or ATTACHMENT_SOURCE_UPLOADED_CONTENT
		ref = str(data_ref or "").strip()
		if resolved_source == ATTACHMENT_SOURCE_UPLOADED_CONTENT and not ref:
			ref = f"CHAT-ATT-{self._token(22)}"
		return _Attachment(
			name=f"{message.name}/attachments/{self._token(8)}",
			content_name=str(content_name or "").strip() or _FALLBACK_CONTENT_NAME,
			content_type=str(content_type or "").strip() or _FALLBACK_CONTENT_TYPE,
			source=resolved_source,
			data_ref=ref if resolved_source == ATTACHMENT_SOURCE_UPLOADED_CONTENT else "",
			drive_file_id=str(drive_file_id or "").strip()
			if resolved_source == ATTACHMENT_SOURCE_DRIVE_FILE
			else "",
		)

	def _resolve_message(self, name: str) -> _Message | None:
		direct = self._messages.get(name)
		if direct is not None:
			return direct
		space, _, message_id = name.partition("/messages/")
		if message_id.startswith(_CLIENT_ID_PREFIX):
			aliased = self._client_ids.get((space, message_id))
			if aliased:
				return self._messages.get(aliased)
		return None

	def _ordered_messages(self, space: str) -> Iterator[_Message]:
		return iter(
			sorted(
				(m for m in self._messages.values() if m.space == space),
				key=lambda m: (m.create_time_ms, m.name),
			)
		)

	def _claim_race(self, space: str) -> _Race | None:
		for race in self._races:
			if race.space == space and race.remaining > 0:
				race.remaining -= 1
				return race
		return None

	def _replay(self, google_method: str, scope: str, request_id: str) -> dict[str, Any] | None:
		if not request_id:
			return None
		return self._request_ids.get((google_method, scope, request_id))

	def _remember(self, google_method: str, scope: str, request_id: str, payload: Mapping[str, Any]) -> None:
		"""Remember a ``requestId``'s answer — **for the lifetime of the fake**.

		``VERIFY:`` the real deduplication window's length is undocumented: absent from the
		REST reference, the discovery doc, the proto comment and the guide, and AIP-155 leaves
		it to the implementer. Outside whatever it is, a replay creates a **duplicate
		message**. Modelling it as unbounded makes the fake more forgiving than production, so
		nothing may rely on it — ``messageId`` is the durable key and the fake enforces *that*
		one permanently and for real. **Do not quote a window figure anywhere**; no Chat
		documentation supports one.
		"""
		if request_id:
			self._request_ids[(google_method, scope, request_id)] = dict(payload)

	# -- resource rendering -----------------------------------------------------------------

	def _space_resource(self, space: _Space) -> dict[str, Any]:
		"""``spaceThreadingState`` is reported, never accepted.

		``GROUPED_MESSAGES`` for every space this fake creates. ``VERIFY:`` that is the only
		plausible reading for a named space and no document states it, which is exactly why
		``Chat Room.gchat_threading_state`` reads the value back off the created space rather
		than assuming one. For a DM or a group chat it is a straight guess.
		"""
		resource: dict[str, Any] = {
			"name": space.name,
			"spaceType": space.space_type,
			"singleUserBotDm": space.single_user_bot_dm,
			"spaceThreadingState": "GROUPED_MESSAGES",
			"spaceHistoryState": "HISTORY_ON",
			"createTime": rfc3339_from_epoch_ms(space.create_time_ms),
			"importMode": False,
			"adminInstalled": False,
		}
		if space.display_name:
			resource["displayName"] = space.display_name
		return resource

	def _membership_resource(self, membership: _Membership) -> dict[str, Any]:
		return {
			"name": membership.name,
			"state": membership.state,
			"role": membership.role,
			"createTime": rfc3339_from_epoch_ms(membership.create_time_ms),
			"member": {
				"name": f"users/{_user_id_for(membership.email)}",
				"displayName": membership.email.split("@", 1)[0],
				"type": "HUMAN",
			},
		}

	def _attachment_resource(self, attachment: _Attachment) -> dict[str, Any]:
		"""One ``Attachment``, as ``messages.get`` returns it. **The whole documented schema.**

		Read off the v1 discovery document: ``name``, ``contentName``, ``contentType``,
		``source``, ``attachmentDataRef``, ``driveDataRef``, ``downloadUri``, ``thumbnailUri``
		— and **no size field**, which is why an inbound size check cannot be a pre-flight and
		has to happen after the bytes are in memory.

		``downloadUri`` and ``thumbnailUri`` are emitted **on purpose**. They are human,
		browser-session URLs that a bearer token cannot use, and the failure they produce is
		the worst kind: a download built on one works when a developer pastes it into a
		logged-in browser and 401s in every job. A fixture that omitted them would let
		"the planner ignores them" pass vacuously; producing them is what makes the assertion
		mean something.
		"""
		resource: dict[str, Any] = {
			"name": attachment.name,
			"contentName": attachment.content_name,
			"contentType": attachment.content_type,
			"source": attachment.source,
			# Not the real Chat host: `tests/test_chat_guardrails.py` scans every live string
			# under `chat/` for a Google host and cannot tell a fixture from a call site.
			"downloadUri": f"https://{self.service_host}/get_attachment_url?url_type=DOWNLOAD_URL",
			"thumbnailUri": f"https://{self.service_host}/get_attachment_url?url_type=FIFE_URL",
		}
		if attachment.data_ref:
			resource["attachmentDataRef"] = {"resourceName": attachment.data_ref}
		if attachment.drive_file_id:
			resource["driveDataRef"] = {"driveFileId": attachment.drive_file_id}
		return resource

	def _message_resource(self, message: _Message) -> dict[str, Any]:
		"""A ``Message``, or its tombstone.

		The tombstone carries ``deleteTime`` and ``deletionMetadata`` and **no text field at
		all** — not an empty string, which a caller could mistake for a message someone sent
		blank. Content is gone; metadata is not. **``attachment[]`` goes with the text**: an
		attachment is content, and Google's tombstone keeps only metadata. That is the same
		fact that makes ERPNext the only party still holding a deleted message's file.

		``attachment`` is **absent** rather than an empty list on a message that has none —
		the field is ``Optional`` and Google omits it, so a parser that tested for ``[]``
		would be testing a shape the API never sends.
		"""
		resource: dict[str, Any] = {
			"name": message.name,
			"sender": {
				"name": f"users/{_user_id_for(message.sender)}",
				"displayName": message.sender.split("@", 1)[0],
				"type": "HUMAN",
			},
			"createTime": rfc3339_from_epoch_ms(message.create_time_ms),
			"space": {"name": message.space},
			"thread": {"name": message.thread},
		}

		if message.delete_time_ms is not None:
			resource["deleteTime"] = rfc3339_from_epoch_ms(message.delete_time_ms)
			resource["deletionMetadata"] = {"deletionType": message.deletion_type}
			return resource

		resource["text"] = message.text
		resource["argumentText"] = message.text
		if message.attachments:
			resource["attachment"] = [self._attachment_resource(a) for a in message.attachments]
		if message.last_update_ms is not None:
			# Only populated after an edit — the field is documented as empty for a message
			# that has never been edited, and Rule 3 (LAST-WRITER-WINS by lastUpdateTime)
			# treats "missing" as "ERPNext wins", so a fake that always filled it in would make
			# the tie-break branch unreachable.
			resource["lastUpdateTime"] = rfc3339_from_epoch_ms(message.last_update_ms)
		if message.client_id and self.return_client_ids:
			resource["clientAssignedMessageId"] = message.client_id
		return resource


# --------------------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------------------

#: ``google_method`` → the handler method's name. A table rather than ``getattr`` on a
#: derived string, so an unimplemented method is a ``KeyError`` at dispatch instead of a
#: mysterious ``AttributeError`` inside the client's exception handler.
_HANDLERS: Final[Mapping[str, str]] = {
	"spaces.setup": "_handle_setup_space",
	"spaces.get": "_handle_get_space",
	"spaces.findDirectMessage": "_handle_find_direct_message",
	"spaces.patch": "_handle_patch_space",
	"spaces.delete": "_handle_delete_space",
	"spaces.members.list": "_handle_list_members",
	"spaces.members.create": "_handle_create_membership",
	"spaces.members.get": "_handle_get_membership",
	"spaces.members.delete": "_handle_delete_membership",
	"spaces.messages.create": "_handle_create_message",
	"spaces.messages.get": "_handle_get_message",
	"spaces.messages.patch": "_handle_patch_message",
	"spaces.messages.delete": "_handle_delete_message",
	"spaces.messages.list": "_handle_list_messages",
	"media.upload": "_handle_upload_attachment",
	"media.download": "_handle_download_media",
}

_EVENT_METHODS: Final[Mapping[str, str]] = {
	EVENT_MESSAGE_CREATED: "spaces.messages.create",
	EVENT_MESSAGE_UPDATED: "spaces.messages.patch",
	EVENT_MESSAGE_DELETED: "spaces.messages.delete",
	EVENT_MEMBERSHIP_CREATED: "spaces.members.create",
	EVENT_MEMBERSHIP_DELETED: "spaces.members.delete",
	EVENT_SPACE_UPDATED: "spaces.patch",
}


def _method_of_event(event_type: str) -> str:
	"""Which method's fault switches govern an event of this type.

	So ``fake.duplicate_events("spaces.messages.create")`` duplicates message-created events
	and leaves membership events alone — faults stay scoped to a method even on the event
	side, where the event is emitted by a handler rather than by the caller.
	"""
	return _EVENT_METHODS.get(event_type, "")


def _client_id_problem(message_id: str) -> str:
	"""Google's four constraints, enforced server-side. Empty string means "legal".

	Verbatim: begins ``client-``; at most 63 characters; lowercase letters, numbers and
	hyphens only; unique within the space. The first three are checked here; the fourth is
	the ``409`` in :meth:`FakeChatAPI._handle_create_message`, because uniqueness is state and
	these are syntax.
	"""
	if not message_id.startswith(_CLIENT_ID_PREFIX):
		return f"it must begin with {_CLIENT_ID_PREFIX!r}"
	if len(message_id) > _CLIENT_ID_MAX_LENGTH:
		return f"it is {len(message_id)} characters, over the {_CLIENT_ID_MAX_LENGTH} limit"
	if not _CLIENT_ID_RE.match(message_id):
		return "it may contain only lowercase letters, numbers and hyphens"
	return ""


def _parse_multipart_related(raw: bytes) -> tuple[dict[str, Any], bytes, str]:
	"""``uploadType=multipart`` body → ``(metadata, media bytes, media content type)``.

	The boundary is taken from the body's own first line rather than from the ``Content-Type``
	header, and that is not a shortcut: the header does not reach a handler here (the fake
	routes on verb and path, and ``_RequestContext`` deliberately carries no headers beyond
	the caller), while the first line of a well-formed multipart body **is** ``--boundary``.
	Reading it from the payload also means the parser cannot disagree with the payload.

	Total and forgiving: anything it cannot parse comes back as ``({}, b"", "")`` and the
	caller falls back to storing the raw body. A harness that raised here would turn a
	malformed-upload test into a harness crash, which tells you nothing about the client.
	"""
	payload = bytes(raw or b"")
	if not payload.startswith(b"--"):
		return {}, b"", ""
	delimiter, _, _rest = payload.partition(b"\r\n")
	delimiter = delimiter.strip()
	if not delimiter:
		return {}, b"", ""

	parts = [part for part in payload.split(delimiter) if part not in (b"", b"--\r\n", b"--")]
	if len(parts) < 2:
		return {}, b"", ""

	def _split_part(part: bytes) -> tuple[str, bytes]:
		headers, _, body = part.partition(b"\r\n\r\n")
		content_type = ""
		for line in headers.split(b"\r\n"):
			if line.lower().startswith(b"content-type:"):
				content_type = line.split(b":", 1)[1].decode("ascii", "replace").strip()
		# The trailing CRLF belongs to the delimiter that follows, not to the payload.
		return content_type, body[:-2] if body.endswith(b"\r\n") else body

	_metadata_type, metadata_bytes = _split_part(parts[0])
	media_type, media_bytes = _split_part(parts[1])
	try:
		metadata = json.loads(metadata_bytes.decode("utf-8"))
	except (UnicodeDecodeError, ValueError):
		metadata = {}
	return (metadata if isinstance(metadata, dict) else {}), media_bytes, media_type


def _int_or(value: Any, default: int) -> int:
	try:
		parsed = int(str(value))
	except (TypeError, ValueError):
		return default
	return parsed if parsed > 0 else default


def _page_offset(page_token: Any) -> int:
	token = str(page_token or "")
	if token.startswith("page-"):
		try:
			return max(int(token[5:]), 0)
		except ValueError:
			return 0
	return 0


_FILTER_CREATE_TIME_RE: Final[re.Pattern[str]] = re.compile(
	r'^createTime\s*(?P<op>[<>])\s*"(?P<stamp>[^"]+)"$'
)
_FILTER_THREAD_RE: Final[re.Pattern[str]] = re.compile(r'^thread\.name\s*=\s*"(?P<name>[^"]+)"$')


def _matches_filter(message: _Message, predicate: str) -> bool:
	"""Evaluate a ``messages.list`` filter. Raises ``ValueError`` on an unsupported field.

	Only ``createTime`` and ``thread.name`` — the API's entire filter vocabulary. Comparison
	is on the **formatted RFC-3339 string**, which sorts correctly because every timestamp
	this harness produces is UTC with a fixed number of fractional digits. That is a property
	of the fake, not of RFC-3339 in general: a real comparison must parse, because Google's
	offsets and fractional precision vary.
	"""
	for clause in [part.strip() for part in predicate.split(" AND ") if part.strip()]:
		stamp_match = _FILTER_CREATE_TIME_RE.match(clause)
		if stamp_match:
			created = rfc3339_from_epoch_ms(message.create_time_ms)
			stamp = stamp_match.group("stamp")
			if stamp_match.group("op") == ">" and not created > stamp:
				return False
			if stamp_match.group("op") == "<" and not created < stamp:
				return False
			continue
		thread_match = _FILTER_THREAD_RE.match(clause)
		if thread_match:
			if message.thread != thread_match.group("name"):
				return False
			continue
		raise ValueError(
			f"filter clause {clause!r} names a field messages.list cannot filter on; the API "
			"supports exactly createTime and thread.name."
		)
	return True
