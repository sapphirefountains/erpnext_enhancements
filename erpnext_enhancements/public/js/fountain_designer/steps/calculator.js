// The Calculator drawer on the Review step: one engine calculation on its own, the quick calculator of the
// retired Water Engineering Wizard (v1.771.0, phase H). boot().calculator lists the calcs and their inputs
// (water_engineering/calculator.py); Work it out sends run_calc and the drawer shows the CalcResult whole:
// the value, the formula, every step, the sources, the warnings and the options. Nothing here touches the
// design: the fields are bound under the "calc." path prefix, which the page keeps apart from the model.
//
// Pure: strings in, strings out, and the input coercion the page sends with.

import { card, field, fmt } from "../ui.js";

export const DRAWER = "calculator";
export const PREFIX = "calc.";
export const PICK = PREFIX + "_pick";

// A calc input's kind onto ui.field's: a list the engine takes whole is typed as JSON in a text box.
const KINDS = { number: "number", int: "int", select: "select", check: "check", text: "text", json: "textarea" };

export function calcs(boot) {
	return (boot && Array.isArray(boot.calculator) ? boot.calculator : []).filter((c) => c && c.key);
}

export function spec(boot, key) {
	const list = calcs(boot);
	return list.find((c) => c.key === key) || list[0] || null;
}

// What each field shows for a calc that has just been picked: its defaults, a JSON list as indented text.
export function default_raw(s) {
	const raw = {};
	for (const input of (s && s.inputs) || []) {
		const d = input.default;
		if (input.kind === "json") raw[input.key] = d == null ? "" : JSON.stringify(d, null, 1);
		else if (input.kind === "check") raw[input.key] = Boolean(d);
		else raw[input.key] = d == null ? "" : d;
	}
	return raw;
}

// The fields' text to the inputs run_calc takes: { inputs, errors: [label] }. A blank box is left out, so
// the engine uses its own default; a number or a JSON list that does not read is an error, not a guess.
export function coerce(s, raw) {
	const inputs = {};
	const errors = [];
	for (const input of (s && s.inputs) || []) {
		const value = raw ? raw[input.key] : undefined;
		if (input.kind === "check") {
			inputs[input.key] = Boolean(value);
			continue;
		}
		const text = value == null ? "" : String(value).trim();
		if (!text) continue;
		if (input.kind === "number" || input.kind === "int") {
			const n = Number(text.replace(/,/g, ""));
			if (!Number.isFinite(n) || (input.kind === "int" && !Number.isInteger(n))) errors.push(input.label);
			else inputs[input.key] = n;
		} else if (input.kind === "json") {
			try {
				const parsed = JSON.parse(text);
				if (!Array.isArray(parsed)) throw new Error("not a list");
				inputs[input.key] = parsed;
			} catch (e) {
				errors.push(input.label);
			}
		} else {
			// A select of numbers (none today) would come back as text; the engine reads text for every select.
			inputs[input.key] = text;
		}
	}
	return { inputs, errors };
}

// ctx.calc: { key, raw, result, error, busy }.
export function render(ctx) {
	const { h, boot } = ctx;
	const { esc, __ } = h;
	const list = calcs(boot);
	const state = ctx.calc || {};
	const body = !list.length
		? h.state_html("empty", { title: __("No calculator here."), body: __("This site's designer did not send the list of calculations.") })
		: form_html(ctx, list, spec(boot, state.key), state);
	return `<details class="fd-drawer fd-calc" data-fd-drawer="${DRAWER}"><summary>${esc(__("Calculator"))}</summary>
		<div class="fd-drawer-body">${body}</div></details>`;
}

