# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for attachment relay in both directions — Phase 2 §4.I.

**Shape P: plain pytest functions, and it needs its own ``python -m pytest`` step in
``ci.yml``.** ``python -m unittest`` silently collects zero function-style tests and reports
success; this repo has already shipped a suite that ran nowhere for weeks because of exactly
that, so the style is load-bearing rather than a preference.

**It installs its own ``frappe`` stub**, and therefore must not share a CI step with another
suite that installs one. ``chat/sync/attachments.py`` imports ``frappe`` at module scope, as
``PHASE2_INTERFACES.md`` §9 says every non-pure ``sync/`` module may — so the choice here is a
stub or no coverage at all, and no coverage is not a choice on the only CI tier this repo can
regression-test automatically.

What is being defended, in one line each:

* **The two inbound rules never blur.** ``UPLOADED_CONTENT`` is copied, ``DRIVE_FILE`` is
  linked, and an unrecognised source is recorded rather than guessed at. Getting this wrong
  re-homes Drive's ACL inside ours, permanently and silently.
* **A hostile filename cannot escape.** Inbound names come from other people's Chat clients.
* **The size ceilings, both directions** — chaos test 14's second half.
* **A 403 on ``media.download`` produces a ``Failed`` row and no bytes**, and its message
  carries no bearer token — chaos test 14's first half.
* **The outbound payload is the documented one**, and the *bytes* go to Chat while a private
  ERPNext URL never does.
* **A message with attachments costs the right number of write tokens.**

The permission-parity rows that need a real ``DatabaseQuery``, a real DocPerm stack and a real
private-file route live in ``test_chat_attachments_bench.py``, which CI does not run. Both
files are named in the parity table in ``chat/sync/attachments.py``'s docstring.

No bench, no network, no database. Nothing here contains real employee content.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

# ---------------------------------------------------------------------------
# Bench guard, then the frappe stub
# ---------------------------------------------------------------------------


def _real_frappe_is_installed() -> bool:
	"""Is there an actual ``frappe`` package importable here, as opposed to a stub?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` another suite
	left in ``sys.modules`` has no ``__spec__`` and reads as absent — which is the answer we
	want.
	"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	pytest.skip(
		"bench-free suite: a real frappe is installed, and stubbing over it would break the "
		"rest of the bench run. The bench half of §4.I is test_chat_attachments_bench.py.",
		allow_module_level=True,
	)


class _StubPermissionError(Exception):
	"""Stands in for ``frappe.PermissionError`` so the endpoint's refusals are assertable."""


class _StubDuplicateEntryError(Exception):
	"""``frappe.DuplicateEntryError`` — primary-key collisions only, on the real thing."""


class _StubUniqueValidationError(Exception):
	"""``frappe.UniqueValidationError`` — every **other** unique index.

	Two distinct classes with no shared base beyond ``Exception``, exactly as v16 has them
	(``DuplicateEntryError(NameError)`` vs ``UniqueValidationError(ValidationError)``). The
	stub keeps them unrelated on purpose: a stub that gave them a common base would make
	``except frappe.DuplicateEntryError`` look like it catches both, which is the precise bug
	``PHASE2_VERIFIED.md`` §1.1 exists to stop.
	"""


def install_frappe_stub() -> types.ModuleType:
	"""The minimal ``frappe`` that importing ``chat/sync/attachments.py`` requires.

	House pattern (``test_chat_realtime_targeting``, ``test_chat_webhook_verify``): build the
	module, attach only what the module under test actually reaches at import time, and put it
	in ``sys.modules`` before the import. Deliberately *not* a database: the functions that
	need one are covered on a bench, and a fake ORM good enough to fool them would be a second
	implementation of Frappe to keep in step.
	"""
	frappe = types.ModuleType("frappe")
	frappe.PermissionError = _StubPermissionError
	frappe.DuplicateEntryError = _StubDuplicateEntryError
	frappe.UniqueValidationError = _StubUniqueValidationError
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe._ = lambda text, *a, **k: text
	frappe.local = types.SimpleNamespace(site="test.invalid", response={})
	frappe.session = types.SimpleNamespace(user="Administrator")
	frappe.logger = lambda *a, **k: None
	frappe.db = types.SimpleNamespace(get_single_value=lambda *a, **k: None)
	sys.modules["frappe"] = frappe

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class _Document:
		pass

	document.Document = _Document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	model.document = document
	frappe.model = model

	database_pkg = types.ModuleType("frappe.database")
	database = types.ModuleType("frappe.database.database")

	@contextlib.contextmanager
	def _savepoint(catch: Any = Exception) -> Iterator[None]:
		"""``frappe.database.database.savepoint``, with the one behaviour that matters here.

		A real context manager rather than the ``lambda: None`` this used to be. The ingest's
		dedupe is ``with savepoint(catch=(DuplicateEntryError, UniqueValidationError))``, and a
		stub that was not a context manager made that line unreachable from the bench-free
		tier — which is where §G.8 Rule 2 ("a unique collision is SUCCESS") most needs to be
		proved, because it is the rule a redelivered Pub/Sub event exercises on every replay.

		It swallows exactly what it is told to and re-raises everything else, which is the half
		that stops this from turning a genuine failure into a silent pass. It does **not**
		model the rollback: there is no transaction here, and pretending otherwise would be a
		second implementation of Frappe.
		"""
		try:
			yield
		except catch:
			pass

	database.savepoint = _savepoint
	sys.modules["frappe.database"] = database_pkg
	sys.modules["frappe.database.database"] = database
	database_pkg.database = database
	frappe.database = database_pkg

	return frappe


FRAPPE = install_frappe_stub()

