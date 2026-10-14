// Finance Calendar — Finance Dashboard Custom HTML Block.
//
// Renders upcoming events from the configured "Finance" Google Calendar via
// erpnext_enhancements.api.finance_calendar.get_finance_calendar (server-side
// cached). Shadow-DOM block model: the workspace runs this script again only when
// it renders the page, so a return from a form or a list is reloaded by the shared
// helper, public/js/global_enhancements/workspace_block_return.js.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || kind);
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fcal-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function whenLabel(ev) {
        const raw = ev.start;
        if (!raw) return "";
        const d = new Date(raw);
        if (isNaN(d.getTime())) return raw;
        if (ev.all_day) {
            return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
        }
        return d.toLocaleString([], {
            weekday: "short",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        });
    }

    function render(body, sf, message) {
        if (!message || message.enabled === false) {
            sf.state.render(body, "empty", {
                title: __("Finance Calendar is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        const events = message.events || [];
        if (!events.length) {
            sf.state.render(body, "empty", { title: message.reason || __("No upcoming events.") });
            return;
        }
        body.innerHTML = events
            .map((ev) => {
                const loc = ev.location ? `<div class="fcal-loc">${sf.esc(ev.location)}</div>` : "";
                const title = ev.html_link
                    ? `<a class="fcal-name" href="${sf.esc(ev.html_link)}" target="_blank" rel="noopener">${sf.esc(ev.summary)}</a>`
                    : `<span class="fcal-name">${sf.esc(ev.summary)}</span>`;
                return `
                    <div class="fcal-item">
                        <div class="fcal-when">${sf.esc(whenLabel(ev))}</div>
                        <div class="fcal-detail">
                            ${title}
                            ${loc}
                        </div>
                    </div>`;
            })
            .join("");
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#fcal-body");
        const refresh = container.querySelector("#fcal-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket on the root (blocks.ticket) and only
        // the newest answer draws, into the root it was for.
        function load() {
            const current = ticket(container);
            sf.state.render(body, "loading");
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_calendar.get_finance_calendar" })
                .then((r) => {
                    if (current()) render(body, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load the calendar.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
