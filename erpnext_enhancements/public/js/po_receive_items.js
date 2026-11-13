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
 *
 * "Put in" (v1.817.0, Nik 2026-11-12) is where each line goes. It opens on the line's default
 * place -- the Project Shelf for a job's line, the item's own bin for an Overhead one
 * (`procurement_project.receiving_defaults`) -- and the person receiving can pick any other
 * location per line. The dialog still opens if that lookup fails: each line then shows the
 * order line's own warehouse, which is what a receipt used before.
 */
frappe.provide("erpnext_enhancements.procurement");

(function () {
	const METHOD = "erpnext_enhancements.api.procurement.receive_items";
	// v1.809.0: which open lines are a stock item's with no price. Such a line can't be received
	// until it has one (inventory_enhancements/receipt_price_rules.py), so the dialog asks here.
	const NEEDS_PRICE = "erpnext_enhancements.api.procurement.lines_needing_price";
	// v1.817.0: where each line goes by default (the Project Shelf for a job's line, the item's own
	// bin for an Overhead one). Asked after the price question; the dialog opens without it too.
	const DEFAULTS = "erpnext_enhancements.procurement_project.receiving_defaults";

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

	/** Where each line goes by default. Never rejects: `{}` when the lookup fails, and each line then shows the order's own warehouse. */
	function receiving_defaults(frm) {
		try {
			return frappe
				.call({ method: DEFAULTS, args: { purchase_order: frm.doc.name } })
				.then(
					(r) => (r && r.message) || {},
					() => ({})
				);
		} catch (e) {
			return Promise.resolve({});
		}
	}

	/** One sentence under the table when a line is not going where the order said. */
	function moved_note(defaults, rows) {
		const lines = (defaults && defaults.lines) || {};
		const moved = rows.filter((row) => (lines[row.purchase_order_item] || {}).changed);
		if (!moved.length || !defaults.shelf_name) return "";
		return __(
			"{0} of these lines were ordered for {1} on an Overhead project. They are set to go to the item's own bin instead; change any of them here.",
			[moved.length, frappe.utils.escape_html(defaults.shelf_name)]
		);
	}

	// Opens the dialog once the server has said which lines need a price. If that question fails,
	// the dialog opens without the price columns: the server still refuses an unpriced line, in words.
	// Where each line goes is asked next and never holds the dialog back either.
	erpnext_enhancements.procurement.receive_items = function (frm) {
		let opened = false;
		const open = (answer) => {
			if (opened) return;
			opened = true;
			receiving_defaults(frm).then((defaults) =>
				open_dialog(frm, answer || { lines: [], can_price: false }, defaults || {})
			);
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

	// What the dialog says above the table. Three people: one who may price the order (a price or
	// "no charge" with a reason), one who may only mark a line free (if the site allows that), and one
	// who can do neither. For the last, the server leaves those lines unreceived and asks purchasing
	// with a ToDo; the dialog says so before Receive is pressed, and never tells them to tick anything.
	function price_note(needing, answer) {
		const esc = frappe.utils.escape_html;
		const rows = needing.map((line) => esc(`${line.idx} (${line.item_code})`)).join(", ");
		const lead =
			needing.length === 1
				? __("Row {0} has no price on this order.", [rows])
				: __("Rows {0} have no price on this order.", [rows]);
		const why = __("Stock comes in at the price on the order, so a line with none can't be received.");
		let how;
		if (answer.can_price) {
			how = __(
				"Enter what each one cost in Price each, and it is put on the order. If it was free (a sample or a warranty replacement), tick No charge and say why below."
			);
		} else if (answer.can_mark_free) {
			how =
				(answer.why || "") +
				" " +
				__(
					"If it was free (a sample or a warranty replacement), tick No charge and say why below. Otherwise it is left for purchasing, who will be asked when you press Receive."
				);
		} else {
			how =
				(answer.why || "") +
				" " +
				__(
					"Those lines are left unreceived, and purchasing is asked for the price when you press Receive. Everything else is received as usual. Set the goods aside until the order has a price."
				);
		}
		return `<div class="alert alert-warning" style="margin-bottom:var(--margin-md)">${lead} ${esc(
			why
		)} ${esc(how)}</div>`;
	}

	function open_dialog(frm, answer, defaults) {
		const needing = (answer.lines || []).filter((line) => line && line.purchase_order_item);
		const needs = new Set(needing.map((line) => line.purchase_order_item));
		const asks = needs.size > 0;
		const can_price = asks && !!answer.can_price;
		const can_free = asks && !!answer.can_mark_free;
		const reason_required = answer.reason_required !== false;
		const places = (defaults && defaults.lines) || {};
		const rows = receivable_rows(frm).map((row) => ({
			purchase_order_item: row.name,
			item_code: row.item_code,
			item_name: row.item_name,
			ordered: flt(row.qty),
			received: flt(row.received_qty),
			arriving: pending(row),
			price: null,
			no_charge: 0,
			warehouse: (places[row.name] || {}).warehouse || row.warehouse || "",
		}));
		const note = moved_note(defaults, rows);
		// A column only for what this person may do. Someone who can neither price nor mark a line
		// free gets no extra column at all: there is nothing for them to fill in.
		const price_fields = [];
		if (can_price) {
			price_fields.push({
				fieldtype: "Currency",
				fieldname: "price",
				label: __("Price each"),
				in_list_view: 1,
				columns: 2,
			});
		}
		if (can_free) {
			price_fields.push({
				fieldtype: "Check",
				fieldname: "no_charge",
				label: __("No charge"),
				in_list_view: 1,
				columns: 1,
			});
		}
		const extra = (can_price ? 2 : 0) + (can_free ? 1 : 0);
		const wide = extra ? 2 : 3;

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
							columns: extra === 3 ? 1 : 2,
							precision: precision("qty"),
						},
						...price_fields,
						{
							fieldtype: "Link",
							fieldname: "warehouse",
							options: "Warehouse",
							label: __("Put in"),
							in_list_view: 1,
							// On Frappe 16.56.0 a grid column's `columns` is a pixel width (3 is 140px), not
							// a share of ten, so a column more does not push another out of the list.
							columns: 3,
							get_query: () => ({
								filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
							}),
						},
					],
				},
				{ fieldtype: "HTML", fieldname: "moved_note", options: note ? `<p class="text-muted small">${note}</p>` : "" },
				{
					fieldtype: "Data",
					fieldname: "no_charge_reason",
					label: __("Why no charge"),
					description: __("For the lines ticked No charge: a free sample, a warranty replacement."),
					hidden: can_free ? 0 : 1,
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
					const out = {
						purchase_order_item: line.purchase_order_item,
						qty: flt(line.arriving),
						// Where this line is put (v1.817.0). A line the server holds back for a price
						// is not received, so the place sent for it is not used.
						warehouse: line.warehouse || "",
					};
					// Only an unpriced stock line's answer is sent; the server ignores any other.
					if (needs.has(line.purchase_order_item)) {
						if (can_free && cint(line.no_charge)) out.no_charge = 1;
						else if (can_price && flt(line.price) > 0) out.rate = flt(line.price);
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
				// Only a person who may price is held to an answer here. For anyone else the server
				// leaves the line unreceived and asks purchasing; that is not something to fix in the dialog.
				const unanswered = can_price
					? arriving.filter((line) => needs.has(line.purchase_order_item) && !line.no_charge && !line.rate)
					: [];
				if (unanswered.length) {
					frappe.msgprint(
						__(
							"Enter a price, or tick No charge, for each line that has no price on the order. To receive the rest now, set that line to 0."
						)
					);
					return;
				}
				const reason = (values.no_charge_reason || "").trim();
				if (reason_required && !reason && arriving.some((line) => line.no_charge)) {
					frappe.msgprint(__("Say why it was no charge (a free sample, a warranty replacement)."));
					return;
				}
				frappe.call({
					method: METHOD,
					args: {
						purchase_order: frm.doc.name,
						rows: arriving,
						posting_date: values.posting_date,
						no_charge_reason: reason || undefined,
					},
					freeze: true,
					freeze_message: __("Receiving..."),
					callback(r) {
						dialog.hide();
						const out = r.message || {};
						const receipt = out.purchase_receipt;
						if (receipt) {
							frappe.show_alert(
								{ message: __("Received on {0}", [receipt_link(receipt)]), indicator: "green" },
								8
							);
						}
						// Lines left unreceived for want of a price: the server's own sentence says which,
						// why, and who was asked. A dialog of its own, so it is read, not missed.
						if (out.message) {
							frappe.msgprint({
								title: receipt ? __("Some lines were not received") : __("Nothing was received"),
								message: frappe.utils.escape_html(out.message),
								indicator: "orange",
							});
						}
						frm.reload_doc();
					},
				});
			},
		});
		if (asks) dialog.fields_dict.price_note.$wrapper.html(price_note(needing, answer));
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
