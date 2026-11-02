#!/usr/bin/env node
/**
 * Drives the real address_autocomplete.js widget from a typed query to a
 * picked suggestion, against a fake DOM and a fake Places library, and checks
 * what each kind of caller is handed (v1.774.0).
 *
 * Why: the widget gained a second way to report a pick. The Address form, the
 * quick-entry dialog and Plan a Trip take `on_pick(values, meta)` — Frappe
 * Address fields, the country resolved through a Country lookup. The Fountain
 * Designer's Site step has no Address fields and takes `on_place(details)`:
 * the formatted address, the point and the state code it puts the design in.
 * Both paths run inside the widget's pick handler, which no pure-region test
 * (scripts/test_address_components.js) can reach, and a regression in either
 * is silent — the suggestion list still shows, the pick just fills nothing.
 *
 * Plain node, no npm install: the file is evaluated in a vm context whose
 * globals are the stubs below.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const TARGET = path.join(
	__dirname,
	'..',
	'erpnext_enhancements',
	'public',
	'js',
	'global_enhancements',
	'address_autocomplete.js'
);

let failures = 0;

function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log('  ok   ' + label);
	} else {
		failures += 1;
		console.error('  FAIL ' + label + '\n         expected ' + e + '\n         actual   ' + a);
	}
}

// ------------------------------------------------------------------ fake DOM

function makeElement(tag) {
	const el = {
		tagName: String(tag).toUpperCase(),
		attrs: {},
		children: [],
		listeners: {},
		style: {},
		hidden: false,
		className: '',
		id: '',
		value: '',
		textContent: '',
		parentNode: null,
		offsetHeight: 100,
		setAttribute(k, v) { this.attrs[k] = String(v); },
		getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
		removeAttribute(k) { delete this.attrs[k]; },
		appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
		removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; },
		addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
		removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); },
		dispatch(type, event) { (this.listeners[type] || []).slice().forEach((fn) => fn(event || {})); },
		contains(other) { return other === this || this.children.some((c) => c.contains && c.contains(other)); },
		querySelectorAll(selector) {
			const cls = selector.replace(/^\./, '');
			const found = [];
			(function walk(node) {
				node.children.forEach((c) => {
					if (String(c.className).split(' ').includes(cls)) found.push(c);
					walk(c);
				});
			})(this);
			return found;
		},
		getBoundingClientRect() { return { left: 0, top: 0, bottom: 30, width: 300 }; },
		scrollIntoView() {},
	};
	Object.defineProperty(el, 'innerHTML', {
		set() { this.children = []; },
		get() { return ''; },
	});
	return el;
}

function setup(countryNames) {
	const body = makeElement('body');
	const document = {
		body,
		activeElement: null,
		createElement: makeElement,
		addEventListener() {},
		removeEventListener() {},
	};
	const lookups = [];
	const places = {
		AutocompleteSessionToken: function () {},
		AutocompleteSuggestion: {
			fetchAutocompleteSuggestions(request) {
				lookups.push(request.input);
				return Promise.resolve({
					suggestions: [
						{
							placePrediction: {
								text: { text: '451 S State St, Salt Lake City, UT, USA' },
								mainText: { text: '451 S State St' },
								toPlace() {
									return {
										id: 'place-1',
										formattedAddress: '451 S State St, Salt Lake City, UT 84111, USA',
										// a LatLng: methods, not numbers
										location: { lat: () => 40.7608, lng: () => -111.8910 },
										addressComponents: [
											{ shortText: '451', longText: '451', types: ['street_number'] },
											{ shortText: 'S State St', longText: 'South State Street', types: ['route'] },
											{ shortText: 'Salt Lake City', longText: 'Salt Lake City', types: ['locality'] },
											{ shortText: 'UT', longText: 'Utah', types: ['administrative_area_level_1'] },
											{ shortText: 'US', longText: 'United States', types: ['country'] },
											{ shortText: '84111', longText: '84111', types: ['postal_code'] },
										],
										fetchFields() { return Promise.resolve(); },
									};
								},
							},
						},
					],
				});
			},
		},
	};
	const window = {
		innerHeight: 800,
		console: { warn() {} },
		setTimeout: (fn, ms) => setTimeout(fn, ms),
		clearTimeout: (id) => clearTimeout(id),
		addEventListener() {},
		removeEventListener() {},
		EEGoogleMaps: { load: () => Promise.resolve() },
		google: { maps: { importLibrary: () => Promise.resolve(places) } },
	};
	const frappe = {
		provide(name) {
			let node = sandbox;
			name.split('.').forEach((part) => { node = node[part] = node[part] || {}; });
		},
		xcall: () => Promise.resolve('BROWSER-KEY'),
		db: {
			get_value(doctype, filters) {
				const code = filters && filters.code;
				return Promise.resolve({ message: { name: countryNames[code] || '' } });
			},
		},
		show_alert() {},
	};
	const sandbox = { window, document, frappe, console, __: (s) => s };
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(TARGET, 'utf8'), sandbox, { filename: TARGET });
	return { api: sandbox.erpnext_enhancements.address_autocomplete, document, lookups };
}

const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms || 0));

/** Attach, type, wait out the debounce, then pick the first suggestion with the keyboard. */
async function typeAndPick(env, options) {
	const input = makeElement('input');
	const ctl = env.api.attach(input, options);
	await tick(); // the key, the loader and importLibrary resolve
	env.document.activeElement = input;
	input.value = '451 S State';
	input.dispatch('input');
	await tick(300); // past the 250 ms debounce, and the suggestions' promise
	input.dispatch('keydown', { key: 'ArrowDown', preventDefault() {} });
	input.dispatch('keydown', { key: 'Enter', preventDefault() {} });
	await tick(10); // fetchFields, apply, the Country lookup
	return { input, ctl };
}

