# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free unit tests for the uncertified-dispatch advisory.

Stubs a minimal ``frappe`` (no site/bench) so ``training.compliance`` runs under
plain unittest, in the style of ``test_training_gcs_media.py``. The stub is
installed in ``setUpModule`` — execution time, not import time — so it never fools
the bench-only suites' ``import frappe`` skip-guards.

**The two assertions that matter are the safety ones**, and they are the reason
this file exists at all:

  * the module contains no ``frappe.throw`` — the check fires on Sapphire
	Maintenance Record and Task ``validate``, by which time a truck is usually
	already at a site, and blocking the save means the visit happens with no record
	at all. That is strictly worse than the uncertified assignment being flagged;
  * an exception *anywhere* inside the check is swallowed. A compliance advisory
	that breaks dispatch is a self-inflicted outage, so "the advisory is broken"
	must degrade to "there is no advisory", never to "nobody can save".

The rest pin the semantics that make it worth reading: a live completion silences
the warning, a lapsed one raises it whether or not the nightly sweep has run,
optional courses are never a finding, and a re-save does not add a second comment.

``training.gamification`` is exercised here too, for one property only — that a
board can never mix staff with customers. It shares the stub, and the separation
is a safeguard of exactly the same kind: quiet if it stops working.

Run: python -m unittest erpnext_enhancements.tests.test_training_compliance
"""

import datetime
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

compliance = None
gamification = None

TODAY = "2026-08-01"

STATE = {}


class _Dict(dict):
	"""``frappe._dict``: attribute access, and a miss reads as None rather than
	raising. Several guards in the code under test rely on that exact behaviour
	(``frappe.flags.in_migrate`` on a site that has never set it)."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Doc:
	def __init__(self, doctype, name, **fields):
		self.doctype = doctype
		self.name = name
		for key, value in fields.items():
			setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def add_comment(self, comment_type="Comment", text=None, **kwargs):
		STATE["comments"].append(
			{
				"reference_doctype": self.doctype,
				"reference_name": self.name,
				"comment_type": comment_type,
				"content": text,
			}
		)
		STATE["tables"].setdefault("Comment", []).append(
			_Dict(
				name=f"CMT-{len(STATE['comments'])}",
				reference_doctype=self.doctype,
				reference_name=self.name,
				comment_type=comment_type,
				content=text,
			)
		)


class _NewDoc(_Dict):
	def insert(self, *args, **kwargs):
		STATE["inserted"].append(dict(self))
		return self


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"settings": {
				"training_enabled": 1,
				"warn_on_uncertified_dispatch": 1,
				"notifications_enabled": 1,
				"gamification_enabled": 1,
				"escalation_role": "Training Manager",
			},
			"flags": {},
			"tables": {
				"Training Course": [
					_Dict(name="TRN-CRS-00001", weight="Required", status="Published"),
					_Dict(name="TRN-CRS-00002", weight="Optional", status="Published"),
				],
				"Training Assignment": [],
				"Training Completion": [],
				"Comment": [],
				"Has Role": [],
				"User": [
					_Dict(
						name="tech@example.com", full_name="Tess Tech", email="tech@example.com", enabled=1
					),
					_Dict(name="boss@example.com", full_name="Bo Boss", email="boss@example.com", enabled=1),
				],
				"Employee": [
					_Dict(
						name="HR-EMP-001",
						user_id="tech@example.com",
						status="Active",
						reports_to="HR-EMP-002",
					),
					_Dict(name="HR-EMP-002", user_id="boss@example.com", status="Active", reports_to=None),
				],
				"Training Learner Stat": [],
				"DocType": [_Dict(name="Training Learner Stat")],
			},
			"comments": [],
			"emails": [],
			"enqueued": [],
			"errors": [],
			"inserted": [],
			"msgprints": [],
			"roles": {"tech@example.com": ["Employee"], "boss@example.com": ["Training Manager"]},
			"session_user": "dispatcher@example.com",
			"raise_in": None,
		}
	)


# ------------------------------------------------------------------ stub frappe


def _table(doctype):
	return STATE["tables"].setdefault(doctype, [])


def _like(value, pattern):
	body = str(pattern).strip("%")
	return body in (value or "")


