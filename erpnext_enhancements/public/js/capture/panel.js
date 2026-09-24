/**
 * The capture panel: "Report a problem", from the Desk, the kiosk or an allowlisted web page.
 *
 * Lazy-loaded by `window.ee_capture.open()` (recorder.js) through `capture_panel.bundle.js`,
 * which installs `window.ee_capture_panel.open(snapshot, opts)`. It shows the recorder's
 * snapshot **in full** before anything is sent — ADR 0016 §4 — takes a screenshot by paste or
 * upload, runs it through the annotator, and files the report with `submit_capture`.
 *
 * **Vanilla DOM, and no `frappe.*` beyond guarded reads.** The same file runs on the Desk, on
 * `/kiosk` (its own shell, no Desk bundle) and on web pages; a hard dependency on Desk
 * globals would work in a developer's Desk tab and fail on the kiosk. Nothing assigns
 * `innerHTML`: the snapshot holds console text and URLs from the page, and all of it is
 * rendered with `textContent`.
 *
 * **Never break the host page.** Everything the panel does happens after a click, inside
 * try/catch, and it cleans up after itself: its own <style>, one root element, and listeners
 * it removes on close. Keyboard events are stopped at the panel's root, because the Desk
 * binds shortcuts on `document` — Ctrl+S typed into the description would otherwise save the
 * form underneath.
 *
 * **Offline, a report becomes a draft, never a lost report.** On a kiosk in a basement the
 * network is the thing most likely to fail. A network failure (or `navigator.onLine` false)
 * saves the flattened screenshot, the fields and the snapshot to IndexedDB (drafts.js), keyed
 * to the user. They are offered for sending on the next open, when the browser comes back
 * online, and when this bundle loads (which the kiosk does on its own once it is idle), after
 * asking the server who is signed in, so one person's draft is never filed under another's
 * session on a shared tablet. Each report carries its own id, so a draft of a report whose
 * response was lost, but which did arrive, is not filed twice.
 *
 * **Back closes the panel, on the web and the kiosk.** On a phone the panel fills the screen,
 * so Back is how people expect to close it. Without an entry of its own, Back left the page
 * and took the typed report with it, without asking "Discard this report?". So the panel
 * pushes one history entry, `{ee_capture: <panel id>}` with no URL. A Back pops it and asks
 * the same question the Close button asks. When the panel closes any other way, it removes the
 * entry again. Pages with history of their own (the kiosk, Stock Scan) leave `popstate` alone
 * while `ee_capture.isOpen()` is true. This is not done on the Desk, where frappe's router
 * owns `popstate`, nor yet on /feedback, whose router re-renders on every `popstate` (see
 * `wantsHistoryEntry`). See `takeHistoryEntry`.
 *
 * A closed panel's entry can stay behind. After a Back has closed the panel, a Forward steps
 * onto the entry again. After a reload with the panel open, the page starts on it. The entry
 * never reopens the panel, and it has the page's own address. Pages with history of their own
 * re-stamp it as the screen they are showing. On a page without (/itinerary,
 * /travel_guidelines), leaving it costs one Back that changes nothing on screen. Re-stamping
 * would not save that Back: `replaceState` rewrites an entry but cannot remove it.
 *
 * The pure pieces (payload, validation, error wording, snapshot fitting) are exported and
 * tested in plain node by `scripts/test_capture_panel.js`, which also mounts the panel on a
 * fake DOM to drive its history entry. Nothing touches `document` at import time.
 */

import { call, upload, M } from "../feedback/transport.js";
import { openAnnotator, ANNOTATOR_CSS } from "./annotate.js";
import { saveDraft, listDrafts, deleteDraft, pruneForUser, clearDrafts } from "./drafts.js";

export const TITLE_MAX = 200;
/** `Enhancement Request.impact`, exactly as the Select and `api.feedback.VALID_IMPACTS` spell
 * it. The server refuses a report without one, so the panel asks; the middle option is
 * preselected, as the /feedback form does, and shown, so it is a choice and not a guess. */
export const IMPACTS = ["Blocking my work", "Painful but I can work around it", "Nice to have"];
export const DEFAULT_IMPACT = IMPACTS[1];
export const DESCRIPTION_MIN = 20;
/** The server refuses a context over 200 KB; leave room for the JSON envelope around it. */
export const SNAPSHOT_MAX_BYTES = 190 * 1024;

export const MSG_THROTTLED = "You've sent several reports just now. Try again in a minute.";
export const MSG_FORBIDDEN = "Only staff accounts can send reports.";
// Offered, not sent: nothing leaves the device without a tap, and after a reload the offer comes
// back only when this bundle loads again.
export const MSG_SAVED =
	"Saved on this device. When you're back online, open Report a problem again to send it.";
export const MSG_SESSION_SAVED =
	"Your session has ended. The report is saved on this device. Sign in again, then open Report a problem to send it.";
export const MSG_STALE_SAVED =
	"This page is out of date. The report is saved on this device. Reload the page, then open Report a problem to send it.";
export const MSG_PAUSED = "New requests are paused right now. Try again later.";
const MSG_SERVER = "The server had a problem. Try again in a minute.";

const STYLE_ID = "ee-cap-style";
const UPLOAD_TIMEOUT_MS = 90000;
const CALL_TIMEOUT_MS = 60000;
const WHOAMI_TIMEOUT_MS = 15000;
/** The `online` event fires when the interface comes up, a moment before requests succeed. */
const ONLINE_SETTLE_MS = 1500;
const SURFACES = ["desk", "web", "kiosk"];
/**
 * The key of the panel's own history entry, `{ee_capture: <panel id>}`. Pages with history of
 * their own read any state that carries it as "not one of my screens".
 */
export const HISTORY_KEY = "ee_capture";
/** The panel's own `history.back()` lands within milliseconds. After this long it stops waiting. */
export const BACK_SETTLE_MS = 1500;
/** Nobody reads and answers a confirm() this fast. A quicker `false` means it was never shown. */
const CONFIRM_UNSHOWN_MS = 50;

// ------------------------------------------------------------------ pure helpers

function str(value) {
	return value === undefined || value === null ? "" : String(value);
}

export function plural(n, noun) {
	return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** "Send 1 saved report" / "Send 3 saved reports". */
export function sendDraftsLabel(n) {
	return `Send ${plural(n, "saved report")}`;
}

export function normalizeSurface(value, fallback) {
	const v = str(value).toLowerCase();
	if (SURFACES.includes(v)) return v;
	return SURFACES.includes(fallback) ? fallback : "web";
}

/** The same two rules the server applies. The server stays the authority. */
export function validateFields(fields) {
	const f = fields || {};
	const errors = {};
	const title = str(f.title).trim();
	if (!title) errors.title = "Add a short title.";
	else if (title.length > TITLE_MAX) errors.title = `Keep the title under ${TITLE_MAX} characters.`;
	if (str(f.description).trim().length < DESCRIPTION_MIN) {
		errors.description = `Write at least ${DESCRIPTION_MIN} characters.`;
	}
	if (f.request_type !== "Bug" && f.request_type !== "Feature") errors.request_type = "Choose Bug or Feature.";
	if (!IMPACTS.includes(f.impact)) errors.impact = "Choose how much it affects your work.";
	return { ok: Object.keys(errors).length === 0, errors };
}

/** The live counter under the description. */
export function descriptionCounter(text) {
	const n = str(text).trim().length;
	if (n < DESCRIPTION_MIN) {
		const left = DESCRIPTION_MIN - n;
		return { ok: false, text: `${left} more character${left === 1 ? "" : "s"} needed` };
	}
	return { ok: true, text: plural(n, "character") };
}

/** The deployed app version: the Desk's boot, else what the web template passed. */
export function resolveAppVersion(win) {
	const w = win || {};
	try {
		const version = w.frappe && w.frappe.boot && w.frappe.boot.versions && w.frappe.boot.versions.erpnext_enhancements;
		if (version) return str(version).slice(0, 140);
	} catch (e) {
		// A half-booted Desk; fall through.
	}
	try {
		if (w.EE_CAPTURE && w.EE_CAPTURE.build) return str(w.EE_CAPTURE.build).slice(0, 140);
	} catch (e) {
		// Fall through.
	}
	return "";
}

/**
 * Who the panel thinks is signed in, for keying drafts. Only a hint: nothing is ever SENT
 * on the strength of it — `sendSavedDrafts` asks the server first.
 */
export function resolveUser(opts, win) {
	const w = win || {};
	const candidates = [
		() => opts && opts.user,
		() => w.ee_capture && w.ee_capture.config && w.ee_capture.config.user,
		() => w.EE_CAPTURE && w.EE_CAPTURE.user,
		() => w.frappe && w.frappe.session && w.frappe.session.user,
	];
	for (const read of candidates) {
		try {
			const user = read();
			if (user && user !== "Guest") return String(user);
		} catch (e) {
			// Try the next source.
		}
	}
	return "";
}

/**
 * The `context_*` fields of the payload, derived once when the panel opens and shown in the
 * technical details, so what is sent is exactly what was shown.
 *
 * The URL is the snapshot's `page.path` (which is `location.pathname`) plus, on the Desk
 * only, its `page.query`. Taken from the snapshot rather than re-read from `location` because
 * the snapshot is what the person saw and what the recorder already scrubbed; `location` is
 * the fallback only when there is no snapshot page at all. On web and kiosk pages the query
 * string is never used, whatever the snapshot says: token pages carry secrets there.
 */
export function contextFields(snapshot, env) {
	const e = env || {};
	const snap = snapshot && typeof snapshot === "object" ? snapshot : {};
	const page = snap.page && typeof snap.page === "object" ? snap.page : null;
	const surface = normalizeSurface(e.surface || snap.surface, "web");
	const loc = e.location || {};

	const path = str((page && page.path) || loc.pathname);
	let query = "";
	if (surface === "desk") {
		query = page ? str(page.query) : str(loc.search);
		if (query && query.charAt(0) !== "?") query = `?${query}`;
	}

	const form = page && page.form && typeof page.form === "object" ? page.form : null;
	const list = page && page.list && typeof page.list === "object" ? page.list : null;
	return {
		context_url: (path + query).slice(0, 500),
		context_doctype: str(form ? form.doctype : list ? list.doctype : "").slice(0, 140),
		// A new, unsaved document's name is a throwaway `new-todo-abc`; it identifies nothing.
		context_docname: form && !form.is_new ? str(form.name).slice(0, 140) : "",
		context_user_agent: str(e.userAgent).slice(0, 300),
		context_app_version: str(e.appVersion).slice(0, 140),
	};
}

/** The `payload` argument of `submit_capture`: the person's words plus the context fields. */
export function buildPayload(fields, context) {
	const f = fields || {};
	const c = context || {};
	const requestType = f.request_type === "Feature" ? "Feature" : "Bug";
	const payload = {
		title: str(f.title).trim().slice(0, TITLE_MAX),
		request_type: requestType,
		impact: IMPACTS.includes(f.impact) ? f.impact : DEFAULT_IMPACT,
		description: str(f.description).trim(),
		context_url: str(c.context_url),
		context_doctype: str(c.context_doctype),
		context_docname: str(c.context_docname),
		context_user_agent: str(c.context_user_agent),
		context_app_version: str(c.context_app_version),
	};
	const steps = requestType === "Bug" ? str(f.steps_to_reproduce).trim() : "";
	if (steps) payload.steps_to_reproduce = steps;
	return payload;
}

function byteLength(text) {
	try {
		if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text).length;
	} catch (e) {
		// Fall back to the worst case.
	}
	return text.length * 3;
}

