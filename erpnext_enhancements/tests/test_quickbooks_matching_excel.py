"""Bench-free tests for the Record Matching page's Excel round trip (``core/matching_excel.py``).

Nik wanted the matching screens in Excel so Meg can work on them there, and her work brought
back into ERPNext. What is worth guarding is everything that could quietly do the wrong thing
on the way back in:

* **Export shaping** -- the sheet rows say what the page says, the Link to column opens on the
  page's own best suggestion, and the hidden Row version fingerprints exactly what a decision
  depends on (an unlinked row and a parked transaction must not read as changed the moment
  they are exported).
* **The workbook** -- Instructions first, the export marker hidden, the key columns hidden, the
  Decision and Link to dropdowns present (the Link to list is relative, one per row).
* **Parsing** -- columns found by header, Meg's own sheets and blank rows ignored, an ID Excel
  turned into a float read back as text, a foreign or older file refused.
* **The preview classification** -- will change / already done / changed since the export /
  cannot be applied, decided in that order, because "already done" must win over "changed"
  or a second import of the same file would report every row stale.
* **Apply** -- only rows that are *still* a change run, through the page's own ``decide`` /
  ``confirm`` / ``sync_entity``; one failure never stops the rest; and importing the same file
  twice changes nothing the second time, notes included.

Plain pytest functions (``monkeypatch``), so this file has its own ``python -m pytest`` step in
``ci.yml`` -- ``python -m unittest`` would collect nothing from it and report green. It needs
the real openpyxl (Frappe v16 pins ``openpyxl~=3.1.5``), which that step installs: the
dropdowns and hidden columns are exactly what a stub could not check.

Run: python -m pytest erpnext_enhancements/tests/test_quickbooks_matching_excel.py -q
"""

import ast
import io
import re
import types
from pathlib import Path

import pytest

from erpnext_enhancements.tests.test_quickbooks_online import install_frappe_stub

openpyxl = pytest.importorskip("openpyxl")

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
CORE_API = APP / "quickbooks_online" / "core" / "api.py"
MODULE_API = APP / "quickbooks_online" / "api.py"
PAGE_JS = APP / "quickbooks_online" / "page" / "quickbooks_record_matching" / "quickbooks_record_matching.js"

ENDPOINTS = (
	"start_matching_export",
	"get_matching_export_status",
	"preview_matching_import",
	"apply_matching_import",
)


def _excel():
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import matching_excel

	return matching_excel, frappe


def _master_row(**overrides):
	row = {
		"mapping": "MAP-1",
		"entity_type": "Vendor",
		"qbo_id": "56",
		"qbo": {"name": "ABC Supply", "detail": ["abc@example.com"], "inactive": False, "job": False},
		"has_payload": True,
		"expected_doctype": "Supplier",
		"link": {"doctype": "Supplier", "name": "ABC Supply-1", "title": "ABC Supply", "exists": True},
		"match_status": "Created",
		"conflict_status": "Clean",
		"match_rule": None,
		"issues": [],
		"suggestions": [
			{
				"doctype": "Supplier",
				"name": "ABC Supply Co",
				"title": "ABC Supply Co",
				"score": 100,
				"rule": "name similarity",
			},
			{
				"doctype": "Supplier",
				"name": "SUP-0009",
				"title": "ABC Supplies",
				"score": 88,
				"rule": "name similarity",
			},
		],
		"reviewed_by": None,
		"reviewed_on": None,
		"modified": "2026-10-20 10:00:00.000001",
	}
	row.update(overrides)
	return row


def _txn_row(**overrides):
	row = {
		"mapping": "MAP-T1",
		"entity_type": "Invoice",
		"qbo_id": "9001",
		"doctype": "Sales Invoice",
		"name": None,
		"exists": False,
		"qbo": {"doc_number": "1042", "txn_date": "2026-09-01", "total": 1250.5, "party": "Acme"},
		"issues": ["Industry is required"],
		"match_rule": "preflight",
		"modified": "2026-10-19 08:00:00",
	}
	row.update(overrides)
	return row


def _current(**overrides):
	"""A mapping row as ``_current_rows`` reads it, matching ``_master_row``'s export state."""
	row = {
		"name": "MAP-1",
		"qbo_entity_type": "Vendor",
		"qbo_id": "56",
		"erpnext_doctype": "Supplier",
		"erpnext_name": "ABC Supply-1",
		"match_status": "Created",
		"conflict_status": "Clean",
		"reviewed_on": None,
		"modified": "2026-10-20 10:00:00.000001",
		"deleted": 0,
	}
	row.update(overrides)
	return row


