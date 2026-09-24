/**
 * HTTP transport for the Stock Scan page. Plain `fetch` against `/api/method/...`.
 *
 * **No Frappe client API at all.** This is a website route, not Desk: Desk's call helper would
 * work in a developer's Desk tab and fail on the phone. The CSRF token comes off
 * `window.EE_STOCK_SCAN_BOOT`, which `www/stock_scan.py` put there — read lazily inside
 * `call()`, never at import, so node can import this module to test `errorMessage`.
 *
 * One `call()` for everything, so the CSRF header, the timeout, the error shape and the
 * `{"message": …}` unwrapping exist once. Modelled on `public/js/feedback/transport.js`.
 */

/**
 * Endpoint names, written once — `erpnext_enhancements.api.stock_scan.*`, every one
 * whitelisted for POST only. A typo here is a 404 on a phone in a warehouse, so
 * `tests/test_stock_scan_surface.py` asserts the set equals the module's whitelisted names.
 */
export const M = {
	RESOLVE: "erpnext_enhancements.api.stock_scan.resolve",
	LOCATION: "erpnext_enhancements.api.stock_scan.get_location",
	ITEM: "erpnext_enhancements.api.stock_scan.get_item",
	SEARCH_ITEMS: "erpnext_enhancements.api.stock_scan.search_items",
	SEARCH_LOCATIONS: "erpnext_enhancements.api.stock_scan.search_locations",
	SEARCH_PROJECTS: "erpnext_enhancements.api.stock_scan.search_projects",
	TAKE: "erpnext_enhancements.api.stock_scan.take",
	ADD: "erpnext_enhancements.api.stock_scan.add",
	MOVE: "erpnext_enhancements.api.stock_scan.move_here",
	UNDO: "erpnext_enhancements.api.stock_scan.undo",
	RECENT: "erpnext_enhancements.api.stock_scan.get_recent",
	// "Bought on a store run" (v1.535.0): one line of a run, and the quick-item name check.
	STORE_RUN: "erpnext_enhancements.api.stock_scan.store_run",
	CHECK_NEW_ITEM: "erpnext_enhancements.api.stock_scan.check_new_item",
};
Object.freeze(M);

/** How long a receipt photo may take to go up on a warehouse's signal before we give up. */
export const UPLOAD_TIMEOUT_MS = 60000;

/** Long enough for a Purchase Receipt submit on a slow site; short enough that a dead signal says so. */
export const DEFAULT_TIMEOUT_MS = 30000;

/** What a signed-out phone is told, whichever way the server said it. */
export const SIGNED_OUT = "You were signed out. Reload the page to sign in again.";

/**
 * A failed call, carrying what the page needs to decide what to say and whether a retry
 * must reuse the save's `client_ref`.
 *
 * `retryable` means the request never got an answer (status 0: no connection, or our own
 * timeout). The save may still have happened, so the retry sends the same reference and the
 * server returns the first save instead of posting a second.
 *
 * `signedOut` means the session is gone, so no retry can work and the page offers Reload
 * (`www/stock_scan.py` sends a signed-out visitor to log in and back). `needsReload` adds a
 * stale CSRF token, which a reload fixes the same way.
 */
export class StockScanCallError extends Error {
	constructor(message, status, payload) {
		super(message);
		this.name = "StockScanCallError";
		this.status = status || 0;
		this.payload = payload || null;
		this.excType = (payload && payload.exc_type) || "";
		this.retryable = this.status === 0;
		this.signedOut = isSignedOut(payload, this.status);
		this.needsReload = this.signedOut || this.excType === "CSRFTokenError";
	}
}

function bootCsrf() {
	const boot = (typeof window !== "undefined" && window.EE_STOCK_SCAN_BOOT) || {};
	return boot.csrf_token || "";
}

/**
 * POST `args` to a whitelisted method and return its `message`.
 * Throws `StockScanCallError`; status 0 for no answer at all.
 */
