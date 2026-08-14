#!/usr/bin/env node
/**
 * Source-shape guards for the field text-wrapping feature. Every rule below fails
 * SILENTLY — none of them throws, and none is reliably caught by a code review.
 *
 *   1. Bundle wiring. A global asset reached through a raw /assets path is served
 *      immutable for a year with no content hash, so edits never reach a device
 *      that already cached it (ADR 0008; the "fix works on desktop, phones still
 *      broken" bug). And the SCSS import must stay extension-less, or sass emits
 *      a runtime `@import url(...css)` that 404s instead of inlining the file.
 *   2. The gate. The feature reads one boot flag. Lose it and the toggle on
 *      ERPNext Enhancements Settings silently stops doing anything — the CSS just
 *      applies to everybody, forever.
 *   3. The `finally` around the html_element swap. This is the sharp one.
 *      field_text_wrap.js flips ControlData's `html_element` static to "textarea"
 *      for the duration of ONE synchronous make_input() call. If a control throws
 *      in that window and the restore is not in a `finally`, the static stays
 *      flipped and every Link, Date, Int and Password field rendered afterwards
 *      becomes a textarea. The page keeps working, so nothing reports it.
 *   4. The exact-match fieldtype test. `df.fieldtype !== "Data"` is what keeps the
 *      swap off ControlData's dozen subclasses. Loosened to a substring or a
 *      prefix test — the obvious "improvement" — it starts eating Link fields.
 *   5. Enter handling, split by field kind. A Data field must REFUSE Enter (its
 *      column is a varchar; a newline in it is a data change). A Small Text field
 *      must KEEP it. The two live in separate functions for exactly that reason,
 *      and merging them is the natural-looking refactor that breaks one or the
 *      other with no visible symptom until someone's note loses its paragraphs.
 *   6. The CSS allowlist. It must not grow to cover the fieldtypes frappe already
 *      styles per-cell (Check, Rating, Code, HTML Editor, Text Editor) or the
 *      numeric ones, where wrapping reads worse than clipping.
 *   7. The sticky grid columns. `.data-row` is a flex row: leave `.row-index` and
 *      `.row-check` out of the height rule and they hold a fixed 43px while the
 *      rest of the row grows, so the checkbox and row number float at the top of
 *      every wrapped row.
 *   8. The missing-field guard on the server flag. feature_flags is read inside
 *      boot_session on EVERY desk page load, and v16's db.get_single_value THROWS
 *      when the field is not yet in the Settings meta — the window between a
 *      deploy and its migrate. Without has_field() that window is a 500 on every
 *      desk page, for everyone.
 *
 * If a marker no longer resolves this exits 2 with a loud message rather than
 * passing vacuously. A test that quietly stops testing is worse than no test;
 * this repo has shipped that twice (see CLAUDE.md on the QuickBooks suite).
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');

function read(...parts) {
	const file = path.join(ROOT, ...parts);
	if (!fs.existsSync(file)) {
		console.error(
			'MARKERS NOT FOUND: ' +
				path.relative(process.cwd(), file) +
				' does not exist. The feature was moved or removed — re-derive these checks ' +
				'deliberately rather than deleting them.'
		);
		process.exit(2);
	}
	return fs.readFileSync(file, 'utf8');
}

const JS = read('erpnext_enhancements', 'public', 'js', 'global_enhancements', 'field_text_wrap.js');
const CSS = read('erpnext_enhancements', 'public', 'css', 'global_enhancements', 'field_text_wrap.css');
const SCSS_ENTRY = read('erpnext_enhancements', 'public', 'css', 'desk_addons.bundle.scss');
const JS_ENTRY = read('erpnext_enhancements', 'public', 'js', 'erpnext_enhancements.bundle.js');
const HOOKS = read('erpnext_enhancements', 'hooks.py');
const BOOT = read('erpnext_enhancements', 'boot.py');
const FLAGS = read('erpnext_enhancements', 'feature_flags.py');

let failures = 0;

function fail(message) {
	failures += 1;
	console.error('  FAIL  ' + message);
}

function pass(message) {
	console.log('  ok    ' + message);
}

/**
 * The body of `name`, from its declaration to the next declaration at the same
 * (one-tab) indentation. The file is one IIFE with a single level of function
 * nesting; a miss is reported rather than guessed at.
 */
