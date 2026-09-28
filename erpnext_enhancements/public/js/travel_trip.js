/**
 * Travel Trip form script.
 *
 * Targets: the "Travel Trip" doctype form and its child grids.
 * Loaded via: hooks.py `doctype_js["Travel Trip"]` (alongside
 *   public/js/travel/travel_trip_map.js, which owns the agenda map).
 *
 * Provides:
 *  - Create-group buttons backed by erpnext_enhancements.travel_management.api:
 *    per-traveler / all-traveler Expense Claims, Employee Advance, Vehicle Log
 *    (Company Fleet rows), Lead/Opportunity from an itinerary stop, plus
 *    Send Itinerary (email + ICS) and the coordinator Reopen Trip action.
 *  - Link scoping: the Travel For type is limited to Project / Opportunity /
 *    Lead / Customer, agenda related-party types to the server-validated set,
 *    and traveler-ish Employee links to the trip's travelers.
 *  - New cost rows inherit the trip-level `billable` default.
 *  - The trip checklist headline (counts from the server's
 *    travel_management/completeness.py, sent as __onload.trip_gaps) and the
 *    "Plan step by step" door into the Plan a Trip page, which words each gap
 *    and fixes it. The wording lives on the page only, so the two cannot drift.
 *    Paperwork gaps are not in the headline's count: they are a quiet
 *    "N files not attached yet" line under it, linking to the Files step.
 *  - "Trip views": the whole trip day by day (Overview), the crew by day (Crew
 *    grid), a column per person (Side by side), every place on one map (Map)
 *    and one person's own itinerary (View as) — each opens Plan a Trip on that
 *    view for this trip.
 *  - "Copy trip": plan another trip like this one — opens Plan a Trip's copy
 *    screen for it (?copy=), which asks for the new start date and whether the
 *    same crew goes, and leaves confirmation numbers and costs behind.
 */

const TRAVEL_FOR_DOCTYPES = ['Project', 'Opportunity', 'Lead', 'Customer'];
const RELATED_PARTY_DOCTYPES = ['Customer', 'Lead', 'Opportunity', 'Contact', 'Supplier', 'Project'];
const COST_TABLES = ['flights', 'accommodations', 'ground_transport', 'freight', 'other_costs'];

function travelers_options(frm) {
	return (frm.doc.travelers || [])
		.filter((t) => t.employee)
		.map((t) => ({ value: t.name, label: `${t.employee_name || t.employee}` }));
}

function traveler_employee_query(frm) {
	return {
		filters: { name: ['in', (frm.doc.travelers || []).map((t) => t.employee).filter(Boolean)] },
	};
}

function call_and_reload(frm, method, args, success_message) {
	return frappe
		.call({ method: `erpnext_enhancements.travel_management.api.${method}`, args })
		.then((r) => {
			if (success_message) frappe.show_alert({ message: success_message(r.message), indicator: 'green' });
			frm.reload_doc();
			return r.message;
		});
}

function pick_traveler(frm, title, callback) {
	const options = travelers_options(frm);
	if (!options.length) {
		frappe.msgprint(__('Add at least one traveler first.'));
		return;
	}
	frappe.prompt(
		[
			{
				fieldname: 'traveler',
				label: __('Traveler'),
				fieldtype: 'Select',
				options: options,
				reqd: 1,
			},
		],
		(values) => callback(values.traveler),
		title
	);
}

function create_expense_claims(frm) {
	frappe.confirm(
		__('Create draft Expense Claims for every traveler with unclaimed employee-paid costs, per diem or mileage?'),
		() =>
			call_and_reload(frm, 'create_expense_claims', { trip: frm.doc.name }, (claims) => {
				const count = Object.keys(claims || {}).length;
				return count
					? __('Created/updated {0} Expense Claim(s)', [count])
					: __('Nothing left to claim.');
			})
	);
}

function create_expense_claim_for_one(frm) {
	pick_traveler(frm, __('Expense Claim for Traveler'), (traveler) =>
		call_and_reload(frm, 'create_expense_claim', { trip: frm.doc.name, traveler }, (claim) =>
			claim ? __('Expense Claim {0} ready', [claim]) : __('Nothing left to claim.')
		)
	);
}

