"""Bench-free tests: the hand-off and launch meetings as Google Calendar events (v1.733.0).

Stubs a minimal ``frappe`` and a fake Google Calendar service, then drives the real
``crm_enhancements.handoff`` endpoints through ``crm_enhancements.handoff_calendar``.
The stub is installed in ``setUpModule``, like ``test_handoff_gate``, so it never
fools the bench-only suites' ``import frappe`` skip-guards; it gets its own CI step
so the two stubs never share a process.

What these guard, each a way the old invite failed or the new one could:

* **Who organizes.** The clicker's own connected calendar, then the shared fallback,
  then email — and a calendar that is switched off or was never authorized is
  skipped, not tried.
* **The guest list.** Every address, group addresses included. Frappe's own sync
  drops ``production@`` because it reads only Event Participants rows.
* **Google sends the invite.** ``sendUpdates="all"`` and a Meet request with
  ``conferenceDataVersion=1``; and then no ERPNext email as well (two invites for
  one meeting was the complaint).
* **Re-schedule moves the meeting.** The same Event and the same Google event,
  patched, guests' answers kept. Summit Vista has two Events for one hand-off
  (EV00454, EV00455) because re-schedule used to insert a second one.
* **Skip cancels it**, on Google with ``sendUpdates="all"``, and the Event reads
  Cancelled.
* **Failures fall back and say so.** A revoked token moves on to the next
  organizer, is logged with keyword arguments and no secret, and never leaves a
  queued message that would pop a red modal.
* **A time that has passed is refused.** EV00456 was booked at 09:21 for 01:30 the
  same morning; nothing converted a time zone, "1:30" on the 24-hour picker meant
  1:30 PM.

Run: python -m unittest erpnext_enhancements.tests.test_handoff_calendar
"""

import copy
import datetime
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

#: 2026-09-25 09:21 — the moment EV00456 was booked.
NOW = datetime.datetime(2026, 9, 25, 9, 21, 0)

#: A string that must never reach a log row or a response.
SECRET = "ya29.refresh-token-that-must-not-leak"

STATE = {}
handoff = None
handoff_calendar = None


class StubThrow(Exception):
	pass


class StubPermissionError(StubThrow):
	pass


class StubValidationError(StubThrow):
	pass


class FakeHttpError(Exception):
	"""Shaped like googleapiclient's HttpError: the status is on ``resp``."""

	def __init__(self, status):
		super().__init__(f"<HttpError {status} when requesting https://example.invalid/?token={SECRET}>")
		self.resp = types.SimpleNamespace(status=status)


class RefreshError(Exception):
	"""Named like google.auth's: what a revoked refresh token raises."""


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key)

	def __setattr__(self, key, value):
		self[key] = value


class StubEvent:
	"""An Event document. ``get_doc`` hands out fresh copies, as Frappe does."""

	def __init__(self, name=None, **fields):
		self.__dict__["name"] = name
		self.__dict__["doctype"] = "Event"
		self.__dict__["flags"] = _Dict()
		self.__dict__["_f"] = {"event_participants": [], **fields}

	def __getattr__(self, key):
		return self.__dict__["_f"].get(key)

	def __setattr__(self, key, value):
		if key in ("name", "flags"):
			self.__dict__[key] = value
		else:
			self.__dict__["_f"][key] = value

	def get(self, key, default=None):
		return self.__dict__["_f"].get(key, default)

	def set(self, key, value):
		self.__dict__["_f"][key] = value

	def append(self, table, row):
		self.__dict__["_f"].setdefault(table, []).append(row)

	def insert(self, ignore_permissions=False):
		STATE["event_seq"] += 1
		self.name = f"EV-{STATE['event_seq']}"
		STATE["events"][self.name] = copy.deepcopy(self.__dict__["_f"])
		STATE["inserted"].append(self.name)
		return self

	def save(self, ignore_permissions=False):
		STATE["events"][self.name] = copy.deepcopy(self.__dict__["_f"])
		STATE["saved"].append((self.name, dict(self.flags)))
		return self

	def get_doc_before_save(self):
		return None


class StubDoc:
	"""An Opportunity or Project."""

	def __init__(self, name, doctype, **fields):
		self.name = name
		self.doctype = doctype
		self.fields = fields
		self.comments = []

	def get(self, key, default=None):
		return self.fields.get(key, default)

	def has_permission(self, ptype="read"):
		return True

	def db_set(self, fieldname, value=None, update_modified=True):
		if isinstance(fieldname, dict):
			self.fields.update(fieldname)
		else:
			self.fields[fieldname] = value

	def add_comment(self, comment_type, text=None):
		self.comments.append(text)


