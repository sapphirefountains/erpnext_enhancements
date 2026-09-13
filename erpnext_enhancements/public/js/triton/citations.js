/**
 * Inline `[[ref:N]]` citation rendering. Decision #7's addition, and its whole contract.
 *
 * THE THREE RULES THIS FILE EXISTS TO KEEP
 * ========================================
 *
 * 1. **The sources dropdown is unchanged.** It is populated from the manifest, not from
 *    what the model cited, so a stream with no citations at all renders exactly as it does
 *    today and the dropdown is exactly as full as it was. Nothing in this module touches it.
 * 2. **Streaming survives.** The tokenizer is fed the newly-flushable segment only, never
 *    the whole accumulated answer — re-running a regex over the full string on every frame
 *    is O(n²) and visibly stutters on long answers.
 * 3. **No `innerHTML` string concatenation, anywhere.** Labels and snippets are
 *    user-authored: a coworker's message quoted as a source, a filename, a document title.
 *    Concatenating them into HTML is a stored-XSS vector with a straight path from any
 *    employee to every employee. Every node here is built with `document.createElement` and
 *    `textContent`.
 *
 * WHAT MAKES IT SHIPPABLE BEFORE PHASE 5
 * ======================================
 *
 * Phase 5 builds the retrieval that produces the manifest. This module is written against
 * the contract and **degrades to today's exact behaviour when no `citations` event
 * arrives** — that is the required behaviour, not a fallback afterthought, and it is what
 * lets this phase be accepted and shipped independently. `applyCitations` with an empty
 * manifest is a no-op that returns the container untouched.
 *
 * Every exported function except `applyCitations` is pure (no DOM, no clock, no network),
 * so `scripts/test_chat_citations.js` runs the whole degradation table under plain `node`.
 */

/** Tolerant match. The model may space it oddly; anything that does not match stays literal
 *  text, because the model may legitimately be *discussing* the syntax. */
export const CITATION_RE = /\[\[\s*ref\s*:\s*(\d+)\s*\]\]/g;

/**
 * The longest a citation token can be, in characters, used to size the streaming tail
 * buffer. `[[ref:` + digits + `]]` with tolerant whitespace; 16 covers every id this system
 * can assign (manifests are 1..N over a bounded context) with room to spare.
 */
export const MAX_TOKEN_LEN = 16;

/** Citation types whose `url` we will render as a link. `digest` is deliberately absent. */
const LINKABLE = new Set(["erpnext_doc", "chat_message", "email", "drive_file", "url"]);

/**
 * Turn a manifest array into a lookup keyed on `k`.
 *
 * Ids are small integers assigned at context-assembly time, `1..N`, which is what makes an
 * id outside that range PROVABLY invalid and therefore safely droppable. Entries without a
 * usable `k` are discarded here rather than at render time.
 */
export function indexManifest(entries) {
	const index = new Map();
	for (const entry of entries || []) {
		if (!entry || typeof entry !== "object") continue;
		const k = Number(entry.k);
		if (!Number.isInteger(k) || k < 1) continue;
		index.set(k, entry);
	}
	return index;
}

/**
 * **Pure.** Split a text segment into `{type:"text"}` / `{type:"citation"}` tokens.
 *
 * The unit `§4.10` asks for: "Input: a text segment plus the manifest. Output: a token list
 * that the component turns into DOM." Keeping the split and the DOM construction apart is
 * what makes the whole degradation table testable without a browser.
 *
 * An id that is not in the manifest is **dropped silently** — the token disappears and the
 * surrounding prose is left intact. Not rendered raw (which looks like the model leaked its
 * scaffolding), not rendered as a dead link (which is worse: it looks like a source until
 * clicked). `onMiss` is called so the caller can count them; a `citation_miss` rate above
 * ~2% over a rolling window means a prompt edit broke citing, silently, and is worth a
 * health check.
 */
