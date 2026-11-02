/*
 * Retired Desk pages, and where their addresses go now.
 *
 * frappe's router looks a path up in frappe.re_route before it parses it (router.js, route()
 * then re_route(), read at v16.53.0) and sends a match on with frappe.set_route. Its check against
 * the page being left makes Back step over the old address instead of bouncing on it, so a
 * bookmark, a link in an email or an old Route History suggestion lands on the new page and the
 * browser's Back and Forward keep working.
 *
 * Keys are the sub path after /desk/ (or /app/), lower case, exactly as the router spells it.
 *
 *   water-engineering-wizard -> fountain-designer   v1.771.0, Fountain Designer phase H: the
 *       wizard was retired and its quick calculator is the Calculator drawer on the Review step.
 */
(function () {
	if (typeof frappe === "undefined") return;
	frappe.re_route = frappe.re_route || {};
	const RETIRED = {
		"water-engineering-wizard": "fountain-designer",
	};
	Object.keys(RETIRED).forEach((from) => {
		if (frappe.re_route[from] === undefined) frappe.re_route[from] = RETIRED[from];
	});
})();
