"""Bench-free tests for the E-Stop quote-to-order slice (v1.825.0).

Plain pytest functions, so CI runs this file in its own ``python -m pytest`` step (``python -m
unittest`` would collect nothing from it). What it holds:

* the one line format every configured product is quoted in, built from the shipped PDT-0040 seed
  (so a seed edit that changes what a customer reads shows up here);
* who a Quotation is for, and the Quotation's header and line (the configured price kept, the
  warehouse a Sales Order needs, the link back to the configuration);
* options given as data (the phone page and the Triton tool will start configurations that way);
* the refusals' plain words;
* the product-sale rule that keeps a project away from a product sale, in Python and in SQL;
* the stage guard that undoes ERPNext's "Quotation" / "Converted" statuses;
* the wiring: hooks, the hand-off, the endpoints, the no-commit contract, the DocType JSON.

The modules under test import no frappe at module level; where a function reads the database, a
small fake ``frappe`` stands in through ``monkeypatch`` and is removed afterwards.

Run: python -m pytest erpnext_enhancements/tests/test_product_quote.py -q
"""

import ast
import io
import json
import re
import sys
import tokenize
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.crm_enhancements import opportunity_stage_guard as guard
from erpnext_enhancements.crm_enhancements import product_sale
from erpnext_enhancements.product_configurator.engine import quote
from erpnext_enhancements.product_configurator.seed_data import PDT_0040

APP = REPO_ROOT / "erpnext_enhancements"
STAGES = [
	"Qualification",
	"Needs Analysis",
	"Proposal/Price Quote",
	"Negotiation/Review",
	"Lost",
	"Closed Won",
	"On Hold",
]


def _code(path):
	"""A Python file's source with comments and docstrings removed (absence checks read code only)."""
	source = Path(path).read_text(encoding="utf-8")
	out = []
	previous = tokenize.INDENT
	for tok in tokenize.generate_tokens(io.StringIO(source).readline):
		if tok.type == tokenize.COMMENT:
			continue
		if tok.type == tokenize.STRING and previous in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT):
			previous = tok.type
			continue
		out.append(tok.string)
		previous = tok.type
	return " ".join(out)


def seed_rows(**chosen):
	"""The option rows a new PDT-0040 configuration holds, as ``sync_option_rows`` builds them.

	``chosen`` overrides a default: ``mounting="2"`` or ``relay_qty=1``.
	"""
	rows = []
	defaults = {}
	for opt in PDT_0040["options"]:
		if opt["option_type"] == "Choice" and (opt.get("is_default") or opt["option_key"] not in defaults):
			defaults[opt["option_key"]] = str(opt["choice_code"])
	for opt in PDT_0040["options"]:
		kind = opt["option_type"]
		if kind == "Base":
			continue
		key = opt["option_key"]
		if kind == "Choice":
			code = str(opt["choice_code"])
			picked = str(chosen.get(key, defaults[key]))
			rows.append(
				{
					"option_key": key,
					"option_type": kind,
					"option_label": opt["option_label"],
					"choice_code": code,
					"choice_label": opt.get("choice_label", ""),
					"selected": 1 if code == picked else 0,
					"qty": 0,
				}
			)
		else:
			rows.append(
				{
					"option_key": key,
					"option_type": kind,
					"option_label": opt["option_label"],
					"choice_code": "",
					"choice_label": "",
					"selected": 0,
					"qty": chosen.get(key, opt.get("default_qty", 0)),
				}
			)
	return rows


# ------------------------------------------------------------------ the line format


def test_the_most_common_e_stop_reads_part_number_then_one_line_per_option():
	assert quote.description_lines("PDT-0040-1-1-1-2-0", seed_rows()) == [
		"PDT-0040-1-1-1-2-0",
		"E-Stop Mounting: Flush",
		"E-Stop Button Qty: 1",
		"Timer & Button Qty: 1",
		"Pump Contactor Qty: 2",
		"Auxiliary Relay Qty: 0",
	]


def test_every_option_prints_zeros_included_and_the_choice_by_its_label():
	lines = quote.description_lines(
		"PDT-0040-3-2-0-0-3", seed_rows(mounting="3", estop_qty=2, timer_qty=0, contactor_qty=0, relay_qty=3)
	)
	assert lines[1] == "E-Stop Mounting: Pedestal"
	assert "Timer & Button Qty: 0" in lines
	assert "Pump Contactor Qty: 0" in lines
	assert "Auxiliary Relay Qty: 3" in lines
	assert len(lines) == 6  # the part number and five options, nothing else


def test_a_choice_group_prints_once_however_many_choices_it_has():
	lines = quote.description_lines("X", seed_rows(mounting="2"))
	assert [line for line in lines if line.startswith("E-Stop Mounting")] == ["E-Stop Mounting: Surface"]


def test_the_choice_code_stands_in_for_a_missing_label():
	rows = seed_rows()
	for row in rows:
		row["choice_label"] = ""
	assert quote.description_lines("X", rows)[1] == "E-Stop Mounting: 1"


