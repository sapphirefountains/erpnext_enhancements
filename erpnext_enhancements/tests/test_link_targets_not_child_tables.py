"""Bench-free guard: no Link field points at a child-table DocType this app owns.

A child table (``istable: 1``) has no permissions of its own, so frappe refuses every direct read
of it: link search, link validation and ``get_list`` all fail with "Insufficient Permission", for
a System Manager too. ``Lead Source`` was ported into the app as a child table on 2026-06-09
although its records were always standalone (no parent), so the Lead Source field on Lead,
Opportunity and Customer could not be picked for four months; Nik hit it on 2026-10-14. v1.678.1
made it a standalone list again.

Checks every Link field in ``fixtures/custom_field.json`` and in this app's own DocType JSONs.

Run: python -m unittest erpnext_enhancements.tests.test_link_targets_not_child_tables
"""

import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

# Known and harmless, each with its reason. Add nothing here without one.
ALLOWED = {
	# A dead field on the Value Streams list pointing at the Value Stream child table (the rows of
	# the multi-select on Customer, Opportunity and Project). Empty on all 6 records on prod
	# (2026-10-14) and never read; removing it is a separate change.
	("Value Streams", "value_stream_link", "Value Stream"),
}


def _app_doctypes():
	out = {}
	for path in APP.glob("*/doctype/*/*.json"):
		if path.stem != path.parent.name:
			continue
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except (ValueError, UnicodeDecodeError):
			continue
		if data.get("doctype") == "DocType":
			out[data["name"]] = data
	return out


def links_to_child_tables(doctypes, custom_fields):
	"""(owner, fieldname, target) for each Link field whose target is a child table here."""
	child = {name for name, data in doctypes.items() if data.get("istable")}
	bad = []
	for row in custom_fields:
		if row.get("fieldtype") == "Link" and row.get("options") in child:
			bad.append((row["dt"], row["fieldname"], row["options"]))
	for name, data in doctypes.items():
		for field in data.get("fields") or []:
			if field.get("fieldtype") == "Link" and field.get("options") in child:
				bad.append((name, field["fieldname"], field["options"]))
	return sorted(bad)


class TestLinkTargetsAreNotChildTables(unittest.TestCase):
	def test_no_link_points_at_a_child_table(self):
		doctypes = _app_doctypes()
		self.assertIn("Lead Source", doctypes)
		custom_fields = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		found = links_to_child_tables(doctypes, custom_fields)
		self.assertEqual([link for link in found if link not in ALLOWED], [])
		# An allow-listed field that has gone needs its entry gone too.
		self.assertEqual(sorted(ALLOWED - set(found)), [])

	def test_lead_source_is_a_standalone_list_anyone_on_the_desk_can_pick_from(self):
		data = _app_doctypes()["Lead Source"]
		self.assertFalse(data.get("istable"))
		self.assertEqual(data.get("autoname"), "Prompt")
		roles = {p["role"]: p for p in data["permissions"]}
		self.assertTrue(roles["Desk User"].get("read") and roles["Desk User"].get("select"))
		self.assertFalse(roles["Desk User"].get("write") or roles["Desk User"].get("create"))
		for role in ("System Manager", "Sales Manager"):
			with self.subTest(role=role):
				self.assertTrue(roles[role].get("create") and roles[role].get("write"))

	def test_the_checker_finds_a_link_to_a_child_table(self):
		doctypes = {"Lead Source": {"istable": 1, "fields": []}, "Lead": {"fields": []}}
		rows = [
			{
				"dt": "Opportunity",
				"fieldname": "custom_lead_source",
				"fieldtype": "Link",
				"options": "Lead Source",
			}
		]
		self.assertEqual(
			links_to_child_tables(doctypes, rows), [("Opportunity", "custom_lead_source", "Lead Source")]
		)


if __name__ == "__main__":
	unittest.main()
