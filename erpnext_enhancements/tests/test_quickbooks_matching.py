"""Bench-free tests for the QuickBooks record-matching review (``core/matching.py``).

The page exists because the dashboard's "Link Existing Records" dialog only listed QBO
records with *no* Sync Mapping row, and after a full import that is none of them -- on
production it answered "No unlinked QuickBooks raw payloads were found" to the one person
who had 2,300 master links to check. What is worth guarding here is not the listing but
the one action that deletes something:

* ``merge_plan`` -- a click on Link folds away **only** the record the import itself
  created (``Created``). A record a person made, one the import linked *to* (``Auto
  Matched``), or one already decided (``Manual Matched``) is never merged because the
  accountant re-pointed a QBO id at something else; a Customer is never folded into a
  Project; a Project is never merged here at all.
* ``decide`` -- the link is committed **before** the merge is attempted, and a merge
  ERPNext refuses comes back as ``merge.status == "failed"`` with the message rather than
  raising, so the decision stands and the duplicate is simply still there.
* ``account_merge_blockers`` -- the four properties ERPNext's own ``merge_account``
  insists on, because ``rename_doc(merge=True)`` alone would fold a Liability into an
  Asset without a word.
* ``decide_many`` -- one failing row is reported and the rest still land, and a ticked
  row's own "Fill blank fields" box reaches only that row.
* The page's bulk actions -- 500-row pages that the server really serves, and *Link
  selected* sent in chunks small enough that a run of merges cannot outrun the gateway.

Plus the wiring that fails silently when it is wrong: a workspace shortcut row without its
content block renders as nothing, a Page whose roles differ from the endpoint gate opens a
refusal, a doctype JSON whose ``modified`` was not bumped never re-imports, and a suite
absent from ``ci.yml`` runs nowhere.

Plain pytest functions (``monkeypatch``), so this file needs its own ``python -m pytest``
step in ``ci.yml``; ``python -m unittest`` would collect nothing from it and report green.

Run: python -m pytest erpnext_enhancements/tests/test_quickbooks_matching.py -q
"""

import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
PAGE_DIR = APP / "quickbooks_online" / "page" / "quickbooks_record_matching"
DASHBOARD_JS = (
	APP / "quickbooks_online" / "page" / "quickbooks_online_dashboard" / "quickbooks_online_dashboard.js"
)
CORE_API = APP / "quickbooks_online" / "core" / "api.py"
MODULE_API = APP / "quickbooks_online" / "api.py"
MAPPING_JSON = (
	APP / "quickbooks_online" / "doctype" / "quickbooks_sync_mapping" / "quickbooks_sync_mapping.json"
)
RAW_JSON = APP / "quickbooks_online" / "doctype" / "quickbooks_raw_payload" / "quickbooks_raw_payload.json"

ENDPOINTS = (
	"get_match_queue",
	"get_parked_transactions",
	"decide_match",
	"decide_matches",
	"confirm_match",
	"confirm_matches",
)


def _read(path):
	return path.read_text(encoding="utf-8")


def _matching():
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import matching

	return matching, frappe


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalise_name_drops_punctuation_and_legal_suffixes():
	matching, _ = _matching()
	assert matching.normalise_name("ABC Supply Co., Inc.") == "abc supply"
	assert matching.normalise_name("  the  ABC-Supply LLC ") == "abc supply"
	# A name that is nothing but suffixes keeps its words rather than vanishing.
	assert matching.normalise_name("The Company") == "the company"
	assert matching.normalise_name(None) == ""


def test_similarity_is_100_only_after_normalised_equality():
	matching, _ = _matching()
	assert matching.similarity("ABC Supply Co", "abc supply, inc.") == 100
	assert matching.similarity("Acme", "Acme Ltd") == 100
	# A two-word subset earns the containment floor; a single word does not.
	assert matching.similarity("ABC Supply", "ABC Supply Salt Lake") >= 85
	assert matching.similarity("ABC", "ABC Supply Salt Lake") < 85
	assert matching.similarity("Wasatch Retail", "Zwick Construction") < matching.SIMILARITY_FLOOR
	assert matching.similarity("", "anything") == 0
	assert matching.similarity("Almost", "Almost.") == 100


