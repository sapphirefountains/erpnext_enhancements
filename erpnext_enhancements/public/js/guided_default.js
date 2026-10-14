/**
 * The guided page is where a guided DocType's records open (form kit P5.2, ERPNext TASK-2026-02742).
 *
 * Nik, 2026-10-13: "I want these guided setups to be the default for new and existing documents
 * of the DocTypes we are making guided setups for." Those DocTypes are frappe.boot.sf_guided
 * (form_kit.config.CONFIGS; Opportunity today), for people who may open the guided page.
 *
 * NEW. frappe.create_routes is v16's own hook for it: frappe.new_doc (the list's + Add, the
 * awesomebar, the Kanban's add card, the Customer form's Create > Opportunity in public/js/
 * customer.js) routes there instead of making a form, and its options travel as route options,
 * which the guided page reads as step 1's starting values.
 *
 * EXISTING. v16's view factories ask `before_show()` before they draw, and stop when it returns
 * false; the List view uses that to re-route (frappe/public/js/frappe/list/list_factory.js). The
 * Form factory defines none, so this adds one: a Form route for a guided DocType goes to the
 * guided page, replacing the form's history entry so Back still leaves the way the person came.
 * Any before_show already there runs first and keeps its say.
 *
 * WHEN THE FORM STAYS. The decision is form_kit/opening.js's redirect_for (pure, and run under
 * node by tests/test_guided_entry.py). The form opens as before:
 *   - for anyone who cannot open the guided page (frappe.boot.page_info);
 *   - for a record this tab opened in the full form on purpose (the guided page's "Open form",
 *     remembered in sessionStorage until the form's Guided button is used);
 *   - for a link into one tab or field of the form (a #hash);
 *   - for an unsaved record the Desk already holds that the guided page cannot take whole (a
 *     mapped document with a table in it, or a field the flow does not offer), for one a Link
 *     field's "Create a new ..." is waiting on, and for one asked for in the full form;
 *   - for a record just saved from the full form (frappe.model.new_names).
 * An unsaved record the guided page CAN take whole (ERPNext's own Create > Opportunity on a
 * Customer maps the party, a contact and an address) opens there, with those values.
 *
 * Kill switch: site_config `disable_guided_default` empties frappe.boot.sf_guided.
 */

import { redirect_for, full_form_key } from "./form_kit/opening.js";

const STORE_KEY = "sf_guided_full_form";
const STORE_LIMIT = 200;
const state = { bypass: null, installed: false };

function guided() {
	return (window.frappe && frappe.boot && frappe.boot.sf_guided) || {};
}

function page_open() {
	return !!(frappe.boot && frappe.boot.page_info && frappe.boot.page_info["guided-entry"]);
}

// sessionStorage can be missing or refuse (a private window); without it the choice lasts until
// the next reload, which only means the guided page comes back sooner.
const memory = [];
function read_store() {
	try {
		const list = JSON.parse(window.sessionStorage.getItem(STORE_KEY) || "[]");
		return Array.isArray(list) ? list : [];
	} catch (e) {
		return memory.slice();
	}
}

function write_store(list) {
	const kept = list.slice(-STORE_LIMIT);
	memory.splice(0, memory.length, ...kept);
	try {
		window.sessionStorage.setItem(STORE_KEY, JSON.stringify(kept));
	} catch (e) {
		// Kept in memory only.
	}
}

function scalar_values(values) {
	const out = {};
	Object.keys(values || {}).forEach((key) => {
		const value = values[key];
		if (value === null || value === undefined || typeof value === "object") return;
		if (String(value).trim() === "") return;
		out[key] = value;
	});
	return out;
}

const api = {
	// Whether records of `doctype` open on the guided page for this person.
	is_guided(doctype) {
		return page_open() && !!guided()[doctype];
	},
	// The guided page's "Open form": this record opens in the full form in this tab from now on.
	allow_form(doctype, name) {
		const key = full_form_key(doctype, name);
		write_store(read_store().filter((item) => item !== key).concat([key]));
	},
	// The form's Guided button: back to the default for this record.
	forget_form(doctype, name) {
		const key = full_form_key(doctype, name);
		write_store(read_store().filter((item) => item !== key));
	},
	// A new record in the full form, starting from `values` (what step 1 held): frappe.new_doc
	// without this file's create route, and the unsaved record it makes is left on the form.
	new_in_form(doctype, values) {
		const route = frappe.create_routes[doctype];
		delete frappe.create_routes[doctype];
		state.bypass = doctype;
		return Promise.resolve(frappe.new_doc(doctype, scalar_values(values))).finally(() => {
			if (route && api.is_guided(doctype)) frappe.create_routes[doctype] = route;
		});
	},
};

// A new record of `doctype` as the Desk starts one, to tell defaults from what was put there.
// frappe.model.get_new_doc applies (and clears) route options, so they are set aside around it.
function fresh_doc(doctype) {
	const options = frappe.route_options;
	frappe.route_options = null;
	try {
		const doc = frappe.model.get_new_doc(doctype);
		const copy = Object.assign({}, doc);
		frappe.model.clear_doc(doctype, doc.name);
		return copy;
	} catch (e) {
		return null;
	} finally {
		frappe.route_options = options;
	}
}

// Fields the new record fills itself (read-only, fetched, virtual): not a reason to keep the form.
function ignored_fields(doctype) {
	const meta = frappe.get_meta(doctype);
	return ((meta && meta.fields) || [])
		.filter((df) => cint(df.read_only) || df.fetch_from || cint(df.is_virtual))
		.map((df) => df.fieldname);
}

function redirect(route) {
	if (!Array.isArray(route) || route[0] !== "Form" || !api.is_guided(route[1])) return false;
	const doctype = route[1];
	const name = route.slice(2).join("/");
	const held = locals[doctype] && locals[doctype][name];
	const local = held && held.__islocal ? held : null;
	const plan = redirect_for(route, {
		guided: guided(),
		page_open: page_open(),
		full_form: read_store(),
		hash: window.location.hash || "",
		from_link: !!frappe._from_link,
		bypass: state.bypass,
		renamed: Object.values(frappe.model.new_names || {}),
		local,
		fresh: local ? fresh_doc(doctype) : null,
		ignore: local ? ignored_fields(doctype) : [],
	});
	if (local && state.bypass === doctype) state.bypass = null;
	if (!plan) return false;
	// The unsaved record goes: the guided page creates the real one from the same values.
	if (local) frappe.model.clear_doc(doctype, name);
	frappe.route_options = plan.prefill && Object.keys(plan.prefill).length ? plan.prefill : null;
	frappe.route_flags.replace_route = true;
	frappe.set_route(plan.route);
	return true;
}

function set_create_routes() {
	Object.keys(guided()).forEach((doctype) => {
		if (api.is_guided(doctype)) frappe.create_routes[doctype] = ["guided-entry", doctype, "new"];
	});
}

function install() {
	if (state.installed) return true;
	const Factory = frappe.views && frappe.views.FormFactory;
	if (!Factory || !frappe.boot || !frappe.create_routes) return false;
	const own = Factory.prototype.before_show;
	Factory.prototype.before_show = function (...args) {
		if (own && own.apply(this, args) === false) return false;
		try {
			if (redirect(this.route || frappe.get_route())) return false;
		} catch (e) {
			// The form is always a safe answer.
			console.error("[ERPNext Enhancements] Guided Entry could not decide where this record opens", e);
		}
		return undefined;
	};
	set_create_routes();
	state.installed = true;
	return true;
}

window.sf_guided = api;
if (window.frappe && !install()) {
	$(document).on("app_ready", install);
}
