// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The JavaScript twin of erpnext_enhancements/utils/normalize.py — same API, same rules,
// same results. Both are held to one case file, tests/fixtures/normalize_cases.json, by
// tests/test_normalize.py, which runs this module under node; change a rule here without
// changing it there (or the reverse) and that suite goes red. docs/data-normalization.md
// is the prose version.
//
// An ES module with no dependencies and no DOM, so node can import it as it is. The Desk
// reaches it through desk_display.js (form kit P2-B), which erpnext_enhancements.bundle.js
// imports.
//
// Results are plain objects: { value, changed, ok, reason }. ok=false means a person must
// look, and value is then the input exactly as given.
//
// Dates are parsed by hand. new Date("2026-10-14") parses as UTC midnight and shows the
// 13th anywhere west of Greenwich; the server's strings are site-local, so nothing here
// converts a time zone.

function as_text(raw) {
	return raw === null || raw === undefined ? "" : String(raw);
}

function ok(raw, value) {
	return { value, changed: value !== as_text(raw), ok: true, reason: "" };
}

function fail(raw, reason) {
	return { value: as_text(raw), changed: false, ok: false, reason };
}

function digits_of(text) {
	return text.replace(/[^0-9]/g, "");
}

// Python's str.strip() and JavaScript's trim() agree on every whitespace character a
// person types; that is all this needs.
function strip(text) {
	return text.trim();
}

// ---------------------------------------------------------------------------
// Phones

