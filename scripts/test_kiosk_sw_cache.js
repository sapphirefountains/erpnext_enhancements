#!/usr/bin/env node
/**
 * The Time Kiosk service worker's offline API answers, one person at a time.
 *
 * Loads the REAL `www/kiosk-sw.js` into a vm context over a stand-in Cache Storage and a
 * stand-in network whose answers depend on who the server thinks is signed in, then fires the
 * worker's own install / activate / fetch events at it. Plain node, no runner and no npm install.
 *
 * The bug it pins (on main until v1.750.1): the worker kept every GET under
 * `/api/method/erpnext_enhancements.api.time_kiosk.` under its URL alone and replayed it offline,
 * so on a shared phone the next technician to sign in, offline, was handed the last one's My Day,
 * history, correction requests and Map trail. What must hold now:
 *
 *   - **a person's own answers still replay offline** — the whole point of keeping them;
 *   - **nobody else's ever do** — not even before the new person's first answer has been kept,
 *     which is the moment the old entries are still on the phone;
 *   - **one person's answers on the phone at a time** — keeping one for somebody new forgets
 *     the rest;
 *   - **signing out forgets everything**, the kept /kiosk page included (it carries the boot
 *     payload), whether from desk (`POST /api/method/logout`) or the /logout page (`POST /` with
 *     `X-Frappe-CMD: logout`); any other POST, or another site's, forgets nothing;
 *   - **a call that names nobody is neither kept nor replayed**;
 *   - **a deploy still drops the previous deploy's answers**, and the new worker keeps its own;
 *   - **the name never reaches the server**: it is only ever part of a cache address.
 *
 * Run: node scripts/test_kiosk_sw_cache.js [path/to/kiosk-sw.js]
 * (the optional path is for checking the harness against another copy of the worker, e.g. the
 * pre-fix one, which must fail it.)
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const WORKER =
	process.argv[2] || path.join(__dirname, "..", "erpnext_enhancements", "www", "kiosk-sw.js");
const SOURCE = fs.readFileSync(WORKER, "utf8");

const ORIGIN = "https://erp.example.com";
const API = "/api/method/erpnext_enhancements.api.time_kiosk.";
const PAT = "pat@example.com";
const SAM = "sam.o'neil+kiosk@example.com";

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

// ---------------------------------------------------------------------------- stand-ins

function headerBag(raw) {
	const lower = {};
	for (const [k, v] of Object.entries(raw || {})) lower[k.toLowerCase()] = String(v);
	return {
		get: (k) => (k.toLowerCase() in lower ? lower[k.toLowerCase()] : null),
		has: (k) => k.toLowerCase() in lower,
	};
}

class FakeResponse {
	constructor(body, init) {
		init = init || {};
		this.body = body == null ? "" : body;
		this.status = init.status == null ? 200 : init.status;
		this.ok = this.status >= 200 && this.status < 300;
		this.type = init.type || "basic";
		this.redirected = !!init.redirected;
		this.rawHeaders = init.rawHeaders || init.headers || {};
		this.headers = headerBag(this.rawHeaders);
	}
	clone() {
		return new FakeResponse(this.body, this);
	}
}

class FakeRequest {
	constructor(url, init) {
		init = init || {};
		this.url = new URL(typeof url === "string" ? url : url.url, ORIGIN).href;
		this.method = init.method || "GET";
		this.mode = init.mode || "cors";
		this.rawHeaders = init.headers || {};
		this.headers = headerBag(this.rawHeaders);
	}
}

const keyOf = (req, ignoreSearch) => {
	const u = new URL(typeof req === "string" ? req : req.url, ORIGIN);
	if (ignoreSearch) u.search = "";
	return u.href;
};

// Cache Storage, shared by every worker this run loads (the phone's, across deploys).
const store = new Map();

function openCache(name) {
	if (!store.has(name)) store.set(name, new Map());
	const m = store.get(name);
	return {
		async match(req, opts) {
			const loose = !!(opts && opts.ignoreSearch);
			for (const [k, v] of m) if (keyOf(k, loose) === keyOf(req, loose)) return v.clone();
			return undefined;
		},
		async put(req, res) {
			m.set(keyOf(req), res);
		},
		async add(req) {
			const res = await net.fetch(req);
			if (!res.ok) throw new TypeError("add: " + res.status);
			m.set(keyOf(req), res);
		},
		async delete(req) {
			return m.delete(keyOf(req));
		},
		async keys() {
			return [...m.keys()].map((k) => new FakeRequest(k));
		},
	};
}

const caches = {
	async open(name) {
		return openCache(name);
	},
	async match(req, opts) {
		for (const name of store.keys()) {
			const hit = await openCache(name).match(req, opts);
			if (hit) return hit;
		}
		return undefined;
	},
	async keys() {
		return [...store.keys()];
	},
	async has(name) {
		return store.has(name);
	},
	async delete(name) {
		return store.delete(name);
	},
};

// The network: answers as whoever the server's session says is signed in (`net.session`),
// whatever the page claims. Logs every URL that actually left the phone.
const net = {
	online: true,
	session: null,
	log: [],
	async fetch(req) {
		const url = new URL(typeof req === "string" ? req : req.url, ORIGIN);
		const method = (req && req.method) || "GET";
		if (!net.online) throw new TypeError("Failed to fetch");
		net.log.push(`${method} ${url.pathname}${url.search}`);
		if (url.origin !== ORIGIN) return new FakeResponse("", { status: 200 });
		if (method === "POST") {
			const cmd = req.headers ? req.headers.get("X-Frappe-CMD") : null;
			if (/\/method\/(web_)?logout$/.test(url.pathname) || cmd === "logout") net.session = null;
			return new FakeResponse("{}", { status: 200 });
		}
		if (url.pathname === "/kiosk") {
			if (!net.session) return new FakeResponse("", { status: 0, type: "opaqueredirect" });
			return new FakeResponse(`shell for ${net.session}`);
		}
		if (url.pathname.startsWith(API)) {
			if (!net.session) return new FakeResponse("", { status: 403 });
			return new FakeResponse(`${url.pathname.slice(API.length)}${url.search} for ${net.session}`);
		}
		return new FakeResponse(`asset ${url.pathname}`);
	},
};

// ---------------------------------------------------------------------------- the worker

function loadWorker(build) {
	const handlers = {};
	const sandbox = {
		URL,
		Request: FakeRequest,
		Response: FakeResponse,
		caches,
		fetch: (req) => net.fetch(req),
		console,
		setTimeout,
		clearTimeout,
	};
	sandbox.self = sandbox;
	sandbox.location = { href: `${ORIGIN}/kiosk-sw.js?v=${build}`, origin: ORIGIN };
	sandbox.addEventListener = (type, fn) => {
		(handlers[type] = handlers[type] || []).push(fn);
	};
	sandbox.skipWaiting = () => Promise.resolve();
	sandbox.clients = { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]) };
	sandbox.registration = {};
	vm.createContext(sandbox);
	vm.runInContext(SOURCE, sandbox, { filename: path.basename(WORKER) });
	return { build, handlers };
}

function dispatch(worker, type, init) {
	const waits = [];
	let responded = null;
	const event = Object.assign(
		{
			waitUntil(p) {
				waits.push(Promise.resolve(p));
			},
			respondWith(p) {
				responded = Promise.resolve(p);
			},
		},
		init
	);
	for (const fn of worker.handlers[type] || []) fn(event);
	return { waits, responded };
}

async function drain(waits) {
	for (let i = 0; i < waits.length; i++) await waits[i];
}

async function lifecycle(worker) {
	for (const type of ["install", "activate"]) await drain(dispatch(worker, type, {}).waits);
}

// One request from a page this worker controls. `user` is what app.js puts in X-Kiosk-User
// (BOOT.user of the screen asking); leave it out for a call that names nobody.
async function request(worker, url, opts) {
	opts = opts || {};
	const headers = Object.assign({}, opts.headers);
	if (opts.user !== undefined) headers["X-Kiosk-User"] = encodeURIComponent(opts.user);
	const req = new FakeRequest(url, { method: opts.method, mode: opts.mode, headers });
	const { waits, responded } = dispatch(worker, "fetch", { request: req });
	let res;
	if (responded) {
		res = await responded;
	} else {
		// Not answered by the worker: the browser's own fetch.
		try {
			res = await net.fetch(req);
		} catch (e) {
			res = { status: "network error", body: null };
		}
	}
	await drain(waits);
	return { handled: !!responded, status: res.status, body: res.body };
}

const api = (method, query) => `${API}${method}${query ? "?" + query : ""}`;
const DAY = api("get_my_day", "date=2026-10-27");
const HISTORY = api("get_my_history", "days=14");
const CORRECTIONS = api("get_my_correction_requests", "limit=20");
const TRAIL = api("get_my_trail", "date=2026-10-27");
const STATUS = api("get_current_status");
const MINE = [DAY, HISTORY, CORRECTIONS, TRAIL];

// Every kept API answer on the phone, as "<cache>|<method>|<whose>" (whose: the key's owner).
function keptAnswers() {
	const out = [];
	for (const [name, m] of store) {
		for (const k of m.keys()) {
			const u = new URL(k);
			if (!u.pathname.startsWith(API)) continue;
			out.push(`${name}|${u.pathname.slice(API.length)}|${u.searchParams.get("__kiosk_user")}`);
		}
	}
	return out.sort();
}

function hasShell() {
	return [...store.values()].some((m) => m.has(`${ORIGIN}/kiosk`));
}

function reset() {
	store.clear();
	net.online = true;
	net.session = null;
	net.log = [];
}

// Pat signs in, opens the kiosk and every person-specific tab while online.
async function patUsedThePhone(worker) {
	net.online = true;
	net.session = PAT;
	await request(worker, "/kiosk", { mode: "navigate" });
	for (const url of [...MINE, STATUS]) await request(worker, url, { user: PAT });
}

// ---------------------------------------------------------------------------- scenarios

const tests = [];
const scenario = (name, fn) => tests.push({ name, fn });

scenario("a person's own answers replay offline", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	check("online answers come from the network", (await request(w, DAY, { user: PAT })).body, `get_my_day?date=2026-10-27 for ${PAT}`);
	net.online = false;
	for (const url of MINE) {
		const res = await request(w, url, { user: PAT });
		check(`offline, Pat gets Pat's ${url.slice(API.length).split("?")[0]}`, [res.handled, res.status, res.body.endsWith(`for ${PAT}`)], [true, 200, true]);
	}
	const shell = await request(w, "/kiosk", { mode: "navigate" });
	check("offline, the kept shell is still Pat's", shell.body, `shell for ${PAT}`);
});

scenario("the next person never sees the last one's answers", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	// Pat walks away still signed in on the server; Sam signs in (online) and the kiosk loads.
	net.session = SAM;
	await request(w, "/kiosk", { mode: "navigate" });
	// The phone drops off the network before Sam's screens have fetched anything: Pat's answers
	// are all still on the phone at this moment, which is exactly when the old worker leaked.
	net.online = false;
	check("Pat's answers are still on the phone", keptAnswers().length, 5);
	for (const url of MINE) {
		const res = await request(w, url, { user: SAM });
		check(`offline, Sam gets no ${url.slice(API.length).split("?")[0]} (504), not Pat's`, [res.status, res.body], [504, ""]);
	}
	// Back online, Sam's first answer is kept, and that forgets every one of Pat's.
	net.online = true;
	await request(w, STATUS, { user: SAM });
	check("keeping Sam's first answer forgets Pat's", keptAnswers(), ["time-kiosk-BUILD1|get_current_status|" + encodeURIComponent(SAM)]);
	net.online = false;
	check("offline, Sam's own status replays", (await request(w, STATUS, { user: SAM })).body, `get_current_status for ${SAM}`);
	check("offline, Sam's day is still not Pat's", (await request(w, DAY, { user: SAM })).status, 504);
});

scenario("a screen still showing Pat cannot read Sam's answers either", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	net.session = SAM;
	for (const url of MINE) await request(w, url, { user: SAM });
	net.online = false;
	check("offline, a page that says Pat gets nothing of Sam's", (await request(w, TRAIL, { user: PAT })).status, 504);
	check("… and Sam's are untouched", keptAnswers().length, 4);
});

scenario("signing out from desk forgets everything", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	check("before: Pat's answers and shell are kept", [keptAnswers().length, hasShell()], [5, true]);
	const out = await request(w, "/api/method/logout", { method: "POST" });
	check("the sign-out itself goes to the server", [out.handled, net.session], [false, null]);
	check("after: no kept answers", keptAnswers(), []);
	check("after: no kept shell", hasShell(), false);
	net.online = false;
	const shell = await request(w, "/kiosk", { mode: "navigate" });
	check("offline, /kiosk no longer opens Pat's screen", [shell.status, shell.body], [503, "Offline"]);
	check("offline, Pat's day is gone too", (await request(w, DAY, { user: PAT })).status, 504);
	check("the precached app shell survives (the kiosk still opens once online)", store.get("time-kiosk-BUILD1").has(`${ORIGIN}/assets/erpnext_enhancements/js/kiosk/app.js?v=BUILD1`), true);
});

scenario("signing out from the /logout page, or web_logout, forgets everything", async () => {
	for (const [label, url, headers] of [
		["the /logout page (POST / with X-Frappe-CMD: logout)", "/", { "X-Frappe-CMD": "logout" }],
		["web_logout", "/api/method/web_logout", {}],
		["the v2 API's logout", "/api/v2/method/logout", {}],
	]) {
		reset();
		const w = loadWorker("BUILD1");
		await lifecycle(w);
		await patUsedThePhone(w);
		await request(w, url, { method: "POST", headers });
		check(`${label}: nothing kept`, [keptAnswers().length, hasShell()], [0, false]);
	}
});

scenario("nothing else forgets anything", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	await request(w, api("log_time"), { method: "POST", user: PAT });
	await request(w, "/api/method/frappe.desk.form.save.savedocs", { method: "POST", headers: { "X-Frappe-CMD": "savedocs" } });
	await request(w, "https://other.example.com/api/method/logout", { method: "POST" });
	await request(w, "/api/method/logout");
	check("a clock action, a desk save, another site's logout and a GET of logout keep everything", [keptAnswers().length, hasShell()], [5, true]);
});

scenario("a call that names nobody is neither kept nor replayed", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	net.session = PAT;
	const online = await request(w, DAY);
	check("online, it passes straight to the network", [online.handled, online.body], [false, `get_my_day?date=2026-10-27 for ${PAT}`]);
	check("nothing is kept", keptAnswers(), []);
	await request(w, DAY, { user: PAT });
	net.online = false;
	check("offline, it is not answered from Pat's kept copy", (await request(w, DAY)).status, "network error");
});

scenario("only a good answer is kept", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	// Pat's session ends on the server; the page still says Pat, and the server says 403.
	net.session = null;
	check("online, the 403 is handed back", (await request(w, DAY, { user: PAT })).status, 403);
	net.online = false;
	check("offline, Pat's last good day still replays", (await request(w, DAY, { user: PAT })).body, `get_my_day?date=2026-10-27 for ${PAT}`);
});

scenario("a deploy drops the previous deploy's answers", async () => {
	reset();
	const old = loadWorker("BUILD1");
	await lifecycle(old);
	await patUsedThePhone(old);
	const w = loadWorker("BUILD2");
	await lifecycle(w);
	check("only this deploy's cache is left", [...store.keys()], ["time-kiosk-BUILD2"]);
	check("no kept answers survive the deploy", keptAnswers(), []);
	net.online = false;
	check("offline, nothing old replays", (await request(w, DAY, { user: PAT })).status, 504);
	net.online = true;
	await request(w, DAY, { user: PAT });
	check("the new worker keeps its own, in its own cache", keptAnswers(), ["time-kiosk-BUILD2|get_my_day|" + encodeURIComponent(PAT)]);
	net.online = false;
	check("… and replays it", (await request(w, DAY, { user: PAT })).body, `get_my_day?date=2026-10-27 for ${PAT}`);
});

scenario("a worker restarted mid-shift still keeps one person at a time", async () => {
	reset();
	const first = loadWorker("BUILD1");
	await lifecycle(first);
	await patUsedThePhone(first);
	// The browser stops an idle worker and starts it again: same caches, no memory of whose they are.
	const again = loadWorker("BUILD1");
	net.session = SAM;
	await request(again, STATUS, { user: SAM });
	check("Sam's first answer still forgets Pat's", keptAnswers(), ["time-kiosk-BUILD1|get_current_status|" + encodeURIComponent(SAM)]);
});

scenario("the person's name never reaches the server", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await patUsedThePhone(w);
	check("no request that left the phone carries the cache key's name", net.log.filter((l) => l.includes("__kiosk_user")), []);
	check("the API requests went out as the page made them", net.log.includes(`GET ${DAY}`), true);
});

(async () => {
	for (const t of tests) {
		console.log(t.name);
		try {
			await t.fn();
		} catch (err) {
			failures += 1;
			console.error(`  FAIL ${t.name}: threw ${(err && err.stack) || err}`);
		}
	}
	if (failures) {
		console.error(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed (${tests.length} scenarios)`);
})();
