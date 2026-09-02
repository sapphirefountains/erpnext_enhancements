// Bank Balances — Finance Dashboard Custom HTML Block.
//
// Reads the cached snapshot from
// erpnext_enhancements.plaid_banking.core.api.get_bank_balances (the widget never
// calls Plaid directly). "Refresh" spends one live Plaid call per linked bank via
// refresh_now. Balances are grouped by Bank, each with its own status: a bank in
// "Reconnect Required" shows an amber banner routing to that Bank's form, where the
// native "Refresh Plaid Link" button lives — re-linking is ERPNext's flow, not ours.
// Shadow-DOM block model (state on window).

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

    function bankFormUrl(bank) {
        return `/app/bank/${encodeURIComponent(bank)}`;
    }

    function renderAccount(a) {
        const esc = frappe.utils.escape_html;
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
    }

    function renderBank(b) {
        const esc = frappe.utils.escape_html;
        const status = b.status || "Connected";
        let body;
        if (status === "Reconnect Required") {
            body = `<div class="fbb-reconnect">${__("Bank connection needs re-authentication.")}
                <a href="${bankFormUrl(b.bank)}">${__("Open {0} → Refresh Plaid Link", [esc(b.bank)])}</a></div>`;
        } else if (status === "Error") {
            body = `<div class="fbb-bank-msg">${esc(b.message || __("Could not fetch balances."))}</div>`;
        } else if (!(b.accounts || []).length) {
            body = `<div class="fbb-bank-msg">${__("No accounts returned yet — press Refresh.")}</div>`;
        } else {
            body = b.accounts.map(renderAccount).join("");
        }
        const pill = status === "Connected" ? "" : `<span class="fbb-bank-status fbb-status-${status === "Error" ? "error" : "warn"}">${esc(status)}</span>`;
        return `
            <div class="fbb-bank">
                <div class="fbb-bank-head">
                    <span class="fbb-bank-name">${esc(b.bank)}</span>
                    ${pill}
                </div>
                ${body}
            </div>`;
    }

    function render(container, message) {
        const esc = frappe.utils.escape_html;
        const body = container.querySelector("#fbb-body");
        const meta = container.querySelector("#fbb-meta");
        meta.textContent = "";

        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fbb-muted">${__("Bank Balances are turned off. Switch the widget on in Plaid Banking Settings.")}</div>`;
            return;
        }
        if (message.paused) {
            body.innerHTML = `<div class="fbb-reconnect">${esc(message.status_message || __("Plaid is paused after a configuration error."))}
                <a href="/app/plaid-settings">${__("Check the native Plaid Settings")}</a>
                <a href="/app/plaid-banking-settings">${__("Plaid Banking Settings → Test Connection")}</a></div>`;
            return;
        }
        const banks = message.banks || [];
        if (!banks.length) {
            const reason =
                message.status === "Connected"
                    ? __("No balances cached yet — press Refresh.")
                    : __("No bank is linked. Link one in the native Plaid Settings (ERPNext Integrations).");
            body.innerHTML = `<div class="fbb-muted">${reason} <a href="/app/plaid-settings">${__("Open Plaid Settings")}</a></div>`;
            return;
        }

        meta.textContent = __("{0} bank(s)", [banks.length]);
        body.innerHTML = banks.map(renderBank).join("");

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
            body.innerHTML = `<div class="fbb-muted">${__("Refreshing from your banks…")}</div>`;
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
