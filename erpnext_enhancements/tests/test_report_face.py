"""Bench-free tests for the report face (form kit P3.3), run under node.

``public/js/report_face/cells.js`` decides, in pure functions, what a script report's cell shows
for a phone, a party, a pillar, money and a date; ``report_face.js`` wires it to the Desk as
``erpnext_enhancements.report_face``. Both run here under node: the pure functions directly, and
the Desk half against a fake ``frappe`` with only what it calls (``provide``,
``utils.get_form_link``, ``model.can_read``) and a default formatter that records its column.

The modules and their imports (``normalize/normalize.js``, ``sf_kit/esc.js``) are copied to a temp
tree beside a ``package.json`` that says ``"type": "module"``, the way ``test_desk_display`` runs
``desk_display.js``. Skips the node half cleanly when ``node`` is not on PATH.

Also held, statically: the chip's inline declarations equal sf_kit's ``.sf-chip`` rules, every
token they read is defined on every Desk page, ``STREAM_PILLAR`` equals ``print_style``'s, the
global bundle imports the face and not the kit, and nothing in it builds a route by hand.

The scan of the reports themselves is ``test_report_hand_formatting``.

Run: python -m unittest erpnext_enhancements.tests.test_report_face -v
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import normalize as n

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FACE_DIR = JS / "report_face"
CELLS = FACE_DIR / "cells.js"
FACE = FACE_DIR / "report_face.js"
NORMALIZE_JS = JS / "normalize" / "normalize.js"
ESC_JS = JS / "sf_kit" / "esc.js"
BUNDLE = JS / "erpnext_enhancements.bundle.js"
KIT_CSS = APP / "public" / "css" / "sf_kit.bundle.css"
TOKENS_CSS = APP / "public" / "css" / "sf_tokens.bundle.css"
PRINT_STYLE = APP / "print_style.py"

DATES = ["2026-10-14", "2026-10-14 14:05:00", "2026-10-14 00:30:00.123", "2026-02-30", "not a date", ""]
TIMES = ["14:05:00", "09:00", "0:00:00", "garbage"]
PHONES = ["+18015550100", "+18015550100,12", "801.555.0100", "+442079460958", "call Bob <b>", ""]

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [cellsFile, faceFile, inputsJson] = process.argv.slice(2);
const c = await import(pathToFileURL(cellsFile).href);
const face_mod = await import(pathToFileURL(faceFile).href);
const inputs = JSON.parse(inputsJson);
const out = {};

out.kinds = c.KINDS;
out.kind = {
	phone_marker: c.face_kind({ fieldtype: "Data", sf_face: "phone" }),
	date_type: c.face_kind({ fieldtype: "Date" }),
	datetime_type: c.face_kind({ fieldtype: "Datetime" }),
	time_type: c.face_kind({ fieldtype: "Time" }),
	currency_type: c.face_kind({ fieldtype: "Currency" }),
	phone_type: c.face_kind({ fieldtype: "Phone" }),
	data_type: c.face_kind({ fieldtype: "Data" }),
	link_type: c.face_kind({ fieldtype: "Link", options: "Customer" }),
	marker_wins: c.face_kind({ fieldtype: "Link", options: "Customer", sf_face: "party" }),
	opt_out: c.face_kind({ fieldtype: "Date", sf_face: "none" }),
	opt_out_false: c.face_kind({ fieldtype: "Currency", sf_face: false }),
	typo: c.face_kind({ fieldtype: "Date", sf_face: "dates" }),
	no_column: c.face_kind(undefined),
	inherited_key: c.face_kind({ fieldtype: "toString" }),
};

out.phone = Object.fromEntries(inputs.phones.map((p) => [p, c.phone_text(p)]));
out.phone_null = c.phone_text(null);
out.date = Object.fromEntries(inputs.dates.map((d) => [d, c.date_text(d)]));
out.datetime = Object.fromEntries(inputs.dates.map((d) => [d, c.datetime_text(d)]));
out.time = Object.fromEntries(inputs.times.map((t) => [t, c.time_text(t)]));
out.date_escaped = c.date_text("<b>soon</b>");

out.pillar_for = Object.fromEntries(Object.keys(c.STREAM_PILLAR).map((s) => [s, c.pillar_for(s)]));
out.pillar_unknown = c.pillar_for("Unallocated");
out.pillar_inherited = c.pillar_for("constructor");
out.streams = c.streams_of("Build, Service,Build\nEvents, ,");
out.pillar_html = {
	two: c.pillar_html("Build, Service"),
	unallocated: c.pillar_html("Unallocated"),
	mixed: c.pillar_html("Events, Consulting"),
	empty: c.pillar_html(""),
	none: c.pillar_html(null),
	escaped: c.pillar_html("<i>x</i>"),
};
out.chip = {
	build: c.chip_html("Build", "build"),
	unknown_tone: c.chip_html("Hi", "magenta"),
	blank: c.chip_html("   ", "build"),
	nothing: c.chip_html(null, "build"),
	escaped: c.chip_html("<b>", "danger"),
	styles: Object.fromEntries(c.CHIP_TONES.map((t) => [t, c.chip_style(t)])),
};
out.tables = { base: c.CHIP_BASE, tones: c.CHIP_TONE_RULES, all: c.CHIP_TONES };

const link = { fieldtype: "Link", options: "Customer", sf_face: "party", sf_label: "customer_name" };
const dyn = { fieldtype: "Dynamic Link", options: "party_type", sf_face: "party", sf_label: "party_label" };
// The other shape: the cell holds the name; the row carries the docname and doctype.
const named = { fieldtype: "Data", sf_face: "party", sf_name: "party_id", sf_doctype_field: "party_type" };
out.party_of = {
	link: c.party_of("CUST-1", link, { customer_name: "Acme Fountains" }),
	link_no_label: c.party_of("CUST-1", link, {}),
	dynamic: c.party_of("LEAD-9", dyn, { party_type: "Lead", party_label: "Jo Smith" }),
	dynamic_no_type: c.party_of("LEAD-9", dyn, { party_label: "Jo Smith" }),
	none: c.party_of("", dyn, { party_label: "—" }),
	no_data: c.party_of("X", link, undefined),
	data_column: c.party_of("Acme Fountains", named, { party_id: "CUST-1", party_type: "Customer" }),
	data_fixed_doctype: c.party_of("Acme", { fieldtype: "Data", sf_face: "party", sf_name: "cust", sf_doctype: "Customer" }, { cust: "CUST-1" }),
	data_no_docname: c.party_of("Acme Fountains", named, { party_id: "", party_type: "Opportunity" }),
	data_no_label: c.party_of("", named, { party_id: "CUST-1", party_type: "Customer" }),
};
out.party_html = {
	link: c.party_html({ doctype: "Customer", name: "CUST-1", label: "Acme", href: "/desk/customer/CUST-1" }),
	no_href: c.party_html({ doctype: "Customer", name: "CUST-1", label: "Acme", href: "" }),
	no_name: c.party_html({ doctype: "Customer", name: "", label: "—", href: "/desk/customer/" }),
	new_tab: c.party_html({ doctype: "Project", name: "P-1", label: "P-1", href: "/desk/project/P-1", new_tab: true }),
	escaped: c.party_html({ doctype: "Customer", name: 'A"B', label: "<A&B>", href: '/desk/customer/A"B' }),
	label_falls_back: c.party_html({ doctype: "Customer", name: "CUST-1", label: "", href: "/x" }),
};

const calls = [];
const env = {
	fallback: () => "FALLBACK",
	money: () => "MONEY",
	form_link: (doctype, name) => {
		calls.push([doctype, name]);
		return `/desk/${doctype.toLowerCase()}/${name}`;
	},
};
const row = { customer_name: "Acme", party_type: "Lead", party_label: "Jo" };
// A stand-in for convert_to_user_tz: system time 14:05 is 08:05 for the person.
const shift = (v) => v.replace(/ 14:05/, " 08:05");
out.tz = {
	converted: c.user_datetime("2026-10-14 14:05:00", shift),
	t_separator: c.user_datetime("2026-10-14T14:05:00", (v) => "2026-10-14 08:05:00"),
	date_only_untouched: c.user_datetime("2026-10-14", () => "WRONG"),
	invalid_answer_ignored: c.user_datetime("2026-10-14 14:05:00", () => "Invalid date"),
	not_a_string: c.user_datetime(null, () => "WRONG"),
	no_converter: c.user_datetime("2026-10-14 14:05:00"),
	text: c.datetime_text("2026-10-14 14:05:00", shift),
};
out.chip_cached = c.chip_style("build") === c.chip_style("build") && c.chip_style("magenta") === c.chip_style("neutral");
out.cell = {
	date: c.format_cell("2026-10-14", { fieldtype: "Date" }, row, env),
	datetime: c.format_cell("2026-10-14 14:05:00", { fieldtype: "Datetime" }, row, env),
	time: c.format_cell("14:05:00", { fieldtype: "Time" }, row, env),
	phone: c.format_cell("+18015550100", { fieldtype: "Data", sf_face: "phone" }, row, env),
	money: c.format_cell(12, { fieldtype: "Currency" }, row, env),
	pillar: c.format_cell("Design", { fieldtype: "Data", sf_face: "pillar" }, row, env),
	party: c.format_cell("CUST-1", link, row, env),
	party_dynamic: c.format_cell("LEAD-9", dyn, row, env),
	party_none: c.format_cell("", dyn, { party_label: "—" }, env),
	party_data: c.format_cell("Acme Fountains", named, { party_id: "CUST-9", party_type: "Customer" }, env),
	party_data_unlinked: c.format_cell("Acme Fountains", named, { party_id: "", party_type: "Lead" }, env),
	datetime_no_tz: c.format_cell("2026-10-14 14:05:00", { fieldtype: "Datetime" }, row, env),
	datetime_tz: c.format_cell("2026-10-14 14:05:00", { fieldtype: "Datetime" }, row, { ...env, to_user_tz: shift }),
	plain: c.format_cell("x", { fieldtype: "Data" }, row, env),
	total_row: c.format_cell("2026-10-14", { fieldtype: "Date" }, undefined, env),
	opt_out: c.format_cell("2026-10-14", { fieldtype: "Date", sf_face: "none" }, row, env),
};
out.form_link_calls = calls;

// ---- the Desk half
const warnings = [];
const tz_calls = [];
console.warn = (...args) => warnings.push(args.map(String).join(" "));
const readable = new Set(["Customer", "Lead", "Project"]);
const routes = [];
const fakeFrappe = {
	provide(path) {
		let o = globalThis;
		for (const part of path.split(".")) o = o[part] = o[part] || {};
	},
	utils: {
		get_form_link(doctype, name) {
			routes.push([doctype, name]);
			return `/desk/${encodeURIComponent(doctype.toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(name)}`;
		},
	},
	model: { can_read: (doctype) => readable.has(doctype) },
	datetime: {
		convert_to_user_tz(value) {
			tz_calls.push(value);
			if (value === "2026-01-01 00:00:00") throw new Error("no moment-timezone");
			return value.replace(/ 14:05/, " 08:05");
		},
	},
};
const fakeWindow = globalThis;
fakeWindow.format_currency = (v, cur) => `${cur || "$"}${Number(v).toFixed(2)}`;
out.no_frappe = face_mod.install({}, fakeWindow);
out.installed = face_mod.install(fakeFrappe, fakeWindow);
const f = globalThis.erpnext_enhancements.report_face;
out.api = Object.keys(f).sort();

const seen = [];
const default_formatter = (value, r, column, data) => {
	seen.push({ fieldtype: column.fieldtype, fieldname: column.fieldname, value });
	return `DEFAULT(${column.fieldtype}:${value})`;
};
const amount = { fieldname: "amount", fieldtype: "Currency", options: "currency", width: 120 };
out.desk_tz = {
	cell: f.format("2026-10-14 14:05:00", 0, { fieldname: "at", fieldtype: "Datetime" }, { at: 1 }, default_formatter),
	helper: f.datetime("2026-10-14 14:05:00"),
	date_not_converted: f.date("2026-10-14"),
	converter_throws: f.datetime("2026-01-01 00:00:00"),
	calls: tz_calls.slice(),
};
out.desk = {
	money: f.format(1500, 0, amount, { currency: "USD" }, default_formatter),
	money_marker: f.format("99", 0, { fieldname: "x", fieldtype: "Data", sf_face: "money" }, { a: 1 }, default_formatter),
	column_untouched: amount.fieldtype,
	date: f.format("2026-10-14", 0, { fieldname: "d", fieldtype: "Date" }, { d: 1 }, default_formatter),
	plain: f.format("hi", 0, { fieldname: "t", fieldtype: "Data" }, { t: 1 }, default_formatter),
	party: f.format("CUST-1", 0, link, { customer_name: "Acme" }, default_formatter),
	party_unreadable: f.format("SUP-1", 0, { fieldtype: "Link", options: "Supplier", sf_face: "party", sf_label: "n" }, { n: "Pumps Inc" }, default_formatter),
	total: f.format(null, 0, { fieldname: "d", fieldtype: "Date" }, undefined, default_formatter),
	helpers: {
		phone: f.phone("+18015550100"),
		date: f.date("2026-10-14"),
		datetime: f.datetime("2026-10-14 14:05"),
		time: f.time("14:05"),
		money: f.money(12.5, "USD"),
		money_blank: f.money(null),
		party: f.party("Project", "PRJ-1", "PRJ-1", { new_tab: true }),
		party_unreadable: f.party("Supplier", "SUP-1", "Pumps Inc"),
		pillar: f.pillar("Service"),
		chip: f.chip("Overdue", "danger"),
	},
};
out.default_seen = seen;
out.routes = routes;

// Fails open: a column whose marker read throws gets the default formatter, and one warning.
const hostile = { fieldname: "h", fieldtype: "Date" };
Object.defineProperty(hostile, "sf_face", { get() { throw new Error("boom"); } });
out.fail_open = [
	f.format("2026-10-14", 0, hostile, { h: 1 }, default_formatter),
	f.format("2026-10-14", 0, hostile, { h: 1 }, default_formatter),
];
out.warnings = warnings.length;

// Installing twice keeps one namespace object and replaces nothing a report captured.
const before = globalThis.erpnext_enhancements.report_face;
face_mod.install(fakeFrappe, fakeWindow);
out.same_namespace = before === globalThis.erpnext_enhancements.report_face;

process.stdout.write(JSON.stringify(out));
"""


