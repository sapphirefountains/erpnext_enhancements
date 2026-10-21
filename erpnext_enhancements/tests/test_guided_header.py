"""Bench-free tests for the guided page's head band and its files section (v1.711.0).

Nik, 2026-10-21: "Need to surface attachments and show stage of opportunity at the top header for the
new guided layout."

* The stage chip: a DocType declares ``header.status`` in its form kit config; ``get_flow`` sends it;
  ``public/js/form_kit/header.js`` (pure, run here under node) turns it and the record's values into a
  word, a tone and a sentence for a screen reader; the page redraws it in ``refresh_chrome``.
* The files: they were a group inside the collapsible Record tools block. The guided page now draws
  them in a section of their own under the steps (``.ge-files``), counts them in the head band
  (``.ge-files-chip``) and takes files dropped anywhere on the page, as the Desk form does.

The decisions run under node (copied beside a ``package.json`` that says ``"type": "module"``, as
test_guided_record_tools does); the rest is static: the shell draws the pieces, the editor's tools
fill them, nothing singles out a DocType by name, and the CSS styles what the script draws.

Run: python -m unittest erpnext_enhancements.tests.test_guided_header -v
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

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FORM_KIT_JS = JS / "form_kit"
HEADER_JS = FORM_KIT_JS / "header.js"
UI_JS = FORM_KIT_JS / "ui" / "record_tools.js"
SIDE_PANEL_JS = FORM_KIT_JS / "ui" / "side_panel.js"
PAGE_JS = APP / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js"
CSS = APP / "public" / "css" / "guided_entry.bundle.css"
CHIP_JS = JS / "sf_kit" / "chip.js"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an absence check must not be satisfied by its comment."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir] = process.argv.slice(2);
const h = await import(pathToFileURL(dir + "/form_kit/header.js").href);
const kit = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const out = {};

const opp = {
	status: {
		field: "status",
		label: "Stage",
		tones: { "Closed Won": "good", Lost: "danger", "On Hold": "warn" },
	},
};
out.exported = ["header_status", "files_chip", "files_drop", "tone_of"].every((k) => typeof kit[k] === "function");
out.stage = {
	open: h.header_status(opp, { status: "Proposal/Price Quote" }),
	won: h.header_status(opp, { status: "Closed Won" }),
	lost: h.header_status(opp, { status: "Lost" }),
	held: h.header_status(opp, { status: "On Hold" }),
	unlisted: h.header_status(opp, { status: "Some stage added later" }),
	padded: h.header_status(opp, { status: "  Lost " }),
	blank: h.header_status(opp, { status: "" }),
	missing: h.header_status(opp, {}),
	no_values: h.header_status(opp, null),
	no_header: h.header_status(undefined, { status: "Lost" }),
	empty_header: h.header_status({}, { status: "Lost" }),
	no_field: h.header_status({ status: { label: "Stage" } }, { status: "Lost" }),
	no_label: h.header_status({ status: { field: "status" } }, { status: "Lost" }),
	bad_tone: h.header_status({ status: { field: "status", label: "Stage", tones: { Lost: "purple" } } }, { status: "Lost" }),
	other_field: h.header_status({ status: { field: "workflow_state", label: "State" } }, { workflow_state: "Draft", status: "Lost" }),
};
// It follows the value: the same header, the value changed on screen.
const values = { status: "Needs Analysis" };
const before = h.header_status(opp, values);
values.status = "Closed Won";
out.follows = [before.value, before.tone, h.header_status(opp, values).value, h.header_status(opp, values).tone];

out.files = {
	none: h.files_chip(0),
	one: h.files_chip(1),
	many: h.files_chip(12),
	unknown: [h.files_chip(null), h.files_chip(undefined), h.files_chip("x")],
	negative: h.files_chip(-3),
	text_number: h.files_chip("4"),
};
const base = { has_files: true, is_new: false, loaded: true, can_add: true };
out.drop = {
	attach: h.files_drop(base),
	not_files: h.files_drop({ ...base, has_files: false }),
	new_record: h.files_drop({ ...base, is_new: true }),
	loading: h.files_drop({ ...base, loaded: false }),
	denied: h.files_drop({ ...base, can_add: false }),
	empty: h.files_drop(),
	new_beats_loading: h.files_drop({ ...base, is_new: true, loaded: false }),
};
out.tones = h.TONES;
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheDecisionsUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit", ignore=shutil.ignore_patterns("ui"))
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			done = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder)],
				capture_output=True,
				text=True,
				timeout=60,
				check=False,
			)
		if done.returncode != 0:
			raise AssertionError(f"node failed:\n{done.stdout}\n{done.stderr}")
		cls.out = json.loads(done.stdout.strip().splitlines()[-1])

	def test_the_kit_exports_them(self):
		self.assertTrue(self.out["exported"])

	def test_the_stage_chip_says_the_stage_in_the_boards_words(self):
		stage = self.out["stage"]
		self.assertEqual(
			stage["open"],
			{
				"field": "status",
				"label": "Stage",
				"value": "Proposal/Price Quote",
				"tone": "neutral",
				"aria": "Stage: Proposal/Price Quote",
			},
		)
		self.assertEqual(stage["padded"]["value"], "Lost", "a stray space is not part of the stage")

	def test_closing_stages_have_their_own_tones_and_open_ones_stay_neutral(self):
		stage = self.out["stage"]
		self.assertEqual(stage["won"]["tone"], "good")
		self.assertEqual(stage["lost"]["tone"], "danger")
		self.assertEqual(stage["held"]["tone"], "warn")
		self.assertEqual(stage["open"]["tone"], "neutral", "a pillar colour keeps meaning a pillar")
		self.assertEqual(stage["unlisted"]["tone"], "neutral", "a stage added later still shows")
		self.assertEqual(stage["unlisted"]["value"], "Some stage added later")

	def test_a_tone_that_is_not_a_chip_tone_is_neutral(self):
		self.assertEqual(self.out["stage"]["bad_tone"]["tone"], "neutral")

	def test_there_is_no_chip_without_a_word_or_a_declaration(self):
		stage = self.out["stage"]
		for key in ("blank", "missing", "no_values", "no_header", "empty_header", "no_field"):
			self.assertIsNone(stage[key], key)

	def test_a_header_without_a_label_still_has_a_sentence(self):
		self.assertEqual(self.out["stage"]["no_label"]["aria"], "Lost")

	def test_any_field_can_be_declared_the_header_status(self):
		"""Not Opportunity's `status`: whatever the DocType's config names."""
		other = self.out["stage"]["other_field"]
		self.assertEqual((other["field"], other["value"], other["aria"]), ("workflow_state", "Draft", "State: Draft"))

	def test_the_chip_follows_the_value(self):
		self.assertEqual(self.out["follows"], ["Needs Analysis", "neutral", "Closed Won", "good"])

	def test_the_files_chip_counts(self):
		files = self.out["files"]
		self.assertEqual(files["none"], {"count": 0, "aria": "No files attached"})
		self.assertEqual(files["one"], {"count": 1, "aria": "1 file attached"})
		self.assertEqual(files["many"], {"count": 12, "aria": "12 files attached"})
		self.assertEqual(files["text_number"]["count"], 4)
		self.assertEqual(files["negative"]["count"], 0)

	def test_the_files_chip_does_not_claim_none_before_it_knows(self):
		for chip in self.out["files"]["unknown"]:
			self.assertEqual(chip, {"count": None, "aria": ""})

	def test_a_drop_is_answered_whatever_the_record_is(self):
		drop = self.out["drop"]
		self.assertEqual(drop["attach"], "attach")
		self.assertEqual(drop["not_files"], "ignore", "dragged text is the browser's")
		self.assertEqual(drop["new_record"], "save", "the form says: Please save before attaching")
		self.assertEqual(drop["new_beats_loading"], "save")
		self.assertEqual(drop["loading"], "wait", "permissions have not arrived: do not guess")
		self.assertEqual(drop["denied"], "denied")
		self.assertEqual(drop["empty"], "ignore")

	def test_the_tones_are_sf_kits_chip_tones(self):
		chip = _text(CHIP_JS)
		declared = re.search(r"PILLAR_TONES = \[(.*?)\];", chip).group(1) + "," + re.search(r"STATUS_TONES = \[(.*?)\];", chip).group(1)
		sf_tones = set(re.findall(r'"(\w+)"', declared))
		self.assertEqual(set(self.out["tones"]), sf_tones)

		from erpnext_enhancements.form_kit import config

		self.assertEqual(set(config.CHIP_TONES), sf_tones)


class TestTheShellDrawsThem(unittest.TestCase):
	def setUp(self):
		self.page = _code(PAGE_JS)
		self.tools = _code(UI_JS)
		self.css = _text(CSS)

	def test_the_band_has_the_stage_chip_and_the_files_chip(self):
		band = self.page[self.page.index('<div class="ge-band">') : self.page.index('<div class="ge-progress">')]
		self.assertIn("ge-stage-chip", band)
		self.assertIn("ge-files-chip", band)
		self.assertLess(band.index('class="ge-title"'), band.index("ge-stage-chip"), "under the title, inside the band")

	def test_the_files_chip_starts_hidden_and_opens_the_section(self):
		self.assertRegex(self.page, r'<button type="button" class="ge-files-chip"[^>]*hidden>')
		self.assertIn('find(".ge-files-chip").on("click", () => this.show_files())', self.page)
		self.assertIn("show_files() {", self.page)

	def test_the_files_section_sits_under_the_steps_whatever_the_view(self):
		self.assertRegex(self.page, r'<section class="ge-stage"></section>\s*<section class="ge-files"[^>]*hidden></section>')
		self.assertIn('find(".ge-files").insertAfter(this.$root.find(".ge-stage"))', self.page)

	def test_the_chip_follows_the_values_on_every_refresh(self):
		chrome = self.page[self.page.index("\trefresh_chrome() {") :]
		chrome = chrome[: chrome.index("\n\t}\n")]
		self.assertIn("this.render_stage_chip()", chrome)
		method = self.page[self.page.index("\trender_stage_chip() {") :]
		method = method[: method.index("\n\t}\n")]
		self.assertIn("this.kit.header_status(this.flow && this.flow.header, this.values)", method)
		self.assertIn("this.sf.chip(", method)
		self.assertIn("data-key", method, "a live region is not redrawn when nothing changed")

	def test_the_stage_chip_is_a_live_region_that_names_what_it_is(self):
		self.assertRegex(self.page, r'class="ge-stage-chip" aria-live="polite"')
		self.assertIn('<span class="sr-only"></span>', self.page)

	def test_focus_mode_folds_the_files_but_the_chip_opens_them(self):
		self.assertIn("files_open_default()", self.page)
		self.assertIn('prop("open", !focus)', self.page)
		self.assertIn('find("details").prop("open", true)', self.page)

	def test_the_tools_fill_the_section_and_the_chip(self):
		self.assertIn('this.$dock = $root.find(".ge-files")', self.tools)
		self.assertIn("this.$dock.length ? this.dock_files() : this.group(", self.tools)
		self.assertIn("this.show_count(files.length)", self.tools)
		self.assertIn("this.ed.kit.files_chip(n)", self.tools)
		self.assertIn(".ge-files-chip", self.tools)

	def test_the_side_panel_keeps_its_files_in_the_tools(self):
		"""It draws no .ge-files, so the tools' own Attachments group is still where they are."""
		panel = _code(SIDE_PANEL_JS)
		self.assertNotIn("ge-files", panel)

	def test_a_new_record_has_no_files_and_no_chip(self):
		mount = self.tools[self.tools.index("\tmount() {") :]
		mount = mount[: mount.index("\n\t}\n")]
		self.assertLess(mount.index("this.dock_reset()"), mount.index("if (ed.is_new"))

	def test_the_drop_is_the_forms_own(self):
		drop = self.tools[self.tools.index("\tbind_drop($root) {") :]
		drop = drop[: drop.index("\n\t}\n")]
		self.assertIn("dragenter", drop)
		self.assertIn("dragover", drop)
		self.assertIn("event.preventDefault()", drop)
		self.assertIn("this.ed.kit.files_drop(", drop)
		self.assertIn("this.attach(files)", drop)
		self.assertIn('"Please save before attaching."', drop, "the form's own sentence")
		self.assertIn("$root.off(ns)", drop, "bound once per mount, not once per record")
		attach = self.tools[self.tools.index("\tattach(dropped) {") :]
		attach = attach[: attach.index("\n\t}\n")]
		self.assertIn("files: dropped", attach)
		self.assertIn('folder: "Home/Attachments"', attach)
		self.assertIn("make_attachments_public: meta.make_attachments_public", attach)

	def test_what_may_be_added_or_removed_is_still_the_sidebars_rule(self):
		self.assertIn("rules.can_add_file(", self.tools)
		self.assertIn("rules.can_remove_files(", self.tools)
		self.assertIn("frappe.desk.form.utils.remove_attach", self.tools)

	def test_nothing_singles_out_a_doctype_by_name(self):
		for src in (_code(HEADER_JS), self.tools):
			self.assertNotRegex(src, r"""["']Opportunity["']""")
		self.assertNotIn("Opportunity", _code(HEADER_JS))

	def test_every_user_string_is_written_as_text(self):
		for src in (_code(HEADER_JS), self.tools, self.page):
			self.assertNotRegex(src, r"\.html\([^)]*(status|file)\.", "a stage or a file name is never HTML")
		stage = self.page[self.page.index("\trender_stage_chip() {") :]
		stage = stage[: stage.index("\n\t}\n")]
		self.assertNotIn(".html(", stage)

	def test_the_css_styles_what_the_script_draws(self):
		for selector in (
			".ge-chips",
			".ge-stage-chip",
			".ge-files-chip",
			".ge-files {",
			".ge-files-summary",
			".ge-files-title",
			".ge-files-count",
			".ge-files-body",
			".is-dropping-files",
		):
			self.assertIn(selector, self.css, selector)

	def test_the_files_chip_is_a_phone_sized_target(self):
		rule = self.css[self.css.index(".ge-files-chip {") :]
		rule = rule[: rule.index("}")]
		self.assertIn("min-height: var(--sf-touch-min)", rule)

	def test_the_chip_class_is_not_given_a_colour_outside_the_tokens(self):
		new = self.css[self.css.index("The head band's chips and the files section") :]
		self.assertNotRegex(new, r"#[0-9a-fA-F]{3,8}\b")
		self.assertNotRegex(new, r"rgba?\(")


if __name__ == "__main__":
	unittest.main()
