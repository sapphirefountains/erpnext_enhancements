/*
 * form kit P4-B: the guided page's one save path.
 *
 * Every change the person makes goes through here, and nothing else on the page writes the
 * record (step 1's insert is the page's create_record, before there is a record to queue for).
 * Kept as one module, with the transport injected as `send`, so the offline queue (P4.7) can wrap
 * it rather than find a second path.
 *
 *   const queue = create_save_queue({ send, on_state, on_saved, on_conflict, set_timeout, clear_timeout });
 *   queue.set_modified(modified)       the `modified` of the last good load
 *   queue.change(fieldname, value)     a field now differs from what is saved: autosave after `delay`
 *   queue.discard(fieldname)           it is back to what is saved: nothing to send for it
 *   queue.flush()                      save now; resolves when nothing is left, rejects on failure
 *   queue.retry()                      after an error or a sign-in, try again
 *
 * The rules, each tested (tests/test_guided_entry.py):
 *
 * - One request in flight at a time. Changes made while one is out wait in `pending` and coalesce
 *   by field (the newest value wins), and go in the next request.
 * - Autosave runs `delay` ms (2.5 s) after the last change; flush() cancels the wait and saves now.
 * - Every request carries the `modified` of the last good load or save, and a success replaces
 *   it with the one returned. on_saved(result, sent) is told what was sent so the page can show
 *   the returned (normalized) values for exactly those fields, except any changed again since.
 * - A conflict (`{conflict: true}`, returned, not raised) stops the queue, and waiting flush()
 *   calls reject with `{conflict: true}`. on_conflict(result, sent) returns a promise of
 *   `{ save, modified }`, settled when the person has chosen; `save` is put back under anything
 *   typed since (the newer typing wins), the new `modified` is taken, and it saves again. A null
 *   answer sets the conflict aside: what was sent waits under the old `modified` and the next
 *   save asks again.
 * - A failure puts what was sent back under anything typed since, and stops: state "error", or
 *   "expired" when the session is gone (no autosave then, it would only be refused again; retry()
 *   after the person signs in). Waiting flush() calls reject with the error, so the page keeps the
 *   person on the step for a validation message.
 *
 * States, for the always-visible saving line: "saved", "dirty", "saving", "error", "conflict",
 * "expired". Pure: no frappe, no DOM, timers injected.
 */

export const STATES = ["saved", "dirty", "saving", "error", "conflict", "expired"];

export function create_save_queue(opts) {
	const delay = Number(opts.delay) > 0 ? Number(opts.delay) : 2500;
	const set_timeout = opts.set_timeout || ((fn, ms) => setTimeout(fn, ms));
	const clear_timeout = opts.clear_timeout || ((id) => clearTimeout(id));

	let modified = null;
	let pending = {};
	let inflight = null; // { values, promise }
	let state = "saved";
	let last_error = null;
	let timer = null;
	let waiters = [];
	let conflict_open = false;

	function count(map) {
		return Object.keys(map).length;
	}

	function set_state(next, error) {
		state = next;
		last_error = error || null;
		if (typeof opts.on_state === "function") opts.on_state(state, last_error);
	}

	function settle(error) {
		const list = waiters;
		waiters = [];
		list.forEach((w) => (error ? w.reject(error) : w.resolve()));
	}

	function cancel_timer() {
		if (timer !== null) clear_timeout(timer);
		timer = null;
	}

	function schedule() {
		cancel_timer();
		if (state === "expired" || state === "conflict") return;
		timer = set_timeout(() => {
			timer = null;
			flush().catch(() => {});
		}, delay);
	}

	function idle_state() {
		return count(pending) ? "dirty" : "saved";
	}

	// Put `values` back beneath anything typed since they were taken: the newer typing wins.
	function put_back(values) {
		pending = Object.assign({}, values, pending);
	}

	function send_next() {
		if (inflight || conflict_open) return;
		if (!count(pending)) {
			set_state("saved");
			settle(null);
			return;
		}
		const values = pending;
		pending = {};
		set_state("saving");
		let request;
		try {
			request = Promise.resolve(opts.send(values, modified));
		} catch (e) {
			request = Promise.reject(e);
		}
		inflight = { values, promise: request };
		request.then(
			(result) => {
				inflight = null;
				if (result && result.conflict) return on_conflict(result, values);
				if (result && result.modified) modified = result.modified;
				if (typeof opts.on_saved === "function") opts.on_saved(result || {}, values);
				if (count(pending)) {
					set_state("saving");
					send_next();
				} else {
					set_state("saved");
					settle(null);
				}
			},
			(error) => {
				inflight = null;
				put_back(values);
				const expired = !!(error && error.session_expired);
				set_state(expired ? "expired" : "error", error || {});
				settle(error || {});
			}
		);
	}

	function on_conflict(result, sent) {
		conflict_open = true;
		set_state("conflict");
		const conflict_error = { conflict: true, result, message: "" };
		settle(conflict_error);
		let answer;
		try {
			answer = Promise.resolve(typeof opts.on_conflict === "function" ? opts.on_conflict(result, sent) : null);
		} catch (e) {
			answer = Promise.resolve(null);
		}
		return answer.then(
			(decision) => {
				conflict_open = false;
				if (!decision) {
					// Set aside: what was sent waits, unsent, under the old `modified`, so the next
					// save meets the same conflict and asks again.
					put_back(sent);
					set_state("dirty");
					return;
				}
				if (decision.modified) modified = decision.modified;
				put_back(decision.save || {});
				if (count(pending)) {
					set_state("dirty");
					send_next();
				} else {
					set_state("saved");
				}
			},
			() => {
				conflict_open = false;
				put_back(sent);
				set_state("error", { message: "" });
			}
		);
	}

	function flush() {
		cancel_timer();
		if (conflict_open) {
			return Promise.reject({ conflict: true, message: "" });
		}
		if (!inflight && !count(pending)) return Promise.resolve();
		const promise = new Promise((resolve, reject) => waiters.push({ resolve, reject }));
		if (state === "error" || state === "expired") set_state(idle_state());
		send_next();
		return promise;
	}

	return {
		set_modified(value) {
			modified = value || null;
		},
		get modified() {
			return modified;
		},
		get state() {
			return state;
		},
		get error() {
			return last_error;
		},
		change(fieldname, value) {
			pending[fieldname] = value;
			if (state === "saved") set_state("dirty");
			schedule();
		},
		discard(fieldname) {
			delete pending[fieldname];
			if (state === "dirty" && !count(pending)) set_state("saved");
		},
		pending() {
			return Object.assign({}, pending);
		},
		in_flight() {
			return inflight ? Object.assign({}, inflight.values) : null;
		},
		// Anything not yet stored: typed and waiting, out in a request, or held by a conflict.
		has_unsaved() {
			return !!(count(pending) || inflight || conflict_open);
		},
		flush,
		retry() {
			return flush();
		},
		cancel() {
			cancel_timer();
		},
	};
}
