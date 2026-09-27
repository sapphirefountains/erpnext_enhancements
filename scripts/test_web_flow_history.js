#!/usr/bin/env node
/**
 * Browser Back / Forward on three customer and traveller web pages: `/pay-card`, `/itinerary`
 * and `/contract-sign`.
 *
 * Loads the REAL page scripts (the inline script of `www/pay-card.html`, rendered with stand-in
 * values; `public/js/travel/itinerary.js`; `public/js/contract_sign/contract_sign.js`) into a vm
 * context over a small fake DOM and a fake session history, then taps and presses Back and
 * Forward. The history behaves the way a browser's does where it matters: a traversal is an
 * asynchronous task that fires `popstate` when it lands inside the same document and leaves the
 * page otherwise, `pushState` drops the forward entries, and a push made without a tap is counted
 * (Chrome's Back skips such entries). Plain node, no runner and no npm install.
 *
 * What it pins:
 *
 *   /pay-card
 *   - the page reaches the server over fetch (`/api/method/<method>`, a same-origin form POST
 *     carrying `X-Frappe-CSRF-Token`), never frappe.call. The website build of frappe.call
 *     (frappe v16 website/js/website.js) calls back on an HTTP 200 only and never calls error(),
 *     so in production a declined card (a 417) left Pay on "Please wait…" with Back held — while
 *     this harness, faking frappe.call *with* an error() callback, passed. It fakes fetch now,
 *     and the page's frappe.call stand-in, like the real one, never answers a refusal;
 *   - a decline after Pay returns to the card step with the server's message, and Continue works
 *     again; a decline at Continue says why under the form, and no answer there (a quote moves no
 *     money) says to try again;
 *   - Pay charges the quote its review was drawn for, even when the page's latest quote has been
 *     set aside (the Payment Element's change event), and a Pay tap with no quote behind the
 *     review on screen says what to do instead of doing nothing;
 *   - the boot entry is stamped with replaceState, nothing is pushed on load, and Back from it
 *     leaves the page;
 *   - Continue pushes one review entry naming its quote (two arguments: `?invoice=` never
 *     changes), and the phone's Back returns to the card step with every fee line hidden;
 *   - Forward shows that review again while the page still holds its quote, and a review whose
 *     quote is gone (the card changed, a new Continue, a failed charge) is stepped back off,
 *     so Forward lands on the card step and one Back from the card step still leaves the page;
 *   - the page's own "Back — use a different payment method" shows the card step at once (a
 *     Pay tap before the traversal lands charges nothing) and goes through the history;
 *   - Back while a charge is in flight interrupts nothing: a success still goes to
 *     /stripe-return (with `location.replace`);
 *   - a definite failure (a refusal or a decline — a 417 naming its exc_type — or 3-D Secure
 *     refused with a card, validation or invalid-request error) spends its quote: the card step
 *     says why, and neither Pay nor Forward can send that row and token again;
 *   - a refusal for the invoice rather than the card (`PaymentBlocked`: another payment
 *     settling, the invoice paid or credited), at Pay or at Continue, never leaves a live card
 *     form beside it: the page asks the server, which renders why; an emailed link that could
 *     not be closed (`LinkStillOpen`) and an earlier attempt Stripe would not confirm canceled
 *     (`AttemptUnreleased`) are ordinary refusals, at Pay and at Continue: the card form stays
 *     and says why, since a render (which never releases) would show the form with no word of it;
 *     the message shown is the last of `_server_messages` (the throw, after any msgprint);
 *   - a session the page no longer has reloads too, at Continue and at Pay, since every call
 *     would be refused the same way until a render: a stale CSRF token (`CSRFTokenError`), and a
 *     sign-in that expired or ended elsewhere (frappe's `PermissionError` naming the method, or
 *     carrying `session_expired`); the endpoints' own `PermissionError` stays an ordinary refusal;
 *   - no answer (a dropped connection, a 5xx — even one naming an exc_type — a proxy's error
 *     page, a body cut off or unparseable, a 200 carrying an exception or no message, no answer
 *     within the page's timeout (the request then aborted), 3-D Secure ending in any other error
 *     type — Stripe unreachable, a rate limit, one the page does not know — or anything throwing
 *     after the answer) is never a failure — the charge may have gone
 *     through — so the page never shows a card form then: it holds Pay and Back and asks the
 *     server again, which renders "being processed" for an attempt on record; and a server that
 *     could not learn the outcome itself answers Processing, which goes to /stripe-return;
 *   - a price that lands after the payer has moved on is dropped;
 *   - a page restored from the back-forward cache asks the server again.
 *   /itinerary
 *   - boot reads `?trip=` and `?as=` and asks the server for exactly that (`as` goes up as
 *     `as_employee`), listed trip or not: a trip they own, or a coordinator's link, loads with no
 *     history call, and so does a `?trip=` for someone with no trips (or no Employee record);
 *   - the entry is replaced only when `?trip=` is missing, or when the server REFUSES it: a
 *     trip that is gone or not theirs (403, 404) falls back to the default trip (keeping any
 *     other query and the hash, dropping `?as=`), a person not on the trip (417, and only 417)
 *     to the trip's default view, and with no trip to fall back on the page says so in place;
 *     a refused entry the page pushed from the very view it would fall back to is stepped back
 *     off instead, so no two entries in a row are the same; and while "Report a problem" is
 *     open a refusal writes no history at all;
 *   - signed out since the page loaded (a 403 with `session_expired`, or the `user_id` cookie
 *     at Guest) is not a refusal: the entry stays, and the page offers to sign in back to it;
 *   - the default trip is one they travel on (`mine`), on this phone's calendar date, not UTC's;
 *   - a chip tap pushes `?trip=<name>` and drops `?as=`; a person pick pushes `?trip=&as=`; Back
 *     and Forward load that entry's trip and person and push nothing;
 *   - the whole-crew view gives each person on a shared booking their own number;
 *   - a stale response (or error) for a trip, or a person, no longer on screen is dropped;
 *   - while "Report a problem" is open its Back is its own, and the page follows the address
 *     only once the panel has answered;
 *   - a booking row's `attachment` (its receipt: money) is never drawn, even from a stale
 *     answer; each booking lists its files, a picture opening the viewer and anything else
 *     (a PDF) opening in a new tab with no history write;
 *   - "Documents" pushes `&view=docs` and lists the files for the person shown, the whole
 *     trip's first; Back returns to the day list and Forward restores it, a person pick keeps
 *     the screen, a trip chip drops it (and `&file=`), and a reload lands on it; "Day by day"
 *     steps Back when the day list is the entry behind;
 *   - a picture pushes `&file=`: Back closes the viewer and Forward reopens it, Close and
 *     Escape go Back (or, opened straight from a link, rewrite the entry), a reload reopens
 *     it, a file the answer does not list (or a PDF) is not opened, and nothing about the
 *     viewer moves while "Report a problem" is open.
 *   /contract-sign
 *   - no history entry is ever added: signing and declining swap in place (declining no
 *     longer reloads into "This link isn't available");
 *   - Back from Stripe's card page lands on "Already signed" with the enrolment offered again,
 *     only while the server says it is unfinished and only with the link this tab was given
 *     (kept in sessionStorage under a fingerprint of the ref, never the ref);
 *   - a back-forward-cache restore re-enables "Save a card".
 *
 * Run: node scripts/test_web_flow_history.js [pay-card|itinerary|contract-sign]
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const ORIGIN = "https://erp.example.com";

let failures = 0;
let checks = 0;

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function clone(v) {
	return v === undefined || v === null ? null : JSON.parse(JSON.stringify(v));
}

async function flush() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve));
	await new Promise((resolve) => setTimeout(resolve, 2));
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve));
}

function stripComments(js) {
	return js.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'])\/\/.*$/gm, "$1");
}

// ---------------------------------------------------------------------------- fake DOM

class El {
	constructor(tag, env) {
		this.tagName = String(tag).toUpperCase();
		this.env = env;
		this.children = [];
		this.parentNode = null;
		this.listeners = {};
		this.style = {};
		this.attrs = {};
		this.className = "";
		this.id = "";
		this.hidden = false;
		this.disabled = false;
		this.checked = false;
		this.value = "";
		this.text = "";
		this.href = "";
	}
	get textContent() {
		return this.text + this.children.map((c) => c.textContent).join("");
	}
	set textContent(v) {
		this.detachChildren();
		this.text = v == null ? "" : String(v);
	}
	set innerHTML(v) {
		this.detachChildren();
		this.text = "";
	}
	detachChildren() {
		this.children.forEach((c) => {
			c.parentNode = null;
		});
		this.children = [];
	}
	get classList() {
		const self = this;
		const list = () => self.className.split(/\s+/).filter(Boolean);
		return {
			add: (n) => list().includes(n) || (self.className = list().concat(n).join(" ")),
			remove: (n) => (self.className = list().filter((x) => x !== n).join(" ")),
			contains: (n) => list().includes(n),
			toggle(n, force) {
				const on = force === undefined ? !list().includes(n) : !!force;
				if (on) this.add(n);
				else this.remove(n);
				return on;
			},
		};
	}
	appendChild(c) {
		c.parentNode = this;
		this.children.push(c);
		return c;
	}
	removeChild(c) {
		this.children = this.children.filter((x) => x !== c);
		c.parentNode = null;
		return c;
	}
	// In the page: under a <body>. An element a redraw threw away is not.
	get isConnected() {
		let n = this;
		while (n.parentNode) n = n.parentNode;
		return n.tagName === "BODY";
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	removeAttribute(k) {
		delete this.attrs[k];
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	dispatch(type, ev) {
		ev = Object.assign({ type, target: this, preventDefault() {} }, ev || {});
		(this.listeners[type] || []).slice().forEach((fn) => fn.call(this, ev));
	}
	click() {
		if (this.disabled) return;
		this.env.browser.activation = true; // a tap: the licence for one push
		this.dispatch("click");
	}
	focus() {
		this.env.focused = this; // document.activeElement
	}
	getBoundingClientRect() {
		return { width: 0, height: 0, left: 0, top: 0 };
	}
	find(cls) {
		const out = [];
		const walk = (n) =>
			n.children.forEach((c) => {
				if (c.classList.contains(cls)) out.push(c);
				walk(c);
			});
		walk(this);
		return out;
	}
}

function makeDocument(env, ids) {
	const byId = {};
	const document = {
		listeners: {},
		head: new El("head", env),
		createElement: (tag) => new El(tag, env),
		getElementById: (id) => byId[id] || null,
		get activeElement() {
			return env.focused || null;
		},
		addEventListener(type, fn) {
			(this.listeners[type] = this.listeners[type] || []).push(fn);
		},
		// `ev` for an event that carries more than its type (a keydown's `key`).
		dispatch(type, ev) {
			const event = Object.assign({ type, defaultPrevented: false }, ev || {});
			event.preventDefault = () => {
				event.defaultPrevented = true;
			};
			(this.listeners[type] || []).slice().forEach((fn) => fn(event));
			return event;
		},
	};
	for (const [id, init] of Object.entries(ids)) {
		const el = new El(init.tag || "div", env);
		el.id = id;
		Object.assign(el, init);
		if (init.style) el.style = Object.assign({}, init.style);
		byId[id] = el;
	}
	document.byId = byId;
	return document;
}

// ---------------------------------------------------------------------------- fake browser

/**
 * One tab's session history: the page before this one (`prev`), then the page under test.
 * `back()` / `forward()` are the browser's buttons (no activation); the page's own
 * `history.back()` is the same traversal. Leaving the document is recorded, never followed.
 * `opts.behind` puts entries of this same page between the two ([{url, state}], oldest
 * first): the page reloaded on an entry it had pushed, with the ones behind it still there.
 */
function makeBrowser(url, opts) {
	opts = opts || {};
	const behind = (opts.behind || []).map((e) => ({ doc: "page", url: ORIGIN + e.url, state: clone(e.state) }));
	const browser = {
		entries: [
			{ doc: "prev", url: ORIGIN + (opts.prevPath || "/pay"), state: null },
			...behind,
			{ doc: "page", url: ORIGIN + url, state: clone(opts.state) },
		],
		index: 1 + behind.length,
		calls: [], // every pushState / replaceState: {kind, argc, url}
		unactivated: 0,
		activation: false,
		left: null, // the URL the tab went to when it left this document
		replacedWith: null,
		assigned: null,
		reloads: 0,
		tasks: [],
		listeners: { popstate: [], pageshow: [] },
	};

	const current = () => browser.entries[browser.index];

	browser.history = {
		get state() {
			return clone(current().state);
		},
		get length() {
			return browser.entries.length;
		},
		pushState(state, title, url) {
			browser.calls.push({ kind: "push", argc: arguments.length, url: url === undefined ? null : url });
			if (!browser.activation) browser.unactivated += 1;
			browser.activation = false;
			const next = { doc: "page", url: url ? new URL(url, current().url).href : current().url, state: clone(state) };
			browser.entries.splice(browser.index + 1);
			browser.entries.push(next);
			browser.index += 1;
		},
		replaceState(state, title, url) {
			browser.calls.push({ kind: "replace", argc: arguments.length, url: url === undefined ? null : url });
			const entry = current();
			entry.state = clone(state);
			if (url) entry.url = new URL(url, entry.url).href;
		},
		back() {
			browser.traverse(-1);
		},
		forward() {
			browser.traverse(1);
		},
	};

	browser.traverse = function (delta) {
		browser.tasks.push(() => {
			const target = browser.index + delta;
			if (target < 0 || target >= browser.entries.length) return;
			const from = current();
			browser.index = target;
			const to = current();
			if (from.doc !== "page" || to.doc !== "page") {
				browser.left = to.url;
				return;
			}
			browser.fire("popstate", { state: clone(to.state) });
		});
	};
	browser.back = () => browser.traverse(-1);
	browser.forward = () => browser.traverse(1);

	browser.fire = function (type, ev) {
		if (browser.left || browser.replacedWith) return;
		browser.listeners[type].slice().forEach((fn) => fn(Object.assign({ type }, ev)));
	};

	browser.settle = async function () {
		await flush();
		while (browser.tasks.length) {
			browser.tasks.shift()();
			await flush();
		}
	};

	const location = {
		get href() {
			return current().url;
		},
		set href(v) {
			browser.assigned = String(v);
		},
		get pathname() {
			return new URL(current().url).pathname;
		},
		get search() {
			return new URL(current().url).search;
		},
		get hash() {
			return new URL(current().url).hash;
		},
		replace(v) {
			// A new document takes this entry's place; Back from it reaches the one before.
			browser.replacedWith = String(v);
			browser.entries[browser.index] = { doc: "next", url: new URL(v, ORIGIN).href, state: null };
		},
		reload() {
			browser.reloads += 1;
		},
	};
	browser.location = location;

	browser.window = {
		history: browser.history,
		location,
		addEventListener(type, fn) {
			(browser.listeners[type] = browser.listeners[type] || []).push(fn);
		},
		removeEventListener() {},
		scrollTo() {},
	};
	return browser;
}