def test_additional_cost_adds_a_line_and_zero_adds_none():
	rows = seed_rows()
	assert quote.description_lines("X", rows, "Current switch", 120)[-1] == "Additional: Current switch"
	assert quote.description_lines("X", rows, "", 50)[-1] == "Additional: see configuration"
	assert not any(line.startswith("Additional") for line in quote.description_lines("X", rows, "Spare", 0))


def test_a_fractional_quantity_is_not_rounded_away():
	rows = [{"option_type": "Quantity", "option_label": "Wire (ft)", "qty": 2.5}]
	assert quote.description_lines("X", rows) == ["X", "Wire (ft): 2.5"]


def test_the_html_is_one_escaped_paragraph_per_line():
	html = quote.description_html(["PDT-0040-1-1-1-2-0", "Timer & Button Qty: 1", "<b>x</b>"])
	assert html == "<p>PDT-0040-1-1-1-2-0</p><p>Timer &amp; Button Qty: 1</p><p>&lt;b&gt;x&lt;/b&gt;</p>"


def test_the_item_description_uses_the_same_format():
	code = _code(APP / "product_configurator" / "erp_integration.py")
	body = code[code.index("def _compose_description") :]
	body = body[: body.index("def ", 10)]
	assert "description_lines (" in body and "description_html (" in body
	assert '" | "' not in body


# ------------------------------------------------------------------ who it is for


@pytest.mark.parametrize(
	"args, expected",
	[
		(("CUST-1", "LEAD-1", "Lead", "LEAD-2"), ("Customer", "CUST-1")),
		((None, None, "Customer", "CUST-2"), ("Customer", "CUST-2")),
		((None, "LEAD-1", "Lead", "LEAD-2"), ("Lead", "LEAD-2")),
		((None, None, "Prospect", "Acme Pools"), ("Prospect", "Acme Pools")),
		((None, "LEAD-1", None, None), ("Lead", "LEAD-1")),
		((None, "LEAD-1", "CRM Deal", "D-1"), ("Lead", "LEAD-1")),
		((None, None, None, None), (None, None)),
	],
)
def test_the_quote_goes_to_the_configurations_customer_then_the_deals_party_then_its_lead(args, expected):
	assert quote.quotation_party(*args) == expected


def test_erpnexts_opportunity_mapping_is_used_only_for_the_deals_own_party():
	assert quote.uses_opportunity_mapping("OPP-1", "Customer", "CUST-1", "Customer", "CUST-1")
	assert quote.uses_opportunity_mapping("OPP-1", "Lead", "LEAD-1", "Lead", "LEAD-1")
	assert not quote.uses_opportunity_mapping("OPP-1", "Customer", "CUST-9", "Lead", "LEAD-1")
	assert not quote.uses_opportunity_mapping("OPP-1", "Customer", "CUST-9", "Customer", "CUST-1")
	assert not quote.uses_opportunity_mapping(None, "Customer", "CUST-1", "Customer", "CUST-1")


# ------------------------------------------------------------------ the Quotation


def test_the_line_keeps_the_configured_price_against_erpnexts_repricing():
	line = quote.quotation_line(
		"PDT-0040-1-1-1-2-0", 1512.979, "<p>d</p>", "PCFG-2026-00001", "Finished Goods - SF"
	)
	assert line["rate"] == 1512.98
	assert line["price_list_rate"] == line["rate"]
	assert line["qty"] == 1 and line["uom"] == "Nos"
	assert line["warehouse"] == "Finished Goods - SF"
	assert line["custom_product_configuration"] == "PCFG-2026-00001"
	assert line["description"] == "<p>d</p>"


def test_a_line_without_a_warehouse_leaves_the_field_to_erpnext():
	assert "warehouse" not in quote.quotation_line("X", 10, "", "PCFG-1")


def test_the_header_quotes_the_party_at_the_configured_price():
	header = quote.quotation_header(
		"Lead", "LEAD-1", "Sapphire Fountains", "OPP-1", "Standard Selling", "2026-11-18"
	)
	assert header == {
		"quotation_to": "Lead",
		"party_name": "LEAD-1",
		"company": "Sapphire Fountains",
		"ignore_pricing_rule": 1,
		"opportunity": "OPP-1",
		"selling_price_list": "Standard Selling",
		"transaction_date": "2026-11-18",
	}
	assert "opportunity" not in quote.quotation_header("Customer", "C", "Co")


def test_the_whole_quote_for_the_common_setup():
	"""End to end on pure parts: seed rows -> description -> the line the Quotation gets."""
	lines = quote.description_lines("PDT-0040-1-1-1-2-0", seed_rows())
	line = quote.quotation_line(
		"PDT-0040-1-1-1-2-0", 1512.979, quote.description_html(lines), "PCFG-1", "FG - SF"
	)
	assert line["description"].startswith("<p>PDT-0040-1-1-1-2-0</p><p>E-Stop Mounting: Flush</p>")
	assert line["description"].count("<p>") == 6


