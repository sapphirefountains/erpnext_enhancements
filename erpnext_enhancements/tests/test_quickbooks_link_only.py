"""Bench-free tests for "QuickBooks sync links, never overwrites" (v1.800.0).

Nik's decision of 2026-10-06 has two halves, and this file pins both.

**No self-starting imports.** ``sync.retry_failed`` used to end by running a full Import All
whenever a failed Import All or Run Resync log existed. The hourly scheduler called it on the
300-second default queue, where a full import cannot finish, so every run failed and earned
the next one: 2 to 6 full imports a day. What is held here: ``retry_failed`` never calls
``import_all`` (by behaviour, and by reading the module for any other caller), a named import
log answers "start it again by hand" without queuing anything, and RQ's job timeout is no
longer swallowed by the per-record ``except Exception`` -- the swallow that left a run with no
time limit and a Sync Log stuck in Running.

**A linked record is never saved by the sync.** The already-linked path used to apply every
mapped value and save. For a QuickBooks job linked to a Project that is status, customer and
company; one import reopened 338 Projects and re-pointed 13 to another company. What is held
here: a linked Project whose job moved to another customer is not saved and its mapping is
flagged, and its status and company are never even offered; an unmoved payload writes nothing;
a moved payload that still agrees only advances the cursor; a field QuickBooks did not change
is not a difference however far ERPNext moved; a linked draft transaction is flagged, not
saved; a submitted document still takes its own path; ``overwrite=True``, ``recheck=True`` and
preview change none of that; the flag is written with ``frappe.db.set_value`` and never touches
``owned_fields``; and the three answers a person can give -- Keep ERPNext, Use QuickBooks
values, Refresh draft -- do exactly what their names say.

The source checks at the bottom exist because the bench-free stub cannot see a decorator or a
missing CI step: an endpoint that lost its role gate, a review action reachable by GET, and a
pytest file that ``python -m unittest`` silently collects nothing from all pass every
behavioural test above.

Plain pytest functions (``monkeypatch``), so this file needs its own ``python -m pytest`` step
in ``ci.yml``.

Run: python -m pytest erpnext_enhancements/tests/test_quickbooks_link_only.py -q
"""

import ast
import json
import re
import types
from pathlib import Path

import pytest

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
CORE = APP / "quickbooks_online" / "core"
PAGE_JS = APP / "quickbooks_online" / "page" / "quickbooks_record_matching" / "quickbooks_record_matching.js"
MAPPING_JSON = (
	APP / "quickbooks_online" / "doctype" / "quickbooks_sync_mapping" / "quickbooks_sync_mapping.json"
)

REVIEW_ENDPOINTS = ("keep_erpnext", "use_quickbooks_values", "refresh_drafts")
SETTINGS = types.SimpleNamespace(company="Main Co")


def _read(path):
	return path.read_text(encoding="utf-8")


def _function_source(path, name):
	source = _read(path)
	node = next(
		node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == name
	)
	return ast.get_source_segment(source, node)


def _code_only(source):
	"""``source`` with its docstring and comments removed, so an absence check reads the
	code and not the prose that explains what the code used to do."""
	tree = ast.parse(source)
	for node in ast.walk(tree):
		if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node) is not None:
			node.body = node.body[1:] or [ast.Pass()]
	return ast.unparse(tree)


class JobTimeoutException(Exception):
	"""Stand-in for ``rq.timeouts.JobTimeoutException``; ``is_rq_job_timeout`` matches the
	class by name when ``rq`` is not installed, which is the case in CI."""


def _doc(**fields):
	"""A record double that remembers every ``set`` and every ``save``."""
	doc = types.SimpleNamespace(flags=types.SimpleNamespace(), saves=[], sets=[], **fields)
	doc.get = lambda fieldname: getattr(doc, fieldname, None)

	def _set(fieldname, value):
		doc.sets.append(fieldname)
		setattr(doc, fieldname, value)

	doc.set = _set
	doc.save = lambda **kwargs: doc.saves.append(kwargs)
	return doc


def _mapping_row(**overrides):
	row = {
		"name": "QBO-MAP-Customer-77",
		"erpnext_doctype": "Project",
		"erpnext_name": "PRJ-00123",
		"owned_fields": "{}",
		"match_status": "Auto Matched",
		"conflict_status": "Clean",
		"sync_token": "5",
		"last_qbo_updated_at": None,
		"qbo_changes": None,
		"settled_qbo_values": None,
		"deleted": 0,
	}
	row.update(overrides)
	return types.SimpleNamespace(**row)


def _replay(row, writes):
	"""Carry the mapping writes of the pass just run onto the mapping double, as the
	database would, so the next pass reads what this one stored."""
	for (_doctype, _name, updates), _kwargs in writes["set_value"]:
		for fieldname, value in updates.items():
			setattr(row, fieldname, value)
	writes["set_value"].clear()


def _meta(*fieldnames):
	"""A DocType meta double that knows exactly these fields."""
	return types.SimpleNamespace(has_field=lambda fieldname: fieldname in fieldnames)


def _wire(monkeypatch, *, doctype, values, mapping_row, doc, issues=None):
	"""Point ``upsert_entity`` at one linked record and record every write it attempts."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	writes = {"set_value": [], "save_mapping": [], "manual_review": [], "group_parent": []}
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: (doctype, dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: list(issues or []))
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: mapping_row)
	monkeypatch.setattr(mapping, "_ensure_group_parent", lambda *args: writes["group_parent"].append(args))
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: writes["save_mapping"].append(kwargs))
	monkeypatch.setattr(
		mapping, "save_manual_review_mapping", lambda *args, **kwargs: writes["manual_review"].append(args)
	)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe.db, "set_value", lambda *args, **kwargs: writes["set_value"].append((args, kwargs)), raising=False
	)
	return mapping, frappe, writes


def _project_case(monkeypatch, **mapping_overrides):
	"""A Project a person finished, linked to a QuickBooks job that someone then moved under
	another customer. The mapper also sends what it always sends for a job: its DisplayName
	as the title, the first open-like status, and the Settings company."""
	values = {
		"project_name": "PRJ-00123 Renamed In QuickBooks",
		"customer": "Other Customer",
		"company": "Other Company",
		"status": "Open",
	}
	doc = _doc(
		doctype="Project",
		name="PRJ-00123",
		docstatus=0,
		project_name="Courtyard Fountain",
		customer="First Customer",
		company="Main Co",
		status="Completed",
	)
	mapping_overrides.setdefault(
		"owned_fields", json.dumps({"customer": "First Customer", "company": "Main Co", "status": "Open"})
	)
	row = _mapping_row(**mapping_overrides)
	mapping, frappe, writes = _wire(monkeypatch, doctype="Project", values=values, mapping_row=row, doc=doc)
	return mapping, frappe, writes, doc, row


JOB_PAYLOAD = {"Id": "77", "DisplayName": "PRJ-00123 Renamed In QuickBooks", "SyncToken": "6"}


# ---------------------------------------------------------------------------
# Part A -- no self-starting imports
# ---------------------------------------------------------------------------


def _retry_stub(monkeypatch, failed):
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	saved = []

	def get_all(doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if filters.get("status") != "Failed":
			return []  # the reaper's Running/Queued query
		return [log for log in failed if not filters.get("name") or log.name == filters["name"]]

	def get_doc(doctype, name):
		doc = types.SimpleNamespace(name=name, retry_count=0)
		doc.save = lambda **kwargs: saved.append(name)
		return doc

	monkeypatch.setattr(frappe, "get_all", get_all)
	monkeypatch.setattr(frappe, "get_doc", get_doc, raising=False)
	monkeypatch.setattr(sync, "get_settings", lambda: types.SimpleNamespace(retry_limit=3))
	calls = {"cdc": 0}
	monkeypatch.setattr(sync, "run_cdc", lambda: calls.__setitem__("cdc", calls["cdc"] + 1))

	def no_import(*args, **kwargs):
		raise AssertionError("retry_failed started an Import All")

	monkeypatch.setattr(sync, "import_all", no_import)
	monkeypatch.setattr(sync, "run_resync", no_import)
	return sync, calls, saved


@pytest.mark.parametrize("sync_type", ["Import All", "Run Resync", "Preview Resync"])
def test_retry_failed_never_starts_an_import(monkeypatch, sync_type):
	"""The scheduler path with nothing but a failed full run on the books: it used to end
	in ``import_all()``. Now nothing runs, and the log is not even counted as retried."""
	failed = [types.SimpleNamespace(name="QBO-SYNC-1", sync_type=sync_type, retry_count=0)]
	sync, calls, saved = _retry_stub(monkeypatch, failed)

	result = sync.retry_failed()

	assert result == {"cdc": False, "restart_by_hand": ["QBO-SYNC-1"]}
	assert calls == {"cdc": 0}
	assert saved == [], "no retry is attempted, so none is counted"


def test_retry_failed_still_retries_cdc_once_beside_a_failed_import(monkeypatch):
	failed = [
		types.SimpleNamespace(name="QBO-SYNC-1", sync_type="Import All", retry_count=0),
		types.SimpleNamespace(name="QBO-SYNC-2", sync_type="CDC", retry_count=0),
		types.SimpleNamespace(name="QBO-SYNC-3", sync_type="CDC", retry_count=1),
	]
	sync, calls, saved = _retry_stub(monkeypatch, failed)

	result = sync.retry_failed()

	assert calls == {"cdc": 1}
	assert saved == ["QBO-SYNC-2", "QBO-SYNC-3"]
	assert result == {"cdc": True, "restart_by_hand": ["QBO-SYNC-1"]}


def test_retry_failed_for_a_named_import_log_runs_nothing(monkeypatch):
	"""The dashboard's Retry for one named log: CDC still works by name, an import does not."""
	failed = [
		types.SimpleNamespace(name="QBO-SYNC-1", sync_type="Import All", retry_count=0),
		types.SimpleNamespace(name="QBO-SYNC-2", sync_type="CDC", retry_count=0),
	]
	sync, calls, saved = _retry_stub(monkeypatch, failed)

	assert sync.retry_failed("QBO-SYNC-1") == {"cdc": False, "restart_by_hand": ["QBO-SYNC-1"]}
	assert calls == {"cdc": 0} and saved == []

	assert sync.retry_failed("QBO-SYNC-2") == {"cdc": True, "restart_by_hand": []}
	assert calls == {"cdc": 1} and saved == ["QBO-SYNC-2"]


def test_only_the_dashboard_endpoint_can_start_an_import():
	"""Read the code, not the behaviour: a new caller added next year would pass every test
	above. In ``sync.py`` nothing calls ``import_all`` or ``run_resync``; ``tasks.py`` (the
	scheduler's entry points) and ``webhooks.py`` never name them; and no scheduler entry in
	``hooks.py`` points at either."""
	for module in ("sync.py", "tasks.py", "webhooks.py"):
		tree = ast.parse(_read(CORE / module))
		called = {
			node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
		}
		assert not called & {"import_all", "run_resync", "run_import_all", "run_run_resync"}, module
	tasks = _code_only(_read(CORE / "tasks.py"))
	assert "import_all" not in tasks and "run_resync" not in tasks
	hooks = _read(APP / "hooks.py")
	assert "quickbooks_online.core.sync.import_all" not in hooks
	assert "quickbooks_online.core.sync.run_resync" not in hooks
	assert "quickbooks_online.core.api.import_all" not in hooks
	# The one remaining caller is the operator-gated endpoint, which queues it as its own job.
	endpoint = _function_source(CORE / "api.py", "import_all")
	assert "_require_qbo_operator()" in endpoint
	assert 'frappe.enqueue(run_import_all, queue="long"' in endpoint


def _api(monkeypatch, failed):
	frappe = install_frappe_stub()
	enqueued = []
	monkeypatch.setattr(frappe, "only_for", lambda *args, **kwargs: None, raising=False)
	monkeypatch.setattr(frappe, "enqueue", lambda method, **kwargs: enqueued.append((method, kwargs)), raising=False)

	def get_all(doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		types_wanted = set((filters.get("sync_type") or ["in", []])[1])
		return [
			log
			for log in failed
			if log.sync_type in types_wanted and (not filters.get("name") or log.name == filters["name"])
		]

	monkeypatch.setattr(frappe, "get_all", get_all)
	from erpnext_enhancements.quickbooks_online.core import api

	return api, enqueued


def test_retry_endpoint_tells_a_named_import_log_to_be_restarted_by_hand(monkeypatch):
	failed = [
		types.SimpleNamespace(name="QBO-SYNC-1", sync_type="Import All"),
		types.SimpleNamespace(name="QBO-SYNC-2", sync_type="CDC"),
	]
	api, enqueued = _api(monkeypatch, failed)

	assert api.retry_failed("QBO-SYNC-1") == {"status": "restart_by_hand", "restart_by_hand": ["QBO-SYNC-1"]}
	assert enqueued == [], "nothing is queued for a failed import"

	assert api.retry_failed("QBO-SYNC-2") == {"status": "queued", "restart_by_hand": []}
	assert api.retry_failed() == {"status": "queued", "restart_by_hand": ["QBO-SYNC-1"]}
	assert [call[0] for call in enqueued] == [api.run_retry_failed, api.run_retry_failed]
	assert enqueued[0][1]["log_name"] == "QBO-SYNC-2" and enqueued[1][1]["log_name"] is None


def test_safe_upsert_lets_the_job_timeout_through_after_rolling_back(monkeypatch):
	"""RQ's timeout is an ``Exception`` raised from a one-shot alarm. Caught as one more bad
	record, it leaves the run with no time limit; re-raised, the run wrapper fails the log."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	rollbacks = []
	monkeypatch.setattr(frappe.db, "rollback", lambda save_point=None: rollbacks.append(save_point), raising=False)
	logged = []
	monkeypatch.setattr(frappe, "log_error", lambda *args, **kwargs: logged.append(args), raising=False)

	def times_out(*args, **kwargs):
		raise JobTimeoutException("Task exceeded maximum timeout value (300 seconds)")

	monkeypatch.setattr(sync, "upsert_entity", times_out)
	with pytest.raises(JobTimeoutException):
		sync.safe_upsert("Customer", {"Id": "1"}, SETTINGS)
	assert rollbacks == ["qbo_upsert"], "the half-written record is undone before the timeout travels on"
	assert logged == [], "a timeout is not a failed record"

	# The retry after a TimestampMismatchError is the same door.
	calls = {"n": 0}

	def mismatch_then_timeout(*args, **kwargs):
		calls["n"] += 1
		if calls["n"] == 1:
			raise frappe.exceptions.TimestampMismatchError("modified after open")
		raise JobTimeoutException("300 seconds")

	monkeypatch.setattr(sync, "upsert_entity", mismatch_then_timeout)
	with pytest.raises(JobTimeoutException):
		sync.safe_upsert("Customer", {"Id": "1"}, SETTINGS)

	# An ordinary failure is still one failed record, and the batch carries on.
	monkeypatch.setattr(sync, "upsert_entity", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad record")))
	assert sync.safe_upsert("Customer", {"Id": "9"}, SETTINGS)["action"] == "failed"
	assert len(logged) == 1


def test_a_timed_out_import_ends_failed_not_running(monkeypatch):
	"""The whole chain: the timeout lands inside one record's upsert, leaves ``safe_upsert``,
	and the run wrapper's ``fail_log`` closes the log as Failed before re-raising."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	log = types.SimpleNamespace(name="QBO-SYNC-9", status="Running", failed_count=0)
	log.save = lambda **kwargs: None
	failed_with = []
	monkeypatch.setattr(sync, "get_settings", lambda: types.SimpleNamespace(realm_id="1", save=lambda **k: None))
	monkeypatch.setattr(sync, "run_in_progress", lambda sync_type: False)
	monkeypatch.setattr(sync, "start_log", lambda sync_type: log)
	monkeypatch.setattr(sync, "ordered_entities", lambda entity_types=None: ["Customer"])
	monkeypatch.setattr(sync, "query_entity_payloads", lambda entity_type, settings=None: iter([{"Id": "1"}]))
	monkeypatch.setattr(sync, "store_raw_payload", lambda *args, **kwargs: None)
	monkeypatch.setattr(sync.frappe.db, "commit", lambda: None, raising=False)

	def times_out(*args, **kwargs):
		raise JobTimeoutException("300 seconds")

	monkeypatch.setattr(sync, "upsert_entity", times_out)
	monkeypatch.setattr(
		sync, "fail_log", lambda log, exc: (setattr(log, "status", "Failed"), failed_with.append(type(exc).__name__))
	)

	with pytest.raises(JobTimeoutException):
		sync.import_all()
	assert log.status == "Failed"
	assert failed_with == ["JobTimeoutException"]


def test_is_rq_job_timeout_matches_by_name_and_nothing_else():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.utils import is_rq_job_timeout

	assert is_rq_job_timeout(JobTimeoutException("x")) is True
	assert is_rq_job_timeout(ValueError("x")) is False
	assert is_rq_job_timeout(TimeoutError("a socket timeout is an ordinary failed record")) is False


# ---------------------------------------------------------------------------
# Part B -- the sync's half: compare, flag, never save
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [{}, {"overwrite": True}, {"recheck": True}, {"overwrite": True, "recheck": True}])
def test_a_linked_project_whose_job_changed_is_flagged_and_never_saved(monkeypatch, flags):
	"""The case the rule was written for. QuickBooks moved the job under another customer,
	and the mapper sends "Open" and the Settings company as it does for every job; the
	Project is Completed here, under its own company. Nothing reaches the Project --
	whatever the caller passes: the ``overwrite=True`` Run Resync used to send, and the
	``recheck=True`` it sends now."""
	mapping, _frappe, writes, doc, row = _project_case(monkeypatch)

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, **flags)

	assert result["action"] == "flagged"
	assert result["doctype"] == "Project" and result["name"] == "PRJ-00123"
	# The customer is the one thing a job can say about a linked Project. Its title, status
	# and company are dropped before the comparison: never flagged, so never applicable.
	assert result["fields"] == ["customer"]
	assert doc.saves == [] and doc.sets == []
	assert (doc.customer, doc.company, doc.status, doc.project_name) == (
		"First Customer",
		"Main Co",
		"Completed",
		"Courtyard Fountain",
	)
	assert writes["save_mapping"] == [] and writes["manual_review"] == []
	assert writes["group_parent"] == [], "a linked record never promotes another Account"

	assert len(writes["set_value"]) == 1
	(doctype, name, updates), _kwargs = writes["set_value"][0]
	assert (doctype, name) == ("QuickBooks Sync Mapping", row.name)
	assert updates["conflict_status"] == "QuickBooks Changed"
	# The submitted-document guard's pattern: owned_fields and the cursor are left alone.
	assert set(updates) == {"conflict_status", "qbo_changes"}
	note = json.loads(updates["qbo_changes"])
	assert note["sync_token"] == "6"
	assert note["fields"] == {"customer": {"erpnext": "First Customer", "qbo": "Other Customer"}}