def test_rank_candidates_excludes_the_current_link_orders_by_score_and_floors():
	matching, _ = _matching()
	rows = [
		{"name": "ABC Supply Co", "supplier_name": "ABC Supply Co"},
		{"name": "SUP-0001", "supplier_name": "ABC Supply"},
		{"name": "Zebra Ltd", "supplier_name": "Zebra"},
		{"name": "ABC Supply Salt Lake", "supplier_name": "ABC Supply Salt Lake"},
	]
	ranked = matching.rank_candidates(
		"ABC Supply", rows, "supplier_name", doctype="Supplier", exclude={"ABC Supply Co"}
	)
	names = [candidate["name"] for candidate in ranked]
	assert "ABC Supply Co" not in names, "the record already linked is never its own suggestion"
	assert names[0] == "SUP-0001", "a docname that drifted from its title still scores on the title"
	assert names[1] == "ABC Supply Salt Lake"
	assert "Zebra Ltd" not in names
	assert all(
		candidate["doctype"] == "Supplier" and candidate["source"] == "similar" for candidate in ranked
	)
	assert ranked[0]["title"] == "ABC Supply"


@pytest.mark.parametrize(
	"previous, chosen_doctype, chosen_name, merges, reason_fragment",
	[
		({}, "Customer", "Acme", False, "nothing was linked"),
		(
			{"doctype": "Customer", "name": "Acme", "match_status": "Created"},
			"Customer",
			"Acme",
			False,
			"did not change",
		),
		(
			{"doctype": "Customer", "name": "Acme Copy", "match_status": "Auto Matched"},
			"Customer",
			"Acme",
			False,
			"Auto Matched",
		),
		(
			{"doctype": "Customer", "name": "Acme Copy", "match_status": "Manual Matched"},
			"Customer",
			"Acme",
			False,
			"Manual Matched",
		),
		(
			{"doctype": "Customer", "name": "Acme Copy", "match_status": "Pending Review"},
			"Customer",
			"Acme",
			False,
			"Pending Review",
		),
		(
			{"doctype": "Customer", "name": "Acme Copy", "match_status": None},
			"Customer",
			"Acme",
			False,
			"unlabelled",
		),
		(
			{"doctype": "Customer", "name": "Parent Co", "match_status": "Created"},
			"Project",
			"PRJ-00401",
			False,
			"cannot be folded",
		),
		(
			{"doctype": "Project", "name": "PRJ-00999", "match_status": "Created"},
			"Project",
			"PRJ-00401",
			False,
			"Project Merge tool",
		),
		(
			{"doctype": "Supplier", "name": "ABC Supply Co", "match_status": "Created"},
			"Supplier",
			"ABC Supply",
			True,
			"created by the import",
		),
		(
			{"doctype": "Account", "name": "1234 - Old - SF", "match_status": "Created"},
			"Account",
			"1234 - New - SF",
			True,
			"created by the import",
		),
	],
)
def test_merge_plan_only_folds_import_created_copies(
	previous, chosen_doctype, chosen_name, merges, reason_fragment
):
	matching, _ = _matching()
	plan = matching.merge_plan(previous, chosen_doctype, chosen_name)
	assert plan["merge"] is merges
	assert reason_fragment in plan["reason"]
	if merges:
		assert (plan["doctype"], plan["from"], plan["into"]) == (
			previous["doctype"],
			previous["name"],
			chosen_name,
		)


def test_project_is_deliberately_not_mergeable():
	matching, _ = _matching()
	assert "Project" not in matching.MERGEABLE_DOCTYPES
	assert "Project" in matching.NAME_FIELDS, "jobs still get name-similar Project suggestions"


def test_account_merge_blockers_name_each_differing_property():
	matching, _ = _matching()
	old = {"is_group": 0, "root_type": "Asset", "company": "SF", "account_currency": "USD"}
	assert matching.account_merge_blockers(old, dict(old)) == []
	assert matching.account_merge_blockers(old, dict(old, is_group="0")) == [], "0 and '0' are the same fact"
	assert matching.account_merge_blockers(old, dict(old, root_type="Liability", account_currency=None)) == [
		"root_type",
		"account_currency",
	]
	assert matching.account_merge_blockers(None, old) == ["root_type", "company", "account_currency"]


