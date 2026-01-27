alert("Kanban Script Loaded");

/*
 * Kanban Enhancements for ERPNext
 * - Touch Latency Fix (Drag Delay)
 * - Swimlane Rendering
 * - WIP Limits
 */

console.log("[Kanban Debug] Loading kanban_enhancements.js...");

frappe.provide("erpnext_enhancements.kanban");
frappe.provide("erpnext_enhancements.utils");

// ============================================================
// 1. Utility Implementation: waitForObject
// ============================================================

/**
 * Waits for an object to exist in the global scope (window).
 * @param {string} namespace_string - The dot-separated namespace (e.g., "frappe.views.KanbanBoard").
 * @param {number} interval - Polling interval in ms (default: 100).
 * @param {number} max_timeout - Maximum time to wait in ms (default: 5000).
 * @returns {Promise<any>} - Resolves with the object or rejects on timeout.
 */
erpnext_enhancements.utils.waitForObject = function(namespace_string, interval = 100, max_timeout = 5000) {
    console.log(`[Kanban Debug] waitForObject: Starting wait for ${namespace_string}`);
    return new Promise((resolve, reject) => {
        const check = () => {
            const parts = namespace_string.split('.');
            let obj = window;
            for (let part of parts) {
                if (obj && obj[part]) {
                    obj = obj[part];
                } else {
                    return null; // Not found yet
                }
            }
            return obj;
        };

        // Check immediately
        const existing = check();
        if (existing) {
            console.log(`[Kanban Debug] waitForObject: Found ${namespace_string} immediately.`);
            resolve(existing);
            return;
        }

        // Start polling
        const timer = setInterval(() => {
            const found = check();
            if (found) {
                console.log(`[Kanban Debug] waitForObject: Found ${namespace_string} after polling.`);
                clearInterval(timer);
                resolve(found);
            }
        }, interval);

        // Set timeout
        setTimeout(() => {
            clearInterval(timer);
            console.warn(`[Kanban Debug] waitForObject: Timeout waiting for ${namespace_string}`);
            reject(new Error(`Timeout waiting for object: ${namespace_string}`));
        }, max_timeout);
    });
};

// ============================================================
// 2. Unified Entry Point
// ============================================================

async function initialize_kanban_logic() {
    console.log("[Kanban Debug] initialize_kanban_logic called.");

    // Step A: Check Route (Must be 'kanban')
    const route = frappe.get_route();
    console.log("[Kanban Debug] Current route:", route);

    if (!route || route[0] !== 'kanban') {
        console.log("[Kanban Debug] Not a kanban route. Skipping.");
        return;
    }

    // Step B: Idempotency Check (Optimization)
    if (erpnext_enhancements.kanban.patched) {
        console.log("[Kanban Debug] Already patched. Skipping.");
        return;
    }

    // Step C: Await Dependency
    try {
        console.log("[Kanban Debug] Waiting for frappe.views.KanbanBoard...");
        await erpnext_enhancements.utils.waitForObject("frappe.views.KanbanBoard");

        // Check again to prevent race conditions during await
        if (erpnext_enhancements.kanban.patched) {
             console.log("[Kanban Debug] Patched during wait. Skipping.");
             return;
        }

        apply_kanban_patches();
    } catch (error) {
        console.warn("[Enhancements] KanbanBoard failed to load within timeout.", error);
    }
}

// ============================================================
// 3. Event Listeners
// ============================================================

$(document).on('app_ready', function() {
    console.log("[Kanban Debug] app_ready triggered.");
    initialize_kanban_logic();
});

if (frappe.router) {
    frappe.router.on('change', () => {
        console.log("[Kanban Debug] router change triggered.");
        initialize_kanban_logic();
    });
}

// ============================================================
// 4. Patch Application
// ============================================================