export async function call(method, args, options) {
	const timeout = (options && options.timeout) || DEFAULT_TIMEOUT_MS;
	const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
	const timer = controller ? setTimeout(() => controller.abort(), timeout) : null;

	let res;
	try {
		res = await fetch(`/api/method/${method}`, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-Frappe-CSRF-Token": bootCsrf(),
			},
			body: JSON.stringify(args || {}),
			signal: controller ? controller.signal : undefined,
		});
	} catch (e) {
		if (timer) clearTimeout(timer);
		throw new StockScanCallError(
			e && e.name === "AbortError"
				? "That took too long. Check your signal and try again."
				: "Could not reach the server. Check your signal and try again.",
			0,
			null
		);
	}

	let payload = null;
	try {
		payload = await res.json();
	} catch (e) {
		// A proxy's HTML error page, or a body cut off by the timeout. The status still says what happened.
		payload = null;
	} finally {
		if (timer) clearTimeout(timer);
	}

	if (!res.ok) {
		// Frappe v16 never answers an expired session with a 401: the request runs as Guest and
		// every method here refuses Guest with a 403 (see `isSignedOut`). Said as a 401 from here
		// on, so nothing downstream has to know the disguise.
		if (isSignedOut(payload, res.status)) throw new StockScanCallError(SIGNED_OUT, 401, payload);
		throw new StockScanCallError(errorMessage(payload, res.status), res.status, payload);
	}
	if (!payload) {
		// A 2xx whose body never arrived whole (the timeout fired mid-read). The save may well
		// have happened, so this is status 0: the retry reuses the reference and gets it back.
		throw new StockScanCallError("The answer was cut off. Check your signal and try again.", 0, null);
	}
	return payload.message === undefined ? null : payload.message;
}

/**
 * Upload the receipt photo through Frappe's own `upload_file`, private, with **no doctype or
 * docname** — so `check_write_permission` has nothing to check and any signed-in user may
 * create an unattached File (the shape of `public/js/feedback/transport.js`'s `upload`).
 * `api.stock_scan.store_run` then accepts it only if this person uploaded it and nothing has
 * claimed it, and Frappe's own `attach_files_to_document` attaches it to the receipt.
 *
 * XMLHttpRequest, not fetch: fetch still has no upload progress, and a phone photo on a
 * warehouse's signal needs a bar. Resolves `{name, file_url}`; rejects `StockScanCallError`.
 *
 * Every page user is a System User, and for them `upload_file` has no MIME or doctype limit, so
 * a 403 here can only mean the session is gone (v16 answers a Guest upload with a bare
 * PermissionError): said as signed out. A stale CSRF token (400 `CSRFTokenError`) needs a
 * reload the same way. Status 0 is no answer, and a photo that got no answer is simply taken
 * again — nothing is posted until the line is saved.
 */
export function upload(file, onProgress) {
	return new Promise((resolve, reject) => {
		let xhr;
		let form;
		try {
			xhr = new XMLHttpRequest();
			form = new FormData();
			form.append("file", file, (file && file.name) || "receipt.jpg");
			form.append("is_private", "1");
			form.append("folder", "Home/Attachments");
			xhr.open("POST", "/api/method/upload_file", true);
			xhr.withCredentials = true;
			xhr.timeout = UPLOAD_TIMEOUT_MS;
			xhr.setRequestHeader("X-Frappe-CSRF-Token", bootCsrf());
			xhr.setRequestHeader("Accept", "application/json");
			if (xhr.upload && onProgress) {
				xhr.upload.onprogress = (ev) => {
					if (ev && ev.lengthComputable && ev.total) onProgress(ev.loaded / ev.total);
				};
			}
		} catch (e) {
			reject(new StockScanCallError("This phone could not send the photo. Reload the page and try again.", 0, null));
			return;
		}
		xhr.onload = () => {
			let payload = null;
			try {
				payload = JSON.parse(xhr.responseText);
			} catch (e) {
				payload = null;
			}
			const message = payload && payload.message;
			if (xhr.status >= 200 && xhr.status < 300 && message && message.file_url) {
				resolve({ name: message.name, file_url: message.file_url });
				return;
			}
			const exc = (payload && payload.exc_type) || "";
			if (xhr.status === 403 || xhr.status === 401 || isSignedOut(payload, xhr.status)) {
				reject(new StockScanCallError(SIGNED_OUT, 401, payload));
				return;
			}
			if (exc === "CSRFTokenError") {
				reject(new StockScanCallError("Your session changed. Reload the page.", xhr.status, payload));
				return;
			}
			reject(new StockScanCallError(errorMessage(payload, xhr.status || 0), xhr.status || 0, payload));
		};
		xhr.onerror = () => reject(new StockScanCallError("The photo did not go through. Check your signal and try again.", 0, null));
		xhr.ontimeout = () => reject(new StockScanCallError("The photo took too long to send. Check your signal and try again.", 0, null));
		xhr.onabort = () => reject(new StockScanCallError("The photo upload was stopped.", 0, null));
		xhr.send(form);
	});
}

// ---------------------------------------------------------------------------
// Error text (pure: node tests it)
// ---------------------------------------------------------------------------

