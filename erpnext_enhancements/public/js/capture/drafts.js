/**
 * Reports saved on the device while offline, waiting to be sent.
 *
 * **IndexedDB, in its own database, and never Cache Storage.** Both service workers in this
 * app (`www/kiosk-sw.js` and `www/wall-sw.js`) delete every cache but their own when a new
 * version activates, so a draft kept there would vanish on the next deploy — often exactly
 * when a kiosk that was offline comes back. A separate database (`ee-capture`) also means no
 * other feature's schema upgrade can block or drop it.
 *
 * **Keyed to the user, and never sent as anybody else.** A kiosk tablet is shared. Every
 * draft records who wrote it; `listDrafts` only ever returns the caller's, `pruneForUser`
 * deletes everyone else's along with anything past the 7-day limit, and the panel checks the
 * server's session user before it sends a single draft. A draft from one person reaching the
 * server under another's session would file a report in their name with the first person's
 * screenshot attached.
 *
 * **Nothing here may throw into the page.** Private windows, Safari's intermittent
 * `indexedDB.open` hang and storage-blocked profiles are all normal. Reads degrade to "no
 * drafts"; writes reject with a sentence the panel can show.
 *
 * The pure rules (`isExpired`, `partitionDrafts`) are exported and tested in plain node by
 * `scripts/test_capture_drafts.js`; nothing touches `indexedDB` at import time.
 */

const DB_NAME = "ee-capture";
const DB_VERSION = 1;
const STORE = "drafts";
const OPEN_TIMEOUT_MS = 5000;

export const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
/** A ceiling so a kiosk left offline for a week cannot fill the device with screenshots. */
export const MAX_DRAFTS_PER_USER = 20;

// ------------------------------------------------------------------ pure rules

/**
 * A draft is expired past the 7-day limit — and also when its timestamp is missing or lies
 * more than the limit in the future. A clock that was wrong when the draft was saved would
 * otherwise keep it forever.
 */
export function isExpired(draft, now) {
	const at = Number(draft && draft.created_at);
	const clock = typeof now === "number" ? now : Date.now();
	if (!Number.isFinite(at) || at <= 0) return true;
	return clock - at > DRAFT_TTL_MS || at - clock > DRAFT_TTL_MS;
}

/**
 * Split stored drafts into those `user` may send (oldest first) and those to delete: other
 * users' drafts and expired ones. An empty or Guest user keeps nothing — there is nobody to
 * send them as.
 */
export function partitionDrafts(drafts, user, now) {
	const who = String(user || "");
	const keep = [];
	const drop = [];
	for (const draft of Array.isArray(drafts) ? drafts : []) {
		if (!draft || typeof draft !== "object") continue;
		const mine = who && who !== "Guest" && draft.user === who;
		if (mine && !isExpired(draft, now)) keep.push(draft);
		else drop.push(draft);
	}
	keep.sort((a, b) => Number(a.created_at) - Number(b.created_at));
	return { keep, drop };
}

// ------------------------------------------------------------------ IndexedDB plumbing

let dbPromise = null;

function factory() {
	try {
		return typeof indexedDB !== "undefined" && indexedDB ? indexedDB : null;
	} catch (e) {
		// Some privacy modes throw on the mere property access.
		return null;
	}
}

export function draftsSupported() {
	return !!factory();
}

function openDb() {
	if (dbPromise) return dbPromise;
	dbPromise = new Promise((resolve, reject) => {
		const idb = factory();
		if (!idb) {
			reject(new Error("This device cannot save reports."));
			return;
		}
		// Safari has shipped versions where open() neither succeeds nor fails. Without a
		// timeout the Send button would spin forever.
		const timer = setTimeout(() => reject(new Error("This device's storage did not respond.")), OPEN_TIMEOUT_MS);
		let request;
		try {
			request = idb.open(DB_NAME, DB_VERSION);
		} catch (e) {
			clearTimeout(timer);
			reject(e);
			return;
		}
		request.onupgradeneeded = () => {
			const db = request.result;
			if (!db.objectStoreNames.contains(STORE)) {
				const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
				store.createIndex("user", "user", { unique: false });
			}
		};
		request.onsuccess = () => {
			clearTimeout(timer);
			const db = request.result;
			// Another tab upgrading the schema must not be blocked by this one holding it open.
			db.onversionchange = () => {
				try {
					db.close();
				} catch (e) {
					// Already closed.
				}
				dbPromise = null;
			};
			resolve(db);
		};
		request.onerror = () => {
			clearTimeout(timer);
			reject(request.error || new Error("This device's storage refused the report."));
		};
		request.onblocked = () => {
			clearTimeout(timer);
			reject(new Error("This device's storage is busy in another tab."));
		};
	});
	// A failed open is retried on the next call rather than cached forever.
	dbPromise.catch(() => {
		dbPromise = null;
	});
	return dbPromise;
}

