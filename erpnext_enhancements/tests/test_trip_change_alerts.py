"""Bench-free tests for trip change alerts (``travel_management/change_alerts.py``).

When a Booked or In Progress trip changes, the people it affects are emailed what it was and
what it is now, with an updated calendar invite. The dispatcher (``notifications.on_trip_update``)
records each person's changes as a Pending **Trip Change Alert**, merged across the saves of
one round of edits; a scheduler job sends each once its last change is ten minutes old.

The failures guarded here all look fine on screen:

1. **The wrong people.** Only the people a change affects: a flight's travelers, a room's
   guests, a shipment's receiver, the whole crew for a stop. Never the person who made the
   change. Someone taken off the trip is told so.
2. **Money in a crew email.** The rule is "all but money": no cost, who paid, receipt, per
   diem or total reaches an alert, whatever changed. A change to money alone alerts nobody.
3. **Emails nobody asked for.** Both Travel Settings switches must be on (the new one reads 0
   after deploy, on purpose: a trip was under way in production). A trip being planned, a
   migrate, an insert: nothing.
4. **An alert that is no longer true.** One round of edits is one email with the first
   "before" and the last "after"; a change undone drops out, and an alert with nothing left
   is deleted.
5. **Sent twice, or lost.** Stamp first (Sent, commit, then send), a failure marked Failed and
   never raised, the switches and the trip's status checked again at send time.
6. **A calendar that keeps the old event.** Every event carries SEQUENCE; a booking taken off
   someone's trip is canceled under its old UID; ``build_ics`` without the new keys is
   unchanged (the CRM hand-off shares it).

unittest, not pytest, with its own CI step: this module installs its own ``frappe`` stub in
``setUpModule`` and must not share a process with another suite's.

Run: python -m unittest erpnext_enhancements.tests.test_trip_change_alerts -v
"""

import copy
import json
import os
import re
import sys
import types
import unittest
from datetime import date, datetime, timedelta

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(APP_DIR)

EMAIL_STYLE = "erpnext_enhancements.email_style"

#: Modules this suite imports afresh against its own stub, and puts back afterwards.
STUBBED = (
	"frappe",
	"frappe.utils",
	EMAIL_STYLE,
	"erpnext_enhancements.api.travel",
	"erpnext_enhancements.travel_management.ics",
	"erpnext_enhancements.travel_management.notifications",
	"erpnext_enhancements.travel_management.change_alerts",
)
_saved_modules = {}

frappe = None
change_alerts = None
notifications = None
ics = None

#: What the stubs saw.
SITE = types.SimpleNamespace()

#: Amounts that must never reach an alert. Distinct, so a leak is findable.
SENTINELS = ("211.11", "322.22", "433.33", "544.44", "655.55", "766.66", "877.77", "988.88")

#: Keys that are money, which no stored change may name.
MONEY_KEYS = {
	"cost",
	"estimated_cost",
	"paid_by",
	"paid_by_traveler",
	"billable",
	"attachment",
	"receipt",
	"per_diem_amount",
	"amount",
	"expense_claim",
	"total_actual_cost",
	"total_estimated_cost",
}

T0 = datetime(2026, 9, 27, 12, 0, 0)


def _reset_site():
	SITE.settings = {"notifications_enabled": 1, "change_alerts_enabled": 1}
	SITE.trips = {}
	SITE.employees = {
		"EMP-A": {"employee_name": "Ann", "user_id": "ann@example.com", "prefered_email": "ann@example.com"},
		"EMP-B": {"employee_name": "Bo", "user_id": "bo@example.com", "prefered_email": "bo@example.com"},
		"EMP-C": {"employee_name": "Cy", "user_id": "cy@example.com", "company_email": "cy@example.com"},
	}
	SITE.users = {"ann@example.com": "EMP-A", "bo@example.com": "EMP-B", "cy@example.com": "EMP-C"}
	SITE.pois = {"POI-1": {"poi_name": "Harbor job site", "category": "Job Site"}}
	SITE.alerts = {}
	SITE.notification_logs = []
	SITE.log = []
	SITE.errors = []  # every Error Log title; never cleared within a test
	SITE.rendered = []
	SITE.now = T0
	SITE.sendmail_raises = None
	SITE.counter = 0


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
	def get_doc_before_save(self):
		return self._before


def _matches(row, filters):
	for key, want in (filters or {}).items():
		value = row.get(key)
		if isinstance(want, (list, tuple)):
			op, operand = want
			if op == "<=" and not (value is not None and value <= operand):
				return False
			if op == "in" and value not in operand:
				return False
		elif value != want:
			return False
	return True


