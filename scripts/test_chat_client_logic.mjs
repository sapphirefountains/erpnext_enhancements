#!/usr/bin/env node
/**
 * The SPA's pure logic: routes, the restoration precedence, the handoff record, optimistic
 * reconciliation, the read batcher, typing, the presence union, and mention offsets.
 *
 * Every one of these is a rule that is easy to state, easy to agree with, and impossible to
 * notice breaking:
 *
 *   * the URL losing to restored state dumps people in the wrong room INTERMITTENTLY, only
 *     when they happen to have restored state pointing elsewhere;
 *   * a non-idempotent reconciliation shows a message twice, but only in one of the four
 *     orderings;
 *   * a non-monotonic read mark un-reads messages the user has already seen, silently;
 *   * a multi-tab presence union that takes the last writer makes notifications stop when
 *     somebody opens a second tab, which nobody connects to the second tab.
 *
 * All of it runs under plain `node`, no runner, no npm install — the shape the repo's other
 * JS guards already use.
 */

import assert from "node:assert/strict";
import { KIND, buildRoute, degrade, encodeSegment, parseRoute, resolveRestoration } from "../erpnext_enhancements/public/js/chat/routes.js";
import { HANDOFF_VERSION, MIRROR_TTL_MS, buildRecord, validateRecord } from "../erpnext_enhancements/public/js/chat/handoff.js";
import { OfflineQueue, OutboxStore, STATE, mergeForRender } from "../erpnext_enhancements/public/js/chat/optimistic.js";
import { ReadBatcher, TypingRegistry, TypingThrottle, hasRead, readersOf, typingLabel, unionPresence } from "../erpnext_enhancements/public/js/chat/signals.js";
import { findTrigger, insertMention, reanchor, tokenizeMentions, toPayload } from "../erpnext_enhancements/public/js/chat/mentions.js";
import { isComposingKey, shouldGroup } from "../erpnext_enhancements/public/js/chat/dom.js";

let failures = 0;
let checks = 0;

function test(name, fn) {
	checks += 1;
	try {
		fn();
		console.log("  ok    " + name);
	} catch (err) {
		failures += 1;
		console.error("  FAIL  " + name + "\n        " + (err && err.message));
	}
}

console.log("chat SPA — pure client logic\n");

// =================================================================== routes

test("every route form in §4.2 round-trips", () => {
	const cases = [
		["/chat", "", { kind: KIND.ROOT }],
		["/chat/room/R1", "", { kind: KIND.ROOM, room: "R1" }],
		["/chat/room/R1", "?message=M1", { kind: KIND.ROOM, room: "R1", message: "M1" }],
		["/chat/room/R1", "?thread=T1", { kind: KIND.ROOM, room: "R1", thread: "T1" }],
		["/chat/room/R1", "?message=M1&thread=T1", { kind: KIND.ROOM, room: "R1", message: "M1", thread: "T1" }],
		["/chat/search", "?q=pump", { kind: KIND.SEARCH, query: "pump" }],
		["/chat/triton", "", { kind: KIND.TRITON }],
		["/chat/triton/conv-789", "", { kind: KIND.TRITON, conversation: "conv-789" }],
	];
	for (const [path, search, expected] of cases) {
		const parsed = parseRoute(path, search);
		for (const key of Object.keys(expected)) {
			assert.equal(parsed[key], expected[key], `${path}${search} -> ${key}`);
		}
		assert.equal(buildRoute(parsed), path + search, `rebuild ${path}${search}`);
	}
});

test("the query-parameter ORDER is fixed at message then thread", () => {
	// Phase 4 compares these URLs for equality when de-duplicating notifications and stores
	// them in Notification Log.link, so two calls describing one target must produce the same
	// bytes — including the same bytes as chat/links.py::build_chat_route.
	assert.equal(
		buildRoute({ room: "R1", thread: "T1", message: "M1" }),
		"/chat/room/R1?message=M1&thread=T1"
	);
});

