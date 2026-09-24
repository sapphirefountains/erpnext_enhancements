"""Bench-free tests for the five procurement print formats in the Purchase Order's design.

A print format fails the worst way there is: the deploy succeeds, nothing logs, and the
defect is found by whoever is holding the paper. So, as for the sales formats, this suite
compiles and renders every template against sample documents and checks what fails
silently — plus two things particular to this family:

* **they match the Purchase Order.** The totals are `print_style`'s shared totals styles in
  both modules, the dash and the table rules are held equal to the order's own, and the
  chrome is the same neutral stripe, so the family cannot drift apart one edit at a time;
* **they are the defaults.** A format nobody reaches is a format nobody prints: the six
  Property Setters that make each "<Doctype> - Sapphire" the doctype's default are checked
  against the formats this app actually ships, and the after_migrate hook against its
  position above the chrome pass.

Run: python -m unittest erpnext_enhancements.tests.test_procurement_print_formats -v
"""

import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE_PATH = APP / "enhancements_core" / "setup_procurement_print_formats.py"
PO_MODULE_PATH = APP / "enhancements_core" / "setup_print_formats.py"
HOOKS = APP / "hooks.py"
PROPERTY_SETTERS = APP / "fixtures" / "property_setter.json"

# Both modules import `frappe` at the top and are pure strings otherwise, so they are
# exec'd under a stub rather than imported — the sales suite's approach.
_NS = {}
_PO_NS = {}


def setUpModule():
	sys.modules.setdefault("frappe", types.ModuleType("frappe"))
	for path, namespace in ((MODULE_PATH, _NS), (PO_MODULE_PATH, _PO_NS)):
		exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)


def formats():
	return _NS["FORMATS"]


def html_for(doctype):
	return {d: h for _n, d, h in formats()}[doctype]


def buying_formats():
	"""The five here plus the Purchase Order, as (name, doctype, html)."""
	return (*formats(), (_PO_NS["PURCHASE_ORDER_FORMAT"], "Purchase Order", _PO_NS["_HTML"]))


class _Doc:
	"""Attribute access plus .get(), like a Frappe document.

	Deliberately NOT a dict subclass: `doc.items` would resolve to the bound `dict.items`.
	"""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def __getattr__(self, key):
		return None

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _frappe():
	names = {
		("Supplier", "supplier_name"): "Harrington Industrial Plastics",
		("User", "full_name"): "Lisa Carter",
	}
	return types.SimpleNamespace(
		format=lambda value, opts=None: f"DATE({value})",
		utils=types.SimpleNamespace(
			fmt_money=lambda v, currency=None: f"{currency or ''} {float(v or 0):,.2f}".strip()
		),
		db=types.SimpleNamespace(get_value=lambda dt, name, field: names.get((dt, field), f"<{dt}:{name}>")),
	)


def _items(rows, **extra):
	out = []
	for i in range(1, rows + 1):
		row = dict(
			item_code=f"ITEM-{i}",
			item_name=f"Item & {i}",
			description="<p>Markup <b>from the item master</b></p>" if i % 2 else None,
			qty=float(i),
			uom="Nos",
			rate=10.0,
			amount=10.0 * i,
			schedule_date="2026-09-30",
			warehouse="Stores - SF",
			purchase_order="PO-2026-00262",
			rejected_qty=0,
			supplier_part_no=None,
		)
		row.update(extra)
		out.append(_Doc(**row))
	return out


