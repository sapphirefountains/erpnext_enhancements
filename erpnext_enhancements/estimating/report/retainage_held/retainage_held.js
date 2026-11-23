// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Retainage Held (estimating B9 follow-up): what each job holds back and has released, from its latest
 * certified pay application. Jobs holding nothing are left out unless "Include jobs holding nothing" is
 * ticked (an unticked box is never sent to the server, so the box is the exception, not the rule).
 */
frappe.query_reports["Retainage Held"] = {
	filters: [
		{ fieldname: "customer", label: __("Account"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nDraft\nLocked\nClosed" },
		{ fieldname: "include_empty", label: __("Include jobs holding nothing"), fieldtype: "Check", default: 0 },
	],
};
