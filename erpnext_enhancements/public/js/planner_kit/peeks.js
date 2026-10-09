/*
 * Planner kit: the two quick looks both planners share, a person's week and everyone's day, drawn
 * from `api/planner_views.py` (get_person_schedule, get_day_overview) into the drawer.
 *
 *   planner_kit.peeks.person({
 *     resource | user, label, start, days,        // who and which days (the week on screen)
 *     owner, push,                                // as for drawer.open
 *     can_drag: (booking, day, person, data) => bool,   // only what the page owns (its tasks,
 *                                                       // its visits); data: the drawer's answer
 *     on_full_route: (resource, ymd) => ...,      // the route view, /app/project-planner/route/...
 *   });
 *   planner_kit.peeks.day({ date, group, owner, push, can_drag, person_key, on_person, on_week, only });
 *   // person_key(person): what identifies a person to the page (a resource, or a user);
 *   // only(person): which people to show (the Maintenance Planner shows its technicians).
 *
 * Each returns a controller ({ refresh(), is_open(), data() }) the page refreshes after a change of
 * its own. The arrows inside a drawer step its own days and never move the calendar behind it.
 * Times off read only "Off" / "Holiday" (the server never sends a type or a reason), drive times
 * come with the server's note that they are estimates, and every value is escaped here.
 *
 * A booking the page says it owns is marked `data-pk-drag="1"` with its kind, ref, key, date and
 * person; the page's own pointer-drag code reads those, so a drop goes through the page's normal save
 * path (reason prompt, Undo). The renderers are pure, for tests/test_planner_phase6a.py.
 */

import { escape, hours, drive, day_label, add_days, contact_href, glow_keys, t } from "./escape.js";

export const METHODS = {
	person: "erpnext_enhancements.api.planner_views.get_person_schedule",
	day: "erpnext_enhancements.api.planner_views.get_day_overview",
};

const KINDS = { task: "Task", rental: "Rental", visit: "Visit", travel: "Travel", drive: "Driving" };
const MAPS = /^https:\/\/www\.google\.com\/maps\//;

// One person-day as a tone and a short text: the planners' own wording.
export function day_state(day) {
	if (!day) return { tone: "idle", text: "" };
	const capacity = Number(day.capacity) || 0;
	const booked = Number(day.booked) || 0;
	const free = Math.max(0, Number(day.free) || 0);
	const soft = Math.max(0, Number(day.soft_booked) || 0);
	let state;
	if (capacity <= 0 && booked <= 0.01) state = { tone: "off", text: day.off ? t(day.off) : t("Off") };
	else if (capacity <= 0) state = { tone: "red", text: t("{0}, {1}h booked", [day.off ? t(day.off) : t("Off"), hours(booked)]) };
	else if (booked - capacity > 0.01) state = { tone: "red", text: t("Over {0}h", [hours(booked - capacity)]) };
	else {
		state = {
			tone: booked / capacity <= 0.75 ? "green" : "amber",
			text: free > 0.01 ? t("{0}h free", [hours(free)]) : t("Full"),
		};
		if (day.off) state.text += ` · ${t(day.off)}`;
	}
	if (soft > 0.01) state.text += ` · ${t("+{0}h pencil", [hours(soft)])}`;
	if ((day.conflicts || []).length) state.conflict = true;
	return state;
}

function drag_attrs(booking, day, person) {
	return (
		` data-pk-drag="1" data-pk-kind="${escape(booking.kind)}" data-pk-ref="${escape(booking.ref)}"` +
		` data-pk-key="${escape(booking.key || booking.ref)}" data-pk-date="${escape(day.date)}"` +
		` data-pk-resource="${escape(person.resource || "")}" data-pk-user="${escape(person.user || "")}"`
	);
}

