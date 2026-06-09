/*
 * Kanban drag-to-scroll perf fix — layout thrash in frappe core's bind_clickdrag.
 *
 * Targets: every `.kanban` board (the bug is in frappe core). Loaded via hooks.py
 *   `app_include_js`. See CHANGELOG for additional detail.
 *
 * THE BUG (frappe/frappe kanban_board.bundle.js -> bind_clickdrag):
 *   The board's "grab the background and drag sideways to reveal more columns"
 *   feature binds a mousemove handler that READS layout on every move:
 *
 *       draggable.addEventListener("mousemove", (e) => {
 *           if (!isDown) return;
 *           e.preventDefault();
 *           const x = e.pageX - draggable.offsetLeft;     // <-- forced reflow
 *           const walk = x - startX;
 *           draggable.scrollLeft = scrollLeft - walk;      // <-- dirties layout
 *       });
 *
 *   Reading `offsetLeft` immediately after the previous move wrote `scrollLeft`
 *   forces a SYNCHRONOUS full-document style/layout recalc. On a large board the
 *   tree is huge -- in our Chrome trace the Opportunity board recalculated
 *   ~34,800 elements at up to ~88ms per move. At 60fps that is ~5 dropped frames
 *   for every mousemove, so dragging fast side to side stutters badly (the
 *   mousemove handler alone accounted for ~0.55s of main-thread time across one
 *   short drag session).
 *
 * THE FIX (client-side, no core changes):
 *   `draggable.offsetLeft` is CONSTANT while you scroll the board horizontally,
 *   and it cancels out algebraically:
 *       walk = (e.pageX - offsetLeft) - (startPageX - offsetLeft) = e.pageX - startPageX
 *   so the read is pure waste. We install a single capture-phase pointer handler
 *   that reimplements drag-to-scroll using only `e.pageX` (no layout read) and
 *   `stopPropagation()`s the mousemove during an active drag so frappe's
 *   reflow-forcing handler never runs. Writing `scrollLeft` on its own does not
 *   force a synchronous recalc (only reading geometry afterwards does), so the
 *   stutter disappears. We mirror frappe's exact ignore-selectors, so which
 *   areas initiate a drag-scroll is unchanged. Applies to every `.kanban` board
 *   (the bug is core), and composes with our other Kanban patches -- it only
 *   touches the background drag-scroll gesture, not render_card / get_data /
 *   totals / card-drag-disable.
 *
 * Bound once at the document level for the page session: no per-render rebind,
 * nothing to leak. REMOVE once frappe core stops reading offsetLeft on mousemove
 * in bind_clickdrag.
 */

(function patch_kanban_drag_scroll_thrash() {
    // Same selectors frappe's bind_clickdrag bails on, so we never hijack a
    // column drag, a card, the "+ Add Card" affordance or a column header.
    const IGNORE = [
        ".kanban-column .kanban-column-header",
        ".kanban-column .add-card",
        ".kanban-column .kanban-card.new-card-area",
        ".kanban-card-wrapper",
    ].join(",");

    let board = null;       // the .kanban element currently being drag-scrolled
    let startPageX = 0;     // e.pageX captured at drag start
    let startScrollLeft = 0; // board.scrollLeft captured at drag start

    function endDrag() {
        if (!board) return;
        board.classList.remove("clickdrag-active");
        board = null;
    }

    function onMouseDown(e) {
        if (e.button !== 0) return; // primary button only, like frappe
        const t = e.target;
        if (!t || !t.closest) return;
        const b = t.closest(".kanban");
        if (!b || t.closest(IGNORE)) return;

        board = b;
        startPageX = e.pageX;
        startScrollLeft = b.scrollLeft;
        b.classList.add("clickdrag-active");
        // Deliberately do NOT stopPropagation here: other mousedown listeners
        // (focus, popover dismissal, frappe's own bookkeeping) must still run.
    }

    function onMouseMove(e) {
        if (!board) return;
        // Button released somewhere we didn't see (e.g. outside the window)?
        if ((e.buttons & 1) === 0) {
            endDrag();
            return;
        }
        // No layout read: offsetLeft is constant during the drag and cancels out.
        board.scrollLeft = startScrollLeft - (e.pageX - startPageX);
        board.classList.add("clickdrag-active"); // re-assert if a stray mouseleave cleared it
        e.preventDefault();
        // Skip frappe's bubble-phase mousemove (the offsetLeft reader) for this drag.
        e.stopPropagation();
    }

    // Capture phase so we run BEFORE the event descends to frappe's listener on
    // the .kanban element; stopPropagation() then keeps it from getting there.
    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("mousemove", onMouseMove, true);
    document.addEventListener("mouseup", endDrag, true);
})();
