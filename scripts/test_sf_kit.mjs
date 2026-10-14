#!/usr/bin/env node
/**
 * sf_kit, the shared page pieces (public/js/sf_kit/), executed under node.
 *
 * Every primitive is a pure ES module that takes what it needs from the page in an `env`, so it
 * runs here against a stand-in document, timers and frappe.call, and what it draws is read back
 * as markup. What this pins, because each part fails quietly on a real page:
 *
 *   - esc escapes the six characters and turns null/undefined into "", and planner_kit's
 *     `escape` IS that function (one implementation, re-exported);
 *   - call() settles exactly once, the same way, for every reply v16's request.js can produce:
 *     resolves r.message on 200; rejects {status, message} on a frappe.throw (417, the
 *     server's own words, HTML taken out), a 500 (a generic sentence, never the exception), a
 *     network failure (status 0), a frappe.call that throws, and a reply frappe answers with no
 *     callback at all; `silent` is passed through; nothing is ever left unhandled;
 *   - the state panel's roles, its one action button only when there is something to call,
 *     and an escaped title;
 *   - a chip always carries its word, an unknown tone is neutral, a wordless chip is not drawn;
 *   - cards and tiles escape everything and only make links of /desk/ routes and http(s) URLs;
 *   - the toast: one at a time, 8 s by default, held while the pointer is over it, its action
 *     closes it; and planner_kit's toast (prefix "pk") draws BYTE FOR BYTE what it drew before
 *     it moved here (SNAPSHOT, taken from the old planner_kit/toast.js with --print-pk-snapshot);
 *   - styles.js exports exactly the text of sf_kit.bundle.css;
 *   - the bundle installs window.sf_kit once, and injects no stylesheet.
 *
 * Run: node scripts/test_sf_kit.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const JS = path.join(APP, "public", "js");
const KIT = path.join(JS, "sf_kit");
const CSS_FILE = path.join(APP, "public", "css", "sf_kit.bundle.css");
const url = (file) => pathToFileURL(file).href;

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
		console.log("       " + (err && err.message ? err.message : String(err)).split("\n").join("\n       "));
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(rounds = 6) {
	for (let i = 0; i < rounds; i += 1) await tick();
}

// ------------------------------------------------------------------ a stand-in document

function attr_text(value) {
	return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

function node_text(value) {
	return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Just enough DOM for the toast: attributes kept in the order they were set, so a snapshot
 * also pins the order the code does things in. */
function make_el(tag) {
	const el = {
		tagName: tag.toUpperCase(),
		attrs: [],
		children: [],
		parentNode: null,
		listeners: {},
		text: null,
		html: null,
		setAttribute(name, value) {
			const found = el.attrs.find((a) => a[0] === name);
			if (found) found[1] = String(value);
			else el.attrs.push([name, String(value)]);
		},
		getAttribute(name) {
			const found = el.attrs.find((a) => a[0] === name);
			return found ? found[1] : null;
		},
		appendChild(child) {
			if (child.parentNode) child.parentNode.removeChild(child);
			child.parentNode = el;
			el.children.push(child);
			return child;
		},
		removeChild(child) {
			const index = el.children.indexOf(child);
			if (index >= 0) el.children.splice(index, 1);
			child.parentNode = null;
			return child;
		},
		addEventListener(type, fn) {
			(el.listeners[type] = el.listeners[type] || []).push(fn);
		},
		fire(type) {
			for (const fn of el.listeners[type] || []) fn({ type, target: el, preventDefault() {} });
		},
		find(cls) {
			for (const child of el.children) {
				if ((child.getAttribute("class") || "").split(" ").includes(cls)) return child;
				const deeper = child.find(cls);
				if (deeper) return deeper;
			}
			return null;
		},
		get outerHTML() {
			const attrs = el.attrs.map(([n, v]) => ` ${n}="${attr_text(v)}"`).join("");
			const inner =
				(el.text !== null ? node_text(el.text) : "") +
				(el.html !== null ? el.html : "") +
				el.children.map((c) => c.outerHTML).join("");
			const name = el.tagName.toLowerCase();
			return `<${name}${attrs}>${inner}</${name}>`;
		},
	};
	Object.defineProperty(el, "className", {
		get: () => el.getAttribute("class") || "",
		set: (v) => el.setAttribute("class", v),
	});
	Object.defineProperty(el, "type", {
		get: () => el.getAttribute("type") || "",
		set: (v) => el.setAttribute("type", v),
	});
	Object.defineProperty(el, "textContent", {
		get: () => (el.text !== null ? el.text : el.children.map((c) => c.textContent).join("")),
		set: (v) => {
			el.children = [];
			el.html = null;
			el.text = String(v);
		},
	});
	Object.defineProperty(el, "innerHTML", {
		get: () => el.html || "",
		set: (v) => {
			el.children = [];
			el.text = null;
			el.html = String(v);
		},
	});
	return el;
}

