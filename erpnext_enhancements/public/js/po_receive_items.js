/**
 * Purchase Order — Receive Items: type what arrived, get a real Purchase Receipt.
 *
 * Targets: the "Purchase Order" doctype form.
 * Loaded via: hooks.py `doctype_js["Purchase Order"]`.
 *
 * ER-2026-458194 (TASK-2026-02045). Parker went looking for "the number we received" in
 * ERPNext's Update Items dialog, which is the one place it cannot go: that dialog rewrites
 * the order, and its server half refuses a quantity below what was received. The receipt is
 * a Purchase Receipt. Create > Purchase Receipt already makes one, pre-filled with the whole
 * remaining quantity, on a separate page — so every receipt on this site had been for the
 * whole order, and partial deliveries were recorded by hand as an Order Stage with no
 * per-line figure anywhere.
 *
 * This puts a "Receive Items" button beside Update Items on a submitted, not-yet-fully-
 * received order. It opens one table — Item, Ordered, Received, Arriving now — and posts to
 * `erpnext_enhancements.api.procurement.receive_items`, which builds the receipt with
 * ERPNext's own mapper and submits it. Everything downstream is then ERPNext's:
 * `received_qty` and % Received move, the status pill moves, and `po_order_stage`'s hook
 * sets the Order Stage. Cancelling the receipt undoes all of it.
 *
 * Deliberately no client-side cap at the pending quantity: the server applies the same
 * over-receipt allowance ERPNext would (the Item's own, then Stock Settings'), and a copy
 * of that rule here would either duplicate it or drift from it. The client refuses only what
 * needs no settings to refuse — a negative, and a table that is all zeros.
 *
 * "Arriving now" defaults to what is still pending, so the common case of "the whole rest
 * turned up" is one click, and a partial delivery is typing the smaller numbers.
 */
frappe.provide("erpnext_enhancements.procurement");

