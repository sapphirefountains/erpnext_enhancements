# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Attachment relay, both directions — Phase 2 §4.I.

The whole module is shaped by one verified asymmetry in Google's own scope lists
(``PHASE2_VERIFIED.md`` §4), and everything else follows from it:

* **A Chat app cannot upload.** ``media.upload``'s scopes are exactly ``chat.import``,
  ``chat.messages`` and ``chat.messages.create``. ``chat.bot`` — the app-authentication
  scope — is *absent*, so no app-auth token can create an attachment at all. The outbound
  relay must impersonate the sending author. Our DWD grant holds ``chat.messages``, so it
  is authorised; :func:`upload_outbound_attachments` therefore refuses an app-identity
  client rather than letting a 403 surface four steps later looking like a DWD
  misconfiguration.
* **A Chat app can download.** ``media.download``'s scopes *include* ``chat.bot``. That
  runs in exactly the direction inbound needs, because the sender of an inbound attachment
  is frequently somebody we hold no DWD mandate for. :func:`fetch_attachment_bytes`
  therefore accepts either identity and the ingest job builds an app client.

**Never relay a private ERPNext URL into a Chat message.** Chat clients cannot fetch
``/private/files/…`` — they arrive without our session — so the recipient gets a link that
403s and the sender has no way to know. Bytes are uploaded; URLs never are.

**An attachment message costs two tokens of the space's 1-write/second budget**, because
``media.upload`` shares the bucket with ``messages.create``. :func:`upload_cost_ms` is the
arithmetic, and it is ``(n + 1) × 1000`` for *n* attachments rather than a flat 2000 —
``ratelimit.UPLOAD_WRITE_COST_MS`` is the one-attachment case of it. The charge itself is
``outbound.charge_attachment_write``, made **once, before the upload loop**, and it also
spends *n* from the separate 600-per-minute project attachment-write bucket (that one is not
the message-write bucket — ``PHASE2_VERIFIED.md`` §2 correction 3).

**The upload runs inside the ``Message Create`` relay job, not as a job of its own.** The
ordering constraint is absolute — a Chat message cannot reference an attachment that has not
been uploaded — and an ``Attachment Upload`` job would have to hand an ``attachmentUploadToken``
to a *later* row, for which Google documents no lifetime at all. So one job does the whole
burst: reserve, upload, create. ``outbound.OPERATION_ATTACHMENT_UPLOAD`` keeps a registered
handler so a stray row is ``Skipped`` with that explanation rather than dead-lettering on a
missing dispatch entry.

--------------------------------------------------------------------------------------
Inbound: two sources, two different rules, and the difference is an ACL decision
--------------------------------------------------------------------------------------

``UPLOADED_CONTENT``
    Download with ``media.download`` using **the data ref** and store as a private ``File``
    attached to the message row. ERPNext becomes the durable copy. ``downloadUri`` and
    ``thumbnailUri`` are documented as human, browser-session URLs, are not usable with a
    bearer token, and are never stored (see :func:`plan_inbound_attachment`).

``DRIVE_FILE``
    **Do not copy the bytes.** Store ``driveFileId`` and render a link. A Chat-hosted blob
    is gated by space membership; a Drive file is gated by Drive's own permissions, which
    are independent of the space. Copying the bytes into ERPNext re-homes somebody else's
    ACL decision inside ours and there is no way to un-make that later — a Drive file whose
    sharing is revoked tomorrow is still readable from our copy.

Both rows persist ``source``, ``file_name``, ``content_type``, the data ref and
``content_hash`` so a re-download can prove it fetched the same object and so the same blob
in two rooms is recognised without a second copy.

**One entry point: :func:`ingest_message_attachments`.** The inbound pipeline hands over a
``Chat Message`` name and the fetched ``Message`` resource, and gets back a summary. Nothing
about Google's attachment shapes — the source enum, the two ref types, the two human-only
URIs — is visible from ``sync/inbound.py``, which is the module least able to say anything
sensible about them.

**A download is charged.** ``media.download`` spends the space's 15-reads-per-second bucket
and the per-project attachment-read window (:func:`charge_attachment_read`). Both are
distinct from the write buckets: charging a read against the 1-write-per-second space bucket
would let an ingest storm starve the room's outbound FIFO.

--------------------------------------------------------------------------------------
``content_hash`` is ours and is SHA-256; Frappe's is MD5, and that is why ours exists
--------------------------------------------------------------------------------------

``File.content_hash`` is ``hashlib.md5(...)`` (``frappe/core/doctype/file/utils.py``), and
``File.save_file`` **deduplicates on it**: a new ``File`` whose MD5 and ``is_private`` match
an existing row reuses that row's ``file_url`` and never writes the bytes. MD5 has practical
chosen-prefix collisions, so that dedupe is a place where two genuinely different blobs can
end up sharing one stored object. Our own SHA-256 in ``Chat Attachment.content_hash`` is what
makes "the bytes behind this attachment are the bytes we downloaded" checkable at all, and it
is deliberately **not unique** — the same screenshot legitimately appears in many rooms.

A consequence of the same dedupe worth knowing before it is discovered in an incident: two
attachments in *different rooms* with identical bytes share one ``file_url``, and
``frappe.core.doctype.file.utils.find_file_by_url`` returns the first ``File`` row for that
URL that the caller may download. A member of room A therefore passes the check on a URL room
B's attachment also uses. It discloses nothing — the bytes are byte-identical and the room A
member is entitled to them — but a stranger to both rooms is still refused on every row, and
``test_chat_attachments_bench.py`` asserts exactly that rather than leaving it to reasoning.

--------------------------------------------------------------------------------------
Every chat attachment is ``is_private = 1`` and attached to the message row
--------------------------------------------------------------------------------------

That single decision makes Frappe's private-file check delegate to ``has_permission`` on the
message, so attachment security follows room security by construction. Confirmed in the v16
tree, not assumed: ``/private/files/…`` → ``frappe/app.py`` → ``download_private_file`` →
``find_file_by_url`` → ``File.is_downloadable`` → ``File.has_permission``, which for a row
carrying ``attached_to_doctype``/``attached_to_name`` ends in
``ref_doc.has_permission("read")``. A **public** file is served straight off disk by the web
server with no auth at all; there is no safe variant of a public chat attachment.

**But the delegation cuts both ways, and this is the part that surprises people.**
``Chat Message`` ships **zero DocPerm** (ADR §F.18.1), so ``ref_doc.has_permission("read")``
is ``False`` for *everyone* except Administrator — including the room members who are
entitled to the file. The raw ``/private/files/…`` URL therefore 403s for members too. That
is the intended posture, not a defect: the SPA never publishes a file URL, and the byte path
is :func:`download`, which establishes membership itself through
``chat.permissions.chat_attachment_has_permission`` and is the single place a Phase 6 audit
row will hang.

--------------------------------------------------------------------------------------
Permission parity (§4.I) — filled in, one named test per row
--------------------------------------------------------------------------------------

``bench`` marks the rows that need a real database; everything else is bench-free.

+------------------------+------------------------+---------------------------+--------------------------+--------------------------+------------------------------+
| Attachment origin      | Bytes live in          | ERPNext member sees       | ERPNext non-member sees  | Chat member sees         | Viewer lacking Drive access  |
+========================+========================+===========================+==========================+==========================+==============================+
| SPA upload             | private ERPNext        | the bytes, via            | **403** from             | the uploaded copy,       | n/a                          |
| (``source=ERPNext``)   | ``File`` **+** a       | :func:`download`;         | :func:`download` **and**  | gated by space           |                              |
|                        | Chat copy of the       | ``/private/files/`` 403s  | **403** from             | membership               |                              |
|                        | bytes                  | for members too           | ``/private/files/``      |                          |                              |
+------------------------+------------------------+---------------------------+--------------------------+--------------------------+------------------------------+
| Chat upload            | private ERPNext        | the bytes, via            | **403** from both        | the original             | n/a                          |
| (``UPLOADED_CONTENT``) | ``File`` — the         | :func:`download`          | paths                    | Chat-hosted blob         |                              |
|                        | durable copy           |                           |                          |                          |                              |
+------------------------+------------------------+---------------------------+--------------------------+--------------------------+------------------------------+
| Chat Drive link        | Drive, and only        | a link                    | **no link** — the row    | the link Chat            | Drive's own denial.          |
| (``DRIVE_FILE``)       | Drive; never copied    | (``drive_file_id``),      | is invisible and         | renders                  | **ERPNext must not**         |
|                        |                        | no bytes, no ``File``     | :func:`download` 403s    |                          | **attempt to grant access**  |
+------------------------+------------------------+---------------------------+--------------------------+--------------------------+------------------------------+

======================================================================  ==================
Row                                                                     Test
======================================================================  ==================
1 — SPA upload, member reads the bytes                                  ``test_parity_row_1_spa_upload_member_reads_the_bytes`` (bench)
1 — SPA upload, non-member is refused on both paths                     ``test_parity_row_1_spa_upload_non_member_is_refused`` (bench)
1 — SPA upload relays bytes and never a private URL                     ``test_parity_row_1_outbound_relays_bytes_and_never_a_private_url``
2 — Chat upload is stored private and attached to the message           ``test_parity_row_2_chat_upload_is_stored_private_and_attached`` (bench)
2 — Chat upload, non-member is refused on both paths                    ``test_parity_row_2_chat_upload_non_member_is_refused`` (bench)
3 — Drive link is linked, never copied, and carries no ``File``         ``test_parity_row_3_drive_link_is_linked_never_copied``
3 — Drive link, non-member sees no row and no link                      ``test_parity_row_3_drive_link_non_member_sees_nothing`` (bench)
3 — ERPNext never calls Drive to widen access                           ``test_parity_row_3_erpnext_never_grants_drive_access``
all — identical bytes in two rooms still refuse a stranger              ``test_deduplicated_file_url_still_refuses_a_stranger`` (bench)
all — the raw private URL 403s authenticated **and** unauthenticated    ``test_private_file_url_is_forbidden_for_a_non_member`` (bench)
======================================================================  ==================

