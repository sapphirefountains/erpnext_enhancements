/*
 * Kanban Enhancements for ERPNext (Updated for Frappe v16+)
 * - Touch Latency Fix (Drag Delay) via Global Sortable Patch
 */

console.log("[Kanban Enhancements] Loading...");

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
            console.log("[Kanban Enhancements] Patching Sortable for drag delay...");
            
            const OriginalSortable = window.Sortable;
            
            // Proxy the constructor (or create method depending on implementation)
            // Sortable usually works via 'new Sortable(el, options)'
            window.Sortable = function(el, options) {
                if (!options) options = {};
                
                // Enforce 1000ms delay for better touch/mouse experience on busy boards
                options.delay = 1000;
                options.touchStartThreshold = 5; 
                options.animation = 150; // Smooth animation

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
