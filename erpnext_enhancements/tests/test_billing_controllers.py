"""Bench-free tests for the Schedule of Values and Pay Application controllers, and their endpoints.

Runs the real controllers (``estimating/doctype/schedule_of_values``, ``.../pay_application``) and the
milestone-2 endpoints in ``api/estimating.py`` against an in-memory ``frappe`` stub installed in
``setUpModule``, so it has its own CI step. What it holds, in made-up numbers:

* a schedule mints a ``line_key`` per row once and keeps it; a duplicated row or a repeated item number
  is refused; totals and the difference from the contract sum are whole cents;
* a locked schedule refuses a changed line, a new base line and a new contract sum, and accepts a
  change-order row; a billed line cannot be deleted, locked or not;
* filling a schedule from an estimate partitions it exactly;
* a pay application chains to the latest one, refuses a second open one, starts the day after the last
  period, prices from the previous application's work completed BY KEY, picks up change orders
  approved by the end of its period, counts a change order as "this month" only when it is new, bills
  a milestone's percent on every line, defaults the amount certified to the payment due, and refuses at
  certify with every reason at once; only the latest application can be canceled.

Run: python -m unittest erpnext_enhancements.tests.test_billing_controllers -v
"""

import datetime
import importlib
import sys
import types
import unittest


class Thrown(Exception):
	pass


DOCS = {}
STORE = {"Pay Application": {}, "Change Order": {}}
SQL = {"billed": []}
COUNTER = {"n": 0}