def _workbook(matching_excel, masters=(), txns=()):
	sheets = [
		(
			"Vendors",
			matching_excel.MASTER_COLUMNS,
			[matching_excel.master_sheet_row(r) for r in masters],
			matching_excel.MASTER_DECISIONS,
		)
	]
	sheets.append(
		(
			matching_excel.TXN_SHEET,
			matching_excel.TXN_COLUMNS,
			[matching_excel.txn_sheet_row(r) for r in txns],
			matching_excel.TXN_DECISIONS,
		)
	)
	meta = {
		"exported_on": "2026-10-22 09:00",
		"exported_by": "nik@example.com",
		"filter": "Needs decision",
		"counts": {"Vendors": len(masters)},
	}
	return matching_excel.build_workbook(sheets, meta)


def _fill(content, edits):
	"""Edit an exported workbook the way Meg would: ``{sheet: {row: {header: value}}}``."""
	workbook = openpyxl.load_workbook(io.BytesIO(content))
	for sheet_name, rows in edits.items():
		sheet = workbook[sheet_name]
		headers = {cell.value: cell.column for cell in sheet[1]}
		for row_number, cells in rows.items():
			for header, value in cells.items():
				# .value, not cell(value=...): openpyxl's cell() ignores a None, and clearing is an edit too.
				sheet.cell(row=row_number, column=headers[header]).value = value
	out = io.BytesIO()
	workbook.save(out)
	return out.getvalue()


# ---------------------------------------------------------------------------
# Export shaping
# ---------------------------------------------------------------------------


def test_master_sheet_row_says_what_the_page_says_and_prefills_the_best_suggestion():
	matching_excel, _ = _excel()
	row = matching_excel.master_sheet_row(_master_row())
	assert row["qbo_type"] == "Vendor" and row["qbo_id"] == "56" and row["qbo_name"] == "ABC Supply"
	assert row["linked_id"] == "ABC Supply-1" and row["linked_doctype"] == "Supplier"
	assert row["status"] == "Created" and row["target_doctype"] == "Supplier"
	assert (
		row["link_to"] == "ABC Supply Co"
	), "the page's picker opens on the best suggestion; so does the file"
	assert row["decision"] == "", "nothing is decided for Meg"
	assert row["suggestion_1"] == "ABC Supply Co (100%, name similarity)"
	assert row["suggestion_2"] == "ABC Supplies [SUP-0009] (88%, name similarity)"
	assert row["suggestion_id_2"] == "SUP-0009" and row["suggestion_id_3"] == ""
	assert row["row_id"] == "MAP-1" and row["exported_status"] == "Created"
	assert row["modified"] == "2026-10-20 10:00:00.000001"


def test_master_sheet_row_flags_conflicts_missing_records_and_absent_payloads():
	matching_excel, _ = _excel()
	row = matching_excel.master_sheet_row(
		_master_row(
			conflict_status="Conflict",
			has_payload=False,
			link={"doctype": "Supplier", "name": "Gone", "title": "Gone", "exists": False},
			qbo={"name": "Job", "detail": [], "inactive": True, "job": True},
			issues=["Industry is required", "Territory"],
			suggestions=[],
		)
	)
	assert row["status"] == "Created (conflict)"
	assert row["linked_title"] == "Gone (no longer exists)"
	assert "QBO job" in row["qbo_detail"] and "inactive in QBO" in row["qbo_detail"]
	assert "no payload stored" in row["qbo_detail"]
	assert row["issues"] == "Industry is required\nTerritory"
	assert row["link_to"] == ""


def test_row_version_matches_the_database_state_it_was_exported_from():
	"""The export computes the version from the page row; the import from the mapping row.
	If the two ever disagree for an untouched row, every decision reads as stale."""
	matching_excel, _ = _excel()
	exported = matching_excel.master_sheet_row(_master_row())["row_version"]
	assert exported == matching_excel._current_version(_current(), "master")
	# Every fact a decision depends on moves it...
	for change in (
		{"erpnext_name": "Other"},
		{"match_status": "Manual Matched"},
		{"conflict_status": "Conflict"},
		{"reviewed_on": "2026-10-21 12:00:00"},
	):
		assert matching_excel._current_version(_current(**change), "master") != exported, change
	# ...and modified alone does not: a CDC touch is not a reason to discard Meg's work.
	assert matching_excel._current_version(_current(modified="2026-10-22 01:00:00"), "master") == exported


def test_an_unlinked_row_is_not_stale_because_the_mapping_still_names_a_doctype():
	matching_excel, _ = _excel()
	exported = matching_excel.master_sheet_row(_master_row(link=None, match_status="Pending Review"))[
		"row_version"
	]
	current = _current(erpnext_name=None, erpnext_doctype="Supplier", match_status="Pending Review")
	assert exported == matching_excel._current_version(current, "master")


