/*
 * ER-2026-520743: what a buying grid row already says about an Item that does not exist yet.
 *
 * Someone filling in a Purchase Order types the description, the unit and the price into a row,
 * then types a code nobody has made yet into Item and picks "Create a new Item". Frappe's Link
 * control carries only the typed code across (`route_options.name_field`, which
 * `frappe.model.get_new_doc` writes into Item's `field:item_code` autoname). Everything else
 * they typed stayed on the row and had to be typed again. `new_item_prefill.js` hands what this
 * module returns to the Link control's `get_route_options_for_new_doc` hook instead.
 *
 * The keys, and who reads them:
 *
 *   item_code, item_name, description, stock_uom   Item fields. `get_new_doc` copies a route
 *                                                  option onto the new doc only when the doctype
 *                                                  has that field, so these land on the Desk's
 *                                                  Item Quick Entry (and in the full form).
 *   uom, rate, supplier                            Not Item fields, so the Desk drops them. The
 *                                                  form kit's guided Item flow (P8.1) reads
 *                                                  `frappe.route_options` itself and uses them
 *                                                  for its supplier-and-price step.
 *
 * Only a row that is being filled in for the new item gives its details. A row that already
 * holds an Item, where someone retypes the code, still carries that other Item's fetched name,
 * description and unit; copying those would describe the wrong part, so such a row gives the
 * typed code and the order's supplier and nothing else.
 *
 * Pure: no frappe, no DOM. Run under node by scripts/test_new_item_prefill.mjs.
 */

// The buying documents whose `items` grid gets the hook: every one where a person types a
// part in by hand. Selling documents are left alone; their rows describe what is sold.
export const BUYING_DOCTYPES = [
	"Material Request",
	"Request for Quotation",
	"Supplier Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
];

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

// True when a Text Editor value holds no words: "", "<div><br></div>", "<p>&nbsp;</p>".
// Only a test for emptiness; the description itself travels exactly as it was written. A typed
// non-breaking space needs no pattern of its own: String.prototype.trim() removes U+00A0.
export function is_blank_html(value) {
	return !text(value)
		.replace(/<[^>]*>/g, "")
		.replace(/&nbsp;|&#160;/gi, "")
		.trim();
}

// The route options for a new Item made from `row` of the buying document `parent`, where the
// person typed `typed` into the row's Item field. Empty values are left out, so nothing typed
// on the row ever overwrites an Item default with a blank.
export function new_item_values(row, parent, typed) {
	const r = row || {};
	const p = parent || {};
	const out = {};

	const code = text(typed) || text(r.item_code);
	if (code) out.item_code = code;

	const supplier = text(p.supplier);
	if (supplier) out.supplier = supplier;

	const held = text(r.item_code);
	if (held && held !== code) return out;

	// The naming guard refuses an Item whose name is just its code, so a name that only
	// repeats the code is left for the person to write.
	const name = text(r.item_name);
	if (name && name.toUpperCase() !== code.toUpperCase()) out.item_name = name;

	if (!is_blank_html(r.description)) out.description = r.description;

	const uom = text(r.uom);
	const stock_uom = text(r.stock_uom) || uom;
	if (stock_uom) out.stock_uom = stock_uom;
	if (uom) out.uom = uom;

	const rate = Number(r.rate);
	if (Number.isFinite(rate) && rate > 0) out.rate = rate;

	return out;
}
