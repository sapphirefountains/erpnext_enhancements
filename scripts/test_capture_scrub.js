#!/usr/bin/env node
/**
 * Guards the capture recorder's scrubber (`public/js/capture/scrub.js`, WI-079 slice 2).
 *
 * Everything the recorder keeps passes through `scrubText` or `scrubPath` before it is stored,
 * and the report it feeds is a File that any reviewer can open. So this suite has two halves,
 * and both matter:
 *
 *   - secrets go: emails, JWTs, hex and base64 runs, Frappe `api_key:api_secret` pairs, query
 *     strings, credentials in a URL;
 *   - ordinary text stays. The scrubber sees every console error the Desk prints, and a
 *     heuristic that eats `frappe.exceptions.ValidationError` or a naming-series doc name makes
 *     every report useless. People stop trusting a tool like that, and then they delete it.
 *
 * Loads the module directly. `scrub.js` is a pure ES module; if it ever grows an import that
 * needs a browser, this exits 2 rather than passing without testing anything.
 */

const path = require('path');
const { pathToFileURL } = require('url');

const TARGET = path.join(__dirname, '..', 'erpnext_enhancements', 'public', 'js', 'capture', 'scrub.js');

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
	let S;
	try {
		S = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error('COULD NOT LOAD scrub.js — it must stay a pure module with no DOM imports.');
		console.error(err && err.message);
		process.exit(2);
	}
	for (const name of ['scrubText', 'scrubPath', 'looksLikeToken']) {
		if (typeof S[name] !== 'function') {
			console.error(`MARKER NOT FOUND: scrub.js no longer exports ${name}()`);
			process.exit(2);
		}
	}
	const { scrubText, scrubPath, looksLikeToken } = S;

	console.log('\nemails');
	check('plain', scrubText('Mail nik@sapphirefountains.com now'), 'Mail [email] now');
	check('plus tag and subdomains', scrubText('to: a.b+tag@mail.example.co.uk,'), 'to: [email],');
	check('two in one line', scrubText('x@y.io and z@w.org'), '[email] and [email]');

	console.log('\ntokens');
	const jwt =
		'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';
	check('JWT', scrubText('Bearer ' + jwt + ' rejected'), 'Bearer [token] rejected');
	check('md5 hex', scrubText('hash 5f4dcc3b5aa765d61d8327deb882cf99 bad'), 'hash [token] bad');
	check(
		'sha256 hex',
		scrubText('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
		'[token]'
	);
	check('uuid', scrubText('id 550e8400-e29b-41d4-a716-446655440000.'), 'id [token].');
	check('api_key:api_secret pair', scrubText('Authorization: token 1a2b3c4d5e6f7a8:9f8e7d6c5b4a392'), 'Authorization: token [token]');
	check('base64 basic auth', scrubText('Basic YWJjZGVmMDEyMzQ1Njc4OjlmOGU3ZDZjNWI0YTM5Mg=='), 'Basic [token]');
	check('base64 with a slash', scrubText('key q8Zr/Kd93LmPx2Vb7Tn4Wq+J1sHf0Ye=='), 'key [token]');
	check('random base64url', scrubText('secret_4eC39HqLyjWDarjtT1zdp7dc'), '[token]');
	check('looksLikeToken: 40 random', looksLikeToken('Ab3kP9xQ2mZ7vL1nR8tW4yC6hJ0dF5gS2bN7kM3q'), true);

	// The token rule is statistical, so it is measured statistically: a seeded generator, so a
	// failure is reproducible rather than flaky. 32 random alphanumerics is the shape of most
	// API keys and session tokens; the rule's measured catch rate there is about 98%.
	const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
	let seed = 20260923;
	const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
	let caught = 0;
	for (let i = 0; i < 1000; i++) {
		let s = '';
		for (let j = 0; j < 32; j++) s += ALPHABET[Math.floor(rand() * ALPHABET.length)];
		if (scrubText(s) === '[token]') caught += 1;
	}
	check('catches >= 95% of 1000 random 32-char keys', caught >= 950, true);

	console.log('\nquery strings in text');
	check(
		'absolute URL',
		scrubText('GET https://erp.example.com/api/method/x?token=abc123 404'),
		'GET https://erp.example.com/api/method/x?[query] 404'
	);
	check('root-relative path', scrubText('failed: /itinerary?key=s3cret'), 'failed: /itinerary?[query]');
	check('a question is not a query', scrubText('Is /desk/todo? It is.'), 'Is /desk/todo? It is.');

	console.log('\nordinary text is left alone');
	for (const benign of [
		'frappe.exceptions.ValidationError: Row 3: Item Code is mandatory',
		'CannotChangeConstantError',
		'Uncaught TypeError: Cannot read properties of undefined (reading \'doc\')',
		'/api/method/erpnext_enhancements.api.feedback.submit_capture',
		'erpnext_enhancements_quickbooks_online_sync_settings',
		'ACC-SINV-2026-00001 could not be submitted',
		'PRJ-00739-TASK-2026-00353-followup-reminder',
		'Order 12345678901234567890123456 is late',
		'iPhone12ProMaxUltraEdition2026',
		'/assets/erpnext_enhancements/dist/js/erpnext_enhancements.bundle.ABC123XY.js',
		'Failed to fetch',
		'ResizeObserver loop completed with undelivered notifications.',
	]) {
		check(benign, scrubText(benign), benign);
	}

	console.log('\nbounds');
	check('null', scrubText(null), '');
	check('undefined', scrubText(undefined), '');
	check('number', scrubText(42), '42');
	check('clipped to max', scrubText('a'.repeat(50), 10), 'aaaaaaaaa…');
	check('default max is 500', scrubText('b'.repeat(2000)).length, 500);
	// A token cut in half by the limit must already have been replaced.
	check('scrubbed before clipping', scrubText('x ' + jwt, 12), 'x [token]');

	console.log('\npaths');
	const origin = 'https://erp.example.com';
	check('query and fragment dropped', scrubPath('/desk/todo?x=1#frag'), '/desk/todo');
	check('same origin becomes a path', scrubPath(origin + '/api/method/x?cmd=y', origin), '/api/method/x');
	check(
		'foreign origin keeps its host',
		scrubPath('https://maps.googleapis.com/maps/api/js?key=AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv', origin),
		'//maps.googleapis.com/maps/api/js'
	);
	check('credentials in a URL go', scrubPath('https://user:pass@files.example.net/x', origin), '//files.example.net/x');
	check('protocol-relative same host', scrubPath('//erp.example.com/assets/a.js', origin), '/assets/a.js');
	check(
		'token segment',
		scrubPath('/itinerary/9f8e7d6c5b4a39281706f5e4d3c2b1a0/day/2'),
		'/itinerary/[token]/day/2'
	);
	check('percent-encoded email', scrubPath('/desk/user/nik%40example.com'), '/desk/user/[email]');
	check('doc names survive', scrubPath('/desk/sales-invoice/ACC-SINV-2026-00001'), '/desk/sales-invoice/ACC-SINV-2026-00001');
	check('dotted method path survives', scrubPath('/api/method/frappe.desk.form.load.getdoc'), '/api/method/frappe.desk.form.load.getdoc');
	check('data URL', scrubPath('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA'), 'data:');
	check('blob URL', scrubPath('blob:https://erp.example.com/1234-5678'), 'blob:');
	check('relative', scrubPath('api/method/x?y=1'), 'api/method/x');
	check('malformed encoding is kept, not thrown', scrubPath('/files/100%/x'), '/files/100%/x');
	check('empty', scrubPath(''), '');
	check('null', scrubPath(null), '');
	check('long path clipped', scrubPath('/' + 'segment/'.repeat(80)).length, 300);

	console.log('\npercent-encoded emails and emoji at the cut');
	check('an encoded email is an email', S.scrubText('owner=bob%40acme.com', 200), 'owner=[email]');
	check('a multi-label encoded email too', S.scrubText('to bob%40acme.co.uk now', 200), 'to [email] now');
	{
		const out = S.scrubText('ab\ud83d\ude00cd', 4);
		check('a clip never leaves half an emoji', /[\ud800-\udbff](?![\udc00-\udfff])/.test(out), false);
	}

	console.log('');
	if (failures) {
		console.error(failures + ' assertion(s) failed');
		process.exit(1);
	}
	console.log('capture scrub: all assertions passed');
})();
