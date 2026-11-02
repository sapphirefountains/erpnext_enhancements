#!/usr/bin/env node
/**
 * The Fountain Designer page's pure modules, run under node (phase F, TASK-2026-03018).
 *
 * What it pins, because each part fails quietly on a real page:
 *
 *   - the bundle entry installs window.sf_fountain_designer once, with mount() and the pure modules;
 *   - the route: take_route reads design/step/demo from frappe.route_options and CONSUMES them (a key
 *     left there is replayed by the next plain set_route), parse_search does the same from a query,
 *     build_query writes design, step, demo in that order and nothing for the bare list, a design name
 *     with a slash or a control character is no design, and write_address pushes a chosen place,
 *     replaces to correct one, and writes nothing when the address already says it;
 *   - the step lists: quick is Start, Site, Features, Review, Outputs; full is all seven; a full-only
 *     step opened in quick mode lands on the nearest quick step before it;
 *   - the live derive: edits inside the 400 ms window send one request; a reply to a model that has
 *     since been edited is dropped (never drawn over the newer state), and so is its error; cancel()
 *     drops what is in flight; debounce flush/cancel;
 *   - the model helpers: no edit mutates the model it was given; editing an auto section pins it
 *     "custom" and a site edit pins nothing; an edited equipment row stops being auto; presets apply,
 *     match, and turn "Custom" once a value moves; merge_derived writes only into auto sections, fills
 *     only blank site fields, splits a pump's control fields from the pump, and keeps a person's
 *     equipment rows; quick mode puts every section but the features back on auto; coerce never makes
 *     a blank number 0;
 *   - the chart sanitizer passes the fixture's three charts and refuses script, handlers,
 *     foreignObject, links out and style;
 *   - the verdict panel: issues map to the step that fixes them, every chip keeps its word, and the
 *     server's text is escaped;
 *   - the steps render for every demo design in both modes, and quick Features draws exactly the kit's
 *     headline inputs;
 *   - the demo fixture answers every contract endpoint in the contract's shapes, a headline input
 *     moves the numbers, a remedy clears its issue, signing off the warnings makes the package ready.
 *
 * Run: node scripts/test_fountain_designer.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const JS = path.join(APP, "public", "js");
const FD = path.join(JS, "fountain_designer");
const url = (file) => pathToFileURL(file).href;
const read_json = (name) => JSON.parse(fs.readFileSync(path.join(FD, "demo", name), "utf-8"));

let failures = 0;
let checks = 0;
const unhandled = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));

async function test(name, fn) {
	checks += 1;
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.stack ? err.stack : String(err)).split("\n").slice(0, 6).join("\n       "));
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(rounds = 6) {
	for (let i = 0; i < rounds; i += 1) await tick();
}

// Timers we drive by hand.
function fake_timers() {
	let now = 0;
	let next = 1;
	const timers = new Map();
	return {
		set_timeout: (fn, ms) => {
			const id = next++;
			timers.set(id, { fn, at: now + (ms || 0) });
			return id;
		},
		clear_timeout: (id) => timers.delete(id),
		advance(ms) {
			now += ms;
			for (const [id, t] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
				if (t.at <= now && timers.has(id)) {
					timers.delete(id);
					t.fn();
				}
			}
		},
		count: () => timers.size,
	};
}

const M = await import(url(path.join(FD, "model.js")));
const R = await import(url(path.join(FD, "router.js")));
const A = await import(url(path.join(FD, "api.js")));
const C = await import(url(path.join(FD, "chart.js")));
const V = await import(url(path.join(FD, "verdict.js")));
const D = await import(url(path.join(FD, "demo", "demo.js")));
const STEPS = {};
for (const key of ["start", "site", "features", "basins", "pump_room", "review", "outputs"]) {
	STEPS[key] = await import(url(path.join(FD, "steps", key + ".js")));
}

const boot = read_json("boot.json");
const catalog = read_json("catalog.json");
const designs = read_json("designs.json");
const calc = read_json("calc.json");
const replies = {};
for (const name of ["derive_lobby.json", "derive_plaza.json", "derive_pond.json"]) {
	const r = read_json(name);
	replies[r.model.kit] = r;
}
const fixture = { boot, catalog, designs, replies, calc };

const esc = (v) =>
	v == null
		? ""
		: String(v).replace(/[&<>"'`]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" })[c]);
const __ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, n) => (args && args[n] !== undefined ? String(args[n]) : m));
const H = {
	esc,
	__,
	chip: (label, tone) => (label ? `<span class="sf-chip sf-chip--${tone}">${esc(label)}</span>` : ""),
	state_html: (kind, opts) => `<div class="sf-state sf-state--${kind}">${esc((opts && opts.title) || "")}</div>`,
	parser: null,
};

console.log("bundle entry");

await test("the bundle installs window.sf_fountain_designer once, with mount and the pure modules", async () => {
	globalThis.window = {};
	await import(url(path.join(JS, "fountain_designer.bundle.js")));
	const fd = globalThis.window.sf_fountain_designer;
	assert.ok(fd, "installed");
	assert.equal(fd.version, 1);
	assert.equal(typeof fd.mount, "function");
	for (const key of ["model", "router", "api", "verdict", "chart"]) assert.ok(fd[key], key);
	assert.equal(fd.router.steps_for, R.steps_for, "the same module, not a copy");
	delete globalThis.window;
});

console.log("route");

await test("take_route reads design, step and demo and consumes them", () => {
	const opts = { design: "WFD-2026-0001", step: "site", demo: "1", other: "x" };
	const place = R.take_route(opts);
	assert.deepEqual(place, { design: "WFD-2026-0001", step: "site", demo: true, asked: true });
	assert.deepEqual(opts, { other: "x" }, "our keys are gone, others are left");
	assert.equal(R.take_route(null).asked, false);
	assert.equal(R.take_route({}).asked, false);
});

await test("an unknown step or a bad design name is ignored, not thrown", () => {
	assert.equal(R.take_route({ step: "nowhere" }).step, null);
	assert.equal(R.take_route({ design: "a/b" }).design, null);
	assert.equal(R.take_route({ design: "a\u0001" }).design, null);
	assert.equal(R.take_route({ design: "x".repeat(141) }).design, null);
	assert.equal(R.take_route({ design: "   " }).design, null);
	assert.equal(R.take_route({ demo: "0" }).demo, false);
});

await test("parse_search reads a query the same way", () => {
	assert.deepEqual(R.parse_search("?design=WFD%202026&step=review&demo=1&x=y"), {
		design: "WFD 2026",
		step: "review",
		demo: true,
		asked: true,
	});
	assert.equal(R.parse_search("").asked, false);
	assert.equal(R.parse_search("?design=%E0%A4%A").design, null, "a malformed escape is no design");
});

await test("build_query writes design, step, demo in order, and the bare list as nothing", () => {
	assert.equal(R.build_query({ design: "WFD-1", step: "site", demo: true }), "?design=WFD-1&step=site&demo=1");
	assert.equal(R.build_query({ design: null, step: "start", demo: false }), "");
	assert.equal(R.build_query({ design: null, step: "start", demo: true }), "?demo=1");
	assert.equal(R.build_query({ design: "A & B", step: "start" }), "?design=A%20%26%20B&step=start");
	const round = R.parse_search(R.build_query({ design: "A & B", step: "pump_room", demo: true }));
	assert.deepEqual(round, { design: "A & B", step: "pump_room", demo: true, asked: true });
});

await test("write_address pushes a chosen place, replaces to correct one, and skips a no-op", () => {
	const calls = [];
	const env = {
		location: { pathname: "/desk/fountain-designer", search: "" },
		history: {
			state: { tag: 1 },
			pushState: (s, t, u) => calls.push(["push", u]),
			replaceState: (s, t, u) => calls.push(["replace", u, s]),
		},
	};
	assert.equal(R.write_address(env, { design: "D1", step: "site" }, true), true);
	env.location.search = "?design=D1&step=site";
	assert.equal(R.write_address(env, { design: "D1", step: "site" }, true), false, "already says it");
	assert.equal(R.write_address(env, { design: "D1", step: "review" }, false), true);
	assert.deepEqual(calls[0], ["push", "/desk/fountain-designer?design=D1&step=site"]);
	assert.deepEqual(calls[1], ["replace", "/desk/fountain-designer?design=D1&step=review", { tag: 1 }]);
	env.location.pathname = "/desk/water-engineering/fountain-designer";
	env.location.search = "";
	R.write_address(env, { design: "D1", step: "site" }, true);
	assert.equal(calls[2][1], "/desk/water-engineering/fountain-designer?design=D1&step=site", "the path on screen is kept");
});

await test("an entry with no keys is the list, out of the demo, whatever was on screen", () => {
	// The bare address the page was opened at (from the Dock), as Back lands on it: the router leaves
	// route_options empty and the address bare.
	assert.deepEqual(R.place_from(null, ""), { design: null, step: "start", demo: false });
	assert.deepEqual(R.place_from({}, ""), { design: null, step: "start", demo: false });
	// The router copied the query in: it wins, and its keys are consumed.
	const opts = { design: "D1", step: "site", demo: "1" };
	assert.deepEqual(R.place_from(opts, ""), { design: "D1", step: "site", demo: true });
	assert.deepEqual(opts, {});
	// Nothing in route_options (consumed on an earlier show): the address on screen decides.
	assert.deepEqual(R.place_from(null, "?design=D1&step=review&demo=1"), { design: "D1", step: "review", demo: true });
	assert.deepEqual(R.place_from(null, "?design=D1"), { design: "D1", step: "start", demo: false });
	assert.deepEqual(R.place_from(null, "?step=site"), { design: null, step: "start", demo: false }, "no design is Start");
});

await test("Back and Forward walk list, design, step and the demo", () => {
	// A browser history the page pushed onto, read back the way each show reads it.
	const entries = [
		"", // opened from the Dock
		"?design=D1&step=review", // a design opened
		"?design=D1&step=site", // a step
		"?demo=1", // Open the demo
	];
	const shown = entries.map((search) => R.place_from(null, search));
	assert.deepEqual(shown[0], { design: null, step: "start", demo: false }, "Back to the bare entry is the list");
	assert.deepEqual(shown[1], { design: "D1", step: "review", demo: false });
	assert.deepEqual(shown[2], { design: "D1", step: "site", demo: false });
	assert.equal(shown[3].demo, true);
	assert.equal(R.place_from(null, entries[2]).demo, false, "Back out of the demo leaves it");
	// And what each entry writes back is itself: no correction rewrites an entry into another one.
	for (const search of entries) {
		const place = R.place_from(null, search);
		assert.equal(R.build_query(place), search.replace(/^\?$/, ""), search);
	}
});

console.log("steps");

await test("quick and full step lists", () => {
	assert.deepEqual(R.steps_for("quick"), ["start", "site", "features", "review", "outputs"]);
	assert.deepEqual(R.steps_for("full"), ["start", "site", "features", "basins", "pump_room", "review", "outputs"]);
	assert.deepEqual(R.steps_for(undefined), R.steps_for("quick"), "anything not full is quick");
	R.steps_for("full").push("x");
	assert.equal(R.steps_for("full").length, 7, "a copy each time");
});

await test("normalize, next and prev", () => {
	assert.equal(R.normalize_step("quick", "pump_room"), "features");
	assert.equal(R.normalize_step("quick", "basins"), "features");
	assert.equal(R.normalize_step("full", "pump_room"), "pump_room");
	assert.equal(R.normalize_step("full", "bogus"), "start");
	assert.equal(R.next_step("quick", "features"), "review");
	assert.equal(R.next_step("full", "features"), "basins");
	assert.equal(R.prev_step("quick", "review"), "features");
	assert.equal(R.prev_step("full", "start"), null);
	assert.equal(R.next_step("full", "outputs"), null);
});

console.log("live derive");

await test("edits inside the window send one request, with the newest model", async () => {
	const t = fake_timers();
	const sent = [];
	const drawn = [];
	const d = A.create_deriver({
		derive: (m) => (sent.push(m), Promise.resolve({ for: m.n })),
		set_timeout: t.set_timeout,
		clear_timeout: t.clear_timeout,
		on_result: (r) => drawn.push(r.for),
	});
	d.schedule({ n: 1 });
	t.advance(200);
	d.schedule({ n: 2 });
	t.advance(399);
	assert.equal(sent.length, 0, "still waiting");
	t.advance(1);
	assert.deepEqual(sent, [{ n: 2 }]);
	await settle();
	assert.deepEqual(drawn, [2]);
	assert.equal(d.busy(), false);
});

await test("a reply to a model edited since is dropped, and so is its error", async () => {
	const t = fake_timers();
	const pending = [];
	const drawn = [];
	const errors = [];
	const d = A.create_deriver({
		derive: (m) =>
			new Promise((resolve, reject) => {
				pending.push({ m, resolve, reject });
			}),
		set_timeout: t.set_timeout,
		clear_timeout: t.clear_timeout,
		on_result: (r) => drawn.push(r),
		on_error: (e) => errors.push(e),
	});
	d.schedule({ n: 1 });
	t.advance(400);
	d.schedule({ n: 2 }); // an edit after the first request went out
	t.advance(400);
	assert.equal(pending.length, 2);
	pending[1].resolve("second");
	await settle();
	pending[0].resolve("first, late");
	await settle();
	assert.deepEqual(drawn, ["second"], "the late first reply never paints over the second");
	d.schedule({ n: 3 });
	t.advance(400);
	d.schedule({ n: 4 });
	pending[2].reject({ message: "boom" });
	await settle();
	assert.deepEqual(errors, [], "a stale error is not shown either");
	assert.equal(d.counts().dropped, 2);
});

await test("an edit after the reply is sent but before it lands still makes it stale", async () => {
	const t = fake_timers();
	let resolve;
	const drawn = [];
	const d = A.create_deriver({
		derive: () => new Promise((r) => (resolve = r)),
		set_timeout: t.set_timeout,
		clear_timeout: t.clear_timeout,
		on_result: (r) => drawn.push(r),
	});
	d.schedule({ n: 1 });
	t.advance(400);
	d.schedule({ n: 2 }); // typed again; its own request is still waiting out the debounce
	resolve("for n=1");
	await settle();
	assert.deepEqual(drawn, []);
	assert.equal(d.busy(), true, "the newer request is still to go");
});

await test("cancel drops what is waiting and what is in flight", async () => {
	const t = fake_timers();
	let resolve;
	const drawn = [];
	const d = A.create_deriver({
		derive: () => new Promise((r) => (resolve = r)),
		set_timeout: t.set_timeout,
		clear_timeout: t.clear_timeout,
		on_result: (r) => drawn.push(r),
	});
	d.now({ n: 1 });
	t.advance(0);
	d.cancel();
	resolve("late");
	await settle();
	assert.deepEqual(drawn, []);
	d.schedule({ n: 2 });
	d.cancel();
	assert.equal(t.count(), 0);
});

await test("a derive that throws synchronously is an error, not an unhandled exception", async () => {
	const t = fake_timers();
	const errors = [];
	const d = A.create_deriver({
		derive: () => {
			throw new Error("sync");
		},
		set_timeout: t.set_timeout,
		clear_timeout: t.clear_timeout,
		on_error: (e) => errors.push(e.message),
	});
	d.now({});
	t.advance(0);
	await settle();
	assert.deepEqual(errors, ["sync"]);
});

await test("debounce: trailing, flush runs a waiting call at once, cancel drops it", () => {
	const t = fake_timers();
	const runs = [];
	const f = A.debounce((x) => runs.push(x), 100, t.set_timeout, t.clear_timeout);
	f(1);
	f(2);
	t.advance(100);
	assert.deepEqual(runs, [2]);
	f(3);
	assert.equal(f.pending(), true);
	f.flush();
	assert.deepEqual(runs, [2, 3]);
	assert.equal(f.flush(), undefined, "nothing waiting");
	f(4);
	f.cancel();
	t.advance(200);
	assert.deepEqual(runs, [2, 3]);
});

await test("the api sends the contract's names and arguments", async () => {
	const calls = [];
	const api = A.create_api((method, args, opts) => (calls.push({ method, args, opts }), Promise.resolve(null)));
	await api.boot();
	await api.derive({ schema: 2 });
	await api.save_design(null, { schema: 2 }, { design_title: "X" });
	await api.list_designs();
	await api.get_design("D1");
	await api.catalog_search("pump", "gou");
	await api.acknowledge_issue("D1", "K", "ok");
	await api.push_quotation("D1");
	await api.push_material_request("D1");
	await api.check_permission();
	await api.run_calc("weir_flow", { length_ft: 4 });
	const base = "erpnext_enhancements.water_engineering.api.designer.";
	assert.deepEqual(
		calls.map((c) => c.method),
		[
			base + "boot",
			base + "derive",
			base + "save_design",
			base + "get_design",
			base + "get_design",
			base + "catalog_search",
			base + "acknowledge_issue",
			base + "push_quotation",
			base + "push_material_request",
			// The gate moved here from the retired wizard's api.water_design in v1.771.0 (phase H).
			base + "check_permission",
			base + "run_calc",
		]
	);
	assert.deepEqual(calls[10].args, { calc: "weir_flow", inputs: '{"length_ft":4}' }, "the calc inputs go as JSON text");
	assert.equal(calls[10].opts.method, "POST");
	assert.equal(calls[1].args.model_json, '{"schema":2}', "the model goes as JSON text");
	assert.deepEqual(calls[2].args, { name: null, model_json: '{"schema":2}', fields_json: '{"design_title":"X"}' });
	assert.deepEqual(calls[3].args, {}, "the list is get_design with no name");
	assert.equal(calls[0].opts.method, "GET");
	assert.equal(calls[1].opts.method, "POST", "a model is too long for a query string");
	assert.ok(calls.every((c) => c.opts.quiet), "every call is quiet");
});

console.log("model");

const lobby_kit = boot.kits.find((k) => k.key === "lobby-sheet-wall-8ft");

await test("set_in and edit never mutate the model they are given", () => {
	const m = M.model_from_kit(lobby_kit, "quick");
	const before = JSON.stringify(m);
	const next = M.edit(m, "basins.0.depth_in", 16);
	assert.equal(JSON.stringify(m), before);
	assert.equal(next.basins[0].depth_in, 16);
	assert.equal(M.set_in({}, "a.0.b", 1).a[0].b, 1, "an index makes an array");
	assert.ok(Array.isArray(M.set_in({}, "a.0.b", 1).a));
	assert.equal(M.get_in(m, "features.0.inputs.length_ft"), 8);
	assert.equal(M.get_in(m, "no.such.path"), undefined);
});

await test("editing an auto section pins it custom; a site or pump room edit pins nothing", () => {
	const m = M.model_from_kit(lobby_kit, "full");
	assert.equal(M.policy_of(m, "basin"), "auto");
	assert.equal(M.policy_of(M.edit(m, "basins.0.depth_in", 16), "basin"), "custom");
	assert.equal(M.policy_of(M.edit(m, "treatment.uv", true), "treatment"), "custom");
	assert.equal(M.policy_of(M.edit(m, "network.pumps.0.control", "vfd"), "control"), "custom");
	assert.equal(M.policy_of(M.edit(m, "network.pumps.0.control", "vfd"), "pump"), "auto", "control is its own section");
	const site = M.edit(m, "site.address", "50 W Broadway");
	assert.deepEqual(site.setup, m.setup);
	const room = M.edit(m, "pump_room.distance_ft", 40);
	assert.deepEqual(room.setup, m.setup);
});

await test("an edited equipment row stops being the engine's and pins its section", () => {
	let m = M.model_from_kit(lobby_kit, "full");
	m = M.set_in(m, "equipment", [{ slot: "filter", item: "A", qty: 1, auto: true }]);
	const next = M.edit(m, "equipment.0.item", "PNT-CCP520");
	assert.equal(next.equipment[0].auto, false);
	assert.equal(M.policy_of(next, "treatment"), "custom");
});

await test("presets apply, match, and read Custom once a value moves", () => {
	const presets = boot.sections.find((s) => s.key === "electrical").presets;
	const m = M.model_from_kit(lobby_kit, "full");
	const on = M.apply_preset(m, "electrical", presets.find((p) => p.key === "480_3"));
	assert.equal(on.electrical.service_v, 480);
	assert.equal(on.setup.electrical, "preset");
	assert.equal(on.presets.electrical, "480_3");
	assert.equal(M.match_preset(on, presets), "480_3");
	const moved = M.edit(on, "electrical.service_v", 208);
	assert.equal(moved.setup.electrical, "custom");
	assert.equal(moved.presets.electrical, undefined, "the remembered preset goes with it");
	assert.equal(M.match_preset(M.set_in(moved, "electrical.phase", 1), presets), null);
	assert.equal(M.set_policy(m, "nope", "auto"), m, "an unknown section changes nothing");
	assert.equal(M.set_policy(m, "pump", "sometimes"), m, "an unknown policy changes nothing");
});

await test("merge_derived writes only into auto sections and fills only a blank site", () => {
	const derived = replies["lobby-sheet-wall-8ft"].model;
	let mine = M.model_from_kit(lobby_kit, "full");
	mine = M.edit(mine, "basins.0.depth_in", 20); // basin is now custom
	mine = M.set_in(mine, "site.elevation_ft", 5000); // typed
	const out = M.merge_derived(mine, derived);
	assert.equal(out.basins[0].depth_in, 20, "custom basin kept");
	assert.deepEqual(out.network.segments, derived.network.segments, "auto piping taken");
	assert.equal(out.site.elevation_ft, 5000, "a typed elevation is never overwritten");
	assert.equal(out.site.air_temp_f, derived.site.air_temp_f, "a blank one is filled");
	assert.deepEqual(out.features, mine.features, "features are custom by default");
	assert.equal(M.merge_derived(mine, null), mine);
});

await test("merge_derived: control fields follow their own section", () => {
	const derived = { network: { pumps: [{ id: "p1", item: "ENGINE", control: "vfd", setpoint_psi: null }] } };
	let mine = M.set_in(M.blank_model("full"), "network.pumps", [{ id: "p1", item: "MINE", control: "prv", setpoint_psi: 40 }]);
	mine = M.set_policy(mine, "control", "custom");
	const out = M.merge_derived(mine, derived);
	assert.equal(out.network.pumps[0].item, "ENGINE", "auto pump taken");
	assert.equal(out.network.pumps[0].control, "prv", "custom control kept");
	assert.equal(out.network.pumps[0].setpoint_psi, 40);
	mine = M.set_policy(M.set_policy(mine, "pump", "custom"), "control", "auto");
	const out2 = M.merge_derived(mine, derived);
	assert.equal(out2.network.pumps[0].item, "MINE");
	assert.equal(out2.network.pumps[0].control, "vfd");
});

await test("merge_derived: the person's equipment rows stay, the engine's are replaced", () => {
	let mine = M.blank_model("full");
	mine = M.set_in(mine, "equipment", [
		{ slot: "filter", item: "OLD-AUTO", qty: 1, auto: true },
		{ slot: "light", item: "MY-LIGHT", qty: 2, auto: false },
	]);
	const derived = { equipment: [{ slot: "filter", item: "NEW", qty: 1, auto: true }, { slot: "light", item: "ENGINE-LIGHT", qty: 4, auto: true }] };
	const out = M.merge_derived(mine, derived);
	const items = out.equipment.map((r) => r.item).sort();
	assert.deepEqual(items, ["ENGINE-LIGHT", "MY-LIGHT", "NEW"]);
	const pinned = M.merge_derived(M.set_policy(mine, "treatment", "custom"), derived);
	assert.ok(pinned.equipment.some((r) => r.item === "OLD-AUTO"), "a pinned section keeps even its auto rows");
	assert.ok(!pinned.equipment.some((r) => r.item === "NEW"));
});

await test("quick mode puts every section but the features back on auto", () => {
	let m = M.model_from_kit(lobby_kit, "full");
	m = M.edit(m, "treatment.uv", true);
	m = M.edit(m, "features.0.label", "Wall");
	const quick = M.set_mode(m, "quick");
	assert.equal(quick.mode, "quick");
	for (const key of M.SECTION_KEYS) assert.equal(quick.setup[key], key === "features" ? "custom" : "auto", key);
	const full = M.set_mode(quick, "full");
	assert.deepEqual(full.setup, quick.setup, "full keeps whatever was set");
});

await test("a new model from a kit, blank, and the rows", () => {
	const m = M.model_from_kit(lobby_kit, "full");
	assert.equal(m.schema, 2);
	assert.equal(m.kit, "lobby-sheet-wall-8ft");
	assert.equal(m.mode, "full");
	m.features[0].label = "changed";
	assert.notEqual(lobby_kit.model.features[0].label, "changed", "the kit's model is copied");
	const blank = M.model_from_kit(null, "quick");
	assert.equal(blank.kit, null);
	assert.deepEqual(Object.keys(blank.setup), M.SECTION_KEYS);
	let rows = M.add_basin(M.add_feature(blank, "jets"));
	rows = M.add_feature(M.add_basin(rows), "weir");
	assert.deepEqual(rows.features.map((f) => f.id), ["f1", "f2"]);
	assert.deepEqual(rows.basins.map((b) => b.id), ["b1", "b2"]);
	assert.equal(rows.features[1].basin, "b1");
	const gone = M.remove_row(rows, "basins", "b1");
	assert.deepEqual(gone.basins.map((b) => b.id), ["b2"]);
	assert.equal(gone.features[1].basin, null, "no dangling basin id");
	assert.equal(gone.setup.basin, "custom");
	assert.equal(M.remove_row(rows, "features", "nope"), rows);
});

await test("coerce: a blank number is null, never 0", () => {
	assert.equal(M.coerce("number", ""), null);
	assert.equal(M.coerce("number", "  "), null);
	assert.equal(M.coerce("number", "1,200.5"), 1200.5);
	assert.equal(M.coerce("number", "abc"), null);
	assert.equal(M.coerce("int", "7.6"), 8);
	assert.equal(M.coerce("number", "0"), 0);
	assert.equal(M.coerce("check", "1"), true);
	assert.equal(M.coerce("text", "  a "), "a");
	assert.equal(M.section_of(M.blank_model(), "equipment.9.item"), null, "a row that is not there");
});

await test("pinned_sections names what quick mode would hand back", () => {
	let m = M.model_from_kit(lobby_kit, "full");
	assert.deepEqual(M.pinned_sections(m), []);
	m = M.edit(m, "basins.0.depth_in", 20);
	m = M.apply_preset(m, "electrical", boot.sections.find((x) => x.key === "electrical").presets[0]);
	assert.deepEqual(M.pinned_sections(m), ["basin", "electrical"], "features are never counted");
	assert.deepEqual(M.pinned_sections(M.set_mode(m, "quick")), []);
});

await test("clear_site_lookups blanks every looked-up field and keeps the rest", () => {
	const m = M.merge_derived(M.model_from_kit(lobby_kit, "quick"), replies["lobby-sheet-wall-8ft"].model);
	assert.equal(m.site.elevation_ft, 4226);
	const cleared = M.clear_site_lookups(M.set_in(m, "site.address", "1 Main St, Boise"));
	for (const key of M.SITE_LOOKUPS) assert.equal(cleared.site[key], null, key);
	assert.equal(cleared.site.address, "1 Main St, Boise");
	assert.equal(cleared.site.jurisdiction, m.site.jurisdiction);
	assert.equal(m.site.elevation_ft, 4226, "the model it was given is untouched");
	const refilled = M.merge_derived(cleared, replies["lobby-sheet-wall-8ft"].model);
	assert.equal(refilled.site.elevation_ft, 4226, "the next derive fills them again");
});

await test("phase J: an engine-filled site value follows the engine, a typed one stays", () => {
	let mine = M.model_from_kit(lobby_kit, "full");
	const src = (v) => ({ src: "default", v });
	mine = M.set_in(mine, "site", Object.assign({}, mine.site, { elevation_ft: 4300, frost_depth_in: 30, air_temp_f: 95,
		sources: { elevation_ft: src(4300), frost_depth_in: src(30), air_temp_f: src(95) } }));
	const typed = M.edit(mine, "site.air_temp_f", 100);
	assert.equal(typed.site.sources.air_temp_f, undefined, "typing a value takes it off the engine");
	assert.equal(typed.setup.features, mine.setup.features, "a site edit pins no section");
	// the reply to that model in another state: the engine's values for it, one it has no figure for
	// (blanked, its source gone), and the typed value as it was sent
	const derived = JSON.parse(JSON.stringify(typed));
	Object.assign(derived.site, { jurisdiction: "AZ", elevation_ft: 1100, frost_depth_in: null, sources: { elevation_ft: src(1100) } });
	const out = M.merge_derived(typed, derived);
	assert.equal(out.site.elevation_ft, 1100, "an engine value follows the new state");
	assert.equal(out.site.frost_depth_in, null, "one the new state lacks is blanked, not left from the old state");
	assert.equal(out.site.air_temp_f, 100, "the typed value stays");
	assert.deepEqual(out.site.sources, { elevation_ft: src(1100) }, "the reply's record replaces ours");
	const none = M.merge_derived(out, Object.assign({}, derived, { site: Object.assign({}, derived.site, { sources: undefined }) }));
	assert.equal(none.site.elevation_ft, 1100);
	assert.equal(none.site.sources, null, "no record left: no sources");
	assert.equal(M.clear_site_lookups(out).site.sources, null, "Refresh drops the record too");
	for (const key of ["seismic_sdc", "sds", "sd1", "winterize"]) assert.ok(M.SITE_LOOKUPS.includes(key), key);
	// full mode's answer to a new address: only what the lookups filled goes
	const looked = M.set_in(mine, "site", Object.assign({}, mine.site, { lat: 40.7, lng: -111.9, elevation_ft: 4226, hardness_ppm: 300,
		sources: { lat: { src: "lookup", v: 40.7 }, lng: { src: "lookup", v: -111.9 }, elevation_ft: { src: "lookup", v: 4226 },
			frost_depth_in: src(30) } }));
	const moved = M.clear_looked_up(looked);
	assert.equal(moved.site.lat, null);
	assert.equal(moved.site.elevation_ft, null);
	assert.equal(moved.site.hardness_ppm, 300, "a typed value stays");
	assert.equal(moved.site.frost_depth_in, 30, "a state default stays");
	assert.deepEqual(moved.site.sources, { frost_depth_in: src(30) });
});

console.log("charts");

await test("the fixture's charts pass the sanitizer unchanged", () => {
	for (const r of Object.values(replies)) {
		for (const key of ["duty_svg", "schematic_svg"]) {
			const svg = r.charts[key];
			assert.equal(C.safe_svg(svg), svg, `${r.model.kit} ${key}`);
		}
	}
	assert.ok(C.safe_svg(D.duty_svg(160, 62)), "the demo's redrawn chart too");
});

await test("anything that could run or fetch is refused", () => {
	const bad = [
		'<svg><script>alert(1)</script></svg>',
		'<svg onload="alert(1)"></svg>',
		'<svg><foreignObject><div>x</div></foreignObject></svg>',
		'<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
		'<svg><rect style="fill:url(http://x/y)"/></svg>',
		'<svg><rect fill="url(http://x/y)"/></svg>',
		'<svg><image href="http://x/y.png"/></svg>',
		"<!DOCTYPE svg><svg></svg>",
		"<div>not svg</div>",
		null,
	];
	for (const svg of bad) assert.equal(C.safe_svg(svg), "", String(svg));
	assert.ok(C.safe_svg('<svg viewBox="0 0 1 1"><rect fill="url(#g)"/></svg>'), "a reference inside the drawing is fine");
	assert.match(C.chart_html(null, { esc, empty: "No chart" }), /No chart/);
	assert.match(C.chart_html('<svg viewBox="0 0 1 1"></svg>', { esc, label: 'a"b' }), /aria-label="a&quot;b"/);
});

console.log("verdict");

await test("an issue goes to the step that fixes it", () => {
	assert.equal(V.step_for_issue({ section: "treatment" }), "pump_room");
	assert.equal(V.step_for_issue({ section: "basin" }), "basins");
	assert.equal(V.step_for_issue({ section: "basins" }), "basins", "issues.py v1 vocabulary");
	assert.equal(V.step_for_issue({ section: "site" }), "site");
	assert.equal(V.step_for_issue({ section: "x", step: "outputs" }), "outputs");
	assert.equal(V.step_for_issue({ section: "deliverables" }), "review");
	assert.equal(V.step_for_issue(null), "review");
});

await test("the panel: words on every chip, escaped server text, the rollups and the chart", () => {
	const r = JSON.parse(JSON.stringify(replies["plaza-interactive-24jet"]));
	r.issues[0].title = "<img src=x onerror=alert(1)>";
	const html = V.verdict_html(r, H, { pending: true });
	assert.ok(!html.includes("<img"), "escaped");
	assert.match(html, /1 blocker/);
	assert.match(html, /1 warning/);
	assert.match(html, /Package not ready/);
	assert.match(html, /Updating/);
	assert.match(html, /\$169,320/);
	assert.match(html, /data-fd-goto="pump_room"/);
	assert.match(html, /<svg/);
	const strip = V.strip_html(r, H);
	assert.match(strip, /1 blocker</, "singular");
	assert.match(strip, /1 warning</);
	assert.ok(!/sf-chip--\w+">\d+<\/span>/.test(strip), "no bare number for a chip");
	assert.match(V.verdict_html(null, H, {}), /Not worked out yet/, "no green count before the first reply");
	assert.ok(!/0 blockers/.test(V.verdict_html(null, H, {})));
	assert.deepEqual(V.sort_issues([{ severity: "info" }, { severity: "blocker" }, null, { severity: "warning" }]).map((i) => i.severity), [
		"blocker",
		"warning",
		"info",
	]);
});

console.log("steps render");

function ctx_for(reply, mode, extra) {
	const model = M.set_mode(M.merge_derived(M.model_from_kit(boot.kits.find((k) => k.key === reply.model.kit), mode), reply.model), mode);
	return Object.assign(
		{
			h: H,
			boot,
			model,
			kit: boot.kits.find((k) => k.key === model.kit),
			reply,
			values: reply.values,
			full: mode === "full",
			design: { name: "DEMO-X", design_title: "T", project: "", opportunity: "", customer: "", status: "Draft" },
			can: boot.can,
			can_write: true,
			can_create: true,
			designs_state: { kind: "ready", rows: designs.designs },
			new_form: {},
			acking: null,
			busy: false,
			last_push: "",
			adv: () => true,
			section_card: (s) => `<div data-section-card="${s}"></div>`,
		},
		extra || {}
	);
}

await test("every step renders for every demo design, in both modes", () => {
	for (const reply of Object.values(replies)) {
		for (const mode of ["quick", "full"]) {
			for (const step of R.steps_for(mode)) {
				const html = STEPS[step].render(ctx_for(reply, mode));
				assert.ok(typeof html === "string" && html.includes("data-fd-stage-title"), `${reply.model.kit} ${mode} ${step}`);
			}
		}
	}
	const list = STEPS.start.render(ctx_for(replies["pond-aerator-1ac"], "quick", { design: null }));
	for (const d of designs.designs) assert.ok(list.includes(`data-fd-open="${d.name}"`), d.name);
});

await test("phase J: the Site step lists every state and says what the state enforces", () => {
	const reply = replies["plaza-interactive-24jet"];
	for (const mode of ["quick", "full"]) {
		const html = STEPS.site.render(ctx_for(reply, mode));
		assert.match(html, /data-fd-path="site\.jurisdiction"/, `${mode}: the picker is on the step`);
		for (const o of boot.options.jurisdictions) assert.ok(html.includes(`value="${o.value}"`), `${mode}: ${o.value}`);
		assert.match(html, /Codes and jurisdiction/);
		assert.match(html, /data-fd-codes/);
		assert.match(html, /Health authority/);
		assert.match(html, /interactive water features/, "the venues Utah regulates");
	}
	const full = STEPS.site.render(ctx_for(reply, "full"));
	assert.match(full, /data-fd-path="site\.seismic_sdc"/);
	assert.match(full, /data-fd-path="site\.winterize"/);
	const v = reply.results.jurisdiction.value;
	const card = STEPS.site.codes_html(H, reply, null);
	assert.equal(card.includes("confirm") || card.includes("Confirm"), Boolean(v.confirm_ahj), "the AHJ line when a code needs it");
	const local = JSON.parse(JSON.stringify(reply));
	Object.assign(local.results.jurisdiction.value, { editions: ["NEC 2020", "IBC 2018"], confirm_ahj: true,
		ahj_note: "Confirm the IBC edition with the authority having jurisdiction: Testland adopts it locally.",
		licensing: "Contractor: <b>pool</b> license.", regulated_venues: ["public pools"], iwf_rule: null });
	const html = STEPS.site.codes_html(H, local, null);
	assert.match(html, /NEC 2020, IBC 2018/);
	assert.match(html, /Testland adopts it locally/);
	assert.match(html, /fd-hint--warn/);
	assert.ok(!html.includes("<b>"), "server text is escaped");
	const unknown = { results: { jurisdiction: { value: { known: false, code: "XX" } } } };
	assert.match(STEPS.site.codes_html(H, unknown, null), /No jurisdiction data for XX/);
	assert.match(STEPS.site.codes_html(H, null, null), /Working out/);
});

await test("phase J: a derive repaints the codes card in place", async () => {
	const { d } = await controller();
	const codes = { innerHTML: "" };
	const found = { innerHTML: "" };
	d.stage.querySelector = (sel) => (sel === "[data-fd-codes]" ? codes : sel === "[data-fd-site-found]" ? found : null);
	d.stage.querySelectorAll = () => [];
	d.root.querySelector = () => null;
	d.reply = replies["lobby-sheet-wall-8ft"];
	d.paint();
	assert.match(codes.innerHTML, /Health authority/);
	d.reply = { results: { jurisdiction: { value: { known: false, code: "XX" } } } };
	d.paint();
	assert.match(codes.innerHTML, /No jurisdiction data for XX/);
	d.autosave.cancel();
});

await test("quick Features draws exactly the kit's headline inputs", () => {
	for (const reply of Object.values(replies)) {
		const ctx = ctx_for(reply, "quick");
		const html = STEPS.features.render(ctx);
		const paths = [...html.matchAll(/data-fd-(?:path|tile)="([^"]+)"/g)].map((m) => m[1]);
		assert.deepEqual([...new Set(paths)].sort(), ctx.kit.headline.map((h) => h.path).sort(), reply.model.kit);
	}
});

await test("full Pump room has a policy select for every ladder section it holds", () => {
	const html = STEPS.pump_room.render(ctx_for(replies["plaza-interactive-24jet"], "full"));
	for (const s of ["piping", "pump", "control", "bypass", "surge", "treatment", "lighting", "electrical", "winterization"]) {
		assert.ok(html.includes(`data-fd-policy="${s}"`), s);
	}
	assert.ok(html.includes('data-fd-catalog="pump"'), "a catalog picker for the pump");
	assert.ok(html.includes('data-fd-catalog="chemical"'), "the chlorinator searches its own class, from boot");
	assert.ok(html.includes('data-fd-catalog="drive"'), "the VFD too");
	const no_class = JSON.parse(JSON.stringify(boot));
	delete no_class.catalogs.slot_classes.vfd;
	const plain = STEPS.pump_room.render(ctx_for(replies["plaza-interactive-24jet"], "full", { boot: no_class }));
	assert.ok(!plain.includes('data-fd-catalog="drive"'));
	assert.match(plain, /Item code, or blank for generic/, "a slot with no class is an item code, never a pump search");
	assert.match(plain, /data-fd-kind="text" data-fd-null/, "an emptied item code is null (generic), not an empty name");
	assert.ok(html.includes('aria-expanded="true"'), "Advanced reveals are buttons with aria-expanded");
});

await test("Review offers the remedies and a sign-off for a warning only", () => {
	const html = STEPS.review.render(ctx_for(replies["plaza-interactive-24jet"], "full"));
	assert.match(html, /data-fd-remedy="IWF_UV_REQUIRED"/);
	assert.match(html, /data-fd-ack="SUCTION_VELOCITY\|s1"/);
	assert.ok(!/data-fd-ack="IWF_UV_REQUIRED"/.test(html), "a blocker cannot be signed off");
	const ro = STEPS.review.render(ctx_for(replies["plaza-interactive-24jet"], "full", { can_write: false }));
	assert.ok(!/data-fd-ack=/.test(ro), "no sign-off without write");
});

await test("Outputs: the budget, the BOM, and print links only to this site", async () => {
	const html = STEPS.outputs.render(ctx_for(replies["lobby-sheet-wall-8ft"], "full"));
	assert.match(html, /\$36,506/);
	assert.match(html, /data-fd-action="push-quotation"/);
	assert.ok(!/Contingency/.test(html), "no contingency in the demo: the foot is the budget alone");
	// With a contingency, the foot is the subtotal, the contingency and the budget (README, rollups).
	const priced = JSON.parse(JSON.stringify(replies["lobby-sheet-wall-8ft"]));
	priced.rollups.budget_total = Math.round(priced.rollups.bom_subtotal * 1.1 * 100) / 100;
	const with_c = STEPS.outputs.render(ctx_for(priced, "full"));
	assert.match(with_c, /Subtotal<\/th><td class="fd-num">\$36,506<\/td>/);
	assert.match(with_c, /Contingency<\/th><td class="fd-num">\$3,651<\/td>/);
	assert.match(with_c, /Budget<\/th><td class="fd-num"><strong>\$40,157<\/strong>/);
	// Every paper boot.prints names links to the saved design's print view (phase G).
	assert.match(html, /href="\/printview\?doctype=Water%20Feature%20Design&amp;name=DEMO-X&amp;format=Water%20Feature%20Design%20-%20Design%20Report&amp;no_letterhead=1"/);
	assert.match(html, /format=Water%20Feature%20Design%20-%20Budget%20Proposal/);
	assert.match(html, /format=Water%20Feature%20Design%20-%20Submittal%20Packet/);
	assert.ok(!/not available yet/.test(html), "every demo paper has a format in boot.prints");
	// Send design: a paper picker over boot.prints and the button, on a saved design with the email permission.
	assert.match(html, /<select id="fd-send-format" class="fd-input" data-fd-send-format>/);
	assert.match(html, /<option value="Water Feature Design - Design Report">Design report<\/option>/);
	assert.match(html, /data-fd-action="send-design">/);
	const no_email = STEPS.outputs.render(ctx_for(replies["lobby-sheet-wall-8ft"], "full", { can: Object.assign({}, boot.can, { email: false }) }));
	assert.match(no_email, /data-fd-action="send-design" disabled/);
	const bare = JSON.parse(JSON.stringify(boot));
	bare.prints = bare.prints.filter((p) => p.key !== "budget");
	assert.match(STEPS.outputs.render(ctx_for(replies["lobby-sheet-wall-8ft"], "full", { boot: bare })), /Budget proposal <span class="fd-muted">\(not available yet\)/);
	assert.equal(STEPS.outputs.print_url(boot, "", "budget"), "");
	assert.equal(STEPS.outputs.print_url(boot, "A&B", "nope"), "");
	assert.match(STEPS.outputs.print_url(boot, "A&B", "budget"), /name=A%26B&/);
	assert.equal(STEPS.outputs.safe_desk_url("/printview?doctype=Water%20Feature%20Design&name=X"), "/printview?doctype=Water%20Feature%20Design&name=X");
	assert.equal(STEPS.outputs.safe_desk_url("javascript:alert(1)"), "");
	assert.equal(STEPS.outputs.safe_desk_url("https://evil.example/x"), "");
	assert.equal(STEPS.outputs.safe_desk_url('/desk/x" onclick="y'), "");
	const unsaved = STEPS.outputs.render(ctx_for(replies["lobby-sheet-wall-8ft"], "full", { design: { name: null } }));
	assert.match(unsaved, /data-fd-action="push-quotation" disabled/);
	assert.match(unsaved, /save the design to print it/);
	assert.ok(!/href="\/printview/.test(unsaved), "no print link before the design is saved");
	assert.match(unsaved, /data-fd-action="send-design" disabled/);
});

console.log("demo fixture and contract");

const REPLY_KEYS = ["model", "results", "values", "rollups", "issues", "readiness", "verdict", "bom", "charts", "outputs"];
const ROLLUP_KEYS = [
	"design_flow_gpm",
	"operating_flow_gpm",
	"tdh_ft",
	"operating_head_ft",
	"selected_pump",
	"pump_kw",
	"total_basin_gallons",
	"budget_total",
	"bom_subtotal",
	"om_cost_per_year",
	"blocker_count",
	"warning_count",
	"completion_percent",
	"issue_ready",
];
const ISSUE_KEYS = ["key", "code", "severity", "title", "detail", "section", "fix_hint", "citation", "remedies"];
const BOM_KEYS = ["section", "item", "description", "qty", "uom", "rate", "amount", "source"];

await test("every fixture reply has the contract's keys", () => {
	for (const r of Object.values(replies)) {
		for (const k of REPLY_KEYS) assert.ok(k in r, `${r.model.kit}: ${k}`);
		for (const k of ROLLUP_KEYS) assert.ok(k in r.rollups, `${r.model.kit}: rollups.${k}`);
		for (const i of r.issues) for (const k of ISSUE_KEYS) assert.ok(k in i, `${i.key}: ${k}`);
		for (const l of r.bom) for (const k of BOM_KEYS) assert.ok(k in l, `bom ${k}`);
		for (const k of ["duty_svg", "schematic_svg", "oneline_svg"]) assert.ok(k in r.charts, k);
		assert.equal(r.model.schema, 2);
		assert.ok(["good", "warn", "bad"].includes(r.verdict.level));
		const total = Math.round(r.bom.reduce((s, l) => s + l.amount, 0) * 100) / 100;
		assert.equal(r.rollups.bom_subtotal, total, "the subtotal is the BOM's sum");
		assert.equal(r.rollups.budget_total, total, "the demo carries no contingency, so its budget is that sum");
	}
	for (const k of ["schema", "can", "kits", "families", "sections", "options", "catalogs", "house_rules", "prints"]) assert.ok(k in boot, `boot.${k}`);
	for (const kit of boot.kits) {
		assert.ok(replies[kit.key], `a reply for ${kit.key}`);
		for (const h of kit.headline) assert.notEqual(M.get_in(kit.model, h.path), undefined, `${kit.key}: ${h.path}`);
	}
	assert.deepEqual(boot.sections.map((s) => s.key), M.SECTION_KEYS, "a section entry per setup section, in ladder order");
});

function demo() {
	return D.create_demo(fixture, { delay: 0 });
}
const BASE = A.METHOD_BASE;

await test("the demo answers every contract endpoint", async () => {
	const t = demo();
	const api = A.create_api(t);
	assert.equal((await api.boot()).kits.length, 3);
	const list = await api.list_designs();
	assert.deepEqual(list.designs.map((d) => d.name).sort(), ["DEMO-LOBBY", "DEMO-PLAZA", "DEMO-POND"]);
	const d = await api.get_design("DEMO-PLAZA");
	assert.equal(d.model.mode, "full");
	assert.equal(d.model.schema, 2);
	const r = await api.derive(d.model);
	assert.equal(r.rollups.blocker_count, 1);
	const saved = await api.save_design(null, M.model_from_kit(lobby_kit, "quick"), { design_title: "New wall" });
	assert.match(saved.name, /^DEMO-NEW-\d{3}$/);
	assert.equal((await api.get_design(saved.name)).design_title, "New wall");
	const q = await api.push_quotation("DEMO-LOBBY");
	assert.equal(q.doctype, "Quotation");
	assert.equal(q.demo, true);
	assert.ok(q.lines > 0 && q.text_lines > 0);
	assert.equal((await api.push_material_request("DEMO-LOBBY")).doctype, "Material Request");
	assert.equal((await api.email_draft("DEMO-LOBBY", "Water Feature Design - Design Report")).demo, true, "the demo emails nothing");
	assert.deepEqual((await api.catalog_search("pump", "goulds")).map((x) => x.item), ["GLD-3656-5"]);
	assert.equal((await api.catalog_search("pump", "")).length, 4);
	await assert.rejects(api.get_design("NOPE"), (e) => e.status === 404);
	const weir = await api.run_calc("weir_flow", { length_ft: 99 });
	assert.equal(weir.calc, "weir_flow");
	assert.deepEqual(weir.demo_inputs, calc.weir_flow.inputs, "the demo says which inputs its answer is for");
	await assert.rejects(api.run_calc("water_hammer", {}), (e) => e.status === 417, "a calc the fixture has no answer for");
	await assert.rejects(t(BASE + "no_such_method", {}), (e) => e.status === 404);
	await assert.rejects(api.acknowledge_issue("DEMO-PLAZA", "IWF_UV_REQUIRED", "x"), (e) => e.status === 417, "a blocker cannot be acknowledged");
});

await test("the demo moves with the headline inputs", async () => {
	const base = D.derive(fixture, M.model_from_kit(lobby_kit, "quick"));
	const wider = D.derive(fixture, M.edit(M.model_from_kit(lobby_kit, "quick"), "features.0.inputs.length_ft", 16));
	assert.equal(base.rollups.design_flow_gpm, 120);
	assert.equal(wider.rollups.design_flow_gpm, 240);
	assert.ok(wider.rollups.budget_total > base.rollups.budget_total);
	assert.notEqual(wider.charts.duty_svg, base.charts.duty_svg, "the chart is redrawn at the new duty point");
	const heavy = D.derive(fixture, M.edit(M.model_from_kit(lobby_kit, "quick"), "features.0.effect.look", "heavy"));
	assert.equal(heavy.rollups.design_flow_gpm, 150);
});

await test("a remedy clears its issue; signing off the warnings makes the package ready", async () => {
	const plaza_kit = boot.kits.find((k) => k.key === "plaza-interactive-24jet");
	let m = M.model_from_kit(plaza_kit, "full");
	assert.equal(D.derive(fixture, m).rollups.blocker_count, 1);
	m = M.edit(m, "treatment.uv", true);
	const r = D.derive(fixture, m);
	assert.equal(r.rollups.blocker_count, 0);
	assert.equal(r.verdict.package_ready, false, "a warning is still open");
	assert.equal(r.readiness.sections.find((x) => x.key === "treatment").status, "ok", "the blocked section is ready once its blocker is gone");
	const api = A.create_api(demo());
	const acks = await api.acknowledge_issue("DEMO-LOBBY", "TROUGH_SURGE|b1", "Check valve at the header");
	assert.equal(acks.acks.length, 1);
	await api.acknowledge_issue("DEMO-LOBBY", "HW_DW_SPREAD", "Within tolerance");
	const d = await api.get_design("DEMO-LOBBY");
	const after = await api.derive(d.model);
	assert.equal(after.rollups.issue_ready, true);
	assert.equal(after.verdict.level, "good");
	assert.ok(after.issues.find((i) => i.key === "HW_DW_SPREAD").acknowledged.note);
});

await test("a model with no kit gets an honest blank reply", () => {
	const r = D.derive(fixture, M.blank_model("full"));
	for (const k of REPLY_KEYS) assert.ok(k in r, k);
	assert.equal(r.rollups.budget_total, null);
	assert.equal(r.issues[0].severity, "info");
});

await test("the demo remembers its saves in the storage it is given, and survives a broken one", async () => {
	const store = new Map();
	const storage = { getItem: (k) => store.get(k) || null, setItem: (k, v) => store.set(k, v) };
	const first = A.create_api(D.create_demo(fixture, { delay: 0, storage }));
	const saved = await first.save_design(null, M.model_from_kit(lobby_kit, "quick"), { design_title: "Kept" });
	const second = A.create_api(D.create_demo(fixture, { delay: 0, storage }));
	assert.equal((await second.get_design(saved.name)).design_title, "Kept");
	const broken = {
		getItem: () => {
			throw new Error("denied");
		},
		setItem: () => {
			throw new Error("denied");
		},
	};
	const third = A.create_api(D.create_demo(fixture, { delay: 0, storage: broken }));
	assert.ok((await third.save_design(null, M.model_from_kit(lobby_kit, "quick"), { design_title: "x" })).name);
});

console.log("controller: saves before what reads the stored design");

// The real controller against stand-ins: enough of a DOM to construct and draw, and an api whose calls
// we hold open and release in the order a slow server would.
function stub_el() {
	return {
		innerHTML: "",
		hidden: false,
		addEventListener() {},
		setAttribute() {},
		querySelector: () => null,
		querySelectorAll: () => [],
		contains: () => false,
		appendChild() {},
	};
}
async function controller() {
	const app = await import(url(path.join(FD, "app.js")));
	const root = stub_el();
	const win = {
		addEventListener() {},
		setTimeout: (fn, ms) => setTimeout(fn, ms),
		clearTimeout: (id) => clearTimeout(id),
		localStorage: { getItem: () => null, setItem() {} },
		location: { pathname: "/desk/fountain-designer", search: "" },
		history: { state: null, pushState() {}, replaceState() {} },
	};
	const sf = { esc, t: __, chip: H.chip, state: { html: H.state_html, render() {} }, toast() {} };
	const d = app.mount({ root, sf, frappe: {}, win, doc: { activeElement: null, getElementById: () => null }, __ });
	const log = [];
	const held = [];
	const hold = (name, value) => (...args) =>
		new Promise((resolve, reject) => {
			log.push(name);
			held.push({ name, args, resolve: () => resolve(value), reject });
		});
	d.api = {
		save_design: hold("save", { status: "Derived" }),
		push_quotation: hold("push", { doctype: "Quotation", name: "Q1", lines: 1, text_lines: 0 }),
		push_material_request: hold("push_mr", { doctype: "Material Request", name: "M1" }),
		acknowledge_issue: hold("ack", { acks: [{ issue_key: "K", note: "n" }] }),
		derive: () => new Promise(() => {}),
	};
	d.deriver = A.create_deriver({ derive: () => new Promise(() => {}), delay: 100000 });
	d.boot = Object.assign({}, boot);
	d.stage = stub_el();
	d.model = M.model_from_kit(lobby_kit, "quick");
	d.meta = { name: "D1", design_title: "T", can_write: true };
	return { d, log, held };
}

await test("a push waits for the save in flight and the one after it", async () => {
	const { d, log, held } = await controller();
	d.edit("features.0.inputs.length_ft", 9);
	d.save(); // the autosave goes out
	d.edit("features.0.inputs.length_ft", 10); // and the person edits again while it is out
	d.push("quotation");
	await settle();
	assert.deepEqual(log, ["save"], "nothing pushed while a save is out");
	held[0].resolve();
	await settle();
	assert.deepEqual(log, ["save", "save"], "the edit made meanwhile is saved first");
	assert.equal(JSON.parse(held[1].args[1] ? JSON.stringify(held[1].args[1]) : "{}").features[0].inputs.length_ft, 10);
	held[1].resolve();
	await settle();
	assert.deepEqual(log, ["save", "save", "push"], "then the push, built from what is stored");
	d.autosave.cancel();
});

await test("Send design saves first, then opens the composer with the paper attached and ticked", async () => {
	const { d, log, held } = await controller();
	const file = { name: "F1", file_name: "D1-print-design-report.pdf", file_url: "/private/files/x.pdf" };
	const opened = [];
	d.env.frappe = {
		views: {
			CommunicationComposer: class {
				constructor(opts) {
					this.opts = opts;
					this.ticked = [];
					opened.push(this);
				}
				render_attachment_rows(f) {
					this.ticked.push(f.name);
				}
			},
		},
	};
	d.api.email_draft = hold_into(log, held, "email", { subject: "T: Design report (#D1)", recipients: "a@b.c", message: "<p>x</p>", attachment: file });
	d.edit("features.0.inputs.length_ft", 9);
	d.send_design("Water Feature Design - Design Report");
	await settle();
	assert.deepEqual(log, ["save"], "nothing is prepared while the design is unsaved");
	held[0].resolve();
	await settle();
	assert.deepEqual(log, ["save", "email"]);
	assert.deepEqual(held[1].args, ["D1", "Water Feature Design - Design Report"]);
	held[1].resolve();
	await settle();
	assert.equal(opened.length, 1);
	assert.deepEqual(opened[0].opts.doc, { doctype: "Water Feature Design", name: "D1" }, "the email links to the design's timeline");
	assert.equal(opened[0].opts.recipients, "a@b.c");
	assert.deepEqual(opened[0].ticked, ["F1"], "the paper is attached, not just offered");
	assert.equal(d.busy, false);
	d.autosave.cancel();
});

function hold_into(log, held, name, value) {
	return (...args) =>
		new Promise((resolve, reject) => {
			log.push(name);
			held.push({ name, args, resolve: () => resolve(value), reject });
		});
}

await test("a failed save pushes nothing", async () => {
	const { d, log, held } = await controller();
	d.edit("features.0.inputs.length_ft", 9);
	d.push("material_request");
	await settle();
	held[0].reject({ message: "Locked" });
	await settle();
	assert.deepEqual(log, ["save"]);
	assert.equal(d.busy, false);
	assert.equal(d.dirty, true, "still unsaved, and the footer says so");
});

await test("a sign-off goes after the save, and the acks are saved once more after it", async () => {
	const { d, log, held } = await controller();
	d.edit("features.0.inputs.length_ft", 9);
	d.acknowledge("K", "n");
	await settle();
	assert.deepEqual(log, ["save"]);
	held[0].resolve();
	await settle();
	assert.deepEqual(log, ["save", "ack"]);
	held[1].resolve();
	await settle();
	assert.deepEqual(d.model.acks, [{ issue_key: "K", note: "n" }]);
	assert.equal(d.dirty, true, "an autosave carrying the acks is due");
	d.autosave.flush();
	await settle();
	assert.deepEqual(log, ["save", "ack", "save"]);
	assert.deepEqual(held[2].args[1].acks, [{ issue_key: "K", note: "n" }]);
	held[2].resolve();
	await settle();
});

await test("switching a hand-sized design to Quick asks first, and No changes nothing", async () => {
	const { d } = await controller();
	d.model = M.edit(M.set_mode(d.model, "full"), "basins.0.depth_in", 20);
	let asked = null;
	d.ask = (message, yes) => (asked = { message, yes });
	d.choose_tile("mode", "quick");
	assert.match(asked.message, /Basins/);
	assert.equal(d.model.mode, "full", "nothing until they say yes");
	assert.equal(d.model.setup.basin, "custom");
	asked.yes();
	assert.equal(d.model.mode, "quick");
	asked = null;
	d.choose_tile("mode", "full");
	assert.equal(asked, null, "to Full loses nothing, so it does not ask");
	d.autosave.cancel();
});

await test("a stored model that is not schema 2 is never opened as a blank design", async () => {
	const { d } = await controller();
	d.api.get_design = () => Promise.resolve({ name: "OLD-1", model: null, mode: "Full" });
	let shown = null;
	d.sf.state.render = (el, kind, opts) => (shown = { kind, title: opts.title });
	d.open_design("OLD-1");
	await settle();
	assert.equal(d.model, null);
	assert.equal(d.meta, null, "no design to autosave over the stored one");
	assert.equal(shown.kind, "error");
	assert.match(shown.title, /OLD-1/);
});

console.log("the Calculator drawer (Review step)");

const CALC = await import(url(path.join(FD, "steps", "calculator.js")));

await test("boot lists every calc with typed inputs, and the fixture's examples are among them", () => {
	const list = CALC.calcs(boot);
	assert.ok(list.length >= 40, "the wizard's whole calc list");
	const kinds = new Set(list.flatMap((c) => c.inputs.map((i) => i.kind)));
	for (const k of kinds) assert.ok(["number", "int", "select", "check", "text", "json"].includes(k), k);
	for (const key of Object.keys(calc)) assert.ok(list.some((c) => c.key === key), key);
});

await test("the drawer draws the picked calc's fields under the calc. prefix, never a model path", () => {
	const ctx = Object.assign(ctx_for(replies["plaza-interactive-24jet"], "full"), { calc: { key: "weir_flow", raw: null } });
	const html = STEPS.review.render(ctx);
	assert.match(html, /data-fd-drawer="calculator"/);
	assert.match(html, /data-fd-calc-form/);
	const paths = [...html.matchAll(/data-fd-path="([^"]+)"/g)].map((m) => m[1]).filter((p) => p.startsWith("calc."));
	assert.deepEqual(paths, ["calc._pick", "calc.length_ft", "calc.head_in", "calc.contractions"]);
	assert.match(html, /<option value="weir_flow" selected>/);
});

await test("coerce: numbers and lists read or are named; a blank box is left to the engine", () => {
	const tdh = CALC.spec(boot, "total_dynamic_head");
	const ok = CALC.coerce(tdh, { segments: '[{"flow_gpm": 10}]', static_lift_ft: "2.5", c: "" });
	assert.deepEqual(ok, { inputs: { segments: [{ flow_gpm: 10 }], static_lift_ft: 2.5 }, errors: [] });
	const bad = CALC.coerce(tdh, { segments: "{oops", static_lift_ft: "two" });
	assert.deepEqual(bad.errors, ["Pipe segments", "Static lift"]);
	const weir = CALC.spec(boot, "weir_flow");
	assert.deepEqual(CALC.coerce(weir, { contractions: "1.5" }).errors, ["End contractions"], "an int is whole");
	assert.deepEqual(CALC.coerce(CALC.spec(boot, "heating_load"), { wind: false }).inputs.wind, false);
});

await test("a small result keeps its significant figures", () => {
	assert.equal(CALC.number_text(0.004123), "0.004123");
	assert.equal(CALC.number_text(673.24608), "673.2");
	assert.equal(CALC.number_text(22.433333), "22.43");
	assert.equal(CALC.number_text(12345.67), "12,345.7");
	assert.equal(CALC.number_text(30), "30");
	const html = CALC.result_html({ h: H }, CALC.spec(boot, "hazen_williams_loss"), {
		result: { calc: "hazen_williams_loss", value: 0.0042, unit: "ft", steps: [], warnings: [], citations: [], options: [] },
	});
	assert.match(html, /0\.0042 ft/);
	assert.doesNotMatch(html, /0\.00 ft/);
});

await test("the result shows the value, formula, every step, the sources, warnings and options", () => {
	const s = CALC.spec(boot, "size_pipe");
	const r = calc.size_pipe.result;
	const html = CALC.result_html({ h: H }, s, { result: r });
	assert.ok(html.includes(esc(r.formula)), "the formula");
	for (const step of r.steps) assert.ok(html.includes(esc(step)), step);
	assert.ok(r.options.every((o) => html.includes(esc(o.label))), "every option is a row");
	assert.match(html, /Recommended/);
	assert.ok(r.citations.every((c) => html.includes(esc(c))));
	assert.equal(CALC.result_html({ h: H }, CALC.spec(boot, "weir_flow"), { result: r }), "", "an answer for another calc is not drawn");
	assert.match(CALC.result_html({ h: H }, s, { error: "Unknown calculation: x" }), /sf-state--error/);
});

await test("controller: a run sends the coerced inputs, draws the newest answer and never touches the model", async () => {
	const { d } = await controller();
	const before = JSON.stringify(d.model);
	const sent = [];
	let answer;
	d.api.run_calc = (key, inputs) => (sent.push([key, inputs]), new Promise((resolve) => (answer = resolve)));
	d.calc_input("calc._pick", { value: "weir_flow" }, true);
	d.calc_input("calc.length_ft", { value: "4" }, false);
	d.calc_input("calc.head_in", { value: "0.5" }, false);
	const run = d.run_calc();
	assert.equal(d.calc.busy, true);
	assert.deepEqual(sent, [["weir_flow", { length_ft: 4, head_in: 0.5, contractions: 2 }]]);
	answer(calc.weir_flow.result);
	await run;
	assert.equal(d.calc.result.calc, "weir_flow");
	assert.equal(d.calc.busy, false);
	d.calc_input("calc._pick", { value: "basin_volume" }, true);
	assert.equal(d.calc.result, null, "another calc starts empty");
	assert.equal(JSON.stringify(d.model), before, "the design is untouched");
	d.calc_input("calc.length_in", { value: "ten" }, false);
	d.run_calc();
	assert.match(d.calc.error, /Length/);
	d.autosave.cancel();
});

await test("controller: a derive's repaint leaves the Calculator drawer's fields alone", async () => {
	const { d } = await controller();
	const field = (path, value) => ({ value, type: "text", tagName: "INPUT", getAttribute: (n) => (n === "data-fd-path" ? path : null), hasAttribute: () => false });
	const calc_box = field("calc.length_ft", "4");
	const model_box = field("features.0.inputs.length_ft", "stale");
	d.stage.querySelectorAll = (sel) => (sel === "[data-fd-path]" ? [calc_box, model_box] : []);
	d.root.querySelector = () => null;
	d.paint();
	assert.equal(calc_box.value, "4", "the typed calc input survives");
	assert.equal(model_box.value, String(M.get_in(d.model, "features.0.inputs.length_ft")), "a model field is still repainted");
	d.autosave.cancel();
});

await settle();
if (unhandled.length) {
	failures += 1;
	console.log("  FAIL unhandled rejections: " + unhandled.map((r) => (r && r.message) || String(r)).join("; "));
}
console.log(`\n${checks} checks, ${failures} failed`);
process.exit(failures ? 1 : 0);
