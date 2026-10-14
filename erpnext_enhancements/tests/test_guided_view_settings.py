"""Bench-free tests for the guided page's view settings (v1.684.0).

Nik, 2026-10-14: a saved record on the guided page is laid out the way each person chooses (One
page, Tabs or Step by step; where the steps sit; the summary; notes and comments; the record tools;
density, hints, text size, contrast, focus mode; pinned sections and where a record opens; fields
and sections put away; summary fields; presets; shortcuts), "saved to their instance and not change
it for everyone else's views". And, standing: "We shouldn't have lost any fidelity or information".

Four halves:

* **Under node**: the pure decisions (``public/js/form_kit/view.js``: cleaning, the shell's
  attributes, tabs and pins, scroll-spy, where a record opens, what may be put away, presets, the
  shortcuts), the set of FieldGroups One page draws (``form_kit/field_groups.js``), and the board's
  card parts (``public/js/opportunity_board/board.js``). Copied beside a ``{"type": "module"}``
  package.json, as the other form kit suites do.
* **The store** (``api/planner_views_prefs.py``, the guided part) against a frappe stub: only the
  caller's own ``__UserSettings`` row, written straight to the table (never frappe's Redis-only
  user_settings), everything cleaned on the way in and out, the caps, a DocType the guided page
  does not serve refused, read permission checked, the first-time pop-up remembered.
* **Fidelity**: every layout draws through the record editor's one renderer; a new record's setup
  flow is always step by step; the side panel is unchanged.
* **The wiring**: endpoints, quiet calls, the stylesheet (tokens only, attributes not inline
  styles), CI.

Run: python -m unittest erpnext_enhancements.tests.test_guided_view_settings -v
"""

import ast
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FORM_KIT_JS = JS / "form_kit"
BOARD_JS = JS / "opportunity_board"
VIEW_JS = FORM_KIT_JS / "view.js"
EDITOR_JS = FORM_KIT_JS / "ui" / "editor.js"
PANEL_JS = FORM_KIT_JS / "ui" / "side_panel.js"
PAGE_JS = APP / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js"
BOARD_PAGE_JS = APP / "crm_enhancements" / "page" / "opportunity_board" / "opportunity_board.js"
CSS = APP / "public" / "css" / "guided_entry.bundle.css"
PREFS_PY = APP / "api" / "planner_views_prefs.py"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
README = APP / "form_kit" / "README.md"
PREFS = "erpnext_enhancements.api.planner_views_prefs"
USER = "nik@example.com"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _method(code, head):
	"""One class method's body, from its head to the line that closes it at the class's indent."""
	start = code.index(head)
	return code[start : code.index("\n\t}\n", start)]


# ---------------------------------------------------------------------------------- under node

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir] = process.argv.slice(2);
const v = await import(pathToFileURL(dir + "/form_kit/view.js").href);
const steps_mod = await import(pathToFileURL(dir + "/form_kit/steps.js").href);
const { FieldGroups } = await import(pathToFileURL(dir + "/form_kit/field_groups.js").href);
const board = await import(pathToFileURL(dir + "/opportunity_board/board.js").href);
const out = {};
out.schema = v.VIEW_SCHEMA;
out.defaults = v.VIEW_DEFAULTS;
out.setup = v.SETUP_VIEW;
out.caps = { pinned: v.MAX_PINNED, hidden: v.MAX_HIDDEN, summary_fields: v.MAX_SUMMARY_FIELDS, presets: v.MAX_PRESETS, preset_name: v.MAX_PRESET_NAME, last: v.MAX_LAST };
out.landings = v.LANDINGS;

// ---- cleaning: unknown keys go, invalid values fall back
out.clean = v.clean_view({ layout: "tabs", nav: "diagonal", junk: 1, focus: "on", text: 3 });
out.clean_null = v.clean_view(null);
out.entry = v.clean_entry({
	prefs: { layout: "steps" },
	pinned: ["close", "close", "bad key!", "budget"],
	landing: "<script>",
	hidden_fields: ["website", 7, "website"],
	summary_fields: ["status", "x".repeat(200)],
	presets: [{ name: "  Phone  ", prefs: { density: "compact" } }, { name: "phone", prefs: {} }, { name: "", prefs: {} }],
	last: [["A", "close"], ["A", "budget"], ["B", "nope nope"], ["C", "basics"]],
});
out.entry_none = v.clean_entry(undefined);
out.store_missing = v.entry_for({ doctypes: { Other: { prefs: { layout: "tabs" } } } }, "Opportunity").prefs.layout;
out.effective_new = v.effective_view({ layout: "page", nav: "right" }, true);
out.effective_saved = v.effective_view({ layout: "tabs" }, false).layout;
out.effective_blocks = [v.effective_view({ layout: "page", nav: "top" }, false, true).layout, v.effective_view({ layout: "page", nav: "top" }, false, true).nav];

// ---- the shell's attributes
out.attrs_default = v.shell_attrs(v.VIEW_DEFAULTS);
out.attrs_hidden_summary = v.shell_attrs({ summary: "hidden", notes: "below", tools: "below" }).aside;
out.attrs_notes_side = v.shell_attrs({ summary: "hidden", notes: "side", tools: "below" }).aside;
out.attrs_left = v.shell_attrs({ summary: "left" }).aside;
out.attrs_focus = v.shell_attrs({ focus: "on", summary: "left", notes: "side", tools: "side", hints: "shown" });

