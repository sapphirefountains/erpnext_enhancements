/**
 * Which Triton widget owns the button: the classic one, or the framed one (v1.802.0).
 *
 * Loaded via: erpnext_enhancements.bundle.js (global), straight after triton_widget.js.
 *
 * ON THE CLASSIC WIDGET THIS FILE DOES ONE THING: it reads `frappe.boot.triton_frame`, finds it
 * absent, and returns. That is everyone until *Triton Assistant Settings > Widget* is changed.
 * Nothing is fetched, nothing is wrapped and nothing is built; `triton_widget.js` runs exactly as
 * it did before this file existed.
 *
 * For a person on the framed widget (the server put `triton_frame` in their boot; see
 * `api/triton_frame.py`) it:
 *
 * 1. fetches the loader, `triton_frame.bundle.js` and its stylesheet, with its own two tags
 *    rather than `frappe.require`, which freezes the whole Desk while it loads and resolves the
 *    same way whether the file arrived or not;
 * 2. stands in for `SapphireTriton.ask()` until the loader is there, so a Desk button that opens
 *    Triton with a prompt works from the first moment;
 * 3. hands the button back to the classic widget, ONCE, when the framed one cannot work: the
 *    loader did not arrive, the frame never said "ready", it speaks a contract this loader does
 *    not know, it could not sign the person in, or Triton has no ticket route (the loader decides
 *    which; `docs/triton-framed-widget.md` lists them). `triton_widget.js` stays asleep while
 *    boot says "framed" and wakes when `SapphireTriton.startClassic()` is called. If the panel was
 *    open, the classic one opens in its place, and a prompt that was waiting is carried over.
 *    One line goes to the console and nothing is shown to the person: from where they sit,
 *    Triton opened.
 *
 * There is no way back to the framed widget after that until the page is reloaded. A panel that
 * swaps under a person twice is worse than one that is a version behind.
 *
 * `installGate(win)` takes its window as an argument so the whole file runs under node
 * (`scripts/test_triton_frame.mjs`); in a browser it installs itself on `window` below.
 */

export const LOADER_JS = "triton_frame.bundle.js";
export const LOADER_CSS = "triton_frame.bundle.css";
/** How long the two files have to arrive before the classic widget takes over. */
export const LOAD_TIMEOUT_MS = 10000;

export function installGate(win) {
	const doc = win.document;
	let armed = false;
	let handedBack = false;
	let loader = null;
	let classicAsk = null;

	function frameConfig() {
		const frappe = win.frappe;
		return (frappe && frappe.boot && frappe.boot.triton_frame) || null;
	}

	function handBack(reason, detail) {
		if (handedBack) return;
		handedBack = true;
		const dying = loader;
		loader = null;
		if (dying && typeof dying.destroy === "function") dying.destroy();

		const triton = win.SapphireTriton || {};
		if (classicAsk) triton.ask = classicAsk;
		if (win.console && typeof win.console.info === "function") {
			// eslint-disable-next-line no-console
			win.console.info("[Triton] The framed assistant is not available (" + reason + "). Using the classic assistant.");
		}
		const start = typeof triton.startClassic === "function" ? triton.startClassic() : null;
		Promise.resolve(start)
			.catch(() => null)
			.then(() => {
				if (!classicAsk) return;
				const ask = detail && detail.ask;
				if (ask) classicAsk(ask.prompt, ask.context);
				else if (detail && detail.open) classicAsk();
			});
	}

	/** One tag, settled once: true when the file loaded, false when it did not. */
	function fetchTag(node) {
		return new Promise((resolve) => {
			node.onload = () => resolve(true);
			node.onerror = () => resolve(false);
			doc.head.appendChild(node);
		});
	}

	function assetUrl(name) {
		const assets = win.frappe && win.frappe.assets;
		const url = assets && typeof assets.bundled_asset === "function" ? assets.bundled_asset(name) : "";
		// An unknown bundle comes back as the bare name: this Desk was built before the loader.
		return typeof url === "string" && url.indexOf("/assets/") === 0 ? url : "";
	}

	function fetchLoader() {
		const js = assetUrl(LOADER_JS);
		const css = assetUrl(LOADER_CSS);
		if (!js || !css) return Promise.resolve(false);

		const link = doc.createElement("link");
		link.rel = "stylesheet";
		link.href = css;
		const script = doc.createElement("script");
		script.src = js;

		const arrived = Promise.all([fetchTag(link), fetchTag(script)]).then((ok) => ok[0] && ok[1]);
		const tooLate = new Promise((resolve) => win.setTimeout(() => resolve(false), LOAD_TIMEOUT_MS));
		return Promise.race([arrived, tooLate]);
	}

	function arm() {
		if (armed) return;
		const config = frameConfig();
		if (!config) return; // the classic widget: nothing below this line runs
		const session = win.frappe.session;
		if (!session || !session.user || session.user === "Guest") return;
		armed = true;

		win.SapphireTriton = win.SapphireTriton || {};
		classicAsk = win.SapphireTriton.ask || null;

		const started = fetchLoader().then((arrived) => {
			if (handedBack) return null;
			const entry = win.SapphireTritonFrame;
			if (!arrived || !entry || typeof entry.start !== "function") {
				handBack("loader");
				return null;
			}
			try {
				loader = entry.start({ config: config, giveUp: handBack });
			} catch (e) {
				loader = null;
				handBack("loader");
			}
			return loader;
		});

		win.SapphireTriton.ask = function (prompt, context) {
			if (handedBack) return classicAsk ? classicAsk(prompt, context) : Promise.resolve(false);
			return started.then((ready) => {
				if (ready && !handedBack) return ready.ask(prompt, context);
				return classicAsk ? classicAsk(prompt, context) : false;
			});
		};
	}

	// Boot is in the page before any bundle runs, so this normally arms at once. The two
	// fallbacks are the classic widget's own: `app_ready`, and a timer in case that already fired.
	arm();
	if (typeof win.$ === "function") {
		win.$(doc).on("app_ready", arm);
		win.$(() => win.setTimeout(arm, 1500));
	}

	return { arm: arm, handBack: handBack, isHandedBack: () => handedBack };
}

if (typeof window !== "undefined") installGate(window);