# ------------------------------------------------------------------ options as data


def test_selections_pick_a_choice_and_clear_its_siblings():
	rows = seed_rows()
	assert quote.apply_selections(rows, {"mounting": "3", "relay_qty": "2"}) == []
	picked = [row["choice_code"] for row in rows if row["option_key"] == "mounting" and row["selected"]]
	assert picked == ["3"]
	assert next(row for row in rows if row["option_key"] == "relay_qty")["qty"] == 2


def test_unknown_options_and_choices_are_reported_and_change_nothing():
	rows = seed_rows()
	before = json.dumps(rows, sort_keys=True)
	assert quote.apply_selections(rows, {"colour": "red", "mounting": "9", "timer_qty": "lots"}) == [
		"colour",
		"mounting",
		"timer_qty",
	]
	assert json.dumps(rows, sort_keys=True) == before


def test_the_endpoint_applies_selections_to_plain_dicts_it_writes_back():
	code = _code(APP / "product_configurator" / "quoting.py")
	assert (
		"rows = [ { field : row . get ( field ) for field in OPTION_FIELDS } for row in doc . options ]"
		in code
	)
	assert 'doc . set ( "options" , rows )' in code


# ------------------------------------------------------------------ plain words


def test_a_missing_role_reads_as_what_you_cant_do_and_whom_to_ask():
	assert quote.missing_permission_message([("BOM", "create"), ("BOM", "submit")]) == (
		"You can't create bills of materials or submit bills of materials yet, and making this quote "
		"needs both. Ask a System Manager to give you the Manufacturing User role."
	)
	assert quote.missing_permission_message([("Quotation", "create")]) == (
		"You can't create quotations yet, and making this quote needs it. "
		"Ask a System Manager to give you the Sales User role."
	)
	three = quote.missing_permission_message(
		[("Item", "create"), ("Item Price", "create"), ("Supplier", "create")]
	)
	assert three.startswith("You can't create items, set item prices or add suppliers yet")
	assert "needs all of them" in three
	assert "Item Manager, Sales Master Manager and Purchase Master Manager roles" in three
	assert quote.missing_permission_message([]) == ""


def test_no_refusal_speaks_frappe_s_old_words():
	for path in (
		APP / "product_configurator" / "api" / "configurator.py",
		APP / "product_configurator" / "erp_integration.py",
		APP / "product_configurator" / "quoting.py",
	):
		code = _code(path)
		assert "Not permitted ({0} {1})" not in code, path.name
		assert "You need these permissions to generate ERPNext records" not in code, path.name
	assert "Product Engineer role" in (APP / "product_configurator" / "quoting.py").read_text(
		encoding="utf-8"
	)


# ------------------------------------------------------------------ the product-sale rule


@pytest.mark.parametrize(
	"marked, documents, expected",
	[
		(True, [], True),  # a person said so and nothing linked disagrees (the real quote may be in QBO)
		(True, [True], True),
		(True, [True, True], True),
		(True, [True, False], False),  # ticked, but a linked quote carries a fountain: a job
		(True, [False], False),
		(False, [True], False),  # a configured quote alone is never enough
		(False, [True, True], False),
		(False, [], False),
	],
)
def test_a_product_sale_needs_a_persons_tick_and_no_contrary_quote(marked, documents, expected):
	assert product_sale.is_product_sale(marked, documents) is expected


@pytest.mark.parametrize(
	"marked, has_configuration, documents, expected",
	[
		(False, True, [], True),  # a configuration and no quote yet: ask
		(False, False, [True], True),
		(False, True, [True, False], False),  # something else is quoted: don't even ask
		(False, False, [], False),  # nothing product-shaped on the deal
		(True, True, [True], False),  # already ticked: nothing to ask
	],
)
def test_the_configurator_only_suggests(marked, has_configuration, documents, expected):
	assert product_sale.suggests_product_sale(marked, has_configuration, documents) is expected


ESTOP = {"item_code": "PDT-0040-1-1-1-2-0", "source_configuration": "PCFG-2026-00001"}
HAND_MADE_ESTOP = {"item_code": "PDT-0040", "item_name": "PDT-0040 STILLWATER E-STOP SYSTEM"}
FREIGHT = {"item_code": "SRV-105", "item_name": "BUILD - FREIGHT & DELIVERY"}
PLAIN_FREIGHT = {"item_code": "Freight"}
FOUNTAIN = {"item_code": "SF-BASIN-12", "item_name": "Fountain basin, 12 ft"}
PANEL = {"item_code": "PDT-0003", "item_name": "Splash Wizard Industrial"}
BILLING = {"item_code": "PDT-0701", "item_name": "SERVICE - FOUNTAIN MATERIALS & EQUIPMENT"}


