# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar sync (C2, v1.604.0): committed planner work pushed one way to Google Calendar.

What this pins, because each of these fails quietly:

* **The event says the right thing.** Title ``site · subject — crew first names``; an all-day end
  that is Google's exclusive day after; pencil and projected work tentative; finished work kept with
  "✓ "; the "Managed by ERPNext" line with an absolute planner link; no attendees.
* **Nothing secret reaches a shared calendar.** Gate, alarm and lockbox codes, PINs and passwords are
  scrubbed out of the free text that does show, the readers never select a block's note, a profile's
  access codes or any notes field, and a block's title never carries its note.
* **Ids are deterministic and valid base32hex**, so a re-run inserts the same id and a 409 becomes
  an update: no duplicates after a lost job.
* **An unchanged item costs no Google call** (content hash), a moved calendar does.
* **Snap-back's decision table**: other people's events and another site's are never touched; our
  own write echoing back is ignored by etag; an edit is rewritten, a deletion recreated, a stray copy
  or an orphan deleted; a 410 lists in full.
* **Drafts are never read**: no module of the sync names Planner Draft Change or the draft overlays.
* **The master switch off means no Google call at all**, from every job, and no job queued.
* **The preview writes nothing**, to Google or to the database.
* **Triggers never raise into a save** and enqueue once per record per request, after commit.
* **The client backs off** on 429 / 5xx / rate-limit 403s, refreshes once on 401, and no error
  carries a token.