function booking_html(booking, day, person, ctx) {
	const kind = KINDS[booking.kind] ? booking.kind : "other";
	const grab = !!(ctx.can_drag && booking.kind !== "drive" && ctx.can_drag(booking, day, person, ctx.data));
	const when = booking.slot && booking.slot.length === 2 ? booking.slot.join("–") : booking.arrive || "";
	const length =
		booking.kind === "drive"
			? drive(day.drive_minutes || (Number(booking.hours) || 0) * 60)
			: booking.kind === "travel"
			? t("Away")
			: `${hours(booking.hours)}h`;
	const site = booking.project_title && booking.project_title !== booking.label ? booking.project_title : "";
	const meta = [when, length, site].filter(Boolean).join(" · ");
	const chips = [];
	if (booking.tentative) chips.push(`<span class="pk-chip pk-chip-plain">${escape(t("Pencil"))}</span>`);
	if (booking.kind === "drive" && booking.estimated) chips.push(`<span class="pk-chip pk-chip-plain">${escape(t("estimate"))}</span>`);
	const tip = [booking.label, meta, booking.address, grab ? t("Drag onto the calendar to move it") : ""]
		.filter(Boolean)
		.join("\n");
	const projects = booking.project ? ` data-pk-projects="${escape(glow_keys([booking.project]))}"` : "";
	return (
		`<li class="pk-item pk-k-${kind}${grab ? " pk-grab" : ""}"${grab ? drag_attrs(booking, day, person) : ""}${projects}` +
		` title="${escape(tip)}"><span class="pk-kind">${escape(t(KINDS[kind] || "Booking"))}</span>` +
		`<span class="pk-item-main"><span class="pk-item-label">${escape(booking.label)}</span>` +
		`<span class="pk-item-meta">${escape(meta)}</span></span>${chips.join("")}</li>`
	);
}

function stops_html(day, person, ctx) {
	const stops = day.stops || [];
	const route =
		person.resource && ctx.full_route !== false
			? `<button type="button" class="btn btn-default btn-xs" data-pk-full-route="${escape(day.date)}">${escape(
					t("Full route")
			  )}</button>`
			: "";
	const head = `<div class="pk-stops-head"><h4>${escape(t("Stops on {0}", [day_label(day.date)]))}</h4>${route}</div>`;
	if (!stops.length) {
		const empty = day.travel ? day.travel : day.off ? t(day.off) : t("Nothing to drive to on this day.");
		return `<section class="pk-stops">${head}<p class="pk-empty">${escape(empty)}</p></section>`;
	}
	const items = stops.map((stop, index) => {
		const when = stop.slot && stop.slot.length === 2 ? stop.slot.join("–") : stop.arrive || stop.time || "";
		const sub = [stop.project_title && stop.project_title !== stop.label ? stop.project_title : "", Number(stop.hours) > 0 ? `${hours(stop.hours)}h` : ""]
			.filter(Boolean)
			.join(" · ");
		const maps = stop.maps_url && MAPS.test(stop.maps_url) ? stop.maps_url : "";
		const address = stop.address
			? maps
				? `<a class="pk-stop-addr" href="${escape(maps)}" target="_blank" rel="noopener noreferrer">${escape(stop.address)}</a>`
				: `<div class="pk-stop-addr">${escape(stop.address)}</div>`
			: "";
		const crew = (stop.crew || []).length ? `<div class="pk-stop-sub">${escape(t("With {0}", [stop.crew.join(", ")]))}</div>` : "";
		return (
			`<li class="pk-stop"><span class="pk-stop-num">${escape(index + 1)}</span><div class="pk-stop-body">` +
			(when ? `<div class="pk-stop-time">${escape(when)}</div>` : "") +
			`<div class="pk-stop-label">${escape(stop.label || stop.ref)}</div>` +
			(sub ? `<div class="pk-stop-sub">${escape(sub)}</div>` : "") +
			`${address}${crew}</div></li>`
		);
	});
	return `<section class="pk-stops">${head}<ol class="pk-stop-list">${items.join("")}</ol></section>`;
}

// The day the stops section shows: the one asked for, else today, else the first day with stops.
export function pick_day(data, selected) {
	const days = (data && data.days) || [];
	if (!days.length) return null;
	return (
		days.find((day) => day.date === selected) ||
		days.find((day) => day.date === data.today) ||
		days.find((day) => (day.stops || []).length) ||
		days[0]
	);
}

export function contact_html(contact) {
	contact = contact || {};
	const links = [];
	const tel = contact_href("tel", contact.tel);
	const sms = contact_href("sms", contact.tel);
	const mail = contact_href("mailto", contact.email);
	if (tel) links.push(`<a class="btn btn-default btn-xs pk-contact-link" href="${escape(tel)}" title="${escape(contact.phone || contact.tel)}">${escape(t("Call"))}</a>`);
	if (sms) links.push(`<a class="btn btn-default btn-xs pk-contact-link" href="${escape(sms)}">${escape(t("Text"))}</a>`);
	if (mail) links.push(`<a class="btn btn-default btn-xs pk-contact-link" href="${escape(mail)}" title="${escape(contact.email)}">${escape(t("Email"))}</a>`);
	return links.length ? `<div class="pk-contact">${links.join("")}</div>` : "";
}

