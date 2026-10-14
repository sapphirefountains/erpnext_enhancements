"""Bench-free tests for the guided Item flow's page side (form kit P8.1, TASK-2026-02790).

The Item flow's decisions are pure ES modules in ``public/js/guided_item/`` (the name builder, the
code helper, which findings are refusals and which advice, the blocks' small rules), and the
guided page's extension point is ``public/js/form_kit/extensions.js`` with ``ends_core`` and
``opens_by_default`` beside it. Those run here under node, copied with ``form_kit/`` to a temp dir
beside a ``package.json`` that says ``"type": "module"``, the way ``test_guided_entry`` runs the
page's own modules. Skips the node half cleanly when ``node`` is not on PATH.

The rest is static or Python: the JS copies of ``item_naming_rules``' constants held to the
originals, the assembled names held to ``item_naming_rules.check_name``'s punctuation rules, and the
wiring (the page draws a step's block, the Item form has a Guided button, the guided page lets an
Item Manager in, every request is ``sf_kit.call`` quiet with reads as GET, no ``/app/`` path).

Run: python -m unittest erpnext_enhancements.tests.test_guided_item -v
"""

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

from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming
from erpnext_enhancements.inventory_enhancements import item_suggest_rules as suggest_rules

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
ITEM_JS = JS / "guided_item"
EXTENSION_JS = ITEM_JS / "extension.js"
PAGE_DIR = APP / "enhancements_core" / "page" / "guided_entry"
PAGE_JS = PAGE_DIR / "guided_entry.js"
PAGE_JSON = PAGE_DIR / "guided_entry.json"
# The record editor both shells run (form kit P4.4, v1.675.0): the blocks are mounted there.
EDITOR_JS = JS / "form_kit" / "ui" / "editor.js"
PANEL_JS = JS / "form_kit" / "ui" / "side_panel.js"
BUNDLE = JS / "guided_entry.bundle.js"
ITEM_FORM_JS = JS / "item.js"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped, so an absence check is not satisfied by its own comment."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


CODES = [
	"",
	"  ",
	"PDT-0042",
	"pdt-0042",
	"PDT-00051",
	"SRV-007",
	"CON-ELEC-TAPE",
	"con-x",
	"505025",
	"A-21",
	"PDTX-1",
]
NAMES = [
	["elbow", "90", "soc", "pvc", '2" sch80', "", ""],
	["  pump ", "", "submersible,  quiet", "", "1200 GPH", "", ""],
	["", "", "", "", "", "", ""],
	["TAPE", "ELECTRICAL", "", "VINYL", '3/4"', "", "ROLL"],
]

# The builder's rules, run by both naming.js/codes.js and item_suggest_rules' Python copies.
MIRROR = {
	"names": NAMES + [["a", "b", "c", "d", "e", "f", "g", "h"], [None, " x ", "y,z"]],
	"parts": [" AQU-98,125 ", "Paper  Towel!", "steel, grey ", "", "  ", "ß straße", '2"\tsch80'],
	"con": [["elec", "zip tie", '8"'], ["OFFC", "Paper  Towel!", ""], ["srv", "--a--b--", " 1/2 "], ["", "", ""]],
}


def _server_answer():
	"""What api.item_suggest answers for one made-up pump, built by the server's own pure rules, so
	the page's reading of it is tested against the real shape rather than a copy."""
	data = {
		"family": {"value": "vendor", "reason": "A purchased pump."},
		"vendor_part_no": {"value": "LG-505025", "reason": "On the label in the photo."},
		"name": {
			"CATEGORY": {"value": "pumps", "reason": "It moves water."},
			"SUB-CATEGORY": {"value": "submersible"},
			"SIZE": {"value": '1-1/2"'},
		},
		"item_group": {"value": "Pumps"},
		"stock_uom": {"value": "Unit"},
	}
	checked = suggest_rules.validate_suggestion(
		data, description="a pump", has_photo=True, item_groups=["Pumps"], uoms=["Unit"]
	)
	suggest_rules.normalize_category(checked)
	composed = suggest_rules.compose(checked)
	review = suggest_rules.review(composed, existing_codes=[])
	return {"ok": True, "fields": suggest_rules.field_rows(checked, review), "item_name": composed["item_name"]}


ANSWER = _server_answer()

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const load = (path) => import(pathToFileURL(dir + path).href);
const kit = await load("/form_kit/index.js");
const naming = await load("/guided_item/naming.js");
const codes = await load("/guided_item/codes.js");
const advice = await load("/guided_item/advice.js");
const blocks = await load("/guided_item/blocks.js");
const suggest = await load("/guided_item/suggest.js");
const input = JSON.parse(inputJson);
const out = {};

// ---- the extension point
out.registry = { before: kit.has_extension("Item"), none: kit.extension_for("Opportunity") };
await load("/guided_item/extension.js");
const ext = kit.extension_for("Item");
out.registry.after = kit.has_extension("Item");
out.registry.methods = ["render", "on_change", "before_create", "leave"].filter((m) => typeof ext[m] === "function");
out.registry.fresh = kit.extension_for("Item") !== ext;
out.registry.before_create = ext.before_create();
kit.register_extension("Broken", () => { throw new Error("no"); });
out.registry.broken = kit.extension_for("Broken");

// ---- where the core of a flow ends
const steps = [
	{ key: "basics", fields: [] },
	{ key: "stock", applies: { field: "is_stock_item", in: [1] }, fields: [] },
	{ key: "asset", applies: { field: "is_fixed_asset", in: [1] }, fields: [] },
	{ key: "settings", more: 1, fields: [] },
	{ key: "units", more: 1, fields: [] },
];
out.ends_core = {
	basics_stock: kit.ends_core(steps, { is_stock_item: 1 }, "basics"),
	basics_plain: kit.ends_core(steps, {}, "basics"),
	stock: kit.ends_core(steps, { is_stock_item: 1 }, "stock"),
	settings: kit.ends_core(steps, {}, "settings"),
	last: kit.ends_core(steps, {}, "units"),
	no_more: kit.ends_core([{ key: "a" }, { key: "b" }], {}, "a"),
	unknown: kit.ends_core(steps, {}, "nope"),
};