# Imported after the stub: `attachments` and the DocType controller both `import frappe` at
# module scope, so on a bench-free runner those are the stub or they are an ImportError.
from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleChatClient,
	build_create_message_call,
	build_upload_attachment_call,
)
from erpnext_enhancements.chat.sync import attachments
from erpnext_enhancements.chat.sync.ratelimit import (
	SPACE_WRITE_COST_MS,
	UPLOAD_WRITE_COST_MS,
)
from erpnext_enhancements.chat.testing.fake_chat import (
	FakeChatAPI,
	FakeChatSettings,
	FakeResponse,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"

#: A one-pixel-ish payload. Bytes, deliberately non-UTF-8, so a code path that treats an
#: attachment as text corrupts it visibly rather than round-tripping by luck.
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00\x01"


def _client(fake: FakeChatAPI, *, subject: str | None = ALICE, identity: Any = None):
	"""A real ``GoogleChatClient`` wired to the fake — real builders, real retry loop."""
	return GoogleChatClient(
		subject=subject,
		identity=identity or AuthIdentity.USER,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=fake,
	)


def _uploaded(**overrides: Any) -> dict[str, Any]:
	"""An ``UPLOADED_CONTENT`` ``Attachment`` resource, constructed — never captured."""
	resource: dict[str, Any] = {
		"name": "spaces/AAAA1/messages/MMMM1/attachments/ATT1",
		"contentName": "site-photo.png",
		"contentType": "image/png",
		"source": "UPLOADED_CONTENT",
		"attachmentDataRef": {"resourceName": "CHAT-ATT-abc123"},
		# Present in the real payload and never usable by us. Their presence in the fixture is
		# the point: the planner must ignore them, not merely happen not to need them.
		"downloadUri": "https://chat.google.com/api/get_attachment_url?url_type=DOWNLOAD_URL",
		"thumbnailUri": "https://chat.google.com/api/get_attachment_url?url_type=FIFE_URL",
	}
	resource.update(overrides)
	return resource


def _drive(**overrides: Any) -> dict[str, Any]:
	resource: dict[str, Any] = {
		"name": "spaces/AAAA1/messages/MMMM1/attachments/ATT2",
		"contentName": "Pump schedule.xlsx",
		"contentType": "application/vnd.google-apps.spreadsheet",
		"source": "DRIVE_FILE",
		"driveDataRef": {"driveFileId": "1AbCdEfGhIjK"},
		"thumbnailUri": "https://chat.google.com/api/get_attachment_url?url_type=FIFE_URL",
	}
	resource.update(overrides)
	return resource


# ---------------------------------------------------------------------------
# Inbound planning — the two rules, and the one that must never blur
# ---------------------------------------------------------------------------


def test_uploaded_content_is_planned_for_storage_from_the_data_ref() -> None:
	plan = attachments.plan_inbound_attachment(_uploaded())
	assert plan.action is attachments.AttachmentAction.STORE
	assert plan.source == "Uploaded"
	assert plan.data_ref == "CHAT-ATT-abc123"
	assert plan.ingest_state == "Pending"
	assert plan.drive_file_id == ""


def test_the_planner_never_returns_a_download_uri_or_a_thumbnail_uri() -> None:
	"""``downloadUri``/``thumbnailUri`` are human, browser-session URLs.

	They are not usable with a bearer token, and a download built on one works when a
	developer pastes it into a logged-in browser and 401s in every job. The fixture carries
	both so this is a real assertion rather than a vacuous one.
	"""
	plan = attachments.plan_inbound_attachment(_uploaded())
	rendered = repr(plan)
	assert "get_attachment_url" not in rendered
	assert "DOWNLOAD_URL" not in rendered and "FIFE_URL" not in rendered


def test_parity_row_3_drive_link_is_linked_never_copied() -> None:
	"""Parity table row 3. Copying a Drive file re-homes Drive's ACL inside ours."""
	plan = attachments.plan_inbound_attachment(_drive())
	assert plan.action is attachments.AttachmentAction.LINK
	assert plan.source == "Drive Link"
	assert plan.drive_file_id == "1AbCdEfGhIjK"
	assert plan.data_ref == "", "a Drive Link must carry no download handle at all"
	assert plan.ingest_state == "Linked"


def test_parity_row_3_erpnext_never_grants_drive_access() -> None:
	"""Parity table row 3, last column: a viewer without Drive access sees Drive's denial.

	The assertion is structural and it is the strongest one available bench-free: nothing in
	the chat package names the Drive API, so there is no code path that could widen a Drive
	permission on somebody's behalf. If that ever changes, this fails before the behaviour
	ships rather than after somebody's file is shared with a room.
	"""
	source = (attachments.__file__ or "").replace("\\", "/")
	text = open(source, encoding="utf-8").read()
	for forbidden in ("drive.googleapis.com", "www.googleapis.com/drive", "permissions().create"):
		assert forbidden not in text, (
			f"{forbidden!r} appears in the attachment module. ERPNext must not attempt to grant "
			"Drive access: Drive's own ACL is the governing one, and a chat relay that widened it "
			"would be making a sharing decision on the file owner's behalf."
		)


def test_an_uploaded_attachment_with_no_data_ref_is_skipped_with_a_reason() -> None:
	plan = attachments.plan_inbound_attachment(_uploaded(attachmentDataRef={}))
	assert plan.action is attachments.AttachmentAction.SKIP
	assert plan.ingest_state == "Skipped"
	assert "downloadUri" in plan.skip_reason


def test_an_unrecognised_source_is_recorded_rather_than_guessed_at() -> None:
	"""``SOURCE_UNSPECIFIED``, or whatever Google adds next.

	The two known sources have *opposite* ACL consequences, so mapping an unknown one onto
	either is a coin flip on somebody's permission model.
	"""
	plan = attachments.plan_inbound_attachment(_uploaded(source="SOURCE_UNSPECIFIED"))
	assert plan.action is attachments.AttachmentAction.SKIP
	assert "SOURCE_UNSPECIFIED" in plan.skip_reason


def test_a_message_with_no_attachment_key_plans_nothing() -> None:
	"""``Message.attachment`` is absent, not empty, on a message with none."""
	assert attachments.plan_inbound_attachments({"text": "hello"}) == []


def test_attachments_are_planned_in_the_order_google_returned_them() -> None:
	plans = attachments.plan_inbound_attachments({"attachment": [_drive(), _uploaded()]})
	assert [p.action for p in plans] == [
		attachments.AttachmentAction.LINK,
		attachments.AttachmentAction.STORE,
	]


# ---------------------------------------------------------------------------
# Filenames arrive from other people's clients
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	("raw", "expected"),
	[
		# The traversal segments survive as literal text and the LEADING dots do not: what
		# matters is that no `/` reaches the filesystem and that the name cannot become `..`
		# or a dotfile. Preserving the rest keeps the title recognisable to whoever sent it.
		("../../etc/passwd", "_.._etc_passwd"),
		("report/2026.pdf", "report_2026.pdf"),
		("windows\\path.docx", "windows_path.docx"),
		("query?a=1#frag.txt", "query_a=1_frag.txt"),
		("  spaced.txt  ", "spaced.txt"),
		(".hidden", "hidden"),
		("with\rcr.txt", "with_cr.txt"),
		("", "attachment"),
		(None, "attachment"),
	],
)
def test_a_hostile_inbound_filename_is_sanitised_not_rejected(raw: Any, expected: str) -> None:
	"""Sanitise, never raise — the difference between inbound and outbound naming.

	Outbound the name comes from our own ``File`` row and a separator in it is a bug worth
	surfacing (``client.validate_attachment_filename`` raises). Inbound it is whatever
	somebody's Chat client called the file, and refusing the attachment because of the name
	would lose the file rather than the name.
	"""
	assert attachments.safe_attachment_file_name(raw) == expected


def test_a_bidi_override_cannot_disguise_an_extension() -> None:
	"""``exe.txt`` rendered right-to-left reads ``txt.exe``, and no UI shows the control char."""
	spoofed = "invoice‮gnp.exe"
	cleaned = attachments.safe_attachment_file_name(spoofed)
	assert "‮" not in cleaned
	assert cleaned == "invoicegnp.exe"


def test_a_long_filename_is_truncated_to_the_column_width() -> None:
	cleaned = attachments.safe_attachment_file_name("x" * 400 + ".pdf")
	assert len(cleaned) == attachments.MAX_FILE_NAME_LENGTH


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_content_hash_is_lowercase_sha256_and_not_frappes_md5() -> None:
	"""64 hex characters. ``File.content_hash`` is MD5 (32) and dedupes storage on it.

	Ours exists because MD5 has practical chosen-prefix collisions, so ``File.content_hash``
	cannot answer "are these the bytes we downloaded?" once two different blobs can share one.
	"""
	digest = attachments.content_hash(PNG_BYTES)
	assert len(digest) == 64
	assert digest == digest.lower()
	assert digest != attachments.content_hash(PNG_BYTES + b"x")


# ---------------------------------------------------------------------------
# Chaos 14, second half — the size ceilings, both directions
# ---------------------------------------------------------------------------


def test_chaos_14_inbound_attachment_over_the_ceiling_is_skipped_not_failed() -> None:
	"""Skipped is a policy decision that a retry will not change; Failed is a fault that might.

	Conflating them is how a 300 MB file gets re-downloaded every fifteen minutes forever.
	"""
	limits = attachments.AttachmentLimits(chat_bytes=attachments.CHAT_ATTACHMENT_BYTE_LIMIT, erpnext_bytes=0)
	verdict = attachments.check_inbound_size(attachments.CHAT_ATTACHMENT_BYTE_LIMIT + 1, limits)
	assert verdict.accepted is False
	assert "inbound ceiling" in verdict.reason