def _matches(row, filters):
	for field, condition in (filters or {}).items():
		value = row.get(field)
		if isinstance(condition, (list, tuple)):
			operator, operand = condition[0], condition[1]
			operator = str(operator).lower()
			if operator == "in":
				ok = value in operand
			elif operator == "not in":
				ok = value not in operand
			elif operator == "!=":
				ok = value != operand
			elif operator == "<":
				ok = value is not None and str(value) < str(operand)
			elif operator == ">":
				ok = value is not None and str(value) > str(operand)
			elif operator == "like":
				ok = _like(value, operand)
			else:
				raise AssertionError(f"stub does not implement operator {operator!r}")
		else:
			ok = value == condition
		if not ok:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, limit=None, **kwargs):
	if STATE.get("raise_in") == "get_all":
		raise RuntimeError("database on fire")

	rows = [r for r in _table(doctype) if _matches(r, filters)]
	if kwargs.get("order_by"):
		# Only the leaderboard orders, and only ever by points desc.
		rows = sorted(rows, key=lambda r: -int(r.get("points") or 0))
	if limit:
		rows = rows[: int(limit)]
	if pluck:
		values = [r.get(pluck) for r in rows]
		return list(dict.fromkeys(values)) if kwargs.get("distinct") else values
	if fields:
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]
	return [_Dict(r) for r in rows]


def _db_get_value(doctype, filters=None, fieldname=None, as_dict=False, **kwargs):
	rows = _table(doctype)
	if isinstance(filters, str):
		matched = [r for r in rows if r.get("name") == filters]
	else:
		matched = [r for r in rows if _matches(r, filters)]
	if not matched:
		return None
	row = matched[0]
	if isinstance(fieldname, (list, tuple)):
		if as_dict:
			return _Dict({f: row.get(f) for f in fieldname})
		return [row.get(f) for f in fieldname]
	value = row.get(fieldname or "name")
	return _Dict({fieldname: value}) if as_dict else value


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.session = _Dict(user="dispatcher@example.com")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_traceback = lambda: "traceback"
	frappe.get_roles = lambda user=None: STATE["roles"].get(user, [])

	def _log_error(*args, **kwargs):
		STATE["errors"].append(kwargs.get("title") or (args[0] if args else ""))

	frappe.log_error = _log_error

	def _msgprint(msg, title=None, indicator=None, **kwargs):
		STATE["msgprints"].append({"msg": msg, "title": title, "indicator": indicator})

	frappe.msgprint = _msgprint

	def _enqueue(method, **kwargs):
		kwargs.pop("queue", None)
		kwargs.pop("enqueue_after_commit", None)
		STATE["enqueued"].append({"method": method, "kwargs": kwargs})

	frappe.enqueue = _enqueue
	frappe.sendmail = lambda **kwargs: STATE["emails"].append(kwargs)

	def _get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			return _NewDoc(doctype)
		existing = [r for r in _table(doctype) if r.get("name") == name]
		if existing:
			return _Doc(doctype, name, **existing[0])
		return _Doc(doctype, name)

	frappe.get_doc = _get_doc
	frappe.new_doc = lambda doctype: _Doc(doctype, None)
	frappe.get_cached_doc = lambda *a, **k: _Dict(STATE["settings"])

	frappe.db = types.SimpleNamespace(
		get_value=_db_get_value,
		get_single_value=lambda doctype, field: STATE["settings"].get(field),
		set_value=lambda *a, **k: None,
		count=lambda doctype, filters=None: len([r for r in _table(doctype) if _matches(r, filters)]),
		exists=lambda doctype, name=None: bool([r for r in _table(doctype) if r.get("name") == name]),
		commit=lambda: None,
	)
	frappe.get_all = _get_all

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	utils.nowdate = lambda: TODAY
	utils.today = lambda: TODAY
	utils.now_datetime = lambda: datetime.datetime(2026, 8, 1, 12, 0, 0)

	def _getdate(value=None):
		if not value:
			return datetime.date(2026, 8, 1)
		if isinstance(value, datetime.datetime):
			return value.date()
		if isinstance(value, datetime.date):
			return value
		return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()

	utils.getdate = _getdate
	utils.add_days = lambda date, days: str(_getdate(date) + datetime.timedelta(days=days))
	utils.formatdate = lambda value, *a, **k: str(value)
	utils.escape_html = lambda value: (
		str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	)
	utils.get_url = lambda path="": f"https://example.test{path}"
	utils.get_url_to_form = lambda doctype, name: f"https://example.test/app/{doctype}/{name}"
	frappe.utils = utils
	frappe.__dict__["_"] = lambda s, *a, **k: s

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		def __init__(self, *args, **kwargs):
			pass

	document.Document = Document
	model.document = document

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


