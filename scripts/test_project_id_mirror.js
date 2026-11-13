#!/usr/bin/env node
/**
 * The Project form must not open as "Not Saved" because of the Project ID mirror (v1.816.2).
 * Runs the REAL public/js/project.js in a vm against a stubbed Desk.
 *
 * The stub's `frm.set_value` does what frappe.model.set_value does: if the stored value differs it
 * writes it AND dirties the form (`frm.dirty()` sets `doc.__unsaved` and, in the real Desk, registers
 * the `beforeunload` "Leave site?" listener). A direct write to `frm.doc` dirties nothing, as in the
 * real Desk. So the old `frm.set_value("custom_project_id", frm.doc.name)` leaves the stub dirty when
 * the stored value is empty or stale, and the new code must not.
 *
 * Run: node scripts/test_project_id_mirror.js [path/to/project.js]
 * (the optional path lets a test point it at an older copy of the file to prove the check bites)
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT =
	process.argv[2] ||
	path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "project.js");

let checks = 0;
let failures = 0;
function check(label, actual, expected) {
	checks += 1;
	if (actual !== expected) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${expected}\n    actual   ${actual}`);
	}
}

let handlers = null;

function makeFrm(doc, isNew) {
	const frm = {
		doc,
		dirtied: 0,
		refreshed: [],
		buttons: [],
		is_new: () => isNew,
		is_dirty: () => !!doc.__unsaved,
		dirty() {
			doc.__unsaved = 1;
			frm.dirtied += 1;
		},
		// frappe.model.set_value: a no-op when the value is unchanged, else write + dirty.
		set_value(field, value) {
			if (doc[field] === value) return Promise.resolve();
			doc[field] = value;
			frm.dirty();
			return Promise.resolve();
		},
		refresh_field(field) {
			frm.refreshed.push(field);
		},
		add_custom_button(label, fn, group) {
			frm.buttons.push({ label, group });
		},
	};
	return frm;
}

const sandbox = {
	__: (s) => s,
	console,
	Promise,
	frappe: {
		ui: { form: { on: (dt, h) => { if (dt === "Project") handlers = h; } } },
		db: { get_value: () => Promise.resolve({ message: {} }) },
		set_route() {},
		model: { open_mapped_doc() {} },
	},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

check("the script registers a Project handler with refresh", typeof (handlers && handlers.refresh), "function");

// 1. Stored value empty (older Projects): shown, and the form is not dirty.
let frm = makeFrm({ name: "PRJ-00746", custom_project_id: null }, false);
handlers.refresh(frm);
check("empty stored ID: the name is shown", frm.doc.custom_project_id, "PRJ-00746");
check("empty stored ID: the form is NOT marked changed", frm.is_dirty(), false);
check("empty stored ID: dirty() was never called", frm.dirtied, 0);
check("empty stored ID: the field was redrawn", frm.refreshed.join(","), "custom_project_id");

// 2. Stored value stale (renamed Project): same.
frm = makeFrm({ name: "PRJ-00747", custom_project_id: "OLD-NAME" }, false);
handlers.refresh(frm);
check("stale stored ID: the name is shown", frm.doc.custom_project_id, "PRJ-00747");
check("stale stored ID: the form is NOT marked changed", frm.is_dirty(), false);

// 3. Already correct: nothing to do, no redraw needed, never dirty.
frm = makeFrm({ name: "PRJ-00001", custom_project_id: "PRJ-00001" }, false);
handlers.refresh(frm);
check("matching stored ID: the form is not dirty", frm.is_dirty(), false);
check("matching stored ID: no redraw", frm.refreshed.length, 0);

// 4. A person's real edit made earlier still counts: refresh must not clear a dirty form.
frm = makeFrm({ name: "PRJ-00002", custom_project_id: null, project_name: "x" }, false);
frm.set_value("project_name", "y");
handlers.refresh(frm);
check("a real edit keeps the form dirty through a refresh", frm.is_dirty(), true);
check("the mirrored value is on the doc a save would send", frm.doc.custom_project_id, "PRJ-00002");

// 5. A new (unsaved) Project is left alone: no name yet to mirror.
frm = makeFrm({ name: "new-project-abc", custom_project_id: null }, true);
handlers.refresh(frm);
check("new Project: the ID is untouched", frm.doc.custom_project_id, null);
check("new Project: nothing redrawn", frm.refreshed.length, 0);

console.log(failures ? `${failures} of ${checks} checks FAILED` : `all ${checks} checks passed`);
process.exit(failures ? 1 : 0);
