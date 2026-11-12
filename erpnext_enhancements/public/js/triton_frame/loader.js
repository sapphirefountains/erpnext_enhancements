/**
 * The framed Triton widget's loader: a button, a panel and ONE frame (v1.802.0).
 *
 * The classic widget (`global_enhancements/triton_widget.js`) is a whole chat program in the
 * Desk. This is what replaces it for a person on the framed widget: the same round button and the
 * same panel, with Triton's own compact chat page (`/embed.html`, served by Triton) in a frame
 * where the transcript used to be. The Desk keeps only what the Desk alone can do: the button,
 * the panel, Alt+T, `SapphireTriton.ask()`, telling the frame which record is open, opening a
 * record the frame asks for, and getting the frame signed in.
 *
 * Loaded only for a person on the framed widget, by `global_enhancements/triton_frame_gate.js`,
 * as its own bundle (`triton_frame.bundle.js`). Everyone else never downloads it.
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
 *    again costs nothing and the chat is where it was left.
 * 3. A person is never left with a dead panel. If the frame has not said "ready" within
 *    READY_TIMEOUT_MS, or says it speaks a contract version this loader does not know, or Triton
 *    has no ticket route, everything here is torn down and `giveUp` is called once; the gate then
 *    starts the classic widget in its place. Until the frame is ready the panel shows its own
 *    header with a working Close, so a phone is never stuck under a blank full-screen sheet.
 * 4. Only Triton's configured origin is listened to, and only it is posted to (`bridge.js`).
 * 5. The phone sheet is sized to the VISIBLE screen. A framed page cannot see the on-screen
 *    keyboard (inside a frame `visualViewport` is just the frame's own box), so it cannot move
 *    its message box out of the keyboard's way. This page can, so it shrinks the sheet and the
 *    frame's own layout does the rest.
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
import { CONTRACT_VERSION, FROM_FRAME, TO_FRAME } from "./contract.js";
import { createPageSource } from "./page_source.js";
import { recordRoute } from "./record_link.js";

/** How long the frame has to say "ready" before the classic widget takes over. */
export const READY_TIMEOUT_MS = 6000;
export const TICKET_METHOD = "erpnext_enhancements.api.triton_frame.get_ticket";
/** What the framed page may use. Without the attribute a cross-origin frame gets neither. */
export const FRAME_ALLOW = "clipboard-write; microphone";
/** A frame stuck asking to be signed in again must not hammer two servers. */
export const TICKETS_PER_MINUTE = 6;
const PHONE_QUERY = "(max-width: 480px)";
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

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
	const t = typeof win.__ === "function" ? win.__ : (s) => s;

	const state = {
		open: false,
		frame: null,
		ready: false, // the frame has said "ready" in a version we speak
		delivered: false, // a ticket has been posted to the frame at least once
		live: false, // the frame is what the panel shows (the waiting state is hidden)
		dead: false,
		readyTimer: null,
		ticketInFlight: null,
		ticketTimes: [],
		pendingAsk: null,
	};
	const els = {};
	const listeners = [];

	function listen(target, type, fn) {
		if (!target || typeof target.addEventListener !== "function") return;
		target.addEventListener(type, fn);
		listeners.push([target, type, fn]);
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

		// What the panel shows until the frame is ready and signed in, and again if signing in
		// fails: the classic header (so it looks like the same panel) with a Close that works,
		// and one line of plain words.
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
		const words = el("p", "triton-frame-words", t("Opening Triton…"));
		const retry = el("button", "btn btn-default btn-sm triton-frame-retry", t("Try again"));
		retry.type = "button";
		retry.hidden = true;
		listen(retry, "click", () => {
			showShell(t("Opening Triton…"), false);
			sendTicket();
		});
		body.appendChild(words);
		body.appendChild(retry);
		shell.appendChild(body);
		panel.appendChild(shell);

		els.fab = fab;
		els.panel = panel;
		els.shell = shell;
		els.words = words;
		els.retry = retry;
		doc.body.appendChild(fab);
		doc.body.appendChild(panel);

		listen(doc, "keydown", onKeydown);
		if (win.visualViewport) {
			listen(win.visualViewport, "resize", fitSheet);
			listen(win.visualViewport, "scroll", fitSheet);
		}
		if (config.page_context) pages.start();
	}

	function showShell(text, canRetry) {
		els.words.textContent = text;
		els.retry.hidden = !canRetry;
		els.shell.hidden = false;
		state.live = false;
		els.panel.classList.remove("triton-frame-live");
	}

	function hideShell() {
		els.shell.hidden = true;
		state.live = true;
		els.panel.classList.add("triton-frame-live");
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
		els.panel.appendChild(frame);
		state.readyTimer = win.setTimeout(() => handBack("timeout"), READY_TIMEOUT_MS);
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
			fitSheet();
			if (state.ready) {
				// The record may have been edited, or a list re-filtered, since the last report.
				if (config.page_context) pages.report(true);
				bridge.post(TO_FRAME.OPENED);
			}
			focusFrame();
		} else {
			const hadFocus = doc.activeElement === state.frame;
			if (state.ready) bridge.post(TO_FRAME.CLOSED);
			// Focus inside a hidden frame is focus nowhere: the next key press would go to a
			// page the person cannot see. Hand it back to the button they opened it with.
			if (hadFocus && typeof els.fab.focus === "function") els.fab.focus();
		}
	}

	// Only into a frame the person can see. A new ticket arrives every half hour whether the
	// panel is open or not, and focusing a hidden frame then would take the keyboard away from
	// whatever Desk field the person is typing in.
	function focusFrame() {
		if (!state.open || !state.live || !state.frame) return;
		if (typeof state.frame.focus === "function") state.frame.focus();
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
	// asked for anything. The panel says what went wrong, once, in its own words.
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

	function sendTicket() {
		if (state.dead) return Promise.resolve();
		if (state.ticketInFlight) return state.ticketInFlight;

		const now = Date.now();
		state.ticketTimes = state.ticketTimes.filter((at) => now - at < 60000);
		if (state.ticketTimes.length >= TICKETS_PER_MINUTE) {
			ticketFailed({ reason: "busy", message: t("Triton keeps asking to sign in again. Wait a minute, then try again.") });
			return Promise.resolve();
		}
		state.ticketTimes.push(now);

		state.ticketInFlight = requestTicket().then((result) => {
			state.ticketInFlight = null;
			if (state.dead) return;
			if (result.ok && typeof result.ticket === "string" && result.ticket) {
				// Posted and forgotten: the ticket is not kept on `state`, and never logged.
				if (bridge.post(TO_FRAME.TICKET, { ticket: result.ticket })) {
					state.delivered = true;
					hideShell();
					focusFrame();
				}
				return;
			}
			// Triton has no ticket route: this Triton cannot serve the framed widget at all.
			if (result.reason === "unsupported") return handBack("no-ticket-route");
			ticketFailed(result);
		});
		return state.ticketInFlight;
	}

	function ticketFailed(result) {
		const message = result.message || t("Triton could not be reached. Try again in a minute.");
		if (!state.delivered) {
			// Never signed in: the frame has nothing to show, so the panel says it.
			showShell(message, true);
			return;
		}
		// Mid-conversation. Do not cover the chat; the frame says it beside the message box.
		bridge.post(TO_FRAME.TICKET_ERROR, { reason: result.reason || "unreachable", message: message });
	}

	// ---- messages from the frame --------------------------------------------
	function onMessage({ version, type, body }) {
		if (state.dead) return;
		if (type === FROM_FRAME.READY) {
			// Rule 3: a page speaking another contract cannot be driven by this loader.
			if (version !== CONTRACT_VERSION) return handBack("contract");
			return onReady();
		}
		if (version !== CONTRACT_VERSION || !state.ready) return;

		if (type === FROM_FRAME.NEED_TICKET) sendTicket();
		else if (type === FROM_FRAME.OPEN_RECORD) openRecord(body);
		else if (type === FROM_FRAME.CLOSE) toggle(false);
		else if (type === FROM_FRAME.TOGGLE) toggle();
	}

	function onReady() {
		if (state.readyTimer) win.clearTimeout(state.readyTimer);
		state.readyTimer = null;
		state.ready = true;
		// "ready" can come again (the page in the frame reloaded itself). Same answer each time.
		sendTicket().then(() => {
			if (state.dead) return;
			if (config.page_context) pages.report(true);
			if (state.open) bridge.post(TO_FRAME.OPENED);
			flushAsk();
		});
	}

	function openRecord(body) {
		const readable = (frappe.boot && frappe.boot.user && frappe.boot.user.can_read) || [];
		const route = recordRoute(body, readable);
		if (!route || typeof frappe.set_route !== "function") return;
		// frappe.set_route: the Desk the person is in moves, with an ordinary history entry, so
		// Back returns to the page they were on.
		frappe.set_route(route);
		// On a phone the sheet covers the whole Desk; get out of the way of what was asked for.
		if (onPhone()) toggle(false);
	}

	// ---- SapphireTriton.ask() -----------------------------------------------
	// Opens the panel with a prompt in the message box and, optionally, a record pinned. It
	// prefills and never sends, as the classic opener does: the person reads the brief first.
	function ask(prompt, context) {
		if (state.dead) return false;
		let pinned = null;
		if (context && typeof context === "object") {
			try {
				pinned = JSON.parse(JSON.stringify(context)); // plain data only crosses the bridge
			} catch (e) {
				pinned = null;
			}
		}
		state.pendingAsk = { prompt: prompt === undefined || prompt === null ? "" : String(prompt), context: pinned };
		toggle(true);
		flushAsk();
		return true;
	}

	function flushAsk() {
		if (!state.ready || !state.pendingAsk) return;
		if (bridge.post(TO_FRAME.ASK, state.pendingAsk)) state.pendingAsk = null;
	}

	// ---- giving up ----------------------------------------------------------
	function destroy() {
		if (state.dead) return;
		state.dead = true;
		if (state.readyTimer) win.clearTimeout(state.readyTimer);
		state.readyTimer = null;
		bridge.stop();
		pages.stop();
		listeners.splice(0).forEach(([target, type, fn]) => target.removeEventListener(type, fn));
		// The frame leaves with the panel. Removing a frame adds nothing to the history list.
		[els.panel, els.fab].forEach((node) => {
			if (node && node.parentNode) node.parentNode.removeChild(node);
		});
		state.frame = null;
	}

	function handBack(reason) {
		if (state.dead) return;
		const detail = { open: state.open, ask: state.pendingAsk };
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
	};
}
