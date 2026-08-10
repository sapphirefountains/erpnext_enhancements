/**
 * Triton embedded assistant widget.
 *
 * Targets: every ERPNext desk page (a global floating widget, not tied to any
 * doctype). Loaded via: hooks.py `app_include_js` (global). Self-disables unless
 * the server `get_config` reports it enabled and the user is not Guest.
 *
 * A floating trident button on every ERPNext desk page that opens a chat panel
 * wired to Triton. All traffic goes through same-origin whitelisted methods on
 * `erpnext_enhancements.triton_chat` (no CORS, no client-side secrets). The chat
 * stream is relayed back as SSE and rendered token-by-token. Users can pin the
 * page they're on (document / list / report) as context, and Triton's proposed
 * ERPNext changes arrive as confirmation cards.
 *
 * PHASE 3 (ADR 0009, decision #8) made this bubble DUAL-SURFACE. It now hosts both the
 * Triton conversation (everything below, unchanged) and a coworker chat surface
 * (`chat_surface.js`), and it can expand into the full SPA at /chat deep-linked to the
 * conversation the user was in. Every Phase 3 addition is marked `--- phase 3 ---` and is
 * additive: Appendix A of ADR 0009 is this widget's preserved-behaviour inventory, and a
 * regression against it is a phase failure rather than a tradeoff. The streaming
 * re-entrancy rule (`scripts/test_triton_widget_guards.js`) still holds — the surface
 * switch HIDES the Triton transcript rather than clearing it, so `pumpText` keeps writing
 * into an attached node.
 */
import {
	applyCitations,
	citationLabel,
	holdbackLength,
	indexManifest,
	isSafeUrl,
	orderManifestForDisplay,
} from "../chat/citations.js";
import { isComposingKey } from "../chat/dom.js";
import { writeHandoff, readHandoff } from "../chat/handoff.js";
import { buildRoute } from "../chat/routes.js";
import { BubbleChatSurface } from "./chat_surface.js";

