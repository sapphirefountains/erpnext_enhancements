/*
 * form kit: how a saved record is laid out on the guided page, chosen by each person (v1.684.0).
 *
 * Nik, 2026-10-14: "once a document has been created via the flow, the presentation could be
 * better ... somewhat flexible and customizable by the user and their preferences", and "all
 * sections on one page, but leave the ability for tabs to be used and visible ... Preferences
 * should be saved to their instance and not change it for everyone else's views." Later the same
 * day: pinned sections, a landing section, per-person hidden fields and sections, the last section
 * per record, text size and contrast, focus mode, summary fields, shortcuts and presets.
 *
 * A layout is a presentation of the flow, never a different form: every layout draws the same
 * steps, with the same fields and the same controls, through the record editor's one renderer
 * (ui/editor.js render_fields). Nothing here changes what is saved or how.
 *
 * What a person may choose, per DocType (`prefs`, every key an enum; the first value is the default):
 *
 *   layout    page (every step as a section on one page, under its tab) | tabs (one tab at a
 *             time) | steps (one step at a time, the setup flow's own page)
 *   nav       where the steps (or tabs) sit: left | right | top. Always on top on a phone.
 *   summary   right | left | hidden. Collapsible on a phone.
 *   notes     notes and comments: below the form | side column | collapsed behind a bar
 *   tools     the record tools (.ge-tools): side column | below the form | collapsed
 *   density   comfortable | compact
 *   hints     the hint under each field: shown | hidden (it moves into the (i), which always stays)
 *   fields    all | filled (only fields with a value; a required field always shows)
 *   text      normal | large | larger (the type scale, through the CSS tokens)
 *   contrast  normal | high (stronger text and rules, for a phone in sunlight)
 *   focus     off | on (only the form and its nav: no summary, no side column, no hints)
 *
 * and, beside them (`entry`):
 *
 *   pinned          step keys drawn first, at the top of One page and Tabs, marked in the nav
 *   landing         where a record opens: "last" (where this person left it), "first", or a step
 *   hidden_fields   fields this person has put away; never a required field that is empty, never
 *                   one the record's stage still needs (completeness.missing)
 *   hidden_steps    sections put away, in One page and Tabs, on the same terms
 *   summary_fields  the fields the summary shows, or null for the DocType's own summary
 *   presets         named sets of `prefs` ("My setup", "Phone"), at most MAX_PRESETS
 *   last            [[record name, step key]], newest first, at most MAX_LAST
 *
 * A new record's setup flow is always SETUP_VIEW: the choices are for a record that exists. The
 * server keeps the same schema (api/planner_views_prefs.py GUIDED_SCHEMA and the caps, held equal by
 * tests/test_guided_view_settings.py) in the person's own __UserSettings row, never in the browser.
 *
 * Pure: no frappe, no DOM.
 */

export const VIEW_SCHEMA = {
	layout: ["page", "tabs", "steps"],
	nav: ["left", "right", "top"],
	summary: ["right", "left", "hidden"],
	notes: ["below", "side", "collapsed"],
	tools: ["side", "below", "collapsed"],
	density: ["comfortable", "compact"],
	hints: ["shown", "hidden"],
	fields: ["all", "filled"],
	text: ["normal", "large", "larger"],
	contrast: ["normal", "high"],
	focus: ["off", "on"],
};

export const MAX_PINNED = 20;
export const MAX_HIDDEN = 200;
export const MAX_SUMMARY_FIELDS = 12;
export const MAX_PRESETS = 10;
export const MAX_PRESET_NAME = 40;
export const MAX_LAST = 50;
export const LANDINGS = ["last", "first"];
// A step key or a fieldname, as the server accepts one.
const KEY_RE = /^[A-Za-z0-9_]{1,140}$/;

export const VIEW_DEFAULTS = Object.freeze(
	Object.keys(VIEW_SCHEMA).reduce((out, key) => {
		out[key] = VIEW_SCHEMA[key][0];
		return out;
	}, {})
);

// What a new record's step-by-step setup flow shows, whatever the person chose for saved ones:
// the page exactly as it was before view settings existed.
export const SETUP_VIEW = Object.freeze(Object.assign({}, VIEW_DEFAULTS, { layout: "steps" }));

// Layouts that draw several steps at once, each in its own section.
export const SECTION_LAYOUTS = ["page", "tabs"];