def test_a_quote_skips_a_project_by_the_shared_rule():
	"""#1464's product_orders.skips_project, so both PRs answer the same for the same lines."""
	assert product_sale.document_skips_project([ESTOP])
	assert product_sale.document_skips_project([ESTOP, FREIGHT])
	# A hand-made PDT-0040 line and a configured one get the same answer.
	assert product_sale.document_skips_project([HAND_MADE_ESTOP, PLAIN_FREIGHT])
	assert not product_sale.document_skips_project([ESTOP, FOUNTAIN])  # mixed means job
	assert not product_sale.document_skips_project([BILLING])  # a billing family, not a product
	assert not product_sale.document_skips_project([FREIGHT])  # freight alone sells nothing
	assert not product_sale.document_skips_project([])


def test_a_control_panel_quote_keeps_the_project():
	"""Decision pick 1: custom-engineered control panels keep a Project (needs_project)."""
	assert not product_sale.document_skips_project([PANEL])
	assert not product_sale.document_skips_project([ESTOP, PANEL])


class FakeDB:
	"""The three reads ``deal_facts`` makes: ticks, configurations, live linked quote lines."""

	def __init__(self, marked=(), configured=(), lines=(), columns=True, sql_raises=False):
		self.marked = list(marked)
		self.configured = list(configured)
		self.lines = list(lines)  # (opportunity, quotation, item dict)
		self.columns = columns
		self.sql_raises = sql_raises
		self.queries = []

	def has_column(self, doctype, column):
		if doctype.startswith("tab"):
			raise AssertionError("has_column takes a DocType")
		return self.columns

	def sql(self, query, values=None, as_dict=False):
		self.queries.append(query)
		if self.sql_raises:
			raise RuntimeError("database went away")
		wanted = set(values["names"])
		marker = "i.custom_source_configuration" in query
		rows = []
		for opportunity, quotation, item in self.lines:
			if opportunity in wanted:
				rows.append(
					{
						"opportunity": opportunity,
						"quotation": quotation,
						"item_code": item.get("item_code"),
						"item_name": item.get("item_name"),
						"source_configuration": item.get("source_configuration") if marker else "",
					}
				)
		return rows


@pytest.fixture
def fake_frappe(monkeypatch):
	def install(db):
		frappe = types.ModuleType("frappe")
		frappe.db = db

		def get_all(doctype, filters=None, pluck=None, **kwargs):
			if doctype == "Opportunity":
				assert filters.get("custom_product_sale") == 1
				return [name for name in db.marked if name in filters["name"][1]]
			if doctype == "Product Configuration":
				return [name for name in db.configured if name in filters["opportunity"][1]]
			raise AssertionError(doctype)

		frappe.get_all = get_all
		monkeypatch.setitem(sys.modules, "frappe", frappe)
		return frappe

	return install


def test_the_reviewed_blocker_a_fountain_deal_quoting_an_estop_add_on_keeps_its_project(fake_frappe):
	"""The manager review's scenario: the fountain's real quote lives in QuickBooks (never linked),
	a rep quotes an E-Stop add-on (or presses Quote by mistake). Nobody ticks Product Sale."""
	fake_frappe(FakeDB(configured=["OPP-1"], lines=[("OPP-1", "QTN-1", ESTOP)]))
	assert product_sale.product_sale_names(["OPP-1"]) == set()
	assert product_sale.is_product_opportunity("OPP-1") is False
	state = product_sale.deal_state("OPP-1")
	assert state["is_product_sale"] is False
	assert state["suggested"] is True  # asked, never assumed


def test_a_ticked_deal_is_a_product_sale_until_a_linked_quote_says_otherwise(fake_frappe):
	db = FakeDB(
		marked=["OPP-1", "OPP-2", "OPP-3"],
		configured=["OPP-1"],
		lines=[
			("OPP-1", "QTN-1", ESTOP),
			("OPP-1", "QTN-1", FREIGHT),
			("OPP-2", "QTN-2", ESTOP),
			("OPP-2", "QTN-3", FOUNTAIN),
		],
	)
	fake_frappe(db)
	# OPP-3: ticked, nothing linked in ERPNext (its quote may be in QuickBooks): the person's word stands.
	assert product_sale.product_sale_names(["OPP-1", "OPP-2", "OPP-3", "OPP-4", None]) == {"OPP-1", "OPP-3"}
	assert len(db.queries) == 1  # plus two get_all calls: three reads whatever the count
	assert "q.docstatus < 2" in db.queries[0]  # a canceled Quotation says nothing about the deal
	conflict = product_sale.deal_state("OPP-2")
	assert conflict == {"marked": True, "is_product_sale": False, "conflict": True, "suggested": False}


def test_a_database_failure_reads_as_a_job(fake_frappe):
	fake_frappe(FakeDB(marked=["OPP-1"], sql_raises=True))
	assert product_sale.product_sale_names(["OPP-1"]) == set()
	assert product_sale.is_product_opportunity("OPP-1") is False
	assert product_sale.deal_state("OPP-1")["is_product_sale"] is False