def test_a_projects_status_company_and_title_are_never_a_difference(monkeypatch):
	"""The job still sits under the Project's own customer. That the Project is Completed
	under another company with a curated title, while the mapper says Open, the Settings
	company and the job's DisplayName, is not QuickBooks changing anything: ``unchanged``,
	with a fresh baseline or none. This is the write that reopened 338 Projects."""
	for owned in ({"customer": "First Customer", "status": "Open", "company": "Main Co"}, {}):
		mapping, _frappe, writes, doc, _row = _project_case(monkeypatch, owned_fields=json.dumps(owned))
		monkeypatch.setattr(
			mapping,
			"map_qbo_to_erpnext",
			lambda *args: (
				"Project",
				{
					"project_name": "PRJ-00123 Renamed In QuickBooks",
					"customer": "First Customer",
					"company": "Other Company",
					"status": "Open",
				},
			),
		)

		result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, recheck=True)

		assert result == {"action": "unchanged", "doctype": "Project", "name": "PRJ-00123"}
		assert doc.saves == [] and doc.sets == []
		assert (doc.status, doc.company, doc.project_name) == ("Completed", "Main Co", "Courtyard Fountain")
		assert all("conflict_status" not in call[0][2] for call in writes["set_value"])


def test_a_field_quickbooks_did_not_change_is_not_a_difference(monkeypatch):
	"""The flag is "QuickBooks changed". A person renamed the customer here and QuickBooks
	still says what it said at the last sync: not flagged, however far apart the two are.
	QuickBooks then changes the payment terms as well, a field ERPNext had also edited:
	flagged, for that field alone, with the note that both sides moved."""
	synced = {"customer_name": "Acme Supply", "payment_terms": "Net 30", "website": "https://www.example.com"}
	values = {"customer_name": "Acme Supply", "payment_terms": "Net 30", "website": "www.example.com"}
	doc = _doc(
		doctype="Customer",
		name="Acme Supply",
		customer_name="Acme Supply Company (renamed here)",
		payment_terms="Net 45",
		website="https://www.example.com",
	)
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps(synced),
		sync_token="2",
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)
	assert result["action"] == "unchanged", "QuickBooks moved, but changed none of these fields"
	assert doc.saves == [] and doc.sets == []
	updates = writes["set_value"][0][0][2]
	assert "conflict_status" not in updates and "settled_qbo_values" not in updates

	values["payment_terms"] = "Net 15"
	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "4"}, SETTINGS)
	assert result["action"] == "flagged" and result["fields"] == ["payment_terms"]
	note = json.loads(writes["set_value"][1][0][2]["qbo_changes"])
	assert note["fields"] == {"payment_terms": {"erpnext": "Net 45", "qbo": "Net 15", "erpnext_edited": True}}
	assert doc.payment_terms == "Net 45"


def test_a_change_back_is_flagged_because_the_reference_follows_quickbooks(monkeypatch):
	"""The lost change, reproduced. The last sync stored Net 30. A person sets ERPNext to
	Net 45. QuickBooks moves to Net 45 too: they agree, the cursor advances -- and the
	reference must move to Net 45 with it. QuickBooks then goes back to Net 30. Held against
	the stale reference that read as "QuickBooks did not change it" and nothing was flagged,
	with ERPNext left on Net 45."""
	values = {"customer_name": "Acme Supply", "payment_terms": "Net 45"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply", payment_terms="Net 45")
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps({"customer_name": "Acme Supply", "payment_terms": "Net 30"}),
		sync_token="2",
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)
	assert result["action"] == "unchanged"
	updates = writes["set_value"][0][0][2]
	assert updates["sync_token"] == "3"
	assert json.loads(updates["settled_qbo_values"]) == {"payment_terms": "Net 45"}
	assert "owned_fields" not in updates
	_replay(row, writes)

	values["payment_terms"] = "Net 30"
	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "4"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["payment_terms"]
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert note["fields"]["payment_terms"]["erpnext"] == "Net 45"
	assert note["fields"]["payment_terms"]["qbo"] == "Net 30"
	assert doc.payment_terms == "Net 45" and doc.saves == []


def test_a_field_quickbooks_cleared_is_flagged_and_one_that_never_had_a_value_is_not(monkeypatch):
	"""A mapped None is QuickBooks saying nothing. Where the reference held a value that is
	a change -- the payment term was removed, or its reference no longer resolves -- and it
	used to be skipped while the cursor advanced. Where there never was a value it is still
	skipped: the mapper sends None for every field QuickBooks does not fill."""
	values = {"customer_name": "Acme Supply", "payment_terms": None, "tax_id": None, "website": None}
	doc = _doc(
		doctype="Customer",
		name="Acme Supply",
		customer_name="Acme Supply",
		payment_terms="Net 30",
		tax_id="99-0000001",  # typed in ERPNext; QuickBooks never sent one
		website="",
	)
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps({"customer_name": "Acme Supply", "payment_terms": "Net 30", "website": "x.example"}),
		sync_token="2",
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["payment_terms"]
	updates = writes["set_value"][0][0][2]
	note = json.loads(updates["qbo_changes"])
	assert note["fields"] == {"payment_terms": {"erpnext": "Net 30", "qbo": None, "cleared": True}}
	# The website was cleared on both sides: nothing to review, and its reference moves on.
	assert json.loads(updates["settled_qbo_values"]) == {"website": None}
	assert doc.saves == [] and doc.payment_terms == "Net 30"


def test_a_field_with_no_baseline_is_compared_with_the_record(monkeypatch):
	"""A record the import linked to (not created) has a baseline only for the fields that
	agreed at the time. A field that differed then has no reference, so the first pass that
	looks at it shows it to a person instead of writing it, as it used to -- marked
	``no_baseline``, because QuickBooks may not have changed it at all."""
	values = {"customer_name": "Acme Supply", "tax_id": "99-0000001"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply", tax_id="")
	row = _mapping_row(
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps({"customer_name": "Acme Supply"}),
		sync_token="2",
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["tax_id"]
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert note["fields"] == {"tax_id": {"erpnext": "", "qbo": "99-0000001", "no_baseline": True}}
	assert doc.saves == [] and doc.tax_id == ""
	assert writes["save_mapping"] == []


@pytest.mark.parametrize(
	"entity_type, doctype, mapped, held",
	[
		(
			"Item",
			"Item",
			{"item_code": "NOZ-2", "item_name": "Nozzle", "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0},
			{"item_name": "Nozzle", "item_group": "Nozzles", "stock_uom": "Each", "is_stock_item": 1},
		),
		(
			"Customer",
			"Customer",
			{"customer_name": "Acme Supply", "customer_type": "Individual"},
			{"customer_name": "Acme Supply", "customer_type": "Partnership"},
		),
		(
			"Vendor",
			"Supplier",
			{"supplier_name": "Acme Steel", "supplier_type": "Individual"},
			{"supplier_name": "Acme Steel", "supplier_type": "Company"},
		),
		(
			"TaxCode",
			"Account",
			{
				"account_name": "Sales Tax",
				"company": "Main Co",
				"parent_account": "Liabilities - MC",
				"is_group": 0,
				"root_type": "Liability",
				"account_type": "Tax",
			},
			{"account_name": "Sales Tax", "parent_account": "Duties and Taxes - MC", "root_type": "Liability", "account_type": ""},
		),
		(
			"PaymentMethod",
			"Mode of Payment",
			{"mode_of_payment": "Check (renamed)", "enabled": 1, "type": "Cash"},
			{"mode_of_payment": "Check", "enabled": 0, "type": "Cash"},
		),
		(
			"Term",
			"Payment Terms Template",
			{"template_name": "Net 30 (renamed)"},
			{"template_name": "Net 30"},
		),
		("Class", "Cost Center", {"cost_center_name": "Service", "company": "Main Co"}, {"cost_center_name": "Service", "company": "Other Co"}),
		("Account", "Account", {"account_name": "Tools", "company": "Main Co"}, {"account_name": "Tools", "company": "Other Co"}),
	],
)
def test_mapper_constants_and_guesses_are_never_a_difference(monkeypatch, entity_type, doctype, mapped, held):
	"""What a mapper supplies so that a NEW record can be inserted is not QuickBooks data
	about an existing one. With no baseline (a record the import linked to), each of these
	flagged on the first QuickBooks touch, and one "Use QuickBooks values" would have
	re-filed an Item under All Item Groups and tried to make a stock item non-stock."""
	doc = _doc(doctype=doctype, name="REC-1", docstatus=0, **held)
	row = _mapping_row(erpnext_doctype=doctype, erpnext_name="REC-1", owned_fields="{}", sync_token="2")
	mapping, _frappe, writes = _wire(monkeypatch, doctype=doctype, values=mapped, mapping_row=row, doc=doc)

	result = mapping.upsert_entity(entity_type, {"Id": "1", "SyncToken": "3"}, SETTINGS, recheck=True)

	assert result["action"] == "unchanged", result
	assert doc.saves == [] and doc.sets == []
	assert all("conflict_status" not in call[0][2] for call in writes["set_value"])
	owned_by_erpnext = mapping.MAPPER_DEFAULTS_ERPNEXT_OWNS[(entity_type, doctype)]
	settled = json.loads((writes["set_value"][0][0][2] or {}).get("settled_qbo_values") or "{}")
	assert not set(settled) & set(owned_by_erpnext), "a stripped field is never even a reference"


@pytest.mark.parametrize("doctype, parent_field", [("Account", "parent_account"), ("Cost Center", "parent_cost_center")])
def test_a_group_with_children_is_never_told_it_is_a_ledger(monkeypatch, doctype, parent_field):
	"""``is_group`` comes from ``_qbo_has_children``, which only a full query sets, so a CDC
	payload of a parent always reads as a leaf with an Account Type. On a group that has
	children in ERPNext neither is a difference."""
	entity_type = "Account" if doctype == "Account" else "Class"
	values = {"is_group": 0, "account_type": "Bank"} if doctype == "Account" else {"is_group": 0}
	doc = _doc(doctype=doctype, name="Parent - MC", is_group=1, account_type=None)
	row = _mapping_row(
		erpnext_doctype=doctype,
		erpnext_name="Parent - MC",
		owned_fields=json.dumps({"is_group": 1, "account_type": None}),
		sync_token="2",
	)
	mapping, frappe, writes = _wire(monkeypatch, doctype=doctype, values=values, mapping_row=row, doc=doc)
	asked = []

	def exists(target, name=None):
		asked.append((target, name))
		return True

	monkeypatch.setattr(frappe.db, "exists", exists, raising=False)

	result = mapping.upsert_entity(entity_type, {"Id": "1", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "unchanged"
	assert (doctype, {parent_field: "Parent - MC"}) in asked
	assert all("conflict_status" not in call[0][2] for call in writes["set_value"])


def test_an_unmoved_payload_is_unchanged_with_no_write_of_any_kind(monkeypatch):
	"""QuickBooks has not advanced since the mapping's last sync. That ERPNext differs is a
	person's edit, and not the sync's business: no save, no flag, no mapping write."""
	mapping, _frappe, writes, doc, _row = _project_case(monkeypatch, sync_token="6")

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)

	assert result == {"action": "unchanged", "doctype": "Project", "name": "PRJ-00123"}
	assert doc.saves == [] and doc.sets == []
	assert writes == {"set_value": [], "save_mapping": [], "manual_review": [], "group_parent": []}


def test_a_resync_compares_an_unmoved_record_and_still_only_flags(monkeypatch):
	"""Run Resync is a person asking for the full comparison, so it looks even when
	QuickBooks has not moved -- the way to surface a standing difference after a mapper fix.
	It flags; it does not save. ``overwrite=True`` alone, what Run Resync sent before
	v1.800.0, is ignored: the unmoved record is simply unchanged."""
	mapping, _frappe, writes, doc, _row = _project_case(monkeypatch, sync_token="6")

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, overwrite=True)
	assert result["action"] == "unchanged"
	assert writes["set_value"] == []

	preview = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, preview=True, recheck=True)
	assert preview["action"] == "would_flag" and preview["fields"] == ["customer"]
	assert writes["set_value"] == []

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, recheck=True)
	assert result["action"] == "flagged"
	assert doc.saves == [] and doc.sets == []
	assert writes["save_mapping"] == []
	assert writes["set_value"][0][0][2]["conflict_status"] == "QuickBooks Changed"


def test_a_resync_of_an_unmoved_agreeing_record_writes_nothing(monkeypatch):
	values = {"customer_name": "Acme Supply"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply")
	row = _mapping_row(erpnext_doctype="Customer", erpnext_name="Acme Supply", sync_token="3")
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS, recheck=True)

	assert result == {"action": "unchanged", "doctype": "Customer", "name": "Acme Supply"}
	assert writes["set_value"] == [] and doc.saves == []


def test_the_resync_asks_for_a_recheck_and_never_for_an_overwrite():
	"""Preview and run must compare the same way, or the preview's count is not what the
	run flags. Neither passes ``overwrite`` any more."""
	sync_code = _code_only(_read(CORE / "sync.py"))
	assert "overwrite" not in sync_code
	assert "safe_upsert(raw.qbo_entity_type, payload, settings, recheck=True)" in sync_code
	assert "safe_upsert(entity_type, payload, settings, preview=True, recheck=True)" in sync_code
	# Import All, CDC and the single-entity sync never recheck: an unmoved record is left alone.
	assert sync_code.count("recheck=True") == 2


def test_a_moved_payload_that_still_agrees_only_advances_the_cursor(monkeypatch):
	values = {"customer_name": "Acme Supply", "customer_type": "Commercial", "disabled": 0}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply ", customer_type="Commercial")
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps(values),
		sync_token="2",
	)
	mapping, _frappe, writes, = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)

	assert result == {"action": "unchanged", "doctype": "Customer", "name": "Acme Supply"}
	assert doc.saves == [] and doc.sets == []
	assert writes["save_mapping"] == []
	(_doctype, _name, updates), kwargs = writes["set_value"][0]
	assert updates["sync_token"] == "3" and updates["deleted"] == 0
	assert "conflict_status" not in updates and "owned_fields" not in updates and "qbo_changes" not in updates
	assert kwargs == {"update_modified": False}


def test_a_linked_draft_transaction_is_flagged_not_saved(monkeypatch):
	"""Drafts are covered: a docstatus 0 Sales Invoice the import created is the accountant's
	to edit, so a QuickBooks edit to the invoice is shown beside it, row by row."""
	values = {
		"customer": "Acme Supply",
		"posting_date": "2026-11-02",
		"items": [
			{"item_code": "SERVICE", "qty": 1, "rate": 120.0},
			{"item_code": "PARTS", "qty": 2, "rate": 15.0},
		],
	}
	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00042",
		docstatus=0,
		customer="Acme Supply",
		posting_date="2026-11-02",
		items=[types.SimpleNamespace(item_code="SERVICE", qty=1.0, rate=100.0)],
	)
	row = _mapping_row(
		name="QBO-MAP-Invoice-900",
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		match_status="Created",
		sync_token="0",
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Sales Invoice", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "1"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["items"]
	assert doc.saves == [] and doc.sets == []
	assert len(doc.items) == 1, "the draft's rows are untouched"
	assert writes["save_mapping"] == [] and writes["manual_review"] == []
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	table = note["tables"]["items"]
	assert (table["erpnext_rows"], table["qbo_rows"]) == (1, 2)
	assert table["rows"] == [
		{"row": 1, "fields": {"rate": {"erpnext": 100.0, "qbo": 120.0}}},
		{"row": 2, "only_in": "qbo"},
	]
	assert table["qbo"] == values["items"], "the whole QuickBooks table is kept for the reviewer's decision"
	# Records the import itself created follow the same rule.
	assert row.match_status == "Created"


def test_a_submitted_document_still_takes_the_submitted_document_path(monkeypatch):
	"""docstatus 1 is the older guard and keeps its own outcome: parked for manual review by
	set_value, never ``QuickBooks Changed``, never saved. Since v1.800.0 it also wins over a
	failing preflight, which used to rewrite ``owned_fields`` on a posted document."""
	values = {"customer": "Other Customer", "posting_date": "2026-11-02"}
	doc = _doc(doctype="Sales Invoice", name="ACC-SINV-2026-00007", docstatus=1, customer="Acme Supply")
	row = _mapping_row(erpnext_doctype="Sales Invoice", erpnext_name=doc.name, name="QBO-MAP-Invoice-7")
	mapping, _frappe, writes = _wire(
		monkeypatch,
		doctype="Sales Invoice",
		values=values,
		mapping_row=row,
		doc=doc,
		issues=["Customer reference is not linked"],
	)

	result = mapping.upsert_entity("Invoice", {"Id": "7", "SyncToken": "6"}, SETTINGS)

	assert result["action"] == "manual_review" and "submitted" in result["reason"]
	assert doc.saves == [] and doc.sets == []
	assert writes["save_mapping"] == [] and writes["manual_review"] == []
	(_doctype, _name, updates), kwargs = writes["set_value"][0]
	assert updates == {"conflict_status": "Pending Review", "match_status": "Pending Review"}
	assert kwargs == {"update_modified": False}

	# Unmoved: nothing to do, nothing written.
	writes["set_value"].clear()
	result = mapping.upsert_entity("Invoice", {"Id": "7", "SyncToken": "5"}, SETTINGS)
	assert result == {"action": "unchanged", "doctype": "Sales Invoice", "name": doc.name}
	assert writes["set_value"] == [] and writes["manual_review"] == []


def test_preview_reports_would_flag_and_writes_nothing(monkeypatch):
	mapping, _frappe, writes, doc, _row = _project_case(monkeypatch)

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS, preview=True)

	assert result["action"] == "would_flag"
	assert result["fields"] == ["customer"]
	assert doc.saves == [] and doc.sets == []
	assert writes == {"set_value": [], "save_mapping": [], "manual_review": [], "group_parent": []}


def test_preview_of_an_agreeing_record_does_not_advance_the_cursor(monkeypatch):
	values = {"customer_name": "Acme Supply"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply")
	row = _mapping_row(erpnext_doctype="Customer", erpnext_name="Acme Supply", sync_token="2")
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS, preview=True)

	assert result["action"] == "unchanged"
	assert writes["set_value"] == []


def test_a_replay_of_the_same_payload_does_not_rewrite_the_flag(monkeypatch):
	"""An unreviewed row is polled every hour. The cursor is held back on purpose, so the
	payload reads as moved each time; the flag must not be rewritten each time too."""
	mapping, _frappe, writes, _doc_, row = _project_case(monkeypatch)

	mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)
	(_doctype, _name, updates), _kwargs = writes["set_value"][0]
	row.conflict_status = updates["conflict_status"]
	row.qbo_changes = updates["qbo_changes"]

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)

	assert result["action"] == "flagged"
	assert len(writes["set_value"]) == 1, "same differences, no second write"

	# QuickBooks says something new: the note is replaced.
	later = dict(JOB_PAYLOAD, SyncToken="7")
	monkeypatch.setattr(
		mapping, "map_qbo_to_erpnext", lambda *args: ("Project", {"customer": "Third Customer", "status": "Open"})
	)
	mapping.upsert_entity("Customer", later, SETTINGS)
	assert len(writes["set_value"]) == 2
	assert json.loads(writes["set_value"][1][0][2]["qbo_changes"])["fields"]["customer"]["qbo"] == "Third Customer"


