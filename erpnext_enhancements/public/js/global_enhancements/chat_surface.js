/**
 * The coworker chat surface **inside the floating bubble**.
 *
 * Decision #8: the bubble stays as the entry point on every Desk page and now handles BOTH
 * Triton conversations and coworker conversations; expanding it opens the full SPA deep-linked
 * to the conversation the user was in.
 *
 * This module is the coworker half. It is a separate file rather than another 600 lines inside
 * `triton_widget.js` for one reason: Appendix A is the widget's preserved-behaviour inventory,
 * every row of it is defended by reading that file, and the smaller the diff to it the smaller
 * the surface for a regression. `triton_widget.js` gains a surface switch, an expand control, a
 * badge and a handoff writer; everything below is new code in a new file.
 *
 * It shares the SPA's modules — `transport`, `handoff`, `optimistic`, `signals`, `mentions` —
 * so the bubble and the SPA cannot disagree about the API shape, the idempotency rule or the
 * handoff record. What it does NOT share is the SPA's virtualised transcript: a bubble shows
 * the last page and pages back, and 200 lines of windowing for a 380px panel is not a trade
 * worth making.
 *
 * NO VUE, like the rest of chat. This runs on Desk pages where the vendored UMD `window.Vue`
 * exists; touching it would be the two-runtime hazard from the wrong direction.
 */

import { clear, el, initials, isComposingKey, linkify, relativeTime } from "../chat/dom.js";
import * as mentions from "../chat/mentions.js";
import { OutboxStore, newClientMessageId, mergeForRender } from "../chat/optimistic.js";
import {
	READ_DWELL_MS,
	ReadBatcher,
	TypingRegistry,
	TypingThrottle,
	ensureClientId,
	typingLabel,
} from "../chat/signals.js";
import { M, call } from "../chat/transport.js";

/** How many messages the bubble loads at once. Smaller than the SPA's 50: the panel is
 *  ~380px wide and the round trip happens on every Desk page load that opens it. */
const BUBBLE_PAGE = 30;

export class BubbleChatSurface {
	/**
	 * @param {HTMLElement} host the panel section this surface owns
	 * @param {object} hooks `{onUnread(total), onStateChange(state), me}`
	 */
	constructor(host, hooks) {
		this.host = host;
		this.hooks = hooks || {};
		this.me = this.hooks.me || null;
		this.rooms = [];
		this.room = null;
		this.roomDetail = null;
		this.messages = [];
		this.marks = [];
		this.thread = null;
		this.outbox = new OutboxStore();
		this.readBatcher = new ReadBatcher();
		this.typingOut = new TypingThrottle();
		this.typingIn = new TypingRegistry();
		this.composerMentions = [];
		this.drafts = new Map();
		this.clientId = ensureClientId();
		this.loaded = false;
		this.build();
		this.startHeartbeat();
	}

	/**
	 * Publish presence, the same way the SPA does.
	 *
	 * The bubble had no beat at all, and it is the surface everybody actually lives in — so a
	 * colleague working in the bubble all day was painted **offline** in the SPA's member
	 * pane, under a caption reading "No ERPNext session", one element away from a live
	 * "…is typing" line for that same person. Typing was published; existence was not.
	 *
	 * `focused` is `document.hasFocus()` on the Desk page, not on the panel: the question the
	 * server is answering is "is this person at this computer", and the panel losing focus to
	 * a form field on the same page does not change the answer.
	 */
	beatPresence() {
		if (!this.me) return Promise.resolve(null);
		return call(M.HEARTBEAT, {
			client_id: this.clientId,
			active_room: this.room || null,
			thread: null,
			focused: document.hasFocus() && document.visibilityState === "visible" ? 1 : 0,
			visibility: document.visibilityState,
			// `desk`, and it is load-bearing rather than telemetry. A record from here with no
			// active room is the ONLY evidence the server has that somebody is in ERPNext with
			// chat closed — the row of the notification table that would otherwise be
			// indistinguishable from "not logged in", because Frappe v16 exposes no view of who
			// holds a socket. Without this the two rows collapse and an operator reading the
			// debug output cannot tell a colleague at their desk from one who has gone home.
			surface: "desk",
		})
			.then((res) => {
				const seconds = Number(res && res.heartbeat_seconds) || 0;
				if (seconds > 0 && seconds * 1000 !== this._heartbeatMs) this.restartHeartbeat(seconds * 1000);
				return res;
			})
			.catch(() => {
				/* presence is an accelerator; a missed beat expires by TTL */
				return null;
			});
	}

	restartHeartbeat(ms) {
		this._heartbeatMs = ms;
		if (this._heartbeat) clearInterval(this._heartbeat);
		this._heartbeat = setInterval(() => this.beatPresence(), ms);
	}

