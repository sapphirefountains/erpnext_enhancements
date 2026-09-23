#!/usr/bin/env node
/**
 * Source-level rules for the /marketing client (TASK-2026-01487), each one a rule that "we were
 * careful" cannot keep. Adapted from the retired chat SPA's `test_chat_source_rules.js`
 * (deleted with the chat module in v1.426.0), whose rules were each written after a defect
 * shipped.
 *
 *  1. NO `innerHTML`, at all, so the rule needs no judgement at the call site. Captions,
 *     comments, asset titles and alt text are typed by one employee and shown to another, the
 *     approver among them.
 *  2. NO VUE. The Desk has window.Vue; this document has the app and no Vue.
 *  3. NO `frappe.*`. This is a website route: the Desk bundle is not on the page, so a call that
 *     works in a developer's Desk tab fails on the real one.
 *  4. CROSS-MODULE NAMES RESOLVE. esbuild fails on an unresolvable import path, but a name that
 *     is simply never imported compiles to a bare global and throws ReferenceError at load.
 *  5. THE BUNDLE RULES. One content-hashed entry; the shell loads bundles, never raw /assets.
 *  6. NO STATE RENDERS NOTHING. Every list renderer branches on the empty case and reaches the
 *     one placeholder writer (`app.showPlaceholder`), because an emptied list reads as a broken
 *     page -- which the chat app shipped three times.
 *  7. A SURFACE THAT READS SHARED STATE ALSO WRITES IT. The chat app shipped three features
 *     whose read side was on one surface and write side on another, each silently dead.
 *  8. WHAT IS APPROVED IS WHAT WAS REVIEWED. Approve and reschedule send the `modified` the page
 *     loaded, so the server can refuse a post that changed underneath them.
 *  9. AN OUTBOUND LINK IS http(s) OR TEXT. A permalink can be typed by a person.
 * 10. THE ACCENT CARRIES NO TEXT. `--ee-brand` (#00a0dd) is 2.97:1 against white both ways.
 * 11. EVERY HEADING CLASS SETS ITS OWN COLOUR, or Frappe's fixed --heading-color wins on dark.
 * 12. THE STYLESHEET'S COMMENTS BALANCE. esbuild only warns on broken CSS and ships it.
 * 13. THE BUNDLE STAYS SMALL. Measured, reported, and held under a ceiling.
 *
 * Plain CommonJS node, no runner and no npm install. Exits 2 with a loud message if a marker
 * stops resolving, rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const ROOT = path.join(__dirname, '..');
const APP = path.join(ROOT, 'erpnext_enhancements');
const CLIENT_DIR = path.join(APP, 'public', 'js', 'marketing');
const BUNDLE = path.join(APP, 'public', 'js', 'marketing.bundle.js');
const CSS = path.join(APP, 'public', 'css', 'marketing.bundle.css');
const SHELL = path.join(APP, 'www', 'marketing.html');

/** Code-only, gzipped: the ceiling for rule 13. Raise it on purpose, in the same commit as the reason. */
const GZIP_BUDGET_BYTES = 24 * 1024;

let failures = 0;
const fail = (m) => {
	failures += 1;
	console.error('  FAIL  ' + m);
};
const pass = (m) => console.log('  ok    ' + m);

function must(file) {
	if (!fs.existsSync(file)) {
		console.error(
			'MARKERS NOT FOUND: ' + path.relative(ROOT, file) + ' does not exist.\n' +
				'The marketing client has been restructured. Re-derive these paths rather than deleting ' +
				'the check: the rules outlive the file names.'
		);
		process.exit(2);
	}
	return fs.readFileSync(file, 'utf8');
}

/** Blank out comments, keeping line numbers. The modules DISCUSS these rules in their headers. */
function stripComments(source) {
	let out = '';
	let i = 0;
	let quote = null;
	while (i < source.length) {
		const ch = source[i];
		const two = source.slice(i, i + 2);
		if (quote) {
			out += ch;
			if (ch === '\\') {
				out += source[i + 1] || '';
				i += 2;
				continue;
			}
			if (ch === quote) quote = null;
			i += 1;
		} else if (ch === '\\') {
			// An escape outside a string belongs to a regex literal: /^https?:\/\// holds a `//`
			// that is not a comment.
			out += two;
			i += 2;
		} else if (ch === '"' || ch === "'" || ch === '`') {
			quote = ch;
			out += ch;
			i += 1;
		} else if (two === '//') {
			while (i < source.length && source[i] !== '\n') {
				out += ' ';
				i += 1;
			}
		} else if (two === '/*') {
			while (i < source.length && source.slice(i, i + 2) !== '*/') {
				out += source[i] === '\n' ? '\n' : ' ';
				i += 1;
			}
			out += '  ';
			i += 2;
		} else {
			out += ch;
			i += 1;
		}
	}
	return out;
}

