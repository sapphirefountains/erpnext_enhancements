# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for what the Procurement Tracker says things cost (v1.814.0).

Two layers, one file (pytest; own CI step):

* ``procurement_prices`` (pure): what was spent on an item, the unit price or its range, what a
  missing price is, what a quote is, and a document's total for the job's lines.
* The feed (``project_enhancements/__init__.py``), which needs a bench to import, so the functions
  under test are lifted out of its source and run against a small stand-in for ``frappe``: the real
  ``_chain_rows`` and ``_documents_and_overview`` with the query's rows handed to them. This is
  where the permission rule is held: **for a person who may not read Purchase Orders the price keys
  are not in the response at all**, and every key that was there before is still there.

Every figure is made up. The repo is public.

Run: python -m pytest erpnext_enhancements/tests/test_procurement_prices.py -q
"""

import ast
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements import procurement_overview  # noqa: E402
from erpnext_enhancements import procurement_prices as prices  # noqa: E402
from erpnext_enhancements import procurement_quantities  # noqa: E402

FEED = APP / "project_enhancements" / "__init__.py"
TOOL = APP / "assistant_tools" / "project_procurement_status.py"
PROJECT = "PRJ-90001"
ACCESS = {"orders": True, "quotes": True}


# ---------------------------------------------------------------------------
# The query's rows
# ---------------------------------------------------------------------------


def chain(**kw):
	"""One record of the feed's chain query: a submitted direct order line at a rate of 10 and a
	quantity of 5, unless the test says otherwise."""
	row = {
		"source_doctype": "Purchase Order",
		"item_code": "IT-100",
		"item_name": "Item 100",
		"mr_qty": 0,
		"mr_item_name": None,
		"mr_requested_qty": None,
		"mr_ordered_qty": None,
		"mr_received_qty": None,
		"line_uom": "Nos",
		"line_stock_uom": "Nos",
		"mr_name": None,
		"transaction_date": "2026-11-01",
		"mr_status": None,
		"rfq_name": None,
		"rfq_status": None,
		"sq_name": None,
		"sq_status": None,
		"po_name": "PO-1",
		"po_status": "To Receive and Bill",
		"po_item_name": "POI-1",
		"po_docstatus": 1,
		"po_line_qty": 5,
		"po_line_received_qty": 0,
		"pr_name": None,
		"pr_status": None,
		"pi_name": None,
		"pi_status": None,
		"se_name": None,
		"se_status": None,
		"warehouse": None,
		"supplier": "Supplier A",
		"expected_date": None,
		"po_conversion_factor": 1,
		"po_line_rate": 10,
		"po_line_base_rate": 10,
		"po_line_base_amount": 50,
		"po_line_free": 0,
		"po_line_uom": "Nos",
		"pr_item_name": None,
		"pr_line_qty": None,
		"sq_item_name": None,
		"sq_line_rate": None,
		"sq_line_base_rate": None,
		"sq_line_base_amount": None,
		"sq_line_conversion_factor": None,
		"pr_docstatus": None,
		"sq_docstatus": None,
		"is_subcontracted": 0,
	}
	row.update(kw)
	# A receipt or a quotation that is named is submitted unless the test says it is not.
	if row["pr_name"] and "pr_docstatus" not in kw:
		row["pr_docstatus"] = 1
	if row["sq_name"] and "sq_docstatus" not in kw:
		row["sq_docstatus"] = 1
	return row


def request_line(**kw):
	"""A Material Request line (requested 10) joined to its order line."""
	base = {
		"source_doctype": "Material Request",
		"mr_qty": 10,
		"mr_item_name": "MRI-1",
		"mr_requested_qty": 10,
		"mr_ordered_qty": 10,
		"mr_received_qty": 0,
		"mr_name": "MR-1",
		"mr_status": "Ordered",
	}
	base.update(kw)
	return chain(**base)


# ---------------------------------------------------------------------------
# A feed stand-in: the real functions, lifted out of the source
# ---------------------------------------------------------------------------

_LIFTED = (
	"_chain_rows",
	"_line_progress",
	"_documents_and_overview",
	"_price_access",
	"_company_currency",
	"_price_total",
	"_line_counts",
	"_fetch_doc_meta",
	"_document_rollup",
	"_po_grain_row",
	"_docstatus_label",
	"_supplementary_documents",
	"_minimal_item_row",
	"get_procurement_overview",
	"PROCUREMENT_DOCTYPE_ORDER",
	"_CHAIN_FIELD",
	"_DATE_FIELD",
	"_HAS_SUPPLIER",
	"_PRICED_DOCTYPES",
	"_ORDER_STAGE_DOCTYPES",
)


def _lift(*names):
	tree = ast.parse(FEED.read_text(encoding="utf-8"))

	def named(node):
		if isinstance(node, ast.FunctionDef):
			return node.name
		if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
			return node.targets[0].id
		return None

	wanted = [node for node in tree.body if named(node) in names]
	assert {named(n) for n in wanted} == set(names), set(names) - {named(n) for n in wanted}
	for node in wanted:
		if isinstance(node, ast.FunctionDef):
			node.decorator_list = []
	module = ast.Module(body=wanted, type_ignores=[])
	namespace = {}
	exec(compile(ast.fix_missing_locations(module), str(FEED), "exec"), namespace)
	return namespace


class Feed:
	"""The lifted feed on a stand-in ``frappe``. ``rows`` is what the chain query returns, ``docs``
	the header rows ``get_all`` finds per doctype, ``lines`` the number of lines each document has,
	``can`` the doctypes the caller may read."""

	def __init__(self, rows, docs=None, lines=None, can=("Purchase Order", "Supplier Quotation")):
		self.rows = rows
		self.docs = docs or {}
		self.lines = lines or {}
		self.can = set(can)
		self.sql_calls = []
		self.ns = _lift(*_LIFTED)
		feed = self

		class _Row(dict):
			__getattr__ = dict.get

		def sql(query, params=None, as_dict=False):
			feed.sql_calls.append(query)
			if "UNION ALL" in query:
				return [dict(r) for r in feed.rows]
			return [{"parent": name, "n": n} for name, n in feed.lines.items()]

		def get_all(doctype, filters=None, fields=None, **kw):
			return [_Row(d) for d in feed.docs.get(doctype, [])]

		frappe = types.SimpleNamespace(
			db=types.SimpleNamespace(
				sql=sql, get_value=lambda *a, **k: "Sapphire Co", get_default=lambda k: "USD"
			),
			get_all=get_all,
			get_meta=lambda dt: types.SimpleNamespace(has_field=lambda f: False),
			has_permission=lambda doctype, ptype="read", **kw: doctype in feed.can,
			get_cached_value=lambda *a, **k: "USD",
			log_error=lambda *a, **k: None,
			get_traceback=lambda: "",
		)
		self.ns.update(
			{
				"frappe": frappe,
				"procurement_quantities": procurement_quantities,
				"procurement_overview": procurement_overview,
				"procurement_prices": prices,
				"_today": lambda: "2026-11-12",
				"require_project_read": lambda project: None,
			}
		)
		# The where-now lookup needs a bench's stock ledger: not what is under test here.
		self.ns["_attach_overview"] = lambda project, rows, access=None: (
			procurement_overview.attach(rows, None, "2026-11-12", prices=bool(access)),
			False,
		)

	def access(self):
		return self.ns["_price_access"]()

	def chain(self, access=None):
		return self.ns["_chain_rows"](PROJECT, access)

	def overview(self):
		return self.ns["get_procurement_overview"](PROJECT)

	def groups(self, access=ACCESS):
		return self.ns["_documents_and_overview"](PROJECT, access)


def flat(result):
	return [row for rows in result.values() for row in rows]


# ---------------------------------------------------------------------------
# Who may see prices
# ---------------------------------------------------------------------------


def test_a_person_who_may_read_purchase_orders_may_see_prices():
	assert Feed([]).access() == {"orders": True, "quotes": True}


def test_a_quote_needs_supplier_quotation_read_too():
	assert Feed([], can=("Purchase Order",)).access() == {"orders": True, "quotes": False}


def test_a_person_who_may_not_read_purchase_orders_may_see_none():
	assert Feed([], can=("Supplier Quotation", "Project")).access() is None
	assert Feed([], can=()).access() is None


def test_the_price_keys_are_not_on_the_rows_without_access():
	feed = Feed([chain(po_line_rate=10, po_line_base_amount=50)])
	row = flat(feed.chain(None))[0]
	assert not (set(prices.PRICE_KEYS) & set(row)), "a price key reached a row it must not reach"
	# ...and nothing that was there before has gone.
	with_access = flat(feed.chain(ACCESS))[0]
	assert set(with_access) == set(row) | set(prices.PRICE_KEYS)
	for key in row:
		assert with_access[key] == row[key], key


def test_the_existing_keys_are_all_still_emitted():
	"""The assistant tool and the tracker read these; adding keys is safe, losing one is not."""
	row = flat(Feed([chain()]).chain(None))[0]
	for key in (
		"item_code", "item_name", "mr_item", "po_item", "row_id", "mr", "rfq", "sq", "po", "pr", "pi",
		"warehouse", "uom", "stock_uom", "po_line_qty", "po_line_received_qty", "po_docstatus", "supplier",
		"expected_date", "conversion_factor", "requested_qty", "ordered_qty", "received_qty",
		"order_status", "receive_status", "completion_percentage",
	):
		assert key in row, key


def test_the_overview_has_no_prices_key_and_no_price_anywhere_without_access():
	feed = Feed([chain()], docs={"Purchase Order": [{"name": "PO-1", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 0}]}, can=())
	out = feed.overview()
	assert "prices" not in out
	text = repr(out)
	for key in prices.PRICE_KEYS + prices.ITEM_PRICE_KEYS + ("price_total", "base_total"):
		assert "'%s'" % key not in text, key


def test_the_overview_carries_the_prices_summary_with_access():
	feed = Feed([chain()], docs={"Purchase Order": [{"name": "PO-1", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 0}]}, lines={"PO-1": 1})
	out = feed.overview()
	assert out["prices"]["spent"] == 50
	assert out["prices"]["currency"] == "USD"
	assert out["prices"]["no_price_lines"] == 0


def test_the_assistant_tool_reads_the_prices_only_through_the_feed():
	"""The tool adds no price of its own: it passes through what the feed decided for this caller."""
	source = TOOL.read_text(encoding="utf-8")
	assert "get_procurement_overview" in source and "get_procurement_documents" in source
	assert "PO_ITEM" not in source.upper().replace("PO_ITEM_NAME", "")
	assert 'requires_permission = "Purchase Order"' in source
	assert 'overview.get("prices")' in source


# ---------------------------------------------------------------------------
# What a line costs
# ---------------------------------------------------------------------------


def test_a_priced_order_line():
	keys = prices.price_keys(chain(po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50))
	assert keys["price_state"] == prices.PRICED
	assert (keys["po_rate"], keys["po_amount"], keys["po_unit_rate"]) == (10, 50, 10)


def test_a_rate_of_zero_is_no_price_and_carries_no_figures():
	keys = prices.price_keys(chain(po_line_rate=0, po_line_base_rate=0, po_line_base_amount=0))
	assert keys["price_state"] == prices.NO_PRICE
	assert keys["po_rate"] is None and keys["po_amount"] is None


def test_a_blank_rate_is_no_price_too():
	assert prices.price_keys(chain(po_line_rate=None))["price_state"] == prices.NO_PRICE


def test_a_free_line_is_no_charge_not_no_price():
	keys = prices.price_keys(chain(po_line_rate=0, po_line_free=1))
	assert keys["price_state"] == prices.NO_CHARGE


def test_a_free_flag_on_a_line_that_has_a_rate_is_still_priced():
	assert prices.price_keys(chain(po_line_rate=4, po_line_base_rate=4, po_line_free=1))["price_state"] == prices.PRICED


def test_the_unit_price_is_per_stock_unit():
	# A line written in boxes of 12, at 24 a box: 2 a piece.
	keys = prices.price_keys(chain(po_conversion_factor=12, po_line_rate=24, po_line_base_rate=24))
	assert keys["po_rate"] == 24
	assert keys["po_unit_rate"] == 2


def test_a_cancelled_order_has_no_price():
	# The query's order join drops a cancelled order but the line's own columns survive the outer join.
	keys = prices.price_keys(chain(po_name=None, po_item_name="POI-9", po_line_rate=10))
	assert keys["price_state"] is None and keys["po_amount"] is None


def test_a_request_with_nothing_ordered_has_no_price_to_be_missing():
	keys = prices.price_keys(request_line(po_name=None, po_item_name=None, po_line_rate=None))
	assert keys["price_state"] is None


def test_received_value_is_received_quantity_at_the_line_rate():
	keys = prices.price_keys(chain(po_line_qty=10, po_line_received_qty=4, po_line_rate=3, po_line_base_rate=3, po_line_base_amount=30))
	assert keys["po_amount"] == 30
	assert keys["received_value"] == 12


def test_a_receipt_is_valued_at_the_orders_rate_not_its_own():
	row = chain(
		po_line_rate=3, po_line_base_rate=3, po_line_base_amount=30, po_line_qty=10, po_line_received_qty=4,
		pr_name="PR-1", pr_item_name="PRI-1", pr_line_qty=4,
	)
	keys = prices.price_keys(row)
	assert keys["receipt_item"] == "PRI-1"
	assert keys["receipt_rate"] == 3
	assert keys["receipt_value"] == 12


def test_a_quote_is_its_own_keys_and_never_the_orders():
	row = request_line(
		po_name=None, po_item_name=None, sq_name="SQ-1", sq_item_name="SQI-1", sq_line_rate=8,
		sq_line_base_rate=8, sq_line_base_amount=80, sq_line_conversion_factor=1,
	)
	keys = prices.price_keys(row)
	assert keys["quote_rate"] == 8 and keys["quote_amount"] == 80 and keys["sq_item"] == "SQI-1"
	assert keys["price_state"] is None and keys["po_amount"] is None


def test_a_quote_is_left_out_without_quote_access():
	row = request_line(po_name=None, po_item_name=None, sq_name="SQ-1", sq_item_name="SQI-1", sq_line_rate=8, sq_line_base_rate=8, sq_line_base_amount=80)
	assert prices.price_keys(row, quotes=False)["quote_rate"] is None


def test_a_quote_of_zero_is_not_a_quote():
	row = request_line(po_name=None, po_item_name=None, sq_name="SQ-1", sq_item_name="SQI-1", sq_line_rate=0)
	assert prices.price_keys(row)["sq_item"] is None


# ---------------------------------------------------------------------------
# By item: what was spent
# ---------------------------------------------------------------------------


def test_the_sum_of_several_lines_at_different_rates():
	"""One item on three orders at three prices: spent is the sum of the lines' own amounts."""
	result = Feed(
		[
			chain(po_name="PO-1", po_item_name="POI-1", po_line_qty=5, po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50),
			chain(po_name="PO-2", po_item_name="POI-2", po_line_qty=5, po_line_rate=12, po_line_base_rate=12, po_line_base_amount=60),
			chain(po_name="PO-3", po_item_name="POI-3", po_line_qty=2, po_line_rate=11, po_line_base_rate=11, po_line_base_amount=22.5),
		]
	).chain(ACCESS)
	items = procurement_overview.by_item(flat(result), "2026-11-12", prices=True)
	assert len(items) == 1
	item = items[0]
	# The line's own base amount, not a recomputed 2 x 11 = 22.
	assert item["spent"] == 132.5
	assert item["spent_lines"] == 3
	assert (item["unit_price_low"], item["unit_price_high"]) == (10, 12)
	# What was spent over what was ordered: 132.5 / 12
	assert round(item["unit_price_avg"], 4) == round(132.5 / 12, 4)
	assert item["no_price_lines"] == 0


