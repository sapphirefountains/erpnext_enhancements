/*
 * Change Order form: recording the approvals (WI-075 sub-phase K).
 *
 * Sub-phase K shipped `api/change_order.py` with four whitelisted endpoints and no way to reach
 * any of them. That is not a missing convenience. Every approval stamp on this document is
 * read-only, and `before_submit` refuses a change order the project manager or the customer has
 * not approved — so a change order could be raised, and then never locked by anybody, while the
 * two gates that make it a commercial instrument recorded nothing at all.
 *
 * It sits in the DocType folder and is registered NOWHERE. Frappe already loads
 * <module>/doctype/<name>/<name>.js for a DocType this app owns; adding a `doctype_js` entry as
 * well appends the file to the same string a second time with no dedupe, and a top-level `const`
 * then becomes a SyntaxError that costs the form every button it has. `doctype_js` is only for
 * the core DocTypes this app extends, like `Non Conformance`. For the same reason everything
 * below is a function declaration: nothing here is declared at the top level that a second copy
 * of the file could not redeclare.
 *
 * The approver and the moment are written by the server, from the logged-in account and its own
 * clock. Nothing here proposes either, because an approval whose time the approver chooses is not
 * evidence of anything — the old `complete_step` client path let the browser send one, and the
 * audit that followed found retroactive box-ticking.
 *
 * Recording the customer's approval always asks **how**, because the endpoint requires it and a
 * placeholder would defeat the point of requiring it. Approval arrives by signature, by email, or
 * in a meeting; "they said yes" is the sentence missing from every change-order dispute.
 */

frappe.ui.form.on("Change Order", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		render_approval_state(frm);
		if (frm.doc.docstatus === 0) add_approval_buttons(frm);
		if (frm.doc.project) add_project_context_button(frm);
	},
});

function add_approval_buttons(frm) {
	const group = __("Approvals");

	// The two approvals are offered in either order, unlike the second approval on a Budget
	// Reallocation. That one countersigns the first, so offering it early would be a signature on
	// something nobody had signed. These are two different acts by two different parties — the
	// project manager approves that the work is right and the figure is real, the customer
	// approves that they will pay for it — and a customer often agrees before the figure has
	// finished being checked. Gating them would only stop the true one being recorded.
	if (!frm.doc.pm_approved) {
		frm.add_custom_button(
			__("Approve as Project Manager"),
			() => approve_as_project_manager(frm),
			group
		);
	} else {
		frm.add_custom_button(__("Revoke PM Approval"), () => revoke(frm, "pm"), group);
	}

	if (!frm.doc.customer_approved) {
		frm.add_custom_button(
			__("Record Customer Approval"),
			() => record_customer_approval(frm),
			group
		);
	} else {
		frm.add_custom_button(
			__("Revoke Customer Approval"),
			() => revoke(frm, "customer"),
			group
		);
	}
}

function approve_as_project_manager(frm) {
	frappe.call({
		method: "erpnext_enhancements.api.change_order.approve_change_order",
		args: { change_order: frm.doc.name },
		freeze: true,
		freeze_message: __("Recording approval..."),
		callback: function () {
			frm.reload_doc();
		},
	});
}

function record_customer_approval(frm) {
	frappe.prompt(
		[
			{
				fieldname: "how",
				fieldtype: "Small Text",
				label: __("How did the customer approve?"),
				reqd: 1,
				description: __(
					"A signature, an email, or the meeting it was agreed in. This is the record of the approval, so name something that can still be pointed at a year from now."
				),
			},
		],
		function (values) {
			frappe.call({
				method: "erpnext_enhancements.api.change_order.record_customer_approval",
				args: { change_order: frm.doc.name, how: values.how },
				freeze: true,
				freeze_message: __("Recording approval..."),
				callback: function () {
					frm.reload_doc();
				},
			});
		},
		__("Record Customer Approval"),
		__("Record")
	);
}

function revoke(frm, which) {
	frappe.confirm(
		__("Remove this approval? The change order cannot be submitted without it."),
		function () {
			frappe.call({
				method: "erpnext_enhancements.api.change_order.revoke_approval",
				args: { change_order: frm.doc.name, which: which },
				freeze: true,
				callback: function () {
					frm.reload_doc();
				},
			});
		}
	);
}

function render_approval_state(frm) {
	if (frm.doc.docstatus !== 0) {
		frm.dashboard.clear_headline();
		return;
	}

	const waiting = [];
	if (!frm.doc.pm_approved) waiting.push(__("the project manager"));
	if (!frm.doc.customer_approved) waiting.push(__("the customer"));

	if (!waiting.length) {
		frm.dashboard.set_headline(
			__("Approved by both. Submitting locks it, and the added criteria become contracted."),
			"green"
		);
		return;
	}

	frm.dashboard.set_headline(
		__("Waiting on approval from {0}.", [waiting.join(__(" and "))]),
		"orange"
	);
}

function add_project_context_button(frm) {
	// The approver's missing context, and the fourth endpoint sub-phase K left with no door.
	// Whether to approve another twelve thousand reads differently on a job that has already
	// absorbed fifty. Additions and credits stay separate here for the same reason the endpoint
	// reports them separately: a net figure hides a job that added fifty thousand and credited
	// forty-eight, which is a very different job from one that barely changed.
	frm.add_custom_button(__("Change Orders on This Project"), function () {
		frappe.call({
			method: "erpnext_enhancements.api.change_order.get_project_change_orders",
			args: { project: frm.doc.project },
			freeze: true,
			callback: function (r) {
				if (r.message) show_project_totals(frm, r.message);
			},
		});
	});
}

function show_project_totals(frm, payload) {
	const totals = payload.totals || {};
	const lines = [
		__("Locked change orders: {0}", [totals.count || 0]),
		__("Added: {0}", [format_currency(totals.addition || 0)]),
		__("Credited: {0}", [format_currency(totals.credit || 0)]),
		__("Net: {0}", [format_currency(totals.net || 0)]),
		__("Schedule impact: {0} days", [totals.schedule_days || 0]),
	];

	// Drafts are counted out loud but never added in. A proposal inside the contract value is how
	// a number nobody has agreed to ends up on a dashboard.
	if (payload.draft_count) {
		lines.push(__("{0} still in draft, and not counted above.", [payload.draft_count]));
	}

	frappe.msgprint({
		title: __("Change orders on {0}", [frm.doc.project]),
		message: "<ul><li>" + lines.join("</li><li>") + "</li></ul>",
		indicator: "blue",
	});
}