def _run_node():
	node = shutil.which("node")
	if not node:
		raise unittest.SkipTest("node is not on PATH")
	with tempfile.TemporaryDirectory() as tmp:
		root = Path(tmp)
		(root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
		for source, rel in (
			(CELLS, "report_face/cells.js"),
			(FACE, "report_face/report_face.js"),
			(NORMALIZE_JS, "normalize/normalize.js"),
			(ESC_JS, "sf_kit/esc.js"),
		):
			target = root / rel
			target.parent.mkdir(parents=True, exist_ok=True)
			shutil.copy(source, target)
		(root / "runner.mjs").write_text(RUNNER, encoding="utf-8")
		inputs = {"phones": PHONES, "dates": DATES, "times": TIMES}
		proc = subprocess.run(
			[
				node,
				str(root / "runner.mjs"),
				str(root / "report_face/cells.js"),
				str(root / "report_face/report_face.js"),
				json.dumps(inputs),
			],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
	if proc.returncode != 0:
		raise AssertionError(f"node failed:\n{proc.stderr}")
	return json.loads(proc.stdout)


_CACHE = {}


def _node_results():
	"""One node run for the whole module; every TestCase reads the same results."""
	if "out" not in _CACHE:
		_CACHE["out"] = _run_node()
	return _CACHE["out"]


def _esc(text):
	return (
		text.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace('"', "&quot;")
		.replace("'", "&#39;")
		.replace("`", "&#96;")
	)


def _css_rules(path):
	"""{selector: [(property, value)]} for every simple rule block in a stylesheet."""
	css = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	out = {}
	for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
		decls = []
		for part in body.split(";"):
			if ":" in part:
				prop, value = part.split(":", 1)
				decls.append((prop.strip(), " ".join(value.split())))
		out.setdefault(selector.strip(), decls)
	return out


class PureCellsTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _node_results()

	def test_the_kinds(self):
		self.assertEqual(self.out["kinds"], ["phone", "party", "pillar", "money", "date", "datetime", "time"])

	def test_a_column_is_its_marker_or_else_its_fieldtype(self):
		k = self.out["kind"]
		self.assertEqual(k["phone_marker"], "phone")
		self.assertEqual(k["date_type"], "date")
		self.assertEqual(k["datetime_type"], "datetime")
		self.assertEqual(k["time_type"], "time")
		self.assertEqual(k["currency_type"], "money")
		self.assertEqual(k["phone_type"], "phone")
		self.assertEqual(k["marker_wins"], "party")
		for left_alone in (
			"data_type",
			"link_type",
			"opt_out",
			"opt_out_false",
			"typo",
			"no_column",
			"inherited_key",
		):
			with self.subTest(case=left_alone):
				self.assertIsNone(k[left_alone])

	def test_phones_and_dates_are_the_twins_words_escaped(self):
		"""The same answer the server gives (utils/normalize.py), so a report and a printed page agree."""
		for p in PHONES:
			with self.subTest(phone=p):
				self.assertEqual(self.out["phone"][p], _esc(n.phone_display(p)))
		self.assertEqual(self.out["phone"]["+18015550100,12"], "(801) 555-0100 x12")
		self.assertEqual(self.out["phone"]["call Bob <b>"], "call Bob &lt;b&gt;")
		self.assertEqual(self.out["phone_null"], "")
		for d in DATES:
			with self.subTest(date=d):
				self.assertEqual(self.out["date"][d], _esc(n.format_date(d)))
				self.assertEqual(self.out["datetime"][d], _esc(n.format_datetime(d)))
		for t in TIMES:
			with self.subTest(time=t):
				self.assertEqual(self.out["time"][t], _esc(n.format_time(t)))
		self.assertEqual(self.out["date"]["2026-10-14"], "10/14/2026")
		self.assertEqual(self.out["datetime"]["2026-10-14 14:05:00"], "10/14/2026 2:05 PM")
		self.assertEqual(self.out["date_escaped"], "&lt;b&gt;soon&lt;/b&gt;")

	def test_pillars(self):
		self.assertEqual(
			self.out["pillar_for"],
			{
				"Design": "design",
				"Build": "build",
				"Products": "build",
				"Delivery": "build",
				"Service": "service",
				"Events": "rent",
			},
		)
		self.assertIsNone(self.out["pillar_unknown"])
		self.assertIsNone(self.out["pillar_inherited"])
		self.assertEqual(self.out["streams"], ["Build", "Service", "Events"])
		html = self.out["pillar_html"]
		self.assertEqual(html["two"].count("<span"), 2)
		self.assertIn("sf-report-chip--build", html["two"])
		self.assertIn("sf-report-chip--service", html["two"])
		self.assertIn(">Build</span> <span", html["two"])
		self.assertEqual(html["unallocated"], "Unallocated", "a word that is not a stream is not a chip")
		self.assertIn("sf-report-chip--rent", html["mixed"])
		self.assertTrue(html["mixed"].endswith(" Consulting"))
		self.assertEqual(html["empty"], "")
		self.assertEqual(html["none"], "")
		self.assertEqual(html["escaped"], "&lt;i&gt;x&lt;/i&gt;")

	def test_chips(self):
		chip = self.out["chip"]
		self.assertTrue(
			chip["build"].startswith('<span class="sf-report-chip sf-report-chip--build" style="')
		)
		self.assertTrue(chip["build"].endswith('">Build</span>'))
		self.assertIn("sf-report-chip--neutral", chip["unknown_tone"])
		self.assertEqual(chip["blank"], "")
		self.assertEqual(chip["nothing"], "")
		self.assertIn(">&lt;b&gt;</span>", chip["escaped"])
		for tone, style in chip["styles"].items():
			with self.subTest(tone=tone):
				self.assertNotIn('"', style, "the style sits in a double-quoted attribute")
				self.assertIn("var(--sf-radius-pill)", style)

	def test_party_of_reads_the_link_the_way_frappe_does(self):
		p = self.out["party_of"]
		self.assertEqual(p["link"], {"doctype": "Customer", "name": "CUST-1", "label": "Acme Fountains"})
		self.assertEqual(p["link_no_label"]["label"], "CUST-1")
		self.assertEqual(p["dynamic"], {"doctype": "Lead", "name": "LEAD-9", "label": "Jo Smith"})
		self.assertEqual(p["dynamic_no_type"]["doctype"], "")
		self.assertEqual(p["none"], {"doctype": "", "name": "", "label": "—"})
		self.assertEqual(p["no_data"]["label"], "X")

	def test_a_data_party_column_holds_the_name_and_links_from_the_row(self):
		"""The shape that keeps export, sort and search on the name people know."""
		p = self.out["party_of"]
		self.assertEqual(
			p["data_column"], {"doctype": "Customer", "name": "CUST-1", "label": "Acme Fountains"}
		)
		self.assertEqual(p["data_fixed_doctype"], {"doctype": "Customer", "name": "CUST-1", "label": "Acme"})
		# An Opportunity with no party_name but a customer_name: still the name, unlinked.
		self.assertEqual(p["data_no_docname"]["label"], "Acme Fountains")
		self.assertEqual(p["data_no_docname"]["name"], "")
		self.assertEqual(p["data_no_label"]["label"], "CUST-1")
		c = self.out["cell"]
		self.assertIn('href="/desk/customer/CUST-9"', c["party_data"])
		self.assertIn(">Acme Fountains</a>", c["party_data"])
		self.assertEqual(c["party_data_unlinked"], "Acme Fountains")

	def test_a_datetime_is_moved_into_the_persons_time_zone_first(self):
		tz = self.out["tz"]
		self.assertEqual(tz["converted"], "2026-10-14 08:05:00")
		self.assertEqual(tz["t_separator"], "2026-10-14 08:05:00")
		self.assertEqual(tz["date_only_untouched"], "2026-10-14")
		self.assertEqual(tz["invalid_answer_ignored"], "2026-10-14 14:05:00")
		self.assertIsNone(tz["not_a_string"])
		self.assertEqual(tz["no_converter"], "2026-10-14 14:05:00")
		self.assertEqual(tz["text"], "10/14/2026 8:05 AM")
		self.assertEqual(self.out["cell"]["datetime_no_tz"], "10/14/2026 2:05 PM")
		self.assertEqual(self.out["cell"]["datetime_tz"], "10/14/2026 8:05 AM")

	def test_chip_styles_are_built_once(self):
		self.assertTrue(self.out["chip_cached"])

	def test_party_html(self):
		h = self.out["party_html"]
		self.assertEqual(
			h["link"],
			'<a href="/desk/customer/CUST-1" data-doctype="Customer" data-name="CUST-1" data-value="CUST-1">Acme</a>',
		)
		self.assertEqual(h["no_href"], "Acme")
		self.assertEqual(h["no_name"], "—")
		self.assertIn(' target="_blank" rel="noopener">', h["new_tab"])
		self.assertIn('href="/desk/customer/A&quot;B"', h["escaped"])
		self.assertIn(">&lt;A&amp;B&gt;</a>", h["escaped"])
		self.assertIn(">CUST-1</a>", h["label_falls_back"])

	def test_the_dispatcher(self):
		c = self.out["cell"]
		self.assertEqual(c["date"], "10/14/2026")
		self.assertEqual(c["datetime"], "10/14/2026 2:05 PM")
		self.assertEqual(c["time"], "2:05 PM")
		self.assertEqual(c["phone"], "(801) 555-0100")
		self.assertEqual(c["money"], "MONEY")
		self.assertIn("sf-report-chip--design", c["pillar"])
		self.assertIn('href="/desk/customer/CUST-1"', c["party"])
		self.assertIn(">Acme</a>", c["party"])
		self.assertIn('href="/desk/lead/LEAD-9"', c["party_dynamic"])
		self.assertEqual(c["party_none"], "—")
		self.assertEqual(c["plain"], "FALLBACK")
		self.assertEqual(c["total_row"], "FALLBACK", "the total row is Frappe's to draw")
		self.assertEqual(c["opt_out"], "FALLBACK")
		self.assertEqual(
			self.out["form_link_calls"], [["Customer", "CUST-1"], ["Lead", "LEAD-9"], ["Customer", "CUST-9"]]
		)


class DeskHalfTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _node_results()

	def test_it_installs_the_namespace(self):
		self.assertFalse(self.out["no_frappe"])
		self.assertTrue(self.out["installed"])
		self.assertEqual(
			self.out["api"],
			sorted(
				["format", "kind", "phone", "date", "datetime", "time", "pillar", "chip", "money", "party"]
			),
		)
		self.assertTrue(self.out["same_namespace"])

	def test_money_is_frappes_own_currency_formatting(self):
		d = self.out["desk"]
		self.assertEqual(d["money"], "DEFAULT(Currency:1500)")
		self.assertEqual(
			d["money_marker"], "DEFAULT(Currency:99)", "a Data column marked money is read as Currency"
		)
		self.assertEqual(d["column_untouched"], "Currency")
		self.assertEqual(d["helpers"]["money"], "USD12.50")
		self.assertEqual(d["helpers"]["money_blank"], "")

	def test_everything_else_goes_through_the_face_or_untouched_to_frappe(self):
		d = self.out["desk"]
		self.assertEqual(d["date"], "10/14/2026")
		self.assertEqual(d["plain"], "DEFAULT(Data:hi)")
		self.assertEqual(d["total"], "DEFAULT(Date:null)")
		self.assertIn('href="/desk/customer/CUST-1"', d["party"])
		self.assertIn(">Acme</a>", d["party"])

	def test_links_come_from_get_form_link_and_respect_read_permission(self):
		d = self.out["desk"]
		self.assertEqual(
			d["party_unreadable"], "Pumps Inc", "no read on Supplier: the name, unlinked, as Frappe does"
		)
		self.assertEqual(d["helpers"]["party_unreadable"], "Pumps Inc")
		self.assertIn('href="/desk/project/PRJ-1"', d["helpers"]["party"])
		self.assertIn('target="_blank"', d["helpers"]["party"])
		self.assertNotIn(["Supplier", "SUP-1"], self.out["routes"])
		self.assertIn(["Customer", "CUST-1"], self.out["routes"])

	def test_datetimes_use_frappes_convert_to_user_tz(self):
		"""As v16's own Datetime formatter does: moment(frappe.datetime.convert_to_user_tz(value))."""
		tz = self.out["desk_tz"]
		self.assertEqual(tz["cell"], "10/14/2026 8:05 AM")
		self.assertEqual(tz["helper"], "10/14/2026 8:05 AM")
		self.assertEqual(tz["date_not_converted"], "10/14/2026")
		self.assertEqual(tz["converter_throws"], "01/01/2026 12:00 AM", "unconverted rather than an error")
		self.assertEqual(tz["calls"], ["2026-10-14 14:05:00", "2026-10-14 14:05:00", "2026-01-01 00:00:00"])

	def test_the_helpers(self):
		h = self.out["desk"]["helpers"]
		self.assertEqual(h["phone"], "(801) 555-0100")
		self.assertEqual(h["date"], "10/14/2026")
		self.assertEqual(h["datetime"], "10/14/2026 8:05 AM", "converted into the person's time zone")
		self.assertEqual(h["time"], "2:05 PM")
		self.assertIn("sf-report-chip--service", h["pillar"])
		self.assertIn("sf-report-chip--danger", h["chip"])

	def test_it_fails_open(self):
		self.assertEqual(self.out["fail_open"], ["DEFAULT(Date:2026-10-14)"] * 2)
		self.assertEqual(self.out["warnings"], 1, "one console warning, not one per cell")


class StaticTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _node_results()

	def test_the_chip_is_sf_kits_chip(self):
		"""The inline declarations are the kit's .sf-chip rules, so a report chip and a page chip match."""
		rules = _css_rules(KIT_CSS)
		tables = self.out["tables"]
		self.assertEqual([tuple(d) for d in tables["base"]], rules[".sf-chip"])
		for tone in tables["all"]:
			with self.subTest(tone=tone):
				selector = f".sf-chip--{tone}"
				expected = rules.get(selector, [])
				self.assertEqual([tuple(d) for d in tables["tones"].get(tone, [])], expected)
		self.assertNotIn(".sf-chip--neutral", rules, "neutral is the base rule")

	def test_every_token_the_chip_reads_is_on_every_desk_page(self):
		defined = set(re.findall(r"(--sf-[a-z0-9-]+)\s*:", TOKENS_CSS.read_text(encoding="utf-8")))
		used = set()
		for style in self.out["chip"]["styles"].values():
			used |= set(re.findall(r"var\((--sf-[a-z0-9-]+)\)", style))
		self.assertTrue(used)
		self.assertLessEqual(used, defined, f"undefined tokens: {sorted(used - defined)}")

	def test_stream_pillar_is_print_styles(self):
		tree = ast.parse(PRINT_STYLE.read_text(encoding="utf-8"))
		original = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "STREAM_PILLAR"
		)
		self.assertEqual(self.out["pillar_for"], original)

	def test_the_global_bundle_imports_the_face_and_not_the_kit(self):
		bundle = BUNDLE.read_text(encoding="utf-8")
		self.assertIn('import "./report_face/report_face.js";', bundle)
		self.assertLess(
			bundle.index('import "./normalize/desk_display.js";'),
			bundle.index('import "./report_face/report_face.js";'),
		)
		self.assertNotIn("sf_kit.bundle", bundle)
		self.assertNotIn("sf_kit/index.js", bundle)
		for path in (CELLS, FACE):
			code = path.read_text(encoding="utf-8")
			imports = re.findall(r'^import .* from "([^"]+)";', code, re.M)
			with self.subTest(file=path.name):
				for target in imports:
					self.assertIn(target, {"../sf_kit/esc.js", "../normalize/normalize.js", "./cells.js"})

	def test_no_route_is_built_by_hand(self):
		for path in (CELLS, FACE):
			code = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
			code = "\n".join(line for line in code.splitlines() if not line.strip().startswith("//"))
			with self.subTest(file=path.name):
				self.assertNotRegex(code, r"/(?:app|desk)/")
		self.assertIn("f.utils.get_form_link(doctype, name)", FACE.read_text(encoding="utf-8"))

	def test_the_pure_module_touches_no_frappe_or_dom(self):
		code = re.sub(r"/\*[\s\S]*?\*/", "", CELLS.read_text(encoding="utf-8"))
		code = "\n".join(line for line in code.splitlines() if not line.strip().startswith("//"))
		for name in ("frappe", "document", "window", "$("):
			with self.subTest(name=name):
				self.assertNotIn(name, code)


if __name__ == "__main__":
	unittest.main()