/**
 * The string with every lone UTF-16 surrogate replaced by U+FFFD. A console message cut in
 * the middle of an emoji leaves half of it behind; JSON.stringify writes that as `\ud83d`,
 * and Frappe's orjson refuses the whole body with a 417. A loop rather than a lookbehind
 * regex, which older kiosk WebViews cannot even parse.
 */
export function wellFormed(text) {
	if (typeof text !== "string") return text;
	if (typeof text.toWellFormed === "function") return text.toWellFormed();
	let out = "";
	for (let i = 0; i < text.length; i++) {
		const c = text.charCodeAt(i);
		if (c >= 0xd800 && c <= 0xdbff) {
			const next = i + 1 < text.length ? text.charCodeAt(i + 1) : 0;
			if (next >= 0xdc00 && next <= 0xdfff) {
				out += text[i] + text[i + 1];
				i++;
			} else {
				out += "\ufffd";
			}
		} else if (c >= 0xdc00 && c <= 0xdfff) {
			out += "\ufffd";
		} else {
			out += text[i];
		}
	}
	return out;
}

/** `text` cut to `max` UTF-16 units without splitting a surrogate pair. */
export function cut(text, max) {
	let end = Math.max(0, max);
	if (end > 0 && end < text.length) {
		const last = text.charCodeAt(end - 1);
		if (last >= 0xd800 && last <= 0xdbff) end -= 1;
	}
	return text.slice(0, end);
}

function deepWellFormed(value) {
	if (typeof value === "string") return wellFormed(value);
	if (Array.isArray(value)) return value.map(deepWellFormed);
	if (value && typeof value === "object") {
		const out = {};
		for (const key of Object.keys(value)) out[wellFormed(key)] = deepWellFormed(value[key]);
		return out;
	}
	return value;
}

function clipStrings(value, max) {
	if (typeof value === "string") return value.length > max ? `${cut(value, max)}…` : value;
	if (Array.isArray(value)) return value.map((item) => clipStrings(item, max));
	if (value && typeof value === "object") {
		const out = {};
		for (const key of Object.keys(value)) out[key] = clipStrings(value[key], max);
		return out;
	}
	return value;
}

/**
 * A deep copy of the snapshot that fits under the server's limit, shortened in the least
 * useful places first. Applied BEFORE the details are shown, so the person reviews exactly
 * what will be sent — shortening after the preview would break "shown in full".
 *
 * Returns `{snapshot, trimmed}`; `snapshot` is null when the input was not a JSON object.
 */
export function fitSnapshot(snapshot, maxBytes) {
	const limit = maxBytes > 0 ? maxBytes : SNAPSHOT_MAX_BYTES;
	let snap;
	try {
		snap = JSON.parse(JSON.stringify(snapshot === undefined ? null : snapshot));
	} catch (e) {
		snap = null;
	}
	if (!snap || typeof snap !== "object" || Array.isArray(snap)) return { snapshot: null, trimmed: false };
	// Before measuring and before showing: the person reviews exactly the bytes that are sent.
	snap = deepWellFormed(snap);

	const fits = () => byteLength(JSON.stringify(snap)) <= limit;
	if (fits()) return { snapshot: snap, trimmed: false };

	const steps = [
		() => {
			snap = clipStrings(snap, 2000);
		},
		() => {
			for (const key of ["console", "requests"]) if (Array.isArray(snap[key])) snap[key] = snap[key].slice(-10);
			if (Array.isArray(snap.routes)) snap.routes = snap.routes.slice(-5);
		},
		() => {
			snap = clipStrings(snap, 300);
		},
		() => {
			if (snap.app && typeof snap.app === "object") snap.app = { omitted: "Too large to include." };
		},
		() => {
			snap.console = [];
			snap.requests = [];
			snap.routes = [];
		},
		() => {
			const page = snap.page && typeof snap.page === "object" ? snap.page : {};
			snap = {
				schema: snap.schema || 1,
				captured_at: snap.captured_at,
				surface: snap.surface,
				page: { path: clipStrings(page.path, 300), title: clipStrings(page.title, 300) },
			};
		},
	];
	for (const step of steps) {
		step();
		if (fits()) break;
	}
	snap.truncated = true;
	return { snapshot: snap, trimmed: true };
}

/** Used only when the recorder handed over nothing usable; says so in the details. */
export function minimalSnapshot(surface, env) {
	const e = env || {};
	return {
		schema: 1,
		captured_at: new Date().toISOString(),
		surface: normalizeSurface(surface, "web"),
		page: {
			path: str(e.pathname),
			query: null,
			route: null,
			title: str(e.title).slice(0, 300),
			form: null,
			list: null,
			report: null,
		},
		routes: [],
		console: [],
		requests: [],
		app: {},
		device: { online: e.online !== false, user_agent: str(e.userAgent).slice(0, 300) },
		note: "The page recorder was not available, so only the page address is included.",
	};
}

/** One line over the JSON, so the details are skimmable before they are read. */
export function summarizeSnapshot(snapshot) {
	const s = snapshot || {};
	const count = (list) => (Array.isArray(list) ? list.length : 0);
	return [
		plural(count(s.console), "console error"),
		plural(count(s.requests), "failed or slow request"),
		plural(count(s.routes), "recent page"),
	].join(" · ");
}

/**
 * What to do about a failed send. `kind` drives the flow (offline and session failures
 * become drafts); `message` is the sentence shown.
 */
export function classifyError(err) {
	const status = err && typeof err.status === "number" ? err.status : -1;
	const payload = (err && err.payload) || null;
	const excType = payload && typeof payload.exc_type === "string" ? payload.exc_type : "";
	if (status === 0) return { kind: "offline", message: MSG_SAVED };
	if (status === 401 || excType === "SessionExpired") return { kind: "session", message: MSG_SESSION_SAVED };
	// Both arrive as refusals (417 and 400) and neither is permanent: an admin lifts a pause, and
	// a reload mints a new token. A saved draft must survive them, so they get kinds of their own.
	if (excType === "FeedbackPausedError") return { kind: "paused", message: MSG_PAUSED };
	if (excType === "CSRFTokenError") return { kind: "stale", message: MSG_STALE_SAVED };
	if (status === 429) return { kind: "throttled", message: MSG_THROTTLED };
	if (status === 403) return { kind: "forbidden", message: MSG_FORBIDDEN };
	if (status === 413) return { kind: "refused", message: "The screenshot is too large. Crop it or use a smaller image." };
	if ([400, 409, 417, 422].includes(status)) {
		return { kind: "refused", message: str(err && err.message) || "The report was refused." };
	}
	if (status >= 500) return { kind: "server", message: MSG_SERVER };
	return { kind: "error", message: str(err && err.message) || "Something went wrong. Try again." };
}

/** The sentence after a batch of saved reports was tried. */
export function describeDraftOutcome(sent, refused, remaining, stopped) {
	const names = (sent || []).filter(Boolean);
	const parts = [];
	if (sent && sent.length) {
		const refs = names.length && names.length <= 3 ? ` (${names.join(", ")})` : "";
		parts.push(`Sent ${plural(sent.length, "saved report")}${refs}.`);
	}
	if (refused && refused.length) {
		const verb = refused.length === 1 ? "was" : "were";
		parts.push(`${plural(refused.length, "saved report")} could not be sent and ${verb} removed: ${refused[0]}`);
	}
	if (remaining > 0) {
		let why = "";
		if (stopped && stopped.kind === "throttled") why = MSG_THROTTLED;
		else if (stopped && stopped.kind === "offline") why = "The connection dropped.";
		else if (stopped && stopped.message) why = stopped.message;
		const left = `${plural(remaining, "report")} still saved on this device.`;
		parts.push(why ? `${why} ${left}` : left);
	}
	if (!parts.length) parts.push("There are no saved reports to send.");
	const clean = !(refused && refused.length) && !(remaining > 0);
	return { message: parts.join(" "), tone: clean ? "ok" : sent && sent.length ? "warn" : "bad" };
}

// ------------------------------------------------------------------ network

function withTimeout(promise, ms, onTimeout) {
	return new Promise((resolve, reject) => {
		const timer = setTimeout(() => {
			try {
				if (onTimeout) onTimeout();
			} catch (e) {
				// The abort itself failing leaves the original promise to settle on its own.
			}
		}, ms);
		promise.then(
			(value) => {
				clearTimeout(timer);
				resolve(value);
			},
			(err) => {
				clearTimeout(timer);
				reject(err);
			}
		);
	});
}

function asFile(blob, name) {
	try {
		return new File([blob], name, { type: blob.type || "image/png" });
	} catch (e) {
		// No File constructor (old WebViews): transport.upload only needs `.name` on it.
		try {
			blob.name = name;
		} catch (e2) {
			// A frozen Blob still uploads; the server names it.
		}
		return blob;
	}
}

