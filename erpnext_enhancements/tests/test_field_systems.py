"""Bench-free tests for the field-systems work: photo capture, photo routing, payroll.

Run: python -m pytest erpnext_enhancements/tests/test_field_systems.py -q

``workforce/photo_gate.py`` and ``workforce/payroll_export.py`` both import
``frappe`` at module top, so this installs a **minimal stub** into
``sys.modules`` before importing them. That is the same technique the other
bench-free suites use, and it is why this file must be run as its own pytest
step in ``ci.yml`` rather than being appended to a unittest module list —
``python -m unittest`` silently cannot collect pytest-style function tests, which
is how the QuickBooks suite ran nowhere for weeks.

What is actually guarded here:

* **The payroll workbook layout**, column for column, against the real submitted
  file. The format IS the deliverable — a file that carries the same numbers in a
  different shape costs the payroll clerk more time than it saves.
* **The OT columns stay blank.** There is no payroll module on this site to
  derive a premium from, and a fabricated qualified-overtime figure is a
  tax-reporting error rather than a rounding one.
* **The capture gate counts a pending upload as captured.** This is the whole
  offline-tolerance decision in one assertion; if it ever flips, a technician on
  a site with no signal cannot clock out.
"""

import ast
import json
import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# frappe stub
# ---------------------------------------------------------------------------


def _install_frappe_stub():
	"""Enough frappe for the pure logic under test, and no more.

	Anything these modules call that is NOT stubbed here is, by construction, a
	database or network dependency — which is exactly what a bench-free suite must
	not reach. A missing attribute failing loudly is the desired behaviour.
	"""
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_field_systems_stub", False):
		return

	frappe = types.ModuleType("frappe")
	frappe._ee_field_systems_stub = True

	class _Dict(dict):
		def __getattr__(self, key):
			return self.get(key)

		def __setattr__(self, key, value):
			self[key] = value

	def _throw(message, *args, **kwargs):
		raise ValueError(message)

	frappe._dict = _Dict
	frappe.throw = _throw
	frappe.log_error = lambda *a, **k: None
	frappe.get_roles = lambda *a, **k: []
	frappe.get_traceback = lambda *a, **k: ""
	frappe.response = {}
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})

	def _whitelist(*dargs, **dkwargs):
		def decorator(fn):
			return fn

		return decorator

	frappe.whitelist = _whitelist

	db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		table_exists=lambda *a, **k: True,
		exists=lambda *a, **k: False,
		count=lambda *a, **k: 0,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
	)
	frappe.db = db

	def _gettext(value, *args, **kwargs):
		return value

	frappe._ = _gettext
	frappe.utils = types.ModuleType("frappe.utils")

	def _cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def _flt(value, precision=None):
		try:
			out = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(out, precision) if precision is not None else out

	import datetime as _datetime

	def _getdate(value=None):
		if value is None:
			return _datetime.date.today()
		if isinstance(value, _datetime.datetime):
			return value.date()
		if isinstance(value, _datetime.date):
			return value
		return _datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()

	def _nowdate():
		return _datetime.date.today().isoformat()

	def _add_days(value, days):
		return _getdate(value) + _datetime.timedelta(days=days)

	def _get_last_day(value):
		day = _getdate(value)
		if day.month == 12:
			return day.replace(day=31)
		return day.replace(month=day.month + 1, day=1) - _datetime.timedelta(days=1)

	for name, fn in (
		("cint", _cint), ("flt", _flt), ("getdate", _getdate), ("nowdate", _nowdate),
		("add_days", _add_days), ("get_last_day", _get_last_day),
		("now_datetime", _datetime.datetime.now),
		("escape_html", lambda v: str(v)),
	):
		setattr(frappe.utils, name, fn)
		setattr(frappe, name, fn)

	frappe.model = types.ModuleType("frappe.model")
	frappe.model.document = types.ModuleType("frappe.model.document")
	frappe.model.document.Document = type("Document", (), {})

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils
	sys.modules["frappe.model"] = frappe.model
	sys.modules["frappe.model.document"] = frappe.model.document


def setup_module(module):
	_install_frappe_stub()


