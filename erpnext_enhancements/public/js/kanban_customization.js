/*
 * Opportunity Kanban Customization (frappe 16.53's classic Kanban engine)
 * - Conditional background for high-value opportunities (> 4000)
 * - Value stream indicator dots in the card footer from the 'custom_value_stream' child table
 * - The customer's name on a card is a link to the Customer (or Lead) form (v1.779.0)
 *
 * Targets: the "Opportunity" Kanban board (board name "Opportunity") only.
 * Loaded via: ee_kanban.bundle.js (hooks.py `app_include_js`, global desk script).
 * Styles: desk_enhancements.bundle.css, "OPPORTUNITY KANBAN CUSTOMIZATION".
 *
 * WHICH ENGINE. frappe 16.50 added a second Kanban engine. Each board picks its own
 * (`Kanban Board.use_kanban_v2`; list_factory.js loads kanban.bundle.js for a v2 board and
 * builds the classic `frappe.views.KanbanView` otherwise). The "Opportunity" board is a classic
 * one (use_kanban_v2 = 0 on prod, 2026-11-03), and this file patches the classic engine only:
 * a v2 board draws its own cards (kanban_v2/kanban_page.js, render_card) and gets none of this.
 *
 * WHERE THE CLASSIC ENGINE DRAWS A CARD (read at v16.53.0). `frappe.views.KanbanView`
 * (views/kanban/kanban_view.js) is a ListView: `set_fields()` picks the columns the list query
 * fetches (name, creation, the column field, the title, _assign, _user_tags, _liked_by, owner,
 * then the board's configured card fields, the image and colour fields), and `render()` ->
 * `render_kanban_board()` hands `this.data` to `frappe.views.KanbanBoard` once
 * kanban_board.bundle.js has loaded (`this.load_lib`, a frappe.require). That bundle's
 * `frappe.views.KanbanBoardCard(card, wrapper)` draws one card: the title, then one
 * `.kanban-doc-field` per board field (`get_doc_content`: an icon span titled with the field's
 * label, then the formatted value), then `.kanban-card-meta` (tags, comments, assignees, like).
 * `card.doc` is the data row.
 *
 * So three hooks:
 *   1. `KanbanView.prototype.set_fields` — after the original, add `party_name`,
 *      `opportunity_from` and `opportunity_amount` to the query (`_add_field` de-duplicates),
 *      so a card knows whom to link and whether it is high value even when the board's
 *      configured fields leave them out.
 *   2. `KanbanView.prototype.render_kanban_board` — before the original, fetch the
 *      `custom_value_stream` child rows for the page's names (a child table is never in a list
 *      query) and hang them on each data row as `custom_value_stream`; the board reads the rows
 *      it was given. A fetch that fails draws the board without dots.
 *   3. `frappe.views.KanbanBoardCard` — a function the bundle defines when it loads, so the
 *      wrapper is installed from hook 2 (after `load_lib` resolves) and once. After the original
 *      has drawn, decorate `self.$card`: the high-value class on `.kanban-card-body`, the dots
 *      in `.kanban-card-meta`, and the customer link.
 *
 * THE CUSTOMER LINK. Nik, 2026-11-03, from the route audit: the Opportunity Kanban to the
 * Customer list 47 times in 90 days and back 42, eleven of them within a minute — people looking
 * a deal's customer up by hand. The card's "Customer Name" field (board field `customer_name`, a
 * Data field) becomes a link to the party: `/desk/customer/<party_name>` when `opportunity_from`
 * is Customer, `/desk/lead/<party_name>` for a Lead. The field is found by the label the engine
 * writes on its icon (or, with "Show Labels" on, in its first span), read from the docfield, not
 * by its position. The click is handled here — `frappe.set_route("Form", ...)`, a history entry of
 * its own, so Back returns to the board — and stopped from bubbling: the card wrapper's own click
 * would open the Opportunity on top, and a bare href would never reach the Desk's body-level link
 * delegate once propagation stops.
 *
 * HISTORY. Until v1.779.0 this file patched `KanbanView.prototype.render_card` and `.get_data`.
 * Neither exists on frappe 16.50's engine (cards moved to kanban_board.bundle.js; a ListView has
 * no get_data), so from the 2026-10-06 upgrade both patches were dead and the high-value cards
 * and dots were gone. `scripts/test_kanban_customization.js` runs this file against the 16.53
 * shapes so it cannot go quiet again unnoticed.
 */

