#!/usr/bin/env node
/**
 * Guards the capture panel's offline drafts (WI-079 slice 2), and one rule above the rest:
 * **a draft is never sent as anybody but the person who wrote it.**
 *
 * A kiosk tablet is shared. A report saved offline by one person, then sent after somebody
 * else signs in, would file under the second person's name with the first person's
 * screenshot attached. So the assertions below pin the rules that prevent it: `listDrafts`
 * returns only the caller's drafts, `pruneForUser` deletes everyone else's and anything past
 * the 7-day limit, and an empty or Guest user owns nothing.
 *
 * Two layers. The pure rules (`isExpired`, `partitionDrafts`) are called directly. The
 * storage calls run against a small in-memory IndexedDB below — just enough of the API
 * (open/upgrade, one store, getAll/add/delete/clear, transaction `complete`) to exercise the
 * real save/list/prune/delete code, including the Blob-refused fallback older Safari needs.
 * Without the fake, node has no `indexedDB` at all, and that path is asserted too: every read
 * degrades to "no drafts" rather than throwing into the page.
 *
 * Run: node scripts/test_capture_drafts.js
 */

const path = require("path");
const { pathToFileURL } = require("url");

const TARGET = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "capture", "drafts.js");
const DAY = 24 * 60 * 60 * 1000;

let failures = 0;

function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function truthy(label, value) {
	check(label, !!value, true);
}

async function rejects(label, promise, pattern) {
	try {
		await promise;
		failures += 1;
		console.error(`  FAIL ${label}: resolved, expected a rejection`);
	} catch (err) {
		const message = String((err && err.message) || err);
		if (pattern && !pattern.test(message)) {
			failures += 1;
			console.error(`  FAIL ${label}: rejected with "${message}"`);
		} else {
			console.log(`  ok   ${label}`);
		}
	}
}

/**
 * The smallest IndexedDB that behaves like the real one where drafts.js depends on it:
 * requests settle asynchronously and in order, `complete` fires once no request is pending,
 * and a synchronous throw from `add` (DataCloneError) surfaces from the call itself.
 */
function fakeIndexedDB(options) {
	const opts = options || {};
	const databases = {};

	function makeDb() {
		const stores = {};
		return {
			objectStoreNames: { contains: (name) => Object.prototype.hasOwnProperty.call(stores, name) },
			createObjectStore(name) {
				stores[name] = { rows: new Map(), seq: 0 };
				return { createIndex() {} };
			},
			close() {},
			transaction(name) {
				const store = stores[name];
				const tx = { error: null, done: false };
				let pending = 0;
				const settle = () =>
					setTimeout(() => {
						if (pending === 0 && !tx.done) {
							tx.done = true;
							if (tx.oncomplete) tx.oncomplete();
						}
					}, 0);
				const request = (work) => {
					const req = {};
					pending += 1;
					setTimeout(() => {
						if (tx.done) return;
						req.result = work();
						pending -= 1;
						if (req.onsuccess) req.onsuccess();
						settle();
					}, 0);
					return req;
				};
				tx.abort = () => {
					if (tx.done) return;
					tx.done = true;
					if (tx.onabort) setTimeout(() => tx.onabort(), 0);
				};
				tx.objectStore = () => ({
					getAll: () => request(() => Array.from(store.rows.values()).map((row) => ({ ...row }))),
					add: (record) => {
						if (opts.refuseBlobs && record.image instanceof Blob) {
							const err = new Error("The object could not be cloned.");
							err.name = "DataCloneError";
							throw err;
						}
						return request(() => {
							store.seq += 1;
							store.rows.set(store.seq, { ...record, id: store.seq });
							return store.seq;
						});
					},
					delete: (id) =>
						request(() => {
							store.rows.delete(id);
						}),
					clear: () =>
						request(() => {
							store.rows.clear();
						}),
				});
				settle();
				return tx;
			},
			_rows: (name) => Array.from(stores[name].rows.values()),
		};
	}

	return {
		open(name) {
			const req = {};
			setTimeout(() => {
				const fresh = !databases[name];
				if (fresh) databases[name] = makeDb();
				req.result = databases[name];
				if (fresh && req.onupgradeneeded) req.onupgradeneeded();
				if (req.onsuccess) req.onsuccess();
			}, 0);
			return req;
		},
		_db: (name) => databases[name],
	};
}

