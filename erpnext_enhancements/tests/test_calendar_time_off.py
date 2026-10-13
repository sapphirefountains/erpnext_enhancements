# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar time off in (C4, v1.607.0): the Company Holidays & Vacations calendar read into
Unavailable Planner Blocks.

What this pins, because each of these fails quietly:

* **Matching**: a name (first, full or alias) plus a time-off word; the longer name wins; a shared
  first name is settled by the creator or goes to review; a name with no time-off word is a guess,
  not a match; with no name, whoever created the event; "Company holiday" is everyone; birthdays are
  skipped; a planner's decision (per event, or per title) wins.
* **ERPNext's own events are never read** (C2's private properties, any site's, or its footer).
* **Blocks follow the event**: created, updated, deleted; multi-day and timed events cut per day in
  the site's zone; nothing outside the window; **a hand-made block is never touched**.
* **The loop guard**: an imported block is never synced out (``sources``, ``triggers``), and is
  read-only (``PlannerBlock``) and not editable on either planner.
* **Review list** remember / ignore / title pattern, and Open rows closed once the event is clear.
* **A conflict alert fires once** per block.
* **Switches off means no read** (no HTTP call at all), and **the dry run writes nothing**.

Bench-free: installs its own ``frappe`` and ``requests`` stubs, imports the real modules, and puts
``sys.modules`` back afterwards. Its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_calendar_time_off
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
DOCTYPES = APP / "project_enhancements" / "doctype"
HOOKS = APP / "hooks.py"
PATCHES_TXT = APP / "patches.txt"
SITE = "erp.test"
TODAY = datetime.date(2026, 10, 12)
NOW = datetime.datetime(2026, 10, 12, 9, 0, 0)
HOLIDAYS = "holidays@group.calendar.google.com"

_saved_modules = {}
_modules_before = set()
frappe = None
rules = None
time_off = None
sources = None
triggers = None
payload = None
engine = None
planner_block = None
review_controller = None
settings_controller = None
requests_stub = None


class _Throw(Exception):
	pass


class _Flags(dict):
	def __getattr__(self, name):
		return self.get(name)

	def __setattr__(self, name, value):
		self[name] = value


class _Doc(dict):
	"""Enough of a Document: dict and attribute access, flags, insert/save into the fake tables."""

	def __init__(self, *args, doctype=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.__dict__["flags"] = _Flags()
		self.__dict__["doctype"] = doctype or self.get("doctype")
		self.__dict__["_before"] = None

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			if name.startswith("__"):
				raise AttributeError(name) from None
			return None

	def __setattr__(self, name, value):
		if name in ("doctype",):
			self.__dict__[name] = value
			return
		self[name] = value

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return not self.get("name")

	def get_doc_before_save(self):
		return self.__dict__.get("_before")

	def get_password(self, fieldname="password", raise_exception=True):
		return self.get(fieldname) or None

	def _controller(self, hook):
		fn = frappe.site.controllers.get((self.doctype, hook))
		if fn:
			fn(self)

	def insert(self, ignore_permissions=False):
		if self.doctype == "Notification Log":
			frappe.site.bells.append(dict(self))
			return self
		self._controller("validate")
		frappe.site.counter += 1
		self["name"] = self.get("name") or f"{self.doctype[:4].upper()}-{frappe.site.counter}"
		for key, value in frappe.site.defaults.get(self.doctype, {}).items():
			self.setdefault(key, value)
		frappe.site.table(self.doctype)[self["name"]] = dict(self)
		frappe.site.writes.append(("insert", self.doctype, self["name"]))
		return self

	def save(self, ignore_permissions=False):
		self._controller("validate")
		frappe.site.table(self.doctype)[self["name"]] = dict(self)
		frappe.site.writes.append(("save", self.doctype, self["name"]))
		return self


class _Site:
	def __init__(self):
		self.tables = {}
		self.writes = []
		self.bells = []
		self.enqueued = []
		self.logs = []
		self.singles = []
		self.counter = 0
		self.commits = 0
		self.cache = {}
		self.roles = ["Projects Manager"]
		self.controllers = {}
		self.columns = True
		self.defaults = {"Planner Block": {"source": "Planner", "time_off_alerted": 0}}
		self.settings = _Doc(
			{
				"enabled": 1,
				"read_time_off": 1,
				"refresh_token": "refresh-SECRET",
				"holidays_calendar_id": HOLIDAYS,
				"days_back": 14,
				"days_ahead": 180,
				"authorized_by": "nik@example.com",
				"time_off_alert_recipients": [],
				"time_off_sync_token": "",
			},
			doctype="Calendar Sync Settings",
		)

	def table(self, doctype):
		return self.tables.setdefault(doctype, {})


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
				if not value or not (low <= str(value)[:10] <= high):
					return False
		elif value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.site.writes.append(("get_all", doctype))
	rows = [dict(r) for r in frappe.site.table(doctype).values() if _matches(r, filters)]
	if pluck:
		return [r.get(pluck) for r in rows]
	if fields:
		return [_Doc({f: r.get(f) for f in fields}) for r in rows]
	return [_Doc(r) for r in rows]


class _DB:
	def get_value(self, doctype, filters, field=None, as_dict=False, **kwargs):
		table = frappe.site.table(doctype)
		if isinstance(filters, dict):
			rows = [r for r in table.values() if _matches(r, filters)]
			row = rows[0] if rows else None
		else:
			row = table.get(filters)
		if row is None:
			return None
		if isinstance(field, list | tuple):
			picked = {f: row.get(f) for f in field}
			return _Doc(picked) if as_dict else tuple(picked.values())
		return row.get(field)

	def set_value(self, doctype, name, field, value=None, update_modified=True):
		frappe.site.table(doctype)[name][field] = value
		frappe.site.writes.append(("set_value", doctype, name, field))

	def set_single_value(self, doctype, values, value=None, update_modified=True):
		values = values if isinstance(values, dict) else {values: value}
		frappe.site.singles.append((doctype, values))
		frappe.site.settings.update(values)

	def commit(self):
		frappe.site.commits += 1

	def exists(self, *args, **kwargs):
		return False

	def table_exists(self, doctype):
		return frappe.site.columns

	def has_column(self, doctype, column):
		return frappe.site.columns

	def count(self, doctype, filters=None):
		return len([r for r in frappe.site.table(doctype).values() if _matches(r, filters)])

	def sql(self, *args, **kwargs):
		return []


class _Cache:
	def get_value(self, key):
		return frappe.site.cache.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		frappe.site.cache[key] = value

	def delete_value(self, key):
		frappe.site.cache.pop(key, None)

	def make_key(self, key):
		return f"{SITE}|{key}"

	def set(self, key, value, ex=None, nx=False):
		if nx and key in frappe.site.cache:
			return None
		frappe.site.cache[key] = value
		return True

	def delete(self, key):
		frappe.site.cache.pop(key, None)


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _Doc(doctype, doctype=doctype.get("doctype"))
	if doctype == "Calendar Sync Settings":
		return frappe.site.settings
	row = frappe.site.table(doctype)[name]
	doc = _Doc(dict(row), doctype=doctype)
	doc.__dict__["_before"] = _Doc(dict(row), doctype=doctype)
	return doc


def _delete_doc(doctype, name, ignore_permissions=False, **kwargs):
	row = frappe.site.table(doctype).get(name)
	if row is not None:
		doc = _Doc(dict(row), doctype=doctype)
		doc._controller("on_trash")
	frappe.site.table(doctype).pop(name, None)
	frappe.site.writes.append(("delete", doctype, name))


def _get_cached_doc(doctype, name=None):
	if doctype == "Calendar Sync Settings":
		return frappe.site.settings
	if doctype == "Google Settings":
		return _Doc({"enable": 1, "client_id": "client-123", "client_secret": "client-SECRET"})
	raise KeyError(doctype)


def _install_frappe():
	module = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.getdate = (
		lambda value=None: TODAY if value is None else datetime.date.fromisoformat(str(value)[:10])
	)
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.date_diff = lambda a, b: (utils.getdate(a) - utils.getdate(b)).days
	utils.cint = lambda value: int(float(value or 0))
	utils.escape_html = lambda text: str(text).replace("<", "&lt;").replace(">", "&gt;")
	utils.get_system_timezone = lambda: "America/Denver"
	module.utils = utils
	module._ = lambda text, *a, **k: text
	module.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))

	def throw(message, exc=None, title=None):
		raise (exc or _Throw)(message)

	module.throw = throw
	module.PermissionError = type("PermissionError", (Exception,), {})
	module.get_traceback = lambda: "Traceback: boom"
	module.log_error = lambda title=None, message=None, **k: frappe.site.logs.append((title, message))
	module.enqueue = lambda method, **kwargs: frappe.site.enqueued.append((method, kwargs))
	module.get_all = _get_all
	module.get_doc = _get_doc
	module.delete_doc = _delete_doc
	module.get_cached_doc = _get_cached_doc
	module.new_doc = lambda doctype: _Doc(dict(frappe.site.defaults.get(doctype, {})), doctype=doctype)
	module.clear_document_cache = lambda *a, **k: None
	module.get_roles = lambda user=None: list(frappe.site.roles)
	module.msgprint = lambda *a, **k: None
	module.session = _Flags(user="planner@example.com")
	module.local = types.SimpleNamespace(site=SITE)
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


