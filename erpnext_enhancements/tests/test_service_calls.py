"""Bench-free pytest suite for Service Calls (``sapphire_maintenance/service_calls.py`` and
``service_call_approval.py``, v1.778.0).

Nikolas, 2026-11-03: a one-off client without an agreement gets a Service Call that hangs off a
Service project, an Opportunity created at its FIRST status (never Closed Won on creation), a
three-task project template dated from the visit, an emailed or texted approval link, and a
Maintenance Record filed against the call instead of a Contract and Serial No. Pinned here:

* the intake planner: what is created, in what order, with what names, status and template;
* the rate copy: the matching Service Call Rate lands on the call once, and the pairs allowed;
* the dates: nothing is born Overdue, the visit task is on the visit's day;
* the approval state machine: tokens by shape, expiry, the transitions, the typed name cleaned,
  no ``.update(`` from a guest, guest endpoints POST-only and rate limited, no ``sid``;
* the invoice line builder: the flat rate as one line, hours times the hourly rate, parts at the
  Item's price, chemicals through the contract visits' billing, and a note for what could not
  be billed;
* the shape of the thing: the www controller's underscore filename, the hooks, the patches, the
  doctype placement, the Record's new link, and no figure anywhere in the repository's terms.

A plain pytest module with its own ``frappe`` stub, so CI runs it in a ``python -m pytest`` step of
its own (``python -m unittest`` cannot collect function tests).
"""

import ast
import datetime
import importlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))
APP = REPO_ROOT / "erpnext_enhancements"
MAINT = APP / "sapphire_maintenance"
CALLS_PY = MAINT / "service_calls.py"
APPROVAL_PY = MAINT / "service_call_approval.py"
PAGE_PY = APP / "www" / "service_call_approval.py"
PAGE_HTML = APP / "www" / "service-call-approval.html"
PAGE_JS = APP / "public" / "js" / "service_call_approval" / "service_call_approval.js"
EMAIL = APP / "templates" / "emails" / "sapphire_maintenance" / "service_call_approval.html"
HOOKS = APP / "hooks.py"
PATCHES = APP / "patches.txt"

TODAY = datetime.date(2026, 11, 3)
NOW = datetime.datetime(2026, 11, 3, 15, 0)


def _getdate(value=None):
	if value is None or value == "":
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _get_datetime(value=None):
	if value is None:
		return NOW
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime(value.year, value.month, value.day)
	return datetime.datetime.fromisoformat(str(value))


def install_frappe_stub():
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	limiter = types.ModuleType("frappe.rate_limiter")

	def flt(value=0, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	utils.cint = lambda value=0, *a, **k: int(flt(value))
	utils.getdate = _getdate
	utils.get_datetime = _get_datetime
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.add_days = lambda day, days: _getdate(day) + datetime.timedelta(days=int(days))
	utils.add_to_date = lambda when, days=0, **k: _get_datetime(when) + datetime.timedelta(days=days)
	utils.escape_html = lambda text: str(text).replace("<", "&lt;").replace(">", "&gt;")
	utils.get_url = lambda path="": "https://erp.example.test" + path
	utils.validate_email_address = lambda value: value if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or "") else ""
	limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
	frappe.utils = utils
	frappe.rate_limiter = limiter
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
		savepoint=lambda *a, **k: None,
		rollback=lambda *a, **k: None,
	)
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.get_meta = lambda *a, **k: None
	frappe.has_permission = lambda *a, **k: True
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire Fountains")
	frappe.session = types.SimpleNamespace(user="office@example.test")
	frappe.local = types.SimpleNamespace(request_ip="203.0.113.9")
	frappe.get_request_header = lambda name: "TestBrowser/1.0"
	frappe.get_app_path = lambda *parts: str(APP.joinpath(*parts[1:]))
	frappe.get_link_to_form = lambda doctype, name: name

	def throw(message, exc=Exception, title=None):
		raise exc(message)

	frappe.throw = throw
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.rate_limiter"] = limiter
	return frappe


@pytest.fixture()
def sc():
	frappe = install_frappe_stub()
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.sapphire_maintenance.service_call"):
			del sys.modules[name]
	module = importlib.import_module("erpnext_enhancements.sapphire_maintenance.service_calls")
	module.frappe = frappe
	return module


@pytest.fixture()
def approval(sc):
	module = importlib.import_module("erpnext_enhancements.sapphire_maintenance.service_call_approval")
	module.frappe = sc.frappe
	module.service_calls = sc
	return module


# ---------------------------------------------------------------------- rates


@pytest.mark.parametrize(
	("service_type", "kind", "ok"),
	[
		("Startup", "Flat", True),
		("Winterization", "Flat", True),
		("Maintenance Visit", "Flat", True),
		("Small Repair", "As-needed", True),
		("Small Repair", "Emergency", True),
		("Small Repair", "After-hours Emergency", True),
		("Small Repair", "Flat", False),
		("Startup", "Emergency", False),
		("Pump Swap", "Flat", False),
		("Startup", "Hourly", False),
	],
)
def test_rate_pairs_allowed(sc, service_type, kind, ok):
	assert (sc.rate_row_problem(service_type, kind) is None) is ok


