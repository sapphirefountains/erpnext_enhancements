/**
 * The Desk's clock-in confirm card, for the framed Triton widget.
 *
 * The assistant was asked to clock the person in. Clocking in needs the ERPNext session and a
 * location fix from the device, and the framed page is allowed neither (its `allow` list has no
 * `geolocation`, on purpose). So the frame only ASKS, with a `clock-in` message, and expects
 * nothing back; the card, the location fix and the time-kiosk call are all here, on the Desk page.
 *
 * It is the classic widget's card (`renderClockInRequest` / `decideClockIn` in
 * `global_enhancements/triton_widget.js`) drawn on the Desk page instead of in a transcript, and
 * it keeps that card's rules, which are what make clock-in from a chat safe rather than merely
 * working (`tests/test_triton_widget_location.py` pins them for the classic widget, and
 * `scripts/test_triton_frame.mjs` pins them here):
 *
 * 1. A `clock-in` message NEVER clocks anyone in. It draws a card. Only the person pressing
 *    Confirm does anything, and Cancel or leaving the card alone does nothing at all.
 * 2. Location is asked for only inside the Confirm click, never when the card is drawn and never
 *    on page load, always fresh (`maximumAge: 0`) and precise (`enableHighAccuracy`). A cached or
 *    coarse fix is not evidence of where somebody is standing.
 * 3. Nothing in the message is a location. Only five keys are read from `params` (project,
 *    project_title, task, time_category, description); a coordinate the model typed is ignored.
 * 4. The clock-in call always carries the fix that was just taken. No fix (refused, timed out,
 *    no geolocation in this browser) means no clock-in, and the card says so.
 * 5. No coordinate is kept: not on the card, not in browser storage.
 *
 * The card reuses the classic action-card classes, so it looks like the card people know.
 * It is placed by `triton_frame.bundle.css` (`.triton-frame-clockin`), above the panel.
 */

const LOG_TIME = "erpnext_enhancements.api.time_kiosk.log_time";
const MAX_TEXT = 140;
const DISMISS_MS = 6000;
const GEO_OPTIONS = Object.freeze({ enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });

/** One short line of text, or null. Everything from the frame is cut down before it is shown or sent. */
function text(value) {
	if (typeof value !== "string") return null;
	const line = value.replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
	return line || null;
}

/** The five keys the card reads, and nothing else the message carried. */
export function readParams(params) {
	const source = params && typeof params === "object" && !Array.isArray(params) ? params : {};
	return {
		project: text(source.project),
		project_title: text(source.project_title),
		task: text(source.task),
		time_category: text(source.time_category),
		description: text(source.description),
	};
}

/**
 * @param {object} opts
 * @param {Window} opts.win
 * @returns {{show: Function, destroy: Function}}
 */
export function createClockIn({ win }) {
	const doc = win.document;
	const frappe = win.frappe || {};
	// frappe's __() translates and fills {0}; off the Desk (the tests) only the filling is left.
	const t = (words, args) =>
		typeof win.__ === "function"
			? win.__(words, args)
			: String(words).replace(/\{(\d+)\}/g, (all, i) => (args && args[i] !== undefined ? args[i] : all));
	let host = null;
	let timer = null;

	function el(tag, className, words) {
		const node = doc.createElement(tag);
		if (className) node.className = className;
		if (words) node.textContent = words;
		return node;
	}

	function close() {
		if (timer) win.clearTimeout(timer);
		timer = null;
		if (host && host.parentNode) host.parentNode.removeChild(host);
		host = null;
	}

	/** Replace the buttons with one line saying how it went. `ok` picks the colour. */
	function resolve(slot, words, ok, autoClose) {
		while (slot.firstChild) slot.removeChild(slot.firstChild);
		slot.appendChild(el("span", "triton-action-resolved " + (ok ? "ok" : "no"), words));
		if (autoClose) {
			timer = win.setTimeout(close, DISMISS_MS);
			return;
		}
		const dismiss = el("button", "triton-decline triton-frame-clockin-dismiss", t("Close"));
		dismiss.type = "button";
		dismiss.addEventListener("click", close);
		slot.appendChild(dismiss);
	}

	// Rule 2: this runs only from the Confirm click below.
	function confirm(params, slot) {
		const noFix = t("Clocking in needs location and nothing was shared.");
		const geo = win.navigator && win.navigator.geolocation;
		if (!geo || typeof geo.getCurrentPosition !== "function") {
			resolve(slot, noFix, false, false);
			return;
		}
		while (slot.firstChild) slot.removeChild(slot.firstChild);
		slot.appendChild(el("span", "text-muted", t("Getting your location…")));

		geo.getCurrentPosition(
			(position) => {
				const coords = (position && position.coords) || {};
				// Rule 4: the fix just taken goes with the call, and only with the call.
				Promise.resolve()
					.then(() =>
						frappe.xcall(LOG_TIME, {
							action: "Start",
							project: params.project,
							task: params.task,
							time_category: params.time_category,
							description: params.description,
							lat: coords.latitude,
							lng: coords.longitude,
							accuracy: coords.accuracy,
						})
					)
					.then((result) => {
						let words = t("Clocked in to {0}.", [params.project_title || params.project]);
						const offsite = result && result.offsite;
						if (offsite && offsite.flagged && offsite.distance_m) {
							words += " " + t("{0} m from the site.", [Math.round(offsite.distance_m)]);
						}
						resolve(slot, "✓ " + words, true, true);
					})
					.catch((error) => {
						// frappe.xcall has already shown the server's own words for a refusal.
						const why = error && typeof error.message === "string" ? ": " + error.message.slice(0, 200) : ".";
						resolve(slot, t("You were not clocked in") + why, false, false);
					});
			},
			() => resolve(slot, noFix, false, false),
			GEO_OPTIONS
		);
	}

	/** Draw the card for one request. A second request replaces the first. Rule 1: this is all it does. */
	function show(rawParams) {
		const params = readParams(rawParams);
		close();
		// With no project there is nothing to clock in to; the time kiosk would refuse it anyway.
		if (!params.project) return false;

		host = el("div", "triton-frame-clockin");
		host.setAttribute("role", "alertdialog");
		host.setAttribute("aria-label", t("Clock in"));
		const card = el("div", "triton-action-card");
		card.appendChild(el("div", "triton-action-summary", t("Clock in to {0}?", [params.project_title || params.project])));
		const detail = [params.task, params.time_category, params.description].filter(Boolean).join(" · ");
		if (detail) card.appendChild(el("div", "triton-action-desc", detail));
		const slot = el("div", "triton-action-slot");
		const buttons = el("div", "triton-action-btns");
		const yes = el("button", "triton-approve", t("Confirm"));
		yes.type = "button";
		const no = el("button", "triton-decline", t("Cancel"));
		no.type = "button";
		yes.addEventListener("click", () => confirm(params, slot));
		no.addEventListener("click", close);
		buttons.appendChild(yes);
		buttons.appendChild(no);
		slot.appendChild(buttons);
		card.appendChild(slot);
		host.appendChild(card);
		doc.body.appendChild(host);
		if (typeof yes.focus === "function") yes.focus();
		return true;
	}

	return { show: show, destroy: close };
}
