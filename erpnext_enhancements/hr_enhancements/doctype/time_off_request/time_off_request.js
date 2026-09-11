// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The buttons. Without this file the whole approval flow is unreachable.
//
// `hr_enhancements/timeoff.py` whitelists four endpoints — `submit_request`,
// `decide`, `cancel_request`, `who_is_out` — and every one of them is correct:
// they check ownership, refuse a self-decision first and unconditionally,
// demand a reason on a decline, notify the other side, and flag their own
// transition so the controller's `_guard_status` lets them through.
//
// Nothing called any of them. There was no client script on this doctype and no
// caller anywhere else in the app, so a Time Off Request could be created and
// then never move — not in the Desk, not on a phone, not by HR. The feature
// shipped decorative.
//
// It is exactly the failure this repo has now hit three times: the endpoint is
// the easy half and the *reachable* half is the one that gets forgotten. The
// training sign-off went out the same way (v1.334.0, `record_signoff` with no
// caller), and the visual editor shipped with no entry point from any page. The
// pattern is always a server function that works perfectly and a UI that has no
// idea it exists.
//
// Note the interaction with the status guard: making `status` read-only and
// adding `_guard_status` — both correct, both from the branch review — removed
// the one accidental workaround, which was editing the Select by hand. So the
// review's fix and this file have to ship together or time off gets *more*
// broken, not less.

frappe.ui.form.on("Time Off Request", {
	refresh(frm) {
		frm.trigger("ee_actions");
		frm.trigger("ee_explain_status");
		frm.trigger("ee_who_else");
	},

	ee_who_else(frm) {
		// The question an approver actually has, and the reason `who_is_out` exists.
		// It was whitelisted with no caller either -- the same gap as the decision
		// buttons, one endpoint over -- and this is where the answer is needed:
		// "can I let him have Thursday" is really "who else is already off Thursday".
		//
		// Shown only while there is still a decision to make, and only to whoever
		// is making it. The endpoint is staff-only on the server and returns dates
		// and names, never a reason.
		if (frm.is_new() || frm.doc.status !== "Requested") return;
		if (frm.doc.user === frappe.session.user) return;
		if (!frm.doc.from_date || !frm.doc.to_date) return;

		frm.call({
			method: "erpnext_enhancements.hr_enhancements.timeoff.who_is_out",
			args: { from_date: frm.doc.from_date, to_date: frm.doc.to_date },
		})
			.then((r) => {
				const rows = ((r && r.message) || []).filter((x) => x.name !== frm.doc.name);
				if (!rows.length) return;
				const names = rows
					.map((x) => frappe.utils.escape_html(x.employee_name || x.employee))
					.join(", ");
				frm.dashboard.add_comment(
					__("Already off over these dates: {0}", [names]),
					"orange",
					true
				);
			})
			.catch(() => {
				// A dashboard hint is not worth a red modal, and a customer contact
				// hitting the staff-only guard would get one.
			});
	},

	ee_actions(frm) {
		if (frm.is_new()) return;
		const doc = frm.doc;
		const me = frappe.session.user;
		const mine = doc.user === me;
		// Mirrors `_may_decide` on the server. Duplicated deliberately and kept
		// thin: this decides which button to DRAW, the server decides whether the
		// action is allowed. A client-side check that disagrees is a confusing
		// button, not a hole.
		const decider =
			(doc.approver_user && doc.approver_user === me) ||
			frappe.user.has_role("HR Manager") ||
			frappe.user.has_role("System Manager");

		if (doc.status === "Draft" && (mine || decider)) {
			frm.add_custom_button(__("Send to approver"), () =>
				frm.trigger("ee_submit")
			).addClass("btn-primary");
		}

		// Never on your own request, whatever roles you hold -- the server refuses
		// it first and unconditionally, and a button that only fails is worse than
		// no button.
		if (doc.status === "Requested" && decider && !mine) {
			frm.add_custom_button(__("Approve"), () => frm.trigger("ee_approve")).addClass(
				"btn-primary"
			);
			frm.add_custom_button(__("Decline"), () => frm.trigger("ee_decline"));
		}

		if (["Draft", "Requested", "Approved"].includes(doc.status) && (mine || decider)) {
			frm.add_custom_button(__("Cancel request"), () => frm.trigger("ee_cancel"));
		}
	},

	ee_explain_status(frm) {
		// `status` is read-only by design -- it moves only through the endpoints --
		// so say where it is rather than leaving a greyed-out field to explain
		// itself.
		if (frm.is_new()) return;
		const tone = {
			Approved: "green",
			Declined: "red",
			Requested: "orange",
			Canceled: "gray",
			Draft: "gray",
		};
		frm.page.set_indicator(__(frm.doc.status), tone[frm.doc.status] || "gray");
	},

	ee_submit(frm) {
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.timeoff.submit_request",
			args: { request: frm.doc.name },
			freeze: true,
			freeze_message: __("Sending…"),
		}).then((r) => {
			if (!r || !r.message) return;
			frappe.show_alert({
				message: r.message.notified
					? __("Sent to {0}.", [frm.doc.approver_user])
					: __("Sent. We could not email the approver, so tell them."),
				indicator: r.message.notified ? "green" : "orange",
			});
			frm.reload_doc();
		});
	},

	ee_approve(frm) {
		frappe.confirm(
			__("Approve {0} off from {1} to {2}?", [
				frm.doc.employee_name || frm.doc.employee,
				frappe.datetime.str_to_user(frm.doc.from_date),
				frappe.datetime.str_to_user(frm.doc.to_date),
			]),
			() => frm.trigger("ee_do_approve")
		);
	},

	ee_do_approve(frm) {
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.timeoff.decide",
			args: { request: frm.doc.name, decision: "Approved" },
			freeze: true,
		}).then(() => {
			frappe.show_alert({ message: __("Approved."), indicator: "green" });
			frm.reload_doc();
		});
	},

	ee_decline(frm) {
		// The note is required here as well as on the server. A refusal with no
		// reason leaves somebody nothing to plan around, and finding that out from
		// a red modal after typing nothing is a worse way to learn it.
		const dialog = new frappe.ui.Dialog({
			title: __("Decline this request"),
			fields: [
				{
					fieldtype: "Small Text",
					fieldname: "note",
					label: __("Why"),
					reqd: 1,
					description: __("They will see this. A reason lets them plan around it."),
				},
			],
			primary_action_label: __("Decline"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.timeoff.decide",
					args: { request: frm.doc.name, decision: "Declined", note: values.note },
					freeze: true,
				}).then(() => {
					frappe.show_alert({ message: __("Declined."), indicator: "red" });
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},

	ee_cancel(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Cancel this request"),
			fields: [
				{
					fieldtype: "Small Text",
					fieldname: "note",
					label: __("Note"),
					description: __("Optional. The approver is told either way."),
				},
			],
			primary_action_label: __("Cancel it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.timeoff.cancel_request",
					args: { request: frm.doc.name, note: values.note },
					freeze: true,
				}).then(() => {
					frappe.show_alert({ message: __("Canceled."), indicator: "gray" });
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},
});