function bodyOf(name) {
	const decl = new RegExp('\\n\\tfunction ' + name + '\\s*\\(');
	const start = JS.search(decl);
	if (start === -1) return null;
	const rest = JS.slice(start + 1);
	const next = rest.search(/\n\tfunction \w+\s*\(/);
	return next === -1 ? rest : rest.slice(0, next);
}

function requireBody(name, why) {
	const body = bodyOf(name);
	if (body === null) {
		console.error(
			'MARKERS NOT FOUND: no function named ' +
				name +
				' in field_text_wrap.js.\n' +
				why +
				'\nRe-derive this check rather than deleting it — the rule outlives the function name.'
		);
		process.exit(2);
	}
	return body;
}

console.log('field text wrapping — bundle wiring, gating and control-swap guards\n');

// --- 1. Bundle wiring -------------------------------------------------------

if (!/@import\s+"\.\/global_enhancements\/field_text_wrap"\s*;/.test(SCSS_ENTRY)) {
	if (/field_text_wrap\.css"/.test(SCSS_ENTRY)) {
		fail(
			'desk_addons.bundle.scss imports field_text_wrap WITH the .css extension. sass leaves ' +
				'that in the output as a runtime CSS import, which 404s — the import must be ' +
				'extension-less so sass inlines the file. See that file\'s header.'
		);
	} else {
		fail(
			'field_text_wrap.css is not imported by desk_addons.bundle.scss, so it never ships. ' +
				'Global CSS must go through a bundle entry (ADR 0008), not a raw /assets path.'
		);
	}
} else {
	pass('field_text_wrap.css is imported extension-less by desk_addons.bundle.scss');
}

if (!/import "\.\/global_enhancements\/field_text_wrap\.js";/.test(JS_ENTRY)) {
	fail(
		'field_text_wrap.js is not imported by erpnext_enhancements.bundle.js, so it never runs. ' +
			'Global desk scripts must be added to a bundle entry, not to app_include_js as a raw path.'
	);
} else {
	pass('field_text_wrap.js is imported by the global desk bundle');
}

if (/\/assets\/erpnext_enhancements\/(js|css)\/[^"']*field_text_wrap/.test(HOOKS)) {
	fail(
		'hooks.py references field_text_wrap by a raw /assets path. Those are served immutable ' +
			'for a year with no content hash, so edits never reach a device that already cached ' +
			'them. A raw /assets path in hooks.py is a bug even when it works in testing (ADR 0008).'
	);
} else {
	pass('hooks.py adds no raw /assets path for this feature');
}

// --- 2. The gate ------------------------------------------------------------

if (!JS.includes('frappe.boot.ee_field_text_wrap')) {
	fail(
		'field_text_wrap.js no longer reads frappe.boot.ee_field_text_wrap. The settings toggle ' +
			'then does nothing and the feature is on for everybody, permanently.'
	);
} else {
	pass('the client reads frappe.boot.ee_field_text_wrap');
}

if (!/bootinfo\.ee_field_text_wrap\s*=/.test(BOOT)) {
	fail(
		'boot.py no longer ships ee_field_text_wrap, so the client flag is always undefined and ' +
			'the feature is off for everybody, permanently.'
	);
} else {
	pass('boot.py ships ee_field_text_wrap to the desk client');
}

if (!JS.includes('window.__ee_field_text_wrap_loaded')) {
	fail(
		'the load-once flag is gone. Every global patch in this app carries one; without it a ' +
			'second evaluation double-wraps the control prototypes.'
	);
} else {
	pass('the module carries its load-once flag');
}

// --- 3. The finally around the html_element swap ----------------------------

{
	const body = requireBody(
		'patch_control_data',
		'That function IS the <input>-to-<textarea> swap.'
	);
	const swapAt = body.indexOf('ControlData.html_element = "textarea"');
	const finallyAt = body.indexOf('} finally {');
	const restoreAt = body.indexOf('ControlData.html_element = previous');

	if (swapAt === -1) {
		console.error(
			'MARKERS NOT FOUND: patch_control_data() no longer flips ControlData.html_element.\n' +
				'If the swap was replaced with another mechanism, re-derive this check — the rule ' +
				'(a mutated global static must always be restored) outlives the mechanism.'
		);
		process.exit(2);
	} else if (finallyAt === -1 || restoreAt === -1) {
		fail(
			'the html_element swap is not restored in a `finally`. One control that throws mid-swap ' +
				'then leaves the static flipped, and every Link, Date, Int and Password field ' +
				'rendered afterwards becomes a textarea. Nothing throws; nothing logs.'
		);
	} else if (restoreAt < swapAt || restoreAt < finallyAt) {
		fail(
			'the html_element restore does not sit inside the `finally` that follows the swap. ' +
				'Presence is not enough — a restore on the happy path only is the same bug.'
		);
	} else {
		pass('the html_element swap is restored in a `finally`');
	}
}

// --- 4. The exact-match fieldtype test --------------------------------------

{
	const body = requireBody(
		'wants_textarea',
		'That function is the entire allowlist for the control swap.'
	);
	if (!/df\.fieldtype !== "Data"/.test(body)) {
		fail(
			'wants_textarea() no longer tests df.fieldtype with an exact !== "Data". Every ' +
				'ControlData subclass (Link, Date, Int, Password, Attach, Color) reaches this code ' +
				'through super.make_input(); a substring or prefix test starts swapping their inputs ' +
				'for textareas and breaks autocomplete and the date picker.'
		);
	} else if (!/df\.options/.test(body)) {
		fail(
			'wants_textarea() no longer excludes Data fields that carry `options`. Those are typed ' +
				'fields — URL, Email, Phone, Barcode — and make_input() gives several of them ' +
				'special treatment.'
		);
	} else {
		pass('the swap is limited to plain Data by an exact fieldtype match');
	}
}

// --- 5. Enter handling, split by field kind ---------------------------------

{
	const dataBody = requireBody('enhance', 'That is the Data (single-line) enhancement.');
	const textBody = requireBody(
		'enhance_multiline_cell',
		'That is the Text / Small Text / Long Text enhancement.'
	);

	if (!/e\.key === "Enter"[\s\S]{0,400}?e\.preventDefault\(\)/.test(dataBody)) {
		fail(
			'enhance() no longer blocks Enter on a swapped Data field. A Data column is a varchar ' +
				'and must not gain newlines — the <input> it replaced could not produce one.'
		);
	} else {
		pass('a swapped Data textarea still refuses Enter');
	}

	if (/e\.key === "Enter"/.test(textBody)) {
		fail(
			'enhance_multiline_cell() now touches Enter. Text and Small Text are genuinely ' +
				'multi-line; taking their Enter key away silently flattens every note typed in a ' +
				'grid. These two functions are separate precisely so this cannot happen — do not ' +
				'merge them.'
		);
	} else {
		pass('Text / Small Text keep their Enter key');
	}

	if (!/strip_newlines/.test(dataBody) || /strip_newlines/.test(textBody)) {
		fail(
			'newline stripping is not confined to the Data path. It belongs only where the column ' +
				'cannot hold a newline; applied to Small Text it destroys pasted paragraphs.'
		);
	} else {
		pass('newline stripping applies to Data only');
	}
}

// --- 6. The CSS allowlist ---------------------------------------------------

{
	const clamp = CSS.slice(CSS.indexOf('body.ee-wrap .grid-body .grid-static-col:is('));
	const allowlist = clamp.slice(0, clamp.indexOf(')'));
	if (!allowlist) {
		console.error(
			'MARKERS NOT FOUND: could not locate the :is() fieldtype allowlist in ' +
				'field_text_wrap.css. That list IS the coverage decision.'
		);
		process.exit(2);
	}

	const mustNotClamp = [
		'Check',
		'Rating',
		'Code',
		'HTML Editor',
		'Text Editor',
		'Markdown Editor',
		'Int',
		'Float',
		'Currency',
		'Percent',
		'Date',
		'Datetime',
	];
	const leaked = mustNotClamp.filter((ft) => allowlist.includes('"' + ft + '"'));
	if (leaked.length) {
		fail(
			'the clamp allowlist has grown to cover ' +
				leaked.join(', ') +
				'. frappe already ships per-cell rules for the rich-text and Check/Rating types ' +
				'(grid.scss:377-409) and this fights them; wrapping a number or a date reads worse ' +
				'than clipping it.'
		);
	} else {
		pass('the clamp allowlist still excludes the rich-text, Check/Rating and numeric types');
	}

	if (!allowlist.includes('"Data"')) {
		fail('the clamp allowlist no longer covers Data — the headline case of this feature.');
	} else {
		pass('the clamp allowlist covers Data');
	}
}

// --- 7. The sticky grid columns ---------------------------------------------

{
	const rule = CSS.match(/body\.ee-wrap \.form-grid \.grid-body \.data-row[^{]*\{[^}]*\}/);
	if (!rule) {
		console.error(
			'MARKERS NOT FOUND: could not locate the row-height rule in field_text_wrap.css.'
		);
		process.exit(2);
	}
	const selectors = rule[0].slice(0, rule[0].indexOf('{'));
	const missing = ['.row-index', '.row-check'].filter((sel) => !selectors.includes(sel));
	if (missing.length) {
		fail(
			'the row-height rule no longer covers ' +
				missing.join(' and ') +
				'. `.data-row` is a flex row, so a child left holding frappe\'s fixed 43px will not ' +
				'stretch — the checkbox and row number end up floating at the top of every wrapped row.'
		);
	} else if (!/height:\s*auto/.test(rule[0]) || !/min-height/.test(rule[0])) {
		fail(
			'the row-height rule no longer sets both `height: auto` and a `min-height`. Without ' +
				'the min-height, short rows collapse below frappe\'s 43px and the whole grid ' +
				'changes density.'
		);
	} else {
		pass('the row-height rule covers the sticky index and check columns');
	}
}

// --- 8. The missing-field guard on the server flag --------------------------

{
	const start = FLAGS.indexOf('def field_text_wrap_enabled(');
	if (start === -1) {
		console.error('MARKERS NOT FOUND: no field_text_wrap_enabled() in feature_flags.py.');
		process.exit(2);
	}
	const rest = FLAGS.slice(start);
	const end = rest.indexOf('\ndef ', 1);
	// Drop the docstring before looking for the calls — it names both of them in
	// prose, and an ordering check that matches prose is not checking anything.
	const body = (end === -1 ? rest : rest.slice(0, end)).replace(/"""[\s\S]*?"""/, '');

	const guardAt = body.indexOf('has_field("field_text_wrap_enabled")');
	const readAt = body.indexOf('frappe.db.get_single_value(');
	if (guardAt === -1) {
		fail(
			'field_text_wrap_enabled() does not guard with has_field(). It is read inside ' +
				'boot_session on every desk page load, and v16 get_single_value THROWS when the ' +
				'field is not yet in the Settings meta — so the window between a deploy and its ' +
				'migrate becomes a 500 on every desk page, for every user.'
		);
	} else if (readAt !== -1 && guardAt > readAt) {
		fail(
			'field_text_wrap_enabled() calls get_single_value BEFORE has_field(). Presence is not ' +
				'enough — by then it has already thrown.'
		);
	} else {
		pass('field_text_wrap_enabled() checks has_field() before reading the value');
	}
}

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('field text wrapping source guards: all assertions passed');
