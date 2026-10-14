/*
 * sf_kit entry point: the shared page pieces every custom Desk page and Custom HTML Block uses
 * (state panels, chips, cards, buttons, toasts, status tiles, one escape and one frappe.call
 * wrapper), as one esbuild bundle with a content-hashed name. Built on the app design tokens
 * (sf_tokens.bundle.css, global); the guide is "sf_kit: the shared page pieces" in
 * docs/app-design-system.md.
 *
 * Loaded only by a page that uses it, never app_include_js:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () => { const sf = window.sf_kit; ... });
 *
 * v16's frappe.require maps each name through frappe.boot.assets_json (assets.js bundled_asset),
 * so both resolve to their hashed files, and the .css one becomes a <link>. A bundle rather than
 * a raw /assets path because raw paths are served immutable for a year with no hash (the "fix
 * works on desktop, phones still broken" trap in CLAUDE.md). For the same reason this file does
 * NOT inject its CSS: a <style> written from JS would be a second copy to keep in step, and the
 * hashed .css is already the cache-safe way. A shadow-root block adopts `sf_kit.CSS` instead.
 *
 * It installs `window.sf_kit`:
 *
 *   esc(value)                                  HTML-escape; null/undefined -> ""
 *   t(text, args)                               __() when present, else {0} substitution
 *   call(method, args, {silent, quiet, method, with_messages})
 *                                               -> Promise of r.message; rejects
 *                                                  {status, message, exc_type, session_expired};
 *                                                  quiet: true = fetch, form-encoded exactly as
 *                                                  frappe.call sends it, no frappe dialog at all
 *   state.html(kind, opts) / state.render(el, kind, opts)   loading | empty | error | no-access
 *   chip(label, tone)                           pillar or status pill, always with its word
 *   card.html({title, body_html, pillar}) / card.class_name(pillar)
 *   tiles.html(items) / tiles.tile(item) / tiles.safe_href(href)
 *   toast(message, {action_label, on_action, timeout, tone}); toast.close(); toast.current()
 *   create_toast({prefix, container})           a separate toast, e.g. inside a block's shadow root
 *   CSS                                         the stylesheet text, for a shadow root
 *
 * The same functions are importable as ES modules from public/js/sf_kit/index.js.
 */

import {
	esc,
	t,
	call,
	STATE_KINDS,
	state_html,
	render_state,
	CHIP_TONES,
	chip,
	card_class,
	card_html,
	safe_href,
	tile_html,
	tiles_html,
	create_toast,
	CSS,
} from "./sf_kit/index.js";

// 2 (v1.632.0): call()'s quiet path and its rejection shape, the shared toast registry. A page
// that loaded version 1 first gets this one in its place.
const VERSION = 2;

function install() {
	if (typeof window === "undefined" || typeof document === "undefined") return;
	if (window.sf_kit && window.sf_kit.version >= VERSION) return;

	const env = {
		doc: document,
		t,
		set_timeout: (fn, ms) => window.setTimeout(fn, ms),
		clear_timeout: (id) => window.clearTimeout(id),
		// The quiet path's transport and the session-expiry check's cookie, both read per call.
		cookie: () => document.cookie,
	};
	// Read frappe and fetch when a call is made, not when the bundle loads.
	Object.defineProperty(env, "frappe", { get: () => window.frappe, enumerable: true });
	Object.defineProperty(env, "fetch", {
		get: () => (typeof window.fetch === "function" ? (url, init) => window.fetch(url, init) : undefined),
		enumerable: true,
	});

	window.sf_kit = {
		version: VERSION,
		esc,
		t,
		call: (method, args, opts) => call(env, method, args, opts),
		state: { kinds: STATE_KINDS, html: state_html, render: render_state },
		chip,
		chip_tones: CHIP_TONES,
		card: { html: card_html, class_name: card_class },
		tiles: { html: tiles_html, tile: tile_html, safe_href },
		toast: create_toast(env),
		create_toast: (options) => create_toast(env, options),
		CSS,
	};
}

try {
	install();
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_kit failed to start", e);
}