def test_parse_review_notes_reads_issues_and_candidates_and_ignores_snapshots():
	matching, _ = _matching()
	assert matching.parse_review_notes('{"issues": ["Industry is required", ""]}') == {
		"issues": ["Industry is required"],
		"candidates": [],
	}
	notes = matching.parse_review_notes(
		'{"candidates": [{"doctype": "Project", "name": "PRJ-1"}, {"name": ""}, "junk"]}'
	)
	assert notes == {"issues": [], "candidates": [{"doctype": "Project", "name": "PRJ-1"}]}
	# An ordinary owned-fields snapshot is neither.
	assert matching.parse_review_notes('{"customer_name": "Acme"}') == {"issues": [], "candidates": []}
	assert matching.parse_review_notes("not json") == {"issues": [], "candidates": []}
	assert matching.parse_review_notes(None) == {"issues": [], "candidates": []}
	assert matching.parse_review_notes('{"issues": "one string"}') == {"issues": [], "candidates": []}


def test_expected_doctype_routes_jobs_to_project():
	matching, _ = _matching()
	assert matching.expected_doctype("Vendor", {}) == "Supplier"
	assert matching.expected_doctype("TaxCode", {}) == "Account"
	assert matching.expected_doctype("Customer", {"DisplayName": "Acme"}) == "Customer"
	job = {"DisplayName": "PRJ-401 Fountain", "Job": True, "ParentRef": {"value": "1"}}
	assert matching.expected_doctype("Customer", job) == "Project"
	assert matching.expected_doctype("NoSuchEntity", {}) is None


def test_describe_qbo_names_the_facts_an_accountant_recognises():
	matching, _ = _matching()
	described = matching.describe_qbo(
		"Vendor",
		{
			"DisplayName": "ABC Supply",
			"CompanyName": "ABC Supply Company",
			"PrimaryEmailAddr": {"Address": "ap@abc.example"},
			"Active": False,
		},
	)
	assert described["name"] == "ABC Supply"
	assert described["detail"] == ["ABC Supply Company", "ap@abc.example"]
	assert described["inactive"] is True
	assert described["job"] is False
	assert matching.describe_qbo("Item", {}) == {"name": "", "detail": [], "inactive": False, "job": False}


def test_describe_transaction_reads_number_date_total_and_party():
	matching, _ = _matching()
	described = matching.describe_transaction(
		{"DocNumber": "I100780", "TxnDate": "2025-11-04", "TotalAmt": 1234.5, "CustomerRef": {"name": "Acme"}}
	)
	assert described == {"doc_number": "I100780", "txn_date": "2025-11-04", "total": 1234.5, "party": "Acme"}
	assert matching.describe_transaction(None)["party"] == ""


def test_clean_error_strips_markup_and_never_returns_empty():
	matching, _ = _matching()
	assert matching.clean_error(Exception("<b>Currency</b> differs")) == "Currency differs"
	assert matching.clean_error(ValueError()) == "ValueError"


# ---------------------------------------------------------------------------
# decide(): link first, merge only the import's copy, report a refusal
# ---------------------------------------------------------------------------


def _wire_decide(
	monkeypatch, matching, frappe, *, previous, exists=True, rename_raises=None, account_props=None
):
	calls = {"link": [], "stamp": [], "rename": [], "commits": 0, "rollbacks": []}
	monkeypatch.setattr(
		matching,
		"get_mapping",
		lambda entity_type, qbo_id: types.SimpleNamespace(**previous) if previous else None,
	)
	monkeypatch.setattr(
		matching,
		"latest_payloads",
		lambda pairs: {(pairs[0][0], str(pairs[0][1])): {"Id": pairs[0][1], "DisplayName": "ABC Supply"}},
	)

	def link(entity_type, qbo_id, erpnext_doctype, erpnext_name, *, apply_qbo_data=False):
		calls["link"].append((entity_type, qbo_id, erpnext_doctype, erpnext_name, apply_qbo_data))
		return f"QBO-MAP-{entity_type}-{qbo_id}"

	monkeypatch.setattr(matching, "link_existing_record", link)
	monkeypatch.setattr(matching, "_stamp_reviewed", lambda name, user: calls["stamp"].append((name, user)))
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name: exists, raising=False)
	monkeypatch.setattr(
		frappe.db, "commit", lambda: calls.__setitem__("commits", calls["commits"] + 1), raising=False
	)
	monkeypatch.setattr(
		frappe.db,
		"rollback",
		lambda save_point=None: calls["rollbacks"].append(save_point),
		raising=False,
	)
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name, fields=None, **kwargs: (account_props or {}).get(name, {}),
		raising=False,
	)

	def rename(doctype, old, new, **kwargs):
		calls["rename"].append((doctype, old, new, kwargs))
		if rename_raises:
			raise rename_raises

	# matching imports the MODEL-level rename_doc lazily; hand it a stub module.
	rename_module = types.ModuleType("frappe.model.rename_doc")
	rename_module.rename_doc = rename
	monkeypatch.setitem(
		sys.modules, "frappe.model", sys.modules.get("frappe.model") or types.ModuleType("frappe.model")
	)
	monkeypatch.setitem(sys.modules, "frappe.model.rename_doc", rename_module)
	return calls


