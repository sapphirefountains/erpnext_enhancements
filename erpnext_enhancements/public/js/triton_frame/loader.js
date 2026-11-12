/**
 * The framed Triton widget's loader: a button, a panel and ONE frame (v1.802.0).
 *
 * The classic widget (`global_enhancements/triton_widget.js`) is a whole chat program in the
 * Desk. This is what replaces it for a person on the framed widget: the same round button and the
 * same panel, with Triton's own compact chat page (`/embed.html`, served by Triton) in a frame
 * where the transcript used to be. The Desk keeps only what the Desk alone can do: the button,
 * the panel and its size, Alt+T, `SapphireTriton.ask()`, telling the frame which page is open,
 * opening a Desk page the frame asks for, the clock-in card, and getting the frame signed in.
 *
 * Loaded only for a person on the framed widget, by `global_enhancements/triton_frame_gate.js`,
 * as its own bundle (`triton_frame.bundle.js`). Everyone else never downloads it.
 *
 * The messages are Triton's contract (`docs/embed-bridge.md` in the Triton repository, version
 * 1); `contract.js` holds its words and `docs/triton-framed-widget.md` says what this file does
 * with each. Inside the frame everything is Triton's: its header, its Close, its sign-in
 * messages, its "Try again". This file does not draw over any of it.
 *
 * THE RULES THIS FILE EXISTS TO KEEP
 *
 * 1. The frame's address is assigned exactly once, before the frame joins the page, and never
 *    again. A frame whose address changes (or that is moved to another parent, which reloads it)
 *    adds entries to the tab's Back and Forward list, and then Back stops moving the Desk. The
 *    proof measured it: with the address set once, three messages across two chats and four chat
 *    switches added no history entry at all. Nothing here writes `src` a second time, reads
 *    `contentWindow.location`, or re-parents the frame. Chat-to-chat movement happens inside the
 *    frame, in memory.
 * 2. The frame stays alive while the person moves around the Desk. It hangs off `document.body`,
 *    which Desk route changes never touch, and closing the panel only hides it. Opening the panel
 *    again costs nothing, spends no ticket, and the chat is where it was left.
 * 3. A person is never left with a dead panel. The classic widget is started in this one's
 *    place, once, when the frame cannot work for this person on this page load:
 *      - no `ready` within READY_TIMEOUT_MS of creating the frame (it did not load: blocked,
 *        offline, Triton down), or within SILENT_AFTER_LOAD_MS of the frame's `load` event (a
 *        page did load, and it is not Triton's chat);
 *      - `ready` in a contract version this loader does not speak;
 *      - `sign-in-failed`, or no word at all about sign-in within SIGN_IN_TIMEOUT_MS of `ready`,
 *        before the frame has ever signed in;
 *      - Triton has no ticket route.
 *    Once the frame has signed in, nothing takes the panel away: a later failure is the frame's
 *    to show, with its own "Try again", and the conversation is still on the server.
 *    Until `ready` the panel shows the classic header with a working Close, so a phone is never
 *    stuck under a blank full-screen sheet.
 * 4. Only Triton's configured origin is listened to, and only it is posted to (`bridge.js`), and
 *    nothing is said to the frame before it has said `ready`.
 * 5. The phone sheet is sized to the VISIBLE screen. A framed page cannot see the on-screen
 *    keyboard (inside a frame `visualViewport` is just the frame's own box), so it cannot move
 *    its message box out of the keyboard's way. This page can, so it shrinks the sheet and the
 *    frame's own layout does the rest.
 * 6. The ticket is posted and forgotten. It is not kept on `state`, not written to browser
 *    storage and not logged; this file has no `console` call at all.
 *
 * The button and the panel reuse the classic widget's classes (`triton-fab`, `triton-panel`,
 * `triton-visible`, `triton-fab-open`, `triton-header`), so position, the phone sheet, the lift
 * above the quick bar and above the Guided Entry footer, the open animation and reduced-motion
 * handling are the classic stylesheet's and cannot drift from it. `triton_frame.bundle.css` adds
 * only what a frame needs.
 *
 * Everything from the outside world arrives through `win`, so the whole file runs under node
 * against a stand-in (`scripts/test_triton_frame.mjs`).
 */