def test_default_rate_kind_and_field(sc):
	assert sc.default_rate_kind("Small Repair") == "As-needed"
	assert sc.default_rate_kind("Startup") == "Flat"
	assert sc.rate_field("Small Repair") == "hourly_rate"
	assert sc.rate_field("Winterization") == "flat_rate"


class _Doc(dict):
	"""A dict that answers attribute reads too, like a Document."""

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None

	def __setattr__(self, key, value):
		self[key] = value

	def get(self, key, default=None):
		return dict.get(self, key, default)

	def set(self, key, value):
		self[key] = value


def test_copy_rates_stamps_the_matching_row_once(sc):
	asked = []

	def get_value(doctype, filters, field, **kw):
		asked.append((doctype, dict(filters), field))
		return 180.0 if filters["rate_kind"] == "As-needed" else None

	sc.frappe.db.get_value = get_value
	doc = _Doc(service_type="Small Repair", rate_kind="As-needed")
	sc.copy_rates(doc)
	assert doc["hourly_rate"] == 180.0
	assert asked == [("Service Call Rate", {"service_type": "Small Repair", "rate_kind": "As-needed", "disabled": 0}, "rate")]
	# A rate already on the call is never overwritten by a later table edit.
	sc.frappe.db.get_value = lambda *a, **k: 999.0
	sc.copy_rates(doc)
	assert doc["hourly_rate"] == 180.0
	# No row: the call is created without a figure, never with a guess.
	sc.frappe.db.get_value = lambda *a, **k: None
	flat = _Doc(service_type="Startup", rate_kind="Flat")
	sc.copy_rates(flat)
	assert "flat_rate" not in flat


def test_no_figure_in_the_repository_terms(sc):
	"""The repository is public: the terms name no dollar amount; rates come from the table."""
	for line in list(sc.TERMS_FLAT) + list(sc.TERMS_HOURLY):
		assert not re.search(r"\$\s?\d", line), line
		assert "/hr" not in line
	source = CALLS_PY.read_text(encoding="utf-8") + APPROVAL_PY.read_text(encoding="utf-8")
	assert not re.search(r"\$\s?\d{2,}", source)


# ---------------------------------------------------------------------- the opening status


def test_first_status_reads_the_site_options(sc):
	assert sc.first_status("Qualification\nNeeds Analysis\nLost\nClosed Won") == "Qualification"
	assert sc.first_status("\n Open\nQuotation") == "Open"
	assert sc.first_status("") == ""
	assert sc.first_status(None) == ""


def test_opening_status_comes_from_the_installed_meta(sc):
	sc.frappe.get_meta = lambda doctype: types.SimpleNamespace(get_field=lambda f: types.SimpleNamespace(options="Qualification\nClosed Won"))
	assert sc.opening_status() == "Qualification"
	sc.frappe.get_meta = lambda doctype: (_ for _ in ()).throw(RuntimeError("no meta"))
	assert sc.opening_status() == "Open"


# ---------------------------------------------------------------------- dates


def test_task_dates_from_a_future_visit(sc):
	rows = sc.task_dates(TODAY, datetime.date(2026, 11, 10))
	assert [r["subject"] for r in rows] == ["Approval received", "Service visit", "Invoice sent"]
	assert (rows[0]["start"], rows[0]["end"]) == (TODAY, datetime.date(2026, 11, 9))
	assert (rows[1]["start"], rows[1]["end"]) == (datetime.date(2026, 11, 10), datetime.date(2026, 11, 10))
	assert (rows[2]["start"], rows[2]["end"]) == (datetime.date(2026, 11, 10), datetime.date(2026, 11, 17))
	# Nothing ends before today, so nothing is born Overdue.
	assert all(r["end"] >= TODAY for r in rows)


def test_task_dates_visit_today_and_no_date_and_past(sc):
	today_rows = sc.task_dates(TODAY, TODAY)
	assert today_rows[0]["end"] == TODAY and today_rows[1]["start"] == TODAY
	bare = sc.task_dates(TODAY, None)
	assert bare[0]["start"] == TODAY
	assert bare[1]["start"] == TODAY + datetime.timedelta(days=3)
	assert bare[2]["end"] == TODAY + datetime.timedelta(days=11)
	past = sc.task_dates(TODAY, datetime.date(2026, 10, 20))
	assert past[0]["end"] == TODAY
	assert past[1]["start"] == datetime.date(2026, 10, 20)
	assert past[2]["start"] == TODAY