class FakeRequest:
	def __init__(self, fn):
		self.fn = fn

	def execute(self):
		return self.fn()


class FakeEvents:
	"""The ``events()`` resource of one organizer's calendar."""

	def __init__(self, calendar):
		self.calendar = calendar

	def _record(self, method, kwargs):
		STATE["calls"].append((self.calendar, method, copy.deepcopy(kwargs)))
		failure = STATE["fail_calls"].get((self.calendar, method))
		if failure:
			raise failure

	def insert(self, **kwargs):
		def run():
			self._record("insert", kwargs)
			STATE["google_seq"] += 1
			event_id = f"g-{STATE['google_seq']}"
			stored = copy.deepcopy(kwargs["body"])
			stored.update(
				id=event_id,
				status="confirmed",
				organizer={"email": STATE["owners"][self.calendar]},
				htmlLink=f"https://www.google.com/calendar/event?eid={event_id}",
				attendees=[dict(a, responseStatus="needsAction") for a in stored.get("attendees", [])],
			)
			if "conferenceData" in stored:
				stored["hangoutLink"] = f"https://meet.google.com/{event_id}"
			STATE["google"][event_id] = stored
			return copy.deepcopy(stored)

		return FakeRequest(run)

	def get(self, **kwargs):
		def run():
			self._record("get", kwargs)
			if kwargs["eventId"] not in STATE["google"]:
				raise FakeHttpError(404)
			return copy.deepcopy(STATE["google"][kwargs["eventId"]])

		return FakeRequest(run)

	def patch(self, **kwargs):
		def run():
			self._record("patch", kwargs)
			stored = STATE["google"][kwargs["eventId"]]
			stored.update(copy.deepcopy(kwargs["body"]))
			return copy.deepcopy(stored)

		return FakeRequest(run)

	def delete(self, **kwargs):
		def run():
			self._record("delete", kwargs)
			if STATE["google"].pop(kwargs["eventId"], None) is None:
				raise FakeHttpError(410)
			return ""

		return FakeRequest(run)


