// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The permit, filled in at the hatch on a phone.
//
// Two things this screen does that the server cannot:
//
// 1. It shows the atmosphere verdict AS YOU TYPE. A reading that is out of range
//    should be visible before somebody finishes the form and presses a button, not
//    after — the whole point is to stop the entry, and stopping it earlier is
//    strictly better.
//
// 2. It makes closing out one tap. A close-out that takes effort is one that
//    happens tomorrow, and a permit nobody closed reads exactly like a person
//    still down a hole.

frappe.ui.form.on("Confined Space Entry Permit", {
	refresh(frm) {
		frm.trigger("ee_atmosphere");
		frm.trigger("ee_actions");
	},

	oxygen_pct: (frm) => frm.trigger("ee_atmosphere"),
	lel_pct: (frm) => frm.trigger("ee_atmosphere"),
	h2s_ppm: (frm) => frm.trigger("ee_atmosphere"),
	co_ppm: (frm) => frm.trigger("ee_atmosphere"),

	ee_atmosphere(frm) {
		// Mirrors the server limits. Duplicated deliberately and kept thin: this
		// decides what to SHOW, the server decides whether the permit opens. A
		// client check that disagrees is a confusing screen, never a hole.
		const problems = [];
		const o2 = flt(frm.doc.oxygen_pct);
		if (frm.doc.oxygen_pct !== undefined && frm.doc.oxygen_pct !== null && o2 > 0) {
			if (o2 < 19.5) problems.push(__("oxygen {0}% is below 19.5%", [o2]));
			else if (o2 > 23.5)
				problems.push(__("oxygen {0}% is above 23.5% — enrichment is a fire risk", [o2]));
		}
		if (flt(frm.doc.lel_pct) >= 10) problems.push(__("LEL at or above 10%"));
		if (flt(frm.doc.h2s_ppm) >= 10) problems.push(__("H₂S at or above 10 ppm"));
		if (flt(frm.doc.co_ppm) >= 25) problems.push(__("CO at or above 25 ppm"));

		frm.dashboard.clear_comment();
		if (problems.length) {
			frm.dashboard.add_comment(
				__("<b>Do not enter.</b> {0}. Ventilate and re-test.", [problems.join("; ")]),
				"red",
				true
			);
		} else if (o2 > 0) {
			frm.dashboard.add_comment(__("Atmosphere within limits."), "green", true);
		}
	},

	ee_actions(frm) {
		if (frm.is_new()) return;
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Open the permit"), () => frm.trigger("ee_open")).addClass(
				"btn-primary"
			);
		}
		if (frm.doc.status === "Open") {
			// Primary and unmissable. This is the button that has to be pressed when
			// somebody is standing next to a hole wanting to go home.
			frm.add_custom_button(__("Everybody out — close it"), () => frm.trigger("ee_close")).addClass(
				"btn-primary"
			);
		}
		if (["Draft", "Open"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Abandon the entry"), () => frm.trigger("ee_cancel"));
		}
	},

	ee_open(frm) {
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.permits.open_permit",
			args: { permit: frm.doc.name },
			freeze: true,
		})
			.then((r) => {
				if (!r || !r.message) return;
				frappe.show_alert({
					message: __("Open until {0}.", [
						frappe.datetime.str_to_user(r.message.expires_on),
					]),
					indicator: "green",
				});
				frm.reload_doc();
			})
			.catch(() => {
				// The server refuses on a bad atmosphere and lists unticked controls.
				// Both arrive as modals; offer the override only for the second.
				frm.trigger("ee_offer_override");
			});
	},

	ee_offer_override(frm) {
		// Deliberately NOT offered for the atmosphere. There is no argument to have
		// with a gas reading, and a button that looks like it might override one is
		// a button somebody will press.
		const problems = frm.doc.atmosphere_ok ? [] : ["atmosphere"];
		if (problems.length) return;
		frappe.confirm(
			__(
				"Some of the checks are not ticked. Confirm you are going ahead anyway — this is recorded."
			),
			() => {
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.permits.open_permit",
					args: { permit: frm.doc.name, acknowledge_controls: 1 },
					freeze: true,
				}).then(() => frm.reload_doc());
			}
		);
	},

	ee_close(frm) {
		frappe.confirm(__("Is everybody out and accounted for?"), () => {
			frm.call({
				method: "erpnext_enhancements.hr_enhancements.permits.close_permit",
				args: { permit: frm.doc.name, everyone_out: 1 },
				freeze: true,
			}).then(() => {
				frappe.show_alert({ message: __("Closed."), indicator: "green" });
				frm.reload_doc();
			});
		});
	},

	ee_cancel(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Abandon this entry"),
			fields: [
				{
					fieldtype: "Small Text",
					fieldname: "notes",
					label: __("Why"),
					description: __("Usually the gas. Worth recording — it is the reading that stopped somebody going in."),
				},
			],
			primary_action_label: __("Abandon it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.permits.cancel_permit",
					args: { permit: frm.doc.name, notes: values.notes },
					freeze: true,
				}).then(() => frm.reload_doc());
			},
		});
		dialog.show();
	},
});
