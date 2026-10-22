/*
 * Planner kit: the first-visit tour, a few short steps in one small dialog (P12 of the planner UX
 * redesign, docs/planner-ux-redesign.md).
 *
 *   const handle = planner_kit.tour.open({
 *     title: "Welcome to the Project Planner",         // the dialog's name for a screen reader
 *     steps: [{ title: "Five tabs", text: "Lookahead is your home ...", keys: "Esc skips" }],
 *     owner: "pp",
 *     return_focus: element,                            // optional: where focus goes when it closes
 *     on_close: (how, index) => ...,                    // "done" | "skip" | "esc" | "outside" | "route" | "replaced"
 *   });
 *   handle.next() / back() / go(index) / close(how) / is_open() / index() / element()
 *   planner_kit.tour.current()  ·  .is_open()  ·  .close(how)
 *
 * What it promises, because the people it is for have never seen the planner:
 *
 *   - Brief. A step is a heading and a sentence or two, set as text (a record's words can never become
 *     markup here: there are none). At most MAX_STEPS steps, so a tour cannot grow into a manual.
 *   - Skippable at every step: Esc, the Skip button, or a press outside the card. Back and the arrow
 *     keys step through it; the last step's button says Done.
 *   - Keyboard-complete and named for a screen reader: a modal dialog labelled by the step's heading and
 *     described by its text, the count ("Step 2 of 5") in a polite live region, focus on the Next
 *     button when it opens and trapped inside until it closes, then back where it was.
 *   - It never touches the browser's history. The kit's drawers and sheet take one history entry so Back
 *     can close them; the tour does not join that guard, so opening, stepping through and closing it
 *     push, replace and step off nothing, and Back and Forward mean what they meant before it showed.
 *     The page closes it ("route") when its own route changes or the page hides.
 *   - It owns the keyboard while it is open: every key pressed inside the card stops there, so the
 *     planner's shortcuts and Frappe's never act on the page behind it. (The pages' own capture-phase
 *     shortcut handlers run first and are told it is open: kit.tour.is_open().)
 *
 * The rules are planner_kit.bundle.css (.pk-tour-*). `clean_steps` and `count_text` are pure, so
 * scripts/test_planner_tour.js runs this file under node on a small fake DOM.
 */

export const MAX_STEPS = 8;
const MAX_TITLE = 80;
const MAX_TEXT = 320;
const MAX_KEYS = 80;

const text_of = (value, limit) => {
	if (typeof value !== "string") return "";
	return value.replace(/\s+/g, " ").trim().slice(0, limit);
};

// Steps as the dialog takes them: each {title, text, keys?} with a heading and a sentence, strings only,
// at most MAX_STEPS. Anything else (a step with no words, a number, null) is dropped, never thrown at.
export function clean_steps(steps) {
	const out = [];
	(Array.isArray(steps) ? steps : []).forEach((step) => {
		if (!step || typeof step !== "object" || out.length >= MAX_STEPS) return;
		const title = text_of(step.title, MAX_TITLE);
		const text = text_of(step.text, MAX_TEXT);
		if (!title || !text) return;
		out.push({ title, text, keys: text_of(step.keys, MAX_KEYS) });
	});
	return out;
}

// "Step 2 of 5" (index is 0-based).
export function count_text(index, total, t) {
	const say = typeof t === "function" ? t : (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] != null ? args[i] : m));
	return say("Step {0} of {1}", [index + 1, total]);
}

