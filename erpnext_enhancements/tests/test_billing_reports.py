"""The three progress-billing reports (estimating B9 follow-up), without a bench.

* the arithmetic in ``estimating/billing_reports.py``: which rows each report shows, in whole cents;
* every report admits only roles that may run a report on its reference doctype (frappe refuses a run
  otherwise, after the report's own role check);
* every field the reports read is permlevel 0 on its doctype: they run server-side, where the
  field-level read checks (v1.835.0) do not reach, so the rule is held here.

Every number is made up: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_billing_reports
"""

import ast
import datetime
import json
import os
import unittest

from erpnext_enhancements.estimating import billing_reports as br

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS = os.path.join(APP, "estimating", "report")
DOCTYPES = os.path.join(APP, "estimating", "doctype")


def doctype_json(name):
	folder = name.lower().replace(" ", "_")
	with open(os.path.join(DOCTYPES, folder, folder + ".json"), encoding="utf-8") as fh:
		return json.load(fh)


def report(name):
	folder = name.lower().replace(" ", "_")
	base = os.path.join(REPORTS, folder, folder)
	with open(base + ".json", encoding="utf-8") as fh:
		doc = json.load(fh)
	with open(base + ".py", encoding="utf-8") as fh:
		src = fh.read()
	return doc, src


NAMES = ("Retainage Held", "Pay Applications Outstanding", "Billed vs Scheduled")


class TestRetainageHeld(unittest.TestCase):
	SCHEDULES = [
		{
			"name": "SOV-1",
			"project": "PRJ-1",
			"status": "Locked",
			"contract_sum_to_date": 10000,
			"billed_to_date": 6000,
			"retainage_held": 300,
			"retainage_released": 0,
		},
		{
			"name": "SOV-2",
			"project": "PRJ-2",
			"status": "Closed",
			"contract_sum_to_date": 5000,
			"billed_to_date": 5000,
			"retainage_held": 0,
			"retainage_released": 250,
		},
		{
			"name": "SOV-3",
			"project": "PRJ-3",
			"status": "Locked",
			"contract_sum_to_date": 20000,
			"billed_to_date": 3000,
			"retainage_held": 300.01,
			"retainage_released": 0,
		},
	]
	CERTIFIED = [
		{
			"name": "PAY-1",
			"schedule_of_values": "SOV-1",
			"application_no": 1,
			"period_to": "2026-09-30",
			"application_type": "Progress",
		},
		{
			"name": "PAY-2",
			"schedule_of_values": "SOV-1",
			"application_no": 2,
			"period_to": "2026-10-31",
			"application_type": "Progress",
		},
		{
			"name": "PAY-3",
			"schedule_of_values": "SOV-2",
			"application_no": 4,
			"period_to": "2026-10-31",
			"application_type": "Final",
		},
	]

	def test_jobs_holding_retainage_most_first(self):
		rows = br.retainage_rows(self.SCHEDULES, self.CERTIFIED)
		self.assertEqual([r["schedule_of_values"] for r in rows], ["SOV-3", "SOV-1"])
		sov1 = rows[1]
		self.assertEqual((sov1["latest_application"], str(sov1["latest_period_to"])), ("PAY-2", "2026-10-31"))
		self.assertEqual(sov1["held_percent_of_billed"], 5.0)
		self.assertIsNone(rows[0]["latest_application"])  # holds retainage from an opening balance only

	def test_every_job_when_asked(self):
		rows = br.retainage_rows(self.SCHEDULES, self.CERTIFIED, holding_only=False)
		closed = next(r for r in rows if r["schedule_of_values"] == "SOV-2")
		self.assertEqual(
			(closed["retainage_held"], closed["retainage_released"], closed["latest_type"]),
			(0.0, 250.0, "Final"),
		)

	def test_nothing_billed_is_no_percent(self):
		rows = br.retainage_rows([{"name": "SOV-9", "retainage_held": 10, "billed_to_date": 0}], [])
		self.assertIsNone(rows[0]["held_percent_of_billed"])


