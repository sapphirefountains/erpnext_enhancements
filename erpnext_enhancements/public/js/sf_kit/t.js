/*
 * sf_kit: translation. The page's own __() when the Desk is there, so the kit's words are
 * translated like the page's; plain {0} substitution otherwise (node, a test, a www page).
 *
 * Read at call time, not import time: the bundle can load before a test or page installs __.
 *
 * The kit's own sentences are written as literal `__("Loading…")` calls, with `__` imported from
 * here, because that is the only shape frappe's extractor finds: v16's
 * frappe/gettext/extractors/javascript.py tokenizes the source with babel's JS lexer and keeps a
 * call only when the name token right before `(` is `__` and the first argument is a string
 * literal. `t("Loading…")` or `env.t(...)` is never extracted, so those words could never be
 * translated (v1.630.0). This module-level `__` shadows the Desk's global one inside the kit and
 * hands straight to it when it exists; without one (node) it substitutes {0} itself.
 *
 * `t` stays for a caller's own dynamic text; it is the same function.
 */

export function __(text, args, context) {
	const tr = typeof globalThis !== "undefined" && typeof globalThis.__ === "function" ? globalThis.__ : null;
	if (tr && tr !== __) return tr(text, args, context);
	return String(text).replace(/\{(\d+)\}/g, (match, index) => (args && args[index] != null ? args[index] : match));
}

export function t(text, args) {
	return __(text, args);
}
