// Features. Quick mode shows the kit's headline inputs only (height, length, look). Full mode shows
// every feature: family and type, its family's inputs, the look, the product (a catalog pick or "generic,
// pick later"), how many, and the basin that catches it.

import { card, derived, field, stage_head, tiles } from "../ui.js";
import { get_in } from "../model.js";

export const key = "features";

export function render(ctx) {
	return ctx.full ? render_full(ctx) : render_quick(ctx);
}

function feature_values(ctx, feature) {
	return get_in(ctx.values, ["features", feature && feature.id]) || {};
}

function render_quick(ctx) {
	const { h, model, kit } = ctx;
	const { __ } = h;
	const f0 = (model.features || [])[0];
	if (!kit || !kit.headline || !kit.headline.length || !f0) {
		return `${stage_head(h, __("Features"), __("This design has no kit. Switch it to Full on Start to add its features one by one."))}`;
	}
	const inputs = kit.headline
		.map((hl) =>
			hl.kind === "tiles"
				? tiles(h, hl.path, hl.options || [], get_in(model, hl.path), hl.label)
				: field(h, { path: hl.path, label: hl.label, kind: hl.kind === "int" ? "int" : "number", unit: hl.unit, value: get_in(model, hl.path) })
		)
		.join("");
	const v = feature_values(ctx, f0);
	return `${stage_head(h, __("Features"), __(kit.label))}
		${card(h, {
			id: "headline",
			title: f0.label || __(kit.label),
			simple: `${inputs}
				${derived_line(h, "features." + f0.id + ".flow_gpm", v.flow_gpm, "Flow", "gpm", 0)}
				${v.note ? `<p class="fd-hint">${h.esc(v.note)}</p>` : ""}`,
		})}`;
}

function derived_line(h, key, value, label, unit, digits) {
	return derived(h, key, value, { label, unit, digits });
}

function render_full(ctx) {
	const { h, model, boot } = ctx;
	const { esc, __ } = h;
	const families = boot.families || [];
	const basins = (model.basins || []).map((b) => ({ value: b.id, label: b.label || b.id }));
	const cards = (model.features || [])
		.map((f, i) => {
			const fam = families.find((x) => x.key === f.family) || { inputs: [], looks: [], types: [] };
			const base = `features.${i}`;
			const simple_inputs = (fam.inputs || []).filter((x) => !x.advanced);
			const adv_inputs = (fam.inputs || []).filter((x) => x.advanced);
			const input = (x) =>
				field(h, { path: `${base}.inputs.${x.key}`, label: x.label, kind: x.kind || "number", unit: x.unit, options: x.options, value: get_in(f, ["inputs", x.key]) });
			const v = feature_values(ctx, f);
			const simple = `
				<div class="fd-grid">
					${field(h, { path: `${base}.label`, label: "Label", kind: "text", value: f.label })}
					${field(h, { path: `${base}.family`, label: "Family", kind: "select", options: families.map((x) => ({ value: x.key, label: x.label })), value: f.family })}
					${field(h, { path: `${base}.type`, label: "Type", kind: "select", options: fam.types || [], value: f.type, blank: "Pick a type" })}
					${field(h, { path: `${base}.count`, label: "How many", kind: "int", value: f.count })}
				</div>
				<div class="fd-grid">${simple_inputs.map(input).join("")}</div>
				${fam.looks && fam.looks.length ? tiles(h, `${base}.effect.look`, fam.looks, get_in(f, ["effect", "look"]), "Look") : ""}
				${derived_line(h, `features.${f.id}.flow_gpm`, v.flow_gpm, "Flow", "gpm", 0)}
				${v.note ? `<p class="fd-hint">${esc(v.note)}</p>` : ""}`;
			const advanced = `
				<div class="fd-grid">${adv_inputs.map(input).join("")}
					${field(h, { path: `${base}.basin`, label: "Caught by", kind: "select", options: basins, value: f.basin, blank: "No basin" })}
				</div>
				${field(h, {
					path: `${base}.product.item`,
					label: "Product",
					kind: "catalog",
					equipment_class: fam.product_class || "nozzle",
					value: get_in(f, ["product", "item"]) || "",
					placeholder: "Generic, pick later",
					hint: "Left blank, the design is sized on a generic product and flagged before a submittal.",
				})}`;
			return card(h, {
				id: "feature-" + f.id,
				title: f.label || __("Feature {0}", [i + 1]),
				full: true,
				open: ctx.adv("feature-" + f.id),
				simple,
				advanced,
				actions: ctx.can_write
					? `<button type="button" class="sf-btn sf-btn--quiet" data-fd-remove="features" data-fd-id="${esc(f.id)}">${esc(__("Remove"))}</button>`
					: "",
			});
		})
		.join("");
	const section = ctx.section_card("features");
	return `${stage_head(h, __("Features"), __("What the water does. Each feature's flow is worked out from its look and size."))}
		${section}
		${cards || h.state_html("empty", { title: __("No features yet.") })}
		${ctx.can_write ? `<div class="fd-actions"><button type="button" class="sf-btn sf-btn--secondary" data-fd-action="add-feature">${esc(__("Add a feature"))}</button></div>` : ""}`;
}