Bench-free: installs its own ``frappe`` and ``requests`` stubs, imports the real modules, and puts
``sys.modules`` back afterwards. Its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_calendar_sync
"""

import ast
import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
SYNC_DIR = APP / "project_enhancements" / "calendar_sync"
API_FILE = APP / "api" / "calendar_sync.py"
HOOKS = APP / "hooks.py"
DOCTYPES = APP / "project_enhancements" / "doctype"
PATCHES_TXT = APP / "patches.txt"
SITE = "erp.test"
TODAY = datetime.date(2026, 10, 12)
NOW = datetime.datetime(2026, 10, 12, 9, 0, 0)

_saved_modules = {}
_modules_before = set()
frappe = None
payload = None
sources = None
engine = None
triggers = None
google_api = None
api = None
requests_stub = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Flags(dict):
	def __getattr__(self, name):
		return self.get(name)

	def __setattr__(self, name, value):
		self[name] = value


class _Doc(dict):
	"""Enough of a Document for the sync: dict and attribute access, get/set, save/insert."""

	def __init__(self, *args, doctype=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.__dict__["flags"] = _Flags()
		self.__dict__["_doctype"] = doctype

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return not self.get("name")

	def get_password(self, fieldname="password", raise_exception=True):
		return self.get(fieldname) or None

	def insert(self):
		frappe.site.counter += 1
		self["name"] = f"LINK-{frappe.site.counter}"
		return self.save()

	def save(self):
		if self.get("source_ref"):
			for other in frappe.site.links.values():
				if other["source_ref"] == self["source_ref"] and other["name"] != self["name"]:
					raise type("UniqueValidationError", (Exception,), {})()
		frappe.site.links[self["name"]] = dict(self)
		frappe.site.writes.append(("link", self["name"], dict(self)))
		return self


class _Site:
	def __init__(self):
		self.links = {}
		self.writes = []
		self.counter = 0
		self.enqueued = []
		self.logs = []
		self.singles = []
		self.commits = 0
		self.cache = {}
		self.roles = ["System Manager"]
		self.settings = _Doc(
			{
				"enabled": 1,
				"refresh_token": "refresh-SECRET",
				"projects_calendar_id": "projects@group.calendar.google.com",
				"maintenance_calendar_id": "maint@group.calendar.google.com",
				"holidays_calendar_id": "holidays@group.calendar.google.com",
				"days_back": 14,
				"days_ahead": 180,
				"colors": [],
				"sync_tokens": "",
			}
		)


def _matches(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, list | tuple):
			op, arg = cond[0], cond[1]
			if op == "!=" and value == arg:
				return False
			if op == "in" and value not in arg:
				return False
			if op == "between":
				low, high = (str(a) for a in arg)
				if not value or not (low <= str(value) <= high):
					return False
		elif value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.site.writes.append(("get_all", doctype))
	if doctype != "Calendar Sync Link":
		return []
	rows = [row for row in frappe.site.links.values() if _matches(row, filters)]
	if pluck:
		return [row.get(pluck) for row in rows]
	return [dict(row) for row in rows]


class _DB:
	def get_value(self, doctype, filters, field=None, *args, **kwargs):
		if doctype == "Calendar Sync Link" and isinstance(filters, dict):
			for row in frappe.site.links.values():
				if _matches(row, filters):
					return row.get(field)
			return None
		return None

	def set_single_value(self, doctype, values, value=None, update_modified=True):
		frappe.site.singles.append((doctype, values if isinstance(values, dict) else {values: value}))

	def commit(self):
		frappe.site.commits += 1

	def exists(self, *args, **kwargs):
		return False

	def table_exists(self, doctype):
		return True

	def has_column(self, doctype, column):
		return True

	def sql(self, *args, **kwargs):
		frappe.site.writes.append(("sql", args[0] if args else ""))
		return []


class _Cache:
	def get_value(self, key):
		return frappe.site.cache.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		frappe.site.cache[key] = value

	def delete_value(self, key):
		frappe.site.cache.pop(key, None)


def _get_doc(doctype, name=None):
	if doctype == "Calendar Sync Link":
		return _Doc(frappe.site.links[name], doctype=doctype)
	if doctype == "Calendar Sync Settings":
		return frappe.site.settings
	raise KeyError(doctype)


def _get_cached_doc(doctype, name=None):
	if doctype in ("Calendar Sync Settings",):
		return frappe.site.settings
	if doctype == "Google Settings":
		return _Doc({"enable": 1, "client_id": "client-123", "client_secret": "client-SECRET"})
	raise KeyError(doctype)


def _enqueue(method, **kwargs):
	frappe.site.enqueued.append((method, kwargs))


def _log_error(title=None, message=None, **kwargs):
	frappe.site.logs.append((title, message))


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _install_frappe():
	module = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_request_site_address = lambda full_address=False: "https://erp.example.com"
	utils.getdate = (
		lambda value=None: TODAY if value is None else datetime.date.fromisoformat(str(value)[:10])
	)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.date_diff = lambda a, b: (utils.getdate(a) - utils.getdate(b)).days
	utils.cint = lambda value: int(float(value or 0))
	utils.add_days = lambda value, days: utils.getdate(value) + datetime.timedelta(days=days)
	utils.formatdate = lambda value=None, *a, **k: str(value)
	module.utils = utils
	module._ = lambda text, *a, **k: text
	module.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	module.throw = _throw
	module.PermissionError = _PermissionError
	module.get_traceback = lambda: "Traceback: boom"
	module.log_error = _log_error
	module.enqueue = _enqueue
	module.get_all = _get_all
	module.get_doc = _get_doc
	module.get_cached_doc = _get_cached_doc
	module.new_doc = lambda doctype: _Doc(doctype=doctype)
	module.clear_document_cache = lambda *a, **k: None
	module.get_roles = lambda user=None: list(frappe.site.roles)
	module.session = _Flags(user="nik@example.com")
	module.local = types.SimpleNamespace(site=SITE)
	module.flags = _Flags()
	module.db = _DB()
	module.cache = _Cache()
	module.site = _Site()

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Doc
	integrations = types.ModuleType("frappe.integrations")
	oauth = types.ModuleType("frappe.integrations.google_oauth")
	oauth.states = []

	def create_state(state):
		oauth.states.append(dict(state))
		return "state-token-1"

	oauth.create_google_oauth_state = create_state
	sys.modules.update(
		{
			"frappe": module,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.integrations": integrations,
			"frappe.integrations.google_oauth": oauth,
		}
	)
	return module


class _Response:
	def __init__(self, status, payload=None, headers=None):
		self.status_code = status
		self._payload = payload
		self.headers = headers or {}
		self.content = b"x" if payload is not None else b""

	def json(self):
		return self._payload


def _install_requests():
	module = types.ModuleType("requests")
	module.script = []
	module.calls = []

	def request(method, url, **kwargs):
		module.calls.append((method, url, kwargs))
		if not module.script:
			raise AssertionError("an unexpected HTTP call to Google")
		return module.script.pop(0)

	def post(url, **kwargs):
		module.calls.append(("POST", url, kwargs))
		if not module.script:
			raise AssertionError("an unexpected HTTP call to Google")
		return module.script.pop(0)

	module.request = request
	module.post = post
	sys.modules["requests"] = module
	return module


def _bare_package(name, path):
	pkg = types.ModuleType(name)
	pkg.__path__ = [str(path)]
	sys.modules[name] = pkg


def setUpModule():
	global frappe, payload, sources, engine, triggers, google_api, api, requests_stub
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = _install_frappe()
	requests_stub = _install_requests()
	importlib.import_module("erpnext_enhancements")
	# Skip the heavy package __init__ files (email, procurement): the sync imports none of them.
	_bare_package("erpnext_enhancements.project_enhancements", APP / "project_enhancements")
	_bare_package("erpnext_enhancements.api", APP / "api")
	payload = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.payload")
	google_api = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.google_api")
	sources = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.sources")
	engine = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.engine")
	triggers = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.triggers")
	api = importlib.import_module("erpnext_enhancements.api.calendar_sync")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


def _reset():
	frappe.site = _Site()
	frappe.flags = _Flags()
	frappe.local = types.SimpleNamespace(site=SITE)
	frappe.session = _Flags(user="nik@example.com")
	requests_stub.script = []
	requests_stub.calls = []


def _task(**overrides):
	item = {
		"doctype": "Task",
		"name": "TASK-2026-00001",
		"kind": "task",
		"calendar": "projects",
		"state": "active",
		"tentative": False,
		"start": datetime.date(2026, 10, 12),
		"end": datetime.date(2026, 10, 14),
		"site": "Riverwalk",
		"label": "Set the pump vault",
		"crew": [{"name": "Austin Smith", "lead": True}, {"name": "Jose Garcia"}],
		"hours": 24,
		"equipment": ["Truck 3"],
		"credentials": ["Confined Space"],
		"weather": ["Tue Oct 13: Rain 70%"],
		"location": "123 Main St, Denver",
		"streams": ["Build"],
		"link": "https://erp.example.com/app/project-planner/week/2026-10-12",
	}
	item.update(overrides)
	return item


def _code_tokens(path, names=False):
	"""Every string literal (and with ``names`` every identifier and import) in a file, docstrings
	excluded: an absence check must not be satisfied or broken by the prose that explains it."""
	tree = ast.parse(Path(path).read_text(encoding="utf-8"))
	docstrings = {
		id(node.value)
		for node in ast.walk(tree)
		if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
	}
	tokens = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			tokens.append(node.value)
		elif names and isinstance(node, ast.Name):
			tokens.append(node.id)
		elif names and isinstance(node, ast.Attribute):
			tokens.append(node.attr)
		elif names and isinstance(node, ast.alias):
			tokens.append(node.name)
		elif names and isinstance(node, ast.ImportFrom):
			tokens.append(node.module or "")
	return "\n".join(tokens)


# ---------------------------------------------------------------------- the event


class EventPayload(unittest.TestCase):
	def test_title_is_site_subject_and_crew_first_names(self):
		self.assertEqual(payload.title_for(_task()), "Riverwalk · Set the pump vault — Austin, Jose")

	def test_finished_keeps_its_event_with_a_check_mark(self):
		self.assertTrue(payload.title_for(_task(state="finished")).startswith("✓ Riverwalk"))
		self.assertEqual(payload.build_event(_task(state="finished"), SITE)["status"], "confirmed")

	def test_pencil_is_tentative_and_marked(self):
		body = payload.build_event(_task(tentative=True), SITE)
		self.assertEqual(body["status"], "tentative")
		self.assertTrue(body["summary"].startswith("✎ "))
		self.assertIn("pencilled in", body["description"])

	def test_projected_visit_is_tentative_and_says_so(self):
		item = _task(
			kind="projected",
			doctype="Sapphire Maintenance Contract",
			key="MNT-1|SN|2026-10-20",
			calendar="maintenance",
		)
		body = payload.build_event(item, SITE)
		self.assertEqual(body["status"], "tentative")
		self.assertIn("(projected)", body["summary"])
		self.assertEqual(body["extendedProperties"]["private"]["erpnext_key"], "MNT-1|SN|2026-10-20")

	def test_all_day_end_is_exclusive(self):
		body = payload.build_event(_task(), SITE)
		self.assertEqual(body["start"], {"date": "2026-10-12"})
		self.assertEqual(body["end"], {"date": "2026-10-15"})
		one = payload.build_event(_task(end=datetime.date(2026, 10, 12)), SITE)
		self.assertEqual(one["end"], {"date": "2026-10-13"})
		backwards = payload.build_event(_task(end=datetime.date(2026, 10, 1)), SITE)
		self.assertEqual(backwards["end"], {"date": "2026-10-13"})

	def test_event_times_is_the_one_seam_for_dates(self):
		self.assertEqual(
			payload.event_times({"start": "2026-10-12", "end": "2026-10-12"}),
			{"start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}},
		)

	def test_description_has_the_facts_and_the_managed_link(self):
		text = payload.build_event(_task(), SITE)["description"]
		for expected in (
			"Hours: 24h over 3 days",
			"Crew: Austin Smith (lead), Jose Garcia",
			"Equipment: Truck 3",
			"Qualifications needed: Confined Space",
			"Weather: Tue Oct 13: Rain 70%",
			"Managed by ERPNext — edits made here are undone. Change it in the planner: "
			"https://erp.example.com/app/project-planner/week/2026-10-12",
		):
			self.assertIn(expected, text)

	def test_no_attendees_and_our_private_properties(self):
		body = payload.build_event(_task(), SITE)
		self.assertNotIn("attendees", body)
		self.assertEqual(
			body["extendedProperties"]["private"],
			{
				"erpnext_site": SITE,
				"erpnext_doctype": "Task",
				"erpnext_name": "TASK-2026-00001",
				"erpnext_kind": "task",
			},
		)
		self.assertEqual(body["reminders"], {"useDefault": False, "overrides": []})

	def test_block_title_never_carries_a_note(self):
		item = {
			"doctype": "Planner Block",
			"name": "PBLK-1",
			"kind": "block",
			"calendar": "holidays",
			"state": "active",
			"start": TODAY,
			"end": TODAY,
			"person": "Austin Smith",
			"time_text": payload.time_range("14:00:00", "16:30:00"),
			"streams": ["Unavailable"],
			"note": "Dentist, alarm code 4412",
		}
		body = payload.build_event(item, SITE)
		self.assertEqual(body["summary"], "Austin — Unavailable 2 pm–4:30 pm")
		self.assertNotIn("Dentist", json.dumps(body))
		self.assertNotIn("4412", json.dumps(body))

	def test_day_note_title_and_audience(self):
		item = {
			"doctype": "Planner Day Note",
			"name": "PDN-1",
			"kind": "day_note",
			"calendar": "projects",
			"state": "active",
			"start": TODAY,
			"end": TODAY,
			"note": "Shop meeting 7 am\nBring boots",
			"audience": "Field",
			"site": "",
			"streams": ["Day Note"],
		}
		body = payload.build_event(item, SITE)
		self.assertEqual(body["summary"], "Note: Shop meeting 7 am (Field)")
		self.assertEqual(body["colorId"], "8")


class NoSecrets(unittest.TestCase):
	def test_scrub_drops_code_segments(self):
		self.assertEqual(payload.scrub("123 Main St, Gate code 4412, Denver"), "123 Main St, Denver")
		self.assertEqual(payload.scrub("Lockbox on the left; key inside"), "key inside")
		self.assertEqual(payload.scrub("Alarm code: 0911"), "")
		self.assertEqual(payload.scrub("PIN 2231 | north door"), "north door")
		self.assertEqual(payload.scrub("Password is fountain1"), "")
		self.assertEqual(payload.scrub("code #4412"), "")
		self.assertEqual(payload.scrub("Building code review"), "Building code review")
		self.assertEqual(payload.scrub(None), "")

	def test_no_code_survives_anywhere_in_the_event(self):
		item = _task(
			label="Replace lid, gate code 7781",
			location="500 Elm St, Lockbox 3321, Boulder",
			equipment=["Truck 3", "Keypad code 1111"],
		)
		text = json.dumps(payload.build_event(item, SITE))
		for secret in ("7781", "3321", "1111"):
			self.assertNotIn(secret, text)
		self.assertIn("Replace lid", text)

	def test_readers_never_select_secret_or_free_text_fields(self):
		"""The readers' code (comments and docstrings stripped) names none of these fields."""
		code = _code_tokens(SYNC_DIR / "sources.py")
		for field in (
			"access_codes",
			"visit_notes",
			"safety_instructions",
			"wrapup_instructions",
			"custom_notes_for_scheduling",
			"description",
			"custom_feature_note",
		):
			self.assertFalse(field in code, f"sources.py names {field}")
		self.assertNotIn("note", sources.BLOCK_FIELDS)

	def test_block_reader_asks_for_no_note(self):
		_reset()
		asked = []

		def get_value(doctype, name, fields, as_dict=False):
			asked.append((doctype, list(fields)))
			return {
				"name": name,
				"resource": "RES-1",
				"resource_name": "Austin Smith",
				"date": "2026-10-13",
				"all_day": 1,
				"from_time": None,
				"to_time": None,
			}

		with mock.patch.object(frappe.db, "get_value", get_value):
			item = sources.block_item("PBLK-1")
		self.assertEqual(asked[0][0], "Planner Block")
		self.assertNotIn("note", asked[0][1])
		self.assertNotIn("note", item)
		self.assertEqual(item["calendar"], "holidays")


