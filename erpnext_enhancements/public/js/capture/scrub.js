/**
 * Scrubbing for the capture recorder. Pure functions with no DOM, so a plain node script can
 * prove them (`scripts/test_capture_scrub.js`).
 *
 * Text is scrubbed when the recorder *stores* it, not when a report is sent: the rings live for
 * the page's lifetime, and a snapshot is only a copy of what they already hold. Two things are
 * removed, emails and token-shaped strings, because those are what turn a bug report into a
 * credential leak. The report is a File that any reviewer can open.
 *
 * The token rule is a heuristic, tuned in both directions. It leaves Frappe's own vocabulary
 * alone (dotted method paths, snake_case, CamelCase exception names, naming-series doc names)
 * and catches strings that look random. A false positive costs a reviewer one word. A false
 * negative costs a credential.
 */

const REPLACED_TOKEN = "[token]";
const REPLACED_EMAIL = "[email]";

const JWT = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?/g;
// `%40` too: an email percent-encoded into a URL or a console message is still an email.
const EMAIL = /[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
// Frappe's `Authorization: token <api_key>:<api_secret>`. Each half is 15 hex characters, so
// neither half is long enough for the run rule below.
const KEY_PAIR = /\b([A-Za-z0-9]{10,}):([A-Za-z0-9]{10,})\b/g;
// Standard base64 can contain "/", which makes it look like a path. It gets its own rule.
const BASE64 = /[A-Za-z0-9+/]{24,}={0,2}/g;
// "/" is left out of the run on purpose. Otherwise `/itinerary/<hex>` becomes one run and the
// whole path is replaced, when only the last segment is the secret.
const RUN = /[A-Za-z0-9+_=-]{24,}/g;
const URL_QUERY = /(\bhttps?:\/\/[^\s?#"'<>()]+)\?[^\s"'<>()]*/gi;
const PATH_QUERY = /((?:^|[\s"'(])\/[A-Za-z0-9_\-./%]*)\?[^\s"'<>()=]*=[^\s"'<>()]*/g;

function charClass(c) {
	if (c >= "a" && c <= "z") return 1;
	if (c >= "A" && c <= "Z") return 2;
	if (c >= "0" && c <= "9") return 3;
	return 0;
}

/**
 * Does this run of characters look random rather than written?
 *
 * Hex (and so every UUID) always counts. Anything else needs letters *and* digits, and must
 * change between lower case, upper case and digits often. A capital followed by a lower-case
 * letter is not counted as a change, because that is how every CamelCase word starts. On that
 * measure a random base64 string changes on about 44% of its characters, while
 * `iPhone12ProMaxUltraEdition2026` changes on 24%, `ACCSINV202600001` on 13%, and
 * `CannotChangeConstantError` has no digit at all.
 */
export function looksLikeToken(run) {
	const s = String(run || "").replace(/[^A-Za-z0-9]/g, "");
	if (s.length < 20) return false;
	if (/^[0-9a-f]+$/i.test(s)) return /[0-9]/.test(s) && /[a-f]/i.test(s);
	if (!/[0-9]/.test(s) || !/[A-Za-z]/.test(s)) return false;
	let changes = 0;
	for (let i = 1; i < s.length; i++) {
		const before = charClass(s[i - 1]);
		const after = charClass(s[i]);
		if (before !== after && !(before === 2 && after === 1)) changes++;
	}
	return changes / (s.length - 1) >= 0.28;
}

function clip(s, max) {
	if (s.length <= max) return s;
	let end = Math.max(0, max - 1);
	// Never half an emoji: a lone surrogate makes the server refuse the whole report.
	const last = end > 0 ? s.charCodeAt(end - 1) : 0;
	if (last >= 0xd800 && last <= 0xdbff) end -= 1;
	return s.slice(0, end) + "…";
}

/**
 * Free text (a console message, a title, a filter value) with emails, tokens and URL query
 * strings removed, then clipped to `max` characters.
 *
 * The input is clipped *generously* first, so a megabyte-long message costs a bounded amount
 * of regex work, and clipped to `max` only after scrubbing, so a token cut in half by the
 * limit has already been replaced.
 */
export function scrubText(value, max) {
	const limit = max > 0 ? max : 500;
	if (value === null || value === undefined) return "";
	let s = String(value);
	if (s.length > limit * 4) s = s.slice(0, limit * 4);
	s = s
		.replace(URL_QUERY, "$1?[query]")
		.replace(PATH_QUERY, "$1?[query]")
		.replace(JWT, REPLACED_TOKEN)
		.replace(EMAIL, REPLACED_EMAIL)
		.replace(KEY_PAIR, (m, a, b) =>
			/[0-9]/.test(a) && /[0-9]/.test(b) && /[A-Za-z]/.test(a + b) ? REPLACED_TOKEN : m
		)
		.replace(BASE64, (m) => {
			if (m.indexOf("/") === -1 && m.indexOf("+") === -1) return m;
			// A lower-case word between slashes is a path (`/desk/todo/...`), not base64.
			if (m.split("/").some((part) => /^[a-z][a-z-]{2,}$/.test(part))) return m;
			return looksLikeToken(m) ? REPLACED_TOKEN : m;
		})
		.replace(RUN, (m) => (looksLikeToken(m) ? REPLACED_TOKEN : m));
	return clip(s, limit);
}

/**
 * A URL reduced to something safe to keep: no query string, no fragment, no credentials, and
 * each path segment scrubbed. Same-origin URLs become a bare path. A foreign one keeps its host
 * as `//host/path`, because "the Maps script failed" is worth knowing and the host is not
 * a secret.
 *
 * The query and fragment go before anything else is looked at. Token pages carry their secret
 * there, and the recorder never keeps it.
 */
export function scrubPath(value, origin) {
	let s = value === null || value === undefined ? "" : String(value).trim();
	if (!s) return "";
	if (/^(data|blob|javascript):/i.test(s)) return s.split(":")[0].toLowerCase() + ":";
	s = s.replace(/[?#][\s\S]*$/, "");

	let prefix = "";
	const abs = /^([a-z][a-z0-9+.-]*:)?\/\/([^/]*)([\s\S]*)$/i.exec(s);
	if (abs) {
		// userinfo (`user:pass@host`) is a credential in the one place nobody looks for it.
		const host = abs[2].replace(/^[^@]*@/, "").toLowerCase();
		const here = String(origin || "").toLowerCase();
		const same = here && (abs[1] ? abs[1].toLowerCase() + "//" + host === here : here.endsWith("//" + host));
		if (!same) prefix = "//" + host;
		s = abs[3] || "/";
	}

	const segments = s.split("/").map((segment) => {
		if (!segment) return segment;
		let decoded = segment;
		try {
			decoded = decodeURIComponent(segment);
		} catch (e) {
			// Malformed percent-encoding: scrub it as written.
		}
		return scrubText(decoded, 120);
	});
	return clip(prefix + segments.join("/"), 300);
}