_install_frappe_stub()

from erpnext_enhancements.workforce import payroll_export, photo_gate

# ---------------------------------------------------------------------------
# WP-2 — the capture gate
# ---------------------------------------------------------------------------


class _Row:
	def __init__(self, upload_status):
		self.upload_status = upload_status


class _Interval:
	def __init__(self, *statuses):
		self.photos = [_Row(s) for s in statuses]
		self.photo_status = None
		self.photo_skip_reason = None
		self.photos_pending_upload = 0


def _settings(**overrides):
	base = {"require_job_photos": 1, "min_photos_per_interval": 1,
	        "allow_photo_skip": 1, "require_skip_reason": 1}
	base.update(overrides)
	return base


def test_a_pending_upload_counts_as_captured():
	"""THE offline-tolerance decision, in one assertion.

	The photo was taken; the bytes are queued on a phone with no signal. If this
	ever flips to counting only Uploaded rows, a technician on a bad site cannot
	clock out — which is the failure mode that would get the whole rollout
	switched off inside a week.
	"""
	assert photo_gate.photos_captured(_Interval("Pending")) == 1
	assert photo_gate.check(_Interval("Pending"), settings=_settings()) == photo_gate.STATUS_CAPTURED


def test_a_failed_upload_does_not_count():
	"""The device gave up. At that point there is no evidence the photo exists
	anywhere, so it cannot satisfy a requirement."""
	assert photo_gate.photos_captured(_Interval("Failed")) == 0


def test_no_photo_and_no_reason_is_refused():
	try:
		photo_gate.check(_Interval(), settings=_settings())
	except ValueError:
		return
	raise AssertionError("an interval with no photo and no reason must not close")


def test_a_reason_closes_the_interval_when_skipping_is_allowed():
	assert photo_gate.check(_Interval(), skip_reason="customer declined",
	                        settings=_settings()) == photo_gate.STATUS_SKIPPED


def test_a_blank_reason_is_not_a_reason():
	try:
		photo_gate.check(_Interval(), skip_reason="   ", settings=_settings())
	except ValueError:
		return
	raise AssertionError("whitespace must not satisfy a mandatory skip reason")


def test_skipping_can_be_forbidden_outright():
	"""allow_photo_skip = 0 is a hard block with no escape hatch. It is a people
	decision, it ships OFF, and it must actually work when switched on."""
	try:
		photo_gate.check(_Interval(), skip_reason="anything",
		                 settings=_settings(allow_photo_skip=0))
	except ValueError:
		return
	raise AssertionError("allow_photo_skip=0 must refuse even with a reason")


def test_the_gate_is_inert_when_switched_off():
	assert photo_gate.check(_Interval(), settings=_settings(require_job_photos=0)) == \
		photo_gate.STATUS_NOT_REQUIRED


def test_a_zero_minimum_cannot_make_the_gate_vacuous():
	"""A misconfigured 0 would read as ON while requiring nothing — worse than
	either honest state, because the report would show full compliance."""
	assert photo_gate.minimum_photos(_settings(min_photos_per_interval=0)) == 1


def test_stamp_records_the_pending_flag():
	interval = _Interval("Pending", "Uploaded")
	photo_gate.stamp(interval, photo_gate.STATUS_CAPTURED)
	assert interval.photo_status == photo_gate.STATUS_CAPTURED
	assert interval.photos_pending_upload == 1

	settled = _Interval("Uploaded")
	photo_gate.stamp(settled, photo_gate.STATUS_CAPTURED)
	assert settled.photos_pending_upload == 0


def test_skip_reason_is_only_kept_on_a_skip():
	"""A reason left behind on an interval that ended up with photos would read as
	a skip forever in the compliance report."""
	interval = _Interval("Uploaded")
	photo_gate.stamp(interval, photo_gate.STATUS_CAPTURED, skip_reason="typed then changed their mind")
	assert not interval.photo_skip_reason


# ---------------------------------------------------------------------------
# WP-8 — the payroll workbook
# ---------------------------------------------------------------------------