// ---- tabs and pins, from the real Opportunity steps' tabs
const steps = [
	{ key: "basics", title: "Basics", tab: "Details" },
	{ key: "contact", title: "Contact & site", tab: "Contacts & Addresses" },
	{ key: "scope", title: "Scope", tab: "Scope" },
	{ key: "scope_design", title: "Design scope", tab: "Scope", applies: { field: "custom_value_stream", row_field: "value_stream", in: ["Design"] } },
	{ key: "schedule", title: "Schedule", tab: "Schedule" },
	{ key: "budget", title: "Budget", tab: "Budget" },
	{ key: "close", title: "Close", tab: "Details" },
	{ key: "more", title: "More details", tab: "" },
];
const values = { custom_value_stream: [{ value_stream: "Build" }] };
const tabs = v.view_tabs(steps, values, steps_mod.step_active, []);
out.tabs = tabs.map((t) => [t.key, t.label, t.steps.map((s) => s.key + (s.active ? "" : "-off")), t.active]);
const pinned_tabs = v.view_tabs(steps, values, steps_mod.step_active, ["close", "budget"]);
out.pinned_tabs = pinned_tabs.map((t) => [t.label, t.steps.map((s) => s.key)]);
const group = v.pinned_group(steps, values, steps_mod.step_active, ["close", "budget"], "Pinned");
out.pinned_group = [group.key, group.label, group.steps.map((s) => s.key), group.pinned];
out.pinned_none = v.pinned_group(steps, values, steps_mod.step_active, [], "Pinned");
out.tab_of = [v.tab_of(tabs, "close").label, v.tab_of(tabs, "nope")];
out.tab_step = [v.tab_step(tabs[2]), v.tab_step({ steps: [{ key: "x", active: false }] })];
out.neighbor_tab = [v.neighbor_tab(tabs, "t0", 1).label, v.neighbor_tab(tabs, "t0", -1), v.neighbor_tab(tabs, "t5", 1)];
out.tab_missing = v.tab_missing(tabs[0], { basics: [1, 2], close: [3], more: [4] });
out.section_keys_page = v.section_keys(pinned_tabs, "page", "*", group);
out.section_keys_tabs = v.section_keys(pinned_tabs, "tabs", "t1", group);
out.neighbor_section = [
	v.neighbor_section(["a", "b", "c"], "b", 1),
	v.neighbor_section(["a", "b", "c"], "c", 1),
	v.neighbor_section(["a", "b", "c"], "a", -1),
	v.neighbor_section(["a", "b", "c"], "zz", 1),
];
out.spy = [
	v.spy_pick([{ key: "a", top: -400 }, { key: "b", top: 120 }, { key: "c", top: 900 }], 150),
	v.spy_pick([{ key: "a", top: 300 }, { key: "b", top: 900 }], 150),
	v.spy_pick([], 150),
	v.spy_pick([{ key: "a", top: NaN }], 150),
];
out.toggle = [v.toggle_key(["a"], "b", true, 2), v.toggle_key(["a", "b"], "c", true, 2), v.toggle_key(["a", "b"], "a", false, 2)];

// ---- where a record opens
let last = [];
for (let i = 0; i < 60; i += 1) last = v.remember_last(last, "R" + i, "budget");
out.last_len = last.length;
out.last_first = last[0];
out.last_dropped = v.last_step(last, "R0");
last = v.remember_last(last, "R30", "close");
out.last_moved = [last[0], last.filter((p) => p[0] === "R30").length];
const entry = v.clean_entry({ landing: "last", last: [["OPP-1", "scope_design"], ["OPP-2", "close"]] });
out.start = [
	v.start_step(entry, "OPP-2", steps, values, steps_mod.step_active),
	v.start_step(entry, "OPP-1", steps, values, steps_mod.step_active),
	v.start_step(entry, "OPP-9", steps, values, steps_mod.step_active),
	v.start_step(v.clean_entry({ landing: "budget" }), "OPP-9", steps, values, steps_mod.step_active),
	v.start_step(v.clean_entry({ landing: "first", last: [["OPP-2", "close"]] }), "OPP-2", steps, values, steps_mod.step_active),
	v.start_step(v.clean_entry({ landing: "gone" }), "OPP-9", steps, values, steps_mod.step_active),
];

// ---- what shows
const def = (fieldname, fieldtype, extra) => Object.assign({ fieldname, fieldtype }, extra || {});
out.has_value = [
	v.has_value(def("a", "Data"), " "),
	v.has_value(def("a", "Data"), "x"),
	v.has_value(def("a", "Currency"), 0),
	v.has_value(def("a", "Currency"), "12.5"),
	v.has_value(def("a", "Check"), 0),
	v.has_value(def("a", "Check"), 1),
	v.has_value(def("a", "Table"), []),
	v.has_value(def("a", "Table"), [{}]),
	v.has_value(def("a", "Link"), null),
];
const missing = new Set(["expected_closing"]);
out.must_show = [
	v.must_show(def("x", "Data", { reqd: 1 }), "", missing),
	v.must_show(def("x", "Data", { reqd: 1 }), "set", missing),
	v.must_show(def("expected_closing", "Date"), "", missing),
	v.must_show(def("y", "Data", { mandatory_depends_on: "eval:1" }), null, missing),
	v.must_show(def("z", "Data"), "", missing),
];
const opts = { hidden_fields: ["website", "title", "expected_closing"], missing, filled_only: false, reveal: false };
out.field_hidden = [
	v.field_hidden(def("website", "Data"), "a.com", opts),
	v.field_hidden(def("title", "Data", { reqd: 1 }), "", opts),
	v.field_hidden(def("title", "Data", { reqd: 1 }), "set", opts),
	v.field_hidden(def("expected_closing", "Date"), "", opts),
	v.field_hidden(def("website", "Data"), "a.com", Object.assign({}, opts, { reveal: true })),
	v.field_hidden(def("notes", "Small Text"), "", Object.assign({}, opts, { filled_only: true })),
	v.field_hidden(def("notes", "Small Text"), "hi", Object.assign({}, opts, { filled_only: true })),
	v.field_hidden(def("expected_closing", "Date"), "", Object.assign({}, opts, { hidden_fields: [], filled_only: true })),
	v.field_hidden(def("must", "Data", { reqd: 1 }), "", Object.assign({}, opts, { hidden_fields: [], filled_only: true })),
];
const step = { key: "more", fields: [def("website", "Data"), def("title", "Data", { reqd: 1 })] };
out.step_hidden = [
	v.step_hidden(step, { title: "set" }, { hidden_steps: ["more"], missing }),
	v.step_hidden(step, { title: "" }, { hidden_steps: ["more"], missing }),
	v.step_hidden(step, { title: "set" }, { hidden_steps: ["more"], missing, reveal: true }),
	v.step_hidden(step, { title: "set" }, { hidden_steps: [], missing }),
];
out.can_hide = [v.can_hide(def("a", "Data"), "", missing), v.can_hide(def("a", "Data", { reqd: 1 }), "", missing)];

// ---- presets
let presets = [];
let result = v.save_preset(presets, "  My   setup ", { layout: "tabs" });
out.preset_saved = [result.ok, result.name, result.presets[0].prefs.layout];
presets = result.presets;
result = v.save_preset(presets, "my setup", { layout: "steps" });
out.preset_replaced = [result.presets.length, result.presets[0].name, result.presets[0].prefs.layout];
out.preset_noname = v.save_preset(presets, "   ", {}).reason;
let full = [];
for (let i = 0; i < 10; i += 1) full = v.save_preset(full, "P" + i, {}).presets;
out.preset_full = [v.save_preset(full, "P11", {}).reason, v.save_preset(full, "p3", { layout: "tabs" }).ok];
out.preset_delete = v.delete_preset(full, "P3").length;
out.preset_current = [
	(v.current_preset([{ name: "A", prefs: { layout: "tabs" } }], { layout: "tabs" }) || {}).name,
	v.current_preset([{ name: "A", prefs: { layout: "tabs" } }], { layout: "page" }),
];

