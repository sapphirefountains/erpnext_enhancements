/**
 * The one markdown renderer, shared by the chat SPA and the floating widget.
 *
 * WHY THIS EXISTS RATHER THAN `frappe.markdown`
 * =============================================
 *
 * Three surfaces rendered Triton's answers three different ways, and all three were wrong:
 *
 * 1. The **SPA** built message bodies entirely with `textContent`, so an answer arrived as
 *    literal `**bold**` and `- item`. No markdown renderer existed on that page at all.
 * 2. The **widget** called `frappe.markdown`, which in v16 is `new showdown.Converter()` with
 *    **no sanitiser** — and which lives in the Desk bundle. On a Desk page it rendered; on
 *    `/chat`, where `frappe`/`showdown`/`jQuery` are absent, it threw and fell back **silently**
 *    to escaped raw text. Same widget, two different outputs, no error either way.
 * 3. **Google Chat** got the raw markdown. Chat's own bold is a *single* asterisk, so
 *    `**Incompressibility:**` arrived bold with a stray `*` glued to it. That transform is
 *    server-side (`chat/gchat/markdown.py`); this module is the browser half.
 *
 * So the fix could not be "call `frappe.markdown` in more places": that spreads an unsanitised
 * `innerHTML` sink to two more surfaces and still does nothing on `/chat`.
 *
 * THE RULE THIS FILE KEEPS
 * ========================
 *
 * **Nodes, never HTML strings.** Every element here is `createElement` and every piece of text
 * is `textContent`. There is no `innerHTML` in this module and there must never be one — the
 * input is model output, which is untrusted twice over: it can carry prompt-injected content,
 * and it quotes employees' own messages back verbatim. `citations.js` states the same rule as
 * its rule 3, and this renderer is downstream of the same threat.
 *
 * A consequence worth stating: **this is a renderer, not a parser.** It handles the constructs
 * Triton actually emits — bold, italic, inline code, fenced code, bullet and numbered lists,
 * links, headings — and anything else falls through as literal text. That is the correct
 * failure mode. An unhandled construct rendering as the characters the model typed is a
 * cosmetic miss; an unhandled construct rendering as markup is a vulnerability.
 *
 * Tables and blockquotes are deliberately **not** supported (settled 2026-08-13). Add them by
 * extending `renderBlocks`, with corpus rows, never by reaching for a general parser.
 */

import { isSafeUrl } from "./citations.js";