#: Transcribed from the submitted file, 5813 7-16-2026 to 7-31-2026.
EXPECTED_HEADER = (
	("", "", "S/H", "Reg", "Qualified", "OT", "PTO", "Hol", "Bonus", "Comm", "Reimb", "AddlH", "HlthS", "Svcs"),
	("Emp", "Employee", "Salaried", "Regular", "OT", "Overtime", "PTO", "Holiday", "Bonus",
	 "Commission", "Reimbursement", "Hourly Pay", "Healthcare Stipend", "Services"),
	("Num", "Name", "/ Hourly", "Hours", "Hours", "Hours", "Hours", "Hours", "Amount",
	 "Amount", "Amount", "Hours", "Amount", "Amount"),
)


def test_the_column_header_matches_the_submitted_file_exactly():
	"""The provider's import maps by POSITION. A reordered or renamed column does
	not fail loudly — it lands somebody's hours in the Bonus field."""
	assert payroll_export.HEADER_ROWS == EXPECTED_HEADER
	for row in payroll_export.HEADER_ROWS:
		assert len(row) == 14


def test_provider_identifiers_are_right():
	assert payroll_export.CLIENT_CODE == "5813"
	assert payroll_export.FIRM_CODE == "SHAWA2530"
	assert payroll_export.EMPLOYER_NAME == "Sapphire Fountains LLC"


def test_salaried_period_hours_is_the_annual_figure_over_24_periods():
	assert payroll_export.SALARIED_PERIOD_HOURS == 86.67
	assert round(2080 / 24, 2) == 86.67


def test_semi_monthly_period_boundaries():
	assert payroll_export.current_period("2026-07-31") == \
		(__import__("datetime").date(2026, 7, 16), __import__("datetime").date(2026, 7, 31))
	assert payroll_export.current_period("2026-07-01")[1].day == 15
	# February, because a 30/31 lookup table would be wrong twice a year.
	assert payroll_export.current_period("2026-02-20")[1].day == 28
	assert payroll_export.current_period("2024-02-20")[1].day == 29


def test_previous_period_crosses_the_month_boundary():
	assert payroll_export.previous_period("2026-08-04") == \
		(__import__("datetime").date(2026, 7, 16), __import__("datetime").date(2026, 7, 31))


def test_period_and_sheet_labels_match_the_submitted_file():
	assert payroll_export.period_label("2026-07-16", "2026-07-31") == "7/16/2026 to 7/31/2026"
	assert payroll_export.sheet_name("2026-07-16", "2026-07-31") == "5813 7-16-2026 to 7-31-2026"


def test_employee_names_use_the_providers_double_space_format():
	"""``Symanski  Lisa J.`` — two spaces, middle initial with a period. Not a typo
	in the source: it is how the provider's own export writes it, and a clerk
	comparing two files notices single-spaced names as a difference."""
	assert payroll_export._provider_name(
		{"last_name": "Symanski", "first_name": "Lisa", "middle_name": "Jane"}
	) == "Symanski  Lisa J."
	assert payroll_export._provider_name(
		{"last_name": "Bailey", "first_name": "Parker", "middle_name": ""}
	) == "Bailey  Parker"
	assert payroll_export._provider_name(
		{"last_name": "", "first_name": "", "employee_name": "Shellyce Keyes", "name": "HR-EMP-1"}
	) == "Shellyce Keyes"


def test_overtime_columns_are_never_populated():
	"""hrms is NOT installed on this site — no Salary Structure, no salary slips,
	no payroll module at all. Emitting a computed 'Qualified OT' would be inventing
	a federal tax figure. The columns exist, in position, and stay blank."""
	source = (ROOT / "workforce" / "payroll_export.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	builder = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == "workbook_rows"
	)
	assigned_indices = set()
	for node in ast.walk(builder):
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
					if target.value.id == "line" and isinstance(target.slice, ast.Constant):
						assigned_indices.add(target.slice.value)
	# 4..11 are Qualified OT, Overtime, PTO, Holiday, Bonus, Commission,
	# Reimbursement and Additional Hourly Pay.
	for index in range(4, 12):
		assert index not in assigned_indices, (
			f"workbook_rows writes column {index}, which must be left for the payroll provider"
		)


