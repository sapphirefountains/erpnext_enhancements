// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Account Data Quality"] = {
	filters: [
		{
			fieldname: "gap",
			label: __("Missing"),
			fieldtype: "Select",
			options: ["Any", "Industry", "Customer Group", "Value Stream"].join("\n"),
			default: "Any",
		},
		{
			fieldname: "customer_type",
			label: __("Customer Type"),
			fieldtype: "Select",
			options: ["", "Commercial", "Company", "Residential", "Individual", "Partnership"].join("\n"),
		},
		{
			fieldname: "commercial_only",
			label: __("Commercial accounts only"),
			fieldtype: "Check",
			default: 0,
		},
		{
			// The 364-row un-migrated import: customer_type 'Company' AND missing
			// both industry and group. One bad import, not 364 decisions.
			fieldname: "legacy_only",
			label: __("Legacy import only"),
			fieldtype: "Check",
			default: 0,
		},
	],

	onload(report) {
		const selected = () => {
			const rows = report.datatable?.rowmanager?.getCheckedRows() || [];
			return rows
				.map((i) => report.data[i])
				.filter(Boolean)
				.map((row) => row.name);
		};

		const guard = (names) => {
			if (!names.length) {
				frappe.msgprint(__("Tick the rows you want to update first."));
				return false;
			}
			return true;
		};

		const applyScalar = (fieldname, label, linkDoctype) => {
			const names = selected();
			if (!guard(names)) return;

			// A prompt, not a derived value. The whole point of this tool is that a
			// person decides and the machine only saves keystrokes.
			frappe.prompt(
				[
					{
						fieldname: "value",
						label: label,
						fieldtype: linkDoctype ? "Link" : "Data",
						options: linkDoctype,
						reqd: 1,
					},
				],
				({ value }) => {
					frappe.call({
						method: "erpnext_enhancements.crm_enhancements.data_quality.bulk_assign",
						args: { customers: names, fieldname, value },
						freeze: true,
						freeze_message: __("Assigning {0} to {1} account(s)…", [label, names.length]),
						callback: ({ message }) => {
							if (!message) return;
							// Rows that already had a value are SKIPPED, never overwritten —
							// report that honestly rather than claiming a clean sweep.
							const skipped = (message.skipped || []).length;
							frappe.show_alert({
								message: __("{0} updated, {1} skipped", [message.updated, skipped]),
								indicator: message.updated ? "green" : "orange",
							});
							report.refresh();
						},
					});
				},
				__("Assign {0}", [label]),
				__("Assign")
			);
		};

		report.page.add_inner_button(__("Assign Customer Group"), () =>
			applyScalar("customer_group", __("Customer Group"), "Customer Group")
		);
		report.page.add_inner_button(__("Assign Industry"), () =>
			applyScalar("industry", __("Industry"), "Industry Type")
		);
		report.page.add_inner_button(__("Assign Territory"), () =>
			applyScalar("territory", __("Territory"), "Territory")
		);

		report.page.add_inner_button(__("Add Value Streams"), () => {
			const names = selected();
			if (!guard(names)) return;
			frappe.prompt(
				[
					{
						fieldname: "streams",
						label: __("Value Streams"),
						fieldtype: "MultiSelectList",
						reqd: 1,
						// NOTE the naming trap: the MASTER list is the PLURAL doctype.
						// The singular "Value Stream" is the child table. See
						// crm_enhancements/README.md.
						get_data: (txt) =>
							frappe.db.get_link_options("Value Streams", txt),
					},
				],
				({ streams }) => {
					frappe.call({
						method: "erpnext_enhancements.crm_enhancements.data_quality.assign_value_streams",
						args: { customers: names, value_streams: streams },
						freeze: true,
						freeze_message: __("Adding value streams to {0} account(s)…", [names.length]),
						callback: ({ message }) => {
							if (!message) return;
							frappe.show_alert({
								message: __("{0} updated, {1} skipped", [
									message.updated,
									(message.skipped || []).length,
								]),
								indicator: message.updated ? "green" : "orange",
							});
							report.refresh();
						},
					});
				},
				__("Add Value Streams"),
				__("Add")
			);
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "customer_type" && data && data.is_legacy) {
			return `<span style="color: var(--orange-500);" title="${__(
				"Un-migrated legacy import"
			)}">${formatted}</span>`;
		}
		return formatted;
	},
};
