#!/usr/bin/env node
/**
 * The Time Kiosk on a shared phone: what its service worker keeps, for whom, and when it forgets.
 *
 * Loads the REAL `www/kiosk-sw.js` into a vm context over a stand-in phone (Cache Storage,
 * IndexedDB, open windows, the browser's cookie jar) and a stand-in server that behaves like
 * frappe and `workforce/kiosk_offline.py`: sessions that sign in by password POST, by `POST /`
 * with `X-Frappe-CMD: login`, or by a Google-style GET redirect, sessions that expire with no
 * request at all, the `ee_kiosk_key` marker cookie (set when /kiosk is drawn, removed by
 * `on_logout` and by `on_login` as anybody else), the `X-Kiosk-Key` echo on every kiosk answer,
 * and `log_geolocation_batch`'s refusal of another person's points. Then it fires the worker's own
 * install / activate / fetch / message / sync events at it. Plain node, no runner, no npm install.
 *
 * The page is modelled by the four lines of `app.js` that decide anything here (`OFFLINE_KEY`,
 * `phoneKey`, `askingKey`, and `init`'s refusal); `tests/test_kiosk_service_worker.py` pins those
 * lines in app.js, so the model and the page cannot drift apart unnoticed.
 *
 * The bug it started from (on main until v1.750.1): the worker kept every GET under
 * `/api/method/erpnext_enhancements.api.time_kiosk.` under its URL alone and replayed it offline,
 * so on a shared phone the next technician to sign in, offline, was handed the last one's My Day,
 * history, correction requests and Map trail, and the kept /kiosk page outlived every sign-out.
 * What must hold now:
 *
 *   - **a person's own answers still replay offline**;
 *   - **nobody else's ever do**, even before the new person's first answer has been kept;
 *   - **somebody else's sign-in, by any route, refuses the last person's kept page** after their
 *     session merely expired, and the worker deletes what it kept;
 *   - **an answer is kept only when the server's echo names the person the page claims**;
 *   - **one person's answers on the phone at a time**;
 *   - **a sign-out forgets everything** (answers, the page, queued points), answers the request
 *     unchanged, and tells open kiosk windows; anything else forgets nothing;
 *   - **nothing asked for before a forget is kept after it** (the epoch);
 *   - **a /kiosk load sent to the login page forgets the answers and the page**, not the queued
 *     points;
 *   - **each person's queued points upload under their own marker only**, wait for them, and go
 *     when somebody else's kiosk takes the phone;
 *   - **a deploy still drops the previous deploy's answers**;
 *   - **the email never reaches the server**, and the cache key's parameter never does either.
 *
 * Run: node scripts/test_kiosk_sw_cache.js [path/to/kiosk-sw.js]
 * (the optional path is for checking the harness against another copy of the worker, e.g. the
 * pre-fix one, which must fail it.)
 */

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const WORKER =
	process.argv[2] || path.join(__dirname, "..", "erpnext_enhancements", "www", "kiosk-sw.js");
const SOURCE = fs.readFileSync(WORKER, "utf8");

const ORIGIN = "https://erp.example.com";
const API = "/api/method/erpnext_enhancements.api.time_kiosk.";
const BATCH = API + "log_geolocation_batch";
const PAT = "pat@example.com";
const SAM = "sam.o'neil+kiosk@example.com";
const GOOGLE = "/api/method/frappe.integrations.oauth2_logins.login_via_google?code=abc&state=xyz";

// kiosk_offline.key_for: HMAC-SHA256(site key, context + user), never the email.
const markerFor = (user) =>
	crypto.createHmac("sha256", "site-encryption-key").update("kiosk:" + user).digest("hex");
const PATK = markerFor(PAT);
const SAMK = markerFor(SAM);

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

const tick = () => new Promise((r) => setImmediate(r));

// ---------------------------------------------------------------------------- stand-ins