test("empty components are DROPPED rather than emitted as ?message=", () => {
	assert.equal(buildRoute({ room: "R1", message: "", thread: null }), "/chat/room/R1");
});

test("components are percent-encoded the way Python's quote(safe='') encodes them", () => {
	assert.equal(encodeSegment("a/b"), "a%2Fb");
	assert.equal(encodeSegment("a b"), "a%20b");
	// The five characters encodeURIComponent leaves alone and quote(safe="") does not.
	assert.equal(encodeSegment("!'()*"), "%21%27%28%29%2A");
});

test("the parser tolerates unknown query parameters", () => {
	const parsed = parseRoute("/chat/room/R1", "?utm_source=mail&message=M1&fbclid=x");
	assert.equal(parsed.room, "R1");
	assert.equal(parsed.message, "M1");
});

test("an unrecognised sub-path lands on the root rather than erroring", () => {
	assert.equal(parseRoute("/chat/nonsense/deep", "").kind, KIND.ROOT);
});

// =================================================================== restoration precedence

const HANDOFF = { room: "HANDOFF-ROOM", thread: "T9", draft: "half typed", anchor_message: "M9", anchor_offset_ratio: 0.42 };
const BOOTHINT = { room: "BOOT-ROOM", thread: null };

test("THE URL WINS, ALWAYS — over both the handoff and the server hint", () => {
	const r = resolveRestoration(parseRoute("/chat/room/URL-ROOM", "?message=M1"), HANDOFF, BOOTHINT);
	assert.equal(r.source, "url");
	assert.equal(r.room, "URL-ROOM");
	assert.equal(r.message, "M1");
});

test("with no room in the URL, the session handoff wins over the server hint", () => {
	const r = resolveRestoration(parseRoute("/chat", ""), HANDOFF, BOOTHINT);
	assert.equal(r.source, "handoff");
	assert.equal(r.room, "HANDOFF-ROOM");
	assert.equal(r.draft, "half typed");
	assert.equal(r.anchorRatio, 0.42);
});

test("with neither, the server-side last-open room is used", () => {
	const r = resolveRestoration(parseRoute("/chat", ""), null, BOOTHINT);
	assert.equal(r.source, "boot");
	assert.equal(r.room, "BOOT-ROOM");
});

test("with nothing at all, the room list — which is a destination, not a failure", () => {
	const r = resolveRestoration(parseRoute("/chat", ""), null, null);
	assert.equal(r.source, "empty");
	assert.equal(r.room, null);
});

test("an unreadable room falls to the NEXT TIER rather than erroring", () => {
	const canRead = (room) => room !== "URL-ROOM";
	const r = resolveRestoration(parseRoute("/chat/room/URL-ROOM", ""), HANDOFF, BOOTHINT, canRead);
	assert.equal(r.source, "handoff", "removed from the linked room -> fall back, do not blank");
});

test("degrade() steps down one tier at a time: thread, then room, then the list", () => {
	const start = { source: "url", room: "R1", thread: "T1", message: "M1" };
	const noThread = degrade(start);
	assert.equal(noThread.room, "R1");
	assert.equal(noThread.thread, null);
	const noRoom = degrade(noThread);
	assert.equal(noRoom.room, null);
	assert.equal(degrade(noRoom).source, "empty");
});

// =================================================================== handoff

test("a handoff record with a matching nonce and inside the TTL is honoured", () => {
	const record = buildRecord({ room: "R1", draft: "x" }, 1000, "abc");
	assert.equal(validateRecord(record, { nowMs: 1000 + MIRROR_TTL_MS - 1, requireFreshWithin: MIRROR_TTL_MS, expectNonce: "abc" }).room, "R1");
});

