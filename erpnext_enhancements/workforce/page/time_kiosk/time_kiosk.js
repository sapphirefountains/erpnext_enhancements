/*
 * The Time Kiosk now lives at /kiosk as a standalone, installable PWA
 * (see erpnext_enhancements/www/kiosk.*). This desk page is kept only as a
 * redirect so existing bookmarks / links to /app/time-kiosk (or the legacy
 * /desk/time-kiosk) land on the new app instead of the retired in-desk UI.
 *
 * location.replace() avoids leaving the desk page in history, so the browser's
 * back button doesn't bounce the user straight back here.
 */
const redirect_to_kiosk = function () {
    window.location.replace('/kiosk');
};

const page = frappe.pages['time-kiosk'] || (frappe.pages['time-kiosk'] = {});
page.on_page_load = redirect_to_kiosk;
page.on_page_show = redirect_to_kiosk;
