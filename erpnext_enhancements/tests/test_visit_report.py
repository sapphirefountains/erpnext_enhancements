# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for what the customer's visit report adds (``sapphire_maintenance/visit_report.py``).

D1 of Phase D (TASK-2026-02486), from Lisa's review of the first report on 2026-10-12:

* water features print by name ("East Courtyard Fountain"), not ID (``MAINT-HARDWARE-East-Courtyard``);
* the contract line carries the visit's calendar year (Nik's choice);
* each reading gets a chart over the site's recent approved visits, one line per fountain,
  the target range shaded, and red where it was out of range;
* the technician's "fountain as we left it" photo is inlined, shrunk, as a data URI;
* none of it can stop the PDF: a failing part is logged and left out;
* it reads the visit as saved, by name, and only for a caller who may read it, and inlines only
  a photo attached to that visit (a Jinja global is callable from any template, and v16's print
  view renders a document posted as JSON).

Stubs a minimal ``frappe`` in ``setUpModule``; runs the real ``visit_report``, ``visit_compare``
and ``print_style``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_report
"""

import base64
import datetime
import io
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RECORD = "Sapphire Maintenance Record"
READING = "Sapphire Chemistry Reading"
STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.sapphire_maintenance.visit_compare",
	"erpnext_enhancements.sapphire_maintenance.visit_report",
)

report = None
ROWS = {}
FILES = {}
DOCS = {}
ERRORS = []
READABLE = {"yes": True}
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 10, 12)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _match(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, (list, tuple)):
			op, arg = cond
			if op == "in" and value not in arg:
				return False
		elif value != cond:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, **kwargs):
	if ROWS.get("raise") == doctype:
		raise RuntimeError(f"{doctype} on fire")
	return [_Dict({f: row.get(f) for f in fields}) for row in ROWS.get(doctype, []) if _match(row, filters)]


def _db_sql(query, params, as_dict=False):
	"""visit_compare.EARLIER_SQL (its rule is pinned in test_visit_compare)."""
	rows = [
		row
		for row in ROWS.get(RECORD, [])
		if row.project == params["project"]
		and row.docstatus == 1
		and row.name != params["name"]
		and report.visit_compare.visit_day(row) <= params["day"]
	]
	rows.sort(key=lambda row: report.visit_compare.visit_day(row), reverse=True)
	return [_Dict(row) for row in rows]


def _db_get_value(doctype, filters, field=None, **kwargs):
	if doctype == "Project":
		return {"PRJ-1": "Myers Mortuary Maintenance Contract"}.get(filters)
	if doctype == "File":
		found = FILES.get(filters["file_url"], {})
		if (filters["attached_to_doctype"], filters["attached_to_name"]) != found.get("attached_to"):
			return None
		return found.get("name")
	return None


def _get_doc(doctype, name):
	if doctype == RECORD:
		return DOCS[name]
	assert doctype == "File", doctype
	return _File(next(f["path"] for f in FILES.values() if f["name"] == name))


class _File:
	def __init__(self, path):
		self.path = path

	def get_full_path(self):
		return self.path


def setUpModule():
	global report
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	import erpnext_enhancements.sapphire_maintenance as package

	for attr in ("visit_compare", "visit_report"):
		if attr in vars(package):
			delattr(package, attr)

	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.has_permission = lambda doctype, ptype="read", doc=None: READABLE["yes"]
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: ERRORS.append(k.get("title"))
	frappe.db = types.SimpleNamespace(
		sql=_db_sql, get_value=_db_get_value, exists=lambda doctype, name: doctype == RECORD and name in DOCS
	)
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v, *a: float(v or 0)
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-12"
	frappe.utils = utils
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	from erpnext_enhancements.sapphire_maintenance import visit_report

	report = visit_report


def tearDownModule():
	import erpnext_enhancements.sapphire_maintenance as package

	for attr in ("visit_compare", "visit_report"):
		if attr in vars(package):
			delattr(package, attr)
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset():
	ROWS.clear()
	FILES.clear()
	DOCS.clear()
	ERRORS.clear()
	READABLE["yes"] = True
	ROWS["Serial No"] = [
		_Dict(name="SN-EAST", item_name="Hardware Apartments - East Courtyard Fountain"),
		_Dict(name="SN-WEST", item_name="Hardware Apartments - West Lobby Granite Water Wall"),
		_Dict(name="SN-PLAIN", item_name="Lobby Fountain"),
	]


def _visit(readings=(), serial_no=None, day="2026-10-12", **extra):
	"""This visit, saved: maintenance_report loads it again by name."""
	doc = _Dict(
		doctype=RECORD,
		name="NOW",
		project="PRJ-1",
		docstatus=0,
		serial_no=serial_no,
		visit_date=day,
		scheduled_visit_date=None,
		creation=f"{day} 10:00:00",
		chemistry_readings=[_Dict(r) for r in readings],
		cleaning_tasks=[],
		maintenance_results=[],
		consumables=[],
		**extra,
	)
	DOCS[doc.name] = doc
	return doc


def _approved(name, day, readings):
	ROWS.setdefault(RECORD, []).append(
		_Dict(
			name=name,
			project="PRJ-1",
			docstatus=1,
			serial_no=None,
			visit_date=day,
			scheduled_visit_date=None,
			creation=f"{day} 09:00:00",
		)
	)
	for reading, value, serial in readings:
		ROWS.setdefault(READING, []).append(
			_Dict(
				parent=name,
				parenttype=RECORD,
				reading=reading,
				reading_value=value,
				out_of_range=0,
				serial_no=serial,
			)
		)


class TestNames(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_part_after_the_site(self):
		self.assertEqual(
			report.short_name("Hardware Apartments - East Courtyard Fountain"), "East Courtyard Fountain"
		)
		self.assertEqual(
			report.short_name("Valley Fair Mall - Fountain System (Bowl - Wall)"),
			"Fountain System (Bowl - Wall)",
		)
		self.assertEqual(report.short_name("Lobby Fountain"), "Lobby Fountain")
		self.assertEqual(report.short_name(None), "")

	def test_a_site_visit_names_each_fountain_in_form_order(self):
		doc = _visit(
			[
				{"reading": "pH", "reading_value": 7.4, "serial_no": "SN-WEST"},
				{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"},
			]
		)
		self.assertEqual(report.covered_features(doc), ["SN-WEST", "SN-EAST"])
		self.assertEqual(
			report.maintenance_report(doc)["feature_lines"],
			["West Lobby Granite Water Wall", "East Courtyard Fountain"],
		)

	def test_a_single_fountain_visit_names_its_own(self):
		names = report.maintenance_report(_visit(serial_no="SN-PLAIN"))
		self.assertEqual(
			(names["features"], names["feature_lines"]), ({"SN-PLAIN": "Lobby Fountain"}, ["Lobby Fountain"])
		)

	def test_the_contract_line_carries_the_calendar_year(self):
		self.assertEqual(
			report.contract_line(_visit(day="2026-10-06")), "Myers Mortuary Maintenance Contract · 2026"
		)
		self.assertEqual(
			report.contract_line(_visit(day="2027-01-05")), "Myers Mortuary Maintenance Contract · 2027"
		)
		self.assertEqual(report.contract_line(_Dict(project=None)), "")


class TestCharts(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_one_point_is_not_a_trend(self):
		doc = _visit([{"reading": "pH", "reading_value": 7.4, "min_value": 7.0, "max_value": 7.6}])
		self.assertEqual(report.chemistry_charts(doc), [])

	def test_a_chart_per_reading_over_recent_approved_visits(self):
		_approved("A", "2026-09-29", [("pH", 7.2, None), ("ORP", 680, None)])
		_approved("B", "2026-10-06", [("pH", 7.4, None), ("ORP", 400, None)])
		doc = _visit(
			[
				{"reading": "pH", "reading_value": 7.5, "min_value": 7.0, "max_value": 7.6},
				{
					"reading": "ORP",
					"reading_value": 600,
					"uom": "mV",
					"min_value": 650,
					"max_value": 750,
					"out_of_range": 1,
				},
			]
		)
		charts = report.chemistry_charts(doc)
		self.assertEqual(len(charts), 2)
		ph, orp = charts
		self.assertIn(">pH</text>", ph)
		self.assertIn(">ORP (mV)</text>", orp)
		for svg in charts:
			self.assertEqual(svg.count("<circle"), 3, "two earlier visits and this one")
			self.assertIn("<polyline", svg)
			self.assertIn(">9/29</text>", svg)
			self.assertIn(">10/12</text>", svg)
			self.assertIn(f'fill="{report.ps.TEAL}" fill-opacity="0.22"', svg, "the target band")
		self.assertEqual(orp.count(f'fill="{report.ps.RED}"'), 1, "this visit's out-of-range point")

	def test_a_site_visit_draws_a_line_per_fountain_with_a_legend(self):
		_approved("A", "2026-10-06", [("pH", 7.2, "SN-EAST"), ("pH", 8.4, "SN-WEST")])
		doc = _visit(
			[
				{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"},
				{"reading": "pH", "reading_value": 8.1, "serial_no": "SN-WEST"},
			]
		)
		(svg,) = report.chemistry_charts(doc, {"SN-EAST": "East Courtyard Fountain", "SN-WEST": "West Lobby"})
		self.assertEqual(svg.count("<polyline"), 2)
		self.assertIn(">East Courtyard Fountain</text>", svg)
		self.assertIn(">West Lobby</text>", svg)

	def test_the_axis_is_labelled_at_the_target_range(self):
		"""What a customer reads the line against; the extremes only when there is no target."""
		_approved("A", "2026-10-06", [("pH", 8.4, None)])
		(svg,) = report.chemistry_charts(
			_visit([{"reading": "pH", "reading_value": 6.9, "min_value": 7.2, "max_value": 7.8}])
		)
		labels = re.findall(r'text-anchor="end"[^>]*>([^<]+)</text>', svg)
		self.assertEqual(labels, ["7.2", "7.8"])
		_reset()
		_approved("A", "2026-10-06", [("TDS", 410, None)])
		(svg,) = report.chemistry_charts(_visit([{"reading": "TDS", "reading_value": 380}]))
		self.assertEqual(re.findall(r'text-anchor="end"[^>]*>([^<]+)</text>', svg), ["380", "410"])

	def test_four_fountains_keep_the_legend_above_the_plot(self):
		serials = ["SN-1", "SN-2", "SN-3", "SN-4"]
		_approved("A", "2026-10-06", [("pH", 7.0 + i / 10, serial) for i, serial in enumerate(serials)])
		doc = _visit(
			[
				{"reading": "pH", "reading_value": 7.9 - i / 10, "serial_no": serial}
				for i, serial in enumerate(serials)
			]
		)
		(svg,) = report.chemistry_charts(doc, {serial: f"Fountain {serial}" for serial in serials})
		legend_rows = [float(v) for v in re.findall(r'<text x="63" y="([\d.]+)"', svg)]
		self.assertEqual(len(legend_rows), 4)
		self.assertEqual(svg.count('x2="58"'), 4, "a colour swatch per fountain")
		top_point = min(float(v) for v in re.findall(r'cy="([\d.]+)"', svg))
		self.assertLess(max(legend_rows), top_point - 4)
		height = int(re.search(r'height="(\d+)"', svg).group(1))
		self.assertGreater(height, 160, "the chart grows to hold its legend")

	def test_text_in_the_chart_is_escaped(self):
		_approved("A", "2026-10-06", [("<b>pH</b>", 7.2, None)])
		(svg,) = report.chemistry_charts(_visit([{"reading": "<b>pH</b>", "reading_value": 7.5}]))
		self.assertIn("&lt;b&gt;pH&lt;/b&gt;", svg)
		self.assertNotIn("<b>", svg)

	def test_only_print_design_system_colours(self):
		_approved("A", "2026-10-06", [("pH", 7.2, None)])
		(svg,) = report.chemistry_charts(
			_visit([{"reading": "pH", "reading_value": 7.5, "min_value": 7, "max_value": 7.6}])
		)
		tokens = {
			getattr(report.ps, name).lower()
			for name in dir(report.ps)
			if isinstance(getattr(report.ps, name), str)
			and re.fullmatch(r"#[0-9a-fA-F]{6}", getattr(report.ps, name))
		}
		for colour in re.findall(r"#[0-9a-fA-F]{6}", svg):
			self.assertIn(colour.lower(), tokens)


def _needs_pillow(test):
	try:
		import PIL
	except ImportError:  # pragma: no cover
		test.skipTest("Pillow not installed")


def _file(test, url, data, attached_to=(RECORD, "NOW"), suffix=".png"):
	handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
	handle.write(data)
	handle.close()
	test.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
	FILES[url] = {"name": f"FILE-{len(FILES) + 1}", "path": handle.name, "attached_to": attached_to}
	return url


def _png(size=(2400, 1600)):
	from PIL import Image

	buffer = io.BytesIO()
	Image.new("RGB", size, (0, 96, 156)).save(buffer, format="PNG")
	return buffer.getvalue()


class TestFountainPhoto(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_private_photo_is_inlined_and_shrunk(self):
		_needs_pillow(self)
		uri = report.image_data_uri(_file(self, "/private/files/fountain.png", _png()), "NOW")
		self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
		from PIL import Image

		image = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
		self.assertEqual(max(image.size), report.PHOTO_SIDE)

	def test_no_photo_or_a_missing_file_prints_nothing(self):
		self.assertIsNone(report.image_data_uri(None, "NOW"))
		self.assertIsNone(report.image_data_uri("/private/files/gone.jpg", "NOW"))

	def test_only_a_file_on_this_sites_disk(self):
		FILES["https://example.test/a.jpg"] = {"name": "FILE-9", "path": "x", "attached_to": (RECORD, "NOW")}
		self.assertIsNone(report.image_data_uri("https://example.test/a.jpg", "NOW"))

	def test_a_file_attached_anywhere_else_prints_nothing(self):
		"""Only this visit's own Files: a URL is not a permission."""
		_needs_pillow(self)
		payroll = _file(
			self, "/private/files/payroll.png", _png((40, 30)), attached_to=("Salary Slip", "SAL-1")
		)
		loose = _file(self, "/private/files/loose.png", _png((40, 30)), attached_to=(None, None))
		other = _file(self, "/private/files/other.png", _png((40, 30)), attached_to=(RECORD, "ANOTHER"))
		for url in (payroll, loose, other):
			with self.subTest(url=url):
				self.assertIsNone(report.image_data_uri(url, "NOW"))

	def test_a_file_that_is_not_an_image_prints_nothing(self):
		pdf = _file(self, "/private/files/scope.pdf", b"%PDF-1.4 not a photo", suffix=".pdf")
		self.assertIsNone(report.image_data_uri(pdf, "NOW"))

	def test_the_report_carries_it(self):
		_needs_pillow(self)
		doc = _visit(fountain_photo=_file(self, "/private/files/fountain.png", _png((40, 30))))
		self.assertTrue(
			report.maintenance_report(doc)["fountain_photo"].startswith("data:image/jpeg;base64,")
		)


