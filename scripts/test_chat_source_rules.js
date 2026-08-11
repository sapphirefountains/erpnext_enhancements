#!/usr/bin/env node
/**
 * Source-level rules for the chat client, each of which "we were careful" cannot keep.
 *
 * 1. NO `innerHTML` IN THE CHAT CLIENT. Not "no innerHTML with user data" — none at all, so
 *    the rule needs no judgement call at the call site. Message bodies, sender names, room
 *    titles, filenames and citation labels are all user-authored and all reach the renderer;
 *    one careless template literal is a stored-XSS vector with a straight path from any
 *    employee to every employee.
 *
 * 2. EXACTLY ONE VUE RUNTIME. The repo vendors a UMD Vue that sets `window.Vue` on every
 *    Desk page. Phase 3 resolves the two-copies hazard structurally — the SPA is a separate
 *    document at a website route, bundles no Vue at all, and is reached by NAVIGATION rather
 *    than by mounting into a Desk page. This asserts the "bundles no Vue" half, because the
 *    hazard would return the day somebody adds a Vue component to the SPA and it would
 *    present six weeks later as "the widget stopped updating after I opened chat".
 *
 * 3. THE EVENT NAMES MATCH. The server publishes from `chat/realtime.py::ALL_EVENTS`; the
 *    client listens from `chat/socket.js::EVENT_NAMES`. A name added on one side only is
 *    silent: a client listening for an event nobody publishes is indistinguishable, from the
 *    client's side, from a quiet room.
 *
 * 4. CROSS-MODULE NAMES RESOLVE. Every imported name is really exported, and every shared
 *    helper a file USES is really imported. The second half is the one the bundler cannot
 *    do for you: esbuild fails on an unresolvable import PATH, but a name that is simply
 *    never imported compiles to a bare global reference and throws `ReferenceError` at load,
 *    in the browser, on every desk page — which for the widget means the whole assistant
 *    silently fails to build. This caught exactly that during the Phase 3 checkpoint.
 *
 * 5. THE BUNDLE RULES. One content-hashed entry, and no raw `/assets` path in the shell.
 *
 * Plain CommonJS `node`, no runner and no npm install — the shape of the repo's other JS
 * guards. Exits 2 with a loud message if the markers stop resolving, rather than passing
 * vacuously.
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const CLIENT_DIR = path.join(ROOT, 'erpnext_enhancements', 'public', 'js', 'chat');
const REALTIME_PY = path.join(ROOT, 'erpnext_enhancements', 'chat', 'realtime.py');
const SOCKET_JS = path.join(CLIENT_DIR, 'socket.js');
const SURFACE_JS = path.join(
	ROOT, 'erpnext_enhancements', 'public', 'js', 'global_enhancements', 'chat_surface.js'
);
const SPA_BUNDLE = path.join(ROOT, 'erpnext_enhancements', 'public', 'js', 'chat.bundle.js');
const WIDGET_JS = path.join(
	ROOT, 'erpnext_enhancements', 'public', 'js', 'global_enhancements', 'triton_widget.js'
);

let failures = 0;
const fail = (m) => { failures += 1; console.error('  FAIL  ' + m); };
const pass = (m) => console.log('  ok    ' + m);

function must(file) {
	if (!fs.existsSync(file)) {
		console.error(
			'MARKERS NOT FOUND: ' + path.relative(ROOT, file) + ' does not exist.\n' +
			'The chat client has been restructured. Re-derive these paths rather than deleting ' +
			'the test — the rules outlive the file names.'
		);
		process.exit(2);
	}
	return fs.readFileSync(file, 'utf8');
}

console.log('chat client — source rules\n');

// ------------------------------------------------------------------ 1. innerHTML

const clientFiles = fs
	.readdirSync(CLIENT_DIR)
	.filter((f) => f.endsWith('.js'))
	.map((f) => path.join(CLIENT_DIR, f))
	.concat([SPA_BUNDLE, SURFACE_JS]);

if (clientFiles.length < 8) {
	console.error(
		'MARKERS NOT FOUND: only ' + clientFiles.length + ' chat client modules found under ' +
		path.relative(ROOT, CLIENT_DIR) + '. This scan is meant to cover the whole client; a ' +
		'collapse to a handful means the directory moved and the rule is now unenforced.'
	);
	process.exit(2);
}

/**
 * Blank out comments, preserving line numbers so an offender still reports its real line.
 *
 * Necessary rather than fussy: this file's own modules DISCUSS the innerHTML rule at length
 * in their headers, and a naive scan reports every one of those sentences as a violation —
 * which is how a guard gets muted with a broad exemption and stops guarding.
 *
 * Deliberately not a JS parser. Comments are stripped; string literals are left alone, so a
 * string containing the word is still a finding (`node.setAttribute("innerHTML", …)` is not
 * a thing anybody should be doing either).
 */
