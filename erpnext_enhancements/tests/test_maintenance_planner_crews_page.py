# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Maintenance Planner page and multi-person visits (P1.8, spec item 7).

A maintenance visit can take several people. The page shows everyone on a card (the technician
first, ringed), edits the crew, planned hours and Full day from the card's dialog, adds a person to
a visit when their chip is dropped on it (``add_crew``, not a replacement), lists a multi-person
visit in every crew member's row of the crew view, and moves only the person whose chip was dragged.

Bench-free and Frappe-free. Part one reads the page as source, like ``test_maintenance_planner``.
Part two runs the page's own methods under ``node`` with a tiny stub of the browser globals (skipped
when node is not on PATH), so the drop rules are checked by behavior and not only by spelling.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_planner_crews_page
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"


def _body(code, header, until):
	start = code.index(header)
	return code[start : code.index(until, start + len(header))]


class TestCrewsPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PAGE.read_text(encoding="utf-8")

	def body(self, header, until):
		return _body(self.code, header, until)

	def test_cards_show_everyone_on_the_visit_with_the_lead_first_and_ringed(self):
		code = self.code
		people = self.body("visit_people(card) {", "crew_has(card, user) {")
		# The technician is added first, then the crew, each once.
		self.assertLess(people.index("add(card.technician)"), people.index("card.crew"))
		badges = self.body("people_badges(card, in_row) {", "tech_badge(user, extra, role) {")
		self.assertIn("mp-lead", badges)
		self.assertIn("user === card.technician", badges)
		# A lone person is not ringed, and a crew-view row already says who it is.
		self.assertIn("people.length > 1", badges)
		self.assertIn("in_row && people.length < 2", badges)
		self.assertIn(".mp-tech.mp-lead{", code)
		# Names and initials go through mp_esc on their way into the markup.
		self.assertIn("mp_esc(initials.toUpperCase())", code)
		self.assertIn("mp_esc(role ? `${name} (${role})` : name)", code)
		# The card names the whole crew, and the technician filter finds a helper's visits too.
		self.assertIn("this.visit_people(card).includes(this.technician)", code)

	def test_the_dialog_edits_crew_planned_hours_and_full_day(self):
		dialog = self.body("open_card(card) {", "build_crew_editor(dialog, card) {")
		for needle in (
			'fieldname: "planned_hours"',
			'__("Planned hours")',
			'fieldname: "full_day"',
			'__("Full day")',
			'fieldname: "crew_editor"',
			"this.build_crew_editor(dialog, card)",
		):
			self.assertIn(needle, dialog)
		# Only visit records have a crew to edit, and a projected visit keeps its date-only dialog.
		self.assertRegex(
			dialog, r'if \(card\.kind === "visit"\) \{\s+fields\.push\(\{\s+fieldtype: "Select",'
		)
		# Only what changed is sent.
		for needle in (
			"change.crew = crew",
			"change.planned_hours = planned",
			"change.full_day = full_day",
			"this.crew_key(crew) !== this.crew_key(this.crew_rows(card))",
			"planned !== (Number(card.planned_hours) || 0)",
			"full_day !== (card.full_day ? 1 : 0)",
		):
			self.assertIn(needle, dialog)
		editor = self.code[self.code.index("build_crew_editor(dialog, card) {") :]
		# Technicians and Field helpers the API lists, plus anyone already on the visit; names as text.
		self.assertIn("this.data.technicians", editor)
		self.assertIn(".text(person.name)", editor)
		self.assertNotIn(".html(", editor)
		# The technician is not also crew.
		self.assertIn("user === lead", editor)

	def test_a_chip_dropped_on_a_visit_adds_to_the_crew(self):
		plan = self.body("plan_drop(source, target) {", "drop(source, target) {")
		self.assertIn("return { card, add_crew: source.user }", plan)
		# Nobody on the visit yet: the chip assigns the technician, as before.
		self.assertRegex(
			plan, r"if \(!card\.technician\) return \{ card, change: \{ technician: source\.user \} \};"
		)
		self.assertIn("this.crew_has(card, source.user)", plan)
		# A projected visit cannot be added to, and says where the crew is set.
		self.assertIn("Set the site's default crew on its Maintenance Profile", plan)
		drop = self.body("drop(source, target) {", "snapshot_of(card) {")
		self.assertIn("this.add_to_crew(plan.card, plan.add_crew)", drop)
		add = self.body("add_to_crew(card, user) {", "// Resolves either way")
		self.assertRegex(add, r'this\.send\(\s*"add_crew"')
		self.assertIn("record: card.name, user, modified:", add)
		self.assertIn("this.send(", add)
		self.assertIn("snapshot: Object.assign({ kind: \"visit\", site, record: card.name }, before)", add)
		self.assertIn("add_crew: `${MP.api}.add_crew`,", self.code)
		# The chip's tooltip says what the drop does.
		self.assertIn("to add them to its crew", self.code)

	def test_the_crew_view_lists_a_visit_in_every_crew_members_row(self):
		crew = self.body("render_crew(cards) {", "compare(a, b) {")
		self.assertIn("this.visit_people(card)", crew)
		self.assertIn("this.card_row_html(card, user)", crew)
		# The calendar's one-argument form is unchanged.
		self.assertIn("card_html(card) {\n\t\treturn this.card_row_html(card, undefined);", self.code)
		self.assertIn('data-row-user="${mp_esc(row_user)}"', self.code)
		source = self.body("drag_source(el) {", "on_down(e) {")
		self.assertIn('card_el.getAttribute("data-row-user")', source)
		self.assertIn("from_user:", source)
		# The calendar's day tooltips and route check count every crew member's day.
		render = self.body("render() {", "render_calendar(cards) {")
		self.assertIn("this.visit_people(card).forEach", render)

	def test_dragging_one_persons_chip_moves_only_that_person(self):
		plan = self.body("plan_drop(source, target) {", "drop(source, target) {")
		self.assertIn("change.technician = target.user;\n\t\t\tchange.from_user = row_user;", plan)
		# The lead keeps the plain handover.
		self.assertIn('(target.user || "") !== (card.technician || "")', plan)
		self.assertLess(plan.index('card.kind === "projected"'), plan.index("change.technician = target.user"))
		save = self.body("save_move(card, change, before) {", "// Send one call.")
		self.assertIn("args.crew = JSON.stringify(args.crew)", save)
		self.assertIn("args.full_day = args.full_day ? 1 : 0", save)
		self.assertIn("handed over to", save)

	def test_undo_and_the_reason_dialog_cover_crew_edits(self):
		save = self.body("save_move(card, change, before) {", "// Send one call.")
		for needle in ("crew: before.crew", "planned_hours: before.planned_hours", "full_day: before.full_day"):
			self.assertIn(needle, save)
		snap = self.body("snapshot_of(card) {", "// Show the card on its new day")
		for needle in ("this.crew_rows(card)", "card.planned_hours", "card.full_day"):
			self.assertIn(needle, snap)
		undo = self.body("undo() {", "// ------------------------------------------------------------------ dialog")
		for needle in (
			"snap.crew",
			"args.crew = JSON.stringify(snap.crew)",
			"args.planned_hours = snap.planned_hours",
			"args.full_day = snap.full_day",
		):
			self.assertIn(needle, undo)
		# add_crew goes through send(): the same needs_reason dialog and the same single retry.
		send = self.body("send(method, args, opts) {", "ask_reason(conflicts, noun) {")
		self.assertIn("result.needs_reason", send)
		self.assertIn("MP.methods[method]", send)

	def test_nothing_else_changed_about_how_the_page_behaves(self):
		code = self.code
		for forbidden in (
			"draggable",
			"dragstart",
			"dragover",
			"ondrop",
			"dataTransfer",
			"pushState",
			"replaceState",
			"location.hash",
		):
			self.assertNotIn(forbidden, code)
		self.assertEqual(code.count("localStorage"), 2)
		self.assertIn("frappe.set_route(MP.route, view, anchor)", code)
		# New visible text is translated.
		for text in ("Planned hours", "Full day", "{0} added to {1}", "{0}h each", "Crew"):
			self.assertIn(f'__("{text}"', code)
		self.assertNotIn("\r\n", code)

	def test_the_javascript_parses(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, "--check", str(PAGE)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)


