# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Phase 5, "extras": template planning, weather flags, who is free, and the
customer date confirmation for both planners.

What this pins, because each of them fails quietly:

* **Templates carry planning without overwriting.** A Task made from a template Task gets the
  template's hours, crew size, qualifications and the two Phase 5 flags only where it has none of
  its own, and never from a Task that is not a template.
* **Weather is a refinement that never costs a planner load.** Thresholds and wording are fixed;
  every uncached site goes in ONE Open-Meteo request; answers are cached three hours; a failure
  hides the chip, backs off, and is logged once an hour **without the request URL** (requests puts
  it in its exception text). A flagged day is a worse suggestion, never a refused one.
* **who_is_free** is gated like the planner, bounded, and prices driving without Google.
* **The customer confirmation stays dark until switched on**: nothing is queued or sent while
  Project Planner Settings ``customer_date_confirmations`` is 0 (its default). When on, a firm,
  customer-facing date set or moved is persisted as *due* before the job is enqueued (the deploy
  FLUSHDBs redis), sent at most once per (document, date) through a database stamp, wrapped in the
  shared email shell, and a Desk form can never write the stamps back. The preview renders exactly
  what would go out and never sends, with the switch off too, and only for managers.
* **The wiring**: hooks (before_insert, on_update for both doctypes, keep_stamps on validate, the
  */10 sweep, the AI tool), the four Task custom fields appended to the fixture, the record's two
  stamps, the Settings fields and their patch, the insert-only template seed, and the page blocks.

Bench-free: installs its own ``frappe`` stub (plus ``email_style``, ``requests`` and
``frappe_assistant_core`` stand-ins), runs the real modules, and puts ``sys.modules`` back.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase5
"""

import ast
import datetime
import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"
PAGE_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
TASK_JS = APP / "task_enhancements/doctype/task/task.js"
CUSTOM_FIELDS = APP / "fixtures/custom_field.json"
SETTINGS_JSON = APP / "project_enhancements/doctype/project_planner_settings/project_planner_settings.json"
RECORD_JSON = (
	APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.json"
)
PATCHES_TXT = APP / "patches.txt"
SEED_PATCH = APP / "patches/seed_planner_date_confirmation_template.py"
SETTINGS_PATCH = APP / "patches/materialize_planner_phase5_settings.py"
API_PATH = APP / "api/project_planner.py"

D = datetime.date
TODAY = D(2026, 10, 9)  # a Friday
NOW = datetime.datetime(2026, 10, 9, 7, 0, 0)

_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
routing = None
weather = None
confirmations = None
templates = None
api = None
email_style = None
requests_stub = None
tool_module = None
gate = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(dict):
	"""Enough of a frappe Document: dict and attribute access, set/append, comments."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def append(self, key, row):
		self.setdefault(key, [])
		self[key].append(_Doc(row))

	def is_new(self):
		return bool(self.get("__new"))

	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype, self.get("name")))
		if self.get("name") in frappe.denied:
			raise _PermissionError(ptype)

	def add_comment(self, kind, text):
		frappe.comments.append((self.get("doctype"), self.get("name"), kind, text))

	def get_doc_before_save(self):
		return self.get("__before")


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


class _Cache:
	def __init__(self):
		self.store = {}
		self.expiry = {}

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value
		self.expiry[key] = expires_in_sec

	def delete_value(self, key):
		self.store.pop(key, None)


def _fake_email_style():
	module = types.ModuleType("erpnext_enhancements.email_style")
	module.calls = []

	def wrap(body, **kwargs):
		module.calls.append(("wrap", body, kwargs))
		return f"<shell>{body}</shell>"

	module.wrap = wrap
	module.p = lambda text: f"<p>{text}</p>"
	module.bullets = lambda items: "".join(f"<li>{i}</li>" for i in items)
	module.button = lambda url, label, tone="primary": f"<a href='{url}'>{label}</a>"
	return module


def _fake_requests():
	module = types.ModuleType("requests")
	module.calls = []
	module.answer = None
	module.error = None

	class Response:
		def __init__(self, status, payload):
			self.status_code = status
			self._payload = payload

		def json(self):
			return self._payload

	def get(url, params=None, timeout=None):
		module.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
		if module.error:
			raise module.error
		status, payload = module.answer if module.answer else (200, None)
		return Response(status, payload)

	module.get = get
	return module


def _fake_fac():
	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		def __init__(self):
			self.name = ""
			self.description = ""
			self.inputSchema = {}
			self.requires_permission = None
			self.category = "Custom"
			self.source_app = "frappe_assistant_core"

	base_tool.BaseTool = BaseTool
	fac = types.ModuleType("frappe_assistant_core")
	fac_core = types.ModuleType("frappe_assistant_core.core")
	fac.core, fac_core.base_tool = fac_core, base_tool
	return {
		"frappe_assistant_core": fac,
		"frappe_assistant_core.core": fac_core,
		"frappe_assistant_core.core.base_tool": base_tool,
	}


def setUpModule():
	global frappe, engine, routing, weather, confirmations, templates, api, email_style
	global requests_stub, tool_module, gate
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith(
			("frappe.", "erpnext_enhancements", "frappe_assistant_core")
		):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.format_datetime = lambda value=None, *a, **k: str(value)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: (
		str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
	utils.validate_email_address = lambda value, throw=False: value if "@" in str(value or "") else ""
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_fullname = lambda user=None: user
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_traceback = lambda: "Traceback: boom"
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})

	email_style = _fake_email_style()
	sys.modules["erpnext_enhancements.email_style"] = email_style
	requests_stub = _fake_requests()
	sys.modules["requests"] = requests_stub
	sys.modules.update(_fake_fac())
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	api = importlib.import_module("erpnext_enhancements.api.project_planner")
	weather = importlib.import_module("erpnext_enhancements.project_enhancements.planner_weather")
	confirmations = importlib.import_module(
		"erpnext_enhancements.project_enhancements.customer_confirmations"
	)
	templates = importlib.import_module("erpnext_enhancements.project_enhancements.task_templates")
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	tool_module = importlib.import_module("erpnext_enhancements.assistant_tools.crew_who_is_free")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name in ("frappe", "requests") or name.startswith(
			("frappe.", "erpnext_enhancements", "frappe_assistant_core")
		):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith(("frappe.", "frappe_assistant_core")):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- the fake site


def _matches(row, filters):
	for key, wanted in (filters or {}).items():
		value = row.get(key)
		if isinstance(wanted, list | tuple) and len(wanted) == 2 and isinstance(wanted[0], str):
			op, operand = wanted
			if op == "in" and value not in operand:
				return False
			if op == "is" and operand == "set" and not value:
				return False
			continue
		if value != wanted:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.calls.append(("get_all", doctype, filters))
	rows = [doc for (dt, _name), doc in frappe.docs.items() if dt == doctype]
	rows += [_Doc(r) for r in frappe.tables.get(doctype, [])]
	rows = [r for r in rows if _matches(r, filters)]
	limit = kwargs.get("limit_page_length")
	if limit:
		rows = rows[:limit]
	if pluck:
		return [r.get(pluck) for r in rows]
	return [_Doc(r) for r in rows]


def _get_value(doctype, name, field=None, *args, as_dict=False, for_update=False, **kwargs):
	doc = frappe.docs.get((doctype, name))
	if for_update:
		frappe.calls.append(("for_update", doctype, name))
	if doc is None:
		return None
	if isinstance(field, list | tuple):
		row = _Doc({f: doc.get(f) for f in field})
		return row if as_dict else tuple(row.values())
	return doc.get(field)


def _set_value(doctype, name, field, value=None, update_modified=True, **kwargs):
	values = field if isinstance(field, dict) else {field: value}
	frappe.writes.append((doctype, name, dict(values), update_modified))
	doc = frappe.docs.get((doctype, name))
	if doc is not None:
		doc.update(values)


