/**
 * Triton embedded assistant widget.
 *
 * A floating trident button on every ERPNext desk page that opens a chat panel
 * wired to Triton. All traffic goes through same-origin whitelisted methods on
 * `erpnext_enhancements.triton_chat` (no CORS, no client-side secrets). The chat
 * stream is relayed back as SSE and rendered token-by-token. Users can pin the
 * page they're on (document / list / report) as context, and Triton's proposed
 * ERPNext changes arrive as confirmation cards.
 */
(function () {
	const METHOD = "erpnext_enhancements.triton_chat";
	const LS_SESSION = "triton_session_id";
	const LS_MODEL = "triton_model";
	// Local date (YYYY-MM-DD) the morning briefing was last shown, so it appears
	// once on the first chat of each day.
	const LS_BRIEF = "triton_briefing_date";

	const state = {
		config: null,
		sessionId: null,
		// Selected model id ("" = let Triton auto-route). Persisted in LS_MODEL.
		model: "",
		contextRefs: [],
		open: false,
		streaming: false,
		els: {},
		// The assistant message currently being streamed.
		live: null,
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

	// ---- DOM construction ------------------------------------------------
	function build() {
		const fab = document.createElement("button");
		fab.className = "triton-fab";
		fab.title = "Ask Triton (Alt+T)";
		fab.textContent = "🔱";
		fab.addEventListener("click", toggle);
		document.body.appendChild(fab);

		const panel = document.createElement("div");
		panel.className = "triton-panel";
		panel.innerHTML = `
			<div class="triton-header">
				<span class="triton-logo">🔱</span>
				<span class="triton-title">Triton</span>
				<select class="triton-model-select" title="Choose model"></select>
				<button class="triton-icon-btn triton-history" title="Chat history">🕘</button>
				<button class="triton-icon-btn triton-new" title="New chat">✎</button>
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
			</div>`;
		document.body.appendChild(panel);

		state.els = {
			fab,
			panel,
			messages: panel.querySelector(".triton-messages"),
			contextBar: panel.querySelector(".triton-context-bar"),
			contextAdd: panel.querySelector(".triton-context-add"),
			text: panel.querySelector(".triton-text"),
			send: panel.querySelector(".triton-send"),
			modelSelect: panel.querySelector(".triton-model-select"),
			historyBtn: panel.querySelector(".triton-history"),
			historyPanel: panel.querySelector(".triton-history-panel"),
			historyList: panel.querySelector(".triton-history-list"),
			historyBack: panel.querySelector(".triton-history-back"),
		};

		populateModels();
		refreshModels(); // replace the fallback list with Triton's live models
		panel.querySelector(".triton-close").addEventListener("click", () => toggle(false));
		panel.querySelector(".triton-new").addEventListener("click", newChat);
		state.els.historyBtn.addEventListener("click", openHistory);
		state.els.historyBack.addEventListener("click", closeHistory);
		state.els.modelSelect.addEventListener("change", (e) => setModel(e.target.value));
		state.els.contextAdd.addEventListener("click", addCurrentPage);
		state.els.send.addEventListener("click", onSend);
		state.els.text.addEventListener("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				onSend();
			}
		});
		state.els.text.addEventListener("input", autoGrow);

		document.addEventListener("keydown", (e) => {
			if (e.altKey && (e.key === "t" || e.key === "T")) {
				e.preventDefault();
				toggle();
			}
		});

		if (!state.config.enable_page_context) {
			state.els.contextAdd.style.display = "none";
		}
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

	function newAssistantMsg() {
		clearEmpty();
		const wrap = document.createElement("div");
		wrap.className = "triton-msg triton-assistant";
		wrap.innerHTML = `<div class="triton-bubble"></div>`;
		state.els.messages.appendChild(wrap);
		const live = {
			wrap,
			bubble: wrap.querySelector(".triton-bubble"),
			text: "",
			thoughts: "",
			statusEl: null,
			thinkingEl: null,
		};
		scrollDown();
		return live;
	}

	function setStatus(live, content) {
		if (!live.statusEl) {
			live.statusEl = document.createElement("div");
			live.statusEl.className = "triton-status";
			live.wrap.appendChild(live.statusEl);
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

	function appendText(live, content) {
		live.text += content;
		live.bubble.innerHTML = md(live.text);
		scrollDown();
	}

	function appendThought(live, content) {
		live.thoughts += content;
		if (!live.thinkingEl) {
			const d = document.createElement("details");
			d.className = "triton-thinking";
			d.innerHTML = `<summary>${__("Thinking…")}</summary><div class="triton-thinking-body"></div>`;
			live.wrap.appendChild(d);
			live.thinkingEl = d.querySelector(".triton-thinking-body");
		}
		live.thinkingEl.textContent = live.thoughts;
		scrollDown();
	}

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
		appendText(live, m.content || "");
		if (meta.thinking) {
			appendThought(live, meta.thinking);
		}
		if (meta.sources) renderSources(live.wrap, meta.sources);
		(meta.pending_actions || []).forEach((p) =>
			renderActionCard(live.wrap, p, { liveStatus: p.live_status || "pending" })
		);
	}

	// ---- sending / streaming --------------------------------------------
	function newChat() {
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
		const s = await xcall("start_session", { model: state.model || state.config.default_model });
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
		const live = newAssistantMsg();
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
			appendText(live, `\n\n*${__("Error")}: ${esc(e.message || e)}*`);
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
				setStatus(live, ev.content || "");
				break;
			case "thought":
				appendThought(live, ev.content || "");
				break;
			case "text":
				clearStatus(live);
				appendText(live, ev.content || "");
				break;
			case "sources":
				if (ev.content) renderSources(live.wrap, ev.content);
				break;
			case "pending_action":
				if (ev.params) renderActionCard(live.wrap, ev.params, { liveStatus: "pending" });
				break;
			case "done": {
				clearStatus(live);
				const meta = ev.ui_metadata || {};
				if (typeof ev.content === "string" && ev.content && !live.text) {
					appendText(live, ev.content);
				}
				if (meta.sources && !live.wrap.querySelector(".triton-sources")) {
					renderSources(live.wrap, meta.sources);
				}
				break;
			}
			case "error":
				clearStatus(live);
				appendText(live, `\n\n*${esc(ev.content || "Error")}*`);
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
	}

	$(document).on("app_ready", init);
	// Fallbacks: app_ready may have already fired before this script ran.
	$(() => setTimeout(init, 1500));
})();
