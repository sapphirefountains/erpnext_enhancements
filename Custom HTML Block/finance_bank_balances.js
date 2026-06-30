// Bank Balances — Finance Dashboard Custom HTML Block.
//
// Reads the cached snapshot from
// erpnext_enhancements.plaid_banking.core.api.get_bank_balances (the widget never
// calls Plaid directly). "Refresh" spends a live Plaid call via refresh_now.
// On a reconnect-required state it shows an amber banner routing to Plaid
// Settings instead of stale numbers. Shadow-DOM block model (state on window).

(function () {
    const MAX_ATTEMPTS = 50;
    const READ = "erpnext_enhancements.plaid_banking.core.api.get_bank_balances";
    const REFRESH = "erpnext_enhancements.plaid_banking.core.api.refresh_now";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fbb-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function money(value, currency) {
        if (value === null || value === undefined) return "—";
        return frappe.format(value, { fieldtype: "Currency", options: currency || "USD" });
    }

    function render(container, message) {
        const esc = frappe.utils.escape_html;
        const body = container.querySelector("#fbb-body");
        const meta = container.querySelector("#fbb-meta");
        meta.textContent = "";

        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fbb-muted">${__("Bank Balances are turned off. Enable Plaid in Plaid Settings.")}</div>`;
            return;
        }
        if (message.reconnect_required) {
            body.innerHTML = `<div class="fbb-reconnect">${__("Bank connection needs re-authentication.")}
                <a href="/app/plaid-settings">${__("Open Plaid Settings → Reconnect Bank")}</a></div>`;
            return;
        }
        const accounts = message.accounts || [];
        if (!accounts.length) {
            const reason =
                message.status === "Connected"
                    ? __("No accounts returned yet — press Refresh.")
                    : __("Not connected. Open Plaid Settings to connect a bank.");
            body.innerHTML = `<div class="fbb-muted">${reason}</div>`;
            return;
        }

        if (message.institution_name) meta.textContent = message.institution_name;

        body.innerHTML = accounts
            .map((a) => {
                const mask = a.mask ? `••${esc(a.mask)}` : "";
                const sub = [esc(a.subtype || a.type || ""), mask].filter(Boolean).join(" ");
                const available =
                    a.available !== null && a.available !== undefined
                        ? `<span class="fbb-avail">${__("avail")} ${esc(money(a.available, a.currency))}</span>`
                        : "";
                return `
                    <div class="fbb-acct">
                        <div class="fbb-acct-top">
                            <span class="fbb-acct-name">${esc(a.name || __("Account"))}</span>
                            <span class="fbb-acct-bal">${esc(money(a.current, a.currency))}</span>
                        </div>
                        <div class="fbb-acct-foot">
                            <span class="fbb-acct-sub">${sub}</span>
                            ${available}
                        </div>
                    </div>`;
            })
            .join("");

        if (message.last_sync) {
            const stamp = document.createElement("div");
            stamp.className = "fbb-stamp";
            stamp.textContent = __("as of {0}", [frappe.datetime.str_to_user(message.last_sync) || message.last_sync]);
            body.appendChild(stamp);
        }
    }

    function startApp(container) {
        const body = container.querySelector("#fbb-body");
        const refresh = container.querySelector("#fbb-refresh");

        function load() {
            body.innerHTML = `<div class="fbb-muted">${__("Loading…")}</div>`;
            refresh.disabled = true;
            frappe
                .call({ method: READ })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="fbb-muted">${__("Could not load bank balances.")}</div>`;
                })
                .then(() => {
                    refresh.disabled = false;
                });
        }

        function refreshNow() {
            refresh.disabled = true;
            body.innerHTML = `<div class="fbb-muted">${__("Refreshing from your bank…")}</div>`;
            frappe
                .call({ method: REFRESH })
                .then((r) => {
                    const m = r.message || {};
                    if (!m.ok && m.message) {
                        frappe.show_alert({ message: m.message, indicator: "orange" });
                    }
                })
                .catch(() => {
                    frappe.show_alert({ message: __("Refresh failed."), indicator: "red" });
                })
                .then(() => load());
        }

        refresh.addEventListener("click", refreshNow);
        load();
    }

    waitForDOM();
})();
