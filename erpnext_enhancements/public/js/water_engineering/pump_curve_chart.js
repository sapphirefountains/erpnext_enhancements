/**
 * Pump performance-curve chart on the Item form (Pump Specifications section).
 *
 * Renders the `custom_pump_curve` points (flow GPM vs. head ft) into the
 * `custom_pump_curve_chart` HTML field as a frappe-charts line chart, redrawn
 * live as points are added/edited/removed — so the curve is readable at a glance
 * instead of a table of numbers. Pure desk form script; no new dependencies
 * (frappe.Chart ships with the desk).
 */

frappe.ui.form.on("Item", {
	refresh: render_pump_curve_chart,
	custom_pump_curve_add: render_pump_curve_chart,
	custom_pump_curve_remove: render_pump_curve_chart,
});

frappe.ui.form.on("Pump Curve Point", {
	flow_gpm: render_pump_curve_chart,
	head_ft: render_pump_curve_chart,
});

function render_pump_curve_chart(frm) {
	const field = frm.get_field && frm.get_field("custom_pump_curve_chart");
	if (!field || !field.$wrapper) return;
	field.$wrapper.empty();

	const rows = (frm.doc.custom_pump_curve || [])
		.filter((r) => r.flow_gpm !== undefined && r.flow_gpm !== null)
		.slice()
		.sort((a, b) => (a.flow_gpm || 0) - (b.flow_gpm || 0));

	if (rows.length < 2) {
		field.$wrapper.html(
			`<div class="text-muted" style="padding:8px 2px">${__(
				"Add at least two Pump Curve points (flow + head) to plot the curve."
			)}</div>`
		);
		return;
	}

	const container = $('<div class="pump-curve-chart"></div>').appendTo(field.$wrapper).get(0);
	// eslint-disable-next-line no-new
	new frappe.Chart(container, {
		title: __("Pump Curve — Head (ft) vs Flow (GPM)"),
		type: "line",
		height: 260,
		colors: ["#2490ef"],
		data: {
			labels: rows.map((r) => String(r.flow_gpm)),
			datasets: [{ name: __("Head (ft)"), values: rows.map((r) => r.head_ft || 0) }],
		},
		lineOptions: { hideDots: 0, regionFill: 0, spline: 1 },
		axisOptions: { xIsSeries: 1 },
		tooltipOptions: {
			formatTooltipX: (d) => `${d} GPM`,
			formatTooltipY: (d) => `${d} ft`,
		},
	});
}
