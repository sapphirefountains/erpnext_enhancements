// Basins (full mode). Shape and size, the freeboard, and which features each catches; the volume, the
// weight and the surge at shut-down are worked out.

import { card, derived, field, stage_head } from "../ui.js";
import { get_in } from "../model.js";

export const key = "basins";

export function render(ctx) {
	const { h, model, boot } = ctx;
	const { esc, __ } = h;
	const shapes = (boot.options && boot.options.basin_shapes) || ["rectangular", "circular", "irregular"];
	const cards = (model.basins || [])
		.map((b, i) => {
			const base = `basins.${i}`;
			const v = get_in(ctx.values, ["basins", b.id]) || {};
			const caught = (model.features || []).filter((f) => f.basin === b.id).map((f) => f.label || f.id);
			const round = b.shape === "circular";
			const simple = `
				<div class="fd-grid">
					${field(h, { path: `${base}.label`, label: "Label", kind: "text", value: b.label })}
					${field(h, { path: `${base}.shape`, label: "Shape", kind: "select", options: shapes, value: b.shape })}
					${field(h, { path: `${base}.length_in`, label: round ? "Diameter" : "Length", kind: "number", unit: "in", value: b.length_in })}
					${round ? "" : field(h, { path: `${base}.width_in`, label: "Width", kind: "number", unit: "in", value: b.width_in })}
					${field(h, { path: `${base}.depth_in`, label: "Water depth", kind: "number", unit: "in", value: b.depth_in })}
				</div>
				${derived(h, `basins.${b.id}.volume_gal`, v.volume_gal, { label: "Volume", unit: "gal", digits: 0 })}
				${derived(h, `basins.${b.id}.surge_in`, v.surge_in, { label: "Surge at shut-down", unit: "in", digits: 1 })}
				<p class="fd-muted">${esc(caught.length ? __("Catches: {0}", [caught.join(", ")]) : __("Catches no feature yet."))}</p>`;
			const advanced = `
				<div class="fd-grid">
					${field(h, { path: `${base}.freeboard_in`, label: "Freeboard", kind: "number", unit: "in", value: b.freeboard_in, placeholder: __("Worked out") })}
					${field(h, { path: `${base}.volume_gal_override`, label: "Volume override", kind: "number", unit: "gal", value: b.volume_gal_override, hint: "For a pond or an odd shape." })}
					${field(h, { path: `${base}.elevation_ft`, label: "Elevation", kind: "number", unit: "ft", value: b.elevation_ft, hint: "Water surface against the pump room floor." })}
				</div>
				${derived(h, `basins.${b.id}.weight_lb`, v.weight_lb, { label: "Water weight", unit: "lb", digits: 0 })}`;
			return card(h, {
				id: "basin-" + b.id,
				title: b.label || __("Basin {0}", [i + 1]),
				full: true,
				open: ctx.adv("basin-" + b.id),
				simple,
				advanced,
				actions: ctx.can_write
					? `<button type="button" class="sf-btn sf-btn--quiet" data-fd-remove="basins" data-fd-id="${esc(b.id)}">${esc(__("Remove"))}</button>`
					: "",
			});
		})
		.join("");
	return `${stage_head(h, __("Basins"), __("Where the water lands and what holds it."))}
		${ctx.section_card("basin")}
		${cards || h.state_html("empty", { title: __("No basins yet."), body: __("Left on Auto, the engine sizes one for the features.") })}
		${ctx.can_write ? `<div class="fd-actions"><button type="button" class="sf-btn sf-btn--secondary" data-fd-action="add-basin">${esc(__("Add a basin"))}</button></div>` : ""}`;
}
