"""Bench-free tests for lead triage and the speed-to-lead SLA (TASK-2026-01473).

Two halves:

* **Pure** -- ``utils/business_hours`` (the working-time arithmetic every deadline
  rests on), the rotation, and the remind/escalate decision. No stub.
* **Wired** -- ``crm_enhancements/lead_triage`` against a stubbed frappe: who gets
  the Lead, that the deadline is only stamped while the SLA is on, that the ToDo and
  the notification are made once and never say "Guest assigned", that only a real
  outbound response stamps the Lead, and that the sweep chases each Lead once per
  stage. Also that attribution still reaches the Opportunity (``propagate_to_opportunity``),
  which the qualification path in docs/lead-triage-runbook.md depends on.

The stub is installed in ``setUpModule`` and removed in ``tearDownModule``.

Run: python -m unittest erpnext_enhancements.tests.test_lead_triage -v
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import business_hours

D = datetime.datetime
T8, T17 = datetime.time(8, 0), datetime.time(17, 0)

STUBBED = ("frappe", "frappe.utils", "erpnext_enhancements.email_style")
OURS = (
	"erpnext_enhancements.crm_enhancements.lead_triage",
	"erpnext_enhancements.crm_enhancements.attribution",
	"erpnext_enhancements.patches.backfill_lead_triage_settings_defaults",
	"erpnext_enhancements.utils.error_throttle",
)
STATE = {}
lead_triage = None
attribution = None
_saved = {}


# ---------------------------------------------------------------- pure: hours


class BusinessHoursTests(unittest.TestCase):
	def add(self, start, minutes, is_holiday=None):
		return business_hours.add_business_minutes(start, minutes, T8, T17, is_holiday or (lambda d: False))

	def test_inside_the_day(self):
		self.assertEqual(self.add(D(2026, 9, 22, 9, 15), 60), D(2026, 9, 22, 10, 15))

	def test_friday_afternoon_rolls_to_monday(self):
		# 2026-09-25 is a Friday. 30 minutes today, 30 on Monday.
		self.assertEqual(self.add(D(2026, 9, 25, 16, 30), 60), D(2026, 9, 28, 8, 30))

	def test_after_hours_counts_from_next_opening(self):
		self.assertEqual(self.add(D(2026, 9, 22, 19, 0), 60), D(2026, 9, 23, 9, 0))
		self.assertEqual(self.add(D(2026, 9, 22, 6, 0), 60), D(2026, 9, 22, 9, 0))

	def test_weekend_arrival(self):
		self.assertEqual(self.add(D(2026, 9, 26, 11, 0), 60), D(2026, 9, 28, 9, 0))

	def test_holiday_is_skipped(self):
		monday = datetime.date(2026, 9, 28)
		self.assertEqual(self.add(D(2026, 9, 25, 16, 30), 60, lambda d: d == monday), D(2026, 9, 29, 8, 30))

	def test_deadline_on_closing_time_stays_there(self):
		self.assertEqual(self.add(D(2026, 9, 22, 16, 0), 60), D(2026, 9, 22, 17, 0))

	def test_arriving_at_close_is_next_morning(self):
		self.assertEqual(self.add(D(2026, 9, 22, 17, 0), 1), D(2026, 9, 23, 8, 1))

	def test_four_hours_spans_days(self):
		self.assertEqual(self.add(D(2026, 9, 22, 15, 0), 240), D(2026, 9, 23, 10, 0))

	def test_zero_minutes_is_unchanged(self):
		start = D(2026, 9, 26, 3, 0)
		self.assertEqual(self.add(start, 0), start)

	def test_a_day_with_no_length_raises(self):
		with self.assertRaises(ValueError):
			business_hours.add_business_minutes(D(2026, 9, 22, 9), 60, T17, T8)

	def test_an_all_holiday_calendar_raises_rather_than_spinning(self):
		with self.assertRaises(ValueError):
			self.add(D(2026, 9, 22, 9), 60, lambda d: True)


# ---------------------------------------------------------------- stub


class FakeDoc(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value

	def insert(self, ignore_permissions=False):
		STATE["inserted"].append(dict(self))
		return self


class FakeSettings(dict):
	def get(self, key, default=None):
		return dict.get(self, key, default)


def _install_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe.flags = types.SimpleNamespace(
		in_migrate=False,
		in_install=False,
		in_patch=False,
		in_import=False,
		in_test=False,
		in_setup_wizard=False,
	)
	frappe.get_cached_doc = lambda doctype: STATE["settings"]

	def get_all(doctype, filters=None, pluck=None, **_kw):
		if doctype == "User":
			wanted = set(filters["name"][1])
			return [u for u in sorted(wanted) if u in STATE["enabled_users"]]
		if doctype == "Has Role":
			return [u for u, roles in STATE["roles"].items() if filters["role"] in roles]
		return []

	frappe.get_all = get_all
	frappe.get_doc = lambda fields: FakeDoc(fields)
	frappe.log_error = lambda *args, **kwargs: STATE["errors"].append(args)
	frappe.get_traceback = lambda: "traceback"
	frappe.sendmail = lambda **kwargs: STATE["emails"].append(kwargs)
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: None)
	frappe.get_cached_value = lambda *args: None

	def get_value(doctype, name, field, *args, **kwargs):
		return STATE["lead_values"].get((name, field))

	def set_value(doctype, name, field, value, update_modified=True):
		STATE["set_values"].append((doctype, name, field, value))
		STATE["lead_values"][(name, field)] = value

	def set_default(key, value):
		STATE["defaults"][key] = value

	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: True,
		get_value=get_value,
		set_value=set_value,
		exists=lambda doctype, filters=None: any(
			d.get("doctype") == "ToDo" and d.get("reference_name") == filters.get("reference_name")
			for d in STATE["inserted"]
		),
		get_default=lambda key: STATE["defaults"].get(key),
		set_default=set_default,
		sql=lambda *args, **kwargs: STATE["sweep_rows"],
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)

	def get_datetime(value):
		return value if isinstance(value, D) else D.fromisoformat(str(value))

	def get_time(value):
		if isinstance(value, datetime.time):
			return value
		return datetime.time.fromisoformat(str(value))

	utils.get_datetime = get_datetime
	utils.get_time = get_time
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example/app/lead/{name}"
	utils.now_datetime = lambda: STATE["now"]
	frappe.utils = utils

	email_style = types.ModuleType("erpnext_enhancements.email_style")
	email_style.prose = lambda text: text
	email_style.wrap = lambda body, **kwargs: body

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["erpnext_enhancements.email_style"] = email_style


def setUpModule():
	global lead_triage, attribution
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stub()
	from erpnext_enhancements.crm_enhancements import attribution as attr_module
	from erpnext_enhancements.crm_enhancements import lead_triage as module

	lead_triage = module
	attribution = attr_module
	lead_triage._holiday_checker = lambda: (lambda d: False)


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


PEOPLE = {
	"ann@example.com": {"Sales Team", "Sales Manager"},
	"bob@example.com": {"Sales Team"},
	"cat@example.com": {"Sales Team"},
	"dan@example.com": {"Sales Manager"},  # a manager outside the sales team
	"eve@example.com": {"Sales User"},  # the role nearly everybody holds
	"Administrator": {"Sales Team", "Sales Manager"},
	# On prod the Triton service account holds Sales Team AND Sales Manager, and is a
	# Google Group rather than a person. It must never own a Lead or be escalated to.
	"triton@sapphirefountains.com": {"Sales Team", "Sales Manager"},
}


def _reset(**settings):
	STATE.clear()
	STATE.update(
		{
			"settings": FakeSettings(settings),
			"enabled_users": {
				"ann@example.com",
				"bob@example.com",
				"cat@example.com",
				"dan@example.com",
				"eve@example.com",
				"triton@sapphirefountains.com",
			},
			"roles": PEOPLE,
			"defaults": {},
			"inserted": [],
			"errors": [],
			"emails": [],
			"set_values": [],
			"lead_values": {},
			"sweep_rows": [],
			"now": D(2026, 9, 22, 10, 0),  # a Tuesday
		}
	)


# ---------------------------------------------------------------- pure: decisions


class DecisionTests(unittest.TestCase):
	def test_rotation(self):
		pool = {"cat@example.com", "ann@example.com", "bob@example.com"}
		self.assertEqual(lead_triage.next_in_rotation(pool, None), "ann@example.com")
		self.assertEqual(lead_triage.next_in_rotation(pool, "ann@example.com"), "bob@example.com")
		self.assertEqual(lead_triage.next_in_rotation(pool, "cat@example.com"), "ann@example.com")
		self.assertEqual(lead_triage.next_in_rotation(pool, "ben@example.com"), "bob@example.com")
		self.assertIsNone(lead_triage.next_in_rotation(set(), "ann@example.com"))

	def test_sla_action(self):
		due, esc = D(2026, 9, 22, 11), D(2026, 9, 22, 14)
		act = lead_triage.sla_action
		self.assertIsNone(act(D(2026, 9, 22, 10, 59), due, esc, None))
		self.assertEqual(act(D(2026, 9, 22, 11), due, esc, None), "remind")
		self.assertIsNone(act(D(2026, 9, 22, 12), due, esc, "Reminded"), "reminded once, not every sweep")
		self.assertEqual(act(D(2026, 9, 22, 14), due, esc, "Reminded"), "escalate")
		self.assertEqual(
			act(D(2026, 9, 22, 15), due, esc, None),
			"escalate",
			"a backlog escalates once, skipping the reminder",
		)
		self.assertIsNone(act(D(2026, 9, 23), due, esc, "Escalated"))
		self.assertIsNone(act(D(2026, 9, 23), None, esc, None), "no deadline, no chase")

	def test_what_counts_as_a_response(self):
		base = {
			"reference_doctype": "Lead",
			"reference_name": "L-1",
			"sent_or_received": "Sent",
			"communication_type": "Communication",
		}
		self.assertTrue(lead_triage.is_response(base))
		self.assertFalse(lead_triage.is_response(dict(base, sent_or_received="Received")))
		self.assertFalse(lead_triage.is_response(dict(base, communication_type="Automated Message")))
		self.assertFalse(lead_triage.is_response(dict(base, reference_doctype="Opportunity")))
		self.assertFalse(lead_triage.is_response(dict(base, reference_name=None)))

	def test_config_falls_back_on_blanks_and_nonsense(self):
		_reset()
		cfg = lead_triage.sla_config(FakeSettings())
		self.assertEqual((cfg["response_minutes"], cfg["escalation_minutes"]), (60, 240))
		self.assertEqual((cfg["day_start"], cfg["day_end"]), (T8, T17))
		self.assertFalse(cfg["enabled"])
		backwards = lead_triage.sla_config(
			FakeSettings(
				lead_sla_business_start="18:00:00",
				lead_sla_business_end="09:00:00",
				lead_sla_response_minutes=90,
				lead_sla_escalation_minutes=30,
			)
		)
		self.assertEqual((backwards["day_start"], backwards["day_end"]), (T8, T17))
		self.assertEqual(backwards["escalation_minutes"], 90, "escalation is never earlier than the reminder")

	def test_one_response_rule_shared_with_the_dashboard(self):
		source = (REPO_ROOT / "erpnext_enhancements" / "api" / "sales_dashboard.py").read_text(
			encoding="utf-8"
		)
		self.assertIn("RESPONSE_EXISTS_SQL", source)
		self.assertNotIn(
			"c.sent_or_received = 'Sent'", source, "the dashboard must not keep its own copy of the rule"
		)


# ---------------------------------------------------------------- wired


class OwnerTests(unittest.TestCase):
	def test_named_owner_wins(self):
		_reset(web_lead_default_owner="cat@example.com")
		self.assertEqual(lead_triage.pick_owner(), ("cat@example.com", "default"))

	def test_disabled_named_owner_falls_back_to_the_rotation(self):
		_reset(web_lead_default_owner="gone@example.com")
		self.assertEqual(lead_triage.pick_owner(), ("ann@example.com", "rotation"))
		self.assertEqual(lead_triage.pick_owner(), ("bob@example.com", "rotation"))
		self.assertEqual(lead_triage.pick_owner(), ("cat@example.com", "rotation"))
		self.assertEqual(lead_triage.pick_owner(), ("ann@example.com", "rotation"))

	def test_rotation_is_the_sales_team_not_everybody(self):
		_reset()
		picked = {lead_triage.pick_owner()[0] for _ in range(6)}
		self.assertEqual(picked, {"ann@example.com", "bob@example.com", "cat@example.com"})

	def test_nobody_qualifies(self):
		_reset(lead_triage_role="Nonexistent Role")
		self.assertEqual(lead_triage.pick_owner(), (None, None))

	def test_escalation_needs_a_named_person(self):
		# No role fallback: on prod, Sales Manager AND Sales Team is the whole team.
		_reset()
		self.assertEqual(lead_triage.escalation_recipients(), [])
		_reset(lead_sla_escalate_to="dan@example.com")
		self.assertEqual(lead_triage.escalation_recipients(), ["dan@example.com"])
		_reset(lead_sla_escalate_to="gone@example.com")
		self.assertEqual(lead_triage.escalation_recipients(), [], "a disabled user is nobody")
		_reset(lead_sla_escalate_to="triton@sapphirefountains.com")
		self.assertEqual(lead_triage.escalation_recipients(), [], "a service account is nobody")

	def test_the_sla_cannot_be_enabled_without_one(self):
		_reset()
		self.assertIsNone(lead_triage.sla_settings_error(FakeSettings()), "off never blocks a save")
		self.assertIsNotNone(lead_triage.sla_settings_error(FakeSettings(lead_sla_enabled=1)))
		self.assertIsNone(
			lead_triage.sla_settings_error(
				FakeSettings(lead_sla_enabled=1, lead_sla_escalate_to="dan@example.com")
			)
		)

	def test_the_controller_calls_the_rule(self):
		source = (
			REPO_ROOT
			/ "erpnext_enhancements"
			/ "enhancements_core"
			/ "doctype"
			/ "erpnext_enhancements_settings"
			/ "erpnext_enhancements_settings.py"
		).read_text(encoding="utf-8")
		self.assertIn("self.validate_lead_sla()", source)
		self.assertIn("sla_settings_error(self)", source)


class InboundTests(unittest.TestCase):
	def _lead(self):
		return FakeDoc(
			doctype="Lead",
			name="CRM-LEAD-1",
			lead_name="Jane Doe",
			company_name="Doe Co",
			lead_owner=None,
			custom_first_response_due=None,
		)

	def test_no_deadline_while_the_sla_is_off(self):
		_reset(web_lead_default_owner="cat@example.com")
		lead = self._lead()
		lead_triage.prepare_inbound_lead(lead)
		self.assertEqual(lead.lead_owner, "cat@example.com")
		self.assertIsNone(lead.custom_first_response_due)

	def test_deadline_in_working_time(self):
		_reset(web_lead_default_owner="cat@example.com", lead_sla_enabled=1)
		STATE["now"] = D(2026, 9, 25, 16, 30)  # Friday
		lead = self._lead()
		lead_triage.prepare_inbound_lead(lead)
		self.assertEqual(lead.custom_first_response_due, D(2026, 9, 28, 8, 30))

	def test_an_existing_owner_is_kept(self):
		_reset(web_lead_default_owner="cat@example.com")
		lead = self._lead()
		lead.lead_owner = "bob@example.com"
		lead_triage.prepare_inbound_lead(lead)
		self.assertEqual(lead.lead_owner, "bob@example.com")

	def test_assignment_is_a_todo_and_a_notification_never_from_guest(self):
		_reset()
		lead = self._lead()
		lead.lead_owner = "cat@example.com"
		lead.custom_first_response_due = D(2026, 9, 22, 11, 0)
		lead_triage.assign_inbound_lead(lead)
		lead_triage.assign_inbound_lead(lead)
		todos = [d for d in STATE["inserted"] if d["doctype"] == "ToDo"]
		self.assertEqual(len(todos), 1, "assigning twice must not stack ToDos")
		self.assertEqual(todos[0]["allocated_to"], "cat@example.com")
		self.assertEqual(todos[0]["assigned_by"], "Administrator")
		self.assertEqual(todos[0]["date"], datetime.date(2026, 9, 22))
		notes = [d for d in STATE["inserted"] if d["doctype"] == "Notification Log"]
		self.assertEqual(notes[0]["type"], "Assignment")
		self.assertIn("Tue Sep 22, 11:00 AM", notes[0]["subject"])
		self.assertNotIn("Guest", notes[0]["subject"])
		self.assertEqual(STATE["emails"], [], "Frappe emails Assignment notifications itself; no second copy")

	def test_no_owner_no_assignment(self):
		_reset()
		lead = self._lead()
		self.assertIsNone(lead_triage.assign_inbound_lead(lead))
		self.assertEqual(STATE["inserted"], [])


class ResponseStampTests(unittest.TestCase):
	def comm(self, **overrides):
		base = {
			"reference_doctype": "Lead",
			"reference_name": "CRM-LEAD-1",
			"sent_or_received": "Sent",
			"communication_type": "Communication",
			"communication_date": D(2026, 9, 22, 10, 20),
		}
		base.update(overrides)
		return FakeDoc(base)

	def test_first_response_is_stamped_once(self):
		_reset()
		lead_triage.stamp_first_response(self.comm())
		lead_triage.stamp_first_response(self.comm(communication_date=D(2026, 9, 22, 12, 0)))
		self.assertEqual(
			STATE["set_values"], [("Lead", "CRM-LEAD-1", "custom_first_response_at", D(2026, 9, 22, 10, 20))]
		)

	def test_inbound_and_automated_do_not_count(self):
		_reset()
		lead_triage.stamp_first_response(self.comm(sent_or_received="Received"))
		lead_triage.stamp_first_response(self.comm(communication_type="Automated Message"))
		lead_triage.stamp_first_response(self.comm(reference_doctype="Opportunity"))
		self.assertEqual(STATE["set_values"], [])


class SweepTests(unittest.TestCase):
	def row(self, **overrides):
		base = {
			"name": "CRM-LEAD-1",
			"lead_name": "Jane Doe",
			"company_name": None,
			"lead_owner": "cat@example.com",
			"creation": D(2026, 9, 22, 9, 0),
			"due": D(2026, 9, 22, 10, 0),
			"alert": None,
		}
		base.update(overrides)
		return FakeDoc(base)

	def test_off_means_nothing(self):
		_reset()
		STATE["sweep_rows"] = [self.row()]
		self.assertIsNone(lead_triage.sweep_first_response_sla())
		self.assertEqual(STATE["inserted"], [])

	def test_reminder_goes_to_the_owner(self):
		_reset(lead_sla_enabled=1)
		STATE["now"] = D(2026, 9, 22, 10, 5)
		STATE["sweep_rows"] = [self.row()]
		self.assertEqual(lead_triage.sweep_first_response_sla(), {"remind": 1, "escalate": 0})
		self.assertEqual([e["recipients"] for e in STATE["emails"]], [["cat@example.com"]])
		self.assertIn(("Lead", "CRM-LEAD-1", "custom_sla_alert", "Reminded"), STATE["set_values"])

	def test_escalation_reaches_the_manager_and_the_owner(self):
		_reset(lead_sla_enabled=1, lead_sla_escalate_to="ann@example.com")
		STATE["now"] = D(2026, 9, 22, 13, 0)  # 240 working minutes after 09:00
		STATE["sweep_rows"] = [self.row(alert="Reminded")]
		self.assertEqual(lead_triage.sweep_first_response_sla(), {"remind": 0, "escalate": 1})
		self.assertEqual(
			sorted(e["recipients"][0] for e in STATE["emails"]), ["ann@example.com", "cat@example.com"]
		)
		self.assertIn(("Lead", "CRM-LEAD-1", "custom_sla_alert", "Escalated"), STATE["set_values"])

	def test_an_ownerless_lead_is_reminded_to_the_escalation_list(self):
		_reset(lead_sla_enabled=1, lead_sla_escalate_to="ann@example.com")
		STATE["now"] = D(2026, 9, 22, 10, 5)
		STATE["sweep_rows"] = [self.row(lead_owner=None)]
		lead_triage.sweep_first_response_sla()
		self.assertEqual([e["recipients"] for e in STATE["emails"]], [["ann@example.com"]])

	def test_escalation_with_nobody_named_tells_the_owner_and_the_error_log(self):
		_reset(lead_sla_enabled=1)
		STATE["now"] = D(2026, 9, 22, 13, 0)
		STATE["sweep_rows"] = [self.row(alert="Reminded")]
		lead_triage.sweep_first_response_sla()
		self.assertEqual([e["recipients"] for e in STATE["emails"]], [["cat@example.com"]])
		self.assertTrue(STATE["errors"], "a missing escalation recipient must be said out loud")

	def test_not_yet_due_is_left_alone(self):
		_reset(lead_sla_enabled=1)
		STATE["now"] = D(2026, 9, 22, 9, 30)
		STATE["sweep_rows"] = [self.row()]
		self.assertEqual(lead_triage.sweep_first_response_sla(), {"remind": 0, "escalate": 0})
		self.assertEqual(STATE["emails"], [])


class SchemaTests(unittest.TestCase):
	"""The fields the code writes exist, and the backfill covers what the Settings declare."""

	APP = REPO_ROOT / "erpnext_enhancements"

	def _json(self, *parts):
		import json

		return json.loads((self.APP.joinpath(*parts)).read_text(encoding="utf-8"))

	def test_lead_fields_are_read_only_fixtures(self):
		fields = {r["fieldname"]: r for r in self._json("fixtures", "custom_field.json") if r["dt"] == "Lead"}
		for name in ("custom_first_response_due", "custom_first_response_at", "custom_sla_alert"):
			self.assertIn(name, fields)
			self.assertTrue(fields[name].get("read_only"), f"{name} is stamped by code, not typed")
		options = [o for o in fields["custom_sla_alert"]["options"].split("\n") if o]
		self.assertEqual(options, [lead_triage.ALERT_REMINDED, lead_triage.ALERT_ESCALATED])

	def test_settings_and_backfill_agree(self):
		from erpnext_enhancements.patches import backfill_lead_triage_settings_defaults as patch

		doc = self._json(
			"enhancements_core",
			"doctype",
			"erpnext_enhancements_settings",
			"erpnext_enhancements_settings.json",
		)
		fields = {f["fieldname"]: f for f in doc["fields"]}
		with_defaults = {
			n
			for n, f in fields.items()
			if n.startswith(("lead_triage", "lead_sla")) and f.get("default") not in (None, "")
		}
		self.assertEqual(
			set(patch.FIELDS), with_defaults, "every new default must be backfilled, and nothing else"
		)
		self.assertEqual(fields["lead_triage_role"]["default"], lead_triage.DEFAULT_TRIAGE_ROLE)
		self.assertEqual(
			int(fields["lead_sla_response_minutes"]["default"]), lead_triage.DEFAULT_RESPONSE_MINUTES
		)
		self.assertEqual(
			int(fields["lead_sla_escalation_minutes"]["default"]), lead_triage.DEFAULT_ESCALATION_MINUTES
		)
		self.assertEqual(fields["lead_sla_enabled"]["default"], "0", "the SLA ships dormant")
		self.assertIn(
			"erpnext_enhancements.patches.backfill_lead_triage_settings_defaults",
			(self.APP / "patches.txt").read_text(encoding="utf-8"),
		)

	def test_hooks(self):
		hooks = (self.APP / "hooks.py").read_text(encoding="utf-8")
		for path in ("stamp_first_response", "sweep_first_response_sla", "backfill_first_responses"):
			self.assertIn(f'"erpnext_enhancements.crm_enhancements.lead_triage.{path}"', hooks)


class QualificationPathTests(unittest.TestCase):
	"""Lead -> Opportunity: the attribution a web Lead carries, utm_id included,
	must be on the Opportunity sales creates from it."""

	def test_attribution_propagates_to_the_opportunity(self):
		_reset()
		lead_row = {
			"custom_utm_source": "google",
			"custom_utm_id": "21456789012",
			"custom_gclid": "Cj0",
			"custom_lead_source": "Advertisement",
		}
		db = sys.modules["frappe"].db
		saved = (db.exists, db.get_value)
		db.exists = lambda doctype, name=None: doctype == "Lead"
		db.get_value = lambda doctype, name, fields, as_dict=False: dict(lead_row)
		opp = FakeDoc(doctype="Opportunity", opportunity_from="Lead", party_name="CRM-LEAD-1")
		for field in attribution.PROPAGATED_FIELDS:
			opp[field] = None
		try:
			attribution.propagate_to_opportunity(opp)
		finally:
			db.exists, db.get_value = saved
		self.assertEqual(opp.custom_utm_id, "21456789012")
		self.assertEqual(opp.custom_gclid, "Cj0")
		self.assertEqual(opp.custom_lead_source, "Advertisement")
		self.assertIsNotNone(opp.custom_attribution_captured_on)

	def test_attribution_survives_lead_to_customer_to_opportunity(self):
		"""The path this site must use (docs/lead-triage-runbook.md): a Lead-party won deal
		never becomes a Project, so the Opportunity is made from the Customer."""
		_reset()
		store = {
			("Lead", "CRM-LEAD-1"): {
				"custom_utm_source": "google",
				"custom_utm_id": "21456789012",
				"custom_lead_source": "Advertisement",
				"custom_attribution_captured_on": "2026-09-22 10:00:00",
			}
		}
		db = sys.modules["frappe"].db
		saved = (db.exists, db.get_value)
		db.exists = lambda doctype, name=None: (doctype, name) in store
		db.get_value = lambda doctype, name, fields, as_dict=False: dict(store[(doctype, name)])
		try:
			customer = FakeDoc(doctype="Customer", name="Jane Doe", lead_name="CRM-LEAD-1")
			for field in attribution.PROPAGATED_FIELDS:
				customer[field] = None
			attribution.propagate_to_customer(customer)
			store[("Customer", "Jane Doe")] = {k: v for k, v in customer.items() if k.startswith("custom_") and v}

			opp = FakeDoc(doctype="Opportunity", opportunity_from="Customer", party_name="Jane Doe")
			for field in attribution.PROPAGATED_FIELDS:
				opp[field] = None
			attribution.propagate_to_opportunity(opp)
		finally:
			db.exists, db.get_value = saved
		self.assertEqual(customer.custom_utm_id, "21456789012")
		self.assertEqual(opp.custom_utm_id, "21456789012", "the ad reaches the deal through the Customer")
		self.assertEqual(opp.custom_lead_source, "Advertisement")
		self.assertEqual(opp.custom_attribution_captured_on, "2026-09-22 10:00:00", "the touch time, not the deal's")


if __name__ == "__main__":
	unittest.main()