def _install_stub():
	stub = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def flt(value, precision=None):
		try:
			result = float(value or 0)
		except (TypeError, ValueError):
			result = 0.0
		return round(result, precision) if precision is not None else result

	def getdate(value=None):
		if value is None:
			return SITE.now.date()
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

	utils.cint = cint
	utils.flt = flt
	utils.getdate = getdate
	utils.get_datetime = get_datetime
	utils.add_days = lambda value, days: getdate(value) + timedelta(days=days)
	utils.today = lambda: str(SITE.now.date())
	utils.now_datetime = lambda: SITE.now
	utils.get_system_timezone = lambda: "America/Phoenix"
	utils.get_url = lambda path="": f"https://example.com{path or ''}"
	utils.get_url_to_form = lambda doctype, name: f"https://example.com/desk/{doctype}/{name}"

	def whitelist(*args, **kwargs):
		if args and callable(args[0]) and not kwargs:
			return args[0]
		return lambda fn: fn

	class ValidationError(Exception):
		pass

	class PermissionError_(Exception):
		pass

	def throw(msg, exc=None, **kwargs):
		raise (exc or ValidationError)(msg)

	def get_value(doctype, name=None, fieldname=None, as_dict=False, **kwargs):
		if doctype == "Employee":
			if isinstance(name, dict):
				return SITE.users.get(name.get("user_id"))
			record = SITE.employees.get(name)
			if not record:
				return None
			if isinstance(fieldname, (list, tuple)):
				return _dict({f: record.get(f) for f in fieldname})
			return record.get(fieldname)
		if doctype == "Travel POI":
			record = SITE.pois.get(name)
			if not record:
				return None
			if isinstance(fieldname, (list, tuple)):
				return _dict({f: record.get(f) for f in fieldname})
			return record.get(fieldname)
		if doctype == "Trip Change Alert":
			SITE.log.append(("lock", name) if kwargs.get("for_update") else ("read", name))
			row = SITE.alerts.get(name)
			if not row:
				return None
			if isinstance(fieldname, (list, tuple)):
				picked = _dict({f: row.get(f) for f in fieldname})
				return picked if as_dict else tuple(picked.values())
			return row.get(fieldname)
		return None

	def get_values(doctype, filters=None, fieldname="name", as_dict=False, **kwargs):
		assert doctype == "Trip Change Alert", doctype
		rows = sorted(
			(row for row in SITE.alerts.values() if _matches(row, filters)), key=lambda r: r["creation"]
		)
		fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
		return [_dict({f: row.get(f) for f in fields}) for row in rows]

	def set_value(doctype, name, field, value=None, **kwargs):
		assert doctype == "Trip Change Alert", doctype
		values = field if isinstance(field, dict) else {field: value}
		SITE.alerts[name].update(values)
		SITE.log.append(("set_value", name, dict(values)))

	def delete(doctype, filters=None):
		assert doctype == "Trip Change Alert", doctype
		for name in [n for n, row in SITE.alerts.items() if _matches(row, filters)]:
			del SITE.alerts[name]
		SITE.log.append(("delete", dict(filters or {})))

	def exists(doctype, name=None, *args, **kwargs):
		if doctype == "Travel Trip":
			return name if name in SITE.trips else None
		return None

	def get_single_value(doctype, field, *args, **kwargs):
		if doctype == "Travel Settings":
			return SITE.settings.get(field)
		return None

	def get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			record = dict(args[0])

			def insert(ignore_permissions=False, **kw):
				if record["doctype"] == "Trip Change Alert":
					assert ignore_permissions, "the crew cannot create alerts; the code does"
					SITE.counter += 1
					record["name"] = f"TCA-{SITE.counter}"
					record["creation"] = SITE.counter
					record["employee_name"] = (SITE.employees.get(record["employee"]) or {}).get(
						"employee_name"
					)
					record.setdefault("sent_at", None)
					record.setdefault("error", None)
					SITE.alerts[record["name"]] = record
				elif record["doctype"] == "Notification Log":
					SITE.notification_logs.append(record)
				SITE.log.append(("insert", record["doctype"]))
				return types.SimpleNamespace(**record)

			return types.SimpleNamespace(insert=insert)
		return SITE.trips[args[1]]

	def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kwargs):
		assert doctype == "Trip Change Alert", doctype
		rows = [row for row in SITE.alerts.values() if _matches(row, filters)]
		rows.sort(key=lambda r: r["last_change_at"])
		rows = rows[:limit] if limit else rows
		return [row["name"] for row in rows] if pluck else [_dict(row) for row in rows]

	def sendmail(**kwargs):
		SITE.log.append(("sendmail", kwargs))
		if SITE.sendmail_raises:
			raise SITE.sendmail_raises

	def render_template(path, context):
		from jinja2 import Environment, FileSystemLoader

		html = (
			Environment(loader=FileSystemLoader(REPO_ROOT), autoescape=False)
			.get_template(path)
			.render(context)
		)
		SITE.rendered.append((path, html))
		return html

	stub.utils = utils
	stub._ = lambda text, *a, **k: text
	stub._dict = _dict
	stub.whitelist = whitelist
	stub.ValidationError = ValidationError
	stub.PermissionError = PermissionError_
	stub.throw = throw
	stub.session = types.SimpleNamespace(user="office@example.com")
	stub.db = types.SimpleNamespace(
		get_value=get_value,
		get_values=get_values,
		set_value=set_value,
		delete=delete,
		exists=exists,
		get_single_value=get_single_value,
		has_column=lambda *a, **k: False,
		commit=lambda: SITE.log.append(("commit",)),
		rollback=lambda: SITE.log.append(("rollback",)),
	)
	stub.get_doc = get_doc
	stub.get_all = get_all
	stub.sendmail = sendmail
	stub.enqueue = lambda method, **kwargs: SITE.log.append(("enqueue", method))
	stub.render_template = render_template

	def log_error(*args, **kwargs):
		SITE.log.append(("log_error", kwargs.get("title")))
		SITE.errors.append(kwargs.get("title"))

	stub.log_error = log_error
	stub.get_traceback = lambda: "Traceback"
	stub.flags = types.SimpleNamespace(in_migrate=False, in_install=False, in_patch=False, in_import=False)
	stub.local = types.SimpleNamespace(site="test.site")
	stub.scrub = lambda value: str(value).replace("-", "_").replace(" ", "_").lower()
	stub.parse_json = json.loads
	sys.modules["frappe"] = stub
	sys.modules["frappe.utils"] = utils
	return stub


def _fake_email_style():
	module = types.ModuleType(EMAIL_STYLE)
	module.wrap = lambda body, title=None, eyebrow=None, **kw: f"<shell title='{title}'>{body}</shell>"
	return module


def setUpModule():
	global frappe, change_alerts, notifications, ics
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)
	_reset_site()
	frappe = _install_stub()
	# The real email_style pulls in print_style and company_contact, far more frappe than this
	# stub offers. The chrome is not under test here; what reaches sendmail is.
	sys.modules[EMAIL_STYLE] = _fake_email_style()
	from erpnext_enhancements.travel_management import change_alerts as c
	from erpnext_enhancements.travel_management import ics as i
	from erpnext_enhancements.travel_management import notifications as n

	change_alerts, notifications, ics = c, n, i


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved_modules.get(name) is not None:
			sys.modules[name] = _saved_modules[name]


# --------------------------------------------------------------------------- fixture