def test_lines_that_agree_have_one_price():
	rows = [
		chain(po_name="PO-1", po_item_name="POI-1", po_line_rate=7, po_line_base_rate=7, po_line_base_amount=35),
		chain(po_name="PO-2", po_item_name="POI-2", po_line_rate=7, po_line_base_rate=7, po_line_base_amount=35),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["unit_price_low"] == item["unit_price_high"] == 7
	assert item["spent"] == 70


def test_a_zero_rate_line_is_left_out_of_the_sum_and_counted():
	rows = [
		chain(po_name="PO-1", po_item_name="POI-1", po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50),
		chain(po_name="PO-2", po_item_name="POI-2", po_line_rate=0, po_line_base_rate=0, po_line_base_amount=0),
		chain(po_name="PO-3", po_item_name="POI-3", po_line_rate=0, po_line_base_rate=0, po_line_base_amount=0),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] == 50
	assert item["spent_lines"] == 1
	assert item["no_price_lines"] == 2
	# The unit price is the priced line's alone: a 0 does not drag it down.
	assert (item["unit_price_low"], item["unit_price_high"]) == (10, 10)


def test_an_item_with_only_unpriced_lines_has_no_spent_not_zero():
	rows = [chain(po_line_rate=0, po_line_base_rate=0, po_line_base_amount=0)]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] is None
	assert item["unit_price_low"] is None and item["unit_price_avg"] is None
	assert item["no_price_lines"] == 1


