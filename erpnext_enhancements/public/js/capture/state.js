/**
 * Page state and device facts for the capture snapshot, read at the moment somebody opens the
 * report form rather than recorded as they go.
 *
 * Every function takes the window it reads, so the recorder test can hand it a fake one. Each
 * one returns a plain JSON-safe object and never throws: the recorder calls them while building
 * a snapshot, and a fault here must cost a field rather than the report.
 *
 * Web pages never read `location.search`. Token pages carry their secret there. The Desk's
 * query string is kept, because on the Desk it holds list filters and nothing secret.
 */

import { scrubPath, scrubText } from "./scrub.js";
import { currentRoute, formFacts, listFacts, matchingForm, reportFacts } from "./page_context.js";

// Fields every save rewrites. Listing them as "changed" would be noise on every form.
const SKIP_FIELDS = { modified: 1, modified_by: 1, creation: 1, owner: 1, idx: 1, docstatus: 1 };
const MAX_CHANGED = 50;

let baseline = null;
let formHookInstalled = false;

/** Frappe writes null, undefined and "" for the same empty field at different times. */
function normalize(value) {
	return value === null || value === undefined || value === "" ? "" : String(value);
}

/**
 * The scalar fields of a doc, normalized. Child tables and objects are skipped on purpose: the
 * point is a cheap list of names, and walking child rows on every form refresh is not cheap.
 */
function scalars(doc) {
	const out = {};
	for (const key of Object.keys(doc || {})) {
		if (key.charAt(0) === "_" || SKIP_FIELDS[key]) continue;
		const value = doc[key];
		const t = typeof value;
		if (value === null || value === undefined || t === "string" || t === "number" || t === "boolean") {
			out[key] = normalize(value);
		}
	}
	return out;
}

/**
 * Take a baseline of the form's saved values on each refresh, so a report can say which fields
 * the user had changed. Names only ever leave this module. The values stay in memory, for one
 * form at a time.
 *
 * A refresh on the *same* doc while it is dirty keeps the old baseline. Form scripts call
 * `frm.refresh()` in the middle of an edit, and re-baselining there would hide the edit.
 */
export function installFormBaseline(win) {
	if (formHookInstalled) return true;
	try {
		const form = win.frappe && win.frappe.ui && win.frappe.ui.form;
		if (!form || typeof form.on !== "function") return false;
		// Set before the call, not after: frappe.ui.form.on pushes the handler before it does
		// anything that could throw, so retrying after a throw would register it twice.
		formHookInstalled = true;
		// It returns a settled promise, always. A handler that returns nothing makes ScriptManager
		// wait on `frappe.after_server_call()`, i.e. on every frappe.call in flight, before the
		// form moves past refresh; that would slow every form in the Desk for a feature that
		// must not change the page.
		form.on("*", "refresh", (frm) => {
			try {
				if (!frm || !frm.doc) return Promise.resolve();
				const same = baseline && baseline.doctype === frm.doctype && baseline.name === frm.docname;
				if (same && frm.is_dirty && frm.is_dirty()) return Promise.resolve();
				baseline = { doctype: frm.doctype, name: frm.docname, values: scalars(frm.doc) };
			} catch (e) {
				baseline = null;
			}
			return Promise.resolve();
		});
	} catch (e) {
		return false;
	}
	return true;
}

/** Names of the scalar fields that differ from the baseline, or [] when that is unknowable. */
export function changedFields(frm) {
	try {
		if (!frm || !frm.doc || !baseline) return [];
		if (baseline.doctype !== frm.doctype || baseline.name !== frm.docname) return [];
		if (frm.is_dirty && !frm.is_dirty()) return [];
		const now = scalars(frm.doc);
		const before = baseline.values;
		const names = [];
		for (const key of Object.keys(now)) {
			if (now[key] !== (key in before ? before[key] : "")) names.push(key);
			if (names.length >= MAX_CHANGED) break;
		}
		return names;
	} catch (e) {
		return [];
	}
}

/**
 * The query string decoded before it is scrubbed. `?owner=bob%40acme.com` is how the Desk
 * writes an email into a filter, and the scrubber's patterns look for the decoded form.
 * Kept decoded: `context_url` is for reading, never followed.
 */
export function decodeQuery(search) {
	const text = String(search || "").replace(/\+/g, " ");
	try {
		return decodeURIComponent(text);
	} catch (e) {
		// A malformed escape: decode what can be, piece by piece.
		return text.replace(/(%[0-9A-Fa-f]{2})+/g, (m) => {
			try {
				return decodeURIComponent(m);
			} catch (e2) {
				return m;
			}
		});
	}
}

/** The `page` block of the snapshot. */
export function collectPage(win, surface) {
	const page = { path: "", query: null, route: null, title: "", form: null, list: null, report: null };
	try {
		const loc = win.location || {};
		page.path = scrubPath(loc.pathname || "");
		page.title = scrubText((win.document && win.document.title) || "", 200);
		if (surface !== "desk") return page;

		page.query = loc.search ? scrubText(decodeQuery(loc.search), 500) : null;
		const route = currentRoute();
		if (!route) return page;
		page.route = route;
		page.form = formFacts(route);
		if (page.form) page.form.changed_fields = changedFields(matchingForm(route));
		page.list = listFacts(route);
		page.report = reportFacts(route);
	} catch (e) {
		// Whatever was read before the fault stays.
	}
	return page;
}

function mediaMatches(win, query) {
	try {
		return !!(win.matchMedia && win.matchMedia(query).matches);
	} catch (e) {
		return false;
	}
}

/**
 * The service worker's deploy token. The kiosk registers `/kiosk-sw.js?v=<token>`, so the
 * controller's own script URL says which build is serving the page. That is exactly the
 * question a stale-cache report needs answered. Only that one parameter is read, and only when
 * it looks like a version.
 */
function swVersion(win) {
	try {
		const controller = win.navigator && win.navigator.serviceWorker && win.navigator.serviceWorker.controller;
		if (!controller || !controller.scriptURL) return null;
		const match = /[?&]v=([\w.-]{1,64})(?:&|$)/.exec(controller.scriptURL);
		return match ? match[1] : scrubPath(controller.scriptURL, win.location && win.location.origin);
	} catch (e) {
		return null;
	}
}

/** The `device` block of the snapshot. */
export function collectDevice(win) {
	const device = {
		online: true,
		viewport: { w: 0, h: 0 },
		pixel_ratio: 1,
		theme: "",
		locale: "",
		standalone: false,
		sw_version: null,
		user_agent: "",
	};
	try {
		const nav = win.navigator || {};
		device.online = nav.onLine !== false;
		device.viewport = { w: Math.round(win.innerWidth || 0), h: Math.round(win.innerHeight || 0) };
		device.pixel_ratio = Number(win.devicePixelRatio) || 1;
		const root = win.document && win.document.documentElement;
		const attr = root && root.getAttribute ? root.getAttribute("data-theme") : "";
		device.theme = /^[a-z-]{1,20}$/.test(attr || "")
			? attr
			: mediaMatches(win, "(prefers-color-scheme: dark)")
				? "dark"
				: "light";
		device.locale = String(nav.language || "").slice(0, 20);
		device.standalone = mediaMatches(win, "(display-mode: standalone)") || nav.standalone === true;
		device.sw_version = swVersion(win);
		device.user_agent = String(nav.userAgent || "").slice(0, 300);
	} catch (e) {
		// Defaults stand.
	}
	return device;
}