def test_a_parked_transaction_version_ignores_what_the_parked_list_never_reads():
	matching_excel, _ = _excel()
	row = matching_excel.txn_sheet_row(_txn_row())
	current = _current(
		name="MAP-T1",
		qbo_entity_type="Invoice",
		qbo_id="9001",
		erpnext_doctype="Sales Invoice",
		erpnext_name=None,
		match_status="Pending Review",
		conflict_status="Clean",
		reviewed_on="2026-10-01",
	)
	assert row["row_version"] == matching_excel._current_version(current, "txn")
	assert row["draft"] == "" and row["total"] == 1250.5 and row["issues"] == "Industry is required"
	assert row["exported_status"] == "Pending Review"
	gone = matching_excel.txn_sheet_row(_txn_row(name="ACC-SINV-1", exists=False))
	assert gone["draft"] == "Sales Invoice ACC-SINV-1 (gone)"


def test_collect_export_pages_through_the_queue_by_the_length_served(monkeypatch):
	"""It pages by the page_length the server reports serving, like the page, so a clamp
	can never skip rows -- and groups the rows into a sheet per master type."""
	matching_excel, _ = _excel()
	rows = [
		_master_row(mapping=f"M{n}", qbo_id=str(n), entity_type="Vendor" if n % 2 else "Customer")
		for n in range(7)
	]
	calls = []

	def queue(entity_types=None, status=None, search=None, start=0, page_length=50):
		calls.append((status, start))
		return {"rows": rows[start : start + 3], "total": len(rows), "page_length": 3}

	parked = [_txn_row(mapping=f"T{n}", qbo_id=str(n)) for n in range(2)]
	monkeypatch.setattr(matching_excel.matching, "master_queue", queue)
	monkeypatch.setattr(
		matching_excel.matching,
		"parked_transactions",
		lambda entity_types=None, start=0, page_length=50: {
			"rows": parked[start:],
			"total": 2,
			"page_length": 500,
		},
	)
	sheets = matching_excel.collect_export(status="Bogus", entity_types=["Vendor", "Customer", "Nope"])
	assert [call[1] for call in calls] == [0, 3, 6]
	assert {call[0] for call in calls} == {"Needs decision"}, "an unknown filter falls back to the default"
	titles = {title: len(sheet_rows) for title, _c, sheet_rows, _d in sheets}
	assert titles == {"Vendors": 3, "Customers": 4, "Parked transactions": 2}
	without = matching_excel.collect_export(status="All", entity_types=None, include_parked=False)
	assert "Parked transactions" not in [title for title, *_ in without]


# ---------------------------------------------------------------------------
# The workbook
# ---------------------------------------------------------------------------


def test_workbook_has_instructions_hidden_keys_and_dropdowns():
	matching_excel, _ = _excel()
	content = _workbook(matching_excel, masters=[_master_row()], txns=[_txn_row()])
	workbook = openpyxl.load_workbook(io.BytesIO(content))
	assert workbook.sheetnames[0] == "Instructions"
	assert workbook[matching_excel.META_SHEET].sheet_state == "hidden"
	instructions = "\n".join(str(cell.value or "") for cell in workbook["Instructions"]["A"])
	for word in ("Keep", "Link", "Retry", "Notes", ".xlsx", "Apply"):
		assert word in instructions

	sheet = workbook["Vendors"]
	headers = {cell.value: cell.column_letter for cell in sheet[1]}
	for hidden in ("Row ID", "Row version", "Modified", "Status at export", "Suggestion 1 ID"):
		assert sheet.column_dimensions[headers[hidden]].hidden, f"{hidden} should be hidden"
	for shown in ("QBO ID", "QuickBooks name", "Decision", "Link to (ERPNext ID)", "Notes"):
		assert not sheet.column_dimensions[headers[shown]].hidden, f"{shown} should be visible"

	validations = {str(dv.sqref): dv for dv in sheet.data_validations.dataValidation}
	decision = validations[f"{headers['Decision']}2"]
	assert decision.formula1 == '"Link,Keep,Retry"'
	link_to = validations[f"{headers['Link to (ERPNext ID)']}2"]
	assert (
		link_to.formula1 == f"${headers['Suggestion 1 ID']}2:${headers['Suggestion 5 ID']}2"
	), "one relative list, so each row's dropdown is that row's own suggestions"
	assert not link_to.showErrorMessage, "any other ERPNext ID may be typed"
	txn = workbook["Parked transactions"]
	txn_headers = {cell.value: cell.column_letter for cell in txn[1]}
	assert [dv.formula1 for dv in txn.data_validations.dataValidation] == ['"Retry"']
	assert txn[f"{txn_headers['Total']}2"].value == 1250.5


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_reads_meg_edits_by_header_and_ignores_her_own_sheets():
	matching_excel, _ = _excel()
	content = _workbook(
		matching_excel, masters=[_master_row(), _master_row(mapping="MAP-2", qbo_id="57")], txns=[_txn_row()]
	)
	content = _fill(
		content,
		{
			"Vendors": {
				2: {
					"Decision": "link",
					"Link to (ERPNext ID)": 1001.0,
					"Fill blank fields from QBO": "yes",
					"Notes": "  check with Lisa ",
				},
				3: {"Decision": "Maybe"},
			},
			"Parked transactions": {2: {"Decision": "RETRY"}},
		},
	)
	workbook = openpyxl.load_workbook(io.BytesIO(content))
	workbook.create_sheet("Meg scratch").append(["Row ID", "whatever"])  # has no other keys: skipped
	workbook.create_sheet("Totals").append(["sum", 3])
	vendors = workbook["Vendors"]
	vendors.insert_cols(1)  # Meg adds a column on the left: headers still find everything
	vendors.cell(row=1, column=1, value="My column")
	vendors.append([])  # a blank row at the end
	out = io.BytesIO()
	workbook.save(out)

	parsed = matching_excel.parse_workbook(out.getvalue())
	assert parsed["errors"] == ["Sheet 'Meg scratch' is missing columns it needs; it was skipped."]
	rows = {(row["sheet"], row["row_number"]): row for row in parsed["rows"]}
	first = rows[("Vendors", 2)]
	assert first["kind"] == "master" and first["decision"] == "Link" and first["decision_ok"]
	assert first["link_to"] == "1001", "an ID Excel stored as a number comes back as text"
	assert first["fill_blanks"] is True and first["notes"] == "check with Lisa"
	assert first["row_id"] == "MAP-1" and first["qbo_id"] == "56" and first["exported_status"] == "Created"
	assert rows[("Vendors", 3)]["decision"] == "Maybe" and not rows[("Vendors", 3)]["decision_ok"]
	txn = rows[("Parked transactions", 2)]
	assert txn["kind"] == "txn" and txn["decision"] == "Retry"