def setUpModule():
	global compliance, gamification
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.training import compliance as compliance_module
	from erpnext_enhancements.training import gamification as gamification_module

	compliance = compliance_module
	gamification = gamification_module


# ------------------------------------------------------------------- fixtures


def _assign(user="tech@example.com", course="TRN-CRS-00001", status="Overdue"):
	_table("Training Assignment").append(
		_Dict(
			name=f"TRN-ASG-{len(_table('Training Assignment')) + 1:05d}",
			user=user,
			course=course,
			course_title="Pump Safety",
			status=status,
		)
	)


def _complete(user="tech@example.com", course="TRN-CRS-00001", status="Valid", expires_on=None):
	_table("Training Completion").append(
		_Dict(
			name=f"TRN-CMP-{len(_table('Training Completion')) + 1:05d}",
			user=user,
			course=course,
			course_title_snapshot="Pump Safety",
			status=status,
			docstatus=1,
			expires_on=expires_on,
			score_percent=90,
			completed_on="2026-07-01 09:00:00",
		)
	)


def _visit(technician="tech@example.com", name="SMR-0001"):
	return _Doc("Sapphire Maintenance Record", name, technician=technician, docstatus=0)


def _task(assignees=("tech@example.com",), name="TASK-0001", status="Open"):
	return _Doc("Task", name, status=status, _assign=json.dumps(list(assignees)))


def _run_enqueued():
	"""Run what the save deferred, the way the worker would after commit."""
	for job in list(STATE["enqueued"]):
		if job["method"].endswith("record_advisory"):
			compliance.record_advisory(**job["kwargs"])


# ------------------------------------------------------- the safety properties


class TestTheAdvisoryNeverBlocksASave(unittest.TestCase):
	"""The single most important property in Phase 4.

	It fires on validate, when a truck is usually already at a site. A hard stop
	does not prevent the uncertified visit — it prevents the record of it.
	"""

	def setUp(self):
		_reset_state()

	def test_the_module_contains_no_throw(self):
		source = Path(compliance.__file__).with_suffix(".py").read_text(encoding="utf-8")
		self.assertNotIn("frappe.throw", source)

	def test_the_module_raises_nothing_of_its_own(self):
		"""``throw`` is the obvious way to block a save; a bare ``raise`` is the
		non-obvious one, and it would block it just as hard."""
		source = Path(compliance.__file__).with_suffix(".py").read_text(encoding="utf-8")
		offenders = [line.strip() for line in source.splitlines() if line.strip().startswith("raise ")]
		self.assertEqual(offenders, [])

	def test_an_exploding_query_does_not_propagate(self):
		STATE["raise_in"] = "get_all"
		self.assertIsNone(compliance.warn_uncertified_technician(_visit()))
		self.assertTrue(STATE["errors"], "the failure must still be logged, just not raised")

	def test_an_exploding_helper_does_not_propagate(self):
		original = compliance._uncertified
		compliance._uncertified = lambda *a, **k: 1 / 0
		try:
			_assign()
			self.assertIsNone(compliance.warn_uncertified_technician(_visit()))
			self.assertIsNone(compliance.warn_uncertified_assignee(_task()))
		finally:
			compliance._uncertified = original
		self.assertTrue(STATE["errors"])

	def test_a_failure_while_recording_does_not_propagate(self):
		STATE["raise_in"] = "get_all"
		self.assertIsNone(
			compliance.record_advisory(
				doctype="Sapphire Maintenance Record",
				name="SMR-0001",
				findings=[{"user": "tech@example.com", "course": "TRN-CRS-00001"}],
				fingerprint="abc123",
			)
		)

	def test_even_a_broken_error_log_does_not_propagate(self):
		"""log_error writes a document, and inside an unhappy transaction that write
		can fail too. Reporting the failure must not become the failure."""

		def _broken_log(*args, **kwargs):
			raise RuntimeError("error log is down too")

		frappe = sys.modules["frappe"]
		original = frappe.log_error
		frappe.log_error = _broken_log
		STATE["raise_in"] = "get_all"
		try:
			self.assertIsNone(compliance.warn_uncertified_technician(_visit()))
		finally:
			frappe.log_error = original


