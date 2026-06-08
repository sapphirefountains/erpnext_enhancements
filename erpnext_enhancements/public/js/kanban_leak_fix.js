/*
 * Kanban filter memory-leak hotfix (upstream: frappe/frappe#24156)
 *
 * THE BUG (in frappe core, kanban_board.bundle.js):
 *   The Kanban board's Vuex `store` is a module-level singleton that lives for
 *   the whole page session. On every refresh (and a filter change IS a refresh)
 *   frappe's KanbanView.render() runs:
 *
 *       this.$result.empty();
 *       this.kanban.update(this.data);
 *
 *   Because $result was just emptied, the board's update() no longer finds its
 *   `.kanban` element and falls through to init(), which re-registers ~3
 *   board-level + N-per-column `store.watch()` subscriptions on the singleton
 *   store WITHOUT ever unwatching the previous generation. Every leaked closure
 *   also retains the now-detached board DOM and its SortableJS instances, and
 *   every later state mutation re-fires all the stale watchers (the CPU spike).
 *   So each open/close of the filter window or filter change grows the heap by
 *   ~3 + (number of columns) watchers until the tab OOMs.
 *
 * THE FIX (client-side, no core changes):
 *   On a same-board refresh we drive the board's already-built REACTIVE update
 *   path (store action "update_cards") instead of emptying $result and forcing a
 *   re-init(). update_cards re-prepares the cards and lets the existing per-column
 *   watchers refresh the card DOM in place -- no new watcher is ever registered,
 *   so nothing accumulates on the singleton store. The very first render (board
 *   creation) and board switches still defer to frappe's original render(), so
 *   the board's watchers are registered exactly once, as intended.
 *
 *   This is behaviour-preserving for filtering: both paths rebuild the card DOM
 *   via KanbanBoardColumn.make_cards(); columns (which depend on the board, not
 *   the filters) simply stay mounted instead of being destroyed and rebuilt.
 *   It composes with our other Kanban patches (kanban_patches hold-to-drag /
 *   render_card / get_data / opportunity_kanban_totals refresh): the hold-to-drag
 *   patch reaches every SortableJS instance through a document MutationObserver, and
 *   the others hook render_card, get_data and refresh -- none of which depend on the
 *   board being torn down on each filter change.
 *
 * REMOVE THIS FILE once the upstream fix (frappe/frappe#24156) ships in our
 * deployed frappe version. Tracking branches: develop + version-16.
 */

frappe.provide("frappe.views");

(function patch_kanban_filter_leak() {
    const KanbanView = frappe.views && frappe.views.KanbanView;

    // Defensive: if the view class isn't loaded yet for some reason, do nothing
    // rather than throw (matches how our other app_include_js patches behave).
    if (!KanbanView || !KanbanView.prototype || KanbanView.prototype._leak_fix_24156) {
        return;
    }

    const original_render = KanbanView.prototype.render;

    KanbanView.prototype.render = function () {
        const board_name = this.board_name;

        // Same-board refresh (filter change / filter window toggle): refresh the
        // cards reactively instead of $result.empty() + board re-init(), which is
        // what leaks watchers on the singleton Vuex store (frappe/frappe#24156).
        if (this.kanban && board_name === this.kanban.board_name) {
            // opts.cards is updated inside update(); because we did NOT empty
            // $result, the board still has its `.kanban` element and takes the
            // store "update_cards" path -- no init(), no new watchers.
            this.kanban.update(this.data);
            return;
        }

        // First render or board switch: keep frappe's original behaviour, which
        // creates the board and registers its watchers exactly once.
        return original_render.apply(this, arguments);
    };

    KanbanView.prototype._leak_fix_24156 = true;
})();