@pytest.mark.parametrize(
	"content, fragment",
	[
		(b"not a zip at all", "not an Excel workbook"),
		(None, "not a Record Matching export"),
		("old", "different version"),
	],
)
def test_parse_refuses_files_that_are_not_this_export(content, fragment):
	matching_excel, _ = _excel()
	if content is None:
		workbook = openpyxl.Workbook()
		workbook.active.append(["Row ID", "QBO type"])
		out = io.BytesIO()
		workbook.save(out)
		content = out.getvalue()
	elif content == "old":
		workbook = openpyxl.load_workbook(io.BytesIO(_workbook(matching_excel, masters=[_master_row()])))
		workbook[matching_excel.META_SHEET]["B1"] = "0"
		out = io.BytesIO()
		workbook.save(out)
		content = out.getvalue()
	parsed = matching_excel.parse_workbook(content)
	assert parsed["rows"] == []
	assert fragment in parsed["errors"][0]


# ---------------------------------------------------------------------------
# Preview classification
# ---------------------------------------------------------------------------


def _parsed(matching_excel, **overrides):
	row = {
		"sheet": "Vendors",
		"row_number": 2,
		"kind": "master",
		"row_id": "MAP-1",
		"qbo_type": "Vendor",
		"qbo_id": "56",
		"qbo_name": "ABC Supply",
		"row_version": matching_excel._current_version(_current(), "master"),
		"modified": "2026-10-20 10:00:00.000001",
		"linked_doctype": "Supplier",
		"linked_id": "ABC Supply-1",
		"exported_status": "Created",
		"decision": "",
		"decision_ok": True,
		"link_to": "ABC Supply Co",
		"fill_blanks": False,
		"notes": "",
	}
	row.update(overrides)
	return row


def _classify(matching_excel, row, current="default", **kwargs):
	kwargs.setdefault("target_doctype", "Supplier")
	kwargs.setdefault("target_exists", True)
	return matching_excel.classify_row(row, _current() if current == "default" else current, **kwargs)


@pytest.mark.parametrize(
	"overrides, current, kwargs, fragment",
	[
		({"row_id": ""}, "default", {}, "Row ID is empty"),
		({}, None, {}, "No QuickBooks mapping"),
		({"qbo_id": "99"}, "default", {}, "Were the QBO type or QBO ID cells edited"),
		({"decision": "Keep"}, {"deleted": 1}, {}, "deleted in QuickBooks"),
		({"decision": "Maybe", "decision_ok": False}, "default", {}, "is not a decision"),
		({"decision": "Link", "link_to": ""}, "default", {}, "'Link to' is empty"),
		(
			{"decision": "Link", "link_to": "Nope"},
			"default",
			{"target_exists": False},
			"There is no Supplier",
		),
		({"decision": "Link"}, "default", {"target_doctype": None}, "no ERPNext destination"),
		({"decision": "Retry"}, "default", {}, "Only a row in Pending Review"),
		({"decision": "Keep", "kind": "txn"}, "default", {}, "is not a decision"),
	],
)
def test_preview_says_why_a_row_cannot_be_applied(overrides, current, kwargs, fragment):
	matching_excel, _ = _excel()
	if isinstance(current, dict):
		current = _current(**current)
	result = _classify(matching_excel, _parsed(matching_excel, **overrides), current, **kwargs)
	assert result["category"] == "invalid", result
	assert fragment in result["message"]
	assert not result["action"] and not result["save_note"]


