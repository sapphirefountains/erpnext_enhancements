// Markup helpers every step shares: a field bound to a model path, a settings card with its setup policy
// and an Advanced reveal (the rain curtain's setupui.card), choice tiles, and a derived value. Pure:
// strings in, strings out. The page binds every input by delegation on data-fd-* attributes, so a step
// never wires its own listeners.
//
// h: { esc, __, chip }.

import { get_in, match_preset, policy_of } from "./model.js";

export function field_id(path) {
	return "fd-f-" + String(path).replace(/[^\w-]/g, "-");
}

function options_html(h, options, value) {
	return (options || [])
		.map((o) => {
			const v = typeof o === "object" && o !== null ? o.value : o;
			const label = typeof o === "object" && o !== null ? o.label : o;
			const on = String(v) === String(value == null ? "" : value) ? " selected" : "";
			return `<option value="${h.esc(v)}"${on}>${h.esc(h.__(String(label)))}</option>`;
		})
		.join("");
}

// f: { path, label, kind (number | int | text | select | check | textarea | catalog), value, unit,
// options, hint, derived: { key, unit, digits } , readonly, placeholder, equipment_class, blank }
export function field(h, f) {
	const { esc, __ } = h;
	const id = field_id(f.path);
	const kind = f.kind || "text";
	const value = f.value;
	const ro = f.readonly ? " disabled" : "";
	const hint = f.hint ? `<p class="fd-hint" id="${id}-hint">${esc(__(f.hint))}</p>` : "";
	const described = f.hint ? ` aria-describedby="${id}-hint"` : "";
	// blank_null: an emptied box is null (an Item left generic), not "".
	const data = `data-fd-path="${esc(f.path)}" data-fd-kind="${esc(kind)}"${f.blank_null ? " data-fd-null" : ""}`;
	let control;
	if (kind === "select") {
		const blank = f.blank ? `<option value="">${esc(__(f.blank))}</option>` : "";
		// A select whose choices are numbers (a voltage, a phase) stores a number, not its text.
		const opts = f.options || [];
		const numeric = opts.length && opts.every((o) => typeof (o !== null && typeof o === "object" ? o.value : o) === "number");
		const cast = numeric ? ' data-fd-cast="number"' : "";
		control = `<select class="fd-input" id="${id}" ${data}${cast}${described}${ro}>${blank}${options_html(h, opts, value)}</select>`;
	} else if (kind === "check") {
		return `<div class="fd-field fd-field--check">
			<label class="fd-check" for="${id}"><input type="checkbox" id="${id}" ${data}${value ? " checked" : ""}${described}${ro}>
			<span>${esc(__(f.label))}</span></label>${hint}</div>`;
	} else if (kind === "textarea") {
		control = `<textarea class="fd-input fd-textarea" id="${id}" rows="3" ${data}${described}${ro}>${esc(value)}</textarea>`;
	} else if (kind === "catalog") {
		control = `<div class="fd-picker">
			<input type="text" class="fd-input" id="${id}" ${data} data-fd-catalog="${esc(f.equipment_class)}" role="combobox"
				aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-list" autocomplete="off"
				value="${esc(value)}" placeholder="${esc(__(f.placeholder || "Search the catalog"))}"${described}${ro}>
			<ul class="fd-picker-list" id="${id}-list" role="listbox" hidden></ul>
		</div>`;
	} else {
		const mode = kind === "number" ? ' inputmode="decimal"' : kind === "int" ? ' inputmode="numeric"' : "";
		const shown = value === null || value === undefined ? "" : value;
		const ph = f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "";
		control = `<input type="text" class="fd-input" id="${id}" ${data}${mode} value="${esc(shown)}"${ph}${described}${ro}>`;
	}
	const unit = f.unit ? `<span class="fd-unit">${esc(__(f.unit))}</span>` : "";
	return `<div class="fd-field">
		<label class="fd-label" for="${id}">${esc(__(f.label))}</label>
		<div class="fd-control">${control}${unit}</div>
		${f.derived ? derived(h, f.derived.key, f.derived.value, f.derived) : ""}
		${hint}
	</div>`;
}

// A value the engine worked out, repainted in place after every derive (data-fd-derived is the key into
// reply.values).
export function derived(h, key, value, spec) {
	const s = spec || {};
	const text = fmt(value, s);
	return `<p class="fd-derived"><span class="fd-derived-label">${h.esc(h.__(s.label || "Worked out"))}:</span>
		<span data-fd-derived="${h.esc(key)}" data-fd-unit="${h.esc(s.unit || "")}" data-fd-digits="${h.esc(
		s.digits == null ? 1 : s.digits
	)}">${h.esc(text || "—")}</span></p>`;
}

