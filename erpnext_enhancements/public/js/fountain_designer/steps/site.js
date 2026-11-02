// Site. The address and the state; the engine fills the elevation, design temperatures, frost depth,
// hardness, seismic design category and utility rates from them (ladder step 0) wherever they are blank,
// and never over a value typed here. The "Codes and jurisdiction" card says what the state enforces: the
// code editions, the health authority, the venues it regulates, plan review and licensing (phase J).

import { card, field, stage_head } from "../ui.js";

export const key = "site";

const SDC = ["A", "B", "C", "D", "D0", "D1", "D2", "E", "F"];

const LOOKED_UP = [
	{ key: "elevation_ft", label: "Elevation", unit: "ft", kind: "number" },
	{ key: "air_temp_f", label: "Air design temperature", unit: "°F", kind: "number" },
	{ key: "water_temp_f", label: "Water design temperature", unit: "°F", kind: "number" },
	{ key: "frost_depth_in", label: "Frost depth", unit: "in", kind: "number" },
	{ key: "hardness_ppm", label: "Water hardness", unit: "ppm", kind: "number" },
	{ key: "seismic_sdc", label: "Seismic design category", unit: "", kind: "select", options: SDC },
	{ key: "kwh_rate", label: "Power rate", unit: "$/kWh", kind: "number" },
	{ key: "water_rate_per_ccf", label: "Water rate", unit: "$/ccf", kind: "number" },
];

function shown(site, f) {
	const v = site[f.key];
	return v != null && v !== "";
}

// The quick-mode summary of what the address and the state filled (repainted after every derive).
export function found_html(h, model) {
	const site = (model && model.site) || {};
	const rows = LOOKED_UP.filter((f) => shown(site, f)).map(
		(f) => `<div class="fd-rollup"><dt>${h.esc(h.__(f.label))}</dt><dd>${h.esc(site[f.key])} ${h.esc(f.unit)}</dd></div>`
	);
	if (typeof site.winterize === "boolean") {
		rows.push(`<div class="fd-rollup"><dt>${h.esc(h.__("Winterize"))}</dt><dd>${h.esc(h.__(site.winterize ? "Yes" : "No"))}</dd></div>`);
	}
	return rows.length ? `<dl class="fd-rollups">${rows.join("")}</dl>` : `<p class="fd-muted">${h.esc(h.__("Filled in once the design is worked out."))}</p>`;
}

function list(h, items) {
	const words = (items || []).map((x) => h.__(String(x)));
	if (words.length < 2) return words.join("");
	return words.slice(0, -1).join(", ") + " " + h.__("and") + " " + words[words.length - 1];
}

// The "Codes and jurisdiction" card's body, from the derive's jurisdiction result (repainted after every derive).
export function codes_html(h, reply, model) {
	const { esc, __ } = h;
	const j = reply && reply.results && reply.results.jurisdiction;
	const v = (j && j.value) || null;
	if (!v) return `<p class="fd-muted">${esc(__("Working out what the state enforces…"))}</p>`;
	if (!v.known) {
		const code = v.code || (model && model.site && model.site.jurisdiction) || "";
		return `<p class="fd-hint fd-hint--warn" role="note">${esc(
			code
				? __("No jurisdiction data for {0}: type the site's elevation, temperatures, frost depth and hardness, and confirm the codes with the authority having jurisdiction.", [code])
				: __("Pick the site's state to see the codes it enforces.")
		)}</p>`;
	}
	const editions = (v.editions || []).length ? v.editions.join(", ") : __("No editions on file");
	const venues = (v.regulated_venues || []).length ? list(h, v.regulated_venues) : __("None listed");
	const rows = [
		[__("Codes"), editions],
		[__("Health authority"), v.agency],
		[__("Regulated venues"), venues],
		[__("Interactive features"), v.iwf_rule ? (v.iwf_like_utah ? __("{0}, checked as Utah's rule", [v.iwf_rule]) : __("{0}, the state's own rule", [v.iwf_rule])) : null],
		[__("Health-department plan review"), v.plan_review ? __(String(v.plan_review)) : null],
	]
		.filter((r) => r[1])
		.map((r) => `<div class="fd-rollup"><dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd></div>`)
		.join("");
	const licensing = v.licensing ? `<p class="fd-hint">${esc(v.licensing)}</p>` : "";
	const notes = v.notes ? `<p class="fd-hint">${esc(v.notes)}</p>` : "";
	const ahj = v.confirm_ahj
		? `<p class="fd-hint fd-hint--warn" role="note">${esc(v.ahj_note || __("Confirm the code editions with the authority having jurisdiction."))}</p>`
		: "";
	return `<dl class="fd-rollups">${rows}</dl>${licensing}${notes}${ahj}`;
}

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
		hint: "Street address or the nearest intersection. The elevation, seismic values and temperatures come from it.",
	});
	const juris = field(h, {
		path: "site.jurisdiction",
		label: "State",
		kind: "select",
		options: opts.jurisdictions || [],
		value: site.jurisdiction,
		blank: "Pick the state",
		hint: full ? "" : "Its codes, health rules and climate defaults apply.",
	});
	const codes = card(h, {
		id: "site-codes",
		title: __("Codes and jurisdiction"),
		simple: `<div data-fd-codes>${codes_html(h, ctx.reply, model)}</div>`,
	});
	const looked_up = LOOKED_UP.map((f) =>
		field(h, {
			path: "site." + f.key,
			label: f.label,
			kind: f.kind,
			unit: f.unit,
			options: f.options,
			blank: f.kind === "select" ? "From the address" : undefined,
			value: site[f.key],
			placeholder: __("From the address"),
		})
	).join("");
	if (!full) {
		return `${stage_head(h, __("Site"), __("Where the fountain goes."))}
			${card(h, { id: "site", title: __("Where"), simple: `${address}${juris}` })}
			${card(h, { id: "site-found", title: __("From the address"), simple: `<div data-fd-site-found>${found_html(h, model)}</div>` })}
			${codes}`;
	}
	const venue = field(h, { path: "site.venue_type", label: "Venue", kind: "select", options: opts.venue_types || [], value: site.venue_type });
	const regulated = field(h, {
		path: "site.regulated",
		label: "Regulated venue (health department review)",
		kind: "check",
		value: site.regulated,
		hint: "Pools, spas, and interactive features where the state regulates them. The engine sets it from the venue and the state.",
	});
	const winterize = field(h, {
		path: "site.winterize",
		label: "The site freezes: winterize",
		kind: "check",
		value: site.winterize,
		hint: "From the state's design city until you change it.",
	});
	return `${stage_head(h, __("Site"), __("Where the fountain goes, and the rules it is built to."))}
		${card(h, {
			id: "site",
			title: __("Where"),
			full,
			open: ctx.adv("site"),
			simple: `${address}<div class="fd-grid">${venue}${juris}</div>${regulated}`,
			advanced: `<div class="fd-grid">${looked_up}</div>${winterize}
				${
					ctx.can_write
						? `<div class="fd-actions"><button type="button" class="sf-btn sf-btn--secondary" data-fd-action="site-refresh">${h.esc(
								__("Refresh from the address")
						  )}</button></div><p class="fd-hint">${h.esc(__("Blanks every value above, typed ones too, and looks them up again."))}</p>`
						: ""
				}`,
			adv_note: "Left blank, each of these comes from the address or the state.",
		})}
		${codes}`;
}
