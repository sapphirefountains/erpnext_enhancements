/*
 * Kanban Enhancements for ERPNext (Updated for Frappe v16+)
 * - Touch Latency Fix (Drag Delay) via Global Sortable Patch
 * - Swimlane Rendering via Instance Override
 * - WIP Limits
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


// ============================================================
// 2. Kanban Logic Injection
// ============================================================

async function initialize_kanban_logic() {
    // 0. Only run on Kanban view
    const route = frappe.get_route();
    // Typical route: List/DocType/Kanban/BoardName or similar
    // We check if "Kanban" appears in route
    if (!route || !route.includes('Kanban')) {
        return;
    }

    // 1. Wait for Kanban instance to be ready
    erpnext_enhancements.utils.waitFor(
        () => {
            return window.cur_list &&
                   window.cur_list.kanban &&
                   window.cur_list.kanban.board_name;
        },
        async () => {
             await inject_logic();
        },
        20, // 20 attempts
        500 // 500ms interval = 10 seconds max wait
    );
}

async function inject_logic() {
    // 1. Validate Context
    if (!cur_list || !cur_list.kanban) {
        return; 
    }
    
    // 2. Idempotency Check on the specific instance
    if (cur_list.kanban._enhanced) {
        return;
    }

    console.log("[Kanban Enhancements] Initializing logic for board:", cur_list.kanban.board_name);
    cur_list.kanban._enhanced = true;

    // 3. Get Board Config
    const board_name = cur_list.kanban.board_name;
    let board_doc = locals['Kanban Board'] && locals['Kanban Board'][board_name];

    if (!board_doc) {
        try {
            board_doc = await frappe.db.get_doc('Kanban Board', board_name);
        } catch (e) {
            console.error("[Kanban Enhancements] Failed to load board config", e);
            return;
        }
    }

    // 4. Override the 'update' method
    // The 'update' method is called whenever the board needs to re-render (e.g. initial load, filters)
    // Signature based on investigation: update(cards)
    const original_update = cur_list.kanban.update;

    cur_list.kanban.update = function(cards) {
        // console.log("[Kanban Enhancements] Intercepted update with cards:", cards ? cards.length : 0);
        
        // Save cards to instance for reference
        this.cards = cards;

        if (board_doc.custom_swimlane_field) {
            // --- Swimlane Mode ---
            render_swimlanes(this, cards, board_doc);
        } else {
            // --- Standard Mode ---
            original_update.call(this, cards);
            
            // Apply WIP Limits after standard render
            // We use setTimeout to ensure DOM is ready (or use requestAnimationFrame)
             setTimeout(() => {
                check_wip_limits(this, board_doc);
            }, 200);
        }
    };

    // Trigger an immediate update to apply changes if data is already loaded
    if (cur_list.kanban.cards) {
        cur_list.kanban.update(cur_list.kanban.cards);
    }
}

// ============================================================
// 3. Renderers
// ============================================================

function check_wip_limits(kanban_inst, board_doc) {
    if (!kanban_inst.wrapper) return;

    // Create a map of status -> limit
    const limit_map = {};
    (board_doc.columns || []).forEach(c => {
        limit_map[c.status] = c.custom_wip_limit || 0;
    });

    // Find columns in DOM
    const $columns = kanban_inst.wrapper.find('.kanban-column');
    
    $columns.each(function() {
        const $col = $(this);
        // Try to identify status. Usually stored in data attribute or header.
        // Based on standard Frappe markup:
        const status = $col.find('.kanban-column-title').text().trim(); 
        const limit = limit_map[status];

        if (limit > 0) {
            const card_count = $col.find('.kanban-card').length;
            if (card_count > limit) {
                $col.addClass('wip-violation');
                $col.css('border-top', '3px solid red');
            } else {
                $col.removeClass('wip-violation');
                $col.css('border-top', '');
            }
        }
    });
}

function render_swimlanes(kanban_inst, cards, board_doc) {
    const $wrapper = kanban_inst.wrapper.find('.kanban');
    const group_by = board_doc.custom_swimlane_field;
    
    $wrapper.empty().addClass('kanban-swimlane-mode');
    
    // Group Cards
    const groups = {};
    (cards || []).forEach(card => {
        let val = card[group_by];
        // Handle Link fields that might return [name, title]
        if (Array.isArray(val)) val = val[0]; 
        if (!val) val = __("Unassigned");
        
        if (!groups[val]) groups[val] = [];
        groups[val].push(card);
    });

    // Render Each Group
    for (const [group_name, group_cards] of Object.entries(groups)) {
        // Create Swimlane Row
        const $row = $(`
            <div class="kanban-swimlane" style="margin-bottom: 20px; border: 1px solid #d1d8dd; border-radius: 4px;">
                <div class="kanban-swimlane-header" style="background-color: #f8f9fa; padding: 10px; font-weight: bold; border-bottom: 1px solid #d1d8dd;">
                    <span>${group_name}</span>
                    <span class="badge badge-secondary" style="margin-left: 10px;">${group_cards.length}</span>
                </div>
                <div class="kanban-swimlane-body" style="display: flex; overflow-x: auto; padding: 10px; gap: 10px;"></div>
            </div>
        `).appendTo($wrapper);

        const $body = $row.find('.kanban-swimlane-body');

        // Render Columns inside Swimlane
        (board_doc.columns || []).forEach(col_def => {
            const col_status = col_def.status;
            
            // Filter cards for this column + swimlane
            const col_cards = group_cards.filter(c => c.status === col_status);

            // Create Column Wrapper
            const $col = $(`
                <div class="kanban-column" style="min-width: 250px; width: 250px; background: #fff; border: 1px solid #ebf0f5; border-radius: 3px; display: flex; flex-direction: column;">
                    <div class="kanban-column-header" style="padding: 10px; border-bottom: 1px solid #ebf0f5; font-size: 12px; color: #8d99a6;">
                        <span class="kanban-column-title">${col_status}</span>
                        <span class="flt-right">${col_cards.length}</span>
                    </div>
                    <div class="kanban-cards" style="padding: 10px; flex-grow: 1; min-height: 50px;"></div>
                </div>
            `).appendTo($body);

            // WIP Check
            if (col_def.custom_wip_limit > 0 && col_cards.length > col_def.custom_wip_limit) {
                 $col.css('border-top', '3px solid red');
            }

            const $cards_container = $col.find('.kanban-cards');

            // Render Cards
            // We reuse the standard card rendering logic if possible, 
            // but since we don't have access to the internal renderer easily, 
            // we will construct a basic card or try to invoke a frappe utility.
            
            col_cards.forEach(card => {
                const color = card.color || 'blue'; // fallback
                const $card = $(`
                    <div class="kanban-card" data-name="${card.name}" style="background: #fff; border: 1px solid #e2e6ea; border-radius: 3px; padding: 10px; margin-bottom: 10px; cursor: move; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                         <div style="font-weight: 500; margin-bottom: 5px;">
                            ${frappe.utils.get_avatar ? frappe.utils.get_avatar(card._assign ? JSON.parse(card._assign)[0] : null) : ''}
                            <a href="/app/${frappe.router.slug(card.doctype)}/${card.name}">${card[kanban_inst.card_meta.title_field || 'name']}</a>
                         </div>
                         <div style="font-size: 11px; color: #8d99a6;">
                            ${card.name}
                         </div>
                    </div>
                `).appendTo($cards_container);
                
                // Store data for Sortable/jQuery
                $card.data('data', card);
            });

            // Initialize Sortable for this column
            if (window.Sortable) {
                new window.Sortable($cards_container[0], {
                    group: 'kanban',
                    delay: 1000, // Enforced delay
                    animation: 150,
                    onEnd: function(evt) {
                        const item = evt.item;
                        const card_name = $(item).attr('data-name');
                        const new_status = col_status;
                        
                        // Call standard API to update status
                        frappe.call({
                            method: 'frappe.desk.doctype.kanban_board.kanban_board.update_doc_status',
                            args: {
                                board_name: board_name,
                                doc_name: card_name,
                                status: new_status
                            },
                            callback: function(r) {
                                if (!r.exc) {
                                    // Optional: Trigger full refresh to sync state
                                    // cur_list.kanban.update(cur_list.kanban.cards);
                                    frappe.show_alert({message: __('Saved'), indicator: 'green'});
                                }
                            }
                        });
                    }
                });
            }
        });
    }
}

// ============================================================
// 4. Event Listeners
// ============================================================

// Listen for route changes to re-initialize
if (frappe.router) {
    frappe.router.on('change', () => {
        // Use waitFor indirectly via initialize_kanban_logic
        initialize_kanban_logic();
    });
}

// Initial check
$(document).ready(() => {
    initialize_kanban_logic();
});
