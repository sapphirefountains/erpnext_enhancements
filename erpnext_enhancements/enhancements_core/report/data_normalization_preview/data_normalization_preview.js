// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// A dry run of the phone/email/website/address cleanup. Nothing here writes; the rules
// are utils/normalize.py and the fields are utils/normalize_fields.py.
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
				"Everything, including Already clean",
			].join("\n"),
			default: "Will change + Can't normalize",
		},
	],

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
		return formatted;
	},
};
