"""Bench-free tests for the trip views: Plan a Trip's Overview / Grid / Compare / View-as,
``/itinerary?trip=X&as=Y``, and the itinerary email preview.

``api/travel.py`` shapes a trip for display, ``travel_management/views.py`` assembles the
Plan a Trip views from that shape, and ``travel_management/notifications.py`` renders the
itinerary email. All three run here without a site: a ``frappe`` stub is installed in
``setUpModule`` before they are imported.

The failures guarded here all look fine on screen:

1. **Money on a crew member's screen.** The views follow "all but money" (owner's rule,
   2026-09-26): anyone who can open the trip sees every booking and confirmation number,
   but cost, who paid, billable, per diem, claims and totals are for coordinators only —
   and they must be absent from the *payload*, not hidden by the page. Every
   non-coordinator payload is walked key by key, and the fixture's sentinel amounts must
   not appear anywhere in its JSON.
2. **A preview that sends.** Previewing one person's itinerary email must never call
   ``frappe.sendmail``, insert a Notification Log or queue a job; the stubs record every
   such call and the tests assert there were none. The real send must still deliver exactly
   what the preview showed.
3. **The emails changing under the new keys.** ``shape_itinerary`` only gains keys; each
   person's own view keeps its own confirmation number and no ``members`` list.
4. **Someone else's itinerary for a stranger.** ``?as=`` accepts only a crew member, and
   refuses anyone else with one plain sentence without looking them up. Naming a known
   Employee told any trip reader the name behind any Employee id. A filter dict sent in a
   JSON body turned that lookup into a yes/no question about any Employee field.
5. **The coordinator gate itself.** Most tests patch ``_is_coordinator``, so one class runs
   the real one down to the Travel Trip controller's role check. With the gate wired to True
   for everyone, the suite used to pass.

unittest, not pytest, with its own CI step: a pytest-style suite in a unittest list is
collected as nothing, and this module's ``frappe`` stub must not share a process with
another suite's.

Run: python -m unittest erpnext_enhancements.tests.test_travel_views -v
"""

import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = "erpnext_enhancements.travel_management.doctype.travel_trip.travel_trip"

travel = None
views = None
notifications = None
planner = None
ics = None

EMAIL_STYLE = "erpnext_enhancements.email_style"
NOTIFICATIONS = "erpnext_enhancements.travel_management.notifications"
_installed_fake_email_style = False

#: What the stubs saw: every send, log, job and permission check.
SITE = types.SimpleNamespace()


def _reset_site():
	SITE.trips = {}
	SITE.users = {}  # user id -> employee
	SITE.employees = {}  # employee -> {employee_name, user_id, prefered_email, ...}
	SITE.pois = {}
	SITE.poi_lookups = 0
	SITE.side_effects = []  # sendmail / Notification Log / enqueue
	SITE.allow_side_effects = False
	SITE.permission_checks = []
	SITE.deny = False
	SITE.get_all = {}  # doctype -> rows
	SITE.get_list_calls = []
	SITE.get_list = {}
	SITE.rendered = []
	SITE.employee_lookups = []  # every name / filter frappe.db.get_value("Employee", ...) was given
	SITE.print_formats = {"Trip Sheet"}  # the Print Formats the site has (frappe.db.exists)


class _dict(dict):
	"""frappe._dict: attribute access, None for a missing key."""

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class FakeRow(types.SimpleNamespace):
	"""A child row: any field it was not given reads as None, like an empty column."""

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return None

	def get(self, key, default=None):
		value = getattr(self, key, None)
		return default if value is None else value


class FakeDoc(FakeRow):
	def is_new(self):
		return False

	def check_permission(self, ptype):
		SITE.permission_checks.append(("check_permission", ptype))

	def has_permission(self, ptype):
		return True


def _install_frappe_stub():
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			result = float(value or 0)
		except (TypeError, ValueError):
			result = 0.0
		return round(result, precision) if precision is not None else result

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def getdate(value=None):
		if value is None:
			return date(2026, 9, 26)
		if isinstance(value, datetime):
			return value.date()
		if isinstance(value, date):
			return value
		return datetime.fromisoformat(str(value)[:19]).date()

	def get_datetime(value):
		if isinstance(value, datetime):
			return value
		if isinstance(value, date):
			return datetime(value.year, value.month, value.day)
		return datetime.fromisoformat(str(value))

	def add_days(value, days):
		return getdate(value) + timedelta(days=days)

	def whitelist(*args, **kwargs):
		if args and callable(args[0]) and not kwargs:
			return args[0]
		return lambda fn: fn

	class ValidationError(Exception):
		pass

	class PermissionError_(Exception):
		pass

	def throw(msg, exc=None, title=None, **kwargs):
		raise (exc or ValidationError)(msg)

	def get_value(doctype, name=None, fieldname=None, as_dict=False, **kwargs):
		if doctype == "Employee":
			SITE.employee_lookups.append(name)
			if isinstance(name, dict):
				return SITE.users.get(name.get("user_id"))
			record = SITE.employees.get(name)
			if not record:
				return None
			if isinstance(fieldname, (list, tuple)):
				return _dict({f: record.get(f) for f in fieldname})
			return record.get(fieldname)
		if doctype == "Travel POI":
			SITE.poi_lookups += 1
			record = SITE.pois.get(name)
			if not record:
				return None
			# Only the fields asked for, as frappe answers: a field left out of the list reads
			# None, so a shape that stops asking for one (a place's `notes`) is seen to lose it.
			if isinstance(fieldname, (list, tuple)):
				return _dict({f: record.get(f) for f in fieldname})
			return _dict(record)
		if doctype == "Company":
			return "USD"
		return None

	def get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			record = args[0]
			SITE.side_effects.append(("get_doc", record.get("doctype")))

			def insert(**kw):
				SITE.side_effects.append(("insert", record.get("doctype")))
				if not SITE.allow_side_effects:
					raise AssertionError(f"{record.get('doctype')} inserted on a read path")

			return types.SimpleNamespace(insert=insert)
		return SITE.trips[args[1]]

	def has_permission(doctype=None, ptype="read", doc=None, throw=False, **kwargs):
		SITE.permission_checks.append((doctype, ptype, getattr(doc, "name", doc), throw))
		if SITE.deny:
			if throw:
				raise frappe.PermissionError("No permission")
			return False
		return True

	def sendmail(**kwargs):
		SITE.side_effects.append(("sendmail", kwargs))
		if not SITE.allow_side_effects:
			raise AssertionError("frappe.sendmail called on a read path")

	def enqueue(*args, **kwargs):
		SITE.side_effects.append(("enqueue", args, kwargs))
		raise AssertionError("frappe.enqueue called")

	def render_template(path, context):
		SITE.rendered.append((path, context))
		lines = [line for day in context.get("itinerary_days") or [] for line in day["lines"]]
		return f"<p data-t='{path}'>{context.get('itinerary_url')}</p>" + "".join(
			f"<p>{line}</p>" for line in lines
		)

	def exists(doctype, name=None, *args, **kwargs):
		# Only a string names a record: a dict is a filter, and nothing here is asked one.
		if not isinstance(name, str):
			return None
		if doctype == "Print Format":
			return name if name in SITE.print_formats else None
		if doctype == "Travel Trip":
			return name if name in SITE.trips else None
		return None

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		rows = SITE.get_all.get(doctype, [])
		if pluck:
			return list(rows)
		return [_dict(row) for row in rows]

	def get_list(doctype, **kwargs):
		SITE.get_list_calls.append((doctype, kwargs))
		# v16's query engine checks select permission on the doctype before it builds the
		# query, and raises rather than returning nothing.
		if SITE.deny:
			raise frappe.PermissionError(f"No permission for {doctype}")
		return [_dict(row) for row in SITE.get_list.get(doctype, [])]

	utils.flt = flt
	utils.cint = cint
	utils.getdate = getdate
	utils.get_datetime = get_datetime
	utils.add_days = add_days
	utils.today = lambda: "2026-09-26"
	utils.now_datetime = lambda: datetime(2026, 9, 26, 9, 0, 0)
	utils.get_system_timezone = lambda: "America/Phoenix"
	utils.get_url = lambda path="": f"https://example.com{path or ''}"
	utils.get_url_to_form = lambda doctype, name: f"https://example.com/desk/{doctype}/{name}"

	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _dict
	frappe.whitelist = whitelist
	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.throw = throw
	frappe.session = types.SimpleNamespace(user="bo@example.com")
	frappe.sessions = types.SimpleNamespace(get_csrf_token=lambda: "csrf")
	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		has_column=lambda *a, **k: False,
		get_single_value=lambda *a, **k: None,
		exists=exists,
	)
	frappe.get_doc = get_doc
	frappe.has_permission = has_permission
	frappe.sendmail = sendmail
	frappe.enqueue = enqueue
	frappe.render_template = render_template
	frappe.get_all = get_all
	frappe.get_list = get_list
	frappe.log_error = lambda *a, **k: SITE.side_effects.append(("log_error", k.get("title")))
	frappe.get_traceback = lambda: ""
	frappe.flags = types.SimpleNamespace(in_migrate=False, in_install=False, in_patch=False, in_import=False)
	frappe.local = types.SimpleNamespace(site="test.site")
	frappe.scrub = lambda value: str(value).replace("-", "_").replace(" ", "_").lower()
	frappe.parse_json = json.loads
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def _fake_email_style():
	module = types.ModuleType(EMAIL_STYLE)
	module.wrap = lambda body, title=None, eyebrow=None, **kw: (
		f"<shell title='{title}' eyebrow='{eyebrow}'>{body}</shell>"
	)
	return module


FAKE_EMAIL_STYLE = _fake_email_style()


def setUpModule():
	global travel, views, notifications, planner, ics, _installed_fake_email_style
	_reset_site()
	_install_frappe_stub()
	# The real email_style pulls in print_style and company_contact — far more frappe than
	# this stub offers. The chrome is not under test here; what reaches sendmail is.
	if EMAIL_STYLE not in sys.modules:
		sys.modules[EMAIL_STYLE] = FAKE_EMAIL_STYLE
		_installed_fake_email_style = True
	from erpnext_enhancements.api import travel as t
	from erpnext_enhancements.travel_management import ics as i
	from erpnext_enhancements.travel_management import notifications as n
	from erpnext_enhancements.travel_management import planner as p
	from erpnext_enhancements.travel_management import views as v

	n.email_style = FAKE_EMAIL_STYLE
	travel, views, notifications, planner, ics = t, v, n, p, i


def tearDownModule():
	# Leave no module behind that captured the fake chrome.
	if _installed_fake_email_style:
		sys.modules.pop(EMAIL_STYLE, None)
		sys.modules.pop(NOTIFICATIONS, None)


# --------------------------------------------------------------------------- fixture

#: Amounts that must never reach a non-coordinator. Distinct, so a leak is findable.
SENTINELS = ("211.11", "322.22", "433.33", "544.44", "655.55", "766.66", "877.77", "988.88")

#: Every receipt on the fixture's cost rows is named receipt-*: none may reach any payload.
RECEIPT_MARK = "receipt-"


def make_trip(name="TRIP-1", **overrides):
	"""Ann and Bo share a flight and a room; a whole-crew rental; Cy receives a shipment.

	Money is set everywhere the doctype has it — rows, travelers, the trip's rollups — so
	a payload that copied a row or a doc wholesale would carry it."""
	doc = FakeDoc(
		doctype="Travel Trip",
		name=name,
		purpose="Install",
		status="Booked",
		travel_type="Domestic",
		company="SF",
		start_date="2026-10-05",
		end_date="2026-10-08",
		travel_for_doctype="Project",
		travel_for_name="PRJ-1",
		billable=1,
		total_estimated_cost=988.88,
		total_actual_cost=988.88,
		total_per_diem=877.77,
		travelers=[
			FakeRow(name="T1", employee="EMP-A", employee_name="Ann", is_trip_lead=1, per_diem_amount=877.77),
			FakeRow(
				name="T2", employee="EMP-B", employee_name="Bo", from_date="2026-10-06", advance_amount=766.66
			),
			FakeRow(name="T3", employee="EMP-C", employee_name="Cy", expense_claim="HR-EXP-1"),
		],
		flights=[
			FakeRow(
				name="F1",
				traveler="EMP-A",
				booking_group="g1",
				airline="Southwest",
				flight_number="WN 1",
				departure_airport="PHX",
				arrival_airport="LAS",
				departure_time="2026-10-05 07:15:00",
				arrival_time="2026-10-05 08:20:00",
				booking_reference="PNR-A",
				cost=211.11,
				estimated_cost=211.11,
				paid_by="Company",
				billable=1,
				attachment="/private/files/receipt-f1.pdf",
			),
			FakeRow(
				name="F2",
				traveler="EMP-B",
				booking_group="g1",
				airline="Southwest",
				flight_number="WN 1",
				departure_airport="PHX",
				arrival_airport="LAS",
				departure_time="2026-10-05 07:15:00",
				arrival_time="2026-10-05 08:20:00",
				booking_reference="PNR-B",
				cost=211.11,
				paid_by="Company",
				billable=1,
				expense_claim="HR-EXP-2",
			),
			# Typed on the form, whole crew, no number: the day before the trip starts.
			FakeRow(
				name="F3",
				traveler=None,
				booking_group=None,
				airline="Delta",
				flight_number="DL 9",
				departure_airport="PHX",
				arrival_airport="LAS",
				departure_time="2026-10-04 18:00:00",
				booking_reference=None,
				cost=0,
			),
		],
		accommodations=[
			FakeRow(
				name="R1",
				traveler="EMP-A",
				booking_group="g3",
				hotel_lodging="Hotel One",
				address="1 Main",
				check_in_date="2026-10-05",
				check_out_date="2026-10-08",
				booking_confirmation="H-A",
				cost=322.22,
				paid_by="Employee",
				paid_by_traveler="EMP-A",
				attachment="/private/files/receipt-r1.pdf",
			),
			FakeRow(
				name="R2",
				traveler="EMP-B",
				booking_group="g3",
				hotel_lodging="Hotel One",
				address="1 Main",
				check_in_date="2026-10-05",
				check_out_date="2026-10-08",
				booking_confirmation="H-B",
				cost=322.22,
				paid_by="Employee",
				paid_by_traveler="EMP-A",
			),
		],
		ground_transport=[
			FakeRow(
				name="G1",
				traveler=None,
				booking_group="g4",
				transport_type="Rental/Third Party",
				supplier="Enterprise",
				pickup_location="LAS",
				pickup_datetime="2026-10-05 09:00:00",
				booking_reference="RC-1",
				cost=0,
				paid_by="Company",
				attachment="/private/files/receipt-g1.pdf",
			),
		],
		freight=[
			FakeRow(
				name="S1",
				traveler="EMP-C",
				carrier="Old Dominion",
				tracking_number="PRO-1",
				delivery_from="2026-10-06 10:00:00",
				deliver_to="Site",
				cost=433.33,
				paid_by="Company",
				billable=1,
				attachment="/private/files/receipt-s1.pdf",
			),
		],
		other_costs=[
			FakeRow(
				name="X1",
				cost=544.44,
				paid_by="Employee",
				paid_by_traveler="EMP-B",
				attachment="/private/files/receipt-x1.pdf",
			)
		],
		mileage=[FakeRow(name="M1", distance=655.55, amount=655.55)],
		itinerary=[
			FakeRow(
				name="A1",
				date="2026-10-06",
				time="09:30:00",
				activity_description="Walk the site",
				location="POI-1",
			),
			FakeRow(
				name="A2",
				date="2026-10-07",
				time="13:00:00",
				activity_description="Start-up",
				location="POI-1",
			),
		],
		# The trip's files (Trip Document). Not money: anyone who can open the trip sees
		# them on the views, each person the ones that are theirs or their bookings'.
		documents=[
			FakeRow(name="D1", title="Site map", kind="Site map", file="/files/site-map.png"),
			FakeRow(
				name="D2",
				title="",
				kind="Boarding pass",
				file="/private/files/bo-pass.pdf",
				traveler="EMP-B",
				booking_group="g1",
			),
			FakeRow(
				name="D3",
				title="Hotel One confirmation",
				kind="Booking confirmation",
				file="/private/files/hotel.pdf",
				booking_group="g3",
				booking_label="Hotel One",
			),
			FakeRow(
				name="D4",
				title="Ann's job packet",
				kind="Job packet",
				file="/private/files/ann-packet.pdf",
				traveler="EMP-A",
				traveler_name="Ann",
			),
			FakeRow(
				name="D5",
				title="Rental agreement",
				kind="Rental agreement",
				file="/private/files/rental.pdf",
				booking_group="g4",
			),
			# Its booking was deleted on the form: it reads as a file for the whole trip.
			FakeRow(
				name="D6",
				title="Old booking",
				kind="Other",
				file="/private/files/old.pdf",
				booking_group="gone00000001",
				booking_label="Gone",
			),
			# Nothing to open.
			FakeRow(name="D7", title="No file", kind="Other", file=None),
		],
	)
	for key, value in overrides.items():
		setattr(doc, key, value)
	return doc


def install_site(doc=None):
	_reset_site()
	doc = doc or make_trip()
	SITE.trips[doc.name] = doc
	SITE.users = {"ann@example.com": "EMP-A", "bo@example.com": "EMP-B", "cy@example.com": "EMP-C"}
	SITE.employees = {
		"EMP-A": {"employee_name": "Ann", "user_id": "ann@example.com", "prefered_email": "ann@example.com"},
		"EMP-B": {"employee_name": "Bo", "user_id": "bo@example.com", "prefered_email": "bo@corp.example"},
		# Cy has an Employee record but no address of any kind.
		"EMP-C": {"employee_name": "Cy", "user_id": None},
		"EMP-Z": {"employee_name": "Zed", "user_id": "zed@example.com"},
	}
	SITE.pois = {
		"POI-1": {
			"poi_name": "The site",
			"category": "Customer Site",
			"geolocation": json.dumps(
				{
					"type": "FeatureCollection",
					"features": [{"geometry": {"type": "Point", "coordinates": [-115.1, 36.1]}}],
				}
			),
			"address": None,
		}
	}
	sys.modules["frappe"].session.user = "bo@example.com"
	return doc


# --------------------------------------------------------------------------- the money walk

#: Keys that carry money anywhere in Travel Trip, its rows or a money block.
MONEY_KEYS = frozenset(
	{
		"cost",
		"estimated_cost",
		"paid_by",
		"paid_by_traveler",
		"paid_by_name",
		"billable",
		"expense_claim",
		"total",
		"currency",
		"mileage_rate",
		"distance",
		"claimed",
		"protected",
		"amount",
		"money",
		# A receipt is money (trip files, 2026-09-26): the rows' Receipt field is named
		# "attachment", and shape_itinerary stopped emitting it.
		"attachment",
		"receipt",
	}
)
MONEY_PREFIXES = ("per_diem", "advance", "claim", "total_", "expense_", "estimated_", "mileage")


def money_paths(value, path="$"):
	"""Every place in ``value`` a money key appears. ``money: null`` is the one allowed."""
	found = []
	if isinstance(value, dict):
		for key, child in value.items():
			if key == "money" and child is None:
				continue
			if key in MONEY_KEYS or str(key).startswith(MONEY_PREFIXES):
				found.append(f"{path}.{key}")
			found.extend(money_paths(child, f"{path}.{key}"))
	elif isinstance(value, (list, tuple)):
		for index, child in enumerate(value):
			found.extend(money_paths(child, f"{path}[{index}]"))
	return found


class MoneyAssertions(unittest.TestCase):
	def assertNoMoney(self, payload):
		self.assertEqual(money_paths(payload), [], "money reached a non-coordinator payload")
		text = json.dumps(payload, default=str)
		for amount in SENTINELS:
			self.assertNotIn(amount, text, f"the amount {amount} reached a non-coordinator payload")
		self.assertNoReceipt(payload)

	def assertNoReceipt(self, payload):
		"""No receipt in any payload, a coordinator's included: the views, /itinerary and the
		emails never carry one (the coordinator's money block is cost and who paid)."""
		self.assertEqual(
			[path for path in money_paths(payload) if path.endswith((".attachment", ".receipt"))], []
		)
		self.assertNotIn(RECEIPT_MARK, json.dumps(payload, default=str), "a receipt reached a payload")


class TestMoneyWalk(unittest.TestCase):
	def test_the_walk_finds_money_at_any_depth(self):
		# The walk itself, or every assertNoMoney below passes vacuously.
		self.assertEqual(money_paths({"a": [{"b": {"cost": 1}}]}), ["$.a[0].b.cost"])
		self.assertEqual(money_paths({"per_diem_amount": 1, "money": None}), ["$.per_diem_amount"])
		self.assertEqual(money_paths({"money": {"total": 1}}), ["$.money", "$.money.total"])
		self.assertEqual(money_paths({"days": [{"attachment": "/x"}]}), ["$.days[0].attachment"])

	def test_the_fixture_has_receipts_to_leak(self):
		# Or assertNoReceipt passes vacuously.
		doc = make_trip()
		receipts = [
			row.attachment
			for table in ("flights", "accommodations", "ground_transport", "freight", "other_costs")
			for row in getattr(doc, table)
			if row.attachment
		]
		self.assertEqual(len(receipts), 5)
		self.assertTrue(all(RECEIPT_MARK in url for url in receipts))


def items_of(days):
	return [item for day in days for item in day["items"]]


# --------------------------------------------------------------------------- shape_itinerary


