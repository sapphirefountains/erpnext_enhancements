#!/usr/bin/env node
/**
 * The inline-citation renderer's whole degradation table, as executable rows.
 *
 * §4.10 of the Phase 3 brief lists these failure modes and requires each to have a passing
 * test, and the reason is that every one of them is INVISIBLE in production: a dropped token
 * looks like the model chose not to cite, a truncated tail looks like the model stopped
 * early, and a split token looks like a rendering glitch nobody can reproduce. None of them
 * throws, so none of them is reported.
 *
 * The row that matters most is the LAST one: a stream with no `citations` event must render
 * exactly as it does today. That is what lets Phase 3 ship before Phase 5 exists — inline
 * links are simply absent and nothing else changes — and it is the row most likely to be
 * quietly broken by an "improvement" to the renderer.
 *
 * ES module (.mjs) because the code under test is, and the repo's other JS guards are plain
 * `node scripts/*.js` with no npm install. This follows that shape: no runner, no framework,
 * exit 2 with a loud message if the markers stop resolving.
 */

import assert from "node:assert/strict";
import {
	CitationStream,
	citationLabel,
	holdbackLength,
	indexManifest,
	isSafeUrl,
	isTokenPrefix,
	orderManifestForDisplay,
	renderTokens,
	tokenizeCitations,
} from "../erpnext_enhancements/public/js/chat/citations.js";

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

const MANIFEST = [
	{ k: 1, type: "erpnext_doc", label: "SO-2026-00184", url: "/app/sales-order/SO-2026-00184", snippet: "the order" },
	{ k: 2, type: "chat_message", label: "Jane, 5 Aug", url: "/chat/room/R1?message=M1" },
	{ k: 3, type: "url", label: "docs", url: "https://example.com/a" },
	{ k: 4, type: "digest", label: "Room summary · #riverwalk" },
	{ k: 5, type: "url", label: "bad", url: "javascript:alert(1)" },
	{ k: 6, type: "erpnext_doc", label: "<script>alert(1)</script>", url: "/app/item/X" },
];
const INDEX = indexManifest(MANIFEST);

console.log("chat citation renderer — the §4.10 degradation table\n");

// --------------------------------------------------------------- well-formed

test("a well-formed token becomes a citation with the manifest's entry", () => {
	const tokens = tokenizeCitations("See [[ref:1]] for the price.", INDEX);
	assert.equal(tokens.length, 3);
	assert.deepEqual(tokens[0], { type: "text", value: "See " });
	assert.equal(tokens[1].type, "citation");
	assert.equal(tokens[1].k, 1);
	assert.equal(tokens[1].citation.url, "/app/sales-order/SO-2026-00184");
	assert.deepEqual(tokens[2], { type: "text", value: " for the price." });
});

test("several citations on one claim all resolve", () => {
	const tokens = tokenizeCitations("Confirmed [[ref:1]][[ref:2]].", INDEX);
	assert.equal(tokens.filter((t) => t.type === "citation").length, 2);
});

// --------------------------------------------------------------- unknown k

test("an id not in the manifest is DROPPED SILENTLY and the prose survives intact", () => {
	const misses = [];
	const tokens = tokenizeCitations("The total [[ref:99]] is fine.", INDEX, (k) => misses.push(k));
	// One coalesced text run: no raw token, no dead link, and no seam where it was.
	assert.deepEqual(tokens, [{ type: "text", value: "The total  is fine." }]);
	assert.deepEqual(misses, [99], "the miss must be reported so a rising rate is visible");
});

// --------------------------------------------------------------- malformed

for (const malformed of ["[[ref:]]", "[ref:7]", "[[ref]]", "[[refs:7]]", "[[ ref : ]]"]) {
	test(`malformed ${JSON.stringify(malformed)} stays literal text`, () => {
		const tokens = tokenizeCitations("a " + malformed + " b", INDEX);
		assert.deepEqual(tokens, [{ type: "text", value: "a " + malformed + " b" }]);
	});
}

