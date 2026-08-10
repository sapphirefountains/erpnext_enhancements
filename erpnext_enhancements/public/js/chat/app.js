/**
 * The SPA shell: layout, router, and the wiring between every module in this directory.
 *
 * The interesting logic is deliberately NOT here — routing rules, restoration precedence,
 * optimistic reconciliation, the read batcher, typing, presence and the citation renderer all
 * live in their own modules as pure functions with `node` tests, because this repo has no
 * Frappe integration-test job and the bench-free tier is the only one with automatic
 * regression protection. What is left in this file is composition: fetch, render, listen.
 *
 * Reading order: `mount()` at the bottom, then `openRoom`, then the composer.
 */

import { applyCitations, indexManifest } from "./citations.js";
import { clear, el, fill, initials, isComposingKey, relativeTime, shouldGroup } from "./dom.js";
import { clearHandoff, readHandoff, writeHandoff } from "./handoff.js";
import * as mentions from "./mentions.js";
import { renderDaySeparator, renderMessage, renderThinkingPlaceholder } from "./message_view.js";
import { OfflineQueue, OutboxStore, STATE, newClientMessageId, mergeForRender } from "./optimistic.js";
import { KIND, buildRoute, degrade, parseRoute, resolveRestoration } from "./routes.js";
import {
	ReadBatcher,
	TypingRegistry,
	TypingThrottle,
	typingLabel,
} from "./signals.js";
import { ChatSocket } from "./socket.js";
import { M, boot, call, upload } from "./transport.js";
import { VirtualTranscript } from "./virtual_list.js";

/** Below this width the SPA is a push-navigation stack rather than three panes. */
const NARROW_PX = 768;

/** How often the handoff record is rewritten while the user is just scrolling and typing. */
const HANDOFF_THROTTLE_MS = 500;

/** Client-side room-switch throttle for `set_last_open_room`. The server throttles too. */
const LAST_OPEN_THROTTLE_MS = 30000;

class ChatApp {
	constructor(root) {
		this.root = root;
		this.boot = boot();
		this.me = this.boot.user;
		this.state = {
			rooms: [],
			roomsById: new Map(),
			room: null,
			roomDetail: null,
			messages: [],
			marks: [],
			presence: {},
			thread: null,
			threadMessages: [],
			unread: { rooms: [], total_unread: 0, total_mentions: 0 },
			flags: {},
			avatarLimit: 10,
			pageSize: 50,
			hasMore: true,
			search: null,
			pane: "list",
			connection: "connecting",
			citationMisses: 0,
			citationTokens: 0,
		};

		this.outbox = new OutboxStore();
		this.offline = new OfflineQueue();
		this.readBatcher = new ReadBatcher();
		this.typingOut = new TypingThrottle();
		this.typingIn = new TypingRegistry();
		this.clientId = ensureClientId();
		this.drafts = new Map();
		this.pendingUploads = [];
		this.mentionState = { open: false, targets: [], index: 0, trigger: null };
		this.composerMentions = [];
		this._lastHandoffAt = 0;
		this._lastOpenSentAt = 0;
		this._reducedMotion =
			!!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
	}

	// ------------------------------------------------------------------ lifecycle

	async mount() {
		performance.mark("ee-chat-shell-start");
		this.buildLayout();
		performance.mark("ee-chat-shell-painted");
		performance.measure("ee-chat-shell", "ee-chat-shell-start", "ee-chat-shell-painted");

		this.socket = new ChatSocket({
			onEvent: (name, payload) => this.onRealtime(name, payload),
			onResync: (reason) => this.resync(reason),
			onStatus: (status) => this.setConnection(status),
		});

		let bootstrap;
		try {
			bootstrap = await call(M.BOOTSTRAP);
		} catch (err) {
			this.fatal(err);
			return;
		}

		this.state.rooms = bootstrap.rooms || [];
		this.indexRooms();
		this.state.flags = bootstrap.flags || {};
		this.state.avatarLimit = bootstrap.receipt_avatar_member_limit || 10;
		this.state.pageSize = bootstrap.page_size || 50;
		this.state.unread = {
			rooms: [],
			total_unread: bootstrap.total_unread || 0,
			total_mentions: 0,
		};
		this.renderRoomList();

		this.socket.connect();
		this.startHeartbeat();
		this.bindGlobalEvents();

		await this.routeTo(location.pathname, location.search, {
			handoff: readHandoff({ session: sessionStorage, local: localStorage }, Date.now()),
			bootHint: bootstrap.last_open || null,
			replace: true,
		});
	}

	/**
	 * Resolve where to land, apply the three-tier precedence, and open it.
	 *
	 * The precedence itself is `routes.resolveRestoration` — a pure function with its own
	 * tests — so the rule that "the URL wins, always" cannot be quietly weakened by an edit
	 * to this method. A deep link from a notification, a citation or a pasted URL must never
	 * be overridden by restored state, and it is the tier people get wrong.
	 */
	async routeTo(pathname, search, options) {
		const opts = options || {};
		const route = parseRoute(pathname, search);
		let resolution = resolveRestoration(route, opts.handoff || null, opts.bootHint || null, (room) =>
			// Rooms we have not loaded are optimistically readable: the room list only carries
			// active rooms, and a deep link into an archived one is legitimate. The server is
			// the authority and a 403 degrades below.
			true
		);

		if (resolution.kind === KIND.SEARCH) {
			this.openSearch(route.query || "", { replace: opts.replace });
			return;
		}
		if (resolution.kind === KIND.TRITON) {
			this.openTriton({ replace: opts.replace });
			return;
		}
		if (!resolution.room) {
			this.showEmptyState();
			return;
		}

		try {
			await this.openRoom(resolution.room, {
				message: resolution.message,
				thread: resolution.thread,
				draft: resolution.draft,
				anchorRatio: resolution.anchorRatio,
				replace: opts.replace,
			});
		} catch (err) {
			// §4.3's failure behaviour: fall down the tiers, one at a time, with a single quiet
			// inline notice. Never a blank screen and never a modal.
			resolution = degrade(resolution);
			this.notice("That conversation is no longer available.");
			if (resolution.room) {
				try {
					await this.openRoom(resolution.room, { replace: true });
					return;
				} catch (e) {
					/* fall through to the list */
				}
			}
			clearHandoff({ session: sessionStorage, local: localStorage });
			this.showEmptyState();
		}
	}

	// ------------------------------------------------------------------ layout

