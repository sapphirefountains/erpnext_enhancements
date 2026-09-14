#!/usr/bin/env node
/**
 * Guards the Gantt export's DATE RANGE (`opts.range = {from, to}`).
 *
 * Why this exists: every failure mode here is silent. A windowed export still
 * produces a chart, still downloads, still looks like a schedule — it is only
 * wrong about which schedule. The three that matter:
 *
 *   1. CLAMPED INSTEAD OF CLIPPED. A task running March–May, exported for
 *      April, must show as filling April *and crossing both edges*. If its
 *      geometry is clamped to the window rather than drawn true and clipped, it
 *      renders as a task that starts on 1 April and ends on the 30th — a
 *      different fact, presented with the same confidence. So the renderer
 *      keeps real coordinates (a bar genuinely at x = -900) and hides the
 *      overflow with a clipPath. Which means the inverse failure is now
 *      possible too: a bar appended to the UNCLIPPED group gets drawn straight
 *      across the name column and off the page. Both are asserted below.
 *   2. DROPPED ROWS WITH NOTHING SAYING SO. Narrowing the calendar drops the
 *      rows that fall outside it, and a reader has no way to tell twelve rows
 *      of schedule from twelve rows left of two hundred and forty. The header
 *      and footer must carry the window and the "N of M" count.
 *   3. OFF-BY-ONE AT THE BOUNDARIES. Shaped `end_date` is EXCLUSIVE (a
 *      date-only end is pushed forward a day server-side) while the `{from,to}`
 *      a person types is INCLUSIVE at both ends. Every combination of those two
 *      conventions produces a plausible-looking chart; only one is right.
 *
 * None of it is reachable from python: the renderer is browser JS. The server
 * half of the same feature — the CSV/XLSX window — is covered by
 * erpnext_enhancements/tests/test_gantt_api.py, and the two must agree, so the
 * boundary cases here deliberately mirror the ones there.
 *
 * How it loads the code: gantt_export.js is a desk IIFE that calls
 * `frappe.provide()` at load and builds SVG through `document.createElementNS`.
 * Both are shimmed below — just enough of an element to record tags, attributes
 * and children, which is also what makes the structural assertions possible.
 */

const fs = require('fs');
const path = require('path');

const TARGET = path.join(
	__dirname,
	'..',
	'erpnext_enhancements',
	'public',
	'js',
	'gantt_widget',
	'gantt_export.js'
);

/* ---------------------------------------------------------------- shims -- */

