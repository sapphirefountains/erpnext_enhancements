/*
 * sf_kit: translation. The page's own __() when the Desk is there, so the kit's words are
 * translated like the page's; plain {0} substitution otherwise (node, a test, a www page).
 *
 * Read at call time, not import time: the bundle can load before a test or page installs __.
 */

export function t(text, args) {
	const tr = typeof globalThis !== "undefined" && typeof globalThis.__ === "function" ? globalThis.__ : null;
	if (tr) return tr(text, args);
	return String(text).replace(/\{(\d+)\}/g, (match, index) => (args && args[index] != null ? args[index] : match));
}