function stripComments(source) {
	let out = '';
	let i = 0;
	while (i < source.length) {
		const two = source.slice(i, i + 2);
		if (two === '//') {
			while (i < source.length && source[i] !== '\n') { out += ' '; i += 1; }
		} else if (two === '/*') {
			while (i < source.length && source.slice(i, i + 2) !== '*/') {
				out += source[i] === '\n' ? '\n' : ' ';
				i += 1;
			}
			out += '  ';
			i += 2;
		} else {
			out += source[i];
			i += 1;
		}
	}
	return out;
}

{
	const offenders = [];
	for (const file of clientFiles) {
		const source = stripComments(must(file));
		source.split('\n').forEach((line, index) => {
			if (/\binnerHTML\b/.test(line)) {
				offenders.push(path.relative(ROOT, file) + ':' + (index + 1) + '  ' + line.trim());
			}
		});
	}
	if (offenders.length) {
		fail(
			'innerHTML appears in the chat client:\n        ' + offenders.join('\n        ') +
			'\n        Build the nodes instead (dom.js::el / fill). Every message body, sender ' +
			'name, room title, filename and citation label in this application is written by an ' +
			'employee and rendered to every other employee.'
		);
	} else {
		pass('no innerHTML anywhere in the chat client (' + clientFiles.length + ' modules scanned)');
	}
}

// ------------------------------------------------------------------ 2. one Vue

