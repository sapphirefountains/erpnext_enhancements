/**
 * The calendar: a month or a week of posts, the unscheduled drafts, and each Instagram and
 * YouTube account's remaining quota.
 *
 * **Drag to reschedule** moves a post to another day at the same time of day
 * (`calendar.moveToDay`) through `spa.reschedule`, which takes the `modified` the calendar
 * loaded, so a post somebody edited since is refused rather than overwritten. Only a Draft or a
 * post waiting for approval moves: once approved, the time is part of what was approved, and
 * the server refuses the move. Past days take no drops. Dragging is a shortcut, never the only
 * way: every post's own page has a date field that does the same.
 */

import { call, M } from "./transport.js";
import { VIEW_MONTH, VIEW_NEW, VIEW_POST, VIEW_RESULTS, VIEW_WEEK, buildRoute } from "./routes.js";
import {
	WEEKDAYS,
	addDays,
	addMonths,
	bucketByDay,
	clockLabel,
	dayLabel,
	isPast,
	monthGrid,
	monthLabel,
	monthOf,
	moveToDay,
	timePart,
	weekDays,
	weekdayOf,
	whenLabel,
	windowOf,
} from "./calendar.js";
import { append, el, fill, link, networkTag, statusPill } from "./dom.js";

const DRAG_TYPE = "application/x-ee-marketing-post";

export async function renderCalendar(app, route) {
	const today = app.data.today;
	const isWeek = route.view === VIEW_WEEK;
	let weeks;
	let title;
	let prev;
	let next;
	if (isWeek) {
		const days = weekDays(route.day || today, app.weekStart);
		weeks = [days];
		title = `Week of ${dayLabel(days[0])}`;
		prev = buildRoute(VIEW_WEEK, addDays(days[0], -7));
		next = buildRoute(VIEW_WEEK, addDays(days[0], 7));
	} else {
		const month = route.month || monthOf(today);
		weeks = monthGrid(month, app.weekStart);
		title = monthLabel(month);
		prev = buildRoute(VIEW_MONTH, addMonths(month, -1));
		next = buildRoute(VIEW_MONTH, addMonths(month, 1));
	}
	const month = isWeek ? "" : route.month || monthOf(today);

	const toolbar = el("div", "ee-mk-toolbar");
	const go = (href) => app.navigate(href);
	append(
		toolbar,
		link("‹", prev, go, "ee-mk-btn ee-mk-btn-small"),
		el("h2", "ee-mk-toolbar-title", title),
		link("›", next, go, "ee-mk-btn ee-mk-btn-small"),
		link("Today", isWeek ? buildRoute(VIEW_WEEK, today) : buildRoute(VIEW_MONTH), go, "ee-mk-btn ee-mk-btn-small"),
		el("span", "ee-mk-spacer"),
		segmented(app, isWeek, weeks[0][0], month || monthOf(today)),
		link("New post", buildRoute(VIEW_NEW), go, "ee-mk-btn ee-mk-btn-primary")
	);

	const grid = el("div", isWeek ? "ee-mk-cal ee-mk-cal-week-view" : "ee-mk-cal");
	const unscheduled = el("section", "ee-mk-section");
	fill(app.pane, toolbar, quotaStrip(app), grid, unscheduled);
	app.showPlaceholder(grid, "◷", "Loading the calendar…");

	const days = weeks.flat();
	let data;
	try {
		data = await call(M.CALENDAR, windowOf(days));
	} catch (e) {
		app.showPlaceholder(grid, "!", "The calendar could not load", e.message);
		return;
	}
	drawGrid(app, grid, weeks, month, bucketByDay(data.posts), route, isWeek);
	renderUnscheduled(app, unscheduled, data.unscheduled);
}

function segmented(app, isWeek, firstDay, month) {
	const wrap = el("div", "ee-mk-seg");
	wrap.setAttribute("role", "group");
	wrap.setAttribute("aria-label", "Calendar view");
	const monthLink = link("Month", buildRoute(VIEW_MONTH, month), (h) => app.navigate(h), "ee-mk-seg-item");
	const weekLink = link("Week", buildRoute(VIEW_WEEK, firstDay), (h) => app.navigate(h), "ee-mk-seg-item");
	(isWeek ? weekLink : monthLink).classList.add("ee-mk-seg-active");
	(isWeek ? weekLink : monthLink).setAttribute("aria-current", "true");
	return append(wrap, monthLink, weekLink);
}

/** Each Instagram and YouTube account's remaining quota, as the rate limiter last saw it. */
function quotaStrip(app) {
	const rows = app.data.quota || [];
	const strip = el("div", "ee-mk-quota");
	if (!rows.length) return strip;
	for (const row of rows) {
		const item = el("div", `ee-mk-quota-item${row.stale ? " ee-mk-quota-stale" : ""}`);
		const figure =
			row.remaining === null || row.remaining === undefined
				? "not checked yet"
				: `${row.remaining} ${row.unit} left${row.stale ? ` (as of ${whenLabel(row.checked_at)})` : ""}`;
		append(item, networkTag(row.network), el("span", "ee-mk-quota-label", row.label), el("span", "ee-mk-quota-figure", figure));
		strip.appendChild(item);
	}
	return strip;
}

