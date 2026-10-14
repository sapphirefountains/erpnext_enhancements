/*
 * sf_kit: frappe.call as a promise that always settles, and settles one way.
 *
 *   sf_kit.call("erpnext_enhancements.api.x.get_y", { name }, { silent: true })
 *     .then((message) => ...)                 // r.message
 *     .catch(({ status, message }) => ...);   // status: HTTP status, 0 = never reached the server
 *
 * Why not frappe.call's own callbacks: v16's request.js calls `error` with a different argument
 * for nearly every status (417 passes the response body, 401/403/404/500/502/504 pass nothing,
 * a network failure passes the xhr), and for QueryTimeoutError / QueryDeadlockError it shows its
 * own dialog and calls nothing at all, so a page waiting on `error` waits forever. The jqXHR that
 * frappe.call returns is a thenable that always settles, with the real xhr on failure, so the
 * status and the body come from there. The callbacks are kept only as a fallback for a frappe.call
 * that returns no thenable (a stub, or a wrapper); in that case a status the callback cannot see is
 * reported as 0.
 *
 * The message is meant for a person: the server's own words when it sent some (a frappe.throw
 * arrives as _server_messages), with any HTML taken out, because callers put it through esc().
 * A traceback is never shown, and for a 5xx neither is the exception text (it names tables and
 * columns, not anything the reader can act on).
 *
 * `silent` is passed through: frappe then shows no msgprint of its own for the reply, so the page
 * can say it once, in its own error panel.
 */

const PASS_THROUGH = ["silent", "freeze", "freeze_message"];

function strip_html(text) {
	return String(text)
		.replace(/<[^>]*>/g, " ")
		.replace(/&nbsp;/g, " ")
		.replace(/\s+/g, " ")
		.trim();
}

function parse_json(value) {
	if (typeof value !== "string") return value;
	try {
		return JSON.parse(value);
	} catch (e) {
		return null;
	}
}

// "frappe.exceptions.ValidationError: Pick a date" -> "Pick a date"
function exception_text(line) {
	const text = String(line || "").trim();
	const match = /^[\w.]+(?:Error|Exception)\s*:\s*([\s\S]+)$/.exec(text);
	return match ? match[1].trim() : "";
}

function server_messages(raw) {
	const list = parse_json(raw);
	if (!Array.isArray(list)) return "";
	return list
		.map((item) => {
			const entry = parse_json(item);
			if (entry && typeof entry === "object") return entry.message || "";
			return typeof item === "string" ? item : "";
		})
		.map((text) => strip_html(text))
		.filter(Boolean)
		.join(" ");
}

// The readable part of a Frappe error body, or "" when there is none.
export function error_text(body, status) {
	body = parse_json(body);
	if (!body || typeof body !== "object") return "";
	const from_messages = server_messages(body._server_messages);
	if (from_messages) return from_messages;
	if (typeof body._error_message === "string" && body._error_message.trim()) {
		return strip_html(body._error_message);
	}
	if (typeof body.message === "string" && body.message.trim()) return strip_html(body.message);
	if (Array.isArray(body.errors) && body.errors[0] && typeof body.errors[0].message === "string") {
		return strip_html(body.errors[0].message);
	}
	if (status >= 500) return "";
	const from_exception = exception_text(String(body.exception || "").split("\n")[0]);
	if (from_exception) return strip_html(from_exception);
	const exc = parse_json(body.exc);
	const trace = Array.isArray(exc) ? exc.filter(Boolean).pop() : typeof exc === "string" ? exc : "";
	const last = String(trace || "")
		.trim()
		.split("\n")
		.pop();
	return strip_html(exception_text(last));
}

export function generic_message(env, status) {
	if (!status) return env.t("Could not reach the server.");
	if (status === 403) return env.t("You do not have permission to do that.");
	if (status >= 500) return env.t("The server could not finish this request. Please try again.");
	return env.t("The server refused this request.");
}

export function call(env, method, args, opts) {
	opts = opts || {};
	return new Promise((resolve, reject) => {
		let settled = false;
		const ok = (message) => {
			if (settled) return;
			settled = true;
			resolve(message);
		};
		const fail = (status, body) => {
			if (settled) return;
			settled = true;
			status = Number(status) || 0;
			reject({ status, message: error_text(body, status) || generic_message(env, status) });
		};

		const frappe = env.frappe;
		if (!frappe || typeof frappe.call !== "function") {
			fail(0, null);
			return;
		}
		const request = { method, args: args || {} };
		PASS_THROUGH.forEach((key) => {
			if (opts[key] !== undefined) request[key] = opts[key];
		});
		// Ignored once frappe.call has returned a thenable: v16 runs its own fail handler (and so
		// `error`) before ours, and for a 417 it passes the body without the status.
		let thenable = false;
		request.callback = (r) => {
			if (!thenable) ok(r ? r.message : undefined);
		};
		request.error = (r) => {
			if (thenable) return;
			fail(r && typeof r.status === "number" ? r.status : 0, r && r.responseText ? r.responseText : r);
		};

		let pending;
		try {
			pending = frappe.call(request);
		} catch (e) {
			fail(0, null);
			return;
		}
		if (pending && typeof pending.then === "function") {
			thenable = true;
			// Both handlers are attached in one then(), so a rejection is always handled here.
			try {
				pending.then(
					(data) => {
						const body = parse_json(data);
						ok(body && typeof body === "object" ? body.message : undefined);
					},
					(xhr) => {
						const status = xhr && typeof xhr.status === "number" ? xhr.status : 0;
						const body = xhr ? xhr.responseJSON || xhr.responseText : null;
						fail(status, body);
					}
				);
			} catch (e) {
				fail(0, null);
			}
		}
	});
}