def _sample(doctype, rows=2, **overrides):
	fields = dict(
		name="DOC-0001",
		doctype=doctype,
		owner="lisa@example.com",
		status="Submitted",
		currency="USD",
		transaction_date="2026-09-23",
		posting_date="2026-09-23",
		schedule_date="2026-09-30",
		supplier="SUP-0001",
		supplier_name="A Supplier",
		address_display="1 Supplier Street",
		contact_display="A Person",
		billing_address_display="2 Our Street",
		custom_project="PRJ-00706",
		project="PRJ-00706",
		material_request_type="Purchase",
		suppliers=[
			_Doc(supplier="SUP-0001", supplier_name="A Supplier"),
			_Doc(supplier="SUP-0002", supplier_name="B Supplier"),
		],
		net_total=100.0,
		grand_total=107.25,
		outstanding_amount=107.25,
		taxes=[_Doc(description="Utah Sales Tax", tax_amount=7.25)],
		payment_schedule=[],
		items=_items(rows),
	)
	fields.update(overrides)
	return _Doc(**fields)


def _fake_party(doc):
	"""Stands in for `print_lookup.ps_party`, which needs a bench (it follows the Contact,
	Address and Supplier records). Built on the real `print_style.party_block` from the
	document's own fields, so the block looks exactly as it does on production."""
	ps = _NS["ps"]
	return ps.party_block(
		doc.get("supplier_name") or doc.get("supplier") or "",
		doc.get("address_display") or "",
		doc.get("contact_display") or "",
		doc.get("contact_mobile") or "",
		doc.get("contact_email") or "",
	)


def _fake_rfq_suppliers(doc):
	"""Stands in for `print_lookup.ps_rfq_suppliers` with its rule: `vendor` alone when ERPNext
	set it, else every supplier row; a dash when there are none."""
	ps = _NS["ps"]
	rows = list(doc.get("suppliers") or [])
	vendor = doc.get("vendor")
	if vendor:
		rows = [r for r in rows if r.supplier == vendor] or [_Doc(supplier=vendor)]
	blocks = [ps.party_block(r.supplier_name or r.supplier or "") for r in rows]
	return "<br>".join(b for b in blocks if b) or _NS["_DASH"]


def _jinja_globals():
	"""Every `ps_*` global hooks.py registers: print_style's for real, print_lookup's faked."""
	ps = _NS["ps"]
	methods = {name: getattr(ps, name) for name in dir(ps) if name.startswith("ps_")}
	methods.update(ps_party=_fake_party, ps_rfq_suppliers=_fake_rfq_suppliers)
	return methods


def _render(doctype, doc=None, **overrides):
	from jinja2 import Environment

	return (
		Environment()
		.from_string(html_for(doctype))
		.render(
			doc=doc or _sample(doctype, **overrides),
			frappe=_frappe(),
			letter_head="<div>LETTERHEAD</div>",
			**_jinja_globals(),
		)
	)


class _JinjaCase(unittest.TestCase):
	def setUp(self):
		try:
			import jinja2
		except ImportError:  # pragma: no cover
			self.skipTest("jinja2 not installed")


class TestDeclared(unittest.TestCase):
	def test_five_formats_one_per_doctype(self):
		self.assertEqual(
			{(n, d) for n, d, _h in formats()},
			{
				("Material Request - Sapphire", "Material Request"),
				("Request for Quotation - Sapphire", "Request for Quotation"),
				("Supplier Quotation - Sapphire", "Supplier Quotation"),
				("Purchase Receipt - Sapphire", "Purchase Receipt"),
				("Purchase Invoice - Sapphire", "Purchase Invoice"),
			},
		)

	def test_no_placeholder_survives_composition(self):
		"""`__DESCRIPTION_EXTRA__` here and print_style's `__OPEN__` are substituted at
		compose time; one left behind prints as literal text."""
		for name, _d, html in formats():
			with self.subTest(name):
				self.assertEqual(re.findall(r"__[A-Z_]+__", html), [])


