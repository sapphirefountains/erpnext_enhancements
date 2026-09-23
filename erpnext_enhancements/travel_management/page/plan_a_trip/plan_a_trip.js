// Plan a Trip — the office's step-by-step way to enter a whole trip.
//
// A desk Page (/app/plan-a-trip, or ?trip=TRIP-... to carry on with one) that walks through a
// Travel Trip one question at a time: the trip, who's going, getting there, getting back, where
// everyone sleeps, getting around, the schedule — and ends on a checklist of what is still
// missing. It reads and writes the same Travel Trip as the desk form, through
// travel_management/planner.py, and every save runs the Travel Trip controller.
//
// BOOKINGS HERE, ONE ROW PER PERSON THERE. The office books for the crew, so a card on this page
// is a booking — one flight with four people on it, one room shared by two — with a tick box per
// person. The server stores one row per person, each with that person's own confirmation number,
// so each traveler's itinerary, email and calendar invite shows their booking and nobody else's.
// Cards carry a `changed` set: an existing row only has a shared field rewritten when it was
// actually edited here (see the planner.py module docstring for why).
//
// SAVING. Moving between steps saves, and so does leaving the page. Nothing is saved until the
// trip has its basics and at least one person — the Travel Trip controller refuses a trip with
// no traveler. A save based on a version somebody else replaced is refused by the server, and
// the page offers a reload rather than merging.
//
// TIMES are native <input type="time">, which shows AM/PM on a US browser or phone. A stored
// time of exactly midnight reads as "no time given": the Datetime column cannot hold a date
// without a time, so a flight whose time is not known yet is stored at 00:00:00.
//
// The checklist is the server's (planner.get_state -> completeness.find_gaps). The only thing
// computed here is the at-a-glance coverage on the step being edited, which is replaced by the
// server's answer on the next save.
//
// Styling uses Frappe CSS variables so Frappe Light and Timeless Night both work. The page loader
// serves this file version-aware, so no .bundle.* is needed.

frappe.pages["plan-a-trip"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Plan a Trip"),
		single_column: true,
	});
	wrapper.trip_planner = new TripPlanner(page, wrapper);
	// Leaving the page (a link, the sidebar, "create a new Supplier" opening a form) saves
	// what is there. Frappe triggers "hide" on the page wrapper when another page takes over.
	$(wrapper).on("hide", () => wrapper.trip_planner.save_quietly());
};

frappe.pages["plan-a-trip"].on_page_show = function (wrapper) {
	if (wrapper.trip_planner) wrapper.trip_planner.handle_route();
};

const TP_STYLE = `
.tp-wrap{max-width:760px;margin:0 auto;padding-bottom:110px;font-size:15px;color:var(--text-color);}
.tp-muted{color:var(--text-muted);font-size:13px;}
.tp-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 8px;}
.tp-head h4{margin:0;font-size:19px;}
.tp-pill{display:inline-block;font-size:12px;border-radius:10px;padding:2px 10px;background:var(--control-bg);border:1px solid var(--border-color);}
.tp-pill-green{background:#e7f7ed;color:#15803d;border-color:#15803d;}
.tp-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 12px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.tp-tab{flex:0 0 auto;padding:8px 14px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:14px;cursor:pointer;white-space:nowrap;}
.tp-tab.tp-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-tab .tp-badge{display:inline-block;min-width:18px;margin-left:6px;padding:0 5px;border-radius:9px;background:#fde8e8;color:#b91c1c;font-size:12px;font-weight:600;text-align:center;}
.tp-tab.tp-active .tp-badge{background:#fff;}
.tp-step-title{font-size:18px;font-weight:600;margin:6px 0 2px;}
.tp-step-help{color:var(--text-muted);margin-bottom:14px;line-height:1.45;}
.tp-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:12px;}
.tp-card.tp-bad{border-color:#dc2626;}
.tp-card-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;}
.tp-card-head b{font-size:16px;}
.tp-card-head .tp-remove{margin-left:auto;}
.tp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 14px;}
.tp-field{display:flex;flex-direction:column;gap:4px;min-width:0;}
.tp-field label{font-size:13px;color:var(--text-muted);margin:0;}
.tp-field label .tp-req{color:#b91c1c;}
.tp-field input,.tp-field select,.tp-field textarea{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.tp-field input[type=checkbox],.tp-field input[type=radio]{width:auto;padding:0;}
.tp-field textarea{min-height:84px;}
.tp-field .frappe-control{margin:0;}
.tp-field .frappe-control input{height:auto;}
.tp-pair{display:flex;flex-wrap:wrap;gap:6px;}
.tp-pair input[type=date]{flex:1 1 150px;min-width:0;}
.tp-pair input[type=time]{flex:1 1 120px;min-width:0;}
.tp-wide{grid-column:1/-1;}
.tp-seg{display:flex;flex-wrap:wrap;gap:8px;}
.tp-seg button{flex:1 1 auto;min-height:42px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;padding:6px 12px;}
.tp-seg button.tp-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-chips{display:flex;flex-wrap:wrap;gap:8px;}
.tp-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:16px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;user-select:none;}
.tp-chip.tp-on{background:rgba(36,144,239,.12);border-color:var(--primary,#2490ef);font-weight:600;}
.tp-chip.tp-ok{background:#e7f7ed;border-color:#15803d;color:#15803d;cursor:default;}
.tp-chip.tp-miss{background:#fde8e8;border-color:#dc2626;color:#b91c1c;cursor:default;}
.tp-refs{display:flex;flex-direction:column;gap:6px;}
.tp-ref-row{display:flex;align-items:center;gap:8px;}
.tp-ref-row span{flex:0 0 160px;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tp-ref-row input{flex:1;padding:6px 10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.tp-check{display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;margin:2px 0;}
.tp-add{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px;}
.tp-btn{min-height:40px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;padding:6px 14px;}
.tp-btn-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-btn-link{border:none;background:none;color:var(--primary,#2490ef);padding:0;min-height:0;cursor:pointer;font-size:14px;}
.tp-gap{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:8px;background:#fde8e8;color:#b91c1c;font-size:14px;margin-top:8px;}
.tp-gap .tp-btn-link{margin-left:auto;white-space:nowrap;}
.tp-ok-line{padding:8px 10px;border-radius:8px;background:#e7f7ed;color:#15803d;font-size:14px;margin-top:8px;}
.tp-crew-row{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-color);border-radius:10px;margin-bottom:8px;background:var(--card-bg);}
.tp-crew-row.tp-on{border-color:var(--primary,#2490ef);}
.tp-crew-row .tp-crew-name{flex:1;min-width:0;}
.tp-crew-extra{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:8px;}
.tp-crew-extra input{padding:6px 8px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.tp-matrix{overflow-x:auto;margin-bottom:14px;}
.tp-matrix table{border-collapse:collapse;font-size:13px;}
.tp-matrix th,.tp-matrix td{border:1px solid var(--border-color);padding:5px 8px;text-align:center;white-space:nowrap;}
.tp-matrix th:first-child,.tp-matrix td:first-child{text-align:left;}
.tp-matrix td.tp-y{background:#e7f7ed;color:#15803d;}
.tp-matrix td.tp-n{background:#fde8e8;color:#b91c1c;font-weight:600;}
.tp-day{font-weight:600;margin:14px 0 6px;}
.tp-review-sec{margin-bottom:16px;}
.tp-review-sec h5{margin:0 0 6px;font-size:15px;}
.tp-sum{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:14px;}
.tp-sum div{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;}
.tp-sum b{display:block;font-size:18px;}
.tp-nav{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;padding:12px 14px calc(12px + env(safe-area-inset-bottom));background:var(--bg-color);border-top:1px solid var(--border-color);z-index:5;}
.tp-nav button{flex:1;min-height:48px;border-radius:9px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:16px;cursor:pointer;}
.tp-nav button.tp-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-nav button:disabled{opacity:.5;cursor:default;}
.tp-savestate{position:fixed;left:0;right:0;bottom:calc(76px + env(safe-area-inset-bottom));text-align:center;font-size:13px;pointer-events:none;z-index:6;}
.tp-savestate span{display:inline-block;padding:4px 12px;border-radius:12px;background:var(--control-bg);color:var(--text-muted);border:1px solid var(--border-color);}
.tp-savestate.tp-err span{background:#fde8e8;color:#b91c1c;border-color:#b91c1c;font-weight:600;}
.tp-list-item{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);cursor:pointer;}
.tp-list-item h5{margin:0 0 4px;font-size:16px;}
.tp-empty{text-align:center;padding:40px 16px;color:var(--text-muted);}
@media (max-width:600px){.tp-ref-row span{flex-basis:110px;}}
`;

// Steps, in order. `leg` ties a transport step to the Leg stored on its rows.
const TP_STEPS = [
	{ key: "trip", title: __("The trip") },
	{ key: "crew", title: __("Who's going") },
	{ key: "there", title: __("Getting there"), leg: "Outbound" },
	{ key: "back", title: __("Getting back"), leg: "Return" },
	{ key: "lodging", title: __("Where everyone sleeps") },
	{ key: "around", title: __("Getting around"), leg: "During Trip" },
	{ key: "freight", title: __("Freight") },
	{ key: "schedule", title: __("Schedule") },
	{ key: "review", title: __("Review") },
];

const TP_LEG_STEP = { Outbound: "there", Return: "back", "During Trip": "around" };

// What the page offers for ground transport, in the words the office uses. The values are the
// Trip Ground Transport `transport_type` options.
const TP_RIDE_TYPES = [
	{ value: "Company Fleet", label: __("Company vehicle") },
	{ value: "Personal Vehicle", label: __("Personal vehicle") },
	{ value: "Rental/Third Party", label: __("Rental") },
	{ value: "Taxi/Rideshare", label: __("Taxi or rideshare") },
];

