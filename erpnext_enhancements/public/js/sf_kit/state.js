/*
 * sf_kit: the panel a page shows instead of its content while it loads, when there is nothing to
 * show, when the load failed, or when the reader may not see it.
 *
 *   sf_kit.state.render(el, "loading");
 *   sf_kit.state.render(el, "error", { body: err.message, on_action: load });   // "Try again"
 *   el.innerHTML = sf_kit.state.html("empty", { title: __("No visits this week.") });
 *
 * It is page content, written into the element the content would go in: never an overlay, never
 * a dialog, and it pushes no history entry, so Back still leaves the page in one step.
 *
 * One optional action button, drawn only when there is something to call (`on_action`); render()
 * wires it, and html() leaves it for a caller that delegates clicks on `.sf-state-action`.
 * Loading is role="status" (announced politely), error role="alert" (announced at once). The
 * spinner stops turning under prefers-reduced-motion (the CSS); the word "Loading" carries the
 * meaning either way.
 */

import { esc } from "./esc.js";
import { t } from "./t.js";

export const STATE_KINDS = ["loading", "empty", "error", "no-access"];

const ROLES = { loading: "status", error: "alert" };

function default_title(kind) {
	if (kind === "loading") return t("Loading…");
	if (kind === "error") return t("Something went wrong.");
	if (kind === "no-access") return t("You do not have access to this.");
	return t("Nothing here yet.");
}

export function state_kind(kind) {
	return STATE_KINDS.includes(kind) ? kind : "empty";
}

export function state_html(kind, opts) {
	opts = opts || {};
	kind = state_kind(kind);
	const role = ROLES[kind] ? ` role="${ROLES[kind]}"` : "";
	const live = kind === "loading" ? ' aria-live="polite"' : "";
	const title = opts.title === undefined || opts.title === null || opts.title === "" ? default_title(kind) : opts.title;
	const parts = [];
	if (kind === "loading") parts.push('<span class="sf-state-spinner" aria-hidden="true"></span>');
	if (kind === "error") parts.push('<span class="sf-state-mark" aria-hidden="true">!</span>');
	parts.push(`<p class="sf-state-title">${esc(title)}</p>`);
	if (opts.body !== undefined && opts.body !== null && opts.body !== "") {
		parts.push(`<p class="sf-state-body">${esc(opts.body)}</p>`);
	}
	if (typeof opts.on_action === "function") {
		const label = opts.action_label || t("Try again");
		parts.push(`<button type="button" class="sf-btn sf-btn--secondary sf-state-action">${esc(label)}</button>`);
	}
	return `<div class="sf-state sf-state--${kind}"${role}${live}>${parts.join("")}</div>`;
}

// `el` is an element or a jQuery object. Returns the panel element.
export function render_state(el, kind, opts) {
	const host = el && el.jquery ? el[0] : el;
	if (!host) return null;
	opts = opts || {};
	host.innerHTML = state_html(kind, opts);
	const button = typeof opts.on_action === "function" && host.querySelector(".sf-state-action");
	if (button) {
		button.addEventListener("click", (e) => {
			if (e && typeof e.preventDefault === "function") e.preventDefault();
			opts.on_action(e);
		});
	}
	return host.firstElementChild || host.firstChild || null;
}