class TestRender(_JinjaCase):
	def test_compiles(self):
		from jinja2 import Environment

		for name, _d, html in formats():
			with self.subTest(name):
				Environment().parse(html)

	def test_renders_with_one_line_and_with_ten(self):
		for name, doctype, _h in formats():
			for rows in (1, 10):
				with self.subTest(f"{name}/{rows}"):
					out = _render(doctype, rows=rows)
					self.assertIn("ITEM-1", out)
					self.assertIn("<svg", out, "the wordmark is drawn by the template")
					self.assertNotIn("LETTERHEAD", out, "the site's Letter Head must not add a second logo")
					self.assertNotIn("{{", out)
					self.assertNotIn("{%", out)

	def test_renders_with_no_lines(self):
		for name, doctype, _h in formats():
			with self.subTest(name):
				self.assertRegex(_render(doctype, rows=0), r"No items on this \w+\.")

	def test_the_sparsest_document_prints_no_none(self):
		"""Every optional field blank at once."""
		blanks = dict(
			address_display=None,
			contact_display=None,
			billing_address_display=None,
			custom_project=None,
			project=None,
			schedule_date=None,
			valid_till=None,
			due_date=None,
			bill_no=None,
			bill_date=None,
			supplier_delivery_note=None,
			set_warehouse=None,
			terms=None,
			message_for_supplier=None,
			material_request_type=None,
			vendor=None,
			suppliers=[],
			taxes=[],
			outstanding_amount=None,
			items=_items(2, uom=None, warehouse=None, purchase_order=None, schedule_date=None),
		)
		for name, doctype, _h in formats():
			with self.subTest(name):
				out = _render(doctype, **blanks)
				self.assertNotIn("None", out)
				self.assertIn("&mdash;", out, "a blank prints a dash, not nothing")

	def test_description_markup_survives_and_item_name_is_escaped(self):
		for name, doctype, _h in formats():
			with self.subTest(name):
				out = _render(doctype)
				self.assertIn("<b>from the item master</b>", out)
				self.assertIn("Item &amp; 2", out)


class TestSilentFailureModes(unittest.TestCase):
	def test_the_chrome_is_the_neutral_pillar_stripe(self):
		stripe = _NS["ps"].stripe_css(None)
		for name, _d, html in formats():
			with self.subTest(name):
				self.assertEqual(html.count(stripe), 2, "top and bottom stripe, the order's neutral band")
				self.assertIn("@font-face", html)
				self.assertIn("Sapphire Fountains, LLC", html)
				self.assertNotIn("{{ letter_head }}", html)

	def test_description_through_ps_rich_item_name_escaped(self):
		"""Markup from the Item master passes through; plain text keeps its line breaks.
		Neither the raw field nor an escaped one."""
		for name, _d, html in formats():
			with self.subTest(name):
				self.assertIn("{{ ps_rich(row.description) }}", html)
				self.assertNotIn("{{ row.description }}", html)
				self.assertNotIn("row.description | e", html)
				self.assertIn("{{ row.item_name | e }}", html)

	def test_quantities_units_and_codes_print_as_a_person_writes_them(self):
		for name, _d, html in formats():
			with self.subTest(name):
				self.assertIn("{{ ps_qty(row.qty) }}", html)
				self.assertNotIn("{{ row.qty }}", html)
				self.assertIn("{{ ps_uom(row.uom) }}", html)
				self.assertNotIn("row.uom or", html)
				self.assertIn('white-space:nowrap;">{{ row.item_code | e }}</td>', html)

	def test_no_link_is_printed_where_a_rendered_twin_exists(self):
		"""A Link holds a record's NAME. `Purchase Order.shipping_address` printed "Sapphire
		Fountain-Billing" on 221 orders; these are the Links with a `*_display` beside them."""
		links = (
			r"doc\.(shipping_address|supplier_address|billing_address|dispatch_address"
			r"|company_address|contact_person)\b"
		)
		for name, _d, html in buying_formats():
			with self.subTest(name):
				self.assertIsNone(re.search(links, html))

	def test_every_address_display_goes_through_ps_address(self):
		"""Every US address ends in `<br>`; printed raw it leaves a blank line under it."""
		for name, _d, html in buying_formats():
			with self.subTest(name):
				self.assertIsNone(re.search(r"\{\{\s*doc\.\w*address_display", html))

	def test_supplier_blocks_are_the_lookup_helpers(self):
		"""This site keeps a supplier's address, phone and email on its Contact, Address and
		Supplier records, not on the document -- only `print_lookup` can find them."""
		for doctype in ("Supplier Quotation", "Purchase Receipt", "Purchase Invoice"):
			with self.subTest(doctype):
				self.assertIn("{{ ps_party(doc) }}", html_for(doctype))
				self.assertNotIn("doc.address_display", html_for(doctype))
		rfq = html_for("Request for Quotation")
		self.assertIn("{{ ps_rfq_suppliers(doc) }}", rfq)
		self.assertNotIn('get_value("Supplier"', rfq)
		self.assertNotIn("doc.suppliers", rfq)

	def test_print_safe_css(self):
		for name, _d, html in formats():
			with self.subTest(name):
				compact = html.replace(" ", "")
				self.assertNotIn("display:flex", compact)
				self.assertNotIn("display:grid", compact)
				self.assertIn("display:table-header-group", html)
				self.assertIn("page-break-inside:avoid", html)

	def test_money_always_carries_a_currency(self):
		for name, _d, html in formats():
			with self.subTest(name):
				for call in re.findall(r"fmt_money\([^)]*\)", html):
					self.assertIn("currency=", call, f"{name}: {call}")

	def test_custom_fields_are_read_through_get(self):
		"""`custom_project` is this app's field; a site without it must print a dash."""
		for name, _d, html in formats():
			with self.subTest(name):
				self.assertNotIn("doc.custom_project", html)


