"""Bench-free tests for the printable rack QR sheet (v1.831.0).

From the 2026-11-19 operations review: the north-facing Atrium shelves are too high and their side
blocks too thin for QR stickers, so each rack gets one printed page listing every shelf's code with
its position (Shelf 1 = top), laminated and hung low beside the rack.

What is held:

* **The rows** (``rack_sheet_rules``): a rack's children in the order a person sees them (``Shelf 2``
  before ``Shelf 10``), a leaf child one shelf with one code, a group child one shelf with a code per
  location left to right; Shelf 1 at the top; the Warehouse Map box's *Shelf 1 is at the* Bottom, *Bin
  1 is at the* Right and own order honored; disabled and Transit locations left off; an empty shelf
  kept, with no code.
* **Which racks.** The group itself, or, when its map box is a Room, every group inside it that holds
  a location; nothing for a location, a disabled group or an unknown name.
* **The sizes.** Codes as large as the page allows up to 1.8 in, never below 0.8 in; a busy shelf
  wraps; a rack that cannot fit says so.
* **The read** (``warehouse_labels.rack_sheets``): the codes are the labels' own URL
  (``<site>/stock-scan?w=<warehouse>``, ``stock_scan_rules.scan_url``) drawn by ``qr_svg``; the map's
  boxes are read once, and a site without the map's doctype prints every group as a rack.
* **The page.** The controller sends a signed-out visitor to log in and back, refuses anyone without
  the labels' roles, reads ``w`` (repeatable); the template renders (Jinja, over a stand-in for
  ``templates/web.html``), escapes every name, and marks only the SVG safe; the controller's filename
  is the one Frappe imports.
* **The door.** The Warehouse form's group buttons include **Rack QR Sheet**, opening
  ``/rack-qr-sheet?w=<name>``; and CI runs this suite.

Run: python -m unittest erpnext_enhancements.tests.test_rack_qr_sheet -v
"""

import importlib
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import rack_sheet_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules
from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as map_rules

APP = REPO_ROOT / "erpnext_enhancements"
WWW = APP / "www"
TEMPLATE = WWW / "rack-qr-sheet.html"
CONTROLLER = WWW / "rack_qr_sheet.py"
FORM_JS = APP / "public" / "js" / "warehouse_stock_scan.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

try:
	import pyqrcode

	HAVE_QR = True
except ImportError:  # pragma: no cover - CI installs it for this step
	HAVE_QR = False

try:
	import jinja2

	HAVE_JINJA = True
except ImportError:  # pragma: no cover
	HAVE_JINJA = False