// ---- shortcuts
const k = (key, mods) => Object.assign({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false }, mods || {});
out.shortcuts = [
	v.shortcut_for(k("j"), false),
	v.shortcut_for(k("j"), true),
	v.shortcut_for(k("k"), false),
	v.shortcut_for(k("s", { ctrlKey: true }), true),
	v.shortcut_for(k("S", { metaKey: true }), false),
	v.shortcut_for(k("s", { ctrlKey: true, shiftKey: true }), false),
	v.shortcut_for(k("?", { shiftKey: true }), false),
	v.shortcut_for(k("?", { shiftKey: true }), true),
	v.shortcut_for(k("f"), false),
	v.shortcut_for(k("F", { shiftKey: true }), false),
	v.shortcut_for(k("c", { ctrlKey: true }), false),
	v.shortcut_for(k("v"), false),
	v.shortcut_for(k("c"), false),
	v.shortcut_for(k("g", { altKey: true }), false),
	v.shortcut_for(k("x"), false),
];
out.shortcut_actions = v.SHORTCUTS.map((s) => s.action);

// ---- the FieldGroups of One page
const groups = new FieldGroups();
const refreshed = [];
const fg = (name, fields) => ({ fields_dict: fields, refresh_dependency: () => refreshed.push(name) });
groups.add({ key: "basics" }, fg("basics", { title: { id: 1 }, status: { id: 2 } }));
groups.add({ key: "more" }, fg("more", { website: { id: 3 }, notes__ge_list: { id: 4 } }));
groups.refresh_dependency();
out.groups = {
	multi: groups.multi,
	fields: Object.keys(groups.fields_dict).sort(),
	steps: groups.steps().map((s) => s.key),
	owns: [groups.owns(groups.list[0]), groups.owns({})],
	step_of: [(groups.step_of("website") || {}).key, (groups.step_of("notes", "__ge_list") || {}).key, groups.step_of("nope")],
	group_of: [!!groups.group_of("more"), groups.group_of("x")],
	refreshed,
};

// ---- the board's card parts
out.card = {
	parts: board.CARD_PARTS,
	defaults: board.DEFAULT_CARD,
	empty: board.card_parts(""),
	missing: board.card_parts(undefined),
	none: board.card_parts("none"),
	some: board.card_parts("meter, amount ,junk"),
	junk_only: board.card_parts("junk"),
	text_default: board.card_text(board.DEFAULT_CARD.slice().reverse()),
	text_none: board.card_text([]),
	text_some: board.card_text(["meter", "owner", "account"]),
	view_kind: board.VIEW_SCHEMA.card,
	picked: board.pick_view({ card: "owner,amount", view: "board" }),
};
// ---- review of #1299, S1: what is put away follows the record; filled-only keeps its snapshot
const status = def("custom_lost_detail", "Small Text");
const hidden_opts = (missing_now) => ({ hidden_fields: ["custom_lost_detail"], missing: new Set(missing_now), filled_only: false, reveal: false });
const snap = v.filled_snapshot([{ fields: [def("a", "Data"), def("b", "Data"), def("c", "Data", { reqd: 1 }), { fieldtype: "Column Break", fieldname: "cb" }] }], { a: "", b: "x", c: "" });
out.s1 = {
	before_status: v.field_hidden(status, "", hidden_opts([])).hidden,
	after_status: v.field_hidden(status, "", hidden_opts(["custom_lost_detail"])).hidden,
	snapshot: Array.from(snap).sort(),
	// "b" had a value when the snapshot was taken; emptied while typing, it stays on screen.
	emptied_while_typing: v.field_hidden(def("b", "Data"), "", { filled_only: true, filled_snapshot: snap }).hidden,
	still_empty: v.field_hidden(def("a", "Data"), "", { filled_only: true, filled_snapshot: snap }).hidden,
	filled_since: v.field_hidden(def("a", "Data"), "now", { filled_only: true, filled_snapshot: snap }).hidden,
	needed_since: v.field_hidden(def("a", "Data"), "", { filled_only: true, filled_snapshot: snap, missing: new Set(["a"]) }).hidden,
	revealed: v.field_hidden(def("website", "Data"), "x", { hidden_fields: ["website"], revealed: new Set(["website"]) }).hidden,
};

// ---- review of #1299, S2: One page reads back only the sections that show
globalThis.frappe = { session: { user: "u@example.com" }, boot: {} };
globalThis.document = { activeElement: null };
globalThis.__ = (text) => text;
const { RecordEditor } = await import(pathToFileURL(dir + "/form_kit/ui/editor.js").href);
const kit = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const S2_STEPS = [
	{ key: "basics", fields: [{ fieldname: "title", fieldtype: "Data" }] },
	{ key: "scope_design", applies: { field: "streams", row_field: "s", in: ["Design"] }, fields: [{ fieldname: "design_kind", fieldtype: "Select" }] },
	{ key: "more", fields: [{ fieldname: "source", fieldtype: "Select" }] },
];
// A Select with no blank option reads back its first option for a null value, as v16's does.
const control = (value) => ({ get_value: () => value });
const s2 = (visible_more, active_design) => {
	const ed = Object.create(RecordEditor.prototype);
	const changes = [];
	Object.assign(ed, {
		kit,
		flow: { steps: S2_STEPS },
		values: { title: "T", design_kind: null, source: null, streams: active_design ? [{ s: "Design" }] : [] },
		saved: { title: "T", design_kind: null, source: null, streams: active_design ? [{ s: "Design" }] : [] },
		defs: {},
		suppress: new Set(),
		bases: {},
		held: {},
		retired: [],
		failed_keys: new Set(),
		is_new: false,
		doctype: "Opportunity",
		name: "OPP-1",
		$root: [{ contains: () => false }],
		party_ui: { changed() {} },
		queue: { change: (f, value) => changes.push([f, value]), discard() {}, pending: () => ({}), in_flight: () => ({}), manual: false, record_key: "" },
		refresh_chrome() {},
		render_saveline() {},
		refresh_distinct() {},
		write_journal() {},
		step_visible: (step) => step.key !== "more" || visible_more,
	});
	const groups = new kit.FieldGroups();
	groups.add(S2_STEPS[0], { fields_dict: { title: control("T") }, refresh_dependency() {} });
	groups.add(S2_STEPS[1], { fields_dict: { design_kind: control("Concept") }, refresh_dependency() {} });
	groups.add(S2_STEPS[2], { fields_dict: { source: control("Website") }, refresh_dependency() {} });
	ed.fg = groups;
	ed.collect_step();
	return { changes, collected: ed.collect_steps().map((st) => st.key) };
};
out.s2 = { hidden: s2(false, false), shown: s2(true, true) };

