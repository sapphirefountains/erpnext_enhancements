"""Excel round trip for the QuickBooks Record Matching page (v1.721.0).

Nik, 2026-10-22: *"I need an excel export of the QuickBooks Record Matching screens so Meg
can work on them in Excel and later I can take her work and reimport it into ERPNext."*

**Export** writes one workbook: an Instructions sheet in plain words, one sheet per QBO
master type (Customers, Vendors, Items, ...) holding the same rows the Masters tab shows --
the QBO record, what it is linked to now, the status, why it parked, up to five suggested
ERPNext records -- and a Parked transactions sheet. Each row has yellow columns to fill in:
**Decision** (a dropdown of the page's own three actions, Link / Keep / Retry, blank = leave
it alone), **Link to** (a dropdown of that row's suggestions, any other ID may be typed),
**Fill blank fields from QBO** and **Notes**. Hidden columns carry the keys the import needs:
the mapping's Row ID, a Row version, and the mapping's ``modified``.

**Import** never writes on upload. ``preview`` reads the file and classifies every row
against the live database:

* ``change``    -- will do something (the decision, the note, or both);
* ``unchanged`` -- the row already says what the file asks for (a second import of the same
  file lands every row here, which is what makes the round trip idempotent);
* ``stale``     -- the row changed in ERPNext since the export, so the decision was made
  against facts that no longer hold, and it is skipped;
* ``invalid``   -- an unknown Row ID, keys that were edited, a decision the row cannot take,
  a Link target that does not exist or is the wrong type;
* ``blank``     -- nothing filled in.

``apply`` then runs only the ``change`` rows, in the chunks the page sends, through the
page's own code path -- ``matching.decide`` (Link, with its merge policy), ``matching.confirm``
(Keep) and ``sync.sync_entity`` (Retry) -- after classifying each row **again**, so a row
that went stale between the preview and the click is skipped, not applied.

Things this module is careful about:

* **The stale check is a fingerprint of what the decision depends on**, not ``modified``
  alone: the link (doctype, name), ``match_status``, ``conflict_status`` and ``reviewed_on``.
  ``modified`` moves on every CDC touch of the QBO record, and keying on it would throw away
  an afternoon of Meg's work because a customer's phone number changed in QuickBooks. A
  moved ``modified`` with an unchanged fingerprint is reported as a warning instead. Note
  that ``reviewed_on`` is in the fingerprint and ``_stamp_reviewed`` writes it with
  ``update_modified=False`` -- so it is precisely the change ``modified`` would have missed.
* **"Already done" is decided before "stale".** Applying a row changes its fingerprint, so a
  second import of the same file would otherwise report every row stale; it checks whether
  the row already holds the decision's outcome first, and reports it unchanged.
* **Two QBO records linked to one ERPNext record is a warning, not a refusal** (Nik,
  2026-10-22): QBO duplicates are the common case, and the merge policy folds each import
  copy into the chosen record exactly as two clicks on the page would.
* **Notes are saved as a Comment on the Sync Mapping row** (Nik, 2026-10-22), and a note is
  "already saved" when an existing comment on that row contains its text, so importing the
  same file twice adds no second comment.
* **The export runs as a background job** and the page polls for it. The Masters queue is
  ~2,700 rows on production, and every row costs the matcher, a similar-name query and a
  title lookup; done in one web request it would race the gateway timeout. The file is a
  private File owned by the person who asked for it.
* **Columns are found by their header text**, so Meg may sort, filter, reorder, add columns
  or add her own sheets. A sheet without a ``Row ID`` header is not a matching sheet and is
  ignored.
* **Built with openpyxl**, which ships with Frappe (``openpyxl~=3.1.5`` on v16), for the
  data validation the dropdowns need -- ``frappe.utils.xlsxutils.make_xlsx`` writes one plain
  sheet through xlsxwriter (``workforce/payroll_export.py`` made the same call).
"""

from __future__ import annotations

import datetime
import hashlib
import html
import io
import re
import secrets
import time
import zipfile

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.quickbooks_online.core import matching
from erpnext_enhancements.quickbooks_online.core.constants import MASTER_ENTITIES, TRANSACTION_ENTITIES

FORMAT_VERSION = "1"
META_SHEET = "_export"
INSTRUCTIONS_SHEET = "Instructions"
TXN_SHEET = "Parked transactions"
SHEET_TITLES = {
	"Customer": "Customers",
	"Vendor": "Vendors",
	"Item": "Items",
	"Account": "Accounts",
	"TaxCode": "Tax codes",
	"Class": "Classes",
	"Term": "Terms",
	"PaymentMethod": "Payment methods",
}

LINK, KEEP, RETRY = "Link", "Keep", "Retry"
MASTER_DECISIONS = (LINK, KEEP, RETRY)
TXN_DECISIONS = (RETRY,)
YES = "Yes"
PENDING = "Pending Review"

CHANGE, UNCHANGED, STALE, INVALID, BLANK = "change", "unchanged", "stale", "invalid", "blank"
CATEGORIES = (CHANGE, UNCHANGED, STALE, INVALID, BLANK)

#: Export filters the dialog offers -- the page's own status filters, less *Unmapped
#: payloads*: those rows have no mapping row, so nothing in the file could find them again.
EXPORT_STATUSES = (
	"Needs decision",
	"All",
	"Pending Review",
	"Conflict",
	"Auto Matched",
	"Created",
	"Manual Matched",
)

SUGGESTION_COLUMNS = 5
#: Rows per ``master_queue`` / ``parked_transactions`` call while exporting.
EXPORT_PAGE = matching.MAX_PAGE_LENGTH
#: Rows per apply request the page sends. A Link can fold a duplicate away with rename_doc,
#: so a chunk stays small enough for the gateway; a Retry is a round trip to Intuit, and the
#: page sends each one alone, as its own Retry button does.
APPLY_CHUNK = 10
MAX_IMPORT_ROWS = 20000
MAX_IMPORT_BYTES = 15 * 1024 * 1024
JOB_TTL = 6 * 3600
EXPORT_TIMEOUT = 1500
PLAN_TTL = 6 * 3600
#: A cheap zip-bomb guard: the workbook's uncompressed size, checked before openpyxl reads it.
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
#: Every export and every uploaded import lives in this folder, and the daily
#: ``delete_old_files`` job removes what is older than ``FILE_MAX_AGE_DAYS``: the files hold
#: customer and vendor names, emails and links, and nothing else ever cleans an unattached File.
FILE_FOLDER_NAME = "QuickBooks Matching Excel"
FILE_FOLDER = f"Home/{FILE_FOLDER_NAME}"
FILE_MAX_AGE_DAYS = 7
#: The mark on every File this feature made or accepted, and the ONLY thing the clean-up
#: deletes by. The folder alone is not enough: anything can be moved into a folder. Stored in
#: ``attached_to_field``, which Frappe reads only together with ``attached_to_doctype`` -- on an
#: unattached File nothing else ever sets it, so it cannot collide with a real attachment.
FILE_TAG = "qbo_matching_excel"
#: A string cell starting with one of these is a formula to Excel or to whatever the sheet
#: is pasted into; see ``_put``.
FORMULA_LEADS = ("=", "+", "-", "@")

