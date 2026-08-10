#!/usr/bin/env node
/**
 * Source-shape guards for the floating Triton widget. Three rule families, all of
 * which fail SILENTLY in production and none of which a code review reliably catches.
 *
 *   1. Streaming re-entrancy (original) — every function that clears the transcript,
 *      or switches which session it belongs to, refuses while a stream is in flight.
 *   2. Keyboard correctness (added at the Phase 3 checkpoint) — Enter-to-send ignores
 *      an in-flight IME composition, and Alt+T ignores AltGr. Both defects are
 *      invisible to anybody not using the affected input method or keyboard layout,
 *      which is exactly why they survived from the widget's first commit.
 *   3. Decision #7's "preserve exactly" (added at the same checkpoint) — the human
 *      approved a NEW manifest-backed sources row that marks and reorders; they did
 *      not approve changing what a turn with no manifest sees, which is every turn on
 *      every site until Phase 5 ships. So the original renderer must still be there
 *      and must still be a separate function.
 *
 * THE STREAMING RULE: every function that clears the transcript, or switches which
 * session the transcript belongs to, must refuse while a stream is in flight.
 *
 * Why it needs a test rather than a code review. The failure is completely
 * silent. `pumpText` writes into a `live.wrap` captured in a closure, so once
 * `messages.innerHTML = ""` detaches that node the caret keeps animating on a
 * subtree nobody can see, `finishStreaming` runs `mermaid` against dead DOM, and
 * the turn is still persisted server-side against the session the user just
 * walked away from. Nothing throws. Nothing logs. The user sees an empty panel
 * and assumes they clicked wrong.
 *
 * Two of the four such functions were missing the guard when this was written —
 * `newChat` and `startDailyBriefing` — while `selectSession` and `send` had it.
 * That ratio is the argument for a test: the rule is easy to state, easy to
 * agree with, and easy to forget on the next function somebody adds. Phase 3
 * adds several, since the SPA rewires session switching entirely.
 *
 * How it loads the code: `triton_widget.js` is a desk IIFE that touches
 * `document` and `window` at load, so it cannot be `require`d under node. This
 * reads the source and asserts on its *shape*. That is a real limit — it proves
 * the line is present, not that it executes first — so the assertions below
 * check ORDER too: the guard must precede the destructive statement in the same
 * function body, which is the part that actually matters.
 *
 * If the file is restructured so the markers no longer resolve, this exits 2
 * with a loud message rather than passing vacuously. A test that quietly stops
 * testing is worse than no test; this repo has shipped that twice (see
 * CLAUDE.md on the QuickBooks suite, and D-14 on test_triton_personas.py).
 */

const fs = require('fs');
const path = require('path');

const TARGET = path.join(
	__dirname,
	'..',
	'erpnext_enhancements',
	'public',
	'js',
	'global_enhancements',
	'triton_widget.js'
);

const SOURCE = fs.readFileSync(TARGET, 'utf8');

/** The guard, written exactly one way everywhere. A variant spelling is a finding. */
const GUARD = 'if (state.streaming) return;';

/**
 * Functions that must carry it, and the destructive statement each one performs.
 * `destructive` is what makes the guard load-bearing — a function listed here
 * with no destructive line is either mis-listed or has been defanged.
 */
const MUST_GUARD = [
	{ fn: 'newChat', destructive: 'state.els.messages.innerHTML = ""' },
	{ fn: 'startDailyBriefing', destructive: 'state.els.messages.innerHTML = ""' },
	{ fn: 'selectSession', destructive: 'state.sessionId = id' },
	{ fn: 'send', destructive: 'state.streaming = true' },
];

let failures = 0;

function fail(message) {
	failures += 1;
	console.error('  FAIL  ' + message);
}

function pass(message) {
	console.log('  ok    ' + message);
}

/**
 * The body of `name`, from its declaration to the next top-level `\n\tfunction ` /
 * `\n\tasync function ` at the same indentation. Crude, and adequate: the file is
 * one IIFE with a single level of function nesting, and a miss is reported rather
 * than guessed at.
 */