def test_decide_links_then_folds_only_the_import_created_copy(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Supplier", "erpnext_name": "ABC Supply Co", "match_status": "Created"},
	)
	result = matching.decide("Vendor", "5", " ABC Supply ", fill_blanks=True, user="lisa@example.com")

	assert calls["link"] == [("Vendor", "5", "Supplier", "ABC Supply", True)]
	assert calls["stamp"] == [("QBO-MAP-Vendor-5", "lisa@example.com")]
	assert len(calls["rename"]) == 1
	doctype, old, new, kwargs = calls["rename"][0]
	assert (doctype, old, new) == ("Supplier", "ABC Supply Co", "ABC Supply")
	assert kwargs["merge"] is True and kwargs["force"] is True and kwargs["ignore_permissions"] is True
	assert (
		kwargs["rebuild_search"] is False
	), "one global-search rebuild per click is a queued job for nothing"
	assert result["merge"]["status"] == "merged"
	assert result["linked"] == {"doctype": "Supplier", "name": "ABC Supply"}
	assert result["reviewed_by"] == "lisa@example.com"
	# The link committed before the merge, and the merge committed on its own.
	assert calls["commits"] == 2


def test_decide_reports_a_refused_merge_and_keeps_the_link(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Customer", "erpnext_name": "Acme Copy", "match_status": "Created"},
		rename_raises=Exception("<b>Acme Copy</b> and Acme have different currencies"),
	)
	result = matching.decide("Customer", "9", "Acme", user="lisa@example.com")

	assert calls["link"] and calls["stamp"], "the decision itself landed"
	assert result["merge"]["status"] == "failed"
	assert result["merge"]["from"] == "Acme Copy"
	assert "different currencies" in result["merge"]["error"]
	assert "<b>" not in result["merge"]["error"]
	assert calls["rollbacks"] == ["qbo_match_merge"], "only the merge was rolled back"
	assert calls["commits"] == 1, "the link's commit; the merge never got one"


@pytest.mark.parametrize("status", ["Auto Matched", "Manual Matched", "Pending Review"])
def test_decide_never_merges_a_record_the_import_did_not_create(monkeypatch, status):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Customer", "erpnext_name": "Real Customer", "match_status": status},
	)
	result = matching.decide("Customer", "9", "Other Customer", user="lisa@example.com")
	assert calls["rename"] == []
	assert result["merge"]["status"] == "skipped"
	assert status in result["merge"]["reason"]


def test_decide_leaves_the_copy_in_place_when_merging_is_declined(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Customer", "erpnext_name": "Acme Copy", "match_status": "Created"},
	)
	result = matching.decide("Customer", "9", "Acme", merge_duplicate=False, user="lisa@example.com")
	assert calls["rename"] == []
	assert result["merge"] == {"status": "skipped", "reason": "merge not requested", "duplicate": "Acme Copy"}


def test_decide_skips_a_copy_that_no_longer_exists(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Customer", "erpnext_name": "Acme Copy", "match_status": "Created"},
		exists=False,
	)
	result = matching.decide("Customer", "9", "Acme", user="lisa@example.com")
	assert calls["rename"] == []
	assert result["merge"]["status"] == "skipped" and "no longer exists" in result["merge"]["reason"]