def test_payroll_seed_refuses_ambiguous_name_matches():
	"""A payroll number on the wrong person is invisible; a missing one is flagged
	in the export report. So a two-way name collision must skip, not guess."""
	source = (ROOT / "patches" / "seed_payroll_employee_numbers.py").read_text(encoding="utf-8")
	assert "len(candidates) > 1" in source
	assert "refusing to set number" in source


def test_payroll_seed_covers_every_row_of_the_submitted_file():
	source = (ROOT / "patches" / "seed_payroll_employee_numbers.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	records = next(
		node for node in tree.body
		if isinstance(node, ast.Assign)
		and getattr(node.targets[0], "id", None) == "PROVIDER_RECORDS"
	)
	numbers = {
		element.values[0].value
		for element in records.value.values
	}
	# The 16 employee rows of 5813 7-16-2026 to 7-31-2026.
	assert numbers == {"22", "7", "17", "27", "21", "14", "3", "15",
	                   "29", "30", "20", "25", "19", "31", "28", "24"}


def test_the_four_salaried_employees_match_the_submitted_file():
	source = (ROOT / "patches" / "seed_payroll_employee_numbers.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	records = next(
		node for node in tree.body
		if isinstance(node, ast.Assign)
		and getattr(node.targets[0], "id", None) == "PROVIDER_RECORDS"
	)
	salaried = set()
	for key, value in zip(records.value.keys, records.value.values):
		fields = {k.value: v.value for k, v in zip(value.keys, value.values)}
		if fields["classification"] == "Salaried":
			salaried.add(fields["number"])
	# Blass (7), Harris (3), Mabey (25), Morisseau (19) carry 86.67 in the file.
	assert salaried == {"7", "3", "25", "19"}
	# 4 x 86.67 = 346.68, the Regular Hours total on the submitted sheet.
	assert round(4 * payroll_export.SALARIED_PERIOD_HOURS, 2) == 346.68


def test_healthcare_stipends_total_the_submitted_figure():
	source = (ROOT / "patches" / "seed_payroll_employee_numbers.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	records = next(
		node for node in tree.body
		if isinstance(node, ast.Assign)
		and getattr(node.targets[0], "id", None) == "PROVIDER_RECORDS"
	)
	total = 0
	for value in records.value.values:
		fields = {k.value: v.value for k, v in zip(value.keys, value.values)}
		total += fields["stipend"]
	assert total == 375, "the submitted file totals 375 in the HlthS column"


def test_payroll_export_is_role_gated():
	source = (ROOT / "workforce" / "payroll_export.py").read_text(encoding="utf-8")
	assert "_may_export" in source
	assert "PermissionError" in source, "the workbook contains every employee's hours"


# ---------------------------------------------------------------------------
# WP-2 / WP-3 — schema and wiring
# ---------------------------------------------------------------------------


def _doctype(module, name):
	path = ROOT / module / "doctype" / name / f"{name}.json"
	with open(path, encoding="utf-8") as fh:
		return json.load(fh)


def test_job_interval_photo_is_a_child_table_in_workforce():
	"""Every custom DocType must sit in its declared module — tests/test_doctype_modules
	asserts it against the filesystem and fails the build otherwise."""
	doc = _doctype("workforce", "job_interval_photo")
	assert doc["istable"] == 1
	assert doc["module"] == "Workforce"


def test_the_photo_row_carries_a_device_minted_id():
	"""Idempotency across an offline queue that retries depends entirely on this."""
	doc = _doctype("workforce", "job_interval_photo")
	fields = {f["fieldname"]: f for f in doc["fields"]}
	assert fields["client_uid"]["reqd"] == 1
	assert set(fields["upload_status"]["options"].split("\n")) == {"Pending", "Uploaded", "Failed"}


def test_job_interval_photo_fields_exist():
	doc = _doctype("workforce", "job_interval")
	fields = {f["fieldname"]: f for f in doc["fields"]}
	assert fields["photos"]["options"] == "Job Interval Photo"
	assert fields["photo_status"]["read_only"] == 1, (
		"photo_status is the server's verdict — a writable one could be set by a modified PWA bundle"
	)
	assert fields["photos_pending_upload"]["read_only"] == 1


