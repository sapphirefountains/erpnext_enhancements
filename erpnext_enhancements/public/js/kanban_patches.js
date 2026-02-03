/*
 * Kanban Enhancements for ERPNext (Updated for Frappe v16+)
 * - Touch Latency Fix (Drag Delay) via Global Sortable Patch
 */

frappe.provide("erpnext_enhancements.kanban");
frappe.provide("erpnext_enhancements.utils");

// ============================================================
// 1. Global Sortable Patch (Drag Delay)
// ============================================================
// This runs immediately to catch any Sortable instances created by Frappe.

(function apply_sortable_patch() {
    let attempts = 0;
    const max_attempts = 20;

    // Poll for Sortable because it might be loaded asynchronously
    const sortable_interval = setInterval(() => {
        attempts++;
        if (window.Sortable && !window.Sortable._patched) {
            
            const OriginalSortable = window.Sortable;
            
            // Proxy the constructor (or create method depending on implementation)
            // Sortable usually works via 'new Sortable(el, options)'
            window.Sortable = function(el, options) {
                if (!options) options = {};

                // Check if this is a Kanban component
                // We check if the element itself or any parent indicates it's part of a Kanban board
                let is_kanban = false;
                
                if (el) {
                    // Check class names of the element itself
                    if (el.className && typeof el.className === 'string' &&
                        (el.className.includes('kanban') || el.className.includes('meta-list'))) {
                        is_kanban = true;
                    }

                    // Check if inside a Kanban container (using jQuery if available, or native closest)
                    if (!is_kanban) {
                        if (typeof $ !== 'undefined' && $(el).closest && $(el).closest('.kanban').length > 0) {
                            is_kanban = true;
                        } else if (el.closest && el.closest('.kanban')) {
                            is_kanban = true;
                        }
                    }
                }

                if (is_kanban) {
                    // Completely disable drag and drop for Kanban
                    options.disabled = true;
                    options.sort = false;
                } else {
                    // Enforce 1000ms delay for better touch/mouse experience on busy boards
                    options.delay = 1000;
                    options.touchStartThreshold = 5;
                    options.animation = 150; // Smooth animation
                }

                // console.log("[Kanban Enhancements] Sortable initialized with 1000ms delay on", el);
                
                return new OriginalSortable(el, options);
            };

            // Copy static properties
            Object.assign(window.Sortable, OriginalSortable);
            window.Sortable.prototype = OriginalSortable.prototype;
            window.Sortable._patched = true; // Prevent double patching
            
            clearInterval(sortable_interval);
        } else if (attempts >= max_attempts) {
            clearInterval(sortable_interval);
        }
    }, 500);
})();