def test_a_free_line_is_counted_as_no_charge_and_left_out():
	rows = [
		chain(po_name="PO-1", po_item_name="POI-1", po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50),
		chain(po_name="PO-2", po_item_name="POI-2", po_line_rate=0, po_line_free=1),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] == 50
	assert item["no_charge_lines"] == 1
	assert item["no_price_lines"] == 0


def test_a_request_split_across_two_orders_is_two_prices_not_one():
	"""The request line is one line; the money is two. The quantity de-duplication keys on the
	request line, which is the wrong grain for a price."""
	rows = [
		request_line(po_name="PO-1", po_item_name="POI-1", po_line_qty=4, po_line_rate=10, po_line_base_rate=10, po_line_base_amount=40),
		request_line(po_name="PO-2", po_item_name="POI-2", po_line_qty=6, po_line_rate=12, po_line_base_rate=12, po_line_base_amount=72),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["line_count"] == 1  # the quantities still see one request line
	assert item["spent"] == 112
	assert item["spent_lines"] == 2
	assert (item["unit_price_low"], item["unit_price_high"]) == (10, 12)


def test_the_same_order_line_seen_twice_is_counted_once():
	"""The feed's invoice and receipt joins repeat an order line: money counts it once."""
	rows = [chain(), chain(pr_name="PR-1", pr_item_name="PRI-1", pr_line_qty=2), chain(pr_name="PR-2", pr_item_name="PRI-2", pr_line_qty=3)]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] == 50
	assert item["spent_lines"] == 1