function make_doc() {
	const doc = { createElement: (tag) => make_el(tag) };
	doc.body = make_el("body");
	doc.head = make_el("head");
	return doc;
}

function make_timers() {
	const timers = new Map();
	let next = 1;
	return {
		set_timeout: (fn, ms) => {
			const id = next++;
			timers.set(id, { fn, ms });
			return id;
		},
		clear_timeout: (id) => timers.delete(id),
		pending: () => [...timers.values()].map((x) => x.ms),
		run: () => {
			const due = [...timers.entries()];
			timers.clear();
			for (const [, x] of due) x.fn();
		},
	};
}

function toast_env() {
	const doc = make_doc();
	const timers = make_timers();
	return { doc, timers, t: (s) => s, set_timeout: timers.set_timeout, clear_timeout: timers.clear_timeout };
}

/** What a toast draws, for each case the planners use: plain, with an action, and with a tone
 * the toast does not know and an action label but nothing to call. */
function toast_markup(create, extra_args) {
	const env = toast_env();
	const toast = create(env, ...extra_args);
	const out = [];
	out.push(toast("Dig moved to <b>Thu</b> & \"Fri\"").el.outerHTML);
	out.push(toast("Saved", { action_label: "Undo", on_action: () => {}, tone: "warning" }).el.outerHTML);
	out.push(toast("Done", { tone: "success", timeout: 3000 }).el.outerHTML);
	out.push(toast(null, { tone: "bogus", action_label: "Undo" }).el.outerHTML);
	return out;
}

// planner_kit/toast.js's markup before it became a wrapper around sf_kit (v1.623.0), printed by
// `node scripts/test_sf_kit.mjs --print-pk-snapshot` against that file. Never regenerate it from
// the new code: it is the record of what the planners' pk-toast CSS was written against.
const SNAPSHOT = String.raw`["<div class=\"pk-toast pk-toast-info\" role=\"status\" aria-live=\"polite\"><span class=\"pk-toast-text\">Dig moved to &lt;b&gt;Thu&lt;/b&gt; &amp; \"Fri\"</span><button type=\"button\" class=\"pk-toast-close\" aria-label=\"Dismiss\">&times;</button></div>","<div class=\"pk-toast pk-toast-warning\" role=\"status\" aria-live=\"polite\"><span class=\"pk-toast-text\">Saved</span><button type=\"button\" class=\"pk-toast-action\">Undo</button><button type=\"button\" class=\"pk-toast-close\" aria-label=\"Dismiss\">&times;</button></div>","<div class=\"pk-toast pk-toast-success\" role=\"status\" aria-live=\"polite\"><span class=\"pk-toast-text\">Done</span><button type=\"button\" class=\"pk-toast-close\" aria-label=\"Dismiss\">&times;</button></div>","<div class=\"pk-toast pk-toast-info\" role=\"status\" aria-live=\"polite\"><span class=\"pk-toast-text\"></span><button type=\"button\" class=\"pk-toast-close\" aria-label=\"Dismiss\">&times;</button></div>"]`;

if (process.argv.includes("--print-pk-snapshot")) {
	const { create_toast } = await import(url(path.join(JS, "planner_kit", "toast.js")));
	console.log(JSON.stringify(toast_markup(create_toast, [])));
	process.exit(0);
}

