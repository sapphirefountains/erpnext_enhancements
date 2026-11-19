// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * The Schedule of Values form (estimating milestone 2). The lines are the standard table; this script adds
 * the reconciliation panel and the buttons: Lock / Unlock, Add Approved Change Orders, and Start
 * Application N. The server decides everything (schedule_of_values.py, api/estimating.py).
 */

const API = "erpnext_enhancements.api.estimating.";

function money(value) {
	return frappe.format(value || 0, { fieldtype: "Currency" });
}

function esc(value) {
	return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
}

function draw_reconciliation(frm) {
	const wrap = frm.fields_dict.reconcile_html && frm.fields_dict.reconcile_html.$wrapper;
	if (!wrap) return;
	const lines = frm.doc.lines || [];
	const others = lines.filter((r) => r.by_others).length;
	const changes = lines.filter((r) => r.change_order).length;
	// Live, in whole cents, while rows are typed; the server totals them again on save.
	const c = (v) => Math.round((Number(v) || 0) * 100);
	const scheduled = lines.filter((r) => !r.change_order && !r.by_others).reduce((s, r) => s + c(r.scheduled_value), 0);
	const change = lines.filter((r) => r.change_order).reduce((s, r) => s + c(r.scheduled_value), 0);
	const diff = c(frm.doc.contract_sum) - scheduled;
	const row = (label, value, cls = "") =>
		`<tr class="${cls}"><td>${esc(label)}</td><td class="text-right">${value}</td></tr>`;
	wrap.html(`
		<table class="table table-condensed" style="max-width:520px;margin-bottom:8px">
			${row(__("Contract sum signed"), money(frm.doc.contract_sum))}
			${row(__("Sum of scheduled values"), money(scheduled / 100))}
			${row(__("Difference"), money(diff / 100), diff ? "text-danger" : "text-success")}
			${row(__("Rows by others, not billed"), esc(others))}
			${row(__("Approved change orders ({0})", [changes]), money(change / 100))}
			${row(__("Contract sum to date"), `<b>${money((c(frm.doc.contract_sum) + change) / 100)}</b>`)}
			${row(__("Billed to date"), money(frm.doc.billed_to_date))}
		</table>
		<div class="text-muted small">${esc(
			frm.doc.status === "Locked"
				? __("Locked: these lines are the contract breakdown. A change in scope is a change order.")
				: __("A schedule cannot lock while the difference is not zero.")
		)}</div>`);
}

async function call(method, args, message) {
	const r = await frappe.call({ method: API + method, args, freeze: true, freeze_message: message });
	return r && r.message;
}

frappe.ui.form.on("Schedule of Values", {
	refresh(frm) {
		draw_reconciliation(frm);
		const locked = frm.doc.status === "Locked";
		frm.set_df_property("lines", "read_only", locked ? 1 : 0);
		frm.set_df_property("contract_sum", "read_only", locked ? 1 : 0);
		if (frm.is_new()) return;
		const can_write = frm.perm[0] && frm.perm[0].write;

		if (can_write && !locked) {
			frm.add_custom_button(__("Lock"), async () => {
				if (frm.is_dirty()) await frm.save();
				if (frm.is_dirty()) return; // the save was refused; nothing to lock
				await call("lock_schedule", { schedule: frm.doc.name }, __("Locking"));
				frm.reload_doc();
			});
		}
		if (can_write) {
			frm.add_custom_button(
				__("Add Approved Change Orders"),
				async () => {
					if (frm.is_dirty()) await frm.save();
					if (frm.is_dirty()) return;
					const added = await call("add_change_orders", { schedule: frm.doc.name });
					frappe.show_alert({
						message: added ? __("Added {0} change orders.", [added]) : __("No approved change orders to add."),
						indicator: added ? "green" : "blue",
					});
					frm.reload_doc();
				},
				__("Actions")
			);
		}
		if (can_write && locked) {
			frm.add_custom_button(
				__("Unlock"),
				() =>
					frappe.confirm(
						__("Unlock the schedule so its lines can change? Only possible before any application is certified."),
						async () => {
							await call("unlock_schedule", { schedule: frm.doc.name });
							frm.reload_doc();
						}
					),
				__("Actions")
			);
		}
		if (locked && frappe.model.can_create("Pay Application")) {
			frm.add_custom_button(__("Start Pay Application"), async () => {
				const name = await call("start_pay_application", { schedule: frm.doc.name }, __("Starting the application"));
				if (name) frappe.set_route("Form", "Pay Application", name);
			}).addClass("btn-primary");
		}
		frm.add_custom_button(
			__("Pay Applications"),
			() => frappe.set_route("List", "Pay Application", { schedule_of_values: frm.doc.name }),
			__("View")
		);
		if (frm.doc.estimate) {
			frm.add_custom_button(__("Estimate"), () => frappe.set_route("Form", "Estimate", frm.doc.estimate), __("View"));
		}
	},
	contract_sum: draw_reconciliation,
});

frappe.ui.form.on("Schedule of Values Line", {
	scheduled_value: draw_reconciliation,
	lines_remove: draw_reconciliation,
});