class Readers(unittest.TestCase):
	"""The readers' decisions on rows shaped like the database's, with the lookups stubbed."""

	def setUp(self):
		_reset()
		self.row = {
			"name": "TASK-1",
			"subject": "Set the pump vault",
			"project": "PRJ-1",
			"status": "Open",
			"exp_start_date": "2026-10-12 00:00:00",
			"exp_end_date": "2026-10-14 00:00:00",
			"expected_time": 24,
			"is_group": 0,
			"is_template": 0,
			"custom_tentative": 0,
			"custom_rental_booking": None,
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_outdoor": 1,
			"project_title": "Riverwalk Fountain",
			"project_status": "Active",
			"project_type": "Build",
			"planner_stream": 0,
		}
		ca = sources.ca
		self.patches = [
			mock.patch.object(sources, "_task_row", lambda name: dict(self.row) if self.row else None),
			mock.patch.object(
				sources, "_project_row", lambda p: {"name": p, "project_name": "Riverwalk Fountain"}
			),
			mock.patch.object(sources, "_project_streams", lambda p, t=None: [t] if t else []),
			mock.patch.object(
				sources, "_locate", lambda kind, ref, project: ((39.7, -104.9), "1 Plaza Way, Denver")
			),
			mock.patch.object(sources, "_weather", lambda point, a, b: ["Tue Oct 13: Rain 70%"]),
			mock.patch.object(sources, "_credentials", lambda name: ["Confined Space"]),
			mock.patch.object(
				ca,
				"read_crews",
				lambda names: (
					{
						"TASK-1": [
							{"resource": "RES-2", "resource_name": "Jose Garcia", "hours": 0, "is_lead": 0},
							{"resource": "RES-1", "resource_name": "Austin Smith", "hours": 0, "is_lead": 1},
						]
					},
					{},
				),
			),
			mock.patch.object(
				ca,
				"read_equipment",
				lambda names: {"TASK-1": [{"type": "Vehicle", "name": "T3", "label": "Truck 3"}]},
			),
		]
		for patcher in self.patches:
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_a_firm_customer_job_task(self):
		item = sources.task_item("TASK-1")
		self.assertEqual(item["state"], "active")
		self.assertEqual(
			(item["start"], item["end"]), (datetime.date(2026, 10, 12), datetime.date(2026, 10, 14))
		)
		self.assertEqual(item["calendar"], "projects")
		self.assertEqual(item["site"], "Riverwalk Fountain")
		self.assertEqual([c["first"] for c in item["crew"]], ["Austin", "Jose"])
		self.assertTrue(item["crew"][0]["lead"])
		self.assertEqual(item["equipment"], ["Truck 3"])
		self.assertEqual(item["weather"], ["Tue Oct 13: Rain 70%"])
		self.assertEqual(item["link"], "https://erp.example.com/app/project-planner/week/2026-10-12")
		title = payload.build_event(item, SITE)["summary"]
		self.assertEqual(title, "Riverwalk Fountain · Set the pump vault — Austin, Jose")

	def test_task_states(self):
		cases = [
			({}, "active"),
			({"custom_tentative": 1}, "active"),
			({"status": "Completed"}, "finished"),
			({"project_status": "Invoiced"}, "finished"),
			({"status": "Canceled"}, "gone"),
			({"project_status": "Canceled"}, "gone"),
			({"is_group": 1}, "gone"),
			({"is_template": 1}, "gone"),
			({"exp_start_date": None, "exp_end_date": None}, "gone"),
			({"project_type": "Internal"}, "gone"),
			({"project_type": "Internal", "planner_stream": 1}, "active"),
			({"project": None, "project_type": ""}, "gone"),
			({"project": None, "project_type": "", "custom_rental_booking": "RB-1"}, "active"),
		]
		for change, expected in cases:
			row = dict(self.row, **change)
			self.assertEqual(sources.task_state(row), expected, change)
		self.assertEqual(sources.task_state(None), "gone")

	def test_a_finished_task_has_no_weather_and_a_check(self):
		self.row["status"] = "Completed"
		item = sources.task_item("TASK-1")
		self.assertEqual(item["weather"], [])
		self.assertTrue(payload.title_for(item).startswith("✓ "))

	def test_an_undated_or_internal_task_is_gone(self):
		self.row["exp_start_date"] = self.row["exp_end_date"] = None
		self.assertEqual(sources.task_item("TASK-1")["state"], "gone")
		self.row = None
		self.assertEqual(sources.task_item("TASK-1")["state"], "gone")

	def test_visit_states_and_day(self):
		draft = {
			"docstatus": 0,
			"workflow_state": "Draft",
			"scheduled_visit_date": "2026-10-20",
			"visit_date": None,
		}
		self.assertEqual(sources.visit_state(draft), "active")
		self.assertEqual(sources.visit_day(draft), datetime.date(2026, 10, 20))
		review = dict(draft, workflow_state="Pending Review", visit_date="2026-10-21")
		self.assertEqual(sources.visit_state(review), "finished")
		self.assertEqual(sources.visit_day(review), datetime.date(2026, 10, 21))
		self.assertEqual(sources.visit_state(dict(draft, docstatus=1, visit_date="2026-10-19")), "finished")
		self.assertEqual(sources.visit_state(dict(draft, docstatus=2)), "gone")
		self.assertEqual(sources.visit_state(dict(draft, scheduled_visit_date=None)), "gone")

	def test_projected_key_carries_its_date(self):
		with mock.patch.object(sources, "projected_items", return_value=[]) as found:
			item = sources.projected_item("MNT-1", "MNT-1|SN-1|2026-10-20")
		found.assert_called_once_with(datetime.date(2026, 10, 20), datetime.date(2026, 10, 20))
		self.assertEqual(item["state"], "gone")


