/*
 * Planner kit: a tap on something a planner draws opens its own record, in the same tab (Nik,
 * 2026-10-27: "tapping the task should take you to the task, tapping the project should take you to
 * the project, and so on"). A task opens the Task, a project the Project, a maintenance visit its
 * Sapphire Maintenance Record, a person their Employee record. The quick looks and the quick-edit side
 * panel that a tap used to open are on the hold / right-click menu instead.
 *
 *   planner_kit.records.record_of(kind, item)   -> {doctype, name} | null
 *   planner_kit.records.open(record)            -> true when it is going there
 *   planner_kit.records.open_item(kind, item)   -> both at once
 *
 * `kind` is what the planners already call their things:
 *
 *   task, move, rental   a Task (`ref` or `name`). A Fountain Move job and a rental's crew work are Tasks.
 *                        A drafted new task (`new_task`, or a `draft-new:` name) is not a Task yet: null,
 *                        and the page opens its side panel instead.
 *   visit                a Sapphire Maintenance Record, or for a projected visit (`projected`), which has
 *                        no record yet, the Sapphire Maintenance Contract that will make it.
 *   projected            a Maintenance Planner projected card: its contract.
 *   travel               a Travel Trip.
 *   project              a Project (`project`, else `name`).
 *   person               what the server said this person may open (`open_as`, api/planner_open.py): the
 *                        Employee (`employee`) or the Planner Resource (`resource`); without `open_as`,
 *                        nothing, and the page shows their week.
 *   asset                an Asset.
 *   drive, block, note   nothing: driving is padding, a block and a note have forms of their own.
 *
 * `record_of` returns null for a record this user cannot read, so a page can keep its old answer (a quick
 * look, a note) rather than send someone to "Not permitted". Two checks, because neither is enough alone:
 * `frappe.model.can_read` knows only which doctypes a role reads, and the server marks the rows a User
 * Permission or a doctype's own row rules refuse (`open: false` on a booking, `open_as` on a person; on
 * production most planner users may open no Employee but their own).
 *
 * `open` goes through `drawer.navigate`, which closes an open drawer first so the Form route replaces the
 * drawer's history entry instead of stacking on it (history.js). Back then returns to the planner on the
 * same view, dates and filters. When the side panel holds edits that are not saved it asks first: held
 * planner changes are on the server (Planner Draft Change), so the panel's fields are the only thing a
 * tap elsewhere could throw away.
 */

import { t } from "./escape.js";

export const DOCTYPES = {
	task: "Task",
	move: "Task",
	rental: "Task",
	travel: "Travel Trip",
	project: "Project",
	visit: "Sapphire Maintenance Record",
	projected: "Sapphire Maintenance Contract",
	employee: "Employee",
	resource: "Planner Resource",
	asset: "Asset",
};

const DRAFT_NEW = /^draft-new:/;

const text = (value) => (value == null ? "" : String(value));

export function is_draft_name(name) {
	return DRAFT_NEW.test(text(name));
}

// The contract of a projected visit: a Maintenance Planner card has `contract`; a booking in the Project
// Planner or a quick look has `projected: {contract}`, and its `ref` is the contract too.
function contract_of(item) {
	const projected = item.projected && typeof item.projected === "object" ? item.projected : {};
	return projected.contract || item.contract || item.ref;
}

// `can_read(doctype)`: optional; without it every doctype counts as readable (the tests' pure calls).
export function record_of(kind, item, can_read) {
	const it = item || {};
	if (it.open === false) return null; // the server's answer for this row (api/planner_open.py)
	const pick = (doctype, name) => {
		const value = text(name).trim();
		if (!value) return null;
		if (typeof can_read === "function" && !can_read(doctype)) return null;
		return { doctype, name: value };
	};
	switch (kind) {
		case "task":
		case "move":
		case "rental": {
			const name = it.ref || it.name;
			if (it.new_task || is_draft_name(name)) return null;
			return pick(DOCTYPES.task, name);
		}
		case "visit":
			return it.projected ? pick(DOCTYPES.projected, contract_of(it)) : pick(DOCTYPES.visit, it.ref || it.name);
		case "projected":
			return pick(DOCTYPES.projected, contract_of(it));
		case "travel":
			return pick(DOCTYPES.travel, it.ref || it.name);
		case "project":
			return pick(DOCTYPES.project, it.project || it.name);
		case "person":
			if (it.open_as === DOCTYPES.employee) return pick(DOCTYPES.employee, it.employee);
			if (it.open_as === DOCTYPES.resource) return pick(DOCTYPES.resource, it.resource);
			return null;
		case "asset":
			return pick(DOCTYPES.asset, it.asset || it.name);
		default:
			return null;
	}
}

export function create_records(env) {
	const can_read = (doctype) => {
		const model = env.frappe && env.frappe.model;
		if (!model || typeof model.can_read !== "function") return true;
		try {
			return !!model.can_read(doctype);
		} catch (e) {
			return true;
		}
	};

	function open(record) {
		if (!record || !record.doctype || !record.name) return false;
		const go = () => {
			// An Undo toast belongs to the planner: on the Form it would undo blind (it has no route hook).
			if (env.toast && typeof env.toast.close === "function") env.toast.close();
			const route = () => env.frappe.set_route("Form", record.doctype, record.name);
			return env.drawer ? env.drawer.navigate(route) : route();
		};
		if (typeof env.unsaved === "function" && env.unsaved()) {
			env.frappe.confirm(t("The side panel has changes that are not saved. Leave without saving them?"), go);
			return true;
		}
		go();
		return true;
	}

	return {
		DOCTYPES,
		record_of: (kind, item) => record_of(kind, item, can_read),
		open,
		open_item: (kind, item) => open(record_of(kind, item, can_read)),
	};
}
