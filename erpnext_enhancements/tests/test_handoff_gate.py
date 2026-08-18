"""Bench-free unit tests for the Closed-Won hand-off gate (PRO-0204, 2026-08-06).

Stubs a minimal ``frappe`` (no site/bench) so the decision logic in
``erpnext_enhancements.crm_enhancements.handoff`` and the business-day SLA maths
run under plain unittest. The stub is installed in ``setUpModule`` (execution
time), not at import, so it never fools the bench-only suites' ``import frappe``
skip-guards.

What these guard, in order of how expensive the failure is:

* **The gate itself.** A gate that silently stops gating looks exactly like a
  gate that is working — the audit found 8 of 28 projects created off-process and
  nothing reported it. Includes the legacy-exemption case, because a gate that
  over-fires would freeze a 227-deal backlog.
* **Business-day maths across a weekend.** An SLA that lands on a Sunday is
  wrong in a way nobody notices until somebody is nagged a day early, or two
  days late, forever.
* **Attendee resolution.** A meeting invite that silently drops Production or
  Billing produces a "hand-off meeting" that hands nothing off.
* **Notification recipients.** Specifically that step 5 produces nothing and
  step 4 does: nagging AR daily about when a *customer* pays teaches people to
  filter these emails, which costs us the steps that are ours.

Run: python -m unittest erpnext_enhancements.tests.test_handoff_gate
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

#: Mutable state the frappe stub reads at call time.
STATE = {
	"roles": [],
	"user": "ae@example.com",
	"singles": {},
	"docs": {},
	"columns": set(),
	"records": {},
	"role_holders": {},
	"users": {},
	"holidays": [],
}

handoff = None
working_days = None

#: Fixed "now" so every assertion on a due date can be exact.
#: 2026-08-06 is a THURSDAY — chosen so the +2 business-day SLA has to cross a
#: weekend to be right.
NOW = datetime.datetime(2026, 8, 6, 9, 0, 0)


class StubThrow(Exception):
	pass


class StubPermissionError(StubThrow):
	pass


class _StubDoc:
	"""Minimal Document stand-in: attribute + .get access, and db_set."""

	def __init__(self, name, doctype="Opportunity", **fields):
		self.name = name
		self.doctype = doctype
		self._fields = fields
		self.comments = []
		self.permissions = {"read": True, "write": True}

	def get(self, key, default=None):
		return self._fields.get(key, default)

	def __getattr__(self, key):
		try:
			return self.__dict__["_fields"][key]
		except KeyError:
			raise AttributeError(key)

	def has_permission(self, ptype="read"):
		return self.permissions.get(ptype, True)

	def db_set(self, fieldname, value=None, update_modified=True, **kwargs):
		if isinstance(fieldname, dict):
			self._fields.update(fieldname)
			STATE["records"].setdefault(self.name, {}).update(fieldname)
		else:
			self._fields[fieldname] = value
			STATE["records"].setdefault(self.name, {})[fieldname] = value

	def add_comment(self, comment_type, text=None, **kwargs):
		self.comments.append((comment_type, text))


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def throw(msg, exc=None, title=None):
		if exc is not None and "Permission" in str(exc):
			raise StubPermissionError(msg)
		raise StubThrow(msg)

	class _Dict(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError:
				raise AttributeError(key)

		def __setattr__(self, key, value):
			self[key] = value

	# Pass-through: the decorator only registers the method for HTTP dispatch,
	# and these tests call the functions directly.
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	frappe.throw = throw
	frappe.ValidationError = StubThrow
	frappe.PermissionError = StubPermissionError
	frappe._ = lambda s: s
	frappe._dict = _Dict
	frappe.session = types.SimpleNamespace(user=STATE["user"])
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.local = types.SimpleNamespace(site="test.local")
	frappe.scrub = lambda s: str(s).lower().replace(" ", "_").replace("-", "_")
	frappe.parse_json = lambda v: __import__("json").loads(v)
	frappe.log_error = lambda *a, **k: None
	# Real code calls this from inside `except` blocks. Without it the stub turns
	# a swallowed, logged failure into an AttributeError that escapes.
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.sendmail = lambda **kwargs: STATE.setdefault("sent", []).append(kwargs)
	frappe.new_doc = lambda dt: _StubDoc("NEW", doctype=dt)
	frappe.get_cached_doc = lambda dt: _StubDoc(dt, doctype=dt, **STATE["singles"])
	frappe.get_single = frappe.get_cached_doc

	def get_doc(doctype, name=None):
		key = name or doctype
		if key not in STATE["docs"]:
			raise StubThrow(f"{doctype} {key} not found")
		return STATE["docs"][key]

	frappe.get_doc = get_doc
	frappe.has_permission = lambda doctype, ptype="read", doc=None: True
	# feature_flags guards every Settings read with has_field, because v16's
	# get_single_value throws for a field the meta does not know yet.
	frappe.get_meta = lambda doctype: types.SimpleNamespace(
		has_field=lambda field: field in STATE["singles"],
		get_field=lambda field: object() if field in STATE["columns"] else None,
	)

	db = types.SimpleNamespace()
	db.has_column = lambda doctype, column: column in STATE["columns"]
	db.exists = lambda doctype, name=None: (
		name in STATE["records"] if name else doctype in STATE["records"]
	)

	def get_value(doctype, filters, fieldname=None, as_dict=False):
		record = STATE["records"].get(filters) if isinstance(filters, str) else None
		if record is None:
			return None
		if as_dict:
			out = _Dict({f: record.get(f) for f in fieldname})
			out["name"] = filters
			return out
		if isinstance(fieldname, list):
			return [record.get(f) for f in fieldname]
		return record.get(fieldname)

	db.get_value = get_value
	db.get_single_value = lambda doctype, field: STATE["singles"].get(field)
	db.set_value = lambda *a, **k: None
	db.count = lambda *a, **k: 0

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		if doctype == "Has Role":
			role = (filters or {}).get("role")
			return STATE["role_holders"].get(role, [])
		if doctype == "User":
			wanted = (filters or {}).get("name", ("in", []))[1]
			return [u for u in wanted if STATE["users"].get(u, {}).get("enabled", True)]
		if doctype == "Holiday":
			return [_Dict(holiday_date=d) for d in STATE["holidays"]]
		return []

	frappe.db = db
	frappe.get_all = get_all

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: float(v or 0)
	utils.now_datetime = lambda: NOW
	utils.escape_html = lambda s: str(s)
	utils.format_datetime = lambda v: str(v)
	utils.format_date = lambda v: str(v)
	utils.get_url = lambda path="": f"https://test.local{path}"
	utils.get_url_to_form = lambda dt, dn: f"https://test.local/app/{dt}/{dn}"
	utils.quoted = lambda s: str(s)
	utils.validate_email_address = lambda address, throw=False: (
		address if "@" in str(address) else (_ for _ in ()).throw(ValueError(address))
	)

	def get_datetime(value):
		if isinstance(value, datetime.datetime):
			return value
		if isinstance(value, datetime.date):
			return datetime.datetime(value.year, value.month, value.day)
		return datetime.datetime.fromisoformat(str(value))

	def getdate(value=None):
		if value is None:
			return NOW.date()
		if isinstance(value, datetime.datetime):
			return value.date()
		if isinstance(value, datetime.date):
			return value
		return datetime.date.fromisoformat(str(value)[:10])

	def add_to_date(dt, days=0, minutes=0, **kwargs):
		return get_datetime(dt) + datetime.timedelta(days=days, minutes=minutes)

	utils.get_datetime = get_datetime
	utils.getdate = getdate
	utils.add_to_date = add_to_date
	utils.date_diff = lambda a, b: (getdate(a) - getdate(b)).days
	utils.today = lambda: NOW.date().isoformat()
	utils.add_days = lambda dt, days: get_datetime(dt) + datetime.timedelta(days=days)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global handoff, working_days
	_install_frappe_stub()
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements"):
			sys.modules.pop(module, None)
	from erpnext_enhancements.crm_enhancements import handoff as handoff_mod
	from erpnext_enhancements.utils import working_days as wd_mod

	handoff = handoff_mod
	working_days = wd_mod


def _reset(**overrides):
	STATE.update(
		roles=[],
		user="ae@example.com",
		singles={"process_automation_enabled": 1, "handoff_gate_enabled": 1},
		docs={},
		columns={
			"custom_handoff_gate_applies",
			"custom_handoff_meeting_held",
			"custom_handoff_event",
			"custom_handoff_due_by",
			"custom_launch_deadline",
		},
		records={},
		role_holders={},
		users={},
		holidays=[],
		sent=[],
	)
	STATE.update(overrides)
	sys.modules["frappe"].session.user = STATE["user"]


def _opportunity(name="CRM-OPP-0001", **fields):
	record = {
		"name": name,
		"status": "Closed Won",
		"custom_handoff_gate_applies": 1,
		"custom_handoff_meeting_held": 0,
		"opportunity_owner": "ae@example.com",
	}
	record.update(fields)
	STATE["records"][name] = record
	doc_fields = {k: v for k, v in record.items() if k != "name"}
	STATE["docs"][name] = _StubDoc(name, **doc_fields)
	return name


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestGate(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_won_without_handoff_is_blocked(self):
		"""The whole point: a won deal with no hand-off at all cannot convert."""
		name = _opportunity()
		self.assertEqual(
			handoff.handoff_block_reason(name),
			"Schedule the Hand-Off Meeting on the Opportunity first.",
		)

	def test_message_is_the_exact_agreed_wording(self):
		"""The meeting asked for an error that says what to do. Pinned verbatim.

		Reworded in v1.263.0 when the gate moved from "recorded" to "booked": an
		error that asks for something other than what actually unblocks it sends
		people to the wrong button.
		"""
		self.assertEqual(
			handoff.GATE_MESSAGE,
			"Schedule the Hand-Off Meeting on the Opportunity first.",
		)

	def test_throw_helper_raises_for_blocked(self):
		name = _opportunity()
		with self.assertRaises(StubThrow):
			handoff.throw_if_handoff_missing(name)

	def test_booked_meeting_unblocks(self):
		"""v1.263.0: booking the meeting opens the gate — recording it is separate.

		The meeting is routinely two days out while the project needs to start
		now. Gating on "held" was therefore inviting the exact falsification the
		audit was about: tick it for a meeting still in the diary.
		"""
		name = _opportunity(custom_handoff_event="EV-00001")
		self.assertIsNone(handoff.handoff_block_reason(name))
		handoff.throw_if_handoff_missing(name)  # must not raise

	def test_recorded_handoff_unblocks(self):
		name = _opportunity(custom_handoff_meeting_held=1)
		self.assertIsNone(handoff.handoff_block_reason(name))
		handoff.throw_if_handoff_missing(name)  # must not raise

	def test_legacy_backlog_is_exempt(self):
		"""The 227 deals already won never transitioned, so they never got the flag.

		Over-firing here would freeze a backlog the team explicitly decided not to
		hold meetings for — as damaging as under-firing, and far more visible.
		"""
		name = _opportunity(custom_handoff_gate_applies=0)
		self.assertIsNone(handoff.handoff_block_reason(name))

	def test_open_opportunity_is_not_gated(self):
		name = _opportunity(status="Open")
		self.assertIsNone(handoff.handoff_block_reason(name))

	def test_gate_respects_its_own_switch(self):
		_reset(singles={"process_automation_enabled": 1, "handoff_gate_enabled": 0})
		name = _opportunity()
		self.assertIsNone(handoff.handoff_block_reason(name))

	def test_gate_respects_the_suite_master_switch(self):
		"""Turning the suite off must not leave creation blocked with no UI to fix it."""
		_reset(singles={"process_automation_enabled": 0, "handoff_gate_enabled": 1})
		name = _opportunity()
		self.assertIsNone(handoff.handoff_block_reason(name))

	def test_inert_before_the_fields_exist(self):
		"""doc_events fire during ERPNext's test bootstrap, before our fields exist."""
		_reset(columns=set())
		name = _opportunity()
		self.assertIsNone(handoff.handoff_block_reason(name))

	def test_missing_opportunity_does_not_block(self):
		self.assertIsNone(handoff.handoff_block_reason("CRM-OPP-NOPE"))
		self.assertIsNone(handoff.handoff_block_reason(None))


