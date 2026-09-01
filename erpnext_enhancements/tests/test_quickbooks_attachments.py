"""Pure-Python (no Frappe site) unit tests for QBO attachment mirroring.

Reuses ``test_quickbooks_online.install_frappe_stub`` to make the QBO core importable
without a bench, then monkeypatches the client + mapping lookup so the mirror logic
(``core.attachments.sync_attachments``) runs deterministically. Covers: a mapped file is
mirrored once, re-runs skip it (idempotency), Notes/metadata-only Attachables and
unmapped entities are skipped, one Attachable linked to several entities mirrors to each,
the entity-type filter, the ``max_new`` cap, the ``start_position``/``max_scan`` resumable
chunk window (and its ``next_start``/``exhausted`` bookkeeping), that one file's failure is
counted and never aborts the run, the SIGALRM save-timeout guard's no-op conditions, and
the streamed ``download_attachable`` total-time / size caps.

The fake client is faithful to QBO's ``STARTPOSITION``/``MAXRESULTS`` paging (it parses the
query and slices a flat Attachable list) so the chunk-window math is exercised for real
rather than mocked away.
"""

import re
import signal
import time
import types

import pytest

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub

_HAS_SIGALRM = hasattr(signal, "SIGALRM")


class _FakeClient:
	"""Stand-in QuickBooksClient over a flat Attachable list, faithful to QBO paging."""

	def __init__(self, attachables, downloads=None):
		self._all = list(attachables)
		self._downloads = downloads or {}
		self.downloaded = []
		self.queries = []

	def query(self, query):
		self.queries.append(query)
		m = re.search(r"STARTPOSITION\s+(\d+)\s+MAXRESULTS\s+(\d+)", query)
		start = int(m.group(1)) - 1  # QBO STARTPOSITION is 1-based
		maxresults = int(m.group(2))
		return {"QueryResponse": {"Attachable": self._all[start : start + maxresults]}}

	def download_attachable(self, uri, **_kwargs):
		self.downloaded.append(uri)
		return self._downloads.get(uri, b"PDF-BYTES")


def _attach(att_id, refs, file_name="receipt.pdf", uri=None):
	"""Build one QBO Attachable dict. ``refs`` = list of (type, id); no file_name => a Note."""
	att = {"Id": att_id, "AttachableRef": [{"EntityRef": {"type": t, "value": v}} for t, v in refs]}
	if file_name is not None:
		att["FileName"] = file_name
		att["TempDownloadUri"] = uri or f"https://intuit.example/dl/{att_id}"
	return att


