// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The buttons, written at the same time as the endpoints rather than after them.
//
// This app has now shipped three complete, correct server sides that nothing
// reached: `record_signoff` had no caller from v1.334.0, the visual editor had no
// entry point from any page, and the entire time-off approval flow — four
// endpoints, all correct — had no client script at all, so a request could be
// created and then never move. The endpoint is the easy half.
//
// Four transitions, and which buttons are drawn depends on who is looking:
//
//   Draft            → the person sends it to a reviewer
//   With the reviewer → the reviewer promotes, or says not yet
//   Decided/Promote   → somebody presses Apply, which is what actually moves them
//
// The client check decides which button to DRAW; the server decides whether the
// action is allowed. A client-side check that disagrees is a confusing button,
// never a hole.

frappe.ui.form.on("Tier Review", {
	refresh(frm) {
		frm.trigger("ee_indicator");
		frm.trigger("ee_actions");
		frm.trigger("ee_snapshot_note");
	},

	ee_indicator(frm) {
		if (frm.is_new()) return;
		const tone = {
			Draft: "gray",
			"With the reviewer": "orange",
			Decided: frm.doc.decision === "Promote" ? "green" : "red",
			Canceled: "gray",
		};
		let label = frm.doc.status;
		if (frm.doc.status === "Decided" && frm.doc.decision) {
			label = frm.doc.applied_on ? __("Promoted") : frm.doc.decision;
		}
		frm.page.set_indicator(__(label), tone[frm.doc.status] || "gray");
	},

	ee_snapshot_note(frm) {
		// Said on the form, because a frozen table looks like a broken one. Somebody
		// who edits the rung's requirements and comes back here to find the old list
		// should be told it is deliberate rather than left to file a bug.
		if (frm.is_new() || !(frm.doc.lines || []).length) return;
		frm.dashboard.add_comment(
			__(
				"These requirements are a snapshot from when the review opened. Changing what the rung asks for does not change what this review was measured against."
			),
			"blue",
			true
		);
	},

	ee_actions(frm) {
		if (frm.is_new()) return;
		const doc = frm.doc;
		const me = frappe.session.user;
		const mine = doc.user === me;
		const decider =
			(doc.reviewer_user && doc.reviewer_user === me) ||
			frappe.user.has_role("HR Manager") ||
			frappe.user.has_role("System Manager");

		if (doc.status === "Draft" && mine) {
			frm.add_custom_button(__("Send to reviewer"), () => frm.trigger("ee_send")).addClass(
				"btn-primary"
			);
		}

		// Never on your own review, whatever roles you hold — the server refuses it
		// first and unconditionally, and a button that only fails is worse than none.
		if (doc.status === "With the reviewer" && decider && !mine) {
			frm.add_custom_button(__("Promote"), () => frm.trigger("ee_promote")).addClass(
				"btn-primary"
			);
			frm.add_custom_button(__("Not yet"), () => frm.trigger("ee_not_yet"));
		}

		if (doc.status === "Decided" && doc.decision === "Promote" && !doc.applied_on && decider && !mine) {
			frm.add_custom_button(__("Apply promotion"), () => frm.trigger("ee_apply")).addClass(
				"btn-primary"
			);
		}

		if (!doc.applied_on && doc.status !== "Canceled" && (mine || decider)) {
			frm.add_custom_button(__("Cancel review"), () => frm.trigger("ee_cancel"));
		}
	},

	ee_send(frm) {
		// The picker is server-built, and it tells the truth when the answer is
		// nobody. On prod a Junior Technician has exactly one eligible reviewer, and
		// seeing that one name — or none — is the most useful thing this screen does.
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.tier_review.reviewers_for",
			args: { employee: frm.doc.employee },
		}).then((r) => {
			const people = (r && r.message) || [];
			if (!people.length) {
				frappe.msgprint({
					title: __("Nobody can review this"),
					indicator: "orange",
					message: __(
						"Nobody currently holds a position that outranks {0} on that ladder, so there is no one who can review it. That is worth knowing on its own — it means the same person is also the only one who can sign anybody off at this tier.",
						[frm.doc.from_position || __("this position")]
					),
				});
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Who should review this?"),
				fields: [
					{
						fieldtype: "Select",
						fieldname: "reviewer",
						label: __("Reviewer"),
						reqd: 1,
						options: people.map((p) => ({
							value: p.name,
							label: `${p.employee_name} — ${p.custom_position || ""}`,
						})),
					},
					{
						fieldtype: "HTML",
						fieldname: "why",
						options: `<p class="text-muted small">${__(
							"Only people whose position outranks yours on the same ladder appear here — the same rule that decides who may sign off your work."
						)}</p>`,
					},
				],
				primary_action_label: __("Send"),
				primary_action: (values) => {
					dialog.hide();
					frm.call({
						method: "erpnext_enhancements.hr_enhancements.tier_review.send_to_reviewer",
						args: { review: frm.doc.name, reviewer: values.reviewer },
						freeze: true,
					}).then(() => {
						frappe.show_alert({ message: __("Sent."), indicator: "green" });
						frm.reload_doc();
					});
				},
			});
			dialog.show();
		});
	},

	ee_promote(frm) {
		frappe.confirm(
			__(
				"Decide that {0} stands on {1}? This records the decision; it does not move them yet.",
				[frm.doc.employee_name || frm.doc.employee, frm.doc.to_position]
			),
			() => frm.trigger("ee_do_promote")
		);
	},

	ee_do_promote(frm) {
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.tier_review.decide",
			args: { review: frm.doc.name, decision: "Promote" },
			freeze: true,
		}).then(() => {
			frappe.show_alert({ message: __("Recorded."), indicator: "green" });
			frm.reload_doc();
		});
	},

	ee_not_yet(frm) {
		// The reason is required here as well as on the server. A refusal with no
		// reason leaves somebody nothing to work on, and learning that from a red
		// modal after typing nothing is a worse way to find out.
		const dialog = new frappe.ui.Dialog({
			title: __("Not yet"),
			fields: [
				{
					fieldtype: "Small Text",
					fieldname: "note",
					label: __("What is still missing"),
					reqd: 1,
					description: __("They will see this. It is the thing they can come back with."),
				},
			],
			primary_action_label: __("Record it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.tier_review.decide",
					args: { review: frm.doc.name, decision: "Not yet", note: values.note },
					freeze: true,
				}).then(() => {
					frappe.show_alert({ message: __("Recorded."), indicator: "orange" });
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},

	ee_apply(frm) {
		frappe.confirm(
			__(
				"Move {0} to {1} now? This also gives them authority to sign off people below that rung.",
				[frm.doc.employee_name || frm.doc.employee, frm.doc.to_position]
			),
			() => {
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.tier_review.apply_promotion",
					args: { review: frm.doc.name },
					freeze: true,
				}).then(() => {
					frappe.show_alert({ message: __("Done."), indicator: "green" });
					frm.reload_doc();
				});
			}
		);
	},

	ee_cancel(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Cancel this review"),
			fields: [{ fieldtype: "Small Text", fieldname: "note", label: __("Note") }],
			primary_action_label: __("Cancel it"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.tier_review.cancel_review",
					args: { review: frm.doc.name, note: values.note },
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
