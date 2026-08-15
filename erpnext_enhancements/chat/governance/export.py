# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The export bundle. Phase 6 §4.C — the artefact that leaves the building.

Everything else in this phase is read by people inside the company through surfaces that
enforce their own rules. **This produces a file that is emailed to a lawyer.** Its contents
are a legal question wearing a serialization costume, which is why the interesting code here
is not the ZIP writer but the four decisions above it.

--------------------------------------------------------------------------------------
G6-9: the deleted body appears in ``revisions.jsonl`` and NOT in ``messages.jsonl``
--------------------------------------------------------------------------------------

This is a **decision**, not a filter, and the reason is divergence D4: on this schema an
``is_deleted = 1`` row **retains its ``text``** (ADR §F.6.5, argued in five places). So a
naive dump of the message table hands over every deleted message body in the file people
read first — the one that looks like "the conversation".

So :func:`message_record` drops the body of a deleted row and leaves a tombstone in its
place. The body is not lost: it is in ``revisions.jsonl``, where a reader has to go
deliberately and where it sits beside who deleted it and when. **Same bytes, different
sentence.** Decision D-9 lets an export include deleted bodies inline, ships it off, and
makes turning it on its own audited act.

--------------------------------------------------------------------------------------
A re-export is byte-identical apart from the timestamp and the export id
--------------------------------------------------------------------------------------

Also G6-9, and it is what makes the manifest worth anything: if two exports of the same
range differ, the difference has to mean something. Everything here is therefore
deterministic — records sorted by ``seq``, JSON with sorted keys and no whitespace, no
``now()`` inside any content file. The only varying values live in ``manifest.json``, which
is the file you would expect to differ.

That property is what lets an investigator answer "was this tampered with between the export
and the hearing" with a hash comparison rather than an argument.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Iterable
from typing import Any

#: The files a bundle always contains. Named here rather than inline so the manifest, the
#: README and the test all agree about what "complete" means.
BUNDLE_FILES = (
	"manifest.json",
	"README.txt",
	"messages.jsonl",
	"revisions.jsonl",
	"transcript.html",
)

#: What replaces a deleted message's body in ``messages.jsonl``. A marker rather than an
#: empty string, because empty reads as "they sent a blank message".
TOMBSTONE = "[deleted — the original text is in revisions.jsonl]"

#: Message fields carried into the export, in the order a reader wants them. An allowlist
#: rather than "everything the row has": a column added next year is not automatically part
#: of a legal disclosure, and the failure of the opposite is silent.
MESSAGE_FIELDS = (
	"name",
	"room",
	"seq",
	"sender",
	"sender_email",
	"sender_kind",
	"message_type",
	"text",
	"thread_root",
	"parent_message",
	"is_edited",
	"edited_at",
	"is_deleted",
	"deleted_at",
	"deleted_by",
	"deletion_source",
	"has_attachments",
	"sync_origin",
	"gchat_create_time",
	"creation",
)

#: Revision fields. `text_before` is the deleted body, and it is why this file exists.
REVISION_FIELDS = (
	"name",
	"message",
	"room",
	"revision_no",
	"change_type",
	"origin",
	"origin_timestamp",
	"actor",
	"actor_email",
	"text_before",
	"text_after",
	"note",
)


def _clean(value: Any) -> Any:
	"""Values a JSON reader can rely on. Dates become strings; nothing becomes ``NaN``."""
	if value is None or isinstance(value, str | int | float | bool):
		return value
	return str(value)


def message_record(row: dict[str, Any], *, include_deleted_content: bool = False) -> dict[str, Any]:
	"""One line of ``messages.jsonl``.

	**The deleted body is dropped here unless the export was deliberately asked for it.**
	`is_deleted = 1` rows keep their `text` in this schema, so without this the file a
	reader opens first would carry every deleted message in the range — which is the
	opposite of what deleting one meant.

	``deleted_content_included`` is stamped on every record rather than only on the ones it
	affected, so a reader can tell an export that withheld nothing from one that had nothing
	to withhold. Those are different facts and they look identical without it.
	"""
	record = {key: _clean(row.get(key)) for key in MESSAGE_FIELDS}
	deleted = bool(row.get("is_deleted"))
	record["deleted_content_included"] = bool(include_deleted_content)
	if deleted and not include_deleted_content:
		record["text"] = TOMBSTONE
	return record