class TestMatchesThePurchaseOrder(unittest.TestCase):
	"""The brief was the order's design. These hold the shared pieces to the order's own."""

	def test_the_dash_is_the_orders(self):
		self.assertEqual(_NS["_DASH"], _PO_NS["_DASH"])

	def test_both_modules_total_in_the_print_style_constants(self):
		"""No module-local copy of the totals styles left to drift: both use `print_style`'s
		TOTAL_SPACER / TOTAL_LABEL / TOTAL_VALUE / GRAND, whose padding carries the inline
		`!important` that frappe's print stylesheets otherwise override."""
		ps = _NS["ps"]
		for namespace, label in ((_NS, "procurement"), (_PO_NS, "order")):
			for local in ("_TOTAL_LABEL_TD", "_TOTAL_VALUE_TD", "_GRAND_TD"):
				with self.subTest(f"{label}/{local}"):
					self.assertNotIn(local, namespace)
		priced = [html_for(d) for d in ("Supplier Quotation", "Purchase Invoice")] + [_PO_NS["_HTML"]]
		for html in priced:
			for style in (ps.TOTAL_SPACER, ps.TOTAL_LABEL, ps.TOTAL_VALUE, ps.GRAND):
				with self.subTest(style[:30]):
					self.assertIn(style, html)

	def test_an_extra_top_padding_beats_the_important_one(self):
		"""In one style attribute an ordinary `padding-top` loses to TOTAL_LABEL's
		`padding:... !important`, whatever the order; Amount due's must be important too."""
		html = html_for("Purchase Invoice")
		self.assertNotRegex(html, r"padding-top:6px(?! !important)")
		self.assertIn("padding-top:6px !important", html)

	def test_same_table_rules_and_stripe_as_the_order(self):
		ps = _NS["ps"]
		po_html = _PO_NS["_HTML"]
		for needle in (ps.th(), ps.th(right=True), ps.TD, ps.TD_RIGHT, ps.stripe_css(None)):
			self.assertIn(needle, po_html)
			for name, _d, html in formats():
				with self.subTest(f"{name}/{needle[:30]}"):
					self.assertIn(needle, html)

	def test_the_supplier_facing_four_read_the_orders_address_field(self):
		"""`billing_address_display` on buying doctypes; the sales name for it would print
		nothing and raise nothing, falling back to the constant forever."""
		self.assertEqual(_NS["ADDRESS_FIELD"], _PO_NS["ADDRESS_FIELD"])
		for doctype in (
			"Request for Quotation",
			"Supplier Quotation",
			"Purchase Receipt",
			"Purchase Invoice",
		):
			with self.subTest(doctype):
				self.assertIn("doc.billing_address_display", html_for(doctype))
				self.assertNotIn("company_address_display", html_for(doctype))

	def test_a_material_request_has_no_company_address_and_prints_ours(self):
		from erpnext_enhancements.enhancements_core.company_contact import COMPANY_ADDRESS_HTML

		html = html_for("Material Request")
		self.assertNotIn("address_display", html)
		self.assertIn(COMPANY_ADDRESS_HTML, html)


