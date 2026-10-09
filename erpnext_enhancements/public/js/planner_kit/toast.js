/*
 * Planner kit: a small notice along the bottom with at most one action, e.g. "Dig moved to Thu,
 * Oct 15 · Undo". One at a time: a new toast replaces the one showing. It goes after `timeout` ms
 * (8 s by default: long enough to reach Undo), stays while the pointer is over it, and is announced
 * politely to a screen reader.
 *
 *   const toast = planner_kit.toast("Dig moved to Thu, Oct 15", {
 *     action_label: "Undo", on_action: () => ..., timeout: 8000, tone: "success" | "warning" | "info",
 *   });
 *   toast.close();  planner_kit.toast.close();
 */

export function create_toast(env) {
	const doc = env.doc;
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
		el.className = `pk-toast pk-toast-${["success", "warning", "info"].includes(opts.tone) ? opts.tone : "info"}`;
		el.setAttribute("role", "status");
		el.setAttribute("aria-live", "polite");
		const text = doc.createElement("span");
		text.className = "pk-toast-text";
		text.textContent = message == null ? "" : String(message);
		el.appendChild(text);
		const item = { el, timer: null };
		if (opts.action_label && typeof opts.on_action === "function") {
			const action = doc.createElement("button");
			action.type = "button";
			action.className = "pk-toast-action";
			action.textContent = opts.action_label;
			action.addEventListener("click", () => {
				if (current === item) close_current();
				opts.on_action();
			});
			el.appendChild(action);
		}
		const dismiss = doc.createElement("button");
		dismiss.type = "button";
		dismiss.className = "pk-toast-close";
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
		doc.body.appendChild(el);
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