#: (key, header, width, hidden, editable). The import finds columns by header text.
MASTER_COLUMNS = (
	("qbo_type", "QBO type", 13, False, False),
	("qbo_id", "QBO ID", 10, False, False),
	("qbo_name", "QuickBooks name", 34, False, False),
	("qbo_detail", "QuickBooks details", 34, False, False),
	("status", "Status now", 20, False, False),
	("linked_title", "Linked to now", 30, False, False),
	("linked_id", "Linked to now (ERPNext ID)", 26, False, False),
	("target_doctype", "ERPNext type", 15, False, False),
	("issues", "Why it is parked", 36, False, False),
	("reviewed_by", "Reviewed by", 20, False, False),
	*((f"suggestion_{n}", f"Suggestion {n}", 32, False, False) for n in range(1, SUGGESTION_COLUMNS + 1)),
	("decision", "Decision", 12, False, True),
	("link_to", "Link to (ERPNext ID)", 28, False, True),
	("fill_blanks", "Fill blank fields from QBO", 14, False, True),
	("notes", "Notes", 40, False, True),
	("row_id", "Row ID", 14, True, False),
	("row_version", "Row version", 14, True, False),
	("modified", "Modified", 22, True, False),
	("linked_doctype", "Linked to now (type)", 16, True, False),
	("exported_status", "Status at export", 16, True, False),
	*(
		(f"suggestion_id_{n}", f"Suggestion {n} ID", 20, True, False)
		for n in range(1, SUGGESTION_COLUMNS + 1)
	),
)
TXN_COLUMNS = (
	("qbo_type", "QBO type", 16, False, False),
	("qbo_id", "QBO ID", 10, False, False),
	("doc_number", "Doc number", 12, False, False),
	("txn_date", "Date", 12, False, False),
	("party", "Customer / vendor", 30, False, False),
	("total", "Total", 12, False, False),
	("draft", "ERPNext draft", 28, False, False),
	("issues", "Why it is parked", 48, False, False),
	("decision", "Decision", 12, False, True),
	("notes", "Notes", 40, False, True),
	("row_id", "Row ID", 14, True, False),
	("row_version", "Row version", 14, True, False),
	("modified", "Modified", 22, True, False),
	("linked_doctype", "ERPNext draft (type)", 16, True, False),
	("linked_id", "ERPNext draft (ID)", 20, True, False),
	("exported_status", "Status at export", 16, True, False),
)
HEADER_TO_KEY = {header: key for key, header, *_ in (*MASTER_COLUMNS, *TXN_COLUMNS)}

_HTML_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Pure helpers (bench-free; covered by tests/test_quickbooks_matching_excel.py)
# ---------------------------------------------------------------------------


def row_version(link_doctype, link_name, match_status, conflict_status, reviewed_on) -> str:
	"""Fingerprint of the facts a decision on this row depends on -- see the module
	docstring for why ``modified`` is not one of them. An unlinked row's doctype does not
	count: the page reports no link at all, while the column may still hold the type."""
	if not link_name:
		link_doctype = ""
	raw = "|".join(
		str(value or "") for value in (link_doctype, link_name, match_status, conflict_status, reviewed_on)
	)
	return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def text(value) -> str:
	"""A cell as the import reads it: stripped text, an integral number without ``.0`` (an
	ID typed as 1001 comes back from Excel as the float 1001.0), a date as ISO."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return YES if value else ""
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	if isinstance(value, (datetime.datetime, datetime.date)):
		return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
	return str(value).strip()


def normalise_decision(value, allowed) -> tuple[str, bool]:
	"""``(decision, recognised)``: case and spaces forgiven, anything else kept verbatim
	so the preview can say what it did not understand."""
	raw = text(value)
	if not raw:
		return "", True
	for option in allowed:
		if raw.casefold() == option.casefold():
			return option, True
	return raw, False


def truthy(value) -> bool:
	return text(value).casefold() in {"yes", "y", "true", "1", "x"}


def plain(content) -> str:
	"""A Comment's HTML as comparable text: tags dropped, entities decoded, spaces folded."""
	return _SPACE.sub(" ", html.unescape(_HTML_TAG.sub(" ", str(content or "")))).strip()


def note_key(note) -> str:
	"""The marker an import's own comment carries for a note: a hash of its exact text."""
	return hashlib.sha1(str(note or "").strip().encode("utf-8")).hexdigest()[:12]


def note_marker(note) -> str:
	return f"[Excel note {note_key(note)}]"


def note_already_saved(note, comments) -> bool:
	"""True only when one of this import's own comments carries this exact note's marker. A
	substring test was wrong both ways: "ok" was "already saved" inside any comment that said
	"ok", and a reworded note matched nothing."""
	if not str(note or "").strip():
		return False
	marker = note_marker(note)
	return any(marker in plain(comment) for comment in comments or ())


def suggestion_label(suggestion) -> str:
	title = suggestion.get("title") or suggestion.get("name") or ""
	label = title if title == suggestion.get("name") else f"{title} [{suggestion.get('name')}]"
	bits = [f"{cint(suggestion.get('score'))}%"]
	if suggestion.get("rule"):
		bits.append(str(suggestion["rule"]))
	return f"{label} ({', '.join(bits)})"


def master_sheet_row(row) -> dict:
	"""One Masters-tab row (``matching.master_queue``) as a sheet row keyed by column key."""
	qbo = row.get("qbo") or {}
	link = row.get("link") or {}
	details = list(qbo.get("detail") or [])
	if qbo.get("job"):
		details.append("QBO job")
	if qbo.get("inactive"):
		details.append("inactive in QBO")
	if not row.get("has_payload"):
		details.append("no payload stored")
	status = row.get("match_status") or ""
	if row.get("conflict_status") == "Conflict":
		status = f"{status} (conflict)" if status else "Conflict"
	linked_title = link.get("title") or ""
	if link and not link.get("exists"):
		linked_title = f"{link.get('name')} (no longer exists)"
	suggestions = list(row.get("suggestions") or [])[:SUGGESTION_COLUMNS]
	out = {
		"qbo_type": row.get("entity_type"),
		"qbo_id": str(row.get("qbo_id") or ""),
		"qbo_name": qbo.get("name") or str(row.get("qbo_id") or ""),
		"qbo_detail": " · ".join(details),
		"status": status,
		"linked_title": linked_title,
		"linked_id": link.get("name") or "",
		"target_doctype": row.get("expected_doctype") or "",
		"issues": "\n".join(row.get("issues") or []),
		"reviewed_by": row.get("reviewed_by") or "",
		"decision": "",
		# The page's picker opens on the best suggestion; so does this column. Nothing
		# happens unless Decision says Link.
		"link_to": suggestions[0]["name"] if suggestions else "",
		"fill_blanks": "",
		"notes": "",
		"row_id": row.get("mapping") or "",
		"row_version": row_version(
			link.get("doctype"),
			link.get("name"),
			row.get("match_status"),
			row.get("conflict_status"),
			row.get("reviewed_on"),
		),
		"modified": row.get("modified") or "",
		"linked_doctype": link.get("doctype") or "",
		"exported_status": row.get("match_status") or "",
	}
	for n in range(1, SUGGESTION_COLUMNS + 1):
		suggestion = suggestions[n - 1] if n <= len(suggestions) else None
		out[f"suggestion_{n}"] = suggestion_label(suggestion) if suggestion else ""
		out[f"suggestion_id_{n}"] = suggestion["name"] if suggestion else ""
	return out