export function tokenizeCitations(text, manifest, onMiss) {
	const source = String(text == null ? "" : text);
	const index = manifest instanceof Map ? manifest : indexManifest(manifest);
	const out = [];
	if (!source) return out;

	// NO MANIFEST AT ALL is a different thing from a manifest that lacks this id, and the two
	// must not be collapsed:
	//
	//   * manifest present, id missing  -> the model invented an id. Drop the token.
	//   * manifest absent               -> the FEATURE is off (Phase 5 is not emitting yet, or
	//                                      the flag is down). Touch nothing. The body renders
	//                                      exactly as it does today, including a stray token,
	//                                      because today nothing rewrites it either.
	//
	// The rule lives here, at the lowest level, rather than only in `applyCitations`, so no
	// caller can reach the drop path with an empty manifest by accident. This is the row that
	// lets Phase 3 ship before Phase 5.
	if (!index.size) return [{ type: "text", value: source }];

	let last = 0;
	CITATION_RE.lastIndex = 0;
	let match;
	while ((match = CITATION_RE.exec(source)) !== null) {
		const k = Number(match[1]);
		const entry = index.get(k);
		if (match.index > last) out.push({ type: "text", value: source.slice(last, match.index) });
		if (entry) {
			out.push({ type: "citation", k, citation: entry });
		} else if (typeof onMiss === "function") {
			onMiss(k);
		}
		last = match.index + match[0].length;
	}
	if (last < source.length) out.push({ type: "text", value: source.slice(last) });

	// Coalesce adjacent text runs so a dropped token does not leave the prose split into two
	// nodes with a seam a screen reader announces as two separate phrases.
	return coalesce(out);
}

/**
 * The streaming tail buffer. A citation token can arrive split across chunks — `[[re` then
 * `f:7]]` — so text is only released once it cannot be the prefix of a token.
 *
 * `flush()` on stream end is NOT optional. Forgetting it truncates the last few characters
 * of every answer that happens to end near a token, which is maddening to diagnose because
 * it depends on where the model stopped.
 */
export class CitationStream {
	constructor() {
		this.buffer = "";
	}

	/**
	 * Feed a chunk; get back the portion that is safe to render now.
	 * @returns {string} possibly empty
	 */
	push(chunk) {
		this.buffer += String(chunk == null ? "" : chunk);
		const hold = holdbackLength(this.buffer);
		if (hold === 0) {
			const out = this.buffer;
			this.buffer = "";
			return out;
		}
		const out = this.buffer.slice(0, this.buffer.length - hold);
		this.buffer = this.buffer.slice(this.buffer.length - hold);
		return out;
	}

	/** Release everything held back. Call on `done` and on `error`, always. */
	flush() {
		const out = this.buffer;
		this.buffer = "";
		return out;
	}
}

/**
 * How many trailing characters could still be the start of a citation token.
 *
 * Exported because it is the one genuinely fiddly piece of arithmetic in the file and it
 * deserves its own test rows: `"…text[["` holds 2, `"…text[[ref:7"` holds 8,
 * `"…text[[ref:7]]"` holds 0 (it is complete — the tokenizer will take it), and a lone `"["`
 * at the very end holds 1.
 */
export function holdbackLength(text) {
	const s = String(text == null ? "" : text);
	const window = s.slice(Math.max(0, s.length - MAX_TOKEN_LEN));
	for (let start = 0; start < window.length; start++) {
		const tail = window.slice(start);
		if (isTokenPrefix(tail)) return tail.length;
	}
	return 0;
}

/**
 * Whether `s` could grow into a complete `[[ref:N]]`. A COMPLETE token is **not** a prefix —
 * it is done, and holding it back would stall the stream forever on an answer that ends in a
 * citation, which is exactly the answer most likely to.
 *
 * Written as an explicit character walk rather than a nested-optional regex. The regex form
 * of "every prefix of this pattern" is unreadable, and unreadable is how the `[[ref:` case
 * ends up excluded and every stream that pauses mid-token loses six characters.
 */
