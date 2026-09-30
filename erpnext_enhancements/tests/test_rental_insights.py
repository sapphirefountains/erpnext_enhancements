"""Bench-free tests for the rental fleet's KPIs, calendar feeds and assistant tools (v1.567.0).

Source-level, no ``frappe`` import. (The utilization arithmetic itself, ``rental_rules.covered_hours``,
is executed in ``test_rental_rules``.)

Run: python -m unittest erpnext_enhancements.tests.test_rental_insights -v

The one that matters most: :meth:`TestCalendarFeed.test_the_key_is_compared_in_constant_time_and_required`
— the feed is guest-readable by design (a calendar app cannot log in) and names customers, so the
secret in its URL is the only thing between the fleet's schedule and the internet.
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
AM = APP / "asset_management"


def functions(path):
	return {n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


def src(node):
	return ast.unparse(node)


class TestCalendarFeed(unittest.TestCase):
	fns = functions(AM / "rental_calendar.py")

	def test_the_key_is_compared_in_constant_time_and_required(self):
		fn = self.fns["feed"]
		body = src(fn)
		self.assertIn("hmac.compare_digest(str(feed_key), stored)", body)
		self.assertIn("if not stored or not feed_key or (not hmac.compare_digest", body)
		# The refusal comes before any event is read.
		self.assertLess(body.index("compare_digest"), body.index("fleet_events("))

	def test_guest_get_rate_limited(self):
		decorators = [src(d) for d in self.fns["feed"].decorator_list]
		self.assertIn("frappe.whitelist(allow_guest=True, methods=['GET'])", decorators)
		self.assertTrue(any(d.startswith("rate_limit(") for d in decorators))

	def test_the_key_is_strong_and_only_managers_see_it(self):
		fn = self.fns["feed_links"]
		body = src(fn)
		self.assertIn("secrets.token_urlsafe(32)", body)
		self.assertEqual(src(fn.body[1]), "frappe.only_for(MANAGE_ROLES)")
		self.assertIn("frappe.whitelist(methods=['POST'])", [src(d) for d in fn.decorator_list])

	def test_the_key_is_stored_encrypted_and_hidden(self):
		doc = json.loads((AM / "doctype" / "rental_settings" / "rental_settings.json").read_text(encoding="utf-8"))
		field = next(f for f in doc["fields"] if f["fieldname"] == "calendar_feed_key")
		self.assertEqual(field["fieldtype"], "Password")
		self.assertEqual(field.get("hidden"), 1)

	def test_the_request_key_is_not_a_frappe_reserved_name(self):
		"""Frappe consumes ``sid`` (and friends) before the endpoint sees them."""
		args = [a.arg for a in self.fns["feed"].args.args]
		for reserved in ("sid", "cmd", "usr", "pwd", "key", "token"):
			self.assertNotIn(reserved, args)

	def test_released_entries_drop_out(self):
		self.assertIn("ab.docstatus < 2", src(self.fns["fleet_events"]))


class TestKpis(unittest.TestCase):
	def test_rental_kpis_cannot_break_the_snapshot(self):
		snapshots = (APP / "kpi_dashboards" / "snapshots.py").read_text(encoding="utf-8")
		start = snapshots.index("rental_fleet_metrics")
		block = snapshots[snapshots.rindex("try:", 0, start) : snapshots.index("except Exception:", start) + 60]
		self.assertIn("frappe.log_error", block)

	def test_kpi_keys_are_new(self):
		keys = re.findall(r'\(\s*"([a-z0-9_]+)",', (AM / "rental_kpis.py").read_text(encoding="utf-8"))
		self.assertTrue(keys)
		snapshots = (APP / "kpi_dashboards" / "snapshots.py").read_text(encoding="utf-8")
		for key in keys:
			self.assertNotIn(f'"{key}"', snapshots, key)
		self.assertEqual(len(keys), len(set(keys)))

	def test_utilization_counts_firm_rentals_only(self):
		body = src(functions(AM / "rental_kpis.py")["_rental_windows"])
		self.assertIn("docstatus = 1", body)
		self.assertIn("booking_type = 'Rental'", body)


class TestAssistantTools(unittest.TestCase):
	def test_registered_and_read_only(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		gate = (APP / "assistant_tools" / "_gate.py").read_text(encoding="utf-8")
		for name, cls in (("rental_board", "RentalBoard"), ("rental_availability", "RentalAvailability")):
			self.assertIn(f"erpnext_enhancements.assistant_tools.{name}.{cls}", hooks)
			readonly = gate[gate.index("EXPLICIT_READONLY = {") : gate.index("}", gate.index("EXPLICIT_READONLY = {"))]
			self.assertIn(f'"{name}"', readonly)

	def test_the_board_checks_permission(self):
		body = src(functions(AM / "rental_planner.py")["board_summary"])
		self.assertIn("frappe.has_permission('Rental Booking', 'read', throw=True)", body)

	def test_availability_goes_through_the_permission_checked_endpoint(self):
		source = (APP / "assistant_tools" / "rental_availability.py").read_text(encoding="utf-8")
		self.assertIn("get_availability(str(delivery), str(takedown))", source)


if __name__ == "__main__":
	unittest.main()