class El {
	constructor(tag) {
		this.tagName = tag;
		this.attrs = {};
		this.children = [];
		this._text = null;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	getAttribute(k) {
		return this.attrs[k];
	}
	setAttributeNS(_ns, k, v) {
		this.attrs[k] = String(v);
	}
	appendChild(c) {
		this.children.push(c);
		return c;
	}
	set textContent(v) {
		this._text = v;
	}
	get textContent() {
		return this._text;
	}
}

function installShims() {
	global.document = {
		createElementNS: (_ns, tag) => new El(tag),
		createElement: (tag) => new El(tag),
	};
	global.XMLSerializer = class {
		serializeToString(node) {
			const attrs = Object.entries(node.attrs)
				.map(([k, v]) => ` ${k}="${v}"`)
				.join('');
			const inner =
				node._text != null
					? String(node._text)
					: node.children.map((c) => this.serializeToString(c)).join('');
			return `<${node.tagName}${attrs}>${inner}</${node.tagName}>`;
		}
	};
	global.__ = (s, args) =>
		(args || []).reduce((acc, v, i) => acc.split('{' + i + '}').join(String(v)), s);
	global.frappe = {
		provide(dotted) {
			let cur = global;
			for (const part of dotted.split('.')) {
				cur[part] = cur[part] || {};
				cur = cur[part];
			}
		},
		// The export asks frappe to format dates for the header; a fixed ISO
		// format keeps the assertions independent of any locale setting.
		datetime: {
			obj_to_str: (d) => d,
			str_to_user: (d) => {
				const p = (n) => String(n).padStart(2, '0');
				return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
			},
		},
		show_alert: () => {},
	};
	global.erpnext_enhancements = {};
}

function loadExport() {
	installShims();
	// eslint-disable-next-line no-eval
	eval(fs.readFileSync(TARGET, 'utf8'));
	const NS = global.erpnext_enhancements.gantt_export;
	if (!NS || !NS.render_svg || !NS.normalize_range || !NS.filter_rows_to_range) {
		console.error(
			'gantt_export.js did not export render_svg / normalize_range / filter_rows_to_range.\n' +
				'If the range helpers were renamed, update this script — do not delete the test.'
		);
		process.exit(2);
	}
	return NS;
}

/* ------------------------------------------------------------- fixtures -- */

const day = (iso) => {
	const [y, m, d] = iso.split('-').map(Number);
	return new Date(y, m - 1, d);
};

/**
 * One export row, as collect_rows() shapes it. `end_incl` is the last day the
 * task covers; `end` is the exclusive value the renderer does geometry with.
 */
function row(id, text, start, end_incl, extra) {
	const e = day(end_incl);
	return Object.assign(
		{
			id,
			text,
			level: 0,
			parent: null,
			start: day(start),
			end: new Date(e.getFullYear(), e.getMonth(), e.getDate() + 1),
			end_inclusive: e,
			progress: 0,
			type: 'task',
			is_group: false,
		},
		extra || {}
	);
}

// April is the window in every case below. Five tasks: one wholly before, one
// crossing the start, one contained, one crossing the end, one wholly after.
const APRIL = { from: '2026-04-01', to: '2026-04-30' };
const SPREAD = [
	row('P1', 'Phase 1', '2026-01-01', '2026-12-31', { is_group: true }),
	row('T1', 'January', '2026-01-05', '2026-01-20', { level: 1, parent: 'P1' }),
	row('T2', 'CrossesStart', '2026-03-20', '2026-04-10', { level: 1, parent: 'P1' }),
	row('T3', 'Inside', '2026-04-05', '2026-04-20', { level: 1, parent: 'P1' }),
	row('T4', 'CrossesEnd', '2026-04-25', '2026-06-30', { level: 1, parent: 'P1' }),
	row('T5', 'August', '2026-08-02', '2026-08-20', { level: 1, parent: 'P1' }),
];

/* ----------------------------------------------------------- assertions -- */

let failures = 0;
function ok(name, condition, detail) {
	if (condition) {
		console.log(`  ok   ${name}`);
	} else {
		failures++;
		console.log(`  FAIL ${name}${detail == null ? '' : '  -> ' + detail}`);
	}
}

/** Depth-first walk yielding [node, clipped] where clipped means an ancestor
 *  (or the node itself) carries a clip-path. */
function* walk(node, clipped) {
	const now = clipped || !!node.attrs['clip-path'];
	yield [node, now];
	for (const child of node.children) {
		yield* walk(child, now);
	}
}

/** Every x coordinate a node paints at, from x / d / points. */
function xs(node) {
	const out = [];
	if (node.attrs.x != null && !isNaN(+node.attrs.x)) {
		out.push(+node.attrs.x);
		if (node.attrs.width != null) {
			out.push(+node.attrs.x + +node.attrs.width);
		}
	}
	if (node.attrs.x1 != null) out.push(+node.attrs.x1);
	if (node.attrs.x2 != null) out.push(+node.attrs.x2);
	if (node.attrs.d) {
		for (const m of node.attrs.d.matchAll(/[ML]\s+(-?[\d.]+)\s+(-?[\d.]+)/g)) {
			out.push(+m[1]);
		}
	}
	if (node.attrs.points) {
		for (const pair of node.attrs.points.split(' ')) {
			const v = +pair.split(',')[0];
			if (!isNaN(v)) out.push(v);
		}
	}
	return out;
}

/**
 * Continuation chevrons: 3-point triangles, in a bar colour, inside the clipped
 * timeline group. Each of those three conditions rules out something else that
 * is also a filled path — the today marker (same red, but unclipped and in the
 * frame), a milestone diamond (4 points), a dependency arrowhead (clipped and
 * 3-point, but drawn in the link grey).
 */
const BAR_TONES = new Set(['#2563eb', '#1d4ed8', '#dc2626', '#475569']);

function countChevrons(svg) {
	let n = 0;
	for (const [node, clipped] of walk(svg, false)) {
		if (!clipped || node.tagName !== 'path' || !BAR_TONES.has(node.attrs.fill)) {
			continue;
		}
		const points = (node.attrs.d.match(/[ML]\s+-?[\d.]+\s+-?[\d.]+/g) || []).length;
		if (points === 3) {
			n++;
		}
	}
	return n;
}

/**
 * Primitives painted OUTSIDE the timeline clip that fall off the page. Zero is
 * the invariant: the clip is what makes true (unclamped) bar geometry safe, so
 * a bar appended to the unclipped group is exactly the regression this counts.
 */
function strayCount(svg, width) {
	let n = 0;
	for (const [node, clipped] of walk(svg, false)) {
		if (clipped || ['g', 'svg', 'defs', 'clipPath'].includes(node.tagName)) {
			continue;
		}
		if (xs(node).some((x) => x < 0 || x > width)) {
			n++;
		}
	}
	return n;
}

function main() {
	const NS = loadExport();

	console.log('normalize_range');
	ok('both bounds required', NS.normalize_range({ from: '2026-01-01' }) === null);
	ok('inverted rejected', NS.normalize_range({ from: '2026-02-01', to: '2026-01-01' }) === null);
	ok('garbage rejected', NS.normalize_range({ from: 'nope', to: '2026-01-01' }) === null);
	const one = NS.normalize_range({ from: '2026-04-10', to: '2026-04-10' });
	ok(
		'from == to is one WHOLE day (the pair is inclusive)',
		one && one.end - one.start === 86400000,
		one && (one.end - one.start) / 3600000 + 'h'
	);

	console.log('filter_rows_to_range');
	const win = NS.normalize_range(APRIL);
	const kept = NS.filter_rows_to_range(SPREAD, win).map((r) => r.id);
	ok('only the rows that touch the window survive', kept.join(',') === 'P1,T2,T3,T4', kept.join(','));

	// Boundaries. Both ends of a row are exclusive, so these two rows sit one
	// instant either side of the window and must land on opposite sides of it.
	// These mirror test_export_range_boundaries_are_exclusive_at_both_ends.
	const edges = NS.filter_rows_to_range(
		[
			row('A', 'EndsDayBefore', '2026-03-01', '2026-03-31'),
			row('B', 'StartsOnLastDay', '2026-04-30', '2026-05-15'),
			row('C', 'StartsDayAfter', '2026-05-01', '2026-05-10'),
		],
		win
	).map((r) => r.id);
	ok('a task ending the day before the window is out', !edges.includes('A'), edges.join(','));
	ok('a task starting on the window\'s last day is in', edges.includes('B'));
	ok('a task starting the day after is out', !edges.includes('C'));

	const ancestors = NS.filter_rows_to_range(
		[
			row('P', 'Mobilisation', '2026-01-01', '2026-01-31', { is_group: true }),
			row('C', 'Pour slab', '2026-04-05', '2026-04-06', { level: 1, parent: 'P' }),
		],
		win
	).map((r) => r.id);
	ok(
		'an out-of-window ancestor is kept for its in-window child',
		ancestors.join(',') === 'P,C',
		ancestors.join(',')
	);

	const started = Date.now();
	const cycle = NS.filter_rows_to_range(
		[
			row('A', 'A', '2026-04-02', '2026-04-03', { parent: 'B' }),
			row('B', 'B', '2026-04-02', '2026-04-03', { parent: 'A' }),
		],
		win
	).map((r) => r.id);
	ok('a parent cycle terminates', Date.now() - started < 2000 && cycle.length === 2, cycle.join(','));

	console.log('render_svg with a range');
	const out = NS.render_svg(NS.filter_rows_to_range(SPREAD, win), {
		title: 'PRJ-0001',
		brand: { company: 'Sapphire Fountains' },
		range: APRIL,
		total_rows: SPREAD.length,
		links: [{ source: 'T2', target: 'T3' }],
	});
	const xml = NS.svg_string(out.svg);

	ok('the scale covers the window, not the data', out.range_label === '2026-04-01 – 2026-04-30', out.range_label);
	ok('the header states how many rows were dropped', xml.includes('4 of 6 rows'), 'missing "4 of 6 rows"');
	ok(
		'with a header band the footer does not repeat the window',
		!xml.split('Generated').pop().includes('2026-04-01 – 2026-04-30')
	);

	// Print renders with NO header band — export_utils supplies the page its
	// own, and two logos stacked look like a bug. That is also the output most
	// likely to be handed to a customer, so the window has to survive the band
	// being dropped. It moves to the footer.
	const printed = NS.render_svg(NS.filter_rows_to_range(SPREAD, win), {
		brand: false,
		title: '',
		range: APRIL,
		total_rows: SPREAD.length,
	});
	const printedXml = NS.svg_string(printed.svg);
	const printedFooter = printedXml.split('Generated').pop();
	ok('without a header band the footer carries the window', printedFooter.includes('2026-04-01 – 2026-04-30'));
	ok('without a header band the footer carries the dropped-row count', printedFooter.includes('4 of 6 rows'));

	// (1) geometry stays TRUE — a bar that begins before the window must be
	// drawn at a negative offset, not snapped to the window's first day.
	const negative = [...walk(out.svg, false)].some(([n, clipped]) => clipped && xs(n).some((x) => x < 0));
	ok('a bar crossing the start is drawn at true (negative) x, not clamped', negative);

	// (1, inverse) and nothing painted outside the clip may stray off the page.
	const strays = strayCount(out.svg, out.width);
	ok('nothing outside the clipped group is drawn off the page', strays === 0, strays + ' strays');

	// Continuation chevrons: the group row crosses both edges, T2 the start,
	// T4 the end — four. T3 is contained and must carry none.
	//
	// Counted structurally rather than by colour, because C.today is the same
	// red as C.bar_late: a regex over the markup counts the today marker as a
	// chevron, and the "none without a range" assertion below then passes on a
	// chart that has one drawn on it.
	ok('one chevron per crossed edge, none for a contained bar', countChevrons(out.svg) === 4, countChevrons(out.svg) + ' chevrons');

	// Two charts can share one print page, so the clip id must not.
	const a = NS.svg_string(NS.render_svg([row('T', 'T', '2026-04-10', '2026-04-12')], { range: APRIL }).svg);
	const b = NS.svg_string(NS.render_svg([row('T', 'T', '2026-04-10', '2026-04-12')], { range: APRIL }).svg);
	const idOf = (s) => (s.match(/clipPath id="([^"]+)"/) || [])[1];
	ok('clip ids are unique per render', idOf(a) && idOf(a) !== idOf(b), `${idOf(a)} vs ${idOf(b)}`);

	// A one-day window is the degenerate case the widths were built for: it
	// must stretch to the minimum timeline rather than draw a hairline column.
	const single = NS.render_svg([row('T', 'T', '2026-04-10', '2026-04-10')], {
		range: { from: '2026-04-10', to: '2026-04-10' },
		total_rows: 1,
	});
	ok('a one-day window stretches instead of collapsing', single.width > 500, single.width + 'px');

	console.log('render_svg with NO range (the feature is opt-in)');
	const plain = NS.render_svg(SPREAD, { title: 'PRJ-0001' });
	const plainXml = NS.svg_string(plain.svg);
	ok('every row is kept', plain.rows === SPREAD.length, plain.rows + ' rows');
	ok('no chevrons are drawn', countChevrons(plain.svg) === 0, countChevrons(plain.svg) + ' chevrons');
	const plainStrays = strayCount(plain.svg, plain.width);
	ok('nothing is drawn off the page', plainStrays === 0, plainStrays + ' strays');
	ok('the footer says nothing about a range', !plainXml.split('Generated').pop().includes(' – '));

	console.log(failures ? `\n${failures} assertion(s) failed` : '\nall assertions passed');
	process.exit(failures ? 1 : 0);
}

main();