class TestOutstanding(unittest.TestCase):
	TODAY = datetime.date(2026, 11, 30)
	SCHEDULES = [
		{"name": "SOV-1", "project": "PRJ-1", "status": "Locked"},  # an open draft
		{"name": "SOV-2", "project": "PRJ-2", "status": "Locked"},  # last certified long ago: due
		{"name": "SOV-3", "project": "PRJ-3", "status": "Locked"},  # certified recently: nothing to do
		{"name": "SOV-4", "project": "PRJ-4", "status": "Locked"},  # never billed
		{"name": "SOV-5", "project": "PRJ-5", "status": "Closed"},  # billed out
	]
	APPS = [
		{
			"name": "PAY-1",
			"schedule_of_values": "SOV-1",
			"application_no": 3,
			"docstatus": 0,
			"status": "To Review",
			"period_to": "2026-11-15",
			"current_payment_due": 1234.5,
		},
		{
			"name": "PAY-0",
			"schedule_of_values": "SOV-1",
			"application_no": 2,
			"docstatus": 1,
			"status": "Certified",
			"period_to": "2026-10-15",
		},
		{
			"name": "PAY-2",
			"schedule_of_values": "SOV-2",
			"application_no": 1,
			"docstatus": 1,
			"status": "Certified",
			"period_to": "2026-09-30",
			"application_type": "Progress",
		},
		{
			"name": "PAY-3",
			"schedule_of_values": "SOV-3",
			"application_no": 5,
			"docstatus": 1,
			"status": "Certified",
			"period_to": "2026-11-10",
		},
		{
			"name": "PAY-5",
			"schedule_of_values": "SOV-5",
			"application_no": 6,
			"docstatus": 1,
			"status": "Certified",
			"period_to": "2026-06-30",
			"application_type": "Final",
		},
	]

	def test_open_due_and_not_started(self):
		rows = br.outstanding_rows(self.SCHEDULES, self.APPS, self.TODAY)
		self.assertEqual(
			[(r["state"], r["schedule_of_values"]) for r in rows],
			[("Due", "SOV-2"), ("To Review", "SOV-1"), ("Not started", "SOV-4")],
		)
		due, review = rows[0], rows[1]
		self.assertEqual((due["pay_application"], due["days_waiting"]), ("PAY-2", 61))
		self.assertEqual(
			(review["days_waiting"], review["current_payment_due"], review["application_type"]),
			(15, 1234.5, "Progress"),
		)

	def test_the_window_decides_due(self):
		rows = br.outstanding_rows(self.SCHEDULES, self.APPS, self.TODAY, due_after_days=10)
		self.assertIn(("Due", "SOV-3"), [(r["state"], r["schedule_of_values"]) for r in rows])
		rows = br.outstanding_rows(self.SCHEDULES, self.APPS, self.TODAY, due_after_days=90)
		self.assertNotIn("Due", [r["state"] for r in rows])


class TestBilledVsScheduled(unittest.TestCase):
	LINES = [
		{
			"idx": 1,
			"item_no": "1.1",
			"description": "Schematic design",
			"phase_number": 120,
			"scheduled_value": 1000,
			"billed_to_date": 1000,
		},
		{
			"idx": 2,
			"item_no": "1.2",
			"description": "Project management",
			"phase_number": 100,
			"scheduled_value": 333.33,
			"billed_to_date": 100,
		},
		{
			"idx": 3,
			"item_no": "5.1",
			"description": "Pumps",
			"phase_number": 500,
			"scheduled_value": 2717.39,
			"billed_to_date": 0,
		},
		{
			"idx": 4,
			"item_no": "7.1",
			"description": "Tile",
			"phase_number": 700,
			"scheduled_value": 0,
			"billed_to_date": 0,
			"by_others": 1,
		},
		{
			"idx": 5,
			"item_no": "CO-01",
			"description": "Change order 1",
			"phase_number": 0,
			"scheduled_value": -500,
			"billed_to_date": -250,
			"change_order": "CO-1",
		},
		{
			"idx": 6,
			"item_no": "9",
			"description": "Mobilization",
			"phase_number": 0,
			"scheduled_value": 100,
			"billed_to_date": 100,
		},
	]

	def test_by_phase_change_orders_last(self):
		rows = br.billed_rows(self.LINES)
		labels = [r["label"] for r in rows]
		self.assertEqual(labels[0], "Lines")
		self.assertTrue(labels[1].startswith("100 "))
		self.assertTrue(labels[2].startswith("500 "))
		self.assertEqual(labels[-1], "Change orders")
		self.assertEqual(len(rows), 4)  # the line by others is counted nowhere
		design = rows[1]
		self.assertEqual(
			(design["scheduled_value"], design["billed_to_date"], design["balance"]),
			(1333.33, 1100.0, 233.33),
		)
		self.assertEqual(design["percent_billed"], 82.5)
		self.assertEqual(rows[-1]["percent_billed"], 50.0)
		# the groups add up to every line in whole cents
		self.assertEqual(sum(round(r["scheduled_value"] * 100) for r in rows), 365072)

	def test_one_row_per_line(self):
		rows = br.billed_rows(self.LINES, "Line")
		self.assertEqual([r["label"] for r in rows][:2], ["1.1 Schematic design", "1.2 Project management"])
		tile = rows[3]
		self.assertEqual((tile["by_others"], tile["percent_billed"]), (1, None))

	def test_one_row_per_schedule(self):
		rows = br.schedule_summary_rows(
			[
				{"name": "SOV-2", "title": "Fountain", "contract_sum_to_date": 5000, "billed_to_date": 5000},
				{"name": "SOV-1", "project": "PRJ-1", "contract_sum_to_date": 0, "billed_to_date": 0},
			]
		)
		self.assertEqual([r["schedule_of_values"] for r in rows], ["SOV-1", "SOV-2"])
		self.assertEqual((rows[0]["label"], rows[0]["percent_billed"]), ("PRJ-1", None))
		self.assertEqual(rows[1]["percent_billed"], 100.0)


