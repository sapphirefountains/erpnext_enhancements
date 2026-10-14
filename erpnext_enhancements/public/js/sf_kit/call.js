/*
 * sf_kit: a server call as a promise that always settles, exactly once, and one way.
 *
 *   sf_kit.call("erpnext_enhancements.api.x.get_y", { name }, { quiet: true, method: "GET" })
 *     .then((message) => ...)                 // r.message
 *     .catch((err) => ...);                   // { status, message, exc_type, session_expired }
 *
 *   { quiet: true, with_messages: true }     resolves { message, server_messages: [...] } instead
 *
 * The rejection, on both paths:
 *   status           the HTTP status; 0 = the server was never reached, or what answered was not
 *                    the server (a 200 whose body is not JSON: a proxy page, a login page)
 *   message          for a person: the server's own words for a frappe.throw (it arrives as
 *                    _server_messages), HTML taken out; otherwise a plain translated sentence. A
 *                    traceback is never shown, and for a 5xx neither is the exception text (it
 *                    names tables and columns, not anything the reader can act on)
 *   exc_type         the server's exception class ("ValidationError", "PermissionError" ...), or ""
 *   session_expired  true when the reply means the person is signed out; exc_type is then
 *                    "SessionExpired" too, whatever the server named it (see below)
 *
 * TWO PATHS, and which dialogs each one shows (v1.630.0).
 *
 * Default: frappe.call. v16's request.js shows its own dialogs for most failures, and `silent`
 * (passed through, with freeze / freeze_message) stops only ONE of them: the _server_messages
 * msgprint in frappe.request.cleanup (`if (messages && !opts.silent)`, request.js:477 on
 * version-16), i.e. a frappe.throw / msgprint carried by a 417 or a 200. It does NOT stop the
 * per-status handlers in frappe.request.call (request.js:131-230): the 403 "Not permitted"
 * msgprint, 404 "Not found", 413 "File too big", 502 "Internal Server Error", 504 "Request Timed
 * Out", 508 "Please try again", the 500 report_error "Server Error" dialog (request.js:551; it
 * carries the traceback for a system user when allow_error_traceback is on), nor the
 * QueryTimeoutError / QueryDeadlockError dialogs (request.js:232-251), the session-expired
 * dialog (401/403, handle_session_expiry) or the offline "Connection Lost" toast. Use it where
 * frappe's dialogs are acceptable and the page wants frappe.call's side effects (doc sync into
 * locals, translated-message sync, `freeze`).
 *
 * `quiet: true`: no frappe.call at all. A fetch to /api/method/<method>, so frappe shows nothing,
 * ever; the page says it once, in its own panel. It is a separate option rather than what
 * `silent` means because the two differ in more than the dialogs: quiet skips frappe.call's
 * model sync and `freeze`, and the page must handle session expiry itself (frappe's sign-in
 * dialog is not raised). The request carries exactly what frappe.call sends:
 *   - POST by default, form-encoded (application/x-www-form-urlencoded; charset=UTF-8, jQuery's
 *     default), or `method: "GET"` with the same pairs as a query string, for reads. ONE encoder
 *     for both (`form_pairs`), and it is frappe.call's: frappe.request.prepare (request.js:388 on
 *     version-16) first JSON-stringifies every truthy plain object or array, then jQuery 3.7.1's
 *     $.param (v16 pins jquery 3.7.1) writes each key as encodeURIComponent(key) "="
 *     encodeURIComponent(value == null ? "" : value), joined with "&". So 3 goes as "3", true as
 *     "true", null and undefined as "" (the key is still sent), {a: 1} as '{"a":1}'. A JSON body
 *     would NOT be the same request (v1.630.0 review): the server would see 3, False and None,
 *     v16's typing_validations reject an int or None for a `str` parameter with a 417, an
 *     undefined arg vanishes, and an untyped `== "true"` check changes its answer.
 *   - A GET to a method whitelisted methods=["POST"] is refused 403 PermissionError, "Not
 *     permitted" (handler.py is_valid_http_method -> frappe.throw_permission_error). It reads like
 *     a permission problem and is a wiring one, so a quiet GET that gets one while signed in
 *     writes a console.warn naming the method, once per method.
 *   - X-Frappe-CSRF-Token from frappe.csrf_token on a POST only (auth.py validate_csrf_token
 *     checks it on the unsafe methods; a GET has no use for it and should not carry it),
 *     Accept: application/json (so app.py handle_exception answers JSON, not an error page),
 *     X-Requested-With, credentials same-origin, cache no-store, and redirect: "error": a 3xx is
 *     never followed (it would re-send the token to wherever it points) and reads as status 0.
 *   - On a 2xx it resolves r.message and drops the rest of the reply: no doc sync into locals,
 *     no __messages translation sync, and no msgprint of the reply's _server_messages (a
 *     frappe.msgprint warning from a method that succeeded). A caller that wants those words
 *     passes `with_messages: true` and gets { message, server_messages } instead, the messages as
 *     plain text, HTML taken out, in the order the server sent them.
 * NB this is not frappe.call's own `quiet` option (which only hides the spinner); sf_kit never
 * passes `quiet` to frappe.call.
 *
 * SESSION EXPIRY, as v16 reports it on /api/method. A request whose sid names no live session:
 * sessions.py get_session_record (v16 :340) sets frappe.response["session_expired"] = 1, clears the
 * cookies and continues as Guest; is_whitelisted (__init__.py:484) then raises SessionExpired
 * (exceptions.py: http_status_code 401), and utils/response.py report_error answers 401 with
 * exc_type "SessionExpired" and session_expired: 1 in the same body. But that reply also DELETES
 * the sid and user_id cookies, so every later call arrives with no sid at all, is a plain Guest
 * with no session_expired flag, and is refused 403 PermissionError ("not permitted ... Login to
 * access"). Reading only the body would call that an ordinary refusal, and a retry queue would
 * keep going. So, as v16's own frappe.request.is_session_expired does (request.js:416), a
 * 401/403 also counts as expiry when the page was signed in (frappe.session) and the user_id
 * cookie is now gone or "Guest". The test for a caller is `err.session_expired`.
 * Two more shapes: a 2xx whose body carries session_expired: 1 (an allow_guest method that ran as
 * Guest because the session had lapsed) REJECTS as expired when the page was signed in, since
 * what it answered was not this person's request; and a 417 is expiry only when its own body
 * says so, never from the cookies, so a frappe.throw from a method that ran signed in keeps its
 * words.
 *
 * Why not frappe.call's own callbacks on the default path: request.js calls `error` with a
 * different argument for nearly every status (417 passes the body, 401/403/404/500/502/504 pass
 * nothing, a network failure passes the xhr), and for QueryTimeoutError / QueryDeadlockError it
 * shows its own dialog and calls nothing at all. The jqXHR frappe.call returns is a thenable that
 * always settles, with the real xhr on failure, so the status and the body come from there. The
 * callbacks are kept only as a fallback for a frappe.call that returns no thenable (a stub, or a
 * wrapper); a status the callback cannot see is then reported as 0.
 *
 * `env` is { frappe, fetch, cookie }: frappe and fetch read when a call is made, `cookie()` returns
 * the page's cookie string. The bundle supplies all three from the page.
 */