--------------------------------------------------------------------------------------
Size limits, and what happens when Chat's 200 MB ceiling is exceeded outbound
--------------------------------------------------------------------------------------

Three ceilings, and they are not the same number:

* **Chat: 200 MB** per attachment (:data:`CHAT_ATTACHMENT_BYTE_LIMIT`, mirrored in
  ``Chat Settings.attachment_byte_limit`` so an operator can lower it, never raise it).
* **ERPNext:** whatever ``site_config.max_file_size`` allows — Frappe's default is 10 MB, an
  order of magnitude *below* Chat's. Enforced by ``File.check_max_file_size``, which throws
  ``MaxFileSizeReachedError``.
* **The message resource: 32,000 bytes** total, which ``sync/budget.py`` owns and which has
  nothing to do with attachment bytes.

**Outbound, over the limit: the attachment is dropped and the message is still relayed**,
with a deep link back to the ERPNext message appended to the text
(:func:`skipped_attachment_notice`) and the ``Chat Attachment`` row marked ``Skipped`` with a
reason. Refusing the whole message
would let Google's transport limit decide what an employee may say inside ERPNext, which
inverts decision #1 — the same reasoning ``Chat Settings.oversized_message_policy`` applies
to text. Nothing is lost: the ERPNext row keeps the file and is the source of truth.

**Inbound, over the limit: no bytes are stored** and the row lands ``Skipped`` with a reason,
which the SPA renders as "stored in Google Chat only". A ``Skipped`` row is *not* the same as
``Failed``: skipped is a policy decision that will not change on a retry, failed is a fault
that will.

``VERIFY:`` an inbound size check cannot be a pre-flight. Google's ``Attachment`` resource
carries **no size field at all** (read from the v1 discovery document this session:
``attachmentDataRef``, ``downloadUri``, ``thumbnailUri``, ``driveDataRef``, ``name``,
``contentName``, ``source``, ``contentType`` — that is the whole schema), and
``client.download_media`` reads the body into memory before anything can measure it. So a
200 MB inbound attachment is a 200 MB allocation in a background worker before we decline it.
Closing that needs a streaming/``Content-Length``-aware download in ``gchat/client.py``,
which this module does not own; it is reported at the checkpoint rather than worked around
here, because a workaround would be a second transport.

--------------------------------------------------------------------------------------
Retries, and why this table's ``modified`` is allowed to move
--------------------------------------------------------------------------------------

There are no RQ retries in Frappe v16 and the production deploy ``FLUSHDB``s the queue Redis,
so an enqueued download does not survive a release. :func:`sweep_pending_attachments` is the
delivery guarantee, exactly as the ``Chat Relay Job`` sweeper is for outbound writes.

``Chat Attachment`` has no ``attempts`` column, so the retry clock is the row's own
``modified`` and the sweeper only re-picks a row that has been quiet for
:data:`RETRY_COOLDOWN_SECONDS`. That is the deliberate **opposite** of the rule on
``Chat Message.sync_state``, which must be written ``update_modified=False`` because the D6
digest watermark is ``(max(seq), count(*), max(modified))`` and a retry that bumped
``modified`` would invalidate every cached digest for the room. Writes this module makes to
``Chat Message`` therefore pass ``update_modified=False``; writes to ``Chat Attachment``
deliberately do not.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

import frappe
from frappe.database.database import savepoint

from erpnext_enhancements.chat.doctype.chat_attachment.chat_attachment import (
	GOOGLE_SOURCE_DRIVE_FILE,
	GOOGLE_SOURCE_MAP,
	GOOGLE_SOURCE_UPLOADED_CONTENT,
	INGEST_FAILED,
	INGEST_LINKED,
	INGEST_PENDING,
	INGEST_SKIPPED,
	INGEST_STORED,
	INGEST_UNSETTLED,
	SOURCE_DRIVE_LINK,
	SOURCE_ERPNEXT,
	SOURCE_UPLOADED,
)
from erpnext_enhancements.chat.gchat.client import (
	DEFAULT_ATTACHMENT_CONTENT_TYPE,
	AuthIdentity,
	GoogleChatClient,
	GoogleChatError,
	attachment_bytes,
	attachment_content_type,
	scrub_secrets,
)
from erpnext_enhancements.chat.sync import ratelimit
from erpnext_enhancements.chat.sync.ratelimit import SPACE_WRITE_COST_MS

#: Google's documented per-attachment ceiling: 200 MB. A constant rather than a settings
#: read, because it is *Google's* number — ``Chat Settings.attachment_byte_limit`` exists so
#: an operator can be stricter, and :func:`resolve_limits` takes the minimum of the two so a
#: mis-set setting can never authorise something the API will reject.
CHAT_ATTACHMENT_BYTE_LIMIT: Final[int] = 209_715_200

#: Frappe's own default when ``site_config.max_file_size`` is unset. Only a fallback: the
#: live value is read from the site, because a bench that has raised it should not have its
#: attachments declined by a number hard-coded here.
DEFAULT_ERPNEXT_FILE_BYTE_LIMIT: Final[int] = 10 * 1024 * 1024

#: ``Chat Attachment.file_name`` is ``Data(255)``. A longer name is truncated rather than
#: rejected — a file with a 300-character name is a real file somebody really sent.
MAX_FILE_NAME_LENGTH: Final[int] = 255

#: What an attachment is called when Google reports no ``contentName``. Deliberately dull
#: and deliberately extension-free: guessing ``.png`` on the strength of a MIME type is how
#: a mislabelled file opens as garbage with nothing in any log to explain it.
FALLBACK_FILE_NAME: Final[str] = "attachment"

#: How long a failed or unfinished ingest rests before the sweeper picks it up again. The
#: retry clock is the row's ``modified`` — see the module docstring on why this table's
#: ``modified`` is allowed to move when ``Chat Message``'s is not.
RETRY_COOLDOWN_SECONDS: Final[int] = 900

#: Sweeper page size when ``Chat Settings.sweeper_batch_size`` is unreadable.
DEFAULT_SWEEP_BATCH: Final[int] = 20

#: The queue a download job goes on. ``long`` because a 200 MB body over a slow link
#: outlives the ``default`` queue's timeout, and a job killed by a timeout looks exactly
#: like a Google fault in the Error Log.
DOWNLOAD_QUEUE: Final[str] = "long"
DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 900

#: 15 reads per second per space ⇒ one ``media.download`` costs 1000/15 ms of that space's
#: **read** budget. A download is not free, and it is not a write either: the read bucket
#: lives under its own Redis key so an ingest storm cannot starve the relay of the one write
#: per second it needs to answer (``PHASE2_VERIFIED.md`` §2). Same numbers and same key
#: prefix as ``sync/reconcile.py``, deliberately — the two share the bucket because Google
#: does.
SPACE_READ_COST_MS: Final[int] = 1000 // 15
SPACE_READ_KEY_PREFIX: Final[str] = "chat:space_read"

#: The per-project attachment **read** bucket. ``ratelimit`` names the five published buckets
#: and this is not one of them: correction 3 establishes that Google buckets reads *by
#: category*, so ``media.download`` cannot be charged to ``message_reads`` — but no page
#: publishes the attachment-read ceiling. The name matches the fake harness's
#: ``PROJECT_BUCKETS`` so the two sides of every test agree about which counter moved.
BUCKET_ATTACHMENT_READS: Final[str] = "attachment_reads"

#: ``VERIFY:`` **assumed, not published.** 3,000/60 s is the least-surprising figure — it is
#: what every read category Google *does* publish uses — and it is a fallback for a
#: ``Chat Settings`` field that does not exist yet, so an operator who needs a different
#: number today lowers ``project_message_writes_per_minute``'s neighbours instead. Do not
#: quote this back as a documented limit.
DEFAULT_PROJECT_ATTACHMENT_READS_PER_MINUTE: Final[int] = 3000