function apply_kanban_patches() {
    try {
        console.log("[Enhancements] Applying Kanban Patches...");
        erpnext_enhancements.kanban.patched = true;

        const KanbanBoard = frappe.views.KanbanBoard;
        console.log("[Kanban Debug] KanbanBoard prototype found:", !!KanbanBoard.prototype);

        // ----------------------------------------------------------------
        // 1. Drag & Drop Latency (Monkey Patch make_sortable)
        // ----------------------------------------------------------------
        const original_make_sortable = KanbanBoard.prototype.make_sortable;
        console.log("[Kanban Debug] original_make_sortable exists:", !!original_make_sortable);

        // We expect make_sortable to exist. If not, we warn.
        if (original_make_sortable) {
            KanbanBoard.prototype.make_sortable = function($el, args, options) {
                console.log("[Kanban Debug] Custom make_sortable called.");
                if (!options) options = {};

                // Inject 1000ms delay for touch/drag
                options.delay = 1000;
                options.touchStartThreshold = 5; // Start drag only after 5px movement (prevents accidental clicks)

                console.log("[Enhancements] Initializing Sortable with delay: 1000ms");

                return original_make_sortable.call(this, $el, args, options);
            };
        } else {
            console.warn("[Enhancements] KanbanBoard.prototype.make_sortable not found. Drag delay patch skipped.");
        }

        // ----------------------------------------------------------------
        // 2. Rendering Logic (Swimlanes & WIP Limits)
        // ----------------------------------------------------------------
        const original_refresh = KanbanBoard.prototype.refresh;
        console.log("[Kanban Debug] original_refresh exists:", !!original_refresh);

        KanbanBoard.prototype.refresh = function() {
            console.log("[Kanban Debug] Custom refresh called. Board:", this.board && this.board.name);
            // Retrieve custom configuration
            const swimlane_field = this.board.custom_swimlane_field;

            if (swimlane_field) {
                // SWIMLANE MODE
                console.log("[Enhancements] Swimlane Mode Active. Field:", swimlane_field);
                this.render_swimlanes(swimlane_field);
            } else {
                // STANDARD MODE
                const res = original_refresh.call(this);
                // Apply WIP limits as a post-process
                this.check_wip_limits();
                return res;
            }
        };

        // Helper: Check WIP Limits (Standard & Swimlane)
        KanbanBoard.prototype.check_wip_limits = function() {
            if (!this.columns) return;

            // Map column definitions for easy lookup
            const col_map = {};
            (this.board.columns || []).forEach(c => {
                 col_map[c.status] = c;
            });

            this.columns.forEach(col => {
                const limit = col_map[col.status] ? col_map[col.status].custom_wip_limit : 0;
                if (limit > 0) {
                    const count = col.cards ? col.cards.length : 0;
                    const $wrapper = col.$wrapper;

                    if (count > limit) {
                        $wrapper.addClass('wip-violation');
                    } else {
                        $wrapper.removeClass('wip-violation');
                    }
                }
            });
        };

        // Helper: Render Swimlanes
        KanbanBoard.prototype.render_swimlanes = function(group_by) {
            console.log("[Kanban Debug] render_swimlanes called with group_by:", group_by);
            const me = this;
            this.$wrapper.empty().addClass('kanban-swimlane-mode');

            // Reset columns array to track new instances
            this.columns = [];

            // 1. Group Cards
            const groups = {};
            const cards = this.cards || [];

            cards.forEach(card => {
                let val = card[group_by];
                if (!val) val = __("Unassigned");

                if (!groups[val]) groups[val] = [];
                groups[val].push(card);
            });

            // 2. Render Each Group as a Swimlane
            for (const [group_name, group_cards] of Object.entries(groups)) {
                const $row = $(`<div class="kanban-swimlane">
                    <div class="kanban-swimlane-header">
                        <span class="indicator blue">${group_name}</span>
                        <span class="badge">${group_cards.length}</span>
                    </div>
                    <div class="kanban-swimlane-body"></div>
                </div>`).appendTo(this.$wrapper);

                const $body = $row.find('.kanban-swimlane-body');

                // Render Columns inside this Swimlane
                this.board.columns.forEach(col_def => {
                    const $col_wrapper = $(`<div class="kanban-column"></div>`).appendTo($body);

                    // Filter cards for this column (Status) AND this swimlane
                    const col_cards = group_cards.filter(c => c.status === col_def.status);

                    // Instantiate KanbanColumn (assuming global availability)
                    if (frappe.views.KanbanColumn) {
                        const col_inst = new frappe.views.KanbanColumn(col_def, col_cards, $col_wrapper, me);

                        // Register the column instance
                        me.columns.push(col_inst);

                        // Apply WIP Limit Class immediately
                        if (col_def.custom_wip_limit > 0 && col_cards.length > col_def.custom_wip_limit) {
                            $col_wrapper.addClass('wip-violation');
                        }

                        // Column Folding Logic
                        const storage_key = `kanban_folding:${me.board.name}:${frappe.session.user}:${col_def.status}`;
                        const is_collapsed = localStorage.getItem(storage_key) === '1';

                        if (is_collapsed) {
                            $col_wrapper.addClass('collapsed');
                        }

                        // Inject Folding Button
                        const $header = $col_wrapper.find('.kanban-column-header');
                        // Check if already exists to avoid dupes on re-render
                        if ($header.find('.kanban-fold-btn').length === 0) {
                            const $fold_btn = $(`<span class="kanban-fold-btn" style="cursor:pointer; margin-left:auto; font-size: 12px; opacity: 0.7;">
                                ${is_collapsed ? '►' : '▼'}
                            </span>`);

                            $fold_btn.on('click', function(e) {
                                e.stopPropagation();
                                e.stopImmediatePropagation(); // Prevent sorting trigger

                                const will_collapse = !$col_wrapper.hasClass('collapsed');

                                if (will_collapse) {
                                    $col_wrapper.addClass('collapsed');
                                    localStorage.setItem(storage_key, '1');
                                    $(this).text('►');
                                } else {
                                    $col_wrapper.removeClass('collapsed');
                                    localStorage.removeItem(storage_key);
                                    $(this).text('▼');
                                }
                            });

                            $header.append($fold_btn);
                        }
                    } else {
                        console.error("[Enhancements] frappe.views.KanbanColumn is not defined. Cannot render swimlane columns.");
                    }
                });
            }
        };

        // Inject Dynamic Styles for Collapsing
        $('head').append(`<style>
            .kanban-column.collapsed {
                min-width: 50px !important;
                width: 50px !important;
                overflow: hidden;
                transition: all 0.2s ease;
            }
            .kanban-column.collapsed .kanban-cards,
            .kanban-column.collapsed .kanban-column-footer {
                display: none !important;
            }
            .kanban-column.collapsed .kanban-column-header {
                flex-direction: column;
                align-items: center;
                justify-content: flex-start;
                height: 100%;
                padding-top: 10px;
            }
            .kanban-column.collapsed .kanban-column-title {
                writing-mode: vertical-rl;
                text-orientation: mixed;
                margin-top: 10px;
            }
            .kanban-column.collapsed .kanban-fold-btn {
                margin: 0 !important;
                margin-bottom: 5px;
            }
        </style>`);

    } catch (e) {
        console.error("[Enhancements] Error applying Kanban patches:", e);
    }
}

// ============================================================
// 5. Diagnostic Tool
// ============================================================
erpnext_enhancements.kanban.diagnose = function() {
    console.group("[Kanban Diagnostics]");
    console.log("Script Loaded:", true);
    console.log("Patch Applied Flag (erpnext_enhancements.kanban.patched):", erpnext_enhancements.kanban.patched);
    console.log("Current Route:", frappe.get_route());
    console.log("frappe.views.KanbanBoard exists:", !!(frappe.views && frappe.views.KanbanBoard));

    if (frappe.views && frappe.views.KanbanBoard) {
        const kb = frappe.views.KanbanBoard.prototype;
        const make_sortable_str = kb.make_sortable.toString();
        const refresh_str = kb.refresh.toString();

        console.log("make_sortable patched (contains '1000ms'):", make_sortable_str.includes("1000"));
        console.log("refresh patched (contains 'swimlane'):", refresh_str.includes("swimlane"));
    } else {
        console.warn("KanbanBoard class not found.");
    }
    console.groupEnd();
    return "Diagnostic Complete";
};