def test_decide_refuses_an_account_merge_erpnext_would_refuse(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(
		monkeypatch,
		matching,
		frappe,
		previous={"erpnext_doctype": "Account", "erpnext_name": "Old - SF", "match_status": "Created"},
		account_props={
			"Old - SF": {"is_group": 0, "root_type": "Liability", "company": "SF", "account_currency": "USD"},
			"New - SF": {"is_group": 0, "root_type": "Asset", "company": "SF", "account_currency": "USD"},
		},
	)
	result = matching.decide("Account", "77", "New - SF", user="lisa@example.com")
	assert calls["rename"] == [], "rename_doc alone would have folded a Liability into an Asset"
	assert result["merge"]["status"] == "failed"
	assert "root_type" in result["merge"]["error"]


def test_decide_requires_a_target_and_a_stored_payload(monkeypatch):
	matching, frappe = _matching()
	_wire_decide(monkeypatch, matching, frappe, previous={})
	with pytest.raises(Exception, match="Choose the ERPNext record"):
		matching.decide("Customer", "9", "   ", user="lisa@example.com")
	monkeypatch.setattr(matching, "latest_payloads", lambda pairs: {})
	with pytest.raises(Exception, match="No QuickBooks payload"):
		matching.decide("Customer", "9", "Acme", user="lisa@example.com")


def test_decide_many_isolates_one_failure(monkeypatch):
	matching, frappe = _matching()
	calls = _wire_decide(monkeypatch, matching, frappe, previous={})
	original_link = matching.link_existing_record

	def link(entity_type, qbo_id, erpnext_doctype, erpnext_name, *, apply_qbo_data=False):
		if qbo_id == "2":
			raise Exception("Supplier Nope does not exist.")
		return original_link(
			entity_type, qbo_id, erpnext_doctype, erpnext_name, apply_qbo_data=apply_qbo_data
		)

	monkeypatch.setattr(matching, "link_existing_record", link)
	results = matching.decide_many(
		[
			{"entity_type": "Vendor", "qbo_id": "1", "erpnext_name": "A"},
			{"entity_type": "Vendor", "qbo_id": "2", "erpnext_name": "Nope"},
			{"entity_type": "Vendor", "qbo_id": "3", "erpnext_name": "C"},
			None,
		],
		user="lisa@example.com",
	)
	assert [result["ok"] for result in results] == [True, False, True, False]
	assert results[1]["error"] == "Supplier Nope does not exist."
	assert results[1]["qbo_id"] == "2"
	assert "qbo_match_decide" in calls["rollbacks"]
	assert [call[1] for call in calls["link"]] == ["1", "3"]


def test_decide_many_lets_a_row_carry_its_own_fill_blanks(monkeypatch):
	"""Link selected sends each ticked row's own "Fill blank fields" box. One row asking for
	the QBO data must not impose it on the rest, and a row that says nothing falls back to
	the call-level flag (Accept suggestions sends none)."""
	matching, frappe = _matching()
	calls = _wire_decide(monkeypatch, matching, frappe, previous={})
	matching.decide_many(
		[
			{"entity_type": "Vendor", "qbo_id": "1", "erpnext_name": "A", "fill_blanks": 1},
			{"entity_type": "Vendor", "qbo_id": "2", "erpnext_name": "B", "fill_blanks": "0"},
			{"entity_type": "Vendor", "qbo_id": "3", "erpnext_name": "C"},
		],
		fill_blanks=False,
		user="lisa@example.com",
	)
	assert [(call[1], call[4]) for call in calls["link"]] == [("1", True), ("2", False), ("3", False)]

	calls["link"].clear()
	matching.decide_many(
		[{"entity_type": "Vendor", "qbo_id": "4", "erpnext_name": "D"}],
		fill_blanks=True,
		user="lisa@example.com",
	)
	assert calls["link"][0][4] is True, "no per-row flag: the call-level one applies"


def test_confirm_many_reports_a_row_with_no_mapping_and_carries_on(monkeypatch):
	matching, frappe = _matching()
	stamped = []
	monkeypatch.setattr(
		matching,
		"get_mapping",
		lambda entity_type, qbo_id: None
		if qbo_id == "2"
		else types.SimpleNamespace(name=f"QBO-MAP-{entity_type}-{qbo_id}", match_status="Auto Matched"),
	)
	monkeypatch.setattr(matching, "_stamp_reviewed", lambda name, user: stamped.append(name))
	monkeypatch.setattr(frappe.db, "commit", lambda: None, raising=False)
	results = matching.confirm_many(
		[
			{"entity_type": "Customer", "qbo_id": "1"},
			{"entity_type": "Customer", "qbo_id": "2"},
			{"entity_type": "Customer", "qbo_id": "3"},
			None,
		],
		user="lisa@example.com",
	)
	assert [result["ok"] for result in results] == [True, False, True, False]
	assert results[1]["qbo_id"] == "2" and "No mapping exists" in results[1]["error"]
	assert results[0]["mapping"] == "QBO-MAP-Customer-1"
	assert stamped == ["QBO-MAP-Customer-1", "QBO-MAP-Customer-3"]


def test_confirm_stamps_and_changes_no_status(monkeypatch):
	matching, frappe = _matching()
	stamped = []
	monkeypatch.setattr(
		matching,
		"get_mapping",
		lambda entity_type, qbo_id: types.SimpleNamespace(
			name="QBO-MAP-Customer-1", match_status="Pending Review"
		),
	)
	monkeypatch.setattr(matching, "_stamp_reviewed", lambda name, user: stamped.append((name, user)))
	monkeypatch.setattr(frappe.db, "commit", lambda: None, raising=False)
	assert matching.confirm("Customer", "1", user="lisa@example.com") == {
		"mapping": "QBO-MAP-Customer-1",
		"reviewed_by": "lisa@example.com",
	}
	assert stamped == [("QBO-MAP-Customer-1", "lisa@example.com")]
	monkeypatch.setattr(matching, "get_mapping", lambda entity_type, qbo_id: None)
	with pytest.raises(Exception, match="No mapping exists"):
		matching.confirm("Customer", "404", user="lisa@example.com")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_master_where_binds_every_value_and_narrows_within_the_status():
	"""The page query and its count share this clause; every caller value is bound and the
	search is ANDed onto the status, never ORed beside it."""
	matching, _ = _matching()
	where, params = matching._master_where(["Customer", "Vendor"], "Needs decision", "  acme ")
	assert where.startswith("qbo_entity_type in %(types)s and coalesce(deleted, 0) = 0")
	assert params["types"] == ("Customer", "Vendor")
	assert "reviewed_on is null" in where
	assert "coalesce(match_status, '') != 'Manual Matched'" in where
	assert where.endswith("and (erpnext_name like %(term)s or qbo_id like %(term)s)")
	assert params["term"] == "%acme%"
	assert "acme" not in where, "user text never reaches the clause"

	where, params = matching._master_where(["Item"], "Conflict", None)
	assert where.endswith("and conflict_status = 'Conflict'") and "term" not in params
	where, params = matching._master_where(["Item"], "Created", "")
	assert where.endswith("and match_status = %(status)s") and params["status"] == "Created"
	where, params = matching._master_where(["Item"], "All", None)
	assert where == "qbo_entity_type in %(types)s and coalesce(deleted, 0) = 0"
	assert set(params) == {"types"}


def test_no_sql_function_is_handed_to_get_all_as_a_string_anywhere_in_the_app():
	"""Frappe 16's query engine refuses ``fields=["count(name) as n"]`` -- *SQL functions are
	not allowed as strings in SELECT* -- and nothing bench-free can see that: the test stub's
	``get_all`` accepts anything, ruff sees a plain string, and the page fails only when a
	browser asks for it. v1.474.0 shipped two of them on a green build and the Record Matching
	page could not load on production. So the shape is checked at every ``get_all`` /
	``get_list`` call site in the app: a string field that starts like a function call."""
	pattern = re.compile(r"^\s*[A-Za-z_][A-Za-z_0-9]*\s*\(")
	offences = []
	for path in sorted(APP.rglob("*.py")):
		if "__pycache__" in path.parts or "tests" in path.parts:
			continue
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except SyntaxError:
			continue
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call):
				continue
			func = node.func
			name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
			if name not in {"get_all", "get_list"}:
				continue
			for keyword in node.keywords:
				if keyword.arg != "fields" or not isinstance(keyword.value, ast.List | ast.Tuple):
					continue
				for element in keyword.value.elts:
					if (
						isinstance(element, ast.Constant)
						and isinstance(element.value, str)
						and pattern.match(element.value)
					):
						offences.append(f"{path.relative_to(APP)}:{element.lineno} {element.value!r}")
	assert offences == [], (
		"Frappe 16 refuses these at runtime; use frappe.db.sql or the dict form:\n" + "\n".join(offences)
	)