class TestOnlyTheSavedVisit(unittest.TestCase):
	"""A Jinja global is callable from any template, and v16's print view renders a document a
	caller posts as JSON: only the name is taken from what it is handed."""

	EMPTY = {
		"features": {},
		"feature_lines": [],
		"contract_line": "",
		"charts": [],
		"fountain_photo": None,
		# v1.614.0: the day a sign-off link went to a client who has not signed.
		"sign_off_sent": "",
	}

	def setUp(self):
		_reset()

	def test_a_posted_copy_prints_what_is_saved(self):
		_needs_pillow(self)
		_file(self, "/private/files/payroll.png", _png((40, 30)), attached_to=("Salary Slip", "SAL-1"))
		_visit(serial_no="SN-PLAIN")
		posted = _Dict(
			DOCS["NOW"], serial_no="SN-EAST", project="PRJ-2", fountain_photo="/private/files/payroll.png"
		)
		out = report.maintenance_report(posted)
		self.assertEqual(out["feature_lines"], ["Lobby Fountain"])
		self.assertEqual(out["contract_line"], "Myers Mortuary Maintenance Contract · 2026")
		self.assertIsNone(out["fountain_photo"])

	def test_a_caller_who_cannot_read_the_visit_gets_nothing(self):
		doc = _visit(serial_no="SN-PLAIN")
		READABLE["yes"] = False
		self.assertEqual(report.maintenance_report(doc), self.EMPTY)
		self.assertEqual(ERRORS, [], "a refusal is not an error")

	def test_an_unsaved_visit_or_another_doctype_gets_nothing(self):
		self.assertEqual(
			report.maintenance_report(_Dict(doctype=RECORD, name="NEW-1", project="PRJ-1")), self.EMPTY
		)
		_visit(serial_no="SN-PLAIN")
		self.assertEqual(report.maintenance_report(_Dict(doctype="Project", name="NOW")), self.EMPTY)
		self.assertEqual(report.maintenance_report(None), self.EMPTY)
		self.assertEqual(ERRORS, [])