def test_the_site_file_limit_binds_before_googles_when_it_is_smaller() -> None:
	"""Frappe's default ``max_file_size`` is 10 MB — an order of magnitude under Chat's 200 MB.

	``File.check_max_file_size`` would throw; a ``Skipped`` row saying why is a much more
	legible outcome than a ``MaxFileSizeReachedError`` inside a background job.
	"""
	limits = attachments.AttachmentLimits(chat_bytes=209_715_200, erpnext_bytes=10 * 1024 * 1024)
	assert limits.inbound_ceiling() == 10 * 1024 * 1024
	assert attachments.check_inbound_size(11 * 1024 * 1024, limits).accepted is False
	assert attachments.check_inbound_size(9 * 1024 * 1024, limits).accepted is True


def test_an_unreadable_site_limit_does_not_decline_the_file() -> None:
	"""``erpnext_bytes = 0`` means "unknown", not "zero".

	Declining an attachment on the strength of a number we failed to read would turn an
	unreadable setting into data loss.
	"""
	limits = attachments.AttachmentLimits(chat_bytes=209_715_200, erpnext_bytes=0)
	assert limits.inbound_ceiling() == 209_715_200


def test_chaos_14_outbound_attachment_over_googles_limit_does_not_fail_the_message() -> None:
	"""Over 200 MB: the attachment is dropped, the message still goes, nothing is lost.

	Refusing the whole message would let Google's transport limit decide what an employee may
	say inside ERPNext, which inverts decision #1 — the ERPNext row is the source of truth.
	"""
	limits = attachments.AttachmentLimits()
	big = attachments.OutboundAttachment(
		file="FILE-big",
		file_name="drone-survey.mp4",
		content_type="video/mp4",
		byte_size=attachments.CHAT_ATTACHMENT_BYTE_LIMIT + 1,
		is_private=True,
	)
	small = attachments.OutboundAttachment(
		file="FILE-small",
		file_name="site-photo.png",
		content_type="image/png",
		byte_size=len(PNG_BYTES),
		is_private=True,
	)
	plan = attachments.plan_outbound_attachments([big, small], limits)
	assert [entry.file for entry in plan.upload] == ["FILE-small"]
	assert [entry[0].file for entry in plan.skipped] == ["FILE-big"]
	assert "200" in plan.skipped[0][1] or "209715200" in plan.skipped[0][1]

	notice = attachments.skipped_attachment_notice(plan.skipped, "https://erp.example.com/chat/room/R1")
	assert "drone-survey.mp4" in notice
	assert "erp.example.com" in notice
	assert attachments.skipped_attachment_notice((), "https://erp.example.com") == ""


def test_a_public_local_file_is_never_relayed() -> None:
	"""A public file is served off disk with no permission check; it is not a chat attachment.

	Skipping it here is what keeps the DocType's ``is_private`` invariant from turning an ACL
	mistake into a stuck relay job.
	"""
	public = attachments.OutboundAttachment(
		file="FILE-public",
		file_name="leak.png",
		content_type="image/png",
		byte_size=len(PNG_BYTES),
		is_private=False,
	)
	plan = attachments.plan_outbound_attachments([public], attachments.AttachmentLimits())
	assert plan.upload == ()
	assert "public" in plan.skipped[0][1]


def test_a_zero_byte_attachment_is_refused_in_both_directions() -> None:
	"""Google accepts it; Chat then shows an attachment nobody can open, which is
	indistinguishable from a delivery failure at the only moment anyone looks."""
	limits = attachments.AttachmentLimits()
	assert attachments.check_outbound_size(0, limits).accepted is False
	assert attachments.check_inbound_size(0, limits).accepted is False


# ---------------------------------------------------------------------------
# Chaos 14, first half — a 403 on media.download
# ---------------------------------------------------------------------------


class _ForbiddenTransport:
	"""A transport that answers every request with Google's AIP-193 403.

	Written here rather than added to ``FakeChatAPI`` because the harness's fault injection
	models faults Google *produces on its own* (timeouts, 5xx, 429); a 403 on a download is a
	statement about authorisation, and arming it globally would make every other suite's
	client look unauthorised.
	"""

	body = (
		'{\n  "error": {\n    "code": 403,\n    "message": "The caller does not have permission",\n'
		'    "status": "PERMISSION_DENIED"\n  }\n}'
	)

	def __init__(self) -> None:
		self.calls = 0

	def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
		self.calls += 1
		# Recorded so the assertion below is about the real header the client sends, not about
		# a header the test invented.
		self.last_headers = dict(kwargs.get("headers") or {})
		return FakeResponse(status_code=403, text=self.body, headers={"Content-Type": "application/json"})


def test_chaos_14_a_403_download_raises_a_scrubbed_error_and_never_the_bearer_token() -> None:
	"""The failure a caller sees carries no credential and no Google frame locals.

	``raise … from None`` on every path out of the Google surface: a bare re-raise out of a
	background job publishes the failing frames' locals into the Error Log, and those frames
	hold an ``Authorization: Bearer`` header. This app has leaked private key material exactly
	that way once.
	"""
	transport = _ForbiddenTransport()
	client = GoogleChatClient(
		identity=AuthIdentity.APP,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=lambda *a, **k: "ya29.super-secret-token",
		transport=transport,
	)

	with pytest.raises(attachments.AttachmentDownloadFailed) as caught:
		attachments.fetch_attachment_bytes(client, "CHAT-ATT-abc123")

	message = str(caught.value)
	assert "PERMISSION_DENIED" in message, "the operator must still learn what Google said"
	assert "ya29.super-secret-token" not in message
	assert "Bearer" not in message or "[redacted]" in message
	assert caught.value.__cause__ is None, "raise ... from None, so no Google frame is chained"
	assert "Bearer ya29" in str(transport.last_headers.get("Authorization", "")), (
		"the transport never actually sent a bearer token, so the scrubbing assertion above is "
		"passing vacuously. Fix the harness, do not delete the assertion."
	)


def test_a_403_download_never_reaches_the_byte_store() -> None:
	"""The failure is raised, so ``_store_bytes`` is never called and no ``File`` is written.

	Asserted by construction rather than by mocking: the exception escapes
	:func:`fetch_attachment_bytes`, and ``download_attachment`` writes the ``Failed`` state
	*before* it could reach storage. The bench suite asserts the row state; here we assert the
	control flow that makes it impossible to store bytes we never received.
	"""
	transport = _ForbiddenTransport()
	client = GoogleChatClient(
		identity=AuthIdentity.APP,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=lambda *a, **k: "token",
		transport=transport,
	)
	with pytest.raises(attachments.AttachmentDownloadFailed):
		attachments.fetch_attachment_bytes(client, "CHAT-ATT-abc123")
	assert transport.calls == 1, "a 403 is not retryable and must not be retried"


def test_a_dry_run_download_is_a_failure_and_not_an_empty_file() -> None:
	"""Dry-run answers zero bytes with a marker. Writing that as a ``File`` would store an
	empty attachment that looks exactly like a successful ingest of a corrupt file."""

	class _DryRunClient:
		def download_media(self, resource_name: str) -> dict[str, Any]:
			return {"_media_bytes": b"", "_media_content_type": "application/octet-stream", "dryRun": True}

	with pytest.raises(attachments.AttachmentDownloadFailed) as caught:
		attachments.fetch_attachment_bytes(_DryRunClient(), "CHAT-ATT-abc123")
	assert "dry-run" in str(caught.value)


