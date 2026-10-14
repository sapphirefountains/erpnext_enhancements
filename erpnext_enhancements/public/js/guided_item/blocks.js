/*
 * form kit P8.1: the small decisions behind the Item flow's supplier, stock and asset blocks.
 * Pure: no frappe, no DOM; run under node by tests/test_guided_item.py. The blocks themselves
 * are extension.js; what they read and write is api/item_flow.py (its get_setup payload).
 */

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

// The bin's printable QR label: the same page and query the Warehouse form's "QR Label" button
// opens (public/js/warehouse_stock_scan.js, www/warehouse_labels.py), not a label of our own.
export function label_url(warehouse) {
	const name = text(warehouse);
	return name ? `/warehouse-labels?w=${encodeURIComponent(name)}` : "";
}

// An id for one Asset create, minted once and sent again on a retry, so the server can tell a
// resend from a second Asset (api.item_flow.create_asset). The shape item_flow_rules.client_id
// accepts: letters, digits and hyphens, 8 to 64 characters. `random` is Math.random (or a stub).
export function new_client_id(now, random) {
	const time = Number(now || 0).toString(36);
	let tail = "";
	const draw = typeof random === "function" ? random : Math.random;
	while (tail.length < 12) tail += Math.floor(draw() * 36).toString(36);
	return `gi-${time}-${tail.slice(0, 12)}`;
}

// What the supplier block starts from: the default supplier's row, else the first one, else blank.
export function supplier_start(setup) {
	const s = setup || {};
	const rows = Array.isArray(s.suppliers) ? s.suppliers : [];
	const row = rows.find((r) => r.supplier === s.default_supplier) || rows[0] || null;
	return {
		supplier: row ? row.supplier : text(s.default_supplier),
		supplier_part_no: row ? text(row.supplier_part_no) : "",
		purchase_url: row ? text(row.purchase_url) : "",
		rate: row && row.rate !== null && row.rate !== undefined ? row.rate : "",
	};
}

// The supplier's own row from the payload, when the person picks another supplier in the block.
export function supplier_row(setup, supplier) {
	const rows = Array.isArray((setup || {}).suppliers) ? setup.suppliers : [];
	return rows.find((r) => r.supplier === supplier) || null;
}

// The boxes for `supplier`: their own row's details, or blank when the Item has no row for them
// yet. Never the previously picked supplier's, which would be saved under the new one.
export function supplier_fields(setup, supplier) {
	const row = supplier_row(setup, supplier);
	return {
		supplier_part_no: row ? text(row.supplier_part_no) : "",
		purchase_url: row ? text(row.purchase_url) : "",
		rate: row && row.rate !== null && row.rate !== undefined ? row.rate : "",
	};
}

// Whether "reorders go to this supplier" starts ticked: only when the Item has no default supplier
// yet, or `supplier` already is it. Picking a second supplier never takes the default by accident.
export function default_ticked(setup, supplier) {
	const current = text((setup || {}).default_supplier);
	return !current || current === text(supplier);
}

// The rate to send: what was typed, only when it differs from the rate shown for that supplier
// (blank or unchanged sends nothing, so a stale or untouched price is never written again).
export function rate_to_send(shown, typed) {
	if (!given(typed)) return null;
	const value = Number(typed);
	if (!Number.isFinite(value)) return text(typed);
	if (given(shown) && Number(shown) === value) return null;
	return text(typed);
}

// What the stock block starts from: the default bin and its Purchase reorder rule.
export function stock_start(setup) {
	const s = setup || {};
	const warehouse = text(s.default_warehouse);
	const rows = Array.isArray(s.reorder) ? s.reorder : [];
	const rule = rows.find((r) => r.warehouse === warehouse && (r.material_request_type || "Purchase") === "Purchase");
	return {
		warehouse,
		reorder_level: rule ? rule.level : "",
		reorder_qty: rule ? rule.qty : "",
	};
}

// Whether a number box holds something to send: "" and null are "not given", 0 is a value.
export function given(value) {
	return value !== null && value !== undefined && text(value) !== "";
}
