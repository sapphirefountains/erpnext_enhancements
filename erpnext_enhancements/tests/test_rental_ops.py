"""Bench-free tests for event-rental operations (v1.566.0): crew tasks, checklists, deposit release,
customer reminders. Source-level, no ``frappe`` import.

Run: python -m unittest erpnext_enhancements.tests.test_rental_ops -v

The ones that matter most:

* :meth:`TestDeposit.test_no_request_can_switch_the_permission_check_off` — a whitelisted function
  with a ``check_permission`` argument can be called with JSON ``false``.
* :meth:`TestDeposit.test_only_refund_roles_move_money` and ``test_the_refund_never_exceeds_the_payment``.
* :meth:`TestTasks.test_the_project_is_widened_before_any_task_is_saved` — ERPNext refuses a Task
  outside its Project's expected dates, and cleaning always falls after take-down.
* :meth:`TestReminders.test_every_reminder_ships_off` — emailing customers is switched on on purpose.
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
AM = APP / "asset_management"


def functions(path):
	return {n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


def src(node):
	return ast.unparse(node)


def statements(fn):
	"""The function's statements without its docstring (which may name what it avoids)."""
	body = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) else fn.body
	return "\n".join(src(s) for s in body)


class TestTasks(unittest.TestCase):
	fns = functions(AM / "rental_logistics.py")

	def test_the_project_is_widened_before_any_task_is_saved(self):
		body = statements(self.fns["sync_tasks"])
		self.assertLess(body.index("_widen_project("), body.index("task.save()"))

	def test_widening_never_narrows(self):
		body = statements(self.fns["_widen_project"])
		self.assertIn("> start", body)
		self.assertIn("< end", body)

	def test_a_completed_task_is_never_touched(self):
		body = statements(self.fns["sync_tasks"])
		self.assertIn("row.status == 'Completed'", body)
		self.assertLess(body.index("row.status == 'Completed'"), body.index("task.update("))

	def test_a_released_booking_cancels_its_open_tasks(self):
		body = statements(self.fns["sync_tasks"])
		self.assertIn("rules.RELEASED_STATUSES", body)
		self.assertIn("'Cancelled'", body)

	def test_booking_save_side_effects_never_raise(self):
		fn = self.fns["after_booking_save"]
		tries = [n for n in fn.body if isinstance(n, ast.Try)]
		self.assertEqual(len(tries), 1)
		self.assertIn("sync_tasks(booking)", src(tries[0]))
		self.assertNotIn("raise", "\n".join(src(h) for h in tries[0].handlers))

	def test_assignments_do_not_depend_on_the_callers_rights(self):
		self.assertIn("insert(ignore_permissions=True)", statements(self.fns["_assign"]))

	def test_digest_is_at_most_once_a_day_and_gated(self):
		body = statements(self.fns["send_crew_digests"])
		self.assertIn("settings().get('crew_digest')", body)
		self.assertLess(body.index("custom_rental_digest_sent_on', today"), body.index("_send_digest("))


class TestInspections(unittest.TestCase):
	def test_the_http_door_still_checks_permission_and_the_internal_one_is_not_whitelisted(self):
		fns = functions(APP / "api" / "booking.py")
		door = statements(fns["generate_inspection"])
		self.assertLess(door.index("check_permission('read')"), door.index("make_inspection("))
		self.assertEqual(fns["make_inspection"].decorator_list, [])

	def test_checklists_are_made_as_the_system(self):
		body = statements(functions(AM / "rental_logistics.py")["_generate"])
		self.assertIn("ignore_permissions=True", body)

	def test_the_return_sheet_starts_the_deposit_release_in_a_savepoint(self):
		source = (AM / "doctype" / "rental_inspection" / "rental_inspection.py").read_text(encoding="utf-8")
		self.assertIn("self.start_deposit_release()", source)
		self.assertIn('frappe.db.rollback(save_point="rental_deposit_release")', source)


