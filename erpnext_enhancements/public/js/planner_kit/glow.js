/*
 * Planner kit: hover glow. Hovering a person's name lights up every booking of theirs on screen;
 * leaving it clears them. The same for a project's name and its cards.
 *
 *   const off = planner_kit.hover_glow(container, "person");   // or "project", or options:
 *   planner_kit.hover_glow(container, { source: "data-pk-person", carriers: "data-pk-persons" });
 *   off.destroy();
 *
 * A name carries its key in `data-pk-<kind>` and a booking the keys of everyone on it in
 * `data-pk-<kind>s` (space-separated, each written with `planner_kit.glow_key`, so a key never holds
 * a space or a quote). Hovering adds `pk-glow` to every element in `scope` (the whole document by
 * default, so a drawer's rows light up too) whose list holds the key, and to the names themselves.
 * The class is deliberately generic: Phase 6C's click-to-highlight uses the same one.
 */

export const GLOW_CLASS = "pk-glow";

export function create_glow(env) {
	const doc = env.doc;

	return function hover_glow(container, options) {
		const opts = typeof options === "string" ? { kind: options } : options || {};
		const kind = opts.kind || "person";
		const source = opts.source || `data-pk-${kind}`;
		const carriers = opts.carriers || `data-pk-${kind}s`;
		const cls = opts.cls || GLOW_CLASS;
		const scope = opts.scope || doc;
		let active = null;

		const clear = () => {
			active = null;
			Array.from(scope.querySelectorAll(`.${cls}[data-pk-glow-by="${kind}"]`)).forEach((el) => {
				el.classList.remove(cls);
				el.removeAttribute("data-pk-glow-by");
			});
		};
		const mark = (key) => {
			// Keys are written encoded; anything else is not one of ours.
			if (!key || /["\\\s]/.test(key)) return;
			active = key;
			Array.from(scope.querySelectorAll(`[${carriers}~="${key}"], [${source}="${key}"]`)).forEach((el) => {
				el.classList.add(cls);
				el.setAttribute("data-pk-glow-by", kind);
			});
		};
		const over = (e) => {
			const el = e.target && e.target.closest ? e.target.closest(`[${source}]`) : null;
			if (!el || !container.contains(el)) return;
			const key = el.getAttribute(source);
			if (key === active) return;
			clear();
			mark(key);
		};
		const out = (e) => {
			const el = e.target && e.target.closest ? e.target.closest(`[${source}]`) : null;
			if (!el) return;
			if (e.relatedTarget && el.contains(e.relatedTarget)) return;
			clear();
		};
		container.addEventListener("pointerover", over);
		container.addEventListener("pointerout", out);
		return {
			clear,
			destroy() {
				clear();
				container.removeEventListener("pointerover", over);
				container.removeEventListener("pointerout", out);
			},
		};
	};
}