class _Response:
	def __init__(self, status, payload=None):
		self.status_code = status
		self._payload = payload
		self.headers = {}
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
	global frappe, rules, time_off, sources, triggers, payload, engine, requests_stub
	global planner_block, review_controller, settings_controller
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = _install_frappe()
	requests_stub = _install_requests()
	importlib.import_module("erpnext_enhancements")
	_bare_package("erpnext_enhancements.project_enhancements", APP / "project_enhancements")
	_bare_package("erpnext_enhancements.api", APP / "api")
	base = "erpnext_enhancements.project_enhancements."
	rules = importlib.import_module(base + "calendar_sync.time_off_rules")
	payload = importlib.import_module(base + "calendar_sync.payload")
	sources = importlib.import_module(base + "calendar_sync.sources")
	engine = importlib.import_module(base + "calendar_sync.engine")
	triggers = importlib.import_module(base + "calendar_sync.triggers")
	time_off = importlib.import_module(base + "calendar_sync.time_off")
	planner_block = importlib.import_module(base + "doctype.planner_block.planner_block")
	review_controller = importlib.import_module(
		base + "doctype.calendar_time_off_review.calendar_time_off_review"
	)
	settings_controller = importlib.import_module(
		base + "doctype.calendar_sync_settings.calendar_sync_settings"
	)


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name in ("frappe", "requests") or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- fixtures

STAFF = [
	{
		"name": "RES-1",
		"resource_name": "Austin Smith",
		"resource_type": "Employee",
		"user": "austin@sf.com",
		"is_active": 1,
	},
	{
		"name": "RES-2",
		"resource_name": "Clegg Jones",
		"resource_type": "Employee",
		"user": "clegg@sf.com",
		"is_active": 1,
	},
	{
		"name": "RES-3",
		"resource_name": "Chris Lee",
		"resource_type": "Employee",
		"user": "chrisl@sf.com",
		"is_active": 1,
	},
	{
		"name": "RES-4",
		"resource_name": "Chris Park",
		"resource_type": "Employee",
		"user": "chrisp@sf.com",
		"is_active": 1,
		"calendar_aliases": "CP, Parky",
	},
	{
		"name": "RES-5",
		"resource_name": "Acme Electric",
		"resource_type": "Subcontractor",
		"user": None,
		"is_active": 1,
	},
	{
		"name": "RES-6",
		"resource_name": "Dana Old",
		"resource_type": "Employee",
		"user": "dana@sf.com",
		"is_active": 0,
	},
]