test("A STALE localStorage MIRROR IS IGNORED — a week-old record must not hijack /chat", () => {
	const record = buildRecord({ room: "R1" }, 1000, "abc");
	assert.equal(validateRecord(record, { nowMs: 1000 + MIRROR_TTL_MS + 1, requireFreshWithin: MIRROR_TTL_MS }), null);
});

test("a nonce mismatch is ignored", () => {
	const record = buildRecord({ room: "R1" }, 1000, "abc");
	assert.equal(validateRecord(record, { nowMs: 1000, expectNonce: "different" }), null);
});

test("a record from a future schema version is ignored rather than half-read", () => {
	const record = { ...buildRecord({ room: "R1" }, 1000, "abc"), v: HANDOFF_VERSION + 1 };
	assert.equal(validateRecord(record, { nowMs: 1000 }), null);
});

test("the scroll anchor is a MESSAGE plus a ratio, never a pixel offset", () => {
	const record = buildRecord({ room: "R1", anchorMessage: "M9", anchorRatio: 0.42 }, 1, "n");
	assert.equal(record.anchor_message, "M9");
	assert.equal(record.anchor_offset_ratio, 0.42);
	assert.equal(buildRecord({ room: "R1", anchorRatio: 7 }, 1, "n").anchor_offset_ratio, 1, "clamped");
});

// =================================================================== optimistic send

function sentRow(overrides) {
	return {
		name: "MSG-1",
		room: "R1",
		seq: 5,
		text: "hello",
		client_message_id: "spa-1",
		sync_state: "Pending",
		creation: "2026-08-10 10:00:00",
		...overrides,
	};
}

test("ordering A — the response arrives first: exactly one bubble", () => {
	const store = new OutboxStore();
	store.enqueue({ clientMessageId: "spa-1", room: "R1", text: "hello", sender: "a", now: 0 });
	assert.equal(store.reconcile(sentRow(), 1).action, "reconciled");
	assert.equal(store.reconcile(sentRow(), 2).action, "ignored", "the realtime echo must not append");
	assert.equal(store.pending().length, 0);
});

test("ordering B — the realtime event arrives first: exactly one bubble", () => {
	const store = new OutboxStore();
	store.enqueue({ clientMessageId: "spa-1", room: "R1", text: "hello", sender: "a", now: 0 });
	assert.equal(store.reconcile(sentRow(), 1).action, "reconciled");
	assert.equal(store.reconcile(sentRow(), 2).action, "ignored");
});

test("ordering C — a message from somebody else is APPENDED, once", () => {
	const store = new OutboxStore();
	const other = sentRow({ name: "MSG-2", client_message_id: "spa-other" });
	assert.equal(store.reconcile(other, 1).action, "appended");
	assert.equal(store.reconcile(other, 2).action, "ignored", "a redelivered event is not a second message");
});

test("ordering D — RETRY REUSES THE ID, and a duplicate insert reconciles as success", () => {
	const store = new OutboxStore();
	store.enqueue({ clientMessageId: "spa-1", room: "R1", text: "hello", sender: "a", now: 0 });
	store.fail("spa-1", new Error("network"));
	const retried = store.retry("spa-1", 100);
	assert.equal(retried.client_message_id, "spa-1", "a fresh id would insert a real duplicate");
	assert.equal(retried.state, STATE.PENDING);
	assert.equal(store.reconcile(sentRow({ deduplicated: true }), 101).action, "reconciled");
});

test("a timeout moves to failed but KEEPS the id, so a late event still reconciles", () => {
	const store = new OutboxStore();
	store.enqueue({ clientMessageId: "spa-1", room: "R1", text: "hi", sender: "a", now: 0 });
	const timedOut = store.sweepTimeouts(20000);
	assert.equal(timedOut.length, 1);
	assert.equal(timedOut[0].state, STATE.FAILED);
	assert.equal(store.reconcile(sentRow(), 20001).action, "reconciled", "the entry must still be keyed");
});

