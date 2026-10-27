# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A tap on a planner item opens its record (Nik, 2026-10-27: "When using the Project Planner or other
planners, tapping the task should take you to the task, tapping the project should take you to the
project, and so on.").

He picked, through four questions: the record opens in the same tab, so Back returns to the planner on
the same tab, dates and filters; what a tap opened before (the quick-edit side panel, Project at a glance,
a person's week) moves to the hold / right-click menu; Tap to move is off by default on a touch screen;
Overview rows and Rental Planner fountains open their records too, and a person's name their Employee.

What this pins, and what would fail quietly otherwise:

* **One map from what a planner draws to its record** (``planner_kit/records.js``), run under node with
  every kind: a drafted new task is no Task yet, a projected visit is its contract however the page
  carries it, and a record this person cannot open is none, so the page keeps its old answer instead of
  sending them to "Not permitted".
* **Who may open what is decided per record, on the server** (``api/planner_open.py``, the v1.753.0
  review). ``frappe.model.can_read`` sees only doctypes; on production 14 of the 17 planner users hold an
  Employee User Permission for their own record alone, so every other Employee, and every Planner Resource
  naming one, is refused them, and a Travel Trip or a Maintenance Record can be refused by its own row
  rules. Each payload a tap reads marks it: a person's ``open_as``, a booking's ``open``. Unit tests
  below run the module against a stub whose ``get_list`` refuses rows the way production does.
* **Opening goes through ``drawer.navigate``**, in the same tab, so an open drawer's history entry is
  replaced rather than stacked on (Nik's Back/Forward rule), and **asks first** when the side panel holds
  edits that are not saved; a page's own ``set_value`` is never an edit.
* **Both pages and the quick looks route every tap through it**, run under node against the real page
  scripts in ``scripts/test_planner_tap_opens.js``; here, the wiring: the bundle, the payloads that carry
  what a tap needs (a projected visit's flag, a technician's Employee), Tap to move's default, the blocks'
  order, the styles and the CI step.

Bench-free: reads files and runs node; ``api/planner_open.py`` against a small ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_tap_opens
"""

import importlib
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
KIT = APP / "public" / "js" / "planner_kit"
BUNDLE = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
RP_JS = APP / "asset_management" / "page" / "rental_planner" / "rental_planner.js"
PP_API = APP / "api" / "project_planner.py"
MP_API = APP / "api" / "maintenance_planner.py"
PV_API = APP / "api" / "planner_views.py"
OPEN_API = APP / "api" / "planner_open.py"
WIDGET = APP / "public" / "js" / "gantt_widget" / "gantt_widget.js"
WIDGET_CSS = APP / "public" / "css" / "gantt_widget.bundle.css"
HARNESS = REPO_ROOT / "scripts" / "test_planner_tap_opens.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_saved = {}
planner_open = None


def _pyfunc(source, name):
	"""One Python function's text, up to the next top-level definition."""
	start = source.index(f"def {name}(")
	end = re.compile(r"\n(?:def |@|class )").search(source, start + 1)
	return source[start : end.start() if end else len(source)]


class _PermissionError(Exception):
	pass


def setUpModule():
	"""A ``frappe`` with what planner_open uses: has_permission, get_list, PermissionError, the message
	log, log_error. ``throw`` writes to the message log before it raises, as frappe 16.53.0's does
	(``utils/messages.py``): that is what turns a caught refusal into a red dialog on the Desk."""
	global planner_open
	for name in ("frappe", "erpnext_enhancements.api.planner_open"):
		_saved[name] = sys.modules.pop(name, None)
	frappe = types.ModuleType("frappe")
	frappe.PermissionError = _PermissionError
	frappe.errors = []
	frappe.calls = []
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.no_read = set()
	frappe.has_permission = lambda doctype=None, ptype="read", *a, **k: doctype not in frappe.no_read

	def throw(message, exc=Exception):
		frappe.local.message_log.append({"message": message, "indicator": "red"})
		raise exc(message)

	frappe.throw = throw
	frappe.log_error = lambda *a, **k: frappe.errors.append(k)
	frappe.get_traceback = lambda: "traceback"
	sys.modules["frappe"] = frappe
	planner_open = importlib.import_module("erpnext_enhancements.api.planner_open")


def tearDownModule():
	sys.modules.pop("erpnext_enhancements.api.planner_open", None)
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _allow(readable):
	"""get_list as production answers it: role, User Permissions and row rules, per record."""
	frappe = sys.modules["frappe"]

	def get_list(doctype, filters=None, pluck=None, **kwargs):
		frappe.calls.append((doctype, filters, pluck, kwargs))
		if readable.get(doctype) == "no-role":
			frappe.throw(f"Insufficient Permission for {doctype}", _PermissionError)
		names = filters["name"][1]
		allowed = readable.get(doctype)
		return [n for n in names if allowed is None or n in allowed]

	frappe.calls = []
	frappe.no_read = set()
	frappe.local.message_log = []
	frappe.get_list = get_list


def _read(path):
	return path.read_text(encoding="utf-8")


def _strip_comments(js):
	"""Line comments out, so a pin on code never matches the prose that explains it."""
	return re.sub(r"(?m)^\s*//.*$", "", js)


def _method(js, signature):
	"""One method's text: a class method ends at a tab and a brace, an object's at a tab, brace, comma."""
	start = js.index(signature)
	end = re.compile(r"\n\t\},?\n").search(js, start).start()
	return js[start:end]


class TestTheKit(unittest.TestCase):
	def test_the_bundle_installs_records_over_the_panel(self):
		bundle = _read(BUNDLE)
		self.assertIn('import { create_records } from "./planner_kit/records.js";', bundle)
		self.assertIn("const panel = create_panel(env);", bundle)
		self.assertIn("env.unsaved = panel.unsaved;", bundle)
		self.assertIn("env.records = create_records(env);", bundle)
		self.assertIn("records: env.records,", bundle)
		# The quick looks read env.records, so it is made before they are.
		self.assertLess(bundle.index("env.records = create_records(env);"), bundle.index("peeks: create_peeks(env),"))

	def test_records_opens_through_the_drawer_and_asks_over_unsaved_edits(self):
		records = _strip_comments(_read(KIT / "records.js"))
		self.assertIn('env.frappe.set_route("Form", record.doctype, record.name)', records)
		self.assertIn("env.drawer.navigate(route)", records)
		self.assertIn("env.unsaved()", records)
		self.assertIn("env.frappe.confirm(", records)
		# The server's answer per row, and a person only as the server marked them.
		self.assertIn("if (it.open === false) return null;", records)
		self.assertIn("if (it.open_as === DOCTYPES.employee) return pick(DOCTYPES.employee, it.employee);", records)
		# An Undo toast belongs to the planner: closed before the Form opens.
		self.assertIn('if (env.toast && typeof env.toast.close === "function") env.toast.close();', records)
		# Same tab, always: nothing opens a window.
		self.assertNotIn("window.open", records)
		self.assertNotIn("_blank", records)

	def test_the_panel_counts_only_the_persons_own_edits(self):
		panel = _strip_comments(_read(KIT / "panel.js"))
		self.assertIn('this.container.addEventListener("pointerdown", touch, true);', panel)
		self.assertIn('this.container.addEventListener("keydown", touch, true);', panel)
		self.assertIn("e.isTrusted !== false", panel)
		self.assertIn("panel.unsaved = () => !!(current && current.is_dirty());", panel)
		# A late reply set through the panel moves the remembered value; keys never reorder a compare.
		self.assertIn("Promise.resolve(done).then(() => this.rebase([fieldname]));", panel)
		self.assertIn("return canonical(this.values());", panel)

	def test_the_quick_looks_mark_and_open_their_bookings(self):
		peeks = _strip_comments(_read(KIT / "peeks.js"))
		self.assertIn("const record_of = env.records ? env.records.record_of : null;", peeks)
		self.assertIn('data-pk-open="${escape(record.doctype)}" data-pk-open-name="${escape(record.name)}"', peeks)
		self.assertIn("if (opts && typeof opts.on_open === \"function\") opts.on_open(record);", peeks)
		css = _read(KIT_CSS)
		self.assertIn(".pk-item.pk-open {", css)


class TestThePages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = _read(PP_JS)
		cls.mp = _read(MP_JS)
		cls.pp_code = _strip_comments(cls.pp)
		cls.mp_code = _strip_comments(cls.mp)

	def test_the_blocks_sit_before_the_tour(self):
		# The tour's block stays last (test_planner_tour); these go just before it.
		for code, block, tour in (
			(self.pp, "Object.assign(ProjectPlanner.prototype, PPR_METHODS);", "const PPTOUR = {"),
			(self.mp, "Object.assign(MaintenancePlanner.prototype, MPR_METHODS);", "const MPTOUR = {"),
		):
			self.assertEqual(code.count(block), 1, block)
			self.assertLess(code.index(block), code.index(tour))

	def test_a_tap_goes_through_the_kits_records_and_falls_back(self):
		self.assertIn("return !!(kit && kit.records.open_item(kind, item));", self.pp_code)
		self.assertIn("return !!(kit && kit.records.open_item(kind, item));", self.mp_code)
		# A drafted new task, no kit, or no permission: what a tap did before.
		self.assertIn('if (card && !this.ppr_open("task", card)) this.open_card(card);', self.pp_code)
		self.assertIn("const known = { employee: person.employee, resource, open_as: person.open_as };", self.pp_code)
		self.assertIn('if (!this.ppr_open("person", known)) this.p6a_open_person(resource, selected);', self.pp_code)
		self.assertIn("const known = { employee: person.employee, resource: person.resource, open_as: person.open_as };", self.mp_code)
		# A fallback route still goes through the drawer, so an open drawer's entry is replaced.
		self.assertIn('this.ppr_leave(() => frappe.set_route("Form", "Task", name));', self.pp_code)
		self.assertIn('this.ppr_leave(() => frappe.set_route("Form", "Travel Trip", ref));', self.pp_code)
		self.assertTrue(_method(self.mp, "\tmpr_open_card(card) {").rstrip().endswith("this.open_card(card);"))

	def test_every_tap_target_is_routed(self):
		activate = _method(self.pp, "\tactivate(target) {")
		for needle in (
			"this.ppr_open_task(equip_el.getAttribute(\"data-etask\"));",
			"this.ppr_open_task(od_el.getAttribute(\"data-od-task\"));",
			"if (card) this.ppr_open_card(card);",
			'const label_el = target.closest("[data-ppr-task]");',
			"this.open_foreign(item.kind, item.ref, item.date, item.projected, item.open);",
		):
			self.assertIn(needle, activate, needle)
		click = _method(self.pp, "\tp6a_click(e) {")
		self.assertIn("this.ppr_open_person(decodeURIComponent(person.getAttribute(\"data-pk-person\")));", click)
		self.assertIn("this.ppr_open_project(decodeURIComponent(project.getAttribute(\"data-pk-project\")));", click)
		self.assertIn("e.stopImmediatePropagation();", click)
		self.assertIn("this.p6a_open_day(day.getAttribute(\"data-pp6a-day\"));", click)  # a date: no record
		self.assertIn("if (card) this.ppr_open_card(card);", _method(self.pp, "\tppg_click(task) {"))
		self.assertIn('this.ppr_open("task", { name: task.ee_task })', _method(self.pp, "\tppo_click(task) {"))
		self.assertIn("on_task_context: (id, task, e) => this.ppo_menu(task, e),", self.pp_code)
		self.assertEqual(self.pp_code.count("on_open: (record) => this.ppr_peek_open(record),"), 2)
		self.assertEqual(self.mp_code.count("on_open: (record) => this.mpr_peek_open(record),"), 2)
		self.assertEqual(self.mp_code.count("if (card) this.mpr_open_card(card);"), 3)  # the click, Enter, Overdue
		self.assertIn(
			"this.mpr_open_person(decodeURIComponent(person.getAttribute(\"data-pk-person\")));",
			_method(self.mp, "\tp6a_click(e) {"),
		)

	def test_the_menus_start_with_quick_edit(self):
		self.assertIn('{ label: __("Quick edit"), on_click: () => this.open_card(card) },', self.pp_code)
		self.assertIn('fresh ? null : { label: __("Open task"), on_click: () => this.ppr_open_task(card.name) },', self.pp_code)
		self.assertIn('{ label: __("Quick edit"), on_click: () => this.open_card(card) },', self.mp_code)
		self.assertNotIn('label: __("Edit")', self.pp_code)
		self.assertNotIn('label: __("Edit")', self.mp_code)

	def test_tap_to_move_is_off_unless_chosen(self):
		for code, pref in ((self.pp, "PP6C"), (self.mp, "MP6C")):
			body = _method(code, "\tp6c_tap_default() {")
			self.assertIn(f'return this.load_pref({pref}.tap_pref, "") === "1";', body)
			self.assertNotIn("p6c_coarse", body)

	def test_capacity_and_the_route_know_who_a_name_is(self):
		person = _method(self.pp, "\tppr_person(resource) {")
		self.assertIn("this.heatmap && this.heatmap.rows", person)
		self.assertIn("route.resource === resource", person)
		self.assertIn('employee: person.employee || null, open_as: person.open_as || null', self.pp)

	def test_names_have_their_menu_everywhere(self):
		target = _method(self.pp, "\tp6b_target(el) {")
		self.assertIn('el.closest(".pp-card [data-pk-person], .pp-hm-name[data-pk-person]")', target)
		self.assertIn('el.closest(".pp-route-title")', target)
		self.assertLess(target.index('".pp-card [data-pk-person]'), target.index('el.closest(".pp-card[data-task]")'))
		opening = _method(self.pp, "\tp6b_open_for(el, anchor, from_touch) {")
		self.assertIn('const anywhere = target.kind === "person" || target.kind === "project";', opening)
		self.assertIn('.attr("data-pk-project", encodeURIComponent(row.name))', self.pp)  # Needs tasks
		mp_target = _method(self.mp, "\tp6b_target(el) {")
		self.assertLess(mp_target.index('".mp-card [data-pk-person]"'), mp_target.index('".mp-card[data-key]"'))

	def test_undo_is_retired_when_its_record_changed_elsewhere(self):
		send = _method(self.pp, "\tsend(method, args, opts) {")
		self.assertLess(
			send.index("if (result.name && result.modified) this.modified[result.name] = result.modified;"),
			send.index("this.push_undo(opts.snapshot, opts.message)"),
		)
		self.assertEqual(send.count("this.modified[result.name] = result.modified"), 1)
		self.assertIn("this.ppr_stamp_undo(snapshot);", _method(self.pp, "\tpush_undo(snapshot, message) {"))
		self.assertIn("this.ppr_drop_stale_undo();", _method(self.pp, "\tindex() {"))
		self.assertIn("this.mpr_stamp_undo(snapshot);", _method(self.mp, "\tpush_undo(snapshot, message) {"))
		self.assertIn("this.mpr_drop_stale_undo(fresh);", _method(self.mp, "\tindex() {"))
		self.assertEqual(self.mp_code.count("this.modified[result.name] = result.modified"), 1)

	def test_a_trip_this_person_may_not_open_is_never_routed(self):
		self.assertIn('data-open="${booking.open === false ? "0" : "1"}"', self.pp)
		self.assertIn('if (kind === "travel" && open === false) {', self.pp_code)
		self.assertIn("open: item.open,", self.mp_code)
		self.assertIn("if (card.open === false) {", _method(self.mp, "\tmpr_open_card(card) {"))
		# The route view's link: a visit (its contract when projected), and only one this person may open.
		self.assertIn('open_label && stop.ref && stop.open !== false', self.pp_code)
		self.assertNotIn('travel: __("Open trip")', self.pp_code)

	def test_every_way_out_closes_the_undo_toast(self):
		leave = _method(self.pp, "\tppr_leave(fn) {")
		self.assertLess(leave.index("this.p6a_close_toast();"), leave.index("return this.p6b_navigate(fn);"))
		mp_leave = _method(self.mp, "\tmpr_leave(fn) {")
		self.assertLess(mp_leave.index("this.p6a_close_toast()"), mp_leave.index("kit.drawer.navigate(fn)"))
		# Quick edit's own links: records.open first (it asks over the panel's edits), the old way after.
		self.assertIn('if (action === "task" && this.ppr_open("task", card)) return;', self.pp_code)
		self.assertIn('if (action === "form" && this.mpr_open("visit", { name: card.name })) return;', self.mp_code)
		for code in (self.pp_code, self.mp_code):
			self.assertNotIn('kit.drawer.navigate(() => frappe.set_route("Form"', code)
		self.assertNotIn('this.p6b_navigate(() => frappe.set_route("Form"', self.pp_code)

	def test_the_crew_boxes_are_part_of_the_panels_values(self):
		self.assertIn(
			'if (typeof dialog.add_values === "function") dialog.add_values(() => this.read_crew_editor(dialog, ""));',
			_method(self.mp, "\tbuild_crew_editor(dialog, card) {"),
		)
		self.assertIn("add_values(fn) {", _read(KIT / "panel.js"))

	def test_a_role_with_no_read_is_answered_before_the_query(self):
		openable = _pyfunc(_read(OPEN_API), "openable")
		self.assertLess(openable.index('if not frappe.has_permission(doctype, "read"):'), openable.index("frappe.get_list("))
		self.assertEqual(openable.count("_drop_messages(mark)"), 2)

	def test_the_dead_code_is_gone(self):
		for code in (self.pp, self.mp):
			self.assertNotIn("p6c_coarse", code)
		self.assertNotIn("p6c_body_click", self.pp)

	def test_hold_targets_select_nothing_on_a_touch_screen_only(self):
		# A finger's hold opens the menu, so nothing is selected under it; a mouse can still select and
		# copy a name (the v1.753.0 re-review), so the rules sit in a coarse-pointer block.
		css = _read(PP_JS.with_suffix(".css"))
		start = css.index("@media (pointer: coarse) {\n\t.ppl-task-name[data-ppr-task],\n\t.ppl-phase-name[data-ppr-task],")
		rule = css[start : css.index("\n}\n", start)]
		for selector in (".ppl-project-name", ".pp-p6a-proj", ".t1-tray-title", ".pp-hm-name.pp-p6a-name", ".pp-route-title"):
			self.assertIn(selector, rule)
		self.assertIn("-webkit-touch-callout: none;", rule)
		self.assertIn("user-select: none;", rule)
		bare = re.sub(r"@media \(pointer: coarse\) \{.*?\n\}\n", "", css, flags=re.S)
		self.assertNotIn(".ppl-project-name,\n", bare)
		kit_css = _read(KIT_CSS)
		coarse = kit_css[kit_css.rindex("@media (pointer: coarse) {") :]
		coarse = coarse[: coarse.index("\n}\n")]
		self.assertIn(".pk-note-project[data-pk-project] {", coarse)
		self.assertIn("-webkit-touch-callout: none;", coarse)
		outside = kit_css.replace(coarse, "")
		note = outside[outside.index(".pk-note-project[data-pk-project] {") :]
		self.assertNotIn("user-select", note[: note.index("}")])

	def test_a_grip_is_never_a_tap(self):
		body = _method(self.pp, "\tp6b_resize_end(save) {")
		self.assertLess(
			body.index("this.click_blocked_until = Date.now() + 400;"),
			body.index("if (!save || (resize.new_start === resize.start && resize.new_end === resize.end)) return;"),
		)

	def test_the_lookahead_labels_are_targets_and_styled(self):
		self.assertIn(' data-ppr-task="${pp_esc(phase.name)}" role="button" tabindex="0"', self.pp)
		self.assertIn('<span class="ppl-task-name" data-ppr-task="${pp_esc(', self.pp)
		css = _read(PP_JS.with_suffix(".css"))
		self.assertIn(".ppl-phase-name[data-ppr-task],", css)
		self.assertIn(".pp-hm-name.pp-p6a-name:hover span:last-child,", css)

	def test_the_rental_planners_fountain_opens_its_asset(self):
		rp = _read(RP_JS)
		self.assertIn('$(\'<button type="button" class="rp-asset"></button>\')', rp)
		self.assertIn('.on("click", () => frappe.set_route("Form", "Asset", f.asset))', rp)
		self.assertIn(".rp-asset {", _read(RP_JS.with_suffix(".css")))


class TestThePayloads(unittest.TestCase):
	def test_a_projected_visit_says_so_everywhere_a_tap_reads_it(self):
		pp = _read(PP_API)
		self.assertIn('"projected": bool(booking.get("projected")),', pp)
		# The route view: the routed stops carry their booking, and the unrouted ones are given it.
		self.assertIn('booking = stop.get("booking") or {}', pp)
		self.assertIn('"booking": b,', pp)
		self.assertIn('"projected": bool(booking.get("projected")),', _read(PV_API))

	def test_every_payload_a_tap_reads_is_marked_for_its_caller(self):
		pp = _read(PP_API)
		self.assertIn('planner_open.mark_people(data["resources"])', _pyfunc(pp, "get_planner"))
		self.assertIn('planner_open.mark_bookings(planner_open.day_bookings(data["days"]))', _pyfunc(pp, "get_planner"))
		self.assertIn('planner_open.mark_people(data["resources"])', _pyfunc(pp, "get_heatmap"))
		route = _pyfunc(pp, "get_route")
		self.assertIn("planner_open.mark_people([person])", route)
		self.assertIn('planner_open.mark_bookings(cell.get("bookings"))', route)
		self.assertIn('"open_as": person.get("open_as"),', pp)
		pv = _read(PV_API)
		self.assertEqual(pv.count('planner_open.mark_bookings(planner_open.day_bookings(data.get("days")))'), 2)
		self.assertIn('"open": booking.get("open", True),', pv)
		mp = _read(MP_API)
		self.assertIn('planner_open.mark_people(people, resource_key="resource")', mp)
		self.assertIn('**({"open": False} if b.get("open") is False else {}),', mp)
		self.assertIn("employee=None, open_as=None", mp)

	def test_a_technician_carries_their_employee(self):
		api = _read(MP_API)
		self.assertIn('employee=resource.get("employee"),', api)
		self.assertIn('"employee": resource.get("employee"),', api)


class TestPlannerOpen(unittest.TestCase):
	"""api/planner_open.py against a get_list that refuses rows the way production does (2026-10-27)."""

	def test_one_query_per_doctype_and_only_names(self):
		_allow({})
		self.assertEqual(planner_open.openable("Employee", ["E2", None, "E1", "E1", ""]), {"E1", "E2"})
		((doctype, filters, pluck, kwargs),) = sys.modules["frappe"].calls
		self.assertEqual((doctype, filters, pluck, kwargs), ("Employee", {"name": ["in", ["E1", "E2"]]}, "name", {"limit_page_length": 0}))
		_allow({})
		self.assertEqual(planner_open.openable("Employee", []), set())
		self.assertEqual(sys.modules["frappe"].calls, [])

	def test_a_person_opens_what_this_user_may_open(self):
		# Production: an Employee User Permission for their own record only. Planner Resource links to
		# Employee without ignoring user permissions, so a subcontractor's (no Employee) still opens.
		_allow({"Employee": {"EMP-ME"}, "Planner Resource": {"RES-ME", "RES-SUB"}})
		people = [
			{"name": "RES-ME", "employee": "EMP-ME"},
			{"name": "RES-KYLE", "employee": "EMP-KYLE"},
			{"name": "RES-SUB", "employee": None},
		]
		planner_open.mark_people(people)
		self.assertEqual([p["open_as"] for p in people], ["Employee", None, "Planner Resource"])
		self.assertEqual(sorted(c[0] for c in sys.modules["frappe"].calls), ["Employee", "Planner Resource"])
		# The Maintenance Planner keys a person by user and names the resource `resource`.
		_allow({})
		techs = [{"user": "a@x.com", "name": "Austin", "resource": "RES-A", "employee": None}]
		planner_open.mark_people(techs, resource_key="resource")
		self.assertEqual(techs[0]["open_as"], "Planner Resource")

	def test_bookings_with_rows_of_their_own_are_marked_and_the_rest_left(self):
		_allow({"Sapphire Maintenance Record": {"SMR-MINE"}, "Travel Trip": set(), "Sapphire Maintenance Contract": {"CON-1"}})
		bookings = [
			{"kind": "visit", "ref": "SMR-MINE"},
			{"kind": "visit", "ref": "SMR-THEIRS"},
			{"kind": "visit", "ref": "CON-1", "projected": {"contract": "CON-1"}},
			{"kind": "travel", "ref": "TRIP-1"},
			{"kind": "task", "ref": "TASK-1"},
			{"kind": "drive", "ref": None},
		]
		planner_open.mark_bookings(bookings)
		self.assertEqual([b.get("open") for b in bookings], [True, False, True, False, None, None])
		self.assertEqual(
			sorted(c[0] for c in sys.modules["frappe"].calls),
			["Sapphire Maintenance Contract", "Sapphire Maintenance Record", "Travel Trip"],
		)

	def test_a_role_with_no_read_is_answered_before_the_query(self):
		# A Projects User on the Project Planner holds no permission on Sapphire Maintenance Record or
		# Contract: get_list would throw, and throw leaves "Insufficient Permission" for the Desk.
		_allow({})
		frappe = sys.modules["frappe"]
		frappe.no_read = {"Sapphire Maintenance Record"}
		self.assertEqual(planner_open.openable("Sapphire Maintenance Record", ["SMR-1"]), set())
		self.assertEqual((frappe.calls, frappe.local.message_log), ([], []))

	def test_a_refusal_or_a_failure_opens_nothing_and_leaves_no_message(self):
		_allow({"Travel Trip": "no-role"})
		frappe = sys.modules["frappe"]
		earlier = {"message": "The page's own message"}
		frappe.local.message_log = [earlier]
		self.assertEqual(planner_open.openable("Travel Trip", ["TRIP-1"]), set())
		self.assertEqual(frappe.local.message_log, [earlier])  # only what the refusal wrote is cut

		def broken(*a, **k):
			frappe.local.message_log.append({"message": "half-written"})
			raise ZeroDivisionError

		frappe.get_list = broken
		frappe.errors = []
		self.assertEqual(planner_open.openable("Employee", ["E1"]), set())
		self.assertEqual(frappe.errors[0]["title"], "Planner: which Employee records open")
		self.assertEqual(frappe.local.message_log, [earlier])

	def test_day_bookings_walks_the_engines_days(self):
		days = {"RES-1": {"2026-10-27": {"bookings": [{"kind": "task"}]}, "2026-10-28": {}}, "RES-2": None}
		self.assertEqual(planner_open.day_bookings(days), [{"kind": "task"}])
		self.assertEqual(planner_open.day_bookings(None), [])


class TestTheGanttWidget(unittest.TestCase):
	def test_a_long_press_opens_the_hosts_menu_and_an_edge_is_never_a_tap(self):
		widget = _read(WIDGET)
		self.assertIn("this._init_long_press(g);", widget)
		press = widget[widget.index("_init_long_press(g) {") :]
		press = press[: press.index("\n\t\t}\n")]
		self.assertIn('e.pointerType === "touch"', press)
		self.assertIn("const LONG_MS = 500;", press)
		self.assertIn("const MOVE_PX = 6;", press)
		self.assertIn("this._swallow_click_until = Date.now() + 700;", press)
		self.assertIn("g.locate(", press)
		self.assertIn('e.target.closest(".gantt_task_drag")', widget)
		self.assertIn("if (Date.now() < (this._swallow_click_until || 0)) {", widget)
		css = _read(WIDGET_CSS)
		self.assertIn("@media (pointer: coarse) {", css)
		self.assertIn("-webkit-touch-callout: none;", css[css.index("@media (pointer: coarse) {") :])


class TestTheWiring(unittest.TestCase):
	def test_ci_runs_this_suite(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_tap_opens -v", _read(CI))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=300,
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)


if __name__ == "__main__":
	unittest.main()
