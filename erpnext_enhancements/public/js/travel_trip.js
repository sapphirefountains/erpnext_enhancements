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
 */

const TRAVEL_FOR_DOCTYPES = ['Project', 'Opportunity', 'Lead', 'Customer'];
const RELATED_PARTY_DOCTYPES = ['Customer', 'Lead', 'Opportunity', 'Contact', 'Supplier', 'Project'];
const COST_TABLES = ['flights', 'accommodations', 'ground_transport', 'other_costs'];

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

frappe.ui.form.on('Travel Trip', {
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
	},

	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status !== 'Closed') {
			frm.add_custom_button(__('Expense Claims (All Travelers)'), () => create_expense_claims(frm), __('Create'));
			frm.add_custom_button(__('Expense Claim (One Traveler)'), () => create_expense_claim_for_one(frm), __('Create'));
			frm.add_custom_button(__('Employee Advance'), () => create_employee_advance(frm), __('Create'));
			frm.add_custom_button(__('Vehicle Log'), () => create_vehicle_log(frm), __('Create'));
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
