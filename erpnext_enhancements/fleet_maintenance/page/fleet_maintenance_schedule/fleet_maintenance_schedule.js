// Fleet Maintenance Schedule — an in-desk reference of the routine vehicle
// maintenance cadence (daily / weekly / 3-month / 6-month). Static reference;
// the actual tracking lives in the Vehicle Maintenance Log doctype.
frappe.pages["fleet-maintenance-schedule"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Fleet Maintenance Schedule"),
		single_column: true,
	});

	page.set_primary_action(__("Log Maintenance"), () => frappe.new_doc("Vehicle Maintenance Log"), "add");
	page.add_inner_button(__("Fleet Vehicles"), () => frappe.set_route("List", "Fleet Vehicle"));
	page.add_inner_button(__("Maintenance Logs"), () => frappe.set_route("List", "Vehicle Maintenance Log"));

	const SCHEDULE = [
		{
			cadence: "Daily",
			accent: "#2490ef",
			note: "Standing instruction for every driver — not tracked on a form.",
			items: ["Check the gas level; refill the tank if it is at or below half."],
		},
		{
			cadence: "Weekly",
			accent: "#28a745",
			note: 'Record on a Vehicle Maintenance Log (type "Weekly").',
			items: [
				"Check &amp; restock vehicle inventory/stock",
				"Check vehicle fluids — engine oil &amp; windshield washer",
				"Check tire pressure (all tires)",
				"Car wash (exterior) and interior cleaning",
			],
		},
		{
			cadence: "Every 3 Months",
			accent: "#fd7e14",
			note: 'Use the calendar / the vehicle’s "Oil Change Due" date to time it. Log type "Oil Change (3-Month)".',
			items: ["Oil change (oil &amp; filter)"],
		},
		{
			cadence: "Every 6 Months",
			accent: "#e03131",
			note: 'Use the calendar / the vehicle’s "Due" dates to time these. Log types "Dealership Check-Up" and "Windshield Wipers".',
			items: ["Dealership check-up / inspection", "Replace windshield wipers"],
		},
	];

	const cards = SCHEDULE.map(
		(s) => `
		<div style="flex:1 1 260px; min-width:240px; background:var(--card-bg); border:1px solid var(--border-color);
		            border-top:3px solid ${s.accent}; border-radius:8px; padding:14px 16px;">
			<div style="font-size:15px; font-weight:600; color:var(--text-color); margin-bottom:2px;">${__(s.cadence)}</div>
			<div style="font-size:11px; color:var(--text-muted); margin-bottom:10px;">${s.note}</div>
			<ul style="margin:0; padding-left:18px; color:var(--text-color); font-size:13px; line-height:1.7;">
				${s.items.map((i) => `<li>${i}</li>`).join("")}
			</ul>
		</div>`
	).join("");

	const html = `
		<div style="padding:4px 2px 18px;">
			<p style="color:var(--text-muted); margin:0 0 16px; max-width:680px;">
				The routine maintenance cadence for company vehicles. Weekly and longer-interval
				work is recorded on a <b>Vehicle Maintenance Log</b>; each vehicle's
				<b>Fleet Vehicle</b> record tracks the last service and shows when the next one is due.
			</p>
			<div style="display:flex; flex-wrap:wrap; gap:14px;">${cards}</div>
			<p style="color:var(--text-muted); margin:18px 0 0; font-size:12px;">
				Tip: open a Vehicle Maintenance Log, pick a type, and use
				<b>Menu &rarr; Print</b> (Vehicle Maintenance Checklist) to print a blank sheet to keep in the vehicle.
			</p>
		</div>`;

	$(page.main).html(html);
};