# Runs the page's own methods against a stub of the browser: no Frappe, no DOM.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const ctx = {
	frappe: {
		pages: { "maintenance-planner": {} },
		utils: { escape_html: esc },
		show_alert: (a) => ctx.alerts.push(a.message),
		msgprint: () => {},
		call: () => Promise.resolve({ message: {} }),
		datetime: { get_today: () => "2026-10-09", str_to_user: (x) => x },
	},
	alerts: [],
	__: (s, args) => String(s).replace(/{(\d+)}/g, (m, i) => (args && args[i] != null ? args[i] : m)),
	moment: () => ({}),
	$: () => ({}),
	document: {},
	window: {},
	console,
};
vm.createContext(ctx);
vm.runInContext(code + "\nthis.MaintenancePlanner = MaintenancePlanner;", ctx);

const make = () => {
	const p = Object.create(ctx.MaintenancePlanner.prototype);
	p.tech_names = { austin: "Austin Lee", korben: "Korben Fox", jesse: "Jesse Day<b>", daniel: "Daniel Poe" };
	p.tech_by_user = {};
	p.modified = {};
	p.by_key = {};
	p.undo_stack = [];
	p.data = { technicians: [], today: "2026-10-09" };
	p.today = () => "2026-10-09";
	p.render = () => {};
	p.load = () => Promise.resolve();
	p.sent = [];
	p.send = (method, args, opts) => {
		p.sent.push({ method, args, opts });
		return Promise.resolve({});
	};
	return p;
};

