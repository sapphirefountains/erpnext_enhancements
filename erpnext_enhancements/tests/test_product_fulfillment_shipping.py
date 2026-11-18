"""Getting a product unit to the customer (v1.827.0, TASK-2026-03231).

Nikolas, 2026-11-18 (vault decisions/2026-11-18-estop-product-line.md, pick 7): a product order is
picked up at the shop, shipped by a carrier, or delivered by Sapphire. The Delivery Note records
which; the carrier and tracking number go in its own ``transporter`` and ``lr_no`` (relabeled
"Tracking Number"); the customer contact gets "Ready for pickup" or "Shipped" with the tracking link.

What goes wrong without these checks, concretely:

* the Shipped email's tracking button points at nothing, or at the wrong carrier, because "UPS store"
  or "Usps" (prod's supplier names) was not recognized, or a number pasted with spaces made a dead
  link;
* a tracking number with a quote in it lands unescaped in the email's ``href`` (the email macros
  escape text, never URLs);
* a carrier shipment is submitted with no tracking number, or a Sapphire delivery with nobody to
  deliver it, and the customer is emailed a link to nothing;
* the Sales Order's fulfillment field and the Delivery Note's drift apart in name, so ERPNext's Make
  Delivery Note stops carrying the customer's choice across;
* the new settings field is added without a backfill, and the next person to save the settings page
  switches the Shipped email off without knowing.

Rules are stub-free; the two email bodies render under plain Jinja from the repo (as
``test_email_design`` does); the hooks run on a ``frappe`` stub installed in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_product_fulfillment_shipping
"""

import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from erpnext_enhancements.product_fulfillment import shipping_rules as sr

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "erpnext_enhancements"
FIXTURES = APP / "fixtures"

_saved = {}
_STUBBED = ("frappe", "frappe.utils", "erpnext_enhancements.email_style")
_MODULES = (
	"erpnext_enhancements.product_fulfillment.settings",
	"erpnext_enhancements.product_fulfillment.serials",
	"erpnext_enhancements.product_fulfillment.qc_gate",
	"erpnext_enhancements.product_fulfillment.shipping",
)


class Thrown(Exception):
	pass


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as error:
			raise AttributeError(key) from error


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------


class TestWhichCarrier(unittest.TestCase):
	def test_prods_supplier_names_are_recognized(self):
		for name, carrier in (
			("UPS", sr.UPS),
			("UPS store", sr.UPS),
			("FedEx", sr.FEDEX),
			("FedEx Office", sr.FEDEX),
			("Usps", sr.USPS),
			("DHL", sr.DHL),
		):
			with self.subTest(name=name):
				self.assertEqual(sr.carrier_for(name, name), (carrier, None))

	def test_a_name_that_only_contains_the_letters_is_not_a_carrier(self):
		self.assertEqual(sr.carrier_for("Groups Inc", "Groups Inc"), (sr.OTHER, None))

	def test_the_carrier_table_wins(self):
		table = [
			{
				"transporter": "XPO ltl",
				"carrier": "Other",
				"tracking_url": "https://track.example.com/?pro={number}",
			}
		]
		self.assertEqual(
			sr.carrier_for("XPO ltl", "XPO ltl", table), (sr.OTHER, "https://track.example.com/?pro={number}")
		)
		table = [{"transporter": "UPS store", "carrier": "FedEx", "tracking_url": ""}]
		self.assertEqual(sr.carrier_for("UPS store", "UPS store", table), (sr.FEDEX, None))

	def test_a_table_template_must_be_a_link_with_the_number(self):
		for bad in ("javascript:alert(1)//{number}", "https://track.example.com/", "ftp://x/{number}"):
			table = [{"transporter": "X", "carrier": "Other", "tracking_url": bad}]
			self.assertEqual(sr.carrier_for("X", "X", table), (sr.OTHER, None), bad)

	def test_no_transporter_is_no_carrier(self):
		self.assertEqual(sr.carrier_for(None, None), (None, None))