def test_a_row_that_changed_in_erpnext_since_the_export_is_stale_not_applied():
	matching_excel, _ = _excel()
	moved = _current(erpnext_name="Someone Else", match_status="Manual Matched", reviewed_on="2026-10-21")
	result = _classify(matching_excel, _parsed(matching_excel, decision="Keep"), moved)
	assert result["category"] == "stale"
	assert "Someone Else" in result["message"]
	# A link someone else made that differs from Meg's pick is stale too.
	result = _classify(matching_excel, _parsed(matching_excel, decision="Link"), moved)
	assert result["category"] == "stale"


def test_already_done_wins_over_stale_so_a_second_import_reports_unchanged():
	matching_excel, _ = _excel()
	linked = _current(
		erpnext_name="ABC Supply Co", match_status="Manual Matched", reviewed_on="2026-10-22 10:00"
	)
	assert (
		_classify(matching_excel, _parsed(matching_excel, decision="Link"), linked)["category"] == "unchanged"
	)
	kept = _current(reviewed_on="2026-10-22 10:00")
	assert (
		_classify(matching_excel, _parsed(matching_excel, decision="Keep"), kept)["category"] == "unchanged"
	)
	retried = _current(match_status="Created")
	row = _parsed(matching_excel, decision="Retry", exported_status="Pending Review")
	assert _classify(matching_excel, row, retried)["category"] == "unchanged"


def test_changes_say_what_will_happen():
	matching_excel, _ = _excel()
	link = _classify(matching_excel, _parsed(matching_excel, decision="Link", fill_blanks=True))
	assert link["category"] == "change" and link["action"] == "Link"
	assert "Links to Supplier ABC Supply Co (now Supplier ABC Supply-1)" in link["message"]
	assert (
		"Merges the import's copy ABC Supply-1 into it" in link["message"]
	), "the merge policy is named up front"
	assert "Fills its blank fields" in link["message"]

	same = _classify(matching_excel, _parsed(matching_excel, decision="Link", link_to="ABC Supply-1"))
	assert same["category"] == "change" and "Confirms the link" in same["message"]
	assert "Merges" not in same["message"]

	keep = _classify(matching_excel, _parsed(matching_excel, decision="Keep"))
	assert keep["category"] == "change" and keep["action"] == "Keep"

	parked = _current(match_status="Pending Review")
	row = _parsed(
		matching_excel,
		decision="Retry",
		exported_status="Pending Review",
		row_version=matching_excel._current_version(parked, "master"),
	)
	retry = _classify(matching_excel, row, parked)
	assert retry["category"] == "change" and retry["action"] == "Retry"


def test_notes_are_saved_once_with_or_without_a_decision():
	matching_excel, _ = _excel()
	note_only = _classify(matching_excel, _parsed(matching_excel, notes="Ask Lisa"))
	assert note_only["category"] == "change" and note_only["save_note"] and not note_only["action"]
	saved = _classify(matching_excel, _parsed(matching_excel, notes="Ask Lisa"), note_saved=True)
	assert saved["category"] == "unchanged"
	done_with_note = _classify(
		matching_excel,
		_parsed(matching_excel, decision="Keep", notes="fine"),
		_current(reviewed_on="2026-10-22"),
	)
	assert (
		done_with_note["category"] == "change"
		and done_with_note["save_note"]
		and not done_with_note["action"]
	)
	assert _classify(matching_excel, _parsed(matching_excel))["category"] == "blank"


def test_note_already_saved_reads_through_the_comment_markup():
	matching_excel, _ = _excel()
	stored = "<p><b>Note from the Excel import</b> (Vendors row 2):</p><p>Lisa says &amp; Brian agrees</p>"
	assert matching_excel.note_already_saved("Lisa says & Brian agrees", [stored])
	assert not matching_excel.note_already_saved("Something else", [stored])
	assert not matching_excel.note_already_saved("", [stored])


def test_two_rows_linking_one_record_both_apply_with_a_warning():
	"""Nik, 2026-10-22: QBO duplicates are common; warn, still apply."""
	matching_excel, _ = _excel()
	rows = [
		_parsed(matching_excel, row_number=2, decision="Link"),
		_parsed(matching_excel, row_number=3, decision="Link"),
		_parsed(matching_excel, row_number=4, decision="Link", link_to="Other"),
	]
	results = [dict(category="change", action="Link", target_doctype="Supplier", warnings=[]) for _ in rows]
	matching_excel.shared_target_warnings(rows, results)
	assert results[0]["warnings"] == ["Supplier ABC Supply Co is also the Link target of Vendors row 3."]
	assert results[1]["warnings"] == ["Supplier ABC Supply Co is also the Link target of Vendors row 2."]
	assert results[2]["warnings"] == []
	assert all(result["category"] == "change" for result in results)


# ---------------------------------------------------------------------------
# Preview and apply against an in-memory ledger
# ---------------------------------------------------------------------------