	buildLayout() {
		clear(this.root);

		this.els = {};
		this.els.sidebar = el("nav", { class: "ee-sidebar", "aria-label": "Conversations" }, [
			el("div", { class: "ee-sidebar-head" }, [
				el("h1", { class: "ee-sidebar-title", text: "Chat" }),
				(this.els.markAll = el("button", {
					class: "ee-link-btn",
					type: "button",
					text: "Mark all read",
					onclick: () => this.markAllRead(),
				})),
			]),
			el("div", { class: "ee-sidebar-search" }, [
				(this.els.globalSearch = el("input", {
					class: "ee-input",
					type: "search",
					placeholder: "Search messages",
					"aria-label": "Search all conversations",
					onkeydown: (ev) => {
						// Same IME rule as the composers: Enter commits a candidate before it
						// submits anything. Searching on a half-typed word is milder than
						// sending one, and it is the same one-line guard.
						if (isComposingKey(ev)) return;
						if (ev.key === "Enter") this.openSearch(ev.target.value.trim());
					},
				})),
			]),
			(this.els.roomList = el("ul", { class: "ee-room-list", role: "list" })),
			el("div", { class: "ee-sidebar-foot" }, [
				(this.els.archivedToggle = el("button", {
					class: "ee-link-btn",
					type: "button",
					text: "Show archived",
					onclick: () => this.toggleArchived(),
				})),
			]),
		]);

		this.els.header = el("header", { class: "ee-conv-head" }, [
			(this.els.backBtn = el("button", {
				class: "ee-icon-btn ee-back",
				type: "button",
				"aria-label": "Back to conversations",
				text: "‹",
				onclick: () => this.showPane("list"),
			})),
			(this.els.roomTitle = el("h2", { class: "ee-conv-title", text: "" })),
			(this.els.roomSubtitle = el("div", { class: "ee-conv-subtitle" })),
			(this.els.membersBtn = el("button", {
				class: "ee-icon-btn",
				type: "button",
				title: "Members",
				"aria-label": "Members",
				text: "👥",
				onclick: () => this.toggleMembers(),
			})),
		]);

		this.els.viewport = el("div", { class: "ee-transcript", tabindex: "0" });
		this.els.canvas = el("div", { class: "ee-transcript-canvas", role: "log" });
		// aria-live polite + additions, and announcements are throttled by only announcing when
		// the transcript is not focused (§4.11): a busy room must not read every message aloud
		// continuously.
		this.els.canvas.setAttribute("aria-live", "polite");
		this.els.canvas.setAttribute("aria-relevant", "additions");
		this.els.viewport.appendChild(this.els.canvas);

		this.els.jumpLatest = el("button", {
			class: "ee-jump-latest is-hidden",
			type: "button",
			text: "Jump to latest",
			onclick: () => this.jumpToLatest(),
		});

		// aria-live="off": typing indicators are noise for a screen-reader user, and they
		// change several times a second.
		this.els.typing = el("div", { class: "ee-typing", "aria-live": "off" });

		this.els.composer = this.buildComposer();
		this.els.notice = el("div", { class: "ee-notice is-hidden", role: "status" });

		this.els.conversation = el("section", { class: "ee-conversation", "aria-label": "Conversation" }, [
			this.els.header,
			this.els.notice,
			this.els.viewport,
			this.els.jumpLatest,
			this.els.typing,
			this.els.composer.root,
		]);

		this.els.threadPane = el("aside", { class: "ee-thread is-hidden", "aria-label": "Thread" });
		this.els.membersPane = el("aside", { class: "ee-members is-hidden", "aria-label": "Members" });

		this.els.status = el("div", { class: "ee-connection", role: "status" });

		this.root.appendChild(
			el("div", { class: "ee-chat-shell", "data-pane": "list" }, [
				this.els.sidebar,
				this.els.conversation,
				this.els.threadPane,
				this.els.membersPane,
				this.els.status,
			])
		);
		this.els.shell = this.root.firstChild;

		this.transcript = new VirtualTranscript(this.els.viewport, this.els.canvas, (item, index) =>
			this.renderRow(item, index)
		);

		this.els.viewport.addEventListener("scroll", () => this.onScroll(), { passive: true });
		this.setupReadObserver();
		this.applyViewport();
		window.addEventListener("resize", () => this.applyViewport());
	}

	buildComposer() {
		const menu = el("ul", { class: "ee-mention-menu is-hidden", role: "listbox" });
		const textarea = el("textarea", {
			class: "ee-composer-text",
			rows: "1",
			placeholder: "Message",
			"aria-label": "Message",
			onkeydown: (ev) => this.onComposerKey(ev),
			oninput: (ev) => this.onComposerInput(ev),
			onblur: () => this.onComposerBlur(),
		});
		const files = el("div", { class: "ee-composer-files" });
		const send = el("button", {
			class: "ee-send",
			type: "button",
			"aria-label": "Send",
			text: "➤",
			onclick: () => this.send(),
		});
		const attach = el("button", {
			class: "ee-icon-btn",
			type: "button",
			title: "Attach a file",
			"aria-label": "Attach a file",
			text: "📎",
			onclick: () => fileInput.click(),
		});
		const fileInput = el("input", {
			class: "ee-file-input",
			type: "file",
			multiple: true,
			onchange: (ev) => this.queueUploads(Array.from(ev.target.files || [])),
		});

		const root = el("div", { class: "ee-composer" }, [
			menu,
			files,
			el("div", { class: "ee-composer-row" }, [attach, textarea, send, fileInput]),
			el("div", { class: "ee-composer-hint", text: "Enter to send · Shift+Enter for a new line" }),
		]);

		root.addEventListener("paste", (ev) => this.onPaste(ev));
		root.addEventListener("dragover", (ev) => ev.preventDefault());
		root.addEventListener("drop", (ev) => {
			ev.preventDefault();
			this.queueUploads(Array.from((ev.dataTransfer && ev.dataTransfer.files) || []));
		});

		return { root, textarea, menu, files, send, fileInput };
	}

	// ------------------------------------------------------------------ rooms

	indexRooms() {
		this.state.roomsById = new Map(this.state.rooms.map((room) => [room.name, room]));
	}

	renderRoomList() {
		const list = this.els.roomList;
		clear(list);
		const showArchived = !!this.state.showArchived;
		for (const room of this.state.rooms) {
			if (room.is_archived && !showArchived) continue;
			list.appendChild(this.renderRoomRow(room));
		}
		if (!list.childNodes.length) {
			list.appendChild(el("li", { class: "ee-room-empty", text: "No conversations yet." }));
		}
	}

	renderRoomRow(room) {
		const unread = Number(room.unread) || 0;
		const mentioned = (this.state.unread.rooms || []).some(
			(r) => r.room === room.name && r.mentions > 0
		);
		const active = this.state.room === room.name;

		return el("li", { class: "ee-room-item" }, [
			el(
				"a",
				{
					class:
						"ee-room" +
						(active ? " is-active" : "") +
						(unread ? " is-unread" : "") +
						(mentioned ? " is-mentioned" : ""),
					href: buildRoute({ room: room.name }),
					"aria-current": active ? "page" : null,
					onclick: (ev) => {
						if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
						ev.preventDefault();
						this.openRoom(room.name).catch(() => this.notice("That conversation is no longer available."));
					},
				},
				[
					el("span", { class: "ee-room-avatar", text: initials(roomTitle(room, this.me)) }),
					el("span", { class: "ee-room-main" }, [
						el("span", { class: "ee-room-title-row" }, [
							el("span", { class: "ee-room-title", text: roomTitle(room, this.me) }),
							el("time", { class: "ee-room-time", text: relativeTime(room.last_message_at) }),
						]),
						el("span", { class: "ee-room-preview", text: room.last_message_preview || "" }),
						room.linked_document
							? el("span", { class: "ee-room-doc", text: `${room.linked_doctype} · ${room.linked_document}` })
							: null,
					]),
					// A colour-only unread dot fails WCAG and fails anybody who cannot see the
					// colour; the bold room name and the count are the second and third cues.
					unread
						? el("span", {
								class: "ee-room-badge",
								text: unread > 99 ? "99+" : String(unread),
								"aria-label": `${unread} unread`,
							})
						: null,
					mentioned ? el("span", { class: "ee-room-mention", text: "@", "aria-label": "mentions you" }) : null,
				]
			),
		]);
	}

