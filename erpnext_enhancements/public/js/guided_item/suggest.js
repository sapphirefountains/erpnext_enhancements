/*
 * The Item flow's Describe it card (ER-2026-1320546, v1.691.0): the small decisions behind the AI
 * suggest step. Pure: no frappe, no DOM; run under node by tests/test_guided_item.py.
 *
 * The server (api/item_suggest.py) answers with rows, one per suggested field. Here is where each
 * row goes when the person accepts it: a name segment into the name builder, the kind of code and
 * its parts into the code helper (codes.js builds the code from them, as when typed), the Item
 * Group and unit into the step's own fields. A product or service suggestion is the family alone:
 * the code helper then lists the blocks, and the number is the one the person picks there.
 * Accept all never takes a row marked `confirm` (a part number read off a photo).
 */

import { SEGMENTS, clean_part } from "./naming.js";
import { classify } from "./codes.js";

// item_suggest_rules.DESCRIPTION_MAX and IMAGE_MAX_BYTES (test_guided_item holds them equal).
export const DESCRIPTION_MAX = 1500;
export const PHOTO_MAX_BYTES = 4 * 1024 * 1024;
// A phone photo is scaled to this many pixels on its long side before it is sent: plenty to read a
// label, and a few hundred KB instead of several MB.
export const PHOTO_MAX_EDGE = 1600;
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"];
// A photo this large is refused before the browser tries to decode it.
export const PHOTO_READ_MAX = 25 * 1024 * 1024;

const CODE_PARTS = { vendor_part_no: "vendor", consumable_group: "group", descriptor: "descriptor", code_size: "size" };
const FIELDS = { item_group: "item_group", stock_uom: "stock_uom" };
const TONES = { OK: "good", FIX: "warn", STOP: "danger", NOTE: "neutral" };

function text(value) {
	return value === null || value === undefined ? "" : String(value);
}

