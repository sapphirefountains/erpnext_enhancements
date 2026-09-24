#!/usr/bin/env node
/**
 * Guards the capture recorder (`public/js/capture/recorder.js`, WI-079 slice 2) and the
 * launcher's gate.
 *
 * The recorder is imported FIRST in the Desk bundle and wraps fetch, XHR and console.error on
 * every Desk page. So most of what is asserted here is not "does it record" but "does the page
 * still behave exactly as it did": the wrapped fetch hands back the same Response and the same
 * rejection, a synchronous throw is still a synchronous throw, console.error still prints with
 * the same arguments, XHR methods return what they returned, and the page's own response body is
 * never read. None of those failures would show up in a review, and every one of them would
 * surface as some other feature's bug.
 *
 * How it runs without a browser: `recorder.js` installs itself only when a global `window`
 * exists, and exports `install(win)`, which reads everything off the window it is given. This
 * script imports it under node, where nothing installs, then hands `install` hand-built fake
 * windows. The fakes are small and explicit on purpose, so a failure points at one behaviour.
 *
 * If an export this depends on disappears, it exits 2 rather than passing vacuously.
 */

const path = require('path');
const { pathToFileURL } = require('url');

const JS = path.join(__dirname, '..', 'erpnext_enhancements', 'public', 'js', 'capture');

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

const flush = () => new Promise((resolve) => setImmediate(resolve));

/** A fake Response. `bodyReads` counts reads of THIS object, so a read of the page's own body shows. */
function fakeResponse(status, body, extra) {
	const res = {
		status,
		ok: status >= 200 && status < 300,
		type: 'basic',
		bodyReads: 0,
		headers: { get: (name) => (name === 'content-type' ? 'application/json' : null) },
		json() {
			res.bodyReads += 1;
			return Promise.resolve(body);
		},
		clone() {
			return { json: () => Promise.resolve(body) };
		},
	};
	return Object.assign(res, extra || {});
}

function fakeWindow(options) {
	const opts = options || {};
	const listeners = {};
	const head = [];
	const loc = {
		origin: 'https://erp.example.com',
		pathname: opts.path || '/feedback',
		search: opts.search || '',
	};
	let now = 0;
	const consoleCalls = [];

	// A fresh class per window: install() patches the prototype, and windows must not share one.
	class FakeXhr {
		constructor() {
			this.listeners = {};
			this.status = 0;
			this.responseType = '';
			this.responseText = '';
		}
		addEventListener(type, fn) {
			(this.listeners[type] = this.listeners[type] || []).push(fn);
		}
		fire(type) {
			for (const fn of this.listeners[type] || []) fn.call(this, {});
		}
	}
	FakeXhr.prototype.open = function (method, url) {
		this.opened = [method, url];
		return 'open-result';
	};
	FakeXhr.prototype.send = function (body) {
		this.sent = body;
		return 'send-result';
	};

	const move = (url) => {
		const u = new URL(url, loc.origin);
		loc.pathname = u.pathname;
		loc.search = u.search;
	};

	const win = {
		EE_CAPTURE: opts.boot,
		frappe: opts.frappe,
		location: loc,
		performance: { now: () => now },
		console: {
			error: function () {
				consoleCalls.push({ self: this, args: Array.from(arguments) });
			},
		},
		history: {
			pushState(state, title, url) {
				move(url);
				return 'push-result';
			},
			replaceState(state, title, url) {
				move(url);
				return 'replace-result';
			},
		},
		addEventListener(type, fn) {
			(listeners[type] = listeners[type] || []).push(fn);
		},
		document: {
			title: opts.title || 'Feedback | Sapphire',
			cookie: opts.cookie || '',
			documentElement: { getAttribute: () => 'dark' },
			head: {
				appendChild(el) {
					head.push(el);
					if (opts.onScript) opts.onScript(el, win);
				},
			},
			createElement: (tag) => ({ tagName: String(tag).toUpperCase() }),
		},
		navigator: { onLine: true, language: 'en-US', userAgent: 'TestAgent/1.0' },
		innerWidth: 390,
		innerHeight: 844,
		devicePixelRatio: 3,
		matchMedia: (query) => ({ matches: query.indexOf('standalone') !== -1 }),
		fetch: (input, init) => opts.fetchImpl(input, init),
		XMLHttpRequest: FakeXhr,
	};
	return {
		win,
		head,
		consoleCalls,
		setNow: (t) => {
			now = t;
		},
		dispatch(type, ev) {
			for (const fn of listeners[type] || []) fn(ev);
		},
	};
}

