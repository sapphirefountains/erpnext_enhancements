/**
 * Which Desk page is the person on, ready to hand to the framed Triton chat page.
 *
 * Only the Desk knows the open record; the frame is another origin and cannot look. This module
 * produces the same reference the classic widget's "Add this page" pins (a record, a list or a
 * report with its filters, and an "unsaved edits" flag), from the same function
 * (`capture/page_context.js` `detectPageContext`), so Triton's server reads one format whichever
 * widget sent it.
 *
 * Two differences from the classic widget, both because this one reports on its own instead of
 * waiting for a button:
 *
 * - `cur_list` and `frappe.query_report` outlive their page. Opening Customer after Sales Order
 *   leaves `cur_list` pointing at the Sales Order list for a moment, and the classic function
 *   reads its filters without asking whose they are. That is harmless when a person presses a
 *   button on a page that has finished drawing. Here it would attach one list's filters to
 *   another list, so filters are kept only when the list or report they came from is the one the
 *   route names.
 * - A route change fires before the new page has drawn, so the reference is read a moment later
 *   (`SETTLE_MS`), and again whenever the panel opens. The same reference is never reported twice
 *   in a row.
 *
 * Nothing here runs at import time, and every Desk global is read behind a guard.
 */

export const SETTLE_MS = 400;

/** Drop filters that belong to some other list or report than the one the route names. */
export function ownFilters(ref, win) {
	if (!ref || !ref.filters) return ref;
	let own = false;
	try {
		if (ref.type === "list") {
			own = !!win.cur_list && win.cur_list.doctype === ref.doctype;
		} else if (ref.type === "report") {
			const report = win.frappe && win.frappe.query_report;
			const viewOfList = !!win.cur_list && win.cur_list.doctype === ref.report_name;
			own = (!!report && report.report_name === ref.report_name) || viewOfList;
		}
	} catch (e) {
		own = false;
	}
	return own ? ref : Object.assign({}, ref, { filters: null });
}

/**
 * @param {object}   opts
 * @param {Window}   opts.win
 * @param {Function} opts.detect    detectPageContext, injected so this module loads under node
 * @param {Function} opts.onChange  called with the reference (or null) when it changes
 */
export function createPageSource({ win, detect, onChange }) {
	let last;
	let timer = null;
	let handler = null;

	function current() {
		try {
			return ownFilters(detect() || null, win);
		} catch (e) {
			return null; // a page mid-teardown: "nothing to report" is the honest answer
		}
	}

	/** Report now. With `force`, even when nothing changed (the frame has just loaded). */
	function report(force) {
		const ref = current();
		const key = JSON.stringify(ref);
		if (!force && key === last) return;
		last = key;
		onChange(ref);
	}

	function onRoute() {
		if (timer) win.clearTimeout(timer);
		timer = win.setTimeout(() => {
			timer = null;
			report(false);
		}, SETTLE_MS);
	}

	return {
		current: current,
		report: report,
		start() {
			const router = win.frappe && win.frappe.router;
			if (handler || !router || typeof router.on !== "function") return;
			handler = onRoute;
			router.on("change", handler);
		},
		stop() {
			if (timer) win.clearTimeout(timer);
			timer = null;
			const router = win.frappe && win.frappe.router;
			if (handler && router && typeof router.off === "function") router.off("change", handler);
			handler = null;
		},
	};
}