(async () => {
	let D;
	try {
		D = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error("COULD NOT LOAD drafts.js — it must stay importable without a browser.");
		console.error(err && err.message);
		process.exit(2);
	}

	for (const name of ["isExpired", "partitionDrafts", "saveDraft", "listDrafts", "deleteDraft", "pruneForUser", "clearDrafts"]) {
		if (typeof D[name] !== "function") {
			console.error(`MARKER NOT FOUND: drafts.js no longer exports ${name}()`);
			process.exit(2);
		}
	}

	const now = 1_800_000_000_000;

	console.log("\nthe 7-day limit");
	check("DRAFT_TTL_MS is seven days", D.DRAFT_TTL_MS, 7 * DAY);
	check("fresh draft is kept", D.isExpired({ created_at: now - 1000 }, now), false);
	check("exactly seven days is kept", D.isExpired({ created_at: now - 7 * DAY }, now), false);
	check("a millisecond past is expired", D.isExpired({ created_at: now - 7 * DAY - 1 }, now), true);
	check("no timestamp is expired", D.isExpired({}, now), true);
	check("garbage timestamp is expired", D.isExpired({ created_at: "soon" }, now), true);
	check("a stamp from a clock far in the future is expired", D.isExpired({ created_at: now + 8 * DAY }, now), true);
	check("a little clock skew is tolerated", D.isExpired({ created_at: now + 60000 }, now), false);

	console.log("\nonly the caller's own drafts are kept");
	const stored = [
		{ id: 1, user: "bob@example.com", created_at: now - 1000 },
		{ id: 2, user: "alice@example.com", created_at: now - 3000 },
		{ id: 3, user: "alice@example.com", created_at: now - 8 * DAY },
		{ id: 4, user: "alice@example.com", created_at: now - 2000 },
		null,
		"junk",
	];
	const split = D.partitionDrafts(stored, "alice@example.com", now);
	check("alice keeps her unexpired drafts, oldest first", split.keep.map((d) => d.id), [2, 4]);
	check("bob's draft and alice's expired one are dropped", split.drop.map((d) => d.id).sort(), [1, 3]);
	check("Guest owns nothing", D.partitionDrafts(stored, "Guest", now).keep, []);
	check("an empty user owns nothing", D.partitionDrafts(stored, "", now).keep, []);
	check("an unknown user owns nothing", D.partitionDrafts(stored, "carol@example.com", now).keep, []);
	check("non-array input is empty, not a throw", D.partitionDrafts(null, "alice@example.com", now), { keep: [], drop: [] });

	console.log("\nwith no IndexedDB at all, nothing throws into the page");
	check("draftsSupported() is false", D.draftsSupported(), false);
	check("listDrafts resolves empty", await D.listDrafts("alice@example.com"), []);
	check("pruneForUser resolves 0", await D.pruneForUser("alice@example.com"), 0);
	check("clearDrafts resolves false", await D.clearDrafts(), false);
	await rejects("saveDraft says the device cannot save", D.saveDraft({ user: "alice@example.com", payload: {} }), /cannot save/);
	await rejects("saveDraft refuses Guest before touching storage", D.saveDraft({ user: "Guest" }), /Sign in/);

	console.log("\nagainst an IndexedDB: save, list, isolate, prune");
	const idb = fakeIndexedDB();
	globalThis.indexedDB = idb;
	check("draftsSupported() is true", D.draftsSupported(), true);

	const png = new Blob([new Uint8Array([137, 80, 78, 71, 1, 2, 3])], { type: "image/png" });
	const aliceId = await D.saveDraft({
		user: "alice@example.com",
		payload: { title: "Save button does nothing", request_type: "Bug" },
		snapshot: { schema: 1, page: { path: "/kiosk" } },
		image: png,
		surface: "kiosk",
	});
	truthy("saveDraft resolves an id", aliceId !== undefined && aliceId !== null);
	await D.saveDraft({ user: "bob@example.com", payload: { title: "Bob's report" }, snapshot: null, image: null });

	const alices = await D.listDrafts("alice@example.com");
	check("alice sees exactly her draft", alices.map((d) => d.payload.title), ["Save button does nothing"]);
	truthy("the screenshot comes back as a Blob", alices[0].image instanceof Blob);
	check("with the same bytes", Array.from(new Uint8Array(await alices[0].image.arrayBuffer())), [137, 80, 78, 71, 1, 2, 3]);
	check("the snapshot survives", alices[0].snapshot, { schema: 1, page: { path: "/kiosk" } });
	check("bob sees only his", (await D.listDrafts("bob@example.com")).map((d) => d.payload.title), ["Bob's report"]);
	check("Guest sees nothing", await D.listDrafts("Guest"), []);

	check("pruning for bob deletes alice's draft", await D.pruneForUser("bob@example.com"), 1);
	check("alice's draft is gone from storage", (await D.listDrafts("alice@example.com")).length, 0);
	check("bob's is still there", (await D.listDrafts("bob@example.com")).length, 1);

	console.log("\nexpired drafts are hidden, then pruned");
	const realNow = Date.now;
	Date.now = () => realNow() - 8 * DAY;
	try {
		await D.saveDraft({ user: "bob@example.com", payload: { title: "old" }, snapshot: null, image: null });
	} finally {
		Date.now = realNow;
	}
	check("the week-old draft is not listed", (await D.listDrafts("bob@example.com")).map((d) => d.payload.title), ["Bob's report"]);
	check("and pruning removes it", await D.pruneForUser("bob@example.com"), 1);

	console.log("\ndelete and clear");
	const [bobs] = await D.listDrafts("bob@example.com");
	await D.deleteDraft(bobs.id);
	check("deleteDraft removes it", (await D.listDrafts("bob@example.com")).length, 0);
	await D.saveDraft({ user: "bob@example.com", payload: {}, snapshot: null, image: null });
	check("clearDrafts resolves true", await D.clearDrafts(), true);
	check("and leaves nothing", idb._db("ee-capture")._rows("drafts").length, 0);

	console.log("\na device cannot fill up with drafts");
	for (let i = 0; i < D.MAX_DRAFTS_PER_USER; i++) {
		await D.saveDraft({ user: "carol@example.com", payload: { title: `n${i}` }, snapshot: null, image: null });
	}
	await rejects(
		`draft ${D.MAX_DRAFTS_PER_USER + 1} is refused with a sentence`,
		D.saveDraft({ user: "carol@example.com", payload: {}, snapshot: null, image: null }),
		/Too many reports/
	);
	await D.clearDrafts();

	console.log("\nan engine that refuses Blobs still keeps the screenshot");
	// A fresh module instance, so its connection cache starts empty and opens this store.
	const D2 = await import(pathToFileURL(TARGET).href + "?refuse-blobs");
	const strict = fakeIndexedDB({ refuseBlobs: true });
	globalThis.indexedDB = strict;
	await D2.saveDraft({ user: "dana@example.com", payload: { title: "strict" }, snapshot: null, image: png });
	const rows = strict._db("ee-capture")._rows("drafts");
	check("exactly one draft was stored", rows.length, 1);
	const storedImage = rows[0] && rows[0].image;
	truthy("stored as bytes, not as the refused Blob", storedImage && !(storedImage instanceof Blob) && storedImage.buffer);
	const dana = await D2.listDrafts("dana@example.com");
	check("listed back with a Blob screenshot", dana.length === 1 && dana[0].image instanceof Blob, true);
	check("of the right type", dana.length ? dana[0].image.type : "", "image/png");
	check(
		"rebuilt with the same bytes",
		dana.length ? Array.from(new Uint8Array(await dana[0].image.arrayBuffer())) : [],
		[137, 80, 78, 71, 1, 2, 3]
	);

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("capture drafts: all assertions passed");
})();
