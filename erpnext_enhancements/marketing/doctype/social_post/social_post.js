// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The Social Post actions (TASK-2026-01486): Submit for Approval, Approve, Send Back, Cancel.
// Each is a POST to publish/approval.py, and publish/workflow.py decides who may do what. The
// buttons shown here are a convenience, never the check. Approve sends the post's `modified` as
// the approver saw it, so a post edited since is refused rather than approved unseen.

const SOCIAL_ACTIONS = "erpnext_enhancements.marketing.publish.approval.";
const SOCIAL_CANCELABLE = ["Draft", "Pending Approval", "Approved", "Scheduled", "Publishing"];

function social_action(frm, action, args) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the post first."));
		return;
	}
	frappe.call({
		method: SOCIAL_ACTIONS + action,
		type: "POST",
		args: Object.assign({ post: frm.doc.name }, args || {}),
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}

function social_with_reason(frm, action, title, label) {
	frappe.prompt(
		[{ fieldname: "reason", fieldtype: "Small Text", label: label }],
		(values) => social_action(frm, action, { reason: values.reason }),
		title
	);
}

frappe.ui.form.on("Social Post", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		const status = frm.doc.status || "Draft";
		const me = frappe.session.user;

		if (status === "Draft") {
			frm.add_custom_button(__("Submit for Approval"), () =>
				social_action(frm, "submit_for_approval")
			);
		}
		if (status === "Pending Approval") {
			const may_approve =
				frappe.user.has_role("Marketing Manager") &&
				me !== frm.doc.owner &&
				me !== frm.doc.modified_by;
			if (may_approve) {
				frm.add_custom_button(__("Approve"), () =>
					frappe.confirm(
						__(
							"Approve this post? It is queued for every account listed, at its publish time, and its content is locked."
						),
						() => social_action(frm, "approve", { modified: frm.doc.modified })
					)
				);
			} else {
				frm.set_intro(
					__(
						"Waiting for a Marketing Manager to approve it. The approver cannot be the person who wrote it or made the latest change."
					),
					"blue"
				);
			}
			frm.add_custom_button(__("Send Back"), () =>
				social_with_reason(frm, "send_back", __("Send back to Draft"), __("What should change?"))
			);
		}
		if (SOCIAL_CANCELABLE.includes(status)) {
			frm.add_custom_button(__("Cancel Post"), () =>
				social_with_reason(
					frm,
					"cancel",
					__("Cancel this post"),
					status === "Draft" || status === "Pending Approval"
						? __("Why? (optional)")
						: __(
								"Why? (optional) Canceling stops what has not gone out yet; it cannot take down anything already published."
						  )
				)
			);
		}
	},
});
