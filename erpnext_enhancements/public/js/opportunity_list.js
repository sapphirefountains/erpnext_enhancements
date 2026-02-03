frappe.provide("frappe.listview_settings");

frappe.listview_settings['Opportunity'] = frappe.listview_settings['Opportunity'] || {};

// Preserve existing onload if any
const existing_onload_opportunity = frappe.listview_settings['Opportunity'].onload;

frappe.listview_settings['Opportunity'].onload = function(listview) {
    if (existing_onload_opportunity) {
        existing_onload_opportunity(listview);
    }

    // Only apply redirection logic if we are rendering the standard 'List' view
    if (listview.view_name === 'List') {
        const prev_route = frappe.get_prev_route();

        // Determine if the user is coming from outside the Opportunity module.
        // If prev_route is null, undefined, or empty, it's a fresh load/reload -> Redirect.
        // If 'opportunity' is NOT found in prev_route, it's from another module -> Redirect.
        // If 'opportunity' IS found, they came from Kanban, Form, or List -> Stay.

        let came_from_outside = true;

        if (prev_route && prev_route.length > 1) {
            // We only want to stay on List view (block redirect) if we came from:
            // 1. Kanban View of Opportunity (Manual switch)
            // 2. List View of Opportunity (Filter change/Navigation within list)

            // Route for List/Kanban is ['List', 'Opportunity', 'List'|'Kanban', ...]
            // We check if the first segment is 'List' and the second is 'Opportunity'
            if (prev_route[0] === 'List' && typeof prev_route[1] === 'string' && prev_route[1].toLowerCase() === 'opportunity') {
                came_from_outside = false;
            }
        }

        if (came_from_outside) {
            // Redirect to the 'Opportunity' Kanban board.
            // Route format: [doctype, 'view', 'kanban', board_name]
            frappe.set_route('opportunity', 'view', 'kanban', 'Opportunity');
        }
    }
};