def _exists(doctype, name=None):
	if doctype == "DocType":
		return name not in frappe.missing_doctypes
	return (doctype, name) in frappe.docs


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		doc = _Doc(doctype)

		def insert(ignore_permissions=False, set_name=None):
			doc["name"] = set_name or doc.get("name")
			frappe.inserted.append(doc)
			return doc

		doc["insert"] = insert
		return doc
	doc = frappe.docs.get((doctype, name))
	if doc is None:
		raise _Throw(f"{doctype} {name} not found")
	return doc


def _render_template(template, context):
	import jinja2

	return jinja2.Environment().from_string(template).render(**(context or {}))


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.flags = types.SimpleNamespace()
	frappe.docs = {}
	frappe.tables = {}
	frappe.calls, frappe.writes, frappe.comments, frappe.errors = [], [], [], []
	frappe.sent, frappe.enqueued, frappe.inserted, frappe.db_calls = [], [], [], []
	frappe.denied = set()
	frappe.missing_columns = set()
	frappe.missing_doctypes = set()
	frappe.settings = {"customer_date_confirmations": 0}
	frappe.cache = _Cache()
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = lambda doctype, name=None: _Doc(frappe.settings)
	frappe.has_permission = lambda *a, **k: True
	frappe.render_template = _render_template
	frappe.defaults = types.SimpleNamespace(
		get_global_default=lambda key: "Sapphire Fountains", get_user_default=lambda key: "Sapphire Fountains"
	)
	frappe.sendmail = lambda **kwargs: frappe.sent.append(kwargs)
	frappe.enqueue = lambda method, **kwargs: frappe.enqueued.append((method, kwargs))
	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: (doctype, column) not in frappe.missing_columns,
		get_value=_get_value,
		set_value=_set_value,
		exists=_exists,
		sql=lambda *a, **k: [],
		commit=lambda: frappe.db_calls.append("commit"),
		rollback=lambda *a, **k: frappe.db_calls.append("rollback"),
		get_single_value=lambda *a, **k: None,
	)
	if email_style is not None:
		email_style.calls.clear()
	if requests_stub is not None:
		requests_stub.calls.clear()
		requests_stub.answer = None
		requests_stub.error = None


def _site():
	"""A customer-facing task on a project whose customer has a primary contact."""
	frappe.docs[("Project", "PRJ-1")] = _Doc(
		{"doctype": "Project", "name": "PRJ-1", "project_name": "Highlands Plaza", "customer": "CUST-1"}
	)
	frappe.docs[("Customer", "CUST-1")] = _Doc(
		{
			"doctype": "Customer",
			"name": "CUST-1",
			"customer_name": "Highlands HOA",
			"customer_primary_contact": "CON-1",
		}
	)
	frappe.docs[("Contact", "CON-1")] = _Doc(
		{"doctype": "Contact", "name": "CON-1", "first_name": "Pat", "email_id": "pat@example.com"}
	)
	frappe.docs[("Company", "Sapphire Fountains")] = _Doc(
		{"doctype": "Company", "name": "Sapphire Fountains", "phone_no": "303-555-0100"}
	)


def _task(name="TASK-1", start="2026-10-15", **values):
	doc = _Doc(
		{
			"doctype": "Task",
			"name": name,
			"subject": "Pump set",
			"project": "PRJ-1",
			"status": "Open",
			"exp_start_date": start,
			"exp_end_date": start,
			"custom_customer_visit": 1,
			"custom_tentative": 0,
			"custom_outdoor": 0,
			"custom_customer_confirmation_due": None,
			"custom_customer_confirmed_for": None,
			"custom_crew": [
				_Doc({"resource": "RES-1", "resource_name": "Austin Healey"}),
				_Doc({"resource": "RES-2", "resource_name": "Korben Fox"}),
			],
			"modified": "2026-10-09 07:00:00",
		}
	)
	doc.update(values)
	frappe.docs[("Task", name)] = doc
	return doc


def _visit(name="SMR-1", date="2026-10-16", **values):
	doc = _Doc(
		{
			"doctype": "Sapphire Maintenance Record",
			"name": name,
			"docstatus": 0,
			"customer": "CUST-1",
			"project": "PRJ-1",
			"maintenance_contract": "MNT-1",
			"technician": "austin@example.com",
			"crew": [_Doc({"user": "korben@example.com"})],
			"scheduled_visit_date": date,
			"visit_date": None,
			"customer_confirmation_due": None,
			"customer_confirmed_for": None,
		}
	)
	doc.update(values)
	frappe.docs[("Sapphire Maintenance Record", name)] = doc
	frappe.docs[("User", "austin@example.com")] = _Doc(
		{"name": "austin@example.com", "full_name": "Austin Healey"}
	)
	frappe.docs[("User", "korben@example.com")] = _Doc(
		{"name": "korben@example.com", "full_name": "Korben Fox"}
	)
	return doc


def _locate(bookings, detail=False):
	out = {}
	for booking in bookings:
		value = {"point": (39.7392, -104.9903), "address": "1 Main St, Denver"}
		out[booking["ref"]] = value if detail else value["point"]
	return out


# ====================================================================== P5.1 templates