def test_no_frappe_at_all_reads_as_a_job(monkeypatch):
	monkeypatch.setitem(sys.modules, "frappe", None)  # import frappe raises ImportError
	assert product_sale.is_product_opportunity("OPP-1") is False
	assert product_sale.deal_state("OPP-1")["is_product_sale"] is False
	assert product_sale.is_product_opportunity("") is False


def test_without_the_marker_column_nothing_is_ticked(fake_frappe):
	fake_frappe(FakeDB(marked=["OPP-1"], lines=[("OPP-1", "QTN-1", ESTOP)], columns=False))
	assert product_sale.product_sale_names(["OPP-1"]) == set()


class SavingOpp:
	def __init__(self, name, marked, new=False):
		self.name = name
		self._marked = marked
		self._new = new

	def is_new(self):
		return self._new

	def get(self, key):
		return self._marked if key == product_sale.MARKER else None


def test_the_save_reads_its_own_tick_not_the_stored_one(fake_frappe):
	"""A person can tick Product Sale in the same save that marks the deal Closed Won."""
	fake_frappe(FakeDB(marked=[], lines=[("OPP-1", "QTN-1", ESTOP)]))
	assert product_sale.is_product_sale_doc(SavingOpp("OPP-1", 1)) is True
	assert product_sale.is_product_sale_doc(SavingOpp("OPP-1", 0)) is False
	assert product_sale.is_product_sale_doc(SavingOpp(None, 1, new=True)) is False
	fake_frappe(FakeDB(marked=[], lines=[("OPP-1", "QTN-1", ESTOP), ("OPP-1", "QTN-2", FOUNTAIN)]))
	assert product_sale.is_product_sale_doc(SavingOpp("OPP-1", 1)) is False


def test_the_pure_modules_import_no_frappe():
	for path in (
		APP / "crm_enhancements" / "product_sale.py",
		APP / "crm_enhancements" / "opportunity_stage_guard.py",
		APP / "product_configurator" / "engine" / "quote.py",
	):
		tree_imports = re.findall(r"^(?:import|from)\s+(\S+)", path.read_text(encoding="utf-8"), re.M)
		assert not any(name.startswith("frappe") for name in tree_imports), path.name


def test_the_item_rule_is_the_shared_one():
	"""One item rule for both PRs: product_orders (#1464), never a copy here."""
	code = (APP / "crm_enhancements" / "product_sale.py").read_text(encoding="utf-8")
	assert "from erpnext_enhancements import product_orders" in code
	assert "product_orders.skips_project(lines)" in code
	# The code with every docstring and comment gone: no item codes, freight or panel rules of its own.
	tree = ast.parse(code)
	for node in ast.walk(tree):
		body = getattr(node, "body", None)
		if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
			if isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(
				body[0].value.value, str
			):
				body.pop(0)
	rule = ast.unparse(tree)
	assert "PDT-" not in rule and "freight" not in rule.lower() and "LINE_PANELS" not in rule


# ------------------------------------------------------------------ the stage guard


@pytest.mark.parametrize(
	"new, previous, automatic, expected",
	[
		("Quotation", "Proposal/Price Quote", True, "Proposal/Price Quote"),  # a submitted quote
		("Converted", "Negotiation/Review", True, "Negotiation/Review"),  # its Sales Order
		("Converted", "Closed Won", True, "Closed Won"),
		("Open", "Proposal/Price Quote", True, "Proposal/Price Quote"),  # the quote canceled
		("Quotation", "Quotation", True, "Proposal/Price Quote"),  # nothing usable before: ERPNext's meaning
		("Converted", None, True, "Closed Won"),
		("Open", "Zoho Stage", True, "Qualification"),
		("Closed", "", True, "Lost"),
		# A lost E-Stop add-on quote must not close the deal (declare_enquiry_lost's db_set).
		("Lost", "Negotiation/Review", True, "Negotiation/Review"),
		("Lost", "Closed Won", True, "Closed Won"),
		# A person setting Lost (a save, with its reason) is left alone.
		("Lost", "Negotiation/Review", False, None),
		("Lost", "Lost", True, None),
		("Lost", None, True, None),  # nothing to go back to
		("Closed Won", "Qualification", True, None),  # on the list and not Lost: left alone
		("Prospecting", "Qualification", True, None),  # not ERPNext's own: not this guard's business
		(None, "Qualification", True, None),
	],
)
def test_erpnexts_own_writes_go_back_to_the_stage_the_deal_had(new, previous, automatic, expected):
	assert guard.restored_status(new, previous, STAGES, automatic=automatic) == expected


def test_a_site_that_offers_the_status_keeps_it():
	assert guard.restored_status("Converted", "Closed Won", [*STAGES, "Converted"]) is None


def test_with_no_fallback_stage_the_first_stage_is_used():
	assert guard.restored_status("Converted", None, ["New", "Won", "Lost"]) == "New"
	assert guard.restored_status("Converted", None, []) is None