	startHeartbeat() {
		this.beatPresence();
		this.restartHeartbeat(this._heartbeatMs || 20000);

		// Out of band on every transition, for the same reason the SPA does it: the server
		// suppresses notifications from the last record this tab wrote, so a stale record is a
		// wrong notification decision for up to a whole interval.
		window.addEventListener("focus", () => this.beatPresence());
		window.addEventListener("blur", () => this.beatPresence());
		document.addEventListener("visibilitychange", () => this.beatPresence());
		window.addEventListener("online", () => this.beatPresence());
		window.addEventListener("pagehide", () => {
			call(M.GOODBYE, { client_id: this.clientId }, { keepalive: true }).catch(() => {});
		});
	}

	build() {
		clear(this.host);
		this.els = {};
		// Its own strip, above the list. `notice()` used to render INTO `els.list`, which is
		// the entire left pane — every room row, the unread badges and the "Message someone…"
		// box — so one failed room open replaced the bubble's whole navigation surface with a
		// single line of grey text. Nothing repainted it either: `ensureLoaded` early-returns
		// on `this.loaded`, and the reconnect path bails when there is no open room, which is
		// exactly the state the failure left behind. The user's only fix was reloading Desk.
		this.els.notice = el("div", { class: "triton-chat-notice is-hidden", role: "status" });

		// The people picker is built ONCE, here, outside the region `renderRooms` repaints.
		// It used to be rebuilt on every repaint — and a repaint happens on every inbound
		// `chat_unread_updated`, i.e. whenever any colleague sends anything in any room — so a
		// user half way through typing a name lost both the text and the keyboard focus, with
		// the following keystrokes going to `document.body`.
		this.els.newSearch = el("input", {
			class: "triton-chat-newinput",
			type: "search",
			placeholder: "Message someone…",
			"aria-label": "Search people to message",
			oninput: (ev) => this.searchPeople(ev.target.value),
			onkeydown: (ev) => {
				if (isComposingKey(ev)) return;
				if (ev.key === "Escape") this.clearPeople();
			},
		});
		this.els.people = el("div", { class: "triton-chat-people is-hidden" });
		this.els.roomRows = el("div", { class: "triton-chat-roomrows" });

		// The bubble gets a one-line people picker rather than the SPA's modal: a 380px panel
		// has no room for a dialog, and the bubble's job here is "message somebody quickly".
		// Group creation is deliberately SPA-only — naming a room and picking a roster is not a
		// thing to do in a strip this narrow.
		this.els.list = el("div", { class: "triton-chat-rooms" }, [
			el("div", { class: "triton-chat-newrow" }, [this.els.newSearch]),
			this.els.people,
			this.els.roomRows,
		]);
		this.els.conversation = el("div", { class: "triton-chat-conv is-hidden" }, [
			(this.els.convHead = el("div", { class: "triton-chat-conv-head" }, [
				el("button", {
					class: "triton-icon-btn",
					type: "button",
					"aria-label": "Back to conversations",
					text: "←",
					onclick: () => this.closeRoom(),
				}),
				(this.els.convTitle = el("span", { class: "triton-chat-conv-title", text: "" })),
			])),
			(this.els.messages = el("div", { class: "triton-chat-messages" })),
			(this.els.typing = el("div", { class: "triton-chat-typing", "aria-live": "off" })),
			el("div", { class: "triton-chat-composer" }, [
				(this.els.menu = el("ul", { class: "triton-chat-mentions is-hidden", role: "listbox" })),
				(this.els.text = el("textarea", {
					class: "triton-chat-text",
					rows: "1",
					placeholder: "Message",
					"aria-label": "Message",
					onkeydown: (ev) => this.onKey(ev),
					oninput: (ev) => this.onInput(ev),
					onblur: () => this.onBlur(),
				})),
				el("button", {
					class: "triton-chat-send",
					type: "button",
					"aria-label": "Send",
					text: "➤",
					onclick: () => this.send(),
				}),
			]),
		]);

		this.host.appendChild(this.els.notice);
		this.host.appendChild(this.els.list);
		this.host.appendChild(this.els.conversation);
		this.els.messages.addEventListener("scroll", () => this.onScroll(), { passive: true });

		// Focus is one of the three read conditions, so a change in it must re-open the
		// question. Losing focus flushes what is already earned; regaining it restarts the
		// dwell for whatever is on screen. Without the focus half, alt-tabbing away and back
		// leaves every pending dwell cleared with nothing to restart it.
		window.addEventListener("focus", () => this.evaluateRead());
		window.addEventListener("blur", () => this.flushRead(true));
		document.addEventListener("visibilitychange", () => {
			if (document.visibilityState === "visible") this.evaluateRead();
			else this.flushRead(true);
		});
	}

