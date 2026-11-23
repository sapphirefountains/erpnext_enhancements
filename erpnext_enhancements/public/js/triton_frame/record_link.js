/**
 * "Open this page of the Desk", as asked by the framed Triton chat page: checked, or refused.
 *
 * The person followed a link in a reply. The frame does not navigate; it sends the link's path
 * and asks the Desk to. The frame is another origin's page showing text a model wrote, so the
 * path is checked here before anything acts on it, and the answer is either a route for the Desk's
 * own router or null. The loader does nothing on null.
 *
 * WHAT IS ACCEPTED: a path on this Desk, under `/desk` or `/app` (the two spellings of a Desk
 * address), with an optional query and fragment. That is any Desk page, workspace segment
 * included (`/desk/stock/item/ITEM-0001`): the frame has a URL and nothing else, and does not
 * know DocType names. It is also exactly what the classic widget has always done, where a
 * `/app/...` link in a reply is followed in place by the Desk's own router. Following one moves
 * the Desk and writes nothing; what the person may see there is still the server's decision.
 *
 * WHAT IS REFUSED, and each is refused twice, once by the pattern and once by the parser:
 *   - anything that is not a string, or is longer than a Desk address could be;
 *   - a full URL, or anything with a scheme (`https://…`, `javascript:…`, `data:…`);
 *   - a protocol-relative value (`//host/desk/x`), and the backslash spelling browsers read the
 *     same way (`/\host`);
 *   - a path outside `/desk` and `/app` (`/api/method/logout`, `/files/x`), including one that
 *     only gets outside after `..` is resolved (`/desk/../api/method/logout`);
 *   - control characters, and characters a path does not carry unencoded.
 *
 * The parser check resolves the path against a made-up base and requires the result to still be
 * on that base's origin and still under `/desk` or `/app`. It is the half that catches what a
 * pattern reads differently from a browser.
 */

const MAX_PATH = 2000;
// The characters RFC 3986 lets a path segment carry, plus the space Frappe names often have.
const PATH_RE = /^\/(?:desk|app)(?:\/[A-Za-z0-9 ._~%!$&'()*+,;=:@-]+)*\/?(?:\?[^#]*)?(?:#.*)?$/;
// eslint-disable-next-line no-control-regex
const UNSAFE_RE = /[\u0000-\u001f\u007f\\]/;
const BASE = "https://desk.invalid";

/**
 * @param {*} path  what the frame sent as `open-record.path`
 * @returns {{route: string, hash: string}|null}
 *   `route` is the path and query for `frappe.set_route`; `hash` is the fragment ("" or "#…"),
 *   which the Desk's router takes separately (`frappe.route_hash`). Null to refuse.
 */
export function deskRoute(path) {
	if (typeof path !== "string" || !path || path.length > MAX_PATH) return null;
	if (UNSAFE_RE.test(path) || path.indexOf("//") === 0 || !PATH_RE.test(path)) return null;
	// A `//` inside the path part is never a Desk address, and is how a second host is smuggled.
	if (path.split(/[?#]/)[0].indexOf("//") !== -1) return null;

	let url;
	try {
		url = new URL(path, BASE);
	} catch (e) {
		return null;
	}
	if (url.origin !== BASE || url.username || url.password) return null;
	const inside = /^\/(?:desk|app)(?:\/|$)/.test(url.pathname);
	if (!inside) return null;
	return { route: url.pathname + url.search, hash: url.hash };
}