def test_a_flag_that_no_longer_holds_clears_itself(monkeypatch):
	"""Someone fixed the record by hand to match QuickBooks. The next pass finds no
	difference, clears the flag and its note, and advances the cursor."""
	values = {"customer_name": "Acme Supply"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply")
	row = _mapping_row(
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		sync_token="2",
		conflict_status="QuickBooks Changed",
		qbo_changes=json.dumps({"sync_token": "3", "fields": {"customer_name": {"erpnext": "Acme", "qbo": "Acme Supply"}}}),
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)

	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "unchanged"
	updates = writes["set_value"][0][0][2]
	assert updates["conflict_status"] == "Clean" and updates["qbo_changes"] is None
	assert updates["sync_token"] == "3"


def test_a_legacy_conflict_row_is_reread_even_when_quickbooks_has_not_moved(monkeypatch):
	"""Rows flagged ``Conflict`` before v1.800.0 have no note. They are compared on their
	next sync whatever the token says, and become ``QuickBooks Changed`` or ``Clean``."""
	mapping, _frappe, writes, doc, _row = _project_case(monkeypatch, sync_token="6", conflict_status="Conflict")

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)

	assert result["action"] == "flagged"
	assert doc.saves == []
	assert writes["set_value"][0][0][2]["conflict_status"] == "QuickBooks Changed"


def test_a_value_a_person_kept_is_not_flagged_again_until_quickbooks_changes_it(monkeypatch):
	"""One Keep ERPNext is final for a standing difference: the declined value is the new
	reference, and the field is raised again only when QuickBooks says something else."""
	settled = {"customer": "Other Customer"}
	mapping, _frappe, writes, doc, _row = _project_case(monkeypatch, settled_qbo_values=json.dumps(settled))

	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)

	assert result["action"] == "unchanged", "the standing difference was already answered"
	assert doc.saves == [] and doc.customer == "First Customer"
	assert "conflict_status" not in writes["set_value"][0][0][2]

	monkeypatch.setattr(
		mapping, "map_qbo_to_erpnext", lambda *args: ("Project", {"customer": "Third Customer", "status": "Open"})
	)
	result = mapping.upsert_entity("Customer", dict(JOB_PAYLOAD), SETTINGS)
	assert result["action"] == "flagged" and result["fields"] == ["customer"]


def test_a_failing_preflight_on_a_linked_record_is_a_note_not_a_rewritten_mapping(monkeypatch):
	"""``save_manual_review_mapping`` replaces ``owned_fields`` with the issue list. For a
	record that already exists that destroyed the baseline; the issues now ride on the flag
	when there is something to flag, and are not a reason to raise one when there is not."""
	values = {"customer": "Acme Supply", "posting_date": "2026-11-05"}
	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00042",
		docstatus=0,
		customer="Acme Supply",
		posting_date="2026-11-02",
	)
	row = _mapping_row(
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		name="QBO-MAP-Invoice-900",
		match_status="Pending Review",
		conflict_status="Pending Review",
		owned_fields=json.dumps({"customer": "Acme Supply", "posting_date": "2026-11-02"}),
	)
	mapping, _frappe, writes = _wire(
		monkeypatch,
		doctype="Sales Invoice",
		values=values,
		mapping_row=row,
		doc=doc,
		issues=["Item reference 42 is not linked to an ERPNext Item"],
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "6"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["posting_date"]
	assert writes["manual_review"] == [] and writes["save_mapping"] == []
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert note["issues"] == ["Item reference 42 is not linked to an ERPNext Item"]
	assert doc.saves == []

	# The same issue with nothing QuickBooks changed: no flag, and nothing written at all.
	# The cursor stays (a pass that could not read everything settles nothing) and the row
	# keeps the parked status it had, because the data still does not validate.
	values["posting_date"] = "2026-11-02"
	writes["set_value"].clear()
	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "6"}, SETTINGS)

	assert result["action"] == "unchanged"
	assert result["issues"] == ["Item reference 42 is not linked to an ERPNext Item"]
	assert writes["manual_review"] == [] and writes["set_value"] == [] and writes["save_mapping"] == []


def test_an_unlinked_record_still_parks_on_a_failing_preflight(monkeypatch):
	"""What a first-time create does is unchanged: no link, failing preflight, parked."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	parked = []
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Sales Invoice", {"customer": None}))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: ["Customer is required"])
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: None)
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *args: parked.append(args))
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: False, raising=False)

	result = mapping.upsert_entity("Invoice", {"Id": "5", "SyncToken": "0"}, SETTINGS)

	assert result["action"] == "manual_review" and result["issues"] == ["Customer is required"]
	assert len(parked) == 1


def test_a_mapping_to_another_doctype_is_not_the_linked_path(monkeypatch):
	"""A QBO id linked to a Customer whose payload now resolves to a Project is the
	doctype-flip guard's case, even when a Project happens to carry the Customer's name."""
	values = {"project_name": "Courtyard Fountain", "customer": "Acme Supply"}
	doc = _doc(doctype="Project", name="Acme Supply", customer="Somebody Else")
	row = _mapping_row(erpnext_doctype="Customer", erpnext_name="Acme Supply", match_status="Created")
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Project", values=values, mapping_row=row, doc=doc)
	reviewed = []
	monkeypatch.setattr(mapping, "_review_linked_record", lambda *args, **kwargs: reviewed.append(args))
	monkeypatch.setattr(mapping, "_is_qbo_customer_job", lambda payload: False)
	row.save = lambda **kwargs: None

	result = mapping.upsert_entity("Customer", {"Id": "77", "SyncToken": "6"}, SETTINGS)

	assert reviewed == []
	assert result["action"] == "manual_review" and "already linked to Customer" in result["reason"]
	assert doc.saves == []


def test_review_comparison_forgives_what_an_accountant_would_not_call_a_difference():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _same_for_review

	assert _same_for_review(None, "") and _same_for_review(None, 0) and _same_for_review("", False)
	assert _same_for_review("Acme Supply ", "Acme Supply")
	# What ERPNext's own clean-up does to a value between the mapper and the column.
	assert _same_for_review("https://www.example.com/", "www.example.com")
	assert _same_for_review("<div>Bronze nozzle,  2 in.</div>", "Bronze nozzle, 2 in.")
	assert not _same_for_review("https://www.example.com", "www.example.org")
	assert not _same_for_review("Acme Supply", "ACME SUPPLY"), "case is a real difference in a name"
	assert _same_for_review(1.0, 1)
	assert not _same_for_review(0, 1) and not _same_for_review("Open", "Completed")
	assert not _same_for_review("0123", "123"), "two strings stay strings"
	assert not _same_for_review(None, "Acme")


def test_flagged_and_would_flag_are_counted_on_the_sync_log():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	log = types.SimpleNamespace(conflict_count=0, updated_count=0)
	sync._track_result(log, {"action": "flagged"})
	sync._track_result(log, {"action": "would_flag"})
	assert log.conflict_count == 2 and log.updated_count == 0


def test_the_linked_path_contains_no_write_to_the_record():
	"""Read the code. ``_review_linked_record`` and everything it calls to decide must hold
	no way to write the record or to rewrite ``owned_fields`` wholesale, and the linked
	branch of ``upsert_entity`` must hand over to it without touching the record itself."""
	writes = ("apply_values(", "apply_blank_values(", ".save(", ".insert(", "save_mapping(", "_save_or_manual_review(")
	for name in (
		"_review_linked_record",
		"_strip_values_erpnext_owns",
		"_linked_differences",
		"_table_differences",
		"_flag_qbo_changed",
		"_linked_record_needs_a_look",
	):
		code = _code_only(_function_source(CORE / "mapping.py", name))
		for write in writes:
			assert write not in code, f"{name} contains {write}"
	upsert = _code_only(_function_source(CORE / "mapping.py", "upsert_entity"))
	branch = upsert[upsert.index("frappe.db.exists(erpnext_doctype, mapping.erpnext_name)") :]
	branch = branch[: branch.index("if preflight_issues:")]
	assert "return _review_linked_record(" in branch
	for write in writes:
		assert write not in branch, f"the linked branch of upsert_entity contains {write}"
	# ``overwrite`` stays in the signature for an old caller and is read nowhere in the body.
	function = ast.parse(_function_source(CORE / "mapping.py", "upsert_entity")).body[0]
	assert "overwrite" in [argument.arg for argument in function.args.kwonlyargs]
	read = [node for node in ast.walk(function) if isinstance(node, ast.Name) and node.id == "overwrite"]
	assert read == [], "overwrite is accepted and ignored; nothing may branch on it"
	# ``recheck`` is read once, handed to the function that cannot write the record.
	read = [node for node in ast.walk(function) if isinstance(node, ast.Name) and node.id == "recheck"]
	assert len(read) == 1 and "recheck=recheck" in branch
	# The cursor and the flag are written with set_value, which leaves owned_fields alone.
	assert "frappe.db.set_value(MAPPING_DOCTYPE" in _function_source(CORE / "mapping.py", "_flag_qbo_changed")
	assert "frappe.db.set_value(MAPPING_DOCTYPE" in _function_source(CORE / "mapping.py", "_advance_mapping_cursor")


# ---------------------------------------------------------------------------
# Part B -- the side doors: a sync saving a linked record OTHER than the one it is syncing
# ---------------------------------------------------------------------------


def _link(entity_type="Account", qbo_id="50", match_rule=None, deleted=0):
	"""A mapping row, as ``other_mapping_linking`` reads it."""
	return types.SimpleNamespace(
		name=f"QBO-MAP-{entity_type}-{qbo_id}",
		qbo_entity_type=entity_type,
		qbo_id=qbo_id,
		match_rule=match_rule,
		deleted=deleted,
	)


def _mapping_rows(rows, filters):
	"""What ``frappe.get_all`` on the mapping table returns for ``rows``: a ``deleted``
	filter is honored. The first version of these stubs ignored filters, which is how a
	``deleted = 0`` filter in the code hid every deleted mapping without a test noticing."""
	wanted = (filters or {}).get("deleted")
	return [row for row in rows if wanted is None or row.deleted == wanted]


def _new_sub_account(monkeypatch, *, parent_links):
	"""An unlinked QuickBooks sub-account arriving under a ledger parent that has no GL
	entries -- the case ``_ensure_group_parent`` promotes. That function and
	``_blocked_unpromotable_parent`` are NOT stubbed here: they are what is under test."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	values = {
		"account_name": "Petty Cash 2",
		"parent_account": "Cash - MC",
		"is_group": 0,
		"root_type": "Asset",
		"account_type": "Cash",
		"company": "Main Co",
	}
	events = {"parked": [], "parent_saves": [], "inserted": [], "save_mapping": []}
	parent = _doc(doctype="Account", name="Cash - MC", is_group=0, account_type="Cash")
	parent.save = lambda **kwargs: events["parent_saves"].append((parent.is_group, parent.account_type))
	child = _doc(doctype="Account", name="Petty Cash 2 - MC")
	child.insert = lambda **kwargs: events["inserted"].append(child.name)

	def preflight(entity_type, doctype, mapped, **kwargs):
		issue = mapping._blocked_unpromotable_parent(doctype, mapped)
		return [issue] if issue else []

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Account" and filters == "Cash - MC" and fieldname == "is_group":
			return 0
		return None  # in particular: no GL Entry on the parent

	def get_all(doctype, filters=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and (filters or {}).get("erpnext_name") == "Cash - MC":
			return _mapping_rows(parent_links, filters)
		return []

	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Account", dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", preflight)
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: None)
	monkeypatch.setattr(mapping, "find_existing_match", lambda *args: None)
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *args: events["parked"].append(args))
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: events["save_mapping"].append(args))
	monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
	monkeypatch.setattr(frappe.db, "get_value", get_value, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: False, raising=False)
	monkeypatch.setattr(frappe, "get_all", get_all)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: parent, raising=False)
	monkeypatch.setattr(frappe, "new_doc", lambda doctype: child, raising=False)
	return mapping, events, parent, values


def test_a_new_sub_account_never_promotes_a_parent_the_sync_has_linked(monkeypatch):
	"""Promoting a ledger parent to a group sets ``is_group``, clears ``account_type`` and
	saves the parent Account. When that parent is itself a linked record, that is a sync
	saving a linked record -- reached by any path a new sub-account arrives on, the hourly
	CDC included. The child is parked for a person instead."""
	mapping, events, parent, _values = _new_sub_account(monkeypatch, parent_links=[_link("Account", "50")])

	result = mapping.upsert_entity("Account", {"Id": "91", "SyncToken": "0"}, SETTINGS)

	assert result["action"] == "manual_review"
	assert "QuickBooks Account 50 is linked to" in result["reason"]
	assert "Make it a group by hand" in result["reason"]
	assert events["parent_saves"] == [], "the linked parent was saved"
	assert (parent.is_group, parent.account_type) == (0, "Cash")
	assert events["inserted"] == [] and events["save_mapping"] == []
	assert len(events["parked"]) == 1

	# A preview parks nothing and saves nothing.
	events["parked"].clear()
	preview = mapping.upsert_entity("Account", {"Id": "91", "SyncToken": "0"}, SETTINGS, preview=True)
	assert preview["action"] == "manual_review"
	assert events == {"parked": [], "parent_saves": [], "inserted": [], "save_mapping": []}


def test_a_new_sub_account_still_promotes_a_parent_nothing_links(monkeypatch):
	"""The other half, so the test above cannot pass by ``_ensure_group_parent`` never
	running: with no mapping on the parent, the first-time create is what it always was."""
	mapping, events, _parent, _values = _new_sub_account(monkeypatch, parent_links=[])

	result = mapping.upsert_entity("Account", {"Id": "91", "SyncToken": "0"}, SETTINGS)

	assert result["action"] == "created"
	assert events["parent_saves"] == [(1, None)], "promoted to a group, Account Type cleared"
	assert events["inserted"] == ["Petty Cash 2 - MC"] and events["parked"] == []


def test_ensure_group_parent_refuses_a_linked_parent_even_past_the_preflight(monkeypatch):
	"""The guard sits next to the write as well as at the gate: called directly, with the
	preflight out of the picture, the function still does not load or save a linked parent."""
	mapping, events, _parent, values = _new_sub_account(monkeypatch, parent_links=[_link("TaxCode", "7")])

	mapping._ensure_group_parent("Account", dict(values))

	assert events["parent_saves"] == []


def _auto_link(monkeypatch, *, other_links):
	"""An unmapped QuickBooks customer whose name matches one existing ERPNext Customer."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	values = {"customer_name": "Acme Supply", "tax_id": "99-0000001"}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply", tax_id="")
	events = {"parked": [], "save_mapping": []}
	match = {"status": "matched", "name": "Acme Supply", "rule": "customer name", "confidence": 90}
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Customer", dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: [])
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: None)
	monkeypatch.setattr(mapping, "find_existing_match", lambda *args: dict(match))
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *args: events["parked"].append(args))
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: events["save_mapping"].append((args, kwargs)))
	monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
	monkeypatch.setattr(mapping, "_heal_invalid_owned_selects", lambda doc, values: [])
	monkeypatch.setattr(
		frappe,
		"get_all",
		lambda doctype, filters=None, **kwargs: _mapping_rows(other_links, filters)
		if doctype == "QuickBooks Sync Mapping"
		else [],
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: False, raising=False)
	return mapping, events, doc


@pytest.mark.parametrize("preview", [False, True])
def test_auto_link_never_saves_a_record_another_mapping_already_links(monkeypatch, preview):
	"""A customer re-created in QuickBooks under the same name, two jobs with one PRJ number,
	a TaxCode named like a linked Account: the matcher finds the record, and the auto-link
	path would fill its blank fields and save it. It is another QuickBooks record's link, so
	the sync parks this one for a person: no save, no second link."""
	mapping, events, doc = _auto_link(monkeypatch, other_links=[_link("Customer", "12")])

	result = mapping.upsert_entity("Customer", {"Id": "9", "SyncToken": "0"}, SETTINGS, preview=preview)

	assert result["action"] == "manual_review"
	assert "QuickBooks Customer 12 is already linked to it" in result["reason"]
	assert result["candidates"] == [{"doctype": "Customer", "name": "Acme Supply"}]
	assert doc.saves == [] and doc.sets == [] and doc.tax_id == ""
	assert events["save_mapping"] == []
	assert len(events["parked"]) == (0 if preview else 1)


@pytest.mark.parametrize(
	"other_links",
	[
		[],
		# A job consolidated onto this Customer files transactions under it; it is not the
		# Customer's own QuickBooks record, which must still be able to link.
		[_link("Customer", "301", match_rule="job_merge_no_project")],
		# The mapping being synced is not "another" mapping.
		[_link("Customer", "9")],
	],
)
def test_auto_link_still_links_a_record_no_other_quickbooks_record_owns(monkeypatch, other_links):
	mapping, events, doc = _auto_link(monkeypatch, other_links=other_links)

	result = mapping.upsert_entity("Customer", {"Id": "9", "SyncToken": "0"}, SETTINGS)

	assert result["action"] == "linked" and result["filled_fields"] == ["tax_id"]
	assert doc.tax_id == "99-0000001" and len(doc.saves) == 1
	assert len(events["save_mapping"]) == 1 and events["parked"] == []


def test_link_with_fill_blanks_is_refused_on_a_record_another_quickbooks_record_links(monkeypatch):
	"""A person's Link is an explicit action and two QuickBooks records may link one ERPNext
	record (Nik, 2026-10-22: a warning, not a refusal). What is refused is "Fill blank fields
	from QBO" onto a record that is already another QuickBooks record's: the link goes ahead
	without it, and the record is not saved."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply", tax_id="")
	saved = []
	monkeypatch.setattr(frappe, "get_single", lambda doctype: SETTINGS, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: True, raising=False)
	monkeypatch.setattr(frappe.db, "commit", lambda: None, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe,
		"get_all",
		lambda doctype, filters=None, **kwargs: [_link("Customer", "12")] if doctype == "QuickBooks Sync Mapping" else [],
	)
	monkeypatch.setattr(
		mapping,
		"_latest_raw_payload",
		lambda entity_type, qbo_id: types.SimpleNamespace(payload=json.dumps({"Id": "9", "SyncToken": "1"})),
	)
	monkeypatch.setattr(
		mapping, "map_qbo_to_erpnext", lambda *args: ("Customer", {"customer_name": "Acme Supply", "tax_id": "99-0000001"})
	)
	monkeypatch.setattr(
		mapping,
		"save_mapping",
		lambda *args, **kwargs: saved.append(kwargs) or types.SimpleNamespace(name="QBO-MAP-Customer-9"),
	)

	with pytest.raises(Exception, match="already linked to QuickBooks Customer 12"):
		mapping.link_existing_record("Customer", "9", "Customer", "Acme Supply", apply_qbo_data=True)
	assert doc.saves == [] and doc.sets == [] and saved == []

	name = mapping.link_existing_record("Customer", "9", "Customer", "Acme Supply", apply_qbo_data=False)
	assert name == "QBO-MAP-Customer-9"
	assert doc.saves == [] and doc.sets == [], "linking alone never writes the record"
	assert saved[0]["match_status"] == "Manual Matched"