	/** Load the room list, once per Desk page. Cheap: zero joins, denormalised columns. */
	async ensureLoaded() {
		if (this.loaded) return;
		this.loaded = true;
		try {
			this.rooms = (await call(M.ROOMS)) || [];
			this.renderRooms();
			this.reportUnread();
		} catch (err) {
			// `loaded` back to false so the next tab switch retries. Only the room rows are
			// replaced — the picker stays, so "message someone" still works even when the list
			// itself could not be read.
			this.loaded = false;
			clear(this.els.roomRows);
			this.els.roomRows.appendChild(
				el("div", { class: "triton-chat-empty", text: "Conversations are unavailable right now." })
			);
		}
	}

	/** Repaint the room rows **only**. The picker above them is built once and left alone. */
	renderRooms() {
		clear(this.els.roomRows);

		if (!this.rooms.length) {
			this.els.roomRows.appendChild(
				el("div", { class: "triton-chat-empty", text: "No conversations yet." })
			);
			return;
		}
		for (const room of this.rooms) {
			if (room.is_archived) continue;
			const unread = Number(room.unread) || 0;
			this.els.roomRows.appendChild(
				el(
					"button",
					{
						class: "triton-chat-room" + (unread ? " is-unread" : ""),
						type: "button",
						onclick: () => this.openRoom(room.name),
					},
					[
						el("span", { class: "triton-chat-avatar", text: initials(this.titleOf(room)) }),
						el("span", { class: "triton-chat-room-main" }, [
							el("span", { class: "triton-chat-room-title", text: this.titleOf(room) }),
							el("span", { class: "triton-chat-room-preview", text: room.last_message_preview || "" }),
						]),
						el("span", { class: "triton-chat-room-time", text: relativeTime(room.last_message_at) }),
						unread
							? el("span", {
									class: "triton-chat-badge",
									text: unread > 99 ? "99+" : String(unread),
									"aria-label": `${unread} unread`,
								})
							: null,
					]
				)
			);
		}
	}

	searchPeople(query) {
		const term = (query || "").trim();
		if (!term) {
			this.clearPeople();
			return;
		}
		clearTimeout(this._peopleTimer);
		this._peopleTimer = setTimeout(async () => {
			let people = [];
			try {
				people = (await call(M.PEOPLE, { query: term })) || [];
			} catch (e) {
				people = [];
			}
			clear(this.els.people);
			this.els.people.classList.toggle("is-hidden", !people.length);
			for (const person of people) {
				this.els.people.appendChild(
					el(
						"button",
						{
							class: "triton-chat-person",
							type: "button",
							onclick: () => this.startDm(person.user),
						},
						[
							el("span", { class: "triton-chat-avatar", text: initials(person.label) }),
							el("span", { class: "triton-chat-person-name", text: person.label }),
						]
					)
				);
			}
		}, 150);
	}

	clearPeople() {
		clearTimeout(this._peopleTimer);
		if (this.els.newSearch) this.els.newSearch.value = "";
		if (this.els.people) {
			clear(this.els.people);
			this.els.people.classList.add("is-hidden");
		}
	}

	/** Idempotent server-side: clicking somebody you already talk to opens that room. */
	async startDm(user) {
		try {
			const room = await call(M.NEW_DM, { user });
			this.clearPeople();
			if (room && room.name) {
				const known = this.rooms.filter((r) => r.name === room.name).length;
				if (!known) this.rooms.unshift(room);
				this.renderRooms();
				await this.openRoom(room.name);
			}
		} catch (err) {
			this.notice((err && err.message) || "Could not start that conversation.");
		}
	}

	// ------------------------------------------------------------------ the doc-room join
	//
	// **Binding a handler is not subscribing.** `frappe.realtime.on(name, fn)` registers a
	// callback; the server only DELIVERS a room's events to sockets that have joined that
	// room. The bubble listened for all nine chat events and never emitted `doc_subscribe`,
	// so it received nothing — silently, because there is no error for "you are not in the
	// room you never asked to join".
	//
	// Only the ACTIVE room is joined. Unread counters ride the user room
	// (`chat_unread_updated`), which the server joins from the authenticated session with no
	// client verb at all, so the badge works without subscribing to anything.
	//
	// Two traps in Frappe's client, both read from `socketio_client.js` (v16):
	//
	//  1. `doc_subscribe` is throttled to **one per second GLOBALLY** via
	//     `frappe.flags.doc_subscribe`, and a throttled call logs "throttled" and returns —
	//     it does not queue and it does not add to `open_docs`. Opening a room within a
	//     second of any form subscribing silently never joins. Hence the verify-and-retry.
	//  2. `open_docs` is **never replayed on reconnect** — nothing iterates it in the connect
	//     handler — and `doc_subscribe` early-returns when the key is present. So after any
	//     disconnect the client believes it is subscribed and refuses to re-emit, forever.
	//     The GCLB will disconnect us, so this is the common case, not the exotic one.

