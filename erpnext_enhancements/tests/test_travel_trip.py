"""Integration tests for the Travel Management redesign (``travel_management``).

Uses ``FrappeTestCase`` against a real test DB (bench-only — not part of the
standalone CI job, same as the other FrappeTestCase suites here). Coverage:
per-diem math (multi-day with first/last-day percent, single day, override
rate, ineligible), financial rollups with mixed paid_by flags, traveler
validation (duplicates, single trip lead), expense-claim generation
(per-traveler split, dedupe stamps, idempotent re-run, stamp clearing when the
claim is deleted), and crew-scoped row-level permissions.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.travel_management import api as travel_api

COMPANY = "_Test Travel Co_"


class TestTravelTrip(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

		if not frappe.db.exists("Company", COMPANY):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": COMPANY,
					"abbr": "_TTC_",
					"default_currency": "USD",
					"country": "United States",
				}
			).insert()

		self.emp_a = self._make_employee("Trav", "Lead", "trav.lead@example.com")
		self.emp_b = self._make_employee("Trav", "Mate", None)

		settings = frappe.get_doc("Travel Settings")
		settings.auto_advance_statuses = 1
		settings.notifications_enabled = 0
		settings.mileage_rate = 0.5
		settings.set("per_diem_rates", [])
		settings.append(
			"per_diem_rates",
			{"travel_type": "Domestic", "daily_rate": 100, "first_last_day_percent": 50},
		)
		for fieldname in (
			"flight_expense_type",
			"hotel_expense_type",
			"ground_expense_type",
			"misc_expense_type",
			"per_diem_expense_type",
			"mileage_expense_type",
		):
			settings.set(fieldname, self._make_expense_type("_Test Travel Type_"))
		settings.save()

	def _make_employee(self, first, last, user_email):
		existing = frappe.db.get_value(
			"Employee", {"first_name": first, "last_name": last}, "name"
		)
		if existing:
			return existing
		if user_email and not frappe.db.exists("User", user_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": first,
					"send_welcome_email": 0,
					"roles": [{"role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		emp = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first,
				"last_name": last,
				"gender": "Other",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"company": COMPANY,
				"status": "Active",
				"user_id": user_email,
			}
		)
		emp.insert(ignore_permissions=True)
		return emp.name

	def _make_expense_type(self, name):
		if not frappe.db.exists("Expense Claim Type", name):
			frappe.get_doc(
				{"doctype": "Expense Claim Type", "expense_type": name}
			).insert(ignore_permissions=True)
		return name

	def _make_trip(self, **overrides):
		doc = frappe.get_doc(
			{
				"doctype": "Travel Trip",
				"purpose": "Test trip",
				"travel_type": "Domestic",
				"company": COMPANY,
				"start_date": "2026-07-01",
				"end_date": "2026-07-04",
				"travelers": [
					{"employee": self.emp_a, "is_trip_lead": 1},
					{"employee": self.emp_b},
				],
				**overrides,
			}
		)
		doc.insert()
		self.addCleanup(self._cleanup_trip, doc.name)
		return doc

	def _cleanup_trip(self, name):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Travel Trip", name):
			return
		for claim in frappe.get_all(
			"Expense Claim", filters={"custom_travel_trip": name}, pluck="name"
		):
			frappe.delete_doc("Expense Claim", claim, force=True)
		frappe.delete_doc("Travel Trip", name, force=True)

	# ---------------------------------------------------------------- per diem

	def test_per_diem_multi_day_with_partial_first_last_day(self):
		trip = self._make_trip()
		# 4 days inclusive: 2 full days @ 100 + 2 edge days @ 50% = 300
		self.assertEqual(trip.travelers[0].per_diem_amount, 300)
		self.assertEqual(trip.total_per_diem, 600)

	def test_per_diem_single_day_uses_partial_rate(self):
		trip = self._make_trip(
			start_date="2026-07-01",
			end_date="2026-07-01",
			travelers=[{"employee": self.emp_a, "is_trip_lead": 1}],
		)
		self.assertEqual(trip.travelers[0].per_diem_amount, 50)

	def test_per_diem_override_and_ineligible(self):
		trip = self._make_trip(
			travelers=[
				{"employee": self.emp_a, "is_trip_lead": 1, "per_diem_override_rate": 10},
				{"employee": self.emp_b, "per_diem_eligible": 0},
			]
		)
		# override rate: 2 full days @ 10 + 2 edge days @ 50% = 30
		self.assertEqual(trip.travelers[0].per_diem_amount, 30)
		self.assertEqual(trip.travelers[1].per_diem_amount, 0)

	def test_traveler_dates_default_and_scale_per_diem(self):
		trip = self._make_trip(
			travelers=[
				{"employee": self.emp_a, "is_trip_lead": 1},
				{"employee": self.emp_b, "from_date": "2026-07-02", "to_date": "2026-07-03"},
			]
		)
		self.assertEqual(str(trip.travelers[0].from_date), "2026-07-01")
		self.assertEqual(str(trip.travelers[0].to_date), "2026-07-04")
		# partial crew member: 2 days inclusive -> both edge days @ 50%
		self.assertEqual(trip.travelers[1].per_diem_amount, 100)

	# ---------------------------------------------------------------- rollups

	def test_rollups_split_company_vs_employee(self):
		trip = self._make_trip(
			flights=[
				{
					"airline": self._make_supplier("_Test Airline_"),
					"flight_number": "AA1",
					"estimated_cost": 400,
					"cost": 450,
					"paid_by": "Company",
				}
			],
			other_costs=[
				{
					"expense_date": "2026-07-02",
					"description": "Parking",
					"cost": 40,
					"paid_by": "Employee",
					"paid_by_traveler": self.emp_a,
				}
			],
			mileage=[
				{"traveler": self.emp_a, "date": "2026-07-02", "distance": 100}
			],
		)
		# mileage: 100 mi @ 0.5 = 50; per diem 300 x 2 travelers = 600
		self.assertEqual(trip.mileage[0].amount, 50)
		self.assertEqual(trip.total_company_paid, 450)
		self.assertEqual(trip.total_employee_paid, 40 + 600 + 50)
		self.assertEqual(trip.total_actual_cost, 450 + 40 + 600 + 50)
		self.assertEqual(trip.total_estimated_cost, 400 + 600 + 50)

	def _make_supplier(self, name):
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc(
				{"doctype": "Supplier", "supplier_name": name, "supplier_group": "All Supplier Groups"}
			).insert(ignore_permissions=True)
		return name

	# ------------------------------------------------------------- travelers

	def test_duplicate_travelers_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._make_trip(
				travelers=[{"employee": self.emp_a}, {"employee": self.emp_a}]
			)

	def test_single_trip_lead_enforced(self):
		trip = self._make_trip(
			travelers=[{"employee": self.emp_a}, {"employee": self.emp_b}]
		)
		self.assertEqual(trip.travelers[0].is_trip_lead, 1)  # auto-set
		trip.travelers[1].is_trip_lead = 1
		with self.assertRaises(frappe.ValidationError):
			trip.save()

	# --------------------------------------------------------- expense claims

	def test_expense_claim_per_traveler_with_dedupe_stamps(self):
		trip = self._make_trip(
			other_costs=[
				{
					"expense_date": "2026-07-02",
					"description": "Parking",
					"cost": 40,
					"paid_by": "Employee",
					"paid_by_traveler": self.emp_a,
				}
			]
		)
		claims = travel_api.create_expense_claims(trip.name)
		# both travelers have per diem; emp_a additionally has the parking row
		self.assertEqual(set(claims), {self.emp_a, self.emp_b})

		claim_a = frappe.get_doc("Expense Claim", claims[self.emp_a])
		self.assertEqual(claim_a.docstatus, 0)
		self.assertEqual(len(claim_a.expenses), 2)  # parking + per diem
		self.assertEqual(claim_a.get("custom_travel_trip"), trip.name)

		trip.reload()
		self.assertEqual(trip.other_costs[0].expense_claim, claims[self.emp_a])
		self.assertTrue(trip.travelers[0].per_diem_claimed)
		self.assertEqual(trip.travelers[0].expense_claim, claims[self.emp_a])

		# idempotent: everything is stamped, nothing left to claim
		self.assertEqual(travel_api.create_expense_claims(trip.name), {})

	def test_claim_deletion_clears_stamps(self):
		trip = self._make_trip(
			travelers=[{"employee": self.emp_a, "is_trip_lead": 1}]
		)
		claims = travel_api.create_expense_claims(trip.name)
		frappe.delete_doc("Expense Claim", claims[self.emp_a], force=True)

		trip.reload()
		self.assertFalse(trip.travelers[0].expense_claim)
		self.assertFalse(trip.travelers[0].per_diem_claimed)
		# material is claimable again
		self.assertEqual(set(travel_api.create_expense_claims(trip.name)), {self.emp_a})

	# ------------------------------------------------------------ permissions

	def test_crew_scoping_in_list_and_form(self):
		trip = self._make_trip(
			travelers=[{"employee": self.emp_a, "is_trip_lead": 1}]
		)
		user = frappe.db.get_value("Employee", self.emp_a, "user_id")
		self.assertTrue(user)

		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		visible = frappe.get_list("Travel Trip", pluck="name")
		self.assertIn(trip.name, visible)
		self.assertTrue(frappe.has_permission("Travel Trip", "write", doc=trip.name))

		frappe.set_user("Administrator")
		stranger_trip = self._make_trip(
			travelers=[{"employee": self.emp_b, "is_trip_lead": 1}]
		)
		frappe.set_user(user)
		visible = frappe.get_list("Travel Trip", pluck="name")
		self.assertNotIn(stranger_trip.name, visible)
		self.assertFalse(frappe.has_permission("Travel Trip", "read", doc=stranger_trip.name))