	toggleArchived() {
		this.state.showArchived = !this.state.showArchived;
		this.els.archivedToggle.textContent = this.state.showArchived ? "Hide archived" : "Show archived";
		call(M.ROOMS, { include_archived: this.state.showArchived ? 1 : 0 })
			.then((rooms) => {
				this.state.rooms = rooms || [];
				this.indexRooms();
				this.renderRoomList();
			})
			.catch(() => this.renderRoomList());
	}

	// ------------------------------------------------------------------ conversation

	async openRoom(room, options) {
		const opts = options || {};
		if (this.state.room && this.state.room !== room) {
			// Flush the read mark before leaving: a room switch is one of the forced flushes,
			// and skipping it is how the last few messages of every room stay unread forever.
			this.flushRead(true);
			this.saveDraft();
			if (this.socket) this.socket.leaveRoom(this.state.room);
		}

		performance.mark("ee-chat-room-start");
		this.state.room = room;
		this.state.thread = null;
		this.readBatcher.reset();
		this.typingIn.clearAll();

		const detail = await call(M.ROOM, { room });
		this.state.roomDetail = detail;
		this.state.marks = (detail.members || []).map((m) => ({
			user: m.user,
			last_read_seq: m.last_read_seq,
		}));
		this.readBatcher.prime((detail.members || []).find((m) => m.user === this.me)?.last_read_seq || 0);

		const page = opts.message
			? await call(M.CONTEXT, { message: opts.message })
			: await call(M.MESSAGES, { room, limit: this.state.pageSize });

		this.state.messages = page.messages || [];
		this.state.hasMore = page.has_more != null ? page.has_more : page.has_more_before;

		this.renderHeader();
		this.renderTranscript({
			anchor: opts.message
				? { name: opts.message, ratio: opts.anchorRatio == null ? 0.35 : opts.anchorRatio }
				: null,
		});
		if (!opts.message) this.transcript.scrollToBottom();

		if (this.socket) this.socket.joinRoom(room);
		this.renderRoomList();
		this.restoreDraft(opts.draft);
		this.showPane("conversation");
		this.els.composer.textarea.focus();

		if (opts.thread) this.openThread(opts.thread).catch(() => this.notice("That thread is no longer available."));

		this.pushRoute({ room, message: opts.message, thread: opts.thread }, opts.replace);
		this.rememberLastOpen(room, opts.thread);
		this.writeHandoffNow();

		performance.mark("ee-chat-room-ready");
		performance.measure("ee-chat-room", "ee-chat-room-start", "ee-chat-room-ready");
		if (opts.message) this.highlight(opts.message);
	}

	renderHeader() {
		const detail = this.state.roomDetail || {};
		this.els.roomTitle.textContent = roomTitle(detail, this.me);
		clear(this.els.roomSubtitle);
		if (detail.linked_document && detail.linked_document_url) {
			this.els.roomSubtitle.appendChild(
				el("a", {
					class: "ee-chat-link",
					href: detail.linked_document_url,
					target: "_blank",
					rel: "noopener noreferrer",
					text: `${detail.linked_doctype} · ${detail.linked_document}`,
				})
			);
		} else if (detail.description) {
			this.els.roomSubtitle.appendChild(document.createTextNode(detail.description));
		}
	}

	renderTranscript(options) {
		const opts = options || {};
		const rows = mergeForRender(this.state.messages, this.outbox.pending());
		const items = [];
		let previous = null;
		let previousDay = "";
		for (const message of rows) {
			const day = (message.creation || "").slice(0, 10);
			if (day && day !== previousDay) {
				items.push({ name: `day-${day}`, kind: "day", creation: message.creation });
				previousDay = day;
				previous = null;
			}
			items.push({
				...message,
				kind: "message",
				grouped: shouldGroup(previous, message),
			});
			previous = message;
		}
		this.transcript.setItems(items, opts.anchor);
	}

	renderRow(item) {
		if (item.kind === "day") return renderDaySeparator(item.creation);
		if (item.kind === "thinking") return renderThinkingPlaceholder();
		return renderMessage(item, {
			me: this.me,
			members: (this.state.roomDetail || {}).members || [],
			marks: this.state.marks,
			avatarLimit: this.state.avatarLimit,
			grouped: item.grouped,
			now: Date.now(),
			citationsEnabled: !!this.state.flags.triton_enabled,
			onCitationMiss: () => this.recordCitationMiss(),
			actions: {
				openThread: (message) => this.openThread(message.thread_root || message.name),
				copyLink: (message) => this.copyLink(message),
				edit: (message) => this.beginEdit(message),
				remove: (message) => this.deleteMessage(message),
				retry: (message) => this.retrySend(message),
				discard: (message) => this.discardSend(message),
				lightbox: (attachment) => this.openLightbox(attachment),
				showReaders: (readers) => this.showReaders(readers),
			},
		});
	}

	onScroll() {
		// Prefetch when within two viewport heights of the top boundary, per §4.13.
		if (this.els.viewport.scrollTop < this.els.viewport.clientHeight * 2) this.loadOlder();
		this.els.jumpLatest.classList.toggle("is-hidden", this.transcript.atBottom());
		this.evaluateReadState();
		this.writeHandoffThrottled();
	}

	async loadOlder() {
		if (this._loadingOlder || !this.state.hasMore || !this.state.messages.length) return;
		this._loadingOlder = true;
		const anchor = this.transcript.currentAnchor();
		try {
			const page = await call(M.MESSAGES, {
				room: this.state.room,
				before_seq: this.state.messages[0].seq,
				limit: this.state.pageSize,
			});
			if (page.messages && page.messages.length) {
				this.state.messages = page.messages.concat(this.state.messages);
				// Anchored on the message that was at the top, so prepending a page does not move
				// the reader. A pixel offset here is the "it jumped while I was reading" bug.
				this.renderTranscript({ anchor });
			}
			this.state.hasMore = !!page.has_more;
		} catch (err) {
			this.notice("Could not load older messages.");
		} finally {
			this._loadingOlder = false;
		}
	}

	jumpToLatest() {
		if (this.state.hasMore && this.state.messages.length) {
			this.openRoom(this.state.room, { replace: true }).catch(() => {});
			return;
		}
		this.transcript.scrollToBottom();
		this.els.jumpLatest.classList.add("is-hidden");
	}

	highlight(message) {
		const node = this.transcript.nodes.get(message);
		if (!node) return;
		node.classList.add("is-highlighted");
		if (!this._reducedMotion) setTimeout(() => node.classList.remove("is-highlighted"), 2500);
	}

	showEmptyState() {
		this.state.room = null;
		this.state.roomDetail = null;
		this.state.messages = [];
		this.transcript.setItems([]);
		this.els.roomTitle.textContent = "";
		clear(this.els.roomSubtitle);
		this.showPane("list");
	}

	// ------------------------------------------------------------------ threads

	async openThread(root) {
		const thread = await call(M.THREAD, { root });
		this.state.thread = thread.root.name;
		this.state.threadMessages = thread.messages || [];
		this.renderThread(thread);
		this.els.threadPane.classList.remove("is-hidden");
		this.showPane("thread");
		this.pushRoute({ room: this.state.room, thread: this.state.thread }, true);
		this.writeHandoffNow();
	}

