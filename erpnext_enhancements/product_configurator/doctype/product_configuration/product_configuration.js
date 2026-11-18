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
			// v1.825.0: the sale's next step. The Item, BOM and price are made on the way, and the
			// Quotation opens; once it exists this becomes a way back to it.
			if (frm.doc.quotation) {
				frm.add_custom_button(__("Open Quotation"), () =>
					frappe.set_route("Form", "Quotation", frm.doc.quotation)
				);
			} else {
				frm.add_custom_button(__("Create Quotation"), () => pc_create_quotation(frm)).addClass(
					"btn-primary"
				);
			}
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
			const generated = __("Generated: {0} · {1}", [
				frappe.utils.get_form_link("Item", frm.doc.item, true),
				frm.doc.bom ? frappe.utils.get_form_link("BOM", frm.doc.bom, true) : __("no BOM"),
			]);
			const quoted = frm.doc.quotation
				? " · " + __("Quoted on {0}", [frappe.utils.get_form_link("Quotation", frm.doc.quotation, true)])
				: "";
			frm.dashboard.set_headline(generated + quoted);
		}
	},

	// Picking the deal fills who the quote is for, as the server does when neither is set.
	opportunity(frm) {
		if (!frm.doc.opportunity || frm.doc.customer || frm.doc.lead) return;
		frappe.db
			.get_value("Opportunity", frm.doc.opportunity, ["opportunity_from", "party_name", "company"])
			.then((r) => {
				const opp = (r && r.message) || {};
				if (opp.opportunity_from === "Customer") frm.set_value("customer", opp.party_name);
				else if (opp.opportunity_from === "Lead") frm.set_value("lead", opp.party_name);
				if (opp.company && !frm.doc.company) frm.set_value("company", opp.company);
			});
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

// v1.825.0: one click from a configuration to its draft Quotation. What is typed saves first; the
// server makes (or refreshes) the Item, BOM and price, makes the Quotation and opens it. Anything it
// could not do alongside (the attached Pricing Summary, the Products tag on the deal) comes back as a
// note, never as a failed quote. A refusal (the Product Engineer role, say) is the server's own words.
function pc_create_quotation(frm) {
	const go = () =>
		frappe
			.call({
				method: PC_API + "create_quotation",
				args: { configuration: frm.doc.name },
				freeze: true,
				freeze_message: __("Making the item, its bill of materials and the quotation…"),
			})
			.then((r) => {
				const out = r && r.message;
				if (!out || !out.quotation) return;
				(out.warnings || []).forEach((text) =>
					frappe.show_alert({ message: frappe.utils.escape_html(text), indicator: "orange" }, 12)
				);
				frappe.show_alert({
					message: out.existing
						? __("This configuration was already quoted on {0}.", [out.quotation])
						: __("Draft Quotation {0} made.", [out.quotation]),
					indicator: out.existing ? "blue" : "green",
				});
				const open_quote = () => frappe.set_route("Form", "Quotation", out.quotation);
				// v1.825.0: everything quoted on the deal in ERPNext is a product. Ask, never assume:
				// the deal may have other work, or a quote that lives in QuickBooks
				// (crm_enhancements/product_sale.py). Either answer opens the Quotation.
				if (!out.suggest_product_sale || !out.opportunity) {
					open_quote();
					return;
				}
				frappe.confirm(
					__(
						"Is {0} only this product, with no other work? A product sale gets no project or hand-off meeting: its Sales Order is the record.",
						[frappe.utils.escape_html(out.opportunity)]
					),
					() =>
						frappe
							.xcall("erpnext_enhancements.crm_enhancements.handoff.mark_product_sale", {
								opportunity: out.opportunity,
								ticked: 1,
							})
							.then(() => {
								frappe.show_alert({
									message: __("{0} is marked a product sale: no project.", [out.opportunity]),
									indicator: "blue",
								});
								open_quote();
							}, open_quote),
					open_quote
				);
			});
	if (frm.is_dirty()) {
		frm.save().then(go);
	} else {
		go();
	}
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