def make_trip(name="TRIP-1", status="Booked"):
	"""Ann and Bo share a flight and a room; a whole-crew rental; Cy receives a shipment; one
	stop for everyone. Money is set everywhere the doctype has it, so an alert that copied a
	row wholesale would carry it."""
	return FakeDoc(
		doctype="Travel Trip",
		name=name,
		purpose="Fountain install",
		status=status,
		travel_type="Domestic",
		company="SF",
		start_date="2026-10-05",
		end_date="2026-10-08",
		travel_for_doctype="Project",
		travel_for_name="PRJ-1",
		modified=datetime(2026, 9, 27, 10, 0, 0),
		total_estimated_cost=988.88,
		total_actual_cost=988.88,
		travelers=[
			FakeRow(name="T1", employee="EMP-A", employee_name="Ann", per_diem_amount=877.77),
			FakeRow(name="T2", employee="EMP-B", employee_name="Bo"),
			FakeRow(name="T3", employee="EMP-C", employee_name="Cy"),
		],
		flights=[
			FakeRow(
				name=f"F{n}",
				traveler=employee,
				booking_group="g1",
				airline="Southwest",
				flight_number="WN 1422",
				departure_airport="PHX",
				arrival_airport="LAS",
				departure_time="2026-10-05 07:05:00",
				arrival_time="2026-10-05 08:20:00",
				booking_reference=ref,
				cost=211.11,
				estimated_cost=211.11,
				paid_by="Company",
				billable=1,
				attachment=f"/private/files/receipt-f{n}.pdf",
			)
			for n, employee, ref in ((1, "EMP-A", "PNR-A"), (2, "EMP-B", "PNR-B"))
		],
		accommodations=[
			FakeRow(
				name=f"R{n}",
				traveler=employee,
				booking_group="g3",
				hotel_lodging="Harborview Suites",
				address="1 Harbor Dr",
				check_in_date="2026-10-05",
				check_in_time="15:00:00",
				check_out_date="2026-10-08",
				check_out_time="11:00:00",
				booking_confirmation=ref,
				cost=322.22,
				paid_by="Employee",
				paid_by_traveler=employee,
			)
			for n, employee, ref in ((1, "EMP-A", "H-A"), (2, "EMP-B", "H-B"))
		],
		ground_transport=[
			FakeRow(
				name="G1",
				traveler=None,
				booking_group="g4",
				transport_type="Rental/Third Party",
				supplier="Enterprise",
				pickup_location="LAS",
				dropoff_location="Site",
				pickup_datetime="2026-10-05 09:00:00",
				booking_reference="RC-1",
				cost=433.33,
				paid_by="Company",
			)
		],
		freight=[
			FakeRow(
				name="S1",
				traveler="EMP-C",
				booking_group="s1",
				carrier="Old Dominion",
				tracking_number="PRO-1",
				delivery_from="2026-10-06 10:00:00",
				delivery_to="2026-10-06 14:00:00",
				deliver_to="Site",
				cost=544.44,
				paid_by="Company",
			)
		],
		other_costs=[FakeRow(name="X1", cost=655.55, paid_by="Employee", paid_by_traveler="EMP-B")],
		mileage=[FakeRow(name="M1", distance=12, amount=766.66)],
		itinerary=[
			FakeRow(
				name="A1",
				date="2026-10-06",
				time="09:30:00",
				end_time="11:00:00",
				activity_description="Walk the site",
				location="POI-1",
			)
		],
		documents=[],
	)


def rows(doc, table, name):
	return next(row for row in getattr(doc, table) if row.name == name)


def save(before, change, user="office@example.com", minutes=1):
	"""The save that follows ``before``: a copy with ``change`` applied, run through the real
	dispatcher as ``user``. The trip as saved is what the site then has."""
	after = copy.deepcopy(before)
	after._before = None
	change(after)
	after._before = before
	after.modified = (before.modified or T0) + timedelta(minutes=minutes)
	frappe.session.user = user
	notifications.on_trip_update(after)
	SITE.trips[after.name] = after
	return after


def alert(employee, status="Pending"):
	found = [row for row in SITE.alerts.values() if row["employee"] == employee and row["status"] == status]
	assert len(found) <= 1, f"{len(found)} {status} alerts for {employee}"
	return found[0] if found else None


def changes(employee):
	row = alert(employee)
	return json.loads(row["changes"]) if row else None


def fields(change):
	return {f["field"]: (f["before"], f["after"]) for f in change["fields"]}


def sent():
	return [entry[1] for entry in SITE.log if entry[0] == "sendmail"]


class Base(unittest.TestCase):
	#: Set by a test that makes something fail on purpose.
	expect_error = False

	def setUp(self):
		_reset_site()
		frappe.session.user = "office@example.com"
		frappe.flags.in_migrate = False
		self.trip = make_trip()
		SITE.trips[self.trip.name] = self.trip

	def tearDown(self):
		# Detection never raises into a save; it logs. So "no alert" would also be what a crash
		# looks like, and every test that expects nothing would pass on broken code.
		if not self.expect_error:
			self.assertEqual(SITE.errors, [])


# --------------------------------------------------------------------------- detection


def move_flight(departure, arrival=None):
	def change(doc):
		for row in doc.flights:
			row.departure_time = departure
			if arrival:
				row.arrival_time = arrival

	return change


