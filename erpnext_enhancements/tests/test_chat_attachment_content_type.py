# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What an attachment's ``content_type`` is derived from — which, until v1.302.0, was nothing.

Inbound rows carried Google's ``contentType`` verbatim, from a resource whose entire schema is
four fields. Rows created from the SPA were written as ``application/octet-stream``
unconditionally, and uploaded to Google under that type too.

`public/js/chat/message_view.js` decides whether to render an attachment inline by matching
that field against ``INLINE_IMAGE``. So **the same photo appeared inline when it arrived from
Google Chat and as a generic file row when a colleague posted it from the SPA** — an asymmetry
a user notices immediately and cannot explain.

**This is not a security fix and must not be read as one.** Both SPA render paths are ``<img>``
tags, where browsers refuse to execute script inside an SVG, and the byte endpoint serves via
Frappe's ``as_raw``, which sets ``Content-Disposition: attachment`` (verified on
``origin/version-16``). Nothing here closes a live hole. What it does is make the stored value
a *fact* where the bytes can prove one, so whatever reads it next inherits something true
rather than an uploader's claim.

Byte literals here are built with ``bytes([...])`` rather than ``b"\\x89PNG"`` on purpose: this
file is generated and edited by tooling, and an escape that survives one layer and not the next
writes a real control byte into the source. That happened once already.

