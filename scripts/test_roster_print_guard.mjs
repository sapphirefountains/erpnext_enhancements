#!/usr/bin/env node
/**
 * The Crew Qualification Roster print guard — executed, not grepped.
 *
 * WHY THIS FILE EXISTS
 * ====================
 *
 * Because the last defect on this report was a template that had never once been
 * rendered. `crew_qualification_roster.html` carried a double brace inside an HTML
 * comment; `frappe.template.compile` rewrites those across the whole file before it
 * parses anything, so the prose became a live read of a name that is not in the render
 * context, and under the `with(obj)` scope the compiled template runs in that is a
 * ReferenceError. Print and Download PDF produced **nothing** from v1.397.0 to
 * v1.424.0 and every static check in the suite stayed green, because reading the file
 * cannot tell you whether it runs.
 *
 * So the guard added in v1.425.0 is asserted by running it. It wraps
 * `print_report`/`pdf_report` on the report instance, which is a real monkey-patch
 * against core's prototype methods, and a static assertion that the wrapper exists
 * would say nothing about whether core's menu ever reaches it.
 *
 * WHAT IT GUARDS
 * ==============
 *
 * `query_report.js` on version-16:
 *
 *     get_print_template(print_settings, custom_format) {
 *         return print_settings.columns?.length || !custom_format ? "print_grid" : custom_format;
 *     }
 *
 * so ticking "Pick Columns" (whose MultiCheck is `select_all: true`) replaces the
 * designed evidence sheet with an unformatted grid — no heading, no as-of date, none
 * of the caveats, and it loops `data`, the on-screen rows, so an inline filter drops
 * rows from it silently. Choosing a Print Format substitutes it wholesale. There is no
 * server-side defence in v16, so the guard is a warning the operator must dismiss
 * rather than a block: it removes the *silent* part, which is the defect.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPORT_JS = path.join(
	HERE,
	"..",
	"erpnext_enhancements",
	"hr_enhancements",
	"report",
	"crew_qualification_roster",
	"crew_qualification_roster.js"
);

let failures = 0;
let checks = 0;

function test(name, fn) {
	checks += 1;
	try {
		fn();
		console.log("  ok    " + name);
	} catch (err) {
		failures += 1;
		console.error("  FAIL  " + name);
		console.error("        " + (err && err.message ? err.message : err));
	}
}

// ---------------------------------------------------------------- the desk stubs

const calls = { printed: [], warned: [] };

globalThis.__ = (text) => text;
globalThis.frappe = {
	query_reports: {},
	datetime: { get_today: () => "2026-09-13" },
	warn(title, message_html, proceed_action, primary_label) {
		calls.warned.push({ title, message_html, proceed_action, primary_label });
	},
};

vm.runInThisContext(fs.readFileSync(REPORT_JS, "utf8"), { filename: REPORT_JS });

const settings = globalThis.frappe.query_reports["Crew Qualification Roster"];
assert.ok(settings, "the report did not register itself under its own name");
assert.equal(typeof settings.onload, "function", "no onload to hang the guard on");

/** A stand-in for core's QueryReport: print_report/pdf_report live on the PROTOTYPE,
 *  which is what makes the instance-property override meaningful. */
function makeReport() {
	calls.printed.length = 0;
	calls.warned.length = 0;
	class FakeQueryReport {
		print_report(s) {
			calls.printed.push(["print", s]);
		}
		pdf_report(s) {
			calls.printed.push(["pdf", s]);
		}
	}
	const report = new FakeQueryReport();
	report.page = { add_inner_button: () => {} };
	report.set_filter_value = () => {};
	settings.onload(report);
	return report;
}

// ------------------------------------------------------------------- the guard

test("the ordinary print is not interrupted", () => {
	const report = makeReport();
	report.print_report({ orientation: "Landscape" });
	assert.equal(calls.printed.length, 1, "a clean print should go straight through");
	assert.equal(calls.warned.length, 0, "nothing to warn about");
});

test("an unticked Pick Columns still prints the designed sheet", () => {
	// get_print_settings ends with `if (!settings.pick_columns) settings.columns = null`,
	// so this is the shape the dialog really sends. A guard that fired here would
	// interrupt every ordinary print and get switched off within a week.
	const report = makeReport();
	report.print_report({ columns: null });
	report.print_report({ columns: [] });
	assert.equal(calls.printed.length, 2);
	assert.equal(calls.warned.length, 0);
});

test("ticking Pick Columns warns instead of printing", () => {
	const report = makeReport();
	report.print_report({ columns: ["employee_name", "verdict"] });
	assert.equal(calls.printed.length, 0, "it must not print before the operator decides");
	assert.equal(calls.warned.length, 1);
});

test("the warning says what is actually lost", () => {
	const report = makeReport();
	report.print_report({ columns: ["employee_name"] });
	const body = calls.warned[0].message_html;
	assert.match(body, /not cleared to work alone/, "the colouring that matters most");
	assert.match(body, /as-of date/, "the date the whole report answers");
	assert.match(body, /sorted and filtered on\s*screen/, "the rows it silently drops");
	assert.match(body, /insurer/, "who the document is for");
});

test("proceeding prints exactly what was asked for", () => {
	const report = makeReport();
	report.print_report({ columns: ["a", "b"], orientation: "Portrait" });
	calls.warned[0].proceed_action();
	assert.equal(calls.printed.length, 1);
	assert.deepEqual(calls.printed[0][1].columns, ["a", "b"], "the settings must pass through intact");
	assert.equal(calls.printed[0][1].orientation, "Portrait");
});

test("cancelling prints nothing at all", () => {
	const report = makeReport();
	report.print_report({ columns: ["a"] });
	// no proceed_action call -- the operator closed the dialog
	assert.equal(calls.printed.length, 0);
});

test("a Print Format is intercepted on the PDF path too", () => {
	// Unreachable on this report today (the field filters Print Format to ones scoped
	// to this report, and all nine on the site belong to accounting reports), but it
	// becomes reachable the day somebody creates one.
	const report = makeReport();
	report.pdf_report({ print_format: "Some Report Format" });
	assert.equal(calls.printed.length, 0);
	assert.equal(calls.warned.length, 1);
});

test("both entry points are guarded, not just Print", () => {
	const report = makeReport();
	report.pdf_report({ columns: ["a"] });
	assert.equal(calls.warned.length, 1, "PDF is the one that reaches an insurer by email");
});

test("a second onload does not double-wrap", () => {
	const report = makeReport();
	settings.onload(report);
	report.print_report({ columns: ["a"] });
	assert.equal(calls.warned.length, 1);
	// The symptom of a double wrap is NOT two warnings at this point -- the outer
	// wrapper short-circuits into frappe.warn and never reaches the inner one, so the
	// obvious assertion here passes either way. It is that PROCEEDING warns a second
	// time instead of printing, leaving the operator unable to get the grid they
	// explicitly asked for. Checking the wrong symptom is how this test survived a
	// mutation that removed the flag entirely.
	calls.warned[0].proceed_action();
	assert.equal(calls.printed.length, 1, "proceeding must print, not warn again");
	assert.equal(calls.warned.length, 1, "a second warning is a second wrapper");
});

test("the guard is a wrapper, not a replacement", () => {
	// Vacuity check: if makeReport's prototype methods were never reached, every
	// assertion above about `printed` would pass for the wrong reason.
	const report = makeReport();
	report.print_report({});
	assert.deepEqual(calls.printed[0], ["print", {}]);
});

console.log("");
if (failures) {
	console.error(`roster print guard: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`roster print guard: ${checks} assertions passed`);