const visit = (extra) =>
	Object.assign(
		{
			kind: "visit",
			key: "V1",
			name: "V1",
			site: "Hotel",
			date: "2026-10-12",
			technician: "austin",
			crew: [
				{ user: "korben", name: "Korben Fox", hours: null },
				{ user: "jesse", name: "Jesse Day", hours: 3 },
			],
			planned_hours: null,
			full_day: 0,
			movable: true,
			modified: "m1",
		},
		extra || {}
	);
const out = {};
const p = make();

// ---- who is on a visit
const v = visit();
out.people = p.visit_people(v);
out.rows = p.crew_rows(v);
out.badges = p.people_badges(v, false);
out.solo_badges = p.people_badges(visit({ crew: [] }), false);
out.row_solo = p.people_badges(visit({ crew: [] }), true);
out.row_multi = p.people_badges(v, true);
out.markup = p.card_row_html(v, "korben");
out.markup_plain = p.card_html(v);
out.visible_helper = (() => {
	const q = make();
	q.technician = "jesse";
	return q.visible(v);
})();

// ---- chip drops
const chip = (user) => ({ kind: "person", user });
const onto = (card) => {
	const q = make();
	q.by_key = { [card.key]: card };
	return (user) => q.plan_drop(chip(user), { key: card.key });
};
out.chip_adds = onto(v)("daniel");
out.chip_already_crew = onto(v)("korben");
out.chip_already_lead = onto(v)("austin");
out.chip_unassigned = onto(visit({ technician: "", crew: [] }))("daniel");
out.chip_projected = onto(visit({ kind: "projected" }))("daniel");
out.chip_started = onto(visit({ movable: false }))("daniel");

// ---- visit drags in the crew view
const drag = (card, from_user, target) => {
	const q = make();
	return q.plan_drop({ kind: "card", card, from_user }, target);
};
const cell = (user, date) => ({ row: true, user, date: date || "2026-10-12" });
out.lead_to_row = drag(v, "austin", cell("daniel"));
out.helper_to_row = drag(v, "jesse", cell("daniel"));
out.helper_to_lead_row = drag(v, "jesse", cell("austin"));
out.helper_to_crew_row = drag(v, "jesse", cell("korben"));
out.helper_to_unassigned = drag(v, "jesse", cell(""));
out.helper_new_day = drag(v, "jesse", cell("jesse", "2026-10-14"));
out.helper_row_and_day = drag(v, "jesse", cell("daniel", "2026-10-14"));
out.helper_projected = drag(visit({ kind: "projected" }), "jesse", cell("daniel"));
out.calendar_move = drag(v, null, { date: "2026-10-14" });
out.past = drag(v, null, { date: "2026-10-01" });

