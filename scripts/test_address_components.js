#!/usr/bin/env node
/**
 * Guards the Google -> Frappe address component mapping.
 *
 * Why this exists: the mapping decides what lands in a saved Address when
 * somebody picks a suggestion, and every one of its rules is a judgement call
 * that looks arbitrary until it is wrong — `street_number` and `route` are two
 * components but one field; a city is `locality` in the US, `postal_town` in
 * the UK and `sublocality` where neither exists; the state field wants "CA",
 * not "California", but only the US reliably ships that short form; and the
 * ZIP must not silently become ZIP+4. A wrong rule here writes a plausible
 * address that is quietly not the one the user picked.
 *
 * It also covers what production cannot: the live site is US-only, so the
 * UK/Nordic and no-locality branches have no real record to exercise them.
 *
 * How it loads the code: address_autocomplete.js is a desk IIFE — it calls
 * frappe.provide() at load, so it cannot simply be `require`d under node. This
 * extracts the pure-mapping region between the file's own banner comments and
 * evaluates that. If those banners are renamed or the helpers move, the script
 * exits 2 with MARKERS NOT FOUND rather than silently asserting nothing — a
 * loud failure is the point, since a test that quietly stops testing is worse
 * than none (the QuickBooks suite ran nowhere for weeks; see CLAUDE.md).
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
	'address_autocomplete.js'
);

const START = '// -------------------------------------------------------- component mapping';
const END = '// ------------------------------------------------------------------- widget';

function loadHelpers() {
	const src = fs.readFileSync(TARGET, 'utf8');
	const start = src.indexOf(START);
	const end = src.indexOf(END);
	if (start < 0 || end < 0) {
		console.error(
			'MARKERS NOT FOUND in ' + TARGET + '\n' +
			'This script extracts the pure component-mapping helpers between two banner\n' +
			'comments. If you moved or renamed them, update START/END here — do not\n' +
			'delete the test.'
		);
		process.exit(2);
	}
	// The region is deliberately pure: no frappe, no DOM, nothing to stub.
	return eval(
		'(function () {' +
			src.slice(start, end) +
			'\n return { index_components, map_address_components, street_line }; })()'
	);
}

const m = loadHelpers();
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

/** Terse fixture builder: comp('CA', 'California', 'administrative_area_level_1') */
function comp(shortText, longText, ...types) {
	return { shortText: shortText, longText: longText, types: types };
}

// --------------------------------------------------------------- a US address

console.log('US street address (1600 Amphitheatre Pkwy)');
{
	const mapped = m.map_address_components([
		comp('1600', '1600', 'street_number'),
		comp('Amphitheatre Pkwy', 'Amphitheatre Parkway', 'route'),
		comp('Mountain View', 'Mountain View', 'locality', 'political'),
		comp('Santa Clara County', 'Santa Clara County', 'administrative_area_level_2'),
		comp('CA', 'California', 'administrative_area_level_1', 'political'),
		comp('US', 'United States', 'country', 'political'),
		comp('94043', '94043', 'postal_code'),
		comp('1351', '1351', 'postal_code_suffix'),
	]);
	// street_number + route join into one line, with the LONG route name.
	check('address_line1', mapped.address_line1, '1600 Amphitheatre Parkway');
	check('address_line2', mapped.address_line2, '');
	check('city', mapped.city, 'Mountain View');
	// Short form for the state — the field holds "CA", not "California".
	check('state', mapped.state, 'CA');
	// The bare ZIP: postal_code_suffix is a separate component and must not
	// be joined on, or every US address saves as ZIP+4.
	check('pincode', mapped.pincode, '94043');
	check('country_code', mapped.country_code, 'us');
}

console.log('US address with a unit (subpremise -> line 2)');
{
	const mapped = m.map_address_components([
		comp('500', '500', 'street_number'),
		comp('W Main St', 'West Main Street', 'route'),
		comp('Apt 4B', 'Apt 4B', 'subpremise'),
		comp('Mesa', 'Mesa', 'locality'),
		comp('AZ', 'Arizona', 'administrative_area_level_1'),
		comp('US', 'United States', 'country'),
		comp('85201', '85201', 'postal_code'),
	]);
	check('address_line1', mapped.address_line1, '500 West Main Street');
	check('address_line2', mapped.address_line2, 'Apt 4B');
}

// ------------------------------------------------------------ partial results

console.log('Rural address with no street number');
{
	const mapped = m.map_address_components([
		comp('Old Mill Rd', 'Old Mill Road', 'route'),
		comp('Snowflake', 'Snowflake', 'locality'),
		comp('AZ', 'Arizona', 'administrative_area_level_1'),
		comp('US', 'United States', 'country'),
	]);
	// No stray leading space, and no ZIP invented. The caller drops empty
	// values rather than blanking what the user already typed.
	check('address_line1', mapped.address_line1, 'Old Mill Road');
	check('pincode', mapped.pincode, '');
}

