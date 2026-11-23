/**
 * Catalog curve charts for the Fountain Designer (Item, Pump Curve, Nozzle Profile forms).
 *
 * - Item (Pump Specifications section): the "Pump Curves" panel lists this Item's Pump Curve
 *   records, best first, charts them together, and offers "New Pump Curve". The legacy
 *   `custom_pump_curve` table, shown only while it still has rows, keeps its own chart.
 * - Pump Curve: head vs flow, redrawn live as points are edited.
 * - Nozzle Profile: display height and flow vs head from the performance points.
 * - A "Generic estimate" banner on all three when the record is flagged.
 *
 * The charts are drawn as inline SVG on proportional axes. frappe.Chart's line chart spaces
 * its x labels evenly, so a curve with points at 0, 10, 40 and 45 GPM drew the 10-to-40 jump
 * as wide as the 40-to-45 one, which misreads exactly the gaps a designer is looking for.
 *
 * Loaded through hooks.py `doctype_js` on the three forms; the guard below registers the
 * handlers once however many of those forms a session opens.
 */

(function () {
	if (window.sf_water_curves) return;

	const COLORS = ["#2490ef", "#e86c13", "#28a745", "#743ee2", "#e24c4c", "#0f9aa8"];
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	function nice_step(span) {
		const raw = span / 5;
		const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
		const norm = raw / mag;
		return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
	}

	/**
	 * Draw an x/y line chart into `container`.
	 * opts: {series: [{name, points: [[x, y], ...]}], x_label, y_label, height}
	 */
	function plot(container, opts) {
		const series = (opts.series || []).filter((s) => (s.points || []).length >= 2);
		if (!series.length) {
			$(container).html(
				`<div class="text-muted" style="padding:8px 2px">${esc(
					opts.empty || __("Add at least two points to draw the chart.")
				)}</div>`
			);
			return;
		}
		const W = 560,
			H = opts.height || 260,
			L = 52,
			R = 16,
			T = 14,
			B = 40;
		const xs = series.flatMap((s) => s.points.map((p) => p[0]));
		const ys = series.flatMap((s) => s.points.map((p) => p[1]));
		const xstep = nice_step(Math.max(...xs) || 1);
		const ystep = nice_step(Math.max(...ys) || 1);
		const xmax = Math.ceil((Math.max(...xs) || 1) / xstep) * xstep;
		const ymax = Math.ceil((Math.max(...ys) || 1) / ystep) * ystep;
		const sx = (x) => L + (x / xmax) * (W - L - R);
		const sy = (y) => H - B - (y / ymax) * (H - T - B);
		const grid = [];
		for (let x = 0; x <= xmax + 1e-9; x += xstep) {
			grid.push(
				`<line x1="${sx(x)}" y1="${T}" x2="${sx(x)}" y2="${H - B}" stroke="var(--border-color, #e2e6e9)"/>`,
				`<text x="${sx(x)}" y="${H - B + 14}" text-anchor="middle" font-size="10" fill="var(--text-muted, #6c7680)">${+x.toFixed(2)}</text>`
			);
		}
		for (let y = 0; y <= ymax + 1e-9; y += ystep) {
			grid.push(
				`<line x1="${L}" y1="${sy(y)}" x2="${W - R}" y2="${sy(y)}" stroke="var(--border-color, #e2e6e9)"/>`,
				`<text x="${L - 6}" y="${sy(y) + 3}" text-anchor="end" font-size="10" fill="var(--text-muted, #6c7680)">${+y.toFixed(2)}</text>`
			);
		}
		const lines = series.map((s, i) => {
			const color = COLORS[i % COLORS.length];
			const pts = s.points
				.slice()
				.sort((a, b) => a[0] - b[0])
				.map((p) => `${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`);
			const dots = pts
				.map((p) => {
					const [cx, cy] = p.split(",");
					return `<circle cx="${cx}" cy="${cy}" r="2.5" fill="${color}"/>`;
				})
				.join("");
			return `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts.join(" ")}"/>${dots}`;
		});
		const legend =
			series.length > 1 || opts.legend
				? `<div style="display:flex;flex-wrap:wrap;gap:12px;font-size:11px;margin-top:4px">${series
						.map(
							(s, i) =>
								`<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${
									COLORS[i % COLORS.length]
								};margin-right:4px"></span>${esc(s.name)}</span>`
						)
						.join("")}</div>`
				: "";
		$(container).html(
			`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px" role="img" aria-label="${esc(
				opts.y_label
			)} vs ${esc(opts.x_label)}">${grid.join("")}
			<line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" stroke="var(--text-muted, #6c7680)"/>
			<line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" stroke="var(--text-muted, #6c7680)"/>
			${lines.join("")}
			<text x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--text-color, #1f272e)">${esc(opts.x_label)}</text>
			<text x="12" y="${(T + H - B) / 2}" text-anchor="middle" font-size="11" fill="var(--text-color, #1f272e)" transform="rotate(-90 12 ${(T + H - B) / 2})">${esc(opts.y_label)}</text>
			</svg>${legend}`
		);
	}

	function generic_banner(text) {
		return `<div class="alert alert-warning" style="margin:4px 0 8px;padding:6px 10px;font-size:12px">
			<b>${__("Generic estimate")}</b>: ${esc(text)}</div>`;
	}

	function points_of(rows, xkey, ykey) {
		return (rows || [])
			.filter((r) => r[xkey] != null && r[xkey] !== "" && r[ykey] != null && r[ykey] !== "")
			.map((r) => [+r[xkey], +r[ykey]]);
	}

	// ------------------------------------------------------------------ Item

	function render_item_legacy_chart(frm) {
		const field = frm.get_field && frm.get_field("custom_pump_curve_chart");
		if (!field || !field.$wrapper) return;
		plot(field.$wrapper.get(0), {
			series: [{ name: __("Head (ft)"), points: points_of(frm.doc.custom_pump_curve, "flow_gpm", "head_ft") }],
			x_label: __("Flow (GPM)"),
			y_label: __("Head (ft)"),
			empty: __("Add at least two Pump Curve points (flow + head) to plot the curve."),
		});
	}

	function render_item_curves(frm) {
		const field = frm.get_field && frm.get_field("custom_pump_curves_html");
		if (!field || !field.$wrapper) return;
		const $w = field.$wrapper.empty();
		// The panel sits in the Pump Specifications section, which shows for these two only.
		if (frm.doc.item_group !== "Pumps" && frm.doc.custom_equipment_class !== "Pump") return;
		const banner = frm.doc.custom_generic_estimate
			? generic_banner(__("this Item's specs are typical values, not a chosen model's datasheet."))
			: "";
		if (frm.is_new()) {
			$w.html(banner + `<div class="text-muted small">${__("Save the Item to add pump curves.")}</div>`);
			return;
		}
		frappe
			.call({
				method: "erpnext_enhancements.water_engineering.api.designer.item_pump_curves",
				args: { item_code: frm.doc.name },
			})
			.then((r) => {
				const data = (r && r.message) || {};
				if (!data.can_read) {
					$w.html(banner);
					return;
				}
				const curves = data.curves || [];
				const rows = curves
					.map((c, i) => {
						const label = c.name
							? `<a href="${frappe.utils.get_form_link("Pump Curve", c.name)}">${esc(c.curve_label || c.name)}</a>`
							: esc(c.curve_label);
						const tags = [
							c.source,
							c.rpm ? `${c.rpm} rpm` : "",
							c.impeller_dia_in ? `${c.impeller_dia_in} in` : "",
							c.speed_pct && +c.speed_pct !== 100 ? `${c.speed_pct}%` : "",
							`${(c.points || []).length} ${__("points")}`,
						]
							.filter(Boolean)
							.map(esc)
							.join(" · ");
						const best = i === 0 ? ` <span class="indicator-pill green">${__("used by designs")}</span>` : "";
						const generic = c.is_generic_estimate
							? ` <span class="indicator-pill orange">${__("Generic estimate")}</span>`
							: "";
						return `<div style="padding:3px 0">${label}${best}${generic}<div class="text-muted small">${tags}</div></div>`;
					})
					.join("");
				$w.html(
					banner +
						(curves.length
							? rows
							: `<div class="text-muted small">${__("No pump curves yet for this Item.")}</div>`) +
						`<div class="pump-curves-plot" style="margin-top:8px"></div>` +
						(data.can_create
							? `<button class="btn btn-xs btn-default new-pump-curve" style="margin-top:6px">${__(
									"New Pump Curve"
							  )}</button>`
							: "")
				);
				if (curves.length) {
					plot($w.find(".pump-curves-plot").get(0), {
						series: curves.map((c) => ({
							name: c.curve_label || c.source,
							points: points_of(c.points, "flow_gpm", "head_ft"),
						})),
						x_label: __("Flow (GPM)"),
						y_label: __("Head (ft)"),
						legend: true,
					});
				}
				$w.find(".new-pump-curve").on("click", () => frappe.new_doc("Pump Curve", { item: frm.doc.name }));
			});
	}

	frappe.ui.form.on("Item", {
		refresh(frm) {
			render_item_legacy_chart(frm);
			render_item_curves(frm);
		},
		custom_generic_estimate: render_item_curves,
		custom_equipment_class: render_item_curves,
		custom_pump_curve_add: render_item_legacy_chart,
		custom_pump_curve_remove: render_item_legacy_chart,
	});

	// ------------------------------------------------------------------ Pump Curve

	function render_pump_curve(frm) {
		frm.set_intro(
			frm.doc.source === "Generic estimate"
				? __("Generic estimate: a typical curve for sizing before a model is chosen. A design using it warns before a submittal.")
				: "",
			"orange"
		);
		const field = frm.get_field("curve_chart");
		if (!field || !field.$wrapper) return;
		plot(field.$wrapper.get(0), {
			series: [{ name: __("Head (ft)"), points: points_of(frm.doc.points, "flow_gpm", "head_ft") }],
			x_label: __("Flow (GPM)"),
			y_label: __("Head (ft)"),
			empty: __("Add at least two points (flow + head) to draw the curve."),
		});
	}

	frappe.ui.form.on("Pump Curve", {
		refresh: render_pump_curve,
		source: render_pump_curve,
		points_add: render_pump_curve,
		points_remove: render_pump_curve,
	});

	function on_curve_point(frm) {
		if (frm.doctype === "Pump Curve") render_pump_curve(frm);
		else if (frm.doctype === "Item") render_item_legacy_chart(frm);
	}

	frappe.ui.form.on("Pump Curve Point", { flow_gpm: on_curve_point, head_ft: on_curve_point });

	// ------------------------------------------------------------------ Nozzle Profile

	function render_nozzle(frm) {
		frm.set_intro(
			frm.doc.is_generic_estimate
				? __("Generic estimate: replace these values with the manufacturer's table before a submittal.")
				: "",
			"orange"
		);
		const field = frm.get_field("performance_chart");
		if (!field || !field.$wrapper) return;
		const rows = (frm.doc.performance || []).map((r) =>
			Object.assign({}, r, { head: r.head_ft || (r.psi ? r.psi * 2.31 : null) })
		);
		const $w = field.$wrapper.empty();
		const $h = $('<div style="margin-bottom:10px"></div>').appendTo($w);
		const $f = $("<div></div>").appendTo($w);
		plot($h.get(0), {
			series: [{ name: __("Display height (ft)"), points: points_of(rows, "head", "display_height_ft") }],
			x_label: __("Head (ft)"),
			y_label: __("Display height (ft)"),
			height: 200,
			empty: __("Add rows with a head and a display height to chart the display."),
		});
		plot($f.get(0), {
			series: [{ name: __("Flow (GPM)"), points: points_of(rows, "head", "flow_gpm") }],
			x_label: __("Head (ft)"),
			y_label: __("Flow (GPM)"),
			height: 200,
			empty: __("Add rows with a head and a flow to chart the flow."),
		});
	}

	frappe.ui.form.on("Nozzle Profile", {
		refresh: render_nozzle,
		is_generic_estimate: render_nozzle,
		performance_add: render_nozzle,
		performance_remove: render_nozzle,
	});

	frappe.ui.form.on("Nozzle Performance Point", {
		head_ft: render_nozzle,
		psi: render_nozzle,
		flow_gpm: render_nozzle,
		display_height_ft: render_nozzle,
	});

	window.sf_water_curves = { plot, generic_banner };
})();
