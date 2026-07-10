// Package Dispatch form — live totals, catalog item value + customer address
// auto-fill (only when the Package Dispatch switch is on), a "how much to insure"
// headline, and a one-click "Mark Delivered".
//
// frappe.boot.ee_package_dispatch is set from ERPNext Enhancements Settings (see
// boot.py). When it's 0 the auto-fill calls are skipped entirely — you can still
// type every field by hand — so the form never errors with the feature off.

frappe.ui.form.on("Package Dispatch", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.requested_by) {
			frm.set_value("requested_by", frappe.session.user);
		}
	},

	refresh(frm) {
		render_insure_headline(frm);

		if (frm.doc.docstatus === 1 && frm.doc.shipment_status !== "Delivered") {
			frm.add_custom_button(__("Mark Delivered"), () => {
				frappe.prompt(
					{
						fieldname: "delivered_date",
						label: __("Delivered On"),
						fieldtype: "Date",
						reqd: 1,
						default: frappe.datetime.get_today(),
					},
					(values) => {
						frm.set_value("delivered_date", values.delivered_date).then(() => frm.save());
					},
					__("Mark Delivered")
				);
			});
		}
	},

	ship_to_customer(frm) {
		if (!frm.doc.ship_to_customer || !frappe.boot.ee_package_dispatch) {
			return;
		}
		frappe.call({
			method: "erpnext_enhancements.package_dispatch.api.get_customer_ship_to",
			args: { customer: frm.doc.ship_to_customer },
			callback: (r) => {
				const d = r.message || {};
				Object.keys(d).forEach((field) => frm.set_value(field, d[field]));
			},
		});
	},

	insured_value(frm) {
		render_insure_headline(frm);
	},

	validate(frm) {
		recompute_total(frm);
	},
});

frappe.ui.form.on("Package Dispatch Item", {
	item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item || !frappe.boot.ee_package_dispatch) {
			return;
		}
		frappe.call({
			method: "erpnext_enhancements.package_dispatch.api.get_item_dispatch_details",
			args: { item_code: row.item },
			callback: (r) => {
				const d = r.message || {};
				if (d.description && !row.description) {
					frappe.model.set_value(cdt, cdn, "description", d.description);
				}
				if (d.rate && !flt(row.rate)) {
					frappe.model.set_value(cdt, cdn, "rate", d.rate);
				}
			},
		});
	},

	qty(frm, cdt, cdn) {
		recompute_row(cdt, cdn);
		recompute_total(frm);
	},

	rate(frm, cdt, cdn) {
		recompute_row(cdt, cdn);
		recompute_total(frm);
	},

	items_remove(frm) {
		recompute_total(frm);
	},
});

function recompute_row(cdt, cdn) {
	const row = locals[cdt][cdn];
	const qty = flt(row.qty) || 1;
	frappe.model.set_value(cdt, cdn, "amount", qty * flt(row.rate));
}

function recompute_total(frm) {
	let total = 0;
	(frm.doc.items || []).forEach((row) => {
		const qty = flt(row.qty) || 1;
		row.amount = qty * flt(row.rate);
		total += row.amount;
	});
	frm.set_value("total_declared_value", total);
	if (!flt(frm.doc.insured_value)) {
		frm.set_value("insured_value", total);
	}
	frm.refresh_field("items");
	render_insure_headline(frm);
}

function render_insure_headline(frm) {
	frm.dashboard.clear_headline();
	const value = flt(frm.doc.insured_value) || flt(frm.doc.total_declared_value);
	if (!value) {
		return;
	}
	const money = format_currency(value, frappe.defaults.get_default("currency"));
	frm.dashboard.set_headline(
		`<span class="indicator-pill blue">${__("Insure for")}: <b>${money}</b></span>`
	);
}