Run: python -m pytest erpnext_enhancements/tests/test_chat_attachment_content_type.py
"""

from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any


def _install_frappe_stub() -> None:
	"""`attachments` imports frappe at module scope; this tier has none."""
	if "frappe" in sys.modules:
		return
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw(Exception(msg))
	frappe.logger = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.session = types.SimpleNamespace(user="tester@example.com")
	frappe.flags = types.SimpleNamespace()
	frappe.local = types.SimpleNamespace(response=types.SimpleNamespace())
	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None, get_single_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None, exists=lambda *a, **k: None, sql=lambda *a, **k: [],
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.get_cached_doc = lambda *a, **k: None
	frappe.__dict__["_"] = lambda s: s
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
	frappe.ValidationError = type("ValidationError", (Exception,), {})

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	utils.now_datetime = lambda: "2026-08-15 00:00:00"
	utils.get_url = lambda *a, **k: "https://example.invalid"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document

	# `attachments` imports `savepoint` at module scope. A no-op decorator is right here:
	# nothing in this file exercises a transaction, and a stub that pretended to manage one
	# would be asserting something this tier cannot see.
	database_pkg = types.ModuleType("frappe.database")
	database_mod = types.ModuleType("frappe.database.database")
	database_mod.savepoint = lambda *a, **k: (lambda fn: fn)
	database_pkg.database = database_mod
	frappe.database = database_pkg

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.database": database_pkg,
			"frappe.database.database": database_mod,
		}
	)


_install_frappe_stub()

from erpnext_enhancements.chat.sync import attachments  # noqa: E402

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b" trailing"
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b" trailing"
GIF = b"GIF89a" + b" trailing"
PDF = b"%PDF-1.7 trailing"
UNKNOWN = bytes([0x00, 0x01, 0x02, 0x03])
OCTET: Any = attachments.DEFAULT_ATTACHMENT_CONTENT_TYPE


# ----------------------------------------------------------------------------- the sniffer


def test_a_signature_beats_a_lying_extension() -> None:
	"""The only branch that produces a fact rather than a claim."""
	assert attachments.sniff_content_type(PNG, "photo.jpg") == "image/png"


def test_each_declared_signature_is_actually_recognised() -> None:
	"""Otherwise the table is decoration."""
	for blob, expected in ((PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (PDF, "application/pdf")):
		assert attachments.sniff_content_type(blob, "whatever.bin") == expected


def test_the_extension_is_the_fallback_when_the_bytes_prove_nothing() -> None:
	assert attachments.sniff_content_type(b"not a known header", "notes.txt") == "text/plain"
	assert attachments.sniff_content_type(b"riff-ish bytes", "clip.webp") == "image/webp"


def test_unknown_bytes_with_an_unknown_name_are_octet_stream() -> None:
	"""Not a failure. It is the correct name for unknown bytes, and it downloads rather than
	rendering."""
	assert attachments.sniff_content_type(UNKNOWN, "mystery.qqq") == OCTET


def test_svg_is_never_produced() -> None:
	"""The one entry worth arguing about.

	SVG has no magic number — it is XML — so it can only ever be guessed at, and it is the one
	image type that is a document with a script element in it. Guessing a file into the single
	format that renders as an image *and* can execute is the wrong direction to be wrong in, so
	an ``.svg`` stores as octet-stream and downloads instead of rendering inline.

	Defence in depth, not a patched hole: see the module docstring.
	"""
	svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
	assert attachments.sniff_content_type(svg, "logo.svg") == OCTET
	assert "image/svg+xml" not in set(attachments._EXTENSION_TYPES.values())


def test_it_survives_empty_and_missing_input() -> None:
	"""It runs on the failure path of a background fan-out, where the bytes may be empty."""
	assert attachments.sniff_content_type(None, "") == OCTET
	assert attachments.sniff_content_type(b"", "x.png") == "image/png"


def test_no_signature_is_shadowed_by_an_earlier_one() -> None:
	"""The table is scanned in order and returns on the first match, so a signature listed
	after one of its own prefixes could never be reached. Asserted directly rather than via a
	length ordering — ordering by length is one way to satisfy this and not the property."""
	for i, (prefix, _) in enumerate(attachments._SIGNATURES):
		for other, _ in attachments._SIGNATURES[:i]:
			assert not prefix.startswith(other), f"{prefix!r} is shadowed by {other!r}"


# ------------------------------------------------------- inbound: the bytes check the claim


def test_google_keeps_its_claim_when_the_bytes_prove_nothing() -> None:
	"""Google saw the upload; an extension did not. The declared type wins a tie."""
	assert (
		attachments._verified_content_type(b"no signature here", "f.bin", "application/vnd.thing")
		== "application/vnd.thing"
	)


def test_a_proven_signature_overrides_a_wrong_declared_type() -> None:
	"""If the first bytes say PNG and the resource says PDF, one of those two is a fact."""
	assert attachments._verified_content_type(PNG, "f.pdf", "application/pdf") == "image/png"


def test_a_declared_type_carrying_parameters_is_not_a_disagreement() -> None:
	"""``text/plain; charset=utf-8`` does not contradict ``text/plain``."""
	assert (
		attachments._verified_content_type(b"plain words", "notes.txt", "text/plain; charset=utf-8")
		== "text/plain; charset=utf-8"
	)


def test_an_absent_declared_type_falls_back_to_the_sniff() -> None:
	assert attachments._verified_content_type(PNG, "f.png", "") == "image/png"


def test_it_never_raises_on_a_half_booted_logger() -> None:
	"""``_logger()`` returns None on a half-booted frappe, and this helper runs inside the
	ingest. Calling ``.info`` on it unguarded was enough to turn a stored attachment into
	``Failed`` — which is how the first version of this shipped into the suite."""
	assert attachments._verified_content_type(PNG, "f.pdf", "application/pdf") == "image/png"


# ------------------------------------------------------------- outbound: it reaches the row


def test_the_outbound_upload_carries_the_sniffed_type() -> None:
	"""Carried on the upload rather than re-derived at the write: the bytes are in hand exactly
	once, and re-reading the File to answer a question already answered is how two sides of one
	record drift apart."""
	fields = {f.name for f in dataclasses.fields(attachments.OutboundUpload)}
	assert "content_type" in fields


def test_the_upload_defaults_to_octet_stream_rather_than_empty() -> None:
	"""An empty content type is not a valid one, and it would reach Google as a header."""
	upload = attachments.OutboundUpload(
		file="F", file_name="a.bin", upload_token="t", resource_name="r", content_hash="h", byte_size=1
	)
	assert upload.content_type == OCTET