function form_html(ctx, list, s, state) {
	const { h } = ctx;
	const { esc, __ } = h;
	const raw = state.raw || default_raw(s);
	const pick = field(h, {
		path: PICK,
		label: "Calculation",
		kind: "select",
		value: s.key,
		options: list.map((c) => ({ value: c.key, label: c.group ? `${c.label} (${c.group})` : c.label })),
	});
	const inputs = (s.inputs || [])
		.map((input) =>
			field(h, {
				path: PREFIX + input.key,
				label: input.label,
				kind: KINDS[input.kind] || "text",
				value: raw[input.key],
				unit: input.unit,
				options: input.options,
				hint: input.kind === "json" ? "A JSON list, like the example." : "",
			})
		)
		.join("");
	return `<p class="fd-hint">${esc(__("One engine calculation on its own, with its working. Nothing here changes the design."))}</p>
		<form class="fd-calc-form" data-fd-calc-form novalidate>
			${pick}
			${s.note ? `<p class="fd-hint">${esc(__(s.note))}</p>` : ""}
			<div class="fd-calc-inputs">${inputs}</div>
			<div class="fd-actions">
				<button type="submit" class="sf-btn sf-btn--primary"${state.busy ? " disabled" : ""}>${esc(
		state.busy ? __("Working it out…") : __("Work it out")
	)}</button>
			</div>
		</form>
		<div class="fd-calc-result" aria-live="polite">${result_html(ctx, s, state)}</div>`;
}

function shown(value, unit) {
	if (value === null || value === undefined || value === "") return "—";
	if (typeof value === "number") return fmt(value, { digits: Number.isInteger(value) ? 0 : 2, unit });
	if (typeof value === "object") return JSON.stringify(value);
	return unit ? `${value} ${unit}` : String(value);
}

export function result_html(ctx, s, state) {
	const { h } = ctx;
	const { esc, __ } = h;
	if (state.error) return h.state_html("error", { title: __("That did not work out."), body: state.error });
	const r = state.result;
	if (!r || r.calc !== s.key) return "";
	const list = (items, cls) => (items && items.length ? `<ul class="${cls}">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
	const steps = r.steps && r.steps.length ? `<ol class="fd-calc-steps">${r.steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : "";
	const options = (r.options || []).length
		? `<div class="fd-table-wrap"><table class="fd-table"><caption class="fd-visually-hidden">${esc(__("Options"))}</caption>
			<thead><tr><th scope="col">${esc(__("Option"))}</th><th scope="col">${esc(__("Value"))}</th><th scope="col">${esc(__("Detail"))}</th></tr></thead>
			<tbody>${r.options
				.map(
					(o) => `<tr${o.recommended ? ' class="fd-calc-pick"' : ""}><th scope="row">${esc(o.label)}${
						o.recommended ? " " + h.chip(__("Recommended"), "good") : ""
					}</th><td>${esc(shown(o.value, ""))}</td><td>${esc(
						Object.entries(o.detail || {})
							.map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "number" ? fmt(v, { digits: 2 }) : v}`)
							.join("; ")
					)}</td></tr>`
				)
				.join("")}</tbody></table></div>`
		: "";
	const demo = r.demo_inputs
		? `<p class="fd-hint">${esc(__("Demo data: worked out for the example inputs {0}, whatever was typed.", [JSON.stringify(r.demo_inputs)]))}</p>`
		: "";
	const simple = `${demo}
		<p class="fd-calc-value"><span class="fd-calc-big">${esc(shown(r.value, r.unit))}</span>${r.status ? " " + h.chip(r.status, "neutral") : ""}</p>
		${r.formula ? `<p class="fd-calc-formula"><span class="fd-derived-label">${esc(__("Formula"))}:</span> <code>${esc(r.formula)}</code></p>` : ""}
		${steps ? `<h4 class="fd-calc-head">${esc(__("Working"))}</h4>${steps}` : ""}
		${(r.warnings || []).length ? `<h4 class="fd-calc-head">${esc(__("Warnings"))}</h4>${list(r.warnings, "fd-calc-warnings")}` : ""}
		${options ? `<h4 class="fd-calc-head">${esc(__("Options"))}</h4>${options}` : ""}
		${(r.citations || []).length ? `<p class="fd-muted">${esc(__("Sources: {0}", [r.citations.join("; ")]))}</p>` : ""}`;
	return card(h, { id: "calc-result", title: __(s.label), simple });
}
