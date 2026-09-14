/*
 * Non Conformance form: acknowledging a Critical alert (WI-075 sub-phase G).
 *
 * The email links here rather than carrying a one-click acknowledge link, and that is a
 * deliberate trade. A tokenised link in an email can be clicked by a mail scanner, a
 * link-preview fetcher or a forwarded copy, and the resulting timestamp would say the
 * President read this when a security appliance opened it. Acknowledging from inside the Desk
 * costs one extra click and means a signed-in human was looking at the record.
 *
 * Registered through `doctype_js` because `Non Conformance` is ERPNext's DocType, not ours.
 * Never do this for a DocType this app owns: frappe already loads
 * <module>/doctype/<name>/<name>.js, `doctype_js` appends to the same string with no dedupe,
 * and a top-level `const` then becomes a SyntaxError that costs the form every button.
 */

frappe.ui.form.on("Non Conformance", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		if (frm.doc.custom_severity !== "Critical") return;
		render_alert_state(frm);
	},
});

function render_alert_state(frm) {
	frappe.call({
		method: "erpnext_enhancements.api.quality_ncr.critical_alert_state",
		args: { ncr: frm.doc.name },
		callback: function (r) {
			const state = r && r.message;
			if (!state) return;

			if (!state.sent_on) {
				// Not yet dispatched. Worth saying, because the gap between "severity set to
				// Critical" and "three people know" is the window this whole sub-phase exists
				// to close, and silence here reads as success.
				frm.dashboard.set_headline_alert(
					__("The Critical alert has not gone out yet. It is queued, and the hourly sweep re-sends anything a deploy interrupted."),
					"orange"
				);
				return;
			}

			show_recipient_summary(frm, state);

			if (state.may_acknowledge && !state.mine_is_acknowledged) {
				frm.page
					.set_primary_action(__("Acknowledge"), function () {
						acknowledge(frm);
					})
					.addClass("btn-danger");
			}
		},
	});
}

function show_recipient_summary(frm, state) {
	const outstanding = state.recipients.filter(function (row) {
		return !row.acknowledged_on;
	});

	if (!outstanding.length) {
		frm.dashboard.set_headline_alert(
			__("All {0} recipients have acknowledged this.", [state.total]),
			"green"
		);
		return;
	}

	// Named, not counted. "Two of three" hides which one is missing, and which one is missing
	// is the only part anybody can act on.
	const names = outstanding
		.map(function (row) {
			return frappe.utils.escape_html(row.full_name || row.user);
		})
		.join(", ");
	frm.dashboard.set_headline_alert(
		__("Waiting on {0}.", [names]),
		"orange"
	);
}

function acknowledge(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Acknowledge this non-conformance"),
		fields: [
			{
				fieldtype: "HTML",
				options:
					"<p>" +
					__("This records that you have seen it, with your name and the time. It does not resolve anything.") +
					"</p>",
			},
			{
				fieldname: "note",
				fieldtype: "Small Text",
				label: __("Note (optional)"),
				description: __("Anything you want on the record alongside your acknowledgement."),
			},
		],
		primary_action_label: __("Acknowledge"),
		primary_action: function (values) {
			dialog.hide();
			frappe.call({
				method: "erpnext_enhancements.api.quality_ncr.acknowledge_critical_alert",
				args: { ncr: frm.doc.name, note: values.note },
				freeze: true,
				freeze_message: __("Recording your acknowledgement..."),
				callback: function (r) {
					if (!r || !r.message) return;
					frappe.show_alert({ message: r.message.message, indicator: "green" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}