(function () {
    if (typeof frappe === "undefined" || !frappe.views) return;
    frappe.provide("erpnext_enhancements.kanban");
    const api = erpnext_enhancements.kanban;
    if (api.installed) return;
    api.installed = true;

    const BOARD = "Opportunity";
    const HIGH_VALUE = 4000;
    const COLOR_MAP = {
        Build: "#00609C",
        Design: "#B34FC5",
        Service: "#00A0DF",
        Events: "#65CBC9",
    };
    const PARTY_DOCTYPES = ["Customer", "Lead"];

    function is_opportunity_board(view) {
        if (!view || view.doctype !== "Opportunity") return false;
        return view.board_name === BOARD || (view.board && view.board.name === BOARD);
    }

    function warn(message, error) {
        try {
            console.warn("kanban_customization: " + message, error);
        } catch (e) {
            // nothing left to tell
        }
    }

    // ---- 1. the query carries the party and the amount -------------------------------------
    const View = frappe.views.KanbanView;
    const proto = View && View.prototype;
    if (!proto || typeof proto.set_fields !== "function" || typeof proto.render_kanban_board !== "function") {
        warn("frappe.views.KanbanView has no set_fields/render_kanban_board; nothing patched");
        return;
    }

    const original_set_fields = proto.set_fields;
    proto.set_fields = function () {
        const result = original_set_fields.apply(this, arguments);
        try {
            if (is_opportunity_board(this) && typeof this._add_field === "function") {
                ["party_name", "opportunity_from", "opportunity_amount"].forEach((f) => this._add_field(f));
            }
        } catch (error) {
            warn("could not add the party fields", error);
        }
        return result;
    };

    // ---- 2. the value streams, fetched per page of cards -----------------------------------
    function child_doctype() {
        try {
            const meta = frappe.get_meta("Opportunity");
            const field = meta && meta.fields ? meta.fields.find((f) => f.fieldname === "custom_value_stream") : null;
            return (field && field.options) || "Opportunity Value Stream";
        } catch (e) {
            return "Opportunity Value Stream";
        }
    }

    function attach_value_streams(view) {
        const rows = Array.isArray(view.data) ? view.data : [];
        const names = rows.map((d) => d.name).filter(Boolean);
        if (!names.length || typeof frappe.call !== "function") return Promise.resolve();
        let pending;
        try {
            pending = frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: child_doctype(),
                    filters: { parent: ["in", names] },
                    fields: ["parent", "value_stream"],
                    limit_page_length: 1000,
                },
            });
        } catch (error) {
            warn("value stream fetch failed", error);
            return Promise.resolve();
        }
        return Promise.resolve(pending).then(
            (r) => {
                const by_parent = {};
                ((r && r.message) || []).forEach((row) => {
                    if (!by_parent[row.parent]) by_parent[row.parent] = [];
                    by_parent[row.parent].push(row);
                });
                rows.forEach((d) => {
                    d.custom_value_stream = by_parent[d.name] || [];
                });
            },
            (error) => warn("value stream fetch failed", error)
        );
    }

    const original_render = proto.render_kanban_board;
    proto.render_kanban_board = function () {
        const args = arguments;
        if (!is_opportunity_board(this)) return original_render.apply(this, args);
        return Promise.resolve(this.load_lib)
            .then(() => install_card_wrapper())
            .then(() => attach_value_streams(this))
            .then(() => original_render.apply(this, args));
    };

    // ---- 3. the card ------------------------------------------------------------------------
    function install_card_wrapper() {
        const original_card = frappe.views.KanbanBoardCard;
        if (typeof original_card !== "function" || original_card.ee_wrapped) return;
        const wrapped = function (card, wrapper) {
            const self = original_card.apply(this, arguments);
            try {
                if (card && card.doctype === "Opportunity" && is_opportunity_board(window.cur_list)) {
                    decorate(card, self && self.$card ? self.$card : null);
                }
            } catch (error) {
                warn("could not decorate a card", error);
            }
            return self;
        };
        wrapped.ee_wrapped = true;
        wrapped.original = original_card;
        frappe.views.KanbanBoardCard = wrapped;
    }

    function decorate(card, $card) {
        if (!$card || !$card.length) return;
        const doc = card.doc || {};

        // 1. high value: the dark navy card
        if (parseFloat(doc.opportunity_amount) > HIGH_VALUE) {
            const $body = $card.find(".kanban-card-body");
            ($body.length ? $body : $card).addClass("opportunity-high-value");
            $card.addClass("opportunity-high-value-parent");
        }

        // 2. value stream dots
        render_value_stream_dots($card, doc.custom_value_stream || []);

        // 3. the customer link
        link_party($card, doc);
    }

    function render_value_stream_dots($card, value_streams) {
        if (!value_streams.length) return;
        let $meta = $card.find(".kanban-card-meta");
        if (!$meta.length) $meta = $card.find(".kanban-card-body");
        if (!$meta.length) $meta = $card;

        let $dots = $meta.find(".kanban-card-value-stream");
        if (!$dots.length) {
            $dots = $('<div class="kanban-card-value-stream"></div>').appendTo($meta);
        } else {
            $dots.empty();
        }
        const seen = new Set();
        value_streams.forEach((row) => {
            const color = COLOR_MAP[row.value_stream];
            if (!color || seen.has(color)) return;
            $('<div class="value-stream-dot"></div>').css("background-color", color).attr("title", row.value_stream).appendTo($dots);
            seen.add(color);
        });
    }

    function party_label() {
        try {
            const df = frappe.meta && typeof frappe.meta.get_docfield === "function" ? frappe.meta.get_docfield("Opportunity", "customer_name") : null;
            const label = (df && df.label) || "Customer Name";
            return typeof __ === "function" ? __(label, null, "Opportunity") : label;
        } catch (e) {
            return "Customer Name";
        }
    }

    // The .kanban-doc-field that shows customer_name: its icon carries the label as a title
    // (labels off), or its first span reads "Label: " (labels on).
    function find_party_field($card, label) {
        let found = null;
        $card.find(".kanban-doc-field").each(function () {
            const $field = $(this);
            const icon_title = $field.find(".kanban-doc-icon").attr("title");
            const first = $field.children("span").first().text().trim();
            if (icon_title === label || first === label + ":") {
                found = $field;
                return false;
            }
        });
        return found;
    }

    function link_party($card, doc) {
        const party = doc.party_name;
        const doctype = doc.opportunity_from;
        if (!party || !PARTY_DOCTYPES.includes(doctype)) return;
        const $field = find_party_field($card, party_label());
        if (!$field || $field.find("a.kanban-card-party-link").length) return;
        const $value = $field.children("span").last();
        const text = $value.text().trim();
        if (!text || text === "-") return;

        const href = "/desk/" + doctype.toLowerCase() + "/" + encodeURIComponent(party);
        const $link = $('<a class="kanban-card-party-link"></a>')
            .attr("href", href)
            .attr("title", (typeof __ === "function" ? __("Open {0}", [doctype]) : "Open " + doctype))
            .text(text);
        $value.empty().append($link);
        $link.on("click", function (event) {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.button > 0) return;
            event.preventDefault();
            event.stopPropagation();
            frappe.set_route("Form", doctype, party);
        });
        // A press on the link must not start the card's hold-to-drag either.
        $link.on("mousedown touchstart", (event) => event.stopPropagation());
    }

    api.install_card_wrapper = install_card_wrapper;
    api.decorate = decorate;
    api.HIGH_VALUE = HIGH_VALUE;
    api.COLOR_MAP = COLOR_MAP;
})();