function serverMessages(payload) {
	const raw = payload && payload._server_messages;
	if (!raw) return [];
	try {
		const list = typeof raw === "string" ? JSON.parse(raw) : raw;
		if (!Array.isArray(list)) return [];
		return list
			.map((entry) => {
				try {
					return typeof entry === "string" ? JSON.parse(entry) : entry;
				} catch (e) {
					return { message: entry };
				}
			})
			.filter((m) => m && typeof m === "object");
	} catch (e) {
		return [];
	}
}

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " };

function flatten(value) {
	if (Array.isArray(value)) return value.map(flatten).join(" ");
	return value === null || value === undefined ? "" : String(value);
}

/**
 * Message text as a plain sentence: lists flattened (`as_list` / `as_table` messages are
 * lists), tags dropped, the common entities decoded.
 */
function plainText(message) {
	return flatten(message)
		.replace(/<br\s*\/?>/gi, " ")
		.replace(/<[^>]*>/g, "")
		.replace(/&(amp|lt|gt|quot|#39|apos|nbsp);/g, (m, name) => ENTITIES[name])
		.replace(/\s+/g, " ")
		.trim();
}

/**
 * Whether a failed response means the session is gone.
 *
 * Frappe v16 does not say so with a 401. A request whose `sid` has no live session is run as
 * **Guest** (`sessions.get_session_record` falls back, clears the cookie and sets
 * `session_expired: 1` on that one response); the CSRF check is skipped for Guest; and every
 * `api.stock_scan` method refuses Guest in `frappe.is_whitelisted` with a 403
 * `PermissionError` whose message ends "Function … is not whitelisted." So: `session_expired`
 * on the first response, and that 403 on every one after it, when the cookie is already
 * gone. Every method this page calls IS whitelisted (`tests/test_stock_scan_surface.py`
 * holds the `M` map to that), so the sentence can only mean Guest. A 401 or `SessionExpired`
 * is kept for completeness.
 */
export function isSignedOut(payload, status) {
	if (payload && payload.session_expired && String(payload.session_expired) !== "0") return true;
	const exc = (payload && payload.exc_type) || "";
	if (status === 401 || exc === "SessionExpired") return true;
	if (status !== 403 || exc !== "PermissionError") return false;
	return serverMessages(payload).some((m) => /\bnot whitelisted\b/i.test(plainText(m.message)));
}

/**
 * The sentence to show for a failed response.
 *
 * Frappe returns `_server_messages`: a JSON string of a list of JSON strings, each
 * `{message, title, indicator, raise_exception}`. The thrown error is the LAST one with
 * `raise_exception` — a unique-key collision queues Frappe's own message ahead of ours, so the
 * first is the wrong one — else the last message. Tags are stripped: ERPNext's "Insufficient
 * Stock" message is full of links. With no message, the status and `exc_type` say what to do.
 *
 * A CSRF failure is the one exception, checked before any message: v16's
 * `validate_csrf_token` raises `frappe.throw(_("Invalid Request"), CSRFTokenError)`, so the
 * server's own sentence is always "Invalid Request", which tells a technician nothing. What
 * they need to know is to reload (their token went stale when the session was renewed).
 * A signed-out session is the other (`isSignedOut`): its server text is "Login to access …
 * Function … is not whitelisted", which reads like a bug in the page.
 */
export function errorMessage(payload, status) {
	const exc = (payload && payload.exc_type) || "";
	if (exc === "CSRFTokenError") return "Your session changed. Reload the page.";
	if (isSignedOut(payload, status)) return SIGNED_OUT;

	const messages = serverMessages(payload);
	let hit = null;
	for (let i = messages.length - 1; i >= 0 && !hit; i--) {
		if (messages[i].raise_exception) hit = messages[i];
	}
	for (let i = messages.length - 1; i >= 0 && !hit; i--) {
		if (plainText(messages[i].message)) hit = messages[i];
	}
	const text = hit ? plainText(hit.message) : "";
	if (text) return text;

	if (exc === "AuthenticationError") return SIGNED_OUT;
	if (status === 403 || exc === "PermissionError") {
		return "You do not have permission to do that. Ask a Stock Manager.";
	}
	if (status === 404) return "Not found. Reload the page and try again.";
	if (status === 409 || exc === "TimestampMismatchError") return "Someone else changed this just now. Try again.";
	if (status === 429) return "Too many requests just now. Wait a moment and try again.";
	if (status >= 500) return `The server had a problem (${status}). Try again in a moment.`;
	if (status === 417 || exc === "ValidationError") return "That was refused. Check it and try again.";
	return `Request failed (${status || "no answer"}). Try again.`;
}