	_docKey(room) {
		return "Chat Room:" + room;
	}

	_realtime() {
		const rt = window.frappe && frappe.realtime;
		return rt && typeof rt.doc_subscribe === "function" ? rt : null;
	}

	/** Join `room`'s document room, retrying past the 1/sec throttle. */
	joinRoom(room, attempt) {
		const rt = this._realtime();
		if (!rt || !room) return;
		rt.doc_subscribe("Chat Room", room);

		// `open_docs` gains the key only when the emit actually happened, so it distinguishes
		// "throttled, never sent" from "sent". It does NOT prove the server accepted the join
		// — a refused join is silent and never settles — which is why the read paths re-fetch
		// regardless and never treat a subscribe as delivery.
		const sent = rt.open_docs && rt.open_docs.has(this._docKey(room));
		if (sent || (attempt || 0) >= 4) return;
		clearTimeout(this._joinTimer);
		this._joinTimer = setTimeout(() => {
			if (this.room === room) this.joinRoom(room, (attempt || 0) + 1);
		}, 1100);
	}

	leaveRoom(room) {
		const rt = this._realtime();
		if (!rt || !room || typeof rt.doc_unsubscribe !== "function") return;
		rt.doc_unsubscribe("Chat Room", room);
	}

	/**
	 * Re-join after a reconnect, clearing Frappe's stale bookkeeping first.
	 *
	 * Without the delete, `doc_subscribe` sees the key still in `open_docs` and returns
	 * immediately — so the one call that matters after a disconnect is the one call the
	 * client refuses to make.
	 */
	rejoinAfterReconnect() {
		const rt = this._realtime();
		if (!rt || !this.room) return;
		if (rt.open_docs) rt.open_docs.delete(this._docKey(this.room));
		this.joinRoom(this.room);
		this.resync();
	}

	async openRoom(room) {
		this.flushRead(true);
		// **Before** `this.room` moves. The composer node is built once and lives for the whole
		// Desk page, and nothing used to reset it on a switch — so typing to one coworker,
		// pressing "←", opening a different conversation and pressing Enter delivered that text
		// to the wrong person. The SPA has had per-room drafts since it shipped; this is the
		// same behaviour, not a new feature.
		if (this.room && this.room !== room) {
			this.saveDraft();
			this.leaveRoom(this.room);
		}
		this.room = room;
		this.joinRoom(room);
		this.readBatcher.reset();
		// Tell the server which room this tab is watching before the first message can arrive
		// in it, or the very next message notifies somebody who is looking straight at it.
		this.beatPresence();
		try {
			this.roomDetail = await call(M.ROOM, { room });
			this.marks = (this.roomDetail.members || []).map((m) => ({
				user: m.user,
				last_read_seq: m.last_read_seq,
			}));
			const mine = (this.roomDetail.members || []).find((m) => m.user === this.me);
			this.readBatcher.prime((mine && mine.last_read_seq) || 0);

			const page = await call(M.MESSAGES, { room, limit: BUBBLE_PAGE });
			this.messages = page.messages || [];
			this.hasMore = !!page.has_more;
		} catch (err) {
			this.notice("That conversation is no longer available.");
			this.closeRoom();
			return;
		}

		this.els.convTitle.textContent = this.titleOf(this.roomDetail);

		// Tier 3 of the SPA's restoration (§4.3) lives on the server, and until now ONLY the
		// SPA wrote it — so somebody who lives in the bubble, which is everybody today, opened
		// /chat cold to an empty room list. The hint has to be written by whichever surface the
		// user actually used. Throttled server-side to one write per 30s per user.
		call(M.LAST_OPEN, { room, thread: null }).catch(() => {});
		this.els.list.classList.add("is-hidden");
		this.els.conversation.classList.remove("is-hidden");
		this.restoreDraft();
		this.renderMessages();
		this.scrollDown();
		this.els.text.focus();
		this.notifyState();
	}

	/** Park the composer's contents under the room they were typed for. */
	saveDraft() {
		if (!this.room) return;
		const text = this.els.text.value || "";
		if (text.trim()) this.drafts.set(this.room, { text, mentions: this.composerMentions });
		else this.drafts.delete(this.room);
	}

	/** Put back whatever was typed for the room now open — and nothing from any other room. */
	restoreDraft() {
		const stored = this.drafts.get(this.room);
		this.els.text.value = (stored && stored.text) || "";
		// The anchors travel with the text they point into. Carrying the previous room's
		// anchors over new text would leave them indexing into a string they were not measured
		// against; `_clean_mentions` drops non-members server-side, so the damage was cosmetic,
		// but a span at the wrong offset is still a chip drawn around the wrong word.
		this.composerMentions = (stored && stored.mentions) || [];
	}

