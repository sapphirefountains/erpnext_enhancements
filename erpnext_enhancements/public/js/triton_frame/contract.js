/**
 * The bridge contract between the Desk and the framed Triton chat page, as this loader speaks it.
 *
 * ONE FILE ON PURPOSE. Everything that depends on the contract's exact words (the version, the
 * envelope, the message names) is here and nowhere else, so that when the contract changes this
 * file changes and the loader, the bridge and the tests follow from it.
 *
 * PROVISIONAL (v1.802.0, stage 1). The contract is written by the Triton side (its `docs/`), and
 * that document did not exist when this file was written. The names below are the proof's
 * (`frontend/spike-embed/desk-stub/desk.js` on Triton branch
 * `claude/spike-embed-proof-do-not-merge`), plus the handful the audit named and the proof did
 * not build. Stage 2 replaces them with the document's, and adds a test that fails when this
 * version and the document's disagree.
 *
 * Shape of every message, both ways:   { triton: <version>, type: "<name>", ...fields }
 *
 * A message from the frame whose version is not CONTRACT_VERSION is dropped, with one exception
 * the loader handles itself: a "ready" with another version means the page in the frame speaks a
 * contract this loader does not know, and the loader hands the panel back to the classic widget.
 */

export const CONTRACT_VERSION = 1;

/** The key that marks an object as a bridge message and carries the version. */
export const ENVELOPE_KEY = "triton";

/** Desk to frame. */
export const TO_FRAME = Object.freeze({
	// { ticket }           a one-time sign-in ticket from api.triton_frame.get_ticket
	TICKET: "ticket",
	// { reason, message }  the Desk could not get a ticket; the frame says so in its own words
	TICKET_ERROR: "ticket-error",
	// { page }             the record, list or report the person is looking at, or null
	PAGE: "page",
	// { prompt, context }  SapphireTriton.ask(): put this in the message box, do not send it
	ASK: "ask",
	// {}                   the panel was opened / closed
	OPENED: "opened",
	CLOSED: "closed",
});

/** Frame to Desk. */
export const FROM_FRAME = Object.freeze({
	// the page has loaded and is listening; the Desk answers with a ticket and the page
	READY: "ready",
	// the token ran out (or never arrived); the Desk answers with a new ticket
	NEED_TICKET: "need-ticket",
	// { doctype, name } or { path }   open this ERPNext record in the Desk the person is in
	OPEN_RECORD: "open-record",
	// the person pressed the frame's own Close, or Escape inside it
	CLOSE: "close",
	// Alt+T pressed while typing in the frame: keys typed there never reach the Desk
	TOGGLE: "toggle",
});

/** A message ready to post. Fields never override the envelope or the type. */
export function envelope(type, fields) {
	return Object.assign({}, fields || {}, { [ENVELOPE_KEY]: CONTRACT_VERSION, type: type });
}

/**
 * What a `message` event's data says, or null when it is not a bridge message at all.
 * `{ version, type, body }`; the caller compares `version` with CONTRACT_VERSION.
 */
export function readMessage(data) {
	if (!data || typeof data !== "object" || Array.isArray(data)) return null;
	const version = data[ENVELOPE_KEY];
	if (typeof version !== "number" || typeof data.type !== "string" || !data.type) return null;
	return { version: version, type: data.type, body: data };
}
