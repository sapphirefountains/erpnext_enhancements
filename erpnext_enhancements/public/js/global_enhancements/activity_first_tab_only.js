// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * Show the activity feed on the first tab only (TASK-2026-00353).
 *
 * Frappe renders the timeline into `.form-footer`, which is a SIBLING of the tab
 * panes rather than a child of one. So it sits below whichever tab is open — read
 * the Details tab and the conversation is where it belongs; open Contacts, or
 * Budget, or Dispatch, and the same wall of comments is still underneath, pushing
 * the thing you actually came for off the screen.
 *
 * One mechanism, not N copies. The task asked for a shared form script rather than
 * a per-doctype hook, and this is the same shape `activity_log_numbering.js`
 * already uses for global timeline work: a `form-refresh` handler for the initial
 * paint plus a MutationObserver, because Frappe re-renders `.form-footer` on save
 * and on tab construction without firing an event we can hook.
 *
 * A CLASS on the form wrapper, not `display: none` written from JS. The stylesheet
 * owns the looks; toggling a class means the rule lives with the rest of the desk
 * CSS, survives Frappe re-rendering the footer, and can be inspected in devtools
 * without wondering which script set an inline style.
 *
 * Deliberately does nothing to a form with no tabs — the timeline is simply the
 * bottom of that form and there is no "other tab" for it to be wrong on. A form
 * whose first tab is open is left alone for the same reason: the point is to stop
 * the feed following you around, not to hide it.
 *
 * And nothing to a form where the footer is ALREADY inside a tab pane. ERPNext's
 * CRM doctypes — Lead, Opportunity, Prospect — move it there themselves:
 * `erpnext/public/js/utils/crm_activities.js` does
 * `$(cur_form_footer).appendTo(this.all_activities_wrapper)`, relocating the whole
 * footer into the "Activities" tab. The premise this script is built on ("a sibling
 * of the tab panes, so it follows you onto every tab") is then false — Bootstrap
 * already scopes it to that one pane. Hiding it on every tab but the first only
 * removes the last tab it was reachable from, and on Opportunity that left the
 * activity feed with nowhere to show at all: invisible on Details because its host
 * pane was closed, invisible on Activities because this script closed it (v1.331.1).
 */
frappe.provide("erpnext_enhancements.activity_tabs");

(() => {
	const HIDDEN_CLASS = "ee-activity-off-tab";

	// The active tab is the first one. Frappe builds the tab list in document
	// order, so "first" is `:first-child` on the list rather than a name — a title
	// check would break the moment somebody renames Details, and several of our
	// doctypes have already renamed it (Overview, Summary).
	function on_first_tab(wrapper) {
		const links = wrapper.querySelectorAll(".form-tabs .nav-link");
		if (links.length < 2) {
			// No tabs, or only one. Nothing to be on the wrong side of.
			return true;
		}
		const active = wrapper.querySelector(".form-tabs .nav-link.active");
		if (!active) {
			// Mid-render. Assume the first tab rather than flashing the feed away
			// and back on every paint.
			return true;
		}
		return active === links[0];
	}

	function apply() {
		document.querySelectorAll(".form-page").forEach((wrapper) => {
			// `.form-footer` is where the timeline lives; a page without one (a
			// new doc, a print view) has nothing to toggle.
			const footer = wrapper.querySelector(".form-footer");
			if (!footer) return;
			// Already inside a pane — ERPNext's CRM doctypes move it into their
			// "Activities" tab. Bootstrap shows it on that tab and hides it on the
			// rest, which is the whole of what this script exists to do; adding our
			// rule on top only takes away the one tab it was still reachable from.
			// `remove` and not merely `return`: the relocation happens during form
			// refresh, so an earlier paint may already have set the class.
			if (footer.closest(".tab-pane")) {
				wrapper.classList.remove(HIDDEN_CLASS);
				return;
			}
			wrapper.classList.toggle(HIDDEN_CLASS, !on_first_tab(wrapper));
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

	const debounced = debounce(apply, 50);

	// Bootstrap fires this on the tab that becomes visible. It is the fast path —
	// the observer below would catch it too, a frame later, which is long enough
	// to see the feed flash.
	$(document).on("shown.bs.tab", ".form-tabs .nav-link", debounced);

	// `form-refresh` fires BEFORE refresh_fields, so the tabs may not exist yet;
	// the same `setTimeout(0)` that `field_description_icons.js` documents.
	$(document).on("form-refresh", () => setTimeout(apply, 0));

	// Frappe rebuilds `.form-footer` after a save and when it constructs the tab
	// list, neither of which fires an event we can hook. Scoped to those two
	// subtrees so this is not a general-purpose DOM firehose.
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