class Ids(unittest.TestCase):
	def test_ids_are_valid_and_deterministic(self):
		one = payload.event_id(SITE, "Task", "TASK-1")
		self.assertTrue(payload.is_valid_event_id(one))
		self.assertTrue(5 <= len(one) <= 1024)
		self.assertRegex(one, r"^[a-v0-9]+$")
		self.assertEqual(one, payload.event_id(SITE, "Task", "TASK-1"))
		self.assertNotEqual(one, payload.event_id("staging.test", "Task", "TASK-1"))
		self.assertNotEqual(one, payload.event_id(SITE, "Planner Block", "TASK-1"))
		self.assertNotEqual(
			payload.event_id(SITE, "Sapphire Maintenance Contract", "C", "C|SN|2026-10-20"),
			payload.event_id(SITE, "Sapphire Maintenance Contract", "C", "C|SN|2026-10-27"),
		)
		self.assertTrue(payload.is_valid_event_id(payload.event_id(SITE, "Task", "TASK-1", generation=2)))
		self.assertFalse(payload.is_valid_event_id("TASK-1"))


class Colors(unittest.TestCase):
	def test_defaults_aliases_and_overrides(self):
		self.assertEqual(payload.color_for({"streams": ["Build"]}), "9")
		self.assertEqual(payload.color_for({"streams": ["Rent"]}), "6")
		self.assertEqual(payload.color_for({"streams": ["Internal", "Service"]}), "10")
		self.assertEqual(payload.color_for({"streams": ["Design"]}, {"Design": "11"}), "11")
		self.assertEqual(payload.color_for({"streams": ["Design"]}, {"Design": "99"}), "3")
		self.assertIsNone(payload.color_for({"streams": ["Internal"]}))
		self.assertNotIn("colorId", payload.build_event(_task(streams=[]), SITE))

	def test_every_default_is_a_google_color(self):
		for value in payload.DEFAULT_COLORS.values():
			self.assertTrue(1 <= int(value) <= 11)