// ------------------------------------------------------------------ the modules

const kit = await import(url(path.join(KIT, "index.js")));
const { esc, t, call, chip, state_html, render_state, card_html, tiles_html, safe_href, create_toast, CSS } = kit;
const planner_escape = await import(url(path.join(JS, "planner_kit", "escape.js")));
const planner_toast = await import(url(path.join(JS, "planner_kit", "toast.js")));

console.log("esc and t");

await test("esc escapes the six characters and nothing else", () => {
	assert.equal(esc(`<b a="1" b='2'>&\``), "&lt;b a=&quot;1&quot; b=&#39;2&#39;&gt;&amp;&#96;");
	assert.equal(esc("plain words, 100% / ok"), "plain words, 100% / ok");
	assert.equal(esc(null), "");
	assert.equal(esc(undefined), "");
	assert.equal(esc(0), "0");
	assert.equal(esc(false), "false");
	assert.equal(esc("&amp;"), "&amp;amp;");
});

await test("planner_kit's escape is sf_kit's esc, not a copy", () => {
	assert.equal(planner_escape.escape, esc);
	// The rest of escape.js is still its own.
	assert.equal(planner_escape.safe_color("#12ab34", "x"), "#12ab34");
	assert.equal(planner_escape.hours(2.5), "2.5");
});

await test("t substitutes {0} without a Desk and defers to __ when there is one", () => {
	assert.equal(t("{0} of {1}", [1, 2]), "1 of 2");
	assert.equal(t("left {5}", []), "left {5}");
	globalThis.__ = (text, args) => `tr:${text}:${(args || []).join(",")}`;
	try {
		assert.equal(t("Hi {0}", ["x"]), "tr:Hi {0}:x");
	} finally {
		delete globalThis.__;
	}
});

// ------------------------------------------------------------------ call

console.log("call");

/** A frappe.call that behaves like v16's request.js for one reply: its own handlers (callback on
 * 200; error with the body on 417, with nothing on a 5xx) run first, then the returned jqXHR
 * settles with the data, or rejects with the xhr. */
function frappe_answering(reply) {
	const seen = [];
	return {
		seen,
		call(opts) {
			seen.push(opts);
			return new Promise((resolve, reject) => {
				setImmediate(() => {
					if (reply.status === 200) {
						if (opts.callback) opts.callback(reply.body);
						resolve(reply.body);
						return;
					}
					const xhr = { status: reply.status, responseJSON: reply.body, responseText: reply.text || "" };
					if (!reply.no_callback && opts.error) {
						if (reply.status === 417) opts.error(reply.body);
						else if (reply.status === 0) opts.error(xhr);
						else opts.error();
					}
					reject(xhr);
				});
			});
		},
	};
}

async function outcome(promise) {
	try {
		return { ok: await promise };
	} catch (err) {
		return { err };
	}
}

const call_env = (frappe) => ({ frappe, t });

const THROW_BODY = {
	exc_type: "ValidationError",
	exception: "frappe.exceptions.ValidationError: Pick a date",
	exc: JSON.stringify(["Traceback (most recent call last):\n  ...\nfrappe.exceptions.ValidationError: Pick a date"]),
	_server_messages: JSON.stringify([
		JSON.stringify({ message: "Pick a <b>date</b> first.", title: "Message", indicator: "red" }),
	]),
};

await test("200 resolves r.message", async () => {
	const frappe = frappe_answering({ status: 200, body: { message: { rows: [1, 2] } } });
	const result = await outcome(call(call_env(frappe), "app.api.get_rows", { a: 1 }));
	assert.deepEqual(result, { ok: { rows: [1, 2] } });
	assert.equal(frappe.seen[0].method, "app.api.get_rows");
	assert.deepEqual(frappe.seen[0].args, { a: 1 });
	assert.equal("silent" in frappe.seen[0], false);
});

await test("a frappe.throw (417) rejects with its status and the server's words, HTML out", async () => {
	const frappe = frappe_answering({ status: 417, body: THROW_BODY });
	const result = await outcome(call(call_env(frappe), "app.api.save", {}));
	assert.deepEqual(result.err, { status: 417, message: "Pick a date first." });
});

