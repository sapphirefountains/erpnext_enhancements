/**
 * Client-side routing for `/feedback`, as **pure functions with no DOM and no fetch**.
 *
 * Kept separate from `app.js` so it can be exercised by a plain node script, which is the
 * only automatic regression protection browser code gets in this repo. Same reasoning as
 * `public/js/chat/routes.js`.
 *
 * Three routes, and the shell renders all of them:
 *
 *   /feedback                          -> {view: "new"}
 *   /feedback/mine                     -> {view: "mine"}
 *   /feedback/review                   -> {view: "review"}
 *   /feedback/request/ER-2026-00001    -> {view: "request", name: "ER-2026-00001"}
 *
 * `hooks.py` carries the `<path:feedback_path>` rule that makes the last two survive a hard
 * refresh. **Every link this SPA builds and every link a notification sends must agree with
 * `buildRoute` here** — `product_feedback/notify.py` writes `/feedback/request/<name>`, and a
 * divergence is a dead link in an email nobody tests.
 */

export const VIEW_NEW = "new";
export const VIEW_MINE = "mine";
export const VIEW_REVIEW = "review";
export const VIEW_REQUEST = "request";

const BASE = "/feedback";

/**
 * Parse a pathname into a view. Never throws; anything unrecognised is the default view,
 * because a bad URL should land somewhere usable rather than on an error page.
 */
export function parseRoute(pathname) {
	const path = String(pathname || "");
	const parts = path.split("/").filter(Boolean);

	// ["feedback", ...rest]
	if (!parts.length || parts[0] !== "feedback") return { view: VIEW_NEW, name: "" };

	const section = parts[1] || "";
	if (section === VIEW_MINE) return { view: VIEW_MINE, name: "" };
	if (section === VIEW_REVIEW) return { view: VIEW_REVIEW, name: "" };
	if (section === VIEW_REQUEST && parts[2]) {
		return { view: VIEW_REQUEST, name: decodeURIComponent(parts[2]) };
	}
	return { view: VIEW_NEW, name: "" };
}

/** The inverse of `parseRoute`. Must byte-match what `notify.py` writes. */
export function buildRoute(view, name) {
	if (view === VIEW_REQUEST && name) return `${BASE}/request/${encodeURIComponent(name)}`;
	if (view === VIEW_MINE) return `${BASE}/mine`;
	if (view === VIEW_REVIEW) return `${BASE}/review`;
	return BASE;
}

/**
 * Which view a signed-in person should land on when they arrive at the bare route.
 *
 * Reviewers get the queue: they open this page to decide things, and filing is the rarer
 * act for them. Everybody else gets the form, because for them the queue is empty and "my
 * requests" is a list they only want after they have made one.
 */
export function defaultView(isReviewer, hasRequests) {
	if (isReviewer) return VIEW_REVIEW;
	return hasRequests ? VIEW_MINE : VIEW_NEW;
}