class SnapbackTable(unittest.TestCase):
	CAL = "projects@group.calendar.google.com"

	def _event(self, **kw):
		event = {
			"id": "erpabc",
			"etag": '"e1"',
			"status": "confirmed",
			"extendedProperties": {
				"private": {"erpnext_site": SITE, "erpnext_doctype": "Task", "erpnext_name": "T"}
			},
		}
		event.update(kw)
		return event

	def decide(self, event, state="active", item_cal=CAL, etag='"e1"', expected="erpabc"):
		return payload.snapback_action(event, SITE, state, item_cal, self.CAL, etag, expected)

	def test_not_ours_is_ignored(self):
		self.assertEqual(self.decide({"id": "x", "status": "confirmed"}), payload.IGNORE)
		other = self._event(
			extendedProperties={"private": {"erpnext_site": "staging.test", "erpnext_doctype": "Task"}}
		)
		self.assertEqual(self.decide(other, state="gone"), payload.IGNORE)

	def test_our_own_write_echoing_back_is_ignored(self):
		self.assertEqual(self.decide(self._event()), payload.IGNORE)

	def test_edited_is_rewritten(self):
		self.assertEqual(self.decide(self._event(etag='"e9"')), payload.REWRITE)

	def test_deleted_in_google_is_recreated(self):
		self.assertEqual(self.decide(self._event(status="cancelled")), payload.RECREATE)

	def test_source_gone(self):
		self.assertEqual(self.decide(self._event(), state="gone"), payload.DELETE)
		self.assertEqual(self.decide(self._event(status="cancelled"), state="gone"), payload.FORGET)

	def test_stray_copy_is_deleted(self):
		self.assertEqual(self.decide(self._event(id="erpcopy", etag='"e5"')), payload.DELETE)

	def test_wrong_calendar_is_deleted_here(self):
		self.assertEqual(self.decide(self._event(), item_cal="other@calendar"), payload.DELETE)
		self.assertEqual(
			self.decide(self._event(status="cancelled"), item_cal="other@calendar"), payload.IGNORE
		)

	def test_unconfigured_calendar_is_left_alone(self):
		self.assertEqual(self.decide(self._event(etag='"e7"'), item_cal=""), payload.IGNORE)


# ---------------------------------------------------------------------- engine


class FakeGoogle:
	def __init__(self):
		self.events = {}
		self.calls = []
		self.n = 0
		self.pages = {}
		self.fail = None

	def _etag(self):
		self.n += 1
		return f'"e{self.n}"'

	def insert_event(self, cal, body):
		self.calls.append(("insert", cal, body["id"]))
		if self.fail:
			raise self.fail
		if (cal, body["id"]) in self.events:
			raise google_api.GoogleCalendarError(409, "duplicate")
		event = dict(body, etag=self._etag())
		self.events[(cal, body["id"])] = event
		return event

	def update_event(self, cal, event_id, body):
		self.calls.append(("update", cal, event_id))
		if self.fail:
			raise self.fail
		if (cal, event_id) not in self.events:
			raise google_api.GoogleCalendarError(404, "notFound")
		event = dict(body, id=event_id, etag=self._etag())
		self.events[(cal, event_id)] = event
		return event

	def delete_event(self, cal, event_id):
		self.calls.append(("delete", cal, event_id))
		if (cal, event_id) in self.events:
			self.events[(cal, event_id)]["status"] = "cancelled"
		return True

	def list_events(self, cal, sync_token=None, page_token=None):
		self.calls.append(("list", cal, sync_token))
		pages = self.pages.get(cal) or []
		if not pages:
			return {"items": [], "nextSyncToken": f"tok-{cal[:5]}"}
		page = pages.pop(0)
		if isinstance(page, Exception):
			raise page
		return page


class EngineCase(unittest.TestCase):
	def setUp(self):
		_reset()
		self.google = FakeGoogle()
		self.built = []

		def factory():
			self.built.append(1)
			return self.google

		patcher = mock.patch.object(google_api, "Client", factory)
		patcher.start()
		self.addCleanup(patcher.stop)

	def reconcile(self, item, **kw):
		return engine.reconcile(item, client=self.google, **kw)

	def link(self):
		return next(iter(frappe.site.links.values()))


class Reconcile(EngineCase):
	def test_create_then_unchanged_costs_no_call(self):
		self.assertEqual(self.reconcile(_task())[0], "create")
		link = self.link()
		self.assertEqual(link["status"], "Synced")
		self.assertEqual(link["event_id"], payload.event_id(SITE, "Task", "TASK-2026-00001"))
		self.assertEqual(link["etag"], '"e1"')
		self.assertEqual(link["kind"], "Task")
		calls = len(self.google.calls)
		self.assertEqual(self.reconcile(_task())[0], "unchanged")
		self.assertEqual(len(self.google.calls), calls)

	def test_a_change_updates_in_place(self):
		self.reconcile(_task())
		self.assertEqual(self.reconcile(_task(label="Set the vault lid"))[0], "update")
		self.assertEqual(self.google.calls[-1][0], "update")
		self.assertEqual(len(frappe.site.links), 1)

	def test_a_rerun_after_a_lost_link_updates_instead_of_duplicating(self):
		event_id = payload.event_id(SITE, "Task", "TASK-2026-00001")
		self.google.events[("projects@group.calendar.google.com", event_id)] = {"id": event_id}
		self.assertEqual(self.reconcile(_task())[0], "create")
		self.assertEqual([c[0] for c in self.google.calls], ["insert", "update"])
		self.assertEqual(len(self.google.events), 1)

	def test_gone_deletes_once(self):
		self.reconcile(_task())
		gone = {"doctype": "Task", "name": "TASK-2026-00001", "state": "gone"}
		self.assertEqual(self.reconcile(gone)[0], "delete")
		self.assertEqual(self.link()["status"], "Deleted")
		calls = len(self.google.calls)
		self.assertEqual(self.reconcile(gone)[0], "none")
		self.assertEqual(len(self.google.calls), calls)

	def test_a_calendar_change_moves_the_event(self):
		self.reconcile(_task())
		frappe.site.settings["projects_calendar_id"] = "new@group.calendar.google.com"
		self.assertEqual(self.reconcile(_task())[0], "update")
		kinds = [(c[0], c[1]) for c in self.google.calls[1:]]
		self.assertEqual(kinds[0], ("delete", "projects@group.calendar.google.com"))
		self.assertEqual(kinds[1], ("insert", "new@group.calendar.google.com"))
		self.assertEqual(self.link()["calendar_id"], "new@group.calendar.google.com")

	def test_no_calendar_writes_nothing(self):
		frappe.site.settings["holidays_calendar_id"] = ""
		item = {
			"doctype": "Planner Block",
			"name": "PBLK-1",
			"kind": "block",
			"calendar": "holidays",
			"state": "active",
			"start": TODAY,
			"end": TODAY,
			"person": "Austin",
		}
		self.assertEqual(self.reconcile(item)[0], "no_calendar")
		self.assertEqual(self.google.calls, [])

	def test_an_error_is_recorded_and_logged_once(self):
		self.google.fail = google_api.GoogleCalendarError(500, "backendError", "try later")
		self.assertEqual(self.reconcile(_task())[0], "error")
		self.assertEqual(self.reconcile(_task())[0], "error")
		self.assertEqual(self.link()["status"], "Error")
		self.assertEqual(self.link()["error_count"], 2)
		self.assertEqual(len(frappe.site.logs), 1)
		self.assertNotIn("SECRET", json.dumps(frappe.site.logs))


