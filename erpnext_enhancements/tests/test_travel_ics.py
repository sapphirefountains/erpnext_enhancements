"""Pure-Python (no Frappe site) unit tests for the travel ICS builder
(``travel_management/ics.py``).

Same pattern as ``test_quickbooks_online.py``: a minimal fake ``frappe`` /
``frappe.utils`` is installed into ``sys.modules`` before importing the module
under test, so RFC 5545 details (CRLF, 75-octet folding, escaping, site-tz →
UTC conversion, all-day DTEND exclusivity, stable UIDs) can be asserted
deterministically without a bench.

Run: ``python -m pytest erpnext_enhancements/tests/test_travel_ics.py``
"""

import sys
import types
from datetime import date, datetime


def install_frappe_stub():
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = types.ModuleType("frappe.utils")

	def get_datetime(value):
		if isinstance(value, datetime):
			return value
		if isinstance(value, date):
			return datetime(value.year, value.month, value.day)
		return datetime.fromisoformat(str(value))

	def getdate(value):
		if isinstance(value, datetime):
			return value.date()
		if isinstance(value, date):
			return value
		return datetime.fromisoformat(str(value)).date()

	frappe_utils.get_datetime = get_datetime
	frappe_utils.getdate = getdate
	frappe_utils.get_system_timezone = lambda: "America/New_York"
	frappe_utils.now_datetime = lambda: datetime(2026, 6, 11, 9, 0, 0)
	frappe_utils.get_url = lambda path="": f"https://example.com{path}"

	frappe.utils = frappe_utils
	frappe.scrub = lambda value: str(value).replace("-", "_").replace(" ", "_").lower()
	frappe.local = types.SimpleNamespace(site="test.site")

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe_utils
	return frappe


install_frappe_stub()

from erpnext_enhancements.travel_management import ics  # noqa: E402


def make_row(**kwargs):
	return types.SimpleNamespace(**kwargs)


def make_trip():
	return types.SimpleNamespace(
		name="TRIP-2026-00001",
		purpose="Trade show swing",
		travel_type="Domestic",
		start_date="2026-07-01",
		end_date="2026-07-03",
		flights=[
			make_row(
				name="fl1",
				traveler=None,
				airline="Acme Air",
				flight_number="AA100",
				departure_airport="PHX",
				arrival_airport="DEN",
				departure_time="2026-07-01 08:30:00",
				arrival_time="2026-07-01 10:30:00",
				booking_reference="PNR123",
			),
			make_row(
				name="fl2",
				traveler="EMP-OTHER",  # pinned to a different traveler
				airline="Acme Air",
				flight_number="AA200",
				departure_airport="DEN",
				arrival_airport="PHX",
				departure_time="2026-07-03 17:00:00",
				arrival_time=None,
				booking_reference=None,
			),
		],
		accommodations=[
			make_row(
				name="ho1",
				traveler=None,
				hotel_lodging="Grand Hotel",
				address="1 Main St",
				check_in_date="2026-07-01",
				check_out_date="2026-07-03",
				booking_confirmation="CONF9",
			)
		],
	)


def make_traveler(employee="EMP-001"):
	return make_row(name="tr1", employee=employee, from_date="2026-07-01", to_date="2026-07-03")


# --------------------------------------------------------------- build_ics


def test_crlf_and_envelope():
	out = ics.build_ics(
		[{"uid": "u1@test", "summary": "Hello", "start": "2026-07-01", "all_day": True}]
	)
	assert out.endswith("\r\n")
	lines = out.split("\r\n")
	assert lines[0] == "BEGIN:VCALENDAR"
	assert "METHOD:PUBLISH" in lines
	assert "BEGIN:VEVENT" in lines and "END:VEVENT" in lines
	assert lines[-2] == "END:VCALENDAR"


def test_text_escaping():
	out = ics.build_ics(
		[
			{
				"uid": "u1@test",
				"summary": "a,b;c\nd\\e",
				"start": "2026-07-01",
				"all_day": True,
			}
		]
	)
	assert "SUMMARY:a\\,b\\;c\\nd\\\\e" in out


def test_folding_at_75_octets():
	out = ics.build_ics(
		[{"uid": "u1@test", "summary": "x" * 200, "start": "2026-07-01", "all_day": True}]
	)
	for line in out.split("\r\n"):
		assert len(line.encode("utf-8")) <= 75
	# folded continuation reassembles to the original
	unfolded = out.replace("\r\n ", "")
	assert "SUMMARY:" + "x" * 200 in unfolded


def test_folding_never_splits_multibyte():
	out = ics.build_ics(
		[{"uid": "u1@test", "summary": "é" * 100, "start": "2026-07-01", "all_day": True}]
	)
	# decoding line-by-line would raise if a UTF-8 sequence was cut
	for line in out.split("\r\n"):
		line.encode("utf-8").decode("utf-8")


def test_timed_event_converted_to_utc():
	out = ics.build_ics(
		[
			{
				"uid": "u1@test",
				"summary": "Flight",
				"start": "2026-07-01 08:30:00",  # America/New_York, EDT = UTC-4
				"end": "2026-07-01 10:30:00",
			}
		]
	)
	assert "DTSTART:20260701T123000Z" in out
	assert "DTEND:20260701T143000Z" in out


def test_all_day_dtend_is_exclusive():
	out = ics.build_ics(
		[
			{
				"uid": "u1@test",
				"summary": "Trip",
				"start": "2026-07-01",
				"end": "2026-07-03",
				"all_day": True,
			}
		]
	)
	assert "DTSTART;VALUE=DATE:20260701" in out
	assert "DTEND;VALUE=DATE:20260704" in out  # exclusive end: +1 day


# ----------------------------------------------------- trip_events_for_traveler


def test_trip_events_span_flights_and_checkin():
	trip = make_trip()
	events = ics.trip_events_for_traveler(trip, make_traveler())
	assert len(events) == 3  # span + visible flight + hotel check-in
	assert events[0]["all_day"] is True
	assert any("AA100" in e["summary"] for e in events)
	assert any("Check-in" in e["summary"] for e in events)
	# flight pinned to another traveler is excluded
	assert not any("AA200" in e["summary"] for e in events)


def test_uids_are_stable_and_site_scoped():
	trip = make_trip()
	first = ics.trip_events_for_traveler(trip, make_traveler())
	second = ics.trip_events_for_traveler(trip, make_traveler())
	assert [e["uid"] for e in first] == [e["uid"] for e in second]
	assert all(uid.endswith("@test.site") for uid in (e["uid"] for e in first))
	assert first[1]["uid"] == "TRIP-2026-00001-fl1@test.site"


def test_pnr_lands_in_description():
	trip = make_trip()
	events = ics.trip_events_for_traveler(trip, make_traveler())
	flight = next(e for e in events if "AA100" in e["summary"])
	assert "PNR: PNR123" in flight["description"]