function fileStamp() {
	return new Date().toISOString().replace(/[:.]/g, "-");
}

/**
 * The screenshot's upload name. The prefix is load-bearing: the daily retention job deletes a
 * private, unattached File with it once it is a day old (a filing that failed after the upload),
 * and nothing else in the app uses it.
 */
export const SHOT_PREFIX = "capture-shot-";

/** One id per report, kept with its draft, so the server can tell a resend from a new report. */
export function newClientId() {
	try {
		if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
	} catch (e) {
		// Not a secure context, or no crypto; fall through.
	}
	let id = "";
	for (let i = 0; i < 32; i++) id += Math.floor(Math.random() * 16).toString(16);
	return id;
}

/**
 * The snapshot as sent: the reviewed snapshot plus `sent_at`, the browser's clock at the moment
 * of sending. The server pairs it with its own arrival time to remove clock skew when it matches
 * Error Logs (product_feedback/capture_jobs.py); `captured_at` is when the panel opened, which
 * can be minutes or, for a draft, days earlier.
 */
export function contextToSend(snapshot, now) {
	const snap = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
	return { ...snap, sent_at: (now || new Date()).toISOString() };
}

/**
 * The server's idea of who is signed in — "" for Guest or an ended session. Throws only when
 * the server cannot be reached, which callers treat as "try later", never as "nobody".
 */
async function whoAmI() {
	const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
	const timer = controller ? setTimeout(() => controller.abort(), WHOAMI_TIMEOUT_MS) : null;
	try {
		const res = await fetch("/api/method/frappe.auth.get_logged_user", {
			method: "GET",
			credentials: "same-origin",
			headers: { Accept: "application/json" },
			cache: "no-store",
			signal: controller ? controller.signal : undefined,
		});
		if (res.status === 401 || res.status === 403) return "";
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		let body = null;
		try {
			body = await res.json();
		} catch (e) {
			body = null;
		}
		const user = body && typeof body.message === "string" ? body.message : "";
		return user === "Guest" ? "" : user;
	} finally {
		if (timer) clearTimeout(timer);
	}
}

/**
 * Upload the flattened screenshot (private, attached to nothing — see transport.upload) and
 * file the report. Used for a live report and for each saved draft alike, so both paths send
 * the same thing.
 */
async function sendReport({ payload, snapshot, image, clientId, onProgress }) {
	const method = M && M.CAPTURE;
	if (!method) {
		const err = new Error("Reporting is not available on this page.");
		err.status = -1;
		throw err;
	}
	const attachments = [];
	if (image) {
		const handle = upload(asFile(image, `${SHOT_PREFIX}${fileStamp()}.png`), onProgress);
		// transport.upload has no timeout of its own, and a stalled tether would otherwise
		// leave the panel "Uploading…" forever. The abort rejects with status 0, which the
		// caller treats as offline and saves a draft.
		const uploaded = await withTimeout(handle.promise, UPLOAD_TIMEOUT_MS, () => handle.abort());
		if (uploaded && uploaded.name) attachments.push(uploaded.name);
	}
	const args = { payload, context: contextToSend(snapshot), attachments };
	if (clientId) args.client_id = clientId;
	const result = await call(method, args, { timeout: CALL_TIMEOUT_MS });
	return { result: result || {}, uploaded: attachments.length };
}

// ------------------------------------------------------------------ saved drafts

let draftRun = null;
/**
 * Draft ids sent in this page's lifetime. If deleting a draft fails after it was filed, this
 * stops the same page filing it twice; a reload can still, which is the lesser evil next to
 * losing a report.
 */
const sentDraftIds = new Set();

/**
 * Send every saved draft that belongs to the user the SERVER says is signed in, deleting the
 * rest first. Resolves with `{sent, refused, remaining, message, tone}`; never rejects.
 */
export function sendSavedDrafts() {
	if (draftRun) return draftRun;
	draftRun = runDrafts()
		.catch(() => ({
			sent: [],
			refused: [],
			remaining: 0,
			tone: "bad",
			message: "Saved reports could not be sent. They are still on this device.",
		}))
		.finally(() => {
			draftRun = null;
		});
	return draftRun;
}

async function runDrafts() {
	let serverUser;
	try {
		serverUser = await whoAmI();
	} catch (e) {
		return {
			sent: [],
			refused: [],
			remaining: 0,
			tone: "bad",
			message: "Could not reach the server. Your saved reports are still on this device.",
		};
	}
	if (!serverUser) {
		return { sent: [], refused: [], remaining: 0, tone: "warn", message: "Sign in to send your saved reports." };
	}

	// Everyone else's drafts go before anything is sent: a different person is signed in now.
	await pruneForUser(serverUser);
	const drafts = (await listDrafts(serverUser)).filter((d) => !sentDraftIds.has(d.id));

	const sent = [];
	const refused = [];
	let stopped = null;
	for (const draft of drafts) {
		if (draft.user !== serverUser) {
			// listDrafts already filters by user; checked again because this is the line that
			// must never be crossed.
			await deleteDraft(draft.id).catch(() => {});
			continue;
		}
		try {
			const { result } = await sendReport({
				payload: draft.payload,
				snapshot: draft.snapshot,
				image: draft.image,
				clientId: draft.client_id,
			});
			sentDraftIds.add(draft.id);
			await deleteDraft(draft.id).catch(() => {});
			sent.push(result && result.name ? String(result.name) : "");
		} catch (err) {
			const outcome = classifyError(err);
			// A refusal will be a refusal every time. Keeping it would offer "Send 1 saved
			// report" for a week and fail each time, so it is dropped — and said so. A pause and
			// a stale token are not refusals (classifyError gives them their own kinds): they
			// stop the run and keep the draft, as a 429 does.
			if (outcome.kind === "refused" || outcome.kind === "forbidden") {
				await deleteDraft(draft.id).catch(() => {});
				refused.push(outcome.message);
				continue;
			}
			stopped = outcome;
			break;
		}
	}
	const remaining = drafts.length - sent.length - refused.length;
	return { sent, refused, remaining, ...describeDraftOutcome(sent, refused, remaining, stopped) };
}

/** For sign-out wiring: nobody is left to send them as. */
export function clearSavedDrafts() {
	return clearDrafts();
}

// ------------------------------------------------------------------ DOM helpers

function h(tag, className, text) {
	const node = document.createElement(tag);
	if (className) node.className = className;
	if (text !== undefined && text !== null && text !== "") node.textContent = String(text);
	return node;
}

function btn(label, className, onClick) {
	const node = h("button", className || "ee-cap-btn", label);
	node.type = "button";
	if (onClick) {
		node.addEventListener("click", (ev) => {
			try {
				onClick(ev);
			} catch (e) {
				// A handler bug must not escape into the page's error handlers as "the page broke".
				safeWarn(e);
			}
		});
	}
	return node;
}

function clear(node) {
	while (node && node.firstChild) node.removeChild(node.firstChild);
	return node;
}

function safeWarn(err) {
	try {
		// warn, not error: the recorder collects console.error, and the panel's own hiccup is
		// not evidence about the page being reported.
		console.warn("[capture panel]", err);
	} catch (e) {
		// No console.
	}
}

function prettyJson(value) {
	try {
		return JSON.stringify(value, null, 2);
	} catch (e) {
		return "(These details could not be displayed.)";
	}
}

function pasteKeys() {
	try {
		const platform = str(navigator.platform || navigator.userAgent);
		return /Mac|iPhone|iPad|iPod/.test(platform) ? "⌘V" : "Ctrl+V";
	} catch (e) {
		return "Ctrl+V";
	}
}

function imageFromTransfer(data) {
	if (!data) return null;
	try {
		for (const item of Array.from(data.items || [])) {
			if (item.kind === "file" && /^image\//i.test(item.type)) {
				const file = item.getAsFile();
				if (file) return file;
			}
		}
		for (const file of Array.from(data.files || [])) {
			if (/^image\//i.test(file.type)) return file;
		}
	} catch (e) {
		// Clipboard access varies by browser; no image is the safe answer.
	}
	return null;
}

function hasFiles(data) {
	try {
		return !!data && Array.from(data.types || []).includes("Files");
	} catch (e) {
		return false;
	}
}

const FOCUSABLE =
	'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(container) {
	return Array.from(container.querySelectorAll(FOCUSABLE)).filter((node) => {
		if (node.tabIndex < 0) return false;
		if (node.closest("[hidden]")) return false;
		// An unchecked radio is not a Tab stop; the checked one of the group is.
		if (node.type === "radio" && !node.checked) return false;
		return node.getClientRects().length > 0;
	});
}

function safeLocation() {
	try {
		return { pathname: window.location.pathname, search: window.location.search };
	} catch (e) {
		return { pathname: "", search: "" };
	}
}

function injectStyles() {
	if (document.getElementById(STYLE_ID)) return;
	const style = document.createElement("style");
	style.id = STYLE_ID;
	style.textContent = PANEL_CSS + ANNOTATOR_CSS;
	(document.head || document.documentElement).appendChild(style);
}

// ------------------------------------------------------------------ the panel

let active = null;
let sequence = 0;

class CapturePanel {
	constructor(snapshot, opts) {
		this.opts = opts || {};
		const configSurface = (() => {
			try {
				return window.ee_capture && window.ee_capture.config && window.ee_capture.config.surface;
			} catch (e) {
				return "";
			}
		})();
		const guessed = window.frappe && typeof window.frappe.get_route === "function" ? "desk" : "web";
		this.surface = normalizeSurface(
			this.opts.surface || (snapshot && snapshot.surface) || configSurface,
			guessed
		);
		this.kiosk = this.surface === "kiosk";
		this.user = resolveUser(this.opts, window);

		const loc = safeLocation();
		const fitted = fitSnapshot(snapshot);
		this.trimmed = fitted.trimmed;
		this.snapshot =
			fitted.snapshot ||
			minimalSnapshot(this.surface, {
				pathname: loc.pathname,
				title: document.title,
				online: navigator.onLine,
				userAgent: navigator.userAgent,
			});
		this.context = contextFields(this.snapshot, {
			surface: this.surface,
			location: loc,
			userAgent: (typeof navigator !== "undefined" && navigator.userAgent) || "",
			appVersion: resolveAppVersion(window),
		});

		this.id = ++sequence;
		// Reused by every try of this report, so a retry after a lost response is not a second one.
		this.clientId = newClientId();
		// The id in the panel's history entry. Random rather than `id`, which restarts at 1 on a
		// reload, while an entry pushed before the reload is still in the history.
		this.historyId = newClientId();
		this.historyWanted = wantsHistoryEntry(this.surface, window);
		// True while the panel's entry is in the history and has not been popped.
		this.historyArmed = false;
		this.annotator = null;
		this.busy = false;
		this.closed = false;
		this.cleanups = [];
		this.prevFocus = document.activeElement;
		this.result = { status: "canceled", name: null };
		this.closedPromise = new Promise((resolve) => {
			this.resolveClosed = resolve;
		});
		this.handle = {
			close: () => this.close(),
			closed: this.closedPromise,
			surface: this.surface,
		};
	}

