// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * Form kit P3.3: erpnext_enhancements.report_face, the one formatter script reports call for their
 * phone, party, pillar, money and date columns. Loaded on every Desk page by
 * erpnext_enhancements.bundle.js, because a report's .js runs on the query-report page and can
 * import nothing. The rules are in cells.js (pure, tested under node); this file is only the
 * Desk half. docs/app-design-system.md, "The report face", is the guide.
 *
 * A report's formatter, whole:
 *
 *   formatter(value, row, column, data, default_formatter) {
 *       return erpnext_enhancements.report_face.format(value, row, column, data, default_formatter);
 *   }
 *
 * and a report with its own coloring wraps what that returns. For HTML a report builds itself (a
 * dialog, a message) the same answers are there one at a time: phone(v), date(v), datetime(v),
 * time(v), money(v, currency), party(doctype, name, label, { new_tab }), pillar(streams),
 * chip(label, tone).
 *
 * Fails open: if the face throws, the cell gets the report's default formatter and the console
 * one warning, never a blank column.
 */

import { esc } from "../sf_kit/esc.js";
import * as cells from "./cells.js";

let warned = false;
function warn_once(error) {
	if (warned) return;
	warned = true;
	try {
		console.warn("report_face: a cell fell back to the default formatter", error);
	} catch (e) {
		// nothing to do
	}
}

// The record's route from Frappe's own helper (never a hand-built /app/ or /desk/ path), or ""
// when the person cannot read the doctype: Frappe's Link formatter shows text then, and so do we.
function form_link_for(f) {
	return (doctype, name) => {
		if (!doctype || !name) return "";
		if (f.model && typeof f.model.can_read === "function" && !f.model.can_read(doctype)) return "";
		return f.utils.get_form_link(doctype, name);
	};
}

function money_for(w) {
	return (value, currency) => {
		if (value === null || value === undefined || value === "") return "";
		if (typeof w.format_currency === "function") return w.format_currency(value, currency);
		return esc(value);
	};
}

// Frappe v16's own Datetime formatter (form/formatters.js) shows
// moment(frappe.datetime.convert_to_user_tz(value)): stored system time, moved into the person's
// time zone. The face does the same move before the twin formats it (cells.user_datetime).
function to_user_tz_for(f) {
	return (value) => {
		const dt = f.datetime;
		if (!dt || typeof dt.convert_to_user_tz !== "function") return value;
		try {
			return dt.convert_to_user_tz(value);
		} catch (e) {
			warn_once(e);
			return value;
		}
	};
}

export function make_face(f, w) {
	const form_link = form_link_for(f);
	const to_user_tz = to_user_tz_for(f);

	function format(value, row, column, data, default_formatter, filter) {
		const fallback = () => default_formatter(value, row, column, data, filter);
		try {
			return cells.format_cell(value, column, data, {
				fallback,
				money: () =>
					default_formatter(value, row, Object.assign({}, column, { fieldtype: "Currency" }), data, filter),
				form_link,
				to_user_tz,
			});
		} catch (e) {
			warn_once(e);
			return fallback();
		}
	}

	return {
		format,
		kind: cells.face_kind,
		phone: cells.phone_text,
		date: cells.date_text,
		datetime: (value) => cells.datetime_text(value, to_user_tz),
		time: cells.time_text,
		pillar: cells.pillar_html,
		chip: cells.chip_html,
		money: money_for(w),
		party(doctype, name, label, options) {
			return cells.party_html({
				doctype,
				name,
				label,
				href: form_link(doctype, name),
				new_tab: Boolean(options && options.new_tab),
			});
		},
	};
}

export function install(f, w) {
	if (!f || !f.utils || typeof f.provide !== "function") return false;
	f.provide("erpnext_enhancements.report_face");
	Object.assign(w.erpnext_enhancements.report_face, make_face(f, w));
	return true;
}

if (typeof frappe !== "undefined" && typeof window !== "undefined") {
	try {
		install(frappe, window);
	} catch (e) {
		warn_once(e);
	}
}