	closeRoom() {
		this.flushRead(true);
		this.saveDraft();
		this.leaveRoom(this.room);
		this.room = null;
		this.roomDetail = null;
		this.messages = [];
		this.els.conversation.classList.add("is-hidden");
		this.els.list.classList.remove("is-hidden");
		this.notifyState();
	}

	renderMessages() {
		const rows = mergeForRender(this.messages, this.outbox.pending());
		clear(this.els.messages);
		let previousSender = null;
		for (const message of rows) {
			const sender = message.sender || message.sender_email;
			const grouped = sender === previousSender && !message.is_deleted;
			previousSender = message.is_deleted ? null : sender;
			this.els.messages.appendChild(this.renderMessage(message, grouped));
		}
		this.evaluateRead();
	}

	renderMessage(message, grouped) {
		const row = el("div", {
			class:
				"triton-chat-msg" +
				(grouped ? " is-grouped" : "") +
				(message.is_deleted ? " is-deleted" : "") +
				(message.state === "pending" ? " is-pending" : "") +
				(message.state === "failed" ? " is-failed" : ""),
			"data-seq": message.seq == null ? "" : String(message.seq),
		});

		if (!grouped) {
			row.appendChild(el("div", { class: "triton-chat-msg-who", text: this.nameOf(message) }));
		}

		const body = el("div", { class: "triton-chat-msg-text" });
		if (message.is_deleted) {
			body.classList.add("is-tombstone");
			body.textContent = "This message was deleted";
		} else {
			// Same rule as everywhere else in chat: mention spans and links are built as nodes,
			// never interpolated into HTML. Message bodies are user-authored.
			for (const token of mentions.tokenizeMentions(message.text || "", message.mentions || [])) {
				if (token.type === "mention") {
					body.appendChild(
						el("span", {
							class: "triton-chat-mention" + (token.user === this.me ? " is-me" : ""),
							text: token.value,
						})
					);
				} else {
					for (const node of linkify(token.value)) body.appendChild(node);
				}
			}
		}
		row.appendChild(body);

		if (message.attachments && message.attachments.length) {
			const wrap = el("div", { class: "triton-chat-attachments" });
			for (const attachment of message.attachments) {
				wrap.appendChild(
					el("a", {
						class: "triton-chat-attachment",
						href: attachment.file_url || "#",
						target: "_blank",
						rel: "noopener noreferrer",
						text: "📎 " + (attachment.file_name || "Attachment"),
					})
				);
			}
			row.appendChild(wrap);
		}

		if (message.reply_count) {
			row.appendChild(
				el("div", {
					class: "triton-chat-thread",
					text: message.reply_count === 1 ? "1 reply" : `${message.reply_count} replies`,
				})
			);
		}

		if (message.state === "failed") {
			row.appendChild(
				el("div", { class: "triton-chat-failed" }, [
					el("span", { text: message.error || "Failed to send" }),
					el("button", {
						class: "triton-chat-retry",
						type: "button",
						text: "Retry",
						onclick: () => this.retry(message),
					}),
				])
			);
		}

		return row;
	}

	// ------------------------------------------------------------------ composer