function drawGrid(app, grid, weeks, month, buckets, route, isWeek) {
	const today = app.data.today;
	const head = el("div", "ee-mk-cal-head");
	for (const key of weeks[0]) head.appendChild(el("div", "ee-mk-cal-dow", WEEKDAYS[weekdayOf(key)].slice(0, 3)));
	const body = el("div", "ee-mk-cal-body");
	for (const week of weeks) {
		const row = el("div", "ee-mk-cal-week");
		for (const key of week) row.appendChild(dayCell(app, key, buckets[key] || [], month, today, route, isWeek));
		body.appendChild(row);
	}
	fill(grid, head, body);
}

function dayCell(app, key, posts, month, today, route, isWeek) {
	const classes = ["ee-mk-day"];
	if (month && !key.startsWith(month)) classes.push("ee-mk-day-out");
	if (key === today) classes.push("ee-mk-day-today");
	const past = isPast(key, today);
	if (past) classes.push("ee-mk-day-past");
	const cell = el("div", classes.join(" "));
	cell.dataset.day = key;

	const head = el("div", "ee-mk-day-head");
	// In the week view the day says its name too: on a phone the week is seven stacked days
	// with no column headings above them.
	const number = el("span", "ee-mk-day-num", isWeek ? dayLabel(key) : String(Number(key.slice(8))));
	if (key === today) number.setAttribute("aria-label", "Today");
	head.appendChild(number);
	if (!past) {
		const add = link("+", buildRoute(VIEW_NEW, key), (h) => app.navigate(h), "ee-mk-day-add");
		add.setAttribute("aria-label", `New post on ${dayLabel(key)}`);
		head.appendChild(add);
	}
	cell.appendChild(head);

	const list = el("div", "ee-mk-day-posts");
	for (const post of posts) list.appendChild(chip(app, post, isWeek));
	cell.appendChild(list);

	if (!past) acceptDrops(app, cell, key, route);
	return cell;
}

function chip(app, post, isWeek) {
	const href = post.editable ? buildRoute(VIEW_POST, post.name) : buildRoute(VIEW_RESULTS, post.name);
	const node = link("", href, (h) => app.navigate(h), `ee-mk-chip ee-mk-chip-${slug(post.status)}`);
	const time = clockLabel(timePart(post.scheduled_at));
	append(
		node,
		time ? el("span", "ee-mk-chip-time", time) : null,
		el("span", "ee-mk-chip-title", post.title),
		isWeek ? statusPill(post.status) : null,
		el("span", "ee-mk-chip-nets", post.networks.map(shortNet).join(" "))
	);
	node.title = `${post.title} — ${post.status}${post.networks.length ? ` — ${post.networks.join(", ")}` : ""}`;
	if (post.editable) {
		node.draggable = true;
		node.addEventListener("dragstart", (ev) => {
			ev.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ name: post.name, scheduled_at: post.scheduled_at, modified: post.modified }));
			ev.dataTransfer.effectAllowed = "move";
			node.classList.add("ee-mk-chip-dragging");
		});
		node.addEventListener("dragend", () => node.classList.remove("ee-mk-chip-dragging"));
	} else {
		// A link is draggable by default; an approved post's is not (its time was approved).
		node.draggable = false;
		node.classList.add("ee-mk-chip-locked");
	}
	return node;
}

function acceptDrops(app, cell, key, route) {
	cell.addEventListener("dragover", (ev) => {
		if (!Array.from(ev.dataTransfer.types || []).includes(DRAG_TYPE)) return;
		ev.preventDefault();
		ev.dataTransfer.dropEffect = "move";
		cell.classList.add("ee-mk-day-over");
	});
	cell.addEventListener("dragleave", () => cell.classList.remove("ee-mk-day-over"));
	cell.addEventListener("drop", async (ev) => {
		cell.classList.remove("ee-mk-day-over");
		let post;
		try {
			post = JSON.parse(ev.dataTransfer.getData(DRAG_TYPE));
		} catch (e) {
			return;
		}
		ev.preventDefault();
		if (!post || String(post.scheduled_at || "").startsWith(key)) return;
		try {
			await call(M.RESCHEDULE, {
				name: post.name,
				scheduled_at: moveToDay(post.scheduled_at, key),
				modified: post.modified,
			});
			app.say(`Moved to ${whenLabel(moveToDay(post.scheduled_at, key))}.`, "ok");
		} catch (e) {
			app.fail(e);
		}
		renderCalendar(app, route);
	});
}

/** Drafts with no time yet: the ones the calendar cannot place. Drag one onto a day to give it one. */
function renderUnscheduled(app, section, posts) {
	const heading = el("h3", "ee-mk-section-title", "Not scheduled yet");
	const list = el("div", "ee-mk-list ee-mk-unscheduled");
	fill(section, heading, list);
	if (!posts || posts.length === 0) {
		app.showPlaceholder(list, "✓", "Every draft has a time", "A draft with no time shows here until it gets one.");
		return;
	}
	for (const post of posts) {
		const row = el("div", "ee-mk-row");
		append(
			row,
			chip(app, post, true),
			el("span", "ee-mk-row-meta", `by ${post.author || "someone"}`),
			...post.networks.map(networkTag)
		);
		list.appendChild(row);
	}
}

function shortNet(network) {
	return { Facebook: "f", Instagram: "◎", LinkedIn: "in", YouTube: "▶" }[network] || "•";
}

function slug(value) {
	return String(value || "").toLowerCase().replace(/[^a-z]+/g, "-");
}