#: Characters that must never survive into ``File.file_name`` or into the SPA's rendering of
#: an attachment title. Path separators and the URL-significant set are what Frappe's own
#: ``get_safe_file_name`` strips; the control characters are ours, because a ``\r`` in a
#: filename is a header-injection primitive the moment anything echoes it.
_UNSAFE_FILE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[/\\%?#\x00-\x1f\x7f]")

_LOGGER_NAME: Final[str] = "chat.sync.attachments"


class AttachmentAction(str, Enum):
	"""What the ingest must *do* with one inbound attachment. Decided purely, then applied.

	Three values rather than two because "we do not recognise this" has to be distinguishable
	from "we deliberately did not copy it": a ``SKIP`` is a row we can show an operator, and a
	silent drop is a row nobody ever finds.
	"""

	#: ``UPLOADED_CONTENT`` — download the bytes and store a private ``File``.
	STORE = "STORE"
	#: ``DRIVE_FILE`` — record ``drive_file_id`` and render a link. Never copy.
	LINK = "LINK"
	#: Unknown source, or an ``UPLOADED_CONTENT`` with no usable data ref. Recorded, not run.
	SKIP = "SKIP"


@dataclass(frozen=True)
class AttachmentLimits:
	"""The three ceilings, resolved once so no call site re-derives them.

	``erpnext_bytes`` of ``0`` means "unknown" and is treated as no ERPNext-side ceiling —
	the site's own ``File.check_max_file_size`` still applies, and declining a file on the
	strength of a number we failed to read would turn an unreadable setting into data loss.
	"""

	chat_bytes: int = CHAT_ATTACHMENT_BYTE_LIMIT
	erpnext_bytes: int = 0

	def inbound_ceiling(self) -> int:
		"""The smallest ceiling an inbound attachment must fit under to be stored."""
		candidates = [value for value in (self.chat_bytes, self.erpnext_bytes) if value > 0]
		return min(candidates) if candidates else CHAT_ATTACHMENT_BYTE_LIMIT

	def outbound_ceiling(self) -> int:
		"""What Chat will accept. The ERPNext ceiling is irrelevant here — the bytes are
		already on our disk, so the only question left is whether Google will take them."""
		return self.chat_bytes if self.chat_bytes > 0 else CHAT_ATTACHMENT_BYTE_LIMIT


@dataclass(frozen=True)
class SizeVerdict:
	"""``(accepted, reason)`` for one file against one ceiling.

	The reason is written for a human reading ``Chat Attachment.skip_reason`` in the desk,
	which is the only place this decision is ever visible after the fact.
	"""

	accepted: bool
	reason: str = ""


@dataclass(frozen=True)
class InboundAttachmentPlan:
	"""Everything the ingest needs about one inbound attachment, decided without I/O.

	Deliberately flat and deliberately stringly-typed: it is written straight onto a
	``Chat Attachment`` row, and a plan that needed interpretation at the write site would
	put half the rules there.
	"""

	action: AttachmentAction
	source: str
	file_name: str
	content_type: str
	data_ref: str = ""
	drive_file_id: str = ""
	gchat_attachment_name: str = ""
	skip_reason: str = ""

	@property
	def ingest_state(self) -> str:
		"""The ``ingest_state`` this plan lands in *before* any download is attempted."""
		if self.action is AttachmentAction.LINK:
			return INGEST_LINKED
		if self.action is AttachmentAction.SKIP:
			return INGEST_SKIPPED
		return INGEST_PENDING


@dataclass(frozen=True)
class OutboundAttachment:
	"""One private ``File`` on a ``Chat Message``, as the relay needs to see it."""

	file: str
	file_name: str
	content_type: str
	byte_size: int
	is_private: bool


@dataclass(frozen=True)
class OutboundUpload:
	"""The result of uploading one file: the token that binds it to a ``messages.create``."""

	file: str
	file_name: str
	upload_token: str
	resource_name: str
	content_hash: str
	byte_size: int


@dataclass(frozen=True)
class OutboundPlan:
	"""What the relay should send, and what it must not.

	``skipped`` is not an error list. Each entry is a file the message keeps and Chat does
	not get, and the message still goes — see the module docstring on the 200 MB ceiling.
	"""

	upload: tuple[OutboundAttachment, ...] = ()
	skipped: tuple[tuple[OutboundAttachment, str], ...] = ()

	@property
	def cost_ms(self) -> int:
		return upload_cost_ms(len(self.upload))


@dataclass(frozen=True)
class OutboundUploadResult:
	"""What actually happened to the upload set: the tokens, and the files that did not make it.

	Two lists rather than a bare ``list[OutboundUpload]`` because **a failed upload must not
	fail the message**. ``media.upload`` is one HTTP call per file against a surface that 403s,
	429s and times out independently of ``messages.create``; if the third of four files fails,
	the correct outcome is a message carrying three attachments and one ``Chat Attachment`` row
	marked ``Failed`` with a reason — not a dead-lettered message nobody in the space ever sees.
	Losing the mirror of a *file* must never lose the *message*, which is the same rule
	``_dead_letter`` states for the message itself.

	``failed`` is shaped like :attr:`OutboundPlan.skipped` — ``(attachment, reason)`` — so the
	caller can concatenate the two straight into :func:`skipped_attachment_notice`. They mean
	different things on the row (``Skipped`` is a policy decision that will not change on a
	retry; ``Failed`` is a fault that might) and the same thing in the message text, which is
	all the recipient can act on.
	"""

	uploads: tuple[OutboundUpload, ...] = ()
	failed: tuple[tuple[OutboundAttachment, str], ...] = ()

	@property
	def tokens(self) -> list[str]:
		"""The ``attachmentUploadToken``s, in upload order, for :func:`with_attachments`."""
		return [entry.upload_token for entry in self.uploads]


class AttachmentError(Exception):
	"""Base for the failures this module raises. Carries no credential and no file bytes."""


class AttachmentDownloadFailed(AttachmentError):
	"""``media.download`` did not produce bytes. The reason is scrubbed and bounded."""


# --------------------------------------------------------------------------------------
# Pure: naming, hashing, sizing
# --------------------------------------------------------------------------------------


def content_hash(data: bytes) -> str:
	"""Lowercase SHA-256 hex of the stored bytes — ``Chat Attachment.content_hash``.

	SHA-256 and not Frappe's MD5, for the reason in the module docstring: ``File`` dedupes on
	MD5 and MD5 has practical chosen-prefix collisions, so ``File.content_hash`` cannot answer
	"are these the bytes we downloaded?". This can.
	"""
	return hashlib.sha256(bytes(data or b"")).hexdigest()


def safe_attachment_file_name(raw: str, *, fallback: str = FALLBACK_FILE_NAME) -> str:
	"""A file name safe to store, to render, and to hand to ``File``.

	**Sanitises rather than raises**, and that is the difference between this and
	``client.validate_attachment_filename``. Outbound, the name comes from our own ``File``
	row and a path separator in it is a bug worth surfacing. Inbound, the name is whatever a
	Chat user's client called the file — hostile input by definition — and refusing to ingest
	an attachment because somebody named it oddly would lose the file rather than the name.

	What it removes, and why each one:

	* path separators and the URL-significant ``% ? #`` — the same set Frappe's own
	  ``get_safe_file_name`` strips before writing to disk, applied here as well so the value
	  we *store* matches the value on disk instead of drifting from it;
	* control characters, so a ``\\r`` in a name cannot become a header when something echoes
	  it back;
	* Unicode ``Cf`` format characters, which include the bidirectional overrides that make
	  ``exe.txt`` render as ``txt.exe`` — a filename spoof that is invisible in every UI;
	* leading dots and leading/trailing whitespace, so a name cannot become ``..`` or hide.

	Truncation is on a character boundary at :data:`MAX_FILE_NAME_LENGTH` because the column
	is ``Data(255)``; the extension is not preserved across the cut, deliberately, because
	silently rebuilding ``name.pdf`` out of a truncated stem invents a name nobody sent.
	"""
	candidate = str(raw or "")
	candidate = "".join(ch for ch in candidate if unicodedata.category(ch) != "Cf")
	candidate = _UNSAFE_FILE_NAME_RE.sub("_", candidate)
	candidate = candidate.strip().lstrip(".").strip()
	if not candidate:
		candidate = str(fallback or FALLBACK_FILE_NAME)
	return candidate[:MAX_FILE_NAME_LENGTH]


def check_inbound_size(byte_length: int, limits: AttachmentLimits) -> SizeVerdict:
	"""Whether downloaded bytes may be stored. Chaos test 14, second half.

	Both ceilings apply: Chat's 200 MB because that is what can exist on the other side, and
	the site's ``max_file_size`` because ``File.check_max_file_size`` will throw otherwise and
	a throw inside an ingest job is a much less legible outcome than a ``Skipped`` row saying
	why.
	"""
	size = max(int(byte_length or 0), 0)
	ceiling = limits.inbound_ceiling()
	if size <= 0:
		return SizeVerdict(False, "the download produced zero bytes")
	if size > ceiling:
		return SizeVerdict(
			False,
			f"{size} bytes exceeds the {ceiling}-byte inbound ceiling "
			f"(Google Chat allows {limits.chat_bytes}; this site's File limit is "
			f"{limits.erpnext_bytes or 'unset'}). The file stays in Google Chat and is not "
			"copied here.",
		)
	return SizeVerdict(True)


def check_outbound_size(byte_length: int, limits: AttachmentLimits) -> SizeVerdict:
	"""Whether a local file may be uploaded to Chat. Chaos test 14, second half, outbound.

	Over the ceiling the message is still relayed without this attachment — see
	:func:`oversize_notice` and the module docstring. Zero bytes is refused for the reason
	``client.build_upload_attachment_call`` refuses it: Google accepts it, Chat then shows an
	attachment nobody can open, and that is indistinguishable from a delivery failure at the
	only moment anyone looks.
	"""
	size = max(int(byte_length or 0), 0)
	ceiling = limits.outbound_ceiling()
	if size <= 0:
		return SizeVerdict(False, "the file is empty; Chat would show an attachment nobody can open")
	if size > ceiling:
		return SizeVerdict(
			False,
			f"{size} bytes exceeds Google Chat's {ceiling}-byte attachment limit. The message "
			"was relayed without it; the file remains on the ERPNext message, which is the "
			"source of truth.",
		)
	return SizeVerdict(True)


def skipped_attachment_notice(skipped: Sequence[tuple[OutboundAttachment, str]], deep_link: str) -> str:
	"""The line appended to a relayed message whose attachments were not sent.

	Says what happened and where the file is, and says it in the *message*, because the
	``Chat Attachment.skip_reason`` this pairs with is visible only to somebody already
	looking at the ERPNext row — which is nobody, at the moment it matters. Returns ``""``
	when nothing was skipped so the caller can concatenate unconditionally.

	Deliberately generic about *why*. The reason is per-file and lives on the row; a notice
	that said "too large" would be wrong for the other skip case (a local ``File`` that is not
	private, which must never be relayed and must never exist), and a notice that enumerated
	reasons would leak operational detail into a space that may contain external members.

	The caller must fit this inside the 32,000-byte message budget with
	``sync/budget.fit_to_byte_budget`` — appending it *after* a fit is how a message that was
	exactly at the limit becomes a 400.
	"""
	if not skipped:
		return ""
	names = ", ".join(entry[0].file_name for entry in skipped)
	plural = "attachment" if len(skipped) == 1 else "attachments"
	tail = f" — open the message in ERPNext: {deep_link}" if deep_link else ""
	return f"[{len(skipped)} {plural} not relayed: {names}{tail}]"


def upload_cost_ms(attachment_count: int) -> int:
	"""Milliseconds of the space's 1-write/second budget an attachment message costs.

	``(n + 1) × 1000``: one write per ``media.upload`` plus one for the ``messages.create``
	they are attached to, because ``media.upload`` shares the per-space bucket rather than
	having one of its own (``PHASE2_VERIFIED.md`` §2). ``ratelimit.UPLOAD_WRITE_COST_MS`` is
	the ``n = 1`` case of this function and not a separate rule; a message with three
	attachments costs four seconds of that space, not two.
	"""
	return (max(int(attachment_count or 0), 0) + 1) * SPACE_WRITE_COST_MS


# --------------------------------------------------------------------------------------
# Pure: inbound planning
# --------------------------------------------------------------------------------------


def plan_inbound_attachment(attachment: Mapping[str, Any]) -> InboundAttachmentPlan:
	"""One Google ``Attachment`` resource → what to do with it. No I/O, total.

	The source enum decides everything (``PHASE2_CONTRACT`` §4.I) and is *output only* on
	Google's side, so it is read and never inferred: an ``UPLOADED_CONTENT`` whose
	``attachmentDataRef.resourceName`` is missing is a ``SKIP`` with a reason, not a guess at
	a download handle.

	``downloadUri`` and ``thumbnailUri`` are read from the resource and **discarded here** —
	they are never returned, never stored, and never logged. They are documented twice as
	human, browser-session URLs that Chat apps must not use, and the failure they produce is
	the worst kind: a download built on them works when a developer pastes one into a
	logged-in browser and 401s in every job.
	"""
	resource = dict(attachment or {})
	google_source = str(resource.get("source") or "").strip()
	# The enum → our `source` translation happens once, through the map on the DocType
	# controller, and never as a string literal at a branch. An unmapped member falls through
	# to the SKIP branch below rather than being coerced into one of the two real values.
	mapped_source = GOOGLE_SOURCE_MAP.get(google_source, "")
	name = str(resource.get("name") or "").strip()
	content_type = str(resource.get("contentType") or "").strip() or DEFAULT_ATTACHMENT_CONTENT_TYPE
	file_name = safe_attachment_file_name(resource.get("contentName") or "")

	data_ref = str(dict(resource.get("attachmentDataRef") or {}).get("resourceName") or "").strip()
	drive_id = str(dict(resource.get("driveDataRef") or {}).get("driveFileId") or "").strip()

	if google_source == GOOGLE_SOURCE_DRIVE_FILE:
		# The bytes stay in Drive under Drive's ACL. A DRIVE_FILE with no driveFileId is still
		# a LINK and not a SKIP: the row records that the message carried a Drive attachment
		# we cannot address, which is a thing an operator can act on. Copying it is the only
		# option that is never correct.
		return InboundAttachmentPlan(
			action=AttachmentAction.LINK,
			source=mapped_source or SOURCE_DRIVE_LINK,
			file_name=file_name,
			content_type=content_type,
			drive_file_id=drive_id,
			gchat_attachment_name=name,
			skip_reason="" if drive_id else "Google reported a Drive attachment with no driveFileId",
		)

	if google_source == GOOGLE_SOURCE_UPLOADED_CONTENT:
		if not data_ref:
			return InboundAttachmentPlan(
				action=AttachmentAction.SKIP,
				source=mapped_source or SOURCE_UPLOADED,
				file_name=file_name,
				content_type=content_type,
				gchat_attachment_name=name,
				skip_reason=(
					"no attachmentDataRef.resourceName, which is the only handle media.download "
					"accepts; downloadUri and thumbnailUri are human-only and cannot be used"
				),
			)
		return InboundAttachmentPlan(
			action=AttachmentAction.STORE,
			source=mapped_source or SOURCE_UPLOADED,
			file_name=file_name,
			content_type=content_type,
			data_ref=data_ref,
			gchat_attachment_name=name,
		)

	# SOURCE_UNSPECIFIED, or an enum member Google adds after this was written. Recorded,
	# never guessed at: the two known sources have opposite ACL consequences, so treating an
	# unknown one as either is a coin flip on somebody's permission model. The action is SKIP
	# regardless, so nothing is copied either way; `source` is reqd on the DocType and is
	# labelled from which ref Google actually sent purely so the row reads honestly in the
	# desk — it is a description of the payload, not a decision about it.
	return InboundAttachmentPlan(
		action=AttachmentAction.SKIP,
		source=SOURCE_UPLOADED if data_ref else SOURCE_DRIVE_LINK,
		file_name=file_name,
		content_type=content_type,
		data_ref=data_ref,
		drive_file_id=drive_id,
		gchat_attachment_name=name,
		skip_reason=f"unrecognised Attachment.source {google_source or '<empty>'!r}",
	)


def plan_inbound_attachments(message_resource: Mapping[str, Any]) -> list[InboundAttachmentPlan]:
	"""Every attachment on one ``Message`` resource, in the order Google returned them.

	``Message.attachment`` is a list and is absent on a message with none — never an empty
	list to test for. Order is preserved because it is the order the sender saw and there is
	nothing better to sort by: attachments carry no sequence and no timestamp.
	"""
	raw = (message_resource or {}).get("attachment") or []
	if isinstance(raw, Mapping):  # defensive: a single resource where a list was documented
		raw = [raw]
	return [plan_inbound_attachment(item) for item in raw if isinstance(item, Mapping)]


# --------------------------------------------------------------------------------------
# Pure: outbound payload shaping
# --------------------------------------------------------------------------------------


def upload_token_of(upload_response: Mapping[str, Any]) -> str:
	"""``attachmentDataRef.attachmentUploadToken`` from an ``UploadAttachmentResponse``."""
	ref = dict((upload_response or {}).get("attachmentDataRef") or {})
	return str(ref.get("attachmentUploadToken") or "").strip()


def upload_resource_name_of(upload_response: Mapping[str, Any]) -> str:
	"""``attachmentDataRef.resourceName`` from an ``UploadAttachmentResponse``.

	Stored on the row as the download handle. Google populates one or both of the two fields
	and the reference does not promise which, so both are read and neither is required.
	"""
	ref = dict((upload_response or {}).get("attachmentDataRef") or {})
	return str(ref.get("resourceName") or "").strip()


def attachment_parts(upload_tokens: Sequence[str]) -> list[dict[str, Any]]:
	"""``Message.attachment[]`` for a ``messages.create`` that carries uploaded files.

	Confirmed against the v1 discovery document: ``Message.attachment`` is
	``Optional. User-uploaded attachment.`` and is **not** ``readOnly``, so it is writable on
	create; ``AttachmentDataRef.attachmentUploadToken`` is *"an opaque token containing a
	reference to an uploaded attachment… used to create or update Chat messages with
	attachments"*. That pair is the documented binding between an upload and a message, and
	it is the only one.
	"""
	return [
		{"attachmentDataRef": {"attachmentUploadToken": token}}
		for token in (upload_tokens or ())
		if str(token or "").strip()
	]


def with_attachments(call: Any, upload_tokens: Sequence[str]) -> Any:
	"""Return ``call`` with ``attachment[]`` added to its body. The relay's one-line seam.

	``dataclasses.replace`` on the :class:`~erpnext_enhancements.chat.gchat.client.GoogleCall`
	the relay already built, rather than a new builder or a new parameter on
	``build_create_message_call``. Three reasons, in order of how much they matter:

	1. **The Chat host stays in one module.** ``tests/test_chat_guardrails.py`` asserts that
	   ``chat.googleapis.com`` appears in ``gchat/client.py`` and nowhere else; a builder here
	   would either duplicate the path or need the constant.
	2. **The real builder still runs.** Every validation ``build_create_message_call``
	   performs — the ``client-`` id rules, the identity check on notification options, the
	   thread trap — is unchanged, because this decorates its output rather than replacing it.
	3. **Ownership.** ``client.py`` belongs to the transport task; adding an ``attachment=``
	   parameter there is the tidier long-term shape and is reported at the checkpoint rather
	   than taken unilaterally mid-wave.

	Passing no tokens returns the call unchanged, so the relay can call this unconditionally.
	"""
	parts = attachment_parts(upload_tokens)
	if not parts:
		return call
	body = dict(getattr(call, "body", None) or {})
	body["attachment"] = parts
	return dataclasses.replace(call, body=body)


# --------------------------------------------------------------------------------------
# Settings and limits (Frappe)
# --------------------------------------------------------------------------------------


def _logger() -> Any:
	"""``frappe.logger`` behind a helper: a logging failure must never fail an ingest."""
	try:
		return frappe.logger(_LOGGER_NAME)
	except Exception:  # pragma: no cover - only reachable on a half-booted frappe
		return None


def _setting(fieldname: str, default: int) -> int:
	"""One numeric ``Chat Settings`` value, defensively.

	``getattr(obj, field, None) or default`` per the house rule: these reads happen during
	``bench migrate`` and during ERPNext's own test bootstrap, when the field may not exist
	yet, and a missing tuning value must degrade to a default rather than crash a worker.
	"""
	try:
		raw = frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return int(default)
	try:
		value = int(raw or 0)
	except (TypeError, ValueError):
		return int(default)
	return value if value > 0 else int(default)


def _site_file_byte_limit() -> int:
	"""``site_config.max_file_size``, or Frappe's default. Never raises.

	Read from the framework rather than from ``Chat Settings`` because it is the framework's
	limit — ``File.check_max_file_size`` enforces it, and a second copy in our settings would
	be wrong on the day somebody raises the real one.
	"""
	try:
		from frappe.core.api.file import get_max_file_size

		return int(get_max_file_size() or 0) or DEFAULT_ERPNEXT_FILE_BYTE_LIMIT
	except Exception:
		return DEFAULT_ERPNEXT_FILE_BYTE_LIMIT


def resolve_limits() -> AttachmentLimits:
	"""The live ceilings. ``Chat Settings`` may lower Google's number and never raise it."""
	configured = _setting("attachment_byte_limit", CHAT_ATTACHMENT_BYTE_LIMIT)
	return AttachmentLimits(
		chat_bytes=min(max(configured, 0) or CHAT_ATTACHMENT_BYTE_LIMIT, CHAT_ATTACHMENT_BYTE_LIMIT),
		erpnext_bytes=_site_file_byte_limit(),
	)


def mirroring_attachments() -> bool:
	"""``Chat Settings.mirror_attachments``. Off by default; outbound reads it, inbound does not.

	The asymmetry is deliberate. Outbound is a choice — whether our files go to Google.
	Inbound is not: the attachment already exists in the space and ERPNext is the record of
	what was said, so declining to ingest it would leave a message whose content nobody can
	reconstruct. The flag gates :func:`plan_outbound_attachments` and nothing else.
	"""
	try:
		return bool(frappe.db.get_single_value("Chat Settings", "mirror_attachments"))
	except Exception:
		return False


# --------------------------------------------------------------------------------------
# Inbound (Frappe)
# --------------------------------------------------------------------------------------


def _clear_expected_duplicate_message() -> None:
	"""Drop the ``msgprint`` a swallowed unique-index collision leaves behind.

	``show_unique_validation_message`` calls ``frappe.msgprint`` **before** raising, so an
	*expected* duplicate — which is a success, per §G.8 Rule 2 — otherwise surfaces a
	user-visible "must be unique" toast from a background job.
	"""
	for name in ("clear_last_message", "clear_messages"):
		clear = getattr(frappe, name, None)
		if callable(clear):
			try:
				clear()
			except Exception:
				pass
			return


def _insert_deduped(doc: Any) -> str:
	"""Insert, treating a unique collision as success. Returns the row name, or ``""``.

	**Both** exceptions are caught. ``frappe.DuplicateEntryError`` is raised only for a
	primary-key collision; every *other* unique index — which is what
	``gchat_attachment_name`` is — raises ``frappe.UniqueValidationError``, and the two share
	no base beyond ``Exception``
	(``DuplicateEntryError(NameError)`` vs ``UniqueValidationError(ValidationError)``). The
	ADR names only the first and the ADR is wrong; see ``PHASE2_VERIFIED.md`` §1.1.

	The ``savepoint`` is mandatory rather than hygiene: a failed statement can poison the
	surrounding transaction, and Frappe's own docstring models exactly this pattern.
	``insert(ignore_if_duplicate=True)`` covers only the primary-key branch and is not enough.
	"""
	with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
		doc.insert(ignore_permissions=True)
		return str(doc.name)
	_clear_expected_duplicate_message()
	return ""


def _existing_attachment(message: str, plan: InboundAttachmentPlan) -> str | None:
	"""The ``Chat Attachment`` already recorded for this plan, if any.

	Structural dedupe first: ``gchat_attachment_name`` is unique, so when Google supplies one
	the index decides and this probe is only an optimisation that avoids a pointless insert.

	When Google supplies **no** ``name`` the probe is load-bearing, and it is a
	``SELECT``-then-``INSERT`` — which §G.8 Rule 2 forbids in general and which is tolerable
	here for a reason worth stating rather than assuming: the write is still wrapped in
	:func:`_insert_deduped`, so a lost race degrades to a duplicate *row* (two rows for one
	blob, same bytes, same permissions) and never to an error or a lost attachment. The
	unique index cannot help because there is no unique value to index. ``VERIFY:`` whether
	``Attachment.name`` is in fact populated on a ``messages.get`` response — the discovery
	document marks it ``Identifier`` rather than ``Output only``, and no published sample
	shows one, so the no-name branch may be dead code or may be the only branch.
	"""
	if plan.gchat_attachment_name:
		return frappe.db.exists("Chat Attachment", {"gchat_attachment_name": plan.gchat_attachment_name})
	filters: dict[str, Any] = {"message": message}
	if plan.data_ref:
		filters["gchat_attachment_data_ref"] = plan.data_ref
	elif plan.drive_file_id:
		filters["drive_file_id"] = plan.drive_file_id
	else:
		filters["file_name"] = plan.file_name
	return frappe.db.exists("Chat Attachment", filters)


def _no_attachments(message: str) -> dict[str, Any]:
	"""The :func:`ingest_message_attachments` result for "there was nothing to do".

	One shape on every path, including the early returns. A caller that had to check whether
	it got a dict or a ``None`` would end up with the check in the *inbound* pipeline, which
	is the module least able to say anything sensible about attachments.
	"""
	return {
		"message": message,
		"attachments": [],
		"created": 0,
		"pending": [],
		"stored": 0,
		"linked": 0,
		"skipped": 0,
		"failed": 0,
		"has_attachments": 0,
	}


def ingest_message_attachments(
	message: str, resource: Mapping[str, Any], *, enqueue_downloads: bool = True
) -> dict[str, Any]:
	"""**The inbound entry point.** Record every attachment on one fetched ``Message``.

	One call, from ``inbound._apply_created`` and ``inbound._apply_updated``, once the
	``Message`` resource is in hand. Everything the two sources need is decided here and
	nothing about Google's shapes leaks back into the inbound pipeline: it hands over a
	message name and a resource, and gets back a summary.

	**Idempotent, and structurally rather than by a guard.** ``gchat_attachment_name`` is a
	unique index and :func:`_insert_deduped` treats a collision as success (§G.8 Rule 2), so
	reprocessing a redelivered raw event produces no second row and no second ``File`` —
	which is the property the replay half of the soak asserts. Both duplicate exceptions are
	caught, inside a savepoint: ``DuplicateEntryError`` is raised **only** for a primary-key
	collision and every other unique index raises ``UniqueValidationError``
	(``PHASE2_VERIFIED.md`` §1.1).

	**It creates rows and enqueues work; it downloads nothing.** This runs inside the ingest
	transaction, and a 200 MB body has no business in one — the bytes arrive in
	:func:`download_attachment`, on the ``long`` queue, with :func:`sweep_pending_attachments`
	as the delivery guarantee behind it.

	The two-source rule, which is an ACL decision and not a storage preference:

	``UPLOADED_CONTENT``
	    ``Pending`` now, then downloaded **via the data ref** and stored as a private ``File``
	    on the message row. ``downloadUri``/``thumbnailUri`` are read by the planner and
	    discarded — they are human, browser-session URLs and a bearer token cannot use them.
	``DRIVE_FILE``
	    ``Linked`` immediately: ``drive_file_id`` and a link, **no bytes ever**. Copying them
	    detaches the file from Drive's permission model, which is the governing ACL, and
	    there is no way to un-make that later.

	Returns a summary rather than a bare list of names, because two of its fields are things
	the caller cannot cheaply recompute: ``pending`` is the set of rows whose bytes have not
	arrived yet (the soak drains exactly these), and ``created`` distinguishes a first ingest
	from a replay that found everything already there. ``attachments`` is every row touched,
	created or already present, in the order Google returned them.
	"""
	message_name = str(message or "").strip()
	if not message_name:
		return _no_attachments(message_name)

	plans = plan_inbound_attachments(resource)
	if not plans:
		return _no_attachments(message_name)

	room = frappe.db.get_value("Chat Message", message_name, "room") or ""
	if not room:
		# No room means no ACL. `Chat Attachment.room` is denormalised precisely so the byte
		# path never has to join, and a row without it would be an attachment nobody can
		# resolve a permission for.
		log = _logger()
		if log is not None:
			log.warning("chat attachment ingest skipped: message %s has no room", message_name)
		return _no_attachments(message_name)

	names: list[str] = []
	pending: list[str] = []
	created_count = 0
	states: list[str] = []
	for plan in plans:
		existing = _existing_attachment(message_name, plan)
		if existing:
			names.append(str(existing))
			state = str(frappe.db.get_value("Chat Attachment", existing, "ingest_state") or "")
			states.append(state)
			if plan.action is AttachmentAction.STORE and state == INGEST_PENDING:
				pending.append(str(existing))
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Chat Attachment",
				"message": message_name,
				"room": room,
				"source": plan.source,
				"ingest_state": plan.ingest_state,
				"skip_reason": plan.skip_reason,
				"file_name": plan.file_name,
				"content_type": plan.content_type,
				"gchat_attachment_name": plan.gchat_attachment_name,
				"gchat_attachment_data_ref": plan.data_ref,
				"drive_file_id": plan.drive_file_id,
			}
		)
		inserted = _insert_deduped(doc)
		if not inserted:
			# Somebody else won the race on the unique index. That is Rule 2's definition of
			# success, not an error: the row the writer wanted exists.
			resolved = _existing_attachment(message_name, plan)
			if resolved:
				names.append(str(resolved))
				states.append(str(frappe.db.get_value("Chat Attachment", resolved, "ingest_state") or ""))
			continue
		names.append(inserted)
		states.append(plan.ingest_state)
		created_count += 1
		if plan.action is AttachmentAction.STORE:
			pending.append(inserted)

	if names:
		# update_modified=False: the D6 digest watermark is (max(seq), count(*),
		# max(modified)) and an attachment ingest that bumped the message's `modified` would
		# invalidate every cached digest for the room. See the module docstring.
		frappe.db.set_value("Chat Message", message_name, "has_attachments", 1, update_modified=False)

	if enqueue_downloads:
		for attachment in pending:
			enqueue_download(attachment)

	return {
		"message": message_name,
		"attachments": names,
		"created": created_count,
		"pending": pending,
		"stored": states.count(INGEST_STORED),
		"linked": states.count(INGEST_LINKED),
		"skipped": states.count(INGEST_SKIPPED),
		"failed": states.count(INGEST_FAILED),
		"has_attachments": 1 if names else 0,
	}