// ---- which DocTypes open on the guided page
const ctx = (guided) => ({ guided, page_open: true, full_form: [], hash: "", renamed: [], options: {} });
out.default = {
	flags: [undefined, null, {}, { default: 1 }, { default: 0 }, { default: "0" }, { accepts: [] }].map(kit.opens_by_default),
	opportunity: kit.redirect_for(["Form", "Opportunity", "CRM-OPP-1"], ctx({ Opportunity: { accepts: [], default: 1 } })),
	opportunity_old_boot: kit.redirect_for(["Form", "Opportunity", "CRM-OPP-1"], ctx({ Opportunity: { accepts: [] } })),
	item: kit.redirect_for(["Form", "Item", "LG-505025"], ctx({ Item: { accepts: [], default: 0 } })),
	item_new: kit.redirect_for(["Form", "Item", "new"], ctx({ Item: { accepts: [], default: 0 } })),
	// A reader (an Item Manager let in for the Item flow, who may only read deals) keeps the form.
	reader_saved: kit.redirect_for(["Form", "Opportunity", "CRM-OPP-1"], ctx({ Opportunity: { accepts: [], default: 0, default_new: 0 } })),
	reader_new: kit.redirect_for(["Form", "Opportunity", "new"], ctx({ Opportunity: { accepts: [], default: 0, default_new: 0 } })),
	// Someone who may create deals but not change this one: New opens here, a saved deal does not.
	creator_saved: kit.redirect_for(["Form", "Opportunity", "CRM-OPP-1"], ctx({ Opportunity: { accepts: [], default: 0, default_new: 1 } })),
	creator_new: kit.redirect_for(["Form", "Opportunity", "new"], ctx({ Opportunity: { accepts: [], default: 0, default_new: 1 } })),
	new_flags: [{ default: 1 }, { default: 1, default_new: 0 }, { default: 0, default_new: 1 }].map((e) => kit.opens_by_default(e, true)),
};

// ---- the name builder
out.names = input.names.map((parts) => naming.assemble_name(parts));
out.split = ["ELBOW, 90, SOC, PVC", "A, B, C, D, E, F, G, H, I", "", "Pump,submersible"].map(naming.split_name);
out.same = ["ELBOW, 90, SOC", "ELBOW,90", "Elbow, 90", ""].map(naming.builds_same);
out.segments = naming.SEGMENTS;
const catalog = [
	["ELBOW", "90", "SOC", "PVC", '2" SCH80'],
	["ELBOW", "90", "SOC", "PVC", '1" SCH40'],
	["ELBOW", "45", "SOC", "PVC"],
	["ELBOW", "90", "THD", "BRASS"],
	["TEE", "REDUCING", "SOC", "PVC"],
	["WIDGET", "X"],
];
out.options = {
	category: naming.part_options(catalog, 0, [], ["ELBOW", "TEE", "VALVE"]),
	sub_any: naming.part_options(catalog, 1, [], []),
	sub_elbow: naming.part_options(catalog, 1, ["elbow"], []),
	material_elbow_90: naming.part_options(catalog, 3, ["ELBOW", "90"], []),
	size_elbow_45: naming.part_options(catalog, 4, ["ELBOW", "45"], []),
	nothing_matches: naming.part_options(catalog, 2, ["ELBOW", "180"], []),
};

// ---- the code helper
out.classify = input.codes.map(codes.classify);
out.codes = {
	vendor: codes.build_code({ family: "vendor", vendor: " AQU-98,125 " }),
	con: codes.build_code({ family: "consumable", group: "elec", descriptor: "zip tie", size: '8"' }),
	con_no_size: codes.build_code({ family: "consumable", group: "OFFC", descriptor: "Paper  Towel!" }),
	pdt: codes.build_code({ family: "product", number: 42 }),
	srv: codes.build_code({ family: "service", number: 7 }),
	bad_number: codes.numbered_code("PDT", -1),
	unknown_prefix: codes.numbered_code("XYZ", 1),
};
out.code_problems = {
	vendor_blank: codes.code_problem({ family: "vendor", vendor: " " }),
	vendor_ours: codes.code_problem({ family: "vendor", vendor: "PDT-0001" }),
	vendor_ok: codes.code_problem({ family: "vendor", vendor: "505025" }),
	con_group: codes.code_problem({ family: "consumable", group: "FOOD", descriptor: "X" }),
	con_descriptor: codes.code_problem({ family: "consumable", group: "ELEC", descriptor: "!!" }),
	con_ok: codes.code_problem({ family: "consumable", group: "ELEC", descriptor: "TAPE" }),
	pdt_no_block: codes.code_problem({ family: "product" }),
	pdt_ok: codes.code_problem({ family: "product", number: 101 }),
	none: codes.code_problem({}),
	too_long: codes.code_problem({ family: "vendor", vendor: "X".repeat(141) }),
};
out.constants = { groups: codes.CONSUMABLE_GROUPS, prefixes: codes.RESERVED_PREFIXES, families: codes.FAMILIES };

