// Pump room (full mode). Where the pump sits and how far it is from the feature (the person's), then a
// card per setup section the ladder fills from it: piping, pump, control, bypass, surge, treatment,
// lighting, electrical, winterization. Each card has its setup policy and an Advanced reveal.

import { card, derived, field, fmt, stage_head, tiles } from "../ui.js";
import { get_in, SLOT_SECTION } from "../model.js";

export const key = "pump_room";

export function render(ctx) {
	const { h } = ctx;
	const { __ } = h;
	return `${stage_head(h, __("Pump room"), __("Where the pump sits, and everything between it and the feature."))}
		${layout(ctx)}
		${piping(ctx)}
		${pump(ctx)}
		${control(ctx)}
		${equipment_card(ctx, "bypass", __("Bypass"), __("A minimum-flow bypass, when the pump would run too far left of its curve."))}
		${equipment_card(ctx, "surge", __("Surge"), __("An arrestor, when a valve slam could exceed the pipe's rating."))}
		${treatment(ctx)}
		${lighting(ctx)}
		${electrical(ctx)}
		${winterization(ctx)}`;
}

function layout(ctx) {
	const { h, model, boot } = ctx;
	const room = model.pump_room || {};
	const opts = boot.options || {};
	return card(h, {
		id: "layout",
		title: h.__("Layout"),
		full: true,
		open: ctx.adv("layout"),
		simple: `${tiles(h, "pump_room.location", opts.pump_locations || [], room.location, "Pump location")}
			<div class="fd-grid">
				${field(h, { path: "pump_room.distance_ft", label: "Distance to the feature", kind: "number", unit: "ft", value: room.distance_ft })}
				${field(h, { path: "pump_room.elevation_ft", label: "Pump below the water", kind: "number", unit: "ft", value: room.elevation_ft, hint: "Negative when the pump sits below the basin's water." })}
			</div>`,
		advanced: `<div class="fd-grid">
				${field(h, { path: "pump_room.material", label: "Pipe material", kind: "select", options: opts.materials || [], value: room.material })}
				${field(h, { path: "pump_room.route_template", label: "Route template", kind: "text", value: room.route_template, placeholder: h.__("From the feature family") })}
			</div>`,
	});
}

function preset_list(ctx, section) {
	const s = ((ctx.boot.sections || []).find((x) => x.key === section) || {}).presets;
	return s || [];
}

function piping(ctx) {
	const { h, model, boot } = ctx;
	const { esc, __ } = h;
	const segs = get_in(model, "network.segments") || [];
	const opts = boot.options || {};
	const rows = segs
		.map((s, i) => {
			const v = get_in(ctx.values, ["segments", s.id]) || {};
			const base = `network.segments.${i}`;
			return `<tr>
				<th scope="row">${esc(s.id)}<span class="fd-muted"> ${esc(__(s.line || ""))}</span></th>
				<td>${esc(s.from)} → ${esc(s.to)}</td>
				<td>${field(h, { path: `${base}.nominal_size`, label: "Size", kind: "text", value: s.nominal_size || "", placeholder: v.size || __("Auto") })}</td>
				<td>${field(h, { path: `${base}.length_ft`, label: "Length", kind: "number", unit: "ft", value: s.length_ft })}</td>
				<td><span data-fd-derived="segments.${esc(s.id)}.velocity_fps" data-fd-unit="ft/s" data-fd-digits="1">${esc(fmt1(v.velocity_fps, "ft/s"))}</span></td>
				<td><span data-fd-derived="segments.${esc(s.id)}.loss_ft" data-fd-unit="ft" data-fd-digits="1">${esc(fmt1(v.loss_ft, "ft"))}</span></td>
			</tr>`;
		})
		.join("");
	const table = segs.length
		? `<div class="fd-table-wrap"><table class="fd-table">
			<caption class="fd-visually-hidden">${esc(__("Pipe segments"))}</caption>
			<thead><tr><th scope="col">${esc(__("Segment"))}</th><th scope="col">${esc(__("Route"))}</th><th scope="col">${esc(__("Size"))}</th>
			<th scope="col">${esc(__("Length"))}</th><th scope="col">${esc(__("Velocity"))}</th><th scope="col">${esc(__("Friction"))}</th></tr></thead>
			<tbody>${rows}</tbody></table></div>`
		: `<p class="fd-muted">${esc(__("The route is laid out from the pump location once the design is worked out."))}</p>`;
	const fittings = segs
		.map((s, i) => {
			const list = (s.fittings || []).map((f) => `${f.qty || 1} × ${f.key}`).join(", ");
			return `<div class="fd-grid">
				${field(h, { path: `network.segments.${i}.material`, label: __("{0} material", [s.id]), kind: "select", options: opts.materials || [], value: s.material })}
				${field(h, { path: `network.segments.${i}.line`, label: __("{0} line", [s.id]), kind: "select", options: opts.lines || [], value: s.line })}
				<p class="fd-muted">${esc(__("Fittings: {0}", [list || __("none")]))}</p>
			</div>`;
		})
		.join("");
	return card(h, {
		id: "piping",
		title: __("Piping"),
		section: "piping",
		full: true,
		model,
		presets: preset_list(ctx, "piping"),
		open: ctx.adv("piping"),
		simple: table,
		advanced: fittings,
	});
}