export function person_meta(data) {
	if (!data) return "";
	const team = data.home_team ? t("{0} team", [t(data.home_team)]) : "";
	return [data.group ? t(data.group) : "", team, data.type === "Subcontractor" ? t("Subcontractor") : ""]
		.filter((part, index, all) => part && all.indexOf(part) === index)
		.join(" · ");
}

export function person_week_html(data, ctx) {
	ctx = ctx || {};
	if (!data) return `<p class="pk-empty">${escape(t("The schedule could not be loaded. Try again."))}</p>`;
	const parts = [contact_html(data.contact)];
	if (data.message) {
		parts.push(`<div class="pk-note pk-note-warn">${escape(data.message)}</div>`);
		return `<div class="pk-person">${parts.join("")}</div>`;
	}
	const person = { resource: data.resource, user: data.user };
	const chosen = pick_day(data, ctx.selected);
	const days = (data.days || []).map((day) => {
		const state = day_state(day);
		const classes = ["pk-day"];
		if (state.tone === "off") classes.push("pk-day-off");
		if (state.conflict) classes.push("pk-day-conflict");
		if (chosen && chosen.date === day.date) classes.push("pk-day-selected");
		if (day.date === data.today) classes.push("pk-day-today");
		const drive_text =
			Number(day.drive_minutes) > 0
				? `<span class="pk-day-drive">${escape(t("{0} drive", [drive(day.drive_minutes)]))}${
						day.long_drive ? ` <span class="pk-chip pk-chip-red">${escape(t("Long drive"))}</span>` : ""
				  }</span>`
				: "";
		const bookings = (day.bookings || []).filter((booking) => booking.kind !== "drive");
		const items = bookings.length
			? `<ul class="pk-items">${bookings.map((booking) => booking_html(booking, day, person, ctx)).join("")}</ul>`
			: `<p class="pk-empty">${escape(day.travel ? day.travel : state.tone === "off" ? state.text : t("Nothing booked"))}</p>`;
		const conflicts = (day.conflicts || [])
			.map((text) => `<div class="pk-conflict">${escape(t("Conflict"))}: ${escape(text)}</div>`)
			.join("");
		const warnings = (day.warnings || []).map((text) => `<div class="pk-warning">${escape(text)}</div>`).join("");
		return (
			`<section class="${classes.join(" ")}" data-pk-day="${escape(day.date)}">` +
			`<header class="pk-day-head" role="button" tabindex="0" data-pk-select-day="${escape(day.date)}"` +
			` title="${escape(t("Show this day's stops"))}"><b>${escape(day_label(day.date))}</b>` +
			`<span class="pk-state pk-state-${escape(state.tone)}">${escape(state.text)}</span>${drive_text}</header>` +
			`${items}${conflicts}${warnings}</section>`
		);
	});
	parts.push(`<div class="pk-week">${days.join("")}</div>`);
	if (chosen) parts.push(stops_html(chosen, person, ctx));
	if (data.estimate_note) parts.push(`<p class="pk-note">${escape(data.estimate_note)}</p>`);
	return `<div class="pk-person">${parts.join("")}</div>`;
}

