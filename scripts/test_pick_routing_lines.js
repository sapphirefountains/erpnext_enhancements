#!/usr/bin/env node
/**
 * Guards the Pick Routing Map's line arithmetic.
 *
 * Why this exists: `lineModel()` decides the number a driver reads off a printed
 * pick sheet at a supplier's counter — "collect 3". Getting it wrong sends someone
 * across the valley for the wrong quantity, and the mistake is discovered at the
 * counter rather than in review. The same values are rendered twice (a compact
 * list in the dialog, a table on the sheet), so the arithmetic is deliberately
 * computed in one place and this asserts that place is right.
 *
 * It also covers the case production cannot: there is currently **no partly
 * received Purchase Order on the live site**, so the "(n of m received)" and the
 * dimmed already-collected states have no real record to exercise them.
 *
 * How it loads the code: pick_routing_map.js is a desk IIFE — it calls
 * `frappe.ui.form.on()` at load, so it cannot simply be `require`d under node.
 * This extracts the pure-helper region between the file's own banner comments and
 * evaluates that. If those banners are renamed or the helpers move, the script
 * exits 2 with MARKERS NOT FOUND rather than silently asserting nothing — a loud
 * failure is the point, since a test that quietly stops testing is worse than none
 * (the QuickBooks suite ran nowhere for weeks; see CLAUDE.md).
 */

const fs = require('fs');
const path = require('path');

const TARGET = path.join(
	__dirname,
	'..',
	'erpnext_enhancements',
	'public',
	'js',
	'project_enhancements',
	'pick_routing_map.js'
);

const START = '// ------------------------------------------------------------- item lines';
const END = '// --------------------------------------------------------------- controller';

function loadHelpers() {
	const src = fs.readFileSync(TARGET, 'utf8');
	const start = src.indexOf(START);
	const end = src.indexOf(END);
	if (start < 0 || end < 0) {
		console.error(
			'MARKERS NOT FOUND in ' + TARGET + '\n' +
			'This script extracts the pure line helpers between two banner comments.\n' +
			'If you moved or renamed them, update START/END here — do not delete the test.'
		);
		process.exit(2);
	}
	// Desk globals the helper block closes over.
	global.__ = (s, args) =>
		(args || []).reduce((acc, v, i) => acc.split('{' + i + '}').join(String(v)), s);
	global.frappe = {
		utils: {
			escape_html: (s) =>
				String(s == null ? '' : s).replace(
					/[&<>"']/g,
					(c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
				),
		},
		datetime: { str_to_user: (s) => s },
	};

	return eval(
		'(function () { const esc = global.frappe.utils.escape_html; ' +
			src.slice(start, end) +
			'\n return { lineModel, stopLineModels, stopLinesLabel, qtyText, stopLinesHtml }; })()'
	);
}

const m = loadHelpers();
let failures = 0;

function check(name, condition, detail) {
	if (condition) {
		console.log('  ok   ' + name);
	} else {
		console.log('  FAIL ' + name + '  ->  ' + detail);
		failures++;
	}
}

const line = (qty, received) => ({
	item_code: 'X',
	item_name: 'Widget',
	qty: qty,
	received_qty: received,
	uom: 'Unit',
});

// --- outstanding quantity -------------------------------------------------

let r = m.lineModel(line(5, 0));
check('nothing received: 5 outstanding, not done', r.outstanding === 5 && !r.done, JSON.stringify(r));

r = m.lineModel(line(5, 2));
check('partly received: 3 outstanding, not done', r.outstanding === 3 && !r.done, JSON.stringify(r));

r = m.lineModel(line(5, 5));
check('fully received: 0 outstanding, done', r.outstanding === 0 && r.done, JSON.stringify(r));

// Float quantities never land on zero exactly; the tolerance mirrors
// procurement_quantities.TOLERANCE so the two features agree on "received".
r = m.lineModel(line(5, 4.999));
check('0.001 short counts as done', r.done === true, JSON.stringify(r));

r = m.lineModel(line(5, 4.99));
check('0.01 short does NOT count as done', r.done === false, JSON.stringify(r));

// An over-receipt must never print a negative "collect" figure.
r = m.lineModel(line(5, 8));
check('over-receipt clamps to 0', r.outstanding === 0 && r.done, JSON.stringify(r));

r = m.lineModel({ qty: null, received_qty: undefined });
check('missing quantities do not produce NaN', r.outstanding === 0 && r.done, JSON.stringify(r));

// --- display --------------------------------------------------------------

check('qtyText trims a trailing .0', m.qtyText(2.0) === '2', m.qtyText(2.0));
check('qtyText keeps a real fraction', m.qtyText(2.5) === '2.5', m.qtyText(2.5));

// --- stop-level summary ---------------------------------------------------

const mixed = {
	purchase_orders: [
		{
			name: 'PO-1',
			status: 'To Receive and Bill',
			items: [line(5, 0), line(5, 5)],
		},
	],
};
check(
	'label counts only what is still outstanding',
	m.stopLinesLabel(mixed).indexOf('1 still to collect') !== -1,
	m.stopLinesLabel(mixed)
);

const settled = { purchase_orders: [{ name: 'PO-1', items: [line(5, 5)] }] };
check(
	'label says all collected when nothing is outstanding',
	m.stopLinesLabel(settled).indexOf('all collected') !== -1,
	m.stopLinesLabel(settled)
);

check('flattening walks every PO at the stop', m.stopLineModels(mixed).length === 2, 'wrong count');

// --- markup ---------------------------------------------------------------

const html = m.stopLinesHtml(mixed);
check('a received line is dimmed', html.indexOf('is-done') !== -1, 'no is-done class emitted');
check(
	'an outstanding line is not dimmed',
	(html.match(/is-done/g) || []).length === 1,
	'is-done count = ' + (html.match(/is-done/g) || []).length
);

// Item names and descriptions come from the Item master, which staff edit. They
// reach this markup as raw strings, so escaping is a contract, not a nicety.
const hostile = {
	purchase_orders: [
		{
			name: 'PO-<script>alert(1)</script>',
			items: [
				{
					item_code: '"><b>pwn</b>',
					item_name: '<img src=x onerror=alert(1)>',
					qty: 1,
					received_qty: 0,
					uom: 'U',
				},
			],
		},
	],
};
const hostileHtml = m.stopLinesHtml(hostile);
check('item_name is escaped', hostileHtml.indexOf('<img src=x') === -1, 'raw markup leaked');
check('item_code is escaped', hostileHtml.indexOf('<b>pwn</b>') === -1, 'raw markup leaked');
check('PO name is escaped', hostileHtml.indexOf('<script>') === -1, 'raw markup leaked');

if (failures) {
	console.log('\n' + failures + ' assertion(s) failed.');
	process.exit(1);
}
console.log('\nok: pick routing line arithmetic and escaping');
