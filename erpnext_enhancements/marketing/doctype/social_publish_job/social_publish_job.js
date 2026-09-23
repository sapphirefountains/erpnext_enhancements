// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// A person's answer for a job the outbox could not settle on its own (TASK-2026-01481).
// Unconfirmed means the request went out and nobody knows whether the post is live: check the
// network first. Every call is a POST to sweeper.resolve_job (System Manager only).

const SOCIAL_RESOLVE = "erpnext_enhancements.marketing.publish.sweeper.resolve_job";

function social_resolve(frm, outcome, extra) {
	frappe.call({
		method: SOCIAL_RESOLVE,
		type: "POST",
		args: Object.assign({ job: frm.doc.name, outcome }, extra || {}),
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}

frappe.ui.form.on("Social Publish Job", {
	refresh(frm) {
		const state = frm.doc.state;
		if (state === "Unconfirmed") {
			frm.set_intro(
				__(
					"The request left ERPNext but no answer came back, so this post may already be live. Check the account on {0}, then say which it was. Sending it again without checking can publish it twice.",
					[frappe.utils.escape_html(frm.doc.network || __("the network"))]
				),
				"orange"
			);
			frm.add_custom_button(__("It was published"), () =>
				frappe.prompt(
					[
						{ fieldname: "permalink", fieldtype: "Data", options: "URL", label: __("Link to the post") },
						{ fieldname: "external_post_id", fieldtype: "Data", label: __("Platform post ID (if known)") },
					],
					(values) => social_resolve(frm, "published", values),
					__("Record as published")
				)
			);
		}
		if (state === "Unconfirmed" || state === "Failed") {
			frm.add_custom_button(__("Send it again"), () =>
				frappe.confirm(
					state === "Unconfirmed"
						? __("Only if you checked and it is NOT on the account. Send it again?")
						: __("Send it again?"),
					() => social_resolve(frm, "retry")
				)
			);
		}
		if (state === "Unconfirmed" || state === "Failed" || state === "Pending") {
			frm.add_custom_button(__("Cancel"), () =>
				frappe.confirm(__("Stop this job? The post will not go to this account."), () =>
					social_resolve(frm, "cancel")
				)
			);
		}
	},
});