def txn_sheet_row(row) -> dict:
	"""One Parked-transactions row (``matching.parked_transactions``) as a sheet row."""
	qbo = row.get("qbo") or {}
	draft = ""
	if row.get("name"):
		draft = f"{row.get('doctype') or ''} {row['name']}".strip()
		if not row.get("exists"):
			draft += " (gone)"
	total = qbo.get("total")
	return {
		"qbo_type": row.get("entity_type"),
		"qbo_id": str(row.get("qbo_id") or ""),
		"doc_number": qbo.get("doc_number") or "",
		"txn_date": qbo.get("txn_date") or "",
		"party": qbo.get("party") or "",
		"total": total if isinstance(total, (int, float)) else text(total),
		"draft": draft,
		"issues": "\n".join(row.get("issues") or []) or (row.get("match_rule") or ""),
		"decision": "",
		"notes": "",
		"row_id": row.get("mapping") or "",
		# A parked transaction's version is its draft and its status only: the queue reads
		# neither conflict_status nor reviewed_on for these rows (see _current_version).
		"row_version": row_version(row.get("doctype"), row.get("name"), PENDING, None, None),
		"modified": row.get("modified") or "",
		"linked_doctype": row.get("doctype") or "",
		"linked_id": row.get("name") or "",
		"exported_status": PENDING,
	}


def instructions(meta) -> list:
	"""The Instructions sheet, one paragraph per row, for someone who has never seen the page."""
	counts = meta.get("counts") or {}
	lines = [
		"QuickBooks record matching -- how to use this file",
		"",
		"Each tab is one kind of QuickBooks record (Customers, Vendors, Items and so on). Each row is one "
		"QuickBooks record and the ERPNext record it is linked to now. The last tab lists QuickBooks "
		"transactions that did not import.",
		"",
		"Fill in the yellow columns only. Everything else is there to help you decide. Some columns are "
		"hidden: ERPNext uses them to find the row again, so please leave them alone.",
		"",
		"Decision -- pick one from the dropdown, or leave it blank:",
		"   Keep  -- the current link is right. ERPNext marks the row as reviewed. If the import created "
		"the ERPNext record, Keep means: keep that new record.",
		"   Link  -- the QuickBooks record belongs to a different ERPNext record. Put that record's ERPNext "
		"ID in 'Link to'. The dropdown there lists the suggestions on the row (the same ones as in the "
		"Suggestion columns), and you can type any other ID. If the import created its own copy of the "
		"record, ERPNext merges that copy into the one you chose.",
		"   Retry -- only for rows whose status is 'Pending Review': ERPNext fetches the record from "
		"QuickBooks again and tries the import again. Fix the reason in 'Why it is parked' first.",
		"   (blank) -- leave the row as it is.",
		"",
		"Link to -- the ERPNext ID to link to. Only used when Decision is Link. It starts out filled with "
		"the best suggestion; change it if that is not the right one.",
		"Fill blank fields from QBO -- 'Yes' copies the QuickBooks details into the ERPNext record's empty "
		"fields when linking. Only used with Link.",
		"Notes -- anything you want Nik to see. Notes are saved on the row in ERPNext when the file is "
		"imported, with or without a decision.",
		"",
		"Suggestions: the percentage is how closely the names match (100% = the same name).",
		"",
		"Each tab is protected so that only the yellow columns can be changed: the grey and hidden "
		"columns are how ERPNext finds the row again. Filtering and column widths still work; sorting is "
		"turned off because it would move the protected cells. You can add your own tabs.",
		"",
		"When you are done, save the file as an Excel workbook (.xlsx, not .csv) and send it to Nik. "
		"Nothing changes in ERPNext until Nik imports it, checks the preview, and clicks Apply. If a row "
		"changes in ERPNext in the meantime, the import skips that row and says so.",
		"",
		f"Exported {meta.get('exported_on') or ''} by {meta.get('exported_by') or ''}.",
		f"Rows: {meta.get('filter') or ''}.",
	]
	for sheet, count in counts.items():
		lines.append(f"   {sheet}: {count} rows")
	return lines


def _put(sheet, row, column, value):
	"""Write one cell. Every string is stored as text: openpyxl writes a string that starts
	with ``=`` as a FORMULA, so a QuickBooks name like ``=HYPERLINK(...)`` would run in Meg's
	Excel. A string with a formula lead also gets Excel's quote prefix, so it stays text when
	the cell is edited or the sheet is pasted elsewhere."""
	cell = sheet.cell(row=row, column=column)
	if value is None or value == "":
		return cell
	cell.value = value
	if isinstance(value, str):
		cell.data_type = "s"
		if value.startswith(FORMULA_LEADS):
			cell.quotePrefix = True
	return cell