class FakeService:
	def __init__(self, calendar):
		self.calendar = calendar

	def events(self):
		return FakeEvents(self.calendar)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe._ = lambda s: s
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.PermissionError = StubPermissionError
	frappe.ValidationError = StubValidationError
	frappe.DoesNotExistError = StubThrow
	frappe.flags = _Dict(mute_messages=False)
	frappe.local = types.SimpleNamespace(site="test.local")
	frappe.session = types.SimpleNamespace(user="ae@example.com")

	def msgprint(msg, *args, **kwargs):
		if frappe.flags.mute_messages:
			return
		STATE["message_log"].append(msg)

	def throw(msg, exc=None, title=None):
		# Like v16: the message is queued BEFORE the raise, so a caught throw
		# still reaches the browser unless mute_messages was on.
		msgprint(msg)
		if exc is StubPermissionError:
			raise StubPermissionError(msg)
		raise StubValidationError(msg)

	def log_error(*args, **kwargs):
		STATE["logged"].append((args, kwargs))

	frappe.msgprint = msgprint
	frappe.throw = throw
	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: "Traceback (most recent call last): ..."
	frappe.scrub = lambda s: str(s).lower().replace(" ", "_").replace("-", "_")
	frappe.parse_json = lambda v: __import__("json").loads(v)
	frappe.generate_hash = lambda *a, **k: "abcdef1234"
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.has_permission = lambda *a, **k: True
	frappe.sendmail = lambda **kwargs: STATE["sent"].append(kwargs)
	frappe.get_meta = lambda doctype: types.SimpleNamespace(
		has_field=lambda f: True, get_field=lambda f: None
	)

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			record = dict(doctype)
			kind = record.pop("doctype")
			doc = StubEvent(None, **record)
			doc.__dict__["doctype"] = kind

			def insert(ignore_permissions=False):
				doc.name = record["calendar_name"]
				STATE["gcals"][doc.name] = dict(record)
				STATE["gcal_inserted"].append(doc.name)
				return doc

			doc.__dict__["insert"] = insert
			return doc
		if doctype == "Event":
			if name not in STATE["events"]:
				raise StubThrow(f"Event {name} not found")
			return StubEvent(name, **copy.deepcopy(STATE["events"][name]))
		if doctype == "Google Calendar":
			doc = StubEvent(name, **STATE["gcals"][name])
			doc.__dict__["save"] = lambda **k: STATE["gcals"][name].update(doc.__dict__["_f"])
			return doc
		return STATE["docs"][name]

	frappe.get_doc = get_doc
	frappe.new_doc = lambda doctype: StubEvent(None)
	frappe.get_cached_doc = (
		lambda doctype: _Dict(STATE["google_settings"])
		if doctype == "Google Settings"
		else _Dict(STATE["singles"])
	)

	def get_all(doctype, filters=None, pluck=None, **kwargs):
		filters = filters or {}
		if doctype == "Google Calendar":
			return [
				name
				for name, row in STATE["gcals"].items()
				if all(row.get(k) == v for k, v in filters.items())
			]
		if doctype == "Event":
			prefix = filters["subject"][1].rstrip("%")
			return [
				name
				for name, row in reversed(list(STATE["events"].items()))
				if row.get("reference_doctype") == filters["reference_doctype"]
				and row.get("reference_docname") == filters["reference_docname"]
				and row.get("status") not in filters["status"][1]
				and str(row.get("subject") or "").startswith(prefix)
			][: kwargs.get("limit") or None]
		return []

	frappe.get_all = get_all

	db = types.SimpleNamespace()

	def get_value(doctype, filters, fieldname=None, as_dict=False):
		if doctype == "Event":
			row = STATE["events"].get(filters)
		elif doctype == "Google Calendar":
			if isinstance(filters, dict):
				matches = [
					n for n, r in STATE["gcals"].items() if all(r.get(k) == v for k, v in filters.items())
				]
				return matches[0] if matches else None
			row = STATE["gcals"].get(filters)
		elif doctype in ("User", "Employee", "Contact"):
			# No Employee or Contact behind any address: the participants grid stays
			# empty, which is exactly the group-address case.
			return None
		else:
			row = STATE["docs"][filters].fields if filters in STATE["docs"] else None
		if row is None:
			return None
		if as_dict:
			out = _Dict({f: row.get(f) for f in fieldname})
			out.setdefault("name", filters)
			return out
		return row.get(fieldname)

	def set_value(doctype, name, field, value=None, update_modified=True):
		assert doctype == "Event", doctype
		values = field if isinstance(field, dict) else {field: value}
		STATE["events"][name].update(values)

	db.get_value = get_value
	db.set_value = set_value
	db.get_single_value = lambda doctype, field: STATE["singles"].get(field)
	db.has_column = lambda doctype, column: True
	db.exists = lambda *a, **k: False
	frappe.db = db

	cache = types.SimpleNamespace()
	cache.get_value = lambda key: STATE["cache"].get(key)
	cache.set_value = lambda key, value, expires_in_sec=None: STATE["cache"].__setitem__(key, value)
	cache.delete_value = lambda key: STATE["cache"].pop(key, None)
	frappe.cache = cache

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: float(v or 0)
	utils.now_datetime = lambda: NOW
	utils.escape_html = lambda s: str(s)
	utils.format_datetime = lambda v: str(v)
	utils.get_url = lambda path="": f"https://test.local{path}"
	utils.get_request_site_address = lambda full_address=False: "https://test.local"
	utils.get_url_to_form = lambda dt, dn: f"https://test.local/app/{dt}/{dn}"
	utils.get_system_timezone = lambda: "America/Denver"
	utils.validate_email_address = lambda address, throw=False: (
		address if "@" in str(address) else (_ for _ in ()).throw(ValueError(address))
	)

	def get_datetime(value):
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.fromisoformat(str(value))

	utils.get_datetime = get_datetime
	utils.getdate = lambda value=None: get_datetime(value or NOW).date()
	utils.add_to_date = lambda dt, days=0, minutes=0, **k: get_datetime(dt) + datetime.timedelta(
		days=days, minutes=minutes
	)
	utils.add_days = lambda dt, days: get_datetime(dt) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (get_datetime(a) - get_datetime(b)).days
	utils.today = lambda: NOW.date().isoformat()
	frappe.utils = utils

	# Frappe v16's own Google Calendar module: the OAuth half this app reuses.
	gcal = types.ModuleType("frappe.integrations.doctype.google_calendar.google_calendar")

	def get_google_calendar_object(calendar):
		failure = STATE["fail_connect"].get(calendar)
		if failure == "throw":
			frappe.throw("Google Calendar - Could not find Calendar for x, error code 401.")
		if failure:
			raise failure
		account = types.SimpleNamespace(google_calendar_id=STATE["gcals"][calendar].get("google_calendar_id"))
		return FakeService(calendar), account

	def authorize_access(g_calendar, reauthorize=False):
		STATE["authorized"].append((g_calendar, reauthorize))
		return {"url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=x"}

	gcal.get_google_calendar_object = get_google_calendar_object
	gcal.authorize_access = authorize_access

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	for parent in (
		"frappe.integrations",
		"frappe.integrations.doctype",
		"frappe.integrations.doctype.google_calendar",
	):
		sys.modules[parent] = types.ModuleType(parent)
	sys.modules["frappe.integrations.doctype.google_calendar.google_calendar"] = gcal