class TestWhoIsTold(Base):
	def test_a_flight_time_change_tells_the_people_on_it(self):
		save(self.trip, move_flight("2026-10-05 09:40:00", "2026-10-05 10:55:00"))
		for employee in ("EMP-A", "EMP-B"):
			(change,) = changes(employee)
			self.assertEqual(change["key"], "flight:g1")
			self.assertEqual(change["kind"], "flight")
			self.assertEqual(change["change"], "changed")
			self.assertEqual(fields(change)["departure_time"], ("7:05 AM", "9:40 AM"))
			self.assertEqual(fields(change)["arrival_time"], ("8:20 AM", "10:55 AM"))
			# The date did not move, so it is not on the alert.
			self.assertNotIn("departure_date", fields(change))
		self.assertIsNone(alert("EMP-C"), "Cy is not on that flight")

	def test_the_person_who_made_the_change_is_not_told(self):
		save(self.trip, move_flight("2026-10-05 09:40:00"), user="bo@example.com")
		self.assertIsNotNone(alert("EMP-A"))
		self.assertIsNone(alert("EMP-B"))

	def test_a_confirmation_number_goes_only_to_its_owner(self):
		save(self.trip, lambda doc: setattr(rows(doc, "flights", "F1"), "booking_reference", "PNR-A2"))
		(change,) = changes("EMP-A")
		self.assertEqual(fields(change), {"booking_reference": ("PNR-A", "PNR-A2")})
		self.assertEqual(change["fields"][0]["label"], "Confirmation (PNR)")
		self.assertIsNone(alert("EMP-B"), "Bo's own PNR did not change")
		self.assertIsNone(alert("EMP-C"))

	def test_an_added_booking_tells_its_traveler(self):
		def add(doc):
			doc.flights.append(
				FakeRow(
					name="F3",
					traveler="EMP-C",
					booking_group="g5",
					airline="Delta",
					flight_number="DL 9",
					departure_airport="LAS",
					arrival_airport="PHX",
					departure_time="2026-10-08 17:00:00",
					booking_reference="PNR-C",
					cost=877.77,
				)
			)

		save(self.trip, add)
		(change,) = changes("EMP-C")
		self.assertEqual((change["key"], change["change"]), ("flight:g5", "added"))
		self.assertEqual(change["label"], "Flight Delta DL 9, LAS → PHX")
		self.assertEqual(fields(change)["departure_time"], ("", "5:00 PM"))
		self.assertEqual(fields(change)["booking_reference"], ("", "PNR-C"))
		self.assertIsNone(alert("EMP-A"))
		self.assertIsNone(alert("EMP-B"))

	def test_a_removed_booking_is_canceled_on_the_calendar(self):
		save(self.trip, lambda doc: doc.accommodations.remove(rows(doc, "accommodations", "R2")))
		(change,) = changes("EMP-B")
		self.assertEqual((change["key"], change["change"]), ("hotel:g3", "removed"))
		self.assertEqual(change["label"], "Room at Harborview Suites")
		self.assertEqual([c["uid"] for c in change["cancel"]], ["TRIP-1-R2@test.site"])
		self.assertIsNone(alert("EMP-A"), "Ann's own room is unchanged")

	def test_someone_taken_off_the_trip_is_told_so(self):
		def drop_cy(doc):
			doc.travelers = [t for t in doc.travelers if t.employee != "EMP-C"]
			# What Plan a Trip does: the shipment Cy was to receive goes to the whole crew.
			rows(doc, "freight", "S1").traveler = None

		save(self.trip, drop_cy)
		cy = {c["key"]: c for c in changes("EMP-C")}
		self.assertEqual(cy["trip:TRIP-1"]["change"], "removed")
		self.assertEqual(cy["trip:TRIP-1"]["kind"], "trip")
		canceled = {entry["uid"] for c in cy.values() for entry in c.get("cancel") or []}
		# Every calendar entry Cy had: the trip itself, the whole crew's rental, the shipment.
		self.assertEqual(canceled, {"TRIP-1-T3-span@test.site", "TRIP-1-G1@test.site", "TRIP-1-S1@test.site"})
		# Ann and Bo now receive the shipment.
		for employee in ("EMP-A", "EMP-B"):
			(change,) = changes(employee)
			self.assertEqual((change["key"], change["change"]), ("freight:s1", "added"))

	def test_a_stop_change_tells_the_whole_crew(self):
		save(self.trip, lambda doc: setattr(rows(doc, "itinerary", "A1"), "time", "10:15:00"))
		for employee in ("EMP-A", "EMP-B", "EMP-C"):
			(change,) = changes(employee)
			self.assertEqual((change["key"], change["kind"]), ("stop:A1", "stop"))
			self.assertEqual(fields(change), {"time": ("9:30 AM", "10:15 AM")})
			self.assertEqual(change["label"], "Walk the site (stop)")

	def test_a_stop_change_skips_the_crew_member_who_made_it(self):
		save(
			self.trip,
			lambda doc: setattr(rows(doc, "itinerary", "A1"), "time", "10:15:00"),
			user="cy@example.com",
		)
		self.assertIsNotNone(alert("EMP-A"))
		self.assertIsNotNone(alert("EMP-B"))
		self.assertIsNone(alert("EMP-C"))

	def test_a_rooms_check_in_and_check_out_are_one_booking(self):
		def later(doc):
			for row in doc.accommodations:
				row.check_in_time = "16:00:00"
				row.check_out_date = "2026-10-09"

		save(self.trip, later)
		for employee in ("EMP-A", "EMP-B"):
			(change,) = changes(employee)
			self.assertEqual(change["key"], "hotel:g3")
			self.assertEqual(
				fields(change),
				{"check_in_time": ("3:00 PM", "4:00 PM"), "check_out_date": ("Thu Oct 8", "Fri Oct 9")},
			)

	def test_a_shipment_window_goes_to_its_receiver(self):
		save(self.trip, lambda doc: setattr(rows(doc, "freight", "S1"), "delivery_to", "2026-10-06 16:00:00"))
		(change,) = changes("EMP-C")
		self.assertEqual(
			fields(change),
			{"delivery_window": ("Tue Oct 6, 10:00 AM – 2:00 PM", "Tue Oct 6, 10:00 AM – 4:00 PM")},
		)
		self.assertIsNone(alert("EMP-A"))

	def test_a_whole_crew_ride_tells_everyone(self):
		save(
			self.trip,
			lambda doc: setattr(
				rows(doc, "ground_transport", "G1"), "pickup_datetime", "2026-10-05 10:30:00"
			),
		)
		for employee in ("EMP-A", "EMP-B", "EMP-C"):
			(change,) = changes(employee)
			self.assertEqual(change["key"], "ground:g4")
			self.assertEqual(fields(change), {"pickup_time": ("9:00 AM", "10:30 AM")})

	def test_a_persons_own_dates(self):
		save(self.trip, lambda doc: setattr(doc.travelers[1], "from_date", "2026-10-06"))
		(change,) = changes("EMP-B")
		self.assertEqual((change["key"], change["kind"]), ("trip:TRIP-1", "trip"))
		self.assertEqual(fields(change), {"first_day": ("Mon Oct 5", "Tue Oct 6")})

	def test_someone_just_added_gets_the_added_email_not_an_alert(self):
		def add_dee(doc):
			doc.travelers.append(FakeRow(name="T4", employee="EMP-D", employee_name="Dee"))

		SITE.employees["EMP-D"] = {"employee_name": "Dee", "prefered_email": "dee@example.com"}
		save(self.trip, add_dee)
		self.assertIsNone(alert("EMP-D"))
		self.assertIn(
			("enqueue", "erpnext_enhancements.travel_management.notifications.deliver_traveler_added"),
			SITE.log,
		)


def as_the_database_returns_it(doc):
	"""``doc`` with every date, datetime and time as the database hands them back (``date``,
	``datetime``, ``timedelta``), where the saved document holds what the page sent (strings)."""

	def to_time(text):
		hours, minutes, seconds = (int(part) for part in text.split(":"))
		return timedelta(hours=hours, minutes=minutes, seconds=seconds)

	for row in doc.flights:
		row.departure_time = datetime.fromisoformat(row.departure_time)
		row.arrival_time = datetime.fromisoformat(row.arrival_time)
	for row in doc.accommodations:
		row.check_in_date = date.fromisoformat(row.check_in_date)
		row.check_out_date = date.fromisoformat(row.check_out_date)
		row.check_in_time = to_time(row.check_in_time)
		row.check_out_time = to_time(row.check_out_time)
	for row in doc.ground_transport:
		row.pickup_datetime = datetime.fromisoformat(row.pickup_datetime)
	for row in doc.freight:
		row.delivery_from = datetime.fromisoformat(row.delivery_from)
		row.delivery_to = datetime.fromisoformat(row.delivery_to)
	for row in doc.itinerary:
		row.date = date.fromisoformat(row.date)
		row.time = to_time(row.time)
		row.end_time = to_time(row.end_time)
	doc.start_date = date.fromisoformat(doc.start_date)
	doc.end_date = date.fromisoformat(doc.end_date)
	return doc


