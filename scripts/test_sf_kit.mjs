#!/usr/bin/env node
/**
 * sf_kit, the shared page pieces (public/js/sf_kit/), executed under node.
 *
 * Every primitive is a pure ES module that takes what it needs from the page in an `env`, so it
 * runs here against a stand-in document, timers, frappe.call and fetch, and what it draws is read
 * back as markup. What this pins, because each part fails quietly on a real page:
 *
 *   - esc escapes the six characters and turns null/undefined into "", and planner_kit's
 *     `escape` IS that function (one implementation, re-exported);
 *   - call() settles exactly once, the same way, for every reply v16's request.js can produce:
 *     resolves r.message on 200; rejects {status, message, exc_type, session_expired} on a
 *     frappe.throw (417, the server's own words, HTML taken out), a 500 (a generic sentence,
 *     never the exception), a network failure (status 0), a frappe.call that throws, and a reply
 *     frappe answers with no callback at all; nothing is ever left unhandled;
 *   - the stand-in frappe.call MODELS v16's per-status handlers (request.js:131-251 on
 *     version-16: msgprint on 403/404/413/502/504/508, report_error's dialog on a 500 with a
 *     traceback, the QueryTimeout / Deadlock dialogs, the session-expired dialog, and cleanup's
 *     _server_messages msgprint, the only one `silent` stops), so the tests show what `silent`
 *     does NOT suppress, and that the quiet path (fetch) shows none of it;
 *   - the quiet path: POST with a JSON body and the CSRF header by default, GET with a query
 *     string on request, frappe.call's argument encoding, a 417's own words, session expiry
 *     recognised from the 401 body and from the 403 that follows it once the cookies are gone, a
 *     200 that is not JSON read as "not the server", a network failure as status 0;
 *   - strip_html decodes entities, keeps a bare "<" in prose, and puts no space before
 *     punctuation; it uses DOMParser when there is one;
 *   - the state panel's roles, its action button whenever it has a label or a function, and an
 *     escaped title;
 *   - a chip always carries its word, an unknown tone is neutral, a wordless chip is not drawn;
 *   - cards and tiles escape everything, only make links of /desk/ routes and http(s) URLs, and
 *     survive malformed tile data;
 *   - the toast: one per document across every instance and every copy of the module, 8 s by
 *     default, held while the pointer or focus is in it, focus handed back when it closes, its
 *     action closes it; and planner_kit's toast (prefix "pk") draws BYTE FOR BYTE what it drew
 *     before it moved here (SNAPSHOT, taken from the old planner_kit/toast.js with
 *     --print-pk-snapshot);
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
 * also pins the order the code does things in. `owner` is the stand-in document, for focus. */
