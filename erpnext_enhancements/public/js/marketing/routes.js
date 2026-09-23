/**
 * Client-side routing for `/marketing`, as **pure functions with no DOM and no fetch**, so a
 * plain node script can check it (`scripts/test_marketing_client.js`).
 *
 * The shell renders every one of these; `hooks.py` carries the `<path:marketing_path>` rule
 * that makes them survive a hard refresh:
 *
 *   /marketing                              -> month view, this month
 *   /marketing/calendar/2026-10             -> month view, October 2026
 *   /marketing/week/2026-09-21              -> week view, the week holding that day
 *   /marketing/new                          -> composer, a new post
 *   /marketing/new/2026-09-23               -> composer, a new post on that day
 *   /marketing/post/SPOST-00001             -> the post (composer while editable)
 *   /marketing/post/SPOST-00001/results     -> what happened to it on each network
 *   /marketing/queue                        -> the approval queue
 *   /marketing/media                        -> the media library
 *
 * Anything unrecognised is this month's calendar: a bad URL should land somewhere usable.
 */

export const VIEW_MONTH = "month";
export const VIEW_WEEK = "week";
export const VIEW_NEW = "new";
export const VIEW_POST = "post";
export const VIEW_RESULTS = "results";
export const VIEW_QUEUE = "queue";
export const VIEW_MEDIA = "media";

const BASE = "/marketing";
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** True for a real calendar day in `YYYY-MM-DD` (so 2026-02-30 is not one). */
export function isDay(value) {
	const match = DAY.exec(String(value || ""));
	if (!match) return false;
	const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
	const probe = new Date(Date.UTC(y, m - 1, d));
	return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d;
}

export function isMonth(value) {
	return MONTH.test(String(value || ""));
}

function route(view, extra) {
	return Object.assign({ view, month: "", day: "", name: "" }, extra || {});
}

/** Parse a pathname into a view. Never throws. */
export function parseRoute(pathname) {
	const parts = String(pathname || "")
		.split("?")[0]
		.split("/")
		.filter(Boolean);
	if (parts[0] !== "marketing") return route(VIEW_MONTH);
	const [section, arg, sub] = parts.slice(1);

	if (section === "calendar") return route(VIEW_MONTH, { month: isMonth(arg) ? arg : "" });
	if (section === "week") return route(VIEW_WEEK, { day: isDay(arg) ? arg : "" });
	if (section === VIEW_NEW) return route(VIEW_NEW, { day: isDay(arg) ? arg : "" });
	if (section === VIEW_QUEUE) return route(VIEW_QUEUE);
	if (section === VIEW_MEDIA) return route(VIEW_MEDIA);
	if (section === VIEW_POST && arg) {
		const name = decodeURIComponent(arg);
		return route(sub === VIEW_RESULTS ? VIEW_RESULTS : VIEW_POST, { name });
	}
	return route(VIEW_MONTH);
}

/** The inverse of `parseRoute`. `arg` is the month, the day, or the post's name. */
export function buildRoute(view, arg) {
	const value = arg || "";
	if (view === VIEW_MONTH) return isMonth(value) ? `${BASE}/calendar/${value}` : BASE;
	if (view === VIEW_WEEK) return isDay(value) ? `${BASE}/week/${value}` : `${BASE}/week`;
	if (view === VIEW_NEW) return isDay(value) ? `${BASE}/new/${value}` : `${BASE}/new`;
	if (view === VIEW_POST && value) return `${BASE}/post/${encodeURIComponent(value)}`;
	if (view === VIEW_RESULTS && value) return `${BASE}/post/${encodeURIComponent(value)}/results`;
	if (view === VIEW_QUEUE) return `${BASE}/queue`;
	if (view === VIEW_MEDIA) return `${BASE}/media`;
	return BASE;
}

/** Which tab is lit for a view. A post and its results belong to no tab. */
export function tabOf(view) {
	if (view === VIEW_MONTH || view === VIEW_WEEK) return "calendar";
	if (view === VIEW_NEW) return "new";
	if (view === VIEW_QUEUE) return "queue";
	if (view === VIEW_MEDIA) return "media";
	return "";
}
