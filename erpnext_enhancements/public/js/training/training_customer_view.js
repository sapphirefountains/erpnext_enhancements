// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// "Are the people who operate this client's fountain trained?"
//
// `training.portal.get_customer_training` answers exactly that — completions
// rather than attempts, with expiry computed against today rather than trusted
// from a nightly sweep that may not have run — and it had no caller of any kind
// until v1.334.0. The answer existed and nobody could ask the question.
//
// Read-only and staff-facing. The same endpoint serves a portal user asking about
// their own company; the server resolves the scope, so this passes the customer
// explicitly and never filters client-side.

frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Training"), () => frm.trigger("ee_show_customer_training"), __("View"));
	},

	ee_show_customer_training(frm) {
		frappe.dom.freeze(__("Loading…"));
		frappe
			.call({
				method: "erpnext_enhancements.training.portal.get_customer_training",
				args: { customer: frm.doc.name },
			})
			.then((r) => {
				frappe.dom.unfreeze();
				const data = (r && r.message) || {};

				if (data.enabled === false) {
					frappe.msgprint({
						title: __("Training"),
						message: data.message || __("Training is not available yet."),
						indicator: "orange",
					});
					return;
				}

				const learners = data.learners || [];
				if (!learners.length) {
					frappe.msgprint({
						title: __("Training"),
						// Named precisely, because the two causes need different actions
						// and "no records" would hide which one this is.
						message: __(
							"Nobody at {0} has a training login yet. Grant portal access from a Contact on this account.",
							[frm.doc.customer_name || frm.doc.name]
						),
						indicator: "blue",
					});
					return;
				}

				frappe.msgprint({
					title: __("Training at {0}", [frm.doc.customer_name || frm.doc.name]),
					message: renderLearners(learners),
					wide: true,
				});
			})
			.catch(() => frappe.dom.unfreeze());
	},
});

function renderLearners(learners) {
	const esc = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));

	return learners
		.map((learner) => {
			const courses = learner.courses || [];
			const body = courses.length
				? courses
						.map((c) => {
							// `current` is the server's judgement and the only one that
							// answers the question being asked. Status alone would show a
							// "Valid" completion that expired last month as a pass.
							const indicator = c.current ? "green" : c.status === "Expired" ? "red" : "gray";
							const when = c.completed_on ? frappe.datetime.str_to_user(c.completed_on) : "—";
							const until = c.expires_on
								? __("expires {0}", [frappe.datetime.str_to_user(c.expires_on)])
								: __("no expiry");
							return `<tr>
								<td>${esc(c.course_title)}</td>
								<td><span class="indicator ${indicator}">${esc(c.status)}</span></td>
								<td>${esc(when)}</td>
								<td class="text-muted">${esc(until)}</td>
							</tr>`;
						})
						.join("")
				: `<tr><td colspan="4" class="text-muted">${__("No completed training.")}</td></tr>`;

			return `<h5 style="margin-top:12px">${esc(learner.full_name)}</h5>
				<table class="table table-bordered table-sm">
					<thead><tr>
						<th>${__("Course")}</th><th>${__("Status")}</th>
						<th>${__("Completed")}</th><th>${__("Validity")}</th>
					</tr></thead>
					<tbody>${body}</tbody>
				</table>`;
		})
		.join("");
}