class FakeOpp:
	doctype = "Opportunity"
	name = "OPP-1"

	def __init__(self, status, before, options=None, action=None):
		options = "\n".join(STAGES) if options is None else options
		self.status = status
		self._before = {"status": before} if before is not None else None
		self.meta = types.SimpleNamespace(get_field=lambda name: types.SimpleNamespace(options=options))
		self.writes = []
		if action:
			self._action = action  # what every real save sets; ERPNext's db_set leaves it unset

	def get(self, key):
		return getattr(self, key, None)

	def get_doc_before_save(self):
		return self._before

	def db_set(self, field, value, update_modified=True):
		self.writes.append((field, value, update_modified))
		setattr(self, field, value)


def test_the_hook_writes_the_stage_back_without_moving_modified():
	opp = FakeOpp("Quotation", "Negotiation/Review")
	guard.keep_stage_on_list(opp)
	assert opp.writes == [("status", "Negotiation/Review", False)]


def test_the_hook_tells_erpnexts_db_set_from_a_persons_save():
	automatic = FakeOpp("Lost", "Negotiation/Review")
	guard.keep_stage_on_list(automatic)
	assert automatic.writes == [("status", "Negotiation/Review", False)]

	person = FakeOpp("Lost", "Negotiation/Review", action="save")
	guard.keep_stage_on_list(person)
	assert person.writes == []


def test_the_hook_leaves_an_ordinary_save_alone_and_never_raises():
	opp = FakeOpp("Closed Won", "Negotiation/Review", action="save")
	guard.keep_stage_on_list(opp)
	assert opp.writes == []

	broken = FakeOpp("Quotation", "Negotiation/Review")
	broken.meta = None  # meta.get_field raises: logged (or not), never raised
	guard.keep_stage_on_list(broken)
	assert broken.writes == []


def test_erpnexts_label_comment_goes_with_the_status_it_announced(monkeypatch):
	deleted, asked = [], []
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text

	def get_all(doctype, filters=None, pluck=None, order_by=None, limit=None, **kwargs):
		asked.append((doctype, filters, order_by, limit))
		return ["CMT-9"]

	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(delete=lambda doctype, filters: deleted.append((doctype, filters)))
	frappe.log_error = lambda **kwargs: None
	frappe.get_traceback = lambda: ""
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "NOW"
	utils.add_to_date = lambda when, seconds=0: f"{when}{seconds}"
	monkeypatch.setitem(sys.modules, "frappe", frappe)
	monkeypatch.setitem(sys.modules, "frappe.utils", utils)

	guard.keep_stage_on_list(FakeOpp("Quotation", "Proposal/Price Quote"))
	assert deleted == [("Comment", {"name": "CMT-9"})]
	doctype, filters, order_by, limit = asked[0]
	assert doctype == "Comment" and order_by == "creation desc" and limit == 1
	assert filters["comment_type"] == "Label" and filters["reference_name"] == "OPP-1"
	assert filters["content"] == ("in", ["Quotation"])
	assert filters["creation"] == (">=", f"NOW-{guard.LABEL_WINDOW_SECONDS}")

	deleted.clear()
	guard.keep_stage_on_list(FakeOpp("Closed Won", "Qualification", action="save"))
	assert deleted == []  # nothing put back, nothing deleted


# ------------------------------------------------------------------ wiring


def _hooks():
	return (APP / "hooks.py").read_text(encoding="utf-8")


def test_the_stage_guard_runs_first_on_every_opportunity_change():
	hooks = _hooks()
	guard_at = hooks.index(
		'"erpnext_enhancements.crm_enhancements.opportunity_stage_guard.keep_stage_on_list"'
	)
	# The list it sits in: from its `"on_change": [` to the closing `],`.
	opened = hooks.rindex('"on_change": [', 0, guard_at)
	closed = hooks.index("],", guard_at)
	block = hooks[opened:closed]
	assert block.index("opportunity_stage_guard") < block.index(
		'"erpnext_enhancements.task_enhancements.auto_close.on_change"'
	)
	# ...and that list is the Opportunity's: no other doctype's events start between them.
	owner = hooks[:opened]
	assert owner.rindex('"Opportunity": {') > owner.rindex('"Event": {')


def test_a_product_sale_skips_every_project_step():
	handoff = _code(APP / "crm_enhancements" / "handoff.py")
	stamp = handoff[handoff.index("def stamp_handoff_gate") :]
	assert "if _is_product_sale_deal ( doc ) :" in stamp[: stamp.index("def ", 10)]
	helper = handoff[handoff.index("def _is_product_sale_deal") :]
	helper = helper[: helper.index("def ", 10)]
	# The deal's own tick as this save holds it (is_product_sale_doc never raises).
	assert "product_sale . is_product_sale_doc ( doc )" in helper
	rule = _code(APP / "crm_enhancements" / "product_sale.py")
	doc_rule = rule[rule.index("def is_product_sale_doc") :]
	doc_rule = doc_rule[: doc_rule.index("def ", 10)]
	assert "try :" in doc_rule and "except Exception :" in doc_rule
	state = handoff[handoff.index("def handoff_state") :]
	assert state[: state.index("def ", 10)].count("** product_keys") == 2

	prompt = _code(APP / "crm_enhancements" / "project_prompt.py")
	body = prompt[prompt.index("def prompt_create_project_on_won") :]
	assert "product_sale . is_product_sale_doc ( doc )" in body[: body.index("def ", 10)]

	api = _code(APP / "crm_enhancements" / "api.py")
	enqueue = api[api.index("def enqueue_project_creation") :]
	enqueue = enqueue[: enqueue.index("def ", 10)]
	# Before the template is demanded: a product sale needs none.
	assert enqueue.index("is_product_opportunity") < enqueue.index("Please select a Project Template.")
	assert '"status" : "product_sale"' in enqueue