(function () {
	const METHOD = "erpnext_enhancements.triton_chat";
	const LS_SESSION = "triton_session_id";
	const LS_MODEL = "triton_model";
	// Selected persona key ("" = Triton's default voice). Personas live in
	// Triton and are keyed on the same user the identity bridge resolves to, so
	// one created here also shows up in the Triton web app.
	const LS_PERSONA = "triton_persona_key";
	// Local date (YYYY-MM-DD) the morning briefing was last shown, so it appears
	// once on the first chat of each day.
	const LS_BRIEF = "triton_briefing_date";

	const state = {
		config: null,
		sessionId: null,
		// Selected model id ("" = let Triton auto-route). Persisted in LS_MODEL.
		model: "",
		// Selected persona key ("" = default voice). Persisted in LS_PERSONA.
		persona: "",
		personas: [],
		contextRefs: [],
		open: false,
		streaming: false,
		els: {},
		// The assistant message currently being streamed.
		live: null,
		// --- phase 3 --- which half of the bubble is showing: "triton" or "chat".
		surface: "triton",
		// --- phase 3 --- the coworker surface (BubbleChatSurface), built on first switch.
		chat: null,
		// --- phase 3 --- total unread across coworker rooms, rendered as the FAB badge.
		// Decision #3c: this is the count that matters, and Phase 4 wires notifications to it.
		unread: 0,
	};

	// ---- helpers ---------------------------------------------------------
	const esc = frappe.utils.escape_html;
	const xcall = (m, args) => frappe.xcall(`${METHOD}.${m}`, args);

	function md(text) {
		try {
			return frappe.markdown(text || "");
		} catch (e) {
			return esc(text || "").replace(/\n/g, "<br>");
		}
	}

	function scrollDown() {
		const m = state.els.messages;
		if (m) m.scrollTop = m.scrollHeight;
	}

	// Honour the OS "reduce motion" setting: when on, we snap text in instead of
	// running the typewriter pump and let CSS drop the spin/shimmer/cursor.
	const reducedMotion =
		!!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

	// ---- mermaid diagrams ------------------------------------------------
	// Lazy-load Mermaid once per session from the same CDN the Process Document
	// form uses, then turn ```mermaid fenced code blocks in a finished message
	// into rendered diagrams. Loading/rendering failures degrade to the raw code.
	let _mermaidPromise = null;
	function ensureMermaid() {
		if (window.mermaid) return Promise.resolve(window.mermaid);
		if (_mermaidPromise) return _mermaidPromise;
		_mermaidPromise = new Promise((resolve, reject) => {
			const s = document.createElement("script");
			s.src = "https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js";
			s.onload = () => {
				try {
					// Sapphire Fountains brand theme (mermaid_theme.js, same bundle)
					if (window.sf_mermaid) {
						window.sf_mermaid.init(window.mermaid);
					} else {
						window.mermaid.initialize({ startOnLoad: false, theme: "default" });
					}
				} catch (e) {
					// theme init must never block the mermaid load
				}
				resolve(window.mermaid);
			};
			s.onerror = () => reject(new Error("mermaid load failed"));
			document.head.appendChild(s);
		});
		return _mermaidPromise;
	}

	async function renderMermaidIn(container) {
		if (!container) return;
		const codes = container.querySelectorAll("code.language-mermaid");
		if (!codes.length) return;
		let mermaid;
		try {
			mermaid = await ensureMermaid();
		} catch (e) {
			return; // library unavailable — leave the fenced code visible
		}
		codes.forEach((code) => {
			const pre = code.closest("pre") || code;
			if (pre.dataset.tritonMermaid) return; // already processed
			pre.dataset.tritonMermaid = "1";
			const src = code.textContent || "";
			const holder = document.createElement("div");
			holder.className = "triton-mermaid";
			const node = document.createElement("pre");
			node.className = "mermaid";
			node.textContent = src;
			holder.appendChild(node);
			pre.replaceWith(holder);
			Promise.resolve()
				.then(() => mermaid.run({ nodes: [node] }))
				.then(() => scrollDown())
				.catch(() => {
					const fb = document.createElement("pre");
					fb.textContent = src;
					holder.replaceWith(fb);
				});
		});
	}

	// ---- inline charts (Triton render_chart ui_command) ------------------
	// render_chart ships a self-contained Chart.js-shaped config; the Desk bundles
	// frappe-charts (frappe.Chart), so we map between the two and render inline.
	function renderChart(wrap, params) {
		if (!wrap || !params) return;
		const src = params.data || {};
		const labels = src.labels || [];
		const datasets = (src.datasets || []).map((ds) => ({
			name: ds.label || ds.name || "",
			values: (ds.data || ds.values || []).map((v) =>
				typeof v === "number" ? v : Number(v) || 0
			),
		}));
		if (!labels.length && !datasets.length) return;

		const typeMap = { doughnut: "donut", donut: "donut", pie: "pie", line: "line", bar: "bar" };
		const type = typeMap[String(params.chart_type || "bar").toLowerCase()] || "bar";

		const box = document.createElement("div");
		box.className = "triton-chart";
		if (params.title) {
			const t = document.createElement("div");
			t.className = "triton-chart-title";
			t.textContent = params.title;
			box.appendChild(t);
		}
		const host = document.createElement("div");
		host.className = "triton-chart-host";
		box.appendChild(host);
		wrap.appendChild(box);

		try {
			if (window.frappe && frappe.Chart) {
				new frappe.Chart(host, {
					type,
					height: 220,
					animate: !reducedMotion,
					colors: ["#1f6feb", "#2da44e", "#bf8700", "#cf222e", "#8250df", "#0969da"],
					axisOptions: { xIsSeries: type === "line" },
					data: { labels, datasets },
				});
			} else {
				host.appendChild(chartFallbackTable(labels, datasets));
			}
		} catch (e) {
			host.appendChild(chartFallbackTable(labels, datasets));
		}
		scrollDown();
	}

	function chartFallbackTable(labels, datasets) {
		const tbl = document.createElement("table");
		tbl.className = "triton-chart-table";
		const ds0 = datasets[0] || { values: [] };
		labels.forEach((lab, i) => {
			const tr = document.createElement("tr");
			const k = document.createElement("td");
			k.textContent = lab;
			const v = document.createElement("td");
			v.textContent = ds0.values[i] != null ? String(ds0.values[i]) : "";
			tr.appendChild(k);
			tr.appendChild(v);
			tbl.appendChild(tr);
		});
		return tbl;
	}

	// Gantt/Kanban/3D visualizations are doctype query specs (no inline data) and
	// need Triton's full viz engine — show a compact pointer rather than break.
	function renderVizFallback(wrap, cmd, params) {
		if (!wrap) return;
		let kind = __("visualization");
		if (cmd === "render_3d_simulation") kind = __("3D simulation");
		else if (params && (params.viz_type || params.chart_kind)) {
			kind = (params.viz_type || params.chart_kind) + " " + __("visualization");
		}
		const note = document.createElement("div");
		note.className = "triton-viz-note";
		note.textContent = "📊 " + kind + " — " + __("open the Triton app to view");
		wrap.appendChild(note);
		scrollDown();
	}

	// ---- DOM construction ------------------------------------------------
	function build() {
		const fab = document.createElement("button");
		fab.className = "triton-fab";
		fab.title = "Ask Triton (Alt+T)";
		fab.textContent = "🔱";
		fab.addEventListener("click", toggle);
		// --- phase 3 --- unread badge. Appended rather than folded into textContent so the
		// trident is still the button's accessible name and the existing CSS still positions it.
		const badge = document.createElement("span");
		badge.className = "triton-fab-badge is-hidden";
		fab.appendChild(badge);
		document.body.appendChild(fab);

		const panel = document.createElement("div");
		panel.className = "triton-panel";
		// --- phase 3 --- two additions to the header markup, and nothing removed: the surface
		// tabs and the expand control. Every existing control keeps its class and its order, so
		// Appendix A's header rows still resolve.
		panel.innerHTML = `
			<div class="triton-header">
				<span class="triton-logo">🔱</span>
				<span class="triton-title">Triton</span>
				<div class="triton-surface-tabs" role="tablist">
					<button class="triton-surface-tab is-active" data-surface="triton" role="tab" aria-selected="true">Triton</button>
					<button class="triton-surface-tab" data-surface="chat" role="tab" aria-selected="false">Chats</button>
				</div>
				<select class="triton-persona-select" title="Choose persona"></select>
				<select class="triton-model-select" title="Choose model"></select>
				<button class="triton-icon-btn triton-history" title="Chat history">🕘</button>
				<button class="triton-icon-btn triton-new" title="New chat">✎</button>
				<a class="triton-icon-btn triton-expand" title="Open the full chat app" href="/chat">⤢</a>
				<button class="triton-icon-btn triton-close" title="Close">✕</button>
			</div>
			<div class="triton-context-bar">
				<button class="triton-context-add" title="Attach the page you're viewing">＋ Add this page</button>
			</div>
			<div class="triton-messages"></div>
			<div class="triton-input-bar">
				<textarea class="triton-text" rows="1" placeholder="Ask about your data…"></textarea>
				<button class="triton-send" title="Send">➤</button>
			</div>
			<div class="triton-history-panel">
				<div class="triton-history-head">
					<button class="triton-icon-btn triton-history-back" title="Back">←</button>
					<span class="triton-history-heading">Chat history</span>
				</div>
				<div class="triton-history-list"></div>
			</div>
			<div class="triton-history-panel triton-personas-panel">
				<div class="triton-history-head">
					<button class="triton-icon-btn triton-personas-back" title="Back">←</button>
					<span class="triton-history-heading">Personas</span>
					<button class="triton-icon-btn triton-persona-new" title="New persona">＋</button>
				</div>
				<div class="triton-history-list triton-personas-list"></div>
			</div>
			<div class="triton-chat-surface is-hidden"></div>`;
		document.body.appendChild(panel);

		state.els = {
			fab,
			badge,
			chatSurface: panel.querySelector(".triton-chat-surface"),
			surfaceTabs: panel.querySelectorAll(".triton-surface-tab"),
			expand: panel.querySelector(".triton-expand"),
			panel,
			messages: panel.querySelector(".triton-messages"),
			contextBar: panel.querySelector(".triton-context-bar"),
			contextAdd: panel.querySelector(".triton-context-add"),
			text: panel.querySelector(".triton-text"),
			send: panel.querySelector(".triton-send"),
			modelSelect: panel.querySelector(".triton-model-select"),
			personaSelect: panel.querySelector(".triton-persona-select"),
			personasPanel: panel.querySelector(".triton-personas-panel"),
			personasList: panel.querySelector(".triton-personas-list"),
			personasBack: panel.querySelector(".triton-personas-back"),
			personaNew: panel.querySelector(".triton-persona-new"),
			historyBtn: panel.querySelector(".triton-history"),
			historyPanel: panel.querySelector(".triton-history-panel"),
			historyList: panel.querySelector(".triton-history-list"),
			historyBack: panel.querySelector(".triton-history-back"),
		};

		populateModels();
		refreshModels(); // replace the fallback list with Triton's live models
		applyPersonas([]); // "Default" + the manage sentinel until the list lands
		refreshPersonas();
		panel.querySelector(".triton-close").addEventListener("click", () => toggle(false));
		panel.querySelector(".triton-new").addEventListener("click", newChat);
		state.els.historyBtn.addEventListener("click", openHistory);
		state.els.historyBack.addEventListener("click", closeHistory);
		state.els.modelSelect.addEventListener("change", (e) => setModel(e.target.value));
		state.els.personaSelect.addEventListener("change", onPersonaChange);
		state.els.personasBack.addEventListener("click", closePersonas);
		state.els.personaNew.addEventListener("click", () => showPersonaForm(null));
		state.els.contextAdd.addEventListener("click", addCurrentPage);
		state.els.send.addEventListener("click", onSend);
		state.els.text.addEventListener("keydown", (e) => {
			// --- phase 3 fix --- `isComposing` (and the legacy 229 keycode that older WebKit
			// and Android IMEs report instead of it). Without this, an IME user pressing Enter
			// to COMMIT a candidate — the ordinary way to type Japanese, Chinese or Korean —
			// sends the half-finished message instead of finishing the word. The two checks are
			// belt and braces because `isComposing` is unset on the keydown that ends
			// composition in some engines, and 229 is the only signal there.
			if (isComposingKey(e)) return;
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				onSend();
			}
		});
		state.els.text.addEventListener("input", autoGrow);

		document.addEventListener("keydown", (e) => {
			// --- phase 3 fix --- exclude ctrl/meta. AltGr is reported as ctrlKey+altKey on
			// Windows and on many EU layouts, so the un-excluded form made AltGr+T — which is
			// how you type a perfectly ordinary character on several of them — open the
			// assistant over whatever the user was writing. `altKey` alone is not a shortcut on
			// those keyboards; it is a modifier the user did not press.
			if (e.ctrlKey || e.metaKey) return;
			if (e.altKey && (e.key === "t" || e.key === "T")) {
				e.preventDefault();
				toggle();
			}
		});

		if (!state.config.enable_page_context) {
			state.els.contextAdd.style.display = "none";
		}

		// --- phase 3 --- surface tabs and the expand control.
		state.els.surfaceTabs.forEach((tab) => {
			tab.addEventListener("click", () => setSurface(tab.dataset.surface));
		});
		// A real <a href> so middle-click and ctrl-click open a tab, which the handoff's
		// localStorage mirror is there to survive. The click handler navigates in the SAME tab
		// (location.assign) because sessionStorage is per-tab and that is the primary copy.
		state.els.expand.addEventListener("click", onExpand);
		// --- phase 3 --- header space. The tabs and the expand control added ~155px to a
		// header that had ~64px of slack at 410px, which pushed new-chat / expand / close off
		// the right edge entirely.
		//
		// With chat ON the tabs literally read "Triton", so the separate title beside them is
		// a duplicate — `has-tabs` drops it and returns most of the space. With chat OFF there
		// is nothing to switch between, so the whole switcher goes and the title comes back;
		// a one-item toggle is not a toggle.
		if (chatEnabled()) {
			panel.querySelector(".triton-header").classList.add("has-tabs");
		} else {
			panel.querySelector(".triton-surface-tabs").classList.add("is-hidden");
			state.els.expand.classList.add("is-hidden");
		}
	}

	// ---- phase 3: dual surface, badge, handoff ---------------------------

	// Gated on the same boolean boot.py computes: the master switch AND this user's pilot
	// standing. Cosmetic only — every endpoint re-checks — but it keeps a tab out of the
	// header for the people it would 403 for.
	function chatEnabled() {
		return !!(window.frappe && frappe.boot && frappe.boot.ee_chat);
	}

	// Switch which half of the bubble is showing.
	//
	// Deliberately does NOT clear the Triton transcript and therefore carries no
	// `state.streaming` guard: hiding an attached node is safe, and `pumpText` keeps writing
	// into it, so switching to Chats mid-answer and back finds the answer where it was left.
	// Clearing here instead would be the exact defect
	// `scripts/test_triton_widget_guards.js` exists to catch.
	function setSurface(name) {
		const surface = name === "chat" ? "chat" : "triton";
		state.surface = surface;
		state.els.surfaceTabs.forEach((tab) => {
			const active = tab.dataset.surface === surface;
			tab.classList.toggle("is-active", active);
			tab.setAttribute("aria-selected", active ? "true" : "false");
		});

		const showChat = surface === "chat";
		state.els.chatSurface.classList.toggle("is-hidden", !showChat);
		state.els.messages.classList.toggle("is-hidden", showChat);
		state.els.contextBar.classList.toggle("is-hidden", showChat);
		state.els.panel.querySelector(".triton-input-bar").classList.toggle("is-hidden", showChat);
		state.els.modelSelect.classList.toggle("is-hidden", showChat);
		state.els.personaSelect.classList.toggle("is-hidden", showChat);
		state.els.historyBtn.classList.toggle("is-hidden", showChat);

		if (showChat) {
			ensureChatSurface();
			state.chat.ensureLoaded();
		}
		writeBubbleHandoff();
	}

	function ensureChatSurface() {
		if (state.chat) return state.chat;
		state.chat = new BubbleChatSurface(state.els.chatSurface, {
			me: (window.frappe && frappe.session && frappe.session.user) || null,
			onUnread: (total) => renderBadge(total),
			onStateChange: () => writeBubbleHandoffThrottled(),
		});
		subscribeRealtime();
		return state.chat;
	}

	// Realtime through Desk's OWN socket. `frappe.realtime` is already connected and
	// authenticated on every Desk page, so opening a second socket here would double the
	// connection count for every employee to gain nothing. The SPA connects its own because
	// it is a website route with no Desk bundle.
	function subscribeRealtime() {
		if (!window.frappe || !frappe.realtime || !frappe.realtime.on) return;

		// Re-join the active room after a reconnect. Frappe's client does NOT replay
		// `open_docs` on connect and `doc_subscribe` early-returns while the key is still in
		// it, so without this the bubble goes permanently deaf after the first disconnect —
		// and the load balancer guarantees there will be one.
		const raw = frappe.realtime.socket;
		if (raw && typeof raw.on === "function") {
			raw.on("connect", () => {
				if (state.chat) state.chat.rejoinAfterReconnect();
			});
		}

		const events = [
			"chat_message_created",
			"chat_message_edited",
			"chat_message_deleted",
			"chat_typing",
			"chat_typing_stopped",
			"chat_read_receipt",
			"chat_unread_updated",
			"chat_room_updated",
			"chat_mention",
		];
		events.forEach((name) => {
			frappe.realtime.on(name, (payload) => {
				if (state.chat) state.chat.onRealtime(name, payload || {});
			});
		});
	}

	function renderBadge(total) {
		state.unread = Number(total) || 0;
		const badge = state.els.badge;
		if (!badge) return;
		badge.textContent = state.unread > 99 ? "99+" : String(state.unread);
		badge.classList.toggle("is-hidden", state.unread < 1);
		state.els.fab.setAttribute(
			"aria-label",
			state.unread ? `Ask Triton — ${state.unread} unread messages` : "Ask Triton"
		);
	}

	// The handoff record the SPA reads on load. Written on every meaningful change and
	// SYNCHRONOUSLY immediately before navigating — see onExpand.
	function writeBubbleHandoff() {
		const chatState = state.chat ? state.chat.handoffState() : {};
		writeHandoff(
			{
				room: chatState.room || null,
				thread: chatState.thread || null,
				anchorMessage: chatState.anchorMessage || null,
				anchorRatio: chatState.anchorRatio,
				draft: chatState.draft || "",
				surface: state.surface === "chat" ? "coworker" : "triton",
				tritonConversation: state.sessionId ? String(state.sessionId) : null,
			},
			{ session: window.sessionStorage, local: window.localStorage },
			Date.now()
		);
	}

	let _handoffAt = 0;
	function writeBubbleHandoffThrottled() {
		const now = Date.now();
		if (now - _handoffAt < 500) return;
		_handoffAt = now;
		writeBubbleHandoff();
	}

	// Expand. The write is SYNCHRONOUS and happens before navigation, because a throttled
	// write that has not fired yet when location.assign runs is a handoff that silently does
	// not happen — and it fails for exactly the user who clicks expand quickly, which is most
	// of them.
	function onExpand(e) {
		if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1)) {
			// A deliberate new tab. sessionStorage will not follow, which is what the
			// localStorage mirror (nonce + 60s TTL) exists for; writeHandoff wrote both.
			writeBubbleHandoff();
			return;
		}
		if (e) e.preventDefault();
		writeBubbleHandoff();
		const chatState = state.chat ? state.chat.handoffState() : {};
		window.location.assign(
			chatState.room
				? buildRoute({ room: chatState.room, thread: chatState.thread || null })
				: "/chat"
		);
	}

	// The reverse handoff: the SPA wrote where it left off, so the bubble opens there.
	// Symmetric or it is half a feature.
	function restoreFromHandoff() {
		if (!chatEnabled()) return;
		const record = readHandoff(
			{ session: window.sessionStorage, local: window.localStorage },
			Date.now()
		);
		if (!record || record.surface !== "coworker" || !record.room) return;
		setSurface("chat");
		ensureChatSurface().restore(record);
	}

	function autoGrow() {
		const t = state.els.text;
		t.style.height = "auto";
		t.style.height = Math.min(t.scrollHeight, 120) + "px";
	}

	// Restart a one-shot CSS animation by toggling a class (force reflow between).
	function pulse(el, cls) {
		if (!el) return;
		el.classList.remove(cls);
		void el.offsetWidth; // reflow so the animation replays
		el.classList.add(cls);
	}

	// ---- model picker ----------------------------------------------------
	// Render an options list and resolve the active selection, preserving the
	// user's current pick when it survives a live refresh.
	function applyModels(models) {
		const sel = state.els.modelSelect;
		if (!sel || !models || !models.length) return;
		state.config.models = models;
		sel.innerHTML = "";
		models.forEach((m) => {
			const o = document.createElement("option");
			o.value = m.value;
			o.textContent = m.label;
			sel.appendChild(o);
		});
		// Selection priority: current pick (if still listed) > saved choice >
		// configured default > Flash (requested default) > first option.
		const values = models.map((m) => m.value);
		const saved = localStorage.getItem(LS_MODEL);
		let initial;
		if (state.model && values.includes(state.model)) {
			initial = state.model;
		} else if (saved !== null && values.includes(saved)) {
			initial = saved;
		} else if (values.includes(state.config.default_model)) {
			initial = state.config.default_model;
		} else if (values.includes("gemini-3.5-flash")) {
			initial = "gemini-3.5-flash";
		} else {
			initial = models[0].value;
		}
		state.model = initial;
		sel.value = initial;
	}

	function populateModels() {
		const models = (state.config.models && state.config.models.length)
			? state.config.models
			: [{ value: "", label: "Auto" }];
		applyModels(models);
	}

	// Pull the live model list from Triton (via the proxy) so the picker tracks
	// backend changes globally. Best-effort: the fallback list already shows.
	async function refreshModels() {
		try {
			const live = await xcall("list_models");
			if (live && live.length) applyModels(live);
		} catch (e) {
			/* keep the fallback list */
		}
	}

	function setModel(v) {
		state.model = v || "";
		localStorage.setItem(LS_MODEL, state.model);
	}

	// ---- persona picker --------------------------------------------------
	// Mirrors the model picker above, with two differences: the list is
	// per-user (it contains the caller's own private personas), and it carries
	// a trailing "Manage…" sentinel that opens the CRUD panel instead of
	// selecting anything.
	const PERSONA_MANAGE = "__manage__";

	function applyPersonas(personas) {
		const sel = state.els.personaSelect;
		if (!sel) return;
		state.personas = Array.isArray(personas) ? personas : [];
		sel.innerHTML = "";

		const mk = (value, label) => {
			const o = document.createElement("option");
			o.value = value;
			o.textContent = label;
			return o;
		};
		sel.appendChild(mk("", __("Default")));

		const groups = [
			[__("Built in"), state.personas.filter((p) => p.is_builtin)],
			[__("Yours"), state.personas.filter((p) => !p.is_builtin && p.editable)],
			[__("Shared"), state.personas.filter((p) => !p.is_builtin && !p.editable)],
		];
		groups.forEach(([label, items]) => {
			if (!items.length) return;
			const g = document.createElement("optgroup");
			g.label = label;
			items.forEach((p) => {
				g.appendChild(mk(p.key, `${p.emoji ? p.emoji + " " : ""}${p.name}`));
			});
			sel.appendChild(g);
		});
		sel.appendChild(mk(PERSONA_MANAGE, __("⚙ Manage…")));

		// Selection priority: current pick (if still listed) > saved choice > none.
		// Anything else is dropped rather than pinned, so a persona deleted from
		// the Triton web app stops riding along on every request from here.
		const keys = state.personas.map((p) => p.key);
		const saved = localStorage.getItem(LS_PERSONA);
		let initial = "";
		if (state.persona && keys.includes(state.persona)) {
			initial = state.persona;
		} else if (saved && keys.includes(saved)) {
			initial = saved;
		}
		state.persona = initial;
		sel.value = initial;
	}

	async function refreshPersonas() {
		try {
			applyPersonas(await xcall("list_personas"));
		} catch (e) {
			/* keep whatever is rendered; "Default" always works */
		}
	}

	function setPersona(v) {
		state.persona = v || "";
		if (state.persona) localStorage.setItem(LS_PERSONA, state.persona);
		else localStorage.removeItem(LS_PERSONA);
		if (state.els.personaSelect) state.els.personaSelect.value = state.persona;
	}

	function onPersonaChange(e) {
		if (e.target.value === PERSONA_MANAGE) {
			// Sentinel, not a choice — restore the real selection and open the panel.
			e.target.value = state.persona;
			openPersonas();
			return;
		}
		setPersona(e.target.value);
	}

	function openPersonas() {
		state.els.personasPanel.classList.add("triton-history-open");
		loadPersonas();
	}

	function closePersonas() {
		state.els.personasPanel.classList.remove("triton-history-open");
	}

	async function loadPersonas() {
		const list = state.els.personasList;
		list.innerHTML = `<div class="triton-history-empty">${__("Loading…")}</div>`;
		try {
			const personas = await xcall("list_personas");
			applyPersonas(personas);
			if (!state.personas.length) {
				list.innerHTML = `<div class="triton-history-empty">${__("No personas yet.")}</div>`;
				return;
			}
			list.innerHTML = "";
			state.personas.forEach((p) => list.appendChild(renderPersonaItem(p)));
		} catch (e) {
			list.innerHTML = `<div class="triton-history-empty">${__("Couldn't load personas.")}</div>`;
		}
	}

	function renderPersonaItem(p) {
		// A div, not a button: the row carries its own edit/delete buttons and
		// nesting buttons inside a button is invalid HTML.
		const item = document.createElement("div");
		item.className = "triton-history-item triton-persona-item";
		item.tabIndex = 0;
		if (p.key === state.persona) item.classList.add("active");

		const label = document.createElement("div");
		label.className = "triton-persona-label";
		const sub = p.description || (p.is_builtin
			? __("Built in")
			: p.editable
				? (p.visibility === "company" ? __("Yours · shared") : __("Yours"))
				: __("Shared by {0}", [p.author || __("a colleague")]));
		label.innerHTML =
			`<span class="triton-history-title">${esc((p.emoji ? p.emoji + " " : "") + p.name)}</span>` +
			`<span class="triton-history-when">${esc(sub)}</span>`;
		label.addEventListener("click", () => {
			setPersona(p.key);
			closePersonas();
		});
		item.appendChild(label);

		const actions = document.createElement("div");
		actions.className = "triton-persona-actions";
		const btn = (glyph, title, fn) => {
			const b = document.createElement("button");
			b.className = "triton-icon-btn";
			b.title = title;
			b.textContent = glyph;
			b.addEventListener("click", (e) => {
				e.stopPropagation();
				fn();
			});
			return b;
		};

		if (p.editable) {
			actions.appendChild(btn("✎", __("Edit"), () => showPersonaForm(p)));
			actions.appendChild(btn("🗑", __("Delete"), () => confirmDeletePersona(p)));
		} else {
			// Built-ins and colleagues' personas are read-only; duplicating is how
			// you customise one.
			actions.appendChild(btn("⧉", __("Duplicate"), () => duplicatePersona(p)));
		}
		item.appendChild(actions);
		return item;
	}

	function showPersonaForm(p) {
		const editing = !!(p && p.editable);
		const d = new frappe.ui.Dialog({
			title: editing ? __("Edit persona") : __("New persona"),
			fields: [
				{ fieldname: "name", fieldtype: "Data", label: __("Name"), reqd: 1, default: p ? p.name : "" },
				{ fieldname: "emoji", fieldtype: "Data", label: __("Emoji"), default: p ? p.emoji : "" },
				{
					fieldname: "description",
					fieldtype: "Small Text",
					label: __("Description"),
					description: __("One line, shown in the picker."),
					default: p ? p.description : "",
				},
				{
					fieldname: "system_prompt",
					fieldtype: "Long Text",
					label: __("System prompt"),
					reqd: 1,
					description: __(
						"Sets tone and voice. Triton's tool rules and approval gates always still apply."
					),
					default: p ? p.system_prompt : "",
				},
				{
					fieldname: "visibility",
					fieldtype: "Check",
					label: __("Share with everyone at the company"),
					default: p && p.visibility === "company" ? 1 : 0,
				},
			],
			primary_action_label: __("Save"),
			primary_action: async (values) => {
				const args = {
					name: values.name,
					system_prompt: values.system_prompt,
					description: values.description || "",
					emoji: values.emoji || "",
					visibility: values.visibility ? "company" : "private",
				};
				d.disable_primary_action();
				try {
					let saved;
					if (editing) {
						args.persona_id = p.key.split(":")[1];
						saved = await xcall("update_persona", args);
					} else {
						saved = await xcall("create_persona", args);
					}
					d.hide();
					await refreshPersonas();
					if (saved && saved.key) setPersona(saved.key);
					if (state.els.personasPanel.classList.contains("triton-history-open")) {
						loadPersonas();
					}
				} catch (e) {
					d.enable_primary_action();
				}
			},
		});
		d.show();
	}

	async function duplicatePersona(p) {
		try {
			const copy = await xcall("duplicate_persona", { persona_key: p.key });
			await refreshPersonas();
			if (copy && copy.key) setPersona(copy.key);
			loadPersonas();
			if (copy) showPersonaForm(copy);
		} catch (e) {
			/* Frappe surfaces the server error */
		}
	}

	function confirmDeletePersona(p) {
		frappe.confirm(
			__("Delete the persona {0}? This cannot be undone.", [`<b>${esc(p.name)}</b>`]),
			async () => {
				try {
					await xcall("delete_persona", { persona_id: p.key.split(":")[1] });
					if (state.persona === p.key) setPersona("");
					await refreshPersonas();
					loadPersonas();
				} catch (e) {
					/* Frappe surfaces the server error */
				}
			}
		);
	}

	// ---- session history -------------------------------------------------
	function openHistory() {
		state.els.historyPanel.classList.add("triton-history-open");
		loadSessions();
	}

	function closeHistory() {
		state.els.historyPanel.classList.remove("triton-history-open");
	}

	async function loadSessions() {
		const list = state.els.historyList;
		list.innerHTML = `<div class="triton-history-empty">${__("Loading…")}</div>`;
		try {
			const sessions = await xcall("list_sessions");
			if (!sessions || !sessions.length) {
				list.innerHTML = `<div class="triton-history-empty">${__("No previous chats yet.")}</div>`;
				return;
			}
			list.innerHTML = "";
			sessions.forEach((s) => list.appendChild(renderSessionItem(s)));
		} catch (e) {
			list.innerHTML = `<div class="triton-history-empty">${__("Couldn't load chat history.")}</div>`;
		}
	}

	function renderSessionItem(s) {
		const item = document.createElement("button");
		item.className = "triton-history-item";
		if (s.id === state.sessionId) item.classList.add("active");
		const title = (s.title || "").trim() || __("Untitled chat");
		let when = "";
		try {
			if (s.created_at && frappe.datetime && frappe.datetime.comment_when) {
				when = frappe.datetime.comment_when(s.created_at);
			}
		} catch (e) {}
		item.innerHTML =
			`<span class="triton-history-title">${esc(title)}</span>` +
			(when ? `<span class="triton-history-when">${esc(when)}</span>` : "");
		item.addEventListener("click", () => selectSession(s.id));
		return item;
	}

	async function selectSession(id) {
		if (state.streaming) return;
		state.sessionId = id;
		localStorage.setItem(LS_SESSION, String(id));
		state.contextRefs = [];
		renderChips();
		closeHistory();
		state.els.messages.innerHTML = `<div class="triton-empty">${__("Loading chat…")}</div>`;
		try {
			const msgs = await xcall("get_messages", { session_id: id, limit: 50 });
			state.messages_loaded = true;
			state.els.messages.innerHTML = "";
			if (!msgs || !msgs.length) {
				showEmpty();
			} else {
				msgs.forEach(renderHistoryMessage);
				scrollDown();
			}
			pulse(state.els.messages, "triton-fresh");
		} catch (e) {
			localStorage.removeItem(LS_SESSION);
			state.sessionId = null;
			showEmpty();
		}
	}

	// ---- morning briefing ------------------------------------------------
	function todayStr() {
		const d = new Date();
		return (
			d.getFullYear() +
			"-" +
			String(d.getMonth() + 1).padStart(2, "0") +
			"-" +
			String(d.getDate()).padStart(2, "0")
		);
	}

	function briefingShownToday() {
		return localStorage.getItem(LS_BRIEF) === todayStr();
	}

	// First open of the day: start a fresh chat and surface the user's Morning
	// Briefing as the opening assistant message. Prior conversations remain
	// reachable through the history picker.
	async function startDailyBriefing() {
		// Same guard as newChat(), and deliberately BEFORE the LS_BRIEF stamp below: if we
		// refuse to show the briefing we must not record that today's was shown, or the
		// user silently loses it until tomorrow. Clearing the transcript mid-stream has the
		// same detached-node consequence described on newChat().
		if (state.streaming) return;
		localStorage.setItem(LS_BRIEF, todayStr());
		state.sessionId = null;
		localStorage.removeItem(LS_SESSION);
		state.messages_loaded = true;
		state.contextRefs = [];
		renderChips();
		state.els.messages.innerHTML = "";

		const live = newAssistantMsg();
		live.wrap.classList.add("triton-briefing");
		setStatus(live, __("Preparing your morning briefing…"));
		try {
			const r = await xcall("morning_briefing");
			clearStatus(live);
			const text = (r && (r.briefing || r.content)) || "";
			if (text) {
				appendText(live, text);
			} else {
				live.wrap.remove();
				showEmpty();
			}
		} catch (e) {
			// Couldn't fetch — don't burn today's slot; let it retry next open.
			live.wrap.remove();
			showEmpty();
			localStorage.removeItem(LS_BRIEF);
		}
	}

	// ---- open / close ----------------------------------------------------
	function toggle(force) {
		state.open = typeof force === "boolean" ? force : !state.open;
		state.els.panel.classList.toggle("triton-visible", state.open);
		state.els.fab.classList.toggle("triton-fab-open", state.open);
		if (state.open) {
			suggestCurrentPage();
			state.els.text.focus();
			if (!briefingShownToday()) {
				// New day → fresh chat opening with the morning briefing.
				startDailyBriefing();
			} else if (!state.sessionId && state.messages_loaded !== true) {
				loadHistory();
			}
		} else {
			closeHistory();
		}
	}

	// ---- context chips ---------------------------------------------------
	function detectPageContext() {
		const route = frappe.get_route();
		if (!route || !route.length) return null;
		const r0 = route[0];
		const hash = "#" + (frappe.get_route_str ? frappe.get_route_str() : route.join("/"));

		if (r0 === "Form" && route[1] && route[2]) {
			const ref = {
				type: "document",
				doctype: route[1],
				name: route[2],
				title: `${route[1]}: ${route[2]}`,
				route: hash,
			};
			try {
				if (window.cur_frm && cur_frm.doc && cur_frm.docname === route[2] && cur_frm.is_dirty && cur_frm.is_dirty()) {
					ref.unsaved = true;
				}
			} catch (e) {}
			return ref;
		}
		if (r0 === "List" || r0 === "list") {
			const doctype = route[1];
			const view = route[2];
			let filters = null;
			try {
				if (window.cur_list && cur_list.get_filters_for_args) filters = cur_list.get_filters_for_args();
			} catch (e) {}
			if (view === "Report") {
				return { type: "report", report_name: doctype, name: doctype, filters, title: `${doctype} (Report)`, route: hash };
			}
			return { type: "list", doctype, filters, title: `${doctype} list`, route: hash };
		}
		if (r0 === "query-report" && route[1]) {
			let filters = null;
			try {
				if (frappe.query_report && frappe.query_report.get_filter_values) filters = frappe.query_report.get_filter_values();
			} catch (e) {}
			return { type: "report", report_name: route[1], name: route[1], filters, title: `Report: ${route[1]}`, route: hash };
		}
		return { type: "page", title: document.title.replace(/\s*\|.*/, "").trim() || r0, route: hash };
	}

	function refKey(r) {
		return [r.type, r.doctype, r.name, r.report_name, r.route].filter(Boolean).join("::");
	}

	function addCurrentPage() {
		const ref = detectPageContext();
		if (!ref) {
			frappe.show_alert({ message: __("Nothing to add from this page."), indicator: "orange" });
			return;
		}
		if (state.contextRefs.some((r) => refKey(r) === refKey(ref))) return;
		state.contextRefs.push(ref);
		renderChips();
	}

	function suggestCurrentPage() {
		// Surface a one-tap suggestion for the page you're on without auto-pinning.
		if (!state.config.enable_page_context) return;
		const ref = detectPageContext();
		state.els.contextAdd.textContent = ref && ref.title ? `＋ ${ref.title}` : "＋ Add this page";
	}

	function renderChips() {
		state.els.contextBar.querySelectorAll(".triton-chip").forEach((c) => c.remove());
		state.contextRefs.forEach((r, i) => {
			const chip = document.createElement("span");
			chip.className = "triton-chip";
			chip.innerHTML = `<span class="triton-chip-label">${esc(r.title || r.name || r.type)}</span><span class="triton-chip-x">✕</span>`;
			chip.querySelector(".triton-chip-x").addEventListener("click", () => {
				state.contextRefs.splice(i, 1);
				renderChips();
			});
			state.els.contextBar.appendChild(chip);
		});
	}

	// ---- message rendering ----------------------------------------------
	function clearEmpty() {
		const e = state.els.messages.querySelector(".triton-empty");
		if (e) e.remove();
	}

	function showEmpty() {
		state.els.messages.innerHTML = `
			<div class="triton-empty">
				<span class="triton-empty-icon">🔱</span>
				${__("Ask Triton anything about your business data.")}<br>
				<small>${__("Tip: pin the page you're on with “Add this page”.")}</small>
			</div>`;
	}

	function addUserMsg(text) {
		clearEmpty();
		const el = document.createElement("div");
		el.className = "triton-msg triton-user";
		el.innerHTML = esc(text).replace(/\n/g, "<br>");
		state.els.messages.appendChild(el);
		scrollDown();
	}

	function newAssistantMsg(streaming) {
		clearEmpty();
		const wrap = document.createElement("div");
		wrap.className = "triton-msg triton-assistant";
		wrap.innerHTML = `<div class="triton-bubble"></div>`;
		state.els.messages.appendChild(wrap);
		const live = {
			wrap,
			bubble: wrap.querySelector(".triton-bubble"),
			text: "",
			shownLen: 0, // chars currently revealed by the typewriter pump
			thoughts: "",
			streaming: !!streaming,
			statusEl: null,
			// tool/agent activity timeline
			stepsEl: null,
			lastStep: "",
			// live "thinking" disclosure
			thinkingDetails: null,
			thinkingEl: null,
			thinkingLabel: null,
			thinkingTimer: null,
			thinkStart: 0,
			thinkInterval: null,
			thinkingCollapsed: false,
			// raf handles
			pumpRaf: null,
			thoughtRaf: null,
			// ids the model cited that the manifest does not contain. A Set, per turn, for the
			// same reason `cited` is one: onMiss fires again on every streaming frame.
			missed: null,
			// --- phase 3 --- the citation manifest for this turn, or null. Null is the
			// shipped state until Phase 5 emits a `citations` event, and null means the
			// message renders exactly as it does today.
			manifest: null,
			// The ids the model actually cited in THIS answer. Accumulated across streaming
			// frames rather than recomputed, because `renderBubble` rebuilds the bubble from
			// scratch every frame and text only ever grows.
			cited: new Set(),
			// k -> chip element, so marking a chip is a lookup rather than a re-render.
			sourceChips: null,
		};
		scrollDown();
		return live;
	}

	// Transient one-liner ("Connecting to Triton…") shown above the bubble until
	// the first real event arrives. Tool activity uses the step timeline instead.
	function setStatus(live, content) {
		if (!live.statusEl) {
			live.statusEl = document.createElement("div");
			live.statusEl.className = "triton-status";
			live.wrap.insertBefore(live.statusEl, live.bubble);
		}
		live.statusEl.textContent = content;
		scrollDown();
	}

	function clearStatus(live) {
		if (live.statusEl) {
			live.statusEl.remove();
			live.statusEl = null;
		}
	}

	// ---- tool / agent step timeline -------------------------------------
	function ensureSteps(live) {
		if (!live.stepsEl) {
			live.stepsEl = document.createElement("div");
			live.stepsEl.className = "triton-steps";
			live.wrap.insertBefore(live.stepsEl, live.bubble);
		}
		return live.stepsEl;
	}

	function markActiveStepsDone(live) {
		if (!live.stepsEl) return;
		live.stepsEl.querySelectorAll(".triton-step.is-active").forEach((s) => {
			s.classList.remove("is-active");
			s.classList.add("is-done");
		});
	}

	// A live, ordered timeline of tool/agent activity. Each new status settles the
	// previous step (✓) and animates in a new active row, replacing the single
	// overwritten status line so multi-step runs stay legible.
	function pushStep(live, content) {
		content = (content || "").trim();
		if (!content || content === live.lastStep) return;
		markActiveStepsDone(live);
		live.lastStep = content;
		const row = document.createElement("div");
		row.className = "triton-step is-active";
		const dot = document.createElement("span");
		dot.className = "triton-step-dot";
		const txt = document.createElement("span");
		txt.className = "triton-step-text";
		txt.textContent = content;
		row.appendChild(dot);
		row.appendChild(txt);
		ensureSteps(live).appendChild(row);
		scrollDown();
	}

	// ---- streamed answer text (typewriter smoothing) --------------------
	function renderBubble(live) {
		// --- phase 3 --- hold back a partial citation token so `[[re` never flashes as
		// literal text before `f:7]]` arrives. `holdbackLength` is the pure tail-buffer
		// arithmetic from chat/citations.js; with no manifest the slice is unchanged and this
		// line is a no-op, which is what keeps streaming byte-for-byte identical to today.
		let visible = live.text.slice(0, live.shownLen);
		if (live.manifest && live.shownLen < live.text.length) {
			visible = visible.slice(0, visible.length - holdbackLength(visible));
		}
		live.bubble.innerHTML = md(visible);
		// --- phase 3 --- inline citations, applied to the markdown renderer's OUTPUT rather
		// than to its input. The sanitiser policy Appendix A records is load-bearing and is
		// therefore untouched: it sees exactly the string it saw before this phase, and the
		// anchors are added afterwards as DOM with createElement/textContent. With no
		// manifest applyCitations returns immediately.
		if (live.manifest) {
			applyCitations(live.bubble, live.manifest, {
				onMiss: (k) => noteCitationMiss(live, k),
				// Feeds the sources row's "cited" marking (approved 2026-08-10). A Set, because
				// this fires once per rendered token per frame and the bubble is rebuilt from
				// scratch on every frame.
				onCite: (k) => live.cited.add(k),
			});
			markCitedSources(live);
		}
		scrollDown();
	}

	// --- phase 3 --- a `citation_miss` is a token whose id is not in the manifest. Dropped
	// silently from the render (never a raw token, never a dead link) and counted here,
	// because a miss rate over ~2% means a prompt edit broke citing and the only symptom is
	// inline links quietly disappearing.
	//
	// **Counted per DISTINCT id per turn, not per callback.** `renderBubble` rebuilds the
	// bubble from scratch on every streaming frame, so `onMiss` fires again for the same bad
	// id on every frame — dozens of times on a long answer. An earlier revision incremented a
	// module counter directly, which meant ONE bad id on ONE turn tripped the "repeated
	// misses" warning within a second. A counter that fires on a single occurrence is not a
	// rate signal, it is a false alarm, and a false alarm is how the real one gets ignored.
	let _citationMisses = 0;
	function noteCitationMiss(live, k) {
		if (!live.missed) live.missed = new Set();
		live.missed.add(k);
	}

	// Folded into the module counter once, when the turn is complete and `live.missed` is
	// final. Called from finishStreaming.
	function tallyCitationMisses(live) {
		if (!live.missed || !live.missed.size) return;
		_citationMisses += live.missed.size;
		if (_citationMisses >= 10) {
			console.warn(
				"triton: repeated citation misses — the manifest and the answer disagree",
				{ this_turn: [...live.missed], total: _citationMisses }
			);
		}
	}

	function schedulePump(live) {
		if (live.pumpRaf == null) {
			live.pumpRaf = requestAnimationFrame(() => pumpText(live));
		}
	}

	// Reveal buffered characters at a steady, backlog-adaptive cadence so text
	// flows smoothly no matter how bursty the SSE chunks are.
	function pumpText(live) {
		live.pumpRaf = null;
		const remaining = live.text.length - live.shownLen;
		if (remaining > 0) {
			const step = Math.max(2, Math.min(60, Math.ceil(remaining / 3)));
			live.shownLen = Math.min(live.text.length, live.shownLen + step);
			renderBubble(live);
		}
		if (live.shownLen < live.text.length) schedulePump(live);
	}

	function appendText(live, content) {
		if (!content) return;
		// The answer has started — settle the reasoning + tool timeline.
		if (live.streaming) {
			collapseThinking(live);
			markActiveStepsDone(live);
		}
		live.text += content;
		if (live.streaming && !reducedMotion) {
			live.wrap.classList.add("triton-streaming");
			schedulePump(live);
		} else {
			live.shownLen = live.text.length;
			renderBubble(live);
			renderMermaidIn(live.bubble);
		}
	}

	// Called on done/error: flush remaining text instantly and drop the cursor.
	function finishStreaming(live) {
		if (live.pumpRaf != null) {
			cancelAnimationFrame(live.pumpRaf);
			live.pumpRaf = null;
		}
		if (live.thoughtRaf != null) {
			cancelAnimationFrame(live.thoughtRaf);
			live.thoughtRaf = null;
		}
		// This line is also the citation tail-buffer FLUSH: `renderBubble` only holds back a
		// partial token while `shownLen < text.length`, so setting them equal releases
		// everything held. Forgetting the flush truncates the last few characters of every
		// answer that happens to end near a token, which is maddening to diagnose.
		live.shownLen = live.text.length;
		if (live.thinkingEl) live.thinkingEl.innerHTML = md(live.thoughts);
		collapseThinking(live);
		markActiveStepsDone(live);
		renderBubble(live);
		// --- phase 3 --- the ONE reorder of the sources row, here and nowhere else. Marking
		// happens live (a chip lighting up as the model leans on it is worth watching);
		// reordering live would reshuffle the row several times a second and move a chip the
		// reader was about to click. By this point the answer is complete, so `live.cited` is
		// final and the order is stable.
		if (live.manifest) renderManifestSources(live, { sortCitedFirst: true });
		tallyCitationMisses(live);
		renderMermaidIn(live.bubble);
		live.wrap.classList.remove("triton-streaming");
		live.streaming = false;
	}

	// ---- live "thinking" disclosure -------------------------------------
	function ensureThinking(live) {
		if (live.thinkingDetails) return live.thinkingDetails;
		const d = document.createElement("details");
		d.className = "triton-thinking";
		d.open = !!live.streaming; // auto-expand while the model is actively thinking
		d.innerHTML =
			'<summary><span class="triton-think-label"></span>' +
			'<span class="triton-think-timer"></span></summary>' +
			'<div class="triton-thinking-body"></div>';
		live.wrap.insertBefore(d, live.bubble);
		live.thinkingDetails = d;
		live.thinkingEl = d.querySelector(".triton-thinking-body");
		live.thinkingLabel = d.querySelector(".triton-think-label");
		live.thinkingTimer = d.querySelector(".triton-think-timer");
		live.thinkingLabel.textContent = live.streaming ? __("Thinking") : __("Thoughts");
		if (live.streaming) {
			live.thinkStart = Date.now();
			live.thinkInterval = setInterval(() => {
				if (!live.thinkingTimer) return;
				const s = Math.round((Date.now() - live.thinkStart) / 1000);
				live.thinkingTimer.textContent = s > 0 ? " " + s + "s" : "";
			}, 500);
		}
		return d;
	}

	function appendThought(live, content) {
		if (!content) return;
		live.thoughts += content;
		ensureThinking(live);
		// Coalesce the markdown re-render of the growing thought text to one/frame.
		if (live.thoughtRaf == null) {
			live.thoughtRaf = requestAnimationFrame(() => {
				live.thoughtRaf = null;
				if (live.thinkingEl) live.thinkingEl.innerHTML = md(live.thoughts);
				scrollDown();
			});
		}
	}

	// Collapse the disclosure once the answer begins (or on stream end) and swap
	// the live "Thinking 3s" header for a settled "Thought for 5s".
	function collapseThinking(live) {
		if (!live.thinkingDetails || live.thinkingCollapsed) return;
		live.thinkingCollapsed = true;
		live.thinkingDetails.open = false;
		live.thinkingDetails.classList.add("triton-thinking-done");
		if (live.thinkInterval) {
			clearInterval(live.thinkInterval);
			live.thinkInterval = null;
		}
		const secs = live.thinkStart
			? Math.max(1, Math.round((Date.now() - live.thinkStart) / 1000))
			: 0;
		if (live.thinkingLabel) {
			live.thinkingLabel.textContent = secs
				? __("Thought for") + " " + secs + "s"
				: __("Thoughts");
		}
		if (live.thinkingTimer) live.thinkingTimer.textContent = "";
	}

	// UNCHANGED from before Phase 3, deliberately and byte for byte. This is the path taken
	// whenever there is no citation manifest — which is every site until Phase 5 emits one,
	// and every turn where the retrieval produced nothing. Decision #7's "preserve exactly"
	// still governs it.
	function renderSources(container, sources) {
		if (!sources || !sources.length) return;
		const box = document.createElement("div");
		box.className = "triton-sources";
		sources.forEach((s) => {
			const label = s.label || s.title || s.url || "source";
			let a;
			if (s.url) {
				a = document.createElement("a");
				a.href = s.url;
				a.target = "_blank";
				a.rel = "noopener";
			} else {
				a = document.createElement("span");
			}
			a.className = "triton-source";
			a.textContent = label;
			a.title = label;
			box.appendChild(a);
		});
		container.appendChild(box);
	}

	// --- phase 3 --- the manifest-backed sources row: every retrieved entry, with the ones
	// the model actually cited MARKED and SORTED FIRST.
	//
	// **This is an approved change to a "preserve exactly" surface.** Locked decision #7 says
	// the sources dropdown is preserved; research 03 §12.6 proposed exactly this instead and
	// required an explicit human yes rather than a unilateral edit. Raised at the Phase 3
	// checkpoint, approved 2026-08-10.
	//
	// It renders into the same `.triton-sources` container with the same `.triton-source`
	// chips, so the existing "already rendered?" guard in the `done` handler still works and
	// no CSS moves. What is added is a `[k]` marker matching the inline `[k]` in the answer,
	// an `is-cited` class, and the ordering.
	//
	// Same node-building discipline as everywhere else in chat: labels and snippets are
	// user-authored — a document title, a coworker's message, a filename — so every one of
	// them goes in through `textContent`.
	function renderManifestSources(live, opts) {
		if (!live.manifest || !live.manifest.size) return;

		let box = live.wrap.querySelector(".triton-sources");
		if (!box) {
			box = document.createElement("div");
			box.className = "triton-sources";
			// Appended only when NEWLY created, never repositioned on a rebuild.
			//
			// An earlier revision of this function moved the box to the end on every call, on
			// the belief that the pre-Phase-3 row was always last. It was not: the `done`
			// handler runs `renderSources()` and only THEN `renderChart()`, so the old row sat
			// *above* the chart — and `renderHistoryMessage` does the same. Forcing it to the
			// end therefore moved it, and made a live turn disagree with the same turn
			// re-opened from history. Creating it in place gives it the slot the old row had,
			// in both paths.
			live.wrap.appendChild(box);
		}
		box.classList.add("triton-sources-manifest");
		box.textContent = "";
		live.sourceChips = new Map();

		// `sortCitedFirst` only at the end of the turn. `live.cited` grows with every frame,
		// so sorting on it mid-stream — which `citations_append` did — reshuffles chips the
		// reader may be about to click, once per append. The MARKS stay accurate throughout;
		// only the order waits.
		const sortCitedFirst = !!(opts && opts.sortCitedFirst);
		for (const entry of orderManifestForDisplay(live.manifest, live.cited, { sortCitedFirst })) {
			const citation = entry.citation || {};
			const label = citationLabel(citation);
			const linkable = isSafeUrl(citation.url);

			const chip = document.createElement(linkable ? "a" : "span");
			chip.className = "triton-source" + (entry.cited ? " is-cited" : "");
			if (linkable) {
				chip.href = citation.url;
				// Same as the pre-Phase-3 chip: new tab, noopener. `noreferrer` is added for
				// the external case only, matching the inline-citation renderer.
				chip.target = "_blank";
				chip.rel = /^https?:\/\//i.test(String(citation.url)) ? "noopener noreferrer" : "noopener";
			}

			const marker = document.createElement("span");
			marker.className = "triton-source-k";
			marker.textContent = String(entry.k);
			chip.appendChild(marker);
			chip.appendChild(document.createTextNode(label));
			chip.title = citation.snippet ? `${label} — ${citation.snippet}` : label;

			box.appendChild(chip);
			live.sourceChips.set(entry.k, chip);
		}
	}

	// Mark newly-cited chips as the answer streams, WITHOUT reordering.
	//
	// The split is the point. Marking live is informative — a chip lighting up as the model
	// leans on it is the thing worth watching. Reordering live is not: the row would reshuffle
	// under the reader's eyes several times a second, and a chip they were about to click
	// would move. So the sort happens exactly once, in `finishStreaming`.
	function markCitedSources(live) {
		if (!live.sourceChips || !live.cited) return;
		for (const k of live.cited) {
			const chip = live.sourceChips.get(k);
			if (chip) chip.classList.add("is-cited");
		}
	}

	function renderActionCard(container, params, opts) {
		opts = opts || {};
		const card = document.createElement("div");
		card.className = "triton-action-card" + (params.risk === "high" ? " triton-risk-high" : "");
		const summary = esc(params.summary || params.tool_name || "Proposed action");
		const desc = esc(params.description || "");
		card.innerHTML = `
			<div class="triton-action-summary">${summary}</div>
			${desc ? `<div class="triton-action-desc">${desc}</div>` : ""}
			<div class="triton-action-slot"></div>`;
		const slot = card.querySelector(".triton-action-slot");

		const liveStatus = opts.liveStatus || "pending";
		if (liveStatus === "pending") {
			const btns = document.createElement("div");
			btns.className = "triton-action-btns";
			btns.innerHTML = `
				<button class="triton-approve">${__("Approve")}</button>
				<button class="triton-decline">${__("Decline")}</button>`;
			btns.querySelector(".triton-approve").addEventListener("click", () => decideAction(params, true, slot));
			btns.querySelector(".triton-decline").addEventListener("click", () => decideAction(params, false, slot));
			slot.appendChild(btns);
		} else {
			renderResolved(slot, liveStatus);
		}
		container.appendChild(card);
		scrollDown();
	}

	function renderResolved(slot, status) {
		const ok = status === "confirmed" || status === "executed" || status === "approved";
		slot.innerHTML = `<span class="triton-action-resolved ${ok ? "ok" : "no"}">${
			ok ? "✓ " + __("Approved") : (status === "expired" ? __("Expired") : "✕ " + __("Declined"))
		}</span>`;
	}

	async function decideAction(params, approve, slot) {
		slot.innerHTML = `<span class="text-muted">${approve ? __("Approving…") : __("Declining…")}</span>`;
		try {
			const fn = approve ? "confirm_action" : "cancel_action";
			await xcall(fn, { action_id: params.action_id, session_id: state.sessionId });
			renderResolved(slot, approve ? "confirmed" : "cancelled");
			if (approve) {
				// Fire a hidden continuation so Triton runs the now-approved action
				// and reports the result, mirroring the Triton web app.
				send(__("The proposed action was approved. Please proceed."), { hidden: true });
			}
		} catch (e) {
			slot.innerHTML = `<span class="triton-action-resolved no">${__("Failed")}: ${esc(e.message || e)}</span>`;
		}
	}

	// ---- history ---------------------------------------------------------
	async function loadHistory() {
		const saved = localStorage.getItem(LS_SESSION);
		if (!saved) {
			showEmpty();
			state.messages_loaded = true;
			return;
		}
		state.sessionId = parseInt(saved, 10);
		try {
			const msgs = await xcall("get_messages", { session_id: state.sessionId, limit: 50 });
			state.messages_loaded = true;
			if (!msgs || !msgs.length) {
				showEmpty();
				return;
			}
			state.els.messages.innerHTML = "";
			msgs.forEach(renderHistoryMessage);
			scrollDown();
		} catch (e) {
			// Session gone server-side — reset.
			localStorage.removeItem(LS_SESSION);
			state.sessionId = null;
			showEmpty();
			state.messages_loaded = true;
		}
	}

	function renderHistoryMessage(m) {
		const meta = m.ui_metadata || {};
		if (meta.system_note) return; // hidden continuation turns
		if (m.role === "user") {
			addUserMsg(m.content);
			return;
		}
		const live = newAssistantMsg();
		// --- phase 3 --- a stored turn carries its manifest, so re-opening a conversation
		// renders the same inline links it had while streaming. Set BEFORE appendText, which
		// renders. Absent on every turn stored before Phase 5 ships, and absent is today's
		// behaviour rather than an error.
		if (meta.citations && meta.citations.length) live.manifest = indexManifest(meta.citations);
		appendText(live, m.content || "");
		if (meta.thinking) {
			appendThought(live, meta.thinking);
		}
		// --- phase 3 --- `appendText` renders synchronously for a stored turn (no streaming,
		// so `shownLen` is set to the full length and `renderBubble` runs once), which means
		// `live.cited` is already populated by the time we get here. So a re-opened
		// conversation shows the same marked-and-sorted row it ended with, rather than an
		// unmarked one — the ordering is a property of the answer, not of the session.
		if (live.manifest && live.manifest.size) renderManifestSources(live, { sortCitedFirst: true });
		else if (meta.sources) renderSources(live.wrap, meta.sources);
		if (meta.direct_chart) renderChart(live.wrap, meta.direct_chart);
		(meta.pending_actions || []).forEach((p) =>
			renderActionCard(live.wrap, p, { liveStatus: p.live_status || "pending" })
		);
	}

	// ---- sending / streaming --------------------------------------------
	function newChat() {
		// Refuse mid-stream, exactly as selectSession() and send() do. Without this,
		// clicking the new-chat pencil while an answer is streaming clears
		// messages.innerHTML below while pumpText keeps writing into the now-detached
		// live.wrap: the caret animates on a node nobody can see, finishStreaming runs
		// against a dead subtree, and the turn is still persisted server-side against the
		// session the user just abandoned. Nothing throws, so nothing reports it.
		//
		// scripts/test_triton_widget_guards.js asserts every transcript-clearing function
		// carries this line, because this is the third such function and the guard was
		// missing from two of them.
		if (state.streaming) return;
		state.sessionId = null;
		localStorage.removeItem(LS_SESSION);
		state.contextRefs = [];
		renderChips();
		closeHistory();
		state.els.messages.innerHTML = "";
		showEmpty();
		// Animate the freshly cleared canvas so the reset feels deliberate.
		pulse(state.els.messages, "triton-fresh");
	}

	async function ensureSession() {
		if (state.sessionId) return state.sessionId;
		const s = await xcall("start_session", {
			model: state.model || state.config.default_model,
			persona_key: state.persona || "",
		});
		state.sessionId = s.id;
		localStorage.setItem(LS_SESSION, String(s.id));
		return state.sessionId;
	}

	function onSend() {
		const text = state.els.text.value.trim();
		if (!text || state.streaming) return;
		state.els.text.value = "";
		autoGrow();
		send(text, {});
	}

	async function send(text, opts) {
		opts = opts || {};
		if (state.streaming) return;
		state.streaming = true;
		state.els.send.disabled = true;

		if (!opts.hidden) addUserMsg(text);
		const live = newAssistantMsg(true);
		state.live = live;
		setStatus(live, __("Connecting to Triton…"));

		try {
			await ensureSession();
			await runStream(text, live, opts);
			// Context is consumed once it has informed a turn; clear chips so it
			// isn't silently re-sent on every subsequent message.
			if (!opts.hidden && state.contextRefs.length) {
				state.contextRefs = [];
				renderChips();
			}
		} catch (e) {
			clearStatus(live);
			live.text += `\n\n*${__("Error")}: ${esc(e.message || e)}*`;
			finishStreaming(live);
		} finally {
			state.streaming = false;
			state.els.send.disabled = false;
		}
	}

	async function runStream(text, live, opts) {
		const res = await fetch(`/api/method/${METHOD}.stream_query`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				"X-Frappe-CSRF-Token": frappe.csrf_token,
				Accept: "text/event-stream",
			},
			body: JSON.stringify({
				session_id: state.sessionId,
				prompt: text,
				context: opts.hidden ? "[]" : JSON.stringify(state.contextRefs),
				hidden: opts.hidden ? 1 : 0,
				// Per-message model override; "" lets Triton auto-route.
				model: state.model || "",
				// Per-message persona; "" means the plain Triton voice.
				persona_key: state.persona || "",
			}),
		});

		if (!res.ok || !res.body) {
			throw new Error(`HTTP ${res.status}`);
		}

		const reader = res.body.getReader();
		const decoder = new TextDecoder();
		let buffer = "";
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });
			let idx;
			while ((idx = buffer.indexOf("\n\n")) >= 0) {
				const frame = buffer.slice(0, idx);
				buffer = buffer.slice(idx + 2);
				handleFrame(frame, live);
			}
		}
	}

	function handleFrame(frame, live) {
		const dataLines = frame
			.split("\n")
			.filter((l) => l.startsWith("data:"))
			.map((l) => l.slice(5).trim());
		if (!dataLines.length) return;
		let ev;
		try {
			ev = JSON.parse(dataLines.join("\n"));
		} catch (e) {
			return;
		}
		handleEvent(ev, live);
	}

	function handleEvent(ev, live) {
		switch (ev.type) {
			case "tool_status":
				clearStatus(live);
				pushStep(live, ev.content || "");
				break;
			case "agent_spawn":
				clearStatus(live);
				pushStep(live, (ev.label || ev.agent || __("Agent")) + " " + __("working…"));
				break;
			case "thought":
				clearStatus(live);
				appendThought(live, ev.content || "");
				break;
			case "text":
				clearStatus(live);
				appendText(live, ev.content || "");
				break;
			case "sources":
				// The manifest wins when there is one. It is a superset of this list — every
				// retrieved item, with ids — so rendering both would show the same sources
				// twice, and the manifest is the version the human approved on 2026-08-10
				// (marked and sorted). With no manifest this is the pre-Phase-3 path, and it
				// renders exactly as it did before, which is what decision #7 protects.
				if (live.manifest && live.manifest.size) break;
				if (ev.content) renderSources(live.wrap, ev.content);
				break;
			// --- phase 3 --- the citation manifest. Arrives BEFORE any token (that ordering is
			// what makes streaming citations possible at all: the ids are assigned at
			// context-assembly time, so the manifest is known before generation starts).
			// `citations_append` extends the SAME integer space from a mid-turn tool call —
			// one id space, one renderer, no special cases.
			case "citations":
				live.manifest = indexManifest(ev.content || ev.citations || []);
				// Painted immediately, before a single token: the sources row is the reader's
				// answer to "what is it about to look at", and it is known before generation
				// starts. Every chip starts unmarked and lights up as the answer cites it.
				renderManifestSources(live);
				renderBubble(live);
				break;
			case "citations_append": {
				const extra = indexManifest(ev.content || ev.citations || []);
				if (!live.manifest) live.manifest = extra;
				else for (const [k, entry] of extra) live.manifest.set(k, entry);
				// A mid-turn tool call added entries. Re-render the row rather than appending,
				// so a late arrival lands in id order instead of after everything.
				renderManifestSources(live);
				renderBubble(live);
				break;
			}
			case "pending_action":
				if (ev.params) renderActionCard(live.wrap, ev.params, { liveStatus: "pending" });
				break;
			case "ui_command":
				if (ev.command === "render_chart") {
					renderChart(live.wrap, ev.params);
				} else if (ev.command === "render_visualization" || ev.command === "render_3d_simulation") {
					renderVizFallback(live.wrap, ev.command, ev.params);
				}
				// voice_dial / show_native_plan_approval are Desk-side actions and
				// are intentionally not surfaced in the embedded widget.
				break;
			case "done": {
				clearStatus(live);
				const meta = ev.ui_metadata || {};
				if (typeof ev.content === "string" && ev.content && !live.text) {
					live.text = ev.content;
				}
				if (meta.sources && !live.wrap.querySelector(".triton-sources")) {
					renderSources(live.wrap, meta.sources);
				}
				if (meta.direct_chart && !live.wrap.querySelector(".triton-chart")) {
					renderChart(live.wrap, meta.direct_chart);
				}
				finishStreaming(live);
				break;
			}
			case "error":
				clearStatus(live);
				live.text += `\n\n*${esc(ev.content || "Error")}*`;
				finishStreaming(live);
				break;
			default:
				break;
		}
	}

	// ---- bootstrap -------------------------------------------------------
	let _booted = false;
	async function init() {
		if (_booted) return; // Desk is a SPA; build the widget exactly once.
		if (!window.frappe || !frappe.xcall || !frappe.session || frappe.session.user === "Guest") return;
		_booted = true;
		let cfg;
		try {
			cfg = await xcall("get_config");
		} catch (e) {
			_booted = false; // allow a retry once the app is fully ready
			return;
		}
		if (!cfg || !cfg.enabled) return;
		state.config = cfg;
		build();
		showEmpty();

		// --- phase 3 --- the coworker half. Both calls are guarded on `frappe.boot.ee_chat`
		// and both fail closed, so a site with chat off boots exactly as before.
		if (chatEnabled()) {
			// The badge ships to every Desk page, so the room list is fetched once per page
			// load and nothing else. It is the only chat call a user who never opens the
			// bubble ever makes.
			ensureChatSurface()
				.ensureLoaded()
				.catch(() => {});
			// The reverse handoff: if the SPA (or a previous bubble session in this tab) left a
			// coworker conversation open, come back to it.
			restoreFromHandoff();
		}
	}

	$(document).on("app_ready", init);
	// Fallbacks: app_ready may have already fired before this script ran.
	$(() => setTimeout(init, 1500));
})();