class TestTrackingLinks(unittest.TestCase):
	def test_each_carriers_public_page(self):
		self.assertEqual(
			sr.tracking_url(sr.UPS, "1z 999 aa1 0123456784"),
			"https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784",
		)
		self.assertEqual(
			sr.tracking_url(sr.FEDEX, "1234-5678-9012"),
			"https://www.fedex.com/wtrk/track/?trknbr=123456789012",
		)
		self.assertTrue(sr.tracking_url(sr.USPS, "9400111899223817").startswith("https://tools.usps.com/"))
		self.assertIn("tracking-id=1234567890", sr.tracking_url(sr.DHL, "1234567890"))

	def test_ups_and_fedex_are_carrier_trackings_own(self):
		from erpnext_enhancements.carrier_tracking import carriers

		self.assertEqual(sr.TRACKING_URLS[sr.UPS], carriers.PUBLIC_TRACKING_URLS[carriers.UPS])
		self.assertEqual(sr.TRACKING_URLS[sr.FEDEX], carriers.PUBLIC_TRACKING_URLS[carriers.FEDEX])

	def test_a_template_wins_and_other_without_one_has_none(self):
		self.assertEqual(
			sr.tracking_url(sr.OTHER, "PRO12345", "https://t.example.com/{number}"),
			"https://t.example.com/PRO12345",
		)
		self.assertEqual(sr.tracking_url(sr.OTHER, "PRO12345"), "")

	def test_only_letters_and_digits_make_a_link(self):
		for bad in ('12345"onmouseover="x', "<script>", "12", ""):
			self.assertEqual(sr.tracking_url(sr.UPS, bad), "", bad)


class TestWhatAMethodNeeds(unittest.TestCase):
	def test_a_configured_product_must_say_how_it_travels(self):
		self.assertEqual(sr.method_problems(None, required=True), [sr.NEEDS_METHOD])
		self.assertEqual(sr.method_problems(None, required=False), [])

	def test_carrier_shipping_needs_the_carrier_and_the_number(self):
		self.assertEqual(sr.method_problems(sr.CARRIER), [sr.NEEDS_CARRIER, sr.NEEDS_TRACKING])
		self.assertEqual(
			sr.method_problems(sr.CARRIER, "UPS", " - "), [sr.NEEDS_TRACKING], "dashes alone are no number"
		)
		self.assertEqual(sr.method_problems(sr.CARRIER, "UPS", "1Z999AA10123456784"), [])

	def test_sapphire_delivery_needs_a_date_and_a_person(self):
		self.assertEqual(sr.method_problems(sr.SAPPHIRE), [sr.NEEDS_DATE, sr.NEEDS_PERSON])
		self.assertEqual(
			sr.method_problems(sr.SAPPHIRE, delivery_date="2026-11-20", delivery_person="a@example.com"), []
		)

	def test_pickup_needs_nothing_more(self):
		self.assertEqual(sr.method_problems(sr.PICKUP, required=True), [])

	def test_every_problem_has_a_sentence(self):
		for code in (sr.NEEDS_METHOD, sr.NEEDS_CARRIER, sr.NEEDS_TRACKING, sr.NEEDS_DATE, sr.NEEDS_PERSON):
			self.assertTrue(sr.PROBLEM_MESSAGES.get(code), code)

	def test_which_email(self):
		self.assertEqual(sr.email_for(sr.PICKUP), sr.READY_FOR_PICKUP)
		self.assertEqual(sr.email_for(sr.CARRIER), sr.SHIPPED)
		self.assertIsNone(sr.email_for(sr.SAPPHIRE))
		self.assertIsNone(sr.email_for(None))

	def test_first_email(self):
		self.assertEqual(sr.first_email("", None, "not an email", "buyer@example.com"), "buyer@example.com")
		self.assertIsNone(sr.first_email("", "a,b@example.com"))


# ---------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------


def _load(path):
	return json.loads(Path(path).read_text(encoding="utf-8"))


