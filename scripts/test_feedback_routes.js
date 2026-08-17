#!/usr/bin/env node
/**
 * Guards the `/feedback` SPA's router, and one bug in particular.
 *
 * **The bug this was written after.** "New request" linked to the bare `/feedback`, and
 * `routeTo` treated *every* arrival at `/feedback` as a fresh landing — resolving it to
 * whichever view suited the caller. Those two rules compose: a System Manager clicking
 * "New request" was sent straight back to the review queue, so the tab appeared to do
 * nothing and **the form was unreachable for anyone who reviews**. A non-reviewer who had
 * filed before was bounced to "My requests" the same way. It shipped, and it was found by
 * a human clicking the tab — nothing in CI could see it, because both halves are correct
 * on their own and only the composition is wrong.
 *
 * So the assertions below are about the composition. The two that matter most:
 *
 *   - the href the "New request" tab is built from must resolve back to the form, for
 *     every combination of reviewer/has-requests;
 *   - `landingView` must return null for every explicit path, which is what makes it
 *     impossible to express "override the view somebody just clicked".
 *
 * Loads the module directly: `routes.js` is a plain ES module with no DOM and no fetch,
 * which is exactly why the routing logic lives there rather than in `app.js`. If it ever
 * grows an import that needs a browser this script fails loudly rather than silently
 * asserting nothing — a test that quietly stops testing is worse than none (the QuickBooks
 * suite ran nowhere for weeks; see CLAUDE.md).
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
	'routes.js'
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
	let R;
	try {
		R = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error('COULD NOT LOAD routes.js — it must stay free of DOM/fetch imports.');
		console.error(err && err.message);
		process.exit(2);
	}

	const { parseRoute, buildRoute, landingView, defaultView } = R;
	const { VIEW_NEW, VIEW_MINE, VIEW_REVIEW, VIEW_REQUEST } = R;

	for (const name of ['parseRoute', 'buildRoute', 'landingView', 'defaultView']) {
		if (typeof R[name] !== 'function') {
			console.error(`MARKER NOT FOUND: routes.js no longer exports ${name}()`);
			process.exit(2);
		}
	}

	console.log('\nthe regression: clicking "New request" reaches the form');
	// Every audience, because the original bug only bit two of the four.
	for (const [isReviewer, hasRequests, who] of [
		[true, true, 'reviewer with requests'],
		[true, false, 'reviewer, none filed'],
		[false, true, 'employee with requests'],
		[false, false, 'employee, first time'],
	]) {
		const href = buildRoute(VIEW_NEW);
		const landed = landingView(href, isReviewer, hasRequests);
		check(`${who}: landingView leaves the tab alone`, landed, null);
		check(`${who}: href resolves to the form`, parseRoute(href).view, VIEW_NEW);
	}

	console.log('\nthe form has its own URL, so it survives a refresh and can be linked');
	check('buildRoute(new)', buildRoute(VIEW_NEW), '/feedback/new');
	check('parseRoute(/feedback/new)', parseRoute('/feedback/new'), {
		view: VIEW_NEW,
		name: '',
		bare: false,
	});

	console.log('\nonly the bare route is a landing');
	check('bare /feedback is bare', parseRoute('/feedback').bare, true);
	for (const explicit of ['/feedback/new', '/feedback/mine', '/feedback/review', '/feedback/request/ER-1']) {
		check(`${explicit} is not bare`, parseRoute(explicit).bare, false);
		check(`${explicit} is never redirected`, landingView(explicit, true, true), null);
	}

	console.log('\nthe bare route still lands somewhere useful');
	check('reviewer -> queue', landingView('/feedback', true, true), VIEW_REVIEW);
	check('employee with requests -> mine', landingView('/feedback', false, true), VIEW_MINE);
	// null means "stay", and the form is what a bare path already renders.
	check('employee, first time -> stay on the form', landingView('/feedback', false, false), null);

	console.log('\ndeep links round-trip (notify.py writes these into every email)');
	const deep = buildRoute(VIEW_REQUEST, 'ER-2026-00001');
	check('buildRoute(request)', deep, '/feedback/request/ER-2026-00001');
	check('parseRoute round trip', parseRoute(deep), {
		view: VIEW_REQUEST,
		name: 'ER-2026-00001',
		bare: false,
	});
	// product_feedback/notify.py builds this string itself; a divergence is a dead link in
	// an email nobody tests.
	check('matches what notify.py writes', deep, '/feedback/request/ER-2026-00001');

	console.log('\nnonsense lands somewhere usable rather than on an error page');
	check('unknown section', parseRoute('/feedback/wat').view, VIEW_NEW);
	check('request with no name', parseRoute('/feedback/request/').view, VIEW_NEW);
	check('foreign path', parseRoute('/somewhere-else').view, VIEW_NEW);
	check('empty', parseRoute('').view, VIEW_NEW);

	console.log('');
	if (failures) {
		console.error(failures + ' assertion(s) failed');
		process.exit(1);
	}
	console.log('feedback routing: all assertions passed');
})();