// ---- refusals and advice
const blocking = [{ code: "duplicate_code_normalised", message: "Item Code <b>X</b> is the same as Y." }];
const found = [
	{ code: "name_not_uppercase", severity: "FIX", message: "Not uppercase." },
	{ code: "name_category_unapproved", severity: "STOP", message: "Not on Appendix A." },
	{ code: "name_size_word_inch", severity: "NOTE", message: "INCH." },
];
out.verdict = {
	enforced: advice.verdict({ checked: true, blocking, advice: found, will_refuse: true, similar: [{ item_code: "A" }, {}] }),
	before_go_live: advice.verdict({ checked: true, blocking, advice: [], will_refuse: false }),
	clean: advice.verdict({ checked: true, blocking: [], advice: [] }),
	empty: advice.verdict(null),
};
out.answers = [
	advice.answers({ item_code: "A", item_name: "B" }, { item_code: "A", item_name: "B" }),
	advice.answers({ item_code: "A", item_name: "B" }, { item_code: "A", item_name: "BC" }),
];

// ---- the blocks
const setup = {
	default_supplier: "Little Giant",
	default_warehouse: "Bin A1-3-1 - SF",
	suppliers: [
		{ supplier: "Aquascape", supplier_part_no: "AQ-1", purchase_url: "", rate: null },
		{ supplier: "Little Giant", supplier_part_no: "505025", purchase_url: "https://lg.example/p", rate: 41.5 },
	],
	reorder: [
		{ warehouse: "Bin A1-3-1 - SF", level: 2, qty: 6, material_request_type: "Transfer" },
		{ warehouse: "Bin A1-3-1 - SF", level: 4, qty: 12, material_request_type: "Purchase" },
	],
};
out.blocks = {
	label: blocks.label_url("Bin A1-3-1 - SF"),
	label_none: blocks.label_url(" "),
	id: blocks.new_client_id(1760400000000, () => 0.5),
	supplier_start: blocks.supplier_start(setup),
	supplier_start_empty: blocks.supplier_start({}),
	supplier_row: blocks.supplier_row(setup, "Aquascape"),
	stock_start: blocks.stock_start(setup),
	stock_start_empty: blocks.stock_start({}),
	given: ["", null, undefined, 0, "0", " "].map(blocks.given),
	fields_known: blocks.supplier_fields(setup, "Aquascape"),
	fields_unknown: blocks.supplier_fields(setup, "Pentair"),
	ticked: [
		blocks.default_ticked(setup, "Little Giant"),
		blocks.default_ticked(setup, "Aquascape"),
		blocks.default_ticked({ default_supplier: "" }, "Aquascape"),
		blocks.default_ticked({}, ""),
	],
	rate: [
		blocks.rate_to_send(41.5, "41.5"),
		blocks.rate_to_send(41.5, "41.50"),
		blocks.rate_to_send(41.5, "45"),
		blocks.rate_to_send("", "12"),
		blocks.rate_to_send(41.5, ""),
		blocks.rate_to_send("", ""),
		blocks.rate_to_send(41.5, "0"),
	],
};

// ---- the Describe it card (v1.691.0): the server's own answer, built by item_suggest_rules
const answer = input.answer;
out.suggest = {
	constants: { description_max: suggest.DESCRIPTION_MAX, photo_max: suggest.PHOTO_MAX_BYTES },
	start: [
		suggest.start_from({ item_code: "AFS-7731", item_name: "" }, { description: "<p>Submersible pump &amp; 1/2&nbsp;HP</p>" }),
		suggest.start_from({ item_code: "pump 1/2 hp submersible", item_name: "" }, {}),
		suggest.start_from({ item_code: "PDT-0042", item_name: "Pump, X" }, {}),
		suggest.start_from({ item_code: "825Y-QT 1" }, { description: "<p><br></p>" }),
		suggest.start_from(null, null),
	],
	untagged: suggest.untagged('<img src=x onerror="alert(1)">Tee &#8220;2&#x22;&#8221; &bogus; &lt;b&gt;'),
	fit: [[4032, 3024], [800, 600], [3000, 9000], [0, 0]].map(([w, h]) => suggest.fit_photo(w, h, suggest.PHOTO_MAX_EDGE)),
	bytes: ["data:image/png;base64,AAAA", "data:image/png;base64,AAA=", "AAAAAA==", ""].map(suggest.data_url_bytes),
	photo: [
		suggest.photo_problem({ type: "image/jpeg", size: 1000 }),
		suggest.photo_problem({ type: "", size: 1000 }),
		suggest.photo_problem({ type: "application/pdf", size: 10 }),
		suggest.photo_problem({ type: "image/heic", size: 30 * 1024 * 1024 }),
	],
	targets: ["family", "vendor_part_no", "consumable_group", "descriptor", "code_size", "segment_0", "segment_6", "segment_7", "item_group", "stock_uom", "item_code", "rate"].map(suggest.target_for),
	rows: suggest.rows_for(answer),
	rows_junk: [suggest.rows_for(null), suggest.rows_for({ fields: [null, { key: "segment_0", value: " " }, { key: "owner", value: "x" }] })],
	spec: {
		family: suggest.code_spec_after({ family: "product", number: 42, vendor: "X" }, { kind: "family" }, "service"),
		vendor: suggest.code_spec_after({ family: "product", number: 42 }, { kind: "code", part: "vendor" }, "AFS-7731"),
		group: suggest.code_spec_after({ family: "vendor", vendor: "A" }, { kind: "code", part: "group" }, "ELEC"),
	},
	segments: suggest.segments_after(["PUMP", "", ""], 3, "stainless, 304"),
	segments_bad: suggest.segments_after(["pump"], 9, "X"),
	order: suggest.accept_all_order(suggest.rows_for(answer)).map((row) => row.key),
};
// The builder's rules, for item_suggest_rules' Python copies of them.
out.mirror = {
	names: input.mirror.names.map((parts) => naming.assemble_name(parts)),
	parts: input.mirror.parts.map(naming.clean_part),
	vendor: input.mirror.parts.map(codes.vendor_code),
	con: input.mirror.con.map(([group, descriptor, size]) => codes.consumable_code(group, descriptor, size)),
};
// A buying line's supplier and rate, for the supplier block of the Item it became.
const no_suppliers = { stock_uom: "Nos", suppliers: [] };
const line = { supplier: "Example Pumps", rate: 41.5, uom: "Nos" };
out.line = {
	from: [blocks.line_from({ supplier: "Example Pumps", rate: 41.5, uom: "Nos", item_code: "X" }), blocks.line_from({ rate: 5 }), blocks.line_from(null)],
	same_unit: blocks.line_supplier_start(blocks.supplier_start(no_suppliers), line, no_suppliers),
	other_unit: blocks.line_supplier_start(blocks.supplier_start(no_suppliers), Object.assign({}, line, { uom: "Box" }), no_suppliers),
	no_unit: blocks.line_supplier_start({}, Object.assign({}, line, { uom: "" }), no_suppliers),
	zero: blocks.line_supplier_start({}, Object.assign({}, line, { rate: 0 }), no_suppliers),
	text_rate: blocks.line_supplier_start({}, Object.assign({}, line, { rate: "abc" }), no_suppliers),
	has_suppliers: blocks.line_supplier_start({}, line, { stock_uom: "Nos", suppliers: [{ supplier: "Other" }] }),
	no_supplier: blocks.line_supplier_start({}, {}, no_suppliers),
};