test("the tolerant whitespace form IS matched", () => {
	const tokens = tokenizeCitations("x [[ ref : 1 ]] y", INDEX);
	assert.equal(tokens.filter((t) => t.type === "citation").length, 1);
});

// --------------------------------------------------------------- split tokens

test("a token split across chunk boundaries is reassembled", () => {
	const stream = new CitationStream();
	let out = "";
	out += stream.push("See [[re");
	out += stream.push("f:1]] now");
	out += stream.flush();
	assert.equal(out, "See [[ref:1]] now");
	const tokens = tokenizeCitations(out, INDEX);
	assert.equal(tokens.filter((t) => t.type === "citation").length, 1);
});

test("the partial prefix is HELD BACK rather than rendered", () => {
	const stream = new CitationStream();
	const first = stream.push("See [[re");
	assert.equal(first, "See ", "'[[re' must not reach the DOM as literal text");
});

test("THE TAIL BUFFER IS FLUSHED ON STREAM END", () => {
	// Forgetting this truncates the last few characters of every answer ending near a token.
	const stream = new CitationStream();
	assert.equal(stream.push("done at last["), "done at last");
	assert.equal(stream.flush(), "[", "the held character must be released, not dropped");
});

test("an answer ending in a COMPLETE token is not held forever", () => {
	const stream = new CitationStream();
	assert.equal(stream.push("cited [[ref:1]]"), "cited [[ref:1]]");
	assert.equal(stream.flush(), "");
});

test("holdbackLength holds exactly the prefix and nothing before it", () => {
	assert.equal(holdbackLength("abc"), 0);
	assert.equal(holdbackLength("abc["), 1);
	assert.equal(holdbackLength("abc[["), 2);
	assert.equal(holdbackLength("abc[[ref:"), 6);
	assert.equal(holdbackLength("abc[[ref:7"), 7);
	assert.equal(holdbackLength("abc[[ref:7]"), 8);
	assert.equal(holdbackLength("abc[[ref:7]]"), 0);
	assert.equal(holdbackLength("a [x] b"), 0, "a bracket that cannot become a token holds nothing");
});

test("isTokenPrefix rejects a complete token and a malformed one", () => {
	assert.equal(isTokenPrefix("[[ref:7]]"), false);
	assert.equal(isTokenPrefix("[[ref:]]"), false);
	assert.equal(isTokenPrefix("[[ref:"), true);
});

// --------------------------------------------------------------- no manifest

test("NO CITATIONS EVENT AT ALL renders identically to today", () => {
	const source = "Plain answer with no citations, and a [[ref:1]] the backend never declared.";
	const tokens = tokenizeCitations(source, indexManifest([]));
	assert.deepEqual(
		tokens,
		[{ type: "text", value: source }],
		"with an empty manifest NOTHING is rewritten — not even a token that looks well formed. " +
			"This is the row that lets Phase 3 ship before Phase 5."
	);
});

test("citations_append extends the SAME id space", () => {
	const index = indexManifest([{ k: 1, type: "url", url: "https://a" }]);
	for (const [k, entry] of indexManifest([{ k: 2, type: "url", url: "https://b" }])) index.set(k, entry);
	const tokens = tokenizeCitations("[[ref:1]] and [[ref:2]]", index);
	assert.equal(tokens.filter((t) => t.type === "citation").length, 2);
});

// --------------------------------------------------------------- rendering + XSS

test("a javascript: url is refused and the citation degrades to a flat pill", () => {
	assert.equal(isSafeUrl("javascript:alert(1)"), false);
	assert.equal(isSafeUrl("data:text/html,x"), false);
	assert.equal(isSafeUrl("/app/item/X"), true);
	assert.equal(isSafeUrl("https://example.com"), true);
	assert.equal(isSafeUrl("//evil.example"), false, "protocol-relative is not same-origin");
});