class TestNoFalseAlarms(Base):
	def test_the_same_values_in_database_types_are_not_a_change(self):
		strings = make_trip()
		# A stop at midnight: timedelta(0) is falsy, "00:00:00" is not.
		strings.itinerary[0].time = "00:00:00"
		before = as_the_database_returns_it(copy.deepcopy(strings))
		save(before, lambda doc: doc.__dict__.update(copy.deepcopy(strings).__dict__))
		self.assertEqual(SITE.alerts, {})

	def test_a_save_that_changes_nothing_alerts_nobody(self):
		save(self.trip, lambda doc: None)
		self.assertEqual(SITE.alerts, {})
		self.assertFalse([e for e in SITE.log if e[0] in ("insert", "set_value", "delete")])


class TestMoney(Base):
	def test_a_change_to_money_alone_alerts_nobody(self):
		def reprice(doc):
			for table in ("flights", "accommodations", "ground_transport", "freight", "other_costs"):
				for row in getattr(doc, table):
					row.cost = 999.99
					row.estimated_cost = 999.99
					row.paid_by = "Employee"
					row.paid_by_traveler = "EMP-A"
					row.billable = 0
					row.attachment = "/private/files/receipt-new.pdf"
			doc.travelers[0].per_diem_amount = 1.5
			doc.total_actual_cost = 12345

		save(self.trip, reprice)
		self.assertEqual(SITE.alerts, {})

	def test_no_money_in_any_alert(self):
		def everything(doc):
			move_flight("2026-10-05 09:40:00")(doc)
			for row in doc.accommodations:
				row.check_in_time = "16:00:00"
			rows(doc, "freight", "S1").tracking_number = "PRO-2"
			rows(doc, "itinerary", "A1").time = "10:15:00"
			doc.flights.append(
				FakeRow(
					name="F3",
					traveler="EMP-C",
					booking_group="g5",
					airline="Delta",
					flight_number="DL 9",
					cost=877.77,
				)
			)
			doc.accommodations.remove(rows(doc, "accommodations", "R2"))

		save(self.trip, everything)
		self.assertEqual(len(SITE.alerts), 3)
		allowed = {name for kind in change_alerts.FIELD_LABELS.values() for name, _label in kind}
		for row in SITE.alerts.values():
			text = row["changes"]
			for amount in (*SENTINELS, "877.77", "999.99"):
				self.assertNotIn(amount, text)
			self.assertNotIn("receipt", text)
			for change in json.loads(text):
				self.assertFalse(MONEY_KEYS & set(change), change)
				for field in change["fields"]:
					self.assertIn(field["field"], allowed)
					self.assertNotIn(field["field"], MONEY_KEYS)

	def test_the_field_list_has_no_money(self):
		names = {name for kind in change_alerts.FIELD_LABELS.values() for name, _label in kind}
		self.assertFalse(names & MONEY_KEYS)


class TestGates(Base):
	def test_nothing_while_travel_notifications_are_off(self):
		SITE.settings["notifications_enabled"] = 0
		save(self.trip, move_flight("2026-10-05 09:40:00"))
		self.assertEqual(SITE.alerts, {})

	def test_nothing_while_change_alerts_are_off(self):
		SITE.settings["change_alerts_enabled"] = 0
		save(self.trip, move_flight("2026-10-05 09:40:00"))
		self.assertEqual(SITE.alerts, {})

	def test_nothing_before_anyone_ticks_the_new_switch(self):
		# The field has no default: on every existing site it reads None until ticked.
		del SITE.settings["change_alerts_enabled"]
		save(self.trip, move_flight("2026-10-05 09:40:00"))
		self.assertEqual(SITE.alerts, {})

	def test_nothing_on_a_trip_being_planned(self):
		trip = make_trip(status="Planning")
		save(trip, move_flight("2026-10-05 09:40:00"))
		self.assertEqual(SITE.alerts, {})

	def test_nothing_when_a_trip_is_booked(self):
		trip = make_trip(status="Planning")

		def book(doc):
			doc.status = "Booked"
			move_flight("2026-10-05 09:40:00")(doc)

		save(trip, book)
		self.assertEqual(SITE.alerts, {}, "'Trip booked' tells them; an alert would say it twice")

	def test_nothing_when_a_trip_goes_back_to_planning(self):
		def unbook(doc):
			doc.status = "Planning"
			move_flight("2026-10-05 09:40:00")(doc)

		save(self.trip, unbook)
		self.assertEqual(SITE.alerts, {})

	def test_a_trip_under_way_still_alerts(self):
		trip = make_trip(status="In Progress")
		save(trip, move_flight("2026-10-05 09:40:00"))
		self.assertIsNotNone(alert("EMP-A"))

	def test_booked_to_in_progress_with_nothing_else_is_quiet(self):
		save(self.trip, lambda doc: setattr(doc, "status", "In Progress"))
		self.assertEqual(SITE.alerts, {})

	def test_nothing_during_a_migrate(self):
		frappe.flags.in_migrate = True
		try:
			save(self.trip, move_flight("2026-10-05 09:40:00"))
		finally:
			frappe.flags.in_migrate = False
		self.assertEqual(SITE.alerts, {})

	def test_a_failure_never_stops_the_save(self):
		self.expect_error = True
		original = frappe.db.get_values
		frappe.db.get_values = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
		try:
			save(self.trip, move_flight("2026-10-05 09:40:00"))
		finally:
			frappe.db.get_values = original
		self.assertIn(("log_error", "Trip change alert failed"), SITE.log)


# --------------------------------------------------------------------------- merging


