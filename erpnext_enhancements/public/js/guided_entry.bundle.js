/*
 * Guided Entry entry point (form kit P4-B): the pure decisions of the guided step-by-step page
 * (enhancements_core/page/guided_entry), as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by that page, never app_include_js, together with sf_kit and the two stylesheets:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "guided_entry.bundle.js",
 *                   "guided_entry.bundle.css"], () => { const kit = window.sf_form_kit; ... });
 *
 * and, since P4.4 (v1.668.0), on demand by a page that opens the side panel (the Opportunity board),
 * after planner_kit.bundle.js, whose drawer the panel lives in.
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_form_kit`: every export of public/js/form_kit/index.js (routes, step
 * activeness, missing-per-step, input modes, the text-list table, conflict diffing, the save
 * queue, the journal and the side panel's decisions), and the record editor both shells run:
 *
 *   RecordEditor   form_kit/ui/editor.js: fields, values, the save queue, conflicts, the journal
 *   mix_editor     the guided page's way to RecordEditor (its class is defined before this loads)
 *   SidePanel      form_kit/ui/side_panel.js: the same editor in the planner kit's drawer
 *
 * The guided page's DOM and Frappe wiring stay in the page script; the pure decisions live in
 * form_kit/*.js so tests/test_guided_entry.py can run them under node.
 */

import * as form_kit from "./form_kit/index.js";
import { RecordEditor, mix_editor } from "./form_kit/ui/editor.js";
import { SidePanel } from "./form_kit/ui/side_panel.js";

// 2 since v1.668.0: the editor and the side panel. A tab still holding version 1 (loaded before a
// deploy) takes this one, so a page that needs SidePanel finds it.
const VERSION = 2;

try {
	if (typeof window !== "undefined" && !(window.sf_form_kit && window.sf_form_kit.version >= VERSION)) {
		window.sf_form_kit = Object.assign({ version: VERSION }, form_kit, { RecordEditor, mix_editor, SidePanel });
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_form_kit failed to start", e);
}