test("a label containing markup is text, never HTML", () => {
	// The label is user-authored: a document title, a filename, a coworker's message. The
	// renderer builds nodes, so the only assertion that matters is that the raw string comes
	// back out unchanged rather than being parsed.
	assert.equal(citationLabel(MANIFEST[5]), "<script>alert(1)</script>");
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	renderTokens(parent, tokenizeCitations("x [[ref:6]]", INDEX), {});
	const node = parent.children.find((c) => c.tag === "a");
	assert.equal(node.attrs["aria-label"], "<script>alert(1)</script>");
	assert.equal(node.textContent, "[6]", "the visible text is the id, not the label");
	assert.equal(node.innerHTMLCalls, 0, "renderTokens must never touch innerHTML");
});

test("a digest renders as a NON-NAVIGATING pill", () => {
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	renderTokens(parent, tokenizeCitations("[[ref:4]]", INDEX), {});
	const node = parent.children[0];
	assert.equal(node.tag, "span", "a digest has no canonical target and must not be a link");
	assert.ok(String(node.className).includes("is-flat"));
});

test("an unsafe url in the manifest still renders as a pill, not a link", () => {
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	renderTokens(parent, tokenizeCitations("[[ref:5]]", INDEX), {});
	assert.equal(parent.children[0].tag, "span");
});

test("an external link carries rel=noopener noreferrer and target=_blank", () => {
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	renderTokens(parent, tokenizeCitations("[[ref:3]]", INDEX), {});
	const node = parent.children[0];
	assert.equal(node.tag, "a");
	assert.equal(node.attrs.rel, "noopener noreferrer");
	assert.equal(node.attrs.target, "_blank");
});

test("a same-origin desk link does NOT open a new tab", () => {
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	renderTokens(parent, tokenizeCitations("[[ref:1]]", INDEX), {});
	assert.equal(parent.children[0].attrs.target, undefined);
});

// --------------------------------------------------- the approved sources ordering

test("the sources row puts CITED entries first, each group in id order", () => {
	// Approved 2026-08-10 against decision #7's "preserve exactly". The ordering must be by
	// id within each group, not by relevance: the inline markers in the answer read [1], [2],
	// [3], and a row sorted any other way makes the reader hunt for the chip matching the
	// number they just clicked past.
	const ordered = orderManifestForDisplay(INDEX, new Set([3, 1]));
	assert.deepEqual(ordered.map((e) => e.k), [1, 3, 2, 4, 5, 6]);
	assert.deepEqual(ordered.map((e) => e.cited), [true, true, false, false, false, false]);
});

test("EVERY manifest entry survives the ordering — the row is the full retrieved set", () => {
	// This is the half of decision #7 the approval did NOT change. Showing only what the
	// model cited would hide retrieved-but-unused sources, which is exactly the information
	// somebody checking the answer wants.
	assert.equal(orderManifestForDisplay(INDEX, new Set()).length, INDEX.size);
	assert.equal(orderManifestForDisplay(INDEX, new Set([1, 2, 3, 4, 5, 6])).length, INDEX.size);
});

test("with no citations at all the row is the manifest in plain id order", () => {
	const ordered = orderManifestForDisplay(INDEX, new Set());
	assert.deepEqual(ordered.map((e) => e.k), [1, 2, 3, 4, 5, 6]);
	assert.ok(ordered.every((e) => e.cited === false));
});

test("an empty manifest orders to nothing, so the caller falls back to today's row", () => {
	assert.deepEqual(orderManifestForDisplay([], new Set()), []);
	assert.deepEqual(orderManifestForDisplay(new Map(), new Set([1])), []);
});