def enqueue_download(attachment: str) -> None:
	"""Queue one ``media.download``. Never raises; the sweeper is the real guarantee.

	``enqueue_after_commit=True`` because the worker is a different process against the same
	database and would otherwise read a row its transaction has not committed —
	an intermittent "attachment not found" that reproduces only under load.

	``deduplicate=True`` with an explicit ``job_id`` (it throws without one) so a sweeper pass
	that overlaps an in-flight download does not start a second one. Note the v16 behaviour:
	deduplicate skips while an existing job is ``QUEUED`` **or ``STARTED``**, which is the
	half people miss and is exactly the half that matters here.
	"""
	name = str(attachment or "").strip()
	if not name:
		return
	try:
		frappe.enqueue(
			"erpnext_enhancements.chat.sync.attachments.download_attachment",
			queue=DOWNLOAD_QUEUE,
			timeout=DOWNLOAD_TIMEOUT_SECONDS,
			enqueue_after_commit=True,
			job_id=f"chat-attachment-download::{name}",
			deduplicate=True,
			attachment=name,
		)
	except Exception:
		# A queue that will not accept a job is not a reason to fail the ingest transaction.
		# The row stays Pending and sweep_pending_attachments picks it up — which is the same
		# path a production deploy's FLUSHDB puts it on anyway.
		log = _logger()
		if log is not None:
			log.warning("chat attachment %s could not be enqueued; left for the sweeper", name)