class TestMerge(Base):
	def test_a_round_of_edits_is_one_alert_first_before_last_after(self):
		first = save(self.trip, move_flight("2026-10-05 09:40:00"))
		SITE.now = T0 + timedelta(minutes=3)
		save(first, move_flight("2026-10-05 10:15:00"))
		row = alert("EMP-A")
		(change,) = json.loads(row["changes"])
		self.assertEqual(fields(change)["departure_time"], ("7:05 AM", "10:15 AM"))
		self.assertEqual(row["first_change_at"], T0)
		self.assertEqual(row["last_change_at"], T0 + timedelta(minutes=3))
		self.assertEqual(len([r for r in SITE.alerts.values() if r["employee"] == "EMP-A"]), 1)

	def test_a_change_undone_is_dropped_and_so_is_the_alert(self):
		first = save(self.trip, move_flight("2026-10-05 09:40:00"))
		save(first, move_flight("2026-10-05 07:05:00"))
		self.assertEqual(SITE.alerts, {})

	def test_undoing_one_field_keeps_the_others(self):
		def two(doc):
			move_flight("2026-10-05 09:40:00")(doc)
			rows(doc, "flights", "F1").booking_reference = "PNR-A2"

		first = save(self.trip, two)
		save(first, move_flight("2026-10-05 07:05:00"))
		(change,) = changes("EMP-A")
		self.assertEqual(fields(change), {"booking_reference": ("PNR-A", "PNR-A2")})
		self.assertIsNone(alert("EMP-B"), "Bo's only change was undone")

	def test_added_then_removed_is_nothing(self):
		def add(doc):
			doc.flights.append(FakeRow(name="F3", traveler="EMP-C", booking_group="g5", airline="Delta"))

		first = save(self.trip, add)
		self.assertIsNotNone(alert("EMP-C"))
		save(first, lambda doc: doc.flights.remove(rows(doc, "flights", "F3")))
		self.assertIsNone(alert("EMP-C"))

	def test_removed_then_added_back_is_a_change(self):
		first = save(self.trip, lambda doc: doc.accommodations.remove(rows(doc, "accommodations", "R2")))

		def back(doc):
			room = copy.deepcopy(rows(doc, "accommodations", "R1"))
			room.name, room.traveler, room.booking_confirmation = "R9", "EMP-B", "H-B2"
			doc.accommodations.append(room)

		save(first, back)
		(change,) = changes("EMP-B")
		self.assertEqual(change["change"], "changed")
		self.assertEqual(fields(change), {"booking_confirmation": ("H-B", "H-B2")})
		# The old row's calendar entry is still canceled: the room is a new row, a new UID.
		self.assertEqual([c["uid"] for c in change["cancel"]], ["TRIP-1-R2@test.site"])

	def test_the_actors_waiting_alert_stays_true(self):
		first = save(self.trip, move_flight("2026-10-05 09:40:00"))
		save(first, move_flight("2026-10-05 10:15:00"), user="ann@example.com")
		(change,) = changes("EMP-A")
		self.assertEqual(fields(change)["departure_time"], ("7:05 AM", "10:15 AM"))

	def test_merge_rules_on_their_own(self):
		def field(before, after):
			return {"field": "departure_time", "label": "Departs", "before": before, "after": after}

		def change(kind, status, *fs, key="flight:g1", label="Flight X", label_before=None, cancel=None):
			out = {
				"key": key,
				"kind": kind,
				"label": label,
				"label_before": label_before,
				"change": status,
				"fields": list(fs),
			}
			if cancel:
				out["cancel"] = cancel
			return out

		merge = change_alerts.merge_changes
		one = change("flight", "changed", field("7:05 AM", "9:40 AM"), label_before="Flight X")
		two = change("flight", "changed", field("9:40 AM", "10:15 AM"), label_before="Flight X")
		self.assertEqual(merge([one], [two])[0]["fields"], [field("7:05 AM", "10:15 AM")])
		self.assertEqual(merge([one], [change("flight", "changed", field("9:40 AM", "7:05 AM"))]), [])
		added = change("flight", "added", field("", "9:40 AM"))
		self.assertEqual(merge([added], [change("flight", "removed", field("9:40 AM", ""))]), [])
		removed = change(
			"flight",
			"removed",
			field("7:05 AM", ""),
			label="Flight X",
			label_before="Flight X",
			cancel=[{"uid": "u1", "summary": "s", "start": "2026-10-05", "all_day": True}],
		)
		back = merge([removed], [change("flight", "added", field("", "9:40 AM"), label="Flight Y")])
		self.assertEqual(back[0]["change"], "changed")
		self.assertEqual(back[0]["label"], "Flight Y")
		self.assertEqual(back[0]["fields"], [field("7:05 AM", "9:40 AM")])
		self.assertEqual([c["uid"] for c in back[0]["cancel"]], ["u1"])
		# Changed, then removed: the email names the booking as the person knew it.
		gone = merge(
			[
				change(
					"flight",
					"changed",
					field("7:05 AM", "9:40 AM"),
					label="Flight Y",
					label_before="Flight X",
				)
			],
			[change("flight", "removed", field("9:40 AM", ""), label="Flight Y", label_before="Flight Y")],
		)
		self.assertEqual((gone[0]["change"], gone[0]["label"]), ("removed", "Flight X"))
		# Nothing is changed in place.
		self.assertEqual(one["fields"], [field("7:05 AM", "9:40 AM")])


# --------------------------------------------------------------------------- the sender


def pending_alert(employee="EMP-A", minutes_ago=11):
	"""A Pending alert for Ann's flight, as detection writes it."""
	SITE.now = T0
	save(make_trip(), move_flight("2026-10-05 09:40:00"))
	row = alert(employee)
	row["last_change_at"] = T0 - timedelta(minutes=minutes_ago)
	row["first_change_at"] = row["last_change_at"]
	SITE.log.clear()
	return row