test("ordering is by the SERVER's seq, with pending entries last", () => {
	const merged = mergeForRender(
		[{ name: "b", seq: 2 }, { name: "a", seq: 1 }],
		[{ name: null, seq: null, optimistic: true, sentAt: 5 }]
	);
	assert.deepEqual(merged.map((m) => m.seq), [1, 2, null]);
});

test("the offline queue is capped and says so rather than dropping silently", () => {
	const queue = new OfflineQueue(2);
	assert.equal(queue.push({ a: 1 }).accepted, true);
	assert.equal(queue.push({ a: 2 }).accepted, true);
	assert.equal(queue.push({ a: 3 }).accepted, false);
	assert.deepEqual(queue.drain().map((i) => i.a), [1, 2], "FIFO: messages arrive as typed");
});

// =================================================================== read receipts

test("the batcher NEVER emits a regression", () => {
	const b = new ReadBatcher({ dwellMs: 0, flushMs: 0 });
	b.observe(10, true, 0);
	assert.equal(b.take(0), 10);
	b.observe(4, true, 1);
	assert.equal(b.take(1), 0, "a lower seq from a slow tab must be a no-op, not an un-read");
});

test("the batcher coalesces to at most one call per 2s, and force flushes immediately", () => {
	const b = new ReadBatcher({ dwellMs: 0 });
	b.observe(1, true, 0);
	assert.equal(b.shouldFlush(0, false), true);
	b.take(0);
	b.observe(2, true, 500);
	assert.equal(b.shouldFlush(500, false), false, "inside the 2s window");
	assert.equal(b.shouldFlush(500, true), true, "room switch / blur / hide / pagehide");
	assert.equal(b.shouldFlush(2100, false), true);
});

test("the DWELL means a fast scroll past 200 messages marks none of them read", () => {
	const b = new ReadBatcher({ dwellMs: 400 });
	for (let seq = 1; seq <= 200; seq++) b.observe(seq, true, seq); // 1ms apart
	assert.equal(b.take(1000), 0, "nothing held for 400ms, so nothing is read");
});

test("conditions that stop holding RESET the dwell rather than accumulating", () => {
	const b = new ReadBatcher({ dwellMs: 400 });
	b.observe(1, true, 0);
	b.observe(1, false, 200); // scrolled away
	b.observe(1, true, 300); // scrolled back
	assert.equal(b.observe(1, true, 600), false, "300ms since it came back, not 600 total");
	assert.equal(b.observe(1, true, 701), true);
});

test("acknowledge takes the MAX, so another tab getting there first is not undone", () => {
	const b = new ReadBatcher({ dwellMs: 0 });
	b.observe(3, true, 0);
	b.take(0);
	b.acknowledge(9);
	b.observe(5, true, 5000);
	assert.equal(b.take(5000), 0, "5 is below the server's 9 and must not be sent");
});

test("read state is DERIVED at the boundary: seq == last_read_seq counts as read", () => {
	assert.equal(hasRead(5, 5), true, "off by one here shows as 'the last message never reads'");
	assert.equal(hasRead(6, 5), false);
	assert.deepEqual(
		readersOf(5, [{ user: "a", last_read_seq: 5 }, { user: "b", last_read_seq: 4 }, { user: "me", last_read_seq: 9 }], { exclude: "me" }),
		["a"]
	);
});

// =================================================================== typing

test("typing emits at most once per 3s AND only when the text changed", () => {
	const t = new TypingThrottle(3000);
	assert.equal(t.onInput("h", 0), "typing");
	assert.equal(t.onInput("he", 100), null, "inside the window");
	assert.equal(t.onInput("he", 4000), null, "unchanged text is not typing");
	assert.equal(t.onInput("hel", 4000), "typing");
});

test("clearing the composer sends the stop event", () => {
	const t = new TypingThrottle(3000);
	t.onInput("hi", 0);
	assert.equal(t.onInput("", 10), "stopped");
});