	uid(suffix) {
		return `ee-cap-${this.id}-${suffix}`;
	}

	mount() {
		injectStyles();
		this.buildDom();
		document.body.appendChild(this.root);
		this.lockScroll();
		this.bindEvents();
		this.takeHistoryEntry();
		this.syncType();
		this.updateCounter();
		try {
			this.titleInput.focus({ preventScroll: true });
		} catch (e) {
			this.dialog.focus();
		}
		this.refreshDrafts();
	}

	// ---- building

	buildDom() {
		this.root = h("div", `ee-cap-root ee-cap-theme${this.kiosk ? " ee-cap-kiosk" : ""}`);
		this.root.dataset.surface = this.surface;

		const dialog = h("div", "ee-cap-dialog");
		dialog.setAttribute("role", "dialog");
		dialog.setAttribute("aria-modal", "true");
		dialog.setAttribute("aria-labelledby", this.uid("heading"));
		dialog.tabIndex = -1;
		this.dialog = dialog;

		const header = h("div", "ee-cap-header");
		const heading = h("h2", "ee-cap-heading", "Report a problem");
		heading.id = this.uid("heading");
		const closeButton = btn("×", "ee-cap-btn ee-cap-icon-btn", () => this.requestClose());
		closeButton.setAttribute("aria-label", "Close");
		header.append(heading, closeButton);

		this.body = h("div", "ee-cap-body");
		this.draftsBar = h("div", "ee-cap-drafts");
		this.draftsBar.hidden = true;
		this.alert = h("div", "ee-cap-alert");
		this.alert.setAttribute("role", "alert");
		this.alert.hidden = true;
		this.form = this.buildForm();
		this.body.append(this.draftsBar, this.alert, this.form);

		this.footer = h("div", "ee-cap-footer");
		this.progress = h("span", "ee-cap-progress");
		this.progress.setAttribute("role", "status");
		this.progress.setAttribute("aria-live", "polite");
		this.cancelButton = btn("Cancel", "ee-cap-btn", () => this.requestClose());
		this.sendButton = btn("Send report", "ee-cap-btn ee-cap-primary", () => this.submit());
		this.footer.append(this.progress, this.cancelButton, this.sendButton);

		dialog.append(header, this.body, this.footer);
		// No close on a backdrop click: a stray tap on a tablet would throw away a typed
		// report. Close, Cancel and Escape are all deliberate.
		this.root.append(dialog);
	}

	field(control, labelText) {
		this.fieldSeq = (this.fieldSeq || 0) + 1;
		const id = this.uid(`field-${this.fieldSeq}`);
		const wrap = h("div", "ee-cap-field");
		const label = h("label", "ee-cap-label", labelText);
		label.htmlFor = id;
		control.id = id;
		const error = h("p", "ee-cap-error");
		error.id = `${id}-error`;
		error.hidden = true;
		wrap.append(label, control, error);
		return { wrap, label, error };
	}

	buildForm() {
		const form = h("form", "ee-cap-form");
		form.noValidate = true;
		form.addEventListener("submit", (ev) => ev.preventDefault());

		// Type: two big radio segments rather than a <select>, so it is one tap on a kiosk.
		const typeSet = h("fieldset", "ee-cap-field ee-cap-type");
		const legend = h("legend", "ee-cap-label", "Type");
		const segments = h("div", "ee-cap-seg");
		this.typeInputs = {};
		for (const value of ["Bug", "Feature"]) {
			const option = h("label", "ee-cap-seg-option");
			const radio = document.createElement("input");
			radio.type = "radio";
			radio.name = this.uid("type");
			radio.value = value;
			radio.checked = value === "Bug";
			radio.addEventListener("change", () => this.syncType());
			option.append(radio, h("span", null, value));
			segments.append(option);
			this.typeInputs[value] = radio;
		}
		typeSet.append(legend, segments);

		// Impact: required by the server. Stacked rather than side by side, because the labels are
		// the Select's own sentences and three of them do not fit a phone's width in one row.
		const impactSet = h("fieldset", "ee-cap-field ee-cap-impact");
		const impactLegend = h("legend", "ee-cap-label", "How much does it affect your work?");
		const impactSegments = h("div", "ee-cap-seg ee-cap-seg-stack");
		this.impactInputs = {};
		for (const value of IMPACTS) {
			const option = h("label", "ee-cap-seg-option");
			const radio = document.createElement("input");
			radio.type = "radio";
			radio.name = this.uid("impact");
			radio.value = value;
			radio.checked = value === DEFAULT_IMPACT;
			option.append(radio, h("span", null, value));
			impactSegments.append(option);
			this.impactInputs[value] = radio;
		}
		impactSet.append(impactLegend, impactSegments);

		this.titleInput = document.createElement("input");
		this.titleInput.type = "text";
		this.titleInput.className = "ee-cap-input";
		this.titleInput.maxLength = TITLE_MAX;
		this.titleInput.required = true;
		this.titleInput.autocomplete = "off";
		this.titleInput.placeholder = "One line: what went wrong?";
		this.titleField = this.field(this.titleInput, "Title");

		this.descInput = document.createElement("textarea");
		this.descInput.className = "ee-cap-input";
		this.descInput.rows = 5;
		this.descInput.required = true;
		this.descField = this.field(this.descInput, "What happened?");
		this.counter = h("span", "ee-cap-counter");
		this.counter.id = `${this.descInput.id}-count`;
		this.descField.wrap.insertBefore(this.counter, this.descField.error);
		this.descInput.addEventListener("input", () => this.updateCounter());
		this.describe(this.descInput);

		this.stepsInput = document.createElement("textarea");
		this.stepsInput.className = "ee-cap-input";
		this.stepsInput.rows = 3;
		this.stepsInput.placeholder = "1. Open …\n2. Click …\n3. See …";
		this.stepsField = this.field(this.stepsInput, "Steps to reproduce (optional)");

		form.append(
			typeSet,
			impactSet,
			this.titleField.wrap,
			this.descField.wrap,
			this.stepsField.wrap,
			this.buildShot(),
			this.buildDetails()
		);
		return form;
	}

	buildShot() {
		const section = h("section", "ee-cap-section ee-cap-shot");
		const title = h("h3", "ee-cap-section-title", "Screenshot (optional)");
		title.id = this.uid("shot");
		section.setAttribute("aria-labelledby", title.id);

		// Visually hidden rather than display:none: some iOS versions ignore .click() on a
		// file input that is not rendered.
		this.fileInput = document.createElement("input");
		this.fileInput.type = "file";
		this.fileInput.accept = "image/*";
		this.fileInput.className = "ee-cap-file";
		this.fileInput.tabIndex = -1;
		this.fileInput.setAttribute("aria-hidden", "true");
		this.fileInput.addEventListener("change", () => {
			const file = this.fileInput.files && this.fileInput.files[0];
			// Cleared so choosing the same file again after "Remove" still fires `change`.
			this.fileInput.value = "";
			if (file) this.useImage(file);
		});

		this.shotEmpty = h("div", "ee-cap-shot-empty");
		const hint = h(
			"p",
			"ee-cap-hint",
			this.kiosk ? "Take a photo or upload an image." : `Paste a screenshot (${pasteKeys()}) or upload an image.`
		);
		this.shotEmpty.append(hint, btn("Upload image", "ee-cap-btn", () => this.pickImage()));

		this.shotEditor = h("div", "ee-cap-shot-editor");
		this.shotEditor.hidden = true;
		this.annotatorHost = h("div", "ee-cap-shot-host");
		const actions = h("div", "ee-cap-shot-actions");
		actions.append(
			btn("Retake", "ee-cap-btn", () => this.pickImage()),
			btn("Remove", "ee-cap-btn", () => this.removeImage())
		);
		const note = h("p", "ee-cap-hint", "Only the marked-up image is sent. Blurred areas are hidden for good.");
		this.shotEditor.append(this.annotatorHost, actions, note);

		section.append(title, this.shotEmpty, this.shotEditor, this.fileInput);

		section.addEventListener("dragover", (ev) => {
			if (!hasFiles(ev.dataTransfer)) return;
			ev.preventDefault();
			section.classList.add("ee-cap-drop");
		});
		section.addEventListener("dragleave", () => section.classList.remove("ee-cap-drop"));
		// The file itself is handled on the root (bindEvents), for a drop anywhere on the panel.
		section.addEventListener("drop", () => section.classList.remove("ee-cap-drop"));
		return section;
	}

	buildDetails() {
		const section = h("section", "ee-cap-section ee-cap-tech");
		const title = h("h3", "ee-cap-section-title", "Technical details that will be sent");
		title.id = this.uid("tech");
		section.setAttribute("aria-labelledby", title.id);
		section.append(
			title,
			h("p", "ee-cap-hint", "These stay in ERPNext. They are attached to your report and not sent anywhere else."),
			h("p", "ee-cap-tech-summary", summarizeSnapshot(this.snapshot))
		);
		if (this.trimmed) section.append(h("p", "ee-cap-hint", "Some details were shortened to fit."));

		const list = h("dl", "ee-cap-kv");
		const rows = [
			["Page", this.context.context_url],
			[
				"Record",
				[this.context.context_doctype, this.context.context_docname].filter(Boolean).join(" "),
			],
			["Version", this.context.context_app_version],
			["Browser", this.context.context_user_agent],
		];
		for (const [label, value] of rows) {
			if (!value) continue;
			list.append(h("dt", null, label), h("dd", null, value));
		}
		if (list.firstChild) section.append(list);

		const pre = h("pre", "ee-cap-json", prettyJson(this.snapshot));
		// Focusable so a keyboard user can scroll it.
		pre.tabIndex = 0;
		pre.setAttribute("aria-label", "Technical details, in full");
		section.append(pre);
		return section;
	}

