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

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fcal-body")) {
            startApp(container);
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

    function render(body, message) {
        const esc = frappe.utils.escape_html;
        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fcal-muted">${__("Finance Calendar is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }
        const events = message.events || [];
        if (!events.length) {
            body.innerHTML = `<div class="fcal-muted">${esc(message.reason || __("No upcoming events."))}</div>`;
            return;
        }
        body.innerHTML = events
            .map((ev) => {
                const loc = ev.location ? `<div class="fcal-loc">${esc(ev.location)}</div>` : "";
                const title = ev.html_link
                    ? `<a class="fcal-name" href="${esc(ev.html_link)}" target="_blank" rel="noopener">${esc(ev.summary)}</a>`
                    : `<span class="fcal-name">${esc(ev.summary)}</span>`;
                return `
                    <div class="fcal-item">
                        <div class="fcal-when">${esc(whenLabel(ev))}</div>
                        <div class="fcal-detail">
                            ${title}
                            ${loc}
                        </div>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#fcal-body");
        const refresh = container.querySelector("#fcal-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket and only the newest one draws.
        function load() {
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            body.innerHTML = `<div class="fcal-muted">${__("Loading…")}</div>`;
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_calendar.get_finance_calendar" })
                .then((r) => {
                    if (current()) render(body, r.message);
                })
                .catch(() => {
                    if (!current()) return;
                    body.innerHTML = `<div class="fcal-muted">${__("Could not load the calendar.")}</div>`;
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