class TestNeverRaises(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_failing_part_is_left_out_and_logged(self):
		ROWS["raise"] = "Serial No"
		doc = _visit([{"reading": "pH", "reading_value": 7.5, "serial_no": "SN-EAST"}])
		out = report.maintenance_report(doc)
		self.assertEqual((out["features"], out["feature_lines"]), ({}, []))
		self.assertEqual(
			out["contract_line"], "Myers Mortuary Maintenance Contract · 2026", "the rest still printed"
		)
		self.assertEqual(ERRORS, ["Visit report: names not printed"])


class TestTheWiring(unittest.TestCase):
	def test_it_is_a_registered_jinja_global(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.sapphire_maintenance.visit_report.maintenance_report",', hooks)

	def test_the_report_template_calls_it_once(self):
		import json

		records = json.loads((APP / "fixtures/print_format.json").read_text(encoding="utf-8"))
		html = next(r for r in records if r["name"] == "Maintenance Record Print")["html"]
		self.assertEqual(html.count("{%- set mr = maintenance_report(doc) -%}"), 1)
		for use in (
			"mr.feature_lines",
			"mr.contract_line",
			"mr.features.get(row.serial_no)",
			"mr.charts",
			"mr.fountain_photo",
			# v1.610.0: the technician's signature, with who signed and when. A data URI already,
			# so the session-less finalize PDF prints it without inlining anything.
			'<img src="{{ doc.technician_signature | e }}"',
			"doc.technician_signed_by",
			"frappe.utils.format_datetime(doc.technician_signed_on)",
		):
			with self.subTest(use=use):
				self.assertIn(use, html)

	def test_the_portal_page_shows_both_signatures(self):
		portal = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.html"
		).read_text(encoding="utf-8")
		self.assertIn('<img src="{{ doc.technician_signature | e }}"', portal)
		self.assertIn('<img src="{{ doc.client_sign_off | e }}"', portal)


if __name__ == "__main__":
	unittest.main()
