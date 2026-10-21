/**
 * Project form: View > Open in Project Planner (custom pages S5b, v1.667.0).
 *
 * Opens the Project Planner with this project as its Project filter. The planner reads
 * `project` from frappe.route_options and consumes it (project_planner.js, take_route_project).
 * It is a one-off: the planner neither remembers it nor writes it over the person's own filters.
 *
 * frappe.set_route with an options object, never an href or a hand-built URL: on v16.50 it
 * fills frappe.route_options AND writes ?project= to the address, and it is a history entry of
 * its own, so Back returns to this form. A project that has not started yet opens the Lookahead
 * (the planner's home tab since P5 of the planner UX redesign) on the week it starts, where its work
 * is, rather than on this week; one under way opens the planner's last-used tab, the Lookahead when
 * there is none.
 *
 * Only on a project the planner plans: a customer job (its type one of the planner's types,
 * crew_availability.PLANNER_PROJECT_TYPES, which tests/test_form_page_links.py holds this list
 * to) that is not Completed or Canceled. And only for those whose roles open the page:
 * frappe.boot.page_info lists the Pages a person's roles reach.
 *
 * Loaded through doctype_js["Project"], which concatenates every Project script into one: so
 * nothing here is declared at the top level.
 */
frappe.ui.form.on("Project", {
	refresh(frm) {
		const pages = (frappe.boot && frappe.boot.page_info) || {};
		const planned = ["Design", "Build", "Service", "Events", "Rent", "Delivery"];
		if (frm.is_new() || !pages["project-planner"]) return;
		// "Canceled" is this site's spelling (a Property Setter); "Cancelled" is ERPNext's own.
		const closed = ["Completed", "Canceled", "Cancelled"];
		if (!planned.includes(frm.doc.project_type) || closed.includes(frm.doc.status)) return;
		frm.add_custom_button(
			__("Open in Project Planner"),
			() => {
				const options = { project: frm.doc.name };
				const starts = String(frm.doc.expected_start_date || "").slice(0, 10);
				if (starts && starts > frappe.datetime.get_today()) {
					frappe.set_route("project-planner", "lookahead", starts, options);
				} else {
					frappe.set_route("project-planner", options);
				}
			},
			__("View")
		);
	},
});
