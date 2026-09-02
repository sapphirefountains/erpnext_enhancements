"""Pure-Python (no Frappe site) unit tests for QBO attachment mirroring.

Reuses ``test_quickbooks_online.install_frappe_stub`` to make the QBO core importable
without a bench, then monkeypatches the client + mapping lookup so the mirror logic
(``core.attachments.sync_attachments``) runs deterministically. Covers: a mapped file is
mirrored once, re-runs skip it (idempotency), Notes/metadata-only Attachables and
unmapped entities are skipped, one Attachable linked to several entities mirrors to each,
the entity-type filter, the ``max_new`` cap, the ``start_position``/``max_scan`` resumable
chunk window (and its ``next_start``/``exhausted`` bookkeeping), that one file's failure is
counted and never aborts the run, the SIGALRM save-timeout guard's no-op conditions, the
streamed ``download_attachable`` total-time / size caps, the write-ahead attempt marker
that makes the scheduled pass self-protecting (stale marker => hung, reported once; marker
cleared on success; N failures => skipped; RQ timeout released vs settled), and the
fresh-ticket batching (URIs re-queried <= 50 at a time right before their downloads; a
rejected ticket refreshed exactly once).

The fake client is faithful to QBO's ``STARTPOSITION``/``MAXRESULTS`` paging (it parses the
query and slices a flat Attachable list) so the chunk-window math is exercised for real
rather than mocked away, and to the ``WHERE Id IN (...)`` re-query, minting a new ticket
serial per query so a test can tell a page-time URI from a fresh one. The attempt markers
live in an in-memory stand-in for the QuickBooks Sync Mapping rows (``_MarkerTable``).
"""

import json
import re
import signal
import time
import types
from datetime import datetime, timedelta

import pytest

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub

_HAS_SIGALRM = hasattr(signal, "SIGALRM")
_NOW = datetime(2026, 9, 1, 3, 0, 0)


def _client_module():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	return client_module


class _FakeClient:
	"""Stand-in QuickBooksClient over a flat Attachable list.

	Faithful to QBO paging and to the ``WHERE Id IN (...)`` re-query the mirror uses for
	fresh download tickets: every such query (and every ``get_entity``) bumps a ticket
	serial that is stamped into the returned ``TempDownloadUri`` (``...?ticket=N``), so a
	page-time URI (no ticket) is distinguishable from a just-fetched one. ``reject_once`` /
	``reject_always`` make ``download_attachable`` reject an Attachable's ticket the way an
	expired one is rejected; ``vanished`` ids are paged but absent from the re-query (deleted
	in QBO in between); ``reject_in_queries`` simulates QBO refusing the IN grammar.
	``events`` is a shared, ordered log the harness also appends its DB events to, so tests
	can assert interleaving.

	``api_error`` / ``ticket_error`` are the real client exception classes, handed over by
	``_wire`` -- resolving them here via ``install_frappe_stub()`` mid-run would re-create
	``frappe.db`` and discard the harness's monkeypatches."""

	def __init__(self, attachables, downloads=None, events=None):
		self._all = list(attachables)
		self._downloads = downloads or {}
		self.downloaded = []
		self.queries = []
		self.uri_queries = []
		self.entity_gets = []
		self.events = events if events is not None else []
		self.ticket = 0
		self.reject_once = set()
		self.reject_always = set()
		self.vanished = set()
		self.reject_in_queries = False
		self.api_error = self.ticket_error = None  # set by _wire

	def query(self, query):
		self.queries.append(query)
		m = re.search(r"WHERE Id IN \((.*?)\)", query)
		if m:
			if self.reject_in_queries:
				raise self.api_error("QuickBooks API request failed: 400 QueryParserError")
			ids = re.findall(r"'([^']*)'", m.group(1))
			self.uri_queries.append(ids)
			self.events.append(("uri_query", ids))
			self.ticket += 1
			rows = [
				self._fresh(att)
				for att in self._all
				if str(att.get("Id")) in ids and str(att.get("Id")) not in self.vanished
			]
			return {"QueryResponse": {"Attachable": rows}}
		m = re.search(r"STARTPOSITION\s+(\d+)\s+MAXRESULTS\s+(\d+)", query)
		start = int(m.group(1)) - 1  # QBO STARTPOSITION is 1-based
		maxresults = int(m.group(2))
		return {"QueryResponse": {"Attachable": self._all[start : start + maxresults]}}

	def get_entity(self, entity_type, qbo_id):
		self.entity_gets.append(str(qbo_id))
		self.ticket += 1
		for att in self._all:
			if str(att.get("Id")) == str(qbo_id) and str(qbo_id) not in self.vanished:
				return {"Attachable": self._fresh(att)}
		raise self.api_error("QuickBooks API request failed: 400 Object Not Found")

	def _fresh(self, att):
		row = dict(att)
		if row.get("TempDownloadUri"):
			row["TempDownloadUri"] = f"{row['TempDownloadUri'].split('?')[0]}?ticket={self.ticket}"
		return row

	def download_attachable(self, uri, **_kwargs):
		base = uri.split("?")[0]
		att_id = base.rsplit("/", 1)[-1]
		self.downloaded.append(uri)
		self.events.append(("download", att_id, uri))
		if att_id in self.reject_always or att_id in self.reject_once:
			self.reject_once.discard(att_id)
			raise self.ticket_error(f"QuickBooks attachment download ticket rejected: 401 {uri}")
		return self._downloads.get(base, b"PDF-BYTES")


class _DuplicateEntryError(Exception):
	"""Stand-in for ``frappe.DuplicateEntryError`` -- the primary-key collision an
	overlapping run's marker insert raises (the row name IS the natural key)."""


