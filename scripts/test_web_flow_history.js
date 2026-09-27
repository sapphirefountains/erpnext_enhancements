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
 *     and the modal says why, since a render (which never releases) would show the form with
 *     no word of it;
 *   - no answer (a dropped connection, a 5xx, a proxy timeout, 3-D Secure ending in any other
 *     error type — Stripe unreachable, a rate limit, one the page does not know) is never a
 *     failure — the charge may have gone through — so the page never shows a card form then: it
 *     holds Pay and Back and asks the server again, which renders "being processed" for an
 *     attempt on record; and a server that could not learn the outcome itself answers
 *     Processing, which goes to /stripe-return;
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
 *     only once the panel has answered.
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
		this.children = [];
		this.text = v == null ? "" : String(v);
	}
	set innerHTML(v) {
		this.children = [];
		this.text = "";
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
	focus() {}
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
		addEventListener(type, fn) {
			(this.listeners[type] = this.listeners[type] || []).push(fn);
		},
		dispatch(type) {
			(this.listeners[type] || []).slice().forEach((fn) => fn({ type }));
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
 */
function makeBrowser(url, opts) {
	opts = opts || {};
	const browser = {
		entries: [
			{ doc: "prev", url: ORIGIN + (opts.prevPath || "/pay"), state: null },
			{ doc: "page", url: ORIGIN + url, state: clone(opts.state) },
		],
		index: 1,
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
// What frappe.call hands error() for a frappe.throw on the server (HTTP 417): the parsed body,
// naming its exc_type. The server's definite answer that nothing was charged.
const DECLINED = { exc_type: "ValidationError", _server_messages: '["Your card was declined."]' };
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
		"pay-btn": { tag: "button" },
		"back-btn": { tag: "button" },
	});
	const calls = [];
	const stripeState = { tokens: 0, nextAction: null, elementListeners: {} };
	const frappe = {
		call(o) {
			calls.push(o);
		},
	};
	const paymentElement = {
		mount() {},
		on(type, fn) {
			(stripeState.elementListeners[type] = stripeState.elementListeners[type] || []).push(fn);
		},
	};
	const Stripe = () => ({
		elements: () => ({ create: () => paymentElement, submit: async () => ({}) }),
		createConfirmationToken: async () => ({ confirmationToken: { id: "ctok_" + ++stripeState.tokens } }),
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
		Stripe,
		console,
		setTimeout,
		clearTimeout,
	});
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
		stripe: stripeState,
		take,
		shown: () => ($("step-review").style.display === "none" ? "card" : "review"),
		visible: (id) => $(id).style.display !== "none",
		async continueWith(quote) {
			$("continue-btn").click();
			await flush();
			const call = take("portal_price_card_payment");
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
	for (const [label, answer] of [
		["a dropped connection", (c) => c.error({ readyState: 0, status: 0 })],
		["a 5xx or a proxy timeout", (c) => c.error()],
		["a 417 whose body would not parse", (c) => c.error("<html>")],
		["a 200 carrying an exception", (c) => c.callback({ exc: '["Traceback"]' })],
	]) {
		p = loadPayCard();
		await p.continueWith(CREDIT);
		confirm = await p.pay();
		answer(confirm);
		await p.browser.settle();
		check(`${label}: the page asks the server again`, p.browser.replacedWith, RELOAD);
		check("...never showing the card step", p.shown(), "review");
		check("...with Pay and Back held", [p.$("pay-btn").disabled, p.$("pay-btn").textContent, p.$("back-btn").disabled], [true, "Checking your payment…", true]);
		p.$("pay-btn").disabled = false; // even if it were not
		p.$("pay-btn").click();
		await flush();
		check("...and the quote is never sent again", p.take("portal_confirm_card_payment"), null);
		confirm.error(DECLINED);
		await flush();
		check("...nor does a late answer bring the card step back", p.shown(), "review");
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
	// render, so the card step (the modal says why); nothing was charged, and Continue tries again.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(LINK_OPEN);
	await p.browser.settle();
	check("a link that could not be closed: the card step, no reload", [p.shown(), p.browser.replacedWith, p.$("pay-btn").disabled], ["card", null, false]);

	// An earlier attempt Stripe would not confirm canceled: the same. Reloading used to land on a
	// card form with no message (the render never releases it), and every tap looped; now the form
	// stays, the modal says "could not be released just now", and Continue tries again.
	p = loadPayCard();
	await p.continueWith(CREDIT);
	confirm = await p.pay();
	confirm.error(UNRELEASED);
	await p.browser.settle();
	check("an attempt that could not be released, at Pay: the card step, no reload", [p.shown(), p.browser.replacedWith, p.$("pay-btn").disabled, p.$("continue-btn").disabled], ["card", null, false, false]);
	p = loadPayCard();
	let refused = await p.continueWith(null);
	refused.error(UNRELEASED);
	await p.browser.settle();
	check("...and at Continue: the card step stays, Continue usable, no reload", [p.browser.replacedWith, p.shown(), p.$("continue-btn").disabled], [null, "card", false]);
	await p.continueWith(DEBIT);
	check("...and the next Continue prices a quote as usual", p.shown(), "review");

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

// A shared flight: one booking, one row (and one PNR) per person.
function sharedFlight(viewing) {
	const rows = [
		{ employee: "EMP-1", ref: "PNR-PAT" },
		{ employee: "EMP-2", ref: "PNR-SAM" },
	];
	const base = { type: "flight", date: iso(0), sort_time: "", airline: "Southwest", flight_number: "WN 1", departure_airport: "PHX", arrival_airport: "LAS", group: "g1" };
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
	} },
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
	const browser = makeBrowser(url, { prevPath: "/desk", state: opts.state });
	env.browser = browser;
	const document = makeDocument(env, { "itinerary-root": {} });
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
