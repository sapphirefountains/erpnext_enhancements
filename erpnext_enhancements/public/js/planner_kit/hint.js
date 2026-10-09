/*
 * Planner kit: first-time hints ("Click a name to see their week"), each dismissed once and then
 * never shown again to that person on that browser.
 *
 *   planner_kit.hint("person-peek", "Click a name to see their week.", { container: element });
 *   planner_kit.hint.reset("person-peek");   // or reset() for all of them
 *
 * Remembered in localStorage, per user (`pk_hint:<user>:<key>`), with every access in try/catch: it is
 * a per-viewer convenience, so a private window, blocked storage or a preview simply shows the hint
 * again and nothing breaks. The text is set as text.
 */

const PREFIX = "pk_hint:";

export function create_hint(env) {
	const doc = env.doc;

	function storage() {
		try {
			return env.win.localStorage || null;
		} catch (e) {
			return null;
		}
	}

	function key_of(key) {
		return `${PREFIX}${env.user() || "guest"}:${key}`;
	}

	function seen(key) {
		try {
			const store = storage();
			return !!(store && store.getItem(key_of(key)) === "1");
		} catch (e) {
			return false;
		}
	}

	function remember(key) {
		try {
			const store = storage();
			if (store) store.setItem(key_of(key), "1");
		} catch (e) {
			// A convenience: shown again next time.
		}
	}

	function hint(key, text, opts) {
		opts = opts || {};
		const container = opts.container;
		if (!key || !text || !container || seen(key)) return null;
		const existing = Array.from(container.querySelectorAll(".pk-hint")).find(
			(el) => el.getAttribute("data-pk-hint") === String(key)
		);
		if (existing) return existing;
		const el = doc.createElement("div");
		el.className = "pk-hint";
		el.setAttribute("role", "note");
		el.setAttribute("data-pk-hint", String(key));
		const label = doc.createElement("b");
		label.textContent = env.t("Tip");
		const body = doc.createElement("span");
		body.textContent = text;
		const button = doc.createElement("button");
		button.type = "button";
		button.className = "pk-hint-close";
		button.textContent = env.t("Got it");
		button.addEventListener("click", () => {
			remember(key);
			if (el.parentNode) el.parentNode.removeChild(el);
		});
		el.appendChild(label);
		el.appendChild(body);
		el.appendChild(button);
		container.appendChild(el);
		return el;
	}

	hint.seen = seen;
	hint.reset = (key) => {
		try {
			const store = storage();
			if (!store) return;
			if (key) {
				store.removeItem(key_of(key));
				return;
			}
			const mine = `${PREFIX}${env.user() || "guest"}:`;
			const keys = [];
			for (let i = 0; i < store.length; i++) {
				const name = store.key(i);
				if (name && name.startsWith(mine)) keys.push(name);
			}
			keys.forEach((name) => store.removeItem(name));
		} catch (e) {
			// Nothing remembered to forget.
		}
	};
	return hint;
}