@pytest.mark.parametrize(
	("scheduled", "planned_end"),
	[
		(datetime.date(2026, 11, 4), datetime.date(2026, 11, 11)),  # visit tomorrow
		(datetime.date(2026, 11, 2), datetime.date(2026, 11, 10)),  # visit yesterday
		(None, datetime.date(2026, 11, 14)),  # no date
		(datetime.date(2027, 1, 20), datetime.date(2027, 1, 27)),  # far ahead
	],
)
def test_project_window_fits_every_template_task(sc, scheduled, planned_end):
	"""ERPNext copies each template Task at expected start + its offsets and Task's
	validate_parent_project_dates (16.52.1) refuses a new task ending after the project's end, so
	the Project is inserted wide and takes its real end after the tasks are re-dated."""
	start, insert_end, real_end = sc.project_window(TODAY, scheduled)
	assert real_end == planned_end
	assert start <= TODAY
	if scheduled:
		assert start <= sc.getdate(scheduled)
	for task in sc.TASKS:
		copied_end = sc.add_days(start, task["start"] + task["duration"])
		assert insert_end >= copied_end + datetime.timedelta(days=7), task["subject"]  # holiday shifts too
	assert insert_end >= real_end


def test_intake_writes_the_real_end_after_the_tasks(sc):
	writes = []
	made = []

	class Doc(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError:
				raise AttributeError(key) from None

		def insert(self, ignore_permissions=False):
			self["name"] = "PRJ-01000"
			made.append(dict(self))
			return self

	sc.frappe.get_doc = lambda values: Doc(values)
	sc.frappe.db.exists = lambda *a, **k: True
	sc.frappe.db.set_value = lambda doctype, name, field, value=None, **k: writes.append((doctype, name, field, value))
	sc.frappe.get_all = lambda *a, **k: [types.SimpleNamespace(name="TASK-3", subject="Invoice sent")]
	plan = dict(sc.plan_intake(_values(scheduled_date="2026-11-04"), TODAY, "Example Residence", "Qualification", "u"))
	project = sc._make_project(plan["project"], "CRM-OPP-1", "2026-11-04")
	assert made[0]["expected_end_date"] == datetime.date(2026, 12, 3)
	assert writes[-1] == ("Project", "PRJ-01000", "expected_end_date", datetime.date(2026, 11, 11))
	assert project.expected_end_date == datetime.date(2026, 11, 11)
	assert ("Task", "TASK-3") == writes[0][:2] and writes[0][2] == {"exp_start_date": datetime.date(2026, 11, 4), "exp_end_date": datetime.date(2026, 11, 11)}


def test_template_task_offsets_chain_without_overlap(sc):
	ends = -1
	for task in sc.TASKS:
		assert task["start"] > ends
		ends = task["start"] + task["duration"]
		assert not re.match(r"Phase \d", task["subject"])


# ---------------------------------------------------------------------- the intake plan


def _values(**extra):
	values = {
		"customer": "CUST-0001",
		"contact_name": "Pat Example",
		"contact_phone": "801-555-0100",
		"contact_email": "pat@example.test",
		"site_address": "Example Residence-Shipping",
		"service_type": "Winterization",
		"scheduled_date": "2026-11-10",
		"scope": "<p>Drain and blow out the lines.</p>",
		"technician": "tech@example.test",
	}
	values.update(extra)
	return values


def test_plan_creates_opportunity_then_project_then_call(sc):
	plan = sc.plan_intake(_values(), TODAY, "Example Residence", "Qualification", "office@example.test")
	assert [step for step, _ in plan] == ["opportunity", "project", "service_call"]
	opportunity, project, call = (doc for _, doc in plan)

	assert opportunity["doctype"] == "Opportunity"
	assert opportunity["status"] == "Qualification"
	assert opportunity["status"] != "Closed Won"
	assert opportunity["opportunity_from"] == "Customer" and opportunity["party_name"] == "CUST-0001"
	assert opportunity["custom_opportunity_name"] == "Example Residence - Winterization 2026-11-10"
	assert opportunity["custom_value_stream"] == [{"value_stream": "Service"}]
	assert opportunity["opportunity_owner"] == "office@example.test"
	assert opportunity["customer_address"] == "Example Residence-Shipping"

	assert project["project_name"] == "Example Residence - Winterization 2026-11-10"
	assert project["project_type"] == "Service"
	assert project["project_template"] == "Service Call"
	assert project["status"] == "Active"
	assert project["expected_start_date"] == TODAY
	# Inserted wide (today + 30), so ERPNext's copied template tasks fit; the real end lands after.
	assert project["expected_end_date"] == datetime.date(2026, 12, 3)

	assert call["doctype"] == "Service Call"
	assert call["status"] == "Requested"
	assert call["rate_kind"] == "Flat"
	assert call["scheduled_date"] == "2026-11-10"
	assert call["technician"] == "tech@example.test"
	assert "project" not in call and "opportunity" not in call  # linked after both exist


def test_plan_names_the_job_from_today_without_a_date(sc):
	plan = dict(sc.plan_intake(_values(scheduled_date=None, service_type="Small Repair", rate_kind=None), TODAY, "  Example   Residence ", "Qualification", "u"))
	assert plan["project"]["project_name"] == "Example Residence - Small Repair 2026-11-03"
	assert plan["service_call"]["rate_kind"] == "As-needed"
	assert plan["service_call"]["scheduled_date"] is None


def test_job_name_is_single_spaced_and_fits_the_field(sc):
	assert sc.job_name("A  B", "Startup", "2026-11-03") == "A B - Startup 2026-11-03"
	assert len(sc.job_name("x" * 200, "Startup", "2026-11-03")) == sc.NAME_MAX


def test_template_by_type_covers_every_service_type(sc):
	assert set(sc.TEMPLATE_BY_TYPE) == set(sc.SERVICE_TYPES)
	assert sc.TEMPLATE_BY_TYPE["Small Repair"] == sc.SMALL_REPAIR_TEMPLATE
	sc.frappe.db.exists = lambda doctype, name: name in {"Seasonal Startup", "Small Repair"}
	assert sc.template_for("Startup") == "Seasonal Startup"
	assert sc.template_for("Winterization") is None


# ---------------------------------------------------------------------- the state machine


@pytest.mark.parametrize(
	("current", "new", "ok"),
	[
		("Requested", "Sent for Approval", True),
		("Requested", "Done", False),
		("Sent for Approval", "Approved", True),
		("Sent for Approval", "Declined", True),
		("Sent for Approval", "Sent for Approval", True),
		("Declined", "Sent for Approval", True),
		("Declined", "Approved", False),
		("Approved", "Scheduled", True),
		("Scheduled", "Done", True),
		("Done", "Invoiced", True),
		("Done", "Canceled", False),
		("Invoiced", "Canceled", False),
		("Canceled", "Requested", False),
		(None, "Canceled", True),
	],
)
def test_transitions(sc, current, new, ok):
	assert sc.can_transition(current, new) is ok


def test_every_transition_names_a_real_status(sc):
	for current, targets in sc.TRANSITIONS.items():
		assert current in sc.STATUSES
		assert targets <= set(sc.STATUSES)
	assert set(sc.SENDABLE) <= set(sc.STATUSES)


# ---------------------------------------------------------------------- the Opportunity


class _Opp(_Doc):
	def __init__(self, **values):
		super().__init__(values)
		self["lost_reasons"] = []
		self["flags"] = types.SimpleNamespace()
		self["meta"] = types.SimpleNamespace(get_field=lambda f: f in ("order_lost_reason", "lost_reasons"))
		self["saved"] = 0

	def append(self, table, row):
		self[table].append(row)

	def save(self, ignore_permissions=False):
		if self["status"] == "Lost" and not self["lost_reasons"]:
			raise RuntimeError("Please set at least one Lost Reason")
		self["saved"] += 1


def test_lost_carries_a_lost_reason_row_and_makes_the_master(sc):
	opp = _Opp(status="Qualification")
	masters = []
	sc.frappe.db.exists = lambda doctype, name=None: doctype == "Opportunity"
	sc.frappe.get_doc = lambda *a, **k: opp if a and a[0] == "Opportunity" else _Master(a[0], masters)
	sc.set_opportunity_status("CRM-OPP-1", "Lost", "Too expensive", lost_reason=sc.LOST_DECLINED, service_call="SVC-1")
	assert opp["status"] == "Lost" and opp["saved"] == 1
	assert opp["lost_reasons"] == [{"lost_reason": "Client declined"}]
	assert opp["order_lost_reason"] == "Too expensive"
	assert masters == [{"doctype": "Opportunity Lost Reason", "lost_reason": "Client declined"}]
	# Again, with the master present and the row already there: nothing duplicated.
	opp["status"] = "Sent"
	sc.frappe.db.exists = lambda doctype, name=None: True
	sc.set_opportunity_status("CRM-OPP-1", "Lost", None, lost_reason=sc.LOST_DECLINED)
	assert opp["lost_reasons"] == [{"lost_reason": "Client declined"}] and len(masters) == 1


class _Master(dict):
	def __init__(self, values, made):
		super().__init__(values)
		self.made = made

	def insert(self, ignore_permissions=False):
		self.made.append(dict(self))
		return self


def test_a_refused_opportunity_move_is_a_comment_on_the_call(sc):
	comments = []
	logged = []

	class Refusing(_Opp):
		def save(self, ignore_permissions=False):
			raise RuntimeError("Please set a Lead Source before marking this Opportunity Closed Won")

	call = _Doc(name="SVC-1")
	call.add_comment = lambda kind, text: comments.append(text)
	sc.frappe.db.exists = lambda *a, **k: True
	sc.frappe.get_doc = lambda doctype, name=None, **k: Refusing(status="Sent for Approval") if doctype == "Opportunity" else call
	sc.frappe.get_link_to_form = lambda doctype, name: name
	sc.frappe.log_error = lambda **k: logged.append(k)
	sc.set_opportunity_status("CRM-OPP-1", "Closed Won", service_call="SVC-1")
	assert logged and comments and "NOT moved to Closed Won" in comments[0]


def test_sending_needs_a_rate(approval):
	stamped = []
	call = _Doc(service_type="Startup", rate_kind="Flat", flat_rate=0)
	call.db_set = lambda field, value=None: stamped.append((field, value))
	approval.frappe.db.get_value = lambda *a, **k: None
	with pytest.raises(Exception):
		approval._require_rate(call)
	approval.frappe.db.get_value = lambda *a, **k: 425.0
	approval._require_rate(call)
	assert stamped == [("flat_rate", 425.0)]
	assert call["flat_rate"] == 425.0
	stamped.clear()
	approval._require_rate(call)  # already quoted: nothing copied again
	assert stamped == []


def test_rates_are_fixed_once_sent():
	call = json.loads((MAINT / "doctype" / "service_call" / "service_call.json").read_text(encoding="utf-8"))
	fields = {f["fieldname"]: f for f in call["fields"]}
	for name in ("flat_rate", "hourly_rate"):
		assert fields[name]["read_only_depends_on"] == 'eval:doc.status && doc.status != "Requested"'


def test_belongs_to_service_call_never_raises(sc):
	sc.frappe.db.exists = lambda doctype, filters=None: doctype == "Service Call" and filters == {"opportunity": "CRM-OPP-1"}
	assert sc.belongs_to_service_call("CRM-OPP-1") is True
	assert sc.belongs_to_service_call("CRM-OPP-2") is False
	assert sc.belongs_to_service_call(None) is False

	def boom(*a, **k):
		raise RuntimeError("Table 'tabService Call' doesn't exist")

	sc.frappe.db.exists = boom
	assert sc.belongs_to_service_call("CRM-OPP-1") is False


# ---------------------------------------------------------------------- the invoice lines


def _record(**extra):
	record = {
		"name": "PRJ-00999-2026-V001",
		"clock_in_time": datetime.datetime(2026, 11, 10, 9, 0),
		"clock_out_time": datetime.datetime(2026, 11, 10, 11, 30),
		"paused_duration": 1800,
		"chemistry_readings": [],
		"consumables": [],
	}
	record.update(extra)
	return _Doc(record)


def test_billable_hours_prefers_the_typed_reading_then_the_clock(sc):
	assert sc.billable_hours(_record()) == 2.0
	typed = _record(chemistry_readings=[{"reading": "Labor Hours", "reading_value": 1.5}])
	assert sc.billable_hours(typed) == 1.5
	zero = _record(chemistry_readings=[{"reading": "Labor hours", "reading_value": 0}])
	assert sc.billable_hours(zero) == 2.0
	assert sc.billable_hours(_record(clock_out_time=None)) == 0.0


def _lines(sc, call, record, prices=None, chemicals=("CHEM-1",)):
	prices = prices or {}
	billed_as_chemicals = []

	def chemical_lines(rows):
		billed_as_chemicals.extend(rows)
		return [{"item_code": row["item"], "qty": row["qty"], "rate": 1, "description": "chem"} for row in rows]

	lines, notes = sc.invoice_lines(call, record, "MAINT-FEE", lambda item: prices.get(item, 0), lambda item: item in chemicals, chemical_lines)
	return lines, notes, billed_as_chemicals


def test_flat_rate_bills_one_line_plus_chemicals(sc):
	call = _Doc(service_type="Winterization", rate_kind="Flat", flat_rate=350)
	record = _record(consumables=[{"item": "CHEM-1", "item_name": "Algaecide", "qty": 2, "section": "Chemical Dosing"}])
	lines, notes, chems = _lines(sc, call, record)
	assert lines[0] == {"item_code": "MAINT-FEE", "qty": 1, "rate": 350.0, "description": "Winterization"}
	assert [line["item_code"] for line in lines[1:]] == ["CHEM-1"]
	assert [row["item"] for row in chems] == ["CHEM-1"]
	assert notes == []


def test_hourly_bills_hours_times_rate_and_parts_at_item_price(sc):
	call = _Doc(service_type="Small Repair", rate_kind="Emergency", hourly_rate=250)
	record = _record(
		consumables=[
			{"item": "VALVE-2IN", "item_name": '2" check valve', "qty": 1, "section": sc.PARTS_SECTION},
			{"item": "UNION-2IN", "item_name": "Union", "qty": 2, "section": None},
			{"item": "CHEM-1", "item_name": "Algaecide", "qty": 1, "section": None},
			{"item": "NOPRICE", "item_name": "Mystery part", "qty": 1, "section": sc.PARTS_SECTION},
			{"item": "ZERO", "qty": 0, "section": sc.PARTS_SECTION},
		]
	)
	lines, notes, chems = _lines(sc, call, record, prices={"VALVE-2IN": 48.5, "UNION-2IN": 12})
	assert lines[0]["item_code"] == "MAINT-FEE" and lines[0]["qty"] == 2.0 and lines[0]["rate"] == 250.0
	assert "Emergency" in lines[0]["description"]
	parts = {line["item_code"]: line for line in lines[1:3]}
	assert parts["VALVE-2IN"]["rate"] == 48.5 and parts["VALVE-2IN"]["qty"] == 1.0
	assert parts["UNION-2IN"]["rate"] == 12.0 and parts["UNION-2IN"]["qty"] == 2.0
	assert [row["item"] for row in chems] == ["CHEM-1"]
	assert any("Mystery part" in note for note in notes)
	assert all(line["item_code"] != "ZERO" for line in lines)


def test_missing_rate_or_fee_item_is_a_note_not_a_line(sc):
	record = _record()
	lines, notes, _ = _lines(sc, _Doc(service_type="Startup", rate_kind="Flat", flat_rate=0), record)
	assert lines == [] and any("no flat rate" in note for note in notes)
	lines, notes, _ = _lines(sc, _Doc(service_type="Small Repair", rate_kind="As-needed", hourly_rate=0), record)
	assert lines == [] and any("no hourly rate" in note for note in notes)
	lines, notes = sc.invoice_lines(_Doc(service_type="Startup", flat_rate=100), record, None, lambda i: 0, lambda i: False, lambda rows: [])
	assert lines == [] and any("Default Maintenance Fee Item" in note for note in notes)
	no_hours = _record(clock_out_time=None)
	lines, notes, _ = _lines(sc, _Doc(service_type="Small Repair", rate_kind="As-needed", hourly_rate=180), no_hours)
	assert lines == [] and any("no labor hours" in note for note in notes)


def test_describe_rate_in_words(sc):
	fmt = lambda v: f"${v:,.2f}"  # noqa: E731
	assert sc.describe_rate(_Doc(service_type="Startup", flat_rate=425), fmt) == "$425.00 flat, plus chemicals used"
	assert sc.describe_rate(_Doc(service_type="Small Repair", rate_kind="After-hours Emergency", hourly_rate=350), fmt) == "$350.00 per hour (After-hours Emergency), plus parts and materials"
	assert "as quoted" in sc.describe_rate(_Doc(service_type="Startup"), fmt)


# ---------------------------------------------------------------------- approval tokens and state


def test_tokens_are_urlsafe_and_only_the_hash_is_kept(approval):
	token, digest = approval.mint_token()
	assert approval.TOKEN_RE.match(token)
	assert len(digest) == 64 and approval.hash_token(token) == digest
	assert approval.hash_token(token + "!") is None
	assert approval.hash_token("short") is None
	assert approval.hash_token(None) is None
	assert "ref=" in approval.approval_url(token) and "sid=" not in approval.approval_url(token)


def test_effective_status_reads_expiry_itself(approval):
	live = _Doc(status="Sent", expires_on=NOW + datetime.timedelta(days=1))
	stale = _Doc(status="Viewed", expires_on=NOW - datetime.timedelta(minutes=1))
	assert approval.effective_status(live) == "Sent" and approval.is_live(live)
	assert approval.effective_status(stale) == "Expired" and not approval.is_live(stale)
	assert approval.effective_status(_Doc(status="Approved", expires_on=NOW - datetime.timedelta(days=1))) == "Approved"
	assert approval.LINK_DAYS == 14


def test_clean_text_strips_markup_and_controls(approval):
	assert approval.clean_text(" Pat <b>Example</b>‮ ") == "Pat bExample/b"
	assert approval.clean_text("x" * 500) == "x" * approval.NAME_MAX
	assert approval.clean_text(None) == ""


def test_sms_text_is_short_and_carries_the_link(approval):
	text = approval.sms_text("Example Residence", "Winterization", "https://erp.example.test/service-call-approval?ref=abc", "11-17-2026")
	assert "https://erp.example.test/service-call-approval?ref=abc" in text
	assert len(text) <= approval.SMS_MAX


def test_clean_email_and_phone(approval):
	assert approval.clean_email(" Pat@Example.TEST ") == "pat@example.test"
	assert approval.clean_email("a@b.test, c@d.test") == ""
	assert approval.clean_phone("(801) 555-0100") == "(801) 555-0100"
	assert approval.clean_phone("555-0100") == ""


def test_guest_endpoints_refuse_dead_links_uniformly(approval):
	approval.resolve = lambda ref: None
	assert approval.approve_service_call("nope", "Pat") == {"ok": False, "error": approval.UNAVAILABLE}
	approval.resolve = lambda ref: _Doc(name="SCA-1", status="Expired", expires_on=NOW - datetime.timedelta(days=1))
	assert approval.decline_service_call("x", "no") == {"ok": False, "error": approval.UNAVAILABLE}
	approval.resolve = lambda ref: _Doc(name="SCA-1", status="Approved")
	assert approval.approve_service_call("x", "Pat") == {"ok": True, "already": True}
	approval.resolve = lambda ref: _Doc(name="SCA-1", status="Sent", expires_on=NOW + datetime.timedelta(days=1))
	assert approval.approve_service_call("x", "   ") == {"ok": False, "error": "Please type your full name."}


def test_expire_stale_declines_the_call_and_loses_the_opportunity(approval):
	writes = []
	moved = []
	approval.frappe.get_all = lambda doctype, filters=None, pluck=None, **k: ["SCA-9"]
	approval.frappe.db.get_value = lambda doctype, name, field, **k: ("SVC-9" if field == "service_call" else types.SimpleNamespace(status="Sent for Approval", opportunity="CRM-OPP-9"))
	approval.frappe.db.set_value = lambda doctype, name, field, value=None, **k: writes.append((doctype, name, field if isinstance(field, dict) else {field: value}))
	approval.service_calls.set_opportunity_status = lambda opp, status, reason=None, **kw: moved.append((opp, status, reason, kw))
	approval.expire_stale()
	assert writes[0] == ("Service Call Approval Request", "SCA-9", {"status": "Expired"})
	assert writes[1][1] == "SVC-9" and writes[1][2]["status"] == "Declined"
	assert moved == [("CRM-OPP-9", "Lost", approval.EXPIRED_REASON, {"lost_reason": "Approval link expired", "service_call": "SVC-9"})]


def test_describe_approval(approval):
	fmt = str
	assert approval.describe(_Doc(approved_on=NOW, approved_name="Pat"), None, fmt) == f"Approved by Pat on {NOW}"
	assert approval.describe(_Doc(), _Doc(status="Sent", email="p@x.test", sent_on=NOW, expires_on=NOW + datetime.timedelta(days=1)), fmt).startswith("Sent to p@x.test")
	assert approval.describe(_Doc(), None, fmt) == "Not sent"


# ---------------------------------------------------------------------- the shape of the thing


def _calls_named(tree, name):
	for node in ast.walk(tree):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name:
			yield node


def test_guest_module_never_splats_a_payload():
	tree = ast.parse(APPROVAL_PY.read_text(encoding="utf-8"))
	assert not list(_calls_named(tree, "update")), "every field is assigned by name"


def test_guest_endpoints_are_post_only_and_rate_limited():
	tree = ast.parse(APPROVAL_PY.read_text(encoding="utf-8"))
	guests = {}
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef):
			decorators = [ast.unparse(d) for d in node.decorator_list]
			if any("allow_guest=True" in d for d in decorators):
				guests[node.name] = decorators
				assert any("methods=" in d and "POST" in d for d in decorators), node.name
				assert any("rate_limit" in d for d in decorators), node.name
				assert "sid" not in [a.arg for a in node.args.args], node.name
				assert not any(a.arg in {"service_call", "name", "docname"} for a in node.args.args), node.name
	assert set(guests) == {"approve_service_call", "decline_service_call"}