def test_every_endpoint_that_links_or_saves_a_record_is_post_only():
	"""``decide_match``, ``decide_matches`` and ``link_existing_record`` write a mapping and
	can save an ERPNext record or merge one away. Like the three review endpoints, none of
	them answers a GET."""
	source = _read(CORE / "api.py")
	functions = {node.name: node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
	for endpoint in ("decide_match", "decide_matches", "link_existing_record", *REVIEW_ENDPOINTS):
		decorators = [ast.unparse(decorator) for decorator in functions[endpoint].decorator_list]
		assert decorators == ["frappe.whitelist(methods=['POST'])"], f"{endpoint}: {decorators}"
		assert "_require_qbo_operator()" in ast.get_source_segment(source, functions[endpoint])
	# The page reaches them with frappe.call, whose default is POST; nothing asks for a GET.
	assert 'type: "GET"' not in _read(PAGE_JS)


# ---------------------------------------------------------------------------
# Part B -- the person's half: Keep ERPNext, Use QuickBooks values, Refresh draft
# ---------------------------------------------------------------------------


FLAGGED_QBO = {"customer_name": "Acme Supply Co", "payment_terms": "Net 15", "tax_id": "99-0000001"}
CUSTOMER_FIELDS = ("customer_name", "customer_type", "customer_group", "payment_terms", "tax_id", "website")
PROJECT_FIELDS = ("project_name", "customer", "company", "status")


def _flagged_customer(monkeypatch, *, docstatus=0, issues=None, settled=None):
	"""A Customer flagged on three fields. ``docstatus`` stands in for a submittable record."""
	note = {
		"sync_token": "6",
		"last_updated": "2026-11-03T10:20:00-08:00",
		"detected_at": "2026-11-03 18:20:05",
		"fields": {
			"customer_name": {"erpnext": "Acme Supply", "qbo": "Acme Supply Co"},
			"payment_terms": {"erpnext": "Net 45", "qbo": "Net 15", "erpnext_edited": True},
			"tax_id": {"erpnext": "", "qbo": "99-0000001", "no_baseline": True},
		},
		"tables": {},
		"issues": list(issues or []),
	}
	doc = _doc(
		doctype="Customer",
		name="Acme Supply",
		docstatus=docstatus,
		customer_name="Acme Supply",
		payment_terms="Net 45",
		tax_id="",
		customer_group="Commercial",
	)
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		conflict_status="QuickBooks Changed",
		qbo_changes=json.dumps(note),
		owned_fields=json.dumps({"customer_name": "Acme Supply", "payment_terms": "Net 30"}),
		settled_qbo_values=json.dumps(settled) if settled else None,
	)
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	writes = {"set_value": [], "save_mapping": []}
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: writes["save_mapping"].append((args, kwargs)))
	monkeypatch.setattr(
		frappe,
		"get_meta",
		lambda doctype: _meta(*(PROJECT_FIELDS if doctype == "Project" else CUSTOMER_FIELDS)),
		raising=False,
	)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe.db, "set_value", lambda *args, **kwargs: writes["set_value"].append((args, kwargs)), raising=False
	)
	return mapping, frappe, writes, doc, row


def test_keep_erpnext_clears_the_flag_without_touching_the_record(monkeypatch):
	mapping, frappe, writes, doc, row = _flagged_customer(monkeypatch, settled={"website": "www.example.com"})

	def no_record(*args, **kwargs):
		raise AssertionError("Keep ERPNext loaded the ERPNext record")

	monkeypatch.setattr(frappe, "get_doc", no_record, raising=False)

	result = mapping.keep_erpnext_values("Customer", "1")

	assert result["kept"] == ["customer_name", "payment_terms", "tax_id"]
	assert doc.saves == [] and doc.sets == []
	assert writes["save_mapping"] == []
	(doctype, name, updates), kwargs = writes["set_value"][0]
	assert (doctype, name) == ("QuickBooks Sync Mapping", row.name)
	assert updates["conflict_status"] == "Clean" and updates["qbo_changes"] is None
	assert updates["sync_token"] == "6", "the cursor advances to what the reviewer was shown"
	# The declined values become the reference, beside what was settled before.
	assert json.loads(updates["settled_qbo_values"]) == dict(FLAGGED_QBO, website="www.example.com")
	assert "owned_fields" not in updates
	assert kwargs == {"update_modified": False}


def test_keep_erpnext_refuses_a_row_that_is_not_flagged(monkeypatch):
	mapping, _frappe, writes, _doc_, row = _flagged_customer(monkeypatch)
	row.conflict_status = "Clean"

	with pytest.raises(Exception, match="not flagged QuickBooks changed"):
		mapping.keep_erpnext_values("Customer", "1")
	assert writes["set_value"] == []


def test_use_quickbooks_values_writes_exactly_the_flagged_fields(monkeypatch):
	mapping, _frappe, writes, doc, _row = _flagged_customer(monkeypatch)

	result = mapping.use_qbo_values("Customer", "1", fields=["customer_name", "payment_terms", "tax_id"])

	assert sorted(doc.sets) == ["customer_name", "payment_terms", "tax_id"], "nothing else on the record is set"
	assert (doc.customer_name, doc.payment_terms, doc.tax_id) == ("Acme Supply Co", "Net 15", "99-0000001")
	assert doc.customer_group == "Commercial"
	assert doc.saves == [{"ignore_permissions": True}]
	assert doc.flags.ignore_links is True
	assert result["applied"] == ["customer_name", "payment_terms", "tax_id"] and result["kept"] == []
	updates = writes["set_value"][0][0][2]
	assert updates["conflict_status"] == "Clean" and updates["qbo_changes"] is None
	assert updates["sync_token"] == "6"
	assert json.loads(updates["owned_fields"]) == FLAGGED_QBO
	assert json.loads(updates["settled_qbo_values"]) == FLAGGED_QBO
	assert writes["save_mapping"] == []


def test_use_quickbooks_values_with_no_list_takes_what_the_page_ticks_by_default(monkeypatch):
	"""A caller that names no fields gets the page's default: the fields QuickBooks is known
	to have changed. ``tax_id`` has no baseline here, so it is left, and settled."""
	mapping, _frappe, writes, doc, _row = _flagged_customer(monkeypatch)

	result = mapping.use_qbo_values("Customer", "1")

	assert sorted(doc.sets) == ["customer_name", "payment_terms"]
	assert doc.tax_id == ""
	assert result["applied"] == ["customer_name", "payment_terms"] and result["kept"] == ["tax_id"]
	assert json.loads(writes["set_value"][0][0][2]["settled_qbo_values"]) == FLAGGED_QBO


def test_use_quickbooks_values_takes_only_the_ticked_fields_and_settles_the_rest(monkeypatch):
	mapping, _frappe, writes, doc, _row = _flagged_customer(monkeypatch)

	result = mapping.use_qbo_values("Customer", "1", fields=["payment_terms"])

	assert doc.sets == ["payment_terms"]
	assert (doc.customer_name, doc.payment_terms, doc.tax_id) == ("Acme Supply", "Net 15", "")
	assert result["applied"] == ["payment_terms"] and result["kept"] == ["customer_name", "tax_id"]
	updates = writes["set_value"][0][0][2]
	# Taken or left, every flagged value has been answered and is not raised again.
	assert json.loads(updates["settled_qbo_values"]) == FLAGGED_QBO
	# The untouched part of the baseline survives; the applied field is refreshed.
	assert json.loads(updates["owned_fields"]) == {"customer_name": "Acme Supply", "payment_terms": "Net 15"}


def test_use_quickbooks_values_blanks_a_field_quickbooks_cleared_only_when_it_is_chosen(monkeypatch):
	mapping, _frappe, writes, doc, row = _flagged_customer(monkeypatch)
	row.qbo_changes = json.dumps(
		{
			"sync_token": "6",
			"fields": {
				"payment_terms": {"erpnext": "Net 45", "qbo": None, "cleared": True},
				"customer_name": {"erpnext": "Acme Supply", "qbo": "Acme Supply Co"},
			},
		}
	)

	result = mapping.use_qbo_values("Customer", "1", fields=["customer_name"])
	assert doc.sets == ["customer_name"] and doc.payment_terms == "Net 45"
	assert result["kept"] == ["payment_terms"]
	assert json.loads(writes["set_value"][0][0][2]["settled_qbo_values"]) == {
		"customer_name": "Acme Supply Co",
		"payment_terms": None,
	}

	doc.sets.clear()
	result = mapping.use_qbo_values("Customer", "1", fields=["payment_terms"])
	assert doc.sets == ["payment_terms"] and doc.payment_terms is None
	assert result["applied"] == ["payment_terms"]
	assert json.loads(writes["set_value"][1][0][2]["owned_fields"])["payment_terms"] is None


def test_use_quickbooks_values_refuses_a_name_that_is_not_a_field_of_the_doctype(monkeypatch):
	"""The field names on a flag are stored JSON. A note naming ``docstatus`` or ``owner``,
	or a field the DocType does not have, sets nothing."""
	mapping, _frappe, writes, doc, row = _flagged_customer(monkeypatch)
	row.qbo_changes = json.dumps(
		{
			"sync_token": "6",
			"fields": {
				"customer_name": {"erpnext": "Acme Supply", "qbo": "Acme Supply Co"},
				"docstatus": {"erpnext": 0, "qbo": 1},
				"owner": {"erpnext": "a@example.com", "qbo": "b@example.com"},
			},
		}
	)

	with pytest.raises(Exception, match="Not a field of Customer: docstatus, owner"):
		mapping.use_qbo_values("Customer", "1")
	assert doc.saves == [] and doc.sets == []
	assert writes["set_value"] == []

	# The field that is real still applies when it is the one chosen.
	mapping.use_qbo_values("Customer", "1", fields=["customer_name"])
	assert doc.sets == ["customer_name"]


def test_use_quickbooks_values_says_so_when_every_flagged_field_is_erpnexts(monkeypatch):
	mapping, _frappe, writes, doc, row = _flagged_customer(monkeypatch)
	row.qbo_changes = json.dumps({"sync_token": "6", "fields": {"customer_type": {"erpnext": "Partnership", "qbo": "Individual"}}})

	with pytest.raises(Exception, match="Nothing on this flag can be written"):
		mapping.use_qbo_values("Customer", "1")
	assert doc.saves == [] and doc.sets == [] and writes["set_value"] == []


def test_use_quickbooks_values_cannot_reopen_or_repoint_a_project(monkeypatch):
	"""Belt and braces. No flag the sync writes holds a Project's status, company or title,
	but a note is stored JSON; if one ever did, the write still drops them."""
	mapping, frappe, writes, _customer, row = _flagged_customer(monkeypatch)
	project = _doc(
		doctype="Project",
		name="PRJ-00123",
		docstatus=0,
		project_name="Courtyard Fountain",
		customer="First Customer",
		company="Main Co",
		status="Completed",
	)
	row.erpnext_doctype, row.erpnext_name = "Project", "PRJ-00123"
	row.qbo_changes = json.dumps(
		{
			"sync_token": "6",
			"fields": {
				"customer": {"erpnext": "First Customer", "qbo": "Other Customer"},
				"status": {"erpnext": "Completed", "qbo": "Open"},
				"company": {"erpnext": "Main Co", "qbo": "Other Company"},
				"project_name": {"erpnext": "Courtyard Fountain", "qbo": "PRJ-00123 Renamed"},
			},
		}
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: project, raising=False)

	result = mapping.use_qbo_values("Customer", "77")

	assert project.sets == ["customer"] and result["applied"] == ["customer"]
	assert (project.status, project.company, project.project_name) == ("Completed", "Main Co", "Courtyard Fountain")
	assert len(writes["set_value"]) == 1


@pytest.mark.parametrize(
	"kwargs, setup, message",
	[
		({"fields": ["customer_group"]}, {}, "Not flagged on this record"),
		({"fields": []}, {}, "Tick at least one field"),
		({}, {"docstatus": 1}, "is submitted"),
		({}, {"docstatus": 2}, "is cancelled"),
		({}, {"issues": ["Customer reference is not linked"]}, "does not pass ERPNext's checks"),
	],
)
def test_use_quickbooks_values_refusals_write_nothing(monkeypatch, kwargs, setup, message):
	"""The submitted-document guard on the review side, and its neighbours: a posted or
	cancelled document, a field the flag does not hold, a flag whose data did not validate."""
	mapping, _frappe, writes, doc, _row = _flagged_customer(monkeypatch, **setup)

	with pytest.raises(Exception, match=re.escape(message)):
		mapping.use_qbo_values("Customer", "1", **kwargs)
	assert doc.saves == [] and doc.sets == []
	assert writes["set_value"] == []


NEWEST_PAYLOAD = {"Id": "7", "SyncToken": "9", "MetaData": {"LastUpdatedTime": "2026-11-09T10:00:00-08:00"}}


def _row_without_a_note(monkeypatch, *, conflict_status, match_status, docstatus, match_rule=None, payload=NEWEST_PAYLOAD):
	"""A linked Sales Invoice whose mapping carries no ``qbo_changes`` note: parked by the
	submitted-document guard, or flagged ``Conflict`` before v1.800.0. The newest stored
	payload moved the posting date."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00007",
		docstatus=docstatus,
		customer="Acme Supply",
		posting_date="2026-11-02",
	)
	row = _mapping_row(
		name="QBO-MAP-Invoice-7",
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		conflict_status=conflict_status,
		match_status=match_status,
		match_rule=match_rule,
		owned_fields=json.dumps({"customer": "Acme Supply", "posting_date": "2026-11-02"}),
		sync_token="5",
	)
	writes = {"set_value": [], "save_mapping": []}
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(mapping, "_raw_payload_dict", lambda entity_type, qbo_id: dict(payload) if payload else None)
	monkeypatch.setattr(
		mapping,
		"map_qbo_to_erpnext",
		lambda *args: ("Sales Invoice", {"customer": "Acme Supply", "posting_date": "2026-11-09"}),
	)
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: writes["save_mapping"].append(kwargs))
	monkeypatch.setattr(frappe, "get_single", lambda doctype: SETTINGS, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: docstatus if fieldname == "docstatus" else None,
		raising=False,
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe.db, "set_value", lambda *args, **kwargs: writes["set_value"].append((args, kwargs)), raising=False
	)
	return mapping, writes, doc, row


@pytest.mark.parametrize(
	"match_rule, restored", [(None, "Created"), ("manual", "Manual Matched"), ("customer name", "Auto Matched")]
)
def test_keep_erpnext_un_parks_a_posted_document_quickbooks_changed(monkeypatch, match_rule, restored):
	"""The submitted-document guard parks a posted document QuickBooks changed, and its
	message says to resolve it in ERPNext. Until now nothing on the page could then clear
	the row: Keep ERPNext refused everything but ``QuickBooks Changed``. It un-parks the row,
	moves the cursor to the newest stored payload and writes nothing to the document."""
	mapping, writes, doc, row = _row_without_a_note(
		monkeypatch, conflict_status="Pending Review", match_status="Pending Review", docstatus=1, match_rule=match_rule
	)

	result = mapping.keep_erpnext_values("Invoice", "7")

	assert doc.saves == [] and doc.sets == [] and writes["save_mapping"] == []
	(doctype, name, updates), kwargs = writes["set_value"][0]
	assert (doctype, name) == ("QuickBooks Sync Mapping", row.name)
	assert updates["conflict_status"] == "Clean" and updates["qbo_changes"] is None
	assert updates["sync_token"] == "9", "the next sync of the same payload is unchanged"
	assert updates["match_status"] == restored
	assert "owned_fields" not in updates and "settled_qbo_values" not in updates
	assert kwargs == {"update_modified": False}
	assert result["kept"] == []


def test_keep_erpnext_refuses_a_parked_draft(monkeypatch):
	"""Pending Review on a draft is a parked import with a cause to fix (Retry or Refresh
	draft). Only the posted case, which can never be refreshed, is Keep ERPNext's."""
	mapping, writes, _doc_, _row = _row_without_a_note(
		monkeypatch, conflict_status="Pending Review", match_status="Pending Review", docstatus=0
	)

	with pytest.raises(Exception, match=re.escape("not flagged QuickBooks changed (it is Pending Review)")):
		mapping.keep_erpnext_values("Invoice", "7")
	assert writes["set_value"] == []


def test_keep_erpnext_clears_a_legacy_conflict_row_and_settles_what_differs(monkeypatch):
	"""``Conflict`` is what the sync wrote before v1.800.0, with no note of what differed.
	Keep ERPNext compares the newest stored payload with the record (a read), settles the
	difference as declined so it is not raised again, and advances the cursor."""
	mapping, writes, doc, _row = _row_without_a_note(
		monkeypatch, conflict_status="Conflict", match_status="Created", docstatus=0
	)

	result = mapping.keep_erpnext_values("Invoice", "7")

	assert result["kept"] == ["posting_date"]
	assert doc.saves == [] and doc.sets == [] and doc.posting_date == "2026-11-02"
	updates = writes["set_value"][0][0][2]
	assert updates["conflict_status"] == "Clean" and updates["sync_token"] == "9"
	assert json.loads(updates["settled_qbo_values"]) == {"posting_date": "2026-11-09"}
	assert "owned_fields" not in updates and "match_status" not in updates


def test_keep_erpnext_clears_a_legacy_conflict_row_that_has_no_stored_payload(monkeypatch):
	mapping, writes, doc, _row = _row_without_a_note(
		monkeypatch, conflict_status="Conflict", match_status="Created", docstatus=0, payload=None
	)

	mapping.keep_erpnext_values("Invoice", "7")

	updates = writes["set_value"][0][0][2]
	assert updates == {"conflict_status": "Clean", "qbo_changes": None}, "nothing to move the cursor to"
	assert doc.saves == []


def _draft_invoice(monkeypatch, *, docstatus=0, issues=None, match_status="Created"):
	payload = {"Id": "900", "SyncToken": "4", "MetaData": {"LastUpdatedTime": "2026-11-03T10:20:00-08:00"}}
	values = {
		"customer": "Acme Supply",
		"posting_date": "2026-11-02",
		"due_date": None,
		"items": [{"item_code": "SERVICE", "qty": 1, "rate": 120.0}],
	}
	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00042",
		docstatus=docstatus,
		customer="Acme Supply",
		posting_date="2026-10-30",
		items=[types.SimpleNamespace(item_code="SERVICE", qty=1.0, rate=100.0)],
	)
	row = _mapping_row(
		name="QBO-MAP-Invoice-900",
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		match_status=match_status,
		conflict_status="QuickBooks Changed",
	)
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	writes = {"set_value": [], "save_mapping": []}
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(mapping, "_raw_payload_dict", lambda entity_type, qbo_id: dict(payload))
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Sales Invoice", dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: list(issues or []))
	monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: writes["save_mapping"].append((args, kwargs)))
	monkeypatch.setattr(frappe, "get_single", lambda doctype: SETTINGS, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe.db, "set_value", lambda *args, **kwargs: writes["set_value"].append((args, kwargs)), raising=False
	)
	return mapping, writes, doc, payload


def test_refresh_draft_rebuilds_the_one_draft_from_the_stored_payload(monkeypatch):
	mapping, writes, doc, payload = _draft_invoice(monkeypatch, match_status="Pending Review")

	result = mapping.refresh_linked_draft("Invoice", "900")

	assert sorted(doc.sets) == ["customer", "items", "posting_date"], "a None value is never applied"
	assert doc.posting_date == "2026-11-02" and doc.items == [{"item_code": "SERVICE", "qty": 1, "rate": 120.0}]
	assert doc.saves == [{"ignore_permissions": True}]
	assert result["applied"] == ["customer", "items", "posting_date"]
	(args, kwargs), = writes["save_mapping"]
	assert args[:5] == ("Invoice", "900", payload, "Sales Invoice", doc.name)
	# A parked draft is no longer parked. save_mapping records the rebuilt values as settled.
	assert kwargs == {"conflict_status": "Clean", "match_status": "Auto Matched"}
	assert args[5]["posting_date"] == "2026-11-02"