# ---------------------------------------------------------------------------
# The real download path, against the fake harness
# ---------------------------------------------------------------------------


def test_media_download_round_trips_bytes_through_the_real_client() -> None:
	"""Upload with a user client, download with an **app** client — the whole asymmetry.

	``media.upload``'s scopes omit ``chat.bot`` so an app cannot upload; ``media.download``'s
	include it, so an app can read. That runs in the direction inbound needs, because the
	sender of an inbound attachment is often somebody we hold no DWD mandate for.

	**Byte-exact, and it did not used to be.** The fake stored the whole
	``multipart/related`` request body as the attachment rather than the media part inside
	it, so a round trip returned the file wrapped in its own MIME frame and this could only
	ever be a containment check. ``FakeChatAPI`` now parses the body, which is what makes an
	equality assertion available here and what gives an uploaded attachment a ``contentName``
	to report on the created message.
	"""
	fake = FakeChatAPI()
	space = fake.seed_space(members=[ALICE, BOB])
	uploaded = _client(fake).upload_attachment(space, "site-photo.png", content=PNG_BYTES)
	resource_name = attachments.upload_resource_name_of(uploaded)
	assert resource_name

	app_client = _client(fake, subject=None, identity=AuthIdentity.APP)
	data, served_type = attachments.fetch_attachment_bytes(app_client, resource_name)
	assert data == PNG_BYTES, "the app identity could not read back what the user identity wrote"
	assert served_type


def test_an_app_identity_client_cannot_upload() -> None:
	"""``chat.bot`` is absent from ``media.upload``'s scope list, so this is impossible, not
	merely disallowed. Refusing here names the impossibility instead of surfacing a 403 four
	steps later that reads like a DWD misconfiguration."""

	class _AppClient:
		identity = AuthIdentity.APP

	with pytest.raises(attachments.AttachmentError) as caught:
		attachments.upload_outbound_attachments(
			_AppClient(), space="spaces/AAAA1", message="MSG-1", plan=attachments.OutboundPlan()
		)
	assert "chat.bot" in str(caught.value)


def test_an_identity_less_client_is_refused_rather_than_assumed_to_be_a_user() -> None:
	"""The check is positive — ``is AuthIdentity.USER`` — and not "not APP".

	A client whose identity was never set would sail through a negative check and fail at
	Google with a 403 that reads like a DWD misconfiguration, which is the exact outcome this
	guard exists to prevent. ``PHASE2_VERIFIED.md`` §4 says assert it; this is the assert.
	"""

	class _NoIdentity:
		pass

	with pytest.raises(attachments.AttachmentError):
		attachments.upload_outbound_attachments(
			_NoIdentity(), space="spaces/AAAA1", message="MSG-1", plan=attachments.OutboundPlan()
		)


def test_a_user_identity_with_no_subject_is_refused() -> None:
	"""``AuthIdentity.USER`` with nobody to be is an app token wearing a user's label.

	The token provider falls back to the app credential when there is no subject, so this would
	reach ``media.upload`` as app auth and 403 — the same failure, one layer further from the
	cause.
	"""

	class _NoSubject:
		identity = AuthIdentity.USER
		subject = ""

	with pytest.raises(attachments.AttachmentError) as caught:
		attachments.upload_outbound_attachments(
			_NoSubject(), space="spaces/AAAA1", message="MSG-1", plan=attachments.OutboundPlan()
		)
	assert "subject" in str(caught.value)


class _UploadFailsOnNth:
	"""The fake transport with the *n*th ``media.upload`` refused; everything else passes through.

	Per-call rather than :meth:`FakeChatAPI.fail_with_server_error`, because the harness consumes
	armed faults in order and would therefore fail the **first** upload. The direction that
	matters is a failure *after* a success, which is where a loop either aborts the message or
	drops the token it already had.
	"""

	def __init__(self, fake: FakeChatAPI, *, fail_upload_number: int) -> None:
		self.fake = fake
		self.fail_upload_number = int(fail_upload_number)
		self.uploads = 0

	def __getattr__(self, name: str) -> Any:
		return getattr(self.fake, name)

	def request(self, method: str, url: str, **kwargs: Any) -> Any:
		if str(method).upper() == "POST" and "/attachments:upload" in str(url):
			self.uploads += 1
			if self.uploads == self.fail_upload_number:
				return self.fake._error(
					403,
					"PERMISSION_DENIED",
					"injected: the caller may not upload to this space",
					google_method="media.upload",
				)
		return self.fake.request(method, url, **kwargs)


def test_one_failed_upload_keeps_the_tokens_that_succeeded(monkeypatch: Any) -> None:
	"""Losing the mirror of a *file* must never lose the *message* — or the other files.

	The second of two uploads 403s. Before the repair the exception left
	``upload_outbound_attachments`` and aborted the relay job, which retried the **whole**
	upload set and eventually dead-lettered a message whose text was never the problem. Now the
	first token survives, the loser is reported as data, and the caller decides.

	The reason string is asserted to be scrubbed: it lands in ``Chat Attachment.skip_reason``,
	which anyone holding the DocType can read, and the frames underneath the call hold an
	``Authorization: Bearer`` header.
	"""
	fake = FakeChatAPI(enforce_space_write_quota=False)
	space = fake.seed_space(display_name="Site", members=[ALICE], creator=ALICE)
	transport = _UploadFailsOnNth(fake, fail_upload_number=2)

	client = GoogleChatClient(
		subject=ALICE,
		identity=AuthIdentity.USER,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=transport,
	)

	bytes_by_file = {"FILE-1": PNG_BYTES, "FILE-2": b"%PDF-1.7\n\xff\xd8trailer\n"}
	monkeypatch.setattr(attachments, "_file_bytes", lambda name: bytes_by_file.get(name, b""))

	plan = attachments.OutboundPlan(
		upload=(
			attachments.OutboundAttachment("FILE-1", "site-photo.png", "image/png", len(PNG_BYTES), True),
			attachments.OutboundAttachment("FILE-2", "roof-plan.pdf", "application/pdf", 24, True),
		)
	)

	result = attachments.upload_outbound_attachments(client, space=space, message="MSG-1", plan=plan)

	assert transport.uploads == 2, "the second upload must still be attempted"
	assert [entry.file for entry in result.uploads] == ["FILE-1"]
	assert result.tokens == [result.uploads[0].upload_token]
	assert [entry[0].file for entry in result.failed] == ["FILE-2"]
	reason = result.failed[0][1]
	assert "PERMISSION_DENIED" in reason
	assert "Bearer" not in reason and "Authorization" not in reason


def test_an_upload_answered_without_a_token_is_a_failure_not_a_silent_drop(monkeypatch: Any) -> None:
	"""A 200 with no ``attachmentUploadToken`` binds nothing, so the file is as un-relayed as a
	403'd one. It used to fall out of a ``continue`` and vanish from both lists."""

	class _TokenlessClient:
		identity = AuthIdentity.USER
		subject = ALICE

		def upload_attachment(self, *_a: Any, **_k: Any) -> dict[str, Any]:
			return {"attachmentDataRef": {"resourceName": "CHAT-ATT-1"}}

	monkeypatch.setattr(attachments, "_file_bytes", lambda name: PNG_BYTES)
	plan = attachments.OutboundPlan(
		upload=(attachments.OutboundAttachment("FILE-1", "a.png", "image/png", 10, True),)
	)

	result = attachments.upload_outbound_attachments(
		_TokenlessClient(), space="spaces/AAAA1", message="MSG-1", plan=plan
	)
	assert result.uploads == ()
	assert [entry[0].file for entry in result.failed] == ["FILE-1"]
	assert "attachmentUploadToken" in result.failed[0][1]