def _people():
	return [
		{
			"resource": r["name"],
			"full_name": r["resource_name"],
			"aliases": r.get("calendar_aliases") or "",
			"user": r.get("user"),
		}
		for r in STAFF
		if r["is_active"] and r["resource_type"] != "Subcontractor"
	]


BY_EMAIL = {
	"austin@sf.com": "RES-1",
	"clegg@sf.com": "RES-2",
	"chrisl@sf.com": "RES-3",
	"chrisp@sf.com": "RES-4",
}


def _event(summary, start="2026-10-15", end=None, eid="evt1", **extra):
	event = {
		"id": eid,
		"status": "confirmed",
		"summary": summary,
		"start": {"date": start},
		"end": {"date": end or str(datetime.date.fromisoformat(start) + datetime.timedelta(days=1))},
		"creator": {"email": "lisa@sf.com"},
		"organizer": {"email": HOLIDAYS, "self": True},
	}
	event.update(extra)
	return event


def _classify(event, **kwargs):
	return rules.classify(event, _people(), BY_EMAIL, calendar_id=HOLIDAYS, **kwargs)


def _reset():
	frappe.site = _Site()
	frappe.flags = _Flags()
	frappe.local = types.SimpleNamespace(site=SITE)
	frappe.session = _Flags(user="planner@example.com")
	requests_stub.script = []
	requests_stub.calls = []
	resources = frappe.site.table("Planner Resource")
	for row in STAFF:
		resources[row["name"]] = dict(row)
	users = frappe.site.table("User")
	for row in STAFF:
		if row.get("user"):
			users[row["user"]] = {"name": row["user"], "email": row["user"]}
	# The real Planner Block controller guards every write the import makes.
	frappe.site.controllers[("Planner Block", "validate")] = (
		lambda doc: planner_block.PlannerBlock._guard_imported(doc)
	)
	frappe.site.controllers[("Planner Block", "on_trash")] = lambda doc: planner_block.PlannerBlock.on_trash(
		doc
	)


def _blocks(source=None):
	rows = list(frappe.site.table("Planner Block").values())
	return [r for r in rows if source is None or r.get("source") == source]


def _hand_made(name, resource, date, source="Planner"):
	frappe.site.table("Planner Block")[name] = {
		"name": name,
		"resource": resource,
		"date": date,
		"all_day": 1,
		"from_time": None,
		"to_time": None,
		"note": "Dentist",
		"source": source,
		"google_event_id": None,
		"time_off_alerted": 0,
	}


def _run(dry_run=False):
	return time_off.Run(
		frappe.site.settings, datetime.date(2026, 9, 28), datetime.date(2027, 4, 10), dry_run=dry_run
	)


def _apply(event, dry_run=False, run=None):
	run = run or _run(dry_run)
	existing = time_off.imported_blocks([event["id"]], run.start, run.end).get(event["id"]) or []
	with mock.patch.object(time_off, "_conflicts_for", return_value=[]):
		outcome = time_off.handle_event(run, event, existing)
	return outcome, run


# ---------------------------------------------------------------------- matching


class TitleMatching(unittest.TestCase):
	def test_name_and_word(self):
		for title, who in (
			("Austin Off", ["RES-1"]),
			("Clegg - ooo", ["RES-2"]),
			("Austin's vacation", ["RES-1"]),
			("AUSTIN PTO", ["RES-1"]),
			("Clegg out sick", ["RES-2"]),
			("Austin & Clegg out", ["RES-1", "RES-2"]),
			("Austin Smith on leave", ["RES-1"]),
			("Parky off", ["RES-4"]),
			("CP - dentist appt", ["RES-4"]),
		):
			with self.subTest(title=title):
				answer = _classify(_event(title))
				self.assertEqual(answer["outcome"], rules.IMPORT, answer)
				self.assertEqual(answer["how"], rules.HOW_TITLE)
				self.assertEqual(answer["resources"], who)

	def test_whole_words_only(self):
		# "Austin" inside "Austintown" is not Austin; "off" inside "office" is not off.
		self.assertEqual(_classify(_event("Austintown office party"))["outcome"], rules.REVIEW)

	def test_longer_name_wins_and_shared_first_name_goes_to_review(self):
		self.assertEqual(_classify(_event("Chris Park off"))["resources"], ["RES-4"])
		answer = _classify(_event("Chris off"))
		self.assertEqual(answer["outcome"], rules.REVIEW)
		self.assertIn("More than one", answer["reason"])

	def test_creator_settles_a_shared_first_name(self):
		answer = _classify(_event("Chris off", creator={"email": "ChrisP@sf.com"}))
		self.assertEqual((answer["outcome"], answer["resources"]), (rules.IMPORT, ["RES-4"]))

	def test_name_without_a_time_off_word_is_a_guess(self):
		answer = _classify(_event("Austin - Denver"))
		self.assertEqual(answer["outcome"], rules.REVIEW)
		self.assertEqual(answer["suggested"], "RES-1")

	def test_inactive_and_subcontractors_are_not_names(self):
		self.assertEqual(_classify(_event("Dana off"))["outcome"], rules.REVIEW)
		self.assertEqual(_classify(_event("Acme off"))["outcome"], rules.REVIEW)

	def test_aliases_are_split_and_normalized(self):
		self.assertEqual(rules.split_aliases("AJ, Mr. Smith\nSmitty;aj"), ["aj", "mr smith", "smitty"])


class CreatorFallback(unittest.TestCase):
	def test_word_without_a_name_is_the_creator(self):
		answer = _classify(_event("OOO", creator={"email": "clegg@sf.com"}))
		self.assertEqual(
			(answer["outcome"], answer["how"], answer["resources"]),
			(rules.IMPORT, rules.HOW_CREATOR, ["RES-2"]),
		)

	def test_organizer_when_no_creator_but_never_the_calendar_itself(self):
		event = _event("Out sick", creator={}, organizer={"email": "austin@sf.com"})
		self.assertEqual(_classify(event)["resources"], ["RES-1"])
		event = _event("Out sick", creator={"email": "nobody@else.com"})
		self.assertEqual(_classify(event)["outcome"], rules.REVIEW)

	def test_no_word_no_name_is_review_with_the_creator_as_a_guess(self):
		answer = _classify(_event("Thanksgiving", creator={"email": "austin@sf.com"}))
		self.assertEqual(answer["outcome"], rules.REVIEW)
		self.assertEqual(answer["suggested"], "RES-1")


