/**
 * What the requester was looking at when they hit "report", derived rather than asked.
 *
 * Pure functions, no DOM reads — the caller passes the referrer and user agent in, so this
 * can be exercised by a plain node script.
 *
 * The point of capturing this is the round trip it saves. "Which screen were you on?" is the
 * first question every bug report gets and the one the reporter has already forgotten the
 * answer to by the time it is asked. `context_app_version` answers the second one — "is this
 * already fixed?" — before anybody opens the code.
 *
 * Everything here is **untrusted display data**. It is stored as text, shown as text, never
 * followed and never used in a query; `Enhancement Request.context_doctype` is a `Data` field
 * rather than a `Link` for exactly that reason.
 */

/**
 * Pull `{url, doctype, docname}` out of a referrer.
 *
 * Desk URLs are `/app/<doctype-slug>/<docname>`; a list view is `/app/<doctype-slug>` with no
 * name. Anything else (a portal page, an external site, no referrer at all) yields the URL
 * and two empty strings, which is still worth having.
 *
 * Only same-origin referrers are kept. A referrer from elsewhere tells us nothing about our
 * own software and storing it would quietly record where employees browse.
 */
export function parseReferrer(referrer, origin) {
	const out = { url: "", doctype: "", docname: "" };
	const raw = String(referrer || "").trim();
	if (!raw) return out;

	let parsed;
	try {
		parsed = new URL(raw, origin || undefined);
	} catch (e) {
		return out;
	}
	if (origin && parsed.origin !== origin) return out;

	out.url = parsed.pathname + parsed.search;

	const parts = parsed.pathname.split("/").filter(Boolean);
	if (parts[0] === "app" && parts[1]) {
		out.doctype = deslug(parts[1]);
		if (parts[2]) out.docname = decodeURIComponent(parts[2]);
	}
	return out;
}

/** `sales-invoice` -> `Sales Invoice`. Frappe's own desk slug, reversed. */
function deslug(slug) {
	return decodeURIComponent(String(slug || ""))
		.split("-")
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

/**
 * The whole captured-context payload.
 *
 * The user agent is truncated here rather than server-side as well as: modern UA strings run
 * to 200+ characters of brand list, and the column is 300.
 */
export function captureContext({ referrer, origin, userAgent, build }) {
	const referrerParts = parseReferrer(referrer, origin);
	return {
		context_url: referrerParts.url,
		context_doctype: referrerParts.doctype,
		context_docname: referrerParts.docname,
		context_user_agent: String(userAgent || "").slice(0, 300),
		context_app_version: String(build || ""),
	};
}