# ---------------------------------------------------------------------------
# Outbound payload shaping
# ---------------------------------------------------------------------------


def test_parity_row_1_outbound_relays_bytes_and_never_a_private_url() -> None:
	"""Parity table row 1. A ``/private/files/`` URL in a Chat message 403s for the recipient.

	Chat clients arrive without our session, so the link cannot work and the sender has no way
	to find out. The assertion is on the built request: the bytes are in ``raw_body`` and the
	local path appears nowhere in the call.
	"""
	call = build_upload_attachment_call(
		"spaces/AAAA1", "site-photo.png", content=PNG_BYTES, content_type="image/png"
	)
	assert call.raw_body is not None and PNG_BYTES in call.raw_body
	assert "/private/files/" not in repr(call.body)
	assert "/private/files/" not in repr(call.query)
	assert "/private/files/" not in call.path


def test_attachment_parts_use_the_documented_upload_token_binding() -> None:
	"""``Message.attachment[].attachmentDataRef.attachmentUploadToken``.

	Confirmed against the v1 discovery document: ``Message.attachment`` is not ``readOnly`` so
	it is writable on create, and ``attachmentUploadToken`` is documented as the opaque handle
	"used to create or update Chat messages with attachments". That pair is the only
	documented binding between an upload and a message.
	"""
	parts = attachments.attachment_parts(["tok-1", "", None, "tok-2"])
	assert parts == [
		{"attachmentDataRef": {"attachmentUploadToken": "tok-1"}},
		{"attachmentDataRef": {"attachmentUploadToken": "tok-2"}},
	]


def test_with_attachments_decorates_the_real_builders_output() -> None:
	"""The relay keeps calling ``build_create_message_call``; this only adds ``attachment[]``.

	Every validation the builder performs — the ``client-`` id rules, the identity check on
	notification options, the thread trap — is therefore unchanged, and the Chat host stays in
	one module (``tests/test_chat_guardrails.py`` asserts that).
	"""
	call = build_create_message_call("spaces/AAAA1", "client-" + "a" * 32, "req-1", "here is the survey")
	decorated = attachments.with_attachments(call, ["tok-1"])
	assert decorated is not call, "GoogleCall is frozen; a decorated call is a new value"
	assert decorated.body["text"] == "here is the survey"
	assert decorated.body["attachment"] == [{"attachmentDataRef": {"attachmentUploadToken": "tok-1"}}]
	assert decorated.query == call.query
	assert "attachment" not in (call.body or {}), "the original call must not be mutated"


def test_with_attachments_is_a_no_op_when_there_is_nothing_to_attach() -> None:
	"""So the relay can call it unconditionally instead of branching."""
	call = build_create_message_call("spaces/AAAA1", "client-" + "b" * 32, "req-2", "text only")
	assert attachments.with_attachments(call, []) is call
	assert attachments.with_attachments(call, ["", None]) is call


def test_upload_token_and_resource_name_are_read_from_the_response_shape() -> None:
	response = {"attachmentDataRef": {"resourceName": "CHAT-ATT-1", "attachmentUploadToken": "tok-1"}}
	assert attachments.upload_token_of(response) == "tok-1"
	assert attachments.upload_resource_name_of(response) == "CHAT-ATT-1"
	assert attachments.upload_token_of({}) == ""
	assert attachments.upload_resource_name_of({"attachmentDataRef": {}}) == ""


# ---------------------------------------------------------------------------
# Quota arithmetic
# ---------------------------------------------------------------------------


def test_an_attachment_message_costs_two_write_tokens_and_three_costs_four() -> None:
	"""``media.upload`` shares the per-space 1-write/second bucket with ``messages.create``.

	``ratelimit.UPLOAD_WRITE_COST_MS`` is the one-attachment case of this and not a separate
	rule — a message with three attachments costs the space four seconds, not two.
	"""
	assert attachments.upload_cost_ms(0) == SPACE_WRITE_COST_MS
	assert attachments.upload_cost_ms(1) == UPLOAD_WRITE_COST_MS == 2 * SPACE_WRITE_COST_MS
	assert attachments.upload_cost_ms(3) == 4 * SPACE_WRITE_COST_MS
	assert attachments.upload_cost_ms(-1) == SPACE_WRITE_COST_MS


def test_the_outbound_plan_prices_itself() -> None:
	small = attachments.OutboundAttachment("F1", "a.png", "image/png", 10, True)
	plan = attachments.plan_outbound_attachments([small, small], attachments.AttachmentLimits())
	assert plan.cost_ms == 3 * SPACE_WRITE_COST_MS


# ---------------------------------------------------------------------------
# Settings-derived limits never exceed Google's
# ---------------------------------------------------------------------------


def test_a_settings_value_above_googles_ceiling_cannot_raise_it(monkeypatch: Any) -> None:
	"""``Chat Settings.attachment_byte_limit`` may be stricter than Google and never looser.

	A setting that authorised a 1 GB upload would produce a rejection from Google that reads
	as a transport fault rather than as the configuration mistake it is.
	"""
	monkeypatch.setattr(attachments, "_setting", lambda field, default: 1_000_000_000)
	monkeypatch.setattr(attachments, "_site_file_byte_limit", lambda: 0)
	assert attachments.resolve_limits().chat_bytes == attachments.CHAT_ATTACHMENT_BYTE_LIMIT


def test_a_stricter_settings_value_is_honoured(monkeypatch: Any) -> None:
	monkeypatch.setattr(attachments, "_setting", lambda field, default: 5_000_000)
	monkeypatch.setattr(attachments, "_site_file_byte_limit", lambda: 0)
	limits = attachments.resolve_limits()
	assert limits.chat_bytes == 5_000_000
	assert limits.outbound_ceiling() == 5_000_000


# ---------------------------------------------------------------------------
# The inbound entry point — ingest_message_attachments
# ---------------------------------------------------------------------------
#
# This block needs somewhere for rows to live, so it adds a *small* in-memory store to the
# module's stub rather than a fake ORM. The line it will not cross is the one the module
# docstring draws: nothing here re-implements permissions, DocPerm, DocShare or the private
# file route — those are `test_chat_attachments_bench.py`'s, on a real database. What it does
# implement is the one thing the ingest's correctness rests on and a bench is not needed to
# model: a **real unique index on `gchat_attachment_name`**, raising the same
# `UniqueValidationError` v16 raises, so §G.8 Rule 2 is proved rather than asserted.


