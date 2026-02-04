// Copyright (c) 2024, Sapphire Fountains and contributors
// For license information, please see license.txt

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
                method: "erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.check_availability",
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
