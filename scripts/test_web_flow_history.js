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
 *     viewer moves while "Report a problem" is open;
 *   - the Contacts card draws what the answer's `contacts` sends for the person shown, and
 *     nothing it leaves out: 911 first, a tel: link built from a phone's digits (and a leading
 *     +) only, a mailto: only for an address that is one, a hotel's urgent care and directions
 *     only as https links in a new tab, every value as text; it starts shut, as one line naming
 *     the parts it has with 911 last, and its button's aria-expanded says which; opening and
 *     shutting it writes no history, is kept across redraws, and an open card is remembered per
 *     trip in localStorage when there is one (and works when storage throws, or is absent as it
 *     is here by default); an answer with no `contacts` draws no card;
 *   - "Print / save as PDF" opens the person shown's trip sheet (`my_sheet_url`, else the whole
 *     trip's `sheet_url`) in a new tab with no history write, and only this site's path or a web
 *     address is made a link;
 *   - a stop's place notes (`poi.notes`, v1.553.0) are drawn under "Place notes" after the
 *     visit's own notes, as text with their line breaks (markup in them is never a tag), short
 *     ones whole and long ones in a <details> that starts shut on their first line and writes no
 *     history when tapped; blank or missing notes draw nothing; the marker's popup carries them
 *     too; and the copy saved on the phone draws them offline, asking for nothing more;
 *   - offline (a stand-in IndexedDB and service worker, `makeIndexedDB` / `makeServiceWorker`):
 *     the page registers its worker for /itinerary only, at this deploy's address, and tells it
 *     who is signed in; every answer is saved for that person, trip and view, with the boot's
 *     trip list; the person's own trips in progress or starting within two weeks are saved
 *     ahead once a page, one at a time, and their pictures and PDFs (this site's own, 20 a trip)
 *     handed to the worker; with no answer at all (fetch rejects) the saved copy is drawn under
 *     "You're offline — showing your itinerary as saved …" with no history call, the Documents
 *     screen and Back / Forward work from it, a view never saved says so, a PDF opens from the
 *     phone's copy in a tab opened at the tap, and back online the answer replaces it in place;
 *     everything is saved under the offline marker the page was drawn with (the boot's
 *     `offline_key`, the `ee_itinerary_key` cookie), and shown only while the phone still holds
 *     it: with the marker gone (a sign-out) or somebody else's, nothing saved is shown, nothing of
 *     the kept page's boot is drawn while it waits, and everything saved is deleted (answers,
 *     lists, and the worker told to purge); a page drawn for somebody else, or a session that
 *     ran out ("Guest"), shows nothing saved and keeps it (no `user_id` at all, with the marker
 *     still there, is a home-screen app started again, and shows it); opening the page deletes
 *     everything saved under any other marker, and everything when there is none; with no
 *     marker in the boot nothing is saved; and with no IndexedDB, storage that throws, or a
 *     worker that will not register, the page is exactly what it was;
 *   - no usable answer (`makeTimers` for one bar of signal): a 200 cut off or naming no trip, the
 *     gateway's 502/503/504 and one bar of signal draw the saved copy (never saved over, and the
 *     late answer replaces it), frappe's own 500 does not; "Me" offline keeps the saved default
 *     view; a view never saved says so in place of "Loading trip…" and is asked again online; a
 *     phone with the kiosk's worker at "/" sends nothing to it on the first visit; a stale CSRF
 *     token is replaced once; a refusal lifts when the session answers again; a failed map is
 *     tried again.
 *   - the list of all trips (`?view=trips`, get_all_trips): a boot or reload on it draws it with
 *     no history call and asks for nothing else; its groups and rows are the server's, each row
 *     a link naming its trip (encoded) that says who is on it and whether this person is on it
 *     or organized it; "Show N more" opens the earlier trips in place with no entry; a row tap
 *     pushes `?trip=` and Back returns to the list, drawn at once as it was left while it is
 *     asked for again; "All trips" (first in the chip bar, there even for a single trip) pushes
 *     `?view=trips`, or steps Back when the list is the entry behind, and does nothing while
 *     "Report a problem" is open; the empty page's "See all trips" pushes it, and Back is the
 *     empty page again; a trip refused after a tap on the list steps back onto the list, which
 *     says why once; a 403 says the list is not theirs, an expired session offers to sign in
 *     back to the list, a 500 says so; with no usable answer (no signal, the gateway, six
 *     seconds of one bar) it says the list needs a connection and offers their own trips, only
 *     when a saved copy could be shown (a page kept from before a sign-out is refused), keeps a
 *     list drawn earlier under "may be out of date", and asks again online; an answer for a list
 *     since left, or since asked for again, is dropped, and so is a trip's answer once the list
 *     is on screen; every value on a row is text;
 *   - someone else's trip (`limited: true`): a line saying numbers and files are left out, no
 *     Documents screen and no print link, and none of those drawn even from a limited answer
 *     that carried them, whatever screen or picture the address names.
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

// Trip A's contacts, as api/travel._trip_contacts sends them: the hotels are the person shown's
// only (every one on the whole crew's view), and there is no job-site contact. Phones are as
// people typed them.
const URGENT = "https://www.google.com/maps/search/?api=1&query=urgent%20care%20near%20";
const DIRECTIONS = "https://www.google.com/maps/dir/?api=1&destination=";
const HOTELS = {
	"EMP-1": { name: "Hampton Inn", phone: "(702) 555-0142", address: "1 Main St<br>Las Vegas, NV", urgent_care_url: URGENT + "1%20Main%20St", directions_url: DIRECTIONS + "1%20Main%20St" },
	"EMP-2": { name: "Harborview Suites", phone: "+1 702.555.0199", address: "", urgent_care_url: URGENT + "Harborview%20Suites", directions_url: DIRECTIONS + "Harborview%20Suites" },
};
function tripAContacts(viewing) {
	return {
		emergency: "911",
		office: { label: "Sapphire travel desk", phone: "(602) 555-0100 ext. 12", email: "travel@example.com" },
		booked_by: { name: "Nik", phone: null, email: "nik@example.com" },
		lead: { name: "Pat", phone: "602-555-0111" },
		site: null,
		hotels: viewing ? (HOTELS[viewing] ? [HOTELS[viewing]] : []) : Object.values(HOTELS),
	};
}
const SHEET = "/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip&name=TRIP-A&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome";

// What the server knows. TRIP-X is readable but in nobody's boot list here (a coordinator's
// link); TRIP-SECRET is someone else's (403); anything else is gone (404).
const SERVER_TRIPS = {
	"TRIP-A": { crew: ["EMP-1", "EMP-2", "EMP-3"], days: (viewing) => {
		const items = sharedFlight(viewing);
		return items.length ? [{ date: iso(0), items }] : [];
	}, documents: (viewing) => visibleFiles(viewing), contacts: tripAContacts,
	sheet: (viewing) => ({ sheet_url: SHEET, my_sheet_url: viewing ? `${SHEET}&as=${viewing}` : null }) },
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
	// Someone else's trip, opened from the list of all trips: Pat is not on it and did not
	// organize it, so the server answers it `limited`, with no number and no file on any booking
	// (get_trip_itinerary's outsider view) and no trip sheet.
	"TRIP-Z": { purpose: "Trip Z", start_date: iso(5), end_date: iso(7), crew: ["EMP-4"], limited: true, days: () => [
		{ date: iso(5), items: [{ type: "flight", date: iso(5), sort_time: "", airline: "Delta", flight_number: "DL 9", departure_airport: "PHX", arrival_airport: "SEA", group: "z1", booking_reference: "", travelers: ["Dana"], members: [{ employee: "EMP-4", employee_name: "Dana", ref: "" }], whole_crew: false }] },
	], documents: () => [] },
};