class FakeStyle:
	"""email_style renders Jinja templates; the tests only need the text through."""

	wrap = staticmethod(lambda body, **k: body)
	p = staticmethod(lambda text: f"<p>{text}</p>")
	kv = staticmethod(lambda rows: "".join(f"{k}: {v}" for k, v in rows))
	button = staticmethod(lambda url, label: f"[{label}]({url})")


def setUpModule():
	global handoff, handoff_calendar
	_install_stubs()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	from erpnext_enhancements.crm_enhancements import handoff as handoff_mod
	from erpnext_enhancements.crm_enhancements import handoff_calendar as calendar_mod

	handoff = handoff_mod
	handoff_calendar = calendar_mod


def _reset():
	STATE.clear()
	STATE.update(
		roles=["System Manager"],
		singles={"process_automation_enabled": 1, "handoff_gate_enabled": 1},
		google_settings={"enable": 1, "client_id": "client.apps.googleusercontent.com"},
		docs={},
		events={},
		gcals={},
		owners={},
		google={},
		calls=[],
		fail_calls={},
		fail_connect={},
		cache={},
		sent=[],
		logged=[],
		message_log=[],
		inserted=[],
		saved=[],
		gcal_inserted=[],
		authorized=[],
		event_seq=0,
		google_seq=0,
	)
	sys.modules["frappe"].session.user = "ae@example.com"
	sys.modules["frappe"].flags.mute_messages = False


def _calendar(name, user, owner_email=None, authorized=True, enable=1, push=1):
	STATE["gcals"][name] = {
		"user": user,
		"enable": enable,
		"push_to_google_calendar": push,
		"refresh_token": "*****" if authorized else None,
		"google_calendar_id": "primary",
	}
	STATE["owners"][name] = owner_email or user


def _opportunity(name="CRM-OPP-1", **fields):
	STATE["docs"][name] = StubDoc(name, "Opportunity", customer_name="Summit Vista", **fields)
	return name


def _project(name="PROJ-1", **fields):
	STATE["docs"][name] = StubDoc(name, "Project", project_name="Fountain Rebuild", **fields)
	return name


GUESTS = ["ae@example.com", "production@example.com", "billing@example.com"]


def _book(opportunity, starts_on="2026-09-25 13:30:00", attendees=None):
	with mock.patch.object(handoff, "email_style", FakeStyle):
		return handoff.schedule_handoff_meeting(
			opportunity, starts_on=starts_on, attendees=attendees or GUESTS
		)


def _calls(method):
	return [call for call in STATE["calls"] if call[1] == method]


class TestOrganizerResolution(unittest.TestCase):
	"""Own calendar, then the shared fallback, then email — in that order."""

	def setUp(self):
		_reset()

	def test_the_clickers_own_calendar_organizes(self):
		_calendar("ae@example.com", "ae@example.com")
		_calendar("Hand-offs", "admin@example.com", owner_email="handoffs@example.com")
		STATE["singles"]["handoff_fallback_google_calendar"] = "Hand-offs"
		result = _book(_opportunity())
		self.assertEqual(result["calendar_path"], "own_calendar")
		self.assertEqual(result["organizer"], "ae@example.com")
		self.assertEqual([c[0] for c in _calls("insert")], ["ae@example.com"])

	def test_the_fallback_calendar_organizes_when_the_clicker_has_none(self):
		_calendar("Hand-offs", "admin@example.com", owner_email="handoffs@example.com")
		STATE["singles"]["handoff_fallback_google_calendar"] = "Hand-offs"
		result = _book(_opportunity())
		self.assertEqual(result["calendar_path"], "fallback_calendar")
		self.assertEqual(result["organizer"], "handoffs@example.com")

	def test_email_when_no_calendar_can(self):
		result = _book(_opportunity())
		self.assertEqual(result["calendar_path"], "email")
		self.assertEqual(STATE["calls"], [])
		self.assertEqual(len(STATE["sent"]), 1)
		self.assertTrue(result["invited"])

	def test_an_unauthorized_or_switched_off_calendar_is_not_tried(self):
		_calendar("ae@example.com", "ae@example.com", authorized=False)
		_calendar("Hand-offs", "admin@example.com", push=0)
		STATE["singles"]["handoff_fallback_google_calendar"] = "Hand-offs"
		self.assertEqual(handoff_calendar.organizers(), [])
		self.assertEqual(_book(_opportunity())["calendar_path"], "email")
		self.assertEqual(STATE["calls"], [])

	def test_another_users_calendar_is_never_the_clickers_own(self):
		_calendar("someone@example.com", "someone@example.com")
		self.assertIsNone(handoff_calendar.own_calendar("ae@example.com"))