def test_create_project_anyway_is_always_honored():
	"""The escape: create_anyway skips the product-sale answer and clears the tick, in the same
	request as the queueing, so a refusal further down (no template, a bad address) keeps the tick."""
	api = _code(APP / "crm_enhancements" / "api.py")
	enqueue = api[api.index("def enqueue_project_creation") :]
	enqueue = enqueue[: enqueue.index("def ", 10)]
	assert "create_anyway = None" in enqueue
	assert "if not anyway and product_sale . is_product_opportunity ( opportunity_name ) :" in enqueue
	clear = enqueue.index("_clear_product_sale ( opportunity_name )")
	assert (
		enqueue.index("Please select a Project Template.") < clear < enqueue.index("progress . mark_queued")
	)
	clearer = api[api.index("def _clear_product_sale") :]
	clearer = clearer[: clearer.index("def ", 10)]
	assert (
		'frappe . db . set_value ( "Opportunity" , opportunity_name , product_sale . MARKER , 0 )' in clearer
	)


def test_the_tick_is_a_persons_with_write_permission():
	handoff = _code(APP / "crm_enhancements" / "handoff.py")
	at = handoff.index("def mark_product_sale")
	assert handoff[max(0, at - 60) : at].rstrip().endswith('@ frappe . whitelist ( methods = [ "POST" ] )')
	mark = handoff[at:]
	mark = mark[: mark.index("def ", 10)]
	assert 'frappe . has_permission ( "Opportunity" , "write" , doc = opportunity )' in mark
	# Ticking a won deal with no project lifts the hand-off deadline the escalation reads.
	assert '"custom_handoff_gate_applies" : 0' in mark and '"custom_handoff_due_by" : None' in mark
	# The Products value stream follows the person's tick, never a quote.
	assert "if value :" in mark and "_tag_products_stream ( opportunity )" in mark
	quoting = _code(APP / "product_configurator" / "quoting.py")
	assert "_tag_products_stream" not in quoting
	create = quoting[quoting.index("def create_quotation") :]
	create = create[: create.index("def _attach_pricing_summary")]
	# A quote writes nothing on the deal: no tick, no stream, no stage.
	assert 'set_value ( "Opportunity"' not in create and "db_insert" not in create
	assert "mark_product_sale" not in create
	assert '"suggest_product_sale" : suggest' in quoting


def test_the_marker_is_a_visible_field_on_the_hand_off_tab():
	rows = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
	(field,) = [row for row in rows if row["name"] == "Opportunity-custom_product_sale"]
	assert field["dt"] == "Opportunity" and field["fieldname"] == "custom_product_sale"
	assert field["fieldtype"] == "Check" and field["default"] == "0"
	assert field["description"].startswith("Tick when the deal sells only standard products")
	assert not field["read_only"] and not field["hidden"]
	assert field["insert_after"] == "custom_handoff_last_reminded_on"  # the Hand-Off Process tab


def test_the_backlogs_leave_product_sales_out():
	assert "product_sale_names (" in _code(APP / "status_alerts.py")
	assert "product_sale_names (" in _code(APP / "api" / "sales_dashboard.py")
	assert "product_sale_names ( waiting )" in _code(APP / "kpi_dashboards" / "snapshots.py")


def test_the_client_asks_and_keeps_the_escape():
	prompt = (APP / "public" / "js" / "crm_enhancements" / "create_project_prompt.js").read_text(
		encoding="utf-8"
	)
	assert "state.is_product_sale && !opts.create_anyway" in prompt
	assert "create_anyway: opts.create_anyway ? 1 : 0," in prompt
	assert 'result.status === "product_sale"' in prompt
	assert "if (state && state.product_sale_suggested) ask_product_sale(next_step);" in prompt
	tab = (APP / "public" / "js" / "crm_enhancements" / "opportunity_handoff.js").read_text(encoding="utf-8")
	assert "product_sale: !!s.is_product_sale," in tab
	assert "product_suggested: !!s.product_sale_suggested," in tab
	assert 'button("anyway", __("Create Project Anyway"), "default")' in tab
	assert "anyway: create_project_anyway," in tab and "mark_product: mark_product_sale," in tab
	assert "open_create_project_dialog(frm.doc.name, { frm: frm, create_anyway: true })" in tab
	form = (
		APP / "product_configurator" / "doctype" / "product_configuration" / "product_configuration.js"
	).read_text(encoding="utf-8")
	assert "out.suggest_product_sale" in form and "mark_product_sale" in form


