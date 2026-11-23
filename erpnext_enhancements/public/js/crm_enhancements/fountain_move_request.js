/*
 * Fountain Move Request form: triage actions and a summary of what converted.
 *
 * Retry / Mark as Spam are role-gated server-side but NOT feature-flag gated —
 * a backlog must stay workable with the public form switched off, so these
 * buttons deliberately appear regardless of frappe.boot.ee_fountain_move.
 *
 * Confirm Match (Duplicate Review only): a staff member picks the existing Customer a
 * held request belongs to; the server links it and creates the Opportunity under it.
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
				"This submission matches an existing record, so nothing was created and nothing was added to that record. Check the match below, then press Confirm Match to link it to the right Customer."
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

function suggested_customer(frm) {
	// match_candidates is the JSON the conversion recorded when it held the request.
	let match = {};
	try {
		match = JSON.parse(frm.doc.match_candidates || "{}") || {};
	} catch (e) {
		match = {};
	}
	if (match.customer) return match.customer;
	const first = (match.candidates || []).find((candidate) => candidate.type === "Customer");
	return first ? first.name : "";
}

function add_confirm_match_button(frm) {
	frm.add_custom_button(__("Confirm Match"), () => {
		frappe.prompt(
			[
				{
					fieldtype: "Link",
					fieldname: "customer",
					label: __("Customer"),
					options: "Customer",
					reqd: 1,
					default: suggested_customer(frm),
					description: __(
						"Links this request to the Customer and creates the Opportunity under it. Nothing on the Customer, its contacts or its addresses is changed; the details on this request are copied into the Opportunity's notes for you to copy across by hand."
					),
				},
			],
			(values) => {
				frappe.call({
					method: "erpnext_enhancements.crm_enhancements.fountain_move.api.confirm_match",
					args: { docname: frm.doc.name, customer: values.customer },
					freeze: true,
					freeze_message: __("Queueing conversion…"),
					callback() {
						frappe.show_alert(
							{ message: __("Match confirmed. Conversion queued."), indicator: "green" },
							5
						);
						setTimeout(() => frm.reload_doc(), 1500);
					},
				});
			},
			__("Confirm match"),
			__("Confirm")
		);
	});
}

function add_triage_buttons(frm) {
	const status = frm.doc.status;

	if (status === "Duplicate Review") {
		add_confirm_match_button(frm);
	}

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