class TestDocumentSpecifics(_JinjaCase):
	def test_rfq_is_addressed_to_the_vendor_being_sent(self):
		"""ERPNext renders an RFQ once per supplier with `vendor` set; `ps_rfq_suppliers`
		gets the document and decides, so the vendor's block alone reaches the page."""
		out = _render("Request for Quotation", vendor="SUP-0002")
		self.assertIn("B Supplier", out)
		self.assertNotIn("A Supplier", out)

	def test_rfq_without_a_vendor_lists_every_supplier(self):
		out = _render("Request for Quotation", vendor=None)
		self.assertIn("A Supplier", out)
		self.assertIn("B Supplier", out)

	def test_rfq_deliver_to_is_the_rendered_address_without_its_trailing_break(self):
		out = _render(
			"Request for Quotation", shipping_address_display="85 W 300 S<br>Bountiful, UT 84010<br>"
		)
		deliver_to = out.split(">DELIVER TO<", 1)[1].split(">PROJECT<", 1)[0]
		self.assertIn("85 W 300 S<br>Bountiful, UT 84010</div>", deliver_to)
		self.assertIn("Not specified", _render("Request for Quotation", shipping_address_display=None))

	def test_quantities_print_without_a_trailing_zero(self):
		"""`{{ row.qty }}` printed the raw float -- "12.0" -- on every line."""
		for name, doctype, _h in formats():
			with self.subTest(name):
				body = _render(doctype, items=_items(1, qty=12.0)).split("<tbody>", 1)[1]
				self.assertIn(">12</td>", body)
				self.assertNotIn("12.0", body)
				self.assertIn(">ea</td>", body, '"Nos" prints as "ea"')

	def test_a_plain_text_description_keeps_its_line_breaks(self):
		for name, doctype, _h in formats():
			with self.subTest(name):
				out = _render(doctype, items=_items(1, description="Pump, 3/4 HP\n50 ft cord & bracket"))
				self.assertIn("Pump, 3/4 HP<br>50 ft cord &amp; bracket", out)

	def test_invoice_net_30_is_printed_once(self):
		"""The template's name is also its one schedule row's label."""
		out = _render(
			"Purchase Invoice",
			payment_terms_template="Net 30",
			payment_schedule=[
				_Doc(payment_term="Net 30", description=None, payment_amount=107.25, due_date="2026-10-23")
			],
		)
		self.assertEqual(out.count("Net 30"), 1)
		self.assertIn("USD 107.25 due DATE(2026-10-23)", out)

	def test_invoice_amount_due_is_styled_like_the_totals(self):
		ps = _NS["ps"]
		out = _render("Purchase Invoice", outstanding_amount=50.0)
		row = out.split(">Amount due<", 1)[0].rsplit("<tr>", 1)[1]
		self.assertIn(ps.TOTAL_LABEL, row)
		self.assertIn(ps.TOTAL_SPACER, row)

	def test_rfq_carries_no_prices_and_shows_a_supplier_part_number(self):
		html = html_for("Request for Quotation")
		self.assertNotIn("row.rate", html)
		self.assertNotIn("row.amount", html)
		out = _render("Request for Quotation", items=_items(1, supplier_part_no="HX-9"))
		self.assertIn("YOUR PART NO.", out)
		self.assertIn("HX-9", out)
		self.assertIn("Please quote <b>DOC-0001</b>", out)

	def test_receipt_prints_no_money(self):
		self.assertNotIn("fmt_money", html_for("Purchase Receipt"))

	def test_receipt_rejected_column_only_when_something_was_rejected(self):
		clean = _render("Purchase Receipt")
		self.assertNotIn(">Rejected<", clean)
		rejected = _render("Purchase Receipt", items=_items(2, rejected_qty=3.0))
		self.assertIn(">Rejected<", rejected)
		lines = rejected.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
		self.assertIn(">3</td>", lines)
		self.assertNotIn("3.0", lines)

	def test_receipt_empty_state_spans_the_columns_actually_drawn(self):
		self.assertIn('colspan="6"', _render("Purchase Receipt", rows=0))

	def test_receipt_names_its_orders_once_each(self):
		out = _render("Purchase Receipt", items=_items(3))
		self.assertIn("PO-2026-00262", out)
		facts = out.split("AGAINST ORDER", 1)[1].split("PROJECT", 1)[0]
		self.assertEqual(facts.count("PO-2026-00262"), 1)

	def test_receipt_has_a_line_to_sign(self):
		self.assertIn("RECEIVED BY", _render("Purchase Receipt"))

	def test_returns_say_so_in_the_title(self):
		self.assertIn("Purchase Return", _render("Purchase Receipt", is_return=1))
		self.assertNotIn("Purchase Return", _render("Purchase Receipt", is_return=0))
		self.assertIn("Debit Note", _render("Purchase Invoice", is_return=1))
		self.assertNotIn("Debit Note", _render("Purchase Invoice", is_return=0))

	def test_invoice_amount_due_only_when_part_paid(self):
		self.assertIn("Amount due", _render("Purchase Invoice", outstanding_amount=50.0))
		self.assertNotIn("Amount due", _render("Purchase Invoice", outstanding_amount=107.25))

	def test_invoice_shows_the_suppliers_own_invoice_number(self):
		out = _render("Purchase Invoice", bill_no="INV-88213", bill_date="2026-09-21")
		self.assertIn("INV-88213", out)
		self.assertIn("DATE(2026-09-21)", out)

	def test_material_request_prints_no_money_and_the_project(self):
		self.assertNotIn("fmt_money", html_for("Material Request"))
		self.assertIn("PRJ-00706", _render("Material Request"))

	def test_supplier_quotation_shows_validity(self):
		self.assertIn("DATE(2026-10-23)", _render("Supplier Quotation", valid_till="2026-10-23"))


