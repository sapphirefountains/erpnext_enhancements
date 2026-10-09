/*
 * Planner kit: the big picture (Phase 6C, TASK-2026-02469), the pure half of what both planners add
 * in their PP6C_METHODS / MP6C_METHODS blocks: card colors, the team capacity strip's arithmetic,
 * a saved view's shape, and the printed view.
 *
 *   planner_kit.big_picture.hash_color("PRJ-00612")      // the same project is always the same color
 *   planner_kit.big_picture.job_type("Rent", false)       // "Events"
 *   planner_kit.big_picture.team_day(cells)               // a day of the whole (filtered) team
 *   planner_kit.big_picture.pick_view(state, schema)      // only the keys a planner's views may hold
 *   planner_kit.big_picture.print_document(model)         // a whole HTML document, every value escaped
 *
 * Colors. Job type and status use fixed palettes (below, and in the README): mid-tone hues that read
 * on the Desk's light and dark card backgrounds, used only as a card's 4px left edge. Project, PM,
 * site, contract and person use `hash_color`: FNV-1a (32-bit) over the key's UTF-16 code units,
 * modulo the 12-color HASH_PALETTE. It is defined arithmetic rather than a lookup anybody keeps, so
 * every device and every session paints a project the same, and tests/test_planner_phase6c.py pins
 * it against a Python twin. Two of 300 projects can share a color; the legend says which is which.
 *
 * The capacity strip: per day, the capacity, firm hours booked and free hours of everyone the page's
 * group/team filter shows, summed from the payload the page already has (`days` / `bookings`; no
 * request). Red when the team as a whole is booked past its capacity, amber at 90% or more. Pencil
 * hours are soft load, as on the heatmap: never in `booked`, drawn hatched after the firm hours.
 * "People off" counts a holiday, time off or an all-day personal block, never a day the person's
 * pattern does not work, and never says which kind (the engine's label is not passed on).
 *
 * Printing: the page builds a model of exactly what it shows (its filters, its color-by, the
 * selection when it prints only that) from the loaded data, and the server only adds the print
 * design system's chrome (api/planner_views_prefs.get_print_chrome). Nothing here clones the live DOM,
 * so no drag handle, drawer or toolbar can reach the paper.
 *
 * No DOM and no frappe at import time: tests run every function under plain node.
 */

import { escape, safe_color, hours, t } from "./escape.js";

// ---------------------------------------------------------------------- colors

export const HASH_PALETTE = [
	"#2563eb",
	"#0d9488",
	"#9333ea",
	"#ea580c",
	"#db2777",
	"#65a30d",
	"#0891b2",
	"#ca8a04",
	"#4f46e5",
	"#be185d",
	"#15803d",
	"#a16207",
];

// FNV-1a, 32-bit, over UTF-16 code units: a stable index into a palette of `size` colors.
export function hash_index(key, size) {
	const text = key === null || key === undefined ? "" : String(key);
	let hash = 0x811c9dc5;
	for (let i = 0; i < text.length; i++) {
		hash ^= text.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193) >>> 0;
	}
	return size > 0 ? hash % size : 0;
}

export function hash_color(key) {
	return HASH_PALETTE[hash_index(key, HASH_PALETTE.length)];
}

// The job a card is part of, from its project's type: what Nik means by Design / Build / Service /
// Events. A rental crew task, and a Rent project, are Events; a project whose type is blank (it counts
// on the planners through a value stream) is Other.
export const JOB_TYPES = [
	{ key: "Design", label: "Design", color: "#9333ea" },
	{ key: "Build", label: "Build", color: "#ea580c" },
	{ key: "Service", label: "Service", color: "#0891b2" },
	{ key: "Events", label: "Events (rentals)", color: "#db2777" },
	{ key: "Delivery", label: "Delivery", color: "#65a30d" },
	{ key: "Other", label: "Other", color: "#64748b" },
];

