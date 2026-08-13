/**
 * Optimistic send, and the reconciliation that makes "my message appeared twice" structurally
 * impossible.
 *
 * A sent message has FOUR states the UI distinguishes, because ERPNext is the source of truth
 * and the relay to Google Chat is asynchronous by invariant I1:
 *
 * | state     | meaning                                       | rendering                       |
 * |-----------|-----------------------------------------------|---------------------------------|
 * | `pending` | rendered locally, server has not confirmed    | reduced opacity + clock         |
 * | `sent`    | the `Chat Message` row is committed           | normal — this IS "delivered"    |
 * | `relayed` | the outbox has posted it to Google Chat       | subtle, and never gates `sent`  |
 * | `failed`  | the call errored or timed out                 | error styling, Retry + Delete   |
 *
 * Conflating `sent` and `relayed` is a real support cost: a message that shows as "sending"
 * because a Google quota is exhausted has in fact been delivered to every ERPNext client,
 * and telling the user otherwise makes them send it again.
 *
 * THE MECHANISM. The client mints a `client_message_id` BEFORE sending. Phase 1 put a
 * unique index on `Chat Message.client_message_id` for exactly this. Reconciliation is then
 * a map lookup and, crucially, IDEMPOTENT — which is what makes it correct in all four
 * orderings (response first, realtime first, both, retry-after-success), each of which has
 * its own test in `scripts/test_chat_optimistic.js`.
 *
 * Entirely pure: a store of plain objects with no DOM and no clock (`now` is passed in).
 */

export const STATE = {
	PENDING: "pending",
	SENT: "sent",
	RELAYED: "relayed",
	FAILED: "failed",
};

/** How long a message may stay `pending` before it is presented as failed. */
export const PENDING_TIMEOUT_MS = 15000;

/** Cap on the offline queue. Unbounded queues turn a bad connection into a memory leak and
 *  then into a burst the server has to absorb all at once. */
export const OFFLINE_QUEUE_MAX = 50;

/**
 * Mint an id. A UUID with a stable prefix so it is recognisable in a log and in the
 * `Chat Message` table, and so a value that arrives from somewhere else is obviously not one.
 */
export function newClientMessageId() {
	let uuid;
	try {
		uuid = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : null;
	} catch (e) {
		uuid = null;
	}
	if (!uuid) {
		uuid = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
			const r = (Math.random() * 16) | 0;
			return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
		});
	}
	// `client-`, not `spa-`, because this id becomes Google Chat's clientAssignedMessageId
	// when the message is mirrored, and Google requires that prefix. Ours was rejected by our
	// own pre-flight check before the request was sent, which dead-lettered every outbound
	// mirror as non-retryable — messages_relayed sat at 0 while ERPNext looked perfectly fine.
	// The prefix is not matched on anywhere; it is a namespace, and Google gets to name it.
	return "client-" + uuid;
}

/**
 * The pending-message store for one room.
 *
 * Keyed on `client_message_id` throughout. Nothing in here is keyed on a server docname,
 * because the docname is precisely what is not known while the message is pending — and a
 * store that has to re-key on confirmation is a store that can lose an entry between the two
 * keys.
 */
export class OutboxStore {
	constructor() {
		/** @type {Map<string, object>} */
		this.byClientId = new Map();
		/** Server docnames already reconciled, so a late realtime echo cannot resurrect one. */
		this.settled = new Set();
	}

	/** Register a message the user just sent. Returns the optimistic entry to render. */
	enqueue({ clientMessageId, room, text, parentMessage, sender, attachments, now }) {
		const entry = {
			client_message_id: clientMessageId,
			name: null,
			room,
			text: text || "",
			parent_message: parentMessage || null,
			thread_root: parentMessage || null,
			sender,
			sender_kind: "Human",
			message_type: attachments && attachments.length ? "File" : "Text",
			attachments: attachments || [],
			creation: null,
			seq: null,
			state: STATE.PENDING,
			sentAt: now,
			error: null,
			optimistic: true,
		};
		this.byClientId.set(clientMessageId, entry);
		return entry;
	}