class MasterSwitch(EngineCase):
	def run_every_job(self):
		with (
			mock.patch.object(sources, "item_for", side_effect=AssertionError("read while off")),
			mock.patch.object(sources, "refs_in_window", side_effect=AssertionError("read while off")),
			mock.patch.object(sources, "projected_items", side_effect=AssertionError("read while off")),
		):
			engine.run_item_job("Task", "TASK-1")
			engine.run_projections_job()
			engine.run_project_job("PRJ-1")
			engine.run_full_repair()
			engine.nightly_repair()
			engine.snapback()

	def test_off_means_no_google_call(self):
		frappe.site.settings["enabled"] = 0
		self.run_every_job()
		self.assertEqual(self.built, [])
		self.assertEqual(self.google.calls, [])
		self.assertEqual(requests_stub.calls, [])
		self.assertEqual(frappe.site.singles, [])

	def test_on_without_an_account_means_no_google_call(self):
		frappe.site.settings["refresh_token"] = ""
		self.run_every_job()
		self.assertEqual(self.built, [])
		self.assertEqual(requests_stub.calls, [])

	def test_off_queues_nothing(self):
		frappe.site.settings["enabled"] = 0
		triggers.on_change(_Doc({"doctype": "Task", "name": "TASK-1"}))
		self.assertEqual(frappe.site.enqueued, [])


class DryRun(EngineCase):
	def test_preview_writes_nothing(self):
		self.reconcile(_task(name="TASK-OLD"))
		before_links = json.dumps(frappe.site.links, default=str)
		writes_before = [w for w in frappe.site.writes if w[0] == "link"]
		self.google.calls.clear()
		items = {
			("Task", "TASK-NEW"): _task(name="TASK-NEW"),
			("Task", "TASK-OLD"): {"doctype": "Task", "name": "TASK-OLD", "state": "gone"},
		}
		projected = [
			_task(
				doctype="Sapphire Maintenance Contract",
				name="MNT-1",
				key="MNT-1||2026-10-20",
				kind="projected",
				calendar="maintenance",
			)
		]
		with (
			mock.patch.object(sources, "refs_in_window", return_value=list(items)),
			mock.patch.object(sources, "item_for", side_effect=lambda d, n, k=None: items[(d, n)]),
			mock.patch.object(sources, "projected_items", return_value=projected),
		):
			for enabled in (0, 1):
				frappe.site.settings["enabled"] = enabled
				result = engine.plan("2026-10-01", "2026-12-31")
		self.assertEqual({r["action"] for r in result["rows"]}, {"create", "delete"})
		self.assertEqual(len(result["rows"]), 3)
		self.assertEqual(self.google.calls, [])
		self.assertEqual(self.built, [])
		self.assertEqual(json.dumps(frappe.site.links, default=str), before_links)
		self.assertEqual([w for w in frappe.site.writes if w[0] == "link"], writes_before)
		self.assertEqual(frappe.site.singles, [])

	def test_preview_range_is_bounded(self):
		with self.assertRaises(_Throw):
			engine.plan("2026-01-01", "2027-12-31")


class Snapback(EngineCase):
	CAL = "projects@group.calendar.google.com"

	def test_decisions_applied_and_token_stored(self):
		engine.reconcile(_task(), client=self.google)
		link = self.link()
		ours = dict(self.google.events[(self.CAL, link["event_id"])])
		edited = dict(ours, etag='"someone"', summary="Moved by hand")
		foreign = {"id": "abc123", "etag": '"x"', "status": "confirmed", "summary": "Lunch"}
		other_site = dict(
			ours,
			id="erpzzz",
			extendedProperties={
				"private": {"erpnext_site": "staging.test", "erpnext_doctype": "Task", "erpnext_name": "T"}
			},
		)
		self.google.pages[self.CAL] = [
			{"items": [ours, foreign, other_site], "nextPageToken": "p2"},
			{"items": [edited], "nextSyncToken": "tok-new"},
		]
		self.google.calls.clear()
		order = []
		with (
			mock.patch.object(sources, "item_for", return_value=_task()),
			mock.patch(
				"erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings.materialize_defaults",
				side_effect=lambda: order.append("materialize"),
			),
			mock.patch.object(
				frappe.db, "set_single_value", side_effect=lambda *a, **k: order.append(("set", a))
			),
		):
			engine.snapback()
		writes = [c for c in self.google.calls if c[0] in ("insert", "update", "delete")]
		self.assertEqual(writes, [("update", self.CAL, link["event_id"])])
		self.assertEqual(order[0], "materialize")
		values = order[1][1][1]
		tokens = json.loads(values["sync_tokens"])
		self.assertEqual(tokens[self.CAL], "tok-new")
		self.assertNotIn("abc123", json.dumps(self.google.calls))

	def test_source_gone_deletes_and_410_lists_in_full(self):
		engine.reconcile(_task(), client=self.google)
		link = self.link()
		ours = dict(self.google.events[(self.CAL, link["event_id"])], etag='"other"')
		frappe.site.settings["sync_tokens"] = json.dumps({self.CAL: "stale"})
		self.google.pages[self.CAL] = [
			google_api.GoogleCalendarError(410, "fullSyncRequired"),
			{"items": [ours], "nextSyncToken": "fresh"},
		]
		with mock.patch.object(
			sources, "item_for", return_value={"doctype": "Task", "name": "TASK-2026-00001", "state": "gone"}
		):
			engine.snapback()
		lists = [c for c in self.google.calls if c[0] == "list" and c[1] == self.CAL]
		self.assertEqual([c[2] for c in lists[-2:]], ["stale", None])
		self.assertIn(("delete", self.CAL, link["event_id"]), self.google.calls)
		self.assertEqual(self.link()["status"], "Deleted")
		tokens = json.loads(frappe.site.singles[-1][1]["sync_tokens"])
		self.assertEqual(tokens[self.CAL], "fresh")