def build_workbook(sheets, meta) -> bytes:
	"""``sheets`` is ``[(title, columns, rows, decisions), ...]``; returns the .xlsx bytes."""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill, Protection
	from openpyxl.utils import get_column_letter
	from openpyxl.worksheet.datavalidation import DataValidation

	workbook = Workbook()
	guide = workbook.active
	guide.title = INSTRUCTIONS_SHEET
	guide.column_dimensions["A"].width = 120
	for index, line in enumerate(instructions(meta), start=1):
		cell = _put(guide, index, 1, line)
		cell.alignment = Alignment(wrap_text=True, vertical="top")
	guide["A1"].font = Font(bold=True, size=14)

	bold = Font(bold=True)
	head_fill = PatternFill("solid", fgColor="D9D9D9")
	edit_head_fill = PatternFill("solid", fgColor="FFD966")
	edit_fill = PatternFill("solid", fgColor="FFF2CC")
	wrap = Alignment(wrap_text=True, vertical="top")
	top = Alignment(vertical="top")
	unlocked = Protection(locked=False)

	for title, columns, rows, decisions in sheets:
		sheet = workbook.create_sheet(title=title[:31])
		letters = {}
		for position, (key, header, width, hidden, editable) in enumerate(columns, start=1):
			letter = get_column_letter(position)
			letters[key] = letter
			cell = _put(sheet, 1, position, header)
			cell.font = bold
			cell.fill = edit_head_fill if editable else head_fill
			cell.alignment = wrap
			dimension = sheet.column_dimensions[letter]
			dimension.width = width
			dimension.hidden = hidden
		for row_index, row in enumerate(rows, start=2):
			for position, (key, _header, _width, _hidden, editable) in enumerate(columns, start=1):
				cell = _put(sheet, row_index, position, row.get(key))
				cell.alignment = wrap if key in ("issues", "qbo_detail", "notes") else top
				if editable:
					cell.fill = edit_fill
					cell.protection = unlocked
		last = max(len(rows) + 1, 2)
		sheet.freeze_panes = "D2"
		sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{last}"
		# Only the yellow cells can change. No password: this keeps a slip from breaking a
		# row, it is not a security boundary -- the import's Row ID / QBO type / QBO ID
		# cross-check is the backstop. Filtering and column widths stay allowed (False =
		# not protected); sorting stays off, since it would have to move locked cells.
		sheet.protection.sheet = True
		sheet.protection.autoFilter = False
		sheet.protection.formatColumns = False

		pick = DataValidation(
			type="list",
			formula1='"' + ",".join(decisions) + '"',
			allow_blank=True,
			showErrorMessage=True,
			errorTitle="Decision",
			error="Pick " + ", ".join(decisions) + ", or leave it blank.",
		)
		sheet.add_data_validation(pick)
		pick.add(f"{letters['decision']}2:{letters['decision']}{last}")
		if "link_to" in letters:
			# A list per row, from that row's own hidden suggestion IDs: the formula is
			# relative, so Excel shifts it down with each row. Information style, because
			# any other ERPNext ID is allowed too.
			first = letters["suggestion_id_1"]
			final = letters[f"suggestion_id_{SUGGESTION_COLUMNS}"]
			targets = DataValidation(
				type="list",
				formula1=f"${first}2:${final}2",
				allow_blank=True,
				showErrorMessage=False,
				errorStyle="information",
			)
			sheet.add_data_validation(targets)
			targets.add(f"{letters['link_to']}2:{letters['link_to']}{last}")
			fill = DataValidation(type="list", formula1=f'"{YES}"', allow_blank=True)
			sheet.add_data_validation(fill)
			fill.add(f"{letters['fill_blanks']}2:{letters['fill_blanks']}{last}")

	info = workbook.create_sheet(title=META_SHEET)
	info.sheet_state = "hidden"
	for index, (key, value) in enumerate(
		(
			("format_version", FORMAT_VERSION),
			("exported_on", meta.get("exported_on")),
			("exported_by", meta.get("exported_by")),
			("filter", meta.get("filter")),
		),
		start=1,
	):
		_put(info, index, 1, key)
		_put(info, index, 2, text(value))
	info.protection.sheet = True

	out = io.BytesIO()
	workbook.save(out)
	return out.getvalue()


def parse_workbook(content) -> dict:
	"""``{"rows": [...], "errors": [...]}`` from an export Meg has worked on. Pure: no
	database. Each row carries the sheet, the row number Excel shows, its kind (``master``
	or ``txn``) and the cells the import reads."""
	from openpyxl import load_workbook

	not_workbook = {
		"rows": [],
		"errors": ["This is not an Excel workbook (.xlsx). Save it as .xlsx and try again."],
	}
	try:
		expanded = sum(member.file_size for member in zipfile.ZipFile(io.BytesIO(content)).infolist())
	except (zipfile.BadZipFile, ValueError, TypeError):
		return not_workbook
	if expanded > MAX_EXPANDED_BYTES:
		return {"rows": [], "errors": ["That file is far too large to be a Record Matching export."]}
	try:
		workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	except Exception:
		return {
			"rows": [],
			"errors": ["This is not an Excel workbook (.xlsx). Save it as .xlsx and try again."],
		}
	if META_SHEET not in workbook.sheetnames:
		return {
			"rows": [],
			"errors": [
				"This file is not a Record Matching export. Export from the page and work in that file."
			],
		}
	not_export = {
		"rows": [],
		"errors": [
			"This file was not made by the Record Matching export. Export from the page and work in that file."
		],
	}
	meta = {}
	marker = workbook[META_SHEET]
	marker.reset_dimensions()
	try:
		for values in marker.iter_rows(values_only=True):
			values = tuple(values or ())
			if values and values[0]:
				meta[text(values[0])] = text(values[1]) if len(values) > 1 else ""
	except Exception:
		return not_export
	if not meta.get("format_version"):
		return not_export
	if meta.get("format_version") != FORMAT_VERSION:
		return {"rows": [], "errors": ["This export is from a different version of the page. Export again."]}

	rows, errors = [], []
	found = False
	for sheet in workbook.worksheets:
		if sheet.title in (META_SHEET, INSTRUCTIONS_SHEET):
			continue
		# Read-only mode trusts the sheet's stored <dimension>, and a file that another tool
		# saved with a wrong one would read as empty -- zero rows and no error.
		sheet.reset_dimensions()
		cells = sheet.iter_rows(values_only=True)
		header = next(cells, None) or ()
		columns = {}
		for position, value in enumerate(header):
			key = HEADER_TO_KEY.get(text(value))
			if key and key not in columns:
				columns[key] = position
		if "row_id" not in columns:
			continue  # one of Meg's own sheets
		found = True
		missing = [key for key in ("qbo_type", "qbo_id", "decision", "row_version") if key not in columns]
		if missing:
			errors.append(f"Sheet '{sheet.title}' is missing columns it needs; it was skipped.")
			continue
		for number, values in enumerate(cells, start=2):

			def cell(key, values=values, columns=columns):
				position = columns.get(key)
				return values[position] if position is not None and position < len(values) else None

			if not any(text(value) for value in values or ()):
				continue
			if len(rows) >= MAX_IMPORT_ROWS:
				errors.append(f"The file has more than {MAX_IMPORT_ROWS} rows; the rest were not read.")
				return {"rows": rows, "errors": errors, "meta": meta}
			# The row's kind comes from its QBO type, never from which headers the sheet has:
			# a "Link to" column pasted onto the Parked sheet must not make an Invoice linkable.
			qbo_type = text(cell("qbo_type"))
			kind = "master" if qbo_type in MASTER_ENTITIES else "txn"
			decision, recognised = normalise_decision(
				cell("decision"), MASTER_DECISIONS if kind == "master" else TXN_DECISIONS
			)
			rows.append(
				{
					"sheet": sheet.title,
					"row_number": number,
					"kind": kind,
					"row_id": text(cell("row_id")),
					"qbo_type": qbo_type,
					"qbo_id": text(cell("qbo_id")),
					"qbo_name": text(cell("qbo_name")) or text(cell("doc_number")),
					"row_version": text(cell("row_version")),
					"modified": text(cell("modified")),
					"linked_doctype": text(cell("linked_doctype")),
					"linked_id": text(cell("linked_id")),
					"exported_status": text(cell("exported_status")),
					"decision": decision,
					"decision_ok": recognised,
					"link_to": text(cell("link_to")),
					"fill_blanks": truthy(cell("fill_blanks")),
					"notes": text(cell("notes")),
				}
			)
	if not found:
		errors.append(
			"No Record Matching sheet was found in this file (no sheet has the export's Row ID column)."
		)
	return {"rows": rows, "errors": errors, "meta": meta}