class TestWiring(unittest.TestCase):
	def test_after_migrate_runs_it_before_the_chrome_pass(self):
		"""After it, the new formats would render on wkhtmltopdf."""
		hooks = HOOKS.read_text(encoding="utf-8")
		ours = hooks.index(
			'"erpnext_enhancements.enhancements_core.setup_procurement_print_formats.ensure_procurement_print_formats"'
		)
		chrome = hooks.index(
			'"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator"'
		)
		self.assertLess(ours, chrome)

	def test_each_procurement_doctype_defaults_to_its_sapphire_format(self):
		setters = {
			row["doc_type"]: row["value"]
			for row in json.loads(PROPERTY_SETTERS.read_text(encoding="utf-8"))
			if row["property"] == "default_print_format" and row["doctype_or_field"] == "DocType"
		}
		shipped = {d: n for n, d, _h in formats()}
		shipped["Purchase Order"] = _PO_NS["PURCHASE_ORDER_FORMAT"]
		for doctype, fmt in shipped.items():
			with self.subTest(doctype):
				self.assertEqual(
					setters.get(doctype), fmt, "a default naming a format we do not ship prints nothing"
				)

	def test_the_default_setters_follow_the_fixture_spec(self):
		rows = [
			r
			for r in json.loads(PROPERTY_SETTERS.read_text(encoding="utf-8"))
			if r["property"] == "default_print_format"
		]
		for row in rows:
			with self.subTest(row["name"]):
				self.assertEqual(row["name"], f'{row["doc_type"]}-main-default_print_format')
				self.assertEqual(row["is_system_generated"], 0, "the fixture export filters on 0")
				for volatile in ("modified", "modified_by", "creation", "owner", "idx"):
					self.assertNotIn(volatile, row)