class TestTheGoogleInvite(unittest.TestCase):
	"""One Google event, every address a guest, a Meet link, Google sends it."""

	def setUp(self):
		_reset()
		_calendar("ae@example.com", "ae@example.com")

	def test_group_addresses_are_guests(self):
		_book(_opportunity())
		body = _calls("insert")[0][2]["body"]
		self.assertEqual([a["email"] for a in body["attendees"]], GUESTS)
		self.assertTrue(body["guestsCanSeeOtherGuests"])

	def test_google_sends_the_invites_with_a_meet_request(self):
		result = _book(_opportunity())
		kwargs = _calls("insert")[0][2]
		self.assertEqual(kwargs["sendUpdates"], "all")
		self.assertEqual(kwargs["conferenceDataVersion"], 1)
		self.assertEqual(
			kwargs["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"],
			{"type": "hangoutsMeet"},
		)
		self.assertEqual(result["meet_link"], "https://meet.google.com/g-1")
		self.assertTrue(result["html_link"].startswith("https://www.google.com/calendar/"))

	def test_no_erpnext_email_when_google_sent_the_invite(self):
		_book(_opportunity())
		self.assertEqual(STATE["sent"], [])

	def test_the_event_keeps_the_google_ids_and_never_syncs_itself(self):
		result = _book(_opportunity())
		row = STATE["events"][result["event"]]
		self.assertEqual(row["google_calendar"], "ae@example.com")
		self.assertEqual(row["google_calendar_id"], "primary")
		self.assertEqual(row["google_calendar_event_id"], "g-1")
		self.assertEqual(row["google_meet_link"], "https://meet.google.com/g-1")
		# Frappe's own Event hooks would push a second copy built from the
		# participants grid, without the group addresses.
		self.assertFalse(row.get("sync_with_google_calendar"))

	def test_the_wall_clock_time_goes_to_google_with_the_site_zone(self):
		_book(_opportunity(), starts_on="2026-09-25 13:30:00")
		body = _calls("insert")[0][2]["body"]
		self.assertEqual(body["start"], {"dateTime": "2026-09-25T13:30:00", "timeZone": "America/Denver"})
		self.assertEqual(body["end"], {"dateTime": "2026-09-25T13:45:00", "timeZone": "America/Denver"})


