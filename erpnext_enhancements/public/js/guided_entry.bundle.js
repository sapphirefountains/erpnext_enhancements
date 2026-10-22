/*
 * Guided Entry entry point (form kit P4-B): the pure decisions of the guided step-by-step page
 * (enhancements_core/page/guided_entry), as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by that page, never app_include_js, together with sf_kit and the two stylesheets:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "guided_entry.bundle.js",
 *                   "guided_entry.bundle.css"], () => { const kit = window.sf_form_kit; ... });
 *
 * and, since P4.4 (v1.675.0), on demand by a page that opens the side panel (the Opportunity board),
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
 * form_kit/*.js so tests/test_guided_entry.py can run them under node. It also carries the DocType
 * extensions the editor draws step blocks with (form kit P8.1; public/js/guided_item/ for Item),
 * found through sf_form_kit.extension_for(doctype).
 */

import * as form_kit from "./form_kit/index.js";
import { RecordEditor, mix_editor } from "./form_kit/ui/editor.js";
import { SidePanel } from "./form_kit/ui/side_panel.js";
// Each DocType's extension registers itself with form_kit/extensions.js (form kit P8.1): the Item
// flow's name builder and its supplier, stock and asset blocks.
import "./guided_item/extension.js";

// 2 since v1.675.0: the editor and the side panel. 3 since v1.683.0 (form kit P8.1): extension_for,
// ends_core and opens_by_default. 4 since v1.684.0: the view settings (form_kit/view.js) and the
// FieldGroups of One page and Tabs, which the page now calls. A tab still holding an older version
// (loaded before a deploy) takes this one, so a page that needs SidePanel, an extension or the view
// settings finds it. 5 since v1.711.0: header_status, files_chip_label, files_drop and chip_action
// (form_kit/header.js), which the page's head band calls; a tab that holds 4 would throw out of
// refresh_chrome.
const VERSION = 5;

try {
	if (typeof window !== "undefined" && !(window.sf_form_kit && window.sf_form_kit.version >= VERSION)) {
		window.sf_form_kit = Object.assign({ version: VERSION }, form_kit, { RecordEditor, mix_editor, SidePanel });
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_form_kit failed to start", e);
}