function create_employee_advance(frm) {
	const options = travelers_options(frm);
	if (!options.length) {
		frappe.msgprint(__('Add at least one traveler first.'));
		return;
	}
	frappe.prompt(
		[
			{ fieldname: 'traveler', label: __('Traveler'), fieldtype: 'Select', options, reqd: 1 },
			{ fieldname: 'amount', label: __('Advance Amount'), fieldtype: 'Currency', reqd: 1 },
		],
		(values) =>
			call_and_reload(
				frm,
				'create_employee_advance',
				{ trip: frm.doc.name, traveler: values.traveler, amount: values.amount },
				(advance) => __('Employee Advance {0} created (draft)', [advance])
			),
		__('Travel Advance')
	);
}

function create_vehicle_log(frm) {
	const fleet_rows = (frm.doc.ground_transport || []).filter(
		(g) => g.transport_type === 'Company Fleet' && g.vehicle && !g.vehicle_log
	);
	if (!fleet_rows.length) {
		frappe.msgprint(__('No Company Fleet ground-transport rows without a Vehicle Log.'));
		return;
	}
	frappe.prompt(
		[
			{
				fieldname: 'row',
				label: __('Fleet Row'),
				fieldtype: 'Select',
				options: fleet_rows.map((g) => ({
					value: g.name,
					label: `#${g.idx}: ${g.vehicle} ${g.pickup_location || ''} → ${g.dropoff_location || ''}`,
				})),
				reqd: 1,
			},
			{ fieldname: 'odometer', label: __('Odometer'), fieldtype: 'Int', reqd: 1 },
			{ fieldname: 'date', label: __('Date'), fieldtype: 'Date' },
		],
		(values) =>
			call_and_reload(
				frm,
				'create_vehicle_log',
				{ trip: frm.doc.name, ground_row: values.row, odometer: values.odometer, date: values.date },
				(log) => __('Vehicle Log {0} created (draft)', [log])
			),
		__('Vehicle Log')
	);
}

function create_outcome(frm, target_doctype) {
	const stops = (frm.doc.itinerary || []).filter((s) => !s.outcome_name);
	if (!stops.length) {
		frappe.msgprint(__('No itinerary stops without an outcome yet — add a stop first.'));
		return;
	}
	const fields = [
		{
			fieldname: 'stop',
			label: __('Itinerary Stop'),
			fieldtype: 'Select',
			options: stops.map((s) => ({
				value: s.name,
				label: `#${s.idx} ${s.date}: ${(s.activity_description || '').slice(0, 60)}`,
			})),
			reqd: 1,
		},
	];
	if (target_doctype === 'Lead') {
		fields.push(
			{ fieldname: 'lead_name', label: __('Person Name'), fieldtype: 'Data', reqd: 1 },
			{ fieldname: 'company_name', label: __('Company Name'), fieldtype: 'Data' },
			{ fieldname: 'email_id', label: __('Email'), fieldtype: 'Data' },
			{ fieldname: 'mobile_no', label: __('Mobile'), fieldtype: 'Data' }
		);
	} else {
		fields.push(
			{
				fieldname: 'opportunity_from',
				label: __('Opportunity From'),
				fieldtype: 'Select',
				options: ['', 'Lead', 'Customer'],
				description: __("Leave blank to use the stop's related party."),
			},
			{
				fieldname: 'party_name',
				label: __('Party'),
				fieldtype: 'Dynamic Link',
				options: 'opportunity_from',
				depends_on: 'opportunity_from',
			}
		);
	}
	frappe.prompt(
		fields,
		(values) => {
			const { stop, ...doc_values } = values;
			call_and_reload(
				frm,
				'create_outcome_from_stop',
				{
					trip: frm.doc.name,
					agenda_row: stop,
					target_doctype,
					values: doc_values,
				},
				(name) => __('{0} {1} created', [__(target_doctype), name])
			);
		},
		__('New {0} from Stop', [__(target_doctype)])
	);
}

function send_itinerary(frm) {
	const options = travelers_options(frm);
	frappe.prompt(
		[
			{
				fieldname: 'traveler_row',
				label: __('Traveler (blank = everyone)'),
				fieldtype: 'Select',
				options: [{ value: '', label: __('All travelers') }].concat(options),
			},
		],
		(values) => {
			const row = (frm.doc.travelers || []).find((t) => t.name === values.traveler_row);
			frappe
				.call({
					method: 'erpnext_enhancements.api.travel.send_itinerary_email',
					args: { trip: frm.doc.name, employee: row ? row.employee : null },
				})
				.then((r) =>
					frappe.show_alert({
						message: __('Itinerary sent to {0} traveler(s)', [(r.message || []).length]),
						indicator: 'green',
					})
				);
		},
		__('Send Itinerary')
	);
}