class TestTemplatePlanning(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_blank_means_unset(self):
		for value in (None, 0, 0.0, "", " ", "0", "0.0"):
			self.assertTrue(templates.is_blank(value), value)
		for value in (1, 2.5, "3", "Yes"):
			self.assertFalse(templates.is_blank(value), value)

	def test_only_blank_fields_are_filled_and_nothing_is_overwritten(self):
		template = {
			"expected_time": 16,
			"custom_crew_size": 2,
			"custom_outdoor": 1,
			"custom_customer_visit": 0,
		}
		current = {"expected_time": 0, "custom_crew_size": 3, "custom_outdoor": 0, "custom_customer_visit": 0}
		self.assertEqual(
			templates.values_to_copy(template, current), {"expected_time": 16, "custom_outdoor": 1}
		)

	def test_qualifications_copy_only_onto_a_task_with_none(self):
		rows = [{"credential_type": "Forklift"}, {"credential_type": "Forklift"}, {"credential_type": "CPO"}]
		self.assertEqual(templates.credentials_to_copy(rows, []), ["Forklift", "CPO"])
		self.assertEqual(templates.credentials_to_copy(rows, [{"credential_type": "Confined Space"}]), [])

	def _new(self, **values):
		doc = _Doc(
			{
				"doctype": "Task",
				"template_task": "TMPL-1",
				"expected_time": 0,
				"custom_crew_size": 3,
				"custom_outdoor": 0,
				"custom_customer_visit": 0,
				"custom_required_credentials": [],
				"meta": types.SimpleNamespace(get_field=lambda name: name == "custom_required_credentials"),
			}
		)
		doc.update(values)
		return doc

	def test_the_hook_fills_a_task_made_from_a_template(self):
		frappe.docs[("Task", "TMPL-1")] = _Doc(
			{
				"name": "TMPL-1",
				"is_template": 1,
				"expected_time": 16,
				"custom_crew_size": 2,
				"custom_outdoor": 1,
				"custom_customer_visit": 1,
			}
		)
		frappe.tables["Task Required Credential"] = [
			{
				"parenttype": "Task",
				"parent": "TMPL-1",
				"parentfield": "custom_required_credentials",
				"credential_type": "CPO",
			}
		]
		doc = self._new()
		templates.copy_template_planning(doc)
		self.assertEqual(doc.expected_time, 16)
		self.assertEqual(doc.custom_crew_size, 3)  # its own value wins
		self.assertEqual((doc.custom_outdoor, doc.custom_customer_visit), (1, 1))
		self.assertEqual([r["credential_type"] for r in doc.custom_required_credentials], ["CPO"])
		self.assertNotIn("custom_crew", doc)  # who goes is decided on the planner

	def test_a_task_that_is_not_a_template_is_never_copied_from(self):
		frappe.docs[("Task", "TMPL-1")] = _Doc({"name": "TMPL-1", "is_template": 0, "expected_time": 16})
		doc = self._new()
		templates.copy_template_planning(doc)
		self.assertEqual(doc.expected_time, 0)

	def test_no_template_link_does_nothing_and_a_failure_never_raises(self):
		doc = self._new(template_task=None)
		templates.copy_template_planning(doc)
		self.assertEqual(frappe.calls, [])

		def broken(*a, **k):
			raise RuntimeError("db down")

		frappe.get_all = broken
		templates.copy_template_planning(self._new())
		self.assertEqual(len(frappe.errors), 1)


# ====================================================================== P5.2 weather


class TestWeatherWording(unittest.TestCase):
	def test_thresholds_and_wording(self):
		self.assertEqual(weather.day_flags(70, 5, 10), ["Rain 70%"])
		self.assertEqual(weather.day_flags(59, 0.4, 39.9), [])
		self.assertEqual(weather.day_flags(60, 0, 40), ["Rain 60%", "Freezing 0 °C", "Wind 40 km/h"])
		self.assertEqual(weather.day_flags(None, -3.4, 45.2), ["Freezing −3 °C", "Wind 45 km/h"])
		self.assertEqual(weather.day_flags("", None, "x"), [])
		self.assertEqual((weather.RAIN_PERCENT, weather.FREEZING_C, weather.WIND_KMH), (60, 0, 40))

	def test_point_keys_round_to_two_decimals(self):
		self.assertEqual(weather.point_key((39.73915, -104.99025)), "39.74,-104.99")
		self.assertIsNone(weather.point_key(None))
		self.assertIsNone(weather.point_key((0, 0)))

	def test_one_location_and_several(self):
		daily = {
			"time": ["2026-10-09", "2026-10-10"],
			"precipitation_probability_max": [80, 10],
			"temperature_2m_min": [3, -2],
			"wind_speed_10m_max": [12, 50],
		}
		self.assertEqual(
			weather.parse_daily({"daily": daily}),
			{"2026-10-09": ["Rain 80%"], "2026-10-10": ["Freezing −2 °C", "Wind 50 km/h"]},
		)
		self.assertEqual(len(weather.parse_payload({"daily": daily}, 1)), 1)
		self.assertEqual(len(weather.parse_payload([{"daily": daily}, {}], 2)), 2)
		self.assertIsNone(weather.parse_payload([{"daily": daily}], 2))
		self.assertEqual(weather.parse_daily({"error": True}), {})

	def test_the_request_asks_for_the_spec_fields(self):
		params = weather.request_params(["39.74,-104.99", "40.02,-105.27"])
		self.assertEqual(params["latitude"], "39.74,40.02")
		self.assertEqual(params["longitude"], "-104.99,-105.27")
		self.assertEqual(
			params["daily"], "precipitation_probability_max,temperature_2m_min,wind_speed_10m_max"
		)
		self.assertEqual((params["timezone"], params["forecast_days"]), ("America/Denver", 16))
		self.assertEqual(weather.OPEN_METEO_URL, "https://api.open-meteo.com/v1/forecast")

	def test_span_weather_is_none_outside_the_window_and_lists_flagged_days(self):
		forecast = {"2026-10-12": ["Rain 70%"], "2026-10-13": []}
		self.assertEqual(
			weather.span_weather((D(2026, 10, 12), D(2026, 10, 13)), forecast, TODAY),
			[{"date": "2026-10-12", "flags": ["Rain 70%"]}],
		)
		self.assertEqual(weather.span_weather((D(2026, 10, 13), D(2026, 10, 13)), forecast, TODAY), [])
		self.assertIsNone(weather.span_weather((D(2026, 11, 30), D(2026, 12, 1)), forecast, TODAY))
		self.assertIsNone(weather.span_weather((D(2026, 10, 12), D(2026, 10, 12)), None, TODAY))
		self.assertEqual(weather.window(TODAY), (TODAY, D(2026, 10, 24)))


class TestWeatherFetching(unittest.TestCase):
	def setUp(self):
		_reset()

	def _answer(self, *rains):
		return [
			{
				"daily": {
					"time": ["2026-10-12"],
					"precipitation_probability_max": [rain],
					"temperature_2m_min": [5],
					"wind_speed_10m_max": [5],
				}
			}
			for rain in rains
		]

	def test_every_uncached_site_goes_in_one_request_and_is_cached_three_hours(self):
		requests_stub.answer = (200, self._answer(70, 10))
		out = weather.forecasts([(39.7392, -104.9903), (40.015, -105.2705), (39.7391, -104.9902)])
		self.assertEqual(len(requests_stub.calls), 1)
		self.assertEqual(requests_stub.calls[0]["timeout"], 8)
		self.assertEqual(out["39.74,-104.99"], {"2026-10-12": ["Rain 70%"]})
		self.assertEqual(frappe.cache.expiry[weather.CACHE_PREFIX + "39.74,-104.99"], 3 * 3600)
		weather.forecasts([(39.7392, -104.9903)])
		self.assertEqual(len(requests_stub.calls), 1)  # served from the cache

	def test_a_failure_hides_weather_backs_off_and_logs_once_without_the_url(self):
		requests_stub.error = RuntimeError(
			"HTTPSConnectionPool(host='api.open-meteo.com'): /v1/forecast?latitude=39.74 timed out"
		)
		self.assertEqual(weather.forecasts([(39.7392, -104.9903)]), {})
		self.assertEqual(weather.forecasts([(40.0, -105.0)]), {})
		self.assertEqual(len(requests_stub.calls), 1)  # backed off: the second load did not wait
		self.assertEqual(len(frappe.errors), 1)
		message = frappe.errors[0][1]["message"]
		self.assertEqual(frappe.errors[0][1]["title"], weather.LOG_TITLE)
		self.assertIn("RuntimeError", message)
		self.assertNotIn("latitude", message)
		self.assertNotIn("/v1/forecast", message)
		frappe.cache.delete_value(weather.BACKOFF_FLAG)
		weather.forecasts([(40.0, -105.0)])
		self.assertEqual(len(frappe.errors), 1)  # still inside the hour

	def test_an_http_error_is_described_by_status(self):
		requests_stub.answer = (503, None)
		self.assertEqual(weather.forecasts([(39.7392, -104.9903)]), {})
		self.assertIn("HTTP 503", frappe.errors[0][1]["message"])


# ====================================================================== P5.2 on the planner


class TestWeatherOnThePlanner(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_flagged_day_is_a_worse_suggestion_and_says_why(self):
		entry = {"date": "2026-10-12", "score": 12.5, "reason": "Austin is nearby."}
		out = api.weather_adjust(entry, ["Rain 70%", "Wind 45 km/h"])
		self.assertEqual(out["score"], 72.5)
		self.assertEqual(out["weather"], ["Rain 70%", "Wind 45 km/h"])
		self.assertEqual(out["reason"], "Austin is nearby. Forecast: Rain 70%, Wind 45 km/h.")
		clear = api.weather_adjust(entry, [])
		self.assertEqual((clear["score"], clear["weather"], clear["reason"]), (12.5, [], "Austin is nearby."))
		self.assertIsNone(api.weather_adjust(None, ["Rain 70%"]))
		self.assertEqual(api.WEATHER_PENALTY_MINUTES, 30)

	def test_suggestions_for_an_outdoor_task_weigh_the_forecast(self):
		entries = [
			{"date": "2026-10-12", "score": 10, "reason": "r"},
			None,
			{"date": "2026-10-13", "score": 20},
		]
		point = (39.7392, -104.9903)
		with mock.patch.object(
			weather, "forecasts", return_value={"39.74,-104.99": {"2026-10-12": ["Rain 80%"]}}
		):
			out = api._weather_entries(entries, _Doc({"custom_outdoor": 1}), point)
			indoor = api._weather_entries(entries, _Doc({"custom_outdoor": 0}), point)
		self.assertEqual((out[0]["score"], out[0]["weather"]), (40, ["Rain 80%"]))
		self.assertIsNone(out[1])
		self.assertEqual((out[2]["score"], out[2]["weather"]), (20, []))
		self.assertIs(indoor, entries)
		ranked = api.rank_suggestions(out, with_site=True)
		self.assertEqual([e["date"] for e in ranked], ["2026-10-13", "2026-10-12"])

	def test_cards_get_their_flags_and_weather(self):
		frappe.tables["Task"] = [
			{"name": "TASK-1", "custom_outdoor": 1, "custom_customer_visit": 1},
			{"name": "TASK-2", "custom_outdoor": 0, "custom_customer_visit": 0},
			{"name": "TASK-3", "custom_outdoor": 1, "custom_customer_visit": 0},
		]
		cards = [
			{"name": "TASK-1", "project": "PRJ-1", "start": "2026-10-12", "end": "2026-10-13"},
			{"name": "TASK-2", "project": "PRJ-1", "start": "2026-10-12", "end": "2026-10-12"},
			{"name": "TASK-3", "project": "PRJ-1", "start": None, "end": None},
		]
		known = {"TASK-1": {"2026-10-12": [], "2026-10-13": ["Freezing −1 °C"]}}
		with mock.patch.object(weather, "forecast_for_tasks", return_value=known) as fetch:
			api._phase5_cards(cards, str(TODAY))
		self.assertEqual(fetch.call_args[0][0], [{"name": "TASK-1", "project": "PRJ-1"}])
		self.assertEqual((cards[0]["outdoor"], cards[0]["customer_visit"]), (True, True))
		self.assertEqual(cards[0]["weather"], [{"date": "2026-10-13", "flags": ["Freezing −1 °C"]}])
		self.assertEqual((cards[1]["outdoor"], cards[1]["weather"]), (False, None))
		self.assertIsNone(cards[2]["weather"])  # undated: nothing to forecast

	def test_card_details_never_fail_the_planner(self):
		with mock.patch.object(api, "_flag_rows", side_effect=RuntimeError("boom")):
			api._phase5_cards([{"name": "TASK-1"}], str(TODAY))
		self.assertEqual(len(frappe.errors), 1)

	def test_route_stops_on_outdoor_tasks_carry_that_days_flags(self):
		frappe.tables["Task"] = [{"name": "TASK-1", "custom_outdoor": 1, "custom_customer_visit": 0}]
		payload = {
			"date": "2026-10-12",
			"stops": [
				{"kind": "task", "ref": "TASK-1", "project": "PRJ-1"},
				{"kind": "visit", "ref": "SMR-1", "project": "PRJ-2"},
			],
		}
		with mock.patch.object(
			weather, "forecast_for_tasks", return_value={"TASK-1": {"2026-10-12": ["Rain 90%"]}}
		):
			out = api._route_weather(payload)
		self.assertEqual(out["stops"][0]["weather"], ["Rain 90%"])
		self.assertNotIn("weather", out["stops"][1])

	def test_task_points_use_the_route_locator(self):
		with mock.patch.object(routing, "locate", side_effect=_locate) as locate:
			points = weather.task_points([{"name": "TASK-1", "project": "PRJ-1"}])
		self.assertEqual(points, {"TASK-1": (39.7392, -104.9903)})
		self.assertEqual(locate.call_args[0][0], [{"kind": "task", "ref": "TASK-1", "project": "PRJ-1"}])


# ====================================================================== P5.3 who is free


def _availability(people, cells):
	return {
		"resources": people,
		"days": cells,
		"user_to_resource": {},
	}


class TestWhoIsFree(unittest.TestCase):
	PEOPLE = [
		{"name": "RES-1", "label": "Austin Healey", "group": "Field"},
		{"name": "RES-2", "label": "Lisa Park", "group": "Field"},
		{"name": "RES-3", "label": "Korben Fox", "group": "Field"},
		{"name": "RES-4", "label": "Jesse Day", "group": "Field"},
	]

	def setUp(self):
		_reset()
		self.cells = {
			"RES-1": {"2026-10-12": {"capacity": 8, "free": 6, "booked": 2, "bookings": []}},
			"RES-2": {
				"2026-10-12": {"capacity": 0, "free": 0, "booked": 0, "off": "Time off", "bookings": []}
			},
			"RES-3": {
				"2026-10-12": {
					"capacity": 8,
					"free": 0,
					"booked": 8,
					"bookings": [{"kind": "travel", "label": "Trip"}],
				}
			},
			"RES-4": {"2026-10-12": {"capacity": 8, "free": 2, "booked": 6, "bookings": [{"kind": "task"}]}},
		}

	def test_free_people_first_and_a_reason_for_everyone_else(self):
		days = api.free_days(self.PEOPLE, self.cells, D(2026, 10, 12), D(2026, 10, 12), 4)
		self.assertEqual(len(days), 1)
		day = days[0]
		self.assertEqual([e["label"] for e in day["free"]], ["Austin Healey"])
		reasons = {e["label"]: e["reason"] for e in day["not_free"]}
		self.assertEqual(
			reasons,
			{
				"Jesse Day": "Only 2h free (6h booked of 8h)",
				"Korben Fox": "Travelling",
				"Lisa Park": "Time off",
			},
		)
		self.assertEqual([e["label"] for e in day["not_free"]], ["Jesse Day", "Korben Fox", "Lisa Park"])

	def test_the_endpoint_is_gated_bounded_and_never_asks_google(self):
		frappe.roles = []
		with self.assertRaises(_PermissionError):
			api.who_is_free("2026-10-12")
		frappe.roles = ["Maintenance User"]
		with self.assertRaises(_Throw):
			api.who_is_free("2026-10-12", end="2026-11-30")
		with self.assertRaises(_Throw):
			api.who_is_free("2026-10-12", hours="0")
		with self.assertRaises(_Throw):
			api.who_is_free("2026-10-12", end="2026-10-11")
		with mock.patch.object(
			engine, "availability", return_value=_availability(self.PEOPLE, self.cells)
		) as avail:
			answer = api.who_is_free("2026-10-12", end="null", hours="")
		self.assertEqual(avail.call_args, mock.call(D(2026, 10, 12), D(2026, 10, 12), None, google=False))
		self.assertEqual((answer["start"], answer["end"], answer["hours"]), ("2026-10-12", "2026-10-12", 1.0))
		self.assertEqual([e["label"] for e in answer["days"][0]["free"]], ["Austin Healey", "Jesse Day"])

	def test_a_group_narrows_the_people_and_an_empty_group_says_so(self):
		frappe.tables["Planner Resource"] = [
			{"name": "RES-1", "is_active": 1, "resource_group": "Field"},
			{"name": "RES-9", "is_active": 1, "resource_group": "Design"},
		]
		with mock.patch.object(engine, "availability", return_value=_availability([], {})) as avail:
			api.who_is_free("2026-10-12", group="Field")
			answer = api.who_is_free("2026-10-12", group="PM")
		self.assertEqual(avail.call_args_list[0][0][2], ["RES-1"])
		self.assertEqual(len(avail.call_args_list), 1)
		self.assertIn("Nobody active", answer["note"])


class TestWhoIsFreeTool(unittest.TestCase):
	def setUp(self):
		_reset()
		self.tool = tool_module.CrewWhoIsFree()

	def test_the_contract(self):
		self.assertEqual(self.tool.name, "crew_who_is_free")
		self.assertEqual(self.tool.source_app, "erpnext_enhancements")
		self.assertEqual(self.tool.requires_permission, "Planner Resource")
		self.assertIn("Who is free on <day> for <n> hours?", self.tool.description)
		self.assertIn("What is <person> doing next week?", self.tool.description)
		self.assertIn("crew_day_route", self.tool.description)
		self.assertIn("Read-only; it books nothing.", self.tool.description)
		schema = self.tool.inputSchema
		self.assertEqual(schema["required"], ["start"])
		self.assertEqual(set(schema["properties"]), {"start", "end", "hours", "group"})
		self.assertEqual(schema["properties"]["hours"]["type"], "number")
		self.assertNotIn("exclusiveMinimum", json.dumps(schema))  # Gemini's schema subset
		for prop in schema["properties"].values():
			self.assertTrue(prop["description"])

	def test_it_is_read_only_in_the_gate(self):
		self.assertIn("crew_who_is_free", gate.EXPLICIT_READONLY)
		self.assertNotIn("crew_who_is_free", gate.APP_MUTATING)
		self.assertIs(self.tool.annotations["readOnlyHint"], True)

	def test_bad_arguments_never_reach_the_planner(self):
		with mock.patch.object(api, "who_is_free") as call:
			for arguments in (
				{},
				{"start": "June 14"},
				{"start": "2026-10-12", "end": "2026-10-11"},
				{"start": "2026-10-01", "end": "2026-11-15"},
				{"start": "2026-10-12", "hours": 0},
				{"start": "2026-10-12", "hours": "lots"},
				{"start": "2026-10-12", "group": "Office"},
			):
				with self.subTest(arguments=arguments):
					self.assertFalse(self.tool.execute(arguments)["success"])
			call.assert_not_called()

	def test_the_arguments_reach_the_planner(self):
		with mock.patch.object(api, "who_is_free", return_value={"days": []}) as call:
			result = self.tool.execute({"start": "2026-10-12", "hours": "4", "group": "Field"})
		call.assert_called_once_with("2026-10-12", end="2026-10-12", hours=4.0, group="Field")
		self.assertEqual(result, {"success": True, "days": []})


# ====================================================================== P5.4 customer confirmation


class TestConfirmationRules(unittest.TestCase):
	def test_which_tasks_have_a_customer_date(self):
		base = {
			"custom_customer_visit": 1,
			"status": "Open",
			"exp_start_date": "2026-10-15",
			"exp_end_date": "2026-10-16",
		}
		self.assertEqual(confirmations.task_customer_date(base), D(2026, 10, 15))
		for change in (
			{"custom_customer_visit": 0},
			{"custom_tentative": 1},
			{"status": "Completed"},
			{"is_template": 1},
			{"is_group": 1},
			{"exp_start_date": None, "exp_end_date": None},
		):
			with self.subTest(change=change):
				self.assertIsNone(confirmations.task_customer_date(dict(base, **change)))
		self.assertIsNone(confirmations.task_customer_date(None))

	def test_which_visits_have_one(self):
		draft = {"docstatus": 0, "scheduled_visit_date": "2026-10-16"}
		self.assertEqual(confirmations.visit_customer_date(draft), D(2026, 10, 16))
		self.assertIsNone(confirmations.visit_customer_date(dict(draft, docstatus=1)))
		self.assertIsNone(confirmations.visit_customer_date(dict(draft, visit_date="2026-10-16")))
		self.assertIsNone(confirmations.visit_customer_date(dict(draft, scheduled_visit_date=None)))

	def test_when_to_send(self):
		d15, d16 = D(2026, 10, 15), D(2026, 10, 16)
		self.assertTrue(confirmations.needs_sending(None, d15, None, TODAY))
		self.assertTrue(confirmations.needs_sending(d15, d16, "2026-10-15", TODAY))
		self.assertFalse(confirmations.needs_sending(d15, d15, None, TODAY))  # nothing changed
		self.assertFalse(confirmations.needs_sending(d15, d16, "2026-10-16", TODAY))  # already told
		self.assertFalse(confirmations.needs_sending(None, D(2026, 10, 1), None, TODAY))  # in the past
		self.assertFalse(confirmations.needs_sending(d15, None, None, TODAY))

	def test_wording_helpers(self):
		self.assertEqual(confirmations.join_names([]), "")
		self.assertEqual(confirmations.join_names(["Austin"]), "Austin")
		self.assertEqual(confirmations.join_names(["Austin", "Korben", "Jesse"]), "Austin, Korben and Jesse")
		self.assertEqual(confirmations.date_text("2026-10-15"), "Thursday, October 15, 2026")
		slot = (datetime.datetime(2026, 10, 15, 8, 0), datetime.datetime(2026, 10, 15, 13, 30))
		self.assertEqual(confirmations.arrival(slot), ("8:00 AM – 1:30 PM", "8:00 AM", "1:30 PM"))
		self.assertEqual(confirmations.arrival(None), ("", "", ""))

	def test_the_default_message_is_content_only(self):
		body = confirmations.DEFAULT_RESPONSE
		for forbidden in ("max-width", "<table", "style=", "#", "<div"):
			self.assertNotIn(forbidden, body)
		for variable in ("contact_name", "site", "date", "arrival_window", "crew_names", "contact_phone"):
			self.assertIn(variable, body)
		self.assertIn("{{ date }}", confirmations.DEFAULT_SUBJECT)


class TestConfirmationTriggers(unittest.TestCase):
	def setUp(self):
		_reset()
		_site()

	def _on(self):
		frappe.settings["customer_date_confirmations"] = 1

	def _due_writes(self):
		return [
			w
			for w in frappe.writes
			if "custom_customer_confirmation_due" in w[2] or "customer_confirmation_due" in w[2]
		]

	def test_switched_off_nothing_is_queued(self):
		doc = _task()
		doc["__before"] = _task(start="2026-10-14")
		doc.update(exp_start_date="2026-10-15", exp_end_date="2026-10-15")
		confirmations.on_task_update(doc)
		self.assertEqual((frappe.writes, frappe.enqueued), ([], []))

	def test_a_moved_date_is_persisted_as_due_then_enqueued_after_commit(self):
		self._on()
		before = _Doc(_task(start="2026-10-14"))
		doc = _task()
		doc["__before"] = before
		confirmations.on_task_update(doc)
		self.assertEqual(
			frappe.writes, [("Task", "TASK-1", {"custom_customer_confirmation_due": "2026-10-15"}, False)]
		)
		self.assertEqual(
			frappe.enqueued,
			[
				(
					confirmations.JOB,
					{"queue": "short", "enqueue_after_commit": True, "doctype": "Task", "name": "TASK-1"},
				)
			],
		)

	def test_no_email_for_an_unchanged_date_a_pencil_a_told_date_or_a_template_copy(self):
		self._on()
		same = _task()
		same["__before"] = _Doc(_task())
		pencil = _task(name="TASK-2", custom_tentative=1)
		told = _task(name="TASK-3", custom_customer_confirmed_for="2026-10-15")
		copied = _task(name="TASK-4", template_task="TMPL-1")
		for doc in (same, pencil, told, copied):
			confirmations.on_task_update(doc)
		self.assertEqual((frappe.writes, frappe.enqueued), ([], []))

	def test_firming_up_a_pencil_tells_the_customer(self):
		self._on()
		doc = _task()
		doc["__before"] = _Doc(_task(custom_tentative=1))
		confirmations.on_task_update(doc)
		self.assertEqual(len(frappe.enqueued), 1)

	def test_migrate_and_import_are_quiet_and_a_failure_never_raises(self):
		self._on()
		frappe.flags.in_migrate = True
		confirmations.on_task_update(_task())
		self.assertEqual(frappe.enqueued, [])
		frappe.flags = types.SimpleNamespace()
		broken = _task()
		broken["__before"] = 1  # not a document
		confirmations.on_task_update(broken)
		self.assertEqual(len(frappe.errors), 1)

	def test_a_draft_visits_date_set_or_moved(self):
		self._on()
		visit = _visit()
		visit["__before"] = _Doc(_visit(date="2026-10-14"))
		confirmations.on_visit_update(visit)
		submitted = _visit(name="SMR-2", docstatus=1)
		confirmations.on_visit_update(submitted)
		self.assertEqual(
			frappe.writes,
			[("Sapphire Maintenance Record", "SMR-1", {"customer_confirmation_due": "2026-10-16"}, False)],
		)
		self.assertEqual(len(frappe.enqueued), 1)

	def test_the_form_can_never_write_the_stamps(self):
		stored = _task(custom_customer_confirmation_due=None, custom_customer_confirmed_for="2026-10-15")
		posted = _Doc(
			dict(
				stored,
				custom_customer_confirmed_for="2026-10-01",
				custom_customer_confirmation_due="2026-10-15",
			)
		)
		confirmations.keep_stamps(posted)
		self.assertEqual(posted["custom_customer_confirmed_for"], "2026-10-15")
		self.assertIsNone(posted["custom_customer_confirmation_due"])
		fresh = _Doc(
			{"doctype": "Task", "name": "new", "__new": True, "custom_customer_confirmed_for": "2026-10-15"}
		)
		confirmations.keep_stamps(fresh)
		self.assertIsNone(fresh["custom_customer_confirmed_for"])


class TestConfirmationSending(unittest.TestCase):
	def setUp(self):
		_reset()
		_site()
		frappe.settings["customer_date_confirmations"] = 1
		self.locate = mock.patch.object(routing, "locate", side_effect=_locate)
		self.locate.start()

	def tearDown(self):
		self.locate.stop()

	def test_sent_once_through_the_shell_and_stamped_in_the_same_transaction(self):
		_task(custom_customer_confirmation_due="2026-10-15")
		self.assertEqual(confirmations.send_one("Task", "TASK-1"), "sent")
		self.assertIn(("for_update", "Task", "TASK-1"), frappe.calls)
		self.assertEqual(len(frappe.sent), 1)
		mail = frappe.sent[0]
		self.assertEqual(mail["recipients"], ["pat@example.com"])
		self.assertEqual(mail["subject"], "Your Sapphire Fountains visit on Thursday, October 15, 2026")
		self.assertTrue(mail["message"].startswith("<shell>"))
		for text in (
			"Hello Pat",
			"Highlands Plaza",
			"Austin and Korben",
			"1 Main St, Denver",
			"303-555-0100",
		):
			self.assertIn(text, mail["message"])
		wrap = email_style.calls[-1][2]
		self.assertEqual((wrap["pillar"], wrap["tagline"]), ("build", True))
		self.assertEqual(
			frappe.writes[-1],
			(
				"Task",
				"TASK-1",
				{"custom_customer_confirmation_due": None, "custom_customer_confirmed_for": "2026-10-15"},
				False,
			),
		)
		self.assertEqual(confirmations.send_one("Task", "TASK-1"), "skipped")
		self.assertEqual(len(frappe.sent), 1)

	def test_a_date_that_moved_on_or_passed_is_dropped_not_sent(self):
		_task(name="TASK-1", custom_customer_confirmation_due="2026-10-14")  # the task is now on the 15th
		_task(name="TASK-2", start="2026-10-01", custom_customer_confirmation_due="2026-10-01")
		_task(
			name="TASK-3",
			custom_customer_confirmation_due="2026-10-15",
			custom_customer_confirmed_for="2026-10-15",
		)
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.assertEqual(confirmations.send_one("Task", name), "skipped")
			self.assertIsNone(frappe.docs[("Task", name)]["custom_customer_confirmation_due"])
		self.assertEqual(frappe.sent, [])

	def test_no_address_is_skipped_quietly(self):
		frappe.docs[("Contact", "CON-1")]["email_id"] = ""
		_task(custom_customer_confirmation_due="2026-10-15")
		self.assertEqual(confirmations.send_one("Task", "TASK-1"), "skipped")
		self.assertEqual((frappe.sent, frappe.errors), ([], []))

	def test_the_project_email_is_the_last_resort(self):
		frappe.docs[("Contact", "CON-1")]["email_id"] = ""
		frappe.docs[("Project", "PRJ-1")]["custom_customer_email"] = "office@hoa.example.com"
		self.assertEqual(confirmations.recipient("CUST-1", "PRJ-1"), ("office@hoa.example.com", "Pat"))

	def test_what_people_typed_is_escaped_in_the_body_but_not_the_subject(self):
		frappe.docs[("Project", "PRJ-1")]["project_name"] = "A & B <Fountains>"
		frappe.docs[("Email Template", confirmations.TEMPLATE)] = _Doc(
			{"subject": "Visit at {{ site }}", "use_html": 1, "response_html": "<p>{{ site }}</p>"}
		)
		_task(custom_customer_confirmation_due="2026-10-15")
		confirmations.send_one("Task", "TASK-1")
		self.assertEqual(frappe.sent[0]["subject"], "Visit at A & B")  # tags stripped, plain text
		self.assertIn("A &amp; B &lt;Fountains&gt;", frappe.sent[0]["message"])

	def test_a_broken_template_keeps_it_due_and_logs_once(self):
		frappe.docs[("Email Template", confirmations.TEMPLATE)] = _Doc(
			{"subject": "x", "use_html": 1, "response_html": "{% if %}"}
		)
		_task(custom_customer_confirmation_due="2026-10-15")
		self.assertEqual(confirmations.send_one("Task", "TASK-1"), "failed")
		self.assertEqual(confirmations.send_one("Task", "TASK-1"), "failed")
		self.assertEqual(frappe.docs[("Task", "TASK-1")]["custom_customer_confirmation_due"], "2026-10-15")
		self.assertEqual(len(frappe.errors), 1)
		self.assertEqual(frappe.sent, [])

	def test_a_visit_goes_to_its_customer_with_its_crew(self):
		_visit(customer_confirmation_due="2026-10-16")
		self.assertEqual(confirmations.send_one("Sapphire Maintenance Record", "SMR-1"), "sent")
		self.assertIn("Austin and Korben", frappe.sent[0]["message"])
		self.assertEqual(email_style.calls[-1][2]["pillar"], "service")
		self.assertEqual(
			frappe.docs[("Sapphire Maintenance Record", "SMR-1")]["customer_confirmed_for"], "2026-10-16"
		)

	def test_switched_off_the_job_and_the_sweep_send_nothing(self):
		frappe.settings["customer_date_confirmations"] = 0
		_task(custom_customer_confirmation_due="2026-10-15")
		confirmations.send_confirmation("Task", "TASK-1")
		confirmations.send_due_confirmations()
		self.assertEqual(frappe.sent, [])
		self.assertEqual([c for c in frappe.calls if c[0] == "get_all"], [])

	def test_the_sweep_re_drives_what_is_still_due(self):
		_task(name="TASK-1", custom_customer_confirmation_due="2026-10-15")
		_task(name="TASK-2", custom_customer_confirmation_due=None)
		_visit(customer_confirmation_due="2026-10-16")
		confirmations.send_due_confirmations()
		self.assertEqual(len(frappe.sent), 2)
		self.assertEqual(frappe.db_calls.count("commit"), 2)


class TestConfirmationPreview(unittest.TestCase):
	def setUp(self):
		_reset()
		_site()
		self.locate = mock.patch.object(routing, "locate", side_effect=_locate)
		self.locate.start()

	def tearDown(self):
		self.locate.stop()

	def test_the_preview_works_switched_off_and_sends_nothing(self):
		_task()
		answer = confirmations.preview("Task", "TASK-1")
		self.assertFalse(answer["enabled"])
		self.assertFalse(answer["would_send"])
		self.assertEqual(answer["recipient"], "pat@example.com")
		self.assertTrue(answer["html"].startswith("<shell>"))
		self.assertTrue(any("off" in note for note in answer["notes"]))
		self.assertEqual((frappe.sent, frappe.writes, frappe.enqueued), ([], [], []))
		self.assertIn(("check_permission", "read", "TASK-1"), frappe.calls)

	def test_switched_on_it_says_it_would_send(self):
		frappe.settings["customer_date_confirmations"] = 1
		_task()
		answer = confirmations.preview("Task", "TASK-1")
		self.assertTrue(answer["would_send"])
		self.assertEqual(answer["date"], "2026-10-15")
		self.assertEqual(frappe.sent, [])

	def test_it_explains_why_not(self):
		frappe.settings["customer_date_confirmations"] = 1
		_task(custom_customer_visit=0)
		notes = confirmations.preview("Task", "TASK-1")["notes"]
		self.assertTrue(any("not marked" in note for note in notes))
		_task(name="TASK-2", custom_customer_confirmed_for="2026-10-15")
		answer = confirmations.preview("Task", "TASK-2")
		self.assertFalse(answer["would_send"])
		self.assertTrue(any("already told" in note for note in answer["notes"]))

	def test_a_broken_template_shows_its_error(self):
		frappe.docs[("Email Template", confirmations.TEMPLATE)] = _Doc(
			{"subject": "x", "use_html": 1, "response_html": "{% if %}"}
		)
		_task()
		answer = confirmations.preview("Task", "TASK-1")
		self.assertTrue(answer["error"])
		self.assertEqual(answer["html"], "")

	def test_the_endpoint_is_for_managers_and_two_doctypes_only(self):
		_task()
		frappe.roles = ["Projects User"]
		with self.assertRaises(_PermissionError):
			api.preview_customer_confirmation("Task", "TASK-1")
		frappe.roles = ["Projects Manager"]
		with self.assertRaises(_Throw):
			api.preview_customer_confirmation("Customer", "CUST-1")
		answer = api.preview_customer_confirmation("Task", "TASK-1")
		self.assertEqual(answer["recipient"], "pat@example.com")
		self.assertEqual(frappe.sent, [])


class TestSetTaskFlags(unittest.TestCase):
	def setUp(self):
		_reset()
		_site()

	def test_the_flags_are_written_without_touching_modified(self):
		_task(custom_customer_visit=0)
		frappe.roles = ["Projects User"]
		answer = api.set_task_flags("TASK-1", outdoor="true", customer_visit=None)
		self.assertEqual(frappe.writes, [("Task", "TASK-1", {"custom_outdoor": 1}, False)])
		self.assertEqual(
			answer, {"name": "TASK-1", "outdoor": True, "customer_visit": False, "queued": False}
		)
		self.assertIn(("check_permission", "write", "TASK-1"), frappe.calls)
		self.assertEqual(frappe.comments[-1][2], "Info")
		self.assertIn("Outdoor work on", frappe.comments[-1][3])
		frappe.writes.clear()
		api.set_task_flags("TASK-1", outdoor=1)
		self.assertEqual(frappe.writes, [])  # unchanged: nothing written

	def test_it_needs_the_planner_gate_and_write_permission(self):
		_task()
		frappe.roles = []
		with self.assertRaises(_PermissionError):
			api.set_task_flags("TASK-1", outdoor=1)
		frappe.roles = ["Projects User"]
		frappe.denied.add("TASK-1")
		with self.assertRaises(_PermissionError):
			api.set_task_flags("TASK-1", outdoor=1)
		self.assertEqual(frappe.writes, [])

	def test_marking_a_firm_task_customer_facing_tells_the_customer_only_when_switched_on(self):
		_task(custom_customer_visit=0)
		api.set_task_flags("TASK-1", customer_visit=1)
		self.assertEqual(frappe.enqueued, [])
		frappe.settings["customer_date_confirmations"] = 1
		_task(name="TASK-2", custom_customer_visit=0)
		answer = api.set_task_flags("TASK-2", customer_visit="1")
		self.assertTrue(answer["queued"])
		self.assertEqual(frappe.enqueued[0][1]["name"], "TASK-2")


# ====================================================================== wiring


def _hooks():
	values = {}
	for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
			if node.targets[0].id in ("doc_events", "scheduler_events", "assistant_tools"):
				values[node.targets[0].id] = ast.literal_eval(node.value)
	return values


class TestWiring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.hooks = _hooks()

	def test_doc_events(self):
		task = self.hooks["doc_events"]["Task"]
		module = "erpnext_enhancements.project_enhancements"
		self.assertEqual(task["before_insert"], f"{module}.task_templates.copy_template_planning")
		self.assertIn(f"{module}.customer_confirmations.on_task_update", task["on_update"])
		self.assertEqual(task["on_update"][-1], f"{module}.crew_sync.on_task_update")
		self.assertIn(f"{module}.customer_confirmations.keep_stamps", task["validate"])
		visit = self.hooks["doc_events"]["Sapphire Maintenance Record"]
		self.assertIn(f"{module}.customer_confirmations.on_visit_update", visit["on_update"])
		self.assertIn(f"{module}.customer_confirmations.keep_stamps", visit["validate"])

	def test_the_sweep_runs_every_ten_minutes_once(self):
		sweep = "erpnext_enhancements.project_enhancements.customer_confirmations.send_due_confirmations"
		cron = self.hooks["scheduler_events"]["cron"]
		self.assertEqual(cron["*/10 * * * *"].count(sweep), 1)
		self.assertEqual(sum(jobs.count(sweep) for jobs in cron.values()), 1)

	def test_the_tool_is_registered_once(self):
		paths = [p for p in self.hooks["assistant_tools"] if p.split(".")[-2] == "crew_who_is_free"]
		self.assertEqual(paths, ["erpnext_enhancements.assistant_tools.crew_who_is_free.CrewWhoIsFree"])

	def test_whitelisting(self):
		tree = ast.parse(API_PATH.read_text(encoding="utf-8"))
		methods = {}
		for node in tree.body:
			if isinstance(node, ast.FunctionDef):
				for decorator in node.decorator_list:
					target = decorator.func if isinstance(decorator, ast.Call) else decorator
					if getattr(target, "attr", None) == "whitelist":
						methods[node.name] = (
							ast.literal_eval(decorator.keywords[0].value)
							if isinstance(decorator, ast.Call) and decorator.keywords
							else None
						)
		self.assertIsNone(methods["who_is_free"])
		self.assertEqual(methods["set_task_flags"], ["POST"])
		self.assertEqual(methods["preview_customer_confirmation"], ["POST"])

	def test_log_error_is_called_with_keywords(self):
		for path in (
			API_PATH,
			APP / "project_enhancements/planner_weather.py",
			APP / "project_enhancements/customer_confirmations.py",
			APP / "project_enhancements/task_templates.py",
			SEED_PATCH,
		):
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "log_error":
					self.assertEqual(node.args, [], path.name)

	def test_the_weather_module_never_logs_an_exception_message(self):
		source = (APP / "project_enhancements/planner_weather.py").read_text(encoding="utf-8")
		# No str()/repr()/f-string of a caught exception anywhere in the code (the docstring may say so).
		for node in ast.walk(ast.parse(source)):
			if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("str", "repr"):
				self.assertFalse(any(getattr(a, "id", None) == "exc" for a in node.args))
			if isinstance(node, ast.FormattedValue):
				self.assertNotEqual(getattr(node.value, "id", None), "exc")
		self.assertIn("type(exc).__name__", source)
		self.assertIn("timeout=TIMEOUT_S", source)


class TestFieldsAndPatches(unittest.TestCase):
	def test_the_task_fields_are_appended_to_the_fixture(self):
		records = json.loads(CUSTOM_FIELDS.read_text(encoding="utf-8"))
		tail = {r["name"]: r for r in records[-4:]}
		self.assertEqual(
			list(tail),
			[
				"Task-custom_outdoor",
				"Task-custom_customer_visit",
				"Task-custom_customer_confirmation_due",
				"Task-custom_customer_confirmed_for",
			],
		)
		for name in ("Task-custom_outdoor", "Task-custom_customer_visit"):
			self.assertEqual(
				(tail[name]["fieldtype"], tail[name]["default"], tail[name]["dt"]), ("Check", "0", "Task")
			)
		for name in ("Task-custom_customer_confirmation_due", "Task-custom_customer_confirmed_for"):
			field = tail[name]
			self.assertEqual(
				(field["fieldtype"], field["hidden"], field["read_only"], field["no_copy"]), ("Date", 1, 1, 1)
			)
		self.assertEqual(tail["Task-custom_outdoor"]["insert_after"], "custom_tentative")
		self.assertEqual(len({r["name"] for r in records}), len(records))

	def test_the_visit_record_carries_its_stamps(self):
		doc = json.loads(RECORD_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in doc["fields"]}
		for name in confirmations.FIELDS["Sapphire Maintenance Record"]:
			self.assertEqual(
				(fields[name]["fieldtype"], fields[name]["read_only"], fields[name]["no_copy"]),
				("Date", 1, 1),
			)
			self.assertIn(name, doc["field_order"])
		self.assertEqual(doc["field_order"], [f["fieldname"] for f in doc["fields"]])

	def test_the_switch_defaults_off_and_gets_its_row(self):
		doc = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual(
			(
				fields["customer_date_confirmations"]["fieldtype"],
				fields["customer_date_confirmations"]["default"],
			),
			("Check", "0"),
		)
		self.assertNotIn("default", fields["customer_confirmation_phone"])
		self.assertEqual(doc["field_order"], [f["fieldname"] for f in doc["fields"]])
		post = PATCHES_TXT.read_text(encoding="utf-8").split("[post_model_sync]", 1)[1].splitlines()
		for patch in ("materialize_planner_phase5_settings", "seed_planner_date_confirmation_template"):
			self.assertEqual(post.count(f"erpnext_enhancements.patches.{patch}"), 1, patch)
		self.assertIn("materialize_defaults()", SETTINGS_PATCH.read_text(encoding="utf-8"))

	def test_the_template_seed_is_insert_only_and_sends_nothing(self):
		spec = importlib.util.spec_from_file_location("_seed_phase5", SEED_PATCH)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		_reset()
		module.execute()
		self.assertEqual(len(frappe.inserted), 1)
		seeded = frappe.inserted[0]
		self.assertEqual(
			(seeded["name"], seeded["use_html"], seeded["response_html"]),
			(confirmations.TEMPLATE, 1, confirmations.DEFAULT_RESPONSE),
		)
		frappe.docs[("Email Template", confirmations.TEMPLATE)] = _Doc({"subject": "Nik's own"})
		module.execute()
		self.assertEqual(len(frappe.inserted), 1)
		self.assertEqual(frappe.sent, [])


# ====================================================================== the pages


def _bare(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


class TestPages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PAGE_JS.read_text(encoding="utf-8")
		cls.block = cls.code[
			cls.code.index(
				"// ====================================================================== Phase 5"
			) :
		]
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.mp_block = cls.mp[
			cls.mp.index(
				"// ====================================================================== Project Planner Phase 5"
			) :
		]

	def test_the_class_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase5();",
			"chips.push(...this.weather_chips(card, ymd));",
			"...this.weather_lines(card),",
			'this.weather_lines(card).forEach((text) => add(__("Weather"), text));',
			"links.push(...this.phase5_links(card));",
			"...this.phase5_dialog_fields(card),",
			"else this.phase5_action(action, card);",
			"const flags = this.save_phase5_flags(card, values);",
			"${this.suggestion_weather_html(item)}",
			"${this.stop_weather_html(stop)}",
			"Object.assign(ProjectPlanner.prototype, PP5_METHODS);",
		):
			self.assertIn(line, self.code, line)

	def test_the_boxes_and_the_preview_call_their_endpoints(self):
		self.assertIn("${PP.api}.set_task_flags", self.block)
		self.assertIn("${PP.api}.preview_customer_confirmation", self.block)
		self.assertIn('fieldname: "outdoor"', self.block)
		self.assertIn('fieldname: "customer_visit"', self.block)
		self.assertIn('sandbox=""', self.block)
		self.assertIn('.attr("srcdoc", answer.html)', self.block)

	def test_the_block_is_namespaced_and_escapes_what_it_shows(self):
		style = self.code[
			self.code.index("const PP5_STYLE = `") : self.code.index(
				"`;", self.code.index("const PP5_STYLE = `")
			)
		]
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
		self.assertTrue(classes)
		self.assertEqual({c for c in classes if not c.startswith("pp-")}, set())
		self.assertIn("@media (max-width:760px)", style)
		self.assertIn("id='pp-style-5'", self.block)
		self.assertNotIn("localStorage", self.block)
		raw = [
			line.strip()
			for line in self.block.splitlines()
			if "<" in line and re.search(r"\$\{(?:card|item|stop|entry|answer|result)\.\w+", line)
		]
		self.assertEqual(raw, [])
		for forbidden in ("pushState", "replaceState", "window.location", "location.href"):
			self.assertNotIn(forbidden, _bare(self.block))

	def test_the_maintenance_planner_previews_its_visits(self):
		self.assertIn("links.push(...this.phase5_links(card));", self.mp)
		self.assertIn("else this.phase5_action(action, card);", self.mp)
		self.assertIn(
			'preview_method: "erpnext_enhancements.api.project_planner.preview_customer_confirmation"',
			self.mp_block,
		)
		self.assertIn('record: "Sapphire Maintenance Record"', self.mp_block)
		self.assertIn('card.kind === "visit"', self.mp_block)
		self.assertIn('sandbox=""', self.mp_block)
		self.assertIn("Object.assign(MaintenancePlanner.prototype, MP5_METHODS);", self.mp)
		self.assertEqual(self.mp.count("localStorage"), 2)

	def test_the_task_form_has_the_preview_button(self):
		code = TASK_JS.read_text(encoding="utf-8")
		self.assertIn('__("Preview customer email")', code)
		self.assertIn("frm.doc.custom_customer_visit", code)
		self.assertIn("erpnext_enhancements.api.project_planner.preview_customer_confirmation", code)
		self.assertIn('sandbox=""', code)

	def _node(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		return node

	def test_the_javascript_parses(self):
		node = self._node()
		for path in (PAGE_JS, MP_JS, TASK_JS):
			result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=60)
			self.assertEqual(result.returncode, 0, result.stderr)

	def test_the_page_methods_behave(self):
		node = self._node()
		harness = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[process.argv.length - 1], "utf8");
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const calls = [];
const ctx = {
	frappe: {
		pages: { "project-planner": {} },
		utils: { escape_html: esc },
		user: { has_role: (role) => role === "Projects Manager" },
		call: (opts) => { calls.push(opts); return Promise.resolve({ message: { queued: false } }); },
		show_alert: () => {},
	},
	__: (s, args) => String(s).replace(/{(\d+)}/g, (m, i) => (args && args[i] != null ? args[i] : m)),
	moment: (value) => ({ format: () => "Mon, Oct 12" }),
	$: () => ({}),
	document: {},
	window: {},
	console,
};
vm.createContext(ctx);
vm.runInContext(code + "\nthis.ProjectPlanner = ProjectPlanner;", ctx);
const p = Object.create(ctx.ProjectPlanner.prototype);
const card = {
	name: "TASK-1", outdoor: true, customer_visit: true,
	weather: [{ date: "2026-10-12", flags: ["Rain 70%", "Wind <45> km/h"] }],
};
const out = {};
out.day = p.weather_chips(card, "2026-10-12");
out.other_day = p.weather_chips(card, "2026-10-13");
out.tray = p.weather_chips(card, null);
out.none = p.weather_chips({ weather: null }, "2026-10-12");
out.lines = p.weather_lines(card);
out.links = p.phase5_links(card);
out.no_links = p.phase5_links({ customer_visit: false });
out.fields = p.phase5_dialog_fields(card).map((f) => [f.fieldname, f.default]);
p.save_phase5_flags(card, { outdoor: 1, customer_visit: 0 }).then((saved) => {
	out.saved = saved;
	out.sent = calls.map((c) => [c.method, c.args]);
	return p.save_phase5_flags(card, { outdoor: 1, customer_visit: 1 });
}).then((again) => {
	out.unchanged = again;
	out.count = calls.length;
	process.stdout.write(JSON.stringify(out));
});
"""
		result = subprocess.run(
			[node, "-e", harness, str(PAGE_JS)], capture_output=True, text=True, encoding="utf-8", timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		out = json.loads(result.stdout)
		self.assertEqual(len(out["day"]), 1)
		self.assertIn("Rain 70% · Wind &lt;45&gt; km/h", out["day"][0])
		self.assertEqual(out["other_day"], [])
		self.assertIn("Rain 70% · Mon, Oct 12", out["tray"][0])
		self.assertEqual(out["none"], [])
		self.assertEqual(out["lines"], ["Mon, Oct 12: Rain 70%, Wind <45> km/h"])
		self.assertEqual(out["links"], [["customer_preview", "Preview customer email"]])
		self.assertEqual(out["no_links"], [])
		self.assertEqual(out["fields"], [["outdoor", 1], ["customer_visit", 1]])
		self.assertIs(out["saved"], True)
		self.assertEqual(
			out["sent"],
			[
				[
					"erpnext_enhancements.api.project_planner.set_task_flags",
					{"task": "TASK-1", "customer_visit": 0},
				]
			],
		)
		self.assertIs(out["unchanged"], False)
		self.assertEqual(out["count"], 1)


if __name__ == "__main__":
	unittest.main()
