/*
 * sf_kit: a small notice along the bottom with at most one action, e.g. "Visit saved · Undo".
 * One at a time: a new toast replaces the one showing. It goes after `timeout` ms (8 s by
 * default: long enough to reach Undo), stays while the pointer is over it or focus is inside it,
 * and is announced politely to a screen reader.
 *
 *   const toast = create_toast(env);              // sf-toast, sf-toast-text, ...
 *   toast("Visit saved", { action_label: "Undo", on_action: () => ..., timeout: 8000,
 *                          tone: "success" | "warning" | "info" });
 *   toast.close();  toast.current();              // this toast's element showing, or null
 *
 * Moved here from planner_kit/toast.js, which now calls this with { prefix: "pk" } and must keep
 * producing exactly the markup it always did (scripts/test_sf_kit.mjs compares it byte for byte),
 * so the planners' pk-toast CSS keeps matching. Change the behaviour here and both change.
 *
 * ONE PER DOCUMENT, across every instance (v1.632.0). The toast is position: fixed at the bottom
 * centre, so two from different instances (the page's sf toast, a planner's pk toast, a block's
 * toast in its shadow root) would sit on top of each other. The "current toast" is therefore kept
 * on the document itself, under Symbol.for("sf_kit.toast.current"), not in this module: the
 * planner and sf_kit bundles each carry their own copy of this file, and a Symbol.for key is the
 * same in both. A new toast from any of them closes the one showing. Per document rather than per
 * container because a shadow root does not change where a fixed element lands.
 *
 * FOCUS. Focus inside the toast (Tab to Undo) holds it like the pointer does: focusin pauses the
 * timer, focusout to somewhere outside re-arms it. When a toast that holds focus closes (timer,
 * Dismiss, its action, or a newer toast), focus goes back to the element that had it before focus
 * entered the toast, if that element is still in the page; otherwise focus would drop to <body>
 * and a keyboard user would start again from the top. "Had it before" is the focusin's
 * relatedTarget: when that is null (the person clicked the page background, then the toast),
 * nothing had focus, so nothing gets it back; jumping to an input they left a while ago would be
 * worse than staying put. A target that is no longer rendered (display: none, so
 * getClientRects() is empty) is skipped, and a focus() that does not take is left alone: either
 * way focus stays where the browser put it, without an error.
 *
 * `env` is { doc, set_timeout, clear_timeout } (a `t` in it is no longer read: the kit's one word
 * here is a literal __() call). `container` (default doc.body) is where the toast goes; a Custom
 * HTML Block passes its shadow root, so the toast sits inside the stylesheet the block adopted (a
 * stylesheet does not cross the shadow boundary; the --sf-* tokens do).
 */

import { __ } from "./t.js";

const TONES = ["success", "warning", "info"];

const CURRENT = typeof Symbol === "function" && Symbol.for ? Symbol.for("sf_kit.toast.current") : "__sf_kit_toast_current";

export function create_toast(env, options) {
	const doc = env.doc;
	const prefix = (options && options.prefix) || "sf";
	const container = options && options.container;

	function active_element() {
		// A shadow root knows its own focused element; the document only sees the host.
		const from_root = container && container.activeElement;
		return from_root || (doc && doc.activeElement) || null;
	}

	function holds_focus(item) {
		if (item.focused) return true;
		const active = active_element();
		return Boolean(active && typeof item.el.contains === "function" && item.el.contains(active));
	}

	// True when focus landed on `target`. False, quietly, when it was not there to take it.
	function give_focus_back(target) {
		if (!target || target.isConnected === false || typeof target.focus !== "function") return false;
		if (target === doc.body) return false;
		if (typeof target.getClientRects === "function" && target.getClientRects().length === 0) return false;
		try {
			target.focus();
		} catch (e) {
			return false;
		}
		const root = typeof target.getRootNode === "function" ? target.getRootNode() : doc;
		return Boolean(root && root.activeElement === target);
	}

	function close_item(item) {
		if (!item || item.closed) return;
		const had_focus = holds_focus(item);
		item.closed = true;
		env.clear_timeout(item.timer);
		item.timer = null;
		if (doc[CURRENT] === item.entry) doc[CURRENT] = null;
		if (item.el.parentNode) item.el.parentNode.removeChild(item.el);
		if (had_focus) give_focus_back(item.return_to);
	}

	function toast(message, opts) {
		opts = opts || {};
		const previous = doc[CURRENT];
		if (previous && typeof previous.close === "function") previous.close();
		const el = doc.createElement("div");
		el.className = `${prefix}-toast ${prefix}-toast-${TONES.includes(opts.tone) ? opts.tone : "info"}`;
		el.setAttribute("role", "status");
		el.setAttribute("aria-live", "polite");
		const text = doc.createElement("span");
		text.className = `${prefix}-toast-text`;
		text.textContent = message == null ? "" : String(message);
		el.appendChild(text);
		const item = {
			el,
			timer: null,
			closed: false,
			pointer: false,
			focused: false,
			return_to: active_element(),
			entry: null,
		};
		item.entry = { el, owner: toast, close: () => close_item(item) };
		if (opts.action_label && typeof opts.on_action === "function") {
			const action = doc.createElement("button");
			action.type = "button";
			action.className = `${prefix}-toast-action`;
			action.textContent = opts.action_label;
			action.addEventListener("click", () => {
				close_item(item);
				opts.on_action();
			});
			el.appendChild(action);
		}
		const dismiss = doc.createElement("button");
		dismiss.type = "button";
		dismiss.className = `${prefix}-toast-close`;
		dismiss.setAttribute("aria-label", __("Dismiss"));
		dismiss.innerHTML = "&times;";
		dismiss.addEventListener("click", () => close_item(item));
		el.appendChild(dismiss);
		const timeout = Number(opts.timeout) > 0 ? Number(opts.timeout) : 8000;
		const arm = () => {
			env.clear_timeout(item.timer);
			item.timer = env.set_timeout(() => close_item(item), timeout);
		};
		const pause = () => {
			env.clear_timeout(item.timer);
			item.timer = null;
		};
		const resume = () => {
			if (!item.closed && !item.pointer && !item.focused) arm();
		};
		el.addEventListener("pointerenter", () => {
			item.pointer = true;
			pause();
		});
		el.addEventListener("pointerleave", () => {
			item.pointer = false;
			resume();
		});
		el.addEventListener("focusin", (e) => {
			if (item.closed) return;
			if (!item.focused) {
				const from = e && e.relatedTarget;
				const outside = from && !(typeof el.contains === "function" && el.contains(from));
				item.return_to = outside ? from : null;
				item.focused = true;
			}
			pause();
		});
		el.addEventListener("focusout", (e) => {
			const to = e && e.relatedTarget;
			if (to && typeof el.contains === "function" && el.contains(to)) return;
			item.focused = false;
			resume();
		});
		(container || doc.body).appendChild(el);
		doc[CURRENT] = item.entry;
		arm();
		return {
			el,
			close: () => close_item(item),
		};
	}

	toast.close = () => {
		const entry = doc[CURRENT];
		if (entry && entry.owner === toast) entry.close();
	};
	toast.current = () => {
		const entry = doc[CURRENT];
		return entry && entry.owner === toast ? entry.el : null;
	};
	return toast;
}