class TestReschedule(unittest.TestCase):
	"""Booking again moves the same Event and the same Google event."""

	def setUp(self):
		_reset()
		_calendar("ae@example.com", "ae@example.com")

	def test_reschedule_patches_the_same_google_event_and_inserts_no_second_event(self):
		name = _opportunity()
		first = _book(name)
		STATE["google"]["g-1"]["attendees"][1]["responseStatus"] = "accepted"
		second = _book(name, starts_on="2026-09-26 10:00:00", attendees=[*GUESTS, "pm@example.com"])

		self.assertEqual(second["event"], first["event"])
		self.assertTrue(second["rescheduled"])
		self.assertEqual(STATE["inserted"], [first["event"]])
		self.assertEqual(len(_calls("insert")), 1)
		patch = _calls("patch")[0][2]
		self.assertEqual(patch["eventId"], "g-1")
		self.assertEqual(patch["sendUpdates"], "all")
		self.assertEqual(patch["body"]["start"]["dateTime"], "2026-09-26T10:00:00")
		self.assertEqual(STATE["events"][first["event"]]["starts_on"], datetime.datetime(2026, 9, 26, 10, 0))
		self.assertEqual(STATE["docs"][name].fields["custom_handoff_event"], first["event"])
		self.assertEqual(STATE["sent"], [])

	def test_changing_guests_keeps_the_answers_of_those_still_invited(self):
		name = _opportunity()
		_book(name)
		STATE["google"]["g-1"]["attendees"][1]["responseStatus"] = "accepted"
		_book(name, attendees=["production@example.com", "pm@example.com"])
		guests = _calls("patch")[0][2]["body"]["attendees"]
		self.assertEqual(
			guests,
			[{"email": "production@example.com", "responseStatus": "accepted"}, {"email": "pm@example.com"}],
		)

	def test_an_existing_meet_link_is_not_requested_again(self):
		name = _opportunity()
		_book(name)
		_book(name, starts_on="2026-09-26 10:00:00")
		self.assertNotIn("conferenceData", _calls("patch")[0][2]["body"])

	def test_a_cancelled_event_is_not_moved_back_to_life(self):
		name = _opportunity()
		first = _book(name)
		STATE["events"][first["event"]]["status"] = "Cancelled"
		second = _book(name, starts_on="2026-09-26 10:00:00")
		self.assertNotEqual(second["event"], first["event"])
		self.assertFalse(second["rescheduled"])

	def test_moving_an_emailed_invite_onto_google_withdraws_the_email(self):
		name = _opportunity()
		STATE["gcals"].clear()
		first = _book(name)
		self.assertEqual(first["calendar_path"], "email")
		_calendar("ae@example.com", "ae@example.com")
		STATE["sent"].clear()
		second = _book(name, starts_on="2026-09-26 10:00:00")
		self.assertEqual(second["calendar_path"], "own_calendar")
		self.assertEqual(len(STATE["sent"]), 1)
		cancel = STATE["sent"][0]
		self.assertEqual(sorted(cancel["recipients"]), sorted(GUESTS))
		ics = cancel["attachments"][0]["fcontent"]
		self.assertIn("METHOD:CANCEL", ics)
		self.assertIn("STATUS:CANCELLED", ics)

	def test_the_launch_meeting_moves_too(self):
		project = _project()
		with mock.patch.object(handoff, "email_style", FakeStyle):
			first = handoff.schedule_project_meeting(
				project, starts_on="2026-09-25 13:30:00", attendees=GUESTS
			)
			second = handoff.schedule_project_meeting(
				project, starts_on="2026-09-28 09:00:00", attendees=GUESTS
			)
		self.assertEqual(second["event"], first["event"])
		self.assertTrue(second["rescheduled"])
		self.assertEqual(len(_calls("insert")), 1)
		self.assertEqual(len(_calls("patch")), 1)
		self.assertEqual(STATE["inserted"], [first["event"]])


class TestSkipCancels(unittest.TestCase):
	def setUp(self):
		_reset()
		_calendar("ae@example.com", "ae@example.com")

	def test_skip_deletes_the_google_event_for_everyone_and_cancels_the_event(self):
		name = _opportunity()
		booked = _book(name)
		result = handoff.skip_handoff(name, "Repeat customer, same crew")
		delete = _calls("delete")[0][2]
		self.assertEqual(delete["eventId"], "g-1")
		self.assertEqual(delete["sendUpdates"], "all")
		self.assertEqual(STATE["events"][booked["event"]]["status"], "Cancelled")
		self.assertTrue(result["meeting"]["cancelled"])
		self.assertIsNone(result["meeting"]["warning"])

	def test_skip_still_records_when_google_refuses(self):
		name = _opportunity()
		booked = _book(name)
		STATE["fail_calls"][("ae@example.com", "delete")] = FakeHttpError(500)
		result = handoff.skip_handoff(name, "No meeting needed")
		self.assertFalse(result["already"])
		self.assertEqual(STATE["docs"][name].fields["custom_handoff_skip_reason"], "No meeting needed")
		self.assertFalse(result["meeting"]["cancelled"])
		self.assertIn("HTTP 500", result["meeting"]["warning"])
		self.assertEqual(STATE["events"][booked["event"]]["status"], "Cancelled")

	def test_skip_withdraws_an_emailed_invite(self):
		STATE["gcals"].clear()
		name = _opportunity()
		_book(name)
		STATE["sent"].clear()
		with mock.patch.object(handoff, "email_style", FakeStyle):
			handoff.skip_handoff(name, "No meeting needed")
		self.assertEqual(len(STATE["sent"]), 1)
		self.assertIn("METHOD:CANCEL", STATE["sent"][0]["attachments"][0]["fcontent"])

	def test_cancelling_the_event_in_the_desk_cancels_the_invite(self):
		booked = _book(_opportunity())
		event = sys.modules["frappe"].get_doc("Event", booked["event"])
		event.status = "Cancelled"
		handoff_calendar.cancel_on_status_change(event)
		self.assertEqual(len(_calls("delete")), 1)

	def test_the_apps_own_saves_do_not_cancel_twice(self):
		booked = _book(_opportunity())
		event = sys.modules["frappe"].get_doc("Event", booked["event"])
		event.status = "Cancelled"
		event.flags.ee_handoff_calendar = True
		handoff_calendar.cancel_on_status_change(event)
		self.assertEqual(_calls("delete"), [])


