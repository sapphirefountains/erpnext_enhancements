/**
 * `@mention` composer logic: trigger detection, insertion, and the structured payload.
 *
 * All pure. The server re-validates everything this produces
 * (`chat/api/compose.py::_clean_mentions`) rather than trusting it — a mention decides who
 * Phase 4 notifies, so a client-supplied list is an input, not a decision — but the offsets
 * have to be computed here because only the composer knows where a display name ended.
 *
 * `@triton` is offered in every room and produces `mention_type: "Triton"` with no user. What
 * happens next is Phase 5's; this phase makes the mention and renders the placeholder.
 */

/** The client-side sentinel for the assistant. Matches `chat/api/mentions.py::TRITON_KEY`. */
export const TRITON_KEY = "__triton__";

/**
 * Find the `@` trigger the caret is currently inside, if any.
 *
 * A trigger only counts **at a word boundary** — start of text, or after whitespace or an
 * opening bracket. Mid-word `@` is an email address, and popping an autocomplete over
 * somebody typing `nikolas@sapphirefountains.com` is the behaviour that makes people turn
 * mentions off.
 *
 * @returns {?{start: number, query: string}} `start` is the index of the `@`.
 */
export function findTrigger(text, caret) {
	const s = String(text || "");
	const pos = Math.max(0, Math.min(Number(caret) || 0, s.length));

	for (let i = pos - 1; i >= 0; i--) {
		const ch = s[i];
		if (ch === "@") {
			const before = i === 0 ? "" : s[i - 1];
			if (before && !/[\s([{<]/.test(before)) return null;
			const query = s.slice(i + 1, pos);
			// A space closes the trigger. Multi-word display names are handled by picking from
			// the menu, which inserts the whole name atomically, not by matching across spaces.
			if (/\s/.test(query)) return null;
			return { start: i, query };
		}
		if (/\s/.test(ch)) return null;
	}
	return null;
}

/**
 * Insert a chosen target at the trigger, returning the new text, caret and mention record.
 *
 * The inserted text is the DISPLAY NAME with a trailing space (`@Jane Doe `), because that is
 * what the relay sends to Google: under user auth the Chat leg is text-only, so a mention
 * that renders as a rich chip here must degrade to something a human reads correctly in the
 * native client. `@Jane Doe` does; a bare user id (`jane@sapphirefountains.com`) reads as an
 * email address, and an opaque chip marker reads as noise.
 */
export function insertMention(text, trigger, target) {
	const s = String(text || "");
	const label = target.kind === "triton" ? "Triton" : String(target.label || target.user || "");
	const inserted = "@" + label + " ";
	const next = s.slice(0, trigger.start) + inserted + s.slice(trigger.start + 1 + trigger.query.length);

	return {
		text: next,
		caret: trigger.start + inserted.length,
		mention: {
			kind: target.kind === "triton" ? "triton" : "user",
			user: target.kind === "triton" ? TRITON_KEY : target.user,
			// The literal text this span is supposed to cover, kept so `reanchor` can find it
			// again after the user types in front of it rather than guessing from offsets.
			label: "@" + label,
			start: trigger.start,
			// The span covers "@Name" and NOT the trailing space: the space is punctuation the
			// user can delete without the mention becoming wrong.
			length: inserted.length - 1,
		},
	};
}

/**
 * Re-anchor the recorded mentions against the text as it now stands, dropping any whose span
 * no longer reads back as the mention it claims to be.
 *
 * Called on every composer change. Without it, typing a character before an existing mention
 * shifts every later offset by one and the stored spans render chips over the wrong words —
 * which looks like corruption rather than like a bug, and survives all the way into the
 * relayed message.
 *
 * Dropped rather than clamped, deliberately: a mention whose text the user edited away is a
 * mention they removed.
 */
export function reanchor(text, mentions) {
	const s = String(text || "");
	const out = [];
	// Search forward from the previous match so two identical mentions of the same person do
	// not both re-anchor onto the first occurrence.
	let cursor = 0;
	for (const mention of mentions || []) {
		const expected = s.substr(mention.start, mention.length);
		const label = mention.label || expected;
		if (expected === label) {
			out.push(mention);
			cursor = Math.max(cursor, mention.start + mention.length);
			continue;
		}
		const found = s.indexOf(label, cursor);
		if (found < 0) continue; // the user edited it away
		out.push({ ...mention, start: found, length: label.length });
		cursor = found + label.length;
	}
	return out;
}

/**
 * The payload `send_message` receives. Shaped as `Chat Mention` child rows so the server does
 * not have to translate, and so a reader of either side sees the same field names.
 */
export function toPayload(mentions) {
	return (mentions || []).map((m) => ({
		mention_type: m.kind === "triton" ? "Triton" : "User",
		user: m.kind === "triton" ? null : m.user,
		start_index: m.start,
		length: m.length,
	}));
}

/** Whether this message mentions Triton — the trigger for the "thinking…" placeholder. */
export function mentionsTriton(mentions) {
	return (mentions || []).some((m) => m.kind === "triton" || m.mention_type === "Triton");
}

/**
 * **Pure.** Split a body into text and mention spans for rendering.
 *
 * Returns tokens the message component turns into DOM with `textContent` — the same shape,
 * and the same reason, as the citation tokenizer. Overlapping or out-of-range spans are
 * dropped rather than trusted: these offsets came off the wire.
 */
export function tokenizeMentions(text, mentions) {
	const s = String(text == null ? "" : text);
	const spans = (mentions || [])
		.map((m) => ({
			start: Number(m.start_index != null ? m.start_index : m.start) || 0,
			length: Number(m.length) || 0,
			user: m.user || null,
			kind: (m.mention_type || m.kind || "User").toLowerCase() === "triton" ? "triton" : "user",
		}))
		.filter((m) => m.length > 0 && m.start >= 0 && m.start + m.length <= s.length)
		.sort((a, b) => a.start - b.start);

	const out = [];
	let cursor = 0;
	for (const span of spans) {
		if (span.start < cursor) continue; // overlapping — keep the first, drop the rest
		if (span.start > cursor) out.push({ type: "text", value: s.slice(cursor, span.start) });
		out.push({
			type: "mention",
			value: s.substr(span.start, span.length),
			user: span.user,
			kind: span.kind,
		});
		cursor = span.start + span.length;
	}
	if (cursor < s.length) out.push({ type: "text", value: s.slice(cursor) });
	return out;
}
