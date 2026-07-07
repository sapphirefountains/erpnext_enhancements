// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Live configurator UX: picking options re-prices in memory on the server (the
// same engine that runs on save) and renders the part number + module
// breakdown into the Live Preview block. Generation buttons are gated by the
// Product Configurator master switch (frappe.boot.ee_product_configurator).

const PC_API = "erpnext_enhancements.product_configurator.api.configurator.";

frappe.ui.form.on("Product Configuration", {
	refresh(frm) {
		pc_render_preview_from_doc(frm);

		if (!frappe.boot.ee_product_configurator) {
			frm.set_intro(
				__(
					"The Product Configurator's ERPNext generation is switched off " +
						"(ERPNext Enhancements Settings → Product Configurator). " +
						"Configurations and pricing still work; Item/BOM generation is disabled."
				),
				"orange"
			);
		}

		if (frm.is_new()) return;

		if (frappe.boot.ee_product_configurator) {
			frm.add_custom_button(
				__("Item + BOM + Selling Price"),
				() => pc_generate(frm),
				__("Create")
			);
			if (frm.doc.bom) {
				frm.add_custom_button(__("Update BOM Cost"), () => pc_generate(frm), __("Create"));
			}
		}

		[
			["Build Sheet", "Product Configuration - Build Instructions"],
			["QC Checklist", "Product Configuration - QC Checklist"],
			["Pricing Summary", "Product Configuration - Pricing Summary"],
		].forEach(([label, format]) => {
			frm.add_custom_button(
				__(label),
				() =>
					window.open(
						`/printview?doctype=${encodeURIComponent(frm.doc.doctype)}` +
							`&name=${encodeURIComponent(frm.doc.name)}` +
							`&format=${encodeURIComponent(format)}&no_letterhead=1`
					),
				__("View")
			);
		});

		if (frm.doc.item) {
			frm.dashboard.set_headline(
				__("Generated: {0} · {1}", [
					frappe.utils.get_form_link("Item", frm.doc.item, true),
					frm.doc.bom
						? frappe.utils.get_form_link("BOM", frm.doc.bom, true)
						: __("no BOM"),
				])
			);
		}
	},

	product(frm) {
		if (!frm.doc.product) return;
		frappe.call({
			method: PC_API + "get_product_options",
			args: { product: frm.doc.product },
			callback(r) {
				if (!r.message) return;
				frm.clear_table("options");
				(r.message.rows || []).forEach((row) => frm.add_child("options", row));
				frm.refresh_field("options");
				pc_preview(frm);
			},
		});
	},

	additional_cost(frm) {
		pc_preview(frm);
	},
});

frappe.ui.form.on("Product Configuration Option", {
	selected(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.selected && row.option_type === "Choice") {
			// one choice per group — untick siblings client-side (server re-validates)
			(frm.doc.options || []).forEach((sibling) => {
				if (
					sibling.name !== row.name &&
					sibling.option_key === row.option_key &&
					sibling.selected
				) {
					frappe.model.set_value(sibling.doctype, sibling.name, "selected", 0);
				}
			});
		}
		pc_preview(frm);
	},
	qty(frm) {
		pc_preview(frm);
	},
});

const pc_preview = frappe.utils.debounce((frm) => {
	if (!frm.doc.product || !(frm.doc.options || []).length) return;
	frappe.call({
		method: PC_API + "preview_configuration",
		args: {
			payload: {
				product: frm.doc.product,
				options: (frm.doc.options || []).map((row) => ({
					option_key: row.option_key,
					option_type: row.option_type,
					choice_code: row.choice_code,
					selected: row.selected,
					qty: row.qty,
				})),
				additional_description: frm.doc.additional_description,
				additional_cost: frm.doc.additional_cost,
			},
		},
		callback(r) {
			if (r.message) pc_render_preview(frm, r.message);
		},
	});
}, 300);

function pc_render_preview_from_doc(frm) {
	if (!frm.doc.part_number) return;
	pc_render_preview(frm, {
		part_number: frm.doc.part_number,
		sell_price: frm.doc.sell_price,
		lines: (frm.doc.price_lines || []).map((ln) => ({
			module_label: ln.module_label,
			qty: ln.qty,
			line_price: ln.line_price,
		})),
		warnings: frm.doc.warnings_text ? frm.doc.warnings_text.split("\n") : [],
	});
}

function pc_render_preview(frm, data) {
	const wrapper = frm.fields_dict.price_preview && frm.fields_dict.price_preview.$wrapper;
	if (!wrapper) return;
	if (data.error) {
		wrapper.html(
			`<div class="text-danger" style="padding:8px 0">${frappe.utils.escape_html(data.error)}</div>`
		);
		return;
	}
	const rows = (data.lines || [])
		.map(
			(ln) => `
			<tr>
				<td>${frappe.utils.escape_html(ln.module_label || "")}</td>
				<td class="text-right">${flt(ln.qty)}</td>
				<td class="text-right">${format_currency(ln.line_price)}</td>
			</tr>`
		)
		.join("");
	const warnings = (data.warnings || [])
		.filter(Boolean)
		.map(
			(w) =>
				`<div class="text-warning small">&#9888; ${frappe.utils.escape_html(w)}</div>`
		)
		.join("");
	wrapper.html(`
		<div style="padding:4px 0">
			<div style="font-size:1.2em;font-weight:600">${frappe.utils.escape_html(data.part_number || "")}</div>
			<table class="table table-sm" style="margin:8px 0 4px;max-width:480px">
				<thead><tr><th>${__("Module")}</th><th class="text-right">${__("Qty")}</th>
				<th class="text-right">${__("Price")}</th></tr></thead>
				<tbody>${rows}</tbody>
				<tfoot><tr><th colspan="2">${__("Selling Price")}</th>
				<th class="text-right">${format_currency(data.sell_price)}</th></tr></tfoot>
			</table>
			${warnings}
		</div>`);
}

function pc_generate(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the configuration first."));
		return;
	}
	frappe.call({
		method: PC_API + "generate_erpnext_records",
		args: { configuration: frm.doc.name },
		freeze: true,
		freeze_message: __("Generating Item, BOM and Selling Price…"),
		callback(r) {
			if (!r.message) return;
			frappe.show_alert({
				message: __("Generated {0} / {1}", [r.message.item, r.message.bom]),
				indicator: "green",
			});
			frm.reload_doc();
		},
	});
}
