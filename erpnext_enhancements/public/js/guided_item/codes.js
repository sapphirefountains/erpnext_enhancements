/*
 * form kit P8.1, the Item flow's code helper: the four code families of POL-0602 §5 Phase 2, how
 * each is written, and what is wrong with one before it is sent. Pure: no frappe, no DOM; run
 * under node by tests/test_guided_item.py.
 *
 *   vendor       the vendor's part number exactly as printed; only a comma is dropped (SOP §6:
 *                strip the comma, keep every other character, record the original in the
 *                Description)
 *   consumable   CON-<GROUP>-<DESCRIPTOR>[-<SIZE>], GROUP one of ELEC, OFFC, SRV
 *   product      PDT-#### and
 *   service      SRV-###, numbered from the block a person chooses (api.item_flow.code_blocks
 *                lists the blocks in use and each one's lowest free number); never MAX() + 1
 *
 * The constants are copies of inventory_enhancements/item_naming_rules.py's
 * (CONSUMABLE_GROUPS, RESERVED_PREFIXES), held equal by test_guided_item, and classify() is its
 * classify_code_family line for line, so the helper names the family the naming check will.
 */

export const FAMILIES = ["vendor", "consumable", "product", "service"];
export const CONSUMABLE_GROUPS = ["ELEC", "OFFC", "SRV"];
export const RESERVED_PREFIXES = { PDT: 4, SRV: 3 };
export const FAMILY_PREFIX = { product: "PDT", service: "SRV" };

// The longest code Frappe's name column takes (stock_scan_rules.MAX_ITEM_TEXT).
export const MAX_CODE = 140;

function text(value) {
	return value === null || value === undefined ? "" : String(value);
}

// item_naming_rules.classify_code_family: the reserved prefixes claim a code on the prefix alone.
export function classify(code) {
	const value = text(code).trim().toUpperCase();
	if (!value) return "unknown";
	if (value.startsWith("PDT-")) return "product";
	if (value.startsWith("SRV-")) return "service";
	if (value.startsWith("CON-")) return "consumable";
	return "vendor";
}

// A vendor's part number as it goes in: as printed, without commas, spaces at the ends trimmed.
export function vendor_code(value) {
	return text(value).replace(/,/g, "").trim();
}

// One part of a CON- code: uppercase, spaces to hyphens, nothing but letters, digits, hyphens,
// dots, slashes and the inch mark.
function con_part(value) {
	return text(value)
		.toUpperCase()
		.trim()
		.replace(/\s+/g, "-")
		.replace(/[^A-Z0-9\-./"]/g, "")
		.replace(/-+/g, "-")
		.replace(/^-|-$/g, "");
}

export function consumable_code(group, descriptor, size) {
	const parts = ["CON", con_part(group), con_part(descriptor)];
	const extra = con_part(size);
	if (extra) parts.push(extra);
	return parts.join("-");
}

// PDT-0042, SRV-007: the number padded to the family's width.
export function numbered_code(prefix, number) {
	const key = text(prefix).toUpperCase();
	const width = RESERVED_PREFIXES[key];
	const n = Number(number);
	if (!width || !Number.isInteger(n) || n < 0) return "";
	return `${key}-${String(n).padStart(width, "0")}`;
}

// The code a family's inputs make: { family, vendor?, group?, descriptor?, size?, prefix?, number? }.
export function build_code(spec) {
	const s = spec || {};
	if (s.family === "vendor") return vendor_code(s.vendor);
	if (s.family === "consumable") return consumable_code(s.group, s.descriptor, s.size);
	if (s.family === "product" || s.family === "service") return numbered_code(FAMILY_PREFIX[s.family], s.number);
	return "";
}

// Why a family's inputs do not make a usable code, or "" when they do. The naming check on the
// server has the last word (a duplicate is only known there); this catches the shape at once.
export function code_problem(spec) {
	const s = spec || {};
	const code = build_code(s);
	if (s.family === "vendor") {
		if (!vendor_code(s.vendor)) return "Type the vendor's part number as it is printed.";
		if (classify(code) !== "vendor") {
			return "A vendor part number cannot start with PDT-, SRV- or CON-: those are our own families.";
		}
	} else if (s.family === "consumable") {
		if (!CONSUMABLE_GROUPS.includes(con_part(s.group))) {
			return `Choose the group: ${CONSUMABLE_GROUPS.join(", ")}.`;
		}
		if (!con_part(s.descriptor)) return "Say what it is, in a word or two, for the descriptor.";
	} else if (s.family === "product" || s.family === "service") {
		if (!code) return "Choose a block to take the next free number from.";
	} else {
		return "Choose the kind of code.";
	}
	if (code.length > MAX_CODE) return `The code is ${code.length} characters; it can be at most ${MAX_CODE}.`;
	return "";
}
