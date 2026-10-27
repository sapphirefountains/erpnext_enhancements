/*
 * Stock Scan lives at /stock-scan, a phone web app outside the desk (www/stock-scan.html). This
 * desk page only sends the browser there, so the Dock rail can offer it: a rail row opens through
 * frappe.set_route, which reads any path as a desk route, and a row naming /stock-scan itself would
 * land on /desk/stock-scan. The field technicians' rail does exactly that (v1.751.0,
 * setup/dock_profiles.py), the same way /desk/time-kiosk leads to /kiosk.
 *
 * location.replace() keeps this page out of history, so Back from Stock Scan returns to the page
 * the rail was clicked on instead of bouncing straight back here.
 */
const redirect_to_stock_scan = function () {
	window.location.replace("/stock-scan");
};

const page = frappe.pages["stock-scan"] || (frappe.pages["stock-scan"] = {});
page.on_page_load = redirect_to_stock_scan;
page.on_page_show = redirect_to_stock_scan;