function fmt1(value, unit) {
	return fmt(value, { unit, digits: 1 }) || "—";
}

function pump(ctx) {
	const { h, model, boot } = ctx;
	const { __ } = h;
	const pumps = get_in(model, "network.pumps") || [];
	const simple = pumps.length
		? pumps
				.map((p, i) => {
					const v = get_in(ctx.values, ["pumps", p.id]) || {};
					return `<div class="fd-subcard">
					${field(h, { path: `network.pumps.${i}.item`, label: __("Pump {0}", [p.id]), kind: "catalog", equipment_class: "pump", value: p.item || "", placeholder: p.label || "Generic, pick later" })}
					${derived(h, `pumps.${p.id}.flow_gpm`, v.flow_gpm, { label: "Flow", unit: "gpm", digits: 0 })}
					${derived(h, `pumps.${p.id}.head_ft`, v.head_ft, { label: "Head", unit: "ft", digits: 1 })}
					${derived(h, `pumps.${p.id}.kw`, v.kw, { label: "Power", unit: "kW", digits: 2 })}
				</div>`;
				})
				.join("")
		: `<p class="fd-muted">${h.esc(__("The pump is picked once the design is worked out."))}</p>`;
	const advanced = pumps
		.map((p, i) => {
			const v = get_in(ctx.values, ["pumps", p.id]) || {};
			return `<div class="fd-grid">
				${field(h, { path: `network.pumps.${i}.generic_class`, label: "Pump type", kind: "select", options: (boot.options && boot.options.pump_classes) || [], value: p.generic_class })}
				${field(h, { path: `network.pumps.${i}.speed_pct`, label: "Speed", kind: "number", unit: "%", value: p.speed_pct })}
				${derived(h, `pumps.${p.id}.npsh_margin_ft`, v.npsh_margin_ft, { label: "NPSH margin", unit: "ft", digits: 1 })}
				${derived(h, `pumps.${p.id}.bep_pct`, v.bep_pct, { label: "Flow against best efficiency", unit: "%", digits: 0 })}
			</div>`;
		})
		.join("");
	return card(h, { id: "pump", title: __("Pump"), section: "pump", full: true, model, presets: preset_list(ctx, "pump"), open: ctx.adv("pump"), simple, advanced });
}

function control(ctx) {
	const { h, model, boot } = ctx;
	const pumps = get_in(model, "network.pumps") || [];
	const simple = pumps
		.map(
			(p, i) => `<div class="fd-grid">
			${field(h, { path: `network.pumps.${i}.control`, label: h.__("Control for {0}", [p.id]), kind: "select", options: (boot.options && boot.options.controls) || [], value: p.control || "none" })}
			${p.control === "prv" ? field(h, { path: `network.pumps.${i}.setpoint_psi`, label: "PRV setpoint", kind: "number", unit: "psi", value: p.setpoint_psi }) : ""}
		</div>`
		)
		.join("");
	return card(h, {
		id: "control",
		title: h.__("Control"),
		section: "control",
		full: true,
		model,
		presets: preset_list(ctx, "control"),
		// The drive or valve that does the controlling (a vfd or prv slot) sits with the choice it serves.
		simple: (simple || `<p class="fd-muted">${h.esc(h.__("No pump yet."))}</p>`) + equipment_rows(ctx, "control"),
	});
}

// The equipment rows of one section (model.equipment, by slot), each a catalog pick and a quantity.
function equipment_rows(ctx, section) {
	const { h, model } = ctx;
	const classes = (ctx.boot.catalogs && ctx.boot.catalogs.slot_classes) || {};
	return (model.equipment || [])
		.map((row, i) => ({ row, i }))
		.filter(({ row }) => SLOT_SECTION[row && row.slot] === section)
		.map(
			({ row, i }) => `<div class="fd-grid">
				${item_field(h, `equipment.${i}.item`, slot_label(row.slot), classes[row.slot], row.item)}
				${field(h, { path: `equipment.${i}.qty`, label: "Quantity", kind: "int", value: row.qty })}
			</div>`
		)
		.join("");
}

