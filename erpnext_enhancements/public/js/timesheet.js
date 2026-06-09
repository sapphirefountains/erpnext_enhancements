/**
 * Timesheet form script.
 *
 * Targets: the "Timesheet" doctype form.
 * Loaded via: hooks.py `doctype_js["Timesheet"]` (with vue.global.js +
 *   comments.js).
 *
 * Mounts the custom Comments App into `custom_comments_field` on saved Timesheets
 * (see comments.js). Timesheet is excluded from comments_auto.js to avoid a
 * double mount.
 */
frappe.ui.form.on("Timesheet", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_comments_section");
		}
	},

	render_comments_section: function (frm) {
		if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
			erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
		} else {
			console.error("erpnext_enhancements.render_comments_app is not defined.");
		}
	}
});
