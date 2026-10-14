/*
 * form kit P4.7: what the guided page keeps on the phone while it cannot reach the server.
 *
 * Nik, 2026-10-13: forms keep working when the connection drops (field staff lose signal on
 * sites). The guided page was open when the signal went, so the record is on screen; what has
 * not reached the server must not be lost to a reload, a crash, or the phone closing the browser.
 * The Visit Wizard's journal (v1.596.0) settled how, and this follows it:
 *
 * - What the save queue holds for a record (waiting, or out in a request) is written to the
 *   phone on every change, with the server's value from before each change (`base`) and the
 *   record's `modified`. A save that lands takes its fields out; nothing left, nothing kept.
 * - Opening the record again puts each kept value back, decided by the server's current one
 *   (reconcile): unchanged since (still `base`), the kept value goes back on and is saved;
 *   already there (a save that landed and lost its answer), it is dropped; changed by someone
 *   else, the person is asked, the same side-by-side choice as any conflict. A newer edit on the
 *   server is never overwritten without saying so.
 * - A save that fails for want of signal (try_later) is retried when the signal comes back.
 *
 * Pure: no frappe, no DOM, no storage. The page owns localStorage and the timers.
 */

import { same_value } from "./conflict.js";

export const JOURNAL_PREFIX = "ge-unsent:";
// A kept change older than this is from a visit nobody came back to; it is not put back.
export const JOURNAL_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

const TRY_LATER = [0, 408, 425, 429, 502, 503, 504];

export function journal_key(doctype, name) {
	return `${JOURNAL_PREFIX}${doctype}/${name}`;
}

// Whether a failed save is the network's doing (no signal, a gateway, a restart), so it is kept
// and retried, rather than the server refusing it (a validation, a permission).
export function try_later(error) {
	if (!error || error.session_expired) return !error;
	const status = Number(error.status) || 0;
	if (TRY_LATER.includes(status)) return true;
	return status >= 500 && !error.exc_type;
}

function own(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

function copy(value) {
	return value === undefined ? null : JSON.parse(JSON.stringify(value));
}

// The entry to keep for a record, or null when nothing is unsent. `unsent` is every value not
// yet stored (the queue's pending, over what is in flight); `base` the server's value from before
// each was first changed, of which only the unsent fields are kept.
export function make_entry({ doctype, name, modified, unsent, base, at }) {
	const fields = Object.keys(unsent || {});
	if (!doctype || !name || !fields.length) return null;
	const kept_base = {};
	fields.forEach((fieldname) => {
		kept_base[fieldname] = own(base, fieldname) ? copy(base[fieldname]) : null;
	});
	return {
		doctype,
		name,
		modified: modified || null,
		unsent: copy(unsent),
		base: kept_base,
		at: Number(at) || 0,
	};
}

// Whether a stored entry is one this page wrote and still worth putting back.
export function usable_entry(entry, now) {
	if (!entry || typeof entry !== "object" || !entry.unsent || typeof entry.unsent !== "object") return false;
	if (!Object.keys(entry.unsent).length) return false;
	return !(Number(now) && Number(entry.at) && Number(now) - Number(entry.at) > JOURNAL_MAX_AGE_MS);
}

// What to do with each kept value against the server's current ones. Returns
// { reapply: {fieldname: value}, drop: [fieldname], conflicts: [{fieldname, mine, theirs}] }:
// conflicts in the shape conflict.js's split_conflict gives the page's chooser.
export function reconcile(entry, server_values) {
	const out = { reapply: {}, drop: [], conflicts: [] };
	if (!entry || !entry.unsent) return out;
	const server = server_values || {};
	Object.keys(entry.unsent).forEach((fieldname) => {
		const mine = entry.unsent[fieldname];
		const theirs = own(server, fieldname) ? server[fieldname] : null;
		const base = own(entry.base, fieldname) ? entry.base[fieldname] : null;
		if (same_value(mine, theirs)) out.drop.push(fieldname);
		else if (same_value(theirs, base)) out.reapply[fieldname] = mine;
		else out.conflicts.push({ fieldname, mine, theirs });
	});
	return out;
}