def test_an_order_spanning_two_projects_counts_only_this_jobs_lines():
	"""The query hands over only the lines that belong to this job, so only those are spent here;
	the document total says it is a part."""
	feed = Feed(
		[chain(po_name="PO-9", po_item_name="POI-9a", po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50)],
		docs={"Purchase Order": [{"name": "PO-9", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 0, "base_total": 350}]},
		lines={"PO-9": 3},
	)
	groups, _rows, items, _located = feed.groups()
	order = groups[0]["documents"][0]
	total = order["price_total"]
	assert total["amount"] == 50
	assert total["partial"] is True
	assert (total["job_lines"], total["doc_lines"]) == (1, 3)
	assert total["doc_total"] == 350
	assert items[0]["spent"] == 50


def test_an_order_wholly_this_jobs_is_not_partial_and_repeats_no_total():
	feed = Feed(
		[chain(po_name="PO-9", po_item_name="POI-9a")],
		docs={"Purchase Order": [{"name": "PO-9", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 0, "base_total": 50}]},
		lines={"PO-9": 1},
	)
	total = feed.groups()[0][0]["documents"][0]["price_total"]
	assert total["partial"] is False and total["doc_total"] is None


def test_a_partial_receipt_values_what_arrived_and_spent_is_still_what_was_ordered():
	feed = Feed(
		[chain(po_line_qty=10, po_line_received_qty=4, po_line_rate=3, po_line_base_rate=3, po_line_base_amount=30, pr_name="PR-1", pr_status="Submitted", pr_item_name="PRI-1", pr_line_qty=4)],
		docs={
			"Purchase Order": [{"name": "PO-1", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 40, "base_total": 30}],
			"Purchase Receipt": [{"name": "PR-1", "status": "To Bill", "docstatus": 1, "doc_date": "2026-11-05", "supplier": "Supplier A"}],
		},
		lines={"PO-1": 1, "PR-1": 1},
	)
	groups, _rows, items, _located = feed.groups()
	by_type = {g["doctype"]: g for g in groups}
	assert by_type["Purchase Order"]["documents"][0]["price_total"]["amount"] == 30
	assert by_type["Purchase Receipt"]["documents"][0]["price_total"]["amount"] == 12
	assert items[0]["spent"] == 30
	assert items[0]["received_value"] == 12


