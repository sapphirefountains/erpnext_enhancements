"""Bench-free tests for the E-Stop quote-to-order slice (v1.824.0).

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
	"has_configuration, lines, expected",
	[
		(True, [], True),  # configured, not quoted yet
		(True, [True], True),
		(False, [True], True),  # quoted from a configured Item without a linked configuration
		(False, [True, True], True),
		(True, [True, False], False),  # an E-Stop and a fountain: a job, keeps its project
		(False, [False], False),
		(True, [False], False),
		(False, [], False),  # an ordinary deal
	],
)
def test_a_product_sale_is_configured_products_and_nothing_else(has_configuration, lines, expected):
	assert product_sale.is_product_sale(has_configuration, lines) is expected


class FakeDB:
	def __init__(self, configured_opps=(), lines=(), columns=True, sql_raises=False):
		self.configured_opps = list(configured_opps)
		self.lines = list(lines)
		self.columns = columns
		self.sql_raises = sql_raises
		self.queries = []

	def has_column(self, doctype, column):
		if doctype.startswith("tab"):
			raise AssertionError("has_column takes a DocType")
		return self.columns

	def sql(self, query, values=None, as_dict=False):
		self.queries.append((query, values))
		if self.sql_raises:
			raise RuntimeError("database went away")
		wanted = set(values["names"])
		# The query says "0 as configured" when the marker column is missing; honor what it asks.
		marked = "custom_source_configuration" in query
		return [
			{"opportunity": opp, "configured": int(flag) if marked else 0}
			for opp, flag in self.lines
			if opp in wanted
		]


@pytest.fixture
def fake_frappe(monkeypatch):
	def install(db):
		frappe = types.ModuleType("frappe")
		frappe.db = db

		def get_all(doctype, filters=None, pluck=None, **kwargs):
			assert doctype == "Product Configuration"
			wanted = set(filters["opportunity"][1])
			return [opp for opp in db.configured_opps if opp in wanted]

		frappe.get_all = get_all
		monkeypatch.setitem(sys.modules, "frappe", frappe)
		return frappe

	return install


def test_the_batch_read_applies_the_same_rule_in_two_queries(fake_frappe):
	db = FakeDB(
		configured_opps=["OPP-1", "OPP-3"],
		lines=[("OPP-1", True), ("OPP-2", True), ("OPP-3", True), ("OPP-3", False), ("OPP-4", False)],
	)
	fake_frappe(db)
	assert product_sale.product_sale_names(["OPP-1", "OPP-2", "OPP-3", "OPP-4", "OPP-5", None]) == {
		"OPP-1",
		"OPP-2",
	}
	assert len(db.queries) == 1  # plus the one get_all: two reads whatever the count
	query = db.queries[0][0]
	assert "q.docstatus < 2" in query  # a canceled Quotation says nothing about the deal
	assert "ifnull(i.custom_source_configuration, '') != ''" in query
	assert "left join `tabItem` i" in query


def test_a_database_failure_reads_as_not_a_product_sale(fake_frappe):
	fake_frappe(FakeDB(configured_opps=["OPP-1"], sql_raises=True))
	assert product_sale.product_sale_names(["OPP-1"]) == set()
	assert product_sale.is_product_opportunity("OPP-1") is False


def test_no_frappe_at_all_reads_as_not_a_product_sale(monkeypatch):
	monkeypatch.setitem(sys.modules, "frappe", None)  # import frappe raises ImportError
	assert product_sale.is_product_opportunity("OPP-1") is False
	assert product_sale.product_sale_sql("o.name") == "0"
	assert product_sale.is_product_opportunity("") is False


def test_without_the_marker_column_no_quoted_line_counts_as_configured(fake_frappe):
	db = FakeDB(configured_opps=[], lines=[("OPP-1", True)], columns=False)
	fake_frappe(db)
	assert product_sale.product_sale_names(["OPP-1"]) == set()
	assert "custom_source_configuration" not in db.queries[0][0]


def test_the_sql_form_states_the_same_rule(fake_frappe):
	fake_frappe(FakeDB())
	sql = product_sale.product_sale_sql("`tabOpportunity`.name")
	assert sql.count("`tabOpportunity`.name") == 3
	assert (
		"exists (select 1 from `tabProduct Configuration` pc where pc.opportunity = `tabOpportunity`.name)"
		in sql
	)
	assert "and not exists (" in sql and "and not (ifnull(i.custom_source_configuration, '') != '')" in sql
	assert sql.count("q.docstatus < 2") == 2
	fake_frappe(FakeDB(columns=False))
	assert product_sale.product_sale_sql("o.name") == "0"


def test_the_pure_modules_import_no_frappe():
	for path in (
		APP / "crm_enhancements" / "product_sale.py",
		APP / "crm_enhancements" / "opportunity_stage_guard.py",
		APP / "product_configurator" / "engine" / "quote.py",
	):
		tree_imports = re.findall(r"^(?:import|from)\s+(\S+)", path.read_text(encoding="utf-8"), re.M)
		assert not any(name.startswith("frappe") for name in tree_imports), path.name


# ------------------------------------------------------------------ the stage guard


@pytest.mark.parametrize(
	"new, previous, expected",
	[
		("Quotation", "Proposal/Price Quote", "Proposal/Price Quote"),  # a submitted quote
		("Converted", "Negotiation/Review", "Negotiation/Review"),  # its Sales Order
		("Converted", "Closed Won", "Closed Won"),
		("Open", "Proposal/Price Quote", "Proposal/Price Quote"),  # the quote canceled
		("Quotation", "Quotation", "Proposal/Price Quote"),  # nothing usable before: ERPNext's meaning
		("Converted", None, "Closed Won"),
		("Open", "Zoho Stage", "Qualification"),
		("Closed", "", "Lost"),
		("Lost", "Negotiation/Review", None),  # on the list: left alone
		("Closed Won", "Qualification", None),
		("Prospecting", "Qualification", None),  # not ERPNext's own: not this guard's business
		(None, "Qualification", None),
	],
)
def test_erpnexts_own_statuses_go_back_to_the_stage_the_deal_had(new, previous, expected):
	assert guard.restored_status(new, previous, STAGES) == expected


def test_a_site_that_offers_the_status_keeps_it():
	assert guard.restored_status("Converted", "Closed Won", [*STAGES, "Converted"]) is None


def test_with_no_fallback_stage_the_first_stage_is_used():
	assert guard.restored_status("Converted", None, ["New", "Won", "Lost"]) == "New"
	assert guard.restored_status("Converted", None, []) is None


class FakeOpp:
	def __init__(self, status, before, options=None):
		options = "\n".join(STAGES) if options is None else options
		self.status = status
		self._before = {"status": before} if before is not None else None
		self.meta = types.SimpleNamespace(get_field=lambda name: types.SimpleNamespace(options=options))
		self.writes = []

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


def test_the_hook_leaves_an_ordinary_save_alone_and_never_raises():
	opp = FakeOpp("Closed Won", "Negotiation/Review")
	guard.keep_stage_on_list(opp)
	assert opp.writes == []

	broken = FakeOpp("Quotation", "Negotiation/Review")
	broken.meta = None  # meta.get_field raises: logged (or not), never raised
	guard.keep_stage_on_list(broken)
	assert broken.writes == []


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
	# A before_save hook on every deal: guarded, like the Service Call one beside it.
	assert "try :" in helper and "except Exception :" in helper
	assert "product_sale . is_product_opportunity (" in helper
	state = handoff[handoff.index("def handoff_state") :]
	assert state[: state.index("def ", 10)].count('"is_product_sale" : is_product') == 2

	prompt = _code(APP / "crm_enhancements" / "project_prompt.py")
	body = prompt[prompt.index("def prompt_create_project_on_won") :]
	assert "product_sale . is_product_opportunity (" in body[: body.index("def ", 10)]

	api = _code(APP / "crm_enhancements" / "api.py")
	enqueue = api[api.index("def enqueue_project_creation") :]
	enqueue = enqueue[: enqueue.index("def ", 10)]
	# Before the template is demanded: a product sale needs none.
	assert enqueue.index("is_product_opportunity") < enqueue.index("Please select a Project Template.")
	assert '"status" : "product_sale"' in enqueue


def test_the_backlogs_leave_product_sales_out():
	assert "product_sale_names (" in _code(APP / "status_alerts.py")
	assert "product_sale_names (" in _code(APP / "api" / "sales_dashboard.py")
	assert "product_sale_sql (" in _code(APP / "kpi_dashboards" / "snapshots.py")


def test_the_client_shows_a_quiet_line_not_a_project_step():
	prompt = (APP / "public" / "js" / "crm_enhancements" / "create_project_prompt.js").read_text(
		encoding="utf-8"
	)
	assert prompt.count("state.is_product_sale") == 2
	assert 'result.status === "product_sale"' in prompt
	tab = (APP / "public" / "js" / "crm_enhancements" / "opportunity_handoff.js").read_text(encoding="utf-8")
	assert "product_sale: !!s.is_product_sale," in tab
	assert "if (!f.won || (f.product_sale && !f.project)) return by_step;" in tab


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
	assert 'frappe . db . savepoint ( "pc_quote_tag" )' in quoting
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