class TestShapeItinerary(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()

	def test_every_booking_and_shipment_carries_its_checklist_group(self):
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			for item in items_of(travel.shape_itinerary(self.doc, viewer)["days"]):
				self.assertIn("group", item, (viewer, item["type"]))
				if item["type"] == "agenda":
					self.assertIsNone(item["group"])
				elif item["type"] == "freight":
					self.assertEqual(item["group"], "row:S1")
				else:
					self.assertTrue(item["group"])
		whole = items_of(travel.shape_itinerary(self.doc)["days"])
		groups = {(i["type"], i["group"]) for i in whole}
		self.assertIn(("flight", "g1"), groups)
		# A row typed on the form has no booking_group: the checklist's own key for it.
		self.assertIn(("flight", "row:F3"), groups)
		self.assertIn(("hotel_checkin", "g3"), groups)
		self.assertIn(("hotel_checkout", "g3"), groups)
		self.assertIn(("ground", "g4"), groups)

	def test_the_group_matches_the_checklist_key(self):
		from erpnext_enhancements.travel_management.completeness import find_gaps

		items = {i["group"] for i in items_of(travel.shape_itinerary(self.doc)["days"])}
		for gap in find_gaps(self.doc):
			if gap.get("group"):
				self.assertIn(gap["group"], items, gap)

	def test_the_whole_crew_view_keeps_each_persons_own_number(self):
		whole = items_of(travel.shape_itinerary(self.doc)["days"])
		flight = next(i for i in whole if i["type"] == "flight" and i["group"] == "g1")
		self.assertEqual(
			flight["members"],
			[
				{"employee": "EMP-A", "employee_name": "Ann", "ref": "PNR-A"},
				{"employee": "EMP-B", "employee_name": "Bo", "ref": "PNR-B"},
			],
		)
		self.assertIs(flight["whole_crew"], False)
		# The keys the emails read are untouched.
		self.assertEqual(flight["booking_reference"], "PNR-A, PNR-B")
		self.assertEqual(flight["travelers"], ["Ann", "Bo"])

		crew_flight = next(i for i in whole if i["group"] == "row:F3")
		self.assertEqual(crew_flight["members"], [{"employee": "", "employee_name": "", "ref": ""}])
		self.assertIs(crew_flight["whole_crew"], True)
		self.assertIsNone(crew_flight["travelers"])

		room = next(i for i in whole if i["type"] == "hotel_checkin")
		self.assertEqual([m["ref"] for m in room["members"]], ["H-A", "H-B"])
		ride = next(i for i in whole if i["type"] == "ground")
		self.assertIs(ride["whole_crew"], True)

		shipment = next(i for i in whole if i["type"] == "freight")
		self.assertEqual(shipment["members"], [])
		self.assertIs(shipment["whole_crew"], False)
		self.assertEqual(shipment["received_by"], "Cy")

	def test_a_persons_own_view_is_unchanged(self):
		days = travel.shape_itinerary(self.doc, "EMP-B")["days"]
		for item in items_of(days):
			self.assertNotIn("members", item)
			self.assertNotIn("whole_crew", item)
		flight = next(i for i in items_of(days) if i["group"] == "g1")
		self.assertEqual(flight["booking_reference"], "PNR-B")
		self.assertIsNone(flight["travelers"])
		room = next(i for i in items_of(days) if i["type"] == "hotel_checkin")
		self.assertEqual(room["booking_confirmation"], "H-B")
		# Cy's shipment is Cy's.
		self.assertNotIn("freight", {i["type"] for i in items_of(days)})
		self.assertIn(
			"freight", {i["type"] for i in items_of(travel.shape_itinerary(self.doc, "EMP-C")["days"])}
		)

	def test_the_keys_the_emails_read_are_all_still_there(self):
		whole = items_of(travel.shape_itinerary(self.doc)["days"])
		required = {
			"flight": {
				"airline",
				"flight_number",
				"departure_airport",
				"departure_time",
				"arrival_airport",
				"arrival_time",
				"booking_reference",
				"travelers",
			},
			"hotel_checkin": {"time", "hotel", "address", "booking_confirmation", "travelers"},
			"ground": {
				"transport_type",
				"provider",
				"pickup_location",
				"dropoff_location",
				"pickup_datetime",
				"arrival_datetime",
				"return_datetime",
				"cargo",
				"booking_reference",
				"travelers",
			},
			"freight": {
				"carrier",
				"tracking_number",
				"contents",
				"ship_from",
				"deliver_to",
				"pickup_from",
				"pickup_to",
				"delivery_from",
				"delivery_to",
				"received_by",
			},
			"agenda": {
				"time",
				"end_time",
				"activity",
				"related_party_doctype",
				"related_party",
				"poi",
				"visit_notes",
			},
		}
		for item in whole:
			if item["type"] in required:
				self.assertLessEqual(
					required[item["type"]] | {"type", "date", "sort_time"}, set(item), item["type"]
				)
			# Changed on purpose with trip files (2026-09-26): "attachment" used to be required
			# here. It is the row's Receipt, and a receipt is money, so no item carries it now;
			# no email or template ever read it. Booking paperwork is "documents".
			self.assertNotIn("attachment", item)
			if item["type"] != "agenda":
				self.assertIn("documents", item)

	def test_one_poi_cache_serves_several_people(self):
		cache = {}
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			travel.shape_itinerary(self.doc, viewer, poi_cache=cache)
		self.assertEqual(SITE.poi_lookups, 1)
		self.assertIn("POI-1", cache)

	def test_no_money_in_any_view(self):
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			self.assertNoMoney(travel.shape_itinerary(self.doc, viewer))

	def test_a_room_guest_is_marked_with_their_own_nights(self):
		# #1133's guests: in the whole-crew view each person on a room says whether they stay
		# free, and a guest fitted to fewer nights says which, so the Overview can read
		# "Bo (guest, Tue–Wed)".
		room = next(
			i for i in items_of(travel.shape_itinerary(self.doc)["days"]) if i["type"] == "hotel_checkin"
		)
		self.assertEqual([m["guest"] for m in room["members"]], [False, False])
		self.assertNotIn("check_in_date", room["members"][1])

		self.doc.accommodations[1].guest = 1
		self.doc.accommodations[1].check_in_date = "2026-10-06"
		self.doc.accommodations[1].check_out_date = "2026-10-07"
		whole = items_of(travel.shape_itinerary(self.doc)["days"])
		room = next(i for i in whole if i["type"] == "hotel_checkin" and i["group"] == "g3")
		self.assertEqual(room["date"], "2026-10-05")  # the room's dates: its first row's
		self.assertEqual(
			room["members"],
			[
				{"employee": "EMP-A", "employee_name": "Ann", "ref": "H-A", "guest": False},
				{
					"employee": "EMP-B",
					"employee_name": "Bo",
					"ref": "H-B",
					"guest": True,
					"check_in_date": "2026-10-06",
					"check_out_date": "2026-10-07",
				},
			],
		)
		# Only hotels have guests, and a person's own view has no members at all.
		flight = next(i for i in whole if i["group"] == "g1")
		self.assertNotIn("guest", flight["members"][0])
		for item in items_of(travel.shape_itinerary(self.doc, "EMP-B")["days"]):
			self.assertNotIn("members", item)
		self.assertNoMoney(travel.shape_itinerary(self.doc))


class TestPlaceNotes(MoneyAssertions):
	"""A stop's place carries its own Notes (Travel POI ``notes``: parking, the gate code, site
	access) since v1.553.0, for ``/itinerary`` to show the crew. The travel guidelines said they
	were on everyone's mobile itinerary; ``shape_itinerary`` never read the field, so they were
	not. Free text about a place, so no money; and Employee reads every Travel POI, so nobody
	who can open the trip learns anything new."""

	NOTES = "Park at the north gate.\r\nGate code 4471#\n\nSign in at the trailer."
	#: Leave the fixture's POI as it is: its record has no `notes` key at all.
	UNTOUCHED = object()

	def setUp(self):
		self.doc = install_site()

	def stops(self, viewer=None, notes=NOTES):
		if notes is not self.UNTOUCHED:
			SITE.pois["POI-1"]["notes"] = notes
		shaped = travel.shape_itinerary(self.doc, viewer)
		return [item for item in items_of(shaped["days"]) if item["type"] == "agenda"]

	def test_every_stop_at_the_place_carries_its_notes_in_every_view(self):
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			stops = self.stops(viewer)
			self.assertEqual(len(stops), 2, viewer)
			for stop in stops:
				self.assertEqual(
					stop["poi"]["notes"],
					"Park at the north gate.\nGate code 4471#\n\nSign in at the trailer.",
					viewer,
				)

	def test_line_breaks_are_kept_and_only_the_ends_trimmed(self):
		# Windows line endings become plain newlines; a blank line inside stays.
		notes = self.stops(notes="\n  Gate B\r\r\nCode 12  \n")[0]["poi"]["notes"]
		self.assertEqual(notes, "Gate B\n\nCode 12")

	def test_blank_notes_are_none(self):
		for blank in ("", "   ", "\r\n\t \n", None):
			self.doc = install_site()
			stop = self.stops(notes=blank)[0]
			self.assertIn("notes", stop["poi"], repr(blank))
			self.assertIsNone(stop["poi"]["notes"], repr(blank))
		# A POI that has no `notes` value at all (the stub's record never had the key).
		self.doc = install_site()
		self.assertIsNone(self.stops(notes=self.UNTOUCHED)[0]["poi"]["notes"])

	def test_notes_are_sent_as_typed_and_the_page_draws_them_as_text(self):
		# `notes` is a plain Text field, not a Text Editor: there is no markup to strip, and
		# nothing is unescaped or parsed on the way. /itinerary draws it with textContent
		# (scripts/test_web_flow_history.js checks that a tag in it is drawn as text).
		typed = "<script>alert(1)</script> <b>Gate</b> &amp; code"
		self.assertEqual(self.stops(notes=typed)[0]["poi"]["notes"], typed)

	def test_the_field_is_asked_for_by_name(self):
		# The stub answers only the fields asked for, as frappe does: a shape that stopped
		# asking for `notes` reads None here, and the first test fails.
		SITE.pois["POI-1"]["notes"] = "Gate B"
		original = sys.modules["frappe"].db.get_value
		asked = []

		def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "Travel POI":
				asked.append(tuple(fieldname) if isinstance(fieldname, (list, tuple)) else fieldname)
			return original(doctype, name, fieldname, *args, **kwargs)

		with mock.patch.object(sys.modules["frappe"].db, "get_value", get_value):
			travel.shape_itinerary(self.doc)
		self.assertTrue(asked)
		self.assertTrue(all("notes" in fields for fields in asked), asked)

	def test_one_lookup_serves_every_person_notes_included(self):
		SITE.pois["POI-1"]["notes"] = "Gate B"
		cache = {}
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			stops = [
				i
				for i in items_of(travel.shape_itinerary(self.doc, viewer, poi_cache=cache)["days"])
				if i["type"] == "agenda"
			]
			self.assertEqual({s["poi"]["notes"] for s in stops}, {"Gate B"})
		self.assertEqual(SITE.poi_lookups, 1)

	def test_the_notes_reach_itinerary_and_the_views_with_no_money(self):
		SITE.pois["POI-1"]["notes"] = self.NOTES
		sys.modules["frappe"].session.user = "bo@example.com"
		result = travel.get_trip_itinerary("TRIP-1", None)
		stops = [i for i in items_of(result["days"]) if i["type"] == "agenda"]
		self.assertTrue(stops)
		self.assertTrue(all(s["poi"]["notes"].startswith("Park at the north gate.") for s in stops))
		self.assertNoMoney(result)

		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			payload = travel.get_trip_views("TRIP-1")
		# View as draws a person's own itinerary from `people`, as their phone does.
		for employee, days in payload["people"].items():
			stops = [i for i in items_of(days) if i["type"] == "agenda"]
			self.assertTrue(all(s["poi"]["notes"] for s in stops), employee)
		self.assertNoMoney(payload)


class TestTripFiles(MoneyAssertions):
	"""Trip files on the views and /itinerary. All but money: everyone who can open the trip
	sees every booking's files on the whole-crew view; one person's view shows the files that
	are theirs, their bookings', and the whole trip's; and no receipt anywhere."""

	def setUp(self):
		self.doc = install_site()

	def names(self, documents):
		return [d["name"] for d in documents]

	def test_the_whole_crew_sees_every_file_trip_wide_first_then_in_itinerary_order(self):
		shaped = travel.shape_itinerary(self.doc)
		# D1, D4 and D6 (its booking is gone) are the whole trip's, in row order; then the
		# bookings as the days come: the flight (7:15), the rental (9:00), the hotel (evening).
		# D7 has no file.
		self.assertEqual(self.names(shaped["documents"]), ["D1", "D4", "D6", "D2", "D5", "D3"])
		by_name = {d["name"]: d for d in shaped["documents"]}
		self.assertEqual(
			by_name["D2"],
			{
				"name": "D2",
				"title": "bo-pass.pdf",
				"kind": "Boarding pass",
				"url": "/private/files/bo-pass.pdf",
				"file_name": "bo-pass.pdf",
				"is_image": False,
				"for_name": "Bo",
				"for_employee": "EMP-B",
				"group": "g1",
				"booking_label": "Southwest WN 1",
				# Who is on the booking and when: what tells two same-named bookings apart.
				"booking_dates": ["2026-10-05", None],
				"booking_people": ["Ann", "Bo"],
			},
		)
		self.assertIs(by_name["D1"]["is_image"], True)
		self.assertNotIn("booking_people", by_name["D1"], "a file for the whole trip is on no booking")
		self.assertEqual(
			(by_name["D1"]["for_name"], by_name["D1"]["group"], by_name["D1"]["booking_label"]),
			(None, None, None),
		)
		self.assertEqual((by_name["D4"]["for_name"], by_name["D4"]["group"]), ("Ann", None))
		self.assertEqual((by_name["D6"]["group"], by_name["D6"]["booking_label"]), (None, None))
		self.assertEqual(by_name["D3"]["booking_label"], "Hotel One")
		self.assertEqual(by_name["D5"]["booking_label"], "Enterprise")

	def test_each_person_sees_their_own_their_bookings_and_the_whole_trips(self):
		expected = {
			# Ann: not Bo's boarding pass, but her job packet, her room and the crew's rental.
			"EMP-A": ["D1", "D4", "D6", "D5", "D3"],
			"EMP-B": ["D1", "D6", "D2", "D5", "D3"],
			# Cy is on no flight and in no room: the whole trip's files and the crew's rental.
			"EMP-C": ["D1", "D6", "D5"],
		}
		for employee, names in expected.items():
			shaped = travel.shape_itinerary(self.doc, employee)
			self.assertEqual(self.names(shaped["documents"]), names, employee)
			self.assertNoMoney(shaped)

	def test_each_booking_carries_its_own_files(self):
		def files(viewer, kind, group):
			items = items_of(travel.shape_itinerary(self.doc, viewer)["days"])
			return [self.names(i["documents"]) for i in items if i["type"] == kind and i["group"] == group]

		self.assertEqual(files(None, "flight", "g1"), [["D2"]])
		self.assertEqual(files(None, "flight", "row:F3"), [[]])
		self.assertEqual(files(None, "hotel_checkin", "g3"), [["D3"]])
		self.assertEqual(files(None, "hotel_checkout", "g3"), [["D3"]])
		self.assertEqual(files(None, "ground", "g4"), [["D5"]])
		self.assertEqual(files(None, "freight", "row:S1"), [[]])
		# Bo's boarding pass is on Bo's flight, not on Ann's.
		self.assertEqual(files("EMP-A", "flight", "g1"), [[]])
		self.assertEqual(files("EMP-B", "flight", "g1"), [["D2"]])
		agenda = [i for i in items_of(travel.shape_itinerary(self.doc)["days"]) if i["type"] == "agenda"]
		self.assertTrue(agenda)
		self.assertTrue(all("documents" not in i for i in agenda))

	def test_a_file_for_everyone_on_a_booking_follows_who_is_on_it(self):
		# The rental is a whole-crew row, so its file is everyone's; pin the rental to Ann and
		# its file is hers alone.
		self.doc.ground_transport[0].traveler = "EMP-A"
		self.assertIn("D5", self.names(travel.shape_itinerary(self.doc, "EMP-A")["documents"]))
		self.assertNotIn("D5", self.names(travel.shape_itinerary(self.doc, "EMP-B")["documents"]))
		self.assertIn("D5", self.names(travel.shape_itinerary(self.doc)["documents"]))

	def test_a_shipments_files_follow_its_id(self):
		self.doc.freight[0].booking_group = "ship00000001"
		self.doc.documents.append(
			FakeRow(
				name="D8", kind="Bill of lading", file="/private/files/bol.pdf", booking_group="ship00000001"
			)
		)
		items = items_of(travel.shape_itinerary(self.doc)["days"])
		shipment = next(i for i in items if i["type"] == "freight")
		self.assertEqual(shipment["group"], "ship00000001")
		self.assertEqual(self.names(shipment["documents"]), ["D8"])
		self.assertEqual(shipment["documents"][0]["booking_label"], "Old Dominion PRO-1")
		# A file for nobody in particular on a shipment is its receiver's: Cy's, not Ann's.
		self.assertIn("D8", self.names(travel.shape_itinerary(self.doc, "EMP-C")["documents"]))
		self.assertNotIn("D8", self.names(travel.shape_itinerary(self.doc, "EMP-A")["documents"]))

	def test_a_file_on_a_booking_with_no_name_yet_is_still_on_that_booking(self):
		# /itinerary heads a booking's files with its label; a flight with no airline or number
		# yet must not read as blank (the whole trip's), so it is called what it is.
		for row in self.doc.flights:
			if row.booking_group == "g1":
				row.airline = ""
				row.flight_number = ""
		by_name = {d["name"]: d for d in travel.shape_itinerary(self.doc)["documents"]}
		self.assertEqual((by_name["D2"]["group"], by_name["D2"]["booking_label"]), ("g1", "Flight"))

	def test_a_trip_file_that_is_a_receipt_reaches_no_payload(self):
		# A row added on the form's Documents tab is checked by nothing on the way in, and
		# frappe reuses a file_url for the same content uploaded again, so a Trip Document can
		# name a cost's Receipt. What is SENT keeps the rule: every reader leaves it out.
		self.doc.documents.append(
			FakeRow(
				name="D9",
				title="Flight confirmation",
				kind="Booking confirmation",
				file="/private/files/receipt-f1.pdf",
				booking_group="g1",
			)
		)
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			shaped = travel.shape_itinerary(self.doc, viewer)
			self.assertNotIn("D9", self.names(shaped["documents"]), viewer)
			self.assertNoReceipt(shaped)
		for coordinator in (False, True):
			with mock.patch.object(travel, "_is_coordinator", return_value=coordinator):
				payload = travel.get_trip_views("TRIP-1")
				self.assertNoReceipt(payload)
				# ...nor does it count as the flight's paperwork: Ann still has none of her own.
				paperwork = [g for g in payload["gaps"] if g["check"] == "documents" and g["group"] == "g1"]
				self.assertEqual([g["employee_names"] for g in paperwork], [["Ann"]])
		for as_employee in (None, "crew", "EMP-A"):
			self.assertNoReceipt(travel.get_trip_itinerary("TRIP-1", as_employee))
		self.assertNotIn("D9", [d["name"] for d in planner._documents_state(self.doc)])

	def test_two_bookings_with_one_name_carry_who_is_on_each_and_when(self):
		# One adult to a room (the travel policy): four rooms at one hotel are four bookings
		# called the same. /itinerary's Documents screen tells them apart by these.
		ann, bo = self.doc.accommodations
		bo.booking_group = "g5"
		bo.check_in_date = "2026-10-06"
		self.doc.documents.append(
			FakeRow(name="D8", title="Hotel One confirmation", kind="Booking confirmation", file="/private/files/hotel-bo.pdf", booking_group="g5")
		)
		by_name = {d["name"]: d for d in travel.shape_itinerary(self.doc)["documents"]}
		self.assertEqual(
			[(by_name[n]["booking_label"], by_name[n]["booking_people"], by_name[n]["booking_dates"]) for n in ("D3", "D8")],
			[
				("Hotel One", ["Ann"], ["2026-10-05", "2026-10-08"]),
				("Hotel One", ["Bo"], ["2026-10-06", "2026-10-08"]),
			],
		)
		# A booking the whole crew is on names nobody; a guest's own nights are not the room's.
		self.assertEqual((by_name["D5"]["booking_people"], by_name["D5"]["booking_dates"]), ([], ["2026-10-05", None]))
		ann.guest = 1
		ann.check_in_date = "2026-10-07"
		bo.booking_group = "g3"
		by_name = {d["name"]: d for d in travel.shape_itinerary(self.doc)["documents"]}
		self.assertEqual(by_name["D3"]["booking_dates"], ["2026-10-06", "2026-10-08"])
		# One person's own list has the dates, and does not name the others on the booking.
		mine = {d["name"]: d for d in travel.shape_itinerary(self.doc, "EMP-B")["documents"]}
		self.assertEqual(mine["D3"]["booking_dates"], ["2026-10-06", "2026-10-08"])
		self.assertNotIn("booking_people", mine["D3"])
		self.assertNoMoney(travel.shape_itinerary(self.doc, "EMP-B"))

	def test_no_receipt_in_any_payload(self):
		for viewer in (None, "EMP-A", "EMP-B", "EMP-C"):
			self.assertNoReceipt(travel.shape_itinerary(self.doc, viewer))
		for coordinator in (False, True):
			with mock.patch.object(travel, "_is_coordinator", return_value=coordinator):
				self.assertNoReceipt(travel.get_trip_views("TRIP-1"))
				self.assertNoReceipt(travel.preview_itinerary_email("TRIP-1", "EMP-A"))
		for as_employee in (None, "crew", "EMP-C"):
			self.assertNoReceipt(travel.get_trip_itinerary("TRIP-1", as_employee))
		# Nor is the Receipt field read at all (the docstrings may name it).
		code = "".join(inspect.getsource(travel.shape_itinerary).split('"""')[0::2])
		self.assertNotIn("attachment", code)

	def test_the_real_file_names_are_the_last_path_segment(self):
		from erpnext_enhancements.travel_management import completeness

		self.assertEqual(
			completeness.file_name_of("/private/files/Boarding Pass.JPG?fid=1"), "Boarding Pass.JPG"
		)
		self.assertEqual(completeness.file_name_of(None), "")
		for url, image in (
			("/files/a.png", True),
			("/files/a.JPEG", True),
			("/files/a.webp", True),
			("/files/a.heic", True),
			("/files/a.gif", True),
			("/files/a.pdf", False),
			("/files/png", False),
			("", False),
		):
			self.assertIs(completeness.is_image_file(url), image, url)


# --------------------------------------------------------------------------- build_trip_views


class TestBuildTripViews(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()

	def build(self, is_coordinator, doc=None):
		return views.build_trip_views(
			doc or self.doc,
			travel.shape_itinerary,
			is_coordinator=is_coordinator,
			viewer_employee="EMP-B",
			currency="USD",
		)

	def test_every_day_of_the_trip_plus_a_booking_outside_it(self):
		payload = self.build(False)
		self.assertEqual(
			payload["days"], ["2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
		)
		self.assertEqual(payload["start_date"], "2026-10-05")
		self.assertEqual(payload["end_date"], "2026-10-08")

	def test_a_mistyped_end_date_is_capped(self):
		doc = make_trip(end_date="2062-10-08")
		days = self.build(False, doc)["days"]
		# 120 days from the start, plus the flight the day before it.
		self.assertEqual(len(days), views.MAX_DAYS + 1)
		self.assertEqual(days[1], "2026-10-05")
		self.assertEqual(days[-1], (date(2026, 10, 5) + timedelta(days=views.MAX_DAYS - 1)).isoformat())

	def test_trip_days_edges(self):
		self.assertEqual(views.trip_days("2026-10-05", "2026-10-05"), ["2026-10-05"])
		self.assertEqual(views.trip_days(None, None, ["2026-10-02", "None", ""]), ["2026-10-02"])
		self.assertEqual(
			views.trip_days(date(2026, 10, 5), datetime(2026, 10, 6, 8)), ["2026-10-05", "2026-10-06"]
		)

	def test_crew_dates_fall_back_to_the_trip(self):
		crew = {c["employee"]: c for c in self.build(False)["crew"]}
		self.assertEqual(list(crew), ["EMP-A", "EMP-B", "EMP-C"])
		self.assertEqual((crew["EMP-A"]["from_date"], crew["EMP-A"]["to_date"]), ("2026-10-05", "2026-10-08"))
		self.assertEqual((crew["EMP-B"]["from_date"], crew["EMP-B"]["to_date"]), ("2026-10-06", "2026-10-08"))
		self.assertEqual(crew["EMP-A"]["is_trip_lead"], 1)
		self.assertEqual(crew["EMP-B"]["is_trip_lead"], 0)
		self.assertEqual(crew["EMP-C"]["employee_name"], "Cy")
		self.assertEqual(
			set(crew["EMP-A"]), {"employee", "employee_name", "from_date", "to_date", "is_trip_lead"}
		)

	def test_every_crew_member_has_their_own_itinerary(self):
		payload = self.build(False)
		self.assertEqual(set(payload["people"]), {"EMP-A", "EMP-B", "EMP-C"})
		for employee, days in payload["people"].items():
			self.assertEqual(days, travel.shape_itinerary(self.doc, employee)["days"])
		self.assertEqual(payload["whole"], travel.shape_itinerary(self.doc)["days"])
		self.assertEqual(payload["itinerary_url"], "/itinerary?trip=TRIP-1")
		self.assertEqual(payload["viewer_employee"], "EMP-B")
		self.assertEqual(payload["travel_for"], "PRJ-1")

	def test_a_crew_member_gets_no_money_and_no_cost_gaps(self):
		payload = self.build(False)
		self.assertIsNone(payload["money"])
		self.assertIs(payload["is_coordinator"], False)
		self.assertNotIn("cost", {g["check"] for g in payload["gaps"]})
		# The other checks are still there: the whole-crew flight has no number.
		self.assertIn("confirmation", {g["check"] for g in payload["gaps"]})
		self.assertNoMoney(payload)

	def test_a_coordinator_gets_money_keyed_like_the_items(self):
		payload = self.build(True)
		self.assertIs(payload["is_coordinator"], True)
		self.assertIn("cost", {g["check"] for g in payload["gaps"]})
		money = payload["money"]
		self.assertEqual(money["currency"], "USD")
		groups = money["groups"]
		self.assertEqual(set(groups), {"g1", "row:F3", "g3", "g4", "row:S1"})
		item_groups = {i["group"] for i in items_of(payload["whole"]) if i["type"] != "agenda"}
		self.assertEqual(set(groups), item_groups)
		self.assertEqual(groups["g1"], {"cost": 422.22, "paid_by": "Company", "paid_by_name": None})
		self.assertEqual(groups["g3"], {"cost": 644.44, "paid_by": "Employee", "paid_by_name": "Ann"})
		self.assertEqual(groups["row:S1"]["cost"], 433.33)
		self.assertEqual(groups["row:F3"], {"cost": 0.0, "paid_by": None, "paid_by_name": None})
		# Bookings plus freight, as the Review step's "Booked so far".
		self.assertEqual(money["total"], round(422.22 + 644.44 + 433.33, 2))

	def test_a_shipments_money_is_keyed_by_its_id_like_its_item(self):
		# Shipments gained a booking_group with trip files; the money block, the items and the
		# checklist must all key one by it (was: always row:<name>).
		doc = make_trip()
		doc.freight[0].booking_group = "ship00000001"
		payload = self.build(True, doc)
		groups = payload["money"]["groups"]
		self.assertIn("ship00000001", groups)
		self.assertNotIn("row:S1", groups)
		self.assertEqual(groups["ship00000001"]["cost"], 433.33)
		item_groups = {i["group"] for i in items_of(payload["whole"]) if i["type"] != "agenda"}
		self.assertEqual(set(groups), item_groups)
		self.assertIn("ship00000001", {g.get("group") for g in payload["gaps"]})
		self.assertNoReceipt(payload)

	def test_the_payload_carries_the_trips_files(self):
		payload = self.build(False)
		self.assertEqual(payload["documents"], travel.shape_itinerary(self.doc)["documents"])
		self.assertEqual([d["name"] for d in payload["documents"]], ["D1", "D4", "D6", "D2", "D5", "D3"])
		self.assertEqual(set(payload["people_documents"]), {"EMP-A", "EMP-B", "EMP-C"})
		for employee, documents in payload["people_documents"].items():
			self.assertEqual(documents, travel.shape_itinerary(self.doc, employee)["documents"])
		# The files are not money: a crew member gets them all.
		self.assertEqual(self.build(True)["documents"], payload["documents"])
		self.assertNoMoney(payload)

	def test_paperwork_gaps_reach_a_crew_member(self):
		payload = self.build(False)
		paperwork = [g for g in payload["gaps"] if g["check"] == "documents"]
		# Ann has her PNR and no boarding pass (Bo's is his alone); the shipment has its
		# tracking number and no bill of lading. The room and the rental have theirs.
		self.assertEqual(
			[(g["group"], g["employee_names"]) for g in paperwork], [("g1", ["Ann"]), ("row:S1", [])]
		)

	def test_each_payer_is_named(self):
		doc = make_trip()
		doc.accommodations[1].paid_by_traveler = "EMP-B"
		groups = self.build(True, doc)["money"]["groups"]
		self.assertEqual(groups["g3"]["paid_by_name"], "Ann, Bo")

	def test_bookings_are_counted_like_the_review_steps_cards(self):
		# A room typed on the form with no dates yet has no item on any day. The Overview used
		# to count rooms from the items and said "Rooms 1" beside Review's "Rooms 2".
		doc = make_trip()
		doc.accommodations.append(
			FakeRow(name="R3", traveler="EMP-C", booking_group="g7", hotel_lodging="Half Booked Inn")
		)
		payload = self.build(False, doc)
		self.assertEqual(
			payload["bookings"], {"flights": 2, "accommodations": 2, "ground_transport": 1, "freight": 1}
		)
		self.assertNotIn("g7", {i["group"] for i in items_of(payload["whole"])})
		self.assertIn("g7", {g.get("group") for g in payload["gaps"] if g.get("kind") == "dates"})
		self.assertNoMoney(payload)

	def test_the_itinerary_link_is_encoded(self):
		self.assertEqual(views.itinerary_path("TRIP 1&x=2"), "/itinerary?trip=TRIP%201%26x%3D2")

	def test_the_money_block_is_built_only_for_a_coordinator(self):
		with mock.patch.object(views, "build_money", side_effect=AssertionError("built for a crew member")):
			self.build(False)
		source = inspect.getsource(views.build_trip_views)
		self.assertIn("build_money(doc, currency) if is_coordinator else None", source)


# --------------------------------------------------------------------------- endpoints


class TestGetTripViews(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()

	def test_read_permission_is_checked(self):
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			travel.get_trip_views("TRIP-1")
		self.assertIn(("Travel Trip", "read", "TRIP-1", True), SITE.permission_checks)
		SITE.deny = True
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			with self.assertRaises(sys.modules["frappe"].PermissionError):
				travel.get_trip_views("TRIP-1")

	def test_a_crew_member_sees_everyone_but_no_money(self):
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			payload = travel.get_trip_views("TRIP-1")
		self.assertEqual(payload["viewer_employee"], "EMP-B")
		self.assertEqual(set(payload["people"]), {"EMP-A", "EMP-B", "EMP-C"})
		# Everyone's confirmation numbers are there: all but money.
		refs = {m["ref"] for i in items_of(payload["whole"]) for m in i.get("members") or []}
		self.assertLessEqual({"PNR-A", "PNR-B", "H-A", "H-B", "RC-1"}, refs)
		self.assertNoMoney(payload)

	def test_a_coordinator_gets_the_money(self):
		with mock.patch.object(travel, "_is_coordinator", return_value=True):
			payload = travel.get_trip_views("TRIP-1")
		self.assertEqual(payload["money"]["currency"], "USD")
		self.assertIn("g1", payload["money"]["groups"])


class TestTheCoordinatorGate(MoneyAssertions):
	"""Every other test here patches ``_is_coordinator``. With it wired to True for everyone,
	the suite still passed, so the one decision that puts money on the new views had no test.
	These run it for real, down to the Travel Trip controller's own
	``user_is_travel_coordinator`` and ``TRAVEL_COORDINATOR_ROLES``. The only stand-ins are
	``frappe.get_roles`` and the two imports the controller module needs that the stub lacks
	(``frappe.model.document``, ``frappe.utils.date_diff``)."""

	ROLES = {
		"bo@example.com": ["Employee"],
		"ann@example.com": ["Employee", "Projects User", "Accounts User"],
		"hr@example.com": ["Employee", "HR Manager"],
		"tc@example.com": ["Travel Coordinator"],
		"sm@example.com": ["System Manager"],
	}

	def setUp(self):
		self.doc = install_site()
		frappe = sys.modules["frappe"]
		document = types.ModuleType("frappe.model.document")
		document.Document = type("Document", (), {})
		model = types.ModuleType("frappe.model")
		model.document = document
		for patcher in (
			# patch.dict puts sys.modules back as it found it, so the controller imported here
			# is dropped again after each test and no other test sees these stand-ins.
			mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}),
			mock.patch.object(frappe, "get_roles", lambda user=None: self.ROLES.get(user, []), create=True),
			mock.patch.object(frappe.utils, "date_diff", lambda a, b: 0, create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		sys.modules.pop(CONTROLLER, None)

	def as_user(self, user):
		sys.modules["frappe"].session.user = user

	def test_who_sees_money(self):
		for user, coordinator in (
			("bo@example.com", False),
			("ann@example.com", False),
			("nobody@example.com", False),
			("Administrator", True),
			("hr@example.com", True),
			("tc@example.com", True),
			("sm@example.com", True),
		):
			self.as_user(user)
			self.assertIs(travel._is_coordinator(), coordinator, user)

	def test_a_crew_member_gets_no_money_through_the_real_gate(self):
		self.as_user("bo@example.com")
		payload = travel.get_trip_views("TRIP-1")
		self.assertIs(payload["is_coordinator"], False)
		self.assertIsNone(payload["money"])
		self.assertNoMoney(payload)
		preview = travel.preview_itinerary_email("TRIP-1", "EMP-A")
		self.assertIsNone(preview["to_email"])
		self.assertNoMoney(preview)

	def test_a_coordinator_gets_money_through_the_real_gate(self):
		self.as_user("tc@example.com")
		payload = travel.get_trip_views("TRIP-1")
		self.assertIs(payload["is_coordinator"], True)
		self.assertEqual(payload["money"]["groups"]["g1"]["cost"], 422.22)
		self.assertEqual(travel.preview_itinerary_email("TRIP-1", "EMP-B")["to_email"], "bo@corp.example")


class TestTheControllersFileRules(unittest.TestCase):
	"""What the Travel Trip controller enforces on every save, the form's included — Plan a
	Trip's own checks (``planner.merge_documents``) run on its saves only. The real
	controller, imported with the same stand-ins as ``TestTheCoordinatorGate``."""

	def setUp(self):
		install_site()
		frappe = sys.modules["frappe"]
		document = types.ModuleType("frappe.model.document")
		document.Document = type(
			"Document", (), {"get": lambda self, key, default=None: getattr(self, key, default)}
		)
		model = types.ModuleType("frappe.model")
		model.document = document
		for patcher in (
			mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}),
			mock.patch.object(frappe, "get_roles", lambda user=None: [], create=True),
			mock.patch.object(frappe.utils, "date_diff", lambda a, b: 0, create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		sys.modules.pop(CONTROLLER, None)
		import importlib

		self.controller = importlib.import_module(CONTROLLER)

	def trip(self, **tables):
		doc = self.controller.TravelTrip()
		for table in ("flights", "accommodations", "ground_transport", "freight", "other_costs", "documents"):
			setattr(doc, table, tables.get(table, []))
		doc.meta = types.SimpleNamespace(get_label=lambda fieldname: fieldname.replace("_", " ").title())
		return doc

	def test_a_receipt_is_never_a_trip_file_in_either_order(self):
		# Paperwork first and the same PDF later as the cost's Receipt, or the other way round:
		# frappe reuses the file_url either way, so both end as this one state.
		url = "/private/files/wn1.pdf"
		doc = self.trip(
			flights=[FakeRow(name="F1", idx=1, attachment=url)],
			documents=[FakeRow(name="D1", idx=2, title="WN1 confirmation", file=url)],
		)
		with self.assertRaises(sys.modules["frappe"].ValidationError) as refused:
			doc._validate_trip_files()
		self.assertIn("Trip Documents row 2 (WN1 confirmation) is also the Receipt on Flights row 1", str(refused.exception))
		# Any other file is fine, and so is a trip with no files at all.
		doc.documents = [FakeRow(name="D1", idx=1, title="Pass", file="/private/files/pass.png")]
		doc._validate_trip_files()
		self.trip()._validate_trip_files()

	def test_a_shipment_duplicated_on_the_form_gets_its_own_id_back(self):
		original = FakeRow(name="FR1", idx=1, booking_group="aaaaaaaaaaaa")
		copy = FakeRow(name="FR2", idx=2, booking_group="aaaaaaaaaaaa")
		doc = self.trip(freight=[original, copy])
		doc._validate_trip_files()
		self.assertEqual((original.booking_group, copy.booking_group), ("aaaaaaaaaaaa", None))

	def test_validate_runs_it(self):
		source = inspect.getsource(self.controller.TravelTrip.validate)
		self.assertIn("self._validate_trip_files()", source)


class TestDeletingATripThatWasCopied(unittest.TestCase):
	"""A copy names the trip it came from (``copied_from``, v1.550.0), a Link to Travel Trip. frappe's
	``delete_doc`` runs ``on_trash`` and then refuses to delete anything another record links to
	(``check_if_doc_is_linked``, frappe v16 ``model/delete_doc.py``) — so without ``on_trash``
	clearing it, a trip anyone had ever copied could never be deleted again. The real
	controller's ``on_trash``, with the same stand-ins as ``TestTheControllersFileRules``."""

	def setUp(self):
		TestTheControllersFileRules.setUp(self)
		self.writes = []
		frappe = sys.modules["frappe"]

		def set_value(doctype, name, field, value=None, *a, **kw):
			self.writes.append((doctype, name, field, value, kw))

		for patcher in (
			mock.patch.object(frappe.db, "set_value", set_value, create=True),
			mock.patch.object(frappe, "get_all", lambda *a, **k: self.claims, create=True),
			mock.patch.object(self.controller, "_has_travel_backlink", lambda doctype: True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		self.claims = []
		self.doc = self.controller.TravelTrip()
		self.doc.name = "TRIP-2026-00007"

	def test_its_copies_let_go_of_it_and_keep_their_modified(self):
		self.doc.on_trash()
		self.assertIn(
			("Travel Trip", {"copied_from": "TRIP-2026-00007"}, "copied_from", None, {"update_modified": False}),
			self.writes,
		)

	def test_a_trip_it_refuses_to_delete_keeps_its_copies(self):
		# A submitted claim still on the trip: the delete is refused, and nothing is unlinked on
		# the way (frappe rolls the request back anyway; this keeps the order honest).
		self.claims = [types.SimpleNamespace(name="HR-EXP-1", docstatus=1)]
		with self.assertRaises(sys.modules["frappe"].ValidationError) as refused:
			self.doc.on_trash()
		self.assertIn("Expense Claim HR-EXP-1", str(refused.exception))
		self.assertNotIn("Travel Trip", [write[0] for write in self.writes])


class TestCopiedFromNeverRefusesASave(unittest.TestCase):
	"""``copied_from`` is the server's and is provenance only (v1.550.0). frappe v16 checks every
	Link in ``_validate_links`` — before ``before_insert`` and ``validate`` — so the controller
	settles it in ``get_invalid_links``. Two saves that would otherwise be refused over a field
	nobody can edit: a Desk form opened before the source trip was deleted (``on_trash`` cleared
	the stored value without touching ``modified``, so the form still posts the deleted name), and
	a copy restored from Deleted Document after its source was deleted too."""

	def setUp(self):
		TestTheControllersFileRules.setUp(self)
		frappe = sys.modules["frappe"]
		self.trips = {"TRIP-A"}
		base = self.controller.TravelTrip.__bases__[0]
		for patcher in (
			mock.patch.object(frappe.db, "exists", lambda doctype, name: name in self.trips, create=True),
			mock.patch.object(
				base, "get_doc_before_save", lambda doc: getattr(doc, "_doc_before_save", None), create=True
			),
			mock.patch.object(base, "get_invalid_links", lambda doc, is_submittable=False: ("frappe's", "check"),
				create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def trip(self, posted, stored="new"):
		doc = self.controller.TravelTrip()
		doc.name = "TRIP-B"
		doc.copied_from = posted
		if stored != "new":
			before = self.controller.TravelTrip()
			before.copied_from = stored
			doc._doc_before_save = before
		return doc

	def test_a_desk_form_opened_before_the_source_was_deleted_still_saves(self):
		self.trips = set()  # TRIP-A deleted; on_trash cleared the stored value
		doc = self.trip("TRIP-A", stored=None)
		self.assertEqual(doc.get_invalid_links(), ("frappe's", "check"), "frappe's own check still runs")
		self.assertIsNone(doc.copied_from)

	def test_an_update_keeps_the_stored_source_whatever_is_posted(self):
		doc = self.trip("TRIP-A", stored=None)
		doc.get_invalid_links()
		self.assertIsNone(doc.copied_from, "an update cannot make a trip a copy")
		self.trips.add("TRIP-X")
		doc = self.trip("TRIP-X", stored="TRIP-A")
		doc.get_invalid_links()
		self.assertEqual(doc.copied_from, "TRIP-A", "nor move it")

	def test_a_first_save_keeps_a_source_that_is_there_and_drops_one_that_is_gone(self):
		doc = self.trip("TRIP-A")  # save_plan's first save, or a restore while the source is there
		doc.get_invalid_links()
		self.assertEqual(doc.copied_from, "TRIP-A")
		self.trips = set()  # a restore after the source was deleted too
		doc = self.trip("TRIP-A")
		doc.get_invalid_links()
		self.assertIsNone(doc.copied_from)
		doc = self.trip(None)
		doc.get_invalid_links()
		self.assertIsNone(doc.copied_from)


def _frappe_flt(value, precision=None):
	"""frappe v16 ``utils.data.flt`` with the default rounding method, Banker's Rounding (legacy).

	The module stub's ``flt`` is Python's ``round``, which rounds the binary float: 3 x 0.725 is
	2.17499…, so ``round(3 * 0.725, 2)`` is 2.17. frappe scales and snaps to 8 places first, so
	the half cent rounds up and ``flt(3 * 0.725, 2)`` is 2.18 — as do the other two methods
	(Banker's Rounding breaks the tie to the even 8, Commercial Rounding away from zero)."""
	try:
		num = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	if precision is None:
		return num
	precision = int(precision)
	multiplier = 10**precision
	num = round(num * multiplier if precision else num, 8)
	floor_num = math.floor(num)
	decimal_part = num - floor_num
	if not precision and decimal_part == 0.5:
		num = floor_num if floor_num % 2 == 0 else floor_num + 1
	elif decimal_part == 0.5:
		num = floor_num + 1
	else:
		num = round(num)
	return num / multiplier if precision else num


class TestTheMileageRate(unittest.TestCase):
	"""The mileage rate is $0.725 a mile, and the form would not take it (reported 2026-09-28).

	Both rate fields are Currency, and a Currency field with no ``precision`` of its own takes the
	site's currency precision, 2 (frappe v16 ``model/meta.py`` ``get_field_precision``); the
	Desk's ``ControlCurrency.parse`` rounds what is typed to that, so 0.725 saved as 0.72. Each
	rate field now carries precision 3. The amount is money and stays at cents: the controller
	rounds miles x rate to the amount field's own precision, so it never holds 31.175."""

	DOCTYPES = os.path.join(APP_DIR, "travel_management", "doctype")
	#: What ``get_field_precision`` answers for a Currency field with no precision of its own.
	SITE_CURRENCY_PRECISION = 2

	@classmethod
	def fields(cls, doctype):
		with open(os.path.join(cls.DOCTYPES, doctype, f"{doctype}.json"), encoding="utf-8") as fh:
			return {f["fieldname"]: f for f in json.load(fh)["fields"]}

	def setUp(self):
		TestTheControllersFileRules.setUp(self)
		self.settings = types.SimpleNamespace(mileage_rate=0.725)
		for patcher in (
			mock.patch.object(self.controller, "get_travel_settings", lambda: self.settings),
			mock.patch.object(self.controller, "flt", _frappe_flt),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def row(self, **values):
		"""A Trip Mileage row whose ``precision()`` answers as frappe v16's ``BaseDocument.precision``
		would, from the doctype JSON: the field's own precision, else the site's currency precision."""
		fields = self.fields("trip_mileage")
		row = FakeRow(**values)
		row.precision = lambda fieldname: int(
			fields[fieldname].get("precision") or self.SITE_CURRENCY_PRECISION
		)
		return row

	def trip(self, *rows):
		doc = self.controller.TravelTrip()
		doc.mileage = list(rows)
		return doc

	def test_both_rate_fields_keep_a_tenth_of_a_cent(self):
		for doctype, fieldname in (("travel_settings", "mileage_rate"), ("trip_mileage", "rate")):
			with self.subTest(f"{doctype}.{fieldname}"):
				field = self.fields(doctype)[fieldname]
				self.assertEqual(field["fieldtype"], "Currency")
				self.assertEqual(field.get("precision"), "3")

	def test_the_amount_stays_in_cents(self):
		amount = self.fields("trip_mileage")["amount"]
		self.assertEqual(amount["fieldtype"], "Currency")
		self.assertNotIn("precision", amount, "the site's currency precision: cents")

	def test_a_row_takes_the_settings_rate_unrounded_and_its_amount_in_cents(self):
		row = self.row(idx=1, distance=43, rate=0)
		self.trip(row)._compute_mileage()
		self.assertEqual(row.rate, 0.725)
		self.assertEqual(row.amount, 31.18)  # 43 x 0.725 = 31.175

	def test_a_half_cent_rounds_up_as_frappe_rounds_it(self):
		# 3 x 0.725 = 2.175, held as 2.17499…: Python's round gives 2.17, frappe's flt 2.18.
		row = self.row(idx=1, distance=3, rate=None)
		self.trip(row)._compute_mileage()
		self.assertEqual((row.rate, row.amount), (0.725, 2.18))

	def test_a_row_with_its_own_rate_keeps_it(self):
		row = self.row(idx=1, distance=10, rate=0.655)
		self.trip(row)._compute_mileage()
		self.assertEqual((row.rate, row.amount), (0.655, 6.55))

	def test_a_claimed_row_is_left_as_it_was(self):
		# On an Expense Claim already: a rate entered as 0.72 before this fix stays 0.72.
		row = self.row(idx=1, distance=43, rate=0.72, amount=30.96, expense_claim="HR-EXP-1")
		self.trip(row)._compute_mileage()
		self.assertEqual((row.rate, row.amount), (0.72, 30.96))

	def test_plan_a_trip_shows_the_rate_to_its_third_place(self):
		# format_currency with no places rounds to the currency's two: $0.725 would lose its third.
		with open(
			os.path.join(APP_DIR, "travel_management", "page", "plan_a_trip", "plan_a_trip.js"),
			encoding="utf-8",
		) as fh:
			page = fh.read()
		self.assertIn("const rate_places = flt(rate, 2) === rate ? undefined : 3;", page)
		self.assertIn("format_currency(rate, this.lookups.currency, rate_places)", page)
		# The page is handed the rate as stored, not rounded on the way.
		self.assertIn('"mileage_rate": flt(settings.mileage_rate),', inspect.getsource(planner._lookups))


class TestTheClosingWarning(unittest.TestCase):
	"""``TravelTrip._warn_unclaimed_on_close``: what whoever closes a trip is told while it still
	shows money owed to someone. A traveler can close their own trip (Completed -> Closed is in
	``EMPLOYEE_ALLOWED_TRANSITIONS``), and on production HRMS is absent
	(``expense_claims_available()`` is False), so nothing is ever on an Expense Claim and the old
	"…not yet on an Expense Claim" fired on every close, about a claim nobody could make. There it
	now says who is owed, that accounting reimburses them from the trip, how many employee-paid
	costs have no receipt, and who can change a closed trip. It stays a warning, never a refusal.
	The real controller, with the same stand-ins as ``TestTheControllersFileRules``."""

	def setUp(self):
		TestTheControllersFileRules.setUp(self)
		frappe = sys.modules["frappe"]
		self.printed = []
		self.hrms = False
		for patcher in (
			mock.patch.object(
				frappe, "msgprint", lambda message, **kw: self.printed.append((message, kw)), create=True
			),
			mock.patch.object(frappe, "format_value", lambda value, df=None: f"$ {value:,.2f}", create=True),
			mock.patch.object(self.controller, "expense_claims_available", lambda: self.hrms),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def trip(self, **tables):
		"""Ann has per diem, a flight she paid for (its receipt attached) and 43 miles; Bo paid for
		the room and has not attached its receipt; Cy gets no per diem. The rental was the
		company's, and takes no part."""
		doc = TestTheControllersFileRules.trip(self)
		doc.travelers = tables.get(
			"travelers",
			[
				FakeRow(employee="EMP-A", employee_name="Ann", per_diem_eligible=1, per_diem_amount=150),
				FakeRow(employee="EMP-B", employee_name="Bo", per_diem_eligible=1, per_diem_amount=0),
				FakeRow(employee="EMP-C", employee_name="Cy", per_diem_eligible=0, per_diem_amount=0),
			],
		)
		doc.flights = tables.get(
			"flights",
			[FakeRow(paid_by="Employee", paid_by_traveler="EMP-A", cost=300, attachment="/private/files/wn1.pdf")],
		)
		doc.accommodations = tables.get(
			"accommodations", [FakeRow(paid_by="Employee", paid_by_traveler="EMP-B", cost=240)]
		)
		doc.ground_transport = tables.get("ground_transport", [FakeRow(paid_by="Company", cost=500)])
		doc.mileage = tables.get("mileage", [FakeRow(traveler="EMP-A", amount=31.18)])
		return doc

	def warning(self, doc):
		self.printed.clear()
		doc._warn_unclaimed_on_close()
		self.assertLessEqual(len(self.printed), 1)
		return self.printed[0] if self.printed else (None, None)

	def test_on_production_it_says_who_is_owed_and_who_can_change_a_closed_trip(self):
		message, kw = self.warning(self.trip())
		self.assertEqual(
			message,
			"This trip is being closed while it shows $ 721.18 owed to Ann, Bo for employee-paid costs, "
			"per diem or mileage. Accounting reimburses that from the trip; there is no claim to make. "
			"1 employee-paid cost has no receipt attached yet. "
			"Only a Travel Coordinator can change a closed trip, so a receipt attached after this means "
			"asking one to reopen it. Per diem and mileage need no receipts.",
		)
		self.assertEqual(kw, {"indicator": "orange"})
		self.assertNotIn("Expense Claim", message)

	def test_it_counts_the_employee_paid_costs_with_no_receipt(self):
		doc = self.trip(flights=[FakeRow(paid_by="Employee", paid_by_traveler="EMP-A", cost=300)])
		self.assertIn(" 2 employee-paid costs have no receipt attached yet. ", self.warning(doc)[0])
		# Every receipt on: nothing to count. A company-paid row never counted.
		doc = self.trip(accommodations=[])
		message = self.warning(doc)[0]
		self.assertNotIn("receipt attached yet", message)
		self.assertIn("owed to Ann for employee-paid costs", message)

	def test_someone_owed_only_per_diem_needs_no_receipt(self):
		doc = self.trip(flights=[], accommodations=[], mileage=[])
		message = self.warning(doc)[0]
		self.assertIn("shows $ 150.00 owed to Ann for", message)
		self.assertNotIn("receipt attached yet", message)
		self.assertTrue(message.endswith("Per diem and mileage need no receipts."))

	def test_with_hrms_it_means_what_it_always_meant(self):
		self.hrms = True
		message, kw = self.warning(self.trip())
		self.assertEqual(
			message,
			"This trip is being closed with $ 721.18 of employee-paid costs, per diem or mileage not yet "
			"on an Expense Claim.",
		)
		self.assertEqual(kw, {"indicator": "orange"})
		# What is on a claim already is not owed.
		doc = self.trip(
			flights=[FakeRow(paid_by="Employee", paid_by_traveler="EMP-A", cost=300, expense_claim="HR-EXP-1")],
			mileage=[FakeRow(traveler="EMP-A", amount=31.18, expense_claim="HR-EXP-1")],
		)
		self.assertIn(" $ 390.00 of ", self.warning(doc)[0])

	def test_nothing_owed_says_nothing(self):
		for hrms in (False, True):
			self.hrms = hrms
			doc = self.trip(
				travelers=[FakeRow(employee="EMP-C", employee_name="Cy", per_diem_eligible=0)],
				flights=[],
				accommodations=[],
				mileage=[],
			)
			self.assertEqual(self.warning(doc), (None, None), hrms)

	def test_a_traveler_closing_their_own_trip_is_warned_and_the_close_goes_ahead(self):
		# No coordinator role (the setUp's get_roles answers []): Completed -> Closed is theirs.
		doc = self.trip()
		doc.status = "Closed"
		doc.is_new = lambda: False
		doc._before = types.SimpleNamespace(status="Completed")
		doc._handle_status_change()
		self.assertIsNotNone(doc.closed_on)
		self.assertEqual(len(self.printed), 1)
		self.assertIn("Only a Travel Coordinator can change a closed trip", self.printed[0][0])
		# A warning, never a refusal.
		self.assertNotIn("frappe.throw", inspect.getsource(self.controller.TravelTrip._warn_unclaimed_on_close))


class TestGetTripItinerary(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()

	def call(self, as_employee=None, user="bo@example.com"):
		sys.modules["frappe"].session.user = user
		return travel.get_trip_itinerary("TRIP-1", as_employee)

	def test_default_is_your_own_when_you_are_on_the_crew(self):
		result = self.call()
		self.assertEqual(result["viewing"], "EMP-B")
		self.assertEqual(result["viewer_employee"], "EMP-B")
		self.assertIs(result["viewer_on_trip"], True)
		self.assertEqual([c["employee"] for c in result["crew"]], ["EMP-A", "EMP-B", "EMP-C"])
		self.assertTrue(all("members" not in i for i in items_of(result["days"])))
		self.assertNoMoney(result)

	def test_default_is_the_whole_crew_for_someone_not_on_it(self):
		result = self.call(user="owner@example.com")
		self.assertIsNone(result["viewing"])
		self.assertIsNone(result["viewer_employee"])
		self.assertIs(result["viewer_on_trip"], False)
		self.assertTrue(any("members" in i for i in items_of(result["days"])))
		self.assertNoMoney(result)

	def test_crew_asks_for_the_whole_crew(self):
		result = self.call("crew")
		self.assertIsNone(result["viewing"])
		self.assertEqual(result["viewer_employee"], "EMP-B")
		self.assertIs(result["viewer_on_trip"], True)
		self.assertNoMoney(result)

	def test_anyone_who_can_read_the_trip_may_view_as_a_crew_member(self):
		result = self.call("EMP-C", user="owner@example.com")
		self.assertEqual(result["viewing"], "EMP-C")
		shipment = next(i for i in items_of(result["days"]) if i["type"] == "freight")
		self.assertNotIn("members", shipment)
		self.assertEqual(
			result,
			dict(
				travel.shape_itinerary(self.doc, "EMP-C"),
				**{
					"crew": result["crew"],
					"viewing": "EMP-C",
					"viewer_employee": None,
					"viewer_on_trip": False,
					# PR 3: who to call (Cy's own hotels: none) and the printed sheet, the
					# whole trip's and Cy's.
					"contacts": travel._trip_contacts(self.doc, "EMP-C"),
					"sheet_url": views.trip_sheet_url("TRIP-1"),
					"my_sheet_url": views.trip_sheet_url("TRIP-1", "EMP-C"),
					# 2026-09-28: someone who can read the trip gets the full answer.
					"limited": False,
				},
			),
		)
		self.assertTrue(result["my_sheet_url"].endswith("&as=EMP-C"))
		self.assertNoMoney(result)

	def looked_up(self):
		"""Every Employee lookup but the session's own (``_session_employee``)."""
		return [
			name
			for name in SITE.employee_lookups
			if not (isinstance(name, dict) and set(name) == {"user_id"})
		]

	def test_someone_not_on_the_crew_is_refused_and_never_named(self):
		frappe = sys.modules["frappe"]
		# Zed is a real Employee, just not on this trip. Naming him told anyone who could read
		# one trip the name behind any Employee id. An id from the URL is never echoed either.
		for stranger in ("EMP-Z", "<img src=x onerror=alert(1)>"):
			SITE.employee_lookups.clear()
			with self.assertRaises(frappe.ValidationError) as refused:
				self.call(stranger)
			self.assertEqual(str(refused.exception), "That person is not on this trip.")
			self.assertEqual(self.looked_up(), [], f"{stranger} was looked up")

	def test_a_filter_in_place_of_a_person_is_refused_and_never_queried(self):
		# A JSON body can send a dict where the page sends a string. frappe.db.get_value takes a
		# dict as filters, with no permission check. Looking one up to name it answered yes or
		# no about any Employee field (CTC, bank account, date of birth), one request at a time.
		frappe = sys.modules["frappe"]
		for probe in ({"name": "EMP-Z", "ctc": [">", 90000]}, {"name": "EMP-A"}, ["EMP-A"]):
			SITE.employee_lookups.clear()
			with self.assertRaises(frappe.ValidationError) as refused:
				self.call(probe)
			self.assertEqual(str(refused.exception), "That person is not on this trip.")
			self.assertEqual(self.looked_up(), [], probe)

	def test_read_permission_comes_first(self):
		# Someone who cannot read the trip and is not staff (a Website User): refused, before the
		# person asked for is even looked at. Staff who cannot read it get the outsider view
		# instead (TestTheOutsidersItinerary).
		SITE.deny = True
		with mock.patch.object(travel, "_is_staff", return_value=False):
			with self.assertRaises(sys.modules["frappe"].PermissionError):
				self.call("EMP-A")
		self.assertIn(("Travel Trip", "read", "TRIP-1", False), SITE.permission_checks)


class TestBootstrap(unittest.TestCase):
	#: Who holds what, for ``_is_staff``, which runs for real: the Employee role or a coordinator's.
	ROLES = {
		"bo@example.com": ["Employee"],
		"office@example.com": ["Employee"],
		"tc@example.com": ["Travel Coordinator"],
		# A customer signed in to the portal: a Website User.
		"portal@example.com": ["Customer"],
		# A desk account whose Role Profile has no Employee role.
		"desk@example.com": ["Projects User"],
	}

	def setUp(self):
		install_site()
		frappe = sys.modules["frappe"]
		# The controller ``_is_coordinator`` imports needs these two, as in TestTheCoordinatorGate.
		document = types.ModuleType("frappe.model.document")
		document.Document = type("Document", (), {})
		model = types.ModuleType("frappe.model")
		model.document = document
		for patcher in (
			mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}),
			mock.patch.object(frappe, "get_roles", lambda user=None: self.ROLES.get(user, []), create=True),
			mock.patch.object(frappe.utils, "date_diff", lambda a, b: 0, create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		sys.modules.pop(CONTROLLER, None)
		SITE.get_all = {
			"Trip Traveler": ["TRIP-1", "TRIP-2"],
			"Travel Trip": [
				{
					"name": "TRIP-1",
					"purpose": "Install",
					"start_date": "2026-10-05",
					"end_date": "2026-10-08",
				},
				{
					"name": "TRIP-2",
					"purpose": "Service",
					"start_date": "2026-10-20",
					"end_date": "2026-10-21",
				},
			],
		}
		SITE.get_list = {
			"Travel Trip": [
				{
					"name": "TRIP-1",
					"purpose": "Install",
					"start_date": "2026-10-05",
					"end_date": "2026-10-08",
				},
				{"name": "TRIP-3", "purpose": "Survey", "start_date": "2026-10-07", "end_date": "2026-10-07"},
			]
		}

	def test_your_trips_and_the_ones_you_booked(self):
		boot = travel.get_itinerary_bootstrap()
		self.assertEqual(boot["employee"], "EMP-B")
		self.assertEqual([t["name"] for t in boot["trips"]], ["TRIP-1", "TRIP-3", "TRIP-2"])
		self.assertEqual([t["mine"] for t in boot["trips"]], [True, False, True])
		doctype, kwargs = SITE.get_list_calls[0]
		self.assertEqual(doctype, "Travel Trip")
		self.assertEqual(kwargs["filters"]["owner"], "bo@example.com")
		self.assertEqual(kwargs["filters"]["status"], ["!=", "Closed"])
		self.assertEqual(kwargs["filters"]["end_date"][0], ">=")

	def test_an_owner_with_no_employee_record_still_finds_their_trips(self):
		sys.modules["frappe"].session.user = "office@example.com"
		boot = travel.get_itinerary_bootstrap()
		self.assertIsNone(boot["employee"])
		self.assertEqual(
			[(t["name"], t["mine"]) for t in boot["trips"]], [("TRIP-1", False), ("TRIP-3", False)]
		)

	def test_the_owned_list_is_permission_scoped(self):
		source = inspect.getsource(travel._itinerary_trips)
		self.assertIn("frappe.get_list(", source)
		self.assertNotIn("frappe.get_all(", source)

	def test_no_read_on_travel_trip_is_an_empty_owned_list_not_an_error(self):
		# A Website User, or a staff account with no Employee role: get_list raises for them
		# (the stub does too, like v16), and /itinerary became Frappe's "Not Permitted" page
		# where it used to show its own trips or its empty state.
		SITE.deny = True
		boot = travel.get_itinerary_bootstrap()
		self.assertEqual(
			[(t["name"], t["mine"]) for t in boot["trips"]], [("TRIP-1", True), ("TRIP-2", True)]
		)
		self.assertEqual(SITE.get_list_calls, [], "get_list was asked, and would have raised")
		self.assertIn(("Travel Trip", "read", None, False), SITE.permission_checks)

	def test_the_boot_says_who_is_staff(self):
		"""``is_staff`` decides whether /itinerary offers the list of every trip ("All trips" and
		"See all trips"). ``get_all_trips`` refuses anyone who is not staff, so until v1.556.2 a
		portal customer who tapped either was told "You don't have access to the list of all
		trips." It is ``_is_staff``'s answer, a real bool for the page's ``=== true``."""
		for user, staff in (
			("bo@example.com", True),  # the Employee role
			("tc@example.com", True),  # a coordinator, with no Employee role
			("Administrator", True),
			("portal@example.com", False),  # a Website User
			("desk@example.com", False),  # a desk account with no Employee role
		):
			sys.modules["frappe"].session.user = user
			with self.subTest(user):
				self.assertIs(travel.get_itinerary_bootstrap()["is_staff"], staff)
		# The same answer the list itself is gated on.
		self.assertIn('"is_staff": bool(_is_staff()),', inspect.getsource(travel.get_itinerary_bootstrap))
		self.assertIn("if not _is_staff():", inspect.getsource(travel.get_all_trips))

	def test_the_page_draws_the_list_only_for_staff(self):
		"""What the page does with it (driven in node by scripts/test_web_flow_history.js): the
		chip and the empty page's link are drawn only on ``is_staff === true``, so a boot from
		before it was sent offers neither."""
		with open(os.path.join(APP_DIR, "public", "js", "travel", "itinerary.js"), encoding="utf-8") as fh:
			page = fh.read()
		self.assertIn("return BOOT.is_staff === true;", page)
		self.assertIn("if (isStaff()) root.appendChild(allTripsLink());", page)
		chip = page.index("var allChip = el('button', 'ti-all-chip');")
		self.assertLess(page.rindex("if (isStaff()) {", 0, chip), chip)
		self.assertLess(chip - page.rindex("if (isStaff()) {", 0, chip), 80)


# --------------------------------------------------------------------------- email preview


class TestEmailPreview(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()

	def preview(self, employee="EMP-B", coordinator=False):
		with mock.patch.object(travel, "_is_coordinator", return_value=coordinator):
			return travel.preview_itinerary_email("TRIP-1", employee)

	def assertNothingSent(self):
		self.assertEqual(SITE.side_effects, [], "a preview sent mail, wrote a log or queued a job")

	def test_the_preview_is_what_that_person_gets(self):
		preview = self.preview()
		self.assertNothingSent()
		self.assertEqual(preview["subject"], "Your itinerary: Install (2026-10-05 – 2026-10-08)")
		self.assertEqual(preview["to_name"], "Bo")
		self.assertIsNone(preview["to_email"])  # not a coordinator
		self.assertIs(preview["no_email"], False)
		self.assertTrue(preview["html"].startswith("<shell title='Your itinerary"))
		self.assertIn("eyebrow='Travel'", preview["html"])
		self.assertIn("PNR-B", preview["html"])
		self.assertNotIn("PNR-A", preview["html"])
		self.assertTrue(preview["ics"].startswith("BEGIN:VCALENDAR\r\n"))
		self.assertIn("METHOD:PUBLISH", preview["ics"])
		self.assertEqual(preview["ics_filename"], "trip_1.ics")
		summaries = [event["summary"] for event in preview["events"]]
		self.assertEqual(summaries[0], "Trip: Install")
		self.assertIn("🏨 Check-in: Hotel One", summaries)
		span = preview["events"][0]
		self.assertEqual((span["start"], span["end"], span["all_day"]), ("2026-10-06", "2026-10-08", True))
		self.assertEqual(
			set(preview),
			{"subject", "to_name", "to_email", "html", "ics", "ics_filename", "events", "no_email"},
		)
		self.assertNoMoney(preview)

	def test_a_coordinator_sees_the_address(self):
		self.assertEqual(self.preview(coordinator=True)["to_email"], "bo@corp.example")
		self.assertNothingSent()

	def test_someone_with_no_address_still_gets_a_preview(self):
		preview = self.preview("EMP-C", coordinator=True)
		self.assertIs(preview["no_email"], True)
		self.assertIsNone(preview["to_email"])
		self.assertIn("Old Dominion", preview["ics"])
		self.assertNothingSent()

	def test_a_row_with_no_employee_record_still_previews(self):
		del SITE.employees["EMP-C"]
		preview = self.preview("EMP-C")
		self.assertEqual(preview["to_name"], "Cy")
		self.assertIs(preview["no_email"], True)
		self.assertNothingSent()

	def test_someone_not_on_the_crew_is_refused(self):
		frappe = sys.modules["frappe"]
		# A filter dict or a list in place of the person too: never looked up (see
		# TestGetTripItinerary), and the same sentence whoever was asked for.
		for stranger in ("EMP-Z", {"name": "EMP-Z", "ctc": [">", 90000]}, ["EMP-B"]):
			SITE.employee_lookups.clear()
			with self.assertRaises(frappe.ValidationError) as refused:
				self.preview(stranger, coordinator=True)
			self.assertEqual(str(refused.exception), "That person is not on this trip.")
			self.assertEqual(SITE.employee_lookups, [], stranger)
		self.assertNothingSent()

	def test_frappes_own_frame_is_used_when_it_is_there(self):
		email_body = types.ModuleType("frappe.email.email_body")
		email_body.get_formatted_html = lambda subject, message, **kw: f"<frappe>{message}</frappe>"
		with mock.patch.dict(
			sys.modules,
			{"frappe.email": types.ModuleType("frappe.email"), "frappe.email.email_body": email_body},
		):
			preview = self.preview()
		self.assertTrue(preview["html"].startswith("<frappe><shell"))
		self.assertNothingSent()

	def test_the_send_delivers_exactly_what_the_preview_showed(self):
		preview = self.preview()
		SITE.allow_side_effects = True
		sent = notifications.send_itinerary_emails(self.doc, employee="EMP-B", force=True)
		self.assertEqual(sent, ["EMP-B"])
		mails = [effect[1] for effect in SITE.side_effects if effect[0] == "sendmail"]
		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0]["recipients"], ["bo@corp.example"])
		self.assertEqual(mails[0]["subject"], preview["subject"])
		self.assertEqual(mails[0]["message"], preview["html"])
		self.assertEqual(mails[0]["reference_doctype"], "Travel Trip")
		self.assertEqual(
			mails[0]["attachments"], [{"fname": preview["ics_filename"], "fcontent": preview["ics"]}]
		)
		# Bo has a user, so the bell gets the unwrapped fragment.
		self.assertIn(("insert", "Notification Log"), SITE.side_effects)
		self.assertEqual(
			SITE.rendered[-1][0], "erpnext_enhancements/templates/emails/travel/pre_travel_reminder.html"
		)

	def test_everyone_is_emailed_who_has_an_address(self):
		SITE.allow_side_effects = True
		sent = notifications.send_itinerary_emails(self.doc, force=True)
		self.assertEqual(sent, ["EMP-A", "EMP-B"])  # Cy has no address

	def test_no_send_on_the_preview_path(self):
		"""Static: nothing the preview runs can deliver. A refactor that routed it through
		_send or _deliver would turn a preview into a real email."""
		for function in (
			travel.preview_itinerary_email,
			notifications.render_itinerary_preview,
			notifications._render,
			notifications._itinerary_email,
			notifications._full_html,
		):
			source = inspect.getsource(function)
			code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
			code = code.split('"""')[0] + (code.split('"""')[2] if code.count('"""') >= 2 else "")
			for forbidden in (
				"sendmail(",
				"enqueue(",
				"Notification Log",
				"_deliver(",
				"_send(",
				"send_itinerary",
			):
				self.assertNotIn(forbidden, code, f"{function.__name__} reaches {forbidden}")


class TestTheEmailTemplateCarriesNoMoney(unittest.TestCase):
	"""The preview shows any crew member's itinerary email to anyone who can read the trip, and
	the stub ``render_template`` above never reads the template file, so ``assertNoMoney`` on a
	preview says nothing about what the template prints. Its context holds the whole trip
	(``trip``, every rollup included) and the traveler row (``recipient.row``: per diem,
	advance). One added line there would put money in every preview and every sent itinerary."""

	TEMPLATE = "erpnext_enhancements/templates/emails/travel/pre_travel_reminder.html"
	MONEY = re.compile(r"cost|amount|per_diem|advance|total_|billable|paid_by|claim|currency", re.I)

	def money_in(self, source):
		# The comments may name what is left out: only the template's code is checked.
		code = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
		found = self.MONEY.search(code)
		return found.group(0) if found else None

	def test_the_template_the_preview_renders_names_no_money(self):
		install_site()
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			travel.preview_itinerary_email("TRIP-1", "EMP-B")
		self.assertEqual({path for path, _context in SITE.rendered}, {self.TEMPLATE})
		path = os.path.join(os.path.dirname(APP_DIR), *self.TEMPLATE.split("/"))
		with open(path, encoding="utf-8") as fh:
			source = fh.read()
		self.assertIn("itinerary_days", source)
		self.assertIsNone(self.money_in(source))

	def test_the_check_catches_a_money_line(self):
		self.assertEqual(self.money_in('{{ ee.kv([("Budget", trip.total_estimated_cost)]) }}'), "total_")
		self.assertEqual(self.money_in("{{ recipient.row.per_diem_amount }}"), "per_diem")
		self.assertIsNone(self.money_in("{# the cost is left out on purpose #}{{ trip.name }}"))


class TestTheTravelerIsToldWhatProductionCanDo(unittest.TestCase):
	"""The travel guidelines and the two post-trip emails may only send a traveler to something
	production has (v1.552.1). HRMS is not installed there and cannot be
	(``accounting_intake/actions/receipt_expense.py``), so Expense Claim, Employee Advance and
	Vehicle Log do not exist and the trip form hides their Create buttons — yet the guidelines
	told every traveler to use Create → Expense Claim and to create a Vehicle Log, the expense
	nudge said the same, and the Closed notice asked them to finish a claim. Nik's decision
	(2026-09-28): each itemized receipt goes on its cost row on the trip, and accounting
	reimburses from there. There is no claim to submit.

	The emails are rendered with the real macros, from the context their senders build. The
	guidelines extend the website's base template, so their source is read instead, with
	comments removed first: the comments explain the absence and so name what is absent."""

	GUIDELINES = os.path.join(APP_DIR, "www", "travel_guidelines.html")
	EMAILS = "erpnext_enhancements/templates/emails/travel"
	HRMS = re.compile(r"Expense Claim|Vehicle Log|Employee Advance|Create\s*(?:→|&rarr;|&nbsp;)", re.I)

	def setUp(self):
		try:
			import jinja2
		except ImportError:  # pragma: no cover
			self.skipTest("jinja2 not installed")
		self.doc = install_site()

	@staticmethod
	def code(source):
		"""``source`` without Jinja or HTML comments, whitespace collapsed."""
		source = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
		source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
		return re.sub(r"\s+", " ", source)

	def hrms_in(self, source):
		found = self.HRMS.search(self.code(source))
		return found.group(0) if found else None

	def env(self):
		from jinja2 import Environment, FileSystemLoader, StrictUndefined

		return Environment(
			loader=FileSystemLoader(os.path.dirname(APP_DIR)), autoescape=False, undefined=StrictUndefined
		)

	def render(self, template, status="Completed", **extra):
		self.doc.status = status
		context = dict(notifications._base_context(self.doc), recipient=None, **extra)
		return self.code(self.env().get_template(f"{self.EMAILS}/{template}").render(**context))

	def nudge(self, status="Completed", receipts_due=True):
		# The three keys reminders.send_post_trip_expense_nudges adds to the base context.
		return self.render(
			"expense_nudge.html",
			status,
			unclaimed_amount="$ 612.50",
			days_since_end=3,
			receipts_due=receipts_due,
		)

	def test_the_check_catches_the_old_wording(self):
		self.assertEqual(self.hrms_in("use <b>Create&nbsp;→&nbsp;Expense Claim</b>"), "Create&nbsp;")
		self.assertEqual(self.hrms_in("use <b>Create &rarr; Expense Claim</b>"), "Create &rarr;")
		self.assertEqual(self.hrms_in("create its <b>Vehicle Log</b>"), "Vehicle Log")
		self.assertIsNone(self.hrms_in("{# no Expense Claim here #}<!-- nor a Vehicle Log --> receipts"))

	def test_each_template_reads_only_what_its_sender_passes(self):
		from jinja2 import meta

		base = set(notifications._base_context(self.doc)) | {"recipient"}
		for template, extra in (
			("expense_nudge.html", {"unclaimed_amount", "days_since_end", "receipts_due"}),
			("trip_closed.html", set()),
		):
			env = self.env()
			source = env.loader.get_source(env, f"{self.EMAILS}/{template}")[0]
			with self.subTest(template):
				self.assertLessEqual(meta.find_undeclared_variables(env.parse(source)), base | extra)
		with open(os.path.join(APP_DIR, "travel_management", "reminders.py"), encoding="utf-8") as fh:
			reminders = fh.read()
		self.assertIn('"expense_nudge.html"', reminders)
		self.assertIn("unclaimed_amount=", reminders)
		self.assertIn("days_since_end=", reminders)
		self.assertIn("receipts_due=", reminders)
		# The subject asks for the receipts too; "Unclaimed" promised a claim to make. Someone
		# with no receipt to attach is not asked for one (TestTheExpenseNudgeAsksOnlyForReceiptsDue).
		self.assertIn('_("Attach your travel receipts: {0}")', reminders)
		self.assertIn('_("Your travel reimbursement: {0}")', reminders)
		self.assertNotIn("Unclaimed travel expenses", reminders)

	def test_the_expense_nudge_asks_for_receipts_on_the_trip(self):
		html = self.nudge()
		self.assertIsNone(self.hrms_in(html))
		self.assertIn("Attach your itemized receipts to the trip so accounting can reimburse you", html)
		self.assertIn("<b>Receipt</b> field of the cost it paid for", html)
		self.assertIn("There is no claim to submit", html)
		# Their per diem and mileage are part of what they are owed, and take no receipt.
		self.assertIn("owed to you for employee-paid costs, per diem or mileage.", html)
		self.assertIn("Your per diem and mileage need no receipts.", html)
		self.assertNotIn("nothing for you to attach", html)
		# Nothing is ever stamped as claimed without HRMS: the amount is what the trip says the
		# company owes this person, not something left off a claim.
		self.assertIn(">To reimburse<", html)
		self.assertIn("$ 612.50", html)
		self.assertNotIn("nclaimed", html)
		self.assertIn('href="https://example.com/travel_guidelines"', html)
		# An open trip can take the receipts: no word about reopening it.
		self.assertNotIn("Travel Coordinator", html)

	def test_the_nudge_on_a_closed_trip_says_who_can_reopen_it(self):
		# travel_trip._check_closed_lock: a Closed trip refuses every save but a coordinator's.
		html = self.nudge("Closed")
		self.assertIn("only a Travel Coordinator can change it: ask one to reopen it", html)
		self.assertIsNone(self.hrms_in(html))

	def test_someone_owed_only_per_diem_or_mileage_is_asked_for_nothing(self):
		"""Nik, 2026-09-28: "Meal receipts shouldn't be required if paid by per diem." Per diem
		and mileage have no Receipt field, so a traveler owed only those has nothing to attach,
		and a Closed trip is nothing to reopen for."""
		for status in ("Completed", "Closed"):
			with self.subTest(status):
				html = self.nudge(status, receipts_due=False)
				self.assertIsNone(self.hrms_in(html))
				self.assertIn("it shows $ 612.50 owed to you for per diem or mileage.", html)
				self.assertIn(
					"Per diem and mileage need no receipts, so there is nothing for you to attach for them.", html
				)
				self.assertIn("There is no claim to submit either — accounting pays them from the trip.", html)
				self.assertNotIn("employee-paid", html)
				self.assertNotIn("Attach your itemized receipts", html)
				self.assertNotIn("<b>Receipt</b>", html)
				self.assertNotIn("Travel Coordinator", html)
				self.assertNotIn("within a week", html)
				# Still what they are owed, and the way to the trip.
				self.assertIn(">To reimburse<", html)
				self.assertIn("$ 612.50", html)
				self.assertIn("View the trip", html)

	def test_the_closed_notice_asks_for_receipts_and_names_who_can_reopen(self):
		html = self.render("trip_closed.html", "Closed")
		self.assertIsNone(self.hrms_in(html))
		self.assertNotIn("claim", html.lower())
		self.assertIn("has been closed", html)
		self.assertIn("per diem and mileage from the trip", html)
		# It goes to every traveler alike, so it says which costs take a receipt (v1.556.2): a
		# traveler owed only per diem was sent looking for "each of your itemized receipts".
		self.assertIn("Per diem and mileage need no receipts", html)
		self.assertIn(
			"the itemized receipt for each cost you paid yourself or put on a company card is attached to it",
			html,
		)
		self.assertNotIn("each of your itemized receipts", html)
		self.assertIn("Only a Travel Coordinator can change a closed trip", html)
		self.assertIn("ask one to reopen the trip, then attach each receipt to the Receipt field", html)

	def test_the_guidelines_send_travelers_nowhere_production_lacks(self):
		with open(self.GUIDELINES, encoding="utf-8") as fh:
			source = fh.read()
		policy = self.code(source)
		self.assertIsNone(self.hrms_in(source))
		# Section 6: the process that works.
		self.assertIn("there is no claim to fill in", policy)
		self.assertIn("attach each itemized receipt to its cost on the Travel Trip form", policy)
		self.assertIn(
			"accounting reimburses your employee-paid costs, per diem, and mileage from the trip", policy
		)
		self.assertIn("a one-time reminder about three days after the trip ends", policy)
		self.assertIn("a closed trip can only be changed by a Travel Coordinator", policy)
		# Sections 5 and 6: what the per diem covers takes no receipt (Nik, 2026-09-28: "Meal
		# receipts shouldn't be required if paid by per diem"). Until v1.556.2 section 6 said to
		# "still include receipts" for meals and incidentals.
		self.assertNotIn("still include receipts", policy)
		self.assertIn("the meals and incidentals it covers need no receipts", policy)
		self.assertIn("the daily per-diem rate covers meals and incidentals, so they need no receipts", policy)
		self.assertIn(
			"Receipts are only for costs you paid yourself that the per diem does not cover, and for "
			"company-card purchases",
			policy,
		)
		self.assertIn("Per diem and mileage need no receipts", policy)
		self.assertIn("Accounting pays your per diem from the trip, and there is nothing to attach for it.", policy)
		# "Nothing to attach" is about being paid back. Someone owed only per diem may still have put
		# the hotel on a company card, and that receipt is needed for reconciliation.
		self.assertNotIn("there is nothing for you to attach.", policy)
		self.assertIn(
			"you have nothing to attach to be paid back, though a purchase you put on a company card "
			"still needs its receipt",
			policy,
		)
		# The receipts that are still required still are.
		self.assertIn("itemized receipts are mandatory for all out-of-pocket expenses", policy)
		self.assertIn("itemized receipts are also required for all purchases made on a company card", policy)
		# Section 4: no rate is promised. It lives in Travel Settings.mileage_rate (0 on production
		# when this was written, $0.725 a mile from 2026-09-28), and a figure here would go stale.
		self.assertNotIn("company rate", policy)
		self.assertIn("accounting reimburses them with the rest of your trip expenses", policy)
		# Section 5: the itinerary email carries no money (pre_travel_reminder.html).
		self.assertNotIn("itinerary email", policy)
		# The page keeps its shape: seven numbered sections, each with its callout.
		self.assertEqual(re.findall(r"<h2>(\d)\. ", policy), ["1", "2", "3", "4", "5", "6", "7"])
		self.assertEqual(policy.count('<div class="tg-system"> <b>In the system</b>'), 7)

	def test_nothing_sends_a_coordinator_to_install_hrms(self):
		"""hrms cannot be installed on production (``accounting_intake/actions/receipt_expense.py``),
		and travel finance does not need it: accounting reimburses from the trip. Until v1.556.2
		the Travel Settings notice ended "Install hrms to enable travel finance", and the refusal
		behind the hidden Create buttons "Install hrms to use this.\""""
		import ast

		install = re.compile(r"install\s*(?:<code>)?\s*hrms", re.I)
		path = os.path.join(APP_DIR, "travel_management", "doctype", "travel_settings", "travel_settings.js")
		with open(path, encoding="utf-8") as fh:
			# The comments say what it used to say.
			settings = "\n".join(line for line in fh.read().splitlines() if not line.strip().startswith("//"))
		self.assertIsNone(install.search(settings))
		self.assertIn("The per-diem and mileage rates below still apply.", settings)
		self.assertIn(
			"Travel finance is handled by accounting from the trip: it reimburses employee-paid costs, "
			"per diem and mileage from each trip, using the receipts attached to its costs.",
			settings,
		)
		self.assertIn("frm.toggle_display('expense_types_section', available);", settings)

		# The refusal's own words: every string in _require_hrms but its docstring.
		with open(os.path.join(APP_DIR, "travel_management", "api.py"), encoding="utf-8") as fh:
			tree = ast.parse(fh.read())
		guard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_require_hrms")
		words = " ".join(
			n.value for n in ast.walk(ast.Module(body=guard.body[1:], type_ignores=[]))
			if isinstance(n, ast.Constant) and isinstance(n.value, str)
		)
		self.assertIn("which is not installed on this site. Accounting reimburses travel from the trip instead.", words)
		self.assertIsNone(install.search(words))


class TestTheExpenseNudgeAsksOnlyForReceiptsDue(unittest.TestCase):
	"""``reminders.send_post_trip_expense_nudges``, run. Each traveler the trip shows as owed gets
	the nudge, but only someone with a cost they paid themselves is asked for receipts
	(``receipts_due``, and the subject). Per diem and mileage take none (Nik, 2026-09-28: "Meal
	receipts shouldn't be required if paid by per diem"); until v1.556.2 every recipient was told
	to attach receipts, and a traveler owed only per diem went looking for meal receipts."""

	REMINDERS = "erpnext_enhancements.travel_management.reminders"

	def setUp(self):
		install_site()
		frappe = sys.modules["frappe"]
		self.sent = []
		self.stamped = []

		def date_diff(a, b):
			return (sys.modules["frappe.utils"].getdate(a) - sys.modules["frappe.utils"].getdate(b)).days

		for patcher in (
			# patch.dict puts sys.modules back as it found it: the reminders module imported here,
			# over this stub, is dropped again after each test.
			mock.patch.dict(sys.modules),
			mock.patch.object(frappe.utils, "date_diff", date_diff, create=True),
			mock.patch.object(frappe.db, "set_value", lambda *a, **k: self.stamped.append(a), create=True),
			mock.patch.object(frappe, "format_value", lambda value, df=None: f"$ {value:,.2f}", create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		sys.modules.pop(self.REMINDERS, None)
		import importlib

		self.reminders = importlib.import_module(self.REMINDERS)
		for name, value in (
			("_in_maintenance_context", lambda: False),
			("_notifications_enabled", lambda: True),
			(
				"_traveler_recipients",
				lambda doc, employees=None: [
					types.SimpleNamespace(row=t, employee=t.employee)
					for t in doc.travelers
					if employees is None or t.employee in employees
				],
			),
			("_send", lambda recipient, subject, template, context, doc: self.sent.append(
				(recipient.employee, subject, template, context)
			)),
		):
			patcher = mock.patch.object(self.reminders, name, value)
			patcher.start()
			self.addCleanup(patcher.stop)

		# Ann is owed per diem and her miles; Bo paid for the room, and has per diem too; Cy paid
		# for the rental, but it is on a claim already; Dee is owed nothing.
		self.doc = FakeDoc(
			name="TRIP-9",
			purpose="Harbor install",
			status="Completed",
			end_date="2026-09-20",
			travelers=[
				FakeRow(name="T1", employee="EMP-A", per_diem_eligible=1, per_diem_amount=150),
				FakeRow(name="T2", employee="EMP-B", per_diem_eligible=1, per_diem_amount=150),
				FakeRow(name="T3", employee="EMP-C", per_diem_eligible=1, per_diem_amount=75),
				FakeRow(name="T4", employee="EMP-D", per_diem_eligible=0),
			],
			flights=[],
			accommodations=[FakeRow(paid_by="Employee", paid_by_traveler="EMP-B", cost=240)],
			ground_transport=[
				FakeRow(paid_by="Employee", paid_by_traveler="EMP-C", cost=90, expense_claim="HR-EXP-1"),
				FakeRow(paid_by="Company", cost=500),
			],
			freight=[],
			other_costs=[],
			mileage=[FakeRow(traveler="EMP-A", amount=31.18)],
		)
		SITE.trips["TRIP-9"] = self.doc
		SITE.get_all = {"Travel Trip": ["TRIP-9"]}

	def test_only_a_cost_they_paid_asks_for_receipts(self):
		self.reminders.send_post_trip_expense_nudges()
		self.assertEqual(
			[(who, subject, template, context["receipts_due"], context["unclaimed_amount"])
				for who, subject, template, context in self.sent],
			[
				("EMP-A", "Your travel reimbursement: Harbor install", "expense_nudge.html", False, "$ 181.18"),
				("EMP-B", "Attach your travel receipts: Harbor install", "expense_nudge.html", True, "$ 390.00"),
				("EMP-C", "Your travel reimbursement: Harbor install", "expense_nudge.html", False, "$ 75.00"),
			],
		)
		self.assertEqual({context["days_since_end"] for *_, context in self.sent}, {6})
		# Each was stamped before it was sent, and nobody owed nothing was.
		self.assertEqual(
			self.stamped,
			[("Trip Traveler", name, "expense_nudge_sent", 1) for name in ("T1", "T2", "T3")],
		)

	def test_the_costs_that_take_a_receipt(self):
		rows = self.reminders._unclaimed_costs
		self.assertEqual([r.cost for r in rows(self.doc, self.doc.travelers[1])], [240])
		# A row on a claim, a company-paid row, and someone else's: none of them.
		self.assertEqual(rows(self.doc, self.doc.travelers[2]), [])
		self.assertEqual(rows(self.doc, self.doc.travelers[0]), [])
		# The total still counts everything they are owed.
		self.assertAlmostEqual(self.reminders._unclaimed_total(self.doc, self.doc.travelers[0]), 181.18)
		self.assertAlmostEqual(self.reminders._unclaimed_total(self.doc, self.doc.travelers[1]), 390)


# --------------------------------------------------------------------------- links + planner


class TestLinks(unittest.TestCase):
	def setUp(self):
		self.doc = install_site()

	def test_the_emails_open_this_trip(self):
		self.assertEqual(
			notifications._base_context(self.doc)["itinerary_url"],
			"https://example.com/itinerary?trip=TRIP-1",
		)

	def test_the_calendar_invite_opens_this_trip(self):
		span = ics.trip_events_for_traveler(self.doc, self.doc.travelers[0])[0]
		self.assertEqual(span["url"], "https://example.com/itinerary?trip=TRIP-1")


class TestPlannerViewer(unittest.TestCase):
	def setUp(self):
		self.doc = install_site()
		SITE.get_all = {"Travel POI": []}

	def test_get_plan_says_who_is_looking(self):
		with (
			mock.patch.object(planner, "_lookups", return_value={}),
			mock.patch.object(travel, "_is_coordinator", return_value=False),
		):
			state = planner.get_plan("TRIP-1")["state"]
		self.assertIs(state["is_coordinator"], False)
		self.assertEqual(state["viewer_employee"], "EMP-B")
		self.assertIs(state["sheet_available"], True)

	def test_a_coordinator_with_no_employee_record(self):
		sys.modules["frappe"].session.user = "Administrator"
		with (
			mock.patch.object(planner, "_lookups", return_value={}),
			mock.patch.object(travel, "_is_coordinator", return_value=True),
		):
			state = planner.get_plan("TRIP-1")["state"]
		self.assertIs(state["is_coordinator"], True)
		self.assertIsNone(state["viewer_employee"])

	def test_the_saved_state_keeps_it(self):
		self.assertIn("state.update(_viewer())", inspect.getsource(planner.save_plan))


class TestEndpointShapes(unittest.TestCase):
	def test_signatures(self):
		self.assertEqual(
			list(inspect.signature(travel.get_trip_itinerary).parameters), ["trip", "as_employee"]
		)
		self.assertEqual(list(inspect.signature(travel.get_trip_views).parameters), ["trip"])
		self.assertEqual(
			list(inspect.signature(travel.preview_itinerary_email).parameters), ["trip", "employee"]
		)
		self.assertEqual(
			list(inspect.signature(travel.shape_itinerary).parameters),
			["doc", "viewing_employee", "poi_cache"],
		)
		# reminders.py imports _send by name and calls it with this signature.
		self.assertEqual(
			list(inspect.signature(notifications._send).parameters),
			["recipient", "subject", "template", "context", "doc", "attachments"],
		)

	def test_the_person_asked_for_must_be_a_string(self):
		# Frappe v16 checks a whitelisted function's arguments against its annotations
		# (frappe.utils.typing_validations) before calling it, so a dict or a list sent in a
		# JSON body is refused at the door, before _crew_member_or_throw even runs.
		itinerary = inspect.signature(travel.get_trip_itinerary).parameters
		preview = inspect.signature(travel.preview_itinerary_email).parameters
		self.assertEqual(itinerary["as_employee"].annotation, str | None)
		self.assertEqual(preview["employee"].annotation, str)
		for endpoint in (travel.get_trip_itinerary, travel.get_trip_views, travel.preview_itinerary_email):
			self.assertEqual(
				inspect.signature(endpoint).parameters["trip"].annotation, str, endpoint.__name__
			)

	def test_the_new_endpoints_are_whitelisted(self):
		with open(inspect.getsourcefile(travel), encoding="utf-8") as fh:
			source = fh.read()
		for name in (
			"get_trip_views",
			"get_trip_itinerary",
			"preview_itinerary_email",
			"get_itinerary_bootstrap",
			# /itinerary?view=trips (2026-09-28): every trip, for staff. It takes nothing.
			"get_all_trips",
		):
			self.assertRegex(source, re.compile(r"@frappe\.whitelist\(\)\ndef " + name + r"\("), name)
		self.assertEqual(list(inspect.signature(travel.get_all_trips).parameters), [])


# --------------------------------------------------------------------------- PR 3: contacts
#
# The contacts card (Nik, 2026-09-26): 911, the office travel desk, who booked the trip, the
# trip lead, the job-site contact and each hotel with its nearest urgent care. Contacts are
# not money, so everyone who can read the trip gets them — which is exactly why the one
# thing that must never ride along is a crew member's own phone, email, next of kin, home
# address or health details: `_trip_contacts` reads Employee records the crew could not open.

#: The fields each doctype has on the fake site (frappe.get_meta). Contact and Supplier lack
#: custom_phone_number here: a field a site does not have is never asked for.
META_FIELDS = {
	"User": {"full_name", "mobile_no", "phone", "email"},
	"Employee": {
		"employee_name",
		"user_id",
		"cell_number",
		"prefered_email",
		"company_email",
		"personal_email",
		"emergency_phone_number",
		"person_to_be_contacted",
		"relation",
		"current_address",
		"permanent_address",
		"blood_group",
		"health_details",
		"passport_number",
	},
	"Customer": {
		"customer_name",
		"customer_primary_contact",
		"customer_primary_address",
		"mobile_no",
		"email_id",
	},
	"Contact": {
		"full_name",
		"first_name",
		"last_name",
		"mobile_no",
		"phone",
		"email_id",
		"custom_mobile_number",
		"custom_email",
	},
	"Address": {
		"address_line1",
		"address_line2",
		"city",
		"state",
		"pincode",
		"country",
		"phone",
		"email_id",
		"custom_latitude",
		"custom_longitude",
	},
	"Supplier": {"supplier_name", "supplier_primary_address", "supplier_primary_contact", "mobile_no"},
	# This app's Custom Fields on Project (fixtures/custom_field.json): the job site's own
	# address and contact, which the card reads before the customer's.
	"Project": {
		"project_name",
		"customer",
		"custom_project_address",
		"custom_customer__lead_address",
		"custom_primary_first_name",
		"custom_customer_name",
		"custom_primary_phone",
		"custom_contact_phone",
		"custom_customer_phone",
		"custom_primary_email_address",
		"custom_customer_email",
	},
	"Opportunity": {
		"title",
		"customer_name",
		"opportunity_from",
		"party_name",
		"contact_person",
		"contact_display",
		"contact_mobile",
		"phone",
		"contact_email",
		"customer_address",
		"address_display",
	},
	"Lead": {"title", "lead_name", "company_name", "mobile_no", "phone", "email_id"},
}

#: What an Employee record also holds, and no payload may ever carry: a crew member's own
#: email, next of kin, home address, health details and passport. Each value is distinct so a
#: leak is findable, and Bo's cell is here too — only the trip lead's work cell is shown. (The
#: one other way an Employee's cell is shown is as the trip's booker, when that person booked
#: it and their User has no phone: ``test_who_booked_it``. The fixture's owner is the office.)
PERSONAL = {
	"EMP-A": {
		"personal_email": "ann.home@example.net",
		"emergency_phone_number": "(801) 555-9911",
		"person_to_be_contacted": "Pat Kinsman",
		"relation": "Spouse-Sentinel",
		"current_address": "12 Home Lane Sentinel",
		"permanent_address": "34 Old Home Road Sentinel",
		"blood_group": "BLOOD-SENTINEL",
		"health_details": "HEALTH-SENTINEL",
		"passport_number": "PASSPORT-SENTINEL",
	},
	"EMP-B": {"personal_email": "bo.home@example.net", "emergency_phone_number": "(801) 555-8822"},
}
PERSONAL_VALUES = [value for record in PERSONAL.values() for value in record.values()] + ["8015550102"]
PERSONAL_KEY_PARTS = (
	"personal",
	"emergency_",
	"relation",
	"person_to_be_contacted",
	"current_address",
	"permanent_address",
	"blood",
	"health",
	"passport",
	"prefered_email",
	"company_email",
	"cell_number",
	"user_id",
)


def personal_paths(value, path="$"):
	"""Every key in ``value`` that names a crew member's own details. ``emergency: "911"`` (the
	contacts card's first line) is the one key allowed to say "emergency"."""
	found = []
	if isinstance(value, dict):
		for key, child in value.items():
			text = str(key)
			if text == "emergency" and child != "911":
				found.append(f"{path}.{key}")
			elif any(part in text for part in PERSONAL_KEY_PARTS):
				found.append(f"{path}.{key}")
			found.extend(personal_paths(child, f"{path}.{key}"))
	elif isinstance(value, (list, tuple)):
		for index, child in enumerate(value):
			found.extend(personal_paths(child, f"{path}[{index}]"))
	return found


class ContactsSite:
	"""A site with the records the contacts card reads: the trip's owner, the lead's Employee
	(with every personal field filled), the Project's customer, its primary contact and
	address, and the hotel's Supplier, Address and Contact. ``get_value`` answers like v16:
	only the fields asked for, and an unknown column raises."""

	SETTINGS = {
		"travel_desk_label": "Sapphire travel desk",
		"travel_desk_phone": "+1 801-555-0142",
		"travel_desk_email": "travel@sapphire.example",
	}

	def __init__(self, test, doc):
		frappe = sys.modules["frappe"]
		doc.owner = "office@example.com"
		self.settings = dict(self.SETTINGS)
		self.requests = []
		self.records = {
			("User", "office@example.com"): {
				"full_name": "Olive Office",
				"mobile_no": "801-555-0000",
				"email": "office@example.com",
			},
			("Project", "PRJ-1"): {"project_name": "Harbor Fountain", "customer": "CUST-1"},
			("Customer", "CUST-1"): {
				"customer_name": "Harbor Resort",
				"customer_primary_contact": "CON-1",
				"customer_primary_address": "ADDR-SITE",
				"mobile_no": "(208) 555-0100",
				"email_id": "front@harbor.example",
			},
			("Contact", "CON-1"): {
				"full_name": "Sam Site",
				"custom_mobile_number": "",
				"mobile_no": "208-555-0199",
				"email_id": "sam@harbor.example",
			},
			("Address", "ADDR-SITE"): {
				"address_line1": "500 Harbor Way",
				"city": "Boise",
				"state": "ID",
				"pincode": "83702",
				"country": "United States",
			},
			# The room's `address` is the Address record's name, fetched from the hotel.
			("Supplier", "Hotel One"): {
				"supplier_primary_address": "1 Main",
				"supplier_primary_contact": "CON-H",
				"mobile_no": "7025550000",
			},
			("Address", "1 Main"): {
				"address_line1": "1 Main St",
				"city": "Las Vegas",
				"state": "NV",
				"phone": "(702) 555-0123",
				"custom_latitude": 36.17,
				"custom_longitude": -115.14,
			},
			("Contact", "CON-H"): {"mobile_no": "7025550999"},
		}
		SITE.employees["EMP-A"].update(cell_number="8015550101", **PERSONAL["EMP-A"])
		SITE.employees["EMP-B"].update(cell_number="8015550102", **PERSONAL["EMP-B"])
		original = frappe.db.get_value

		def get_value(doctype, name=None, fieldname=None, as_dict=False, **kwargs):
			fields = tuple(fieldname) if isinstance(fieldname, (list, tuple)) else (fieldname,)
			self.requests.append((doctype, name if isinstance(name, str) else "<filters>", fields))
			if doctype in ("Employee", "Travel POI", "Company"):
				return original(doctype, name, fieldname, as_dict, **kwargs)
			unknown = [f for f in fields if f and f != "name" and f not in META_FIELDS.get(doctype, ())]
			if unknown:
				raise Exception(f"(1054, \"Unknown column '{unknown[0]}' in 'SELECT'\")")
			record = self.records.get((doctype, name))
			if record is None:
				return None
			if isinstance(fieldname, (list, tuple)):
				return _dict({f: record.get(f) for f in fieldname})
			return record.get(fieldname)

		class Meta:
			def __init__(self, doctype):
				if doctype not in META_FIELDS:
					raise Exception(f"DocType {doctype} not found")
				self.fields = META_FIELDS[doctype]

			def has_field(self, fieldname):
				return fieldname in self.fields

		for patcher in (
			mock.patch.object(frappe.db, "get_value", get_value),
			mock.patch.object(
				frappe.db, "get_single_value", lambda doctype, field, *a, **k: self.settings.get(field)
			),
			mock.patch.object(
				frappe.db, "has_column", lambda doctype, column: column in META_FIELDS.get(doctype, ())
			),
			mock.patch.object(frappe, "get_meta", Meta, create=True),
		):
			patcher.start()
			test.addCleanup(patcher.stop)


class TestTripContacts(MoneyAssertions):
	def setUp(self):
		self.doc = install_site()
		self.site = ContactsSite(self, self.doc)

	def assertNothingPersonal(self, payload):
		self.assertEqual(personal_paths(payload), [], "a crew member's own details reached a payload")
		text = json.dumps(payload, default=str)
		for value in PERSONAL_VALUES:
			self.assertNotIn(value, text, f"{value} reached a payload")

	def test_the_whole_card(self):
		hotel = "1 Main St, Las Vegas, NV"
		self.assertEqual(
			travel._trip_contacts(self.doc),
			{
				"emergency": "911",
				"office": {
					"label": "Sapphire travel desk",
					"phone": "+1 801-555-0142",
					"email": "travel@sapphire.example",
				},
				"booked_by": {"name": "Olive Office", "phone": "801-555-0000", "email": "office@example.com"},
				"lead": {"name": "Ann", "phone": "8015550101"},
				"site": {
					"label": "Harbor Fountain",
					"contact_name": "Sam Site",
					"phone": "208-555-0199",
					"email": "sam@harbor.example",
					# One line, state and ZIP together, and no "United States": changed on purpose
					# (2026-09-27) from "500 Harbor Way, Boise, ID, 83702, United States".
					"address": "500 Harbor Way, Boise, ID 83702",
				},
				"hotels": [
					{
						"name": "Hotel One",
						"phone": "(702) 555-0123",
						"address": hotel,
						"urgent_care_url": "https://www.google.com/maps/search/?api=1&query="
						"urgent%20care%20near%201%20Main%20St%2C%20Las%20Vegas%2C%20NV",
						"directions_url": "https://www.google.com/maps/dir/?api=1&destination="
						"1%20Main%20St%2C%20Las%20Vegas%2C%20NV",
					}
				],
			},
		)

	def test_one_persons_card_has_only_their_hotels(self):
		self.assertEqual(
			[h["name"] for h in travel._trip_contacts(self.doc, "EMP-A")["hotels"]], ["Hotel One"]
		)
		# Cy has no room (both rooms are pinned to Ann and Bo), so no hotel on his card; the
		# rest of the card is the same for everyone on the trip.
		cy = travel._trip_contacts(self.doc, "EMP-C")
		self.assertEqual(cy["hotels"], [])
		self.assertEqual(cy["lead"], {"name": "Ann", "phone": "8015550101"})
		# A room for the whole crew is everyone's.
		doc = make_trip()
		doc.accommodations.append(
			FakeRow(
				name="R9",
				traveler=None,
				booking_group="g9",
				hotel_lodging="Crew House",
				check_in_date="2026-10-04",
			)
		)
		self.assertEqual([h["name"] for h in travel._trip_contacts(doc, "EMP-C")["hotels"]], ["Crew House"])
		# In check-in order.
		self.assertEqual(
			[h["name"] for h in travel._trip_contacts(doc)["hotels"]], ["Crew House", "Hotel One"]
		)

	def test_view_as_lists_the_hotels_their_itinerary_lists(self):
		"""View as used to rebuild a person's hotels from their check-in items, which a room with
		no dates yet does not have, while /itinerary lists every room pinned to them. The server
		now sends each person's list by the one rule."""
		self.doc.accommodations.append(
			FakeRow(name="R5", traveler="EMP-A", booking_group="g5", hotel_lodging="Harborview Suites")
		)
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			payload = travel.get_trip_views("TRIP-1")
			for employee in ("EMP-A", "EMP-B", "EMP-C"):
				itinerary = travel.get_trip_itinerary("TRIP-1", employee)
				self.assertEqual(
					payload["people_hotels"][employee],
					[hotel["name"] for hotel in itinerary["contacts"]["hotels"]],
					employee,
				)
		self.assertEqual(payload["people_hotels"]["EMP-A"], ["Hotel One", "Harborview Suites"])
		self.assertEqual(payload["people_hotels"]["EMP-C"], [])
		self.assertEqual(
			{h["name"] for h in payload["contacts"]["hotels"]}, {"Hotel One", "Harborview Suites"}
		)

	def test_the_lead_is_the_only_crew_member_with_a_number(self):
		card = travel._trip_contacts(self.doc)
		self.assertNotIn("8015550102", json.dumps(card), "Bo is not the lead: his cell is his own")
		doc = make_trip()
		for row in doc.travelers:
			row.is_trip_lead = 0
		self.assertIsNone(travel._trip_contacts(doc)["lead"])

	def test_the_employee_record_is_asked_for_two_fields_and_no_more(self):
		# The strongest form of the privacy rule: the personal fields are never even read.
		self.site.requests.clear()
		travel._trip_contacts(self.doc)
		asked = {
			field
			for doctype, _name, fields in self.site.requests
			if doctype == "Employee"
			for field in fields
		}
		self.assertLessEqual(asked, {"employee_name", "cell_number", "name"})
		asked_user = {
			field for doctype, _n, fields in self.site.requests if doctype == "User" for field in fields
		}
		self.assertLessEqual(asked_user, {"full_name", "mobile_no", "phone", "email"})

	def test_a_crew_members_own_details_reach_no_payload(self):
		frappe = sys.modules["frappe"]
		for coordinator in (False, True):
			with mock.patch.object(travel, "_is_coordinator", return_value=coordinator):
				views_payload = travel.get_trip_views("TRIP-1")
				self.assertNothingPersonal(views_payload)
				for as_employee in (None, "crew", "EMP-A", "EMP-B", "EMP-C"):
					self.assertNothingPersonal(travel.get_trip_itinerary("TRIP-1", as_employee))
				for employee in ("EMP-A", "EMP-B"):
					with mock.patch.object(frappe, "form_dict", _dict({"as": employee}), create=True):
						self.assertNothingPersonal(travel.ee_trip_sheet(self.doc))
				self.assertNothingPersonal(travel.ee_trip_sheet(self.doc))
			self.assertEqual(views_payload["contacts"]["lead"], {"name": "Ann", "phone": "8015550101"})
		# The itinerary email's contacts.
		SITE.rendered.clear()
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			travel.preview_itinerary_email("TRIP-1", "EMP-A")
		context = SITE.rendered[-1][1]
		for key in ("contacts", "contact_rows", "contact_links"):
			self.assertNothingPersonal(context[key])

	def test_the_payloads_carry_the_card_and_still_no_money(self):
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			views_payload = travel.get_trip_views("TRIP-1")
			itinerary = travel.get_trip_itinerary("TRIP-1", "EMP-B")
		self.assertEqual(views_payload["contacts"], travel._trip_contacts(self.doc))
		self.assertEqual(itinerary["contacts"], travel._trip_contacts(self.doc, "EMP-B"))
		self.assertNoMoney(views_payload)
		self.assertNoMoney(itinerary)

	def test_every_kind_of_job_with_its_records_missing_never_raises(self):
		frappe = sys.modules["frappe"]
		for doctype in ("Project", "Opportunity", "Lead", "Customer", "Supplier", None, ""):
			with self.subTest(doctype=doctype):
				doc = make_trip(travel_for_doctype=doctype, travel_for_name="GONE-1")
				doc.owner = "gone@example.com"
				card = travel._trip_contacts(doc)
				self.assertIsNone(card["site"])
				self.assertIsNone(card["booked_by"], "a deleted owner has no card line")
				self.assertEqual(card["emergency"], "911")
		# A record that is there but holds nobody to call is no card line either.
		self.site.records[("Project", "PRJ-2")] = {"project_name": "Bare", "customer": None}
		self.assertIsNone(travel._trip_contacts(make_trip(travel_for_name="PRJ-2"))["site"])

		# Every lookup failing at once — no meta, every read raising, settings raising — still
		# answers, with 911 and the hotel's name.
		def boom(*args, **kwargs):
			raise RuntimeError("the database is down")

		with (
			mock.patch.object(frappe, "get_meta", boom),
			mock.patch.object(frappe.db, "get_value", boom),
			mock.patch.object(frappe.db, "get_single_value", boom),
			mock.patch.object(frappe.db, "has_column", boom),
		):
			card = travel._trip_contacts(self.doc)
		self.assertEqual(card["emergency"], "911")
		self.assertEqual(
			[card[k] for k in ("office", "booked_by", "lead", "site")],
			[None, None, {"name": "Ann", "phone": None}, None],
		)
		self.assertEqual(card["hotels"][0]["name"], "Hotel One")
		self.assertIsNone(card["hotels"][0]["phone"])
		self.assertEqual(SITE.side_effects, [], "a guarded lookup logged or wrote something")

	def test_a_part_that_breaks_is_null_with_an_error_log(self):
		with mock.patch.object(travel, "_site_contact", side_effect=KeyError("bug")):
			card = travel._trip_contacts(self.doc)
		self.assertIsNone(card["site"])
		self.assertEqual(card["lead"], {"name": "Ann", "phone": "8015550101"})
		self.assertIn(("log_error", "Trip contacts"), SITE.side_effects)

	def test_an_opportunity_its_own_contact_then_its_customers(self):
		self.site.records[("Opportunity", "OPP-1")] = {
			"title": "Lobby fountain",
			"opportunity_from": "Customer",
			"party_name": "CUST-1",
			"contact_person": "CON-1",
			"contact_display": "Sam Site",
			"contact_mobile": "208-555-0111",
			"contact_email": "",
			"address_display": "77 Lake St<br>Boise, ID<br>",
		}
		site = travel._trip_contacts(make_trip(travel_for_doctype="Opportunity", travel_for_name="OPP-1"))[
			"site"
		]
		self.assertEqual(
			site,
			{
				"label": "Lobby fountain",
				"contact_name": "Sam Site",
				"phone": "208-555-0111",
				"email": "sam@harbor.example",  # the Contact's, as the Opportunity has none
				"address": "77 Lake St, Boise, ID",
			},
		)
		# Nothing of its own and no contact: the customer's.
		self.site.records[("Opportunity", "OPP-2")] = {
			"title": "Bare",
			"opportunity_from": "Customer",
			"party_name": "CUST-1",
		}
		site = travel._trip_contacts(make_trip(travel_for_doctype="Opportunity", travel_for_name="OPP-2"))[
			"site"
		]
		self.assertEqual(
			(site["label"], site["contact_name"], site["address"][:14]),
			("Bare", "Sam Site", "500 Harbor Way"),
		)

	def test_a_lead_and_a_customer(self):
		self.site.records[("Lead", "CRM-LEAD-1")] = {
			"title": "Kim's Garden Co",
			"lead_name": "Kim Lee",
			"company_name": "Kim's Garden Co",
			"phone": "385-555-0100",
			"email_id": "kim@garden.example",
		}
		self.assertEqual(
			travel._trip_contacts(make_trip(travel_for_doctype="Lead", travel_for_name="CRM-LEAD-1"))["site"],
			{
				"label": "Kim's Garden Co",
				"contact_name": "Kim Lee",
				"phone": "385-555-0100",
				"email": "kim@garden.example",
				"address": None,
			},
		)
		customer = travel._trip_contacts(make_trip(travel_for_doctype="Customer", travel_for_name="CUST-1"))[
			"site"
		]
		self.assertEqual((customer["label"], customer["contact_name"]), ("Harbor Resort", "Sam Site"))

	def test_a_projects_own_site_comes_before_its_customers(self):
		"""The customer's primary Address is its billing office: a Las Vegas install for a Boise
		customer sent the crew's "Directions to the job site" to Boise, and a Project with no
		customer had no job site at all."""
		project = self.site.records[("Project", "PRJ-1")]
		project["custom_project_address"] = "4500 Fountain Blvd, Las Vegas, NV"
		site = travel._trip_contacts(self.doc)["site"]
		self.assertEqual(
			site["address"], "4500 Fountain Blvd, Las Vegas, NV", "the site, not the billing office"
		)
		# Nobody of its own to reach: the customer's primary contact, whole.
		self.assertEqual((site["contact_name"], site["phone"]), ("Sam Site", "208-555-0199"))
		directions = [url for row in views.contact_list({"site": site}) for url, _label in row["links"]]
		self.assertEqual(directions, [views.maps_directions_url("4500 Fountain Blvd, Las Vegas, NV")])

		# Its own contact: name, phone and email together, never one person's name over
		# another's number.
		project.update(custom_primary_first_name="Jo Super", custom_contact_phone="702-555-0177")
		site = travel._trip_contacts(self.doc)["site"]
		self.assertEqual(
			site,
			{
				"label": "Harbor Fountain",
				"contact_name": "Jo Super",
				"phone": "702-555-0177",
				"email": None,
				"address": "4500 Fountain Blvd, Las Vegas, NV",
			},
		)
		# No typed address: the Customer / Lead Address link, then the customer's.
		del project["custom_project_address"]
		project["custom_customer__lead_address"] = "1 Main"
		self.assertEqual(travel._trip_contacts(self.doc)["site"]["address"], "1 Main St, Las Vegas, NV")
		del project["custom_customer__lead_address"]
		site = travel._trip_contacts(self.doc)["site"]
		self.assertEqual(site["address"], "500 Harbor Way, Boise, ID 83702")

		# No customer at all, and a site of its own.
		self.site.records[("Project", "PRJ-3")] = {
			"project_name": "Lakeside",
			"customer": None,
			"custom_customer_name": "Lee Lake",
			"custom_customer_phone": "435-555-0100",
			"custom_customer_email": "lee@lake.example",
			"custom_project_address": "9 Shore Rd, Provo, UT",
		}
		doc = make_trip(travel_for_name="PRJ-3")
		self.assertEqual(
			travel._trip_contacts(doc)["site"],
			{
				"label": "Lakeside",
				"contact_name": "Lee Lake",
				"phone": "435-555-0100",
				"email": "lee@lake.example",
				"address": "9 Shore Rd, Provo, UT",
			},
		)

	def test_an_address_is_read_once(self):
		self.site.requests.clear()
		travel._trip_contacts(self.doc)
		reads = [(doctype, name) for doctype, name, _fields in self.site.requests if doctype == "Address"]
		self.assertEqual(sorted(reads), [("Address", "1 Main"), ("Address", "ADDR-SITE")])
		# ...and still with its phone.
		self.assertEqual(travel._trip_contacts(self.doc)["hotels"][0]["phone"], "(702) 555-0123")

	def test_the_email_reaches_a_contact_with_only_an_email(self):
		"""The office line is on the card with a phone OR an email. The email's table used to
		show only a phone, so a desk set up with just an email was a row reading "—"."""
		self.site.settings = {"travel_desk_email": "travel@sapphire.example"}
		card = travel._trip_contacts(self.doc)
		rows = views.contact_rows(card)
		self.assertEqual(
			rows[1],
			["Travel desk", "Travel desk", ("mailto:travel@sapphire.example", "travel@sapphire.example")],
		)
		# A phone still wins, and a phone that is no number to dial is shown as typed.
		self.assertEqual(rows[2][2], ("tel:8015550000", "(801) 555-0000"))
		card["booked_by"]["phone"] = "1-800-FLOWERS"
		self.assertEqual(views.contact_rows(card)[2][2], "1-800-FLOWERS")
		# Only a plain address is made a link: ee.table writes it into href unescaped.
		for odd in ('x"onmouseover="alert(1)@evil.example', "travel desk at the office", "a@b"):
			card["office"]["email"] = odd
			self.assertEqual(views.contact_rows(card)[1][2], odd, odd)
		self.assertIsNone(views.mailto_href("javascript:alert(1)//@x.example"))
		self.assertEqual(
			views.mailto_href(" o'neil+trips@corp.example "), "mailto:o%27neil%2Btrips@corp.example"
		)

	def test_the_office_line_needs_a_phone_or_an_email(self):
		self.site.settings = {"travel_desk_label": "Travel desk only"}
		self.assertIsNone(travel._trip_contacts(self.doc)["office"])
		self.site.settings = {"travel_desk_email": "travel@sapphire.example"}
		self.assertEqual(
			travel._trip_contacts(self.doc)["office"],
			{"label": "Travel desk", "phone": None, "email": "travel@sapphire.example"},
		)

	def test_who_booked_it(self):
		doc = make_trip()
		doc.owner = "Administrator"
		self.assertIsNone(travel._trip_contacts(doc)["booked_by"])
		# No phone on the User: the owner's own Employee's work cell.
		self.site.records[("User", "bo@example.com")] = {"full_name": "Bo B", "email": "bo@example.com"}
		doc.owner = "bo@example.com"
		self.assertEqual(
			travel._trip_contacts(doc)["booked_by"],
			{"name": "Bo B", "phone": "8015550102", "email": "bo@example.com"},
		)

	def test_a_hotel_with_no_address_is_searched_by_name(self):
		del self.site.records[("Address", "1 Main")]
		del self.site.records[("Supplier", "Hotel One")]
		(hotel,) = travel._trip_contacts(self.doc)["hotels"]
		self.assertEqual((hotel["name"], hotel["phone"], hotel["address"]), ("Hotel One", None, None))
		self.assertTrue(hotel["urgent_care_url"].endswith("query=urgent%20care%20near%20Hotel%20One"))
		self.assertTrue(hotel["directions_url"].endswith("destination=Hotel%20One"))

	def test_a_hotels_phone_falls_back_to_its_contact(self):
		self.site.records[("Address", "1 Main")].pop("phone")
		self.assertEqual(travel._trip_contacts(self.doc)["hotels"][0]["phone"], "7025550999")

	def test_the_card_as_rows_for_print_and_email(self):
		card = travel._trip_contacts(self.doc)
		rows = views.contact_list(card)
		self.assertEqual(
			[(r["role"], r["name"], r["tel"]) for r in rows],
			[
				("Emergency", "Call 911", "tel:911"),
				("Travel desk", "Sapphire travel desk", "tel:+18015550142"),
				("Booked by", "Olive Office", "tel:8015550000"),
				("Trip lead", "Ann", "tel:8015550101"),
				("Job site", "Sam Site", "tel:2085550199"),
				("Hotel", "Hotel One", "tel:7025550123"),
			],
		)
		self.assertEqual(rows[4]["detail"], "Harbor Fountain")
		email = views.contact_rows(card)
		self.assertEqual(email[0], ["Emergency", "Call 911", ("tel:911", "911")])
		self.assertEqual(email[3], ["Trip lead", "Ann", ("tel:8015550101", "(801) 555-0101")])
		self.assertEqual(email[4][1], "Sam Site (Harbor Fountain)")
		self.assertEqual(
			[label for _url, label in views.contact_links(card)],
			["Directions to the job site", "Nearest urgent care: Hotel One", "Directions: Hotel One"],
		)
		# A card with nothing on it but 911 is still a card.
		self.assertEqual(views.contact_rows({})[0][0], "Emergency")

	def test_tel_links_are_digits(self):
		self.assertEqual(views.tel_href("(801) 555-0100"), "tel:8015550100")
		self.assertEqual(views.tel_href("+1 801.555.0100"), "tel:+18015550100")
		self.assertEqual(views.tel_href("801-555-0100 ext. 4"), "tel:8015550100")
		self.assertIsNone(views.tel_href("call the front desk"))
		self.assertIsNone(views.tel_href(None))


class TestTheItineraryEmailShowsTheCard(unittest.TestCase):
	"""The real template, rendered with the real email macros (``_components.html``), from the
	context the send and the preview build (``notifications._itinerary_email``)."""

	def setUp(self):
		try:
			import jinja2
		except ImportError:  # pragma: no cover
			self.skipTest("jinja2 not installed")
		self.doc = install_site()
		self.site = ContactsSite(self, self.doc)

	def render(self, employee):
		from jinja2 import Environment, FileSystemLoader

		recipient = _dict(
			row=next(t for t in self.doc.travelers if t.employee == employee),
			employee=employee,
			employee_name=employee,
			email="x@example.com",
			user_id=None,
		)
		email = notifications._itinerary_email(self.doc, recipient)
		env = Environment(loader=FileSystemLoader(os.path.dirname(APP_DIR)), autoescape=False)
		template = env.get_template(TestTheEmailTemplateCarriesNoMoney.TEMPLATE)
		context = dict(email["context"], recipient=recipient, frappe=types.SimpleNamespace(format_date=str))
		return template.render(**context), email["context"]

	def test_the_contacts_block(self):
		html, context = self.render("EMP-B")
		self.assertIn(">Contacts</h2>", html)
		self.assertIn('href="tel:911"', html)
		self.assertIn('href="tel:8015550101"', html)  # the trip lead
		self.assertIn("(801) 555-0101", html)
		self.assertIn("Nearest urgent care: Hotel One", html)
		self.assertIn(views.maps_search_url("urgent care near 1 Main St, Las Vegas, NV"), html)
		self.assertIn("Sapphire travel desk", html)
		self.assertEqual(context["contacts"], travel._trip_contacts(self.doc, "EMP-B"))
		for value in PERSONAL_VALUES:
			self.assertNotIn(value, html)

	def test_someone_with_no_room_gets_no_hotel(self):
		html, _context = self.render("EMP-C")
		self.assertIn('href="tel:911"', html)
		self.assertNotIn("Nearest urgent care", html)

	def test_a_desk_with_only_an_email_can_be_written_to(self):
		self.site.settings = {
			"travel_desk_label": "Travel desk",
			"travel_desk_email": "travel@sapphire.example",
		}
		html, _context = self.render("EMP-B")
		self.assertIn(">Phone / email</th>", html)
		self.assertIn('href="mailto:travel@sapphire.example"', html)

	def test_a_send_to_the_crew_looks_each_hotel_up_once(self):
		"""Each person's email carries their own hotels; the whole send looks every hotel up
		once, as it shares the Places (``poi_cache``), not once per person. Since 2026-09-27 the
		same cache also serves each email's hotel items and its calendar invite's LOCATION, which
		resolve the room's Address too: still one read of it for the whole send."""
		SITE.allow_side_effects = True
		self.site.requests.clear()
		self.assertEqual(notifications.send_itinerary_emails(self.doc, force=True), ["EMP-A", "EMP-B"])
		self.assertEqual([n for d, n, _f in self.site.requests if d == "Supplier"], ["Hotel One"])
		self.assertEqual(
			[n for d, n, _f in self.site.requests if d == "Address" and n == "1 Main"], ["1 Main"]
		)
		sent = [effect[1] for effect in SITE.side_effects if effect[0] == "sendmail"]
		for mail in sent:
			(invite,) = mail["attachments"]
			self.assertIn("LOCATION:1 Main St\\, Las Vegas\\, NV", invite["fcontent"])


# --------------------------------------------------------------------------- 2026-09-27: room addresses


class TestTheRoomsStreetAddress(MoneyAssertions):
	"""A room's ``address`` is ``fetch_from: hotel_lodging.supplier_primary_address``, a Link, so
	the row holds the Address record's NAME. That name was shown where the street belonged
	("Harborview Suites-Billing") from v1.15.0 until 2026-09-27: Plan a Trip's Overview and View
	as, ``/itinerary``, the calendar invite the emails attach (the emails' own text never printed
	a room's address) and the planner's room card.
	The fixture's rooms carry "1 Main", which reads as a street whether it names a record or not,
	which is why nothing caught it: this class names the record the way prod does."""

	RECORD = "Hotel One-Billing"
	STREET = "1 Main St, Las Vegas, NV"

	def setUp(self):
		self.doc = install_site()
		self.site = ContactsSite(self, self.doc)
		# As on prod: the rows hold the Address record's name, and so does the Supplier.
		self.site.records[("Address", self.RECORD)] = dict(self.site.records[("Address", "1 Main")])
		self.site.records[("Supplier", "Hotel One")]["supplier_primary_address"] = self.RECORD
		for row in self.doc.accommodations:
			row.address = self.RECORD

	def hotel_addresses(self, days):
		return [item["address"] for item in items_of(days) if item["type"].startswith("hotel_")]

	def address_reads(self):
		"""Every read of the rooms' Address (the job site's own Address is read once more)."""
		return [
			name for doctype, name, _f in self.site.requests if doctype == "Address" and name == self.RECORD
		]

	def room_card_address(self):
		return planner.get_state(self.doc)["bookings"]["accommodations"][0]["address"]

	def test_every_itinerary_prints_the_street_not_the_record(self):
		for viewer in (None, "EMP-A", "EMP-B"):
			with self.subTest(viewer=viewer):
				days = travel.shape_itinerary(self.doc, viewer)["days"]
				self.assertEqual(set(self.hotel_addresses(days)), {self.STREET})
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			views_payload = travel.get_trip_views("TRIP-1")
			itineraries = [travel.get_trip_itinerary("TRIP-1", who) for who in (None, "crew", "EMP-A")]
			preview = travel.preview_itinerary_email("TRIP-1", "EMP-A")
		self.assertEqual(set(self.hotel_addresses(views_payload["whole"])), {self.STREET})
		self.assertEqual(set(self.hotel_addresses(views_payload["people"]["EMP-A"])), {self.STREET})
		for itinerary in itineraries:
			self.assertEqual(set(self.hotel_addresses(itinerary["days"])), {self.STREET})
		# The calendar invite the email carries, and the events View as lists from it.
		(stay,) = [event for event in preview["events"] if "Check-in" in event["summary"]]
		self.assertEqual(stay["location"], self.STREET)
		self.assertIn("LOCATION:1 Main St\\, Las Vegas\\, NV", preview["ics"])
		# The planner's room card.
		(room,) = planner.get_state(self.doc)["bookings"]["accommodations"]
		self.assertEqual(room["address"], self.STREET)
		for payload in (views_payload, *itineraries, preview, room):
			self.assertNotIn(self.RECORD, json.dumps(payload, default=str))
		self.assertNoMoney(views_payload)

	def test_text_no_address_is_named_is_printed_as_typed(self):
		"""A value that names no Address is text somebody typed, and is sent as it is — which is
		also why every other class here, whose base stub has no Address at all, still reads the
		fixture's "1 Main"."""
		for row in self.doc.accommodations:
			row.address = "12 Typed Rd, Reno NV"
		days = travel.shape_itinerary(self.doc)["days"]
		self.assertEqual(set(self.hotel_addresses(days)), {"12 Typed Rd, Reno NV"})
		self.assertEqual(self.room_card_address(), "12 Typed Rd, Reno NV")
		# An Address with nothing written on it is no address, and never its own name.
		self.site.records[("Address", self.RECORD)] = {}
		for row in self.doc.accommodations:
			row.address = self.RECORD
		self.assertEqual(set(self.hotel_addresses(travel.shape_itinerary(self.doc)["days"])), {None})
		self.assertEqual(self.room_card_address(), "")
		# No address at all.
		for row in self.doc.accommodations:
			row.address = None
		self.assertEqual(set(self.hotel_addresses(travel.shape_itinerary(self.doc)["days"])), {None})

	def test_a_read_that_fails_never_stops_the_itinerary(self):
		frappe = sys.modules["frappe"]
		original = frappe.db.get_value

		def get_value(doctype, *args, **kwargs):
			if doctype == "Address":
				raise RuntimeError("the database is down")
			return original(doctype, *args, **kwargs)

		with mock.patch.object(frappe.db, "get_value", get_value):
			days = travel.shape_itinerary(self.doc)["days"]
			room = self.room_card_address()
		self.assertEqual(set(self.hotel_addresses(days)), {self.RECORD}, "the value as stored")
		self.assertEqual(room, self.RECORD)
		self.assertEqual(SITE.side_effects, [], "nothing logged for a line of text")

	def test_one_read_of_the_address_per_answer(self):
		"""Every person's itinerary on the views, the contacts card and the Map share one cache:
		the rooms' Address is read once for the whole payload, however many rooms and people name
		it. The same for /itinerary, the sheet, the planner's cards and the email preview (whose
		contacts card read every hotel again, uncached, until the preview looked the hotels up
		through its cache as the send does)."""
		frappe = sys.modules["frappe"]
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			for name, call in (
				("get_trip_views", lambda: travel.get_trip_views("TRIP-1")),
				("get_trip_itinerary", lambda: travel.get_trip_itinerary("TRIP-1", "crew")),
				("get_state", lambda: planner.get_state(self.doc)),
				("preview_itinerary_email", lambda: travel.preview_itinerary_email("TRIP-1", "EMP-A")),
			):
				with self.subTest(name):
					self.site.requests.clear()
					call()
					self.assertEqual(self.address_reads(), [self.RECORD])
			with (
				mock.patch.object(frappe, "form_dict", _dict({}), create=True),
				mock.patch.object(frappe.local, "request", object(), create=True),
				mock.patch.object(frappe.flags, "ignore_print_permissions", False, create=True),
			):
				self.site.requests.clear()
				travel.ee_trip_sheet(self.doc)
				self.assertEqual(self.address_reads(), [self.RECORD])


class TestOneLineAddress(unittest.TestCase):
	"""An address as one line (2026-09-27): "1 Harbor Dr, San Diego, CA 92101" — state and ZIP
	together, and the country only when it is not home. It was "1 Harbor Dr, San Diego, CA,
	92101, United States", which wrapped every hotel's line on the sheet and the card."""

	def test_state_and_zip_are_one_part_and_home_is_not_named(self):
		line = views.one_line_address
		self.assertEqual(
			line("1 Harbor Dr", None, "San Diego", "CA", "92101", "United States"),
			"1 Harbor Dr, San Diego, CA 92101",
		)
		self.assertEqual(
			line(" 1 Harbor Dr ", "Suite 200", "San Diego", "CA", " 92101", "united states"),
			"1 Harbor Dr, Suite 200, San Diego, CA 92101",
		)
		self.assertEqual(line("1 Harbor Dr", None, "Reno", None, "89501"), "1 Harbor Dr, Reno, 89501")
		self.assertEqual(line("1 Harbor Dr", None, "San Diego", "CA"), "1 Harbor Dr, San Diego, CA")
		self.assertIsNone(line())
		self.assertIsNone(line("", " ", None, "", None, "United States"))

	def test_the_country_is_named_only_when_it_is_not_home(self):
		line = views.one_line_address
		vancouver = ("1055 Canada Pl", None, "Vancouver", "BC", "V6C 0C3", "Canada")
		# Home unknown: only "United States" goes unsaid.
		self.assertEqual(line(*vancouver), "1055 Canada Pl, Vancouver, BC V6C 0C3, Canada")
		self.assertEqual(line(*vancouver, home_country="United States"), line(*vancouver))
		# A company at home in Canada names the United States and not Canada.
		self.assertEqual(line(*vancouver, home_country="Canada"), "1055 Canada Pl, Vancouver, BC V6C 0C3")
		self.assertEqual(
			line("1 Harbor Dr", None, "San Diego", "CA", "92101", "United States", home_country="Canada"),
			"1 Harbor Dr, San Diego, CA 92101, United States",
		)

	def test_home_is_the_sites_country_read_safely(self):
		frappe = sys.modules["frappe"]
		install_site()
		settings = {"country": "Canada", "default_company": "SF"}
		asked = []

		def get_cached_value(doctype, name=None, fieldname=None, *a, **k):
			asked.append((doctype, name, fieldname))
			return {"SF": "Mexico"}.get(name)

		def get_single_value(doctype, field, *args, **kwargs):
			self.assertEqual(doctype, "Global Defaults")
			return settings.get(field)

		with (
			mock.patch.object(frappe.db, "get_single_value", get_single_value),
			mock.patch.object(frappe, "get_cached_value", get_cached_value, create=True),
		):
			self.assertEqual(travel._home_country(), "Canada", "Global Defaults' country first")
			self.assertEqual(asked, [])
			settings.pop("country")
			self.assertEqual(travel._home_country(), "Mexico", "else the default company's")
			self.assertEqual(asked, [("Company", "SF", "country")])
			settings.pop("default_company")
			self.assertIsNone(travel._home_country())

		def boom(*args, **kwargs):
			raise RuntimeError("the database is down")

		with mock.patch.object(frappe.db, "get_single_value", boom):
			self.assertIsNone(travel._home_country())

	def test_the_card_and_the_itinerary_use_it(self):
		doc = install_site()
		site = ContactsSite(self, doc)
		# Global Defaults says Canada: the Boise site's country is named, a Canadian hotel's is not.
		site.settings["country"] = "Canada"
		site.records[("Address", "1 Main")].update(
			address_line1="1055 Canada Pl", city="Vancouver", state="BC", pincode="V6C 0C3", country="Canada"
		)
		card = travel._trip_contacts(doc)
		self.assertEqual(card["site"]["address"], "500 Harbor Way, Boise, ID 83702, United States")
		self.assertEqual(card["hotels"][0]["address"], "1055 Canada Pl, Vancouver, BC V6C 0C3")
		items = items_of(travel.shape_itinerary(doc)["days"])
		addresses = {i["address"] for i in items if i["type"] == "hotel_checkin"}
		self.assertEqual(addresses, {"1055 Canada Pl, Vancouver, BC V6C 0C3"})


# --------------------------------------------------------------------------- PR 3: the map


class TestTripPlaces(MoneyAssertions):
	HOTELS = {"Hotel One": {"address": "1 Main St, Las Vegas, NV", "lat": 36.17, "lng": -115.14}}

	def setUp(self):
		self.doc = install_site()

	def places(self, doc=None, hotels=HOTELS):
		doc = doc or self.doc
		names = [p["employee_name"] for p in views.crew(doc)]
		return views.trip_places(travel.shape_itinerary(doc)["days"], names, hotels)

	def test_a_realistic_trip(self):
		places, legs = self.places()
		by_key = {p["key"]: p for p in places}
		self.assertEqual(
			[(p["key"], p["kind"], p["label"], p["query"]) for p in places],
			[
				("airport-1", "airport", "PHX", "PHX airport"),
				("airport-2", "airport", "LAS", "LAS airport"),
				("hotel-1", "hotel", "Hotel One", "1 Main St, Las Vegas, NV"),
				# In itinerary order: Tuesday's 9:30 stop comes up before the 10:00 delivery.
				("stop-1", "stop", "The site", "The site"),
				("freight-1", "freight", "Site", "Site"),
			],
		)
		phx, las = by_key["airport-1"], by_key["airport-2"]
		# Two flights leave PHX (the whole crew's the evening before, then Ann and Bo's): one
		# place, both days, the time on the first of them.
		self.assertEqual(phx["days"], ["2026-10-04", "2026-10-05"])
		self.assertEqual(phx["first_time"], "18:00")
		self.assertEqual(phx["who"], ["Ann", "Bo", "Cy"])
		self.assertEqual(phx["group"], "row:F3")
		self.assertEqual((phx["lat"], phx["lng"]), (None, None))
		# The rental is picked up at "LAS": the airport, not a second pin.
		self.assertEqual(las["days"], ["2026-10-04", "2026-10-05"])
		self.assertIsNone(las["first_time"], "the first flight's landing time is not known")
		self.assertNotIn("pickup", {p["kind"] for p in places})
		hotel = by_key["hotel-1"]
		self.assertEqual((hotel["lat"], hotel["lng"]), (36.17, -115.14))
		# Every night of the stay, not only the check-in and check-out: Tuesday's chip shows the
		# site stop and the hotel the crew starts and ends that day at. Changed on purpose
		# (2026-09-27); it was ["2026-10-05", "2026-10-08"].
		self.assertEqual(hotel["days"], ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"])
		self.assertEqual(hotel["who"], ["Ann", "Bo"])
		self.assertEqual(hotel["group"], "g3")
		self.assertEqual(by_key["freight-1"]["who"], ["Cy"])
		self.assertEqual(by_key["freight-1"]["first_time"], "10:00")
		stop = by_key["stop-1"]
		# A stop's Place with a point keeps it; two visits are one place.
		self.assertEqual((stop["lat"], stop["lng"]), (36.1, -115.1))
		self.assertEqual(stop["days"], ["2026-10-06", "2026-10-07"])
		self.assertEqual(stop["first_time"], "09:30")
		self.assertIsNone(stop["group"])
		self.assertEqual(
			legs,
			[
				{
					"day": "2026-10-04",
					"from": "airport-1",
					"to": "airport-2",
					"kind": "flight",
					"who": ["Ann", "Bo", "Cy"],
				},
				{
					"day": "2026-10-05",
					"from": "airport-1",
					"to": "airport-2",
					"kind": "flight",
					"who": ["Ann", "Bo"],
				},
			],
		)
		for place in places:
			self.assertEqual(
				set(place),
				{"key", "kind", "label", "query", "lat", "lng", "days", "first_time", "who", "group"},
			)

	def test_drives_and_their_ends(self):
		doc = make_trip()
		doc.ground_transport = [
			FakeRow(
				name="G2",
				traveler=None,
				booking_group="g5",
				transport_type="Company Fleet",
				pickup_location="las",  # lower case: a place name, not a code
				dropoff_location="500 Harbor Way, Boise",
				pickup_datetime="2026-10-06 00:00:00",  # no time given
				arrival_datetime="2026-10-06 11:45:00",
			),
			FakeRow(
				name="G3",
				traveler="EMP-A",
				booking_group="g6",
				transport_type="Personal Vehicle",
				pickup_location="500 HARBOR WAY, BOISE",  # the same place, typed differently
				dropoff_location="PHX",
				pickup_datetime="2026-10-08 07:00:00",
			),
		]
		places, legs = self.places(doc)
		kinds = [(p["key"], p["label"]) for p in places if p["kind"] in ("pickup", "dropoff")]
		self.assertEqual(
			kinds,
			[
				("pickup-1", "las"),
				("dropoff-1", "500 Harbor Way, Boise"),
				("pickup-2", "500 HARBOR WAY, BOISE"),
			],
		)
		pickup = next(p for p in places if p["key"] == "pickup-1")
		self.assertIsNone(pickup["first_time"], "midnight is no time given")
		dropoff = next(p for p in places if p["key"] == "dropoff-1")
		self.assertEqual(dropoff["first_time"], "11:45")
		drives = [leg for leg in legs if leg["kind"] == "drive"]
		self.assertEqual(
			drives,
			[
				{
					"day": "2026-10-06",
					"from": "pickup-1",
					"to": "dropoff-1",
					"kind": "drive",
					"who": ["Ann", "Bo", "Cy"],
				},
				# Driven home to the airport: PHX's place, the one the flights left from.
				{"day": "2026-10-08", "from": "pickup-2", "to": "airport-1", "kind": "drive", "who": ["Ann"]},
			],
		)

	def test_a_hotel_is_on_every_night_of_each_stay(self):
		doc = make_trip()
		# A second stay at the same hotel after a night away: its nights, and not the gap.
		doc.accommodations.append(
			FakeRow(
				name="R3",
				traveler="EMP-C",
				booking_group="g7",
				hotel_lodging="Hotel One",
				address="1 Main",
				check_in_date="2026-10-10",
				check_in_time="16:00:00",
				check_out_date="2026-10-12",
			)
		)
		# A room with one date only is on that day.
		doc.accommodations.append(
			FakeRow(
				name="R4",
				traveler="EMP-C",
				booking_group="g8",
				hotel_lodging="Motel 9",
				check_in_date="2026-10-06",
			)
		)
		places, _legs = self.places(doc)
		hotel = next(p for p in places if p["label"] == "Hotel One")
		self.assertEqual(
			hotel["days"],
			[
				"2026-10-05",
				"2026-10-06",
				"2026-10-07",
				"2026-10-08",
				"2026-10-10",
				"2026-10-11",
				"2026-10-12",
			],
		)
		self.assertIsNone(hotel["first_time"], "the time is the first check-in's, not a later stay's")
		self.assertEqual(next(p for p in places if p["label"] == "Motel 9")["days"], ["2026-10-06"])

	def test_a_placeholder_is_never_a_place(self):
		"""The office types "TBD" for a drop-off not known yet. Three capitals looked like an
		airport code, so "TBD airport" was looked up (and answered with somewhere) and a drive
		was drawn to it. A code is an airport only when one of the trip's flights uses it."""
		doc = make_trip()
		doc.ground_transport = [
			FakeRow(
				name="G2",
				traveler=None,
				booking_group="g5",
				transport_type="Rental/Third Party",
				pickup_location="LAS",  # a flight lands there: the airport
				dropoff_location="TBD",
				pickup_datetime="2026-10-06 09:00:00",
			),
			FakeRow(
				name="G3",
				traveler=None,
				booking_group="g6",
				transport_type="Company Fleet",
				pickup_location="BYU",  # three capitals, no flight: a place as typed
				dropoff_location="n/a",
				pickup_datetime="2026-10-07 09:00:00",
			),
		]
		doc.flights[2].arrival_airport = "TBA"
		# The flight says "LAS Airport": "LAS" typed as the rental's pick-up is still that airport.
		doc.flights[0].arrival_airport = doc.flights[1].arrival_airport = "LAS Airport"
		doc.freight[0].deliver_to = "TBD"
		places, legs = self.places(doc)
		queries = {(p["kind"], p["query"].casefold()) for p in places}
		self.assertIn(("airport", "las airport"), queries)
		self.assertNotIn(("pickup", "las"), queries)
		las = next(p for p in places if p["query"].casefold() == "las airport")
		self.assertIn("2026-10-06", las["days"], "the pick-up is at the airport the flight lands at")
		self.assertIn(("pickup", "byu"), queries)
		for placeholder in ("tbd", "tba", "n/a", "tbd airport", "tba airport"):
			self.assertNotIn(placeholder, {query for _kind, query in queries}, placeholder)
		self.assertNotIn("freight", {p["kind"] for p in places})
		self.assertEqual([leg["kind"] for leg in legs], ["flight"], "no leg to a place that is not there")
		self.assertEqual(views._typed_airport_code("TBD"), True, "the shape alone is not enough")

	def test_a_hotel_with_no_address_is_asked_for_by_name(self):
		places, _legs = self.places(hotels=None)
		hotel = next(p for p in places if p["kind"] == "hotel")
		self.assertEqual((hotel["query"], hotel["lat"], hotel["lng"]), ("Hotel One", None, None))

	def test_nothing_to_place_is_left_off(self):
		doc = make_trip(
			itinerary=[FakeRow(name="A9", date="2026-10-06", activity_description="Call the office")]
		)
		doc.freight[0].deliver_to = None
		places, _legs = self.places(doc)
		self.assertNotIn("stop", {p["kind"] for p in places})
		self.assertNotIn("freight", {p["kind"] for p in places})

	def test_the_views_payload_carries_the_map_and_no_money(self):
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			payload = travel.get_trip_views("TRIP-1")
		self.assertEqual(
			[p["key"] for p in payload["places"]],
			["airport-1", "airport-2", "hotel-1", "stop-1", "freight-1"],
		)
		self.assertEqual(len(payload["legs"]), 2)
		self.assertEqual(payload["maps"], {"api_key": "", "map_id_light": "", "map_id_dark": ""})
		self.assertEqual(payload["sheet_url"], views.trip_sheet_url("TRIP-1"))
		self.assertEqual(
			payload["people_sheet_urls"],
			{e: views.trip_sheet_url("TRIP-1", e) for e in ("EMP-A", "EMP-B", "EMP-C")},
		)
		self.assertNoMoney(payload)

	def test_the_views_hand_the_map_the_hotels_street_address(self):
		ContactsSite(self, self.doc)
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			payload = travel.get_trip_views("TRIP-1")
		hotel = next(p for p in payload["places"] if p["kind"] == "hotel")
		self.assertEqual(
			(hotel["query"], hotel["lat"], hotel["lng"]), ("1 Main St, Las Vegas, NV", 36.17, -115.14)
		)

	def test_the_map_key_is_the_one_every_signed_in_user_reads(self):
		frappe = sys.modules["frappe"]
		settings = {"google_maps_api_key": "browser-key", "google_maps_map_id_light": "light-id"}
		with (
			mock.patch.object(
				frappe.db, "get_single_value", lambda doctype, field, *a, **k: settings.get(field)
			),
			mock.patch.object(travel, "_is_coordinator", return_value=False),
		):
			payload = travel.get_trip_views("TRIP-1")
			self.assertEqual(payload["maps"], travel.get_maps_config())
		self.assertEqual(
			payload["maps"], {"api_key": "browser-key", "map_id_light": "light-id", "map_id_dark": ""}
		)


# --------------------------------------------------------------------------- PR 3: the trip sheet

SHEET_TEMPLATE = os.path.join(APP_DIR, "travel_management", "print_formats", "trip_sheet.html")
PLAN_A_TRIP_JS = os.path.join(APP_DIR, "travel_management", "page", "plan_a_trip", "plan_a_trip.js")
SHEET_SETUP = "erpnext_enhancements.travel_management.setup_print_formats"


def _sheet_source():
	with open(SHEET_TEMPLATE, encoding="utf-8") as fh:
		return fh.read()


class TestTripSheet(MoneyAssertions):
	"""The Trip Sheet: its payload (``views.build_trip_sheet`` through ``ee_trip_sheet``) and
	the real template rendered with jinja2 and the real ``ps_*`` globals — nothing else compiles
	a print format before somebody is holding the paper."""

	#: Every number on the fixture: each person's own on the whole trip's sheet.
	REFS = ("PNR-A", "PNR-B", "H-A", "H-B", "RC-1", "PRO-1")

	def setUp(self):
		try:
			import jinja2
		except ImportError:  # pragma: no cover
			self.skipTest("jinja2 not installed")
		self.doc = install_site()
		self.site = ContactsSite(self, self.doc)

	def sheet(self, coordinator=False, employee=None, doc=None, request=True, emailed=False):
		"""``ee_trip_sheet`` as the print view calls it: in a web request (``request``), or as the
		email queue does for an attachment (``emailed``: ``attach_print`` sets the flag)."""
		frappe = sys.modules["frappe"]
		with (
			mock.patch.object(travel, "_is_coordinator", return_value=coordinator),
			mock.patch.object(frappe, "form_dict", _dict({"as": employee} if employee else {}), create=True),
			mock.patch.object(frappe.local, "request", object() if request else None, create=True),
			mock.patch.object(frappe.flags, "ignore_print_permissions", emailed, create=True),
		):
			return travel.ee_trip_sheet(doc or self.doc)

	def render(self, sheet):
		from jinja2 import Environment, StrictUndefined

		from erpnext_enhancements import print_style as ps

		env = Environment(undefined=StrictUndefined)
		env.globals.update({name: getattr(ps, name) for name in dir(ps) if name.startswith("ps_")})
		env.globals["ee_trip_sheet"] = lambda doc: sheet
		return env.from_string(_sheet_source()).render(doc=self.doc)

	def test_a_crew_member_gets_every_number_and_no_money(self):
		sheet = self.sheet()
		self.assertIsNone(sheet["money"])
		self.assertNoMoney(sheet)
		html = self.render(sheet)
		for ref in self.REFS:
			self.assertIn(ref, html)
		for amount in (*SENTINELS, "1,499.99", "1499.99"):
			self.assertNotIn(amount, html)
		self.assertNotIn("BOOKED SO FAR", html)
		self.assertNotIn("{{", html)
		self.assertNotIn("None", html)
		self.assertNotIn(RECEIPT_MARK, html)

	def test_a_coordinator_gets_the_total(self):
		sheet = self.sheet(coordinator=True)
		self.assertEqual(sheet["money"]["total"], round(422.22 + 644.44 + 433.33, 2))
		self.assertEqual(sheet["money"]["currency"], "USD")
		html = self.render(sheet)
		self.assertIn("BOOKED SO FAR", html)
		self.assertIn("USD 1,499.99", html)
		self.assertNoReceipt(sheet)

	def test_an_emailed_sheet_is_never_priced(self):
		"""The composer's "Attach Document Print" (the Trip Sheet is the default format, and the
		Employee role may email a trip) queues the mail; the email queue renders the attachment
		later as Administrator, a coordinator, with ``ignore_print_permissions`` set by
		``attach_print``. That copy went out with the total on it, whoever sent it."""
		for emailed, request in ((True, True), (True, False), (False, False)):
			with self.subTest(emailed=emailed, request=request):
				sheet = self.sheet(coordinator=True, request=request, emailed=emailed)
				self.assertIsNone(sheet["money"])
				self.assertNoMoney(sheet)
				html = self.render(sheet)
				self.assertNotIn("BOOKED SO FAR", html)
				self.assertNotIn("1,499.99", html)
		# Through the real gate, as the email queue runs: Administrator, in no request.
		frappe = sys.modules["frappe"]
		frappe.session.user = "Administrator"
		with (
			mock.patch.object(
				travel, "_is_coordinator", side_effect=lambda: frappe.session.user == "Administrator"
			),
			mock.patch.object(frappe.flags, "ignore_print_permissions", True, create=True),
			mock.patch.object(frappe, "form_dict", _dict(), create=True),
		):
			self.assertIsNone(travel.ee_trip_sheet(self.doc)["money"])

	def test_a_forged_trip_prints_the_saved_one(self):
		"""The print view renders a document posted as JSON (``printview.get_html_and_style``),
		and the permission hook passes one with no ``creation``. Its owner, crew, job and hotels
		are the caller's to write, ``creation`` included, so the sheet must come from the trip
		as saved: a forged owner read that user's phone and email into the contacts card, a
		forged job any customer's or lead's."""
		frappe = sys.modules["frappe"]
		self.site.records[("User", "victim@example.com")] = {
			"full_name": "Vic Tim",
			"mobile_no": "(801) 555-7777",
			"email": "vic.personal@gmail.example",
		}
		self.site.records[("Lead", "CRM-LEAD-9")] = {
			"lead_name": "Private Lead",
			"mobile_no": "385-555-9999",
			"email_id": "lead@private.example",
		}
		forged = make_trip(
			creation="2026-01-01 00:00:00",
			travel_for_doctype="Lead",
			travel_for_name="CRM-LEAD-9",
		)
		forged.owner = "victim@example.com"
		forged.travelers = [FakeRow(name="T9", employee="EMP-Z", employee_name="Zed", is_trip_lead=1)]
		forged.accommodations = [FakeRow(name="R9", hotel_lodging="Other Supplier", address="ADDR-SITE")]
		self.site.requests.clear()
		with mock.patch.object(frappe, "has_permission", wraps=frappe.has_permission) as checked:
			sheet = self.sheet(doc=forged)
		text = json.dumps(sheet)
		forged_values = ("Vic Tim", "555-7777", "vic.personal", "Private Lead", "385-555-9999", "Zed")
		for value in (*forged_values, "Other Supplier"):
			self.assertNotIn(value, text, value)
		self.assertEqual(sheet["job"], "Harbor Fountain (PRJ-1)", "the saved trip's job")
		self.assertIn("Olive Office", text, "the saved trip's owner")
		self.assertNotIn(("User", "victim@example.com"), [(d, n) for d, n, _f in self.site.requests])
		self.assertNotIn("Lead", {d for d, _n, _f in self.site.requests})
		# The saved trip is what was checked, never the copy.
		self.assertTrue(checked.call_args_list)
		for call in checked.call_args_list:
			self.assertIs(call.kwargs.get("doc"), self.doc)

		# A trip that is not saved (no name, or a name that is no trip) prints nothing and asks
		# for nothing.
		for name in (None, "", "TRIP-NOT-SAVED", {"name": "TRIP-1"}):
			with self.subTest(name=name):
				unsaved = make_trip(name=name)
				unsaved.owner = "victim@example.com"
				self.site.requests.clear()
				with self.assertRaises(frappe.ValidationError):
					self.sheet(doc=unsaved)
				self.assertEqual(
					{d for d, _n, _f in self.site.requests}
					& {"User", "Customer", "Contact", "Supplier", "Lead"},
					set(),
				)

	def test_the_map_and_the_sheet_number_the_days_alike(self):
		"""The Map's day chips (plan_a_trip.js ``map_day_words``, run in node) and the printed
		sheet in a crew lead's hand say the same day. The fixture has a flight the evening before
		the trip (Oct 4): the Map counted the days the views show, so every chip was one ahead."""
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		with open(PLAN_A_TRIP_JS, encoding="utf-8") as fh:
			source = fh.read()
		helper = re.search(r"\nfunction tp_days_apart\(from, to\) \{.*?\n\}\n", source, re.S).group(0)
		method = re.search(r"\n\tmap_day_words\(date, start\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		sheet = self.sheet()
		dates = [day["date"] for day in sheet["days"]]
		script = (
			"const __ = (text, args) => text.replace(/\\{(\\d+)\\}/g, (m, i) => args[i]);"
			"const tp_pretty_date = (date) => date;"
			+ helper
			+ f"function map_day_words(date, start) {{{method}\n}}"
			+ f"process.stdout.write(JSON.stringify({json.dumps(dates)}"
			+ f".map((date) => map_day_words(date, {json.dumps(self.doc.start_date)}))));"
		)
		result = subprocess.run(
			[node, "-e", script], capture_output=True, text=True, encoding="utf-8", check=False, timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)

		def number(label):
			return label.split(" · ")[0] if " · " in label else ""

		self.assertEqual(
			[number(words) for words in json.loads(result.stdout)],
			[number(day["label"]) for day in sheet["days"]],
		)
		self.assertEqual(
			[number(day["label"]) for day in sheet["days"]], ["", "Day 1", "Day 2", "Day 3", "Day 4"]
		)

	def test_the_job_keeps_its_name_with_nobody_to_call(self):
		self.site.records[("Project", "PRJ-2")] = {"project_name": "Bare", "customer": None}
		doc = make_trip(travel_for_name="PRJ-2")
		SITE.trips["TRIP-1"] = doc
		sheet = self.sheet(doc=doc)
		self.assertIsNone(travel._trip_contacts(doc)["site"])
		self.assertEqual(sheet["job"], "Bare (PRJ-2)")
		# A record that is gone: its kind and id, as typed.
		doc.travel_for_name = "PRJ-GONE"
		self.assertEqual(self.sheet(doc=doc)["job"], "Project PRJ-GONE")

	def test_a_persons_sheet_is_theirs_alone_and_never_priced(self):
		for coordinator in (False, True):
			sheet = self.sheet(coordinator=coordinator, employee="EMP-A")
			self.assertIsNone(sheet["money"], "a person's sheet is handed to that person")
			self.assertEqual((sheet["for_employee"], sheet["for_name"]), ("EMP-A", "Ann"))
			self.assertEqual(sheet["eyebrow"], "TRIP SHEET · ANN")
			html = self.render(sheet)
			for ref in ("PNR-A", "H-A", "RC-1"):
				self.assertIn(ref, html)
			for ref in ("PNR-B", "H-B", "PRO-1"):
				self.assertNotIn(ref, html)
			self.assertNotIn(">Who</th>", html)
			self.assertNotIn("BOOKED SO FAR", html)
			self.assertNoMoney(sheet)
		# Ann's own files, the whole trip's and her bookings' — not Bo's boarding pass.
		self.assertEqual(
			[d["title"] for d in self.sheet(employee="EMP-A")["documents"]],
			[d["title"] or d["file_name"] for d in travel.shape_itinerary(self.doc, "EMP-A")["documents"]],
		)
		self.assertNotIn("bo-pass.pdf", json.dumps(self.sheet(employee="EMP-A")))

	def test_anyone_not_on_the_crew_prints_the_whole_trip(self):
		whole = self.sheet()
		for asked in ("EMP-Z", "crew", "<b>x</b>"):
			self.assertEqual(self.sheet(employee=asked), whole, asked)
		frappe = sys.modules["frappe"]
		with (
			mock.patch.object(frappe, "form_dict", _dict({"as": {"name": "EMP-A"}}), create=True),
			mock.patch.object(travel, "_is_coordinator", return_value=False),
		):
			self.assertIsNone(travel.ee_trip_sheet(self.doc)["for_employee"])
		self.assertIsNone(whole["for_name"])

	def test_the_whole_sheet(self):
		sheet = self.sheet()
		self.assertEqual(sheet["eyebrow"], "TRIP SHEET")
		self.assertEqual(sheet["dates_text"], "Mon Oct 5 – Thu Oct 8")
		self.assertEqual(sheet["job"], "Harbor Fountain (PRJ-1)")
		self.assertEqual(sheet["lead_name"], "Ann")
		self.assertEqual(
			sheet["crew"],
			[
				{"name": "Ann", "dates": "Mon Oct 5 – Thu Oct 8", "is_trip_lead": True, "is_you": False},
				{"name": "Bo", "dates": "Tue Oct 6 – Thu Oct 8", "is_trip_lead": False, "is_you": False},
				{"name": "Cy", "dates": "Mon Oct 5 – Thu Oct 8", "is_trip_lead": False, "is_you": False},
			],
		)
		self.assertEqual(
			[day["label"] for day in sheet["days"]],
			["Sun Oct 4", "Day 1 · Mon Oct 5", "Day 2 · Tue Oct 6", "Day 3 · Wed Oct 7", "Day 4 · Thu Oct 8"],
		)
		flight = sheet["days"][1]["rows"][0]
		self.assertEqual(
			flight,
			{
				"time": "7:15 AM",
				"what": "Flight: Southwest WN 1",
				"detail": "PHX → LAS, lands 8:20 AM",
				"who": "Ann, Bo",
				"refs": [{"name": "Ann", "ref": "PNR-A"}, {"name": "Bo", "ref": "PNR-B"}],
				"ref_kind": "PNR",
				"ref_note": "",
			},
		)
		self.assertEqual(sheet["days"][0]["rows"][0]["ref_note"], "no PNR yet")
		# Changed on purpose (2026-09-27): the check-in row is the hotel's name only. Its street
		# address is under "Who to call" (never the Address record's name the row holds), and
		# printing it again on every check-in pushed a five-day trip onto a third page.
		checkin = next(r for r in sheet["days"][1]["rows"] if r["what"] == "Check in: Hotel One")
		self.assertEqual(checkin["detail"], "")
		self.assertEqual(
			[c["address"] for c in sheet["contacts"] if c["role"] == "Hotel"], ["1 Main St, Las Vegas, NV"]
		)
		# Bo joins a day late, so the crew is the per-person table, not the one line.
		self.assertIsNone(sheet["crew_dates_text"])
		freight = next(r for r in sheet["days"][2]["rows"] if r["what"].startswith("Freight"))
		self.assertEqual((freight["who"], freight["refs"]), ("Cy", [{"name": "", "ref": "PRO-1"}]))
		self.assertEqual((freight["time"], freight["detail"]), ("10:00 AM", "to Site"))
		self.assertEqual(sheet["contacts"][0]["name"], "Call 911")
		self.assertEqual(sheet["itinerary_url"], "https://example.com/itinerary?trip=TRIP-1")
		# Files by title and kind only: a printed link to a private file is no use on paper.
		self.assertEqual(set(sheet["documents"][0]), {"title", "kind", "for_name", "booking_label"})
		self.assertNotIn("/private/files", json.dumps(sheet))

	def test_the_check_in_rows_name_the_hotel_only(self):
		"""The hotel's street address is under "Who to call", once. Printed again on every check-in
		it pushed a five-day, four-person trip onto a third page (2026-09-27); the row keeps what a
		crew lead needs there: the time, the hotel, who and every confirmation number."""
		for employee in (None, "EMP-A"):
			with self.subTest(employee=employee):
				sheet = self.sheet(employee=employee)
				rows = [r for day in sheet["days"] for r in day["rows"] if r["what"].startswith("Check ")]
				self.assertEqual([r["what"] for r in rows], ["Check in: Hotel One", "Check out: Hotel One"])
				self.assertEqual({r["detail"] for r in rows}, {""})
				html = self.render(sheet)
				self.assertEqual(html.count("1 Main St, Las Vegas, NV"), 1, "in Who to call only")
		rows = [r for day in self.sheet()["days"] for r in day["rows"]]
		checkin = next(r for r in rows if r["what"] == "Check in: Hotel One")
		self.assertEqual(checkin["refs"], [{"name": "Ann", "ref": "H-A"}, {"name": "Bo", "ref": "H-B"}])
		self.assertEqual(checkin["who"], "Ann, Bo")

	def test_a_crew_on_the_trips_dates_is_one_line(self):
		"""Everyone on the trip for all of it (the usual crew): one line under the facts, the dates
		said once, instead of a table printing the trip's dates on every row."""
		for row in self.doc.travelers:
			row.from_date = row.to_date = None
		self.doc.travelers[2].from_date, self.doc.travelers[2].to_date = "2026-10-05", "2026-10-08"
		sheet = self.sheet()
		self.assertEqual(sheet["crew_dates_text"], "Mon Oct 5 – Thu Oct 8")
		html = self.render(sheet)
		self.assertIn(">CREW</div>", html)
		self.assertIn("Ann, Bo, Cy &middot; everyone Mon Oct 5 – Thu Oct 8", html)
		self.assertNotIn(">Crew</h2>", html)
		self.assertNotIn(">Dates</th>", html)
		self.assertIn(">TRIP LEAD</div>", html)
		# One person: no "everyone".
		self.doc.travelers = self.doc.travelers[:1]
		self.assertIn("Ann &middot; Mon Oct 5 – Thu Oct 8", self.render(self.sheet()))
		# Someone joining a day late: the table, with each person's own dates.
		late = FakeRow(name="T2", employee="EMP-B", employee_name="Bo", from_date="2026-10-06")
		self.doc.travelers.append(late)
		sheet = self.sheet()
		self.assertIsNone(sheet["crew_dates_text"])
		html = self.render(sheet)
		self.assertIn(">Crew</h2>", html)
		self.assertIn("Tue Oct 6 – Thu Oct 8", html)
		self.assertNotIn(">CREW</div>", html)

	def test_it_renders_the_whole_sheet(self):
		html = self.render(self.sheet())
		for text in (
			"TRIP SHEET",
			"Install",
			"TRIP-1",
			"Harbor Fountain (PRJ-1)",
			"Call 911",
			'href="tel:911"',
			'href="tel:8015550101"',
			"(801) 555-0101",
			"Nearest urgent care",
			"Day 1 · Mon Oct 5",
			"Ann: ",
			"no PNR yet",
			"Site map",
			"The whole trip",
			"https://example.com/itinerary?trip=TRIP-1",
			"Trip lead",
		):
			self.assertIn(text, html)
		self.assertEqual(html.count("linear-gradient(90deg"), 2, "the stripe top and bottom")
		self.assertIn("<svg", html)

	def test_the_sparsest_sheet_renders(self):
		doc = make_trip(
			flights=[],
			accommodations=[],
			ground_transport=[],
			freight=[],
			itinerary=[],
			documents=[],
			travel_for_doctype=None,
			travel_for_name=None,
		)
		doc.travelers = [FakeRow(name="T1", employee="EMP-A", employee_name="Ann")]
		SITE.trips["TRIP-1"] = doc
		sheet = self.sheet(doc=doc)
		self.assertIsNone(sheet["lead_name"])
		html = self.render(sheet)
		self.assertIn("Nothing booked.", html)
		self.assertIn("Not set", html)
		self.assertNotIn(">Files</h2>", html)
		self.assertNotIn("None", html)

	def test_what_people_typed_is_escaped(self):
		doc = make_trip(purpose="<script>x</script>")
		doc.flights[0].booking_reference = "<b>PNR</b>"
		SITE.trips["TRIP-1"] = doc
		html = self.render(self.sheet(doc=doc))
		self.assertNotIn("<script>x", html)
		self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
		self.assertIn("&lt;b&gt;PNR&lt;/b&gt;", html)

	def test_the_template_is_print_safe_and_reads_no_field_of_the_trip(self):
		source = _sheet_source()
		code = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
		squashed = code.replace(" ", "")
		self.assertNotIn("display:flex", squashed)
		self.assertNotIn("display:grid", squashed)
		self.assertIn("display:table", squashed)
		self.assertIn("page-break-inside:avoid", squashed)
		# `doc` is handed to ee_trip_sheet and read nowhere: every money field is on it.
		self.assertEqual(re.findall(r"\bdoc\b[^)]", code), [], "the template reads the trip directly")
		self.assertIn("ee_trip_sheet(doc)", code)
		self.assertNotIn("letter_head", code, "print_style draws the wordmark: two logos otherwise")
		# No color of its own: the chrome's, through ps_*.
		self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,6}\b", code), [])

	def test_the_sheet_checks_the_trip_and_the_reader(self):
		frappe = sys.modules["frappe"]
		self.assertEqual(travel.ee_trip_sheet(types.SimpleNamespace(doctype="Sales Invoice")), {})
		SITE.deny = True
		with mock.patch.object(travel, "_is_coordinator", return_value=True):
			with self.assertRaises(frappe.PermissionError):
				travel.ee_trip_sheet(self.doc)

	def test_the_links(self):
		self.assertEqual(
			views.trip_sheet_url("TRIP-2026-00001"),
			"/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip"
			"&name=TRIP-2026-00001&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome",
		)
		self.assertEqual(
			views.trip_sheet_url("TRIP 1&x", "EMP/1"),
			"/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip"
			"&name=TRIP%201%26x&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome&as=EMP%2F1",
		)
		self.assertEqual(
			views.trip_sheet_url("TRIP-1", "EMP-A", printview=True),
			"/printview?doctype=Travel%20Trip&name=TRIP-1&format=Trip%20Sheet&no_letterhead=1&as=EMP-A",
		)
		with mock.patch.object(travel, "_is_coordinator", return_value=False):
			itinerary = travel.get_trip_itinerary("TRIP-1")
			crew = travel.get_trip_itinerary("TRIP-1", "crew")
		self.assertEqual(itinerary["sheet_url"], views.trip_sheet_url("TRIP-1"))
		self.assertEqual(itinerary["my_sheet_url"], views.trip_sheet_url("TRIP-1", "EMP-B"))
		self.assertEqual(
			crew["my_sheet_url"], views.trip_sheet_url("TRIP-1"), "the whole crew's is the whole trip's"
		)

	def test_no_link_to_a_sheet_the_site_does_not_have(self):
		"""frappe prints a format it cannot find as Standard, every cost included. Until the Trip
		Sheet exists (its upsert failed and logged, or the site has not migrated), no answer
		carries a link to it, and the Review step is told not to spell one."""
		SITE.print_formats = set()
		SITE.get_all = {"Travel POI": []}
		with (
			mock.patch.object(travel, "_is_coordinator", return_value=False),
			mock.patch.object(planner, "_lookups", return_value={}),
		):
			itinerary = travel.get_trip_itinerary("TRIP-1")
			payload = travel.get_trip_views("TRIP-1")
			state = planner.get_plan("TRIP-1")["state"]
			self.assertEqual((itinerary["sheet_url"], itinerary["my_sheet_url"]), (None, None))
			self.assertEqual((payload["sheet_url"], payload["people_sheet_urls"]), (None, {}))
			self.assertIs(state["sheet_available"], False)
			SITE.print_formats = {"Trip Sheet"}
			self.assertIs(planner.get_plan("TRIP-1")["state"]["sheet_available"], True)
			self.assertEqual(travel.get_trip_views("TRIP-1")["sheet_url"], views.trip_sheet_url("TRIP-1"))
		self.assertIn(
			"state.update(_viewer())", inspect.getsource(planner.save_plan), "the saved state says so too"
		)


class TestTheTripSheetFormat(unittest.TestCase):
	"""The after_migrate upsert, its place in hooks.py, and the Jinja global's registration."""

	def setUp(self):
		install_site()
		sys.modules.pop(SHEET_SETUP, None)
		import importlib

		self.setup = importlib.import_module(SHEET_SETUP)
		self.saved = []
		self.existing = set()
		# The site's Property Setters for a doctype's default print format, {doc_type: value},
		# and every make_property_setter call, as (args, kwargs).
		self.defaults = {}
		self.setters = []
		frappe = sys.modules["frappe"]
		test = self

		class PrintFormat(types.SimpleNamespace):
			def save(self, ignore_permissions=False):
				test.saved.append(dict(vars(self)))
				test.existing.add(self.name)

		def exists(doctype, name=None, *a, **k):
			if doctype == "DocType":
				return name == "Travel Trip"
			if doctype == "Print Format":
				return name in self.existing
			raise AssertionError(f"exists({doctype!r}) was not expected")

		def get_value(doctype, filters=None, fieldname=None, *a, **k):
			self.assertEqual(doctype, "Property Setter")
			self.assertEqual(
				filters,
				{
					"doc_type": "Travel Trip",
					"doctype_or_field": "DocType",
					"property": "default_print_format",
				},
			)
			self.assertEqual(fieldname, "value")
			return self.defaults.get("Travel Trip")

		def make_property_setter(args, *a, **k):
			self.setters.append((dict(args), a, k))
			self.defaults[args["doctype"]] = args["value"]

		for patcher in (
			mock.patch.object(frappe.db, "exists", exists, create=True),
			mock.patch.object(frappe.db, "get_value", get_value, create=True),
			mock.patch.object(frappe, "make_property_setter", make_property_setter, create=True),
			mock.patch.object(frappe.db, "commit", lambda: None, create=True),
			mock.patch.object(frappe.db, "has_column", lambda doctype, column: True),
			mock.patch.object(frappe, "new_doc", lambda doctype: PrintFormat(), create=True),
			mock.patch.object(
				frappe, "get_doc", lambda doctype, name: PrintFormat(name=name, html="old"), create=True
			),
			mock.patch.object(
				frappe,
				"logger",
				lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None),
				create=True,
			),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_it_is_upserted_from_the_repo_file(self):
		self.setup.ensure_travel_print_formats()
		self.setup.ensure_travel_print_formats()
		self.assertEqual(len(self.saved), 2)
		for saved in self.saved:
			self.assertEqual(
				{
					k: saved[k]
					for k in (
						"name",
						"doc_type",
						"module",
						"print_format_type",
						"custom_format",
						"standard",
						"disabled",
						"pdf_generator",
					)
				},
				{
					"name": "Trip Sheet",
					"doc_type": "Travel Trip",
					"module": "Travel Management",
					"print_format_type": "Jinja",
					"custom_format": 1,
					"standard": "No",
					"disabled": 0,
					"pdf_generator": "chrome",
				},
			)
			self.assertEqual(saved["html"], _sheet_source())
		self.assertEqual(views.TRIP_SHEET_FORMAT, "Trip Sheet")

	def test_a_failure_logs_and_never_aborts_the_migrate(self):
		frappe = sys.modules["frappe"]
		logged = []
		with (
			mock.patch.object(frappe, "new_doc", side_effect=RuntimeError("boom"), create=True),
			mock.patch.object(frappe, "log_error", lambda *a, **k: logged.append(a[1:] or k.get("title"))),
		):
			self.setup.ensure_travel_print_formats()
		self.assertEqual(logged, [("Travel print formats",)])
		self.assertEqual(self.saved, [])
		self.assertEqual(self.setters, [], "a default naming a format that is not there hides Standard")

	# -- The default print format (Nik, 2026-09-27) -------------------------------------------

	DEFAULT_ARGS = {
		"doctype": "Travel Trip",
		"doctype_or_field": "DocType",
		"property": "default_print_format",
		"value": "Trip Sheet",
		"property_type": "Data",
	}

	def test_it_becomes_travel_trips_default_print_format(self):
		self.setup.ensure_travel_print_formats()
		self.assertEqual(self.setters, [(self.DEFAULT_ARGS, (), {"validate_fields_for_doctype": False})])
		# Idempotent: a migrate with nothing to change writes no Property Setter.
		self.setup.ensure_travel_print_formats()
		self.assertEqual(len(self.setters), 1)
		self.assertEqual(len(self.saved), 2, "the template is still upserted every migrate")

	def test_a_default_chosen_on_the_site_is_put_back(self):
		self.defaults["Travel Trip"] = "Standard"
		self.setup.ensure_travel_print_formats()
		self.assertEqual([s[0] for s in self.setters], [self.DEFAULT_ARGS])

	def test_the_default_is_set_only_after_the_upsert_and_only_when_the_format_exists(self):
		"""A fixture would name the format before it exists: fixtures sync before after_migrate,
		and `bench install-app` syncs them and never runs after_migrate at all. With the default
		naming a missing format, frappe's get_print_formats splices out index -1, the menu's last
		entry, which is "Standard" itself on a trip with no other format."""
		source = inspect.getsource(self.setup.ensure_travel_print_formats)
		self.assertLess(source.index("_upsert_print_format("), source.index("_make_default("))
		self.assertLess(source.index("_make_default("), source.index("except Exception"))
		# The format is gone (deleted on the site, or its save rolled back): no default.
		self.setup._make_default("Trip Sheet", "Travel Trip")
		self.assertEqual(self.setters, [])

	def test_the_default_is_code_owned_never_a_fixture(self):
		"""The row is frappe's default is_system_generated, so the fixture export (which takes
		only is_system_generated = 0) never picks it up, and no fixture sets it either: two
		writers of one Property Setter would fight on every migrate."""
		self.assertNotIn("is_system_generated", inspect.getsource(self.setup._make_default))
		with open(os.path.join(APP_DIR, "fixtures", "property_setter.json"), encoding="utf-8") as fh:
			rows = json.load(fh)
		self.assertEqual([r["name"] for r in rows if r.get("doc_type") == "Travel Trip"], [])
		with open(os.path.join(APP_DIR, "hooks.py"), encoding="utf-8") as fh:
			hooks = fh.read()
		setter_hook = hooks[hooks.index('"dt": "Property Setter"') :]
		self.assertIn('["is_system_generated", "=", 0]', setter_hook[: setter_hook.index("},")])

	def test_hooks_run_it_before_the_chrome_pass_and_register_the_global(self):
		with open(os.path.join(APP_DIR, "hooks.py"), encoding="utf-8") as fh:
			hooks = fh.read()
		upsert = '"erpnext_enhancements.travel_management.setup_print_formats.ensure_travel_print_formats"'
		chrome = '"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator"'
		self.assertIn(upsert, hooks)
		self.assertLess(hooks.index(upsert), hooks.index(chrome))
		self.assertIn('"erpnext_enhancements.api.travel.ee_trip_sheet"', hooks)
		self.assertRegex(inspect.getsource(travel), r"\ndef ee_trip_sheet\(doc\):")
		# A Jinja global, not an endpoint.
		self.assertNotRegex(inspect.getsource(travel), r"@frappe\.whitelist\([^)]*\)\ndef ee_trip_sheet")


class TestTheTravelDeskSettings(unittest.TestCase):
	PATH = os.path.join(APP_DIR, "travel_management", "doctype", "travel_settings", "travel_settings.json")

	def test_the_fields_have_no_default(self):
		# A default on a new field of a Single never reaches the row a site already has
		# (CLAUDE.md): the field would read blank on prod while the JSON said otherwise.
		with open(self.PATH, encoding="utf-8") as fh:
			meta = json.load(fh)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		for fieldname, fieldtype, options in (
			("travel_desk_label", "Data", None),
			("travel_desk_phone", "Data", "Phone"),
			("travel_desk_email", "Data", "Email"),
		):
			with self.subTest(fieldname):
				self.assertIn(fieldname, meta["field_order"])
				self.assertEqual(fields[fieldname]["fieldtype"], fieldtype)
				self.assertEqual(fields[fieldname].get("options"), options)
				self.assertNotIn("default", fields[fieldname])
		self.assertEqual(fields["travel_desk_section"]["fieldtype"], "Section Break")

	def test_they_are_read_as_a_single(self):
		source = inspect.getsource(travel._office_contact)
		self.assertIn('frappe.db.get_single_value("Travel Settings", field)', source)
		self.assertNotIn('"Singles"', source)


# --------------------------------------------------------------------------- the Travel hub
#
# "My Travel", the block at the top of the Travel workspace (2026-09-28): one call,
# ``travel_management.home.get_travel_home``, answers "where is MY information?" for whoever
# is looking — their trip now or next, their other trips, receipts due, and for a coordinator
# what needs attention. It is a crew member's screen, so every answer is walked for money like
# every other payload in this file, and the coordinator's list is checked to be absent — not
# empty — for anyone else, through the real gate as well as a patched one.

HOME = "erpnext_enhancements.travel_management.home"

#: The stub's today (``frappe.utils.today``): Saturday, September 26, 2026.
HUB_TODAY = date(2026, 9, 26)


def _home():
	import importlib

	return importlib.import_module(HOME)


def hub_day(offset):
	"""The ISO date ``offset`` days from the stub's today."""
	return (HUB_TODAY + timedelta(days=offset)).isoformat()


def hub_trip(name, start, end, status="Booked", owner="office@example.com", clean=False, **overrides):
	"""A trip from the shared fixture (Ann, Bo and Cy on the crew, money on every row), moved to
	the given days. ``clean`` empties it — no crew, no bookings, no files — so its checklist has
	nothing to say and only its status and dates can put it on a coordinator's list.

	Each traveler's own days are the trip's, as a save leaves them (``TravelTrip._validate_dates``
	fills a blank one with the trip's): the fixture's own ``from_date`` for Bo belongs to its
	October trip and would put him outside a moved one. :func:`own_days` gives one person their
	own."""
	doc = make_trip(
		name,
		purpose=overrides.pop("purpose", f"Trip {name}"),
		status=status,
		owner=owner,
		**overrides,
	)
	move_trip(doc, start, end)
	if clean:
		for table in ("travelers", "flights", "accommodations", "ground_transport", "freight", "documents"):
			setattr(doc, table, [])
	return doc


def move_trip(doc, start, end):
	"""Move a trip to the given days, with every traveler's own days the trip's."""
	doc.start_date, doc.end_date = hub_day(start), hub_day(end)
	for traveler in doc.travelers or []:
		traveler.from_date, traveler.to_date = doc.start_date, doc.end_date
	return doc


def own_days(doc, employee, first, last):
	"""Give one traveler their own days on ``doc`` — offsets from the stub's today, or None for a
	blank date — the way Plan a Trip saves someone who joins late or leaves early."""
	row = next(row for row in doc.travelers if row.employee == employee)
	row.from_date = None if first is None else hub_day(first)
	row.to_date = None if last is None else hub_day(last)
	return doc


def _hub_norm(value):
	if isinstance(value, datetime):
		return value.date().isoformat()
	if isinstance(value, date):
		return value.isoformat()
	return value


def _hub_matches(row, filters):
	for field, condition in (filters or {}).items():
		op, value = condition if isinstance(condition, (list, tuple)) else ("=", condition)
		have = _hub_norm(row.get(field))
		if op == "=":
			ok = have == _hub_norm(value)
		elif op == "!=":
			ok = have != _hub_norm(value)
		elif op == "in":
			ok = have in [_hub_norm(v) for v in value]
		elif op == ">=":
			ok = have is not None and str(have) >= str(_hub_norm(value))
		elif op == "<":
			ok = have is not None and str(have) < str(_hub_norm(value))
		else:
			raise AssertionError(f"the hub's fake site does not know the filter {op!r}")
		if not ok:
			return False
	return True


class HomeSite:
	"""The fake site the hub reads. The module stub's ``get_all``/``get_list`` return one fixed
	list whatever they are asked, which cannot tell "the trips Bo travels on" from "the trips he
	owns"; here both answer from the trips on ``SITE.trips`` with their filters applied, and
	``get_list`` is scoped the way the Travel Trip permission hooks scope it: a coordinator sees
	every trip, anyone else the ones they own or travel on. Every call is recorded, so a test can
	say which queries ran. ``coordinator=None`` leaves ``_is_coordinator`` real."""

	def __init__(self, test, *docs, coordinator=False, settings=None, first_names=None):
		frappe = sys.modules["frappe"]
		for doc in docs:
			SITE.trips[doc.name] = doc
		self.coordinator = coordinator
		self.alerts = []
		self.calls = []
		self.settings = dict(settings or {})
		self.first_names = dict(first_names or {})
		previous_value = frappe.db.get_value
		previous_single = frappe.db.get_single_value

		def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "User" and fieldname == "first_name":
				return self.first_names.get(name)
			return previous_value(doctype, name, fieldname, *args, **kwargs)

		def get_single_value(doctype, field, *args, **kwargs):
			if field in self.settings:
				return self.settings[field]
			return previous_single(doctype, field, *args, **kwargs)

		patchers = [
			mock.patch.object(frappe, "get_all", self.get_all),
			mock.patch.object(frappe, "get_list", self.get_list),
			mock.patch.object(frappe.db, "get_value", get_value),
			mock.patch.object(frappe.db, "get_single_value", get_single_value),
		]
		if coordinator is not None:
			patchers.append(mock.patch.object(travel, "_is_coordinator", lambda: self.coordinator))
		for patcher in patchers:
			patcher.start()
			test.addCleanup(patcher.stop)

	def table(self, doctype):
		if doctype == "Travel Trip":
			return [
				_dict(
					name=doc.name,
					purpose=doc.purpose,
					status=doc.status,
					start_date=doc.start_date,
					end_date=doc.end_date,
					owner=doc.owner,
					travel_for_doctype=doc.travel_for_doctype,
					travel_for_name=doc.travel_for_name,
				)
				for doc in SITE.trips.values()
			]
		if doctype == "Trip Traveler":
			return [
				_dict(
					parent=doc.name,
					parenttype="Travel Trip",
					employee=row.employee,
					from_date=row.from_date,
					to_date=row.to_date,
				)
				for doc in SITE.trips.values()
				for row in doc.travelers or []
			]
		if doctype == "Trip Change Alert":
			return [_dict(row) for row in self.alerts]
		raise AssertionError(f"the hub asked the site for {doctype}")

	@staticmethod
	def query(rows, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None, **kwargs):
		rows = [row for row in rows if _hub_matches(row, filters)]
		if order_by:
			field, _space, direction = order_by.partition(" ")
			rows.sort(
				key=lambda row: str(_hub_norm(row.get(field)) or ""), reverse=direction.strip() == "desc"
			)
		if limit_page_length:
			rows = rows[:limit_page_length]
		if pluck:
			return [row.get(pluck) for row in rows]
		return [_dict({f: row.get(f) for f in fields}) if fields else _dict(row) for row in rows]

	def get_all(self, doctype, **kwargs):
		self.calls.append(("get_all", doctype))
		return self.query(self.table(doctype), **kwargs)

	def get_list(self, doctype, **kwargs):
		self.calls.append(("get_list", doctype))
		frappe = sys.modules["frappe"]
		if SITE.deny:
			raise frappe.PermissionError(f"No permission for {doctype}")
		rows = self.table(doctype)
		coordinator = travel._is_coordinator() if self.coordinator is None else self.coordinator
		if doctype == "Travel Trip" and not coordinator:
			user = frappe.session.user
			employee = SITE.users.get(user)
			rows = [
				row
				for row in rows
				if row.owner == user
				or (employee and any(t.employee == employee for t in SITE.trips[row.name].travelers or []))
			]
		return self.query(rows, **kwargs)

	def ran(self, method, doctype):
		return (method, doctype) in self.calls


class HubAssertions(MoneyAssertions):
	def setUp(self):
		self.home = _home()

	def answer(self, user="bo@example.com"):
		sys.modules["frappe"].session.user = user
		return self.home.get_travel_home()

	def assertNothingPersonal(self, payload):
		self.assertEqual(personal_paths(payload), [], "a crew member's own details reached the hub")
		text = json.dumps(payload, default=str)
		for value in PERSONAL_VALUES:
			self.assertNotIn(value, text, f"{value} reached the hub")

	def logged(self):
		return [effect for effect in SITE.side_effects if effect == ("log_error", "Travel home")]


class TestTravelHomeForTheCrew(HubAssertions):
	def test_a_crew_member_on_the_road_gets_their_trip_first_and_no_money(self):
		doc = install_site(hub_trip("TRIP-1", -1, 4, status="In Progress", purpose="Install"))
		ContactsSite(self, doc)
		HomeSite(self, first_names={"bo@example.com": "Bo"})
		answer = self.answer()
		featured = answer["featured"]

		self.assertEqual(
			answer["viewer"],
			{"user": "bo@example.com", "employee": "EMP-B", "first_name": "Bo", "is_coordinator": False},
		)
		self.assertIsNone(answer["message"])
		self.assertEqual(featured["trip"], "TRIP-1")
		self.assertEqual(featured["purpose"], "Install")
		self.assertEqual(featured["status"], "In Progress")
		self.assertEqual(featured["dates"], "Fri Sep 25 – Wed Sep 30")
		self.assertEqual(featured["headline"], "You're on this trip now — day 2 of 6")
		self.assertEqual(featured["travel_for"], "Harbor Fountain")
		self.assertEqual(featured["itinerary_url"], "/itinerary?trip=TRIP-1")
		self.assertEqual(featured["docs_url"], "/itinerary?trip=TRIP-1&view=docs")
		# Their own sheet: the one their /itinerary prints, never the whole trip's.
		self.assertEqual(featured["sheet_url"], views.trip_sheet_url("TRIP-1", "EMP-B"))
		self.assertTrue(featured["sheet_url"].endswith("&as=EMP-B"))
		# The number on their /itinerary's "Documents (N)" tab: Bo's files, not Ann's packet.
		self.assertEqual(featured["documents"], len(travel.shape_itinerary(doc, "EMP-B")["documents"]))
		self.assertEqual(featured["documents"], 5)

		contacts = {row["label"]: row for row in featured["contacts"]}
		self.assertEqual(
			[row["label"] for row in featured["contacts"]],
			["Travel desk", "Trip lead", "Booked by", "Job site", "Hotel", "Emergency"],
		)
		self.assertEqual(contacts["Travel desk"]["phone_href"], "tel:+18015550142")
		self.assertEqual(contacts["Travel desk"]["email_href"], "mailto:travel@sapphire.example")
		self.assertEqual(
			(contacts["Trip lead"]["name"], contacts["Trip lead"]["phone"]), ("Ann", "8015550101")
		)
		self.assertEqual(contacts["Booked by"]["email_href"], "mailto:office@example.com")
		self.assertEqual(contacts["Job site"]["name"], "Sam Site")
		self.assertEqual(contacts["Job site"]["detail"], "Harbor Fountain · 500 Harbor Way, Boise, ID 83702")
		self.assertEqual(contacts["Hotel"]["name"], "Hotel One")
		self.assertEqual(contacts["Hotel"]["phone_href"], "tel:7025550123")
		self.assertEqual(
			[link["label"] for link in contacts["Hotel"]["links"]], ["Nearest urgent care", "Directions"]
		)
		self.assertEqual(
			(contacts["Emergency"]["name"], contacts["Emergency"]["phone_href"]), ("Call 911", "tel:911")
		)

		self.assertEqual(
			answer["office"],
			{
				"label": "Sapphire travel desk",
				"phone": "+1 801-555-0142",
				"phone_href": "tel:+18015550142",
				"email": "travel@sapphire.example",
				"email_href": "mailto:travel@sapphire.example",
			},
		)
		self.assertEqual(answer["guidelines_url"], "/travel_guidelines")
		self.assertEqual((answer["trips"], answer["receipts"]), ([], []))
		self.assertIsNone(answer["attention"])
		self.assertNoMoney(answer)
		self.assertNothingPersonal(featured)
		self.assertEqual(self.logged(), [])

	def test_a_trip_starting_soon_says_how_soon(self):
		doc = install_site(hub_trip("TRIP-1", 3, 5))
		HomeSite(self)
		for start, end, headline in (
			(3, 5, "Starts in 3 days"),
			(1, 3, "Starts tomorrow"),
			(0, 0, "You're on this trip today"),
		):
			with self.subTest(headline):
				move_trip(doc, start, end)
				featured = self.answer()["featured"]
				self.assertEqual(featured["trip"], "TRIP-1")
				self.assertEqual(featured["headline"], headline)
		self.assertEqual(self.answer()["featured"]["dates"], "Sat Sep 26")

	def test_the_trip_now_beats_the_trip_next_and_the_rest_are_listed(self):
		install_site(hub_trip("TRIP-1", 3, 5))
		HomeSite(
			self,
			hub_trip("TRIP-2", -2, 1, status="In Progress"),
			hub_trip("TRIP-3", 10, 12),
			hub_trip("TRIP-4", -6, -3, status="Completed"),
			hub_trip("TRIP-5", -14, -10, status="Completed"),
			hub_trip("TRIP-6", -30, -20, status="Completed"),  # past the fortnight
			hub_trip("TRIP-7", -8, -2, status="Closed"),  # closed: settled and locked
			hub_trip("TRIP-8", 5, 6, owner="bo@example.com", clean=True),  # Bo booked it for others
			hub_trip("TRIP-9", 2, 4, clean=True),  # someone else's crew
		)
		answer = self.answer()
		self.assertEqual(answer["featured"]["trip"], "TRIP-2")
		self.assertEqual(answer["featured"]["headline"], "You're on this trip now — day 3 of 4")
		trips = answer["trips"]
		self.assertEqual([t["trip"] for t in trips], ["TRIP-1", "TRIP-8", "TRIP-3", "TRIP-4", "TRIP-5"])
		self.assertEqual(
			[t["note"] for t in trips],
			[
				"Starts in 3 days",
				"You're organizing this",
				"Starts in 10 days",
				"Ended 3 days ago",
				"Ended 10 days ago",
			],
		)
		self.assertEqual(
			[t["relation"] for t in trips], ["traveling", "organizing", "traveling", "traveling", "traveling"]
		)
		# "Keep planning" is for the person who organized it, while it is still ahead.
		self.assertEqual([t["can_plan"] for t in trips], [False, True, False, False, False])
		self.assertEqual(trips[1]["itinerary_url"], "/itinerary?trip=TRIP-8")
		self.assertEqual(trips[0]["dates"], "Tue Sep 29 – Thu Oct 1")
		self.assertNoMoney(answer)

	def test_receipts_are_due_one_to_seven_days_after_getting_back(self):
		install_site(hub_trip("TRIP-1", -3, 0, status="In Progress"))  # back today: not yet
		HomeSite(
			self,
			hub_trip("TRIP-2", -4, -1, status="Completed", purpose="Harbor install"),
			hub_trip("TRIP-3", -9, -7, status="Completed", purpose="Boise service"),
			hub_trip("TRIP-4", -10, -8, status="Completed"),  # a day too late
			hub_trip("TRIP-5", -5, -2, status="Completed", owner="bo@example.com", clean=True),  # not on it
			hub_trip("TRIP-6", -5, -3, status="Closed"),
		)
		answer = self.answer()
		self.assertEqual(
			answer["receipts"],
			[
				{
					"trip": "TRIP-2",
					"purpose": "Harbor install",
					"text": "Back from Harbor install? Within a week of getting back, attach to the trip the receipt "
					"for each cost you paid yourself, and accounting will reimburse you. Attach the receipt "
					"for anything you put on a company card too. Per diem and mileage need no receipts.",
				},
				{
					"trip": "TRIP-3",
					"purpose": "Boise service",
					"text": "Back from Boise service? Within a week of getting back, attach to the trip the receipt "
					"for each cost you paid yourself, and accounting will reimburse you. Attach the receipt "
					"for anything you put on a company card too. Per diem and mileage need no receipts.",
				},
			],
		)
		# The trip Bo only organized is listed, but asks him for no receipts.
		self.assertIn("TRIP-5", [t["trip"] for t in answer["trips"]])
		self.assertEqual(
			next(t["note"] for t in answer["trips"] if t["trip"] == "TRIP-5"),
			"Ended 2 days ago — you organized it",
		)
		self.assertNoMoney(answer)

	def test_an_organizer_who_does_not_travel_can_keep_planning(self):
		install_site(hub_trip("TRIP-1", 5, 8, owner="zed@example.com"))
		SITE.users["zed@example.com"] = "EMP-Z"
		site = HomeSite(self)
		answer = self.answer("zed@example.com")
		self.assertIsNone(answer["featured"])
		self.assertIsNone(answer["message"])
		self.assertEqual(
			answer["trips"],
			[
				{
					"trip": "TRIP-1",
					"purpose": "Trip TRIP-1",
					"status": "Booked",
					"dates": "Thu Oct 1 – Sun Oct 4",
					"relation": "organizing",
					"note": "You're organizing this",
					"itinerary_url": "/itinerary?trip=TRIP-1",
					"can_plan": True,
				}
			],
		)
		self.assertEqual(answer["receipts"], [])
		self.assertIsNone(answer["attention"])
		self.assertTrue(site.ran("get_list", "Travel Trip"))
		self.assertNoMoney(answer)

	def test_an_organizer_with_no_employee_record_still_finds_what_they_booked(self):
		install_site(hub_trip("TRIP-1", 5, 8))
		HomeSite(self)
		answer = self.answer("office@example.com")
		self.assertIsNotNone(answer["message"])
		self.assertIsNone(answer["featured"])
		self.assertEqual([(t["trip"], t["relation"]) for t in answer["trips"]], [("TRIP-1", "organizing")])

	def test_no_employee_record_says_so_in_plain_words(self):
		install_site(hub_trip("TRIP-1", 5, 8))
		site = HomeSite(self)
		answer = self.answer("nobody@example.com")
		self.assertEqual(
			answer["message"],
			"Your user account isn't linked to an employee record, so your trips can't show here. "
			"Ask the office to link it.",
		)
		self.assertIsNone(answer["viewer"]["employee"])
		self.assertIsNone(answer["featured"])
		self.assertEqual((answer["trips"], answer["receipts"]), ([], []))
		self.assertFalse(site.ran("get_all", "Trip Traveler"), "a crew lookup ran with no Employee")

	def test_no_read_on_travel_trip_is_an_empty_hub_not_an_error(self):
		install_site(hub_trip("TRIP-1", 5, 8))
		site = HomeSite(self)
		SITE.deny = True
		answer = self.answer()
		self.assertIsNone(answer["featured"])
		self.assertEqual(answer["trips"], [])
		self.assertFalse(site.ran("get_list", "Travel Trip"), "get_list was asked, and would have raised")
		self.assertEqual(self.logged(), [])

	def test_a_crew_member_runs_none_of_the_coordinators_queries(self):
		install_site(hub_trip("TRIP-1", -1, 4, status="In Progress"))
		site = HomeSite(self, hub_trip("TRIP-2", -9, -4, status="Completed"))
		site.alerts = [{"trip": "TRIP-1", "status": "Failed"}]
		answer = self.answer()
		self.assertIsNone(answer["attention"])
		self.assertFalse(site.ran("get_all", "Travel Trip"))
		self.assertFalse(site.ran("get_all", "Trip Change Alert"))
		self.assertFalse(site.ran("get_list", "Trip Change Alert"))


class TestTravelHomeByYourOwnDays(HubAssertions):
	"""A crew member's own ``from_date``–``to_date`` — Plan a Trip edits them for someone who joins
	late or leaves early — decide their card, their list and their receipts, as they already
	decide their pre-travel reminder, their ``&as=`` trip sheet and their calendar invite. By the
	trip's dates, the hub told someone flying out on Monday that they were on the trip now, and
	started the receipts week of someone who got back on Thursday only when everyone else did."""

	def test_joining_a_trip_that_has_started_is_not_being_on_it(self):
		install_site(own_days(hub_trip("TRIP-1", -2, 4, status="In Progress"), "EMP-B", 2, 4))
		HomeSite(self)
		answer = self.answer()
		featured = answer["featured"]
		self.assertEqual(featured["trip"], "TRIP-1")
		self.assertEqual(featured["headline"], "Starts in 2 days")
		# Their card, their days: the trip itself started on Thu Sep 24.
		self.assertEqual(featured["dates"], "Mon Sep 28 – Wed Sep 30")
		self.assertEqual(answer["receipts"], [])
		self.assertNoMoney(answer)

	def test_the_trip_you_are_on_beats_one_you_join_later(self):
		install_site(own_days(hub_trip("TRIP-1", -2, 4, status="In Progress"), "EMP-B", 2, 4))
		HomeSite(self, hub_trip("TRIP-2", -1, 1, status="In Progress"))
		answer = self.answer()
		# By the trips' own dates TRIP-1 started first, and would be the one "now".
		self.assertEqual(answer["featured"]["trip"], "TRIP-2")
		self.assertEqual(answer["featured"]["headline"], "You're on this trip now — day 2 of 3")
		self.assertEqual(
			[(t["trip"], t["note"], t["dates"]) for t in answer["trips"]],
			[("TRIP-1", "Starts in 2 days", "Mon Sep 28 – Wed Sep 30")],
		)
		self.assertNoMoney(answer)

	def test_day_n_of_m_counts_your_own_days(self):
		install_site(own_days(hub_trip("TRIP-1", -5, 5, status="In Progress"), "EMP-B", -1, 2))
		HomeSite(self)
		answer = self.answer()
		self.assertEqual(answer["featured"]["headline"], "You're on this trip now — day 2 of 4")
		self.assertEqual(answer["featured"]["dates"], "Fri Sep 25 – Mon Sep 28")
		self.assertNoMoney(answer)

	def test_leaving_early_starts_your_receipts_week_when_you_get_back(self):
		doc = hub_trip("TRIP-1", -5, 3, status="In Progress", purpose="Harbor install")
		install_site(own_days(doc, "EMP-B", -5, -2))
		HomeSite(self)
		answer = self.answer()
		# The trip goes on without them: it is not their trip now, and it is over for them.
		self.assertIsNone(answer["featured"])
		self.assertEqual(
			[(t["trip"], t["note"], t["dates"]) for t in answer["trips"]],
			[("TRIP-1", "Ended 2 days ago", "Mon Sep 21 – Thu Sep 24")],
		)
		self.assertEqual([row["trip"] for row in answer["receipts"]], ["TRIP-1"])
		self.assertNoMoney(answer)

	def test_your_receipts_week_ends_a_week_after_your_own_last_day(self):
		install_site(own_days(hub_trip("TRIP-1", -12, -1, status="Completed"), "EMP-B", -12, -9))
		HomeSite(self)
		answer = self.answer()
		# The trip ended yesterday; Bo got back nine days ago, and his week is over.
		self.assertEqual(answer["receipts"], [])
		self.assertEqual([(t["trip"], t["note"]) for t in answer["trips"]], [("TRIP-1", "Ended 9 days ago")])
		self.assertNoMoney(answer)

	def test_an_organizer_who_came_home_early_can_still_keep_planning(self):
		doc = hub_trip("TRIP-1", -5, 3, status="In Progress", owner="bo@example.com")
		install_site(own_days(doc, "EMP-B", -5, -2))
		HomeSite(self)
		answer = self.answer()
		(item,) = answer["trips"]
		self.assertEqual((item["relation"], item["note"]), ("traveling", "Ended 2 days ago"))
		# "Keep planning" is about the trip, which has not ended.
		self.assertIs(item["can_plan"], True)
		self.assertNoMoney(answer)

	def test_blank_days_are_the_trips(self):
		install_site(own_days(hub_trip("TRIP-1", -1, 4, status="In Progress"), "EMP-B", None, None))
		HomeSite(
			self,
			own_days(hub_trip("TRIP-2", -6, -3, status="Completed"), "EMP-B", None, None),
			own_days(hub_trip("TRIP-3", 6, 8), "EMP-B", None, 7),  # only the last day set
		)
		answer = self.answer()
		self.assertEqual(answer["featured"]["headline"], "You're on this trip now — day 2 of 6")
		self.assertEqual(answer["featured"]["dates"], "Fri Sep 25 – Wed Sep 30")
		self.assertEqual(
			[(t["trip"], t["note"], t["dates"]) for t in answer["trips"]],
			[
				("TRIP-3", "Starts in 6 days", "Fri Oct 2 – Sat Oct 3"),
				("TRIP-2", "Ended 3 days ago", "Sun Sep 20 – Wed Sep 23"),
			],
		)
		self.assertEqual([row["trip"] for row in answer["receipts"]], ["TRIP-2"])
		self.assertNoMoney(answer)

	def test_a_trip_you_only_organize_keeps_its_own_dates(self):
		doc = hub_trip("TRIP-1", 5, 9, owner="bo@example.com")
		doc.travelers = [row for row in doc.travelers if row.employee != "EMP-B"]
		install_site(own_days(doc, "EMP-A", 6, 7))  # Ann's own days are nothing to Bo
		HomeSite(self)
		answer = self.answer()
		(item,) = answer["trips"]
		self.assertEqual(
			(item["relation"], item["note"], item["dates"]),
			("organizing", "You're organizing this", "Thu Oct 1 – Mon Oct 5"),
		)
		self.assertNoMoney(answer)


class TestTravelHomeForACoordinator(HubAssertions):
	USER = "tc@example.com"

	def setUp(self):
		super().setUp()
		install_site()
		SITE.trips.clear()

	def test_what_needs_attention_and_why(self):
		planning = hub_trip("TRIP-A", 5, 8, status="Planning")
		site = HomeSite(
			self,
			planning,
			hub_trip("TRIP-B", 30, 32, clean=True),  # nothing to say: not listed
			hub_trip("TRIP-C", 40, 42, clean=True),
			# Back two days ago: its crew are still inside their receipts week, so it is not yet
			# ready to close — it is listed only for its failed alert.
			hub_trip("TRIP-D", -5, -2, status="Completed", clean=True),
			hub_trip("TRIP-E", 20, 22, status="Planning", clean=True),  # three weeks out: not yet
			hub_trip("TRIP-F", -20, -15, status="Closed", clean=True),
			coordinator=True,
			settings={"notifications_enabled": 1, "travel_desk_email": "travel@sapphire.example"},
		)
		site.alerts = [
			{"trip": "TRIP-C", "status": "Failed"},
			{"trip": "TRIP-C", "status": "Failed"},
			{"trip": "TRIP-B", "status": "Pending"},
			{"trip": "TRIP-F", "status": "Failed"},  # its trip is closed
			{"trip": "TRIP-D", "status": "Failed"},
		]
		gaps = self.home.find_gaps(planning)
		missing = len(self.home.counted_gaps(gaps))
		files = self.home.files_not_attached(gaps)
		# A coordinator's count includes the cost gaps (the fixture has two), which a crew
		# member's never does; paperwork is the quieter tally, counted apart.
		self.assertEqual(len([gap for gap in gaps if gap["check"] == "cost"]), 2)
		self.assertEqual(missing, len([gap for gap in gaps if gap["check"] != "documents"]))
		self.assertGreater(files, 1)

		answer = self.answer(self.USER)
		attention = answer["attention"]
		self.assertIs(answer["viewer"]["is_coordinator"], True)
		self.assertEqual(
			[(row["trip"], row["reasons"], row["target"]) for row in attention["trips"]],
			[
				(
					"TRIP-A",
					[
						"Starts in 5 days and is still Planning",
						f"{missing} things missing",
						f"{files} files not attached",
					],
					"plan",
				),
				("TRIP-C", ["2 change alerts failed to send"], "plan"),
				("TRIP-D", ["A change alert failed to send"], "form"),
			],
		)
		self.assertEqual(attention["trips"][0]["dates"], "Thu Oct 1 – Sun Oct 4")
		self.assertEqual(
			(attention["trips"][0]["status"], attention["trips"][0]["purpose"]), ("Planning", "Trip TRIP-A")
		)
		self.assertEqual(attention["more"], 0)
		self.assertEqual(attention["setup"], [])
		self.assertNoMoney(answer)

	def test_the_setup_notes(self):
		site = HomeSite(self, coordinator=True)
		attention = self.answer(self.USER)["attention"]
		self.assertEqual(
			attention["setup"],
			[
				{
					"text": "Add a Travel Desk phone or email so travelers know who to call",
					"route": "travel-settings",
				},
				{"text": "Travel emails are off", "route": "travel-settings"},
			],
		)
		# Change alerts off is deliberate, and not a note; a desk with a phone is enough.
		site.settings.update(
			notifications_enabled=1, change_alerts_enabled=0, travel_desk_phone="801-555-0142"
		)
		answer = self.answer(self.USER)
		self.assertEqual(answer["attention"]["setup"], [])
		self.assertEqual(answer["office"]["phone_href"], "tel:8015550142")
		self.assertEqual(answer["office"]["label"], "Travel desk")

	def test_the_list_stops_at_twenty_and_says_how_many_more(self):
		# 25 trips ready to close: every one ended 11 to 35 days ago, past the receipts week.
		HomeSite(
			self,
			*[hub_trip(f"TRIP-{n:02d}", -45 + n, -35 + n, status="Completed", clean=True) for n in range(25)],
			coordinator=True,
		)
		attention = self.answer(self.USER)["attention"]
		self.assertEqual(len(attention["trips"]), 20)
		self.assertEqual(attention["more"], 5)
		self.assertEqual(attention["trips"][0]["trip"], "TRIP-00")

	def test_a_finished_trip_is_ready_to_close_only_after_the_receipts_week(self):
		"""Closing locks the trip to everyone but a coordinator, and the same answer gives each
		traveler a week after getting back to attach their receipts to it. Offered the day after
		the trip ended, "ready to close" invited a coordinator to lock them out mid-week."""
		days = self.home.RECEIPTS_DAYS
		HomeSite(
			self,
			hub_trip("TRIP-1", -9, -1, status="Completed", clean=True),  # back yesterday
			hub_trip("TRIP-7", -12, -days, status="Completed", clean=True),  # the week's last day
			hub_trip("TRIP-8", -12, -days - 1, status="Completed", clean=True),  # the day after it
			coordinator=True,
		)
		answer = self.answer(self.USER)
		self.assertEqual(
			[(row["trip"], row["reasons"], row["target"]) for row in answer["attention"]["trips"]],
			[("TRIP-8", ["Finished — ready to close"], "form")],
		)
		self.assertNoMoney(answer)

	def test_the_receipts_week_and_ready_to_close_meet_with_no_gap_and_no_overlap(self):
		"""Day by day after a trip, the traveler is reminded or the coordinator is told to close
		it — never both, and never neither."""
		doc = hub_trip("TRIP-1", -3, -1, status="Completed")
		site = HomeSite(self, doc, coordinator=False)
		for back in range(1, self.home.RECEIPTS_DAYS + 4):
			with self.subTest(days_since_the_trip=back):
				move_trip(doc, -back - 2, -back)
				site.coordinator = False
				crew = self.answer()
				site.coordinator = True
				office = self.answer(self.USER)
				reminded = [row["trip"] for row in crew["receipts"]] == ["TRIP-1"]
				ready = any(
					"Finished — ready to close" in row["reasons"] for row in office["attention"]["trips"]
				)
				self.assertNotEqual(reminded, ready)
				self.assertEqual(ready, back > self.home.RECEIPTS_DAYS)
				self.assertNoMoney(crew)

	def test_only_thirty_trips_are_loaded_whole(self):
		HomeSite(
			self,
			*[hub_trip(f"TRIP-{n:02d}", 30 + n, 31 + n, clean=True) for n in range(35)],
			coordinator=True,
		)
		frappe = sys.modules["frappe"]
		loaded = []
		original = frappe.get_doc

		def get_doc(*args, **kwargs):
			loaded.append(args[1] if len(args) > 1 else args)
			return original(*args, **kwargs)

		with mock.patch.object(frappe, "get_doc", get_doc):
			self.answer(self.USER)
		self.assertEqual(len(loaded), 30)
		self.assertEqual(loaded[0], "TRIP-00")

	def test_one_trip_that_cannot_be_read_does_not_break_the_list(self):
		other = hub_trip("TRIP-G", 6, 9)
		HomeSite(
			self,
			hub_trip("TRIP-A", 5, 8, status="Planning"),
			other,
			coordinator=True,
			settings={"notifications_enabled": 1},
		)
		real = self.home.find_gaps
		gaps = real(other)
		expected = [
			f"{len(self.home.counted_gaps(gaps))} things missing",
			f"{self.home.files_not_attached(gaps)} files not attached",
		]

		def find_gaps_or_fail(doc):
			if doc.name == "TRIP-A":
				raise RuntimeError("a row the checklist cannot read")
			return real(doc)

		with mock.patch.object(self.home, "find_gaps", find_gaps_or_fail):
			answer = self.answer(self.USER)
		rows = {row["trip"]: row["reasons"] for row in answer["attention"]["trips"]}
		# TRIP-A keeps the reason its list row gives; TRIP-G is untouched.
		self.assertEqual(rows["TRIP-A"], ["Starts in 5 days and is still Planning"])
		self.assertEqual(rows["TRIP-G"], expected)
		self.assertEqual(len(self.logged()), 1)
		self.assertNoMoney(answer)

	def test_a_coordinator_on_a_trip_also_gets_their_own_card(self):
		install_site(hub_trip("TRIP-1", 2, 4))
		SITE.users["tc@example.com"] = "EMP-A"  # Ann's user, for this test
		HomeSite(self, coordinator=True)
		answer = self.answer(self.USER)
		self.assertEqual(answer["featured"]["trip"], "TRIP-1")
		self.assertEqual(answer["featured"]["sheet_url"], views.trip_sheet_url("TRIP-1", "EMP-A"))
		self.assertIsNotNone(answer["attention"])


class TestTravelHomeDegrades(HubAssertions):
	"""The featured card loses the part it cannot read, and keeps its links."""

	def setUp(self):
		super().setUp()
		self.doc = install_site(hub_trip("TRIP-1", -1, 4, status="In Progress"))
		ContactsSite(self, self.doc)
		HomeSite(self, hub_trip("TRIP-2", 10, 12))

	def test_files_that_cannot_be_read(self):
		with mock.patch.object(travel, "_trip_files", side_effect=RuntimeError("bad file row")):
			answer = self.answer()
		featured = answer["featured"]
		self.assertEqual(featured["documents"], 0)
		self.assertEqual(featured["docs_url"], "/itinerary?trip=TRIP-1&view=docs")
		self.assertEqual(featured["contacts"][-1]["label"], "Emergency")
		self.assertEqual([t["trip"] for t in answer["trips"]], ["TRIP-2"])
		self.assertEqual(len(self.logged()), 1)
		self.assertNoMoney(answer)

	def test_contacts_that_cannot_be_read(self):
		with mock.patch.object(travel, "_hotel_details", side_effect=RuntimeError("bad address")):
			featured = self.answer()["featured"]
		self.assertEqual(featured["contacts"], [])
		self.assertEqual(featured["documents"], 5)
		self.assertEqual(len(self.logged()), 1)

	def test_a_trip_that_cannot_be_loaded(self):
		frappe = sys.modules["frappe"]
		with mock.patch.object(frappe, "get_doc", side_effect=RuntimeError("gone")):
			answer = self.answer()
		featured = answer["featured"]
		self.assertEqual(featured["itinerary_url"], "/itinerary?trip=TRIP-1")
		self.assertEqual((featured["documents"], featured["contacts"], featured["sheet_url"]), (0, [], None))
		self.assertEqual(featured["headline"], "You're on this trip now — day 2 of 6")

	def test_no_trip_sheet_format_no_sheet_link(self):
		# A missing Trip Sheet prints as Standard, costs included: no link to it at all.
		SITE.print_formats = set()
		self.assertIsNone(self.answer()["featured"]["sheet_url"])


class TestTravelHomeThroughTheRealGate(HubAssertions):
	"""``TestTheCoordinatorGate``'s stand-ins: the real ``_is_coordinator``, down to the Travel
	Trip controller's role check. With the gate wired to True, a crew member would get the
	coordinator's list — and its checklist counts, cost gaps included."""

	ROLES = TestTheCoordinatorGate.ROLES

	def setUp(self):
		super().setUp()
		install_site(hub_trip("TRIP-1", 5, 8, status="Planning"))
		frappe = sys.modules["frappe"]
		document = types.ModuleType("frappe.model.document")
		document.Document = type("Document", (), {})
		model = types.ModuleType("frappe.model")
		model.document = document
		for patcher in (
			mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}),
			mock.patch.object(frappe, "get_roles", lambda user=None: self.ROLES.get(user, []), create=True),
			mock.patch.object(frappe.utils, "date_diff", lambda a, b: 0, create=True),
		):
			patcher.start()
			self.addCleanup(patcher.stop)
		sys.modules.pop(CONTROLLER, None)
		HomeSite(self, coordinator=None)

	def test_a_crew_member_gets_no_list(self):
		answer = self.answer("bo@example.com")
		self.assertIs(answer["viewer"]["is_coordinator"], False)
		self.assertIsNone(answer["attention"])
		self.assertEqual(answer["featured"]["trip"], "TRIP-1")
		self.assertNoMoney(answer)

	def test_a_travel_coordinator_gets_it(self):
		answer = self.answer("tc@example.com")
		self.assertIs(answer["viewer"]["is_coordinator"], True)
		self.assertEqual(answer["attention"]["trips"][0]["trip"], "TRIP-1")
		self.assertIn("Starts in 5 days and is still Planning", answer["attention"]["trips"][0]["reasons"])


class TestTravelHomeEndpoint(unittest.TestCase):
	def setUp(self):
		self.home = _home()
		with open(inspect.getsourcefile(self.home), encoding="utf-8") as fh:
			self.source = fh.read()
		# Comments and docstrings may name what the code must not do.
		self.code = re.sub(r'""".*?"""', "", self.source, flags=re.S)
		self.code = "\n".join(line for line in self.code.splitlines() if not line.strip().startswith("#"))

	def test_it_is_the_one_endpoint_and_takes_nothing(self):
		self.assertRegex(self.source, re.compile(r"@frappe\.whitelist\(\)\ndef get_travel_home\(\):"))
		self.assertEqual(self.source.count("@frappe.whitelist"), 1)
		self.assertEqual(list(inspect.signature(self.home.get_travel_home).parameters), [])

	def test_dates_are_the_sites(self):
		for forbidden in ("datetime.now", "utcnow", "date.today", "import datetime", "from datetime"):
			self.assertNotIn(forbidden, self.code)

	def test_the_viewers_lists_are_permission_scoped(self):
		source = inspect.getsource(self.home._my_trips)
		self.assertEqual(source.count("frappe.get_list("), 2)
		# The one get_all is the viewer's own crew rows.
		self.assertEqual(re.findall(r'frappe\.get_all\(\s*"([^"]+)"', source), ["Trip Traveler"])

	def test_a_crew_members_answer_has_the_contract_keys(self):
		install_site(hub_trip("TRIP-1", -1, 4, status="In Progress"))
		HomeSite(self, hub_trip("TRIP-2", 10, 12, owner="bo@example.com", clean=True))
		sys.modules["frappe"].session.user = "bo@example.com"
		answer = self.home.get_travel_home()
		self.assertEqual(
			set(answer),
			{"viewer", "message", "office", "guidelines_url", "featured", "trips", "receipts", "attention"},
		)
		self.assertEqual(
			set(answer["featured"]),
			{
				"trip",
				"purpose",
				"status",
				"dates",
				"headline",
				"travel_for",
				"itinerary_url",
				"docs_url",
				"sheet_url",
				"documents",
				"contacts",
			},
		)
		for row in answer["featured"]["contacts"]:
			self.assertEqual(
				set(row), {"label", "name", "detail", "phone", "phone_href", "email", "email_href", "links"}
			)
		self.assertEqual(
			set(answer["trips"][0]),
			{"trip", "purpose", "status", "dates", "relation", "note", "itinerary_url", "can_plan"},
		)


# --------------------------------------------------------------------------- every trip, for staff
#
# Nik, 2026-09-28: "Change it from My Trips to just Trips so anyone can see anyone's trips —
# should link to the non-desk form of it automatically." Asked where that applies, he chose the
# itinerary pages only. Any member of staff lists every trip (`get_all_trips`, behind
# /itinerary?view=trips) and opens any of them on /itinerary. The desk's Travel Trip list, form
# and Report view stay row-scoped, because money is permlevel 0 there. A staff member who is not
# on a trip, did not make it and is not a coordinator gets the *outsider view*: no confirmation,
# booking or tracking number, no files, no Trip Sheet, `limited: true`. Guest and Website Users
# are not staff.
#
# All three failures below would look fine on screen:
# * a portal customer listing every trip;
# * a staff member reading a PNR, a boarding pass or a tracking number off a trip they are not on;
# * a key added to shape_itinerary next month that carries a number past the redaction.
# The last is caught by pinning every key the answer sends to one side of the line or the other.

#: The keys an outsider is not sent (``api.travel._OUTSIDER_REMOVED``, ``_EMPTIED`` and ``_NULLED``
#: together): every confirmation, booking and tracking number, the files, the Trip Sheet links.
OUTSIDER_WITHHELD = frozenset(
	{
		"booking_reference",
		"booking_confirmation",
		"tracking_number",
		"ref",
		"documents",
		"sheet_url",
		"my_sheet_url",
	}
)

#: Every other key ``get_trip_itinerary`` sends, anywhere in its answer outside the files. Each is
#: something a crew member sees that is not a number, a file or money, and so an outsider sees it
#: too. **A key in neither set fails ``test_every_key_the_answer_sends_is_on_one_side``.** Before
#: adding one here, ask whether it identifies a booking (a confirmation, a ticket, a locker code, a
#: file). If it does, it goes in ``api.travel``'s withheld sets instead.
OUTSIDER_SEES = frozenset(
	{
		# The answer
		"trip",
		"purpose",
		"status",
		"travel_type",
		"start_date",
		"end_date",
		"travel_for_doctype",
		"travel_for",
		"days",
		"crew",
		"viewing",
		"viewer_employee",
		"viewer_on_trip",
		"contacts",
		"limited",
		# A day, and what every item carries
		"date",
		"items",
		"type",
		"sort_time",
		"group",
		"travelers",
		"members",
		"whole_crew",
		# A flight
		"airline",
		"flight_number",
		"departure_airport",
		"departure_time",
		"arrival_airport",
		"arrival_time",
		# A room
		"hotel",
		"address",
		"time",
		# A ride
		"transport_type",
		"provider",
		"pickup_location",
		"dropoff_location",
		"pickup_datetime",
		"arrival_datetime",
		"return_datetime",
		"cargo",
		# A shipment
		"carrier",
		"contents",
		"ship_from",
		"deliver_to",
		"pickup_from",
		"pickup_to",
		"delivery_from",
		"delivery_to",
		"received_by",
		# A stop, and its place
		"end_time",
		"activity",
		"related_party_doctype",
		"related_party",
		"poi",
		"visit_notes",
		"name",
		"poi_name",
		"category",
		"lat",
		"lng",
		"notes",
		# A person on a booking (the whole crew's view), and on the crew
		"employee",
		"employee_name",
		"guest",
		"check_in_date",
		"check_out_date",
		"from_date",
		"to_date",
		"is_trip_lead",
		# Whom to call (``_trip_contacts``, which has its own tests for what it may read)
		"emergency",
		"office",
		"booked_by",
		"lead",
		"site",
		"hotels",
		"label",
		"phone",
		"email",
		"contact_name",
		"urgent_care_url",
		"directions_url",
	}
)

#: Every confirmation, booking and tracking number on the shared fixture, and every file. None may
#: reach someone who is not on the trip, and the trip list carries none of them either.
FIXTURE_SECRETS = (
	"PNR-A",
	"PNR-B",
	"H-A",
	"H-B",
	"RC-1",
	"PRO-1",
	"site-map.png",
	"bo-pass.pdf",
	"hotel.pdf",
	"ann-packet.pdf",
	"rental.pdf",
	"old.pdf",
	"/files/",
	"download_pdf",
	"Trip%20Sheet",
)


def keys_outside_files(value, found=None):
	"""Every key anywhere in ``value``, but not inside a ``documents`` list: an outsider gets those
	empty, whatever their entries hold."""
	found = set() if found is None else found
	if isinstance(value, dict):
		for key, child in value.items():
			found.add(key)
			if key != "documents":
				keys_outside_files(child, found)
	elif isinstance(value, (list, tuple)):
		for child in value:
			keys_outside_files(child, found)
	return found


def as_an_outsider_would_get_it(value):
	"""The test's own reading of the outsider view, written apart from ``api.travel``'s: the
	withheld numbers dropped, the files empty, the sheet links null, and nothing else touched."""
	if isinstance(value, dict):
		out = {}
		for key, child in value.items():
			if key in ("booking_reference", "booking_confirmation", "tracking_number", "ref"):
				continue
			if key == "documents":
				out[key] = []
			elif key in ("sheet_url", "my_sheet_url"):
				out[key] = None
			else:
				out[key] = as_an_outsider_would_get_it(child)
		return out
	if isinstance(value, (list, tuple)):
		return [as_an_outsider_would_get_it(child) for child in value]
	return value


def all_trip(name, start, end, status="Booked", owner="office@example.com", clean=False, **overrides):
	"""A trip from the shared fixture for the list of every trip, from ``start`` to ``end`` days
	after the stub's today (``None`` for a blank date)."""
	doc = hub_trip(name, 0, 0, status=status, owner=owner, clean=clean, **overrides)
	doc.start_date = None if start is None else hub_day(start)
	doc.end_date = None if end is None else hub_day(end)
	return doc


def _clauses(filters):
	"""frappe's filter forms (a dict, or a list of ``[field, op, value]`` or
	``[doctype, field, op, value]``) as ``(field, op, value)`` triples."""
	if not filters:
		return []
	if isinstance(filters, dict):
		return [
			(field, *(condition if isinstance(condition, (list, tuple)) else ("=", condition)))
			for field, condition in filters.items()
		]
	return [tuple(clause[-3:]) for clause in filters]


def _clause_holds(row, clause):
	field, op, value = clause
	have = _hub_norm(row.get(field))
	blank = have in (None, "")
	if op == "is":
		return blank if value == "not set" else not blank
	if op == "=":
		return have == value
	if op == "in":
		return have in list(value)
	if op == ">=":
		# frappe v16 wraps no `>=` on a date in a coalesce: a blank never passes.
		return not blank and str(have) >= str(value)
	if op == "<":
		# frappe v16 compiles `<` on a nullable date as coalesce(col, '0001-01-01') < value, so a
		# blank passes. That is why get_all_trips says `is set` as well.
		return str("0001-01-01" if blank else have) < str(value)
	raise AssertionError(f"the trip list's fake site does not know the filter {op!r}")


def _column_key(value):
	"""One column's sort key, a blank first when ascending (as MariaDB puts NULL)."""
	value = _hub_norm(value)
	if value in (None, ""):
		return (0, "")
	if isinstance(value, (int, float)):
		return (1, f"{value:015.3f}")
	return (1, str(value))


class AllTripsSite:
	"""The fake site ``get_all_trips`` reads, built from the trips on ``SITE.trips``.
	``get_all`` applies ``filters`` (all of them) and ``or_filters`` (any of them), as frappe
	composes the two. It sorts by one column, and ``limit_page_length`` of 0 or None means every
	row. A column the table does not have raises, as SQL does. Every call is recorded with what it
	asked, so a test can say which reads ran and which columns they read. Money sits in the tables
	(the trips' totals, each traveler's per diem and claim), so a read that took a row wholesale
	would carry it."""

	RECORDS = {
		("Project", "PRJ-1"): {"project_name": "Harbor Fountain", "customer": "CUST-1"},
		("Customer", "CUST-1"): {"customer_name": "Harbor Resort", "mobile_no": "(208) 555-0100"},
	}

	def __init__(self, test, *docs):
		frappe = sys.modules["frappe"]
		for doc in docs:
			SITE.trips[doc.name] = doc
		self.calls = []

		class Meta:
			def __init__(self, doctype):
				self.fields = META_FIELDS.get(doctype, set())

			def has_field(self, fieldname):
				return fieldname in self.fields

		def count(doctype, *args, **kwargs):
			self.calls.append(("count", doctype, {}))
			return len(self.table(doctype))

		for patcher in (
			mock.patch.object(frappe, "get_all", self.get_all),
			mock.patch.object(frappe.db, "count", count, create=True),
			mock.patch.object(frappe, "get_meta", Meta, create=True),
		):
			patcher.start()
			test.addCleanup(patcher.stop)

	def table(self, doctype):
		if doctype == "Travel Trip":
			return [
				_dict(
					name=doc.name,
					purpose=doc.purpose,
					status=doc.status,
					travel_type=doc.travel_type,
					start_date=doc.start_date,
					end_date=doc.end_date,
					travel_for_doctype=doc.travel_for_doctype,
					travel_for_name=doc.travel_for_name,
					owner=doc.owner,
					total_estimated_cost=doc.total_estimated_cost,
					total_per_diem=doc.total_per_diem,
					billable=doc.billable,
				)
				for doc in SITE.trips.values()
			]
		if doctype == "Trip Traveler":
			return [
				_dict(
					parent=doc.name,
					parenttype="Travel Trip",
					idx=idx,
					employee=row.employee,
					employee_name=row.employee_name,
					is_trip_lead=row.is_trip_lead or 0,
					per_diem_amount=row.per_diem_amount,
					advance_amount=row.advance_amount,
					expense_claim=row.expense_claim,
				)
				for doc in SITE.trips.values()
				for idx, row in enumerate(doc.travelers or [], 1)
			]
		rows = [
			_dict(name=name, **record) for (kind, name), record in self.RECORDS.items() if kind == doctype
		]
		if rows or doctype in META_FIELDS:
			return rows
		raise AssertionError(f"the trip list asked the site for {doctype}")

	def get_all(
		self,
		doctype,
		filters=None,
		or_filters=None,
		fields=None,
		order_by=None,
		limit_page_length=None,
		pluck=None,
		**kwargs,
	):
		self.calls.append(
			(
				"get_all",
				doctype,
				{
					"filters": filters,
					"or_filters": or_filters,
					"fields": fields,
					"order_by": order_by,
					"limit_page_length": limit_page_length,
				},
			)
		)
		table = self.table(doctype)
		columns = set(META_FIELDS.get(doctype, ())) | {"name"} | set(table[0] if table else ())
		for field in fields or ():
			if table and field not in columns:
				raise Exception(f"(1054, \"Unknown column '{field}' in 'SELECT'\")")
		rows = [row for row in table if all(_clause_holds(row, clause) for clause in _clauses(filters))]
		either = _clauses(or_filters)
		if either:
			rows = [row for row in rows if any(_clause_holds(row, clause) for clause in either)]
		if order_by:
			field, _space, direction = order_by.partition(" ")
			rows.sort(
				key=lambda row: _column_key(row.get(field)), reverse=direction.strip().lower() == "desc"
			)
		if limit_page_length:
			rows = rows[:limit_page_length]
		if pluck:
			return [row.get(pluck) for row in rows]
		return [_dict({f: row.get(f) for f in fields}) if fields else _dict(row) for row in rows]

	def asked(self, doctype, kind="get_all"):
		return [asked for call, name, asked in self.calls if call == kind and name == doctype]


def use_real_roles(test, roles):
	"""The real gates, down to the roles. ``frappe.get_roles`` answers from ``roles``, and the
	Travel Trip controller that ``_is_coordinator`` imports gets the two stand-ins it needs, as in
	``TestTheCoordinatorGate``. ``frappe.has_permission`` on a trip is answered as v16 answers it
	on the site. First, a role must have read on Travel Trip: the doctype's own DocPerms, read
	from its JSON. Then this app's row-level hook, ``travel_management.permissions.has_permission``,
	runs for real: coordinators, the owner, the crew."""
	from erpnext_enhancements.travel_management import permissions

	frappe = sys.modules["frappe"]
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model = types.ModuleType("frappe.model")
	model.document = document
	path = os.path.join(APP_DIR, "travel_management", "doctype", "travel_trip", "travel_trip.json")
	with open(path, encoding="utf-8") as fh:
		readers = {perm["role"] for perm in json.load(fh)["permissions"] if perm.get("read")}

	def get_roles(user=None):
		return list(roles.get(user or frappe.session.user, []))

	def has_permission(doctype=None, ptype="read", doc=None, throw=False, **kwargs):
		SITE.permission_checks.append((doctype, ptype, getattr(doc, "name", doc), throw))
		user = frappe.session.user
		allowed = user == "Administrator" or bool(readers & set(get_roles(user)))
		if allowed and doc is not None:
			allowed = bool(permissions.has_permission(doc, ptype, user))
		if throw and not allowed:
			raise frappe.PermissionError("No permission")
		return allowed

	for patcher in (
		mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}),
		mock.patch.object(frappe, "get_roles", get_roles, create=True),
		mock.patch.object(frappe.utils, "date_diff", lambda a, b: 0, create=True),
		mock.patch.object(frappe, "has_permission", has_permission),
	):
		patcher.start()
		test.addCleanup(patcher.stop)
	sys.modules.pop(CONTROLLER, None)


class OutsiderAssertions(MoneyAssertions):
	def assertNoSecrets(self, payload):
		text = json.dumps(payload, default=str)
		for secret in FIXTURE_SECRETS:
			self.assertNotIn(secret, text, f"{secret} reached someone who is not on the trip")

	def assertOutsiderView(self, result):
		"""No number, file or sheet anywhere in ``result``, and no money."""
		self.assertIs(result["limited"], True)
		self.assertEqual(
			keys_outside_files(result)
			& {"booking_reference", "booking_confirmation", "tracking_number", "ref"},
			set(),
		)
		self.assertEqual(result["documents"], [])
		self.assertEqual([i["documents"] for i in items_of(result["days"]) if i.get("documents")], [])
		self.assertIsNone(result["sheet_url"])
		self.assertIsNone(result["my_sheet_url"])
		self.assertNoSecrets(result)
		self.assertNoMoney(result)
		self.assertEqual(personal_paths(result), [])


class TestAllTrips(OutsiderAssertions):
	"""``get_all_trips`` for a member of staff. The gate is patched here and run for real in
	``TestTheStaffGate``. The viewer is Bo, who travels on some of these trips and made others.
	The stub's today is Saturday, September 26, 2026."""

	def trips(self):
		return [
			all_trip(
				"T-NOW-2",
				-1,
				3,
				status="In Progress",
				owner="bo@example.com",
				# Bo made it and is not on it. Zed's row has no name yet: his id stands in.
				travelers=[FakeRow(name="T1", employee="EMP-Z", employee_name=None)],
				travel_for_doctype="Customer",
				travel_for_name="CUST-1",
			),
			# Bo made it and travels on it: his, not one he organized for others.
			all_trip("T-NOW-1", -3, 0, status="In Progress", owner="bo@example.com"),
			all_trip("T-TODAY", 0, 0),
			all_trip(
				"T-UP-2",
				20,
				22,
				status="Planning",
				owner="bo@example.com",
				clean=True,
				travel_for_doctype=None,
				travel_for_name=None,
			),
			all_trip("T-UP-1", 1, 2),
			# Both dates are required. A row that got past validation without one is coming up, last.
			all_trip("T-BLANK", -50, None, status="Planning"),
			all_trip("T-PAST-3", -400, -398, status="Closed", travel_for_name="PRJ-GONE"),
			all_trip(
				"T-PAST-1",
				-10,
				-1,
				status="Completed",
				# The trip lead is the last row: they still come first.
				travelers=[
					FakeRow(name="T1", employee="EMP-C", employee_name="Cy"),
					FakeRow(name="T2", employee="EMP-A", employee_name="Ann", is_trip_lead=1),
				],
			),
			all_trip("T-PAST-2", -40, -30, status="Closed"),
		]

	def setUp(self):
		docs = self.trips()
		install_site(docs[0])
		self.site = AllTripsSite(self, *docs[1:])
		patcher = mock.patch.object(travel, "_is_staff", return_value=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def answer(self, user="bo@example.com"):
		sys.modules["frappe"].session.user = user
		return travel.get_all_trips()

	def test_every_trip_is_listed_once_grouped_and_in_order(self):
		answer = self.answer()
		self.assertEqual(
			[(t["name"], t["group"]) for t in answer["trips"]],
			[
				# On the road now, by first day. A trip ending today is still on the road.
				("T-NOW-1", "now"),
				("T-NOW-2", "now"),
				("T-TODAY", "now"),
				# Coming up, by first day. The one missing a date is last.
				("T-UP-1", "upcoming"),
				("T-UP-2", "upcoming"),
				("T-BLANK", "upcoming"),
				# Earlier trips, the most recently ended first. One ending yesterday is over.
				("T-PAST-1", "past"),
				("T-PAST-2", "past"),
				("T-PAST-3", "past"),
			],
		)
		self.assertEqual(answer["more"], 0)
		# "Anyone's trips" is every trip, the Closed ones included.
		self.assertEqual(
			{t["status"] for t in answer["trips"] if t["group"] == "past"}, {"Completed", "Closed"}
		)
		# Nothing left off, so the table was not counted.
		self.assertEqual(self.site.asked("Travel Trip", "count"), [])

	def test_each_row_is_the_contract_and_nothing_more(self):
		answer = self.answer()
		self.assertEqual(set(answer), {"trips", "more"})
		rows = {t["name"]: t for t in answer["trips"]}
		self.assertEqual(
			rows["T-NOW-1"],
			{
				"name": "T-NOW-1",
				"purpose": "Trip T-NOW-1",
				"status": "In Progress",
				"travel_type": "Domestic",
				"start_date": "2026-09-23",
				"end_date": "2026-09-26",
				"travel_for": "Harbor Fountain",
				"crew": ["Ann", "Bo", "Cy"],
				"lead": "Ann",
				"mine": True,
				"organizing": False,
				"group": "now",
			},
		)
		for row in answer["trips"]:
			self.assertEqual(set(row), set(rows["T-NOW-1"]), row["name"])
		self.assertEqual((rows["T-BLANK"]["start_date"], rows["T-BLANK"]["end_date"]), ("2026-08-07", None))
		self.assertNoMoney(answer)
		self.assertNoSecrets(answer)

	def test_it_reads_no_money_and_no_bookings(self):
		self.answer()
		asked = {name for call, name, _asked in self.site.calls}
		# The trips, their crews and what they are for. No booking table, no file, no cost row.
		self.assertEqual(asked, {"Travel Trip", "Trip Traveler", "Project", "Customer"})
		for doctype in ("Travel Trip", "Trip Traveler"):
			for read in self.site.asked(doctype):
				self.assertEqual(money_paths({field: 1 for field in read["fields"]}), [], doctype)
		self.assertEqual(
			self.site.asked("Travel Trip")[0]["fields"],
			[
				"name",
				"purpose",
				"status",
				"travel_type",
				"start_date",
				"end_date",
				"travel_for_doctype",
				"travel_for_name",
				"owner",
			],
		)

	def test_the_trip_lead_comes_first(self):
		rows = {t["name"]: t for t in self.answer()["trips"]}
		self.assertEqual((rows["T-PAST-1"]["crew"], rows["T-PAST-1"]["lead"]), (["Ann", "Cy"], "Ann"))
		# No lead: the trip's order, and a person whose name is blank shows as their id.
		self.assertEqual((rows["T-NOW-2"]["crew"], rows["T-NOW-2"]["lead"]), (["EMP-Z"], None))
		self.assertEqual((rows["T-UP-2"]["crew"], rows["T-UP-2"]["lead"]), ([], None))

	def test_mine_and_organizing(self):
		rows = {t["name"]: t for t in self.answer()["trips"]}
		relation = {name: (row["mine"], row["organizing"]) for name, row in rows.items()}
		self.assertEqual(relation["T-NOW-1"], (True, False), "he made it and travels on it: his")
		self.assertEqual(relation["T-NOW-2"], (False, True), "he made it for someone else")
		self.assertEqual(relation["T-UP-2"], (False, True), "he made it and nobody is on it yet")
		self.assertEqual(relation["T-UP-1"], (True, False), "the office made it; he travels on it")
		self.assertEqual(relation["T-PAST-1"], (False, False), "neither")
		# Someone with no Employee record (the office) travels on nothing, and made the rest.
		rows = {t["name"]: t for t in self.answer("office@example.com")["trips"]}
		self.assertFalse(any(row["mine"] for row in rows.values()))
		self.assertEqual(
			sorted(name for name, row in rows.items() if row["organizing"]),
			["T-BLANK", "T-PAST-1", "T-PAST-2", "T-PAST-3", "T-TODAY", "T-UP-1"],
		)

	def test_what_it_is_for_is_the_jobs_name(self):
		rows = {t["name"]: t for t in self.answer()["trips"]}
		self.assertEqual(rows["T-NOW-1"]["travel_for"], "Harbor Fountain")
		self.assertEqual(rows["T-NOW-2"]["travel_for"], "Harbor Resort")
		self.assertIsNone(rows["T-UP-2"]["travel_for"], "a trip for nothing in particular")
		self.assertIsNone(rows["T-PAST-3"]["travel_for"], "a Project that is gone")
		# One read per doctype for every trip on the list, and only the name field.
		reads = self.site.asked("Project")
		self.assertEqual(len(reads), 1)
		self.assertEqual(reads[0]["fields"], ["name", "project_name"])
		self.assertEqual(reads[0]["filters"], {"name": ["in", ["PRJ-1", "PRJ-GONE"]]})

	def test_a_name_that_cannot_be_read_leaves_the_list_standing(self):
		frappe = sys.modules["frappe"]

		def boom(doctype):
			raise Exception(f"DocType {doctype} not found")

		with mock.patch.object(frappe, "get_meta", boom):
			rows = self.answer()["trips"]
		self.assertEqual(len(rows), 9)
		self.assertEqual({row["travel_for"] for row in rows}, {None})

	def test_today_is_the_sites(self):
		# Four days on: the trips on the road and the next one are over. Nothing about a trip moved.
		with mock.patch.object(travel, "today", return_value="2026-09-30"):
			answer = self.answer()
		groups = {t["name"]: t["group"] for t in answer["trips"]}
		self.assertEqual(
			[name for name, group in groups.items() if group == "past"][:4],
			["T-NOW-2", "T-UP-1", "T-NOW-1", "T-TODAY"],
		)
		self.assertEqual(groups["T-UP-2"], "upcoming")
		self.assertIn(["end_date", ">=", "2026-09-30"], self.site.asked("Travel Trip")[0]["or_filters"])
		code = re.sub(r'""".*?"""', "", inspect.getsource(travel.get_all_trips), flags=re.S)
		for forbidden in ("datetime.now", "utcnow", "date.today", "now_datetime"):
			self.assertNotIn(forbidden, code)

	def test_the_cap_drops_the_trips_furthest_from_today_and_says_how_many(self):
		del SITE.trips["T-BLANK"]  # it has the earliest first day; this is about the rest
		with mock.patch.object(travel, "ALL_TRIPS_LIMIT", 3):
			answer = self.answer()
		self.assertEqual([t["name"] for t in answer["trips"]], ["T-NOW-1", "T-NOW-2", "T-TODAY"])
		self.assertEqual(answer["more"], 5)
		# No room left: the earlier trips were never asked for (a limit of 0 is every row to frappe).
		self.assertEqual(len(self.site.asked("Travel Trip")), 1)
		self.assertEqual(self.site.asked("Travel Trip")[0]["limit_page_length"], 3)

		self.site.calls.clear()
		with mock.patch.object(travel, "ALL_TRIPS_LIMIT", 6):
			answer = self.answer()
		self.assertEqual(
			[t["name"] for t in answer["trips"]],
			["T-NOW-1", "T-NOW-2", "T-TODAY", "T-UP-1", "T-UP-2", "T-PAST-1"],
		)
		self.assertEqual(answer["more"], 2)
		self.assertEqual([read["limit_page_length"] for read in self.site.asked("Travel Trip")], [6, 1])
		self.assertEqual(len(self.site.asked("Travel Trip", "count")), 1)

		self.assertEqual(travel.ALL_TRIPS_LIMIT, 300)

	def test_the_gate_comes_before_any_read(self):
		frappe = sys.modules["frappe"]
		with mock.patch.object(travel, "_is_staff", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				self.answer()
		self.assertEqual(self.site.calls, [])
		# And the table is read with get_all only below the gate.
		source = inspect.getsource(travel.get_all_trips)
		self.assertLess(source.index("_is_staff()"), source.index("frappe.get_all("))


class TestTheStaffGate(OutsiderAssertions):
	"""``_is_staff`` for real, down to the roles, and the two doors it keeps: the list of every
	trip, and a trip someone cannot read. Staff is a coordinator or the Employee role. A portal
	customer (a Website User) and a desk account whose profile has no Employee role are not staff,
	and neither is Guest."""

	ROLES = {
		"bo@example.com": ["Employee"],
		"zed@example.com": ["Employee"],
		"office@example.com": ["Employee"],
		"tc@example.com": ["Travel Coordinator"],
		"hr@example.com": ["HR Manager"],
		"sm@example.com": ["System Manager"],
		# A customer signed in to the portal: a Website User.
		"portal@example.com": ["Customer"],
		# A desk account whose Role Profile has no Employee role.
		"desk@example.com": ["Projects User"],
		"Guest": ["Guest"],
	}

	def setUp(self):
		self.doc = install_site()
		self.doc.creation = "2026-09-01 09:00:00"
		self.doc.owner = "office@example.com"
		SITE.users["zed@example.com"] = "EMP-Z"
		self.site = AllTripsSite(self)
		use_real_roles(self, self.ROLES)

	def as_user(self, user):
		sys.modules["frappe"].session.user = user

	def test_who_is_staff(self):
		for user, staff in (
			("bo@example.com", True),
			("zed@example.com", True),
			("tc@example.com", True),
			("hr@example.com", True),
			("sm@example.com", True),
			("Administrator", True),
			("portal@example.com", False),
			("desk@example.com", False),
			("Guest", False),
			("", False),
		):
			self.as_user(user)
			self.assertIs(travel._is_staff(), staff, user)
		# Guest is never staff, whatever a misconfigured site says its roles are.
		self.as_user("Guest")
		with mock.patch.dict(self.ROLES, {"Guest": ["Guest", "Employee", "Travel Coordinator"]}):
			self.assertIs(travel._is_staff(), False)

	def test_only_staff_may_list_every_trip(self):
		frappe = sys.modules["frappe"]
		for user in ("portal@example.com", "desk@example.com", "Guest"):
			self.site.calls.clear()
			self.as_user(user)
			with self.assertRaises(frappe.PermissionError):
				travel.get_all_trips()
			self.assertEqual(self.site.calls, [], f"{user}: the trips were read before the gate")
		for user in ("zed@example.com", "tc@example.com", "Administrator"):
			self.as_user(user)
			self.assertEqual([t["name"] for t in travel.get_all_trips()["trips"]], ["TRIP-1"], user)

	def test_only_staff_may_open_a_trip_they_cannot_read(self):
		frappe = sys.modules["frappe"]
		for user in ("portal@example.com", "desk@example.com", "Guest"):
			self.as_user(user)
			with self.assertRaises(frappe.PermissionError):
				travel.get_trip_itinerary("TRIP-1")
		# Zed is staff and not on it: the outsider view. Bo travels on it, the office made it and a
		# coordinator sees every trip: the whole answer, as before.
		self.as_user("zed@example.com")
		self.assertOutsiderView(travel.get_trip_itinerary("TRIP-1"))
		for user in ("bo@example.com", "office@example.com", "tc@example.com"):
			self.as_user(user)
			self.assertIs(travel.get_trip_itinerary("TRIP-1")["limited"], False, user)


class TestTheOutsidersItinerary(OutsiderAssertions):
	"""``get_trip_itinerary`` for a member of staff who is not on the trip (Zed). The permission
	hook and the staff gate are the real ones (``use_real_roles``). The contacts card is filled
	(``ContactsSite``), so the answer is compared with everything it can carry."""

	ROLES = {
		"bo@example.com": ["Employee"],
		"zed@example.com": ["Employee"],
		"office@example.com": ["Employee"],
		"tc@example.com": ["Travel Coordinator"],
	}

	def setUp(self):
		self.doc = install_site()
		self.doc.creation = "2026-09-01 09:00:00"
		SITE.users["zed@example.com"] = "EMP-Z"
		ContactsSite(self, self.doc)  # the office made the trip
		use_real_roles(self, self.ROLES)

	def call(self, user="zed@example.com", as_employee=None):
		sys.modules["frappe"].session.user = user
		return travel.get_trip_itinerary("TRIP-1", as_employee)

	def full(self, as_employee=None):
		"""The answer Zed would get if he could read the trip."""
		frappe = sys.modules["frappe"]
		with mock.patch.object(frappe, "has_permission", lambda *a, **k: True):
			return self.call(as_employee=as_employee)

	def test_staff_not_on_the_trip_see_it_without_numbers_or_files(self):
		result = self.call()
		self.assertOutsiderView(result)
		# The whole crew: they are not on it.
		self.assertIsNone(result["viewing"])
		self.assertIs(result["viewer_on_trip"], False)
		self.assertEqual(result["viewer_employee"], "EMP-Z")
		# Everything else a crew member sees: every day and booking, the crew and whom to call.
		items = items_of(result["days"])
		self.assertEqual(
			sorted({item["type"] for item in items}),
			["agenda", "flight", "freight", "ground", "hotel_checkin", "hotel_checkout"],
		)
		flight = next(item for item in items if item.get("flight_number") == "WN 1")
		self.assertEqual([m["employee_name"] for m in flight["members"]], ["Ann", "Bo"])
		self.assertEqual(flight["travelers"], ["Ann", "Bo"])
		shipment = next(item for item in items if item["type"] == "freight")
		self.assertEqual((shipment["carrier"], shipment["received_by"]), ("Old Dominion", "Cy"))
		self.assertEqual([c["employee"] for c in result["crew"]], ["EMP-A", "EMP-B", "EMP-C"])
		self.assertEqual(result["contacts"]["site"]["label"], "Harbor Fountain")
		self.assertEqual(result["contacts"]["booked_by"]["name"], "Olive Office")
		self.assertEqual(result["contacts"]["lead"]["name"], "Ann")
		self.assertEqual([h["name"] for h in result["contacts"]["hotels"]], ["Hotel One"])
		self.assertEqual(len(result["days"]), len(self.full()["days"]))

	def test_it_is_the_whole_answer_less_exactly_the_withheld_keys(self):
		for who in (None, "crew", "EMP-A", "EMP-B", "EMP-C"):
			self.assertEqual(
				self.call(as_employee=who),
				dict(as_an_outsider_would_get_it(self.full(who)), limited=True),
				who,
			)

	def test_every_key_the_answer_sends_is_on_one_side(self):
		sent = set()
		for who in (None, "crew", "EMP-A", "EMP-B", "EMP-C"):
			sent |= keys_outside_files(self.full(who))
		self.assertEqual(
			sent - OUTSIDER_SEES - OUTSIDER_WITHHELD,
			set(),
			"get_trip_itinerary sends a key nobody decided about. If it identifies a booking (a "
			"number, a code, a file), add it to api.travel's _OUTSIDER_REMOVED; if not, to "
			"OUTSIDER_SEES here.",
		)
		self.assertEqual(OUTSIDER_SEES & OUTSIDER_WITHHELD, set())
		self.assertEqual(
			travel._OUTSIDER_REMOVED | travel._OUTSIDER_EMPTIED | travel._OUTSIDER_NULLED, OUTSIDER_WITHHELD
		)
		# The fixture sends every withheld key, or the checks above would pass on nothing.
		self.assertLessEqual(OUTSIDER_WITHHELD, sent)
		full = json.dumps(self.full(), default=str)
		for secret in FIXTURE_SECRETS:
			self.assertIn(secret, full, f"the fixture no longer carries {secret}")

	def test_they_may_view_as_anyone_on_the_crew_and_it_is_withheld_there_too(self):
		frappe = sys.modules["frappe"]
		for who in ("EMP-A", "EMP-B", "EMP-C", "crew"):
			result = self.call(as_employee=who)
			self.assertOutsiderView(result)
			self.assertEqual(result["viewing"], None if who == "crew" else who)
		ann = items_of(self.call(as_employee="EMP-A")["days"])
		self.assertEqual(
			[(i["type"], i.get("flight_number")) for i in ann if i["type"] == "flight"],
			[("flight", "DL 9"), ("flight", "WN 1")],
		)
		# Still only the crew: himself (he is not on it) and a filter are refused, and never named.
		for stranger in ("EMP-Z", {"name": "EMP-A"}):
			with self.assertRaises(frappe.ValidationError) as refused:
				self.call(as_employee=stranger)
			self.assertEqual(str(refused.exception), "That person is not on this trip.")

	def test_who_can_read_the_trip_gets_it_all_as_before(self):
		# An outsider's answer first: the redaction must change nothing it shares with the next.
		self.assertOutsiderView(self.call())
		for user, viewing, refs in (
			("bo@example.com", "EMP-B", ("PNR-B", "H-B", "RC-1")),
			("office@example.com", None, ("PNR-A", "PNR-B", "H-A", "H-B", "RC-1", "PRO-1")),
			("tc@example.com", None, ("PNR-A", "PNR-B", "H-A", "H-B", "RC-1", "PRO-1")),
		):
			result = self.call(user)
			self.assertIs(result["limited"], False, user)
			self.assertEqual(result["viewing"], viewing, user)
			self.assertTrue(result["documents"], user)
			self.assertEqual(result["sheet_url"], views.trip_sheet_url("TRIP-1"), user)
			text = json.dumps(result, default=str)
			for ref in refs:
				self.assertIn(ref, text, f"{user} lost {ref}")
			self.assertNoMoney(result)


class TestTheOutsiderViewItself(unittest.TestCase):
	def test_it_drops_the_numbers_at_any_depth_and_changes_nothing_it_is_given(self):
		given = {
			"days": [
				{"items": [{"members": [{"ref": "X1", "employee": "EMP-A"}], "booking_reference": "X2"}]}
			],
			"later": ({"booking_confirmation": "X3", "tracking_number": "X4", "keep": 1},),
			"documents": [{"url": "/private/files/x.pdf"}],
			"items": [{"documents": [{"url": "/private/files/y.pdf"}]}],
			"sheet_url": "/printview?x",
			"my_sheet_url": "/printview?y",
			"notes": None,
		}
		before = json.loads(json.dumps(given))
		self.assertEqual(
			travel._outsider_view(given),
			{
				"days": [{"items": [{"members": [{"employee": "EMP-A"}]}]}],
				"later": [{"keep": 1}],
				"documents": [],
				"items": [{"documents": []}],
				"sheet_url": None,
				"my_sheet_url": None,
				"notes": None,
			},
		)
		self.assertEqual(json.loads(json.dumps(given)), before)