class Ledger:
	"""Mapping rows, Link targets and Comments, mutated the way the real code paths would."""

	def __init__(self):
		self.mappings = {
			"MAP-1": _current(),
			"MAP-2": _current(name="MAP-2", qbo_id="57", erpnext_name="XYZ Corp-1"),
			"MAP-3": _current(
				name="MAP-3", qbo_id="58", erpnext_name="Old Co", match_status="Pending Review"
			),
		}
		self.suppliers = {"ABC Supply Co", "XYZ Corp", "ABC Supply-1", "XYZ Corp-1", "Old Co"}
		self.comments = {}
		self.calls = []


def _wire(monkeypatch, matching_excel, frappe, ledger):
	cache = {}
	monkeypatch.setattr(frappe, "session", types.SimpleNamespace(user="nik@example.com"), raising=False)
	monkeypatch.setattr(
		frappe,
		"cache",
		types.SimpleNamespace(
			set_value=lambda key, value, expires_in_sec=None: cache.__setitem__(key, value),
			get_value=lambda key: cache.get(key),
		),
		raising=False,
	)
	monkeypatch.setattr(frappe.db, "commit", lambda: None, raising=False)
	monkeypatch.setattr(
		matching_excel,
		"_current_rows",
		lambda names: {n: dict(ledger.mappings[n]) for n in names if n in ledger.mappings},
	)
	monkeypatch.setattr(
		matching_excel, "_saved_comments", lambda names: {n: list(ledger.comments.get(n, [])) for n in names}
	)
	monkeypatch.setattr(
		matching_excel.matching, "latest_payloads", lambda pairs: {(t, str(i)): {"Id": i} for t, i in pairs}
	)
	monkeypatch.setattr(matching_excel.matching, "expected_doctype", lambda entity_type, payload: "Supplier")
	monkeypatch.setattr(
		matching_excel.matching,
		"_record_titles",
		lambda refs: {(d, n): (n if n in ledger.suppliers else None) for d, n in refs},
	)

	def find(entity_type, qbo_id):
		return next(
			m
			for m in ledger.mappings.values()
			if m["qbo_entity_type"] == entity_type and m["qbo_id"] == str(qbo_id)
		)

	def decide(entity_type, qbo_id, erpnext_name, *, fill_blanks=False, merge_duplicate=True, user=None):
		ledger.calls.append(("decide", qbo_id, erpnext_name, fill_blanks, merge_duplicate))
		if erpnext_name == "Boom":
			raise Exception("<b>Supplier</b> Boom refused")
		mapping = find(entity_type, qbo_id)
		mapping.update(
			erpnext_name=erpnext_name,
			match_status="Manual Matched",
			reviewed_on="2026-10-22 12:00",
			modified="2026-10-22 12:00",
		)
		return {
			"mapping": mapping["name"],
			"linked": {"doctype": "Supplier", "name": erpnext_name},
			"merge": {"status": "merged", "from": "ABC Supply-1"},
		}

	def confirm(entity_type, qbo_id, *, user=None):
		ledger.calls.append(("confirm", qbo_id))
		find(entity_type, qbo_id)["reviewed_on"] = "2026-10-22 12:00"  # update_modified=False
		return {}

	sync_module = types.ModuleType("erpnext_enhancements.quickbooks_online.core.sync")

	def sync_entity(entity_type, qbo_id, source="Manual"):
		ledger.calls.append(("sync", qbo_id))
		find(entity_type, qbo_id).update(match_status="Created", modified="2026-10-22 12:01")
		return {"result": {"action": "created"}}

	sync_module.sync_entity = sync_entity
	monkeypatch.setitem(
		__import__("sys").modules, "erpnext_enhancements.quickbooks_online.core.sync", sync_module
	)
	monkeypatch.setattr(matching_excel.matching, "decide", decide)
	monkeypatch.setattr(matching_excel.matching, "confirm", confirm)
	monkeypatch.setattr(
		matching_excel,
		"_save_note",
		lambda mapping, row, user: ledger.comments.setdefault(mapping, []).append(f"<p>{row['notes']}</p>"),
	)
	return cache


def _meg_file(matching_excel, ledger_rows=None):
	masters = [
		_master_row(),
		_master_row(
			mapping="MAP-2",
			qbo_id="57",
			link={"doctype": "Supplier", "name": "XYZ Corp-1", "title": "XYZ", "exists": True},
		),
		_master_row(
			mapping="MAP-3",
			qbo_id="58",
			match_status="Pending Review",
			link={"doctype": "Supplier", "name": "Old Co", "title": "Old Co", "exists": True},
		),
	]
	content = _workbook(matching_excel, masters=masters)
	return _fill(
		content,
		{
			"Vendors": {
				2: {"Decision": "Link", "Fill blank fields from QBO": "Yes", "Notes": "Same company"},
				3: {"Decision": "Keep"},
				4: {"Decision": "Retry", "Notes": "Fixed the industry"},
			}
		},
	)