	describe(control) {
		const ids = [];
		if (control === this.descInput && this.counter) ids.push(this.counter.id);
		const field = control === this.titleInput ? this.titleField : control === this.descInput ? this.descField : null;
		if (field && !field.error.hidden) ids.push(field.error.id);
		if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
		else control.removeAttribute("aria-describedby");
	}

	// ---- events

	bindEvents() {
		const root = this.root;
		const on = (target, type, fn, options) => {
			target.addEventListener(type, fn, options);
			this.cleanups.push(() => target.removeEventListener(type, fn, options));
		};

		// Registered before the propagation stops below, so it still runs.
		on(root, "keydown", (ev) => this.onKeydown(ev));
		// The Desk binds its shortcuts on `document`; none of them should act on the page
		// underneath while somebody types a report.
		for (const type of ["keydown", "keyup", "keypress", "paste"]) on(root, type, (ev) => ev.stopPropagation());
		// Bootstrap's modal (every frappe.ui.Dialog) pulls focus back into itself on any
		// `focusin` outside it. Opened over a Desk dialog, the panel's inputs would be
		// unusable without this.
		on(root, "focusin", (ev) => ev.stopPropagation());

		// A press on the dimmed backdrop would move focus to <body>, outside the panel, with no
		// focusin to pull it back; Escape and Ctrl+S would then reach the Desk underneath.
		on(root, "mousedown", (ev) => {
			if (ev.target === root) ev.preventDefault();
		});
		// And if focus gets out some other way, keys still do not reach the page: capture phase on
		// document runs before the Desk's window-level handler would see the event bubble.
		on(
			document,
			"keydown",
			(ev) => {
				if (this.closed || root.contains(ev.target)) return;
				ev.stopPropagation();
				if (ev.key === "Escape") {
					ev.preventDefault();
					this.requestClose();
					return;
				}
				// The browser's own Save Page, which the Desk would have prevented. Other keys keep
				// their defaults, so a paste still arrives (the window paste handler takes images).
				if ((ev.ctrlKey || ev.metaKey) && (ev.key === "s" || ev.key === "S")) ev.preventDefault();
				this.focus();
			},
			true
		);

		// A dropped file anywhere on the panel: an image becomes the screenshot, anything else is
		// refused here. Left alone, the browser would open the file in this tab and the report
		// would be gone without the discard question.
		on(root, "dragover", (ev) => {
			if (hasFiles(ev.dataTransfer)) ev.preventDefault();
		});
		on(root, "drop", (ev) => {
			if (!hasFiles(ev.dataTransfer)) return;
			ev.preventDefault();
			if (this.busy) return;
			const file = imageFromTransfer(ev.dataTransfer);
			if (file) this.useImage(file);
			else this.showAlert("That file is not an image.", "bad");
		});

		// Focus that escapes the panel (a script, a click on the page behind) comes back.
		on(document, "focusin", (ev) => {
			if (this.closed || root.contains(ev.target)) return;
			try {
				this.dialog.focus({ preventScroll: true });
			} catch (e) {
				// Nothing focusable; the trap is a courtesy.
			}
		});

		// Paste anywhere: window capture, so an image paste reaches the panel before any
		// page-level paste handler can treat it as an attachment for the page.
		on(
			window,
			"paste",
			(ev) => {
				if (this.closed || this.busy) return;
				const file = imageFromTransfer(ev.clipboardData);
				if (!file) return; // Text pastes into the fields as normal.
				ev.preventDefault();
				ev.stopPropagation();
				this.useImage(file);
				this.announce("Screenshot added.");
			},
			true
		);

		on(window, "online", () => this.refreshDrafts());
		on(window, "offline", () => this.refreshDrafts());

		// Back while the panel is open. Removed on close with the rest, so a Forward onto the
		// dead entry later reaches nothing here and cannot reopen the panel.
		if (this.historyWanted) on(window, "popstate", (ev) => this.onPopState(ev));
	}

	onKeydown(ev) {
		if (ev.key === "Escape") {
			ev.preventDefault();
			this.requestClose();
			return;
		}
		if (ev.key !== "Tab") return;
		const items = focusables(this.dialog);
		if (!items.length) {
			ev.preventDefault();
			this.dialog.focus();
			return;
		}
		const first = items[0];
		const last = items[items.length - 1];
		const current = document.activeElement;
		if (ev.shiftKey && (current === first || current === this.dialog || !this.dialog.contains(current))) {
			ev.preventDefault();
			last.focus();
		} else if (!ev.shiftKey && (current === last || !this.dialog.contains(current))) {
			ev.preventDefault();
			first.focus();
		}
	}

	// ---- form state

	syncType() {
		const feature = this.typeInputs.Feature.checked;
		this.descField.label.textContent = feature ? "What do you need?" : "What happened?";
		this.descInput.placeholder = feature
			? "What you are trying to do, and what would help."
			: "What you did, what you expected, and what happened instead.";
		this.stepsField.wrap.hidden = feature;
	}

	updateCounter() {
		const state = descriptionCounter(this.descInput.value);
		this.counter.textContent = state.text;
		this.counter.classList.toggle("ee-cap-counter-ok", state.ok);
		if (state.ok) this.setFieldError(this.descInput, "");
	}

	readFields() {
		const type = this.typeInputs.Feature.checked ? "Feature" : "Bug";
		const impact = IMPACTS.find((value) => this.impactInputs[value] && this.impactInputs[value].checked) || DEFAULT_IMPACT;
		return {
			request_type: type,
			impact,
			title: this.titleInput.value,
			description: this.descInput.value,
			steps_to_reproduce: type === "Bug" ? this.stepsInput.value : "",
		};
	}

	setFieldError(control, message) {
		const field = control === this.titleInput ? this.titleField : this.descField;
		field.error.textContent = message || "";
		field.error.hidden = !message;
		if (message) control.setAttribute("aria-invalid", "true");
		else control.removeAttribute("aria-invalid");
		this.describe(control);
	}

	hasContent() {
		return !!(
			this.titleInput.value.trim() ||
			this.descInput.value.trim() ||
			this.stepsInput.value.trim() ||
			this.annotator
		);
	}

	// ---- screenshot

	pickImage() {
		if (this.busy) return;
		try {
			this.fileInput.click();
		} catch (e) {
			this.showAlert("The file picker could not be opened.", "bad");
		}
	}

	annotatorMaxHeight() {
		const vh = window.innerHeight || 800;
		return Math.max(220, Math.round(vh * (this.kiosk ? 0.5 : 0.55)));
	}