def _constant(src, name):
	for node in ast.parse(src).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	return None


class TestReportDefinitions(unittest.TestCase):
	def test_every_role_may_run_a_report_on_the_reference_doctype(self):
		for name in NAMES:
			doc, _src = report(name)
			self.assertEqual(
				(doc["report_type"], doc["module"], doc["is_standard"]),
				("Script Report", "Estimating", "Yes"),
			)
			perms = doctype_json(doc["ref_doctype"])["permissions"]
			may = {p["role"] for p in perms if p.get("report") and p.get("read") and not p.get("permlevel")}
			for role in doc["roles"]:
				self.assertIn(
					role["role"], may, f"{name}: {role['role']} cannot report on {doc['ref_doctype']}"
				)

	def test_every_field_read_is_permlevel_0(self):
		reads = {
			"Retainage Held": {"SOV_FIELDS": "Schedule of Values", "APP_FIELDS": "Pay Application"},
			"Pay Applications Outstanding": {
				"SOV_FIELDS": "Schedule of Values",
				"APP_FIELDS": "Pay Application",
			},
			"Billed vs Scheduled": {
				"SOV_FIELDS": "Schedule of Values",
				"LINE_FIELDS": "Schedule of Values Line",
			},
		}
		standard = {"name", "idx", "docstatus"}
		for name, constants in reads.items():
			_doc, src = report(name)
			for const, doctype in constants.items():
				fields = _constant(src, const)
				self.assertTrue(fields, f"{name}.{const}")
				meta = {f["fieldname"]: f for f in doctype_json(doctype)["fields"]}
				for field in fields:
					if field in standard:
						continue
					self.assertIn(field, meta, f"{name}: {doctype}.{field}")
					self.assertEqual(meta[field].get("permlevel", 0), 0, f"{name}: {doctype}.{field}")

	def test_reports_using_other_doctypes_admit_only_roles_that_read_them(self):
		# Retainage Held and Outstanding also read the other doctype; get_list would refuse a role
		# that cannot read it, so every role on the report must read both.
		for name in ("Retainage Held", "Pay Applications Outstanding"):
			doc, _src = report(name)
			for doctype in ("Schedule of Values", "Pay Application"):
				perms = doctype_json(doctype)["permissions"]
				readers = {p["role"] for p in perms if p.get("read") and not p.get("permlevel")}
				for role in doc["roles"]:
					self.assertIn(role["role"], readers, f"{name}: {role['role']} cannot read {doctype}")

	def test_the_workspace_card_links_every_report(self):
		path = os.path.join(
			APP, "project_enhancements", "workspace", "project_enhancements", "project_enhancements.json"
		)
		with open(path, encoding="utf-8") as fh:
			ws = json.load(fh)
		reports = {
			l["link_to"] for l in ws["links"] if l.get("link_type") == "Report" and l.get("is_query_report")
		}
		self.assertTrue(set(NAMES) <= reports)
		self.assertGreaterEqual(ws["modified"], "2026-11-23")


if __name__ == "__main__":
	unittest.main()
