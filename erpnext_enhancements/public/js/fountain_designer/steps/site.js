// Site. The address; the engine fills the elevation, design temperatures, frost depth, hardness and
// utility rates from it (ladder step 0) wherever they are blank, and never over a value typed here.

import { card, field, stage_head } from "../ui.js";

export const key = "site";

const LOOKED_UP = [
	{ key: "elevation_ft", label: "Elevation", unit: "ft", kind: "number" },
	{ key: "air_temp_f", label: "Air design temperature", unit: "°F", kind: "number" },
	{ key: "water_temp_f", label: "Water design temperature", unit: "°F", kind: "number" },
	{ key: "frost_depth_in", label: "Frost depth", unit: "in", kind: "number" },
	{ key: "hardness_ppm", label: "Water hardness", unit: "ppm", kind: "number" },
	{ key: "kwh_rate", label: "Power rate", unit: "$/kWh", kind: "number" },
	{ key: "water_rate_per_ccf", label: "Water rate", unit: "$/ccf", kind: "number" },
];

export function render(ctx) {
	const { h, model, full, boot } = ctx;
	const { __ } = h;
	const site = model.site || {};
	const opts = boot.options || {};
	const address = field(h, {
		path: "site.address",
		label: "Address",
		kind: "text",
		value: site.address || "",
		hint: "Street address or the nearest intersection. The elevation, temperatures and rates come from it.",
	});
	const looked_up = LOOKED_UP.map((f) =>
		field(h, {
			path: "site." + f.key,
			label: f.label,
			kind: f.kind,
			unit: f.unit,
			value: site[f.key],
			placeholder: __("From the address"),
		})
	).join("");
	if (!full) {
		const summary = LOOKED_UP.filter((f) => site[f.key] != null && site[f.key] !== "")
			.map((f) => `<div class="fd-rollup"><dt>${h.esc(__(f.label))}</dt><dd>${h.esc(site[f.key])} ${h.esc(f.unit)}</dd></div>`)
			.join("");
		return `${stage_head(h, __("Site"), __("Where the fountain goes."))}
			${card(h, { id: "site", title: __("Where"), simple: address })}
			${summary ? card(h, { id: "site-found", title: __("From the address"), simple: `<dl class="fd-rollups">${summary}</dl>` }) : ""}`;
	}
	const venue = field(h, { path: "site.venue_type", label: "Venue", kind: "select", options: opts.venue_types || [], value: site.venue_type });
	const juris = field(h, { path: "site.jurisdiction", label: "Jurisdiction", kind: "select", options: opts.jurisdictions || [], value: site.jurisdiction });
	const regulated = field(h, {
		path: "site.regulated",
		label: "Regulated venue (health department review)",
		kind: "check",
		value: site.regulated,
		hint: "Pools, spas and interactive features in Utah. The engine also sets it from the venue.",
	});
	return `${stage_head(h, __("Site"), __("Where the fountain goes, and the rules it is built to."))}
		${card(h, {
			id: "site",
			title: __("Where"),
			full,
			open: ctx.adv("site"),
			simple: `${address}<div class="fd-grid">${venue}${juris}</div>${regulated}`,
			advanced: `<div class="fd-grid">${looked_up}</div>
				${
					ctx.can_write
						? `<div class="fd-actions"><button type="button" class="sf-btn sf-btn--secondary" data-fd-action="site-refresh">${h.esc(
								__("Refresh from the address")
						  )}</button></div><p class="fd-hint">${h.esc(__("Blanks every value above, typed ones too, and looks them up again."))}</p>`
						: ""
				}`,
			adv_note: "Left blank, each of these is looked up from the address.",
		})}`;
}