	/**
	 * Reconcile against a server row — from the send response OR from `chat_message_created`.
	 *
	 * **Look up by `client_message_id` FIRST.** If it matches a pending local entry, this is
	 * the same message arriving by the other path: reconcile in place, never append a second
	 * bubble. That single ordering rule is what prevents the duplicate, and the unique index
	 * is what makes it reliable rather than hopeful.
	 *
	 * @returns {{action: "reconciled"|"appended"|"ignored", entry: ?object}}
	 */
	reconcile(row, now) {
		if (!row || !row.name) return { action: "ignored", entry: null };

		const key = row.client_message_id || null;
		const pending = key ? this.byClientId.get(key) : null;

		if (pending) {
			Object.assign(pending, row, {
				state: row.sync_state === "Relayed" ? STATE.RELAYED : STATE.SENT,
				optimistic: false,
				error: null,
				reconciledAt: now,
			});
			this.byClientId.delete(key);
			this.settled.add(row.name);
			return { action: "reconciled", entry: pending };
		}

		if (this.settled.has(row.name)) {
			// Already reconciled through the other path. Both orderings converge here, and
			// this branch is the one that makes "both arrive" produce exactly one bubble.
			return { action: "ignored", entry: null };
		}

		this.settled.add(row.name);
		return { action: "appended", entry: row };
	}

	/** Mark a send as failed, keeping the id so a late realtime event still reconciles it. */
	fail(clientMessageId, error) {
		const entry = this.byClientId.get(clientMessageId);
		if (!entry) return null;
		entry.state = STATE.FAILED;
		entry.error = error ? String(error.message || error) : "Failed to send";
		return entry;
	}

	/**
	 * Time out anything that has been pending too long.
	 *
	 * Moves to `failed` with Retry available — but **keeps the entry and its id**, so a
	 * late-arriving realtime event still reconciles it rather than producing a duplicate
	 * next to the failed one. That is the difference between "it eventually appeared" and
	 * "it appeared twice, one of them red".
	 */
	sweepTimeouts(now, timeout = PENDING_TIMEOUT_MS) {
		const timedOut = [];
		for (const entry of this.byClientId.values()) {
			if (entry.state === STATE.PENDING && now - entry.sentAt >= timeout) {
				entry.state = STATE.FAILED;
				entry.error = "Timed out";
				timedOut.push(entry);
			}
		}
		return timedOut;
	}

	/** Prepare a retry. **Same id**, so the server's unique index turns a duplicate insert
	 *  into a detectable no-op and returns the existing row as success. */
	retry(clientMessageId, now) {
		const entry = this.byClientId.get(clientMessageId);
		if (!entry) return null;
		entry.state = STATE.PENDING;
		entry.error = null;
		entry.sentAt = now;
		return entry;
	}

	/** Abandon a failed send. The only way an entry leaves without being reconciled. */
	discard(clientMessageId) {
		return this.byClientId.delete(clientMessageId);
	}

	pending() {
		return Array.from(this.byClientId.values());
	}
}

/**
 * The offline queue. Sends made with the socket down or the network offline are held here in
 * order and flushed on reconnect.
 *
 * Capped, and the cap is surfaced rather than silent: a queue that quietly drops the message
 * a user typed is worse than one that refuses to accept it.
 */
export class OfflineQueue {
	constructor(max = OFFLINE_QUEUE_MAX) {
		this.max = max;
		this.items = [];
	}

	push(item) {
		if (this.items.length >= this.max) return { accepted: false, reason: "full" };
		this.items.push(item);
		return { accepted: true, depth: this.items.length };
	}

	/** Drain in FIFO order. Order matters: messages arrive in the order they were typed. */
	drain() {
		const out = this.items.slice();
		this.items = [];
		return out;
	}

	get length() {
		return this.items.length;
	}
}

/**
 * Merge a page of server rows with the pending entries for the same room, in `seq` order
 * with pending messages last.
 *
 * Ordering is by the SERVER's `seq`, never by local send time. When the server row's
 * position differs from where the optimistic bubble sat, it re-sorts — silently. A visible
 * jump is worse than a wrong-for-200ms position, and the alternative (holding the optimistic
 * position) is how two people's messages end up interleaved differently on their two screens.
 */
export function mergeForRender(rows, pending) {
	const confirmed = (rows || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
	const optimistic = (pending || [])
		.filter((entry) => entry.optimistic || entry.state === STATE.FAILED)
		.sort((a, b) => (a.sentAt || 0) - (b.sentAt || 0));
	return confirmed.concat(optimistic);
}