// Shared fields per table: mirrors planner.BOOKING_TABLES. A new card sends all of them as
// changed; an existing card sends only what was edited.
const TP_SHARED = {
	flights: [
		"leg",
		"airline",
		"flight_number",
		"departure_airport",
		"departure_time",
		"arrival_airport",
		"arrival_time",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
	accommodations: [
		"hotel_lodging",
		"check_in_date",
		"check_in_time",
		"check_out_date",
		"check_out_time",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
	ground_transport: [
		"leg",
		"transport_type",
		"supplier",
		"vehicle",
		"pickup_location",
		"dropoff_location",
		"pickup_datetime",
		"arrival_datetime",
		"return_datetime",
		"cargo",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
};

// Freight fields: mirrors planner.FREIGHT_FIELDS. One row per shipment, not per person.
const TP_FREIGHT = [
	"carrier",
	"tracking_number",
	"contents",
	"traveler",
	"ship_from",
	"deliver_to",
	"pickup_from",
	"pickup_to",
	"delivery_from",
	"delivery_to",
	"cost",
	"billable",
	"paid_by",
	"paid_by_traveler",
];

const TP_UNBOOKED = ["Company Fleet", "Personal Vehicle"];

function tp_esc(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

function tp_date_part(datetime) {
	return datetime ? String(datetime).slice(0, 10) : "";
}

function tp_time_part(datetime) {
	// "2026-10-03 14:30:00" -> "14:30". Midnight is "not given" (see the header).
	if (!datetime || String(datetime).length < 16) return "";
	const time = String(datetime).slice(11, 16);
	return time === "00:00" ? "" : time;
}

function tp_join_datetime(date, time) {
	if (!date) return "";
	return `${date} ${time ? time : "00:00"}:00`;
}

function tp_pretty_date(date) {
	return date ? moment(date).format("ddd, MMM D") : "";
}

function tp_pretty_time(time) {
	return time ? moment(time, "HH:mm:ss").format("h:mm A") : "";
}

function tp_days_between(start, end) {
	const days = [];
	if (!start || !end) return days;
	const cursor = moment(start);
	const last = moment(end);
	while (cursor.isSameOrBefore(last, "day") && days.length < 120) {
		days.push(cursor.format("YYYY-MM-DD"));
		cursor.add(1, "day");
	}
	return days;
}

function tp_has_own_dates(traveler, trip) {
	return !!(
		(traveler.from_date && traveler.from_date !== trip.start_date) ||
		(traveler.to_date && traveler.to_date !== trip.end_date)
	);
}

function tp_sorted_stops(stops) {
	return stops
		.slice()
		.sort((a, b) => `${a.date || ""} ${a.time || ""}`.localeCompare(`${b.date || ""} ${b.time || ""}`));
}

function tp_html_to_text(html) {
	// DOMParser, not innerHTML: a parsed document never runs handlers or loads images.
	if (!html) return "";
	const marked = String(html)
		.replace(/<br\s*\/?>/gi, "\n")
		.replace(/<\/(p|div|li)>/gi, "\n");
	return new DOMParser().parseFromString(marked, "text/html").body.textContent.replace(/\n{3,}/g, "\n\n").trim();
}

function tp_text_to_html(text) {
	if (!text || !text.trim()) return "";
	return text
		.trim()
		.split(/\n/)
		.map((line) => `<p>${tp_esc(line) || "<br>"}</p>`)
		.join("");
}

class TripPlanner {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.body = $('<div class="tp-wrap"></div>').appendTo(page.main);
		$(`<style>${TP_STYLE}</style>`).appendTo(page.main);
		this.step = 0;
		this.state = null;
		this.lookups = null;
		this.saving = null;
		this.card_seq = 0;
		this.bind_unload();
		this.handle_route();
	}

	bind_unload() {
		$(window).on("beforeunload.tp", () => {
			if (this.state && this.is_dirty()) {
				return __("This trip has changes that are not saved yet.");
			}
		});
	}

	// ------------------------------------------------------------------ routing and loading

	handle_route() {
		// Switching trips, or back to the list, saves the one on screen first. The route is
		// read again afterwards, so a save that creates the trip cannot send you back to it.
		if (this.state && this.is_dirty() && !this.saving) {
			this.save({ quiet: true }).then(() => this.route());
			return;
		}
		this.route();
	}

	route() {
		const name = frappe.utils.get_url_arg("trip");
		const step_key = frappe.utils.get_url_arg("step");
		if (name) {
			if (this.state && this.state.name === name) {
				if (step_key) this.jump_to(step_key);
				return;
			}
			this.load(name, step_key);
			return;
		}
		if (frappe.utils.get_url_arg("new")) {
			if (this.state && !this.state.name) return;
			this.start_new();
			return;
		}
		this.render_landing();
	}

	load(name, step_key) {
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		frappe
			.call({ method: "erpnext_enhancements.travel_management.planner.get_plan", args: { trip: name } })
			.then(
				(r) => {
					const data = (r && r.message) || {};
					this.lookups = data.lookups;
					this.adopt(data.state);
					this.step = 0;
					if (step_key) this.jump_to(step_key, true);
					this.render();
				},
				() => this.body.html(`<div class="tp-empty">${__("This trip could not be opened.")}</div>`)
			);
	}

	start_new() {
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		frappe.call({ method: "erpnext_enhancements.travel_management.planner.get_plan" }).then(
			(r) => {
				const data = (r && r.message) || {};
				this.lookups = data.lookups;
				this.adopt(this.blank_state());
				this.step = 0;
				this.render();
			},
			() => this.body.html(`<div class="tp-empty">${__("You are not allowed to plan trips.")}</div>`)
		);
	}

	render_landing() {
		this.state = null;
		this.remove_chrome();
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		frappe.call({ method: "erpnext_enhancements.travel_management.planner.get_recent_plans" }).then(
			(r) => {
				const rows = (r && r.message) || [];
				const items = rows
					.map(
						(row) => `<button class="tp-list-item" data-name="${tp_esc(row.name)}">
							<h5>${tp_esc(row.purpose || row.name)}</h5>
							<div class="tp-muted">${tp_esc(tp_pretty_date(row.start_date))} - ${tp_esc(
							tp_pretty_date(row.end_date)
						)} &middot; ${tp_esc(__(row.status))} &middot; ${tp_esc(row.name)}</div>
						</button>`
					)
					.join("");
				this.body.html(`
					<div class="tp-card">
						<div class="tp-step-title">${__("Plan a new trip")}</div>
						<div class="tp-step-help">${__(
							"One step at a time: the trip, who's going, how everyone gets there and back, where they sleep, and the schedule. At the end you get a checklist of anything still missing."
						)}</div>
						<button class="tp-btn tp-btn-primary" data-action="new">${__("Start a new trip")}</button>
					</div>
					${
						rows.length
							? `<h5 style="margin:18px 0 8px;">${__("Carry on with a trip")}</h5>${items}`
							: `<div class="tp-muted">${__("No upcoming trips yet.")}</div>`
					}`);
				this.body.find('[data-action="new"]').on("click", () => frappe.set_route("plan-a-trip", { new: 1 }));
				this.body.find(".tp-list-item").on("click", (event) => {
					frappe.set_route("plan-a-trip", { trip: $(event.currentTarget).data("name") });
				});
			},
			() => this.body.html(`<div class="tp-empty">${__("Could not load trips.")}</div>`)
		);
	}

	jump_to(step_key, silent) {
		const index = TP_STEPS.findIndex((s) => s.key === step_key);
		if (index < 0) return;
		if (silent) {
			this.step = index;
		} else {
			this.go(index);
		}
	}

	// ------------------------------------------------------------------ state

	blank_state() {
		return {
			name: null,
			modified: null,
			status: "Planning",
			can_write: true,
			trip: {
				purpose: "",
				travel_type: "Domestic",
				company: this.lookups.default_company,
				start_date: "",
				end_date: "",
				travel_for_doctype: "",
				travel_for_name: "",
				billable: 0,
				trip_description: "",
			},
			travelers: [],
			bookings: { flights: [], accommodations: [], ground_transport: [] },
			freight: [],
			stops: [],
			gaps: [],
		};
	}

	adopt(state) {
		// Server state -> page state. Cards get a page id, an empty `changed` set, and the
		// "same confirmation for everyone" switch set from what is stored.
		state.bookings = state.bookings || { flights: [], accommodations: [], ground_transport: [] };
		Object.keys(state.bookings).forEach((table) => {
			state.bookings[table].forEach((card) => this.prepare_card(card, table));
		});
		state.freight = state.freight || [];
		state.description_text = tp_html_to_text(state.trip.trip_description);
		state.description_changed = false;
		this.state = state;
		this.infer_legs();
		this.baseline = this.serialize();
	}

	prepare_card(card, table) {
		card.table = table;
		card.uid = `c${++this.card_seq}`;
		card.changed = new Set();
		card.members = card.members || [];
		const refs = card.members.map((m) => m.ref || "");
		card.same_ref = refs.every((ref) => ref === refs[0]);
		return card;
	}

	infer_legs() {
		// A flight or drive typed on the form may have no Leg. Place it by date, the same rule
		// as completeness.leg_of, and mark it so the next save makes it explicit.
		const start = this.state.trip.start_date;
		const end = this.state.trip.end_date;
		["flights", "ground_transport"].forEach((table) => {
			this.state.bookings[table].forEach((card) => {
				if (card.values.leg) return;
				const when = tp_date_part(
					table === "flights" ? card.values.departure_time : card.values.pickup_datetime
				);
				let leg = "Outbound";
				if (when && start && end) {
					if (when >= end) leg = "Return";
					else if (when > start) leg = "During Trip";
				}
				card.values.leg = leg;
				card.changed.add("leg");
			});
		});
	}

	serialize() {
		const s = this.state;
		return JSON.stringify({
			trip: s.trip,
			desc: s.description_changed ? s.description_text : null,
			status: s.status,
			travelers: s.travelers,
			bookings: Object.keys(s.bookings).map((table) =>
				s.bookings[table].map((card) => [card.values, card.members, card.mileage || null])
			),
			freight: s.freight,
			stops: s.stops,
		});
	}

	is_dirty() {
		return !!this.state && this.serialize() !== this.baseline;
	}

	crew() {
		return this.state ? this.state.travelers : [];
	}

	crew_name(employee) {
		const t = this.crew().find((x) => x.employee === employee);
		if (t) return t.employee_name || employee;
		const e = (this.lookups.employees || []).find((x) => x.name === employee);
		return e ? e.employee_name || employee : employee;
	}

	cards(table) {
		return this.state.bookings[table];
	}

	leg_cards(leg) {
		return this.cards("flights")
			.filter((c) => c.values.leg === leg)
			.concat(this.cards("ground_transport").filter((c) => c.values.leg === leg));
	}

	set_value(card, field, value) {
		card.values[field] = value;
		card.changed.add(field);
	}

	new_card(table, leg, members) {
		const values = {};
		TP_SHARED[table].forEach((field) => {
			values[field] = "";
		});
		values.billable = this.state.trip.billable ? 1 : 0;
		values.paid_by = "Company";
		values.cost = 0;
		if (leg) values.leg = leg;
		const card = this.prepare_card(
			{
				group: `new:${++this.card_seq}`,
				values: values,
				members: (members || []).map((employee) => ({ name: null, traveler: employee, ref: "" })),
			},
			table
		);
		card.same_ref = true;
		TP_SHARED[table].forEach((field) => card.changed.add(field));
		card.changed.add("cost");
		if (table === "ground_transport") card.mileage = { driver: "", distance: 0 };
		return card;
	}

	uncovered(leg) {
		// Crew not yet on any card for this leg (or, for rooms, in no room), so a new card
		// starts with the people who still need one.
		const covered = new Set();
		const pool = leg ? this.leg_cards(leg) : this.cards("accommodations");
		pool.forEach((card) => card.members.forEach((m) => covered.add(m.traveler)));
		const everyone = this.crew().map((t) => t.employee);
		const missing = everyone.filter((e) => !covered.has(e));
		return missing.length ? missing : everyone;
	}

	// ------------------------------------------------------------------ saving

	can_create() {
		const t = this.state.trip;
		return !!(t.purpose && t.travel_type && t.start_date && t.end_date && this.crew().length);
	}

	problems() {
		// What the server would refuse, said before the round trip and in plain words.
		const out = [];
		const t = this.state.trip;
		if (!t.purpose) out.push(__("Say what the trip is for."));
		if (!t.start_date || !t.end_date) out.push(__("Pick the trip's first and last day."));
		if (t.start_date && t.end_date && t.end_date < t.start_date) out.push(__("The last day is before the first day."));
		this.cards("flights").forEach((card) => {
			if (!card.values.airline || !card.values.flight_number) {
				out.push(__("Every flight needs its airline and flight number."));
			}
		});
		// Time ranges: an end before its start is always a typo, and the server would
		// store it without complaint.
		const backwards = (start, end) => start && end && tp_date_part(start) && end < start;
		this.cards("flights").forEach((card) => {
			if (backwards(card.values.departure_time, card.values.arrival_time) && tp_time_part(card.values.arrival_time)) {
				out.push(__("A flight lands before it takes off."));
			}
		});
		this.cards("accommodations").forEach((card) => {
			const v = card.values;
			if (!v.hotel_lodging) out.push(__("Every room needs its hotel."));
			if (v.check_in_date && v.check_out_date && v.check_out_date < v.check_in_date) {
				out.push(__("A room checks out before it checks in."));
			}
		});
		this.cards("ground_transport").forEach((card) => {
			const v = card.values;
			if (!v.transport_type) out.push(__("Pick what kind of vehicle each drive is."));
			if (v.transport_type === "Personal Vehicle" && flt(card.mileage && card.mileage.distance) > 0) {
				if (!card.mileage.driver) out.push(__("Say who is driving the personal vehicle."));
			}
			if (tp_time_part(v.arrival_datetime) && backwards(v.pickup_datetime, v.arrival_datetime)) {
				out.push(__("A drive arrives before it leaves."));
			}
		});
		this.state.freight.forEach((item) => {
			if (!item.carrier) out.push(__("Every shipment needs its carrier."));
			if (backwards(item.pickup_from, item.pickup_to) || backwards(item.delivery_from, item.delivery_to)) {
				out.push(__("A pickup or delivery window ends before it starts."));
			}
			if (item.paid_by === "Employee" && !item.paid_by_traveler) out.push(__("Say which person paid."));
		});
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				if (!card.members.length) out.push(__("Every booking needs at least one person ticked."));
				if (card.values.paid_by === "Employee" && !card.values.paid_by_traveler) {
					out.push(__("Say which person paid."));
				}
			});
		});
		this.state.stops.forEach((stop) => {
			if (!stop.date || !stop.activity_description) out.push(__("Every stop needs a day and what is happening."));
			if (stop.time && stop.end_time && stop.end_time < stop.time) out.push(__("A stop ends before it starts."));
		});
		return Array.from(new Set(out));
	}

	payload() {
		const s = this.state;
		const trip = Object.assign({}, s.trip);
		delete trip.trip_description;
		if (s.description_changed) trip.trip_description = tp_text_to_html(s.description_text);
		const bookings = {};
		Object.keys(s.bookings).forEach((table) => {
			bookings[table] = s.bookings[table].map((card) => ({
				group: card.group,
				label: this.card_label(card),
				values: card.values,
				changed: Array.from(card.changed),
				members: card.members.map((m) => ({ name: m.name || null, traveler: m.traveler, ref: m.ref || "" })),
				mileage: card.mileage || null,
			}));
		});
		return {
			trip: trip,
			status: s.status,
			travelers: s.travelers.map((t) => ({
				employee: t.employee,
				is_trip_lead: t.is_trip_lead ? 1 : 0,
				from_date: t.from_date || "",
				to_date: t.to_date || "",
			})),
			bookings: bookings,
			freight: s.freight.map((item) => {
				const out = { name: item.name || null };
				TP_FREIGHT.forEach((field) => {
					out[field] = item[field] == null ? "" : item[field];
				});
				return out;
			}),
			stops: tp_sorted_stops(s.stops).map((stop) => ({
				name: stop.name || null,
				date: stop.date || "",
				time: stop.time || "",
				end_time: stop.end_time || "",
				activity_description: stop.activity_description || "",
				related_party_doctype: stop.related_party_doctype || "",
				related_party_name: stop.related_party_name || "",
				location: stop.location || "",
				// Typed but never picked from Google: the server finds or makes the
				// Travel POI by this name (planner.apply_plan).
				location_text: stop.location ? "" : stop.location_text || "",
			})),
		};
	}

	save(options) {
		// Resolves true when the page is safe to move on from: saved, nothing to save, or too
		// early to save (no crew yet). Resolves false when the save was refused.
		options = options || {};
		if (!this.state || !this.state.can_write) return Promise.resolve(true);
		if (this.saving) return this.saving;
		if (!this.state.name && !this.can_create()) return Promise.resolve(true);
		if (!this.is_dirty() && !options.force) return Promise.resolve(true);

		const problems = this.problems();
		if (problems.length) {
			if (!options.quiet) {
				frappe.msgprint({
					title: __("A few things to fill in first"),
					message: `<ul>${problems.map((p) => `<li>${tp_esc(p)}</li>`).join("")}</ul>`,
					indicator: "orange",
				});
			}
			return Promise.resolve(false);
		}

		const was_new = !this.state.name;
		this.set_save_state("saving");
		this.saving = frappe
			.call({
				method: "erpnext_enhancements.travel_management.planner.save_plan",
				args: {
					plan: JSON.stringify(this.payload()),
					trip: this.state.name || undefined,
					modified: this.state.modified || undefined,
				},
			})
			.then(
				(r) => {
					this.saving = null;
					const fresh = r && r.message;
					if (!fresh) return false;
					this.adopt(fresh);
					this.set_save_state("saved");
					if ((fresh.notes || []).length) {
						frappe.msgprint({ title: __("Saved, with notes"), message: fresh.notes.map(tp_esc).join("<br>") });
					}
					// Only while still on the new-trip route: a save fired by leaving the page
					// must not pull you back to it.
					if (was_new && frappe.get_route()[0] === "plan-a-trip" && frappe.utils.get_url_arg("new")) {
						frappe.set_route("plan-a-trip", { trip: fresh.name });
					}
					return true;
				},
				() => {
					// The server's own message is already on screen (frappe.call shows it).
					this.saving = null;
					this.set_save_state("error");
					return false;
				}
			);
		return this.saving;
	}

	save_quietly() {
		if (this.state && this.is_dirty()) this.save({ quiet: true });
	}

	go(index) {
		if (index === this.step) return;
		if (!this.state) return;
		// Leaving the first step needs the basics, even before there is anything to save.
		if (this.step === 0 && index > 0) {
			const t = this.state.trip;
			if (!t.purpose || !t.start_date || !t.end_date || t.end_date < t.start_date) {
				frappe.msgprint({
					title: __("The trip first"),
					message: __("Say what the trip is for and pick its first and last day."),
					indicator: "orange",
				});
				return;
			}
		}
		if (index > 1 && !this.crew().length) {
			frappe.msgprint({
				title: __("Who's going?"),
				message: __("Pick at least one person on the Who's going step first."),
				indicator: "orange",
			});
			this.step = 1;
			this.render();
			return;
		}
		this.save().then((ok) => {
			if (!ok) return;
			this.step = Math.max(0, Math.min(TP_STEPS.length - 1, index));
			this.render();
			frappe.utils.scroll_to(0);
		});
	}

	set_save_state(kind) {
		if (!this.save_state_el) return;
		const labels = {
			saving: __("Saving..."),
			saved: __("Saved"),
			error: __("Not saved"),
		};
		this.save_state_el.toggleClass("tp-err", kind === "error").html(`<span>${labels[kind] || ""}</span>`);
		if (kind === "saved") {
			setTimeout(() => {
				if (this.save_state_el) this.save_state_el.html("");
			}, 1600);
		}
	}

	// ------------------------------------------------------------------ gaps

	gaps() {
		return (this.state && this.state.gaps) || [];
	}

	gaps_for_step(key) {
		return this.gaps().filter((g) => g.step === key);
	}

	gap_text(gap) {
		const who = tp_esc(gap.employee_name || "");
		const what = tp_esc(gap.label || __("A booking"));
		if (gap.check === "travel") {
			return gap.kind === "Outbound"
				? __("{0} has no way there yet.", [who])
				: __("{0} has no way back yet.", [who]);
		}
		if (gap.check === "lodging" && gap.kind === "nights") {
			const nights = (gap.nights || []).map((n) => tp_esc(tp_pretty_date(n))).join(", ");
			return __("{0} has nowhere to sleep on {1}.", [who, nights]);
		}
		if (gap.check === "lodging") {
			return __("{0}: the check-in and check-out days are missing.", [what]);
		}
		if (gap.check === "confirmation" && gap.table === "freight") {
			return __("{0}: no tracking, PRO or BOL number.", [what]);
		}
		if (gap.check === "confirmation") {
			const names = (gap.employee_names || []).map(tp_esc).join(", ");
			return names
				? __("{0}: no confirmation number for {1}.", [what, names])
				: __("{0}: no confirmation number.", [what]);
		}
		if (gap.check === "cost") {
			return __("{0}: no cost entered.", [what]);
		}
		return what;
	}

	render_gap_list($parent, gaps, with_fix) {
		gaps.forEach((gap) => {
			const $gap = $(`<div class="tp-gap"><span>${this.gap_text(gap)}</span></div>`).appendTo($parent);
			if (with_fix) {
				$(`<button class="tp-btn-link">${__("Fix")} &rarr;</button>`)
					.appendTo($gap)
					.on("click", () => this.jump_to(gap.step));
			}
		});
	}

	// ------------------------------------------------------------------ rendering

	remove_chrome() {
		this.page.main.find(".tp-nav, .tp-savestate").remove();
		this.save_state_el = null;
	}

	render() {
		if (!this.state) return;
		// Each Google suggestion box lives on <body>, outside this page; drop the old
		// ones before their inputs are thrown away.
		(this.suggesters || []).forEach((suggester) => suggester.destroy());
		this.suggesters = [];
		this.body.empty();
		this.render_header();
		this.render_tabs();
		const $step = $('<div class="tp-step"></div>').appendTo(this.body);
		const step = TP_STEPS[this.step];
		if (!this.state.can_write) {
			$(`<div class="tp-gap">${__("You can look at this trip but not change it.")}</div>`).appendTo($step);
		}
		({
			trip: () => this.step_trip($step),
			crew: () => this.step_crew($step),
			there: () => this.step_legs($step, step),
			back: () => this.step_legs($step, step),
			lodging: () => this.step_lodging($step),
			around: () => this.step_legs($step, step),
			freight: () => this.step_freight($step),
			schedule: () => this.step_schedule($step),
			review: () => this.step_review($step),
		})[step.key]();
		this.render_nav();
	}

	render_header() {
		const s = this.state;
		const title = s.trip.purpose || __("New trip");
		const dates =
			s.trip.start_date && s.trip.end_date
				? `${tp_pretty_date(s.trip.start_date)} - ${tp_pretty_date(s.trip.end_date)}`
				: "";
		const $head = $(`<div class="tp-head">
			<h4>${tp_esc(title)}</h4>
			<span class="tp-pill ${s.status === "Booked" ? "tp-pill-green" : ""}">${tp_esc(__(s.status))}</span>
			<span class="tp-muted">${tp_esc(dates)}${s.name ? ` &middot; ${tp_esc(s.name)}` : ""}</span>
		</div>`).appendTo(this.body);
		if (s.name) {
			$(`<a class="tp-muted" style="margin-left:auto;" href="/app/travel-trip/${encodeURIComponent(s.name)}">${__(
				"Open the full form"
			)}</a>`).appendTo($head);
		}
	}

	render_tabs() {
		const $tabs = $('<div class="tp-tabs"></div>').appendTo(this.body);
		TP_STEPS.forEach((step, index) => {
			const count = this.gaps_for_step(step.key).length;
			$(`<button class="tp-tab ${index === this.step ? "tp-active" : ""}">${tp_esc(step.title)}${
				count ? `<span class="tp-badge">${count}</span>` : ""
			}</button>`)
				.appendTo($tabs)
				.on("click", () => this.go(index));
		});
	}

	render_nav() {
		this.remove_chrome();
		const nav = $('<div class="tp-nav"></div>').appendTo(this.page.main);
		this.save_state_el = $('<div class="tp-savestate"></div>').appendTo(this.page.main);
		$(`<button>${__("Back")}</button>`)
			.appendTo(nav)
			.prop("disabled", this.step === 0)
			.on("click", () => this.go(this.step - 1));
		if (this.step < TP_STEPS.length - 1) {
			$(`<button class="tp-primary">${__("Next")}: ${tp_esc(TP_STEPS[this.step + 1].title)}</button>`)
				.appendTo(nav)
				.on("click", () => this.go(this.step + 1));
		} else {
			$(`<button class="tp-primary">${__("Save")}</button>`)
				.appendTo(nav)
				.on("click", () => this.save().then(() => this.render()));
		}
	}

	step_intro($step, title, help) {
		$(`<div class="tp-step-title">${tp_esc(title)}</div><div class="tp-step-help">${tp_esc(help)}</div>`).appendTo(
			$step
		);
	}

	field($parent, label, required, extra_class) {
		const $field = $(`<div class="tp-field ${extra_class || ""}"><label>${tp_esc(label)}${
			required ? ' <span class="tp-req">*</span>' : ""
		}</label></div>`).appendTo($parent);
		return $field;
	}

	input($parent, type, value, on_change, attrs) {
		const $input = $(`<input type="${type}">`).val(value == null ? "" : value).appendTo($parent);
		Object.entries(attrs || {}).forEach(([key, val]) => $input.attr(key, val));
		$input.on(type === "text" || type === "number" ? "input" : "change", () => on_change($input.val()));
		return $input;
	}

	link($parent, doctype, value, on_change, placeholder) {
		// A Frappe Link control: search, and "Create a new ..." for anyone allowed to create
		// one. set_input seeds the value without firing onchange, so loading a card never
		// marks it edited.
		const $holder = $("<div></div>").appendTo($parent);
		const control = frappe.ui.form.make_control({
			parent: $holder,
			df: {
				fieldtype: "Link",
				options: doctype,
				fieldname: `tp_${doctype.replace(/\W/g, "_").toLowerCase()}_${++this.card_seq}`,
				placeholder: placeholder || "",
				onchange: () => on_change(String(control.value || "").trim()),
			},
			render_input: true,
			only_input: true,
		});
		control.refresh();
		if (value) control.set_input(value);
		return control;
	}

	place($parent, value, on_change, placeholder) {
		// A text box that suggests real places and addresses from Google as you type: the
		// same component the Address form and quick entry use
		// (global_enhancements/address_autocomplete.js, loaded on every desk page). A pick
		// fills in Google's wording, place name and address ("Hilton Las Vegas, Paradise
		// Road, Las Vegas, NV, USA") and passes the point along; typing without picking
		// is still plain text. With no Maps key, or no Places API on it, the component
		// leaves an ordinary text box and says why in the console.
		const $input = this.input($parent, "text", value, (text) => on_change(text.trim(), null), {
			autocomplete: "off",
			placeholder: placeholder || __("Start typing a place or an address"),
		});
		const lib = window.erpnext_enhancements && window.erpnext_enhancements.address_autocomplete;
		if (lib && lib.attach) {
			const suggester = lib.attach($input[0], {
				on_pick: (_values, meta) => {
					const text = meta.label || meta.formatted_address || $input.val();
					$input.val(text);
					on_change(text, meta);
				},
			});
			if (suggester) this.suggesters.push(suggester);
		}
		return $input;
	}

	time_input($parent, label, value, on_change) {
		// A Time column holds "15:00:00"; the browser's time box shows it as 3:00 PM.
		return this.input(this.field($parent, label), "time", value ? String(value).slice(0, 5) : "", (v) =>
			on_change(v ? `${v}:00` : "")
		);
	}

	time_window($parent, label, from, to, on_change) {
		// One day with a start and an end time — a delivery window, "Tue Oct 6, 8 AM – 12 PM".
		// Both ends share the day; a window that runs across days is for the full form.
		const $field = this.field($parent, label, false, "tp-wide");
		const $pair = $('<div class="tp-pair"></div>').appendTo($field);
		const $date = $('<input type="date">').val(tp_date_part(from || to)).appendTo($pair);
		const $from = $('<input type="time">').val(tp_time_part(from)).appendTo($pair);
		$(`<span class="tp-muted" style="align-self:center;">${__("to")}</span>`).appendTo($pair);
		const $to = $('<input type="time">').val(tp_time_part(to)).appendTo($pair);
		const fire = () => {
			const date = $date.val();
			on_change(tp_join_datetime(date, $from.val()), date && $to.val() ? tp_join_datetime(date, $to.val()) : "");
		};
		$date.on("change", fire);
		$from.on("change", fire);
		$to.on("change", fire);
	}

	segmented($parent, options, current, on_pick) {
		const $seg = $('<div class="tp-seg"></div>').appendTo($parent);
		options.forEach((option) => {
			$(`<button type="button" class="${option.value === current ? "tp-on" : ""}">${tp_esc(option.label)}</button>`)
				.appendTo($seg)
				.on("click", () => on_pick(option.value));
		});
		return $seg;
	}

	// ------------------------------------------------------------------ step 1: the trip

	step_trip($step) {
		const t = this.state.trip;
		this.step_intro(
			$step,
			__("The trip"),
			__("What the trip is for, when it is, and which job it belongs to.")
		);
		const $card = $('<div class="tp-card"></div>').appendTo($step);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);

		this.input(this.field($grid, __("What is the trip for?"), true, "tp-wide"), "text", t.purpose, (v) => {
			t.purpose = v;
		}).attr("placeholder", __("e.g. Install the lobby fountain at the Hilton Las Vegas"));

		const $type = this.field($grid, __("Kind of trip"), true, "tp-wide");
		this.segmented(
			$type,
			(this.lookups.travel_types || []).map((v) => ({ value: v, label: __(v) })),
			t.travel_type,
			(v) => {
				t.travel_type = v;
				this.render();
			}
		);

		this.input(this.field($grid, __("First day"), true), "date", t.start_date, (v) => {
			const end = !t.end_date || t.end_date < v ? v : t.end_date;
			this.move_trip_dates(v, end);
			$grid.find('input[data-tp="end"]').val(end);
		});
		this.input(
			this.field($grid, __("Last day"), true),
			"date",
			t.end_date,
			(v) => this.move_trip_dates(t.start_date, v),
			{ "data-tp": "end" }
		);

		const $for = this.field($grid, __("Which job is it for?"), false, "tp-wide");
		const $pair = $('<div class="tp-grid"></div>').appendTo($for);
		const $select = $("<select></select>").appendTo($('<div class="tp-field"></div>').appendTo($pair));
		[""].concat(this.lookups.travel_for_doctypes || []).forEach((doctype) => {
			$("<option></option>")
				.val(doctype)
				.text(doctype ? __(doctype) : __("Not tied to a job"))
				.appendTo($select);
		});
		$select.val(t.travel_for_doctype || "");
		const $link_holder = $('<div class="tp-field"></div>').appendTo($pair);
		const mount = () => {
			$link_holder.empty();
			if (!t.travel_for_doctype) return;
			this.link($link_holder, t.travel_for_doctype, t.travel_for_name, (v) => {
				t.travel_for_name = v;
			}, __("Search {0}", [__(t.travel_for_doctype)]));
		};
		$select.on("change", () => {
			t.travel_for_doctype = $select.val();
			t.travel_for_name = "";
			mount();
		});
		mount();

		if ((this.lookups.companies || []).length > 1) {
			const $company = $("<select></select>").appendTo(this.field($grid, __("Company"), true));
			this.lookups.companies.forEach((c) => $("<option></option>").val(c).text(c).appendTo($company));
			$company.val(t.company || this.lookups.default_company).on("change", () => {
				t.company = $company.val();
			});
		}

		const $bill = $(`<label class="tp-check tp-wide"><input type="checkbox"> ${__(
			"Bill these travel costs to the customer"
		)}</label>`).appendTo($grid);
		$bill.find("input")
			.prop("checked", !!t.billable)
			.on("change", (e) => {
				t.billable = e.target.checked ? 1 : 0;
			});

		const $notes = this.field($grid, __("Notes for the crew"), false, "tp-wide");
		$("<textarea></textarea>")
			.val(this.state.description_text || "")
			.appendTo($notes)
			.on("input", (e) => {
				this.state.description_text = e.target.value;
				this.state.description_changed = true;
			});
	}

	// ------------------------------------------------------------------ step 2: crew

	step_crew($step) {
		this.step_intro(
			$step,
			__("Who's going"),
			__(
				"Tick everyone on the trip and pick the trip lead. Change someone's dates only if they arrive late or leave early."
			)
		);
		const $search = $(`<input type="text" class="form-control" placeholder="${__("Find a person")}">`).appendTo(
			$step
		);
		const $list = $('<div style="margin-top:10px;"></div>').appendTo($step);
		const t = this.state.trip;

		const draw = () => {
			$list.empty();
			const term = ($search.val() || "").toLowerCase();
			// Alphabetical and stable: a row must not jump away from the pointer when ticked.
			(this.lookups.employees || [])
				.filter((e) => !term || `${e.employee_name} ${e.designation || ""}`.toLowerCase().includes(term))
				.forEach((employee) => {
					const traveler = this.crew().find((x) => x.employee === employee.name);
					const $row = $(`<div class="tp-crew-row ${traveler ? "tp-on" : ""}">
						<input type="checkbox" ${traveler ? "checked" : ""}>
						<div class="tp-crew-name"><b>${tp_esc(employee.employee_name || employee.name)}</b>
							<div class="tp-muted">${tp_esc(employee.designation || "")}</div>
							<div class="tp-crew-extra"></div>
						</div>
					</div>`).appendTo($list);
					$row.find('input[type="checkbox"]').on("change", (e) => {
						if (e.target.checked) this.add_traveler(employee);
						else this.remove_traveler(employee.name);
						draw();
					});
					if (!traveler) return;
					const $extra = $row.find(".tp-crew-extra");
					$(`<label class="tp-check"><input type="radio" name="tp-lead" ${
						traveler.is_trip_lead ? "checked" : ""
					}> ${__("Trip lead")}</label>`)
						.appendTo($extra)
						.find("input")
						.on("change", () => {
							this.crew().forEach((x) => {
								x.is_trip_lead = x.employee === employee.name ? 1 : 0;
							});
						});
					// The controller stores the trip dates on every traveler who has none of
					// their own, so "own dates" means different from the trip's, not "set".
					// `_own` holds the box open between ticking it and changing a date.
					const own = traveler._own || tp_has_own_dates(traveler, t);
					const $own = $(`<label class="tp-check"><input type="checkbox" ${own ? "checked" : ""}> ${__(
						"Different dates"
					)}</label>`).appendTo($extra);
					if (own) {
						const $from = $('<input type="date">').val(traveler.from_date || t.start_date).appendTo($extra);
						$('<span class="tp-muted">&ndash;</span>').appendTo($extra);
						const $to = $('<input type="date">').val(traveler.to_date || t.end_date).appendTo($extra);
						$from.attr({ min: t.start_date, max: t.end_date }).on("change", () => {
							traveler.from_date = $from.val();
						});
						$to.attr({ min: t.start_date, max: t.end_date }).on("change", () => {
							traveler.to_date = $to.val();
						});
					}
					$own.find("input").on("change", (e) => {
						traveler._own = e.target.checked;
						if (e.target.checked) {
							traveler.from_date = t.start_date;
							traveler.to_date = t.end_date;
						} else {
							traveler.from_date = "";
							traveler.to_date = "";
						}
						draw();
					});
				});
		};
		$search.on("input", draw);
		draw();
	}

	move_trip_dates(start, end) {
		// Travelers who were on the trip's dates follow them. Their stored dates are the old
		// trip dates (the controller fills blanks), so without this a longer trip would leave
		// the whole crew on the old days. Blank means "the trip's dates" to the server.
		const t = this.state.trip;
		this.crew().forEach((traveler) => {
			if (traveler.from_date === t.start_date) traveler.from_date = "";
			if (traveler.to_date === t.end_date) traveler.to_date = "";
		});
		t.start_date = start;
		t.end_date = end;
	}

	add_traveler(employee) {
		if (this.crew().some((x) => x.employee === employee.name)) return;
		this.state.travelers.push({
			name: null,
			employee: employee.name,
			employee_name: employee.employee_name,
			is_trip_lead: this.crew().length ? 0 : 1,
			from_date: "",
			to_date: "",
		});
	}

	remove_traveler(employee) {
		// Take them off every booking too, or the server refuses a booking for someone who
		// is not in the crew.
		this.state.travelers = this.crew().filter((x) => x.employee !== employee);
		if (this.crew().length && !this.crew().some((x) => x.is_trip_lead)) this.crew()[0].is_trip_lead = 1;
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				card.members = card.members.filter((m) => m.traveler !== employee);
				if (card.values.paid_by_traveler === employee) {
					this.set_value(card, "paid_by", "Company");
					this.set_value(card, "paid_by_traveler", "");
				}
				if (card.mileage && card.mileage.driver === employee) card.mileage.driver = "";
			});
		});
	}

	// ------------------------------------------------------------------ steps 3, 4, 6: getting there / back / around

	step_legs($step, step) {
		const leg = step.leg;
		const help = {
			Outbound: __("How each person gets to the job: flights, a company truck, their own car. Tick who is on each one."),
			Return: __("How each person gets home. Tick who is on each one."),
			"During Trip": __("Rental cars, trucks and rides used while you are there. Optional."),
		}[leg];
		this.step_intro($step, step.title, help);

		if (leg !== "During Trip") {
			const $coverage = $('<div class="tp-chips" style="margin-bottom:14px;"></div>').appendTo($step);
			this.draw_leg_coverage($coverage, leg);
		}

		const cards = this.leg_cards(leg);
		cards.forEach((card) => {
			if (card.table === "flights") this.flight_card($step, card);
			else this.ride_card($step, card);
		});

		const $add = $('<div class="tp-add"></div>').appendTo($step);
		if (leg !== "During Trip") {
			$(`<button class="tp-btn">+ ${__("Add a flight")}</button>`)
				.appendTo($add)
				.on("click", () => {
					this.cards("flights").push(this.new_card("flights", leg, this.uncovered(leg)));
					this.render();
				});
			$(`<button class="tp-btn">+ ${__("Add a drive")}</button>`)
				.appendTo($add)
				.on("click", () => {
					const card = this.new_card("ground_transport", leg, this.uncovered(leg));
					card.values.transport_type = "Company Fleet";
					this.cards("ground_transport").push(card);
					this.render();
				});
		} else {
			$(`<button class="tp-btn">+ ${__("Add a rental or ride")}</button>`)
				.appendTo($add)
				.on("click", () => {
					const card = this.new_card("ground_transport", leg, this.crew().map((x) => x.employee));
					card.values.transport_type = "Rental/Third Party";
					this.cards("ground_transport").push(card);
					this.render();
				});
		}
		if (leg === "Return" && !cards.length && this.leg_cards("Outbound").length) {
			$(`<button class="tp-btn">${__("Copy the way there, reversed")}</button>`)
				.appendTo($add)
				.on("click", () => {
					this.copy_reversed();
					this.render();
				});
		}

		// Per-booking gaps are already on their cards; the step keeps the per-person ones.
		this.render_gap_list(
			$step,
			this.gaps_for_step(step.key).filter((g) => !g.group),
			false
		);
	}

	draw_leg_coverage($coverage, leg) {
		$coverage.empty();
		const covered = new Set();
		this.leg_cards(leg).forEach((card) => card.members.forEach((m) => covered.add(m.traveler)));
		this.crew().forEach((t) => {
			const ok = covered.has(t.employee);
			$(`<span class="tp-chip ${ok ? "tp-ok" : "tp-miss"}">${ok ? "&#10003;" : "&#10007;"} ${tp_esc(
				t.employee_name || t.employee
			)}</span>`).appendTo($coverage);
		});
	}

	copy_reversed() {
		this.leg_cards("Outbound").forEach((out) => {
			const members = out.members.map((m) => m.traveler);
			const card = this.new_card(out.table, "Return", members);
			if (out.table === "flights") {
				card.values.airline = out.values.airline;
				card.values.departure_airport = out.values.arrival_airport;
				card.values.arrival_airport = out.values.departure_airport;
			} else {
				card.values.transport_type = out.values.transport_type;
				card.values.supplier = out.values.supplier;
				card.values.vehicle = out.values.vehicle;
				card.values.pickup_location = out.values.dropoff_location;
				card.values.dropoff_location = out.values.pickup_location;
				if (out.mileage) card.mileage = Object.assign({}, out.mileage, { claimed: false });
			}
			card.values.paid_by = out.values.paid_by;
			card.values.paid_by_traveler = out.values.paid_by_traveler;
			if (this.state.trip.end_date) {
				const field = out.table === "flights" ? "departure_time" : "pickup_datetime";
				card.values[field] = tp_join_datetime(this.state.trip.end_date, "");
			}
			this.cards(out.table).push(card);
		});
	}

	card_shell($parent, card, title) {
		const bad = this.gaps().some((g) => g.group === card.group);
		const $card = $(`<div class="tp-card ${bad ? "tp-bad" : ""}">
			<div class="tp-card-head"><b>${tp_esc(title)}</b></div>
		</div>`).appendTo($parent);
		if (!card.protected) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($card.find(".tp-card-head"))
				.on("click", () => {
					frappe.confirm(__("Remove this booking for everyone on it?"), () => {
						const list = this.cards(card.table);
						list.splice(list.indexOf(card), 1);
						this.render();
					});
				});
		} else {
			$(`<span class="tp-muted" style="margin-left:auto;">${__("On an Expense Claim")}</span>`).appendTo(
				$card.find(".tp-card-head")
			);
		}
		return $card;
	}

	card_label(card) {
		const v = card.values;
		if (card.table === "flights") return [v.airline, v.flight_number].filter(Boolean).join(" ") || __("Flight");
		if (card.table === "accommodations") return v.hotel_lodging || __("Room");
		const type = TP_RIDE_TYPES.find((x) => x.value === v.transport_type);
		return v.supplier || v.vehicle || (type ? type.label : __("Drive"));
	}

	datetime_pair($parent, label, value, on_change, required) {
		const $field = this.field($parent, label, required);
		const $pair = $('<div class="tp-pair"></div>').appendTo($field);
		const $date = $('<input type="date">').val(tp_date_part(value)).appendTo($pair);
		const $time = $('<input type="time">').val(tp_time_part(value)).appendTo($pair);
		const fire = () => on_change(tp_join_datetime($date.val(), $time.val()));
		$date.on("change", fire);
		$time.on("change", fire);
	}

	flight_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, `${__("Flight")}: ${this.card_label(card)}`);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Airline"), true),
			"Supplier",
			v.airline,
			(val) => this.set_value(card, "airline", val),
			__("e.g. Southwest Airlines")
		);
		this.input(this.field($grid, __("Flight number"), true), "text", v.flight_number, (val) =>
			this.set_value(card, "flight_number", val.trim())
		).attr("placeholder", "WN 1234");
		this.input(this.field($grid, __("From (airport)")), "text", v.departure_airport, (val) =>
			this.set_value(card, "departure_airport", val.trim())
		).attr("placeholder", "PHX");
		this.input(this.field($grid, __("To (airport)")), "text", v.arrival_airport, (val) =>
			this.set_value(card, "arrival_airport", val.trim())
		).attr("placeholder", "LAS");
		this.datetime_pair($grid, __("Takes off"), v.departure_time, (val) => this.set_value(card, "departure_time", val));
		this.datetime_pair($grid, __("Lands"), v.arrival_time, (val) => this.set_value(card, "arrival_time", val));
		this.members_block($card, card, __("Who's on this flight?"));
		this.refs_block($card, card);
		this.money_block($card, card, __("Total for all tickets"));
		this.card_gaps($card, card);
	}

	ride_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, this.card_label(card));
		const $type = this.field($card, __("What kind?"), true);
		this.segmented($type, TP_RIDE_TYPES, v.transport_type, (value) => {
			this.set_value(card, "transport_type", value);
			if (value === "Company Fleet") {
				this.set_value(card, "paid_by", "Company");
				this.set_value(card, "paid_by_traveler", "");
			}
			this.render();
		});
		const $grid = $('<div class="tp-grid" style="margin-top:10px;"></div>').appendTo($card);
		const unbooked = TP_UNBOOKED.includes(v.transport_type);
		if (v.transport_type === "Company Fleet") {
			this.link(
				this.field($grid, __("Vehicle (optional)")),
				"Vehicle",
				v.vehicle,
				(val) => this.set_value(card, "vehicle", val)
			);
		} else if (!unbooked) {
			this.link(
				this.field($grid, v.transport_type === "Taxi/Rideshare" ? __("Company") : __("Rental company")),
				"Supplier",
				v.supplier,
				(val) => this.set_value(card, "supplier", val),
				v.transport_type === "Taxi/Rideshare" ? "Uber" : "Enterprise"
			);
		}
		this.place(this.field($grid, v.leg === "During Trip" ? __("Pick up at") : __("Leaving from")), v.pickup_location, (val) =>
			this.set_value(card, "pickup_location", val)
		);
		this.place(this.field($grid, v.leg === "During Trip" ? __("Drop off at") : __("Going to")), v.dropoff_location, (val) =>
			this.set_value(card, "dropoff_location", val)
		);
		this.datetime_pair($grid, v.leg === "During Trip" ? __("Pick up") : __("Leaves"), v.pickup_datetime, (val) =>
			this.set_value(card, "pickup_datetime", val)
		);
		if (v.transport_type === "Company Fleet" || v.transport_type === "Personal Vehicle") {
			// A drive's time range: when it leaves and when it gets there.
			this.datetime_pair($grid, __("Arrives"), v.arrival_datetime, (val) => this.set_value(card, "arrival_datetime", val));
		}
		if (!unbooked) {
			this.datetime_pair($grid, __("Return by"), v.return_datetime, (val) =>
				this.set_value(card, "return_datetime", val)
			);
		}
		if (v.transport_type !== "Taxi/Rideshare") {
			// Our own load: the fountain on the trailer, a rented box truck of materials.
			// Carrier shipments are on the Freight step instead.
			const $haul = this.field($grid, __("Hauling (optional)"), false, "tp-wide");
			$("<textarea rows=\"2\"></textarea>")
				.val(v.cargo || "")
				.attr("placeholder", __("What's on the truck or trailer, e.g. 12 ft basin, pump vault, tools"))
				.appendTo($haul)
				.on("input", (e) => this.set_value(card, "cargo", e.target.value));
		}
		this.members_block($card, card, __("Who's riding?"));
		if (v.transport_type === "Personal Vehicle") this.mileage_block($card, card);
		if (!unbooked) {
			this.refs_block($card, card);
			this.money_block($card, card, __("Total cost"));
		}
		this.card_gaps($card, card);
	}

	members_block($card, card, label) {
		const $field = this.field($card, label, true);
		$field.css("margin-top", "12px");
		const $chips = $('<div class="tp-chips"></div>').appendTo($field);
		const on = new Set(card.members.map((m) => m.traveler));
		const whole_crew = card.members.some((m) => !m.traveler);
		if (whole_crew) {
			$(`<div class="tp-muted">${__(
				"Entered on the form for the whole crew. Tick people to give each of them their own booking."
			)}</div>`).appendTo($field);
		}
		this.crew().forEach((t) => {
			const checked = whole_crew || on.has(t.employee);
			$(`<span class="tp-chip ${checked ? "tp-on" : ""}">${checked ? "&#10003; " : ""}${tp_esc(
				t.employee_name || t.employee
			)}</span>`)
				.appendTo($chips)
				.on("click", () => {
					if (card.protected) return;
					if (whole_crew) {
						// Leaving "whole crew": everyone currently implied, minus this person.
						const ref = card.members[0] ? card.members[0].ref : "";
						card.members = this.crew()
							.filter((x) => x.employee !== t.employee)
							.map((x) => ({ name: null, traveler: x.employee, ref: ref }));
					} else if (on.has(t.employee)) {
						card.members = card.members.filter((m) => m.traveler !== t.employee);
					} else {
						const ref = card.same_ref && card.members[0] ? card.members[0].ref : "";
						card.members.push({ name: null, traveler: t.employee, ref: ref });
					}
					this.render();
				});
		});
	}

	refs_block($card, card) {
		const $field = this.field($card, __("Confirmation number"));
		$field.css("margin-top", "12px");
		if (card.members.length > 1) {
			$(`<label class="tp-check"><input type="checkbox" ${card.same_ref ? "checked" : ""}> ${__(
				"Same for everyone"
			)}</label>`)
				.appendTo($field)
				.find("input")
				.on("change", (e) => {
					card.same_ref = e.target.checked;
					if (card.same_ref) {
						const first = (card.members.find((m) => m.ref) || {}).ref || "";
						card.members.forEach((m) => {
							m.ref = first;
						});
					}
					this.render();
				});
		}
		if (card.same_ref || card.members.length <= 1) {
			const first = card.members[0] ? card.members[0].ref : "";
			this.input($field, "text", first, (val) => {
				card.members.forEach((m) => {
					m.ref = val.trim();
				});
			});
			return;
		}
		const $refs = $('<div class="tp-refs"></div>').appendTo($field);
		card.members.forEach((member) => {
			const $row = $(`<div class="tp-ref-row"><span>${tp_esc(this.crew_name(member.traveler))}</span></div>`).appendTo(
				$refs
			);
			$('<input type="text">')
				.val(member.ref || "")
				.appendTo($row)
				.on("input", (e) => {
					member.ref = e.target.value.trim();
				});
		});
	}

	money_block($card, card, cost_label) {
		const v = card.values;
		const $grid = $('<div class="tp-grid" style="margin-top:12px;"></div>').appendTo($card);
		const $cost = this.field($grid, cost_label);
		const $hint = $('<div class="tp-muted"></div>');
		const hint = () => {
			const n = card.members.length;
			$hint.text(n > 1 && flt(v.cost) ? __("About {0} each", [format_currency(flt(v.cost) / n, this.lookups.currency)]) : "");
		};
		this.input($cost, "number", v.cost || "", (val) => {
			this.set_value(card, "cost", flt(val));
			hint();
		}, { min: "0", step: "0.01", inputmode: "decimal" });
		$hint.appendTo($cost);
		hint();
		const $paid = $("<select></select>").appendTo(this.field($grid, __("Who paid?")));
		$("<option></option>").val("Company").text(__("The company")).appendTo($paid);
		this.crew().forEach((t) => {
			$("<option></option>")
				.val(t.employee)
				.text(__("{0} (to reimburse)", [t.employee_name || t.employee]))
				.appendTo($paid);
		});
		$paid.val(v.paid_by === "Employee" && v.paid_by_traveler ? v.paid_by_traveler : "Company").on("change", () => {
			const who = $paid.val();
			this.set_value(card, "paid_by", who === "Company" ? "Company" : "Employee");
			this.set_value(card, "paid_by_traveler", who === "Company" ? "" : who);
		});
	}

	mileage_block($card, card) {
		card.mileage = card.mileage || { driver: "", distance: 0 };
		const m = card.mileage;
		const $grid = $('<div class="tp-grid" style="margin-top:12px;"></div>').appendTo($card);
		const $driver = $("<select></select>").appendTo(this.field($grid, __("Who's driving?")));
		$("<option></option>").val("").text(__("Pick the driver")).appendTo($driver);
		card.members.forEach((member) => {
			if (!member.traveler) return;
			$("<option></option>").val(member.traveler).text(this.crew_name(member.traveler)).appendTo($driver);
		});
		$driver.val(m.driver || "").prop("disabled", !!m.claimed).on("change", () => {
			m.driver = $driver.val();
		});
		const $miles = this.field($grid, __("Miles for this drive"));
		const $note = $('<div class="tp-muted"></div>');
		const note = () => {
			const rate = flt(this.lookups.mileage_rate);
			$note.text(
				rate && flt(m.distance)
					? __("Reimbursed at {0} a mile: {1}", [
							format_currency(rate, this.lookups.currency),
							format_currency(rate * flt(m.distance), this.lookups.currency),
					  ])
					: ""
			);
		};
		this.input($miles, "number", m.distance || "", (val) => {
			m.distance = flt(val);
			note();
		}, { min: "0", step: "1", inputmode: "decimal" }).prop("disabled", !!m.claimed);
		$note.appendTo($miles);
		note();
	}

	card_gaps($card, card) {
		this.render_gap_list(
			$card,
			this.gaps().filter((g) => g.group === card.group),
			false
		);
	}

	// ------------------------------------------------------------------ step 5: lodging

	step_lodging($step) {
		this.step_intro(
			$step,
			__("Where everyone sleeps"),
			__("Add each room and tick who is in it. The grid shows who still needs a bed on which night.")
		);
		const $matrix = $('<div class="tp-matrix"></div>').appendTo($step);
		this.draw_nights($matrix);

		this.cards("accommodations").forEach((card) => this.room_card($step, card));

		const $add = $('<div class="tp-add"></div>').appendTo($step);
		$(`<button class="tp-btn">+ ${__("Add a room")}</button>`)
			.appendTo($add)
			.on("click", () => {
				const last = this.cards("accommodations").slice(-1)[0];
				const card = this.new_card("accommodations", null, this.uncovered(null));
				// A second room is usually at the same hotel on the same nights.
				card.values.hotel_lodging = last ? last.values.hotel_lodging : "";
				card.values.check_in_date = last ? last.values.check_in_date : this.state.trip.start_date;
				card.values.check_out_date = last ? last.values.check_out_date : this.state.trip.end_date;
				card.values.paid_by = last ? last.values.paid_by : "Company";
				card.values.paid_by_traveler = last ? last.values.paid_by_traveler : "";
				this.cards("accommodations").push(card);
				this.render();
			});
		this.render_gap_list($step, this.gaps_for_step("lodging").filter((g) => !g.group), false);
	}

	draw_nights($matrix) {
		const t = this.state.trip;
		const nights = tp_days_between(t.start_date, t.end_date).slice(0, -1);
		if (!nights.length || !this.crew().length) {
			$matrix.html(`<div class="tp-muted">${__("A one-day trip: nobody needs a bed.")}</div>`);
			return;
		}
		const head = nights.map((n) => `<th>${tp_esc(moment(n).format("ddd D"))}</th>`).join("");
		const rows = this.crew()
			.map((traveler) => {
				const from = traveler.from_date || t.start_date;
				const to = traveler.to_date || t.end_date;
				const cells = nights
					.map((night) => {
						if (night < from || night >= to) return "<td></td>";
						const ok = this.cards("accommodations").some(
							(card) =>
								card.members.some((m) => !m.traveler || m.traveler === traveler.employee) &&
								card.values.check_in_date &&
								card.values.check_out_date &&
								card.values.check_in_date <= night &&
								night < card.values.check_out_date
						);
						return ok ? '<td class="tp-y">&#10003;</td>' : '<td class="tp-n">&#10007;</td>';
					})
					.join("");
				return `<tr><td>${tp_esc(traveler.employee_name || traveler.employee)}</td>${cells}</tr>`;
			})
			.join("");
		$matrix.html(`<table><thead><tr><th>${__("Night of")}</th>${head}</tr></thead><tbody>${rows}</tbody></table>`);
	}

	room_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, `${__("Room")}: ${this.card_label(card)}`);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Hotel"), true, "tp-wide"),
			"Supplier",
			v.hotel_lodging,
			(val) => this.set_value(card, "hotel_lodging", val),
			__("e.g. Hilton Las Vegas")
		);
		if (card.address) {
			$(`<div class="tp-muted tp-wide">${tp_esc(card.address)}</div>`).appendTo($grid);
		}
		const redraw = () => this.draw_nights(this.body.find(".tp-matrix"));
		this.input(this.field($grid, __("Check in"), true), "date", v.check_in_date, (val) => {
			this.set_value(card, "check_in_date", val);
			redraw();
		});
		this.time_input($grid, __("Check-in time (optional)"), v.check_in_time, (val) =>
			this.set_value(card, "check_in_time", val)
		);
		this.input(this.field($grid, __("Check out"), true), "date", v.check_out_date, (val) => {
			this.set_value(card, "check_out_date", val);
			redraw();
		});
		this.time_input($grid, __("Check-out time (optional)"), v.check_out_time, (val) =>
			this.set_value(card, "check_out_time", val)
		);
		this.members_block($card, card, __("Who's in this room?"));
		this.refs_block($card, card);
		this.money_block($card, card, __("Total for the room, whole stay"));
		this.card_gaps($card, card);
	}

	// ------------------------------------------------------------------ freight

	step_freight($step) {
		this.step_intro(
			$step,
			__("Freight"),
			__(
				"Equipment and materials a carrier ships to the job: the tracking number, the pickup and delivery windows, and who on the crew meets it. What rides on our own truck goes under Hauling on that drive instead. Optional."
			)
		);
		this.state.freight.forEach((item) => this.freight_card($step, item));
		$(`<div class="tp-add"><button class="tp-btn">+ ${__("Add a shipment")}</button></div>`)
			.appendTo($step)
			.find("button")
			.on("click", () => {
				const item = { name: null };
				TP_FREIGHT.forEach((field) => {
					item[field] = "";
				});
				item.paid_by = "Company";
				item.cost = 0;
				item.billable = this.state.trip.billable ? 1 : 0;
				this.state.freight.push(item);
				this.render();
			});
	}

	freight_card($parent, item) {
		// A shipment is one row, so its checklist key is the row's (completeness.group_key).
		const key = item.name ? `row:${item.name}` : null;
		const gaps = key ? this.gaps().filter((g) => g.group === key) : [];
		const title = [item.carrier, item.tracking_number].filter(Boolean).join(" ") || __("Shipment");
		const $card = $(`<div class="tp-card ${gaps.length ? "tp-bad" : ""}">
			<div class="tp-card-head"><b>${tp_esc(__("Freight"))}: ${tp_esc(title)}</b></div>
		</div>`).appendTo($parent);
		if (!item.protected) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($card.find(".tp-card-head"))
				.on("click", () => {
					frappe.confirm(__("Remove this shipment?"), () => {
						this.state.freight.splice(this.state.freight.indexOf(item), 1);
						this.render();
					});
				});
		}
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Carrier"), true),
			"Supplier",
			item.carrier,
			(val) => {
				item.carrier = val;
			},
			__("e.g. Old Dominion")
		);
		this.input(this.field($grid, __("Tracking / PRO / BOL #")), "text", item.tracking_number, (val) => {
			item.tracking_number = val.trim();
		});
		$("<textarea rows=\"2\"></textarea>")
			.val(item.contents || "")
			.attr("placeholder", __("e.g. 2 crates: basin liner, pump skid"))
			.appendTo(this.field($grid, __("What's being shipped"), false, "tp-wide"))
			.on("input", (e) => {
				item.contents = e.target.value;
			});
		this.place(this.field($grid, __("Ship from")), item.ship_from, (val) => {
			item.ship_from = val;
		});
		this.place(this.field($grid, __("Deliver to")), item.deliver_to, (val) => {
			item.deliver_to = val;
		});
		this.time_window($grid, __("Pickup window"), item.pickup_from, item.pickup_to, (from, to) => {
			item.pickup_from = from;
			item.pickup_to = to;
		});
		this.time_window($grid, __("Delivery window"), item.delivery_from, item.delivery_to, (from, to) => {
			item.delivery_from = from;
			item.delivery_to = to;
		});
		const $who = $("<select></select>").appendTo(this.field($grid, __("Who meets the delivery?")));
		$("<option></option>").val("").text(__("Whole crew")).appendTo($who);
		this.crew().forEach((t) => {
			$("<option></option>").val(t.employee).text(t.employee_name || t.employee).appendTo($who);
		});
		$who.val(item.traveler || "").on("change", () => {
			item.traveler = $who.val();
		});
		// money_block works on a card; a shipment's fields ARE its values, with nobody to
		// split the cost between.
		this.money_block($card, { values: item, members: [], changed: new Set() }, __("Freight cost"));
		this.render_gap_list($card, gaps, false);
	}

	// ------------------------------------------------------------------ step 7: schedule

	step_schedule($step) {
		this.step_intro(
			$step,
			__("Schedule"),
			__("What happens each day: customer visits, site work, a supply run. Optional, and it lands on everyone's itinerary.")
		);
		const t = this.state.trip;
		const days = tp_days_between(t.start_date, t.end_date);
		const stops = this.state.stops;

		// Sort a copy for display. Sorting the state itself would make an untouched trip look
		// edited; the order is written on the next save instead (see payload()).
		let current_day = null;
		tp_sorted_stops(stops).forEach((stop) => {
			if (stop.date !== current_day) {
				current_day = stop.date;
				$(`<div class="tp-day">${tp_esc(tp_pretty_date(stop.date) || __("No day yet"))}</div>`).appendTo($step);
			}
			this.stop_card($step, stop, days);
		});

		$(`<div class="tp-add"><button class="tp-btn">+ ${__("Add a stop")}</button></div>`)
			.appendTo($step)
			.find("button")
			.on("click", () => {
				const last = stops.slice(-1)[0];
				stops.push({
					name: null,
					date: last ? last.date : days[0] || "",
					time: "",
					end_time: "",
					activity_description: "",
					related_party_doctype: "",
					related_party_name: "",
					location: "",
				});
				this.render();
			});
	}

	stop_card($parent, stop, days) {
		const $card = $('<div class="tp-card"></div>').appendTo($parent);
		const when = stop.time
			? tp_pretty_time(stop.time) + (stop.end_time ? ` - ${tp_pretty_time(stop.end_time)}` : "")
			: __("Any time");
		const $head = $(`<div class="tp-card-head"><b>${tp_esc(when)}</b></div>`).appendTo($card);
		if (!stop.outcome_name) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($head)
				.on("click", () => {
					this.state.stops.splice(this.state.stops.indexOf(stop), 1);
					this.render();
				});
		}
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		const $day = $("<select></select>").appendTo(this.field($grid, __("Day"), true));
		days.forEach((d) => $("<option></option>").val(d).text(tp_pretty_date(d)).appendTo($day));
		if (stop.date && !days.includes(stop.date)) {
			$("<option></option>").val(stop.date).text(tp_pretty_date(stop.date)).appendTo($day);
		}
		$day.val(stop.date || "").on("change", () => {
			stop.date = $day.val();
		});
		this.time_input($grid, __("From"), stop.time, (val) => {
			stop.time = val;
		});
		this.time_input($grid, __("Until (optional)"), stop.end_time, (val) => {
			stop.end_time = val;
		});
		this.input(this.field($grid, __("What's happening?"), true, "tp-wide"), "text", stop.activity_description, (val) => {
			stop.activity_description = val;
		}).attr("placeholder", __("e.g. Walk the site with the GC"));

		const $who = this.field($grid, __("Meeting with (optional)"), false, "tp-wide");
		const $pair = $('<div class="tp-grid"></div>').appendTo($who);
		const $type = $("<select></select>").appendTo($('<div class="tp-field"></div>').appendTo($pair));
		[""].concat(this.lookups.related_party_doctypes || []).forEach((doctype) => {
			$("<option></option>")
				.val(doctype)
				.text(doctype ? __(doctype) : __("Nobody in particular"))
				.appendTo($type);
		});
		$type.val(stop.related_party_doctype || "");
		const $holder = $('<div class="tp-field"></div>').appendTo($pair);
		const mount = () => {
			$holder.empty();
			if (!stop.related_party_doctype) return;
			this.link($holder, stop.related_party_doctype, stop.related_party_name, (val) => {
				stop.related_party_name = val;
			});
		};
		$type.on("change", () => {
			stop.related_party_doctype = $type.val();
			stop.related_party_name = "";
			mount();
		});
		mount();
		// The Place is a Travel POI. Picking a Google suggestion finds or makes that POI
		// right away, with its point, so the trip map can plot it; a name typed without
		// picking is sent as text and the server finds or makes the POI on save.
		const $place = this.field($grid, __("Place (optional)"), false, "tp-wide");
		this.place(
			$place,
			stop.location_title || stop.location_text || "",
			(text, meta) => {
				if (!meta) {
					if (text !== stop.location_title) {
						stop.location = "";
						stop.location_title = "";
					}
					stop.location_text = text;
					return;
				}
				frappe
					.call({
						method: "erpnext_enhancements.travel_management.planner.place_to_poi",
						args: {
							label: text,
							address: meta.formatted_address,
							latitude: meta.latitude,
							longitude: meta.longitude,
						},
					})
					.then((r) => {
						const poi = r && r.message;
						if (!poi) return;
						stop.location = poi.name;
						stop.location_title = poi.poi_name;
						stop.location_text = "";
					});
			},
			__("e.g. The Home Depot, Mesa")
		);
	}

	// ------------------------------------------------------------------ step 8: review

	step_review($step) {
		const s = this.state;
		this.step_intro(
			$step,
			__("Review"),
			__("Everything for this trip in one place, and anything still missing. Fix it now or come back later.")
		);
		if (!s.name) {
			$(`<div class="tp-gap">${__("This trip is not saved yet: it needs its basics and at least one person.")}</div>`).appendTo(
				$step
			);
			return;
		}

		const total =
			Object.keys(s.bookings).reduce(
				(sum, table) => sum + this.cards(table).reduce((acc, card) => acc + flt(card.values.cost), 0),
				0
			) + s.freight.reduce((acc, item) => acc + flt(item.cost), 0);
		$(`<div class="tp-sum">
			<div><span class="tp-muted">${__("People")}</span><b>${this.crew().length}</b></div>
			<div><span class="tp-muted">${__("Flights")}</span><b>${this.cards("flights").length}</b></div>
			<div><span class="tp-muted">${__("Rooms")}</span><b>${this.cards("accommodations").length}</b></div>
			<div><span class="tp-muted">${__("Drives and rentals")}</span><b>${this.cards("ground_transport").length}</b></div>
			<div><span class="tp-muted">${__("Shipments")}</span><b>${s.freight.length}</b></div>
			<div><span class="tp-muted">${__("Booked so far")}</span><b>${tp_esc(format_currency(total, this.lookups.currency))}</b></div>
		</div>`).appendTo($step);

		this.render_numbers_by_person($step);

		const sections = [
			{ check: "travel", title: __("Travel both ways"), ok: __("Everyone has a way there and a way back.") },
			{ check: "lodging", title: __("A bed every night"), ok: __("Everyone has somewhere to sleep every night.") },
			{
				check: "confirmation",
				title: __("Confirmation numbers"),
				ok: __("Every booking has its confirmation number, and every shipment its tracking number."),
			},
			{ check: "cost", title: __("Cost and who paid"), ok: __("Every booking has a cost.") },
		];
		sections.forEach((section) => {
			const $sec = $(`<div class="tp-review-sec"><h5>${tp_esc(section.title)}</h5></div>`).appendTo($step);
			const gaps = this.gaps().filter((g) => g.check === section.check);
			if (!gaps.length) {
				$(`<div class="tp-ok-line">&#10003; ${tp_esc(section.ok)}</div>`).appendTo($sec);
			} else {
				this.render_gap_list($sec, gaps, true);
			}
		});

		const $actions = $('<div class="tp-add" style="margin-top:18px;"></div>').appendTo($step);
		if (s.status === "Planning" && s.can_write) {
			$(`<button class="tp-btn tp-btn-primary">${__("Mark as booked")}</button>`)
				.appendTo($actions)
				.on("click", () => this.mark_booked());
		}
		$(`<button class="tp-btn">${__("Email everyone their itinerary")}</button>`)
			.appendTo($actions)
			.on("click", () => this.send_itineraries());
		$(`<a class="tp-btn" style="display:inline-flex;align-items:center;" href="/app/travel-trip/${encodeURIComponent(
			s.name
		)}">${__("Open the full form")}</a>`).appendTo($actions);
	}

	render_numbers_by_person($step) {
		// What each person will see on their own itinerary: every booking they are on, with
		// their own confirmation number, or a flag where it is missing. Shipments are listed
		// under whoever meets them (all of the crew when nobody is named).
		const $sec = $(`<div class="tp-review-sec"><h5>${__("Each person's confirmation numbers")}</h5></div>`).appendTo(
			$step
		);
		const $table = $('<div class="tp-matrix"><table><tbody></tbody></table></div>').appendTo($sec);
		const $body = $table.find("tbody");
		const icon = { flights: "&#9992;", accommodations: "&#127976;", ground_transport: "&#128663;" };
		this.crew().forEach((t) => {
			const lines = [];
			Object.keys(this.state.bookings).forEach((table) => {
				this.cards(table).forEach((card) => {
					const member = card.members.find((m) => !m.traveler || m.traveler === t.employee);
					if (!member) return;
					const unbooked = table === "ground_transport" && TP_UNBOOKED.includes(card.values.transport_type);
					let number;
					if (member.ref) number = tp_esc(member.ref);
					else if (unbooked) number = `<span class="tp-muted">${__("nothing to confirm")}</span>`;
					else number = `<b style="color:#b91c1c;">${__("missing")}</b>`;
					lines.push(`${icon[table]} ${tp_esc(this.card_label(card))}: ${number}`);
				});
			});
			this.state.freight.forEach((item) => {
				if (item.traveler && item.traveler !== t.employee) return;
				const number = item.tracking_number
					? tp_esc(item.tracking_number)
					: `<b style="color:#b91c1c;">${__("missing")}</b>`;
				lines.push(`&#128230; ${tp_esc(item.carrier || __("Shipment"))}: ${number}`);
			});
			$(`<tr><td>${tp_esc(t.employee_name || t.employee)}</td><td style="text-align:left;white-space:normal;">${
				lines.length ? lines.join("<br>") : `<span class="tp-muted">${__("No bookings yet")}</span>`
			}</td></tr>`).appendTo($body);
		});
	}

	mark_booked() {
		const book = () => {
			this.state.status = "Booked";
			this.save({ force: true }).then((ok) => {
				if (!ok) this.state.status = "Planning";
				this.render();
			});
		};
		const count = this.gaps().length;
		if (!count) {
			book();
			return;
		}
		frappe.confirm(
			__("{0} things on the checklist are still missing. Mark the trip as booked anyway?", [count]),
			book
		);
	}

	send_itineraries() {
		frappe.confirm(
			__("Email each person on this trip their own itinerary, with a calendar invite?"),
			() => {
				this.save().then((ok) => {
					if (!ok) return;
					frappe
						.call({
							method: "erpnext_enhancements.api.travel.send_itinerary_email",
							args: { trip: this.state.name },
						})
						.then((r) => {
							frappe.show_alert({
								message: __("Itinerary sent to {0} people", [((r && r.message) || []).length]),
								indicator: "green",
							});
						});
				});
			}
		);
	}
}