function slot_label(slot) {
	return (
		{
			bypass: "Bypass valve",
			surge_arrestor: "Surge arrestor",
			filter: "Filter",
			uv: "UV",
			chlorinator: "Chlorinator",
			light: "Light",
			transformer: "Transformer",
			autofill: "Autofill",
			vfd: "VFD",
			prv: "PRV",
		}[slot] || slot
	);
}

// The catalog class a slot searches comes from boot.catalogs.slot_classes (README, the contract). A slot
// the server names no class for is an item code typed in, never a search of some other class.
function item_field(h, path, label, equipment_class, value) {
	if (equipment_class) {
		return field(h, { path, label: h.__(label), kind: "catalog", equipment_class, value: value || "", placeholder: "Generic, pick later" });
	}
	return field(h, { path, label: h.__(label), kind: "text", value: value || "", blank_null: true, placeholder: h.__("Item code, or blank for generic") });
}

function equipment_card(ctx, section, title, lede) {
	const { h, model } = ctx;
	const rows = equipment_rows(ctx, section);
	return card(h, {
		id: section,
		title,
		section,
		full: true,
		model,
		presets: preset_list(ctx, section),
		simple: `<p class="fd-hint">${h.esc(lede)}</p>${rows || `<p class="fd-muted">${h.esc(h.__("None needed."))}</p>`}`,
	});
}

function treatment(ctx) {
	const { h, model, boot } = ctx;
	const t = model.treatment || {};
	return card(h, {
		id: "treatment",
		title: h.__("Treatment"),
		section: "treatment",
		full: true,
		model,
		presets: preset_list(ctx, "treatment"),
		open: ctx.adv("treatment"),
		simple: `<div class="fd-grid">
				${field(h, { path: "treatment.sanitizer", label: "Sanitizer", kind: "select", options: (boot.options && boot.options.sanitizers) || [], value: t.sanitizer })}
			</div>
			${field(h, { path: "treatment.uv", label: "UV (required on interactive features)", kind: "check", value: t.uv })}`,
		advanced: equipment_rows(ctx, "treatment") || `<p class="fd-muted">${h.esc(h.__("No treatment equipment yet."))}</p>`,
	});
}

function lighting(ctx) {
	const { h, model, boot } = ctx;
	const l = model.lighting || {};
	return card(h, {
		id: "lighting",
		title: h.__("Lighting"),
		section: "lighting",
		full: true,
		model,
		presets: preset_list(ctx, "lighting"),
		open: ctx.adv("lighting"),
		simple: field(h, { path: "lighting.control", label: "Control", kind: "select", options: (boot.options && boot.options.lighting_controls) || [], value: l.control }),
		advanced: equipment_rows(ctx, "lighting") || `<p class="fd-muted">${h.esc(h.__("No fixtures yet."))}</p>`,
	});
}

function electrical(ctx) {
	const { h, model, boot } = ctx;
	const e = model.electrical || {};
	return card(h, {
		id: "electrical",
		title: h.__("Electrical"),
		section: "electrical",
		full: true,
		model,
		presets: preset_list(ctx, "electrical"),
		simple: `<div class="fd-grid">
			${field(h, { path: "electrical.service_v", label: "Service", kind: "select", options: ((boot.options && boot.options.service_voltages) || []).map((v) => ({ value: v, label: v + " V" })), value: e.service_v })}
			${field(h, { path: "electrical.phase", label: "Phase", kind: "select", options: [{ value: 1, label: "Single" }, { value: 3, label: "Three" }], value: e.phase })}
			${field(h, { path: "electrical.panel_distance_ft", label: "Panel to the pump", kind: "number", unit: "ft", value: e.panel_distance_ft })}
		</div>`,
	});
}

function winterization(ctx) {
	const { h, model } = ctx;
	const w = (ctx.reply && ctx.reply.outputs && ctx.reply.outputs.winterization) || null;
	const steps = w && Array.isArray(w.steps) && w.steps.length ? `<ol class="fd-list">${w.steps.map((s) => `<li>${h.esc(s)}</li>`).join("")}</ol>` : "";
	return card(h, {
		id: "winterization",
		title: h.__("Winterization"),
		section: "winterization",
		full: true,
		model,
		presets: preset_list(ctx, "winterization"),
		simple: steps || `<p class="fd-muted">${h.esc(h.__("Worked out from the routes and the frost depth."))}</p>`,
	});
}