export function isTokenPrefix(s) {
	const n = s.length;
	let i = 0;
	const ws = () => {
		while (i < n && /\s/.test(s[i])) i++;
	};

	if (n === 0 || s[i] !== "[") return false;
	i++;
	if (i === n) return true; // "["
	if (s[i] !== "[") return false;
	i++;
	if (i === n) return true; // "[["

	ws();
	if (i === n) return true; // "[[  "

	for (const ch of "ref") {
		if (i === n) return true; // "[[r", "[[re"
		if (s[i] !== ch) return false;
		i++;
	}
	if (i === n) return true; // "[[ref"

	ws();
	if (i === n) return true;
	if (s[i] !== ":") return false;
	i++;
	if (i === n) return true; // "[[ref:"

	ws();
	if (i === n) return true;

	let digits = 0;
	while (i < n && s[i] >= "0" && s[i] <= "9") {
		i++;
		digits++;
	}
	if (i === n) return digits > 0; // "[[ref:7"
	// "[[ref:]]" has no digits and can never become valid — it is malformed, and malformed
	// tokens stay literal text rather than being held back.
	if (digits === 0) return false;

	ws();
	if (i === n) return true;
	if (s[i] !== "]") return false;
	i++;
	if (i === n) return true; // "[[ref:7]"
	if (s[i] !== "]") return false;
	// The second "]" completes it. Complete is not a prefix.
	return false;
}

/**
 * Render a token list into a parent node. **The only impure function here.**
 *
 * Anchors are built with `createElement` and `textContent`. `href` comes from the server's
 * resolver (`citation.url`) — the client never constructs a URL per type, because desk
 * routing and doctype slugification have changed between versions and a client that builds
 * `/app/{slug}/{name}` itself breaks on an upgrade nobody connected to chat.
 *
 * `digest` renders as a NON-NAVIGATING pill. A digest has no single canonical message to
 * point at, and a link that guesses one is a lie.
 *
 * A `javascript:` (or `data:`, or anything not http/https and not same-origin-relative) URL
 * is refused and the citation degrades to a plain pill. The server validates the scheme too;
 * this is the second of two, because the first one is the one that gets refactored.
 */
export function renderTokens(parent, tokens, options) {
	const opts = options || {};
	const doc = parent.ownerDocument || document;
	for (const token of tokens) {
		if (token.type === "text") {
			parent.appendChild(doc.createTextNode(token.value));
			continue;
		}
		// `onCite` is what lets the sources row mark which entries the model ACTUALLY used
		// (approved 2026-08-10; see `orderManifestForDisplay`). It fires per rendered token,
		// so it fires repeatedly across streaming frames for the same id — the caller
		// accumulates into a Set, which is the only shape that is correct under a renderer
		// that rebuilds the bubble from scratch on every frame.
		if (typeof opts.onCite === "function") opts.onCite(token.k, token.citation);
		parent.appendChild(buildCitationNode(doc, token, opts));
	}
	return parent;
}

/**
 * **Pure.** Order a manifest for the sources row: **cited entries first, then the rest**,
 * each group in id order, with a `cited` flag on every entry.
 *
 * WHY THIS IS A BEHAVIOUR CHANGE AND WHY IT IS DELIBERATE. Locked decision #7 says the
 * sources dropdown is "preserved exactly", and research 03 §12.6 flagged the counter-proposal
 * — show the full manifest with cited entries marked and sorted first — as needing an
 * explicit human decision rather than a unilateral one. It was raised at the Phase 3
 * checkpoint and **approved on 2026-08-10**. This function is where that approval lives.
 *
 * Two properties worth stating because they are what make the change safe:
 *
 * * **With no manifest there is nothing to order**, and the caller falls back to rendering
 *   the `sources` event exactly as it did before this phase. The old path is not merely
 *   reachable, it is the path taken on every site until Phase 5 emits a manifest.
 * * **Id order, not relevance order, within each group.** The ids are assigned at
 *   context-assembly time and the inline markers in the answer read `[1]`, `[2]`, `[3]`; a
 *   sources row sorted by anything else makes the reader hunt for the chip matching the
 *   number they just clicked past.
 *
 * **The `cited` flag and the SORT are separately controllable**, and that separation is
 * load-bearing rather than tidy. While an answer is still streaming the caller wants the
 * marks to be accurate — a chip lighting up as the model leans on it is the thing worth
 * watching — but it does **not** want the row re-sorted, because `cited` grows with every
 * frame and a mid-stream sort slides chips out from under the reader's cursor. So the caller
 * passes `sortCitedFirst: false` while streaming and `true` once at the end.
 *
 * @param {Map|Array} manifest
 * @param {Set|Array} cited  ids that appeared in the rendered answer
 * @param {{sortCitedFirst?: boolean}} [options] defaults to sorting cited-first
 * @returns {Array<{k, citation, cited}>}
 */
