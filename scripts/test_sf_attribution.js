#!/usr/bin/env node
/**
 * The WordPress attribution capture script (docs/website-capture/sf-attribution.js),
 * executed rather than grepped. It runs on a site this repo does not deploy, so
 * this is the only place its behaviour is checked before somebody pastes it into
 * WP Engine — and every failure below is silent there: a Lead arrives with a
 * plausible-looking campaign that is the wrong one.
 *
 *   1. First touch within a session: nothing later in the visit overwrites a value.
 *   2. A new visit WITH campaign tags replaces the whole touch — landing page and
 *      referrer included — and a new visit WITHOUT tags changes nothing.
 *   3. utm_id (the spend-to-lead join key) is captured and filled like the rest.
 *   4. The cookie is scoped to .sapphirefountains.com on our hosts and host-only
 *      elsewhere (a foreign domain attribute makes the browser drop the cookie).
 *   5. It never exceeds the browser's cookie size, and only fills hidden fields
 *      that already exist and are empty.
 *
 * If the script is missing this exits 2 rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'docs', 'website-capture', 'sf-attribution.js');
if (!fs.existsSync(FILE)) {
	console.error('MARKERS NOT FOUND: ' + FILE + ' does not exist. Re-derive these checks deliberately.');
	process.exit(2);
}
const api = require(FILE);
if (!api || typeof api.merge !== 'function') {
	console.error('MARKERS NOT FOUND: sf-attribution.js no longer exports merge() under node.');
	process.exit(2);
}

let failures = 0;
function check(condition, ok, bad) {
	if (condition) {
		console.log('  ok    ' + ok);
	} else {
		failures += 1;
		console.error('  FAIL  ' + (bad || ok));
	}
}

const T0 = Date.parse('2026-09-22T15:00:00Z');
const MIN = 60000;
const page = (search, pathname, referrer) => ({
	params: api.paramsFrom(search || ''),
	path: (pathname || '/') + (search || ''),
	referrer: referrer || '',
});

console.log('sf-attribution.js — session first touch, tagged-visit replacement, cookie, fill\n');

// --- 1. first touch within a session --------------------------------------------
let s = api.merge({}, page('?utm_source=google&utm_medium=cpc&utm_campaign=spring&utm_id=2145&gclid=AAA', '/fountains', 'https://www.google.com/'), T0);
check(s.utm_id === '2145', 'utm_id captured on arrival', 'utm_id not captured: ' + JSON.stringify(s));
check(s.landing_page === '/fountains?utm_source=google&utm_medium=cpc&utm_campaign=spring&utm_id=2145&gclid=AAA', 'landing page is the first page, query included');
check(s.first_referrer === 'https://www.google.com/', 'external referrer recorded');

let s2 = api.merge(s, page('?utm_source=newsletter&utm_campaign=other', '/contact', 'https://www.sapphirefountains.com/fountains'), T0 + 5 * MIN);
check(s2.utm_source === 'google' && s2.utm_campaign === 'spring' && s2.utm_id === '2145', 'a second tagged pageview in the same session overwrites nothing');
check(s2.landing_page === s.landing_page, 'landing page unchanged within the session');

// --- 2. across sessions ----------------------------------------------------------
let untagged = api.merge(s, page('', '/about', 'https://www.bing.com/'), T0 + 3 * 24 * 60 * MIN);
check(
	untagged.utm_campaign === 'spring' && untagged.gclid === 'AAA' && untagged.first_referrer === 'https://www.google.com/' && untagged.landing_page === s.landing_page,
	'an untagged visit days later leaves the stored touch alone (referrer and landing page included)',
	'an untagged visit changed the touch: ' + JSON.stringify(untagged)
);
check(untagged.first_seen === s.first_seen, 'first_seen is never rewritten');

let direct = api.merge({}, page('', '/', ''), T0);
let organicLater = api.merge(direct, page('', '/blog', 'https://www.google.com/'), T0 + 2 * 60 * MIN);
check(!organicLater.first_referrer && organicLater.landing_page === '/', 'a later untagged visit does not patch its referrer onto an older touch');

let paidLater = api.merge(s, page('?utm_source=facebook&utm_medium=paid_social&utm_id=9876', '/offer', 'https://l.facebook.com/'), T0 + 40 * MIN);
check(paidLater.utm_source === 'facebook' && paidLater.utm_id === '9876', 'a new tagged visit starts a new touch');
check(!paidLater.gclid && !paidLater.utm_campaign, 'the new touch carries none of the old one (no stale gclid or campaign)', 'stale keys survived: ' + JSON.stringify(paidLater));
check(paidLater.landing_page === '/offer?utm_source=facebook&utm_medium=paid_social&utm_id=9876' && paidLater.first_referrer === 'https://l.facebook.com/', 'the new touch has its own landing page and referrer');
check(paidLater.first_seen === s.first_seen, 'first_seen survives a replaced touch');

let boundary = api.merge(s, page('?utm_source=x', '/', ''), T0 + api.SESSION_MINUTES * MIN);
check(boundary.utm_source === 'google', 'exactly SESSION_MINUTES later is still the same session');

let legacy = api.merge({ utm_source: 'old', landing_page: '/legacy' }, page('', '/now', 'https://example.com/'), T0);
check(legacy.landing_page === '/legacy' && legacy.utm_source === 'old', 'a cookie without timestamps keeps its values');

// --- 3. parameters ---------------------------------------------------------------
const p = api.paramsFrom('?utm_id=%20123%20&fbclid=zzz&gbraid=GB&msclkid=MS&utm_term=' + 'x'.repeat(400));
check(p.utm_id === '123', 'values are trimmed');
check(!('fbclid' in p), 'fbclid is not captured (Meta adds it to organic links too)');
check(p.gbraid === 'GB' && p.msclkid === 'MS', 'gbraid and msclkid captured');
check(p.utm_term.length === 255, 'values are capped at 255');
check(api.PARAM_KEYS.indexOf('utm_id') !== -1 && api.FIELD_KEYS.indexOf('utm_id') !== -1, 'utm_id is both captured and filled');
check(api.externalReferrer('https://erp.sapphirefountains.com/x') === '' && api.externalReferrer('not a url') === '', 'own hosts and junk are not referrers');

// --- 4. cookie domain ------------------------------------------------------------
check(api.cookieDomainFor('www.sapphirefountains.com') === '.sapphirefountains.com', 'www shares the cookie with erp');
check(api.cookieDomainFor('sapphirefountains.com') === '.sapphirefountains.com', 'apex shares it too');
check(api.cookieDomainFor('sapphirefountains.wpengine.com') === '', 'a staging host gets a host-only cookie');
check(api.cookieDomainFor('evilsapphirefountains.com') === '', 'a lookalike domain does not match');

// --- 5. size and fill ------------------------------------------------------------
const huge = {};
api.PARAM_KEYS.concat(['landing_page', 'first_referrer']).forEach((k) => (huge[k] = '"'.repeat(255)));
const fitted = api.fitCookie(huge);
check(api.serialize(fitted).length <= api.MAX_COOKIE_CHARS, 'an oversized cookie is shed to fit', 'still ' + api.serialize(fitted).length + ' chars');
check('utm_id' in fitted && 'gclid' in fitted, 'the join keys are the last things shed');

function fakeDoc(inputs, cookie) {
	const doc = {
		cookie: cookie || '',
		referrer: '',
		readyState: 'complete',
		written: [],
		documentElement: {},
		addEventListener() {},
		querySelectorAll(sel) {
			const name = sel.match(/name="([^"]+)"/)[1];
			return inputs.filter((i) => i.name === name);
		},
	};
	Object.defineProperty(doc, 'cookie', {
		get() {
			return cookie || '';
		},
		set(v) {
			doc.written.push(v);
		},
	});
	return doc;
}
const inputs = [{ name: 'utm_id', value: '' }, { name: 'utm_source', value: 'set-by-hand' }, { name: 'landing_page', value: '' }];
const doc = fakeDoc(inputs);
api.boot({
	document: doc,
	location: { search: '?utm_source=google&utm_id=555', pathname: '/x', hostname: 'www.sapphirefountains.com', protocol: 'https:' },
	now: () => T0,
});
check(inputs[0].value === '555', 'boot fills an empty hidden field');
check(inputs[1].value === 'set-by-hand', 'boot never clobbers a value already set');
check(inputs[2].value === '/x?utm_source=google&utm_id=555', 'landing page reaches its hidden field');
const written = doc.written[0] || '';
check(/; domain=\.sapphirefountains\.com/.test(written) && /; Secure/.test(written) && /SameSite=Lax/.test(written), 'cookie is domain-wide, Secure and SameSite=Lax', written);
check(written.indexOf('sf_attr=') === 0, 'cookie name is sf_attr');

const roundTrip = api.readCookie({ cookie: 'other=1; ' + written.split(';')[0] });
check(roundTrip && roundTrip.utm_id === '555', 'the cookie reads back');
check(api.readCookie({ cookie: 'sf_attr=%7Bbroken' }) === null, 'a malformed cookie reads as absent');

console.log('');
if (failures) {
	console.error(failures + ' check(s) failed.');
	process.exit(1);
}
console.log('All sf-attribution checks passed.');
