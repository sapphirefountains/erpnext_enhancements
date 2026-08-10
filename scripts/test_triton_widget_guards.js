#!/usr/bin/env node
/**
 * Guards the streaming re-entrancy rule in the floating Triton widget.
 *
 * THE RULE: every function that clears the transcript, or switches which session
 * the transcript belongs to, must refuse while a stream is in flight.
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

console.log('triton widget — streaming re-entrancy guards\n');

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

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('triton widget streaming guards: all assertions passed');
