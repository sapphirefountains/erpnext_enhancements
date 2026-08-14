#!/usr/bin/env node
/**
 * The shared markdown renderer: what it renders, and what it refuses to.
 *
 * Two halves, and the second is the one that matters.
 *
 * **What it renders** is the ordinary case — bold, italic, code, lists, links, headings — and
 * the reason it is tested exhaustively is that all three chat surfaces rendered Triton's
 * answers differently before this module existed, and every one of those was invisible to the
 * person reading it: raw `**bold**` looks like the model typed asterisks, not like a renderer
 * is missing.
 *
 * **What it refuses** is the half that keeps it safe. The input is model output, which is
 * untrusted twice over: it carries prompt-injected content, and it quotes employees' own
 * messages back verbatim. The renderer builds nodes rather than HTML strings, so the assertion
 * that matters is not "the output is escaped" — it is **"no element was ever created that the
 * renderer does not itself construct"**, plus the §4.G.7 hostile corpus through every link path.
 *
 * ES module (.mjs) because the code under test is, and the repo's other JS guards are plain
 * `node scripts/*.js` with no npm install.
 */

import assert from "node:assert/strict";

import { appendInline, renderBlocks, renderMarkdown } from "../erpnext_enhancements/public/js/chat/markdown.js";

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

// A DOM small enough to reason about, and loud about the one thing that must never happen.
function fakeDocument() {
	const make = (tag) => ({
		tag,
		children: [],
		attrs: {},
		className: "",
		textContent: "",
		innerHTMLCalls: 0,
		firstChild: null,
		lastChild: null,
		setAttribute(k, v) {
			this.attrs[k] = v;
		},
		appendChild(child) {
			this.children.push(child);
			this.lastChild = child;
			this.firstChild = this.children[0];
			return child;
		},
		removeChild(child) {
			this.children = this.children.filter((c) => c !== child);
			this.firstChild = this.children[0] || null;
			this.lastChild = this.children[this.children.length - 1] || null;
			return child;
		},
		set innerHTML(_v) {
			this.innerHTMLCalls += 1;
		},
		get innerHTML() {
			return "";
		},
	});
	return {
		createElement: (tag) => make(tag),
		createTextNode: (value) => ({ tag: "#text", textContent: value, children: [] }),
	};
}

/** Every tag in the rendered tree, depth-first. */
function tags(nodes) {
	const out = [];
	const walk = (n) => {
		out.push(n.tag);
		for (const c of n.children || []) walk(c);
	};
	for (const n of nodes) walk(n);
	return out;
}

/** The visible text, as a reader would see it. */
function flatText(nodes) {
	let out = "";
	const walk = (n) => {
		if (n.tag === "#text") out += n.textContent;
		else if (n.tag === "br") out += "\n";
		else {
			if (n.textContent && !(n.children || []).length) out += n.textContent;
			for (const c of n.children || []) walk(c);
		}
	};
	for (const n of nodes) walk(n);
	return out;
}

function render(source) {
	return renderBlocks(fakeDocument(), source);
}

function inline(source) {
	const doc = fakeDocument();
	return appendInline(doc, doc.createElement("div"), source);
}

console.log("chat markdown renderer\n");

// --------------------------------------------------------------- the reported bug

test("THE REPORTED BUG: **bold** is bold, not asterisks", () => {
	const out = render("**Incompressibility:** water is incompressible.");
	assert.ok(tags(out).includes("strong"), "no <strong> — this is the whole complaint");
	assert.ok(!flatText(out).includes("*"), "an asterisk survived into the visible text");
	assert.ok(flatText(out).includes("Incompressibility:"));
});

test("THE REPORTED BUG: a bullet list is a list, not literal dashes", () => {
	const out = render("- one\n- two\n- three");
	assert.equal(out.length, 1);
	assert.equal(out[0].tag, "ul");
	assert.equal(out[0].children.length, 3);
	assert.ok(!flatText(out).includes("-"), "a literal dash survived");
});

test("a bold run inside a list item renders as both", () => {
	const out = render("- **Newtonian Behavior:** viscosity is constant");
	assert.deepEqual(tags(out).slice(0, 3), ["ul", "li", "strong"]);
	assert.ok(flatText(out).startsWith("Newtonian Behavior:"));
});

// --------------------------------------------------------------- inline constructs

test("bold is tried before italic, so ** never renders as * plus a stray", () => {
	const out = inline("**bold** and *italic*");
	assert.deepEqual(
		out.children.filter((c) => c.tag !== "#text").map((c) => c.tag),
		["strong", "em"],
	);
	assert.ok(!flatText([out]).includes("*"), "an asterisk leaked into the text");
});

test("__bold__ and _italic_ are the underscore spellings of the same two", () => {
	assert.equal(inline("__x__").children[0].tag, "strong");
	assert.equal(inline("_x_").children[0].tag, "em");
});

test("inline code is literal — markdown inside it is NOT parsed", () => {
	const out = inline("use `**not bold**` here");
	const code = out.children.find((c) => c.tag === "code");
	assert.ok(code, "no <code> element");
	assert.equal(code.textContent, "**not bold**", "the renderer recursed into code content");
	assert.equal(code.children.length, 0, "code must have no child elements");
});

test("a fenced block is literal, and an unterminated one runs to the end", () => {
	const closed = render("text\n\n```python\nx = **1**\n```\n\nmore");
	const pre = closed.find((n) => n.tag === "pre");
	assert.ok(pre, "no <pre>");
	assert.equal(pre.children[0].textContent, "x = **1**");
	assert.equal(pre.children[0].attrs === undefined ? "" : pre.children[0].className, "language-python");

	const open = render("```\nnever closed\nstill code");
	assert.ok(open.some((n) => n.tag === "pre"), "an unterminated fence must not throw or vanish");
});

