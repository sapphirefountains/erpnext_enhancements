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
 *     new or existing, remembered in sessionStorage until the form's Guided button is used);
 *   - for a link into one tab or field of the form (a #hash);
 *   - for an unsaved record the Desk already holds that the guided page cannot take whole (a
 *     table other than the ones the flow takes, or a field the flow does not offer), and for one
 *     a Link field's "Create a new ..." is waiting on;
 *   - for a record just saved from the full form (frappe.model.new_names).
 * An unsaved record the guided page CAN take whole (ERPNext's own Create > Opportunity on a
 * Customer maps the party, a contact, an address and the value streams) opens there, with them.
 *
 * BACK AND FORWARD. The form and the guided page of one record are two screens, and switching
 * between them twice must not leave two copies of one in history (Nik's rule, 2026-09-24: never
 * break Back/Forward). Each switch tags the entry it pushes (history.state.sf_toggle); switching
 * back from a tagged entry steps back instead of pushing.
 *
 * NOT EVERY FLOW IS THE DEFAULT (form kit P8.1). A DocType whose sf_guided entry has `default: 0`
 * (its config's open_by_default False: Item, until Nik has reviewed its flow) keeps the form as
 * where its records open; nothing here redirects it. Its flow is reached on purpose: the form's
 * Guided button (has_flow), the guided page's start screen and /desk/guided-entry/Item/new.
 *
 * Kill switch: site_config `disable_guided_default` empties frappe.boot.sf_guided.
 */

import { redirect_for, full_form_key, opens_by_default } from "./form_kit/opening.js";

const STORE_KEY = "sf_guided_full_form";
const STORE_LIMIT = 200;
const state = { installed: false };
// Every document has these; they are never values a new record is started with.
const ROW_SKIP = new Set(["name", "doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parentfield", "parenttype"]);

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

// The entry just pushed came from switching away from `from` (see BACK AND FORWARD).
function tag_entry(from) {
	try {
		window.history.replaceState(Object.assign({}, window.history.state, { sf_toggle: from }), "");
	} catch (e) {
		// Untagged, switching back pushes a new entry rather than stepping back.
	}
}

function tagged(from) {
	return ((window.history.state || {}).sf_toggle || "") === from;
}

const api = {
	// Whether records of `doctype` open on the guided page for this person: a saved one when they
	// may change it (`default`), a New when they may create one (`is_new`, `default_new`).
	is_guided(doctype, is_new) {
		return page_open() && opens_by_default(guided()[doctype], !!is_new);
	},
	// Whether `doctype` has a guided flow this person may open, default or not: the form's Guided
	// button and the guided page's start screen offer it (Item, which is not the default, P8.1).
	has_flow(doctype) {
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
	// The guided page's "Open form" for a saved record (to `tab`, when given).
	to_form(doctype, name, tab) {
		api.allow_form(doctype, name);
		const key = full_form_key(doctype, name);
		if (!tab && tagged(`form:${key}`)) {
			window.history.back();
			return;
		}
		if (tab) frappe.route_hash = `#${tab}`;
		frappe.set_route("Form", doctype, name);
		tag_entry(`guided:${key}`);
	},
	// The form's Guided button: this record opens on the guided page again, from now on.
	to_guided(doctype, name) {
		api.forget_form(doctype, name);
		const key = full_form_key(doctype, name);
		if (tagged(`guided:${key}`)) {
			window.history.back();
			return;
		}
		frappe.set_route("guided-entry", doctype, name);
		tag_entry(`form:${key}`);
	},
	// A new record in the full form, starting from `values` (what step 1 held, tables included).
	// Made here rather than with frappe.new_doc, which opens Quick Entry for a DocType that has it
	// (Opportunity does) and would drop the tables; the unsaved record is then left on the form.
	new_in_form(doctype, values) {
		return new Promise((resolve) => frappe.model.with_doctype(doctype, resolve)).then(() => {
			const options = frappe.route_options;
			frappe.route_options = null;
			let doc;
			try {
				doc = frappe.model.get_new_doc(doctype);
			} finally {
				frappe.route_options = options;
			}
			const fields = (frappe.get_meta(doctype) || {}).fields || [];
			Object.keys(values || {}).forEach((fieldname) => {
				const df = fields.find((field) => field.fieldname === fieldname);
				const value = values[fieldname];
				if (!df || value === null || value === undefined) return;
				if (Array.isArray(value)) {
					if (!frappe.model.table_fields.includes(df.fieldtype)) return;
					value.forEach((row) => {
						const child = frappe.model.add_child(doc, df.options, fieldname);
						Object.keys(row || {}).forEach((key) => {
							if (!key.startsWith("__") && !ROW_SKIP.has(key)) child[key] = row[key];
						});
					});
				} else if (typeof value !== "object" && String(value).trim() !== "") {
					doc[fieldname] = value;
				}
			});
			api.allow_form(doctype, doc.name);
			frappe.set_route("Form", doctype, doc.name);
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
	if (!Array.isArray(route) || route[0] !== "Form") return false;
	// Only a DocType that opens here (saved, or new) for this person; redirect_for decides which.
	if (!api.is_guided(route[1]) && !api.is_guided(route[1], true)) return false;
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
		renamed: Object.values(frappe.model.new_names || {}),
		options: frappe.route_options || {},
		local,
		fresh: local ? fresh_doc(doctype) : null,
		ignore: local ? ignored_fields(doctype) : [],
	});
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
		if (api.is_guided(doctype, true)) frappe.create_routes[doctype] = ["guided-entry", doctype, "new"];
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