export function day_overview_html(data, ctx) {
	ctx = ctx || {};
	if (!data) return `<p class="pk-empty">${escape(t("The day could not be loaded. Try again."))}</p>`;
	if (data.note) return `<div class="pk-note pk-note-warn">${escape(data.note)}</div>`;
	const people = data.people || [];
	if (!people.length) return `<p class="pk-empty">${escape(t("Nobody to show for this day."))}</p>`;
	const key_of = ctx.person_key || ((person) => person.resource);
	const columns = people.map((person) => {
		const day = person;
		const state = day_state(day);
		const key = key_of(person) || "";
		const name =
			`<span class="pk-link" role="button" tabindex="0" data-pk-person-open="${escape(key)}"` +
			` data-pk-person="${escape(glow_keys([key]))}" title="${escape(t("See {0}'s week", [person.label]))}">${escape(person.label)}</span>`;
		const facts = [
			Number(day.capacity) > 0 ? t("{0}h of {1}h booked", [hours(day.booked), hours(day.capacity)]) : "",
			Number(day.drive_minutes) > 0 ? t("{0} drive", [drive(day.drive_minutes)]) : "",
		]
			.filter(Boolean)
			.join(" · ");
		const stops = day.stops || [];
		const listed = new Set(stops.map((stop) => `${stop.kind}|${stop.ref}`));
		const rest = (day.bookings || []).filter((booking) => booking.kind !== "drive" && !listed.has(`${booking.kind}|${booking.ref}`));
		const stop_items = stops.map((stop) => {
			const booking = (day.bookings || []).find((entry) => entry.kind === stop.kind && entry.ref === stop.ref) || {
				kind: stop.kind,
				ref: stop.ref,
				key: stop.ref,
				label: stop.label,
				project: stop.project,
				project_title: stop.project_title,
				hours: stop.hours,
				slot: stop.slot,
			};
			return booking_html(Object.assign({}, booking, { arrive: stop.slot ? null : stop.arrive || stop.time }), day, person, ctx);
		});
		const rest_items = rest.map((booking) => booking_html(booking, day, person, ctx));
		const items = stop_items.concat(rest_items);
		const body = items.length
			? `<ol class="pk-items">${items.join("")}</ol>`
			: `<p class="pk-empty">${escape(day.travel ? day.travel : state.tone === "off" ? state.text : t("Nothing booked"))}</p>`;
		const conflicts = (day.conflicts || [])
			.map((text) => `<div class="pk-conflict">${escape(text)}</div>`)
			.join("");
		return (
			`<section class="pk-col${state.tone === "off" ? " pk-col-off" : ""}${state.conflict ? " pk-day-conflict" : ""}">` +
			`<header class="pk-col-head">${name}<span class="pk-state pk-state-${escape(state.tone)}">${escape(state.text)}</span></header>` +
			(facts ? `<div class="pk-col-meta">${escape(facts)}</div>` : "") +
			`${body}${conflicts}</section>`
		);
	});
	return (
		`<div class="pk-dayview"><div class="pk-columns">${columns.join("")}</div>` +
		(data.estimate_note ? `<p class="pk-note">${escape(data.estimate_note)}</p>` : "") +
		`</div>`
	);
}

