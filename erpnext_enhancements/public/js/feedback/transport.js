/**
 * HTTP transport for the feedback SPA. Plain `fetch` against `/api/method/...`.
 *
 * **No `frappe.*` at all.** This is served from a website route, not from Desk, so the Desk
 * bundle is not on the page — a dependency on `frappe.call` works in a developer's Desk tab
 * and fails on the real page. Everything the transport needs comes off
 * `window.EE_FEEDBACK_BOOT`, which `www/feedback.py` put there.
 *
 * One `call()` for everything, so the CSRF header, the error shape and the unwrapping exist
 * once. Frappe answers a whitelisted method as `{"message": ...}`; unwrapping that in every
 * caller is how one caller forgets.
 *
 * Modelled on `public/js/chat/transport.js`, minus the pieces this page has no use for
 * (socket handshake facts, the Desk-bubble dual-source CSRF read).
 */

const BOOT = (typeof window !== "undefined" && window.EE_FEEDBACK_BOOT) || {};

/**
 * Endpoint names, written once. A typo in a dotted path is a 404 at runtime, and
 * `tests/test_feedback_endpoint_surface.py` asserts every name here resolves to a real
 * whitelisted function — a rename with no matching edit is a 404 the user sees.
 */
export const M = {
	BOOTSTRAP: "erpnext_enhancements.api.feedback.get_bootstrap",
	GET: "erpnext_enhancements.api.feedback.get_request",
	SUBMIT: "erpnext_enhancements.api.feedback.submit_request",
	DRAFT: "erpnext_enhancements.api.feedback.draft_description",
	DECIDE: "erpnext_enhancements.api.feedback.review_decision",
	RERUN: "erpnext_enhancements.api.feedback.rerun_breakdown",
	SAVE_PROPOSAL: "erpnext_enhancements.api.feedback.save_proposal",
	CREATE_TASKS: "erpnext_enhancements.api.feedback.create_tasks",
};

/** A refusal the SPA can recognise, so a 403 degrades into a sentence rather than a stack. */
export class FeedbackCallError extends Error {
	constructor(message, status, payload) {
		super(message);
		this.name = "FeedbackCallError";
		this.status = status;
		this.payload = payload;
		this.forbidden = status === 403;
		this.missing = status === 404;
		// `frappe.rate_limiter` is wired into the request lifecycle in `frappe/app.py`, so a
		// global limit applies whether or not an endpoint carries a decorator, and whatever
		// sits in front of the app can produce one too. Named so a caller can retry rather
		// than reporting "that request was refused" for a temporary condition.
		this.throttled = status === 429;
	}
}

function csrfToken() {
	return BOOT.csrf_token || "";
}

export async function call(method, args, options) {
	const opts = options || {};
	// A hung fetch otherwise leaves a button spinning forever with no way back. The training
	// player learned this the same way; 45s is comfortably above the slowest of these calls
	// (create_tasks, which inserts a dozen documents).
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
				"X-Frappe-CSRF-Token": csrfToken(),
			},
			body: JSON.stringify(args || {}),
			signal: controller ? controller.signal : undefined,
		});
	} catch (e) {
		throw new FeedbackCallError(
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

	if (!res.ok) {
		throw new FeedbackCallError(errorMessage(payload, res.status), res.status, payload);
	}
	return payload ? payload.message : null;
}

/**
 * Upload one file through Frappe's own endpoint, with **no doctype/docname**.
 *
 * That omission is the design, not an oversight. `upload_file` calls
 * `check_write_permission(doctype, docname)`, which returns immediately when `doctype` is
 * empty — so any signed-in user may create an unattached private File. Uploading straight
 * onto the request would need write permission on it, and a requester deliberately has only
 * read: write would let them move `status`, and `Submitted -> Approved` is a legal
 * transition. `api.feedback.submit_request` links the resulting File afterwards, checking
 * that the caller owns it.
 *
 * `is_private: 1` always. A public file lives under `sites/<site>/public/files/` and is
 * served with no authentication at all — anyone with the URL gets the bytes.
 */
export function upload(file, onProgress) {
	// XMLHttpRequest rather than fetch, for one reason: `fetch` still has no upload-progress
	// event, and a screenshot on a phone tether needs one.
	const xhr = new XMLHttpRequest();

	const promise = new Promise((resolve, reject) => {
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("is_private", "1");

		xhr.open("POST", "/api/method/upload_file", true);
		xhr.withCredentials = true;
		xhr.setRequestHeader("X-Frappe-CSRF-Token", csrfToken());
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
			if (xhr.status >= 200 && xhr.status < 300 && payload && payload.message) {
				resolve(payload.message);
			} else {
				reject(new FeedbackCallError(errorMessage(payload, xhr.status), xhr.status, payload));
			}
		};
		xhr.onerror = () => reject(new FeedbackCallError("Upload failed.", 0, null));
		xhr.onabort = () => reject(new FeedbackCallError("Upload cancelled.", 0, null));
		xhr.send(form);
	});

	return { promise, abort: () => xhr.abort() };
}

export function boot() {
	return BOOT;
}

/**
 * The sentence to show a human. Frappe puts the useful text in three different places
 * depending on how it threw, and `_server_messages` is a JSON string inside a JSON string.
 */
function errorMessage(payload, status) {
	if (payload) {
		const exc = payload._server_messages || payload.exception || payload.message;
		if (typeof exc === "string" && exc.trim()) {
			try {
				const parsed = JSON.parse(exc);
				const first = Array.isArray(parsed) ? parsed[0] : parsed;
				const inner = typeof first === "string" ? JSON.parse(first) : first;
				if (inner && inner.message) return String(inner.message).replace(/<[^>]*>/g, "").trim();
			} catch (e) {
				return exc.replace(/<[^>]*>/g, "").trim() || `Request failed (${status})`;
			}
		}
	}
	if (status === 403) return "You do not have access to that.";
	if (status === 404) return "That request no longer exists.";
	if (status === 429) return "Too many requests just now. Try again shortly.";
	if (status === 417 || status === 400) return "That request was refused.";
	return `Request failed (${status})`;
}
