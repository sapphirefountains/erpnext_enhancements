// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The incident form. Two jobs, and the first one is the whole reason it exists.
//
// 1. Put the UOSH clock in front of somebody the moment they open the record.
//    Utah gives 8 hours for a fatality and 24 for a hospitalisation, amputation or
//    loss of an eye, counted from when the company learns of it. A deadline that
//    lives in a field halfway down a form is a deadline nobody sees; this one is a
//    red banner at the top with the hours remaining in it.
//
// 2. Raise a corrective action as real work. A corrective action that stays a
//    sentence on a form is a corrective action nobody does.

frappe.ui.form.on("Safety Incident", {
	refresh(frm) {
		frm.trigger("ee_clock");
		frm.trigger("ee_recordable");
		frm.trigger("ee_actions");
	},

	ee_clock(frm) {
		if (frm.is_new()) return;
		const reportable = frm.doc.reportable_to_uosh;
		if (!reportable || reportable === __("No") || reportable === "No") return;

		if (frm.doc.uosh_reported_on) {
			frm.dashboard.add_comment(
				__("Reported to UOSH on {0}.", [frappe.datetime.str_to_user(frm.doc.uosh_reported_on)]),
				"green",
				true
			);
			return;
		}

		// Hours remaining, not just the deadline. "By 14:20" needs arithmetic done in
		// somebody's head at the worst moment; "3 hours left" does not.
		let left = "";
		if (frm.doc.uosh_due_by) {
			const hours = Math.round(
				(frappe.datetime.str_to_obj(frm.doc.uosh_due_by) - new Date()) / 36e5
			);
			left =
				hours > 0
					? __(" — about {0} hour(s) left.", [hours])
					: __(" — <b>that deadline has passed.</b>");
		}
		frm.dashboard.add_comment(
			__("<b>{0}.</b> Call UOSH by {1}{2} Do not move the equipment until they release the scene.", [
				reportable,
				frappe.datetime.str_to_user(frm.doc.uosh_due_by),
				left,
			]),
			"red",
			true
		);
	},

	ee_recordable(frm) {
		// Said out loud, because `is_recordable` is a read-only tick somebody will
		// otherwise try to change. The rule is mechanical and the form should say so
		// rather than look like it made a judgement call.
		if (frm.is_new() || !frm.doc.incident_type) return;
		if (["Near Miss", "Property Damage"].includes(frm.doc.incident_type)) {
			frm.dashboard.add_comment(
				__("A {0} is never OSHA-recordable — and it is the most useful row in the log, because it cost nothing.", [frm.doc.incident_type.toLowerCase()]),
				"blue",
				true
			);
			return;
		}
		if (!frm.doc.is_recordable) {
			frm.dashboard.add_comment(
				__("Not recordable as it stands. First aid ONLY is not recordable; medical treatment beyond it is, and that one distinction decides most cases."),
				"blue",
				true
			);
		}
	},

	ee_actions(frm) {
		if (frm.is_new()) return;
		(frm.doc.actions || []).forEach((row) => {
			if (row.raised_reference) return;
			frm.add_custom_button(
				(row.action || __("Action")).slice(0, 40),
				() => frm.events.ee_raise(frm, row),
				__("Raise as work")
			);
		});
	},

	ee_raise(frm, row) {
		const dialog = new frappe.ui.Dialog({
			title: __("Raise this as real work"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "what",
					options: `<p>${frappe.utils.escape_html(row.action || "")}</p>`,
				},
				{
					fieldtype: "Select",
					fieldname: "kind",
					label: __("As"),
					options: ["Task", "Training Assignment"].join("\n"),
					default: "Task",
					reqd: 1,
					description: __(
						"A Training Assignment needs the course named exactly in the action text — guessing which course was meant is how somebody ends up marked as retrained on the wrong thing."
					),
				},
			],
			primary_action_label: __("Raise it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.safety.raise_action",
					args: { incident: frm.doc.name, row: row.name, kind: values.kind },
					freeze: true,
				}).then((r) => {
					if (!r || !r.message) return;
					frappe.show_alert({
						message: __("Raised as {0}.", [r.message.reference]),
						indicator: "green",
					});
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},
});