def space_read_limiter() -> Any:
	"""The per-space **read** bucket. A function so a test can replace it, as ``outbound`` does.

	Keyed under :data:`SPACE_READ_KEY_PREFIX`, which is *not* ``SpaceRateLimiter``'s default
	write prefix: reads and writes are separate buckets on Google's side (15/s and 1/s), and
	sharing one key would let an ingest storm spend the second the relay needs.
	"""
	return ratelimit.SpaceRateLimiter(key_prefix=SPACE_READ_KEY_PREFIX)


def project_quota() -> Any:
	"""The per-project fixed-window counters. Same seam, same reason."""
	return ratelimit.ProjectQuota()


def _space_of(room: str) -> str:
	"""``Chat Room.gchat_space_name``. The bucket Google enforces is per **space**, not room."""
	if not room:
		return ""
	try:
		return str(frappe.db.get_value("Chat Room", room, "gchat_space_name") or "")
	except Exception:
		return ""


def charge_attachment_read(space: str) -> bool:
	"""Claim one ``media.download`` against both read buckets. ``False`` ⇒ come back later.

	**A download is not free.** It spends the space's 15-reads-per-second budget and the
	per-project attachment-read window, and neither of those is the bucket the relay's writes
	come out of. Charging it to the write bucket — the obvious shortcut, since
	``SpaceRateLimiter`` defaults to that key — would make one busy ingest starve the room's
	outbound FIFO at one write per second, which is the limit the entire outbound design is
	shaped around.

	**Fails open on a Redis fault**, deliberately, and the same way ``reconcile._charge_read``
	does: Google enforces its own limits and answers 429, which the transport's backoff
	already handles, whereas a cache outage that silently stopped every attachment download
	would turn a Redis blip into a permanent hole in the message record. The bucket is an
	optimisation; backoff is the correctness mechanism (``PHASE2_VERIFIED.md`` §2).

	A refusal is **not** an error and is never logged as one. The caller leaves the row
	``Pending`` — untouched, so its ``modified`` retry clock does not move — and
	:func:`sweep_pending_attachments` picks it up.
	"""
	target = str(space or "").strip()
	if target:
		try:
			decision = space_read_limiter().acquire(target, cost_ms=SPACE_READ_COST_MS, block=True)
			if not decision.allowed:
				return False
		except Exception:
			log = _logger()
			if log is not None:
				log.debug("chat attachment: per-space read bucket unavailable; proceeding")

	try:
		limit = _setting("project_attachment_reads_per_minute", DEFAULT_PROJECT_ATTACHMENT_READS_PER_MINUTE)
		return bool(project_quota().charge(BUCKET_ATTACHMENT_READS, limit=limit))
	except Exception:
		log = _logger()
		if log is not None:
			log.debug("chat attachment: project read bucket unavailable; proceeding")
		return True


