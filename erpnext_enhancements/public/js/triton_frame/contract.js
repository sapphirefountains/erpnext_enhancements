/**
 * The bridge contract between the Desk and the framed Triton chat page, as this loader speaks it.
 *
 * THE AGREEMENT IS NOT THIS FILE. It is `docs/embed-bridge.md` in the Triton repository
 * (sapphirefountains/triton), written by the Triton side; this file is the loader's copy of the
 * words in it, and `docs/triton-framed-widget.md` in this repository is the Desk's own account of
 * what the loader does with each one. Contract version 1, read at Triton pull request 379.
 *
 * ONE FILE ON PURPOSE. Everything that depends on the contract's exact words (the version, the
 * envelope, the message names) is here and nowhere else, so that when the contract changes this
 * file changes and the loader, the bridge and the tests follow from it.
 * `tests/test_triton_frame.py` fails when the version or a message name here disagrees with
 * `docs/triton-framed-widget.md`, and, where a Triton checkout is beside this repository, with
 * Triton's own document.
 *
 * Shape of every message, both ways:   { triton: <version>, type: "<name>", ...fields }
 *
 * Both sides ignore a message type or a field they do not know; that is what lets either side
 * ship first. A message whose `triton` number is not CONTRACT_VERSION is ignored too, with one
 * exception the loader handles itself: a `ready` in any other version means the page in the frame
 * speaks a contract this loader does not know, and the loader hands the panel back to the classic
 * widget at once instead of waiting out a timer.
 */

export const CONTRACT_VERSION = 1;

/** The key that marks an object as a bridge message and carries the version. */
export const ENVELOPE_KEY = "triton";

/** Desk to frame. Nothing is sent before the frame has said `ready`: it is not listening. */
export const TO_FRAME = Object.freeze({
	// { ticket }        a one-time sign-in ticket, in answer to `ready` or `need-ticket`
	TICKET: "ticket",
	// { message }       the Desk cannot give a ticket: one plain sentence, shown as written
	TICKET_ERROR: "ticket-error",
	// { page }          a page reference (capture/page_context.js), or null
	PAGE: "page",
	// { prompt, page? } SapphireTriton.ask(): put this in the message box; never sent for them
	ASK: "ask",
	// { open }          the panel was opened or closed
	VISIBILITY: "visibility",
});

/** Frame to Desk. */
export const FROM_FRAME = Object.freeze({
	// { version }       the page has loaded and wants to be signed in
	READY: "ready",
	// { reason }        the token ran out ("expired") or the person pressed Try again ("retry")
	NEED_TICKET: "need-ticket",
	// {}                a ticket was traded for a token: the framed chat works for this person
	SIGNED_IN: "signed-in",
	// { reason }        "no-answer" | "refused" | "bad-ticket" | "no-config"
	SIGN_IN_FAILED: "sign-in-failed",
	// { path }          a link to a page of this Desk; the Desk checks it and routes it
	OPEN_RECORD: "open-record",
	// { mode }          "wide" when a document opens beside the chat, "panel" when it closes
	RESIZE: "resize",
	// {}                the person pressed Close in the chat's own header
	CLOSE: "close",
	// { key }           "alt+t" | "escape", pressed while the cursor was in the chat
	KEY: "key",
	// { chat_id, title } a reply finished; a framed page may not notify, the Desk may
	REPLY_FINISHED: "reply-finished",
	// { params }        the assistant was asked to clock the person in; the card is the Desk's
	CLOCK_IN: "clock-in",
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