def test_www_controller_uses_underscores_and_the_page_adds_no_history():
	assert PAGE_PY.exists() and PAGE_HTML.exists()
	html = PAGE_HTML.read_text(encoding="utf-8")
	js = PAGE_JS.read_text(encoding="utf-8")
	for text in (html, js):
		assert "pushState" not in text and "replaceState" not in text and "location.hash" not in text
	assert 'name="referrer" content="no-referrer"' in html
	assert "noindex" in html
	assert "canvas" not in html, "no drawn signature on a service call"
	assert "approve_service_call" in js and "decline_service_call" in js
	assert "SA_BOOT" in html and "SA_BOOT" in js


def test_email_is_a_fragment_in_the_shell():
	text = EMAIL.read_text(encoding="utf-8")
	assert "_components.html" in text
	assert "max-width" not in text and "<table" not in text
	assert "approval_url" in text


def test_hooks_and_patches_are_wired():
	hooks = HOOKS.read_text(encoding="utf-8")
	assert "erpnext_enhancements.sapphire_maintenance.service_calls.on_visit_submit" in hooks
	assert "erpnext_enhancements.sapphire_maintenance.service_calls.on_visit_cancel" in hooks
	assert "erpnext_enhancements.sapphire_maintenance.service_call_approval.expire_stale" in hooks
	assert '"Service Call": ["public/js/sapphire_maintenance/service_call_intake.js"]' in hooks
	patches = PATCHES.read_text(encoding="utf-8").splitlines()
	assert "erpnext_enhancements.patches.seed_service_call_project_template" in patches
	assert "erpnext_enhancements.patches.seed_small_repair_template" in patches
	assert not any("seed_service_call_rate" in line for line in patches), "rates are never seeded from the repo"


