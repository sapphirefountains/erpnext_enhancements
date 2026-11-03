/**
 * Form breadcrumbs that lead to this app's pages instead of the stock list view.
 *
 * Targets: the Project, Opportunity and Task forms (and their print views). Loaded via
 * erpnext_enhancements.bundle.js (global).
 *
 * Nik, 2026-11-03: "being in the Project document, when I click Project in the top right corner
 * it takes me to the Project DocType List view. I'd like it to go to our custom Projects Dashboard
 * we have instead." The route data behind it (90 days of Route History to 2026-11-03): the
 * Projects Dashboard (the Projects workspace) had 734 visits from 12 of 14 people, the Project
 * list 184 from four, and thirteen of those were a dashboard-to-list-and-straight-back bounce
 * within a minute. The Opportunity Kanban was the busiest route of all (868 visits, six people)
 * and the CRM module already opens on it, so the Opportunity form's crumb goes there too. A Task
 * lives under its Project: its trail reads Projects > the project > the task.
 *
 * HOW frappe 16.53 DRAWS THE TRAIL. `frappe.ui.form.get_breadcrumbs(frm)` (form/form.js, line 36
 * at v16.53.0) returns the items: the list crumb `{label: __(doctype), href: "/desk/<slug>"}`
 * (left out for a Single, and for User without System Manager), then the document's title with no
 * href. `Form.refresh_header()` paints them with `this.page.set_breadcrumbs(...)` on every
 * refresh (line 810), and the print view calls the same function with a plain `{doctype, doc,
 * meta}` object. `frappe.breadcrumbs.*` is the deprecated compatibility layer (views/
 * breadcrumbs.js, "marked for removal in v17") and no longer the path the form takes, so this
 * file wraps the function itself: the original builds the items, and the doctype's target
 * replaces everything before the document. On a frappe without that function (before 16.5x)
 * nothing is wrapped and the Desk draws its own trail.
 *
 * Every href is a `/desk/` path, so the Desk's own link delegate (router.js) routes it in place:
 * a history entry of its own, Back returns to the form. The Projects workspace is reached by the
 * slug of its title: v16's router reads an unknown first segment against `frappe.workspaces`
 * (convert_to_standard_route), so `/desk/projects` is `Workspaces/Projects`.
 *
 * THE TASK'S PROJECT CRUMB. ERPNext 16.52.1's Task carries `project` but no `project_name`, and
 * the trail is built synchronously, so the crumb first shows the project's id and swaps in its
 * title once `frappe.db.get_value` answers; a Project already loaded in `locals` (you came from
 * its form) is used at once. The repaint goes through `frm.page.set_breadcrumbs` only while the
 * same document is still on that form, so a stale answer never retitles another record's trail.
 * Titles are cached per page load; the fetch runs once per project.
 */
(function () {
	if (typeof frappe === "undefined" || !frappe.ui || !frappe.ui.form) return;
	const original = frappe.ui.form.get_breadcrumbs;
	if (typeof original !== "function" || original.ee_breadcrumb_targets) return;

	function translate(text) {
		return typeof __ === "function" ? __(text) : text;
	}

	function form_href(doctype, name) {
		const slug =
			frappe.router && typeof frappe.router.slug === "function"
				? frappe.router.slug(doctype)
				: String(doctype).toLowerCase().replace(/ /g, "-");
		return "/desk/" + slug + "/" + encodeURIComponent(name);
	}

	const projects_crumb = () => ({ label: translate("Projects"), href: "/desk/projects" });
	const kanban_crumb = () => ({
		label: translate("Opportunity"),
		href: "/desk/opportunity/view/kanban/Opportunity",
	});

	// Project titles, by name: a title fetched once is kept for the page load.
	const titles = {};
	const fetching = {};

	function known_title(project) {
		if (titles[project]) return titles[project];
		try {
			const doc = typeof frappe.get_doc === "function" ? frappe.get_doc("Project", project) : null;
			if (doc && doc.project_name) titles[project] = doc.project_name;
		} catch (e) {
			// a Project not in locals: nothing to read yet
		}
		return titles[project] || null;
	}

	function fetch_title(frm, project) {
		if (fetching[project] || !frappe.db || typeof frappe.db.get_value !== "function") return;
		const docname = frm.docname || (frm.doc && frm.doc.name);
		fetching[project] = true;
		let pending;
		try {
			pending = frappe.db.get_value("Project", project, "project_name");
		} catch (e) {
			fetching[project] = false;
			return;
		}
		if (!pending || typeof pending.then !== "function") {
			fetching[project] = false;
			return;
		}
		pending.then(
			(r) => {
				fetching[project] = false;
				const title = r && r.message && r.message.project_name;
				if (!title) return;
				titles[project] = title;
				// Repaint only the form that asked, and only while it still shows that document.
				const same_doc = frm.doc && frm.doc.project === project && (frm.docname || frm.doc.name) === docname;
				if (same_doc && frm.page && typeof frm.page.set_breadcrumbs === "function") {
					frm.page.set_breadcrumbs(frappe.ui.form.get_breadcrumbs(frm));
				}
			},
			() => {
				fetching[project] = false;
			}
		);
	}

	function task_crumbs(frm) {
		const items = [projects_crumb()];
		const project = frm.doc && frm.doc.project;
		if (!project) return items;
		const title = known_title(project);
		if (!title) fetch_title(frm, project);
		items.push({ label: title || project, href: form_href("Project", project) });
		return items;
	}

	// doctype -> the crumbs that stand before the document's own.
	const TARGETS = {
		Project: () => [projects_crumb()],
		Opportunity: () => [kanban_crumb()],
		Task: task_crumbs,
	};

	function get_breadcrumbs(frm) {
		const items = original.apply(this, arguments);
		try {
			const target = frm && TARGETS[frm.doctype];
			if (!target || !Array.isArray(items) || !items.length) return items;
			// The last item is always the document itself (form.js builds it last); everything
			// before it is the list crumb this file replaces.
			return target(frm).concat([items[items.length - 1]]);
		} catch (e) {
			try {
				console.warn("breadcrumb_targets: fell back to the stock trail", e);
			} catch (ignored) {
				// nothing left to tell
			}
			return items;
		}
	}

	get_breadcrumbs.ee_breadcrumb_targets = true;
	get_breadcrumbs.original = original;
	frappe.ui.form.get_breadcrumbs = get_breadcrumbs;

	// For the harness and for anyone reading the Desk console.
	frappe.provide("erpnext_enhancements.breadcrumb_targets");
	erpnext_enhancements.breadcrumb_targets.TARGETS = TARGETS;
	erpnext_enhancements.breadcrumb_targets.titles = titles;
})();