// ---- what is sent
(() => {
	const q = make();
	const card = visit();
	q.by_key = { V1: card };
	q.save_move(card, { crew: [{ user: "korben", hours: null }], full_day: true, planned_hours: 4 }, q.snapshot_of(card));
	out.save_args = q.sent[0].args;
	out.save_snapshot = q.sent[0].opts.snapshot;
})();
(() => {
	const q = make();
	const card = visit();
	q.by_key = { V1: card };
	q.modified.V1 = "m9";
	q.add_to_crew(card, "daniel");
	out.add_sent = q.sent[0];
	out.add_optimistic = card.crew.map((m) => m.user);
})();
(() => {
	const q = make();
	const card = visit();
	q.by_key = { V1: card };
	return q.apply(card, { technician: "daniel", from_user: "jesse" }).then(() => {
		out.swap_args = q.sent[0].args;
		out.swap_optimistic = card.crew.map((m) => m.user);
		out.swap_lead = card.technician;
	});
})();
(() => {
	const q = make();
	const card = visit();
	q.by_key = { V1: card };
	q.apply(card, { technician: "jesse" });
	out.handover_crew = card.crew.map((m) => m.user);
	out.handover_lead = card.technician;
})();

// ---- undo puts the crew back
(() => {
	const q = make();
	const was = visit();
	q.by_key = { V1: visit({ crew: [{ user: "korben", hours: null }], planned_hours: 6, full_day: 1 }) };
	q.modified.V1 = "m2";
	q.undo_stack.push({
		kind: "visit",
		site: "Hotel",
		record: "V1",
		date: "2026-10-12",
		technician: "austin",
		crew: q.crew_rows(was),
		planned_hours: 0,
		full_day: 0,
	});
	q.undo();
	out.undo_args = q.sent[0].args;
	out.undo_opts = { auto_reason: q.sent[0].opts.auto_reason, noun: q.sent[0].opts.noun };
})();
(() => {
	const q = make();
	const same = visit();
	q.by_key = { V1: same };
	q.undo_stack.push({
		kind: "visit",
		site: "Hotel",
		record: "V1",
		date: "2026-10-12",
		technician: "austin",
		crew: q.crew_rows(same),
		planned_hours: 0,
		full_day: 0,
	});
	q.undo();
	out.undo_unchanged_args = q.sent[0].args;
})();

