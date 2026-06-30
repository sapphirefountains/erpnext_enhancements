// Fleet Vehicle form — quick "Log Maintenance" action + a status headline.
frappe.ui.form.on("Fleet Vehicle", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Log Maintenance"), () => {
			frappe.new_doc("Vehicle Maintenance Log", { vehicle: frm.doc.name });
		});

		const colors = { Overdue: "red", "Due Soon": "orange", OK: "green", "No Data": "gray" };
		const status = frm.doc.maintenance_status;
		if (status && colors[status]) {
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline(
				`<span class="indicator-pill ${colors[status]}">${__("Maintenance")}: ${__(status)}</span>`
			);
		}
	},
});
