/**
 * AI Pending Action form — Confirm / Cancel buttons.
 *
 * Calls the whitelisted endpoints in assistant_tools/gating_api.py by dotted
 * path (frappe.call resolves them at request time — no Python import, which
 * keeps the FAC-optional tripwire green). Buttons only show on Pending rows
 * for the requester or a System Manager; the server re-checks identity and
 * expiry regardless.
 */
frappe.ui.form.on("AI Pending Action", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;

		const me = frappe.session.user;
		const may_decide =
			me === frm.doc.requested_by || frappe.user_roles.includes("System Manager");
		if (!may_decide) return;

		frm.add_custom_button(__("Confirm & Execute"), () => {
			frappe.confirm(
				__("Execute this AI action now?<br><br><b>{0}</b> (risk: {1})", [
					frappe.utils.escape_html(frm.doc.summary || frm.doc.tool_name),
					frm.doc.risk || "?",
				]),
				() => {
					frappe.call({
						method: "erpnext_enhancements.assistant_tools.gating_api.confirm_action",
						args: { name: frm.doc.name },
						freeze: true,
						freeze_message: __("Executing..."),
						callback: () => {
							frappe.show_alert({ message: __("Executed."), indicator: "green" });
							frm.reload_doc();
						},
						error: () => frm.reload_doc(),
					});
				}
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("Cancel Action"), () => {
			frappe.call({
				method: "erpnext_enhancements.assistant_tools.gating_api.cancel_action",
				args: { name: frm.doc.name },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Cancelled."), indicator: "orange" });
					frm.reload_doc();
				},
			});
		});
	},
});
