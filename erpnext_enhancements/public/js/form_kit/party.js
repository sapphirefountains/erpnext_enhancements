/*
 * form kit P3.2 (TASK-2026-02755): the Party entry component's decisions, as a pure module (no
 * frappe, no DOM), run under node by tests/test_form_kit_party.py. The component itself is
 * ./ui/party.js; the server half of its one question is form_kit/party.py (party_details).
 *
 * Which field is which comes from the information blocks (public/js/info_blocks/blocks.js, the twin
 * of form_kit/blocks.py), passed in as `blocks` so this module imports nothing:
 *
 *   party_roles(blocks, doctype)      { account_type, account, accounts, contact, billing, site }
 *   role_of(roles, fieldname)         "account_type" | "account" | "contact" | "billing" | "site" | null
 *   account_of(roles, values)         { doctype, name } of the record's account, or null
 *   create_kind(roles, field, values) what "+ New" on a field makes: { doctype, role }
 *   details_args(roles, values)       the record's current contact and addresses, for party_details
 *   plan_account_change(...)          what changes when the account does, and what is kept
 */

// The roles that follow the account: picked from its own records, re-suggested when it changes.
export const FOLLOWERS = ["contact", "billing", "site"];

function has(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

function text(value) {
	return value === null || value === undefined ? "" : String(value);
}

// The twin of form_kit.blocks.party_roles: the Party block's roles on `doctype` (its address is the
// billing address) and the Site block's address as `site`; null when no Party block names it.
export function party_roles(blocks, doctype) {
	const party = blocks && blocks.party && has(blocks.party.doctypes, doctype) ? blocks.party.doctypes[doctype] : null;
	if (!party) return null;
	const roles = party.roles || {};
	const site_spec = blocks.site && has(blocks.site.doctypes, doctype) ? blocks.site.doctypes[doctype] : null;
	const site = (site_spec && site_spec.roles) || {};
	return {
		account_type: roles.account_type || null,
		account: roles.account || null,
		accounts: (party.accounts || []).slice(),
		contact: roles.contact || null,
		billing: roles.address || null,
		site: site.address || null,
	};
}

export function role_of(roles, fieldname) {
	if (!roles || !fieldname) return null;
	for (const role of ["account_type", "account", ...FOLLOWERS]) {
		if (roles[role] === fieldname) return role;
	}
	return null;
}

// The kind of account the record has: the account-type field's value (Opportunity's opportunity_from),
// or the one kind there is (Project: Customer). Only a kind the block allows.
export function account_type_of(roles, values) {
	if (!roles) return null;
	const kind = roles.account_type ? text((values || {})[roles.account_type]) : roles.accounts[0] || "";
	return kind && roles.accounts.includes(kind) ? kind : null;
}

export function account_of(roles, values) {
	if (!roles || !roles.account) return null;
	const doctype = account_type_of(roles, values);
	const name = text((values || {})[roles.account]).trim();
	return doctype && name ? { doctype, name } : null;
}

// What "+ New" on `fieldname` creates: an account of the record's kind, a Contact, or an Address.
// null when the field has no role, or the account's kind is not known yet.
export function create_kind(roles, fieldname, values) {
	const role = role_of(roles, fieldname);
	if (role === "account") {
		const doctype = account_type_of(roles, values);
		return doctype ? { doctype, role } : null;
	}
	if (role === "contact") return { doctype: "Contact", role };
	if (role === "billing" || role === "site") return { doctype: "Address", role };
	return null;
}

// The record's current contact and addresses, for party_details: which of them are the account's.
export function details_args(roles, values) {
	const v = values || {};
	const pick = (list) => [...new Set(list.map((field) => (field ? text(v[field]).trim() : "")).filter(Boolean))];
	if (!roles) return { contacts: [], addresses: [] };
	return { contacts: pick([roles.contact]), addresses: pick([roles.billing, roles.site]) };
}

// When the account changes, for each field that follows it (contact, billing address, site):
//   - a value linked to the new account stays;
//   - a value the person chose in this session (`chosen`) stays, and is reported in `kept` with the
//     new account's suggestion, so the page can say so: it is never overwritten silently;
//   - anything else (empty, or set before this session, or by the server) becomes the new account's
//     default, or empty when it has none, as ERPNext's own form does (get_party_details).
// `editable`, when given, is the set of fieldnames the person may change: nothing else is touched.
// `details` is party_details' answer. Returns { set: { fieldname: value }, kept: [{ fieldname, role,
// suggestion }] }.
export function plan_account_change({ roles, values, chosen, details, editable }) {
	const out = { set: {}, kept: [] };
	if (!roles || !details) return out;
	const v = values || {};
	const defaults = details.defaults || {};
	const linked = details.linked || {};
	const picked = chosen instanceof Set ? chosen : new Set(chosen || []);
	const can = editable instanceof Set ? editable : editable ? new Set(editable) : null;
	FOLLOWERS.forEach((role) => {
		const fieldname = roles[role];
		if (!fieldname || (can && !can.has(fieldname))) return;
		const current = text(v[fieldname]).trim();
		const own = (role === "contact" ? linked.Contact : linked.Address) || [];
		if (current && own.includes(current)) return;
		const suggestion = defaults[role] || null;
		if (current && picked.has(fieldname)) {
			out.kept.push({ fieldname, role, suggestion });
			return;
		}
		if (current !== text(suggestion)) out.set[fieldname] = suggestion;
	});
	return out;
}