class _StubStore:
	"""Tables, an autoincrement name and one unique index. Nothing else.

	The unique index is the whole reason this exists. ``gchat_attachment_name`` is what makes
	a redelivered Workspace Event produce one row instead of two, and a store without it would
	let the ingest look idempotent while resting on a ``SELECT`` that races.
	"""

	#: ``doctype`` → the columns that must be unique together. Only the index the ingest's
	#: idempotency depends on; adding more would be inventing schema.
	UNIQUE: ClassVar[dict[str, tuple[str, ...]]] = {"Chat Attachment": ("gchat_attachment_name",)}

	def __init__(self) -> None:
		self.tables: dict[str, dict[str, dict[str, Any]]] = {}
		self.counter = 0
		self.settings: dict[str, Any] = {}
		self.enqueued: list[dict[str, Any]] = []

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.tables.setdefault(doctype, {})

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		return list(self.table(doctype).values())

	def next_name(self, doctype: str) -> str:
		self.counter += 1
		return f"{doctype.replace(' ', '-').upper()}-{self.counter:04d}"

	def match(self, doctype: str, filters: Any) -> list[dict[str, Any]]:
		if filters is None:
			return []
		if isinstance(filters, str):
			row = self.table(doctype).get(filters)
			return [row] if row else []
		return [
			row
			for row in self.table(doctype).values()
			if all(row.get(key) == value for key, value in dict(filters).items())
		]

	def insert(self, values: dict[str, Any]) -> dict[str, Any]:
		doctype = str(values.get("doctype") or "")
		columns = self.UNIQUE.get(doctype, ())
		if columns:
			key = tuple(values.get(column) for column in columns)
			# An empty unique column is NULL in MariaDB and NULLs never collide — which is the
			# same reason `_existing_attachment` has a no-name branch at all.
			if all(part not in (None, "") for part in key):
				for other in self.table(doctype).values():
					if tuple(other.get(column) for column in columns) == key:
						raise _StubUniqueValidationError(f"{doctype}: duplicate {columns} = {key}")
		values.setdefault("name", self.next_name(doctype))
		if doctype == "File":
			# `_store_bytes` compares this against the number of bytes it handed over, and that
			# comparison is what sets `byte_size_verified` — so the stub has to produce it.
			values["file_size"] = len(values.get("content") or b"")
		self.table(doctype)[str(values["name"])] = values
		return values


class _StubDoc:
	"""``frappe.get_doc``'s return value, as much of it as the ingest touches."""

	def __init__(self, store: _StubStore, values: dict[str, Any]) -> None:
		object.__setattr__(self, "_store", store)
		object.__setattr__(self, "_values", dict(values))

	def __getattr__(self, item: str) -> Any:
		try:
			return self._values[item]
		except KeyError:
			raise AttributeError(item) from None

	def __setattr__(self, item: str, value: Any) -> None:
		self._values[item] = value

	def get(self, item: str, default: Any = None) -> Any:
		return self._values.get(item, default)

	def get_content(self) -> bytes:
		return bytes(self._values.get("content") or b"")

	def insert(self, ignore_permissions: bool = False) -> _StubDoc:
		self._store.insert(self._values)
		return self


class _StubDB:
	def __init__(self, store: _StubStore) -> None:
		self.store = store

	def get_single_value(self, doctype: str, fieldname: str) -> Any:
		return self.store.settings.get(fieldname) if doctype == "Chat Settings" else None

	def exists(self, doctype: str, filters: Any = None) -> Any:
		rows = self.store.match(doctype, filters)
		return rows[0].get("name") if rows else None

	def get_value(
		self, doctype: str, filters: Any = None, fieldname: Any = "name", as_dict: bool = False, **_: Any
	) -> Any:
		rows = self.store.match(doctype, filters)
		if not rows:
			return None
		row = rows[0]
		if isinstance(fieldname, list | tuple):
			picked = {field: row.get(field) for field in fieldname}
			return picked if as_dict else tuple(picked.values())
		return row.get(fieldname)

	def set_value(
		self, doctype: str, name: Any, field_or_map: Any, value: Any = None, update_modified: bool = True
	) -> None:
		values = dict(field_or_map) if isinstance(field_or_map, dict) else {field_or_map: value}
		for row in self.store.match(doctype, name):
			row.update(values)


def _get_doc(store: _StubStore, *args: Any, **_kwargs: Any) -> _StubDoc:
	if args and isinstance(args[0], dict):
		return _StubDoc(store, dict(args[0]))
	doctype, name = str(args[0]), str(args[1])
	row = store.table(doctype).get(name)
	if row is None:
		raise AssertionError(f"no {doctype} named {name!r} in the stub store")
	return _StubDoc(store, row)


@pytest.fixture
def store(monkeypatch: Any) -> _StubStore:
	"""A store, wired into the module's ``frappe`` stub for the duration of one test.

	A fixture rather than a mutation of :func:`install_frappe_stub`, so the pure-planning and
	transport tests above keep running against the minimal stub they were written for. A suite
	where every test suddenly has a database is a suite where the pure functions quietly stop
	being pure.
	"""
	fresh = _StubStore()
	monkeypatch.setattr(FRAPPE, "db", _StubDB(fresh), raising=False)
	monkeypatch.setattr(FRAPPE, "get_doc", lambda *a, **k: _get_doc(fresh, *a, **k), raising=False)
	monkeypatch.setattr(
		FRAPPE,
		"enqueue",
		lambda method, **kwargs: fresh.enqueued.append({"method": method, **kwargs}),
		raising=False,
	)
	# The site file limit is read through `frappe.core.api.file`, which does not exist here.
	# Pinned rather than left to the import failure, so the ceiling under test is Chat's.
	monkeypatch.setattr(attachments, "_site_file_byte_limit", lambda: 0)
	return fresh


def _seed_message(store: _StubStore, *, room: str = "ROOM-1", space: str = "spaces/AAAA1") -> str:
	store.table("Chat Room")[room] = {"name": room, "gchat_space_name": space}
	name = store.next_name("Chat Message")
	store.table("Chat Message")[name] = {"name": name, "room": room, "has_attachments": 0}
	return name


def _rows(store: _StubStore) -> list[dict[str, Any]]:
	return store.rows("Chat Attachment")


def test_the_ingest_applies_both_source_rules_from_one_resource(store: _StubStore) -> None:
	"""One ``Message``, two attachments, two different answers — and the difference is an ACL.

	``UPLOADED_CONTENT`` lands ``Pending`` with a data ref, because ERPNext is about to become
	the durable copy. ``DRIVE_FILE`` lands ``Linked`` with a ``drive_file_id`` and **no**
	pending download, because copying the bytes would re-home Drive's permission model inside
	ours and nothing can un-make that later.
	"""
	message = _seed_message(store)
	result = attachments.ingest_message_attachments(
		message, {"attachment": [_uploaded(), _drive()]}, enqueue_downloads=False
	)

	assert result["created"] == 2
	assert result["linked"] == 1
	assert len(result["attachments"]) == 2

	by_source = {str(row["source"]): row for row in _rows(store)}
	assert by_source["Uploaded"]["ingest_state"] == "Pending"
	assert by_source["Uploaded"]["gchat_attachment_data_ref"] == "CHAT-ATT-abc123"
	assert not by_source["Uploaded"].get("file")
	assert by_source["Drive Link"]["ingest_state"] == "Linked"
	assert by_source["Drive Link"]["drive_file_id"] == "1AbCdEfGhIjK"
	assert not by_source["Drive Link"].get("file")

	assert result["pending"] == [by_source["Uploaded"]["name"]], "only the copied one waits on bytes"


def test_the_ingest_denormalises_the_room_and_flags_the_message(store: _StubStore) -> None:
	"""``room`` is the byte path's ACL scope and ``has_attachments`` is how the SPA knows to ask.

	The message write passes ``update_modified=False``: the D6 digest watermark is
	``(max(seq), count(*), max(modified))``, so an ingest that bumped ``modified`` would
	invalidate every cached digest for the room.
	"""
	message = _seed_message(store, room="ROOM-7")
	attachments.ingest_message_attachments(message, {"attachment": [_uploaded()]}, enqueue_downloads=False)
	assert _rows(store)[0]["room"] == "ROOM-7"
	assert store.table("Chat Message")[message]["has_attachments"] == 1


def test_reprocessing_a_redelivered_event_produces_no_second_row(store: _StubStore) -> None:
	"""Rule 2, against a real unique index: a collision is SUCCESS, not an error.

	Workspace Events are at-least-once and the soak redelivers every tenth one, so this is the
	ordinary case rather than the exotic one.
	"""
	message = _seed_message(store)
	resource = {"attachment": [_uploaded()]}
	first = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)
	second = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)

	assert first["attachments"] == second["attachments"]
	assert (first["created"], second["created"]) == (1, 0)
	assert len(_rows(store)) == 1