def test_the_merge_calls_the_model_level_rename_doc_not_the_alias():
	"""``frappe.rename_doc`` on Frappe 16.30 does not take ``ignore_permissions`` -- the
	keyword that wedged every production migration on 2026-08-07 (test_uom_cleanup
	guards every call site against it). The model-level function does, and the operator
	gate is the boundary here, not the accountant's write permission on Supplier or Item."""
	source = _read(APP / "quickbooks_online" / "core" / "matching.py")
	assert "from frappe.model.rename_doc import rename_doc" in source
	assert "frappe.rename_doc(" not in source
	assert "ignore_permissions=True" in source


def test_page_is_registered_for_exactly_the_operator_roles():
	page = json.loads(_read(PAGE_DIR / "quickbooks_record_matching.json"))
	assert page["name"] == page["page_name"] == "quickbooks-record-matching"
	assert page["module"] == "QuickBooks Online"
	assert page["standard"] == "Yes"
	roles = {row["role"] for row in page["roles"]}
	api = _read(CORE_API)
	assert 'QBO_OPERATOR_ROLES = ("System Manager", "Accounts Manager")' in api
	assert roles == {
		"System Manager",
		"Accounts Manager",
	}, "a page its endpoints refuse is a tile that opens an error"
	assert (PAGE_DIR / "__init__.py").is_file()
	assert "def get_context" in _read(PAGE_DIR / "quickbooks_record_matching.py")


