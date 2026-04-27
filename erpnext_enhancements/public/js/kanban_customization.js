/*
 * Opportunity Kanban Customization (Frappe v16+)
 * - Conditional Background for high value opportunities (> 4000)
 * - Value Stream Indicator Dots in card footer from 'custom_value_stream' child table
 */

(function() {
    frappe.provide("erpnext_enhancements.kanban");

    const original_render_card = frappe.views.KanbanView.prototype.render_card;

    frappe.views.KanbanView.prototype.render_card = function(data) {
        const $card = original_render_card.apply(this, arguments);

        // Target only the 'Opportunity' DocType and specifically the 'Opportunity' board
        const is_opportunity_board = (this.doctype === 'Opportunity' && (this.board_name === 'Opportunity' || (this.board && this.board.name === 'Opportunity')));

        if (is_opportunity_board) {
            
            // 1. Conditional Background Styling
            // If opportunity_amount > 4000, set dark background and white text
            if (data.opportunity_amount > 4000) {
                // Apply to kanban-card-body as per user feedback
                const $body = $card.find('.kanban-card-body');
                if ($body.length > 0) {
                    $body.addClass('opportunity-high-value');
                } else {
                    $card.addClass('opportunity-high-value');
                }
                // Also add to parent just in case, for CSS targeting
                $card.addClass('opportunity-high-value-parent');
            }

            // 2. Value Stream Indicator Dots
            // The field 'custom_value_stream' is a child table
            const value_streams = data.custom_value_stream || [];
            if (value_streams.length > 0) {
                render_value_stream_dots($card, value_streams);
            }
        }
        return $card;
    };

    /**
     * Renders indicator dots in the footer of the Kanban card
     * @param {jQuery} $card The card element
     * @param {Array} value_streams Array of child table rows
     */
    function render_value_stream_dots($card, value_streams) {
        // Use .kanban-card-meta as per user feedback
        let $meta = $card.find('.kanban-card-meta');
        
        if ($meta.length === 0) {
            // Fallback to finding any footer or body if meta is missing
            $meta = $card.find('.kanban-card-body');
        }

        if ($meta.length === 0) {
            // Final fallback: append to card
            $meta = $card;
        }

        // Create or clear dots container
        let $dots_container = $meta.find('.kanban-card-value-stream');
        if ($dots_container.length === 0) {
            $dots_container = $('<div class="kanban-card-value-stream"></div>').appendTo($meta);
        } else {
            $dots_container.empty();
        }

        const color_map = {
            "Build": "#00609C",
            "Design": "#B34FC5",
            "Service": "#00A0DF",
            "Rent": "#65CBC9"
        };

        // Track seen colors to avoid duplicate dots for the same stream
        const seen = new Set();

        value_streams.forEach(row => {
            const val = row.value_stream;
            const color = color_map[val];
            if (color && !seen.has(color)) {
                $('<div class="value-stream-dot"></div>')
                    .css('background-color', color)
                    .attr('title', val)
                    .appendTo($dots_container);
                seen.add(color);
            }
        });
    }

    /**
     * Override get_data to ensure 'custom_value_stream' child table data is fetched.
     * Frappe's standard Kanban fetch (get_list) does not include child tables.
     */
    const original_get_data = frappe.views.KanbanView.prototype.get_data;
    frappe.views.KanbanView.prototype.get_data = function() {
        const self = this;
        // Use Promise.resolve to handle both promise-returning and data-returning get_data
        return Promise.resolve(original_get_data.apply(this, arguments)).then(data => {
            const is_opportunity_board = (self.doctype === 'Opportunity' && (self.board_name === 'Opportunity' || (self.board && self.board.name === 'Opportunity')));

            if (is_opportunity_board && data && data.length > 0) {
                const names = data.map(d => d.name);
                
                // Determine the child table doctype name (Standard pattern or from meta)
                const meta = frappe.get_meta('Opportunity');
                const field = meta && meta.fields ? meta.fields.find(f => f.fieldname === 'custom_value_stream') : null;
                
                // Fallback child DT name if not found in meta
                const child_dt = field ? field.options : 'Opportunity Value Stream';

                return frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: child_dt,
                        filters: { parent: ['in', names] },
                        fields: ['parent', 'value_stream'],
                        limit_page_length: 1000
                    }
                }).then(r => {
                    const vs_map = {};
                    (r.message || []).forEach(row => {
                        if (!vs_map[row.parent]) vs_map[row.parent] = [];
                        vs_map[row.parent].push(row);
                    });
                    
                    // Attach child table data to each document in the main data array
                    data.forEach(d => {
                        d.custom_value_stream = vs_map[d.name] || [];
                    });
                    return data;
                });
            }
            return data;
        });
    };
})();