(async () => {
	let R;
	let P;
	let L;
	try {
		R = await import(pathToFileURL(path.join(JS, 'recorder.js')).href);
		P = await import(pathToFileURL(path.join(JS, 'page_context.js')).href);
		L = await import(pathToFileURL(path.join(JS, 'launcher.js')).href);
	} catch (err) {
		console.error('COULD NOT LOAD the capture modules under node. They must not touch the DOM at import.');
		console.error(err && err.stack);
		process.exit(2);
	}
	for (const [mod, names] of [
		[R, ['install', 'pushRing', 'pushConsole', 'pushRoute', 'classifyRequest', 'excTypeFrom', 'describe']],
		[P, ['detectPageContext', 'formFacts', 'listFacts', 'reportFacts']],
		[L, ['shouldShowLauncher', 'mountLauncher']],
	]) {
		for (const name of names) {
			if (typeof mod[name] !== 'function') {
				console.error(`MARKER NOT FOUND: ${name}() is no longer exported`);
				process.exit(2);
			}
		}
	}

	console.log('\nimporting under node installs nothing');
	check('no global ee_capture', typeof globalThis.ee_capture, 'undefined');
	check('limits are 20 / 20 / 10', R.LIMITS, { console: 20, requests: 20, routes: 10 });
	check('slow threshold is 3000 ms', R.SLOW_MS, 3000);

	console.log('\nring helpers');
	{
		const ring = [];
		for (let i = 1; i <= 25; i++) R.pushRing(ring, i, 20);
		check('capped at max', ring.length, 20);
		check('oldest dropped, oldest first', [ring[0], ring[19]], [6, 25]);
	}
	{
		const ring = [];
		R.pushConsole(ring, { at: 't1', level: 'error', message: 'boom' }, 20);
		R.pushConsole(ring, { at: 't2', level: 'error', message: 'boom' }, 20);
		R.pushConsole(ring, { at: 't3', level: 'error', message: 'boom' }, 20);
		check('consecutive repeats collapse', ring.length, 1);
		check('count increments', ring[0].count, 3);
		check('at moves to the latest repeat', ring[0].at, 't3');
		R.pushConsole(ring, { at: 't4', level: 'uncaught', message: 'boom' }, 20);
		check('same text at another level is a new entry', ring.length, 2);
		R.pushConsole(ring, { at: 't5', level: 'error', message: 'boom' }, 20);
		check('a repeat after something else is a new entry', [ring.length, ring[2].count], [3, 1]);
		for (let i = 0; i < 30; i++) R.pushConsole(ring, { at: 'x', level: 'error', message: 'm' + i }, 20);
		check('cap counts entries, not repeats', ring.length, 20);
		for (let i = 0; i < 500; i++) R.pushConsole(ring, { at: 'y', level: 'error', message: 'loop' }, 20);
		check('a loop cannot push the others out', [ring.length, ring[19].count, ring[18].message], [20, 500, 'm29']);
	}
	{
		const ring = [];
		R.pushRoute(ring, { at: 'a', path: '/desk' }, 10);
		R.pushRoute(ring, { at: 'b', path: '/desk' }, 10);
		check('a repeated route is skipped', ring.length, 1);
		for (let i = 0; i < 15; i++) R.pushRoute(ring, { at: 'c', path: '/desk/p' + i }, 10);
		check('routes capped at 10, newest kept', [ring.length, ring[9].path], [10, '/desk/p14']);
	}

	console.log('\nrequest classification');
	check('network error (0)', R.classifyRequest(0, 10), 'failed');
	check('missing status', R.classifyRequest(undefined, 10), 'failed');
	check('404', R.classifyRequest(404, 10), 'failed');
	check('500', R.classifyRequest(500, 10), 'failed');
	check('200 fast', R.classifyRequest(200, 2999), null);
	check('200 at 3000 ms is slow', R.classifyRequest(200, 3000), 'slow');
	check('304 fast', R.classifyRequest(304, 5), null);
	check('slow AND failed reports failed', R.classifyRequest(502, 9000), 'failed');

	console.log('\nexc_type, and nothing else, from a body');
	check('Frappe exc_type', R.excTypeFrom({ exc_type: 'ValidationError', exception: 'secret' }), 'ValidationError');
	check('dotted', R.excTypeFrom({ exc_type: 'frappe.exceptions.PermissionError' }), 'frappe.exceptions.PermissionError');
	check('markup refused', R.excTypeFrom({ exc_type: '<img src=x>' }), '');
	check('non-string refused', R.excTypeFrom({ exc_type: 12 }), '');
	check('absent', R.excTypeFrom({}), '');
	check('null body', R.excTypeFrom(null), '');

	console.log('\ndescribe keeps console arguments short');
	check('error', R.describe(new TypeError('bad')), 'TypeError: bad');
	check('function is named, not dumped', R.describe(function handler() { return 'secret source'; }), '[function handler]');
	check('object is shallow', R.describe({ a: 1, b: { deep: true }, c: 'x' }), '{a: 1, b: object, c: x}');
	check('array is shallow', R.describe([1, 'two', null]), '[1, two, null]');
	check('null', R.describe(null), 'null');

	console.log('\nfetch: recorded, and the page sees exactly what it would have');
	{
		let nextFetch = null;
		const f = fakeWindow({ boot: { surface: 'web', user: 'u@example.com', panel_url: '/assets/p.js' }, fetchImpl: () => nextFetch() });
		const api = R.install(f.win);
		check('installs window.ee_capture', f.win.ee_capture === api && api.installed === true, true);
		check('surface from EE_CAPTURE', api.config.surface, 'web');
		const wrapped = f.win.fetch;
		check('a second install is a no-op', R.install(f.win) === api && f.win.fetch === wrapped, true);

		const failedRes = fakeResponse(500, { exc_type: 'ValidationError', exception: 'Traceback: secret' });
		nextFetch = () => Promise.resolve(failedRes);
		const got = await f.win.fetch('/api/method/frappe.client.save?cmd=x&token=abc', { method: 'post' });
		await flush();
		check('the page gets the same Response object', got === failedRes, true);
		check("the page's body was not read by the recorder", failedRes.bodyReads, 0);
		const req = api.snapshot().requests[0];
		check('failed request recorded', [req.method, req.path, req.status, req.kind], ['POST', '/api/method/frappe.client.save', 500, 'failed']);
		check('exc_type pulled from a clone', req.exc_type, 'ValidationError');
		check('no body kept', JSON.stringify(api.snapshot()).indexOf('Traceback'), -1);

		const netErr = new TypeError('Failed to fetch');
		nextFetch = () => Promise.reject(netErr);
		let caught = null;
		try {
			await f.win.fetch('https://erp.example.com/api/method/ping');
		} catch (e) {
			caught = e;
		}
		check('the page gets the same rejection', caught === netErr, true);
		check('network error recorded as status 0', api.snapshot().requests[1].status, 0);

		const syncErr = new Error('sync');
		nextFetch = () => {
			throw syncErr;
		};
		caught = null;
		try {
			f.win.fetch('/x');
		} catch (e) {
			caught = e;
		}
		check('a synchronous throw is still synchronous and the same error', caught === syncErr, true);

		const before = api.snapshot().requests.length;
		nextFetch = () => Promise.resolve(fakeResponse(200, {}));
		await f.win.fetch('/api/method/fast');
		await flush();
		check('a fast 200 is not recorded', api.snapshot().requests.length, before);

		f.setNow(0);
		nextFetch = () =>
			new Promise((resolve) => {
				f.setNow(3500);
				resolve(fakeResponse(200, {}));
			});
		await f.win.fetch('/api/method/slow');
		await flush();
		const slow = api.snapshot().requests.pop();
		check('a slow 200 is recorded as slow', [slow.path, slow.status, slow.kind, slow.duration_ms], ['/api/method/slow', 200, 'slow', 3500]);

		f.setNow(0);
		const count = api.snapshot().requests.length;
		nextFetch = () => Promise.resolve(fakeResponse(0, null, { type: 'opaque' }));
		await f.win.fetch('https://cdn.example.net/pixel.gif');
		await flush();
		check('an opaque response is not a failure', api.snapshot().requests.length, count);

		const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
		nextFetch = () => Promise.reject(abort);
		await f.win.fetch('/api/method/typeahead').catch(() => {});
		check('a quick abort is not recorded', api.snapshot().requests.length, count);
		nextFetch = () =>
			new Promise((resolve, reject) => {
				f.setNow(46000);
				reject(abort);
			});
		await f.win.fetch('/api/method/hung').catch(() => {});
		const hung = api.snapshot().requests.pop();
		check('a slow abort (a timeout) is recorded', [hung.path, hung.status, hung.kind], ['/api/method/hung', 0, 'slow']);

		f.setNow(0);
		const n = api.snapshot().requests.length;
		nextFetch = () =>
			new Promise((resolve) => {
				f.setNow(25000);
				resolve(fakeResponse(200, {}));
			});
		await f.win.fetch('/socket.io/?EIO=4&transport=polling');
		await flush();
		check('socket.io long-polling is ignored', api.snapshot().requests.length, n);

		f.setNow(0);
		nextFetch = () => Promise.resolve(fakeResponse(503, {}));
		for (let i = 0; i < 25; i++) await f.win.fetch('/api/method/r' + i);
		await flush();
		const reqs = api.snapshot().requests;
		check('requests capped at 20, newest kept', [reqs.length, reqs[19].path], [20, '/api/method/r24']);
	}

	console.log('\nXHR: recorded, and methods return what they returned');
	{
		const f = fakeWindow({ boot: { surface: 'web' }, fetchImpl: () => Promise.resolve(fakeResponse(200, {})) });
		const api = R.install(f.win);
		const xhr = new f.win.XMLHttpRequest();
		check('open returns the original result', xhr.open('get', '/api/method/x?y=1'), 'open-result');
		check('send returns the original result', xhr.send('body'), 'send-result');
		check('the original ran', [xhr.opened, xhr.sent], [['get', '/api/method/x?y=1'], 'body']);
		xhr.status = 417;
		xhr.responseText = JSON.stringify({ exc_type: 'MandatoryError', _server_messages: 'secret' });
		xhr.fire('loadend');
		const rec = api.snapshot().requests[0];
		check('failed XHR recorded', [rec.method, rec.path, rec.status, rec.exc_type], ['GET', '/api/method/x', 417, 'MandatoryError']);

		xhr.open('POST', '/api/method/again');
		xhr.send();
		check('a reused XHR is hooked once', [xhr.listeners.loadend.length, xhr.listeners.abort.length], [1, 1]);
		xhr.status = 0;
		xhr.fire('abort');
		xhr.fire('loadend');
		check('an aborted XHR is not a failure', api.snapshot().requests.length, 1);

		const x2 = new f.win.XMLHttpRequest();
		x2.open('GET', '/api/method/down');
		x2.send();
		x2.status = 0;
		x2.fire('loadend');
		check('XHR network error is status 0', api.snapshot().requests[1].status, 0);
	}

	console.log('\nconsole and uncaught errors');
	{
		const f = fakeWindow({ boot: { surface: 'web' }, fetchImpl: () => Promise.resolve(fakeResponse(200, {})) });
		const api = R.install(f.win);
		const err = new TypeError('bad');
		f.win.console.error('Save failed for nik@example.com', err);
		check('console.error calls through with the same arguments', f.consoleCalls[0].args[1] === err && f.consoleCalls[0].args[0] === 'Save failed for nik@example.com', true);
		check('... and the same this', f.consoleCalls[0].self === f.win.console, true);
		let c = api.snapshot().console;
		check('recorded, scrubbed', [c[0].level, c[0].message], ['error', 'Save failed for [email] TypeError: bad']);
		f.win.console.error('same');
		f.win.console.error('same');
		f.win.console.error('same');
		c = api.snapshot().console;
		check('repeats collapse', [c.length, c[1].count], [2, 3]);

		f.dispatch('error', {
			target: f.win,
			message: 'Uncaught ReferenceError: x is not defined',
			filename: 'https://erp.example.com/assets/app.bundle.js?v=9',
			lineno: 12,
		});
		f.dispatch('error', { target: { tagName: 'SCRIPT', src: 'https://erp.example.com/assets/old.bundle.js?v=1' } });
		f.dispatch('error', { target: { tagName: 'IMG', src: '/files/x.png' } });
		f.dispatch('error', { target: { tagName: 'LINK', rel: 'icon', href: '/favicon.ico' } });
		f.dispatch('unhandledrejection', { reason: new Error('nope') });
		c = api.snapshot().console;
		check('uncaught error, with its script path', c[2], { at: c[2].at, level: 'uncaught', message: 'Uncaught ReferenceError: x is not defined (/assets/app.bundle.js:12)', count: 1 });
		check('a script that failed to load', c[3].message, 'Could not load script /assets/old.bundle.js');
		check('a broken image or icon is not an error worth a slot', c.length, 5);
		check('unhandled rejection', [c[4].level, c[4].message], ['rejection', 'Error: nope']);
	}

	console.log('\nroutes: path only, never the query string');
	{
		const f = fakeWindow({ boot: { surface: 'web' }, path: '/itinerary', search: '?token=s3cret', fetchImpl: () => null });
		const api = R.install(f.win);
		check('pushState returns the original result', f.win.history.pushState({}, '', '/itinerary/day?token=s3cret'), 'push-result');
		check('replaceState returns the original result', f.win.history.replaceState({}, '', '/itinerary/day?token=other'), 'replace-result');
		f.win.location.pathname = '/itinerary';
		f.dispatch('popstate', {});
		const routes = api.snapshot().routes.map((r) => r.path);
		check('initial, push, (replace deduped), popstate', routes, ['/itinerary', '/itinerary/day', '/itinerary']);
		for (let i = 0; i < 12; i++) f.win.history.pushState({}, '', '/itinerary/p' + i);
		check('routes capped at 10', api.snapshot().routes.length, 10);
	}

	console.log('\nsnapshot');
	{
		const f = fakeWindow({
			boot: { surface: 'kiosk', user: 'tech@example.com', panel_url: '/assets/p.js' },
			path: '/kiosk',
			search: '?token=s3cret',
			title: 'Kiosk',
			fetchImpl: () => null,
		});
		const api = R.install(f.win);
		api.registerCaptureState(() => ({ clock: 'in', queue: 2, sid: 'must-not-appear', nested: { sid: 'x', ok: 1 } }));
		api.registerCaptureState(() => {
			throw new Error('a broken registrant');
		});
		const stop = api.registerCaptureState(() => ({ queue: 5, last_sync: '2026-09-23T10:00:00Z' }));
		api.registerCaptureState(() => ['not', 'an', 'object']);
		const snap = api.snapshot();
		check('schema 1', snap.schema, 1);
		check('surface kiosk', snap.surface, 'kiosk');
		check('captured_at is ISO', /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(snap.captured_at), true);
		check('page on a web surface', snap.page, { path: '/kiosk', query: null, route: null, title: 'Kiosk', form: null, list: null, report: null });
		check('app merged in order; a broken registrant costs only itself', snap.app, { clock: 'in', queue: 5, nested: { ok: 1 }, last_sync: '2026-09-23T10:00:00Z' });
		check('no query string anywhere', JSON.stringify(snap).indexOf('s3cret'), -1);
		check('no "sid" key anywhere', /"sid"/.test(JSON.stringify(snap)), false);
		check('device facts', [snap.device.viewport, snap.device.pixel_ratio, snap.device.theme, snap.device.standalone, snap.device.locale, snap.device.online], [{ w: 390, h: 844 }, 3, 'dark', true, 'en-US', true]);
		snap.console.push('mutated');
		snap.app.clock = 'mutated';
		check('a snapshot is a copy, not a live reference', [api.snapshot().console.length, api.snapshot().app.clock], [0, 'in']);
		stop();
		check('an unregistered state stops contributing', api.snapshot().app.queue, 2);
	}

	console.log('\nopen() on a web page: script tag from panel_url');
	{
		const f = fakeWindow({
			boot: { surface: 'web', panel_url: '/assets/erpnext_enhancements/dist/js/capture_panel.bundle.ABC.js' },
			fetchImpl: () => null,
			onScript: (el, win) => {
				setImmediate(() => {
					win.ee_capture_panel = { open: (snap, opts) => ({ schema: snap.schema, opts }) };
					el.onload();
				});
			},
		});
		const api = R.install(f.win);
		const out = await api.open({ source: 'test' });
		check('panel opened with the snapshot and options', out, { schema: 1, opts: { source: 'test' } });
		check('script src is panel_url', f.head[0].src, '/assets/erpnext_enhancements/dist/js/capture_panel.bundle.ABC.js');
		await api.open({});
		check('a loaded panel is not loaded again', f.head.length, 1);
	}
	{
		let fail = true;
		const f = fakeWindow({
			boot: { surface: 'web', panel_url: '/assets/p.js' },
			fetchImpl: () => null,
			onScript: (el, win) => {
				setImmediate(() => {
					if (fail) return el.onerror();
					win.ee_capture_panel = { open: () => 'opened' };
					el.onload();
				});
			},
		});
		const api = R.install(f.win);
		let message = '';
		await api.open({}).catch((e) => {
			message = e.message;
		});
		check('a failed load rejects with a sentence', message, 'The report form could not be loaded.');
		fail = false;
		check('the next click tries again', await api.open({}), 'opened');
	}
	{
		const f = fakeWindow({ boot: { surface: 'web' }, fetchImpl: () => null });
		const api = R.install(f.win);
		let message = '';
		await api.open({}).catch((e) => {
			message = e.message;
		});
		check('no panel_url rejects rather than throwing', message, 'The report form is not available on this page.');
	}

	console.log('\nthe Desk: route, query, form facts and changed field names');
	{
		const formHooks = [];
		const routerHooks = [];
		const required = [];
		let route = ['Form', 'ToDo', 'TD-0001'];
		let dirty = false;
		const frappe = {
			boot: {},
			session: { user: 'nik@example.com' },
			get_route: () => route,
			ui: { form: { on: (doctype, event, fn) => formHooks.push([doctype, event, fn]) } },
			router: { on: (event, fn) => routerHooks.push([event, fn]) },
			require: (name) => {
				required.push(name);
				globalThis.window.ee_capture_panel = { open: () => 'desk-panel' };
				return Promise.resolve();
			},
		};
		const f = fakeWindow({ frappe, path: '/desk/todo/TD-0001', search: '?view=x', title: 'TD-0001 | ToDo', fetchImpl: () => null });
		// page_context.js reads the real globals (cur_frm, frappe), as the Triton widget always has.
		globalThis.window = f.win;
		globalThis.document = f.win.document;
		const api = R.install(f.win);
		check('surface desk, user from the session', [api.config.surface, api.config.user], ['desk', 'nik@example.com']);
		check('a web page with frappe on it is still a web page', R.install(fakeWindow({ frappe, path: '/itinerary', fetchImpl: () => null }).win).config.surface, 'web');
		check('one form refresh hook, on every doctype', formHooks.map((h) => [h[0], h[1]]), [['*', 'refresh']]);
		check('router change hooked', routerHooks.map((h) => h[0]), ['change']);

		const frm = {
			doctype: 'ToDo',
			docname: 'TD-0001',
			doc: { doctype: 'ToDo', name: 'TD-0001', docstatus: 0, status: 'Open', description: 'Fix pump', priority: null, modified: 't1', __unsaved: 0, items: [{ a: 1 }] },
			is_dirty: () => dirty,
			is_new: () => false,
		};
		// In a browser `window` IS the global object; under node the two are set separately.
		f.win.cur_frm = frm;
		globalThis.cur_frm = frm;
		formHooks[0][2](frm);
		frm.doc.status = 'Closed';
		frm.doc.priority = '';
		frm.doc.modified = 't2';
		frm.doc.items.push({ a: 2 });
		dirty = true;
		let snap = api.snapshot();
		check('route and query kept on the Desk', [snap.page.route, snap.page.query], [['Form', 'ToDo', 'TD-0001'], '?view=x']);
		check('form facts, changed field NAMES only', snap.page.form, {
			doctype: 'ToDo',
			name: 'TD-0001',
			docstatus: 0,
			unsaved: true,
			is_new: false,
			changed_fields: ['status'],
		});
		check('no field values leave', JSON.stringify(snap).indexOf('Closed'), -1);
		formHooks[0][2](frm);
		check('a refresh mid-edit keeps the baseline', api.snapshot().page.form.changed_fields, ['status']);

		route = ['Form', 'ToDo', 'TD-0002'];
		snap = api.snapshot();
		check('a stale cur_frm is not trusted', snap.page.form, { doctype: 'ToDo', name: 'TD-0002', docstatus: null, unsaved: null, is_new: null, changed_fields: [] });

		route = ['List', 'ToDo', 'List'];
		f.win.cur_list = { doctype: 'ToDo', get_filters_for_args: () => [['ToDo', 'owner', '=', 'nik@example.com'], ['ToDo', 'status', 'in', ['Open', 'Closed']]] };
		globalThis.cur_list = f.win.cur_list;
		snap = api.snapshot();
		check('list facts, filter values scrubbed', snap.page.list, {
			doctype: 'ToDo',
			view: 'List',
			filters: [['ToDo', 'owner', '=', '[email]'], ['ToDo', 'status', 'in', ['Open', 'Closed']]],
		});
		check('form is null on a list', snap.page.form, null);

		route = ['query-report', 'General Ledger'];
		frappe.query_report = { report_name: 'General Ledger', get_filter_values: () => ({ company: 'Sapphire', from_date: '2026-01-01', sid: 'x' }) };
		snap = api.snapshot();
		check('report facts', snap.page.report, { name: 'General Ledger', filters: { company: 'Sapphire', from_date: '2026-01-01' } });

		check('open() on the Desk goes through frappe.require', await api.open({}), 'desk-panel');
		check('by bundle name', required, ['capture_panel.bundle.js']);

		console.log('\ndetectPageContext keeps the Triton ref shape exactly');
		route = ['Form', 'ToDo', 'TD-0001'];
		check('document ref', P.detectPageContext(), { type: 'document', doctype: 'ToDo', name: 'TD-0001', title: 'ToDo: TD-0001', route: '#Form/ToDo/TD-0001', unsaved: true });
		dirty = false;
		check('clean document ref has no unsaved key', Object.keys(P.detectPageContext()), ['type', 'doctype', 'name', 'title', 'route']);
		route = ['List', 'ToDo', 'List'];
		check('list ref keeps raw filters', P.detectPageContext(), {
			type: 'list',
			doctype: 'ToDo',
			filters: [['ToDo', 'owner', '=', 'nik@example.com'], ['ToDo', 'status', 'in', ['Open', 'Closed']]],
			title: 'ToDo list',
			route: '#List/ToDo/List',
		});
		route = ['List', 'ToDo', 'Report'];
		check('report view ref', Object.keys(P.detectPageContext()), ['type', 'report_name', 'name', 'filters', 'title', 'route']);
		route = ['query-report', 'General Ledger'];
		check('query report ref', P.detectPageContext().filters.sid, 'x');
		route = ['workspace', 'Home'];
		check('page ref uses the title before " | "', P.detectPageContext(), { type: 'page', title: 'TD-0001', route: '#workspace/Home' });
		frappe.get_route_str = () => 'desk/home';
		check('get_route_str wins when present', P.detectPageContext().route, '#desk/home');
		route = [];
		check('no route, no ref', P.detectPageContext(), null);
		check('no key named dirty_fields in any ref', /dirty_fields/.test(JSON.stringify(P.detectPageContext())), false);

		delete globalThis.window;
		delete globalThis.document;
		delete globalThis.cur_list;
		delete globalThis.cur_frm;
		check('off the Desk it returns null instead of throwing', P.detectPageContext(), null);
	}

	console.log('\nthe launcher gate');
	const web = { surface: 'web', launcher: true };
	check('System User on a web page', L.shouldShowLauncher(web, 'system_user=yes', '/feedback'), true);
	check('cookie among others', L.shouldShowLauncher(web, 'a=1; system_user=yes; b=2', '/itinerary'), true);
	check('website user', L.shouldShowLauncher(web, 'system_user=no', '/feedback'), false);
	check('a lookalike cookie', L.shouldShowLauncher(web, 'not_system_user=yes', '/feedback'), false);
	check('no cookie', L.shouldShowLauncher(web, '', '/feedback'), false);
	check('template did not ask', L.shouldShowLauncher({ surface: 'web', launcher: false }, 'system_user=yes', '/feedback'), false);
	check('"true" the string is not true', L.shouldShowLauncher({ surface: 'web', launcher: 'true' }, 'system_user=yes', '/feedback'), false);
	check('never the kiosk surface', L.shouldShowLauncher({ surface: 'kiosk', launcher: true }, 'system_user=yes', '/kiosk'), false);
	check('never the /kiosk path, whatever the surface says', L.shouldShowLauncher(web, 'system_user=yes', '/kiosk'), false);
	check('no EE_CAPTURE at all', L.shouldShowLauncher(undefined, 'system_user=yes', '/feedback'), false);

	console.log('\nthe launcher button');
	{
		const byId = {};
		const make = (tag) => {
			const node = {
				tagName: tag.toUpperCase(),
				attrs: {},
				handlers: {},
				setAttribute(k, v) {
					this.attrs[k] = String(v);
				},
				removeAttribute(k) {
					delete this.attrs[k];
				},
				addEventListener(type, fn) {
					this.handlers[type] = fn;
				},
			};
			return node;
		};
		const appended = [];
		const addTo = (el) => {
			appended.push(el);
			if (el.id) byId[el.id] = el;
		};
		const opened = [];
		const win = {
			EE_CAPTURE: { surface: 'web', launcher: true },
			location: { pathname: '/feedback' },
			ee_capture: { open: (opts) => (opened.push(opts), Promise.resolve()) },
			document: {
				cookie: 'system_user=yes',
				body: { appendChild: addTo },
				head: { appendChild: addTo },
				getElementById: (id) => byId[id] || null,
				createElement: make,
			},
		};
		check('mounts', L.mountLauncher(win), true);
		const button = appended.find((n) => n.tagName === 'BUTTON');
		check('a real, labelled button', [button.type, button.textContent, button.id], ['button', 'Report a problem', 'ee-cap-launcher']);
		L.mountLauncher(win);
		check('mounted once', appended.filter((n) => n.tagName === 'BUTTON').length, 1);
		button.handlers.click();
		await flush();
		check('click opens the capture panel', opened, [{ source: 'launcher' }]);
		check('busy state cleared afterwards', button.attrs['aria-busy'], undefined);
		check('a broken document does not throw', L.mountLauncher({ document: null }), false);
		const bare = Object.assign({}, win, { ee_capture: undefined, document: Object.assign({}, win.document, { getElementById: () => null }) });
		check('no recorder, no button', L.mountLauncher(bare), false);
	}

	console.log('');
	if (failures) {
		console.error(failures + ' assertion(s) failed');
		process.exit(1);
	}
	console.log('capture recorder: all assertions passed');
})();
