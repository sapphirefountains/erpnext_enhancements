// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Frappe auto-loads this form script for the Water Feature Design form. The
// engine recompute() runs server-side on every save (validate), so the rollups
// and audit trail refresh on save; this just adds quick actions and renders the
// dashboard summary panel.

frappe.ui.form.on("Water Feature Design", {
	refresh(frm) {
		frm.add_custom_button(__("Open Wizard"), () => frappe.set_route("water-engineering-wizard"));
		if (!frm.is_new()) {
			frm.add_custom_button(__("Recalculate"), () => frm.save());
		}
		render_dashboard(frm);
	},
});

function render_dashboard(frm) {
	const d = frm.doc;
	const esc = frappe.utils.escape_html;
	const fmt = (v) => (v == null || v === 0 ? "—" : esc(String(v)));
	const rows = [
		[__("Total Basin (gal)"), d.total_basin_gallons],
		[__("Circulation (GPM)"), d.required_circulation_gpm],
		[__("Design Flow (GPM)"), d.design_flow_gpm],
		[__("TDH (ft)"), d.computed_tdh_ft],
		[__("Selected Pump"), d.selected_pump],
	]
		.map(([k, v]) => `<tr><td style="color:var(--text-muted)">${k}</td><td><b>${fmt(v)}</b></td></tr>`)
		.join("");

	const needs = (d.next_inputs_needed || "")
		.split("\n")
		.filter((s) => s);
	const needsHtml = needs.length
		? `<div style="margin-top:8px;padding:6px 10px;border-radius:6px;background:var(--bg-yellow,#fef3c7);color:var(--text-on-yellow,#7a5b00)">
			<b>${__("Still needed")}:</b> ${needs.map(esc).join(", ")}</div>`
		: `<div style="margin-top:8px;color:var(--text-muted)">${__("All Phase-1 inputs provided.")}</div>`;

	frm.get_field("dashboard").$wrapper.html(`
		<div style="border:1px solid var(--border-color);border-radius:8px;padding:12px 14px">
			<div style="font-weight:600;margin-bottom:6px">${__("Hydraulic Summary")}
				<span style="float:right;color:var(--text-muted)">${d.completion_percent || 0}%</span>
			</div>
			<table style="width:100%;font-size:13px">${rows}</table>
			${needsHtml}
		</div>
	`);
}
