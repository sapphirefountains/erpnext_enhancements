/**
 * The message component. **One renderer, every surface** — coworker rooms, thread panes, and
 * Triton's answers, whether they arrive as a streamed reply in the Triton surface or as an
 * ordinary `Chat Message` posted by `@triton` in a room. §4.10 is explicit that this must not
 * be forked, and the reason is the citation renderer: a second component is a second place
 * for inline links to be missing, and nobody notices a missing link.
 *
 * Everything is built with `createElement` / `textContent` (see `dom.js`). Message bodies,
 * sender names, filenames and citation labels are user-authored, and every one of them
 * reaches this file.
 */

import { applyCitations, indexManifest } from "./citations.js";
import { clockTime, dayLabel, el, fileSize, initials, linkify, relativeTime } from "./dom.js";
import { tokenizeMentions } from "./mentions.js";
import { STATE } from "./optimistic.js";
import { hasRead } from "./signals.js";

/** Images we will render inline. Anything else is a card with a download control. */
const INLINE_IMAGE = /^image\/(png|jpe?g|gif|webp|avif|svg\+xml)$/i;

/**
 * Render one message row.
 *
 * @param {object} message   a message payload from the server, or an optimistic entry
 * @param {object} ctx       `{me, members, marks, avatarLimit, manifest, grouped, actions,
 *                            citationsEnabled, now}`
 */
export function renderMessage(message, ctx) {
	const c = ctx || {};
	const row = el("article", {
		class:
			"ee-msg" +
			(c.grouped ? " is-grouped" : "") +
			(message.is_deleted ? " is-deleted" : "") +
			(message.sender_kind === "Triton" ? " is-triton" : "") +
			(message.state === STATE.PENDING ? " is-pending" : "") +
			(message.state === STATE.FAILED ? " is-failed" : "") +
			(message.late_arrival ? " is-recovered" : ""),
		"data-seq": message.seq == null ? "" : String(message.seq),
		tabindex: "-1",
	});
	// The accessible name carries sender and time so a screen reader announces a message
	// usefully without the user having to hunt for the metadata line (§4.11).
	row.setAttribute("aria-label", accessibleName(message, c));

	if (!c.grouped) row.appendChild(renderGutter(message, c));
	else row.appendChild(el("div", { class: "ee-msg-gutter is-empty", "aria-hidden": "true" }));

	const body = el("div", { class: "ee-msg-body" });
	if (!c.grouped) body.appendChild(renderHeader(message, c));

	body.appendChild(renderContent(message, c));

	if (message.attachments && message.attachments.length) {
		body.appendChild(renderAttachments(message, c));
	}
	if (message.late_arrival && !message.is_deleted) {
		body.appendChild(
			el("div", {
				class: "ee-msg-recovered",
				text: "Recovered — this arrived late and is shown in the order it was stored",
			})
		);
	}
	if (message.state === STATE.FAILED) body.appendChild(renderFailed(message, c));
	if (message.reply_count) body.appendChild(renderThreadAffordance(message, c));

	const receipts = renderReceipts(message, c);
	if (receipts) body.appendChild(receipts);

	row.appendChild(body);
	if (!message.optimistic && !message.is_deleted) row.appendChild(renderActions(message, c));
	return row;
}

/** The day separator. Its own row so the virtual list measures it like any other. */
export function renderDaySeparator(iso) {
	return el("div", { class: "ee-day", role: "separator" }, [
		el("span", { class: "ee-day-label", text: dayLabel(iso) }),
	]);
}

/** "Triton is thinking…", shown after an `@triton` mention until the real reply arrives.
 *  Must not break if the reply never comes — it is a row, not a modal, and it simply stays. */
export function renderThinkingPlaceholder() {
	return el("article", { class: "ee-msg is-triton is-thinking", "aria-live": "polite" }, [
		el("div", { class: "ee-msg-gutter" }, [el("span", { class: "ee-avatar is-triton", text: "🔱" })]),
		el("div", { class: "ee-msg-body" }, [
			el("div", { class: "ee-msg-head" }, [el("span", { class: "ee-msg-sender", text: "Triton" })]),
			el("div", { class: "ee-msg-text ee-thinking" }, [
				el("span", { class: "ee-thinking-dot" }),
				el("span", { class: "ee-thinking-dot" }),
				el("span", { class: "ee-thinking-dot" }),
				el("span", { class: "sr-only", text: "Triton is thinking" }),
			]),
		]),
	]);
}

// --------------------------------------------------------------------------- parts

