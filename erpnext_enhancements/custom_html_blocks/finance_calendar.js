// Finance Calendar — Finance Dashboard Custom HTML Block.
//
// Renders upcoming events from the configured "Finance" Google Calendar via
// erpnext_enhancements.api.finance_calendar.get_finance_calendar (server-side
// cached). Shadow-DOM block model.

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

        function load() {
            body.innerHTML = `<div class="fcal-muted">${__("Loading…")}</div>`;
            refresh.disabled = true;
            frappe
                .call({ method: "erpnext_enhancements.api.finance_calendar.get_finance_calendar" })
                .then((r) => render(body, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="fcal-muted">${__("Could not load the calendar.")}</div>`;
                })
                .then(() => {
                    refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
    }

    waitForDOM();
})();
