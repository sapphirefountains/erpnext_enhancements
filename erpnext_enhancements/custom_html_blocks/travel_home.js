// My Travel — the viewer's own trips, as a Custom HTML Block on the Travel workspace.
//
// The question it answers is the one the hub was redesigned for: "where is MY
// information?" Someone who has never used the system lands on /desk/travel and
// the first thing they see is their current (or next) trip with one obvious
// button, "Open my itinerary", then the documents, the trip sheet and who to call.
// Below that: their other trips, a receipts reminder after a trip, and — for
// travel coordinators only — the trips that need somebody's attention.
//
// WHY A BLOCK AND NOT A WORKSPACE WIDGET. A Number Card, a Quick List or a
// shortcut count carries its filter on the widget, the same for every viewer, so
// none of them can say "the trip YOU are on". The Quick List would be closest and
// is still wrong: it always shows the four most recently created rows, not the
// one you are traveling on this week.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace builds a
// fresh block (a new root, and this whole script run again) only when it renders
// the page: the first visit, and coming back from ANOTHER workspace. Coming back to
// Travel from a trip form, Plan a Trip or a list does not: v16's `Workspace.show()`
// returns early when the page it would show is the one already shown, so the old
// block stays on screen exactly as it was, with the trip you just changed out of
// date. watchReturn() reloads it then. Nothing is cached across renders, every DOM
// listener is bound to the fresh DOM each time, and the one route listener is bound
// once per page load (see watchReturn).
//
// IT COMPUTES NOTHING. `travel_management.home.get_travel_home` sends every
// sentence ready to show ("Starts in 5 days", "Ended 3 days ago", the reasons a
// trip needs attention) and every address ready to open. There is no date
// arithmetic here on purpose: a browser in another time zone would count the days
// differently from the email and the itinerary, and the three would disagree.
// The only thing decided here is presentation — which color a status is.
//
// NO MONEY. The endpoint sends none to anybody who is not a coordinator (crew see
// everything about a trip except what it cost), and this script has nowhere to put
// it if it did.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.travel_management.home.get_travel_home";
    let attempts = 0;

    // A status and the color pair frappe itself uses for it. `--bg-green` /
    // `--text-on-green` and friends are redefined by the dark theme, which is why
    // they are used and a literal color is not.
    const STATUS_TONE = {
        Planning: "orange",
        Booked: "blue",
        "In Progress": "green",
        Completed: "gray",
        Closed: "gray",
    };

    const NO_TRIPS =
        "No trips coming up. When the office books you on one, you'll get an email and it will show up here.";
    // For the person who books trips and is not going on any of them, "when the office
    // books you" would be talking to the office about itself.
    const ORGANIZING_ONLY = "You're not traveling on any upcoming trips. The trips you're organizing are below.";

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#tvh-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
    }

    function attr(name, value) {
        return " " + name + '="' + esc(value) + '"';
    }

    // Only a path on this site or an http(s) address is ever opened. The server
    // builds every one of them; this is the guard that keeps a stray `javascript:`
    // from being one click away if that ever changes.
    function webUrl(value) {
        const text = value === null || value === undefined ? "" : String(value);
        return /^(\/(?!\/)|https?:\/\/)/i.test(text) ? text : "";
    }

    function contactHref(value) {
        const text = value === null || value === undefined ? "" : String(value);
        return /^(tel|mailto):/i.test(text) ? text : "";
    }

    function toneOf(status) {
        return STATUS_TONE[status] || "gray";
    }

    function pill(status) {
        if (!status) return "";
        return '<span class="tvh-pill tone-' + esc(toneOf(status)) + '">' + esc(status) + "</span>";
    }

    // A button that does one thing. `data-action` says which, and bind() below reads
    // it back: open (a web page in a new tab), form (the trip), plan (Plan a Trip at
    // its review step), route (a desk page) or list (every trip).
    function button(icon, label, attrs, extra) {
        return (
            '<button type="button" class="tvh-btn' +
            (extra ? " " + extra : "") +
            '"' +
            attrs +
            ">" +
            (icon ? '<span class="tvh-btn-icon" aria-hidden="true">' + esc(icon) + "</span>" : "") +
            '<span class="tvh-btn-label">' +
            esc(label) +
            "</span></button>"
        );
    }

    function openButton(icon, label, url, extra) {
        const safe = webUrl(url);
        if (!safe) return "";
        return button(icon, label, attr("data-action", "open") + attr("data-url", safe), extra);
    }

    function formButton(icon, label, trip) {
        if (!trip) return "";
        return button(icon, label, attr("data-action", "form") + attr("data-trip", trip));
    }

    function planButton(icon, label, trip) {
        if (!trip) return "";
        return button(icon, label, attr("data-action", "plan") + attr("data-trip", trip));
    }

    function notice(icon, text, tone, actions) {
        return (
            '<div class="tvh-notice' +
            (tone ? " tone-" + esc(tone) : "") +
            '"><span class="tvh-notice-icon" aria-hidden="true">' +
            esc(icon) +
            '</span><div class="tvh-notice-body"><div class="tvh-notice-text">' +
            esc(text) +
            "</div>" +
            (actions ? '<div class="tvh-actions">' + actions + "</div>" : "") +
            "</div></div>"
        );
    }

    // ------------------------------------------------------------ who to call

    // A phone number or an address as a real link, so a tap dials or starts an email.
    function contactLinks(row) {
        const links = [];
        const tel = contactHref(row.phone_href);
        const mail = contactHref(row.email_href);
        if (tel) {
            links.push(
                '<a class="tvh-link-btn"' +
                    attr("href", tel) +
                    '><span class="tvh-link-icon" aria-hidden="true">' +
                    esc("📞") +
                    "</span> " +
                    esc(row.phone || "Call") +
                    "</a>"
            );
        }
        if (mail) {
            links.push(
                '<a class="tvh-link-btn"' +
                    attr("href", mail) +
                    '><span class="tvh-link-icon" aria-hidden="true">' +
                    esc("📧") +
                    "</span> " +
                    esc(row.email || "Email") +
                    "</a>"
            );
        }
        // A hotel's nearest urgent care and directions, the job site's directions: map
        // pages, opened in a new tab like every other web address here.
        (row.links || []).forEach((link) => {
            const url = webUrl(link && link.url);
            if (!url) return;
            links.push(
                '<button type="button" class="tvh-link-btn"' +
                    attr("data-action", "open") +
                    attr("data-url", url) +
                    '><span class="tvh-link-icon" aria-hidden="true">' +
                    esc("📍") +
                    "</span> " +
                    esc(link.label || "Map") +
                    "</button>"
            );
        });
        return links;
    }

    function contactRow(row) {
        const links = contactLinks(row);
        // A number or address with no link the server was willing to make is still
        // worth reading out; it is shown as plain text rather than dropped.
        const plain = [];
        if (!contactHref(row.phone_href) && row.phone) plain.push(row.phone);
        if (!contactHref(row.email_href) && row.email) plain.push(row.email);
        // "Travel desk" under a "TRAVEL DESK" label says it twice.
        const name =
            row.name && String(row.name).toLowerCase() !== String(row.label || "").toLowerCase() ? row.name : "";

        return (
            '<div class="tvh-contact"><div class="tvh-contact-label">' +
            esc(row.label) +
            "</div>" +
            (name ? '<div class="tvh-contact-name">' + esc(name) + "</div>" : "") +
            (row.detail ? '<div class="tvh-contact-detail">' + esc(row.detail) + "</div>" : "") +
            (plain.length ? '<div class="tvh-contact-detail">' + esc(plain.join(" · ")) + "</div>" : "") +
            (links.length ? '<div class="tvh-contact-links">' + links.join("") + "</div>" : "") +
            "</div>"
        );
    }

    function contactsBlock(contacts) {
        const rows = (contacts || []).filter(
            (row) => row && (row.name || row.detail || row.phone || row.email)
        );
        if (!rows.length) return "";
        // Shut by default: it is the thing you need rarely and urgently, so it is
        // one tap away rather than in the way of the itinerary button.
        return (
            '<details class="tvh-contacts"><summary class="tvh-contacts-summary">' +
            '<span class="tvh-chevron" aria-hidden="true">&#8250;</span>' +
            '<span aria-hidden="true">' +
            esc("📞") +
            "</span> Who to call" +
            '<span class="tvh-count">' +
            esc(rows.length) +
            "</span></summary>" +
            '<div class="tvh-contact-list">' +
            rows.map(contactRow).join("") +
            "</div></details>"
        );
    }

    // ------------------------------------------------------------ the featured trip

    function featuredCard(trip) {
        const tone = toneOf(trip.status);
        const meta = [];
        if (trip.dates) meta.push('<span class="tvh-meta-item">' + esc(trip.dates) + "</span>");
        // A job or customer name can be wider than a phone: it may wrap, the dates may not.
        if (trip.travel_for) {
            meta.push('<span class="tvh-meta-item tvh-meta-for">' + esc(trip.travel_for) + "</span>");
        }
        meta.push(pill(trip.status));

        const actions = [
            openButton("🗺️", "Open my itinerary", trip.itinerary_url, "is-primary"),
            openButton("📄", "My trip sheet", trip.sheet_url),
            openButton("📎", "My documents (" + (Number(trip.documents) || 0) + ")", trip.docs_url),
            formButton("📋", "Trip details", trip.trip),
        ].join("");

        return (
            '<section class="tvh-feature tone-' +
            esc(tone) +
            '"><div class="tvh-feature-top">' +
            '<span class="tvh-feature-icon" aria-hidden="true">' +
            esc("✈️") +
            '</span><div class="tvh-feature-text">' +
            (trip.headline ? '<div class="tvh-headline">' + esc(trip.headline) + "</div>" : "") +
            '<div class="tvh-feature-title">' +
            esc(trip.purpose || trip.trip) +
            "</div>" +
            '<div class="tvh-feature-meta">' +
            meta.join("") +
            "</div></div></div>" +
            '<div class="tvh-actions">' +
            actions +
            "</div>" +
            contactsBlock(trip.contacts) +
            "</section>"
        );
    }

    // ------------------------------------------------------------ other trips

    function tripRow(trip) {
        const organizing = trip.relation === "organizing";
        const meta = [];
        if (trip.dates) meta.push('<span class="tvh-meta-item">' + esc(trip.dates) + "</span>");
        meta.push(pill(trip.status));
        if (trip.note) meta.push('<span class="tvh-note">' + esc(trip.note) + "</span>");

        const actions = [
            openButton("🗺️", "Itinerary", trip.itinerary_url),
            formButton("📋", "Details", trip.trip),
            trip.can_plan ? planButton("✏️", "Keep planning", trip.trip) : "",
        ].join("");

        return (
            '<div class="tvh-trip"><span class="tvh-trip-icon" aria-hidden="true">' +
            esc(organizing ? "🗂️" : "✈️") +
            '</span><div class="tvh-trip-main"><div class="tvh-trip-title">' +
            esc(trip.purpose || trip.trip) +
            '</div><div class="tvh-trip-meta">' +
            meta.join("") +
            "</div></div>" +
            '<div class="tvh-trip-actions">' +
            actions +
            "</div></div>"
        );
    }

    function tripsTitle(data, trips) {
        if (data.featured) return "Your other trips";
        if (trips.every((trip) => trip.relation === "organizing")) return "Trips you're organizing";
        return "Your trips";
    }

    function tripsSection(data) {
        const trips = data.trips || [];
        if (!trips.length) return "";
        return (
            '<section class="tvh-section"><div class="tvh-section-title">' +
            esc(tripsTitle(data, trips)) +
            '</div><div class="tvh-trips">' +
            trips.map(tripRow).join("") +
            "</div></section>"
        );
    }

    // ------------------------------------------------------------ receipts

    function receiptsSection(data) {
        const rows = data.receipts || [];
        if (!rows.length) return "";
        return rows
            .map((row) =>
                notice(
                    "🧾",
                    row.text,
                    "orange",
                    openButton("📘", "Travel guidelines", data.guidelines_url) +
                        formButton("📋", "Open the trip", row.trip)
                )
            )
            .join("");
    }

    // ------------------------------------------------------------ coordinators

    function attentionRow(row) {
        const target = row.target === "form" ? "form" : "plan";
        const reasons = (row.reasons || [])
            .map((reason) => '<li class="tvh-reason">' + esc(reason) + "</li>")
            .join("");
        const meta = [];
        if (row.dates) meta.push('<span class="tvh-meta-item">' + esc(row.dates) + "</span>");
        meta.push(pill(row.status));
        return (
            '<button type="button" class="tvh-attn-row"' +
            attr("data-action", target) +
            attr("data-trip", row.trip) +
            '><span class="tvh-attn-main"><span class="tvh-attn-title">' +
            esc(row.purpose || row.trip) +
            '</span><span class="tvh-attn-meta">' +
            meta.join("") +
            "</span>" +
            (reasons ? '<ul class="tvh-reasons">' + reasons + "</ul>" : "") +
            '</span><span class="tvh-go" aria-hidden="true">&#8250;</span></button>'
        );
    }

    function setupRow(row) {
        return (
            '<button type="button" class="tvh-setup-row"' +
            attr("data-action", "route") +
            attr("data-route", row.route) +
            '><span class="tvh-setup-icon" aria-hidden="true">' +
            esc("⚙️") +
            '</span><span class="tvh-setup-text">' +
            esc(row.text) +
            '</span><span class="tvh-go" aria-hidden="true">&#8250;</span></button>'
        );
    }

    function attentionSection(attention) {
        if (!attention) return "";
        const trips = attention.trips || [];
        const setup = attention.setup || [];
        const more = Number(attention.more) || 0;
        if (!trips.length && !setup.length) return "";

        let html =
            '<section class="tvh-section tvh-attention"><div class="tvh-section-title">' +
            esc("Needs attention") +
            '</div><div class="tvh-section-note">' +
            esc("Only travel coordinators see this list.") +
            "</div>";
        if (trips.length) {
            html += '<div class="tvh-attn-list">' + trips.map(attentionRow).join("") + "</div>";
        }
        if (more > 0) {
            html +=
                '<div class="tvh-more"><span>' +
                esc("…and " + more + " more.") +
                "</span>" +
                button("", "See every trip", attr("data-action", "list"), "is-quiet") +
                "</div>";
        }
        if (setup.length) {
            html +=
                '<div class="tvh-subtitle">' +
                esc("Setup") +
                '</div><div class="tvh-attn-list">' +
                setup.map(setupRow).join("") +
                "</div>";
        }
        return html + "</section>";
    }

    // ------------------------------------------------------------ footer

    function officeFooter(office) {
        if (!office) return "";
        const links = contactLinks(office);
        if (!links.length) return "";
        return (
            '<div class="tvh-footer"><span class="tvh-footer-text">' +
            esc("Questions about a trip? Contact the " + (office.label || "Travel desk") + ":") +
            "</span>" +
            links.join("") +
            "</div>"
        );
    }

    // ------------------------------------------------------------ render

    function render(container, data) {
        const body = container.querySelector("#tvh-body");
        const sub = container.querySelector("#tvh-sub");
        const viewer = data.viewer || {};

        sub.textContent = viewer.first_name
            ? "Hi " + viewer.first_name + ". Your trips, your itinerary and who to call."
            : "Your trips, your itinerary and who to call.";

        const parts = [];
        if (data.message) {
            // The server's sentence: no Employee is linked to this user, so no trip can
            // be theirs. "No trips" would be a statement about them, and wrong.
            parts.push(notice("ℹ️", data.message, "blue", ""));
        } else if (data.featured) {
            parts.push(featuredCard(data.featured));
        } else {
            const trips = data.trips || [];
            const organizingOnly = trips.length && trips.every((trip) => trip.relation === "organizing");
            parts.push(notice("🧳", organizingOnly ? ORGANIZING_ONLY : NO_TRIPS, "", ""));
        }
        parts.push(receiptsSection(data));
        parts.push(tripsSection(data));
        parts.push(attentionSection(data.attention));
        parts.push(officeFooter(data.office));

        body.innerHTML = parts.filter(Boolean).join("");
        bind(body);
    }

    function openUrl(url) {
        const safe = webUrl(url);
        if (safe) window.open(safe, "_blank", "noopener");
    }

    function bind(body) {
        body.querySelectorAll("[data-action]").forEach((node) => {
            node.addEventListener("click", () => {
                const action = node.getAttribute("data-action");
                const trip = node.getAttribute("data-trip");
                if (action === "open") {
                    openUrl(node.getAttribute("data-url"));
                } else if (action === "form" && trip) {
                    frappe.set_route("travel-trip", trip);
                } else if (action === "plan" && trip) {
                    // Plan a Trip reads its address from frappe.route_options in v16,
                    // exactly as the trip form's "Plan step by step" button hands it over.
                    frappe.route_options = { trip: trip, step: "review" };
                    frappe.set_route("plan-a-trip");
                } else if (action === "route") {
                    const route = node.getAttribute("data-route");
                    if (route) frappe.set_route(route);
                } else if (action === "list") {
                    frappe.set_route("List", "Travel Trip");
                }
            });
        });
    }

    function load(container) {
        const body = container.querySelector("#tvh-body");
        // The refresh button and a return to the hub can both ask while an answer is
        // on its way, so each ask takes a ticket and only the newest one draws: an
        // older answer arriving late never paints over a newer one. The ticket lives
        // on the root itself, because the route handler that calls this may belong to
        // an earlier run of this script. Each answer only ever draws into the root it
        // was asked for, so one for a block that has been replaced lands nowhere seen.
        const ticket = (container.__tvh_ticket = (container.__tvh_ticket || 0) + 1);
        frappe
            .call({ method: METHOD })
            .then((r) => {
                if (container.__tvh_ticket === ticket) render(container, (r && r.message) || {});
            })
            .catch(() => {
                // frappe has already shown the server's message. Leave a line behind
                // so the block is not silently blank.
                if (container.__tvh_ticket !== ticket) return;
                body.innerHTML = '<div class="tvh-muted">Could not load your travel.</div>';
            });
    }

    // Coming back to the hub from a form or Plan a Trip does not rebuild this block
    // (the header above: v16's Workspace.show() returns early on the workspace already
    // shown), so reload it when the route comes back to Travel. `frappe.router` is an
    // event emitter whose `off()` wraps the handler in a new function before it
    // unbinds, so it can never remove one: one handler per page load, behind a
    // window flag, and it reloads the newest root (`window.__tvh_root`). A host no
    // longer in the page means the workspace was rendered again, and the fresh
    // block's own run of this script has loaded itself. The route is v16's for a
    // workspace: ["Workspaces", name], or ["Workspaces", "private", name].
    function watchReturn(container) {
        window.__tvh_root = container;
        if (window.__tvh_route_bound || !frappe.router || !frappe.router.on) return;
        window.__tvh_route_bound = true;
        frappe.router.on("change", () => {
            const root = window.__tvh_root;
            const host = root && root.host;
            if (!host || !host.isConnected) return;
            const route = frappe.get_route() || [];
            const name = route[1] === "private" ? route[2] : route[1];
            if (route[0] !== "Workspaces" || name !== "Travel") return;
            load(root);
        });
    }

    function startApp(container) {
        const refresh = container.querySelector("#tvh-refresh");
        if (refresh) refresh.addEventListener("click", () => load(container));
        load(container);
        watchReturn(container);
    }

    waitForDOM();
})();