@pytest.mark.parametrize(
	"entity_type, setup, message",
	[
		("Invoice", {"docstatus": 1}, "is submitted"),
		("Invoice", {"issues": ["Item reference 42 is not linked"]}, "does not pass ERPNext's checks"),
		("Customer", {}, "Refresh draft is for draft transactions"),
	],
)
def test_refresh_draft_refusals_write_nothing(monkeypatch, entity_type, setup, message):
	mapping, writes, doc, _payload = _draft_invoice(monkeypatch, **setup)

	with pytest.raises(Exception, match=re.escape(message)):
		mapping.refresh_linked_draft(entity_type, "900")
	assert doc.saves == [] and doc.sets == []
	assert writes == {"set_value": [], "save_mapping": []}


def test_save_mapping_drops_a_resolved_note_and_settles_what_it_was_given(monkeypatch):
	"""``save_mapping`` runs when a record and a payload were just brought into line. A note
	is stale by then, and the values join the reference the next comparison reads (scalars
	only: a child table is already kept as mapped in ``owned_fields``). Settled values
	belong to the record they were settled on, and start again when the link moves."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	row = _mapping_row(
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		conflict_status="QuickBooks Changed",
		qbo_changes='{"fields": {}}',
		settled_qbo_values='{"tax_id": "99-0000001"}',
	)
	row.is_new = lambda: False
	row.save = lambda **kwargs: None
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: False, raising=False)
	values = {"customer_name": "Acme Supply", "website": None, "customer_group": "Commercial", "items": [{"qty": 1}]}

	mapping.save_mapping("Customer", "1", {"SyncToken": "9"}, "Customer", "Acme Supply", values, conflict_status="Clean")
	assert row.qbo_changes is None and row.conflict_status == "Clean"
	assert json.loads(row.settled_qbo_values) == {"tax_id": "99-0000001", "customer_name": "Acme Supply"}

	mapping.save_mapping("Customer", "1", {"SyncToken": "9"}, "Customer", "Acme Supply Inc", {})
	assert row.settled_qbo_values is None


# ---------------------------------------------------------------------------
# The Record Matching page's engine and endpoints
# ---------------------------------------------------------------------------


def _matching(monkeypatch):
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import matching

	events = {"stamp": [], "commit": 0, "rollback": [], "savepoint": []}
	monkeypatch.setattr(frappe, "session", types.SimpleNamespace(user="accountant@example.com"), raising=False)
	monkeypatch.setattr(matching, "_stamp_reviewed", lambda name, user: events["stamp"].append((name, user)))
	monkeypatch.setattr(frappe.db, "commit", lambda: events.__setitem__("commit", events["commit"] + 1), raising=False)
	monkeypatch.setattr(frappe.db, "savepoint", lambda name: events["savepoint"].append(name), raising=False)
	monkeypatch.setattr(frappe.db, "rollback", lambda save_point=None: events["rollback"].append(save_point), raising=False)
	return matching, frappe, events


def test_review_many_gives_one_result_per_row_and_one_failure_never_stops_the_rest(monkeypatch):
	matching, _frappe, events = _matching(monkeypatch)

	def keep(entity_type, qbo_id):
		if qbo_id == "2":
			raise Exception("<p>Customer 2 is not flagged QuickBooks changed</p>")
		return {"mapping": f"QBO-MAP-{entity_type}-{qbo_id}", "kept": ["customer_name"]}

	monkeypatch.setattr(matching, "keep_erpnext_values", keep)
	items = [
		{"entity_type": "Customer", "qbo_id": "1"},
		{"entity_type": "Customer", "qbo_id": "2"},
		{"entity_type": "Customer"},
		{"entity_type": "Customer", "qbo_id": 3},
	]

	results = matching.review_many(items, matching.keep_erpnext)

	assert [result["ok"] for result in results] == [True, False, False, True]
	assert results[0]["action"] == "kept" and results[0]["reviewed_by"] == "accountant@example.com"
	assert results[1]["error"] == "Customer 2 is not flagged QuickBooks changed", "markup stripped"
	assert "entity_type and a qbo_id" in results[2]["error"]
	assert events["stamp"] == [
		("QBO-MAP-Customer-1", "accountant@example.com"),
		("QBO-MAP-Customer-3", "accountant@example.com"),
	]
	assert events["commit"] == 2, "each decision is committed on its own"
	assert events["rollback"] == ["qbo_review_action", "qbo_review_action"]


def test_use_quickbooks_values_passes_each_rows_own_fields(monkeypatch):
	matching, _frappe, _events = _matching(monkeypatch)
	seen = []

	def use(entity_type, qbo_id, fields=None):
		seen.append((qbo_id, fields))
		return {"mapping": "QBO-MAP-Customer-" + qbo_id, "applied": fields or ["all"], "kept": []}

	monkeypatch.setattr(matching, "use_qbo_values", use)

	results = matching.review_many(
		[
			{"entity_type": "Customer", "qbo_id": "1", "fields": ["status"]},
			{"entity_type": "Customer", "qbo_id": "2"},
			{"entity_type": "Customer", "qbo_id": "3", "fields": "status"},
		],
		matching.use_quickbooks_values,
	)

	assert seen == [("1", ["status"]), ("2", None), ("3", None)]
	assert all(result["ok"] and result["action"] == "applied" for result in results)


def test_change_view_shows_what_the_record_holds_now(monkeypatch):
	"""The "ERPNext has" side is read from the record when the page asks. A field somebody
	already corrected by hand shows as agreeing instead of repeating the stored snapshot."""
	matching, frappe, _events = _matching(monkeypatch)
	note = {
		"detected_at": "2026-11-03 18:20:05",
		"fields": {
			"customer": {"erpnext": "First Customer", "qbo": "Other Customer"},
			"status": {"erpnext": "Completed", "qbo": "Open", "erpnext_edited": True},
		},
		"tables": {
			"items": {
				"erpnext_rows": 1,
				"qbo_rows": 2,
				"rows": [{"row": 1, "fields": {"rate": {"erpnext": 100.0, "qbo": 120.0}}}, {"row": 2, "only_in": "qbo"}],
				"more_rows": 0,
				"qbo": [{"rate": 120.0}, {"rate": 15.0}],
			}
		},
		"issues": [],
	}
	row = types.SimpleNamespace(
		conflict_status="QuickBooks Changed",
		qbo_changes=json.dumps(note),
		erpnext_doctype="Project",
		erpnext_name="PRJ-00123",
	)
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name, fields, as_dict=False: {"customer": "Other Customer", "status": "Completed"},
		raising=False,
	)

	view = matching.change_view(row)

	by_name = {field["fieldname"]: field for field in view["fields"]}
	assert by_name["customer"]["erpnext"] == "Other Customer" and by_name["customer"]["agrees"] is True
	assert by_name["status"] == {
		"fieldname": "status",
		"label": "status",
		"erpnext": "Completed",
		"qbo": "Open",
		"edited": True,
		"baselined": True,
		"cleared": False,
		"unresolved": False,
		"agrees": False,
	}
	table = view["tables"][0]
	assert (table["erpnext_rows"], table["qbo_rows"]) == (1, 2)
	assert table["rows"][0]["fields"] == [{"fieldname": "rate", "label": "rate", "erpnext": "100.0", "qbo": "120.0"}]
	assert table["rows"][1]["only_in"] == "qbo"
	assert "qbo" not in table, "the full QuickBooks rows stay on the server"

	row.conflict_status = "Clean"
	assert matching.change_view(row) is None


def test_the_queue_filters_on_the_flag(monkeypatch):
	matching, _frappe, _events = _matching(monkeypatch)

	assert "QuickBooks Changed" in matching.STATUS_FILTERS
	where, params = matching._master_where(["Customer"], "QuickBooks Changed", None)
	assert "conflict_status = 'QuickBooks Changed'" in where
	assert params == {"types": ("Customer",)}
	assert matching._parked_filters(["Invoice"]) == {
		"qbo_entity_type": ["in", ["Invoice"]],
		"deleted": 0,
		"match_status": "Pending Review",
	}
	assert matching._parked_filters(["Invoice"], "changed") == {
		"qbo_entity_type": ["in", ["Invoice"]],
		"deleted": 0,
		"conflict_status": "QuickBooks Changed",
	}


def test_every_review_endpoint_is_post_only_gated_and_re_exported():
	"""The engine saves with ``ignore_permissions``, so the role gate on the endpoint is the
	only access control there is; and a review action that answers a GET can be triggered
	by a link."""
	source = _read(CORE / "api.py")
	tree = ast.parse(source)
	functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
	reexport = _read(APP / "quickbooks_online" / "api.py")
	for endpoint in REVIEW_ENDPOINTS:
		node = functions[endpoint]
		decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
		assert decorators == ["frappe.whitelist(methods=['POST'])"], f"{endpoint}: {decorators}"
		body = ast.get_source_segment(source, node)
		calls = [ast.unparse(statement) for statement in node.body if not isinstance(statement.value, ast.Constant)]
		assert calls[0] == "_require_qbo_operator()", f"{endpoint} does work before the role gate"
		assert "matching.review_many(" in body
		for wider in ("enqueue", "run_cdc", "import_all", "sync_entity", "QuickBooksClient"):
			assert wider not in _code_only(body), f"{endpoint} reaches {wider}"
		assert endpoint in reexport, f"{endpoint} is not callable at ...quickbooks_online.api.{endpoint}"
	assert 'QBO_OPERATOR_ROLES = ("System Manager", "Accounts Manager")' in source


def test_the_review_actions_never_reach_quickbooks_or_the_queue():
	"""Keep / Use / Refresh act on one mapping each. None of the code under them may fetch
	from QuickBooks, queue a job, or call the sync."""
	matching_source = _read(CORE / "matching.py")
	for name in ("keep_erpnext", "use_quickbooks_values", "refresh_draft", "review_many"):
		code = _code_only(_function_source(CORE / "matching.py", name))
		for wider in ("enqueue", "sync_entity", "import_all", "run_cdc", "QuickBooksClient"):
			assert wider not in code, f"matching.{name} reaches {wider}"
	assert "from erpnext_enhancements.quickbooks_online.core.sync" not in matching_source
	for name in ("keep_erpnext_values", "use_qbo_values", "refresh_linked_draft"):
		code = _code_only(_function_source(CORE / "mapping.py", name))
		for wider in ("enqueue", "QuickBooksClient", "upsert_entity("):
			assert wider not in code, f"mapping.{name} reaches {wider}"
	assert "keep_erpnext_values" in matching_source and "use_qbo_values" in matching_source


def test_review_many_refuses_an_oversized_request_before_writing_anything(monkeypatch):
	"""The page sends 20 rows a request. The server has its own ceiling, because two of the
	three actions save an ERPNext record per row and nothing obliges a caller to be the page."""
	matching, _frappe, events = _matching(monkeypatch)
	called = []
	monkeypatch.setattr(matching, "keep_erpnext_values", lambda *args: called.append(args))
	items = [{"entity_type": "Customer", "qbo_id": str(n)} for n in range(matching.MAX_REVIEW_ITEMS + 1)]

	with pytest.raises(Exception, match=f"at most {matching.MAX_REVIEW_ITEMS} rows"):
		matching.review_many(items, matching.keep_erpnext)
	assert called == [] and events["commit"] == 0 and events["savepoint"] == []

	chunk = re.search(r"const REVIEW_CHUNK = (\d+);", _read(PAGE_JS))
	assert int(chunk.group(1)) <= matching.MAX_REVIEW_ITEMS, "the page's own chunk must fit under the ceiling"


def test_change_view_marks_a_field_with_no_baseline_and_a_field_quickbooks_cleared(monkeypatch):
	matching, frappe, _events = _matching(monkeypatch)
	note = {
		"fields": {
			"customer_name": {"erpnext": "Acme Supply", "qbo": "Acme Supply Co"},
			"tax_id": {"erpnext": "", "qbo": "99-0000001", "no_baseline": True},
			"payment_terms": {"erpnext": "Net 30", "qbo": None, "cleared": True},
		},
		"tables": {"terms": {"erpnext_rows": 1, "qbo_rows": 1, "rows": [], "qbo": [], "no_baseline": True}},
	}
	row = types.SimpleNamespace(
		conflict_status="QuickBooks Changed",
		qbo_changes=json.dumps(note),
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
	)
	monkeypatch.setattr(frappe.db, "get_value", lambda *args, **kwargs: None, raising=False)

	view = matching.change_view(row)

	by_name = {field["fieldname"]: field for field in view["fields"]}
	assert (by_name["customer_name"]["baselined"], by_name["customer_name"]["cleared"]) == (True, False)
	assert (by_name["tax_id"]["baselined"], by_name["tax_id"]["cleared"]) == (False, False)
	assert (by_name["payment_terms"]["baselined"], by_name["payment_terms"]["cleared"]) == (True, True)
	assert by_name["payment_terms"]["qbo"] == "", "a clear shows as blank"
	assert view["tables"][0]["baselined"] is False


def test_the_page_leaves_never_baselined_and_cleared_fields_unticked():
	"""One "Use QuickBooks values" click takes what is ticked. A field the sync never wrote
	or read before may not have been changed by QuickBooks at all, and taking a cleared field
	blanks the ERPNext value: both start unticked, each with its own label."""
	js = _read(PAGE_JS)
	assert "tick(field.fieldname, field.baselined !== false && !field.cleared)" in js
	assert "tick(table.fieldname, table.baselined !== false)" in js
	assert '__("never synced before")' in js and '__("cleared in QuickBooks")' in js
	assert '__("also edited in ERPNext")' in js, "a different note from the no-baseline one"
	tick = js[js.index("const tick = (fieldname, on) =>") :]
	tick = tick[: tick.index("const fieldRows")]
	assert '${on ? "checked" : ""}' in tick
	# Keep ERPNext is offered on the two kinds of row that carry no differences.
	assert 'row.conflict_status === "Conflict" || isParkedPosted(row)' in js
	assert 'row.docstatus === 1 && row.match_status === "Pending Review"' in js


# ---------------------------------------------------------------------------
# Second review: stale replays, number precision, unresolved values, deleted mappings
# ---------------------------------------------------------------------------


def _field(fieldname, fieldtype, precision=None, options=None):
	return types.SimpleNamespace(fieldname=fieldname, fieldtype=fieldtype, precision=precision, options=options, label=fieldname)


def _typed_meta(fields):
	"""A meta double with real field definitions, for the precision-aware comparison."""
	names = {field.fieldname for field in fields}
	return types.SimpleNamespace(fields=list(fields), has_field=lambda fieldname: fieldname in names)


def _terms_case(monkeypatch, *, record_terms, synced_terms, token="2", **mapping_overrides):
	"""A linked Customer whose only moving part is ``payment_terms``."""
	values = {"customer_name": "Acme Supply", "payment_terms": synced_terms}
	doc = _doc(doctype="Customer", name="Acme Supply", customer_name="Acme Supply", payment_terms=record_terms)
	row = _mapping_row(
		name="QBO-MAP-Customer-1",
		erpnext_doctype="Customer",
		erpnext_name="Acme Supply",
		owned_fields=json.dumps({"customer_name": "Acme Supply", "payment_terms": synced_terms}),
		sync_token=token,
		**mapping_overrides,
	)
	mapping, _frappe, writes = _wire(monkeypatch, doctype="Customer", values=values, mapping_row=row, doc=doc)
	return mapping, writes, doc, row, values


def test_a_stale_replay_never_clears_a_flag_raised_from_a_newer_payload(monkeypatch):
	"""The probe. Record Net 30, cursor at token 2. CDC delivers token 4 with Net 45:
	flagged, cursor held at 2. Run Resync then replays what an earlier Preview Resync stored
	(token 3, Net 30, ``recheck=True``). Nothing in that old payload differs from the record,
	so the flag and its note were cleared and the mapping ended Clean at token 3 while
	QuickBooks said Net 45."""
	mapping, writes, doc, row, values = _terms_case(monkeypatch, record_terms="Net 30", synced_terms="Net 30")

	values["payment_terms"] = "Net 45"
	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "4"}, SETTINGS)
	assert result["action"] == "flagged"
	_replay(row, writes)
	flagged_note = row.qbo_changes

	values["payment_terms"] = "Net 30"
	for flags in ({"recheck": True}, {}, {"recheck": True, "preview": True}):
		result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS, **flags)
		assert result == {"action": "unchanged", "doctype": "Customer", "name": "Acme Supply", "stale": True}
		assert writes["set_value"] == [], "a stale payload writes nothing"
	assert row.conflict_status == "QuickBooks Changed" and row.qbo_changes == flagged_note
	assert row.sync_token == "2" and doc.saves == []

	# The payload the flag was raised from is not stale: it re-derives the same flag.
	values["payment_terms"] = "Net 45"
	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "4"}, SETTINGS)
	assert result["action"] == "flagged" and writes["set_value"] == []


def test_a_stale_replay_never_raises_a_flag_on_a_mapping_that_has_moved_on(monkeypatch):
	"""The mirror case. The cursor is at token 4 and the record agrees with QuickBooks (Net
	45). A replay of token 3 (Net 30) with ``recheck`` differs from the record and from the
	reference, and used to raise a flag about something QuickBooks no longer says."""
	mapping, writes, doc, row, values = _terms_case(
		monkeypatch, record_terms="Net 45", synced_terms="Net 45", token="4"
	)

	values["payment_terms"] = "Net 30"
	result = mapping.upsert_entity("Customer", {"Id": "1", "SyncToken": "3"}, SETTINGS, recheck=True)

	assert result["action"] == "unchanged" and result["stale"] is True
	assert writes["set_value"] == [] and doc.saves == []
	assert row.conflict_status == "Clean" and row.sync_token == "4"


def test_no_write_of_a_mapping_cursor_goes_backwards(monkeypatch):
	"""Every cursor write is forward-only: the linked path's own, a person's Keep, and the
	``save_*`` helpers the unlinked paths use, which assigned whatever payload they were
	handed."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	row = _mapping_row(sync_token="7")
	assert mapping._forward_cursor(row, {"SyncToken": "6"}) == {}
	assert mapping._forward_cursor(row, {"SyncToken": "7"}) == {"sync_token": "7"}
	assert mapping._forward_cursor(row, {"SyncToken": "8"}) == {"sync_token": "8"}
	assert mapping._cursor_order("10", None, "9", None) == 1, "tokens are numbers, not text"
	assert mapping._cursor_order(None, None, "9", None) is None

	row.is_new = lambda: False
	row.save = lambda **kwargs: None
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(frappe.db, "exists", lambda *args, **kwargs: False, raising=False)
	old = {"Id": "77", "SyncToken": "5"}
	mapping.save_mapping("Customer", "77", old, "Project", "PRJ-00123", {})
	assert row.sync_token == "7"
	mapping.save_manual_review_mapping("Customer", "77", old, "Project", ["a reason"])
	assert row.sync_token == "7"
	mapping.save_pending_mapping("Customer", "77", old, "Project", {"reason": "two matches", "candidates": []})
	assert row.sync_token == "7"
	mapping.save_mapping("Customer", "77", {"Id": "77", "SyncToken": "9"}, "Project", "PRJ-00123", {})
	assert row.sync_token == "9"

	# No remaining unconditional assignment in the module.
	source = _code_only(_read(CORE / "mapping.py"))
	assert "mapping.sync_token = payload.get('SyncToken')" not in source
	assert source.count("mapping.sync_token = ") == 1, "only _stamp_cursor assigns it"


