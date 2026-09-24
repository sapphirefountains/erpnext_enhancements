/**
 * The Stock Scan page's pure rules. **No DOM, no `window`, no `fetch` — at import or anywhere.**
 *
 * `scripts/test_stock_scan_client.mjs` imports this file straight into node, so everything a
 * test should pin lives here: what a scanned code means, how quantities read, when the stepper
 * stops, what the Save button says, and the save's idempotency key. `app.js` holds state and
 * draws; it asks this module every question with a right answer.
 *
 * `parseScan` is the one function with a twin, and the twin is the one that decides.
 * `inventory_enhancements/stock_scan_rules.parse_scan` reads every scan on the server: `app.js`
 * sends the raw text to `resolve` and never acts on its own reading of it. The page uses
 * `parseScan` for one thing only — naming what was scanned in the error it shows when the
 * server could not answer (`scanFailure`: "Couldn't open Bin B1-2-10 - SF: …"). The two are kept
 * identical by `tests/data/stock_scan_parse_vectors.json`, which both test suites read, so the
 * name in that sentence is the one the server would have looked up. Python's `urlsplit` /
 * `parse_qs` / `unquote_plus` / `str.strip` are reproduced here step for step rather than
 * approximated with `new URL()`, which disagrees with them on relative paths, `+`, bad `%`
 * escapes and whitespace.
 */

/** The page every warehouse label points at (`www/stock-scan.html`). */
export const SCAN_ROUTE = "/stock-scan";

/** Query keys a label may carry, in the order `parseScan` tries them (`stock_scan_rules.QUERY_KINDS`). */
export const QUERY_KINDS = [
	["w", "warehouse"],
	["item", "item"],
	["loc", "storage_location"],
];

/** The most one save may move (`stock_scan_rules.MAX_QTY`). A thumb on a key, not a business rule. */
export const MAX_QTY = 100000;

/** What the server accepts as a `client_ref` (`api/stock_scan.py::_CLIENT_REF`). */
export const CLIENT_REF_RE = /^[A-Za-z0-9._:-]{8,80}$/;

/** A job picked for a run is forgotten after a shift, so tomorrow's parts are not charged to today's job. */
export const JOB_TTL_MS = 12 * 60 * 60 * 1000;

/**
 * How long a save that got no answer keeps its `client_ref` for a retry. Tapping again inside
 * this window repeats THAT save (the server hands back the first one if it posted); after it,
 * the same numbers are a new save and get a new reference. The server keeps every reference
 * forever, so without a limit an identical save an hour later would be answered "already
 * saved" and never posted.
 */
export const RETRY_WINDOW_MS = 10 * 60 * 1000;

const EPSILON = 1e-9;

// ---------------------------------------------------------------------------
// Python's str.strip(), exactly
// ---------------------------------------------------------------------------