export function create_tour(env) {
	const doc = env.doc;
	const t = env.t || ((text) => text);
	let current = null;

	function make(tag, cls, text) {
		const el = doc.createElement(tag);
		if (cls) el.className = cls;
		if (text !== undefined) el.textContent = text;
		return el;
	}

	function button(label, cls, on_click) {
		const el = make("button", cls, label);
		el.type = "button";
		el.addEventListener("click", on_click);
		return el;
	}

	function visible_buttons(card) {
		return Array.from(card.querySelectorAll("button")).filter((el) => !el.disabled && !el.hidden);
	}

	function finish(how) {
		const handle = current;
		if (!handle) return;
		current = null;
		doc.removeEventListener("keydown", handle.on_key, true);
		if (handle.overlay.parentNode) handle.overlay.parentNode.removeChild(handle.overlay);
		const back = handle.previous;
		if (back && how !== "route" && typeof back.focus === "function") {
			try {
				back.focus();
			} catch (e) {
				// Courtesy: the page may have redrawn the control that had focus.
			}
		}
		if (typeof handle.opts.on_close === "function") {
			try {
				handle.opts.on_close(how, handle.at);
			} catch (e) {
				if (env.warn) env.warn(e);
			}
		}
	}

	function open(opts) {
		opts = opts || {};
		const steps = clean_steps(opts.steps);
		if (!steps.length) return null;
		if (current) finish("replaced");

		const overlay = make("div", "pk-tour");
		overlay.setAttribute("data-owner", opts.owner || "");
		const card = make("div", "pk-tour-card");
		card.setAttribute("role", "dialog");
		card.setAttribute("aria-modal", "true");
		card.setAttribute("aria-labelledby", "pk-tour-title");
		card.setAttribute("aria-describedby", "pk-tour-text");
		if (opts.title) card.setAttribute("aria-label", text_of(opts.title, MAX_TITLE));
		card.setAttribute("tabindex", "-1");

		// One live region holds the count, the heading and the sentence, so a screen reader hears the new
		// step when Next is pressed without focus having to leave the button.
		const live = make("div", "pk-tour-step");
		live.setAttribute("aria-live", "polite");
		const count = make("p", "pk-tour-count");
		const heading = make("h2", "pk-tour-title");
		heading.id = "pk-tour-title";
		const body = make("p", "pk-tour-text");
		body.id = "pk-tour-text";
		const keys = make("p", "pk-tour-keys");
		live.appendChild(count);
		live.appendChild(heading);
		live.appendChild(body);
		live.appendChild(keys);
		const dots = make("div", "pk-tour-dots");
		dots.setAttribute("aria-hidden", "true");
		steps.forEach(() => dots.appendChild(make("i", "pk-tour-dot")));

		const actions = make("div", "pk-tour-actions");
		const skip = button(t("Skip tour"), "btn btn-default btn-sm pk-tour-skip", () => finish("skip"));
		skip.setAttribute("aria-label", t("Skip the tour"));
		const spacer = make("span", "pk-tour-spacer");
		const back = button(t("Back"), "btn btn-default btn-sm pk-tour-back", () => step_to(handle.at - 1));
		const next = button(t("Next"), "btn btn-primary btn-sm pk-tour-next", () => {
			if (handle.at >= steps.length - 1) finish("done");
			else step_to(handle.at + 1);
		});
		[skip, spacer, back, next].forEach((el) => actions.appendChild(el));

		[live, dots, actions].forEach((el) => card.appendChild(el));
		overlay.appendChild(card);

		function step_to(index) {
			if (current !== handle) return;
			const at = Math.max(0, Math.min(steps.length - 1, Number(index) || 0));
			handle.at = at;
			const step = steps[at];
			count.textContent = count_text(at, steps.length, t);
			heading.textContent = step.title;
			body.textContent = step.text;
			keys.textContent = step.keys;
			keys.hidden = !step.keys;
			Array.from(dots.childNodes).forEach((dot, i) => {
				dot.className = i === at ? "pk-tour-dot pk-tour-dot-on" : "pk-tour-dot";
			});
			back.disabled = at === 0;
			next.textContent = at === steps.length - 1 ? t("Done") : t("Next");
			next.setAttribute("aria-label", at === steps.length - 1 ? t("Done, close the tour") : t("Next step"));
			back.setAttribute("aria-label", t("Previous step"));
		}

		// A tap on the dimmed page, outside the card, skips. It closes on the click, and only when the press
		// began outside the card too: a drag that starts in the card (selecting a word) and ends outside it
		// must not close it, and closing on the press itself would let the tap's own click fall through to
		// whatever the page has under the finger once the dialog has gone.
		let pressed_outside = false;
		overlay.addEventListener("pointerdown", (e) => {
			pressed_outside = e.target === overlay;
		});
		overlay.addEventListener("click", (e) => {
			const outside = pressed_outside && e.target === overlay;
			pressed_outside = false;
			if (outside) finish("outside");
		});
		// Whatever is pressed inside the card stays inside it: the page's and Frappe's shortcuts are not for
		// the page behind a dialog. (Default actions are untouched: Enter and Space still press a button.)
		overlay.addEventListener("keydown", (e) => e.stopPropagation());

		const on_key = (e) => {
			if (current !== handle) return;
			const key = e.key;
			if (key === "Escape" || key === "Esc") {
				e.preventDefault();
				e.stopPropagation();
				finish("esc");
				return;
			}
			if (key === "Tab") {
				const items = visible_buttons(card);
				if (!items.length) return;
				const first = items[0];
				const last = items[items.length - 1];
				const active = doc.activeElement;
				if (!card.contains(active)) {
					e.preventDefault();
					(e.shiftKey ? last : first).focus();
				} else if (e.shiftKey && active === first) {
					e.preventDefault();
					last.focus();
				} else if (!e.shiftKey && active === last) {
					e.preventDefault();
					first.focus();
				}
				return;
			}
			if ((key === "ArrowRight" || key === "ArrowLeft") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				e.stopPropagation();
				if (key === "ArrowRight") {
					if (handle.at < steps.length - 1) step_to(handle.at + 1);
				} else step_to(handle.at - 1);
			}
		};

		const handle = {
			opts,
			steps,
			overlay,
			card,
			at: 0,
			// Where focus goes back to: the control that started it (the help's button), else where it was.
			previous: opts.return_focus || doc.activeElement || null,
			on_key,
			owner: opts.owner || null,
			is_open: () => current === handle,
			index: () => handle.at,
			element: () => overlay,
			next: () => step_to(handle.at + 1),
			back: () => step_to(handle.at - 1),
			go: (index) => step_to(index),
			close: (how) => {
				if (current === handle) finish(how || "api");
			},
		};
		current = handle;
		step_to(Math.max(0, Math.min(steps.length - 1, Number(opts.start) || 0)));
		doc.body.appendChild(overlay);
		// Capture on the document: Esc and Tab are decided here before anything behind the card sees them.
		doc.addEventListener("keydown", on_key, true);
		next.focus();
		return handle;
	}

	return {
		open,
		current: () => current,
		is_open: () => !!current,
		close: (how) => {
			if (current) finish(how || "api");
		},
	};
}