def test_numbers_are_compared_at_the_fields_precision_and_exactly_when_it_is_unknown():
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _same_for_review

	assert _same_for_review(1.0, 1) and _same_for_review("1.0", 1)
	assert not _same_for_review(1234.5, 1234.504), "no blanket tolerance when the precision is unknown"
	# A Currency field that keeps 2 decimals holds 12.35 for a mapped 12.3456: the same value.
	assert _same_for_review(12.35, 12.3456, 2)
	assert not _same_for_review(12.34, 12.3456, 2)
	# An exchange rate is a Float kept to 9 decimals; half a cent is a real change in one.
	assert not _same_for_review(1.32, 1.3245, 9)
	assert _same_for_review(1.5, 1.5004, 3) and not _same_for_review(1.5, 1.5006, 3)
	assert not _same_for_review(0.0067, 0.0071, 9)
	assert not _same_for_review(100, 100.004, None)


def _invoice_with_typed_fields(monkeypatch, *, values, record, owned, issues=None, rate_precision=2):
	doc = _doc(doctype="Sales Invoice", name="ACC-SINV-2026-00042", docstatus=0, **record)
	row = _mapping_row(
		name="QBO-MAP-Invoice-900",
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		match_status="Created",
		owned_fields=json.dumps(owned),
		sync_token="2",
	)
	mapping, frappe, writes = _wire(
		monkeypatch, doctype="Sales Invoice", values=values, mapping_row=row, doc=doc, issues=issues
	)
	metas = {
		"Sales Invoice": _typed_meta(
			[
				_field("customer", "Link"),
				_field("conversion_rate", "Float", 9),
				_field("discount_amount", "Currency"),
				_field("items", "Table", options="Sales Invoice Item"),
				_field("accounts", "Table", options="Journal Entry Account"),
			]
		),
		"Sales Invoice Item": _typed_meta(
			[_field("item_code", "Link"), _field("qty", "Float"), _field("rate", "Currency", rate_precision)]
		),
		"Journal Entry Account": _typed_meta([_field("account", "Link"), _field("debit_in_account_currency", "Currency")]),
	}
	monkeypatch.setattr(frappe, "get_meta", lambda doctype: metas[doctype], raising=False)
	return mapping, writes, doc, row


def test_an_exchange_rate_change_is_flagged_and_a_stored_rounding_is_not(monkeypatch):
	"""``conversion_rate`` comes from QuickBooks' ``ExchangeRate``. 1.32 to 1.3245 is inside
	the half-cent tolerance the first version gave every number of 1 or more, so it read as
	unchanged with the cursor advanced. A Currency amount that ERPNext stored rounded is
	still the same amount."""
	values = {"customer": "Acme Supply", "conversion_rate": 1.3245, "discount_amount": 12.3456}
	record = {"customer": "Acme Supply", "conversion_rate": 1.32, "discount_amount": 12.35}
	mapping, writes, doc, _row = _invoice_with_typed_fields(
		monkeypatch,
		values=values,
		record=record,
		owned={"customer": "Acme Supply", "conversion_rate": 1.32, "discount_amount": 12.35},
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["conversion_rate"]
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert note["fields"] == {"conversion_rate": {"erpnext": 1.32, "qbo": 1.3245}}
	assert doc.saves == []
	assert mapping.number_precisions("Sales Invoice")["conversion_rate"] == 9
	assert mapping.number_precisions("Sales Invoice")["discount_amount"] == 2, "System Settings default"
	assert mapping.number_precisions("Sales Invoice")["items"] == {"qty": 3, "rate": 2}


@pytest.mark.parametrize(
	"rate_precision, record_rate, qbo_rate, record_qty, qbo_qty, flagged",
	[
		(4, 12.3456, 12.3478, 1.0, 1.0, {"rate"}),  # a four-decimal unit price that moved
		(2, 12.35, 12.3456, 1.0, 1.0, set()),  # the same price as a 2-decimal column stores it
		(2, 12.35, 12.3456, 1.5, 1.5006, {"qty"}),  # a quantity is a Float: its own 3 decimals
		(2, 12.35, 12.3456, 1.5, 1.5004, set()),
	],
)
def test_table_cells_are_compared_at_the_child_fields_precision(
	monkeypatch, rate_precision, record_rate, qbo_rate, record_qty, qbo_qty, flagged
):
	values = {"customer": "Acme Supply", "items": [{"item_code": "SERVICE", "qty": qbo_qty, "rate": qbo_rate}]}
	record = {
		"customer": "Acme Supply",
		"items": [types.SimpleNamespace(item_code="SERVICE", qty=record_qty, rate=record_rate)],
	}
	mapping, writes, doc, _row = _invoice_with_typed_fields(
		monkeypatch,
		values=values,
		record=record,
		owned={"customer": "Acme Supply", "items": [{"item_code": "SERVICE", "qty": 9, "rate": 9}]},
		rate_precision=rate_precision,
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "3"}, SETTINGS)

	if not flagged:
		assert result["action"] == "unchanged"
		return
	assert result["action"] == "flagged" and result["fields"] == ["items"]
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert set(note["tables"]["items"]["rows"][0]["fields"]) == flagged
	assert doc.saves == []


def test_a_value_that_did_not_resolve_is_flagged_when_the_payload_fails_the_preflight(monkeypatch):
	"""(i) QuickBooks moved the invoice to a customer ERPNext cannot resolve, so the mapper
	sends None and the preflight says why. The field has no baseline (the invoice was linked,
	not created). Read as "QuickBooks says nothing", that advanced the cursor with no flag."""
	values = {"customer": None, "posting_date": "2026-11-02"}
	record = {"customer": "Acme Supply", "posting_date": "2026-11-02"}
	mapping, writes, doc, row = _invoice_with_typed_fields(
		monkeypatch,
		values=values,
		record=record,
		owned={"posting_date": "2026-11-02"},
		issues=["Customer reference 88 is not linked to an ERPNext Customer"],
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["customer"]
	updates = writes["set_value"][0][0][2]
	note = json.loads(updates["qbo_changes"])
	assert note["fields"] == {
		"customer": {"erpnext": "Acme Supply", "qbo": None, "unresolved": True, "no_baseline": True}
	}
	assert note["issues"] == ["Customer reference 88 is not linked to an ERPNext Customer"]
	assert "sync_token" not in updates and "settled_qbo_values" not in updates, "a pass with issues settles nothing"
	assert doc.saves == [] and writes["manual_review"] == []

	# Use QuickBooks values cannot take it: the flag carries the issues.
	_replay(row, writes)
	with pytest.raises(Exception, match="does not pass ERPNext's checks"):
		mapping.use_qbo_values("Invoice", "900")

	# Keep ERPNext answers it. The same unresolved None on a later payload is then a None
	# that has been seen: not raised again.
	mapping.keep_erpnext_values("Invoice", "900")
	_replay(row, writes)
	assert json.loads(row.settled_qbo_values) == {"customer": None} and row.sync_token == "3"
	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "4"}, SETTINGS)
	assert result["action"] == "unchanged" and writes["set_value"] == []


def test_a_table_cell_that_did_not_resolve_is_flagged_when_the_payload_fails_the_preflight(monkeypatch):
	"""(ii) A journal line moved to a ledger that is not imported: the row's account maps to
	None, the amount is the same. A None cell was skipped, so the row read as identical, the
	mapping went Clean and the preflight's reason was discarded."""
	values = {"accounts": [{"account": None, "debit_in_account_currency": 120.0}]}
	record = {"accounts": [types.SimpleNamespace(account="Tools - MC", debit_in_account_currency=120.0)]}
	issue = "Account reference 310 is not linked to an ERPNext Account"
	mapping, writes, doc, _row = _invoice_with_typed_fields(
		monkeypatch,
		values=values,
		record=record,
		owned={"accounts": [{"account": "Tools - MC", "debit_in_account_currency": 120.0}]},
		issues=[issue],
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "3"}, SETTINGS)

	assert result["action"] == "flagged" and result["fields"] == ["accounts"] and result["issues"] == [issue]
	note = json.loads(writes["set_value"][0][0][2]["qbo_changes"])
	assert note["tables"]["accounts"]["rows"] == [
		{"row": 1, "fields": {"account": {"erpnext": "Tools - MC", "qbo": None}}}
	]
	assert note["issues"] == [issue]
	assert "sync_token" not in writes["set_value"][0][0][2]
	assert doc.saves == []

	# Without preflight issues the same None cell is QuickBooks saying nothing about it.
	mapping, writes, doc, _row = _invoice_with_typed_fields(
		monkeypatch,
		values={"accounts": [{"account": None, "debit_in_account_currency": 120.0}]},
		record=record,
		owned={"accounts": [{"account": "Tools - MC", "debit_in_account_currency": 999.0}]},
	)
	assert mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "3"}, SETTINGS)["action"] == "unchanged"


def test_a_mapping_marked_deleted_still_names_a_linked_record(monkeypatch):
	"""``deleted`` says QuickBooks removed its record. The ERPNext record it names is still
	one the sync linked, and filtering on ``deleted = 0`` left it open to both side doors."""
	removed = _link("Account", "50", deleted=1)
	mapping, events, parent, _values = _new_sub_account(monkeypatch, parent_links=[removed])

	result = mapping.upsert_entity("Account", {"Id": "91", "SyncToken": "0"}, SETTINGS)

	assert result["action"] == "manual_review" and "QuickBooks Account 50 is linked to" in result["reason"]
	assert events["parent_saves"] == [] and events["inserted"] == []
	mapping._ensure_group_parent("Account", {"parent_account": "Cash - MC"})
	assert events["parent_saves"] == []

	mapping, events, doc = _auto_link(monkeypatch, other_links=[_link("Customer", "12", deleted=1)])
	result = mapping.upsert_entity("Customer", {"Id": "9", "SyncToken": "0"}, SETTINGS)
	assert result["action"] == "manual_review"
	assert doc.saves == [] and events["save_mapping"] == []

	# The lookup itself asks for every mapping row of the record, whatever ``deleted`` says.
	lookup = _code_only(_function_source(CORE / "mapping.py", "other_mapping_linking"))
	assert "'deleted': 0" not in lookup


def test_decide_many_refuses_an_oversized_request_before_linking_anything(monkeypatch):
	matching, _frappe, events = _matching(monkeypatch)
	linked = []
	monkeypatch.setattr(matching, "decide", lambda *args, **kwargs: linked.append(args))
	decisions = [
		{"entity_type": "Customer", "qbo_id": str(n), "erpnext_name": "Acme"} for n in range(matching.MAX_REVIEW_ITEMS + 1)
	]

	with pytest.raises(Exception, match=f"at most {matching.MAX_REVIEW_ITEMS} decisions"):
		matching.decide_many(decisions)
	assert linked == [] and events["savepoint"] == []
	chunk = re.search(r"const LINK_CHUNK = (\d+);", _read(PAGE_JS))
	assert int(chunk.group(1)) <= matching.MAX_REVIEW_ITEMS


def test_the_page_says_what_keep_declines_sight_unseen_and_shows_what_did_not_resolve():
	js = _read(PAGE_JS)
	# Keep ERPNext on a legacy Conflict row declines differences the page never showed.
	keep = js[js.index("function keepErpnext(entries, scope) {") :]
	keep = keep[: keep.index("\n\t}\n")]
	assert 'row.conflict_status === "Conflict"' in keep
	assert "is declined without being shown" in keep
	assert "if (entries.length === 1 && !legacy) {" in keep, "one legacy row still gets the question"
	# A value that did not resolve has its own note and no tick box.
	assert '__("does not resolve in ERPNext")' in js
	assert "field.agrees || field.unresolved ?" in js


# ---------------------------------------------------------------------------
# A randomized walk over the real functions
# ---------------------------------------------------------------------------
#
# The cases above each pin one path. This drives upsert_entity, keep_erpnext_values and
# use_qbo_values through a few hundred random steps per seed -- QuickBooks edits, ERPNext
# edits, syncs, replays of older payloads, previews, Keep, Use on a random subset -- and
# checks one invariant after every step:
#
#   * no sync ever writes the record;
#   * after a sync that compares -- QuickBooks moved, the mapping is flagged, or a resync
#     asked (recheck) -- every mapped field of the newest payload the mapping has been
#     shown either equals the record, or is in the flag note (with the value QuickBooks
#     says now), or QuickBooks still says what was last accounted for (it agreed with the
#     record at a sync, or a person declined it);
#   * a sync of a payload QuickBooks has not moved past, with no flag and no recheck, does
#     not look and writes nothing. That is the rule ("has not advanced: unchanged"), and
#     it is why a difference with no baseline waits for the next pass that compares. The
#     first run of this walk failed on exactly that, at step one of 21 of the 40 seeds,
#     until the invariant said so;
#   * a payload older than the newest one shown changes nothing at all;
#   * the stored cursor never moves backwards.
#
# "Accounted for" is tracked by the walk itself, from what it did, not read back from the
# mapping. Seeds are fixed, so a failure reproduces; a seed that ever fails stays here.

WALK_POOLS = {
	"customer_name": ["Acme", "Acme Supply", "Acme Supply Co"],
	"payment_terms": ["Net 15", "Net 30", "Net 45", None],
	"tax_id": ["11-1", "22-2", None],
	"website": ["www.example.com", "https://www.example.com", "example.org", None],
	"credit_limit": [0, 100.0, 250.5, 250.504, 250.51],
	"exchange_factor": [1.0, 1.32, 1.3245],
	"terms": [
		[{"credit_days": 30, "invoice_portion": 100}],
		[{"credit_days": 45, "invoice_portion": 100}],
		[{"credit_days": 30, "invoice_portion": 50}, {"credit_days": 60, "invoice_portion": 50}],
	],
}
WALK_NEVER = object()
WALK_SEEDS = range(40)
WALK_STEPS = 250
#: Decimals each number field stores. The pools hold 250.5 beside 250.504 (the same amount
#: in a 2-decimal Currency field) and 250.51 (not), and 1.32 beside 1.3245 (never the same
#: in a 9-decimal Float).
WALK_DECIMALS = {"credit_limit": 2, "exchange_factor": 9}


def _walk_blank(value):
	return value is None or value == "" or value == [] or (not isinstance(value, (str, list)) and value == 0)


def _walk_same(fieldname, left, right):
	"""The walk's own idea of "the same value", written against the pools above and
	sharing nothing with the code under test: if the code's comparison drifts, the two
	disagree and the walk fails."""
	if _walk_blank(left) and _walk_blank(right):
		return True
	if _walk_blank(left) or _walk_blank(right):
		return False
	if fieldname in WALK_DECIMALS:
		return round(float(left), WALK_DECIMALS[fieldname]) == round(float(right), WALK_DECIMALS[fieldname])
	if fieldname == "website":
		return left.replace("https://", "") == right.replace("https://", "")
	return json.loads(json.dumps(left)) == json.loads(json.dumps(right))