def test_the_quote_action_is_on_the_desk_form_and_the_guided_menu():
	actions = (APP / "public" / "js" / "global_enhancements" / "record_actions.js").read_text(
		encoding="utf-8"
	)
	for name in ("can_quote_product", "quote_product_label", "open_quote_product", "quotable_products"):
		assert f"export function {name}(" in actions
	assert 'CONFIGURATOR_API + "start_configuration"' in actions
	assert "frappe.boot.ee_configurable_products" in actions
	menu = (APP / "public" / "js" / "form_kit" / "ui" / "record_tools.js").read_text(encoding="utf-8")
	assert "record_actions.can_quote_product(doctype, values)" in menu
	assert "record_actions.open_quote_product(name)" in menu
	form = (APP / "public" / "js" / "crm_enhancements" / "opportunity.js").read_text(encoding="utf-8")
	# A tab on an old bundle has no such function: the button is skipped, never a TypeError.
	assert 'typeof actions.can_quote_product === "function"' in form
	assert "bootinfo.ee_configurable_products = _configurable_products()" in (APP / "boot.py").read_text(
		encoding="utf-8"
	)


def test_the_endpoints_are_post_only_thin_adapters():
	code = (APP / "product_configurator" / "api" / "configurator.py").read_text(encoding="utf-8")
	for name in ("start_configuration", "create_quotation"):
		at = code.index(f"def {name}(")
		assert code[max(0, at - 60) : at].rstrip().endswith('@frappe.whitelist(methods=["POST"])'), name
		assert f"quoting.{name}(" in code[at:]


def test_quoting_keeps_the_module_rules():
	for path in (
		APP / "product_configurator" / "quoting.py",
		APP / "product_configurator" / "erp_integration.py",
	):
		code = _code(path)
		assert "frappe . db . commit" not in code, path.name
	quoting = _code(APP / "product_configurator" / "quoting.py")
	create = quoting[quoting.index("def create_quotation") :]
	create = create[: create.index("def _attach_pricing_summary")]
	assert create.index("throw_if_product_configurator_disabled ( )") < create.index(
		"erp_integration . generate"
	)
	# The extras run in savepoints and never take the quote down with them.
	assert 'frappe . db . savepoint ( "pc_quote_attach" )' in quoting
	assert 'frappe . db . rollback ( save_point = "pc_quote_attach" )' in quoting
	# A swallowed refusal must not reach the browser as a stray red modal.
	assert "with erp_integration . muted_messages ( ) :" in quoting


def test_the_configured_item_is_a_stock_item_with_a_warehouse_and_its_default_bom():
	code = _code(APP / "product_configurator" / "erp_integration.py")
	item = code[code.index("def _ensure_product_item") :]
	item = item[: item.index("def _make_stockable")]
	assert '"is_stock_item" : 1' in item
	assert '"item_defaults"' in item and '"default_warehouse" : warehouse' in item
	assert "_make_stockable ( item , company , warehouse )" in item
	assert "_point_default_bom ( item_code , bom_name )" in code
	stockable = code[code.index("def _make_stockable") :]
	stockable = stockable[: stockable.index("def _compose_description")]
	assert "item . save ( )" in stockable and 'db_set ( "is_stock_item"' not in stockable


def test_the_doctype_carries_the_new_links_and_a_newer_modified():
	path = APP / "product_configurator" / "doctype" / "product_configuration" / "product_configuration.json"
	meta = json.loads(path.read_text(encoding="utf-8"))
	fields = {field["fieldname"]: field for field in meta["fields"]}
	assert fields["opportunity"]["options"] == "Opportunity"
	assert fields["lead"]["options"] == "Lead"
	assert fields["quotation"]["options"] == "Quotation" and fields["quotation"]["read_only"] == 1
	for name in ("opportunity", "lead", "quotation"):
		assert name in meta["field_order"]
	# The importer skips a DocType file whose `modified` is not newer than the site's row.
	assert meta["modified"] > "2026-07-02 12:00:00.000000"


def test_the_line_link_is_made_on_both_line_tables_and_copied_by_make_sales_order():
	setup = types.ModuleType("setup_under_test")
	source = (APP / "product_configurator" / "setup.py").read_text(encoding="utf-8")
	fields_src = source[source.index("_LINE_FIELD = {") : source.index("def create_configurator_item_fields")]
	exec(fields_src, setup.__dict__)  # data only: two dict literals
	for doctype in ("Quotation Item", "Sales Order Item"):
		(field,) = setup.FIELDS[doctype]
		assert field["fieldname"] == "custom_product_configuration"
		assert field["options"] == "Product Configuration"
		assert not field.get("no_copy")  # Make Sales Order copies same-named fields unless no_copy
	assert quote.quotation_line("X", 1, "", "PCFG-1")["custom_product_configuration"] == "PCFG-1"