def _current_version(current, kind) -> str:
	if kind != "master":
		return row_version(
			current.get("erpnext_doctype"),
			current.get("erpnext_name"),
			current.get("match_status"),
			None,
			None,
		)
	return row_version(
		current.get("erpnext_doctype"),
		current.get("erpnext_name"),
		current.get("match_status"),
		current.get("conflict_status"),
		str(current["reviewed_on"]) if current.get("reviewed_on") else None,
	)


def _describe_link(current) -> str:
	if not current.get("erpnext_name"):
		return "not linked"
	return f"{current.get('erpnext_doctype')} {current.get('erpnext_name')}"


def classify_row(row, current, *, target_doctype=None, target_exists=None, note_saved=False) -> dict:
	"""What importing ``row`` would do, given ``current`` -- the mapping row as it is now
	(a dict, or None when the Row ID matches nothing). ``target_doctype`` is where the QBO
	record belongs (``matching.expected_doctype``) and ``target_exists`` whether the Link
	target is a record of it. Pure.

	Returns ``{category, action, message, warnings}``; ``action`` is the decision to run
	(or ``""``) and ``save_note`` whether the note still has to be written."""
	decision = row.get("decision") or ""
	note = row.get("notes") or ""
	result = {"category": INVALID, "action": "", "save_note": False, "message": "", "warnings": []}
	allowed = MASTER_DECISIONS if row.get("kind") == "master" else TXN_DECISIONS

	if not row.get("row_id"):
		result["message"] = "The hidden Row ID is empty, so this row cannot be matched to ERPNext."
		return result
	if current is None:
		result["message"] = "No QuickBooks mapping has this Row ID any more."
		return result
	if current.get("qbo_entity_type") != row.get("qbo_type") or str(current.get("qbo_id")) != row.get(
		"qbo_id"
	):
		result["message"] = (
			f"The Row ID belongs to {current.get('qbo_entity_type')} #{current.get('qbo_id')}, not "
			f"{row.get('qbo_type')} #{row.get('qbo_id')}. Were the QBO type or QBO ID cells edited?"
		)
		return result
	if cint(current.get("deleted")):
		result["message"] = "This record was deleted in QuickBooks."
		return result
	if decision in (LINK, KEEP) and row.get("qbo_type") not in MASTER_ENTITIES:
		result["message"] = (
			f"A {row.get('qbo_type')} can only be retried; Link and Keep are for master records."
		)
		return result
	if not row.get("decision_ok", True) or (decision and decision not in allowed):
		result["message"] = f"'{decision}' is not a decision. Use {', '.join(allowed)}, or leave it blank."
		return result

	pending_note = bool(note) and not note_saved
	if not decision:
		if pending_note:
			result.update(category=CHANGE, save_note=True, message="Saves the note.")
		elif note:
			result.update(category=UNCHANGED, message="The note is already saved.")
		else:
			result.update(category=BLANK, message="Nothing filled in.")
		return result

	status = current.get("match_status") or ""
	reviewed = bool(current.get("reviewed_on"))
	if decision == RETRY and row.get("exported_status") != PENDING:
		result["message"] = "Only a row in Pending Review can be retried."
		return result
	if decision == LINK:
		done = (
			current.get("erpnext_name") == row.get("link_to")
			and (not target_doctype or current.get("erpnext_doctype") == target_doctype)
			and status == "Manual Matched"
			and reviewed
		)
	elif decision == KEEP:
		done = reviewed and (current.get("erpnext_name") or "") == (row.get("linked_id") or "")
	else:
		done = status != PENDING

	if done:
		if pending_note:
			result.update(
				category=CHANGE, save_note=True, message=f"{decision} is already done; saves the note."
			)
		else:
			result.update(category=UNCHANGED, message=f"Already done: {_already(decision, current)}.")
		return result

	if row.get("row_version") != _current_version(current, row.get("kind")):
		result.update(
			category=STALE,
			message=(
				f"Changed in ERPNext since the export: now {_describe_link(current)}, {status or 'no status'}"
				f"{', reviewed' if reviewed else ''}. Decide it on the page, or export again."
			),
		)
		return result

	if decision == LINK:
		link_to = row.get("link_to") or ""
		if not link_to:
			result["message"] = "Decision is Link but 'Link to' is empty."
			return result
		if not target_doctype:
			result["message"] = f"{row.get('qbo_type')} has no ERPNext destination, so it cannot be linked."
			return result
		if not target_exists:
			result["message"] = f"There is no {target_doctype} with the ID '{link_to}'."
			return result
		if current.get("erpnext_name") == link_to and current.get("erpnext_doctype") == target_doctype:
			message = f"Confirms the link to {target_doctype} {link_to} and marks it decided."
		else:
			message = f"Links to {target_doctype} {link_to} (now {_describe_link(current)})."
			plan = matching.merge_plan(
				{
					"doctype": current.get("erpnext_doctype"),
					"name": current.get("erpnext_name"),
					"match_status": status,
				},
				target_doctype,
				link_to,
			)
			if plan.get("merge"):
				message += f" Merges the import's copy {plan['from']} into it."
		if row.get("fill_blanks"):
			message += " Fills its blank fields from QuickBooks."
		result.update(category=CHANGE, action=LINK, message=message)
	elif decision == KEEP:
		result.update(
			category=CHANGE, action=KEEP, message=f"Marks reviewed; stays {_describe_link(current)}."
		)
	else:
		result.update(category=CHANGE, action=RETRY, message="Re-syncs it from QuickBooks.")
	result["save_note"] = pending_note
	if pending_note:
		result["message"] += " Saves the note."
	return result


def _signature(row) -> tuple:
	decision = row.get("decision") or ""
	return (
		decision,
		row.get("link_to") if decision == LINK else "",
		bool(row.get("fill_blanks")) if decision == LINK else False,
		(row.get("notes") or "").strip(),
	)