import { createBridge } from "./bridge.js";
import { createClockIn } from "./clock_in.js";
import { CONTRACT_VERSION, FROM_FRAME, TO_FRAME } from "./contract.js";
import { createPageSource } from "./page_source.js";
import { deskRoute } from "./record_link.js";

/** How long the frame has to say `ready`. A cold load on a weak phone connection takes many seconds. */
export const READY_TIMEOUT_MS = 20000;
/** A page that has finished loading and still says nothing is not Triton's chat. */
export const SILENT_AFTER_LOAD_MS = 8000;
/** After `ready`: the frame gives up waiting for a ticket at 15 s and says so, so silence past this is a dead frame. */
export const SIGN_IN_TIMEOUT_MS = 20000;
/** While the panel is open, how often the page reference is re-read (filters and unsaved edits have no event). */
export const PAGE_POLL_MS = 2000;
export const TICKET_METHOD = "erpnext_enhancements.api.triton_frame.get_ticket";
/** What the framed page may use. Without the attribute a cross-origin frame gets neither. */
export const FRAME_ALLOW = "clipboard-write; microphone";
/** A frame stuck asking to be signed in again must not hammer two servers. */
export const TICKETS_PER_MINUTE = 6;
export const MAX_PROMPT = 8000;
export const MAX_MESSAGE = 300;
/** On <html> while this loader owns the button. triton_frame.bundle.css scopes to it whatever it
 *  says about the classic widget's own classes, because that stylesheet stays loaded after a
 *  hand-back and the classic widget then wears the same classes. */
export const OWNER_CLASS = "triton-framed";
const PHONE_QUERY = "(max-width: 480px)";
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];
const SIGN_IN_REASONS = ["no-answer", "refused", "bad-ticket", "no-config"];

/** The boot config, checked again here: the loader posts sign-in tickets to this origin. */
export function validConfig(config) {
	if (!config || typeof config.origin !== "string" || typeof config.url !== "string") return false;
	let url;
	try {
		url = new URL(config.url);
	} catch (e) {
		return false;
	}
	if (url.origin !== config.origin || url.username || url.password) return false;
	if (url.protocol === "https:") return true;
	const local = LOCAL_HOSTS.includes(url.hostname) || url.hostname.endsWith(".localhost");
	return url.protocol === "http:" && local;
}

/**
 * @param {object}   opts
 * @param {Window}   opts.win     the Desk window (or a stand-in)
 * @param {object}   opts.config  frappe.boot.triton_frame: { origin, url, page_context }
 * @param {Function} opts.giveUp  (reason, { open, ask }) called at most once, after teardown
 * @param {Function} opts.detect  capture/page_context.js detectPageContext
 */
