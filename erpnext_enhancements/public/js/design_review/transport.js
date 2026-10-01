/**
 * HTTP transport for the Review Room (/review). Plain `fetch` against `/api/method/...`.
 *
 * No `frappe.*`: this is a website route and the Desk bundle is not on the page. Everything the
 * transport needs comes off `window.EE_REVIEW_BOOT`, which `www/review.py` put there. Modelled on
 * `public/js/feedback/transport.js`.
 */

const BOOT = (typeof window !== "undefined" && window.EE_REVIEW_BOOT) || {};

/** Endpoint names, written once. `tests/test_design_review_surface.py` checks each resolves. */
export const M = {
	LIST: "erpnext_enhancements.api.design_review.list_reviews",
	REVIEW: "erpnext_enhancements.api.design_review.get_review",
	CONTENT: "erpnext_enhancements.api.design_review.get_content",
	PEOPLE: "erpnext_enhancements.api.design_review.find_people",
	VOTE: "erpnext_enhancements.api.design_review.cast_vote",
	VERDICT: "erpnext_enhancements.api.design_review.cast_verdict",
	NOTE: "erpnext_enhancements.api.design_review.add_note",
	DELETE_NOTE: "erpnext_enhancements.api.design_review.delete_note",
	STATUS: "erpnext_enhancements.api.design_review.set_status",
	NOTE_STATUS: "erpnext_enhancements.api.design_review.set_note_status",
	DECIDE: "erpnext_enhancements.api.design_review.record_decision",
	PROMOTE: "erpnext_enhancements.api.design_review.promote_decision",
	CHECK: "erpnext_enhancements.api.design_review.check_bundle",
	IMPORT: "erpnext_enhancements.api.design_review.import_review",
};

export class CallError extends Error {
	constructor(message, status) {
		super(message);
		this.name = "CallError";
		this.status = status;
	}
}

function errorMessage(payload, status) {
	if (payload && payload._server_messages) {
		try {
			const list = JSON.parse(payload._server_messages).map((m) => {
				try {
					return JSON.parse(m).message;
				} catch (e) {
					return m;
				}
			});
			const text = list.join(" ").replace(/<[^>]+>/g, "").trim();
			if (text) return text;
		} catch (e) {
			/* fall through to the generic message */
		}
	}
	if (payload && payload.exception) return String(payload.exception).split(":").slice(1).join(":").trim() || "That was refused.";
	if (status === 403) return "You do not have access to that.";
	if (status === 404) return "That was not found.";
	return "Something went wrong. Try again.";
}

export async function call(method, args, timeout) {
	const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
	const timer = controller ? setTimeout(() => controller.abort(), timeout || 60000) : null;
	let res;
	try {
		res = await fetch(`/api/method/${method}`, {
			method: "POST",
			credentials: "same-origin",
			headers: { "Content-Type": "application/json", Accept: "application/json", "X-Frappe-CSRF-Token": BOOT.csrf_token || "" },
			body: JSON.stringify(args || {}),
			signal: controller ? controller.signal : undefined,
		});
	} catch (e) {
		throw new CallError(e && e.name === "AbortError" ? "That took too long. Try again." : "Could not reach the server.", 0);
	} finally {
		if (timer) clearTimeout(timer);
	}
	let payload = null;
	try {
		payload = await res.json();
	} catch (e) {
		payload = null;
	}
	if (!res.ok) throw new CallError(errorMessage(payload, res.status), res.status);
	return payload ? payload.message : null;
}

/** Upload a bundle through Frappe's own endpoint as a private, unattached File. */
export function upload(file) {
	return new Promise((resolve, reject) => {
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("is_private", "1");
		const xhr = new XMLHttpRequest();
		xhr.open("POST", "/api/method/upload_file", true);
		xhr.setRequestHeader("X-Frappe-CSRF-Token", BOOT.csrf_token || "");
		xhr.setRequestHeader("Accept", "application/json");
		xhr.onload = () => {
			let payload = null;
			try {
				payload = JSON.parse(xhr.responseText);
			} catch (e) {
				payload = null;
			}
			if (xhr.status >= 200 && xhr.status < 300 && payload && payload.message) resolve(payload.message);
			else reject(new CallError(errorMessage(payload, xhr.status), xhr.status));
		};
		xhr.onerror = () => reject(new CallError("Could not reach the server.", 0));
		xhr.send(form);
	});
}
