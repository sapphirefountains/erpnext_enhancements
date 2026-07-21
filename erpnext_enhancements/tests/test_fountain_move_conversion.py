"""Bench-backed tests for the fountain-move conversion engine.

These need a real site: the whole point is the interaction between our code and
erpnext's own hooks (Contact autoname reading ``links[0]``, Address recomputing
``custom_full_address``, Lead minting a stray Contact, Opportunity's mandatory
fields), and stubbing those out would test nothing worth testing.

Run on a bench — ``bench run-tests`` is broken under Python 3.14, so use the
bench-execute wrapper at the bottom (shape copied from ``test_contacts_ux``)::

    bench --site dev.localhost execute erpnext_enhancements.tests.test_fountain_move_conversion.run

Everything rolls back at the end. ``frappe.enqueue`` is patched throughout so
conversion runs inline instead of vanishing into a worker.
"""

import itertools
import unittest
from unittest.mock import patch

import frappe

from erpnext_enhancements.crm_enhancements.fountain_move import conversion, matching

LOCATION = "Cactus & Tropicals Draper"

#: Marks every record this suite creates, so tearDown can find and remove them.
TAG = "fmtest"

#: Unique identity per fixture.
#:
#: This matters more than it looks. ``run_conversion`` commits after every step
#: (deliberately — see its docstring), so ``frappe.db.rollback()`` does NOT undo
#: what a test created. Sharing an email or a PHONE between fixtures therefore
#: means the duplicate matcher legitimately reuses the previous test's Customer,
#: and assertions about names and counts fail for reasons that have nothing to do
#: with the code under test. Every fixture gets its own email and its own number.
_counter = itertools.count(1)


def _make_request(**overrides):
	"""Insert a Fountain Move Request as if the public form had produced it."""
	unique = next(_counter)
	values = {
		"doctype": "Fountain Move Request",
		"status": "New",
		"source_channel": "Public Form",
		"first_name": "Testy",
		"last_name": f"McTestface{unique}",
		"email": f"{TAG}-{unique}@example.com",
		# 801-555-XXXX, unique per fixture and a valid 10-digit NANP number.
		"phone": f"(801) 555-{4000 + unique:04d}",
		"property_type": "Residential",
		"purchase_location": LOCATION,
		"address_line1": f"{700 + unique} Evergreen Terrace",
		"city": "Draper",
		"state": "UT",
		"pincode": "84020",
		"country": "United States",
		"fountain_weight_lbs": 450,
		"water_access": 1,
		"electricity_access": 0,
		"contact_consent": 1,
		"terms_accepted": 1,
		"turnstile_verdict": "Passed",
	}
	values.update(overrides)
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


def _cleanup(request_name):
	"""Delete everything one request produced.

	Necessary because the conversion commits: a rollback leaves real Customers,
	Leads and Opportunities behind on the dev site, and the next run then matches
	against them.
	"""
	request = frappe.db.get_value(
		"Fountain Move Request",
		request_name,
		[
			"created_opportunity",
			"created_lead",
			"created_address",
			"created_contact",
			"created_customer",
		],
		as_dict=True,
	)
	if not request:
		return

	# Reverse dependency order.
	for doctype, name in (
		("Opportunity", request.created_opportunity),
		("Lead", request.created_lead),
		("Address", request.created_address),
		("Contact", request.created_contact),
		("Customer", request.created_customer),
	):
		if not name:
			continue
		try:
			frappe.delete_doc(doctype, name, force=1, ignore_permissions=True, delete_permanently=True)
		except Exception:
			pass

	try:
		frappe.delete_doc(
			"Fountain Move Request", request_name, force=1, ignore_permissions=True,
			delete_permanently=True,
		)
	except Exception:
		pass
	frappe.db.commit()


def _settings_ready():
	"""Ensure the defaults conversion needs are present, without clobbering config."""
	settings = frappe.get_single("ERPNext Enhancements Settings")
	if not settings.fmr_default_owner:
		settings.fmr_default_owner = "Administrator"
	settings.save(ignore_permissions=True)
	return settings


class FountainMoveConversionTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_settings_ready()

	def setUp(self):
		# Conversion enqueues the Drive mirror; run everything inline.
		self.enqueue_patch = patch("frappe.enqueue")
		self.enqueue_patch.start()
		self.requests = []

	def track(self, request):
		"""Register a request for cleanup and return it."""
		self.requests.append(request.name)
		return request

	def tearDown(self):
		self.enqueue_patch.stop()
		frappe.db.rollback()
		# Rollback is not enough on its own: run_conversion commits per step.
		for name in self.requests:
			_cleanup(name)

	# --- happy path ---------------------------------------------------------

	def test_creates_all_five_records_correctly_linked(self):
		request = self.track(_make_request())
		conversion.run_conversion(request.name)
		request.reload()

		self.assertEqual(request.status, "Converted", request.error)
		for field in (
			"created_customer",
			"created_address",
			"created_contact",
			"created_lead",
			"created_opportunity",
		):
			self.assertTrue(request.get(field), f"{field} was not set")

		customer = frappe.get_doc("Customer", request.created_customer)
		self.assertEqual(customer.customer_name, f"Testy {request.last_name} Residence")
		self.assertEqual(customer.customer_type, "Residential")

		# Address title is recomputed by the before_save hook — never set by us.
		address = frappe.get_doc("Address", request.created_address)
		self.assertIn(request.address_line1, address.custom_full_address)

		# The Contact's name proves the Customer link was appended BEFORE insert:
		# autoname reads links[0], so a link added afterwards would not appear.
		contact = frappe.get_doc("Contact", request.created_contact)
		self.assertIn(customer.name, contact.name)
		self.assertEqual(contact.custom_email, request.email)
		self.assertFalse(contact.get("email_id"), "must write custom_email, not the hidden email_id")

		opportunity = frappe.get_doc("Opportunity", request.created_opportunity)
		self.assertEqual(opportunity.opportunity_from, "Customer")
		self.assertEqual(opportunity.party_name, customer.name)
		self.assertEqual(len(opportunity.custom_value_stream), 1)
		self.assertTrue(opportunity.opportunity_owner)

		lead = frappe.get_doc("Lead", request.created_lead)
		self.assertEqual(lead.status, "Converted")

	def test_commercial_customer_name_has_no_residence_suffix(self):
		request = self.track(_make_request(property_type="Commercial"))
		conversion.run_conversion(request.name)
		request.reload()
		customer = frappe.get_doc("Customer", request.created_customer)
		self.assertEqual(customer.customer_name, f"Testy {request.last_name}")

	def test_exactly_one_contact_is_created(self):
		"""erpnext's Lead.before_insert mints its own Contact unless suppressed."""
		before = frappe.db.count("Contact")
		request = self.track(_make_request())
		conversion.run_conversion(request.name)
		request.reload()
		self.assertEqual(request.status, "Converted", request.error)
		self.assertEqual(frappe.db.count("Contact"), before + 1)

	def test_records_are_not_owned_by_guest(self):
		"""enqueue captures frappe.session.user, and the submitter is Guest.
		Without the explicit set_user in run_conversion every master would be
		owned by Guest — this is the only way that reproduces."""
		request = self.track(_make_request())
		original_user = frappe.session.user
		try:
			frappe.set_user("Guest")
			conversion.run_conversion(request.name)
		finally:
			frappe.set_user(original_user)

		request.reload()
		self.assertEqual(request.status, "Converted", request.error)
		self.assertNotEqual(frappe.db.get_value("Customer", request.created_customer, "owner"), "Guest")
		self.assertNotEqual(frappe.db.get_value("Opportunity", request.created_opportunity, "owner"), "Guest")

	# --- reuse --------------------------------------------------------------

	def test_second_request_reuses_the_customer_and_contact(self):
		first = self.track(_make_request())
		conversion.run_conversion(first.name)
		first.reload()

		second = self.track(_make_request(email=first.email, address_line1="99 New Road"))
		conversion.run_conversion(second.name)
		second.reload()

		self.assertEqual(second.status, "Converted", second.error)
		self.assertEqual(second.created_customer, first.created_customer)
		self.assertEqual(second.created_contact, first.created_contact)
		self.assertTrue(second.reused_customer)

		# A move has a new destination and a new job.
		self.assertNotEqual(second.created_address, first.created_address)
		self.assertNotEqual(second.created_opportunity, first.created_opportunity)

		# Reuse must not pile up duplicate Dynamic Link rows.
		contact = frappe.get_doc("Contact", second.created_contact)
		customer_links = [
			link for link in contact.links
			if link.link_doctype == "Customer" and link.link_name == second.created_customer
		]
		self.assertEqual(len(customer_links), 1)

	def test_phone_match_reuses_the_account(self):
		first = self.track(_make_request(phone="(801) 555-7777"))
		conversion.run_conversion(first.name)
		first.reload()

		# Different email, same number written differently.
		second = self.track(_make_request(phone="801-555-7777"))
		conversion.run_conversion(second.name)
		second.reload()

		self.assertEqual(second.created_customer, first.created_customer)
		self.assertEqual(second.match_basis, "Phone")

	# --- ambiguity ----------------------------------------------------------

	def test_two_matching_customers_stop_for_review(self):
		"""Guessing here writes a stranger's address onto someone else's account."""
		shared = "ambiguous@example.com"
		for suffix in ("One", "Two"):
			customer = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": f"Ambiguous {suffix}",
					"customer_type": "Residential",
					"custom_accounts_email_address": shared,
				}
			)
			customer.flags.ignore_mandatory = True
			customer.insert(ignore_permissions=True)

		request = self.track(_make_request(email=shared))
		conversion.run_conversion(request.name)
		request.reload()

		self.assertEqual(request.status, "Duplicate Review")
		self.assertFalse(request.created_customer)
		self.assertFalse(request.created_opportunity)
		self.assertTrue(request.match_candidates)

	# --- failure and retry --------------------------------------------------

	def test_failure_parks_the_row_and_retry_resumes(self):
		request = self.track(_make_request())

		with patch.object(
			conversion, "_create_opportunity", side_effect=Exception("boom")
		):
			conversion.run_conversion(request.name)
		request.reload()

		self.assertEqual(request.status, "Failed")
		self.assertIn("boom", request.error or "")
		# Everything before the failing step survived — that is what makes the
		# retry a resume rather than a duplicate run.
		self.assertTrue(request.created_customer)
		self.assertTrue(request.created_contact)
		self.assertFalse(request.created_opportunity)

		customer_before = request.created_customer
		conversion.run_conversion(request.name, force=1)
		request.reload()

		self.assertEqual(request.status, "Converted", request.error)
		self.assertEqual(request.created_customer, customer_before, "retry must reuse, not recreate")
		self.assertTrue(request.created_opportunity)

	def test_spam_creates_nothing(self):
		request = self.track(
			_make_request(honeypot_tripped=1, spam_reason="honeypot")
		)
		conversion.run_conversion(request.name)
		request.reload()

		self.assertEqual(request.status, "Spam")
		self.assertFalse(request.created_customer)
		self.assertFalse(request.created_lead)
		self.assertFalse(request.created_opportunity)

	# --- matcher ------------------------------------------------------------

	def test_matcher_never_writes(self):
		counts_before = {
			doctype: frappe.db.count(doctype)
			for doctype in ("Customer", "Contact", "Lead", "Address", "Opportunity")
		}
		matching.resolve_party(self.track(_make_request()))
		for doctype, before in counts_before.items():
			self.assertEqual(frappe.db.count(doctype), before, f"{doctype} changed during matching")


def run():
	"""Bench-execute entry point (``bench run-tests`` is broken on Python 3.14)."""
	suite = unittest.TestSuite()
	loader = unittest.TestLoader()
	suite.addTests(loader.loadTestsFromTestCase(FountainMoveConversionTest))
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	frappe.db.rollback()
	if not result.wasSuccessful():
		raise Exception(
			f"fountain move conversion tests failed: "
			f"{len(result.failures)} failure(s), {len(result.errors)} error(s)"
		)
	return f"OK ({result.testsRun} tests)"
