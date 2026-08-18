/**
 * What the status pill should say, given the request's status and how its work is going.
 *
 * Targets: the `/feedback` SPA (list rows and the request detail header).
 * Loaded via: imported by `dom.js` and `app.js`; the whole SPA ships as `feedback.bundle.js`.
 *
 * **Why a derived label and not a real status.** `Tasks Created` is a *terminal* state in
 * `product_feedback/states.py` — its transition set is empty, deliberately, so the same
 * proposal can never be written to a Project twice. That is also why a request whose tasks
 * are all finished still reads "Tasks Created" on the board forever: nothing is allowed to
 * move it, and nothing should be.
 *
 * The fact that the work is done is already carried by the tasks themselves, and the API
 * already returns it (`tasks: {created, done}`, from `_task_progress`) on the same two
 * queries the page was making anyway. So this is a *display* rule over data the page holds,
 * not a second copy of the truth in a column that could drift. Reopen a task and the pill
 * goes back on its own, because the pill IS the tasks.
 *
 * Pure and DOM-free on purpose — same reason `routes.js` is, and it is what lets
 * `scripts/test_feedback_status.js` load it under plain node.
 */

/**
 * The label shown when every task a request produced is finished.
 *
 * **Not a `RequestState` value**, and `scripts/test_feedback_status.js` asserts it never
 * becomes one. If somebody later adds a stored status of this name, two different things
 * would be spelled identically — one meaning "the column says so" and one meaning "the
 * tasks say so" — and they would disagree the first time a task was reopened. The test
 * failing is the point: it forces that to be a decision rather than a collision.
 */
export const TASKS_COMPLETED = "Tasks Completed";

/** The stored status this rule reads. Terminal; see the module comment. */
export const TASKS_CREATED = "Tasks Created";

/**
 * `displayStatus("Tasks Created", {created: 2, done: 2})` -> `"Tasks Completed"`.
 *
 * Everything else is returned untouched, including a request with no work yet: `0/0` is
 * "nothing has been created", not "everything is finished", and `done >= created` would
 * call it complete if the zero case were not excluded first. That off-by-nothing is the
 * whole bug this function could plausibly have.
 */
export function displayStatus(status, progress) {
	if (status !== TASKS_CREATED) return status;
	const created = Number(progress && progress.created) || 0;
	const done = Number(progress && progress.done) || 0;
	if (created > 0 && done >= created) return TASKS_COMPLETED;
	return status;
}
