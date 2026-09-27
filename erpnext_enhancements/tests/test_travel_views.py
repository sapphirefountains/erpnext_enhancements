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
import os
import re
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
			return _dict(record) if record else None
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
				},
			),
		)
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
		SITE.deny = True
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.call("EMP-A")


class TestBootstrap(unittest.TestCase):
	def setUp(self):
		install_site()
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
		):
			self.assertRegex(source, re.compile(r"@frappe\.whitelist\(\)\ndef " + name + r"\("), name)