const NAMED_ENTITIES = { nbsp: " ", amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

// The text of a value the card is seeded with: a buying line's description arrives as its Text
// Editor HTML. Tags become spaces and entities characters; the card then puts the result in a
// textarea with .val(), so nothing here is ever markup on the page.
export function untagged(value) {
	return text(value)
		.replace(/<[^>]*>/g, " ")
		.replace(/&#(\d+);/g, (all, code) => (Number(code) < 0x110000 ? String.fromCodePoint(Number(code)) : " "))
		.replace(/&#x([0-9a-f]+);/gi, (all, code) => (parseInt(code, 16) < 0x110000 ? String.fromCodePoint(parseInt(code, 16)) : " "))
		.replace(/&([a-z]+);/gi, (all, name) => (NAMED_ENTITIES[name.toLowerCase()] !== undefined ? NAMED_ENTITIES[name.toLowerCase()] : all))
		.replace(/\s+/g, " ")
		.trim();
}

// Whether a code reads as a part number rather than words: a digit in it and at most one space
// ("825Y-QT 1" is a part; "pump 1/2 hp submersible", typed into a buying line's item box, is not).
export function looks_like_part(code) {
	const value = text(code).trim();
	return !!value && /\d/.test(value) && (value.match(/\s/g) || []).length <= 1;
}

// What the card starts from on a new Item: the description it was started with (a buying line's
// HTML, through route options), else what Item Name holds; and the code as the vendor part number
// when it reads as one. A code that reads as words is the description instead.
export function start_from(values, carried) {
	const v = values || {};
	const c = carried || {};
	const code = text(v.item_code).trim();
	const part = code && classify(code) === "vendor" && looks_like_part(code) ? code : "";
	const description = untagged(c.description) || untagged(v.item_name) || (part ? "" : code);
	return {
		description: description.slice(0, DESCRIPTION_MAX),
		vendor_part_no: part,
	};
}

// Why a chosen file cannot be sent as the photo, or "". The server checks the bytes again.
export function photo_problem(file) {
	const f = file || {};
	const type = text(f.type).toLowerCase();
	if (type && !type.startsWith("image/")) return "The photo must be a JPEG, PNG, WebP or HEIC image.";
	if (Number(f.size) > PHOTO_READ_MAX) return "The photo is too large. Take it again a little further away.";
	return "";
}

// The size to draw a `width` x `height` photo at so its long side is at most `max_edge`.
export function fit_photo(width, height, max_edge) {
	const w = Math.max(0, Math.round(Number(width) || 0));
	const h = Math.max(0, Math.round(Number(height) || 0));
	const edge = Number(max_edge) || PHOTO_MAX_EDGE;
	const long = Math.max(w, h);
	if (!long || long <= edge) return { width: w, height: h, scaled: false };
	const ratio = edge / long;
	return { width: Math.max(1, Math.round(w * ratio)), height: Math.max(1, Math.round(h * ratio)), scaled: true };
}

// How many bytes a base64 data: URL decodes to (near enough to say "420 KB" and to refuse 5 MB).
export function data_url_bytes(url) {
	const body = text(url).replace(/^data:[^,]*,/, "").replace(/\s+/g, "");
	if (!body) return 0;
	const padding = body.endsWith("==") ? 2 : body.endsWith("=") ? 1 : 0;
	return Math.max(0, Math.floor((body.length * 3) / 4) - padding);
}

// Where accepting a row puts its value.
export function target_for(key) {
	if (key === "family") return { kind: "family" };
	if (CODE_PARTS[key]) return { kind: "code", part: CODE_PARTS[key] };
	if (FIELDS[key]) return { kind: "field", fieldname: FIELDS[key] };
	const match = /^segment_(\d)$/.exec(text(key));
	if (match && Number(match[1]) < SEGMENTS.length) return { kind: "segment", index: Number(match[1]) };
	return null;
}

// The server's rows as the card lists them: each with its target, its verdict as a chip tone and
// words, and whether Accept all may take it. A row with no target or no value is left out.
export function rows_for(answer) {
	const rows = Array.isArray((answer || {}).fields) ? answer.fields : [];
	return rows
		.map((row) => {
			const r = row || {};
			const target = target_for(r.key);
			const value = text(r.value).trim();
			if (!target || !value) return null;
			const verdict = r.verdict && r.verdict.severity ? { tone: TONES[r.verdict.severity] || "neutral", severity: text(r.verdict.severity), message: text(r.verdict.message) } : null;
			return {
				key: text(r.key),
				label: text(r.label) || text(r.key),
				value,
				shown: text(r.display) || value,
				reason: text(r.reason),
				note: text(r.note),
				confirm: !!Number(r.confirm || 0),
				verdict,
				target,
			};
		})
		.filter(Boolean);
}

// The code helper's inputs after accepting `value` for `target` (family or a code part). Accepting
// a family starts its inputs from the suggestion's parts and never carries a number across: a PDT
// or SRV number is only ever the block the person picks. A part implies its family.
export function code_spec_after(spec, target, value) {
	const s = Object.assign({}, spec || {});
	delete s.number;
	const t = target || {};
	if (t.kind === "family") {
		s.family = text(value);
		return s;
	}
	if (t.kind !== "code") return s;
	if (t.part === "vendor") s.family = "vendor";
	else s.family = "consumable";
	s[t.part] = text(value);
	return s;
}

// The seven segments after accepting `value` at `index`, cleaned as the builder cleans them.
export function segments_after(parts, index, value) {
	const out = [];
	for (let i = 0; i < SEGMENTS.length; i += 1) out.push(clean_part((parts || [])[i]));
	if (Number.isInteger(index) && index >= 0 && index < SEGMENTS.length) out[index] = clean_part(value);
	return out;
}

// The rows Accept all takes, in the order that builds the code before its parts: the family first,
// then the code's parts, the segments, and the step's fields. Never a row marked `confirm`.
export function accept_all_order(rows) {
	const rank = { family: 0, code: 1, segment: 2, field: 3 };
	return (rows || [])
		.filter((row) => row && row.target && !row.confirm)
		.slice()
		.sort((a, b) => rank[a.target.kind] - rank[b.target.kind] || (a.target.index || 0) - (b.target.index || 0));
}