class FullRepair(EngineCase):
	def test_repairs_window_and_removes_stale_projections(self):
		stale = _task(
			doctype="Sapphire Maintenance Contract",
			name="MNT-1",
			key="MNT-1||2026-10-20",
			kind="projected",
			calendar="maintenance",
		)
		engine.reconcile(stale, client=self.google)
		items = {("Task", "TASK-A"): _task(name="TASK-A")}
		with (
			mock.patch.object(sources, "refs_in_window", return_value=list(items)),
			mock.patch.object(sources, "item_for", side_effect=lambda d, n, k=None: items[(d, n)]),
			mock.patch.object(sources, "projected_items", return_value=[]),
			mock.patch.object(
				sources, "window", return_value=(datetime.date(2026, 9, 28), datetime.date(2027, 4, 10))
			),
		):
			counts = engine.run_full_repair()
		self.assertEqual(counts.get("create"), 1)
		self.assertEqual(counts.get("delete"), 1)
		statuses = {row["source_name"]: row["status"] for row in frappe.site.links.values()}
		self.assertEqual(statuses, {"MNT-1": "Deleted", "TASK-A": "Synced"})
		self.assertIn("last_full_repair", frappe.site.singles[-1][1])

	def test_twice_is_safe(self):
		items = {("Task", "TASK-A"): _task(name="TASK-A")}
		with (
			mock.patch.object(sources, "refs_in_window", return_value=list(items)),
			mock.patch.object(sources, "item_for", side_effect=lambda d, n, k=None: items[(d, n)]),
			mock.patch.object(sources, "projected_items", return_value=[]),
		):
			engine.run_full_repair()
			second = engine.run_full_repair()
		self.assertEqual(second, {"unchanged": 1})
		self.assertEqual(len(self.google.events), 1)


# ---------------------------------------------------------------------- triggers


