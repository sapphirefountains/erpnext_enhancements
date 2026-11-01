// Where the designer is, in the address: /desk/fountain-designer?design=<name>&step=<key>&demo=1. Pure
// except for write_address, which takes the browser's history and location from its caller.
//
// The v16 router owns popstate. It re-renders this page for every entry Back or Forward lands on, copies
// the address's query into frappe.route_options and fires on_page_show, so the page reads its place from
// route_options on every show and consumes the keys (a key left there would be replayed by the next plain
// set_route to the page). Before v16.50, set_route pushes the path alone, so routing to the page on
// screen to change its query would lose the query: the page writes its own address instead (pushState
// for a place the person chose, replaceState to correct the entry on screen), on location.pathname,
// which may carry a workspace shell in front of the page name. The same shape as the Maintenance Day
// Board's writeAddress (tests/test_desk_page_history.py).

export const STEPS = [
	{ key: "start", label: "Start" },
	{ key: "site", label: "Site" },
	{ key: "features", label: "Features" },
	{ key: "basins", label: "Basins" },
	{ key: "pump_room", label: "Pump room" },
	{ key: "review", label: "Review" },
	{ key: "outputs", label: "Outputs" },
];
export const STEP_KEYS = STEPS.map((s) => s.key);

// Quick mode: a kit and a name, the address, the kit's headline inputs, then the verdict and the budget.
export const QUICK_STEPS = ["start", "site", "features", "review", "outputs"];
export const FULL_STEPS = STEP_KEYS.slice();

export const ROUTE_KEYS = ["design", "step", "demo"];

export function steps_for(mode) {
	return mode === "full" ? FULL_STEPS.slice() : QUICK_STEPS.slice();
}

export function step_label(key) {
	const step = STEPS.find((s) => s.key === key);
	return step ? step.label : "";
}

// A step this mode shows. A full-mode step opened in quick mode (a link from a designer) lands on the
// nearest step before it that quick mode has; anything unknown is Start.
export function normalize_step(mode, step) {
	const steps = steps_for(mode);
	if (steps.includes(step)) return step;
	const at = STEP_KEYS.indexOf(step);
	if (at < 0) return "start";
	for (let i = at; i >= 0; i -= 1) if (steps.includes(STEP_KEYS[i])) return STEP_KEYS[i];
	return "start";
}

export function next_step(mode, step) {
	const steps = steps_for(mode);
	const at = steps.indexOf(normalize_step(mode, step));
	return at >= 0 && at < steps.length - 1 ? steps[at + 1] : null;
}

export function prev_step(mode, step) {
	const steps = steps_for(mode);
	const at = steps.indexOf(normalize_step(mode, step));
	return at > 0 ? steps[at - 1] : null;
}

// A design name as the address may carry it: printable, no slash, at most 140 characters (Frappe's own
// name limit). Anything else is no design.
export function clean_design(value) {
	if (value == null) return null;
	const text = String(value).trim();
	if (!text || text.length > 140 || /[\/\\\u0000-\u001f]/.test(text)) return null;
	return text;
}

function truthy(value) {
	return value === true || value === 1 || value === "1" || value === "true" || value === "yes";
}

// Read the place from the route options (the router's copy of the query) and consume them, the way the
// list view consumes its filters. Returns { design, step, demo, asked } where `asked` says whether the
// route said anything at all (a plain return to the page leaves the place on screen as it was).
export function take_route(route_options) {
	const out = { design: null, step: null, demo: false, asked: false };
	const opts = route_options && typeof route_options === "object" ? route_options : null;
	if (!opts) return out;
	for (const key of ROUTE_KEYS) {
		if (!Object.prototype.hasOwnProperty.call(opts, key)) continue;
		out.asked = true;
		const value = opts[key];
		delete opts[key];
		if (key === "design") out.design = clean_design(value);
		else if (key === "step") out.step = STEP_KEYS.includes(value) ? value : null;
		else out.demo = truthy(value);
	}
	return out;
}

// The same from a query string ("?design=X&step=site"), for the first show on a Desk that has not put the
// query into route_options yet.
export function parse_search(search) {
	const opts = {};
	const text = String(search || "").replace(/^\?/, "");
	if (!text) return take_route(opts);
	for (const pair of text.split("&")) {
		if (!pair) continue;
		const at = pair.indexOf("=");
		const key = decode(at < 0 ? pair : pair.slice(0, at));
		const value = at < 0 ? "" : decode(pair.slice(at + 1));
		if (ROUTE_KEYS.includes(key)) opts[key] = value;
	}
	return take_route(opts);
}

function decode(text) {
	try {
		return decodeURIComponent(String(text).replace(/\+/g, " "));
	} catch (e) {
		return "";
	}
}

// The query for a place: design, step, demo, in that order, each only when it says something. Start with
// no design is the bare address.
export function build_query(place) {
	const parts = [];
	const design = clean_design(place && place.design);
	if (design) parts.push("design=" + encodeURIComponent(design));
	const step = place && STEP_KEYS.includes(place.step) ? place.step : null;
	if (step && (design || step !== "start")) parts.push("step=" + step);
	if (place && place.demo) parts.push("demo=1");
	return parts.length ? "?" + parts.join("&") : "";
}

// Write the address for a place. push: a step or design the person chose, so a new entry; otherwise the
// entry on screen is corrected in place. Nothing is written when the address already says it.
// env: { location: { pathname, search }, history: { pushState, replaceState, state } }.
export function write_address(env, place, push) {
	if (!env || !env.location || !env.history) return false;
	const url = env.location.pathname + build_query(place);
	if (url === env.location.pathname + (env.location.search || "")) return false;
	if (push) env.history.pushState(null, "", url);
	else env.history.replaceState(env.history.state, "", url);
	return true;
}