test("sortCitedFirst:false keeps ID ORDER while still marking accurately", () => {
	// The streaming case. `cited` grows with every frame, so sorting on it mid-answer slides
	// chips out from under a reader who is about to click one — which `citations_append` did,
	// once per append, contradicting the "one reorder, at stream end" rule the code documents.
	// The marks must stay accurate throughout; only the ORDER waits.
	const ordered = orderManifestForDisplay(INDEX, new Set([3, 1]), { sortCitedFirst: false });
	assert.deepEqual(ordered.map((e) => e.k), [1, 2, 3, 4, 5, 6], "id order, not cited-first");
	assert.deepEqual(
		ordered.filter((e) => e.cited).map((e) => e.k),
		[1, 3],
		"the marks are still correct — only the sort is deferred"
	);
});

test("sortCitedFirst defaults to true, so the end-of-turn call needs no argument", () => {
	assert.deepEqual(orderManifestForDisplay(INDEX, new Set([3])).map((e) => e.k), [3, 1, 2, 4, 5, 6]);
	assert.deepEqual(
		orderManifestForDisplay(INDEX, new Set([3]), {}).map((e) => e.k),
		[3, 1, 2, 4, 5, 6]
	);
});

test("it accepts an array manifest and an array of cited ids, not just Map/Set", () => {
	const ordered = orderManifestForDisplay(MANIFEST, [2]);
	assert.equal(ordered[0].k, 2);
	assert.equal(ordered[0].cited, true);
});

test("onCite reports each RESOLVED id and never a miss", () => {
	const cited = [];
	const missed = [];
	const doc = fakeDocument();
	const parent = doc.createElement("div");
	const tokens = tokenizeCitations("a [[ref:1]] b [[ref:99]] c [[ref:3]]", INDEX, (k) => missed.push(k));
	renderTokens(parent, tokens, { onCite: (k) => cited.push(k) });
	assert.deepEqual(cited, [1, 3], "a dropped token must not mark a chip");
	assert.deepEqual(missed, [99]);
});

test("onCite fires again on a re-render, which is why the caller accumulates into a Set", () => {
	// `renderBubble` rebuilds the bubble from scratch on every streaming frame, so the same
	// token is rendered many times. A counter here would be wrong; a Set is right.
	const seen = new Set();
	const doc = fakeDocument();
	for (let frame = 0; frame < 3; frame++) {
		const parent = doc.createElement("div");
		renderTokens(parent, tokenizeCitations("[[ref:1]]", INDEX), { onCite: (k) => seen.add(k) });
	}
	assert.deepEqual([...seen], [1]);
});

/**
 * The smallest possible DOM stand-in. Deliberately not jsdom: the repo's JS guards run with
 * `node` and no npm install, and that constraint is what keeps them running at all.
 *
 * It records `innerHTML` assignment so the "no innerHTML string concatenation" rule is
 * ASSERTED rather than reviewed.
 */
function fakeDocument() {
	const make = (tag) => {
		const node = {
			tag,
			children: [],
			attrs: {},
			dataset: {},
			className: "",
			textContent: "",
			innerHTMLCalls: 0,
			ownerDocument: null,
			setAttribute(k, v) {
				this.attrs[k] = v;
			},
			appendChild(child) {
				this.children.push(child);
				return child;
			},
			addEventListener() {},
			set innerHTML(_v) {
				this.innerHTMLCalls += 1;
			},
			get innerHTML() {
				return "";
			},
		};
		return node;
	};
	const doc = {
		createElement: (tag) => {
			const node = make(tag);
			node.ownerDocument = doc;
			return node;
		},
		createTextNode: (value) => ({ tag: "#text", textContent: value, ownerDocument: doc }),
		createDocumentFragment: () => {
			const node = make("#fragment");
			node.ownerDocument = doc;
			return node;
		},
	};
	return doc;
}

console.log("");
if (failures) {
	console.error(`${failures} of ${checks} assertions failed`);
	process.exit(1);
}
if (checks < 20) {
	console.error(
		`MARKERS NOT FOUND: only ${checks} checks ran. This suite is meant to cover every row of ` +
			"the §4.10 degradation table; a collapse to nothing means the imports stopped resolving " +
			"and it is now green while testing nothing."
	);
	process.exit(2);
}
console.log(`chat citation renderer: ${checks} assertions passed`);