/** Fenced code: ```lang\n...\n``` — `lang` captured so it can be shown, never executed. */
const FENCE_RE = /^```([A-Za-z0-9_+-]*)\s*$/;

/** `# ` through `###### `. Deeper hashes are not headings in any dialect worth matching. */
const HEADING_RE = /^(#{1,6})\s+(.*)$/;

/** `- `, `* ` or `+ ` with optional leading indent. The indent is captured for nesting depth. */
const BULLET_RE = /^(\s*)[-*+]\s+(.*)$/;

/** `1. ` / `1) `, same shape. */
const ORDERED_RE = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;

/**
 * Inline constructs, in the order they are tried. **Order is load-bearing**: `**` must be
 * attempted before `*`, or bold renders as an italic wrapping a literal asterisk — which is
 * precisely the bug Google Chat exhibits for the opposite reason.
 *
 * Code is first because its contents are literal: `` `**not bold**` `` must stay asterisks.
 */
const INLINE_RULES = [
	{ kind: "code", re: /`([^`\n]+)`/ },
	{ kind: "link", re: /\[([^\]\n]*)\]\(([^)\s]+)\)/ },
	// Non-greedy, and NOT `[^*]*`: the inner run must allow single asterisks so that
	// `**bold with *inner* italic**` matches the outer pair. Non-greedy stops
	// `**a** and **b**` being read as one span from the first `**` to the last.
	{ kind: "bold", re: /\*\*(\S(?:.*?\S)?)\*\*/ },
	{ kind: "bold", re: /__(\S(?:.*?\S)?)__/, wordBoundary: true },
	{ kind: "italic", re: /\*(\S(?:[^*]*\S)?)\*/ },
	{ kind: "italic", re: /_(\S(?:[^_]*\S)?)_/, wordBoundary: true },
	{ kind: "strike", re: /~~(\S(?:.*?\S)?)~~/ },
];

/**
 * Word characters either side of which an underscore does **not** open emphasis.
 *
 * `snake_case_name` is one identifier, not a word with an italic in the middle — and Triton
 * emits identifiers constantly: field names, DocType scrub names, Python functions. CommonMark
 * draws exactly this distinction, allowing intraword `*` and forbidding intraword `_`, and the
 * reason is this case.
 *
 * Checked with an explicit character test rather than a lookbehind in the pattern: lookbehind
 * only reached Safari in 16.4, and the failure mode of an unsupported group is the regex
 * throwing at parse time — which would take the whole renderer down on that browser, silently,
 * for everybody on it.
 */
const WORD_CHAR = /[A-Za-z0-9]/;

/** Bare URLs, so a pasted link is clickable without markdown syntax around it. */
const BARE_URL_RE = /\bhttps?:\/\/[^\s<>()[\]]+/;

/** The tag each inline kind becomes. `code` is `<code>`; the rest are semantic emphasis. */
const INLINE_TAG = { bold: "strong", italic: "em", strike: "s", code: "code" };

/**
 * Render markdown into `container`, as nodes.
 *
 * @param {Element} container - emptied, then filled. Never assigned `innerHTML`.
 * @param {string} source - the markdown.
 * @param {object} [opts]
 * @param {Document} [opts.doc] - injected for tests, which run under a fake document in plain
 *   `node` with no DOM. Defaults to the real one.
 * @returns {Element} the container, for chaining.
 */
export function renderMarkdown(container, source, opts) {
	const doc = (opts && opts.doc) || (typeof document !== "undefined" ? document : null);
	if (!container || !doc) return container;

	while (container.firstChild) container.removeChild(container.firstChild);
	for (const node of renderBlocks(doc, String(source == null ? "" : source))) {
		container.appendChild(node);
	}
	return container;
}

/**
 * The block pass: split into paragraphs, lists, fenced code and headings.
 *
 * Exported for the test suite, which asserts the block structure directly rather than through
 * a container — a renderer whose only assertion is "the container has children" is a renderer
 * with no test.
 */
export function renderBlocks(doc, source) {
	const lines = String(source == null ? "" : source).replace(/\r\n?/g, "\n").split("\n");
	const out = [];
	let i = 0;

	while (i < lines.length) {
		const line = lines[i];

		const fence = line.match(FENCE_RE);
		if (fence) {
			// Everything up to the closing fence is literal, including markdown syntax. An
			// unterminated fence runs to the end of the message rather than swallowing the
			// document into a parse error — the model does truncate mid-block.
			const body = [];
			i += 1;
			while (i < lines.length && !FENCE_RE.test(lines[i])) {
				body.push(lines[i]);
				i += 1;
			}
			i += 1; // the closing fence, or the end
			const pre = doc.createElement("pre");
			pre.className = "ee-md-code";
			const code = doc.createElement("code");
			if (fence[1]) code.className = `language-${fence[1]}`;
			code.textContent = body.join("\n");
			pre.appendChild(code);
			out.push(pre);
			continue;
		}

		const heading = line.match(HEADING_RE);
		if (heading) {
			// Capped at h4 in the DOM regardless of hash count: these render inside a chat
			// bubble, and an <h1> in a bubble is a layout bug wearing semantics. The level is
			// kept as a class so styling can still distinguish them.
			const level = Math.min(heading[1].length, 6);
			const el = doc.createElement(`h${Math.min(level + 2, 6)}`);
			el.className = `ee-md-h ee-md-h${level}`;
			appendInline(doc, el, heading[2]);
			out.push(el);
			i += 1;
			continue;
		}

		if (BULLET_RE.test(line) || ORDERED_RE.test(line)) {
			const [list, consumed] = renderList(doc, lines, i);
			out.push(list);
			i = consumed;
			continue;
		}

		if (!line.trim()) {
			i += 1;
			continue;
		}

		// A paragraph runs to the next blank line or the next block construct. Single newlines
		// inside it become <br>, because a chat answer's line breaks are meaningful — markdown's
		// "join the lines" rule reads as a bug when the model formatted a short list by hand.
		const para = doc.createElement("p");
		para.className = "ee-md-p";
		let first = true;
		while (
			i < lines.length &&
			lines[i].trim() &&
			!FENCE_RE.test(lines[i]) &&
			!HEADING_RE.test(lines[i]) &&
			!BULLET_RE.test(lines[i]) &&
			!ORDERED_RE.test(lines[i])
		) {
			if (!first) para.appendChild(doc.createElement("br"));
			appendInline(doc, para, lines[i]);
			first = false;
			i += 1;
		}
		out.push(para);
	}

	return out;
}

/**
 * One list, from `start`, returning `[element, indexAfter]`.
 *
 * Nesting is by leading whitespace, and it is deliberately shallow: a deeper item becomes a
 * nested list, and anything past two levels flattens into the second. Chat bubbles are narrow
 * and the model's own indentation is not reliable enough to trust a fourth level of it.
 */
function renderList(doc, lines, start) {
	const ordered = ORDERED_RE.test(lines[start]);
	const root = doc.createElement(ordered ? "ol" : "ul");
	root.className = "ee-md-list";

	let i = start;
	let current = root;
	let currentDepth = 0;

	while (i < lines.length) {
		const bullet = lines[i].match(BULLET_RE);
		const numbered = lines[i].match(ORDERED_RE);
		if (!bullet && !numbered) break;

		const indent = (bullet ? bullet[1] : numbered[1]).replace(/\t/g, "  ").length;
		const text = bullet ? bullet[2] : numbered[3];
		const depth = indent >= 2 ? 1 : 0;

		if (depth > currentDepth) {
			const nested = doc.createElement(numbered ? "ol" : "ul");
			nested.className = "ee-md-list is-nested";
			(current.lastChild || current).appendChild(nested);
			current = nested;
			currentDepth = 1;
		} else if (depth < currentDepth) {
			current = root;
			currentDepth = 0;
		}

		const li = doc.createElement("li");
		li.className = "ee-md-li";
		appendInline(doc, li, text);
		current.appendChild(li);
		i += 1;
	}

	return [root, i];
}

/**
 * The inline pass. Appends text and emphasis nodes for one line into `parent`.
 *
 * Exported because it is the half most worth testing directly: nesting, unmatched delimiters,
 * and the ordering between `**` and `*` all live here.
 */
export function appendInline(doc, parent, text) {
	const source = String(text == null ? "" : text);
	if (!source) return parent;

	// The earliest match across all rules wins, so `a *b* and `c`` splits at the emphasis
	// rather than at whichever rule happens to be listed first. Ties go to the earlier rule,
	// which is what puts `**` ahead of `*` at the same offset.
	let best = null;
	for (const rule of INLINE_RULES) {
		const match = matchRule(rule, source);
		if (match && (best === null || match.index < best.match.index)) {
			best = { rule, match };
		}
	}
	const url = BARE_URL_RE.exec(source);
	if (url && (best === null || url.index < best.match.index)) {
		best = { rule: { kind: "bare" }, match: url };
	}

	if (!best) {
		parent.appendChild(doc.createTextNode(source));
		return parent;
	}

	const { rule, match } = best;
	if (match.index > 0) appendInline(doc, parent, source.slice(0, match.index));

	if (rule.kind === "bare") {
		parent.appendChild(anchor(doc, match[0], match[0]));
	} else if (rule.kind === "link") {
		parent.appendChild(anchor(doc, match[1] || match[2], match[2]));
	} else if (rule.kind === "code") {
		// Literal by definition: no recursion into the contents, which is what makes
		// `` `**not bold**` `` render as the characters the model typed.
		const el = doc.createElement("code");
		el.className = "ee-md-inline-code";
		el.textContent = match[1];
		parent.appendChild(el);
	} else {
		const el = doc.createElement(INLINE_TAG[rule.kind]);
		appendInline(doc, el, match[1]);
		parent.appendChild(el);
	}

	const rest = source.slice(match.index + match[0].length);
	if (rest) appendInline(doc, parent, rest);
	return parent;
}

/**
 * The first match of `rule` in `source` that clears the rule's own boundary condition.
 *
 * Scans forward rather than giving up on the first rejected hit: in `a_b and _real_ italic`,
 * the intraword `_b and _` must be skipped and the genuine `_real_` still found.
 */
function matchRule(rule, source) {
	if (!rule.wordBoundary) return rule.re.exec(source);

	let offset = 0;
	while (offset < source.length) {
		const match = rule.re.exec(source.slice(offset));
		if (!match) return null;

		const start = offset + match.index;
		const end = start + match[0].length;
		const before = start > 0 ? source[start - 1] : "";
		const after = end < source.length ? source[end] : "";
		if (!WORD_CHAR.test(before) && !WORD_CHAR.test(after)) {
			match.index = start;
			return match;
		}
		offset = start + 1;
	}
	return null;
}

/**
 * An anchor, or inert text when the URL is not safe.
 *
 * Routed through `isSafeUrl` — the parser-based check, not a prefix test — so `javascript:`,
 * `data:`, a credentials trick and the `/\host` origin escape all render as visible text rather
 * than as something clickable. A model can be talked into emitting any of them.
 */
function anchor(doc, label, href) {
	const text = String(label == null ? "" : label);
	if (!isSafeUrl(href)) {
		const span = doc.createElement("span");
		span.className = "ee-md-link is-unsafe";
		span.textContent = text;
		return span;
	}
	const a = doc.createElement("a");
	a.className = "ee-md-link";
	a.textContent = text;
	a.setAttribute("href", href);
	if (/^https?:\/\//i.test(String(href))) {
		a.setAttribute("target", "_blank");
		a.setAttribute("rel", "noopener noreferrer");
	}
	return a;
}
