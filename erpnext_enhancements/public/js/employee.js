/**
 * Employee form script.
 *
 * Targets: the "Employee" doctype form.
 * Loaded via: hooks.py `doctype_js["Employee"]` (with vue.global.js +
 *   comments.js).
 *
 * Mounts the custom Comments App into `custom_comments_field` on saved Employees
 * (see comments.js). Employee is excluded from comments_auto.js to avoid a
 * double mount.
 *
 * Also carries the entry point to a tier review (WI-073). It lives here rather
 * than on the Tier Review list because "is this person ready for the next rung"
 * is a question somebody asks while looking at the person — and because a feature
 * reachable only from its own list view is one nobody finds. This app has shipped
 * three correct server sides with no way in; the Employee form is the way in for
 * this one.
 */
frappe.ui.form.on("Employee", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_comments_section");
			frm.trigger("ee_tier_review_action");
		}
	},

	ee_tier_review_action: function (frm) {
		// Only for somebody actually on the ladder. An employee with no Position has
		// no rung to be reviewed against, and the endpoint says so -- but a button
		// that only ever explains why it cannot work is not worth drawing.
		if (!frm.doc.custom_position) return;
		if (frm.doc.status !== "Active") return;
		if (!frappe.user.has_role("HR Manager") && !frappe.user.has_role("System Manager")) return;

		frm.add_custom_button(
			__("Tier review"),
			function () {
				frappe.call({
					method: "erpnext_enhancements.hr_enhancements.tier_review.open_review",
					args: { employee: frm.doc.name },
					freeze: true,
					freeze_message: __("Building the checklist…"),
					callback: function (r) {
						if (!r || !r.message) return;
						frappe.set_route("Form", "Tier Review", r.message.name);
					},
				});
			},
			__("HR")
		);
	},

	render_comments_section: function (frm) {
		if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
			erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
		} else {
			console.error("erpnext_enhancements.render_comments_app is not defined.");
		}
	},
});