function runInPage(source, globals, file) {
	const context = vm.createContext(globals);
	vm.runInContext(source, context, { filename: file });
	return context;
}

// ---------------------------------------------------------------------------- /pay-card

function payCardScript() {
	const html = fs.readFileSync(path.join(APP, "www", "pay-card.html"), "utf8");
	const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
	let js = blocks.find((b) => b.includes("frappe.csrf_token"));
	const values = {
		"csrf_token": '"tok"',
		"currency | tojson": '"USD"',
		"invoice.name | tojson": '"ACC-SINV-0001"',
		"publishable_key | tojson": '"pk_test_x"',
		"amount_minor": "20000",
	};
	js = js.replace(/"\{\{ csrf_token \}\}"/, values.csrf_token);
	js = js.replace(/\{\{\s*_\('([^']*)'\)\s*\}\}/g, "$1");
	js = js.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (m, expr) => {
		if (!(expr in values)) throw new Error(`pay-card.html: no stand-in for {{ ${expr} }}`);
		return values[expr];
	});
	if (/\{\{|\{%/.test(js)) throw new Error("pay-card.html: template syntax left in the script");
	return js;
}

const CREDIT = {
	stripe_payment: "SP-1",
	amount_display: "$200.00",
	total_display: "$205.80",
	surcharge: 5.8,
	surcharge_label: "Credit card processing fee",
	surcharge_display: "$5.80",
	surcharge_disclosure: "A 2.9% fee applies to credit cards.",
};
const DEBIT = { stripe_payment: "SP-2", amount_display: "$200.00", total_display: "$200.00", surcharge: 0 };
// frappe's JSON body for a frappe.throw on the server (HTTP 417): the raised class in exc_type,
// the message in _server_messages. The server's definite answer that nothing was charged.
const DECLINED = { exc_type: "ValidationError", _server_messages: '["Your card was declined."]' };
// The same, exactly as frappe v16 serialises it: _server_messages is a JSON list of JSON-encoded
// message dicts (frappe.utils.response.make_logs). The message is the one
// card_element._not_charged_message builds for a card error (a 402) from Stripe's own words.
const STRIPE_DECLINED_MESSAGE = "The payment did not go through: Your card was declined. Nothing was charged.";
function serverMessages(...messages) {
	return JSON.stringify(
		messages.map((message) =>
			JSON.stringify({ message, title: "Message", indicator: "red", raise_exception: 1, __frappe_exc_id: "e1" })
		)
	);
}
const STRIPE_DECLINED = { exc_type: "ValidationError", _server_messages: serverMessages(STRIPE_DECLINED_MESSAGE) };
// A refusal for the invoice rather than the card (another payment settling, the invoice paid
// or credited): the server's PaymentBlocked. The page reloads, and /pay-card renders why.
const BLOCKED = { exc_type: "PaymentBlocked", _server_messages: '["A payment for this invoice is already being processed."]' };
// An emailed link Stripe could not be asked to close: a definite refusal with nothing to render.
const LINK_OPEN = { exc_type: "LinkStillOpen", _server_messages: '["A payment link for this invoice is still open."]' };
// An earlier card attempt that cannot charge, whose cancel Stripe would not confirm just now. A
// PaymentBlocked subclass, so the server-side callers treat it as a refusal; but a render never
// releases, so it would read that attempt as not blocking and show the card form with no message.
const UNRELEASED = {
	exc_type: "AttemptUnreleased",
	_server_messages: '["An earlier card payment attempt for this invoice could not be released just now."]',
};
// What stripe.handleNextAction resolves with when the bank refuses 3-D Secure.
const AUTH_FAILED = { type: "invalid_request_error", code: "payment_intent_authentication_failure", message: "Authentication failed." };

function loadPayCard(opts) {
	opts = opts || {};
	const env = {};
	const browser = makeBrowser("/pay-card?invoice=ACC-SINV-0001", { state: opts.state });
	env.browser = browser;
	const none = { style: { display: "none" } };
	const document = makeDocument(env, {
		"payment-element": {},
		"card-error": {},
		"continue-btn": { tag: "button" },
		"step-card": { style: { display: "" } },
		"step-review": none,
		"review-amount": {},
		"review-total": {},
		"review-fee-row": none,
		"review-fee-label": {},
		"review-fee": {},
		"review-disclosure": none,
		"review-nofee": none,
		"review-error": {},
		"pay-btn": { tag: "button" },
		"back-btn": { tag: "button" },
	});
	// Every request the page makes, over fetch: {url, method (the dotted path after
	// /api/method/), args (parsed from the form-encoded body), headers, init}, with the ways to
	// answer it — respond(status, body), callback(data) (a 200), error(body), drop(), cut(status).
	const calls = [];
	// The website build of frappe.call is on the live page too (frappe v16 website/js/website.js).
	// It calls back on an HTTP 200 only and never calls error(), so a refusal never reaches a page
	// that relies on it: what production did with a declined card. Recorded so a page that reaches
	// for it again is caught; like the real one, it never answers a refusal.
	const frappeCalls = [];
	const frappe = {
		call(o) {
			frappeCalls.push(o);
		},
	};
	const reply = (status, payload, cut) => {
		const text = typeof payload === "string" ? payload : JSON.stringify(payload);
		return {
			ok: status >= 200 && status < 300,
			status,
			text: () => (cut ? Promise.reject(new TypeError("network error")) : Promise.resolve(text)),
			json: () => (cut ? Promise.reject(new TypeError("network error")) : Promise.resolve(JSON.parse(text))),
		};
	};
	const fetch = (url, init) =>
		new Promise((resolve, reject) => {
			const u = String(url);
			const call = {
				url: u,
				method: u.replace(/^\/api\/method\//, ""),
				args: Object.fromEntries(new URLSearchParams(String(init.body || ""))),
				headers: Object.assign({}, init.headers),
				init,
				aborted: false,
				respond: (status, body) => resolve(reply(status, body)),
				// A 200 with frappe's JSON body.
				callback: (data) => resolve(reply(200, data)),
				// A frappe.throw (a 417 naming its exc_type), a 417 whose body is text, a dropped
				// connection ({status: 0}), or with nothing, a proxy's 502 page.
				error(body) {
					if (body && typeof body === "object" && body.exc_type) resolve(reply(417, body));
					else if (typeof body === "string") resolve(reply(417, body));
					else if (body && typeof body === "object" && body.status === 0) this.drop();
					else resolve(reply(502, "<html><body><h1>502 Bad Gateway</h1></body></html>"));
				},
				drop: () => reject(new TypeError("Failed to fetch")),
				// A status line, then the connection drops before the body arrives.
				cut: (status) => resolve(reply(status, "", true)),
			};
			if (init.signal) {
				init.signal.addEventListener("abort", () => {
					call.aborted = true;
					reject(new Error("The operation was aborted."));
				});
			}
			calls.push(call);
		});
	// The page's timers (its request timeouts), held until a test fires them.
	const timers = [];
	const pageSetTimeout = (fn, ms) => {
		timers.push({ fn, ms, id: timers.length + 1, live: true });
		return timers.length;
	};
	const pageClearTimeout = (id) => {
		const t = timers.find((x) => x.id === id);
		if (t) t.live = false;
	};
	const stripeState = { tokens: 0, nextAction: null, elementListeners: {} };
	const paymentElement = {
		mount() {},
		on(type, fn) {
			(stripeState.elementListeners[type] = stripeState.elementListeners[type] || []).push(fn);
		},
	};
	const Stripe = () => ({
		elements: () => ({ create: () => paymentElement, submit: async () => ({}) }),
		createConfirmationToken: async () => {
			if (stripeState.failTokens) throw new Error("Stripe.js could not reach Stripe");
			return { confirmationToken: { id: "ctok_" + ++stripeState.tokens } };
		},
		handleNextAction: () =>
			new Promise((resolve) => {
				stripeState.nextAction = resolve;
			}),
	});
	const win = browser.window;
	const globals = Object.assign(win, {
		window: win,
		document,
		history: browser.history,
		frappe,
		fetch,
		Stripe,
		console,
		setTimeout: pageSetTimeout,
		clearTimeout: pageClearTimeout,
	});
	if (!opts.noAbortController) globals.AbortController = AbortController;
	runInPage(payCardScript(), globals, "pay-card.html");
	const $ = (id) => document.byId[id];
	const take = (suffix) => {
		const i = calls.findIndex((c) => c.method.endsWith(suffix));
		return i === -1 ? null : calls.splice(i, 1)[0];
	};
	const page = {
		browser,
		$,
		calls,
		frappeCalls,
		stripe: stripeState,
		take,
		// The delays of the page's timers still running, and firing them (a request timing out).
		timers: {
			pending: () => timers.filter((t) => t.live).map((t) => t.ms),
			async fire() {
				timers.filter((t) => t.live).forEach((t) => {
					t.live = false;
					t.fn();
				});
				await flush();
			},
		},
		shown: () => ($("step-review").style.display === "none" ? "card" : "review"),
		visible: (id) => $(id).style.display !== "none",
		async continueWith(quote) {
			$("continue-btn").click();
			await flush();
			const call = take("portal_price_card_payment");
			if (!call) {
				const how = frappeCalls.length ? ": it used frappe.call, whose website build never calls error()" : "";
				throw new Error(`Continue sent no request over fetch${how}`);
			}
			if (quote) {
				call.callback({ message: quote });
				await flush();
			}
			return call;
		},
		async pay() {
			$("pay-btn").click();
			await flush();
			return take("portal_confirm_card_payment");
		},
		// The payer edits the card in the Payment Element.
		editCard() {
			(stripeState.elementListeners.change || []).forEach((fn) => fn({ elementType: "payment", complete: true }));
		},
		pushes: () => browser.calls.filter((c) => c.kind === "push").length,
	};
	return page;
}

async function testPayCard() {
	console.log("/pay-card");
	const source = payCardScript();
	check("no beforeunload trap", /beforeunload/.test(source), false);
	check("no URL is ever passed to the history", /(push|replace)State\([^)]*,[^)]*,/.test(stripComments(source)), false);
	// The website build of frappe.call never calls error(): a page that relies on it never hears
	// a refusal (tests/test_stripe_payments.py holds the same line).
	check("never frappe.call or frappe.xcall: the server is reached over fetch", /\bfrappe\s*\.\s*x?call\b/.test(stripComments(source)), false);
	check("...at frappe's REST route, with the CSRF token", [/fetch\("\/api\/method\/"/.test(source), /"X-Frappe-CSRF-Token": frappe\.csrf_token/.test(source)], [true, true]);

	// Boot, and Back from the first entry.
	let p = loadPayCard();
	check("boot: one replaceState, no push", p.browser.calls.map((c) => c.kind), ["replace"]);
	check("boot: the entry says card", p.browser.history.state, { payCard: "card" });
	p.browser.back();
	await p.browser.settle();
	check("Back from the card step leaves the page (to /pay)", p.browser.left, ORIGIN + "/pay");

	// Continue, Back, Forward: Forward shows the review again while its quote stands.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	check("Continue shows the review", p.shown(), "review");
	check("Continue pushes one review entry", p.browser.calls.slice(1), [{ kind: "push", argc: 2, url: null }]);
	check("the review entry names its quote", p.browser.history.state, { payCard: "review", quote: "SP-1" });
	check("credit quote shows its fee line", [p.visible("review-fee-row"), p.visible("review-disclosure")], [true, true]);
	p.browser.back();
	await p.browser.settle();
	check("Back returns to the card step", p.shown(), "card");
	check("...without leaving the page", p.browser.left, null);
	check("...and hides every fee line of the old quote", [p.visible("review-fee-row"), p.visible("review-disclosure"), p.visible("review-nofee")], [false, false, false]);
	p.$("pay-btn").click();
	await flush();
	check("...and Pay, off screen, charges nothing", p.take("portal_confirm_card_payment"), null);
	p.browser.forward();
	await p.browser.settle();
	check("Forward with the card unchanged shows that review again", [p.shown(), p.visible("review-fee-row"), p.$("pay-btn").textContent], ["review", true, "Pay $205.80"]);
	check("...pushing and replacing nothing", p.browser.calls.length, 2);
	let confirm = await p.pay();
	check("...and its Pay charges that quote", confirm && confirm.args, { stripe_payment: "SP-1", confirmation_token: "ctok_1" });

	// No duplicate entries: from a restored review, Back is the card step and one more leaves.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.browser.back();
	await p.browser.settle();
	p.browser.forward();
	await p.browser.settle();
	p.browser.back();
	await p.browser.settle();
	check("Back from a restored review: the card step", [p.shown(), p.browser.left], ["card", null]);
	p.browser.back();
	await p.browser.settle();
	check("...and one more Back leaves the page", p.browser.left, ORIGIN + "/pay");

	// A changed card voids the quote: Forward lands on the card step, and the entries stay honest.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.browser.back();
	await p.browser.settle();
	p.editCard();
	p.browser.forward();
	await p.browser.settle();
	check("after the card changes, Forward lands on the card step", [p.shown(), p.browser.index], ["card", 1]);
	check("...stepping back off the stale review, never re-stamping it", p.browser.calls.map((c) => c.kind), ["replace", "push"]);
	p.browser.back();
	await p.browser.settle();
	check("...so one Back from the card step leaves the page", p.browser.left, ORIGIN + "/pay");

	// A new card after Back: a new quote, and its entry takes the old review's place.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.browser.back();
	await p.browser.settle();
	p.editCard();
	await p.continueWith(DEBIT);
	check("a debit quote after a credit one: no fee line, the no-fee note", [p.visible("review-fee-row"), p.visible("review-disclosure"), p.visible("review-nofee")], [false, false, true]);
	check("...on an entry that replaced the old review's", [p.browser.entries.length, p.browser.history.state], [3, { payCard: "review", quote: "SP-2" }]);

	// A change event while the review is up (the Element is hidden then) voids nothing.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.editCard();
	confirm = await p.pay();
	check("a change event under the review leaves its quote payable", confirm && confirm.args.stripe_payment, "SP-1");

	// The page's own Back goes through the history, and is on screen at once.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.$("back-btn").click();
	check("in-page Back shows the card step at once", p.shown(), "card");
	p.$("pay-btn").click();
	await flush();
	check("...so a Pay tap before the traversal lands charges nothing", p.take("portal_confirm_card_payment"), null);
	await p.browser.settle();
	check("...then steps back off the review entry", [p.shown(), p.browser.index, p.browser.history.state], ["card", 1, { payCard: "card" }]);
	p.browser.forward();
	await p.browser.settle();
	check("...and Forward shows that review again, as after the phone's Back", p.shown(), "review");
	p.browser.back();
	await p.browser.settle();
	p.browser.back();
	await p.browser.settle();
	check("...and two Backs from it leave the page", p.browser.left, ORIGIN + "/pay");

	// A charge that succeeds, with no Back.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	check("Pay sends the quoted token", confirm.args, { stripe_payment: "SP-1", confirmation_token: "ctok_1" });
	check("the in-page Back is disabled while charging", p.$("back-btn").disabled, true);
	p.$("back-btn").disabled = false; // even if it were not
	p.$("back-btn").click();
	await p.browser.settle();
	check("...and does nothing mid-charge", [p.shown(), p.browser.index], ["review", 2]);
	confirm.callback({ message: { stripe_payment: "SP-1", status: "Paid", requires_action: false } });
	await flush();
	check("success replaces the review entry with /stripe-return", p.browser.replacedWith, "/stripe-return?status=success&sp=SP-1");
	check("...never pushing it", p.browser.assigned, null);
	p.browser.back();
	await p.browser.settle();
	check("Back from /stripe-return reaches the card entry (a fresh download)", p.browser.left, ORIGIN + "/pay-card?invoice=ACC-SINV-0001");

	// Back while the charge is in flight, then success.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	p.browser.back();
	await p.browser.settle();
	check("Back mid-charge keeps the review on screen", p.shown(), "review");
	check("...and the charge running", p.$("pay-btn").disabled, true);
	confirm.callback({ message: { stripe_payment: "SP-1", status: "Paid" } });
	await flush();
	check("...which still goes to /stripe-return", p.browser.replacedWith, "/stripe-return?status=success&sp=SP-1");

	// Back while the charge is in flight, then a decline (a frappe.throw: a 417 naming its exc_type).
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	p.browser.back();
	await p.browser.settle();
	confirm.error(DECLINED);
	await flush();
	check("a failure after Back shows the card step", p.shown(), "card");
	check("...with both buttons usable again", [p.$("pay-btn").disabled, p.$("back-btn").disabled], [false, false]);
	await p.browser.settle();
	check("...in place: the browser was on the card entry already", [p.browser.index, p.browser.left], [1, null]);

	// Back then Forward while charging, then failure: the quote is spent, so the card step.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	p.browser.back();
	await p.browser.settle();
	p.browser.forward();
	await p.browser.settle();
	confirm.error(DECLINED);
	await p.browser.settle();
	check("Back then Forward mid-charge, then a failure: the card step, off the review's entry", [p.shown(), p.browser.index, p.$("pay-btn").disabled], ["card", 1, false]);

	// A refused or declined charge, no Back: the quote is spent, never sent again.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error({ exc_type: "ValidationError" });
	await p.browser.settle();
	check("a refused charge: the card step, off the review's entry", [p.shown(), p.browser.index, p.browser.left], ["card", 1, null]);
	p.$("pay-btn").click();
	await flush();
	check("...and its quote is never sent again", p.take("portal_confirm_card_payment"), null);
	p.browser.forward();
	await p.browser.settle();
	check("...not even by Forward onto its review", [p.shown(), p.browser.index], ["card", 1]);
	await p.continueWith(DEBIT);
	confirm = await p.pay();
	check("...while Continue prices a new one, which Pay sends", confirm && confirm.args, { stripe_payment: "SP-2", confirmation_token: "ctok_2" });

	// Two answers to one charge step back once.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(DECLINED);
	confirm.error(DECLINED);
	await p.browser.settle();
	check("two answers to one charge step back only once", [p.shown(), p.browser.index, p.browser.left], ["card", 1, null]);

	// No answer is not a failure: the charge may have gone through (a read timeout after Stripe
	// charged, a worker killed by a deploy). Never a fresh card form: the page asks the server
	// again, in place of the review, and /pay-card renders "being processed" for an attempt it
	// has on record. Pay, Back and Forward change nothing meanwhile.
	const RELOAD = ORIGIN + "/pay-card?invoice=ACC-SINV-0001";
	for (const [label, answer, opts] of [
		["a dropped connection", (c) => c.drop()],
		["a proxy's 502 page", (c) => c.error()],
		["a 504 with an empty body", (c) => c.respond(504, "")],
		// An error after Stripe charged is a 500, and frappe names its class too: not a refusal.
		["a 500 naming an exc_type", (c) => c.respond(500, { exc_type: "ValidationError", _server_messages: serverMessages("Server error") })],
		["a 417 whose body would not parse", (c) => c.error("<html>")],
		["a 4xx naming no exc_type", (c) => c.respond(403, { message: "Forbidden" })],
		["a status, then the connection dropped before the body", (c) => c.cut(200)],
		["a 200 carrying an exception", (c) => c.callback({ exc: '["Traceback"]' })],
		["a 200 with no message", (c) => c.callback({})],
		["a 200 whose body would not parse", (c) => c.respond(200, "<html>")],
		["no answer within the timeout", (c, pg) => pg.timers.fire()],
		["no answer within the timeout, in a browser with no AbortController", (c, pg) => pg.timers.fire(), { noAbortController: true }],
	]) {
		p = loadPayCard(opts);
		await p.continueWith(CREDIT);
		confirm = await p.pay();
		await answer(confirm, p);
		await p.browser.settle();
		check(`${label}: the page asks the server again`, p.browser.replacedWith, RELOAD);
		if (label.startsWith("no answer within the timeout")) {
			// The request is abandoned, not left running in the tab, wherever the browser can.
			check("...abandoning the request where the browser can abort it", confirm.aborted, !(opts && opts.noAbortController));
		}
		check("...never showing the card step", p.shown(), "review");
		check("...with Pay and Back held", [p.$("pay-btn").disabled, p.$("pay-btn").textContent, p.$("back-btn").disabled], [true, "Checking your payment…", true]);
		p.$("pay-btn").disabled = false; // even if it were not
		p.$("pay-btn").click();
		await flush();
		check("...and the quote is never sent again", p.take("portal_confirm_card_payment"), null);
		confirm.error(DECLINED);
		await flush();
		check("...nor does a late answer bring the card step back", [p.shown(), p.$("card-error").textContent], ["review", ""]);
	}

	// Back mid-charge, then no answer: the same, from the card entry the browser is on.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	p.browser.back();
	await p.browser.settle();
	confirm.error();
	await p.browser.settle();
	check("Back mid-charge, then no answer: the server is asked, no card step", [p.browser.replacedWith, p.shown()], [RELOAD, "review"]);

	// The server itself could not learn the outcome (Stripe never answered): it says Processing,
	// and the page goes to "being processed".
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.callback({ message: { stripe_payment: "SP-1", status: "Processing", requires_action: false, outcome_unknown: true } });
	await flush();
	check("an outcome the server could not learn goes to \"being processed\"", p.browser.replacedWith, "/stripe-return?status=success&sp=SP-1");

	// 3-D Secure abandoned by Back, then refused.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.callback({ message: { stripe_payment: "SP-1", requires_action: true, client_secret: "pi_secret" } });
	await flush();
	p.browser.back();
	await p.browser.settle();
	check("Back during 3-D Secure interrupts nothing", p.shown(), "review");
	p.stripe.nextAction({ error: AUTH_FAILED });
	await p.browser.settle();
	check("...and once it fails, the card step shows why", [p.shown(), p.$("card-error").textContent, p.browser.index], ["card", "Authentication failed.", 1]);

	// 3-D Secure refused with no Back: its PaymentIntent is spent, so the card step says why.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.callback({ message: { stripe_payment: "SP-1", requires_action: true, client_secret: "pi_secret" } });
	await flush();
	p.stripe.nextAction({ error: AUTH_FAILED });
	await p.browser.settle();
	check("3-D Secure refused, no Back: the card step says why", [p.shown(), p.$("card-error").textContent], ["card", "Authentication failed."]);
	check("...off the review's entry", [p.browser.index, p.browser.left], [1, null]);
	p.$("pay-btn").click();
	await flush();
	check("...and Pay can never resend the spent quote (SP-1, ctok_1)", p.take("portal_confirm_card_payment"), null);
	p.browser.forward();
	await p.browser.settle();
	check("...nor can Forward bring its review back", [p.shown(), p.browser.index], ["card", 1]);

	// 3-D Secure whose answer says nothing about the bank's: Stripe unreachable, a rate limit, a
	// type the page has never heard of, none at all. Whether the bank approved it is unknown.
	for (const type of ["api_connection_error", "api_error", "rate_limit_error", "some_new_error", undefined]) {
		p = loadPayCard();
		await p.continueWith(CREDIT);
		confirm = await p.pay();
		confirm.callback({ message: { stripe_payment: "SP-1", requires_action: true, client_secret: "pi_secret" } });
		await flush();
		p.stripe.nextAction({ error: { type, message: "Network error." } });
		await p.browser.settle();
		check(`3-D Secure that ended in ${type}: the server is asked, no card step`, [p.browser.replacedWith, p.shown()], [RELOAD, "review"]);
	}

	// Anything that throws after the server's answer, outside the handler's own try blocks — here
	// Stripe.js resolving 3-D Secure with nothing at all — still ends the charge: the chain's last
	// catch asks the server. Without it the rejection is lost and Pay waits for good.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.callback({ message: { stripe_payment: "SP-1", requires_action: true, client_secret: "pi_secret" } });
	await flush();
	p.stripe.nextAction(undefined);
	await p.browser.settle();
	check("a throw after the answer (3-D Secure resolving nothing): the server is asked, no card step", [p.browser.replacedWith, p.shown(), p.$("pay-btn").textContent], [RELOAD, "review", "Checking your payment…"]);

	// A card error after 3-D Secure is the bank's definite no: the card step, and why.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.callback({ message: { stripe_payment: "SP-1", requires_action: true, client_secret: "pi_secret" } });
	await flush();
	p.stripe.nextAction({ error: { type: "card_error", message: "Your card was declined." } });
	await p.browser.settle();
	check("3-D Secure that ended in a card_error: the card step says why", [p.shown(), p.$("card-error").textContent, p.browser.replacedWith], ["card", "Your card was declined.", null]);

	// Refused for the invoice, not the card — another tab's charge, autopay holding it, an
	// emailed link just paid, the invoice paid or credited meanwhile: nothing was charged, and
	// there must be no live card form beside "already being processed". The page asks the server,
	// which renders "being processed" / "received" / "paid" instead.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(BLOCKED);
	await p.browser.settle();
	check("Pay refused for the invoice (PaymentBlocked): the page asks the server again", p.browser.replacedWith, RELOAD);
	check("...never showing the card step", [p.shown(), p.$("pay-btn").disabled], ["review", true]);
	p.$("pay-btn").disabled = false; // even if it were not
	p.$("pay-btn").click();
	await flush();
	check("...and the quote is never sent again", p.take("portal_confirm_card_payment"), null);

	// An emailed link Stripe could not be asked to close: a definite refusal with no state to
	// render, so the card step, saying why; nothing was charged, and Continue tries again.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(LINK_OPEN);
	await p.browser.settle();
	check("a link that could not be closed: the card step, no reload", [p.shown(), p.browser.replacedWith, p.$("pay-btn").disabled], ["card", null, false]);
	check("...saying why", p.$("card-error").textContent, "A payment link for this invoice is still open.");

	// An earlier attempt Stripe would not confirm canceled: the same. Reloading used to land on a
	// card form with no message (the render never releases it), and every tap looped; now the form
	// stays, says "could not be released just now", and Continue tries again.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(UNRELEASED);
	await p.browser.settle();
	check("an attempt that could not be released, at Pay: the card step, no reload", [p.shown(), p.browser.replacedWith, p.$("pay-btn").disabled, p.$("continue-btn").disabled], ["card", null, false, false]);
	check("...saying why", p.$("card-error").textContent, "An earlier card payment attempt for this invoice could not be released just now.");
	p = loadPayCard();
	let refused = await p.continueWith(null);
	refused.error(UNRELEASED);
	await p.browser.settle();
	check("...and at Continue: the card step stays, Continue usable, no reload", [p.browser.replacedWith, p.shown(), p.$("continue-btn").disabled], [null, "card", false]);
	check("...saying why", p.$("card-error").textContent, "An earlier card payment attempt for this invoice could not be released just now.");
	await p.continueWith(DEBIT);
	check("...and the next Continue prices a quote as usual", [p.shown(), p.$("card-error").textContent], ["review", ""]);

	// The same refusal at Continue (the quote): the page asks the server rather than leave a
	// form for an invoice that can no longer be paid here. A refusal about the card does not.
	p = loadPayCard();
	let quoting = await p.continueWith(null);
	quoting.error(BLOCKED);
	await p.browser.settle();
	check("Continue refused for the invoice: the page asks the server again", p.browser.replacedWith, RELOAD);
	p = loadPayCard();
	quoting = await p.continueWith(null);
	quoting.error({ exc_type: "ValidationError", _server_messages: '["This page accepts card payments only."]' });
	await p.browser.settle();
	check("Continue refused for the card: the card step stays, Continue usable", [p.browser.replacedWith, p.shown(), p.$("continue-btn").disabled], [null, "card", false]);
	check("...saying why", p.$("card-error").textContent, "This page accepts card payments only.");
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.browser.back();
	await p.browser.settle();
	quoting = await p.continueWith(null);
	p.browser.forward(); // the payer moved on while it was priced
	await p.browser.settle();
	quoting.error(BLOCKED);
	await p.browser.settle();
	check("...and a refusal that lands after the payer moved on is dropped", [p.browser.replacedWith, p.$("continue-btn").disabled], [null, false]);

	// A price that lands after the payer moved on.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.browser.back();
	await p.browser.settle();
	const pending = await p.continueWith(null); // a new Continue: SP-1's review is done with
	p.browser.forward();
	await p.browser.settle();
	check("Forward onto a review a new Continue voided lands on the card step", [p.shown(), p.browser.index], ["card", 1]);
	const before = p.pushes();
	pending.callback({ message: DEBIT });
	await flush();
	check("a price that lands after a Back or Forward is dropped", [p.shown(), p.pushes() - before], ["card", 0]);
	check("...and Continue works again", p.$("continue-btn").disabled, false);

	// ------------------------------------------------------------------ over fetch, not frappe.call

	// The failure production hit: a declined card after Pay. The live page has the website build
	// of frappe.call on window (so does this one), and it never calls error() — a 417 left Pay on
	// "Please wait…", Back held by the charge, and nothing said. The page talks to the server over
	// fetch, which answers a 417 like any other response.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	check("Pay goes over fetch, never the website's frappe.call", [!!confirm, p.frappeCalls.length], [true, 0]);
	confirm.respond(417, STRIPE_DECLINED);
	await p.browser.settle();
	check("a 417 decline after Pay returns to the card step", [p.shown(), p.browser.index, p.browser.left, p.browser.replacedWith], ["card", 1, null, null]);
	check("...with Stripe's message, as frappe serialised it", p.$("card-error").textContent, STRIPE_DECLINED_MESSAGE);
	check("...Pay and Back usable again, Pay no longer waiting", [p.$("pay-btn").disabled, p.$("back-btn").disabled, p.$("pay-btn").textContent], [false, false, "Pay $205.80"]);
	p.browser.forward();
	await p.browser.settle();
	check("...Forward never brings the spent review back", [p.shown(), p.browser.index], ["card", 1]);
	await p.continueWith(DEBIT);
	check("...and Continue works again", [p.shown(), p.$("card-error").textContent, p.$("pay-btn").textContent], ["review", "", "Pay $200.00"]);
	confirm = await p.pay();
	check("...pricing a new quote that Pay sends", confirm && confirm.args, { stripe_payment: "SP-2", confirmation_token: "ctok_2" });
	check("nothing ever went through frappe.call", p.frappeCalls.length, 0);

	// A refusal with markup in it reads as text; one with no message still says something.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.respond(417, { exc_type: "ValidationError", _server_messages: serverMessages("<b>Declined</b><br>Try another card &amp; again.") });
	await p.browser.settle();
	check("a refusal's markup is stripped", p.$("card-error").textContent, "Declined Try another card & again.");
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.respond(417, { exc_type: "ValidationError" });
	await p.browser.settle();
	check("a refusal with no message: the card step, and a word of why", [p.shown(), p.$("card-error").textContent], ["card", "The payment did not go through. Nothing was charged."]);
	// A msgprint earlier in the request, then the throw: frappe lists both, in order, and the
	// throw — the refusal itself — is the last. At Pay and at Continue.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.respond(417, { exc_type: "ValidationError", _server_messages: serverMessages("Checking the card on file.", STRIPE_DECLINED_MESSAGE) });
	await p.browser.settle();
	check("two messages (a msgprint, then the throw): the throw's is shown", p.$("card-error").textContent, STRIPE_DECLINED_MESSAGE);
	p = loadPayCard();
	quoting = await p.continueWith(null);
	quoting.respond(417, { exc_type: "ValidationError", _server_messages: serverMessages("Checking the card on file.", "This page accepts card payments only.") });
	await p.browser.settle();
	check("...at Continue too", p.$("card-error").textContent, "This page accepts card payments only.");

	// A decline at Continue (the quote) says why under the form.
	p = loadPayCard();
	quoting = await p.continueWith(null);
	check("Continue goes over fetch too", [!!quoting, p.frappeCalls.length], [true, 0]);
	quoting.respond(417, { exc_type: "ValidationError", _server_messages: serverMessages("Your card does not support this type of purchase.") });
	await p.browser.settle();
	check("a decline at Continue shows the server's message", [p.shown(), p.$("card-error").textContent, p.$("continue-btn").disabled, p.$("continue-btn").textContent], ["card", "Your card does not support this type of purchase.", false, "Continue"]);
	await p.continueWith(CREDIT);
	check("...and the next Continue clears it", [p.shown(), p.$("card-error").textContent], ["review", ""]);

	// No answer at Continue: a quote moves no money, so the form stays and says to try again.
	for (const [label, answer] of [
		["a dropped connection", (c) => c.drop()],
		["a 500", (c) => c.respond(500, { exc_type: "Exception" })],
		["a proxy's 502 page", (c) => c.error()],
		["a 200 with no quote", (c) => c.callback({})],
		["no answer within the timeout", (c, pg) => pg.timers.fire()],
	]) {
		p = loadPayCard();
		quoting = await p.continueWith(null);
		await answer(quoting, p);
		await p.browser.settle();
		check(`${label} at Continue: the card step says to try again`, [p.shown(), p.$("card-error").textContent, p.$("continue-btn").disabled, p.browser.replacedWith], ["card", "Your card could not be checked just now. Please tap Continue again.", false, null]);
		if (label.startsWith("no answer within the timeout")) {
			check("...abandoning the request", quoting.aborted, true);
		}
		quoting.callback({ message: DEBIT });
		await flush();
		check("...and a late price changes nothing", [p.shown(), p.pushes()], ["card", 0]);
	}

	// Stripe.js failing outright at Continue: never a button stuck on "Please wait…".
	p = loadPayCard();
	p.stripe.failTokens = true;
	p.$("continue-btn").click();
	await flush();
	check("Stripe.js throwing at Continue: the card step says to try again", [p.$("card-error").textContent, p.$("continue-btn").disabled, p.calls.length], ["Your card could not be checked just now. Please tap Continue again.", false, 0]);

	// The Element's change event after the review is drawn (the one suspected when the owner saw
	// Pay do nothing at all). Pay charges the quote the review was drawn for — even when the
	// page's latest quote has been set aside, which the handler's own guard normally prevents.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.editCard();
	confirm = await p.pay();
	check("a change event after the review is drawn: Pay still charges its quote", confirm && confirm.args, { stripe_payment: "SP-1", confirmation_token: "ctok_1" });
	p = loadPayCard();
	await p.continueWith(CREDIT);
	p.$("step-card").style.display = ""; // defeat the handler's guard, so it sets the quote aside
	p.editCard();
	p.$("step-card").style.display = "none";
	confirm = await p.pay();
	check("...even when that event set the page's quote aside", confirm && confirm.args, { stripe_payment: "SP-1", confirmation_token: "ctok_1" });
	check("...and Pay said it was working", [p.$("pay-btn").textContent, p.$("pay-btn").disabled], ["Please wait…", true]);

	// A review on screen with no quote behind it (which show_review never draws): the tap is
	// answered, never ignored in silence, and nothing is charged.
	p = loadPayCard();
	p.$("step-card").style.display = "none";
	p.$("step-review").style.display = "";
	p.$("pay-btn").click();
	await flush();
	check("Pay with no quote behind the review says what to do", p.$("review-error").textContent, "Please tap Back and Continue again.");
	check("...charges nothing, and leaves Pay usable", [p.take("portal_confirm_card_payment"), p.$("pay-btn").disabled], [null, false]);
	p.$("step-card").style.display = "";
	p.$("step-review").style.display = "none";
	await p.continueWith(CREDIT);
	check("...and a review drawn afterwards clears it", [p.shown(), p.$("review-error").textContent], ["review", ""]);

	// What is sent: frappe's REST route, the session's CSRF token, JSON back, form-encoded out.
	p = loadPayCard();
	quoting = await p.continueWith(null);
	check("Continue posts to /api/method/", quoting.url, "/api/method/erpnext_enhancements.stripe_payments.core.api.portal_price_card_payment");
	check("...a same-origin POST", [quoting.init.method, quoting.init.credentials], ["POST", "same-origin"]);
	check("...carrying the CSRF token and asking for JSON", [quoting.headers["X-Frappe-CSRF-Token"], quoting.headers.Accept], ["tok", "application/json"]);
	check("...form-encoded", [quoting.headers["Content-Type"], quoting.args], ["application/x-www-form-urlencoded; charset=UTF-8", { sales_invoice: "ACC-SINV-0001", confirmation_token: "ctok_1" }]);
	check("...giving up after a minute", p.timers.pending(), [60000]);
	quoting.callback({ message: CREDIT });
	await flush();
	check("...a timer cleared once answered", p.timers.pending(), []);
	confirm = await p.pay();
	check("Pay posts to /api/method/ with the CSRF token", [confirm.url, confirm.init.method, confirm.init.credentials, confirm.headers["X-Frappe-CSRF-Token"], confirm.headers.Accept], ["/api/method/erpnext_enhancements.stripe_payments.core.api.portal_confirm_card_payment", "POST", "same-origin", "tok", "application/json"]);
	// A backstop only: a proxy normally answers a slow charge with a 504 first, and what stops a
	// second charge is the server's Processing row, invoice lock and idempotency key.
	check("...its backstop timer set past the usual 120 s proxy limit, and within five minutes", p.timers.pending().length === 1 && p.timers.pending()[0] > 120000 && p.timers.pending()[0] <= 300000, true);

	// ------------------------------------------------------------------ a session the page lost

	// Refusals frappe makes before any handler runs, the same for every call from this page until
	// a render gives it a new session: a stale CSRF token (the payer signed in again in another
	// tab), and a sign-in that expired or was ended elsewhere, which makes the call a Guest's and
	// has is_whitelisted refuse the method by name. Shown under the form they came back on every
	// Continue; the page reloads instead, for a new token or the login page. Nothing was charged,
	// so at Pay the reload is safe as well — the same one as for no answer.
	const PRICE_METHOD = "erpnext_enhancements.stripe_payments.core.api.portal_price_card_payment";
	const CONFIRM_METHOD = "erpnext_enhancements.stripe_payments.core.api.portal_confirm_card_payment";
	// frappe v16 is_whitelisted, word for word, for a Guest calling a method that is not allow_guest.
	const guestRefusal = (method) => ({
		exc_type: "PermissionError",
		_server_messages: serverMessages(
			`<details><summary>You are not permitted to access this resource. Login to access</summary>Function <strong>${method}</strong> is not whitelisted.</details>`
		),
	});
	for (const [label, status, body] of [
		["a stale CSRF token (400 CSRFTokenError)", 400, { exc_type: "CSRFTokenError", _server_messages: serverMessages("Invalid Request") }],
		["an expired sign-in (403, session_expired)", 403, { exc_type: "PermissionError", session_expired: 1 }],
		["a Guest refused the method by is_whitelisted", 403, null],
		["SessionExpired", 401, { exc_type: "SessionExpired" }],
	]) {
		p = loadPayCard();
		quoting = await p.continueWith(null);
		quoting.respond(status, body || guestRefusal(PRICE_METHOD));
		await p.browser.settle();
		check(`${label} at Continue: the page loads again`, [p.browser.replacedWith, p.shown(), p.pushes(), p.$("card-error").textContent], [RELOAD, "card", 0, ""]);
		p = loadPayCard();
		await p.continueWith(CREDIT);
		confirm = await p.pay();
		confirm.respond(status, body || guestRefusal(CONFIRM_METHOD));
		await p.browser.settle();
		check("...and at Pay, never a card form", [p.browser.replacedWith, p.shown(), p.$("pay-btn").textContent, p.$("card-error").textContent], [RELOAD, "review", "Checking your payment…", ""]);
	}
	// The endpoints' own PermissionError is not a lost session: it names no method, so it is an
	// ordinary refusal and says why, rather than a reload that could come back with no word of it.
	p = loadPayCard();
	quoting = await p.continueWith(null);
	quoting.respond(403, { exc_type: "PermissionError", _server_messages: serverMessages("You can only pay your own invoices.") });
	await p.browser.settle();
	check("the endpoint's own PermissionError at Continue: the card step says why", [p.browser.replacedWith, p.shown(), p.$("card-error").textContent], [null, "card", "You can only pay your own invoices."]);
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.respond(403, { exc_type: "PermissionError", _server_messages: serverMessages("You can only pay your own invoices.") });
	await p.browser.settle();
	check("...and at Pay", [p.browser.replacedWith, p.shown(), p.$("card-error").textContent], [null, "card", "You can only pay your own invoices."]);
	// A Guest refusal naming a different method is not an answer about this call.
	p = loadPayCard();
	quoting = await p.continueWith(null);
	quoting.respond(403, guestRefusal("frappe.client.get_list"));
	await p.browser.settle();
	check("...nor is one naming another method", [p.browser.replacedWith, p.shown()], [null, "card"]);

	// Reload on the review step; back-forward cache.
	p = loadPayCard({ state: { payCard: "review" } });
	check("a reload on the review entry re-stamps it as the card step", [p.shown(), p.browser.history.state], ["card", { payCard: "card" }]);
	p.browser.fire("pageshow", { persisted: false });
	check("an ordinary pageshow reloads nothing", p.browser.reloads, 0);
	p.browser.fire("pageshow", { persisted: true });
	check("a back-forward-cache restore asks the server again", p.browser.reloads, 1);

	check("every push was paid for by a tap", p.browser.unactivated, 0);
}

// ---------------------------------------------------------------------------- /itinerary

// A date on this machine's calendar, as the page now reads "today" (not UTC).
function iso(offset) {
	const d = new Date();
	d.setDate(d.getDate() + offset);
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The boot list: trips they travel on, plus one they own but are not on (Trip O, on right now
// too, so the default has to prefer Trip A for being theirs).
const TRIPS = [
	{ name: "TRIP-C", purpose: "Trip C", start_date: iso(-30), end_date: iso(-20), mine: true },
	{ name: "TRIP-A", purpose: "Trip A", start_date: iso(-1), end_date: iso(1), mine: true },
	{ name: "TRIP-O", purpose: "Trip O", start_date: iso(0), end_date: iso(2), mine: false },
	{ name: "TRIP-B", purpose: "Trip B", start_date: iso(10), end_date: iso(12), mine: true },
];

const PEOPLE = { "EMP-1": "Pat", "EMP-2": "Sam", "EMP-3": "Alex", "EMP-4": "Dana" };

// Trip A's files (Trip Document rows): `traveler` "" is everyone on the booking, or the whole
// crew when `group` is "" too (a trip-wide file). The trip-wide site map is listed LAST here, so
// the Documents screen is seen to put "For the whole trip" first whatever order it is sent in.
const TRIP_A_FILES = [
	{ name: "TD-PASS-PAT", title: "Pat's boarding pass", kind: "Boarding pass", file: "/private/files/pat-pass.png", traveler: "EMP-1", group: "g1" },
	{ name: "TD-PASS-SAM", title: "", kind: "Boarding pass", file: "/private/files/sam-pass.jpg", traveler: "EMP-2", group: "g1" },
	{ name: "TD-CONF", title: "Southwest confirmation", kind: "Booking confirmation", file: "/private/files/wn1-confirmation.pdf", traveler: "", group: "g1" },
	{ name: "TD-MAP", title: "Site map", kind: "Site map", file: "/private/files/site-map.pdf", traveler: "", group: "" },
];
const GROUP_PEOPLE = { g1: ["EMP-1", "EMP-2"] };
const GROUP_LABELS = { g1: "Southwest WN 1" };
const RECEIPT = "/private/files/wn1-receipt.pdf";

// A Trip Document as get_trip_itinerary sends it (the contract's DOC).
function tripDoc(f) {
	const fileName = f.file.split("/").pop();
	return {
		name: f.name,
		title: f.title || fileName,
		kind: f.kind,
		url: f.file,
		file_name: fileName,
		is_image: /\.(png|jpe?g|gif|webp|heic)$/i.test(fileName),
		for_name: f.traveler ? PEOPLE[f.traveler] : null,
		for_employee: f.traveler || null,
		group: f.group || null,
		booking_label: f.group ? GROUP_LABELS[f.group] : null,
	};
}

// The contract's visibility rule: the whole crew sees every file; one person sees theirs, and a
// file for everyone when it is trip-wide or on a booking they are on. `group` narrows to one
// booking's files.
function visibleFiles(viewing, group) {
	return TRIP_A_FILES.filter(
		(f) =>
			(group === undefined || f.group === group) &&
			(!viewing || f.traveler === viewing || (!f.traveler && (!f.group || GROUP_PEOPLE[f.group].includes(viewing))))
	).map(tripDoc);
}

// A shared flight: one booking, one row (and one PNR) per person, with its files. `attachment`
// is its receipt, which the server no longer sends: carried here as a stale answer would.
function sharedFlight(viewing) {
	const rows = [
		{ employee: "EMP-1", ref: "PNR-PAT" },
		{ employee: "EMP-2", ref: "PNR-SAM" },
	];
	const base = { type: "flight", date: iso(0), sort_time: "", airline: "Southwest", flight_number: "WN 1", departure_airport: "PHX", arrival_airport: "LAS", group: "g1", attachment: RECEIPT, documents: visibleFiles(viewing, "g1") };
	if (!viewing) {
		const members = rows.map((r) => ({ employee: r.employee, employee_name: PEOPLE[r.employee], ref: r.ref }));
		return [Object.assign({}, base, { booking_reference: "PNR-PAT, PNR-SAM", travelers: ["Pat", "Sam"], members, whole_crew: false })];
	}
	const own = rows.find((r) => r.employee === viewing);
	return own ? [Object.assign({}, base, { booking_reference: own.ref, travelers: null })] : [];
}

// What the server knows. TRIP-X is readable but in nobody's boot list here (a coordinator's
// link); TRIP-SECRET is someone else's (403); anything else is gone (404).
const SERVER_TRIPS = {
	"TRIP-A": { crew: ["EMP-1", "EMP-2", "EMP-3"], days: (viewing) => {
		const items = sharedFlight(viewing);
		return items.length ? [{ date: iso(0), items }] : [];
	}, documents: (viewing) => visibleFiles(viewing) },
	// A room Pat pays for and Sam shares as a guest for one night of it.
	"TRIP-H": { purpose: "Trip H", start_date: iso(0), end_date: iso(2), crew: ["EMP-1", "EMP-2"], days: (viewing) => viewing ? [] : [
		{ date: iso(0), items: [{
			type: "hotel_checkin", date: iso(0), sort_time: "23:00", hotel: "Hampton Inn", address: "", booking_confirmation: "H-1", travelers: ["Pat", "Sam"], group: "h1", whole_crew: false,
			members: [
				{ employee: "EMP-1", employee_name: "Pat", ref: "H-1", guest: false },
				{ employee: "EMP-2", employee_name: "Sam", ref: "H-1", guest: true, check_in_date: "2026-09-28", check_out_date: "2026-09-29" },
			],
		}] },
	] },
	"TRIP-B": { crew: ["EMP-1", "EMP-2"] },
	"TRIP-C": { crew: ["EMP-1"] },
	"TRIP-O": { crew: ["EMP-2", "EMP-3"] },
	"TRIP-X": { purpose: "Trip X", start_date: iso(3), end_date: iso(5), crew: ["EMP-4"] },
	"TRIP-P1": { purpose: "Ends today", start_date: "2026-09-23", end_date: "2026-09-25", crew: ["EMP-1"], days: () => [
		{ date: "2026-09-25", items: [] },
		{ date: "2026-09-26", items: [] },
	] },
	"TRIP-P2": { purpose: "Starts tomorrow", start_date: "2026-09-26", end_date: "2026-09-28", crew: ["EMP-1"] },
};

function refusal(status, excType, message) {
	return { status, body: { exc_type: excType, _server_messages: JSON.stringify([JSON.stringify({ message })]) } };
}

// get_trip_itinerary(trip, as_employee), to the contract: read permission, then '' = the
// viewer when on the crew else the whole crew, 'crew' = the whole crew, an employee = that
// person or a ValidationError (417).
function serve(trip, as, viewer) {
	if (trip === "TRIP-SECRET") return refusal(403, "PermissionError", "Not permitted");
	const t = SERVER_TRIPS[trip];
	if (!t) return refusal(404, "DoesNotExistError", `Travel Trip ${trip} not found`);
	const listed = TRIPS.find((x) => x.name === trip) || {};
	const meta = { trip, purpose: t.purpose || listed.purpose, status: "Booked", start_date: t.start_date || listed.start_date, end_date: t.end_date || listed.end_date };
	const onTrip = !!viewer && t.crew.includes(viewer);
	let viewing;
	if (!as) viewing = onTrip ? viewer : null;
	else if (as === "crew") viewing = null;
	else if (t.crew.includes(as)) viewing = as;
	else return refusal(417, "ValidationError", `${PEOPLE[as] || as} is not on this trip.`);
	const crew = t.crew.map((e) => ({ employee: e, employee_name: PEOPLE[e], from_date: meta.start_date, to_date: meta.end_date, is_trip_lead: 0 }));
	const message = Object.assign({ days: t.days ? t.days(viewing) : [] }, meta, { crew, viewing, viewer_employee: viewer || null, viewer_on_trip: onTrip });
	// Only a trip with files sends `documents`: the page must manage without the key.
	if (t.documents) message.documents = t.documents(viewing);
	return { status: 200, body: { message } };
}

// A phone in Arizona (UTC-7, no daylight time) at 8:30 PM on Sep 25, which is already 03:30 on
// Sep 26 in UTC. Local-calendar getters answer in Arizona whatever this machine's zone is.
function arizonaEvening() {
	const RealDate = Date;
	const NOW = RealDate.UTC(2026, 8, 26, 3, 30);
	const shifted = (d) => new RealDate(d.getTime() - 7 * 3600e3);
	return class ArizonaDate extends RealDate {
		constructor(...args) {
			if (args.length) super(...args);
			else super(NOW);
		}
		getFullYear() {
			return shifted(this).getUTCFullYear();
		}
		getMonth() {
			return shifted(this).getUTCMonth();
		}
		getDate() {
			return shifted(this).getUTCDate();
		}
	};
}

// What frappe v16 answers a request whose session has expired: it carries on as Guest
// (sessions.py sets frappe.response["session_expired"]), Guest may not call the method, so
// is_whitelisted throws PermissionError — a 403, with the flag in the body.
function sessionExpired() {
	return { status: 403, body: Object.assign({ session_expired: 1 }, refusal(403, "PermissionError", "Not permitted").body) };
}

function loadItinerary(url, opts) {
	opts = opts || {};
	const employee = "employee" in opts ? opts.employee : "EMP-1";
	const env = {};
	const browser = makeBrowser(url, { prevPath: "/desk", state: opts.state, behind: opts.behind });
	env.browser = browser;
	const document = makeDocument(env, { "itinerary-root": {} });
	// The picture viewer is drawn into <body>, beside the root.
	document.body = new El("body", env);
	document.body.appendChild(document.byId["itinerary-root"]);
	// frappe's readable cookies (a real browser always has a string here; the stand-in DOM has
	// none unless a test gives it one).
	if ("cookie" in opts) document.cookie = opts.cookie;
	const fetches = [];
	const capture = { open: false };
	// `expired`: every answer is the one an expired session gets. `next`: the next answer is
	// this one, whatever the fake server would have said.
	const session = { expired: false, next: null };
	const win = browser.window;
	Object.assign(win, {
		window: win,
		document,
		history: browser.history,
		ITIN_BOOT: { trips: opts.trips || TRIPS, employee, employee_name: employee ? PEOPLE[employee] : null },
		ITIN_CSRF: "tok",
		ee_capture: { isOpen: () => capture.open },
		fetch(u, o) {
			const body = JSON.parse(o.body);
			return new Promise((resolve, reject) => {
				fetches.push({ trip: body.trip, as: body.as_employee || null, resolve, reject });
			});
		},
		URLSearchParams,
		navigator: {},
		console,
		setTimeout,
		clearTimeout,
	});
	if (opts.Date) win.Date = opts.Date;
	const source = fs.readFileSync(path.join(APP, "public", "js", "travel", "itinerary.js"), "utf8");
	runInPage(source, win, "itinerary.js");
	const root = document.byId["itinerary-root"];
	const texts = (cls) => root.find(cls).map((e) => e.textContent);
	const page = {
		browser,
		document,
		fetches,
		capture,
		root,
		// Answer the oldest request for `trip` (and, when given, that `as`): `ok === false` is no
		// answer at all (offline); otherwise the fake server decides, refusals included.
		async answer(trip, ok, as) {
			const i = fetches.findIndex((f) => f.trip === trip && (as === undefined || f.as === as));
			if (i === -1) {
				check(`a request for ${trip}${as === undefined ? "" : ` as ${as}`} is waiting to be answered`, page.asked(), [[trip, as === undefined ? "?" : as]]);
				return;
			}
			const f = fetches.splice(i, 1)[0];
			if (ok === false) f.reject(new Error("offline"));
			else {
				let r = session.expired ? sessionExpired() : serve(f.trip, f.as, employee);
				if (session.next) {
					r = session.next;
					session.next = null;
				}
				f.resolve({ ok: r.status === 200, status: r.status, json: async () => r.body });
			}
			await flush();
		},
		session,
		shown() {
			const el = root.find("ti-trip-purpose")[0];
			return el ? el.textContent : null;
		},
		async tap(purpose) {
			const chip = root.find("ti-trip-chip").find((c) => c.find("ti-chip-title")[0].textContent === purpose);
			chip.click();
			await flush();
		},
		async pick(label) {
			const chip = root.find("ti-person-chip").find((c) => c.textContent === label);
			if (!chip) {
				check(`the picker offers ${label}`, page.people(), [label]);
				return;
			}
			chip.click();
			await flush();
		},
		trip: () => new URL(browser.location.href).searchParams.get("trip"),
		as: () => new URL(browser.location.href).searchParams.get("as"),
		pending: () => fetches.map((f) => f.trip),
		asked: () => fetches.map((f) => [f.trip, f.as]),
		errors: () => root.find("ti-error").length,
		title: () => texts("ti-header-title")[0],
		people: () => texts("ti-person-chip"),
		picked: () => root.find("ti-person-chip").filter((c) => c.classList.contains("active")).map((c) => c.textContent),
		chips: () => root.find("ti-trip-chip"),
		activeChips: () => root.find("ti-trip-chip").filter((c) => c.classList.contains("active")).map((c) => c.find("ti-chip-title")[0].textContent),
		members: () => texts("ti-member"),
		empty: () => texts("ti-empty"),
		todays: () => root.find("ti-day").map((s) => s.classList.contains("today")),
		has: (text) => root.textContent.includes(text),
		text: texts,
		urls: () => browser.calls.map((c) => `${c.kind} ${c.url}`),
		view: () => new URL(browser.location.href).searchParams.get("view"),
		file: () => new URL(browser.location.href).searchParams.get("file"),
		// The screen tabs ("Day by day", "Documents (N)"), and a tap on the one starting `label`.
		screens: () => texts("ti-screen-tab"),
		onScreen: () => root.find("ti-screen-tab").filter((c) => c.classList.contains("active")).map((c) => c.textContent),
		async screen(label) {
			const tab = root.find("ti-screen-tab").find((c) => c.textContent.startsWith(label));
			if (!tab) {
				check(`the page offers the ${label} screen`, page.screens(), [label]);
				return;
			}
			tab.click();
			await flush();
		},
		days: () => root.find("ti-day").length,
		docs: () => texts("ti-doc-title"),
		docSubs: () => texts("ti-doc-sub"),
		docGroups: () => texts("ti-doc-group-title"),
		// A file's link, by its title as drawn.
		doc: (title) => root.find("ti-doc").find((a) => a.find("ti-doc-title")[0].textContent === title) || null,
		// Every link on the page, anywhere.
		hrefs: () => {
			const out = [];
			const walk = (n) => n.children.forEach((c) => {
				if (c.href) out.push(c.href);
				walk(c);
			});
			walk(root);
			return out;
		},
		viewer: () => document.body.find("ti-viewer")[0] || null,
		viewerTitle: () => {
			const v = document.body.find("ti-viewer")[0];
			return v ? v.find("ti-viewer-title")[0].textContent : null;
		},
		async closeViewer() {
			document.body.find("ti-viewer-close")[0].click();
			await flush();
		},
		focused: () => env.focused || null,
		async key(name) {
			document.dispatch("keydown", { key: name });
			await flush();
		},
	};
	return page;
}

async function testItinerary() {
	console.log("/itinerary");
	const source = fs.readFileSync(path.join(APP, "public", "js", "travel", "itinerary.js"), "utf8");
	check("no beforeunload trap", /beforeunload/.test(source), false);

	let p = loadItinerary("/itinerary");
	check("boot with no ?trip= replaces the entry with the default trip", p.browser.calls, [{ kind: "replace", argc: 3, url: "/itinerary?trip=TRIP-A" }]);
	check("...a trip they travel on, not the one they only own (on now too)", p.trip(), "TRIP-A");
	check("...and loads it", p.pending(), ["TRIP-A"]);
	await p.answer("TRIP-A");
	check("...and shows it", p.shown(), "Trip A");
	check("a trip they own but are not on is a chip too, marked as such", [p.chips().length, p.text("ti-chip-note")], [4, ["Not traveling"]]);
	p.browser.back();
	await p.browser.settle();
	check("Back from the first entry leaves the page", p.browser.left, ORIGIN + "/desk");

	p = loadItinerary("/itinerary", { trips: [TRIPS[2], TRIPS[3]] });
	check("the default prefers their own upcoming trip over one they only own that is on now", p.trip(), "TRIP-B");

	p = loadItinerary("/itinerary?trip=TRIP-B");
	check("boot with a valid ?trip= touches no history", p.browser.calls, []);
	check("...and loads that trip", p.pending(), ["TRIP-B"]);

	// Re-specified with ?as= (was: an unknown ?trip= replaced on sight). A trip outside the
	// list may be one they can read — the server decides — so it is asked for, and only a
	// refusal replaces the entry.
	p = loadItinerary("/itinerary?trip=TRIP-GONE&x=1#top");
	check("an unknown ?trip= is asked for, not replaced on sight", [p.browser.calls, p.pending()], [[], ["TRIP-GONE"]]);
	await p.answer("TRIP-GONE");
	check("...and once the server refuses it, the entry is replaced with the default trip, keeping the rest of the address", p.urls(), ["replace /itinerary?trip=TRIP-A&x=1#top"]);
	check("...which loads", p.pending(), ["TRIP-A"]);
	await p.answer("TRIP-A");
	check("...and shows, with no error", [p.shown(), p.errors()], ["Trip A", 0]);

	p = loadItinerary("/itinerary?trip=TRIP-SECRET&as=EMP-2");
	await p.answer("TRIP-SECRET", undefined, "EMP-2");
	check("someone else's trip (403) falls back the same way, dropping ?as=", [p.urls(), p.asked()], [["replace /itinerary?trip=TRIP-A"], [["TRIP-A", null]]]);

	p = loadItinerary("/itinerary?trip=TRIP-SECRET", { trips: [] });
	await p.answer("TRIP-SECRET");
	check("a refused trip with none of their own to fall back on says so, in place", [p.empty(), p.browser.calls, p.errors()], [["You don't have access to this trip, or it no longer exists."], [], 0]);

	p = loadItinerary("/itinerary?trip=TRIP-X");
	check("a ?trip= that is not in their list is asked for, with no history call", [p.browser.calls, p.pending()], [[], ["TRIP-X"]]);
	await p.answer("TRIP-X");
	check("...and shown when the server answers", [p.shown(), p.browser.calls.length, p.errors()], ["Trip X", 0, 0]);
	check("...as the whole crew, since they are not on it", [p.title(), p.people(), p.picked()], ["Whole crew", ["Whole crew", "Dana"], ["Whole crew"]]);
	check("...with their own trips offered to go back to, none of them picked", [p.chips().length, p.activeChips()], [4, []]);

	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	check("a chip tap pushes ?trip=<name>", p.browser.calls.slice(1), [{ kind: "push", argc: 3, url: "/itinerary?trip=TRIP-B" }]);
	await p.answer("TRIP-B");
	check("...and shows that trip", p.shown(), "Trip B");
	await p.tap("Trip B");
	check("tapping the trip on screen reloads it without a new entry", [p.browser.calls.length, p.pending()], [2, ["TRIP-B"]]);
	await p.answer("TRIP-B");
	p.browser.back();
	await p.browser.settle();
	check("Back loads the previous trip", [p.trip(), p.pending()], ["TRIP-A", ["TRIP-A"]]);
	await p.answer("TRIP-A");
	check("...and shows it", p.shown(), "Trip A");
	p.browser.forward();
	await p.browser.settle();
	await p.answer("TRIP-B");
	check("Forward restores the next one", p.shown(), "Trip B");
	check("popstate never pushes or replaces", p.browser.calls.length, 2);

	// Stale responses.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	p.browser.back();
	await p.browser.settle();
	await p.answer("TRIP-A");
	await p.answer("TRIP-B");
	check("a response for a trip no longer on screen is dropped", p.shown(), "Trip A");
	await p.tap("Trip C");
	p.browser.back();
	await p.browser.settle();
	await p.answer("TRIP-C", false);
	check("...and so is its error", p.errors(), 0);
	await p.answer("TRIP-A");

	// Whose itinerary: the person picker, ?as=, and Back / Forward between people.
	p = loadItinerary("/itinerary");
	check("the default view names no one", p.asked(), [["TRIP-A", null]]);
	await p.answer("TRIP-A");
	check("the picker offers Me, the whole crew and everyone else on the trip", p.people(), ["Me", "Whole crew", "Sam", "Alex"]);
	check("...with Me picked, under 'My Itinerary'", [p.picked(), p.title(), p.document.title], [["Me"], "My Itinerary", "My Itinerary"]);
	check("...showing my own PNR only", [p.has("PNR: PNR-PAT"), p.has("PNR-SAM")], [true, false]);
	await p.pick("Sam");
	check("picking a person pushes one entry, ?trip=&as=", p.browser.calls.slice(1), [{ kind: "push", argc: 3, url: "/itinerary?trip=TRIP-A&as=EMP-2" }]);
	check("...carrying the trip and the person, and the view it was pushed from", p.browser.history.state, {
		itin_trip: "TRIP-A",
		itin_as: "EMP-2",
		itin_from: { trip: "TRIP-A", as: null },
	});
	check("...asks the server for that person (as_employee)", p.asked(), [["TRIP-A", "EMP-2"]]);
	check("...and keeps the picker up while it loads", [p.picked(), p.title()], [["Sam"], "Sam's itinerary"]);
	await p.answer("TRIP-A");
	check("...then shows their itinerary, with their own PNR", [p.title(), p.has("PNR: PNR-SAM"), p.has("PNR-PAT")], ["Sam's itinerary", true, false]);
	check("...and the page title says whose it is", p.document.title, "Sam's itinerary");
	await p.pick("Whole crew");
	check("the whole crew is ?as=crew", p.urls().slice(2), ["push /itinerary?trip=TRIP-A&as=crew"]);
	await p.answer("TRIP-A");
	check("...titled 'Whole crew'", p.title(), "Whole crew");
	check("...each person on a shared booking with their own number", p.members(), ["PatPNR: PNR-PATCopy", "SamPNR: PNR-SAMCopy"]);
	check("...rather than the names and numbers joined", [p.has("With:"), p.has("PNR-PAT, PNR-SAM")], [false, false]);
	await p.pick("Whole crew");
	check("tapping the person on screen reloads without a new entry", [p.browser.calls.length, p.asked()], [3, [["TRIP-A", "crew"]]]);
	await p.answer("TRIP-A");
	await p.tap("Trip A");
	check("tapping the trip on screen keeps the person", [p.browser.calls.length, p.asked()], [3, [["TRIP-A", "crew"]]]);
	await p.answer("TRIP-A");
	p.browser.back();
	await p.browser.settle();
	check("Back returns to the previous person, without a push", [p.as(), p.asked(), p.browser.calls.length], ["EMP-2", [["TRIP-A", "EMP-2"]], 3]);
	await p.answer("TRIP-A");
	check("...and shows them", p.title(), "Sam's itinerary");
	p.browser.back();
	await p.browser.settle();
	check("Back again returns to the default view", [p.as(), p.asked()], [null, [["TRIP-A", null]]]);
	await p.answer("TRIP-A");
	check("...which is mine", [p.title(), p.picked()], ["My Itinerary", ["Me"]]);
	p.browser.forward();
	await p.browser.settle();
	await p.answer("TRIP-A", undefined, "EMP-2");
	check("Forward restores the person", p.title(), "Sam's itinerary");
	await p.tap("Trip B");
	check("a trip chip drops ?as=: another trip opens on its default view", [p.urls().slice(-1), p.asked()], [["push /itinerary?trip=TRIP-B"], [["TRIP-B", null]]]);
	await p.answer("TRIP-B");
	check("...which is mine", p.title(), "My Itinerary");
	p.browser.back();
	await p.browser.settle();
	check("Back from it returns to the person on the previous trip", [p.trip(), p.as(), p.asked()], ["TRIP-A", "EMP-2", [["TRIP-A", "EMP-2"]]]);
	await p.answer("TRIP-A");
	check("...and popstate still never pushed or replaced", p.browser.calls.length, 4);
	check("every push was paid for by a tap", p.browser.unactivated, 0);

	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-2");
	check("?as= at boot touches no history", p.browser.calls, []);
	check("...and goes to the server as as_employee", p.asked(), [["TRIP-A", "EMP-2"]]);
	await p.answer("TRIP-A");
	check("...which shows that person", [p.title(), p.picked()], ["Sam's itinerary", ["Sam"]]);

	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-9");
	await p.answer("TRIP-A");
	check("someone not on the trip: the entry is replaced with the trip's default view", [p.urls(), p.asked()], [["replace /itinerary?trip=TRIP-A"], [["TRIP-A", null]]]);
	await p.answer("TRIP-A");
	check("...which shows, with no error", [p.title(), p.errors()], ["My Itinerary", 0]);

	// A stale answer is keyed on the trip AND the person.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.pick("Sam");
	await p.pick("Alex");
	await p.answer("TRIP-A", undefined, "EMP-2");
	check("an answer for the person picked before is dropped", [p.title(), p.shown()], ["Alex's itinerary", null]);
	await p.answer("TRIP-A", undefined, "EMP-3");
	check("...and the person picked last is shown", [p.title(), p.shown()], ["Alex's itinerary", "Trip A"]);
	await p.pick("Sam");
	await p.pick("Whole crew");
	await p.answer("TRIP-A", false, "EMP-2");
	check("...and so is its error", p.errors(), 0);
	await p.answer("TRIP-A", undefined, "crew");
	check("...and the whole crew, picked last, is shown", [p.title(), p.shown()], ["Whole crew", "Trip A"]);

	// No Employee record (a coordinator's account) with a link to a trip.
	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [], employee: null });
	check("no employee record, but a ?trip=: that trip is asked for, with no history call", [p.browser.calls, p.pending()], [[], ["TRIP-X"]]);
	await p.answer("TRIP-X");
	check("...and shown, not 'No employee record'", [p.shown(), p.has("No employee record")], ["Trip X", false]);
	check("...as the whole crew, with no Me", [p.title(), p.people()], ["Whole crew", ["Whole crew", "Dana"]]);
	await p.pick("Dana");
	check("...where picking a person pushes an entry", p.urls(), ["push /itinerary?trip=TRIP-X&as=EMP-4"]);
	await p.answer("TRIP-X");
	check("...showing them", p.title(), "Dana's itinerary");
	p.browser.back();
	await p.browser.settle();
	check("...and Back works with no trips of their own", [p.asked(), p.browser.calls.length], [[["TRIP-X", null]], 1]);
	await p.answer("TRIP-X");
	check("...back to the whole crew", p.title(), "Whole crew");

	p = loadItinerary("/itinerary", { trips: [], employee: null });
	check("no employee record and no ?trip=: says so, and touches no history", [p.empty(), p.browser.calls], [["No employee record is linked to your user account."], []]);

	// "Today" is this phone's date, not UTC's.
	p = loadItinerary("/itinerary", {
		Date: arizonaEvening(),
		trips: [
			{ name: "TRIP-P1", purpose: "Ends today", start_date: "2026-09-23", end_date: "2026-09-25", mine: true },
			{ name: "TRIP-P2", purpose: "Starts tomorrow", start_date: "2026-09-26", end_date: "2026-09-28", mine: true },
		],
	});
	check("at 8:30 PM in Arizona (already tomorrow in UTC) the default trip is the one on today", p.pending(), ["TRIP-P1"]);
	await p.answer("TRIP-P1");
	check("...and Today is Sep 25, not Sep 26", p.todays(), [true, false]);

	// "Report a problem" open: its Back is its own.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	await p.answer("TRIP-B");
	p.capture.open = true;
	p.browser.history.pushState({ ee_capture: "x" }, ""); // the panel's own entry
	p.browser.back();
	await p.browser.settle();
	check("Back with the panel open (and its discard refused) loads nothing", p.pending(), []);
	// The panel closes as it answers a Back several entries deep, landing on Trip A's entry.
	p.browser.listeners.popstate.push(() => {
		p.capture.open = false;
	});
	p.browser.traverse(-1);
	await p.browser.settle();
	check("once the panel has answered, the page follows the address", p.pending(), ["TRIP-A"]);
	await p.answer("TRIP-A");

	p = loadItinerary("/itinerary", { trips: [TRIPS[1]] });
	check("a single trip gets its ?trip= too, and no chips", [p.trip(), p.root.find("ti-trip-chip").length], ["TRIP-A", 0]);

	p = loadItinerary("/itinerary", { trips: [] });
	check("no trips: no history call at all", p.browser.calls, []);

	// Signed out since the page loaded is not a refusal, though frappe answers it with the
	// same 403 (it carries on as Guest). It used to rewrite the address to the default trip and
	// then say "You don't have access to this trip" about the traveler's own trip.
	p = loadItinerary("/itinerary?trip=TRIP-B", { cookie: "user_id=pat%40example.com; full_name=Pat" });
	await p.answer("TRIP-B");
	await p.tap("Trip C");
	p.session.expired = true;
	await p.answer("TRIP-C");
	await p.browser.settle();
	check(
		"an expired session keeps the entry that was tapped, and writes no history",
		[p.urls(), p.trip(), p.pending()],
		[["push /itinerary?trip=TRIP-C"], "TRIP-C", []]
	);
	check(
		"...says so, with a way to sign in that comes back to this trip",
		[p.empty(), p.root.find("ti-signin").map((a) => a.href), p.has("don't have access")],
		[["Your session has expired. Sign in again"], ["/login?redirect-to=/itinerary%3Ftrip%3DTRIP-C"], false]
	);
	p.session.expired = false;
	p.browser.back();
	await p.browser.settle();
	check("...and Back still goes to the trip before", [p.trip(), p.pending()], ["TRIP-B", ["TRIP-B"]]);

	p = loadItinerary("/itinerary", { cookie: "user_id=pat%40example.com" });
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	p.document.cookie = "user_id=Guest"; // signed out in another tab: no session_expired in the answer
	p.session.next = refusal(403, "PermissionError", "Not permitted");
	await p.answer("TRIP-B");
	await p.browser.settle();
	check(
		"signed out in another tab (a plain 403, and the user_id cookie back to Guest): the same",
		[p.urls(), p.trip(), p.empty()],
		[["replace /itinerary?trip=TRIP-A", "push /itinerary?trip=TRIP-B"], "TRIP-B", ["Your session has expired. Sign in again"]]
	);

	p = loadItinerary("/itinerary?trip=TRIP-SECRET", { cookie: "user_id=pat%40example.com" });
	await p.answer("TRIP-SECRET");
	check("a 403 while still signed in is someone else's trip, and falls back as before", p.urls(), ["replace /itinerary?trip=TRIP-A"]);

	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-2");
	p.session.next = refusal(400, "CSRFTokenError", "Invalid Request");
	await p.answer("TRIP-A");
	check(
		"a refusal that is not about the person (a 400 for a stale CSRF token) keeps ?as= and shows the error",
		[p.urls(), p.pending(), p.errors()],
		[[], [], 1]
	);

	// A refusal of an entry the page just pushed, whose fallback is the entry behind it: one
	// Back after replacing it did nothing (two identical entries in a row).
	const GONE = { name: "TRIP-GONE", purpose: "Trip Gone", start_date: iso(20), end_date: iso(22), mine: true };
	p = loadItinerary("/itinerary", { trips: TRIPS.concat([GONE]) });
	await p.answer("TRIP-A");
	await p.tap("Trip Gone"); // deleted since the page loaded
	await p.answer("TRIP-GONE");
	await p.browser.settle();
	check(
		"a trip refused after a tap on the default trip steps back onto it rather than copying it",
		[p.urls(), p.browser.entries.map((e) => e.url.replace(ORIGIN, "")), p.browser.index],
		[
			["replace /itinerary?trip=TRIP-A", "push /itinerary?trip=TRIP-GONE"],
			["/desk", "/itinerary?trip=TRIP-A", "/itinerary?trip=TRIP-GONE"],
			1,
		]
	);
	await p.answer("TRIP-A");
	check("...showing it, with nothing left to load", [p.shown(), p.pending()], ["Trip A", []]);
	p.browser.back();
	await p.browser.settle();
	check("...so one Back leaves the page", p.browser.left, ORIGIN + "/desk");

	p = loadItinerary("/itinerary", { trips: TRIPS.concat([GONE]) });
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	await p.answer("TRIP-B");
	await p.tap("Trip Gone");
	await p.answer("TRIP-GONE");
	check("...from another trip, the refused entry is replaced with the default trip, as before", p.urls().slice(-1), [
		"replace /itinerary?trip=TRIP-A",
	]);

	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	SERVER_TRIPS["TRIP-A"].crew = ["EMP-1", "EMP-2"]; // Alex taken off the crew after the answer
	try {
		await p.pick("Alex");
		await p.answer("TRIP-A", undefined, "EMP-3");
		await p.browser.settle();
		check(
			"a person refused after a pick from the default view steps back onto it too",
			[p.urls(), p.browser.index, p.as(), p.asked()],
			[["replace /itinerary?trip=TRIP-A", "push /itinerary?trip=TRIP-A&as=EMP-3"], 1, null, [["TRIP-A", null]]]
		);
		await p.answer("TRIP-A", undefined, null);
		check("...showing it", p.title(), "My Itinerary");
	} finally {
		SERVER_TRIPS["TRIP-A"].crew = ["EMP-1", "EMP-2", "EMP-3"];
	}

	// "Report a problem" opened while a refused trip was loading: the panel's entry is on top,
	// and a replace would have rewritten it, leaving the panel unable to step back off it.
	p = loadItinerary("/itinerary?trip=TRIP-SECRET");
	p.capture.open = true;
	p.browser.history.pushState({ ee_capture: "panel-1" }, "");
	await p.answer("TRIP-SECRET");
	check(
		"a refusal while the report panel is open writes no history; the fallback is only shown",
		[p.urls(), p.browser.history.state, p.pending()],
		[["push null"], { ee_capture: "panel-1" }, ["TRIP-A"]]
	);
	p.capture.open = false;
	p.browser.back(); // the panel closing with its own Back
	await p.browser.settle();
	check("...once it has closed, the address's trip is asked for again", p.pending(), ["TRIP-A", "TRIP-SECRET"]);
	await p.answer("TRIP-SECRET");
	check("...and that refusal replaces its entry", [p.urls().slice(1), p.browser.index], [["replace /itinerary?trip=TRIP-A"], 1]);

	await testItineraryDocuments();
}

// The trip's files: on each booking's card, on the Documents screen (&view=docs), and pictures in
// the viewer (&file=). Every screen and the viewer is an entry of its own.
async function testItineraryDocuments() {
	let p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	check(
		"a booking row's attachment (its receipt, which is money) draws nothing, even from a stale answer that still sends one",
		[p.has("Attachment"), p.hrefs().includes(RECEIPT)],
		[false, false]
	);
	check("the flight lists its files for the person shown: their own boarding pass, and the one for everyone on it", p.docs(), ["Pat's boarding pass", "Southwest confirmation"]);
	check("...each saying what it is, with no 'for <name>' on one person's own view", p.docSubs(), ["Boarding pass · Picture", "Booking confirmation · PDF"]);
	check("the trip's two screens are offered, counting the files this person can see", [p.screens(), p.onScreen()], [["Day by day", "Documents (3)"], ["Day by day"]]);

	const pdf = p.doc("Southwest confirmation");
	check(
		"a PDF is a link to the file that opens in a new tab (the phone's own viewer), never a download",
		[pdf.href, pdf.target, pdf.rel, "download" in pdf.attrs],
		["/private/files/wn1-confirmation.pdf", "_blank", "noopener", false]
	);
	pdf.click();
	await p.browser.settle();
	check("...and tapping it writes no history and opens no viewer", [p.urls(), p.viewer()], [["replace /itinerary?trip=TRIP-A"], null]);

	const pass = p.doc("Pat's boarding pass");
	check(
		"a picture is a real link too (a long press still offers a new tab), marked as opening a dialog",
		[pass.href, pass.target || null, pass.attrs["aria-haspopup"]],
		["/private/files/pat-pass.png", null, "dialog"]
	);
	pass.focus(); // as a keyboard would reach it
	pass.click();
	await flush();
	check("tapping a picture pushes one entry, &file=<document>", p.urls().slice(1), ["push /itinerary?trip=TRIP-A&file=TD-PASS-PAT"]);
	check("...remembering the screen it was opened over", p.browser.history.state, {
		itin_trip: "TRIP-A",
		itin_as: null,
		itin_file: "TD-PASS-PAT",
		itin_from: { trip: "TRIP-A", as: null },
	});
	let v = p.viewer();
	check("...and opens the viewer over the page, as a modal dialog", [!!v, v && v.attrs.role, v && v.attrs["aria-modal"], p.viewerTitle()], [true, "dialog", "true", "Pat's boarding pass"]);
	check(
		"...showing the picture itself, and 'Open original' in a new tab",
		[v.find("ti-viewer-img")[0].src, v.find("ti-viewer-original")[0].href, v.find("ti-viewer-original")[0].target],
		["/private/files/pat-pass.png", "/private/files/pat-pass.png", "_blank"]
	);
	check(
		"...with focus on Close, and the page underneath out of reach",
		[p.focused() === v.find("ti-viewer-close")[0], p.root.attrs.inert, p.root.attrs["aria-hidden"], p.document.body.classList.contains("ti-viewer-open")],
		[true, "", "true", true]
	);
	v.find("ti-viewer-original")[0].focus();
	v.dispatch("keydown", { key: "Tab" });
	check("...and Tab stays inside it", p.focused() === v.find("ti-viewer-close")[0], true);
	check("...fetching nothing", p.pending(), []);
	p.browser.back();
	await p.browser.settle();
	check("Back closes it, fetching nothing and writing no history", [p.viewer(), p.file(), p.pending(), p.browser.calls.length], [null, null, [], 2]);
	check(
		"...handing focus back to the picture's link, and the page back",
		[p.focused() === pass, p.root.attrs.inert || null, p.document.body.classList.contains("ti-viewer-open")],
		[true, null, false]
	);
	p.browser.forward();
	await p.browser.settle();
	check("Forward reopens it", [p.viewerTitle(), p.file(), p.pending(), p.browser.calls.length], ["Pat's boarding pass", "TD-PASS-PAT", [], 2]);
	const entries = p.browser.entries.length;
	await p.closeViewer();
	check("its Close button goes Back (history.back()), adding no entry, and shuts at once", [p.browser.calls.length, p.browser.tasks.length, p.viewer()], [2, 1, null]);
	await p.browser.settle();
	check("...leaving the picture's entry ahead for Forward", [p.file(), p.browser.index, p.browser.entries.length], [null, 1, entries]);
	p.browser.forward();
	await p.browser.settle();
	await p.key("Escape");
	check("Escape goes Back the same way", [p.browser.tasks.length, p.viewer()], [1, null]);
	await p.browser.settle();
	check("...onto the screen underneath", [p.file(), p.browser.index, p.browser.calls.length], [null, 1, 2]);

	// "Report a problem" over the viewer: it owns Escape and Back, and the viewer does nothing.
	p.browser.forward();
	await p.browser.settle();
	p.capture.open = true;
	await p.key("Escape");
	await p.closeViewer();
	check(
		"with 'Report a problem' open over the viewer, neither Escape nor Close touches the viewer or history",
		[!!p.viewer(), p.browser.tasks.length, p.browser.calls.length],
		[true, 0, 2]
	);
	p.capture.open = false;
	await p.key("Escape");
	await p.browser.settle();
	check("...and once it has closed, Escape closes the viewer again", [p.viewer(), p.file()], [null, null]);
	p.capture.open = true;
	p.doc("Pat's boarding pass").click();
	await flush();
	check("...a picture tapped while the panel is open opens nothing", [p.viewer(), p.browser.calls.length], [null, 2]);
	p.capture.open = false;

	// The Documents screen.
	await p.screen("Documents");
	check("'Documents' pushes one entry, &view=docs, fetching nothing", [p.urls().slice(2), p.pending()], [["push /itinerary?trip=TRIP-A&view=docs"], []]);
	check(
		"...and lists every file this person can see: the whole trip's first, then each booking's",
		[p.docGroups(), p.docs()],
		[["For the whole trip", "Southwest WN 1"], ["Site map", "Pat's boarding pass", "Southwest confirmation"]]
	);
	check("...on a screen of its own, named in the page title", [p.onScreen(), p.days(), p.document.title], [["Documents (3)"], 0, "Documents – My Itinerary"]);
	p.browser.back();
	await p.browser.settle();
	check("Back returns to the day list, fetching nothing", [p.view(), p.onScreen(), p.days(), p.pending(), p.browser.calls.length], [null, ["Day by day"], 1, [], 3]);
	p.browser.forward();
	await p.browser.settle();
	check("Forward restores the Documents screen", [p.view(), p.onScreen(), p.pending()], ["docs", ["Documents (3)"], []]);
	p.doc("Pat's boarding pass").click();
	await flush();
	check("a picture opened from the Documents screen is over it: &view=docs&file=", p.urls().slice(-1), ["push /itinerary?trip=TRIP-A&view=docs&file=TD-PASS-PAT"]);
	p.browser.back();
	await p.browser.settle();
	check("...and Back leaves the Documents screen showing", [p.viewer(), p.view(), p.onScreen()], [null, "docs", ["Documents (3)"]]);
	await p.screen("Day by day");
	check(
		"'Day by day' goes Back to the day list the Documents screen was opened from, adding no entry",
		[p.browser.calls.length, p.browser.tasks.length, p.days()],
		[4, 1, 1]
	);
	await p.browser.settle();
	check("...so the Documents screen is still ahead for Forward", [p.view(), p.browser.index, p.browser.entries.length], [null, 1, 4]);

	// People and trips.
	await p.screen("Documents");
	await p.pick("Sam");
	check("picking a person keeps the Documents screen: &as=&view=docs", p.urls().slice(-2), ["push /itinerary?trip=TRIP-A&view=docs", "push /itinerary?trip=TRIP-A&as=EMP-2&view=docs"]);
	await p.answer("TRIP-A");
	check("...showing their files (a file with no title goes by its file name)", [p.onScreen(), p.docs()], [["Documents (3)"], ["Site map", "sam-pass.jpg", "Southwest confirmation"]]);
	await p.pick("Whole crew");
	await p.answer("TRIP-A");
	check(
		"the whole crew has every file, each saying who it is for",
		[p.docs(), p.docSubs()],
		[
			["Site map", "Pat's boarding pass", "sam-pass.jpg", "Southwest confirmation"],
			["PDF", "Boarding pass · for Pat · Picture", "Boarding pass · for Sam · Picture", "Booking confirmation · PDF"],
		]
	);
	await p.pick("Alex");
	await p.answer("TRIP-A");
	check("someone on none of the bookings has only the whole trip's", [p.screens(), p.docs()], [["Day by day", "Documents (1)"], ["Site map"]]);
	p.browser.back();
	await p.browser.settle();
	check("Back returns to the person before, on the Documents screen", [p.as(), p.view(), p.asked()], ["crew", "docs", [["TRIP-A", "crew"]]]);
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	check("a trip chip drops ?as=, ?view= and ?file=", p.urls().slice(-1), ["push /itinerary?trip=TRIP-B"]);
	await p.answer("TRIP-B");
	check("...opening that trip day by day, with no screens offered when it has no files", [p.view(), p.screens(), p.days()], [null, [], 0]);
	p.browser.back();
	await p.browser.settle();
	check("Back from it returns to the Documents screen of the trip before", [p.trip(), p.view(), p.asked()], ["TRIP-A", "docs", [["TRIP-A", "crew"]]]);
	await p.answer("TRIP-A");
	check("...drawn as it was", p.onScreen(), ["Documents (4)"]);
	check("every push was paid for by a tap", p.browser.unactivated, 0);

	// Back or Forward onto another trip with the viewer open shuts it.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	await p.answer("TRIP-B");
	await p.tap("Trip A");
	await p.answer("TRIP-A");
	p.doc("Pat's boarding pass").click();
	await flush();
	p.browser.traverse(-2);
	await p.browser.settle();
	check("Back two entries at once, onto another trip, shuts the viewer and loads that trip", [p.viewer(), p.pending(), p.browser.calls.length], [null, ["TRIP-B"], 4]);
	await p.answer("TRIP-B");

	// Reloads and links.
	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs");
	check("a reload of the Documents screen touches no history", p.browser.calls, []);
	await p.answer("TRIP-A");
	check("...and lands on it", [p.onScreen(), p.docGroups()], [["Documents (3)"], ["For the whole trip", "Southwest WN 1"]]);

	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs&file=TD-PASS-PAT", {
		behind: [{ url: "/itinerary?trip=TRIP-A&view=docs", state: { itin_trip: "TRIP-A", itin_as: null, itin_view: "docs" } }],
		state: { itin_trip: "TRIP-A", itin_as: null, itin_view: "docs", itin_file: "TD-PASS-PAT", itin_from: { trip: "TRIP-A", as: null, view: "docs" } },
	});
	check("a reload of a picture's entry draws no viewer before the answer, and touches no history", [p.viewer(), p.browser.calls], [null, []]);
	await p.answer("TRIP-A");
	check("...then reopens it over its screen", [p.viewerTitle(), p.onScreen()], ["Pat's boarding pass", ["Documents (3)"]]);
	await p.closeViewer();
	await p.browser.settle();
	check(
		"...and Close goes Back onto that screen's entry, still behind it",
		[p.viewer(), p.browser.left, p.browser.index, p.view(), p.file(), p.browser.calls],
		[null, null, 1, "docs", null, []]
	);

	p = loadItinerary("/itinerary?trip=TRIP-A&file=TD-PASS-PAT");
	await p.answer("TRIP-A");
	check("a link straight to a picture opens it", p.viewerTitle(), "Pat's boarding pass");
	v = p.viewer();
	v.find("ti-viewer-img")[0].dispatch("error");
	check("...and a picture this browser cannot draw (a HEIC photo off an iPhone) says so, keeping Open original", [p.document.body.find("ti-viewer-error").length, v.find("ti-viewer-original").length], [1, 1]);
	await p.closeViewer();
	await p.browser.settle();
	check(
		"...with nothing of the page's behind it, Close does not leave the page: the entry stops naming the file",
		[p.viewer(), p.browser.left, p.urls()],
		[null, null, ["replace /itinerary?trip=TRIP-A"]]
	);

	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-2&file=TD-PASS-PAT");
	await p.answer("TRIP-A");
	check("a picture the person shown cannot see (Pat's pass, on Sam's view) is not opened, and nothing is written", [p.viewer(), p.browser.calls], [null, []]);
	await p.screen("Documents");
	check("...and the next entry written drops it", p.urls(), ["push /itinerary?trip=TRIP-A&as=EMP-2&view=docs"]);

	p = loadItinerary("/itinerary?trip=TRIP-A&file=TD-CONF");
	await p.answer("TRIP-A");
	check("a PDF named as the picture is never drawn in the viewer", [p.viewer(), p.browser.calls], [null, []]);

	const other = (name, title, url) => ({ name, title, kind: "Other", url, file_name: "x.pdf", is_image: false, for_name: null, booking_label: null });
	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs");
	p.session.next = {
		status: 200,
		body: { message: {
			trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(-1), end_date: iso(1), days: [], crew: [], viewing: null,
			documents: [other("TD-JS", "Script", "javascript:alert(1)"), other("TD-PR", "Elsewhere", "//evil.example/x.pdf"), other("TD-OK", "Good", "/files/ok.pdf")],
		} },
	};
	await p.answer("TRIP-A");
	check(
		"a file whose address is neither this site's path nor a web address is never made a link",
		[p.docs(), p.hrefs().filter((h) => h !== "/travel_guidelines"), p.screens()],
		[["Good"], ["/files/ok.pdf"], ["Day by day", "Documents (1)"]]
	);

	// A file's booking is its `group`, not its label: two rooms at one hotel are two bookings,
	// and a booking with no name yet is still not the whole trip.
	const onBooking = (name, group, label) => Object.assign(other(name, name, "/files/" + name + ".pdf"), { group, booking_label: label });
	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs");
	p.session.next = {
		status: 200,
		body: { message: {
			trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(-1), end_date: iso(1), days: [], crew: [], viewing: null,
			documents: [onBooking("room-1", "r1", "Hampton Inn"), onBooking("room-2", "r2", "Hampton Inn"), onBooking("unnamed", "r3", null), onBooking("map", null, null)],
		} },
	};
	await p.answer("TRIP-A");
	check(
		"the Documents screen groups files by booking, whatever the bookings are called",
		[p.docGroups(), p.docs()],
		[["For the whole trip", "Hampton Inn", "Hampton Inn", "Booking"], ["map", "room-1", "room-2", "unnamed"]]
	);

	p = loadItinerary("/itinerary?trip=TRIP-B&view=docs");
	await p.answer("TRIP-B");
	check(
		"the Documents screen of a trip with no files (an answer with no `documents` at all) says so, and still offers the day list",
		[p.empty(), p.screens()],
		[["No documents for this trip yet."], ["Day by day", "Documents (0)"]]
	);
	await p.screen("Day by day");
	check("...which, with nothing of the page's behind it, is a new entry", [p.urls(), p.days(), p.screens()], [["push /itinerary?trip=TRIP-B"], 0, []]);

	// Room guests on the whole crew's view.
	const day = (d) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
	p = loadItinerary("/itinerary?trip=TRIP-H&as=crew", { trips: [] });
	await p.answer("TRIP-H");
	check(
		"the whole crew's room card marks a guest, with their own nights",
		p.text("ti-member-note"),
		[`guest · ${day("2026-09-28")} – ${day("2026-09-29")}`]
	);
}

// ---------------------------------------------------------------------------- /contract-sign

const REF = "tok_" + "A".repeat(40);
const CHECKOUT = "https://checkout.stripe.com/c/pay/cs_test_123";

function makeStorage(throws) {
	const map = new Map();
	return {
		map,
		getItem(k) {
			if (throws) throw new Error("denied");
			return map.has(k) ? map.get(k) : null;
		},
		setItem(k, v) {
			if (throws) throw new Error("denied");
			map.set(k, String(v));
		},
		removeItem(k) {
			if (throws) throw new Error("denied");
			map.delete(k);
		},
	};
}

function loadContractSign(state, opts) {
	opts = opts || {};
	const env = {};
	const browser = makeBrowser("/contract-sign?ref=" + REF, { prevPath: "/mail" });
	env.browser = browser;
	const ids =
		state === "signable"
			? {
					"cs-agreement": {},
					"cs-form": {},
					"cs-mode-typed": { tag: "button" },
					"cs-mode-drawn": { tag: "button" },
					"cs-typed-pane": {},
					"cs-drawn-pane": { hidden: true },
					"cs-signed-name": { tag: "input", value: "Jane Customer" },
					"cs-preview": {},
					"cs-signed-name-drawn": { tag: "input" },
					"cs-pad": { tag: "canvas" },
					"cs-clear": { tag: "button" },
					"cs-title": { tag: "input" },
					"cs-email": { tag: "input", value: "jane@example.com" },
					"cs-agree": { tag: "input", checked: true },
					"cs-turnstile": {},
					"cs-error": { hidden: true },
					"cs-submit": { tag: "button" },
					"cs-decline": { tag: "button" },
					"cs-done": { hidden: true },
					"cs-autopay": { hidden: true },
					"cs-autopay-start": { tag: "button" },
					"cs-autopay-skip": { tag: "button" },
					"cs-autopay-note": { hidden: true },
					"cs-declined": { hidden: true },
				}
			: {
					"cs-autopay-resume": { hidden: true },
					"cs-autopay-resume-start": { tag: "button" },
					"cs-autopay-resume-skip": { tag: "button" },
				};
	const document = makeDocument(env, ids);
	const posts = [];
	const storage = opts.storage || makeStorage();
	const win = browser.window;
	Object.assign(win, {
		window: win,
		document,
		history: browser.history,
		sessionStorage: storage,
		CS_BOOT: Object.assign({ state, ref: opts.ref || REF, csrf_token: "" }, opts.boot || {}),
		fetch(u, o) {
			return new Promise((resolve) => {
				posts.push({ method: u.split(".").pop(), body: JSON.parse(o.body), resolve });
			});
		},
		confirm: () => true,
		prompt: () => "",
		devicePixelRatio: 1,
		console,
		setTimeout,
		clearTimeout,
		setInterval,
		clearInterval,
		Math,
		JSON,
		Date,
	});
	const source = fs.readFileSync(path.join(APP, "public", "js", "contract_sign", "contract_sign.js"), "utf8");
	runInPage(source, win, "contract_sign.js");
	document.dispatch("DOMContentLoaded");
	const $ = (id) => document.byId[id];
	return {
		browser,
		$,
		posts,
		storage,
		async answer(method, message) {
			await flush();
			const i = posts.findIndex((p) => p.method === method);
			if (i === -1) {
				check(`a ${method} call is waiting to be answered`, posts.map((p) => p.method), [method]);
				return null;
			}
			const post = posts.splice(i, 1)[0];
			post.resolve({ json: async () => ({ message }) });
			await flush();
			return post;
		},
	};
}

async function testContractSign() {
	console.log("/contract-sign");
	const source = fs.readFileSync(path.join(APP, "public", "js", "contract_sign", "contract_sign.js"), "utf8");
	const code = stripComments(source);
	check("no history call, popstate or beforeunload anywhere", /pushState|replaceState|popstate|beforeunload/.test(code), false);
	check("no reload: the page never re-renders from the server behind the customer", /location\.reload/.test(code), false);

	// Sign, then Save a card.
	let p = loadContractSign("signable");
	await p.answer("begin_signing", { esign_sid: "sid1" });
	p.$("cs-form").dispatch("submit");
	await p.answer("sign_contract", { ok: true, autopay: { offer: true } });
	check("signing swaps in place", [p.$("cs-form").hidden, p.$("cs-done").hidden, p.$("cs-autopay").hidden], [true, false, false]);
	p.$("cs-autopay-start").click();
	await p.answer("start_autopay", { ok: true, checkout_url: CHECKOUT });
	check("Save a card goes to Stripe", p.browser.assigned, CHECKOUT);
	const stored = [...p.storage.map.values()][0] || "";
	check("the Stripe link is kept for this tab", JSON.parse(stored || "{}").url, CHECKOUT);
	check("...filed under a fingerprint, never the ref itself", stored.includes(REF), false);
	check("no history entry was added by any of it", p.browser.calls, []);
	const storage = p.storage;

	// A back-forward-cache restore of that same page.
	p.browser.assigned = null;
	p.browser.fire("pageshow", { persisted: true });
	check("a bfcache restore re-enables Save a card", p.$("cs-autopay-start").disabled, false);
	p.$("cs-autopay-start").click();
	await flush();
	check("...which reuses the link it was given, asking nothing", [p.browser.assigned, p.posts.length], [CHECKOUT, 0]);

	// Back from Stripe: a fresh load of the link, now "Already signed".
	p = loadContractSign("signed", { storage, boot: { autopay_resumable: true } });
	check("Back from Stripe offers the enrolment again", p.$("cs-autopay-resume").hidden, false);
	p.$("cs-autopay-resume-start").click();
	check("...with the link this tab was given, asking the server nothing", [p.browser.assigned, p.posts.length], [CHECKOUT, 0]);

	p = loadContractSign("signed", { storage, boot: { autopay_resumable: true }, ref: "tok_" + "B".repeat(40) });
	check("never on another agreement opened in the same tab", p.$("cs-autopay-resume").hidden, true);

	p = loadContractSign("signed", { storage, boot: { autopay_resumable: true } });
	p.$("cs-autopay-resume-skip").click();
	check("No thanks hides it and forgets the link", [p.$("cs-autopay-resume").hidden, storage.map.size], [true, 0]);

	const old = makeStorage();
	old.setItem("cs_autopay_checkout", JSON.stringify({ ref: "x", url: CHECKOUT, at: 0 }));
	p = loadContractSign("signed", { storage: old, boot: { autopay_resumable: true } });
	check("an old or foreign record is not offered", p.$("cs-autopay-resume").hidden, true);

	p = loadContractSign("signable");
	await p.answer("begin_signing", { esign_sid: "sid1" });
	p.$("cs-form").dispatch("submit");
	await p.answer("sign_contract", { ok: true, autopay: { offer: true } });
	p.$("cs-autopay-start").click();
	await p.answer("start_autopay", { ok: true, checkout_url: CHECKOUT });
	p = loadContractSign("signed", { storage: p.storage, boot: { autopay_resumable: false } });
	check("finished (or never started) per the server: not offered, and forgotten", [p.$("cs-autopay-resume").hidden, p.storage.map.size], [true, 0]);

	p = loadContractSign("signed", { storage: makeStorage(true), boot: { autopay_resumable: true } });
	check("storage refused: the notice still renders, without the offer", p.$("cs-autopay-resume").hidden, true);

	// bfcache restore with nothing kept: say we'll follow up rather than show a dead button.
	p = loadContractSign("signable", { storage: makeStorage(true) });
	await p.answer("begin_signing", { esign_sid: "sid1" });
	p.$("cs-form").dispatch("submit");
	await p.answer("sign_contract", { ok: true, autopay: { offer: true } });
	p.$("cs-autopay-start").click();
	await p.answer("start_autopay", { ok: true, checkout_url: CHECKOUT });
	p.browser.fire("pageshow", { persisted: true });
	check("a bfcache restore with no link kept shows the follow-up note", [p.$("cs-autopay-start").disabled, p.$("cs-autopay-note").hidden], [true, false]);

	// Decline, in place.
	p = loadContractSign("signable");
	await p.answer("begin_signing", { esign_sid: "sid1" });
	p.$("cs-decline").click();
	await p.answer("decline_contract", { ok: true });
	check("declining swaps in place to 'declined'", [p.$("cs-form").hidden, p.$("cs-agreement").hidden, p.$("cs-declined").hidden], [true, true, false]);
	check("...with no reload and no history entry", [p.browser.reloads, p.browser.calls.length], [0, 0]);
}

// ---------------------------------------------------------------------------- main

const SUITES = { "pay-card": testPayCard, itinerary: testItinerary, "contract-sign": testContractSign };

// A page promise that rejects with nothing to catch it is a flow left hanging — on /pay-card, Pay
// on "Please wait…" for good. Counted as a failure, rather than crashing the run or passing unseen.
process.on("unhandledRejection", (reason) => {
	checks += 1;
	failures += 1;
	console.error(`  FAIL a promise rejected with no handler: ${(reason && reason.stack) || reason}`);
});

(async () => {
	const only = process.argv[2];
	if (only && !SUITES[only]) {
		console.error(`unknown page "${only}"; one of ${Object.keys(SUITES).join(", ")}`);
		process.exit(2);
	}
	for (const [name, fn] of Object.entries(SUITES)) {
		if (only && only !== name) continue;
		try {
			await fn();
		} catch (e) {
			failures += 1;
			console.error(`  FAIL ${name} threw: ${e && e.stack}`);
		}
	}
	console.log(`\n${checks - failures} of ${checks} checks passed${failures ? `, ${failures} FAILED` : ""}`);
	process.exit(failures ? 1 : 0);
})();
