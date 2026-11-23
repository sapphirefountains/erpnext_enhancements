#!/usr/bin/env node
/**
 * The Tasks Tree's rules (task_tree_manager.js, erpnext_enhancements.task_tree_rules), executed
 * under node rather than grepped: what a drop does, how a column sorts, what the row menu holds.
 *
 * Why this exists (v1.791.0): #1418 shipped a drop-to-nest that the harness of the day called
 * fixed and that still only reordered on prod. SortableJS swapped two rows the moment the pointer
 * crossed a row's edge band; the target slid out from under the pointer and the drop saved the
 * last intent recorded, a reorder. The rules here are what the tree now decides from the pointer
 * alone, with SortableJS refused on every move, so a drop's outcome is a pure function of (rows on
 * screen, dragged row, hovered row, zone) and can be pinned.
 *
 * Checks:
 *   1. Zones: a thin band at each edge (a quarter of the row, 6 to 10 px) reorders; the rest nests.
 *   2. The drop plan: nest on a leaf, on an open parent, on a collapsed parent; never on itself,
 *      its own subtasks or its own parent; a line is always "before the lower row" (first subtask
 *      under an open parent, the next row's level after the last subtask of a group); only the
 *      last row on screen has an "after"; the place it already is does nothing.
 *   3. The rows sent for a reorder: every sibling of the new parent with its position, the moved
 *      row carrying the new parent.
 *   4. The sort cycle: ascending, descending, manual; another column starts ascending; an unknown
 *      column is ignored.
 *   5. Sorting within parents: children never leave their parent; the manual order comes back
 *      untouched; empties last either way; priority and status by rank, dates and names by value.
 *   6. The row menu: three actions in order; arrow keys wrap, Home/End jump, Escape and Tab close.
 *   7. What the file promises the browser: SortableJS refused on every move, a canceled native
 *      drag (dragend without drop) saves nothing, the sort lives in localStorage inside try/catch
 *      and never in frappe's user_settings, no route change and no history write, and the inline
 *      "Add root task…" / "Add subtask…" rows gone (Nikolas, 2026-11-03).
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(ROOT, "erpnext_enhancements", "public", "js", "project_enhancements", "task_tree_manager.js");
if (!fs.existsSync(FILE)) {
	console.error("MARKERS NOT FOUND: " + path.relative(process.cwd(), FILE) + " does not exist.");
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, "utf8");

let failures = 0;
function check(condition, message) {
	if (condition) console.log("  ok    " + message);
	else {
		failures += 1;
		console.error("  FAIL  " + message);
	}
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Load the file as the Desk would, with only what its top level touches.
const sandbox = {
	frappe: {
		provide(ns) {
			ns.split(".").reduce((o, k) => (o[k] = o[k] || {}), sandbox);
		},
		session: { user: "someone@example.com" },
	},
	console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(SOURCE, sandbox, { filename: "task_tree_manager.js" });
const rules = sandbox.erpnext_enhancements.task_tree_rules;
check(rules && typeof rules.dropPlan === "function", "task_tree_rules is defined by the file");

// ---- 1. zones -------------------------------------------------------------------------------
console.log("zones");
check(rules.edgeBand(45) === 10, "a 45px row (what the Desk draws) has a 10px band at each edge");
check(rules.edgeBand(30) === 8, "a 30px row has an 8px band");
check(rules.edgeBand(16) === 6, "the band never goes under 6px");
check(rules.edgeBand(200) === 10, "the band never goes over 10px");
check(rules.dropZone(0, 45) === "before" && rules.dropZone(9, 45) === "before", "the top band reorders before");
check(rules.dropZone(10, 45) === "nest" && rules.dropZone(22, 45) === "nest" && rules.dropZone(35, 45) === "nest", "the body nests");
check(rules.dropZone(36, 45) === "after" && rules.dropZone(45, 45) === "after", "the bottom band reorders after");
check(rules.dropZone(8, 45) === "before" && rules.dropZone(37, 45) === "after", "8px in from either edge is still the band (what v1.787.0 swapped on)");

// ---- 2. the drop plan ------------------------------------------------------------------------
console.log("drop plan");
// On screen: A (open) > A1, A2 ; B (leaf) ; C (collapsed group, subtasks off screen) ; D (open) > D1
const rows = [
	{ id: "A", parent: null },
	{ id: "A1", parent: "A" },
	{ id: "A2", parent: "A" },
	{ id: "B", parent: null },
	{ id: "C", parent: null },
	{ id: "D", parent: null },
	{ id: "D1", parent: "D" },
];
check(same(rules.dropPlan(rows, "B", "C", "nest"), { mode: "nest", target: "C" }), "nest on a collapsed group");
check(same(rules.dropPlan(rows, "A1", "B", "nest"), { mode: "nest", target: "B" }), "nest on a leaf (it becomes a group)");
check(same(rules.dropPlan(rows, "B", "A", "nest"), { mode: "nest", target: "A" }), "nest on an open parent");
check(same(rules.dropPlan(rows, "D1", "A2", "nest"), { mode: "nest", target: "A2" }), "a subtask nests under a task of another group");
check(rules.dropPlan(rows, "A", "A", "nest") === null, "never onto itself");
check(rules.dropPlan(rows, "A", "A1", "nest") === null && rules.dropPlan(rows, "A", "A2", "before") === null, "never onto or beside its own subtask");
check(rules.dropPlan(rows, "A1", "A", "nest") === null, "nest on its own parent does nothing (it is there already)");
check(rules.dropPlan(rows, "B", "Z", "nest") === null && rules.dropPlan(rows, "Z", "B", "nest") === null, "a row not on screen plans nothing");
check(same(rules.dropPlan(rows, "B", "A1", "before"), { mode: "before", target: "A1", parent: "A" }), "the band above A1: before A1, under A");
check(same(rules.dropPlan(rows, "B", "A", "after"), { mode: "before", target: "A1", parent: "A" }), "the band under an open parent: its first subtask");
check(same(rules.dropPlan(rows, "D1", "A2", "after"), { mode: "before", target: "B", parent: null }), "the band under the last subtask of a group: before the next row, at its level");
check(same(rules.dropPlan(rows, "B", "C", "after"), { mode: "before", target: "D", parent: null }), "the band under a collapsed group: before the next row (its subtasks are off screen)");
check(same(rules.dropPlan(rows, "B", "D1", "after"), { mode: "after", target: "D1", parent: "D" }), "only the last row on screen has an after");
check(rules.dropPlan(rows, "A2", "A1", "after") === null, "the place it already is (under A1, where A2 sits) does nothing");
check(same(rules.dropPlan(rows, "A2", "B", "before"), { mode: "before", target: "B", parent: null }), "the line above the row right below it, at another level, is a move (A2 leaves A for the top level)");
check(rules.dropPlan(rows, "A1", "A2", "before") === null, "the line above the row right below it, at the same level, does nothing");
check(same(rules.dropPlan(rows, "A1", "A2", "after"), { mode: "before", target: "B", parent: null }), "but A1 after A2 is a real move (to the top level, before B)");
check(same(rules.dropPlan(rows, "A2", "B", "after"), { mode: "before", target: "C", parent: null }), "A2 under B: before C at the top level");
check(same(rules.dropPlan(rows, "C", "A1", "before"), { mode: "before", target: "A1", parent: "A" }), "a collapsed group can be moved under another group");
check(same(rules.dropPlan(rows, "D", "A2", "nest"), { mode: "nest", target: "A2" }), "a group can nest under a leaf of another group");

// ---- 3. the rows sent for a reorder ---------------------------------------------------------
console.log("reorder rows");
check(
	same(rules.reorderRows(["A1", "A2"], "B", "A2", "before", "A"), [
		{ name: "A1", parent_task: "A", custom_subtask_order: 1 },
		{ name: "B", parent_task: "A", custom_subtask_order: 2 },
		{ name: "A2", parent_task: "A", custom_subtask_order: 3 },
	]),
	"a row from elsewhere lands before the target; every sibling gets its position and the parent"
);
check(
	same(rules.reorderRows(["A", "B", "C", "D"], "A", "D", "after", null), [
		{ name: "B", parent_task: null, custom_subtask_order: 1 },
		{ name: "C", parent_task: null, custom_subtask_order: 2 },
		{ name: "D", parent_task: null, custom_subtask_order: 3 },
		{ name: "A", parent_task: null, custom_subtask_order: 4 },
	]),
	"a top-level row moved after the last row goes last, parent null"
);
check(
	same(rules.reorderRows(["A", "B", "C"], "C", "A", "before", null).map((r) => r.name), ["C", "A", "B"]),
	"a sibling moved before the first goes first"
);
check(
	same(rules.reorderRows(["A", "B"], "X", "missing", "before", null).map((r) => r.name), ["A", "B", "X"]),
	"a target no longer among the siblings puts the row last rather than losing it"
);

// ---- 4. the sort cycle ----------------------------------------------------------------------
console.log("sort cycle");
check(same(rules.nextSort(null, "status"), { key: "status", dir: "asc" }), "first click: ascending");
check(same(rules.nextSort({ key: "status", dir: "asc" }, "status"), { key: "status", dir: "desc" }), "second click: descending");
check(rules.nextSort({ key: "status", dir: "desc" }, "status") === null, "third click: back to the manual order");
check(same(rules.nextSort({ key: "status", dir: "desc" }, "priority"), { key: "priority", dir: "asc" }), "another column starts ascending");
check(same(rules.nextSort({ key: "status", dir: "asc" }, "bogus"), { key: "status", dir: "asc" }), "an unknown column changes nothing");
check(
	same(Object.keys(rules.SORT_LABELS), ["subject", "assigned_to", "status", "priority", "exp_start_date", "exp_end_date", "progress", "expected_time"]),
	"the eight columns Nikolas named sort"
);

// ---- 5. sorting within parents --------------------------------------------------------------
console.log("sort within parents");
const tree = [
	{
		name: "P2",
		subject: "Phase 200",
		status: "Open",
		priority: "Low",
		exp_end_date: "2026-12-01",
		progress: 10,
		children: [
			{ name: "P2b", subject: "b task", status: "Completed", priority: "Urgent", exp_end_date: "", progress: 100 },
			{ name: "P2a", subject: "A task", status: "Open", priority: "Medium", exp_end_date: "2026-11-05", progress: 0 },
			{ name: "P2c", subject: "c task", status: "Working", priority: "High", exp_end_date: "2026-11-01", progress: 50 },
		],
	},
	{ name: "P1", subject: "Phase 100", status: "Working", priority: "High", exp_end_date: "2026-11-12", progress: 40, children: [] },
	{ name: "P3", subject: "Billing", status: "Overdue", priority: "Low", exp_end_date: null, progress: 0 },
];
const names = (list) => list.map((t) => t.name);
const find = (list, name) => list.find((t) => t.name === name);
check(rules.sortTree(tree, null) === tree, "no sort: the rows as given (the manual order), the same array");
const bySubject = rules.sortTree(tree, { key: "subject", dir: "asc" });
check(same(names(bySubject), ["P3", "P1", "P2"]), "roots by name, case aside");
check(same(names(bySubject[2].children), ["P2a", "P2b", "P2c"]), "children sorted under their parent");
check(same(names(tree), ["P2", "P1", "P3"]) && same(names(tree[0].children), ["P2b", "P2a", "P2c"]), "the input is not touched");
check(same(names(rules.sortTree(tree, { key: "subject", dir: "desc" })), ["P2", "P1", "P3"]), "descending reverses");
check(same(names(rules.sortTree(tree, { key: "priority", dir: "asc" })), ["P2", "P3", "P1"]), "priority ascending: Low before High (ties keep the manual order)");
check(same(names(find(rules.sortTree(tree, { key: "priority", dir: "desc" }), "P2").children), ["P2b", "P2c", "P2a"]), "priority descending: Urgent, High, Medium");
check(same(names(find(rules.sortTree(tree, { key: "status", dir: "asc" }), "P2").children), ["P2a", "P2c", "P2b"]), "status by workflow rank: Open, Working, Completed");
check(same(names(rules.sortTree(tree, { key: "status", dir: "asc" })), ["P2", "P1", "P3"]), "Open, Working, Overdue");
check(
	same(names(rules.sortTree([{ name: "late", status: "Open", is_overdue: 1 }, { name: "open", status: "Open" }, { name: "done", status: "Completed" }], { key: "status", dir: "asc" })), ["open", "late", "done"]),
	"an Open task past its end date sorts as the OVERDUE the column shows (review of #1423)"
);
check(same(names(rules.sortTree([{ name: "p10", subject: "Phase 10" }, { name: "p9", subject: "Phase 9" }], { key: "subject", dir: "asc" })), ["p9", "p10"]), "names compare numerically: Phase 9 before Phase 10");
check(same(names(rules.sortTree(tree, { key: "exp_end_date", dir: "asc" })), ["P1", "P2", "P3"]), "dates ascending, a missing date last");
check(same(names(rules.sortTree(tree, { key: "exp_end_date", dir: "desc" })), ["P2", "P1", "P3"]), "dates descending, a missing date still last");
check(same(names(find(rules.sortTree(tree, { key: "exp_end_date", dir: "asc" }), "P2").children), ["P2c", "P2a", "P2b"]), "an empty date among children goes last too");
check(same(names(rules.sortTree(tree, { key: "progress", dir: "desc" })), ["P1", "P2", "P3"]), "% complete is numeric");
check(same(names(rules.sortTree([{ name: "x", progress: 9 }, { name: "y", progress: 10 }], { key: "progress", dir: "asc" })), ["x", "y"]), "9 before 10 (not a string compare)");
check(same(names(rules.sortTree(tree, { key: "bogus", dir: "asc" })), ["P2", "P1", "P3"]), "an unknown column leaves the manual order");
check(same(rules.sortTree(null, { key: "subject", dir: "asc" }), []), "no rows: no crash");

// ---- 6. the row menu ------------------------------------------------------------------------
console.log("row menu");
const actions = rules.rowActions({ name: "T", subject: "Thing" });
check(same(actions.map((a) => a.action), ["add-subtask", "move", "delete"]), "three actions, in that order");
check(actions.every((a) => a.label && a.icon) && actions[2].danger === true, "each has a label and an icon; delete is the red one");
check(rules.rowActions(null).length === 0, "no task, no menu");
check(rules.menuMove("ArrowDown", 0, 3) === 1 && rules.menuMove("ArrowDown", 2, 3) === 0, "ArrowDown moves and wraps");
check(rules.menuMove("ArrowUp", 0, 3) === 2 && rules.menuMove("ArrowUp", 2, 3) === 1, "ArrowUp moves and wraps");
check(rules.menuMove("Home", 2, 3) === 0 && rules.menuMove("End", 0, 3) === 2, "Home and End jump");
check(rules.menuMove("Escape", 1, 3) === "close" && rules.menuMove("Tab", 1, 3) === "close", "Escape and Tab close");
check(rules.menuMove("a", 1, 3) === null && rules.menuMove("ArrowDown", 0, 0) === null, "other keys, or an empty menu, do nothing");

// ---- 7. what the file promises the browser ----------------------------------------------------
console.log("source promises");
const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check(/onMove:\s*function\s*\(evt,\s*originalEvent\)\s*\{[\s\S]*?return false;\s*\}/.test(code), "SortableJS's own move is refused on every onMove");
check(!/handleDragMove/.test(code), "the v1.787.0 handler that let SortableJS swap on the edge band is gone");
check(/TaskTreeManager\.CANCEL_EVENTS\.includes\(original\.type\)/.test(code), "a drag that ends in a cancel event saves nothing");
check(same(sandbox.erpnext_enhancements.TaskTreeManager.CANCEL_EVENTS, ["dragend", "pointercancel", "touchcancel"]), "cancel = dragend (Escape on a native drag), pointercancel and touchcancel (the browser taking a touch gesture over)");
check(/swapThreshold:\s*1\b/.test(code) && /animation:\s*0\b/.test(code), "SortableJS asked to call onMove over the whole row and to animate nothing");
check(/document\.elementFromPoint\(/.test(code), "the drop target is read from the pointer, not from SortableJS");
check(/addEventListener\("dragover", this\._onDocDragOver, this\._dragListenerOptions\)/.test(code) && /removeEventListener\("dragover", this\._onDocDragOver, this\._dragListenerOptions\)/.test(code), "the document dragover listener is bound for the drag and unbound after it, with the same options");
check(/_dragListenerOptions = \{ capture: true, passive: true \}/.test(code), "...in the CAPTURE phase: SortableJS stops a dragover before it bubbles to the document (review of #1423)");
check(/if \(this\._draggedId\) this\.resetDragState\(\);/.test(code), "a drag whose instances are destroyed mid-flight is forgotten (listeners off)");
check(/localStorage\.setItem\(TaskTreeManager\.SORT_STORAGE_KEY\(\)/.test(code), "the sort is remembered in localStorage");
check(!/user_settings/.test(code) && !/frappe\.model\.user_settings/.test(code), "never in frappe's user_settings");
check(/try \{[\s\S]{0,400}localStorage\.getItem\(TaskTreeManager\.SORT_STORAGE_KEY\(\)/.test(code), "read inside try/catch (a private window throws)");
check(!/pushState/.test(code) && !/frappe\.set_route/.test(code) && !/frappe\.new_doc/.test(code), "no route change, no history write (Back and Forward are never broken)");
check(/aria-haspopup="menu"/.test(code) && /role="menuitem"/.test(code) && /aria-expanded/.test(code), "the row menu is a real menu for the keyboard");
check(!/add-subtask-btn/.test(code) && !/move-task-btn/.test(code) && !/delete-task-btn/.test(code), "the three icon buttons are gone");
check(!/saveTaskOrder/.test(code) && !/save-order-btn/.test(code), "the dead Save Order path is gone");
check(!/aria-sort/.test(code) && /aria-label/.test(code), "the sort direction is in the header's accessible name, not aria-sort (not valid on a button)");
check(/me\.closeRowMenu\(true\);/.test(code) && /addEventListener\("scroll", me\._onMenuScroll, true\)/.test(code), "Escape and Tab both return the focus to the button; a scroll closes the menu");
check(/dragLockReason\(\)/.test(code) && /filtersActive\(\)/.test(code), "drag is off while a sort or a filter is on");
check(/Nest under \{0\}/.test(code), "the pill says Nest under <task>");
check(!/quick-add/.test(code) && !/createQuickAddRow/.test(code) && !/create_inline_task/.test(code), "the inline Add root task / Add subtask rows are gone (Nikolas, 2026-11-03)");
check(/\.add-task-btn/.test(code) && /action === "add-subtask"/.test(code) && /\.add-template-btn/.test(code), "the three ways left to add: Add task, a row's Actions → Add subtask, Add from template");
// v1.797.1: staff outside System Manager hold only Desk User's Select on User, and 16.x lets Select
// return `name` and the search field `full_name`, nothing else. Asking for `email` refuses the whole
// call, so the Assign dialog's current-assignee list never rendered for them.
const userLists = [...code.matchAll(/get_list\("User",\s*\{([\s\S]*?)\}\)/g)].map((m) => m[1]);
check(userLists.length > 0 && userLists.every((args) => /fields:\s*\["name", "full_name"\]/.test(args) && !/"email"/.test(args) && !/\bemail\s*:/.test(args)),"every browser list of User asks only for name and full_name, and filters on name, never email (Select allows nothing else)");

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