class Skips(unittest.TestCase):
	def test_birthdays(self):
		for title in ("Austin's Birthday", "Clegg bday", "B-Day: Chris Park", "birthdays"):
			with self.subTest(title=title):
				self.assertEqual(_classify(_event(title))["how"], rules.HOW_BIRTHDAY)
		self.assertEqual(_classify(_event("Austin", eventType="birthday"))["how"], rules.HOW_BIRTHDAY)

	def test_company_holiday_is_everyone(self):
		answer = _classify(_event("Company Holiday - Thanksgiving"))
		self.assertEqual(
			(answer["outcome"], answer["everyone"], answer["how"]), (rules.IMPORT, True, rules.HOW_COMPANY)
		)

	def test_erpnext_events_are_never_read(self):
		ours = _event(
			"Austin — Unavailable",
			extendedProperties={"private": {"erpnext_site": SITE, "erpnext_doctype": "Planner Block"}},
		)
		self.assertEqual(_classify(ours)["how"], rules.HOW_OURS)
		staging = _event("Austin off", extendedProperties={"private": {"erpnext_site": "staging.test"}})
		self.assertEqual(_classify(staging)["how"], rules.HOW_OURS)
		footer = _event("Austin off", description="x\n\n" + payload.FOOTER.format(link="https://x"))
		self.assertEqual(_classify(footer)["how"], rules.HOW_OURS)

	def test_the_property_keys_are_c2s(self):
		self.assertEqual(rules.ERPNEXT_PROPS, (payload.PROP_SITE, payload.PROP_DOCTYPE))
		self.assertTrue(payload.FOOTER.startswith(rules.ERPNEXT_FOOTER))

	def test_a_deleted_event_is_gone(self):
		self.assertEqual(_classify(_event("Austin off", status="cancelled"))["outcome"], rules.GONE)


class Decisions(unittest.TestCase):
	def test_event_decision_wins(self):
		decisions = {"evt1": {"status": "Assigned", "resource": "RES-2"}}
		answer = _classify(_event("Austin off"), decisions=decisions)
		self.assertEqual((answer["how"], answer["resources"]), (rules.HOW_DECISION, ["RES-2"]))
		ignored = _classify(_event("Austin off"), decisions={"evt1": {"status": "Ignored"}})
		self.assertEqual((ignored["outcome"], ignored["how"]), (rules.SKIP, rules.HOW_IGNORED))
		everyone = _classify(_event("Party"), decisions={"evt1": {"status": "Everyone"}})
		self.assertTrue(everyone["everyone"])

	def test_title_pattern(self):
		patterns = {rules.title_key("Thanksgiving 2026"): {"status": "Everyone"}}
		answer = _classify(_event("THANKSGIVING!", eid="other"), patterns=patterns)
		self.assertEqual((answer["how"], answer["everyone"]), (rules.HOW_PATTERN, True))
		self.assertEqual(rules.title_key("Thanksgiving 2026"), "thanksgiving")

	def test_an_open_row_decides_nothing(self):
		answer = _classify(_event("Austin off"), decisions={"evt1": {"status": "Open"}})
		self.assertEqual(answer["how"], rules.HOW_TITLE)


class Days(unittest.TestCase):
	def test_all_day_end_is_exclusive(self):
		days = rules.event_days(_event("x", start="2026-10-15", end="2026-10-18"))
		self.assertEqual([d["date"].day for d in days], [15, 16, 17])
		self.assertTrue(all(d["all_day"] for d in days))

	def test_timed_in_the_site_zone(self):
		zone = rules.zone_for("America/Denver")
		event = {"start": {"dateTime": "2026-10-15T19:00:00Z"}, "end": {"dateTime": "2026-10-15T23:00:00Z"}}
		self.assertEqual(
			rules.event_days(event, zone),
			[
				{
					"date": datetime.date(2026, 10, 15),
					"all_day": False,
					"from_time": "13:00:00",
					"to_time": "17:00:00",
				}
			],
		)

	def test_timed_across_midnight_and_whole_days(self):
		event = {
			"start": {"dateTime": "2026-10-16T17:00:00-06:00"},
			"end": {"dateTime": "2026-10-19T08:00:00-06:00"},
		}
		days = rules.event_days(event, rules.zone_for("America/Denver"))
		self.assertEqual(
			[(d["date"].day, d["all_day"], d["from_time"], d["to_time"]) for d in days],
			[
				(16, False, "17:00:00", "23:59:00"),
				(17, True, None, None),
				(18, True, None, None),
				(19, False, "00:00:00", "08:00:00"),
			],
		)

	def test_backwards_is_nothing(self):
		event = {
			"start": {"dateTime": "2026-10-16T17:00:00-06:00"},
			"end": {"dateTime": "2026-10-16T08:00:00-06:00"},
		}
		self.assertEqual(rules.event_days(event), [])

	def test_time_text_reads_mariadb_timedeltas(self):
		self.assertEqual(rules.time_text(datetime.timedelta(hours=13, minutes=5)), "13:05:00")
		self.assertEqual(rules.time_text("9:30"), "09:30:00")

	def test_diff(self):
		wanted = rules.wanted_blocks(["RES-1"], rules.event_days(_event("x", end="2026-10-17")), "n")
		existing = [
			{"name": "B1", "resource": "RES-1", "date": "2026-10-15", "all_day": 1, "note": "n"},
			{"name": "B0", "resource": "RES-1", "date": "2026-10-15", "all_day": 1, "note": "n"},
			{"name": "B2", "resource": "RES-1", "date": "2026-10-20", "all_day": 1, "note": "n"},
		]
		create, update, delete = rules.diff_blocks(wanted, existing)
		self.assertEqual([str(c["date"]) for c in create], ["2026-10-16"])
		self.assertEqual(update, [])
		self.assertEqual(sorted(delete), ["B1", "B2"])  # the duplicate and the day no longer wanted