function headerBag(raw) {
	const lower = {};
	for (const [k, v] of Object.entries(raw || {})) lower[k.toLowerCase()] = String(v);
	return {
		get: (k) => (k.toLowerCase() in lower ? lower[k.toLowerCase()] : null),
		has: (k) => k.toLowerCase() in lower,
		raw: lower,
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
	async json() {
		return JSON.parse(this.body);
	}
}

class FakeRequest {
	constructor(url, init) {
		init = init || {};
		this.url = new URL(typeof url === "string" ? url : url.url, ORIGIN).href;
		this.method = init.method || "GET";
		this.mode = init.mode || "cors";
		this.body = init.body == null ? null : init.body;
		this.signIn = init.signIn || null; // who the stand-in server signs in (login requests)
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

// IndexedDB, just enough of it for openDB / tx / put / get / getAll / delete.
const idb = new Map();
const indexedDB = {
	open(name, version) {
		const req = { onupgradeneeded: null, onsuccess: null, onerror: null };
		setImmediate(() => {
			let db = idb.get(name);
			const upgrade = !db || db.version < version;
			if (!db) idb.set(name, (db = { version, stores: new Map() }));
			const handle = {
				objectStoreNames: { contains: (n) => db.stores.has(n) },
				createObjectStore: (n, opts) => db.stores.set(n, { keyPath: opts.keyPath, rows: new Map() }),
				transaction: (n) => ({ objectStore: () => storeApi(db.stores.get(n)) }),
			};
			if (upgrade) {
				db.version = version;
				if (req.onupgradeneeded) req.onupgradeneeded({ target: { result: handle } });
			}
			if (req.onsuccess) req.onsuccess({ target: { result: handle } });
		});
		return req;
	},
};
function idbRequest(fn) {
	const req = { result: undefined, error: null, onsuccess: null, onerror: null };
	setImmediate(() => {
		try {
			req.result = fn();
			if (req.onsuccess) req.onsuccess({ target: req });
		} catch (e) {
			req.error = e;
			if (req.onerror) req.onerror({ target: req });
		}
	});
	return req;
}
function storeApi(s) {
	return {
		put: (v) => idbRequest(() => s.rows.set(v[s.keyPath], JSON.parse(JSON.stringify(v)))),
		get: (k) => idbRequest(() => s.rows.get(k)),
		getAll: () => idbRequest(() => [...s.rows.values()]),
		delete: (k) => idbRequest(() => s.rows.delete(k)),
	};
}
function queuedPoints() {
	const db = idb.get("TimeKioskDB");
	const q = db && db.stores.get("GeoQueue");
	return q ? [...q.rows.values()].map((p) => `${p.client_id}:${p.owner === PATK ? "pat" : p.owner === SAMK ? "sam" : p.owner || "-"}`).sort() : [];
}

// Open windows on the phone, for the worker's clients.matchAll.
let windows = [];
function openWindow(pathname) {
	const w = { url: ORIGIN + pathname, told: [], postMessage: (m) => w.told.push(m.type) };
	windows.push(w);
	return w;
}

// The server: frappe's sessions and kiosk_offline's marker, over one browser cookie jar.
const net = {
	online: true,
	session: null, // who the browser's sid is signed in as, or null
	jar: {}, // the browser's cookies that matter here: ee_kiosk_key
	logged: [], // Time Kiosk Log rows: "<client_id>:<whose session>"
	log: [], // every request that left the phone: "METHOD path?query"
	sentHeaders: [], // every X-Kiosk-Key value the server saw
	holdNext: false,
	held: [],
	gateway: 0, // a status the gateway answers /kiosk with instead (a deploy restarting the bench)
	onLogin(user) {
		// kiosk_offline.forget_on_login: a marker left by anybody else goes.
		if (net.jar.ee_kiosk_key && net.jar.ee_kiosk_key !== markerFor(user)) delete net.jar.ee_kiosk_key;
		net.session = user;
	},
	onLogout() {
		delete net.jar.ee_kiosk_key; // kiosk_offline.forget_on_logout
		net.session = null;
	},
	expire() {
		net.session = null; // a session that runs out runs no hook: the marker stays
	},
	release() {
		const held = net.held;
		net.held = [];
		held.forEach((fn) => fn());
	},
	async fetch(input, init) {
		const req = typeof input === "string" || !(input instanceof FakeRequest) ? new FakeRequest(input, init) : input;
		if (!net.online) throw new TypeError("Failed to fetch");
		const res = net.answer(req);
		if (net.holdNext) {
			net.holdNext = false;
			return new Promise((resolve) => net.held.push(() => resolve(res)));
		}
		return res;
	},
	answer(req) {
		const url = new URL(req.url);
		net.log.push(`${req.method} ${url.pathname}${url.search}`);
		const claimed = req.headers.get("X-Kiosk-Key");
		if (claimed !== null) net.sentHeaders.push(claimed);
		if (url.origin !== ORIGIN) return new FakeResponse("", { status: 200 });
		const cmd = req.headers.get("X-Frappe-CMD");
		if (req.method === "POST") {
			if (/\/method\/(web_)?logout$/.test(url.pathname) || cmd === "logout" || cmd === "web_logout") {
				net.onLogout();
				return new FakeResponse("{}", { status: 200, headers: { "x-signed-out": "yes" } });
			}
			if (url.pathname === "/api/method/login" || cmd === "login") {
				net.onLogin(req.signIn);
				return new FakeResponse('{"message":"Logged In"}');
			}
			if (url.pathname === BATCH) return net.batch(JSON.parse(req.body || "{}"));
			return new FakeResponse("{}");
		}
		if (url.pathname.startsWith("/api/method/frappe.integrations.oauth2_logins.")) {
			net.onLogin(req.signIn); // LoginManager.login_as -> post_login -> on_login
			return new FakeResponse("", { status: 0, type: "opaqueredirect" });
		}
		if (url.pathname === "/kiosk") {
			if (net.gateway) return new FakeResponse("Bad Gateway", { status: net.gateway });
			if (!net.session) return new FakeResponse("", { status: 0, type: "opaqueredirect" });
			net.jar.ee_kiosk_key = markerFor(net.session); // kiosk.py -> set_marker
			return new FakeResponse(JSON.stringify({ user: net.session, offline_key: markerFor(net.session) }));
		}
		if (url.pathname.startsWith(API)) {
			if (!net.session) return new FakeResponse("", { status: 403 });
			// kiosk_offline.stamp_response: the session that answered.
			return new FakeResponse(`${url.pathname.slice(API.length)}${url.search} for ${net.session}`, {
				headers: { "x-kiosk-key": markerFor(net.session) },
			});
		}
		return new FakeResponse(`asset ${url.pathname}`);
	},
	batch(body) {
		if (!net.session) return new FakeResponse("", { status: 403 });
		const points = body.points || [];
		// log_geolocation_batch -> kiosk_offline.belongs_to_session
		if (body.offline_key && body.offline_key !== markerFor(net.session)) {
			return new FakeResponse(JSON.stringify({
				message: { status: "other_person", accepted: [], rejected: points.map((p) => ({ client_id: p.client_id, reason: "other_person" })) },
			}));
		}
		for (const p of points) net.logged.push(`${p.client_id}:${net.session === PAT ? "pat" : "sam"}${"owner" in p ? "!owner-sent" : ""}`);
		return new FakeResponse(JSON.stringify({ message: { status: "success", accepted: points.map((p) => p.client_id), rejected: [] } }));
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
		indexedDB,
		fetch: (input, init) => net.fetch(input, init),
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
	sandbox.clients = { claim: () => Promise.resolve(), matchAll: () => Promise.resolve(windows.slice()) };
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

async function message(worker, type, data) {
	await drain(dispatch(worker, "message", { data: { type, data }, ports: [] }).waits);
}

// One request from a page this worker controls. `key` is what app.js puts in X-Kiosk-Key;
// leave it out for a call that names nobody.
async function request(worker, url, opts) {
	opts = opts || {};
	const headers = Object.assign({}, opts.headers);
	if (opts.key) headers["X-Kiosk-Key"] = opts.key;
	const req = new FakeRequest(url, { method: opts.method, mode: opts.mode, headers, body: opts.body, signIn: opts.signIn });
	const { waits, responded } = dispatch(worker, "fetch", { request: req });
	let res;
	if (responded) {
		try {
			res = await responded;
		} catch (e) {
			res = { status: "network error", body: null, headers: headerBag({}) };
		}
	} else {
		// Not answered by the worker: the browser's own fetch.
		try {
			res = await net.fetch(req);
		} catch (e) {
			res = { status: "network error", body: null, headers: headerBag({}) };
		}
	}
	await drain(waits);
	return { handled: !!responded, status: res.status, body: res.body, headers: res.headers };
}

// ---------------------------------------------------------------------------- the page

// app.js: OFFLINE_KEY = BOOT.offline_key; phoneKey() reads the ee_kiosk_key cookie;
// askingKey() = OFFLINE_KEY while phoneKey() still equals it; init() refuses (showSignedOut,
// which posts 'forget' with the cookie's key) when it does not, and otherwise configures the
// worker with owner = askingKey().
const phoneKey = () => net.jar.ee_kiosk_key || "";
const askingKey = (page) => (page.key && phoneKey() === page.key ? page.key : "");

async function openKiosk(worker) {
	const res = await request(worker, "/kiosk", { mode: "navigate" });
	if (res.status !== 200) return { status: res.status, body: res.body, refused: null };
	const boot = JSON.parse(res.body);
	const page = { status: 200, user: boot.user, key: boot.offline_key || "", refused: false };
	if (page.key && phoneKey() !== page.key) {
		page.refused = true;
		await message(worker, "forget", { owner: phoneKey() });
	} else {
		await message(worker, "config", { csrf_token: "csrf", max_batch_size: 50, owner: askingKey(page) });
	}
	return page;
}

const call = (worker, page, url) => request(worker, url, { key: askingKey(page) });

let seq = 0;
async function queuePoint(worker, page) {
	const id = `pt${++seq}`;
	await message(worker, "enqueue", { client_id: id, latitude: 40.7, longitude: -111.9, owner: page.key });
	return id;
}

const signIn = (worker, user) => request(worker, "/api/method/login", { method: "POST", signIn: user });

const api = (method, query) => `${API}${method}${query ? "?" + query : ""}`;
const DAY = api("get_my_day", "date=2026-10-27");
const HISTORY = api("get_my_history", "days=14");
const CORRECTIONS = api("get_my_correction_requests", "limit=20");
const TRAIL = api("get_my_trail", "date=2026-10-27");
const STATUS = api("get_current_status");
const MINE = [DAY, HISTORY, CORRECTIONS, TRAIL];
const short = (url) => url.slice(API.length).split("?")[0];

// Every kept API answer on the phone, as "<cache>|<method>|<whose>".
function keptAnswers() {
	const out = [];
	for (const [name, m] of store) {
		for (const k of m.keys()) {
			const u = new URL(k);
			if (!u.pathname.startsWith(API)) continue;
			const owner = u.searchParams.get("__kiosk_key");
			out.push(`${name}|${short(u.pathname)}|${owner === PATK ? "pat" : owner === SAMK ? "sam" : owner}`);
		}
	}
	return out.sort();
}

function hasShell() {
	return [...store.values()].some((m) => m.has(`${ORIGIN}/kiosk`));
}

function reset() {
	store.clear();
	idb.clear();
	windows = [];
	Object.assign(net, { online: true, session: null, jar: {}, logged: [], log: [], sentHeaders: [], holdNext: false, held: [], gateway: 0 });
}

// A fresh phone with this deploy's worker, and Pat signed in and through every screen online.
async function patsPhone() {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await signIn(w, PAT);
	const page = await openKiosk(w);
	for (const url of [...MINE, STATUS]) await call(w, page, url);
	return { w, page };
}

// ---------------------------------------------------------------------------- scenarios

const tests = [];
const scenario = (name, fn) => tests.push({ name, fn });

scenario("a person's own kiosk works offline", async () => {
	const { w } = await patsPhone();
	check("Pat's five answers are kept, under Pat's marker", keptAnswers().length, 5);
	net.online = false;
	const page = await openKiosk(w);
	check("offline, the kept page opens for Pat and is not refused", [page.status, page.user, page.refused], [200, PAT, false]);
	for (const url of MINE) {
		const res = await call(w, page, url);
		check(`offline, Pat gets Pat's ${short(url)}`, [res.status, res.body.endsWith(`for ${PAT}`)], [200, true]);
	}
});

scenario("the next person never sees the last one's answers", async () => {
	const { w } = await patsPhone();
	// Pat walks away still signed in; Sam signs in over it and opens the kiosk online.
	await signIn(w, SAM);
	const sams = await openKiosk(w);
	check("Sam's kiosk is Sam's", [sams.user, sams.refused], [SAM, false]);
	// The phone drops off the network before Sam's screens have fetched anything: Pat's answers
	// are all still on the phone at this moment, which is exactly when the old worker leaked.
	net.online = false;
	check("Pat's answers are still on the phone", keptAnswers().length, 5);
	for (const url of MINE) {
		const res = await call(w, sams, url);
		check(`offline, Sam gets no ${short(url)} (504), not Pat's`, [res.status, res.body], [504, ""]);
	}
	net.online = true;
	await call(w, sams, STATUS);
	check("keeping Sam's first answer forgets Pat's", keptAnswers(), ["time-kiosk-BUILD1|get_current_status|sam"]);
	net.online = false;
	check("offline, Sam's own status replays", (await call(w, sams, STATUS)).body, `get_current_status for ${SAM}`);
});

for (const [route, how] of [
	["the password form (POST /api/method/login)", (w) => signIn(w, SAM)],
	["the login page's POST / with X-Frappe-CMD: login", (w) => request(w, "/", { method: "POST", headers: { "X-Frappe-CMD": "login" }, signIn: SAM })],
	["Google (a GET redirect back to login_via_google)", (w) => request(w, GOOGLE, { mode: "navigate", signIn: SAM })],
]) {
	scenario(`Pat's session expires, Sam signs in by ${route}, and the phone is offline`, async () => {
		const { w } = await patsPhone();
		const pats = await openKiosk(w);
		net.online = false;
		const id = await queuePoint(w, pats);
		check("Pat's point is queued, offline", queuedPoints(), [`${id}:pat`]);
		net.expire(); // no request reaches the phone, so nothing has been forgotten yet
		net.online = true;
		await how(w);
		check("the sign-in removed Pat's marker cookie", phoneKey(), "");
		// The /kiosk load after the sign-in fails in a dead zone, so the kept page is all there is.
		net.online = false;
		const page = await openKiosk(w);
		check("the kept page (Pat's) refuses to draw", [page.user, page.refused], [PAT, true]);
		check("… and the worker deleted every kept answer", keptAnswers(), []);
		check("… and the kept page", hasShell(), false);
		check("… and Pat's queued points", queuedPoints(), []);
		check("a request still claiming Pat gets nothing", (await request(w, DAY, { key: PATK })).status, 504);
		const again = await openKiosk(w);
		check("opening it again offline finds nothing kept", [again.status, again.body], [503, "Offline"]);
		net.online = true;
		const sams = await openKiosk(w);
		check("online, Sam gets Sam's own kiosk", [sams.user, sams.refused], [SAM, false]);
	});
}

scenario("Pat's session expires and nobody signs in (the documented limit)", async () => {
	const { w } = await patsPhone();
	const pats = await openKiosk(w);
	net.online = false;
	const id = await queuePoint(w, pats);
	net.expire();
	const page = await openKiosk(w);
	check("offline, the kept page still opens as Pat's: expiry tells the phone nothing", [page.user, page.refused], [PAT, false]);
	net.online = true;
	const bounced = await openKiosk(w);
	check("online, /kiosk is sent to the login page", bounced.status, 0);
	check("… which forgets the kept answers", keptAnswers(), []);
	check("… and the kept page", hasShell(), false);
	check("… but not Pat's queued point, which waits for Pat", queuedPoints(), [`${id}:pat`]);
});

scenario("signing out from desk forgets everything and tells the kiosk", async () => {
	const { w } = await patsPhone();
	const pats = await openKiosk(w);
	net.online = false;
	await queuePoint(w, pats);
	net.online = true;
	const kiosk = openWindow("/kiosk");
	const desk = openWindow("/desk/todo");
	check("before: answers, the page and a queued point", [keptAnswers().length, hasShell(), queuedPoints().length], [5, true, 1]);
	const out = await request(w, "/api/method/logout", { method: "POST" });
	check("the sign-out reached the server, and its answer came back unchanged", [out.status, out.headers.get("x-signed-out"), net.session], [200, "yes", null]);
	check("after: no kept answers", keptAnswers(), []);
	check("after: no kept page", hasShell(), false);
	check("after: no queued points", queuedPoints(), []);
	check("the open kiosk window is told, and only it", [kiosk.told, desk.told], [["signed-out"], []]);
	net.online = false;
	const shell = await request(w, "/kiosk", { mode: "navigate" });
	check("offline, /kiosk no longer opens Pat's screen", [shell.status, shell.body], [503, "Offline"]);
	check("the precached app shell survives (the kiosk still opens once online)", store.get("time-kiosk-BUILD1").has(`${ORIGIN}/assets/erpnext_enhancements/js/kiosk/app.js?v=BUILD1`), true);
});

scenario("signing out from the /logout page, web_logout, v2 or the kiosk's Settings forgets everything", async () => {
	for (const [label, url, headers] of [
		["the /logout page (POST / with X-Frappe-CMD: logout)", "/", { "X-Frappe-CMD": "logout" }],
		["web_logout", "/api/method/web_logout", {}],
		["the v2 API's logout", "/api/v2/method/logout", {}],
		["Settings → Sign out (app.js api('logout'))", "/api/method/logout", { "X-Frappe-CSRF-Token": "csrf" }],
	]) {
		const { w } = await patsPhone();
		await request(w, url, { method: "POST", headers });
		check(`${label}: nothing kept, cookie gone`, [keptAnswers().length, hasShell(), phoneKey()], [0, false, ""]);
	}
});

scenario("a sign-out with no signal still forgets", async () => {
	const { w } = await patsPhone();
	net.online = false;
	const out = await request(w, "/api/method/logout", { method: "POST" });
	check("the request fails as it would have", out.status, "network error");
	check("but nothing kept is left", [keptAnswers().length, hasShell()], [0, false]);
});

scenario("nothing else forgets anything", async () => {
	const { w } = await patsPhone();
	await request(w, api("log_time"), { method: "POST", key: PATK });
	await request(w, "/api/method/frappe.desk.form.save.savedocs", { method: "POST", headers: { "X-Frappe-CMD": "savedocs" } });
	await request(w, "https://other.example.com/api/method/logout", { method: "POST" });
	await request(w, "/api/method/logout");
	check("a clock action, a desk save, another site's logout and a GET of logout keep everything", [keptAnswers().length, hasShell()], [5, true]);
});

scenario("an answer the server gave somebody else is not kept", async () => {
	const { w } = await patsPhone();
	const before = keptAnswers();
	// A window still showing Pat while the phone's session is now Sam's (signed in elsewhere).
	await signIn(w, SAM);
	await request(w, "/kiosk", { mode: "navigate" }); // Sam's kiosk drawn in another tab
	const forged = await request(w, TRAIL, { key: PATK }); // the stale (or forged) claim: Pat
	check("the server answered for Sam, and said so", [forged.body, forged.headers.get("X-Kiosk-Key") === SAMK], [`get_my_trail?date=2026-10-27 for ${SAM}`, true]);
	check("… so Sam's trail was not kept under Pat (or at all): the kept answers are as before", keptAnswers(), before);
	net.online = false;
	check("offline, a request claiming Pat still gets Pat's own trail, not Sam's", (await request(w, TRAIL, { key: PATK })).body, `get_my_trail?date=2026-10-27 for ${PAT}`);
	const pats = { key: PATK };
	check("the stale Pat window itself names nobody now (the cookie is Sam's)", askingKey(pats), "");
});

scenario("nothing asked for before a forget is kept after it", async () => {
	const { w } = await patsPhone();
	// An answer and a page load of Pat's are in flight when Pat signs out.
	net.holdNext = true;
	const answer = request(w, HISTORY, { key: PATK });
	await tick();
	net.holdNext = true;
	const nav = request(w, "/kiosk", { mode: "navigate" });
	await tick();
	await request(w, "/api/method/logout", { method: "POST" });
	check("the sign-out forgot everything", [keptAnswers(), hasShell()], [[], false]);
	net.release();
	await answer;
	await nav;
	check("the answer that landed after the sign-out was not kept", keptAnswers(), []);
	check("nor the page", hasShell(), false);
});

scenario("a late answer for the last person neither lands nor disturbs the next one's", async () => {
	const { w } = await patsPhone();
	net.holdNext = true;
	const late = request(w, HISTORY, { key: PATK });
	await tick();
	await request(w, "/api/method/logout", { method: "POST" });
	await signIn(w, SAM);
	const sams = await openKiosk(w);
	await call(w, sams, STATUS);
	net.release();
	await late;
	check("Sam's kept answer is still there, and Pat's late one is not", keptAnswers(), ["time-kiosk-BUILD1|get_current_status|sam"]);
	check("the kept page is Sam's", JSON.parse(store.get("time-kiosk-BUILD1").get(`${ORIGIN}/kiosk`).body).user, SAM);
});

scenario("a gateway error on /kiosk does not replace the kept page", async () => {
	const { w } = await patsPhone();
	net.gateway = 502;
	check("online, the 502 is handed back", (await request(w, "/kiosk", { mode: "navigate" })).status, 502);
	net.gateway = 0;
	net.online = false;
	const page = await openKiosk(w);
	check("offline, Pat's kept page still opens", [page.status, page.user], [200, PAT]);
});

scenario("a call that names nobody is neither kept nor replayed", async () => {
	reset();
	const w = loadWorker("BUILD1");
	await lifecycle(w);
	await signIn(w, PAT);
	const online = await request(w, DAY);
	check("online, it passes straight to the network", [online.handled, online.body], [false, `get_my_day?date=2026-10-27 for ${PAT}`]);
	check("nothing is kept", keptAnswers(), []);
	await request(w, DAY, { key: PATK });
	net.online = false;
	check("offline, it is not answered from Pat's kept copy", (await request(w, DAY)).status, "network error");
});

scenario("only a good answer is kept", async () => {
	const { w } = await patsPhone();
	net.expire();
	check("online, the 403 is handed back", (await request(w, DAY, { key: PATK })).status, 403);
	net.online = false;
	check("offline, Pat's last good day still replays", (await request(w, DAY, { key: PATK })).body, `get_my_day?date=2026-10-27 for ${PAT}`);
});

scenario("each person's queued points go up under their own marker only", async () => {
	const { w } = await patsPhone();
	const pats = await openKiosk(w);
	net.online = false;
	const a = await queuePoint(w, pats);
	const b = await queuePoint(w, pats);
	check("offline, Pat's points wait, tagged as Pat's", queuedPoints(), [`${a}:pat`, `${b}:pat`]);
	// Pat's session runs out; Sam signs in on desk and never opens the kiosk; Background Sync fires.
	net.expire();
	net.online = true;
	await signIn(w, SAM);
	await drain(dispatch(w, "sync", { tag: "flush-geo" }).waits);
	check("the server refused Pat's batch under Sam's session: nothing filed", net.logged, []);
	check("… and the points still wait for Pat", queuedPoints(), [`${a}:pat`, `${b}:pat`]);
	// Sam's session runs out too, Pat signs back in and opens the kiosk.
	net.expire();
	await signIn(w, PAT);
	await openKiosk(w);
	await message(w, "flush");
	check("Pat's points go up as Pat's, without the marker inside them", net.logged, [`${a}:pat`, `${b}:pat`]);
	check("… and leave the queue", queuedPoints(), []);
});

scenario("somebody else's kiosk taking the phone deletes the last person's points", async () => {
	const { w } = await patsPhone();
	const pats = await openKiosk(w);
	net.online = false;
	await queuePoint(w, pats);
	net.online = true;
	net.expire();
	await signIn(w, SAM);
	await openKiosk(w);
	check("Sam's kiosk configured the worker, and Pat's point is gone", queuedPoints(), []);
	await message(w, "flush");
	check("nothing of Pat's was ever filed", net.logged, []);
});

scenario("points queued before this release (no marker) upload as before", async () => {
	const { w } = await patsPhone();
	net.online = false;
	await message(w, "enqueue", { client_id: "legacy1", latitude: 40.7, longitude: -111.9 });
	net.online = true;
	await message(w, "config", { csrf_token: "csrf", max_batch_size: 50, owner: PATK });
	check("a person's kiosk leaves untagged points alone", queuedPoints(), ["legacy1:-"]);
	await message(w, "flush");
	check("they upload under the session, with no offline_key", net.logged, ["legacy1:pat"]);
});

scenario("a deploy drops the previous deploy's answers", async () => {
	const { page } = await patsPhone();
	const w = loadWorker("BUILD2");
	await lifecycle(w);
	check("only this deploy's cache is left", [...store.keys()], ["time-kiosk-BUILD2"]);
	check("no kept answers survive the deploy", keptAnswers(), []);
	net.online = false;
	check("offline, nothing old replays", (await call(w, page, DAY)).status, 504);
	net.online = true;
	await call(w, page, DAY);
	check("the new worker keeps its own, in its own cache", keptAnswers(), ["time-kiosk-BUILD2|get_my_day|pat"]);
});

scenario("a worker restarted mid-shift still keeps one person at a time", async () => {
	await patsPhone();
	// The browser stops an idle worker and starts it again: same caches, no memory of whose they are.
	const again = loadWorker("BUILD1");
	await signIn(again, SAM);
	const sams = await openKiosk(again);
	await call(again, sams, STATUS);
	check("Sam's first answer still forgets Pat's", keptAnswers(), ["time-kiosk-BUILD1|get_current_status|sam"]);
});

scenario("the email never reaches the server, and neither does the cache key", async () => {
	await patsPhone();
	check("every X-Kiosk-Key the server saw is Pat's marker, 64 hex characters", [...new Set(net.sentHeaders)], [PATK]);
	check("… which is not the email", /^[0-9a-f]{64}$/.test(PATK) && !PATK.includes("pat"), true);
	check("no request carries the cache key's parameter", net.log.filter((l) => l.includes("__kiosk_key")), []);
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
