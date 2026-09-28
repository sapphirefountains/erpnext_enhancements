"""What the /itinerary service worker may keep, where, and for whom (Plan a Trip program, PR 4).

``www/itinerary-sw.js`` keeps a traveler's itinerary page and files on their phone, so a boarding
pass still opens in airplane mode. Four properties matter. Each is pinned twice: as a claim about
the source that a text assertion can hold (no browser runs in CI), and by running the worker once
in node over a stand-in Cache Storage and network (``TestTheWorkerRuns``; skipped without node).

1. **Its scope is /itinerary, never "/".** The Time Kiosk and the Wall Display both register at the
   site root, and a registration is keyed by its scope: a second worker at "/" replaces the
   kiosk's, geolocation queue and all, on every technician's phone. A worker scoped to /itinerary
   only ever sees the requests /itinerary makes.
2. **It deletes only its own old caches.** The kiosk and wall workers used to delete every cache on
   the site but their own at each deploy, which would have wiped this one's offline copy on any
   phone that also opened /kiosk (fixed alongside; ``test_kiosk_service_worker`` pins that side).
   This worker must not do the same to them, nor to its own per-person files caches, which outlive
   a deploy.
3. **A phone can be shared.** Files are kept per signed-in person (``itinerary-files-<user>``), the
   page's 'user' message deletes everybody else's, and 'purge' deletes them all.
4. **Only a real answer is kept**, from this site: a 200, not redirected (a signed-out request is
   sent to the login page, which kept as "the itinerary" would be worse than nothing), never another
   site's file, and a response that is both kept and returned is cloned before it is returned.

It is also a Jinja template: Frappe serves ``www/*.js`` through TemplatePage, so a double brace or a
Jinja tag anywhere in it (comments included) is rendered, or breaks the render.

The page side (IndexedDB, the offline fallback, the refusal for another person) is driven in a fake
browser by ``scripts/test_web_flow_history.js itinerary`` (run by ``test_travel_planner``).

Run: python -m unittest erpnext_enhancements.tests.test_itinerary_service_worker -v
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
WORKER = APP / "www" / "itinerary-sw.js"
MANIFEST = APP / "www" / "itinerary-manifest.json"
SHELL = APP / "www" / "itinerary.html"
PAGE_JS = APP / "public" / "js" / "travel" / "itinerary.js"


def _read(path):
	return path.read_text(encoding="utf-8")


def _code(path=WORKER):
	"""The source with comments stripped.

	The prose in these files explains the very mistakes asserted against (a worker at "/", an
	activate step that deletes every cache), so a search over the raw text would match the
	explanation and pass while the code did the wrong thing. A `//` after a colon or a quote is
	part of a string (an address), not a comment.
	"""
	src = re.sub(r"/\*.*?\*/", "", _read(path), flags=re.S)
	return re.sub(r"(^|[^:\"'`])//[^\n]*", r"\1", src)


def _block(code, start_marker):
	"""The brace-balanced block that starts at `start_marker` (a handler or a function)."""
	start = code.index(start_marker)
	depth, opened = 0, False
	for end in range(start, len(code)):
		if code[end] == "{":
			depth += 1
			opened = True
		elif code[end] == "}":
			depth -= 1
			if opened and depth == 0:
				return code[start : end + 1]
	return code[start:]


def _handler(event):
	return _block(_code(), f"self.addEventListener('{event}'")


def _function(name, path=WORKER):
	code = _code(path)
	marker = f"async function {name}(" if f"async function {name}(" in code else f"function {name}("
	return _block(code, marker)


class TestTheWorkerIsReadable(unittest.TestCase):
	"""Guards every assertion below from passing because a parse went stale."""

	def test_it_exists_and_has_its_four_handlers(self):
		self.assertTrue(WORKER.exists())
		for event in ("install", "activate", "fetch", "message"):
			with self.subTest(event=event):
				self.assertIn("event", _handler(event))
		self.assertIn("respondWith", _handler("fetch"))

	def test_it_parses(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		result = subprocess.run([node, "--check", str(WORKER)], capture_output=True, text=True, check=False)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_it_uses_self_clients_not_a_bare_global(self):
		"""eslint's browser env has no bare `clients`; the kiosk worker's rule."""
		code = _code()
		self.assertIn("self.clients.claim()", code)
		self.assertIsNone(re.search(r"(?<![.\w])clients\.", code))


class TestItIsAJinjaTemplate(unittest.TestCase):
	"""TemplatePage renders www/*.js (and the manifest) through Jinja: only a file ending in min.js
	is passed through raw. So the RAW text, comments and strings included, may not open a Jinja
	tag. TemplatePage also replaces a literal `{index}` and `{next}` after rendering."""

	def test_the_worker_opens_no_jinja_tag(self):
		raw = _read(WORKER)
		for tag in ("{{", "}}", "{%", "%}", "{#", "#}", "{index}", "{next}"):
			self.assertNotIn(
				tag, raw, f"itinerary-sw.js contains {tag!r}, which Jinja or TemplatePage would rewrite"
			)

	def test_the_manifest_opens_no_jinja_tag(self):
		raw = _read(MANIFEST)
		for tag in ("{{", "}}", "{%", "{#", "{index}", "{next}"):
			self.assertNotIn(tag, raw)

	def test_neither_file_has_a_controller(self):
		"""A controller beside a template with no page is an orphan scripts/check_www_controllers.py
		refuses; and neither file wants one."""
		for stem in ("itinerary_sw", "itinerary-sw", "itinerary_manifest", "itinerary-manifest"):
			self.assertFalse((APP / "www" / f"{stem}.py").exists(), stem)


class TestItsScopeIsTheItineraryOnly(unittest.TestCase):
	def _register(self):
		code = _code(PAGE_JS)
		calls = re.findall(r"serviceWorker\.register\(([^;]*)\)", code)
		self.assertEqual(len(calls), 1, calls)
		return calls[0], _function("registerWorker", PAGE_JS)

	def test_the_page_registers_it_for_itinerary_only(self):
		args, _fn = self._register()
		self.assertIn("'/itinerary-sw.js?v=' +", args)
		self.assertIn("{ scope: '/itinerary' }", args)
		self.assertNotIn("scope: '/'", _code(PAGE_JS))

	def test_the_address_carries_the_deploy_token(self):
		"""A deploy is a new script address, so a new worker with a new shell cache."""
		_args, fn = self._register()
		self.assertIn("window.ITIN_BUILD", fn)
		self.assertIn("ITIN_BUILD", _read(SHELL))

	def test_registering_is_guarded_and_cannot_throw_into_the_page(self):
		"""The Back/Forward harness gives the page `navigator: {}`; old browsers have no workers."""
		_args, fn = self._register()
		self.assertIn("'serviceWorker' in navigator", fn)
		self.assertIn("try {", fn)
		self.assertIn(".catch(", fn)
		self.assertLess(fn.index("'serviceWorker' in navigator"), fn.index(".register("))


class TestItsCachesAreItsOwn(unittest.TestCase):
	def test_every_cache_it_names_starts_itinerary(self):
		code = _code()
		self.assertIn("const SHELL_PREFIX = 'itinerary-shell-';", code)
		self.assertIn("const SHELL = SHELL_PREFIX + VERSION;", code)
		self.assertIn("const FILES_PREFIX = 'itinerary-files-';", code)
		self.assertIn("return FILES_PREFIX + encodeURIComponent(user);", _function("filesCacheFor"))
		# No cache is ever opened, checked or deleted by a literal name.
		self.assertIsNone(re.search(r"caches\.(open|delete|has)\(\s*['\"`]", code))

	def test_activate_deletes_only_its_own_old_shells(self):
		activate = _handler("activate")
		self.assertIn("keys.filter((k) => k.startsWith(SHELL_PREFIX) && k !== SHELL)", activate)
		self.assertEqual(activate.count("caches.delete("), 1)
		self.assertIn("old.map((k) => caches.delete(k))", activate)
		# The files caches outlive a deploy: activate never touches them.
		self.assertNotIn("FILES_PREFIX", activate)
		# The old wipe-everything filter, in any spelling.
		self.assertIsNone(re.search(r"filter\(\(k\) => k !== \w+\)", activate))

	def test_a_deploy_keeps_the_page(self):
		"""The page that registers a new worker was loaded by the old one, into the old shell, which
		activate deletes: without carrying it forward (or fetching it at install) a deploy would
		leave the phone with no page to open offline."""
		self.assertIn("fetch(new Request(PAGE", _handler("install"))
		activate = _handler("activate")
		self.assertIn("shell.match(PAGE)", activate)
		self.assertLess(activate.index("shell.put(PAGE, kept)"), activate.index("caches.delete("))


class TestFilesAreKeptPerPerson(unittest.TestCase):
	def test_the_user_message_deletes_everybody_elses_files(self):
		body = _function("forUser")
		self.assertIn("if (!isPerson(user)) return;", body)
		self.assertIn("k.startsWith(FILES_PREFIX) && k !== own", body)
		self.assertIn("caches.delete(k)", body)

	def test_nobody_and_guest_are_nobody(self):
		self.assertIn("user !== '' && user !== 'Guest'", _function("isPerson"))

	def test_files_are_kept_for_the_person_named(self):
		body = _function("keepFiles")
		self.assertIn("if (!isPerson(user) || !Array.isArray(urls)) return;", body)
		self.assertIn("if (user !== currentUser) await forUser(user);", body)
		self.assertIn("caches.open(filesCacheFor(user))", body)
		# One message cannot fill the phone, and a person's cache has a ceiling.
		self.assertIn("urls.slice(0, MAX_PER_MESSAGE)", body)
		self.assertIn("MAX_FILES", body)

	def test_purge_forgets_everything_personal(self):
		body = _function("purge")
		self.assertIn("currentUser = null;", body)
		self.assertIn("k.startsWith(FILES_PREFIX)", body)
		self.assertIn(".delete(PAGE)", body)

	def test_the_three_messages(self):
		message = _handler("message")
		for kind in ("'user'", "'cache-files'", "'purge'"):
			self.assertIn(f"data.type === {kind}", message)
		self.assertIn("event.waitUntil(", message)


class TestOnlyRealAnswersFromThisSiteAreKept(unittest.TestCase):
	def test_keepable_is_a_200_from_this_site_not_redirected(self):
		body = _function("keepable")
		for part in ("res.ok", "res.type === 'basic'", "!res.redirected"):
			self.assertIn(part, body)

	def test_every_put_is_behind_keepable(self):
		"""Every place a response from the network is put in a cache checks it first. The one other
		put is activate's carrying forward of the page the previous shell already vetted."""
		for name, body in (
			("install", _handler("install")),
			("pageFromNetworkOrKept", _function("pageFromNetworkOrKept")),
			("shellAsset", _function("shellAsset")),
			("fileFromNetworkOrKept", _function("fileFromNetworkOrKept")),
			("keepFiles", _function("keepFiles")),
		):
			with self.subTest(where=name):
				self.assertIn(".put(", body)
				self.assertIn("keepable(", body)
				self.assertLess(body.index("keepable("), body.index(".put("))
		code = _code()
		puts = code.count(".put(")
		self.assertEqual(puts, 6, "a new cache.put: put it behind keepable() and list it above")

	def test_it_answers_only_this_sites_get_requests(self):
		fetch = _handler("fetch")
		self.assertIn("if (req.method !== 'GET') return;", fetch)
		self.assertIn("if (url.origin !== self.location.origin) return;", fetch)
		self.assertLess(fetch.index("self.location.origin"), fetch.index("respondWith"))

	def test_a_file_is_this_sites_own_file_address(self):
		code = _code()
		self.assertIn("const FILE_PATHS = ['/private/files/', '/files/'];", code)
		body = _function("fileHref")
		self.assertIn("url.origin !== self.location.origin || !isFilePath(url.pathname)", body)
		# The files message goes through fileHref, so another site's address is never fetched.
		self.assertIn("const href = fileHref(raw);", _function("keepFiles"))

	def test_it_answers_nothing_else(self):
		"""Its own page, its own two assets, and files: never the API (every call is a POST, and an
		answer belongs in the page's per-person IndexedDB), never the app's asset root."""
		code = _code()
		self.assertNotIn("/api/", code)
		self.assertNotIn("startsWith('/assets/", code)
		self.assertIn("const PRECACHE_PATHS = new Set(PRECACHE);", code)
		fetch = _handler("fetch")
		self.assertIn("req.mode === 'navigate' && url.pathname === PAGE", fetch)
		self.assertIn("PRECACHE_PATHS.has(url.pathname)", fetch)
		self.assertIn("isFilePath(url.pathname)", fetch)

	def test_ignore_search_is_only_for_its_own_two_assets(self):
		"""`ignoreSearch` reduces a ?v= token to decoration (the kiosk worker's v1.229.0 lesson). Here
		it is used once, inside shellAsset, which only the PRECACHE_PATHS branch calls, and only on
		this worker's own shell cache."""
		code = _code()
		self.assertEqual(code.count("ignoreSearch"), 1)
		self.assertIn("shell.match(req, { ignoreSearch: true })", _function("shellAsset"))
		self.assertNotIn("caches.match(", code)
		fetch = _handler("fetch")
		self.assertLess(fetch.index("PRECACHE_PATHS.has"), fetch.index("shellAsset("))

	def test_a_response_both_kept_and_returned_is_cloned_first(self):
		"""Cloned synchronously into a variable before the response is handed back, while its body
		is unread; a clone inside a later `.then` throws "body is already used"."""
		code = _code()
		self.assertIsNone(re.search(r"put\([^)]*\.clone\(\)\)", code))
		self.assertIn("const copy = res.clone();", _function("pageFromNetworkOrKept"))
		self.assertIn("const copy = res.clone();", _function("shellAsset"))
		self.assertIn("const copy = fresh.clone();", _function("fileFromNetworkOrKept"))


class TestPrecacheMatchesTheShell(unittest.TestCase):
	"""The worker precaches exactly the stylesheet and script itinerary.html loads with ?v=: one
	missing is a page with no styles offline, one extra is an answer for a file the page never
	asks for."""

	def test_the_two_lists_agree(self):
		html = _read(SHELL)
		shell = set(
			re.findall(
				r"(/assets/erpnext_enhancements/(?:js|css)/[\w./-]+)\?v=\{\{ deploy_version \}\}", html
			)
		)
		code = _code()
		block = code[code.index("const PRECACHE = [") : code.index("];", code.index("const PRECACHE = ["))]
		precache = set(re.findall(r"'([^']+)'", block))
		self.assertTrue(precache)
		self.assertEqual(precache, shell)


class TestTheHomeScreenApp(unittest.TestCase):
	def setUp(self):
		self.manifest = json.loads(_read(MANIFEST))

	def test_its_name_and_scope(self):
		m = self.manifest
		self.assertEqual(m["name"], "Sapphire Itinerary")
		self.assertEqual(m["start_url"], "/itinerary")
		self.assertEqual(m["scope"], "/itinerary")
		self.assertEqual(m["id"], "/itinerary")
		self.assertEqual(m["display"], "standalone")

	def test_its_icons_exist(self):
		icons = self.manifest["icons"]
		self.assertTrue(icons)
		for icon in icons:
			with self.subTest(icon=icon["src"]):
				prefix = "/assets/erpnext_enhancements/"
				self.assertTrue(icon["src"].startswith(prefix))
				self.assertTrue((APP / "public" / icon["src"][len(prefix) :]).exists())

	def test_the_shell_links_it(self):
		html = _read(SHELL)
		self.assertIn('<link rel="manifest" href="/itinerary-manifest.json', html)
		head = html[
			html.index("{% block head_include %}") : html.index(
				"{% endblock %}", html.index("{% block head_include %}")
			)
		]
		self.assertIn("itinerary-manifest.json", head)


class TestThePageSide(unittest.TestCase):
	"""What itinerary.js promises: saved answers in its own database, touched only inside a try;
	"offline" only when fetch itself fails; nothing saved shown to anybody but its person."""

	def test_indexeddb_is_reached_in_one_place_inside_a_try(self):
		code = _code(PAGE_JS)
		self.assertEqual(code.count("indexedDB"), 1)
		body = _function("storageFactory", PAGE_JS)
		self.assertIn("window.indexedDB", body)
		self.assertLess(body.index("try {"), body.index("window.indexedDB"))
		self.assertIn("var SAVED_DB = 'sapphire-itinerary';", code)
		self.assertIn("var ANSWERS = 'answers';", code)
		self.assertIn("return user + '|' + trip + '|' + (as || '');", _function("savedKey", PAGE_JS))

	def test_offline_is_only_fetch_failing(self):
		"""A refusal (403, 404, 417) or a server error is an answer, handled as before; only a
		fetch that never got one draws the saved copy."""
		api = _function("api", PAGE_JS)
		self.assertEqual(_code(PAGE_JS).count("unreachable = true"), 1)
		self.assertIn("}).then(null, function (err) {", api)
		self.assertLess(api.index("unreachable = true"), api.index("if (!res.ok)"))

	def test_another_persons_copy_is_never_shown(self):
		code = _code(PAGE_JS)
		self.assertIn(
			"return !!boot && !!cookie && cookie !== boot;", _function("shellIsSomeoneElses", PAGE_JS)
		)
		self.assertIn("if (shellIsSomeoneElses()) {", _function("showSaved", PAGE_JS))
		self.assertIn("saved.user === user", _function("readAnswer", PAGE_JS))
		self.assertIn("'Sign in to see your itinerary.'", code)

	def test_the_offline_code_writes_no_history(self):
		for name in (
			"showSaved",
			"keepForOffline",
			"saveAhead",
			"keepFiles",
			"registerWorker",
			"toWorker",
			"startOffline",
			"openSavedWhenOffline",
			"openSavedCopy",
			"pruneSaved",
		):
			body = _function(name, PAGE_JS)
			for forbidden in ("writeTripEntry", "pushState", "replaceState", "history."):
				self.assertNotIn(forbidden, body, f"{name} calls {forbidden}")


# The worker run once, in node, over a stand-in Cache Storage and network: a phone that already
# holds the previous deploy's shell, the kiosk's cache and another person's files. It prints what
# it saw as JSON; TestTheWorkerRuns asserts on it. Kept here, beside the static reads, so the
# two cannot drift apart.
_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const ORIGIN = "https://erp.example.com";
const seen = {};

class FakeResponse {
	constructor(body, init) {
		init = init || {};
		this.body = body;
		this.status = init.status == null ? 200 : init.status;
		this.ok = this.status >= 200 && this.status < 300;
		this.type = init.type || "basic";
		this.redirected = !!init.redirected;
		const headers = init.headers || {};
		this.headers = { get: (k) => headers[k.toLowerCase()] || null, has: (k) => k.toLowerCase() in headers };
	}
	clone() { return new FakeResponse(this.body, this); }
}
class FakeRequest {
	constructor(url, init) {
		init = init || {};
		this.url = new URL(typeof url === "string" ? url : url.url, ORIGIN).href;
		this.method = init.method || "GET";
		this.mode = init.mode || "cors";
		const headers = init.headers || {};
		this.headers = { has: (k) => k in headers };
	}
}
const keyOf = (req, ignoreSearch) => {
	const u = new URL(typeof req === "string" ? req : req.url, ORIGIN);
	if (ignoreSearch) u.search = "";
	return u.href;
};
const store = new Map();
const caches = {
	async open(name) {
		if (!store.has(name)) store.set(name, new Map());
		const m = store.get(name);
		return {
			async match(req, opts) {
				const loose = !!(opts && opts.ignoreSearch);
				for (const [k, v] of m) if (keyOf(k, loose) === keyOf(req, loose)) return v.clone();
				return undefined;
			},
			async put(req, res) { m.delete(keyOf(req)); m.set(keyOf(req), res); },
			async add(req) { const res = await net.fetch(req); if (!res.ok) throw new Error("add"); m.set(keyOf(req), res); },
			async delete(req) { return m.delete(keyOf(req)); },
			async keys() { return [...m.keys()].map((k) => new FakeRequest(k)); },
		};
	},
	async keys() { return [...store.keys()]; },
	async has(name) { return store.has(name); },
	async delete(name) { return store.delete(name); },
};
const net = {
	online: true,
	pages: {},
	log: [],
	async fetch(req) {
		const url = typeof req === "string" ? new URL(req, ORIGIN).href : req.url;
		net.log.push(url.replace(ORIGIN, ""));
		if (!net.online) throw new TypeError("Failed to fetch");
		const page = net.pages[new URL(url).pathname];
		return page ? new FakeResponse(page.body, page) : new FakeResponse("", { status: 404 });
	},
};
const self = {
	location: { href: ORIGIN + "/itinerary-sw.js?v=200", origin: ORIGIN },
	listeners: {},
	addEventListener(type, fn) { this.listeners[type] = fn; },
	skipWaiting() {},
	clients: { claim: async () => {} },
};
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), vm.createContext({
	self, caches, fetch: (r) => net.fetch(r), Request: FakeRequest, Response: FakeResponse, URL,
	Promise, setTimeout, clearTimeout, Number, Set, Array, encodeURIComponent,
}));
async function fire(type, data) {
	let waited = null, answer = null;
	self.listeners[type](Object.assign({ waitUntil(p) { waited = p; }, respondWith(p) { answer = p; } }, data));
	if (waited) await waited;
	return answer ? await answer : undefined;
}
const get = (path, init) => fire("fetch", { request: new FakeRequest(path, init) });
const names = () => [...store.keys()].sort();
const kept = async (name, key) => { const r = await (await caches.open(name)).match(key); return r ? r.body : null; };
const settle = () => new Promise((r) => setTimeout(r, 5));

(async () => {
	net.pages["/assets/erpnext_enhancements/css/travel/itinerary.css"] = { body: "css" };
	net.pages["/assets/erpnext_enhancements/js/travel/itinerary.js"] = { body: "js" };
	net.pages["/private/files/pass.png"] = { body: "PASS" };
	net.pages["/private/files/conf.pdf"] = { body: "CONF" };
	await (await caches.open("itinerary-shell-100")).put("/itinerary", new FakeResponse("OLD PAGE"));
	await (await caches.open("time-kiosk-55")).put("/kiosk", new FakeResponse("KIOSK"));
	await (await caches.open("itinerary-files-sam%40example.com")).put(ORIGIN + "/files/x.png", new FakeResponse("SAM"));

	// The deploy: install cannot fetch the page (it answers 404 here), so activate carries the old
	// shell's copy forward before deleting that shell.
	await fire("install", {});
	await fire("activate", {});
	seen.after_activate = names();
	seen.page_carried = await kept("itinerary-shell-200", "/itinerary");

	await fire("message", { data: { type: "user", user: "pat@example.com" } });
	seen.after_user = names();
	await fire("message", { data: { type: "cache-files", user: "pat@example.com", urls: [
		"/private/files/pass.png", "https://evil.example/x.pdf", "/api/method/frappe.auth.get_logged_user",
		"/files/../api/method/x", "/private/files/conf.pdf", "/private/files/missing.pdf",
	] } });
	seen.files_kept = [...store.get("itinerary-files-pat%40example.com").keys()].map((k) => k.replace(ORIGIN, ""));
	net.log.length = 0;
	await fire("message", { data: { type: "cache-files", user: "pat@example.com", urls: ["/private/files/pass.png"] } });
	seen.refetched = net.log.slice();

	net.pages["/itinerary"] = { body: "FRESH PAGE" };
	seen.online_page = (await get("/itinerary?trip=T1", { mode: "navigate" })).body;
	await settle();
	seen.page_kept = await kept("itinerary-shell-200", "/itinerary");
	net.pages["/itinerary"] = { body: "", status: 0, type: "opaqueredirect" };
	seen.signed_out_page = (await get("/itinerary", { mode: "navigate" })).type;
	await settle();
	seen.page_after_redirect = await kept("itinerary-shell-200", "/itinerary");
	net.pages["/itinerary"] = { body: "Bad Gateway", status: 502 };
	seen.page_on_502 = (await get("/itinerary?trip=T2", { mode: "navigate" })).body;

	net.online = false;
	seen.offline_page = (await get("/itinerary?trip=T9&as=crew&view=docs", { mode: "navigate" })).body;
	seen.offline_script = (await get("/assets/erpnext_enhancements/js/travel/itinerary.js?v=100")).body;
	seen.offline_file = (await get("/private/files/pass.png")).body;
	try { await get("/private/files/never.png"); seen.offline_unkept = "answered"; } catch (e) { seen.offline_unkept = e.name; }
	seen.other_request = (await get("/assets/frappe/dist/css/website.bundle.css")) === undefined ? "passed through" : "answered";
	seen.api_request = (await get("/api/method/erpnext_enhancements.api.travel.get_trip_itinerary")) === undefined ? "passed through" : "answered";

	await fire("message", { data: { type: "purge" } });
	seen.after_purge = names();
	seen.page_after_purge = await kept("itinerary-shell-200", "/itinerary");
	const nothing = await get("/itinerary", { mode: "navigate" });
	seen.nothing_kept = [nothing.status, /not saved on this phone yet/.test(nothing.body)];
	console.log(JSON.stringify(seen));
})().catch((e) => { console.error(e && e.stack); process.exit(1); });
"""


class TestTheWorkerRuns(unittest.TestCase):
	"""The worker itself, run in node over a stand-in Cache Storage and network (no browser)."""

	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not installed")
		with tempfile.TemporaryDirectory() as tmp:
			driver = os.path.join(tmp, "itinerary_worker_driver.js")
			with open(driver, "w", encoding="utf-8") as f:
				f.write(_DRIVER)
			result = subprocess.run(
				[node, driver, str(WORKER)],
				capture_output=True,
				text=True,
				encoding="utf-8",
				check=False,
				timeout=60,
			)
		if result.returncode != 0:
			raise AssertionError(result.stdout + result.stderr)
		cls.seen = json.loads(result.stdout.strip().splitlines()[-1])

	def test_a_deploy_deletes_only_its_own_old_shell_and_keeps_the_page(self):
		self.assertEqual(
			self.seen["after_activate"],
			["itinerary-files-sam%40example.com", "itinerary-shell-200", "time-kiosk-55"],
		)
		self.assertEqual(self.seen["page_carried"], "OLD PAGE")

	def test_the_user_message_deletes_everybody_elses_files(self):
		self.assertEqual(self.seen["after_user"], ["itinerary-shell-200", "time-kiosk-55"])

	def test_only_this_sites_files_that_answered_200_are_kept_and_only_once(self):
		self.assertEqual(self.seen["files_kept"], ["/private/files/pass.png", "/private/files/conf.pdf"])
		self.assertEqual(self.seen["refetched"], [])

	def test_the_page_is_the_networks_and_is_kept_unless_it_is_a_redirect(self):
		self.assertEqual(self.seen["online_page"], "FRESH PAGE")
		self.assertEqual(self.seen["page_kept"], "FRESH PAGE")
		self.assertEqual(self.seen["signed_out_page"], "opaqueredirect")
		self.assertEqual(self.seen["page_after_redirect"], "FRESH PAGE")
		self.assertEqual(self.seen["page_on_502"], "FRESH PAGE")

	def test_offline_the_kept_page_script_and_files_answer(self):
		self.assertEqual(self.seen["offline_page"], "FRESH PAGE")
		# A page kept before a deploy asks for its own ?v=; this deploy's copy answers.
		self.assertEqual(self.seen["offline_script"], "js")
		self.assertEqual(self.seen["offline_file"], "PASS")
		self.assertEqual(self.seen["offline_unkept"], "TypeError")

	def test_nothing_else_is_answered(self):
		self.assertEqual(self.seen["other_request"], "passed through")
		self.assertEqual(self.seen["api_request"], "passed through")

	def test_purge_forgets_the_files_and_the_page(self):
		self.assertEqual(self.seen["after_purge"], ["itinerary-shell-200", "time-kiosk-55"])
		self.assertIsNone(self.seen["page_after_purge"])
		self.assertEqual(self.seen["nothing_kept"], [503, True])


if __name__ == "__main__":
	unittest.main()
