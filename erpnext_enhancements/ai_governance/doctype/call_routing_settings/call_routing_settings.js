// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// "Test Routing" — ask the server who would ring for a hypothetical call.
//
// The whole point of this button is that a routing table you cannot interrogate is a
// routing table nobody trusts. Rules are ordered and first-match-wins, so the interesting
// question is never "what does rule 3 do" but "which rule wins at 7pm on a Saturday, and
// why did the other four not". The server answers both.

frappe.ui.form.on("Call Routing Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Routing"), () => show_preview_dialog(frm));
		frm.add_custom_button(__("Routing Rules"), () => frappe.set_route("List", "Call Routing Rule"));
	},
});

function show_preview_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Who would ring?"),
		fields: [
			{
				fieldname: "from_number",
				fieldtype: "Data",
				label: __("Calling From"),
				description: __("Leave blank to test an unknown caller."),
			},
			{
				fieldname: "intent",
				fieldtype: "Select",
				label: __("Menu Selection"),
				options: ["General", "Rental", "Design", "Build", "Service"],
				default: "General",
			},
			{
				fieldname: "at",
				fieldtype: "Datetime",
				label: __("At"),
				description: __("Office time. Leave blank for right now."),
			},
		],
		primary_action_label: __("Check"),
		primary_action(values) {
			frappe.call({
				method: "erpnext_enhancements.api.telephony.preview_call_routing",
				args: {
					from_number: values.from_number || "",
					intent: values.intent || "General",
					at: values.at || "",
				},
				freeze: true,
				callback(r) {
					if (!r.message) return;
					dialog.hide();
					frappe.msgprint({
						title: __("Routing preview"),
						indicator: "blue",
						message: render_preview(r.message),
					});
				},
			});
		},
	});
	dialog.show();
	// The preview reads what is stored, not what is on screen. Say so rather than quietly
	// answering a question about a configuration that does not exist yet.
	if (frm.is_dirty()) {
		frappe.show_alert({ message: __("Unsaved changes are not included in the preview."), indicator: "orange" });
	}
}

function render_preview(data) {
	const plan = data.plan || {};
	const rows = [];

	rows.push(row(__("Rule"), plan.rule ? frappe.utils.escape_html(plan.rule) : __("none matched")));
	rows.push(row(__("Why"), frappe.utils.escape_html(plan.reason || "")));

	if (plan.voicemail) {
		rows.push(row(__("Outcome"), __("Straight to voicemail — no phones ring.")));
	} else {
		const legs = [];
		(plan.numbers || []).forEach((n) => legs.push(frappe.utils.escape_html(n)));
		(plan.clients || []).forEach((c) => legs.push(frappe.utils.escape_html(c) + " " + __("(softphone)")));
		if (plan.account_manager) legs.push(__("the caller's account manager"));
		if (plan.include_softphones) {
			const desk = data.softphone_identities || [];
			legs.push(__("the usual desk softphones") + (desk.length ? " (" + desk.length + ")" : ""));
		}
		rows.push(row(__("Rings"), legs.length ? legs.join("<br>") : __("nobody")));
		rows.push(row(__("For"), (plan.ring_seconds || 0) + " " + __("seconds")));
	}

	const skipped = (plan.considered || []).map(
		(c) => `<li>${frappe.utils.escape_html(c.rule)} — ${frappe.utils.escape_html(c.skipped)}</li>`
	);
	if (skipped.length) {
		rows.push(row(__("Rules passed over"), `<ul style="margin:0;padding-left:1.1em">${skipped.join("")}</ul>`));
	}

	const warnings = (data.warnings || []).map((w) => `<li>${frappe.utils.escape_html(w)}</li>`);
	if (warnings.length) {
		rows.push(
			row(
				__("Warnings"),
				`<ul style="margin:0;padding-left:1.1em;color:var(--text-on-orange,#8a5300)">${warnings.join("")}</ul>`
			)
		);
	}

	return `<table class="table table-bordered" style="margin:0">${rows.join("")}</table>`;
}

function row(label, value) {
	return `<tr><td style="width:11rem"><b>${label}</b></td><td>${value}</td></tr>`;
}
