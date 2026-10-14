// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * Form kit P3.3, the report face: what a script report's cell shows for a phone, a party, a
 * pillar, money and a date. One answer, so a phone reads (801) 555-0100 and a date 10/14/2026 in
 * every report, the way print, email and SMS already show them (docs/app-design-system.md, "The
 * report face").
 *
 * Pure: no frappe and no DOM, so tests/test_report_face.py runs every function here under node.
 * report_face.js is the thin Desk half (frappe.utils.get_form_link, the column's own currency
 * formatting, the namespace a report calls). Phones and dates come from the normalization twin
 * (normalize/normalize.js, held to utils/normalize.py by one case file); the escape is sf_kit's
 * one escape (a single small module, not the kit).
 *
 * A column says what it is with one key, `sf_face`:
 *
 *   phone     (801) 555-0100 x12 from the stored +18015550100,12
 *   party     a link to the record, showing its human name (`sf_label` names the row field
 *             holding it). The column is a Link or a Dynamic Link, so Frappe's own formatter
 *             still draws a working link on a device that never got this file.
 *   pillar    a value stream (or a comma-separated list of them) as chips in the pillars' colors
 *   money     Frappe's currency formatting, with the column's own currency options
 *   date, datetime, time
 *             10/14/2026, 10/14/2026 2:00 PM, 2:00 PM
 *
 * A column with no marker takes its kind from its fieldtype: Date, Datetime, Time, Currency and
 * Phone. `sf_face: "none"` (or false) leaves a column to Frappe. An unknown marker does too:
 * markers come from report code, and a typo must not blank a column.
 */

import { esc } from "../sf_kit/esc.js";
import { phone_display, format_date, format_datetime, format_time } from "../normalize/normalize.js";

export const FACE_KEY = "sf_face";
export const LABEL_KEY = "sf_label";
export const KINDS = ["phone", "party", "pillar", "money", "date", "datetime", "time"];

// The kind a column takes when it carries no marker.
export const FIELDTYPE_KINDS = {
	Date: "date",
	Datetime: "datetime",
	Time: "time",
	Currency: "money",
	Phone: "phone",
};

export function face_kind(column) {
	if (!column) return null;
	const marked = column[FACE_KEY];
	if (marked === false || marked === "none") return null;
	if (marked) return KINDS.includes(marked) ? marked : null;
	return Object.prototype.hasOwnProperty.call(FIELDTYPE_KINDS, column.fieldtype)
		? FIELDTYPE_KINDS[column.fieldtype]
		: null;
}

// ---------------------------------------------------------------------------
// Phones and dates: the twin's words, escaped. A value the twin cannot read shows as stored.

export function phone_text(value) {
	return esc(phone_display(value));
}

export function date_text(value) {
	return esc(format_date(value));
}

export function datetime_text(value) {
	return esc(format_datetime(value));
}

export function time_text(value) {
	return esc(format_time(value));
}

// ---------------------------------------------------------------------------
// Pillars

// A copy of print_style.STREAM_PILLAR, the one record of which pillar a value stream takes
// (docs/app-design-system.md, "Pillars and value streams"). tests/test_report_face.py holds it to
// the original, as test_print_style holds project_brief.js's copy.
export const STREAM_PILLAR = {
	Design: "design",
	Build: "build",
	Products: "build",
	Delivery: "build",
	Service: "service",
	Events: "rent",
};

export function pillar_for(stream) {
	return Object.prototype.hasOwnProperty.call(STREAM_PILLAR, stream) ? STREAM_PILLAR[stream] : null;
}

// The chip, as the declarations of sf_kit's .sf-chip and .sf-chip--<tone> rules
// (css/sf_kit.bundle.css; the test holds these tables to that file). Written inline because a
// report page loads no sf_kit stylesheet, and the tokens they read are on every Desk page
// (sf_tokens.bundle.css), so the chip follows the light and dark themes like the kit's does.
export const CHIP_BASE = [
	["box-sizing", "border-box"],
	["display", "inline-flex"],
	["align-items", "center"],
	["max-width", "100%"],
	["padding", "1px var(--sf-space-10)"],
	["border", "var(--sf-border-hairline) solid var(--sf-chip-edge-neutral)"],
	["border-radius", "var(--sf-radius-pill)"],
	["background", "var(--sf-pillar-neutral-deep)"],
	["color", "var(--sf-on-pillar)"],
	["font-family", "var(--sf-font-sans)"],
	["font-size", "var(--sf-text-xs)"],
	["font-weight", "var(--sf-weight-bold)"],
	["line-height", "var(--sf-leading-body)"],
	["white-space", "nowrap"],
	["overflow", "hidden"],
	["text-overflow", "ellipsis"],
	["vertical-align", "middle"],
];

export const CHIP_TONE_RULES = {
	design: [
		["background", "var(--sf-pillar-design-deep)"],
		["border-color", "var(--sf-chip-edge-design)"],
	],
	build: [
		["background", "var(--sf-pillar-build-deep)"],
		["border-color", "var(--sf-chip-edge-build)"],
	],
	service: [
		["background", "var(--sf-pillar-service-deep)"],
		["border-color", "var(--sf-chip-edge-service)"],
	],
	rent: [
		["background", "var(--sf-pillar-rent-deep)"],
		["border-color", "var(--sf-chip-edge-rent)"],
	],
	danger: [
		["background", "var(--sf-danger)"],
		["border-color", "var(--sf-chip-edge-danger)"],
		["color", "var(--sf-on-danger)"],
	],
	warn: [
		["background", "var(--sf-warn)"],
		["border-color", "var(--sf-chip-edge-warn)"],
		["color", "var(--sf-on-warn)"],
	],
	good: [
		["background", "var(--sf-good)"],
		["border-color", "var(--sf-chip-edge-good)"],
		["color", "var(--sf-on-good)"],
	],
};

export const CHIP_TONES = ["design", "build", "service", "rent", "neutral", "danger", "warn", "good"];

export function chip_tone(tone) {
	return CHIP_TONES.includes(tone) ? tone : "neutral";
}

export function chip_style(tone) {
	const rules = CHIP_BASE.concat(CHIP_TONE_RULES[chip_tone(tone)] || []);
	return rules.map(([property, value]) => `${property}: ${value}`).join("; ");
}

// A chip with no word is not drawn: the word is the meaning and the color only repeats it.
export function chip_html(label, tone) {
	const text = label === null || label === undefined ? "" : String(label).trim();
	if (!text) return "";
	const t = chip_tone(tone);
	return `<span class="sf-report-chip sf-report-chip--${t}" style="${chip_style(t)}">${esc(text)}</span>`;
}

// The value streams in a cell, in order, each once. A report joins a child table's streams
// with ", " (GROUP_CONCAT), so a comma or a new line separates them.
export function streams_of(value) {
	if (value === null || value === undefined) return [];
	const seen = new Set();
	const out = [];
	String(value)
		.split(/[,\n]/)
		.map((part) => part.trim())
		.forEach((stream) => {
			if (!stream || seen.has(stream)) return;
			seen.add(stream);
			out.push(stream);
		});
	return out;
}

// Each known stream as a chip in its pillar's colors. A word that is not a value stream (a
// report's "Unallocated" row, a dash) stays plain text: a chip would make it read as a stream.
export function pillar_html(value) {
	const streams = streams_of(value);
	if (!streams.some((stream) => pillar_for(stream))) return esc(streams.join(", "));
	return streams
		.map((stream) => {
			const pillar = pillar_for(stream);
			return pillar ? chip_html(stream, pillar) : esc(stream);
		})
		.join(" ");
}

// ---------------------------------------------------------------------------
// Parties

function text_of(value) {
	return value === null || value === undefined ? "" : String(value);
}

// Which record a party cell points at, and the name to show for it. The doctype is the Link
// column's options, or for a Dynamic Link the row field its options name, as in Frappe.
export function party_of(value, column, data) {
	const row = data || {};
	const col = column || {};
	const name = text_of(value).trim();
	const doctype = text_of(col.fieldtype === "Dynamic Link" ? row[col.options] : col.options).trim();
	const label_field = col[LABEL_KEY];
	const label = text_of(label_field ? row[label_field] : "").trim() || name;
	return { doctype, name, label };
}

// The link. `href` comes from frappe.utils.get_form_link (report_face.js); with none (no record,
// no doctype, or a doctype the person cannot read) the name is plain text. data-doctype and
// data-name are what Frappe's own Link cells carry, so the hover preview works on these too.
export function party_html({ doctype, name, label, href, new_tab } = {}) {
	const shown = text_of(label).trim() || text_of(name);
	if (!href || !name || !doctype) return esc(shown);
	const target = new_tab ? ' target="_blank" rel="noopener"' : "";
	return (
		`<a href="${esc(href)}" data-doctype="${esc(doctype)}" data-name="${esc(name)}" ` +
		`data-value="${esc(name)}"${target}>${esc(shown)}</a>`
	);
}

// ---------------------------------------------------------------------------
// The dispatcher

// What a script report's cell shows. `env` is the Desk half:
//   fallback()              the report's default formatter on the original column
//   money()                 the default formatter on the column read as Currency
//   form_link(doctype, name) the record's route, or "" when the person cannot open it
// A row with no data (the total row) is left to Frappe, which knows how to draw it.
export function format_cell(value, column, data, env) {
	const kind = face_kind(column);
	if (!kind || !data) return env.fallback();
	switch (kind) {
		case "phone":
			return phone_text(value);
		case "date":
			return date_text(value);
		case "datetime":
			return datetime_text(value);
		case "time":
			return time_text(value);
		case "money":
			return env.money();
		case "pillar":
			return pillar_html(value);
		case "party": {
			const party = party_of(value, column, data);
			const href = party.doctype && party.name ? env.form_link(party.doctype, party.name) : "";
			return party_html({ ...party, href });
		}
		default:
			return env.fallback();
	}
}
