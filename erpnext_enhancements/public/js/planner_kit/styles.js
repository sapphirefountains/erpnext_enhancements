/*
 * Planner kit: where its stylesheet went (custom pages S3.4e, v1.673.0).
 *
 * The kit's CSS is a real esbuild bundle, public/css/planner_kit.bundle.css, with a content-hashed
 * name, on the app's --sf-* tokens. It used to be the template string exported here (plus
 * conflicts.js's and blocks.js's), injected as <style id="pk-style">.
 *
 * Each planner loads the stylesheet in the same frappe.require as the kit's script (PP6A.kit and
 * MP6A.kit list both bundles): frappe.require resolves each name through frappe.boot.assets_json,
 * so the file is hashed and never served stale, appends it as a <link> after the planner page's own
 * stylesheet (which Frappe adds when the page loads), so a page rule and a kit rule of equal
 * specificity resolve as they always did, and calls back only once both have loaded, so nothing the
 * kit draws flashes unstyled. If the stylesheet cannot be fetched, frappe.require still calls back:
 * the kit's drawers and menus are plain, and nothing throws.
 *
 * planner_kit.bundle.js still writes its one <style id="pk-style"> from
 * `CSS + conflicts.CONFLICTS_CSS + blocks.BLOCKS_CSS`; all three are empty now, so it holds nothing.
 *
 * The drawer sits at z-index 1025, above the page and its sticky head (6) and Frappe's sidebar
 * (1020-1023), below a dock (1030), a modal and its backdrop (1040/1050) and the datepicker
 * (9999); the menu and the toast sit just above the drawer (see the bundle).
 */

export const STYLE_ID = "pk-style";

// Nothing is injected: the rules are planner_kit.bundle.css, loaded by the planners beside the kit.
export const CSS = "";