function make_el(tag, owner) {
	const el = {
		tagName: tag.toUpperCase(),
		attrs: [],
		children: [],
		parentNode: null,
		listeners: {},
		text: null,
		html: null,
		is_root: false,
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
		contains(node) {
			for (let n = node; n; n = n.parentNode) if (n === el) return true;
			return false;
		},
		focus() {
			if (owner) owner.activeElement = el;
		},
		addEventListener(type, fn) {
			(el.listeners[type] = el.listeners[type] || []).push(fn);
		},
		fire(type, extra) {
			for (const fn of el.listeners[type] || []) fn({ type, target: el, preventDefault() {}, ...(extra || {}) });
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
	Object.defineProperty(el, "isConnected", {
		get: () => {
			for (let n = el; n; n = n.parentNode) if (n.is_root) return true;
			return false;
		},
	});
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
	const doc = { activeElement: null, cookie: "" };
	doc.createElement = (tag) => make_el(tag, doc);
	doc.body = make_el("body", doc);
	doc.body.is_root = true;
	doc.head = make_el("head", doc);
	doc.head.is_root = true;
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

function toast_env(doc) {
	doc = doc || make_doc();
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

// planner_kit/toast.js's markup before it became a wrapper around sf_kit (v1.626.0), printed by
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
const { strip_html } = await import(url(path.join(KIT, "call.js")));
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
		assert.equal(kit.__("Hi {0}", ["y"]), "tr:Hi {0}:y");
	} finally {
		delete globalThis.__;
	}
});

await test("the kit's own words go through the page's __ (they are literal __() calls)", () => {
	globalThis.__ = (text) => `[${text}]`;
	try {
		assert.match(state_html("loading"), /\[Loading…\]/);
		assert.match(state_html("error", { on_action: () => {} }), />\[Try again\]</);
		assert.match(tiles_html([{ title: "A", status: "red" }]), />\[Failing\]</);
		const env = toast_env();
		assert.equal(create_toast(env)("x").el.find("sf-toast-close").getAttribute("aria-label"), "[Dismiss]");
	} finally {
		delete globalThis.__;
	}
});

// ------------------------------------------------------------------ strip_html

console.log("strip_html");

await test("tags out, entities decoded, a bare < in prose kept, no space before punctuation", () => {
	assert.equal(typeof globalThis.DOMParser, "undefined", "node has no DOMParser: this is the fallback");
	assert.equal(strip_html("Qty must be < 5 and > 0"), "Qty must be < 5 and > 0");
	assert.equal(strip_html("Row <b>3</b>:"), "Row 3:");
	assert.equal(strip_html("Pick a <b>date</b> first."), "Pick a date first.");
	assert.equal(strip_html("<p>One</p><p>Two</p>"), "One Two");
	assert.equal(strip_html("A<br>B"), "A B");
	assert.equal(strip_html("Tom &amp; Jerry &lt;3 &quot;hi&quot; it&#39;s&nbsp;ok &#x263A;"), 'Tom & Jerry <3 "hi" it\'s ok ☺');
	assert.equal(strip_html("&amp;lt;b&amp;gt;"), "&lt;b&gt;", "decoded once, never twice");
	assert.equal(strip_html("&lt;script&gt;x"), "<script>x", "a decoded < is text, never a tag");
	assert.equal(strip_html("&bogus; stays"), "&bogus; stays");
	assert.equal(strip_html("<!-- note -->Done ( see <i>log</i> )"), "Done (see log)");
	assert.equal(
		strip_html(
			"<details><summary>You are not permitted to access this resource. Login to access</summary>Function <b>x.y</b> is not whitelisted.</details>"
		),
		"You are not permitted to access this resource. Login to access Function x.y is not whitelisted."
	);
	assert.equal(strip_html(null), "");
	assert.equal(strip_html(0), "0");
});

await test("with a DOM, the browser's parser does the decoding", () => {
	const seen = [];
	globalThis.DOMParser = class {
		parseFromString(text, type) {
			seen.push([text, type]);
			return { body: { textContent: "Row 3 : done" } };
		}
	};
	try {
		assert.equal(strip_html("Row <b>3</b> :&nbsp;done"), "Row 3: done");
		assert.equal(seen[0][1], "text/html");
		globalThis.DOMParser = class {
			parseFromString() {
				throw new Error("no");
			}
		};
		assert.equal(strip_html("a &amp; <b>b</b>"), "a & b", "a parser that throws falls back");
	} finally {
		delete globalThis.DOMParser;
	}
});

// ------------------------------------------------------------------ call: the frappe.call path

console.log("call (frappe.call)");

/** A frappe.call that behaves like v16's request.js for one reply. Its own handlers run first,
 * as request.js's do (frappe.request.call's statusCode table and exception_handlers, then
 * cleanup), and every dialog they would open is written to `shown`; then the returned jqXHR
 * settles with the data, or rejects with the xhr. */
function frappe_v16(reply, extra) {
	const shown = [];
	const seen = [];
	const frappe = {
		seen,
		shown,
		csrf_token: "tok-123",
		session: (extra && extra.session) || { user: "nik@example.com", logged_in_user: "nik@example.com" },
		msgprint: (m) => shown.push(`msgprint:${typeof m === "string" ? m : JSON.stringify(m)}`),
		call(opts) {
			seen.push(opts);
			return new Promise((resolve, reject) => {
				setImmediate(() => {
					const body = reply.body;
					const status = reply.status;
					const text = reply.text !== undefined ? reply.text : body ? JSON.stringify(body) : "";
					const xhr = { status, responseJSON: body, responseText: text };
					const error = (...a) => opts.error && opts.error(...a);
					const expired = (r) => Boolean(r && r.session_expired);
					// cleanup (request.js:444), run from .always: the one place `silent` is read.
					const cleanup = (r) => {
						if (!r) return;
						if (expired(r)) {
							shown.push("session-expired dialog");
							return;
						}
						if (r._server_messages && !opts.silent) shown.push("msgprint:_server_messages");
					};
					if (status === 200) {
						if (opts.callback) opts.callback(body);
						cleanup(body);
						resolve(body);
						return;
					}
					cleanup(body);
					const exception = body && body.exception ? body.exception.split(".").at(-1).split(":")[0] : "";
					if (exception === "QueryTimeoutError" || exception === "QueryDeadlockError") {
						shown.push(`dialog:${exception}`);
					} else if (status === 401) {
						if (!expired(body)) error();
					} else if (status === 403) {
						if (!expired(body)) {
							if (body && body._error_message) shown.push("msgprint:Not permitted");
							else if (!(body && body._server_messages)) shown.push("msgprint:Not permitted");
							error();
						}
					} else if (status === 404) {
						shown.push("msgprint:Not found");
						error();
					} else if (status === 413) {
						shown.push("msgprint:File too big");
						error();
					} else if (status === 417) {
						error(body);
					} else if (status === 500) {
						error();
						if (body && body.exc) shown.push("dialog:Server Error (report_error)");
					} else if (status === 502) {
						shown.push("msgprint:Internal Server Error");
						error();
					} else if (status === 504) {
						shown.push("msgprint:Request Timed Out");
						error();
					} else if (status === 508) {
						shown.push("msgprint:Please try again");
						error();
					} else {
						error(xhr);
					}
					reject(xhr);
				});
			});
		},
	};
	return frappe;
}

async function outcome(promise) {
	try {
		return { ok: await promise };
	} catch (err) {
		return { err };
	}
}

const call_env = (frappe, more) => ({ frappe, t, ...(more || {}) });
const refusal = (status, message, exc_type = "", session_expired = false) => ({ status, message, exc_type, session_expired });

const THROW_BODY = {
	exc_type: "ValidationError",
	exception: "frappe.exceptions.ValidationError: Pick a date",
	exc: JSON.stringify(["Traceback (most recent call last):\n  ...\nfrappe.exceptions.ValidationError: Pick a date"]),
	_server_messages: JSON.stringify([
		JSON.stringify({ message: "Pick a <b>date</b> first.", title: "Message", indicator: "red" }),
	]),
};
const SERVER_ERROR_BODY = {
	exc_type: "OperationalError",
	exception: "pymysql.err.OperationalError: (1054, \"Unknown column 'x' in 'field list'\")",
	exc: JSON.stringify(["Traceback ...\npymysql.err.OperationalError: (1054, ...)"]),
};
const EXPIRED_BODY = { exc_type: "SessionExpired", session_expired: 1 };
const GUEST_REFUSAL_BODY = {
	exc_type: "PermissionError",
	_server_messages: JSON.stringify([
		JSON.stringify({
			message:
				"<details><summary>You are not permitted to access this resource. Login to access</summary>Function <strong>app.api.save</strong> is not whitelisted.</details>",
			title: "Method Not Allowed",
		}),
	]),
};

await test("200 resolves r.message", async () => {
	const frappe = frappe_v16({ status: 200, body: { message: { rows: [1, 2] } } });
	const result = await outcome(call(call_env(frappe), "app.api.get_rows", { a: 1 }));
	assert.deepEqual(result, { ok: { rows: [1, 2] } });
	assert.equal(frappe.seen[0].method, "app.api.get_rows");
	assert.deepEqual(frappe.seen[0].args, { a: 1 });
	assert.equal("silent" in frappe.seen[0], false);
	assert.equal("quiet" in frappe.seen[0], false, "sf_kit's quiet is never frappe.call's quiet");
});

await test("a frappe.throw (417) rejects with its status, the server's words and its exc_type", async () => {
	const frappe = frappe_v16({ status: 417, body: THROW_BODY });
	const result = await outcome(call(call_env(frappe), "app.api.save", {}));
	assert.deepEqual(result.err, refusal(417, "Pick a date first.", "ValidationError"));
});

await test("a 417 with no messages falls back to the exception's own sentence", async () => {
	const body = { exception: "frappe.exceptions.ValidationError: Stage is required", exc: "[]" };
	const result = await outcome(call(call_env(frappe_v16({ status: 417, body })), "m", {}));
	assert.deepEqual(result.err, refusal(417, "Stage is required"));
});

await test("a 500 rejects with a generic sentence, never the exception", async () => {
	const result = await outcome(call(call_env(frappe_v16({ status: 500, body: SERVER_ERROR_BODY })), "m", {}));
	assert.deepEqual(
		result.err,
		refusal(500, "The server could not finish this request. Please try again.", "OperationalError")
	);
});

await test("a reply frappe answers with no callback at all (QueryTimeoutError) still settles", async () => {
	const body = { exc_type: "QueryTimeoutError", exception: "frappe.exceptions.QueryTimeoutError: (3024, 'Query execution was interrupted')" };
	const frappe = frappe_v16({ status: 500, body });
	const result = await outcome(call(call_env(frappe), "m", {}));
	assert.equal(result.err.status, 500);
	assert.deepEqual(frappe.shown, ["dialog:QueryTimeoutError"]);
});

await test("a network failure rejects with status 0 and a translated sentence", async () => {
	globalThis.__ = (text) => `[${text}]`;
	try {
		const result = await outcome(call(call_env(frappe_v16({ status: 0, body: undefined })), "m", {}));
		assert.deepEqual(result.err, refusal(0, "[Could not reach the server.]"));
	} finally {
		delete globalThis.__;
	}
});

await test("403 with an _error_message uses it", async () => {
	const body = { exc_type: "PermissionError", _error_message: "Not permitted to read <i>Project</i>" };
	const result = await outcome(call(call_env(frappe_v16({ status: 403, body })), "m", {}));
	assert.deepEqual(result.err, refusal(403, "Not permitted to read Project", "PermissionError"));
	const bare = await outcome(call(call_env(frappe_v16({ status: 403, body: null })), "m", {}));
	assert.deepEqual(bare.err, refusal(403, "You do not have permission to do that."));
});

await test("a 401 SessionExpired is reported as session expiry on this path too", async () => {
	const frappe = frappe_v16({ status: 401, body: EXPIRED_BODY });
	const result = await outcome(call(call_env(frappe), "m", {}));
	assert.deepEqual(result.err, refusal(401, "Your session has expired. Please sign in again.", "SessionExpired", true));
});

await test("silent (and freeze) are passed through, nothing else is added", async () => {
	const frappe = frappe_v16({ status: 200, body: { message: 1 } });
	await call(call_env(frappe), "m", null, { silent: true, freeze: true, unknown: 1, method: "GET" });
	assert.equal(frappe.seen[0].silent, true);
	assert.equal(frappe.seen[0].freeze, true);
	assert.equal("unknown" in frappe.seen[0], false);
	assert.equal(frappe.seen[0].type, undefined, "method: GET belongs to the quiet path only");
	assert.deepEqual(frappe.seen[0].args, {});
});

await test("what silent stops (the _server_messages msgprint) and what it does not", async () => {
	// Stops: cleanup's msgprint of a frappe.throw's messages.
	const thrown = frappe_v16({ status: 417, body: THROW_BODY });
	await outcome(call(call_env(thrown), "m", {}, { silent: true }));
	assert.deepEqual(thrown.shown, []);
	const loud = frappe_v16({ status: 417, body: THROW_BODY });
	await outcome(call(call_env(loud), "m", {}));
	assert.deepEqual(loud.shown, ["msgprint:_server_messages"]);
	// Does not stop: every per-status handler and exception handler in frappe.request.call.
	const cases = [
		[{ status: 403, body: null }, "msgprint:Not permitted"],
		[{ status: 404, body: { exc_type: "DoesNotExistError" } }, "msgprint:Not found"],
		[{ status: 413, body: null }, "msgprint:File too big"],
		[{ status: 500, body: SERVER_ERROR_BODY }, "dialog:Server Error (report_error)"],
		[{ status: 502, body: null }, "msgprint:Internal Server Error"],
		[{ status: 504, body: null }, "msgprint:Request Timed Out"],
		[{ status: 508, body: null }, "msgprint:Please try again"],
		[{ status: 500, body: { exception: "frappe.exceptions.QueryDeadlockError: (1213, 'Deadlock found')" } }, "dialog:QueryDeadlockError"],
		[{ status: 401, body: EXPIRED_BODY }, "session-expired dialog"],
	];
	for (const [reply, dialog] of cases) {
		const frappe = frappe_v16(reply);
		const result = await outcome(call(call_env(frappe), "m", {}, { silent: true }));
		assert.equal(result.err.status, reply.status);
		assert.ok(frappe.shown.includes(dialog), `${reply.status}: expected ${dialog}, saw ${JSON.stringify(frappe.shown)}`);
	}
});

await test("a frappe.call that throws, or no frappe at all, rejects with status 0", async () => {
	const throwing = { call: () => { throw new Error("boom"); } };
	assert.equal((await outcome(call(call_env(throwing), "m", {}))).err.status, 0);
	assert.equal((await outcome(call(call_env(undefined), "m", {}))).err.status, 0);
	assert.equal((await outcome(call({ frappe: {}, t }, "m", {}))).err.status, 0);
	assert.equal((await outcome(call(undefined, "m", {}))).err.status, 0);
});

await test("a frappe.call that returns no thenable is settled by its callbacks", async () => {
	const ok = { call: (opts) => opts.callback({ message: "yes" }) };
	assert.deepEqual(await outcome(call(call_env(ok), "m", {})), { ok: "yes" });
	const bad = { call: (opts) => opts.error({ _server_messages: JSON.stringify(["Plain words"]) }) };
	assert.deepEqual((await outcome(call(call_env(bad), "m", {}))).err, refusal(0, "Plain words"));
});

await test("a rejected reply is never left unhandled", async () => {
	call(call_env(frappe_v16({ status: 500, body: {} })), "m", {}).catch(() => {});
	await settle();
	assert.deepEqual(unhandled, []);
});

// ------------------------------------------------------------------ call: the quiet path

console.log("call (quiet: fetch)");

/** A fetch that answers one reply. `reply.text` is the raw body; `reply.body` is JSON-encoded;
 * `network` rejects (the browser's TypeError); `throws` throws synchronously; `bad_text` makes
 * reading the body fail. */
function fake_fetch(reply) {
	const seen = [];
	const fetch = (address, init) => {
		seen.push({ url: address, init });
		if (reply.throws) throw new Error("fetch blew up");
		return new Promise((resolve, reject) => {
			setImmediate(() => {
				if (reply.network) {
					reject(new TypeError("Failed to fetch"));
					return;
				}
				const text = reply.text !== undefined ? reply.text : JSON.stringify(reply.body);
				resolve({
					status: reply.status,
					text: () => (reply.bad_text ? Promise.reject(new Error("body stream")) : Promise.resolve(text)),
				});
			});
		});
	};
	return { fetch, seen };
}

/** The quiet path's env: a frappe whose call and msgprint must never be touched, a fetch, and
 * the page's cookies. */
function quiet_env(reply, more) {
	const frappe = frappe_v16({ status: 200, body: { message: "frappe.call was used" } }, more);
	const net = fake_fetch(reply);
	const env = { frappe, t, fetch: net.fetch, cookie: () => (more && more.cookie) || "user_id=nik%40example.com; sid=abc" };
	return { env, frappe, seen: net.seen };
}

const quietly = (env, args, opts) => call(env, "app.api.save", args, { quiet: true, ...(opts || {}) });

await test("POST by default: JSON body, CSRF header, same-origin, nothing from frappe.call", async () => {
	const { env, frappe, seen } = quiet_env({ status: 200, body: { message: { saved: true } } });
	const args = { name: "OPP-1", doc: { a: [1, 2] }, list: ["x"], n: 3, flag: false, none: null, gone: undefined };
	const result = await outcome(quietly(env, args));
	assert.deepEqual(result, { ok: { saved: true } });
	assert.equal(frappe.seen.length, 0, "frappe.call was used");
	assert.deepEqual(frappe.shown, []);
	assert.equal(seen.length, 1);
	const { url: address, init } = seen[0];
	assert.equal(address, "/api/method/app.api.save");
	assert.equal(init.method, "POST");
	assert.equal(init.credentials, "same-origin");
	assert.equal(init.cache, "no-store");
	assert.equal(init.headers["Content-Type"], "application/json");
	assert.equal(init.headers["X-Frappe-CSRF-Token"], "tok-123");
	assert.equal(init.headers.Accept, "application/json");
	assert.equal(init.headers["X-Requested-With"], "XMLHttpRequest");
	// The arguments frappe.call would send: objects and arrays JSON-encoded, undefined left out.
	assert.deepEqual(JSON.parse(init.body), {
		name: "OPP-1",
		doc: '{"a":[1,2]}',
		list: '["x"]',
		n: 3,
		flag: false,
		none: null,
	});
});

await test("method: GET sends the args as a query string and no body", async () => {
	const { env, seen } = quiet_env({ status: 200, body: { message: [1] } });
	const result = await outcome(quietly(env, { flow: "opportunity", filters: { a: "b c" }, empty: null }, { method: "get" }));
	assert.deepEqual(result, { ok: [1] });
	const { url: address, init } = seen[0];
	assert.equal(init.method, "GET");
	assert.equal(init.body, undefined);
	assert.equal(init.headers["Content-Type"], undefined);
	assert.equal(init.headers["X-Frappe-CSRF-Token"], "tok-123");
	assert.equal(address, "/api/method/app.api.save?flow=opportunity&filters=%7B%22a%22%3A%22b%20c%22%7D&empty=");
	const bare = quiet_env({ status: 200, body: {} });
	await quietly(bare.env, {}, { method: "GET" });
	assert.equal(bare.seen[0].url, "/api/method/app.api.save");
});

await test("a 417 keeps the frappe.throw's words, HTML out, and its exc_type", async () => {
	const { env, frappe } = quiet_env({ status: 417, body: THROW_BODY });
	const result = await outcome(quietly(env, {}));
	assert.deepEqual(result.err, refusal(417, "Pick a date first.", "ValidationError"));
	assert.deepEqual(frappe.shown, []);
});

await test("session expiry, first reply: 401 SessionExpired with session_expired: 1", async () => {
	const { env } = quiet_env({ status: 401, body: EXPIRED_BODY });
	const result = await outcome(quietly(env, {}));
	assert.deepEqual(result.err, refusal(401, "Your session has expired. Please sign in again.", "SessionExpired", true));
});

await test("session expiry, every later reply: a Guest's 403 once the user_id cookie is gone", async () => {
	// The 401 above deleted the sid and user_id cookies, so the next call is a plain Guest.
	const gone = quiet_env({ status: 403, body: GUEST_REFUSAL_BODY }, { cookie: "full_name=; system_user=yes" });
	const expired = await outcome(quietly(gone.env, {}));
	assert.deepEqual(expired.err, refusal(403, "Your session has expired. Please sign in again.", "SessionExpired", true));
	const guest = quiet_env({ status: 403, body: GUEST_REFUSAL_BODY }, { cookie: "user_id=Guest" });
	assert.equal((await outcome(quietly(guest.env, {}))).err.session_expired, true);
	// Still signed in: an ordinary refusal, in the server's own words.
	const refused = quiet_env({ status: 403, body: GUEST_REFUSAL_BODY });
	assert.deepEqual(
		(await outcome(quietly(refused.env, {}))).err,
		refusal(
			403,
			"You are not permitted to access this resource. Login to access Function app.api.save is not whitelisted.",
			"PermissionError"
		)
	);
	// A page that was never signed in (a www page, a Guest) is never "expired".
	const visitor = quiet_env({ status: 403, body: GUEST_REFUSAL_BODY }, { cookie: "", session: { user: "Guest" } });
	assert.equal((await outcome(quietly(visitor.env, {}))).err.session_expired, false);
	// A 417 is never expiry, whatever the cookies say.
	const thrown = quiet_env({ status: 417, body: THROW_BODY }, { cookie: "" });
	assert.equal((await outcome(quietly(thrown.env, {}))).err.session_expired, false);
});

await test("a 200 that is not frappe's JSON (proxy page, login page) is 'could not reach'", async () => {
	for (const text of ["<!doctype html><title>Login</title>", "", "[1,2]", "null"]) {
		const { env } = quiet_env({ status: 200, text });
		const result = await outcome(quietly(env, {}));
		assert.deepEqual(result.err, refusal(0, "Could not reach the server."), JSON.stringify(text));
	}
});

await test("a 5xx says the generic sentence; a 502 page that is not JSON keeps its status", async () => {
	const { env, frappe } = quiet_env({ status: 500, body: SERVER_ERROR_BODY });
	assert.deepEqual(
		(await outcome(quietly(env, {}))).err,
		refusal(500, "The server could not finish this request. Please try again.", "OperationalError")
	);
	assert.deepEqual(frappe.shown, []);
	const proxy = quiet_env({ status: 502, text: "<html>Bad Gateway</html>" });
	assert.deepEqual(
		(await outcome(quietly(proxy.env, {}))).err,
		refusal(502, "The server could not finish this request. Please try again.")
	);
});

await test("network failure, a fetch that throws, an unreadable body, no fetch: status 0", async () => {
	for (const reply of [{ network: true }, { throws: true }, { status: 200, bad_text: true }]) {
		const { env } = quiet_env(reply);
		assert.deepEqual((await outcome(quietly(env, {}))).err, refusal(0, "Could not reach the server."), JSON.stringify(reply));
	}
	assert.equal((await outcome(call({ t }, "m", {}, { quiet: true }))).err.status, 0);
	const { env } = quiet_env({ status: 200, body: {} });
	assert.equal((await outcome(call(env, "", {}, { quiet: true }))).err.status, 0);
});

await test("the quiet path shows none of the dialogs frappe.call would", async () => {
	const replies = [
		{ status: 403, body: null },
		{ status: 404, body: { exc_type: "DoesNotExistError" } },
		{ status: 413, body: null },
		{ status: 417, body: THROW_BODY },
		{ status: 500, body: SERVER_ERROR_BODY },
		{ status: 500, body: { exception: "frappe.exceptions.QueryTimeoutError: (3024, 'Query execution was interrupted')" } },
		{ status: 502, body: null },
		{ status: 504, body: null },
		{ status: 508, body: null },
		{ status: 401, body: EXPIRED_BODY },
		{ status: 200, body: { message: 1, _server_messages: THROW_BODY._server_messages } },
	];
	for (const reply of replies) {
		const { env, frappe } = quiet_env(reply);
		await outcome(quietly(env, {}));
		assert.equal(frappe.seen.length, 0, `${reply.status}: frappe.call was used`);
		assert.deepEqual(frappe.shown, [], `${reply.status}: a dialog was shown`);
	}
});

await test("it settles exactly once and leaves nothing unhandled", async () => {
	let settled = 0;
	const { env } = quiet_env({ status: 500, body: {} });
	await quietly(env, {}).then(
		() => (settled += 1),
		() => (settled += 1)
	);
	await settle();
	assert.equal(settled, 1);
	quietly(quiet_env({ network: true }).env, {}).catch(() => {});
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

await test("the action button is drawn for a label or a function, and only then", () => {
	assert.doesNotMatch(state_html("error"), /<button/);
	assert.doesNotMatch(state_html("error", { action_label: "" }), /<button/);
	const button = '<button type="button" class="sf-btn sf-btn--secondary sf-state-action">';
	// A label alone: the caller delegates clicks on .sf-state-action.
	assert.ok(state_html("error", { action_label: "Reload" }).includes(`${button}Reload</button>`));
	assert.ok(state_html("error", { on_action: () => {} }).includes(`${button}Try again</button>`));
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
	// A label without a function draws the button and wires nothing.
	const quiet_button = make_el("button");
	const other = { innerHTML: "", querySelector: () => quiet_button, firstElementChild: null };
	render_state(other, "error", { action_label: "Reload" });
	assert.match(other.innerHTML, /sf-state-action">Reload</);
	assert.deepEqual(quiet_button.listeners, {});
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

await test("a blank href falls back to the route; an unsafe href does not", () => {
	const html = tiles_html([
		{
			title: "A",
			links: [
				{ label: "Null", href: null, route: "/desk/one" },
				{ label: "Empty", href: "  ", route: "/desk/two" },
				{ label: "Bad", href: "javascript:x", route: "/desk/three" },
			],
		},
	]);
	assert.match(html, /<a class="sf-tile-link" href="\/desk\/one">Null<\/a>/);
	assert.match(html, /<a class="sf-tile-link" href="\/desk\/two">Empty<\/a>/);
	assert.match(html, /sf-tile-link--plain">Bad</);
	assert.doesNotMatch(html, /\/desk\/three/);
});

await test("malformed tile data costs that part, never the grid", () => {
	const html = tiles_html([
		{ title: "Metrics not a list", metrics: { label: "x" }, links: "/desk/x" },
		null,
		"a string",
		{ title: "Bad rows", metrics: [null, 3, { label: "Ok", value: 1 }], links: [null, 7, { label: "L", href: "/desk/l" }] },
		{ title: "Fine", tone: "good" },
	]);
	assert.equal((html.match(/<section class="sf-card sf-tile"/g) || []).length, 5);
	assert.match(html, /<dt>Ok<\/dt><dd>1<\/dd>/);
	assert.match(html, /href="\/desk\/l">L</);
	assert.match(html, /sf-chip--good">Healthy</);
	assert.doesNotMatch(html, /href="\/desk\/x"/);
	assert.equal(tiles_html({ not: "a list" }), '<div class="sf-tiles"></div>');
	assert.equal(tiles_html(null), '<div class="sf-tiles"></div>');
});

await test("a tile with no title draws no empty heading", () => {
	assert.doesNotMatch(tiles_html([{ status: "red" }]), /<h3/);
	assert.match(tiles_html([{ status: "red" }]), /<div class="sf-tile-head"><span class="sf-chip sf-chip--danger">Failing<\/span><\/div>/);
	assert.equal(tiles_html([{ value: 3 }]), '<div class="sf-tiles"><section class="sf-card sf-tile"><div class="sf-tile-value">3</div></section></div>');
	assert.doesNotMatch(tiles_html([{ title: "  " }]), /<h3|sf-tile-head/);
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

await test("one per document across instances, prefixes and copies of the module", async () => {
	const env = toast_env();
	const root = make_el("shadow-root", env.doc);
	root.is_root = true;
	const sf = create_toast(env);
	const pk = planner_toast.create_toast(env);
	const block = create_toast(env, { container: root });
	// A second copy of toast.js, as the planner_kit and sf_kit bundles each carry one.
	const copy = await import(url(path.join(KIT, "toast.js")) + "?another-bundle");
	assert.notEqual(copy.create_toast, create_toast);
	const other = copy.create_toast(env);

	const a = sf("From the page");
	const b = pk("From the planner");
	assert.equal(a.el.parentNode, null, "the pk toast did not replace the sf one");
	assert.equal(sf.current(), null);
	assert.equal(pk.current(), b.el);
	const c = block("From the block");
	assert.equal(b.el.parentNode, null);
	assert.equal(root.children.length, 1);
	const d = other("From the other bundle");
	assert.equal(c.el.parentNode, null);
	assert.equal(other.current(), d.el);
	assert.deepEqual(env.timers.pending(), [8000], "a replaced toast's timer was left running");
	// Each instance closes only its own.
	sf.close();
	pk.close();
	assert.equal(other.current(), d.el);
	other.close();
	assert.equal(env.doc.body.children.length, 0);
	// A different document is a different registry.
	const elsewhere = toast_env();
	create_toast(elsewhere)("Elsewhere");
	sf("Here");
	assert.equal(elsewhere.doc.body.children.length, 1);
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

await test("focus inside holds it; leaving re-arms it only when the pointer is gone too", () => {
	const env = toast_env();
	const toast = create_toast(env);
	const shown = toast("Saved", { action_label: "Undo", on_action: () => {} });
	const action = shown.el.find("sf-toast-action");
	const dismiss = shown.el.find("sf-toast-close");
	shown.el.fire("focusin", { relatedTarget: null });
	assert.deepEqual(env.timers.pending(), []);
	// Tab from Undo to Dismiss: focus stays inside, still held.
	shown.el.fire("focusout", { relatedTarget: dismiss });
	assert.deepEqual(env.timers.pending(), []);
	shown.el.fire("pointerenter");
	shown.el.fire("focusout", { relatedTarget: null });
	assert.deepEqual(env.timers.pending(), [], "re-armed while the pointer is still over it");
	shown.el.fire("pointerleave");
	assert.deepEqual(env.timers.pending(), [8000]);
	shown.el.fire("focusin", { relatedTarget: null });
	shown.el.fire("pointerenter");
	shown.el.fire("pointerleave");
	assert.deepEqual(env.timers.pending(), [], "re-armed while focus is still inside");
	assert.ok(action);
});

await test("closing a toast that holds focus hands focus back to where it was", () => {
	const env = toast_env();
	const toast = create_toast(env);
	const before = env.doc.createElement("button");
	env.doc.body.appendChild(before);
	before.focus();
	// Dismiss with focus inside.
	let shown = toast("Saved", { action_label: "Undo", on_action: () => {} });
	let action = shown.el.find("sf-toast-action");
	action.focus();
	shown.el.fire("focusin", { relatedTarget: before });
	shown.el.find("sf-toast-close").fire("click");
	assert.equal(env.doc.activeElement, before, "Dismiss");
	// The action, and the timer.
	shown = toast("Saved", { action_label: "Undo", on_action: () => {} });
	action = shown.el.find("sf-toast-action");
	action.focus();
	shown.el.fire("focusin", { relatedTarget: before });
	action.fire("click");
	assert.equal(env.doc.activeElement, before, "the action");
	// The timer cannot fire while focus is inside (focusin paused it), so: focus in, out, back in
	// with no relatedTarget (a click on the toast from nowhere), then a close by the page.
	shown = toast("Saved", { action_label: "Undo", on_action: () => {} });
	shown.el.find("sf-toast-close").focus();
	shown.el.fire("focusin", { relatedTarget: before });
	shown.el.fire("focusout", { relatedTarget: null });
	shown.el.find("sf-toast-close").focus();
	shown.el.fire("focusin", { relatedTarget: null });
	assert.deepEqual(env.timers.pending(), []);
	shown.close();
	assert.equal(env.doc.activeElement, before, "close(), with no relatedTarget the second time");
	// Replaced by a newer toast while focus is inside.
	shown = toast("Old", { action_label: "Undo", on_action: () => {} });
	shown.el.find("sf-toast-action").focus();
	shown.el.fire("focusin", { relatedTarget: before });
	toast("New");
	assert.equal(env.doc.activeElement, before, "replaced");
	toast.close();
});

await test("focus is left alone when the toast never had it, or the old element is gone", () => {
	const env = toast_env();
	const toast = create_toast(env);
	const elsewhere = env.doc.createElement("input");
	env.doc.body.appendChild(elsewhere);
	const before = env.doc.createElement("button");
	env.doc.body.appendChild(before);
	before.focus();
	const plain = toast("Saved");
	elsewhere.focus();
	plain.close();
	assert.equal(env.doc.activeElement, elsewhere, "focus moved although the toast never had it");
	before.focus();
	const shown = toast("Saved", { action_label: "Undo", on_action: () => {} });
	const action = shown.el.find("sf-toast-action");
	action.focus();
	shown.el.fire("focusin", { relatedTarget: before });
	env.doc.body.removeChild(before);
	action.fire("click");
	assert.equal(env.doc.activeElement, action, "focus went to an element no longer in the page");
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
	first.el.fire("focusout", { relatedTarget: null });
	assert.equal(toast.current(), second.el);
	assert.deepEqual(env.timers.pending(), [8000], "a closed toast re-armed a timer");
	toast.close();
	assert.equal(toast.current(), null);
	toast.close();
});

await test("a container (a block's shadow root) receives the toast instead of the body", () => {
	const env = toast_env();
	const root = make_el("shadow-root", env.doc);
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
	doc.cookie = "user_id=nik%40example.com";
	const answered = frappe_v16({ status: 200, body: { message: "from the bundle" } });
	const net = fake_fetch({ status: 200, body: { message: "quietly" } });
	globalThis.window = { frappe: answered, fetch: net.fetch, setTimeout, clearTimeout, console };
	globalThis.document = doc;
	try {
		await import(url(path.join(JS, "sf_kit.bundle.js")));
		const sf = globalThis.window.sf_kit;
		assert.ok(sf, "window.sf_kit was not installed");
		assert.equal(sf.version, 2);
		for (const name of ["esc", "t", "call", "chip", "state", "card", "tiles", "toast", "create_toast", "CSS"]) {
			assert.ok(name in sf, `window.sf_kit.${name} is missing`);
		}
		assert.equal(sf.esc, esc);
		assert.equal(await sf.call("m", {}), "from the bundle");
		assert.equal(await sf.call("m", { a: 1 }, { quiet: true }), "quietly");
		assert.equal(net.seen[0].url, "/api/method/m");
		assert.equal(net.seen[0].init.headers["X-Frappe-CSRF-Token"], "tok-123");
		assert.equal(answered.seen.length, 1, "the quiet call went through frappe.call");
		assert.match(sf.state.html("loading"), /role="status"/);
		assert.match(sf.tiles.html([{ title: "x" }]), /sf-tiles/);
		sf.toast("Hello");
		assert.equal(doc.body.children[0].className, "sf-toast sf-toast-info");
		sf.create_toast()("Replaces it");
		assert.equal(doc.body.children.length, 1);
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
