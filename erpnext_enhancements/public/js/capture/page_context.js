/**
 * What Desk page is this? One implementation, shared by the Triton widget and the capture
 * recorder (WI-079 slice 2).
 *
 * `detectPageContext()` is the Triton widget's own function, moved here unchanged. Its return
 * value is a wire format: Triton's server reads it as a context ref, so its keys and values are
 * pinned. In particular it must never grow a key named `dirty_fields`, a name Triton's server
 * reads for itself. The capture-only facts live in separate functions (`formFacts`, `listFacts`,
 * `reportFacts`) with their own shape, so the two consumers can change independently.
 *
 * Desk-only. Every read is behind a guard, and nothing here runs at import time, so the module
 * is safe to load on a web page or under node.
 *
 * `cur_frm`, `cur_list` and `frappe.query_report` are globals that outlive their page. Opening a
 * list after a form leaves `cur_frm` pointing at the form. So the capture facts only trust one
 * after checking that it belongs to the route that is showing now.
 */

import { scrubText } from "./scrub.js";

function frappeRef() {
	return typeof window !== "undefined" ? window.frappe : undefined;
}

/** The raw list filters, exactly as the Triton ref has always carried them. */
function readListFilters() {
	try {
		if (window.cur_list && cur_list.get_filters_for_args) return cur_list.get_filters_for_args();
	} catch (e) {
		// cur_list may not expose filters yet; they stay null.
	}
	return null;
}

/** The raw report filter values, exactly as the Triton ref has always carried them. */
function readReportFilters() {
	try {
		const frappe = frappeRef();
		if (frappe.query_report && frappe.query_report.get_filter_values) {
			return frappe.query_report.get_filter_values();
		}
	} catch (e) {
		// query_report may not be loaded; filters stay null.
	}
	return null;
}

/**
 * The Triton context ref for the current Desk page, or null.
 *
 * Moved verbatim from `triton_widget.js`. The one addition is the first guard: with no router
 * it returns null, where the original threw. That can only happen off the Desk, where the
 * widget never runs.
 */
export function detectPageContext() {
	const frappe = frappeRef();
	if (!frappe || typeof frappe.get_route !== "function") return null;
	const route = frappe.get_route();
	if (!route || !route.length) return null;
	const r0 = route[0];
	const hash = "#" + (frappe.get_route_str ? frappe.get_route_str() : route.join("/"));

	if (r0 === "Form" && route[1] && route[2]) {
		const ref = {
			type: "document",
			doctype: route[1],
			name: route[2],
			title: `${route[1]}: ${route[2]}`,
			route: hash,
		};
		try {
			if (window.cur_frm && cur_frm.doc && cur_frm.docname === route[2] && cur_frm.is_dirty && cur_frm.is_dirty()) {
				ref.unsaved = true;
			}
		} catch (e) {
			// cur_frm can be mid-teardown during a route change; unsaved stays false.
		}
		return ref;
	}
	if (r0 === "List" || r0 === "list") {
		const doctype = route[1];
		const view = route[2];
		const filters = readListFilters();
		if (view === "Report") {
			return { type: "report", report_name: doctype, name: doctype, filters, title: `${doctype} (Report)`, route: hash };
		}
		return { type: "list", doctype, filters, title: `${doctype} list`, route: hash };
	}
	if (r0 === "query-report" && route[1]) {
		const filters = readReportFilters();
		return { type: "report", report_name: route[1], name: route[1], filters, title: `Report: ${route[1]}`, route: hash };
	}
	return { type: "page", title: document.title.replace(/\s*\|.*/, "").trim() || r0, route: hash };
}

/** `frappe.get_route()` as an array of strings, or null off the Desk. */
export function currentRoute() {
	try {
		const frappe = frappeRef();
		if (!frappe || typeof frappe.get_route !== "function") return null;
		const route = frappe.get_route();
		if (!Array.isArray(route)) return null;
		return route.slice(0, 8).map((part) => (part === null || part === undefined ? "" : String(part)));
	} catch (e) {
		return null;
	}
}

/** `cur_frm`, but only when it is the form the route is showing. */
export function matchingForm(route) {
	try {
		if (!route || route[0] !== "Form") return null;
		const frm = window.cur_frm;
		if (!frm || !frm.doc || frm.doctype !== route[1] || frm.docname !== route[2]) return null;
		return frm;
	} catch (e) {
		return null;
	}
}

/**
 * A filter value fit for a report. Strings are scrubbed (a filter on `owner` is an email
 * address). Nested structures are flattened to one level, because a list of values for an "in"
 * filter is useful and anything deeper never is.
 */
function cleanValue(value, depth) {
	if (value === null || value === undefined) return null;
	const t = typeof value;
	if (t === "number" || t === "boolean") return value;
	if (t === "string") return scrubText(value, 200);
	if (Array.isArray(value) && !depth) return value.slice(0, 20).map((v) => cleanValue(v, 1));
	return scrubText(String(value), 80);
}

/** Form facts for the capture snapshot. `changed_fields` is filled in by state.js. */
export function formFacts(route) {
	if (!route || route[0] !== "Form" || !route[1] || !route[2]) return null;
	const facts = {
		doctype: route[1],
		name: route[2],
		docstatus: null,
		unsaved: null,
		is_new: null,
		changed_fields: [],
	};
	const frm = matchingForm(route);
	if (!frm) return facts;
	try {
		facts.docstatus = typeof frm.doc.docstatus === "number" ? frm.doc.docstatus : null;
		facts.unsaved = !!(frm.is_dirty && frm.is_dirty());
		facts.is_new = !!(frm.is_new && frm.is_new());
	} catch (e) {
		// A form mid-teardown: the nulls say "unknown", which is the truth.
	}
	return facts;
}

/** List facts for the capture snapshot, including the Report *view* of a doctype list. */
export function listFacts(route) {
	if (!route || (route[0] !== "List" && route[0] !== "list") || !route[1]) return null;
	const facts = { doctype: route[1], view: route[2] || "List", filters: [] };
	try {
		if (!window.cur_list || window.cur_list.doctype !== route[1]) return facts;
		const raw = readListFilters();
		if (Array.isArray(raw)) {
			facts.filters = raw
				.slice(0, 30)
				.filter(Array.isArray)
				.map((f) => f.slice(0, 4).map((v) => cleanValue(v, 0)));
		}
	} catch (e) {
		// Filters stay empty.
	}
	return facts;
}

/** Query and script report facts for the capture snapshot. */
export function reportFacts(route) {
	if (!route || route[0] !== "query-report" || !route[1]) return null;
	const facts = { name: route[1], filters: {} };
	try {
		const frappe = frappeRef();
		if (!frappe.query_report || frappe.query_report.report_name !== route[1]) return facts;
		const raw = readReportFilters();
		if (raw && typeof raw === "object") {
			for (const key of Object.keys(raw).slice(0, 40)) {
				if (key === "sid") continue;
				facts.filters[key] = cleanValue(raw[key], 0);
			}
		}
	} catch (e) {
		// Filters stay empty.
	}
	return facts;
}