class TestTheStockFormatsAreDisabled(unittest.TestCase):
	"""The Sapphire formats are the defaults; the stock ones leave the dropdown, as the
	order's three did. Disabled rather than deleted, because a deleted standard format is
	imported again from ERPNext's JSON; the disable survives that import (frappe keeps
	`disabled` through it), and the order module's every-migrate pass re-applies it anyway,
	which is idempotent and re-disables anything an admin switched back on."""

	STOCK = {
		"Request for Quotation Print Template",
		"Request for Quotation with Item Image",
		"Purchase Receipt Serial and Batch Bundle Print",
		"Purchase Invoice Standard",
		"Purchase Invoice with Item Image",
		"Purchase Auditing Voucher",
		"Purchase eInvoice",
	}

	def test_the_list_is_every_stock_format_on_the_five(self):
		self.assertEqual(set(_PO_NS["SUPERSEDED_PROCUREMENT_FORMATS"]), self.STOCK)

	def test_no_format_we_ship_is_on_it(self):
		ours = {n for n, _d, _h in formats()} | {_PO_NS["PURCHASE_ORDER_FORMAT"]}
		listed = (
			set(_PO_NS["SUPERSEDED_PROCUREMENT_FORMATS"])
			| set(_PO_NS["SUPERSEDED_PURCHASE_ORDER_FORMATS"])
			| set(_PO_NS["SUPERSEDED_SALES_FORMATS"])
		)
		self.assertEqual(ours & listed, set())

	def test_the_pass_disables_them_with_the_low_level_write(self):
		"""Run the real function against a fake database: every enabled stock format is
		disabled -- procurement's and, since v1.535.0, the Sales Invoice ones -- an
		already-disabled one and a missing one are left alone, a stock format nobody listed
		(`Quotation Standard`) is untouched, and nothing goes through the ORM (Print
		Format.validate refuses standard formats)."""
		state = {
			"Purchase Invoice Standard": 0,
			"Request for Quotation Print Template": 0,
			"Purchase eInvoice": 1,
			"Purchase Order Standard": 1,
			"Sales Invoice Standard": 0,
			"Sales Invoice Print": 0,
			"Tax Invoice": 1,
			"Quotation Standard": 0,
		}
		writes = []
		fake = types.SimpleNamespace(
			db=types.SimpleNamespace(
				has_column=lambda dt, col: True,
				exists=lambda dt, name: name in state,
				get_value=lambda dt, name, field: state[name],
				set_value=lambda dt, name, field, value, update_modified=True: writes.append(
					(name, field, value)
				),
				commit=lambda: None,
			),
			logger=lambda: types.SimpleNamespace(info=lambda msg: None),
			log_error=lambda *a, **kw: self.fail(f"the pass logged an error: {a}"),
			get_traceback=lambda: "",
		)
		original = _PO_NS["frappe"]
		_PO_NS["frappe"] = fake
		try:
			_PO_NS["disable_superseded_print_formats"]()
		finally:
			_PO_NS["frappe"] = original
		self.assertEqual(
			sorted(writes),
			[
				("Purchase Invoice Standard", "disabled", 1),
				("Request for Quotation Print Template", "disabled", 1),
				("Sales Invoice Print", "disabled", 1),
				("Sales Invoice Standard", "disabled", 1),
			],
		)


if __name__ == "__main__":
	unittest.main()