class TestSender(Base):
	def test_nothing_is_sent_inside_the_quiet_period(self):
		row = pending_alert(minutes_ago=5)
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Pending")
		self.assertEqual(sent(), [])

	def test_a_due_alert_is_sent_once(self):
		row = pending_alert()
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Sent")
		self.assertEqual(row["sent_at"], T0)
		mails = [m for m in sent() if m["recipients"] == ["ann@example.com"]]
		self.assertEqual(len(mails), 1)
		self.assertEqual(mails[0]["subject"], "Trip update: Fountain install (2026-10-05 – 2026-10-08)")
		self.assertEqual(
			(mails[0]["reference_doctype"], mails[0]["reference_name"]), ("Travel Trip", "TRIP-1")
		)
		self.assertIn("ann@example.com", [log["for_user"] for log in SITE.notification_logs])
		SITE.log.clear()
		SITE.now = T0 + timedelta(minutes=5)
		change_alerts.send_due_change_alerts()
		self.assertEqual([m for m in sent() if m["recipients"] == ["ann@example.com"]], [])

	def test_stamp_first_then_commit_then_send(self):
		row = pending_alert()
		change_alerts.send_due_change_alerts()
		name = row["name"]
		stamp = next(
			i
			for i, e in enumerate(SITE.log)
			if e[0] == "set_value" and e[1] == name and e[2].get("status") == "Sent"
		)
		commit = next(i for i, e in enumerate(SITE.log) if i > stamp and e[0] == "commit")
		mail = next(
			i
			for i, e in enumerate(SITE.log)
			if e[0] == "sendmail" and e[1]["recipients"] == ["ann@example.com"]
		)
		self.assertLess(stamp, commit)
		self.assertLess(commit, mail)
		# The row was locked before it was claimed.
		self.assertLess(SITE.log.index(("lock", name)), stamp)

	def test_a_failed_send_is_marked_failed_and_never_raised(self):
		self.expect_error = True
		row = pending_alert()
		SITE.sendmail_raises = RuntimeError("SMTP down")
		change_alerts.send_due_change_alerts()  # must not raise
		self.assertEqual(row["status"], "Failed")
		self.assertIn("RuntimeError: SMTP down", row["error"])
		self.assertIn(("log_error", "Trip change alert failed"), SITE.log)
		# Stamped first: a failed alert is not sent again by the next run.
		SITE.sendmail_raises = None
		SITE.log.clear()
		change_alerts.send_due_change_alerts()
		self.assertEqual(sent(), [])

	def test_turning_change_alerts_off_skips_what_is_waiting(self):
		row = pending_alert()
		SITE.settings["change_alerts_enabled"] = 0
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Skipped")
		self.assertEqual(sent(), [])

	def test_turning_travel_notifications_off_skips_what_is_waiting(self):
		row = pending_alert()
		SITE.settings["notifications_enabled"] = 0
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Skipped")
		self.assertEqual(sent(), [])

	def test_a_trip_no_longer_booked_is_skipped(self):
		row = pending_alert()
		SITE.trips["TRIP-1"].status = "Completed"
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Skipped")
		self.assertEqual(sent(), [])

	def test_an_alert_for_a_deleted_trip_is_skipped(self):
		row = pending_alert()
		del SITE.trips["TRIP-1"]
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Skipped")
		self.assertEqual(sent(), [])

	def test_a_row_changed_again_since_the_list_was_read_waits(self):
		row = pending_alert()
		original = frappe.db.get_value

		def later(doctype, name=None, *args, **kwargs):
			if doctype == "Trip Change Alert" and kwargs.get("for_update"):
				SITE.alerts[name]["last_change_at"] = T0  # a save landed in between
			return original(doctype, name, *args, **kwargs)

		frappe.db.get_value = later
		try:
			change_alerts.send_due_change_alerts()
		finally:
			frappe.db.get_value = original
		self.assertEqual(row["status"], "Pending")
		self.assertEqual(sent(), [])

	def test_nobody_with_no_email_is_skipped(self):
		SITE.employees["EMP-A"] = {"employee_name": "Ann"}
		row = pending_alert()
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Skipped")
		self.assertEqual([m for m in sent() if m["recipients"] == [None]], [])

	def test_a_waiting_alert_survives_a_lost_job_queue(self):
		# The rows are the timer: nothing is enqueued, so a deploy's FLUSHDB loses nothing.
		row = pending_alert()
		self.assertFalse([e for e in SITE.log if e[0] == "enqueue"])
		change_alerts.send_due_change_alerts()
		self.assertEqual(row["status"], "Sent")


# --------------------------------------------------------------------------- the email


def send_for(employee):
	for row in SITE.alerts.values():
		row["last_change_at"] = T0 - timedelta(minutes=11)
	SITE.log.clear()
	SITE.rendered.clear()
	change_alerts.send_due_change_alerts()
	mails = [
		m
		for m in sent()
		if m["recipients"]
		== [SITE.employees[employee].get("prefered_email") or SITE.employees[employee].get("company_email")]
	]
	assert len(mails) == 1, mails
	return mails[0]


class TestEmail(Base):
	def test_what_changed_before_and_after(self):
		save(self.trip, move_flight("2026-10-05 09:40:00"))
		mail = send_for("EMP-A")
		html = mail["message"]
		self.assertIn("What changed", html)
		self.assertIn("Flight Southwest WN 1422, PHX → LAS", html)
		self.assertIn("Departs: 7:05 AM → 9:40 AM", html)
		self.assertIn('href="https://example.com/itinerary?trip=TRIP-1"', html)
		self.assertIn("Open my itinerary", html)
		for amount in SENTINELS:
			self.assertNotIn(amount, html)

	def test_added_and_removed_lines(self):
		def both(doc):
			doc.accommodations.remove(rows(doc, "accommodations", "R2"))
			doc.flights.append(
				FakeRow(
					name="F3",
					traveler="EMP-B",
					booking_group="g5",
					airline="Delta",
					flight_number="DL 9",
					departure_airport="LAS",
					arrival_airport="PHX",
					departure_time="2026-10-08 17:00:00",
				)
			)

		save(self.trip, both)
		html = send_for("EMP-B")["message"]
		self.assertIn("Added: Flight Delta DL 9, LAS → PHX", html)
		self.assertIn("Departs: 5:00 PM", html)
		self.assertIn("Removed: Room at Harborview Suites", html)
		# Flights are listed before rooms.
		self.assertLess(html.index("Added: Flight"), html.index("Removed: Room"))

	def test_user_typed_text_is_escaped(self):
		save(
			self.trip,
			lambda doc: [setattr(r, "hotel_lodging", "<b>Evil</b> Inn") for r in doc.accommodations],
		)
		html = send_for("EMP-A")["message"]
		self.assertNotIn("<b>Evil</b>", html)
		self.assertIn("&lt;b&gt;Evil&lt;/b&gt; Inn", html)

	def test_someone_off_the_trip_is_told_so_without_an_itinerary_link(self):
		save(
			self.trip,
			lambda doc: setattr(doc, "travelers", [t for t in doc.travelers if t.employee != "EMP-C"]),
		)
		html = send_for("EMP-C")["message"]
		self.assertIn("You are no longer on this trip.", html)
		self.assertNotIn("/itinerary?trip=", html)
		self.assertNotIn("What changed", html)

	def test_the_notification_log_keeps_the_unwrapped_fragment(self):
		save(self.trip, move_flight("2026-10-05 09:40:00"))
		send_for("EMP-A")
		(log,) = [log for log in SITE.notification_logs if log["for_user"] == "ann@example.com"]
		self.assertNotIn("<shell", log["email_content"])
		self.assertIn("Departs: 7:05 AM → 9:40 AM", log["email_content"])


# --------------------------------------------------------------------------- the calendar


def vevents(ics_text):
	unfolded = ics_text.replace("\r\n ", "")
	return [block for block in unfolded.split("BEGIN:VEVENT")[1:]]