// The list of all trips (get_all_trips), to the contract: every trip, grouped and ordered by
// the server (now, then coming up, then earlier ones, the most recent first), the crew by name
// with the trip lead first, and whether Pat travels on it (`mine`) or only organized it.
const ALL_TRIPS_METHOD = "erpnext_enhancements.api.travel.get_all_trips";
function allTripRows() {
	const past = [];
	for (let i = 1; i <= 13; i++) {
		past.push({ name: `TRIP-PAST-${i}`, purpose: `Past ${i}`, status: "Closed", travel_type: "Road", start_date: iso(-20 - i * 7), end_date: iso(-18 - i * 7), travel_for: null, crew: ["Alex"], lead: null, mine: false, organizing: false, group: "past" });
	}
	return [
		{ name: "TRIP-A", purpose: "Trip A", status: "Booked", travel_type: "Air", start_date: iso(-1), end_date: iso(1), travel_for: "Acme Fountains", crew: ["Pat", "Sam", "Alex"], lead: "Pat", mine: true, organizing: false, group: "now" },
		{ name: "TRIP-O", purpose: "Trip O", status: "Booked", travel_type: "Road", start_date: iso(0), end_date: iso(2), travel_for: null, crew: ["Sam", "Alex"], lead: "Sam", mine: false, organizing: true, group: "now" },
		{ name: "TRIP-Z", purpose: "Trip Z", status: "Planning", travel_type: "Air", start_date: iso(5), end_date: iso(7), travel_for: "Lakeside Resort", crew: ["Dana"], lead: "Dana", mine: false, organizing: false, group: "upcoming" },
		{ name: "TRIP-B", purpose: "Trip B", status: "Booked", travel_type: "Air", start_date: iso(10), end_date: iso(12), travel_for: null, crew: ["Pat", "Sam", "Alex", "Dana", "Lee", "Kim", "Ray"], lead: null, mine: true, organizing: false, group: "upcoming" },
		...past,
	];
}

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
	// Only a trip with files sends `documents`: the page must manage without the key. And only
	// Trip A sends `contacts` and its sheet's addresses, so every other answer is one without.
	if (t.documents) message.documents = t.documents(viewing);
	if (t.contacts) message.contacts = t.contacts(viewing);
	if (t.sheet) Object.assign(message, t.sheet(viewing));
	// Someone else's trip (the contract's outsider view): `limited`, and no sheet to print.
	message.limited = !!t.limited;
	if (t.limited) Object.assign(message, { sheet_url: null, my_sheet_url: null });
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
	// Offline (testItineraryOffline): every file the page fetched itself, the tabs it opened, and
	// the files "saved on this phone" (`opts.files`: {url: type}), which answer; anything else is
	// no answer at all, as a file the worker never kept is in airplane mode.
	const fileFetches = [];
	const tabs = [];
	const capture = { open: false };
	// `expired`: every answer is the one an expired session gets. `next`: the next answer is
	// this one, whatever the fake server would have said.
	const session = { expired: false, next: null };
	const boot = { trips: opts.trips || TRIPS, employee, employee_name: employee ? PEOPLE[employee] : null };
	// The signed-in user the page was drawn for. Absent unless a test gives one, as in every
	// check written before the page kept anything offline: it then keeps nothing.
	if ("user" in opts) boot.user = opts.user;
	// The offline marker the page was drawn with (www/itinerary.py: `offline_key`, the same value as
	// the `ee_itinerary_key` cookie it sets). Absent unless a test gives one: the page then saves
	// nothing and, offline, shows nothing saved.
	if ("key" in opts) boot.offline_key = opts.key;
	// No service worker and no IndexedDB unless a test gives them: the page must manage without.
	const navigator = {};
	if (opts.serviceWorker) navigator.serviceWorker = opts.serviceWorker.container;
	if ("onLine" in opts) navigator.onLine = opts.onLine;
	const win = browser.window;
	Object.assign(win, {
		window: win,
		document,
		history: browser.history,
		ITIN_BOOT: boot,
		ITIN_CSRF: "tok",
		ITIN_BUILD: opts.build || "",
		ee_capture: { isOpen: () => capture.open },
		fetch(u, o) {
			if (!String(u).startsWith("/api/method/")) {
				fileFetches.push(String(u));
				// A page the test serves (`opts.pages`: {url: html}), as text: /itinerary fetched for
				// a new CSRF token.
				if (opts.pages && u in opts.pages) {
					const html = opts.pages[u];
					return html === null
						? Promise.reject(new TypeError("Failed to fetch"))
						: Promise.resolve({ ok: true, status: 200, text: async () => html });
				}
				const type = (opts.files || {})[u];
				return type
					? Promise.resolve({ ok: true, status: 200, blob: async () => ({ url: u, type }) })
					: Promise.reject(new TypeError("Failed to fetch"));
			}
			const body = JSON.parse(o.body);
			const method = String(u).slice("/api/method/".length);
			return new Promise((resolve, reject) => {
				fetches.push({ method, trip: body.trip, as: body.as_employee || null, csrf: (o.headers || {})["X-Frappe-CSRF-Token"], resolve, reject });
			});
		},
		open(url, target) {
			const tab = { opened: [url, target], location: { href: "" }, closed: false, close() { this.closed = true; } };
			tabs.push(tab);
			return opts.popupsBlocked ? null : tab;
		},
		URLSearchParams,
		navigator,
		console,
		// `opts.timers` (makeTimers): time passes only when the test says so.
		setTimeout: opts.timers ? opts.timers.set : setTimeout,
		clearTimeout: opts.timers ? opts.timers.clear : clearTimeout,
	});
	if (opts.blobs) win.URL = { createObjectURL: (blob) => `blob:${blob.url}`, revokeObjectURL() {} };
	if (opts.indexedDB === "throws") {
		Object.defineProperty(win, "indexedDB", { get() { throw new Error("SecurityError: storage is blocked"); } });
	} else if (opts.indexedDB) {
		win.indexedDB = opts.indexedDB;
	}
	if (opts.Date) win.Date = opts.Date;
	// No localStorage unless a test gives it one (makeStorage): the page must manage without.
	if ("storage" in opts) win.localStorage = opts.storage;
	const source = fs.readFileSync(path.join(APP, "public", "js", "travel", "itinerary.js"), "utf8");
	runInPage(source, win, "itinerary.js");
	const root = document.byId["itinerary-root"];
	const texts = (cls) => root.find(cls).map((e) => e.textContent);
	const page = {
		browser,
		document,
		fetches,
		fileFetches,
		capture,
		root,
		// The tabs the page opened: where each went, and whether it was closed again.
		tabs: () => tabs.map((t) => ({ opened: t.opened, url: t.location.href, closed: t.closed })),
		// "You're offline — showing your itinerary as saved …", when it is on screen.
		offline: () => root.find("ti-offline").map((e) => e.textContent),
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
				// `cut`: the headers came, then the connection dropped part way through the body.
				const json = r.cut
					? async () => {
							throw new SyntaxError("Unexpected end of JSON input");
						}
					: async () => r.body;
				f.resolve({ ok: r.status === 200, status: r.status, json });
			}
			await flush();
		},
		// The CSRF token each request still waiting carries.
		tokens: () => fetches.map((f) => f.csrf),
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
		// A request for the list of all trips (get_all_trips) names no trip: it is "(all trips)".
		pending: () => fetches.map((f) => (f.method === ALL_TRIPS_METHOD ? "(all trips)" : f.trip)),
		asked: () => fetches.map((f) => (f.method === ALL_TRIPS_METHOD ? ["(all trips)", null] : [f.trip, f.as])),
		listAsked: () => fetches.filter((f) => f.method === ALL_TRIPS_METHOD).length,
		// Answer the oldest request for the list of all trips: `reply === false` is no answer at
		// all (offline); a {status, body} is that answer; nothing is the fake server's list.
		async answerList(reply) {
			const i = fetches.findIndex((f) => f.method === ALL_TRIPS_METHOD);
			if (i === -1) {
				check("a request for the list of all trips is waiting to be answered", page.pending(), ["(all trips)"]);
				return;
			}
			const f = fetches.splice(i, 1)[0];
			if (reply === false) f.reject(new TypeError("Failed to fetch"));
			else {
				const r = reply || { status: 200, body: { message: { trips: allTripRows(), more: 0 } } };
				f.resolve({ ok: r.status === 200, status: r.status, json: async () => r.body });
			}
			await flush();
		},
		// The list screen: its group headings, its rows' titles, one row by its title, and a tap.
		listGroups: () => texts("ti-list-group-title"),
		listRows: () => root.find("ti-list-row").map((a) => a.find("ti-list-title")[0].textContent),
		listRow: (purpose) => root.find("ti-list-row").find((a) => a.find("ti-list-title")[0].textContent === purpose) || null,
		async openRow(purpose) {
			const row = page.listRow(purpose);
			if (!row) {
				check(`the list offers ${purpose}`, page.listRows(), [purpose]);
				return;
			}
			row.click();
			await flush();
		},
		// "All trips", first in the trip chip bar.
		async allTrips() {
			const chip = root.find("ti-all-chip")[0];
			if (!chip) {
				check("the page offers All trips", page.text("ti-all-chip"), ["🧳All trips"]);
				return;
			}
			chip.click();
			await flush();
		},
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
		// What the trip's page is made of, top to bottom, by each part's first class.
		order: () => root.children.map((c) => c.className.split(" ")[0]),
		// Every tag drawn under the root.
		tags: () => {
			const out = [];
			const walk = (n) => n.children.forEach((c) => {
				out.push(c.tagName);
				walk(c);
			});
			walk(root);
			return out;
		},
		// The Contacts card: its rows, its links and whether it is open.
		contacts: () => root.find("ti-contacts")[0] || null,
		contactRoles: () => texts("ti-contact-role"),
		contactNames: () => texts("ti-contact-name"),
		contactSubs: () => texts("ti-contact-sub"),
		contactLinks: () => root.find("ti-contact-link").map((a) => ({
			href: a.href,
			text: a.find("ti-contact-text")[0].textContent,
			target: a.target || null,
			rel: a.rel || null,
			label: a.attrs["aria-label"] || null,
		})),
		contactsToggle: () => root.find("ti-contacts-toggle")[0] || null,
		contactsOpen: () => {
			const card = root.find("ti-contacts")[0];
			return card ? !card.find("ti-contacts-body")[0].hidden : null;
		},
		// What the card's button says: its one line while shut, the title alone while open.
		contactsLine: () => {
			const line = root.find("ti-contacts-line")[0];
			if (!line) return null;
			return line.children.filter((c) => !c.hidden).map((c) => c.textContent).join("");
		},
		async toggleContacts() {
			root.find("ti-contacts-toggle")[0].click();
			await flush();
		},
		print: () => root.find("ti-print-link")[0] || null,
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
	await testItineraryContacts();
	await testItineraryPlaceNotes();
	await testItineraryOffline();
	await testItineraryNoUsableAnswer();
	await testItineraryAllTrips();
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
			documents: [
				other("TD-JS", "Script", "javascript:alert(1)"),
				other("TD-PR", "Elsewhere", "//evil.example/x.pdf"),
				// A browser reads "/\host" as "//host": another site, however it starts.
				other("TD-BS", "Backslash", "/\\evil.example/x.pdf"),
				other("TD-BS2", "Backslash inside", "/files/..\\..\\x.pdf"),
				other("TD-OK", "Good", "/files/ok.pdf"),
			],
		} },
	};
	await p.answer("TRIP-A");
	check(
		"a file whose address is neither this site's path nor a web address (or has a backslash in it) is never made a link",
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

	// Two rooms at one hotel (one adult to a room, the policy): the headings say whose room each
	// is on the whole crew's list, and when on one person's, rather than one name twice.
	const room = (name, group, people, dates) => Object.assign(onBooking(name, group, "Hampton Inn"), { booking_people: people, booking_dates: dates });
	const rooms = (viewing, docs) => ({
		status: 200,
		body: { message: {
			trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(-1), end_date: iso(1), days: [], viewing,
			crew: ["EMP-1", "EMP-2", "EMP-3"].map((e) => ({ employee: e, employee_name: PEOPLE[e] })),
			documents: docs,
		} },
	});
	const shortDay = (d) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
	p = loadItinerary("/itinerary?trip=TRIP-A&as=crew&view=docs");
	p.session.next = rooms(null, [
		room("room-pat", "r1", ["Pat"], ["2026-09-28", "2026-09-30"]),
		Object.assign(room("room-sam", "r2", ["Sam"], ["2026-09-28", "2026-09-30"]), { url: "/files/room-sam.png", file_name: "room-sam.png", is_image: true }),
		room("room-crew", "r3", [], ["2026-09-28", "2026-09-30"]),
		Object.assign(onBooking("rental", "g4", "Enterprise"), { booking_people: ["Pat"], booking_dates: ["2026-09-28", null] }),
	]);
	await p.answer("TRIP-A");
	check(
		"...on the whole crew's list, by who is in each (a label nobody else has is left alone)",
		p.docGroups(),
		["Hampton Inn · Pat", "Hampton Inn · Sam", "Hampton Inn · Whole crew", "Enterprise"]
	);
	p.doc("room-sam").click();
	await flush();
	check("...and so does the picture viewer's line under a file's name", p.document.body.find("ti-viewer-sub").map((e) => e.textContent), ["Other · Hampton Inn · Sam"]);
	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-2&view=docs");
	p.session.next = rooms("EMP-2", [
		room("first-night", "r1", undefined, ["2026-09-28", "2026-09-29"]),
		room("second-night", "r2", undefined, ["2026-09-29", "2026-09-30"]),
	]);
	await p.answer("TRIP-A");
	check(
		"...and by when on one person's (a split stay)",
		p.docGroups(),
		[`Hampton Inn · ${shortDay("2026-09-28")} – ${shortDay("2026-09-29")}`, `Hampton Inn · ${shortDay("2026-09-29")} – ${shortDay("2026-09-30")}`]
	);

	p = loadItinerary("/itinerary?trip=TRIP-B&view=docs");
	await p.answer("TRIP-B");
	check(
		"the Documents screen of a trip with no files (an answer with no `documents` at all) says so, for the person shown, and still offers the day list",
		[p.empty(), p.screens()],
		[["No files for you on this trip yet."], ["Day by day", "Documents (0)"]]
	);
	await p.screen("Day by day");
	check("...which, with nothing of the page's behind it, is a new entry", [p.urls(), p.days(), p.screens()], [["push /itinerary?trip=TRIP-B"], 0, []]);

	// One person's list leaves out what is not theirs: it never says the trip has none.
	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-3&view=docs");
	p.session.next = rooms("EMP-3", []);
	await p.answer("TRIP-A");
	check("someone else's empty list says whose it is, not that the trip has no documents", p.empty(), ["No files for Alex on this trip yet."]);
	p = loadItinerary("/itinerary?trip=TRIP-A&as=crew&view=docs");
	p.session.next = rooms(null, []);
	await p.answer("TRIP-A");
	check("...and only the whole crew's says the trip has none", p.empty(), ["No documents for this trip yet."]);

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

// The Contacts card and "Print / save as PDF": what the answer sends for the person shown, drawn
// as text and safe links, and neither of them ever a history entry.
async function testItineraryContacts() {
	let p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	check(
		"the Contacts card is at the top of the trip: under the person picker, above the screens and the print link",
		p.order().filter((c) => c !== "ti-header" && c !== "ti-switcher"),
		["ti-trip-meta", "ti-people", "ti-contacts", "ti-screens", "ti-print", "ti-day", "ti-footer"]
	);
	check(
		"...shut on a first visit, its button one line naming the parts it has, 911 last, and not a part the answer leaves out (no job site)",
		[p.contactsOpen(), p.contactsToggle().attrs["aria-expanded"], p.contactsToggle().attrs["aria-controls"], p.contactsLine()],
		[false, "false", "ti-contacts-body", "Contacts & emergency · travel desk, booked by, trip lead, 1 hotel, 911"]
	);
	const firstCard = p.contacts();
	const beforeOpen = p.browser.calls.length;
	await p.toggleContacts();
	check(
		"...a tap opens it in place, writing no history and fetching nothing: 911 first, then each part the answer sends",
		[p.contactsOpen(), p.contactsToggle().attrs["aria-expanded"], p.contacts() === firstCard, p.contactsLine(), p.browser.calls.length, p.browser.tasks.length, p.pending(), p.contactRoles()],
		[true, "true", true, "Contacts & emergency", beforeOpen, 0, [], ["Emergency", "Travel desk", "Booked by", "Trip lead", "Hotel"]]
	);
	check(
		"...with Pat's own hotel only, its address one line of text",
		[p.contactNames(), p.contactSubs()],
		[["Sapphire travel desk", "Nik", "Pat", "Hampton Inn"], ["1 Main St, Las Vegas, NV"]]
	);
	check(
		"every phone is a tel: link of its digits alone (an extension dropped, 911 included), every email a mailto:, then the hotel's map links",
		p.contactLinks().map((l) => l.href),
		["tel:911", "tel:6025550100", "mailto:travel@example.com", "mailto:nik@example.com", "tel:6025550111", "tel:7025550142", URGENT + "1%20Main%20St", DIRECTIONS + "1%20Main%20St"]
	);
	check(
		"...showing 911 as 'Call 911', every other number as it was typed, and an email as a short 'Email'",
		p.contactLinks().slice(0, 3).map((l) => l.text),
		["Call 911", "(602) 555-0100 ext. 12", "Email"]
	);
	check(
		"...each link naming who it reaches and where, for a screen reader (the address is not on the button)",
		p.contactLinks().map((l) => l.label),
		[
			"Call 911",
			"Call Sapphire travel desk, (602) 555-0100 ext. 12",
			"Email Sapphire travel desk, travel@example.com",
			"Email Nik, nik@example.com",
			"Call Pat, 602-555-0111",
			"Call Hampton Inn, (702) 555-0142",
			"Urgent care near Hampton Inn (opens in a new tab)",
			"Directions to Hampton Inn (opens in a new tab)",
		]
	);
	check(
		"...a call or an email opens in place (the phone's dialer or mail app), and urgent care and directions in a new tab",
		p.contactLinks().map((l) => [l.text, l.target, l.rel]).filter((l, i) => i < 1 || i > 5),
		[["Call 911", null, null], ["Urgent care nearby", "_blank", "noopener"], ["Directions", "_blank", "noopener"]]
	);

	const print = p.print();
	check(
		"'Print / save as PDF' opens this person's own trip sheet in a new tab",
		[print.href, print.target, print.rel, p.text("ti-print-sub")],
		[SHEET + "&as=EMP-1", "_blank", "noopener", ["Your trip sheet"]]
	);
	const calls = p.browser.calls.length;
	print.click();
	await p.browser.settle();
	check("...and tapping it writes no history and fetches nothing", [p.browser.calls.length, p.pending(), p.browser.left], [calls, [], null]);

	const card = p.contacts();
	await p.toggleContacts();
	check(
		"tapping the card's heading again shuts it in place, back to its one line",
		[p.contactsOpen(), p.contactsToggle().attrs["aria-expanded"], p.contacts() === card, card.find("ti-contacts-summary")[0].hidden, p.contactsLine()],
		[false, "false", true, false, "Contacts & emergency · travel desk, booked by, trip lead, 1 hotel, 911"]
	);
	check("...writing no history and fetching nothing", [p.browser.calls.length, p.browser.tasks.length, p.pending()], [calls, 0, []]);
	await p.screen("Documents");
	check(
		"...and it stays shut when the page redraws, with no storage to keep it in (the Documents screen has it too)",
		[p.onScreen(), p.contactsOpen()],
		[["Documents (3)"], false]
	);
	await p.toggleContacts();
	check(
		"...and opens again, still writing no history (the one entry since is the Documents tap)",
		[p.contactsOpen(), p.browser.calls.length, p.contacts().find("ti-contacts-summary")[0].hidden],
		[true, calls + 1, true]
	);

	p = loadItinerary("/itinerary?trip=TRIP-A&as=EMP-2");
	await p.answer("TRIP-A");
	check(
		"someone else's view has their hotel, not the viewer's, and their own sheet",
		[p.contactNames().slice(-1), p.contactLinks().filter((l) => l.href.startsWith("tel:")).map((l) => l.href), p.print().href, p.text("ti-print-sub")],
		[["Harborview Suites"], ["tel:911", "tel:6025550100", "tel:6025550111", "tel:+17025550199"], SHEET + "&as=EMP-2", ["Sam's trip sheet"]]
	);
	await p.pick("Whole crew");
	await p.answer("TRIP-A");
	check(
		"the whole crew's view has every hotel on the trip, counted in its line, and prints the whole trip",
		[p.contactNames().slice(-2), p.contactsLine(), p.print().href, p.text("ti-print-sub")],
		[["Hampton Inn", "Harborview Suites"], "Contacts & emergency · travel desk, booked by, trip lead, 2 hotels, 911", SHEET, ["The whole trip"]]
	);

	// Remembered in this browser, per trip, when there is storage.
	const storage = makeStorage();
	p = loadItinerary("/itinerary?trip=TRIP-A", { storage });
	await p.answer("TRIP-A");
	check("with storage, a card nobody has opened writes nothing down", [p.contactsOpen(), storage.map.size], [false, 0]);
	await p.toggleContacts();
	check("...an open card is written down, for that trip alone", [...storage.map.entries()], [["ti-contacts-open:TRIP-A", "1"]]);
	p = loadItinerary("/itinerary?trip=TRIP-A", { storage });
	await p.answer("TRIP-A");
	check("...so a trip someone opened stays open on the next visit", [p.contactsOpen(), p.contactsToggle().attrs["aria-expanded"]], [true, "true"]);
	const withContacts = (message) => ({
		status: 200,
		body: { message: Object.assign({ trip: "TRIP-X", purpose: "Trip X", status: "Booked", start_date: iso(3), end_date: iso(5), days: [], viewing: null, crew: [] }, message) },
	});
	p = loadItinerary("/itinerary?trip=TRIP-X", { storage });
	p.session.next = withContacts({ contacts: tripAContacts(null) });
	await p.answer("TRIP-X");
	check("...while another trip's card is shut", p.contactsOpen(), false);
	p = loadItinerary("/itinerary?trip=TRIP-A", { storage });
	await p.answer("TRIP-A");
	await p.toggleContacts();
	check("...and shutting it again forgets it: shut is where every trip starts", [p.contactsOpen(), storage.map.size], [false, 0]);

	p = loadItinerary("/itinerary?trip=TRIP-A", { storage: makeStorage(true) });
	await p.answer("TRIP-A");
	check("storage that refuses: the card still draws, shut", p.contactsOpen(), false);
	await p.toggleContacts();
	check("...and still opens", [p.contactsOpen(), p.errors()], [true, 0]);

	// What is typed in is text, and only what is safe to follow is a link.
	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({
		contacts: {
			emergency: "911",
			office: { label: "Office", phone: null, email: " " },
			booked_by: { name: "<img src=x onerror=alert(1)>", phone: "1-800-FLOWERS", email: "a@b.co?cc=x@evil.example" },
			lead: null,
			site: { label: "PRJ-0042 Bellagio", contact_name: "Jane Doe", phone: "tel:+1 (702) 555-0123;ext=4", email: "jane@example.com", address: "3600 S Las Vegas Blvd\nLas Vegas, NV" },
			hotels: [
				{ name: "Motel", phone: "12", address: "", urgent_care_url: "javascript:alert(1)", directions_url: "http://maps.example/x" },
				null,
				{ name: "", phone: "", address: "" },
				{ name: "Inn", phone: "Front desk: 702-555-0150", address: "", urgent_care_url: null, directions_url: null },
				{ name: "Lodge", phone: "555-CALL-123", address: "", urgent_care_url: null, directions_url: null },
			],
		},
		sheet_url: "javascript:alert(1)",
		my_sheet_url: "//evil.example/sheet.pdf",
	});
	await p.answer("TRIP-X");
	check(
		"a part with nothing to call, write or find is no row (a travel desk with no phone or email, an empty hotel)",
		p.contactRoles(),
		["Emergency", "Booked by", "Job site", "Hotel", "Hotel", "Hotel"]
	);
	check(
		"...and no word in the card's line, which names the job site as 'site'",
		p.contactsLine(),
		"Contacts & emergency · booked by, site, 3 hotels, 911"
	);
	check(
		"a name that looks like markup is drawn as the text it is, and no element comes of it",
		[p.contactNames()[0], p.tags().includes("IMG")],
		["<img src=x onerror=alert(1)>", false]
	);
	check(
		"a number spelled in letters (which would dial 1800), one in two pieces or too short, and an address that is not one, are shown as typed, never linked",
		p.contactSubs(),
		["1-800-FLOWERS", "a@b.co?cc=x@evil.example", "Jane Doe", "3600 S Las Vegas Blvd, Las Vegas, NV", "12", "555-CALL-123"]
	);
	check(
		"the job site gets a call, an email and directions to its address; a hotel link that is not https is dropped; a note beside a number is not dialed",
		p.contactLinks().map((l) => l.href),
		["tel:911", "tel:+17025550123", "mailto:jane@example.com", DIRECTIONS + encodeURIComponent("3600 S Las Vegas Blvd, Las Vegas, NV"), "tel:7025550150"]
	);
	check(
		"...its call and email reaching the site's contact by name",
		p.contactLinks().slice(1, 3).map((l) => l.label),
		["Call Jane Doe, tel:+1 (702) 555-0123;ext=4", "Email Jane Doe, jane@example.com"]
	);
	check("a sheet address that is neither this site's path nor a web address is no print link", p.print(), null);

	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({
		viewing: "EMP-4",
		crew: [{ employee: "EMP-4", employee_name: "Dana" }],
		contacts: { emergency: "911", office: null, booked_by: null, lead: null, site: null, hotels: [] },
		sheet_url: SHEET,
	});
	await p.answer("TRIP-X");
	check(
		"an answer with only 911 draws only 911; one with no sheet of the person's own prints the whole trip",
		[p.contactRoles(), p.contactsLine(), p.print().href, p.text("ti-print-sub")],
		[["Emergency"], "Contacts & emergency · 911", SHEET, ["The whole trip"]]
	);

	// Most trip leads have no work cell on file, and a job site's contact can be a name alone (an
	// Opportunity's contact_display). Known only by name, each is still a row and a word with
	// nothing to tap, as the sheet, the email and Plan a Trip list them. A site with nothing but
	// its job's name is nobody to call: no row, no word.
	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({
		contacts: {
			emergency: "911",
			office: null,
			booked_by: null,
			lead: { name: "Ann Rivera", phone: null },
			site: { label: "Civic Plaza", contact_name: "Sam Ortega", phone: null, email: null, address: null },
			hotels: [],
		},
	});
	await p.answer("TRIP-X");
	check(
		"a trip lead or a site contact known only by name is a row and a word, with nothing to tap",
		[p.contactRoles(), p.contactsLine(), p.contactLinks().map((l) => l.href)],
		[["Emergency", "Trip lead", "Job site"], "Contacts & emergency · trip lead, site, 911", ["tel:911"]]
	);
	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({
		contacts: {
			emergency: "911",
			office: null,
			booked_by: null,
			lead: null,
			site: { label: "Civic Plaza", contact_name: null, phone: null, email: null, address: null },
			hotels: [],
		},
	});
	await p.answer("TRIP-X");
	check("...but a job site with only the job's name is no row and no word", [p.contactRoles(), p.contactsLine()], [["Emergency"], "Contacts & emergency · 911"]);

	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({
		viewing: "EMP-4",
		crew: [{ employee: "EMP-4", employee_name: "Dana" }],
		contacts: { emergency: "911", office: null, booked_by: null, lead: null, site: null, hotels: [] },
		sheet_url: SHEET,
		// A browser reads "/\host" as "//host": another site.
		my_sheet_url: "/\\evil.example/sheet.pdf",
	});
	await p.answer("TRIP-X");
	check(
		"a person's sheet address with a backslash is no link: the whole trip's is printed instead",
		[p.print().href, p.text("ti-print-sub")],
		[SHEET, ["The whole trip"]]
	);

	p = loadItinerary("/itinerary?trip=TRIP-B");
	await p.answer("TRIP-B");
	check(
		"an answer with no `contacts` and no sheet (an older server) draws neither, and the trip as before",
		[p.contacts(), p.print(), p.shown(), p.errors()],
		[null, null, "Trip B", 0]
	);
	p = loadItinerary("/itinerary?trip=TRIP-X", { trips: [] });
	p.session.next = withContacts({ contacts: null });
	await p.answer("TRIP-X");
	check("...nor does `contacts: null`", [p.contacts(), p.shown()], [null, "Trip X"]);
	check("every push was paid for by a tap", p.browser.unactivated, 0);
}

// ---------------------------------------------------------------------------- /itinerary place notes

// Trip A's answer with one stop per place given, each as shape_itinerary sends it: the place
// (`poi`) carries its own `notes` since v1.553.0. The first stop has notes of its own for the
// visit too, which are not the place's.
function stopsAnswer(pois) {
	const items = pois.map((poi, i) => ({
		type: "agenda",
		date: iso(0),
		sort_time: "",
		activity: `Stop ${i + 1}`,
		related_party: null,
		visit_notes: i === 0 ? "Bring the spare pump." : null,
		poi,
		group: null,
	}));
	return { status: 200, body: { message: {
		trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(0), end_date: iso(0), crew: [], viewing: "EMP-1",
		days: [{ date: iso(0), items }],
	} } };
}

// Enough of Leaflet for renderDayMap: every marker's popup, as the page built it, lands in `popups`.
function fakeLeaflet(popups) {
	const chain = { addTo() { return this; }, setView() { return this; }, fitBounds() { return this; } };
	return {
		map: () => Object.create(chain),
		tileLayer: () => Object.create(chain),
		marker: () => Object.assign(Object.create(chain), {
			bindPopup(content) {
				popups.push(content);
				return this;
			},
		}),
	};
}

// Every tag under `node`, itself left out.
function tagsUnder(node) {
	const out = [];
	const walk = (n) => n.children.forEach((c) => {
		out.push(c.tagName);
		walk(c);
	});
	walk(node);
	return out;
}

async function testItineraryPlaceNotes() {
	console.log("/itinerary place notes");
	const GATE = "Park at the north gate.\nGate code 4471#";
	const SITE = { name: "POI-1", poi_name: "The site", category: "Job Site", lat: 33.4, lng: -112, notes: GATE };
	const at = (extra) => Object.assign({}, SITE, extra);

	let p = loadItinerary("/itinerary?trip=TRIP-A");
	p.session.next = stopsAnswer([SITE]);
	await p.answer("TRIP-A");
	const card = p.root.find("ti-agenda")[0];
	check(
		"a stop's place notes are drawn on its card under a label, line breaks and all",
		[p.text("ti-place-notes-label"), p.text("ti-place-notes-text")],
		[["Place notes"], [GATE]]
	);
	check(
		"...after the visit's own notes and before the Open in Maps link",
		card.children.map((c) => c.className.split(" ")[0]),
		["ti-card-kicker", "ti-card-title", "ti-card-sub", "ti-notes", "ti-place-notes", "ti-maps-link"]
	);
	check(
		"...short ones whole, not behind a tap, as text and nothing else",
		[card.find("ti-place-notes").map((n) => n.tagName), tagsUnder(card.find("ti-place-notes-text")[0])],
		[["DIV"], []]
	);
	check("...with no history call and nothing more asked for", [p.browser.calls, p.pending(), p.fileFetches], [[], [], []]);

	// Markup typed into a place's notes is text: the page never parses it.
	const HOSTILE = '<script>alert("x")</script>\n<b>Gate</b> & <img src=x onerror=alert(1)>';
	p = loadItinerary("/itinerary?trip=TRIP-A");
	p.session.next = stopsAnswer([at({ notes: HOSTILE })]);
	await p.answer("TRIP-A");
	check(
		"a <script> or <b> in the notes is drawn as those characters, never as a tag",
		[p.text("ti-place-notes-text"), p.tags().filter((t) => ["SCRIPT", "B", "IMG"].includes(t)), p.document.head.children.length],
		[[HOSTILE], [], 0]
	);

	// Long notes start shut, on one line: the label and their first line.
	const LONG = [
		"Gate B off 5th St; the code is 4471#.",
		"Park on the gravel pad, never the lawn.",
		"Sign in at the trailer before you unload.",
		"Hard hats past the fence.",
		"The water shutoff is behind the pump house.",
	].join("\n");
	const WIDE = "Use the loading dock on the east side, ".repeat(6).trim();
	p = loadItinerary("/itinerary?trip=TRIP-A");
	p.session.next = stopsAnswer([at({ notes: LONG }), at({ notes: WIDE })]);
	await p.answer("TRIP-A");
	const boxes = p.root.find("ti-place-notes");
	check(
		"long notes (five lines, or over 200 characters on one) are a <details> that starts shut",
		boxes.map((b) => [b.tagName, !!b.open, "open" in b.attrs]),
		[["DETAILS", false, false], ["DETAILS", false, false]]
	);
	check(
		"...its one line the label and the notes' first line, the whole notes inside",
		[boxes.map((b) => [b.children[0].tagName, b.children[0].textContent]), p.text("ti-place-notes-text")],
		[[["SUMMARY", "Place notesGate B off 5th St; the code is 4471#."], ["SUMMARY", `Place notes${WIDE}`]], [LONG, WIDE]]
	);
	const before = p.browser.calls.length;
	boxes[0].children[0].click();
	await flush();
	check("...and a tap on it writes no history and asks for nothing", [p.browser.calls.length, p.pending()], [before, []]);

	// Nothing to say, nothing drawn.
	p = loadItinerary("/itinerary?trip=TRIP-A");
	p.session.next = stopsAnswer([at({ notes: "  \n\t " }), at({ notes: null }), { name: "POI-2", poi_name: "Depot", category: "Supply Depot", lat: null, lng: null }]);
	await p.answer("TRIP-A");
	check(
		"blank, null or missing notes draw nothing (an older server sends no `notes`)",
		[p.root.find("ti-agenda").length, p.root.find("ti-place-notes").length, p.errors()],
		[3, 0, 0]
	);

	// The marker's popup on the day's map carries them too.
	p = loadItinerary("/itinerary?trip=TRIP-A");
	p.session.next = stopsAnswer([SITE, at({ lat: 33.5, notes: HOSTILE }), at({ lat: 33.6, notes: null })]);
	await p.answer("TRIP-A");
	const popups = [];
	p.browser.window.L = fakeLeaflet(popups);
	p.root.find("ti-map-btn")[0].click();
	await flush();
	check(
		"each marker's popup carries its place's notes, as text",
		popups.map((box) => box.find("ti-popup-notes").map((n) => [n.textContent, tagsUnder(n)])),
		[[[GATE, []]], [[HOSTILE, []]], []]
	);
	check("...and the Map tap writes no history", p.browser.calls, []);

	// They come in the answer, so the copy saved on the phone has them.
	const PAT = "pat@example.com";
	const KEY = "0123456789abcdef".repeat(4);
	const phone = makeIndexedDB();
	const onPhone = () => ({ user: PAT, key: KEY, cookie: `user_id=pat%40example.com; full_name=Pat; ee_itinerary_key=${KEY}`, indexedDB: phone });
	p = loadItinerary("/itinerary?trip=TRIP-A", onPhone());
	p.session.next = stopsAnswer([SITE]);
	await p.answer("TRIP-A");
	check("the notes are saved on the phone with the answer", phone.get("answers", `${PAT}|TRIP-A|`).answer.days[0].items[0].poi.notes, GATE);
	p = loadItinerary("/itinerary?trip=TRIP-A", onPhone());
	await p.answer("TRIP-A", false);
	check(
		"offline, the saved copy draws them, and nothing more is fetched for them",
		[p.offline().length, p.text("ti-place-notes-text"), p.pending(), p.fileFetches],
		[1, [GATE], [], []]
	);

	// Line breaks are kept by the stylesheet, and a long popup note scrolls inside the popup.
	const css = stripComments(fs.readFileSync(path.join(APP, "public", "css", "travel", "itinerary.css"), "utf8"));
	const rule = (selector) => {
		const m = css.match(new RegExp(`(?:^|})\\s*${selector.replace(/\./g, "\\.")}\\s*\\{([^}]*)\\}`));
		return m ? m[1] : "";
	};
	check(
		"the notes keep their line breaks (white-space: pre-wrap) on the card and in the popup",
		[/white-space:\s*pre-wrap/.test(rule(".ti-place-notes-text")), /white-space:\s*pre-wrap/.test(rule(".ti-popup-notes"))],
		[true, true]
	);
	check("every push was paid for by a tap", p.browser.unactivated, 0);
}

// ---------------------------------------------------------------------------- /itinerary offline

/**
 * A stand-in IndexedDB, enough of one for itinerary.js: open() with an upgrade the first time,
 * object stores with out-of-line keys, get / put / delete / getAll / getAllKeys, and transactions
 * over one store or several that complete once their requests are done. Every request answers
 * asynchronously and in the order it was made, as the real one does, and values are copied in and
 * out (structured clone). `opts.openThrows`: open() throws, as a storage-blocked profile's can.
 * `keys(store)` and `get(store, key)` read what the page left; `seed` puts a record there first.
 */
function makeIndexedDB(opts) {
	opts = opts || {};
	const stores = new Map();
	let upgraded = false;
	const idb = {
		open() {
			if (opts.openThrows) throw new Error("SecurityError: storage is blocked");
			const request = { result: null, error: null };
			setImmediate(() => {
				request.result = makeDb();
				if (!upgraded) {
					upgraded = true;
					if (request.onupgradeneeded) request.onupgradeneeded();
				}
				if (request.onsuccess) request.onsuccess();
			});
			return request;
		},
		keys: (store) => [...(stores.get(store) || new Map()).keys()].sort(),
		get: (store, key) => clone((stores.get(store) || new Map()).get(key)),
		seed(store, key, value) {
			if (!stores.has(store)) stores.set(store, new Map());
			stores.get(store).set(key, clone(value));
		},
	};
	function makeDb() {
		return {
			objectStoreNames: { contains: (name) => stores.has(name) },
			createObjectStore(name) {
				stores.set(name, new Map());
				return {};
			},
			transaction: (names, mode) => makeTx([].concat(names), mode),
			close() {},
		};
	}
	function makeTx(names, mode) {
		const tx = { pending: 0, done: false };
		const settle = () =>
			setImmediate(() => {
				if (tx.pending === 0 && !tx.done) {
					tx.done = true;
					if (tx.oncomplete) tx.oncomplete();
				}
			});
		tx.objectStore = (name) => {
			if (!names.includes(name) || !stores.has(name)) throw new Error(`NotFoundError: ${name}`);
			const data = stores.get(name);
			const request = (run) => {
				const r = { result: undefined };
				tx.pending += 1;
				queueMicrotask(() => {
					r.result = run();
					tx.pending -= 1;
					if (r.onsuccess) r.onsuccess();
					settle();
				});
				return r;
			};
			const write = (run) => {
				if (mode !== "readwrite") throw new Error("ReadOnlyError");
				return request(run);
			};
			const sorted = () => [...data.keys()].sort();
			return {
				get: (key) => request(() => (data.has(key) ? clone(data.get(key)) : undefined)),
				put: (value, key) => write(() => (data.set(key, clone(value)), key)),
				delete: (key) => write(() => void data.delete(key)),
				clear: () => write(() => void data.clear()),
				getAll: () => request(() => sorted().map((key) => clone(data.get(key)))),
				getAllKeys: () => request(sorted),
			};
		};
		tx.abort = () => {
			tx.done = true;
			if (tx.onabort) tx.onabort();
		};
		settle();
		return tx;
	}
	return idb;
}

/**
 * navigator.serviceWorker, recording what the page registers and every message it posts.
 * `kiosk`: the phone already has the Time Kiosk's worker, active at "/" (`sw.kiosk` records what
 * reaches it), and this is the first visit: /itinerary's worker is installing when register()
 * answers and activates a moment later. `ready` is then the kiosk's registration, as in a
 * browser: it matches the page and is the one already active.
 */
function makeServiceWorker(opts) {
	opts = opts || {};
	const sw = { registered: [], posted: [], kiosk: [] };
	const itinerary = { state: "activated", listeners: [], addEventListener(type, fn) { this.listeners.push(fn); }, postMessage: (message) => sw.posted.push(clone(message)) };
	const registration = opts.kiosk ? { installing: itinerary, waiting: null, active: null } : { active: itinerary };
	if (opts.kiosk) itinerary.state = "installing";
	const kiosk = { scope: `${ORIGIN}/`, active: { postMessage: (message) => sw.kiosk.push(clone(message)) } };
	sw.container = {
		register(url, options) {
			sw.registered.push({ url, options: clone(options) });
			if (opts.refuses) return Promise.reject(new Error("SecurityError"));
			if (opts.kiosk) {
				setTimeout(() => {
					itinerary.state = "activated";
					registration.installing = null;
					registration.active = itinerary;
					itinerary.listeners.forEach((fn) => fn({}));
				}, 1);
			}
			return Promise.resolve(registration);
		},
		ready: Promise.resolve(opts.kiosk ? kiosk : registration),
	};
	return sw;
}

/** setTimeout for a page, where time passes only when the test says: `run(ms)` fires every timer set for that long or less. */
function makeTimers() {
	const pending = [];
	let seq = 0;
	return {
		set(fn, ms) {
			seq += 1;
			pending.push({ id: seq, fn, ms: Number(ms) || 0 });
			return seq;
		},
		clear(id) {
			const i = pending.findIndex((t) => t.id === id);
			if (i >= 0) pending.splice(i, 1);
		},
		async run(ms) {
			for (const t of pending.filter((x) => x.ms <= ms)) {
				pending.splice(pending.indexOf(t), 1);
				t.fn();
			}
			await flush();
		},
		waiting: () => pending.map((t) => t.ms),
	};
}

// Offline: every answer is kept on the phone, the person's upcoming trips are saved ahead with
// their files, and with no signal at all the saved copy is drawn where the answer would have
// been, with no history call. Only ever for the person it was saved for.
async function testItineraryOffline() {
	console.log("/itinerary offline");
	const PAT = "pat@example.com";
	const SAM = "sam@example.com";
	// Offline markers (travel_management/itinerary_offline.py): 64 hex characters, never an email.
	const PAT_KEY = "0123456789abcdef".repeat(4);
	const SAM_KEY = "fedcba9876543210".repeat(4);
	const MARKER = `ee_itinerary_key=${PAT_KEY}`;
	const COOKIE = `user_id=pat%40example.com; full_name=Pat; ${MARKER}`;
	const DAY = 24 * 60 * 60 * 1000;
	const PAT_FILES = ["/private/files/pat-pass.png", "/private/files/wn1-confirmation.pdf", "/private/files/site-map.pdf"];
	const phone = makeIndexedDB();
	let sw = makeServiceWorker();
	const online = (extra) => Object.assign({ user: PAT, key: PAT_KEY, cookie: COOKIE, indexedDB: phone, serviceWorker: sw, build: "1727" }, extra || {});
	// A phone holding exactly what `idb` holds: for the checks that delete it.
	const copyOf = (idb) => {
		const out = makeIndexedDB();
		for (const store of ["answers", "trips"]) for (const k of idb.keys(store)) out.seed(store, k, idb.get(store, k));
		return out;
	};

	// Online, on a phone with both.
	let p = loadItinerary("/itinerary", online());
	await flush();
	check(
		"the page registers its own worker, for /itinerary only (never the site root: that is the kiosk's), at this deploy's address",
		sw.registered,
		[{ url: "/itinerary-sw.js?v=1727", options: { scope: "/itinerary" } }]
	);
	check("...and tells it who is signed in, by their marker (never their email)", sw.posted, [{ type: "user", key: PAT_KEY }]);
	await p.answer("TRIP-A");
	check("the answer on screen is saved on the phone, for that person, trip and view", phone.keys("answers"), [`${PAT}|TRIP-A|`]);
	const kept = phone.get("answers", `${PAT}|TRIP-A|`);
	check(
		"...the answer itself, with the marker it was saved under and when",
		[kept.user, kept.key, kept.answer.trip, kept.answer.viewing, typeof kept.saved_at],
		[PAT, PAT_KEY, "TRIP-A", "EMP-1", "number"]
	);
	check("...and the boot's trip list with it, under the same marker", [phone.get("trips", PAT).key, phone.get("trips", PAT).trips.map((t) => t.name)], [PAT_KEY, TRIPS.map((t) => t.name)]);
	check(
		"its pictures and PDFs go to the worker to keep under the marker, at this site's own file addresses",
		sw.posted.slice(1),
		[{ type: "cache-files", key: PAT_KEY, urls: PAT_FILES }]
	);
	check("their own upcoming trips are saved ahead, one at a time: Trip B starts within two weeks", p.asked(), [["TRIP-B", null]]);
	await p.answer("TRIP-B");
	check(
		"...quietly: the screen and the address are as they were, and Trip B is saved",
		[p.shown(), p.urls(), phone.keys("answers")],
		["Trip A", ["replace /itinerary?trip=TRIP-A"], [`${PAT}|TRIP-A|`, `${PAT}|TRIP-B|`]]
	);
	check("...never a trip they only own (Trip O) nor one that has ended (Trip C)", p.asked(), []);
	await p.tap("Trip B");
	await p.answer("TRIP-B");
	check("...and only once a page: opening another trip saves it and asks for nothing more", [p.shown(), p.asked()], ["Trip B", []]);
	await p.pick("Sam");
	await p.answer("TRIP-B", "EMP-2");
	check("another person's view is saved as that view", phone.keys("answers"), [`${PAT}|TRIP-A|`, `${PAT}|TRIP-B|`, `${PAT}|TRIP-B|EMP-2`]);

	// No signal: a new page load, on the same phone.
	sw = makeServiceWorker();
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ files: { "/private/files/wn1-confirmation.pdf": "application/pdf" }, blobs: true }));
	await p.answer("TRIP-A", false);
	check("no signal: the copy saved on this phone is drawn, as it was", [p.shown(), p.has("PNR: PNR-PAT"), p.errors()], ["Trip A", true, 0]);
	check(
		"...under a line saying so, and when it was saved",
		/^You're offline — showing your itinerary as saved \w{3}, \w{3} \d{1,2}, \d{1,2}:\d{2} [AP]M\.$/.test(p.offline()[0] || ""),
		true
	);
	check("...first thing under the header", p.order().slice(0, 2), ["ti-header", "ti-offline"]);
	check("...and no history call", p.browser.calls, []);
	await p.screen("Documents");
	check(
		"the Documents screen works from the saved copy: one entry, from the tap, nothing asked for",
		[p.urls(), p.pending(), p.docs(), p.offline().length],
		[["push /itinerary?trip=TRIP-A&view=docs"], [], ["Site map", "Pat's boarding pass", "Southwest confirmation"], 1]
	);
	p.doc("Southwest confirmation").click();
	await flush();
	check(
		"offline, a PDF opens from the phone's copy, in a tab the page opened at the tap",
		[p.tabs(), p.fileFetches],
		[[{ opened: ["", "_blank"], url: "blob:/private/files/wn1-confirmation.pdf", closed: false }], ["/private/files/wn1-confirmation.pdf"]]
	);
	p.doc("Site map").click();
	await flush();
	check(
		"...a file this phone has no copy of says so, and its tab is closed again",
		[p.tabs()[1].closed, p.text("ti-doc-note")],
		[true, ["This file isn't saved on this phone. It opens once you're back online."]]
	);
	check("...no history call either way", p.urls(), ["push /itinerary?trip=TRIP-A&view=docs"]);
	p.browser.back();
	await p.browser.settle();
	check("Back is the day list again, still the saved copy, asking for nothing", [p.view(), p.days(), p.offline().length, p.pending()], [null, 1, 1, []]);
	await p.tap("Trip B");
	await p.answer("TRIP-B", false);
	check("a trip saved ahead opens with no signal too", [p.shown(), p.offline().length, p.urls().slice(-1)], ["Trip B", 1, ["push /itinerary?trip=TRIP-B"]]);
	await p.pick("Sam");
	await p.answer("TRIP-B", false);
	check("another person's view that was saved opens as theirs", [p.title(), p.offline().length, p.as()], ["Sam's itinerary", 1, "EMP-2"]);
	await p.pick("Whole crew");
	await p.answer("TRIP-B", false);
	check(
		"a view never saved says so, and draws nothing stale",
		[p.shown(), p.text("ti-error"), p.offline()],
		[null, ["You're offline, and this itinerary isn't saved on this phone yet."], []]
	);
	p.browser.back();
	await p.browser.settle();
	await p.answer("TRIP-B", false);
	p.browser.back();
	await p.browser.settle();
	await p.answer("TRIP-B", false);
	check("Back and Back again: each saved view as it was", [p.shown(), p.title(), p.offline().length, p.as()], ["Trip B", "My Itinerary", 1, null]);
	check("every push was paid for by a tap", p.browser.unactivated, 0);

	// Online again, with a saved copy on screen: the server's answer replaces it where it stands.
	p = loadItinerary("/itinerary?trip=TRIP-A", online());
	await p.answer("TRIP-A", false);
	p.browser.fire("online", {});
	await flush();
	check("back online, the trip on screen is asked for again, quietly", p.asked(), [["TRIP-A", null]]);
	await p.answer("TRIP-A");
	check("...and the answer replaces the saved copy, with no history call", [p.offline(), p.shown(), p.browser.calls], [[], "Trip A", []]);

	// Online, a PDF is a link, as it always was.
	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs", online({ blobs: true }));
	await p.answer("TRIP-A");
	p.doc("Southwest confirmation").click();
	await flush();
	check("online, a PDF tap is the link's own (a new tab): the page opens nothing and fetches nothing", [p.tabs(), p.fileFetches], [[], []]);
	p = loadItinerary("/itinerary?trip=TRIP-A&view=docs", online({ blobs: true, onLine: false }));
	await p.answer("TRIP-A");
	p.doc("Southwest confirmation").click();
	await flush();
	check("...but a phone that says it has no connection opens the saved copy, even over a fresh answer", p.fileFetches, ["/private/files/wn1-confirmation.pdf"]);

	// While "Report a problem" is open, the saved copy is still just drawn.
	p = loadItinerary("/itinerary?trip=TRIP-A", online());
	p.capture.open = true;
	await p.answer("TRIP-A", false);
	check("with Report a problem open: the saved copy, and still no history call", [p.shown(), p.browser.calls], ["Trip A", []]);

	// What the worker is asked to keep: this site's pictures and PDFs, 20 a trip at most.
	const many = [
		{ name: "TD-ELSEWHERE", title: "Map", kind: "Site map", url: "https://files.example.com/map.pdf", file_name: "map.pdf", is_image: false, group: null },
		{ name: "TD-DOCX", title: "Notes", kind: "Job packet", url: "/private/files/notes.docx", file_name: "notes.docx", is_image: false, group: null },
		{ name: "TD-PHOTO", title: "Gate", kind: "Boarding pass", url: "/files/gate.jpg", file_name: "gate.jpg", is_image: true, group: null },
	];
	for (let i = 1; i <= 25; i++) {
		many.push({ name: `TD-${i}`, title: `Page ${i}`, kind: "Job packet", url: `/private/files/page-${i}.pdf`, file_name: `page-${i}.pdf`, is_image: false, group: null });
	}
	sw = makeServiceWorker();
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: makeIndexedDB() }));
	p.session.next = { status: 200, body: { message: {
		trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(-1), end_date: iso(1), days: [],
		crew: [{ employee: "EMP-1", employee_name: "Pat" }], viewing: "EMP-1", viewer_employee: "EMP-1", viewer_on_trip: true, documents: many,
	} } };
	await p.answer("TRIP-A");
	const asked = (sw.posted.find((m) => m.type === "cache-files") || { urls: [] }).urls;
	check(
		"the worker is asked to keep this site's own pictures and PDFs only (not another site's, not a .docx), 20 a trip",
		[asked.length, asked[0], asked[1], asked[19]],
		[20, "/files/gate.jpg", "/private/files/page-1.pdf", "/private/files/page-19.pdf"]
	);

	// A phone can be shared: the saved copy is only ever the person's it was saved for, and only
	// while the phone still holds the offline marker it was saved under. Every sign-out deletes the
	// marker (the on_logout hook), and so does a sign-in as anybody else (on_login).
	const three = phone.keys("answers");
	check("(three answers saved for Pat so far)", three.length, 3);

	// A session that ran out: `user_id` is Guest, the marker is still Pat's. Pat's phone.
	sw = makeServiceWorker();
	let held = copyOf(phone);
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: held, cookie: `user_id=Guest; ${MARKER}` }));
	await flush();
	check(
		"a page drawn for Pat on a phone whose session ran out: nothing of Pat's, not even the trip list, and nothing asked for",
		[p.empty(), p.chips().length, p.pending(), p.browser.calls, p.text("ti-header-sub"), p.document.title],
		[["Sign in to see your itinerary."], 0, [], [], [], "My Itinerary"]
	);
	check("...and nothing deleted: the marker is still Pat's, so Pat's copy waits for Pat", [held.keys("answers"), sw.posted], [three, [{ type: "user", key: PAT_KEY }]]);

	// Signed out: frappe sets `user_id` to Guest, and the logout's own response deletes the marker.
	sw = makeServiceWorker();
	held = copyOf(phone);
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: held, cookie: "user_id=Guest" }));
	await flush();
	check("a page drawn for Pat on a phone signed out since: nothing of Pat's", [p.empty(), p.chips().length, p.pending(), p.browser.calls], [["Sign in to see your itinerary."], 0, [], []]);
	check(
		"...and with the marker gone, everything saved goes: answers, lists, and the worker told to purge its files and the kept page",
		[held.keys("answers"), held.keys("trips"), sw.posted],
		[[], [], [{ type: "purge" }]]
	);

	p = loadItinerary("/itinerary?trip=TRIP-A", online({ cookie: `full_name=Pat; ${MARKER}` }));
	await p.answer("TRIP-A", false);
	check(
		"no user_id cookie at all, with the marker still Pat's, is not a sign-out (a home-screen app started again drops user_id): the saved copy shows",
		[p.shown(), p.offline().length],
		["Trip A", 1]
	);

	// The gap the marker closes: Pat signed out, and the home-screen app, started again with no
	// signal, has neither cookie. The kept page is Pat's, boot and all.
	sw = makeServiceWorker();
	held = copyOf(phone);
	p = loadItinerary("/itinerary", online({ indexedDB: held, cookie: "full_name=Pat" }));
	await flush();
	check(
		"no user_id and no marker (Pat signed out, the app started again): while it waits, nothing of the kept page's boot, not the trip list, not Pat's name",
		[p.chips().length, p.text("ti-boot"), p.text("ti-header-sub"), p.document.title, p.pending()],
		[0, ["Loading itinerary…"], [], "My Itinerary", ["TRIP-A"]]
	);
	check("...and everything saved goes at once", [held.keys("answers"), held.keys("trips"), sw.posted], [[], [], [{ type: "purge" }]]);
	await p.answer("TRIP-A", false);
	check(
		"...then no signal: nothing saved is shown, and the page says to sign in",
		[p.empty(), p.shown(), p.offline(), p.chips().length, p.text("ti-header-sub")],
		[["Sign in to see your itinerary."], null, [], 0, []]
	);
	check("...and the worker is told again, with no history call beyond the boot's own", [sw.posted.slice(-1), p.urls()], [[{ type: "purge" }], ["replace /itinerary?trip=TRIP-A"]]);

	// Somebody else's marker on the page kept for Pat: nothing of Pat's either.
	sw = makeServiceWorker();
	held = copyOf(phone);
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: held, cookie: `ee_itinerary_key=${SAM_KEY}` }));
	await flush();
	check("a page kept for Pat with Sam's marker on the phone: nothing of its boot while it waits", [p.chips().length, p.text("ti-header-sub"), p.text("ti-boot")], [0, [], ["Loading itinerary…"]]);
	check("...and Sam is who the worker is told about, so Pat's answers and lists go", [sw.posted, held.keys("answers"), held.keys("trips")], [[{ type: "user", key: SAM_KEY }], [], []]);
	await p.answer("TRIP-A", false);
	check("...and offline it is refused, and the worker told to purge", [p.empty(), p.shown(), sw.posted.slice(-1)], [["Sign in to see your itinerary."], null, [{ type: "purge" }]]);

	// Online, a page drawn with another marker than the phone's (the phone would not keep the
	// cookie) is the page it always was once the server answers: only the wait draws less.
	sw = makeServiceWorker();
	held = makeIndexedDB();
	p = loadItinerary("/itinerary", online({ indexedDB: held, cookie: "user_id=pat%40example.com" }));
	await flush();
	check("no marker on the phone, online: while it waits, no trip list and no name", [p.chips().length, p.text("ti-header-sub"), p.text("ti-boot")], [0, [], ["Loading itinerary…"]]);
	await p.answer("TRIP-A");
	check(
		"...and the answer draws exactly as ever: the trip, the list, the name",
		[p.shown(), p.chips().length, p.text("ti-header-sub"), p.urls()],
		["Trip A", TRIPS.length, [PEOPLE["EMP-1"]], ["replace /itinerary?trip=TRIP-A"]]
	);
	check("...but nothing is saved without the marker, and nothing asked for ahead", [held.keys("answers"), p.pending()], [[], []]);

	sw = makeServiceWorker();
	p = loadItinerary("/itinerary", online({ indexedDB: makeIndexedDB(), cookie: "full_name=Pat" }));
	p.session.expired = true;
	await p.answer("TRIP-A");
	check(
		"...and an answer that says nobody is signed in refuses the page, rather than say whose session expired",
		[p.empty(), p.text("ti-header-sub"), p.chips().length, p.urls()],
		[["Sign in to see your itinerary."], [], 0, ["replace /itinerary?trip=TRIP-A"]]
	);

	// Signed out in another tab while the page is open: the marker and user_id change under it.
	const tabbed = copyOf(phone);
	p = loadItinerary("/itinerary", online({ indexedDB: tabbed }));
	await p.answer("TRIP-A");
	check("Trip B, saved ahead within the hour, is not asked for again on the next page load", p.asked(), []);
	await p.tap("Trip B");
	p.document.cookie = `user_id=Guest; ${MARKER}`;
	await p.answer("TRIP-B", false);
	check(
		"a session that ran out in another tab, then no signal: the saved copy is not shown",
		[p.empty(), p.shown(), p.offline(), p.chips().length],
		[["Sign in to see your itinerary."], null, [], 0]
	);
	check("...and it is kept for Pat", tabbed.keys("answers"), three);

	sw = makeServiceWorker();
	held = copyOf(phone);
	p = loadItinerary("/itinerary", online({ indexedDB: held }));
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	p.document.cookie = "user_id=Guest";
	await p.answer("TRIP-B", false);
	check(
		"signed out in another tab (the marker gone), then no signal: the saved copy is not shown",
		[p.empty(), p.shown(), p.offline(), p.chips().length],
		[["Sign in to see your itinerary."], null, [], 0]
	);
	await flush();
	check("...and everything saved goes, the worker's files and kept page too", [held.keys("answers"), held.keys("trips"), sw.posted.slice(-1)], [[], [], [{ type: "purge" }]]);
	check("...and that writes no history", p.urls(), ["replace /itinerary?trip=TRIP-A", "push /itinerary?trip=TRIP-B"]);

	// Somebody else signed in and opened /itinerary: their marker is the phone's now.
	const shared = makeIndexedDB();
	const hourAgo = Date.now() - 3600e3;
	shared.seed("answers", `${PAT}|TRIP-A|`, { user: PAT, key: PAT_KEY, trip: "TRIP-A", as: "", answer: { trip: "TRIP-A", days: [] }, saved_at: hourAgo });
	shared.seed("answers", `${SAM}|TRIP-B|`, { user: SAM, key: SAM_KEY, trip: "TRIP-B", as: "", answer: { trip: "TRIP-B", days: [] }, saved_at: hourAgo });
	shared.seed("trips", PAT, { user: PAT, key: PAT_KEY, trips: [{ name: "TRIP-A" }], saved_at: hourAgo });
	sw = makeServiceWorker();
	p = loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: shared, cookie: `user_id=sam%40example.com; ee_itinerary_key=${SAM_KEY}` }));
	await flush();
	check(
		"a page drawn for Pat with Sam signed in: nothing of Pat's, and a reload (which draws Sam's own page), never a dead end",
		[p.empty(), p.pending(), p.text("ti-reload")],
		[["This page was saved for someone else."], [], ["Reload"]]
	);
	p.root.find("ti-reload")[0].click();
	check("...which reloads the page", p.browser.reloads, 1);
	check(
		"...and Sam's marker is what the worker is told, and everything saved under any other marker goes",
		[sw.posted, shared.keys("answers"), shared.keys("trips")],
		[[{ type: "user", key: SAM_KEY }], [`${SAM}|TRIP-B|`], []]
	);

	// Opening the page keeps the phone tidy: the person's own answers for trips no longer in their
	// list go after a week, and everything under any other marker at once (their own too, saved
	// under a marker that is not the phone's now).
	const tidy = makeIndexedDB();
	const OLD_KEY = "9".repeat(64);
	tidy.seed("trips", PAT, { user: PAT, key: PAT_KEY, trips: [{ name: "TRIP-A" }], saved_at: hourAgo });
	tidy.seed("trips", SAM, { user: SAM, key: SAM_KEY, trips: [], saved_at: hourAgo });
	tidy.seed("answers", `${PAT}|TRIP-A|crew`, { user: PAT, key: PAT_KEY, trip: "TRIP-A", as: "crew", answer: { trip: "TRIP-A" }, saved_at: Date.now() - 30 * DAY });
	tidy.seed("answers", `${PAT}|TRIP-GONE|`, { user: PAT, key: PAT_KEY, trip: "TRIP-GONE", as: "", answer: { trip: "TRIP-GONE" }, saved_at: Date.now() - 8 * DAY });
	tidy.seed("answers", `${PAT}|TRIP-X|`, { user: PAT, key: PAT_KEY, trip: "TRIP-X", as: "", answer: { trip: "TRIP-X" }, saved_at: Date.now() - 2 * DAY });
	tidy.seed("answers", `${PAT}|TRIP-A|EMP-2`, { user: PAT, key: OLD_KEY, trip: "TRIP-A", as: "EMP-2", answer: { trip: "TRIP-A" }, saved_at: hourAgo });
	tidy.seed("answers", `${PAT}|TRIP-A|EMP-3`, { user: PAT, trip: "TRIP-A", as: "EMP-3", answer: { trip: "TRIP-A" }, saved_at: hourAgo });
	tidy.seed("answers", `${SAM}|TRIP-A|`, { user: SAM, key: SAM_KEY, trip: "TRIP-A", as: "", answer: { trip: "TRIP-A" }, saved_at: hourAgo });
	loadItinerary("/itinerary?trip=TRIP-A", online({ indexedDB: tidy }));
	await flush();
	check(
		"at boot: a trip still theirs is kept however old, one no longer theirs for a week, and nothing under another marker or none",
		[tidy.keys("answers"), tidy.keys("trips")],
		[[`${PAT}|TRIP-A|crew`, `${PAT}|TRIP-X|`], [PAT]]
	);

	// A boot with no marker (a server that could not make one): nothing is saved, nothing shown.
	sw = makeServiceWorker();
	held = makeIndexedDB();
	p = loadItinerary("/itinerary", online({ indexedDB: held, key: undefined }));
	await p.answer("TRIP-A");
	check("a boot with no marker saves nothing and asks for nothing ahead", [p.shown(), held.keys("answers"), p.pending()], ["Trip A", [], []]);
	await p.tap("Trip B");
	await p.answer("TRIP-B", false);
	check(
		"...and offline shows nothing saved, and is not refused: it is Pat's own session, and nothing of anybody's is deleted",
		[p.empty(), p.offline(), p.text("ti-error"), held.keys("answers")],
		[[], [], ["You're offline, and this itinerary isn't saved on this phone yet."], []]
	);

	// Without IndexedDB or a service worker (or with ones that refuse), the page is what it was.
	p = loadItinerary("/itinerary", { user: PAT, key: PAT_KEY, cookie: COOKIE });
	await p.answer("TRIP-A");
	check("no IndexedDB and no service worker: nothing is asked for ahead", [p.shown(), p.pending()], ["Trip A", []]);
	await p.tap("Trip B");
	await p.answer("TRIP-B", false);
	check("...and no signal is the error it has always been", [p.errors(), p.offline(), p.text("ti-error")], [1, [], ["Could not load the trip: offline"]]);
	for (const [label, storage] of [["storage that throws on sight", "throws"], ["storage that refuses to open", makeIndexedDB({ openThrows: true })]]) {
		sw = makeServiceWorker({ refuses: true });
		p = loadItinerary("/itinerary", { user: PAT, key: PAT_KEY, cookie: COOKIE, indexedDB: storage, serviceWorker: sw });
		await p.answer("TRIP-A");
		await p.tap("Trip B");
		await p.answer("TRIP-B", false);
		check(`${label}, and a worker that will not register: the same`, [p.errors(), p.offline(), p.text("ti-error"), p.urls().length], [1, [], ["Could not load the trip: offline"], 2]);
	}
	check("every push was paid for by a tap", p.browser.unactivated, 0);
}

