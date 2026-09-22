#!/usr/bin/env node
/**
 * Customer/Supplier quick entry (party_quick_entry.js), executed against a stubbed
 * frappe rather than grepped. Every behaviour below fails SILENTLY in production:
 *
 *   1. The dialog asks only for fields the saved form shows. ERPNext's stock dialog
 *      collected an address and a primary contact into dialog-only keys, and every
 *      field that would display them is hidden on this site — what was typed looked
 *      like it vanished. A field that quietly comes back (an `address_line1`, the
 *      Supplier's `tax_id` on its hidden Tax tab) is that bug again.
 *   2. Industry is shown and required under the server's rule, and ONLY that rule.
 *      Missing, a commercial account fails on Save with nowhere to put an industry;
 *      required too broadly, a homeowner is asked for an industry they do not have.
 *      The type list must be the server's (data_quality.COMMERCIAL_TYPES), because
 *      two copies drift.
 *   3. A saved record opens its full form — including from the doctype's own list,
 *      where stock quick entry deliberately stays put — and a link-field create
 *      does NOT navigate away from the form the user was filling in.
 *   4. The toggle. Off, every method must be ERPNext's, or turning the feature off
 *      leaves a half-custom dialog nobody can reason about.
 *   5. The banner appears once, on a record just created with no address, and goes
 *      away when an address exists. Shown on every address-less record it would sit
 *      on ~70% of Customers and ~97% of Suppliers.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');

function read(...parts) {
	const file = path.join(ROOT, ...parts);
	if (!fs.existsSync(file)) {
		console.error(
			'MARKERS NOT FOUND: ' +
				path.relative(process.cwd(), file) +
				' does not exist. The feature was moved or removed — re-derive these checks ' +
				'deliberately rather than deleting them.'
		);
		process.exit(2);
	}
	return fs.readFileSync(file, 'utf8');
}

const SOURCE = read('erpnext_enhancements', 'public', 'js', 'global_enhancements', 'party_quick_entry.js');
const JS_ENTRY = read('erpnext_enhancements', 'public', 'js', 'erpnext_enhancements.bundle.js');
const BOOT = read('erpnext_enhancements', 'boot.py');
const DATA_QUALITY = read('erpnext_enhancements', 'crm_enhancements', 'data_quality.py');
const DIRECTORY = read('erpnext_enhancements', 'public', 'js', 'global_enhancements', 'unified_tab_controller.js');
const HOOKS = read('erpnext_enhancements', 'hooks.py');
const CUSTOM_FIELDS = JSON.parse(read('erpnext_enhancements', 'fixtures', 'custom_field.json'));

let failures = 0;

function fail(message) {
	failures += 1;
	console.error('  FAIL  ' + message);
}

function pass(message) {
	console.log('  ok    ' + message);
}

function check(condition, ok, bad) {
	if (condition) pass(ok);
	else fail(bad);
}

function same(a, b) {
	return JSON.stringify(a) === JSON.stringify(b);
}

// --- the server's commercial types, parsed from the source of truth -----------

const typesMatch = DATA_QUALITY.match(/^COMMERCIAL_TYPES\s*=\s*\(([^)]*)\)/m);
if (!typesMatch) {
	console.error('MARKERS NOT FOUND: COMMERCIAL_TYPES tuple in crm_enhancements/data_quality.py.');
	process.exit(2);
}
const SERVER_TYPES = [...typesMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);

// --- a stubbed desk -------------------------------------------------------------

// Every field the stubbed meta knows. Includes the stock dialog's address/contact
// keys and tax_id on purpose: a field missing from here would make its absence
// from the dialog meaningless.
const META = {
	Customer: [
		'customer_name', 'customer_type', 'industry', 'territory', 'customer_group',
		'custom_account_status', 'custom_value_stream', 'custom_accounts_phone_number',
		'custom_accounts_email_address', 'email_id', 'mobile_no', 'first_name', 'last_name',
		'customer_primary_address', 'custom_billing_address',
	],
	Supplier: [
		'supplier_name', 'supplier_type', 'supplier_group', 'country', 'tax_id',
		'custom_phone_number', 'custom_email', 'email_id', 'mobile_no',
	],
};

const FORBIDDEN = [
	'address_line1', 'address_line2', 'city', 'state', 'pincode', 'country_address',
	'email_address', 'mobile_number', 'map_to_first_name', 'map_to_last_name',
	'email_id', 'mobile_no', 'first_name', 'last_name', 'tax_id',
];

function makeDesk({ contactsUx = 1, industryRule, withStock = true } = {}) {
	const log = [];
	const handlers = {};
	const context = {};

	class QuickEntryForm {
		constructor(doctype, after_insert, init_callback, doc, force, skip_insert) {
			this.doctype = doctype;
			this.after_insert = after_insert;
			this.doc = doc || { doctype: doctype, name: 'new-' + doctype.toLowerCase() + '-1', __islocal: 1 };
			this.force = force || false;
			this.skip_insert = skip_insert || false;
			this.values = {};
		}
		is_quick_entry() {
			log.push('plain.is_quick_entry');
			return 'plain';
		}
		render_dialog() {
			log.push({ plain_render: (this.docfields || []).map((df) => df.fieldname || df.fieldtype) });
		}
		insert() {
			log.push('plain.insert');
			return this.update_doc();
		}
		update_doc() {
			Object.assign(this.doc, this.values);
			return this.doc;
		}
		// Stock v16 order: calling link first, then the caller's callback, else
		// open the form unless the user is on the list.
		process_after_insert(r) {
			this.doc = r.message;
			if (context.frappe._from_link) {
				log.push('update_calling_link');
				delete context.frappe._from_link;
			} else if (this.after_insert) {
				this.after_insert(this.doc);
			} else {
				log.push('open_form_if_not_list');
			}
		}
	}

	// erpnext's ContactAddressQuickEntryForm, reduced to what distinguishes it.
	class ErpnextStock extends QuickEntryForm {
		constructor(doctype, after_insert, init_callback, doc, force) {
			super(doctype, after_insert, init_callback, doc, force);
			this.skip_redirect_on_error = true;
		}
		render_dialog() {
			log.push('stock.render_dialog');
		}
		insert() {
			log.push('stock.insert');
		}
	}

	const frappe = {
		boot: {
			ee_contacts_ux: contactsUx,
			ee_industry_rule: industryRule,
		},
		ui: {
			form: {
				QuickEntryForm,
				on(doctype, h) {
					(handlers[doctype] = handlers[doctype] || []).push(h);
				},
			},
		},
		meta: {
			get_docfield(doctype, fieldname) {
				if (!(META[doctype] || []).includes(fieldname)) return null;
				return { fieldname, fieldtype: 'Data', label: fieldname, parent: doctype };
			},
		},
		utils: {
			escape_html: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
			get_form_link(doctype, name, html, text, query) {
				let route = '/desk/' + doctype.toLowerCase().replace(/ /g, '-') + '/' + encodeURIComponent(name);
				if (query) route += '?' + Object.entries(query).map(([k, v]) => k + '=' + v).join('&');
				return route;
			},
		},
		provide(ns) {
			let target = context;
			ns.split('.').forEach((part) => {
				target[part] = target[part] || {};
				target = target[part];
			});
		},
		set_route(...args) {
			log.push({ route: args });
		},
		show_alert(opts, seconds) {
			log.push({ alert: true, message: opts.message, seconds });
		},
	};
	if (withStock) {
		frappe.ui.form.CustomerQuickEntryForm = ErpnextStock;
		frappe.ui.form.SupplierQuickEntryForm = ErpnextStock;
	}

	const $ = () => ({ one() {} });
	$.contains = (_doc, el) => !!(el && el.attached);

	Object.assign(context, {
		frappe,
		$,
		document: {},
		cint: (v) => parseInt(v, 10) || 0,
		__: (s, args) => (args ? s.replace(/\{(\d+)\}/g, (_m, i) => args[i]) : s),
		setTimeout: (fn) => fn(),
	});
	vm.createContext(context);
	vm.runInContext(SOURCE, context, { filename: 'party_quick_entry.js' });

	return { context, frappe, log, handlers, ErpnextStock, QuickEntryForm };
}

function makeForm(doctype, { name = 'ACME', isNew = false, addresses = [] } = {}) {
	const messages = [];
	const frm = {
		doctype,
		doc: { doctype, name, __onload: { addr_list: addresses } },
		fields_dict: { address_list_html: {} },
		is_new: () => isNew,
		scrolled: null,
		scroll_to_field(fieldname) {
			frm.scrolled = fieldname;
			return true;
		},
		layout: {
			show_message(html, color) {
				const el = { attached: true };
				const clicks = {};
				const $msg = {
					0: el,
					html,
					color,
					find: (selector) => ({
						on: (_event, fn) => {
							clicks[selector] = fn;
						},
					}),
					remove() {
						el.attached = false;
					},
					click: (selector) => clicks[selector](),
				};
				messages.push($msg);
				return $msg;
			},
		},
	};
	return { frm, messages };
}

function fire(handlers, doctype, event, frm) {
	(handlers[doctype] || []).forEach((h) => h[event] && h[event](frm));
}

function evalDepends(expr, doc) {
	if (!expr) return false;
	return Function('doc', 'return (' + expr.replace(/^eval:/, '') + ');')(doc);
}

const RULE = { required: 1, types: SERVER_TYPES };

console.log('party quick entry — dialog fields, industry rule, post-save routing, banner\n');

// --- 1. registration -------------------------------------------------------------

{
	const { frappe, ErpnextStock } = makeDesk({ industryRule: RULE });
	for (const dt of ['Customer', 'Supplier']) {
		const cls = frappe.ui.form[dt + 'QuickEntryForm'];
		check(
			cls && cls !== ErpnextStock && cls.prototype instanceof ErpnextStock,
			dt + 'QuickEntryForm is ours and extends erpnext\'s stock class',
			dt + 'QuickEntryForm is not a subclass of erpnext\'s stock class — the toggle-off path cannot be stock'
		);
	}
}

// --- 2. the dialog fields ---------------------------------------------------------

const EXPECTED = {
	Customer: [
		'customer_name', 'customer_type', 'industry', 'Section Break', 'custom_account_status',
		'territory', 'custom_value_stream', 'Section Break', 'custom_accounts_phone_number',
		'custom_accounts_email_address',
	],
	Supplier: [
		'supplier_name', 'supplier_type', 'supplier_group', 'country', 'Section Break',
		'custom_phone_number', 'custom_email',
	],
};

for (const dt of ['Customer', 'Supplier']) {
	const { frappe, log } = makeDesk({ industryRule: RULE });
	const qe = new frappe.ui.form[dt + 'QuickEntryForm'](dt);
	qe.render_dialog();
	const rendered = log.find((e) => e.plain_render);
	check(
		!log.includes('stock.render_dialog') && rendered,
		dt + ': toggle on renders through QuickEntryForm, not erpnext\'s render_dialog',
		dt + ': erpnext\'s render_dialog ran — it appends the dialog-only address and contact sections'
	);
	const names = rendered ? rendered.plain_render : [];
	check(
		same(names, EXPECTED[dt]),
		dt + ': dialog fields are ' + EXPECTED[dt].filter((n) => n !== 'Section Break').join(', '),
		dt + ': dialog fields changed: ' + JSON.stringify(names)
	);
	const leaked = names.filter((n) => FORBIDDEN.includes(n));
	check(
		leaked.length === 0,
		dt + ': no field the saved form hides (address, stock contact, tax_id)',
		dt + ': dialog collects fields the saved form never shows: ' + leaked.join(', ')
	);
	check(qe.is_quick_entry() === true, dt + ': is_quick_entry is true with the toggle on', dt + ': is_quick_entry deferred to meta');
	check(
		qe.title === (dt === 'Customer' ? 'New Account' : 'New Supplier'),
		dt + ': dialog is titled "' + qe.title + '" (not __(doctype), which this site renders "Accounts")',
		dt + ': dialog title is ' + JSON.stringify(qe.title)
	);
	check(qe.skip_redirect_on_error === true, dt + ': a server error keeps the dialog open', dt + ': a server error would dump the user into the full form');
}

// Every custom field the dialog names must exist, or clone_meta_field drops it without a word.
for (const [dt, names] of Object.entries(EXPECTED)) {
	const custom = names.filter((n) => n.startsWith('custom_'));
	const missing = custom.filter((n) => !CUSTOM_FIELDS.some((r) => r.dt === dt && r.fieldname === n));
	check(
		missing.length === 0,
		dt + ': every custom_* dialog field is a fixture Custom Field',
		dt + ': dialog names custom fields that are not in fixtures/custom_field.json: ' + missing.join(', ')
	);
}

// --- 3. the industry rule -----------------------------------------------------------

{
	const jsFallback = SOURCE.match(/types:\s*rule\.types\s*\|\|\s*(\[[^\]]*\])/);
	if (!jsFallback) {
		console.error('MARKERS NOT FOUND: the `rule.types || [...]` fallback in industry_rule().');
		process.exit(2);
	}
	check(
		same(JSON.parse(jsFallback[1]), SERVER_TYPES),
		'the client fallback type list equals data_quality.COMMERCIAL_TYPES',
		'the client fallback ' + jsFallback[1] + ' differs from COMMERCIAL_TYPES ' + JSON.stringify(SERVER_TYPES)
	);
}

for (const [label, rule, fallbackOnly] of [
	['boot rule', RULE, false],
	['no boot rule (fallback list)', undefined, true],
]) {
	const { frappe, log } = makeDesk({ industryRule: rule });
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.render_dialog();
	const industry = qe.docfields.find((df) => df.fieldname === 'industry');
	const cases = { Commercial: true, Company: true, Partnership: true, Residential: false, Individual: false, '': false };
	const wrong = Object.entries(cases).filter(
		([type, want]) => evalDepends(industry.depends_on, { customer_type: type }) !== want
	);
	check(
		wrong.length === 0,
		label + ': Industry shows for exactly ' + SERVER_TYPES.join('/'),
		label + ': Industry visibility wrong for ' + wrong.map(([t]) => t || '(blank)').join(', ')
	);
	if (!fallbackOnly) {
		check(
			industry.mandatory_depends_on === industry.depends_on && industry.hidden === 0,
			label + ': Industry is required exactly when it is shown',
			label + ': Industry requirement does not match its visibility'
		);
	} else {
		check(
			!industry.mandatory_depends_on,
			label + ': without the boot rule Industry is offered but not required (server stays the authority)',
			label + ': Industry required with no boot rule to say so'
		);
	}
	void log;
}

{
	const { frappe } = makeDesk({ industryRule: { required: 0, types: SERVER_TYPES } });
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.render_dialog();
	const industry = qe.docfields.find((df) => df.fieldname === 'industry');
	check(
		!industry.mandatory_depends_on,
		'require_industry_on_commercial off: Industry is not required',
		'Industry required although require_industry_on_commercial is off'
	);
}

{
	const { frappe, log } = makeDesk({ industryRule: RULE });
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.render_dialog();
	qe.values = { customer_name: 'Home', customer_type: 'Residential', industry: 'Hospitality' };
	const doc = qe.insert();
	check(
		log.includes('plain.insert') && !log.includes('stock.insert'),
		'insert goes through QuickEntryForm, not erpnext\'s alias-renaming insert',
		'erpnext\'s insert ran with the toggle on'
	);
	check(doc.industry === null, 'an industry picked before switching to Residential is dropped', 'Residential account saved with an industry: ' + doc.industry);

	const qe2 = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe2.values = { customer_name: 'Hotel', customer_type: 'Commercial', industry: 'Hospitality' };
	check(qe2.update_doc().industry === 'Hospitality', 'a commercial account keeps its industry', 'commercial account lost its industry');
}

// --- 4. after save --------------------------------------------------------------------

{
	const { frappe, context, log } = makeDesk({ industryRule: RULE });
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.process_after_insert({ message: { doctype: 'Customer', name: 'ACME', customer_name: 'Acme' } });
	const route = log.find((e) => e.route);
	check(
		route && same(route.route, ['Form', 'Customer', 'ACME']) && !log.includes('open_form_if_not_list'),
		'list / awesomebar create opens the saved record\'s full form',
		'a quick-entry create did not open the full form: ' + JSON.stringify(log)
	);
	check(
		context.erpnext_enhancements.party_quick_entry.pending.has('Customer::ACME'),
		'the new record is flagged for the one-time address banner',
		'the new record was not flagged for the banner'
	);
}

{
	const { frappe, log } = makeDesk({ industryRule: RULE });
	frappe._from_link = { doctype: 'Project', docname: 'PRJ-1' };
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.process_after_insert({ message: { doctype: 'Customer', name: 'ACME<1>', customer_name: 'Acme <Pools>' } });
	const alert = log.find((e) => e.alert);
	check(
		log.includes('update_calling_link') && !log.some((e) => e.route),
		'link-field create fills the calling field and stays on that form',
		'link-field create navigated away from the calling form: ' + JSON.stringify(log)
	);
	check(
		alert &&
			alert.message.includes('/desk/customer/ACME%3C1%3E?scroll_to=address_list_html') &&
			alert.message.includes('target="_blank"') &&
			alert.message.includes('Acme &lt;Pools&gt;'),
		'…and offers the address as a new-tab link scrolled to the Address Directory',
		'link-field create did not offer a safe new-tab address link: ' + JSON.stringify(alert)
	);
}

{
	const { frappe, log } = makeDesk({ industryRule: RULE });
	let called = null;
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer', (doc) => (called = doc.name));
	qe.process_after_insert({ message: { doctype: 'Customer', name: 'ACME' } });
	check(
		called === 'ACME' && !log.some((e) => e.route),
		'a caller\'s own after_insert callback is honoured, with no redirect',
		'a caller\'s after_insert was overridden by the redirect'
	);
}

// --- 5. the banner ----------------------------------------------------------------------

{
	const { context, handlers } = makeDesk({ industryRule: RULE });
	const pqe = context.erpnext_enhancements.party_quick_entry;

	const quiet = makeForm('Customer', { name: 'OLD' });
	fire(handlers, 'Customer', 'refresh', quiet.frm);
	check(quiet.messages.length === 0, 'no banner on a record that was not just created', 'banner shown on an existing record');

	pqe.pending.add('Customer::ACME');
	const fresh = makeForm('Customer', { name: 'ACME' });
	fire(handlers, 'Customer', 'refresh', fresh.frm);
	check(
		fresh.messages.length === 1 &&
			fresh.messages[0].color === 'blue' &&
			/Add Address/.test(fresh.messages[0].html) &&
			/This account has no address yet\./.test(fresh.messages[0].html),
		'a just-created record with no address shows the Add Address banner',
		'no banner on a just-created record: ' + fresh.messages.length
	);
	fire(handlers, 'Customer', 'refresh', fresh.frm);
	check(fresh.messages.length === 1, 'a second refresh does not stack a second banner', 'banners stacked on refresh');

	fresh.messages[0].click('.ee-add-address');
	check(
		fresh.frm.scrolled === 'address_list_html' && !pqe.pending.has('Customer::ACME') && !fresh.messages[0][0].attached,
		'Add Address jumps to the Address Directory and retires the banner',
		'Add Address did not jump to the directory / clear the banner'
	);

	pqe.pending.add('Customer::LEAD');
	const converted = makeForm('Customer', { name: 'LEAD', addresses: [{ name: 'A-1' }] });
	fire(handlers, 'Customer', 'refresh', converted.frm);
	check(
		converted.messages.length === 0 && !pqe.pending.has('Customer::LEAD'),
		'no banner when the new record already has an address (Lead conversion)',
		'banner shown on a new record that already has an address'
	);

	pqe.pending.add('Supplier::VEND');
	const vendor = makeForm('Supplier', { name: 'VEND' });
	fire(handlers, 'Supplier', 'refresh', vendor.frm);
	pqe.addresses_rendered(vendor.frm, 0);
	check(vendor.messages[0][0].attached, 'an empty Address Directory leaves the banner up', 'banner removed by an empty directory render');
	pqe.addresses_rendered(vendor.frm, 1);
	check(
		!vendor.messages[0][0].attached && !pqe.pending.has('Supplier::VEND'),
		'the banner goes as soon as the Address Directory lists an address',
		'banner survived an address being added'
	);

	const full = makeForm('Supplier', { name: 'NEWV', isNew: true });
	fire(handlers, 'Supplier', 'before_save', full.frm);
	full.frm.is_new = () => false;
	fire(handlers, 'Supplier', 'after_save', full.frm);
	check(
		full.messages.length === 1,
		'the first save from the full form ("Edit Full Form", Lead conversion) gets the same banner',
		'first save from the full form showed no banner'
	);
	fire(handlers, 'Supplier', 'before_save', full.frm);
	fire(handlers, 'Supplier', 'after_save', full.frm);
	check(full.messages.length === 1, 'later saves do not re-raise it', 'a later save raised the banner again');
}

// --- 6. toggle off: every method is erpnext's -------------------------------------------

{
	const { frappe, log, handlers, context } = makeDesk({ contactsUx: 0, industryRule: RULE });
	const qe = new frappe.ui.form.CustomerQuickEntryForm('Customer');
	qe.render_dialog();
	qe.insert();
	const quick = qe.is_quick_entry();
	qe.process_after_insert({ message: { doctype: 'Customer', name: 'ACME' } });
	check(
		log.includes('stock.render_dialog') && log.includes('stock.insert') && quick === 'plain' &&
			log.includes('open_form_if_not_list') && !log.some((e) => e.route || e.plain_render),
		'toggle off: render_dialog, insert, is_quick_entry and after-insert are all stock',
		'toggle off still runs custom behaviour: ' + JSON.stringify(log)
	);
	context.erpnext_enhancements.party_quick_entry.pending.add('Customer::ACME');
	const form = makeForm('Customer', { name: 'ACME' });
	fire(handlers, 'Customer', 'refresh', form.frm);
	check(form.messages.length === 0, 'toggle off: no banner', 'toggle off still shows the banner');
}

// --- 7. without erpnext's class ----------------------------------------------------------

{
	const { frappe, QuickEntryForm, log } = makeDesk({ industryRule: RULE, withStock: false });
	const cls = frappe.ui.form.CustomerQuickEntryForm;
	const qe = new cls('Customer');
	qe.render_dialog();
	check(
		cls.prototype instanceof QuickEntryForm && log.some((e) => e.plain_render),
		'with no erpnext class to extend it still registers on QuickEntryForm',
		'registration depends on erpnext defining CustomerQuickEntryForm'
	);
}

// --- 8. wiring -----------------------------------------------------------------------------

{
	const contactAt = JS_ENTRY.indexOf('import "./global_enhancements/contact_address_quick_entry.js"');
	const partyAt = JS_ENTRY.indexOf('import "./global_enhancements/party_quick_entry.js"');
	check(
		partyAt !== -1 && partyAt > contactAt,
		'party_quick_entry.js is in the desk bundle (content-hashed, not a raw /assets path)',
		'party_quick_entry.js is not imported by erpnext_enhancements.bundle.js'
	);
	check(
		/bootinfo\.ee_industry_rule\s*=\s*industry_rule_for_client\(\)/.test(BOOT),
		'boot.py ships ee_industry_rule from data_quality',
		'boot.py does not ship ee_industry_rule — the dialog would fall back and never require Industry'
	);
	check(
		/def industry_rule_for_client\(\):[\s\S]*?_flag\("require_industry_on_commercial"\)[\s\S]*?list\(COMMERCIAL_TYPES\)/.test(DATA_QUALITY),
		'industry_rule_for_client reads the new-record flag and COMMERCIAL_TYPES',
		'industry_rule_for_client no longer mirrors enforce_industry\'s flag and type list'
	);
	check(
		/party_quick_entry\.addresses_rendered\(frm,/.test(DIRECTORY),
		'the Address Directory reports its address count (retires the banner)',
		'unified_tab_controller.js no longer calls addresses_rendered — the banner would linger'
	);
	for (const dt of ['Customer', 'Supplier']) {
		const block = HOOKS.match(new RegExp('\\n\\t"' + dt + '": \\[([\\s\\S]*?)\\n\\t\\]'));
		check(
			block && block[1].includes('unified_tab_controller.js'),
			dt + ' form loads the Address Directory the banner points at',
			dt + ' doctype_js no longer loads unified_tab_controller.js — "Add Address" would jump to nothing'
		);
	}
}

console.log('');
if (failures) {
	console.error(failures + ' check(s) failed.');
	process.exit(1);
}
console.log('All party quick entry checks passed.');
