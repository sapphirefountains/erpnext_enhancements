/*
 * Kanban "press-and-hold to grab" — drag delay for mouse AND touch.
 *
 * Targets: every Kanban board (board-agnostic). Loaded via kanban.bundle.js
 *   (hooks.py `app_include_js`). See CHANGELOG for the full history of this fix —
 *   including the stale-cache root cause (raw /assets paths + 1-year immutable
 *   Cache-Control) that kept phones running pre-fix copies of this file until
 *   it became a content-hashed bundle (v0.8.1).
 *
 * THE PROBLEM:
 *   Frappe's Kanban starts a drag the instant a pointer lands on a card. On a touch
 *   screen that means brushing a card while trying to scroll the board sideways or a
 *   column up/down immediately picks the card up and drops it in the wrong place; on
 *   desktop an accidental click-drag does the same. We want a card to move only after
 *   a deliberate 1-second press-and-hold, for BOTH mouse and finger. A quick swipe
 *   must still scroll, and a quick tap must still open the card.
 *
 * WHY SORTABLEJS ALREADY SUPPORTS THIS:
 *   Frappe v16 builds each Kanban card list with SortableJS 1.15. SortableJS reads
 *   three options live at pointer-down time:
 *     - delay              : ms the pointer must be held before a drag may begin.
 *     - delayOnTouchOnly   : false -> the hold applies to mouse AND touch. (true,
 *                            the old setting, skips the hold for the mouse so it
 *                            dragged instantly — the opposite of what we want.)
 *     - touchStartThreshold: px the pointer may drift during the hold before the
 *                            pending drag is CANCELLED, so a swipe scrolls instead of
 *                            grabbing a card. Bound for mouse, touch AND pointer.
 *   During the hold SortableJS does NOT preventDefault the move, so the browser
 *   scrolls natively; the element is only made draggable after the delay fires. The
 *   1s hold works with native HTML5 drag too — no forceFallback needed. Because the
 *   options are read live, calling instance.option(...) on an already-built Sortable
 *   takes effect on the next pointer-down, so we can patch boards after they mount.
 *
 * WHY THE OLD APPROACH DIDN'T RELIABLY WORK:
 *   The previous version hooked KanbanView.render() and scanned for Sortables on a
 *   fixed [0,150,400,1000]ms timeline. On a heavy board (the Opportunity board has
 *   thousands of nodes) Vue/SortableJS finish mounting AFTER that 1s window closes,
 *   so the scan found nothing and the delay was never applied — cards still grabbed
 *   instantly. Worse, our sibling kanban_leak_fix.js short-circuits render() on
 *   same-board filter refreshes, so the scans were never re-scheduled either. The
 *   net effect on the live board was "no delay at all".
 *
 * THE FIX (decoupled from render):
 *   A single document-level MutationObserver watches for Kanban *container* nodes
 *   being inserted (board / columns / card-lists — NOT individual cards, so card
 *   shuffles during a drag don't trigger work). Whenever a container appears we
 *   (debounced) walk the Kanban DOM, recover each SortableJS instance from its host
 *   element and set the three options. This catches the board no matter how slowly it
 *   mounts and regardless of how filters refresh it. A short bounded startup poll is a
 *   belt-and-suspenders net for the first paint. Each patched instance is flagged, so
 *   re-scans are idempotent and cheap. This applies to every Kanban board (Task,
 *   Opportunity, …) since the bug and the fix are both board-agnostic.
 *
 * Tuning: HOLD_DELAY_MS = hold time. MOVE_THRESHOLD_PX = drift tolerated during the
 * hold before it becomes a scroll (SortableJS divides this by devicePixelRatio for
 * native drag, so the effective value is smaller on a HiDPI phone — easier to cancel
 * into a scroll). Flip DELAY_ON_TOUCH_ONLY to true to make only touch hold and leave
 * the mouse instant.
 */

