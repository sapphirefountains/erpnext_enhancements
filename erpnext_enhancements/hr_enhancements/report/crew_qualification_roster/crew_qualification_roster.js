// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/* Crew Qualification Roster filters, and a guard on the print dialog.
 *
 * The Employee link deliberately does NOT filter on `status = "Active"`. The whole
 * point of an as-of date is that it can answer a question about somebody who has
 * since left, and after an incident a leaver is exactly who gets asked about. A
 * status filter here would make them unselectable and the report would look complete.
 *
 * ---------------------------------------------------------------------------
 * THE PRINT DIALOG CAN REPLACE THE DESIGNED SHEET, AND v16 OFFERS NO SERVER-SIDE
 * DEFENCE. This is a UI guard, not a permission boundary -- the same distinction
 * this app's own guardrails draw about `Desktop Icon.roles`. Somebody determined to
 * print a grid still can, and should be able to; what they should not be able to do
 * is get one SILENTLY on the document they are about to hand an insurer.
 *
 * Two routes into core's `print_grid`, read from query_report.js on version-16:
 *
 *   get_print_template(print_settings, custom_format) {
 *       return print_settings.columns?.length || !custom_format ? "print_grid" : custom_format;
 *   }
 *
 *   1. TICKING "Pick Columns". Its MultiCheck carries `select_all: true`, so one
 *      click selects every column and `columns.length` becomes truthy -- and that
 *      alone routes to print_grid. This is the live one: one checkbox.
 *   2. CHOOSING A PRINT FORMAT, which `get_custom_format` substitutes wholesale.
 *      Currently unreachable HERE: the field's get_query filters Print Format to
 *      `print_format_for = "Report"` AND `report = <this report>`, and all nine such
 *      rows on this site belong to accounting reports. It becomes reachable the day
 *      somebody creates one for this report, which is why the guard covers it.
 *
 * What print_grid actually costs: the heading, the as-of date, the caveat paragraph,
 * the not-cleared colouring and the footer -- and it loops `data`, the on-screen rows,
 * so an inline column filter silently drops rows from it. The designed sheet reads
 * `original_data` for precisely that reason (v1.424.0).
 *
 * Note what is NOT a bypass: leaving "Pick Columns" unticked. `get_print_settings`
 * ends with `if (!settings.pick_columns) { settings.columns = null; }`, so the
 * MultiCheck's select-all default cannot leak through on its own.
 */

const SHEET_WARNING = __(
	"<p><b>This will print an unformatted column grid, not the roster sheet.</b></p>" +
		"<p>The grid has no heading, no as-of date, none of the caveats about what could " +
		"not be reconstructed, and no highlighting of anybody who is <b>not cleared to " +
		"work alone</b>. It also prints the rows as currently sorted and filtered on " +
		"screen, so any row hidden by a column filter is missing from it without saying " +
		"so.</p>" +
		"<p>This report is written to be handed to an insurer after an incident. If that " +
		"is what this copy is for, cancel and clear <b>Pick Columns</b> and <b>Print " +
		"Format</b>.</p>"
);

/* Wrap the two print entry points so neither can swap the sheet without saying so.
 *
 * An instance property, assigned over the prototype method: the menu items are built
 * as `(print_settings) => this.print_report(print_settings)`, so the lookup happens
 * when the dialog's callback fires rather than when the menu was built, and the
 * override is what it finds. Flagged so a second `onload` cannot double-wrap.
 */
function guard_the_designed_sheet(report) {
	["print_report", "pdf_report"].forEach(function (name) {
		const current = report[name];
		if (typeof current !== "function" || current.cqr_guarded) {
			return;
		}
		const original = current.bind(report);
		const guarded = function (print_settings) {
			const settings = print_settings || {};
			const picked = !!(settings.columns && settings.columns.length);
			const substituted = !!settings.print_format;
			if (!picked && !substituted) {
				return original(settings);
			}
			frappe.warn(
				__("This will not print the roster sheet"),
				SHEET_WARNING,
				function () {
					original(settings);
				},
				__("Print the grid anyway")
			);
		};
		guarded.cqr_guarded = true;
		report[name] = guarded;
	});
}


frappe.query_reports["Crew Qualification Roster"] = {
	filters: [
		{
			fieldname: "as_of",
			label: __("As of"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
			description: __(
				"Everything is re-derived from stored dates as at this day. Facts with no recorded date are reported as not reconstructible rather than given today's value."
			),
		},
		{
			fieldname: "employee",
			label: __("Person"),
			fieldtype: "Link",
			options: "Employee",
			get_query: function () {
				// No status filter -- see the header.
				return {};
			},
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
	],

	onload: function (report) {
		report.page.add_inner_button(__("Today"), function () {
			report.set_filter_value("as_of", frappe.datetime.get_today());
		});
		guard_the_designed_sheet(report);
	},
};