class _MarkerTable(dict):
	"""In-memory stand-in for the QuickBooks Sync Mapping rows of type Attachable that
	carry the attempt markers, keyed by row name (the doctype's autoname format)."""

	@staticmethod
	def row_name(att_id):
		return f"QBO-MAP-Attachable-{att_id}"

	def seed(self, att_id, state, match_status="Not Matched"):
		"""Pre-plant a marker, as a previous run would have left it."""
		self[self.row_name(att_id)] = {
			"name": self.row_name(att_id),
			"qbo_entity_type": "Attachable",
			"qbo_id": str(att_id),
			"owned_fields": json.dumps(state),
			"match_status": match_status,
			"match_rule": "qbo_attachment_mirror",
		}

	def state(self, att_id):
		row = self.get(self.row_name(att_id))
		return json.loads(row["owned_fields"]) if row else None

	def match_status(self, att_id):
		row = self.get(self.row_name(att_id))
		return row["match_status"] if row else None


def _attach(att_id, refs, file_name="receipt.pdf", uri=None):
	"""Build one QBO Attachable dict. ``refs`` = list of (type, id); no file_name => a Note."""
	att = {"Id": att_id, "AttachableRef": [{"EntityRef": {"type": t, "value": v}} for t, v in refs]}
	if file_name is not None:
		att["FileName"] = file_name
		att["TempDownloadUri"] = uri or f"https://intuit.example/dl/{att_id}"
	return att


