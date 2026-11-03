/* The New Service Call intake dialog (v1.777.0).
 *
 * One screen for a client who phones in without an agreement (Nikolas, 2026-11-03): who and
 * where, what kind of visit, when, and the scope in plain words. Create makes, in order, the
 * site Address (found or new), the Opportunity at its first status, the Service project on the
 * three-task "Service Call" template and the Service Call linked to both
 * (sapphire_maintenance/service_calls.create_service_call), then opens the call.
 *
 * The Customer is a Link field: a customer who is not there yet is made through frappe's own
 * quick entry from the field ("Create a new Customer"), so every account goes through the same
 * naming checks as one made by hand. The Address is a Link to the customer's addresses, or the
 * new-address lines below it when none is picked.
 *
 * Loaded with the Service Call form (doctype_js), which opens it on a new, empty form. Cancel
 * leaves the form for a call logged by hand.
 */

(function () {
	"use strict";

	const SMALL_REPAIR = "Small Repair";

	function rate_text(options, service_type, rate_kind) {
		const key = `${service_type}|${rate_kind}`;
		const rate = (options.rates || {})[key];
		if (rate === undefined) return __("No rate entered yet for {0} ({1}): the call is created without one.", [service_type, rate_kind]);
		return __("Rate on file: {0}", [format_currency(rate)]);
	}

	window.ee_service_call_intake = function (frm) {
		frappe.call({
			method: "erpnext_enhancements.sapphire_maintenance.service_calls.intake_options",
			callback: (r) => open_dialog(frm, r.message || {}),
		});
	};

	function open_dialog(frm, options) {
		const technicians = (options.technicians || []).map((t) => ({ label: t.full_name || t.name, value: t.name }));
		const dialog = new frappe.ui.Dialog({
			title: __("New Service Call"),
			size: "large",
			fields: [
				{ fieldtype: "Section Break", label: __("Who and where") },
				{ fieldtype: "Link", fieldname: "customer", label: __("Customer"), options: "Customer", reqd: 1 },
				{ fieldtype: "Data", fieldname: "contact_name", label: __("Contact name") },
				{ fieldtype: "Data", fieldname: "contact_phone", label: __("Phone"), options: "Phone" },
				{ fieldtype: "Data", fieldname: "contact_email", label: __("Email"), options: "Email" },
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Link",
					fieldname: "site_address",
					label: __("Site address"),
					options: "Address",
					description: __("Pick one of the customer's addresses, or type a new one below."),
					get_query: () => ({
						query: "frappe.contacts.doctype.address.address.address_query",
						filters: { link_doctype: "Customer", link_name: dialog.get_value("customer") || "" },
					}),
				},
				{ fieldtype: "Data", fieldname: "address_line1", label: __("New address: street"), depends_on: "eval:!doc.site_address" },
				{ fieldtype: "Data", fieldname: "city", label: __("City"), depends_on: "eval:!doc.site_address" },
				{ fieldtype: "Data", fieldname: "state", label: __("State"), default: "Utah", depends_on: "eval:!doc.site_address" },
				{ fieldtype: "Data", fieldname: "pincode", label: __("ZIP"), depends_on: "eval:!doc.site_address" },
				{ fieldtype: "Link", fieldname: "serial_no", label: __("Water feature (optional)"), options: "Serial No" },

				{ fieldtype: "Section Break", label: __("The job") },
				{
					fieldtype: "Select",
					fieldname: "service_type",
					label: __("Service type"),
					options: (options.service_types || ["Startup", "Winterization", "Maintenance Visit", SMALL_REPAIR]).join("\n"),
					reqd: 1,
					onchange: () => {
						const type = dialog.get_value("service_type");
						dialog.set_value("rate_kind", type === SMALL_REPAIR ? "As-needed" : "Flat");
						show_rate();
					},
				},
				{
					fieldtype: "Select",
					fieldname: "rate_kind",
					label: __("Rate kind"),
					options: ["As-needed"].concat((options.hourly_kinds || ["Emergency", "After-hours Emergency"]).filter((k) => k !== "As-needed")).join("\n"),
					default: "As-needed",
					depends_on: `eval:doc.service_type == "${SMALL_REPAIR}"`,
					onchange: () => show_rate(),
				},
				{ fieldtype: "HTML", fieldname: "rate_note" },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "scheduled_date", label: __("Planned day of the visit") },
				{
					fieldtype: "Select",
					fieldname: "technician",
					label: __("Technician"),
					options: [{ label: "", value: "" }].concat(technicians),
				},
				{ fieldtype: "Section Break", label: __("Scope") },
				{
					fieldtype: "Text Editor",
					fieldname: "scope",
					label: __("What the client asked for"),
					description: __("In plain words. This is what the client reads on the approval page."),
				},
			],
			primary_action_label: __("Create"),
			primary_action: (values) => {
				if (!values.service_type) return frappe.msgprint(__("Pick the service type."));
				if (values.service_type !== SMALL_REPAIR) values.rate_kind = "Flat";
				dialog.get_primary_btn().prop("disabled", true);
				frappe.call({
					method: "erpnext_enhancements.sapphire_maintenance.service_calls.create_service_call",
					args: { values: values },
					freeze: true,
					freeze_message: __("Creating the opportunity, the project and the service call..."),
					callback: (r) => {
						const made = r.message || {};
						dialog.hide();
						if (!made.service_call) return;
						frappe.show_alert({
							message: __("Created {0}, project {1} and opportunity {2}", [made.service_call, made.project, made.opportunity]),
							indicator: "green",
						});
						// The empty new form's history entry is replaced by the call that was made, so
						// Back does not land on the empty form and open this dialog again.
						frappe.route_flags.replace_route = true;
						frappe.set_route("Form", "Service Call", made.service_call);
					},
					always: () => dialog.get_primary_btn().prop("disabled", false),
				});
			},
		});

		function show_rate() {
			const type = dialog.get_value("service_type");
			const kind = type === SMALL_REPAIR ? dialog.get_value("rate_kind") || "As-needed" : "Flat";
			const note = type ? rate_text(options, type, kind) : "";
			dialog.fields_dict.rate_note.$wrapper.html(note ? `<p class="text-muted small">${frappe.utils.escape_html(note)}</p>` : "");
		}

		if (frm && frm.doc && frm.doc.customer) dialog.set_value("customer", frm.doc.customer);
		dialog.show();
	}
})();