def test_page_script_dials_only_endpoints_that_exist():
	js = _read(PAGE_DIR / "quickbooks_record_matching.js")
	assert 'frappe.pages["quickbooks-record-matching"].on_page_load' in js
	tree = ast.parse(_read(CORE_API))
	whitelisted = {
		node.name
		for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef)
		and any("whitelist" in ast.unparse(decorator) for decorator in node.decorator_list)
	}
	for endpoint in (*ENDPOINTS, "sync_entity"):
		assert f'"{endpoint}"' in js, f"the page never calls {endpoint}"
		assert endpoint in whitelisted, f"{endpoint} is dialled by the page but not whitelisted"


def test_every_page_size_the_page_offers_is_one_the_server_serves():
	"""The server clamps ``page_length`` at ``MAX_PAGE_LENGTH`` without a word. If the page
	offered more, it would show MAX rows and label them "1-500 of N" -- and if it paged by
	what it asked for, Next would skip every row between the two. So the ceiling holds
	the offer, and the page pages by the ``page_length`` the response reports."""
	matching, _ = _matching()
	js = _read(PAGE_DIR / "quickbooks_record_matching.js")
	offered = re.search(r"const PAGE_LENGTHS = \[([\d,\s]+)\]", js)
	assert offered, "PAGE_LENGTHS moved or changed shape; this test reads it"
	sizes = [int(size) for size in offered.group(1).split(",") if size.strip()]
	assert sizes == [50, 100, 200, 500]
	assert max(sizes) <= matching.MAX_PAGE_LENGTH
	assert matching._page_args(0, max(sizes)) == (0, max(sizes))
	assert matching._page_args(0, 10_000) == (0, matching.MAX_PAGE_LENGTH)
	assert matching._page_args(-5, 0) == (0, matching.DEFAULT_PAGE_LENGTH)
	assert js.count("data.page_length ||") == 2, "both tabs page by what the server served"


def test_a_big_page_neither_validates_every_picker_nor_links_in_one_request():
	"""Two things that were fine at 50 rows and are not at 500. The picker pre-fill went
	through ``set_value``, which validates the record with a request of its own -- a
	500-row page would open with 500 of them -- so it goes through ``set_input`` now, the
	server having just confirmed each suggestion exists. And a link can run ``rename_doc``
	merges, so *Link selected* and *Accept suggestions* go to ``decide_matches`` in small
	chunks rather than a whole page in one request that outruns the gateway timeout."""
	js = _read(PAGE_DIR / "quickbooks_record_matching.js")
	assert "control.set_input(chosen)" in js
	assert "control.set_value(best.name)" not in js
	chunk = re.search(r"const LINK_CHUNK = (\d+);", js)
	assert chunk and 1 <= int(chunk.group(1)) <= 25
	assert js.count('"decide_matches"') == 1, "every bulk link goes through the one chunked runner"
	runner = js[js.index("function runDecisions(") :]
	runner = runner[: runner.index("\n\t}\n")]
	assert '"decide_matches"' in runner and "size: LINK_CHUNK" in runner


