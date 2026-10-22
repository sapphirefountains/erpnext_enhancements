// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * Show the activity feed on one tab of a form only (TASK-2026-00353; Project: 2026-10-22).
 *
 * Frappe renders the timeline into `.form-footer`, which sits BELOW the tab panes rather than
 * inside one, so it follows you onto whichever tab is open: open Contacts, or Budget, and the same
 * wall of comments is still underneath, pushing the thing you came for off the screen.
 *
 * WHERE THE FOOTER IS (Frappe v16, form.js / page.js). The form's Footer is built with
 * `parent: $("<div>").appendTo(this.page.main.parent())`, and `page.main` is
 * `.layout-main-section`, so the footer is a sibling of `.layout-main-section` inside
 * `.layout-main-section-wrapper`:
 *
 *   .layout-main-section-wrapper
 *     .layout-main-section  >  ... .form-layout > .form-page > .form-tabs, .form-tab-content
 *     div  >  .form-footer
 *
 * It is NOT inside `.form-page`. This script and its stylesheet used to look for it there, which
 * is why the original feature (v1.259.4, "fixed" in v1.299.2) never hid anything on v16: `apply()`
 * returned early on every form that does not move the footer itself. The wrapper is now the
 * unit: the class goes on `.layout-main-section-wrapper`, and the tabs are read from inside it.
 *
 * WHICH FORMS. Only the doctypes named in `HOME_TABS`, each with the tab the feed belongs on:
 * Project -> its Comments tab (Nik, 2026-10-22: "move the Activity Log to only display under
 * Comments tab of the Project view"). Every other form is left exactly as it is, the feed on every
 * tab, as it has always really behaved. The task that first asked for "first tab only on every
 * tabbed form" was never visible to anyone, so turning it on for all of them is a decision to take
 * on its own, not a side effect of a Project change: set `FIRST_TAB_FOR_ALL` to true to do it (the
 * first tab of each form is then its home, found by position, since several doctypes have renamed
 * Details to Overview or Summary).
 *
 * The home tab is found by the id Frappe gives each tab link (form/tab.js:
 * `<doctype scrubbed with "-">-<fieldname>-tab`), so it is scoped to the doctype without knowing
 * which form the page belongs to, and a renamed label does not break it.
 *
 * KNOWING WHEN THE TAB CHANGES. Not `shown.bs.tab`: Frappe's own click handler (tab.js) adds
 * `active` to the link before layout.js calls Bootstrap's `.tab("show")`, which returns early on a
 * link that is already active, so that event does not fire for a Frappe tab click. What does
 * change, every time, is the link's `class` attribute (`set_active()` adds and removes `active`),
 * so each `.form-tabs` list gets a MutationObserver on that attribute, attached once. `form-refresh`
 * and a childList observer on the page cover the first paint and Frappe rebuilding the footer
 * after a save.
 *
 * A CLASS on the wrapper, not `display: none` written from JS. The stylesheet owns the looks.
 *
 * A form whose footer is ALREADY inside a tab pane is left alone. ERPNext's CRM doctypes (Lead,
 * Opportunity, Prospect) move it there themselves (`crm_activities.js`:
 * `$(cur_form_footer).appendTo(this.all_activities_wrapper)`); Bootstrap then scopes it to that one
 * pane, and hiding it on top of that removes the last tab it was reachable from (v1.331.1).
 */
frappe.provide("erpnext_enhancements.activity_tabs");

(() => {
	const HIDDEN_CLASS = "ee-activity-off-tab";

	// Doctypes whose feed lives on a named tab: doctype -> the Tab Break's fieldname.
	const HOME_TABS = { Project: "custom_comments" };
	erpnext_enhancements.activity_tabs.home_tabs = HOME_TABS;

	// Hide the feed off the FIRST tab on every other tabbed form too. Off: that behaviour has
	// never worked on v16, so no one has seen it; enabling it is its own decision.
	const FIRST_TAB_FOR_ALL = false;

	// What holds one form's tabs and its footer together.
	const FORM_ROOT = ".layout-main-section-wrapper";

	// The tab link that is this form's home for the feed, or null if it names none.
	function named_home_link(root) {
		for (const [doctype, fieldname] of Object.entries(HOME_TABS)) {
			const id = `${frappe.scrub(doctype, "-")}-${fieldname}-tab`;
			for (const link of root.querySelectorAll(".form-tabs .nav-link")) {
				if (link.id === id) return link;
			}
		}
		return null;
	}

	// Is the open tab this form's home? True too when the form has no say: no tabs, or not one of ours.
	function on_home_tab(root) {
		const links = root.querySelectorAll(".form-tabs .nav-link");
		if (links.length < 2) {
			// No tabs, or only one. Nothing to be on the wrong side of.
			return true;
		}
		const active = root.querySelector(".form-tabs .nav-link.active");
		if (!active) {
			// Mid-render. Assume home rather than flashing the feed away and back on every paint.
			return true;
		}
		const home = named_home_link(root) || (FIRST_TAB_FOR_ALL ? links[0] : null);
		if (!home) return true;
		return active === home;
	}

	const watched = new WeakSet();

	// Re-evaluate whenever a tab link's class changes: that is what Frappe's tab switch does.
	function watch_tabs(root) {
		for (const tabs of root.querySelectorAll(".form-tabs")) {
			if (watched.has(tabs)) continue;
			watched.add(tabs);
			new MutationObserver(() => debounced()).observe(tabs, {
				attributes: true,
				attributeFilter: ["class"],
				subtree: true,
			});
		}
	}

	function apply() {
		document.querySelectorAll(FORM_ROOT).forEach((root) => {
			// `.form-footer` is where the timeline lives; a page without one (a new doc, a
			// print view) has nothing to toggle.
			const footer = root.querySelector(".form-footer");
			if (!footer) return;
			watch_tabs(root);
			// Already inside a pane (CRM doctypes): Bootstrap already does the job. `remove` and
			// not merely `return`: the relocation happens during form refresh, so an earlier
			// paint may already have set the class.
			if (footer.closest(".tab-pane")) {
				root.classList.remove(HIDDEN_CLASS);
				return;
			}
			root.classList.toggle(HIDDEN_CLASS, !on_home_tab(root));
		});
	}

	erpnext_enhancements.activity_tabs.apply = apply;

	function debounce(fn, wait) {
		let timer;
		return function () {
			clearTimeout(timer);
			timer = setTimeout(() => fn.apply(this, arguments), wait);
		};
	}

	const debounced = debounce(apply, 30);

	// `form-refresh` fires BEFORE refresh_fields, so the tabs may not exist yet; the same
	// `setTimeout(0)` that `field_description_icons.js` documents.
	$(document).on("form-refresh", () => setTimeout(apply, 0));

	// Frappe builds the footer and the tab list as it renders a form and rebuilds the footer after
	// a save, none of which fires an event we can hook. Scoped to those two subtrees so this is
	// not a general-purpose DOM firehose.
	const observer = new MutationObserver((mutations) => {
		for (const mutation of mutations) {
			const target = mutation.target;
			if (!target || !target.closest) continue;
			if (target.closest(".form-footer") || target.closest(".form-tabs")) {
				debounced();
				return;
			}
			for (const node of mutation.addedNodes) {
				if (node.nodeType !== 1 || !node.classList) continue;
				if (node.classList.contains("form-footer") || node.classList.contains("form-tabs")) {
					debounced();
					return;
				}
			}
		}
	});

	$(document).ready(() => {
		apply();
		observer.observe(document.body, { childList: true, subtree: true });
	});
})();