class Row(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value

	def as_dict(self):
		return dict(self)


class FakeDocument:
	TABLES = ("lines", "retainage_steps", "milestones")

	def __init__(self, *args, **fields):
		self.__dict__["_f"] = {}
		self.__dict__["flags"] = types.SimpleNamespace()
		self.__dict__["_before"] = None
		self.__dict__["comments"] = []
		for k, v in fields.items():
			self.set(k, v)

	def __getattr__(self, key):
		if key in FakeDocument.TABLES:
			return self.__dict__["_f"].setdefault(key, [])
		return self.__dict__["_f"].get(key)

	def __setattr__(self, key, value):
		if key == "flags":
			self.__dict__["flags"] = value
			return
		self.set(key, value)

	def get(self, key, default=None):
		value = getattr(self, key)
		return default if value is None else value

	def set(self, key, value):
		if key in FakeDocument.TABLES:
			rows = [v if isinstance(v, Row) else Row(v) for v in (value or [])]
			for i, r in enumerate(rows, start=1):
				r.setdefault("idx", i)
			value = rows
		self.__dict__["_f"][key] = value

	def update(self, values):
		for k, v in values.items():
			self.set(k, v)

	def append(self, table, row):
		rows = getattr(self, table)
		row = Row(row)
		row.setdefault("idx", len(rows) + 1)
		rows.append(row)
		return row

	def as_dict(self):
		out = dict(self.__dict__["_f"])
		for t in FakeDocument.TABLES:
			if t in out:
				out[t] = [dict(r) for r in out[t]]
		return out

	def is_new(self):
		return not self.name

	def get_doc_before_save(self):
		return self.__dict__["_before"]

	def db_set(self, values, value=None):
		if isinstance(values, str):
			values = {values: value}
		self.update(values)

	def add_comment(self, kind, text):
		self.comments.append(text)

	def check_permission(self, ptype):
		pass


def _match(row, filters):
	for key, want in (filters or {}).items():
		have = row.get(key)
		if isinstance(want, list):
			op, val = want
			if op == "<" and not (have is not None and have < val):
				return False
			if op == "!=" and have == val:
				return False
			if op == "in" and have not in val:
				return False
		elif have != want:
			return False
	return True


def setUpModule():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s

	def throw(msg, *a, **k):
		raise Thrown(msg)

	frappe.throw = throw
	frappe.PermissionError = Thrown
	frappe.whitelist = lambda **k: (lambda fn: fn)
	frappe.has_permission = lambda *a, **k: True
	frappe.session = types.SimpleNamespace(user="pm@example.com")
	frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Example Co")
	frappe.format_value = lambda v, df=None: f"{float(v or 0):,.2f}"

	def generate_hash(length=10):
		COUNTER["n"] += 1
		return f"k{COUNTER['n']:03d}"

	frappe.generate_hash = generate_hash

	def get_doc(doctype, name=None):
		return DOCS[(doctype, name)]

	def new_doc(doctype):
		return CLASSES[doctype]() if doctype in CLASSES else FakeDocument()

	def get_all(doctype, filters=None, fields=None, order_by=None, limit=None, pluck=None, **k):
		rows = [Row(r, name=n) for n, r in STORE.get(doctype, {}).items() if _match(dict(r, name=n), filters)]
		if order_by:
			field, _, direction = order_by.partition(" ")
			rows.sort(key=lambda r: r.get(field) or 0, reverse=direction.strip() == "desc")
		if limit:
			rows = rows[:limit]
		return [r[pluck] for r in rows] if pluck else rows

	frappe.get_doc, frappe.new_doc, frappe.get_all = get_doc, new_doc, get_all

	class DB:
		def get_single_value(self, *a):
			return "Example Co"

		def exists(self, doctype, filters=None):
			if isinstance(filters, dict):
				for n, r in STORE.get(doctype, {}).items():
					if _match(dict(r, name=n), filters):
						return n
				return None
			return (doctype, filters) in DOCS

		def get_value(self, doctype, filters, field=None, **k):
			for n, r in STORE.get(doctype, {}).items():
				if _match(dict(r, name=n), filters):
					return n if field == "name" else r.get(field)
			return None

		def table_exists(self, doctype):
			return True

		def sql(self, query, values=None, **k):
			return SQL["billed"]

		def set_value(self, *a, **k):
			pass

	frappe.db = DB()
	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s).replace("<", "&lt;").replace(">", "&gt;")
	utils.flt = lambda v, *a: float(v or 0)

	def getdate(v):
		if isinstance(v, datetime.date):
			return v
		return datetime.date.fromisoformat(str(v)[:10])

	utils.getdate = getdate
	utils.add_days = lambda d, n: getdate(d) + datetime.timedelta(days=n)
	utils.now_datetime = lambda: datetime.datetime(2026, 11, 30, 12, 0)
	utils.nowdate = lambda: "2026-11-30"
	frappe.utils = utils
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = FakeDocument
	frappe.model = model
	model.document = document
	for name, mod in (
		("frappe", frappe),
		("frappe.utils", utils),
		("frappe.model", model),
		("frappe.model.document", document),
	):
		sys.modules[name] = mod

	global sov_mod, pa_mod, api, CLASSES
	sov_mod = importlib.reload(importlib.import_module("erpnext_enhancements.estimating.doctype.schedule_of_values.schedule_of_values"))
	pa_mod = importlib.reload(importlib.import_module("erpnext_enhancements.estimating.doctype.pay_application.pay_application"))
	api = importlib.reload(importlib.import_module("erpnext_enhancements.api.estimating"))
	pa_mod.refresh_billed = lambda schedule: None
	CLASSES = {"Schedule of Values": sov_mod.ScheduleofValues, "Pay Application": pa_mod.PayApplication}


def tearDownModule():
	for name in ("frappe", "frappe.utils", "frappe.model", "frappe.model.document"):
		sys.modules.pop(name, None)


def reset():
	DOCS.clear()
	STORE["Pay Application"] = {}
	SQL["billed"] = []
	COUNTER["n"] = 0


def schedule(**fields):
	doc = sov_mod.ScheduleofValues()
	base = {
		"project": "PRJ-00001",
		"contract_sum": 10000,
		"retainage_applies": 1,
		"retainage_pct": 5,
		"stored_retainage_pct": 5,
		"billing_mode": "Progress",
		"status": "Draft",
		"lines": [
			{"item_no": "1.1", "description": "Design", "scheduled_value": 4000},
			{"item_no": "5.1", "description": "Pumps", "scheduled_value": 6000},
			{"item_no": "7.1", "description": "Tile", "scheduled_value": 0, "by_others": 1},
		],
	}
	base.update(fields)
	doc.update(base)
	return doc