console.log('Empty / missing components');
{
	check('empty array', m.map_address_components([]), {
		address_line1: '',
		address_line2: '',
		city: '',
		state: '',
		pincode: '',
		country_code: '',
	});
	check('undefined', m.map_address_components(undefined).address_line1, '');
	check('component with no types', m.map_address_components([{ longText: 'x' }]).city, '');
}

// ------------------------------------------------------------ the city ladder

console.log('City falls back locality -> postal_town -> sublocality');
{
	check(
		'locality wins over both',
		m.map_address_components([
			comp('Camden Town', 'Camden Town', 'sublocality'),
			comp('London', 'London', 'postal_town'),
			comp('Real Locality', 'Real Locality', 'locality'),
		]).city,
		'Real Locality'
	);
	// UK/Nordic mailing towns: there is no locality at all.
	check(
		'postal_town when there is no locality',
		m.map_address_components([
			comp('Camden Town', 'Camden Town', 'sublocality'),
			comp('London', 'London', 'postal_town'),
		]).city,
		'London'
	);
	check(
		'sublocality is the last resort',
		m.map_address_components([comp('Kowloon', 'Kowloon', 'sublocality')]).city,
		'Kowloon'
	);
}

// ------------------------------------------------------- non-US state / country

console.log('State falls back to the long name outside the US');
{
	// Only the US reliably has a two-letter administrative_area_level_1. Writing
	// nothing would be worse than writing the full name.
	check(
		'no short form',
		m.map_address_components([
			comp('', 'Nordrhein-Westfalen', 'administrative_area_level_1'),
		]).state,
		'Nordrhein-Westfalen'
	);
	check(
		'short form present',
		m.map_address_components([comp('ON', 'Ontario', 'administrative_area_level_1')]).state,
		'ON'
	);
}

console.log('Country code is lowercased to match Country.code');
{
	// Frappe's Country.code is lowercase ISO alpha-2 ("us", "gb"); the lookup
	// that turns this into a Country docname is an exact match on it.
	check(
		'GB',
		m.map_address_components([comp('GB', 'United Kingdom', 'country')]).country_code,
		'gb'
	);
	check(
		'missing short form',
		m.map_address_components([comp('', 'Somewhere', 'country')]).country_code,
		''
	);
}

// ------------------------------------------------------------------- indexing

console.log('index_components');
{
	// A component carrying several types is reachable under each of them.
	const parts = m.index_components([
		comp('CA', 'California', 'administrative_area_level_1', 'political'),
	]);
	check('indexed by first type', parts.administrative_area_level_1, {
		long: 'California',
		short: 'CA',
	});
	check('indexed by second type', parts.political, { long: 'California', short: 'CA' });
}

// ------------------------------------------------ street number/name ordering

console.log('Street line follows the local number/name order');
{
	// The US/UK form, and the default when nothing says otherwise.
	check(
		'number first (US)',
		m.street_line('1600', 'Amphitheatre Parkway', '1600 Amphitheatre Pkwy, Mountain View, CA'),
		'1600 Amphitheatre Parkway'
	);
	// Continental Europe puts the number last. Read back off the formatted
	// address rather than a hardcoded country list.
	check(
		'number last (DE)',
		m.street_line('12', 'Hauptstrasse', 'Hauptstrasse 12, 10827 Berlin, Germany'),
		'Hauptstrasse 12'
	);
	check(
		'number last (FR)',
		m.street_line('10', 'Rue de Rivoli', '10 Rue de Rivoli, 75004 Paris, France'),
		'10 Rue de Rivoli'
	);
	// The formatted line abbreviates the route ("Pkwy"), so the route is not
	// found in it — must fall back to number-first rather than guess.
	check(
		'abbreviated route falls back to number first',
		m.street_line('1600', 'Amphitheatre Parkway', '1600 Amphitheatre Pkwy, Mountain View'),
		'1600 Amphitheatre Parkway'
	);
	check('no formatted address at all', m.street_line('12', 'Hauptstrasse', ''), '12 Hauptstrasse');
	check(
		'route only',
		m.street_line('', 'Old Mill Road', 'Old Mill Road, Snowflake'),
		'Old Mill Road'
	);
	check('number only', m.street_line('12', '', '12, Somewhere'), '12');
	check('neither', m.street_line('', '', 'Somewhere'), '');
}

console.log('Ordering reaches map_address_components');
{
	const mapped = m.map_address_components(
		[
			comp('12', '12', 'street_number'),
			comp('Hauptstrasse', 'Hauptstrasse', 'route'),
			comp('Berlin', 'Berlin', 'locality'),
			comp('DE', 'Germany', 'country'),
		],
		'Hauptstrasse 12, 10827 Berlin, Germany'
	);
	check('address_line1', mapped.address_line1, 'Hauptstrasse 12');
	check('country_code', mapped.country_code, 'de');
}

console.log('');
if (failures) {
	console.error(failures + ' assertion(s) failed');
	process.exit(1);
}
console.log('address component mapping: all assertions passed');
