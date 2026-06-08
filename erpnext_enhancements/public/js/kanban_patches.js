/*
 * Kanban "hold to grab" — touch drag delay
 *
 * THE PROBLEM:
 *   On touch screens it is far too easy to brush a Kanban card and have it
 *   immediately picked up and dropped into the wrong column. Frappe's Kanban
 *   starts a drag the moment a touch lands on a card, with no press-and-hold
 *   gesture, so an incidental tap-and-drift reorders/moves the card.
 *
 * WHY THE OLD APPROACH DIDN'T WORK:
 *   This file used to proxy the global `window.Sortable` constructor to inject a
 *   drag delay. Frappe v16's Kanban board imports SortableJS as a *bundled ES
 *   module* (not `window.Sortable`), so the proxy never saw the real card-drag
 *   instances — the delay was never applied and cards still dragged instantly.
 *   (The proxy also *disabled* Kanban drag entirely, which is the opposite of
 *   what we want here.)
 *
 * THE FIX:
 *   SortableJS stores each live instance on its own DOM element under an expando
 *   key and sets `instance.el` back to that element. After a board renders we walk
 *   the board DOM, recover each card-container's Sortable instance from its
 *   element, and set the drag options live:
 *     - delay              : press-and-hold time before a drag may begin
 *     - delayOnTouchOnly   : true  -> mouse dragging stays instant (desktop is
 *                            unaffected); only touch must hold
 *     - touchStartThreshold: a small px drift cancels the pending drag, so a swipe
 *                            still scrolls the column instead of grabbing a card
 *   SortableJS reads `options.delay`/`delayOnTouchOnly` live at pointer-down, so
 *   mutating them on an existing instance via `.option()` takes effect immediately.
 *
 *   We re-scan on a few short delays after each board build because the Sortables
 *   are created asynchronously on Vue mount. Each instance is flagged once patched,
 *   so repeated scans are idempotent. Same-board filter refreshes reuse the
 *   existing (already-patched) Sortables, so nothing accumulates.
 *
 * Tuning: bump HOLD_DELAY_MS (ms) to require a longer hold; flip TOUCH_ONLY to
 * false to also require the hold for mouse drags.
 */

frappe.provide("frappe.views");

(function patch_kanban_hold_to_drag() {
    const HOLD_DELAY_MS = 1000; // press-and-hold this long before a touch can drag a card
    const TOUCH_ONLY = true; // delay touch only; leave mouse dragging instant
    const TOUCH_MOVE_TOLERANCE = 8; // px a finger may drift during the hold before it's treated as a scroll

    // Recover the SortableJS instance attached to a DOM element. SortableJS assigns
    // `el[expando] = instance` (expando looks like "Sortable<timestamp>") and the
    // instance carries `el` back-reference + an `option()` method — enough to find
    // it without a reference to the (module-bundled, non-global) Sortable class.
    function get_sortable(el) {
        // getOwnPropertyNames (not Object.keys) so a non-enumerable expando is still
        // found; it returns only the element's own props (expandos), never inherited
        // DOM properties, so the scan stays tiny.
        for (const key of Object.getOwnPropertyNames(el)) {
            const value = el[key];
            if (
                value &&
                typeof value === "object" &&
                value.el === el &&
                typeof value.option === "function"
            ) {
                return value;
            }
        }
        return null;
    }

    // Apply the hold-to-drag options to every card Sortable inside the board root.
    function apply_hold_delay(root) {
        if (!root || !root.querySelectorAll) return;
        // The card containers carry a "kanban-*" class; the expando check below
        // discards every non-Sortable element this broad selector also matches.
        root.querySelectorAll('[class*="kanban"]').forEach((el) => {
            const sortable = get_sortable(el);
            if (!sortable || sortable.__hold_to_drag) return;
            sortable.option("delay", HOLD_DELAY_MS);
            sortable.option("delayOnTouchOnly", TOUCH_ONLY);
            sortable.option("touchStartThreshold", TOUCH_MOVE_TOLERANCE);
            sortable.__hold_to_drag = true;
        });
    }

    function patch(KanbanView) {
        if (
            !KanbanView ||
            !KanbanView.prototype ||
            KanbanView.prototype._hold_to_drag_patched
        ) {
            return false;
        }

        const original_render = KanbanView.prototype.render;

        KanbanView.prototype.render = function () {
            const result = original_render.apply(this, arguments);
            // Sortables are built on Vue mount (after render returns), so re-scan on a
            // few short delays. Scope to this view's result area, falling back to the
            // document if it isn't resolvable yet.
            const root =
                (this.$result && this.$result[0]) ||
                (this.wrapper && this.wrapper[0]) ||
                document;
            [0, 150, 400, 1000].forEach((ms) =>
                setTimeout(() => apply_hold_delay(root), ms)
            );
            return result;
        };

        KanbanView.prototype._hold_to_drag_patched = true;
        return true;
    }

    // Patch now if the Kanban view class is already loaded (it is when our sibling
    // Kanban patches run); otherwise poll briefly for the lazily-loaded class.
    if (!patch(frappe.views && frappe.views.KanbanView)) {
        let attempts = 0;
        const timer = setInterval(() => {
            attempts += 1;
            if (patch(frappe.views && frappe.views.KanbanView) || attempts >= 40) {
                clearInterval(timer);
            }
        }, 250);
    }
})();