def fetch_attachment_bytes(client: Any, data_ref: str) -> tuple[bytes, str]:
	"""``media.download`` → ``(bytes, served content type)``. Chaos test 14, first half.

	Raises :class:`AttachmentDownloadFailed` — never a ``GoogleChatError`` — with
	``from None`` on every path. A bare re-raise out of a background job publishes the failing
	frames' locals into the Error Log, and the frames below hold an ``Authorization: Bearer``
	header; this app has leaked private key material exactly that way once. The message is run
	through ``scrub_secrets`` on top of that, because it lands in
	``Chat Attachment.skip_reason``, which is readable by anyone with the DocType.

	A dry-run client answers with zero bytes and a ``dryRun`` marker. That is handled by the
	caller rather than here, because "no bytes" is a legitimate answer in dry-run and a fault
	everywhere else, and only the caller knows which it is looking at.
	"""
	try:
		payload = client.download_media(data_ref)
	except GoogleChatError as exc:
		raise AttachmentDownloadFailed(scrub_secrets(str(exc))[:500]) from None
	except Exception as exc:  # pragma: no cover - transport doubles raise their own types
		raise AttachmentDownloadFailed(f"{type(exc).__name__}: {scrub_secrets(str(exc))[:400]}") from None
	if payload.get("dryRun"):
		raise AttachmentDownloadFailed("dry-run client: no bytes were fetched")
	return attachment_bytes(payload), attachment_content_type(payload)


def _download_client(subject: str | None = None) -> GoogleChatClient:
	"""The client an ingest downloads with — **app identity by default**.

	``media.download``'s scope list includes ``chat.bot``, so no impersonation is needed, and
	needing none is the point: the sender of an inbound attachment is frequently somebody we
	hold no DWD mandate for, and an ingest that had to impersonate them would fail on exactly
	the messages we most need to keep.
	"""
	if subject:
		return GoogleChatClient(subject=subject, identity=AuthIdentity.USER)
	return GoogleChatClient(identity=AuthIdentity.APP)


def download_attachment(attachment: str, *, client: Any | None = None) -> str:
	"""Fetch and store one ``UPLOADED_CONTENT`` attachment. The background job entry point.

	Idempotent by state: a row already ``Stored``, ``Linked`` or ``Skipped`` returns
	immediately, so a replayed event, a sweeper pass and a manual retry cannot produce three
	copies of one file.

	Never re-raises. There are no RQ retries in v16 and the deploy ``FLUSHDB``s the queue, so
	raising would buy an Error Log row (with frame locals) and nothing else; the row is marked
	``Failed`` and :func:`sweep_pending_attachments` is what tries again. Returns the resulting
	``ingest_state`` so a caller — the soak script, a test — can assert on it without a query.

	**Charges both read buckets before the call**, per :func:`charge_attachment_read`. A
	refusal returns ``Pending`` and leaves the row alone: no state write, no ``modified``
	bump, so the sweeper re-picks it on its next pass. That is the one exit from this
	function that is neither a success nor a fault.
	"""
	name = str(attachment or "").strip()
	if not name:
		return ""
	try:
		row = frappe.db.get_value(
			"Chat Attachment",
			name,
			["message", "room", "source", "ingest_state", "gchat_attachment_data_ref", "file_name"],
			as_dict=True,
		)
	except Exception:
		return ""
	if not row:
		return ""

	state = str(row.get("ingest_state") or "")
	if state in (INGEST_STORED, INGEST_LINKED, INGEST_SKIPPED):
		return state
	if str(row.get("source") or "") != SOURCE_UPLOADED:
		# A Drive Link never downloads. Recorded rather than silently returned, because a
		# Drive row reaching this function means somebody enqueued the wrong thing.
		return _finish(name, INGEST_LINKED, "a Drive Link is never copied; the bytes stay in Drive")

	data_ref = str(row.get("gchat_attachment_data_ref") or "")
	if not data_ref:
		return _finish(name, INGEST_SKIPPED, "no attachmentDataRef.resourceName to download with")

	if not charge_attachment_read(_space_of(str(row.get("room") or ""))):
		# Out of read budget. Not a fault and not a policy decision, so neither `Failed` nor
		# `Skipped`: the row stays exactly as it is — including its `modified`, which is this
		# table's retry clock — and the sweeper comes back for it.
		return INGEST_PENDING

	try:
		data, served_type = fetch_attachment_bytes(client or _download_client(), data_ref)
	except AttachmentDownloadFailed as exc:
		return _finish(name, INGEST_FAILED, str(exc))

	limits = resolve_limits()
	verdict = check_inbound_size(len(data), limits)
	if not verdict.accepted:
		return _finish(name, INGEST_SKIPPED, verdict.reason)

	try:
		return _store_bytes(
			attachment=name,
			message=str(row.get("message") or ""),
			file_name=safe_attachment_file_name(row.get("file_name") or ""),
			data=data,
			served_type=served_type,
		)
	except Exception as exc:
		# Broad on purpose. `File` throws for a size ceiling, a blocked extension and a full
		# disk, and all three are the same outcome here: no bytes, a reason on the row, and a
		# sweeper that will try again. `scrub_secrets` because the string is stored.
		return _finish(name, INGEST_FAILED, f"{type(exc).__name__}: {scrub_secrets(str(exc))[:400]}")


