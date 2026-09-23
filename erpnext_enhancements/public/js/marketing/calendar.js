/**
 * Calendar arithmetic for `/marketing`. Pure: no DOM, no fetch, **no browser time zone**.
 *
 * `scheduled_at` is a naive site-local datetime (`2026-09-23 09:00:00`), and the app is opened
 * from phones in whatever zone their owner happens to be in. So nothing here ever builds a
 * local `Date` from one: days are `YYYY-MM-DD` strings, and the only `Date`s are UTC ones used
 * to count days, which have no daylight-saving gaps to fall into. "Today" comes from the server
 * (`get_bootstrap().today`), never from the device clock.
 *
 * Checked by `scripts/test_marketing_client.js`.
 */

const MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
];
export const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function pad(n) {
	return String(n).padStart(2, "0");
}

function utc(key) {
	const [y, m, d] = String(key).split("-").map(Number);
	return new Date(Date.UTC(y, m - 1, d));
}

function keyOf(date) {
	return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** `addDays("2026-09-30", 1)` -> `"2026-10-01"`. */
export function addDays(key, days) {
	const date = utc(key);
	date.setUTCDate(date.getUTCDate() + days);
	return keyOf(date);
}

/** 0 for Sunday .. 6 for Saturday. */
export function weekdayOf(key) {
	return utc(key).getUTCDay();
}

/** System Settings' "first day of the week" as 0..6; Sunday for anything unrecognised. */
export function weekStartIndex(name) {
	const index = WEEKDAYS.indexOf(String(name || ""));
	return index === -1 ? 0 : index;
}

export function startOfWeek(key, weekStart) {
	return addDays(key, -((weekdayOf(key) - weekStart + 7) % 7));
}

export function monthOf(key) {
	return String(key).slice(0, 7);
}

/** `addMonths("2026-12", 1)` -> `"2027-01"`. */
export function addMonths(month, months) {
	const [y, m] = String(month).split("-").map(Number);
	const date = new Date(Date.UTC(y, m - 1 + months, 1));
	return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}`;
}

export function monthLabel(month) {
	const [y, m] = String(month).split("-").map(Number);
	return `${MONTHS[m - 1]} ${y}`;
}

/** The weeks a month view shows: whole weeks from the one holding the 1st to the one holding the last. */
export function monthGrid(month, weekStart) {
	const first = `${month}-01`;
	const last = addDays(`${addMonths(month, 1)}-01`, -1);
	const weeks = [];
	let day = startOfWeek(first, weekStart);
	while (day <= last) {
		const week = [];
		for (let i = 0; i < 7; i += 1) {
			week.push(day);
			day = addDays(day, 1);
		}
		weeks.push(week);
	}
	return weeks;
}

export function weekDays(key, weekStart) {
	const start = startOfWeek(key, weekStart);
	return [0, 1, 2, 3, 4, 5, 6].map((i) => addDays(start, i));
}

/** The first and last day to ask the server for. Always within `spa_rules.MAX_WINDOW_DAYS`. */
export function windowOf(days) {
	return { start: days[0], end: days[days.length - 1] };
}

/** `"2026-09-23 09:05:00"` -> `"2026-09-23"`, or `""`. */
export function datePart(value) {
	const match = /^(\d{4}-\d{2}-\d{2})/.exec(String(value || ""));
	return match ? match[1] : "";
}

/** `"2026-09-23 09:05:00"` -> `"09:05"`, or `""`. */
export function timePart(value) {
	const match = /^\d{4}-\d{2}-\d{2}[ T](\d{2}):(\d{2})/.exec(String(value || ""));
	return match ? `${match[1]}:${match[2]}` : "";
}

/**
 * The time a post dropped on `key` should get: the same time of day on the new day, or
 * `defaultTime` for a post that had none. The calendar moves days, never hours.
 */
export function moveToDay(scheduledAt, key, defaultTime) {
	const time = timePart(scheduledAt) || defaultTime || "09:00";
	return `${key} ${time}:00`;
}

/** `{day: [posts sorted by time]}` for the posts that have a time. */
export function bucketByDay(posts) {
	const buckets = {};
	for (const post of posts || []) {
		const key = datePart(post.scheduled_at);
		if (!key) continue;
		(buckets[key] = buckets[key] || []).push(post);
	}
	for (const key of Object.keys(buckets)) {
		buckets[key].sort((a, b) => String(a.scheduled_at).localeCompare(String(b.scheduled_at)));
	}
	return buckets;
}

/** `"09:05"` -> `"9:05 AM"`. The site is in the US; the clock is the 12-hour one. */
export function clockLabel(time) {
	const match = /^(\d{2}):(\d{2})/.exec(String(time || ""));
	if (!match) return "";
	const hour = Number(match[1]);
	const suffix = hour < 12 ? "AM" : "PM";
	return `${hour % 12 || 12}:${match[2]} ${suffix}`;
}

/** `"2026-09-23 09:05:00"` -> `"Wed, Sep 23, 9:05 AM"`. */
export function whenLabel(value) {
	const key = datePart(value);
	if (!key) return "";
	const time = clockLabel(timePart(value));
	return `${dayLabel(key)}${time ? `, ${time}` : ""}`;
}

/** `"2026-09-23"` -> `"Wed, Sep 23"`. */
export function dayLabel(key) {
	if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key || ""))) return "";
	const [, m, d] = String(key).split("-").map(Number);
	return `${WEEKDAYS[weekdayOf(key)].slice(0, 3)}, ${MONTHS[m - 1].slice(0, 3)} ${d}`;
}

/** Whether a day is before today. Both are `YYYY-MM-DD`, so text order is date order. */
export function isPast(key, today) {
	return String(key) < String(today);
}