{
	const offenders = [];
	for (const file of clientFiles) {
		const source = must(file);
		if (/from\s+["'](vue|vue\/dist[^"']*)["']/.test(source) || /require\(\s*["']vue["']\s*\)/.test(source)) {
			offenders.push(path.relative(ROOT, file));
		}
		if (/\bcreateApp\s*\(/.test(source) || /\bVue\.createApp\b/.test(source)) {
			offenders.push(path.relative(ROOT, file) + ' (mounts a Vue app)');
		}
	}
	if (offenders.length) {
		fail(
			'the chat client imports or mounts Vue:\n        ' + offenders.join('\n        ') +
			'\n        Phase 3 resolved the two-Vue-copies hazard by having NO Vue in this ' +
			'bundle at all (§4.1 option (c)): the Desk document has window.Vue and no SPA, this ' +
			'document has the SPA and no Vue, and expanding the bubble navigates rather than ' +
			'mounting. Adding one here re-opens a bug that presents as "the widget stopped ' +
			'updating after I opened chat", six weeks later.'
		);
	} else {
		pass('the chat client bundles no Vue — one runtime per document, structurally');
	}
}

// ------------------------------------------------------------------ 3. event parity

{
	const py = must(REALTIME_PY);
	const js = must(SOCKET_JS);

	// Server side: the constants gathered into ALL_EVENTS.
	const pyNames = new Set();
	const constants = {};
	for (const m of py.matchAll(/^(EVENT_[A-Z_]+):\s*Final\[str\]\s*=\s*"([^"]+)"/gm)) {
		constants[m[1]] = m[2];
	}
	const allBlock = py.match(/ALL_EVENTS[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
	if (!allBlock || !Object.keys(constants).length) {
		console.error(
			'MARKERS NOT FOUND: could not read EVENT_* constants or ALL_EVENTS out of ' +
			path.relative(ROOT, REALTIME_PY) + '. Re-derive the parse rather than deleting the ' +
			'check — an event published by one side and ignored by the other is silent.'
		);
		process.exit(2);
	}
	for (const m of allBlock[1].matchAll(/EVENT_[A-Z_]+/g)) {
		if (constants[m[0]]) pyNames.add(constants[m[0]]);
	}

	// Client side: the EVENT_NAMES array.
	const jsBlock = js.match(/export const EVENT_NAMES = \[([\s\S]*?)\];/);
	if (!jsBlock) {
		console.error(
			'MARKERS NOT FOUND: no EVENT_NAMES array in ' + path.relative(ROOT, SOCKET_JS) + '.'
		);
		process.exit(2);
	}
	const jsNames = new Set(Array.from(jsBlock[1].matchAll(/"([^"]+)"/g), (m) => m[1]));

	const publishedNotHeard = [...pyNames].filter((n) => !jsNames.has(n));
	const heardNotPublished = [...jsNames].filter((n) => !pyNames.has(n));

	if (pyNames.size < 8) {
		console.error(
			'MARKERS NOT FOUND: only ' + pyNames.size + ' server event names resolved. The parse ' +
			'has stopped matching and this check is now green while comparing almost nothing.'
		);
		process.exit(2);
	}

	if (publishedNotHeard.length) {
		fail(
			'the server publishes events the client never listens for: ' +
			publishedNotHeard.join(', ') + '. Add them to socket.js::EVENT_NAMES.'
		);
	}
	if (heardNotPublished.length) {
		fail(
			'the client listens for events nothing publishes: ' + heardNotPublished.join(', ') +
			'. From the client\'s side that is indistinguishable from a quiet room.'
		);
	}
	if (!publishedNotHeard.length && !heardNotPublished.length) {
		pass('server and client agree on all ' + pyNames.size + ' realtime event names');
	}
}

// ------------------------------------------------------------------ 4. cross-module names

/**
 * Every name a chat module imports must actually be exported by the module it names, and
 * every chat-module export a file USES must actually be imported by that file.
 *
 * The second half is the one that matters, and it is the reason this check exists rather
 * than being left to the bundler. esbuild fails loudly on an unresolvable *import path*, but
 * a name that is simply never imported is not an error to it at all — it compiles to a bare
 * global reference and throws `ReferenceError` at load, in the browser, on every desk page.
 * For the widget that means the entire assistant silently fails to build.
 *
 * This caught exactly that during the Phase 3 checkpoint: `isComposingKey` was wired into
 * `triton_widget.js` and never imported.
 *
 * It is a heuristic, not a scope analyser — it looks for chat-module export names used as
 * identifiers in a file that neither imports nor declares them. That is enough to catch the
 * failure mode and cheap enough to run with no dependencies.
 */
{
	// The widget is scanned HERE but not by the innerHTML/Vue rules above, and the asymmetry
	// is deliberate: it is a pre-existing Desk IIFE that legitimately renders markdown through
	// `innerHTML` (Appendix A protects that renderer and its sanitiser policy), but it is also
	// the single largest CONSUMER of the shared chat modules — so it is exactly where a
	// used-but-not-imported name does the most damage. Leaving it out is how the first such
	// bug got through.
	const nameFiles = clientFiles.concat([WIDGET_JS]);

	const modules = new Map(); // basename -> {exports:Set, source:string}
	for (const file of nameFiles) {
		const source = stripComments(must(file));
		const exports = new Set();
		for (const m of source.matchAll(/^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)/gm)) {
			exports.add(m[1]);
		}
		for (const m of source.matchAll(/^export\s*\{([^}]*)\}/gm)) {
			for (const part of m[1].split(',')) {
				const name = part.trim().split(/\s+as\s+/).pop().trim();
				if (name) exports.add(name);
			}
		}
		modules.set(path.basename(file), { exports, source });
	}

	const allExports = new Set();
	for (const { exports } of modules.values()) for (const name of exports) allExports.add(name);

	if (allExports.size < 20) {
		console.error(
			'MARKERS NOT FOUND: only ' + allExports.size + ' exported names found across the chat ' +
			'client. The export scan has stopped matching and this check now compares nothing.'
		);
		process.exit(2);
	}

	const problems = [];
	for (const [name, { source }] of modules) {
		// What this file imports, and from where.
		const imported = new Set();
		for (const m of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']/g)) {
			const target = path.basename(m[2]);
			const wanted = m[1]
				.split(',')
				.map((p) => p.trim().split(/\s+as\s+/)[0].trim())
				.filter(Boolean);
			for (const w of wanted) {
				imported.add(w);
				const mod = modules.get(target);
				if (mod && !mod.exports.has(w)) {
					problems.push(`${name} imports {${w}} from ${target}, which does not export it`);
				}
			}
		}
		// Namespace imports (`import * as mentions from ...`) put everything behind a prefix.
		for (const m of source.matchAll(/import\s*\*\s*as\s*(\w+)/g)) imported.add(m[1]);
		// Default-ish and side-effect imports contribute no bare names.

		// Names DECLARED here, in any of the five shapes this codebase actually uses. Getting
		// this set wrong in the LENIENT direction only weakens the check; getting it wrong in
		// the strict direction produces false positives, and a guard that cries wolf is a
		// guard somebody deletes. So it is deliberately generous.
		const declared = new Set();
		//  function foo(...)   /   export function foo(...)
		for (const m of source.matchAll(/(?:^|\s)(?:export\s+)?(?:async\s+)?function\s+(\w+)/g)) declared.add(m[1]);
		//  const/let/var/class foo   — including `for (let foo = 0` and `{ const foo`
		for (const m of source.matchAll(/(?:^|[\s(;{,])(?:export\s+)?(?:const|let|var|class)\s+(\w+)/g)) declared.add(m[1]);
		//  class METHODS: `start(room, thread) {`
		for (const m of source.matchAll(/^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{/gm)) declared.add(m[1]);
		//  PARAMETERS of anything that looks like a signature — `(boot, route) =>` / `(a, b) {`
		for (const m of source.matchAll(/\(([^()]*)\)\s*(?:=>|\{)/g)) {
			for (const part of m[1].split(',')) {
				const id = part.trim().replace(/=[\s\S]*$/, '').replace(/^\.\.\./, '').trim();
				if (/^[A-Za-z_$][\w$]*$/.test(id)) declared.add(id);
			}
		}

		for (const exported of allExports) {
			if (imported.has(exported) || declared.has(exported)) continue;
			// Used as a call, as a `new`, or as a bare identifier followed by a delimiter.
			const used = new RegExp('(?<![.\\w$])' + exported + '\\s*[(<),.;\\]]').test(source);
			if (used) {
				problems.push(
					`${name} uses ${exported} but neither imports nor declares it — a bare global ` +
					`reference that throws ReferenceError at load`
				);
			}
		}
	}

	if (problems.length) {
		fail('cross-module names do not resolve:\n        ' + problems.join('\n        '));
	} else {
		pass(`every chat-module name resolves (${modules.size} modules, ${allExports.size} exports)`);
	}
}

// ------------------------------------------------------------------ 5. keyboard ordering

/**
 * Two ORDERING rules for every keydown handler in the chat client. Both are rules where
 * presence is worthless and position is everything, and both were got wrong once already.
 *
 *  (a) A composer's IME guard must be the FIRST thing in the handler. Enter and Tab commit an
 *      IME candidate, Escape cancels one and the arrows move through it — so a guard sitting
 *      below a mention-menu block protects the exact keystrokes it exists to protect on every
 *      path except the one the user is on.
 *
 *  (b) A handler that binds BARE single-letter shortcuts must exclude ctrl/meta/alt before
 *      acting. AltGr is reported as ctrlKey+altKey on Windows and many EU layouts, and
 *      Ctrl+J / Cmd+K belong to the browser. This is the same defect Alt+T was fixed for.
 */
{
	const handlers = [
		{
			file: SURFACE_JS,
			fn: 'onKey',
			kind: 'composer',
			// The first branch the guard must precede.
			firstBranch: 'this.menuOpen',
		},
		{
			file: path.join(CLIENT_DIR, 'app.js'),
			fn: 'onComposerKey',
			kind: 'composer',
			firstBranch: 'this.mentionState.open',
		},
		{
			file: path.join(CLIENT_DIR, 'app.js'),
			fn: 'onShortcut',
			kind: 'global',
			firstBranch: 'ev.key === "Escape"',
		},
	];

	let checked = 0;
	for (const h of handlers) {
		const source = stripComments(must(h.file));
		const start = source.indexOf(`${h.fn}(ev) {`);
		if (start === -1) {
			console.error(
				'MARKERS NOT FOUND: no ' + h.fn + '(ev) in ' + path.relative(ROOT, h.file) +
				'.\nRe-derive this list rather than deleting it — the rule outlives the handler names.'
			);
			process.exit(2);
		}
		const body = source.slice(start, start + 2500);
		const branchAt = body.indexOf(h.firstBranch);
		const imeAt = body.indexOf('isComposingKey(ev)');

		if (imeAt === -1) {
			fail(`${h.fn}() in ${path.basename(h.file)} does not consult isComposingKey().`);
		} else if (branchAt !== -1 && imeAt > branchAt) {
			fail(
				`${h.fn}() in ${path.basename(h.file)} checks isComposingKey() AFTER its first ` +
				`branch (${h.firstBranch}). During a composition that branch swallows the key ` +
				`first, so the guard protects everything except the case it is for.`
			);
		} else {
			checked += 1;
		}

		if (h.kind === 'global') {
			const modAt = body.search(/ev\.ctrlKey \|\| ev\.metaKey/);
			if (modAt === -1) {
				fail(
					`${h.fn}() binds bare single-letter shortcuts without excluding ctrl/meta/alt. ` +
					`AltGr is ctrlKey+altKey on many EU layouts and Ctrl+J / Cmd+K are the browser's.`
				);
			} else if (branchAt !== -1 && modAt > branchAt + 400) {
				fail(`${h.fn}() excludes ctrl/meta too late to protect its own branches.`);
			} else {
				checked += 1;
			}
		}
	}

	if (checked < handlers.length) {
		// Some handler failed above; the failure message already named it.
	} else {
		pass(`all ${handlers.length} keydown handlers guard IME and modifiers, in the right order`);
	}
}

// ------------------------------------------------------------------ 6. listening != joining

/**
 * **Anything that binds chat event handlers must also JOIN a document room.**
 *
 * This is the rule the bubble shipped without, and the failure is completely silent:
 * `frappe.realtime.on(name, fn)` binds a callback, but the server only delivers a room's
 * events to sockets that emitted `doc_subscribe` for it. A client that only calls `on()`
 * looks perfectly wired, throws nothing, logs nothing, and receives nothing forever. There
 * is no error for "you are not in the room you never asked to join".
 *
 * Both surfaces are checked because they join by different means — the SPA owns its own
 * socket (`socket.js`), the bubble borrows Desk's `frappe.realtime` — and a rule that only
 * covers the implementation you happened to look at is how the other one regressed.
 */
{
	const surfaces = [
		{
			file: SOCKET_JS,
			label: 'the SPA socket client',
			listens: /\.on\(\s*name\s*,|EVENT_NAMES/,
			joins: /emit\(\s*["']doc_subscribe["']/,
		},
		{
			file: SURFACE_JS,
			label: "the bubble's coworker surface",
			listens: /onRealtime\s*\(/,
			joins: /doc_subscribe\s*\(/,
		},
	];

	const problems = [];
	for (const s of surfaces) {
		const src = stripComments(must(s.file));
		if (!s.listens.test(src)) {
			console.error(
				'MARKERS NOT FOUND: could not find the event-handling marker in ' +
				path.relative(ROOT, s.file) + '. Re-derive this check rather than deleting it.'
			);
			process.exit(2);
		}
		if (!s.joins.test(src)) {
			problems.push(
				`${s.label} (${path.basename(s.file)}) binds chat event handlers but never joins a ` +
				`document room. It will receive nothing, silently and forever.`
			);
		}
	}

	// The reconnect half, which is separately silent: Frappe's client never replays
	// `open_docs` on connect, and `doc_subscribe` early-returns while the key is still there,
	// so a surface that does not clear it goes permanently deaf after the first disconnect.
	const surfaceSrc = stripComments(must(SURFACE_JS));
	if (!/open_docs\s*&&\s*\w+\.open_docs\.delete|open_docs\.delete\(/.test(surfaceSrc)) {
		problems.push(
			"the bubble re-joins after a reconnect without clearing frappe.realtime.open_docs " +
			"first, so doc_subscribe early-returns and the re-join never happens. The load " +
			"balancer guarantees a disconnect, so this is the common path."
		);
	}

	if (problems.length) {
		fail('listening is not joining:\n        ' + problems.join('\n        '));
	} else {
		pass('every surface that binds chat events also joins the room, and re-joins on reconnect');
	}
}

// ------------------------------------------------------------------ 7. the dwell needs a sampler

/**
 * **A dwell that nothing re-samples never completes.**
 *
 * `ReadBatcher.observe()` promotes a seq on the SECOND call that finds its conditions still
 * holding; the first only records when they started. Every natural caller is a DOM event — a
 * render, a scroll, an IntersectionObserver callback — and a message that simply sits on
 * screen being read generates no further events. So a surface that samples once per event and
 * never comes back can never mark anything read.
 *
 * That shipped. Read receipts never fired in production: every read mark that moved had been
 * moved by the author's own send. The pure tests all passed, because the dwell logic is
 * correct in isolation — what was missing was a caller that returned, which no unit test of
 * `ReadBatcher` can see.
 *
 * So the rule is asserted where the gap actually was: any surface that calls `observe()` must
 * also consult `hasPending()` and schedule another look.
 */
{
	const samplers = [
		{ file: path.join(CLIENT_DIR, "app.js"), label: "the SPA" },
		{ file: SURFACE_JS, label: "the bubble" },
	];

	const problems = [];
	for (const s of samplers) {
		const src = stripComments(must(s.file));
		if (!/readBatcher\.observe\(/.test(src)) {
			console.error(
				"MARKERS NOT FOUND: " + path.relative(ROOT, s.file) + " no longer calls " +
				"readBatcher.observe(). Re-derive this check rather than deleting it."
			);
			process.exit(2);
		}
		if (!/hasPending\(\)/.test(src)) {
			problems.push(
				`${s.label} (${path.basename(s.file)}) feeds readBatcher.observe() but never checks ` +
				`hasPending(). The dwell needs a second sample and nothing schedules one, so no ` +
				`message is ever marked read by being read.`
			);
		// `[\s\S]{0,80}?` rather than `[^)]*`: the real call is
		// `setTimeout(() => this.evaluateRead(), ...)`, and a negated-paren class cannot cross
		// the `)` in the arrow's own parameter list. That mistake made this check fire on
		// correct code the first time it ran.
		} else if (!/setTimeout\([\s\S]{0,80}?(evaluateRead|sampleDwell)/.test(src)) {
			problems.push(
				`${s.label} checks hasPending() but schedules no follow-up sample, so knowing a ` +
				`dwell is pending changes nothing.`
			);
		}
	}

	// And the batcher must actually expose it.
	const sig = stripComments(must(path.join(CLIENT_DIR, "signals.js")));
	if (!/hasPending\s*\(\s*\)\s*\{/.test(sig)) {
		problems.push("ReadBatcher no longer exposes hasPending(), so no caller can re-sample.");
	}

  if (problems.length) {
		fail("a dwell with no sampler:\n        " + problems.join("\n        "));
	} else {
		pass("both surfaces re-sample the read dwell instead of waiting for an event that never comes");
	}
}

// ------------------------------------------------------------------ 8. the bundle rule

{
	const bundle = must(SPA_BUNDLE);
	if (!/from\s+["']\.\/chat\/app\.js["']/.test(bundle)) {
		fail('chat.bundle.js no longer imports the app entry point.');
	} else {
		pass('chat.bundle.js is the single content-hashed entry the www shell loads');
	}

	const shell = must(path.join(ROOT, 'erpnext_enhancements', 'www', 'chat.html'));
	if (/\/assets\/erpnext_enhancements\//.test(shell)) {
		fail(
			'www/chat.html references a raw /assets path. Those are served immutable for a year ' +
			'with no content hash, so an edit never reaches a device that already cached it — ' +
			'the "fix works on desktop, phones still broken" bug this app has shipped once. Use ' +
			'bundled_asset().'
		);
	} else {
		pass('www/chat.html loads bundles, never raw /assets paths');
	}
}

// ------------------------------------------------------------------ 9. no state renders nothing

/**
 * **Every pane that empties the transcript must put something in its place.**
 *
 * `setItems([])` leaves the virtual list holding two zero-height spacers, which is not a
 * blank message — it is a blank *rectangle*, and it reads as a broken page. This shipped
 * three times over: the cold empty state rendered literally nothing, a search with no hits
 * rendered nothing, and a query the server rejected as too short rendered nothing.
 *
 * The rule is structural: a function that calls `setItems([])` must also, within its own
 * body, reach the one placeholder writer. That is checkable; "looks like it handles the
 * empty case" is not.
 */
{
	const app = stripComments(must(path.join(CLIENT_DIR, 'app.js')));
	const problems = [];

	// The one writer must exist, or the rule below is comparing against nothing.
	if (!/showPlaceholder\s*\(mark,\s*title,\s*sub\)\s*\{/.test(app)) {
		console.error(
			'MARKERS NOT FOUND: app.js has no showPlaceholder(mark, title, sub). The placeholder ' +
			'has been restructured — re-derive this check rather than deleting it.'
		);
		process.exit(2);
	}

	// Every `setItems([])` — the literal empty case — and the function it sits in.
	const fnStarts = [...app.matchAll(/^\t(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{/gm)]
		.map((m) => ({ name: m[1], at: m.index }));
	if (fnStarts.length < 20) {
		console.error('MARKERS NOT FOUND: method scan found only ' + fnStarts.length + ' methods in app.js.');
		process.exit(2);
	}

	const enclosing = (index) => {
		let found = null;
		for (const f of fnStarts) {
			if (f.at <= index) found = f;
			else break;
		}
		return found ? found.name : '(top level)';
	};

	// `openTriton` is the one legitimate exception and says so in place: it empties the
	// transcript and writes a signpost into `els.notice` instead of the placeholder.
	const ALLOWED = new Set(['openTriton']);

	for (const m of app.matchAll(/setItems\(\[\]\)/g)) {
		const fn = enclosing(m.index);
		if (ALLOWED.has(fn)) continue;
		// The body from this call to the end of the function, roughly.
		const next = fnStarts.find((f) => f.at > m.index);
		const body = app.slice(m.index, next ? next.at : app.length);
		if (!/showPlaceholder\(|els\.notice/.test(body)) {
			problems.push(
				`${fn}() empties the transcript and never reaches showPlaceholder(), so the pane ` +
				`renders as a blank rectangle`
			);
		}
	}

	// Search specifically must branch on the EMPTY case before it paints. Checking only that
	// the function mentions the placeholder somewhere is too weak — the success path mentions
	// it too, so deleting the zero-result branch would slip through. This asserts the branch.
	{
		const decl = fnStarts.find((f) => f.name === 'renderSearchResults');
		if (!decl) {
			console.error('MARKERS NOT FOUND: app.js has no renderSearchResults method.');
			process.exit(2);
		}
		const next = fnStarts.find((f) => f.at > decl.at);
		const body = app.slice(decl.at, next ? next.at : decl.at + 4000);
		if (!/!items\.length|items\.length === 0/.test(body)) {
			problems.push(
				'renderSearchResults does not branch on an empty result set, so a search with no ' +
				'hits paints a blank rectangle under a "Search: …" header'
			);
		}
		if (!/too_short/.test(body)) {
			problems.push(
				'renderSearchResults ignores the server\'s `too_short` flag, so a one-character ' +
				'query is indistinguishable from a search that genuinely matched nothing'
			);
		}
	}

	// The transcript is hidden by CSS whenever the placeholder is up, so any pane that renders
	// INTO the transcript must clear it first. Search learnt this the hard way: its hits went
	// into a `display: none` element and it read as "no results".
	const css = must(path.join(ROOT, 'erpnext_enhancements', 'public', 'css', 'chat.bundle.css'));
	if (/\.ee-placeholder:not\(\.is-hidden\)\s*~\s*\.ee-transcript/.test(css)) {
		for (const fn of ['openSearch', 'openTriton']) {
			// From the METHOD, not the first textual occurrence — both of these are called from
			// `routeTo` hundreds of lines above their own definition, and slicing from the call
			// site reads an unrelated region. This check reported both as broken when they were
			// correct, which is the failure mode a guard can least afford.
			const decl = fnStarts.find((f) => f.name === fn);
			if (!decl) {
				console.error('MARKERS NOT FOUND: app.js has no ' + fn + ' method.');
				process.exit(2);
			}
			const next = fnStarts.find((f) => f.at > decl.at);
			const body = app.slice(decl.at, next ? next.at : decl.at + 3000);
			if (!/enterAuxPane\(\)|hidePlaceholder\(\)/.test(body)) {
				problems.push(
					`${fn}() renders into the transcript without clearing the placeholder, and CSS ` +
					`hides the transcript while the placeholder is up — the results go nowhere`
				);
			}
		}
	}

	if (problems.length) {
		fail('a state that renders nothing:\n        ' + problems.join('\n        '));
	} else {
		pass('every pane that empties the transcript renders a state in its place');
	}
}

// ------------------------------------------------------------------ 10. notice != destroy

/**
 * **An error message must not replace the navigation.**
 *
 * The bubble's `notice()` was `clear(els.list)` plus one line of text, and `els.list` is the
 * whole left pane — every room, the unread badges, the people picker. One failed room open
 * gutted it, and nothing repainted it: `ensureLoaded` early-returns once loaded, and the
 * reconnect path bails when no room is open, which is precisely the state the failure left.
 * A page reload was the only way back.
 */
{
	const src = stripComments(must(SURFACE_JS));
	const start = src.indexOf('notice(text) {');
	if (start === -1) {
		console.error('MARKERS NOT FOUND: chat_surface.js has no notice(text). Re-derive this check.');
		process.exit(2);
	}
	const body = src.slice(start, start + 700);
	if (/clear\(this\.els\.list\)/.test(body)) {
		fail(
			'chat_surface notice() clears els.list — the container holding every room row and the ' +
			'people picker. An error message must sit beside the navigation, never replace it.'
		);
	} else if (!/this\.els\.notice/.test(body)) {
		fail('chat_surface notice() writes to no dedicated notice element.');
	} else {
		pass('the bubble reports errors without destroying its own conversation list');
	}

	// And the repaint must not eat the picker either: it is built once, outside the region.
	const rr = src.indexOf('renderRooms() {');
	const rrBody = src.slice(rr, rr + 400);
	if (/clear\(this\.els\.list\)/.test(rrBody)) {
		fail(
			'renderRooms() clears els.list, destroying the people-search input on every repaint — ' +
			'and a repaint happens on every inbound message in any room, so it lands mid-typing.'
		);
	} else {
		pass('a room-list repaint leaves the people picker and its focus alone');
	}
}

// ------------------------------------------------------------------ 11. both surfaces write

/**
 * **A feature whose read side is on one surface and write side on another is dead.**
 *
 * Three defects of this exact shape have now shipped: the bubble listened for room events and
 * never joined the room; the bubble read the tier-3 restoration hint that only the SPA ever
 * wrote; and presence had exactly one writer, so a bubble-only colleague read as *offline* in
 * the SPA beside a live "is typing" line for the same person.
 *
 * So: for each endpoint that records per-user state, BOTH surfaces must call it.
 */
{
	const app = stripComments(must(path.join(CLIENT_DIR, 'app.js')));
	const surface = stripComments(must(SURFACE_JS));
	const transport = stripComments(must(path.join(CLIENT_DIR, 'transport.js')));

	const shared = [
		{ key: 'HEARTBEAT', why: 'presence — without it the user is painted offline to everyone else' },
		{ key: 'LAST_OPEN', why: 'the tier-3 restoration hint — without it /chat opens cold' },
		{ key: 'MARK_READ', why: 'read receipts — without it the sender never sees their message land' },
	];

	const problems = [];
	for (const { key, why } of shared) {
		if (!new RegExp(`\\b${key}\\s*:`).test(transport)) {
			console.error('MARKERS NOT FOUND: transport.js no longer declares M.' + key + '.');
			process.exit(2);
		}
		for (const [label, src] of [['the SPA', app], ['the bubble', surface]]) {
			if (!new RegExp(`M\\.${key}\\b`).test(src)) {
				problems.push(`${label} never calls M.${key} (${why})`);
			}
		}
	}

	if (problems.length) {
		fail('a write side that only one surface has:\n        ' + problems.join('\n        '));
	} else {
		pass(`both surfaces write all ${shared.length} pieces of shared per-user state`);
	}
}

// ------------------------------------------------------------------ 12. no raw docnames

/**
 * **A `User` or `Chat Room` docname must never be rendered as a label.**
 *
 * On this site a `User` docname IS an email address, and `Chat Room` is `autoname: hash`. Both
 * have already reached the screen: a DM titled with a raw email, and search hits captioned
 * with a random hash. Both times the cause was a `||` fallback that turned a missing label
 * into a visible identifier — so the fallback is the thing to ban.
 */
{
	const app = stripComments(must(path.join(CLIENT_DIR, 'app.js')));
	const problems = [];

	if (/hit\.room_title\s*\|\|\s*hit\.room\b/.test(app)) {
		problems.push(
			'renderSearchResults falls back to `hit.room` — a Chat Room docname, which is a random hash'
		);
	}
	// A map straight from a reader row to `.user`, with no roster lookup in the arrow body, is
	// a list of email addresses. Matched UNCONDITIONALLY rather than "unless `full_name`
	// appears nearby": the loose form passed when the raw join was planted next to correct
	// code that still mentioned `full_name`, which is exactly how a half-finished edit lands.
	if (/readers\.map\(\s*\([^)]*\)\s*=>\s*\w+\.user\s*\)/.test(app)) {
		problems.push('showReaders joins raw `reader.user` values, which on this site are emails');
	}

	if (problems.length) {
		fail('a raw identifier on screen:\n        ' + problems.join('\n        '));
	} else {
		pass('no room or user docname is rendered as a label');
	}
}

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('chat client source rules: all assertions passed');