export function job_type(project_type, rental) {
	if (rental) return "Events";
	const type = String(project_type || "").trim();
	if (type === "Rent" || type === "Events") return "Events";
	if (type === "Design" || type === "Build" || type === "Service" || type === "Delivery") return type;
	return "Other";
}

// A project task's status as the planner shows it: an overdue card is Overdue whatever it says.
export const TASK_STATUSES = [
	{ key: "Open", label: "Open", color: "#2563eb" },
	{ key: "Working", label: "Working", color: "#16a34a" },
	{ key: "Pending Review", label: "Pending review", color: "#ca8a04" },
	{ key: "Overdue", label: "Overdue", color: "#dc2626" },
	{ key: "Other", label: "Other", color: "#64748b" },
];

export function task_status(status, overdue) {
	if (overdue || status === "Overdue") return "Overdue";
	if (status === "Open" || status === "Working" || status === "Pending Review") return status;
	return "Other";
}

// A maintenance visit's status, from its card (`kind`, `status`, `movable`, `overdue`).
export const VISIT_STATUSES = [
	{ key: "Scheduled", label: "Scheduled", color: "#2563eb" },
	{ key: "Next visit", label: "Next visit (not drafted)", color: "#4f46e5" },
	{ key: "Projected", label: "Projected", color: "#94a3b8" },
	{ key: "Pending review", label: "Pending review", color: "#ca8a04" },
	{ key: "Done", label: "Done", color: "#16a34a" },
	{ key: "Overdue", label: "Overdue", color: "#dc2626" },
];

export function visit_status(card) {
	card = card || {};
	if (card.kind === "projected") return card.movable ? "Next visit" : "Projected";
	if (card.status === "done") return "Done";
	if (card.status === "pending") return "Pending review";
	if (card.overdue) return "Overdue";
	return "Scheduled";
}

export function palette_color(list, key) {
	const found = (list || []).find((entry) => entry.key === key);
	return found ? found.color : "#64748b";
}