class TestSkipPath(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_skip_requires_system_manager(self):
		name = _opportunity()
		STATE["roles"] = ["Sales User"]
		with self.assertRaises(StubPermissionError):
			handoff.skip_handoff(name, "no meeting needed")

	def test_skip_requires_a_reason(self):
		"""Silence must be impossible. A blank reason is silence with extra steps."""
		name = _opportunity()
		STATE["roles"] = ["System Manager"]
		for blank in ("", "   ", None):
			with self.assertRaises(StubThrow):
				handoff.skip_handoff(name, blank)

	def test_skip_records_reason_and_unblocks(self):
		name = _opportunity()
		STATE["roles"] = ["System Manager"]
		result = handoff.skip_handoff(name, "  Repeat customer, same crew  ")

		self.assertEqual(result["reason"], "Repeat customer, same crew")
		stored = STATE["records"][name]
		self.assertEqual(stored["custom_handoff_meeting_held"], 1)
		self.assertEqual(stored["custom_handoff_skip_reason"], "Repeat customer, same crew")
		# Server-generated, not client-supplied — the audit found retroactive
		# box-checking, so this is the assertion that matters.
		self.assertEqual(stored["custom_handoff_meeting_on"], NOW)
		self.assertEqual(stored["custom_handoff_meeting_by"], "ae@example.com")
		# And the skip is visible, not merely recorded.
		self.assertTrue(any("SKIPPED" in (text or "") for _, text in STATE["docs"][name].comments))

	def test_mark_complete_stamps_server_side(self):
		name = _opportunity()
		result = handoff.mark_handoff_complete(name)

		self.assertFalse(result["already"])
		stored = STATE["records"][name]
		self.assertEqual(stored["custom_handoff_meeting_held"], 1)
		self.assertEqual(stored["custom_handoff_meeting_on"], NOW)
		self.assertEqual(stored["custom_handoff_meeting_by"], "ae@example.com")
		self.assertIsNone(stored["custom_handoff_skip_reason"])

	def test_mark_complete_is_idempotent(self):
		name = _opportunity(custom_handoff_meeting_held=1)
		self.assertTrue(handoff.mark_handoff_complete(name)["already"])


# ---------------------------------------------------------------------------
# Recording the hand-off after the project already exists
# ---------------------------------------------------------------------------


class TestMirrorOntoProject(unittest.TestCase):
	"""Closing step 2 on a Project created before the hand-off was recorded.

	A state that could not occur before v1.263.0: the gate used to require the
	hand-off *recorded*, so ``_append_steps`` always found the completion waiting
	and seeded step 2 done. Now that booking opens the gate, the project is
	usually created first and its step 2 sits Pending — with nothing to close it,
	the tracker and the escalation sweep would nag forever about a meeting that
	already happened.
	"""

	def setUp(self):
		_reset()
		STATE["columns"].add("custom_created_project")
		import erpnext_enhancements.process_steps as process_steps

		self.process_steps = process_steps

	def _project(self, name="PROJ-0001", step_two_status="Pending"):
		frappe = sys.modules["frappe"]
		rows = [
			frappe._dict(name="row1", step_number=1, status="Completed"),
			frappe._dict(name="row2", step_number=2, status=step_two_status),
			frappe._dict(name="row3", step_number=3, status="Completed"),
		]
		doc = _StubDoc(name, doctype="Project", custom_process_steps=rows)
		doc.meta = types.SimpleNamespace(get_field=lambda field: object())
		doc.saved = []
		doc.save = lambda **kwargs: doc.saved.append(kwargs)
		STATE["records"][name] = {"name": name}
		STATE["docs"][name] = doc
		return doc, rows[1]

	def test_pending_step_two_is_closed_with_the_opportunitys_own_stamps(self):
		"""Not re-stamped "now": the SLA report measures how long it took."""
		opportunity = _opportunity(custom_created_project="PROJ-0001")
		doc, row = self._project()
		held_on = datetime.datetime(2026, 8, 4, 11, 30)

		touched = self.process_steps.record_handoff_on_project(
			opportunity, when=held_on, by="pm@example.com"
		)

		self.assertEqual(touched, "PROJ-0001")
		self.assertEqual(row.status, "Completed")
		self.assertEqual(row.completed_on, held_on)
		self.assertEqual(row.completed_by, "pm@example.com")
		# ignore_permissions: whoever recorded the hand-off owns the Opportunity,
		# which is not the same as holding write on the Project.
		self.assertEqual(doc.saved, [{"ignore_permissions": True}])

	def test_a_skip_reads_as_a_skip_on_the_project_too(self):
		opportunity = _opportunity(custom_created_project="PROJ-0001")
		_, row = self._project()

		self.process_steps.record_handoff_on_project(
			opportunity, when=NOW, by="admin@example.com", skip_reason="Repeat customer"
		)
		self.assertTrue(row.notes.startswith("[SKIPPED]"))

	def test_a_step_already_completed_is_left_alone(self):
		"""No second save, and no overwriting whoever actually completed it."""
		opportunity = _opportunity(custom_created_project="PROJ-0001")
		doc, _ = self._project(step_two_status="Completed")

		self.assertIsNone(self.process_steps.record_handoff_on_project(opportunity, when=NOW))
		self.assertEqual(doc.saved, [])

	def test_no_project_yet_is_a_no_op(self):
		"""The commonest case by far — the hand-off is recorded before the project."""
		self.assertIsNone(
			self.process_steps.record_handoff_on_project(_opportunity(), when=NOW)
		)

	def test_marking_complete_reaches_the_project(self):
		"""End to end: the button on the Opportunity closes the row on the Project."""
		opportunity = _opportunity(custom_created_project="PROJ-0001")
		_, row = self._project()

		handoff.mark_handoff_complete(opportunity)

		self.assertEqual(STATE["records"][opportunity]["custom_handoff_meeting_held"], 1)
		self.assertEqual(row.status, "Completed")
		self.assertEqual(row.completed_on, NOW)


# ---------------------------------------------------------------------------
# Business-day SLA maths
# ---------------------------------------------------------------------------


class TestBusinessDaySLA(unittest.TestCase):
	"""A due date that lands on a Sunday is wrong in a way nobody notices."""

	def setUp(self):
		_reset()

	def test_thursday_plus_two_business_days_is_monday(self):
		thursday = datetime.datetime(2026, 8, 6, 9, 0)
		self.assertEqual(thursday.weekday(), 3)
		due = working_days.add_working_days(thursday, 2)
		self.assertEqual(due.date(), datetime.date(2026, 8, 10))  # Monday
		self.assertEqual(due.weekday(), 0)

	def test_friday_plus_two_business_days_is_tuesday(self):
		friday = datetime.datetime(2026, 8, 7, 14, 30)
		self.assertEqual(friday.weekday(), 4)
		due = working_days.add_working_days(friday, 2)
		self.assertEqual(due.date(), datetime.date(2026, 8, 11))  # Tuesday

	def test_monday_plus_two_business_days_stays_in_week(self):
		monday = datetime.datetime(2026, 8, 3, 9, 0)
		self.assertEqual(monday.weekday(), 0)
		self.assertEqual(
			working_days.add_working_days(monday, 2).date(), datetime.date(2026, 8, 5)
		)

	def test_seven_business_day_launch_goal_spans_two_weekends_from_wednesday(self):
		wednesday = datetime.datetime(2026, 8, 5, 9, 0)
		due = working_days.add_working_days(wednesday, handoff.LAUNCH_GOAL_BUSINESS_DAYS)
		# Wed +7 business days: Thu Fri (2), Mon-Fri (7) -> Fri 14 Aug.
		self.assertEqual(due.date(), datetime.date(2026, 8, 14))
		self.assertEqual(due.weekday(), 4)

	def test_time_of_day_is_preserved(self):
		start = datetime.datetime(2026, 8, 6, 16, 45)
		self.assertEqual(working_days.add_working_days(start, 2).time(), start.time())

	def test_zero_and_negative_days_are_a_no_op(self):
		start = datetime.datetime(2026, 8, 6, 9, 0)
		self.assertEqual(working_days.add_working_days(start, 0), start)
		self.assertEqual(working_days.add_working_days(start, -3), start)

	def test_configured_holiday_is_skipped(self):
		"""A holiday list is honoured on top of weekends, not instead of them."""
		STATE["holidays"] = [datetime.date(2026, 8, 10)]  # the Monday
		thursday = datetime.datetime(2026, 8, 6, 9, 0)
		due = working_days.add_working_days(thursday, 2, holiday_list="Utah")
		self.assertEqual(due.date(), datetime.date(2026, 8, 11))  # Tuesday instead

	def test_sla_constants_match_the_meeting(self):
		self.assertEqual(handoff.HANDOFF_SLA_BUSINESS_DAYS, 2)
		self.assertEqual(handoff.LAUNCH_GOAL_BUSINESS_DAYS, 7)


class TestGateStamping(unittest.TestCase):
	def setUp(self):
		_reset()

	def _doc(self, status="Closed Won", before_status="Open", **fields):
		doc = _StubDoc("CRM-OPP-0001", status=status, **fields)
		doc.status = status
		doc.meta = types.SimpleNamespace(get_field=lambda f: object())
		doc.get_doc_before_save = lambda: (
			_StubDoc("CRM-OPP-0001", status=before_status) if before_status else None
		)
		if before_status:
			doc.get_doc_before_save().status = before_status
		return doc

	def test_transition_stamps_flag_and_both_deadlines(self):
		doc = self._doc()
		handoff.stamp_handoff_gate(doc)

		self.assertEqual(doc.custom_handoff_gate_applies, 1)
		# NOW is Thursday 2026-08-06: +2 business days -> Monday the 10th.
		self.assertEqual(doc.custom_handoff_due_by.date(), datetime.date(2026, 8, 10))
		# +7 business days -> Monday the 17th.
		self.assertEqual(doc.custom_launch_deadline.date(), datetime.date(2026, 8, 17))

	def test_resaving_an_already_won_deal_does_not_stamp(self):
		"""The guard that keeps the legacy backlog out of the gate.

		`stamp_won_date` fills a blank won date on ANY Closed-Won save, so a date
		comparison would have dragged a merely-re-saved legacy deal into the gate.
		This keys on the transition instead.
		"""
		doc = self._doc(before_status="Closed Won")
		handoff.stamp_handoff_gate(doc)
		self.assertIsNone(doc.get("custom_handoff_gate_applies"))
		self.assertIsNone(doc.get("custom_handoff_due_by"))

	def test_non_won_status_is_untouched(self):
		doc = self._doc(status="Open", before_status="Qualification")
		handoff.stamp_handoff_gate(doc)
		self.assertIsNone(doc.get("custom_handoff_gate_applies"))

	def test_existing_deadlines_are_never_overwritten(self):
		existing = datetime.datetime(2026, 1, 1, 9, 0)
		doc = self._doc(custom_handoff_due_by=existing, custom_launch_deadline=existing)
		handoff.stamp_handoff_gate(doc)
		self.assertEqual(doc.custom_handoff_due_by, existing)
		self.assertEqual(doc.custom_launch_deadline, existing)


# ---------------------------------------------------------------------------
# Attendee resolution
# ---------------------------------------------------------------------------


class TestAttendeeResolution(unittest.TestCase):
	def setUp(self):
		_reset()

	def _configure(self, rows):
		STATE["singles"]["handoff_attendees"] = [
			sys.modules["frappe"]._dict(row) for row in rows
		]

	def test_explicit_email_wins_over_role(self):
		self._configure(
			[
				{"function": "Production", "role": "Production Team", "email": "production@sf.com"},
			]
		)
		STATE["role_holders"]["Production Team"] = ["someone@sf.com"]
		STATE["users"] = {"someone@sf.com": {"enabled": True}}

		resolved = handoff.resolve_attendees(_opportunity())
		self.assertEqual(resolved["Production"], ["production@sf.com"])

	def test_role_holders_used_when_no_email(self):
		self._configure([{"function": "Billing", "role": "Accounts Receivable", "email": None}])
		STATE["role_holders"]["Accounts Receivable"] = ["ar1@sf.com", "ar2@sf.com"]
		STATE["users"] = {"ar1@sf.com": {"enabled": True}, "ar2@sf.com": {"enabled": True}}
		STATE["records"]["Accounts Receivable"] = {"name": "Accounts Receivable"}

		resolved = handoff.resolve_attendees(_opportunity())
		self.assertEqual(sorted(resolved["Billing"]), ["ar1@sf.com", "ar2@sf.com"])

	def test_sales_includes_the_deal_owner(self):
		"""The AE is the one person a distribution list cannot stand in for."""
		self._configure([{"function": "Sales", "role": None, "email": "sales@sf.com"}])
		resolved = handoff.resolve_attendees(_opportunity(opportunity_owner="ae@example.com"))
		self.assertEqual(resolved["Sales"][0], "ae@example.com")
		self.assertIn("sales@sf.com", resolved["Sales"])

	def test_unconfigured_function_falls_back_rather_than_dropping(self):
		"""An empty settings table must not silently produce an empty invite."""
		resolved = handoff.resolve_attendees(_opportunity())
		for function in ("Sales", "Production", "Billing"):
			self.assertTrue(resolved[function], f"{function} resolved to nothing")

	def test_nonexistent_role_is_skipped_not_raised(self):
		"""'Account Executive' is a Select value on the step template, not a Role."""
		self._configure([{"function": "Production", "role": "Account Executive", "email": None}])
		resolved = handoff.resolve_attendees(_opportunity())
		self.assertEqual(resolved["Production"], [])

	def test_duplicates_and_invalid_addresses_are_dropped(self):
		self._configure(
			[
				{"function": "Billing", "role": None, "email": "billing@sf.com"},
				{"function": "Billing", "role": None, "email": "BILLING@sf.com"},
				{"function": "Billing", "role": None, "email": "not-an-address"},
			]
		)
		self.assertEqual(handoff.resolve_attendees(_opportunity())["Billing"], ["billing@sf.com"])


# ---------------------------------------------------------------------------
# Step-4 billing routing and invoice terms
# ---------------------------------------------------------------------------


def _project(name="PROJ-0001", opportunity=None, **fields):
	record = {"name": name, "custom_opportunity": opportunity}
	record.update(fields)
	STATE["records"][name] = record
	STATE["docs"][name] = _StubDoc(name, doctype="Project", **{k: v for k, v in record.items() if k != "name"})
	return name


class TestBillingRouting(unittest.TestCase):
	"""Where the step-4 note goes, and what it says about money.

	The expensive failures here are both silent. A **Billing Email** override
	that quietly replaced the default with nothing would send the hand-off note
	into the void, and nobody finds out until an invoice is a fortnight late; a
	percentage that defaulted to something instead of to silence would have
	Billing invoice a number Sales never agreed.
	"""

	BILLING_COLUMNS = {
		"custom_billing_email",
		"custom_first_invoice_percentage",
		"custom_project_dollar_amount",
		"opportunity_amount",
	}

	def setUp(self):
		_reset()
		STATE["columns"].update(self.BILLING_COLUMNS)

	def _configure(self, rows):
		STATE["singles"]["handoff_attendees"] = [dict(row) for row in rows]

	# -- recipients ---------------------------------------------------------

	def test_blank_field_still_goes_to_the_configured_billing_route(self):
		"""The field is an override. Blank must behave exactly as before it existed."""
		self._configure([{"function": "Billing", "role": None, "email": "billing@sf.com"}])
		project = _project(opportunity=_opportunity())
		self.assertEqual(handoff.billing_notice_context(project)["recipients"], ["billing@sf.com"])

	def test_unconfigured_site_falls_back_to_the_group_address(self):
		"""An empty settings table must not resolve to an empty recipient list."""
		project = _project(opportunity=_opportunity())
		self.assertEqual(
			handoff.billing_notice_context(project)["recipients"],
			["billing@sapphirefountains.com"],
		)

	def test_project_billing_email_overrides_the_configured_route(self):
		self._configure([{"function": "Billing", "role": None, "email": "billing@sf.com"}])
		project = _project(opportunity=_opportunity(), custom_billing_email="ap@customer.com")
		self.assertEqual(handoff.billing_notice_context(project)["recipients"], ["ap@customer.com"])

	def test_opportunity_billing_email_is_used_when_the_project_has_none(self):
		"""The desk mapper route: ERPNext's own mapping cannot carry the field."""
		self._configure([{"function": "Billing", "role": None, "email": "billing@sf.com"}])
		opportunity = _opportunity(custom_billing_email="ap@customer.com")
		project = _project(opportunity=opportunity, custom_billing_email="")
		self.assertEqual(handoff.billing_notice_context(project)["recipients"], ["ap@customer.com"])

	def test_several_addresses_in_one_field(self):
		project = _project(opportunity=_opportunity(), custom_billing_email="ap@customer.com; billing@sf.com")
		self.assertEqual(
			handoff.billing_notice_context(project)["recipients"],
			["ap@customer.com", "billing@sf.com"],
		)

	def test_a_typod_override_falls_back_rather_than_sending_nowhere(self):
		"""Worse than no override: an address nobody reads, and no bounce."""
		self._configure([{"function": "Billing", "role": None, "email": "billing@sf.com"}])
		project = _project(opportunity=_opportunity(), custom_billing_email="ap-at-customer.com")
		self.assertEqual(handoff.billing_notice_context(project)["recipients"], ["billing@sf.com"])

	# -- invoice terms ------------------------------------------------------

	def test_percentage_and_amount_come_from_the_project(self):
		project = _project(
			opportunity=_opportunity(),
			custom_first_invoice_percentage=50,
			custom_project_dollar_amount=120000,
		)
		context = handoff.billing_notice_context(project)
		self.assertEqual(context["first_invoice_percentage"], 50)
		self.assertEqual(context["project_amount"], 120000)
		self.assertEqual(context["first_invoice_amount"], 60000)

	def test_terms_fall_back_to_the_opportunity_field_by_field(self):
		"""Not whole-record: a project may carry the amount but not the percentage.

		The unset percentage is spelled ``0`` rather than omitted because that is
		what a real site returns — frappe declares Percent NOT NULL DEFAULT 0, so
		a fallback keyed on ``is None`` would pass here and never fire in
		production.
		"""
		opportunity = _opportunity(custom_first_invoice_percentage=25, opportunity_amount=1000)
		project = _project(
			opportunity=opportunity,
			custom_first_invoice_percentage=0,
			custom_billing_email="",
			custom_project_dollar_amount=80000,
		)
		context = handoff.billing_notice_context(project)
		self.assertEqual(context["first_invoice_percentage"], 25)
		self.assertEqual(context["project_amount"], 80000)
		self.assertEqual(context["first_invoice_amount"], 20000)

	def test_unset_percentage_is_none_not_zero(self):
		"""The note asks Billing to confirm with Sales; 0% would be a false answer."""
		project = _project(
			opportunity=_opportunity(),
			custom_first_invoice_percentage=0,
			custom_project_dollar_amount=120000,
		)
		context = handoff.billing_notice_context(project)
		self.assertIsNone(context["first_invoice_percentage"])
		self.assertIsNone(context["first_invoice_amount"])

	def test_no_amount_means_no_derived_figure(self):
		"""A percentage of an unknown amount is worse than making them ask."""
		project = _project(opportunity=_opportunity(), custom_first_invoice_percentage=50)
		context = handoff.billing_notice_context(project)
		self.assertEqual(context["first_invoice_percentage"], 50)
		self.assertIsNone(context["first_invoice_amount"])

	def test_missing_columns_read_as_unconfigured_rather_than_raising(self):
		"""doc_events and the desk mapper both run before the fields exist."""
		STATE["columns"].difference_update(self.BILLING_COLUMNS)
		project = _project(opportunity=_opportunity(), custom_billing_email="ap@customer.com")
		context = handoff.billing_notice_context(project)
		self.assertIsNone(context["first_invoice_percentage"])
		self.assertEqual(context["recipients"], ["billing@sapphirefountains.com"])

	def test_terms_resolve_for_an_opportunity_with_no_project_yet(self):
		"""The desk mapper reads them off the deal, before any Project exists."""
		opportunity = _opportunity(custom_billing_email="ap@customer.com", custom_first_invoice_percentage=40)
		terms = handoff.billing_terms(opportunity=opportunity)
		self.assertEqual(terms["billing_email"], "ap@customer.com")
		self.assertEqual(terms["first_invoice_percentage"], 40)


# ---------------------------------------------------------------------------
# Escalation policy
# ---------------------------------------------------------------------------


class TestEscalationPolicy(unittest.TestCase):
	"""Which steps escalate at all — read straight off the module constants.

	Asserted here rather than by running the sweep because the sweep is a query
	over real tables; the policy decision is what regresses silently.
	"""

	def test_payment_step_is_excluded_and_invoice_step_is_not(self):
		import erpnext_enhancements.process_steps as process_steps

		self.assertEqual(process_steps.PAYMENT_STEP_NUMBER, 5)
		self.assertNotEqual(process_steps.PAYMENT_STEP_NUMBER, 4)

	def test_non_blocking_steps_is_exactly_payment(self):
		"""2026-08-07: payment is the only step exempted from *blocking* later
		ones (steps 6/7 no longer wait on it) -- step 4 must still gate normally."""
		import erpnext_enhancements.process_steps as process_steps

		self.assertEqual(process_steps.NON_BLOCKING_STEP_NUMBERS, frozenset({5}))

	def test_handoff_step_number_matches_the_template_sequence(self):
		import erpnext_enhancements.process_steps as process_steps

		self.assertEqual(process_steps.HANDOFF_STEP_NUMBER, 2)

	def test_skipped_prefix_is_stable(self):
		"""The report and the progress bar both key on this string."""
		import erpnext_enhancements.process_steps as process_steps

		self.assertEqual(process_steps.SKIPPED_PREFIX, "[SKIPPED]")


if __name__ == "__main__":
	unittest.main()