class Triggers(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_once_per_record_per_request_after_commit(self):
		frappe.flags.planner_publishing = True
		for name in ("T1", "T2", "T1", "T1", "T2"):
			triggers.on_change(_Doc({"doctype": "Task", "name": name}))
		self.assertEqual(len(frappe.site.enqueued), 2)
		method, kwargs = frappe.site.enqueued[0]
		self.assertEqual(method, triggers.ITEM_JOB)
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertEqual(kwargs["job_id"], "calendar_sync::Task::T1")
		self.assertEqual((kwargs["doctype"], kwargs["name"]), ("Task", "T1"))

	def test_skipped_during_migrate_import_and_tests(self):
		for flag in ("in_migrate", "in_import", "in_patch", "in_install", "in_test"):
			_reset()
			frappe.flags[flag] = True
			triggers.on_change(_Doc({"doctype": "Task", "name": "T1"}))
			self.assertEqual(frappe.site.enqueued, [], flag)

	def test_never_raises_into_the_save(self):
		with mock.patch.object(frappe, "enqueue", side_effect=RuntimeError("redis down")):
			triggers.on_change(_Doc({"doctype": "Task", "name": "T1"}))
		self.assertEqual(frappe.site.logs[0][0], "Calendar sync: change not queued")

	def test_settings_doctype_missing_reads_as_off(self):
		with mock.patch.object(frappe, "get_cached_doc", side_effect=Exception("no such doctype")):
			triggers.on_change(_Doc({"doctype": "Task", "name": "T1"}))
		self.assertEqual(frappe.site.enqueued, [])

	def test_a_visit_also_reprojects_its_contract_once(self):
		for _ in range(3):
			triggers.on_change(
				_Doc(
					{
						"doctype": "Sapphire Maintenance Record",
						"name": "SMR-1",
						"maintenance_contract": "MNT-1",
					}
				)
			)
		methods = [m for m, _k in frappe.site.enqueued]
		self.assertEqual(methods, [triggers.ITEM_JOB, triggers.PROJECTIONS_JOB])

	def test_project_change_only_on_fields_that_show(self):
		before = _Doc({"project_name": "Riverwalk", "status": "Active", "notes": "a"})
		doc = _Doc(
			{
				"doctype": "Project",
				"name": "PRJ-1",
				"project_name": "Riverwalk",
				"status": "Active",
				"notes": "b",
			}
		)
		doc.get_doc_before_save = lambda: before
		triggers.on_project_change(doc)
		self.assertEqual(frappe.site.enqueued, [])
		doc["status"] = "Canceled"
		triggers.on_project_change(doc)
		self.assertEqual(frappe.site.enqueued[0][0], triggers.PROJECT_JOB)


# ---------------------------------------------------------------------- Google client and sign-in


class GoogleClient(unittest.TestCase):
	def setUp(self):
		_reset()
		self.sleeps = []
		for target, value in (
			("_sleep", self.sleeps.append),
			("access_token", lambda force=False: "ya29.ACCESS-SECRET"),
		):
			patcher = mock.patch.object(google_api, target, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_backs_off_on_429_and_rate_limit_403(self):
		rate = {"error": {"errors": [{"reason": "rateLimitExceeded"}], "message": "slow down"}}
		requests_stub.script = [
			_Response(429, {}, {"Retry-After": "3"}),
			_Response(403, rate),
			_Response(200, {"id": "ok"}),
		]
		self.assertEqual(google_api.Client().request("GET", "/x"), {"id": "ok"})
		self.assertEqual(len(self.sleeps), 2)
		self.assertEqual(self.sleeps[0], 3.0)

	def test_a_plain_403_is_not_retried_and_carries_no_token(self):
		forbidden = {
			"error": {"errors": [{"reason": "forbidden"}], "message": "You need to have writer access"}
		}
		requests_stub.script = [_Response(403, forbidden)]
		with self.assertRaises(google_api.GoogleCalendarError) as caught:
			google_api.Client().request("POST", "/calendars/x/events", body={"summary": "s"})
		self.assertEqual(caught.exception.status, 403)
		self.assertEqual(caught.exception.reason, "forbidden")
		self.assertNotIn("SECRET", str(caught.exception))
		self.assertIsNone(caught.exception.__cause__)
		self.assertTrue(caught.exception.__suppress_context__)
		self.assertEqual(self.sleeps, [])

	def test_401_refreshes_once(self):
		requests_stub.script = [_Response(401, {}), _Response(200, {"id": "ok"})]
		self.assertEqual(google_api.Client().request("GET", "/x"), {"id": "ok"})
		requests_stub.script = [_Response(401, {}), _Response(401, {})]
		with self.assertRaises(google_api.GoogleCalendarError):
			google_api.Client().request("GET", "/x")

	def test_a_404_on_delete_counts_as_deleted(self):
		requests_stub.script = [_Response(410, {"error": {"errors": [{"reason": "deleted"}]}})]
		self.assertTrue(google_api.Client().delete_event("cal", "erpabc"))

	def test_writes_never_invite_anyone(self):
		requests_stub.script = [_Response(200, {"id": "erpabc", "etag": '"1"'})]
		google_api.Client().insert_event("cal@x", {"id": "erpabc"})
		self.assertEqual(requests_stub.calls[0][2]["params"], {"sendUpdates": "none"})


class SignIn(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_consent_url_asks_for_two_calendar_scopes_and_nothing_more(self):
		url = google_api.authorization_url("nik@example.com")
		from urllib.parse import parse_qs, urlsplit

		query = parse_qs(urlsplit(url).query)
		self.assertEqual(
			query["scope"][0].split(),
			[
				"https://www.googleapis.com/auth/calendar.events",
				"https://www.googleapis.com/auth/calendar.readonly",
			],
		)
		self.assertNotIn("include_granted_scopes", query)
		self.assertEqual(query["access_type"], ["offline"])
		self.assertEqual(
			query["redirect_uri"],
			["https://erp.example.com/api/method/frappe.integrations.google_oauth.callback"],
		)
		self.assertEqual(query["state"], ["state-token-1"])
		state = sys.modules["frappe.integrations.google_oauth"].states[-1]
		self.assertEqual(state["callback_method"], "erpnext_enhancements.api.calendar_sync.oauth_callback")
		self.assertEqual(state["user"], "nik@example.com")
		self.assertNotIn("client-SECRET", url)

	def test_a_refused_refresh_names_no_secret(self):
		requests_stub.script = [
			_Response(
				400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
			)
		]
		with self.assertRaises(google_api.GoogleAuthError) as caught:
			google_api.access_token(force=True)
		self.assertNotIn("SECRET", str(caught.exception))
		self.assertEqual(caught.exception.reason, "invalid_grant")

	def test_endpoints_are_system_manager_only(self):
		frappe.site.roles = ["Projects Manager"]
		for call in (
			api.authorize_url,
			api.disconnect,
			api.list_calendars,
			api.health,
			api.preview,
			api.resync_all,
		):
			with self.assertRaises(_PermissionError):
				call()

	def test_callback_refuses_another_user(self):
		with self.assertRaises(_PermissionError):
			api.oauth_callback(code="abc", user="someone@example.com")
		self.assertEqual(requests_stub.calls, [])

	def test_callback_is_not_whitelisted(self):
		tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name == "oauth_callback":
				self.assertEqual(node.decorator_list, [])


# ---------------------------------------------------------------------- static wiring


def _literal(name):
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(name)


def _as_list(value):
	return value if isinstance(value, list) else [value]


class Wiring(unittest.TestCase):
	TRIG = "erpnext_enhancements.project_enhancements.calendar_sync.triggers.on_change"

	def test_drafts_are_never_read(self):
		files = [*sorted(SYNC_DIR.glob("*.py")), API_FILE]
		for path in files:
			code = _code_tokens(path, names=True)
			for banned in (
				"Planner Draft Change",
				"maintenance_drafts",
				"draft_overlay",
				"with_drafts",
				"planner_draft",
			):
				self.assertFalse(banned in code, f"{path.name} names {banned}")

	def test_doc_events(self):
		events = _literal("doc_events")
		self.assertIn(self.TRIG, _as_list(events["Task"]["on_update"]))
		self.assertIn(self.TRIG, _as_list(events["Task"]["on_trash"]))
		self.assertEqual(
			_as_list(events["Task"]["on_update"])[-1],
			"erpnext_enhancements.project_enhancements.crew_sync.on_task_update",
		)
		for event in ("on_update", "on_submit", "on_cancel", "on_trash", "on_update_after_submit"):
			self.assertIn(self.TRIG, _as_list(events["Sapphire Maintenance Record"][event]), event)
		for doctype in ("Planner Day Note", "Planner Block"):
			self.assertIn(self.TRIG, _as_list(events[doctype]["on_update"]))
			self.assertIn(self.TRIG, _as_list(events[doctype]["on_trash"]))
		self.assertIn(
			"erpnext_enhancements.project_enhancements.calendar_sync.triggers.on_contract_change",
			_as_list(events["Sapphire Maintenance Contract"]["on_update"]),
		)
		self.assertIn(
			"erpnext_enhancements.project_enhancements.calendar_sync.triggers.on_project_change",
			_as_list(events["Project"]["on_update"]),
		)
		self.assertNotIn("sync_task_to_google_calendar", json.dumps(events))

	def test_scheduler(self):
		events = _literal("scheduler_events")
		self.assertIn(
			"erpnext_enhancements.project_enhancements.calendar_sync.engine.snapback",
			events["cron"]["*/10 * * * *"],
		)
		self.assertIn(
			"erpnext_enhancements.project_enhancements.calendar_sync.engine.nightly_repair",
			events["daily_long"],
		)

	def test_settings_doctype(self):
		meta = json.loads(
			(DOCTYPES / "calendar_sync_settings" / "calendar_sync_settings.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(meta["issingle"], 1)
		self.assertEqual(fields["enabled"]["default"], "0")
		self.assertEqual(fields["refresh_token"]["fieldtype"], "Password")
		self.assertEqual({p["role"] for p in meta["permissions"]}, {"System Manager"})
		self.assertEqual(fields["days_back"]["default"], "14")
		self.assertEqual(fields["days_ahead"]["default"], "180")

	def test_link_doctype_does_not_block_deletes(self):
		meta = json.loads(
			(DOCTYPES / "calendar_sync_link" / "calendar_sync_link.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(fields["source_name"]["fieldtype"], "Data")
		self.assertEqual(fields["source_ref"].get("unique"), 1)
		self.assertNotIn("Dynamic Link", {f["fieldtype"] for f in meta["fields"]})

	def test_defaults_patch_is_registered(self):
		self.assertIn(
			"erpnext_enhancements.patches.materialize_calendar_sync_settings",
			PATCHES_TXT.read_text(encoding="utf-8").split(),
		)
		self.assertTrue((APP / "patches" / "materialize_calendar_sync_settings.py").exists())

	def test_missing_defaults_never_writes_over_a_row(self):
		module = importlib.import_module(
			"erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings"
		)
		todo = module.missing_defaults(
			[("enabled", "0"), ("days_back", "14"), ("days_ahead", "180"), ("x", None)], {"enabled"}
		)
		self.assertEqual(todo, {"days_back": "14", "days_ahead": "180"})


if __name__ == "__main__":
	unittest.main()