def test_a_lost_race_on_the_unique_index_resolves_to_the_winners_row(store: _StubStore) -> None:
	"""Two workers, one redelivered event: the loser must find the row, not raise.

	Forced rather than waited for. The probe in ``_existing_attachment`` is made to miss while
	the row genuinely exists, which is exactly the window between another worker's insert and
	this one's — and it is the window a ``SELECT``-then-``INSERT`` gets wrong.
	"""
	message = _seed_message(store)
	attachments.ingest_message_attachments(message, {"attachment": [_uploaded()]}, enqueue_downloads=False)

	real_exists = FRAPPE.db.exists
	calls = {"n": 0}

	def _blind_first_probe(doctype: str, filters: Any = None) -> Any:
		calls["n"] += 1
		return None if calls["n"] == 1 else real_exists(doctype, filters)

	FRAPPE.db.exists = _blind_first_probe  # type: ignore[method-assign]
	try:
		result = attachments.ingest_message_attachments(
			message, {"attachment": [_uploaded()]}, enqueue_downloads=False
		)
	finally:
		FRAPPE.db.exists = real_exists  # type: ignore[method-assign]

	assert len(_rows(store)) == 1, "the unique index refused the second insert, which is the point"
	assert result["attachments"] == [_rows(store)[0]["name"]]
	assert result["created"] == 0


def test_an_unrecognised_source_is_recorded_skipped_and_downloads_nothing(store: _StubStore) -> None:
	"""The two known sources have opposite ACL consequences, so an unknown one is not guessed at."""
	message = _seed_message(store)
	result = attachments.ingest_message_attachments(
		message, {"attachment": [_uploaded(source="SOURCE_UNSPECIFIED")]}, enqueue_downloads=True
	)
	assert result["skipped"] == 1
	assert result["pending"] == []
	assert store.enqueued == []
	assert "unrecognised" in _rows(store)[0]["skip_reason"]


def test_a_message_with_no_room_records_nothing(store: _StubStore) -> None:
	"""No room means no ACL, and a ``Chat Attachment`` nobody can resolve a permission for."""
	name = store.next_name("Chat Message")
	store.table("Chat Message")[name] = {"name": name, "room": ""}
	assert attachments.ingest_message_attachments(name, {"attachment": [_uploaded()]}) == {
		"message": name,
		"attachments": [],
		"created": 0,
		"pending": [],
		"stored": 0,
		"linked": 0,
		"skipped": 0,
		"failed": 0,
		"has_attachments": 0,
	}
	assert _rows(store) == []


def test_a_resource_with_no_attachments_touches_nothing(store: _StubStore) -> None:
	message = _seed_message(store)
	result = attachments.ingest_message_attachments(message, {"text": "just words"})
	assert result["attachments"] == []
	assert store.table("Chat Message")[message]["has_attachments"] == 0
	assert _rows(store) == []


def test_one_download_is_enqueued_per_uploaded_attachment_and_none_for_drive(store: _StubStore) -> None:
	"""``deduplicate=True`` needs an explicit ``job_id`` — v16 throws without one."""
	message = _seed_message(store)
	attachments.ingest_message_attachments(message, {"attachment": [_uploaded(), _drive()]})
	assert len(store.enqueued) == 1
	job = store.enqueued[0]
	assert job["method"].endswith("attachments.download_attachment")
	assert job["deduplicate"] is True and job["job_id"]
	assert job["enqueue_after_commit"] is True


# ---------------------------------------------------------------------------
# The fake produces the resource, the ingest parses it, the bytes land
# ---------------------------------------------------------------------------


def _inbound_resource(fake: FakeChatAPI, space: str, *, blob: bytes = PNG_BYTES) -> dict[str, Any]:
	"""A ``Message`` resource carrying one real ``UPLOADED_CONTENT`` attachment.

	Produced by ``FakeChatAPI`` and read back through the **real** ``GoogleChatClient``, with
	no decoration anywhere. Until the harness could emit ``attachment[]`` this shape had to be
	injected at the client seam, which made a gap in the fake read as a gap in the pipeline.
	"""
	message = fake.seed_message(space, sender=BOB, text="scan from my phone")
	fake.seed_attachment(message, content_name="scan.png", content_type="image/png", content=blob)
	return _client(fake).get_message(message)


def test_a_fake_produced_resource_ingests_and_stores_its_bytes(store: _StubStore) -> None:
	"""The whole inbound half, end to end: Google's shape → a row → a private ``File``.

	``content_hash`` is **our** SHA-256 and not Frappe's MD5: ``File`` dedupes on MD5, which
	has practical chosen-prefix collisions, so ``File.content_hash`` cannot answer "are these
	the bytes we downloaded?". ``byte_size_verified`` is the narrower claim that the stored
	``File`` reports the length we handed it — Google's ``Attachment`` resource carries no
	size field at all, so there is no server figure to compare against.
	"""
	fake = FakeChatAPI()
	space = fake.seed_space(members=[ALICE, BOB])
	resource = _inbound_resource(fake, space)
	assert resource["attachment"][0]["source"] == "UPLOADED_CONTENT", "the fake really produced it"

	message = _seed_message(store, space=space)
	result = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)
	attachment = result["pending"][0]

	app_client = _client(fake, subject=None, identity=AuthIdentity.APP)
	assert attachments.download_attachment(attachment, client=app_client) == "Stored"

	row = store.table("Chat Attachment")[attachment]
	assert row["ingest_state"] == "Stored"
	assert row["file_size"] == len(PNG_BYTES)
	assert row["content_hash"] == attachments.content_hash(PNG_BYTES)
	assert row["byte_size_verified"] == 1
	assert row["file_name"] == "scan.png"

	stored_file = store.table("File")[row["file"]]
	assert stored_file["content"] == PNG_BYTES
	assert stored_file["is_private"] == 1
	assert stored_file["attached_to_doctype"] == "Chat Message"
	assert stored_file["attached_to_name"] == message


def test_a_fake_produced_message_carrying_both_sources_lands_one_stored_and_one_linked(
	store: _StubStore,
) -> None:
	"""The soak's inbound attachment claim, bench-free and with no overlay anywhere.

	One Chat message, one uploaded blob and one Drive link, straight out of the harness and
	through the real client. What must come out is one row with bytes and one row with a
	``drive_file_id`` and no ``File`` — which is the permission-parity table's rows 2 and 3,
	and the pair that was unreachable while the fake emitted no ``attachment[]``.
	"""
	fake = FakeChatAPI()
	space = fake.seed_space(members=[ALICE, BOB])
	message_name = fake.seed_message(space, sender=BOB, text="scan plus the spec")
	fake.seed_attachment(message_name, content_name="scan.png", content_type="image/png", content=PNG_BYTES)
	fake.seed_attachment(
		message_name,
		source="DRIVE_FILE",
		content_name="Pump schedule.xlsx",
		content_type="application/vnd.google-apps.spreadsheet",
		drive_file_id="1AbCdEfGhIjK",
	)
	resource = _client(fake).get_message(message_name)
	assert len(resource["attachment"]) == 2

	message = _seed_message(store, space=space)
	result = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)
	app_client = _client(fake, subject=None, identity=AuthIdentity.APP)
	for pending in result["pending"]:
		attachments.download_attachment(pending, client=app_client)

	states = {str(row["source"]): str(row["ingest_state"]) for row in _rows(store)}
	assert states == {"Uploaded": "Stored", "Drive Link": "Linked"}
	files = {str(row["source"]): bool(row.get("file")) for row in _rows(store)}
	assert files == {"Uploaded": True, "Drive Link": False}