export function orderManifestForDisplay(manifest, cited, options) {
	const index = manifest instanceof Map ? manifest : indexManifest(manifest);
	const used = cited instanceof Set ? cited : new Set(cited || []);
	const sortCitedFirst = !options || options.sortCitedFirst !== false;

	const entries = Array.from(index.entries(), ([k, citation]) => ({
		k,
		citation,
		cited: used.has(k),
	}));

	// Stable within each group: sort on (not cited, k). Comparing booleans by subtraction
	// after coercion keeps this a single comparator rather than two passes and a concat.
	if (sortCitedFirst) entries.sort((a, b) => (a.cited === b.cited ? a.k - b.k : a.cited ? -1 : 1));
	else entries.sort((a, b) => a.k - b.k);
	return entries;
}

/**
 * Rewrite the text nodes of an already-rendered body, replacing citation tokens with
 * anchors. Used on the markdown-rendered Triton bubble, where the HTML was produced by the
 * EXISTING renderer and its sanitiser policy — which is load-bearing and must not be
 * loosened to make inline links work.
 *
 * Walking text nodes rather than pre-processing the markdown source is what preserves that:
 * the sanitiser sees exactly the string it saw before this phase, and the citations are
 * added afterwards as DOM. Nothing about the existing policy changes.
 *
 * `code`, `pre` and heading elements are skipped — the system prompt tells the model not to
 * cite inside them, and if it does anyway the token is more likely to be the model
 * discussing the syntax than citing a source.
 */
export function applyCitations(container, manifest, options) {
	if (!container) return container;
	const index = manifest instanceof Map ? manifest : indexManifest(manifest);
	if (!index.size) return container; // No manifest: today's behaviour, byte for byte.

	const doc = container.ownerDocument || document;
	const skip = /^(CODE|PRE|H1|H2|H3|H4|H5|H6|A|SCRIPT|STYLE)$/;
	const walker = doc.createTreeWalker(container, 4 /* NodeFilter.SHOW_TEXT */, {
		acceptNode(node) {
			for (let el = node.parentNode; el && el !== container; el = el.parentNode) {
				if (el.nodeType === 1 && skip.test(el.nodeName)) return 2 /* FILTER_REJECT */;
			}
			return node.nodeValue && node.nodeValue.indexOf("[[") >= 0 ? 1 : 2;
		},
	});

	const targets = [];
	let node;
	while ((node = walker.nextNode())) targets.push(node);

	for (const text of targets) {
		const tokens = tokenizeCitations(text.nodeValue, index, options && options.onMiss);
		if (tokens.length === 1 && tokens[0].type === "text") continue;
		const fragment = doc.createDocumentFragment();
		renderTokens(fragment, tokens, options);
		text.parentNode.replaceChild(fragment, text);
	}

	return container;
}

/** The label a citation renders as. Falls back down the shape rather than showing "[object]". */
export function citationLabel(citation) {
	const c = citation || {};
	return String(c.label || c.name || c.title || c.url || "source");
}

/** The only two schemes that may reach an `href`. Everything else renders as inert text. */
const SAFE_PROTOCOLS = new Set(["http:", "https:"]);

/** Does the value carry its own scheme? Matches the URL spec's scheme grammar, not a guess. */
const HAS_SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.\-]*:/;

/**
 * The origin a relative value is resolved against. Deliberately a `.invalid` host rather than
 * the real site: this is a comparison base, not a place, and hardcoding it keeps the function
 * pure — `scripts/test_chat_citations.mjs` runs it under plain `node` with no `location`.
 */
const RESOLVE_BASE = "https://url-safety-check.invalid";