def test_apply_is_idempotent_the_same_file_twice_changes_nothing(monkeypatch):
	matching_excel, frappe = _excel()
	ledger = Ledger()
	_wire(monkeypatch, matching_excel, frappe, ledger)
	content = _meg_file(matching_excel)
	monkeypatch.setattr(matching_excel, "_read_upload", lambda file_name: content)

	first = matching_excel.preview("FILE-1")
	assert (
		first["summary"]["change"] == 3
		and first["summary"]["stale"] == 0
		and first["summary"]["invalid"] == 0
	)
	assert ledger.calls == [], "the preview writes nothing"
	changes = [row["index"] for row in first["rows"] if row["category"] == "change"]
	results = matching_excel.apply(first["token"], changes)
	assert [r["ok"] for r in results] == [True, True, True]
	assert ledger.calls == [("decide", "56", "ABC Supply Co", True, True), ("confirm", "57"), ("sync", "58")]
	assert "Merged the import's copy" in results[0]["message"] and "Note saved" in results[0]["message"]
	assert ledger.comments == {"MAP-1": ["<p>Same company</p>"], "MAP-3": ["<p>Fixed the industry</p>"]}

	before = {name: dict(row) for name, row in ledger.mappings.items()}
	second = matching_excel.preview("FILE-1")
	assert second["summary"]["change"] == 0
	assert second["summary"]["unchanged"] == 3, [row["message"] for row in second["rows"]]
	again = matching_excel.apply(second["token"], [row["index"] for row in second["rows"]])
	assert all(r["skipped"] and not r["ok"] for r in again)
	assert len(ledger.calls) == 3, "nothing ran the second time"
	assert ledger.mappings == before
	assert sum(len(c) for c in ledger.comments.values()) == 2, "no note was saved twice"


def test_apply_rechecks_each_row_and_skips_one_that_moved_after_the_preview(monkeypatch):
	matching_excel, frappe = _excel()
	ledger = Ledger()
	_wire(monkeypatch, matching_excel, frappe, ledger)
	content = _meg_file(matching_excel)
	monkeypatch.setattr(matching_excel, "_read_upload", lambda file_name: content)
	preview = matching_excel.preview("FILE-1")
	ledger.mappings["MAP-2"].update(
		erpnext_name="Lisa Linked It", match_status="Manual Matched", reviewed_on="2026-10-22"
	)
	results = matching_excel.apply(preview["token"], [row["index"] for row in preview["rows"]])
	moved = next(r for r in results if r["qbo_id"] == "57")
	assert moved["skipped"] and moved["category"] == "stale"
	assert ("confirm", "57") not in ledger.calls
	assert [r["ok"] for r in results if r["qbo_id"] != "57"] == [True, True]


def test_one_failing_row_never_stops_the_rest_and_its_note_is_not_saved(monkeypatch):
	matching_excel, frappe = _excel()
	ledger = Ledger()
	ledger.suppliers.add("Boom")
	_wire(monkeypatch, matching_excel, frappe, ledger)
	content = _fill(_meg_file(matching_excel), {"Vendors": {2: {"Link to (ERPNext ID)": "Boom"}}})
	monkeypatch.setattr(matching_excel, "_read_upload", lambda file_name: content)
	rollbacks = []
	monkeypatch.setattr(frappe.db, "rollback", lambda save_point=None: rollbacks.append(save_point))
	preview = matching_excel.preview("FILE-1")
	results = matching_excel.apply(preview["token"], [row["index"] for row in preview["rows"]])
	assert [r["ok"] for r in results] == [False, True, True]
	assert results[0]["message"] == "Supplier Boom refused", "markup stripped, as on the page"
	assert "MAP-1" not in ledger.comments
	assert "qbo_match_excel_apply" in rollbacks


def test_apply_refuses_an_expired_preview(monkeypatch):
	matching_excel, frappe = _excel()
	_wire(monkeypatch, matching_excel, frappe, Ledger())
	with pytest.raises(Exception, match="expired"):
		matching_excel.apply("nope", [0])


def test_preview_lists_every_non_blank_row_with_its_reason(monkeypatch):
	matching_excel, frappe = _excel()
	ledger = Ledger()
	_wire(monkeypatch, matching_excel, frappe, ledger)
	content = _fill(
		_meg_file(matching_excel),
		{
			"Vendors": {
				3: {"Decision": "Link", "Link to (ERPNext ID)": "Nobody"},
				4: {"Decision": None, "Notes": None},
			}
		},
	)
	monkeypatch.setattr(matching_excel, "_read_upload", lambda file_name: content)
	preview = matching_excel.preview("FILE-1")
	assert preview["summary"] == {
		"change": 1,
		"unchanged": 0,
		"stale": 0,
		"invalid": 1,
		"blank": 1,
		"warnings": 0,
	}
	by_row = {row["row_number"]: row for row in preview["rows"]}
	assert set(by_row) == {2, 3}, "blank rows are counted, not listed"
	assert "There is no Supplier with the ID 'Nobody'" in by_row[3]["message"]
	assert by_row[2]["note"] == "Same company"
	# A CDC touch since the export is a warning on a row that still applies, never a refusal.
	ledger.mappings["MAP-1"]["modified"] = "2026-10-22 02:00:00"
	again = matching_excel.preview("FILE-1")
	first = next(row for row in again["rows"] if row["row_number"] == 2)
	assert first["category"] == "change"
	assert first["warnings"] == ["QuickBooks refreshed this record since the export."]