def test_a_403_fails_that_attachment_and_leaves_the_message_intact(store: _StubStore) -> None:
	"""Losing the mirror of a *file* must never lose the *message*.

	``Failed`` and not ``Skipped``: a 403 is a fault a retry might survive, a ceiling is a
	policy decision it will not. Conflating them is how a permanently refused blob gets
	re-downloaded every fifteen minutes forever — or how a recoverable one never is.
	"""
	message = _seed_message(store)
	result = attachments.ingest_message_attachments(
		message, {"attachment": [_uploaded()]}, enqueue_downloads=False
	)
	attachment = result["pending"][0]

	client = GoogleChatClient(
		identity=AuthIdentity.APP,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=lambda *a, **k: "ya29.super-secret-token",
		transport=_ForbiddenTransport(),
	)
	assert attachments.download_attachment(attachment, client=client) == "Failed"

	row = store.table("Chat Attachment")[attachment]
	assert not row.get("file")
	assert store.table("File") == {}
	assert "PERMISSION_DENIED" in row["skip_reason"]
	assert "ya29.super-secret-token" not in row["skip_reason"]
	assert store.table("Chat Message")[message]["has_attachments"] == 1, "the message is untouched"


def test_an_oversized_inbound_attachment_is_skipped_not_dropped(store: _StubStore) -> None:
	"""A skip is a row an operator can find; a drop is content nobody knows is missing.

	The ceiling is lowered rather than the payload raised — the branch under test is the
	comparison, and an 8 MB fixture would make this suite slow to prove nothing extra.
	"""
	store.settings["attachment_byte_limit"] = 4
	fake = FakeChatAPI()
	space = fake.seed_space(members=[ALICE, BOB])
	resource = _inbound_resource(fake, space)

	message = _seed_message(store, space=space)
	pending = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)["pending"]
	app_client = _client(fake, subject=None, identity=AuthIdentity.APP)
	assert attachments.download_attachment(pending[0], client=app_client) == "Skipped"

	row = store.table("Chat Attachment")[pending[0]]
	assert not row.get("file")
	assert store.table("File") == {}
	assert "exceeds" in row["skip_reason"]


# ---------------------------------------------------------------------------
# A download is not free — the read buckets
# ---------------------------------------------------------------------------


class _RecordingBucket:
	"""A ``SpaceRateLimiter``/``ProjectQuota`` double that records and can refuse."""

	def __init__(self, *, allowed: bool = True) -> None:
		self.allowed = allowed
		self.acquired: list[tuple[str, int]] = []
		self.charged: list[tuple[str, int]] = []

	def acquire(self, space: str, *, cost_ms: int, block: bool = True) -> Any:
		self.acquired.append((space, cost_ms))
		return types.SimpleNamespace(allowed=self.allowed, wait_ms=0, next_free_ms=0)

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		self.charged.append((bucket, limit))
		return self.allowed


def _pending_attachment(store: _StubStore, fake: FakeChatAPI) -> tuple[str, str]:
	space = fake.seed_space(members=[ALICE, BOB])
	resource = _inbound_resource(fake, space)
	message = _seed_message(store, space=space)
	pending = attachments.ingest_message_attachments(message, resource, enqueue_downloads=False)["pending"]
	return pending[0], space


def test_a_download_charges_the_space_read_bucket_and_the_project_bucket(
	store: _StubStore, monkeypatch: Any
) -> None:
	"""15 reads/second/space, and a project window that is **not** the message-read one.

	Two independent claims, and the second is the one that is easy to get wrong: Google
	buckets reads by category, so a ``media.download`` charged to ``message_reads`` would
	spend a budget it does not use and under-use the one it does. Neither is the write bucket
	— charging a read there would let an ingest storm starve the room's outbound FIFO of the
	one write per second the whole relay design is paced around.
	"""
	fake = FakeChatAPI()
	attachment, space = _pending_attachment(store, fake)
	bucket = _RecordingBucket()
	monkeypatch.setattr(attachments, "space_read_limiter", lambda: bucket)
	monkeypatch.setattr(attachments, "project_quota", lambda: bucket)

	app_client = _client(fake, subject=None, identity=AuthIdentity.APP)
	assert attachments.download_attachment(attachment, client=app_client) == "Stored"

	assert bucket.acquired == [(space, attachments.SPACE_READ_COST_MS)]
	assert bucket.acquired[0][1] == 1000 // 15
	assert bucket.charged == [
		(attachments.BUCKET_ATTACHMENT_READS, attachments.DEFAULT_PROJECT_ATTACHMENT_READS_PER_MINUTE)
	]


def test_the_read_bucket_is_keyed_apart_from_the_write_bucket() -> None:
	"""Same class, different Redis key. One key would make a read spend a write's second."""
	from erpnext_enhancements.chat.sync import ratelimit

	assert attachments.SPACE_READ_KEY_PREFIX != ratelimit.SpaceRateLimiter().key_prefix
	assert attachments.space_read_limiter().key_prefix == attachments.SPACE_READ_KEY_PREFIX


def test_a_refused_read_budget_leaves_the_row_pending_for_the_sweeper(
	store: _StubStore, monkeypatch: Any
) -> None:
	"""Out of budget is neither a fault nor a policy decision, so it is neither state.

	The row is left **untouched** — including its ``modified``, which is this table's retry
	clock, because there is no ``attempts`` column. Writing ``Failed`` here would burn the
	cooldown on a call that never happened, and the bytes would arrive fifteen minutes late
	for no reason.
	"""
	fake = FakeChatAPI()
	attachment, _space = _pending_attachment(store, fake)
	refusing = _RecordingBucket(allowed=False)
	monkeypatch.setattr(attachments, "space_read_limiter", lambda: refusing)
	monkeypatch.setattr(attachments, "project_quota", lambda: refusing)

	before = dict(store.table("Chat Attachment")[attachment])
	client = _client(fake, subject=None, identity=AuthIdentity.APP)
	assert attachments.download_attachment(attachment, client=client) == "Pending"
	assert store.table("Chat Attachment")[attachment] == before
	assert store.table("File") == {}


def test_a_redis_fault_fails_open_rather_than_stopping_every_download(
	store: _StubStore, monkeypatch: Any
) -> None:
	"""A cache outage must not become a permanent hole in the message record.

	Google enforces its own limits and answers 429, which the transport's backoff handles.
	The bucket is an optimisation; backoff is the correctness mechanism.
	"""
	fake = FakeChatAPI()
	attachment, _space = _pending_attachment(store, fake)

	def _explode() -> Any:
		raise RuntimeError("redis is down")

	monkeypatch.setattr(attachments, "space_read_limiter", _explode)
	monkeypatch.setattr(attachments, "project_quota", _explode)

	client = _client(fake, subject=None, identity=AuthIdentity.APP)
	assert attachments.download_attachment(attachment, client=client) == "Stored"


def test_charging_a_room_with_no_space_still_charges_the_project_bucket(monkeypatch: Any) -> None:
	"""An unresolvable space skips the per-space bucket and never skips the project one.

	The project window is the backstop a runaway loop actually hits; dropping it because one
	row's room lookup failed would remove the only limit left.
	"""
	bucket = _RecordingBucket()
	monkeypatch.setattr(attachments, "space_read_limiter", lambda: bucket)
	monkeypatch.setattr(attachments, "project_quota", lambda: bucket)
	assert attachments.charge_attachment_read("") is True
	assert bucket.acquired == []
	assert bucket.charged and bucket.charged[0][0] == attachments.BUCKET_ATTACHMENT_READS