class _Walk:
	"""One seeded run. ``tax_id`` starts with no baseline: the record holds a value the
	import never wrote."""

	def __init__(self, monkeypatch, seed):
		import copy
		import random

		self.copy = copy.deepcopy
		self.rng = random.Random(seed)
		self.seed = seed
		frappe = install_frappe_stub()
		from erpnext_enhancements.quickbooks_online.core import mapping

		self.mapping = mapping
		initial = {name: self.copy(pool[0]) for name, pool in WALK_POOLS.items()}
		self.doc = _doc(doctype="Customer", name="Acme", docstatus=0, **self.copy(initial))
		self.doc.tax_id = "99-9"
		baseline = {name: value for name, value in initial.items() if name != "tax_id"}
		self.row = _mapping_row(
			name="QBO-MAP-Customer-1",
			erpnext_doctype="Customer",
			erpnext_name="Acme",
			owned_fields=json.dumps(baseline),
			sync_token="1",
		)
		self.accounted = dict(self.copy(baseline), tax_id=WALK_NEVER)
		self.token = 1
		self.history = [self._payload(initial)]
		self.shown = self.history[0]
		self.writes = []
		metas = {
			"Customer": _typed_meta(
				[
					_field("customer_name", "Data"),
					_field("payment_terms", "Link"),
					_field("tax_id", "Data"),
					_field("website", "Data"),
					_field("credit_limit", "Currency"),
					_field("exchange_factor", "Float", 9),
					_field("terms", "Table", options="Walk Term"),
				]
			),
			"Walk Term": _typed_meta([_field("credit_days", "Int"), _field("invoice_portion", "Percent")]),
		}
		monkeypatch.setattr(frappe, "get_meta", lambda doctype: metas[doctype], raising=False)
		monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda et, payload, settings: ("Customer", self.copy(payload["values"])))
		monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: [])
		monkeypatch.setattr(mapping, "get_mapping", lambda *args: self.row)
		monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
		monkeypatch.setattr(mapping, "_ensure_group_parent", self._never("promoted a parent"))
		monkeypatch.setattr(mapping, "save_mapping", self._never("called save_mapping"))
		monkeypatch.setattr(mapping, "save_manual_review_mapping", self._never("re-parked the mapping"))
		monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
		monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: self.doc, raising=False)
		monkeypatch.setattr(frappe.db, "set_value", self._set_value, raising=False)
		assert mapping.number_precisions("Customer")["credit_limit"] == WALK_DECIMALS["credit_limit"]
		assert mapping.number_precisions("Customer")["exchange_factor"] == WALK_DECIMALS["exchange_factor"]

	def _never(self, what):
		def fail(*args, **kwargs):
			raise AssertionError(f"seed {self.seed}: the linked path {what}")

		return fail

	def _payload(self, values):
		return {
			"Id": "1",
			"SyncToken": str(self.token),
			"MetaData": {"LastUpdatedTime": f"2026-11-01T00:{self.token // 60:02d}:{self.token % 60:02d}+00:00"},
			"values": self.copy(values),
		}

	def _set_value(self, doctype, name, updates, **kwargs):
		assert (doctype, name) == ("QuickBooks Sync Mapping", self.row.name)
		self.writes.append(dict(updates))
		for fieldname, value in updates.items():
			setattr(self.row, fieldname, value)

	# ---- what the walk believes

	def note(self):
		if self.row.conflict_status != "QuickBooks Changed":
			return {}
		stored = self.mapping.stored_changes(self.row)
		return {**stored["fields"], **stored["tables"]}

	def cursor(self):
		return int(self.row.sync_token)

	def check(self, where):
		"""The field invariant, against the newest payload the mapping has been shown."""
		note = self.note()
		for fieldname, said in self.shown["values"].items():
			held = getattr(self.doc, fieldname)
			accounted = self.accounted[fieldname]
			context = f"seed {self.seed}, {where}: {fieldname} record={held!r} quickbooks={said!r}"
			if fieldname in note:
				flagged_value = note[fieldname].get("qbo")
				assert flagged_value == json.loads(json.dumps(said)), f"{context}: the flag holds {flagged_value!r}"
				continue
			if _walk_same(fieldname, held, said):
				self.accounted[fieldname] = self.copy(said)
				continue
			if said is None and (accounted is WALK_NEVER or _walk_blank(accounted)):
				continue  # QuickBooks says nothing and never said anything
			assert accounted is not WALK_NEVER and _walk_same(fieldname, accounted, said), (
				f"{context}: differs, is not flagged, and QuickBooks no longer says {accounted!r}"
			)

	# ---- the steps

	def quickbooks_edits(self):
		fieldname = self.rng.choice(list(WALK_POOLS))
		values = self.copy(self.history[-1]["values"])
		values[fieldname] = self.copy(self.rng.choice(WALK_POOLS[fieldname]))
		self.token += 1
		self.history.append(self._payload(values))

	def erpnext_edits(self):
		fieldname = self.rng.choice(list(WALK_POOLS))
		setattr(self.doc, fieldname, self.copy(self.rng.choice(WALK_POOLS[fieldname])))

	def sync(self, payload, **flags):
		before_row = dict(vars(self.row))
		before_doc = {name: self.copy(getattr(self.doc, name)) for name in WALK_POOLS}
		before_cursor = self.cursor()
		# Does this pass compare at all? Only if QuickBooks moved past the cursor, the
		# mapping is flagged, or the caller asked for the full comparison.
		compares = bool(
			flags.get("recheck")
			or int(payload["SyncToken"]) > before_cursor
			or self.row.conflict_status == "QuickBooks Changed"
		)
		self.writes.clear()
		result = self.mapping.upsert_entity("Customer", self.copy(payload), SETTINGS, **flags)
		where = f"sync token {payload['SyncToken']} {flags}"
		assert self.doc.saves == [] and self.doc.sets == [], f"seed {self.seed}, {where}: a sync wrote the record"
		assert {name: getattr(self.doc, name) for name in WALK_POOLS} == before_doc
		assert result["action"] in ("unchanged", "flagged", "would_flag"), result
		assert self.cursor() >= before_cursor, f"seed {self.seed}, {where}: the cursor went backwards"
		if flags.get("preview"):
			assert self.writes == [], f"seed {self.seed}, {where}: a preview wrote the mapping"
			return result
		if int(payload["SyncToken"]) < int(self.shown["SyncToken"]):
			assert result.get("stale") is True and result["action"] == "unchanged", f"seed {self.seed}, {where}: {result}"
			assert dict(vars(self.row)) == before_row, f"seed {self.seed}, {where}: a stale payload changed the mapping"
			return result
		self.shown = payload
		if not compares:
			assert result["action"] == "unchanged" and self.writes == [], f"seed {self.seed}, {where}: {result}"
			return result
		assert (result["action"] == "flagged") == (self.row.conflict_status == "QuickBooks Changed"), result
		self.check(where)
		return result

	def keep(self):
		note = self.note()
		before_cursor = self.cursor()
		self.mapping.keep_erpnext_values("Customer", "1")
		assert self.doc.saves == [] and self.doc.sets == [], f"seed {self.seed}: Keep wrote the record"
		for fieldname, entry in note.items():
			self.accounted[fieldname] = self.copy(entry.get("qbo"))
		self._after_answer("keep", before_cursor)

	def use(self):
		note = self.note()
		chosen = self.rng.sample(sorted(note), self.rng.randint(1, len(note)))
		before_cursor = self.cursor()
		before = {name: self.copy(getattr(self.doc, name)) for name in WALK_POOLS}
		self.mapping.use_qbo_values("Customer", "1", fields=chosen)
		assert sorted(self.doc.sets) == sorted(chosen), f"seed {self.seed}: Use set {self.doc.sets}, asked {chosen}"
		assert len(self.doc.saves) == 1
		for fieldname in WALK_POOLS:
			if fieldname in chosen:
				assert getattr(self.doc, fieldname) == note[fieldname].get("qbo")
			else:
				assert getattr(self.doc, fieldname) == before[fieldname], f"seed {self.seed}: Use touched {fieldname}"
		for fieldname, entry in note.items():
			self.accounted[fieldname] = self.copy(entry.get("qbo"))
		self.doc.saves.clear()
		self.doc.sets.clear()
		self._after_answer(f"use {chosen}", before_cursor)

	def _after_answer(self, what, before_cursor):
		assert self.row.conflict_status == "Clean" and not self.row.qbo_changes, f"seed {self.seed}: {what} left the flag"
		assert self.cursor() >= before_cursor, f"seed {self.seed}: {what} moved the cursor backwards"
		assert self.cursor() == int(self.shown["SyncToken"]), f"seed {self.seed}: {what} left the cursor behind what was shown"
		self.check(what)
		# The payload that was answered is settled: syncing it again, any way, writes nothing.
		for flags in ({}, {"recheck": True}):
			result = self.sync(self.shown, **flags)
			assert result["action"] == "unchanged" and self.writes == [], f"seed {self.seed}: {what}, then {flags}: {result}"

	def step(self):
		roll = self.rng.random()
		flagged = self.row.conflict_status == "QuickBooks Changed"
		if roll < 0.24:
			self.quickbooks_edits()
		elif roll < 0.40:
			self.erpnext_edits()
		elif roll < 0.66:
			self.sync(self.history[-1], **({"recheck": True} if self.rng.random() < 0.3 else {}))
		elif roll < 0.76:
			# Any payload ever fetched, in any order: an older one is a stale replay, one
			# between the cursor and the newest is an ordinary catch-up.
			self.sync(self.rng.choice(self.history), **({"recheck": True} if self.rng.random() < 0.6 else {}))
		elif roll < 0.82:
			self.sync(self.history[-1], preview=True, recheck=self.rng.random() < 0.5)
		elif roll < 0.91 and flagged:
			self.keep()
		elif flagged:
			self.use()
		else:
			self.sync(self.history[-1])


@pytest.mark.parametrize("seed", WALK_SEEDS)
def test_random_walk_keeps_the_link_only_invariant(monkeypatch, seed):
	walk = _Walk(monkeypatch, seed)
	for _ in range(WALK_STEPS):
		walk.step()
	# Every walk exercised the interesting paths at least once across its steps.
	assert walk.token > 20


# ---------------------------------------------------------------------------
# Wiring that fails silently when it is wrong
# ---------------------------------------------------------------------------


def test_the_mapping_doctype_carries_the_flag_and_its_two_columns():
	"""The doctype is this app's own, so the new Select value and the two columns live in
	its JSON. A value added to a Select is safe (rows are only frozen by a value taken away);
	no ``default`` on the new columns, because on a normal doctype a default would be written
	into all ~20,000 existing rows by the ALTER."""
	mapping = json.loads(_read(MAPPING_JSON))
	fields = {field["fieldname"]: field for field in mapping["fields"]}
	options = fields["conflict_status"]["options"].split("\n")
	assert "QuickBooks Changed" in options
	for kept in ("Clean", "Conflict", "Ignored", "Pending Review"):
		assert kept in options, f"{kept} is in use on production rows; removing it freezes them"
	for fieldname in ("qbo_changes", "settled_qbo_values"):
		assert fields[fieldname]["fieldtype"] == "Long Text"
		assert fields[fieldname].get("read_only") == 1
		assert "default" not in fields[fieldname]
		assert fieldname in mapping["field_order"]
	assert len(mapping["field_order"]) == len(mapping["fields"])
	assert mapping["modified"] >= "2026-11-11", "an un-bumped doctype JSON never re-imports"
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import constants

	assert constants.QBO_CHANGED == "QuickBooks Changed"


def test_the_page_offers_the_filter_and_the_three_actions_without_a_new_route():
	js = _read(PAGE_JS)
	assert '["QuickBooks Changed", __("QuickBooks changed")]' in js
	assert 'const QBO_CHANGED = "QuickBooks Changed";' in js
	for endpoint in REVIEW_ENDPOINTS:
		assert js.count(f'method: "{endpoint}"') == 1, f"{endpoint} goes through the one chunked runner"
	assert "view: state.txn.view," in js
	assert 'data-txn-filter="view"' in js
	assert "__(\"ERPNext has\")" in js and "__(\"QuickBooks has\")" in js
	# Review requests are chunked POSTs through frappe.call (POST is its default; none sets a type).
	chunk = re.search(r"const REVIEW_CHUNK = (\d+);", js)
	assert chunk and 1 <= int(chunk.group(1)) <= 25
	assert 'type: "GET"' not in js
	# The flagged views are filters, not routes: Back and Forward still move between the two
	# tabs and nothing else (tests/test_quickbooks_matching.py holds the count at 4).
	assert js.count("frappe.set_route(") == 4
	assert "history.pushState" not in js and "history.replaceState" not in js
	view = js[js.index("function showTxnView(view) {") :]
	view = view[: view.index("\n\t}\n")]
	assert "set_route" not in view
	# Every value from the server is escaped before it reaches the DOM.
	block = js[js.index("function changesRow(row, index, scope, columns) {") : js.index('$root.on("click", "[data-review]"')]
	assert "shown(field.erpnext)" in block and "shown(field.qbo)" in block
	assert "esc(field.label)" in block and "esc(issue)" in block
	assert "const shown = (value) =>" in js and "esc(value));" in js


def test_the_open_conflict_counts_include_the_new_flag():
	"""Two dashboards counted ``conflict_status = 'Conflict'``, which the sync no longer
	writes. Left alone they would read 0 while flags piled up: the failure that passes."""
	cards = json.loads(_read(APP / "fixtures" / "number_card.json"))
	card = next(card for card in cards if card["name"] == "QuickBooks Open Conflicts")
	assert json.loads(card["filters_json"]) == [
		["QuickBooks Sync Mapping", "conflict_status", "in", ["Conflict", "QuickBooks Changed"], False]
	]
	snapshots = _read(APP / "kpi_dashboards" / "snapshots.py")
	assert "conflict_status in ('Conflict', 'QuickBooks Changed')" in snapshots
	assert "conflict_status='Conflict'" not in snapshots


def test_the_dashboard_no_longer_promises_an_overwrite_or_a_silent_retry():
	js = _read(
		APP / "quickbooks_online" / "page" / "quickbooks_online_dashboard" / "quickbooks_online_dashboard.js"
	)
	assert "Run overwrite resync" not in js
	assert "restart_by_hand" in js
	assert "It does not save over any linked record." in js


def test_docs_and_changelog_explain_the_rule():
	readme = _read(APP / "quickbooks_online" / "README.md")
	assert "## The sync links, it never overwrites (v1.800.0)" in readme
	assert "#the-sync-links-it-never-overwrites-v18000" in readme
	assert "QuickBooks Changed" in readme
	changelog = _read(REPO / "CHANGELOG.md")
	assert "retry_failed_syncs" in changelog and "QuickBooks Changed" in changelog


# ---------------------------------------------------------------------------
# Rehearsal findings: ERPNext reprices a built line, and a write trusted its own save
# ---------------------------------------------------------------------------
#
# The mirror rehearsal's first Refresh draft rebuilt a Sales Invoice whose QuickBooks
# invoice has eight zero-amount lines, reported "1 rebuilt from QuickBooks", set the mapping
# Clean -- and left all eight lines at the Item's selling price, 8 x the price too high.
# ERPNext v16 fills a line's missing ``price_list_rate`` on save and then replaces a rate of
# 0 with it. Two things are pinned: every line a mapper builds carries the key, and a
# person's write checks its own result instead of trusting the save.
#
# The stub cannot reprice anything, so the first is an assertion on the key (as
# ``test_chemical_billing`` does it), and the second uses a record double whose ``save``
# does to the lines what ERPNext did.


ZERO_RATE_LINES = [
	# Qty 2 at UnitPrice 0: the shape that was repriced.
	{"Amount": 0, "Description": "included", "{detail}": {"ItemRef": {"value": "11"}, "Qty": 2, "UnitPrice": 0}},
	# No Qty, no UnitPrice, Amount 0: one unit at nothing.
	{"Amount": 0, "Description": "courtesy", "{detail}": {"ItemRef": {"value": "11"}}},
	# Ordinary lines: below and above any list price.
	{"Amount": 555.0, "Description": "labor", "{detail}": {"ItemRef": {"value": "11"}, "Qty": 3, "UnitPrice": 185}},
	{"Amount": 6.82, "Description": "chemicals", "{detail}": {"ItemRef": {"value": "11"}}},
]


def _lines(detail_key):
	return {"Line": [{(detail_key if key == "{detail}" else key): value for key, value in line.items()} for line in ZERO_RATE_LINES]}


def _mappers(monkeypatch):
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	monkeypatch.setattr(mapping, "_linked_name", lambda entity, doctype, qbo_id: "SERVICE-CALL" if qbo_id else None)
	monkeypatch.setattr(mapping, "_txn_currency", lambda payload, settings: ("USD", 1))
	monkeypatch.setattr(mapping, "_resolve_customer_ref", lambda qbo_id: ("Acme Supply", None))
	monkeypatch.setattr(mapping, "_company_value", lambda settings, fieldname: "Ledger - MC")
	monkeypatch.setattr(mapping, "_default_price_list", lambda selling=True: "Standard Selling")
	monkeypatch.setattr(mapping, "_sales_passthrough_charges", lambda payload: [])
	monkeypatch.setattr(mapping, "_sales_charges", lambda payload, settings: [])
	monkeypatch.setattr(mapping, "_purchase_charges", lambda payload, settings: [])
	monkeypatch.setattr(mapping, "_has_field", lambda doctype, fieldname: False)
	return mapping


@pytest.mark.parametrize(
	"builder, detail_key",
	[("_sales_items", "SalesItemLineDetail"), ("_purchase_items", "ItemBasedExpenseLineDetail")],
)
def test_every_built_item_line_carries_its_rate_as_the_price_list_rate(monkeypatch, builder, detail_key):
	"""Sell side and buy side: the repricing is in the controller both share. A line at 0
	must carry a 0, not a missing key -- ERPNext fills a None and leaves a 0 alone."""
	mapping = _mappers(monkeypatch)

	items = getattr(mapping, builder)(_lines(detail_key))

	assert [item["rate"] for item in items] == [0, 0, 185, 6.82]
	for item in items:
		assert "price_list_rate" in item, item
		assert item["price_list_rate"] is not None
		assert item["price_list_rate"] == item["rate"], item
		assert "discount_amount" not in item and "discount_percentage" not in item, "no discount is invented here either"


@pytest.mark.parametrize(
	"mapper, detail_key, doctype",
	[
		("_map_sales_invoice", "SalesItemLineDetail", "Sales Invoice"),
		("_map_sales_receipt", "SalesItemLineDetail", "Sales Invoice"),
		("_map_quotation", "SalesItemLineDetail", "Quotation"),
		("_map_purchase_invoice", "ItemBasedExpenseLineDetail", "Purchase Invoice"),
		("_map_purchase_order", "ItemBasedExpenseLineDetail", "Purchase Order"),
	],
)
def test_every_document_built_from_item_lines_ignores_pricing_rules(monkeypatch, mapper, detail_key, doctype):
	"""All five mappers that build item lines: the lines are priced by ``_priced_line`` and
	the document tells ERPNext not to apply a Pricing Rule to an import."""
	mapping = _mappers(monkeypatch)
	payload = dict(_lines(detail_key), Id="900", TxnDate="2026-11-02", CustomerRef={"value": "1"}, VendorRef={"value": "2"})

	mapped_doctype, values = getattr(mapping, mapper)(payload, SETTINGS)

	assert mapped_doctype == doctype
	assert values["ignore_pricing_rule"] == 1
	assert len(values["items"]) == 4
	assert all(item["price_list_rate"] == item["rate"] for item in values["items"])


