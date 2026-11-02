// The design model (schema 2, docs/fountain-designer/PLAN.md 3.3) as the page edits it. Pure: no DOM,
// no frappe, so scripts/test_fountain_designer.mjs runs every function here under node.
//
// The page never mutates a model in place. Every edit returns a new model (a JSON clone with one path
// set), so a derive request in flight still holds the model it was sent with, and a stale reply can be
// dropped without having touched anything.
//
// The automation ladder writes only into sections whose setup policy is "auto" (PLAN.md 2.2). So an edit
// the person makes to a value the engine filled pins that section to "custom" (the rain curtain's
// deriveSetup pattern), and a "custom" or "preset" section is never repainted by a derive reply.

export const SCHEMA = 2;

// The setup sections, in ladder order, with the step that edits each one.
export const SECTIONS = [
	{ key: "features", label: "Features", step: "features" },
	{ key: "basin", label: "Basins", step: "basins" },
	{ key: "piping", label: "Piping", step: "pump_room" },
	{ key: "pump", label: "Pump", step: "pump_room" },
	{ key: "control", label: "Control", step: "pump_room" },
	{ key: "bypass", label: "Bypass", step: "pump_room" },
	{ key: "surge", label: "Surge", step: "pump_room" },
	{ key: "treatment", label: "Treatment", step: "pump_room" },
	{ key: "lighting", label: "Lighting", step: "pump_room" },
	{ key: "electrical", label: "Electrical", step: "pump_room" },
	{ key: "winterization", label: "Winterization", step: "pump_room" },
];
export const SECTION_KEYS = SECTIONS.map((s) => s.key);
export const POLICIES = ["auto", "preset", "custom"];

// Equipment rows (model.equipment) belong to a section by their slot.
export const SLOT_SECTION = {
	bypass: "bypass",
	surge_arrestor: "surge",
	filter: "treatment",
	uv: "treatment",
	chlorinator: "treatment",
	light: "lighting",
	transformer: "lighting",
	autofill: "basin",
	vfd: "control",
	prv: "control",
};

const CONTROL_FIELDS = new Set(["control", "setpoint_psi"]);