// Plan a Trip's looks at the whole trip, by their &view= key (TP_VIEWS on the page). The
// page reads `view` (and `as`) from frappe.route_options, as it reads `trip`.
const TRIP_VIEWS = [
	['overview', __('Overview')],
	['grid', __('Crew grid')],
	['compare', __('Side by side')],
	['map', __('Map')],
];

// The views show the trip as saved. With unsaved changes here they would show something other
// than this form, and View as could be asked for someone added here and not saved yet, whom
// the page cannot show.
function trip_views_need_a_save(frm) {
	if (!frm.is_dirty()) return false;
	frappe.msgprint({
		title: __('Save the trip first'),
		message: __('Trip views show the trip as it is saved. Save your changes, then open them.'),
		indicator: 'orange',
	});
	return true;
}

function open_trip_view(frm, view, employee) {
	if (trip_views_need_a_save(frm)) return;
	const options = { trip: frm.doc.name, view };
	if (employee) options.as = employee;
	frappe.set_route('plan-a-trip', options);
}

// Plan another trip like this one: Plan a Trip's copy screen, read from frappe.route_options
// like `trip` and `view`. Any status: a finished job is the usual one to repeat. The copy is made
// from the trip as it is saved, so with unsaved changes here it asks for a save first.
function copy_trip(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint({
			title: __('Save the trip first'),
			message: __('A copy is made from the trip as it is saved. Save your changes, then copy it.'),
			indicator: 'orange',
		});
		return;
	}
	frappe.set_route('plan-a-trip', { copy: frm.doc.name });
}

function open_trip_view_as(frm) {
	if (trip_views_need_a_save(frm)) return;
	const crew = (frm.doc.travelers || []).filter((t) => t.employee);
	if (crew.length <= 1) {
		open_trip_view(frm, 'person', crew.length ? crew[0].employee : null);
		return;
	}
	frappe.prompt(
		[
			{
				fieldname: 'employee',
				label: __('Whose itinerary?'),
				fieldtype: 'Select',
				options: crew.map((t) => ({ value: t.employee, label: t.employee_name || t.employee })),
				default: crew[0].employee,
				reqd: 1,
			},
		],
		(values) => open_trip_view(frm, 'person', values.employee),
		__('View as')
	);
}

// What the headline counts. Paperwork (check "documents") is not here on purpose: it is a
// separate, quieter tally (Nik, 2026-09-26), the muted line under the headline.
const CHECKLIST_LABELS = {
	travel: __('a way there or back'),
	lodging: __('a bed for the night'),
	confirmation: __('a confirmation number'),
	cost: __('a cost'),
};

const PAPERWORK_CHECK = 'documents';

// How many files the paperwork gaps stand for: each person on a flight still without a boarding
// pass or ticket, one file for any other booking — completeness.files_not_attached.
function files_not_attached(gaps) {
	return gaps
		.filter((gap) => gap.check === PAPERWORK_CHECK)
		.reduce((count, gap) => count + Math.max(1, (gap.employee_names || []).length), 0);
}

// The trip checklist as the form's headline: what is missing, counted and linked to Review;
// and, apart from it and quieter, the paperwork not attached yet, linked to the Files step.
// Paperwork never makes the headline orange and is never in its count: v1.544.0 had just taken
// the first real trip from six flags to none, and counting its paperwork would have put ten
// back — every booking on it has its number and no file yet.
function show_trip_checklist(frm) {
	if (frm.is_new() || frm.doc.status === 'Closed') {
		frm.dashboard.clear_headline();
		return;
	}
	const all = (frm.doc.__onload && frm.doc.__onload.trip_gaps) || [];
	const gaps = all.filter((gap) => gap.check !== PAPERWORK_CHECK);
	const files = files_not_attached(all);
	const plan_url = `/desk/plan-a-trip?trip=${encodeURIComponent(frm.doc.name)}`;
	const paperwork = files
		? `<div class="small text-muted" style="margin-top:2px;"><a class="text-muted" href="${plan_url}&step=files">${
				files === 1 ? __('1 file not attached yet') : __('{0} files not attached yet', [files])
		  }</a></div>`
		: '';
	if (!gaps.length) {
		frm.dashboard.set_headline_alert(`${__('Trip checklist: nothing missing.')}${paperwork}`, 'green');
		return;
	}
	const counts = {};
	gaps.forEach((gap) => {
		counts[gap.check] = (counts[gap.check] || 0) + 1;
	});
	const parts = Object.keys(CHECKLIST_LABELS)
		.filter((check) => counts[check])
		.map((check) => __('{0} missing {1}', [counts[check], CHECKLIST_LABELS[check]]));
	frm.dashboard.set_headline_alert(
		`${__('Trip checklist')}: ${frappe.utils.escape_html(parts.join('; '))}.
		 <a href="${plan_url}&step=review">${__('See what and fix it')}</a>${paperwork}`,
		'orange'
	);
}