await test("a 417 with no messages falls back to the exception's own sentence", async () => {
	const body = { exception: "frappe.exceptions.ValidationError: Stage is required", exc: "[]" };
	const result = await outcome(call(call_env(frappe_answering({ status: 417, body })), "m", {}));
	assert.deepEqual(result.err, { status: 417, message: "Stage is required" });
});

await test("a 500 rejects with a generic sentence, never the exception", async () => {
	const body = {
		exception: "pymysql.err.OperationalError: (1054, \"Unknown column 'x' in 'field list'\")",
		exc: JSON.stringify(["Traceback ...\npymysql.err.OperationalError: (1054, ...)"]),
	};
	const result = await outcome(call(call_env(frappe_answering({ status: 500, body })), "m", {}));
	assert.equal(result.err.status, 500);
	assert.equal(result.err.message, "The server could not finish this request. Please try again.");
});

await test("a reply frappe answers with no callback at all (QueryTimeoutError) still settles", async () => {
	const body = { exception: "frappe.exceptions.QueryTimeoutError: (3024, ...)" };
	const frappe = frappe_answering({ status: 500, body, no_callback: true });
	const result = await outcome(call(call_env(frappe), "m", {}));
	assert.equal(result.err.status, 500);
});

await test("a network failure rejects with status 0 and a translated sentence", async () => {
	globalThis.__ = (text) => `[${text}]`;
	try {
		const result = await outcome(call(call_env(frappe_answering({ status: 0, body: undefined })), "m", {}));
		assert.deepEqual(result.err, { status: 0, message: "[Could not reach the server.]" });
	} finally {
		delete globalThis.__;
	}
});

await test("403 with an _error_message uses it", async () => {
	const body = { _error_message: "Not permitted to read <i>Project</i>" };
	const result = await outcome(call(call_env(frappe_answering({ status: 403, body })), "m", {}));
	assert.deepEqual(result.err, { status: 403, message: "Not permitted to read Project" });
	const bare = await outcome(call(call_env(frappe_answering({ status: 403, body: null })), "m", {}));
	assert.deepEqual(bare.err, { status: 403, message: "You do not have permission to do that." });
});

await test("silent (and freeze) are passed through, nothing else is added", async () => {
	const frappe = frappe_answering({ status: 200, body: { message: 1 } });
	await call(call_env(frappe), "m", null, { silent: true, freeze: true, unknown: 1 });
	assert.equal(frappe.seen[0].silent, true);
	assert.equal(frappe.seen[0].freeze, true);
	assert.equal("unknown" in frappe.seen[0], false);
	assert.deepEqual(frappe.seen[0].args, {});
});

await test("a frappe.call that throws, or no frappe at all, rejects with status 0", async () => {
	const throwing = { call: () => { throw new Error("boom"); } };
	assert.equal((await outcome(call(call_env(throwing), "m", {}))).err.status, 0);
	assert.equal((await outcome(call(call_env(undefined), "m", {}))).err.status, 0);
	assert.equal((await outcome(call({ frappe: {}, t }, "m", {}))).err.status, 0);
});

await test("a frappe.call that returns no thenable is settled by its callbacks", async () => {
	const ok = { call: (opts) => opts.callback({ message: "yes" }) };
	assert.deepEqual(await outcome(call(call_env(ok), "m", {})), { ok: "yes" });
	const bad = { call: (opts) => opts.error({ _server_messages: JSON.stringify(["Plain words"]) }) };
	assert.deepEqual((await outcome(call(call_env(bad), "m", {}))).err, { status: 0, message: "Plain words" });
});

await test("a rejected reply is never left unhandled", async () => {
	call(call_env(frappe_answering({ status: 500, body: {} })), "m", {}).catch(() => {});
	await settle();
	assert.deepEqual(unhandled, []);
});

// ------------------------------------------------------------------ state

console.log("state");

