#!/usr/bin/env node
/**
 * Three source-level rules for the chat client, each of which is a rule "we were careful"
 * cannot keep.
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

// ------------------------------------------------------------------ 4. the bundle rule

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

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('chat client source rules: all assertions passed');