class _Dict(dict):
	"""frappe._dict: attributes are keys, both ways (a www context is filled by attribute)."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def wh(name, parent, is_group=0, disabled=0, warehouse_type=None, label=None):
	return _Dict(
		name=name,
		warehouse_name=label or name.rsplit(" - ", 1)[0],
		parent_warehouse=parent,
		is_group=is_group,
		disabled=disabled,
		warehouse_type=warehouse_type,
	)


def tree_of(rows):
	return {row["name"]: row for row in rows}


ROOT = "All Warehouses - X"
ATRIUM = "Atrium - X"
NLR1 = "North Large Rack 1 - X"
TOOL = "Tool Rack - X"
BAY = "Bay B1 - X"

ROWS = [
	wh(ROOT, None, 1),
	wh(ATRIUM, ROOT, 1),
	wh(NLR1, ATRIUM, 1),
	# Created out of order on purpose: the tree's own order is creation order.
	wh("Shelf NLR1-2 - X", NLR1),
	wh("Shelf NLR1-1 - X", NLR1),
	wh(TOOL, ATRIUM, 1),
	wh("Shelf TOOL-10 - X", TOOL),
	wh("Shelf TOOL-2 - X", TOOL),
	wh("Shelf TOOL-1 - X", TOOL),
	wh("Shelf TOOL-3 - X", TOOL, disabled=1),
	wh("Empty Rack - X", ATRIUM, 1),
	wh(BAY, ROOT, 1),
	wh("Shelf B1-1 - X", BAY, 1),
	wh("Shelf B1-2 - X", BAY, 1),
	wh("Bin B1-2-10 - X", "Shelf B1-2 - X"),
	wh("Bin B1-2-9 - X", "Shelf B1-2 - X"),
	wh("Bin B1-2-1 - X", "Shelf B1-2 - X"),
	wh("Bin B1-2-T - X", "Shelf B1-2 - X", warehouse_type="Transit"),
	wh("Loose Bin - X", ROOT),
]
TREE = tree_of(ROWS)


class TestRows(unittest.TestCase):
	def test_a_rack_of_shelves_lists_shelf_1_at_the_top(self):
		rows = rules.rack_rows(NLR1, TREE)
		self.assertEqual(
			[(r["shelf"], r["warehouse"], r["position"]) for r in rows],
			[(1, "Shelf NLR1-1 - X", "top"), (2, "Shelf NLR1-2 - X", "bottom")],
		)
		self.assertEqual(rows[0]["locations"], ["Shelf NLR1-1 - X"])
		self.assertEqual(rows[0]["label"], "Shelf NLR1-1")

	def test_numbers_sort_as_numbers_and_a_disabled_shelf_is_left_off(self):
		rows = rules.rack_rows(TOOL, TREE)
		self.assertEqual([r["label"] for r in rows], ["Shelf TOOL-1", "Shelf TOOL-2", "Shelf TOOL-10"])
		self.assertEqual([r["position"] for r in rows], ["top", "", "bottom"])

	def test_a_bay_lists_each_shelf_with_its_bins_left_to_right(self):
		rows = rules.rack_rows(BAY, TREE)
		self.assertEqual([r["label"] for r in rows], ["Shelf B1-1", "Shelf B1-2"])
		# An empty shelf is kept, with no code; a Transit location is never printed.
		self.assertEqual(rows[0]["locations"], [])
		self.assertEqual(rows[1]["locations"], ["Bin B1-2-1 - X", "Bin B1-2-9 - X", "Bin B1-2-10 - X"])

	def test_the_map_box_decides_where_shelf_1_and_bin_1_are(self):
		box = {"shelves_from": rules.BOTTOM, "bins_from": rules.RIGHT, "order": None}
		rows = rules.rack_rows(BAY, TREE, box=box)
		# Rows are still read top to bottom; shelf 1 is now the bottom one.
		self.assertEqual([(r["shelf"], r["position"]) for r in rows], [(2, "top"), (1, "bottom")])
		self.assertEqual(rows[0]["locations"], ["Bin B1-2-10 - X", "Bin B1-2-9 - X", "Bin B1-2-1 - X"])

	def test_the_boxs_own_order_comes_first(self):
		box = {"order": ["Shelf NLR1-2 - X"]}
		rows = rules.rack_rows(NLR1, TREE, box=box)
		self.assertEqual([r["warehouse"] for r in rows], ["Shelf NLR1-2 - X", "Shelf NLR1-1 - X"])
		# Each keeps the number its name ends in, whatever the order.
		self.assertEqual([r["shelf"] for r in rows], [2, 1])

	def test_a_shelf_is_numbered_by_its_name_so_a_disabled_one_leaves_a_gap(self):
		# Shelf TOOL-3 is disabled: the shelf below it is still 10, never renumbered to 3 beside a code
		# captioned "Shelf TOOL-10".
		rows = rules.rack_rows(TOOL, TREE)
		self.assertEqual(
			[(r["shelf"], r["label"]) for r in rows],
			[(1, "Shelf TOOL-1"), (2, "Shelf TOOL-2"), (10, "Shelf TOOL-10")],
		)

	def test_a_name_without_a_number_takes_its_place(self):
		tree = tree_of(
			[
				wh(ROOT, None, 1),
				wh("Odd Rack - X", ROOT, 1),
				wh("Upper Shelf - X", "Odd Rack - X"),
				wh("Shelf OR-2 - X", "Odd Rack - X"),
			]
		)
		rows = rules.rack_rows("Odd Rack - X", tree)
		# "Upper" is a position word, so it sorts first and, with no number of its own, is shelf 1.
		self.assertEqual([(r["shelf"], r["label"]) for r in rows], [(1, "Upper Shelf"), (2, "Shelf OR-2")])
		self.assertEqual(rules.shelf_number("Shelf BR-NR1-4"), 4)
		self.assertEqual(rules.shelf_number("Shelf NLR1-12 "), 12)
		self.assertIsNone(rules.shelf_number("Upper Shelf"))
		self.assertIsNone(rules.shelf_number(None))

	def test_one_shelf_has_no_position_word(self):
		tree = tree_of([wh(ROOT, None, 1), wh("Solo - X", ROOT, 1), wh("Shelf S-1 - X", "Solo - X")])
		self.assertEqual([r["position"] for r in rules.rack_rows("Solo - X", tree)], [""])

	def test_not_a_rack(self):
		for name in ("Loose Bin - X", "Nope - X", "Shelf NLR1-1 - X"):
			with self.subTest(name=name):
				self.assertEqual(rules.rack_rows(name, TREE), [])
		disabled = dict(TREE, **{NLR1: wh(NLR1, ATRIUM, 1, disabled=1)})
		self.assertEqual(rules.rack_rows(NLR1, disabled), [])

	def test_the_words(self):
		self.assertEqual(rules.position_words({"position": "top"}), "Top shelf")
		self.assertEqual(rules.position_words({"position": "bottom"}), "Bottom shelf")
		self.assertEqual(rules.position_words({"position": ""}), "")
		self.assertIn("Shelf 1 is the top shelf", rules.how_to_read())
		self.assertIn("Shelf 1 is the bottom shelf", rules.how_to_read(rules.BOTTOM))


class TestWhichRacks(unittest.TestCase):
	def test_a_rack_is_itself(self):
		self.assertEqual(rules.racks_for(NLR1, TREE, {}), [{"rack": NLR1, "loose": False}])
		self.assertEqual(
			rules.racks_for(NLR1, TREE, {NLR1: {"shape_type": "Rack"}}), [{"rack": NLR1, "loose": False}]
		)

	def test_a_room_on_the_map_prints_each_rack_in_it(self):
		boxes = rules.box_settings([{"warehouse": ATRIUM, "shape_type": "Room"}])
		# The empty rack holds no location, so it gets no page.
		self.assertEqual(
			rules.racks_for(ATRIUM, TREE, boxes),
			[{"rack": NLR1, "loose": False}, {"rack": TOOL, "loose": False}],
		)

	def test_loose_shelves_in_a_room_get_a_page_named_after_the_room(self):
		tree = dict(
			TREE,
			**{
				"Window Shelf - X": wh("Window Shelf - X", ATRIUM),
				"Shelf W-2 - X": wh("Shelf W-2 - X", ATRIUM),
			},
		)
		boxes = rules.box_settings([{"warehouse": ATRIUM, "shape_type": "Room"}])
		pages = rules.racks_for(ATRIUM, tree, boxes)
		self.assertEqual(pages[0], {"rack": ATRIUM, "loose": True})
		self.assertEqual([p["rack"] for p in pages[1:]], [NLR1, TOOL])
		# The room's page lists only the loose shelves, never the racks' shelves again.
		rows = rules.rack_rows(ATRIUM, tree, loose_only=True)
		self.assertEqual([r["warehouse"] for r in rows], ["Shelf W-2 - X", "Window Shelf - X"])
		self.assertTrue(all(r["locations"] == [r["warehouse"]] for r in rows))

	def test_without_a_room_box_a_room_is_one_sheet(self):
		self.assertEqual(rules.racks_for(ATRIUM, TREE, {}), [{"rack": ATRIUM, "loose": False}])

	def test_never_a_location_or_an_unknown_name(self):
		self.assertEqual(rules.racks_for("Loose Bin - X", TREE, {}), [])
		self.assertEqual(rules.racks_for("Nope", TREE, {}), [])

	def test_box_settings(self):
		boxes = rules.box_settings(
			[
				{
					"warehouse": NLR1,
					"shape_type": "Rack",
					"shelves_from": "Bottom",
					"bins_from": "Right",
					"bin_order": "Shelf NLR1-2 - X\nShelf NLR1-1 - X",
				},
				{"warehouse": NLR1, "shape_type": "Room"},
				{"warehouse": TOOL, "shape_type": "Rack", "shelves_from": "", "bins_from": None},
				{"warehouse": None, "shape_type": "Label"},
			]
		)
		self.assertEqual(set(boxes), {NLR1, TOOL})
		self.assertEqual(boxes[NLR1]["shape_type"], "Rack")
		self.assertEqual((boxes[NLR1]["shelves_from"], boxes[NLR1]["bins_from"]), ("Bottom", "Right"))
		self.assertEqual(boxes[NLR1]["order"], ["Shelf NLR1-2 - X", "Shelf NLR1-1 - X"])
		self.assertEqual(
			(boxes[TOOL]["shelves_from"], boxes[TOOL]["bins_from"], boxes[TOOL]["order"]),
			("Top", "Left", None),
		)


def _rows(*counts):
	return [
		{"warehouse": f"S{i}", "locations": [f"B{i}-{j}" for j in range(n)]} for i, n in enumerate(counts)
	]


class TestLayout(unittest.TestCase):
	def test_a_few_shelves_get_the_largest_codes(self):
		# Production's Atrium and Build Room racks have two to four shelves.
		for n in (2, 3):
			with self.subTest(shelves=n):
				rows = [{"warehouse": f"S{i}", "locations": [f"S{i}"]} for i in range(n)]
				got = rules.layout(rows)
				self.assertEqual((got["qr"], got["per_line"], got["fits"]), (rules.MAX_QR, 1, True))
		four = rules.layout([{"warehouse": f"S{i}", "locations": [f"S{i}"]} for i in range(4)])
		self.assertTrue(four["fits"])
		self.assertGreaterEqual(four["qr"], 1.4)

	def test_a_busy_shelf_wraps_and_the_codes_shrink_but_not_below_the_floor(self):
		got = rules.layout(_rows(0, 14, 12, 8, 0))
		self.assertGreaterEqual(got["qr"], rules.MIN_QR)
		self.assertLess(got["qr"], rules.MAX_QR)
		self.assertLessEqual(
			got["per_line"] * got["qr"] + (got["per_line"] - 1) * rules.GAP, rules.CODES_WIDTH + 1e-9
		)
		self.assertGreater(got["lines"], 5)

	def test_too_much_for_one_page_says_so(self):
		got = rules.layout(_rows(*[20] * 8))
		self.assertFalse(got["fits"])
		self.assertEqual(got["qr"], rules.MIN_QR)

	def test_nothing_to_lay_out(self):
		self.assertEqual(rules.layout([])["qr"], rules.MAX_QR)
		self.assertTrue(rules.layout(_rows(0, 0))["fits"])

	def test_the_page_adds_up(self):
		self.assertAlmostEqual(
			rules.CODES_WIDTH + rules.POSITION_WIDTH + 0.25 + 2 * rules.MARGIN, rules.PAGE_WIDTH
		)
		self.assertGreater(rules.ROWS_HEIGHT, 7.5)
		self.assertGreaterEqual(rules.MIN_QR, 0.75, "smaller than a shelf sticker's code")


# --- the read and the page, on a frappe stub ---------------------------------------------------

STATE = {}
_SAVED = {}
STUBBED = ("frappe", "frappe.utils")
OURS = (
	"erpnext_enhancements.inventory_enhancements.warehouse_labels",
	"erpnext_enhancements.www.rack_qr_sheet",
)
labels = None
page = None


class _Thrown(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Redirect(Exception):
	pass


class _Args(dict):
	def getlist(self, key):
		value = self.get(key)
		return list(value) if isinstance(value, list) else ([value] if value else [])


def _install():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe._dict = _Dict
	frappe.PermissionError = _PermissionError
	frappe.Redirect = _Redirect

	def throw(message, exc=_Thrown, *a, **k):
		raise exc(message)

	frappe.throw = throw

	def get_all(doctype, fields=None, filters=None, order_by=None, **kwargs):
		STATE["get_all"].append((doctype, fields, filters, order_by))
		if doctype == "Warehouse":
			return [_Dict({f: row.get(f) for f in fields}) for row in STATE["warehouses"]]
		if doctype == "Warehouse Floor Map Shape":
			if STATE.get("no_map"):
				raise RuntimeError("DocType Warehouse Floor Map Shape not found")
			return [_Dict(row) for row in STATE["shapes"]]
		raise AssertionError(doctype)

	frappe.get_all = get_all
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.session = types.SimpleNamespace(user="tech@example.com")
	frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace(redirect_location=None))
	frappe.request = types.SimpleNamespace(args=_Args(), full_path="/rack-qr-sheet?w=X")
	frappe.form_dict = _Dict()

	utils = types.ModuleType("frappe.utils")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	utils.cint = lambda v: int(float(v or 0))
	utils.nowdate = lambda: "2026-11-19"
	utils.format_date = lambda v=None: "11-19-2026"
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def _reset(**over):
	STATE.clear()
	lft = iter(range(1, 1000))
	STATE.update(
		warehouses=[dict(row, lft=next(lft), rgt=0) for row in ROWS],
		shapes=[],
		roles=["Stock User"],
		get_all=[],
	)
	STATE.update(over)
	frappe = sys.modules["frappe"]
	frappe.session.user = "tech@example.com"
	frappe.local.flags.redirect_location = None
	frappe.request.args = _Args()


def setUpModule():
	global labels, page
	for name in STUBBED + OURS:
		_SAVED[name] = sys.modules.pop(name, None)
	_install()
	labels = importlib.import_module("erpnext_enhancements.inventory_enhancements.warehouse_labels")
	page = importlib.import_module("erpnext_enhancements.www.rack_qr_sheet")


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _SAVED.get(name) is not None:
			sys.modules[name] = _SAVED[name]


@unittest.skipUnless(HAVE_QR, "PyQRCode is not installed (CI installs it for this step)")
class TestTheRead(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_rack_page_with_the_labels_codes(self):
		pages = labels.rack_sheets([NLR1])
		self.assertEqual(len(pages), 1)
		p = pages[0]
		self.assertEqual((p["rack"], p["title"], p["trail"]), (NLR1, "North Large Rack 1", "Atrium"))
		self.assertIn("Shelf 1 is the top shelf", p["how_to_read"])
		self.assertEqual([r["shelf"] for r in p["rows"]], [1, 2])
		first = p["rows"][0]
		self.assertEqual(
			(first["label"], first["position_words"], first["is_location"]),
			("Shelf NLR1-1", "Top shelf", True),
		)
		code = first["codes"][0]
		self.assertEqual(
			code["url"], stock_scan_rules.scan_url("https://erp.example.com", "Shelf NLR1-1 - X")
		)
		self.assertEqual(code["url"], "https://erp.example.com/stock-scan?w=Shelf%20NLR1-1%20-%20X")
		self.assertTrue(code["svg"].startswith("<svg"))
		self.assertIn("<title>Shelf NLR1-1</title>", code["svg"])
		self.assertEqual(p["layout"]["qr"], rules.MAX_QR)

	def test_a_room_on_the_map_prints_a_page_per_rack(self):
		_reset(
			shapes=[
				{
					"warehouse": ATRIUM,
					"shape_type": "Room",
					"shelves_from": "Top",
					"bins_from": "Left",
					"bin_order": "",
				}
			]
		)
		pages = labels.rack_sheets([ATRIUM])
		self.assertEqual([p["rack"] for p in pages], [NLR1, TOOL])
		reads = [c for c in STATE["get_all"] if c[0] == "Warehouse Floor Map Shape"]
		self.assertEqual(len(reads), 1)
		self.assertEqual(reads[0][2], {"parenttype": "Warehouse Floor Map"})

	def test_the_map_box_turns_the_rows(self):
		_reset(
			shapes=[
				{
					"warehouse": BAY,
					"shape_type": "Rack",
					"shelves_from": "Bottom",
					"bins_from": "Left",
					"bin_order": "",
				}
			]
		)
		p = labels.rack_sheets([BAY])[0]
		self.assertEqual([r["shelf"] for r in p["rows"]], [2, 1])
		self.assertIn("Shelf 1 is the bottom shelf", p["how_to_read"])
		self.assertFalse(p["rows"][0]["is_location"])
		self.assertEqual(
			[c["title"] for c in p["rows"][0]["codes"]], ["Bin B1-2-1", "Bin B1-2-9", "Bin B1-2-10"]
		)
		self.assertEqual(p["rows"][1]["codes"], [])

	def test_loose_shelves_in_a_room_get_their_own_page(self):
		_reset(shapes=[{"warehouse": ATRIUM, "shape_type": "Room"}])
		STATE["warehouses"].append(dict(wh("Window Shelf - X", ATRIUM), lft=0, rgt=0))
		pages = labels.rack_sheets([ATRIUM])
		self.assertEqual(
			[(p["rack"], p["loose"]) for p in pages], [(ATRIUM, True), (NLR1, False), (TOOL, False)]
		)
		self.assertEqual(pages[0]["title"], "Atrium")
		self.assertEqual([c["title"] for r in pages[0]["rows"] for c in r["codes"]], ["Window Shelf"])

	def test_without_the_map_every_group_is_a_rack(self):
		_reset(no_map=True)
		self.assertEqual([p["rack"] for p in labels.rack_sheets([ATRIUM])], [ATRIUM])

	def test_skips_what_is_not_a_rack_and_never_prints_one_twice(self):
		_reset(shapes=[{"warehouse": ATRIUM, "shape_type": "Room"}])
		pages = labels.rack_sheets(["Loose Bin - X", "Nope - X", NLR1, ATRIUM, NLR1])
		self.assertEqual([p["rack"] for p in pages], [NLR1, TOOL])
		self.assertEqual(labels.rack_sheets([]), [])


@unittest.skipUnless(HAVE_QR, "PyQRCode is not installed (CI installs it for this step)")
class TestTheController(unittest.TestCase):
	def setUp(self):
		_reset()

	def context(self):
		return page.get_context(_Dict())

	def test_a_signed_out_visitor_logs_in_and_comes_back(self):
		frappe = sys.modules["frappe"]
		frappe.session.user = "Guest"
		frappe.request.full_path = f"/rack-qr-sheet?w={NLR1}"
		with self.assertRaises(_Redirect):
			self.context()
		self.assertEqual(
			frappe.local.flags.redirect_location, stock_scan_rules.login_redirect(f"/rack-qr-sheet?w={NLR1}")
		)

	def test_the_labels_roles_only(self):
		_reset(roles=["Sales User"])
		with self.assertRaises(_PermissionError):
			self.context()
		for role in stock_scan_rules.LABEL_ROLES:
			with self.subTest(role=role):
				_reset(roles=[role])
				self.context()

	def test_reads_every_w(self):
		sys.modules["frappe"].request.args = _Args(w=[NLR1, " ", TOOL])
		ctx = self.context()
		self.assertEqual(ctx.names, [NLR1, TOOL])
		self.assertEqual([p["rack"] for p in ctx.pages], [NLR1, TOOL])
		self.assertEqual(ctx.no_cache, 1)
		self.assertTrue(ctx.groups)
		self.assertEqual(ctx.content_width, 7.7)

	def test_nothing_chosen_reads_no_racks(self):
		ctx = self.context()
		self.assertEqual((ctx.names, ctx.pages), ([], []))
		self.assertFalse(any(c[0] == "Warehouse Floor Map Shape" for c in STATE["get_all"]))

	def test_no_cache_and_the_filename(self):
		self.assertEqual(page.no_cache, 1)
		self.assertTrue(CONTROLLER.exists())
		self.assertNotIn("-", CONTROLLER.stem)
		self.assertEqual(CONTROLLER.stem, TEMPLATE.stem.replace("-", "_"))


WEB_HTML = """<html><head>{% block head_include %}{% endblock %}{% block style %}{% endblock %}</head>
<body>{% block banner %}{% endblock %}{% block navbar %}{% endblock %}{% block page_content %}{% endblock %}
{% block footer %}{% endblock %}{% block script %}{% endblock %}<title>{% block title %}{% endblock %}</title></body></html>"""


@unittest.skipUnless(
	HAVE_QR and HAVE_JINJA, "PyQRCode and Jinja2 are needed (CI installs both for this step)"
)
class TestTheTemplate(unittest.TestCase):
	def render(self, ctx):
		env = jinja2.Environment(
			loader=jinja2.DictLoader(
				{"templates/web.html": WEB_HTML, "rack-qr-sheet.html": TEMPLATE.read_text(encoding="utf-8")}
			),
			autoescape=False,
		)
		return env.get_template("rack-qr-sheet.html").render(**ctx)

	def setUp(self):
		_reset(shapes=[{"warehouse": ATRIUM, "shape_type": "Room"}])

	def test_renders_a_page_per_rack_with_every_shelf(self):
		sys.modules["frappe"].request.args = _Args(w=[ATRIUM])
		html = self.render(page.get_context(_Dict()))
		self.assertEqual(html.count('class="rq-page"'), 2)
		self.assertEqual(html.count('class="rq-row"'), 5)
		self.assertIn("North Large Rack 1", html)
		self.assertIn("Top shelf", html)
		self.assertIn("Bottom shelf", html)
		self.assertEqual(html.count("<svg"), 5)
		self.assertIn("--rq-qr: 1.8in", html)
		self.assertIn("Print 2 pages", html)
		self.assertIn("@page { size: 8.5in 11.0in; margin: 0.4in; }", html)
		# The header and footer are a table's thead and tfoot, which print on every page the rack takes.
		self.assertEqual(html.count("<thead>"), 2)
		self.assertEqual(html.count("<tfoot>"), 2)
		self.assertLess(html.index("<thead>"), html.index('class="rq-title"'))
		self.assertNotIn('class="rq-overflow"', html)

	def test_a_rack_too_long_for_one_sheet_says_so_and_repeats_its_header(self):
		rows = [wh(ROOT, None, 1), wh("Long Rack - X", ROOT, 1)]
		for shelf in range(1, 9):
			rows.append(wh(f"Shelf L-{shelf} - X", "Long Rack - X", 1))
			rows.extend(wh(f"Bin L-{shelf}-{b} - X", f"Shelf L-{shelf} - X") for b in range(1, 21))
		_reset(warehouses=[dict(r, lft=0, rgt=0) for r in rows])
		sys.modules["frappe"].request.args = _Args(w=["Long Rack - X"])
		ctx = page.get_context(_Dict())
		self.assertFalse(ctx.pages[0]["layout"]["fits"])
		html = self.render(ctx)
		self.assertIn('class="rq-overflow"', html)
		self.assertEqual(html.count("<thead>"), 1)

	def test_the_loose_page_says_what_it_is(self):
		_reset(shapes=[{"warehouse": ATRIUM, "shape_type": "Room"}])
		STATE["warehouses"].append(dict(wh("Window Shelf - X", ATRIUM), lft=0, rgt=0))
		sys.modules["frappe"].request.args = _Args(w=[ATRIUM])
		html = self.render(page.get_context(_Dict()))
		self.assertEqual(html.count('class="rq-page"'), 3)
		self.assertIn("Shelves in the room, not on a rack", html)

	def test_names_are_escaped(self):
		_reset(
			warehouses=[dict(row, lft=0, rgt=0) for row in ROWS]
			+ [
				dict(wh("Rack <b> - X", ROOT, 1, label="Rack <b>"), lft=0, rgt=0),
				dict(wh("Shelf <i>1 - X", "Rack <b> - X", label="Shelf <i>1"), lft=0, rgt=0),
			]
		)
		sys.modules["frappe"].request.args = _Args(w=["Rack <b> - X"])
		html = self.render(page.get_context(_Dict()))
		self.assertNotIn("<b>", html)
		self.assertNotIn("<i>", html)
		self.assertIn("Rack &lt;b&gt;", html)

	def test_nothing_chosen(self):
		html = self.render(page.get_context(_Dict()))
		self.assertIn("Choose a rack above", html)
		self.assertNotIn('class="rq-page"', html)

	def test_only_the_svg_is_marked_safe(self):
		src = TEMPLATE.read_text(encoding="utf-8")
		self.assertEqual(re.findall(r"\{\{\s*([\w.]+)\s*\|\s*safe\s*\}\}", src), ["c.svg"])
		self.assertIn('{% extends "templates/web.html" %}', src)
		# The page's own footer must not be hidden by the rule that hides the website's.
		self.assertNotRegex(src, r"(?<![\w.-])footer\s*[,{]")


class TestTheDoor(unittest.TestCase):
	def test_the_warehouse_form_offers_it_on_a_group(self):
		js = FORM_JS.read_text(encoding="utf-8")
		group = js[js.index("if (frm.doc.is_group)") : js.index("return;", js.index("if (frm.doc.is_group)"))]
		self.assertIn('__("Rack QR Sheet")', group)
		self.assertIn("window.open(`/rack-qr-sheet?w=${name}`", group)

	def test_ci_runs_this_suite_with_its_libraries(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_rack_qr_sheet -v", ci)
		step = ci[ci.index("Rack QR sheet") : ci.index("test_rack_qr_sheet")]
		self.assertIn("PyQRCode", step)
		self.assertIn("Jinja2", step)


if __name__ == "__main__":
	unittest.main()
