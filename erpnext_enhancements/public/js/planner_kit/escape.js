/*
 * Planner kit: the one escaping helper, and the small text formatters every kit renderer uses.
 *
 * Pure functions, no DOM and no frappe at import time, so tests/test_planner_phase6a.py runs them
 * under plain node. Everything a person typed (a task subject, a site name, a note) goes through
 * `escape` before it becomes HTML; a color goes through `safe_color` before it reaches a style
 * attribute, because colors come from records people edit too.
 */

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };

export function escape(value) {
	return (value === null || value === undefined ? "" : String(value)).replace(/[&<>"'`]/g, (ch) => ENTITIES[ch]);
}

// Only a plain hex value goes into a style attribute.
export function safe_color(value, fallback) {
	return /^#[0-9a-fA-F]{3,8}$/.test(String(value || "")) ? String(value) : fallback || "";
}

// A key as it is written into a data-pk-* attribute: encoded, so it never holds a space or a quote
// and a `~=` attribute selector can match one key in a space-separated list of them.
export function glow_key(value) {
	return encodeURIComponent(value === null || value === undefined ? "" : String(value));
}

export function glow_keys(values) {
	const seen = [];
	(values || []).forEach((value) => {
		const key = glow_key(value);
		if (key && !seen.includes(key)) seen.push(key);
	});
	return seen.join(" ");
}

// The page's own __() when the Desk is there, so the kit's words are translated like the planners';
// plain {0} substitution otherwise (node, a test).
export function t(text, args) {
	const tr = typeof globalThis !== "undefined" && typeof globalThis.__ === "function" ? globalThis.__ : null;
	if (tr) return tr(text, args);
	return String(text).replace(/\{(\d+)\}/g, (match, index) => (args && args[index] != null ? args[index] : match));
}

// 2 -> "2", 2.5 -> "2.5", 1.25 -> "1.25": the engine's fmt_hours, so the drawers print what the
// planners print.
export function hours(value) {
	return String(Math.round((Number(value) || 0) * 100) / 100);
}

// 70 -> "1h 10m", 25 -> "25m", 120 -> "2h": drive time as people say it.
export function drive(minutes) {
	const total = Math.max(0, Math.round(Number(minutes) || 0));
	const h = Math.floor(total / 60);
	const m = total % 60;
	if (!h) return `${m}m`;
	return m ? `${h}h ${m}m` : `${h}h`;
}

// "2026-10-12" -> "Mon, Oct 12" (or "Monday, October 12" with `long`), read as a calendar day with
// no time zone in it, so it is the same day wherever the browser is.
export function day_label(ymd, long) {
	const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ymd || ""));
	if (!match) return String(ymd || "");
	const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
	const options = long
		? { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" }
		: { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };
	try {
		return date.toLocaleDateString("en-US", options);
	} catch (e) {
		return String(ymd);
	}
}

// "2026-10-12" moved by `days` (negative is earlier), as "YYYY-MM-DD".
export function add_days(ymd, days) {
	const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ymd || ""));
	if (!match) return ymd;
	const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + Number(days || 0)));
	return date.toISOString().slice(0, 10);
}

// A tel:/sms:/mailto: link only for a value that is plainly a number or an address; anything else is
// no link at all (the server already normalizes them; this is the second lock on the door).
export function contact_href(kind, value) {
	const text = String(value || "").trim();
	if (kind === "tel" || kind === "sms") return /^\+?\d{7,15}$/.test(text) ? `${kind}:${text}` : "";
	if (kind === "mailto") return /^[^@\s<>"'`]+@[^@\s<>"'`]+\.[^@\s<>"'`]+$/.test(text) ? `mailto:${text}` : "";
	return "";
}
