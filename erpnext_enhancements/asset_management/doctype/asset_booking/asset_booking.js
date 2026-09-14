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
 *
 * It also carries the two Rental Inspection buttons (ER-2026-312370). Those are gated on
 * `booking_type === "Rental"` on purpose: `api/booking.py::create_composite_booking` wraps
 * every rental in a Travel booking and a Maintenance booking an hour either side, so an
 * ungated button would offer a packing checklist on all three legs of every single rental.
 */

const INSPECTION_DIRECTIONS = [
    { direction: 'Pre-shipping', label: __('Pre-shipping Checklist') },
    { direction: 'Return', label: __('Return Checklist') },
];

/**
 * Add the two checklist buttons to a submitted Rental booking.
 *
 * The server decides whether to generate or hand back what already exists
 * (`generate_inspection` returns `created`), so the button does not need to look first —
 * one round trip, and no window in which two people both see "none yet" and make two.
 */
function add_inspection_buttons(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.booking_type !== 'Rental') {
        return;
    }

    INSPECTION_DIRECTIONS.forEach(({ direction, label }) => {
        frm.add_custom_button(
            label,
            () => {
                frappe.call({
                    method: 'erpnext_enhancements.api.booking.generate_inspection',
                    args: { booking: frm.doc.name, direction: direction },
                    freeze: true,
                    freeze_message: __('Preparing checklist...'),
                    callback: function (r) {
                        if (!r.message) {
                            return;
                        }
                        if (!r.message.created) {
                            frappe.show_alert({
                                message: __('Opening the existing checklist.'),
                                indicator: 'blue',
                            });
                        }
                        frappe.set_route('Form', 'Rental Inspection', r.message.inspection);
                    },
                });
            },
            __('Rental Inspection')
        );
    });
}

frappe.ui.form.on('Asset Booking', {
    refresh: function(frm) {
        add_inspection_buttons(frm);
    },
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