test("a receiver expires an indicator after 5s without a refresh", () => {
	const r = new TypingRegistry(5000);
	r.start("R1", null, "jane", 0);
	assert.deepEqual(r.active("R1", null, 4000), ["jane"]);
	assert.deepEqual(r.active("R1", null, 5001), [], "the safety net for a closed laptop");
});

test("typing is scoped per thread, so a thread reply does not show in the transcript", () => {
	const r = new TypingRegistry(5000);
	r.start("R1", "T1", "jane", 0);
	assert.deepEqual(r.active("R1", null, 100), []);
	assert.deepEqual(r.active("R1", "T1", 100), ["jane"]);
});

test("the typing label is never an unbounded list", () => {
	assert.equal(typingLabel(["Jane"]), "Jane is typing…");
	assert.equal(typingLabel(["Jane", "Sam"]), "Jane and Sam are typing…");
	assert.equal(typingLabel(["Jane", "Sam", "Ali"]), "3 people are typing…");
});

// =================================================================== presence union

test("THREE CLIENTS, ONE FOCUSED ON R — the user counts as focused on R", () => {
	const clients = [
		{ ts: 100, room: "R", focused: 1, visibility: "visible" },
		{ ts: 100, room: "OTHER", focused: 0, visibility: "visible" },
		{ ts: 100, room: null, focused: 0, visibility: "hidden" },
	];
	const union = unionPresence(clients, 110, 75, "R");
	assert.equal(union.focusedHere, true);
	assert.equal(union.state, "online");
	assert.equal(union.clients, 3);
});

test("that client's key expiring means the user is no longer focused there", () => {
	const clients = [
		{ ts: 0, room: "R", focused: 1, visibility: "visible" },
		{ ts: 100, room: "OTHER", focused: 1, visibility: "visible" },
	];
	assert.equal(unionPresence(clients, 110, 75, "R").focusedHere, false);
});

test("all keys expiring is offline — with NO cleanup code path", () => {
	const clients = [{ ts: 0, room: "R", focused: 1, visibility: "visible" }];
	assert.deepEqual(unionPresence(clients, 200, 75, "R"), { state: "offline", focusedHere: false, clients: 0 });
});

test("room open but window BLURRED is not focused — a tab behind a spreadsheet", () => {
	const clients = [{ ts: 100, room: "R", focused: 0, visibility: "visible" }];
	assert.equal(unionPresence(clients, 110, 75, "R").focusedHere, false);
});

// =================================================================== mentions

test("@ triggers only at a word boundary, never mid-word", () => {
	assert.equal(findTrigger("hello @ja", 9).query, "ja");
	assert.equal(findTrigger("@ja", 3).query, "ja");
	assert.equal(findTrigger("nik@sapphirefountains.com", 25), null, "an email is not a mention");
	assert.equal(findTrigger("hello @jane doe", 15), null, "a space closes the trigger");
});

test("insertion produces the DISPLAY NAME, which is what degrades in the native Chat client", () => {
	const trigger = findTrigger("hi @ja", 6);
	const result = insertMention("hi @ja", trigger, { kind: "user", user: "jane@x.com", label: "Jane Doe" });
	assert.equal(result.text, "hi @Jane Doe ");
	assert.equal(result.mention.length, "@Jane Doe".length, "the span excludes the trailing space");
	assert.equal(result.caret, result.text.length);
});

test("@triton is a Triton mention with no user link", () => {
	const trigger = findTrigger("@tri", 4);
	const result = insertMention("@tri", trigger, { kind: "triton", label: "Triton" });
	assert.equal(result.mention.kind, "triton");
	assert.deepEqual(toPayload([result.mention])[0], {
		mention_type: "Triton",
		user: null,
		start_index: 0,
		length: 7,
	});
});

