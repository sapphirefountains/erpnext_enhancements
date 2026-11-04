/**
 * Project form: the "Open in Planner" button (custom pages S5b, v1.667.0; its own button since
 * v1.779.0).
 *
 * Nik, 2026-11-03, from the route audit: the planner had 190 visits from six people in 90 days
 * and was reachable from a form only through the View menu. The button now stands on its own in
 * the page head, beside the trail, rather than under View, so it is one click and visible.
 *
 * Opens the Project Planner's Gantt for this project (P6 of the planner UX redesign:
 * /project-planner/gantt/<project>), the project from its first task to its last, with this
 * project as the planner's Project filter as well, so the Lookahead filtered to it is one tab away.
 * The planner reads `project` from frappe.route_options and consumes it (project_planner.js,
 * take_route_filters). It is a one-off: the planner neither remembers it as a filter nor writes it
 * over the person's own filters.
 *
 * frappe.set_route with an options object, never an href or a hand-built URL: on v16.50 it
 * fills frappe.route_options AND writes ?project= to the address, and it is a history entry of
 * its own, so Back returns to this form.
 *
 * Only on a project the planner plans: a customer job (its type one of the planner's types,
 * crew_availability.PLANNER_PROJECT_TYPES, which tests/test_form_page_links.py holds this list
 * to) that is not Completed or Canceled. And only for those whose roles open the page:
 * frappe.boot.page_info lists the Pages a person's roles reach.
 *
 * v1.795.0 (Nik, 2026-11-04): a Completed job gets the button too, for the managers who may move done
 * work onto old dates (api/planner_history.py BACKDATE_ROLES): its Gantt is where they correct when the
 * work was done. A Canceled job never does.
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
		const history = frm.doc.status === "Completed" && frappe.user.has_role(["System Manager", "Projects Manager", "Maintenance Supervisor"]);
		if (!planned.includes(frm.doc.project_type) || (closed.includes(frm.doc.status) && !history)) return;
		// No group: a grouped button is a menu entry, a bare one is a button in the page head.
		frm.add_custom_button(__("Open in Planner"), () =>
			frappe.set_route("project-planner", "gantt", frm.doc.name, { project: frm.doc.name })
		);
	},
});
