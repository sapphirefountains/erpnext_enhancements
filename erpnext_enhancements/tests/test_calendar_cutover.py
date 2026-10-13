# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar cutover (C5, v1.607.0): retire the hand-made Maintenance series without losing what they know.

What this pins, because each of these fails quietly or on the one shared calendar everybody reads:

* **ERPNext's own events are never read as hand-made**, of any site, and are never patched.
* **Series parsing**: the RRULE is kept as Google gave it, Google Sites links come out of the HTML
  (Google's ``/url?q=`` wrapper unwrapped), the instructions lose their markup and the links, and
  codes are found by C2's own rule.
* **Profile matching** is explained: matched by name, matched by street address, unmatched (with the
  nearest named), and ambiguous (nothing proposed, the close ones named).
* **A rescan never clobbers a reviewed row** (Approved, Applied, Ignored) or a profile picked by hand.
* **Apply appends, never overwrites**: links once each, instructions under a header after what is
  there, codes only when they fit; the site address only when the project has none, then geocoded.
* **Codes never reach an outbound event**: C2's readers and builder never name the profile's fields.
* **RRULE rewriting**: open, COUNT, an existing UNTIL (later and earlier), EXDATE kept, RDATE trimmed,
  all-day and timed (UTC one second before local midnight, both sides of a DST change).
* **The dry run writes nothing**; **confirm is required, with the dry run's token**; a calendar that
  changed since the dry run is refused; the master switch is required to write.
* **Undo restores the original rules**, only where the rule is still ours, and works with the switch off.
* **The role gate**: every endpoint is System Manager only and POST only.
* **The dialogs build** through a stand-in that asks every ``get_data`` / ``get_query`` during
  construction, as Frappe does (the v1.602.0 temporal-dead-zone bug).

Bench-free: installs its own ``frappe`` stub, a fake Google client (never the network), imports the
real modules, and puts ``sys.modules`` back afterwards. Its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_calendar_cutover
"""

import ast
import copy
import datetime
import importlib
import json
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
SYNC_DIR = APP / "project_enhancements" / "calendar_sync"
API_FILE = APP / "api" / "calendar_cutover.py"
DOCTYPES = APP / "project_enhancements" / "doctype"
LIST_JS = DOCTYPES / "calendar_cutover_item" / "calendar_cutover_item_list.js"
FORM_JS = DOCTYPES / "calendar_cutover_item" / "calendar_cutover_item.js"
SETTINGS_JS = DOCTYPES / "calendar_sync_settings" / "calendar_sync_settings.js"
PROFILE_JSON = (
	APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_profile" / "sapphire_maintenance_profile.json"
)
MAINT_CAL = "maint@group.calendar.google.com"
TODAY = datetime.date(2026, 10, 13)
NOW = datetime.datetime(2026, 10, 13, 9, 0, 0)
CUTOVER = "2026-11-02"

_saved_modules = {}
_modules_before = set()
frappe = None
payload = None
google_api = None
cutover = None
api = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _ValidationError(Exception):
	pass


class _Flags(dict):
	def __getattr__(self, name):
		return self.get(name)

	def __setattr__(self, name, value):
		self[name] = value


class _Doc(dict):
	"""Enough of a Document: dict and attribute access, get/set, insert/save into the stub tables."""

	def __init__(self, *args, doctype=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.__dict__["flags"] = _Flags()
		self.__dict__["doctype"] = doctype

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def get_password(self, fieldname="password", raise_exception=True):
		return self.get(fieldname) or None

	def insert(self):
		site = frappe.site
		site.counter += 1
		prefix = {"Calendar Cutover Item": "CCI-", "Calendar Cutover Log": "LOG-"}.get(self.doctype, "DOC-")
		self["name"] = f"{prefix}{site.counter:05d}"
		self["creation"] = site.counter
		return self.save()

	def save(self):
		site = frappe.site
		table = site.tables.setdefault(self.doctype, {})
		if self.doctype == "Calendar Cutover Item":
			for other in table.values():
				if other.get("series_id") == self.get("series_id") and other["name"] != self["name"]:
					raise type("UniqueValidationError", (Exception,), {})()
		table[self["name"]] = dict(self)
		site.writes.append((self.doctype, self["name"]))
		return self

	def add_comment(self, comment_type, text):
		frappe.site.comments.append((self.doctype, self.get("name"), text))


class _Site:
	def __init__(self):
		self.tables = {}
		self.writes = []
		self.set_values = []
		self.enqueued = []
		self.logs = []
		self.comments = []
		self.counter = 0
		self.roles = ["System Manager"]
		self.settings = _Doc(
			{
				"enabled": 1,
				"refresh_token": "refresh-SECRET",
				"projects_calendar_id": "projects@group.calendar.google.com",
				"maintenance_calendar_id": MAINT_CAL,
				"holidays_calendar_id": "holidays@group.calendar.google.com",
				"days_back": 14,
				"days_ahead": 180,
				"colors": [],
			},
			doctype="Calendar Sync Settings",
		)


def _matches(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, list | tuple):
			op, arg = cond[0], cond[1]
			if op == "in" and value not in arg:
				return False
			if op == "!=" and value == arg:
				return False
		elif (value or 0) != cond if isinstance(cond, int) else value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
	rows = [dict(r) for r in frappe.site.tables.get(doctype, {}).values() if _matches(r, filters)]
	if order_by:
		field, _, direction = order_by.partition(" ")
		rows.sort(key=lambda r: (r.get(field) is None, r.get(field) or 0 if field == "creation" else str(r.get(field) or "")))
		if direction.strip().lower() == "desc":
			rows.reverse()
	if pluck:
		return [r.get(pluck) for r in rows]
	if fields:
		return [_Doc({f: r.get(f) for f in fields}) for r in rows]
	return [_Doc(r) for r in rows]


class _DB:
	def get_value(self, doctype, name, field=None, *args, as_dict=False, **kwargs):
		row = frappe.site.tables.get(doctype, {}).get(name)
		if row is None:
			return None
		if isinstance(field, list | tuple):
			return _Doc({f: row.get(f) for f in field}) if as_dict else tuple(row.get(f) for f in field)
		return row.get(field)

	def set_value(self, doctype, name, field, value=None, *args, **kwargs):
		frappe.site.set_values.append((doctype, name, field, value))
		row = frappe.site.tables.setdefault(doctype, {}).setdefault(name, {"name": name})
		if isinstance(field, dict):
			row.update(field)
		else:
			row[field] = value

	def exists(self, doctype, name=None):
		return name in frappe.site.tables.get(doctype, {})

	def has_column(self, doctype, column):
		return True

	def table_exists(self, doctype):
		return True

	def sql(self, *args, **kwargs):
		raise AssertionError("the cutover runs no raw SQL")

	def commit(self):
		pass


class _Cache:
	def get_value(self, key):
		return None

	def set_value(self, *args, **kwargs):
		pass

	def delete_value(self, *args, **kwargs):
		pass


def _get_doc(doctype, name=None):
	if doctype == "Calendar Sync Settings":
		return frappe.site.settings
	row = frappe.site.tables.get(doctype, {}).get(name)
	if row is None:
		raise KeyError(f"{doctype} {name}")
	return _Doc(copy.deepcopy(row), doctype=doctype)


def _get_cached_doc(doctype, name=None):
	if doctype == "Calendar Sync Settings":
		return frappe.site.settings
	if doctype == "Google Settings":
		return _Doc({"enable": 1, "client_id": "client-123", "client_secret": "client-SECRET"})
	raise KeyError(doctype)


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _install_frappe():
	module = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = lambda value=None: (
		TODAY
		if value is None
		else value
		if isinstance(value, datetime.date)
		else datetime.date.fromisoformat(str(value)[:10])
	)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.cint = lambda value: int(float(value or 0))
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.get_request_site_address = lambda full_address=False: "https://erp.example.com"
	utils.date_diff = lambda a, b: (utils.getdate(a) - utils.getdate(b)).days
	utils.add_days = lambda value, days: utils.getdate(value) + datetime.timedelta(days=days)
	module.utils = utils
	module._ = lambda text, *a, **k: text
	module.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	module.throw = _throw
	module.PermissionError = _PermissionError
	module.ValidationError = _ValidationError
	module.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
	module.get_traceback = lambda: "Traceback: boom"
	module.log_error = lambda title=None, message=None, **k: frappe.site.logs.append((title, message))
	module.enqueue = lambda method, **kwargs: frappe.site.enqueued.append((method, kwargs))
	module.get_all = _get_all
	module.get_doc = _get_doc
	module.get_cached_doc = _get_cached_doc
	module.new_doc = lambda doctype: _Doc(doctype=doctype)
	module.clear_document_cache = lambda *a, **k: None
	module.get_roles = lambda user=None: list(frappe.site.roles)
	module.session = _Flags(user="nik@example.com")
	module.local = types.SimpleNamespace(site="erp.test")
	module.flags = _Flags()
	module.db = _DB()
	module.cache = _Cache()
	module.site = _Site()
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Doc
	sys.modules.update(
		{"frappe": module, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)
	return module


def _bare_package(name, path):
	pkg = types.ModuleType(name)
	pkg.__path__ = [str(path)]
	sys.modules[name] = pkg


def setUpModule():
	global frappe, payload, google_api, cutover, api
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = _install_frappe()
	sys.modules["requests"] = types.ModuleType("requests")  # any real HTTP call fails loudly
	importlib.import_module("erpnext_enhancements")
	_bare_package("erpnext_enhancements.project_enhancements", APP / "project_enhancements")
	_bare_package("erpnext_enhancements.api", APP / "api")
	payload = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.payload")
	google_api = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.google_api")
	cutover = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.cutover")
	api = importlib.import_module("erpnext_enhancements.api.calendar_cutover")
	# The two lookups that reach other modules: stand-ins with the same contract.
	cutover._short_site = lambda title: title or ""
	cutover._address_text = lambda row: (row.get("custom_project_address") or row.get("lead_address") or "").strip()


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- the fixtures

RIVERWALK_HTML = (
	'Site page: <a href="https://www.google.com/url?q=https://sites.google.com/sapphirefountains.com/riverwalk'
	'&amp;sa=D&amp;source=calendar">https://sites.google.com/sapphirefountains.com/riverwalk</a><br>'
	"Gate code 4412<br><b>Special:</b> Shut the pump at the panel by the stairs.<br>"
	'Call Joe before arriving &amp; park on 12th. <span title="a > b">Bring waders.</span>'
)


def _events():
	return [
		{
			"id": "ser1",
			"status": "confirmed",
			"summary": "Riverwalk - monthly fountain cleaning",
			"description": RIVERWALK_HTML,
			"location": "1200 N Lincoln St, Denver, CO, Gate code 4412",
			"start": {"date": "2025-01-15"},
			"end": {"date": "2025-01-16"},
			"recurrence": ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15"],
			"organizer": {"email": MAINT_CAL, "self": True},
			"htmlLink": "https://www.google.com/calendar/event?eid=abc",
		},
		{
			# ERPNext's own (another site's, even), and recurring: still never hand-made.
			"id": "erp0123456789abcdef",
			"status": "confirmed",
			"summary": "Riverwalk · Maintenance visit",
			"start": {"date": "2026-10-20"},
			"recurrence": ["RRULE:FREQ=WEEKLY"],
			"extendedProperties": {
				"private": {"erpnext_site": "staging.test", "erpnext_doctype": "Sapphire Maintenance Record"}
			},
		},
		{
			"id": "single1",
			"status": "confirmed",
			"summary": "Riverwalk one-off pump swap",
			"start": {"date": "2026-11-20"},
		},
		{
			"id": "ser1_20261115",
			"status": "confirmed",
			"recurringEventId": "ser1",
			"summary": "Riverwalk - monthly fountain cleaning",
			"originalStartTime": {"date": "2026-11-15"},
			"start": {"date": "2026-11-16"},
		},
		{
			"id": "ser2",
			"status": "confirmed",
			"summary": "Cherry Creek weekly",
			"description": "Check the skimmers.\nLockbox on the north wall, combo 1234",
			"location": "3000 East 1st Avenue, Denver",
			"start": {"dateTime": "2025-03-03T08:00:00-07:00", "timeZone": "America/Denver"},
			"end": {"dateTime": "2025-03-03T10:00:00-07:00", "timeZone": "America/Denver"},
			"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO", "EXDATE;TZID=America/Denver:20251124T080000"],
			"organizer": {"email": MAINT_CAL, "self": True},
		},
		{
			"id": "ser_old",
			"status": "confirmed",
			"summary": "Riverwalk - old schedule",
			"start": {"date": "2023-01-15"},
			"recurrence": ["RRULE:FREQ=MONTHLY;UNTIL=20240101"],
		},
		{
			"id": "ser_staff",
			"status": "confirmed",
			"summary": "Staff meeting",
			"start": {"date": "2025-01-06"},
			"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
		},
		{
			"id": "ser_belmar",
			"status": "confirmed",
			"summary": "Belmar Plaza",
			"start": {"date": "2025-02-01"},
			"recurrence": ["RRULE:FREQ=MONTHLY;BYMONTHDAY=1"],
		},
		{"id": "gone1", "status": "cancelled", "summary": "deleted"},
	]


def _seed_profiles():
	tables = frappe.site.tables
	tables["Sapphire Maintenance Profile"] = {
		"PROF-RW": {
			"name": "PROF-RW",
			"project": "PRJ-RW",
			"customer": "Riverwalk Partners",
			"access_codes": "Key: office",
			"special_instructions": "Bring the long hose.",
			"google_sites_links": "https://sites.google.com/sapphirefountains.com/old",
		},
		"PROF-CC": {"name": "PROF-CC", "project": "PRJ-CC", "customer": "Cherry Creek LLC"},
		"PROF-BE": {"name": "PROF-BE", "project": "PRJ-BE", "customer": "Belmar East"},
		"PROF-BW": {"name": "PROF-BW", "project": "PRJ-BW", "customer": "Belmar West"},
	}
	tables["Project"] = {
		"PRJ-RW": {"name": "PRJ-RW", "project_name": "Riverwalk Fountain Maintenance", "custom_project_address": ""},
		"PRJ-CC": {
			"name": "PRJ-CC",
			"project_name": "Cherry Creek Shopping Center",
			"custom_project_address": "3000 E 1st Ave, Denver, CO 80206",
		},
		"PRJ-BE": {"name": "PRJ-BE", "project_name": "Belmar Plaza East", "custom_project_address": "1 Belmar"},
		"PRJ-BW": {"name": "PRJ-BW", "project_name": "Belmar Plaza West", "custom_project_address": "2 Belmar"},
	}


class FakeGoogle:
	"""The Calendar v3 calls the cutover makes, against an in-memory calendar. Never the network."""

	def __init__(self, events=None, tz="America/Denver"):
		self.events = {e["id"]: copy.deepcopy(e) for e in (events if events is not None else _events())}
		self.tz = tz
		self.calls = []
		self.instances = {}
		self.fail_patch = set()

	def request(self, method, path, params=None, body=None):
		self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(body)))
		parts = path.split("/")
		if unquote(parts[2]) != MAINT_CAL:
			raise AssertionError(f"a call to another calendar: {path}")
		if method == "GET" and len(parts) == 4:
			return {"items": [copy.deepcopy(e) for e in self.events.values()], "timeZone": self.tz}
		event_id = unquote(parts[4])
		if method == "GET" and len(parts) == 6 and parts[5] == "instances":
			return {"items": copy.deepcopy(self.instances.get(event_id, []))}
		if method == "GET" and len(parts) == 5:
			return copy.deepcopy(self.events[event_id])
		if method == "PATCH" and len(parts) == 5:
			if event_id in self.fail_patch:
				raise google_api.GoogleCalendarError(500, "backendError", "try later")
			self.events[event_id].update(copy.deepcopy(body))
			return copy.deepcopy(self.events[event_id])
		raise AssertionError(f"an unexpected Google call: {method} {path}")

	@property
	def writes(self):
		return [c for c in self.calls if c[0] != "GET"]


def _reset():
	frappe.site = _Site()
	frappe.session = _Flags(user="nik@example.com")
	_seed_profiles()


def _items():
	return {row["series_id"]: row for row in frappe.site.tables.get("Calendar Cutover Item", {}).values()}


def _set(series_id, **values):
	for row in frappe.site.tables["Calendar Cutover Item"].values():
		if row["series_id"] == series_id:
			row.update(values)
			return row
	raise KeyError(series_id)


def _code_tokens(path, names=False):
	"""Every string literal (and with ``names`` every identifier) in a file, docstrings excluded: an
	absence check must not be satisfied or broken by the prose that explains it."""
	tree = ast.parse(Path(path).read_text(encoding="utf-8"))
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
			body = getattr(node, "body", [])
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				docstrings.add(id(body[0].value))
	out = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			out.add(node.value)
		elif names and isinstance(node, ast.Name):
			out.add(node.id)
		elif names and isinstance(node, ast.Attribute):
			out.add(node.attr)
	return out


# ---------------------------------------------------------------------- reading events


class TestReadingEvents(unittest.TestCase):
	def test_erpnext_events_are_never_hand_made(self):
		events = {e["id"]: e for e in _events()}
		self.assertEqual(cutover.classify(events["erp0123456789abcdef"]), cutover.KIND_OURS)
		self.assertTrue(cutover.is_erpnext_event(events["erp0123456789abcdef"]))
		self.assertEqual(cutover.classify(events["ser1"]), cutover.KIND_SERIES)
		self.assertEqual(cutover.classify(events["single1"]), cutover.KIND_SINGLE)
		self.assertEqual(cutover.classify(events["ser1_20261115"]), cutover.KIND_EXCEPTION)
		self.assertEqual(cutover.classify(events["gone1"]), cutover.KIND_CANCELLED)

	def test_series_parsing_keeps_the_rule_and_finds_links_instructions_and_codes(self):
		series = cutover.parse_series(_events()[0], TODAY, "America/Denver")
		self.assertEqual(series["rrule"], "RRULE:FREQ=MONTHLY;BYMONTHDAY=15")
		self.assertEqual(series["recurrence"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15"])
		self.assertEqual(series["links"], ["https://sites.google.com/sapphirefountains.com/riverwalk"])
		text = series["instructions"]
		self.assertIn("Shut the pump at the panel by the stairs.", text)
		self.assertIn("Call Joe before arriving & park on 12th.", text)
		self.assertIn("Bring waders.", text)
		self.assertNotIn("<", text)
		self.assertNotIn("sites.google.com", text)
		self.assertNotIn("Site page", text, "a label left dangling by its link is dropped")
		self.assertEqual(series["codes"], "Gate code 4412")
		self.assertEqual(series["state"], cutover.RUNNING)
		self.assertTrue(series["all_day"])

	def test_plain_text_description_and_a_link_without_a_tag(self):
		text, links = cutover.html_to_text("Line one\nSee https://sites.google.com/view/cc-site.\nLockbox: 7781")
		self.assertEqual(links, [])
		self.assertEqual(cutover.sites_links(text, links), ["https://sites.google.com/view/cc-site"])
		self.assertEqual(cutover.find_codes(text), "Lockbox: 7781")

	def test_already_ended_series(self):
		old = next(e for e in _events() if e["id"] == "ser_old")
		self.assertEqual(cutover.parse_series(old, TODAY)["state"], cutover.ALREADY_ENDED)


# ---------------------------------------------------------------------- matching


class TestMatching(unittest.TestCase):
	def setUp(self):
		_reset()
		self.candidates = cutover.profile_candidates()

	def test_matched_by_name(self):
		match = cutover.match_profile("Riverwalk - monthly fountain cleaning", "", self.candidates)
		self.assertEqual(match["quality"], cutover.MATCHED)
		self.assertEqual(match["profile"], "PROF-RW")
		self.assertEqual(match["project"], "PRJ-RW")
		self.assertIn("Riverwalk Fountain Maintenance", match["note"])

	def test_matched_by_name_and_street_address(self):
		match = cutover.match_profile("Cherry Creek weekly", "3000 East 1st Avenue, Denver", self.candidates)
		self.assertEqual(match["quality"], cutover.MATCHED)
		self.assertEqual(match["profile"], "PROF-CC")
		self.assertIn("same street address", match["note"])
		self.assertEqual(cutover.street_key("3000 E 1st Ave, Denver"), ("3000", "1st"))

	def test_unmatched_names_the_nearest(self):
		match = cutover.match_profile("Staff meeting", "", self.candidates)
		self.assertEqual(match["quality"], cutover.UNMATCHED)
		self.assertIsNone(match["profile"])
		near = cutover.match_profile("Riverside plaza", "", self.candidates)
		self.assertEqual(near["quality"], cutover.UNMATCHED)
		self.assertIn("Nearest", near["note"])

	def test_ambiguous_proposes_nothing_and_names_both(self):
		match = cutover.match_profile("Belmar Plaza", "", self.candidates)
		self.assertEqual(match["quality"], cutover.AMBIGUOUS)
		self.assertIsNone(match["profile"])
		self.assertIn("Belmar Plaza East", match["note"])
		self.assertIn("Belmar Plaza West", match["note"])


# ---------------------------------------------------------------------- scan


class TestScan(unittest.TestCase):
	def setUp(self):
		_reset()
		self.google = FakeGoogle()

	def test_scan_reads_only_and_makes_one_row_per_running_hand_made_series(self):
		out = cutover.scan(client=self.google, today=TODAY)
		self.assertEqual(self.google.writes, [], "the scan never writes to Google")
		listed = self.google.calls[0]
		self.assertEqual(listed[2]["singleEvents"], "false", "series masters, not occurrences")
		items = _items()
		self.assertEqual(set(items), {"ser1", "ser2", "ser_staff", "ser_belmar"})
		self.assertNotIn("erp0123456789abcdef", items)
		self.assertNotIn("single1", items)
		self.assertNotIn("ser_old", items, "an already-ended series is not listed by default")
		counts = out["counts"]
		self.assertEqual((counts["erpnext"], counts["single"], counts["exception"]), (1, 1, 1))
		self.assertEqual(counts["already_ended"], 1)
		self.assertEqual(counts["created"], 4)
		rw = items["ser1"]
		self.assertEqual(rw["status"], cutover.PENDING)
		self.assertEqual(rw["profile"], "PROF-RW")
		self.assertEqual(rw["moved_occurrences"], 1)
		self.assertEqual(rw["recurrence"], "RRULE:FREQ=MONTHLY;BYMONTHDAY=15")
		self.assertEqual(rw["proposed_links"], "https://sites.google.com/sapphirefountains.com/riverwalk")
		self.assertEqual(rw["proposed_access_codes"], "Gate code 4412")
		self.assertEqual(rw["end_at_cutover"], 1)
		# The project has no address: the location is proposed, with the gate code scrubbed out.
		self.assertEqual(rw["proposed_address"], "1200 N Lincoln St, Denver, CO")
		# Cherry Creek has an address: nothing proposed.
		self.assertEqual(items["ser2"]["proposed_address"], "")
		self.assertEqual(items["ser_belmar"]["match_quality"], cutover.AMBIGUOUS)
		self.assertIn("Staff meeting", out["unmatched"])

	def test_include_ended_lists_them_unticked(self):
		cutover.scan(include_ended=True, client=self.google, today=TODAY)
		old = _items()["ser_old"]
		self.assertEqual(old["series_state"], cutover.ALREADY_ENDED)
		self.assertEqual(old["end_at_cutover"], 0)

	def test_rescan_never_clobbers_reviewed_rows(self):
		cutover.scan(client=self.google, today=TODAY)
		cutover.set_status([_items()["ser1"]["name"]], cutover.APPROVED)
		_set("ser1", proposed_instructions="Edited by Lisa.", proposed_access_codes="")
		cutover.set_status([_items()["ser_staff"]["name"]], cutover.IGNORED)
		_set("ser_belmar", profile="PROF-BW", match_quality=cutover.BY_HAND)
		before = copy.deepcopy(_items())
		for event in self.google.events.values():
			if event.get("description"):
				event["description"] = "Completely new text. Alarm code 9999"
		self.google.events["ser_staff"]["description"] = "New"
		out = cutover.scan(client=self.google, today=TODAY)
		after = _items()
		for field in (*cutover.PROPOSAL_FIELDS, "status", "profile", "reviewed_by", "reviewed_on"):
			self.assertEqual(after["ser1"].get(field), before["ser1"].get(field), field)
			self.assertEqual(after["ser_staff"].get(field), before["ser_staff"].get(field), field)
		self.assertEqual(after["ser1"]["proposed_instructions"], "Edited by Lisa.")
		# A Pending row IS refreshed...
		self.assertIn("Completely new text", after["ser2"]["proposed_instructions"])
		# ...but a profile picked by hand survives it.
		self.assertEqual(after["ser_belmar"]["profile"], "PROF-BW")
		self.assertEqual(after["ser_belmar"]["match_quality"], cutover.BY_HAND)
		self.assertEqual(out["counts"]["kept"], 2)
		self.assertEqual(len(after), 4, "a rescan never makes a second row for a series")

	def test_a_series_deleted_in_google_is_flagged_not_removed(self):
		cutover.scan(client=self.google, today=TODAY)
		del self.google.events["ser_staff"]
		out = cutover.scan(client=self.google, today=TODAY)
		self.assertEqual(out["counts"]["gone"], 1)
		self.assertEqual(_items()["ser_staff"]["gone_from_calendar"], 1)

	def test_scan_needs_an_authorized_account_and_a_calendar(self):
		frappe.site.settings["refresh_token"] = ""
		with self.assertRaises(_Throw):
			cutover.scan(client=self.google, today=TODAY)
		frappe.site.settings["refresh_token"] = "refresh-SECRET"
		frappe.site.settings["maintenance_calendar_id"] = ""
		with self.assertRaises(_Throw):
			cutover.scan(client=self.google, today=TODAY)
		self.assertEqual(self.google.calls, [])

	def test_scan_does_not_need_the_switch(self):
		frappe.site.settings["enabled"] = 0
		cutover.scan(client=self.google, today=TODAY)
		self.assertEqual(len(_items()), 4)


# ---------------------------------------------------------------------- apply


class TestApply(unittest.TestCase):
	def setUp(self):
		_reset()
		self.google = FakeGoogle()
		cutover.scan(client=self.google, today=TODAY)
		self.rw = _items()["ser1"]["name"]
		self.cc = _items()["ser2"]["name"]
		cutover.set_status([self.rw, self.cc], cutover.APPROVED)

	def _profile(self, name):
		return frappe.site.tables["Sapphire Maintenance Profile"][name]

	def test_the_dry_run_writes_nothing(self):
		before = copy.deepcopy(frappe.site.tables)
		out = cutover.apply_approved(confirm=False, today=TODAY)
		self.assertTrue(out["dry_run"])
		self.assertEqual(frappe.site.tables, before)
		self.assertEqual(frappe.site.enqueued, [])
		notes = {r["item"]: r["notes"] for r in out["rows"]}
		self.assertIn("special instructions appended", notes[self.rw])

	def test_apply_appends_and_never_overwrites(self):
		out = cutover.apply_approved(confirm=True, today=TODAY)
		self.assertEqual(out["applied"], 2)
		profile = self._profile("PROF-RW")
		self.assertEqual(
			profile["google_sites_links"],
			"https://sites.google.com/sapphirefountains.com/old\nhttps://sites.google.com/sapphirefountains.com/riverwalk",
		)
		self.assertTrue(profile["special_instructions"].startswith("Bring the long hose.\n\n"))
		self.assertIn("From the old Maintenance calendar (“Riverwalk - monthly fountain cleaning”", profile["special_instructions"])
		self.assertIn("Shut the pump at the panel by the stairs.", profile["special_instructions"])
		self.assertEqual(profile["access_codes"], "Key: office | Gate code 4412")
		item = frappe.site.tables["Calendar Cutover Item"][self.rw]
		self.assertEqual(item["status"], cutover.APPLIED)
		self.assertEqual(item["applied_by"], "nik@example.com")
		# Nothing outside the three profile fields was written.
		for field in ("project", "customer"):
			self.assertEqual(profile[field], {"PROF-RW": {"project": "PRJ-RW", "customer": "Riverwalk Partners"}}["PROF-RW"][field])
		# A comment says where it came from, and never carries a code.
		self.assertTrue(frappe.site.comments)
		self.assertFalse(any("4412" in text for _d, _n, text in frappe.site.comments))

	def test_apply_twice_adds_nothing_twice(self):
		cutover.apply_approved(confirm=True, today=TODAY)
		first = copy.deepcopy(self._profile("PROF-RW"))
		# The same text approved again on another row (e.g. a split series) is not appended twice.
		cutover.scan(client=self.google, today=TODAY)
		twin = _Doc(copy.deepcopy(frappe.site.tables["Calendar Cutover Item"][self.rw]), doctype="Calendar Cutover Item")
		twin["series_id"], twin["status"] = "ser1_split", cutover.APPROVED
		del twin["name"]
		twin.insert()
		out = cutover.apply_approved(confirm=True, today=TODAY)
		self.assertEqual(self._profile("PROF-RW"), first)
		notes = out["rows"][0]["notes"]
		self.assertIn("special instructions already there", notes)
		self.assertIn("access codes already there", notes)

	def test_address_filled_only_when_empty_then_geocoded(self):
		frappe.site.tables["Calendar Cutover Item"][self.cc]["proposed_address"] = "3000 East 1st Avenue"
		cutover.apply_approved(confirm=True, today=TODAY)
		projects = frappe.site.tables["Project"]
		self.assertEqual(projects["PRJ-RW"]["custom_project_address"], "1200 N Lincoln St, Denver, CO")
		self.assertEqual(projects["PRJ-CC"]["custom_project_address"], "3000 E 1st Ave, Denver, CO 80206")
		self.assertEqual(
			frappe.site.enqueued,
			[(cutover.GEOCODE_JOB, {"queue": "short", "project": "PRJ-RW", "force": True, "enqueue_after_commit": True})],
		)

	def test_an_address_typed_since_the_scan_is_kept(self):
		frappe.site.tables["Project"]["PRJ-RW"]["custom_project_address"] = "1201 Lincoln"
		out = cutover.apply_approved(confirm=True, today=TODAY)
		self.assertEqual(frappe.site.tables["Project"]["PRJ-RW"]["custom_project_address"], "1201 Lincoln")
		self.assertIn("already has an address", out["rows"][0]["address"] + out["rows"][1]["address"])
		self.assertEqual(frappe.site.enqueued, [])

	def test_codes_too_long_for_access_codes_stay_in_the_instructions(self):
		self._profile("PROF-RW")["access_codes"] = "x" * 130
		cutover.apply_approved(confirm=True, today=TODAY)
		profile = self._profile("PROF-RW")
		self.assertEqual(profile["access_codes"], "x" * 130)
		self.assertIn("Gate code 4412", profile["special_instructions"])

	def test_only_approved_rows_are_applied(self):
		cutover.set_status([self.cc], cutover.PENDING)
		staff = _items()["ser_staff"]["name"]
		cutover.set_status([staff], cutover.IGNORED)
		out = cutover.apply_approved(confirm=True, today=TODAY)
		self.assertEqual([r["item"] for r in out["rows"]], [self.rw])

	def test_approving_needs_a_profile_and_applied_stays_applied(self):
		belmar = _items()["ser_belmar"]["name"]
		out = cutover.set_status([belmar], cutover.APPROVED)
		self.assertEqual(out["done"], [])
		self.assertIn("pick a Maintenance Profile", out["skipped"][0][1])
		cutover.apply_approved(confirm=True, today=TODAY)
		out = cutover.set_status([self.rw], cutover.PENDING)
		self.assertEqual(out["done"], [])
		self.assertEqual(frappe.site.tables["Calendar Cutover Item"][self.rw]["status"], cutover.APPLIED)


# ---------------------------------------------------------------------- secrets


class TestCodesNeverReachEvents(unittest.TestCase):
	FIELDS = {"special_instructions", "google_sites_links", "access_codes"}

	def test_c2_never_reads_the_profile_fields(self):
		for name in ("sources.py", "payload.py", "engine.py", "triggers.py"):
			tokens = _code_tokens(SYNC_DIR / name, names=True)
			self.assertFalse(self.FIELDS & tokens, f"{name} names {self.FIELDS & tokens}")
			self.assertNotIn("Sapphire Maintenance Profile", tokens, name)
		# The planner code projected visits come from reads the profile's crew, never these fields.
		for path in (APP / "api" / "maintenance_planner.py", APP / "api" / "maintenance_dispatch.py"):
			self.assertFalse(self.FIELDS & _code_tokens(path, names=True), path.name)

	def test_the_event_builder_ignores_profile_text_and_scrubs_the_location(self):
		item = {
			"doctype": "Sapphire Maintenance Record",
			"name": "SMR-1",
			"kind": payload.KIND_VISIT,
			"calendar": "maintenance",
			"state": payload.STATE_ACTIVE,
			"start": datetime.date(2026, 11, 3),
			"end": datetime.date(2026, 11, 3),
			"site": "Riverwalk",
			"label": "Monthly clean",
			"location": "1200 N Lincoln St, Gate code 4412, Denver",
			"special_instructions": "Alarm code 5150. Lockbox 7781.",
			"google_sites_links": "https://sites.google.com/x",
			"access_codes": "Gate 4412",
			"streams": ["Service"],
		}
		body = json.dumps(payload.build_event(item, "erp.test"))
		for secret in ("4412", "5150", "7781", "sites.google.com"):
			self.assertNotIn(secret, body)
		self.assertIn("1200 N Lincoln St, Denver", body)

	def test_profile_fields_exist_where_apply_writes(self):
		data = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
		names = [f["fieldname"] for f in data["fields"]]
		for field in ("google_sites_links", "special_instructions", "access_codes"):
			self.assertIn(field, names)
			self.assertIn(field, data["field_order"])
		self.assertEqual(set(cutover.PROFILE_FIELDS), {"google_sites_links", "special_instructions", "access_codes"})


# ---------------------------------------------------------------------- RRULE


class TestRRule(unittest.TestCase):
	DENVER = None

	@classmethod
	def setUpClass(cls):
		cls.DENVER = cutover.zone("America/Denver")
		if cls.DENVER is None:
			raise unittest.SkipTest("no tz database for America/Denver")

	def rewrite(self, recurrence, cutover_day="2026-11-01", all_day=True, runs_past=None):
		return cutover.rewrite_recurrence(
			recurrence, datetime.date.fromisoformat(cutover_day), all_day, self.DENVER, runs_past
		)

	def test_open_rule_gets_until_with_its_other_parts_kept(self):
		out = self.rewrite(["RRULE:FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=15"])
		self.assertTrue(out["change"])
		self.assertEqual(out["after"], ["RRULE:FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=15;UNTIL=20261031"])

	def test_timed_until_is_utc_one_second_before_local_midnight_both_sides_of_dst(self):
		mdt = self.rewrite(["RRULE:FREQ=WEEKLY;BYDAY=MO"], "2026-10-20", all_day=False)
		self.assertEqual(mdt["after"], ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261020T055959Z"])
		mst = self.rewrite(["RRULE:FREQ=WEEKLY;BYDAY=MO"], "2026-11-02", all_day=False)
		self.assertEqual(mst["after"], ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261102T065959Z"])

	def test_timed_without_a_time_zone_is_refused(self):
		out = cutover.rewrite_recurrence(["RRULE:FREQ=WEEKLY"], datetime.date(2026, 11, 2), False, None)
		self.assertFalse(out["change"])
		self.assertTrue(out["error"])

	def test_existing_until_later_is_moved_earlier_is_left(self):
		later = self.rewrite(["RRULE:FREQ=WEEKLY;UNTIL=20271231;BYDAY=TU"])
		self.assertEqual(later["after"], ["RRULE:FREQ=WEEKLY;UNTIL=20261031;BYDAY=TU"])
		self.assertIn("UNTIL moved from 2027-12-31", later["note"])
		timed = self.rewrite(["RRULE:FREQ=WEEKLY;UNTIL=20271231T150000Z"], "2026-11-02", all_day=False)
		self.assertEqual(timed["after"], ["RRULE:FREQ=WEEKLY;UNTIL=20261102T065959Z"])
		earlier = self.rewrite(["RRULE:FREQ=WEEKLY;UNTIL=20260901"])
		self.assertFalse(earlier["change"])
		self.assertFalse(earlier["error"])
		self.assertIn("Already ends on 2026-09-01", earlier["note"])
		day_before = self.rewrite(["RRULE:FREQ=WEEKLY;UNTIL=20261031"])
		self.assertFalse(day_before["change"])

	def test_count_is_swapped_for_until_only_when_google_says_it_runs_past(self):
		rule = ["RRULE:FREQ=WEEKLY;COUNT=200;BYDAY=WE"]
		past = self.rewrite(rule, runs_past=True)
		self.assertEqual(past["after"], ["RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261031"])
		self.assertNotIn("COUNT", past["after"][0], "RFC 5545: never COUNT and UNTIL together")
		done = self.rewrite(rule, runs_past=False)
		self.assertFalse(done["change"])
		self.assertIn("already runs out", done["note"])
		unknown = self.rewrite(rule, runs_past=None)
		self.assertFalse(unknown["change"])
		self.assertTrue(unknown["error"])

	def test_exdate_is_kept_and_rdate_after_the_cutover_dropped(self):
		recurrence = [
			"EXDATE;VALUE=DATE:20251124,20261225",
			"RRULE:FREQ=WEEKLY;BYDAY=MO",
			"RDATE;VALUE=DATE:20260704,20261201",
		]
		out = self.rewrite(recurrence)
		self.assertEqual(
			out["after"],
			[
				"EXDATE;VALUE=DATE:20251124,20261225",
				"RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261031",
				"RDATE;VALUE=DATE:20260704",
			],
		)
		self.assertIn("RDATE", out["note"])

	def test_several_rules_are_refused(self):
		out = self.rewrite(["RRULE:FREQ=WEEKLY", "RRULE:FREQ=MONTHLY"])
		self.assertFalse(out["change"])
		self.assertIn("several", out["error"])


# ---------------------------------------------------------------------- end and undo


class _EndBase(unittest.TestCase):
	def setUp(self):
		_reset()
		self.google = FakeGoogle()
		cutover.scan(client=self.google, today=TODAY)
		items = _items()
		cutover.set_status([items["ser1"]["name"]], cutover.APPROVED)
		cutover.set_status([items["ser2"]["name"]], cutover.IGNORED)  # Ignore copies nothing; it still ends
		cutover.set_status([items["ser_staff"]["name"]], cutover.IGNORED)
		_set("ser_staff", end_at_cutover=0)  # a series somebody wants kept
		self.google.calls.clear()

	def end(self, **kwargs):
		kwargs.setdefault("today", TODAY)
		kwargs.setdefault("client", self.google)
		return cutover.end_series(CUTOVER, **kwargs)


class TestEndSeries(_EndBase):
	def test_the_dry_run_writes_nothing_and_says_exactly_what_would_change(self):
		tables = copy.deepcopy(frappe.site.tables)
		plan = self.end()
		self.assertTrue(plan["dry_run"])
		self.assertEqual(self.google.writes, [])
		self.assertEqual(frappe.site.tables, tables)
		changes = {c["series_id"]: c for c in plan["changes"]}
		self.assertEqual(set(changes), {"ser1", "ser2"})
		self.assertEqual(changes["ser1"]["after"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20261101"])
		self.assertEqual(
			changes["ser2"]["after"],
			["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261102T065959Z", "EXDATE;TZID=America/Denver:20251124T080000"],
		)
		self.assertEqual(changes["ser1"]["moved_after"], 1, "the occurrence dragged in Google is reported")
		skipped = {s["title"]: s["reason"] for s in plan["skipped"]}
		self.assertIn("Pending", skipped["Belmar Plaza"])
		self.assertIn("unticked", skipped["Staff meeting"])
		self.assertTrue(plan["token"])

	def test_confirm_is_required_with_the_dry_runs_token(self):
		with self.assertRaises(_Throw):
			self.end(confirm=True)
		with self.assertRaises(_Throw):
			self.end(confirm=True, token="not-the-token")
		self.assertEqual(self.google.writes, [])
		self.assertNotIn("Calendar Cutover Log", frappe.site.tables)

	def test_confirmed_end_patches_only_the_reviewed_series_masters(self):
		token = self.end()["token"]
		out = self.end(confirm=True, token=token)
		self.assertEqual(len(out["done"]), 2)
		patched = sorted(unquote(path.rsplit("/", 1)[1]) for method, path, _p, _b in self.google.writes)
		self.assertEqual(patched, ["ser1", "ser2"])
		for method, _path, params, body in self.google.writes:
			self.assertEqual(method, "PATCH", "never a delete, never an update of the whole event")
			self.assertEqual(params, {"sendUpdates": "none"})
			self.assertEqual(list(body), ["recurrence"])
		self.assertEqual(self.google.events["ser1"]["recurrence"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20261101"])
		# ERPNext's event, the single event and the moved occurrence are untouched.
		for untouched in ("erp0123456789abcdef", "single1", "ser1_20261115", "ser_staff", "ser_belmar"):
			self.assertEqual(self.google.events[untouched], next(e for e in _events() if e["id"] == untouched))
		logs = list(frappe.site.tables["Calendar Cutover Log"].values())
		self.assertEqual({(log["action"], log["result"]) for log in logs}, {("End", "Done")})
		ser1 = next(log for log in logs if log["series_id"] == "ser1")
		self.assertEqual(json.loads(ser1["before_recurrence"]), ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15"])
		self.assertEqual(_items()["ser1"]["series_state"], cutover.ENDED)
		self.assertEqual(_items()["ser1"]["ended_for"], CUTOVER)
		# Ending again is a no-op: those rows are Ended now.
		again = self.end()
		self.assertEqual(again["changes"], [])

	def test_an_erpnext_event_is_never_patched_even_if_a_row_names_it(self):
		row = _Doc({"series_id": "erp0123456789abcdef", "title": "planted", "status": cutover.APPROVED, "end_at_cutover": 1}, doctype="Calendar Cutover Item")
		row.insert()
		row2 = _Doc({"series_id": "single1", "title": "planted 2", "status": cutover.APPROVED, "end_at_cutover": 1}, doctype="Calendar Cutover Item")
		row2.insert()
		token = self.end()["token"]
		self.end(confirm=True, token=token)
		patched = {unquote(path.rsplit("/", 1)[1]) for _m, path, _p, _b in self.google.writes}
		self.assertNotIn("erp0123456789abcdef", patched)
		self.assertNotIn("single1", patched)

	def test_writing_needs_the_master_switch(self):
		token = self.end()["token"]
		frappe.site.settings["enabled"] = 0
		plan = self.end()
		self.assertFalse(plan["sync_on"], "the dry run still works and says the switch is off")
		with self.assertRaises(_Throw):
			self.end(confirm=True, token=token)
		self.assertEqual(self.google.writes, [])

	def test_a_calendar_changed_since_the_dry_run_is_refused(self):
		token = self.end()["token"]
		self.google.events["ser1"]["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=TH"]
		with self.assertRaises(_Throw):
			self.end(confirm=True, token=token)
		self.assertEqual(self.google.writes, [])

	def test_an_invitation_copy_is_not_touched(self):
		self.google.events["ser1"]["organizer"] = {"email": "lisa@sapphirefountains.com"}
		plan = self.end()
		self.assertNotIn("ser1", [c["series_id"] for c in plan["changes"]])
		self.assertIn("invitation copy", {s["title"]: s["reason"] for s in plan["skipped"]}["Riverwalk - monthly fountain cleaning"])

	def test_a_count_rule_asks_google_for_its_occurrences(self):
		self.google.events["ser1"]["recurrence"] = ["RRULE:FREQ=MONTHLY;COUNT=60;BYMONTHDAY=15"]
		self.google.instances["ser1"] = [{"originalStartTime": {"date": "2026-11-15"}, "start": {"date": "2026-11-15"}}]
		plan = self.end()
		self.assertEqual(
			next(c for c in plan["changes"] if c["series_id"] == "ser1")["after"],
			["RRULE:FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20261101"],
		)
		self.assertTrue(any(path.endswith("/instances") for _m, path, _p, _b in self.google.calls))
		# Only an occurrence dragged past the cutover from earlier: the rule has run out, leave it.
		self.google.instances["ser1"] = [{"originalStartTime": {"date": "2026-09-15"}, "start": {"date": "2026-11-20"}}]
		plan = self.end()
		self.assertNotIn("ser1", [c["series_id"] for c in plan["changes"]])
		self.assertIn("ser1", [c["series_id"] for c in plan["unchanged"]])

	def test_a_cutover_in_the_past_is_refused(self):
		with self.assertRaises(_Throw):
			cutover.end_series("2026-10-01", client=self.google, today=TODAY)
		self.assertEqual(self.google.calls, [])

	def test_one_failed_patch_does_not_stop_the_rest(self):
		self.google.fail_patch.add("ser1")
		token = self.end()["token"]
		out = self.end(confirm=True, token=token)
		self.assertEqual([c["series_id"] for c in out["done"]], ["ser2"])
		self.assertEqual([c["series_id"] for c in out["failed"]], ["ser1"])
		failed = [log for log in frappe.site.tables["Calendar Cutover Log"].values() if log["result"] == "Failed"]
		self.assertEqual(len(failed), 1)
		self.assertEqual(_items()["ser1"]["series_state"], cutover.RUNNING)

	def test_new_running_series_not_on_the_review_list_are_named(self):
		self.google.events["ser_new"] = {
			"id": "ser_new",
			"summary": "Union Station",
			"start": {"date": "2026-01-01"},
			"recurrence": ["RRULE:FREQ=WEEKLY"],
		}
		plan = self.end()
		self.assertEqual(plan["not_on_review_list"], ["Union Station"])
		self.assertNotIn("ser_new", [c["series_id"] for c in plan["changes"]])


class TestUndo(_EndBase):
	def setUp(self):
		super().setUp()
		token = self.end()["token"]
		self.end(confirm=True, token=token)
		self.google.calls.clear()

	def test_undo_restores_the_original_rules(self):
		plan = cutover.undo_end(client=self.google)
		self.assertTrue(plan["dry_run"])
		self.assertEqual(self.google.writes, [])
		self.assertEqual({c["series_id"] for c in plan["changes"]}, {"ser1", "ser2"})
		with self.assertRaises(_Throw):
			cutover.undo_end(confirm=True, client=self.google)
		out = cutover.undo_end(confirm=True, token=plan["token"], client=self.google)
		self.assertEqual(len(out["done"]), 2)
		self.assertEqual(self.google.events["ser1"]["recurrence"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=15"])
		self.assertEqual(
			self.google.events["ser2"]["recurrence"],
			["RRULE:FREQ=WEEKLY;BYDAY=MO", "EXDATE;TZID=America/Denver:20251124T080000"],
		)
		self.assertEqual(_items()["ser1"]["series_state"], cutover.RUNNING)
		ends = [log for log in frappe.site.tables["Calendar Cutover Log"].values() if log["action"] == "End"]
		self.assertTrue(all(log["undone"] == 1 for log in ends))
		self.assertEqual(cutover.undo_end(client=self.google)["changes"], [], "nothing left to undo")

	def test_undo_skips_a_rule_changed_in_google_since(self):
		self.google.events["ser1"]["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
		plan = cutover.undo_end(client=self.google)
		self.assertEqual([c["series_id"] for c in plan["changes"]], ["ser2"])
		reason = next(s["reason"] for s in plan["skipped"] if s["series_id"] == "ser1")
		self.assertIn("RRULE:FREQ=MONTHLY;BYMONTHDAY=15", reason, "the original is shown to put back by hand")

	def test_undo_works_with_the_switch_off(self):
		frappe.site.settings["enabled"] = 0
		plan = cutover.undo_end(client=self.google)
		out = cutover.undo_end(confirm=True, token=plan["token"], client=self.google)
		self.assertEqual(len(out["done"]), 2)


# ---------------------------------------------------------------------- the role gate


class TestRoleGate(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_every_endpoint_is_system_manager_only_and_post_only(self):
		tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
		endpoints = []
		for node in tree.body:
			if not isinstance(node, ast.FunctionDef):
				continue
			whitelisted = [d for d in node.decorator_list if "whitelist" in ast.unparse(d)]
			if not whitelisted:
				continue
			endpoints.append(node.name)
			self.assertIn("methods=['POST']", ast.unparse(whitelisted[0]), node.name)
			first = node.body[0]
			self.assertEqual(ast.unparse(first).strip(), "_require_manager()", f"{node.name} checks the role first")
		self.assertEqual(sorted(endpoints), ["apply_approved", "end_series", "scan", "set_status", "undo_end"])

	def test_a_non_manager_is_refused_before_anything_happens(self):
		frappe.site.roles = ["Projects Manager", "Maintenance Supervisor"]
		google = FakeGoogle()
		with mock.patch.object(google_api, "Client", return_value=google):
			for call in (
				lambda: api.scan(),
				lambda: api.set_status(names='["CCI-00001"]', status="Approved"),
				lambda: api.apply_approved(confirm=1),
				lambda: api.end_series(cutover_date=CUTOVER, confirm=1, token="x"),
				lambda: api.undo_end(confirm=1, token="x"),
			):
				with self.assertRaises(_PermissionError):
					call()
		self.assertEqual(google.calls, [])
		self.assertEqual(frappe.site.writes, [])

	def test_a_manager_gets_through(self):
		google = FakeGoogle()
		with mock.patch.object(google_api, "Client", return_value=google):
			with mock.patch.object(cutover, "_today", return_value=TODAY):
				out = api.scan()
		self.assertEqual(out["counts"]["created"], 4)


# ---------------------------------------------------------------------- doctypes


class TestDocTypes(unittest.TestCase):
	def load(self, name):
		return json.loads((DOCTYPES / name / f"{name}.json").read_text(encoding="utf-8"))

	def test_review_and_log_doctypes(self):
		item = self.load("calendar_cutover_item")
		self.assertEqual(item["name"], "Calendar Cutover Item")
		self.assertEqual(item["module"], "Project Enhancements")
		fields = {f["fieldname"]: f for f in item["fields"]}
		self.assertEqual(fields["status"]["options"].split("\n"), ["Pending", "Approved", "Applied", "Ignored"])
		self.assertEqual(fields["status"].get("read_only"), 1, "status moves only through the buttons")
		self.assertEqual(fields["series_id"].get("unique"), 1)
		for field in cutover.FACT_FIELDS + cutover.PROPOSAL_FIELDS:
			self.assertIn(field, fields)
		self.assertEqual([p["role"] for p in item["permissions"]], ["System Manager"])
		self.assertFalse(item["permissions"][0].get("create"), "only the scan makes rows")
		log = self.load("calendar_cutover_log")
		self.assertEqual([p["role"] for p in log["permissions"]], ["System Manager"])
		self.assertFalse(log["permissions"][0].get("write"))
		self.assertFalse(log["permissions"][0].get("delete"), "the undo record cannot be deleted from the desk")

	def test_controller_class_names_are_the_ones_frappe_derives(self):
		for folder, doctype in (("calendar_cutover_item", "Calendar Cutover Item"), ("calendar_cutover_log", "Calendar Cutover Log")):
			tree = ast.parse((DOCTYPES / folder / f"{folder}.py").read_text(encoding="utf-8"))
			classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
			self.assertEqual(classes, [doctype.replace(" ", "")])


# ---------------------------------------------------------------------- the page under node

DIALOG_HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const calls = [];
const built = [];
let reply = null;
const ctx = {
	console,
	__: (text, args) => (args ? text.replace(/\{(\d+)\}/g, (m, i) => String(args[Number(i)])) : text),
	frappe: {
		listview_settings: {},
		utils: { escape_html: (s) => String(s).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";") },
		datetime: { get_today: () => "2026-10-13" },
		call: (opts) => { calls.push({ method: opts.method, args: opts.args || {} }); return Promise.resolve({ message: reply(opts) }); },
		confirm: (message, yes) => { calls.push({ method: "confirm", args: { message } }); return yes(); },
		msgprint: () => null,
		show_alert: () => null,
		ui: {
			Dialog: class {
				constructor(opts) {
					// What Frappe does: controls are made here, and an autocomplete control asks its
					// get_data at once. A callback reading the dialog's `const` would throw right now.
					(opts.fields || []).forEach((df) => {
						if (df.get_data) df.get_data("");
						if (df.get_query) df.get_query();
						if (df.onchange && df.default !== undefined) df.onchange.call({ df });
					});
					this.opts = opts;
					this.primary = { label: opts.primary_action_label, fn: (v) => opts.primary_action.call(this, v) };
					this.html = "";
					built.push(this);
				}
				show() {}
				hide() {}
				get_values() { return { cutover_date: this.date || "2026-11-02" }; }
				get_field() { const self = this; return { $wrapper: { html: (h) => { self.html = h; } } }; }
				set_primary_action(label, fn) { this.primary = { label, fn }; }
			},
		},
	},
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
const listview = { refresh: () => calls.push({ method: "refresh" }) };
const out = {};
(async () => {
	const plan = { cutover: "2026-11-02", last_day: "2026-11-01", sync_on: true, token: "tok-1",
		changes: [{ title: "Riverwalk <b>", before: ["RRULE:FREQ=MONTHLY"], after: ["RRULE:FREQ=MONTHLY;UNTIL=20261101"], note: "n", moved_after: 1 }],
		unchanged: [], skipped: [{ title: "Belmar", reason: "Pending" }], not_on_review_list: [] };
	reply = (opts) => (opts.args.confirm ? { ...plan, done: plan.changes, failed: [] } : plan);
	const end = ctx.cc_end_dialog(listview);
	out.end_label_before = end.primary.label;
	await end.primary.fn(end.get_values());
	out.end_label_after_dry_run = end.primary.label;
	out.end_html_escaped = end.html.includes("Riverwalk &#60;b&#62;") && !end.html.includes("<b>Riverwalk");
	await end.primary.fn(end.get_values());
	out.end_calls = calls.filter((c) => c.method !== "refresh").map((c) => [c.method.split(".").pop(), c.args.confirm, c.args.token || null, c.args.cutover_date || null]);
	// A different date after the dry run means a new dry run, never a confirm.
	calls.length = 0;
	const again = ctx.cc_end_dialog(listview);
	await again.primary.fn(again.get_values());
	again.date = "2026-11-09";
	await again.primary.fn(again.get_values());
	out.changed_date_calls = calls.map((c) => [c.method.split(".").pop(), c.args.confirm]);
	calls.length = 0;
	const undo_plan = { token: "tok-u", changes: [{ title: "Riverwalk", after: ["RRULE:FREQ=MONTHLY"] }], skipped: [] };
	reply = (opts) => (opts.args.confirm ? { ...undo_plan, done: undo_plan.changes, failed: [] } : undo_plan);
	const undo = ctx.cc_undo_dialog(listview);
	await undo.primary.fn({});
	out.undo_label = undo.primary.label;
	await undo.primary.fn({});
	out.undo_calls = calls.filter((c) => c.method !== "refresh").map((c) => [c.method.split(".").pop(), c.args.confirm, c.args.token || null]);
	out.built = built.length;
	out.listview_settings = Object.keys(ctx.frappe.listview_settings);
	process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack ? e.stack : e)); process.exit(1); });
"""


class TestPageUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.node = shutil.which("node")
		if not cls.node:
			raise unittest.SkipTest("node is not on PATH")

	def test_js_parses(self):
		for path in (LIST_JS, FORM_JS, SETTINGS_JS):
			result = subprocess.run([self.node, "--check", str(path)], capture_output=True, text=True, timeout=60)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	def test_dialogs_build_and_confirm_only_what_the_dry_run_showed(self):
		result = subprocess.run(
			[self.node, "-e", DIALOG_HARNESS, str(LIST_JS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		out = json.loads(result.stdout)
		self.assertEqual(out["listview_settings"], ["Calendar Cutover Item"])
		self.assertEqual(out["end_label_before"], "Dry run")
		self.assertEqual(out["end_label_after_dry_run"], "End 1 series")
		self.assertTrue(out["end_html_escaped"])
		self.assertEqual(
			out["end_calls"],
			[["end_series", 0, None, "2026-11-02"], ["confirm", None, None, None], ["end_series", 1, "tok-1", "2026-11-02"]],
		)
		self.assertEqual(out["changed_date_calls"], [["end_series", 0], ["end_series", 0]])
		self.assertEqual(out["undo_label"], "Restore 1 series")
		self.assertEqual(out["undo_calls"], [["undo_end", 0, None], ["confirm", None, None], ["undo_end", 1, "tok-u"]])

	def test_settings_page_opens_the_cutover_list(self):
		text = SETTINGS_JS.read_text(encoding="utf-8")
		self.assertIn('frappe.set_route("List", "Calendar Cutover Item")', text)


if __name__ == "__main__":
	unittest.main()