def duplicate_rows(rows, results) -> dict:
	"""``{index: (category, message)}`` for rows whose Row ID appears more than once with
	something filled in -- a copied sheet, a pasted row. If every copy says the same, the first
	stands and the rest are reported as the same row. If they disagree, none is applied: the
	file says two different things about one record, and only Meg knows which she meant.
	Mutates ``results`` so the preview shows it; the caller keeps the map so apply honours it."""
	by_id: dict = {}
	for index, (row, result) in enumerate(zip(rows, results, strict=True)):
		if row.get("row_id") and result["category"] != BLANK:
			by_id.setdefault(row["row_id"], []).append(index)
	blocked = {}
	for indexes in by_id.values():
		if len(indexes) < 2:
			continue
		places = {i: f"{rows[i]['sheet']} row {rows[i]['row_number']}" for i in indexes}
		agree = len({_signature(rows[i]) for i in indexes}) == 1
		for i in indexes:
			others = ", ".join(places[j] for j in indexes if j != i)
			if agree and i == indexes[0]:
				continue
			if agree:
				blocked[i] = (
					UNCHANGED,
					f"The same record and decision as {places[indexes[0]]}; applied once, there.",
				)
			else:
				blocked[i] = (
					INVALID,
					f"Conflict: the same record is also on {others} with a different decision or note. "
					"Neither is applied; make them agree, or decide it on the page.",
				)
	for i, (category, message) in blocked.items():
		results[i].update(category=category, action="", save_note=False, message=message, held=True)
	return blocked


def _already(decision, current) -> str:
	if decision == RETRY:
		return f"no longer parked ({current.get('match_status') or 'no status'})"
	return f"{_describe_link(current)}, reviewed"


def shared_target_warnings(rows, results) -> None:
	"""Add a warning to every Link that shares its target with another row in the file
	(Nik, 2026-10-22: warn, still apply). Mutates ``results``."""
	claims: dict = {}
	for index, (row, result) in enumerate(zip(rows, results, strict=True)):
		if result.get("action") == LINK:
			key = (result.get("target_doctype"), row.get("link_to"))
			claims.setdefault(key, []).append(index)
	for (doctype, name), indexes in claims.items():
		if len(indexes) < 2:
			continue
		for index in indexes:
			others = [f"{rows[i]['sheet']} row {rows[i]['row_number']}" for i in indexes if i != index]
			results[index]["warnings"].append(
				f"{doctype} {name} is also the Link target of {', '.join(others)}."
			)


# ---------------------------------------------------------------------------
# Database reads for the import
# ---------------------------------------------------------------------------


def _current_rows(names) -> dict:
	if not names:
		return {}
	rows = frappe.db.sql(
		"select name, qbo_entity_type, qbo_id, erpnext_doctype, erpnext_name, match_status,"
		" conflict_status, reviewed_on, modified, deleted"
		" from `tabQuickBooks Sync Mapping` where name in %(names)s",
		{"names": tuple(sorted(names))},
		as_dict=True,
	)
	return {row.name: dict(row) for row in rows}


def _saved_comments(names) -> dict:
	if not names:
		return {}
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": matching.MAPPING_DOCTYPE,
			"reference_name": ["in", sorted(names)],
			"comment_type": "Comment",
		},
		fields=["reference_name", "content"],
		limit_page_length=0,
	)
	out: dict = {}
	for row in rows:
		out.setdefault(row.reference_name, []).append(row.content)
	return out


def classify(rows) -> list:
	"""``classify_row`` for every parsed row, with the database reads batched: one query for
	the mapping rows, one per entity type for the payloads that decide a Link's doctype,
	one per doctype for the Link targets, one for the saved notes."""
	names = {row["row_id"] for row in rows if row.get("row_id")}
	current = _current_rows(names)
	comments = _saved_comments({row["row_id"] for row in rows if row.get("notes") and row.get("row_id")})
	links = [row for row in rows if row.get("decision") == LINK and row.get("row_id") in current]
	payloads = matching.latest_payloads([(row["qbo_type"], row["qbo_id"]) for row in links])
	targets = {}
	for row in links:
		payload = payloads.get((row["qbo_type"], str(row["qbo_id"])))
		targets[id(row)] = matching.expected_doctype(row["qbo_type"], payload) if payload else None
	titles = matching._record_titles(
		[(targets[id(row)], row["link_to"]) for row in links if targets[id(row)] and row.get("link_to")]
	)

	results = []
	for row in rows:
		target = targets.get(id(row))
		result = classify_row(
			row,
			current.get(row.get("row_id")),
			target_doctype=target,
			target_exists=titles.get((target, row.get("link_to"))) is not None,
			note_saved=note_already_saved(row.get("notes"), comments.get(row.get("row_id"))),
		)
		result["target_doctype"] = target
		now = current.get(row.get("row_id")) or {}
		if (
			result["category"] == CHANGE
			and row.get("modified")
			and now.get("modified")
			and str(now["modified"]) != row["modified"]
		):
			result["warnings"].append("QuickBooks refreshed this record since the export.")
		if target and row.get("decision") == LINK and result["category"] == CHANGE:
			title = titles.get((target, row.get("link_to")))
			if title and title != row.get("link_to"):
				result["target_title"] = title
		results.append(result)
	duplicate_rows(rows, results)
	shared_target_warnings(rows, results)
	return results


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def ensure_folder() -> str:
	"""The File folder every export and upload is kept in; created on first use. Two exports
	starting together may race to create it, and the loser finds it there."""
	if not frappe.db.exists("File", FILE_FOLDER):
		try:
			frappe.get_doc(
				{"doctype": "File", "file_name": FILE_FOLDER_NAME, "is_folder": 1, "folder": "Home"}
			).insert(ignore_permissions=True)
		except Exception:
			if not frappe.db.exists("File", FILE_FOLDER):
				raise
	return FILE_FOLDER


def _track(file_doc) -> None:
	"""Put a File this feature made or accepted into its folder, with its tag."""
	if file_doc.folder != FILE_FOLDER or file_doc.get("attached_to_field") != FILE_TAG:
		file_doc.db_set({"folder": ensure_folder(), "attached_to_field": FILE_TAG}, update_modified=False)


def delete_old_files() -> int:
	"""Daily (hooks.py): delete exports and uploaded imports older than ``FILE_MAX_AGE_DAYS``.
	They carry customer and vendor names, emails and links, and an unattached private File is
	otherwise kept forever. One that will not delete is left for tomorrow, never raised.

	Only Files carrying ``FILE_TAG`` and still attached to nothing are candidates -- never a File
	merely sitting in the folder, which anyone may have moved there."""
	from frappe.utils import add_days

	cutoff = add_days(now_datetime(), -FILE_MAX_AGE_DAYS)
	names = frappe.get_all(
		"File",
		filters={
			"folder": FILE_FOLDER,
			"attached_to_field": FILE_TAG,
			"attached_to_doctype": ["is", "not set"],
			"is_folder": 0,
			"creation": ["<", cutoff],
		},
		pluck="name",
	)
	deleted = 0
	for name in names:
		try:
			frappe.delete_doc("File", name, ignore_permissions=True)
			frappe.db.commit()
			deleted += 1
		except Exception:
			frappe.db.rollback()
	return deleted