class TestCalendar(Base):
	def test_every_event_carries_the_trips_sequence(self):
		after = save(self.trip, move_flight("2026-10-05 09:40:00"))
		(attachment,) = send_for("EMP-A")["attachments"]
		self.assertEqual(attachment["fname"], "trip_1.ics")
		events = vevents(attachment["fcontent"])
		self.assertTrue(events)
		sequence = int(after.modified.timestamp())
		for event in events:
			self.assertIn(f"SEQUENCE:{sequence}\r\n", event)
			self.assertNotIn("STATUS:", event)
		self.assertIn("UID:TRIP-1-F1@test.site", attachment["fcontent"])
		self.assertIn("METHOD:PUBLISH", attachment["fcontent"])

	def test_a_removed_booking_is_canceled_under_its_old_uid(self):
		save(self.trip, lambda doc: doc.accommodations.remove(rows(doc, "accommodations", "R2")))
		(attachment,) = send_for("EMP-B")["attachments"]
		(canceled,) = [e for e in vevents(attachment["fcontent"]) if "UID:TRIP-1-R2@test.site" in e]
		self.assertIn("STATUS:CANCELLED", canceled)
		self.assertRegex(canceled, r"SEQUENCE:\d+")
		self.assertIn("SUMMARY:Canceled: ", canceled)
		# The rest of Bo's calendar is still on it.
		self.assertIn("UID:TRIP-1-F2@test.site", attachment["fcontent"])

	def test_someone_off_the_trip_has_everything_canceled(self):
		save(
			self.trip,
			lambda doc: setattr(doc, "travelers", [t for t in doc.travelers if t.employee != "EMP-C"]),
		)
		(attachment,) = send_for("EMP-C")["attachments"]
		events = vevents(attachment["fcontent"])
		uids = {re.search(r"UID:(\S+)", e).group(1) for e in events}
		self.assertEqual(uids, {"TRIP-1-T3-span@test.site", "TRIP-1-G1@test.site", "TRIP-1-S1@test.site"})
		for event in events:
			self.assertIn("STATUS:CANCELLED", event)

	def test_build_ics_without_the_new_keys_is_unchanged(self):
		out = ics.build_ics([{"uid": "u1@test", "summary": "Hello", "start": "2026-07-01", "all_day": True}])
		self.assertNotIn("SEQUENCE", out)
		self.assertNotIn("STATUS", out)
		self.assertEqual(
			out,
			"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Sapphire Fountains//erpnext_enhancements travel//EN\r\n"
			"CALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\nUID:u1@test\r\n"
			"DTSTAMP:20260927T190000Z\r\nDTSTART;VALUE=DATE:20260701\r\nDTEND;VALUE=DATE:20260702\r\n"
			"SUMMARY:Hello\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
		)


# --------------------------------------------------------------------------- wiring


def _read(*parts):
	with open(os.path.join(*parts), encoding="utf-8") as handle:
		return handle.read()


class TestWiring(unittest.TestCase):
	DOCTYPE_DIR = os.path.join(APP_DIR, "travel_management", "doctype", "trip_change_alert")

	def test_the_sender_runs_every_five_minutes(self):
		hooks = _read(APP_DIR, "hooks.py")
		self.assertIn(
			'"*/5 * * * *": ["erpnext_enhancements.travel_management.change_alerts.send_due_change_alerts"]',
			hooks,
		)
		self.assertEqual(hooks.count('"*/5 * * * *"'), 1, "a repeated cron key replaces the first silently")

	def test_an_alert_never_stops_a_trip_being_deleted(self):
		# frappe v16's delete_doc refuses to delete a document any non-cancelled row links to.
		hooks = _read(APP_DIR, "hooks.py")
		match = re.search(r"^ignore_links_on_delete = \[(.*?)\]", hooks, flags=re.MULTILINE | re.DOTALL)
		self.assertIsNotNone(match)
		self.assertIn('"Trip Change Alert"', match.group(1))

	def test_the_new_switch_reads_off_until_it_is_ticked(self):
		settings = json.loads(
			_read(APP_DIR, "travel_management", "doctype", "travel_settings", "travel_settings.json")
		)
		(field,) = [f for f in settings["fields"] if f["fieldname"] == "change_alerts_enabled"]
		self.assertEqual((field["fieldtype"], field["label"]), ("Check", "Send Change Alerts"))
		# No default: off is the wanted state on every existing site (a trip was under way in
		# production with travel notifications on), and a Single's default never reaches the
		# stored row anyway.
		self.assertNotIn("default", field)
		self.assertIn("Send Travel Notifications", field["description"])
		order = settings["field_order"]
		self.assertEqual(order.index("change_alerts_enabled"), order.index("notifications_enabled") + 1)

	def test_the_doctype(self):
		meta = json.loads(_read(self.DOCTYPE_DIR, "trip_change_alert.json"))
		self.assertEqual((meta["name"], meta["module"]), ("Trip Change Alert", "Travel Management"))
		self.assertFalse(meta.get("istable"))
		self.assertEqual(meta.get("track_changes"), 0)
		by_name = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual((by_name["trip"]["fieldtype"], by_name["trip"]["options"]), ("Link", "Travel Trip"))
		self.assertTrue(by_name["trip"]["reqd"] and by_name["trip"]["in_list_view"])
		self.assertEqual(
			(by_name["employee"]["fieldtype"], by_name["employee"]["options"]), ("Link", "Employee")
		)
		self.assertEqual(by_name["employee"]["ignore_user_permissions"], 1)
		self.assertTrue(by_name["employee"]["reqd"])
		self.assertEqual(by_name["employee_name"]["fetch_from"], "employee.employee_name")
		self.assertEqual(by_name["status"]["options"].split("\n"), ["Pending", "Sent", "Skipped", "Failed"])
		self.assertEqual(by_name["status"]["default"], "Pending")
		self.assertTrue(by_name["status"]["in_list_view"])
		self.assertEqual(by_name["changes"]["fieldtype"], "JSON")
		for name in ("first_change_at", "last_change_at", "sent_at"):
			self.assertEqual(by_name[name]["fieldtype"], "Datetime")
		self.assertEqual(by_name["error"]["fieldtype"], "Small Text")
		perms = {p["role"]: p for p in meta["permissions"]}
		self.assertEqual(set(perms), {"System Manager", "Travel Coordinator", "HR Manager"})
		self.assertNotIn("Employee", perms, "the crew must not read each other's alerts")
		for role in ("System Manager", "Travel Coordinator"):
			self.assertTrue(all(perms[role].get(k) for k in ("read", "write", "create", "delete")))
		self.assertTrue(perms["HR Manager"].get("read"))
		self.assertFalse(perms["HR Manager"].get("write") or perms["HR Manager"].get("create"))

	def test_the_controller_class_is_the_one_frappe_derives(self):
		self.assertIn("class TripChangeAlert(Document):", _read(self.DOCTYPE_DIR, "trip_change_alert.py"))
		self.assertTrue(os.path.isfile(os.path.join(self.DOCTYPE_DIR, "__init__.py")))


if __name__ == "__main__":
	unittest.main()