test("unmatched delimiters stay literal rather than eating the rest of the line", () => {
	assert.equal(flatText([inline("2 * 3 = 6")]), "2 * 3 = 6");
	assert.equal(flatText([inline("a ** b")]), "a ** b");
	assert.equal(flatText([inline("snake_case_name")]), "snake_case_name");
});

test("an identifier is not italic, but a real underscore emphasis beside one still is", () => {
	// CommonMark's intraword rule, and the reason for it: Triton emits identifiers constantly
	// — field names, DocType scrub names, Python functions — and `retrieve_for_oversight`
	// rendering as "retrievefor oversight" in italics is worse than no markdown at all.
	assert.equal(flatText([inline("call retrieve_for_oversight() first")]), "call retrieve_for_oversight() first");
	const mixed = inline("a_b and _real_ italic");
	assert.equal(mixed.children.filter((c) => c.tag === "em").length, 1, "the genuine emphasis was skipped too");
	assert.ok(flatText([mixed]).startsWith("a_b and "), "the identifier lost its underscore");
});

test("headings render as headings, capped so an h1 never lands in a bubble", () => {
	const out = render("# Top\n\n### Third");
	assert.equal(out[0].tag, "h3", "# should not become <h1> inside a chat bubble");
	assert.ok(out[0].className.includes("ee-md-h1"), "the level is kept as a class");
	assert.equal(flatText([out[0]]), "Top");
});

test("a numbered list is an <ol>, and single newlines inside a paragraph become <br>", () => {
	assert.equal(render("1. first\n2. second")[0].tag, "ol");
	assert.ok(tags(render("line one\nline two")).includes("br"));
});

// --------------------------------------------------------------- links

test("a markdown link becomes an anchor with the label as its text", () => {
	const a = inline("see [the docs](https://example.com/x)").children.find((c) => c.tag === "a");
	assert.ok(a, "no anchor");
	assert.equal(a.textContent, "the docs");
	assert.equal(a.attrs.href, "https://example.com/x");
	assert.equal(a.attrs.rel, "noopener noreferrer");
	assert.equal(a.attrs.target, "_blank");
});

test("a bare URL is linkified", () => {
	const a = inline("go to https://example.com now").children.find((c) => c.tag === "a");
	assert.ok(a);
	assert.equal(a.attrs.href, "https://example.com");
});

test("a same-origin link does NOT open a new tab", () => {
	const a = inline("[SO-1](/app/sales-order/SO-1)").children.find((c) => c.tag === "a");
	assert.ok(a);
	assert.equal(a.attrs.target, undefined, "an internal link opening a tab is a papercut");
});

// --------------------------------------------------------------- what it refuses

test("§4.G.7 corpus: an unsafe link scheme renders as INERT TEXT, never an anchor", () => {
	for (const href of [
		"javascript:alert(1)",
		"JaVaScRiPt:alert(1)",
		"data:text/html;base64,PHNjcmlwdD4=",
		"vbscript:msgbox(1)",
		"//evil.example/",
		"/" + String.fromCharCode(92) + "evil.example",
		"https://evil.example.com@sapphirefountains.com/",
	]) {
		const out = inline(`[click me](${href})`);
		const anchors = out.children.filter((c) => c.tag === "a");
		assert.equal(anchors.length, 0, `rendered an anchor for ${href}`);
		assert.ok(
			flatText([out]).includes("click me"),
			"the label must still be visible as text — refusing the link is not refusing the words",
		);
	}
});

test("raw HTML in the source is TEXT, because the renderer only ever builds known tags", () => {
	for (const hostile of [
		"<script>alert(1)</script>",
		"<img src=x onerror=alert(1)>",
		'"><script>alert(1)</script>',
		"<svg/onload=alert(1)>",
		"</a><script>alert(1)</script>",
	]) {
		const out = render(hostile);
		const rendered = tags(out);
		for (const tag of rendered) {
			assert.ok(
				["p", "#text", "strong", "em", "code", "s", "a", "span", "br", "ul", "ol", "li", "pre"].includes(tag),
				`the renderer created a <${tag}> from ${hostile}`,
			);
		}
		assert.ok(flatText(out).includes("script") || flatText(out).includes("svg") || flatText(out).includes("img"));
	}
});

test("the renderer NEVER assigns innerHTML", () => {
	const doc = fakeDocument();
	const container = doc.createElement("div");
	renderMarkdown(container, "# H\n\n- **a** `b` [c](https://x)\n\n```\nd\n```", { doc });
	const seen = [];
	const walk = (n) => {
		seen.push(n.innerHTMLCalls || 0);
		for (const c of n.children || []) walk(c);
	};
	walk(container);
	assert.equal(
		seen.reduce((a, b) => a + b, 0),
		0,
		"innerHTML was assigned. The input is model output; this module builds nodes only.",
	);
});

test("renderMarkdown empties the container first, so a re-render does not append twice", () => {
	const doc = fakeDocument();
	const container = doc.createElement("div");
	renderMarkdown(container, "one", { doc });
	renderMarkdown(container, "two", { doc });
	assert.equal(flatText([container]), "two");
});

test("empty, null and whitespace input render nothing and do not throw", () => {
	for (const value of ["", null, undefined, "   \n  \n"]) {
		assert.deepEqual(render(value), [], `${JSON.stringify(value)} produced blocks`);
	}
});

console.log("");
if (failures) {
	console.error(`chat markdown renderer: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`chat markdown renderer: ${checks} assertions passed`);
