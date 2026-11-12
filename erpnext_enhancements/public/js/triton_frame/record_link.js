/**
 * "Open this record", as asked by the framed Triton chat page, turned into a Desk route or refused.
 *
 * The frame is another origin's page showing text a model wrote. What it asks the Desk to open
 * must therefore be a record, described as a record: a DocType this person can read and,
 * optionally, a name. It is never a URL to follow. `recordRoute` returns the arguments for
 * `frappe.set_route` (which moves the Desk the person is already in, with a normal history entry,
 * so Back returns to where they were) or null, and the loader does nothing on null.
 *
 * Two spellings are accepted while the contract is provisional:
 *   { doctype: "Sales Order", name: "SO-0001" }     name is optional; without it, the list
 *   { path: "/app/sales-order/SO-0001" }            or /desk/...; the slug must resolve to a DocType
 *
 * "Is a DocType" means: it is in the list of DocTypes this person may read, which the Desk already
 * has in `frappe.boot.user.can_read`. That one check does three jobs. It refuses anything that is
 * not a DocType at all, it refuses a path whose slug is a page or a workspace, and it means the
 * frame cannot use the Desk to find out which DocTypes exist beyond what the person can open
 * anyway. The server still decides whether the record itself may be read.
 */

const MAX_NAME = 140; // Frappe's own limit on a document name
// DocType names: letters, digits, spaces, hyphens, underscores (frappe's validate_name), plus the
// "&" and brackets a few shipped ones carry.
const DOCTYPE_RE = /^[A-Za-z0-9][A-Za-z0-9 _&()\-]{0,139}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001f\u007f]/;
const PATH_RE = /^\/(?:app|desk)\/([a-z0-9][a-z0-9-]*)(?:\/([^?#]+))?$/;

/** The Desk's slug for a DocType: "Sales Order" -> "sales-order" (frappe.router.slug). */
function slug(doctype) {
	return String(doctype).toLowerCase().replace(/ /g, "-");
}

function cleanName(value) {
	if (value === undefined || value === null || value === "") return "";
	if (typeof value !== "string" || value.length > MAX_NAME || CONTROL_RE.test(value)) return null;
	return value;
}

/**
 * @param {object}   target   what the frame sent
 * @param {string[]} readable DocTypes this person may read (frappe.boot.user.can_read)
 * @returns {string[]|null}   ["Form", doctype, name], ["List", doctype], or null to refuse
 */
export function recordRoute(target, readable) {
	if (!target || typeof target !== "object" || !Array.isArray(readable)) return null;

	let doctype = null;
	let name = "";

	if (typeof target.doctype === "string") {
		if (!DOCTYPE_RE.test(target.doctype) || !readable.includes(target.doctype)) return null;
		doctype = target.doctype;
		name = cleanName(target.name);
	} else if (typeof target.path === "string") {
		const match = PATH_RE.exec(target.path);
		if (!match) return null;
		doctype = readable.find((d) => slug(d) === match[1]) || null;
		if (!doctype) return null;
		if (match[2] !== undefined) {
			try {
				name = cleanName(decodeURIComponent(match[2]));
			} catch (e) {
				return null; // a malformed % escape
			}
		}
	} else {
		return null;
	}

	if (name === null) return null;
	return name ? ["Form", doctype, name] : ["List", doctype];
}