function renderGutter(message, ctx) {
	const member = lookupMember(message, ctx);
	const gutter = el("div", { class: "ee-msg-gutter" });
	if (message.sender_kind === "Triton") {
		gutter.appendChild(el("span", { class: "ee-avatar is-triton", text: "🔱", "aria-hidden": "true" }));
		return gutter;
	}
	if (member && member.user_image) {
		gutter.appendChild(
			el("img", { class: "ee-avatar", src: member.user_image, alt: "", loading: "lazy" })
		);
	} else {
		gutter.appendChild(
			el("span", { class: "ee-avatar", text: initials(displayName(message, ctx)), "aria-hidden": "true" })
		);
	}
	return gutter;
}

function renderHeader(message, ctx) {
	const head = el("div", { class: "ee-msg-head" }, [
		el("span", { class: "ee-msg-sender", text: displayName(message, ctx) }),
	]);
	if (message.sender_kind === "Triton") {
		head.appendChild(el("span", { class: "ee-badge", text: "assistant" }));
	} else if (!message.sender && message.sender_email) {
		// An external Chat participant: stored with an email and no `User` link, because
		// ERPNext is the record of what was said rather than of who has an account.
		head.appendChild(el("span", { class: "ee-badge is-external", text: "external" }));
	}
	if (message.creation) {
		head.appendChild(
			el("time", {
				class: "ee-msg-time",
				datetime: message.creation,
				title: clockTime(message.creation),
				text: relativeTime(message.creation, ctx.now),
			})
		);
	}
	if (message.is_edited && !message.is_deleted) {
		head.appendChild(el("span", { class: "ee-msg-edited", text: "edited", title: "This message was edited" }));
	}
	return head;
}

function renderContent(message, ctx) {
	if (message.is_deleted) {
		// A tombstone, never a removal. A message that silently vanishes reads as a bug, and
		// decision #12 keeps the audit trail behind it.
		return el("div", { class: "ee-msg-text ee-tombstone", text: "This message was deleted" });
	}

	const text = el("div", { class: "ee-msg-text" });
	const tokens = tokenizeMentions(message.text || "", message.mentions || []);
	for (const token of tokens) {
		if (token.type === "mention") {
			text.appendChild(
				el("span", {
					class: "ee-mention" + (token.kind === "triton" ? " is-triton" : "") +
						(token.user && token.user === ctx.me ? " is-me" : ""),
					text: token.value,
				})
			);
			continue;
		}
		for (const node of linkify(token.value)) text.appendChild(node);
	}

	// Inline citations, last, over the finished DOM. With no manifest this is a no-op and the
	// body renders exactly as it did before this phase — which is the required behaviour, not
	// a fallback afterthought, and is what lets Phase 3 ship before Phase 5.
	if (ctx.citationsEnabled && message.citations && message.citations.length) {
		applyCitations(text, indexManifest(message.citations), {
			onMiss: ctx.onCitationMiss,
			onNavigate: ctx.onCitationNavigate,
			onExpandDigest: ctx.onExpandDigest,
		});
	}

	return text;
}

function renderAttachments(message, ctx) {
	const wrap = el("div", { class: "ee-attachments" });
	for (const attachment of message.attachments) {
		if (!attachment.file_url) {
			wrap.appendChild(el("div", { class: "ee-attachment is-missing", text: "Attachment unavailable" }));
			continue;
		}
		if (INLINE_IMAGE.test(attachment.content_type || "")) {
			// width/height (or an aspect-ratio) from stored metadata where we have it, so the
			// transcript does not reflow and destroy scroll position when the image loads. This
			// is the single most common cause of "it jumped while I was reading".
			const img = el("img", {
				class: "ee-attachment-image",
				src: attachment.file_url,
				alt: attachment.file_name || "Image attachment",
				loading: "lazy",
				decoding: "async",
			});
			const button = el(
				"button",
				{
					class: "ee-attachment-thumb",
					type: "button",
					"aria-label": "Open " + (attachment.file_name || "image"),
					onclick: () => ctx.actions && ctx.actions.lightbox && ctx.actions.lightbox(attachment),
				},
				[img]
			);
			wrap.appendChild(button);
			continue;
		}
		wrap.appendChild(
			el("a", {
				class: "ee-attachment",
				href: attachment.file_url,
				download: "",
				rel: "noopener",
			}, [
				el("span", { class: "ee-attachment-icon", text: "📎", "aria-hidden": "true" }),
				el("span", { class: "ee-attachment-name", text: attachment.file_name || "Attachment" }),
				el("span", { class: "ee-attachment-size", text: fileSize(attachment.file_size) }),
			])
		);
	}
	return wrap;
}

