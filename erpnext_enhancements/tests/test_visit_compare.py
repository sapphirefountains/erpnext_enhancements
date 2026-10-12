# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for "Compared with last visit" (``sapphire_maintenance/visit_compare.py``).

A3 of the 2026-10-12 Maintenance tools plan (TASK-2026-02476): each reading on a visit beside
its value at the site's previous approved visit. The review email, the Visit Review page and
the desk-form dashboard all read this one helper, so it is pinned here once:

* a Per Site Visit record compares each water feature with that feature, not with the site;
* a reading the previous visit did not take is looked for further back, and missing
  everywhere it is simply absent;
* a site with no approved visit before this one is a first visit;
* a visit not yet approved, or dated after this one, is never the "previous" visit.

Stubs a minimal in-memory ``frappe`` in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_compare
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

RECORD = "Sapphire Maintenance Record"
READING = "Sapphire Chemistry Reading"
MODULE = "erpnext_enhancements.sapphire_maintenance.visit_compare"
STUBBED = ("frappe", "frappe.utils", MODULE)

compare = None
ROWS = {}
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 10, 12)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _match(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, (list, tuple)):
			op, arg = cond
			if op == "!=" and value == arg:
				return False
			if op == "in" and value not in arg:
				return False
		elif value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, order_by=None, limit=None, **kwargs):
	rows = [row for row in ROWS.get(doctype, []) if _match(row, filters)]
	if order_by:
		field, _, direction = order_by.partition(" ")
		rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction.strip() == "desc")
	if limit:
		rows = rows[:limit]
	return [_Dict({f: row.get(f) for f in fields}) for row in rows]


def _db_sql(query, params, as_dict=False):
	"""``visit_compare.EARLIER_SQL``, done in Python: the site's approved visits, other than this
	one, whose day (visit date, else scheduled date, else creation) is on or before ``day``,
	newest first, at most ``limit``. ``TestTheQuery`` pins that the SQL says the same."""
	assert query is compare.EARLIER_SQL and as_dict
	rows = [
		row
		for row in ROWS.get(RECORD, [])
		if row.project == params["project"]
		and row.docstatus == 1
		and row.name != params["name"]
		and compare.visit_day(row) <= params["day"]
	]
	rows.sort(key=lambda row: (compare.visit_day(row), str(row.creation)), reverse=True)
	return [_Dict(row) for row in rows[: params["limit"]]]


def _db_get_value(doctype, name, fields, as_dict=False, **kwargs):
	"""A record's header (technician, notes) and a user's full name."""
	if doctype == "User":
		return {"austin@example.com": "Austin Healey"}.get(name)
	row = next((r for r in ROWS.get(doctype, []) if r.name == name), None)
	if row is None:
		return None
	return _Dict({f: row.get(f) for f in fields}) if as_dict else [row.get(f) for f in fields]


def _forget_package_attribute():
	"""``from package import name`` prefers a stale package attribute to a fresh import."""
	import erpnext_enhancements.sapphire_maintenance as package

	if "visit_compare" in vars(package):
		delattr(package, "visit_compare")


def setUpModule():
	global compare
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	_forget_package_attribute()
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_all = _get_all
	frappe.db = types.SimpleNamespace(sql=_db_sql, get_value=_db_get_value)
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-12"
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	from erpnext_enhancements.sapphire_maintenance import visit_compare

	compare = visit_compare


def tearDownModule():
	_forget_package_attribute()
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


# ------------------------------------------------------------------ fixtures


def _record(name, day, docstatus=1, serial_no=None, project="PRJ-1", created=None, **extra):
	ROWS.setdefault(RECORD, []).append(
		_Dict(
			name=name,
			project=project,
			docstatus=docstatus,
			serial_no=serial_no,
			visit_date=day,
			scheduled_visit_date=None,
			creation=created or f"{day} 09:00:00",
			**extra,
		)
	)


def _reading(parent, reading, value, serial_no=None, uom=""):
	ROWS.setdefault(READING, []).append(
		_Dict(
			parent=parent,
			parenttype=RECORD,
			reading=reading,
			reading_value=value,
			uom=uom,
			serial_no=serial_no,
		)
	)


def _visit(rows, serial_no=None, day="2026-10-12", name="NOW"):
	"""The visit under review: not approved, with its own readings."""
	return _Dict(
		name=name,
		project="PRJ-1",
		docstatus=0,
		serial_no=serial_no,
		visit_date=day,
		scheduled_visit_date=None,
		creation=f"{day} 10:00:00",
		chemistry_readings=[_Dict(r) for r in rows],
	)


class _Base(unittest.TestCase):
	def setUp(self):
		ROWS.clear()