def test_doctypes_sit_in_the_module_with_the_derived_class_names():
	for folder, cls in (("service_call", "ServiceCall"), ("service_call_rate", "ServiceCallRate"), ("service_call_approval_request", "ServiceCallApprovalRequest")):
		meta = json.loads((MAINT / "doctype" / folder / f"{folder}.json").read_text(encoding="utf-8"))
		assert meta["module"] == "Sapphire Maintenance"
		assert meta["name"].replace(" ", "").replace("-", "") == cls
		source = (MAINT / "doctype" / folder / f"{folder}.py").read_text(encoding="utf-8")
		assert f"class {cls}(Document)" in source
		roles = {row["role"] for row in meta["permissions"]}
		assert "System Manager" in roles and "Maintenance User" in roles
	call = json.loads((MAINT / "doctype" / "service_call" / "service_call.json").read_text(encoding="utf-8"))
	fields = {f["fieldname"]: f for f in call["fields"]}
	assert fields["project"]["reqd"] == 1
	assert fields["status"]["options"].split("\n") == list(importlib.import_module("erpnext_enhancements.sapphire_maintenance.service_calls").STATUSES)
	assert fields["status"]["read_only"] == 1, "the office moves a call with its buttons, the client with the link"
	for name in ("approved_name", "approved_on", "approver_ip", "approver_user_agent"):
		assert fields[name]["read_only"] == 1 and fields[name].get("no_copy") == 1
	request = json.loads((MAINT / "doctype" / "service_call_approval_request" / "service_call_approval_request.json").read_text(encoding="utf-8"))
	assert request["in_create"] == 1
	assert all(f.get("read_only") == 1 for f in request["fields"] if f["fieldtype"] not in ("Section Break", "Column Break"))
	assert request["fields"][-1]["fieldname"] == "token_hash" and request["fields"][-1]["unique"] == 1