/**
 * Run `fn(store, out)` in one transaction and resolve with `out.value` once it commits.
 * Resolving on `complete` rather than on the request's `success` is the point: a write is
 * not durable until the transaction commits.
 */
function run(mode, fn) {
	return openDb().then(
		(db) =>
			new Promise((resolve, reject) => {
				const out = { value: undefined };
				let tx;
				try {
					tx = db.transaction(STORE, mode);
					tx.oncomplete = () => resolve(out.value);
					tx.onerror = () => reject(tx.error || new Error("This device's storage refused the report."));
					tx.onabort = () => reject(tx.error || new Error("Saving on this device was interrupted."));
					fn(tx.objectStore(STORE), out);
				} catch (e) {
					try {
						if (tx) tx.abort();
					} catch (e2) {
						// Nothing to abort.
					}
					// A closed connection (another tab upgraded) should reopen next time.
					dbPromise = null;
					reject(e);
				}
			})
	);
}

function readAll() {
	return run("readonly", (store, out) => {
		const request = store.getAll();
		request.onsuccess = () => {
			out.value = request.result || [];
		};
	});
}

/** Blobs round-trip as Blobs; the ArrayBuffer fallback (see `saveDraft`) is rebuilt here. */
function withBlob(draft) {
	const image = draft && draft.image;
	if (!image) return { ...draft, image: null };
	if (typeof Blob !== "undefined" && image instanceof Blob) return draft;
	if (image.buffer && typeof Blob !== "undefined") {
		return { ...draft, image: new Blob([image.buffer], { type: image.type || "image/png" }) };
	}
	return { ...draft, image: null };
}

/** Structured clone refuses anything that is not plain data; the snapshot is meant to be. */
function plain(value) {
	if (value === undefined || value === null) return null;
	try {
		return JSON.parse(JSON.stringify(value));
	} catch (e) {
		return null;
	}
}

function add(record) {
	return run("readwrite", (store, out) => {
		const request = store.add(record);
		request.onsuccess = () => {
			out.value = request.result;
		};
	});
}

// ------------------------------------------------------------------ public API

/**
 * Save one report for later. `draft` is `{user, payload, snapshot, image, surface}`; `image`
 * is the flattened PNG Blob or null. Resolves with the new draft's id, rejects with an Error
 * whose message is fit to show.
 */
export async function saveDraft(draft) {
	const d = draft || {};
	const user = String(d.user || "");
	if (!user || user === "Guest") throw new Error("Sign in to save reports on this device.");

	const existing = await listDrafts(user);
	if (existing.length >= MAX_DRAFTS_PER_USER) {
		throw new Error("Too many reports are already saved on this device. Send those first.");
	}

	const record = {
		user,
		created_at: Date.now(),
		surface: String(d.surface || ""),
		payload: plain(d.payload) || {},
		snapshot: plain(d.snapshot),
		image: d.image || null,
		// The report's id (panel.js newClientId), so a resend of a report that did arrive is
		// recognized by the server rather than filed twice.
		client_id: typeof d.client_id === "string" ? d.client_id.slice(0, 64) : "",
	};
	try {
		return await add(record);
	} catch (e) {
		const image = record.image;
		if (!image || typeof image.arrayBuffer !== "function") throw e;
		// Some engines (older Safari, some private windows) refuse to store a Blob but accept
		// its bytes. `withBlob` rebuilds it on the way out.
		const buffer = await image.arrayBuffer();
		return add({ ...record, image: { buffer, type: image.type || "image/png" } });
	}
}

/** The caller's unexpired drafts, oldest first. Never throws: unavailable storage is "none". */
export async function listDrafts(user) {
	try {
		const all = await readAll();
		return partitionDrafts(all, user, Date.now()).keep.map(withBlob);
	} catch (e) {
		return [];
	}
}

export function deleteDraft(id) {
	return run("readwrite", (store) => {
		store.delete(id);
	});
}

/**
 * Delete every draft `user` may not send: other users' and expired ones. Resolves with the
 * number deleted (0 when storage is unavailable). Called whenever the panel opens and before
 * any draft is sent, so a user change on a shared device drops the previous person's drafts.
 */
export async function pruneForUser(user) {
	try {
		return await run("readwrite", (store, out) => {
			out.value = 0;
			const request = store.getAll();
			request.onsuccess = () => {
				const { drop } = partitionDrafts(request.result || [], user, Date.now());
				for (const draft of drop) {
					if (draft.id === undefined) continue;
					store.delete(draft.id);
					out.value += 1;
				}
			};
		});
	} catch (e) {
		return 0;
	}
}

/** Delete every draft on the device. For sign-out, where nobody is left to send them. */
export async function clearDrafts() {
	try {
		await run("readwrite", (store) => {
			store.clear();
		});
		return true;
	} catch (e) {
		return false;
	}
}