setTimeout(() => console.log(JSON.stringify(out)), 20);
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestCrewsPageBehavior(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		result = subprocess.run(
			[shutil.which("node"), "-", str(PAGE)],
			input=HARNESS,
			capture_output=True,
			text=True,
			timeout=120,
			encoding="utf-8",
		)
		if result.returncode != 0:
			raise AssertionError(f"the page harness failed:\n{result.stderr}")
		cls.out = json.loads(result.stdout.strip().splitlines()[-1])

	def test_everyone_on_a_visit_is_shown_with_the_lead_first(self):
		out = self.out
		self.assertEqual(out["people"], ["austin", "korben", "jesse"])
		self.assertEqual(out["rows"], [{"user": "korben", "hours": None}, {"user": "jesse", "hours": 3}])
		badges = re.findall(r'<span class="mp-tech([^"]*)"[^>]*>([^<]*)</span>', out["badges"])
		self.assertEqual([initials for _, initials in badges], ["AL", "KF", "JD"])
		# Only the lead is ringed, and only when there is a crew.
		self.assertIn("mp-lead", badges[0][0])
		self.assertTrue(all("mp-lead" not in cls for cls, _ in badges[1:]))
		self.assertNotIn("mp-lead", out["solo_badges"])
		# In a crew-view row a lone person is the row, so nothing is drawn; a crew still is.
		self.assertEqual(out["row_solo"], "")
		self.assertIn("mp-lead", out["row_multi"])

	def test_names_are_escaped_and_a_row_card_knows_its_row(self):
		out = self.out
		self.assertNotIn("<b>", out["markup"])
		self.assertIn('data-row-user="korben"', out["markup"])
		self.assertNotIn("data-row-user", out["markup_plain"])
		self.assertIn("Crew: Austin Lee, Korben Fox, Jesse Day&lt;b&gt;", out["markup"])

	def test_the_technician_filter_finds_a_helpers_visits(self):
		self.assertTrue(self.out["visible_helper"])

	def test_a_chip_dropped_on_a_visit_joins_its_crew(self):
		out = self.out
		self.assertEqual(out["chip_adds"], {"card": out["chip_adds"]["card"], "add_crew": "daniel"})
		self.assertNotIn("change", out["chip_adds"])
		self.assertIn("refuse", out["chip_already_crew"])
		self.assertIn("refuse", out["chip_already_lead"])
		self.assertIn("is already on", out["chip_already_crew"]["refuse"])

	def test_a_chip_dropped_on_a_visit_with_nobody_becomes_its_technician(self):
		plan = self.out["chip_unassigned"]
		self.assertEqual(plan["change"], {"technician": "daniel"})
		self.assertNotIn("add_crew", plan)

	def test_a_projected_or_started_visit_refuses_a_chip(self):
		out = self.out
		self.assertIn("Maintenance Profile", out["chip_projected"]["refuse"])
		self.assertIn("default crew", out["chip_projected"]["refuse"])
		self.assertIn("started or finished", out["chip_started"]["refuse"])

	def test_the_lead_chip_hands_the_visit_over(self):
		plan = self.out["lead_to_row"]
		self.assertEqual(plan["change"], {"technician": "daniel"})

	def test_a_helpers_chip_moves_only_that_helper(self):
		out = self.out
		self.assertEqual(out["helper_to_row"]["change"], {"technician": "daniel", "from_user": "jesse"})
		self.assertEqual(
			out["helper_row_and_day"]["change"],
			{"date": "2026-10-14", "technician": "daniel", "from_user": "jesse"},
		)
		# Staying in their own row, a helper's chip just moves the visit's day.
		self.assertEqual(out["helper_new_day"]["change"], {"date": "2026-10-14"})
		for key in ("helper_to_lead_row", "helper_to_crew_row"):
			self.assertIn("is already on", out[key]["refuse"], key)
		self.assertIn("refuse", out["helper_to_unassigned"])
		self.assertIn("projected visit", out["helper_projected"]["refuse"])

	def test_calendar_moves_are_unchanged(self):
		out = self.out
		self.assertEqual(out["calendar_move"]["change"], {"date": "2026-10-14"})
		self.assertIn("today or a later day", out["past"]["refuse"])

	def test_a_crew_edit_sends_only_what_the_dialog_changed(self):
		args = self.out["save_args"]
		self.assertEqual(args["record"], "V1")
		self.assertEqual(json.loads(args["crew"]), [{"user": "korben", "hours": None}])
		self.assertEqual(args["full_day"], 1)
		self.assertEqual(args["planned_hours"], 4)
		self.assertNotIn("technician", args)
		self.assertNotIn("date", args)
		snap = self.out["save_snapshot"]
		self.assertEqual(snap["crew"], [{"user": "korben", "hours": None}, {"user": "jesse", "hours": 3}])
		self.assertEqual((snap["planned_hours"], snap["full_day"]), (0, 0))

	def test_add_crew_sends_the_record_the_user_and_the_stamp(self):
		sent = self.out["add_sent"]
		self.assertEqual(sent["method"], "add_crew")
		self.assertEqual(sent["args"], {"record": "V1", "user": "daniel", "modified": "m9"})
		self.assertEqual(sent["opts"]["noun"], "visit")
		self.assertEqual([row["user"] for row in sent["opts"]["snapshot"]["crew"]], ["korben", "jesse"])
		self.assertEqual(self.out["add_optimistic"], ["korben", "jesse", "daniel"])

	def test_a_helper_swap_sends_from_user_and_keeps_the_rest(self):
		out = self.out
		args = out["swap_args"]
		self.assertEqual((args["technician"], args["from_user"]), ("daniel", "jesse"))
		self.assertEqual(out["swap_lead"], "austin")
		self.assertEqual(out["swap_optimistic"], ["korben", "daniel"])

	def test_handing_the_visit_to_a_crew_member_does_not_list_them_twice(self):
		self.assertEqual(self.out["handover_lead"], "jesse")
		self.assertEqual(self.out["handover_crew"], ["korben"])

	def test_undo_sends_the_old_crew_hours_and_full_day_back(self):
		args = self.out["undo_args"]
		self.assertEqual(args["record"], "V1")
		self.assertEqual(args["technician"], "austin")
		self.assertEqual(json.loads(args["crew"]), [{"user": "korben", "hours": None}, {"user": "jesse", "hours": 3}])
		self.assertEqual(args["planned_hours"], 0)
		self.assertEqual(args["full_day"], 0)
		self.assertEqual(self.out["undo_opts"]["noun"], "visit")
		self.assertTrue(self.out["undo_opts"]["auto_reason"])

	def test_undo_leaves_untouched_fields_alone(self):
		args = self.out["undo_unchanged_args"]
		for key in ("crew", "planned_hours", "full_day"):
			self.assertNotIn(key, args)


if __name__ == "__main__":
	unittest.main()
