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
