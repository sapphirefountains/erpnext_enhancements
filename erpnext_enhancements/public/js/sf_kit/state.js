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
 * One optional action button, drawn when there is an `action_label` or an `on_action` (label
 * "Try again" by default). render() wires `on_action` when it is given; html() never can, so a
 * caller that builds the markup itself passes `action_label` and delegates clicks on
 * `.sf-state-action` (v1.632.0: html() used to drop the button unless it had a function it could
 * not use). Loading is role="status" (announced politely), error role="alert" (announced at once).
 * The spinner stops turning under prefers-reduced-motion (the CSS); the word "Loading" carries the
 * meaning either way.
 */

import { esc } from "./esc.js";
import { __ } from "./t.js";

export const STATE_KINDS = ["loading", "empty", "error", "no-access"];

const ROLES = { loading: "status", error: "alert" };

function blank(value) {
	return value === undefined || value === null || value === "";
}

function default_title(kind) {
	if (kind === "loading") return __("Loading…");
	if (kind === "error") return __("Something went wrong.");
	if (kind === "no-access") return __("You do not have access to this.");
	return __("Nothing here yet.");
}

export function state_kind(kind) {
	return STATE_KINDS.includes(kind) ? kind : "empty";
}

export function state_html(kind, opts) {
	opts = opts || {};
	kind = state_kind(kind);
	const role = ROLES[kind] ? ` role="${ROLES[kind]}"` : "";
	const live = kind === "loading" ? ' aria-live="polite"' : "";
	const title = blank(opts.title) ? default_title(kind) : opts.title;
	const parts = [];
	if (kind === "loading") parts.push('<span class="sf-state-spinner" aria-hidden="true"></span>');
	if (kind === "error") parts.push('<span class="sf-state-mark" aria-hidden="true">!</span>');
	parts.push(`<p class="sf-state-title">${esc(title)}</p>`);
	if (!blank(opts.body)) parts.push(`<p class="sf-state-body">${esc(opts.body)}</p>`);
	if (!blank(opts.action_label) || typeof opts.on_action === "function") {
		const label = blank(opts.action_label) ? __("Try again") : opts.action_label;
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