(function () {
	const METHOD = "erpnext_enhancements.api.procurement.receive_items";
	// v1.803.0: which open lines are a stock item's with no price. Such a line can't be received
	// until it has one (inventory_enhancements/receipt_price_rules.py), so the dialog asks here.
	const NEEDS_PRICE = "erpnext_enhancements.api.procurement.lines_needing_price";

	function pending(row) {
		// Same arithmetic as erpnext's make_purchase_receipt pre-fill, so the dialog and the
		// receipt it produces cannot disagree about what is left to come.
		return flt(row.qty) - flt(row.received_qty);
	}

	function receivable_rows(frm) {
		// Drop-ship lines go straight from the supplier to the customer and are never
		// received here; erpnext's mapper excludes them too.
		return (frm.doc.items || []).filter((row) => !row.delivered_by_supplier && pending(row) > 0);
	}

	function can_receive(frm) {
		return (
			frm.doc.docstatus === 1 &&
			flt(frm.doc.per_received) < 100 &&
			!["Closed", "On Hold"].includes(frm.doc.status) &&
			frappe.model.can_create("Purchase Receipt") &&
			receivable_rows(frm).length > 0
		);
	}

	function precision(fieldname) {
		const df = frappe.meta.get_docfield("Purchase Order Item", fieldname);
		return df ? df.precision : undefined;
	}

	function receipt_link(name) {
		return frappe.utils.get_form_link("Purchase Receipt", name, true);
	}

	// Opens the dialog once the server has said which lines need a price. If that question fails,
	// the dialog opens without the price columns: the server still refuses an unpriced line, in words.
	erpnext_enhancements.procurement.receive_items = function (frm) {
		let opened = false;
		const open = (answer) => {
			if (opened) return;
			opened = true;
			open_dialog(frm, answer || { lines: [], can_price: false });
		};
		try {
			frappe
				.call({ method: NEEDS_PRICE, type: "GET", args: { purchase_order: frm.doc.name } })
				.then(
					(r) => open(r && r.message),
					() => open(null)
				);
		} catch (e) {
			open(null);
		}
	};

	function price_note(needing, can_price) {
		const esc = frappe.utils.escape_html;
		const rows = needing.map((line) => esc(`${line.idx} (${line.item_code})`)).join(", ");
		const lead =
			needing.length === 1
				? __("Row {0} has no price on this order.", [rows])
				: __("Rows {0} have no price on this order.", [rows]);
		const why = __("Stock comes in at the price on the order, so a line with none can't be received.");
		const how = can_price
			? __(
					"Enter what each one cost in Price each, and it is put on the order. If it was free (a sample or a warranty replacement), tick No charge instead."
			  )
			: __(
					"You can't set prices on this order. Ask purchasing to put the price on it (Update Items), or tick No charge if it was free (a sample or a warranty replacement). Leave the line at 0 to receive the rest."
			  );
		return `<div class="alert alert-warning" style="margin-bottom:var(--margin-md)">${lead} ${esc(
			why
		)} ${esc(how)}</div>`;
	}

	function open_dialog(frm, answer) {
		const needing = (answer.lines || []).filter((line) => line && line.purchase_order_item);
		const needs = new Set(needing.map((line) => line.purchase_order_item));
		const asks = needs.size > 0;
		const rows = receivable_rows(frm).map((row) => ({
			purchase_order_item: row.name,
			item_code: row.item_code,
			item_name: row.item_name,
			ordered: flt(row.qty),
			received: flt(row.received_qty),
			arriving: pending(row),
			price: null,
			no_charge: 0,
		}));
		const price_fields = asks
			? [
					{
						fieldtype: "Currency",
						fieldname: "price",
						label: __("Price each"),
						in_list_view: 1,
						columns: 2,
						read_only: answer.can_price ? 0 : 1,
					},
					{
						fieldtype: "Check",
						fieldname: "no_charge",
						label: __("No charge"),
						in_list_view: 1,
						columns: 1,
					},
			  ]
			: [];
		const wide = asks ? 2 : 3;

		const dialog = new frappe.ui.Dialog({
			title: __("Receive Items"),
			size: asks ? "extra-large" : "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "price_note", hidden: asks ? 0 : 1 },
				{
					fieldname: "lines",
					fieldtype: "Table",
					label: __("What arrived"),
					cannot_add_rows: true,
					cannot_delete_rows: true,
					reqd: 1,
					data: rows,
					get_data: () => rows,
					fields: [
						{ fieldtype: "Data", fieldname: "purchase_order_item", hidden: 1 },
						{
							fieldtype: "Link",
							fieldname: "item_code",
							options: "Item",
							label: __("Item"),
							in_list_view: 1,
							read_only: 1,
							columns: wide,
						},
						{
							fieldtype: "Data",
							fieldname: "item_name",
							label: __("Item Name"),
							in_list_view: 1,
							read_only: 1,
							columns: wide,
						},
						{
							fieldtype: "Float",
							fieldname: "ordered",
							label: __("Ordered"),
							in_list_view: 1,
							read_only: 1,
							columns: 1,
							precision: precision("qty"),
						},
						{
							fieldtype: "Float",
							fieldname: "received",
							label: __("Received"),
							in_list_view: 1,
							read_only: 1,
							columns: 1,
							precision: precision("received_qty"),
						},
						{
							fieldtype: "Float",
							fieldname: "arriving",
							label: __("Arriving now"),
							in_list_view: 1,
							columns: asks ? 1 : 2,
							precision: precision("qty"),
						},
						...price_fields,
					],
				},
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Date",
					fieldname: "posting_date",
					label: __("Received on"),
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
			],
			primary_action_label: __("Receive"),
			primary_action(values) {
				const lines = (values.lines || []).map((line) => {
					const out = { purchase_order_item: line.purchase_order_item, qty: flt(line.arriving) };
					// Only an unpriced stock line's answer is sent; the server ignores any other.
					if (needs.has(line.purchase_order_item)) {
						if (cint(line.no_charge)) out.no_charge = 1;
						else if (flt(line.price) > 0) out.rate = flt(line.price);
					}
					return out;
				});
				if (lines.some((line) => line.qty < 0)) {
					frappe.msgprint(__("A received quantity cannot be negative."));
					return;
				}
				const arriving = lines.filter((line) => line.qty > 0);
				if (!arriving.length) {
					frappe.msgprint(__("Nothing to receive: every line is at 0."));
					return;
				}
				const unanswered = arriving.filter(
					(line) => needs.has(line.purchase_order_item) && !line.no_charge && !line.rate
				);
				if (unanswered.length) {
					frappe.msgprint(
						__(
							"Enter a price, or tick No charge, for each line that has no price on the order. To receive the rest now, set that line to 0."
						)
					);
					return;
				}
				frappe.call({
					method: METHOD,
					args: {
						purchase_order: frm.doc.name,
						rows: arriving,
						posting_date: values.posting_date,
					},
					freeze: true,
					freeze_message: __("Receiving..."),
					callback(r) {
						dialog.hide();
						const receipt = r.message && r.message.purchase_receipt;
						if (receipt) {
							frappe.show_alert(
								{ message: __("Received on {0}", [receipt_link(receipt)]), indicator: "green" },
								8
							);
						}
						frm.reload_doc();
					},
				});
			},
		});
		if (asks) dialog.fields_dict.price_note.$wrapper.html(price_note(needing, !!answer.can_price));
		dialog.show();
	}

	frappe.ui.form.on("Purchase Order", {
		refresh(frm) {
			if (!can_receive(frm)) return;
			// Plain button, beside erpnext's own Update Items: that is where the buyer
			// looked for it (the screenshot on ER-2026-458194), so that is where it goes.
			frm.add_custom_button(__("Receive Items"), () =>
				erpnext_enhancements.procurement.receive_items(frm)
			);
		},
	});
})();