	renderThread(thread) {
		const list = el("div", { class: "ee-thread-list" });
		const ctx = { grouped: false };
		list.appendChild(
			renderMessage(thread.root, {
				me: this.me,
				members: (this.state.roomDetail || {}).members || [],
				marks: this.state.marks,
				avatarLimit: this.state.avatarLimit,
				now: Date.now(),
				citationsEnabled: !!this.state.flags.triton_enabled,
				actions: {},
			})
		);
		let previous = thread.root;
		for (const message of thread.messages || []) {
			list.appendChild(
				renderMessage(message, {
					me: this.me,
					members: (this.state.roomDetail || {}).members || [],
					marks: this.state.marks,
					avatarLimit: this.state.avatarLimit,
					grouped: shouldGroup(previous, message),
					now: Date.now(),
					citationsEnabled: !!this.state.flags.triton_enabled,
					actions: {},
				})
			);
			previous = message;
		}

		fill(this.els.threadPane, [
			el("div", { class: "ee-thread-head" }, [
				el("h3", { class: "ee-thread-title", text: "Thread" }),
				el("button", {
					class: "ee-icon-btn",
					type: "button",
					"aria-label": "Close thread",
					text: "✕",
					onclick: () => this.closeThread(),
				}),
			]),
			list,
			el("div", { class: "ee-thread-composer" }, [
				el("textarea", {
					class: "ee-composer-text",
					rows: "1",
					placeholder: "Reply in thread",
					"aria-label": "Reply in thread",
					onkeydown: (ev) => {
						if (isComposingKey(ev)) return;
						if (ev.key === "Enter" && !ev.shiftKey) {
							ev.preventDefault();
							this.sendThreadReply(ev.target);
						}
					},
				}),
			]),
		]);
		// Focus moves into the pane, and Escape returns it to the originating message (§4.11).
		const composer = this.els.threadPane.querySelector(".ee-composer-text");
		if (composer) composer.focus();
		void ctx;
	}

	closeThread() {
		const origin = this.state.thread;
		this.state.thread = null;
		this.els.threadPane.classList.add("is-hidden");
		clear(this.els.threadPane);
		this.showPane("conversation");
		this.pushRoute({ room: this.state.room }, true);
		const node = origin && this.transcript.nodes.get(origin);
		if (node) node.focus();
		else this.els.composer.textarea.focus();
		this.writeHandoffNow();
	}

	async sendThreadReply(textarea) {
		const text = (textarea.value || "").trim();
		if (!text) return;
		textarea.value = "";
		await this.performSend({ text, parentMessage: this.state.thread });
		try {
			const thread = await call(M.THREAD, { root: this.state.thread });
			this.state.threadMessages = thread.messages || [];
			this.renderThread(thread);
		} catch (e) {
			/* the reply landed; the pane refresh is cosmetic */
		}
	}

	// ------------------------------------------------------------------ composer