def _store_bytes(*, attachment: str, message: str, file_name: str, data: bytes, served_type: str) -> str:
	"""Write the private ``File`` and stamp the row. The only place chat bytes hit our disk.

	``is_private = 1`` and ``attached_to_doctype``/``attached_to_name`` are not optional and
	are not configurable — they are the entire attachment security model (module docstring).
	``ignore_permissions`` because this runs in a background worker with no session user, and
	the permission that matters was decided when the message was ingested.

	``byte_size_verified`` is set when the ``File`` row's own ``file_size`` matches the number
	of bytes we handed it. That is a **narrower** claim than the field's description suggests
	and the narrowness is deliberate: Google's ``Attachment`` resource carries no size at all,
	so there is no server-reported figure to compare against, and ``client.download_media``
	does not surface ``Content-Length``. What the check *does* catch is the case it exists
	for — a short write, and a ``File`` dedupe that reused a different blob under an MD5
	collision. ``VERIFY:`` plumbing ``Content-Length`` through the transport would make this a
	real end-to-end integrity check; it is a ``gchat/client.py`` change and is reported rather
	than faked here.
	"""
	digest = content_hash(data)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name or FALLBACK_FILE_NAME,
			"attached_to_doctype": "Chat Message",
			"attached_to_name": message,
			"is_private": 1,
			"content": data,
		}
	)
	file_doc.insert(ignore_permissions=True)

	stored_size = int(getattr(file_doc, "file_size", None) or 0)
	frappe.db.set_value(
		"Chat Attachment",
		attachment,
		{
			"file": file_doc.name,
			"file_size": len(data),
			"content_hash": digest,
			"content_type": served_type or DEFAULT_ATTACHMENT_CONTENT_TYPE,
			"byte_size_verified": 1 if stored_size == len(data) else 0,
			"ingest_state": INGEST_STORED,
			"skip_reason": "",
		},
	)
	return INGEST_STORED


def _finish(attachment: str, state: str, reason: str) -> str:
	"""Stamp a terminal-ish ingest state and its reason. ``modified`` moves on purpose.

	This table's ``modified`` **is** the retry clock — there is no ``attempts`` column — so
	unlike every write this module makes to ``Chat Message``, these do not pass
	``update_modified=False``.
	"""
	try:
		frappe.db.set_value(
			"Chat Attachment", attachment, {"ingest_state": state, "skip_reason": (reason or "")[:500]}
		)
	except Exception:
		pass
	return state


def sweep_pending_attachments(limit: int | None = None) -> dict[str, int]:
	"""Re-drive attachments whose ingest never finished. **The delivery guarantee.**

	Scheduler entry point. It exists because the queue cannot be trusted with durable work:
	Frappe v16 wires no RQ retries at all, and the production deploy runs
	``redis-cli -p 11000 FLUSHDB`` and restarts the whole honcho group, so every queued
	download vanishes at every release. A ``Chat Attachment`` row does not.

	Only rows quiet for :data:`RETRY_COOLDOWN_SECONDS` are re-picked, which is what stops a
	permanently failing attachment — a revoked space, a deleted blob — from being retried once
	a minute forever. See the module docstring on ``modified`` as the retry clock.
	"""
	from frappe.utils import add_to_date, now_datetime

	batch = int(limit or _setting("sweeper_batch_size", DEFAULT_SWEEP_BATCH))
	# now_datetime() is SITE-LOCAL, and so is the `modified` column it is compared against —
	# which is why this one is safe while comparing the same value to a Google RFC-3339 stamp
	# would be wrong by the site offset. This app has already shipped one bug from that
	# confusion; the comment is here so the next reader does not have to re-derive it.
	cutoff = add_to_date(now_datetime(), seconds=-RETRY_COOLDOWN_SECONDS)
	rows = frappe.get_all(
		"Chat Attachment",
		filters={
			"source": SOURCE_UPLOADED,
			"ingest_state": ["in", sorted(INGEST_UNSETTLED)],
			"modified": ["<", cutoff],
		},
		pluck="name",
		order_by="modified asc",
		limit=batch,
	)
	for name in rows:
		enqueue_download(name)
	return {"considered": len(rows), "batch": batch}


# --------------------------------------------------------------------------------------
# Outbound (Frappe)
# --------------------------------------------------------------------------------------


def collect_outbound_attachments(message: str) -> list[OutboundAttachment]:
	"""The private ``File`` rows hanging off one ``Chat Message``.

	``frappe.get_all`` — i.e. ``get_list(ignore_permissions=True)`` — is correct **here** and
	would not be in an endpoint: this runs in a relay worker with no session user, and the
	scope is a single message the job was handed. Every chat *content* read that answers a
	user applies ``permissions.membership_filter_sql`` by hand; see :func:`download`.
	"""
	name = str(message or "").strip()
	if not name:
		return []
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Chat Message", "attached_to_name": name},
		fields=["name", "file_name", "file_size", "is_private"],
		order_by="creation asc",
	)
	return [
		OutboundAttachment(
			file=str(row.get("name") or ""),
			file_name=safe_attachment_file_name(row.get("file_name") or ""),
			content_type=DEFAULT_ATTACHMENT_CONTENT_TYPE,
			byte_size=int(row.get("file_size") or 0),
			is_private=bool(row.get("is_private")),
		)
		for row in rows
	]


def plan_outbound_attachments(
	candidates: Sequence[OutboundAttachment], limits: AttachmentLimits
) -> OutboundPlan:
	"""Split the files on a message into "upload" and "relay without". Pure.

	Nothing here refuses the *message*. A file over Chat's ceiling is dropped from the upload
	set with a reason and the text still goes — see the module docstring, and
	:func:`skipped_attachment_notice` for what the recipient is told.

	Two skip reasons, and the second is a security rule rather than a limit. A **public**
	local ``File`` is never relayed, because a public chat attachment cannot exist safely
	(the web server serves it with no permission check at all) and relaying one would make
	the accident permanent in a second system. ``chat_attachment.ChatAttachment.validate``
	refuses to *record* one as well; this is the half that keeps the relay from ever reaching
	that throw, so an ACL mistake costs one un-relayed file rather than a stuck message.
	"""
	upload: list[OutboundAttachment] = []
	skipped: list[tuple[OutboundAttachment, str]] = []
	for candidate in candidates or ():
		if not candidate.is_private:
			skipped.append(
				(
					candidate,
					"the local File is public. A public file is served off disk with no permission "
					"check, so it is not a chat attachment and is not relayed; re-upload it private.",
				)
			)
			continue
		verdict = check_outbound_size(candidate.byte_size, limits)
		if verdict.accepted:
			upload.append(candidate)
		else:
			skipped.append((candidate, verdict.reason))
	return OutboundPlan(upload=tuple(upload), skipped=tuple(skipped))


def upload_outbound_attachments(
	client: Any, *, space: str, message: str, plan: OutboundPlan | None = None
) -> OutboundUploadResult:
	"""Upload a message's files and return the tokens ``messages.create`` must reference.

	**Asserts user authentication rather than assuming it.** ``media.upload``'s scope list is
	exactly ``chat.import``, ``chat.messages`` and ``chat.messages.create`` — ``chat.bot``, the
	app-authentication scope, is *absent* (``PHASE2_VERIFIED.md`` §4), so an app-auth upload
	cannot succeed at all and the relay must impersonate the sending author. The check is
	positive (``identity is AuthIdentity.USER``, and a ``subject`` to be that user *as*) rather
	than a negative "not APP", because a client whose identity is unset would otherwise sail
	through and surface a 403 four steps later that reads like a DWD misconfiguration. The
	relay already holds exactly the right client: the one it created for ``messages.create``.

	**Not idempotent, and cannot be made so.** ``media.upload`` takes no ``requestId``, so a
	retried relay job uploads the bytes again and receives a fresh token. That is survivable
	because ``messageId`` — which *is* durable and server-enforced — stops the second
	``messages.create`` from producing a second message; the orphaned upload token is simply
	never referenced. Reusing a stored token instead would be worse: the reference documents
	no lifetime for one, so a "cheap" reuse is an undated guess.

	**One file's failure is that file's failure.** A ``GoogleChatError`` on the third of four
	uploads is caught, scrubbed, and recorded against that file; the other three tokens are
	still returned and the message still relays. Letting it out would abort the whole job —
	the relay would treat it as a message failure, retry the *entire* upload set, and
	eventually dead-letter a message whose text was never the problem. Losing the mirror of a
	file must never lose the message.

	The bytes are read here and never logged. ``client.build_log_record`` reports
	``raw_bytes`` as a length and ``GoogleCall.raw_body`` is unreachable from every logging
	path in the transport by construction — that is why it is a separate field from ``body``.
	"""
	resolved = (
		plan
		if plan is not None
		else plan_outbound_attachments(collect_outbound_attachments(message), resolve_limits())
	)

	# --- the identity checks, and they run AFTER the plan on purpose -----------------------
	#
	# **A message with no files has no upload requirement.** This function used to assert the
	# identity before looking at the plan, so a Triton reply — app-identity, and text-only by
	# construction — was refused for the auth of an upload it was never going to make. Six of
	# them dead-lettered on production the hour app-identity relaying shipped, with an error
	# about `media.upload` on messages that carried nothing to upload.
	#
	# The check itself was right and stays exactly as strict for the case it was written for.
	# What was wrong was its position: an assertion about *how to upload* placed where it also
	# governed *whether there was anything to upload*.
	if not resolved.upload:
		return OutboundUploadResult()

	identity = getattr(client, "identity", None)
	if identity is AuthIdentity.APP:
		# Not raised, and the distinction from the check below is the whole point. **APP is a
		# declared mode**: Triton's replies are relayed that way on purpose, so "this client
		# cannot upload" is an expected fact about a working system, and the right response is
		# the one this module already has for a file that will not go — record it and relay the
		# text. Raising dead-lettered the message, and the words were never the problem.
		reason = (
			"media.upload does not accept app authentication — chat.bot is not in its scope "
			"list, so no app-auth token can create an attachment. This message was relayed as "
			"the Chat app, which cannot carry files; the text was mirrored and the files were "
			"not."
		)
		return OutboundUploadResult(failed=tuple((item, reason) for item in resolved.upload))

	if identity is not AuthIdentity.USER:
		# Still positive, still raising, and for the reason it always was: an identity that is
		# neither USER nor APP was never *set*, which is a programming error rather than a mode.
		# Degrading it the way APP degrades would hide the bug behind a plausible notice, and
		# the 403 it eventually produces reads like a DWD misconfiguration.
		raise AttachmentError(
			"media.upload requires the USER identity and this client declares none. A client "
			"whose identity was never set mints an app token in practice and fails the same way "
			"an app-auth upload does, four steps further from the cause."
		)

	if not str(getattr(client, "subject", "") or "").strip():
		raise AttachmentError(
			"media.upload must be made as a real Workspace user. The client carries the USER "
			"identity but no subject to impersonate, which mints an app token in practice and "
			"fails the same way an app-auth upload does."
		)
	uploads: list[OutboundUpload] = []
	failed: list[tuple[OutboundAttachment, str]] = []
	for candidate in resolved.upload:
		data = _file_bytes(candidate.file)
		if not data:
			failed.append((candidate, "the local File produced no bytes; nothing was uploaded"))
			continue
		try:
			response = client.upload_attachment(
				space, candidate.file_name, content=data, content_type=candidate.content_type
			)
		except GoogleChatError as exc:
			# `scrub_secrets` because this string lands in `Chat Attachment.skip_reason`, which
			# is readable by anyone holding the DocType, and the frames underneath this call hold
			# an `Authorization: Bearer` header. Not re-raised at all, so there is nothing to
			# `raise … from None` — the failure is data here, not control flow.
			failed.append((candidate, scrub_secrets(str(exc))[:500]))
			continue
		except Exception as exc:  # pragma: no cover - transport doubles raise their own types
			failed.append((candidate, f"{type(exc).__name__}: {scrub_secrets(str(exc))[:400]}"))
			continue
		token = upload_token_of(response)
		if not token:
			# Google answered 200 without the one field that binds an upload to a message. There
			# is nothing to reference, so this file is as un-relayed as a failed one and must say
			# so rather than vanishing out of a `continue`.
			failed.append((candidate, "the upload response carried no attachmentUploadToken"))
			continue
		uploads.append(
			OutboundUpload(
				file=candidate.file,
				file_name=candidate.file_name,
				upload_token=token,
				resource_name=upload_resource_name_of(response),
				content_hash=content_hash(data),
				byte_size=len(data),
			)
		)
	return OutboundUploadResult(uploads=tuple(uploads), failed=tuple(failed))