# ---------------------------------------------------------------------------
# Export job
# ---------------------------------------------------------------------------


def test_run_export_saves_a_private_file_and_reports_failure_without_raising(monkeypatch):
	matching_excel, frappe = _excel()
	cache = _wire(monkeypatch, matching_excel, frappe, Ledger())
	monkeypatch.setattr(
		matching_excel,
		"collect_export",
		lambda **kwargs: [
			(
				"Vendors",
				matching_excel.MASTER_COLUMNS,
				[matching_excel.master_sheet_row(_master_row())],
				matching_excel.MASTER_DECISIONS,
			)
		],
	)
	monkeypatch.setattr(matching_excel, "now_datetime", lambda: "2026-10-22 09:30:00")
	saved = {}

	class FakeFile:
		def __init__(self, data):
			saved.update(data)
			self.file_url = "/private/files/x.xlsx"
			self.file_name = data["file_name"]

		def insert(self, ignore_permissions=False):
			saved["ignore_permissions"] = ignore_permissions
			return self

	monkeypatch.setattr(frappe, "get_doc", lambda data: FakeFile(data), raising=False)
	monkeypatch.setattr(
		frappe, "log_error", lambda **kwargs: saved.setdefault("logged", kwargs), raising=False
	)
	matching_excel.run_export("t1", "nik@example.com", status="All")
	state = cache[matching_excel._job_key("nik@example.com", "t1")]
	assert state["status"] == "done" and state["file_url"] == "/private/files/x.xlsx"
	assert saved["is_private"] == 1 and saved["file_name"].endswith(".xlsx")
	assert openpyxl.load_workbook(io.BytesIO(saved["content"])).sheetnames[:2] == ["Instructions", "Vendors"]

	def boom(**kwargs):
		raise Exception("<b>query</b> timed out")

	monkeypatch.setattr(matching_excel, "collect_export", boom)
	matching_excel.run_export("t2", "nik@example.com")
	assert cache[matching_excel._job_key("nik@example.com", "t2")] == {
		"status": "failed",
		"error": "query timed out",
	}
	assert saved["logged"]["message"] == "query timed out"


def test_export_status_is_keyed_on_the_caller(monkeypatch):
	matching_excel, frappe = _excel()
	cache = _wire(monkeypatch, matching_excel, frappe, Ledger())
	cache[matching_excel._job_key("lisa@example.com", "t1")] = {
		"status": "done",
		"file_url": "/private/files/lisa.xlsx",
	}
	assert matching_excel.export_status("t1") == {"status": "unknown"}, "Nik cannot read Lisa's export"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_new_endpoints_are_gated_whitelisted_reexported_and_dialled_by_the_page():
	api = CORE_API.read_text(encoding="utf-8")
	tree = ast.parse(api)
	whitelisted = {
		node.name
		for node in tree.body
		if isinstance(node, ast.FunctionDef)
		and any("whitelist" in ast.unparse(decorator) for decorator in node.decorator_list)
	}
	reexport = MODULE_API.read_text(encoding="utf-8")
	js = PAGE_JS.read_text(encoding="utf-8")
	for endpoint in ENDPOINTS:
		assert endpoint in whitelisted, f"{endpoint} is not whitelisted"
		start = api.index(f"def {endpoint}(")
		assert (
			"_require_qbo_operator()" in api[start : start + 900]
		), f"{endpoint} is reachable by any signed-in user"
		assert endpoint in reexport, f"{endpoint} is not re-exported"
		assert f'"{endpoint}"' in js, f"the page never calls {endpoint}"


def test_the_page_offers_the_server_export_filters_and_apply_chunk():
	matching_excel, _ = _excel()
	js = PAGE_JS.read_text(encoding="utf-8")
	offered = re.search(r"const EXPORT_STATUSES = \[([^\]]+)\]", js).group(1)
	assert tuple(re.findall(r'"([^"]+)"', offered)) == matching_excel.EXPORT_STATUSES
	assert "Unmapped" not in matching_excel.EXPORT_STATUSES, "unmapped rows have no Row ID to come back by"
	assert "apply_chunk" in js and "FileUploader" in js
	assert "allow_toggle_private: false" in js and "make_attachments_public: false" in js


def test_ci_runs_this_suite_with_openpyxl():
	ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
	assert "python -m pytest erpnext_enhancements/tests/test_quickbooks_matching_excel.py" in ci
	assert "openpyxl~=3.1.5" in ci
