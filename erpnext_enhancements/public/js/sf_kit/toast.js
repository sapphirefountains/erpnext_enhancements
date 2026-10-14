/*
 * sf_kit: a small notice along the bottom with at most one action, e.g. "Visit saved · Undo".
 * One at a time: a new toast replaces the one showing. It goes after `timeout` ms (8 s by
 * default: long enough to reach Undo), stays while the pointer is over it, and is announced
 * politely to a screen reader.
 *
 *   const toast = create_toast(env);              // sf-toast, sf-toast-text, ...
 *   toast("Visit saved", { action_label: "Undo", on_action: () => ..., timeout: 8000,
 *                          tone: "success" | "warning" | "info" });
 *   toast.close();  toast.current();              // the element showing, or null
 *
 * Moved here from planner_kit/toast.js, which now calls this with { prefix: "pk" } and must keep
 * producing exactly the markup it always did (scripts/test_sf_kit.mjs compares it byte for byte),
 * so the planners' pk-toast CSS keeps matching. Change the behaviour here and both change.
 *
 * `env` is { doc, t, set_timeout, clear_timeout }. `container` (default doc.body) is where the
 * toast goes; a Custom HTML Block passes its shadow root, so the toast sits inside the stylesheet
 * the block adopted (a stylesheet does not cross the shadow boundary; the --sf-* tokens do).
 */

const TONES = ["success", "warning", "info"];

export function create_toast(env, options) {
	const doc = env.doc;
	const prefix = (options && options.prefix) || "sf";
	const container = options && options.container;
	let current = null;

	function close_current() {
		if (!current) return;
		const item = current;
		current = null;
		env.clear_timeout(item.timer);
		if (item.el.parentNode) item.el.parentNode.removeChild(item.el);
	}

	function toast(message, opts) {
		opts = opts || {};
		close_current();
		const el = doc.createElement("div");
		el.className = `${prefix}-toast ${prefix}-toast-${TONES.includes(opts.tone) ? opts.tone : "info"}`;
		el.setAttribute("role", "status");
		el.setAttribute("aria-live", "polite");
		const text = doc.createElement("span");
		text.className = `${prefix}-toast-text`;
		text.textContent = message == null ? "" : String(message);
		el.appendChild(text);
		const item = { el, timer: null };
		if (opts.action_label && typeof opts.on_action === "function") {
			const action = doc.createElement("button");
			action.type = "button";
			action.className = `${prefix}-toast-action`;
			action.textContent = opts.action_label;
			action.addEventListener("click", () => {
				if (current === item) close_current();
				opts.on_action();
			});
			el.appendChild(action);
		}
		const dismiss = doc.createElement("button");
		dismiss.type = "button";
		dismiss.className = `${prefix}-toast-close`;
		dismiss.setAttribute("aria-label", env.t("Dismiss"));
		dismiss.innerHTML = "&times;";
		dismiss.addEventListener("click", () => {
			if (current === item) close_current();
		});
		el.appendChild(dismiss);
		const timeout = Number(opts.timeout) > 0 ? Number(opts.timeout) : 8000;
		const arm = () => {
			env.clear_timeout(item.timer);
			item.timer = env.set_timeout(() => {
				if (current === item) close_current();
			}, timeout);
		};
		el.addEventListener("pointerenter", () => env.clear_timeout(item.timer));
		el.addEventListener("pointerleave", arm);
		(container || doc.body).appendChild(el);
		current = item;
		arm();
		return {
			el,
			close: () => {
				if (current === item) close_current();
			},
		};
	}

	toast.close = close_current;
	toast.current = () => (current ? current.el : null);
	return toast;
}