export function createLoader({ win, config, giveUp, detect }) {
	const doc = win.document;
	const frappe = win.frappe || {};
	// frappe's __() translates and fills {0}; off the Desk (the tests) only the filling is left.
	const t = (words, args) =>
		typeof win.__ === "function"
			? win.__(words, args)
			: String(words).replace(/\{(\d+)\}/g, (all, i) => (args && args[i] !== undefined ? args[i] : all));

	const state = {
		open: false,
		frame: null,
		ready: false, // the frame has said `ready` in a version we speak
		signedIn: false, // the frame has said `signed-in` at least once on this page load
		dead: false,
		readyTimer: null,
		loadTimer: null,
		signInTimer: null,
		pollTimer: null,
		ticketInFlight: null,
		ticketTimes: [],
		pendingAsk: null, // an ask() not yet posted to the frame
		heldAsk: null, // an ask() the frame is holding until it has signed in, which it may never do
	};
	const els = {};
	const listeners = [];

	function listen(target, type, fn) {
		if (!target || typeof target.addEventListener !== "function") return;
		target.addEventListener(type, fn);
		listeners.push([target, type, fn]);
	}

	function stop(name) {
		if (state[name]) win.clearTimeout(state[name]);
		state[name] = null;
	}

	const bridge = createBridge({
		win: win,
		origin: config.origin,
		frameWindow: () => (state.frame ? state.frame.contentWindow : null),
		onMessage: onMessage,
	});

	const pages = createPageSource({
		win: win,
		detect: detect,
		onChange: (ref) => {
			if (state.ready) bridge.post(TO_FRAME.PAGE, { page: ref });
		},
	});

	const clockIn = createClockIn({ win: win });

	// ---- the button and the panel -------------------------------------------
	function el(tag, className, text) {
		const node = doc.createElement(tag);
		if (className) node.className = className;
		if (text) node.textContent = text;
		return node;
	}

	function build() {
		const fab = el("button", "triton-fab", "🔱");
		fab.type = "button";
		fab.title = t("Ask Triton (Alt+T)");
		fab.setAttribute("aria-label", t("Ask Triton"));
		fab.setAttribute("aria-expanded", "false");
		listen(fab, "click", () => toggle());

		const panel = el("div", "triton-panel triton-frame-panel");
		panel.setAttribute("role", "dialog");
		panel.setAttribute("aria-label", t("Triton"));

		// What the panel shows until the frame says `ready`: the classic header (so it looks
		// like the same panel) with a Close that works, and one line of plain words. After
		// `ready` it is gone for good and everything in the panel is the frame's.
		const shell = el("div", "triton-frame-shell");
		const header = el("div", "triton-header");
		header.appendChild(el("span", "triton-logo", "🔱"));
		header.appendChild(el("span", "triton-title", t("Triton")));
		const close = el("button", "triton-icon-btn triton-close", "✕");
		close.type = "button";
		close.title = t("Close");
		close.setAttribute("aria-label", t("Close"));
		listen(close, "click", () => toggle(false));
		header.appendChild(close);
		shell.appendChild(header);
		const body = el("div", "triton-frame-status");
		body.setAttribute("role", "status");
		body.appendChild(el("p", "triton-frame-words", t("Connecting to Triton…")));
		shell.appendChild(body);
		panel.appendChild(shell);

		els.fab = fab;
		els.panel = panel;
		els.shell = shell;
		if (doc.documentElement) doc.documentElement.classList.add(OWNER_CLASS);
		doc.body.appendChild(fab);
		doc.body.appendChild(panel);

		listen(doc, "keydown", onKeydown);
		if (win.visualViewport) {
			listen(win.visualViewport, "resize", fitSheet);
			listen(win.visualViewport, "scroll", fitSheet);
		}
		if (config.page_context) pages.start();
	}

	function ensureFrame() {
		if (state.frame || state.dead) return;
		const frame = el("iframe", "triton-frame");
		frame.title = t("Triton chat");
		frame.setAttribute("allow", FRAME_ALLOW);
		frame.setAttribute("referrerpolicy", "origin");
		// Rule 1: assigned once, here, before the frame joins the page. Never again.
		frame.src = config.url;
		state.frame = frame;
		bridge.start();
		listen(frame, "load", onFrameLoad);
		els.panel.appendChild(frame);
		state.readyTimer = win.setTimeout(() => handBack("timeout"), READY_TIMEOUT_MS);
	}

	// A frame the browser refused (the wrong address in Triton's policy), an error page, or a
	// sign-in page still fires `load`. Triton's chat says `ready` within a moment of it: its
	// scripts have run by then and one small request is all that is left. So silence after
	// `load` is settled sooner than silence before it, where the page may still be arriving.
	function onFrameLoad() {
		if (state.dead || state.ready || state.loadTimer) return;
		state.loadTimer = win.setTimeout(() => handBack("silent"), SILENT_AFTER_LOAD_MS);
	}

	// ---- open and close -----------------------------------------------------
	function toggle(force) {
		if (state.dead) return;
		const open = typeof force === "boolean" ? force : !state.open;
		if (open === state.open) return;
		state.open = open;
		if (open) ensureFrame();
		els.panel.classList.toggle("triton-visible", open);
		els.fab.classList.toggle("triton-fab-open", open);
		els.fab.setAttribute("aria-expanded", String(open));
		if (open) {
			els.fab.classList.remove("triton-fab-unread");
			fitSheet();
			if (state.ready) {
				// The record may have been edited, or a list re-filtered, since the last report.
				if (config.page_context) pages.report(false);
				// The frame puts the cursor in its message box on this.
				bridge.post(TO_FRAME.VISIBILITY, { open: true });
			}
			focusFrame();
			watchPage();
		} else {
			stop("pollTimer");
			const hadFocus = doc.activeElement === state.frame;
			if (state.ready) bridge.post(TO_FRAME.VISIBILITY, { open: false });
			// Focus inside a hidden frame is focus nowhere: the next key press would go to a
			// page the person cannot see. Hand it back to the button they opened it with.
			if (hadFocus && typeof els.fab.focus === "function") els.fab.focus();
		}
	}

	// Only into a frame the person can see. A new ticket arrives every half hour whether the
	// panel is open or not, and focusing a hidden frame then would take the keyboard away from
	// whatever Desk field the person is typing in.
	function focusFrame() {
		if (!state.open || !state.ready || !state.frame) return;
		if (typeof state.frame.focus === "function") state.frame.focus();
	}

	// The Desk has no event for "this list's filters changed" or "this form now has unsaved
	// edits", and the contract wants the page sent again for both. So while the panel is open,
	// and only then, the reference is re-read every PAGE_POLL_MS; it is sent only when it differs
	// from the last one sent.
	function watchPage() {
		stop("pollTimer");
		if (!config.page_context || !state.open || state.dead) return;
		state.pollTimer = win.setTimeout(() => {
			state.pollTimer = null;
			if (state.ready) pages.report(false);
			watchPage();
		}, PAGE_POLL_MS);
	}

	// Alt+T, exactly as the classic widget reads it: AltGr is reported as ctrlKey+altKey on
	// Windows and on many European layouts, where AltGr+T types an ordinary character.
	function onKeydown(e) {
		if (e.ctrlKey || e.metaKey) return;
		if (e.altKey && (e.key === "t" || e.key === "T")) {
			e.preventDefault();
			toggle();
		}
	}

	// Rule 5. The classic stylesheet makes the panel a sheet under 480px; these two properties
	// are read only inside that media query (triton_frame.bundle.css), so setting them on a
	// desktop does nothing.
	function fitSheet() {
		const vv = win.visualViewport;
		if (!vv || !state.open || state.dead) return;
		els.panel.style.setProperty("--triton-sheet-height", vv.height + "px");
		els.panel.style.setProperty("--triton-sheet-top", vv.offsetTop + "px");
	}

	function onPhone() {
		try {
			return !!win.matchMedia && win.matchMedia(PHONE_QUERY).matches;
		} catch (e) {
			return false;
		}
	}

	// ---- signing the frame in -----------------------------------------------
	// A plain fetch, not frappe.call: v16's request.js shows its own dialog for a 403, a 502 and
	// most other failures whatever is passed to it, and this call runs without the person having
	// asked for anything. The frame says what went wrong, once, in the sentence it is given.
	async function requestTicket() {
		const failed = (reason, message) => ({ ok: false, reason: reason, message: message || "" });
		try {
			const response = await win.fetch("/api/method/" + TICKET_METHOD, {
				method: "POST",
				headers: {
					Accept: "application/json",
					"X-Frappe-CSRF-Token": frappe.csrf_token || "",
					"X-Requested-With": "XMLHttpRequest",
				},
				credentials: "same-origin",
				cache: "no-store",
				redirect: "error",
			});
			if (!response.ok) {
				return failed("desk", t("ERPNext did not answer. Reload the page, then try again."));
			}
			const body = await response.json();
			const message = body && body.message;
			return message && typeof message === "object" ? message : failed("unreachable");
		} catch (e) {
			return failed("unreachable");
		}
	}

	/** Answer `ready` or `need-ticket`: a ticket, or one plain sentence saying why not. */
	function sendTicket() {
		if (state.dead) return Promise.resolve();
		if (state.ticketInFlight) return state.ticketInFlight;

		const now = Date.now();
		state.ticketTimes = state.ticketTimes.filter((at) => now - at < 60000);
		if (state.ticketTimes.length >= TICKETS_PER_MINUTE) {
			ticketError(t("Triton keeps asking to sign in again. Wait a minute, then try again."));
			return Promise.resolve();
		}
		state.ticketTimes.push(now);

		state.ticketInFlight = requestTicket().then((result) => {
			state.ticketInFlight = null;
			if (state.dead) return;
			if (result.ok && typeof result.ticket === "string" && result.ticket) {
				// Rule 6: posted and forgotten.
				bridge.post(TO_FRAME.TICKET, { ticket: result.ticket });
				return;
			}
			// Triton has no ticket route, so this Triton cannot serve the framed widget at all.
			// Only before the first sign-in: a chat that is running is never swapped out.
			if (result.reason === "unsupported" && !state.signedIn) return handBack("no-ticket-route");
			ticketError(result.message);
		});
		return state.ticketInFlight;
	}

	// Said to the frame so it can tell the person at once, instead of waiting out its 15 seconds.
	// The Desk does not also cover the frame with words of its own.
	function ticketError(message) {
		const words = String(message || t("Triton could not be reached. Try again in a minute.")).slice(0, MAX_MESSAGE);
		bridge.post(TO_FRAME.TICKET_ERROR, { message: words });
	}

	// ---- messages from the frame --------------------------------------------
	function onMessage({ version, type, body }) {
		if (state.dead) return;
		if (type === FROM_FRAME.READY) {
			// Rule 3: a page speaking another contract cannot be driven by this loader. `ready`
			// is read whatever version its envelope carries, so that this is known at once.
			if (version !== CONTRACT_VERSION || body.version !== CONTRACT_VERSION) return handBack("contract");
			return onReady();
		}
		if (version !== CONTRACT_VERSION || !state.ready) return;

		switch (type) {
			case FROM_FRAME.NEED_TICKET:
				sendTicket();
				break;
			case FROM_FRAME.SIGNED_IN:
				state.signedIn = true;
				state.heldAsk = null; // the chat is on screen: the frame has shown the prompt
				stop("signInTimer");
				break;
			case FROM_FRAME.SIGN_IN_FAILED:
				// Before the first sign-in the framed chat has never worked for this person on
				// this page: the classic widget takes over. After it, the frame is showing the
				// reason and "Try again", and the conversation is still there behind them.
				if (!state.signedIn) {
					handBack("sign-in-failed, " + (SIGN_IN_REASONS.includes(body.reason) ? body.reason : "unknown"));
				}
				break;
			case FROM_FRAME.OPEN_RECORD:
				openRecord(body.path);
				break;
			case FROM_FRAME.RESIZE:
				if (body.mode === "wide" || body.mode === "panel") {
					els.panel.classList.toggle("triton-frame-wide", body.mode === "wide");
				}
				break;
			case FROM_FRAME.CLOSE:
				toggle(false);
				break;
			case FROM_FRAME.KEY:
				if (body.key === "alt+t") toggle();
				else if (body.key === "escape") toggle(false);
				break;
			case FROM_FRAME.REPLY_FINISHED:
				replyFinished(body);
				break;
			case FROM_FRAME.CLOCK_IN:
				// The card and nothing else. Only the person pressing Confirm clocks anyone in.
				clockIn.show(body.params);
				break;
			default:
				break; // a type this loader does not know: ignored, as the contract asks
		}
	}

	function onReady() {
		stop("readyTimer");
		stop("loadTimer");
		state.ready = true;
		// From here the panel is the frame's: its header, its sign-in state, its "Try again".
		els.shell.hidden = true;
		els.panel.classList.add("triton-frame-live");
		if (!state.signedIn && !state.signInTimer) {
			state.signInTimer = win.setTimeout(() => handBack("no-sign-in"), SIGN_IN_TIMEOUT_MS);
		}
		// `ready` comes again when the page in the frame reloads itself (a new Triton version
		// was deployed while the panel was open). The answer is the same each time, in the
		// contract's order: the ticket, then the page, then whether the panel is showing.
		sendTicket().then(() => {
			if (state.dead) return;
			if (config.page_context) pages.report(true);
			else bridge.post(TO_FRAME.PAGE, { page: null });
			bridge.post(TO_FRAME.VISIBILITY, { open: state.open });
			focusFrame();
			flushAsk();
		});
	}

	// A link in a reply to a page of this Desk. The frame did not navigate; it asks. The path is
	// checked (record_link.js) and then handed to the Desk's own router the way the Desk's own
	// link handler does it: the path and query to frappe.set_route, the fragment beside it. That
	// is one ordinary history entry, so Back returns to the page the person was on.
	function openRecord(path) {
		const target = deskRoute(path);
		if (!target) return;
		let routed = false;
		if (typeof frappe.set_route === "function") {
			try {
				if (target.hash) frappe.route_hash = target.hash;
				frappe.set_route(target.route);
				routed = true;
			} catch (e) {
				routed = false;
			}
		}
		// The Desk could not route it: a new tab, never the frame and never a reload of this page.
		if (!routed && typeof win.open === "function") win.open(target.route + target.hash, "_blank", "noopener");
		// On a phone the sheet covers the whole Desk; get out of the way of what was asked for.
		if (onPhone()) toggle(false);
	}

	// A framed page may not ask the browser for notification permission, so the frame never
	// notifies. The Desk does, modestly: a line in the Desk's own alert corner and a dot on the
	// button, and only when the person is not already looking at the reply. No browser
	// notification, so no permission prompt appears because a reply finished.
	function replyFinished(body) {
		if (state.open && !doc.hidden) return;
		els.fab.classList.add("triton-fab-unread");
		if (typeof frappe.show_alert !== "function") return;
		const title = typeof body.title === "string" ? body.title.replace(/\s+/g, " ").trim().slice(0, 200) : "";
		// show_alert renders its message as HTML, and a chat's title is text a person or a model wrote.
		const safe = title.replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
		frappe.show_alert({
			message: safe ? t("Triton replied in “{0}”.", [safe]) : t("Triton replied."),
			indicator: "blue",
		});
	}

	// ---- SapphireTriton.ask() -----------------------------------------------
	// Opens the panel with a prompt in the message box and, optionally, a page pinned. It
	// prefills and never sends, as the classic opener does: the person reads the brief first.
	function ask(prompt, context) {
		if (state.dead) return false;
		const words = prompt === undefined || prompt === null ? "" : String(prompt).slice(0, MAX_PROMPT);
		toggle(true);
		if (!words) return true; // nothing to put in the box: opening the panel is the whole request
		const fields = { prompt: words };
		if (context && typeof context === "object") {
			try {
				fields.page = JSON.parse(JSON.stringify(context)); // plain data only crosses the bridge
			} catch (e) {
				// A context that cannot be copied is left out; the prompt still goes.
			}
		}
		state.pendingAsk = fields;
		flushAsk();
		return true;
	}

	// May be sent any time after `ready`, sign-in or not: the frame holds it until the chat is up.
	// Until then the Desk keeps its own copy, because a frame that never signs in is replaced by
	// the classic widget and the prompt has to go there instead of being lost with the frame.
	function flushAsk() {
		if (!state.ready || !state.pendingAsk) return;
		if (!bridge.post(TO_FRAME.ASK, state.pendingAsk)) return;
		if (!state.signedIn) state.heldAsk = state.pendingAsk;
		state.pendingAsk = null;
	}

	// ---- giving up ----------------------------------------------------------
	function destroy() {
		if (state.dead) return;
		state.dead = true;
		["readyTimer", "loadTimer", "signInTimer", "pollTimer"].forEach(stop);
		bridge.stop();
		pages.stop();
		clockIn.destroy();
		listeners.splice(0).forEach(([target, type, fn]) => target.removeEventListener(type, fn));
		// The stylesheet stays; what it says about the button's own classes must stop applying.
		if (doc.documentElement) doc.documentElement.classList.remove(OWNER_CLASS);
		// The frame leaves with the panel. Removing a frame adds nothing to the history list.
		[els.panel, els.fab].forEach((node) => {
			if (node && node.parentNode) node.parentNode.removeChild(node);
		});
		state.frame = null;
	}

	function handBack(reason) {
		if (state.dead) return;
		const waiting = state.pendingAsk || state.heldAsk;
		const detail = {
			open: state.open,
			// The gate hands this to the classic opener, which still calls the page a context.
			ask: waiting ? { prompt: waiting.prompt, context: waiting.page || null } : null,
		};
		destroy();
		giveUp(reason, detail);
	}

	build();

	return {
		toggle: toggle,
		ask: ask,
		destroy: destroy,
		isOpen: () => state.open,
		isReady: () => state.ready,
		isSignedIn: () => state.signedIn,
	};
}
