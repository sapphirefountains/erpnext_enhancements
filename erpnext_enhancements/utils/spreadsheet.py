"""Build a downloadable CSV/XLSX payload from a table of rows.

Shared by the Gantt export (``api/gantt.py``) and the Scope-tab task-tree export
(``project_enhancements/page/project_dashboard/project_dashboard.py``). Both
need the same three things — pick a writer, name the file, get the bytes to the
browser — and the interesting part is *how* the bytes travel, which is worth
stating once rather than twice.

**Why base64 in a JSON response rather than a streamed download.** Frappe's
usual pattern is to set ``frappe.response.filecontent`` / ``type = "download"``,
which works for a normal form POST but not for a ``frappe.call`` from JS: the
desk's AJAX layer parses the response as JSON and the binary is lost. The
alternative, ``frappe.utils.print_format.download_...``-style redirects, would
need the whole widget config in a URL query string — and these configs carry
filters and field maps that blow past practical URL length limits. So the bytes
come back inside the JSON envelope and the client rebuilds a Blob. The cost is
~33% transfer overhead on a file that is a few hundred KB at worst.

**Why UTF-8 BOM on the CSV.** Excel on Windows reads a BOM-less UTF-8 CSV as
the system codepage, so any non-ASCII customer or task name arrives mojibake.
The BOM is the only reliable signal Excel honours, and every other CSV consumer
skips it silently.
"""

import base64

import frappe
from frappe import _
from frappe.utils import cstr, nowdate

FORMATS = {
	"csv": ("text/csv", ".csv"),
	"xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
}


def safe_name(title, fallback="export"):
	"""Strip a client-supplied filename down to characters safe in a filename.

	The value is echoed back to the browser and used as a download name, so path
	separators, quotes and control characters must not survive.
	"""
	cleaned = "".join(c for c in cstr(title) if c.isalnum() or c in " -_.")
	return cleaned.strip().replace(" ", "-")[:80] or fallback


def build_payload(rows, file_format="csv", title=None, sheet_name=None):
	"""``[[header...], [cell...], ...]`` -> ``{filename, content_type, filecontent}``.

	Returns ``None`` when there is nothing but a header row, so callers can
	surface "nothing to export" instead of handing the user an empty file.

	Args:
		rows: list of lists; the first is the header.
		file_format: ``"csv"`` or ``"xlsx"``. Anything else throws.
		title: base filename (sanitised; the current date is appended).
		sheet_name: worksheet name for xlsx. Defaults to "Data".
	"""
	file_format = cstr(file_format).lower()
	if file_format not in FORMATS:
		frappe.throw(_("Unsupported export format"))
	if not rows or len(rows) <= 1:
		return None

	content_type, extension = FORMATS[file_format]
	filename = f"{safe_name(title)}-{nowdate()}{extension}"

	if file_format == "csv":
		from frappe.utils.csvutils import to_csv

		payload = to_csv(rows).encode("utf-8-sig")
	else:
		from frappe.utils.xlsxutils import make_xlsx

		payload = make_xlsx(rows, sheet_name or _("Data")).getvalue()

	return {
		"filename": filename,
		"content_type": content_type,
		"filecontent": base64.b64encode(payload).decode("ascii"),
	}