await test("each kind draws its panel with the right role", () => {
	const loading = state_html("loading");
	assert.match(loading, /^<div class="sf-state sf-state--loading" role="status" aria-live="polite">/);
	assert.match(loading, /sf-state-spinner" aria-hidden="true"/);
	assert.match(loading, /<p class="sf-state-title">Loading…<\/p>/);
	const error = state_html("error", { body: "Server said no" });
	assert.match(error, /^<div class="sf-state sf-state--error" role="alert">/);
	assert.match(error, /<p class="sf-state-body">Server said no<\/p>/);
	const empty = state_html("empty");
	assert.match(empty, /^<div class="sf-state sf-state--empty">/);
	assert.match(state_html("no-access"), /^<div class="sf-state sf-state--no-access">/);
	assert.match(state_html("whatever"), /sf-state--empty/);
	for (const html of [error, empty, state_html("no-access")]) assert.doesNotMatch(html, /spinner/);
});

await test("the action button is drawn only when there is something to call", () => {
	assert.doesNotMatch(state_html("error", { action_label: "Try again" }), /<button/);
	const html = state_html("error", { on_action: () => {} });
	assert.match(html, /<button type="button" class="sf-btn sf-btn--secondary sf-state-action">Try again<\/button>/);
	assert.match(state_html("empty", { on_action: () => {}, action_label: "New <visit>" }), />New &lt;visit&gt;</);
});

await test("the title and body are escaped", () => {
	const html = state_html("empty", { title: "<img src=x onerror=alert(1)>", body: "a & b" });
	assert.doesNotMatch(html, /<img/);
	assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
	assert.match(html, /a &amp; b/);
});

await test("render writes the panel into the element and wires its one action", () => {
	let clicks = 0;
	const button = make_el("button");
	const host = {
		innerHTML: "",
		querySelector: (sel) => (sel === ".sf-state-action" && host.innerHTML.includes("sf-state-action") ? button : null),
		firstElementChild: { panel: true },
	};
	const panel = render_state(host, "error", { on_action: () => (clicks += 1) });
	assert.deepEqual(panel, { panel: true });
	assert.match(host.innerHTML, /role="alert"/);
	button.fire("click");
	assert.equal(clicks, 1);
	render_state({ jquery: "3", 0: host }, "loading");
	assert.match(host.innerHTML, /sf-state--loading/);
	assert.equal(render_state(null, "loading"), null);
});

// ------------------------------------------------------------------ chip, card, tiles

console.log("chip, card, tiles");

await test("every tone draws its own class and the word", () => {
	for (const tone of ["design", "build", "service", "rent", "neutral", "danger", "warn", "good"]) {
		assert.equal(chip("Word", tone), `<span class="sf-chip sf-chip--${tone}">Word</span>`);
	}
	assert.equal(chip("Word", "teal"), '<span class="sf-chip sf-chip--neutral">Word</span>');
	assert.equal(chip("Word"), '<span class="sf-chip sf-chip--neutral">Word</span>');
});

await test("a hostile label is escaped and a wordless chip is not drawn", () => {
	assert.equal(
		chip('<script>x</script>"', "build"),
		'<span class="sf-chip sf-chip--build">&lt;script&gt;x&lt;/script&gt;&quot;</span>'
	);
	assert.equal(chip("", "danger"), "");
	assert.equal(chip("   ", "danger"), "");
	assert.equal(chip(null, "good"), "");
	assert.equal(chip(0, "good"), '<span class="sf-chip sf-chip--good">0</span>');
});

await test("a card escapes its title, takes body_html as given, and knows its pillars", () => {
	assert.equal(
		card_html({ title: "<b>A</b>", body_html: "<p>ok</p>", pillar: "rent" }),
		'<section class="sf-card sf-card--pillar-rent"><h3 class="sf-card-title">&lt;b&gt;A&lt;/b&gt;</h3><div class="sf-card-body"><p>ok</p></div></section>'
	);
	assert.equal(card_html({ pillar: "bogus\" onclick=\"x" }), '<section class="sf-card"></section>');
});

await test("tiles escape everything and keep only /desk/ and http(s) links", () => {
	const html = tiles_html([
		{
			key: '"><x',
			title: "<Drive>",
			tone: "red",
			value: "<1>",
			detail: "it's <down>",
			metrics: [{ label: "<q>", value: "<v>" }, { label: "Empty" }],
			links: [
				{ label: "Settings", href: "/desk/google-drive-settings" },
				{ label: "Log", route: "/desk/error-log?x=1" },
				{ label: "Docs", href: "https://example.com/a?b=1&c=2" },
				{ label: "Evil", href: "javascript:alert(1)" },
				{ label: "Data", href: "data:text/html,<x>" },
				{ label: "Other host", href: "//evil.example" },
				{ label: "App", href: "/app/x" },
				{ label: "Split", href: "/desk/a b" },
				{ label: "<lbl>", href: "/desk" },
			],
		},
	]);
	assert.doesNotMatch(html, /<x|<Drive>|<1>|<down>|<q>|<v>|<lbl>/);
	assert.match(html, /^<div class="sf-tiles"><section class="sf-card sf-tile" data-key="&quot;&gt;&lt;x">/);
	assert.match(html, /<span class="sf-chip sf-chip--danger">Failing<\/span>/);
	assert.match(html, /<dt>Empty<\/dt><dd>—<\/dd>/);
	assert.match(html, /<a class="sf-tile-link" href="\/desk\/google-drive-settings">Settings<\/a>/);
	assert.match(html, /<a class="sf-tile-link" href="\/desk\/error-log\?x=1">Log<\/a>/);
	assert.match(html, /href="https:\/\/example.com\/a\?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">Docs</);
	assert.match(html, /href="\/desk">&lt;lbl&gt;</);
	for (const label of ["Evil", "Data", "Other host", "App", "Split"]) {
		assert.match(html, new RegExp(`<span class="sf-tile-link sf-tile-link--plain">${label}</span>`));
	}
	assert.doesNotMatch(html, /javascript:|data:text|evil\.example|\/app\/x/);
});

await test("a tile's chip: aliases, default words, and none without a status", () => {
	assert.match(tiles_html([{ title: "A", status: "green" }]), /sf-chip--good">Healthy</);
	assert.match(tiles_html([{ title: "A", status: "amber" }]), /sf-chip--warn">Needs attention</);
	assert.match(tiles_html([{ title: "A", tone: "good", status_label: "OK" }]), /sf-chip--good">OK</);
	assert.doesNotMatch(tiles_html([{ title: "A" }]), /sf-chip/);
	assert.doesNotMatch(tiles_html([{ title: "A", tone: "neutral" }]), /sf-chip/);
	assert.equal(tiles_html([]), '<div class="sf-tiles"></div>');
	assert.equal(safe_href(" /desk/x "), "/desk/x");
	assert.equal(safe_href("/deskx"), "");
	assert.equal(safe_href("HTTPS://x.y"), "HTTPS://x.y");
	assert.equal(safe_href("https:///x"), "");
	assert.equal(safe_href(null), "");
});

// ------------------------------------------------------------------ toast

console.log("toast");

await test("planner_kit's toast draws what it drew before it moved (prefix pk, byte for byte)", () => {
	assert.notEqual(SNAPSHOT, "__PK_SNAPSHOT__", "the snapshot was never recorded");
	assert.deepEqual(toast_markup(planner_toast.create_toast, []), JSON.parse(SNAPSHOT));
	assert.deepEqual(toast_markup(create_toast, [{ prefix: "pk" }]), JSON.parse(SNAPSHOT));
});

await test("sf_kit's own toast is the same markup under the sf- prefix", () => {
	const expected = JSON.parse(SNAPSHOT).map((html) => html.replace(/\bpk-toast/g, "sf-toast"));
	assert.deepEqual(toast_markup(create_toast, []), expected);
	assert.deepEqual(toast_markup(create_toast, [{ prefix: "" }]), expected);
});

await test("one at a time, 8000 ms by default, and the action closes it", () => {
	const env = toast_env();
	const toast = create_toast(env);
	let acted = 0;
	const first = toast("One", { action_label: "Undo", on_action: () => (acted += 1) });
	assert.deepEqual(env.timers.pending(), [8000]);
	assert.equal(toast.current(), first.el);
	const second = toast("Two");
	assert.equal(env.doc.body.children.length, 1);
	assert.equal(env.doc.body.children[0], second.el);
	assert.equal(first.el.parentNode, null);
	assert.deepEqual(env.timers.pending(), [8000]);
	const third = toast("Three", { action_label: "Undo", on_action: () => (acted += 1), timeout: 2500 });
	assert.deepEqual(env.timers.pending(), [2500]);
	third.el.find("sf-toast-action").fire("click");
	assert.equal(acted, 1);
	assert.equal(toast.current(), null);
	assert.equal(env.doc.body.children.length, 0);
	assert.deepEqual(env.timers.pending(), []);
});

await test("the pointer holds it, leaving re-arms it, and the timer closes it", () => {
	const env = toast_env();
	const toast = create_toast(env);
	const shown = toast("Held");
	shown.el.fire("pointerenter");
	assert.deepEqual(env.timers.pending(), []);
	shown.el.fire("pointerleave");
	assert.deepEqual(env.timers.pending(), [8000]);
	env.timers.run();
	assert.equal(toast.current(), null);
	assert.equal(env.doc.body.children.length, 0);
});

await test("the dismiss button has a label and closes; a stale close does not close the next", () => {
	const env = toast_env();
	const toast = create_toast(env);
	const first = toast("First");
	const close = first.el.find("sf-toast-close");
	assert.equal(close.getAttribute("aria-label"), "Dismiss");
	const second = toast("Second");
	first.close();
	close.fire("click");
	assert.equal(toast.current(), second.el);
	toast.close();
	assert.equal(toast.current(), null);
	toast.close();
});

await test("a container (a block's shadow root) receives the toast instead of the body", () => {
	const env = toast_env();
	const root = make_el("shadow-root");
	create_toast(env, { container: root })("In the block");
	assert.equal(root.children.length, 1);
	assert.equal(env.doc.body.children.length, 0);
});

// ------------------------------------------------------------------ styles and the bundle

console.log("styles and the bundle");

await test("styles.js exports exactly the text of sf_kit.bundle.css", () => {
	assert.equal(CSS, fs.readFileSync(CSS_FILE, "utf8"));
});

await test("the bundle installs window.sf_kit once, bound to the page, and injects no stylesheet", async () => {
	const doc = make_doc();
	const answered = frappe_answering({ status: 200, body: { message: "from the bundle" } });
	globalThis.window = { frappe: answered, setTimeout, clearTimeout, console };
	globalThis.document = doc;
	try {
		await import(url(path.join(JS, "sf_kit.bundle.js")));
		const sf = globalThis.window.sf_kit;
		assert.ok(sf, "window.sf_kit was not installed");
		assert.equal(sf.version, 1);
		for (const name of ["esc", "t", "call", "chip", "state", "card", "tiles", "toast", "create_toast", "CSS"]) {
			assert.ok(name in sf, `window.sf_kit.${name} is missing`);
		}
		assert.equal(sf.esc, esc);
		assert.equal(await sf.call("m", {}), "from the bundle");
		assert.match(sf.state.html("loading"), /role="status"/);
		assert.match(sf.tiles.html([{ title: "x" }]), /sf-tiles/);
		sf.toast("Hello");
		assert.equal(doc.body.children[0].className, "sf-toast sf-toast-info");
		sf.toast.close();
		assert.equal(doc.head.children.length, 0, "the bundle injected a stylesheet");

		// A second copy of the bundle (another page's frappe.require) leaves the first in place.
		const first = globalThis.window.sf_kit;
		await import(url(path.join(JS, "sf_kit.bundle.js")) + "?again");
		assert.equal(globalThis.window.sf_kit, first);
	} finally {
		delete globalThis.window;
		delete globalThis.document;
	}
});

await settle();
await test("nothing was left unhandled", () => {
	assert.deepEqual(unhandled, []);
});

console.log(`\n${checks - failures}/${checks} passed`);
process.exit(failures ? 1 : 0);