# ---------------------------------------------------------------------- the import


class Blocks(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.flags[time_off.IMPORT_FLAG] = True

	def test_create_update_delete(self):
		_apply(_event("Austin Off", end="2026-10-17"))
		blocks = _blocks("Google")
		self.assertEqual(sorted(b["date"] for b in blocks), ["2026-10-15", "2026-10-16"])
		self.assertTrue(all(b["resource"] == "RES-1" and b["google_event_id"] == "evt1" for b in blocks))
		self.assertTrue(all(b["note"] == "From Google Calendar: Austin Off" for b in blocks))

		timed = _event("Austin off")
		timed["start"] = {"dateTime": "2026-10-15T13:00:00-06:00"}
		timed["end"] = {"dateTime": "2026-10-15T15:00:00-06:00"}
		_apply(timed)
		blocks = _blocks("Google")
		self.assertEqual(len(blocks), 1)
		self.assertEqual(
			(blocks[0]["all_day"], blocks[0]["from_time"], blocks[0]["to_time"]), (0, "13:00:00", "15:00:00")
		)

		_apply(_event("Austin off", status="cancelled"))
		self.assertEqual(_blocks("Google"), [])

	def test_hand_made_blocks_untouched(self):
		_hand_made("PBLK-HAND", "RES-1", "2026-10-15")
		_hand_made("PBLK-NULL", "RES-1", "2026-10-15", source=None)  # a row from before the column
		before = {k: dict(v) for k, v in frappe.site.table("Planner Block").items()}
		_apply(_event("Austin Off"))
		_apply(_event("Austin Off", status="cancelled"))
		run = _run()
		time_off.remove_vanished(run)
		for name in ("PBLK-HAND", "PBLK-NULL"):
			self.assertEqual(frappe.site.table("Planner Block")[name], before[name])
		self.assertFalse([w for w in frappe.site.writes if w[0] in ("save", "delete") and w[2] in before])

	def test_never_updates_a_hand_made_block_even_if_asked(self):
		_hand_made("PBLK-HAND", "RES-1", "2026-10-15")
		self.assertIsNone(time_off._update_block("PBLK-HAND", {"all_day": 0}))
		self.assertFalse(time_off._delete_block("PBLK-HAND"))
		self.assertIn("PBLK-HAND", frappe.site.table("Planner Block"))

	def test_company_holiday_everyone_but_subcontractors_and_existing_holidays(self):
		holidays = {"RES-2": {datetime.date(2026, 11, 26): {"description": "Thanksgiving"}}}
		with mock.patch.object(time_off, "_holidays_for", return_value=holidays):
			_apply(_event("Company Holiday", start="2026-11-26"))
		people = sorted(b["resource"] for b in _blocks("Google"))
		self.assertEqual(
			people, ["RES-1", "RES-3", "RES-4"]
		)  # not Clegg (already a holiday), not Acme, not Dana

	def test_outside_the_window_makes_nothing(self):
		_apply(_event("Austin off", start="2028-01-05"))
		self.assertEqual(_blocks(), [])

	def test_birthday_and_erpnext_events_make_nothing(self):
		_apply(_event("Austin's birthday"))
		_apply(
			_event(
				"Austin — Unavailable",
				eid="erp1",
				extendedProperties={"private": {"erpnext_site": SITE, "erpnext_doctype": "Planner Block"}},
			)
		)
		self.assertEqual(_blocks(), [])
		self.assertEqual(frappe.site.table("Calendar Time Off Review"), {})


class ReviewList(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.flags[time_off.IMPORT_FLAG] = True

	def _rows(self):
		return list(frappe.site.table("Calendar Time Off Review").values())

	def test_unmatched_waits_and_is_remembered(self):
		event = _event("Denver trip", eid="evt9", creator={"email": "nobody@x.com"})
		_outcome, run = _apply(event)
		rows = self._rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			(rows[0]["status"], rows[0]["event_id"], rows[0]["title_key"]), ("Open", "evt9", "denver trip")
		)
		self.assertEqual(json.loads(rows[0]["event_json"])["id"], "evt9")
		self.assertEqual(len(run.new_reviews), 1)
		self.assertEqual(_blocks(), [])

		# A planner assigns Clegg: the stored event becomes his block, without Google.
		row = rows[0]
		frappe.site.table("Calendar Time Off Review")[row["name"]].update(status="Assigned", resource="RES-2")
		with mock.patch.object(time_off, "_conflicts_for", return_value=[]):
			time_off.apply_review(row["name"])
		self.assertEqual([(b["resource"], b["date"]) for b in _blocks("Google")], [("RES-2", "2026-10-15")])
		self.assertEqual(requests_stub.calls, [])

		# Re-reading the same event keeps the decision and makes no second row.
		_apply(event)
		self.assertEqual(len(self._rows()), 1)
		self.assertEqual(len(_blocks("Google")), 1)

		# Ignore: the block goes.
		frappe.site.table("Calendar Time Off Review")[row["name"]].update(status="Ignored", resource=None)
		with mock.patch.object(time_off, "_conflicts_for", return_value=[]):
			time_off.apply_review(row["name"])
		self.assertEqual(_blocks("Google"), [])

	def test_title_pattern_resolves_other_open_rows(self):
		_apply(_event("Thanksgiving", eid="a", start="2026-11-26"))
		_apply(_event("Thanksgiving", eid="b", start="2026-11-27"))
		rows = sorted(self._rows(), key=lambda r: r["event_id"])
		self.assertEqual(len(rows), 2)
		frappe.site.table("Calendar Time Off Review")[rows[0]["name"]].update(
			status="Everyone", remember_title=1
		)
		with (
			mock.patch.object(time_off, "_holidays_for", return_value={}),
			mock.patch.object(time_off, "_conflicts_for", return_value=[]),
		):
			time_off.apply_review(rows[0]["name"])
		self.assertEqual(sorted({b["date"] for b in _blocks("Google")}), ["2026-11-26", "2026-11-27"])
		remaining = self._rows()
		self.assertEqual([r["event_id"] for r in remaining], ["a"])  # the other Open row closed

	def test_open_row_closes_when_the_event_becomes_clear(self):
		_apply(_event("Denver", eid="c"))
		self.assertEqual(len(self._rows()), 1)
		_apply(_event("Austin off", eid="c"))
		self.assertEqual(self._rows(), [])
		self.assertEqual(len(_blocks("Google")), 1)

	def test_review_controller_queues_the_decision(self):
		doc = review_controller.CalendarTimeOffReview(
			{"name": "CTOR-1", "status": "Assigned", "resource": "RES-2"}, doctype="Calendar Time Off Review"
		)
		doc.__dict__["_before"] = _Doc({"status": "Open"})
		doc.validate()
		self.assertEqual(doc.decided_by, "planner@example.com")
		doc.on_update()
		self.assertEqual(frappe.site.enqueued[-1][1]["name"], "CTOR-1")
		self.assertTrue(frappe.site.enqueued[-1][1]["enqueue_after_commit"])
		inactive = review_controller.CalendarTimeOffReview(
			{"name": "CTOR-2", "status": "Assigned", "resource": "RES-6"}, doctype="Calendar Time Off Review"
		)
		with self.assertRaises(_Throw):
			inactive.validate()


class ConflictAlert(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.flags[time_off.IMPORT_FLAG] = True
		frappe.site.settings["time_off_alert_recipients"] = [
			{"user": "lisa@sf.com"},
			{"user": "james@sf.com"},
		]

	def test_alerts_once_per_block(self):
		conflict = [
			{"date": "2026-10-15", "label": "Booked on a day off", "items": [{"title": "Riverwalk pump set"}]}
		]
		event = _event("Austin off")
		with mock.patch.object(time_off, "_conflicts_for", return_value=conflict) as check:
			run = _run()
			time_off.handle_event(run, event, [])
			time_off.notify(run)
			self.assertEqual(check.call_count, 1)
			# The same event read again (and edited to a timed slot on the same day): no second alert.
			for title in ("Austin off", "Austin off 1-3"):
				run = _run()
				existing = time_off.imported_blocks(["evt1"], run.start, run.end).get("evt1") or []
				time_off.handle_event(run, _event(title), existing)
				time_off.notify(run)
		self.assertEqual(sorted(b["for_user"] for b in frappe.site.bells), ["james@sf.com", "lisa@sf.com"])
		self.assertIn("Riverwalk pump set", frappe.site.bells[0]["email_content"])
		self.assertEqual(_blocks("Google")[0]["time_off_alerted"], 1)

	def test_no_conflict_no_alert_and_past_days_not_checked(self):
		with mock.patch.object(time_off, "_conflicts_for", return_value=[]) as check:
			run = _run()
			time_off.handle_event(run, _event("Austin off", start="2026-10-01"), [])
			time_off.notify(run)
		check.assert_not_called()
		self.assertEqual(frappe.site.bells, [])

	def test_recipients_fall_back_to_the_authorizer(self):
		frappe.site.settings["time_off_alert_recipients"] = []
		self.assertEqual(time_off.recipients(frappe.site.settings), ["nik@example.com"])


# ---------------------------------------------------------------------- the loop guard


class LoopGuard(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_imported_block_reads_as_gone(self):
		frappe.site.table("Planner Block")["PBLK-G"] = {
			"name": "PBLK-G",
			"resource": "RES-1",
			"resource_name": "Austin Smith",
			"date": "2026-10-15",
			"all_day": 1,
			"source": "Google",
		}
		_hand_made("PBLK-H", "RES-1", "2026-10-15")
		frappe.site.table("Planner Block")["PBLK-H"]["resource_name"] = "Austin Smith"
		self.assertEqual(sources.block_item("PBLK-G")["state"], payload.STATE_GONE)
		self.assertEqual(sources.block_item("PBLK-H")["state"], payload.STATE_ACTIVE)

	def test_window_skips_imported_blocks(self):
		frappe.site.table("Planner Block")["PBLK-G"] = {
			"name": "PBLK-G",
			"date": "2026-10-15",
			"source": "Google",
		}
		_hand_made("PBLK-H", "RES-1", "2026-10-15")
		_hand_made("PBLK-N", "RES-1", "2026-10-15", source=None)
		with (
			mock.patch.object(sources.frappe.db, "sql", return_value=[]),
			mock.patch.object(
				sources.ca,
				"_task_columns",
				return_value={
					"custom_start_datetime": "NULL",
					"custom_end_datetime": "NULL",
					"custom_rental_booking": "NULL",
				},
			),
			mock.patch.object(sources.ca, "planner_job_condition", return_value="1=1"),
		):
			refs = sources.refs_in_window(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
		blocks = sorted(name for doctype, name in refs if doctype == "Planner Block")
		self.assertEqual(blocks, ["PBLK-H", "PBLK-N"])

	def test_reconcile_never_writes_an_imported_block(self):
		frappe.site.table("Planner Block")["PBLK-G"] = {
			"name": "PBLK-G",
			"resource": "RES-1",
			"date": "2026-10-15",
			"all_day": 1,
			"source": "Google",
		}
		item = sources.item_for("Planner Block", "PBLK-G")
		with mock.patch.object(engine, "get_link", return_value=None):
			self.assertEqual(engine.reconcile(item, frappe.site.settings, client=None)[0], "none")
		self.assertEqual(requests_stub.calls, [])

	def test_trigger_does_not_queue_an_imported_block(self):
		frappe.site.settings["enabled"] = 1
		triggers.on_change(_Doc({"name": "PBLK-G", "source": "Google"}, doctype="Planner Block"))
		self.assertEqual(frappe.site.enqueued, [])
		triggers.on_change(_Doc({"name": "PBLK-H", "source": "Planner"}, doctype="Planner Block"))
		self.assertEqual(len(frappe.site.enqueued), 1)

	def test_imported_block_is_read_only(self):
		doc = planner_block.PlannerBlock({"name": "PBLK-G", "source": "Google"}, doctype="Planner Block")
		doc.__dict__["_before"] = _Doc({"source": "Google"})
		with self.assertRaises(_Throw):
			doc._guard_imported()
		forged = planner_block.PlannerBlock({"source": "Google"}, doctype="Planner Block")
		with self.assertRaises(_Throw):
			forged._guard_imported()  # nobody but the import makes one
		converted = planner_block.PlannerBlock(
			{"name": "PBLK-H", "source": "Google"}, doctype="Planner Block"
		)
		converted.__dict__["_before"] = _Doc({"source": "Planner"})
		with self.assertRaises(_Throw):
			converted._guard_imported()
		hand = planner_block.PlannerBlock({"name": "PBLK-H", "source": "Planner"}, doctype="Planner Block")
		hand.__dict__["_before"] = _Doc({"source": "Planner"})
		hand._guard_imported()
		frappe.flags[time_off.IMPORT_FLAG] = True
		doc._guard_imported()
		self.assertEqual(time_off.IMPORT_FLAG, planner_block.IMPORT_FLAG)

	def test_delete_only_by_import_or_system_manager(self):
		doc = planner_block.PlannerBlock({"name": "PBLK-G", "source": "Google"}, doctype="Planner Block")
		with self.assertRaises(_Throw):
			doc.on_trash()
		frappe.site.roles = ["System Manager"]
		doc.on_trash()

	def test_planners_cannot_edit_it(self):
		from erpnext_enhancements.project_enhancements import crew_availability

		booking = crew_availability.block_booking({"name": "PBLK-G", "source": "Google"}, None, 0)
		self.assertTrue(booking["from_google"])
		self.assertFalse(
			crew_availability.block_booking({"name": "B", "source": None}, None, 0)["from_google"]
		)
		code = (APP / "api" / "planner_blocks.py").read_text(encoding="utf-8")
		self.assertIn('"can_edit": bool(schedule or own) and not from_google', code)
		project = (
			APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
		).read_text(encoding="utf-8")
		self.assertEqual(project.count("!booking.from_google"), 3)
		maintenance = (
			APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
		).read_text(encoding="utf-8")
		self.assertEqual(maintenance.count("!block.from_google"), 2)


# ---------------------------------------------------------------------- switches, reads, dry run


def _listing(items, sync="tok-2", page=None):
	answer = {"items": items}
	if page:
		answer["nextPageToken"] = page
	else:
		answer["nextSyncToken"] = sync
	return _Response(200, answer)


class Switches(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.site.cache["ee_calendar_sync_access_token"] = "access-1"

	def test_off_means_no_read(self):
		for field, value in (
			("read_time_off", 0),
			("enabled", 0),
			("holidays_calendar_id", ""),
			("refresh_token", ""),
		):
			with self.subTest(field=field):
				_reset()
				frappe.site.settings[field] = value
				self.assertIsNone(time_off.incremental_read())
				self.assertIsNone(time_off.nightly_read())
				self.assertIsNone(time_off.apply_review("CTOR-1"))
				self.assertEqual(requests_stub.calls, [])
				self.assertEqual(_blocks(), [])
				self.assertEqual(frappe.site.singles, [])

	def test_not_migrated_means_no_read(self):
		frappe.site.columns = False
		self.assertIsNone(time_off.incremental_read())
		self.assertEqual(requests_stub.calls, [])

	def test_incremental_read_stores_its_own_token(self):
		requests_stub.script = [_listing([_event("Austin off")], sync="tok-9")]
		with mock.patch.object(time_off, "_conflicts_for", return_value=[]):
			time_off.incremental_read()
		method, url, kwargs = requests_stub.calls[0]
		self.assertIn("/calendars/holidays%40group.calendar.google.com/events", url)
		self.assertEqual(kwargs["params"]["singleEvents"], "true")
		self.assertNotIn("syncToken", kwargs["params"])
		self.assertEqual(frappe.site.settings["time_off_sync_token"], "tok-9")
		self.assertNotIn("sync_tokens", {k for _d, v in frappe.site.singles for k in v})  # C2's are untouched
		self.assertEqual(len(_blocks("Google")), 1)
		requests_stub.script = [_listing([], sync="tok-10")]
		time_off.incremental_read()
		self.assertEqual(requests_stub.calls[-1][2]["params"]["syncToken"], "tok-9")

	def test_expired_token_lists_again(self):
		frappe.site.settings["time_off_sync_token"] = "old"
		requests_stub.script = [
			_Response(410, {"error": {"errors": [{"reason": "fullSyncRequired"}]}}),
			_listing([], sync="new"),
		]
		time_off.incremental_read()
		self.assertEqual(frappe.site.settings["time_off_sync_token"], "new")

	def test_full_read_removes_vanished_imported_blocks_only(self):
		frappe.flags[time_off.IMPORT_FLAG] = True
		_apply(_event("Austin off", eid="gone1"))
		frappe.flags[time_off.IMPORT_FLAG] = False
		_hand_made("PBLK-HAND", "RES-1", "2026-10-15")
		requests_stub.script = [_listing([_event("Clegg off", eid="still")])]
		with mock.patch.object(time_off, "_conflicts_for", return_value=[]):
			time_off.nightly_read()
		params = requests_stub.calls[0][2]["params"]
		self.assertIn("timeMin", params)
		self.assertNotIn("syncToken", params)
		self.assertEqual(
			sorted((b["google_event_id"], b["resource"]) for b in _blocks("Google")), [("still", "RES-2")]
		)
		self.assertIn("PBLK-HAND", frappe.site.table("Planner Block"))

	def test_incomplete_full_read_deletes_nothing(self):
		frappe.flags[time_off.IMPORT_FLAG] = True
		_apply(_event("Austin off", eid="keep"))
		frappe.flags[time_off.IMPORT_FLAG] = False
		with mock.patch.object(time_off, "PAGES", 1):
			requests_stub.script = [_listing([], page="more")]
			time_off.nightly_read()
		self.assertEqual(len(_blocks("Google")), 1)

	def test_settings_validation(self):
		doc = settings_controller.CalendarSyncSettings(
			dict(frappe.site.settings, holidays_calendar_id=""), doctype="Calendar Sync Settings"
		)
		with self.assertRaises(_Throw):
			doc._validate_time_off()
		doc = settings_controller.CalendarSyncSettings(
			dict(frappe.site.settings, enabled=0), doctype="Calendar Sync Settings"
		)
		doc.__dict__["_before"] = _Doc({"read_time_off": 0})
		with self.assertRaises(_Throw):
			doc._validate_time_off()
		# Already ticked: switching sync off still saves.
		doc.__dict__["_before"] = _Doc({"read_time_off": 1})
		doc._validate_time_off()


class DryRun(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.site.cache["ee_calendar_sync_access_token"] = "access-1"
		frappe.site.settings["read_time_off"] = 0  # the preview works with the switch off

	def test_preview_writes_nothing(self):
		_hand_made("PBLK-HAND", "RES-1", "2026-10-20")
		frappe.site.table("Planner Block")["PBLK-OLD"] = {
			"name": "PBLK-OLD",
			"resource": "RES-2",
			"date": "2026-10-21",
			"all_day": 1,
			"source": "Google",
			"google_event_id": "vanished",
		}

		def snapshot():
			return json.dumps({k: v for k, v in frappe.site.tables.items() if v}, sort_keys=True, default=str)

		before = snapshot()
		writes_before = [w for w in frappe.site.writes if w[0] != "get_all"]
		requests_stub.script = [
			_listing(
				[
					_event("Austin off", eid="a"),
					_event("Denver trip", eid="b"),
					_event("Clegg's birthday", eid="c"),
				]
			)
		]
		with mock.patch.object(
			time_off, "_conflicts_for", side_effect=AssertionError("no conflict check in a dry run")
		):
			result = time_off.preview("2026-10-01", "2026-10-31")
		self.assertEqual(snapshot(), before)
		self.assertEqual([w for w in frappe.site.writes if w[0] != "get_all"], writes_before)
		self.assertEqual(
			(frappe.site.bells, frappe.site.singles, frappe.site.enqueued, frappe.site.commits),
			([], [], [], 0),
		)
		self.assertEqual([c[0] for c in requests_stub.calls], ["GET"])
		actions = sorted((r["action"], r["person"]) for r in result["rows"])
		self.assertEqual(actions, [("create", "Austin Smith"), ("delete", "Clegg Jones")])
		self.assertEqual([r["title"] for r in result["review"]], ["Denver trip"])
		self.assertEqual(result["counts"].get("birthday"), 1)
		self.assertFalse(result["enabled"])


# ---------------------------------------------------------------------- wiring


class Wiring(unittest.TestCase):
	def test_hooks(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		scheduler = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events"
		)
		events = ast.literal_eval(scheduler)
		path = "erpnext_enhancements.project_enhancements.calendar_sync.time_off."
		self.assertIn(path + "incremental_read", events["cron"]["*/10 * * * *"])
		self.assertIn(path + "nightly_read", events["daily_long"])

	def test_patch_and_doctypes(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		self.assertIn("erpnext_enhancements.patches.materialize_calendar_time_off_settings", lines)
		self.assertGreater(
			lines.index("erpnext_enhancements.patches.materialize_calendar_time_off_settings"),
			lines.index("[post_model_sync]"),
		)
		block = json.loads((DOCTYPES / "planner_block" / "planner_block.json").read_text(encoding="utf-8"))
		source = next(f for f in block["fields"] if f["fieldname"] == "source")
		# A normal doctype: the ALTER writes the default into every existing row, so old blocks read
		# as made in ERPNext. The import matches only "Google".
		self.assertEqual(
			(source["default"], source["options"].split("\n")), ("Planner", ["Planner", "Google"])
		)
		settings = json.loads(
			(DOCTYPES / "calendar_sync_settings" / "calendar_sync_settings.json").read_text(encoding="utf-8")
		)
		switch = next(f for f in settings["fields"] if f["fieldname"] == "read_time_off")
		self.assertEqual((switch["fieldtype"], switch["default"]), ("Check", "0"))
		names = {f["fieldname"] for f in settings["fields"]}
		self.assertEqual(names, set(settings["field_order"]))
		review = json.loads(
			(DOCTYPES / "calendar_time_off_review" / "calendar_time_off_review.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertEqual(review["name"], "Calendar Time Off Review")
		self.assertEqual(review_controller.CalendarTimeOffReview.__name__, review["name"].replace(" ", ""))

	def test_reads_with_singleEvents_and_never_writes_to_google(self):
		code = (SYNC_DIR / "time_off.py").read_text(encoding="utf-8")
		for forbidden in ("insert_event", "update_event", "delete_event"):
			self.assertNotIn(forbidden, code)
		client = (SYNC_DIR / "google_api.py").read_text(encoding="utf-8")
		self.assertIn('"singleEvents": "true"', client)

	def test_rules_module_is_pure(self):
		tree = ast.parse((SYNC_DIR / "time_off_rules.py").read_text(encoding="utf-8"))
		imported = {
			alias.name.split(".")[0]
			for node in ast.walk(tree)
			if isinstance(node, ast.Import)
			for alias in node.names
		}
		imported |= {
			node.module.split(".")[0]
			for node in ast.walk(tree)
			if isinstance(node, ast.ImportFrom) and node.module
		}
		self.assertNotIn("frappe", imported)


if __name__ == "__main__":
	unittest.main()
