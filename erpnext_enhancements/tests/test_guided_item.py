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

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir, inputJson] = process.argv.slice(2);
const load = (path) => import(pathToFileURL(dir + path).href);
const kit = await load("/form_kit/index.js");
const naming = await load("/guided_item/naming.js");
const codes = await load("/guided_item/codes.js");
const advice = await load("/guided_item/advice.js");
const blocks = await load("/guided_item/blocks.js");
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
			shutil.copy(BUNDLE, folder / "guided_entry.bundle.js")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			payload = json.dumps({"names": NAMES, "codes": CODES})
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
				self.assertEqual(bool(get), not write)
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

	def test_the_pure_modules_need_no_frappe_and_no_dom(self):
		for name in ("naming.js", "codes.js", "advice.js", "blocks.js"):
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
