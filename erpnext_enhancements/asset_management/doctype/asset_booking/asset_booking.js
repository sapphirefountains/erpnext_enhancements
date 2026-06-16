// Copyright (c) 2024, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Desk form script for the Asset Booking doctype.
 *
 * Loaded automatically when the Asset Booking form opens. Whenever the asset or
 * either datetime changes, it calls the whitelisted `check_availability` server
 * method and shows a red msgprint if the chosen window clashes with an existing
 * booking for that asset (excluding the current record). This is an early-warning
 * UX check; the authoritative overlap guard is the server-side `check_overlap`
 * validate hook.
 */
frappe.ui.form.on('Asset Booking', {
    asset: function(frm) {
        frm.trigger('check_availability');
    },
    from_datetime: function(frm) {
        frm.trigger('check_availability');
    },
    to_datetime: function(frm) {
        frm.trigger('check_availability');
    },
    check_availability: function(frm) {
        if (frm.doc.asset && frm.doc.from_datetime && frm.doc.to_datetime) {
            frappe.call({
                method: "erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.check_availability",
                args: {
                    asset: frm.doc.asset,
                    from_datetime: frm.doc.from_datetime,
                    to_datetime: frm.doc.to_datetime,
                    ignore_booking: frm.doc.name
                },
                callback: function(r) {
                    if (r.message && !r.message.available) {
                        frappe.msgprint({
                            title: __('Unavailable'),
                            message: r.message.message,
                            indicator: 'red'
                        });
                    }
                }
            });
        }
    }
});
