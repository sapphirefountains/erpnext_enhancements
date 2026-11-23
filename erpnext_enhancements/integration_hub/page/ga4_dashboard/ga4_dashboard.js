/*
 * Desk page: GA4 Dashboard (route /desk/ga4-dashboard). Retired in v1.756.0.
 *
 * It asked Google live for the last 30 days on every load, and Route History showed nobody had
 * opened it in 120 days. Marketing Performance (/desk/marketing-performance) replaces it, reading
 * GA4 and Search Console from the daily tables the nightly pull keeps. The Page record stays so old
 * links and bookmarks land somewhere: this replaces the history entry (Back does not bounce here
 * again) with the new page, and passes on nothing, because the old page took no options.
 */
frappe.pages["ga4-dashboard"].on_page_load = function () {};

frappe.pages["ga4-dashboard"].on_page_show = function () {
	frappe.route_flags.replace_route = true;
	frappe.set_route("marketing-performance");
	// Read by the router's push, which has already run; left set it would turn the next route into a replace.
	delete frappe.route_flags.replace_route;
};