def test_photo_settings_default_to_off():
	"""Staged-rollout convention. A gate that arrives switched on without warning
	is how a field rollout gets rejected on day one."""
	doc = _doctype("workforce", "time_kiosk_settings")
	fields = {f["fieldname"]: f for f in doc["fields"]}
	assert fields["require_job_photos"].get("default") == "0"
	# ...but permissive once switched on.
	assert fields["allow_photo_skip"].get("default") == "1"


def test_the_gate_runs_on_the_server_for_both_closing_actions():
	"""Client-side validation on a field device is a suggestion. Switch and Stop
	must both call the gate, and both must call it BEFORE mutating the interval —
	a refused close has to leave the job open, not half-ended."""
	source = (ROOT / "api" / "time_kiosk.py").read_text(encoding="utf-8")
	assert source.count("photo_gate.check(") == 2, "Switch and Stop must each gate exactly once"

	tree = ast.parse(source)
	handler = next(
		node for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef) and node.name == "log_time"
	)
	assert any(
		isinstance(arg, ast.arg) and arg.arg == "skip_reason" for arg in handler.args.args
	), "log_time must accept skip_reason"


def test_drive_mirroring_is_not_reimplemented():
	"""google_drive/drive_sync.py already owns Drive upload end to end, idempotently
	(it bails the moment custom_drive_file_id is set). A second upload path here
	would mean a second idempotency bug."""
	source = (ROOT / "workforce" / "photo_routing.py").read_text(encoding="utf-8")
	for forbidden in ("googleapiclient", "MediaIoBaseUpload", "get_drive_service"):
		assert forbidden not in source, (
			f"photo_routing.py references {forbidden} — Drive upload belongs to drive_sync.py"
		)


def test_photo_routing_is_enqueued_never_inline():
	"""Drive is a third-party API. A technician's clock-out must not wait on it."""
	source = (ROOT / "api" / "time_kiosk.py").read_text(encoding="utf-8")
	assert "frappe.enqueue(" in source
	assert "photo_routing.route_job_photo" in source


def test_photo_copies_reassert_privacy():
	"""Photographs of the inside of customers' property. A public file URL is
	unauthenticated and effectively permanent."""
	for path in (ROOT / "workforce" / "photo_routing.py", ROOT / "api" / "time_kiosk.py"):
		source = path.read_text(encoding="utf-8")
		assert "is_private" in source


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def test_every_new_report_has_explicit_roles():
	"""Definition of done: nothing defaults to All."""
	reports = [
		("workforce", "job_photo_compliance"),
		("workforce", "job_photo_library"),
		("workforce", "payroll_hours_export"),
		("project_enhancements", "featured_jobs"),
		("crm_enhancements", "attribution_gaps"),
	]
	for module, slug in reports:
		path = ROOT / module / "report" / slug / f"{slug}.json"
		with open(path, encoding="utf-8") as fh:
			report = json.load(fh)
		roles = {r["role"] for r in report.get("roles", [])}
		assert roles, f"{slug} has no roles — it would default to All"
		assert "All" not in roles, f"{slug} grants All"


def test_payroll_report_is_not_readable_by_sales():
	"""It contains every employee's hours."""
	path = ROOT / "workforce" / "report" / "payroll_hours_export" / "payroll_hours_export.json"
	with open(path, encoding="utf-8") as fh:
		roles = {r["role"] for r in json.load(fh)["roles"]}
	assert roles == {"System Manager", "HR Manager", "Accounts Manager"}


def test_reports_live_in_a_module_that_exists():
	modules = set((ROOT / "modules.txt").read_text(encoding="utf-8").split("\n"))
	for module, slug in (("workforce", "job_photo_compliance"),
	                     ("workforce", "payroll_hours_export"),
	                     ("project_enhancements", "featured_jobs")):
		path = ROOT / module / "report" / slug / f"{slug}.json"
		with open(path, encoding="utf-8") as fh:
			assert json.load(fh)["module"] in modules