def _wire(monkeypatch, attachables, mapping, existing=None, downloads=None, now=_NOW):
	"""Install stubs; return a namespace of the module under test plus the harness state
	(``attachments``, ``client``, ``saved`` Files, ``markers``, ``events``, ``logged``).

	``now`` is what ``now_datetime()`` returns -- a datetime, or a list of datetimes handed
	out one per call (the last one repeating) so a test can age an attempt between the
	marker write and a later check."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import attachments

	events = []
	client = _FakeClient(attachables, downloads, events)
	client.api_error = attachments.QuickBooksAPIError
	client.ticket_error = attachments.QuickBooksDownloadTicketError
	monkeypatch.setattr(attachments, "QuickBooksClient", lambda settings: client)
	monkeypatch.setattr(attachments, "get_settings", lambda: types.SimpleNamespace(realm_id="1", sync_enabled=1))

	clock = list(now) if isinstance(now, (list, tuple)) else [now]
	monkeypatch.setattr(attachments, "now_datetime", lambda: clock.pop(0) if len(clock) > 1 else clock[0])
	monkeypatch.setattr(
		attachments,
		"get_datetime",
		lambda value: value if isinstance(value, datetime) else datetime.fromisoformat(str(value)),
	)

	markers = _MarkerTable()

	def _get_value(doctype, filters, fieldname=None, as_dict=False, **kwargs):
		if doctype == "QuickBooks Sync Mapping":
			if filters.get("qbo_entity_type") == "Attachable":
				for row in markers.values():
					if row["qbo_id"] == filters.get("qbo_id"):
						return types.SimpleNamespace(**row)
				return None
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

	def _set_value(doctype, name, values, *args, **kwargs):
		assert doctype == "QuickBooks Sync Mapping" and name in markers
		markers[name].update(values)
		events.append(("marker", markers[name]["qbo_id"], json.loads(values["owned_fields"])["state"]))

	def _delete(doctype, filters):
		assert doctype == "QuickBooks Sync Mapping"
		for name, row in list(markers.items()):
			if name == filters.get("name") or (
				row["qbo_entity_type"] == filters.get("qbo_entity_type") and row["qbo_id"] == filters.get("qbo_id")
			):
				del markers[name]
				events.append(("marker_delete", name))

	saved = []
	logged = []

	def _get_doc(d):
		if d.get("doctype") == "File":

			def _insert_file(**_k):
				saved.append(d)
				events.append(("file_insert", d["custom_qbo_attachable_id"]))

			return types.SimpleNamespace(insert=_insert_file)
		if d.get("doctype") == "QuickBooks Sync Mapping":
			doc = types.SimpleNamespace(name=None)

			def _insert_marker(**_k):
				doc.name = _MarkerTable.row_name(d["qbo_id"])
				if doc.name in markers:
					raise _DuplicateEntryError(f"QuickBooks Sync Mapping {doc.name} already exists")
				markers[doc.name] = {"name": doc.name, **{k: v for k, v in d.items() if k != "doctype"}}
				events.append(("marker", d["qbo_id"], json.loads(d["owned_fields"])["state"]))

			doc.insert = _insert_marker
			return doc
		raise AssertionError(f"unexpected get_doc({d!r})")

	monkeypatch.setattr(frappe.db, "get_value", _get_value, raising=False)
	monkeypatch.setattr(frappe.db, "exists", _exists, raising=False)
	monkeypatch.setattr(frappe.db, "set_value", _set_value, raising=False)
	monkeypatch.setattr(frappe.db, "delete", _delete, raising=False)
	# The mirror commits per file (and per marker write) and rolls back a failed one.
	monkeypatch.setattr(frappe.db, "commit", lambda: events.append(("commit",)), raising=False)
	monkeypatch.setattr(frappe.db, "rollback", lambda *a, **k: None, raising=False)
	# sync_attachments toggles these around the run (notification mute); give them a home.
	monkeypatch.setattr(frappe, "flags", types.SimpleNamespace(in_import=False, mute_emails=False), raising=False)
	# The per-file except path logs; the stub has no logger of its own.
	monkeypatch.setattr(frappe, "get_traceback", lambda *a, **k: "traceback", raising=False)
	monkeypatch.setattr(frappe, "log_error", lambda *a, **k: logged.append((a, k)), raising=False)
	monkeypatch.setattr(frappe, "get_doc", _get_doc, raising=False)
	# The PK-collision class _mirror_one recognises for an overlapping run's insert.
	monkeypatch.setattr(frappe, "DuplicateEntryError", _DuplicateEntryError, raising=False)
	return types.SimpleNamespace(
		attachments=attachments, client=client, saved=saved, markers=markers, events=events, logged=logged
	)


def _downloads_of(w):
	"""Attachable ids downloaded, in order."""
	return [event[1] for event in w.events if event[0] == "download"]


def test_mirrors_a_mapped_file_attachment(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456")])]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["scanned"] == 1
	assert summary["exhausted"] is True
	# Downloaded from a freshly re-queried ticket, never the page-time URI.
	assert w.client.downloaded == ["https://intuit.example/dl/A1?ticket=1"]
	assert len(w.saved) == 1
	f = w.saved[0]
	assert f["doctype"] == "File" and f["is_private"] == 1
	assert f["attached_to_doctype"] == "Purchase Invoice" and f["attached_to_name"] == "PINV-1"
	assert f["custom_qbo_attachable_id"] == "A1" and f["content"] == b"PDF-BYTES"


def test_rerun_skips_already_mirrored(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456")])]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	existing = {("A1", "Purchase Invoice", "PINV-1")}
	w = _wire(monkeypatch, attachables, mapping, existing=existing)

	summary = w.attachments.sync_attachments()

	assert summary["skipped_existing"] == 1 and summary["mirrored"] == 0
	assert w.client.downloaded == [] and w.saved == []
	assert w.client.uri_queries == []  # nothing to download => no ticket query at all


def test_note_only_attachable_is_skipped(monkeypatch):
	# No FileName => a text Note, nothing to download.
	attachables = [_attach("A9", [("Bill", "456")], file_name=None)]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["no_file"] == 1 and summary["mirrored"] == 0
	assert w.client.downloaded == [] and w.saved == []


def test_unmapped_entity_is_skipped(monkeypatch):
	attachables = [_attach("A1", [("Estimate", "999")])]
	w = _wire(monkeypatch, attachables, mapping={})

	summary = w.attachments.sync_attachments()

	assert summary["no_mapping"] == 1 and summary["mirrored"] == 0
	assert w.saved == []


def test_one_attachable_mirrors_to_each_linked_doc(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456"), ("Invoice", "789")])]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 2
	assert {(f["attached_to_doctype"], f["attached_to_name"]) for f in w.saved} == {
		("Purchase Invoice", "PINV-1"),
		("Sales Invoice", "SINV-1"),
	}
	assert w.client.uri_queries == [["A1"]]  # one ticket query covers both links


def test_entity_types_filter_restricts_downloads(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456"), ("Invoice", "789")])]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments(entity_types=["Bill"])

	assert summary["mirrored"] == 1
	assert [f["attached_to_doctype"] for f in w.saved] == ["Purchase Invoice"]


def test_max_new_caps_a_bounded_run(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments(max_new=1)

	assert summary["mirrored"] == 1 and len(w.saved) == 1


# --------------------------------------------------------------- resumable chunk window


def test_max_scan_bounds_the_window_and_reports_next_start(monkeypatch):
	# Five mapped Attachables; a chunk of two scans exactly two and points past them.
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments(start_position=1, max_scan=2)

	assert summary["scanned"] == 2 and summary["mirrored"] == 2
	assert summary["next_start"] == 3 and summary["exhausted"] is False
	assert [f["custom_qbo_attachable_id"] for f in w.saved] == ["A1", "A2"]


def test_start_position_resumes_midway(monkeypatch):
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	w = _wire(monkeypatch, attachables, mapping)

	# Resume at the 3rd Attachable, window of two => A3, A4.
	summary = w.attachments.sync_attachments(start_position=3, max_scan=2)

	assert [f["custom_qbo_attachable_id"] for f in w.saved] == ["A3", "A4"]
	assert summary["next_start"] == 5 and summary["exhausted"] is False


def test_chunk_window_reaching_the_end_sets_exhausted(monkeypatch):
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	w = _wire(monkeypatch, attachables, mapping)

	# A window that runs past the last row must report exhaustion so the driver stops.
	summary = w.attachments.sync_attachments(start_position=5, max_scan=50)

	assert summary["scanned"] == 1 and summary["mirrored"] == 1
	assert summary["exhausted"] is True


def test_full_chunk_loop_covers_every_attachable_exactly_once(monkeypatch):
	# Drive the whole list in windows of two, exactly as the backfill shell loop does.
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	w = _wire(monkeypatch, attachables, mapping)

	start, seen = 1, []
	for _ in range(10):  # generous bound; the loop breaks on exhaustion
		summary = w.attachments.sync_attachments(start_position=start, max_scan=2)
		seen.extend(f["custom_qbo_attachable_id"] for f in w.saved[len(seen) :])
		if summary["exhausted"]:
			break
		start = summary["next_start"]

	assert seen == ["A1", "A2", "A3", "A4", "A5"]


# --------------------------------------------------------------- failure containment


def test_one_file_failure_is_counted_and_run_continues(monkeypatch):
	attachables = [_attach("GOOD", [("Bill", "1")]), _attach("BAD", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	w = _wire(monkeypatch, attachables, mapping)

	real_save = w.attachments._save_attachment

	def _flaky_save(content, file_name, att_id, doctype, name):
		if att_id == "BAD":
			raise RuntimeError("simulated pypdf blow-up / timeout")
		return real_save(content, file_name, att_id, doctype, name)

	monkeypatch.setattr(w.attachments, "_save_attachment", _flaky_save)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["errors"] == 1
	assert summary["scanned"] == 2  # the run did not abort on the bad file
	assert [f["custom_qbo_attachable_id"] for f in w.saved] == ["GOOD"]
	# The failure is recorded against the file (one attempt, still retryable); the good
	# file's marker is gone.
	assert w.markers.state("BAD")["state"] == "failed" and w.markers.state("BAD")["attempts"] == 1
	assert w.markers.state("GOOD") is None


def test_rq_job_timeout_is_reraised_not_swallowed(monkeypatch):
	# RQ's death penalty must propagate so RQ can fail the job; the per-file except must
	# NOT count it as a mirror error and continue (that would spend RQ's one-shot alarm
	# and leave the rest of the run untimed).
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	class JobTimeoutException(Exception):  # name-matched by _is_rq_job_timeout's fallback
		pass

	def _timeout(_uri):
		raise JobTimeoutException("rq death penalty")

	monkeypatch.setattr(w.client, "download_attachable", _timeout)

	with pytest.raises(JobTimeoutException):
		w.attachments.sync_attachments()
	assert w.saved == []


# --------------------------------------------------------------- over-long filenames


def test_bounded_file_name_fits_limit_and_keeps_extension(monkeypatch):
	w = _wire(monkeypatch, [], mapping={})
	long_name = "A" * 200 + ".pdf"
	out = w.attachments._bounded_file_name(long_name)
	assert len(out) <= 140 and out.endswith(".pdf") and out.startswith("...")
	# Short names and None pass through untouched.
	assert w.attachments._bounded_file_name("receipt.pdf") == "receipt.pdf"
	assert w.attachments._bounded_file_name(None) is None


def test_over_long_filename_is_truncated_not_errored(monkeypatch):
	# QBO's long base64 filenames exceed File.file_name's Data(140) and used to raise
	# CharacterLengthExceededError on insert (~19 skips in the first backfill).
	long_name = "Z" * 180 + ".pdf"
	attachables = [_attach("A1", [("Bill", "1")], file_name=long_name)]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["errors"] == 0
	assert len(w.saved[0]["file_name"]) <= 140 and w.saved[0]["file_name"].endswith(".pdf")


# --------------------------------------------------------------- write-ahead attempt marker


def test_marker_is_committed_before_the_download_and_cleared_with_the_file(monkeypatch):
	# The protocol, in order: fresh ticket -> marker written + COMMITTED -> download ->
	# File insert -> marker deleted -> one commit for both. A SIGKILL anywhere after the
	# first commit leaves the "attempting" evidence behind.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1
	assert w.markers == {}  # cleared on success
	assert w.events == [
		("uri_query", ["A1"]),
		("marker", "A1", "attempting"),
		("commit",),
		("download", "A1", "https://intuit.example/dl/A1?ticket=1"),
		("file_insert", "A1"),
		("marker_delete", "QBO-MAP-Attachable-A1"),
		("commit",),
	]


def test_stale_attempt_marker_is_settled_as_hung_skipped_and_reported_once(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	w = _wire(monkeypatch, attachables, mapping)
	# A marker a run wrote a day ago and never cleared: that run died on A1 (a malformed
	# PDF hung File.insert() until RQ killed the job, or the worker was SIGKILLed).
	w.markers.seed(
		"A1",
		{"state": "attempting", "attempts": 1, "started_at": "2026-08-31 03:00:00", "target": "Purchase Invoice/P1"},
	)

	summary = w.attachments.sync_attachments()

	assert summary["skipped_hung"] == 1 and summary["mirrored"] == 1 and summary["errors"] == 0
	assert w.client.uri_queries == [["A2"]]  # no ticket is even fetched for the poisoned file
	assert _downloads_of(w) == ["A2"]
	assert w.markers.state("A1")["state"] == "hung" and w.markers.match_status("A1") == "Pending Review"
	assert len(w.logged) == 1 and w.logged[0][1]["title"] == "QBO attachment A1 skipped: hung"
	assert "reset_attachable('A1')" in w.logged[0][1]["message"]

	# A later run: still skipped, still counted, NOT reported again.
	summary = w.attachments.sync_attachments()

	assert summary["skipped_hung"] == 1 and len(w.logged) == 1
	assert _downloads_of(w) == ["A2", "A2"]


def test_young_attempt_marker_is_left_alone_as_in_flight(monkeypatch):
	# Started a minute ago: an overlapping run (the daily job and a manual pass) may still
	# own it. Skip without judgement and without touching the row.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	planted = {"state": "attempting", "attempts": 1, "started_at": "2026-09-01 02:59:00"}
	w.markers.seed("A1", planted)

	summary = w.attachments.sync_attachments()

	assert summary["skipped_in_flight"] == 1 and summary["mirrored"] == 0 and summary["skipped_hung"] == 0
	assert w.client.downloaded == [] and w.logged == []
	assert w.markers.state("A1") == planted


def test_repeated_failures_exhaust_the_attempt_budget_then_skip(monkeypatch):
	attachables = [_attach("BAD", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	def _broken_save(content, file_name, att_id, doctype, name):
		raise RuntimeError("simulated File.insert() validation failure")

	monkeypatch.setattr(w.attachments, "_save_attachment", _broken_save)

	for attempt in (1, 2):
		summary = w.attachments.sync_attachments()
		assert summary["errors"] == 1 and summary["skipped_failed"] == 0
		assert w.markers.state("BAD")["state"] == "failed" and w.markers.state("BAD")["attempts"] == attempt
		assert w.markers.match_status("BAD") == "Not Matched"  # still retryable

	summary = w.attachments.sync_attachments()  # third strike: given up on
	assert summary["errors"] == 1
	assert w.markers.state("BAD")["attempts"] == 3 and w.markers.match_status("BAD") == "Pending Review"
	assert "simulated" in w.markers.state("BAD")["last_error"]

	summary = w.attachments.sync_attachments()  # fourth run: skipped without a download
	assert summary["skipped_failed"] == 1 and summary["errors"] == 0
	assert len(w.client.downloaded) == 3 and w.client.uri_queries == [["BAD"]] * 3


def test_save_timeout_settles_the_file_as_hung_at_once(monkeypatch):
	# On the bench-execute path the SIGALRM guard converts the hang into _AttachmentTimeout;
	# that IS the hang signal, so the file is settled as hung without two more 90s attempts.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	def _hang(content, file_name, att_id, doctype, name):
		raise w.attachments._AttachmentTimeout("File insert exceeded 90s")

	monkeypatch.setattr(w.attachments, "_save_attachment", _hang)

	summary = w.attachments.sync_attachments()
	assert summary["errors"] == 1
	assert w.markers.state("A1")["state"] == "hung" and w.markers.match_status("A1") == "Pending Review"

	summary = w.attachments.sync_attachments()
	assert summary["skipped_hung"] == 1 and summary["errors"] == 0 and len(w.client.downloaded) == 1


def test_sibling_link_of_a_hung_attachable_is_not_retried_in_the_same_run(monkeypatch):
	# One Attachable on two documents: once the first link hangs, the second must re-read
	# the marker and skip -- the poison is in the bytes, not the target.
	attachables = [_attach("A1", [("Bill", "1"), ("Invoice", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Invoice", "2"): ("Sales Invoice", "S1")}
	w = _wire(monkeypatch, attachables, mapping)

	def _hang(content, file_name, att_id, doctype, name):
		raise w.attachments._AttachmentTimeout("File insert exceeded 90s")

	monkeypatch.setattr(w.attachments, "_save_attachment", _hang)

	summary = w.attachments.sync_attachments()

	assert summary["errors"] == 1 and summary["skipped_hung"] == 1
	assert len(w.client.downloaded) == 1


def test_rq_timeout_on_a_healthy_file_releases_its_marker(monkeypatch):
	# RQ times the whole job (300s on the default queue); its alarm landing 5s into this
	# file means the run's budget expired, not that the file hung. Release it for the next
	# run -- and still re-raise.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping, now=[_NOW, _NOW + timedelta(seconds=5)])

	class JobTimeoutException(Exception):
		pass

	def _timeout(_uri):
		raise JobTimeoutException("rq death penalty")

	monkeypatch.setattr(w.client, "download_attachable", _timeout)

	with pytest.raises(JobTimeoutException):
		w.attachments.sync_attachments()

	assert w.markers == {} and w.logged == []
	assert w.events[-2:] == [("marker_delete", "QBO-MAP-Attachable-A1"), ("commit",)]


def test_rq_timeout_on_a_long_running_file_settles_it_as_hung(monkeypatch):
	# Two minutes into one file is past SAVE_TIMEOUT_SECONDS: this file is the hang.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping, now=[_NOW, _NOW + timedelta(seconds=120)])

	class JobTimeoutException(Exception):
		pass

	def _timeout(_uri):
		raise JobTimeoutException("rq death penalty")

	monkeypatch.setattr(w.client, "download_attachable", _timeout)

	with pytest.raises(JobTimeoutException):
		w.attachments.sync_attachments()

	assert w.markers.state("A1")["state"] == "hung" and w.markers.match_status("A1") == "Pending Review"
	assert len(w.logged) == 1 and w.logged[0][1]["title"] == "QBO attachment A1 skipped: hung"

	summary = w.attachments.sync_attachments()
	assert summary["skipped_hung"] == 1


def test_reset_attachable_puts_a_skipped_file_back_in_play(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	w.markers.seed("A1", {"state": "hung", "attempts": 1, "started_at": "2026-08-31 03:00:00"}, "Pending Review")

	assert w.attachments.sync_attachments()["skipped_hung"] == 1

	w.attachments.reset_attachable("A1")

	assert w.markers == {}
	assert w.attachments.sync_attachments()["mirrored"] == 1


def test_a_retry_after_an_ordinary_failure_commits_the_attempting_marker_before_downloading(monkeypatch):
	# The set_value branch of _write_marker (a row already exists from a failed attempt)
	# must commit before the download exactly like the insert branch, and a success then
	# deletes the row rather than leaving it at attempts=1.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	w.markers.seed("A1", {"state": "failed", "attempts": 1, "started_at": "2026-08-31 03:00:00"})

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and w.markers == {}
	i = w.events.index(("marker", "A1", "attempting"))
	assert w.events[i + 1] == ("commit",)
	assert w.events[i + 2][0] == "download"


def test_a_guarded_marker_left_behind_is_retried_not_settled_hung(monkeypatch):
	# The backfill (SIGALRM guard armed) was killed from outside -- its OS `timeout`, a
	# deploy restart -- while a healthy file was in flight. The guard would have settled a
	# real hang itself, so the leftover marker is one ordinary failure: retry it.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	w.markers.seed(
		"A1", {"state": "attempting", "attempts": 1, "started_at": "2026-08-31 03:00:00", "guarded": True}
	)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["skipped_hung"] == 0 and summary["errors"] == 0
	assert w.markers == {} and w.logged == []
	# Rewritten as one failure first (the killed attempt stays on the books), then retried.
	assert w.events.index(("marker", "A1", "failed")) < w.events.index(("marker", "A1", "attempting"))


def test_a_guarded_marker_on_its_last_attempt_is_given_up_on_not_hung(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	w.markers.seed(
		"A1", {"state": "attempting", "attempts": 3, "started_at": "2026-08-31 03:00:00", "guarded": True}
	)

	summary = w.attachments.sync_attachments()

	assert summary["skipped_failed"] == 1 and summary["skipped_hung"] == 0 and summary["mirrored"] == 0
	assert w.markers.state("A1")["state"] == "failed" and w.markers.match_status("A1") == "Pending Review"
	assert w.client.downloaded == [] and w.logged == []


def test_new_markers_record_whether_the_guard_was_armed(monkeypatch):
	# What _check_marker reads a day later to tell "killed from outside" from "hung".
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	monkeypatch.setattr(w.attachments, "_guard_armed", lambda seconds=None: True)
	written = {}
	real_write = w.attachments._write_marker

	def _spy(name, att_id, state, **kwargs):
		written.setdefault(att_id, dict(state))
		return real_write(name, att_id, state, **kwargs)

	monkeypatch.setattr(w.attachments, "_write_marker", _spy)

	w.attachments.sync_attachments()

	assert written["A1"]["state"] == "attempting" and written["A1"]["guarded"] is True


def test_overlap_loser_skips_as_in_flight_without_logging(monkeypatch):
	# Two runs reach the same fresh Attachable; the other one inserts the marker between
	# our _check_marker read and our insert (a REPEATABLE READ snapshot can predate its
	# commit). The PK collision means that run owns the file: skip without judgement, no
	# Error Log, no attempt charged, the winner's row untouched.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	planted = {"state": "attempting", "attempts": 1, "started_at": "2026-09-01 02:59:30"}
	real_check = w.attachments._check_marker
	calls = []

	def _stale_snapshot(att_id):
		calls.append(att_id)
		if len(calls) <= 2:  # _collect_pending's look and _mirror_one's re-read both miss it
			return "go", None, {}
		return real_check(att_id)

	monkeypatch.setattr(w.attachments, "_check_marker", _stale_snapshot)
	w.markers.seed("A1", planted)  # the other run's row, invisible to our snapshot

	summary = w.attachments.sync_attachments()

	assert summary["skipped_in_flight"] == 1 and summary["errors"] == 0 and summary["mirrored"] == 0
	assert w.logged == [] and w.client.downloaded == []
	assert w.markers.state("A1") == planted


def test_a_failure_streak_aborts_the_run_and_rewinds_the_markers(monkeypatch):
	# Twelve pending files, every download failing the same way: Intuit's file host is
	# down, not twelve bad files. After FAILURE_STREAK_LIMIT the run stops, the streak's
	# markers go back to what they were (a fresh row: gone; a prior failure: its old
	# count), one Error Log names the streak, and the files past it are never touched.
	n = 12
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, n + 1)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, n + 1)}
	w = _wire(monkeypatch, attachables, mapping)
	prior = {"state": "failed", "attempts": 1, "started_at": "2026-08-31 03:00:00"}
	w.markers.seed("A3", dict(prior))
	real_download = w.client.download_attachable
	host_down = [True]
	attempted = []

	def _maybe_down(uri, **kwargs):
		attempted.append(uri.split("?")[0].rsplit("/", 1)[-1])
		if host_down[0]:
			raise w.attachments.QuickBooksAPIError("QuickBooks attachment download failed: 503")
		return real_download(uri, **kwargs)

	monkeypatch.setattr(w.client, "download_attachable", _maybe_down)

	summary = w.attachments.sync_attachments()

	limit = w.attachments.FAILURE_STREAK_LIMIT
	assert summary["aborted"] == "failure_streak" and summary["errors"] == limit
	assert attempted == [f"A{i}" for i in range(1, limit + 1)]  # A11, A12 never attempted
	assert set(w.markers) == {"QBO-MAP-Attachable-A3"}  # rewound: nothing charged
	assert w.markers.state("A3") == prior and w.markers.match_status("A3") == "Not Matched"
	abort_titles = [k.get("title") for _a, k in w.logged if k.get("title", "").startswith("QBO attachment mirror aborted")]
	assert abort_titles == [f"QBO attachment mirror aborted: {limit} consecutive failures"]
	assert summary["next_start"] == 1  # the page is re-scanned next run

	# The fault fixed: everything mirrors, nothing was parked meanwhile.
	host_down[0] = False
	summary = w.attachments.sync_attachments()
	assert summary["mirrored"] == n and summary["aborted"] is None and w.markers == {}


def test_a_dead_grant_releases_the_marker_and_aborts_the_run(monkeypatch):
	# The OAuth grant died mid-run: not the file's fault. Forget the attempt (no attempt
	# charged, no per-file Error Log) and let the run abort with the client's own error.
	attachables = [_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	w = _wire(monkeypatch, attachables, mapping)

	attempted = []

	def _revoked(_uri, **_kwargs):
		attempted.append(_uri)
		raise w.attachments.QuickBooksDisconnectedError("QuickBooks is not connected")

	monkeypatch.setattr(w.client, "download_attachable", _revoked)

	with pytest.raises(w.attachments.QuickBooksDisconnectedError):
		w.attachments.sync_attachments()

	assert w.markers == {} and w.logged == []
	assert len(attempted) == 1  # the run stopped at the first file
	# The marker was written, then released -- by natural key -- and both committed.
	assert w.events[-3:] == [("commit",), ("marker_delete", "QBO-MAP-Attachable-A1"), ("commit",)]


def test_a_dead_grant_during_the_ticket_query_is_not_hidden_by_the_per_id_fallback(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	real_query = w.client.query

	def _query(query):
		if "WHERE Id IN" in query:
			raise w.attachments.QuickBooksDisconnectedError("QuickBooks is not connected")
		return real_query(query)

	monkeypatch.setattr(w.client, "query", _query)

	with pytest.raises(w.attachments.QuickBooksDisconnectedError):
		w.attachments.sync_attachments()

	assert w.client.entity_gets == [] and w.client.downloaded == []


# --------------------------------------------------------------- fresh download tickets


def test_download_uris_are_fetched_in_small_batches_right_before_use(monkeypatch):
	# 120 new files on one page: tickets are queried <= 50 at a time, and every download
	# uses the ticket minted by the query immediately preceding it -- never the page-time
	# URI (which would be minutes old by the tail of a 1000-row page), never an earlier
	# batch's.
	n = 120
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, n + 1)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, n + 1)}
	w = _wire(monkeypatch, attachables, mapping)

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == n and summary["errors"] == 0 and summary["url_refreshes"] == 0
	assert [len(ids) for ids in w.client.uri_queries] == [50, 50, 20]
	assert len(w.client.queries) == 1 + 3  # one page query, three ticket queries
	batch_no, current = 0, set()
	for event in w.client.events:
		if event[0] == "uri_query":
			batch_no += 1
			current = set(event[1])
		elif event[0] == "download":
			_, att_id, uri = event
			assert att_id in current
			assert uri.endswith(f"?ticket={batch_no}")
	assert batch_no == 3 and len(w.client.downloaded) == n


def test_rejected_ticket_is_refreshed_exactly_once(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	w = _wire(monkeypatch, attachables, mapping)
	w.client.reject_once.add("A1")  # its first ticket expired; a fresh one works
	w.client.reject_always.add("A2")  # rejected even when fresh: a real failure

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["errors"] == 1 and summary["url_refreshes"] == 2
	# Batch query, then one single-id re-query per rejection -- and only one.
	assert w.client.uri_queries == [["A1", "A2"], ["A1"], ["A2"]]
	assert _downloads_of(w) == ["A1", "A1", "A2", "A2"]
	assert w.client.downloaded[1] == "https://intuit.example/dl/A1?ticket=2"
	assert [f["custom_qbo_attachable_id"] for f in w.saved] == ["A1"]
	assert w.markers.state("A1") is None
	assert w.markers.state("A2")["state"] == "failed" and w.markers.state("A2")["attempts"] == 1


def test_attachable_missing_from_the_fresh_query_falls_back_to_the_page_uri(monkeypatch):
	# Deleted in QBO between the page and the batch: no fresh ticket, so the page URI is
	# tried (it may still work; if it is rejected, the single re-query settles it).
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)
	w.client.vanished.add("A1")

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 1
	assert w.client.downloaded == ["https://intuit.example/dl/A1"]


def test_in_query_rejection_falls_back_to_fetching_tickets_one_by_one(monkeypatch):
	# Belt and braces for the IN grammar (verified from Intuit's docs, not on prod): if
	# QBO refuses it, tickets are still fetched fresh, one GET per Attachable.
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in (1, 2, 3)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in (1, 2, 3)}
	w = _wire(monkeypatch, attachables, mapping)
	w.client.reject_in_queries = True

	summary = w.attachments.sync_attachments()

	assert summary["mirrored"] == 3 and summary["errors"] == 0
	assert w.client.entity_gets == ["A1", "A2", "A3"]
	assert all("?ticket=" in uri for uri in w.client.downloaded)
	# ...and it says so, once per run, so a permanent refusal is not a silent 51-call batch.
	assert summary["uri_query_fallbacks"] == 1
	assert [k.get("title") for _a, k in w.logged] == [
		"QBO attachment mirror: fresh-ticket IN query refused, fetching one by one"
	]


def test_a_plain_download_error_is_not_refreshed(monkeypatch):
	# Only a rejected ticket (401/403) earns a fresh URI; a 5xx or a network error is a
	# plain per-file failure with no second query.
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	w = _wire(monkeypatch, attachables, mapping)

	def _flaky(_uri, **_kwargs):
		raise w.attachments.QuickBooksAPIError("QuickBooks attachment download failed: 503")

	monkeypatch.setattr(w.client, "download_attachable", _flaky)

	summary = w.attachments.sync_attachments()

	assert summary["errors"] == 1 and summary["url_refreshes"] == 0
	assert w.client.uri_queries == [["A1"]]  # the batch query only; no re-query


# --------------------------------------------------------------- save-timeout guard gating


def test_save_time_limit_is_a_noop_when_disabled(monkeypatch):
	w = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(w.attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)

	with w.attachments._save_time_limit(0):
		pass

	assert calls == []  # zero timeout => never arms the alarm


def test_save_time_limit_is_a_noop_off_the_main_thread(monkeypatch):
	w = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(w.attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)
	# Pretend we are not on the main thread (e.g. an RQ worker horse): must not touch signals.
	monkeypatch.setattr(w.attachments.threading, "current_thread", lambda: object())

	with w.attachments._save_time_limit(30):
		pass

	assert calls == []


def test_save_time_limit_is_a_noop_inside_a_background_job(monkeypatch):
	w = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(w.attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)
	monkeypatch.setattr(w.attachments, "_in_background_job", lambda: True)

	with w.attachments._save_time_limit(30):
		pass

	assert calls == []  # RQ owns SIGALRM in a worker; we must not clobber it


# --------------------------------------------------------------- hardened download


class _FakeResp:
	def __init__(self, chunks, status=200, text=""):
		self._chunks = chunks
		self.status_code = status
		self.text = text

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False

	def iter_content(self, chunk_size=None):
		yield from self._chunks


class _FakeRequestException(Exception):
	"""Stand-in for requests.exceptions.RequestException."""


class _FakeConnectionError(_FakeRequestException):
	"""Stand-in for requests.exceptions.ConnectionError (a RequestException subclass)."""


def _install_fake_requests(monkeypatch, client_module, get_fn):
	"""Give client.py a controllable fake ``requests`` with ``get`` and the
	``exceptions.RequestException`` hierarchy ``download_attachable`` catches.

	The bench-free suites install a BARE ``requests`` stub (``types.ModuleType`` with no
	``.get`` and no ``.exceptions`` -- see ``test_quickbooks_online.install_frappe_stub``),
	so patching an attribute onto it fails and the ``except requests.exceptions.*`` clause
	can't even be evaluated. Replacing the whole module reference makes these tests work
	regardless of whether real ``requests`` is installed on the runner."""
	fake = types.SimpleNamespace(
		get=get_fn,
		exceptions=types.SimpleNamespace(
			RequestException=_FakeRequestException,
			ConnectionError=_FakeConnectionError,
		),
	)
	monkeypatch.setattr(client_module, "requests", fake, raising=False)
	return fake


def test_download_streams_and_joins_chunks(monkeypatch):
	client_module = _client_module()
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp([b"AB", b"", b"CD"]))
	client = client_module.QuickBooksClient(settings=object())

	assert client.download_attachable("https://x/dl") == b"ABCD"


def test_download_raises_on_http_error(monkeypatch):
	client_module = _client_module()
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp([], status=404, text="gone"))
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksAPIError) as excinfo:
		client.download_attachable("https://x/dl")
	# A 404 is a plain failure, not an expired ticket: no fresh-URI retry for it.
	assert not isinstance(excinfo.value, client_module.QuickBooksDownloadTicketError)


@pytest.mark.parametrize("status", [401, 403])
def test_download_rejected_ticket_raises_the_ticket_error(monkeypatch, status):
	# The pre-signed ticket expired (the backfill's ~139 tail 401s): its own exception
	# class, still a QuickBooksAPIError, so the mirror can re-query a fresh URI once.
	client_module = _client_module()
	_install_fake_requests(
		monkeypatch, client_module, lambda *a, **k: _FakeResp([], status=status, text="INVALID_AUTHORIZATION")
	)
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksDownloadTicketError) as excinfo:
		client.download_attachable("https://x/dl")
	assert isinstance(excinfo.value, client_module.QuickBooksAPIError)
	assert str(status) in str(excinfo.value)


def test_download_enforces_the_size_cap(monkeypatch):
	client_module = _client_module()
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp([b"x" * 10]))
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksAPIError):
		client.download_attachable("https://x/dl", max_bytes=5)


def test_download_enforces_the_total_time_budget(monkeypatch):
	client_module = _client_module()

	def _endless():
		while True:
			yield b"x"

	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp(_endless()))
	# deadline = 1000 + 10 = 1010; stays under once, then jumps past it.
	ticks = iter([1000.0, 1005.0] + [2000.0] * 100)
	monkeypatch.setattr(client_module.time, "monotonic", lambda: next(ticks))
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksAPIError):
		client.download_attachable("https://x/dl", max_seconds=10)


def test_download_wraps_request_exceptions(monkeypatch):
	# A genuine network error (incl. the per-read stall timeout) is wrapped so the
	# caller's per-file except logs it and moves on.
	client_module = _client_module()

	def _boom(*a, **k):
		raise _FakeConnectionError("reset")

	_install_fake_requests(monkeypatch, client_module, _boom)
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksAPIError):
		client.download_attachable("https://x/dl")


def test_download_propagates_non_request_exceptions(monkeypatch):
	# A control-flow timeout raised into the read (RQ's JobTimeoutException, or our own
	# _AttachmentTimeout from the SIGALRM guard) must NOT be re-wrapped as a
	# QuickBooksAPIError -- else the caller's _is_rq_job_timeout re-raise is defeated and
	# RQ's one-shot death penalty gets swallowed. This is the exact regression the review
	# caught; keep it locked down.
	client_module = _client_module()

	class _JobTimeoutLike(Exception):
		pass

	resp = _FakeResp([])

	def _raising_iter(chunk_size=None):
		raise _JobTimeoutLike("rq death penalty landed in the socket read")
		yield  # pragma: no cover  (makes this a generator)

	resp.iter_content = _raising_iter
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: resp)
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(_JobTimeoutLike):
		client.download_attachable("https://x/dl")


# --------------------------------------------------------------- save-timeout guard: it fires


@pytest.mark.skipif(not _HAS_SIGALRM, reason="SIGALRM (the guard mechanism) is Linux-only")
def test_save_time_limit_actually_interrupts_a_slow_block(monkeypatch):
	# The guard's whole point: a block that overruns is interrupted with _AttachmentTimeout.
	# This is what catches a mutation that neuters the handler (e.g. `pass` instead of raise).
	w = _wire(monkeypatch, [], mapping={})
	monkeypatch.setattr(w.attachments, "_in_background_job", lambda: False)

	with pytest.raises(w.attachments._AttachmentTimeout):
		with w.attachments._save_time_limit(1):
			deadline = time.time() + 5
			while time.time() < deadline:
				pass


@pytest.mark.skipif(not _HAS_SIGALRM, reason="SIGALRM (the guard mechanism) is Linux-only")
def test_save_time_limit_restores_handler_and_clears_timer(monkeypatch):
	w = _wire(monkeypatch, [], mapping={})
	monkeypatch.setattr(w.attachments, "_in_background_job", lambda: False)

	before = signal.getsignal(signal.SIGALRM)
	with w.attachments._save_time_limit(30):
		assert signal.getsignal(signal.SIGALRM) is not before  # our handler is armed
	assert signal.getsignal(signal.SIGALRM) is before  # ...and restored on exit
	assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)  # ...and the alarm cleared