// A color that may go into a style attribute: a hex value, or the hsl() the pages hash a person to
// when their Planner Resource has no color of its own. Anything else is the fallback.
export function safe_paint(value, fallback) {
	const text = String(value || "").trim();
	if (/^#[0-9a-fA-F]{3,8}$/.test(text)) return text;
	if (/^hsl\(\d{1,3},\s?\d{1,3}%,\s?\d{1,3}%\)$/.test(text)) return text;
	return fallback || "";
}

// ---------------------------------------------------------------------- the capacity strip

export const TEAM_AMBER = 0.9;

const num = (value) => {
	const n = Number(value);
	return Number.isFinite(n) ? n : 0;
};
const round2 = (value) => Math.round(value * 100) / 100;

// One day of a team: the cells of everyone shown on that day (a missing cell is skipped).
export function team_day(cells) {
	const sum = {
		people: 0,
		working: 0,
		capacity: 0,
		booked: 0,
		soft: 0,
		free: 0,
		off: 0,
		half: 0,
		over_people: 0,
	};
	(cells || []).forEach((cell) => {
		if (!cell) return;
		const capacity = Math.max(0, num(cell.capacity));
		const booked = Math.max(0, num(cell.booked));
		sum.people += 1;
		sum.capacity += capacity;
		sum.booked += booked;
		sum.soft += Math.max(0, num(cell.soft_booked));
		sum.free += Math.max(0, cell.free === null || cell.free === undefined ? capacity - booked : num(cell.free));
		const label = String(cell.off || "");
		if (capacity > 0) sum.working += 1;
		if (capacity <= 0 && label && label !== "Not a work day") sum.off += 1;
		else if (capacity > 0 && label) sum.half += 1;
		if (booked - capacity > 0.01) sum.over_people += 1;
	});
	["capacity", "booked", "soft", "free"].forEach((key) => (sum[key] = round2(sum[key])));
	sum.ratio = sum.capacity > 0 ? sum.booked / sum.capacity : sum.booked > 0.01 ? 2 : 0;
	sum.over = round2(Math.max(0, sum.booked - sum.capacity));
	if (sum.capacity <= 0) sum.level = sum.booked > 0.01 ? "red" : "off";
	else if (sum.booked - sum.capacity > 0.01) sum.level = "red";
	else if (sum.ratio >= TEAM_AMBER) sum.level = "amber";
	else sum.level = "green";
	sum.soft_ratio = sum.capacity > 0 ? Math.min(Math.max(0, 1 - Math.min(1, sum.ratio)), sum.soft / sum.capacity) : 0;
	return sum;
}

// The strip's short text: "46h free", "Over 12h", "Full", "Off".
export function team_text(sum) {
	if (!sum || !sum.people) return "";
	if (sum.level === "off") return t("Off");
	if (sum.level === "red") return t("Over {0}h", [hours(sum.over)]);
	return sum.free > 0.01 ? t("{0}h free", [hours(sum.free)]) : t("Full");
}

// The tooltip: "Field crew Thu: 46h free of 120h · 3 people off", then the booked share and pencil.
export function team_tip(label, weekday, sum) {
	if (!sum || !sum.people) return t("{0} {1}: nobody to show", [label, weekday]);
	let first = t("{0} {1}: {2}h free of {3}h", [label, weekday, hours(sum.free), hours(sum.capacity)]);
	if (sum.off === 1) first += ` · ${t("1 person off")}`;
	else if (sum.off > 1) first += ` · ${t("{0} people off", [sum.off])}`;
	const lines = [first];
	if (sum.capacity > 0) {
		lines.push(t("{0}h booked of {1}h ({2}%)", [hours(sum.booked), hours(sum.capacity), Math.round(sum.ratio * 100)]));
	} else if (sum.booked > 0.01) {
		lines.push(t("{0}h booked on a day nobody works", [hours(sum.booked)]));
	}
	if (sum.level === "red" && sum.capacity > 0) lines.push(t("The team is booked {0}h past its hours", [hours(sum.over)]));
	if (sum.soft > 0.01) lines.push(t("+{0}h pencil (tentative, not counted as booked)", [hours(sum.soft)]));
	if (sum.half === 1) lines.push(t("1 person on a half day off"));
	else if (sum.half > 1) lines.push(t("{0} people on a half day off", [sum.half]));
	if (sum.over_people === 1) lines.push(t("1 person is over their own hours"));
	else if (sum.over_people > 1) lines.push(t("{0} people are over their own hours", [sum.over_people]));
	return lines.join("\n");
}

// ---------------------------------------------------------------------- saved views

// Only the keys `schema` allows, each of its type: `{key: ["bool"] | ["text"] | ["enum", values]}`.
// The server applies the same rules (api/planner_views_prefs.clean_view); this is the second lock.
export function pick_view(state, schema) {
	const out = {};
	if (!state || typeof state !== "object") return out;
	Object.keys(schema || {}).forEach((key) => {
		if (!Object.prototype.hasOwnProperty.call(state, key)) return;
		const rule = schema[key] || [];
		const value = state[key];
		if (rule[0] === "bool") out[key] = value === true || value === 1 || value === "1" || value === "true";
		else if (rule[0] === "enum") {
			if (typeof value === "string" && (rule[1] || []).includes(value)) out[key] = value;
		} else if (rule[0] === "text") {
			if (value === null || value === undefined) out[key] = "";
			else if (typeof value === "string" || typeof value === "number") {
				out[key] = String(value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 140);
			}
		}
	});
	return out;
}

// ---------------------------------------------------------------------- print

export const PRINT_CSS = `
@page { size: letter landscape; margin: 9mm; }
html, body { margin: 0; background: #ffffff; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; font-family: Lato, "Helvetica Neue", Helvetica, Arial, sans-serif; color: #363636; }
.pkp-grid { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 9px; line-height: 1.3; }
.pkp-grid th { text-align: left; vertical-align: bottom; }
.pkp-grid td { vertical-align: top; word-wrap: break-word; overflow-wrap: anywhere; }
.pkp-grid tr { page-break-inside: avoid; break-inside: avoid; }
.pkp-label { width: 12%; font-weight: 700; }
.pkp-label .pkp-sub { font-weight: 400; }
.pkp-head { font-weight: 700; color: #00263e; }
.pkp-cellsub { color: #363636; font-size: 8.5px; margin-bottom: 3px; }
.pkp-off { background: #f8f8f8; }
.pkp-out .pkp-head, .pkp-out .pkp-cellsub { color: #8a8a8a; }
.pkp-item { border-left: 3px solid #2563eb; padding: 1px 0 1px 4px; margin: 0 0 3px; }
.pkp-item.pkp-dashed { border-left-style: dashed; }
.pkp-item.pkp-faded { opacity: .4; }
.pkp-item.pkp-hi { background: #eef4fb; }
.pkp-item b { color: #151515; }
.pkp-sub { color: #363636; }
.pkp-mark { display: inline-block; border: 1px solid #dadbdd; padding: 0 3px; font-size: 7.5px; margin: 1px 2px 0 0; }
.pkp-mark-red { border-color: #bd2e2b; color: #bd2e2b; }
.pkp-list tr.pkp-faded { opacity: .4; }
.pkp-empty { color: #363636; font-style: italic; }
.pkp-legend { margin-top: 8px; font-size: 9px; page-break-inside: avoid; }
.pkp-legend b { margin-right: 8px; color: #00263e; }
.pkp-legend span { display: inline-block; margin: 0 12px 3px 0; }
.pkp-legend i { display: inline-block; width: 12px; height: 9px; border-left: 4px solid #64748b; margin-right: 4px; vertical-align: middle; background: #f8f8f8; }
.pkp-notes { margin-top: 4px; font-size: 8.5px; color: #363636; }
.pkp-plain h1 { font-size: 20px; margin: 0 0 4px; color: #00263e; }
.pkp-plain .pkp-meta { font-size: 11px; margin-bottom: 10px; }
`;

const PLAIN_TH = "text-align:left;padding:4px 6px;font-size:10px;font-weight:700;color:#00609c;border-bottom:2px solid #00263e";
const PLAIN_TD = "padding:4px 6px;border-bottom:1px solid #dadbdd;color:#363636";

function mark_html(mark) {
	const entry = typeof mark === "string" ? { text: mark } : mark || {};
	if (!entry.text) return "";
	const tone = entry.tone === "red" ? " pkp-mark-red" : "";
	return `<span class="pkp-mark${tone}">${escape(entry.text)}</span>`;
}

function item_html(item) {
	const classes = ["pkp-item"];
	if (item.dashed) classes.push("pkp-dashed");
	if (item.faded) classes.push("pkp-faded");
	if (item.highlight) classes.push("pkp-hi");
	const color = safe_paint(item.color, "#64748b");
	const marks = (item.marks || []).map(mark_html).join("");
	return (
		`<div class="${classes.join(" ")}" style="border-left-color:${color}">` +
		`<b>${escape(item.title)}</b>` +
		(item.sub ? `<div class="pkp-sub">${escape(item.sub)}</div>` : "") +
		(marks ? `<div>${marks}</div>` : "") +
		"</div>"
	);
}

function cell_html(cell, td) {
	cell = cell || {};
	const classes = [];
	if (cell.tone === "off") classes.push("pkp-off");
	if (cell.tone === "out") classes.push("pkp-out");
	const items = (cell.items || []).map(item_html).join("");
	return (
		`<td${classes.length ? ` class="${classes.join(" ")}"` : ""} style="${escape(td)}">` +
		(cell.head ? `<div class="pkp-head">${escape(cell.head)}</div>` : "") +
		(cell.sub ? `<div class="pkp-cellsub">${escape(cell.sub)}</div>` : "") +
		(items || (cell.empty ? `<span class="pkp-empty">${escape(cell.empty)}</span>` : "")) +
		"</td>"
	);
}

function legend_html(model) {
	const entries = (model.legend || []).filter((entry) => entry && entry.label);
	if (!entries.length) return "";
	const spans = entries
		.map((entry) => {
			const color = safe_paint(entry.color, "#64748b");
			const style = entry.dashed ? "dashed" : "solid";
			return `<span><i style="border-left-color:${color};border-left-style:${style}"></i>${escape(entry.label)}</span>`;
		})
		.join("");
	return `<div class="pkp-legend"><b>${escape(model.legend_title || t("Key"))}</b>${spans}</div>`;
}

// The whole printed page. `model`:
//   { doc_title, heading, lines, chrome: {open, close, th, td} | null,
//     layout: "grid" | "list", columns: [text], label_column: bool,
//     rows: [{ label, sub, cells: [{ head, sub, tone, empty, items: [item] }] }]   (grid)
//     rows: [{ color, faded, cols: [text] }]                                        (list)
//     legend_title, legend: [{ color, label, dashed }], notes: [text], empty: text }
//   item: { title, sub, color, dashed, faded, highlight, marks: [text | {text, tone}] }
// `chrome` comes from the server already built and escaped (the print design system); without it
// (the request failed) the page still prints, under a plain heading.
export function print_document(model) {
	model = model || {};
	const chrome = model.chrome && typeof model.chrome.open === "string" ? model.chrome : null;
	const th = chrome && chrome.th ? chrome.th : PLAIN_TH;
	const td = chrome && chrome.td ? chrome.td : PLAIN_TD;
	const columns = model.columns || [];
	const head = columns.map((label) => `<th style="${escape(th)}">${escape(label)}</th>`).join("");
	const rows = model.rows || [];
	let body = "";
	if (model.layout === "list") {
		body = rows
			.map((row) => {
				const color = safe_paint(row.color, "#64748b");
				const cells = (row.cols || [])
					.map((text, index) => {
						const edge = index === 0 ? `border-left:4px solid ${color};` : "";
						return `<td style="${edge}${escape(td)}">${escape(text)}</td>`;
					})
					.join("");
				return `<tr${row.faded ? ' class="pkp-faded"' : ""}>${cells}</tr>`;
			})
			.join("");
	} else {
		body = rows
			.map((row) => {
				const label = model.label_column
					? `<td class="pkp-label" style="${escape(td)}">${escape(row.label)}${
							row.sub ? `<div class="pkp-sub">${escape(row.sub)}</div>` : ""
					  }</td>`
					: "";
				return `<tr>${label}${(row.cells || []).map((cell) => cell_html(cell, td)).join("")}</tr>`;
			})
			.join("");
	}
	if (!rows.length) {
		body = `<tr><td colspan="${Math.max(1, columns.length)}" style="${escape(td)}">${escape(
			model.empty || t("Nothing to print.")
		)}</td></tr>`;
	}
	const table = `<table class="pkp-grid${model.layout === "list" ? " pkp-list" : ""}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
	const notes = (model.notes || []).filter(Boolean).map((note) => `<div class="pkp-notes">${escape(note)}</div>`).join("");
	const top = chrome
		? chrome.open
		: `<div class="pkp-plain"><h1>${escape(model.heading || model.doc_title || "")}</h1><div class="pkp-meta">${(model.lines || [])
				.map((line) => escape(line))
				.join("<br>")}</div></div>`;
	return (
		"<!doctype html><html><head><meta charset='utf-8'>" +
		`<title>${escape(model.doc_title || model.heading || "")}</title>` +
		`<style>${PRINT_CSS}</style></head><body>` +
		top +
		table +
		legend_html(model) +
		notes +
		(chrome && typeof chrome.close === "string" ? chrome.close : "") +
		"</body></html>"
	);
}

// The legend entries of a fixed palette, for the screen key and the printed one.
export function palette_legend(list) {
	return (list || []).map((entry) => ({ key: entry.key, label: t(entry.label), color: safe_color(entry.color, "#64748b") }));
}