/**
 * Blank the insides of string literals, keeping template `${...}` expressions: a name check must
 * not read "up to 100 characters." as a call to `characters`.
 */
function blankStrings(src) {
	let out = '';
	let i = 0;
	while (i < src.length) {
		const ch = src[i];
		if (ch === '\\') {
			out += src.slice(i, i + 2);
			i += 2;
		} else if (ch === '"' || ch === "'") {
			out += ch;
			i += 1;
			while (i < src.length && src[i] !== ch && src[i] !== '\n') {
				const escaped = src[i] === '\\';
				out += escaped ? '  ' : ' ';
				i += escaped ? 2 : 1;
			}
			out += src[i] || '';
			i += 1;
		} else if (ch === '`') {
			out += ch;
			i += 1;
			while (i < src.length && src[i] !== '`') {
				if (src[i] === '\\') {
					out += '  ';
					i += 2;
				} else if (src.slice(i, i + 2) === '${') {
					let depth = 1;
					out += '${';
					i += 2;
					while (i < src.length && depth) {
						if (src[i] === '{') depth += 1;
						if (src[i] === '}') depth -= 1;
						out += src[i];
						i += 1;
					}
				} else {
					out += src[i] === '\n' ? '\n' : ' ';
					i += 1;
				}
			}
			out += src[i] || '';
			i += 1;
		} else {
			out += ch;
			i += 1;
		}
	}
	return out;
}

console.log('marketing client — source rules\n');

const moduleFiles = fs
	.readdirSync(CLIENT_DIR)
	.filter((f) => f.endsWith('.js'))
	.map((f) => path.join(CLIENT_DIR, f));
const clientFiles = moduleFiles.concat([BUNDLE]);
if (moduleFiles.length < 10) {
	console.error(
		'MARKERS NOT FOUND: only ' + moduleFiles.length + ' modules under ' + path.relative(ROOT, CLIENT_DIR) +
			'. The scan is meant to cover the whole client; a collapse means the directory moved.'
	);
	process.exit(2);
}
const source = (file) => stripComments(must(file));
const view = (name) => source(path.join(CLIENT_DIR, name));