export function fmt(value, spec) {
	const s = spec || {};
	if (value === null || value === undefined || value === "") return "";
	const n = Number(value);
	if (!Number.isFinite(n)) return String(value);
	const digits = s.digits == null ? 1 : Number(s.digits);
	const text = n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
	return s.unit ? `${text} ${s.unit}` : text;
}

// Big choice tiles bound to one path (a radio group: arrow keys move between them).
export function tiles(h, path, items, value, label) {
	const { esc, __ } = h;
	const group = field_id(path);
	return `<div class="fd-field"><span class="fd-label" id="${group}-label">${esc(__(label || ""))}</span>
		<div class="fd-tiles" role="radiogroup" aria-labelledby="${group}-label">${(items || [])
			.map((it) => {
				const on = String(it.value) === String(value);
				return `<button type="button" class="fd-tile${on ? " fd-tile--on" : ""}" role="radio" aria-checked="${on}"
					tabindex="${on ? 0 : -1}" data-fd-tile="${esc(path)}" data-fd-value="${esc(it.value)}">
					<span class="fd-tile-label">${esc(__(it.label))}</span>
					${it.desc ? `<span class="fd-tile-desc">${esc(__(it.desc))}</span>` : ""}
				</button>`;
			})
			.join("")}</div></div>`;
}

// The policy select for a section: Auto (worked out), each preset, Custom. A section on a preset whose
// values no longer match shows Custom, the rain curtain's "Custom" detection.
export function policy_select(h, model, section, presets) {
	const { esc, __ } = h;
	const policy = policy_of(model, section);
	const matched = policy === "preset" ? match_preset(model, presets) : null;
	const current = policy === "auto" ? "auto" : matched ? "preset:" + matched : "custom";
	const opts = [{ value: "auto", label: __("Auto (worked out)") }]
		.concat((presets || []).map((p) => ({ value: "preset:" + p.key, label: __("Preset: {0}", [__(p.label)]) })))
		.concat([{ value: "custom", label: __("Custom (yours)") }]);
	const id = "fd-policy-" + section;
	return `<label class="fd-policy" for="${id}"><span class="fd-visually-hidden">${esc(__("Setup for this section"))}</span>
		<select class="fd-input fd-input--compact" id="${id}" data-fd-policy="${esc(section)}">${opts
			.map((o) => `<option value="${esc(o.value)}"${o.value === current ? " selected" : ""}>${esc(o.label)}</option>`)
			.join("")}</select></label>`;
}

// c: { id, title, section, simple, advanced, note, full, open, model, presets, actions }
export function card(h, c) {
	const { esc, __ } = h;
	const policy = c.section && c.full ? policy_select(h, c.model, c.section, c.presets) : "";
	const auto = c.section && policy_of(c.model, c.section) === "auto";
	const has_adv = c.full && c.advanced;
	const body_id = "fd-adv-" + c.id;
	const toggle = has_adv
		? `<button type="button" class="fd-adv-toggle${c.open ? " fd-adv-toggle--on" : ""}" aria-expanded="${c.open ? "true" : "false"}"
			aria-controls="${body_id}" data-fd-adv="${esc(c.id)}">${esc(__("Advanced"))}</button>`
		: "";
	const note = auto && c.full ? `<p class="fd-hint">${esc(__("Worked out for you. Changing a value here makes this section yours."))}</p>` : "";
	return `<section class="fd-card" data-fd-card="${esc(c.id)}"${c.section ? ` data-fd-section="${esc(c.section)}"` : ""}>
		<header class="fd-card-head">
			<h3 class="fd-card-title">${esc(c.title)}</h3>
			<div class="fd-card-tools">${policy}${toggle}${c.actions || ""}</div>
		</header>
		${note}
		<div class="fd-card-body">${c.simple || ""}</div>
		${has_adv ? `<div class="fd-adv-body" id="${body_id}"${c.open ? "" : " hidden"}>${c.adv_note ? `<p class="fd-hint">${esc(__(c.adv_note))}</p>` : ""}${c.advanced}</div>` : ""}
	</section>`;
}

// A stage heading and lede: the heading takes focus when the step opens (keyboard and screen reader land
// on the step, not the top of the Desk).
export function stage_head(h, title, lede) {
	return `<h2 class="fd-stage-title" tabindex="-1" data-fd-stage-title>${h.esc(title)}</h2>${
		lede ? `<p class="fd-lede">${h.esc(lede)}</p>` : ""
	}`;
}

// The value at a path, for a field.
export const at = (model, path) => get_in(model, path);
