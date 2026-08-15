// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The author's side of ask-the-author.
//
// The gap this closes is narrower and more annoying than "there was no way to
// answer". Training Author, Training Manager and System Manager all hold `write`
// on this DocType, and the controller is thorough: `_stamp_answer` sets
// `answered_by` and `answered_on`, `_derive_status` moves the thread to Answered,
// `_clamp_visibility` refuses to publish an unanswered or hidden one. So typing
// into the Answer field and pressing Ctrl+S saves a completely correct record.
//
// It just never tells the learner. The notification lives in
// `training.qa.answer_question_thread`, which had no caller at all until v1.304.0 —
// so the obvious way to answer produced a right-looking row and silence, and the
// learner had no reason to reopen the lesson to find out.
//
// Hence two things here, and the second matters more than the first:
//
//   1. an "Answer and notify" action that routes through the endpoint;
//   2. a warning the moment the Answer field is edited directly, because that is
//      the path somebody will take and it is the one that goes quiet.

frappe.ui.form.on("Training Question Thread", {
	refresh(frm) {
		frm.trigger("ee_answer_action");
		frm.trigger("ee_show_asker_context");
	},

	ee_answer_action(frm) {
		if (frm.is_new()) return;
		// Hidden is sticky and means "stop showing me this". Offering to answer it
		// would be offering to un-hide it as a side effect, which is not this
		// button's decision to make — the server refuses it too.
		if (frm.doc.status === "Hidden") return;

		frm.add_custom_button(__("Answer and notify"), () => frm.trigger("ee_open_answer_dialog"))
			.addClass("btn-primary");
	},

	ee_open_answer_dialog(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Answer this question"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "question_html",
					options: `<blockquote class="text-muted">${frappe.utils.escape_html(
						frappe.utils.strip_html(frm.doc.question || "")
					)}</blockquote>`,
				},
				{
					fieldtype: "Text Editor",
					fieldname: "answer",
					label: __("Answer"),
					reqd: 1,
					default: frm.doc.answer || "",
				},
				{
					fieldtype: "Check",
					fieldname: "is_public",
					label: __("Show this answer to everyone taking this lesson"),
					default: frm.doc.is_public ? 1 : 0,
					// Questions arrive phrased personally and routinely carry a site
					// name, a customer name, or a photo of somebody's pump room. The
					// DocType's own docstring makes publishing a per-thread decision
					// on purpose; this checkbox is that decision, not a default.
					description: __(
						"Off by default. The question is shown to nobody either way — only your answer is published."
					),
				},
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				dialog.hide();
				frappe.dom.freeze(__("Sending…"));
				frappe
					.call({
						method: "erpnext_enhancements.training.qa.answer_question_thread",
						args: {
							thread: frm.doc.name,
							answer: values.answer,
							is_public: values.is_public ? 1 : 0,
						},
					})
					.then((r) => {
						frappe.dom.unfreeze();
						const result = (r && r.message) || {};
						frm.reload_doc();
						// `notified` is reported rather than assumed. `_notify` is
						// best-effort by design — a learner with no email address, or
						// a mail queue that is down, must not fail the answer — and an
						// author who is told "sent" when nothing was sent will not
						// follow up.
						if (result.notified) {
							frappe.show_alert({ message: __("Answered. The learner has been notified."), indicator: "green" });
						} else {
							frappe.msgprint({
								title: __("Answered, but not delivered"),
								indicator: "orange",
								message: __(
									"The answer is saved and will show in the lesson. No notification went out — check that the learner has an email address on their User record."
								),
							});
						}
					})
					.catch(() => {
						frappe.dom.unfreeze();
					});
			},
		});
		dialog.show();
	},

	ee_show_asker_context(frm) {
		if (frm.is_new() || !frm.doc.at_seconds) return;
		// The timestamp is the whole point of anchoring a question to a video, and
		// it renders as a bare integer in a form field where nobody reads it.
		const total = Number(frm.doc.at_seconds) || 0;
		const mins = Math.floor(total / 60);
		const secs = String(total % 60).padStart(2, "0");
		frm.dashboard.add_comment(
			__("Asked at {0}:{1} in the video.", [mins, secs]),
			"blue",
			true
		);
	},

	answer(frm) {
		// Fires on any edit of the field, including the one that matters: an author
		// typing here and saving. The record that produces is correct — the
		// controller stamps answered_by/answered_on and derives the status — and the
		// learner is never told, because the notification lives in the endpoint the
		// button above calls and not in the DocType's validate().
		if (frm.is_new() || !frm.doc.answer) return;
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			__(
				"Saving this form records the answer but does not notify the learner. Use <b>Answer and notify</b> to send it."
			),
			"orange"
		);
	},
});