def saved(doc, name):
	"""Validate as a new document, then make it 'saved' under ``name`` with its before-image."""
	doc.validate()
	doc.name = name
	doc.__dict__["_before"] = FakeDocument(**{**doc.as_dict()})
	DOCS[(_doctype(doc), name)] = doc
	return doc


def _doctype(doc):
	return "Schedule of Values" if isinstance(doc, sov_mod.ScheduleofValues) else "Pay Application"


class TestScheduleOfValues(unittest.TestCase):
	def setUp(self):
		reset()

	def test_keys_are_minted_once_and_totals_are_cents(self):
		doc = saved(schedule(contract_sum=10000.01), "SOV-1")
		keys = [r.line_key for r in doc.lines]
		self.assertEqual(keys, ["k001", "k002", "k003"])
		doc.validate()
		self.assertEqual([r.line_key for r in doc.lines], keys)
		self.assertEqual(doc.scheduled_total, 10000.0)
		self.assertEqual(doc.difference, 0.01)

	def test_duplicates_are_refused(self):
		doc = schedule()
		doc.lines[1]["item_no"] = "1.1"
		with self.assertRaises(Thrown):
			doc.validate()
		doc = saved(schedule(), "SOV-1")
		doc.append("lines", dict(doc.lines[0]))
		with self.assertRaises(Thrown):
			doc.validate()

	def test_a_locked_schedule_takes_change_orders_only(self):
		doc = saved(schedule(), "SOV-1")
		doc.status = "Locked"
		doc.__dict__["_before"] = FakeDocument(**doc.as_dict())
		doc.append("lines", {"item_no": "CO-01", "description": "Change order 1", "scheduled_value": 1500, "change_order": "CO-1"})
		doc.validate()
		self.assertEqual(doc.contract_sum_to_date, 11500.0)
		doc.lines[1]["scheduled_value"] = 5000
		with self.assertRaises(Thrown):
			doc.validate()
		doc.lines[1]["scheduled_value"] = 6000
		doc.append("lines", {"item_no": "8.1", "description": "New scope", "scheduled_value": 10})
		with self.assertRaises(Thrown):
			doc.validate()

	def test_a_billed_line_cannot_be_deleted(self):
		doc = saved(schedule(), "SOV-1")
		SQL["billed"] = [("k002",)]
		doc.set("lines", [r for r in doc.lines if r.line_key != "k002"])
		with self.assertRaises(Thrown) as cm:
			doc.validate()
		self.assertIn("Pumps", str(cm.exception))

	def test_a_locked_schedule_keeps_how_the_job_bills(self):
		doc = saved(schedule(), "SOV-1")
		doc.status = "Locked"
		doc.__dict__["_before"] = FakeDocument(**doc.as_dict())
		doc.billing_mode = "Milestones"
		with self.assertRaises(Thrown):
			doc.validate()

	def test_billed_to_date_is_never_taken_from_the_form(self):
		doc = saved(schedule(), "SOV-1")
		doc.__dict__["_before"].lines[1]["billed_to_date"] = 1500
		doc.lines[1]["billed_to_date"] = 99  # a stale copy from a form open across a certify
		doc.validate()
		self.assertEqual(doc.lines[1].billed_to_date, 1500)

	def test_lock_problems(self):
		doc = saved(schedule(contract_sum=9000), "SOV-1")
		self.assertEqual(len(doc.lock_problems()), 1)
		doc = saved(schedule(), "SOV-2")
		self.assertEqual(doc.lock_problems(), [])


class TestFromEstimate(unittest.TestCase):
	def setUp(self):
		reset()

	def test_the_schedule_partitions_the_estimate(self):
		est = FakeDocument(
			name="EST-1",
			status="Issued",
			project="PRJ-00001",
			title="Water wall",
			customer="Example Account",
			revision=2,
			company="Example Co",
			total_amount=3717.4,
			phases=[],
		)
		est.__dict__["_f"]["phases"] = [Row(phase_number=500, label="Mechanical room")]
		est.lines = [
			{"idx": 1, "phase_number": 500, "cost_type": "Equipment", "description": "Pump", "amount": 2717.39},
			{"idx": 2, "phase_number": 500, "cost_type": "Equipment", "description": "Filter", "amount": 1000.01},
		]
		DOCS[("Estimate", "EST-1")] = est
		inserted = {}
		sov_mod.ScheduleofValues.insert = lambda self: (self.validate(), inserted.setdefault("doc", self), setattr(self, "name", "SOV-9"))
		try:
			name = api.make_schedule_of_values("EST-1")
		finally:
			del sov_mod.ScheduleofValues.insert
		doc = inserted["doc"]
		self.assertEqual(name, "SOV-9")
		self.assertEqual((doc.basis, doc.estimate, doc.estimate_revision), ("Estimate", "EST-1", 2))
		self.assertEqual([r.description for r in doc.lines], ["Mechanical room: equipment"])
		self.assertEqual(doc.difference, 0.0)