def test_record_takes_a_service_call_in_place_of_a_contract():
	record = json.loads((MAINT / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.json").read_text(encoding="utf-8"))
	fields = {f["fieldname"]: f for f in record["fields"]}
	assert fields["service_call"]["options"] == "Service Call"
	assert not fields["maintenance_contract"].get("reqd") and not fields["serial_no"].get("reqd")
	order = record["field_order"]
	assert order.index("service_call") == order.index("maintenance_contract") + 1
	source = (MAINT / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.py").read_text(encoding="utf-8")
	assert "service_call=None" in source
	wizard = (APP / "api" / "maintenance_visit.py").read_text(encoding="utf-8")
	assert 'service_call=doc.get("service_call")' in wizard
	workflow = (APP / "api" / "maintenance_workflow.py").read_text(encoding="utf-8")
	assert "invoice_for_visit" in workflow


def test_seed_patches_cannot_raise_and_seed_by_name(sc):
	for name in ("seed_service_call_project_template", "seed_small_repair_template"):
		source = (APP / "patches" / f"{name}.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		execute = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute")
		assert isinstance(execute.body[0], ast.Try), f"{name}.execute must catch everything"
		assert "log_error" in source
	patch = importlib.import_module("erpnext_enhancements.patches.seed_service_call_project_template")
	patch.frappe = sc.frappe
	made = []

	class Doc(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError:
				raise AttributeError(key) from None

		def insert(self, ignore_permissions=False, set_name=None):
			self["name"] = set_name or f"TASK-{len(made) + 1}"
			made.append(dict(self))
			return self

	sc.frappe.db.exists = lambda doctype, name=None: doctype == "Project Type" and name == "Service"
	sc.frappe.get_doc = lambda values: Doc(values)
	assert patch.seed_template() == "created"
	tasks = [doc for doc in made if doc["doctype"] == "Task"]
	assert [t["subject"] for t in tasks] == ["Approval received", "Service visit", "Invoice sent"]
	assert all(t["is_template"] == 1 for t in tasks)
	assert "depends_on" not in tasks[0] and tasks[1]["depends_on"] == [{"task": "TASK-1"}] and tasks[2]["depends_on"] == [{"task": "TASK-2"}]
	template = made[-1]
	assert template["doctype"] == "Project Template" and template["name"] == "Service Call" and template["project_type"] == "Service"
	assert [row["task"] for row in template["tasks"]] == ["TASK-1", "TASK-2", "TASK-3"]
	sc.frappe.db.exists = lambda doctype, name=None: True
	assert patch.seed_template() == "exists"
	assert len(made) == 4


def test_small_repair_template_has_the_four_parts(sc):
	patch = importlib.import_module("erpnext_enhancements.patches.seed_small_repair_template")
	titles = [row[0] for row in patch.SECTIONS]
	assert titles == [sc.WORK_SECTION, sc.LABOR_SECTION, sc.PARTS_SECTION]
	work = dict((row[0], row) for row in patch.SECTIONS)[sc.WORK_SECTION]
	labels = [item["label"] for item in work[3]]
	assert "Work performed" in labels and "Before photo" in labels and "After photo" in labels
	assert next(item for item in work[3] if item["label"] == "Work performed")["is_mandatory"] == 1
	labor = dict((row[0], row) for row in patch.SECTIONS)[sc.LABOR_SECTION]
	assert labor[1] == "Water Chemistry" and labor[3][0]["label"] == sc.LABOR_READING
	parts = dict((row[0], row) for row in patch.SECTIONS)[sc.PARTS_SECTION]
	assert parts[1] == "Chemical Dosing" and parts[3] == []


def test_workspace_lists_the_call_and_the_rates():
	workspace = json.loads((MAINT / "workspace" / "sapphire_maintenance" / "sapphire_maintenance.json").read_text(encoding="utf-8"))
	links = {row.get("link_to") for row in workspace["links"]}
	assert {"Service Call", "Service Call Rate"} <= links
	shortcut = next(s for s in workspace["shortcuts"] if s["label"] == "New Service Call")
	assert shortcut["url"] == "/desk/service-call/new"
	assert any(block.get("data", {}).get("shortcut_name") == "New Service Call" for block in json.loads(workspace["content"]))