def _job_key(user, token) -> str:
	return f"qbo_matching_excel:{user}:{token}"


def _set_state(key, value, ttl) -> None:
	frappe.cache.set_value(key, value, expires_in_sec=ttl)


def collect_export(status=None, entity_types=None, include_parked=True) -> list:
	"""The workbook's sheets: the Masters queue under the page's own filter, built by the
	page's own ``matching.build_master_rows`` so the rows are the ones the page shows.

	The mapping rows are read **once**, then built ``EXPORT_PAGE`` at a time. Paging the queue
	by offset would skip rows: the export takes minutes, and every row someone decides on the
	page meanwhile leaves a *Needs decision* filter and pulls the rows behind it forward, past
	an offset already read."""
	status = status if status in EXPORT_STATUSES else "Needs decision"
	chosen = [entity for entity in (entity_types or []) if entity in MASTER_ENTITIES] or list(MASTER_ENTITIES)
	by_type: dict = {entity: [] for entity in chosen}
	where, params = matching._master_where(chosen, status, None)
	mapping_rows = matching.master_mapping_rows(where, params)
	for offset in range(0, len(mapping_rows), EXPORT_PAGE):
		for row in matching.build_master_rows(mapping_rows[offset : offset + EXPORT_PAGE]):
			by_type.setdefault(row.get("entity_type"), []).append(master_sheet_row(row))
	sheets = [
		(SHEET_TITLES.get(entity, entity), MASTER_COLUMNS, rows, MASTER_DECISIONS)
		for entity, rows in by_type.items()
		if rows
	]
	if include_parked:
		# Read once too: a Retry on the page takes a row out of Pending Review mid-export.
		parked_rows = matching.parked_mapping_rows(list(TRANSACTION_ENTITIES))
		parked = []
		for offset in range(0, len(parked_rows), EXPORT_PAGE):
			parked.extend(
				txn_sheet_row(row)
				for row in matching.build_parked_rows(parked_rows[offset : offset + EXPORT_PAGE])
			)
		sheets.append((TXN_SHEET, TXN_COLUMNS, parked, TXN_DECISIONS))
	return sheets


def start_export(status=None, entity_types=None, include_parked=True) -> dict:
	"""Queue the export; the page polls ``export_status`` with the token."""
	token = secrets.token_hex(8)
	user = frappe.session.user
	_set_state(_job_key(user, token), {"status": "queued"}, JOB_TTL)
	frappe.enqueue(
		"erpnext_enhancements.quickbooks_online.core.matching_excel.run_export",
		queue="long",
		timeout=EXPORT_TIMEOUT,
		token=token,
		for_user=user,
		status=status,
		entity_types=entity_types,
		include_parked=bool(include_parked),
	)
	return {"token": token}


def run_export(token, for_user, status=None, entity_types=None, include_parked=True) -> None:
	"""The background job: build the workbook, save it as a private File, record the URL."""
	key = _job_key(for_user, token)
	_set_state(key, {"status": "running", "started": time.time()}, JOB_TTL)
	try:
		sheets = collect_export(status=status, entity_types=entity_types, include_parked=include_parked)
		stamp = now_datetime()
		chosen = [entity for entity in (entity_types or []) if entity in MASTER_ENTITIES]
		meta = {
			"exported_on": str(stamp)[:16],
			"exported_by": for_user,
			"filter": f"{status if status in EXPORT_STATUSES else 'Needs decision'}"
			f" -- {', '.join(chosen) if chosen else 'every master type'}"
			f"{' + parked transactions' if include_parked else ''}",
			"counts": {title: len(rows) for title, _columns, rows, _decisions in sheets},
		}
		content = build_workbook(sheets, meta)
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"QuickBooks record matching {str(stamp)[:16].replace(':', '')}.xlsx",
				"is_private": 1,
				"folder": ensure_folder(),
				"content": content,
			}
		).insert(ignore_permissions=True)
		_track(file_doc)
		frappe.db.commit()
		_set_state(
			key,
			{
				"status": "done",
				"file_url": file_doc.file_url,
				"file_name": file_doc.file_name,
				"counts": meta["counts"],
			},
			JOB_TTL,
		)
	except Exception as exc:
		frappe.db.rollback()
		# The message only: a bare re-raise or a traceback would publish frame locals.
		_set_state(key, {"status": "failed", "error": matching.clean_error(exc)}, JOB_TTL)
		frappe.log_error(title="QuickBooks matching Excel export failed", message=matching.clean_error(exc))


def export_status(token) -> dict:
	"""The job's state for its own requester. A job still ``running`` past its RQ timeout was
	killed -- a deploy restarts the workers -- and is reported as lost rather than polled on."""
	state = frappe.cache.get_value(_job_key(frappe.session.user, token)) or {"status": "unknown"}
	if state.get("status") == "running" and time.time() - (state.get("started") or 0) > EXPORT_TIMEOUT + 60:
		return {
			"status": "failed",
			"error": "The export job stopped (a deploy restarts the workers). Export again.",
		}
	return state


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _read_upload(file_name):
	"""The uploaded File's bytes. The upload went through Frappe's own File handling; this
	only reads what it stored -- and only a File the caller uploaded for this: their own,
	private, attached to nothing. Accepting it tags it for the daily clean-up, so accepting
	anything else (another person's file, a document's attachment) would hand that file to a
	job that deletes it in a week. The page only ever sends a fresh upload; the endpoint is
	reachable directly, which is why the check is here."""
	file_doc = frappe.get_doc("File", file_name)
	if not frappe.has_permission("File", "read", doc=file_doc):
		frappe.throw("You cannot read that file.", frappe.PermissionError)
	if file_doc.get("attached_to_doctype") or file_doc.get("attached_to_name"):
		frappe.throw(
			"That file is attached to a document. Upload the worked workbook itself with Import from Excel.",
			frappe.PermissionError,
		)
	if file_doc.owner != frappe.session.user:
		frappe.throw(
			"That file was uploaded by someone else. Upload the workbook yourself with Import from Excel.",
			frappe.PermissionError,
		)
	if not cint(file_doc.is_private):
		frappe.throw(
			"That file is public. Upload the workbook again as a private file.", frappe.PermissionError
		)
	if not str(file_doc.file_name or "").lower().endswith(".xlsx"):
		frappe.throw("Upload the Excel workbook (.xlsx) the export produced.")
	if cint(file_doc.file_size) > MAX_IMPORT_BYTES:
		frappe.throw("That file is too large to be a Record Matching export.")
	# Into the folder the daily clean-up empties, with the tag it deletes by: an upload is
	# the same data as an export.
	_track(file_doc)
	content = file_doc.get_content()
	return content.encode("utf-8") if isinstance(content, str) else content