export function create_peeks(env) {
	const doc = env.doc;

	function arrows(prev_title, next_title, on_step) {
		const box = doc.createElement("span");
		box.className = "pk-arrows";
		const make = (text, title, sign) => {
			const button = doc.createElement("button");
			button.type = "button";
			button.className = "btn btn-default btn-xs";
			button.textContent = text;
			button.title = title;
			button.setAttribute("aria-label", title);
			button.addEventListener("click", () => on_step(sign));
			return button;
		};
		const label = doc.createElement("span");
		label.className = "pk-arrows-label";
		box.appendChild(make("‹", prev_title, -1));
		box.appendChild(label);
		box.appendChild(make("›", next_title, 1));
		return { box, label };
	}

	function loading() {
		return `<p class="pk-empty pk-loading-note">${escape(t("Loading…"))}</p>`;
	}

	function person(opts) {
		opts = opts || {};
		const days = Math.max(1, Math.min(31, Number(opts.days) || 7));
		const state = { start: opts.start, selected: opts.selected || null, data: null, token: 0, handle: null };
		const nav = arrows(t("Previous week"), t("Next week"), (sign) => {
			state.start = add_days(state.start, sign * days);
			state.selected = null;
			load();
		});
		const render = () => {
			const handle = state.handle;
			if (!handle || !handle.is_open()) return;
			const data = state.data;
			handle.set_title((data && data.label) || opts.label || t("Schedule"));
			handle.set_subtitle(person_meta(data));
			nav.label.textContent = `${day_label(state.start)} – ${day_label(add_days(state.start, days - 1))}`;
			handle.set_body(person_week_html(data, { selected: state.selected, can_drag: opts.can_drag, data }));
		};
		const load = () => {
			const token = ++state.token;
			const handle = state.handle;
			if (!handle) return Promise.resolve(null);
			nav.label.textContent = `${day_label(state.start)} – ${day_label(add_days(state.start, days - 1))}`;
			if (!state.data) handle.set_body(loading());
			else handle.body.classList.add("pk-busy");
			const args = { start: state.start, days };
			if (opts.resource) args.resource = opts.resource;
			if (opts.user) args.user = opts.user;
			return Promise.resolve(env.call(METHODS.person, args))
				.then((data) => {
					if (token !== state.token) return null;
					handle.body.classList.remove("pk-busy");
					state.data = data || null;
					render();
					return data;
				})
				.catch(() => {
					if (token !== state.token) return null;
					handle.body.classList.remove("pk-busy");
					state.data = null;
					render();
					return null;
				});
		};
		state.handle = env.drawer.open({
			title: opts.label || t("Schedule"),
			body: loading(),
			tools: nav.box,
			width: opts.width || 460,
			key: `person:${opts.resource || opts.user || ""}`,
			owner: opts.owner || null,
			push: opts.push || null,
			reopen: () => person(Object.assign({}, opts, { start: state.start, selected: state.selected })),
			on_click: (e) => {
				const target = e.target;
				if (!target || !target.closest) return;
				const route = target.closest("[data-pk-full-route]");
				if (route) {
					const data = state.data || {};
					if (data.resource && typeof opts.on_full_route === "function") {
						opts.on_full_route(data.resource, route.getAttribute("data-pk-full-route"));
					}
					return;
				}
				const day = target.closest("[data-pk-select-day]");
				if (day) {
					state.selected = day.getAttribute("data-pk-select-day");
					render();
				}
			},
		});
		load();
		return {
			refresh: load,
			is_open: () => !!(state.handle && state.handle.is_open()),
			data: () => state.data,
			key: () => opts.resource || opts.user || "",
		};
	}

	function day(opts) {
		opts = opts || {};
		const state = { date: opts.date, data: null, token: 0, handle: null };
		const nav = arrows(t("Previous day"), t("Next day"), (sign) => {
			state.date = add_days(state.date, sign);
			load();
		});
		const render = () => {
			const handle = state.handle;
			if (!handle || !handle.is_open()) return;
			handle.set_title(t("Everyone on {0}", [day_label(state.date, true)]));
			handle.set_subtitle(opts.group ? t("{0} group", [t(opts.group)]) : "");
			nav.label.textContent = day_label(state.date);
			// `only` narrows the people a page shows (the Maintenance Planner: its technicians).
			const data =
				state.data && typeof opts.only === "function"
					? Object.assign({}, state.data, { people: (state.data.people || []).filter(opts.only) })
					: state.data;
			handle.set_body(day_overview_html(data, { can_drag: opts.can_drag, person_key: opts.person_key, data }));
		};
		const load = () => {
			const token = ++state.token;
			const handle = state.handle;
			if (!handle) return Promise.resolve(null);
			nav.label.textContent = day_label(state.date);
			handle.set_title(t("Everyone on {0}", [day_label(state.date, true)]));
			if (!state.data) handle.set_body(loading());
			else handle.body.classList.add("pk-busy");
			const args = { date: state.date };
			if (opts.group) args.group = opts.group;
			return Promise.resolve(env.call(METHODS.day, args))
				.then((data) => {
					if (token !== state.token) return null;
					handle.body.classList.remove("pk-busy");
					state.data = data || null;
					render();
					return data;
				})
				.catch(() => {
					if (token !== state.token) return null;
					handle.body.classList.remove("pk-busy");
					state.data = null;
					render();
					return null;
				});
		};
		const actions = [];
		if (typeof opts.on_week === "function") {
			actions.push({ label: t("Open this week"), on_click: () => opts.on_week(state.date) });
		}
		state.handle = env.drawer.open({
			title: t("Everyone on {0}", [day_label(state.date, true)]),
			body: loading(),
			tools: nav.box,
			width: opts.width || 760,
			key: `day:${state.date}`,
			owner: opts.owner || null,
			push: opts.push || null,
			actions,
			reopen: () => day(Object.assign({}, opts, { date: state.date })),
			on_click: (e) => {
				const target = e.target;
				const el = target && target.closest ? target.closest("[data-pk-person-open]") : null;
				if (!el || typeof opts.on_person !== "function") return;
				const key = el.getAttribute("data-pk-person-open");
				const found = ((state.data && state.data.people) || []).find(
					(entry) => ((opts.person_key && opts.person_key(entry)) || entry.resource) === key
				);
				opts.on_person(found || { resource: key }, state.date);
			},
		});
		load();
		return {
			refresh: load,
			is_open: () => !!(state.handle && state.handle.is_open()),
			data: () => state.data,
			date: () => state.date,
		};
	}

	return { person, day };
}