test("typing IN FRONT of a mention re-anchors it rather than shifting the chip", () => {
	const mention = { kind: "user", user: "jane@x.com", label: "@Jane Doe", start: 3, length: 9 };
	const moved = reanchor("okay hi @Jane Doe ", [mention]);
	assert.equal(moved.length, 1);
	assert.equal(moved[0].start, 8, "found again at its new offset");
});

test("a mention whose text the user EDITED AWAY is dropped, not clamped", () => {
	const mention = { kind: "user", user: "jane@x.com", label: "@Jane Doe", start: 3, length: 9 };
	assert.deepEqual(reanchor("hi there", [mention]), []);
});

test("tokenizeMentions drops out-of-range and overlapping spans rather than trusting them", () => {
	const text = "hi @Jane Doe";
	assert.deepEqual(
		tokenizeMentions(text, [{ start_index: 3, length: 9, user: "jane@x.com", mention_type: "User" }]).map((t) => t.type),
		["text", "mention"]
	);
	assert.deepEqual(
		tokenizeMentions(text, [{ start_index: 3, length: 900, user: "jane@x.com" }]),
		[{ type: "text", value: text }],
		"an offset off the wire that runs past the body is a lie, not a clamp"
	);
	assert.equal(
		tokenizeMentions(text, [
			{ start_index: 3, length: 9, user: "a" },
			{ start_index: 5, length: 4, user: "b" },
		]).filter((t) => t.type === "mention").length,
		1,
		"overlapping: keep the first"
	);
});

// =================================================================== IME composition

test("isComposingKey catches BOTH the modern signal and the legacy 229", () => {
	// Two checks, not one. `isComposing` is the modern signal, but older WebKit and several
	// Android IMEs leave it unset on the keydown that ENDS composition and report
	// keyCode 229 instead — which is precisely the keystroke the guard exists for, so
	// checking only isComposing fixes the bug everywhere except where it bites.
	assert.equal(isComposingKey({ isComposing: true, key: "Enter" }), true);
	assert.equal(isComposingKey({ isComposing: false, keyCode: 229, key: "Enter" }), true);
	assert.equal(isComposingKey({ keyCode: 229 }), true);
});

test("isComposingKey does NOT swallow an ordinary keystroke", () => {
	// The composers return early on true, so a false positive here would break Enter-to-send
	// for every user who is not using an IME — i.e. it would trade one silent bug for a loud
	// one affecting everybody.
	assert.equal(isComposingKey({ isComposing: false, key: "Enter", keyCode: 13 }), false);
	assert.equal(isComposingKey({ key: "Enter" }), false);
	assert.equal(isComposingKey({ key: "a", keyCode: 65 }), false);
	assert.equal(isComposingKey(null), false, "a missing event must not read as composing");
	assert.equal(isComposingKey(undefined), false);
});

test("message grouping never groups across senders, or a tombstone", () => {
	const base = { sender: "a@x", sender_kind: "Human", creation: "2026-08-10 10:00:00" };
	const soon = { ...base, creation: "2026-08-10 10:01:00" };
	assert.equal(shouldGroup(base, soon), true);
	assert.equal(shouldGroup(base, { ...soon, sender: "b@x" }), false);
	assert.equal(shouldGroup(base, { ...soon, sender_kind: "Triton" }), false);
	assert.equal(shouldGroup(base, { ...soon, is_deleted: 1 }), false, "a tombstone stands alone");
	assert.equal(shouldGroup(base, { ...base, creation: "2026-08-10 10:30:00" }), false, "outside the window");
});

console.log("");
if (failures) {
	console.error(`${failures} of ${checks} assertions failed`);
	process.exit(1);
}
if (checks < 35) {
	console.error(
		`MARKERS NOT FOUND: only ${checks} checks ran, well below the ~40 this file defines. The ` +
			"imports have stopped resolving and this suite is now green while testing nothing."
	);
	process.exit(2);
}
console.log(`chat SPA pure client logic: ${checks} assertions passed`);
