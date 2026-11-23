// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/* The Service Call form (v1.778.0).
 *
 * A NEW form opens the New Service Call intake dialog over itself
 * (public/js/sapphire_maintenance/service_call_intake.js): one screen that creates the Address,
 * the Opportunity at its first status, the Service project on the three-task template and the
 * Service Call, in that order. The workspace's "New Service Call" shortcut and the list's "+ Add"
 * both land here. Cancel leaves the plain form for a call logged by hand against a project that
 * already exists.
 *
 * A SAVED form shows the approval line and the buttons the status allows: Send for Approval
 * (Requested, Sent, Declined), Schedule Visit (Approved), Open Visit (Scheduled onward), Cancel.
 */

frappe.ui.form.on("Service Call", {
	onload: function (frm) {
		if (frm.is_new() && !frm.doc.project && window.ee_service_call_intake) {
			window.ee_service_call_intake(frm);
		}
	},

	refresh: function (frm) {
		frm.set_query("site_address", function () {
			return {
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: { link_doctype: "Customer", link_name: frm.doc.customer || "" },
			};
		});
		frm.set_query("project", function () {
			const filters = { status: ["!=", "Cancelled"] };
			if (frm.doc.customer) filters.customer = frm.doc.customer;
			return { filters: filters };
		});
		frm.set_query("template", function () {
			return { filters: { status: ["in", ["Active", "Draft"]] } };
		});

		if (frm.is_new()) return;
		ee_svc_render_approval(frm);
		ee_svc_buttons(frm);
	},

	service_type: function (frm) {
		if (frm.doc.service_type === "Small Repair") {
			if (!frm.doc.rate_kind || frm.doc.rate_kind === "Flat") frm.set_value("rate_kind", "As-needed");
		} else {
			frm.set_value("rate_kind", "Flat");
		}
	},
});

function ee_svc_render_approval(frm) {
	frappe.call({
		method: "erpnext_enhancements.sapphire_maintenance.service_call_approval.get_approval_status",
		args: { service_call: frm.doc.name },
		callback: function (r) {
			const status = r.message || {};
			frm._ee_svc_status = status;
			frm.dashboard.set_headline(__("Client approval: {0}", [frappe.utils.escape_html(status.summary || "")]));
			if (status.can_send) {
				const label = status.request ? __("Re-send Approval Link") : __("Send for Approval");
				frm.add_custom_button(label, () => ee_svc_send_dialog(frm, status), null);
				if (!status.request) frm.page.set_primary_action(__("Send for Approval"), () => ee_svc_send_dialog(frm, status));
			}
		},
	});
}

function ee_svc_buttons(frm) {
	const doc = frm.doc;
	if (doc.status === "Approved" && !doc.maintenance_record) {
		frm.add_custom_button(__("Schedule Visit"), () => ee_svc_schedule_dialog(frm), null);
	}
	if (doc.maintenance_record) {
		frm.add_custom_button(__("Open Visit"), () => frappe.set_route("visit-wizard", doc.maintenance_record), null);
		frm.add_custom_button(__("Maintenance Record"), () => frappe.set_route("Form", "Sapphire Maintenance Record", doc.maintenance_record), __("View"));
	}
	if (doc.project) frm.add_custom_button(__("Project"), () => frappe.set_route("Form", "Project", doc.project), __("View"));
	if (doc.opportunity) frm.add_custom_button(__("Opportunity"), () => frappe.set_route("Form", "Opportunity", doc.opportunity), __("View"));
	if (doc.sales_invoice) frm.add_custom_button(__("Sales Invoice"), () => frappe.set_route("Form", "Sales Invoice", doc.sales_invoice), __("View"));
	if (!["Done", "Invoiced", "Canceled"].includes(doc.status)) {
		frm.add_custom_button(__("Cancel Service Call"), () => ee_svc_cancel(frm), null);
	}
}

function ee_svc_send_dialog(frm, status) {
	const fields = [
		{
			fieldtype: "HTML",
			fieldname: "intro",
			options: `<p class="text-muted">${__(
				"Emails the client a link with the summary, the rate and our terms, and one Approve button. The link works for 14 days."
			)}</p>`,
		},
		{ fieldtype: "Data", fieldname: "email", label: __("Client email"), options: "Email", reqd: 1, default: status.default_email || "" },
	];
	if (status.sms_available) {
		fields.push({ fieldtype: "Check", fieldname: "via_sms", label: __("Also text the link"), default: status.default_phone ? 1 : 0 });
		fields.push({
			fieldtype: "Data",
			fieldname: "phone",
			label: __("Client mobile"),
			options: "Phone",
			default: status.default_phone || "",
			depends_on: "eval:doc.via_sms",
			mandatory_depends_on: "eval:doc.via_sms",
		});
	} else {
		fields.push({ fieldtype: "HTML", fieldname: "no_sms", options: `<p class="text-muted small">${__("Texting is not set up on this site, so the link goes by email only.")}</p>` });
	}
	const dialog = new frappe.ui.Dialog({
		title: status.request ? __("Re-send the approval link") : __("Send for approval"),
		fields: fields,
		primary_action_label: __("Send"),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: "erpnext_enhancements.sapphire_maintenance.service_call_approval.send_approval_request",
				args: { service_call: frm.doc.name, email: values.email, phone: values.phone || "", via_sms: values.via_sms ? 1 : 0 },
				freeze: true,
				freeze_message: __("Sending..."),
				callback: (r) => {
					const result = r.message || {};
					if (result.sms_error) frappe.msgprint({ message: result.sms_error, indicator: "orange" });
					frappe.show_alert({ message: __("Approval link sent to {0}", [values.email]), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}

function ee_svc_schedule_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Schedule the visit"),
		fields: [
			{ fieldtype: "Date", fieldname: "scheduled_date", label: __("Day of the visit"), reqd: 1, default: frm.doc.scheduled_date },
			{ fieldtype: "Link", fieldname: "technician", label: __("Technician"), options: "User", default: frm.doc.technician },
		],
		primary_action_label: __("Draft the visit"),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: "erpnext_enhancements.sapphire_maintenance.service_calls.schedule_visit",
				args: { service_call: frm.doc.name, scheduled_date: values.scheduled_date, technician: values.technician },
				freeze: true,
				callback: (r) => {
					const result = r.message || {};
					if (result.record) frappe.show_alert({ message: __("Visit {0} drafted", [result.record]), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}

function ee_svc_cancel(frm) {
	frappe.prompt(
		[{ fieldtype: "Small Text", fieldname: "reason", label: __("Why?"), reqd: 1 }],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.sapphire_maintenance.service_calls.cancel_service_call",
				args: { service_call: frm.doc.name, reason: values.reason },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		},
		__("Cancel this service call"),
		__("Cancel it")
	);
}
