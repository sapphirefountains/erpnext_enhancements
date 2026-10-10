# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Planner Phase 6D UI (TASK-2026-02470): the Conflict center, personal blocks and day notes on both
planners' pages and in My week, built on the Phase 6D backend and the Phase 6A planner kit.

What this pins, because each part fails quietly:

* **Every fix goes through the page's own write path.** A Conflict-center fix is exactly the method
  and arguments the server worked out (``api/planner_conflicts.fixes_for``), and the page sends it
  through ``commit()`` / ``send()`` like a drag, so the reason prompt, Draft mode (``draft=1``) and
  Undo apply. Each page may run only its own writes; every method the backend can name is one of
  them, and the kit never calls a fix's method itself.
* **"Pick someone who's free" never picks.** The picker lists who_is_free, free people first and busy
  ones greyed with their reason, with nothing selected; a pick fix without the person tapped has no
  arguments at all.
* **Keeping a conflict needs a reason**: a blank one never reaches the server.
* **A block's note shows only from block_notes.** The renderers take the note only from what the
  page passes; the page reads it only from the payload's ``block_notes`` (and, in My week, from
  ``get_my_week``'s own blocks, which the server filled through the same reader).
* **Blocks are never dragged**, **My week blocks only your own time** (the form sends no person),
  and **everything people typed is escaped** (the notes row above all).
* **``off_label``**: a day with an all-day block reads "Unavailable", and a time-off reason still
  never passes through.

Bench-free: the off_label part installs its own ``frappe`` stub and puts ``sys.modules`` back; the
renderers and the two forms run under node (skipped when node is not on PATH).

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6d_ui
"""

import ast
import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
KIT_DIR = APP / "public/js/planner_kit"
BUNDLE = APP / "public/js/planner_kit.bundle.js"
BLOCKS_API = APP / "api/planner_blocks.py"
CONFLICTS_API = APP / "api/planner_conflicts.py"
PP_API = APP / "api/project_planner.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"
PE_README = APP / "project_enhancements/README.md"
SM_README = APP / "sapphire_maintenance/README.md"
API_README = APP / "api/README.md"

MARKER = "// ====================================================================== Phase 6D"
RULE = "\n// ======================================================================"

# The planner_blocks and planner_conflicts methods the pages and the kit call, and every argument
# they send (test_project_planner_page.API_CONTRACT is the Project Planner's own module; these live in
# their own modules, as test_planner_phase6a.VIEWS_CONTRACT does for planner_views).
BLOCKS_CONTRACT = {
	"save_block": {"date", "all_day", "from_time", "to_time", "name", "resource", "note"},
	"delete_block": {"name"},
	"save_day_note": {"date", "note", "audience", "project", "name"},
	"delete_day_note": {"name"},
	"get_day_notes": {"start", "end"},
}
CONFLICTS_CONTRACT = {
	"get_conflicts": {"start", "end", "planner"},
	"acknowledge_conflict": {"key", "reason", "items", "planner", "fingerprint"},
}
# The Project Planner endpoint the kit's picker calls (the fix names it; the kit checks it is this one).
KIT_PP_CONTRACT = {"who_is_free": {"start", "end", "hours"}}
WRITES = {"save_block", "delete_block", "save_day_note", "delete_day_note", "acknowledge_conflict"}

_saved_modules = {}
_modules_before = set()
views = None


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 10, 9)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or Exception)(message)


def setUpModule():
	"""A frappe stub just big enough to import api/planner_views (for off_label)."""
	global views
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.nowdate = lambda: "2026-10-09"
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 9, 7, 0, 0)
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.get_traceback = lambda: ""
	frappe.log_error = lambda *a, **k: None
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[], flags=types.SimpleNamespace())
	frappe.flags = types.SimpleNamespace()
	frappe.get_roles = lambda user=None: []
	frappe.get_all = lambda *a, **k: []
	frappe.get_system_settings = lambda key: "Sunday"
	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		exists=lambda *a, **k: False,
		has_column=lambda *a, **k: True,
		get_single_value=lambda *a, **k: None,
	)
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	views = importlib.import_module("erpnext_enhancements.api.planner_views")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- source helpers


def _strip(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _block(code):
	"""The Phase 6D block of a page: its banner to the next banner (or the end of the file)."""
	start = code.index(MARKER)
	end = code.find(RULE, start + len(MARKER))
	return code[start:] if end < 0 else code[start:end]


def _obj_method(block, name):
	"""One method of a mixin object (``\\tname(...) {`` to ``\\t},``)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}},\n", block, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