	useImage(file) {
		if (this.closed || this.busy) return;
		if (!file || (file.type && !/^image\//i.test(file.type))) {
			this.showAlert("That file is not an image.", "bad");
			return;
		}
		this.destroyAnnotator();
		this.shotEmpty.hidden = true;
		this.shotEditor.hidden = false;
		let annotator;
		try {
			annotator = openAnnotator(this.annotatorHost, file, { maxHeight: () => this.annotatorMaxHeight() });
		} catch (e) {
			this.imageFailed(e);
			return;
		}
		this.annotator = annotator;
		annotator.ready.then(
			() => {
				if (this.annotator === annotator) this.clearAlert();
			},
			(err) => {
				if (this.annotator === annotator) this.imageFailed(err);
			}
		);
	}

	imageFailed(err) {
		this.destroyAnnotator();
		this.shotEditor.hidden = true;
		this.shotEmpty.hidden = false;
		this.showAlert((err && err.message) || "That image could not be opened. Try another one.", "bad");
	}

	removeImage() {
		if (this.busy) return;
		this.destroyAnnotator();
		this.shotEditor.hidden = true;
		this.shotEmpty.hidden = false;
	}

	destroyAnnotator() {
		const annotator = this.annotator;
		this.annotator = null;
		if (!annotator) return;
		try {
			annotator.destroy();
		} catch (e) {
			safeWarn(e);
		}
		clear(this.annotatorHost);
	}

	// ---- sending

	async submit() {
		if (this.busy || this.closed) return;
		try {
			await this.submitInner();
		} catch (e) {
			// Last line of defense: whatever went wrong, the panel stays usable.
			safeWarn(e);
			if (!this.closed) {
				this.setBusy(false);
				this.showAlert("Something went wrong. Try again.", "bad");
			}
		}
	}

	async submitInner() {
		const fields = this.readFields();
		const check = validateFields(fields);
		this.setFieldError(this.titleInput, check.errors.title || "");
		this.setFieldError(this.descInput, check.errors.description || "");
		if (!check.ok) {
			const first = check.errors.title ? this.titleInput : this.descInput;
			try {
				first.focus();
			} catch (e) {
				// Focus is a courtesy.
			}
			return;
		}

		this.clearAlert();
		const payload = buildPayload(fields, this.context);
		this.setBusy(true, "Preparing…");

		let image = null;
		if (this.annotator) {
			try {
				image = await this.annotator.toBlob();
			} catch (e) {
				if (this.closed) return;
				this.setBusy(false);
				this.showAlert("The screenshot could not be prepared. Remove it or try another image.", "bad");
				return;
			}
		}
		if (this.closed) return;

		if (typeof navigator !== "undefined" && navigator.onLine === false) {
			await this.saveOffline(payload, image, MSG_SAVED);
			return;
		}

		try {
			this.setBusy(true, image ? "Uploading screenshot…" : "Sending…");
			const { result, uploaded } = await sendReport({
				payload,
				snapshot: this.snapshot,
				image,
				clientId: this.clientId,
				onProgress: (fraction) => {
					if (this.closed) return;
					this.progress.textContent =
						fraction >= 1 ? "Sending…" : `Uploading screenshot… ${Math.round(fraction * 100)}%`;
				},
			});
			if (this.closed) return;
			this.showSent(result, uploaded);
		} catch (err) {
			if (this.closed) return;
			let outcome = classifyError(err);
			if (outcome.kind === "forbidden") {
				// A session that ended mid-form looks exactly like a non-staff account: the
				// server downgrades it to Guest and refuses. Ask before telling a colleague
				// they are not staff.
				const who = await whoAmI().catch(() => "?");
				if (!who) outcome = { kind: "session", message: MSG_SESSION_SAVED };
			}
			if (outcome.kind === "offline") {
				await this.saveOffline(payload, image, MSG_SAVED);
				return;
			}
			if (outcome.kind === "session" || outcome.kind === "stale") {
				await this.saveOffline(payload, image, outcome.message);
				return;
			}
			this.setBusy(false);
			this.showAlert(outcome.message, "bad");
		}
	}

	async saveOffline(payload, image, message) {
		try {
			await saveDraft({
				user: this.user,
				payload,
				snapshot: this.snapshot,
				image,
				surface: this.surface,
				client_id: this.clientId,
			});
		} catch (e) {
			if (this.closed) return;
			this.setBusy(false);
			this.showAlert(
				`The report could not be sent, and it could not be saved on this device either. ${
					(e && e.message) || ""
				}`.trim(),
				"bad"
			);
			return;
		}
		installOnlineListener();
		if (this.closed) return;
		this.result = { status: "saved", name: null };
		this.showDone(message, "warn", []);
	}

	showSent(result, uploaded) {
		const name = result && result.name ? String(result.name) : "";
		this.result = { status: "sent", name: name || null };
		const notes = [];
		if (Array.isArray(result.rejected) && result.rejected.length) {
			// Reported rather than swallowed, as the /feedback SPA does: a refused field is a
			// bug in this file, and a silent one is found weeks later.
			notes.push(`Some details were not saved: ${result.rejected.join("; ")}.`);
		}
		if (uploaded && Array.isArray(result.attachments) && result.attachments.length < uploaded) {
			notes.push("The screenshot could not be attached.");
		}
		this.showDone(name ? `Sent. Reference ${name}` : "Sent.", "ok", notes, name);
	}

	showDone(message, tone, notes, name) {
		this.destroyAnnotator();
		this.busy = false;
		clear(this.body);
		const done = h("div", `ee-cap-done ee-cap-tone-${tone}`);
		done.setAttribute("role", "status");
		done.append(h("p", "ee-cap-done-message", message));
		for (const note of notes || []) done.append(h("p", "ee-cap-hint", note));
		// Not on the kiosk: it runs installed, and a link out of it strands the tablet on a
		// page with no way back to the clock.
		if (name && this.surface !== "kiosk") {
			const link = h("a", "ee-cap-link", "Open in Feedback");
			link.href = `/feedback/request/${encodeURIComponent(name)}`;
			link.target = "_blank";
			link.rel = "noopener";
			done.append(link);
		}
		this.body.append(done);

		clear(this.footer);
		const doneButton = btn("Done", "ee-cap-btn ee-cap-primary", () => this.close());
		this.footer.append(this.progress, doneButton);
		this.progress.textContent = "";
		try {
			doneButton.focus({ preventScroll: true });
		} catch (e) {
			// Focus is a courtesy.
		}
	}

	setBusy(busy, label) {
		this.busy = busy;
		this.sendButton.disabled = busy;
		// The image was exported when sending began; a Remove, Retake or new mark now would not
		// change what is uploaded, so the form is frozen until the send settles. `inert` where
		// the browser has it; the image actions also check `busy` themselves.
		try {
			if (this.form) this.form.inert = busy;
		} catch (e) {
			// No `inert`; the guards in pickImage, useImage and removeImage still hold.
		}
		this.sendButton.textContent = busy ? "Sending…" : "Send report";
		this.progress.textContent = busy ? label || "" : "";
		this.dialog.setAttribute("aria-busy", busy ? "true" : "false");
	}

	// ---- saved drafts

	async refreshDrafts() {
		if (!this.user || this.closed) return;
		try {
			await pruneForUser(this.user);
			const drafts = await listDrafts(this.user);
			if (!this.closed) this.renderDrafts(drafts.length);
		} catch (e) {
			// No storage, no offer. The report itself still works.
		}
	}

	renderDrafts(n) {
		if (!this.draftsBar || !this.draftsBar.isConnected) return;
		clear(this.draftsBar);
		if (!n) {
			this.draftsBar.hidden = true;
			return;
		}
		const online = typeof navigator === "undefined" || navigator.onLine !== false;
		const saved = n === 1 ? "You have 1 report saved on this device." : `You have ${n} reports saved on this device.`;
		const later = n === 1 ? " You can send it when you're back online." : " You can send them when you're back online.";
		this.draftsBar.append(h("span", "ee-cap-drafts-text", online ? saved : saved + later));
		if (online) {
			const send = btn(sendDraftsLabel(n), "ee-cap-btn", async () => {
				send.disabled = true;
				send.textContent = "Sending…";
				const outcome = await sendSavedDrafts();
				if (this.closed) return;
				this.showAlert(outcome.message, outcome.tone);
				this.refreshDrafts();
			});
			this.draftsBar.append(send);
		}
		this.draftsBar.hidden = false;
	}

	// ---- messages

	showAlert(message, tone) {
		if (!this.alert || !this.alert.isConnected) return;
		this.alert.className = `ee-cap-alert ee-cap-tone-${tone || "bad"}`;
		this.alert.textContent = message;
		this.alert.hidden = false;
	}

	clearAlert() {
		if (!this.alert) return;
		this.alert.hidden = true;
		this.alert.textContent = "";
	}

	announce(message) {
		if (this.progress && !this.busy) this.progress.textContent = message;
	}

	// ---- open / close

	lockScroll() {
		const html = document.documentElement;
		const body = document.body;
		this.prevOverflow = [html.style.overflow, body ? body.style.overflow : ""];
		html.style.overflow = "hidden";
		if (body) body.style.overflow = "hidden";
	}

	unlockScroll() {
		const [html, body] = this.prevOverflow || ["", ""];
		document.documentElement.style.overflow = html;
		if (document.body) document.body.style.overflow = body;
	}

	/**
	 * Close, asking first when a typed report would be lost. Returns true when the panel closed
	 * and false when the person chose to keep it. Returns null when the browser refused to
	 * show the question: dialogs blocked for this page answer `false` at once.
	 */
	requestClose() {
		if (this.closed) return true;
		if (this.result.status === "canceled" && (this.busy || this.hasContent())) {
			let ok = true;
			const asked = Date.now();
			try {
				ok = window.confirm(this.busy ? "The report is still sending. Close anyway?" : "Discard this report?");
			} catch (e) {
				ok = true;
			}
			if (!ok) return Date.now() - asked < CONFIRM_UNSHOWN_MS ? null : false;
		}
		this.close();
		return true;
	}

	close() {
		if (this.closed) return;
		this.closed = true;
		this.destroyAnnotator();
		for (const fn of this.cleanups.splice(0)) {
			try {
				fn();
			} catch (e) {
				// Keep tearing down.
			}
		}
		this.unlockScroll();
		try {
			this.root.remove();
		} catch (e) {
			// Already gone.
		}
		if (active === this) active = null;
		const back = this.prevFocus;
		if (back && back.isConnected && typeof back.focus === "function") {
			try {
				back.focus({ preventScroll: true });
			} catch (e) {
				// The element that opened the panel may no longer accept focus.
			}
		}
		// `closed` waits until the panel's own Back has landed. A page that pushes a screen of
		// its own when the report closes then pushes it after that Back, not under it.
		const pending = this.releaseHistoryEntry();
		if (pending) pending.after.push(() => this.resolveClosed(this.result));
		else this.resolveClosed(this.result);
	}

	// ---- history: Back closes the panel

	/**
	 * Push the panel's own history entry, so that Back pops it instead of leaving the page. The
	 * call passes no URL, so the address never changes. iOS asks for the camera again when it
	 * does, and a web page's query string belongs to the page. If a previous panel's
	 * cleanup Back is still on its way, the push waits for it. Otherwise that Back would take
	 * this entry instead.
	 */
	takeHistoryEntry() {
		if (!this.historyWanted || this.historyArmed || this.closed) return;
		if (pendingBack) {
			pendingBack.after.push(() => this.takeHistoryEntry());
			return;
		}
		try {
			window.history.pushState({ [HISTORY_KEY]: this.historyId }, "");
			this.historyArmed = true;
		} catch (e) {
			// A sandboxed frame may refuse. The panel still works, but Back does not close it.
			safeWarn(e);
		}
	}

	/** Back, or any other traversal, while the panel is open. */
	onPopState(ev) {
		if (this.closed || !this.historyArmed || ev === cleanupEvent) return;
		const state = ev && ev.state;
		// Back onto the panel's own entry, after the page pushed one over it: still on top.
		if (state && state[HISTORY_KEY] === this.historyId) return;
		// The browser has already left the entry, so closing has nothing to remove.
		this.historyArmed = false;
		// Asked as the Close button asks. If the person stays, the entry goes back, so the next
		// Back asks again. A question the browser never showed does not re-arm it. Otherwise,
		// with dialogs blocked, Back could never leave the page.
		if (this.requestClose() === false) this.takeHistoryEntry();
	}

	/**
	 * Remove the panel's entry when it closes any way but Back: ×, Cancel, Escape, Done or
	 * `handle.close()`. Otherwise the next Back lands on the entry and appears to do nothing.
	 * Only while the entry is still the current one. If the page has moved on, a Back now would
	 * undo the page's own step. At most once per panel. Returns the wait for that Back, if any.
	 */
	releaseHistoryEntry() {
		if (!this.historyArmed) return null;
		this.historyArmed = false;
		let pending = null;
		try {
			const state = window.history.state;
			if (!state || state[HISTORY_KEY] !== this.historyId) return null;
			pending = awaitCleanupBack(this.historyId);
			window.history.back();
			return pending;
		} catch (e) {
			safeWarn(e);
			settleBack(pending);
			return null;
		}
	}

	focus() {
		try {
			this.dialog.focus({ preventScroll: true });
		} catch (e) {
			// Courtesy.
		}
	}
}

// ------------------------------------------------------------------ Back closes the panel

/**
 * Whether the panel takes a history entry of its own (see `takeHistoryEntry`).
 *
 * Not on the Desk. frappe's router re-routes on every `popstate`, so the panel's own Back would
 * re-render the form underneath. A Desk tab also has a mouse and a Close button.
 *
 * Not on /feedback yet. Its SPA router re-renders the current view on every `popstate`
 * (`feedback/app.js`, `mount`), so each close would clear a half-written request under the
 * panel. There, Back with the panel open still moves the page underneath, as it always has.
 * Remove this exception once that router ignores `popstate` while `ee_capture.isOpen()` is
 * true, and keeps its view on a state that carries `ee_capture`, as the kiosk and Stock Scan
 * do. Then flip the "/feedback" checks in `scripts/test_capture_panel.js`.
 *
 * Not where the template says `EE_CAPTURE.history: false`. /stock-scan and /kiosk set it from
 * their settings' "Turn Off Browser Back" box, the off switch in case an iPhone re-prompts for
 * the camera or location after a history entry, so one box turns off the page's entries and
 * this one together.
 */
export function wantsHistoryEntry(surface, win) {
	if (surface === "desk") return false;
	try {
		if (!win || !win.history || typeof win.history.pushState !== "function") return false;
		if (win.EE_CAPTURE && win.EE_CAPTURE.history === false) return false;
		return !win.EE_FEEDBACK_BOOT;
	} catch (e) {
		return false;
	}
}

/** The panel's own `history.back()` in flight: `{id, after, timer, listener, forwarded}`. */
let pendingBack = null;
/** The popstate that the panel's own Back caused. A panel opened meanwhile must not read it as a Back. */
let cleanupEvent = null;

/**
 * True while the panel is open. Also true until the `popstate` from its own closing
 * `history.back()` has been delivered. Pages with history of their own do nothing while this
 * is true. They therefore never read the panel's entry, going or coming, as a Back of their own.
 */
export function isPanelOpen() {
	return !!((active && !active.closed) || pendingBack);
}

/**
 * Wait for the `popstate` of the `history.back()` the caller is about to make. `after`
 * callbacks run once it has arrived, or after BACK_SETTLE_MS if it never does. They are the
 * panel's `closed`, and the entry of a panel opened in the meantime.
 */
function awaitCleanupBack(id) {
	settleBack(pendingBack);
	const pending = { id, after: [], timer: null, listener: null, forwarded: false };
	pending.listener = (ev) => {
		cleanupEvent = ev;
		const state = ev && ev.state;
		if (!pending.forwarded && state && state[HISTORY_KEY] === id) {
			// The page added an entry between back() and the traversal, so the Back landed on the
			// panel's dead entry instead of the page's. Step forward to where the page is, once.
			pending.forwarded = true;
			try {
				window.history.forward();
				return;
			} catch (e) {
				// Settle where it is.
			}
		}
		settleBack(pending);
	};
	pending.timer = setTimeout(() => settleBack(pending), BACK_SETTLE_MS);
	window.addEventListener("popstate", pending.listener);
	pendingBack = pending;
	return pending;
}

function settleBack(pending) {
	if (!pending || pendingBack !== pending) return;
	pendingBack = null;
	clearTimeout(pending.timer);
	try {
		window.removeEventListener("popstate", pending.listener);
	} catch (e) {
		// Gone with the page.
	}
	for (const fn of pending.after.splice(0)) {
		try {
			fn();
		} catch (e) {
			safeWarn(e);
		}
	}
}

// ------------------------------------------------------------------ back online

let onlineInstalled = false;
let toast = null;

/**
 * Installed on the first open and kept for the page's life: a kiosk that saved a draft in a
 * dead zone should offer to send it when the signal returns, whether or not the panel is
 * open. Only ever an offer — nothing is sent without a tap.
 */
function installOnlineListener() {
	if (onlineInstalled || typeof window === "undefined") return;
	onlineInstalled = true;
	window.addEventListener("online", () => {
		setTimeout(() => {
			backOnline().catch(safeWarn);
		}, ONLINE_SETTLE_MS);
	});
}

async function backOnline() {
	if (active && !active.closed) return; // the open panel refreshes its own offer
	const user = resolveUser({}, window);
	if (!user) return;
	// Someone else signed in on this device: their drafts go before anything is offered.
	await pruneForUser(user);
	if (active && !active.closed) return;
	const drafts = await listDrafts(user);
	if (drafts.length && !(active && !active.closed)) showDraftsToast(drafts.length);
}

/**
 * Called once when the panel bundle loads. The online listener lives only as long as the page,
 * and the kiosk reloads itself after an update, so a draft saved before a reload would
 * otherwise wait, unoffered, until somebody opened the panel again. The kiosk preloads this
 * bundle once it is idle, so there it is offered at load; on the Desk the bundle loads on the
 * first open, which shows the offer in the panel anyway.
 */
export function offerSavedDraftsOnLoad() {
	try {
		installOnlineListener();
		if (typeof navigator !== "undefined" && navigator.onLine === false) return;
		setTimeout(() => {
			backOnline().catch(safeWarn);
		}, ONLINE_SETTLE_MS);
	} catch (e) {
		safeWarn(e);
	}
}

function removeToast() {
	if (toast) {
		try {
			toast.remove();
		} catch (e) {
			// Already gone.
		}
	}
	toast = null;
}

function showDraftsToast(n) {
	injectStyles();
	removeToast();
	let kiosk = false;
	try {
		kiosk = normalizeSurface(window.ee_capture && window.ee_capture.config && window.ee_capture.config.surface) === "kiosk";
	} catch (e) {
		kiosk = false;
	}
	const node = h("div", `ee-cap-toast ee-cap-theme${kiosk ? " ee-cap-kiosk" : ""}`);
	node.setAttribute("role", "status");
	node.setAttribute("aria-live", "polite");
	const text = h(
		"span",
		"ee-cap-toast-text",
		n === 1 ? "You have 1 report saved on this device." : `You have ${n} reports saved on this device.`
	);
	const later = btn("Later", "ee-cap-btn", () => removeToast());
	const send = btn(sendDraftsLabel(n), "ee-cap-btn ee-cap-primary", async () => {
		send.disabled = true;
		later.disabled = true;
		text.textContent = "Sending…";
		const outcome = await sendSavedDrafts();
		if (toast !== node) return;
		text.textContent = outcome.message;
		send.remove();
		later.textContent = "Close";
		later.disabled = false;
		setTimeout(() => {
			if (toast === node) removeToast();
		}, 10000);
	});
	node.append(text, send, later);
	document.body.appendChild(node);
	toast = node;
}

// ------------------------------------------------------------------ entry point

/**
 * Open the panel over the current page. `snapshot` is `window.ee_capture.snapshot()`;
 * `opts.surface` is "desk", "web" or "kiosk" (kiosk: larger touch targets, no links out).
 *
 * Resolves once the panel is on screen with `{close(), closed, surface}`; `closed` resolves
 * with `{status: "sent" | "saved" | "canceled", name}` when it is dismissed. On the web and
 * the kiosk, that happens after the panel's history entry is gone. A second call while it is
 * open brings the open one forward rather than stacking another. `isPanelOpen()` reports
 * whether one is open.
 */
export function openPanel(snapshot, opts) {
	try {
		if (active && !active.closed) {
			active.focus();
			return Promise.resolve(active.handle);
		}
		removeToast();
		const panel = new CapturePanel(snapshot, opts);
		active = panel;
		try {
			panel.mount();
		} catch (e) {
			active = null;
			try {
				panel.close();
			} catch (e2) {
				// Partial mount; close removes what exists.
			}
			throw e;
		}
		installOnlineListener();
		return Promise.resolve(panel.handle);
	} catch (e) {
		safeWarn(e);
		return Promise.reject(e);
	}
}

// ------------------------------------------------------------------ styles

/**
 * One <style>, every rule under `ee-cap-`. Colors are custom properties on `.ee-cap-theme`,
 * redefined for dark under both signals the app uses: the Desk and the kiosk set
 * `<html data-theme="dark">`, and a page with no explicit choice follows the OS. The
 * `:not([data-theme="light"])` guard keeps an explicit light Desk light on a dark-mode OS.
 *
 * z-index 3000: above the Desk's `.modal` (1040), the Triton panel (1041) and the highest
 * overlay this app sets (2050), so the panel is never opened behind something.
 */
const DARK_VARS = `
	--ee-cap-bg: #1f2428;
	--ee-cap-fg: #e6edf3;
	--ee-cap-muted: #9198a1;
	--ee-cap-border: #3d444d;
	--ee-cap-subtle: #151b23;
	--ee-cap-accent: #4493f8;
	--ee-cap-accent-fg: #0d1117;
	--ee-cap-focus: #4493f8;
	--ee-cap-ring: rgba(68, 147, 248, 0.4);
	--ee-cap-danger: #f85149;
	--ee-cap-danger-bg: rgba(248, 81, 73, 0.15);
	--ee-cap-ok: #3fb950;
	--ee-cap-ok-bg: rgba(46, 160, 67, 0.15);
	--ee-cap-warn: #d29922;
	--ee-cap-warn-bg: rgba(187, 128, 9, 0.15);
	--ee-cap-overlay: rgba(1, 4, 9, 0.7);
	--ee-cap-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
	color-scheme: dark;
`;

export const PANEL_CSS = `
.ee-cap-theme {
	--ee-cap-bg: #ffffff;
	--ee-cap-fg: #1f2328;
	--ee-cap-muted: #59636e;
	--ee-cap-border: #d1d9e0;
	--ee-cap-subtle: #f6f8fa;
	--ee-cap-accent: #0969da;
	--ee-cap-accent-fg: #ffffff;
	--ee-cap-focus: #0969da;
	--ee-cap-ring: rgba(9, 105, 218, 0.3);
	--ee-cap-danger: #cf222e;
	--ee-cap-danger-bg: #ffebe9;
	--ee-cap-ok: #1a7f37;
	--ee-cap-ok-bg: #dafbe1;
	--ee-cap-warn: #9a6700;
	--ee-cap-warn-bg: #fff8c5;
	--ee-cap-overlay: rgba(15, 23, 42, 0.55);
	--ee-cap-shadow: 0 16px 48px rgba(0, 0, 0, 0.28);
	--ee-cap-tap: 44px;
	color-scheme: light;
}
@media (prefers-color-scheme: dark) {
	:root:not([data-theme="light"]) .ee-cap-theme {${DARK_VARS}}
}
:root[data-theme="dark"] .ee-cap-theme {${DARK_VARS}}
.ee-cap-kiosk { --ee-cap-tap: 56px; }

.ee-cap-root, .ee-cap-toast {
	font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
	color: var(--ee-cap-fg);
	text-align: left;
	letter-spacing: normal;
	text-transform: none;
}
.ee-cap-kiosk.ee-cap-root, .ee-cap-kiosk.ee-cap-toast { font-size: 16px; }
.ee-cap-root *, .ee-cap-root *::before, .ee-cap-root *::after,
.ee-cap-toast *, .ee-cap-toast *::before, .ee-cap-toast *::after { box-sizing: border-box; }
.ee-cap-root [hidden], .ee-cap-toast [hidden] { display: none !important; }

.ee-cap-root {
	position: fixed;
	inset: 0;
	z-index: 3000;
	display: flex;
	align-items: center;
	justify-content: center;
	padding: 16px;
	background: var(--ee-cap-overlay);
}
.ee-cap-dialog {
	display: flex;
	flex-direction: column;
	width: min(760px, 100%);
	max-height: calc(100vh - 32px);
	max-height: calc(100dvh - 32px);
	background: var(--ee-cap-bg);
	border: 1px solid var(--ee-cap-border);
	border-radius: 12px;
	box-shadow: var(--ee-cap-shadow);
	overflow: hidden;
	outline: none;
}
.ee-cap-header {
	display: flex;
	align-items: center;
	gap: 12px;
	padding: 8px 8px 8px 20px;
	border-bottom: 1px solid var(--ee-cap-border);
}
.ee-cap-heading { flex: 1; margin: 0; padding: 0; font-size: 18px; font-weight: 600; line-height: 1.3; color: inherit; }
.ee-cap-kiosk .ee-cap-heading { font-size: 20px; }
.ee-cap-body {
	flex: 1 1 auto;
	display: flex;
	flex-direction: column;
	gap: 16px;
	padding: 16px 20px 20px;
	overflow: auto;
	overscroll-behavior: contain;
}
.ee-cap-form { display: flex; flex-direction: column; gap: 16px; margin: 0; }
.ee-cap-footer {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: flex-end;
	gap: 8px;
	padding: 12px 20px;
	border-top: 1px solid var(--ee-cap-border);
	background: var(--ee-cap-bg);
}
.ee-cap-progress { margin-right: auto; font-size: 13px; color: var(--ee-cap-muted); }

.ee-cap-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; margin: 0; padding: 0; border: 0; }
.ee-cap-label {
	display: block;
	float: none;
	width: auto;
	margin: 0;
	padding: 0;
	font-size: 14px;
	font-weight: 600;
	line-height: 1.4;
	color: inherit;
}
.ee-cap-kiosk .ee-cap-label { font-size: 16px; }
.ee-cap-input {
	display: block;
	width: 100%;
	min-height: var(--ee-cap-tap);
	margin: 0;
	padding: 10px 12px;
	font: inherit;
	font-size: 16px;
	line-height: 1.4;
	color: var(--ee-cap-fg);
	background: var(--ee-cap-bg);
	border: 1px solid var(--ee-cap-border);
	border-radius: 8px;
	box-shadow: none;
}
textarea.ee-cap-input { min-height: 96px; resize: vertical; }
.ee-cap-input::placeholder { color: var(--ee-cap-muted); opacity: 1; }
.ee-cap-input:focus { outline: none; border-color: var(--ee-cap-focus); box-shadow: 0 0 0 3px var(--ee-cap-ring); }
.ee-cap-input[aria-invalid="true"] { border-color: var(--ee-cap-danger); }
.ee-cap-error { margin: 0; font-size: 13px; color: var(--ee-cap-danger); }
.ee-cap-counter { font-size: 13px; color: var(--ee-cap-muted); }
.ee-cap-counter-ok { color: var(--ee-cap-ok); }

.ee-cap-seg { display: inline-flex; align-self: flex-start; border: 1px solid var(--ee-cap-border); border-radius: 8px; overflow: hidden; }
.ee-cap-seg-option { position: relative; display: flex; margin: 0; padding: 0; cursor: pointer; font-weight: 500; }
.ee-cap-seg-option input { position: absolute; width: 1px; height: 1px; margin: 0; opacity: 0; pointer-events: none; }
.ee-cap-seg-option span {
	display: flex;
	align-items: center;
	justify-content: center;
	min-width: 104px;
	min-height: var(--ee-cap-tap);
	padding: 0 16px;
	background: var(--ee-cap-bg);
}
.ee-cap-seg-option + .ee-cap-seg-option span { border-left: 1px solid var(--ee-cap-border); }
.ee-cap-seg-stack { display: flex; flex-direction: column; align-self: stretch; }
.ee-cap-seg-stack .ee-cap-seg-option + .ee-cap-seg-option span { border-left: 0; border-top: 1px solid var(--ee-cap-border); }
.ee-cap-seg-option input:checked + span { background: var(--ee-cap-accent); color: var(--ee-cap-accent-fg); }
.ee-cap-seg-option input:focus-visible + span { outline: 3px solid var(--ee-cap-focus); outline-offset: -3px; }

.ee-cap-btn {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	gap: 6px;
	min-height: var(--ee-cap-tap);
	margin: 0;
	padding: 0 16px;
	font: inherit;
	font-weight: 500;
	line-height: 1.2;
	white-space: nowrap;
	text-decoration: none;
	color: var(--ee-cap-fg);
	background: var(--ee-cap-subtle);
	border: 1px solid var(--ee-cap-border);
	border-radius: 8px;
	box-shadow: none;
	cursor: pointer;
}
.ee-cap-btn:hover:not(:disabled) { border-color: var(--ee-cap-muted); }
.ee-cap-btn:focus-visible { outline: 3px solid var(--ee-cap-focus); outline-offset: 2px; }
.ee-cap-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.ee-cap-primary { color: var(--ee-cap-accent-fg); background: var(--ee-cap-accent); border-color: var(--ee-cap-accent); }
.ee-cap-primary:hover:not(:disabled) { border-color: var(--ee-cap-fg); }
.ee-cap-icon-btn { width: var(--ee-cap-tap); padding: 0; font-size: 24px; line-height: 1; background: transparent; border-color: transparent; }

.ee-cap-section { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--ee-cap-border); border-radius: 10px; }
.ee-cap-section-title { margin: 0; padding: 0; font-size: 15px; font-weight: 600; line-height: 1.3; color: inherit; }
.ee-cap-hint { margin: 0; font-size: 13px; color: var(--ee-cap-muted); }
.ee-cap-kiosk .ee-cap-hint { font-size: 15px; }
.ee-cap-shot-empty {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	gap: 12px;
	padding: 16px;
	border: 2px dashed var(--ee-cap-border);
	border-radius: 8px;
}
.ee-cap-shot.ee-cap-drop .ee-cap-shot-empty { border-color: var(--ee-cap-accent); }
.ee-cap-shot-editor { display: flex; flex-direction: column; gap: 8px; }
.ee-cap-shot-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.ee-cap-file { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); opacity: 0; pointer-events: none; }

.ee-cap-tech-summary { margin: 0; font-size: 13px; font-weight: 500; }
.ee-cap-kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: 13px; }
.ee-cap-kv dt { margin: 0; font-weight: 600; color: var(--ee-cap-muted); }
.ee-cap-kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.ee-cap-json {
	max-height: 280px;
	margin: 0;
	padding: 12px;
	overflow: auto;
	font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
	white-space: pre-wrap;
	overflow-wrap: anywhere;
	color: var(--ee-cap-fg);
	background: var(--ee-cap-subtle);
	border: 1px solid var(--ee-cap-border);
	border-radius: 8px;
}
.ee-cap-json:focus-visible { outline: 3px solid var(--ee-cap-focus); outline-offset: 2px; }

.ee-cap-alert, .ee-cap-drafts {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	gap: 8px 12px;
	padding: 10px 12px;
	border: 1px solid transparent;
	border-radius: 8px;
}
.ee-cap-drafts { background: var(--ee-cap-warn-bg); border-color: var(--ee-cap-warn); }
.ee-cap-drafts-text { flex: 1 1 220px; }
.ee-cap-tone-ok { background: var(--ee-cap-ok-bg); border-color: var(--ee-cap-ok); }
.ee-cap-tone-warn { background: var(--ee-cap-warn-bg); border-color: var(--ee-cap-warn); }
.ee-cap-tone-bad { background: var(--ee-cap-danger-bg); border-color: var(--ee-cap-danger); }
.ee-cap-done { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding: 20px; border: 1px solid transparent; border-radius: 10px; }
.ee-cap-done-message { margin: 0; font-size: 18px; font-weight: 600; }
.ee-cap-link { color: var(--ee-cap-accent); font-weight: 500; text-decoration: underline; }

.ee-cap-toast {
	position: fixed;
	right: 16px;
	bottom: 16px;
	z-index: 3000;
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: flex-end;
	gap: 8px 12px;
	max-width: min(440px, calc(100vw - 32px));
	padding: 12px 14px;
	background: var(--ee-cap-bg);
	border: 1px solid var(--ee-cap-border);
	border-radius: 10px;
	box-shadow: var(--ee-cap-shadow);
}
.ee-cap-toast-text { flex: 1 1 100%; }

@media (max-width: 640px) {
	.ee-cap-root { padding: 0; align-items: stretch; }
	.ee-cap-dialog { width: 100%; height: 100%; max-height: none; border: 0; border-radius: 0; }
	.ee-cap-body { padding: 16px; }
	.ee-cap-footer { padding: 12px 16px max(12px, env(safe-area-inset-bottom)); }
	.ee-cap-footer .ee-cap-btn { flex: 1 1 auto; }
	.ee-cap-toast { left: 16px; right: 16px; max-width: none; }
}
@media (prefers-reduced-motion: no-preference) {
	.ee-cap-dialog { animation: ee-cap-in 0.14s ease-out; }
	@keyframes ee-cap-in { from { opacity: 0; transform: translateY(8px); } }
}
`;