(async () => {
	console.log('on_place: the Fountain Designer gets the address, the point and the state');
	{
		const env = setup({ us: 'United States' });
		const seen = [];
		await typeAndPick(env, { on_place: (d) => seen.push(d) });
		check('looked the query up once', env.lookups, ['451 S State']);
		check('called once', seen.length, 1);
		check('details', seen[0], {
			address: '451 S State St, Salt Lake City, UT 84111, USA',
			lat: 40.7608,
			lng: -111.891,
			state: 'UT',
			country_code: 'US',
			place_id: 'place-1',
			label: '451 S State St, Salt Lake City, UT, USA',
		});
	}

	console.log('on_pick still fills the Address fields (Address form, quick entry, Plan a Trip)');
	{
		const env = setup({ us: 'United States' });
		const seen = [];
		await typeAndPick(env, { on_pick: (values, meta) => seen.push({ values, meta }) });
		check('called once', seen.length, 1);
		check('address_line1', seen[0].values.address_line1, '451 South State Street');
		check('state', seen[0].values.state, 'UT');
		check('country through the Country lookup', seen[0].values.country, 'United States');
		check('meta point', [seen[0].meta.latitude, seen[0].meta.longitude], [40.7608, -111.891]);
	}

	console.log('Both: on_place first, then on_pick');
	{
		const env = setup({ us: 'United States' });
		const order = [];
		await typeAndPick(env, { on_place: () => order.push('place'), on_pick: () => order.push('pick') });
		check('order', order, ['place', 'pick']);
	}

	console.log('A destroyed widget reports nothing');
	{
		const env = setup({ us: 'United States' });
		const seen = [];
		const input = makeElement('input');
		const ctl = env.api.attach(input, { on_place: (d) => seen.push(d) });
		await tick();
		env.document.activeElement = input;
		input.value = '451 S State';
		input.dispatch('input');
		await tick(300);
		input.dispatch('keydown', { key: 'ArrowDown', preventDefault() {} });
		ctl.destroy();
		input.dispatch('keydown', { key: 'Enter', preventDefault() {} });
		await tick(10);
		check('nothing reported', seen.length, 0);
	}

	console.log('place_details: the pure mapping');
	{
		const env = setup({});
		const d = env.api.place_details(
			[
				{ shortText: 'Ontario', longText: 'Ontario', types: ['administrative_area_level_1'] },
				{ shortText: 'CA', longText: 'Canada', types: ['country'] },
			],
			'Toronto, ON, Canada',
			43.65,
			-79.38
		);
		check('a non-US region keeps its own code, upper-cased', [d.state, d.country_code], ['ONTARIO', 'CA']);
		check('no state at all is blank, never guessed', env.api.place_details([], '', null, null).state, '');
		check('no point is null, not 0', [env.api.place_details([], 'x').lat, env.api.place_details([], 'x').lng], [null, null]);
		check('a long name only is blank', env.api.place_details([{ longText: 'Utah', types: ['administrative_area_level_1'] }], '').state, '');
	}

	console.log('');
	if (failures) {
		console.error(failures + ' assertion(s) failed');
		process.exit(1);
	}
	console.log('address autocomplete picks: all assertions passed');
})().catch((err) => {
	console.error(err);
	process.exit(1);
});