	onKey(ev) {
		// FIRST, above the mention menu. Every branch below is a key an IME may own while a
		// composition is in flight — Enter and Tab commit a candidate, Escape cancels one, the
		// arrows move through it — so a guard placed after the menu block protects the exact
		// keystrokes it exists to protect on every path except the one the user is on.
		if (isComposingKey(ev)) return;
		if (this.menuOpen) {
			if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
				ev.preventDefault();
				const delta = ev.key === "ArrowDown" ? 1 : -1;
				const count = this.menuTargets.length || 1;
				this.menuIndex = (this.menuIndex + delta + count) % count;
				this.renderMenu();
				return;
			}
			if (ev.key === "Enter" || ev.key === "Tab") {
				ev.preventDefault();
				this.chooseMention(this.menuTargets[this.menuIndex]);
				return;
			}
			if (ev.key === "Escape") {
				ev.preventDefault();
				this.closeMenu();
				return;
			}
		}
		if (ev.key === "Enter" && !ev.shiftKey) {
			ev.preventDefault();
			this.send();
		}
	}

	onInput(ev) {
		const textarea = ev.target;
		textarea.style.height = "auto";
		textarea.style.height = Math.min(textarea.scrollHeight, 90) + "px";
		this.composerMentions = mentions.reanchor(textarea.value, this.composerMentions);
		this.notifyState();

		const signal = this.typingOut.onInput(textarea.value, Date.now());
		if (signal) this.emitTyping(signal === "stopped");

		const trigger = mentions.findTrigger(textarea.value, textarea.selectionStart);
		if (!trigger) {
			this.closeMenu();
			return;
		}
		this.trigger = trigger;
		clearTimeout(this._menuTimer);
		this._menuTimer = setTimeout(async () => {
			try {
				const res = await call(M.MENTION_TARGETS, { room: this.room, query: trigger.query });
				this.menuTargets = res.results || [];
				this.menuIndex = 0;
				this.menuOpen = this.menuTargets.length > 0;
				this.renderMenu();
			} catch (e) {
				this.closeMenu();
			}
		}, 150);
	}

	onBlur() {
		const signal = this.typingOut.stop();
		if (signal) this.emitTyping(true);
		setTimeout(() => this.closeMenu(), 150);
	}

	renderMenu() {
		this.els.menu.classList.toggle("is-hidden", !this.menuOpen);
		clear(this.els.menu);
		(this.menuTargets || []).forEach((target, index) => {
			this.els.menu.appendChild(
				el("li", {
					class:
						"triton-chat-mention-option" +
						(index === this.menuIndex ? " is-active" : "") +
						(target.kind === "triton" ? " is-triton" : ""),
					role: "option",
					"aria-selected": index === this.menuIndex ? "true" : "false",
					text: target.label,
					onmousedown: (ev) => {
						ev.preventDefault();
						this.chooseMention(target);
					},
				})
			);
		});
	}

	chooseMention(target) {
		if (!target || !this.trigger) return;
		const result = mentions.insertMention(this.els.text.value, this.trigger, target);
		this.els.text.value = result.text;
		this.els.text.setSelectionRange(result.caret, result.caret);
		this.composerMentions = mentions.reanchor(
			result.text,
			this.composerMentions.concat([result.mention])
		);
		this.closeMenu();
		this.els.text.focus();
	}

	closeMenu() {
		this.menuOpen = false;
		this.trigger = null;
		this.els.menu.classList.add("is-hidden");
	}

	async send() {
		const text = (this.els.text.value || "").trim();
		if (!text || !this.room) return;
		this.els.text.value = "";
		this.els.text.style.height = "auto";
		const payload = mentions.toPayload(this.composerMentions);
		this.composerMentions = [];
		const signal = this.typingOut.stop();
		if (signal) this.emitTyping(true);

		const clientMessageId = newClientMessageId();
		this.outbox.enqueue({
			clientMessageId,
			room: this.room,
			text,
			sender: this.me,
			attachments: [],
			now: Date.now(),
		});
		this.renderMessages();
		this.scrollDown();

		try {
			const row = await call(M.SEND, {
				room: this.room,
				text,
				client_message_id: clientMessageId,
				mentions: payload,
			});
			const result = this.outbox.reconcile(row, Date.now());
			if (result.action !== "ignored") this.messages.push(result.entry || row);
		} catch (err) {
			this.outbox.fail(clientMessageId, err);
		}
		this.renderMessages();
		this.scrollDown();
		this.notifyState();
	}

	async retry(message) {
		// Same `client_message_id`: the server's unique index turns a duplicate insert into a
		// no-op and returns the existing row as success.
		const entry = this.outbox.retry(message.client_message_id, Date.now());
		if (!entry) return;
		this.renderMessages();
		try {
			const row = await call(M.SEND, {
				room: entry.room,
				text: entry.text,
				client_message_id: entry.client_message_id,
			});
			const result = this.outbox.reconcile(row, Date.now());
			if (result.action !== "ignored") this.messages.push(result.entry || row);
		} catch (err) {
			this.outbox.fail(entry.client_message_id, err);
		}
		this.renderMessages();
	}

	// ------------------------------------------------------------------ realtime + read state

	/** Fed by the widget's shared socket, so the bubble and the SPA never open two. */
	onRealtime(name, payload) {
		if (!payload) return;
		if (name === "chat_unread_updated") {
			// `reconcile` means the counters moved for more than one room — mark-all-read from
			// another tab. The event cannot carry the new list, so it asks for a refetch; the
			// badge used to sit stale until the Desk page was reloaded.
			if (payload.reconcile) {
				this.resyncRooms();
				return;
			}
			const room = this.rooms.find((r) => r.name === payload.room);
			if (room) room.unread = Number(payload.unread) || 0;
			this.renderRooms();
			this.reportUnread();
			return;
		}
		// Not room-scoped: it says the caller's membership changed, and the room they are
		// looking at may be one they no longer belong to.
		if (name === "chat_room_updated") {
			this.resyncRooms();
			return;
		}
		if (payload.room !== this.room) return;
		if (name === "chat_message_created") {
			this.backfill();
		} else if (name === "chat_message_edited" || name === "chat_message_deleted") {
			// Neither an edit nor a tombstone changes `seq`, so `backfill()` — which asks for
			// rows strictly newer than the newest one held — can never return the affected row.
			// Without this branch a message deleted in the SPA stayed on screen, original text
			// and all, for every bubble user in that room until they reloaded Desk. The author
			// had already been told "It will show as deleted for everyone."
			this.refreshMessage(payload.message);
		} else if (name === "chat_typing" && payload.user !== this.me) {
			this.typingIn.start(payload.room, null, payload.user, Date.now());
			this.renderTyping();
		} else if (name === "chat_typing_stopped") {
			this.typingIn.stop(payload.room, null, payload.user);
			this.renderTyping();
		} else if (name === "chat_read_receipt") {
			const index = this.marks.findIndex((m) => m.user === payload.user);
			if (index >= 0) this.marks[index].last_read_seq = payload.last_read_seq;
			else this.marks.push({ user: payload.user, last_read_seq: payload.last_read_seq });
			this.renderMessages();
		}
	}

	/** Re-read one message in place — the edit and tombstone path. */
	async refreshMessage(message) {
		if (!message || !this.room) return;
		try {
			const res = await call(M.CONTEXT, { message, limit: 1 });
			for (const row of res.messages || []) {
				const index = this.messages.findIndex((m) => m.name === row.name);
				if (index >= 0) this.messages[index] = row;
			}
			this.renderMessages();
		} catch (e) {
			/* the next full room open repaints it */
		}
	}

	/** Re-read the room list wholesale. The reconciliation path, not the accelerator. */
	async resyncRooms() {
		// A resync means the socket dropped and came back, and the commonest cause of that is a
		// deploy — which FLUSHDBs the presence keyspace on its way past. Re-beating here bounds
		// the "everybody looks absent, so everybody gets notified about everything" window to
		// reconnect latency rather than to the heartbeat interval.
		this.beatPresence();
		try {
			this.rooms = (await call(M.ROOMS)) || [];
			this.renderRooms();
			this.reportUnread();
			// The open room may no longer be in the list — removed, or archived. Backing out is
			// the honest response; staying in a room the server would now refuse is not.
			if (this.room && !this.rooms.some((r) => r.name === this.room)) {
				this.notice("You are no longer in that conversation.");
				this.closeRoom();
			}
		} catch (e) {
			/* the next event or page load repaints */
		}
	}

	async backfill() {
		if (!this.room) return;
		const atBottom = this.atBottom();
		try {
			const page = await call(M.MESSAGES, {
				room: this.room,
				after_seq: this.newestSeq(),
				limit: BUBBLE_PAGE,
			});
			for (const row of page.messages || []) {
				const result = this.outbox.reconcile(row, Date.now());
				if (result.action === "ignored") continue;
				const index = this.messages.findIndex((m) => m.name === row.name);
				if (index >= 0) this.messages[index] = result.entry || row;
				else this.messages.push(result.entry || row);
			}
			this.messages.sort((a, b) => (a.seq || 0) - (b.seq || 0));
			this.renderMessages();
			if (atBottom) this.scrollDown();
		} catch (e) {
			/* the next resync picks it up */
		}
	}

	/** Full re-read after a reconnect. Realtime is not durable; this is the correctness path. */
	async resync() {
		this.typingIn.clearAll();
		this.renderTyping();
		try {
			this.rooms = (await call(M.ROOMS)) || [];
			this.renderRooms();
			this.reportUnread();
			if (this.room) await this.backfill();
		} catch (e) {
			/* leave the last good state on screen */
		}
	}

	newestSeq() {
		let seq = 0;
		for (const message of this.messages) if ((message.seq || 0) > seq) seq = message.seq;
		return seq;
	}

	onScroll() {
		this.evaluateRead();
		this.notifyState();
	}

	/**
	 * The same read rule as the SPA: visible AND the window focused AND a dwell. The bubble is
	 * small enough that "visible" is measured against the panel rather than with an observer
	 * per row, but the focus condition is identical — a Desk tab behind a spreadsheet is not
	 * reading anything.
	 */
	evaluateRead() {
		if (!this.room) return;
		const focused = document.visibilityState === "visible" && document.hasFocus();
		const box = this.els.messages.getBoundingClientRect();
		const now = Date.now();
		for (const node of this.els.messages.children) {
			const seq = Number(node.dataset.seq);
			if (!seq) continue;
			const rect = node.getBoundingClientRect();
			const visible = rect.bottom > box.top && rect.top < box.bottom;
			this.readBatcher.observe(seq, visible && focused, now);
		}
		this.flushRead(false);

		// **Come back.** `observe()` promotes on the SECOND call that finds the conditions
		// still holding; the first only records when they started. Every other caller here is
		// a DOM event (a render, a scroll), and a message that simply sits on screen being
		// read generates no further events — so without this the dwell never completes and no
		// read mark ever moves except via the author's own send. That is exactly what
		// production showed.
		//
		// One follow-up, not an interval: if the conditions stop holding, `observe` drops the
		// entry, `hasPending()` goes false, and nothing reschedules.
		clearTimeout(this._dwellTimer);
		if (this.readBatcher.hasPending()) {
			this._dwellTimer = setTimeout(() => this.evaluateRead(), READ_DWELL_MS + 60);
		}
	}

	flushRead(force) {
		if (!this.room) return;
		const now = Date.now();
		if (!this.readBatcher.shouldFlush(now, force)) return;
		const seq = this.readBatcher.take(now);
		if (!seq) return;
		call(M.MARK_READ, { room: this.room, up_to_seq: seq }, { keepalive: !!force })
			.then((res) => res && this.readBatcher.acknowledge(res.last_read_seq))
			.catch(() => {});
	}

	renderTyping() {
		const names = this.typingIn.active(this.room, null, Date.now()).map((user) => {
			const member = ((this.roomDetail || {}).members || []).find((m) => m.user === user);
			return member ? member.full_name : user;
		});
		this.els.typing.textContent = typingLabel(names);
	}

	emitTyping(stopped) {
		if (!this.room) return;
		call(M.TYPING, { room: this.room, stopped: stopped ? 1 : 0 }).catch(() => {});
	}

	reportUnread() {
		const total = this.rooms.reduce((sum, room) => sum + (Number(room.unread) || 0), 0);
		if (this.hooks.onUnread) this.hooks.onUnread(total);
	}

	// ------------------------------------------------------------------ handoff

	/** What the widget writes into the handoff record when the user expands. */
	handoffState() {
		const anchor = this.topVisibleMessage();
		return {
			room: this.room,
			thread: this.thread,
			anchorMessage: anchor && anchor.name,
			anchorRatio: anchor && anchor.ratio,
			draft: this.els.text.value,
			surface: "coworker",
		};
	}

	topVisibleMessage() {
		const box = this.els.messages.getBoundingClientRect();
		for (const node of this.els.messages.children) {
			const rect = node.getBoundingClientRect();
			if (rect.bottom > box.top) {
				const seq = Number(node.dataset.seq);
				const message = this.messages.find((m) => m.seq === seq);
				if (!message) continue;
				return { name: message.name, ratio: Math.max(0, Math.min(1, (rect.top - box.top) / Math.max(1, box.height))) };
			}
		}
		return null;
	}

	/** Restore from a handoff record when the SPA hands control back to the bubble. */
	async restore(record) {
		if (!record || !record.room) return;
		await this.ensureLoaded();
		await this.openRoom(record.room);
		if (record.draft) this.els.text.value = record.draft;
	}

	notifyState() {
		if (this.hooks.onStateChange) this.hooks.onStateChange(this.handoffState());
	}

	/**
	 * Say something went wrong **without** destroying what the user was looking at.
	 *
	 * Auto-clears, like the SPA's. A message that stays on screen forever is indistinguishable
	 * from a broken pane, which is how the previous version of this read.
	 */
	notice(text) {
		this.els.notice.textContent = text;
		this.els.notice.classList.remove("is-hidden");
		clearTimeout(this._noticeTimer);
		this._noticeTimer = setTimeout(() => {
			this.els.notice.classList.add("is-hidden");
			this.els.notice.textContent = "";
		}, 6000);
	}

	// ------------------------------------------------------------------ helpers

	titleOf(room) {
		if (!room) return "";
		// `display_title` is resolved server-side per viewer: the room's own title for a group,
		// the OTHER participant's full name for a DM. The dm_user_* fallback below is kept for
		// a payload from an older build, and it is why this used to render a raw email — a
		// `User` docname on this site IS an email address.
		if (room.display_title) return room.display_title;
		if (room.title) return room.title;
		if (room.room_type === "Direct Message") {
			return room.other_user_name || room.other_user ||
				(room.dm_user_1 === this.me ? room.dm_user_2 : room.dm_user_1) || "Direct message";
		}
		return room.name || "";
	}

	nameOf(message) {
		if (message.sender_kind === "Triton") return "Triton";
		const member = ((this.roomDetail || {}).members || []).find((m) => m.user === message.sender);
		if (member) return member.full_name || member.user;
		return message.sender || message.sender_email || "Unknown";
	}

	atBottom() {
		const box = this.els.messages;
		return box.scrollHeight - box.scrollTop - box.clientHeight <= 40;
	}

	scrollDown() {
		this.els.messages.scrollTop = this.els.messages.scrollHeight;
	}
}
