/*
 * Budget Reallocation form: recording the approvals (WI-075 sub-phase M).
 *
 * This file is not optional polish. Every approval field on the document is read-only, and
 * `before_submit` refuses a reallocation with no project-manager approval — so without these
 * buttons there would be no way to record one and **no reallocation could ever be submitted**.
 *
 * It sits in the DocType folder and is registered NOWHERE. Frappe already loads
 * <module>/doctype/<name>/<name>.js for a DocType this app owns; adding a `doctype_js` entry as
 * well appends the file to the same string a second time with no dedupe, and a top-level `const`
 * then becomes a SyntaxError that costs the form every button it has. `doctype_js` is only for
 * the core DocTypes this app extends, like `Non Conformance`.
 *
 * The approvals are recorded by the server from the session user and the server clock. Nothing
 * here proposes a name or a timestamp, because an approval whose time the approver chooses is not
 * evidence of anything — the old `complete_step` client path let the browser send one and the
 * audit found retroactive box-ticking.
 */

frappe.ui.form.on("Budget Reallocation", {
	refresh: function (frm) {
		if (frm.is_new()) return;
		render_approval_state(frm);
		if (frm.doc.docstatus === 0) add_approval_buttons(frm);
	},

	from_category: function (frm) {
		flag_protected(frm);
	},

	to_category: function (frm) {
		flag_protected(frm);
	},
});

function add_approval_buttons(frm) {
	const group = __("Approvals");

	if (!frm.doc.pm_approved_by) {
		frm.add_custom_button(
			__("Approve as Project Manager"),
			() => record(frm, "erpnext_enhancements.api.project_budget.approve_reallocation"),
			group
		);
	} else {
		frm.add_custom_button(
			__("Revoke PM Approval"),
			() => revoke(frm, "pm"),
			group
		);
	}

	// Only offered once the project manager has approved. A second approval recorded first would
	// read as a countersignature of something nobody had signed.
	if (frm.doc.requires_additional_approval && frm.doc.pm_approved_by) {
		if (!frm.doc.additional_approved_by) {
			frm.add_custom_button(
				__("Give Second Approval"),
				() =>
					record(
						frm,
						"erpnext_enhancements.api.project_budget.give_second_approval"
					),
				group
			);
		} else {
			frm.add_custom_button(
				__("Revoke Second Approval"),
				() => revoke(frm, "second"),
				group
			);
		}
	}
}

function record(frm, method) {
	frappe.call({
		method: method,
		args: { reallocation: frm.doc.name },
		freeze: true,
		freeze_message: __("Recording approval..."),
		callback: function () {
			frm.reload_doc();
		},
	});
}

function revoke(frm, which) {
	frappe.confirm(
		__("Remove this approval? The reallocation cannot be submitted without it."),
		function () {
			frappe.call({
				method: "erpnext_enhancements.api.project_budget.revoke_approval",
				args: { reallocation: frm.doc.name, which: which },
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
	if (!frm.doc.pm_approved_by) waiting.push(__("project manager approval"));
	if (frm.doc.requires_additional_approval && !frm.doc.additional_approved_by) {
		waiting.push(__("a second approval"));
	}

	if (!waiting.length) {
		frm.dashboard.set_headline(
			__("Approved. Submitting will move the money."),
			"green"
		);
		return;
	}

	frm.dashboard.set_headline(
		__("Waiting on {0}.", [waiting.join(__(" and "))]),
		"orange"
	);
}

function flag_protected(frm) {
	// Advisory only. The server decides whether a second approval is required and writes
	// `requires_additional_approval` itself; this just tells somebody drafting one what is coming,
	// so they are not surprised at submit time.
	if (!frm.doc.from_category && !frm.doc.to_category) return;

	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Project Budget Category",
			filters: [
				["name", "in", [frm.doc.from_category, frm.doc.to_category].filter(Boolean)],
				["is_protected", "=", 1],
			],
			fields: ["name"],
			limit_page_length: 2,
		},
		callback: function (r) {
			const protectedNames = (r.message || []).map((row) => row.name);
			if (!protectedNames.length) return;
			frm.dashboard.add_comment(
				__("{0} is protected, so this will need a second approval.", [
					protectedNames.join(__(" and ")),
				]),
				"orange",
				true
			);
		},
	});
}
