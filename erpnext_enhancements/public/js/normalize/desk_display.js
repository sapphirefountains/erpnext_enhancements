// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Form kit P2-B: the Desk shows a phone the way people read it — (801) 555-0100 x123 — while
// the server stores it +18015550100,123 (utils/normalize_hook.py). Loaded by
// erpnext_enhancements.bundle.js. docs/data-normalization.md is the prose version.
//
// Two things are wired, and nothing else is touched:
//
// 1. Display. frappe.form.formatters.Data is wrapped. Every read surface reaches it through
//    frappe.format: a read-only field on a form (ControlInput.set_disp_area), a static grid
//    cell (GridRow._format_static_value), a list view column, a report view cell, a script
//    report cell (query_report's format_cell), and the Phone / Read Only / Select fieldtypes,
//    which have no formatter of their own and fall back to Data. A registered phone field —
//    matched on the docfield's parent + fieldname against frappe.boot.sf_normalize — shows
//    phone_display(value). A Data value in exactly the stored North American shape
//    (+18015550100 or +18015550100,123) is shown the same way even with no registry match,
//    because script-report columns and hand-built dfs carry no parent. Every other value goes
//    to the original formatter untouched.
//
// 2. Editing. ControlData.prototype.format_for_input and .parse are wrapped, for a Data or
//    Read Only control whose field is a registered phone (grid row controls included; list
//    and filter controls, df.is_filter, are left alone so a search stays what was typed). The
//    input shows (801) 555-0100; what the person types is normalized before it reaches the
//    model, so the doc holds +18015550100; a value that cannot be normalized is kept exactly
//    as typed; clearing the field clears it. While the person is still typing, the input keeps
//    their own text as long as it means the stored value, so the caret never jumps.
//
// Fail open: every wrapper runs the original behaviour if its own part throws, and logs to the
// console once. Kill switch: no frappe.boot.sf_normalize (site_config
// disable_value_normalization, read by boot) and nothing is installed.
//
// The decisions are the pure functions exported below, so tests/test_desk_display.py runs them
// under node with no DOM; the Frappe wiring at the bottom is kept thin.

import { phone, phone_display } from "./normalize.js";

// Exactly what normalize.phone stores for a North American number, with or without an
// extension. Anything else is not assumed to be a phone without a registry match.
export const STORED_NANP = /^\+1[2-9][0-9]{9}(,[0-9]{1,6})?$/;

// Fieldtypes the stored-shape fallback may format. A Link or a Select holding that string is
// a name or an option, not a phone to reformat.
const FALLBACK_FIELDTYPES = new Set(["", "Data", "Phone", "Read Only"]);

// The controls whose input is reformatted. ControlLink, ControlPhone and the rest extend
// ControlData; only a plain Data (or Read Only) field is one of ours.
const EDIT_FIELDTYPES = new Set(["Data", "Read Only"]);

// Data options that already have their own formatting in frappe.
const OWN_OPTIONS = new Set(["URL", "IBAN", "Email"]);

function own(obj, key) {
	return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
}

export function registered_kind(df, registry, doctype = null) {
	if (!df || !df.fieldname || !registry || !registry.fields) return null;
	const parent = df.parent || doctype;
	if (!parent || !own(registry.fields, parent)) return null;
	const fields = registry.fields[parent];
	return own(fields, df.fieldname) ? fields[df.fieldname] : null;
}

export function is_registered_phone(df, registry, doctype = null) {
	return registered_kind(df, registry, doctype) === "phone";
}

// What a read surface shows for `value`, or null to leave the original formatter alone.
export function display_value(df, value, registry, doctype = null) {
	if (typeof value !== "string" || !value) return null;
	if (df && OWN_OPTIONS.has(df.options)) return null;
	if (!is_registered_phone(df, registry, doctype)) {
		if (df && !FALLBACK_FIELDTYPES.has(df.fieldtype || "")) return null;
		if (!STORED_NANP.test(value)) return null;
	}
	const shown = phone_display(value);
	return shown === value ? null : shown;
}

// What the model receives for text typed into a registered phone field: the stored form when
// it can be normalized, the text exactly as typed when it cannot, "" when it is blank.
export function parse_value(value) {
	if (typeof value !== "string") return value;
	const result = phone(value);
	return result.ok ? result.value : value;
}

// What the input shows for a model value. `typed` is the input's own text while it has focus
// (undefined otherwise): kept when it means the same stored value, so typing is undisturbed.
export function input_value(value, typed = undefined) {
	if (value === null || value === undefined) return "";
	if (typeof value !== "string") return value;
	if (typeof typed === "string" && typed && parse_value(typed) === value) return typed;
	return phone_display(value);
}

export function escape_html(text) {
	return String(text)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#39;");
}

export function is_phone_control(control, registry) {
	const df = control && control.df;
	if (!df || df.is_filter || !EDIT_FIELDTYPES.has(df.fieldtype)) return false;
	if (OWN_OPTIONS.has(df.options)) return false;
	return is_registered_phone(df, registry, control.doctype || null);
}

// ---------------------------------------------------------------------------
// Frappe wiring

let warned = false;
function warn_once(error) {
	if (warned) return;
	warned = true;
	try {
		console.warn("sf_normalize: Desk phone display failed open", error);
	} catch (e) {
		// nothing to do
	}
}

function wrap_formatter(f, registry) {
	const original = f.form.formatters.Data;
	if (typeof original !== "function" || original.__sf_normalize) return;
	const wrapped = function (value, df, options, doc) {
		let shown = null;
		try {
			shown = display_value(df, value, registry, (doc && doc.doctype) || null);
		} catch (e) {
			warn_once(e);
			shown = null;
		}
		// Only digits, spaces, ( ) - + and x reach here, but it is HTML all the same.
		return original.call(this, shown === null ? value : escape_html(shown), df, options, doc);
	};
	wrapped.__sf_normalize = true;
	f.form.formatters.Data = wrapped;
}

function wrap_control(f, registry) {
	const proto = f.ui.form.ControlData && f.ui.form.ControlData.prototype;
	if (!proto || proto.__sf_normalize) return;
	const original_format = proto.format_for_input;
	const original_parse = proto.parse;
	if (typeof original_format !== "function" || typeof original_parse !== "function") return;

	proto.format_for_input = function (val) {
		try {
			if (is_phone_control(this, registry)) {
				const focused =
					this.input && typeof document !== "undefined" && document.activeElement === this.input;
				return input_value(val, focused && this.$input ? this.$input.val() : undefined);
			}
		} catch (e) {
			warn_once(e);
		}
		return original_format.call(this, val);
	};
	proto.parse = function (value) {
		const parsed = original_parse.call(this, value);
		try {
			if (is_phone_control(this, registry)) return parse_value(parsed);
		} catch (e) {
			warn_once(e);
		}
		return parsed;
	};
	proto.__sf_normalize = true;
}

export function install(f) {
	const registry = f && f.boot && f.boot.sf_normalize;
	if (!registry || !registry.fields) return false;
	if (f.form && f.form.formatters) {
		try {
			wrap_formatter(f, registry);
		} catch (e) {
			warn_once(e);
		}
	}
	if (f.ui && f.ui.form) {
		try {
			wrap_control(f, registry);
		} catch (e) {
			warn_once(e);
		}
	}
	try {
		// For hand-built HTML on the Desk (the contacts directory, for one).
		f.provide("erpnext_enhancements.normalize");
		window.erpnext_enhancements.normalize.phone_display = phone_display;
	} catch (e) {
		warn_once(e);
	}
	return true;
}

if (typeof frappe !== "undefined") {
	install(frappe);
}