def _class_method(code, name):
	"""One method of a page's class (``\\tname(...) {`` to ``\\t}``)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}}\n", code, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


def _js_function(code, name):
	"""A nested ``function name(...) {`` of a kit module, to its closing brace at the same indent."""
	match = re.search(rf"^(\t+)function {name}\(.*?\) \{{\n(.*?)^\1\}}\n", code, flags=re.S | re.M)
	assert match, f"no function {name}"
	return match.group(2)


def _functions(path):
	return {
		n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)
	}


def _kit(name):
	return (KIT_DIR / f"{name}.js").read_text(encoding="utf-8")


PP = PP_JS.read_text(encoding="utf-8")
MP = MP_JS.read_text(encoding="utf-8")
PP_BLOCK = _block(PP)
MP_BLOCK = _block(MP)


# ====================================================================== wiring


class TestHooks(unittest.TestCase):
	def test_the_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase6d();",
			"this.render_phase6d();",
			"...this.p6d_day_peek_opts(),",
			"...this.p6d_legend_sections(),",
		):
			self.assertEqual(PP.count(line), 1, line)
			self.assertEqual(MP.count(line), 1, line)
		self.assertEqual(PP.count("actions: this.p6d_person_actions(resource),"), 1)
		self.assertEqual(MP.count("actions: this.p6d_person_actions(user),"), 1)
		for line in (
			"this.p6d_set_mode(mode);",
			"this.p6d_state(day, state);",
			'if (booking.kind === "block") return this.p6d_block_html(resource, ymd, booking);',
			"${this.p6d_day_html(ymd, resources)}",
			"html.push(this.p6d_crew_notes_row(days));",
			"this.p6d_my_week_day($day, $dh, entry, data);",
			"Object.assign(ProjectPlanner.prototype, PP6D_METHODS);",
		):
			self.assertEqual(PP.count(line), 1, line)
		for line in (
			'if (off === "Unavailable") return __("Unavailable");',
			"this.p6d_day_head($day, ymd);",
			"html.push(this.p6d_crew_notes_row(days));",
			"${this.p6d_cell_blocks(person, ymd)}",
			"Object.assign(MaintenancePlanner.prototype, MP6D_METHODS);",
		):
			self.assertEqual(MP.count(line), 1, line)
		# Each block is the last one in its file, after Phase 6A's.
		self.assertGreater(
			PP.index(MARKER), PP.index("Object.assign(ProjectPlanner.prototype, PP6A_METHODS);")
		)
		self.assertGreater(
			MP.index(MARKER), MP.index("Object.assign(MaintenancePlanner.prototype, MP6A_METHODS);")
		)

	def test_the_kit_is_the_one_loaded_by_frappe_require(self):
		for block, constant in ((PP_BLOCK, "PP6A"), (MP_BLOCK, "MP6A")):
			self.assertIn(f"frappe.require({constant}.kit, () => this.p6d_ready());", block)
			self.assertIn("kit.conflicts.center({", block)
		bundle = BUNDLE.read_text(encoding="utf-8")
		for name in ("conflicts", "blocks"):
			self.assertIn(f'import * as {name} from "./planner_kit/{name}.js";', bundle)
		self.assertIn("conflicts: Object.assign(conflicts.create_conflicts(env), {", bundle)
		self.assertIn("blocks: Object.assign(blocks.create_blocks(env), {", bundle)
		self.assertIn("style.textContent = CSS + conflicts.CONFLICTS_CSS + blocks.BLOCKS_CSS;", bundle)

	def test_the_menu_registry_and_a_way_in_without_the_menu(self):
		for block in (PP_BLOCK, MP_BLOCK):
			self.assertIn("this.p6_menu_providers = this.p6_menu_providers || [];", block)
			self.assertIn("this.p6_menu_providers.push((target) => this.p6d_menu_items(target));", block)
			items = _obj_method(block, "p6d_menu_items")
			for label in ("Show conflicts", "Conflicts this day", "Block time…", "Add a day note…"):
				self.assertIn(f'__("{label}")', items)
			self.assertIn('target.kind === "card"', items)
			self.assertIn('target.kind === "cell"', items)
			self.assertIn('target.kind === "person"', items)
			# 6B's menu is not on this branch: every action also has a button of its own.
			init = _obj_method(block, "init_phase6d")
			self.assertIn('.text(__("Conflicts"))', init)
			self.assertIn('.text(__("Block time"))', init)
			self.assertIn('label: __("Block time…")', _obj_method(block, "p6d_person_actions"))
			self.assertIn('label: __("Add a note")', _obj_method(block, "p6d_day_peek_opts"))
			self.assertIn('target.closest("[data-pk-note-add]")', _obj_method(block, "p6d_click"))
			self.assertIn('target.closest("[data-p6d-conflict]")', _obj_method(block, "p6d_click"))
		self.assertIn("Block my time", PP_BLOCK)

	def test_one_conflict_read_per_load_not_per_render(self):
		for block in (PP_BLOCK, MP_BLOCK):
			render = _obj_method(block, "render_phase6d")
			self.assertIn("if (center && this.p6d.counted !== this.data) {", render)
			self.assertIn("center.schedule();", render)
			self.assertNotIn("get_conflicts", _strip(block))
		conflicts = _strip(_kit("conflicts"))
		schedule = _js_function(conflicts, "schedule")
		self.assertIn("env.clear_timeout(state.timer);", schedule)
		self.assertIn("COUNT_MS", schedule)
		# The background read never opens a dialog on a failure.
		self.assertIn(
			"call(METHODS.get, { start: now.start, end: now.end, planner: opts.planner }, { silent: true })",
			conflicts,
		)
		self.assertIn("planner: PP6D.planner,", PP_BLOCK)
		self.assertIn('planner: "project",', PP_BLOCK)
		self.assertIn('planner: "maintenance",', MP_BLOCK)


# ====================================================================== the rules


class TestFixesGoThroughTheWritePath(unittest.TestCase):
	def test_the_project_planner_sends_a_fix_through_commit_or_send(self):
		run = _obj_method(PP_BLOCK, "p6d_run_fix")
		self.assertIn("const method = PP6D.fix_methods[job.fix.method];", run)
		self.assertIn("return this.commit(card, method, job.args, message);", run)
		self.assertIn("return this.send(method, job.args, { message }).finally(() => this.load());", run)
		self.assertNotIn("frappe.call", run)
		methods = dict(re.findall(r'\[`\$\{PP\.api\}\.(\w+)`\]: "(\w+)"', PP_BLOCK))
		self.assertEqual(set(methods.values()), {"save_task", "swap_crew", "add_crew"})
		self.assertTrue(all(key == value for key, value in methods.items()))
		# Draft mode drafts every one of them: send() adds draft=1 to exactly these methods.
		self.assertIn('draft_methods: ["save_task", "add_crew", "swap_crew"]', PP)
		send = _class_method(PP, "send")
		self.assertIn("args = this.with_draft(method, args);", send)
		self.assertIn("this.ask_reason(result.conflicts || {})", send)
		self.assertIn("this.push_undo(opts.snapshot, opts.message)", send)
		self.assertIn("const snapshot = this.snapshot(card);", _class_method(PP, "commit"))

	def test_the_maintenance_planner_sends_a_fix_through_send_with_an_undo_snapshot(self):
		run = _obj_method(MP_BLOCK, "p6d_run_fix")
		self.assertIn("const method = MP6D.fix_methods[job.fix.method];", run)
		self.assertIn(
			"return this.send(method, job.args, { snapshot,", run.replace("\n", " ").replace("\t", "")
		)
		self.assertNotIn("frappe.call", run)
		methods = dict(re.findall(r'\[`\$\{MP\.api\}\.(\w+)`\]: "(\w+)"', MP_BLOCK))
		self.assertEqual(set(methods.values()), {"move_visit", "move_projected"})
		listed = MP[MP.index("MP.methods = {") : MP.index("};", MP.index("MP.methods = {"))]
		for name in methods.values():
			self.assertIn(f"{name}: `${{MP.api}}.{name}`", listed)
		self.assertIn("this.ask_reason(result.conflicts || {}, opts.noun)", _class_method(MP, "send"))
		snapshot = _obj_method(MP_BLOCK, "p6d_snapshot")
		# The shapes save_move() records for the same move, so undo() plays them back.
		for needle in (
			'kind: "visit"',
			'kind: "projected"',
			"record: card.name",
			"from_date: args.to_date",
			"to_date: before.date",
		):
			self.assertIn(needle, snapshot)

	def test_every_fix_the_backend_names_is_a_write_the_page_may_run(self):
		source = CONFLICTS_API.read_text(encoding="utf-8")
		project = set(re.findall(r'PP_METHOD \+ "(\w+)"', source))
		maintenance = set(re.findall(r'MP_METHOD \+ "(\w+)"', source))
		self.assertEqual(project - {"who_is_free"}, set(re.findall(r"\[`\$\{PP\.api\}\.(\w+)`\]", PP_BLOCK)))
		self.assertEqual(maintenance, set(re.findall(r"\[`\$\{MP\.api\}\.(\w+)`\]", MP_BLOCK)))
		self.assertIn("who_is_free", project)
		self.assertIn('SELF_METHOD + "acknowledge_conflict"', source)

	def test_the_kit_runs_no_fix_itself(self):
		code = _strip(_kit("conflicts"))
		calls = set(re.findall(r"(?<![.\w])call\(([\w.]+)", code))
		self.assertEqual(calls, {"METHODS.get", "METHODS.acknowledge", "METHODS.who_is_free"})
		run = _js_function(code, "run")
		# The fix is looked up again in the newest list, so it carries the newest `modified`.
		self.assertIn("const found = current_fix(key, fix);", run)
		self.assertIn("const args = fix_args(found.fix, picked);", run)
		self.assertIn(
			"opts.run_fix({ conflict: found.conflict, fix: found.fix, args, picked: picked || null })", run
		)
		self.assertIn("if (spec.method !== METHODS.who_is_free) {", _js_function(code, "start_pick"))
		self.assertIn("fix.method !== METHODS.acknowledge", _js_function(code, "keep"))


class TestPickNeverPicks(unittest.TestCase):
	def test_only_a_tap_on_a_person_runs_it(self):
		code = _strip(_kit("conflicts"))
		start = _js_function(code, "start_pick")
		self.assertNotIn("run(", start)
		self.assertNotIn("run_fix", start)
		pick = _js_function(code, "pick")
		self.assertIn("run(chosen.key, chosen.fix, person);", pick)
		self.assertIn("if (!value || exclude[value]) return;", pick)
		click = _js_function(code, "click")
		self.assertIn('const person = target.closest("[data-pk-cc-pick]");', click)
		self.assertIn('pick(Number(person.getAttribute("data-pk-cc-pick")));', click)
		self.assertEqual(code.count("pick(Number("), 1)
		# Nothing in the picker's markup is selected, checked or focused for submit.
		picker = code[
			code.index("export function picker_html") : code.index("export function server_messages")
		]
		for word in ("checked", "selected", "autofocus", 'aria-pressed="true"', "focus("):
			self.assertNotIn(word, picker)

	def test_a_pick_fix_without_the_person_has_no_arguments(self):
		fix_args = _strip(_kit("conflicts"))
		body = fix_args[
			fix_args.index("export function fix_args") : fix_args.index("export function free_people")
		]
		self.assertIn('if (fix.type === "pick_free") {', body)
		self.assertIn("if (!pick.arg || !value) return null;", body)


class TestKeepNeedsAReason(unittest.TestCase):
	def test_a_blank_reason_never_reaches_the_server(self):
		keep = _js_function(_strip(_kit("conflicts")), "keep")
		self.assertIn('const reason = String((field && field.value) || "").trim();', keep)
		self.assertLess(keep.index("if (!reason) {"), keep.index("call(METHODS.acknowledge"))
		self.assertIn("return;", keep[keep.index("if (!reason) {") : keep.index("call(METHODS.acknowledge")])
		self.assertIn("items: JSON.stringify(", keep)
		self.assertIn('t("That conflict changed; here is the current list.")', keep)

	def test_the_backend_refuses_one_too(self):
		source = CONFLICTS_API.read_text(encoding="utf-8")
		self.assertIn('if not reason:\n\t\tfrappe.throw(_("Say why this conflict is being kept."))', source)


class TestBlockNotesOnlyFromBlockNotes(unittest.TestCase):
	def test_the_pages_read_block_notes_in_one_place(self):
		for block in (PP_BLOCK, MP_BLOCK):
			reader = _obj_method(block, "p6d_block_note")
			self.assertIn("const notes = (this.data && this.data.block_notes) || {};", reader)
			self.assertEqual(_strip(block).count("block_notes"), _strip(reader).count("block_notes"))
			# Every block drawn takes its note from that reader (My week: the person's own blocks,
			# whose note get_my_week read through the same block_notes on the server).
			for note in re.findall(r"\bnote: ([^,\n}]+)", _strip(block)):
				self.assertTrue(
					"this.p6d_block_note(" in note
					or note.strip()
					in (
						'""',
						"block.note",
						'block ? block.note : ""',
						"opts.note || null",
						'__("Shop meeting 7 am")',
					),
					note,
				)
			self.assertNotIn("booking.note", block)
			self.assertNotIn(".block.note", block)
		self.assertIn("note: block.note", _obj_method(PP_BLOCK, "p6d_my_week_day"))

	def test_the_kit_never_reads_a_note_off_a_block(self):
		blocks = _strip(_kit("blocks"))
		self.assertNotIn("block.note", blocks)
		chip = blocks[blocks.index("export function chip_html") : blocks.index("function note_text")]
		self.assertIn('const note = ctx.note ? String(ctx.note) : "";', chip)
		conflicts = _strip(_kit("conflicts"))
		self.assertNotIn("block.note", conflicts)
		self.assertIn("ctx.block_note(block)", conflicts)
		# The person and day drawers show a block as "Unavailable" and nothing else.
		peeks = _strip(_kit("peeks"))
		self.assertIn('block: "Blocked"', peeks)
		self.assertNotIn("booking.note", peeks)
		self.assertNotIn("block.note", peeks)


class TestBlocksNeverDrag(unittest.TestCase):
	def test_neither_page_can_pick_a_block_up(self):
		for code in (PP, MP):
			for method in ("drag_source", "find_target", "on_down"):
				self.assertNotIn("pk-block", _class_method(code, method))
			self.assertNotIn("data-pk-drag", _block(code))
		# The crew view's block returns before any card markup is made.
		booking = _class_method(PP, "booking_html")
		self.assertLess(booking.index('booking.kind === "block"'), booking.index("const card ="))
		chip = _strip(_kit("blocks"))
		chip = chip[chip.index("export function chip_html") : chip.index("function note_text")]
		for word in ("data-pk-drag", "pp-card", "mp-card", "movable", "draggable"):
			self.assertNotIn(word, chip)


class TestMyWeekBlocksOnlyYourOwnTime(unittest.TestCase):
	def test_block_my_time_is_self_only(self):
		day = _obj_method(PP_BLOCK, "p6d_my_week_day")
		self.assertIn('.text(__("Block my time"))', day)
		self.assertIn('.on("click", () => this.p6d_my_week_block(entry.date, null))', day)
		form = _obj_method(PP_BLOCK, "p6d_my_week_block")
		self.assertIn("self_only: true,", form)
		self.assertNotIn("people:", form)
		self.assertNotIn("resource:", form)
		blocks = _strip(_kit("blocks"))
		self.assertIn(
			"const people = !opts.self_only && Array.isArray(opts.people) ? opts.people : null;", blocks
		)
		self.assertIn(
			"if (people && !opts.self_only && values.resource) args.resource = values.resource;", blocks
		)
		self.assertNotIn("args.user", blocks)
		self.assertNotIn(
			"args.resource =",
			blocks.replace("if (people && !opts.self_only && values.resource) args.resource =", ""),
		)

	def test_a_technician_on_a_planner_is_self_only_too(self):
		for block in (PP_BLOCK, MP_BLOCK):
			form = _obj_method(block, "p6d_block_form")
			self.assertIn("self_only: !can,", form)
			self.assertIn("people: can ? people : null,", form)
			self.assertIn('__("You can only block your own time.")', form)


class TestApiContract(unittest.TestCase):
	def _texts(self):
		return [_strip(PP), _strip(MP)] + [
			_strip(path.read_text(encoding="utf-8")) for path in KIT_DIR.glob("*.js")
		]

	def test_the_methods_called_are_the_contract(self):
		blocks, conflicts, pp = set(), set(), set()
		for text in self._texts():
			blocks |= set(re.findall(r"api\.planner_blocks\.(\w+)", text))
			conflicts |= set(re.findall(r"api\.planner_conflicts\.(\w+)", text))
			pp |= set(re.findall(r"api\.project_planner\.(\w+)", text))
		self.assertEqual(blocks, set(BLOCKS_CONTRACT))
		self.assertEqual(conflicts, set(CONFLICTS_CONTRACT))
		self.assertIn("who_is_free", pp)

	def test_the_backend_takes_every_argument_sent_and_writes_are_post(self):
		for path, contract in (
			(BLOCKS_API, BLOCKS_CONTRACT),
			(CONFLICTS_API, CONFLICTS_CONTRACT),
			(PP_API, KIT_PP_CONTRACT),
		):
			functions = _functions(path)
			for method, args in contract.items():
				node = functions[method]
				params = {a.arg for a in node.args.args + node.args.kwonlyargs}
				self.assertTrue(args <= params, f"{method} lacks {sorted(args - params)}")
				decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
				if method in WRITES:
					self.assertIn("methods=['POST']", decorators, method)
				else:
					self.assertIn("frappe.whitelist()", decorators, method)

	def test_the_arguments_the_kit_builds_from_a_fix_are_the_backends(self):
		source = CONFLICTS_API.read_text(encoding="utf-8")
		keep = source[source.index('"type": "keep"') : source.index('"needs": "reason"')]
		args = keep.split('"args": {', 1)[1]
		self.assertEqual(
			set(re.findall(r'^\t+"(\w+)": ', args, flags=re.M)), {"key", "items", "planner", "fingerprint"}
		)
		self.assertIn(
			"Object.assign({}, fix.args, { reason, items: JSON.stringify(", _strip(_kit("conflicts"))
		)
		for args in re.findall(
			r'"who_is_free": \{\s*"method": PP_METHOD \+ "who_is_free",\s*"args": \{([^}]*)\}', source
		):
			self.assertTrue(set(re.findall(r'"(\w+)":', args)) <= KIT_PP_CONTRACT["who_is_free"], args)


class TestOffLabel(unittest.TestCase):
	def test_an_all_day_block_reads_unavailable(self):
		self.assertEqual(views.off_label("Unavailable"), "Unavailable")
		self.assertEqual(views.off_kind("Unavailable"), "blocked")
		cell = {"capacity": 0, "booked": 0, "off": "Unavailable", "bookings": []}
		day = views.day_view({"date": "2026-10-14", "items": []}, cell)
		self.assertEqual(day["off"], "Unavailable")
		self.assertEqual(day["off_kind"], "blocked")

	def test_a_time_off_reason_still_never_passes(self):
		self.assertEqual(views.off_label("Time off"), "Off")
		self.assertEqual(views.off_label("Unavailable: Medical (surgery)"), "Off")
		self.assertEqual(views.off_label("Unavailable (sick leave)"), "Off")
		self.assertEqual(views.off_label("unavailable"), "Off")
		self.assertEqual(views.off_label("Sick Leave"), "Off")
		self.assertEqual(views.off_kind("Unavailable: Medical"), "time_off")
		self.assertEqual(views.off_label("Holiday: Thanksgiving"), "Holiday")
		self.assertEqual(views.off_label("Half day off"), "Half day off")
		self.assertIsNone(views.off_label(None))

	def test_the_engine_label_is_the_one_matched(self):
		source = (APP / "api/planner_views.py").read_text(encoding="utf-8")
		self.assertIn('if text in ("Half day off", "Not a work day", engine.UNAVAILABLE):', source)
		engine = (APP / "project_enhancements/crew_availability.py").read_text(encoding="utf-8")
		self.assertIn('UNAVAILABLE = "Unavailable"', engine)


class TestHouseRules(unittest.TestCase):
	def test_nothing_touches_storage_history_or_html5_drag(self):
		for block in (PP_BLOCK, MP_BLOCK, _kit("conflicts"), _kit("blocks")):
			bare = _strip(block)
			for forbidden in (
				"localStorage",
				"sessionStorage",
				"pushState",
				"replaceState",
				"window.location",
				"location.href",
				"history.",
				"draggable",
				"dragstart",
				"dragover",
				"dataTransfer",
				'type: "GET"',
				"frappe.xcall",
				"eval(",
				"new Function",
			):
				self.assertNotIn(forbidden, bare, forbidden)
		self.assertEqual(PP.count("window.localStorage"), 2)
		self.assertEqual(MP.count("localStorage"), 2)

	def test_no_record_field_is_interpolated_raw(self):
		pattern = (
			r"\$\{(?:card|item|data|visit|member|row|booking|forecast|project|profile|entry|stop|result|answer|resource|"
			r"day|note|block|person|conflict|fix|job|args|ctx)\.\w+"
		)
		for name, block in (
			("pp", PP_BLOCK),
			("mp", MP_BLOCK),
			("conflicts", _kit("conflicts")),
			("blocks", _kit("blocks")),
		):
			raw = [line.strip() for line in block.splitlines() if "<" in line and re.search(pattern, line)]
			self.assertEqual(raw, [], name)

	def test_styles_are_namespaced_and_text_is_american(self):
		for block, prefix in ((PP_BLOCK, ("pp-", "pk-")), (MP_BLOCK, ("mp-", "pk-"))):
			style = block[block.index("_STYLE = `") : block.index("`;", block.index("_STYLE = `"))]
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
			self.assertTrue(classes)
			self.assertEqual({c for c in classes if not c.startswith(prefix)}, set(), classes)
		for name in ("CONFLICTS_CSS", "BLOCKS_CSS"):
			text = _kit("conflicts" if name == "CONFLICTS_CSS" else "blocks")
			css = text[text.index(f"export const {name} = `") :]
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
			self.assertEqual({c for c in classes if not c.startswith("pk-")}, set(), classes)
		for text in (PP_BLOCK, MP_BLOCK, _kit("conflicts"), _kit("blocks")):
			for british in ("colour", "cancelled", "labour", "centre", "behaviour", "favour"):
				self.assertNotIn(british, text.lower(), british)

	def test_no_method_is_defined_twice_and_files_are_lf(self):
		for code in (PP, MP):
			names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
			names = [n for n in names if n not in {"if", "for", "while", "switch", "catch", "function"}]
			self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
		# Every method of the blocks carries the phase's prefix, so another phase's never collides.
		for block in (PP_BLOCK, MP_BLOCK):
			for name in re.findall(r"^\t([A-Za-z_]\w*)\(.*\) \{$", block, re.M):
				self.assertTrue(name.startswith("p6d_") or name in ("init_phase6d", "render_phase6d"), name)
		for path in (
			PP_JS,
			MP_JS,
			BUNDLE,
			KIT_DIR / "conflicts.js",
			KIT_DIR / "blocks.js",
			KIT_DIR / "peeks.js",
			Path(__file__),
		):
			self.assertNotIn(b"\r", path.read_bytes(), path.name)


class TestDocsAndCi(unittest.TestCase):
	def test_the_suite_has_its_own_step_after_the_6d_backend(self):
		ci = CI.read_text(encoding="utf-8")
		step = "python -m unittest erpnext_enhancements.tests.test_planner_phase6d_ui -v"
		self.assertIn(step, ci)
		self.assertGreater(
			ci.index(step), ci.index("python -m unittest erpnext_enhancements.tests.test_planner_phase6d -v")
		)

	def test_the_docs_describe_it(self):
		readme = PE_README.read_text(encoding="utf-8")
		self.assertIn("(Phase 6D UI", readme)
		for word in (
			"Conflict center",
			"block_notes",
			"Block my time",
			"conflicts.js",
			"blocks.js",
			"head_html",
		):
			self.assertIn(word, readme)
		self.assertIn("Phase 6D UI", SM_README.read_text(encoding="utf-8"))
		api = API_README.read_text(encoding="utf-8")
		self.assertIn("planner_kit/blocks.js", api)
		self.assertIn("planner_kit/conflicts.js", api)


# ====================================================================== the kit under node


def _module(name):
	"""A kit module as a function scope that returns its exports (imports dropped: the shared
	helpers from escape.js are defined once, outside)."""
	text = _kit(name)
	text = re.sub(r"^import .*?;\s*$", "", text, flags=re.M)
	exports = re.findall(r"^export (?:const|function) (\w+)", text, flags=re.M)
	text = re.sub(r"^export (const|function) ", r"\1 ", text, flags=re.M)
	return f"const {name} = (() => {{\n{text}\nreturn {{ {', '.join(exports)} }};\n}})();\n"


def _kit_source():
	escape = re.sub(r"^export (const|function) ", r"\1 ", _kit("escape"), flags=re.M)
	return escape + "\n" + "".join(_module(name) for name in ("conflicts", "blocks", "peeks"))


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(0, "utf8");
const ctx = { console, JSON, Promise };
vm.createContext(ctx);
vm.runInContext(source + "\nthis.mods = { conflicts, blocks, peeks };", ctx);
const C = ctx.mods.conflicts, B = ctx.mods.blocks, P = ctx.mods.peeks;
const out = {};
const J = (value) => JSON.parse(JSON.stringify(value));

const PPM = "erpnext_enhancements.api.project_planner.";
const keep = (key) => ({ type: "keep", label: "Keep it with a reason", method: "erpnext_enhancements.api.planner_conflicts.acknowledge_conflict", args: { key, items: [{ doctype: "Task", name: "TASK-1" }], planner: "project", fingerprint: "fp" } });
const pick = { type: "pick_free", label: "Pick someone who's free", doctype: "Task", name: "TASK-1", resource: "RES-1", who_is_free: { method: PPM + "who_is_free", args: { start: "2026-10-13", hours: 8 } }, method: PPM + "swap_crew", args: { task: "TASK-1", modified: "m1", from_resource: "RES-1" }, pick: { arg: "to_resource", field: "resource" } };
const next = { type: "next_free_day", label: "Move to Thu Oct 15", doctype: "Task", name: "TASK-1", method: PPM + "save_task", args: { task: "TASK-1", modified: "m1", start: "2026-10-15" } };
const list = [
	{ key: "overbooked|2026-10-13|RES-1|a", kind: "overbooked", date: "2026-10-13", resource: "RES-1", resource_label: "Austin <A>", user: "a@x", sentence: "Over by 2h", message: "Austin: Over by 2h",
		items: [{ doctype: "Task", name: "TASK-1", title: "Dig <script>", planner: "project", own: true, kind: "task", hours: 8, slot: ["09:00", "17:00"] },
			{ doctype: "Sapphire Maintenance Record", name: "SMR-1", title: "Visit", planner: "maintenance", own: false, kind: "visit", hours: 2 }],
		fixes: [next, pick, { type: "pencil", label: "Make it pencil", doctype: "Task", name: "TASK-1", method: PPM + "save_task", args: { task: "TASK-1", modified: "m1", tentative: 1 } }, keep("overbooked|2026-10-13|RES-1|a")],
		acknowledged: null, own: true, fingerprint: "fp", block: { name: "BLK-1", note: "SERVER COPY" } },
	{ key: "equipment|2026-10-15|Fleet Vehicle:T3|b", kind: "equipment", date: "2026-10-15", resource: null, resource_label: "Truck 3", equipment: { type: "Fleet Vehicle", name: "T3", label: "Truck <3>" }, sentence: "Truck 3 is on TASK-1 and TASK-2", message: "Truck 3 is on TASK-1 and TASK-2",
		items: [{ doctype: "Task", name: "TASK-2", title: "Pour", planner: "rental", own: false, kind: "rental" }, { doctype: "Travel Trip", name: "TRIP-1", title: "Expo", planner: "travel", own: false, kind: "travel" }],
		fixes: [keep("equipment|2026-10-15|Fleet Vehicle:T3|b")], acknowledged: null, own: false, fingerprint: "fp2", block: null },
	{ key: "day_off|2026-10-13|RES-2|c", kind: "day_off", date: "2026-10-13", resource: "RES-2", resource_label: "Korben", sentence: "Booked on a day off (Time off)", message: "Korben: Booked on a day off (Time off)",
		items: [{ doctype: "Task", name: "TASK-3", title: "Set", planner: "project", own: true, kind: "task" }], fixes: [keep("day_off|2026-10-13|RES-2|c")],
		acknowledged: { reason: "Client <r>", by: "lisa@x", by_name: "Lisa", on: "2026-10-09 10:00:00" }, own: true, fingerprint: "fp3", block: null },
];
out.open = C.open_count(list);
out.kept = C.kept_count(list);
out.open_none = C.open_count(null);
out.groups = J(C.group_by_day(list, false).map((g) => [g.date, g.conflicts.map((c) => c.index)]));
out.groups_kept = J(C.group_by_day(list, true).map((g) => [g.date, g.conflicts.map((c) => c.index)]));
const note = (name) => (name === "BLK-1" ? "Dentist <b>" : "");
out.html = C.conflicts_html(list, { block_note: note, can_schedule: false });
out.html_kept = C.conflicts_html(list, { show_kept: true, block_note: note });
out.html_keep = C.conflicts_html(list, { keep_key: list[0].key, draft: true, block_note: note });
out.html_keep_other = C.conflicts_html(list, { keep_key: list[1].key, can_schedule: false });
out.html_busy = C.conflicts_html(list, { busy_key: list[0].key });
out.html_loading = C.conflicts_html(null, { loading: true });
out.html_failed = C.conflicts_html(null, {});
out.html_empty = C.conflicts_html([], { range_label: "Sun, Oct 11 – Sat, Oct 17" });
out.html_focus = C.conflicts_html(list, { focus_date: "2026-10-20" });
out.links = J([{ own: true }, { planner: "maintenance" }, { planner: "project" }, { planner: "travel" }, { planner: "rental" }, {}].map((i) => C.item_link(i).action));
out.fixes_for = J(C.fixes_for_item(list[0], list[0].items[0]).map((f) => f.index));
out.index = J(C.index_by_ref(list));
out.args_pick_none = C.fix_args(pick, null);
out.args_pick_blank = C.fix_args(pick, { resource: "" });
out.args_pick = J(C.fix_args(pick, { resource: "RES-3", user: "u3", label: "Lisa" }));
out.args_next = J(C.fix_args(next, null));
out.args_untouched = J(pick.args);
out.args_bad = C.fix_args({ type: "pencil" }, null);
const answer = { days: [{ date: "2026-10-13",
	free: [{ resource: "RES-3", label: "Lisa <i>", user: "lisa@x", free_hours: 6 }, { resource: "RES-2", label: "Korben", user: "korben@x", free_hours: 8 }, { resource: "RES-4", label: "Ann", user: null, free_hours: 6 }],
	not_free: [{ resource: "RES-1", label: "Austin", user: "a@x", free_hours: 0, reason: "Only 0h free (8h booked of 8h)" }, { resource: "RES-5", label: "Bo", user: "bo@x", free_hours: 0, reason: "Unavailable" }] }] };
out.people = J(C.free_people(answer));
const multi = { days: [
	{ date: "2026-10-13", free: [{ resource: "RES-2", label: "Korben", free_hours: 8 }, { resource: "RES-3", label: "Lisa", free_hours: 6 }], not_free: [] },
	{ date: "2026-10-14", free: [{ resource: "RES-2", label: "Korben", free_hours: 4 }], not_free: [{ resource: "RES-3", label: "Lisa", reason: "Time off" }] },
	{ date: "2026-10-15", free: [{ resource: "RES-2", label: "Korben", free_hours: 5 }], not_free: [] },
] };
out.people_multi = J(C.free_people(multi));
out.people_none = J(C.free_people(null));
out.picker = C.picker_html(C.free_people(answer), { field: "resource", exclude: { "RES-1": "Booked on it now", "RES-3": "Already on it" }, title: "Who is free on Tue, Oct 13 for 8h", context: "In place of Austin on Dig <x>" });
out.picker_user = C.picker_html(C.free_people(answer), { field: "user", exclude: {} });
out.picker_loading = C.picker_html(undefined, { loading: true });
out.picker_failed = C.picker_html(null, {});
out.picker_empty = C.picker_html([], {});
out.messages = J(C.server_messages({ _server_messages: JSON.stringify([JSON.stringify({ message: "This conflict has changed" }), JSON.stringify("plain")]) }));
out.messages_none = J(C.server_messages(null));
out.kinds = Object.keys(C.KIND_INFO);

out.windows = [B.block_window(["14:00", "16:00"]), B.block_window(["11:00", "13:30"]), B.block_window(["09:00", "10:15"]), B.block_window(["12:00", "13:00"]), B.block_window(["x", "y"])];
out.texts = [B.block_text({ slot: ["14:00", "16:00"] }), B.block_text({ all_day: 1 }), B.block_text({ all_day: 0, from_time: "09:00:00", to_time: "11:30:00" }), B.block_text({ all_day: 0, from_time: "12:00", to_time: "11:00" }), B.block_text({})];
out.chip = B.chip_html({ ref: "BLK-1", slot: ["14:00", "16:00"], note: "SECRET-ON-THE-BLOCK" }, { who: "Austin <A>", note: "Dentist <b>", date: "2026-10-13", resource: "RES-1", person: "RES 1", editable: true });
out.chip_plain = B.chip_html({ name: "BLK-2", all_day: true, note: "SECRET" }, {});
out.chip_compact = B.chip_html({ ref: "BLK-3", slot: ["09:00", "11:00"] }, { who: "AH", compact: true, note: "not in month" });
const evil = { name: "N1", note: "<img src=x onerror=alert(1)>", audience: "Field", project: "PRJ-1", project_title: "River<walk>" };
out.notes = B.notes_html([evil, { note: "   " }, null], { date: "2026-10-13", can_add: true, project_link: true });
out.notes_plain = B.notes_html([evil], { date: "2026-10-13" });
out.notes_compact = B.notes_html([evil, { note: "Second" }], { date: "2026-10-13", compact: true });
out.notes_compact_empty = B.notes_html([], { compact: true, can_add: true });
out.notes_full = B.notes_html([evil], { full: true });
out.notes_none = B.notes_html([], {});
out.note_list = B.note_list_html([{ name: "N1", note: "Line <b>one</b>", audience: "PM", project: "PRJ-1", project_title: "Plaza", can_edit: true }, { name: "N2", note: "Read only", can_edit: false }], { project_link: true });
out.note_list_none = B.note_list_html([], {});
out.note_list_loading = B.note_list_html(null, { loading: true });

out.state_blocked = J(P.day_state({ capacity: 0, booked: 0, off: "Unavailable" }));
out.state_blocked_booked = J(P.day_state({ capacity: 0, booked: 3, off: "Unavailable" }));
out.person = P.person_week_html({ resource: "RES-1", user: "a@x", today: "2026-10-12", days: [{ date: "2026-10-14", capacity: 0, booked: 0, free: 0, off: "Unavailable", conflicts: [], bookings: [
	{ kind: "block", ref: "BLK-2", key: "BLK-2", label: "Unavailable", hours: 0, slot: null },
	{ kind: "block", ref: "BLK-3", key: "BLK-3", label: "Unavailable", hours: 2, slot: ["14:00", "16:00"] }], stops: [] }] }, { can_drag: () => true });

// The two forms, with a fake frappe that records every call.
const calls = [], dialogs = [], toasts = [], alerts = [];
let reply = () => ({ ok: true });
const fake = {
	call: (opts) => { calls.push(J(opts)); return Promise.resolve({ message: reply(opts) }); },
	ui: { Dialog: class { constructor(cfg) { this.cfg = cfg; dialogs.push(this); } show() { this.shown = true; } hide() { this.hidden = true; } } },
	msgprint: (m) => alerts.push(String(m)),
	confirm: (m, yes) => yes(),
	show_alert: (o) => alerts.push(o.message),
};
const forms = B.create_blocks({ frappe: fake, toast: (m, o) => toasts.push([String(m), o && o.tone]) });
(async () => {
	forms.form({ self_only: true, people: [{ value: "RES-9", label: "Someone else" }], date: "2026-10-13", resource: "RES-9", resource_label: "You" });
	let d = dialogs.pop();
	out.self_fields = d.cfg.fields.map((f) => f.fieldname || f.fieldtype);
	out.self_secondary = d.cfg.secondary_action_label || null;
	await d.cfg.primary_action({ resource: "RES-9", date: "2026-10-13", all_day: 1, note: "Shop day" });
	out.self_call = calls.pop();
	forms.form({ people: [{ value: "RES-1", label: "Austin" }, { value: "RES-2", label: "Korben" }], resource: "RES-1", block: { name: "BLK-1", date: "2026-10-13", slot: ["14:00", "16:00"] }, note: "Dentist" });
	d = dialogs.pop();
	out.edit_fields = J(d.cfg.fields.filter((f) => f.fieldname).map((f) => [f.fieldname, f.default === undefined ? null : f.default]));
	out.edit_secondary = d.cfg.secondary_action_label;
	reply = () => ({ block: {}, conflicts: [{ date: "2026-10-13" }], message: "This overlaps Dig at Riverwalk; your PM will see it in the Conflict center.", warnings: ["That day has passed"] });
	const before = calls.length;
	await d.cfg.primary_action({ resource: "RES-2", date: "2026-10-13", all_day: 0, from_time: "15:00:00", to_time: "14:00:00", note: "Dentist" });
	await d.cfg.primary_action({ resource: "RES-2", date: "2026-10-13", all_day: 0, from_time: "", to_time: "14:00:00", note: "Dentist" });
	out.bad_times_calls = calls.length - before;
	await d.cfg.primary_action({ resource: "RES-2", date: "2026-10-13", all_day: 0, from_time: "14:00:00", to_time: "15:30:00", note: "Dentist" });
	out.edit_call = calls.pop();
	out.edit_toast = toasts.pop();
	out.edit_alerts = alerts.slice();
	reply = () => ({ name: "BLK-1", deleted: true });
	d.cfg.secondary_action();
	await new Promise((r) => setTimeout(r, 5));
	out.delete_call = calls.pop();
	reply = () => ({ name: "N1" });
	forms.note_form({ date: "2026-10-13" });
	d = dialogs.pop();
	out.note_fields = J(d.cfg.fields.map((f) => [f.fieldname, f.fieldtype, f.reqd || 0]));
	out.note_audiences = J(d.cfg.fields.find((f) => f.fieldname === "audience").options.map((o) => o.value));
	const n0 = calls.length;
	await d.cfg.primary_action({ date: "2026-10-13", note: "   ", audience: "" });
	out.blank_note_calls = calls.length - n0;
	await d.cfg.primary_action({ date: "2026-10-13", note: " Shop meeting 7 am ", audience: "Field", project: "" });
	out.note_call = calls.pop();
	forms.note_form({ note: { name: "N1", date: "2026-10-13", note: "x", audience: "PM", project: "PRJ-1" } });
	d = dialogs.pop();
	out.note_edit_secondary = d.cfg.secondary_action_label;
	d.cfg.secondary_action();
	await new Promise((r) => setTimeout(r, 5));
	out.note_delete_call = calls.pop();
	reply = () => ({ notes: { "2026-10-30": [{ name: "N9", note: "Later" }] } });
	out.got_notes = J(await forms.get_notes("2026-10-30"));
	out.get_notes_call = calls.pop();
	process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
"""


class TestKitUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		cls.node = node
		result = subprocess.run(
			[node, "-e", HARNESS],
			input=_kit_source(),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_javascript_parses(self):
		for path in (
			PP_JS,
			MP_JS,
			BUNDLE,
			KIT_DIR / "conflicts.js",
			KIT_DIR / "blocks.js",
			KIT_DIR / "peeks.js",
		):
			result = subprocess.run(
				[self.node, "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	# ------------------------------------------------------------------ the Conflict center

	def test_counts_and_days(self):
		out = self.out
		self.assertEqual(out["open"], 2)
		self.assertEqual(out["kept"], 1)
		self.assertEqual(out["open_none"], 0)
		self.assertEqual(out["groups"], [["2026-10-13", [0]], ["2026-10-15", [1]]])
		self.assertEqual(out["groups_kept"], [["2026-10-13", [0, 2]], ["2026-10-15", [1]]])
		self.assertEqual(
			out["kinds"], ["overbooked", "overlap", "day_off", "blocked", "equipment", "qualification"]
		)

	def test_the_list_groups_by_day_with_icons_people_and_fixes(self):
		html = self.out["html"]
		self.assertEqual(html.count('class="pk-cc-day"'), 2)
		self.assertIn("Tue, Oct 13", html)
		self.assertIn("2 to look at", html)
		self.assertIn("Show kept (1)", html)
		self.assertNotIn("Korben", html)  # kept: behind the toggle
		self.assertIn("Austin &lt;A&gt;", html)
		self.assertIn("Truck &lt;3&gt;", html)  # equipment shows the vehicle
		self.assertIn("Over hours", html)
		self.assertIn("<svg", html)
		# A fix button for each fix of the record, then Keep.
		for label in (
			"Move to Thu Oct 15",
			"Pick someone who&#39;s free",
			"Make it pencil",
			"Keep it with a reason",
		):
			self.assertIn(label, html)
		self.assertIn('data-pk-cc-fix="0.0"', html)
		self.assertIn('data-pk-cc-fix="0.3"', html)
		self.assertEqual(self.out["fixes_for"], [0, 1, 2])
		# Its own record opens here; anyone else's is a link to where it moves.
		self.assertIn('class="pk-link pk-cc-title" role="button" tabindex="0" data-pk-cc-item="0.0"', html)
		for label in ("Open in Maintenance Planner", "Open task", "Open trip"):
			self.assertIn(label, html)
		self.assertEqual(self.out["links"], ["open", "maintenance", "project", "trip", "task", ""])
		self.assertIn("09:00–17:00", html)
		self.assertIn("Drive times here are estimates", html)

	def test_kept_conflicts_show_who_kept_them_and_why(self):
		html = self.out["html_kept"]
		self.assertIn("Hide kept (1)", html)
		self.assertIn('aria-pressed="true"', html)
		self.assertIn("Kept by Lisa on Fri, Oct 9: Client &lt;r&gt;", html)
		self.assertIn("pk-cc-kept", html)
		kept = html[html.index('data-pk-cc-key="day_off') :]
		self.assertNotIn("Keep it with a reason", kept[: kept.index("</article>")])

	def test_the_block_note_comes_only_from_the_page(self):
		self.assertIn("Note: Dentist &lt;b&gt;", self.out["html"])
		for key in ("html", "html_kept", "html_keep"):
			self.assertNotIn("SERVER COPY", self.out[key])

	def test_keep_asks_for_a_reason_and_only_a_planner_keeps_nobodys(self):
		html = self.out["html_keep"]
		self.assertIn("<textarea", html)
		self.assertIn("required", html)
		self.assertIn('data-pk-cc-keep-save="0"', html)
		self.assertIn("saved straight away, even in Draft mode", html)
		self.assertIn("Draft mode is on", html)
		# Nothing of this planner's in it and not a planner: no form, the button is off and says why.
		other = self.out["html_keep_other"]
		self.assertNotIn("<textarea", other)
		equipment = other[other.index('data-pk-cc-key="equipment') :]
		self.assertIn("disabled", equipment[: equipment.index("</article>")])
		self.assertIn("Only a planner can keep", equipment[: equipment.index("</article>")])

	def test_a_running_fix_disables_its_conflict_and_states_render(self):
		busy = self.out["html_busy"]
		first = busy[busy.index('data-pk-cc-key="overbooked') :]
		first = first[: first.index("</article>")]
		self.assertGreaterEqual(first.count("disabled"), 4)
		self.assertIn("pk-busy", busy)
		self.assertIn("Loading", self.out["html_loading"])
		self.assertIn("could not be loaded", self.out["html_failed"])
		self.assertIn("Nothing is wrong in Sun, Oct 11 – Sat, Oct 17.", self.out["html_empty"])
		self.assertIn("Nothing is wrong on Tue, Oct 20.", self.out["html_focus"])

	def test_cards_are_indexed_by_record_for_their_markers(self):
		index = self.out["index"]
		self.assertEqual([hit["key"] for hit in index["Task|TASK-1"]], ["overbooked|2026-10-13|RES-1|a"])
		self.assertEqual(index["Task|TASK-1"][0]["resource"], "RES-1")
		self.assertNotIn("Task|TASK-3", index)  # kept: no marker

	def test_fix_args_are_the_servers_and_a_pick_needs_the_person(self):
		out = self.out
		self.assertIsNone(out["args_pick_none"])
		self.assertIsNone(out["args_pick_blank"])
		self.assertEqual(
			out["args_pick"],
			{"task": "TASK-1", "modified": "m1", "from_resource": "RES-1", "to_resource": "RES-3"},
		)
		self.assertEqual(out["args_next"], {"task": "TASK-1", "modified": "m1", "start": "2026-10-15"})
		self.assertEqual(
			out["args_untouched"], {"task": "TASK-1", "modified": "m1", "from_resource": "RES-1"}
		)
		self.assertIsNone(out["args_bad"])

	def test_who_is_free_lists_free_people_first_and_never_picks(self):
		people = self.out["people"]
		self.assertEqual([p["label"] for p in people], ["Korben", "Ann", "Lisa <i>", "Austin", "Bo"])
		self.assertEqual([p["free"] for p in people], [True, True, True, False, False])
		self.assertEqual(people[3]["reason"], "Only 0h free (8h booked of 8h)")
		picker = self.out["picker"]
		self.assertIn("Back to the conflicts", picker)
		self.assertIn("In place of Austin on Dig &lt;x&gt;", picker)
		self.assertIn("Nobody is picked for you", picker)
		self.assertIn("Free (3)", picker)
		self.assertIn("Busy (2)", picker)
		self.assertIn("Lisa &lt;i&gt;", picker)
		self.assertIn("8h free", picker)
		for word in ("checked", "selected", "autofocus", 'aria-pressed="true"'):
			self.assertNotIn(word, picker)
		buttons = re.findall(
			r"<button type=\"button\" class=\"(pk-cc-person[^\"]*)\" data-pk-cc-pick=\"(\d+)\"([^>]*)>",
			picker,
		)
		self.assertEqual(len(buttons), 5)
		by_index = {int(index): (cls, rest) for cls, index, rest in buttons}
		self.assertNotIn("disabled", by_index[0][1])  # Korben: free
		self.assertIn("disabled", by_index[2][1])  # Lisa: already on it
		self.assertIn("disabled", by_index[3][1])  # Austin: booked on it now
		self.assertIn("pk-cc-busy", by_index[4][0])  # Bo: greyed, busy...
		self.assertNotIn(
			"disabled", by_index[4][1]
		)  # ...but a planner may still choose them (a reason follows)
		self.assertIn("Booked on it now", picker)
		self.assertIn("Already on it", picker)

	def test_a_visit_is_handed_over_by_user(self):
		picker = self.out["picker_user"]
		ann = picker[picker.index('data-pk-cc-pick="1"') :]
		self.assertIn("disabled", ann[: ann.index(">")])
		self.assertIn("No user account to hand it to", picker)
		self.assertIn("Loading", self.out["picker_loading"])
		self.assertIn("could not be loaded", self.out["picker_failed"])
		self.assertIn("Nobody to show", self.out["picker_empty"])

	def test_several_days_count_free_only_on_every_day(self):
		people = self.out["people_multi"]
		self.assertEqual(people[0]["label"], "Korben")
		self.assertTrue(people[0]["free"])
		self.assertEqual(people[0]["free_hours"], 4)
		self.assertFalse(people[1]["free"])
		self.assertEqual(people[1]["reason"], "Wed, Oct 14: Time off")
		self.assertEqual(self.out["people_none"], [])

	def test_server_messages(self):
		self.assertEqual(self.out["messages"], ["This conflict has changed", "plain"])
		self.assertEqual(self.out["messages_none"], [])

	# ------------------------------------------------------------------ blocks and notes

	def test_block_windows_read_as_people_say_them(self):
		self.assertEqual(self.out["windows"], ["2–4 pm", "11 am–1:30 pm", "9–10:15 am", "12–1 pm", ""])
		self.assertEqual(
			self.out["texts"],
			[
				"Unavailable 2–4 pm",
				"Unavailable all day",
				"Unavailable 9–11:30 am",
				"Unavailable all day",
				"Unavailable all day",
			],
		)

	def test_a_block_chip_shows_the_page_note_escaped_and_never_drags(self):
		chip = self.out["chip"]
		self.assertIn("Austin &lt;A&gt; · Unavailable 2–4 pm", chip)
		self.assertIn("Dentist &lt;b&gt;", chip)
		self.assertNotIn("SECRET", chip)
		self.assertIn('data-pk-block="BLK-1"', chip)
		self.assertIn('data-pk-block-resource="RES-1"', chip)
		self.assertIn('data-pk-persons="RES%201"', chip)
		self.assertIn("pk-block-edit", chip)
		for word in ("data-pk-drag", "pp-card", "mp-card", "movable"):
			self.assertNotIn(word, chip)
		plain = self.out["chip_plain"]
		self.assertIn("Unavailable all day", plain)
		self.assertIn("pk-block-allday", plain)
		self.assertNotIn("SECRET", plain)
		self.assertNotIn("pk-block-note", plain)
		self.assertNotIn("pk-block-edit", plain)
		compact = self.out["chip_compact"]
		self.assertIn("AH · 9–11 am", compact)
		# A month cell is too small for the note: at most its tooltip, never the chip.
		self.assertNotIn("pk-block-note", compact)
		self.assertNotIn("not in month", compact[compact.index(">") :])

	def test_the_notes_row_is_escaped(self):
		notes = self.out["notes"]
		self.assertNotIn("<img", notes)
		self.assertIn("&lt;img src=x onerror=alert(1)&gt;", notes)
		self.assertIn('<span class="pk-note-tag"', notes)
		self.assertIn(">Field</span>", notes)
		self.assertIn("River&lt;walk&gt;", notes)
		self.assertIn('data-pk-project="PRJ-1"', notes)
		self.assertIn('data-pk-note-open="2026-10-13"', notes)
		self.assertIn('data-pk-note-add="2026-10-13"', notes)
		self.assertEqual(notes.count("pk-note-line"), 1)  # a blank note is no note
		self.assertNotIn("data-pk-project", self.out["notes_plain"])
		self.assertNotIn("data-pk-note-add", self.out["notes_plain"])
		compact = self.out["notes_compact"]
		self.assertIn("🗒 2", compact)
		self.assertNotIn("<img", compact)
		self.assertIn("&lt;img", compact)
		self.assertEqual(self.out["notes_compact_empty"], "")
		self.assertNotIn("data-pk-note-open", self.out["notes_full"])
		self.assertIn("pk-note-full", self.out["notes_full"])
		self.assertEqual(self.out["notes_none"], "")

	def test_the_day_drawer_lists_notes_with_edit_for_a_planner(self):
		html = self.out["note_list"]
		self.assertIn("Line &lt;b&gt;one&lt;/b&gt;", html)
		self.assertIn('data-pk-note-edit="N1"', html)
		self.assertNotIn('data-pk-note-edit="N2"', html)
		self.assertIn('data-pk-note-project="PRJ-1"', html)
		self.assertEqual(self.out["note_list_none"], "")
		self.assertIn("Loading", self.out["note_list_loading"])

	def test_the_person_drawer_says_unavailable_and_never_drags_a_block(self):
		self.assertEqual(self.out["state_blocked"]["text"], "Unavailable")
		self.assertEqual(self.out["state_blocked"]["tone"], "off")
		self.assertEqual(self.out["state_blocked_booked"]["text"], "Unavailable, 3h booked")
		person = self.out["person"]
		self.assertIn("Blocked", person)
		self.assertIn("All day", person)
		self.assertIn("14:00–16:00", person)
		self.assertNotIn("Off", person.replace("pk-day-off", ""))
		self.assertNotIn("data-pk-drag", person)  # can_drag said yes, but a block is no booking to move

	# ------------------------------------------------------------------ the forms

	def test_my_week_and_a_technician_send_no_person(self):
		self.assertNotIn("resource", self.out["self_fields"])
		self.assertIsNone(self.out["self_secondary"])
		call = self.out["self_call"]
		self.assertEqual(call["method"], "erpnext_enhancements.api.planner_blocks.save_block")
		self.assertEqual(call["args"], {"date": "2026-10-13", "all_day": 1, "note": "Shop day"})

	def test_a_planner_edits_a_block_and_an_unread_note_is_never_wiped(self):
		fields = dict(self.out["edit_fields"])
		self.assertEqual(fields["resource"], "RES-1")
		self.assertEqual(fields["from_time"], "14:00:00")
		self.assertEqual(fields["to_time"], "16:00:00")
		self.assertEqual(fields["all_day"], 0)
		self.assertEqual(fields["note"], "Dentist")
		self.assertEqual(self.out["edit_secondary"], "Delete")
		self.assertEqual(self.out["bad_times_calls"], 0)
		call = self.out["edit_call"]
		self.assertEqual(
			call["args"],
			{
				"date": "2026-10-13",
				"all_day": 0,
				"from_time": "14:00:00",
				"to_time": "15:30:00",
				"name": "BLK-1",
				"resource": "RES-2",
			},
		)
		self.assertTrue(set(call["args"]) <= BLOCKS_CONTRACT["save_block"])

	def test_saving_never_refuses_and_shows_the_servers_sentence(self):
		self.assertEqual(
			self.out["edit_toast"],
			["This overlaps Dig at Riverwalk; your PM will see it in the Conflict center.", "warning"],
		)
		self.assertIn("That day has passed", self.out["edit_alerts"])

	def test_delete_and_the_note_form(self):
		self.assertEqual(
			self.out["delete_call"]["method"], "erpnext_enhancements.api.planner_blocks.delete_block"
		)
		self.assertEqual(self.out["delete_call"]["args"], {"name": "BLK-1"})
		self.assertEqual(self.out["note_audiences"], ["", "Field", "PM", "Design", "Subcontractor"])
		self.assertIn(["note", "Small Text", 1], self.out["note_fields"])
		self.assertEqual(self.out["blank_note_calls"], 0)
		call = self.out["note_call"]
		self.assertEqual(call["method"], "erpnext_enhancements.api.planner_blocks.save_day_note")
		self.assertEqual(
			call["args"],
			{"date": "2026-10-13", "note": "Shop meeting 7 am", "audience": "Field", "project": ""},
		)
		self.assertEqual(self.out["note_edit_secondary"], "Delete")
		self.assertEqual(self.out["note_delete_call"]["args"], {"name": "N1"})
		self.assertEqual(self.out["got_notes"], [{"name": "N9", "note": "Later"}])
		self.assertEqual(self.out["get_notes_call"]["args"], {"start": "2026-10-30", "end": "2026-10-30"})
		for key, method in (
			("self_call", "save_block"),
			("edit_call", "save_block"),
			("delete_call", "delete_block"),
			("note_call", "save_day_note"),
			("note_delete_call", "delete_day_note"),
			("get_notes_call", "get_day_notes"),
		):
			self.assertTrue(set(self.out[key]["args"]) <= BLOCKS_CONTRACT[method], key)


if __name__ == "__main__":
	unittest.main()
