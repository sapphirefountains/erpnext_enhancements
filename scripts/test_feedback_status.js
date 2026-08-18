#!/usr/bin/env node
/**
 * Guards the one label on the `/feedback` board that is not a status.
 *
 * **The bug this was written after.** A request whose tasks were all finished still read
 * "Tasks Created" forever, because that state is *terminal* in `product_feedback/states.py`
 * — its transition set is empty, deliberately, so the same proposal can never be written to
 * a Project twice. Nothing was broken; there was simply nothing that could ever move it.
 * The Work column beside it said `2/2` the whole time.
 *
 * So the fix is a display rule over data the page already holds, and the assertions below
 * are about the edges of that rule rather than the happy path:
 *
 *   - `0/0` is "nothing has been created", NOT "everything is finished". `done >= created`
 *     alone calls an empty request complete, and that is the entire plausible bug here.
 *   - Reopening a task takes the label back off, because the label IS the tasks. A stored
 *     status could not have done that without a hook on every Task save site-wide.
 *   - Every other status passes through untouched — this rule may not repaint `Rejected`
 *     or `Breakdown Failed` green because somebody once made a task against them.
 *
 * Loads the module directly: `status.js` is a plain ES module with no DOM and no fetch,
 * which is exactly why the rule lives there rather than in `dom.js`. If it ever grows an
 * import that needs a browser this script fails loudly rather than silently asserting
 * nothing — a test that quietly stops testing is worse than none (the QuickBooks suite ran
 * nowhere for weeks; see CLAUDE.md).
 */

const path = require('path');
const { pathToFileURL } = require('url');

const TARGET = path.join(
	__dirname,
	'..',
	'erpnext_enhancements',
	'public',
	'js',
	'feedback',
	'status.js'
);

let failures = 0;

function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

(async () => {
	const { displayStatus, TASKS_CREATED, TASKS_COMPLETED } = await import(
		pathToFileURL(TARGET).href
	);

	console.log('a finished request reads as finished');
	check('all done', displayStatus(TASKS_CREATED, { created: 2, done: 2 }), TASKS_COMPLETED);
	check('one task', displayStatus(TASKS_CREATED, { created: 1, done: 1 }), TASKS_COMPLETED);
	check(
		'over-counted never happens, but does not break it',
		displayStatus(TASKS_CREATED, { created: 2, done: 3 }),
		TASKS_COMPLETED
	);

	console.log('\nunfinished work still reads as Tasks Created');
	check('none done', displayStatus(TASKS_CREATED, { created: 3, done: 0 }), TASKS_CREATED);
	check('some done', displayStatus(TASKS_CREATED, { created: 3, done: 2 }), TASKS_CREATED);

	console.log('\n0/0 is "nothing created", not "everything finished"');
	check('zero of zero', displayStatus(TASKS_CREATED, { created: 0, done: 0 }), TASKS_CREATED);
	check('no progress at all', displayStatus(TASKS_CREATED, undefined), TASKS_CREATED);
	check('null progress', displayStatus(TASKS_CREATED, null), TASKS_CREATED);
	check('empty object', displayStatus(TASKS_CREATED, {}), TASKS_CREATED);

	console.log('\nreopening a task takes the label back off');
	const finished = { created: 2, done: 2 };
	check('finished', displayStatus(TASKS_CREATED, finished), TASKS_COMPLETED);
	check('one reopened', displayStatus(TASKS_CREATED, { created: 2, done: 1 }), TASKS_CREATED);

	console.log('\ngarbage from the wire does not paint a request green');
	check('strings that count', displayStatus(TASKS_CREATED, { created: '2', done: '2' }), TASKS_COMPLETED);
	check('unparseable', displayStatus(TASKS_CREATED, { created: 'x', done: 'y' }), TASKS_CREATED);
	check('done is not a number', displayStatus(TASKS_CREATED, { created: 2, done: null }), TASKS_CREATED);

	console.log('\nevery other status passes through untouched');
	for (const status of [
		'Submitted',
		'Approved',
		'Breakdown Ready',
		'Breakdown Failed',
		'Rejected',
		'Duplicate',
	]) {
		check(status, displayStatus(status, { created: 2, done: 2 }), status);
	}
	check('unknown status', displayStatus('Wat', { created: 1, done: 1 }), 'Wat');
	check('empty status', displayStatus('', { created: 1, done: 1 }), '');

	console.log('\nthe derived label is not the stored one');
	check('they differ', TASKS_COMPLETED === TASKS_CREATED, false);

	console.log('');
	if (failures) {
		console.error(failures + ' assertion(s) failed');
		process.exit(1);
	}
	console.log('feedback status: all assertions passed');
})();