class TestFailuresFallBack(unittest.TestCase):
	def setUp(self):
		_reset()
		_calendar("ae@example.com", "ae@example.com")
		_calendar("Hand-offs", "admin@example.com", owner_email="handoffs@example.com")
		STATE["singles"]["handoff_fallback_google_calendar"] = "Hand-offs"

	def test_a_revoked_token_falls_back_to_the_shared_calendar(self):
		STATE["fail_connect"]["ae@example.com"] = RefreshError(f"invalid_grant {SECRET}")
		result = _book(_opportunity())
		self.assertEqual(result["calendar_path"], "fallback_calendar")
		self.assertEqual(len(result["warnings"]), 1)
		self.assertIn("reconnect", result["warnings"][0])
		self.assertEqual(STATE["sent"], [])

	def test_every_organizer_failing_falls_back_to_one_email(self):
		STATE["fail_connect"]["ae@example.com"] = RefreshError(SECRET)
		STATE["fail_calls"][("Hand-offs", "insert")] = FakeHttpError(403)
		result = _book(_opportunity())
		self.assertEqual(result["calendar_path"], "email")
		self.assertEqual(len(result["warnings"]), 2)
		self.assertEqual(len(STATE["sent"]), 1)

	def test_failures_are_logged_with_keywords_and_no_secret(self):
		STATE["fail_connect"]["ae@example.com"] = RefreshError(SECRET)
		STATE["fail_calls"][("Hand-offs", "insert")] = FakeHttpError(403)
		result = _book(_opportunity())
		self.assertEqual(len(STATE["logged"]), 2)
		for args, kwargs in STATE["logged"]:
			self.assertEqual(args, ())
			self.assertIn("title", kwargs)
			self.assertNotIn("\n", kwargs["title"])
			self.assertNotIn(SECRET, kwargs["message"])
		self.assertNotIn(SECRET, repr(result))

	def test_a_caught_frappe_throw_leaves_no_message_for_the_browser(self):
		STATE["fail_connect"]["ae@example.com"] = "throw"
		_book(_opportunity())
		self.assertEqual(STATE["message_log"], [])
		self.assertFalse(sys.modules["frappe"].flags.mute_messages)

	def test_a_reschedule_whose_organizer_lost_access_reinvites_and_says_so(self):
		name = _opportunity()
		_book(name)
		STATE["fail_connect"]["ae@example.com"] = RefreshError(SECRET)
		result = _book(name, starts_on="2026-09-26 10:00:00")
		self.assertEqual(result["calendar_path"], "fallback_calendar")
		# Not tried twice: connecting to the dead calendar failed once.
		self.assertEqual([c[0] for c in _calls("insert")], ["ae@example.com", "Hand-offs"])
		self.assertTrue(any("earlier Google invite" in line for line in result["warnings"]))


class TestPastTimes(unittest.TestCase):
	"""EV00456: booked at 09:21 for 01:30 the same morning."""

	def setUp(self):
		_reset()

	def test_a_time_hours_ago_is_refused(self):
		with self.assertRaises(StubThrow):
			_book(_opportunity(), starts_on="2026-09-25 01:30:00")
		self.assertEqual(STATE["inserted"], [])

	def test_a_meeting_starting_now_is_still_fine(self):
		result = _book(_opportunity(), starts_on="2026-09-25 09:00:00")
		self.assertEqual(result["starts_on"], "2026-09-25 09:00:00")

	def test_the_launch_meeting_refuses_it_too(self):
		with self.assertRaises(StubThrow):
			handoff.schedule_project_meeting(_project(), starts_on="2026-09-24 13:30:00", attendees=GUESTS)