def test_a_draft_order_is_not_spent():
	rows = [
		chain(po_name="PO-1", po_item_name="POI-1", po_docstatus=1, po_line_base_amount=50),
		chain(po_name="PO-2", po_item_name="POI-2", po_docstatus=0, po_status="Draft", po_line_rate=9, po_line_base_rate=9, po_line_base_amount=45),
		chain(po_name="PO-3", po_item_name="POI-3", po_docstatus=0, po_status="Draft", po_line_rate=0, po_line_base_rate=0, po_line_base_amount=0),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] == 50
	assert item["draft_spent"] == 45
	assert item["draft_no_price_lines"] == 1
	assert item["no_price_lines"] == 0


def test_a_quote_is_never_added_into_spent():
	rows = [
		request_line(po_name=None, po_item_name=None, mr_ordered_qty=0, sq_name="SQ-1", sq_item_name="SQI-1", sq_line_rate=8, sq_line_base_rate=8, sq_line_base_amount=80, sq_line_conversion_factor=1),
		request_line(mr_item_name="MRI-2", po_name="PO-1", po_item_name="POI-1", po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] == 50
	assert (item["quote_low"], item["quote_high"], item["quote_count"]) == (8, 8, 1)


def test_an_item_with_only_a_request_has_no_money_at_all():
	rows = [request_line(po_name=None, po_item_name=None, mr_ordered_qty=0)]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["spent"] is None and item["no_price_lines"] == 0 and item["quote_count"] == 0


def test_the_price_keys_are_absent_from_by_item_without_the_flag():
	item = procurement_overview.by_item(flat(Feed([chain()]).chain(None)))[0]
	assert not (set(prices.ITEM_PRICE_KEYS) & set(item))


def test_the_total_across_items_leaves_out_what_has_no_price():
	totals = prices.spent_total(
		[
			{"spent": 50, "spent_lines": 1, "no_price_lines": 2, "no_charge_lines": 0},
			{"spent": None, "spent_lines": 0, "no_price_lines": 1, "no_charge_lines": 1},
			{"spent": 12.5, "spent_lines": 1, "no_price_lines": 0, "no_charge_lines": 0},
		]
	)
	assert totals == {"spent": 62.5, "spent_lines": 2, "no_price_lines": 3, "no_charge_lines": 1}


# ---------------------------------------------------------------------------
# By document: a document's total
# ---------------------------------------------------------------------------


def test_a_purchase_orders_total_counts_what_it_leaves_out():
	rows = [
		prices.price_keys(chain(po_item_name="POI-1")) | {"row_id": "a"},
		prices.price_keys(chain(po_item_name="POI-2", po_line_rate=0)) | {"row_id": "b"},
		prices.price_keys(chain(po_item_name="POI-3", po_line_rate=0, po_line_free=1)) | {"row_id": "c"},
	]
	for r, name in zip(rows, ("POI-1", "POI-2", "POI-3")):
		r["po_item"] = name
	total = prices.document_total("Purchase Order", rows)
	assert total["amount"] == 50
	assert (total["lines"], total["no_price_lines"], total["no_charge_lines"], total["job_lines"]) == (1, 1, 1, 3)


def test_a_request_or_an_invoice_has_no_total():
	assert prices.document_total("Material Request", [{"price_state": "priced"}]) is None
	assert prices.document_total("Purchase Invoice", [{"price_state": "priced"}]) is None
	assert prices.document_total("Request for Quotation", []) is None


def test_a_quotation_total_is_a_quote():
	rows = [{"sq_item": "SQI-1", "quote_amount": 80}, {"sq_item": "SQI-2", "quote_amount": 20}]
	assert prices.document_total("Supplier Quotation", rows)["amount"] == 100


def test_a_document_with_no_price_rows_has_no_total():
	assert prices.document_total("Purchase Order", [{"price_state": None}]) is None


def test_the_document_totals_come_from_set_based_queries():
	"""The count of a document's lines is one query per doctype, however many documents and lines."""
	rows = [
		chain(po_name="PO-%d" % n, po_item_name="POI-%d" % n, item_code="IT-%d" % n) for n in range(1, 30)
	]
	docs = [{"name": "PO-%d" % n, "status": "Draft", "docstatus": 0, "doc_date": "2026-11-01", "supplier": "S"} for n in range(1, 30)]
	feed = Feed(rows, docs={"Purchase Order": docs})
	feed.groups()
	counting = [q for q in feed.sql_calls if "count(*)" in q]
	assert len(counting) == 1
	assert len([q for q in feed.sql_calls if "UNION ALL" in q]) == 1


# ---------------------------------------------------------------------------
# Documents that never joined a chain
# ---------------------------------------------------------------------------


def test_a_draft_order_found_by_the_sweep_has_its_price_and_is_not_spent():
	keys = prices.minimal_price_keys("Purchase Order", 0, {"rate": 9, "base_rate": 9, "base_amount": 45, "qty": 5, "conversion_factor": 1, "uom": "Nos"})
	assert keys["price_state"] == prices.PRICED and keys["price_docstatus"] == 0 and keys["po_amount"] == 45


def test_a_line_that_names_another_job_has_no_price_here():
	keys = prices.minimal_price_keys("Purchase Order", 1, {"rate": 9, "base_amount": 45, "project": "PRJ-90002"}, project=PROJECT)
	assert keys["price_state"] is None and keys["po_amount"] is None
	own = prices.minimal_price_keys("Purchase Order", 1, {"rate": 9, "base_amount": 45, "project": PROJECT}, project=PROJECT)
	assert own["price_state"] == prices.PRICED


def test_a_store_run_receipt_is_its_own_price_and_never_an_order():
	keys = prices.minimal_price_keys("Purchase Receipt", 1, {"name": "PRI-7", "rate": 2, "base_rate": 2, "base_amount": 6})
	assert keys["receipt_state"] == prices.PRICED and keys["receipt_value"] == 6
	assert keys["price_state"] is None, "a receipt with no order must not count as spent"
	free = prices.minimal_price_keys("Purchase Receipt", 1, {"name": "PRI-8", "rate": 0, "allow_zero_valuation_rate": 1})
	assert free["receipt_state"] == prices.NO_CHARGE


def test_a_requests_lines_have_no_price_keys_filled():
	for doctype in ("Material Request", "Request for Quotation", "Purchase Invoice"):
		keys = prices.minimal_price_keys(doctype, 1, {"rate": 5, "base_rate": 5, "base_amount": 25})
		assert all(v is None for v in keys.values()), doctype


def test_the_sweep_asks_for_the_price_columns_only_with_access():
	source = FEED.read_text(encoding="utf-8")
	tree = ast.parse(source)
	fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_supplementary_documents")
	body = ast.unparse(fn)
	assert "if prices:" in body and "'base_amount'" in body


def test_lines_priced_in_different_units_say_so():
	"""On production an FT line and a Unit line of one item both carry a conversion factor of 1, so a
	range can be two prices for two things. The row names the units; the tracker says it."""
	rows = [
		chain(po_name="PO-1", po_item_name="POI-1", po_line_uom="FT", po_line_rate=4.5, po_line_base_rate=4.5, po_line_base_amount=270, po_line_qty=60),
		chain(po_name="PO-2", po_item_name="POI-2", po_line_uom="Unit", po_line_rate=90, po_line_base_rate=90, po_line_base_amount=90, po_line_qty=1),
	]
	item = procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]
	assert item["price_uoms"] == ["FT", "Unit"]
	assert (item["unit_price_low"], item["unit_price_high"]) == (4.5, 90)