// Python's whitespace set, which is not JavaScript's: it includes the ASCII separators
// \x1c-\x1f (a GS1 barcode carries \x1d) and U+0085, and excludes U+FEFF. `trim()` would make
// the page and the server disagree about a code a scanner gun padded.
const PY_SPACE_LEAD = /^[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/;
const PY_SPACE_TRAIL = /[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/;

function pyStrip(text) {
	return String(text).replace(PY_SPACE_LEAD, "").replace(PY_SPACE_TRAIL, "");
}

// ---------------------------------------------------------------------------
// urllib.parse, the parts parse_scan uses (Python 3.12 semantics)
// ---------------------------------------------------------------------------

function decodeUtf8(bytes) {
	// `ignoreBOM: true` keeps a leading U+FEFF, as Python's "utf-8" codec does; the default
	// would silently drop it. Bad bytes become U+FFFD in both, like errors="replace".
	if (typeof TextDecoder !== "undefined") {
		return new TextDecoder("utf-8", { ignoreBOM: true }).decode(new Uint8Array(bytes));
	}
	let encoded = "";
	for (const b of bytes) encoded += "%" + (b < 16 ? "0" : "") + b.toString(16);
	try {
		return decodeURIComponent(encoded);
	} catch (e) {
		return String.fromCharCode.apply(null, bytes);
	}
}

/** `urllib.parse.unquote`: only ASCII runs are unescaped; a bad `%` stays literal. */
function unquote(text) {
	if (text.indexOf("%") === -1) return text;
	return text.replace(/[\x00-\x7f]+/g, (run) => {
		const bytes = [];
		for (let i = 0; i < run.length; i++) {
			const pair = run.substr(i + 1, 2);
			if (run[i] === "%" && /^[0-9A-Fa-f]{2}$/.test(pair)) {
				bytes.push(parseInt(pair, 16));
				i += 2;
			} else {
				bytes.push(run.charCodeAt(i));
			}
		}
		return decodeUtf8(bytes);
	});
}

function unquotePlus(text) {
	return unquote(text.replace(/\+/g, " "));
}

/** `parse_qs(query, keep_blank_values=False)`: a Map of name -> [values], blanks dropped. */
function parseQs(query) {
	const out = new Map();
	if (!query) return out;
	for (const field of query.split("&")) {
		const eq = field.indexOf("=");
		// No "=", or nothing after it: parse_qs drops the field, so `w=&item=X` is an item.
		if (!field || eq === -1 || eq === field.length - 1) continue;
		const name = unquotePlus(field.slice(0, eq));
		const value = unquotePlus(field.slice(eq + 1));
		if (!out.has(name)) out.set(name, []);
		out.get(name).push(value);
	}
	return out;
}

/** `urlsplit(text)` reduced to `{path, query}`, or `null` where Python would raise ValueError. */
function urlSplit(text) {
	let url = text.replace(/^[\x00-\x20]+/, "").replace(/[\t\r\n]/g, "");
	const colon = url.indexOf(":");
	if (colon > 0 && /^[A-Za-z]$/.test(url[0]) && /^[A-Za-z0-9+.-]+$/.test(url.slice(0, colon))) {
		url = url.slice(colon + 1);
	}
	if (url.slice(0, 2) === "//") {
		let end = url.length;
		for (const mark of "/?#") {
			const at = url.indexOf(mark, 2);
			if (at >= 0) end = Math.min(end, at);
		}
		const netloc = url.slice(2, end);
		url = url.slice(end);
		if (!netlocIsValid(netloc)) return null;
	}
	const hash = url.indexOf("#");
	if (hash !== -1) url = url.slice(0, hash);
	const mark = url.indexOf("?");
	return mark === -1 ? { path: url, query: "" } : { path: url.slice(0, mark), query: url.slice(mark + 1) };
}

function netlocIsValid(netloc) {
	const open = netloc.indexOf("[") !== -1;
	const close = netloc.indexOf("]") !== -1;
	if (open !== close) return false;
	if (open) {
		// A bracketed host must be an IP literal; Python checks it with `ipaddress`. Close enough
		// for a label, which never carries one.
		const host = netloc.slice(netloc.lastIndexOf("@") + 1);
		const inner = /^\[([^\]]*)\](:.*)?$/.exec(host);
		if (!inner || !(/^[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*$/.test(inner[1]) || /^v[0-9A-Fa-f]+\..+$/.test(inner[1]))) {
			return false;
		}
	}
	// `_checknetloc`: a non-ASCII host that NFKC-normalizes into URL punctuation is refused.
	if (/[^\x00-\x7f]/.test(netloc) && typeof netloc.normalize === "function") {
		const bare = netloc.replace(/[@:#?]/g, "");
		const folded = bare.normalize("NFKC");
		if (folded !== bare && /[/?#@:]/.test(folded)) return false;
	}
	return true;
}

// ---------------------------------------------------------------------------
// What was scanned
// ---------------------------------------------------------------------------

/**
 * Classify scanned or typed text as `{kind, value}` — `stock_scan_rules.parse_scan`, exactly.
 *
 * `kind` is `"warehouse"`, `"item"` or `"storage_location"` for one of this page's own URLs
 * (any host: a label printed on the test site must still work on production), `"text"` for
 * anything else — a bare code the server looks up — and `""` for blank input. A URL to some
 * other page is text, never followed: a swapped sticker must not become a redirect.
 */
export function parseScan(raw) {
	const text = pyStrip(raw === null || raw === undefined ? "" : raw);
	if (!text) return { kind: "", value: "" };
	const lowered = text.toLowerCase();
	if (lowered.indexOf("://") !== -1 || lowered.startsWith(SCAN_ROUTE)) {
		const parts = urlSplit(text);
		if (parts && parts.path.replace(/\/+$/, "").toLowerCase().endsWith(SCAN_ROUTE)) {
			const query = parseQs(parts.query);
			for (const [key, kind] of QUERY_KINDS) {
				const values = query.get(key);
				// Only the FIRST value of a key counts, blank or not — as in Python.
				if (values && values.length && pyStrip(values[0])) return { kind, value: pyStrip(values[0]) };
			}
		}
	}
	return { kind: "text", value: text };
}

/**
 * The sentence for a scan the server could not answer (no signal, a timeout, a crash):
 * "Couldn't open Bin B1-2-10 - SF: <reason>", or `Couldn't look up "<text>": <reason>` for
 * something that is not one of this page's labels. The only place the page reads a label
 * itself — whether the scan opens anything is always the server's call.
 */
export function scanFailure(raw, reason) {
	const why = String(reason === null || reason === undefined ? "" : reason).trim() || "Something went wrong. Try again.";
	const scan = parseScan(raw);
	if (!scan.value) return why;
	const name = scan.value.length > 60 ? `${scan.value.slice(0, 59)}…` : scan.value;
	return scan.kind === "text" ? `Couldn't look up "${name}": ${why}` : `Couldn't open ${name}: ${why}`;
}

// ---------------------------------------------------------------------------
// Quantities
// ---------------------------------------------------------------------------

function toNumber(value) {
	if (value === null || value === undefined || value === "" || typeof value === "boolean") {
		return value === true ? 1 : value === false ? 0 : null;
	}
	const n = typeof value === "number" ? value : Number(String(value).trim());
	return Number.isFinite(n) ? n : null;
}

/** Six decimal places at most, so 0.1 + 0.2 never prints as 0.30000000000000004. */
function round6(n) {
	return Math.round(n * 1e6) / 1e6 || 0;
}

/** A quantity for a sentence: `3` not `3.0`, `2.5` stays `2.5` (`stock_scan_rules.plain`). */
export function plain(value) {
	const n = toNumber(value);
	if (n === null) return value === null || value === undefined ? "" : String(value);
	if (Math.abs(n - Math.round(n)) <= EPSILON) return String(Math.round(n) || 0);
	return n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}

/**
 * Keep the stepper's change between "everything available here" and `max`.
 * The floor is `-max(available, 0)`: a location the system shows empty cannot be taken from.
 */
export function clampChange(value, available, max = MAX_QTY) {
	const v = toNumber(value) || 0;
	const floor = -Math.max(toNumber(available) || 0, 0);
	return round6(Math.min(Math.max(v, floor), toNumber(max) === null ? MAX_QTY : toNumber(max)));
}

/**
 * What a pending change means: `{verb, label, after}`. `label` is the Save button's text and
 * `after` the on-hand figure once it is saved (`null` when there is no change or no figure).
 *
 * `uom` is accepted so every caller hands over the same three facts; the label stays
 * unit-free because the Save button is narrow at 320 px and the unit is printed just above it.
 */
export function describeChange(change, uom, onHand) {
	const c = toNumber(change) || 0;
	if (Math.abs(c) <= EPSILON) return { verb: "none", label: "Save", after: null };
	const have = toNumber(onHand);
	const after = have === null ? null : round6(have + c);
	if (c < 0) return { verb: "take", label: `Take ${plain(-c)}`, after };
	return { verb: "add", label: `Add ${plain(c)}`, after };
}

/**
 * A quantity typed into the "How many?" sheet: `{qty, problem}`, `qty` always positive.
 * Commas are read as thousands separators (the site is American English).
 */
export function parseQty(text, wholeNumber, uom) {
	const cleaned = String(text === null || text === undefined ? "" : text).replace(/[\s,]/g, "");
	if (!cleaned) return { qty: null, problem: "Enter a number." };
	if (!/^\d*\.?\d+$|^\d+\.$/.test(cleaned)) return { qty: null, problem: "Numbers only, like 3 or 2.5." };
	const qty = Number(cleaned);
	if (!(qty > EPSILON)) return { qty: null, problem: "Enter more than zero." };
	if (qty > MAX_QTY) return { qty: null, problem: `${plain(qty)} is more than one save can move.` };
	if (wholeNumber && Math.abs(qty - Math.round(qty)) > EPSILON) {
		return { qty: null, problem: `${uom || "This unit"} is counted in whole numbers.` };
	}
	return { qty: round6(qty), problem: null };
}

// ---------------------------------------------------------------------------
// Saving: idempotency
// ---------------------------------------------------------------------------

/**
 * A fresh `client_ref`: `ss-<time>-<random>`. Not `crypto.randomUUID()` — older Android
 * handsets in the field do not have it (the kiosk learned this first).
 */
export function mintRef(now, random) {
	const rand = typeof random === "function" ? random : Math.random;
	const time = Number.isFinite(now) ? now : Date.now();
	let tail = "";
	for (let i = 0; i < 6 && tail.length < 10; i++) tail += rand().toString(36).slice(2);
	tail = (tail + "0000000000").slice(0, 10);
	return `ss-${Math.floor(time).toString(36)}-${tail}`;
}

/**
 * Whether a failed save may have reached the server, so the retry must carry the SAME
 * `client_ref` (the server then returns the first save instead of posting a second).
 *
 * Status 0 is a dropped connection or our own timeout. 502/503/504 come from the proxy in
 * front of Frappe, which may have given up on a request the app went on to commit — so they
 * count too. 409 is `api/stock_scan.py` saying this very reference is still being posted by an
 * earlier request (a retry that arrived while the first attempt was still submitting): that
 * first attempt is about to commit, so a fresh reference on the next tap would post it twice.
 * Any other refusal (4xx) or crash (500) rolled back: nothing was saved.
 */
export function mayHaveSaved(status) {
	return status === 0 || status === 409 || status === 502 || status === 503 || status === 504;
}

/**
 * The identity of one intended save. Same key → same `client_ref`; any change to what would
 * be posted (quantity, item, place, order line, job) is a different save and gets a new one.
 */
export function saveKey(parts) {
	const p = parts || {};
	return JSON.stringify([
		p.action || "",
		p.item_code || "",
		p.warehouse || "",
		p.from_warehouse || "",
		p.purchase_order_item || "",
		p.without_po ? 1 : 0,
		p.project || "",
		plain(p.qty),
	]);
}

/**
 * The reference a retry may send again, or `null` to mint a fresh one. `entry` is what the
 * page kept after a save got no answer: `{ref, at}` (`at` = when that attempt failed). Only
 * inside `RETRY_WINDOW_MS` is the next identical save the same save; a stamp from the future
 * (the phone's clock was changed) is not trusted either.
 */
export function keptRef(entry, nowMs) {
	if (!entry || typeof entry.ref !== "string" || !CLIENT_REF_RE.test(entry.ref)) return null;
	const at = toNumber(entry.at);
	if (at === null || at <= 0) return null;
	const now = toNumber(nowMs) === null ? Date.now() : toNumber(nowMs);
	const age = now - at;
	return age <= RETRY_WINDOW_MS && age >= -5 * 60 * 1000 ? entry.ref : null;
}

/** What a save was, for a message raised after the person has moved on: "Take 3 Each of Widget". */
export function saveLabel(action, qty, uom, itemName) {
	const verb = action === "take" ? "Take" : action === "move" ? "Move" : "Add";
	const amount = `${plain(qty)} ${uom || ""}`.trim();
	return `${verb} ${amount} of ${itemName || "this item"}`;
}

// ---------------------------------------------------------------------------
// Images, jobs, words
// ---------------------------------------------------------------------------

/**
 * Whether an Item's `image` may go in an `<img src>`: a same-origin path (`/files/…`,
 * `/private/files/…`) or an https URL. Never `data:`, `javascript:`, plain http (mixed
 * content), or a protocol-relative `//host` — and never a string with a tab, newline or
 * backslash, which URL parsers strip or read as `/`, turning `/\host` into `//host`.
 */
export function isSafeImage(src) {
	if (typeof src !== "string" || !src) return false;
	if (/[\x00-\x1f\x7f\\]/.test(src)) return false;
	if (/^https:\/\/./i.test(src)) return true;
	return src[0] === "/" && src[1] !== "/";
}

/** Whether a job picked at `savedAtMs` has lapsed: older than 12 h, or stamped in the future. */
export function jobExpired(savedAtMs, nowMs) {
	const saved = toNumber(savedAtMs);
	if (saved === null || saved <= 0) return true;
	const now = toNumber(nowMs) === null ? Date.now() : toNumber(nowMs);
	return now - saved > JOB_TTL_MS || saved - now > 5 * 60 * 1000;
}

/**
 * The job remembered on this phone, if it is still this person's: stored by `user`, not
 * lapsed. A phone handed to the next person on a shift must not charge their takes to the
 * job somebody else picked (the stored job carries who picked it; one without is nobody's).
 */
export function rememberedJob(stored, user, nowMs) {
	if (!stored || typeof stored !== "object" || !stored.project) return null;
	if (!user || stored.user !== user) return null;
	return jobExpired(stored.saved_at, nowMs) ? null : stored;
}

/** Up to two letters for an item with no picture: "Ball Valve 2in" → "BV". */
export function initials(name) {
	const words = String(name || "").match(/[A-Za-z0-9]+/g) || [];
	const letters = words.slice(0, 2).map((w) => w[0].toUpperCase()).join("");
	return letters || "?";
}

/**
 * "2:14 PM" for a save made today, "Sep 21" before that. `posted_at` is the site's wall-clock
 * time as text; it is read as text, never through `Date`, so a phone in another time zone
 * cannot shift it.
 */
export function formatWhen(postedAt, today) {
	const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(postedAt || ""));
	if (!m) return "";
	const [, y, mo, d, hh, mm] = m;
	if (today && String(today).slice(0, 10) === `${y}-${mo}-${d}`) {
		const h = Number(hh);
		return `${h % 12 || 12}:${mm} ${h < 12 ? "AM" : "PM"}`;
	}
	const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	return `${months[Number(mo) - 1] || mo} ${Number(d)}`;
}

/** "Sep 30" for a `YYYY-MM-DD` date, or "". */
export function shortDate(value) {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
	if (!m) return "";
	const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	return `${months[Number(m[2]) - 1] || m[2]} ${Number(m[3])}`;
}

/** What a saved row did, in a few words: "Took 3 Unit". */
export function logHeadline(log) {
	const amount = `${plain(log && log.qty)} ${(log && log.stock_uom) || ""}`.trim();
	switch (log && log.action) {
		case "Take":
			return `Took ${amount}`;
		case "Receive":
			return `Received ${amount}`;
		case "Add Without PO":
			// With a job it was parts coming back from that job (`api/stock_scan.add`).
			return log.project ? `Returned ${amount}` : `Added ${amount}`;
		case "Move":
			return `Moved ${amount}`;
		default:
			return amount;
	}
}

/**
 * Draw "Report a problem"? The capture recorder is on the page (`window.ee_capture`) and Frappe's
 * login cookie says System User — the same cookie test as capture/launcher.js, which this bundle
 * may not import. Only a hint: the server checks again on submit.
 */
export function reportAvailable(capture, cookie) {
	return !!(capture && typeof capture.open === "function") && /(?:^|;\s*)system_user=yes(?:;|$)/.test(String(cookie || ""));
}

/** The Undo confirmation: "Undo: took 3 Unit of Widget from Bin A1-3-1?" */
export function undoQuestion(log) {
	const l = log || {};
	const amount = `${plain(l.qty)} ${l.stock_uom || ""}`.trim();
	const item = l.item_name || l.item_code || "this item";
	const here = l.warehouse_name || l.warehouse || "this location";
	switch (l.action) {
		case "Take":
			return `Undo: took ${amount} of ${item} from ${here}?`;
		case "Receive":
			return `Undo: received ${amount} of ${item} into ${here}${l.purchase_order ? ` on ${l.purchase_order}` : ""}?`;
		case "Add Without PO":
			return l.project
				? `Undo: returned ${amount} of ${item} to ${here} from ${l.project}?`
				: `Undo: added ${amount} of ${item} to ${here}?`;
		case "Move":
			return `Undo: moved ${amount} of ${item} from ${l.from_warehouse_name || l.from_warehouse || "another location"} to ${here}?`;
		default:
			return `Undo this save of ${amount} of ${item}?`;
	}
}