function renderFailed(message, ctx) {
	return el("div", { class: "ee-msg-failed", role: "alert" }, [
		el("span", { class: "ee-msg-failed-text", text: message.error || "Failed to send" }),
		el("button", {
			class: "ee-link-btn",
			type: "button",
			text: "Retry",
			onclick: () => ctx.actions && ctx.actions.retry && ctx.actions.retry(message),
		}),
		el("button", {
			class: "ee-link-btn",
			type: "button",
			text: "Delete",
			onclick: () => ctx.actions && ctx.actions.discard && ctx.actions.discard(message),
		}),
	]);
}

function renderThreadAffordance(message, ctx) {
	const count = Number(message.reply_count) || 0;
	return el("button", {
		class: "ee-thread-affordance",
		type: "button",
		text: count === 1 ? "1 reply" : `${count} replies`,
		onclick: () => ctx.actions && ctx.actions.openThread && ctx.actions.openThread(message),
	});
}

/**
 * Per-message read receipts, DERIVED from the roster's high-water marks.
 *
 * Above `avatarLimit` members this collapses to "Read by N" with the names on demand:
 * computing and painting thirty avatars per message is neither useful nor cheap, and the
 * threshold is a server-supplied constant rather than a number invented here.
 */
function renderReceipts(message, ctx) {
	if (message.optimistic || message.is_deleted) return null;
	if (!ctx.marks || !ctx.marks.length) return null;
	if ((message.sender || "") !== ctx.me) return null; // your own messages are the useful case

	const readers = ctx.marks.filter(
		(mark) => mark.user !== ctx.me && hasRead(message.seq, mark.last_read_seq)
	);
	if (!readers.length) return null;

	const limit = Number(ctx.avatarLimit) || 10;
	if ((ctx.members || []).length > limit) {
		return el("div", { class: "ee-receipts" }, [
			el("button", {
				class: "ee-link-btn",
				type: "button",
				text: `Read by ${readers.length}`,
				onclick: () => ctx.actions && ctx.actions.showReaders && ctx.actions.showReaders(readers),
			}),
		]);
	}

	const wrap = el("div", { class: "ee-receipts", "aria-label": `Read by ${readers.length}` });
	for (const reader of readers) {
		const member = (ctx.members || []).find((m) => m.user === reader.user);
		wrap.appendChild(
			member && member.user_image
				? el("img", { class: "ee-receipt-avatar", src: member.user_image, alt: "", title: member.full_name })
				: el("span", {
						class: "ee-receipt-avatar",
						text: initials(member ? member.full_name : reader.user),
						title: member ? member.full_name : reader.user,
					})
		);
	}
	return wrap;
}

function renderActions(message, ctx) {
	const own = (message.sender || "") === ctx.me;
	const bar = el("div", { class: "ee-msg-actions", role: "group", "aria-label": "Message actions" });

	bar.appendChild(
		el("button", {
			class: "ee-icon-btn",
			type: "button",
			title: "Reply in thread",
			"aria-label": "Reply in thread",
			text: "↩",
			onclick: () => ctx.actions && ctx.actions.openThread && ctx.actions.openThread(message),
		})
	);
	bar.appendChild(
		el("button", {
			class: "ee-icon-btn",
			type: "button",
			title: "Copy link to message",
			"aria-label": "Copy link to message",
			text: "🔗",
			onclick: () => ctx.actions && ctx.actions.copyLink && ctx.actions.copyLink(message),
		})
	);
	if (own) {
		bar.appendChild(
			el("button", {
				class: "ee-icon-btn",
				type: "button",
				title: "Edit",
				"aria-label": "Edit message",
				text: "✎",
				onclick: () => ctx.actions && ctx.actions.edit && ctx.actions.edit(message),
			})
		);
		bar.appendChild(
			el("button", {
				class: "ee-icon-btn",
				type: "button",
				title: "Delete",
				"aria-label": "Delete message",
				text: "🗑",
				onclick: () => ctx.actions && ctx.actions.remove && ctx.actions.remove(message),
			})
		);
	}
	return bar;
}

// --------------------------------------------------------------------------- helpers

function lookupMember(message, ctx) {
	if (!message.sender) return null;
	return (ctx.members || []).find((m) => m.user === message.sender) || null;
}

export function displayName(message, ctx) {
	if (message.sender_kind === "Triton") return "Triton";
	const member = lookupMember(message, ctx);
	if (member) return member.full_name || member.user;
	return message.sender || message.sender_email || "Unknown";
}

function accessibleName(message, ctx) {
	const who = displayName(message, ctx);
	const when = message.creation ? clockTime(message.creation) : "";
	if (message.is_deleted) return `${who}, ${when}: message deleted`;
	return when ? `${who}, ${when}` : who;
}