// ---- re-review of #1299: a field the person is editing never goes away under them
const typed = new Set(["custom_billing_email"]);
const bill = def("custom_billing_email", "Data");
const person = (missing_now, revealed) => ({ hidden_fields: ["custom_billing_email"], missing: new Set(missing_now), revealed });
const snap2 = new Set(["notes"]);
const notes = def("notes", "Small Text");
const filled = (revealed) => ({ filled_only: true, filled_snapshot: snap2, missing: new Set(), revealed });
const sec = { key: "more", fields: [bill] };
out.typing = {
	// Shown only because the stage needs it; the person types; the save drops it from `missing`.
	needed: v.field_hidden(bill, "", person(["custom_billing_email"], null)).hidden,
	after_save_untouched: v.field_hidden(bill, "a@b.co", person([], null)).hidden,
	after_save_edited: v.field_hidden(bill, "a@b.co", person([], typed)).hidden,
	// Filled-only: in the snapshot, filled and saved, then cleared to retype with `missing` stale.
	cleared_untouched: v.field_hidden(notes, "", filled(null)).hidden,
	cleared_edited: v.field_hidden(notes, "", filled(new Set(["notes"]))).hidden,
	// A section the person put away keeps showing while a field in it was edited on this visit.
	section_untouched: v.step_hidden(sec, { custom_billing_email: "a@b.co" }, { hidden_steps: ["more"], missing: new Set() }),
	section_edited: v.step_hidden(sec, { custom_billing_email: "a@b.co" }, { hidden_steps: ["more"], missing: new Set(), revealed: typed }),
};
// The editor tells the shell about a real change only (collect_step re-reads unchanged values too).
const heard = [];
const ed2 = Object.create(RecordEditor.prototype);
Object.assign(ed2, {
	kit,
	flow: { steps: S2_STEPS },
	values: { title: "T" },
	saved: { title: "T" },
	bases: {},
	held: {},
	retired: [],
	failed_keys: new Set(),
	is_new: false,
	doctype: "Opportunity",
	name: "OPP-1",
	queue: { change() {}, discard() {}, pending: () => ({}), in_flight: () => ({}), manual: false, record_key: "" },
	refresh_chrome() {},
	render_saveline() {},
	write_journal() {},
	on_field_edited: (fieldname) => heard.push(fieldname),
});
ed2.set_value_local("title", "T");
ed2.set_value_local("title", "Typed");
out.heard = heard;
console.log(JSON.stringify(out));
"""


def _run_node():
	node = shutil.which("node")
	with tempfile.TemporaryDirectory() as tmp:
		folder = Path(tmp)
		(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
		shutil.copytree(FORM_KIT_JS, folder / "form_kit")
		shutil.copytree(BOARD_JS, folder / "opportunity_board")
		shutil.copytree(JS / "info_blocks", folder / "info_blocks")
		# v1.686.0: the editor mounts the record tools (ui/record_tools.js), which import the record
		# actions and the directory's party rules the Desk form shares with them.
		(folder / "global_enhancements").mkdir()
		for name in ("party_sources.js", "record_actions.js"):
			shutil.copy(JS / "global_enhancements" / name, folder / "global_enhancements" / name)
		(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
		proc = subprocess.run(
			[node, str(folder / "runner.mjs"), str(folder)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
	if proc.returncode != 0:
		raise AssertionError(f"node failed:\n{proc.stderr}")
	return json.loads(proc.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheDecisions(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.out = _run_node()

	def test_the_defaults_are_nik_s(self):
		d = self.out["defaults"]
		# "all sections on one page, but leave the ability for tabs to be used and visible"
		self.assertEqual(d["layout"], "page")
		self.assertEqual(d["nav"], "left")
		self.assertEqual(d["summary"], "right")
		self.assertEqual(d["notes"], "below")
		self.assertEqual(d["tools"], "side")
		self.assertEqual(d["focus"], "off")
		self.assertEqual(d["fields"], "all")

	def test_a_new_record_is_always_the_setup_flow(self):
		self.assertEqual(self.out["setup"]["layout"], "steps")
		self.assertEqual(self.out["effective_new"], self.out["setup"])
		self.assertEqual(self.out["effective_saved"], "tabs")
		# A DocType with step blocks (the Item flow, P8.1) stays step by step; its other choices apply.
		self.assertEqual(self.out["effective_blocks"], ["steps", "top"])
		# Apart from the layout, the setup flow is the defaults, which are the page as it was.
		setup = dict(self.out["setup"])
		setup.pop("layout")
		defaults = dict(self.out["defaults"])
		defaults.pop("layout")
		self.assertEqual(setup, defaults)

	def test_unknown_and_invalid_fall_back(self):
		clean = self.out["clean"]
		self.assertEqual(clean["layout"], "tabs")
		self.assertEqual(clean["nav"], "left")
		self.assertEqual(clean["focus"], "on")
		self.assertEqual(clean["text"], "normal")
		self.assertNotIn("junk", clean)
		self.assertEqual(self.out["clean_null"], self.out["defaults"])
		self.assertEqual(self.out["store_missing"], "page")

	def test_an_entry_is_cleaned(self):
		e = self.out["entry"]
		self.assertEqual(e["prefs"]["layout"], "steps")
		self.assertEqual(e["pinned"], ["close", "budget"])
		self.assertEqual(e["landing"], "last")
		self.assertEqual(e["hidden_fields"], ["website"])
		self.assertEqual(e["summary_fields"], ["status"])
		self.assertEqual([p["name"] for p in e["presets"]], ["Phone"])
		self.assertEqual(e["presets"][0]["prefs"]["density"], "compact")
		self.assertEqual(e["last"], [["A", "close"], ["C", "basics"]])
		none = self.out["entry_none"]
		self.assertIsNone(none["summary_fields"], "null means the DocType's own summary")
		self.assertEqual(none["pinned"], [])

	def test_the_shell_attributes(self):
		a = self.out["attrs_default"]
		self.assertEqual((a["layout"], a["nav"], a["aside"]), ("page", "left", "right"))
		self.assertEqual(self.out["attrs_hidden_summary"], "none")
		self.assertEqual(self.out["attrs_notes_side"], "right")
		self.assertEqual(self.out["attrs_left"], "left")
		f = self.out["attrs_focus"]
		# Focus mode: only the form and its nav; notes and tools behind their bars, never gone.
		self.assertEqual((f["aside"], f["summary"], f["hints"]), ("none", "hidden", "hidden"))
		self.assertEqual((f["notes"], f["tools"]), ("collapsed", "collapsed"))

	def test_tabs_group_the_steps_under_their_desk_tab(self):
		tabs = self.out["tabs"]
		self.assertEqual(
			[t[1] for t in tabs],
			["Details", "Contacts & Addresses", "Scope", "Schedule", "Budget", "More details"],
		)
		self.assertEqual(tabs[0][2], ["basics", "close"])
		self.assertEqual(tabs[2][2], ["scope", "scope_design-off"])
		self.assertEqual(self.out["tab_of"], ["Details", None])
		self.assertEqual(self.out["tab_step"], ["scope", None])
		self.assertEqual(self.out["neighbor_tab"], ["Contacts & Addresses", None, None])
		self.assertEqual(self.out["tab_missing"], 3)

	def test_pinned_steps_draw_first_and_once(self):
		self.assertEqual(self.out["pinned_group"], ["pinned", "Pinned", ["budget", "close"], True])
		self.assertIsNone(self.out["pinned_none"])
		labels = [t[0] for t in self.out["pinned_tabs"]]
		self.assertNotIn("Budget", labels, "a tab whose only step is pinned has nothing left to draw")
		self.assertEqual(self.out["pinned_tabs"][0], ["Details", ["basics"]])
		page = self.out["section_keys_page"]
		self.assertEqual(page[:2], ["budget", "close"])
		self.assertEqual(len(page), len(set(page)), "never twice")
		self.assertEqual(self.out["section_keys_tabs"], ["budget", "close", "contact"])
		self.assertEqual(self.out["toggle"], [["a", "b"], ["a", "b"], ["b"]])

	def test_scroll_spy_and_next_section(self):
		self.assertEqual(self.out["spy"], ["b", "a", None, None])
		self.assertEqual(self.out["neighbor_section"], ["c", None, None, "a"])

	def test_the_last_section_per_record_is_capped(self):
		self.assertEqual(self.out["last_len"], 50)
		self.assertEqual(self.out["last_first"], ["R59", "budget"])
		self.assertIsNone(self.out["last_dropped"])
		self.assertEqual(self.out["last_moved"], [["R30", "close"], 1])

	def test_where_a_record_opens(self):
		# Where they left it; not a step that no longer applies; nothing remembered; a chosen step;
		# "first"; a chosen step that is gone.
		self.assertEqual(self.out["start"], ["close", None, None, "budget", None, None])

	def test_what_may_be_put_away(self):
		self.assertEqual(self.out["has_value"], [False, True, False, True, False, True, False, True, False])
		self.assertEqual(self.out["must_show"], [True, False, True, True, False])
		fh = [(r["hidden"], r["by"]) for r in self.out["field_hidden"]]
		self.assertEqual(fh[0], (True, "person"))
		self.assertEqual(fh[1], (False, None), "required and empty: always shown")
		self.assertEqual(fh[2], (True, "person"), "required but filled: the person may put it away")
		self.assertEqual(fh[3], (False, None), "the stage still needs it")
		self.assertEqual(fh[4], (False, None), "Show hidden")
		self.assertEqual(fh[5], (True, "filled"))
		self.assertEqual(fh[6], (False, None))
		self.assertEqual(fh[7], (False, None), "filled-only never hides what the stage needs")
		self.assertEqual(fh[8], (False, None), "filled-only never hides a required field")
		self.assertEqual(self.out["step_hidden"], [True, False, False, False])
		self.assertEqual(self.out["can_hide"], [True, False])

	def test_presets(self):
		self.assertEqual(self.out["preset_saved"], [True, "My setup", "tabs"])
		self.assertEqual(self.out["preset_replaced"], [1, "my setup", "steps"])
		self.assertEqual(self.out["preset_noname"], "name")
		self.assertEqual(self.out["preset_full"], ["full", True])
		self.assertEqual(self.out["preset_delete"], 9)
		self.assertEqual(self.out["preset_current"], ["A", None])

	def test_shortcuts_never_fire_while_typing_except_save(self):
		self.assertEqual(
			self.out["shortcuts"],
			[
				"next",
				None,
				"prev",
				"save",
				"save",
				None,
				"help",
				None,
				"focus",
				None,
				None,
				"settings",
				"comments",
				None,
				None,
			],
		)
		self.assertEqual(
			set(self.out["shortcut_actions"]),
			{"next", "prev", "save", "comments", "settings", "focus", "help"},
		)

	def test_what_is_put_away_follows_the_record(self):
		# Review of #1299, S1: a field the person hid comes back once the stage needs it, and
		# "only fields with a value" keeps its snapshot, so a field emptied while typing stays.
		s1 = self.out["s1"]
		self.assertTrue(s1["before_status"])
		self.assertFalse(s1["after_status"])
		self.assertEqual(s1["snapshot"], ["a"])
		self.assertFalse(s1["emptied_while_typing"])
		self.assertTrue(s1["still_empty"])
		self.assertFalse(s1["filled_since"])
		self.assertFalse(s1["needed_since"])
		self.assertFalse(s1["revealed"])

	def test_one_page_never_reads_a_section_nobody_sees(self):
		# Review of #1299, S2: a Select reads back its first option for null; drawn in a section
		# that does not apply or is not showing, it must queue nothing.
		hidden = self.out["s2"]["hidden"]
		self.assertEqual(hidden["collected"], ["basics"])
		self.assertEqual(hidden["changes"], [], "a layout is only a presentation")
		shown = self.out["s2"]["shown"]
		self.assertEqual(shown["collected"], ["basics", "scope_design", "more"])
		# The same controls on screen do queue: the check above is not vacuous.
		self.assertEqual(sorted(c[0] for c in shown["changes"]), ["design_kind", "source"])

	def test_a_field_being_edited_never_goes_away(self):
		# Re-review of #1299. Before the fix each "untouched" case below is what the page did to the
		# field under the cursor: shown only because the stage needed it, typed in, autosaved, and
		# put away the moment the save took it off `missing`.
		t = self.out["typing"]
		self.assertFalse(t["needed"])
		self.assertTrue(t["after_save_untouched"], "the regression this guards against")
		self.assertFalse(t["after_save_edited"])
		self.assertTrue(t["cleared_untouched"])
		self.assertFalse(t["cleared_edited"])
		self.assertTrue(t["section_untouched"])
		self.assertFalse(t["section_edited"])
		self.assertEqual(self.out["heard"], ["title"], "only a real change counts as an edit")

	def test_field_groups_answer_for_every_section(self):
		g = self.out["groups"]
		self.assertTrue(g["multi"])
		self.assertEqual(g["fields"], ["notes__ge_list", "status", "title", "website"])
		self.assertEqual(g["steps"], ["basics", "more"])
		self.assertEqual(g["owns"], [True, False])
		self.assertEqual(g["step_of"], ["more", "more", None])
		self.assertEqual(g["group_of"], [True, None])
		self.assertEqual(g["refreshed"], ["basics", "more"], "a change refreshes every section's conditions")

	def test_board_cards_keep_today_s_fields_unless_removed(self):
		c = self.out["card"]
		self.assertEqual(c["empty"], c["defaults"])
		self.assertEqual(c["missing"], c["defaults"], "a view saved before cards could be chosen")
		self.assertEqual(c["junk_only"], c["defaults"])
		self.assertEqual(c["none"], [])
		self.assertEqual(c["some"], ["amount", "meter"])
		self.assertEqual(c["text_default"], "")
		self.assertEqual(c["text_none"], "none")
		self.assertEqual(c["text_some"], "account,owner,meter")
		self.assertEqual(c["view_kind"], ["text"])
		self.assertEqual(c["picked"], {"view": "board", "card": "owner,amount"})
		# What a card showed before v1.684.0, every part of it.
		self.assertEqual(set(c["defaults"]), {"account", "streams", "amount", "close", "age", "meter"})

	def test_the_server_schema_is_the_pages(self):
		schema = _literal("GUIDED_SCHEMA")
		self.assertEqual({k: list(v) for k, v in schema.items()}, self.out["schema"])
		caps = _literal("GUIDED_MAX")
		self.assertEqual(
			{k: caps[k] for k in ("pinned", "hidden", "summary_fields", "presets", "preset_name", "last")},
			self.out["caps"],
		)
		self.assertEqual(list(_literal("GUIDED_LANDINGS")), self.out["landings"])


# ---------------------------------------------------------------------------------- the store

_saved_modules = {}
_modules_before = set()


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _FakeTable:
	"""``__UserSettings`` as a dict, with the two statements the module runs."""

	def __init__(self):
		self.rows = {}
		self.writes = []

	def sql(self, query, values=None, **kwargs):
		if "from `__UserSettings`" in query:
			text = self.rows.get((values[0], values[1]))
			return [(text,)] if text is not None else []
		return []

	def multisql(self, sql_dict, values=(), **kwargs):
		self.writes.append((sql_dict, values))
		user, doctype, text, again = values
		assert text == again
		self.rows[(user, doctype)] = text


STATE = {}


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _has_permission(doctype, ptype="read", doc=None, user=None, throw=False):
	STATE["perm"].append((doctype, ptype))
	if STATE.get("deny"):
		if throw:
			raise _PermissionError("no")
		return False
	return True


def _install():
	global _modules_before
	_modules_before = set(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(
			("frappe.", "erpnext_enhancements.api", "erpnext_enhancements.form_kit")
		):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-10-14 09:00:00"
	utils.format_datetime = lambda *a, **k: "Oct 14, 2026"
	utils.cint = lambda value: int(float(value or 0))
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.has_permission = _has_permission
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	return frappe


def _uninstall():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(
			("frappe.", "erpnext_enhancements.api", "erpnext_enhancements.form_kit")
		):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)
	_saved_modules.clear()


def _literal(name):
	"""A module-level constant of planner_views_prefs.py, read without importing it (it imports frappe)."""
	for node in ast.parse(_text(PREFS_PY)).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"no {name}")


class TestTheStore(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.frappe = _install()
		cls.prefs = importlib.import_module(PREFS)

	@classmethod
	def tearDownClass(cls):
		_uninstall()

	def setUp(self):
		f = self.frappe
		STATE.clear()
		STATE["perm"] = []
		f.table = _FakeTable()
		f.dropped = []
		f.session = types.SimpleNamespace(user=USER)
		f.cache = types.SimpleNamespace(hdel=lambda name, key: f.dropped.append((name, key)))
		f.db = types.SimpleNamespace(sql=f.table.sql, multisql=f.table.multisql)

	def _row(self, user=USER):
		text = self.frappe.table.rows.get((user, "Guided Entry"))
		return json.loads(text) if text else None

	def test_nothing_stored_reads_as_defaults_and_unseen(self):
		self.assertEqual(self.prefs.get_guided_view(), {"intro_seen": False, "doctypes": {}})

	def test_a_save_replaces_only_the_parts_sent(self):
		self.prefs.save_guided_view("Opportunity", json.dumps({"prefs": {"layout": "tabs", "nav": "nope"}}))
		self.prefs.save_guided_view(
			"Opportunity", {"pinned": ["close", "close", "bad key"], "landing": "budget"}
		)
		entry = self.prefs.get_guided_view()["doctypes"]["Opportunity"]
		self.assertEqual(entry["prefs"]["layout"], "tabs", "kept from the first save")
		self.assertEqual(entry["prefs"]["nav"], "left", "an invalid value falls back")
		self.assertEqual(entry["pinned"], ["close"])
		self.assertEqual(entry["landing"], "budget")
		self.assertIn(("Opportunity", "read"), STATE["perm"])
		# Straight to the table, then the cached copy dropped.
		self.assertEqual(len(self.frappe.table.writes), 2)
		self.assertIn(("_user_settings", f"Guided Entry::{USER}"), self.frappe.dropped)
		self.assertEqual(self._row()["guided_view"]["version"], 1)

	def test_only_the_caller_s_row(self):
		self.prefs.save_guided_view("Opportunity", {"prefs": {"layout": "steps"}})
		self.frappe.session.user = "lisa@example.com"
		self.assertEqual(
			self.prefs.get_guided_view()["doctypes"], {}, "one person's choices are theirs alone"
		)
		self.prefs.save_guided_view("Opportunity", {"prefs": {"layout": "tabs"}})
		self.assertEqual(
			self._row(USER)["guided_view"]["doctypes"]["Opportunity"]["prefs"]["layout"], "steps"
		)
		self.assertEqual(
			self._row("lisa@example.com")["guided_view"]["doctypes"]["Opportunity"]["prefs"]["layout"], "tabs"
		)

	def test_the_rest_of_the_row_is_kept(self):
		self.frappe.table.rows[(USER, "Guided Entry")] = json.dumps({"something_else": {"a": 1}})
		self.prefs.mark_guided_intro_seen()
		row = self._row()
		self.assertEqual(row["something_else"], {"a": 1})
		self.assertTrue(row["guided_view"]["intro_seen"])
		writes = len(self.frappe.table.writes)
		self.prefs.mark_guided_intro_seen()
		self.assertEqual(len(self.frappe.table.writes), writes, "seen once is seen: nothing to write")
		self.assertTrue(self.prefs.get_guided_view()["intro_seen"])

	def test_a_doctype_the_page_does_not_serve_is_refused(self):
		with self.assertRaises(_Throw):
			self.prefs.save_guided_view("User", {"prefs": {}})
		with self.assertRaises(_Throw):
			self.prefs.remember_guided_section("Customer", "X", "basics")
		self.assertEqual(self.frappe.table.writes, [])

	def test_read_permission_is_required(self):
		STATE["deny"] = True
		with self.assertRaises(_PermissionError):
			self.prefs.save_guided_view("Opportunity", {"prefs": {}})

	def test_the_last_section_keeps_fifty_records(self):
		for i in range(55):
			self.prefs.remember_guided_section("Opportunity", f"CRM-OPP-{i:05d}", "budget")
		last = self.prefs.get_guided_view()["doctypes"]["Opportunity"]["last"]
		self.assertEqual(len(last), 50)
		self.assertEqual(last[0], ["CRM-OPP-00054", "budget"])
		self.prefs.remember_guided_section("Opportunity", "CRM-OPP-00010", "close")
		last = self.prefs.get_guided_view()["doctypes"]["Opportunity"]["last"]
		self.assertEqual(last[0], ["CRM-OPP-00010", "close"])
		self.assertEqual(len([p for p in last if p[0] == "CRM-OPP-00010"]), 1)
		with self.assertRaises(_Throw):
			self.prefs.remember_guided_section("Opportunity", "X", "not a key!")

	def test_caps_and_size(self):
		self.prefs.save_guided_view(
			"Opportunity",
			{
				"pinned": [f"s{i}" for i in range(40)],
				"summary_fields": [f"f{i}" for i in range(30)],
				"presets": [{"name": f"P{i}", "prefs": {}} for i in range(15)],
			},
		)
		entry = self.prefs.get_guided_view()["doctypes"]["Opportunity"]
		self.assertEqual(len(entry["pinned"]), 20)
		self.assertEqual(len(entry["summary_fields"]), 12)
		self.assertEqual(len(entry["presets"]), 10)
		with self.assertRaises(_Throw):
			self.prefs.save_guided_view("Opportunity", json.dumps({"hidden_fields": ["x" * 100] * 200}))
		with self.assertRaises(_Throw):
			self.prefs.save_guided_view("Opportunity", "[1, 2]")

	def test_a_hand_edited_row_is_cleaned_on_the_way_out(self):
		self.frappe.table.rows[(USER, "Guided Entry")] = json.dumps(
			{
				"guided_view": {
					"intro_seen": "yes",
					"doctypes": {"Opportunity": {"prefs": {"layout": "<b>"}, "pinned": "close", "junk": 1}},
				}
			}
		)
		store = self.prefs.get_guided_view()
		self.assertFalse(store["intro_seen"], "only a real true counts")
		entry = store["doctypes"]["Opportunity"]
		self.assertEqual(entry["prefs"]["layout"], "page")
		self.assertEqual(entry["pinned"], [])
		self.assertNotIn("junk", entry)

	def test_reset_of_summary_fields_is_null(self):
		self.prefs.save_guided_view("Opportunity", {"summary_fields": ["status"]})
		self.prefs.save_guided_view("Opportunity", {"summary_fields": None})
		self.assertIsNone(self.prefs.get_guided_view()["doctypes"]["Opportunity"]["summary_fields"])

	def test_the_board_card_is_a_text_view_key(self):
		self.assertEqual(self.prefs.SCHEMA["board"]["card"], ("text",))
		view = self.prefs.clean_view("board", {"card": " owner,amount ", "view": "board"})
		self.assertEqual(view, {"card": "owner,amount", "view": "board"})


class TestTheEndpoints(unittest.TestCase):
	def test_reads_are_get_and_writes_are_post(self):
		tree = ast.parse(_text(PREFS_PY))
		fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		decorators = {name: [ast.unparse(d) for d in fn.decorator_list] for name, fn in fns.items()}
		self.assertEqual(decorators["get_guided_view"], ["frappe.whitelist()"])
		for name in ("save_guided_view", "remember_guided_section", "mark_guided_intro_seen"):
			with self.subTest(endpoint=name):
				self.assertEqual(decorators[name], ["frappe.whitelist(methods=['POST'])"])

	def test_never_frappe_s_redis_only_user_settings(self):
		# The code only: the docstring says why it is not used, by name.
		tree = ast.parse(_text(PREFS_PY))
		for node in ast.walk(tree):
			body = getattr(node, "body", None)
			if (
				isinstance(body, list)
				and body
				and isinstance(body[0], ast.Expr)
				and isinstance(body[0].value, ast.Constant)
			):
				body.pop(0)
		code = ast.unparse(tree)
		self.assertNotIn("user_settings.save", code)
		self.assertNotIn("model.utils.user_settings", code)
		self.assertNotIn("frappe.defaults", code)

	def test_the_page_calls_them_quietly(self):
		code = _code(PAGE_JS)
		self.assertIn('const GE_PREFS = "erpnext_enhancements.api.planner_views_prefs.";', code)
		self.assertIn('GE_PREFS + "get_guided_view", {}, { quiet: true, method: "GET" }', code)
		for method in ("save_guided_view", "mark_guided_intro_seen"):
			with self.subTest(method=method):
				self.assertRegex(code, rf'GE_PREFS \+ "{method}"[^;]*\{{ quiet: true \}}')
		self.assertIn('const method = GE_PREFS + "remember_guided_section";', code)
		for forbidden in ("frappe.call(", "frappe.xcall(", "fetch(", "$.ajax(", "localStorage"):
			self.assertNotIn(forbidden, code)


# ---------------------------------------------------------------------------------- fidelity


class TestOneRendererInEveryLayout(unittest.TestCase):
	def test_sections_and_steps_draw_through_render_fields(self):
		page = _code(PAGE_JS)
		self.assertIn(
			"this.render_fields(step, $fields, editable);", _method(page, "\trender_section(step, $group) {")
		)
		self.assertIn("this.render_fields(step, $fields, editable);", _method(page, "\trender_step(key) {"))
		self.assertNotIn("new frappe.ui.FieldGroup", page, "the page never draws fields of its own")
		self.assertIn("this.begin_groups();", _method(page, "\trender_sections(mode, tab_key) {"))

	def test_the_editor_reads_every_section_on_screen(self):
		editor = _code(EDITOR_JS)
		for head in ("\tcollect_step() {", "\tcollect_tables() {"):
			with self.subTest(method=head):
				self.assertIn("this.collect_steps()", _method(editor, head))
		collect = _method(editor, "\tcollect_steps() {")
		self.assertIn("this.kit.step_active(step, this.values) && this.step_visible(step)", collect)
		page = _code(PAGE_JS)
		self.assertIn('!$sec.prop("hidden")', _method(page, "\tstep_visible(step) {"))
		# S1: every refresh asks again what is put away.
		self.assertIn("this.apply_visibility(false);", _method(page, "\trefresh_chrome() {"))
		# ...but never from under the cursor, and an edited field stays for the visit.
		apply = _method(page, "\tapply_visibility(refresh_filled = true) {")
		self.assertIn("$w[0].contains(document.activeElement)", apply)
		self.assertIn("this.on_field_edited(field.fieldname);", apply)
		self.assertIn("!el.contains(document.activeElement)", _method(page, "\trefresh_section_states() {"))
		self.assertIn("this.revealed.add(fieldname);", _method(page, "\ton_field_edited(fieldname) {"))
		self.assertIn(
			"this.on_field_edited(fieldname)",
			_method(editor, "\tset_value_local(fieldname, value, quiet_chrome) {"),
		)
		self.assertIn("if (this.fg && this.fg.multi) this.fg.add(step, fg);", editor)
		self.assertIn("this.before_focus(fieldname);", _method(editor, "\tfocus_field(fieldname) {"))

	def test_a_new_record_keeps_the_setup_flow(self):
		page = _code(PAGE_JS)
		self.assertIn(
			"this.view = this.kit.effective_view(this.entry.prefs, this.in_setup(), this.steps_only());", page
		)
		in_setup = _method(page, "\tin_setup() {")
		self.assertIn("if (this.is_new) return true;", in_setup)
		self.assertIn("return !this.is_new && !!this.flow", _method(page, "\tsections_mode() {"))
		# The first-time pop-up never shows on a setup flow, and never twice.
		intro = _method(page, "\tmaybe_intro(tries) {")
		self.assertIn("this.in_setup()", intro)
		self.assertIn("this.view_store.intro_seen = true;", intro)
		self.assertLess(intro.index("this.view_store.intro_seen = true;"), intro.index("this.open_overlay("))

	def test_the_intro_waits_for_a_decision_on_screen(self):
		# Nit 4: never over the kept-changes chooser, a conflict or a dialog (the Won nudge).
		intro = _method(_code(PAGE_JS), "\tmaybe_intro(tries) {")
		self.assertIn("!!this.conflict", intro)
		self.assertIn('document.querySelector(".modal.show, .ge-overlay")', intro)
		self.assertIn("!!this.$overlay", intro)
		self.assertLess(intro.index("busy"), intro.index("this.view_store.intro_seen = true;"))

	def test_record_tools_are_offered_once_they_exist(self):
		# Nit 2: the choice shows only when something has mounted into .ge-tools.
		page = _code(PAGE_JS)
		main = _method(page, "\trender_settings_main($body) {")
		self.assertIn("if (this.tools_present()) {", main)
		self.assertLess(main.index("if (this.tools_present()) {"), main.index('group("tools"'))

	def test_the_side_panel_stays_step_by_step(self):
		panel = _code(PANEL_JS)
		for absent in ("begin_groups", "view_settings", "GE_PREFS", "shell_attrs"):
			with self.subTest(absent=absent):
				self.assertNotIn(absent, panel)

	def test_hints_are_never_lost(self):
		editor = _code(EDITOR_JS)
		wants = _method(editor, "\twants_help(field) {")
		self.assertIn("this.hints_hidden", wants)
		# Nit 1: the time-zone line lives in the hint's box; with hints put away the (i) carries it.
		self.assertIn('field.fieldtype === "Datetime" && !this.same_time_zone()', wants)
		self.assertIn("GE_HINT_CLAMP", wants)
		self.assertIn("ge-help-hint", _method(editor, "\tadd_help($wrapper, field, step) {"))


# ---------------------------------------------------------------------------------- wiring


class TestTheStylesheet(unittest.TestCase):
	def test_tokens_only_and_attributes_not_inline_styles(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", _text(CSS))
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css))
		self.assertNotIn("rgb(", css)
		self.assertNotIn("shadow", css.lower())
		for attr in (
			"data-ge-nav",
			"data-ge-aside",
			"data-ge-density",
			"data-ge-hints",
			"data-ge-text",
			"data-ge-contrast",
		):
			with self.subTest(attr=attr):
				self.assertIn(attr, css)
		page = _code(PAGE_JS)
		self.assertNotIn("<style", page)
		self.assertNotIn("insertRule", page)

	def test_the_tightened_fields(self):
		css = _text(CSS)
		self.assertIn("grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));", css)
		self.assertIn("-webkit-line-clamp: 2;", css)
		self.assertIn(".ge-fields .form-group > .clearfix > .ge-help-btn", css)
		editor = _code(EDITOR_JS)
		self.assertIn("df.hide_timezone = 1;", editor)
		self.assertIn("GE_TEXT_MAX", _method(editor, "\tfit_text(el) {"))


class TestWiring(unittest.TestCase):
	def test_wired_into_ci_on_its_own_step(self):
		ci = _text(CI)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_guided_view_settings", ci)

	def test_the_readme_documents_it(self):
		readme = _text(README)
		self.assertIn("## View settings (v1.684.0)", readme)
		for word in (
			"get_guided_view",
			"save_guided_view",
			"remember_guided_section",
			"mark_guided_intro_seen",
		):
			with self.subTest(word=word):
				self.assertIn(word, readme)

	def test_the_board_draws_only_the_chosen_parts(self):
		page = _code(BOARD_PAGE_JS)
		card = _method(page, "\tcard_html(deal, also) {")
		self.assertIn("const parts = new Set(this.card_parts());", card)
		for part in ("account", "streams", "amount", "close", "age", "meter", "owner", "probability"):
			with self.subTest(part=part):
				self.assertIn(f'parts.has("{part}")', card)
		self.assertIn("card: this.kit.card_text(this.card_parts()),", page)


if __name__ == "__main__":
	unittest.main()