const EXTENSION =
	/^([\s\S]*?)\s*(?:,|#|(?:[,;]\s*)?(?<![A-Za-z])(?:extension|ext|x)[.:]?)\s*([0-9]{1,6})\s*$/i;
const LONG_EXTENSION = /(?:#|(?<![A-Za-z])(?:extension|ext|x)[.:]?)\s*[0-9]{7,}\s*$/i;
const NANP_SHAPE = /^(?:1[-. ]?)?\(?[0-9]{3}\)?[-. ]*[0-9]{3}[-. ]?[0-9]{4}$/;
// Unicode dashes (U+2010 to U+2015, U+2212) and spaces (no-break, narrow, fixed-width,
// ideographic) read as "-" and " " for parsing only; a not-ok value is still returned as typed.
const UNICODE_DASHES = /[\u2010-\u2015\u2212]/g;
const UNICODE_SPACES = /[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g;
// A pasted tel: link, with or without //.
const TEL_PREFIX = /^tel:(?:\/\/)?/i;
const INTL_CHARS = /^[0-9\s().\-]+$/;

export const PHONE_MAX_LENGTH = 20;

function stored(raw, main, ext) {
	const value = main + (ext ? "," + ext : "");
	if (value.length > PHONE_MAX_LENGTH) {
		return fail(raw, "too long to store with its extension (20 characters at most)");
	}
	return ok(raw, value);
}

function nanp(raw, national, ext) {
	if (national[0] === "0" || national[0] === "1") {
		return fail(raw, "area code cannot start with 0 or 1");
	}
	return stored(raw, "+1" + national, ext);
}

function two_numbers(main) {
	return digits_of(main).length >= 14 && /[/;&,]|\bor\b|\band\b/i.test(main);
}

export function phone(raw) {
	const text = strip(
		strip(as_text(raw).replace(UNICODE_DASHES, "-").replace(UNICODE_SPACES, " ")).replace(TEL_PREFIX, "")
	);
	if (!text) return ok(raw, "");

	let main = text;
	let ext = "";
	const m = EXTENSION.exec(text);
	if (m) {
		main = strip(m[1]);
		ext = m[2];
		if (!main) return fail(raw, "an extension with no phone number");
	}

	if (two_numbers(main)) return fail(raw, "two numbers in one field");
	if (LONG_EXTENSION.test(main)) return fail(raw, "an extension longer than six digits");
	if (/[A-Za-z]/.test(main)) return fail(raw, "letters in the number");

	if (main.startsWith("+")) {
		const rest = main.slice(1);
		if (rest.replace(/ /g, "").includes("(0)")) {
			return fail(raw, "a (0) trunk prefix inside an international number");
		}
		if (!INTL_CHARS.test(rest)) return fail(raw, "not a phone number");
		const d = digits_of(rest);
		if (d.startsWith("1")) {
			if (d.length !== 11) return fail(raw, "+1 must be followed by exactly ten digits");
			return nanp(raw, d.slice(1), ext);
		}
		if (d.length < 8 || d.length > 15) {
			return fail(raw, "wrong number of digits for an international number");
		}
		return stored(raw, "+" + d, ext);
	}

	if (NANP_SHAPE.test(main)) {
		let d = digits_of(main);
		if (d.length === 11 && d.startsWith("1")) d = d.slice(1);
		if (d.length === 10) return nanp(raw, d, ext);
	}
	if (digits_of(main).length === 7) return fail(raw, "no area code");
	return fail(raw, "not a North American number and no +country code");
}

export function phone_display(value) {
	const result = phone(value);
	if (!result.ok) return as_text(value);
	if (!result.value) return "";
	const comma = result.value.indexOf(",");
	const main = comma === -1 ? result.value : result.value.slice(0, comma);
	const ext = comma === -1 ? "" : result.value.slice(comma + 1);
	const shown =
		main.startsWith("+1") && main.length === 12
			? `(${main.slice(2, 5)}) ${main.slice(5, 8)}-${main.slice(8)}`
			: main;
	return shown + (ext ? ` x${ext}` : "");
}

// ---------------------------------------------------------------------------
// Email

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function email(raw) {
	const text = strip(as_text(raw));
	if (!text) return ok(raw, "");
	if (text.includes("<") || text.includes(">")) {
		return fail(raw, "a name and an address in one field");
	}
	let candidate = strip(text.replace(/^mailto:/i, ""));
	candidate = candidate.replace(/[,;\s]+$/, "");
	const parts = candidate.split(/[,;\s]+/).filter(Boolean);
	const ats = (candidate.match(/@/g) || []).length;
	if (ats > 1 || parts.filter((p) => p.includes("@")).length > 1) {
		return fail(raw, "two addresses in one field");
	}
	if (parts.length > 1) return fail(raw, "spaces inside the address");
	const value = candidate.toLowerCase();
	if (!EMAIL.test(value)) return fail(raw, "not an email address");
	return ok(raw, value);
}

// ---------------------------------------------------------------------------
// Website

export const WEBSITE_EXEMPT_PREFIXES = ["/", "#", "mailto:", "tel:"];
const URL_PARTS = /^([A-Za-z][A-Za-z0-9+.\-]*):\/\/([^/?#]*)([\s\S]*)$/;

function exempt(text) {
	return WEBSITE_EXEMPT_PREFIXES.some((prefix) => text.startsWith(prefix));
}

export function add_scheme(value) {
	if (typeof value !== "string") return null;
	const stripped = strip(value);
	if (!stripped) return null;
	if (stripped.includes("://") || exempt(stripped)) return null;
	return "https://" + stripped;
}

export function website(raw) {
	const text = strip(as_text(raw));
	if (!text) return ok(raw, "");
	if (exempt(text)) return ok(raw, text);
	if (/\s/.test(text)) return fail(raw, "spaces inside the address");
	if (!text.includes("://") && text.includes("@") && !text.includes("/")) {
		return fail(raw, "an email address, not a website");
	}
	const with_scheme = add_scheme(text) || text;
	const m = URL_PARTS.exec(with_scheme);
	if (!m) return fail(raw, "not a web address");
	const [, scheme, netloc, rest] = m;
	const at = netloc.lastIndexOf("@");
	const userinfo = at === -1 ? "" : netloc.slice(0, at + 1);
	const host = at === -1 ? netloc : netloc.slice(at + 1);
	if (!host.includes(".")) return fail(raw, "not a web address");
	return ok(raw, `${scheme.toLowerCase()}://${userinfo}${host.toLowerCase()}${rest}`);
}

// ---------------------------------------------------------------------------
// Text, states, ZIP codes

export function text(raw) {
	let value = strip(as_text(raw).replace(/\s+/g, " "));
	value = value.replace(/[\s,]+$/, "");
	return ok(raw, value);
}

const US_COUNTRIES = new Set(["", "united states", "united states of america", "usa", "us", "u.s.", "u.s.a."]);

export function is_us(country) {
	return US_COUNTRIES.has(strip(as_text(country)).toLowerCase());
}

export const US_STATES = {
	ALABAMA: "AL", ALASKA: "AK", ARIZONA: "AZ", ARKANSAS: "AR", CALIFORNIA: "CA",
	COLORADO: "CO", CONNECTICUT: "CT", DELAWARE: "DE", FLORIDA: "FL", GEORGIA: "GA",
	HAWAII: "HI", IDAHO: "ID", ILLINOIS: "IL", INDIANA: "IN", IOWA: "IA",
	KANSAS: "KS", KENTUCKY: "KY", LOUISIANA: "LA", MAINE: "ME", MARYLAND: "MD",
	MASSACHUSETTS: "MA", MICHIGAN: "MI", MINNESOTA: "MN", MISSISSIPPI: "MS",
	MISSOURI: "MO", MONTANA: "MT", NEBRASKA: "NE", NEVADA: "NV", "NEW HAMPSHIRE": "NH",
	"NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
	"NORTH DAKOTA": "ND", OHIO: "OH", OKLAHOMA: "OK", OREGON: "OR", PENNSYLVANIA: "PA",
	"RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", TENNESSEE: "TN",
	TEXAS: "TX", UTAH: "UT", VERMONT: "VT", VIRGINIA: "VA", WASHINGTON: "WA",
	"WEST VIRGINIA": "WV", WISCONSIN: "WI", WYOMING: "WY",
	"DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC",
	"PUERTO RICO": "PR", GUAM: "GU", "AMERICAN SAMOA": "AS",
	"NORTHERN MARIANA ISLANDS": "MP", "VIRGIN ISLANDS": "VI", "US VIRGIN ISLANDS": "VI",
	"ARMED FORCES AMERICAS": "AA", "ARMED FORCES EUROPE": "AE", "ARMED FORCES PACIFIC": "AP",
};
const US_STATE_CODES = new Set(Object.values(US_STATES));

export function state(raw, country = null) {
	const cleaned = text(raw);
	if (!is_us(country) || !cleaned.value) return cleaned;
	const key = strip(cleaned.value.replace(/\./g, "").replace(/,/g, " ").replace(/\s+/g, " ")).toUpperCase();
	const compact = key.replace(/ /g, "");
	if (compact.length === 2 && US_STATE_CODES.has(compact)) return ok(raw, compact);
	if (Object.prototype.hasOwnProperty.call(US_STATES, key)) return ok(raw, US_STATES[key]);
	return fail(raw, "not a US state or territory");
}

const ZIP = /^([0-9]{5})(?:[-\s]?([0-9]{4}))?$/;

export function zip_code(raw, country = null) {
	const cleaned = text(raw);
	if (!is_us(country) || !cleaned.value) return cleaned;
	const m = ZIP.exec(cleaned.value);
	if (m) return ok(raw, m[1] + (m[2] ? "-" + m[2] : ""));
	if (/^[0-9]{4}$/.test(cleaned.value)) {
		return fail(raw, "four digits: a leading zero may have been lost");
	}
	return fail(raw, "not a US ZIP code");
}

// ---------------------------------------------------------------------------
// Dates and times — display only.

const DATE_STR =
	/^\s*([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?)?\s*$/;
const TIME_STR = /^\s*(?:-?[0-9]+ days?, )?([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?\s*$/;

function valid_day(year, month, day) {
	if (year < 1 || month < 1 || month > 12 || day < 1) return false;
	// Day 0 of the next month is the last day of this one; UTC, so no time zone moves it.
	return day <= new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function parse_date_str(value) {
	const m = DATE_STR.exec(value);
	if (!m) return null;
	const year = Number(m[1]);
	const month = Number(m[2]);
	const day = Number(m[3]);
	if (!valid_day(year, month, day)) return null;
	const date = { year, month, day };
	if (m[4] === undefined) return { date, clock: null };
	const hour = Number(m[4]);
	const minute = Number(m[5]);
	if (hour > 23 || minute > 59) return null;
	return { date, clock: [hour, minute] };
}

function pad(n, width) {
	return String(n).padStart(width, "0");
}

function date_text(d) {
	return `${pad(d.month, 2)}/${pad(d.day, 2)}/${pad(d.year, 4)}`;
}

function clock(hour, minute) {
	return `${hour % 12 || 12}:${pad(minute, 2)} ${hour < 12 ? "AM" : "PM"}`;
}

function from_js_date(value) {
	return { year: value.getFullYear(), month: value.getMonth() + 1, day: value.getDate() };
}

function is_js_date(value) {
	return value instanceof Date && !Number.isNaN(value.getTime());
}

export function format_date(value) {
	if (value === null || value === undefined || value === "") return "";
	if (is_js_date(value)) return date_text(from_js_date(value));
	const parsed = parse_date_str(String(value));
	return parsed ? date_text(parsed.date) : String(value);
}

export function format_datetime(value) {
	if (value === null || value === undefined || value === "") return "";
	if (is_js_date(value)) {
		return `${date_text(from_js_date(value))} ${clock(value.getHours(), value.getMinutes())}`;
	}
	const parsed = parse_date_str(String(value));
	if (!parsed) return String(value);
	return date_text(parsed.date) + (parsed.clock ? ` ${clock(...parsed.clock)}` : "");
}

export function format_time(value) {
	if (value === null || value === undefined || value === "") return "";
	if (is_js_date(value)) return clock(value.getHours(), value.getMinutes());
	const text_value = String(value);
	const parsed = parse_date_str(text_value);
	if (parsed) return parsed.clock ? clock(...parsed.clock) : text_value;
	const m = TIME_STR.exec(text_value);
	if (!m) return text_value;
	const hour = Number(m[1]);
	const minute = Number(m[2]);
	if (hour > 23 || minute > 59) return text_value;
	return clock(hour, minute);
}

// ---------------------------------------------------------------------------
// Dispatch

export const KINDS = { phone, email, website, text, state, zip: zip_code };
const COUNTRY_KINDS = new Set(["state", "zip"]);

export function normalize(kind, raw, country = null) {
	const fn = Object.prototype.hasOwnProperty.call(KINDS, kind) ? KINDS[kind] : null;
	if (!fn) throw new Error(`unknown normalization kind ${JSON.stringify(kind)}`);
	return COUNTRY_KINDS.has(kind) ? fn(raw, country) : fn(raw);
}