frappe.ui.form.on('Travel Trip', {
	onload(frm) {
		if (frm.is_new()) {
			frm.set_intro(
				`${__('Easier: Plan a Trip walks through the crew, flights, rooms and schedule one step at a time.')}
				 <a href="/desk/plan-a-trip?new=1">${__('Plan a Trip')}</a>`,
				'blue'
			);
		}
	},

	setup(frm) {
		frm.set_query('travel_for_doctype', () => ({
			filters: { name: ['in', TRAVEL_FOR_DOCTYPES] },
		}));
		frm.set_query('related_party_doctype', 'itinerary', () => ({
			filters: { name: ['in', RELATED_PARTY_DOCTYPES] },
		}));
		COST_TABLES.forEach((table) =>
			frm.set_query('paid_by_traveler', table, () => traveler_employee_query(frm))
		);
		frm.set_query('traveler', 'mileage', () => traveler_employee_query(frm));
		// Freight's "Received By" is someone on the crew.
		frm.set_query('traveler', 'freight', () => traveler_employee_query(frm));
		// A trip file's "Only For" is someone on the crew (Plan a Trip refuses anyone else).
		frm.set_query('traveler', 'documents', () => traveler_employee_query(frm));
	},

	refresh(frm) {
		show_trip_checklist(frm);
		if (frm.is_new()) return;

		frm.add_custom_button(__('Plan step by step'), () =>
			frappe.set_route('plan-a-trip', { trip: frm.doc.name })
		);
		// A saved trip only, and not while it has unsaved changes: the views read what is stored.
		TRIP_VIEWS.forEach(([view, label]) =>
			frm.add_custom_button(label, () => open_trip_view(frm, view), __('Trip views'))
		);
		frm.add_custom_button(__('View as'), () => open_trip_view_as(frm), __('Trip views'));
		// A saved trip only (refresh returns above for a new one): the copy screen reads it by name.
		frm.add_custom_button(__('Copy trip'), () => copy_trip(frm));

		// HRMS is optional: the Expense Claim / Employee Advance / Vehicle Log
		// actions need its doctypes (api.py also guards them). Hide the buttons
		// when HRMS isn't installed rather than offer a dead end.
		const hrms = !!(frm.doc.__onload && frm.doc.__onload.expense_claims_available);

		if (frm.doc.status !== 'Closed') {
			if (hrms) {
				frm.add_custom_button(__('Expense Claims (All Travelers)'), () => create_expense_claims(frm), __('Create'));
				frm.add_custom_button(__('Expense Claim (One Traveler)'), () => create_expense_claim_for_one(frm), __('Create'));
				frm.add_custom_button(__('Employee Advance'), () => create_employee_advance(frm), __('Create'));
				frm.add_custom_button(__('Vehicle Log'), () => create_vehicle_log(frm), __('Create'));
			}
			frm.add_custom_button(__('Lead from Stop'), () => create_outcome(frm, 'Lead'), __('Create'));
			frm.add_custom_button(__('Opportunity from Stop'), () => create_outcome(frm, 'Opportunity'), __('Create'));
		}

		frm.add_custom_button(__('Send Itinerary'), () => send_itinerary(frm));

		if (frm.doc.status === 'Closed') {
			frm.add_custom_button(__('Reopen Trip'), () =>
				call_and_reload(frm, 'reopen_trip', { trip: frm.doc.name }, () => __('Trip reopened'))
			);
		}
	},
});

// New cost rows inherit the trip-level billable default. The *_add grid
// events fire on the parent doctype handler, keyed by table fieldname.
const billable_mirror = {};
COST_TABLES.concat(['mileage']).forEach((table) => {
	billable_mirror[`${table}_add`] = function (frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'billable', cint(frm.doc.billable));
	};
});
frappe.ui.form.on('Travel Trip', billable_mirror);