// No usable answer is more than no signal: the gateway answering 502/503/504 while a deploy
// restarts the site, a 200 whose body was cut off, one bar of signal where a request hangs, a
// stale CSRF token on the page kept on the phone. Each draws the saved copy rather than an error
// or an empty trip. And the ways out: "Me" offline, a view never saved, a refusal, a map.
async function testItineraryNoUsableAnswer() {
	console.log("/itinerary offline: no usable answer");
	const PAT = "pat@example.com";
	const PAT_KEY = "0123456789abcdef".repeat(4);
	const COOKIE = `user_id=pat%40example.com; full_name=Pat; ee_itinerary_key=${PAT_KEY}`;
	const CSRF_REFUSED = { status: 400, body: { exc_type: "CSRFTokenError", _server_messages: JSON.stringify([JSON.stringify({ message: "Invalid Request" })]) } };
	const phone = makeIndexedDB();
	const copyOf = (idb) => {
		const out = makeIndexedDB();
		for (const store of ["answers", "trips"]) for (const k of idb.keys(store)) out.seed(store, k, idb.get(store, k));
		return out;
	};
	const opts = (extra) => Object.assign({ user: PAT, key: PAT_KEY, cookie: COOKIE, indexedDB: phone, serviceWorker: makeServiceWorker(), build: "1727" }, extra || {});
	const banner = (p) => p.offline()[0] || "";

	let p = loadItinerary("/itinerary?trip=TRIP-A", opts());
	await p.answer("TRIP-A");
	await p.answer("TRIP-B"); // saved ahead
	const good = phone.get("answers", `${PAT}|TRIP-A|`).answer;
	check("(Pat's Trip A is saved on the phone, with its day)", good.days.length, 1);

	// A 200 whose body never arrived whole: it drew "Invalid Date – Invalid Date" and saved that.
	p = loadItinerary("/itinerary?trip=TRIP-A", opts());
	p.session.next = { status: 200, cut: true, body: null };
	await p.answer("TRIP-A");
	check(
		"a 200 cut off part way is no answer: the saved copy, under a line saying the server could not be reached",
		[p.shown(), p.days(), /^Can't reach the server — showing your itinerary as saved /.test(banner(p))],
		["Trip A", 1, true]
	);
	check("...and the good copy on the phone is not written over", phone.get("answers", `${PAT}|TRIP-A|`).answer.days.length, 1);
	p = loadItinerary("/itinerary?trip=TRIP-A", opts());
	p.session.next = { status: 200, body: { message: null } };
	await p.answer("TRIP-A");
	check("...nor by an answer that names no trip", [p.shown(), phone.get("answers", `${PAT}|TRIP-A|`).answer.trip], ["Trip A", "TRIP-A"]);

	// The gateway while a deploy restarts the site: the worker already served the kept page for it.
	for (const status of [502, 503, 504]) {
		p = loadItinerary("/itinerary?trip=TRIP-A", opts());
		// The maintenance window's 503 is frappe's own JSON, SessionStopped: still no answer.
		p.session.next = { status, body: status === 503 ? { exc_type: "SessionStopped", _server_messages: "[]" } : null };
		await p.answer("TRIP-A");
		check(
			`a ${status} while a deploy restarts the site: the saved copy, and why, not "Could not load the trip"`,
			[p.shown(), p.errors(), /^The server isn't answering right now — showing your itinerary as saved /.test(banner(p))],
			["Trip A", 0, true]
		);
	}
	p = loadItinerary("/itinerary?trip=TRIP-A", opts());
	p.session.next = { status: 500, body: { exc_type: "ValidationError", _server_messages: JSON.stringify([JSON.stringify({ message: "Something broke" })]) } };
	await p.answer("TRIP-A");
	check("...but frappe's own 500 is an answer, said as before", [p.shown(), p.offline(), p.text("ti-error")], [null, [], ["Could not load the trip: Something broke"]]);

	// One bar of signal: the request hangs, and the page no longer waits on it.
	let timers = makeTimers();
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ timers }));
	await flush();
	check("one bar of signal: while it waits, the trip is loading", [p.text("ti-boot"), timers.waiting().includes(6000)], [["Loading trip…"], true]);
	await timers.run(6000);
	check(
		"...six seconds with no answer: the saved copy, saying the server could not be reached, and no history call",
		[p.shown(), /^Can't reach the server — /.test(banner(p)), p.browser.calls],
		["Trip A", true, []]
	);
	check("...while the request carries on", p.asked(), [["TRIP-A", null]]);
	await p.answer("TRIP-A");
	check("...and its answer replaces the copy where it stands", [p.shown(), p.offline(), p.browser.calls, p.errors()], ["Trip A", [], [], 0]);
	timers = makeTimers();
	p = loadItinerary("/itinerary?trip=TRIP-A&as=crew", opts({ timers }));
	await timers.run(6000);
	check("...with nothing saved for that view it goes on loading, rather than say it is offline", [p.text("ti-boot"), p.errors()], [["Loading trip…"], 0]);
	await p.answer("TRIP-A", undefined, "crew");
	check("...until it answers", [p.shown(), p.errors()], ["Trip A", 0]);

	// "Me" offline is the default view saved ahead: the same answer.
	p = loadItinerary("/itinerary?trip=TRIP-A", opts());
	await p.answer("TRIP-A", false);
	check("(offline: Trip A as saved)", [p.shown(), p.offline().length], ["Trip A", 1]);
	await p.pick("Me");
	await p.answer("TRIP-A", false, "EMP-1");
	check("offline, tapping Me keeps Pat's itinerary: it is the saved default view", [p.shown(), p.offline().length, p.errors()], ["Trip A", 1, 0]);
	await p.pick("Alex");
	await p.answer("TRIP-A", false, "EMP-3");
	check(
		"a view never saved says so in the place of 'Loading trip…'",
		[p.text("ti-error"), p.text("ti-boot"), p.shown()],
		[["You're offline, and this itinerary isn't saved on this phone yet."], [], null]
	);
	p.browser.fire("online", {});
	await flush();
	check("...back online, it is asked for again", p.asked(), [["TRIP-A", "EMP-3"]]);
	const entries = p.urls().length;
	await p.answer("TRIP-A", undefined, "EMP-3");
	check("...and drawn, with no history call", [p.shown(), p.errors(), p.urls().length], ["Trip A", 0, entries]);
	await p.pick("Me");
	await p.answer("TRIP-A", false, "EMP-1");
	check("...and Me again, offline: Pat's saved copy", [p.shown(), p.offline().length, p.errors()], ["Trip A", 1, 0]);
	// Only when the default copy is theirs: Sam's view is not Pat's default.
	await p.pick("Sam");
	await p.answer("TRIP-A", false, "EMP-2");
	check("...but another person's view never falls back to Pat's", [p.shown(), p.text("ti-error")], [null, ["You're offline, and this itinerary isn't saved on this phone yet."]]);

	// The kiosk's worker at "/" on the first visit: navigator.serviceWorker.ready is the kiosk's.
	const kiosk = makeServiceWorker({ kiosk: true });
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ indexedDB: makeIndexedDB(), serviceWorker: kiosk }));
	await p.answer("TRIP-A");
	await p.answer("TRIP-B");
	await flush();
	check("a phone with the kiosk's worker, first visit: nothing is sent to the kiosk's worker", kiosk.kiosk, []);
	check("...every message reaches /itinerary's own, once it is running", kiosk.posted.map((m) => m.type), ["user", "cache-files"]);

	// A stale CSRF token: the page kept on the phone, from before a sign-in elsewhere.
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ pages: { "/itinerary": '<script>\n  window.ITIN_CSRF = "tok2";\n</script>' } }));
	p.session.next = CSRF_REFUSED;
	await p.answer("TRIP-A");
	check("a stale CSRF token: a new one is read from the page's own address", p.fileFetches, ["/itinerary"]);
	check("...and the trip asked for again with it, once", [p.asked(), p.tokens()], [[["TRIP-A", null]], ["tok2"]]);
	await p.answer("TRIP-A");
	check("...and drawn", [p.shown(), p.errors(), p.offline()], ["Trip A", 0, []]);
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ pages: { "/itinerary": null } }));
	p.session.next = CSRF_REFUSED;
	await p.answer("TRIP-A");
	check("...a token that cannot be had is no answer: the saved copy, not 'Invalid Request' for good", [p.shown(), p.errors(), p.offline().length], ["Trip A", 0, 1]);
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ pages: { "/itinerary": 'window.ITIN_CSRF = "tok3";' } }));
	p.session.next = CSRF_REFUSED;
	await p.answer("TRIP-A");
	p.session.next = CSRF_REFUSED;
	await p.answer("TRIP-A");
	check("...and refused again, it is asked once and no more", [p.fileFetches, p.asked(), p.shown(), p.errors()], [["/itinerary"], [], "Trip A", 0]);

	// Refused offline after a sign-out in another tab, then signed in again: never stuck.
	const held = copyOf(phone);
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ indexedDB: held }));
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	await p.answer("TRIP-B");
	p.document.cookie = "user_id=Guest"; // signed out in another tab: the marker went with it
	p.browser.back();
	await p.browser.settle();
	await p.answer("TRIP-A", false);
	check("signed out elsewhere, then a blip on Back: refused, with the way to sign in", [p.empty(), p.text("ti-signin")], [["Sign in to see your itinerary."], ["Sign in"]]);
	p.document.cookie = COOKIE; // signed in again: the same marker, the same person
	p.browser.forward();
	await p.browser.settle();
	await p.answer("TRIP-B");
	check(
		"...signed in again, Forward with a signal: the trip, not a page stuck on 'Sign in'",
		[p.shown(), p.empty().includes("Sign in to see your itinerary."), p.text("ti-signin")],
		["Trip B", false, []]
	);

	// A map that failed offline is tried again on the next tap.
	p = loadItinerary("/itinerary?trip=TRIP-A", opts({ indexedDB: makeIndexedDB() }));
	p.session.next = { status: 200, body: { message: {
		trip: "TRIP-A", purpose: "Trip A", status: "Booked", start_date: iso(0), end_date: iso(0), crew: [], viewing: "EMP-1",
		days: [{ date: iso(0), items: [{ type: "agenda", activity: "Walk the site", poi: { poi_name: "Site", lat: 33.4, lng: -112 } }] }],
	} } };
	await p.answer("TRIP-A");
	const scripts = () => p.document.head.children.filter((c) => c.tagName === "SCRIPT");
	p.root.find("ti-map-btn")[0].click();
	await flush();
	check("(the Map tap asks for Leaflet)", scripts().length, 1);
	scripts()[0].onerror();
	await flush();
	const holder = p.root.find("ti-day-map")[0];
	check("a map that cannot load says so where it can be seen", [holder.style.display, holder.textContent], ["block", "Map unavailable — use the Open in Maps links."]);
	check("...leaving nothing behind", p.document.head.children.length, 0);
	p.root.find("ti-map-btn")[0].click();
	await flush();
	check("...so the next tap, once the signal is back, asks for it again", scripts().length, 1);
	check("every push was paid for by a tap", p.browser.unactivated, 0);
}