def test_entity_types_arrive_as_json_csv_or_list_and_leave_as_a_list():
	"""frappe.call serialises an array argument as a JSON string; the first version of the
	parser leaned on a frappe helper the stub lacks and quietly returned the CSV split of
	the JSON text. Parsed with the standard library now, so the two environments agree."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import api

	assert api._entity_list('["Customer", "Vendor"]') == ["Customer", "Vendor"]
	assert api._entity_list("Account, Item,") == ["Account", "Item"]
	assert api._entity_list(["Class", " "]) == ["Class"]
	assert api._entity_list("") is None and api._entity_list(None) is None
	assert api._entity_list("[]") == []
	assert api._entity_list('"Customer"') == ['"Customer"'], "a JSON scalar is not a list; CSV wins"


def test_every_new_endpoint_is_gated_and_re_exported():
	api = _read(CORE_API)
	for endpoint in ENDPOINTS:
		start = api.index(f"def {endpoint}(")
		body = api[start : start + 900]
		assert "_require_qbo_operator()" in body, f"{endpoint} is reachable by any signed-in user"
	reexport = _read(MODULE_API)
	for endpoint in ENDPOINTS:
		assert endpoint in reexport, f"{endpoint} is not callable at ...quickbooks_online.api.{endpoint}"


@pytest.mark.parametrize(
	"path, label",
	[
		("quickbooks_online/workspace/quickbooks_online/quickbooks_online.json", "Record Matching"),
		("accounting_intake/workspace/finance_hub/finance_hub.json", "QuickBooks Matching"),
	],
)
def test_both_workspaces_carry_the_shortcut_and_its_content_block(path, label):
	doc = json.loads(_read(APP / path))
	shortcuts = [row for row in doc["shortcuts"] if row["label"] == label]
	assert len(shortcuts) == 1
	assert shortcuts[0]["type"] == "Page" and shortcuts[0]["link_to"] == "quickbooks-record-matching"
	blocks = json.loads(doc["content"])
	placed = [
		block for block in blocks if block["type"] == "shortcut" and block["data"]["shortcut_name"] == label
	]
	assert len(placed) == 1, "a shortcut row with no content block never renders"
	assert doc["modified"] >= "2026-09-17", "an un-bumped workspace JSON never re-imports"


def test_the_workspace_reload_patch_is_registered_and_forced():
	assert "erpnext_enhancements.patches.reload_workspaces_for_qbo_matching" in _read(APP / "patches.txt")
	source = _read(APP / "patches" / "reload_workspaces_for_qbo_matching.py")
	assert "force=True" in source
	assert '"finance_hub"' in source and '"quickbooks_online"' in source
	assert "def execute" in source


def test_sync_mapping_carries_review_columns_and_the_payload_table_is_indexed():
	mapping = json.loads(_read(MAPPING_JSON))
	fields = {field["fieldname"]: field for field in mapping["fields"]}
	assert fields["reviewed_by"]["fieldtype"] == "Link" and fields["reviewed_by"]["options"] == "User"
	assert fields["reviewed_on"]["fieldtype"] == "Datetime"
	assert fields["reviewed_by"].get("read_only") == 1 and fields["reviewed_on"].get("read_only") == 1
	assert "reviewed_by" in mapping["field_order"] and "reviewed_on" in mapping["field_order"]
	assert (
		"default" not in fields["reviewed_by"] and "default" not in fields["reviewed_on"]
	), "a default on a normal doctype reaches every existing row -- NULL is the right start here"
	assert fields["qbo_id"].get("search_index") == 1
	assert mapping["modified"] >= "2026-09-17"
	raw = json.loads(_read(RAW_JSON))
	raw_fields = {field["fieldname"]: field for field in raw["fields"]}
	assert raw_fields["qbo_id"].get("search_index") == 1, "433k rows and every lookup was a full scan"
	assert raw["modified"] >= "2026-09-17"


def test_dashboard_hands_off_to_the_page_and_the_dead_dialog_is_gone():
	js = _read(DASHBOARD_JS)
	assert 'frappe.set_route("quickbooks-record-matching")' in js
	assert "function showMatchDialog" not in js
	assert "function previewExistingMatches" not in js
	css = _read(APP / "public" / "css" / "quickbooks_online" / "qbo_dashboard.css")
	assert ".qbo-match-row" not in css


def test_stylesheet_is_bundled_and_documented():
	assert (APP / "public" / "css" / "quickbooks_online" / "qbo_matching.css").is_file()
	scss = _read(APP / "public" / "css" / "desk_addons.bundle.scss")
	assert '@import "./quickbooks_online/qbo_matching";' in scss
	assert "qbo_matching.css" in _read(APP / "public" / "README.md")


def test_it_is_wired_into_ci_on_a_pytest_step():
	ci = _read(REPO / ".github" / "workflows" / "ci.yml")
	assert "python -m pytest erpnext_enhancements/tests/test_quickbooks_matching.py" in ci