export function clone(value) {
	return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

// "features.0.inputs.length_ft" or ["features", 0, "inputs", "length_ft"]. A segment of digits is an
// array index.
export function parse_path(path) {
	if (Array.isArray(path)) return path.slice();
	if (typeof path !== "string" || !path) return [];
	return path.split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

export function get_in(obj, path) {
	let cur = obj;
	for (const key of parse_path(path)) {
		if (cur == null || typeof cur !== "object") return undefined;
		cur = cur[key];
	}
	return cur;
}

// A new object with `path` set to `value`. Missing containers are created: an array when the next key is
// an index, else an object. The original is untouched.
export function set_in(obj, path, value) {
	const keys = parse_path(path);
	if (!keys.length) return clone(value);
	const root = clone(obj == null ? (typeof keys[0] === "number" ? [] : {}) : obj);
	let cur = root;
	for (let i = 0; i < keys.length - 1; i += 1) {
		const key = keys[i];
		if (cur[key] == null || typeof cur[key] !== "object") cur[key] = typeof keys[i + 1] === "number" ? [] : {};
		cur = cur[key];
	}
	cur[keys[keys.length - 1]] = clone(value);
	return root;
}

// Which setup section a model path belongs to, or null for the person's own inputs (site, identity,
// notes, the pump room's layout) that no derive ever overwrites.
export function section_of(model, path) {
	const keys = parse_path(path);
	const head = keys[0];
	if (head === "features") return "features";
	if (head === "basins") return "basin";
	if (head === "network") {
		if (keys[1] === "pumps") return CONTROL_FIELDS.has(keys[3]) ? "control" : "pump";
		if (keys[1] === "segments" || keys[1] === "nodes" || keys[1] === "terminals") return "piping";
		return null;
	}
	if (head === "equipment") {
		const row = typeof keys[1] === "number" ? get_in(model, ["equipment", keys[1]]) : null;
		return (row && SLOT_SECTION[row.slot]) || null;
	}
	if (head === "treatment" || head === "lighting" || head === "electrical" || head === "winterization") return head;
	return null;
}

export function policy_of(model, section) {
	const value = model && model.setup ? model.setup[section] : null;
	return POLICIES.includes(value) ? value : "auto";
}

// Whether a derive reply may write into this section. Only "auto" is: a preset is a choice the person
// made, and a custom section is theirs.
export function is_auto(model, section) {
	return policy_of(model, section) === "auto";
}

// The edit the person made: the value set, and the section pinned to "custom" when it was "auto" (an
// edited equipment row stops being the engine's too). Site and identity edits pin nothing.
export function edit(model, path, value) {
	const section = section_of(model, path);
	let next = set_in(model, path, value);
	if (section && policy_of(next, section) !== "custom") {
		next = set_policy(next, section, "custom");
	}
	const keys = parse_path(path);
	if (keys[0] === "equipment" && typeof keys[1] === "number" && keys[2] !== "auto" && get_in(next, ["equipment", keys[1]])) {
		next = set_in(next, ["equipment", keys[1], "auto"], false);
	}
	// A site value typed here is the person's: it loses the engine's source, so no later derive replaces it.
	if (keys[0] === "site" && keys.length === 2 && SITE_LOOKUPS.includes(keys[1])) next = drop_source(next, keys[1]);
	return next;
}

function drop_source(model, key) {
	const sources = get_in(model, "site.sources");
	if (!sources || typeof sources !== "object" || !Object.prototype.hasOwnProperty.call(sources, key)) return model;
	const rest = Object.assign({}, sources);
	delete rest[key];
	return set_in(model, "site.sources", Object.keys(rest).length ? rest : null);
}

// A preset is remembered beside the policy (model.presets[section] = key), so the page can show which
// preset is on and offer "Custom" when the values no longer match it.
export function set_policy(model, section, policy, preset_key) {
	if (!SECTION_KEYS.includes(section) || !POLICIES.includes(policy)) return model;
	let next = set_in(model, ["setup", section], policy);
	if (policy === "preset" && preset_key) next = set_in(next, ["presets", section], preset_key);
	else if (next.presets && Object.prototype.hasOwnProperty.call(next.presets, section)) {
		next = clone(next);
		delete next.presets[section];
	}
	return next;
}

// Apply a preset's values ({path: value}) and mark the section "preset".
export function apply_preset(model, section, preset) {
	if (!preset || !preset.values) return model;
	let next = model;
	for (const [path, value] of Object.entries(preset.values)) next = set_in(next, path, value);
	return set_policy(next, section, "preset", preset.key);
}

// The preset whose every value the model still holds, or null ("Custom").
export function match_preset(model, presets) {
	for (const preset of presets || []) {
		const values = preset && preset.values ? Object.entries(preset.values) : [];
		if (values.length && values.every(([path, value]) => same(get_in(model, path), value))) return preset.key;
	}
	return null;
}

function same(a, b) {
	return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
}

// Fold a derive reply's model into the one on screen: every "auto" section takes the engine's values,
// "preset" and "custom" sections keep the person's, and a site field takes the engine's value where it is
// blank or the engine filled it (site.sources, here or in the reply), so another state's climate replaces
// the last state's while an elevation somebody typed stays.
export function merge_derived(local, derived) {
	if (!derived || typeof derived !== "object") return local;
	let next = clone(local);
	const take = (path) => {
		const value = get_in(derived, path);
		if (value !== undefined) next = set_in(next, path, value);
	};
	if (is_auto(next, "features")) take("features");
	if (is_auto(next, "basin")) take("basins");
	if (is_auto(next, "piping")) {
		take("network.nodes");
		take("network.segments");
		take("network.terminals");
	}
	next = merge_pumps(next, derived);
	next = merge_equipment(next, derived);
	for (const section of ["treatment", "lighting", "electrical", "winterization"]) {
		if (is_auto(next, section)) take(section);
	}
	const site = derived.site || {};
	const own = (obj, key) => Boolean(obj) && typeof obj === "object" && Object.prototype.hasOwnProperty.call(obj, key);
	const theirs = site.sources;
	const before = next.site && next.site.sources;
	for (const [key, value] of Object.entries(site)) {
		if (key === "sources" || value === undefined) continue;
		const mine = next.site ? next.site[key] : undefined;
		const blank = mine === undefined || mine === null || mine === "";
		if (blank || own(theirs, key) || (SITE_LOOKUPS.includes(key) && own(before, key))) next = set_in(next, ["site", key], value);
	}
	if (theirs && typeof theirs === "object" && Object.keys(theirs).length) next = set_in(next, "site.sources", clone(theirs));
	else if (before) next = set_in(next, "site.sources", null);
	return next;
}

// The site fields the engine fills from the address and the state (ladder step 0; derive.SITE_LOOKUPS).
export const SITE_LOOKUPS = [
	"lat",
	"lng",
	"elevation_ft",
	"air_temp_f",
	"water_temp_f",
	"frost_depth_in",
	"hardness_ppm",
	"kwh_rate",
	"water_rate_per_ccf",
	"seismic_sdc",
	"sds",
	"sd1",
	"winterize",
];

// Blank every looked-up site field so the next derive fills them from the address on screen, typed ones
// too; the page asks for it only where that is right (quick mode, which never shows them as inputs, or a
// "Refresh from the address").
export function clear_site_lookups(model) {
	let next = model;
	for (const key of SITE_LOOKUPS) next = set_in(next, ["site", key], null);
	return set_in(next, "site.sources", null);
}

// Blank only the site values the address lookups filled (site.sources says "lookup"): they belong to the
// old address. A typed value and a state default stay. Full mode's answer to a new address.
export function clear_looked_up(model) {
	const sources = get_in(model, "site.sources") || {};
	let next = model;
	for (const key of SITE_LOOKUPS) {
		if (sources[key] === "lookup") next = drop_source(set_in(next, ["site", key], null), key);
	}
	return next;
}

// Pumps carry two sections: the pump itself and its control fields.
function merge_pumps(next, derived) {
	const theirs = get_in(derived, "network.pumps");
	if (!Array.isArray(theirs)) return next;
	const pump_auto = is_auto(next, "pump");
	const control_auto = is_auto(next, "control");
	if (!pump_auto && !control_auto) return next;
	const mine = get_in(next, "network.pumps") || [];
	const base = pump_auto ? theirs : mine;
	const merged = base.map((pump) => {
		const own = mine.find((p) => p && p.id === pump.id) || {};
		const other = theirs.find((p) => p && p.id === pump.id) || {};
		const out = Object.assign({}, pump);
		for (const field of CONTROL_FIELDS) {
			const source = control_auto ? other : own;
			if (Object.prototype.hasOwnProperty.call(source, field)) out[field] = source[field];
			else delete out[field];
		}
		return out;
	});
	return set_in(next, "network.pumps", merged);
}

// Equipment rows: an auto row of an auto section is replaced by the engine's rows for that section; a
// row the person edited (auto: false) or one in a pinned section stays.
function merge_equipment(next, derived) {
	const theirs = Array.isArray(derived.equipment) ? derived.equipment : null;
	if (!theirs) return next;
	const auto_section = (row) => {
		const section = SLOT_SECTION[row && row.slot];
		return section ? is_auto(next, section) : false;
	};
	const kept = (next.equipment || []).filter((row) => !(row && row.auto !== false && auto_section(row)));
	const added = theirs.filter((row) => row && row.auto !== false && auto_section(row));
	return set_in(next, "equipment", kept.concat(added));
}

// --- building a model -------------------------------------------------------------------------------

// The empty schema-2 model: every section auto except the features, which are the person's.
export function blank_model(mode) {
	const setup = {};
	for (const key of SECTION_KEYS) setup[key] = key === "features" ? "custom" : "auto";
	return {
		schema: SCHEMA,
		mode: mode === "full" ? "full" : "quick",
		kit: null,
		site: {
			address: "",
			lat: null,
			lng: null,
			elevation_ft: null,
			air_temp_f: null,
			water_temp_f: null,
			frost_depth_in: null,
			hardness_ppm: null,
			kwh_rate: null,
			water_rate_per_ccf: null,
			jurisdiction: "UT",
			venue_type: "Decorative Fountain",
			regulated: false,
		},
		setup,
		presets: {},
		features: [],
		basins: [],
		pump_room: { location: "vault", distance_ft: null, elevation_ft: null, route_template: null, material: "SCH40 PVC" },
		network: { nodes: [], pumps: [], segments: [], terminals: [] },
		equipment: [],
		treatment: { sanitizer: "chlorine", uv: false },
		lighting: { fixtures: [], control: "switched" },
		electrical: { service_v: 240, phase: 1, panel_distance_ft: null },
		winterization: {},
		acks: [],
		notes: "",
	};
}

// A new design from a kit (boot.kits[].model is the kit's starter model), or blank.
export function model_from_kit(kit, mode) {
	const base = blank_model(mode);
	if (!kit || !kit.model) return base;
	const next = Object.assign(base, clone(kit.model));
	next.schema = SCHEMA;
	next.mode = mode === "full" ? "full" : "quick";
	next.kit = kit.key || null;
	next.setup = Object.assign(blank_model(mode).setup, next.setup || {});
	return next;
}

// The sections a person set by hand (custom or a preset), features apart: what quick mode would hand
// back to the engine, so the page asks before it does.
export function pinned_sections(model) {
	return SECTION_KEYS.filter((key) => key !== "features" && policy_of(model, key) !== "auto");
}

// Quick mode is the same model with every section but the features back on "auto" (PLAN.md 2.1); full
// mode keeps every policy as it was.
export function set_mode(model, mode) {
	let next = set_in(model, "mode", mode === "full" ? "full" : "quick");
	if (next.mode === "quick") {
		for (const key of SECTION_KEYS) if (key !== "features") next = set_policy(next, key, "auto");
	}
	return next;
}

// The next free id with a prefix: f1, f2 ... over the ids already in the list.
export function next_id(list, prefix) {
	let n = 1;
	const taken = new Set((list || []).map((row) => row && row.id));
	while (taken.has(prefix + n)) n += 1;
	return prefix + n;
}

export function add_feature(model, family) {
	const features = model.features || [];
	const id = next_id(features, "f");
	const basin = (model.basins && model.basins[0] && model.basins[0].id) || null;
	const row = {
		id,
		family: family || "weir",
		type: "",
		label: "",
		inputs: {},
		effect: {},
		product: { item: null, generic: true },
		count: 1,
		basin,
		node: null,
	};
	return set_in(model, "features", features.concat([row]));
}

export function add_basin(model) {
	const basins = model.basins || [];
	const id = next_id(basins, "b");
	const row = {
		id,
		label: "",
		shape: "rectangular",
		length_in: null,
		width_in: null,
		depth_in: null,
		freeboard_in: null,
		volume_gal_override: null,
		elevation_ft: 0,
	};
	return set_in(model, "basins", basins.concat([row]));
}

// Removing a row is an edit too (it pins its section), and a feature caught by a removed basin is left
// with no basin rather than a dangling id.
export function remove_row(model, list_key, id) {
	const rows = model[list_key] || [];
	if (!rows.some((row) => row && row.id === id)) return model;
	const path = list_key === "basins" ? "basins" : list_key;
	let next = edit(model, path, rows.filter((row) => !(row && row.id === id)));
	if (list_key === "basins") {
		next = set_in(
			next,
			"features",
			(next.features || []).map((f) => (f && f.basin === id ? Object.assign({}, f, { basin: null }) : f))
		);
	}
	return next;
}

// The input's text to a value of its kind: a number field blank is null (the engine reads null as "size
// it for me"), never 0; a check is a boolean; anything else is the trimmed text.
export function coerce(kind, raw) {
	if (kind === "number" || kind === "int") {
		const text = raw == null ? "" : String(raw).trim().replace(/,/g, "");
		if (text === "") return null;
		const n = Number(text);
		if (!Number.isFinite(n)) return null;
		return kind === "int" ? Math.round(n) : n;
	}
	if (kind === "check") return raw === true || raw === "1" || raw === 1 || raw === "true";
	if (kind === "json") return raw;
	return raw == null ? "" : String(raw).trim();
}