	onComposerKey(ev) {
		// FIRST, above every branch. Enter and Tab commit an IME candidate, Escape cancels
		// one, the arrows move through it — so a guard placed after the mention-menu block
		// protects the exact keystrokes it exists to protect on every path except the one the
		// user is actually on. Returning unconditionally is correct here: nothing in this
		// handler should run while a composition is in flight.
		if (isComposingKey(ev)) return;
		if (this.mentionState.open) {
			if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
				ev.preventDefault();
				const delta = ev.key === "ArrowDown" ? 1 : -1;
				const count = this.mentionState.targets.length || 1;
				this.mentionState.index = (this.mentionState.index + delta + count) % count;
				this.renderMentionMenu();
				return;
			}
			if (ev.key === "Enter" || ev.key === "Tab") {
				ev.preventDefault();
				this.chooseMention(this.mentionState.targets[this.mentionState.index]);
				return;
			}
			if (ev.key === "Escape") {
				ev.preventDefault();
				this.closeMentionMenu();
				return;
			}
		}
		if (ev.key === "Escape" && this.state.thread) {
			ev.preventDefault();
			this.closeThread();
			return;
		}
		if (ev.key === "Enter" && !ev.shiftKey) {
			ev.preventDefault();
			this.send();
		}
	}

	onComposerInput(ev) {
		const textarea = ev.target;
		textarea.style.height = "auto";
		textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px";

		this.composerMentions = mentions.reanchor(textarea.value, this.composerMentions);
		this.saveDraft();
		this.writeHandoffThrottled();

		const signal = this.typingOut.onInput(textarea.value, Date.now());
		if (signal) this.emitTyping(signal === "stopped");

		const trigger = mentions.findTrigger(textarea.value, textarea.selectionStart);
		if (!trigger) {
			this.closeMentionMenu();
			return;
		}
		this.mentionState.trigger = trigger;
		this.debouncedMentionSearch(trigger.query);
	}

	onComposerBlur() {
		const signal = this.typingOut.stop();
		if (signal) this.emitTyping(true);
		// Not closed immediately: a click on a menu entry blurs the textarea first, and closing
		// here would remove the entry before the click lands.
		setTimeout(() => this.closeMentionMenu(), 150);
	}

	debouncedMentionSearch(query) {
		if (this._mentionTimer) clearTimeout(this._mentionTimer);
		this._mentionTimer = setTimeout(async () => {
			try {
				const res = await call(M.MENTION_TARGETS, { room: this.state.room, query });
				this.mentionState.targets = res.results || [];
				this.mentionState.index = 0;
				this.mentionState.open = this.mentionState.targets.length > 0;
				this.renderMentionMenu();
			} catch (e) {
				this.closeMentionMenu();
			}
		}, 150);
	}

	renderMentionMenu() {
		const menu = this.els.composer.menu;
		menu.classList.toggle("is-hidden", !this.mentionState.open);
		clear(menu);
		this.mentionState.targets.forEach((target, index) => {
			menu.appendChild(
				el(
					"li",
					{
						class:
							"ee-mention-option" +
							(index === this.mentionState.index ? " is-active" : "") +
							(target.kind === "triton" ? " is-triton" : "") +
							(target.is_member ? "" : " is-outsider"),
						role: "option",
						"aria-selected": index === this.mentionState.index ? "true" : "false",
						onmousedown: (ev) => {
							ev.preventDefault();
							this.chooseMention(target);
						},
					},
					[
						el("span", { class: "ee-mention-label", text: target.label }),
						el("span", { class: "ee-mention-desc", text: target.description || "" }),
						target.is_member ? null : el("span", { class: "ee-badge", text: "not in room" }),
					]
				)
			);
		});
	}

	chooseMention(target) {
		if (!target || !this.mentionState.trigger) return;
		const textarea = this.els.composer.textarea;
		const result = mentions.insertMention(textarea.value, this.mentionState.trigger, target);
		textarea.value = result.text;
		textarea.setSelectionRange(result.caret, result.caret);
		this.composerMentions = mentions.reanchor(
			result.text,
			this.composerMentions.concat([result.mention])
		);
		this.closeMentionMenu();
		textarea.focus();
		this.saveDraft();
	}

	closeMentionMenu() {
		this.mentionState.open = false;
		this.mentionState.trigger = null;
		this.els.composer.menu.classList.add("is-hidden");
	}

	async send() {
		const textarea = this.els.composer.textarea;
		const text = (textarea.value || "").trim();
		const files = this.pendingUploads.filter((u) => u.fileName).map((u) => u.fileName);
		if (!text && !files.length) return;

		textarea.value = "";
		textarea.style.height = "auto";
		const mentionPayload = mentions.toPayload(this.composerMentions);
		const wantedTriton = mentions.mentionsTriton(this.composerMentions);
		this.composerMentions = [];
		this.pendingUploads = [];
		clear(this.els.composer.files);
		this.drafts.delete(this.state.room);

		const signal = this.typingOut.stop();
		if (signal) this.emitTyping(true);

		await this.performSend({ text, mentions: mentionPayload, attachments: files, wantedTriton });
	}

	async performSend({ text, parentMessage, mentions: mentionPayload, attachments, wantedTriton }) {
		const clientMessageId = newClientMessageId();
		const entry = this.outbox.enqueue({
			clientMessageId,
			room: this.state.room,
			text,
			parentMessage,
			sender: this.me,
			attachments: [],
			now: Date.now(),
		});
		this.renderTranscript();
		this.transcript.scrollToBottom();

		if (!navigator.onLine || (this.socket && !this.socket.connected && this.state.connection === "disconnected")) {
			const queued = this.offline.push({ clientMessageId, text, parentMessage, mentionPayload, attachments });
			if (!queued.accepted) {
				this.outbox.fail(clientMessageId, new Error("Offline queue is full"));
				this.notice("You are offline and the queue is full. Send again when you reconnect.");
			} else {
				this.notice("Offline — this will send when you reconnect.");
			}
			this.renderTranscript();
			return entry;
		}

		try {
			const row = await call(M.SEND, {
				room: this.state.room,
				text,
				client_message_id: clientMessageId,
				parent_message: parentMessage || null,
				mentions: mentionPayload || [],
				attachments: attachments || [],
			});
			const result = this.outbox.reconcile(row, Date.now());
			if (result.action === "appended") this.state.messages.push(row);
			else if (result.action === "reconciled") this.state.messages.push(result.entry);
			this.renderTranscript();
			this.transcript.scrollToBottom();
			if (wantedTriton) this.showThinking();
			return result.entry || row;
		} catch (err) {
			this.outbox.fail(clientMessageId, err);
			this.renderTranscript();
			return entry;
		}
	}

	async retrySend(message) {
		// **Same `client_message_id`.** The server's unique index turns a duplicate insert into
		// a detectable no-op and returns the existing row as success, which is what makes retry
		// safe against "it actually worked and the response was lost".
		const entry = this.outbox.retry(message.client_message_id, Date.now());
		if (!entry) return;
		this.renderTranscript();
		try {
			const row = await call(M.SEND, {
				room: entry.room,
				text: entry.text,
				client_message_id: entry.client_message_id,
				parent_message: entry.parent_message || null,
			});
			const result = this.outbox.reconcile(row, Date.now());
			if (result.action !== "ignored") this.state.messages.push(result.entry || row);
		} catch (err) {
			this.outbox.fail(entry.client_message_id, err);
		}
		this.renderTranscript();
	}

	discardSend(message) {
		this.outbox.discard(message.client_message_id);
		this.renderTranscript();
	}

	showThinking() {
		const items = this.transcript.items.slice();
		items.push({ name: "thinking-placeholder", kind: "thinking" });
		this.transcript.setItems(items);
		this.transcript.scrollToBottom();
	}

	saveDraft() {
		if (!this.state.room) return;
		this.drafts.set(this.state.room, {
			text: this.els.composer.textarea.value,
			mentions: this.composerMentions,
		});
	}

	restoreDraft(fromHandoff) {
		const stored = this.drafts.get(this.state.room);
		const text = fromHandoff || (stored && stored.text) || "";
		this.els.composer.textarea.value = text;
		this.composerMentions = (stored && stored.mentions) || [];
	}

	// ------------------------------------------------------------------ attachments

	onPaste(ev) {
		const items = Array.from((ev.clipboardData && ev.clipboardData.items) || []);
		const files = items.filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);
		if (files.length) {
			ev.preventDefault();
			this.queueUploads(files);
		}
	}

	async queueUploads(files) {
		if (!files.length || !this.state.room) return;
		let limits;
		try {
			limits = await call(M.UPLOAD_INFO, { room: this.state.room });
		} catch (e) {
			this.notice("Uploads are not available right now.");
			return;
		}

		for (const file of files) {
			if (limits.max_file_bytes && file.size > limits.max_file_bytes) {
				this.notice(`${file.name} is too large.`);
				continue;
			}
			const row = { file, progress: 0, fileName: null };
			this.pendingUploads.push(row);
			const node = el("div", { class: "ee-upload" }, [
				el("span", { class: "ee-upload-name", text: file.name }),
				el("progress", { class: "ee-upload-progress", max: "1", value: "0" }),
			]);
			this.els.composer.files.appendChild(node);

			const handle = upload(file, this.state.room, (fraction) => {
				const bar = node.querySelector("progress");
				if (bar) bar.value = String(fraction);
			});
			node.appendChild(
				el("button", {
					class: "ee-link-btn",
					type: "button",
					text: "Cancel",
					onclick: () => handle.abort(),
				})
			);
			handle.promise
				.then((fileDoc) => {
					row.fileName = fileDoc.name;
					node.classList.add("is-done");
				})
				.catch(() => {
					node.classList.add("is-failed");
					this.pendingUploads = this.pendingUploads.filter((u) => u !== row);
				});
		}
	}

	openLightbox(attachment) {
		const overlay = el(
			"div",
			{
				class: "ee-lightbox",
				role: "dialog",
				"aria-modal": "true",
				"aria-label": attachment.file_name || "Image",
				onclick: (ev) => {
					if (ev.target === overlay) overlay.remove();
				},
			},
			[
				el("img", { class: "ee-lightbox-image", src: attachment.file_url, alt: attachment.file_name || "" }),
				el("button", {
					class: "ee-lightbox-close",
					type: "button",
					"aria-label": "Close",
					text: "✕",
					onclick: () => overlay.remove(),
				}),
			]
		);
		const onKey = (ev) => {
			if (ev.key === "Escape") {
				overlay.remove();
				document.removeEventListener("keydown", onKey);
			}
		};
		document.addEventListener("keydown", onKey);
		document.body.appendChild(overlay);
		overlay.querySelector(".ee-lightbox-close").focus();
	}

	// ------------------------------------------------------------------ read state

	setupReadObserver() {
		if (typeof IntersectionObserver === "undefined") return;
		this.readObserver = new IntersectionObserver(
			(entries) => {
				const now = Date.now();
				for (const entry of entries) {
					const seq = Number(entry.target.dataset.seq);
					if (!seq) continue;
					this.readBatcher.observe(seq, entry.isIntersecting && this.windowIsBeingRead(), now);
				}
				this.flushRead(false);
			},
			// 0.6, not a sliver: a one-pixel edge of a message scrolling past is not "read".
			{ root: this.els.viewport, threshold: 0.6 }
		);
	}

	/** Conditions 2 and 3 of §4.7: visible AND focused. A chat tab behind a spreadsheet is
	 *  visible and not focused, and is not being read. */
	windowIsBeingRead() {
		return document.visibilityState === "visible" && document.hasFocus();
	}

	evaluateReadState() {
		if (!this.readObserver) return;
		for (const node of this.transcript.nodes.values()) {
			if (node.dataset.seq) this.readObserver.observe(node);
		}
	}

	flushRead(force) {
		const now = Date.now();
		if (!this.state.room) return;
		if (!this.readBatcher.shouldFlush(now, force)) return;
		const seq = this.readBatcher.take(now);
		if (!seq) return;
		call(M.MARK_READ, { room: this.state.room, up_to_seq: seq }, { keepalive: !!force })
			.then((res) => res && this.readBatcher.acknowledge(res.last_read_seq))
			.catch(() => {
				/* the next flush carries the same or a higher value; nothing is lost */
			});
	}

	async markAllRead() {
		try {
			this.state.unread = await call(M.MARK_ALL_READ);
			this.state.rooms = (await call(M.ROOMS)) || [];
			this.indexRooms();
			this.renderRoomList();
		} catch (e) {
			this.notice("Could not mark everything read.");
		}
	}

	// ------------------------------------------------------------------ realtime

	onRealtime(name, payload) {
		if (!payload) return;
		switch (name) {
			case "chat_message_created":
				this.onMessageCreated(payload);
				break;
			case "chat_message_edited":
			case "chat_message_deleted":
				this.refreshMessage(payload);
				break;
			case "chat_typing":
				if (payload.user !== this.me) {
					this.typingIn.start(payload.room, payload.thread_root || null, payload.user, Date.now());
					this.renderTyping();
				}
				break;
			case "chat_typing_stopped":
				this.typingIn.stop(payload.room, payload.thread_root || null, payload.user);
				this.renderTyping();
				break;
			case "chat_presence":
				this.state.presence[payload.user] = payload.state;
				this.renderPresence();
				break;
			case "chat_read_receipt":
				this.applyReceipt(payload);
				break;
			case "chat_unread_updated":
				this.applyUnread(payload);
				break;
			case "chat_room_updated":
				this.refreshRooms();
				break;
			case "chat_mention":
				// A ping. Phase 4 decides what a notification does with it; this phase shows the
				// mention indicator on the room and nothing else.
				this.refreshUnread();
				break;
			default:
				break;
		}
	}

	async onMessageCreated(payload) {
		if (payload.room !== this.state.room) {
			this.refreshUnread();
			return;
		}
		try {
			const page = await call(M.MESSAGES, {
				room: this.state.room,
				after_seq: this.newestSeq(),
				limit: this.state.pageSize,
			});
			this.absorb(page.messages || []);
		} catch (e) {
			/* the next resync picks it up */
		}
	}

	/**
	 * Merge server rows in, reconciling any that are our own optimistic sends.
	 *
	 * `reconcile` is keyed on `client_message_id` and looks there FIRST, so a message that
	 * arrives by both the send response and the realtime path produces exactly one bubble in
	 * either order. That is the whole "my message appeared twice" defence.
	 */
	absorb(rows) {
		const wasAtBottom = this.transcript.atBottom();
		let changed = false;
		for (const row of rows) {
			const result = this.outbox.reconcile(row, Date.now());
			if (result.action === "ignored") continue;
			const existing = this.state.messages.findIndex((m) => m.name === row.name);
			if (existing >= 0) this.state.messages[existing] = result.entry || row;
			else this.state.messages.push(result.entry || row);
			changed = true;
		}
		if (!changed) return;
		this.state.messages.sort((a, b) => (a.seq || 0) - (b.seq || 0));
		this.renderTranscript();
		if (wasAtBottom) this.transcript.scrollToBottom();
		this.evaluateReadState();
	}

	async refreshMessage(payload) {
		if (payload.room !== this.state.room) return;
		try {
			const page = await call(M.CONTEXT, { message: payload.message, limit: 1 });
			const fresh = (page.messages || []).find((m) => m.name === payload.message);
			if (!fresh) return;
			const index = this.state.messages.findIndex((m) => m.name === payload.message);
			if (index >= 0) {
				this.state.messages[index] = fresh;
				this.renderTranscript();
			}
		} catch (e) {
			/* ignore: the row is still readable, this is a refresh */
		}
	}

	applyReceipt(payload) {
		const index = this.state.marks.findIndex((m) => m.user === payload.user);
		if (index >= 0) this.state.marks[index].last_read_seq = payload.last_read_seq;
		else this.state.marks.push({ user: payload.user, last_read_seq: payload.last_read_seq });
		this.renderTranscript();
	}

	applyUnread(payload) {
		if (payload.room) {
			const room = this.state.roomsById.get(payload.room);
			if (room) room.unread = Number(payload.unread) || 0;
		}
		if (payload.total_unread != null) this.state.unread.total_unread = Number(payload.total_unread);
		this.renderRoomList();
	}

	/**
	 * The reconnect resync. Realtime is not durable, so this is the correctness path and the
	 * events are the accelerator.
	 *
	 * Four things, in this order: back-fill the active room from the newest message we hold,
	 * re-read the room's high-water marks, re-fetch unread counts WHOLESALE, and clear every
	 * typing indicator — they are ephemeral and whatever we held is stale.
	 */
	async resync(reason) {
		this.typingIn.clearAll();
		this.renderTyping();
		try {
			this.state.rooms = (await call(M.ROOMS, { include_archived: this.state.showArchived ? 1 : 0 })) || [];
			this.indexRooms();
			this.state.unread = await call(M.UNREAD);
			this.renderRoomList();

			if (this.state.room) {
				const page = await call(M.MESSAGES, {
					room: this.state.room,
					after_seq: this.newestSeq(),
					limit: this.state.pageSize,
				});
				this.absorb(page.messages || []);
				this.state.marks = (await call(M.READ_MARKS, { room: this.state.room })) || [];
				this.renderTranscript();
			}

			const queued = this.offline.drain();
			for (const item of queued) {
				// In order. Messages arrive in the order they were typed, and a parallel flush
				// reorders them for everybody else.
				await this.retrySend({ client_message_id: item.clientMessageId });
			}
		} catch (err) {
			this.notice("Reconnecting…");
		}
		void reason;
	}

	newestSeq() {
		let seq = 0;
		for (const message of this.state.messages) if ((message.seq || 0) > seq) seq = message.seq;
		return seq;
	}

	async refreshUnread() {
		try {
			this.state.unread = await call(M.UNREAD);
			for (const row of this.state.unread.rooms || []) {
				const room = this.state.roomsById.get(row.room);
				if (room) room.unread = row.unread;
			}
			this.renderRoomList();
		} catch (e) {
			/* the next resync corrects it */
		}
	}

	async refreshRooms() {
		try {
			this.state.rooms = (await call(M.ROOMS, { include_archived: this.state.showArchived ? 1 : 0 })) || [];
			this.indexRooms();
			this.renderRoomList();
		} catch (e) {
			/* ignore */
		}
	}

	renderTyping() {
		const names = this.typingIn
			.active(this.state.room, this.state.thread, Date.now())
			.map((user) => {
				const member = ((this.state.roomDetail || {}).members || []).find((m) => m.user === user);
				return member ? member.full_name : user;
			});
		this.els.typing.textContent = typingLabel(names);
	}

	renderPresence() {
		if (!this.els.membersPane || this.els.membersPane.classList.contains("is-hidden")) return;
		this.renderMembers();
	}

	setConnection(status) {
		this.state.connection = status;
		this.els.status.textContent =
			status === "connected"
				? ""
				: status === "unavailable"
					? "Live updates unavailable — refresh to see new messages."
					: "Reconnecting…";
		this.els.status.classList.toggle("is-hidden", status === "connected");
	}

	// ------------------------------------------------------------------ presence heartbeat

	startHeartbeat() {
		const beat = () => {
			if (!this.me) return;
			call(M.HEARTBEAT, {
				client_id: this.clientId,
				active_room: this.state.room || null,
				thread: this.state.thread || null,
				focused: document.hasFocus() ? 1 : 0,
				visibility: document.visibilityState,
			})
				.then((res) => {
					if (res && res.room_presence) {
						this.state.presence = res.room_presence;
						this.renderPresence();
					}
				})
				.catch(() => {
					/* presence is an accelerator; a failed beat expires by TTL */
				});
		};
		beat();
		this._heartbeat = setInterval(beat, 30000);
	}

	// ------------------------------------------------------------------ members

	toggleMembers() {
		const hidden = this.els.membersPane.classList.toggle("is-hidden");
		if (!hidden) this.renderMembers();
	}

	renderMembers() {
		const detail = this.state.roomDetail || {};
		const list = el("ul", { class: "ee-member-list", role: "list" });
		for (const member of detail.members || []) {
			const state = this.state.presence[member.user] || "offline";
			list.appendChild(
				el("li", { class: "ee-member" }, [
					el("span", { class: `ee-presence is-${state}`, "aria-hidden": "true" }),
					el("span", { class: "ee-member-name", text: member.full_name }),
					el("span", { class: "ee-member-role", text: member.role }),
					// Honest, per §4.6: presence reflects an ERPNext session. A coworker who lives
					// in the native Google Chat client has none, and the tooltip says so rather
					// than letting a grey dot imply they are away from their desk.
					el("span", {
						class: "sr-only",
						text:
							state === "offline"
								? "No ERPNext session. Presence reflects ERPNext only, not Google Chat."
								: state,
					}),
				])
			);
		}
		fill(this.els.membersPane, [
			el("div", { class: "ee-thread-head" }, [
				el("h3", { class: "ee-thread-title", text: "Members" }),
				el("button", {
					class: "ee-icon-btn",
					type: "button",
					"aria-label": "Close members",
					text: "✕",
					onclick: () => this.els.membersPane.classList.add("is-hidden"),
				}),
			]),
			el("p", {
				class: "ee-members-note",
				text: "Presence and typing come from ERPNext. Someone using only Google Chat shows as offline.",
			}),
			list,
		]);
	}

	showReaders(readers) {
		this.notice(`Read by ${readers.map((r) => r.user).join(", ")}`);
	}

	// ------------------------------------------------------------------ search

	async openSearch(query, options) {
		const opts = options || {};
		this.state.search = query;
		this.pushRoute({ kind: KIND.SEARCH, query }, opts.replace);
		this.showPane("conversation");
		this.els.roomTitle.textContent = `Search: ${query}`;
		clear(this.els.roomSubtitle);

		try {
			const res = await call(M.SEARCH, { query });
			this.renderSearchResults(res);
		} catch (e) {
			this.notice("Search failed.");
		}
	}

	renderSearchResults(res) {
		const items = (res.results || []).map((hit) => ({ name: hit.message, kind: "search", hit }));
		this.transcript.renderRow = (item) => {
			if (item.kind !== "search") return this.renderRow(item);
			const hit = item.hit;
			// The snippet is highlighted by SLICING at server-supplied offsets and building
			// nodes, never by interpolating <mark> into a string. Message bodies are
			// user-authored and this is the shortest path from any employee to every employee.
			const snippet = el("span", { class: "ee-search-snippet" });
			if (hit.match_start >= 0) {
				snippet.appendChild(document.createTextNode(hit.snippet.slice(0, hit.match_start)));
				snippet.appendChild(
					el("mark", { text: hit.snippet.substr(hit.match_start, hit.match_length) })
				);
				snippet.appendChild(
					document.createTextNode(hit.snippet.slice(hit.match_start + hit.match_length))
				);
			} else {
				snippet.appendChild(document.createTextNode(hit.snippet));
			}
			return el("a", {
				class: "ee-search-hit",
				href: hit.route,
				onclick: (ev) => {
					ev.preventDefault();
					this.transcript.renderRow = (i) => this.renderRow(i);
					this.openRoom(hit.room, { message: hit.message }).catch(() => {});
				},
			}, [
				el("span", { class: "ee-search-meta", text: `${hit.room_title || hit.room} · ${hit.sender_name}` }),
				snippet,
			]);
		};
		this.transcript.setItems(items);
	}

	// ------------------------------------------------------------------ Triton

	/**
	 * The Triton entry in the room list.
	 *
	 * **Scope call, reported at the checkpoint.** Decision #8 forbids forking the widget, and
	 * the widget is a 1,404-line anonymous IIFE with zero exports and hard Desk dependencies
	 * (`frappe.xcall`, `frappe.markdown`, `frappe.Chart`, the Desk mermaid theme) that do not
	 * exist on a website route. Reimplementing its 75-row behaviour inventory here is exactly
	 * the regression the phase gate exists to prevent, so the SPA does not host a second
	 * Triton client: `/chat/triton` returns the user to the bubble, which remains the full
	 * Triton surface on every Desk page.
	 *
	 * What the SPA DOES render with the shared component is `@triton` inside a coworker room —
	 * an ordinary `Chat Message` with `sender_kind = "Triton"` — including its inline
	 * citations. That is the "one renderer, two surfaces" §4.10 asks for.
	 */
	openTriton(options) {
		const opts = options || {};
		this.pushRoute({ kind: KIND.TRITON }, opts.replace);
		this.showPane("conversation");
		this.els.roomTitle.textContent = "Triton";
		clear(this.els.roomSubtitle);
		this.transcript.setItems([]);
		fill(this.els.notice, [
			document.createTextNode("Triton lives in the floating assistant on any ERPNext page. "),
			el("a", { class: "ee-chat-link", href: "/app", text: "Open ERPNext" }),
			document.createTextNode(" and use the 🔱 bubble, or mention "),
			el("span", { class: "ee-mention is-triton", text: "@Triton" }),
			document.createTextNode(" in any conversation here."),
		]);
		this.els.notice.classList.remove("is-hidden");
	}

	recordCitationMiss() {
		this.state.citationMisses += 1;
		// Surfaced rather than swallowed: a `citation_miss` rate over ~2% means a prompt edit
		// broke citing, silently, and the only symptom is inline links quietly disappearing.
		if (this.state.citationMisses === 10) {
			console.warn("ee-chat: repeated citation misses — the manifest and the answer disagree");
		}
	}

	// ------------------------------------------------------------------ routing + handoff

	pushRoute(target, replace) {
		const url = buildRoute(target);
		if (location.pathname + location.search === url) return;
		history[replace ? "replaceState" : "pushState"]({ ee_chat: true }, "", url);
	}

	copyLink(message) {
		const url = location.origin + buildRoute({
			room: message.room,
			message: message.name,
			thread: message.thread_root || null,
		});
		if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {});
		this.notice("Link copied.");
	}

	/**
	 * The reverse handoff. Symmetric with the bubble's, or it is half a feature: collapsing
	 * back to the bubble, or navigating from here into a Desk page, must leave the bubble
	 * where the SPA was.
	 */
	writeHandoffNow() {
		if (!this.state.room) return;
		const anchor = this.transcript.currentAnchor();
		writeHandoff(
			{
				room: this.state.room,
				thread: this.state.thread,
				anchorMessage: anchor && anchor.name,
				anchorRatio: anchor && anchor.ratio,
				draft: this.els.composer.textarea.value,
				surface: "coworker",
			},
			{ session: sessionStorage, local: localStorage },
			Date.now()
		);
		this._lastHandoffAt = Date.now();
	}

	writeHandoffThrottled() {
		if (Date.now() - this._lastHandoffAt < HANDOFF_THROTTLE_MS) return;
		this.writeHandoffNow();
	}

	rememberLastOpen(room, thread) {
		const now = Date.now();
		if (now - this._lastOpenSentAt < LAST_OPEN_THROTTLE_MS) return;
		this._lastOpenSentAt = now;
		call(M.LAST_OPEN, { room, thread: thread || null }).catch(() => {});
	}

	emitTyping(stopped) {
		if (!this.state.room) return;
		call(M.TYPING, {
			room: this.state.room,
			thread: this.state.thread || null,
			stopped: stopped ? 1 : 0,
		}).catch(() => {});
	}

	// ------------------------------------------------------------------ chrome

	bindGlobalEvents() {
		window.addEventListener("popstate", () => {
			this.routeTo(location.pathname, location.search, { replace: true });
		});

		// Forced read flushes (§4.7). `pagehide` uses fetch(keepalive) rather than sendBeacon:
		// a beacon cannot set the CSRF header, so Frappe rejects it — which is exactly how "it
		// never marks the last message read when I close the tab" happens.
		window.addEventListener("blur", () => this.flushRead(true));
		window.addEventListener("focus", () => this.evaluateReadState());
		document.addEventListener("visibilitychange", () => {
			if (document.visibilityState === "hidden") this.flushRead(true);
			else this.resync("visible");
		});
		window.addEventListener("pagehide", () => {
			this.flushRead(true);
			this.writeHandoffNow();
			call(M.GOODBYE, { client_id: this.clientId }, { keepalive: true }).catch(() => {});
		});
		window.addEventListener("online", () => this.resync("online"));

		// Timeout sweep for pending sends: after ~15s a message is presented as failed with
		// Retry, but the entry and its id are KEPT so a late realtime event still reconciles it
		// rather than producing a duplicate next to the failed one.
		setInterval(() => {
			if (this.outbox.sweepTimeouts(Date.now()).length) this.renderTranscript();
		}, 5000);

		document.addEventListener("keydown", (ev) => this.onShortcut(ev));
	}

	onShortcut(ev) {
		// Before ANY branch, including Escape. Escape is how an IME user cancels a
		// composition, so an un-guarded Escape here closes the thread pane — and
		// `closeThread()` discards the reply they were half way through typing — on a
		// keystroke they aimed at the candidate window.
		if (isComposingKey(ev)) return;
		// A modifier the user did not press. AltGr is reported as ctrlKey+altKey on Windows
		// and many EU layouts, and Ctrl+J / Cmd+K are the browser's, not ours. This is the
		// same defect Alt+T was just fixed for, one file over; single-letter global shortcuts
		// are where it always shows up.
		if (ev.ctrlKey || ev.metaKey || ev.altKey) return;

		// Never hijack a shortcut while the user is typing, except Escape.
		const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement && document.activeElement.tagName);
		if (ev.key === "Escape" && this.state.thread) {
			this.closeThread();
			return;
		}
		if (typing) return;
		if (ev.key === "/" ) {
			ev.preventDefault();
			this.els.composer.textarea.focus();
		} else if (ev.key === "j" || ev.key === "k") {
			ev.preventDefault();
			this.stepRoom(ev.key === "j" ? 1 : -1);
		} else if (ev.key === "?") {
			ev.preventDefault();
			this.showShortcuts();
		}
	}

	stepRoom(delta) {
		const visible = this.state.rooms.filter((r) => this.state.showArchived || !r.is_archived);
		if (!visible.length) return;
		const index = visible.findIndex((r) => r.name === this.state.room);
		const next = visible[(index + delta + visible.length) % visible.length];
		this.openRoom(next.name).catch(() => {});
	}

	showShortcuts() {
		this.notice("/ focus composer · j/k next/previous conversation · Esc close thread");
	}

	applyViewport() {
		const narrow = window.innerWidth < NARROW_PX;
		this.els.shell.classList.toggle("is-narrow", narrow);
		if (!narrow) this.els.shell.dataset.pane = "all";
		else this.els.shell.dataset.pane = this.state.pane;
	}

	showPane(pane) {
		this.state.pane = pane;
		this.applyViewport();
	}

	notice(text) {
		this.els.notice.textContent = text;
		this.els.notice.classList.remove("is-hidden");
		clearTimeout(this._noticeTimer);
		this._noticeTimer = setTimeout(() => this.els.notice.classList.add("is-hidden"), 6000);
	}

	fatal(err) {
		fill(this.root, [
			el("div", { class: "ee-chat-fatal", role: "alert" }, [
				el("h1", { text: "Chat is unavailable" }),
				el("p", { text: (err && err.message) || "Could not load your conversations." }),
				el("a", { class: "ee-chat-link", href: "/app", text: "Back to ERPNext" }),
			]),
		]);
	}

	beginEdit(message) {
		const next = window.prompt("Edit message", message.text || "");
		if (next == null) return;
		call(M.EDIT, { message: message.name, text: next })
			.then((row) => {
				const index = this.state.messages.findIndex((m) => m.name === row.name);
				if (index >= 0) this.state.messages[index] = row;
				this.renderTranscript();
			})
			.catch((err) => this.notice(err.message));
	}

	deleteMessage(message) {
		if (!window.confirm("Delete this message? It will show as deleted for everyone.")) return;
		call(M.DELETE, { message: message.name })
			.then((row) => {
				const index = this.state.messages.findIndex((m) => m.name === row.name);
				if (index >= 0) this.state.messages[index] = row;
				this.renderTranscript();
			})
			.catch((err) => this.notice(err.message));
	}
}

function roomTitle(room, me) {
	if (!room) return "";
	if (room.title) return room.title;
	if (room.room_type === "Direct Message") {
		const other = room.dm_user_1 === me ? room.dm_user_2 : room.dm_user_1;
		return other || "Direct message";
	}
	return room.name;
}

/** One id per TAB, in sessionStorage. The multi-tab presence union is keyed on it, and a
 *  localStorage id would make three tabs look like one client. */
function ensureClientId() {
	try {
		let id = sessionStorage.getItem("ee_chat_client_id");
		if (!id) {
			id = "c-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
			sessionStorage.setItem("ee_chat_client_id", id);
		}
		return id;
	} catch (e) {
		return "c-" + Math.random().toString(36).slice(2);
	}
}

export function start() {
	const root = document.getElementById("ee-chat-root");
	if (!root) return null;
	const app = new ChatApp(root);
	app.mount();
	// Exposed for the manual QA checklist and the performance measurements — the retained DOM
	// node count in §4.13 is read off `window.eeChat.transcript.renderedCount()`.
	window.eeChat = app;
	return app;
}

// Unused imports kept honest: these two are re-exported for the widget, which imports the
// citation renderer from this bundle's sibling module rather than duplicating it.
export { applyCitations, indexManifest, STATE };