def _file_bytes(file_name: str) -> bytes:
	"""The bytes behind one ``File`` row, or ``b""``. Never raises, never logs the content."""
	try:
		doc = frappe.get_doc("File", file_name)
		content = doc.get_content()
	except Exception:
		log = _logger()
		if log is not None:
			log.warning("chat attachment could not read File %s", file_name)
		return b""
	if isinstance(content, str):
		return content.encode("utf-8", "surrogateescape")
	return bytes(content or b"")


def record_outbound_attachments(
	*,
	message: str,
	room: str,
	uploads: Iterable[OutboundUpload],
	skipped: Iterable[tuple[OutboundAttachment, str]] = (),
	failed: Iterable[tuple[OutboundAttachment, str]] = (),
) -> list[str]:
	"""Write the ``Chat Attachment`` rows for an outbound relay. Idempotent per ``File``.

	``source = "ERPNext"`` marks a file that originated on our side, which is what lets the
	inbound pipeline recognise the same blob coming back as an echo rather than ingesting a
	second copy of our own attachment.

	Three channels, three ``ingest_state``s, and the distinction is the whole reason an
	operator can act on these rows:

	``uploads`` → ``Stored``
	    the bytes are in Chat and the message references them.
	``skipped`` → ``Skipped``
	    a **policy** decision — over Chat's ceiling, or a public local ``File`` that must never
	    be relayed. A retry would decide the same thing, so nothing re-drives these.
	``failed`` → ``Failed``
	    a **fault** — a 403, a 429 that outlived the client's budget, a timeout. The message
	    relayed without the file; the row records why, and ``Failed`` is in
	    :data:`INGEST_UNSETTLED` so it is visibly unfinished rather than quietly gone.

	Note what ``Failed`` does *not* do here: it does not enqueue a re-upload. The message has
	already been created in Chat and ``messages.patch`` may only set ``text`` under user
	authentication (``PHASE2_VERIFIED.md`` §4), so there is no API call that would attach the
	file to a message that already exists. Re-relaying means a new message, which is a decision
	for a human — hence a row that says so rather than a retry that cannot work.
	"""
	names: list[str] = []
	for upload in uploads or ():
		existing = frappe.db.exists("Chat Attachment", {"message": message, "file": upload.file})
		values = {
			"content_hash": upload.content_hash,
			"file_size": upload.byte_size,
			"byte_size_verified": 1,
			"gchat_attachment_data_ref": upload.resource_name,
			"ingest_state": INGEST_STORED,
			"skip_reason": "",
		}
		if existing:
			frappe.db.set_value("Chat Attachment", existing, values)
			names.append(str(existing))
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Chat Attachment",
				"message": message,
				"room": room,
				"source": SOURCE_ERPNEXT,
				"file": upload.file,
				"file_name": upload.file_name,
				"content_type": DEFAULT_ATTACHMENT_CONTENT_TYPE,
				**values,
			}
		)
		created = _insert_deduped(doc)
		if created:
			names.append(created)

	for state, entries in ((INGEST_SKIPPED, skipped), (INGEST_FAILED, failed)):
		for candidate, reason in entries or ():
			existing = frappe.db.exists("Chat Attachment", {"message": message, "file": candidate.file})
			if existing:
				frappe.db.set_value(
					"Chat Attachment", existing, {"ingest_state": state, "skip_reason": reason[:500]}
				)
				names.append(str(existing))
				continue
			doc = frappe.get_doc(
				{
					"doctype": "Chat Attachment",
					"message": message,
					"room": room,
					"source": SOURCE_ERPNEXT,
					# A public File is deliberately NOT linked. The row still records that the
					# message carried a file Chat did not get — which is the operator-visible
					# fact — but linking it would trip the controller's is_private invariant and
					# turn a skipped attachment into a failed relay job.
					"file": candidate.file if candidate.is_private else "",
					"file_name": candidate.file_name,
					"file_size": candidate.byte_size,
					"ingest_state": state,
					"skip_reason": reason[:500],
				}
			)
			created = _insert_deduped(doc)
			if created:
				names.append(created)
	return names


# --------------------------------------------------------------------------------------
# The byte path (Frappe) — the only way a human reads a chat attachment
# --------------------------------------------------------------------------------------


@frappe.whitelist()
def download(attachment: str) -> None:
	"""Serve one chat attachment's bytes to a room member. **The** byte path.

	It exists because the obvious path does not work and must not be made to. ``Chat Message``
	ships zero DocPerm, so ``File.has_permission`` — which delegates to the attached
	document — denies ``/private/files/…`` to *everyone* but Administrator, members included.
	The alternative to this endpoint is a DocPerm on ``Chat Message``, which would open the
	desk's list and report views onto every message body in the company.

	The membership decision is ``permissions.chat_attachment_has_permission``: the same
	function the ``has_permission`` hook uses, called directly rather than through
	``frappe.has_permission`` (which would refuse first, on the absent DocPerm) and never
	re-implemented here. One rule, one expression of it, and the oversight-role audit hook
	fires inside it.

	Guest is refused explicitly and first. ``frappe.utils.response.download_private_file``
	does the same, and the reason is worth copying: an unauthenticated request is not a
	permission question, and letting it reach a membership probe is how a ``Guest`` row in
	``Chat Room Member`` would one day become a public file server.

	Raises ``frappe.PermissionError`` — a 403 — for every refusal, and the same 403 whether
	the attachment does not exist, is not readable, or has no bytes. Distinguishing those
	would answer "does room R contain an attachment called X" for somebody not in room R.
	"""
	from erpnext_enhancements.chat import permissions
	from erpnext_enhancements.chat.api._common import require_session

	# The pilot gate and the master switch, **before** the membership decision. Added in
	# v1.294.0 because this was the one content endpoint enforcing membership and not them,
	# and §4.J's requirement is worded about exactly this shape: *"a non-pilot user holding a
	# deep link must be refused by the server"*. An attachment URL is a deep link.
	#
	# Two consequences it had, and the second is the one that matters. A member dropped from
	# the pilot whitelist kept fetching bytes from rooms they were still in — recoverable, and
	# arguably minor. But `Chat Settings.enabled` is the top layer of the rollback table, and
	# with it off this path kept serving attachment bytes: a rollback layer with less blast
	# radius than the table would have claimed for it, which is the kind of gap you discover
	# during the incident you are rolling back from.
	#
	# `ChatAccessError` subclasses `frappe.PermissionError`, so this refuses with the same 403
	# as everything else here and the uniform-refusal property below is preserved.
	require_session()

	name = str(attachment or "").strip()
	user = frappe.session.user
	if not name or user in ("", "Guest"):
		raise frappe.PermissionError(frappe._("Not permitted"))

	doc = frappe.db.get_value(
		"Chat Attachment", name, ["name", "message", "room", "file", "file_name", "source"], as_dict=True
	)
	if not doc or not permissions.chat_attachment_has_permission(doc, "read", user):
		raise frappe.PermissionError(frappe._("Not permitted"))

	if str(doc.get("source") or "") == SOURCE_DRIVE_LINK or not doc.get("file"):
		# A Drive Link has no bytes here by design, and asking for them must not read as a
		# permission failure to the SPA — but it must not read as "no such attachment"
		# either. The row is visible through the ordinary read path; only the bytes are not.
		raise frappe.PermissionError(
			frappe._("This attachment is a Google Drive link and has no stored copy")
		)

	data = _file_bytes(str(doc.get("file")))
	if not data:
		raise frappe.PermissionError(frappe._("Not permitted"))

	frappe.local.response.filename = safe_attachment_file_name(doc.get("file_name") or "")
	frappe.local.response.filecontent = data
	frappe.local.response.type = "download"
