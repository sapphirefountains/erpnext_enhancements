/*
 * form kit P4-B: the guided page's addresses, read and written in one place.
 *
 *   /desk/guided-entry                              nothing chosen yet
 *   /desk/guided-entry/<DocType>/new                step 1 of a record that does not exist yet
 *   /desk/guided-entry/<DocType>/<name>             a record, on its first active step
 *   /desk/guided-entry/<DocType>/<name>/<step_key>  a record, on one step
 *
 * Every screen is its own route segment, so v16's router (which owns popstate and re-renders
 * whatever route Back or Forward lands on, firing on_page_show) walks the steps for free. The
 * page only ever moves with frappe.set_route(build_route(...)); nothing here builds a path by
 * hand, so there is no "/app/" to get wrong (tests/test_desk_links.py).
 *
 * frappe.get_route() hands back the segments already decoded (router.js parse: decode_component
 * on each one), and set_route encodes them again (make_url: encodeURIComponent), so a name with a
 * space or a slash round-trips. Pure: no frappe, no DOM.
 */

export const PAGE = "guided-entry";
export const NEW = "new";

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

// What the address asks for: { doctype, name, step, is_new }, or null when it is not this page.
export function parse_route(route) {
	if (!Array.isArray(route) || route[0] !== PAGE) return null;
	const doctype = text(route[1]);
	const second = text(route[2]);
	if (!doctype) return { doctype: "", name: "", step: "", is_new: false };
	if (!second || second === NEW) {
		// A bare /guided-entry/<DocType> is the same screen as /new: there is no record to name.
		return { doctype, name: "", step: "", is_new: true };
	}
	return { doctype, name: second, step: text(route[3]), is_new: false };
}

// The route for a screen, as frappe.set_route takes it: ["guided-entry", doctype, name, step].
export function build_route(doctype, name, step) {
	const route = [PAGE];
	if (!doctype) return route;
	route.push(doctype);
	route.push(name ? name : NEW);
	if (name && step) route.push(step);
	return route;
}

// One string per screen, so "is that the screen already showing?" is a comparison. A record's
// bare address and its first step are told apart by the caller, which knows the first step.
export function route_key(target) {
	if (!target || !target.doctype) return "";
	return [target.doctype, target.is_new ? NEW : target.name, target.is_new ? "" : target.step || ""].join("/");
}