def _plan_key(user, token) -> str:
	return f"qbo_matching_import:{user}:{token}"


def _preview_row(index, row, result) -> dict:
	return {
		"index": index,
		"sheet": row["sheet"],
		"row_number": row["row_number"],
		"qbo_type": row["qbo_type"],
		"qbo_id": row["qbo_id"],
		"qbo_name": row.get("qbo_name") or "",
		"decision": row.get("decision") or "",
		"link_to": row.get("link_to") or "",
		"target_title": result.get("target_title") or "",
		"category": result["category"],
		"message": result["message"],
		"warnings": result["warnings"],
		"note": row.get("notes") or "",
	}


def preview(file_name) -> dict:
	"""Read the file and say, row by row, what Apply would do. Writes nothing to the
	database; the parsed rows are kept in the cache under a token for ``apply``."""
	parsed = parse_workbook(_read_upload(file_name))
	rows = parsed["rows"]
	results = classify(rows) if rows else []
	# classify ran duplicate_rows over the whole file; apply sees one row at a time and
	# cannot, so the plan keeps those verdicts.
	blocked = {
		index: (result["category"], result["message"])
		for index, result in enumerate(results)
		if result.get("held")
	}
	token = secrets.token_hex(8)
	_set_state(
		_plan_key(frappe.session.user, token),
		{"file": file_name, "rows": rows, "blocked": {str(i): list(v) for i, v in blocked.items()}},
		PLAN_TTL,
	)
	summary = {category: 0 for category in CATEGORIES}
	for result in results:
		summary[result["category"]] += 1
	summary["warnings"] = sum(1 for result in results if result["warnings"])
	return {
		"token": token,
		"file": file_name,
		"errors": parsed["errors"],
		"summary": summary,
		"rows": [
			_preview_row(index, row, result)
			for index, (row, result) in enumerate(zip(rows, results, strict=True))
			if result["category"] != BLANK
		],
		"apply_chunk": APPLY_CHUNK,
	}


def _save_note(mapping_name, row, user) -> None:
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": matching.MAPPING_DOCTYPE,
			"reference_name": mapping_name,
			"comment_email": user,
			"content": f"<p><b>Note from the Excel import</b> ({html.escape(row['sheet'])} row "
			f"{row['row_number']}) {note_marker(row['notes'])}:</p><p>{html.escape(row['notes'])}</p>",
		}
	).insert(ignore_permissions=True)


def _run_action(row, result, user) -> tuple[str, bool]:
	"""Run one row's decision through the page's own code path; returns what happened and
	whether a merge ERPNext refused left the import's copy in place."""
	action = result["action"]
	if action == LINK:
		outcome = matching.decide(
			row["qbo_type"],
			row["qbo_id"],
			row["link_to"],
			fill_blanks=bool(row.get("fill_blanks")),
			merge_duplicate=True,
			user=user,
		)
		merge = outcome.get("merge") or {}
		text_out = f"Linked to {outcome['linked']['doctype']} {outcome['linked']['name']}."
		if merge.get("status") == "merged":
			text_out += f" Merged the import's copy {merge.get('from')} into it."
		elif merge.get("status") == "failed":
			text_out += f" The import's copy {merge.get('from')} was not merged: {merge.get('error')}"
			return text_out, True
		return text_out, False
	if action == KEEP:
		matching.confirm(row["qbo_type"], row["qbo_id"], user=user)
		return "Marked reviewed.", False
	if action == RETRY:
		from erpnext_enhancements.quickbooks_online.core.sync import sync_entity

		outcome = (sync_entity(row["qbo_type"], row["qbo_id"]) or {}).get("result") or {}
		if outcome.get("action") == "manual_review":
			return f"Re-synced; still parked: {outcome.get('reason') or 'see the page'}.", False
		return f"Re-synced: {outcome.get('action') or 'done'}.", False
	return "", False


def apply(token, indexes) -> list:
	"""Apply the previewed rows at ``indexes`` (the page sends a few at a time). One failure
	never stops the rest. Returns one result per index, in order.

	Each row is classified again **immediately before it runs**, not once for the chunk: a Link
	earlier in the same request can merge an import copy away, and ``rename_doc`` re-points
	every other mapping that pointed at it, so a row judged before that ran would be applied
	on a verdict that no longer holds."""
	user = frappe.session.user
	plan = frappe.cache.get_value(_plan_key(user, token))
	if not plan:
		frappe.throw("This preview has expired. Import the file again to get a fresh preview.")
	rows = plan.get("rows") or []
	out = []
	for index in indexes or []:
		index = cint(index)
		if not 0 <= index < len(rows):
			continue
		row = rows[index]
		base = {
			"index": index,
			"sheet": row["sheet"],
			"row_number": row["row_number"],
			"qbo_type": row["qbo_type"],
			"qbo_id": row["qbo_id"],
		}
		held = (plan.get("blocked") or {}).get(str(index))
		if held:
			# A duplicate Row ID, judged across the whole file at preview -- one row alone
			# cannot see it, so the preview's verdict stands.
			out.append(dict(base, ok=False, skipped=True, category=held[0], message=held[1]))
			continue
		result = classify([row])[0]
		if result["category"] != CHANGE:
			out.append(
				dict(base, ok=False, skipped=True, category=result["category"], message=result["message"])
			)
			continue
		message, merge_failed = "", False
		if result["action"]:
			try:
				frappe.db.savepoint("qbo_match_excel_apply")
				message, merge_failed = _run_action(row, result, user)
				frappe.db.commit()
			except Exception as exc:
				_rollback("qbo_match_excel_apply")
				out.append(dict(base, ok=False, skipped=False, message=matching.clean_error(exc)))
				continue
		if result["save_note"]:
			# Its own step: decide/confirm have committed by now, so a note that fails must
			# not report a decision that landed as failed.
			try:
				frappe.db.savepoint("qbo_match_excel_note")
				_save_note(row["row_id"], row, user)
				frappe.db.commit()
				message = f"{message} Note saved.".strip()
			except Exception as exc:
				_rollback("qbo_match_excel_note")
				message = f"{message} The note was NOT saved: {matching.clean_error(exc)}".strip()
				if not result["action"]:
					out.append(dict(base, ok=False, skipped=False, message=message))
					continue
		out.append(dict(base, ok=True, merge_failed=merge_failed, message=message))
	return out


def _rollback(save_point) -> None:
	try:
		frappe.db.rollback(save_point=save_point)
	except Exception:
		frappe.db.rollback()