class TestDeposit(unittest.TestCase):
	fns = functions(AM / "rental_deposit.py")

	def test_no_request_can_switch_the_permission_check_off(self):
		door = self.fns["prepare_release"]
		self.assertEqual([a.arg for a in door.args.args], ["booking", "deduction", "reason"])
		self.assertIn("doc.check_permission('write')", statements(door))
		self.assertEqual(self.fns["draft_release"].decorator_list, [])

	def test_only_refund_roles_move_money(self):
		for name in ("refund_deposit", "mark_refunded"):
			with self.subTest(fn=name):
				body = self.fns[name].body
				first = body[1] if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else body[0]
				self.assertEqual(src(first), "frappe.only_for(REFUND_ROLES)")
				self.assertIn("frappe.whitelist(methods=['POST'])", [src(d) for d in self.fns[name].decorator_list])

	def test_the_refund_never_exceeds_the_payment(self):
		body = statements(self.fns["refund_deposit"])
		self.assertLess(body.index("flt(payment.amount) - flt(payment.amount_refunded)"), body.index("create_refund("))

	def test_nothing_is_refunded_before_the_documents_are_posted(self):
		body = statements(self.fns["_check_ready"])
		self.assertIn("docstatus') != 1", body)
		self.assertLess(statements(self.fns["refund_deposit"]).index("_check_ready(doc)"), statements(self.fns["refund_deposit"]).index("create_refund("))

	def test_a_submitted_release_is_never_redrafted_and_links_are_cleared_before_deleting(self):
		body = statements(self.fns["draft_release"])
		self.assertIn("is already submitted", body)
		self.assertLess(body.index("doc.db_set({'deposit_credit_note': None"), body.index("frappe.delete_doc("))

	def test_the_deposit_return_carries_no_tax(self):
		body = statements(self.fns["_draft_credit_note"])
		self.assertIn("note.set('taxes', [])", body)
		self.assertIn("note.set('items', [row])", body)

	def test_a_deduction_needs_a_reason(self):
		self.assertIn("Say what the deduction is for", statements(self.fns["draft_release"]))


class TestReminders(unittest.TestCase):
	def test_every_reminder_ships_off(self):
		doc = json.loads((AM / "doctype" / "rental_settings" / "rental_settings.json").read_text(encoding="utf-8"))
		defaults = {f["fieldname"]: f.get("default") for f in doc["fields"]}
		for field in ("remind_week_out", "remind_site_prep", "remind_day_before", "send_thank_you"):
			self.assertEqual(defaults[field], "0", field)
		for field in ("crew_digest", "generate_inspections"):
			self.assertEqual(defaults[field], "1", field)

	def test_each_is_gated_and_stamped_once(self):
		body = statements(functions(AM / "rental_reminders.py")["run_daily"])
		for field, stamp in (
			("remind_week_out", "reminder_week_sent_on"),
			("remind_site_prep", "reminder_prep_sent_on"),
			("remind_day_before", "reminder_day_before_sent_on"),
			("send_thank_you", "thank_you_sent_on"),
		):
			self.assertIn(f"conf.get('{field}')", body)
			self.assertIn(f"'{stamp}'", body)
		self.assertIn("[stamp, 'is', 'not set']", statements(functions(AM / "rental_reminders.py")["_sweep"]))

	def test_the_ticked_by_default_switches_are_backfilled(self):
		source = (APP / "patches" / "backfill_rental_settings_defaults.py").read_text(encoding="utf-8")
		self.assertIn('"crew_digest"', source)
		self.assertIn('"generate_inspections"', source)
		self.assertIn(
			"erpnext_enhancements.patches.backfill_rental_settings_defaults",
			(APP / "patches.txt").read_text(encoding="utf-8"),
		)


class TestWiring(unittest.TestCase):
	def test_schedules(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		cron = hooks[hooks.index('"0 6 * * *": [') :]
		cron = cron[: cron.index("],")]
		self.assertLess(cron.index("generate_due_inspections"), cron.index("send_crew_digests"))
		self.assertIn("asset_management.rental_reminders.run_daily", hooks)

	def test_fixtures(self):
		fixtures = {f["name"]: f for f in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))}
		self.assertEqual(fixtures["Task-custom_rental_booking"]["options"], "Rental Booking")
		self.assertEqual(
			fixtures["Task-custom_rental_task_kind"]["options"].split("\n"), ["", "Delivery", "Setup", "Take-down", "Cleaning"]
		)
		kinds = fixtures["Sales Invoice-custom_rental_invoice_kind"]["options"].split("\n")
		for kind in ("Deposit", "Balance", "Damage", "Deposit Return"):
			self.assertIn(kind, kinds)

	def test_task_kinds_match_the_code(self):
		fixtures = {f["name"]: f for f in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))}
		source = (AM / "rental_logistics.py").read_text(encoding="utf-8")
		for kind in filter(None, fixtures["Task-custom_rental_task_kind"]["options"].split("\n")):
			self.assertIn(f'"{kind}"', source)


if __name__ == "__main__":
	unittest.main()
