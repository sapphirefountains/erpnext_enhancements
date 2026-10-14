/*
 * form kit P8.1, the Item flow's name builder: an Item Name assembled from POL-0602's seven
 * segments, and the pick list for each one. Pure: no frappe, no DOM; run under node by
 * tests/test_guided_item.py.
 *
 * The rules are inventory_enhancements/item_naming_rules.py's, and that module stays the judge:
 * the page sends the assembled name to the naming check (api.item_flow.preview_item) and shows
 * what it finds. Here is only what makes the builder produce a name the check would not fault
 * for its punctuation: uppercase, each segment separated by a comma and exactly one space, no
 * empty segment, no comma or doubled space inside a segment, no trailing comma.
 */

// item_naming_rules.SEGMENTS, in order (test_guided_item holds the two equal).
export const SEGMENTS = ["CATEGORY", "SUB-CATEGORY", "KEY FEATURE", "MATERIAL", "SIZE", "RATING", "PACKAGING"];

// How many choices a pick list offers. More than this is a list nobody scrolls on a phone; the
// person types instead, and the field still takes anything.
export const PICK_LIMIT = 40;

function text(value) {
	return value === null || value === undefined ? "" : String(value);
}

// One segment as the schema writes it: uppercase, no comma (a comma would start another segment),
// runs of spaces collapsed, no space at either end.
export function clean_part(value) {
	return text(value).replace(/,/g, " ").replace(/\s+/g, " ").trim().toUpperCase();
}

// The Item Name from the segments: the filled ones, in order, joined by ", ".
export function assemble_name(parts) {
	return (Array.isArray(parts) ? parts : [])
		.slice(0, SEGMENTS.length)
		.map(clean_part)
		.filter(Boolean)
		.join(", ");
}

// An existing name back into seven segments, so the builder can edit it. Positions are kept as
// written; anything past the seventh comma stays in the last segment rather than being lost.
export function split_name(name) {
	const raw = text(name).trim();
	const parts = raw ? raw.split(",").map((part) => part.trim()) : [];
	const out = parts.slice(0, SEGMENTS.length);
	if (parts.length > SEGMENTS.length) {
		out[SEGMENTS.length - 1] = parts.slice(SEGMENTS.length - 1).join(" ").trim();
	}
	while (out.length < SEGMENTS.length) out.push("");
	return out.map(clean_part);
}

// Whether the builder can rebuild `name` without changing it: true when the name already reads as
// its own segments (a name typed by hand that the builder would rewrite is left alone until the
// person edits a segment).
export function builds_same(name) {
	return assemble_name(split_name(name)) === text(name).trim();
}

// The choices for segment `position` (0-based): what the catalog uses there, most used first,
// among names that share the segments chosen so far that matter. CATEGORY narrows everything after
// it; each segment also narrows the ones after it when there are names to narrow to, and falls
// back to the CATEGORY's names when that would leave nothing. CATEGORY itself offers Appendix A's
// approved list (`categories`) first, then any other leading word the catalog uses.
//   catalog: [[segment, ...], ...] from api.item_flow.naming_options
//   chosen:    the seven segments as they stand
export function part_options(catalog, position, chosen, categories) {
	const rows = Array.isArray(catalog) ? catalog : [];
	const picked = (Array.isArray(chosen) ? chosen : []).map(clean_part);
	if (position === 0) {
		const approved = (categories || []).map(clean_part).filter(Boolean);
		const seen = new Set(approved);
		const counts = count_at(rows, 0);
		const extra = counts.filter((value) => !seen.has(value));
		return approved.concat(extra).slice(0, Math.max(PICK_LIMIT, approved.length));
	}
	const category = picked[0];
	let pool = category ? rows.filter((row) => clean_part(row[0]) === category) : rows;
	for (let i = 1; i < position; i += 1) {
		if (!picked[i]) continue;
		const narrower = pool.filter((row) => clean_part(row[i]) === picked[i]);
		if (narrower.length) pool = narrower;
	}
	return count_at(pool, position).slice(0, PICK_LIMIT);
}

// The values used at `position`, most used first, then alphabetical.
function count_at(rows, position) {
	const counts = new Map();
	rows.forEach((row) => {
		const value = clean_part((row || [])[position]);
		if (value) counts.set(value, (counts.get(value) || 0) + 1);
	});
	return Array.from(counts.entries())
		.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
		.map(([value]) => value);
}