function own(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

// Every key of the schema, each a valid value: an unknown or invalid one falls back to its default.
export function clean_view(raw) {
	const source = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
	const out = {};
	Object.keys(VIEW_SCHEMA).forEach((key) => {
		const value = source[key];
		out[key] = typeof value === "string" && VIEW_SCHEMA[key].includes(value) ? value : VIEW_DEFAULTS[key];
	});
	return out;
}

// A list of keys (step keys or fieldnames): strings of the allowed shape, each once, at most `max`.
export function clean_keys(raw, max) {
	const out = [];
	(Array.isArray(raw) ? raw : []).forEach((value) => {
		if (typeof value !== "string") return;
		const key = text(value);
		if (KEY_RE.test(key) && !out.includes(key) && out.length < max) out.push(key);
	});
	return out;
}

export function clean_landing(raw) {
	const value = text(raw);
	return LANDINGS.includes(value) || KEY_RE.test(value) ? value : "last";
}

export function clean_presets(raw) {
	const out = [];
	(Array.isArray(raw) ? raw : []).forEach((preset) => {
		if (!preset || typeof preset !== "object") return;
		const name = text(preset.name).replace(/\s+/g, " ").slice(0, MAX_PRESET_NAME).trim();
		if (!name || out.some((p) => p.name.toLowerCase() === name.toLowerCase()) || out.length >= MAX_PRESETS) return;
		out.push({ name, prefs: clean_view(preset.prefs) });
	});
	return out;
}

export function clean_last(raw) {
	const out = [];
	(Array.isArray(raw) ? raw : []).forEach((pair) => {
		if (!Array.isArray(pair) || pair.length < 2) return;
		const name = text(pair[0]).slice(0, 140);
		const step = text(pair[1]);
		if (!name || !KEY_RE.test(step) || out.some((p) => p[0] === name) || out.length >= MAX_LAST) return;
		out.push([name, step]);
	});
	return out;
}

// One DocType's entry as the page uses it, whatever the store held.
export function clean_entry(raw) {
	const r = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
	return {
		prefs: clean_view(r.prefs),
		pinned: clean_keys(r.pinned, MAX_PINNED),
		landing: clean_landing(r.landing),
		hidden_fields: clean_keys(r.hidden_fields, MAX_HIDDEN),
		hidden_steps: clean_keys(r.hidden_steps, MAX_HIDDEN),
		summary_fields: Array.isArray(r.summary_fields) ? clean_keys(r.summary_fields, MAX_SUMMARY_FIELDS) : null,
		presets: clean_presets(r.presets),
		last: clean_last(r.last),
	};
}

// The person's entry for `doctype`, from what get_guided_view returned ({intro_seen, doctypes}).
export function entry_for(store, doctype) {
	const doctypes = (store && store.doctypes) || {};
	return clean_entry(own(doctypes, doctype) ? doctypes[doctype] : null);
}

export function view_for(store, doctype) {
	return entry_for(store, doctype).prefs;
}

// What the page shows: the choices for a saved record, the setup flow for a new one. `steps_only`
// keeps a DocType whose flow draws step blocks (form kit P8.1, the Item flow's name builder and its
// supplier, stock and asset blocks) on Step by step whatever the person chose: those blocks hold one
// step's state at a time, and the flow ends at its core steps with Done and More details, which
// only Step by step has. Every other choice still applies.
export function effective_view(prefs, is_new, steps_only) {
	if (is_new) return Object.assign({}, SETUP_VIEW);
	const view = clean_view(prefs);
	if (steps_only) view.layout = "steps";
	return view;
}

export function same_view(a, b) {
	const x = clean_view(a);
	const y = clean_view(b);
	return Object.keys(VIEW_SCHEMA).every((key) => x[key] === y[key]);
}

export function is_default_view(view) {
	return same_view(view, VIEW_DEFAULTS);
}

// The attributes the shell carries (guided_entry.bundle.css lays the page out from them). `aside`
// is the side column: the summary's side when it shows, else the right when notes or tools sit in
// it, else none. Focus mode keeps only the form and its nav: no side column, no hints, and notes
// and tools behind their bars (still there, one tap away).
export function shell_attrs(view) {
	const v = clean_view(view);
	const focus = v.focus === "on";
	const notes = focus ? "collapsed" : v.notes;
	const tools = focus ? "collapsed" : v.tools;
	const summary = focus ? "hidden" : v.summary;
	let aside = "none";
	if (summary !== "hidden") aside = summary;
	else if (notes === "side" || tools === "side") aside = "right";
	return {
		layout: v.layout,
		nav: v.nav,
		aside,
		summary,
		notes,
		tools,
		density: v.density,
		hints: focus ? "hidden" : v.hints,
		fields: v.fields,
		text: v.text,
		contrast: v.contrast,
		focus: v.focus,
	};
}

// ------------------------------------------------------------------ tabs, pins and sections

export const PINNED = "pinned";

// The steps grouped by their tab (the Desk form's tab, `step.tab`), tabs in the order they first
// appear and steps in flow order within each:
//   [{ key: "t0", label, steps: [{ key, title, active }], active }]
// Every step is listed, active or not: a step that stops applying is hidden, not forgotten. A step
// with no tab is a tab of its own, labelled with its title. Pinned steps (`pinned`) are left out of
// their tab: they draw in the pinned group (pinned_group) instead, never twice.
export function view_tabs(steps, values, step_active, pinned) {
	const skip = new Set(pinned || []);
	const tabs = [];
	const by_label = {};
	(steps || []).forEach((step) => {
		if (!step || !step.key || skip.has(step.key)) return;
		const label = text(step.tab) || String(step.title || step.key);
		let tab = by_label[label];
		if (!tab) {
			tab = by_label[label] = { key: `t${tabs.length}`, label, steps: [], active: false };
			tabs.push(tab);
		}
		const active = typeof step_active === "function" ? !!step_active(step, values) : true;
		tab.steps.push({ key: step.key, title: step.title || step.key, active });
		if (active) tab.active = true;
	});
	return tabs;
}

// The pinned steps, in flow order, as a group of their own; null when nothing is pinned.
export function pinned_group(steps, values, step_active, pinned, label) {
	const keys = new Set(pinned || []);
	const list = (steps || [])
		.filter((step) => step && keys.has(step.key))
		.map((step) => ({
			key: step.key,
			title: step.title || step.key,
			active: typeof step_active === "function" ? !!step_active(step, values) : true,
		}));
	if (!list.length) return null;
	return { key: PINNED, label: label || "Pinned", steps: list, active: list.some((step) => step.active), pinned: true };
}

export function tab_of(tabs, step_key) {
	return (tabs || []).find((tab) => tab.steps.some((step) => step.key === step_key)) || null;
}

// The tab's first active step: the route a tab is reached by.
export function tab_step(tab) {
	const first = ((tab && tab.steps) || []).find((step) => step.active);
	return first ? first.key : null;
}

// The active tab before (dir -1) or after (dir 1) `tab_key`, or null at either end.
export function neighbor_tab(tabs, tab_key, dir) {
	const all = tabs || [];
	const at = all.findIndex((tab) => tab.key === tab_key);
	if (at < 0) return null;
	for (let i = at + dir; i >= 0 && i < all.length; i += dir) {
		if (all[i].active) return all[i];
	}
	return null;
}

// How many required-for-the-stage fields are empty in the tab's active steps.
export function tab_missing(tab, missing_by_step) {
	return ((tab && tab.steps) || []).reduce(
		(sum, step) => sum + (step.active ? ((missing_by_step || {})[step.key] || []).length : 0),
		0
	);
}

// The steps a section layout draws, in drawing order: the pinned group first, then every tab
// (page) or the one tab shown (tabs). Active or not: the page hides the inactive ones.
export function section_keys(tabs, layout, tab_key, pinned) {
	const out = [];
	if (pinned) pinned.steps.forEach((step) => out.push(step.key));
	(tabs || []).forEach((tab) => {
		if (layout === "tabs" && tab.key !== tab_key) return;
		tab.steps.forEach((step) => out.push(step.key));
	});
	return out;
}

// The section the person moves to with "next section" (dir 1) or "previous" (dir -1): among the
// sections drawn and showing, in page order. null at either end.
export function neighbor_section(keys, current, dir) {
	const list = keys || [];
	const at = list.indexOf(current);
	if (at < 0) return list.length ? list[dir > 0 ? 0 : list.length - 1] : null;
	const next = at + dir;
	return next >= 0 && next < list.length ? list[next] : null;
}

// Scroll-spy: the section the person is reading. `sections` is [{ key, top }] in page order, `top`
// each section's top against the viewport; `line` is how far down the viewport reading starts
// (under the sticky bars). The last section whose top has reached the line, else the first.
export function spy_pick(sections, line) {
	const list = (sections || []).filter((s) => s && s.key && typeof s.top === "number" && !Number.isNaN(s.top));
	if (!list.length) return null;
	let pick = list[0].key;
	list.forEach((section) => {
		if (section.top <= line) pick = section.key;
	});
	return pick;
}

// Pin or unpin a step; a list never grows past MAX_PINNED.
export function toggle_key(list, key, on, max) {
	const out = (list || []).filter((k) => k !== key);
	if (on && out.length < (max || MAX_PINNED)) out.push(key);
	return out;
}

// ------------------------------------------------------------------ where a record opens

// The last section this person had open on each record, newest first, at most MAX_LAST records.
export function remember_last(last, name, step) {
	const record = text(name);
	const key = text(step);
	if (!record || !KEY_RE.test(key)) return clean_last(last);
	return clean_last([[record, key]].concat((last || []).filter((pair) => Array.isArray(pair) && pair[0] !== record)));
}

export function last_step(last, name) {
	const pair = (last || []).find((p) => Array.isArray(p) && p[0] === name);
	return pair ? pair[1] : null;
}

// The step a record opens on when its address names none: the landing the person chose ("last":
// where they left this record; "first"; or a step), if it exists and applies. null means the first.
export function start_step(entry, name, steps, values, step_active) {
	const e = entry || {};
	const landing = clean_landing(e.landing);
	let key = null;
	if (landing === "last") key = last_step(e.last, name);
	else if (landing !== "first") key = landing;
	if (!key) return null;
	const step = (steps || []).find((s) => s && s.key === key);
	if (!step) return null;
	if (typeof step_active === "function" && !step_active(step, values)) return null;
	return key;
}

// ------------------------------------------------------------------ what shows

const LAYOUT = ["Section Break", "Column Break", "Tab Break"];
const NUMBER_TYPES = ["Int", "Float", "Currency", "Percent", "Rating", "Duration"];

// Whether a field holds something worth showing in "only fields with a value". Zero is no value
// for a number (a new record's 0 amount is not something anyone entered), unticked for a Check.
export function has_value(def, value) {
	if (value === null || value === undefined) return false;
	if (Array.isArray(value)) return value.length > 0;
	const type = (def && def.fieldtype) || "";
	if (type === "Check") return !!Number(value);
	if (NUMBER_TYPES.includes(type)) {
		const number = Number(value);
		return !Number.isNaN(number) && number !== 0;
	}
	return String(value).trim() !== "";
}

// A field the page must show whatever the person chose: required (or required by a condition)
// and empty, or one the record's stage still needs (completeness.missing).
export function must_show(def, value, missing) {
	if (!def) return true;
	if (missing && missing.has && missing.has(def.fieldname)) return true;
	if ((def.reqd || def.mandatory_depends_on) && !has_value(def, value)) return true;
	return false;
}

// "Only fields with a value" hides a field only when it is empty and nothing can require it, so
// nobody is asked for a value they cannot see.
export function hidden_when_filled_only(def, value) {
	if (!def) return false;
	if (def.reqd || def.mandatory_depends_on) return false;
	return !has_value(def, value);
}

// Whether one field is put away right now: by the person's own list (unless it must show), or by
// "only fields with a value". `reveal` (the "Show hidden" toggle) shows the person's list again;
// `revealed` (a Set) names single fields that stay in view for the rest of the visit: brought into
// view to be filled in, or edited by the person. Once someone has typed in a field it never goes
// away under them, whatever the next save says the stage needs (review of #1299).
//
// The page asks again whenever the record moves (a save's new completeness, a status change), so a
// field that has just become required or stage-needed comes back at once. "Only fields with a value"
// is the exception that must not follow every keystroke: a field the person empties while typing
// would vanish under the cursor. So the page passes `filled_snapshot`, the Set of fields that were
// empty when the person chose it (or the section was drawn), and only those stay put away, and only
// while they are still empty and not needed.
export function field_hidden(def, value, opts) {
	const o = opts || {};
	if (!def) return { hidden: false, by: null };
	if (o.revealed && o.revealed.has && o.revealed.has(def.fieldname)) return { hidden: false, by: null };
	if (!o.reveal && (o.hidden_fields || []).includes(def.fieldname) && !must_show(def, value, o.missing)) {
		return { hidden: true, by: "person" };
	}
	const snapshot = o.filled_snapshot;
	const in_snapshot = !snapshot || (snapshot.has && snapshot.has(def.fieldname));
	if (o.filled_only && in_snapshot && hidden_when_filled_only(def, value) && !must_show(def, value, o.missing)) {
		return { hidden: true, by: "filled" };
	}
	return { hidden: false, by: null };
}

// The fields "only fields with a value" puts away as things stand: { fieldname } for every value
// field of `steps` that is empty and that nothing can require. The page keeps it as the snapshot.
export function filled_snapshot(steps, values) {
	const out = new Set();
	const v = values || {};
	(steps || []).forEach((step) =>
		((step && step.fields) || []).forEach((field) => {
			if (!field || !field.fieldname || LAYOUT.includes(field.fieldtype)) return;
			if (hidden_when_filled_only(field, v[field.fieldname])) out.add(field.fieldname);
		})
	);
	return out;
}

// Whether a section is put away: listed by the person, and none of its fields must show, nor was
// edited on this visit (`revealed`).
export function step_hidden(step, values, opts) {
	const o = opts || {};
	if (o.reveal || !step || !(o.hidden_steps || []).includes(step.key)) return false;
	const v = values || {};
	const revealed = o.revealed && o.revealed.has ? o.revealed : null;
	return !(step.fields || []).some(
		(field) =>
			field && field.fieldname && (must_show(field, v[field.fieldname], o.missing) || (revealed && revealed.has(field.fieldname)))
	);
}

// Whether the person may put a field away at all (the settings list greys out the ones they may not).
export function can_hide(def, value, missing) {
	return !!def && !must_show(def, value, missing);
}

// ------------------------------------------------------------------ presets

// `presets` with `prefs` saved as `name` (replacing one of the same name, case aside); refused (the
// list unchanged, `ok` false) past MAX_PRESETS or with no name.
export function save_preset(presets, name, prefs) {
	const list = clean_presets(presets);
	const clean = text(name).replace(/\s+/g, " ").slice(0, MAX_PRESET_NAME).trim();
	if (!clean) return { ok: false, presets: list, reason: "name" };
	const rest = list.filter((p) => p.name.toLowerCase() !== clean.toLowerCase());
	if (rest.length >= MAX_PRESETS) return { ok: false, presets: list, reason: "full" };
	rest.push({ name: clean, prefs: clean_view(prefs) });
	rest.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
	return { ok: true, presets: rest, name: clean };
}

export function delete_preset(presets, name) {
	return clean_presets(presets).filter((p) => p.name.toLowerCase() !== text(name).toLowerCase());
}

// The preset whose settings are the ones showing, or null.
export function current_preset(presets, prefs) {
	return clean_presets(presets).find((p) => same_view(p.prefs, prefs)) || null;
}

// ------------------------------------------------------------------ keyboard

// The page's shortcuts. None uses Ctrl, Alt or Cmd except Save (the browser's own Save page is
// what Ctrl/Cmd+S would otherwise do here, and the Desk's Ctrl+S has no primary action on this
// page), and none fires while typing in a field, except Save.
export const SHORTCUTS = [
	{ action: "next", keys: ["j"], label: "J" },
	{ action: "prev", keys: ["k"], label: "K" },
	{ action: "save", keys: [], label: "Ctrl+S / ⌘S" },
	{ action: "comments", keys: ["c"], label: "C" },
	{ action: "settings", keys: ["v"], label: "V" },
	{ action: "focus", keys: ["f"], label: "F" },
	{ action: "help", keys: ["?"], label: "?" },
];

// The action a key press asks for, or null. `e` is { key, ctrlKey, metaKey, altKey, shiftKey };
// `typing` says the press landed in a field (an input, a textarea, a select, an editor).
export function shortcut_for(e, typing) {
	if (!e || typeof e.key !== "string") return null;
	const key = e.key;
	if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && key.toLowerCase() === "s") return "save";
	if (typing || e.ctrlKey || e.metaKey || e.altKey) return null;
	if (key === "?") return "help";
	if (e.shiftKey) return null;
	const found = SHORTCUTS.find((s) => s.keys.includes(key));
	return found ? found.action : null;
}
