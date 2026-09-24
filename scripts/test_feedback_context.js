#!/usr/bin/env node
/**
 * Guards `public/js/feedback/context.js`, the /feedback SPA's "which screen were you on" parser.
 *
 * **Why this exists (WI-079 slice 2).** v16 serves the Desk under `/desk/` and redirects every
 * `/app/...` URL there, so a referrer from the Desk is `/desk/...`. The parser matched only
 * `/app/`, which meant every report filed from a Desk screen since the v16 upgrade arrived with
 * an empty doctype and document, and nothing failed. Worse, it failed in silence: the field
 * reads blank, and a blank field reads as "not reported from a form".
 *
 * The cases below cover both prefixes and the Desk shapes that are not a document: a report
 * view, a new unsaved form, and a docname with a slash in it.
 */

const path = require('path');
const { pathToFileURL } = require('url');

const TARGET = path.join(__dirname, '..', 'erpnext_enhancements', 'public', 'js', 'feedback', 'context.js');
const ORIGIN = 'https://erp.example.com';

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
	let C;
	try {
		C = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error('COULD NOT LOAD context.js — it must stay free of DOM/fetch imports.');
		console.error(err && err.message);
		process.exit(1);
	}
	const p = (ref) => C.parseReferrer(ref, ORIGIN);

	check('v16 desk form', p(`${ORIGIN}/desk/sales-invoice/ACC-SINV-2026-00012`), {
		url: '/desk/sales-invoice/ACC-SINV-2026-00012',
		doctype: 'Sales Invoice',
		docname: 'ACC-SINV-2026-00012',
	});
	check('legacy /app form still parses', p(`${ORIGIN}/app/todo/abc123`), {
		url: '/app/todo/abc123',
		doctype: 'Todo',
		docname: 'abc123',
	});
	check('desk list view has no docname', p(`${ORIGIN}/desk/task`), {
		url: '/desk/task',
		doctype: 'Task',
		docname: '',
	});
	check('report view is not a document called "view"', p(`${ORIGIN}/desk/task/view/report`), {
		url: '/desk/task/view/report',
		doctype: 'Task',
		docname: '',
	});
	check('a new unsaved form has no name', p(`${ORIGIN}/desk/todo/new-todo-1`), {
		url: '/desk/todo/new-todo-1',
		doctype: 'Todo',
		docname: '',
	});
	check('a docname with a slash keeps the rest', p(`${ORIGIN}/desk/item/PUMP%2F2HP/extra`), {
		url: '/desk/item/PUMP%2F2HP/extra',
		doctype: 'Item',
		docname: 'PUMP/2HP/extra',
	});
	check('query string is kept in the url only', p(`${ORIGIN}/desk/task?status=Open`), {
		url: '/desk/task?status=Open',
		doctype: 'Task',
		docname: '',
	});
	check('another origin tells us nothing', p('https://elsewhere.example.com/desk/task/T-1'), {
		url: '',
		doctype: '',
		docname: '',
	});
	check('a web page yields just the url', p(`${ORIGIN}/kiosk`), {
		url: '/kiosk',
		doctype: '',
		docname: '',
	});

	if (failures) {
		console.error(`\n${failures} context.js check(s) failed.`);
		process.exit(1);
	}
	console.log('\ncontext.js: all checks passed.');
})();