// The list of all trips (?view=trips, 2026-09-28): a screen of its own, every row a link to its
// trip, and "All trips" in the chip bar. Someone else's trip opens `limited`: no numbers, no
// files, and a line saying so.
async function testItineraryAllTrips() {
	console.log("/itinerary all trips");
	const PAT = "pat@example.com";
	const PAT_KEY = "0123456789abcdef".repeat(4);
	const COOKIE = `user_id=pat%40example.com; full_name=Pat; ee_itinerary_key=${PAT_KEY}`;
	// A date as a row shows it: the year only on a date outside this one.
	const listDay = (value) => {
		const options = { weekday: "short", month: "short", day: "numeric" };
		if (value.slice(0, 4) !== String(new Date().getFullYear())) options.year = "numeric";
		return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, options);
	};
	const row = (p, purpose) => {
		const link = p.listRow(purpose);
		if (!link) return null;
		const part = (cls) => link.find(cls).map((e) => e.textContent);
		return { href: link.href, tags: part("ti-tag"), dates: part("ti-list-dates"), sub: part("ti-list-sub"), crew: part("ti-list-crew-names") };
	};
	const LIST_STATE = { itin_trip: null, itin_as: null, itin_view: "trips", itin_from: { trip: "TRIP-A", as: null } };

	// A reload, a link from the Travel workspace, the address: the list, with no history call.
	let p = loadItinerary("/itinerary?view=trips");
	check(
		"?view=trips at boot is the list of all trips: no history call, and only the list is asked for",
		[p.browser.calls, p.pending(), p.title(), p.document.title, p.text("ti-header-sub")],
		[[], ["(all trips)"], "Trips", "Trips", ["Everyone's trips"]]
	);
	check("...saying it is loading, and nothing of a trip's", [p.text("ti-boot"), p.chips().length, p.root.find("ti-all-chip").length], [["Loading trips…"], 0, 0]);
	await p.answerList();
	check("...then its groups, in the server's order, with how many are in each", [p.listGroups(), p.text("ti-list-count")], [["On the road now", "Coming up", "Earlier trips"], ["2", "2", "13"]]);
	check(
		"...each trip under its group, the earlier ones at their first ten",
		p.listRows(),
		["Trip A", "Trip O", "Trip Z", "Trip B", "Past 1", "Past 2", "Past 3", "Past 4", "Past 5", "Past 6", "Past 7", "Past 8", "Past 9", "Past 10"]
	);
	check(
		"a row says what the trip is, when, its status, kind and job, and who is on it, the trip lead first",
		row(p, "Trip A"),
		{ href: "/itinerary?trip=TRIP-A", tags: ["You're on it"], dates: [`${listDay(iso(-1))} – ${listDay(iso(1))}`], sub: ["Booked · Air · For: Acme Fountains"], crew: ["Pat (lead), Sam, Alex"] }
	);
	check("...a trip they organized but are not on says so", row(p, "Trip O").tags, ["You organized it"]);
	check("...someone else's trip has no tag, and a job with no name is left out", [row(p, "Trip Z").tags, row(p, "Trip O").sub], [[], ["Booked · Road"]]);
	check("...a big crew is cut short, and a trip with no lead named marks nobody", row(p, "Trip B").crew, ["Pat, Sam, Alex, Dana, Lee +2 more"]);
	const more = p.root.find("ti-list-more")[0];
	check("earlier trips past the first ten wait behind 'Show N more'", more && more.textContent, "Show 3 more");
	more.click();
	await flush();
	check(
		"...which adds them in place, writing no history and asking for nothing",
		[p.listRows().length, p.listRows().slice(-3), p.root.find("ti-list-more").length, p.browser.calls, p.pending()],
		[17, ["Past 11", "Past 12", "Past 13"], 0, [], []]
	);

	// A row is a link, and a tap on it is one entry.
	await p.openRow("Trip Z");
	check("tapping a trip on the list pushes one entry, ?trip=<name>, and asks for it", [p.urls(), p.pending(), p.view()], [["push /itinerary?trip=TRIP-Z"], ["TRIP-Z"], null]);
	check("...remembering it was opened from the list", p.browser.history.state, { itin_trip: "TRIP-Z", itin_as: null, itin_from: { trip: null, as: null, view: "trips" } });
	await p.answer("TRIP-Z");
	check("...and shows it, with All trips first in the chip bar", [p.shown(), p.root.find("ti-switcher")[0].children[0].className, p.text("ti-all-chip")], ["Trip Z", "ti-all-chip", ["🧳All trips"]]);
	p.browser.back();
	await p.browser.settle();
	check(
		"Back returns to the list, with no history call: drawn at once from the last answer, as it was left, while it is asked for again",
		[p.view(), p.trip(), p.urls().length, p.listRows().length, p.pending(), p.title()],
		["trips", null, 1, 17, ["(all trips)"], "Trips"]
	);
	await p.answerList();
	check("...and its answer redraws it", [p.listRows().length, p.pending()], [17, []]);
	p.browser.forward();
	await p.browser.settle();
	check("Forward opens the trip again", [p.trip(), p.pending(), p.urls().length], ["TRIP-Z", ["TRIP-Z"], 1]);
	await p.answer("TRIP-Z");
	check("...and shows it", p.shown(), "Trip Z");
	p.browser.back();
	await p.browser.settle();
	p.browser.back();
	await p.browser.settle();
	check("...and Back from the list it booted on leaves the page", p.browser.left, ORIGIN + "/desk");
	check("every push was paid for by a tap", p.browser.unactivated, 0);

	// "All trips" in the chip bar.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	check("'All trips' is first in the trip chip bar, before their own trips", [p.root.find("ti-switcher")[0].children[0].className, p.chips().length], ["ti-all-chip", 4]);
	await p.allTrips();
	check("...a tap pushes one entry, ?view=trips, and asks for the list", [p.urls(), p.pending(), p.title()], [["replace /itinerary?trip=TRIP-A", "push /itinerary?view=trips"], ["(all trips)"], "Trips"]);
	check("...remembering the trip it was pushed from", p.browser.history.state, LIST_STATE);
	check("...nothing of the trip is left on screen", [p.shown(), p.chips().length, p.root.find("ti-contacts").length], [null, 0, 0]);
	await p.answerList();
	await p.openRow("Trip B");
	await p.answer("TRIP-B");
	await p.allTrips();
	await p.browser.settle();
	check(
		"'All trips' on a trip opened from the list steps Back onto it, rather than put it in history twice",
		[p.urls().slice(1), p.browser.index, p.view(), p.listRows().length],
		[["push /itinerary?view=trips", "push /itinerary?trip=TRIP-B"], 2, "trips", 14]
	);
	check("...asking for the list once", p.listAsked(), 1);
	await p.answerList();
	p.browser.forward();
	await p.browser.settle();
	check("...so Forward is that trip again", [p.trip(), p.pending()], ["TRIP-B", ["TRIP-B"]]);
	await p.answer("TRIP-B");
	p.browser.back();
	await p.browser.settle();
	await p.answerList();
	p.browser.back();
	await p.browser.settle();
	check("...and Back past the list is the trip the list was opened from", [p.trip(), p.pending()], ["TRIP-A", ["TRIP-A"]]);
	await p.answer("TRIP-A");
	check("...shown as it was", [p.shown(), p.title()], ["Trip A", "My Itinerary"]);
	check("every push was paid for by a tap", p.browser.unactivated, 0);

	p = loadItinerary("/itinerary", { trips: [TRIPS[1]] });
	await p.answer("TRIP-A");
	check("a single trip of their own: no trip chips, but 'All trips' is there", [p.chips().length, p.text("ti-all-chip")], [0, ["🧳All trips"]]);

	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	p.capture.open = true;
	await p.allTrips();
	check("with 'Report a problem' open, 'All trips' writes no history and changes nothing", [p.urls(), p.shown(), p.pending()], [["replace /itinerary?trip=TRIP-A"], "Trip A", []]);

	// A reload of the list reached from a trip: the list, and Back to the trip behind it.
	p = loadItinerary("/itinerary?view=trips", { state: LIST_STATE, behind: [{ url: "/itinerary?trip=TRIP-A", state: { itin_trip: "TRIP-A", itin_as: null } }] });
	check("a reload of ?view=trips draws the list, with no history call", [p.browser.calls, p.pending(), p.title()], [[], ["(all trips)"], "Trips"]);
	await p.answerList();
	check("...all of it", p.listRows().length, 14);
	p.browser.back();
	await p.browser.settle();
	check("...and Back is the trip it was reached from", [p.trip(), p.pending(), p.browser.calls], ["TRIP-A", ["TRIP-A"], []]);

	// The empty page's way to the list.
	p = loadItinerary("/itinerary", { trips: [] });
	const link = p.root.find("ti-all-link")[0];
	check(
		"no trips of their own: the empty page offers the list of all trips, as a real link",
		[p.empty(), link && link.textContent, link && link.href],
		[["No upcoming or recent trips. Safe travels when the next one comes!"], "See all trips", "/itinerary?view=trips"]
	);
	link.click();
	await flush();
	check("...a tap pushes ?view=trips and asks for the list", [p.urls(), p.pending(), p.title()], [["push /itinerary?view=trips"], ["(all trips)"], "Trips"]);
	await p.answerList();
	p.browser.back();
	await p.browser.settle();
	check("...and Back is the empty page again, asking for nothing", [p.empty(), p.pending(), p.urls().length, p.title()], [["No upcoming or recent trips. Safe travels when the next one comes!"], [], 1, "My Itinerary"]);
	check("every push was paid for by a tap", p.browser.unactivated, 0);
	p = loadItinerary("/itinerary", { trips: [], employee: null });
	check("no Employee record: no link (the list is for staff)", p.root.find("ti-all-link").length, 0);

	// Someone else's trip: `limited`.
	p = loadItinerary("/itinerary?trip=TRIP-Z");
	await p.answer("TRIP-Z");
	check(
		"someone else's trip (limited): a quiet line saying why, and no Documents screen and no print link",
		[p.text("ti-limited"), p.screens(), !!p.print(), p.docs()],
		[["You're not on this trip, so confirmation numbers and files are left out."], [], false, []]
	);
	check("...still the whole crew's itinerary, day by day", [p.title(), p.shown(), p.days(), p.members()], ["Whole crew", "Trip Z", 1, ["Dana"]]);
	check("...under the trip, above the day list", p.order().filter((c) => c !== "ti-header" && c !== "ti-switcher").slice(0, 3), ["ti-trip-meta", "ti-people", "ti-limited"]);
	p = loadItinerary("/itinerary?trip=TRIP-A");
	await p.answer("TRIP-A");
	check("a trip they are on (limited: false) has no such line, and its Documents and print link", [p.text("ti-limited"), p.screens().length, !!p.print()], [[], 2, true]);
	// A limited answer that still carried what the server should have taken out draws none of it.
	p = loadItinerary("/itinerary?trip=TRIP-Z&view=docs&file=TD-PASS-PAT");
	p.session.next = { status: 200, body: { message: {
		trip: "TRIP-Z", purpose: "Trip Z", status: "Booked", start_date: iso(0), end_date: iso(1), limited: true,
		crew: [{ employee: "EMP-1", employee_name: "Pat" }, { employee: "EMP-2", employee_name: "Sam" }], viewing: null, viewer_employee: "EMP-9", viewer_on_trip: false,
		days: [{ date: iso(0), items: sharedFlight(null).concat([{ type: "freight", date: iso(0), carrier: "UPS", contents: "Pump", tracking_number: "1Z999" }]) }],
		documents: visibleFiles(null), sheet_url: SHEET, my_sheet_url: `${SHEET}&as=EMP-1`,
	} } };
	await p.answer("TRIP-Z");
	check(
		"a limited answer that still carried numbers, files or a sheet draws none of them, whatever screen or picture the address names",
		[p.has("PNR"), p.has("1Z999"), p.members(), p.docs(), p.screens(), !!p.print(), !!p.viewer(), p.days(), p.document.title],
		[false, false, ["Pat", "Sam"], [], [], false, false, 1, "Whole crew itinerary"]
	);
	check("...and writes no history", p.browser.calls, []);

	// From the list, Trip Z opens limited; a trip gone since steps back onto the list.
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList();
	await p.openRow("Trip Z");
	await p.answer("TRIP-Z");
	check("a trip opened from the list that is someone else's shows limited", [p.shown(), p.text("ti-limited").length], ["Trip Z", 1]);
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList({ status: 200, body: { message: { trips: allTripRows().concat([{ name: "TRIP-GONE", purpose: "Trip Gone", status: "Booked", start_date: iso(30), end_date: iso(31), crew: [], group: "upcoming" }]), more: 0 } } });
	await p.openRow("Trip Gone"); // deleted since the list was drawn
	await p.answer("TRIP-GONE");
	await p.browser.settle();
	check(
		"a trip refused after a tap on the list steps back onto the list, rather than fall back to one of their own",
		[p.urls(), p.browser.index, p.view(), p.pending()],
		[["push /itinerary?trip=TRIP-GONE"], 1, "trips", ["(all trips)"]]
	);
	await p.answerList();
	check(
		"...which says why, once",
		[p.text("ti-list-note"), p.listRows().length],
		[["That trip couldn't be opened: it may have been deleted, or you don't have access to it."], 14]
	);
	await p.openRow("Trip A");
	await p.answer("TRIP-A");
	p.browser.back();
	await p.browser.settle();
	check("...and not again after another trip", p.text("ti-list-note"), []);

	// Refused, and signed out.
	p = loadItinerary("/itinerary?view=trips", { cookie: "user_id=pat%40example.com" });
	await p.answerList(refusal(403, "PermissionError", "Not permitted"));
	check("someone who is not staff is told the list is not theirs, in place, with no history call", [p.empty(), p.listRows(), p.browser.calls], [["You don't have access to the list of all trips."], [], []]);
	p = loadItinerary("/itinerary?view=trips", { cookie: "user_id=pat%40example.com" });
	await p.answerList(sessionExpired());
	check(
		"a session that has expired says so, with a way to sign in that comes back to the list",
		[p.empty(), p.root.find("ti-signin").map((a) => a.href), p.browser.calls],
		[["Your session has expired. Sign in again"], ["/login?redirect-to=/itinerary%3Fview%3Dtrips"], []]
	);
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList({ status: 500, body: { exc_type: "ValidationError", _server_messages: JSON.stringify([JSON.stringify({ message: "Something broke" })]) } });
	check("frappe's own 500 is an answer, said as such", [p.text("ti-error"), p.text("ti-list-offline")], [["Could not load the trips: Something broke"], []]);

	// Offline: the list is not kept on the phone, and says so; their own trips still open.
	p = loadItinerary("/itinerary?view=trips", { user: PAT, key: PAT_KEY, cookie: COOKIE });
	await p.answerList(false);
	check(
		"?view=trips with no signal says the list needs a connection, with no history call",
		[p.text("ti-list-offline"), p.browser.calls, p.errors()],
		[["You're offline — the list of all trips needs a connection."], [], 0]
	);
	check("...and offers their own trips, which open from the copies saved on the phone", [p.listGroups(), p.listRows()], [["Your trips"], ["Trip C", "Trip A", "Trip O", "Trip B"]]);
	check("...each saying whether they are on it or organized it", [row(p, "Trip A").tags, row(p, "Trip O").tags], [["You're on it"], ["You organized it"]]);
	await p.openRow("Trip B");
	check("...a tap pushes that trip and asks for it", [p.urls(), p.pending()], [["push /itinerary?trip=TRIP-B"], ["TRIP-B"]]);
	p = loadItinerary("/itinerary?view=trips", { user: PAT, key: PAT_KEY, cookie: COOKIE });
	await p.answerList(false);
	p.browser.fire("online", {});
	await flush();
	check("back online, the list is asked for again, in place", [p.pending(), p.browser.calls], [["(all trips)"], []]);
	await p.answerList();
	check("...and drawn", [p.listRows().length, p.text("ti-list-offline")], [14, []]);
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList({ status: 503, body: { exc_type: "SessionStopped", _server_messages: "[]" } });
	check("the gateway while a deploy restarts the site: the same, in its words", p.text("ti-list-offline"), ["The server isn't answering right now — the list of all trips needs a connection."]);
	// The page kept on the phone, from before a sign-out: nothing of its boot is offered.
	p = loadItinerary("/itinerary?view=trips", { user: PAT, key: PAT_KEY, cookie: "full_name=Pat" });
	check("a page kept from before a sign-out: nothing drawn while it waits", [p.text("ti-boot"), p.listRows()], [["Loading itinerary…"], []]);
	await p.answerList(false);
	check("...and offline, refused as a saved trip would be: no trips of the person it was drawn for", [p.empty(), p.listRows(), p.text("ti-list-offline")], [["Sign in to see your itinerary."], [], []]);
	// Offline after the list was drawn once: kept, and said to be out of date.
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList();
	await p.openRow("Trip A");
	await p.answer("TRIP-A");
	p.browser.back();
	await p.browser.settle();
	await p.answerList(false);
	check("offline after it was drawn once: the list stays, said to be out of date", [p.listRows().length, p.offline()], [14, ["You're offline — this list may be out of date."]]);
	// One bar of signal.
	const timers = makeTimers();
	p = loadItinerary("/itinerary?view=trips", { timers });
	await timers.run(6000);
	check("one bar of signal: after six seconds, the same words, while the request carries on", [p.text("ti-list-offline"), p.pending()], [["Can't reach the server — the list of all trips needs a connection."], ["(all trips)"]]);
	await p.answerList();
	check("...and its answer replaces them", [p.listRows().length, p.text("ti-list-offline")], [14, []]);

	// Answers for a screen already left are dropped.
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.allTrips();
	p.browser.back();
	await p.browser.settle();
	await p.answerList();
	check("a list that answers after it was left is dropped: the trip stays", [p.listRows(), p.view(), p.text("ti-boot")], [[], null, ["Loading trip…"]]);
	await p.answer("TRIP-A");
	check("...and the trip is shown", [p.shown(), p.listRows()], ["Trip A", []]);
	p = loadItinerary("/itinerary");
	await p.answer("TRIP-A");
	await p.tap("Trip B");
	await p.allTrips();
	await p.answer("TRIP-B");
	check("a trip that answers after the list was opened is dropped: the list stays", [p.shown(), p.title(), p.text("ti-boot")], [null, "Trips", ["Loading trips…"]]);
	await p.answerList();
	check("...and the list is drawn", p.listRows().length, 14);
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList();
	await p.openRow("Trip A");
	p.browser.back();
	await p.browser.settle(); // the list, asked for again
	p.browser.forward();
	await p.browser.settle();
	p.browser.back();
	await p.browser.settle(); // and again
	check("(the list was asked for twice)", p.listAsked(), 2);
	await p.answerList({ status: 200, body: { message: { trips: [{ name: "TRIP-OLD", purpose: "Old answer", group: "now", crew: [] }], more: 0 } } });
	check("an answer to a list asked for again since is dropped", [p.listRows().length, p.listRows().includes("Old answer")], [14, false]);
	await p.answerList({ status: 200, body: { message: { trips: [{ name: "TRIP-NEW", purpose: "New answer", group: "now", crew: [] }], more: 0 } } });
	check("...and the latest is drawn", p.listRows(), ["New answer"]);

	// Everything on a row is text.
	const HOSTILE = "<img src=x onerror=alert(1)>";
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList({ status: 200, body: { message: { trips: [{
		name: 'TRIP-"><script>', purpose: HOSTILE, status: "<b>Booked</b>", travel_type: "Air", start_date: iso(1), end_date: iso(2), travel_for: "<i>Acme</i>",
		crew: [`${HOSTILE} Pat`, "Sam</div>"], lead: `${HOSTILE} Pat`, mine: true, organizing: false, group: "upcoming",
	}], more: 0 } } });
	const hostile = row(p, HOSTILE);
	check(
		"a purpose, status, job or crew name with markup in it is drawn as text, never a tag",
		[p.listRows(), hostile && hostile.sub, hostile && hostile.crew, p.tags().filter((t) => !["HEADER", "DIV", "SECTION", "H2", "SPAN", "A", "FOOTER"].includes(t))],
		[[HOSTILE], ["<b>Booked</b> · Air · For: <i>Acme</i>"], [`${HOSTILE} Pat (lead), Sam</div>`], []]
	);
	check("...and its name goes into the link encoded, as a query value only", hostile && hostile.href, "/itinerary?trip=TRIP-%22%3E%3Cscript%3E");
	await p.openRow(HOSTILE);
	check("...and into the address the same way", p.urls(), ['push /itinerary?trip=TRIP-%22%3E%3Cscript%3E']);
	p = loadItinerary("/itinerary?view=trips");
	await p.answerList({ status: 200, body: { message: { trips: [{ name: "TRIP-Q", purpose: { html: "<b>x</b>" }, status: 7, start_date: "soon", end_date: null, crew: [null, 3, "Sam"], lead: {}, group: "later" }, "junk", null], more: "5" } } });
	check(
		"a row with values that are not text draws what it can: the name, 'Dates not set', the names that are text, under Coming up",
		[p.listGroups(), p.listRows(), row(p, "TRIP-Q").dates, row(p, "TRIP-Q").sub, row(p, "TRIP-Q").crew, p.text("ti-list-capped")],
		[["Coming up"], ["TRIP-Q"], ["Dates not set"], [], ["Sam"], ["5 more trips aren't listed here."]]
	);
	check("every push was paid for by a tap", p.browser.unactivated, 0);
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