# ---------------------------------------------------------------------------
# What Spent is and is not, and which documents are priced (review of v1.814.0)
# ---------------------------------------------------------------------------


def _item(rows):
	return procurement_overview.by_item(flat(Feed(rows).chain(ACCESS)), prices=True)[0]


def test_a_return_receipt_leaves_spent_and_reduces_what_was_received():
	"""A 5-unit order at 10, a receipt of 5, then a return of 2. ERPNext's own received quantity on
	the order line is net of the return (3), so received value is 30; Spent is what was ordered, 50.
	The return keeps its link to the order line, so it is a row with a negative quantity."""
	base = dict(po_line_qty=5, po_line_received_qty=3, po_line_rate=10, po_line_base_rate=10, po_line_base_amount=50)
	rows = [
		chain(pr_name="PR-1", pr_item_name="PRI-1", pr_line_qty=5, **base),
		chain(pr_name="PR-2", pr_item_name="PRI-2", pr_line_qty=-2, **base),
	]
	item = _item(rows)
	assert item["spent"] == 50 and item["spent_lines"] == 1
	assert item["received_value"] == 30
	cards = {r["pr"]: r["receipt_value"] for r in flat(Feed(rows).chain(ACCESS))}
	assert cards == {"PR-1": 50, "PR-2": -20}