def _wire(monkeypatch, attachables, mapping, existing=None, downloads=None):
	"""Install stubs; return (attachments module, fake client, saved-File list)."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import attachments

	client = _FakeClient(attachables, downloads)
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
	# The mirror commits per file and rolls back a failed one; stub both.
	monkeypatch.setattr(frappe.db, "commit", lambda: None, raising=False)
	monkeypatch.setattr(frappe.db, "rollback", lambda *a, **k: None, raising=False)
	# sync_attachments toggles these around the run (notification mute); give them a home.
	monkeypatch.setattr(frappe, "flags", types.SimpleNamespace(in_import=False, mute_emails=False), raising=False)
	# The per-file except path logs; the stub has no logger of its own.
	monkeypatch.setattr(frappe, "get_traceback", lambda *a, **k: "traceback", raising=False)
	monkeypatch.setattr(frappe, "log_error", lambda *a, **k: None, raising=False)

	saved = []
	monkeypatch.setattr(frappe, "get_doc", lambda d: types.SimpleNamespace(insert=lambda **k: saved.append(d)), raising=False)
	return attachments, client, saved


def test_mirrors_a_mapped_file_attachment(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456")])]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["scanned"] == 1
	assert summary["exhausted"] is True
	assert client.downloaded == ["https://intuit.example/dl/A1"]
	assert len(saved) == 1
	f = saved[0]
	assert f["doctype"] == "File" and f["is_private"] == 1
	assert f["attached_to_doctype"] == "Purchase Invoice" and f["attached_to_name"] == "PINV-1"
	assert f["custom_qbo_attachable_id"] == "A1" and f["content"] == b"PDF-BYTES"


def test_rerun_skips_already_mirrored(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456")])]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	existing = {("A1", "Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping, existing=existing)

	summary = attachments.sync_attachments()

	assert summary["skipped_existing"] == 1 and summary["mirrored"] == 0
	assert client.downloaded == [] and saved == []


def test_note_only_attachable_is_skipped(monkeypatch):
	# No FileName => a text Note, nothing to download.
	attachables = [_attach("A9", [("Bill", "456")], file_name=None)]
	mapping = {("Bill", "456"): ("Purchase Invoice", "PINV-1")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments()

	assert summary["no_file"] == 1 and summary["mirrored"] == 0
	assert client.downloaded == [] and saved == []


def test_unmapped_entity_is_skipped(monkeypatch):
	attachables = [_attach("A1", [("Estimate", "999")])]
	attachments, client, saved = _wire(monkeypatch, attachables, mapping={})

	summary = attachments.sync_attachments()

	assert summary["no_mapping"] == 1 and summary["mirrored"] == 0
	assert saved == []


def test_one_attachable_mirrors_to_each_linked_doc(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456"), ("Invoice", "789")])]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 2
	assert {(f["attached_to_doctype"], f["attached_to_name"]) for f in saved} == {
		("Purchase Invoice", "PINV-1"),
		("Sales Invoice", "SINV-1"),
	}


def test_entity_types_filter_restricts_downloads(monkeypatch):
	attachables = [_attach("A1", [("Bill", "456"), ("Invoice", "789")])]
	mapping = {
		("Bill", "456"): ("Purchase Invoice", "PINV-1"),
		("Invoice", "789"): ("Sales Invoice", "SINV-1"),
	}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments(entity_types=["Bill"])

	assert summary["mirrored"] == 1
	assert [f["attached_to_doctype"] for f in saved] == ["Purchase Invoice"]


def test_max_new_caps_a_bounded_run(monkeypatch):
	attachables = [_attach("A1", [("Bill", "1")]), _attach("A2", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments(max_new=1)

	assert summary["mirrored"] == 1 and len(saved) == 1


# --------------------------------------------------------------- resumable chunk window


def test_max_scan_bounds_the_window_and_reports_next_start(monkeypatch):
	# Five mapped Attachables; a chunk of two scans exactly two and points past them.
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments(start_position=1, max_scan=2)

	assert summary["scanned"] == 2 and summary["mirrored"] == 2
	assert summary["next_start"] == 3 and summary["exhausted"] is False
	assert [f["custom_qbo_attachable_id"] for f in saved] == ["A1", "A2"]


def test_start_position_resumes_midway(monkeypatch):
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	# Resume at the 3rd Attachable, window of two => A3, A4.
	summary = attachments.sync_attachments(start_position=3, max_scan=2)

	assert [f["custom_qbo_attachable_id"] for f in saved] == ["A3", "A4"]
	assert summary["next_start"] == 5 and summary["exhausted"] is False


def test_chunk_window_reaching_the_end_sets_exhausted(monkeypatch):
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	# A window that runs past the last row must report exhaustion so the driver stops.
	summary = attachments.sync_attachments(start_position=5, max_scan=50)

	assert summary["scanned"] == 1 and summary["mirrored"] == 1
	assert summary["exhausted"] is True


def test_full_chunk_loop_covers_every_attachable_exactly_once(monkeypatch):
	# Drive the whole list in windows of two, exactly as the backfill shell loop does.
	attachables = [_attach(f"A{i}", [("Bill", str(i))]) for i in range(1, 6)]
	mapping = {("Bill", str(i)): ("Purchase Invoice", f"P{i}") for i in range(1, 6)}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	start, seen = 1, []
	for _ in range(10):  # generous bound; the loop breaks on exhaustion
		summary = attachments.sync_attachments(start_position=start, max_scan=2)
		seen.extend(f["custom_qbo_attachable_id"] for f in saved[len(seen) :])
		if summary["exhausted"]:
			break
		start = summary["next_start"]

	assert seen == ["A1", "A2", "A3", "A4", "A5"]


# --------------------------------------------------------------- failure containment


def test_one_file_failure_is_counted_and_run_continues(monkeypatch):
	attachables = [_attach("GOOD", [("Bill", "1")]), _attach("BAD", [("Bill", "2")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1"), ("Bill", "2"): ("Purchase Invoice", "P2")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	real_save = attachments._save_attachment

	def _flaky_save(content, file_name, att_id, doctype, name):
		if att_id == "BAD":
			raise RuntimeError("simulated pypdf blow-up / timeout")
		return real_save(content, file_name, att_id, doctype, name)

	monkeypatch.setattr(attachments, "_save_attachment", _flaky_save)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["errors"] == 1
	assert summary["scanned"] == 2  # the run did not abort on the bad file
	assert [f["custom_qbo_attachable_id"] for f in saved] == ["GOOD"]


def test_rq_job_timeout_is_reraised_not_swallowed(monkeypatch):
	# RQ's death penalty must propagate so RQ can fail the job; the per-file except must
	# NOT count it as a mirror error and continue (that would spend RQ's one-shot alarm
	# and leave the rest of the run untimed).
	attachables = [_attach("A1", [("Bill", "1")])]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	class JobTimeoutException(Exception):  # name-matched by _is_rq_job_timeout's fallback
		pass

	def _timeout(_uri):
		raise JobTimeoutException("rq death penalty")

	monkeypatch.setattr(client, "download_attachable", _timeout)

	with pytest.raises(JobTimeoutException):
		attachments.sync_attachments()
	assert saved == []


# --------------------------------------------------------------- over-long filenames


def test_bounded_file_name_fits_limit_and_keeps_extension(monkeypatch):
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	long_name = "A" * 200 + ".pdf"
	out = attachments._bounded_file_name(long_name)
	assert len(out) <= 140 and out.endswith(".pdf") and out.startswith("...")
	# Short names and None pass through untouched.
	assert attachments._bounded_file_name("receipt.pdf") == "receipt.pdf"
	assert attachments._bounded_file_name(None) is None


def test_over_long_filename_is_truncated_not_errored(monkeypatch):
	# QBO's long base64 filenames exceed File.file_name's Data(140) and used to raise
	# CharacterLengthExceededError on insert (~19 skips in the first backfill).
	long_name = "Z" * 180 + ".pdf"
	attachables = [_attach("A1", [("Bill", "1")], file_name=long_name)]
	mapping = {("Bill", "1"): ("Purchase Invoice", "P1")}
	attachments, client, saved = _wire(monkeypatch, attachables, mapping)

	summary = attachments.sync_attachments()

	assert summary["mirrored"] == 1 and summary["errors"] == 0
	assert len(saved[0]["file_name"]) <= 140 and saved[0]["file_name"].endswith(".pdf")


# --------------------------------------------------------------- save-timeout guard gating


def test_save_time_limit_is_a_noop_when_disabled(monkeypatch):
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)

	with attachments._save_time_limit(0):
		pass

	assert calls == []  # zero timeout => never arms the alarm


def test_save_time_limit_is_a_noop_off_the_main_thread(monkeypatch):
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)
	# Pretend we are not on the main thread (e.g. an RQ worker horse): must not touch signals.
	monkeypatch.setattr(attachments.threading, "current_thread", lambda: object())

	with attachments._save_time_limit(30):
		pass

	assert calls == []


def test_save_time_limit_is_a_noop_inside_a_background_job(monkeypatch):
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	calls = []
	monkeypatch.setattr(attachments.signal, "setitimer", lambda *a, **k: calls.append(a), raising=False)
	monkeypatch.setattr(attachments, "_in_background_job", lambda: True)

	with attachments._save_time_limit(30):
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


def _client_module():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	return client_module


def test_download_streams_and_joins_chunks(monkeypatch):
	client_module = _client_module()
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp([b"AB", b"", b"CD"]))
	client = client_module.QuickBooksClient(settings=object())

	assert client.download_attachable("https://x/dl") == b"ABCD"


def test_download_raises_on_http_error(monkeypatch):
	client_module = _client_module()
	_install_fake_requests(monkeypatch, client_module, lambda *a, **k: _FakeResp([], status=404, text="gone"))
	client = client_module.QuickBooksClient(settings=object())

	with pytest.raises(client_module.QuickBooksAPIError):
		client.download_attachable("https://x/dl")


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
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	monkeypatch.setattr(attachments, "_in_background_job", lambda: False)

	with pytest.raises(attachments._AttachmentTimeout):
		with attachments._save_time_limit(1):
			deadline = time.time() + 5
			while time.time() < deadline:
				pass


@pytest.mark.skipif(not _HAS_SIGALRM, reason="SIGALRM (the guard mechanism) is Linux-only")
def test_save_time_limit_restores_handler_and_clears_timer(monkeypatch):
	attachments, _client, _saved = _wire(monkeypatch, [], mapping={})
	monkeypatch.setattr(attachments, "_in_background_job", lambda: False)

	before = signal.getsignal(signal.SIGALRM)
	with attachments._save_time_limit(30):
		assert signal.getsignal(signal.SIGALRM) is not before  # our handler is armed
	assert signal.getsignal(signal.SIGALRM) is before  # ...and restored on exit
	assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)  # ...and the alarm cleared
