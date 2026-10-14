/*
 * Planner kit: how its stylesheet reaches the page (custom pages S3.4e, v1.673.0).
 *
 * The kit's CSS is a real esbuild bundle, public/css/planner_kit.bundle.css, with a content-hashed
 * name, on the app's --sf-* tokens. It used to be the template string exported here (plus
 * conflicts.js's and blocks.js's), injected as <style id="pk-style">.
 *
 * planner_kit.bundle.js still writes that one <style id="pk-style"> element, once, from
 * `CSS + conflicts.CONFLICTS_CSS + blocks.BLOCKS_CSS`. `CSS` is now a single @import of the
 * bundle's hashed path, and the other two are empty, so the import stays the sheet's first rule
 * (an @import after any other rule is ignored). Why an @import and not a second frappe.require:
 *   - the rules land exactly where the injected text did, after the planner page's own
 *     stylesheet, so a page rule and a kit rule of equal specificity resolve as they always have
 *     (the Project Planner's .pk-tl-bar.pp-tl-colored against .pk-tl-bar.pk-tl-over, and
 *     .pp-p6d-mw .pk-block against .pk-block.pk-block-compact);
 *   - frappe.require freezes the screen until every file it was asked for has loaded, and a
 *     stylesheet that is slow or missing would hold that freeze over the planner;
 *   - the path is the same hashed one frappe.require would load: frappe.assets.bundled_asset looks
 *     the name up in frappe.boot.assets_json, so an edit is never served stale.
 * If the name is not in the build (or there is no Desk, as under node), CSS is "" and the kit's
 * drawers and menus are plain but work; nothing here throws.
 *
 * The drawer sits at z-index 1025, above the page and its sticky head (6) and Frappe's sidebar
 * (1020-1023), below a dock (1030), a modal and its backdrop (1040/1050) and the datepicker
 * (9999); the menu and the toast sit just above the drawer (see the bundle).
 */

export const STYLE_ID = "pk-style";

// The bundle's name as frappe.boot.assets_json knows it.
export const CSS_BUNDLE = "planner_kit.bundle.css";

// The @import of the hashed stylesheet, or "" when there is none to import. Pure: `frappe` is
// passed in, so tests run it under node.
export function stylesheet_import(frappe) {
	let path = "";
	try {
		const assets = frappe && frappe.assets;
		if (assets && typeof assets.bundled_asset === "function") {
			path = assets.bundled_asset(CSS_BUNDLE);
		} else if (frappe && frappe.boot && frappe.boot.assets_json) {
			path = frappe.boot.assets_json[CSS_BUNDLE];
		}
	} catch (e) {
		return "";
	}
	// Only a built file under /assets, and nothing that could end the url() early.
	if (typeof path !== "string" || !path.startsWith("/assets/") || /["'()\s]/.test(path)) return "";
	return `@import url("${path}");`;
}

export const CSS = stylesheet_import(typeof window !== "undefined" ? window.frappe : null);