// ------------------------------------------------------------------ 1-3. innerHTML, Vue, frappe
{
	const inner = [];
	const vue = [];
	const desk = [];
	for (const file of clientFiles) {
		const src = source(file);
		src.split('\n').forEach((line, index) => {
			const where = path.relative(ROOT, file) + ':' + (index + 1) + '  ' + line.trim();
			if (/\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|document\.write/.test(line)) inner.push(where);
			if (/from\s+["']vue|require\(\s*["']vue["']|\bcreateApp\s*\(|\bVue\./.test(line)) vue.push(where);
			if (/\bfrappe\./.test(line)) desk.push(where);
		});
	}
	if (inner.length) fail('markup is assigned as a string:\n        ' + inner.join('\n        ') + '\n        Build the nodes (dom.js el / fill).');
	else pass(`no innerHTML, outerHTML, insertAdjacentHTML or document.write (${clientFiles.length} files)`);
	if (vue.length) fail('the client imports or mounts Vue:\n        ' + vue.join('\n        '));
	else pass('the client bundles no Vue: one runtime per document');
	if (desk.length) fail('the client calls frappe.*, which a website route does not load:\n        ' + desk.join('\n        '));
	else pass('the client never reaches for the Desk\'s frappe object');
}

// ------------------------------------------------------------------ 4. cross-module names
{
	const modules = new Map();
	for (const file of clientFiles) {
		const src = source(file);
		const exports = new Set();
		for (const m of src.matchAll(/^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)/gm)) exports.add(m[1]);
		for (const m of src.matchAll(/^export\s*\{([^}]*)\}/gm)) {
			for (const part of m[1].split(',')) {
				const name = part.trim().split(/\s+as\s+/).pop().trim();
				if (name) exports.add(name);
			}
		}
		modules.set(path.basename(file), { exports, src });
	}
	const allExports = new Set();
	for (const { exports } of modules.values()) for (const n of exports) allExports.add(n);
	if (allExports.size < 40) {
		console.error('MARKERS NOT FOUND: only ' + allExports.size + ' exported names; the export scan has stopped matching.');
		process.exit(2);
	}
	const problems = [];
	for (const [name, { src }] of modules) {
		const imported = new Set();
		for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']/g)) {
			const target = path.basename(m[2]);
			const mod = modules.get(target);
			if (!mod) {
				problems.push(`${name} imports from ${m[2]}, which is not a module of this client`);
				continue;
			}
			for (const wanted of m[1].split(',').map((p) => p.trim().split(/\s+as\s+/)[0].trim()).filter(Boolean)) {
				imported.add(wanted);
				if (!mod.exports.has(wanted)) problems.push(`${name} imports {${wanted}} from ${target}, which does not export it`);
			}
		}
		const declared = new Set();
		for (const m of src.matchAll(/(?:^|\s)(?:export\s+)?(?:async\s+)?function\s+(\w+)/g)) declared.add(m[1]);
		for (const m of src.matchAll(/(?:^|[\s(;{,])(?:export\s+)?(?:const|let|var|class)\s+(\w+)/g)) declared.add(m[1]);
		for (const m of src.matchAll(/(?:const|let|var)\s*\{([^}]*)\}\s*=/g)) {
			for (const part of m[1].split(',')) declared.add(part.trim().split(':').pop().trim());
		}
		for (const m of src.matchAll(/(?:const|let|var)\s*\[([^\]]*)\]\s*=/g)) {
			for (const part of m[1].split(',')) declared.add(part.trim());
		}
		for (const m of src.matchAll(/^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{/gm)) declared.add(m[1]);
		for (const m of src.matchAll(/\(([^()]*)\)\s*(?:=>|\{)/g)) {
			for (const part of m[1].split(',')) {
				const id = part.trim().replace(/=[\s\S]*$/, '').replace(/^\.\.\./, '').trim();
				if (/^[A-Za-z_$][\w$]*$/.test(id)) declared.add(id);
			}
		}
		for (const m of src.matchAll(/for\s*\(\s*(?:const|let)\s+\[?([\w\s,]+)\]?\s+of/g)) {
			for (const part of m[1].split(',')) declared.add(part.trim());
		}
		const code = blankStrings(src);
		for (const exported of allExports) {
			if (imported.has(exported) || declared.has(exported)) continue;
			if (new RegExp('(?<![.\\w$])' + exported + '\\s*[(<),.;\\]]').test(code)) {
				problems.push(`${name} uses ${exported} but neither imports nor declares it (ReferenceError at load)`);
			}
		}
	}
	if (problems.length) fail('cross-module names do not resolve:\n        ' + problems.join('\n        '));
	else pass(`every cross-module name resolves (${modules.size} modules, ${allExports.size} exports)`);
}

// ------------------------------------------------------------------ 5. the bundle rules
{
	const bundle = must(BUNDLE);
	if (!/from\s+["']\.\/marketing\/app\.js["']/.test(bundle)) fail('marketing.bundle.js no longer imports ./marketing/app.js.');
	else pass('marketing.bundle.js is the single entry the shell loads');
	const shell = must(SHELL);
	if (/\/assets\/erpnext_enhancements\//.test(shell)) fail('www/marketing.html references a raw /assets path; use bundled_asset().');
	else if (!/bundled_asset\('marketing\.bundle\.js'\)/.test(shell) || !/bundled_asset\('marketing\.bundle\.css'\)/.test(shell)) {
		fail('www/marketing.html does not load both marketing bundles through bundled_asset().');
	} else pass('www/marketing.html loads bundles, never raw /assets paths');
}

// ------------------------------------------------------------------ 6. no state renders nothing
{
	const app = view('app.js');
	if (!/showPlaceholder\(container, mark, title, sub\)\s*\{/.test(app)) {
		console.error('MARKERS NOT FOUND: app.js has no showPlaceholder(container, mark, title, sub).');
		process.exit(2);
	}
	// The list renderers, and where each lives. Add a renderer here when you add a list.
	const renderers = [
		['view_calendar.js', 'renderUnscheduled'],
		['view_composer.js', 'drawAccounts'],
		['view_composer.js', 'drawMedia'],
		['view_composer.js', 'drawPreviews'],
		['view_composer.js', 'drawProblems'],
		['view_media.js', 'renderMediaGrid'],
		['view_queue.js', 'renderQueueList'],
		['view_results.js', 'renderJobs'],
	];
	const problems = [];
	for (const [file, fn] of renderers) {
		const src = view(file);
		const start = src.search(new RegExp('function ' + fn + '\\s*\\('));
		if (start === -1) {
			console.error('MARKERS NOT FOUND: no function ' + fn + ' in ' + file + '. Re-derive this list.');
			process.exit(2);
		}
		const rest = src.slice(start + 10);
		const next = rest.search(/\n(?:export\s+)?(?:async\s+)?function\s/);
		const body = src.slice(start, next === -1 ? src.length : start + 10 + next);
		if (!/\.length === 0|!\w+(?:\.\w+)*\.length\b|\.length\)\s*\{/.test(body)) problems.push(`${file}::${fn} never branches on an empty list`);
		if (!/showPlaceholder\(/.test(body)) problems.push(`${file}::${fn} can empty its list without reaching showPlaceholder()`);
	}
	for (const file of moduleFiles) {
		if (path.basename(file) === 'app.js') continue;
		if (/ee-mk-placeholder/.test(source(file))) problems.push(`${path.basename(file)} draws its own placeholder; use app.showPlaceholder`);
	}
	if (problems.length) fail('a state that renders nothing:\n        ' + problems.join('\n        '));
	else pass(`all ${renderers.length} list renderers reach the one placeholder writer when empty`);
}

// ------------------------------------------------------------------ 7. reads and writes
{
	const surfaces = [
		['view_calendar.js', 'CALENDAR', 'RESCHEDULE'],
		['view_composer.js', 'GET_POST', 'SAVE_POST'],
		['view_queue.js', 'QUEUE', 'SEND_BACK'],
		['view_media.js', 'MEDIA', 'CREATE_ASSET'],
	];
	// Deliberately read-only, each with its reason.
	const READ_ONLY = {
		'view_results.js':
			'an Unconfirmed job is answered on its Desk form by someone who looked at the network (sweeper.resolve_job); a button here would answer without looking',
	};
	const transport = view('transport.js');
	const problems = [];
	for (const [file, read, write] of surfaces) {
		for (const key of [read, write]) {
			if (!new RegExp('\\b' + key + '\\s*:').test(transport)) {
				console.error('MARKERS NOT FOUND: transport.js no longer declares M.' + key + '.');
				process.exit(2);
			}
		}
		const src = view(file);
		if (!new RegExp('M\\.' + read + '\\b').test(src)) problems.push(`${file} no longer reads M.${read}`);
		if (!new RegExp('M\\.' + write + '\\b').test(src)) problems.push(`${file} reads M.${read} but never writes M.${write}`);
	}
	const covered = new Set(surfaces.map((s) => s[0]).concat(Object.keys(READ_ONLY)));
	for (const file of moduleFiles.map((f) => path.basename(f)).filter((f) => f.startsWith('view_'))) {
		if (!covered.has(file)) problems.push(`${file} is a surface with no entry here: say what it reads and writes, or why it is read-only`);
	}
	if (problems.length) fail('a surface that reads without writing:\n        ' + problems.join('\n        '));
	else pass(`every surface that reads shared state writes it too (${surfaces.length}, plus ${Object.keys(READ_ONLY).length} read-only by design)`);
}

// ------------------------------------------------------------------ 8. approved is reviewed
{
	const composer = view('view_composer.js');
	const calendar = view('view_calendar.js');
	const problems = [];
	if (!/call\(M\.APPROVE,\s*\{\s*post:\s*saved\.name,\s*modified:\s*saved\.modified\s*\}\)/.test(composer)) {
		problems.push('Approve does not send the modified the page loaded');
	}
	if (!/isDirty\(ctx\.state, ctx\.baseline\)[\s\S]{0,200}return;/.test(composer.slice(composer.indexOf('function approve(')))) {
		problems.push('Approve does not stop while the page holds unsaved changes');
	}
	if (!/call\(M\.RESCHEDULE,\s*\{[\s\S]{0,160}modified:\s*post\.modified/.test(calendar)) problems.push('a calendar drag does not send modified');
	if (!/call\(M\.SAVE_POST,\s*\{[\s\S]{0,200}modified:/.test(composer)) problems.push('Save does not send modified');
	if (problems.length) fail('what is approved may not be what was reviewed:\n        ' + problems.join('\n        '));
	else pass('approve, save and reschedule all send the modified the page loaded');
}

// ------------------------------------------------------------------ 9. outbound links
{
	const dom = view('dom.js');
	const start = dom.indexOf('export function outLink(');
	const body = dom.slice(start, start + 400);
	if (start === -1 || !/\^https\?:\\\/\\\//.test(body)) fail('dom.outLink no longer refuses a non-http(s) href.');
	else pass('an outbound link is http(s) or plain text');
	const others = moduleFiles
		.filter((f) => path.basename(f) !== 'dom.js')
		.filter((f) => /\.href\s*=/.test(source(f)));
	if (others.length) fail('an href is assigned outside dom.js: ' + others.map((f) => path.basename(f)).join(', '));
	else pass('every href is set in dom.js');
}

// ------------------------------------------------------------------ 10. the accent carries no text
{
	const css = must(CSS).replace(/\/\*[\s\S]*?\*\//g, '');
	for (const token of ['--ee-brand-ink', '--ee-brand-surface']) {
		if (!css.includes(token + ':')) {
			console.error('MARKERS NOT FOUND: marketing.bundle.css no longer defines ' + token + '.');
			process.exit(2);
		}
	}
	const offenders = [];
	for (const m of css.matchAll(/^\s*([a-z-]+)\s*:\s*([^;]*var\(--ee-brand\)[^;]*);/gm)) {
		const [, prop, value] = m;
		if (/color-mix\(/.test(value) || prop === 'outline') continue;
		offenders.push(`${prop}: ${value.trim()}`);
	}
	const uses = [...css.matchAll(/var\(--ee-brand\)/g)].length;
	if (uses < 4) {
		console.error('MARKERS NOT FOUND: only ' + uses + ' --ee-brand usages; the scan has stopped matching.');
		process.exit(2);
	}
	if (offenders.length) fail('--ee-brand carries text (2.97:1 both ways):\n        ' + offenders.join('\n        '));
	else pass(`the accent carries no text (${uses} --ee-brand usages, all tints or the focus ring)`);
}

// ------------------------------------------------------------------ 11. headings
{
	const css = must(CSS).replace(/\/\*[\s\S]*?\*\//g, '');
	const classes = new Set();
	for (const file of moduleFiles) {
		for (const m of must(file).matchAll(/el\(\s*"h[1-6]"\s*,\s*"([a-z0-9-]+)"/g)) classes.add(m[1]);
	}
	if (classes.size < 4) {
		console.error('MARKERS NOT FOUND: only ' + classes.size + ' classed headings found.');
		process.exit(2);
	}
	const coloured = new Set();
	for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
		if (!/(^|[;\s])color\s*:/.test(m[2])) continue;
		for (const cls of m[1].matchAll(/\.([a-z0-9-]+)/g)) coloured.add(cls[1]);
	}
	const missing = [...classes].filter((c) => !coloured.has(c));
	if (missing.length) fail('heading classes with no explicit color (black on the dark theme): ' + missing.join(', '));
	else pass(`every heading class sets its own colour (${classes.size})`);
}

// ------------------------------------------------------------------ 12. balanced comments
{
	const raw = must(CSS);
	const opens = (raw.match(/\/\*/g) || []).length;
	const closes = (raw.match(/\*\//g) || []).length;
	const residue = raw.replace(/\/\*[\s\S]*?\*\//g, '');
	if (opens !== closes || residue.includes('*/') || residue.includes('/*')) {
		fail(`marketing.bundle.css comments do not balance (${opens} open, ${closes} close).`);
	} else pass(`the stylesheet's comments are balanced (${opens} blocks)`);
}

// ------------------------------------------------------------------ 13. size
{
	const raw = clientFiles.reduce((n, f) => n + Buffer.byteLength(must(f)), 0);
	const code = clientFiles.map((f) => stripComments(must(f)).replace(/^\s+$/gm, '')).join('\n');
	const codeBytes = Buffer.byteLength(code);
	const gz = zlib.gzipSync(code, { level: 9 }).length;
	const css = must(CSS);
	const cssGz = zlib.gzipSync(css.replace(/\/\*[\s\S]*?\*\//g, ''), { level: 9 }).length;
	const kb = (n) => (n / 1024).toFixed(1) + ' KB';
	console.log(`        js: ${kb(raw)} raw, ${kb(codeBytes)} code only, ${kb(gz)} gzipped; css: ${kb(Buffer.byteLength(css))} raw, ${kb(cssGz)} gzipped`);
	if (gz > GZIP_BUDGET_BYTES) fail(`the client is ${kb(gz)} gzipped, over its ${kb(GZIP_BUDGET_BYTES)} ceiling. Raise it on purpose, with the reason.`);
	else pass(`the client is under its ${kb(GZIP_BUDGET_BYTES)} gzipped ceiling`);
}

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('marketing client source rules: all assertions passed');
