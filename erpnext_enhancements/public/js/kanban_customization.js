/*
 * Opportunity Kanban Customization (frappe 16.53's classic Kanban engine)
 * - Value stream indicator dots in the card footer from the 'custom_value_stream' child table
 * - The customer's name on a card is a link to the Customer (or Lead) form (v1.779.0)
 * - One contact row: the Full Name with the contact's mobile number after it (v1.794.0)
 * - No tag pills, no comment count, no like heart on a card (v1.794.0)
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
 * (views/kanban/kanban_view.js) is a ListView: `set_fields()` picks the columns the query
 * fetches (name, creation, the column field, the title, _assign, _user_tags, _liked_by, owner,
 * then the board's configured card fields, the image and color fields); the cards come from
 * `get_kanban_board_data` / `get_kanban_column_page` (kanban_pagination.js), which send
 * `get_call_args().args` — the ListView's `get_args()`, `with_comment_count` included — and
 * `render()` -> `render_kanban_board()` hands `this.data` to `frappe.views.KanbanBoard` once
 * kanban_board.bundle.js has loaded (`this.load_lib`, a frappe.require). That bundle's
 * `frappe.views.KanbanBoardCard(card, wrapper)` draws one card: the title, then one
 * `.kanban-doc-field` per board field (`get_doc_content`: an icon span titled with the field's
 * label, then the formatted value), then `.kanban-card-meta` (`render_card_meta`: the tag pills
 * when `card.tags` — the row's `_user_tags` — is set, the comment count when `card.comment_count`
 * is, the assignees, and `cur_list.get_like_html(card)`). `card.doc` is the data row.
 *
 * So the hooks, all on the Opportunity board only:
 *   1. `KanbanView.prototype.set_fields` — after the original, add `party_name` and
 *      `opportunity_from` (whom to link), `primary_contact` and `custom_contact_mobile_number`
 *      (the contact row) to the query (`_add_field` de-duplicates and drops a field the site
 *      lacks), and take `_user_tags` and `_liked_by` OUT of it: a card whose row has no
 *      `_user_tags` gets no `.kanban-tags` at all (`get_tags_html` returns "" for it), so the
 *      pills are gone at the source rather than hidden.
 *   2. `KanbanView.prototype.get_args` — `with_comment_count = 0`. The ListView sets it to 1
 *      unless the doctype's List View Settings disable it (list_view.js ~785); that setting
 *      would also change the Opportunity list, so the switch is thrown here, for the board. The
 *      server then computes no `_comment_count`, and `render_card_meta` draws no count.
 *   3. `KanbanView.prototype.get_like_html` — "" on this board, so `render_card_meta` draws no
 *      heart. The list view's rows are not affected: the method is shadowed on the Kanban
 *      view's prototype, the ListView's own stays.
 *   4. `KanbanView.prototype.render_kanban_board` — after `load_lib` resolves and before the
 *      original draws, install hook 5 (once).
 *   5. `frappe.views.KanbanBoardCard` — a function the bundle defines when it loads. It builds
 *      `var self = {}` and ends with a bare `init();`: it RETURNS NOTHING, and its one caller
 *      (`append_kanban_card`) ignores the result. So after the original has drawn, the card is
 *      found in `wrapper` by its `data-name` (`.kanban-card-wrapper[data-name=encodeURIComponent
 *      (name)]`, from kanban_card.html) and decorated: the dots in `.kanban-card-meta`, the
 *      customer link, and the contact row.
 *
 * THE DOTS are a child table (`custom_value_stream`), which no list query carries, and cards are
 * drawn by more than the first render: `process_document_refreshes` replaces rows after a
 * realtime update and `kanban_pagination.js` rewrites `this.data` on scroll (50 cards a page), both
 * without passing `render_kanban_board`. So the rows are fetched per CARD, lazily: a card whose
 * `doc.custom_value_stream` is still undefined is queued, the queue is fetched once per tick
 * (`frappe.client.get_list`, `parent in [...]`), and the dots are drawn when the answer lands;
 * the rows are then kept on `card.doc` (the data row), so a redraw of the same row asks nothing.
 * A failed fetch draws the card without dots. Every dot (and their container) carries all the
 * stream names as a tooltip, since from v1.794.0 the dots are the only mark of a deal's streams
 * on the card.
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
 * THE CONTACT ROW. Nik, 2026-11-03: "we want the Full Name of the contact to have their phone
 * number next to their name instead of just a new line for the phone." "Full Name" is the label
 * of `primary_contact`, a Link to Contact (TASK-2026-01075 put it on the board in place of
 * `contact_person` in 2026-08); the phone is `custom_contact_mobile_number`, fetched from the
 * Contact. On prod the board's fields are customer_name, primary_contact,
 * custom_contact_mobile_number, opportunity_amount (2026-11-03), so the engine draws a name row and
 * a phone row. This file merges them: the phone row is removed, and the phone — from the data
 * row, not from the drawn text — is appended to the name row's value as " · (801) 555-0100".
 * The name row's value is left as the engine formatted it: a link to the Contact showing the
 * Contact's title (Contact has show_title_field_in_link, and the Kanban endpoints send the link
 * titles the way the list view's do). No phone: the name alone. No contact: neither row — a
 * phone without a name is dropped with it. The rows are found by label, like the customer field.
 * The board's own field list still decides what is drawn: take `primary_contact` off the board
 * and there is no contact row to merge into, and nothing is invented.
 *
 * NO FILL. v1.779.0 (#1413) restored the navy fill on deals over 4000 that the pre-16.50 file had
 * drawn. Nik, 2026-11-03, on the live board: "I want the dark blue removed since it makes it
 * difficult to read." Withdrawn in v1.794.0 with its styles; every card reads on the normal card
 * background in both themes. A later high-value marker, if wanted, will be a small accent.
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
    const COLOR_MAP = {
        Build: "#00609C",
        Design: "#B34FC5",
        Service: "#00A0DF",
        Events: "#65CBC9",
    };
    const PARTY_DOCTYPES = ["Customer", "Lead"];
    // The contact row: the Full Name (a Link to Contact) and the mobile fetched from it.
    const CONTACT_FIELD = "primary_contact";
    const CONTACT_PHONE_FIELD = "custom_contact_mobile_number";
    const CONTACT_SEPARATOR = " · ";
    // Taken out of the board's query: with no `_user_tags` on a row the engine draws no pills,
    // and nothing reads `_liked_by` once get_like_html draws no heart.
    const UNFETCHED_FIELDS = ["_user_tags", "_liked_by"];

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

    function translate(text, args, context) {
        return typeof __ === "function" ? __(text, args, context) : text;
    }

    // ---- 1. the query: the party and the contact in, the tags and likes out -----------------
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
                // The party and the contact fields are always in the query, whatever the board's
                // Card fields picker says: the data row must carry them for the link and the
                // merged contact row, while the picker still decides which rows are DRAWN.
                ["party_name", "opportunity_from", CONTACT_FIELD, CONTACT_PHONE_FIELD].forEach((f) => this._add_field(f));
                if (Array.isArray(this.fields)) {
                    // base_list._add_field stores [fieldname, doctype] pairs
                    this.fields = this.fields.filter((f) => !UNFETCHED_FIELDS.includes(Array.isArray(f) ? f[0] : f));
                }
            }
        } catch (error) {
            warn("could not set the card's fields", error);
        }
        return result;
    };

    // ---- 2. no comment count ----------------------------------------------------------------
    const original_get_args = proto.get_args;
    if (typeof original_get_args === "function") {
        proto.get_args = function () {
            const args = original_get_args.apply(this, arguments);
            try {
                if (args && is_opportunity_board(this)) args.with_comment_count = 0;
            } catch (error) {
                warn("could not switch the comment count off", error);
            }
            return args;
        };
    } else {
        warn("frappe.views.KanbanView has no get_args; the comment count stays");
    }

    // ---- 3. no like heart -------------------------------------------------------------------
    const original_get_like_html = proto.get_like_html;
    if (typeof original_get_like_html === "function") {
        proto.get_like_html = function () {
            try {
                if (is_opportunity_board(this)) return "";
            } catch (error) {
                warn("could not hide the like", error);
            }
            return original_get_like_html.apply(this, arguments);
        };
    } else {
        warn("frappe.views.KanbanView has no get_like_html; the like stays");
    }

    // ---- 4. the value streams, fetched per card, batched per tick ---------------------------
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
                    // The parent names the permission to check: without it, 16.53's
                    // db_query._set_permission_map checks the child doctype itself and
                    // has_child_permission refuses everyone but Administrator (review round 2).
                    parent: "Opportunity",
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
            (error) => {
                warn("value stream fetch failed", error);
                // Mark the rows as tried, so a redraw (scroll virtualization re-draws a card
                // many times) does not ask again and again; no dots until a reload.
                names.forEach((name) => {
                    waiting[name].forEach(({ doc }) => {
                        if (doc.custom_value_stream === undefined) doc.custom_value_stream = [];
                    });
                });
            }
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

    // ---- 5. the card ------------------------------------------------------------------------
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

        // the value stream dots: drawn now when the rows are known, else once they arrive
        if (doc.custom_value_stream !== undefined) render_value_stream_dots($card, doc.custom_value_stream || []);
        else queue_value_streams(doc, $card);

        // the customer link
        link_party($card, doc);

        // the contact row: the name with the phone after it
        merge_contact_row($card, doc);
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
        const streams = [];
        value_streams.forEach((row) => {
            const color = COLOR_MAP[row.value_stream];
            if (!color || seen.has(color)) return;
            seen.add(color);
            streams.push(row);
        });
        // The dots are the card's only mark of its streams (the tag pills are gone), so they say
        // what they mean. The FULL list goes on every dot: an inner title wins over the
        // container's, and the 8px dots cover the container, so a title on the container alone
        // is unreachable (review of #1420).
        const title = streams.length ? translate("Value streams: {0}", [streams.map((row) => row.value_stream).join(", ")]) : "";
        streams.forEach((row) => {
            $('<div class="value-stream-dot"></div>').css("background-color", COLOR_MAP[row.value_stream]).attr("title", title).appendTo($dots);
        });
        $dots.attr("title", title);
    }

    function field_label(fieldname, fallback) {
        try {
            const df = frappe.meta && typeof frappe.meta.get_docfield === "function" ? frappe.meta.get_docfield("Opportunity", fieldname) : null;
            return translate((df && df.label) || fallback, null, "Opportunity");
        } catch (e) {
            return fallback;
        }
    }

    // The .kanban-doc-field that shows a field: its icon carries the label as a title (labels
    // off), or its first span reads "Label: " (labels on).
    function find_field($card, label) {
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
        const $field = find_field($card, field_label("customer_name", "Customer Name"));
        if (!$field || $field.find("a.kanban-card-party-link").length) return;
        const $value = $field.children("span").last();
        const text = $value.text().trim();
        if (!text || text === "-") return;

        const href = "/desk/" + doctype.toLowerCase() + "/" + encodeURIComponent(party);
        const $link = $('<a class="kanban-card-party-link"></a>')
            .attr("href", href)
            .attr("title", translate("Open {0}", [doctype]))
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

    function merge_contact_row($card, doc) {
        const $name_row = find_field($card, field_label(CONTACT_FIELD, "Full Name"));
        const $phone_row = find_field($card, field_label(CONTACT_PHONE_FIELD, "Contact Mobile Number"));
        // The phone joins the name's row, or goes with it: never a row of its own.
        if ($phone_row) $phone_row.remove();
        if (!$name_row || $name_row.find(".kanban-card-contact-phone").length) return;

        const $value = $name_row.children("span").last();
        const name = $value.text().trim();
        if (!doc[CONTACT_FIELD] || !name || name === "-") {
            $name_row.remove();
            return;
        }
        const phone = String(doc[CONTACT_PHONE_FIELD] || "").trim();
        if (phone) {
            // A sibling of the engine's .truncate span, not a child of it: the row is a flex
            // line, so a long name is what gets the ellipsis and the number stays whole.
            $('<span class="kanban-card-contact-phone"></span>').text(CONTACT_SEPARATOR + phone).insertAfter($value);
        }
        $name_row.find(".kanban-doc-icon").attr("title", translate("Contact"));
    }

    api.install_card_wrapper = install_card_wrapper;
    api.decorate = decorate;
    api.COLOR_MAP = COLOR_MAP;
})();