def test_a_foreign_currency_order_is_spent_in_the_companys_currency():
	"""The line is 5.00 in the supplier's currency and 5.50 in the company's: only the base figures
	are read."""
	row = chain(po_line_qty=10, po_line_received_qty=4, po_line_rate=5, po_line_base_rate=5.5, po_line_base_amount=55)
	item = _item([row])
	assert item["spent"] == 55
	assert (item["unit_price_low"], item["unit_price_high"]) == (5.5, 5.5)
	# What has arrived is valued at the company-currency rate too: 4 at 5.50, not 4 at 5.00.
	assert item["received_value"] == 22
	line = flat(Feed([row]).chain(ACCESS))[0]
	assert line["po_rate"] == 5.5 and line["po_amount"] == 55 and line["po_unit_rate"] == 5.5


def test_a_line_with_a_conversion_factor_is_spent_at_its_amount_not_rate_times_stock_quantity():
	"""3 boxes of 12 at 24 a box: the amount is 72. Rate times the stock quantity (36) would be 864."""
	row = chain(
		po_line_uom="Box", po_conversion_factor=12, po_line_qty=3, po_line_received_qty=1,
		po_line_rate=24, po_line_base_rate=24, po_line_base_amount=72,
	)
	item = _item([row])
	assert item["spent"] == 72
	assert item["unit_price_low"] == 2  # per stock unit
	assert item["received_value"] == 24  # one box at 24 a box
	assert item["price_uoms"] == ["Box"]


def test_a_discount_on_the_whole_order_is_not_in_spent():
	"""10 units at 10.00 with a 10% discount on the whole order: the order's net is 90.00. Spent is
	the line's own amount, 100.00, and so is the order's total for the job's lines; the documents say
	so (CHANGELOG, tracker map, the column's tooltip)."""
	feed = Feed(
		[chain(po_line_qty=10, po_line_rate=10, po_line_base_rate=10, po_line_base_amount=100)],
		docs={"Purchase Order": [{"name": "PO-1", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "Supplier A", "per_received": 0, "base_total": 100}]},
		lines={"PO-1": 1},
	)
	groups, _rows, items, _located = feed.groups()
	assert items[0]["spent"] == 100
	assert groups[0]["documents"][0]["price_total"]["amount"] == 100


def test_an_order_closed_short_counts_in_full():
	item = _item([chain(po_status="Closed", po_line_qty=10, po_line_received_qty=2, po_line_rate=10, po_line_base_rate=10, po_line_base_amount=100)])
	assert item["spent"] == 100
	assert item["received_value"] == 20