// ---- the bundle the page loads installs all of it
globalThis.window = {};
await load("/guided_entry.bundle.js");
const installed = globalThis.window.sf_form_kit || {};
out.bundle = {
	version: installed.version,
	has: ["extension_for", "ends_core", "opens_by_default", "parse_route"].filter((name) => typeof installed[name] === "function"),
	item: !!installed.extension_for("Item"),
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheModules(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(JS / "form_kit", folder / "form_kit")
			shutil.copytree(ITEM_JS, folder / "guided_item")
			# The bundle carries the record editor (v1.675.0), whose Party component reads the blocks.
			shutil.copytree(JS / "info_blocks", folder / "info_blocks")
			# v1.686.0: and its record tools import the record actions and party rules the Desk shares.
			(folder / "global_enhancements").mkdir()
			for name in ("party_sources.js", "record_actions.js"):
				shutil.copy(JS / "global_enhancements" / name, folder / "global_enhancements" / name)
			shutil.copy(BUNDLE, folder / "guided_entry.bundle.js")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			payload = json.dumps({"names": NAMES, "codes": CODES, "answer": ANSWER, "mirror": MIRROR})
			proc = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder), payload],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	# ---- the extension point

	def test_the_item_extension_registers_itself_and_others_have_none(self):
		r = self.out["registry"]
		self.assertFalse(r["before"])
		self.assertTrue(r["after"])
		self.assertIsNone(r["none"])
		self.assertEqual(r["methods"], ["render", "on_change", "before_create", "leave"])
		self.assertTrue(r["fresh"], "each record gets its own extension")
		self.assertEqual(r["before_create"], "", "nothing checked yet holds nothing back")
		self.assertIsNone(r["broken"], "an extension that fails to start is none at all")

	def test_the_pages_bundle_installs_the_kit_with_the_item_extension(self):
		b = self.out["bundle"]
		self.assertEqual(b["version"], 4, "3 brought the extensions (P8.1), 4 the view settings (v1.684.0)")
		self.assertEqual(b["has"], ["extension_for", "ends_core", "opens_by_default", "parse_route"])
		self.assertTrue(b["item"])

	def test_done_comes_after_the_core_steps(self):
		e = self.out["ends_core"]
		self.assertFalse(e["basics_stock"], "Next goes on to Stock & bin")
		self.assertTrue(e["basics_plain"], "a plain item's core is one step")
		self.assertTrue(e["stock"])
		self.assertFalse(e["settings"], "inside the more steps Next walks on")
		self.assertFalse(e["last"])
		self.assertFalse(e["no_more"], "a flow without more steps (Opportunity) is unchanged")
		self.assertFalse(e["unknown"])

	def test_only_a_default_flow_takes_a_record_off_its_form(self):
		d = self.out["default"]
		self.assertEqual(d["flags"], [False, False, True, True, False, False, True])
		self.assertEqual(d["opportunity"]["route"], ["guided-entry", "Opportunity", "CRM-OPP-1"])
		self.assertEqual(d["opportunity_old_boot"], d["opportunity"])
		self.assertIsNone(d["item"], "an Item keeps opening in its form")
		self.assertIsNone(d["item_new"])
		self.assertIsNone(d["reader_saved"], "a person who may only read deals keeps the form")
		self.assertIsNone(d["reader_new"])
		self.assertIsNone(d["creator_saved"])
		self.assertEqual(d["creator_new"]["route"], ["guided-entry", "Opportunity", "new"])
		self.assertEqual(d["new_flags"], [True, False, True])

	# ---- the name builder

	def test_names_are_assembled_in_order_with_one_comma_and_space(self):
		self.assertEqual(
			self.out["names"],
			[
				'ELBOW, 90, SOC, PVC, 2" SCH80',
				"PUMP, SUBMERSIBLE QUIET, 1200 GPH",
				"",
				'TAPE, ELECTRICAL, VINYL, 3/4", ROLL',
			],
		)

	def test_the_assembled_names_pass_the_naming_rules_punctuation(self):
		punctuation = {
			naming.NAME_EDGE_WHITESPACE,
			naming.NAME_DOUBLE_SPACE,
			naming.NAME_TRAILING_COMMA,
			naming.NAME_NOT_UPPERCASE,
			naming.NAME_SEPARATOR_SPACING,
			naming.NAME_EMPTY_SEGMENT,
			naming.NAME_TOO_MANY_SEGMENTS,
		}
		for name in self.out["names"]:
			if not name:
				continue
			with self.subTest(name=name):
				codes = {finding["code"] for finding in naming.check_name(name, "X-1")}
				self.assertEqual(codes & punctuation, set())

	def test_an_existing_name_splits_back_into_seven_segments(self):
		split = self.out["split"]
		self.assertEqual(split[0], ["ELBOW", "90", "SOC", "PVC", "", "", ""])
		self.assertEqual(split[1][-1], "G H I", "nothing past the seventh comma is lost")
		self.assertEqual(split[2], [""] * 7)
		self.assertEqual(split[3][:2], ["PUMP", "SUBMERSIBLE"])
		self.assertEqual(self.out["same"], [True, False, False, True])

	def test_the_segments_are_the_rules_segments(self):
		self.assertEqual(self.out["segments"], list(naming.SEGMENTS))

	def test_pick_lists_come_from_the_catalog_narrowed_by_what_is_chosen(self):
		o = self.out["options"]
		self.assertEqual(o["category"][:3], ["ELBOW", "TEE", "VALVE"], "Appendix A first")
		self.assertIn("WIDGET", o["category"], "then what the catalog uses")
		self.assertEqual(o["sub_any"][0], "90")
		self.assertEqual(o["sub_elbow"], ["90", "45"], "most used first, only ELBOW's")
		self.assertEqual(o["material_elbow_90"], ["PVC", "BRASS"])
		self.assertEqual(o["size_elbow_45"], [])
		self.assertEqual(
			o["nothing_matches"], ["SOC", "THD"], "an unknown choice falls back to the category's names"
		)

	# ---- the code helper

	def test_the_family_is_the_rules_family(self):
		self.assertEqual(self.out["classify"], [naming.classify_code_family(code) for code in CODES])

	def test_codes_are_written_the_policy_way(self):
		c = self.out["codes"]
		self.assertEqual(c["vendor"], "AQU-98125", "as printed, without the comma")
		self.assertEqual(c["con"], 'CON-ELEC-ZIP-TIE-8"')
		self.assertEqual(c["con_no_size"], "CON-OFFC-PAPER-TOWEL")
		self.assertEqual(c["pdt"], "PDT-0042")
		self.assertEqual(c["srv"], "SRV-007")
		self.assertEqual((c["bad_number"], c["unknown_prefix"]), ("", ""))
		for code in (c["con"], c["con_no_size"], c["pdt"], c["srv"]):
			with self.subTest(code=code):
				self.assertEqual(naming.check_code(code), [], "the naming check finds nothing wrong")

	def test_a_code_with_a_problem_says_what_it_is(self):
		p = self.out["code_problems"]
		for key in (
			"vendor_blank",
			"vendor_ours",
			"con_group",
			"con_descriptor",
			"pdt_no_block",
			"none",
			"too_long",
		):
			with self.subTest(case=key):
				self.assertTrue(p[key])
		for key in ("vendor_ok", "con_ok", "pdt_ok"):
			with self.subTest(case=key):
				self.assertEqual(p[key], "")
		self.assertIn("ELEC, OFFC, SRV", p["con_group"])

	def test_the_constants_are_the_rules_constants(self):
		k = self.out["constants"]
		self.assertEqual(k["groups"], list(naming.CONSUMABLE_GROUPS))
		self.assertEqual(k["prefixes"], dict(naming.RESERVED_PREFIXES))
		self.assertEqual(
			k["families"],
			[naming.FAMILY_VENDOR, naming.FAMILY_CONSUMABLE, naming.FAMILY_PRODUCT, naming.FAMILY_SERVICE],
		)

	# ---- refusals and advice

	def test_only_the_guards_two_findings_refuse_and_only_while_it_is_in_force(self):
		v = self.out["verdict"]
		enforced = v["enforced"]
		self.assertEqual(enforced["refusals"], ["Item Code <b>X</b> is the same as Y."])
		self.assertFalse(enforced["can_create"])
		self.assertEqual(
			[a["severity"] for a in enforced["advice"]], ["STOP", "FIX", "NOTE"], "a STOP is still advice"
		)
		self.assertEqual(enforced["similar"], [{"item_code": "A"}])
		before = v["before_go_live"]
		self.assertEqual(before["refusals"], [])
		self.assertTrue(before["can_create"])
		self.assertEqual(before["advice"][0]["severity"], "STOP")
		self.assertTrue(v["clean"]["can_create"])
		self.assertEqual(v["empty"]["refusals"], [])
		self.assertEqual(self.out["answers"], [True, False])

	# ---- the blocks

	def test_the_bin_label_is_stock_scans_label_page(self):
		b = self.out["blocks"]
		self.assertEqual(b["label"], "/warehouse-labels?w=Bin%20A1-3-1%20-%20SF")
		self.assertEqual(b["label_none"], "")

	def test_an_asset_create_id_is_the_shape_the_server_accepts(self):
		from erpnext_enhancements.inventory_enhancements.item_flow_rules import client_id

		minted = self.out["blocks"]["id"]
		self.assertEqual(client_id(minted), minted)

	def test_the_blocks_start_from_the_default_supplier_and_bin(self):
		b = self.out["blocks"]
		self.assertEqual(
			b["supplier_start"],
			{
				"supplier": "Little Giant",
				"supplier_part_no": "505025",
				"purchase_url": "https://lg.example/p",
				"rate": 41.5,
			},
		)
		self.assertEqual(
			b["supplier_start_empty"],
			{"supplier": "", "supplier_part_no": "", "purchase_url": "", "rate": ""},
		)
		self.assertEqual(b["supplier_row"]["supplier_part_no"], "AQ-1")
		self.assertEqual(
			b["stock_start"], {"warehouse": "Bin A1-3-1 - SF", "reorder_level": 4, "reorder_qty": 12}
		)
		self.assertEqual(b["stock_start_empty"], {"warehouse": "", "reorder_level": "", "reorder_qty": ""})
		self.assertEqual(b["given"], [False, False, False, True, True, False])

	def test_picking_another_supplier_never_carries_the_last_ones_details(self):
		b = self.out["blocks"]
		self.assertEqual(b["fields_known"], {"supplier_part_no": "AQ-1", "purchase_url": "", "rate": ""})
		self.assertEqual(b["fields_unknown"], {"supplier_part_no": "", "purchase_url": "", "rate": ""})
		self.assertEqual(
			b["ticked"], [True, False, True, True], "a second supplier never takes the default by itself"
		)

	def test_a_price_is_sent_only_when_it_was_changed(self):
		self.assertEqual(self.out["blocks"]["rate"], [None, None, "45", "12", None, None, "0"])

	# ---- the Describe it card (v1.691.0)

	def test_the_builders_python_copies_are_the_builders(self):
		m = self.out["mirror"]
		self.assertEqual(m["names"], [suggest_rules.assemble_name(parts) for parts in MIRROR["names"]])
		self.assertEqual(m["parts"], [suggest_rules.clean_part(part) for part in MIRROR["parts"]])
		self.assertEqual(m["vendor"], [suggest_rules.vendor_code(part) for part in MIRROR["parts"]])
		self.assertEqual(m["con"], [suggest_rules.consumable_code(*args) for args in MIRROR["con"]])
		s = self.out["suggest"]["constants"]
		self.assertEqual(s["description_max"], suggest_rules.DESCRIPTION_MAX)
		self.assertEqual(s["photo_max"], suggest_rules.IMAGE_MAX_BYTES)

	def test_the_card_starts_from_what_the_item_was_started_with(self):
		start = self.out["suggest"]["start"]
		self.assertEqual(start[0], {"description": "Submersible pump & 1/2 HP", "vendor_part_no": "AFS-7731"})
		self.assertEqual(
			start[1], {"description": "pump 1/2 hp submersible", "vendor_part_no": ""}, "words are a description"
		)
		self.assertEqual(start[2], {"description": "Pump, X", "vendor_part_no": ""}, "never our own family")
		self.assertEqual(start[3], {"description": "", "vendor_part_no": "825Y-QT 1"}, "an empty editor is no text")
		self.assertEqual(start[4], {"description": "", "vendor_part_no": ""})
		self.assertEqual(self.out["suggest"]["untagged"], "Tee “2\"” &bogus; <b>", "text, for .val()")

	def test_a_photo_is_scaled_and_sized(self):
		s = self.out["suggest"]
		self.assertEqual(
			[(f["width"], f["height"], f["scaled"]) for f in s["fit"]],
			[(1600, 1200, True), (800, 600, False), (533, 1600, True), (0, 0, False)],
		)
		self.assertEqual(s["bytes"], [3, 2, 4, 0])
		self.assertEqual(s["photo"][:2], ["", ""], "a type the browser does not name goes to the server's check")
		self.assertIn("JPEG, PNG, WebP or HEIC", s["photo"][2])
		self.assertIn("too large", s["photo"][3])

	def test_each_suggested_field_has_one_place_to_go(self):
		targets = self.out["suggest"]["targets"]
		self.assertEqual(
			targets,
			[
				{"kind": "family"},
				{"kind": "code", "part": "vendor"},
				{"kind": "code", "part": "group"},
				{"kind": "code", "part": "descriptor"},
				{"kind": "code", "part": "size"},
				{"kind": "segment", "index": 0},
				{"kind": "segment", "index": 6},
				None,
				{"kind": "field", "fieldname": "item_group"},
				{"kind": "field", "fieldname": "stock_uom"},
				None,
				None,
			],
			"no row can write the Item Code or a price directly",
		)

	def test_the_servers_rows_are_read_as_sent(self):
		rows = self.out["suggest"]["rows"]
		self.assertEqual([row["key"] for row in rows], [row["key"] for row in ANSWER["fields"]])
		by_key = {row["key"]: row for row in rows}
		self.assertTrue(by_key["vendor_part_no"]["confirm"], "a part number off the photo is checked first")
		self.assertEqual(by_key["family"]["shown"], "Vendor part number")
		self.assertEqual(by_key["segment_0"]["value"], "PUMP", "the rule's singular")
		self.assertEqual(by_key["segment_0"]["verdict"]["tone"], "good")
		self.assertEqual(self.out["suggest"]["rows_junk"], [[], []])

	def test_accept_all_never_takes_a_part_number_to_check_and_builds_the_code_first(self):
		order = self.out["suggest"]["order"]
		self.assertNotIn("vendor_part_no", order)
		self.assertEqual(order[0], "family")
		self.assertEqual(order[-2:], ["item_group", "stock_uom"])
		self.assertEqual(order[1:-2], ["segment_0", "segment_1", "segment_4"])

	def test_an_accepted_family_never_carries_a_number(self):
		spec = self.out["suggest"]["spec"]
		self.assertEqual(spec["family"], {"family": "service", "vendor": "X"})
		self.assertEqual(spec["vendor"], {"family": "vendor", "vendor": "AFS-7731"})
		self.assertEqual(spec["group"], {"family": "consumable", "vendor": "A", "group": "ELEC"})
		self.assertEqual(self.out["suggest"]["segments"], ["PUMP", "", "", "STAINLESS 304", "", "", ""])
		self.assertEqual(self.out["suggest"]["segments_bad"], ["PUMP", "", "", "", "", "", ""])

	def test_a_buying_lines_supplier_and_rate_start_the_supplier_block(self):
		line = self.out["line"]
		self.assertEqual(line["from"], [{"supplier": "Example Pumps", "rate": 41.5, "uom": "Nos"}, None, None])
		self.assertEqual(line["same_unit"]["supplier"], "Example Pumps")
		self.assertEqual(line["same_unit"]["rate"], 41.5)
		self.assertEqual(line["other_unit"]["rate"], "", "a price per box is not the price per each")
		self.assertEqual(line["no_unit"]["rate"], 41.5)
		self.assertEqual(line["zero"]["rate"], "")
		self.assertEqual(line["text_rate"]["rate"], "")
		self.assertIsNone(line["has_suppliers"], "an Item with suppliers starts as it always did")
		self.assertIsNone(line["no_supplier"])

	def test_the_supplier_block_uses_them(self):
		code = _code(EXTENSION_JS)
		self.assertIn("const fields = supplier_fields(this.setup, picked);", code)
		self.assertNotIn("if (!row) return;", code)
		self.assertIn('$default.find("input").prop("checked", default_ticked(this.setup, picked));', code)
		self.assertIn("const rate = $rate ? rate_to_send(shown_rate, $rate.val()) : null;", code)
		self.assertNotIn('<input type="checkbox" checked>', code)