class TestSchema(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.fields = {r["name"]: r for r in _load(FIXTURES / "custom_field.json")}
		cls.setters = {r["name"]: r for r in _load(FIXTURES / "property_setter.json")}

	def test_the_method_options_are_the_rules(self):
		for name in ("Delivery Note-custom_fulfillment_method", "Sales Order-custom_fulfillment_method"):
			options = [o for o in self.fields[name]["options"].split("\n") if o]
			self.assertEqual(tuple(options), sr.METHODS, name)

	def test_the_sales_order_carries_the_choice_to_the_delivery_note(self):
		# ERPNext's make_delivery_note copies every same-named field across.
		self.assertEqual(
			self.fields["Sales Order-custom_fulfillment_method"]["fieldname"],
			self.fields["Delivery Note-custom_fulfillment_method"]["fieldname"],
		)
		self.assertEqual(self.fields["Sales Order-custom_fulfillment_method"]["no_copy"], 0)

	def test_lr_no_reads_tracking_number_and_transporter_carrier(self):
		self.assertEqual(self.setters["Delivery Note-lr_no-label"]["value"], "Tracking Number")
		self.assertEqual(self.setters["Delivery Note-transporter-label"]["value"], "Carrier")

	def test_the_new_fields_sit_in_the_transport_section_without_a_break(self):
		for name in (
			"Delivery Note-custom_fulfillment_method",
			"Delivery Note-custom_tracking_url",
			"Delivery Note-custom_delivery_date",
			"Delivery Note-custom_delivery_person",
			"Delivery Note-custom_ready_email_sent_on",
			"Delivery Note-custom_shipped_email_sent_on",
		):
			self.assertNotIn(
				self.fields[name]["fieldtype"], ("Section Break", "Tab Break", "Column Break"), name
			)
		self.assertEqual(
			self.fields["Delivery Note-custom_fulfillment_method"]["insert_after"], "transporter_info"
		)

	def test_the_carrier_table_offers_the_rules_carriers(self):
		carrier = _load(APP / "product_fulfillment" / "doctype" / "product_carrier" / "product_carrier.json")
		select = next(f for f in carrier["fields"] if f["fieldname"] == "carrier")
		self.assertEqual(tuple(select["options"].split("\n")), sr.CARRIERS)

	def test_the_settings_json_is_newer_than_the_release_that_made_it(self):
		# A DocType JSON whose modified is not newer than the site's row is skipped by the importer.
		settings = _load(
			APP / "product_fulfillment" / "doctype" / "product_build_settings" / "product_build_settings.json"
		)
		self.assertGreater(settings["modified"], "2026-11-18 12:00:00.000000")
		names = [f["fieldname"] for f in settings["fields"]]
		for field in ("send_shipped_email", "pickup_instructions", "carriers"):
			self.assertIn(field, names)

	def test_the_new_default_is_backfilled(self):
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.patches.backfill_product_build_settings_defaults\n", patches)
		self.assertIn(
			'"erpnext_enhancements.patches.backfill_product_build_settings_defaults.backfill_product_build_settings_defaults"',
			hooks,
		)
		source = (APP / "patches" / "backfill_product_build_settings_defaults.py").read_text(encoding="utf-8")
		self.assertIn(
			"select field from tabSingles where doctype = %s", source, "read tabSingles, never get_value"
		)


# ---------------------------------------------------------------------------------------------
# The two emails
# ---------------------------------------------------------------------------------------------


def _render(name, **context):
	env = Environment(loader=FileSystemLoader(str(REPO)), autoescape=False)
	return env.get_template(f"erpnext_enhancements/templates/emails/product_fulfillment/{name}").render(
		**context
	)


UNITS = [["STILLWATER E-Stop (PDT-0040-1-1-1-2-0)", "SF-26-00001"]]


class TestTheEmails(unittest.TestCase):
	def test_ready_for_pickup(self):
		html = _render(
			"ready_for_pickup.html",
			greeting_name="Dana",
			units=UNITS,
			references=[("Order", "SAL-ORD-2026-00001")],
			pickup_instructions="Come to the shop door, 8 to 4.",
		)
		for text in (
			"Hi Dana,",
			"ready to pick up",
			"SF-26-00001",
			"PDT-0040-1-1-1-2-0",
			"SAL-ORD-2026-00001",
			"Come to the shop door, 8 to 4.",
			"one year, parts and labor",
		):
			self.assertIn(text, html)

	def test_shipped_has_the_tracking_button(self):
		url = sr.tracking_url(sr.UPS, "1Z999AA10123456784")
		html = _render(
			"shipped.html",
			greeting_name=None,
			units=UNITS,
			references=[],
			carrier="UPS",
			tracking_number="1Z999AA10123456784",
			tracking_url=url,
			ship_date="11/18/2026",
		)
		self.assertIn("Hello,", html)
		self.assertIn(f'href="{url}"', html)
		self.assertIn("TRACK YOUR PACKAGE", html)
		self.assertIn("If the button does not work", html)

	def test_shipped_without_a_link_has_no_button(self):
		html = _render(
			"shipped.html",
			units=UNITS,
			references=[],
			carrier="XPO",
			tracking_number="PRO1",
			tracking_url="",
			ship_date="",
		)
		self.assertNotIn("TRACK YOUR PACKAGE", html)
		self.assertIn("PRO1", html)

	def test_customer_values_are_escaped(self):
		html = _render(
			"ready_for_pickup.html",
			greeting_name="<b>Dana</b>",
			units=[["<i>x</i>", "SF"]],
			references=[],
			pickup_instructions="<script>",
		)
		self.assertNotIn("<b>Dana</b>", html)
		self.assertNotIn("<i>x</i>", html)
		self.assertNotIn("<script>", html)


# ---------------------------------------------------------------------------------------------
# The hooks, on a stub
# ---------------------------------------------------------------------------------------------


def setUpModule():
	for name in (*_STUBBED, *_MODULES):
		_saved[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.bold = lambda text: text

	def throw(message, exc=None, title=None, **kwargs):
		raise Thrown(message)

	frappe.throw = throw
	frappe.messages = []
	frappe.msgprint = lambda message=None, *a, **k: frappe.messages.append(message)
	frappe.sent = []
	frappe.sendmail = lambda **kwargs: frappe.sent.append(kwargs)
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: "Traceback"
	frappe.whitelist = lambda *a, **k: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
	frappe.PermissionError = PermissionError
	frappe.DoesNotExistError = LookupError
	frappe.session = types.SimpleNamespace(user="office@example.com")
	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: True,
		get_value=lambda doctype, name, field, *a, **k: {
			("Contact", "email_id"): "buyer@example.com",
			("Contact", "first_name"): "Dana",
		}.get((doctype, field)),
		exists=lambda *a, **k: None,
	)
	frappe.get_cached_doc = lambda doctype, *a, **k: (_ for _ in ()).throw(LookupError(doctype))
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v=0, *a, **k: int(float(v or 0))
	utils.flt = lambda v=0, *a, **k: float(v or 0)
	utils.formatdate = lambda v=None, *a, **k: str(v or "")
	utils.now_datetime = lambda: "2026-11-18 12:00:00"
	utils.nowdate = lambda: "2026-11-18"
	frappe.utils = utils
	email_style = types.ModuleType("erpnext_enhancements.email_style")
	email_style.render = lambda template, context=None, **kwargs: f"[{template}|{kwargs.get('title')}]"
	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "erpnext_enhancements.email_style": email_style}
	)
	for name in _MODULES:
		sys.modules.pop(name, None)
	global shipping, qc_gate, frappe_stub
	frappe_stub = frappe
	qc_gate = importlib.import_module("erpnext_enhancements.product_fulfillment.qc_gate")
	shipping = importlib.import_module("erpnext_enhancements.product_fulfillment.shipping")


