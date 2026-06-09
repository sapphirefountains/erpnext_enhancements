/**
 * Account form script.
 *
 * Targets: the "Account" doctype form.
 * Loaded via: hooks.py `doctype_js["Account"]` (alongside vue.global.js +
 *   comments.js, which provide the custom Comments App).
 *
 * Mounts the custom Comments App into the `custom_comments_field` HTML field on
 * saved Accounts. The App itself is defined in comments.js
 * (erpnext_enhancements.render_comments_app). Account is one of the doctypes
 * deliberately excluded from comments_auto.js so the App is not mounted twice.
 */
frappe.ui.form.on("Account", {
    refresh: function(frm) {
        if (!frm.doc.__islocal) {
            if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
                erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
            } else {
                console.warn("erpnext_enhancements.render_comments_app is not defined.");
            }
        }
    }
});