class TestTheWiring(unittest.TestCase):
	def test_the_page_draws_a_steps_block_and_keeps_its_fields_if_it_fails(self):
		# Both shells pick the DocType's extension when a record opens, and drop it on reset.
		for shell in (PAGE_JS, PANEL_JS):
			with self.subTest(shell=shell.name):
				shell_code = _code(shell)
				self.assertIn("this.open_extension(doctype);", shell_code)
				self.assertIn("this.ext = null;", shell_code)
		page = _code(PAGE_JS)
		for moved in ("mount_extension(step, $fields) {", "ext_context(step) {", "\tcreate() {"):
			with self.subTest(moved=moved):
				self.assertNotIn(moved, page, "the editor's code is not copied back onto the page")
		code = _code(EDITOR_JS)
		self.assertIn(
			"this.ext = this.kit && this.kit.extension_for ? this.kit.extension_for(doctype) : null;", code
		)
		self.assertIn("this.mount_extension(step, $fields);", code)
		fields = code[code.index("render_fields(step, $fields, editable) {") :]
		fields = fields[: fields.index("\n\t}\n")]
		self.assertLess(fields.index("this.leave_extension();"), fields.index("new frappe.ui.FieldGroup"))
		self.assertIn("this.mount_extension(step, $fields);", fields)
		self.assertIn("this.ext.render(step.extension, $box, this.ext_context(step));", code)
		body = code[code.index("mount_extension(step, $fields) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertLess(body.index("try {"), body.index("this.ext.render("))
		self.assertIn("$box.remove();", body)
		self.assertIn("this.ext_notify(fieldname, value);", code)
		self.assertIn("this.ext.before_create()", code)
		create = code[code.index("\tcreate() {") :]
		self.assertLess(
			create.index("try {"),
			create.index("this.ext.before_create()"),
			"a block that throws holds nothing",
		)

	def test_a_block_writes_after_the_pages_save_and_the_page_takes_the_new_version(self):
		page = _code(EDITOR_JS)
		self.assertIn("save_first: () => (same() ? this.save_before_moving()", page)
		self.assertIn("if (same() && !this.is_new) this.refresh_from_server();", page)
		ext = _code(EXTENSION_JS)
		body = ext[ext.index("write($box, ctx, method, args, done) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertLess(body.index("ctx\n\t\t\t.save_first()"), body.index("ctx.sf.call(API + method"))
		self.assertIn("ctx.record_changed();", body)

	def test_done_ends_the_core_and_more_details_goes_on(self):
		code = _code(PAGE_JS)
		self.assertIn("if (next && !this.ends_core()) {", code)
		self.assertIn('__("More details: {0}"', code)
		self.assertIn("this.kit.ends_core(this.flow.steps, this.values, this.step_key)", code)

	def test_a_tab_holding_the_older_kit_still_works(self):
		# Review of #1270: a tab that loaded sf_form_kit v2 before a deploy has neither the editor's
		# open_extension nor the kit's opens_by_default; the page must not call either blind.
		code = _code(PAGE_JS)
		self.assertIn('if (typeof this.open_extension === "function") this.open_extension(doctype);', code)
		self.assertIn("!this.kit.opens_by_default ||", code)

	def test_server_html_is_read_in_an_inert_document(self):
		# Review of #1270: a created div's innerHTML runs an <img onerror> even unattached.
		code = _code(EXTENSION_JS)
		self.assertIn('new DOMParser().parseFromString(String(message || ""), "text/html")', code)
		self.assertNotIn(".innerHTML", code)

	def test_new_item_on_the_start_screen_starts_on_the_guided_page(self):
		code = _code(PAGE_JS)
		self.assertIn("window.sf_guided.is_guided(doctype, true)", code)
		self.assertIn("frappe.set_route(this.kit.build_route(doctype, null, null))", code)

	def test_the_bundle_carries_the_item_extension(self):
		bundle = _code(BUNDLE)
		self.assertIn('import "./guided_item/extension.js";', bundle)
		# 3: a tab still holding version 2 (the editor, v1.675.0) has no extension_for. 4 since v1.684.0
		# (the view settings): never back below 3.
		version = re.search(r"const VERSION = (\d+);", bundle)
		self.assertTrue(version and int(version.group(1)) >= 3)

	def test_every_request_is_quiet_reads_are_get_writes_post(self):
		code = _code(EXTENSION_JS)
		calls = re.findall(r"ctx\.sf\s*\.call\(([^;]*?)\{ quiet: true(, method: \"GET\")? \}\)", code, re.S)
		raw = len(re.findall(r"\.call\(", code))
		self.assertEqual(len(calls), raw, "a request outside sf_kit.call quiet")
		self.assertNotIn("frappe.call(", code)
		for args, get in calls:
			with self.subTest(call=args.strip()[:40]):
				write = (
					any(f'"{name}"' in args for name in ("save_supplier", "save_stock", "create_asset"))
					or "API + method" in args
				)
				# The Describe it card's Suggest (v1.691.0) writes nothing but is a POST: a photo does
				# not fit an address, and every call is a model call.
				post = write or args.strip().startswith("SUGGEST,")
				self.assertEqual(bool(get), not post)
		for name in ("naming_options", "preview_item", "code_blocks", "get_setup"):
			self.assertIn(f'API + "{name}"', code)
		for name in ("save_supplier", "save_stock", "create_asset"):
			self.assertIn(f'"{name}"', code)

	def test_no_hard_coded_desk_paths(self):
		for path in [EXTENSION_JS, *ITEM_JS.glob("*.js"), JS / "form_kit" / "extensions.js"]:
			with self.subTest(path=path.name):
				self.assertNotIn("/app/", _code(path))
		self.assertIn("frappe.utils.get_form_link(doctype, name)", _code(EXTENSION_JS))

	def test_the_label_is_printed_from_the_existing_label_page(self):
		self.assertIn('window.open(label_url(start.warehouse), "_blank", "noopener")', _code(EXTENSION_JS))
		self.assertIn("/warehouse-labels?w=", _text(ITEM_JS / "blocks.js"))
		self.assertIn("/warehouse-labels?w=", _text(JS / "warehouse_stock_scan.js"))

	def test_the_describe_card_is_wired_to_the_builders_and_never_writes_markup(self):
		code = _code(EXTENSION_JS)
		self.assertIn('const SUGGEST = "erpnext_enhancements.api.item_suggest.suggest_item";', code)
		self.assertIn(".call(SUGGEST, args, { quiet: true })", code)
		card = code[code.index("render_describe($card, ctx) {") : code.index("render_naming($box, ctx) {")]
		self.assertNotIn(".html(", card, "the AI's words are model output: .text() and .val() only")
		self.assertNotIn("innerHTML", card)
		self.assertIn("this.set_code(code_spec_after(this.code_spec, t, value), ctx);", card)
		self.assertIn('ctx.set_value("item_name", name);', card)
		self.assertIn("ctx.set_value(t.fieldname, value);", card)
		naming_step = code[code.index("render_naming($box, ctx) {") :]
		naming_step = naming_step[: naming_step.index("\n\t}\n")]
		self.assertLess(naming_step.index("if (ctx.is_new && ctx.can_write) {"), naming_step.index("this.render_describe("))
		self.assertIn("typeof ctx.carried === \"function\"", card, "a tab with the older editor still draws it")

	def test_a_buying_lines_supplier_waits_for_the_supplier_block(self):
		code = _code(EXTENSION_JS)
		before = code[code.index("before_create() {") :]
		before = before[: before.index("\n\t}\n")]
		self.assertIn("LINE_STARTS.set(code, this.line);", before)
		supplier = code[code.index("render_supplier($box, ctx, setup) {") :]
		supplier = supplier[: supplier.index("\n\t}\n")]
		self.assertIn("LINE_STARTS.delete(String(ctx.name", supplier)
		self.assertIn("let shown_rate = recorded.rate;", supplier, "the line's rate is not recorded yet, so it is sent")
		editor = _code(EDITOR_JS)
		self.assertIn("carried: () => (same() && this.is_new ? Object.assign({}, this.carry || {}) : {}),", editor)
		self.assertIn("started: () => (same() && this.is_new ? Object.assign({}, this.start_options || {}) : {}),", editor)
		page = _code(PAGE_JS)
		self.assertIn("this.start_options = ge_clone(given);", page)
		self.assertIn("this.start_options = {};", page)

	def test_the_item_list_offers_the_guided_flow_beside_new(self):
		code = _code(JS / "item_list.js")
		self.assertIn('listview.page.add_inner_button(__("New item (guided)")', code)
		self.assertIn('frappe.set_route("guided-entry", "Item", "new");', code)
		self.assertIn('window.sf_guided.has_flow("Item")', code)
		self.assertIn('frappe.model.can_create("Item")', code)
		self.assertIn("original_onload.call(this, listview);", code, "ERPNext's own onload still runs")
		from erpnext_enhancements.form_kit.config.item import CONFIG

		self.assertFalse(CONFIG["open_by_default"], "an Item still opens in its form")

	def test_the_pure_modules_need_no_frappe_and_no_dom(self):
		for name in ("naming.js", "codes.js", "advice.js", "blocks.js", "suggest.js"):
			code = _code(ITEM_JS / name)
			with self.subTest(module=name):
				for token in ("frappe", "document.", "window.", "$("):
					self.assertNotIn(token, code)

	def test_the_item_form_has_a_guided_button_that_honors_the_boot(self):
		code = _code(ITEM_FORM_JS)
		self.assertIn('frm.add_custom_button(__("Guided")', code)
		self.assertIn('frappe.boot.page_info["guided-entry"]', code)
		self.assertIn('window.sf_guided.has_flow("Item")', code)
		self.assertIn("window.sf_guided.to_guided(frm.doctype, frm.doc.name);", code)
		self.assertIn('frappe.set_route("guided-entry", "Item", "new");', code)
		hooks = _text(APP / "hooks.py")
		block = hooks[hooks.index('"Item": [', hooks.index("doctype_js = {")) :]
		self.assertIn('"public/js/item.js"', block[: block.index("],")])

	def test_item_managers_may_open_the_guided_page(self):
		page = json.loads(_text(PAGE_JSON))
		roles = {row["role"] for row in page["roles"]}
		self.assertEqual(roles, {"System Manager", "Sales Manager", "Sales User", "Item Manager"})


if __name__ == "__main__":
	unittest.main()