function bodyOf(name) {
	const decl = new RegExp('\\n\\t(?:async )?function ' + name + '\\s*\\(');
	const start = SOURCE.search(decl);
	if (start === -1) {
		return null;
	}
	const rest = SOURCE.slice(start + 1);
	const next = rest.search(/\n\t(?:async )?function \w+\s*\(/);
	return next === -1 ? rest : rest.slice(0, next);
}

console.log('triton widget — streaming, keyboard and preserved-behaviour guards\n');

for (const { fn, destructive } of MUST_GUARD) {
	const body = bodyOf(fn);
	if (body === null) {
		console.error(
			'MARKERS NOT FOUND: no function named ' +
				fn +
				' in ' +
				path.relative(process.cwd(), TARGET) +
				'.\nThe widget has been restructured. Re-derive this list rather than deleting it — ' +
				'the rule outlives the function names.'
		);
		process.exit(2);
	}

	const guardAt = body.indexOf(GUARD);
	const destructiveAt = body.indexOf(destructive);

	if (guardAt === -1) {
		fail(
			fn +
				'() does not refuse while streaming. Clearing or switching the transcript ' +
				'mid-stream detaches the node pumpText is still writing into, and nothing throws.'
		);
		continue;
	}

	if (destructiveAt === -1) {
		fail(
			fn +
				'() carries the guard but no longer performs ' +
				JSON.stringify(destructive) +
				'. Either this list is stale or the function was defanged; a guard on a ' +
				'function that does nothing destructive is not evidence of anything.'
		);
		continue;
	}

	if (guardAt > destructiveAt) {
		fail(
			fn +
				'() has the guard AFTER ' +
				JSON.stringify(destructive) +
				'. Presence is not enough — by then the damage is done.'
		);
		continue;
	}

	pass(fn + '() refuses while streaming, before it ' + JSON.stringify(destructive));
}

// startDailyBriefing has one extra ordering rule of its own, and it is not cosmetic:
// stamping LS_BRIEF marks today's briefing as delivered. Refusing to show it AFTER
// stamping loses the briefing silently until tomorrow.
{
	const body = bodyOf('startDailyBriefing');
	const guardAt = body.indexOf(GUARD);
	const stampAt = body.indexOf('localStorage.setItem(LS_BRIEF');
	if (guardAt !== -1 && stampAt !== -1 && guardAt > stampAt) {
		fail(
			'startDailyBriefing() stamps LS_BRIEF before refusing. A refused briefing must not ' +
				"be recorded as shown, or the user loses today's silently."
		);
	} else if (guardAt !== -1 && stampAt !== -1) {
		pass('startDailyBriefing() refuses before it stamps LS_BRIEF');
	}
}

// ---------------------------------------------------------------------------
// The two keyboard defects fixed at the Phase 3 checkpoint (approved 2026-08-10).
//
// Both are ORDERING rules, like the streaming guards above: an early `return`
// that lands after the branch it is meant to skip is decoration. And both are
// invisible to anybody who does not use the keyboard layout in question, which
// is why they survived from the widget's first commit and why they need a test
// rather than a code review.
// ---------------------------------------------------------------------------
{
	// (a) Enter-to-send must not fire while an IME is composing. Pressing Enter to
	// COMMIT a candidate is the ordinary way to type Japanese, Chinese or Korean;
	// without the guard it sends the half-finished message instead.
	// The rule itself lives in ONE place — `chat/dom.js::isComposingKey`, which checks both
	// `isComposing` and the legacy `keyCode === 229` and is exercised executably by
	// scripts/test_chat_client_logic.mjs. What this asserts is the half that file cannot:
	// that the widget consults it, and consults it BEFORE it sends.
	const composerStart = SOURCE.indexOf('state.els.text.addEventListener("keydown"');
	const composerEnd = SOURCE.indexOf('state.els.text.addEventListener("input"');
	const block = composerStart === -1 ? '' : SOURCE.slice(composerStart, composerEnd);
	const imeAt = block.indexOf('isComposingKey(e)');
	const sendAt = block.indexOf('onSend()');

	if (composerStart === -1 || composerEnd === -1 || sendAt === -1) {
		console.error(
			'MARKERS NOT FOUND: could not locate the composer keydown handler in ' +
				path.relative(process.cwd(), TARGET) + '.\nRe-derive this check rather than ' +
				'deleting it — the rule outlives the handler shape.'
		);
		process.exit(2);
	} else if (imeAt === -1) {
		fail(
			'the composer keydown handler does not call isComposingKey(). Pressing Enter to ' +
				'COMMIT an IME candidate is how Japanese, Chinese and Korean are typed; without ' +
				'the guard it sends the half-finished message instead, on every word.'
		);
	} else if (imeAt > sendAt) {
		fail('the composer calls isComposingKey() AFTER onSend(). By then the message is gone.');
	} else if (!/import \{[^}]*\bisComposingKey\b/s.test(SOURCE)) {
		fail(
			'isComposingKey is used but not imported from chat/dom.js — so it is either a local ' +
				'reimplementation (two copies of one rule, which is how the next one carries only ' +
				'half of it) or a ReferenceError at load.'
		);
	} else {
		pass('Enter-to-send consults isComposingKey() before it sends');
	}
}

{
	// (b) Alt+T must not fire for AltGr. Windows and many EU layouts report AltGr as
	// ctrlKey+altKey, so the un-excluded form opened the assistant over whatever the
	// user was writing every time they typed a perfectly ordinary character.
	const start = SOURCE.indexOf('document.addEventListener("keydown"');
	const block = SOURCE.slice(start, start + 800);
	const excludeAt = block.search(/e\.ctrlKey \|\| e\.metaKey/);
	const toggleAt = block.indexOf('toggle()');

	if (start === -1 || toggleAt === -1) {
		console.error(
			'MARKERS NOT FOUND: no document-level keydown handler with a toggle() call in ' +
				path.relative(process.cwd(), TARGET) + '.'
		);
		process.exit(2);
	} else if (excludeAt === -1) {
		fail(
			'the Alt+T shortcut does not exclude ctrlKey/metaKey. AltGr is reported as ' +
				'ctrlKey+altKey on many EU layouts, so AltGr+T toggles the assistant over the ' +
				"user's work."
		);
	} else if (excludeAt > toggleAt) {
		fail('the Alt+T shortcut excludes ctrl/meta AFTER calling toggle(). Presence is not enough.');
	} else {
		pass('Alt+T ignores AltGr (ctrlKey/metaKey excluded before toggle())');
	}
}

// ---------------------------------------------------------------------------
// Decision #7's "preserve exactly", now that Phase 3 renders a SECOND sources row.
//
// The human approved a manifest-backed row that marks and reorders (2026-08-10).
// They did not approve changing what a turn WITHOUT a manifest sees — which is
// every turn on every site until Phase 5 ships, i.e. all of them today. So the
// original renderer must still be there, still be called, and still be the
// no-manifest path.
// ---------------------------------------------------------------------------
{
	const body = bodyOf('renderSources');
	if (body === null) {
		console.error(
			'MARKERS NOT FOUND: no renderSources() in ' + path.relative(process.cwd(), TARGET) +
				'.\nThat function IS decision #7. If the sources row was restructured, re-derive ' +
				'this check deliberately and say so in the changelog.'
		);
		process.exit(2);
	}

	// The exact shape recorded in ADR 0009 Appendix A: a flat `.triton-sources` box of
	// `.triton-source` chips, anchor when the source has a url and span when it does not,
	// label from `label || title || url`, textContent (never innerHTML), target=_blank
	// + rel=noopener.
	const required = [
		['box.className = "triton-sources"', 'the container class'],
		['s.label || s.title || s.url', 'the label fallback chain'],
		['a.className = "triton-source"', 'the chip class'],
		['a.textContent = label', 'textContent, never innerHTML'],
		['a.target = "_blank"', 'new-tab behaviour'],
		['a.rel = "noopener"', 'the rel'],
	];
	const missing = required.filter(([needle]) => !body.includes(needle)).map(([, what]) => what);

	if (missing.length) {
		fail(
			'renderSources() no longer preserves the pre-Phase-3 rendering (' + missing.join('; ') +
				'). This is the path every turn takes until Phase 5 emits a manifest, and ' +
				'decision #7 says it is preserved exactly. The APPROVED change is the separate ' +
				'renderManifestSources().'
		);
	} else if (/innerHTML/.test(body)) {
		fail('renderSources() now uses innerHTML. Source labels are user-authored.');
	} else {
		pass('renderSources() still renders the pre-Phase-3 row exactly (the no-manifest path)');
	}

	// And the approved second renderer must be a SEPARATE function, not an edit to the
	// first — otherwise "with no manifest, nothing changed" is a claim rather than a fact.
	if (bodyOf('renderManifestSources') === null) {
		fail(
			'renderManifestSources() is gone. The approved marked/sorted row must live beside ' +
				'renderSources(), not inside it.'
		);
	} else {
		pass('the approved manifest row is a separate renderer, so the old path is untouched');
	}
}

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('triton widget source guards: all assertions passed');