class TestEmailInvite(unittest.TestCase):
	"""The last resort is a REQUEST invite, not a PUBLISH copy."""

	def setUp(self):
		_reset()

	def test_the_email_carries_one_request_with_organizer_and_attendees(self):
		_book(_opportunity())
		(sent,) = STATE["sent"]
		attachment = sent["attachments"][0]
		self.assertEqual(attachment["content_type"], "text/calendar; method=REQUEST")
		# Unfolded: RFC 5545 wraps content lines at 75 octets.
		ics = attachment["fcontent"].replace("\r\n ", "")
		self.assertIn("METHOD:REQUEST", ics)
		self.assertIn("ORGANIZER:mailto:ae@example.com", ics)
		for address in GUESTS:
			self.assertIn(
				f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{address}", ics
			)
		self.assertIn("SEQUENCE:", ics)

	def test_an_event_without_the_new_keys_is_written_as_before(self):
		from erpnext_enhancements.travel_management.ics import build_ics

		ics = build_ics([{"uid": "u@x", "summary": "S", "start": "2026-09-25 10:00:00"}])
		self.assertNotIn("ORGANIZER", ics)
		self.assertNotIn("ATTENDEE", ics)
		self.assertIn("METHOD:PUBLISH", ics)


class TestGuestsAndConnect(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_guests_come_from_google_once_then_from_the_cache(self):
		_calendar("ae@example.com", "ae@example.com")
		booked = _book(_opportunity())
		STATE["google"]["g-1"]["attendees"][2]["responseStatus"] = "declined"
		first = handoff_calendar.meeting_guests(booked["event"])
		second = handoff_calendar.meeting_guests(booked["event"])
		self.assertEqual(first, second)
		self.assertEqual(len(_calls("get")), 1)
		self.assertEqual(
			[(g["email"], g["response"]) for g in first["guests"]],
			[
				("ae@example.com", "needsAction"),
				("production@example.com", "needsAction"),
				("billing@example.com", "declined"),
			],
		)
		self.assertEqual(first["meet_link"], "https://meet.google.com/g-1")

	def test_a_google_failure_reads_as_a_message_not_an_error(self):
		_calendar("ae@example.com", "ae@example.com")
		booked = _book(_opportunity())
		STATE["fail_connect"]["ae@example.com"] = RefreshError(SECRET)
		result = handoff_calendar.meeting_guests(booked["event"])
		self.assertIn("reconnect", result["error"])
		self.assertNotIn(SECRET, result["error"])

	def test_the_step_card_summary_costs_no_google_call(self):
		_calendar("ae@example.com", "ae@example.com")
		booked = _book(_opportunity())
		STATE["calls"].clear()
		summary = handoff_calendar.meeting_summary(booked["event"])
		self.assertEqual(summary["meet_link"], "https://meet.google.com/g-1")
		self.assertTrue(summary["google"])
		self.assertEqual(STATE["calls"], [])

	def test_connect_creates_a_push_only_calendar_for_the_session_user(self):
		result = handoff_calendar.connect_google_calendar()
		record = STATE["gcals"]["ae@example.com"]
		self.assertEqual(record["user"], "ae@example.com")
		self.assertEqual(record["pull_from_google_calendar"], 0)
		self.assertEqual(record["push_to_google_calendar"], 1)
		self.assertEqual(record["google_calendar_id"], "primary")
		self.assertEqual(STATE["authorized"], [("ae@example.com", True)])
		self.assertTrue(result["url"].startswith("https://accounts.google.com/"))
		self.assertEqual(
			result["redirect_uri"],
			"https://test.local?cmd=frappe.integrations.doctype.google_calendar.google_calendar.google_callback",
		)

	def test_connect_reuses_an_existing_record(self):
		_calendar("ae@example.com", "ae@example.com", authorized=False)
		handoff_calendar.connect_google_calendar()
		self.assertEqual(STATE["gcal_inserted"], [])
		self.assertEqual(STATE["authorized"], [("ae@example.com", True)])

	def test_connect_says_what_to_do_when_google_is_not_set_up(self):
		STATE["google_settings"]["enable"] = 0
		with self.assertRaises(StubThrow) as raised:
			handoff_calendar.connect_google_calendar()
		self.assertIn("Google Settings", str(raised.exception))

	def test_the_dialog_status_names_the_path(self):
		self.assertEqual(handoff_calendar.calendar_status()["calendar_path"], "email")
		_calendar("Hand-offs", "admin@example.com")
		STATE["singles"]["handoff_fallback_google_calendar"] = "Hand-offs"
		self.assertEqual(handoff_calendar.calendar_status()["calendar_path"], "fallback_calendar")
		_calendar("ae@example.com", "ae@example.com")
		self.assertEqual(handoff_calendar.calendar_status()["calendar_path"], "own_calendar")


if __name__ == "__main__":
	unittest.main()
