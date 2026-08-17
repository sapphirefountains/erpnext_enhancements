/**
 * Client-side routing for `/feedback`, as **pure functions with no DOM and no fetch**.
 *
 * Kept separate from `app.js` so it can be exercised by a plain node script, which is the
 * only automatic regression protection browser code gets in this repo. Same reasoning as
 * `public/js/chat/routes.js`. That script is `scripts/test_feedback_routes.js`.
 *
 * Four routes, and the shell renders all of them:
 *
 *   /feedback                          -> {view: "new", bare: true}   "take me somewhere useful"
 *   /feedback/new                      -> {view: "new"}               "I want the form"
 *   /feedback/mine                     -> {view: "mine"}
 *   /feedback/review                   -> {view: "review"}
 *   /feedback/request/ER-2026-00001    -> {view: "request", name: "ER-2026-00001"}
 *
 * `hooks.py` carries the `<path:feedback_path>` rule that makes all but the first survive a
 * hard refresh. **Every link this SPA builds and every link a notification sends must agree
 * with `buildRoute` here** — `product_feedback/notify.py` writes `/feedback/request/<name>`,
 * and a divergence is a dead link in an email nobody tests.
 *
 * --------------------------------------------------------------------------------------
 * Why the form has its own URL, and why `bare` exists
 * --------------------------------------------------------------------------------------
 *
 * v1.319.0 gave "New request" the bare `/feedback` href and let the router turn a bare path
 * into whichever view suited the caller. Both rules are correct alone; they compose into a
 * redirect loop of one hop. A System Manager clicking "New request" navigated to `/feedback`,
 * the router read that as a fresh landing, and sent them straight back to the review queue —
 * so the tab looked dead and **the form was unreachable for anyone who reviews**. A
 * non-reviewer who had filed before was bounced to "My requests" the same way.
 *
 * So the two intentions get two URLs. `bare` distinguishes them, `landingView` is the only
 * thing that reads it, and **only `mount()` may call `landingView`** — once somebody clicks a
 * tab they have said what they want, and a router that keeps overriding that is not routing.
 */

export const VIEW_NEW = "new";
export const VIEW_MINE = "mine";
export const VIEW_REVIEW = "review";
export const VIEW_ALL = "all";
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
	if (!parts.length || parts[0] !== "feedback") return { view: VIEW_NEW, name: "", bare: true };

	const section = parts[1] || "";
	if (section === VIEW_MINE) return { view: VIEW_MINE, name: "", bare: false };
	if (section === VIEW_REVIEW) return { view: VIEW_REVIEW, name: "", bare: false };
	if (section === VIEW_ALL) return { view: VIEW_ALL, name: "", bare: false };
	if (section === VIEW_NEW) return { view: VIEW_NEW, name: "", bare: false };
	if (section === VIEW_REQUEST && parts[2]) {
		return { view: VIEW_REQUEST, name: decodeURIComponent(parts[2]), bare: false };
	}
	// `/feedback`, and also anything unrecognised.
	return { view: VIEW_NEW, name: "", bare: true };
}

/** The inverse of `parseRoute`. Must byte-match what `notify.py` writes. */
export function buildRoute(view, name) {
	if (view === VIEW_REQUEST && name) return `${BASE}/request/${encodeURIComponent(name)}`;
	if (view === VIEW_MINE) return `${BASE}/mine`;
	if (view === VIEW_REVIEW) return `${BASE}/review`;
	if (view === VIEW_ALL) return `${BASE}/all`;
	// Explicit, so clicking the tab is distinguishable from arriving at the bare route — and
	// so the form survives a refresh and can be linked to.
	if (view === VIEW_NEW) return `${BASE}/new`;
	return BASE;
}

/**
 * Which view a signed-in person should land on when they arrive at the **bare** route.
 *
 * Reviewers get the queue: they open this page to decide things, and filing is the rarer
 * act for them. Everybody else gets the form, because for them the queue is empty and "my
 * requests" is a list they only want after they have made one.
 */
export function defaultView(isReviewer, hasRequests) {
	if (isReviewer) return VIEW_REVIEW;
	return hasRequests ? VIEW_MINE : VIEW_NEW;
}

/**
 * The view to redirect to on **initial load**, or `null` to stay put.
 *
 * `null` for every explicit path, which is the whole point: it is impossible to express
 * "override the view somebody just clicked" through this function, so the bug it was
 * extracted from cannot come back by someone calling it from the wrong place.
 *
 * Only `FeedbackApp.mount()` may call it.
 */
export function landingView(pathname, isReviewer, hasRequests) {
	const route = parseRoute(pathname);
	if (!route.bare) return null;
	const preferred = defaultView(isReviewer, hasRequests);
	return preferred === VIEW_NEW ? null : preferred;
}