class TestFirstVisit(_Base):
	def test_a_site_with_no_approved_visit_is_a_first_visit(self):
		result = compare.compare(_visit([{"reading": "pH", "reading_value": 7.4}]))
		self.assertEqual(result, {"previous": {}, "first_visit": True})

	def test_drafts_and_visits_waiting_for_review_are_not_compared_against(self):
		"""Not approved yet: it may still be sent back and changed."""
		_record("DRAFT", "2026-10-05", docstatus=0)
		_reading("DRAFT", "pH", 8.0)
		_record("CANCELED", "2026-10-04", docstatus=2)
		_reading("CANCELED", "pH", 8.1)
		self.assertTrue(compare.compare(_visit([{"reading": "pH", "reading_value": 7.4}]))["first_visit"])

	def test_another_sites_visits_are_not_this_sites_history(self):
		_record("ELSEWHERE", "2026-10-05", project="PRJ-2")
		_reading("ELSEWHERE", "pH", 8.0)
		self.assertTrue(compare.compare(_visit([{"reading": "pH", "reading_value": 7.4}]))["first_visit"])

	def test_a_visit_dated_after_this_one_is_not_its_previous_visit(self):
		"""Reviewing a backfilled Oct 1 visit after Oct 6 was approved compares with September."""
		_record("LATER", "2026-10-06")
		_reading("LATER", "pH", 9.9)
		_record("EARLIER", "2026-09-29")
		_reading("EARLIER", "pH", 7.6)
		result = compare.compare(_visit([{"reading": "pH", "reading_value": 7.4}], day="2026-10-01"))
		self.assertEqual(result["previous"][("", "ph")]["record"], "EARLIER")
		self.assertEqual(result["previous"][("", "ph")]["value"], 7.6)