class TestTheSettingsGate(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_assign()

	def test_silent_when_the_switch_is_off(self):
		STATE["settings"]["warn_on_uncertified_dispatch"] = 0
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(STATE["msgprints"], [])
		self.assertEqual(STATE["enqueued"], [])

	def test_silent_when_the_module_master_switch_is_off(self):
		STATE["settings"]["training_enabled"] = 0
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(STATE["msgprints"], [])

	def test_silent_during_migrate(self):
		"""doc_events fire during ERPNext's own bootstrap, before this module's
		settings or courses exist."""
		sys.modules["frappe"].flags["in_migrate"] = True
		try:
			compliance.warn_uncertified_technician(_visit())
		finally:
			sys.modules["frappe"].flags["in_migrate"] = False
		self.assertEqual(STATE["msgprints"], [])


# ------------------------------------------------------------ what it warns about


class TestWhatCountsAsUncertified(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_an_overdue_required_course_warns_in_orange(self):
		_assign(status="Overdue")
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(len(STATE["msgprints"]), 1)
		self.assertEqual(STATE["msgprints"][0]["indicator"], "orange")
		self.assertIn("Tess Tech", STATE["msgprints"][0]["msg"])

	def test_an_optional_course_is_never_a_finding(self):
		_assign(course="TRN-CRS-00002", status="Overdue")
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(STATE["msgprints"], [])

	def test_a_live_completion_silences_an_open_recert_assignment(self):
		"""Recertification is assigned before the current certificate lapses. Warning
		then would train everyone to ignore the box."""
		_assign(status="Not Started")
		_complete(expires_on="2026-12-31")
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(STATE["msgprints"], [])

	def test_a_lapsed_completion_warns_even_before_the_nightly_sweep(self):
		"""Still ``Valid`` in the database because the scheduler has not restatused
		it yet — the advisory is read at dispatch time and cannot wait for 3am."""
		_complete(status="Valid", expires_on="2026-07-01")
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(len(STATE["msgprints"]), 1)
		self.assertIn("lapsed", STATE["msgprints"][0]["msg"].lower())

	def test_a_revoked_completion_warns(self):
		_complete(status="Revoked")
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(len(STATE["msgprints"]), 1)

	def test_nothing_outstanding_says_nothing(self):
		_complete()
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(STATE["msgprints"], [])
		self.assertEqual(STATE["enqueued"], [])

	def test_a_record_with_no_technician_says_nothing(self):
		_assign()
		compliance.warn_uncertified_technician(_visit(technician=None))
		self.assertEqual(STATE["msgprints"], [])

	def test_a_cancelled_visit_is_not_warned_about(self):
		_assign()
		doc = _visit()
		doc.docstatus = 2
		compliance.warn_uncertified_technician(doc)
		self.assertEqual(STATE["msgprints"], [])


class TestTaskAssignees(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_assign()

	def test_assignees_come_from_the_assign_json(self):
		compliance.warn_uncertified_assignee(_task())
		self.assertEqual(len(STATE["msgprints"]), 1)

	def test_malformed_assign_reads_as_nobody(self):
		"""``_assign`` is framework-maintained JSON. Garbage in it must mean "no
		assignees", never a traceback on somebody's task save."""
		doc = _task()
		doc._assign = "[not json"
		compliance.warn_uncertified_assignee(doc)
		self.assertEqual(STATE["msgprints"], [])

	def test_a_closed_task_is_not_warned_about(self):
		compliance.warn_uncertified_assignee(_task(status="Completed"))
		self.assertEqual(STATE["msgprints"], [])


# --------------------------------------------------------- the durable half


class TestTheRecordedWarning(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_assign()

	def test_the_comment_is_deferred_until_after_commit(self):
		"""At validate the document may not exist yet, so a Comment pointing at it
		would fail its own link validation."""
		compliance.warn_uncertified_technician(_visit())
		self.assertEqual(len(STATE["enqueued"]), 1)
		self.assertEqual(STATE["comments"], [])
		_run_enqueued()
		self.assertEqual(len(STATE["comments"]), 1)
		self.assertIn("Pump Safety", STATE["comments"][0]["content"])

	def test_resaving_does_not_add_a_second_comment(self):
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		self.assertEqual(len(STATE["comments"]), 1)

	def test_a_new_finding_does_get_its_own_comment(self):
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		STATE["tables"]["Training Course"].append(
			_Dict(name="TRN-CRS-00003", weight="Required", status="Published")
		)
		_assign(course="TRN-CRS-00003")
		STATE["enqueued"].clear()
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		self.assertEqual(len(STATE["comments"]), 2)

	def test_the_fingerprint_ignores_ordering(self):
		one = [
			{"user": "a@example.com", "course": "C1"},
			{"user": "b@example.com", "course": "C2"},
		]
		self.assertEqual(compliance._fingerprint(one), compliance._fingerprint(list(reversed(one))))

	def test_the_supervisor_hears_and_the_learner_does_not(self):
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		recipients = [r for mail in STATE["emails"] for r in mail["recipients"]]
		self.assertEqual(recipients, ["boss@example.com"])

	def test_the_email_half_respects_the_notification_switch(self):
		"""Silenced Training email must not come back through the compliance door —
		but the comment still lands, so the record loses nothing."""
		STATE["settings"]["notifications_enabled"] = 0
		compliance.warn_uncertified_technician(_visit())
		_run_enqueued()
		self.assertEqual(STATE["emails"], [])
		self.assertEqual(len(STATE["comments"]), 1)


# ------------------------------------------------- gamification: separate boards


class TestLeaderboardsNeverMix(unittest.TestCase):
	"""Staff and customers are scored on separate boards, and the separation is
	structural rather than remembered: there is no read of the stat table that does
	not name a learner type."""

	def setUp(self):
		_reset_state()
		_table("Training Learner Stat").extend(
			[
				_Dict(
					name="tech@example.com",
					user="tech@example.com",
					learner_type="Employee",
					total_points=30,
					courses_completed=3,
					badges_earned=1,
					current_streak_days=2,
					longest_streak_days=4,
					last_completion_date=TODAY,
				),
				_Dict(
					name="client@example.com",
					user="client@example.com",
					learner_type="Website User",
					total_points=90,
					courses_completed=9,
					badges_earned=2,
					current_streak_days=0,
					longest_streak_days=3,
					last_completion_date="2026-01-01",
				),
				_Dict(
					name="rival@example.com",
					user="rival@example.com",
					learner_type="Website User",
					total_points=500,
					courses_completed=50,
					badges_earned=9,
					current_streak_days=0,
					longest_streak_days=1,
					last_completion_date="2026-01-01",
				),
			]
		)
		_table("Contact").extend(
			[
				_Dict(name="CON-1", user="client@example.com"),
				_Dict(name="CON-2", user="rival@example.com"),
			]
		)
		_table("Dynamic Link").extend(
			[
				_Dict(
					name="DL-1",
					parent="CON-1",
					parenttype="Contact",
					link_doctype="Customer",
					link_name="CUST-0001",
				),
				_Dict(
					name="DL-2",
					parent="CON-2",
					parenttype="Contact",
					link_doctype="Customer",
					link_name="CUST-0002",
				),
			]
		)

	def test_the_staff_board_excludes_customers(self):
		rows = gamification._stat_rows("Employee")
		self.assertEqual([r["user"] for r in rows], ["tech@example.com"])

	def test_the_customer_board_excludes_staff_and_other_customers(self):
		"""Two unrelated clients must not learn each other's people from a
		leaderboard."""
		rows = gamification._stat_rows("Website User", customer="CUST-0001")
		self.assertEqual([r["user"] for r in rows], ["client@example.com"])

	def test_a_customer_board_with_no_customer_returns_nothing(self):
		"""Fails closed. A privacy filter nobody set reads as "show none", never as
		"show every customer in the database"."""
		self.assertEqual(gamification._stat_rows("Website User"), [])
		self.assertEqual(gamification._stat_rows("Website User", customer=""), [])

	def test_seeing_every_customer_has_to_be_asked_for_explicitly(self):
		rows = gamification._stat_rows("Website User", customer=gamification.ALL_CUSTOMERS)
		self.assertEqual(len(rows), 2)

	def test_an_unrecognised_scope_is_refused_not_widened(self):
		"""The failure being designed against is a future caller that omits the
		argument and silently publishes a mixed board."""
		for bad in (None, "", "all", "everyone"):
			with self.assertRaises(ValueError):
				gamification._stat_rows(bad)

	def test_a_customer_contact_cannot_ask_for_the_staff_board(self):
		STATE["roles"]["client@example.com"] = ["Customer"]
		self.assertEqual(gamification._resolve_scope("Employee", "client@example.com"), "Website User")

	def test_a_manager_may_ask_for_either(self):
		self.assertEqual(gamification._resolve_scope("customers", "boss@example.com"), "Website User")
		self.assertEqual(gamification._resolve_scope("staff", "boss@example.com"), "Employee")

	def test_scope_matches_the_rule_the_stat_row_is_filed_under(self):
		"""``Training Learner Stat`` derives learner_type from an Employee record
		existing at all. Scoping on a different rule (say, only *active* employees)
		would rank somebody on one board while scoping them to the other."""
		self.assertEqual(gamification._learner_type("tech@example.com"), "Employee")
		self.assertEqual(gamification._learner_type("client@example.com"), "Website User")

	def test_a_stale_streak_decays_at_read_time(self):
		"""A missed scheduler run must never show somebody a streak they no longer
		have."""
		stale = _Dict(current_streak_days=9, last_completion_date="2026-06-01")
		self.assertEqual(gamification._decayed_streak(stale), 0)
		fresh = _Dict(current_streak_days=9, last_completion_date=TODAY)
		self.assertEqual(gamification._decayed_streak(fresh), 9)


class TestBadgeCriteria(unittest.TestCase):
	"""Badges are configuration, so the evaluator has to be honest about the rules
	it cannot answer."""

	def setUp(self):
		_reset_state()
		self.stats = {
			"courses": {"TRN-CRS-00001"},
			"courses_completed": 1,
			"perfect_scores": 0,
			"longest_streak": 3,
		}

	def _earned(self, **badge):
		return gamification._badge_is_earned(_Dict(**badge), self.stats)

	def test_thresholds(self):
		self.assertTrue(self._earned(criteria_type="First Completion"))
		self.assertTrue(self._earned(criteria_type="Courses Completed Count", criteria_value="1"))
		self.assertFalse(self._earned(criteria_type="Courses Completed Count", criteria_value="2"))
		self.assertTrue(self._earned(criteria_type="Streak Days", criteria_value="3"))
		self.assertFalse(self._earned(criteria_type="Streak Days", criteria_value="7"))
		self.assertFalse(self._earned(criteria_type="Perfect Score"))
		self.assertTrue(self._earned(criteria_type="Course Completed", criteria_course="TRN-CRS-00001"))
		self.assertFalse(self._earned(criteria_type="Course Completed", criteria_course="TRN-CRS-00002"))

	def test_an_unknown_criterion_awards_nothing(self):
		"""Not everything. A rule this module cannot evaluate must not become a
		badge the whole company holds."""
		self.assertFalse(self._earned(criteria_type="Vibes"))
		self.assertFalse(self._earned(criteria_type=None))

	def test_an_empty_category_is_not_vacuously_complete(self):
		"""``all(courses in category)`` over an empty category is True, and would
		hand the badge to everybody."""
		self.assertFalse(gamification._category_is_complete("TRN-CAT-EMPTY", self.stats["courses"]))

	def test_a_category_needs_every_published_course_in_it(self):
		_table("Training Course").extend(
			[
				_Dict(name="TRN-CRS-00001", category="TRN-CAT-1", status="Published"),
				_Dict(name="TRN-CRS-00009", category="TRN-CAT-1", status="Published"),
			]
		)
		self.assertFalse(gamification._category_is_complete("TRN-CAT-1", {"TRN-CRS-00001"}))
		self.assertTrue(gamification._category_is_complete("TRN-CAT-1", {"TRN-CRS-00001", "TRN-CRS-00009"}))


if __name__ == "__main__":
	unittest.main()