/**
 * Whether a resolved URL is safe to put in an `href`.
 *
 * **Parse, do not pattern-match.** The previous implementation tested prefixes, and a prefix
 * test cannot answer this question: `/\evil.example` starts with `/`, does not start with
 * `//`, and every browser resolves it to `http://evil.example/` — a protocol-relative escape
 * straight past the one line that existed to prevent it. Backslash is not the only such trick,
 * which is the argument for handing the string to the same parser the browser will use rather
 * than trying to enumerate them: `JaVaScRiPt:`, a leading space, an embedded tab and
 * percent-encoded control characters are all normalised by the parser before we see a protocol.
 *
 * Three rules, in order:
 *
 * 1. **Scheme allowlist.** `http:` and `https:` only. `javascript:`, `data:`, `vbscript:`,
 *    `blob:`, `file:` and anything unlisted fail closed. `mailto:` is deliberately absent —
 *    see the note on `email` citations below.
 * 2. **No credentials.** `https://evil.example@sapphirefountains.com/` reads as the second
 *    host to a person and resolves to the first in some parsers. Nothing legitimate cites a
 *    URL carrying userinfo, so the ambiguity is refused rather than rendered.
 * 3. **A relative value must stay on our origin.** Relative means "starts with `/`", as
 *    before; the parser then has to agree it did not leave. `//host`, `/\host` and `/\/host`
 *    all resolve to a different origin, which is precisely the check a prefix test cannot make.
 *
 * `mailto:` is not allowed. Phase 6 §4.G.7 item 1 asks for the decision explicitly: an `email`
 * citation's `url` is built by this app and points at the ERPNext document, so it is already a
 * site-relative path; a user-typed `mailto:` gains nothing and widens the allowlist for it.
 */
export function isSafeUrl(url) {
	const value = String(url == null ? "" : url).trim();
	if (!value) return false;

	let parsed;
	try {
		parsed = new URL(value, RESOLVE_BASE);
	} catch {
		// An unparseable value is not a link. `URL` throws only on genuinely malformed input.
		return false;
	}

	if (!SAFE_PROTOCOLS.has(parsed.protocol)) return false;
	if (parsed.username || parsed.password) return false;
	if (HAS_SCHEME_RE.test(value)) return true;
	return value.startsWith("/") && parsed.origin === RESOLVE_BASE;
}

// --------------------------------------------------------------------------- internals

function buildCitationNode(doc, token, opts) {
	const citation = token.citation || {};
	const type = String(citation.type || "");
	const label = citationLabel(citation);
	const linkable = LINKABLE.has(type) && isSafeUrl(citation.url);

	const node = doc.createElement(linkable ? "a" : "span");
	node.className = "ee-citation ee-citation-" + (type || "unknown") + (linkable ? "" : " is-flat");
	node.textContent = "[" + token.k + "]";
	// title, not a tooltip library: the snippet is user-authored text and `title` is the one
	// attribute that renders it without any parsing at all.
	node.setAttribute("title", citation.snippet ? `${label} — ${citation.snippet}` : label);
	node.setAttribute("aria-label", label);
	node.dataset.citationK = String(token.k);

	if (linkable) {
		node.setAttribute("href", citation.url);
		if (/^https?:\/\//i.test(String(citation.url))) {
			node.setAttribute("target", "_blank");
			node.setAttribute("rel", "noopener noreferrer");
		}
		if (typeof opts.onNavigate === "function") {
			node.addEventListener("click", (ev) => opts.onNavigate(ev, citation));
		}
	} else if (type === "digest" && typeof opts.onExpandDigest === "function") {
		node.setAttribute("role", "button");
		node.setAttribute("tabindex", "0");
		node.addEventListener("click", () => opts.onExpandDigest(citation));
		node.addEventListener("keydown", (ev) => {
			if (ev.key === "Enter" || ev.key === " ") {
				ev.preventDefault();
				opts.onExpandDigest(citation);
			}
		});
	}

	return node;
}

function coalesce(tokens) {
	const out = [];
	for (const token of tokens) {
		const previous = out[out.length - 1];
		if (token.type === "text" && previous && previous.type === "text") {
			previous.value += token.value;
		} else {
			out.push(token);
		}
	}
	return out;
}
