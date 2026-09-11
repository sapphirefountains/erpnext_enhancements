// The company ladder as a tree — /app/position/view/tree.
//
// This IS the "company tree for positions" ask. It is deliberately the tree view
// rather than a bespoke chart page: Frappe's own tree gives drag-free reparenting,
// breadcrumbs, add-child-in-place and permission handling for free, and a chart
// that only renders is a chart nobody maintains.
//
// The people tree is a different route and already exists in ERPNext core —
// /app/employee/view/tree, a nested set on `reports_to`. The two answer different
// questions and are linked from the HR workspace side by side: `reports_to` is who
// runs your week, Position is who is qualified to say you can do the job.

frappe.treeview_settings["Position"] = {
	// Our own, not frappe.desk.treeview.get_children: core selects exactly three
	// columns (value, title, expandable), so `node.data.tier` is undefined and the
	// onrender below silently draws nothing. The tier is the whole reason this tree
	// exists, and it was invisible on it until the branch review caught it.
	get_tree_nodes:
		"erpnext_enhancements.hr_enhancements.doctype.position.position.get_position_children",
	filters: [
		{
			fieldname: "company",
			fieldtype: "Link",
			options: "Company",
			label: __("Company"),
			// Positions are company-agnostic here; the filter exists so the toolbar
			// matches the Department tree people already know. Left unset it shows
			// everything, which on a sixteen-person site is the right default.
			get_query: function () {
				return { filters: [["Company", "name", "like", "%"]] };
			},
		},
	],
	breadcrumb: "HR Enhancements",
	root_label: __("All Positions"),
	get_tree_root: true,
	ignore_fields: ["parent_position"],
	menu_items: [
		{
			label: __("New Position"),
			action: function () {
				frappe.new_doc("Position", true);
			},
			condition: 'frappe.boot.user.can_create.indexOf("Position") !== -1',
		},
	],
	fields: [
		{
			fieldtype: "Data",
			fieldname: "position_name",
			label: __("Position"),
			reqd: true,
			bold: true,
		},
		{
			fieldtype: "Check",
			fieldname: "is_group",
			label: __("Is a job family"),
			description: __(
				"A family groups the rungs of one ladder — Technician, Designer. Rungs go underneath it."
			),
		},
		{
			fieldtype: "Int",
			fieldname: "tier",
			label: __("Tier"),
			// Not `reqd`, because a family legitimately has none. The controller
			// refuses a rung without one, which is where the real rule lives — a
			// client-side reqd here would only refuse it in one of the two places a
			// Position can be created from.
			description: __("Higher outranks lower, on this ladder only. Junior 1, Senior 2, Master 3."),
			depends_on: "eval:!doc.is_group",
		},
		{
			fieldtype: "Data",
			fieldname: "tier_label",
			label: __("Tier label"),
			description: __("What the rung is called out loud. Display only."),
			depends_on: "eval:!doc.is_group",
		},
	],
	onrender: function (node) {
		if (node.is_root || !node.data || !node.$tree_link) return;
		const tier = node.data.tier;
		const label = node.data.tier_label;
		// A job family legitimately has no tier, so it simply gets no chip rather
		// than "tier 0", which would read as a rung below Junior.
		if (!tier && !label) return;
		$('<span class="text-muted small"></span>')
			.text(label ? `${label} · ${__("tier")} ${tier}` : `${__("tier")} ${tier}`)
			.css("margin-left", "8px")
			.appendTo(node.$tree_link);
	},
};