def test_a_canceled_quotation_has_no_quote():
	"""The feed's quotation join lists a canceled quotation (it always did); it gets no price."""
	row = request_line(
		po_name=None, po_item_name=None, mr_ordered_qty=0, sq_name="SQ-1", sq_item_name="SQI-1", sq_docstatus=2,
		sq_line_rate=8, sq_line_base_rate=8, sq_line_base_amount=80, sq_line_conversion_factor=1,
	)
	keys = prices.price_keys(row)
	assert keys["sq_item"] is None and keys["quote_rate"] is None and keys["quote_docstatus"] is None
	item = _item([row])
	assert item["quote_count"] == 0 and item["quote_low"] is None and item["spent"] is None
	# It is still listed: only the price is gone.
	feed = Feed([row], docs={"Supplier Quotation": [{"name": "SQ-1", "status": "Cancelled", "docstatus": 2, "doc_date": "2026-11-01", "supplier": "S"}]}, lines={"SQ-1": 1})
	groups = {g["doctype"]: g for g in feed.groups()[0]}
	assert groups["Supplier Quotation"]["documents"][0]["name"] == "SQ-1"
	assert groups["Supplier Quotation"]["documents"][0]["price_total"] is None


def test_a_draft_quotation_is_a_quote_labeled_as_a_draft():
	row = request_line(
		po_name=None, po_item_name=None, mr_ordered_qty=0, sq_name="SQ-1", sq_item_name="SQI-1", sq_docstatus=0,
		sq_line_rate=8, sq_line_base_rate=8, sq_line_base_amount=80, sq_line_conversion_factor=1,
	)
	keys = prices.price_keys(row)
	assert keys["quote_rate"] == 8 and keys["quote_docstatus"] == 0
	item = _item([row, request_line(mr_item_name="MRI-2", po_name="PO-1", po_item_name="POI-1")])
	assert (item["quote_count"], item["quote_draft_count"]) == (1, 1)
	assert item["spent"] == 50, "a quote is never a spend"
	total = prices.document_total("Supplier Quotation", [keys])
	assert total["docstatus"] == 0 and total["amount"] == 80


def test_a_canceled_receipt_has_no_value():
	"""A canceled receipt (5 units, order rate 10) is still listed with its own Cancelled badge, and
	has no value total. The order's Spent and Received are the order line's own and do not change."""
	row = chain(po_line_qty=5, po_line_received_qty=0, pr_name="PR-1", pr_item_name="PRI-1", pr_line_qty=5, pr_docstatus=2, pr_status="Cancelled")
	keys = prices.price_keys(row)
	assert keys["receipt_item"] is None and keys["receipt_value"] is None and keys["receipt_state"] is None
	assert keys["po_amount"] == 50
	feed = Feed(
		[row],
		docs={
			"Purchase Order": [{"name": "PO-1", "status": "To Receive and Bill", "docstatus": 1, "doc_date": "2026-11-01", "supplier": "S", "per_received": 0, "base_total": 50}],
			"Purchase Receipt": [{"name": "PR-1", "status": "Cancelled", "docstatus": 2, "doc_date": "2026-11-05", "supplier": "S"}],
		},
		lines={"PO-1": 1, "PR-1": 1},
	)
	groups, _rows, items, _located = feed.groups()
	by_type = {g["doctype"]: g for g in groups}
	assert by_type["Purchase Receipt"]["documents"][0]["name"] == "PR-1"
	assert by_type["Purchase Receipt"]["documents"][0]["price_total"] is None
	assert by_type["Purchase Order"]["documents"][0]["price_total"]["amount"] == 50
	assert items[0]["spent"] == 50 and items[0]["received_value"] == 0


def test_a_draft_receipt_is_a_draft_value_not_a_received_one():
	row = chain(po_line_qty=5, pr_name="PR-1", pr_item_name="PRI-1", pr_line_qty=5, pr_docstatus=0)
	keys = prices.price_keys(row)
	assert keys["receipt_value"] == 50 and keys["receipt_docstatus"] == 0
	assert prices.document_total("Purchase Receipt", [dict(keys, po_item="POI-1")])["docstatus"] == 0


def test_the_sweep_prices_a_live_quotation_or_receipt_and_never_a_canceled_one():
	line = {"name": "L-1", "rate": 8, "base_rate": 8, "base_amount": 80, "conversion_factor": 1}
	assert prices.minimal_price_keys("Supplier Quotation", 2, line)["sq_item"] is None
	assert prices.minimal_price_keys("Supplier Quotation", 0, line)["quote_docstatus"] == 0
	assert prices.minimal_price_keys("Purchase Receipt", 2, line)["receipt_item"] is None
	assert prices.minimal_price_keys("Purchase Receipt", 1, line)["receipt_value"] == 80
