"""Pure-Python (no Frappe site) unit tests for QBO attachment mirroring.

Reuses ``test_quickbooks_online.install_frappe_stub`` to make the QBO core importable
without a bench, then monkeypatches the client + mapping lookup so the mirror logic
(``core.attachments.sync_attachments``) runs deterministically. Covers: a mapped file is
mirrored once, re-runs skip it (idempotency), Notes/metadata-only Attachables and
unmapped entities are skipped, one Attachable linked to several entities mirrors to each,
the entity-type filter, and the ``max_new`` cap.
"""

import types

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub


class _FakeClient:
	"""Stand-in QuickBooksClient: serves canned Attachable pages, records downloads."""

	def __init__(self, pages, downloads=None):
		self._pages = pages
		self._page_idx = 0
		self._downloads = downloads or {}
		self.downloaded = []

	def query(self, _query):
		rows = self._pages[self._page_idx] if self._page_idx < len(self._pages) else []
		self._page_idx += 1
		return {"QueryResponse": {"Attachable": rows}}

	def download_attachable(self, uri):
		self.downloaded.append(uri)
		return self._downloads.get(uri, b"PDF-BYTES")


def _attach(att_id, refs, file_name="receipt.pdf", uri=None):
	"""Build one QBO Attachable dict. ``refs`` = list of (type, id); no file_name => a Note."""
	att = {"Id": att_id, "AttachableRef": [{"EntityRef": {"type": t, "value": v}} for t, v in refs]}
	if file_name is not None:
		att["FileName"] = file_name
		att["TempDownloadUri"] = uri or f"https://intuit.example/dl/{att_id}"
	return att


def _wire(monkeypatch, pages, mapping, existing=None, downloads=None):
	"""Install stubs; return (attachments module, fake client, saved-File list)."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import attachments

	client = _FakeClient(pages, downloads)
	monkeypatch.setattr(attachments, "QuickBooksClient", lambda settings: client)
	monkeypatch.setattr(attachments, "get_settings", lambda: types.SimpleNamespace(realm_id="1", sync_enabled=1))

	def _get_value(doctype, filters, fieldname=None, as_dict=False, **kwargs):
		if doctype == "QuickBooks Sync Mapping":
			hit = mapping.get((filters.get("qbo_entity_type"), filters.get("qbo_id")))
			if not hit:
				return None
			return types.SimpleNamespace(erpnext_doctype=hit[0], erpnext_name=hit[1])
		return None

	already = existing or set()

	def _exists(doctype, filters=None):
		# _already_mirrored passes a dict keyed on custom_qbo_attachable_id;
		# _mapped_document passes (doctype, name) to check the target doc still exists.
		if doctype == "File" and isinstance(filters, dict) and "custom_qbo_attachable_id" in filters:
			return (
				filters["custom_qbo_attachable_id"],
				filters["attached_to_doctype"],
				filters["attached_to_name"],
			) in already
		return True

	monkeypatch.setattr(frappe.db, "get_value", _get_value, raising=False)
	monkeypatch.setattr(frappe.db, "exists", _exists, raising=False)

	saved = []
	monkeypatch.setattr(frappe, "get_doc", lambda d: types.SimpleNamespace(insert=lambda **k: saved.append(d)), raising=False)
	return attachments, client, saved


def test_mirrors_a_mapped_file_attachment(monkeypatch):
	pages = [[_attach("A1", [("Bill", "456")])]]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, pages, mapping)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["scanned"] == 1
	assert client.downloaded == ["https://intuit.example/dl/A1"]
	assert len(saved) == 1
	f = saved[0]
	assert f["doctype"] == "File" and f["is_private"] == 1
	assert f["attached_to_doctype"] == "Purchase Invoice" and f["attached_to_name"] == "PINV-1"
	assert f["custom_qbo_attachable_id"] == "A1" and f["content"] == b"PDF-BYTES"


def test_rerun_skips_already_mirrored(monkeypatch):
	pages = [[_attach("A1", [("Bill", "456")])]]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	existing = {("A1", "Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, pages, mapping, existing=existing)

	summary = attachments.sync_attachments()

	assert summary["skipped_existing"] == 1 and summary["mirrored"] == 0
	assert client.downloaded == [] and saved == []


def test_note_only_attachable_is_skipped(monkeypatch):
	# No FileName => a text Note, nothing to download.
	pages = [[_attach("A9", [("Bill", "456")], file_name=None)]]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, pages, mapping)

	summary = attachments.sync_attachments()

	assert summary["no_file"] == 1 and summary["mirrored"] == 0
	assert client.downloaded == [] and saved == []


def test_unmapped_entity_is_skipped(monkeypatch):
	pages = [[_attach("A1", [("Estimate", "999")])]]
	attachments, client, saved = _wire(monkeypatch, pages, mapping={})

	summary = attachments.sync_attachments()

	assert summary["no_mapping"] == 1 and summary["mirrored"] == 0
	assert saved == []


def test_one_attachable_mirrors_to_each_linked_doc(monkeypatch):
	pages = [[_attach("A1", [("Bill", "456"), ("Invoice", "789")])]]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	attachments, client, saved = _wire(monkeypatch, pages, mapping)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 2
	assert {(f["attached_to_doctype"], f["attached_to_name"]) for f in saved} == {
		("Purchase Invoice", "PINV-1"),
		("Sales Invoice", "SINV-1"),
	}


def test_entity_types_filter_restricts_downloads(monkeypatch):
	pages = [[_attach("A1", [("Bill", "456"), ("Invoice", "789")])]]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	attachments, client, saved = _wire(monkeypatch, pages, mapping)

	summary = attachments.sync_attachments(entity_types=["Bill"])

	assert summary["mirrored"] == 1
	assert [f["attached_to_doctype"] for f in saved] == ["Purchase Invoice"]


def test_max_new_caps_a_bounded_run(monkeypatch):
	pages = [[_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	attachments, client, saved = _wire(monkeypatch, pages, mapping)

	summary = attachments.sync_attachments(max_new=1)

	assert summary["mirrored"] == 1 and len(saved) == 1
