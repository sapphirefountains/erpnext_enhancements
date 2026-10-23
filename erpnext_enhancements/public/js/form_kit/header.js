/*
 * What the guided page's head band says about a record (v1.711.0), as pure decisions: no DOM, no
 * frappe at import time, run under node by tests/test_guided_header.py. The band that draws them is
 * enhancements_core/page/guided_entry/guided_entry.js (refresh_chrome); the files they count are
 * ./ui/record_tools.js.
 *
 * Nik, 2026-10-21: "Need to surface attachments and show stage of opportunity at the top header for
 * the new guided layout."
 *
 *   header_status  the stage chip: the value of the field the DocType's config names under `header`
 *                  (get_flow's `header.status`), its tone and a sentence for a screen reader. A
 *                  DocType without `header` has none. The value is whatever the editor holds, so the
 *                  chip follows a change the moment it is made, saved or not.
 *   files_chip     the attachments chip: a count and a sentence, "No files" when there are none.
 *   files_drop     what dropping files on the page does (the Desk form's own drop, with the guard
 *                  the Desk form gets from "Please save before attaching").
 *   chip_action    what a click on the files chip does: scroll to the section, or, when there are no
 *                  files to show, open the uploader.
 */

// sf_kit's chip tones (public/js/sf_kit/chip.js CHIP_TONES). A tone from data is checked here too:
// an unknown one is neutral, as chip() does, so the band never carries a class nothing styles.
export const TONES = ["design", "build", "service", "rent", "neutral", "danger", "warn", "good"];

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

export function tone_of(tones, value) {
	const tone = (tones || {})[text(value)];
	return TONES.includes(tone) ? tone : "neutral";
}

/**
 * `{ field, label, value, tone, aria }` for the head band's chip, or null when the DocType declares
 * no header status or the record has no value for it yet (a chip with no word is not drawn).
 */
export function header_status(header, values) {
	const status = (header || {}).status;
	if (!status || !text(status.field)) return null;
	const value = text((values || {})[status.field]);
	if (!value) return null;
	const label = text(status.label);
	return {
		field: status.field,
		label,
		value,
		tone: tone_of(status.tones, value),
		// Colour repeats the word and never replaces it; the sentence names what the word is.
		aria: label ? `${label}: ${value}` : value,
	};
}

/**
 * The attachments chip: `{ count, aria }`. `count` is the number of files (null while the record's
 * files are not loaded yet, so the chip does not say "No files" for a moment it does not know).
 */
export function files_chip(count) {
	if (count === null || count === undefined || !Number.isFinite(Number(count))) return { count: null, aria: "" };
	const n = Math.max(0, Math.floor(Number(count)));
	return { count: n, aria: n === 1 ? "1 file attached" : n ? `${n} files attached` : "No files attached" };
}

/**
 * What a drop of `files` on the guided page does. The Desk form accepts a drop anywhere on it
 * (`Form.setup_file_drop`); the page does the same, with the same answer for a record not saved yet.
 * Always "claim" it: a file dropped on a page the page does not claim is opened by the browser,
 * which would take the person off a record they were typing in.
 *
 *   attach   the record can take files: open the uploader with them
 *   save     a new record: "Please save before attaching."
 *   denied   no write permission, or the DocType's max_attachments is reached
 *   wait     the record's permissions have not arrived yet (record_info is still loading)
 *   failed   record_info failed, so what the person may do is not known: say so, not "still loading"
 *   ignore   not a file drag (text dragged out of a field, a link): leave it to the browser
 */
export function files_drop(opts) {
	const o = opts || {};
	if (!o.has_files) return "ignore";
	if (o.is_new) return "save";
	if (o.failed) return "failed";
	if (!o.loaded) return "wait";
	return o.can_add ? "attach" : "denied";
}

/**
 * What a click on the band's files chip does: `save` (a record not saved yet cannot take files),
 * `attach` (no files yet and the person may add one: the chip says "Attach a file", so it does), or
 * `show` (scroll to the section and open it).
 */
export function chip_action(opts) {
	const o = opts || {};
	if (o.is_new) return "save";
	// Not Number(null) === 0: files not loaded yet are not "no files".
	const known = o.count !== null && o.count !== undefined && o.count !== "";
	return known && Number(o.count) === 0 && o.can_add ? "attach" : "show";
}

/**
 * Which sentence the files chip shows: `{ kind, count }`, kind one of `unknown` (files not loaded),
 * `attach` (none yet and the person may add one: the chip is a call to action), `none`, `one`, `many`.
 * The page words each kind (translated) and gives it an accessible name that contains the visible
 * word (WCAG label in name), so "Attach a file" is both when there is nothing yet to count.
 */
export function files_chip_label(count, can_add) {
	const chip = files_chip(count);
	if (chip.count === null) return { kind: "unknown", count: null };
	if (chip.count === 0) return { kind: can_add ? "attach" : "none", count: 0 };
	return { kind: chip.count === 1 ? "one" : "many", count: chip.count };
}