def revision_record(row: dict[str, Any]) -> dict[str, Any]:
	"""One line of ``revisions.jsonl``. Carries the bodies, always.

	Not gated on ``include_deleted_content``: this file *is* the deliberate place. Gating it
	too would mean an export that records that a message was deleted and can never say what
	it said, which is not a redaction — it is a gap.
	"""
	return {key: _clean(row.get(key)) for key in REVISION_FIELDS}


def canonical_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
	"""Deterministic JSONL: sorted keys, no incidental whitespace, ``\\n`` endings.

	``sort_keys`` and the explicit separators are what make a re-export byte-identical —
	Python's dict order is insertion order, so two runs that build a record by different
	paths would otherwise produce different bytes for the same data and every hash would
	differ for no reason a reader could act on.

	``ensure_ascii=False`` so a name with an accent in it survives as itself. UTF-8 is
	stated in the README rather than assumed.
	"""
	buf = io.StringIO()
	for record in records:
		buf.write(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
		buf.write("\n")
	return buf.getvalue().encode("utf-8")


def sha256_hex(payload: bytes) -> str:
	return hashlib.sha256(payload).hexdigest()


def build_manifest(
	files: dict[str, bytes],
	*,
	app_version: str,
	git_commit: str,
	export_id: str,
	generated_at: str,
	rooms: list[str],
	include_deleted_content: bool,
	message_count: int,
	revision_count: int,
) -> dict[str, Any]:
	"""``manifest.json`` — the sha256 of every other file, plus what produced them.

	**The app version and commit are in here because a hash proves the bytes and not the
	rules.** Two exports of the same range can legitimately differ if the redaction rule
	changed between them, and without the version the only visible fact is that the hashes
	disagree — which looks exactly like tampering. Naming the code that produced a bundle is
	what turns that into an answerable question.

	``manifest.json`` is deliberately not in its own ``files`` map: a file cannot carry its
	own hash, and pretending otherwise is how a manifest ends up verifying nothing.
	"""
	return {
		"export_id": export_id,
		"generated_at": generated_at,
		"app_version": app_version,
		"git_commit": git_commit,
		"rooms": sorted(rooms),
		"include_deleted_content": bool(include_deleted_content),
		"message_count": int(message_count),
		"revision_count": int(revision_count),
		"files": {name: sha256_hex(payload) for name, payload in sorted(files.items())},
		"hash_algorithm": "sha256",
	}


def verify_bundle(files: dict[str, bytes], manifest: dict[str, Any]) -> list[str]:
	"""Names of files whose bytes do not match the manifest. Empty means intact.

	Reports **missing, extra and altered** rather than only altered. A bundle that quietly
	lost a file would otherwise verify clean, and the file most worth removing is the one
	that contradicts the story being told.
	"""
	claimed = dict(manifest.get("files") or {})
	problems: list[str] = []
	for name, digest in sorted(claimed.items()):
		if name not in files:
			problems.append(f"{name}: missing from the bundle")
		elif sha256_hex(files[name]) != digest:
			problems.append(f"{name}: sha256 does not match the manifest")
	for name in sorted(set(files) - set(claimed)):
		problems.append(f"{name}: present in the bundle but absent from the manifest")
	return problems


def readme_text(
	*,
	export_id: str,
	rooms: list[str],
	include_deleted_content: bool,
	app_version: str,
) -> str:
	"""``README.txt`` — written for a lawyer or an HR investigator, not for an engineer.

	It exists because the most likely misreading of this bundle is that ``messages.jsonl``
	is "the conversation". It is the conversation **as it stands**, and the difference
	between that and what was said is the whole of ``revisions.jsonl``.
	"""
	deleted_note = (
		"This export INCLUDES the original text of deleted messages, inline, in\n"
		"messages.jsonl. That was requested deliberately and the request is recorded\n"
		"in the audit log."
		if include_deleted_content
		else (
			"Deleted messages appear in messages.jsonl as a tombstone, without their\n"
			"original text. That text is NOT missing from this export: it is in\n"
			"revisions.jsonl, beside who deleted it and when."
		)
	)
	return f"""Chat export {export_id}
{"=" * (len("Chat export ") + len(export_id))}

WHAT THIS IS

A copy of the chat records this company holds for the rooms listed below, taken
from the system of record. Produced by erpnext_enhancements {app_version}.

Rooms: {", ".join(sorted(rooms)) or "(none)"}

WHAT EACH FILE CONTAINS

  manifest.json     A sha256 checksum of every other file in this bundle, plus
                    the software version that produced them. Verify these before
                    relying on anything here.

  messages.jsonl    One message per line, oldest first, as the conversation
                    stands TODAY. Edited messages show their current text.

  revisions.jsonl   One line per edit or deletion: what the text was before, what
                    it became, who changed it and when. If you want to know what
                    was originally said, this is the file.

  transcript.html   The same messages, readable in a browser. It is a convenience
                    and it is NOT the record — messages.jsonl is.

  attachments/      Files sent in these rooms, named by their message.

THE THING MOST OFTEN GOT WRONG

messages.jsonl is the conversation AS IT STANDS, not as it happened. A message
edited three times appears once, with its final text. The history is in
revisions.jsonl. Reading only the first file will give you a confident and
incomplete account.

{deleted_note}

ENCODING AND ORDER

All files are UTF-8. Messages are ordered by their sequence number, which is
assigned once and never reused, so the order here is the order they were sent —
not the order of any timestamp, which can differ between systems.

VERIFYING THIS BUNDLE

  sha256sum messages.jsonl revisions.jsonl transcript.html

Compare each against the value in manifest.json. Any difference means the file
has changed since it was exported.
"""


def transcript_html(records: list[dict[str, Any]], *, export_id: str) -> str:
	"""A readable rendering. Explicitly *not* the record — the README says so too.

	Every value is escaped. This file is opened in a browser by somebody who has been handed
	it, and the text in it was typed by the people under investigation; an export that
	executes their script when a lawyer opens it would be a remarkable way to lose a case.
	"""
	from html import escape

	rows = []
	for record in records:
		deleted = ' class="deleted"' if record.get("is_deleted") else ""
		rows.append(
			"<tr{cls}><td>{seq}</td><td>{sender}</td><td>{text}</td><td>{when}</td></tr>".format(
				cls=deleted,
				seq=escape(str(record.get("seq") or "")),
				sender=escape(str(record.get("sender_email") or record.get("sender") or "")),
				text=escape(str(record.get("text") or "")).replace("\n", "<br>"),
				when=escape(str(record.get("gchat_create_time") or record.get("creation") or "")),
			)
		)
	return (
		'<!doctype html>\n<html><head><meta charset="utf-8">'
		f"<title>Chat transcript {escape(export_id)}</title>"
		"<style>body{font-family:system-ui,sans-serif;margin:2rem}"
		"table{border-collapse:collapse;width:100%}"
		"td{border-bottom:1px solid #ddd;padding:.4rem;vertical-align:top}"
		".deleted td{color:#888;font-style:italic}</style></head><body>"
		f"<h1>Chat transcript {escape(export_id)}</h1>"
		"<p>A convenience rendering. <strong>messages.jsonl is the record.</strong></p>"
		"<table>" + "".join(rows) + "</table></body></html>\n"
	)


def build_zip(files: dict[str, bytes]) -> bytes:
	"""A deterministic ZIP: fixed timestamps, sorted entries, stored order.

	ZIP stores an mtime per entry, so a bundle built twice would differ in bytes even when
	every file inside it is identical — which would defeat the byte-identical property the
	manifest depends on. The epoch here is arbitrary and constant; the real time lives in
	``manifest.json``, where it can be read.
	"""
	buf = io.BytesIO()
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
		for name in sorted(files):
			info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
			info.compress_type = zipfile.ZIP_DEFLATED
			info.external_attr = 0o644 << 16
			archive.writestr(info, files[name])
	return buf.getvalue()


__all__ = [
	"BUNDLE_FILES",
	"MESSAGE_FIELDS",
	"REVISION_FIELDS",
	"TOMBSTONE",
	"build_manifest",
	"build_zip",
	"canonical_jsonl",
	"message_record",
	"readme_text",
	"revision_record",
	"sha256_hex",
	"transcript_html",
	"verify_bundle",
]