def tearDownModule():
	for name in _MODULES:
		sys.modules.pop(name, None)
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


class _Doc(_Dict):
	def __init__(self, *a, **k):
		super().__init__(*a, **k)
		self.setdefault("comments", [])
		self.setdefault("db", {})

	def get(self, key, default=None):
		return dict.get(self, key, default)

	def db_set(self, field, value, **kwargs):
		self["db"][field] = value

	def add_comment(self, kind, text):
		self["comments"].append(text)


def _note(method, **fields):
	doc = _Doc(
		name="MAT-DN-2026-00001",
		docstatus=0,
		is_return=0,
		customer="CUST-1",
		customer_name="Customer One",
		contact_person="CONT-1",
		contact_email=None,
		custom_fulfillment_method=method,
		items=[
			_Doc(
				idx=1,
				item_code="PDT-0040-1-1-1-2-0",
				item_name="STILLWATER E-Stop",
				qty=1,
				serial_no="SF-26-00001",
				against_sales_order="SO-1",
			)
		],
	)
	doc.update(fields)
	return doc


class TestTheHooksOnAStub(unittest.TestCase):
	def setUp(self):
		frappe_stub.sent.clear()
		frappe_stub.messages.clear()
		self.product = True
		qc_gate_rows = lambda doc: [{"idx": 1}] if self.product else []  # noqa: E731
		shipping.qc_gate.gate_rows = qc_gate_rows
		shipping.qc_gate.gate_problems = lambda doc: []
		shipping.get_settings = lambda: _Dict(send_shipped_email=1, pickup_instructions=None)
		import erpnext_enhancements.product_fulfillment.serials as serials

		serials.serials_of_row = lambda row: [s for s in (row.get("serial_no") or "").split("\n") if s]

	def test_a_product_note_without_a_method_is_refused(self):
		with self.assertRaises(Thrown) as caught:
			shipping.delivery_note_before_submit(_note(None))
		self.assertIn("Choose how it gets to the customer", str(caught.exception))

	def test_a_non_product_note_may_leave_it_blank(self):
		self.product = False
		shipping.delivery_note_before_submit(_note(None))

	def test_carrier_shipping_without_a_number_is_refused(self):
		with self.assertRaises(Thrown) as caught:
			shipping.delivery_note_before_submit(_note(sr.CARRIER, transporter="UPS"))
		self.assertIn("tracking number", str(caught.exception))

	def test_validate_cleans_the_number_and_sets_the_link(self):
		doc = _note(sr.CARRIER, transporter="UPS", transporter_name="UPS", lr_no="1z 999 aa1-0123456784")
		shipping.delivery_note_validate(doc)
		self.assertEqual(doc.lr_no, "1Z999AA10123456784")
		self.assertEqual(
			doc.custom_tracking_url, "https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784"
		)

	def test_another_method_clears_the_link(self):
		doc = _note(sr.PICKUP, custom_tracking_url="https://stale.example.com")
		shipping.delivery_note_validate(doc)
		self.assertIsNone(doc.custom_tracking_url)

	def test_submitting_a_carrier_shipment_emails_the_contact(self):
		doc = _note(sr.CARRIER, docstatus=1, transporter="UPS", lr_no="1Z999AA10123456784")
		shipping.delivery_note_on_submit(doc)
		self.assertEqual(len(frappe_stub.sent), 1)
		mail = frappe_stub.sent[0]
		self.assertEqual(mail["recipients"], ["buyer@example.com"])
		self.assertIn("shipped", mail["subject"])
		self.assertIn("shipped.html", mail["message"])
		self.assertEqual((mail["reference_doctype"], mail["reference_name"]), ("Delivery Note", doc.name))
		self.assertIn("custom_shipped_email_sent_on", doc["db"])
		self.assertTrue(doc["comments"])

	def test_the_switch_off_sends_nothing(self):
		shipping.get_settings = lambda: _Dict(send_shipped_email=0)
		shipping.delivery_note_on_submit(
			_note(sr.CARRIER, docstatus=1, transporter="UPS", lr_no="1Z999AA10123456784")
		)
		self.assertEqual(frappe_stub.sent, [])

	def test_no_address_never_undoes_the_submit(self):
		original = frappe_stub.db.get_value
		frappe_stub.db.get_value = lambda *a, **k: None
		try:
			shipping.delivery_note_on_submit(
				_note(sr.CARRIER, docstatus=1, transporter="UPS", lr_no="1Z999AA10123456784")
			)
		finally:
			frappe_stub.db.get_value = original
		self.assertEqual(frappe_stub.sent, [])
		self.assertTrue(any("no email address" in str(m) for m in frappe_stub.messages))

	def test_a_return_sends_nothing(self):
		shipping.delivery_note_on_submit(
			_note(sr.CARRIER, docstatus=1, is_return=1, transporter="UPS", lr_no="1Z999AA10123456784")
		)
		self.assertEqual(frappe_stub.sent, [])

	def test_ready_for_pickup_waits_for_the_tests(self):
		doc = _note(sr.PICKUP)
		doc.check_permission = lambda ptype: None
		shipping.frappe.get_doc = lambda doctype, name: doc
		shipping.qc_gate.gate_problems = lambda d: [
			"Row 1: unit SF-26-00001 has no submitted Pre-Shipment Functional Test."
		]
		with self.assertRaises(Thrown) as caught:
			shipping.send_ready_for_pickup(doc.name)
		self.assertIn("Not ready yet", str(caught.exception))
		self.assertEqual(frappe_stub.sent, [])
		shipping.qc_gate.gate_problems = lambda d: []
		self.assertEqual(shipping.send_ready_for_pickup(doc.name), {"sent_to": "buyer@example.com"})
		self.assertIn("ready_for_pickup.html", frappe_stub.sent[0]["message"])

	def test_ready_for_pickup_is_for_a_draft_pickup_only(self):
		doc = _note(sr.CARRIER)
		doc.check_permission = lambda ptype: None
		shipping.frappe.get_doc = lambda doctype, name: doc
		with self.assertRaises(Thrown):
			shipping.send_ready_for_pickup(doc.name)


if __name__ == "__main__":
	unittest.main()
