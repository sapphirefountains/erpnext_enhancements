/**
 * HTTP transport for `/marketing`. Plain `fetch` against `/api/method/...`, every call a POST.
 *
 * **No `frappe.*` at all.** This is a website route, not the Desk, so `frappe.call` is not on
 * the page. Everything the transport needs comes off `window.EE_MARKETING_BOOT`, which
 * `www/marketing.py` put there. One `call()` for everything, so the CSRF header, the error shape
 * and the `{"message": ...}` unwrapping exist once.
 *
 * The session cookie is what makes the approve button work at all: approval is refused to any
 * request authenticated by a token (`publish/workflow.signed_in_browser`), and this transport
 * sends none.
 *
 * Modelled on `public/js/feedback/transport.js`.
 */

const BOOT = (typeof window !== "undefined" && window.EE_MARKETING_BOOT) || {};
const SPA = "erpnext_enhancements.marketing.publish.spa";
const ACTIONS = "erpnext_enhancements.marketing.publish.approval";

/**
 * Endpoint names, written once. `tests/test_marketing_spa.py` asserts every name here resolves
 * to a real POST-only whitelisted function, and that every such function is either here or
 * listed as deliberately not dialled: a rename with no matching edit is a 404 the user sees.
 */
export const M = {
	BOOTSTRAP: `${SPA}.get_bootstrap`,
	CALENDAR: `${SPA}.get_calendar`,
	GET_POST: `${SPA}.get_post`,
	CHECK_POST: `${SPA}.check_post`,
	SAVE_POST: `${SPA}.save_post`,
	RESCHEDULE: `${SPA}.reschedule`,
	QUEUE: `${SPA}.approval_queue`,
	DELETE_POST: `${SPA}.delete_post`,
	MEDIA: `${SPA}.list_media`,
	CREATE_ASSET: `${SPA}.create_asset`,
	RESULTS: `${SPA}.get_results`,
	SUBMIT: `${ACTIONS}.submit_for_approval`,
	APPROVE: `${ACTIONS}.approve`,
	SEND_BACK: `${ACTIONS}.send_back`,
	CANCEL: `${ACTIONS}.cancel`,
};

/** A refusal the app can recognise, so a 403 becomes a sentence rather than a stack. */
export class MarketingCallError extends Error {
	constructor(message, status, payload) {
		super(message);
		this.name = "MarketingCallError";
		this.status = status;
		this.payload = payload;
		this.forbidden = status === 403;
		this.missing = status === 404;
		this.throttled = status === 429;
	}
}

export function boot() {
	return BOOT;
}

export async function call(method, args, options) {
	const opts = options || {};
	const timeout = opts.timeout || 45000;
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
				"X-Frappe-CSRF-Token": BOOT.csrf_token || "",
			},
			body: JSON.stringify(args || {}),
			signal: controller ? controller.signal : undefined,
		});
	} catch (e) {
		throw new MarketingCallError(
			e && e.name === "AbortError" ? "That took too long. Try again." : "Could not reach the server.",
			0,
			null
		);
	} finally {
		if (timer) clearTimeout(timer);
	}

	let payload = null;
	try {
		payload = await res.json();
	} catch (e) {
		payload = null;
	}
	if (!res.ok) throw new MarketingCallError(errorMessage(payload, res.status), res.status, payload);
	return payload ? payload.message : null;
}

/**
 * Upload one file through Frappe's own endpoint, with **no doctype or docname**, then hand the
 * File's name to `spa.create_asset`, which checks it is the caller's own unattached upload.
 * `upload_file` needs write permission on whatever it attaches to, and there is nothing to
 * attach to until the asset exists.
 *
 * `isPrivate` defaults to true. A public file is served to anyone who has its URL, and a photo
 * of a client's fountain that is not yet cleared for social is not something to publish by
 * accident. Facebook and Instagram fetch media from a URL, so they need a public file (or one
 * in Google Cloud Storage); the upload form says so and asks.
 */
export function upload(file, isPrivate, onProgress) {
	const xhr = new XMLHttpRequest();
	const promise = new Promise((resolve, reject) => {
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("is_private", isPrivate === false ? "0" : "1");
		xhr.open("POST", "/api/method/upload_file", true);
		xhr.withCredentials = true;
		xhr.setRequestHeader("X-Frappe-CSRF-Token", BOOT.csrf_token || "");
		xhr.upload.onprogress = (ev) => {
			if (onProgress && ev.lengthComputable) onProgress(ev.loaded / ev.total);
		};
		xhr.onload = () => {
			let payload = null;
			try {
				payload = JSON.parse(xhr.responseText);
			} catch (e) {
				payload = null;
			}
			if (xhr.status >= 200 && xhr.status < 300 && payload && payload.message) resolve(payload.message);
			else reject(new MarketingCallError(errorMessage(payload, xhr.status), xhr.status, payload));
		};
		xhr.onerror = () => reject(new MarketingCallError("Upload failed.", 0, null));
		xhr.onabort = () => reject(new MarketingCallError("Upload canceled.", 0, null));
		xhr.send(form);
	});
	return { promise, abort: () => xhr.abort() };
}

/**
 * The sentence to show a person. Frappe puts it in one of three places depending on how it
 * threw, and `_server_messages` is a JSON string inside a JSON string.
 */
function errorMessage(payload, status) {
	if (payload) {
		const raw = payload._server_messages || payload.exception || payload.message;
		if (typeof raw === "string" && raw.trim()) {
			try {
				const parsed = JSON.parse(raw);
				const first = Array.isArray(parsed) ? parsed[0] : parsed;
				const inner = typeof first === "string" ? JSON.parse(first) : first;
				if (inner && inner.message) return String(inner.message).replace(/<[^>]*>/g, "").trim();
			} catch (e) {
				return raw.replace(/<[^>]*>/g, "").trim() || `Request failed (${status})`;
			}
		}
	}
	if (status === 403) return "You do not have access to that.";
	if (status === 404) return "That no longer exists.";
	if (status === 429) return "Too many requests just now. Try again shortly.";
	if (status === 417 || status === 400) return "That was refused.";
	return `Request failed (${status})`;
}
