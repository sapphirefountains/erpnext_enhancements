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
 * then the board's configured card fields, the image and color fields), and `render()` ->
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
 *   2. `KanbanView.prototype.render_kanban_board` — after `load_lib` resolves and before the
 *      original draws, install hook 3 (once).
 *   3. `frappe.views.KanbanBoardCard` — a function the bundle defines when it loads. It builds
 *      `var self = {}` and ends with a bare `init();`: it RETURNS NOTHING, and its one caller
 *      (`append_kanban_card`) ignores the result. So after the original has drawn, the card is
 *      found in `wrapper` by its `data-name` (`.kanban-card-wrapper[data-name=encodeURIComponent
 *      (name)]`, from kanban_card.html) and decorated: the high-value class on
 *      `.kanban-card-body`, the dots in `.kanban-card-meta`, and the customer link.
 *
 * THE DOTS are a child table (`custom_value_stream`), which no list query carries, and cards are
 * drawn by more than the first render: `process_document_refreshes` replaces rows after a
 * realtime update and `kanban_pagination.js` rewrites `this.data` on scroll (50 cards a page), both
 * without passing `render_kanban_board`. So the rows are fetched per CARD, lazily: a card whose
 * `doc.custom_value_stream` is still undefined is queued, the queue is fetched once per tick
 * (`frappe.client.get_list`, `parent in [...]`), and the dots are drawn when the answer lands;
 * the rows are then kept on `card.doc` (the data row), so a redraw of the same row asks nothing.
 * A failed fetch draws the card without dots.
 *
 * THE CUSTOMER LINK. Nik, 2026-11-03, from the route audit: the Opportunity Kanban to the
 * Customer list 47 times in 90 days and back 42, eleven of them within a minute — people looking
 * a deal's customer up by hand. The card's "Customer Name" field (board field `customer_name`, a
 * Data field) becomes a link to the party: `/desk/customer/<party_name>` when `opportunity_from`
 * is Customer, `/desk/lead/<party_name>` for a Lead. The field is found by the label the engine
 * writes on its icon (or, with "Show Labels" on, in its first span), read from the docfield, not
 * by its position. The click is handled here — `frappe.set_route("Form", ...)`, a history entry of
 * its own, so Back returns to the board — and stopped from bubbling. In 16.53 the only opener on
 * a card is the title link (kanban_card.html), so this is not about a double open: stopping keeps
 * the board's other delegated handlers out of a click that is already routed, and once
 * propagation stops a bare href would never reach the Desk's body-level link delegate, hence
 * the explicit route. The press handlers on the link are a courtesy for the hold-to-drag patch
 * (SortableJS listens on pointerdown where supported, so they may not stop a drag).
 *
 * HISTORY. Until v1.779.0 this file patched `KanbanView.prototype.render_card` and `.get_data`.
 * Neither exists on frappe 16.50's engine (cards moved to kanban_board.bundle.js; a ListView has
 * no get_data), so from the 2026-10-06 upgrade both patches were dead and the high-value cards
 * and dots were gone. The first cut of v1.779.0 then read `KanbanBoardCard`'s return value, which
 * is undefined, and was just as dead (review of PR #1413). `scripts/test_kanban_customization.js`
 * runs this file against the 16.53 shapes, the void return included, so it cannot go quiet again
 * unnoticed.
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

    // ---- 2. the value streams, fetched per card, batched per tick ---------------------------
    function child_doctype() {
        try {
            const meta = frappe.get_meta("Opportunity");
            const field = meta && meta.fields ? meta.fields.find((f) => f.fieldname === "custom_value_stream") : null;
            return (field && field.options) || "Opportunity Value Stream";
        } catch (e) {
            return "Opportunity Value Stream";
        }
    }

    // name -> [{doc, $card}] waiting for their rows; flushed once per tick.
    let queue = {};
    let flush_timer = null;

    function queue_value_streams(doc, $card) {
        if (!doc || !doc.name || doc.custom_value_stream !== undefined) return;
        (queue[doc.name] = queue[doc.name] || []).push({ doc, $card });
        if (flush_timer !== null) return;
        flush_timer = setTimeout(() => {
            flush_timer = null;
            const waiting = queue;
            queue = {};
            fetch_value_streams(waiting);
        }, 0);
    }

    function fetch_value_streams(waiting) {
        const names = Object.keys(waiting);
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
                names.forEach((name) => {
                    waiting[name].forEach(({ doc, $card }) => {
                        doc.custom_value_stream = by_parent[name] || [];
                        render_value_stream_dots($card, doc.custom_value_stream);
                    });
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
            .then(() => original_render.apply(this, args));
    };

    // ---- 3. the card ------------------------------------------------------------------------
    function install_card_wrapper() {
        const original_card = frappe.views.KanbanBoardCard;
        if (typeof original_card !== "function" || original_card.ee_wrapped) return;
        const wrapped = function (card, wrapper) {
            // The original returns undefined (a bare `init();`), so the result is not read.
            const result = original_card.apply(this, arguments);
            try {
                if (card && card.doctype === "Opportunity" && is_opportunity_board(window.cur_list)) {
                    decorate(card, find_card(wrapper, card));
                }
            } catch (error) {
                warn("could not decorate a card", error);
            }
            return result;
        };
        wrapped.ee_wrapped = true;
        wrapped.original = original_card;
        frappe.views.KanbanBoardCard = wrapped;
    }

    // The card the original just appended to `wrapper`: kanban_card.html gives the outer
    // `.kanban-card-wrapper` a `data-name` of `encodeURIComponent(name)`. The last child is the
    // fallback, since `append_kanban_card` appends in order.
    function find_card(wrapper, card) {
        const $host = wrapper && typeof wrapper.children === "function" ? wrapper : $(wrapper);
        const $cards = $host.children(".kanban-card-wrapper");
        if (!$cards.length) return null;
        const wanted = encodeURIComponent(card.name);
        let found = null;
        $cards.each(function () {
            const $candidate = $(this);
            if ($candidate.attr("data-name") === wanted) {
                found = $candidate;
                return false;
            }
        });
        return found || $cards.last();
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

        // 2. value stream dots: drawn now when the rows are known, else once they arrive
        if (doc.custom_value_stream !== undefined) render_value_stream_dots($card, doc.custom_value_stream || []);
        else queue_value_streams(doc, $card);

        // 3. the customer link
        link_party($card, doc);
    }

    function render_value_stream_dots($card, value_streams) {
        if (!value_streams || !value_streams.length || !$card || !$card.length) return;
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
        // A press on the link should not start the card's hold-to-drag either (see the header).
        $link.on("mousedown touchstart", (event) => event.stopPropagation());
    }

    api.install_card_wrapper = install_card_wrapper;
    api.decorate = decorate;
    api.HIGH_VALUE = HIGH_VALUE;
    api.COLOR_MAP = COLOR_MAP;
})();