def locked_schedule(**fields):
	doc = saved(schedule(**fields), "SOV-1")
	doc.status = "Locked"
	doc.__dict__["_before"] = FakeDocument(**doc.as_dict())
	return doc


def application(name, period_to="2026-11-30", **fields):
	pa = pa_mod.PayApplication()
	pa.update({"schedule_of_values": "SOV-1", "period_to": period_to})
	pa.update(fields)
	pa.before_insert()
	pa.name = name
	pa.validate()
	DOCS[("Pay Application", name)] = pa
	STORE["Pay Application"][name] = {
		"schedule_of_values": "SOV-1",
		"docstatus": 0,
		"application_no": pa.application_no,
		"period_to": pa.period_to,
		"previous_application": pa.previous_application,
		"milestone": pa.milestone,
	}
	return pa


def certify(pa):
	pa.validate()
	pa.before_submit()
	pa.docstatus = 1
	STORE["Pay Application"][pa.name]["docstatus"] = 1


def pct(pa, key, value):
	next(r for r in pa.lines if r.line_key == key)["this_period_pct"] = value


class TestPayApplication(unittest.TestCase):
	def setUp(self):
		reset()

	def test_the_first_application(self):
		locked_schedule()
		pa = application("PAY-1")
		self.assertEqual((pa.application_no, pa.previous_application), (1, None))
		self.assertEqual([r.line_key for r in pa.lines], ["k001", "k002", "k003"])
		pct(pa, "k001", 100)
		pct(pa, "k002", 25)
		pa.validate()
		self.assertEqual(pa.this_period_total, 5500.0)
		self.assertEqual(pa.total_retainage, 275.0)
		self.assertEqual(pa.current_payment_due, 5225.0)
		self.assertEqual(pa.amount_certified, 5225.0)

	def test_an_unlocked_schedule_starts_nothing(self):
		saved(schedule(), "SOV-1")
		with self.assertRaises(Thrown):
			application("PAY-1")

	def test_the_chain(self):
		sov = locked_schedule()
		pa1 = application("PAY-1", period_to="2026-10-31")
		pct(pa1, "k001", 100)
		with self.assertRaises(Thrown):
			application("PAY-2")  # PAY-1 is still open
		certify(pa1)
		# The schedule is reordered: the chain follows the key, not the row
		sov.set("lines", list(reversed(sov.lines)))
		pa2 = application("PAY-2")
		self.assertEqual((pa2.application_no, pa2.previous_application), (2, "PAY-1"))
		self.assertEqual(str(pa2.period_from), "2026-11-01")
		design = next(r for r in pa2.lines if r.line_key == "k001")
		self.assertEqual(design.from_previous, 4000.0)
		self.assertEqual(pa2.less_previous_certificates, 3800.0)

	def test_a_short_certification_carries_into_the_next(self):
		locked_schedule()
		pa1 = application("PAY-1", period_to="2026-10-31")
		pct(pa1, "k001", 100)
		pa1.certified_less = 1
		pa1.amount_certified = 3500
		certify(pa1)
		pa2 = application("PAY-2")
		pct(pa2, "k002", 10)
		pa2.validate()
		# earned (4000 + 600) - 230 retainage = 4370, less 3500 certified
		self.assertEqual(pa2.current_payment_due, 870.0)

	def test_change_orders_join_by_their_approval_date(self):
		sov = locked_schedule()
		sov.append("lines", {"item_no": "CO-01", "description": "Change order 1", "scheduled_value": 1500, "change_order": "CO-1", "approved_on": "2026-11-15", "line_key": "kco"})
		pa1 = application("PAY-1", period_to="2026-10-31")
		self.assertNotIn("kco", [r.line_key for r in pa1.lines])
		self.assertEqual(pa1.net_change_orders, 0.0)
		certify(pa1)
		pa2 = application("PAY-2", period_to="2026-11-30")
		self.assertIn("kco", [r.line_key for r in pa2.lines])
		self.assertEqual((pa2.net_change_orders, pa2.co_this_month, pa2.co_previous_months), (1500.0, 1500.0, 0.0))
		certify(pa2)
		pa3 = application("PAY-3", period_to="2026-12-31")
		self.assertEqual((pa3.co_this_month, pa3.co_previous_months), (0.0, 1500.0))

	def test_a_canceled_or_later_change_order_leaves_a_draft(self):
		sov = locked_schedule()
		sov.append("lines", {"item_no": "CO-01", "description": "Change order 1", "scheduled_value": 1500, "change_order": "CO-1", "approved_on": "2026-11-15", "line_key": "kco"})
		pa = application("PAY-1", period_to="2026-11-30")
		self.assertIn("kco", [r.line_key for r in pa.lines])
		pa.period_to = "2026-11-10"  # moved earlier: the change order is after the period now
		pa.validate()
		self.assertNotIn("kco", [r.line_key for r in pa.lines])
		self.assertEqual(pa.net_change_orders, 0.0)
		pa.period_to = "2026-11-30"
		pa.validate()
		pct(pa, "kco", 50)
		STORE["Change Order"]["CO-1"] = {"docstatus": 2}
		pa.validate()
		# typed on, so kept, and refused by name at certify
		self.assertIn("kco", [r.line_key for r in pa.lines])
		with self.assertRaises(Thrown) as cm:
			pa.before_submit()
		self.assertIn("No longer on the schedule", str(cm.exception))
		STORE["Change Order"] = {}

	def test_a_bad_retainage_override_is_refused_by_name(self):
		locked_schedule()
		pa = application("PAY-1")
		pa.work_retainage_override = "abc"
		with self.assertRaises(Thrown) as cm:
			pa.validate()
		self.assertIn("not a percent", str(cm.exception))
		pa.work_retainage_override = " 7.5 % "
		pa.validate()
		self.assertEqual((pa.work_retainage_override, pa.work_retainage_pct), ("7.5", 7.5))
		pa.lines[0]["work_retainage_override"] = "150"
		with self.assertRaises(Thrown):
			pa.validate()

	def test_milestones_bill_their_percent_of_every_line(self):
		locked_schedule(billing_mode="Milestones", milestones=[{"milestone": "Deposit", "percent": 50}, {"milestone": "Rough-in", "percent": 40}])
		pa = application("PAY-1", milestone="Deposit")
		self.assertEqual([r.this_period_pct for r in pa.lines], [50.0, 50.0, 0.0])
		self.assertEqual(pa.this_period_total, 5000.0)
		pa.milestone = "Nonsense"
		with self.assertRaises(Thrown):
			pa.validate()

	def test_certify_refuses_with_every_reason(self):
		locked_schedule()
		pa = application("PAY-1")
		pct(pa, "k001", 150)
		pct(pa, "k003", 10)
		pa.validate()
		with self.assertRaises(Thrown) as cm:
			pa.before_submit()
		self.assertIn("past the scheduled value", str(cm.exception))
		# a percent on a line by others is set back to 0, never billed
		self.assertEqual(next(r for r in pa.lines if r.line_key == "k003").this_period_pct, 0.0)

	def test_only_the_latest_can_be_canceled(self):
		locked_schedule()
		pa1 = application("PAY-1", period_to="2026-10-31")
		pct(pa1, "k001", 100)
		certify(pa1)
		application("PAY-2")
		with self.assertRaises(Thrown):
			pa1.before_cancel()

	def test_amending_is_refused(self):
		locked_schedule()
		pa = pa_mod.PayApplication()
		pa.update({"schedule_of_values": "SOV-1", "period_to": "2026-11-30", "amended_from": "PAY-1"})
		with self.assertRaises(Thrown):
			pa.before_insert()


if __name__ == "__main__":
	unittest.main()