def test_the_build_only_keys_are_never_compared_flagged_or_settled():
	"""``price_list_rate`` and ``ignore_pricing_rule`` tell ERPNext how to build a document.
	They are not QuickBooks data: a draft holding the Item's list price beside the same rate,
	or built before v1.800.0 with pricing rules on, is not something QuickBooks changed."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	assert mapping.BUILD_ONLY_ROW_FIELDS == ("price_list_rate",) and mapping.BUILD_ONLY_FIELDS == ("ignore_pricing_rule",)
	held = [types.SimpleNamespace(item_code="SERVICE-CALL", qty=1.0, rate=400.0, price_list_rate=250.0)]
	incoming = [{"item_code": "SERVICE-CALL", "qty": 1, "rate": 400.0, "price_list_rate": 400.0}]
	assert mapping._table_differences(held, incoming) == []
	# The rate itself still counts.
	assert mapping._table_differences(held, [dict(incoming[0], rate=0.0, price_list_rate=0.0)]) == [
		{"row": 1, "fields": {"rate": {"erpnext": 400.0, "qbo": 0.0}}}
	]
	# A snapshot stored before the key existed says the same thing as the rows mapped now.
	before = [{"item_code": "SERVICE-CALL", "qty": 1, "rate": 400.0}]
	assert mapping._same_qbo_value(before, incoming)
	assert not mapping._same_qbo_value(before, [dict(incoming[0], rate=401.0)])

	doc = _doc(doctype="Sales Invoice", name="ACC-SINV-2026-00042", customer="Acme Supply", ignore_pricing_rule=0, items=held)
	values = {"customer": "Acme Supply", "ignore_pricing_rule": 1, "items": incoming}
	row = _mapping_row(owned_fields=json.dumps({"customer": "Acme Supply", "items": before}))
	assert mapping._linked_differences(doc, values, row) == {"fields": {}, "tables": {}}
	assert mapping._linked_differences(doc, values, _mapping_row(owned_fields="{}")) == {"fields": {}, "tables": {}}
	assert mapping._settle_unflagged(row, "Sales Invoice", values) is None, "nothing to settle: no write"
	assert mapping._settled_from("Sales Invoice", values) == {"customer": "Acme Supply"}


def test_a_linked_draft_built_before_the_key_existed_is_not_flagged_by_a_resync(monkeypatch):
	"""Through ``upsert_entity``: every linked transaction's stored rows lack
	``price_list_rate`` on the day this deploys. A resync must not read that as a change,
	and must not write a mapping row for it."""
	rows = [{"item_code": "SERVICE-CALL", "qty": 1, "rate": 400.0}]
	values = {"customer": "Acme Supply", "ignore_pricing_rule": 1, "items": [dict(rows[0], price_list_rate=400.0)]}
	mapping, writes, doc, _row = _invoice_with_typed_fields(
		monkeypatch,
		values=values,
		record={
			"customer": "Acme Supply",
			"ignore_pricing_rule": 0,
			"items": [types.SimpleNamespace(item_code="SERVICE-CALL", qty=1.0, rate=400.0, price_list_rate=250.0)],
		},
		owned={"customer": "Acme Supply", "items": rows},
	)

	result = mapping.upsert_entity("Invoice", {"Id": "900", "SyncToken": "2"}, SETTINGS, recheck=True)

	assert result["action"] == "unchanged"
	assert doc.saves == [] and doc.sets == []
	assert writes["set_value"] == [] and writes["save_mapping"] == []


REBUILT_LINES = [
	{"item_code": "SERVICE-CALL", "qty": 1, "rate": 0.0, "amount": 0.0, "price_list_rate": 0.0},
	{"item_code": "SERVICE-LABOR", "qty": 2, "rate": 50.0, "amount": 100.0, "price_list_rate": 50.0},
	{"item_code": "SERVICE-CALL", "qty": 1, "rate": 0.0, "amount": 0.0, "price_list_rate": 0.0},
]
OLD_LINES = [{"item_code": "SERVICE-CALL", "qty": 1, "rate": 250.0, "amount": 250.0}]


def _rebuilt_draft(monkeypatch, *, reprice):
	"""A flagged draft and the real ``save_mapping``. With ``reprice`` the record double's
	``save`` does what ERPNext v16 did on the mirror: a line at 0 comes back at 250."""
	payload = {"Id": "900", "SyncToken": "4", "MetaData": {"LastUpdatedTime": "2026-11-10T10:20:00-08:00"}}
	values = {
		"customer": "Acme Supply",
		"posting_date": "2026-11-02",
		"due_date": None,
		"ignore_pricing_rule": 1,
		"items": [dict(line) for line in REBUILT_LINES],
	}
	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00042",
		docstatus=0,
		customer="Acme Supply",
		posting_date="2026-11-02",
		ignore_pricing_rule=0,
		items=[types.SimpleNamespace(**line) for line in OLD_LINES],
	)

	def save(**kwargs):
		doc.saves.append(kwargs)
		if reprice:
			doc.items = [
				dict(line, rate=250.0, amount=250.0, price_list_rate=250.0) if not line["rate"] else dict(line)
				for line in doc.items
			]

	doc.save = save
	row = _mapping_row(
		name="QBO-MAP-Invoice-900",
		erpnext_doctype="Sales Invoice",
		erpnext_name=doc.name,
		match_status="Created",
		conflict_status="QuickBooks Changed",
		sync_token="3",
		qbo_changes=json.dumps({"sync_token": "4", "fields": {}, "tables": {"items": {"rows": []}}, "issues": []}),
		owned_fields=json.dumps({"customer": "Acme Supply", "posting_date": "2026-11-02", "items": OLD_LINES}),
	)
	row.is_new = lambda: False
	row.save = lambda **kwargs: None
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	writes = {"set_value": []}
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: row)
	monkeypatch.setattr(mapping, "_raw_payload_dict", lambda entity_type, qbo_id: dict(payload))
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Sales Invoice", dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: [])
	monkeypatch.setattr(mapping, "_heal_invalid_urls", lambda doc: [])
	monkeypatch.setattr(frappe, "get_single", lambda doctype: SETTINGS, raising=False)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name=None: True, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	monkeypatch.setattr(
		frappe.db, "set_value", lambda *args, **kwargs: writes["set_value"].append((args, kwargs)), raising=False
	)
	return mapping, writes, doc, row, values


def test_refresh_draft_that_erpnext_repriced_stays_flagged_with_what_still_differs(monkeypatch):
	"""The defect itself. The rebuild saved, the saved draft still holds 250 where
	QuickBooks has 0, and the old code reported success and set the mapping Clean."""
	mapping, writes, doc, row, _values = _rebuilt_draft(monkeypatch, reprice=True)

	result = mapping.refresh_linked_draft("Invoice", "900")

	assert doc.saves == [{"ignore_permissions": True}], "the rebuild is kept: most of it is right"
	assert "ignore_pricing_rule" in doc.sets and doc.ignore_pricing_rule == 1
	assert result["still_differs"] == ["items"]
	assert result["applied"] == ["customer", "posting_date"], "not the table, and not a build-only key"
	differing = result["remaining"]["tables"]["items"]["rows"]
	assert differing == [
		{"row": 1, "fields": {"rate": {"erpnext": 250.0, "qbo": 0.0}, "amount": {"erpnext": 250.0, "qbo": 0.0}}},
		{"row": 3, "fields": {"rate": {"erpnext": 250.0, "qbo": 0.0}, "amount": {"erpnext": 250.0, "qbo": 0.0}}},
	]

	# save_mapping first set it Clean with a fresh snapshot; the last write is the flag.
	((doctype, name, updates), kwargs), = writes["set_value"]
	assert (doctype, name) == ("QuickBooks Sync Mapping", row.name) and kwargs == {"update_modified": False}
	_replay(row, writes)
	assert row.conflict_status == "QuickBooks Changed", "never Clean on a record that still disagrees"
	note = json.loads(row.qbo_changes)
	assert note["after_write"] == "refresh" and note["issues"] == []
	assert note["sync_token"] == "4", "the payload the draft was rebuilt from"
	assert note["tables"]["items"]["rows"] == differing
	assert note["tables"]["items"]["qbo"] == REBUILT_LINES, "Keep ERPNext settles what QuickBooks said"
	assert "no_baseline" not in note["tables"]["items"]
	# What still differs has no reference left, so no later pass can call it settled.
	owned = json.loads(row.owned_fields)
	assert "items" not in owned and owned["customer"] == "Acme Supply"
	assert "items" not in json.loads(row.settled_qbo_values or "{}")


def test_a_later_sync_cannot_clear_the_flag_a_repriced_rebuild_left(monkeypatch):
	"""Why the reference is dropped. With the rebuilt rows left in ``owned_fields`` the next
	pass would read "QuickBooks still says what it said" and skip the table: no difference,
	flag cleared, draft still 500 too high. With no reference the table is compared with
	the record."""
	mapping, writes, doc, row, values = _rebuilt_draft(monkeypatch, reprice=True)
	mapping.refresh_linked_draft("Invoice", "900")
	_replay(row, writes)

	again = mapping._linked_differences(doc, values, row)
	assert sorted(again["tables"]) == ["items"] and again["fields"] == {}

	# The hole: the same comparison with the rows kept as a reference finds nothing.
	trusting = _mapping_row(owned_fields=json.dumps({"customer": "Acme Supply", "items": REBUILT_LINES}))
	assert mapping._linked_differences(doc, values, trusting) == {"fields": {}, "tables": {}}

	# Keep ERPNext is the way out: it settles the rows, and then the table is left alone.
	mapping.keep_erpnext_values("Invoice", "900")
	_replay(row, writes)
	assert row.conflict_status == "Clean" and row.qbo_changes is None
	assert mapping._linked_differences(doc, values, row) == {"fields": {}, "tables": {}}


def test_refresh_draft_that_holds_every_value_is_clean(monkeypatch):
	mapping, writes, doc, row, _values = _rebuilt_draft(monkeypatch, reprice=False)

	result = mapping.refresh_linked_draft("Invoice", "900")

	assert result["still_differs"] == [] and result["remaining"] == {"fields": {}, "tables": {}}
	assert result["applied"] == ["customer", "items", "posting_date"]
	assert writes["set_value"] == [], "save_mapping is the only mapping write"
	assert row.conflict_status == "Clean" and row.qbo_changes is None and row.sync_token == "4"
	assert json.loads(row.owned_fields)["items"] == REBUILT_LINES
	assert doc.items == REBUILT_LINES, "each rebuilt line carries its price_list_rate onto the draft"


def test_use_quickbooks_values_keeps_the_flag_on_a_value_erpnext_did_not_hold(monkeypatch):
	"""The same check on the master side. Three values are written; the record's own
	validation puts one back. That one is not applied, not settled and still flagged."""
	mapping, _frappe, writes, doc, row = _flagged_customer(monkeypatch)

	def save(**kwargs):
		doc.saves.append(kwargs)
		doc.payment_terms = "Net 45"

	doc.save = save

	result = mapping.use_qbo_values("Customer", "1", fields=["customer_name", "payment_terms", "tax_id"])

	assert result["applied"] == ["customer_name", "tax_id"] and result["kept"] == []
	assert result["still_differs"] == ["payment_terms"]
	((_doctype, _name, updates), _kwargs), = writes["set_value"]
	assert updates["conflict_status"] == "QuickBooks Changed"
	note = json.loads(updates["qbo_changes"])
	assert note["after_write"] == "use" and note["sync_token"] == "6"
	assert note["fields"] == {"payment_terms": {"erpnext": "Net 45", "qbo": "Net 15"}} and note["tables"] == {}
	assert json.loads(updates["owned_fields"]) == {"customer_name": "Acme Supply Co", "tax_id": "99-0000001"}
	assert json.loads(updates["settled_qbo_values"]) == {"customer_name": "Acme Supply Co", "tax_id": "99-0000001"}


def test_use_quickbooks_values_keeps_the_flag_on_a_clear_that_did_not_take(monkeypatch):
	mapping, _frappe, writes, doc, row = _flagged_customer(monkeypatch)
	note = json.loads(row.qbo_changes)
	note["fields"] = {"payment_terms": {"erpnext": "Net 45", "qbo": None, "cleared": True}}
	row.qbo_changes = json.dumps(note)

	def save(**kwargs):
		doc.saves.append(kwargs)
		doc.payment_terms = "Net 45"

	doc.save = save

	result = mapping.use_qbo_values("Customer", "1", fields=["payment_terms"])

	assert result["applied"] == [] and result["still_differs"] == ["payment_terms"]
	updates = writes["set_value"][0][0][2]
	assert updates["conflict_status"] == "QuickBooks Changed"
	assert json.loads(updates["qbo_changes"])["fields"] == {
		"payment_terms": {"erpnext": "Net 45", "qbo": None, "cleared": True}
	}


def test_the_check_after_a_write_leaves_out_what_erpnext_composes_for_itself(monkeypatch):
	"""A Payment Entry writes its own remarks on every save unless ``custom_remarks`` is
	ticked. That is not the draft disagreeing with QuickBooks, and it must not keep every
	refreshed Payment Entry flagged. The same text on a Sales Invoice is the mapper's own
	and is checked."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	assert mapping.REWRITTEN_ON_SAVE == {"Payment Entry": ("remarks",)}
	doc = _doc(doctype="Payment Entry", name="ACC-PAY-2026-00007", remarks="Amount USD 10 received", paid_amount=10.0)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	values = {"remarks": "Imported from QuickBooks Online payment 7", "paid_amount": 10, "reference_no": None}

	assert mapping._differences_after_write("Payment Entry", doc.name, values) == {"fields": {}, "tables": {}}
	doc.paid_amount = 12.0
	assert sorted(mapping._differences_after_write("Payment Entry", doc.name, values)["fields"]) == ["paid_amount"]
	assert sorted(mapping._differences_after_write("Sales Invoice", doc.name, values)["fields"]) == [
		"paid_amount",
		"remarks",
	]


def test_the_check_after_a_write_does_not_hold_erpnexts_own_arithmetic_against_a_line(monkeypatch):
	"""ERPNext computes a line's amount as qty times the rate it stored. 3 at 33.33333 is
	100.00 in QuickBooks and 99.99 here: the quantity and the rate both took, and no rebuild
	can change the cent. It is not "still differs". A line whose rate did not take keeps
	its amount in the report, and a table with no qty / rate columns keeps every cell."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	metas = {
		"Sales Invoice": _typed_meta(
			[_field("items", "Table", options="Sales Invoice Item"), _field("taxes", "Table", options="Sales Taxes and Charges")]
		),
		"Sales Invoice Item": _typed_meta(
			[_field("qty", "Float"), _field("rate", "Currency", 2), _field("amount", "Currency", 2)]
		),
		"Sales Taxes and Charges": _typed_meta([_field("amount", "Currency", 2)]),
	}
	monkeypatch.setattr(frappe, "get_meta", lambda doctype: metas[doctype], raising=False)
	doc = _doc(
		doctype="Sales Invoice",
		name="ACC-SINV-2026-00042",
		items=[
			types.SimpleNamespace(qty=3.0, rate=33.33, amount=99.99),
			types.SimpleNamespace(qty=1.0, rate=250.0, amount=250.0),
		],
		taxes=[types.SimpleNamespace(amount=7.01)],
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name=None: doc, raising=False)
	values = {
		"items": [{"qty": 3, "rate": 33.33333, "amount": 100.0}, {"qty": 1, "rate": 0.0, "amount": 0.0}],
		"taxes": [{"amount": 7.0}],
	}

	remaining = mapping._differences_after_write("Sales Invoice", doc.name, values)

	assert remaining["tables"]["items"]["rows"] == [
		{"row": 2, "fields": {"rate": {"erpnext": 250.0, "qbo": 0.0}, "amount": {"erpnext": 250.0, "qbo": 0.0}}}
	]
	assert remaining["tables"]["taxes"]["rows"] == [{"row": 1, "fields": {"amount": {"erpnext": 7.01, "qbo": 7.0}}}]

	# Only the rounding left: nothing still differs.
	doc.items[1] = types.SimpleNamespace(qty=1.0, rate=0.0, amount=0.0)
	doc.taxes = [types.SimpleNamespace(amount=7.0)]
	assert mapping._differences_after_write("Sales Invoice", doc.name, values) == {"fields": {}, "tables": {}}
	# More rounding cents than the note's row cap: still nothing, not "and 5 more rows".
	doc.items = [types.SimpleNamespace(qty=3.0, rate=33.33, amount=99.99) for _line in range(30)]
	many = {"items": [{"qty": 3, "rate": 33.33333, "amount": 100.0} for _line in range(30)]}
	assert mapping.TABLE_DIFF_ROW_CAP < 30
	assert mapping._differences_after_write("Sales Invoice", doc.name, many) == {"fields": {}, "tables": {}}
	doc.items[28].rate, doc.items[28].amount = 250.0, 750.0
	late = mapping._differences_after_write("Sales Invoice", doc.name, many)["tables"]["items"]
	assert [row["row"] for row in late["rows"]] == [29] and late["more_rows"] == 0
	# The sync's own comparison is unchanged: it still reports the cent.
	assert mapping._table_differences(doc.items[:1], values["items"][:1], {"rate": 2, "amount": 2}) == [
		{"row": 1, "fields": {"amount": {"erpnext": 99.99, "qbo": 100.0}}}
	]


def test_the_page_is_told_which_fields_still_differ_and_never_sent_the_rows(monkeypatch):
	"""``review_many`` reports a saved-but-differing write as its own outcome, with one
	short label per field. The stored differences (whole child tables) stay on the server."""
	matching, frappe, events = _matching(monkeypatch)
	remaining = {
		"fields": {"customer": {"erpnext": "Acme Supply", "qbo": "Acme Supply Co"}},
		"tables": {"items": {"rows": [{"row": 1}, {"row": 3}, {"row": 4}], "more_rows": 5, "qbo": REBUILT_LINES}},
	}
	monkeypatch.setattr(
		matching,
		"refresh_linked_draft",
		lambda entity_type, qbo_id: {
			"mapping": "QBO-MAP-Invoice-" + qbo_id,
			"doctype": "Sales Invoice",
			"name": "ACC-SINV-2026-00042",
			"applied": ["posting_date"],
			"still_differs": ["customer", "items"] if qbo_id == "900" else [],
			"remaining": remaining if qbo_id == "900" else {"fields": {}, "tables": {}},
		},
	)
	fields = [_field("customer", "Link"), _field("items", "Table", options="Sales Invoice Item")]
	fields[0].label, fields[1].label = "Customer", "Items"
	monkeypatch.setattr(frappe, "get_meta", lambda doctype: _typed_meta(fields), raising=False)

	differing, clean = matching.review_many(
		[{"entity_type": "Invoice", "qbo_id": "900"}, {"entity_type": "Invoice", "qbo_id": "901"}],
		matching.refresh_draft,
	)

	assert differing["ok"] is True and differing["action"] == "still_differs"
	assert differing["still_differs"] == ["customer", "items"]
	assert differing["still_labels"] == ["Customer", "Items (8 rows)"]
	assert "remaining" not in differing and "remaining" not in clean
	assert clean["action"] == "refreshed" and clean["still_differs"] == [] and clean["still_labels"] == []
	assert events["commit"] == 2, "the rebuild is committed either way; it is the flag that differs"

	monkeypatch.setattr(
		matching,
		"use_qbo_values",
		lambda entity_type, qbo_id, fields=None: {
			"mapping": "QBO-MAP-Customer-1",
			"doctype": "Customer",
			"name": "Acme Supply",
			"applied": [],
			"kept": [],
			"still_differs": ["customer"],
			"remaining": {"fields": remaining["fields"], "tables": {}},
		},
	)
	(used,) = matching.review_many([{"entity_type": "Customer", "qbo_id": "1"}], matching.use_quickbooks_values)
	assert used["action"] == "still_differs" and used["still_labels"] == ["Customer"]


def test_change_view_says_when_the_note_is_what_a_write_left(monkeypatch):
	matching, frappe, _events = _matching(monkeypatch)
	monkeypatch.setattr(frappe.db, "get_value", lambda *args, **kwargs: None, raising=False)
	row = types.SimpleNamespace(
		conflict_status="QuickBooks Changed",
		erpnext_doctype="Sales Invoice",
		erpnext_name="ACC-SINV-2026-00042",
		qbo_changes=json.dumps({"after_write": "refresh", "fields": {}, "tables": {"items": {"rows": [], "qbo": []}}}),
	)
	assert matching.change_view(row)["after_write"] == "refresh"
	row.qbo_changes = json.dumps({"fields": {"customer": {"erpnext": "A", "qbo": "B"}}})
	assert matching.change_view(row)["after_write"] is None


def test_the_page_reports_a_write_that_still_differs_and_labels_both_sides():
	js = _read(PAGE_JS)
	# Three outcomes: a saved-but-differing write is never counted with the clean ones.
	review = js[js.index("function runReview({ method, items, scope, title, verb, progress }) {") :]
	review = review[: review.index("\n\t}\n")]
	assert "const still = results.filter((r) => r.ok && (r.still_differs || []).length);" in review
	assert "const ok = results.filter((r) => r.ok && !(r.still_differs || []).length);" in review
	assert "saved, but still differ from QuickBooks" in review and "field(s) still differ" in review
	assert "if (failed.length || still.length) {" in review, "no green toast for a draft that still differs"
	assert "esc((r.still_labels || r.still_differs).join" in review
	# The refusal ERPNext gives for a draft it will not save carries record and party names.
	assert "${esc(r.error || \"\")}" in review
	assert review.count("r.error") == 1, "the one place an error is rendered, and it is escaped"
	# The note a write left has its own sentence over the differences.
	assert 'changes.after_write === "refresh"' in js and 'changes.after_write === "use"' in js
	assert "${esc(changesHead(changes, scope))}" in js

	# Narrow layout: every header row of the page is hidden under 900px, so each value says
	# which side it is from, and the row lines carry a legend in every layout.
	assert js.count('${sideLabel("erpnext")}') == 2 and js.count('${sideLabel("qbo")}') == 2
	assert '<span class="qbo-mt-changes-side">' in js
	assert '__("Rows that differ, shown as ERPNext has → QuickBooks has:")' in js
	css = _read(APP / "public" / "css" / "quickbooks_online" / "qbo_matching.css")
	side = css[css.index(".qbo-mt-changes-side {") :]
	assert "display: none;" in side[: side.index("}")]
	narrow = css[css.index("@media (max-width: 900px) {\n\t.qbo-mt-changes-side {") :]
	assert "display: inline;" in narrow[: narrow.index("}")]
	assert ".qbo-mt-table thead {\n\t\tdisplay: none;" in css, "the rule that hides the differences table's header too"


def test_the_changelog_and_readme_explain_the_repricing_and_the_check():
	changelog = _read(REPO / "CHANGELOG.md")
	assert "price_list_rate" in changelog and "still differ" in changelog
	readme = _read(APP / "quickbooks_online" / "README.md")
	assert "price_list_rate" in readme and "_differences_after_write" in readme


def test_it_is_wired_into_ci_on_a_pytest_step():
	ci = _read(REPO / ".github" / "workflows" / "ci.yml")
	assert "python -m pytest erpnext_enhancements/tests/test_quickbooks_link_only.py" in ci
