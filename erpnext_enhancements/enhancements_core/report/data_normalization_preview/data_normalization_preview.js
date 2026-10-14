// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// A dry run of the phone/email/website/address cleanup, and the place a System Manager applies
// the proposed fixes for what no rule can read (form kit P2-C). Showing the report writes
// nothing; the rules are utils/normalize.py, the fields utils/normalize_fields.py and the
// proposals utils/normalize_fixes.py. "Apply checked fixes" sends only the ticked rows' keys:
// the server works every fix out again (utils/normalize_cleanup.apply_fixes) and writes one only
// where the value is still the one shown, so a stale page can never write a stale proposal.
frappe.query_reports["Data Normalization Preview"] = {
	filters: [
		{
			// Narrows the walk to one registry doctype. A child table (Contact Phone) is its
			// own entry; its rows still link to the parent document.
			fieldname: "doctype",
			label: __("DocType"),
			fieldtype: "Link",
			options: "DocType",
		},
		{
			fieldname: "kind",
			label: __("Kind"),
			fieldtype: "Select",
			options: ["", "phone", "email", "website", "text", "state", "zip"].join("\n"),
		},
		{
			fieldname: "status",
			label: __("Show"),
			fieldtype: "Select",
			options: [
				"Will change + Can't normalize",
				"Will change",
				"Can't normalize — needs a person",
				"Has a proposed fix",
				"Everything, including Already clean",
			].join("\n"),
			default: "Will change + Can't normalize",
		},
	],

	get_datatable_options(options) {
		return Object.assign(options, { checkboxColumn: true });
	},

	onload(report) {
		report.page.add_inner_button(__("Apply checked fixes"), () => apply_checked_fixes(report));
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && data.status) {
			const colour =
				data.status === "Will change"
					? "var(--blue-600)"
					: data.status === "Already clean"
					? "var(--green-600)"
					: "var(--orange-600)";
			return `<span style="color: ${colour}; font-weight: 600;">${formatted}</span>`;
		}
		if (column.fieldname === "proposed" && data && data.fix_key) {
			return `<span style="font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};

function apply_checked_fixes(report) {
	const rows = report.get_checked_items().filter((row) => row && row.fix_key);
	if (!rows.length) {
		frappe.msgprint(
			__(
				"Tick the rows whose proposed fix you want, then press Apply. Only a row with a proposed fix can be applied: Show → Has a proposed fix lists them."
			)
		);
		return;
	}
	frappe.confirm(
		__(
			"Apply {0} proposed fixes? Each is written only where the value is still the one shown here, and an undo file is saved first.",
			[rows.length]
		),
		() =>
			frappe
				.call({
					method: "erpnext_enhancements.utils.normalize_cleanup.apply_fixes",
					args: { keys: JSON.stringify(rows.map((row) => JSON.parse(row.fix_key))) },
					freeze: true,
					freeze_message: __("Applying the fixes…"),
				})
				.then((r) => {
					const s = r.message || {};
					const lines = [
						__("{0} of {1} fixes applied.", [s.written || 0, s.planned || 0]),
					];
					if (s.edited_since) {
						lines.push(
							__("{0} had changed since this page loaded and were left alone.", [
								s.edited_since,
							])
						);
					}
					if (s.not_offered) {
						lines.push(__("{0} no longer have a proposed fix.", [s.not_offered]));
					}
					if (s.failed) {
						lines.push(
							__("{0} could not be written (see the Error Log).", [s.failed])
						);
					}
					if (s.stopped) {
						lines.push(__("Stopped: {0}", [frappe.utils.escape_html(s.stopped)]));
					}
					frappe.msgprint({
						title: __("Proposed fixes"),
						indicator: s.failed || s.stopped ? "orange" : "green",
						message: lines.join("<br>"),
					});
					report.refresh();
				})
	);
}
