/**
 * Supplier Return — where a return starts, and where the standard documents say they belong to one.
 *
 * Targets: the Purchase Receipt, Purchase Order, Purchase Invoice and Payment Entry forms, and the
 * Project form (whose Procurement Tracker calls the launcher).
 * Loaded via: hooks.py `doctype_js` for each of those five.
 *
 * v1.807.0 (Nik, 2026-11-12). A return starts from what was received, so nobody retypes a line:
 *
 *  - **Purchase Receipt**: Create > Return to Supplier. With rows ticked in the items grid, only
 *    those lines; otherwise every line that can still go back.
 *  - **Purchase Order**: Create > Return to Supplier. One receipt against the order opens
 *    straight away; several ask which.
 *  - **Procurement Tracker** (Project form): `erpnext_enhancements.supplier_return.start(...)`.
 *
 * Each ends in `frappe.model.open_mapped_doc`, the same call ERPNext's own Create buttons use: an
 * UNSAVED Supplier Return opens with the lines filled at the most that can still go back. Nothing
 * is saved, nothing is emailed and no stock moves until the person does those things on the form.
 * The buttons show only to somebody who may create a Supplier Return (`frappe.model.can_create`),
 * because the mapper performs no permission check of its own and a form that only fails at save
 * loses what was typed; the server checks again (`api/supplier_returns.make_from_receipt`).
 *
 * The other half: a Purchase Return, a Debit Note, a replacement receipt or a refund payment
 * carries no field naming its Supplier Return (the link is kept on the Supplier Return, so the
 * standard doctypes gain no Custom Field). So their forms ask (`api/supplier_returns.linked`) and
 * say so in a line at the top, with a link.
 *
 * The file is listed under five doctypes and frappe evaluates it once per doctype, so everything
 * is registered behind one flag: a second evaluation changes nothing.
 */
frappe.provide("erpnext_enhancements.supplier_return");

(function () {
	const ns = erpnext_enhancements.supplier_return;
	if (ns.registered) return;
	ns.registered = true;

	const API = "erpnext_enhancements.api.supplier_returns.";

	ns.can_start = function () {
		return frappe.model.can_create("Supplier Return");
	};

	/** Open an unsaved Supplier Return for `receipt`. `args`: {filtered_children, purchase_order, item_code}. */
	ns.start_from_receipt = function (receipt, args) {
		frappe.model.open_mapped_doc({
			method: API + "make_from_receipt",
			source_name: receipt,
			args: args || {},
			freeze_message: __("Building the return..."),
		});
	};

	/**
	 * Start from an order, an item or a supplier: find the receipts with something still
	 * returnable, open the only one, or ask which. `opts`: {purchase_order, item_code, supplier}.
	 */
	ns.start = function (opts) {
		const narrow = { purchase_order: opts.purchase_order || null, item_code: opts.item_code || null };
		frappe.call({ method: API + "receipt_choices", args: opts }).then((r) => {
			const data = r.message || {};
			const receipts = data.receipts || [];
			if (!data.allowed) {
				frappe.msgprint(__("You can't start a Supplier Return. Ask purchasing to start it."));
				return;
			}
			if (!receipts.length) {
				frappe.msgprint({
					title: __("Nothing to return"),
					message: opts.purchase_order
						? __(
								"Nothing received on {0} can be returned: either nothing has been received yet, or it has all been returned already. Receive the items first, then return them.",
								[opts.purchase_order]
							)
						: __("No receipt has anything left to return."),
				});
				return;
			}
			if (receipts.length === 1) {
				ns.start_from_receipt(receipts[0].name, narrow);
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Which receipt are the items from?"),
				fields: [
					{
						fieldname: "receipt",
						fieldtype: "Select",
						label: __("Purchase Receipt"),
						reqd: 1,
						options: receipts.map((row) => ({
							value: row.name,
							label: `${row.name} · ${frappe.datetime.str_to_user(row.posting_date)} · ${row.lines
								.map((line) => `${line.returnable} x ${line.item_code}`)
								.join(", ")}`.slice(0, 140),
						})),
						default: receipts[0].name,
					},
				],
				primary_action_label: __("Start Return"),
				primary_action(values) {
					dialog.hide();
					ns.start_from_receipt(values.receipt, narrow);
				},
			});
			dialog.show();
		});
	};

	function selected_rows(frm) {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		return grid ? grid.get_selected() || [] : [];
	}

	function say_linked(frm) {
		if (frm.is_new() || !frappe.model.can_read("Supplier Return")) return;
		frappe.call({ method: API + "linked", args: { doctype: frm.doctype, name: frm.doc.name } }).then((r) => {
			const rows = r.message || [];
			if (!rows.length || !cur_frm || cur_frm.doc.name !== frm.doc.name) return;
			const text = rows
				.map(
					(row) =>
						`${frappe.utils.escape_html(row.role)} <a href="${frappe.utils.get_form_link(
							"Supplier Return",
							row.name
						)}">${frappe.utils.escape_html(row.name)}</a> (${frappe.utils.escape_html(row.status)})`
				)
				.join("; ");
			frm.dashboard.set_headline(__("Supplier Return: {0}", [text]));
		});
	}

	frappe.ui.form.on("Purchase Receipt", {
		refresh(frm) {
			say_linked(frm);
			if (frm.doc.docstatus !== 1 || frm.doc.is_return || !ns.can_start()) return;
			frm.add_custom_button(
				__("Return to Supplier"),
				() => ns.start_from_receipt(frm.doc.name, { filtered_children: selected_rows(frm) }),
				__("Create")
			);
		},
	});

	frappe.ui.form.on("Purchase Order", {
		refresh(frm) {
			if (frm.doc.docstatus !== 1 || !ns.can_start()) return;
			// Received at some point: per_received drops again after a return, so a line's own
			// received or returned quantity is read too.
			const received =
				flt(frm.doc.per_received) > 0 ||
				(frm.doc.items || []).some((row) => flt(row.received_qty) > 0 || flt(row.returned_qty) > 0);
			if (!received) return;
			frm.add_custom_button(
				__("Return to Supplier"),
				() => ns.start({ purchase_order: frm.doc.name }),
				__("Create")
			);
		},
	});

	frappe.ui.form.on("Purchase Invoice", {
		refresh(frm) {
			if (frm.doc.is_return) say_linked(frm);
		},
	});

	frappe.ui.form.on("Payment Entry", {
		refresh(frm) {
			if (frm.doc.payment_type === "Receive" && frm.doc.party_type === "Supplier") say_linked(frm);
		},
	});
})();