class TestPerFountain(_Base):
	def test_a_site_visit_compares_each_fountain_with_itself(self):
		_record("PREV", "2026-10-05")
		_reading("PREV", "pH", 7.4, serial_no="SN-EAST")
		_reading("PREV", "pH", 7.6, serial_no="SN-WEST")
		doc = _visit(
			[
				{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"},
				{"reading": "pH", "reading_value": 9.1, "serial_no": "SN-WEST", "out_of_range": 1},
			]
		)
		lines = compare.reading_comparisons(doc)
		self.assertEqual(
			[(line["serial_no"], line["previous"]) for line in lines], [("SN-EAST", 7.4), ("SN-WEST", 7.6)]
		)
		self.assertEqual(lines[1]["change"], 1.5)
		self.assertEqual(lines[1]["out_of_range"], 1)

	def test_a_contract_that_changed_shape_still_lines_up(self):
		"""Per Feature before (header serial, untagged rows), Per Site Visit now (tagged rows)."""
		_record("PER-FEATURE", "2026-10-05", serial_no="SN-EAST")
		_reading("PER-FEATURE", "pH", 7.3)
		doc = _visit([{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"}])
		self.assertEqual(compare.compare(doc)["previous"][("SN-EAST", "ph")]["value"], 7.3)

	def test_another_fountains_reading_is_not_this_ones(self):
		_record("PREV", "2026-10-05")
		_reading("PREV", "pH", 7.4, serial_no="SN-WEST")
		doc = _visit([{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"}])
		result = compare.compare(doc)
		self.assertFalse(result["first_visit"])
		self.assertEqual(result["previous"], {})

	def test_the_label_matches_whatever_its_case_and_spacing(self):
		_record("PREV", "2026-10-05")
		_reading("PREV", " Free Chlorine ", 2.5)
		doc = _visit([{"reading": "free chlorine", "reading_value": 3.0}])
		self.assertEqual(compare.compare(doc)["previous"][("", "free chlorine")]["value"], 2.5)


class TestMissingPreviousValue(_Base):
	def test_a_reading_the_last_visit_skipped_is_found_further_back(self):
		_record("LAST", "2026-10-05")
		_reading("LAST", "pH", 7.4)
		_reading("LAST", "Alkalinity", 0)  # blank or zero: not measured
		_record("BEFORE", "2026-09-28")
		_reading("BEFORE", "Alkalinity", 110)
		doc = _visit(
			[{"reading": "pH", "reading_value": 7.5}, {"reading": "Alkalinity", "reading_value": 100}]
		)
		result = compare.compare(doc)
		self.assertEqual(result["previous"][("", "ph")]["record"], "LAST")
		self.assertEqual(result["previous"][("", "alkalinity")]["record"], "BEFORE")
		self.assertEqual(
			compare.compared_days(result), [datetime.date(2026, 10, 5), datetime.date(2026, 9, 28)]
		)

	def test_a_reading_never_taken_before_has_no_previous_value(self):
		_record("LAST", "2026-10-05")
		_reading("LAST", "pH", 7.4)
		doc = _visit([{"reading": "pH", "reading_value": 7.5}, {"reading": "ORP", "reading_value": 700}])
		lines = compare.reading_comparisons(doc)
		self.assertEqual(lines[1]["previous"], None)
		self.assertEqual(lines[1]["change"], None)

	def test_a_reading_not_taken_today_has_no_change(self):
		_record("LAST", "2026-10-05")
		_reading("LAST", "pH", 7.4)
		lines = compare.reading_comparisons(_visit([{"reading": "pH", "reading_value": 0}]))
		self.assertEqual((lines[0]["value"], lines[0]["previous"], lines[0]["change"]), (None, 7.4, None))

	def test_the_search_stops_after_the_lookback(self):
		for week in range(compare.LOOKBACK + 1):
			name = f"V{week}"
			day = datetime.date(2026, 10, 5) - datetime.timedelta(days=7 * week)
			_record(name, str(day))
			if week == compare.LOOKBACK:
				_reading(name, "TDS", 900)
		self.assertEqual(compare.compare(_visit([{"reading": "TDS", "reading_value": 950}]))["previous"], {})


class TestLookbackIsPerFountain(_Base):
	"""A Per Feature contract makes one record per fountain per visit (review of PR #1204)."""

	def test_a_twelve_fountain_site_still_finds_last_weeks_reading(self):
		for n in range(12):
			_record(f"F{n}", "2026-10-05", serial_no=f"SN-{n}", created=f"2026-10-05 09:{n:02d}:00")
			_reading(f"F{n}", "pH", 7.0 + n / 10)
		for n in range(1, 12):  # this week's other fountains, approved already
			_record(f"T{n}", "2026-10-12", serial_no=f"SN-{n}", created=f"2026-10-12 09:{n:02d}:00")
			_reading(f"T{n}", "pH", 8.0)
		doc = _visit([{"reading": "pH", "reading_value": 7.2}], serial_no="SN-0")
		self.assertEqual(compare.compare(doc)["previous"][("SN-0", "ph")]["record"], "F0")

	def test_a_site_visit_counts_for_every_fountain(self):
		_record("SITE", "2026-10-05")
		_reading("SITE", "pH", 7.6, serial_no="SN-0")
		doc = _visit([{"reading": "pH", "reading_value": 7.2}], serial_no="SN-0")
		self.assertEqual(compare.compare(doc)["previous"][("SN-0", "ph")]["value"], 7.6)

	def test_an_older_visit_is_not_crowded_out_by_the_visits_after_it(self):
		"""The dashboard of an approved visit from August, with fifty visits approved since."""
		_record("AUGUST-BEFORE", "2026-08-03")
		_reading("AUGUST-BEFORE", "pH", 7.1)
		for week in range(60):
			name = f"LATER{week}"
			_record(name, str(datetime.date(2026, 8, 10) + datetime.timedelta(days=week)))
			_reading(name, "pH", 8.0)
		doc = _visit([{"reading": "pH", "reading_value": 7.3}], day="2026-08-05", name="AUGUST")
		result = compare.compare(doc)
		self.assertFalse(result["first_visit"])
		self.assertEqual(result["previous"][("", "ph")]["record"], "AUGUST-BEFORE")


class TestTheQuery(unittest.TestCase):
	"""The stub above does in Python what the SQL says; this pins the SQL to the same rule."""

	def test_the_sql_filters_and_orders_by_the_day_the_work_was_done(self):
		sql = " ".join(compare.EARLIER_SQL.split())
		day = "coalesce(visit_date, scheduled_visit_date, date(creation))"
		for clause in (
			"project = %(project)s",
			"docstatus = 1",
			"name != %(name)s",
			f"{day} <= %(day)s",
			f"order by {day} desc, creation desc",
			"limit %(limit)s",
		):
			with self.subTest(clause=clause):
				self.assertIn(clause, sql)


def _result(parent, question, selection, answer="", details="", serial_no=None, idx=1):
	ROWS.setdefault("Sapphire Maintenance Result", []).append(
		_Dict(
			parent=parent,
			parenttype=RECORD,
			question=question,
			selection=selection,
			answer=answer,
			other_details=details,
			serial_no=serial_no,
			idx=idx,
		)
	)


class TestLastVisitHere(_Base):
	"""C1 (TASK-2026-02482): the Visit Wizard's "Last visit here" card."""

	def setUp(self):
		super().setUp()
		ROWS["Serial No"] = [
			_Dict(name="SN-EAST", item_name="East Fountain"),
			_Dict(name="SN-WEST", item_name="West Fountain"),
		]

	def test_first_recorded_visit(self):
		self.assertEqual(
			compare.last_visit(_visit([{"reading": "pH", "reading_value": 7.4}])), {"first_visit": True}
		)

	def test_the_previous_approved_visit_with_readings_problems_and_notes(self):
		_record("PREV", "2026-10-06", technician="austin@example.com", visit_notes="  Pump was loud.  ")
		_reading("PREV", "pH", 7.4)
		_reading("PREV", "ORP", 400, uom="mV")
		_reading("PREV", "Alkalinity", 0)  # not measured
		ROWS[READING][1]["out_of_range"] = 1
		_result("PREV", "Nozzles", "Replace", answer="Two cracked", details="Ordered", idx=1)
		_result("PREV", "Pump", "Pass", idx=2)
		_result("PREV", "Lights", "Fail", idx=3)
		_record("LATER", "2026-10-20")  # after this visit: not its last
		_record("DRAFT", "2026-10-10", docstatus=0)  # not approved
		last = compare.last_visit(_visit([{"reading": "pH", "reading_value": 7.5}]))
		self.assertEqual(
			{k: last[k] for k in ("first_visit", "name", "day", "technician_name", "notes")},
			{
				"first_visit": False,
				"name": "PREV",
				"day": "2026-10-06",
				"technician_name": "Austin Healey",
				"notes": "Pump was loud.",
			},
		)
		(group,) = last["groups"]
		self.assertEqual(
			group["readings"],
			[
				{"reading": "pH", "value": 7.4, "uom": "", "out_of_range": 0},
				{"reading": "ORP", "value": 400.0, "uom": "mV", "out_of_range": 1},
			],
		)
		self.assertEqual(
			group["problems"],
			[
				{"question": "Nozzles", "answer": "Replace Two cracked", "details": "Ordered"},
				{"question": "Lights", "answer": "Fail", "details": ""},
			],
		)

	def test_a_site_visit_shows_each_fountain_under_its_name(self):
		_record("PREV", "2026-10-06")
		_reading("PREV", "pH", 7.4, serial_no="SN-EAST")
		_reading("PREV", "pH", 9.1, serial_no="SN-WEST")
		_result("PREV", "Nozzles", "Fail", serial_no="SN-WEST")
		last = compare.last_visit(_visit([{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"}]))
		self.assertEqual(
			[(g["serial_no"], g["label"]) for g in last["groups"]],
			[("SN-EAST", "East Fountain"), ("SN-WEST", "West Fountain")],
		)
		self.assertEqual(last["groups"][1]["problems"][0]["question"], "Nozzles")

	def test_a_fountains_visit_sees_only_its_own_rows_and_its_own_last_visit(self):
		"""Per Feature: the newest earlier record for this fountain, or a site visit's rows for it."""
		_record("SITE", "2026-09-29")
		_reading("SITE", "pH", 7.1, serial_no="SN-EAST")
		_reading("SITE", "pH", 9.1, serial_no="SN-WEST")
		_record("WEST-ONLY", "2026-10-06", serial_no="SN-WEST")  # newer, but another fountain's
		_reading("WEST-ONLY", "pH", 8.8)
		last = compare.last_visit(_visit([{"reading": "pH", "reading_value": 7.5}], serial_no="SN-EAST"))
		self.assertEqual(last["name"], "SITE")
		(group,) = last["groups"]
		self.assertEqual((group["serial_no"], [r["value"] for r in group["readings"]]), ("SN-EAST", [7.1]))


class TestTheDashboardCarriesIt(unittest.TestCase):
	"""The wizard reads it from the dashboard context it already loads (no second query path)."""

	def test_bootstrap_asks_the_dashboard_for_this_visit(self):
		src = (REPO_ROOT / "erpnext_enhancements/api/maintenance_visit.py").read_text(encoding="utf-8")
		self.assertIn("get_dashboard_context(doc.project, doc.serial_no, record=doc.name)", src)
		record = (
			REPO_ROOT
			/ "erpnext_enhancements/sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.py"
		).read_text(encoding="utf-8")
		self.assertIn("context['last_visit'] = _last_visit_here(doc)", record)
		self.assertIn("return visit_compare.last_visit(doc)", record)


class TestVisitDay(unittest.TestCase):
	def test_the_day_the_work_was_done_then_the_scheduled_day_then_creation(self):
		self.assertEqual(
			compare.visit_day({"visit_date": "2026-10-08", "scheduled_visit_date": "2026-10-06"}),
			datetime.date(2026, 10, 8),
		)
		self.assertEqual(
			compare.visit_day({"scheduled_visit_date": "2026-10-06"}), datetime.date(2026, 10, 6)
		)
		self.assertEqual(compare.visit_day({"creation": "2026-10-02 08:00:00"}), datetime.date(2026, 10, 2))


if __name__ == "__main__":
	unittest.main()