(function patch_kanban_hold_to_drag() {
    "use strict";

    const HOLD_DELAY_MS = 1000;        // press-and-hold this long before a card can drag
    const DELAY_ON_TOUCH_ONLY = false; // false -> the hold applies to mouse AND touch
    const MOVE_THRESHOLD_PX = 8;       // drift during the hold that cancels into a scroll
    // ^ SortableJS divides this by devicePixelRatio for native drag, so on a DPR2-3
    //   phone the *effective* tolerance is ~3-4px. That sits comfortably above idle
    //   finger tremor (~1-2px) — so a deliberate still hold reliably arms a drag —
    //   yet far below a real scroll swipe (tens of px), which still cancels into a
    //   native scroll. (5px would drop to ~1.7-2.5px effective and feel twitchy.)

    // Elements that HOST a SortableJS instance are Kanban containers (the board, a
    // column, a card list) — never an individual card. We trigger a (re)scan only when
    // one of these is inserted, so dragging cards around (which just adds/removes
    // .kanban-card nodes) never schedules work. Note ".kanban-card" is intentionally
    // absent and does not match ".kanban-cards".
    const CONTAINER_SELECTOR =
        ".kanban, .kanban-columns, .kanban-column, .kanban-cards, .kanban-column-cards";

    let patched_any = false; // set once we've patched at least one Sortable this session

    // Recover the SortableJS instance attached to a DOM element. SortableJS sets
    // el[expando] = instance (expando is "Sortable<timestamp>", generated once at
    // module load) and the instance carries an `el` back-ref to the same element plus
    // an `option()` method — enough to identify it without a reference to the
    // module-bundled (non-global) Sortable class. getOwnPropertyNames (not Object.keys)
    // so a non-enumerable expando is still found; it returns only the element's own
    // props, so the scan stays tiny.
    function get_sortable(el) {
        const names = Object.getOwnPropertyNames(el);
        for (let i = 0; i < names.length; i++) {
            const value = el[names[i]];
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

    // Apply the hold-to-drag options to every card/column Sortable under `root`. Broad
    // selector for correctness (whatever element frappe attaches the Sortable to is
    // covered); the expando check discards every non-Sortable match. Cheap because it
    // only runs when a container was just inserted (see the observer) or on the bounded
    // startup poll — never on every card mutation.
    function apply_hold_delay(root) {
        const scope = root && root.querySelectorAll ? root : document;
        // Cheap gate: if no Kanban board is present, do nothing. This keeps the
        // startup poll (and any stray scan) effectively free on non-Kanban pages — a
        // single indexed class lookup — instead of walking the broad substring match
        // below. The board root carries the ".kanban" class; the Sortable hosts
        // (columns / card lists) are all descendants of it.
        if (!scope.querySelector(".kanban")) return;
        scope.querySelectorAll('[class*="kanban"]').forEach((el) => {
            const sortable = get_sortable(el);
            if (!sortable || sortable.__hold_to_drag) return;
            sortable.option("delay", HOLD_DELAY_MS);
            sortable.option("delayOnTouchOnly", DELAY_ON_TOUCH_ONLY);
            sortable.option("touchStartThreshold", MOVE_THRESHOLD_PX);
            sortable.__hold_to_drag = true;
            patched_any = true;
        });
    }

    // Backport of a SortableJS fix that landed in 1.15.4 (frappe pins 1.15.0):
    // 1.15.0's delay branch cancels a pending hold on touchend/touchcancel/mouseup
    // and on >threshold movement, but never listens for `pointercancel`. Real
    // phones are unaffected (touch events fire alongside pointer events, so the
    // bound touchmove/touchcancel still cancel the hold), but on pointer-only
    // inputs (pen/stylus, some Windows touch configs) the browser fires ONLY
    // `pointercancel` when it claims the gesture for native scrolling — the
    // pending 1s timer survives the scroll takeover and fires mid-scroll,
    // grabbing a card nobody is pressing. On pointercancel, abort the pending
    // delayed drag on every patched Sortable. _disableDelayedDrag is a no-op on
    // instances with nothing pending; the chosen-class guard skips the case
    // where the hold already completed and a real drag is in flight (Chrome
    // also fires pointercancel when a native HTML5 drag starts — cancelling
    // then would poke the live drag, not a pending one).
    function cancel_pending_delayed_drags() {
        if (!document.querySelector(".kanban")) return;
        if (document.querySelector(".sortable-chosen")) return; // drag already started
        document.querySelectorAll('[class*="kanban"]').forEach((el) => {
            const sortable = get_sortable(el);
            if (
                sortable &&
                sortable.__hold_to_drag &&
                typeof sortable._disableDelayedDrag === "function"
            ) {
                sortable._disableDelayedDrag();
            }
        });
    }

    // Coalesce a burst of container insertions during a board build into a single
    // pass, then one delayed re-pass to catch a Sortable whose instance is attached a
    // tick after its element lands in the DOM (Vue mounted() vs the observer microtask).
    let scan_timer = null;
    function schedule_scan() {
        if (scan_timer) clearTimeout(scan_timer);
        scan_timer = setTimeout(() => {
            scan_timer = null;
            apply_hold_delay(document);
            setTimeout(() => apply_hold_delay(document), 250);
        }, 60);
    }

    function is_container(node) {
        if (node.nodeType !== 1) return false; // elements only
        if (node.matches && node.matches(CONTAINER_SELECTOR)) return true;
        return !!(node.querySelector && node.querySelector(CONTAINER_SELECTOR));
    }

    function start() {
        // Patch anything already present (e.g. a board built before this script ran).
        apply_hold_delay(document);

        // Capture phase so the pending hold is aborted even if something stops
        // the event from bubbling. pointercancel is rare (scroll takeover /
        // native dragstart), so the handler's DOM walk costs nothing in steady
        // state.
        document.addEventListener("pointercancel", cancel_pending_delayed_drags, true);

        // Permanent net: scan whenever a Kanban container is inserted, regardless of
        // how the board was rendered or refreshed. Lean callback — bails immediately on
        // the (overwhelmingly common) non-Kanban mutations.
        const observer = new MutationObserver((mutations) => {
            for (let i = 0; i < mutations.length; i++) {
                const added = mutations[i].addedNodes;
                for (let j = 0; j < added.length; j++) {
                    if (is_container(added[j])) {
                        schedule_scan();
                        return;
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });

        // Belt-and-suspenders for the first paint: a short bounded poll in case the
        // very first board build slips past the observer for any reason. Stops as soon
        // as it has patched something (the observer covers every later board), or after
        // ~15s if no board appeared during startup — so there is no standing cost.
        let ticks = 0;
        const poll = setInterval(() => {
            apply_hold_delay(document);
            ticks += 1;
            if (patched_any || ticks >= 20) clearInterval(poll);
        }, 750);
    }

    if (document.body) {
        start();
    } else {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    }
})();
