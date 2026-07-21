/*
 * Fountain Move Request form: triage actions and a summary of what converted.
 *
 * Retry / Mark as Spam are role-gated server-side but NOT feature-flag gated —
 * a backlog must stay workable with the public form switched off, so these
 * buttons deliberately appear regardless of frappe.boot.ee_fountain_move.
 */

frappe.ui.form.on("Fountain Move Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		show_status_banner(frm);
		add_triage_buttons(frm);
		add_record_links(frm);
	},
});

function show_status_banner(frm) {
	const banners = {
		"Duplicate Review": [
			__(
				"This submission matches more than one existing account, so nothing was created. Decide which account it belongs to, then press Retry Conversion."
			),
			"orange",
		],
		Failed: [
			__(
				"Conversion failed — see Conversion Error below. The customer's details are safe; fix the cause and press Retry Conversion."
			),
			"red",
		],
		Spam: [
			__("Marked as spam. Nothing was created from this submission."),
			"grey",
		],
		Converting: [__("Conversion is running right now."), "blue"],
	};
	const banner = banners[frm.doc.status];
	if (banner) {
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(banner[0], banner[1]);
	}
}

function add_triage_buttons(frm) {
	const status = frm.doc.status;

	if (status !== "Converted" && status !== "Converting" && status !== "Spam") {
		frm.add_custom_button(__("Retry Conversion"), () => {
			frappe.confirm(
				__(
					"Re-run conversion for this request? Anything already created is reused, not duplicated."
				),
				() => {
					frappe.call({
						method:
							"erpnext_enhancements.crm_enhancements.fountain_move.api.retry_conversion",
						args: { docname: frm.doc.name },
						freeze: true,
						freeze_message: __("Queueing conversion…"),
						callback() {
							frappe.show_alert(
								{ message: __("Conversion queued."), indicator: "green" },
								5
							);
							setTimeout(() => frm.reload_doc(), 1500);
						},
					});
				}
			);
		});
	}

	if (status !== "Spam" && status !== "Converted") {
		frm.add_custom_button(
			__("Mark as Spam"),
			() => {
				frappe.prompt(
					[
						{
							fieldtype: "Small Text",
							fieldname: "reason",
							label: __("Why?"),
							description: __("Recorded on the request. Optional."),
						},
					],
					(values) => {
						frappe.call({
							method:
								"erpnext_enhancements.crm_enhancements.fountain_move.api.mark_spam",
							args: { docname: frm.doc.name, reason: values.reason },
							freeze: true,
							callback: () => frm.reload_doc(),
						});
					},
					__("Mark as spam"),
					__("Mark as spam")
				);
			},
			__("Actions")
		);
	}

	if (status === "Spam") {
		frm.add_custom_button(__("Not Spam"), () => {
			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.fountain_move.api.mark_not_spam",
				args: { docname: frm.doc.name },
				freeze: true,
				freeze_message: __("Releasing…"),
				callback() {
					frappe.show_alert(
						{
							message: __("Released. Press Retry Conversion to create the records."),
							indicator: "green",
						},
						7
					);
					frm.reload_doc();
				},
			});
		});
	}
}

function add_record_links(frm) {
	// A one-click path to each thing this submission produced. The links are
	// already fields on the form, but a converted request is usually opened to
	// get *to* the opportunity, so make that the shortest possible journey.
	const targets = [
		["created_opportunity", "Opportunity", __("Opportunity")],
		["created_lead", "Lead", __("Lead")],
		["created_customer", "Customer", __("Customer")],
		["created_contact", "Contact", __("Contact")],
		["created_address", "Address", __("Address")],
	];

	targets.forEach(([fieldname, doctype, label]) => {
		if (!frm.doc[fieldname]) return;
		frm.add_custom_button(
			label,
			() => frappe.set_route("Form", doctype, frm.doc[fieldname]),
			__("Open")
		);
	});
}