import { __ } from "./t.js";

const PASS_THROUGH = ["silent", "freeze", "freeze_message"];

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

// A tag whose end also ends a run of words: a space goes in, so "<p>A</p><p>B</p>" reads "A B".
const BLOCK_TAG = /<\/?(?:p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|details|summary|section|blockquote|pre|hr)\b[^<>]*>/gi;
// A real tag: "<" then a letter, "/" or "!" straight after it. "Qty < 5" has a space there and is text.
const ANY_TAG = /<!--[\s\S]*?-->|<\/?[A-Za-z][^<>]*>/g;
// Elements whose content is not words: their text is dropped with them. An unclosed one runs to the
// end, as it does in a browser.
const NOT_WORDS = /<(script|style|template)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi;

function decode_entities(text) {
	// One pass, so "&amp;lt;" becomes "&lt;" and is never decoded twice into "<".
	return text.replace(/&(#\d+|#x[0-9a-f]+|[a-z]+);/gi, (match, name) => {
		if (name[0] === "#") {
			const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
			return code > 0 && code < 0x110000 ? String.fromCodePoint(code) : match;
		}
		const value = ENTITIES[name.toLowerCase()];
		return value === undefined ? match : value;
	});
}

function tidy(text) {
	return text
		.replace(/\s+/g, " ")
		.replace(/ ([.,;:!?)\]])/g, "$1")
		.replace(/([([]) /g, "$1")
		.trim();
}

// The words of a server message with the markup taken out and the entities decoded. With a DOM,
// the browser's own parser does it (DOMParser builds an inert tree: no script runs, nothing
// loads), after removing script / style / template, whose text is code, not words; in node, a
// tag is only "<" followed by a letter, so "Qty must be < 5 and > 0" survives.
export function strip_html(value) {
	if (value === null || value === undefined) return "";
	const text = String(value).replace(BLOCK_TAG, (tag) => ` ${tag} `);
	if (typeof DOMParser === "function") {
		try {
			const parsed = new DOMParser().parseFromString(text, "text/html");
			const body = parsed && parsed.body;
			if (body && typeof body.querySelectorAll === "function") {
				for (const node of Array.from(body.querySelectorAll("script, style, template"))) {
					if (node && typeof node.remove === "function") node.remove();
				}
			}
			if (body && typeof body.textContent === "string") return tidy(body.textContent);
		} catch (e) {
			// fall through to the plain-text route
		}
	}
	return tidy(decode_entities(text.replace(NOT_WORDS, " ").replace(ANY_TAG, "")));
}

function parse_json(value) {
	if (typeof value !== "string") return value;
	try {
		return JSON.parse(value);
	} catch (e) {
		return null;
	}
}

// "frappe.exceptions.ValidationError: Pick a date" -> "Pick a date"
function exception_text(line) {
	const text = String(line || "").trim();
	const match = /^[\w.]+(?:Error|Exception)\s*:\s*([\s\S]+)$/.exec(text);
	return match ? match[1].trim() : "";
}

// The readable text of each entry in a reply's _server_messages, in order, blanks left out.
function server_message_list(raw) {
	const list = parse_json(raw);
	if (!Array.isArray(list)) return [];
	return list
		.map((item) => {
			const entry = parse_json(item);
			if (entry && typeof entry === "object") return entry.message || "";
			return typeof item === "string" ? item : "";
		})
		.map((text) => strip_html(text))
		.filter(Boolean);
}

function server_messages(raw) {
	return server_message_list(raw).join(" ");
}

// The readable part of a Frappe error body, or "" when there is none.
export function error_text(body, status) {
	body = parse_json(body);
	if (!body || typeof body !== "object") return "";
	const from_messages = server_messages(body._server_messages);
	if (from_messages) return from_messages;
	if (typeof body._error_message === "string" && body._error_message.trim()) {
		return strip_html(body._error_message);
	}
	if (typeof body.message === "string" && body.message.trim()) return strip_html(body.message);
	if (Array.isArray(body.errors) && body.errors[0] && typeof body.errors[0].message === "string") {
		return strip_html(body.errors[0].message);
	}
	if (status >= 500) return "";
	const from_exception = exception_text(String(body.exception || "").split("\n")[0]);
	if (from_exception) return strip_html(from_exception);
	const exc = parse_json(body.exc);
	const trace = Array.isArray(exc) ? exc.filter(Boolean).pop() : typeof exc === "string" ? exc : "";
	const last = String(trace || "")
		.trim()
		.split("\n")
		.pop();
	return strip_html(exception_text(last));
}

// `env` is kept in the signature for existing callers and not read: the sentences are literal
// __() calls, so the extractor finds them.
export function generic_message(env, status, expired) {
	if (expired) return __("Your session has expired. Please sign in again.");
	if (!status) return __("Could not reach the server.");
	if (status === 403) return __("You do not have permission to do that.");
	if (status >= 500) return __("The server could not finish this request. Please try again.");
	return __("The server refused this request.");
}

function read_cookie(env, name) {
	let raw = "";
	try {
		raw = typeof env.cookie === "function" ? String(env.cookie() || "") : "";
	} catch (e) {
		raw = "";
	}
	for (const part of raw.split(";")) {
		const at = part.indexOf("=");
		if (at > 0 && part.slice(0, at).trim() === name) return part.slice(at + 1).trim();
	}
	return null;
}

function signed_in(env) {
	const session = env && env.frappe && env.frappe.session;
	const user = session && (session.logged_in_user || session.user);
	return Boolean(user && user !== "Guest");
}

// Whether a failed reply means the person is signed out (the SESSION EXPIRY note above).
// exc_type SessionExpired is the server saying so outright. The session_expired flag alone counts
// only when this page was signed in: a visitor on a www page with a stale sid cookie gets the flag
// too, and their frappe.throw is a real one. The cookie reading is only for the 401/403 a lapsed
// session produces, never a reason to replace a 417's message.
export function is_session_expired(env, status, body) {
	const object = body && typeof body === "object" ? body : null;
	if (object && object.exc_type === "SessionExpired") return true;
	if (!signed_in(env)) return false;
	if (object && object.session_expired) return true;
	if (status !== 401 && status !== 403) return false;
	if (typeof env.cookie !== "function") return false;
	const user_id = read_cookie(env, "user_id");
	return !user_id || user_id === "Guest";
}

function settle_once(env, resolve, reject) {
	let settled = false;
	const ok = (message) => {
		if (settled) return;
		settled = true;
		resolve(message);
	};
	const fail = (status, body) => {
		if (settled) return;
		settled = true;
		status = Number(status) || 0;
		const parsed = parse_json(body);
		const object = parsed && typeof parsed === "object" ? parsed : null;
		const expired = is_session_expired(env, status, object);
		const exc_type = expired ? "SessionExpired" : object && typeof object.exc_type === "string" ? object.exc_type : "";
		const message = expired
			? generic_message(env, status, true)
			: error_text(object, status) || generic_message(env, status);
		reject({ status, message, exc_type, session_expired: expired });
	};
	return { ok, fail, expired: (status) => fail(status, { exc_type: "SessionExpired", session_expired: 1 }) };
}

// ---- frappe.call's encoding, for the quiet path: frappe.request.prepare, then jQuery.param.

// jQuery 3.7.1's class2type: what toType() calls each built-in; anything else is "object".
const CLASS_TYPES = {};
for (const name of ["Boolean", "Number", "String", "Function", "Array", "Date", "RegExp", "Object", "Error", "Symbol"]) {
	CLASS_TYPES[`[object ${name}]`] = name.toLowerCase();
}

function to_type(value) {
	if (value === null || value === undefined) return String(value);
	return typeof value === "object" || typeof value === "function"
		? CLASS_TYPES[Object.prototype.toString.call(value)] || "object"
		: typeof value;
}

// jQuery.isPlainObject: a plain {} or Object.create(null), from this realm or another.
function is_plain_object(value) {
	if (!value || Object.prototype.toString.call(value) !== "[object Object]") return false;
	const proto = Object.getPrototypeOf(value);
	if (!proto) return true;
	const ctor = Object.prototype.hasOwnProperty.call(proto, "constructor") && proto.constructor;
	return typeof ctor === "function" && Function.prototype.toString.call(ctor) === Function.prototype.toString.call(Object);
}

// jQuery's buildParams, non-traditional: what is left of an arg after prepare.
function build_params(prefix, value, add) {
	if (Array.isArray(value)) {
		value.forEach((item, index) => {
			if (/\[\]$/.test(prefix)) add(prefix, item);
			else build_params(`${prefix}[${typeof item === "object" && item != null ? index : ""}]`, item, add);
		});
	} else if (to_type(value) === "object") {
		for (const name in value) build_params(`${prefix}[${name}]`, value[name], add);
	} else {
		add(prefix, value);
	}
}

// The args as [key, value] string pairs, exactly as frappe.call puts them on the wire.
export function form_pairs(args) {
	const pairs = [];
	if (!args || typeof args !== "object") return pairs;
	const add = (key, value_or_function) => {
		const value = typeof value_or_function === "function" ? value_or_function() : value_or_function;
		pairs.push([key, value == null ? "" : String(value)]);
	};
	for (const key in args) {
		let value = args[key];
		// frappe.request.prepare: `if (opts.args[key] && ($.isPlainObject(...) || $.isArray(...)))`
		if (value && (is_plain_object(value) || Array.isArray(value))) value = JSON.stringify(value);
		build_params(key, value, add);
	}
	return pairs;
}

// $.param's string: encodeURIComponent on both sides, "&" between (jQuery 3 dropped the old
// %20 -> "+" step, so this is byte for byte what frappe.call sends).
export function form_encode(args) {
	return form_pairs(args)
		.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
		.join("&");
}

// "A quiet GET got a 403 PermissionError while signed in" is usually a POST-only method; say so
// once per method, in the console only.
const WARNED_GET = new Set();
function warn_get_refused(method) {
	if (WARNED_GET.has(method)) return;
	WARNED_GET.add(method);
	if (typeof console !== "undefined" && typeof console.warn === "function") {
		console.warn(
			`sf_kit.call: GET ${method} was refused 403 PermissionError. A method whitelisted ` +
				'methods=["POST"] refuses GET ("Not permitted"); call it without method: "GET".'
		);
	}
}

function quiet_call(env, method, args, opts) {
	return new Promise((resolve, reject) => {
		const { ok, fail, expired } = settle_once(env, resolve, reject);
		const fetch_fn = env.fetch;
		if (typeof fetch_fn !== "function" || typeof method !== "string" || !method.trim()) {
			fail(0, null);
			return;
		}
		method = method.trim();
		const http = String(opts.method || "POST").toUpperCase() === "GET" ? "GET" : "POST";
		const encoded = form_encode(args);
		const headers = { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" };
		let url = `/api/method/${encodeURIComponent(method)}`;
		const init = { method: http, headers, credentials: "same-origin", cache: "no-store", redirect: "error" };
		if (http === "GET") {
			if (encoded) url += `?${encoded}`;
		} else {
			const csrf = env.frappe && env.frappe.csrf_token;
			if (csrf && typeof csrf === "string") headers["X-Frappe-CSRF-Token"] = csrf;
			headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8";
			init.body = encoded;
		}

		let pending;
		try {
			pending = fetch_fn(url, init);
		} catch (e) {
			fail(0, null);
			return;
		}
		if (!pending || typeof pending.then !== "function") {
			fail(0, null);
			return;
		}
		// Both handlers in one then(), and a catch after it: whatever throws, the promise settles.
		pending
			.then(
				(response) => {
					const status = response && typeof response.status === "number" ? response.status : 0;
					const read = response && typeof response.text === "function" ? response.text() : null;
					return Promise.resolve(read).then((text) => {
						const body = parse_json(typeof text === "string" ? text : "");
						const object = body && typeof body === "object" && !Array.isArray(body) ? body : null;
						if (status >= 200 && status < 300) {
							// A 200 that is not frappe's JSON is a proxy or a login page: not the server.
							if (!object) fail(0, null);
							// An allow_guest method that ran as Guest because the session lapsed.
							else if (object.session_expired && signed_in(env)) expired(status);
							else if (opts.with_messages) {
								ok({ message: object.message, server_messages: server_message_list(object._server_messages) });
							} else ok(object.message);
							return;
						}
						if (
							http === "GET" &&
							status === 403 &&
							signed_in(env) &&
							object &&
							object.exc_type === "PermissionError" &&
							!is_session_expired(env, status, object)
						) {
							warn_get_refused(method);
						}
						fail(status, object);
					});
				},
				() => fail(0, null)
			)
			.catch(() => fail(0, null));
	});
}

export function call(env, method, args, opts) {
	env = env || {};
	opts = opts || {};
	if (opts.quiet) return quiet_call(env, method, args, opts);
	return new Promise((resolve, reject) => {
		const { ok, fail } = settle_once(env, resolve, reject);

		const frappe = env.frappe;
		if (!frappe || typeof frappe.call !== "function") {
			fail(0, null);
			return;
		}
		const request = { method, args: args || {} };
		PASS_THROUGH.forEach((key) => {
			if (opts[key] !== undefined) request[key] = opts[key];
		});
		// Ignored once frappe.call has returned a thenable: v16 runs its own fail handler (and so
		// `error`) before ours, and for a 417 it passes the body without the status.
		let thenable = false;
		request.callback = (r) => {
			if (!thenable) ok(r ? r.message : undefined);
		};
		request.error = (r) => {
			if (thenable) return;
			fail(r && typeof r.status === "number" ? r.status : 0, r && r.responseText ? r.responseText : r);
		};

		let pending;
		try {
			pending = frappe.call(request);
		} catch (e) {
			fail(0, null);
			return;
		}
		if (pending && typeof pending.then === "function") {
			thenable = true;
			// Both handlers are attached in one then(), so a rejection is always handled here.
			try {
				pending.then(
					(data) => {
						const body = parse_json(data);
						ok(body && typeof body === "object" ? body.message : undefined);
					},
					(xhr) => {
						const status = xhr && typeof xhr.status === "number" ? xhr.status : 0;
						const body = xhr ? xhr.responseJSON || xhr.responseText : null;
						fail(status, body);
					}
				);
			} catch (e) {
				fail(0, null);
			}
		}
	});
}
